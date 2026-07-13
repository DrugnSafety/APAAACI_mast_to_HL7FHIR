"""
Relevance/Screening 엔진 회귀 테스트 (streamlit/openai 불필요)

실행: python test_relevance_engine.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from models.schemas import (
    OCRResult, PatientInfo, AllergenResult, TestType, InterpretationType,
    AllergenCategory, ScreeningProfile, SymptomSeasonPattern, ClinicalRelevance,
)
from services.knowledge_service import get_knowledge_service, normalize_category
from services.relevance_service import (
    get_relevance_service, Q_EXPOSED, Q_SYMPTOM, Q_REPRODUCIBLE,
)
from services.screening_service import get_screening_service


def _mast(name, ko, value, cls, cat, interp=InterpretationType.POSITIVE, idx=1):
    return AllergenResult(index=idx, raw_text=name, allergen_name=name, korean_name=ko,
                          value=value, unit="kU/L", category=cat,
                          interpretation=interp, class_value=cls)


def build_case():
    return OCRResult(
        test_type=TestType.MAST,
        patient=PatientInfo(name="테스트", age=30, gender="F", test_date="2026-06-01"),
        results=[
            _mast("Dermatophagoides farinae", "미국집먼지진드기", 30.0, 4, AllergenCategory.MITE, idx=1),
            _mast("Japanese hop pollen", "환삼덩굴 꽃가루", 8.0, 3, AllergenCategory.POLLEN, idx=2),
            _mast("Cat dander", "고양이 비듬", 1.5, 2, AllergenCategory.ANIMAL, idx=3),
            _mast("Birch pollen", "자작나무 꽃가루", 2.0, 2, AllergenCategory.POLLEN, idx=4),
            _mast("Dog dander", "개 비듬", 0.1, 0, AllergenCategory.ANIMAL,
                  interp=InterpretationType.NEGATIVE, idx=5),
        ],
    )


def test_knowledge_base_loaded():
    ks = get_knowledge_service()
    assert ks.stats()["total"] >= 10, "지식베이스가 비어 있습니다"
    assert ks.get_rubric().get("principles_ko"), "감별 rubric이 없습니다"
    # 카테고리 정규화
    assert normalize_category("Pollen") in ("pollen_tree", "pollen_grass", "pollen_weed")
    assert normalize_category("Mite") == "mite"
    print("✓ knowledge base loaded & rubric present")


def test_only_positive_assessed():
    rs = get_relevance_service()
    res = rs.build_assessments(build_case(), None)
    names = [a.allergen_name for a in res.assessments]
    assert "Dog dander" not in names, "음성 알러젠이 포함됨"
    assert len(res.assessments) == 4, f"양성 4개여야 하는데 {len(res.assessments)}개"
    print("✓ only positive allergens assessed")


def test_strength():
    rs = get_relevance_service()
    assert rs.compute_strength(TestType.MAST, None, None, 4) == "strong"
    assert rs.compute_strength(TestType.MAST, None, None, 2) == "moderate"
    assert rs.compute_strength(TestType.MAST, None, 1.0, None) == "weak"
    assert rs.compute_strength(TestType.SPT, 9.0, None, None) == "strong"
    assert rs.compute_strength(TestType.MAST, None, 0.1, 0) is None
    print("✓ sensitization strength calculation")


def test_classification_logic():
    rs = get_relevance_service()
    res = rs.build_assessments(build_case(), None)
    by_name = {a.allergen_name: a for a in res.assessments}

    # 진드기: 노출+증상+재현 → clinically_relevant
    by_name["Dermatophagoides farinae"].answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "yes", Q_REPRODUCIBLE: "yes"}
    # 고양이: 노출했지만 증상 없음 → sensitized_only
    by_name["Cat dander"].answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "no", Q_REPRODUCIBLE: "no"}
    # 자작나무: 노출 경험 없음 → indeterminate
    by_name["Birch pollen"].answers = {Q_EXPOSED: "no", Q_SYMPTOM: "unsure", Q_REPRODUCIBLE: "unsure"}
    # 환삼덩굴: 증상 있음 → clinically_relevant
    by_name["Japanese hop pollen"].answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "yes", Q_REPRODUCIBLE: "unsure"}

    rs.classify_all(res)
    assert by_name["Dermatophagoides farinae"].relevance == ClinicalRelevance.CLINICALLY_RELEVANT
    assert by_name["Cat dander"].relevance == ClinicalRelevance.SENSITIZED_ONLY
    assert by_name["Birch pollen"].relevance == ClinicalRelevance.INDETERMINATE
    assert by_name["Japanese hop pollen"].relevance == ClinicalRelevance.CLINICALLY_RELEVANT
    for a in res.assessments:
        assert a.rationale_ko, f"근거 문구 없음: {a.allergen_name}"
    print("✓ classification: relevant / sensitized-only / indeterminate")


def test_season_overlap_autosuggest():
    """봄 악화 환자 + 봄 나무꽃가루 → season_overlap True & 증상악화 자동제안 yes"""
    rs = get_relevance_service()
    screening = ScreeningProfile(season_pattern=SymptomSeasonPattern.SEASONAL, worse_months=[3, 4, 5])
    res = rs.build_assessments(build_case(), screening)
    birch = next(a for a in res.assessments if a.allergen_name == "Birch pollen")
    assert birch.season_overlap is True, "봄 겹침이 감지되지 않음"
    assert birch.answers.get(Q_SYMPTOM) == "yes", "시즌 일치 자동제안이 되지 않음"
    print("✓ season overlap auto-suggestion from screening")


def test_screening_antihistamine_flag():
    sc = get_screening_service()
    prof = ScreeningProfile(current_medications=["antihistamine"], antihistamine_recent=True)
    summ = sc.summarize(prof)
    assert any("항히스타민" in f for f in summ["flags"]), "항히스타민제 주의 플래그 없음"
    print("✓ screening antihistamine SPT-false-negative flag")


def test_unicap_pipeline():
    """UniCAP(ImmunoCAP) 결과가 MAST 와 동일하게 특이 IgE(kU/L)로 해석되는지"""
    from models.schemas import determine_interpretation
    from services.fhir_service import FHIRService
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.UNICAP,
        patient=PatientInfo(name="유니캡", age=28, gender="M", test_date="2026-06-10"),
        results=[
            AllergenResult(index=1, raw_text="Der f 3.52", allergen_name="Dermatophagoides farinae",
                           korean_name="집먼지진드기", value=3.52, unit="kU/L", class_value=3),
            AllergenResult(index=2, raw_text="Cat 0.12", allergen_name="Cat dander",
                           korean_name="고양이", value=0.12, unit="kU/L", class_value=0),
        ],
    )
    # 양성 판정: ≥0.35 kU/L
    assert determine_interpretation(TestType.UNICAP, value=3.52) == InterpretationType.POSITIVE
    assert determine_interpretation(TestType.UNICAP, value=0.12) == InterpretationType.NEGATIVE
    # 양성만 평가 대상
    res = rs.build_assessments(ocr, None)
    names = [a.allergen_name for a in res.assessments]
    assert names == ["Dermatophagoides farinae"], f"UniCAP 양성 필터 실패: {names}"
    assert res.assessments[0].test_unit == "kU/L"
    assert rs.compute_strength(TestType.UNICAP, None, 3.52, 3) == "moderate"
    # FHIR: 특이 IgE 코드 + kU/L
    obs = FHIRService().create_observation(ocr.results[0], patient_id="p1",
                                           test_type=TestType.UNICAP, test_date="2026-06-10")
    assert obs["code"]["coding"][0]["display"] == "Specific IgE measurement"
    assert obs["valueQuantity"]["unit"] == "kU/L"
    print("✓ UniCAP handled as specific-IgE (parse/positivity/strength/FHIR)")


def test_ocr_parsing_large_mast_panel():
    """대형 다열 MAST 패널 파싱: Total IgE 제외, Class 기반 양성판정, 단위 보존, 잘린 응답 복구"""
    import json as _json
    from services.ocr_service import OCRService
    from services.relevance_service import RelevanceService
    from utils.allergen_mapper import get_allergen_mapper
    svc = OCRService.__new__(OCRService)
    svc.allergen_mapper = get_allergen_mapper()

    rows = [
        {"index": 1, "allergen_name": "Total IgE (총 IgE)", "class": None, "value": 100, "unit": "IU/ml"},
        {"index": 2, "allergen_name": "D. pteronyssinus (진드기 Dp)", "class": 3, "value": 15.12, "unit": "IU/ml"},
        {"index": 3, "allergen_name": "D. farinae (진드기 Df)", "class": 0, "value": 0.21, "unit": "IU/ml"},
        {"index": 21, "allergen_name": "House dust (집먼지)", "class": 2, "value": 1.50, "unit": "IU/ml"},
        {"index": 42, "allergen_name": "Peanut (땅콩)", "class": 2, "value": 3.40, "unit": "IU/ml"},
        {"index": 61, "allergen_name": "Mushroom (버섯)", "class": 1, "value": 0.62, "unit": "IU/ml"},
        {"index": 62, "allergen_name": "Candida albicans (칸디다곰팡이)", "class": 4, "value": 22.25, "unit": "IU/ml"},
    ]
    full = _json.dumps({"test_type": "MAST", "patient": {}, "results": rows}, ensure_ascii=False)

    res = svc._parse_ocr_result(svc._extract_json(full))
    assert res.test_type == TestType.MAST
    assert all("total ige" not in r.allergen_name.lower() for r in res.results), "Total IgE 미제외"
    assert res.results[0].unit == "IU/ml", "단위(IU/ml) 보존 실패"
    pos = [r for r in res.results if RelevanceService._is_positive(r, res.test_type)]
    assert len(pos) == 5, f"Class 기반 양성 5개 기대, 실제 {len(pos)}"

    # 잘린 응답 → SPT 빈 결과가 아니라 완성 행 복구 + MAST 유지
    cut = full[: full.find("Peanut") - 20]
    res2 = svc._parse_ocr_result(svc._extract_json(cut))
    assert res2.test_type == TestType.MAST, "잘린 응답에서 test_type이 SPT로 유실됨"
    assert len(res2.results) >= 2, "잘린 응답 복구 실패"
    print("✓ OCR: large multi-column MAST panel (Total IgE 제외·Class 양성·단위보존·truncation 복구)")


def test_questionnaire_engine():
    """적응형 문진 생성 + 그룹 답변 기반 판정 검증"""
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_PATTERN, Q_SEASONS, Q_INDOOR_TIMING,
        Q_MITE_DUST, QP_POLLEN, QP_ANIMAL_CONTACT, QP_ANIMAL_WORSE, QP_FOOD_SYMPTOMS,
        Q_OAS, Q_FOOD_SYSTEMIC,
    )
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.MAST,
        patient=PatientInfo(name="문진", test_date="2026-06-01"),
        results=[
            _mast("Dermatophagoides farinae", "미국집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1),
            _mast("Birch pollen", "자작나무 꽃가루", 6.0, 3, AllergenCategory.POLLEN, idx=2),
            _mast("Cat dander", "고양이 비듬", 1.5, 2, AllergenCategory.ANIMAL, idx=3),
            _mast("Peanut", "땅콩", 2.0, 2, AllergenCategory.FOOD, idx=4),
        ],
    )
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    q = eng.build(res, None)

    # 섹션이 카테고리별로 생성되고, 알러젠마다 3문항 반복이 아님
    sec_ids = [s["id"] for s in q["sections"]]
    assert "pattern" in sec_ids and "pollen" in sec_ids and "indoor" in sec_ids
    assert "animal" in sec_ids and "food" in sec_ids
    # 꽃가루는 시즌 그룹당 1문항 (알러젠 수와 무관) — severity 후속 문항은 제외하고 카운트
    from services.questionnaire_service import QP_POLLEN, QP_SEVERITY
    pollen_sec = next(s for s in q["sections"] if s["id"] == "pollen")
    season_qs = [qq for qq in pollen_sec["questions"] if qq["id"].startswith(QP_POLLEN)]
    assert len(season_qs) == 1, "나무꽃가루 1종이면 봄 시즌 1문항이어야 함"
    # severity 후속 문항이 reveal 조건과 함께 존재
    sev_qs = [qq for qq in pollen_sec["questions"] if qq["id"].startswith(QP_SEVERITY)]
    assert len(sev_qs) == 1 and sev_qs[0].get("reveal_if"), "시즌 증상 있음 → 중증도 후속 문항 필요"

    # 판정: 진드기(아침+먼지 yes)→relevant, 자작(봄 no)→sensitized,
    #       고양이(접촉 yes/악화 no)→sensitized, 땅콩(섭취 no)→sensitized
    answers = {
        Q_PATTERN: "perennial",
        Q_SEASONS: [],
        QP_POLLEN + "spring_tree": "no",
        Q_INDOOR_TIMING: "yes",
        Q_MITE_DUST: "yes",
        QP_ANIMAL_CONTACT + "agn2": "yes",
        QP_ANIMAL_WORSE + "agn2": "no",
        QP_FOOD_SYMPTOMS + "agn3": ["none"],  # 땅콩 문제없이 섭취 → 감작만
        Q_OAS: "no", Q_FOOD_SYSTEMIC: "no",
    }
    eng.classify(res, answers, None)
    by = {a.allergen_name: a.relevance for a in res.assessments}
    assert by["Dermatophagoides farinae"] == ClinicalRelevance.CLINICALLY_RELEVANT
    assert by["Birch pollen"] == ClinicalRelevance.SENSITIZED_ONLY
    assert by["Cat dander"] == ClinicalRelevance.SENSITIZED_ONLY
    assert by["Peanut"] == ClinicalRelevance.SENSITIZED_ONLY
    for a in res.assessments:
        assert a.rationale_ko, f"근거 문구 없음: {a.allergen_name}"
    print("✓ adaptive questionnaire build + grouped-answer classification")


def test_shellfish_and_mite_tropomyosin():
    """갑각류 양방향 감별 + 진드기↔갑각류 트로포마이오신 교차반응"""
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_PATTERN, Q_INDOOR_TIMING, Q_MITE_DUST,
        Q_MITE_SHELLFISH, QP_SHELLFISH,
    )
    rs = get_relevance_service()
    eng = get_questionnaire_engine()

    # (a) 새우 강양성이지만 잘 먹음 → 감작만 (reverse scenario)
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="새우"),
                    results=[_mast("Shrimp", "새우", 25.0, 4, AllergenCategory.FOOD, idx=1)])
    res = rs.build_assessments(ocr, None)
    q = eng.build(res, None)
    fq = [x["id"] for s in q["sections"] if s["id"] == "food" for x in s["questions"]]
    assert any(x.startswith("shellfish_react__") for x in fq), "갑각류 섭취반응 질문 없음"
    eng.classify(res, {QP_SHELLFISH + "agn0": "none"}, None)
    assert res.assessments[0].relevance == ClinicalRelevance.SENSITIZED_ONLY, "강양성+무증상은 감작만이어야"

    # (b) 진드기 양성, 갑각류 미검사 → 트로포마이오신 질문 생성 + 전신반응 → note
    ocr2 = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="진드기"),
                     results=[_mast("Dermatophagoides farinae", "집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1)])
    res2 = rs.build_assessments(ocr2, None)
    q2 = eng.build(res2, None)
    fq2 = [x["id"] for s in q2["sections"] if s["id"] == "food" for x in s["questions"]]
    assert Q_MITE_SHELLFISH in fq2, "진드기 양성 시 갑각류 교차반응 질문이 있어야"
    eng.classify(res2, {Q_PATTERN: "perennial", Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes",
                        Q_MITE_SHELLFISH: "systemic"}, None)
    assert "트로포마이오신" in res2.assessments[0].rationale_ko, "진드기-갑각류 교차 note 누락"
    # FHIR 교차반응 음식 항목에 갑각류 포함
    items = eng.crossreactive_food_items(res2.assessments, {Q_MITE_SHELLFISH: "systemic"})
    assert any(it["source"] == "mite_tropomyosin" for it in items), "FHIR 교차반응 갑각류 항목 누락"
    print("✓ shellfish bidirectional + mite-tropomyosin cross-reactivity")


def test_questionnaire_food_systemic_and_oas():
    """전신 음식반응/OAS 로직: 전신반응 yes → 음식 알러젠 relevant, OAS 교차반응 노트"""
    from services.questionnaire_service import get_questionnaire_engine, Q_FOOD_SYSTEMIC, Q_OAS, QP_POLLEN
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.MAST, patient=PatientInfo(name="음식"),
        results=[
            _mast("Peanut", "땅콩", 5.0, 3, AllergenCategory.FOOD, idx=1),
            _mast("Birch pollen", "자작나무 꽃가루", 5.0, 3, AllergenCategory.POLLEN, idx=2),
        ],
    )
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    eng.build(res, None)
    eng.classify(res, {Q_FOOD_SYSTEMIC: "yes", Q_OAS: "yes", QP_POLLEN + "spring_tree": "yes"}, None)
    by = {a.allergen_name: a for a in res.assessments}
    assert by["Peanut"].relevance == ClinicalRelevance.CLINICALLY_RELEVANT
    assert "전신" in by["Peanut"].rationale_ko
    assert "구강알레르기증후군" in by["Birch pollen"].rationale_ko  # OAS 교차반응 노트
    print("✓ food systemic reaction + OAS cross-reaction note")


def test_pfas_and_immunotherapy():
    """PFAS 교차반응 데이터 + 면역치료 정보 로드/조회"""
    from services.knowledge_service import get_knowledge_service
    ks = get_knowledge_service()
    pf = ks.pfas_foods_for("Birch pollen", "pollen_tree")
    assert any(f["en"] == "apple" for f in pf["foods"]), "자작나무 PFAS 사과 없음"
    pf2 = ks.pfas_foods_for("Ragweed pollen", "pollen_weed")
    assert any("melon" in f["en"] for f in pf2["foods"]), "돼지풀 PFAS 멜론 없음"
    assert ks.immunotherapy_info("mite")["eligible"] is True
    assert ks.immunotherapy_info("food")["eligible"] is False
    print("✓ PFAS dataset + immunotherapy eligibility")


def test_fhir_v2_bundles():
    """FHIR v2: 전체 Observation(음성 포함) + AllergyIntolerance(confirmed/unconfirmed/criticality) + OAS 음식"""
    from services.fhir_service import FHIRService
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_PATTERN, QP_POLLEN, Q_INDOOR_TIMING, Q_MITE_DUST, Q_OAS, Q_OAS_FOODS,
    )
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.MAST,
        patient=PatientInfo(name="에프", test_date="2026-06-01", facility="OO병원"),
        results=[
            _mast("Dermatophagoides farinae", "집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1),
            _mast("Birch pollen", "자작나무 꽃가루", 6.0, 3, AllergenCategory.POLLEN, idx=2),
            _mast("Dog dander", "개 비듬", 0.1, 0, AllergenCategory.ANIMAL,
                  interp=InterpretationType.NEGATIVE, idx=3),
        ],
    )
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    answers = {Q_PATTERN: "both", QP_POLLEN + "spring_tree": "yes",
               Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes", Q_OAS: "yes", Q_OAS_FOODS: ["apple"]}
    eng.classify(res, answers, None)
    oas_foods = eng.oas_selected_foods(res.assessments, answers)
    bundles = FHIRService().build_bundles_from_relevance(ocr, res, None, oas_foods)

    obs = bundles["observation_bundle"]["entry"]
    assert len(obs) == 3, "Observation은 음성 포함 전체 3개여야 함"
    assert obs[0]["resource"].get("performer"), "검사기관 performer 누락"
    ai = bundles["allergy_intolerance_bundle"]["entry"]
    codes = [e["resource"].get("code", {}).get("text", "") for e in ai]
    verifs = {e["resource"]["code"]["text"]: e["resource"]["verificationStatus"]["coding"][0]["code"] for e in ai}
    assert any("apple" in c or "사과" in c for c in codes), "OAS 교차반응 음식(사과) AllergyIntolerance 누락"
    # 임상적 유의(진드기·자작)=confirmed
    assert any(v == "confirmed" for v in verifs.values())

    # CDM(OMOP) 기매핑이 Observation.component 코드로 반영되는지 검증
    obs_codes = {}
    for e in obs:
        comp = e["resource"].get("component", [])
        if comp:
            c = comp[0]["code"]["coding"][0]
            obs_codes[c["code"]] = c["display"]
    assert "46273588" in obs_codes and obs_codes["46273588"] == "Dermatophagoides farinae protein", \
        f"D.farinae CDM concept_id/display 누락: {obs_codes}"
    assert "765811" in obs_codes and obs_codes["765811"] == "Birch pollen", \
        f"Birch CDM concept_id/display 누락: {obs_codes}"
    # AllergyIntolerance.code 도 CDM concept_id 사용
    ai_codes = [e["resource"]["code"]["coding"][0]["code"]
                for e in ai if e["resource"]["code"].get("coding")]
    assert "765811" in ai_codes, f"Birch AllergyIntolerance CDM concept_id 누락: {ai_codes}"
    print("✓ FHIR v2 bundles (all-obs + performer + AllergyIntolerance status + OAS food + CDM SNOMED coding)")


def test_cdm_snomed_mapping():
    """병원 제공 CDM(OMOP) 기매핑 엑셀 → 알러젠별 concept_id·concept_name·vocabulary 조회."""
    from utils.allergen_mapper import get_allergen_mapper
    m = get_allergen_mapper()
    assert len(getattr(m, "cdm_entries", [])) >= 150, "CDM 기매핑 항원 수 부족"
    # 대표 항원: (조회명, 기대 concept_id, 기대 display)
    cases = [
        ("Birch", "765811", "Birch pollen"),
        ("Dermatophagoides farinae", "46273588", "Dermatophagoides farinae protein"),
        ("Cat", "4125384", "Cat dander"),
        ("Cat hair", "4125384", "Cat dander"),  # 신규 별칭
        ("Squid", "42536271", "Squid"),          # 신규 음식 항원
    ]
    for name, cid, disp in cases:
        c = m.get_coding(name, "")
        assert c and c["code"] == cid and c["display"] == disp, f"{name} 매핑 오류: {c}"
        assert c["system"] in ("http://snomed.info/sct", "http://loinc.org")
    # 미매핑 항원은 None (예외 없이)
    assert m.get_coding("완전신종알러젠ZZZ", "") is None
    print("✓ CDM(OMOP) SNOMED 기매핑 로드 및 concept 조회(신규 별칭 포함)")


def test_cardnews_and_report():
    from services.cardnews_service import get_cardnews_service
    from services.report_service import ReportService
    rs = get_relevance_service()
    res = rs.build_assessments(build_case(), None)
    for a in res.assessments:
        a.answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "yes", Q_REPRODUCIBLE: "yes"}
    rs.classify_all(res)
    pinfo = {"name": "테스트", "age": 30, "gender": "F", "test_date": "2026-06-01"}

    html = get_cardnews_service().generate_html(res, pinfo)
    assert "테스트" in html and "카드뉴스" in html
    svc = ReportService.__new__(ReportService)
    svc.api_key = None
    md = ReportService.build_patient_report_markdown(svc, res, pinfo, None)
    assert "실제 주의가 필요한 알러젠" in md and "예방·관리" in md
    print("✓ card news + deterministic patient report generated")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"✗ {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"✗ {t.__name__}: ERROR {e}")
    print("\n" + ("ALL TESTS PASSED ✅" if failed == 0 else f"{failed} TEST(S) FAILED ❌"))
    sys.exit(1 if failed else 0)
