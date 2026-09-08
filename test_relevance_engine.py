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
    # 검증된 SCTID: 397691009 Allergen specific IgE antibody measurement, quantitative (구 165967004 는 미존재 코드)
    assert obs["code"]["coding"][0]["code"] == "397691009"
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
    """갑각류 양방향 감별 + 진드기↔갑각류 트로포마이오신 교차반응(성분 엔진, P3)"""
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_PATTERN, Q_INDOOR_TIMING, Q_MITE_DUST,
        QP_SHELLFISH, QP_CROSSREACT,
    )
    from services.crossreactivity_service import get_crossreactivity_service
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

    if not get_crossreactivity_service().has_data():
        print("✓ shellfish bidirectional (교차반응 성분엔진 스킵 — 레지스트리 미생성)")
        return
    # (b) 진드기 양성, 갑각류 미검사 → 성분(tropomyosin) 기반 갑각류 교차반응 질문 생성(데이터 파생)
    ocr2 = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="진드기"),
                     results=[_mast("Dermatophagoides farinae", "집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1)])
    res2 = rs.build_assessments(ocr2, None)
    q2 = eng.build(res2, None)
    crossq = [x for s in q2["sections"] if s["id"] == "food"
              for x in s["questions"] if x["id"].startswith(QP_CROSSREACT)]
    assert crossq, "진드기 양성 시 성분기반 갑각류 교차반응 질문이 있어야"
    opts = [o["value"] for o in crossq[0]["options"]]
    assert any(v in opts for v in ("Shrimp", "Crab", "Lobster")), f"갑각류 후보 누락: {opts}"
    # 새우 선택 → FHIR 교차반응 항목(component)에 포함
    eng.classify(res2, {Q_PATTERN: "perennial", Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes",
                        QP_CROSSREACT + "agn0": ["Shrimp"]}, None)
    items = eng.crossreactive_food_items(res2.assessments, {QP_CROSSREACT + "agn0": ["Shrimp"]})
    comp = [it for it in items if it["source"] == "component"]
    assert comp and any("새우" in (it["ko"] or "") for it in comp), f"진드기→갑각류 교차반응 항목 누락: {items}"
    print("✓ shellfish bidirectional + 진드기→갑각류 성분기반 교차반응(P3)")


def test_is_shellfish_registry_based_item3():
    """item3: is_shellfish() 레지스트리(성분) 기반 이관 —
    food+tropomyosin=조개/갑각, 어류(parvalbumin)·진드기·바퀴는 제외."""
    from services.knowledge_service import get_knowledge_service
    from services.crossreactivity_service import get_crossreactivity_service
    ks = get_knowledge_service()
    if not get_crossreactivity_service().has_data():
        print("✓ (skip) 레지스트리 미생성 — item3 shellfish 스킵")
        return
    for en, ko in [("Shrimp", "새우"), ("Crab", "게"), ("Lobster", "랍스터"),
                   ("Clam", "조개"), ("Oyster", "굴"), ("Squid", "오징어"),
                   ("Abalone", "전복"), ("Snail", "달팽이")]:
        assert ks.is_shellfish(en, ko), f"{en} 은(는) shellfish 여야"
    for en, ko in [("Cod", "대구"), ("Salmon", "연어"), ("Tuna", "참치"),
                   ("House dust mite", "집먼지진드기"), ("Cockroach", "바퀴"),
                   ("Milk", "우유"), ("Peanut", "땅콩"), ("Birch pollen", "자작나무")]:
        assert not ks.is_shellfish(en, ko), f"{en} 은(는) shellfish 가 아니어야"
    print("✓ item3 is_shellfish 레지스트리 기반(어류·진드기·바퀴 제외)")


def test_spt_observation_components_item5():
    """item5: SPT 측정을 CDM qualifier concept 기반 Observation.component 로 세분화 —
    장축·단축·평균·A/H비. size_text/히스타민 대조로 파생값도 계산."""
    from models.schemas import OCRResult, PatientInfo, TestType, AllergenResult, InterpretationType
    from services.fhir_service import FHIRService
    ocr = OCRResult(
        test_type=TestType.SPT,
        patient=PatientInfo(name="SPT", test_date="2026-07-23", histamine_mean_mm=3.0),
        results=[
            AllergenResult(index=1, raw_text="Df 3x5", allergen_name="Dermatophagoides farinae",
                           korean_name="집먼지진드기", size_text="3x5",
                           interpretation=InterpretationType.POSITIVE, category="mite"),
        ],
    )
    b = FHIRService().create_observation_bundle(ocr, patient_id="SPT")
    obs = b["entry"][0]["resource"]
    # 방법(method) = 히스타민 양성대조 concept
    assert obs.get("method", {}).get("coding"), "SPT method(히스타민 대조) 코딩 누락"
    # 대표값 valueQuantity = 파생 평균((3+5)/2=4)
    assert obs["valueQuantity"]["value"] == 4.0, f"SPT 대표 평균 파생 실패: {obs.get('valueQuantity')}"
    comps = {c["code"]["coding"][0]["code"]: c for c in obs.get("component", [])}
    # CDM qualifier concept_id (major 4037344, minor 4038107, average 45880776, A/H 4187346)
    assert comps.get("4037344", {}).get("valueQuantity", {}).get("value") == 5.0, "장축(major) 파생 실패"
    assert comps.get("4038107", {}).get("valueQuantity", {}).get("value") == 3.0, "단축(minor) 파생 실패"
    assert comps.get("45880776", {}).get("valueQuantity", {}).get("value") == 4.0, "평균(average) 파생 실패"
    # A/H = 평균4 / 히스타민3 = 1.33
    assert comps.get("4187346", {}).get("valueQuantity", {}).get("value") == 1.33, "A/H비 파생 실패"
    # 알러젠 코딩 component 도 유지(component[0])
    assert obs["component"][0]["code"]["coding"][0]["code"] == "112476003", "SPT 알러젠 SCTID component 누락"
    print("✓ item5 SPT Observation.component 세분화(장축·단축·평균·A/H, CDM qualifier concept)")


def test_oas_component_engine_integration_item4():
    """item4: OAS(꽃가루-음식)를 성분 엔진으로 통합 —
    (a) find()가 'Birch pollen'→'Birch' 수식어 흡수, (b) OAS 옵션이 큐레이션 PFAS +
    성분엔진 파생을 통합(엔진 전용 음식도 포함)."""
    from services.crossreactivity_service import get_crossreactivity_service
    svc = get_crossreactivity_service()
    if not svc.has_data():
        print("✓ (skip) 레지스트리 미생성 — item4 OAS 통합 스킵")
        return
    # (a) 수식어가 붙은 검사표기도 레지스트리 항원으로 해소
    a = svc.find("Birch pollen", "자작나무 꽃가루")
    assert a and a["canonical_name"] == "Birch" and "pr10" in a.get("components", []), \
        f"'Birch pollen' 성분 lookup 실패: {a}"
    assert svc.find("Cat epithelium", "고양이 상피"), "'Cat epithelium' 수식어 해소 실패"

    from services.questionnaire_service import get_questionnaire_engine
    rs = get_relevance_service()
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="꽃"),
                    results=[_mast("Birch pollen", "자작나무 꽃가루", 5.0, 3, AllergenCategory.POLLEN, idx=1)])
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    opts, link = eng._pfas_options(res.assessments)
    labels = {o["label"] for o in opts}
    # 큐레이션 PFAS 음식(자작-사과) 유지
    assert "사과" in labels, f"큐레이션 PFAS 음식(사과) 누락: {labels}"
    # 성분엔진 전용 음식(자작 큐레이션 목록에 없는 것)도 통합됨
    assert labels & {"바나나", "옥수수"}, f"성분엔진 파생 음식 통합 실패: {labels}"
    print("✓ item4 OAS 성분엔진 통합(수식어 해소 + 큐레이션+엔진 음식 통합)")


def test_category_resolver_p4():
    """P4: 카테고리 resolve 파이프라인 — 이름변형(Dog hair·Horse dander)도 강건 분류 +
    동물 문진 누락 버그(원 보고 버그) 해결."""
    from services.category_resolver import resolve_category
    cases = {
        ("Dog hair", "개털"): "animal", ("Horse dander", "말 비듬"): "animal",
        ("Cat epithelium", "고양이 상피"): "animal", ("Feline", "고양이"): "animal",
        ("Alternaria alternata", "알터나리아"): "mold", ("Timothy grass", "티모시"): "pollen_grass",
        ("Ragweed", "돼지풀"): "pollen_weed", ("Birch", "자작나무"): "pollen_tree",
        ("Celery", "샐러리"): "food", ("Latex", "라텍스"): "latex",
        ("Blatella germanica", "독일바퀴"): "insect",
    }
    for (n, k), exp in cases.items():
        got = resolve_category(n, k)
        assert got == exp, f"{n}({k}) → {got}, 기대 {exp}"
    assert resolve_category("완전신종물질ZZZ", "") == "other"

    # 원 버그 재현·해결: Dog hair 양성 → 동물 감별 문진 섹션이 생성되어야
    rs = get_relevance_service()
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="개"),
                    results=[
                        _mast("Dermatophagoides farinae", "집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1),
                        _mast("Dog hair", "개털", 3.0, 3, None, idx=2)])
    res = rs.build_assessments(ocr, None)
    from services.questionnaire_service import get_questionnaire_engine, _cat
    q = get_questionnaire_engine().build(res, None)
    dog = next(a for a in res.assessments if a.allergen_name == "Dog hair")
    assert _cat(dog) == "animal", f"Dog hair 카테고리 오류: {_cat(dog)}"
    assert "animal" in [s["id"] for s in q["sections"]], "Dog hair 양성인데 동물 문진 섹션 누락(원 버그)"
    print("✓ P4 카테고리 resolve(Dog hair·Horse dander→animal, 꽃가루 세분) + 동물 문진 누락 버그 해결")


def test_component_crossreactivity_p2():
    """P2: 성분(component) 기반 교차반응 문진 자동생성 + 선택 → confirmed FHIR 교차반응 항원."""
    from services.crossreactivity_service import get_crossreactivity_service
    if not get_crossreactivity_service().has_data():
        print("✓ (skip) 레지스트리 미생성 — P2 교차반응 스킵")
        return
    from services.questionnaire_service import get_questionnaire_engine, QP_CROSSREACT, _key
    from services.fhir_service import FHIRService
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.MAST, patient=PatientInfo(name="교차", test_date="2026-07-13"),
        results=[
            _mast("Celery", "샐러리", 5.0, 3, AllergenCategory.FOOD, idx=1),
            _mast("Shrimp", "새우", 8.0, 3, AllergenCategory.FOOD, idx=2),
        ],
    )
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    q = eng.build(res, None)
    # 셀러리·새우 각각 교차반응 문항이 데이터에서 생성됨
    cq = {qq["id"]: qq for sec in q["sections"] for qq in sec["questions"] if qq["id"].startswith(QP_CROSSREACT)}
    assert len(cq) == 2, f"교차반응 문항 2개 기대: {list(cq)}"
    celery_opts = [o["value"] for qid, qq in cq.items() for o in qq["options"] if "샐러리" in qq["title"]]
    assert "Carrot" in celery_opts and "Apple" in celery_opts, f"셀러리 교차반응 후보 누락: {celery_opts}"
    shrimp_opts = [o["value"] for qid, qq in cq.items() for o in qq["options"] if "새우" in qq["title"]]
    assert "Crab" in shrimp_opts, f"새우 교차반응 후보(게) 누락: {shrimp_opts}"

    # 셀러리 교차반응으로 당근·사과 선택 → confirmed 교차반응 항원 FHIR
    ans = {QP_CROSSREACT + _key(0): ["Carrot", "Apple"], QP_CROSSREACT + _key(1): ["Crab"]}
    eng.classify(res, ans, None)
    items = eng.crossreactive_food_items(res.assessments, ans)
    kos = {it["ko"] for it in items}
    assert {"당근", "사과", "게"} <= kos, f"교차반응 항목 누락: {kos}"
    assert all(it["source"] == "component" for it in items if it["ko"] in ("당근", "사과", "게"))
    bundles = FHIRService().build_bundles_from_relevance(ocr, res, None, items)
    ai = [e["resource"] for e in bundles["allergy_intolerance_bundle"]["entry"]]
    carrot = next(r for r in ai if r["code"]["text"] == "당근")
    assert carrot["verificationStatus"]["coding"][0]["code"] == "confirmed"
    assert carrot["code"].get("coding"), "당근 SNOMED coding 누락"
    print("✓ P2 성분기반 교차반응 문진(셀러리→당근·사과, 새우→게) + confirmed FHIR 교차반응 항원")


def test_questionnaire_report_restructure():
    """고도화 2차: 임상그룹(Df/Dp 통합, Q-0) · 항원별 구체 OAS 문항(A2) · 교차반응 중증도(A3)
    · catch-all 재배치(A4) · severity 전파(B) · 실내/실외 + 음식 동급 경고(C) · 음식중심 카드(D)."""
    from services.crossreactivity_service import get_crossreactivity_service
    if not get_crossreactivity_service().has_data():
        print("✓ (skip) 레지스트리 미생성 — 재구성 테스트 스킵")
        return
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_OAS, QP_POLLEN, QP_ANIMAL_WORSE, QP_CROSSREACT,
        QP_CROSSREACT_SEV, Q_FOOD_GENERAL, Q_FOOD_SYSTEMIC, QP_SEVERITY, Q_MITE_DUST, _key)
    from services.report_service import get_report_service
    from services.cardnews_service import get_cardnews_service
    rs = get_relevance_service()
    eng = get_questionnaire_engine()
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="홍길동"), results=[
        _mast("Dermatophagoides farinae", "집먼지진드기(D.farinae)", 20, 4, AllergenCategory.MITE, idx=1),
        _mast("Dermatophagoides pteronyssinus", "집먼지진드기(D.pteronyssinus)", 18, 4, AllergenCategory.MITE, idx=2),
        _mast("Cat dander", "고양이 비듬", 5, 3, AllergenCategory.ANIMAL, idx=3),
        _mast("Birch pollen", "자작나무 꽃가루", 8, 3, AllergenCategory.POLLEN, idx=4),
    ])
    res = rs.build_assessments(ocr, None)
    q = eng.build(res, None)
    food = [s for s in q["sections"] if s["id"] == "food"][0]["questions"]
    ids = [x["id"] for x in food]

    # Q-0: Df/Dp 는 교차반응 문항 1개로 통합(agn0만, agn1 없음)
    cr_ids = [x for x in ids if x.startswith(QP_CROSSREACT)]
    assert QP_CROSSREACT + _key(0) in cr_ids and QP_CROSSREACT + _key(1) not in cr_ids, \
        f"Df/Dp 교차반응 문항이 통합되지 않음: {cr_ids}"
    mite_q = next(x for x in food if x["id"] == QP_CROSSREACT + _key(0))
    assert "집먼지진드기" in mite_q["title"] and _key(1) in mite_q["applies_to"], "그룹 라벨/적용대상 오류"

    # A2: 꽃가루 문항이 그 꽃가루의 '구체적인' PFAS 음식을 나열
    birch_q = next(x for x in food if x["id"] == QP_CROSSREACT + _key(3))
    labels = [o["label"] for o in birch_q["options"]]
    assert "자작나무" in birch_q["title"] and {"사과", "체리"} <= set(labels), f"자작 OAS 구체 목록 누락: {labels}"

    # A3: 교차반응 증상 범위 문항 존재
    assert QP_CROSSREACT_SEV + _key(3) in ids, "교차반응 중증도 문항 누락"
    # A4: catch-all 이 전신 문항 바로 앞(마무리)에 위치
    assert ids.index(Q_FOOD_GENERAL) < ids.index(Q_FOOD_SYSTEMIC), "catch-all 이 마무리 위치가 아님"
    assert ids.index(Q_FOOD_GENERAL) > ids.index(QP_CROSSREACT + _key(0)), "catch-all 이 교차반응보다 앞"

    # 답변: 진드기→새우(전신), 자작→체리(국소), 고양이 접촉 증상 중증
    ans = {Q_MITE_DUST: "yes", QP_SEVERITY + "indoor": "moderate",
           QP_ANIMAL_WORSE + _key(2): "yes", QP_SEVERITY + _key(2): "severe",
           QP_POLLEN + "spring_tree": "yes", Q_OAS: "yes",
           QP_CROSSREACT + _key(3): ["cherry"], QP_CROSSREACT_SEV + _key(3): "oral",
           QP_CROSSREACT + _key(0): ["Shrimp"], QP_CROSSREACT_SEV + _key(0): "systemic"}
    eng.classify(res, ans, None)
    assert res.assessments[0].crossreact_severity == "systemic", "교차반응 중증도 전파 실패"

    md = get_report_service().build_patient_report_markdown(res, {"name": "홍길동"}, None)
    # A1/C: Df/Dp 통합 서술 + 실내/실외 구분
    assert "집먼지진드기(유럽·미국 두 종)" in md, "Df/Dp 통합 서술 누락"
    assert md.count("### 집먼지진드기(유럽·미국 두 종)") == 1, "집먼지진드기가 중복 서술됨"
    assert "🏠 실내 항원" in md and "🌳 실외(계절성) 항원" in md, "실내/실외 구분 누락"
    # B: 중증도 배지 + 중증 경고
    assert "🔴 **중증**" in md and "중증 반응 병력" in md, "severity 리포트 반영 누락"
    # C/R-4: 음식 동급 경고 + 교차반응 범위 배지
    assert "반드시 함께 주의할 음식" in md and "**새우**" in md, "음식 동급 경고 누락"
    assert "🔴 **전신 반응**" in md, "교차반응 전신 배지 누락"

    # D: 카드뉴스는 음식 중심 + Df/Dp 통합
    html = get_cardnews_service().generate_html(res, {"name": "홍길동"}, None)
    assert "이 음식들을" in html and "새우" in html, "카드뉴스 음식중심 카드 누락"
    assert html.count("집먼지진드기(유럽·미국 두 종)") <= 1, "카드뉴스 Df/Dp 중복"

    # 버그 재발 방지: 요약 불릿·카드뉴스 chip 도 Df/Dp 를 한 번만 표기해야 한다
    # ('지금 우선 관리할 알러젠 요약'에서 같은 줄이 두 번 출력되던 버그)
    assert md.count("- **집먼지진드기**") == 1, f"요약 불릿에서 집먼지진드기 중복 표기: {md.count('- **집먼지진드기**')}회"
    assert html.count("<b>집먼지진드기</b>") == 1, f"카드뉴스 chip에서 집먼지진드기 중복 표기: {html.count('<b>집먼지진드기</b>')}회"
    print("✓ 임상그룹 통합 + 항원별 구체 OAS + 교차반응 중증도 + 재배치 + 실내외/음식 동급 경고")


def test_crossreact_gating_reactivation_and_other_fallthrough():
    """고도화: 교차반응 증상 게이트(Q-3)+catch-all 재활성(Q-5), 동물 교차반응,
    미분류(other) 양성 항원 질문 누락 차단(Q-1), 리포트 그룹화+확대경고(R-1/R-2)."""
    from services.crossreactivity_service import get_crossreactivity_service
    if not get_crossreactivity_service().has_data():
        print("✓ (skip) 레지스트리 미생성 — 고도화 문진/리포트 스킵")
        return
    from services.questionnaire_service import (
        get_questionnaire_engine, QP_CROSSREACT, Q_FOOD_GENERAL, QP_OTHER_SYMPTOM,
        QP_ANIMAL_WORSE, _key)
    from services.report_service import get_report_service
    rs = get_relevance_service()
    eng = get_questionnaire_engine()
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="복합"),
                    results=[_mast("Cat dander", "고양이 비듬", 5, 3, AllergenCategory.ANIMAL, idx=1)])
    res = rs.build_assessments(ocr, None)
    q = eng.build(res, None)
    allq = {qq["id"]: qq for s in q["sections"] for qq in s["questions"]}
    # 동물도 교차반응 질문을 받고, 게이트(reveal_if_any)가 걸려 있음
    cq = [qq for qid, qq in allq.items() if qid.startswith(QP_CROSSREACT)]
    assert cq, "동물(고양이) 교차반응 질문 누락"
    assert all(qq.get("reveal_if_any") for qq in cq), "교차반응 질문에 증상 게이트(reveal) 없음"
    assert Q_FOOD_GENERAL in allq, "종합 음식반응 catch-all 누락"

    # 재활성(Q-5): 무증상이라 숨겨졌어도 catch-all 에서 후보 음식 지목 → confirmed
    from services.crossreactivity_service import get_crossreactivity_service as _svc
    cat_cands = [c["name"] for c in _svc().candidate_foods("Cat dander", "고양이 비듬", limit=8)]
    pick = cat_cands[0]
    eng.classify(res, {Q_FOOD_GENERAL: [pick]}, None)
    assert res.assessments[0].crossreact_confirmed, "catch-all 재활성 confirmed 실패"

    # 리포트: 원인 항원별 그룹 + 확대 가능성 경고(R-1/R-2)
    md = get_report_service().build_patient_report_markdown(res, {"name": "복합"}, None)
    assert "앞으로 주의해서 관찰할 음식" in md, "교차반응 관찰 섹션 누락"
    assert "「고양이 비듬」과" in md, "원인 항원별 그룹 헤더 누락"
    assert "확대 가능성" in md, "OAS/교차반응 확대 경고(R-2) 누락"

    # Q-1: 미분류(other) 양성 항원도 증상 질문을 받고 판정됨
    ocr2 = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="신종"),
                     results=[_mast("완전신종물질ZZZ", "완전신종물질ZZZ", 5, 3, AllergenCategory.OTHER, idx=1)])
    res2 = rs.build_assessments(ocr2, None)
    q2 = eng.build(res2, None)
    oq = [qid for s in q2["sections"] for qq in s["questions"]
          for qid in [qq["id"]] if qid.startswith(QP_OTHER_SYMPTOM)]
    assert oq, "미분류 양성 항원 증상 질문 누락(fallthrough)"
    eng.classify(res2, {QP_OTHER_SYMPTOM + _key(0): ["skin"]}, None)
    assert res2.assessments[0].relevance == ClinicalRelevance.CLINICALLY_RELEVANT, "미분류 항원 증상 판정 실패"
    print("✓ 교차반응 게이트/재활성 + 동물 교차반응 + other 누락차단 + 리포트 그룹화·확대경고")


def test_crossreact_risk_vs_confirmed_item1():
    """item1: 교차반응을 confirmed(증상보고)/risk(가능성)로 분리 + 리포트/카드뉴스/FHIR 반영."""
    from services.crossreactivity_service import get_crossreactivity_service
    if not get_crossreactivity_service().has_data():
        print("✓ (skip) 레지스트리 미생성 — item1 교차반응 스킵")
        return
    from services.questionnaire_service import get_questionnaire_engine, QP_CROSSREACT, _key
    from services.fhir_service import FHIRService
    from services.report_service import get_report_service
    from services.cardnews_service import get_cardnews_service
    rs = get_relevance_service()
    ocr = OCRResult(
        test_type=TestType.MAST, patient=PatientInfo(name="교차", test_date="2026-07-13"),
        results=[_mast("Celery", "샐러리", 5.0, 3, AllergenCategory.FOOD, idx=1)],
    )
    res = rs.build_assessments(ocr, None)
    eng = get_questionnaire_engine()
    eng.build(res, None)
    # 당근만 증상보고(confirmed) → 나머지 후보(사과 등)는 risk 로 분류돼야 함
    ans = {QP_CROSSREACT + _key(0): ["Carrot"]}
    eng.classify(res, ans, None)
    celery = res.assessments[0]
    assert "당근" in celery.crossreact_confirmed, f"confirmed 에 당근 없음: {celery.crossreact_confirmed}"
    assert "당근" not in celery.crossreact_risk, "당근이 risk 에 중복"
    assert celery.crossreact_risk, f"risk 후보가 비어있음: {celery.crossreact_risk}"
    assert "사과" in celery.crossreact_risk, f"미선택 후보(사과)가 risk 에 없음: {celery.crossreact_risk}"

    # FHIR note: confirmed/risk 각각 문구 반영
    bundles = FHIRService().build_bundles_from_relevance(
        ocr, res, None, eng.crossreactive_food_items(res.assessments, ans))
    ai = [e["resource"] for e in bundles["allergy_intolerance_bundle"]["entry"]]
    celery_ai = next(r for r in ai if "샐러리" in r["code"]["text"] and r["code"]["text"] != "당근")
    note = " ".join(n["text"] for n in celery_ai.get("note", []))
    assert "교차반응(확인됨)" in note and "당근" in note, f"FHIR confirmed 노트 누락: {note}"
    assert "교차반응 가능(미확인)" in note and "사과" in note, f"FHIR risk 노트 누락: {note}"
    # 확인된 교차반응(당근)은 별도 AllergyIntolerance 로 존재
    assert any(r["code"]["text"] == "당근" for r in ai), "확인된 교차반응(당근) 별도 항목 누락"

    # 리포트: confirmed 요약(item1.2 — 확인된 성분 교차반응을 리포트에 반영)
    md = get_report_service().build_patient_report_markdown(res, {}, None)
    assert "반드시 함께 주의할 음식" in md and "당근" in md, "리포트 confirmed 교차반응(음식 동급 경고) 누락"
    # 카드뉴스: confirmed 카드 생성
    html = get_cardnews_service().generate_html(res, {}, None)
    assert "반드시 주의할 음식" in html and "당근" in html, "카드뉴스 음식 중심 경고 카드 누락"
    print("✓ item1 교차반응 risk/confirmed 분리 + FHIR·리포트·카드뉴스 반영")


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
        get_questionnaire_engine, Q_PATTERN, QP_POLLEN, Q_INDOOR_TIMING, Q_MITE_DUST, Q_OAS,
        QP_CROSSREACT, _key,
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
    eng.build(res, None)
    # A2 이후 OAS 음식은 꽃가루(자작, idx1) 항원별 교차반응 문항에서 구체적으로 받는다
    answers = {Q_PATTERN: "both", QP_POLLEN + "spring_tree": "yes",
               Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes", Q_OAS: "yes",
               QP_CROSSREACT + _key(1): ["apple"]}
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
    assert "112476003" in obs_codes and obs_codes["112476003"] == "Dermatophagoides farinae", \
        f"D.farinae SCTID/display 누락: {obs_codes}"
    assert "256262001" in obs_codes and obs_codes["256262001"] == "European white birch pollen", \
        f"Birch SCTID/display 누락: {obs_codes}"
    # AllergyIntolerance.code 도 같은 SCTID 사용
    ai_codes = [e["resource"]["code"]["coding"][0]["code"]
                for e in ai if e["resource"]["code"].get("coding")]
    assert "256262001" in ai_codes, f"Birch AllergyIntolerance SCTID 누락: {ai_codes}"
    print("✓ FHIR v2 bundles (all-obs + performer + AllergyIntolerance status + OAS food + CDM SNOMED coding)")


def test_cdm_snomed_mapping():
    """병원 제공 CDM(OMOP) 기매핑 엑셀 → 알러젠별 concept_id·concept_name·vocabulary 조회."""
    from utils.allergen_mapper import get_allergen_mapper
    m = get_allergen_mapper()
    assert len(getattr(m, "cdm_entries", [])) >= 150, "CDM 기매핑 항원 수 부족"
    # CDM 조회 자체는 그대로 동작 (concept_id·concept_name)
    cdm_cases = [
        ("Birch", "765811", "Birch pollen"),
        ("Dermatophagoides farinae", "46273588", "Dermatophagoides farinae protein"),
        ("Cat", "4125384", "Cat dander"),
        ("Cat hair", "4125384", "Cat dander"),  # 신규 별칭
        ("Squid", "42536271", "Squid"),          # 신규 음식 항원
    ]
    for name, cid, disp in cdm_cases:
        e = m.cdm_find(name, "")
        assert e and str(e["concept_id"]) == cid and e["concept_name"] == disp, f"{name} CDM 조회 오류: {e}"
    # get_coding 은 실제 SCTID 를 CDM 보다 우선한다(2026-09 확장)
    c = m.get_coding("Birch", "")
    assert c["system"] == "http://snomed.info/sct" and c["code"] == "256262001", c
    # SCTID 가 없는 혼합 항원은 CDM 으로 폴백하되, OMOP concept_id 를 SNOMED 로 위장하지 않는다
    c = m.get_coding("Tree mixture 1", "")
    assert c["system"] == m.OMOP_SYSTEM and c["code"] == "36684363", c
    # 미매핑 항원은 None (예외 없이)
    assert m.get_coding("완전신종알러젠ZZZ", "") is None
    print("✓ CDM(OMOP) SNOMED 기매핑 로드 및 concept 조회(신규 별칭 포함)")


def test_allergen_registry_p0():
    """P0: 단일 항원 레지스트리 + 성분(component) 교차반응 파생 데이터 검증."""
    import json
    root = Path(__file__).resolve().parent
    reg_path = root / "data" / "allergens.json"
    if not reg_path.exists():
        print("✓ (skip) data/allergens.json 미생성 — scripts/build_allergen_registry.py 필요")
        return
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    _fam = json.loads((root / "data" / "allergen_components.json").read_text(encoding="utf-8"))["families"]
    comp = {f["id"]: f for f in _fam} if isinstance(_fam, list) else _fam  # WHO/IUIS ingest: families 리스트
    antigens = reg["antigens"]
    by = {a["canonical_name"]: a for a in antigens}
    # 대부분 항원이 코드 보유(FHIR 정합). 일부 신규 음식 항원(참깨·캐슈 등)은 CDM 서브셋에
    # OMOP 코드가 없어 text-only coding 이 됨(유효). 커버리지로 검증.
    coded = sum(1 for a in antigens if a["coding"].get("snomed_ct") or a["coding"]["omop_concept_id"] or a["coding"]["snomed"])
    assert coded >= 115, f"코드 보유 항원 부족: {coded}/{len(antigens)}"
    # category 보정: 성분 근거로 food 로 교정된 항원들
    for food in ["Apple", "Celery", "Shrimp", "Lobster", "Peach", "Cod"]:
        assert by[food]["category"] == "food", f"{food} category 보정 실패: {by[food]['category']}"

    # 성분 역인덱스로 교차반응 후보 파생
    from collections import defaultdict
    import re
    def norm(s): return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())
    name2a = {}
    for a in antigens:
        for nm in [a["canonical_name"], a.get("korean_name")] + a.get("aliases", []):
            if nm: name2a.setdefault(norm(nm), a)
    c2a = defaultdict(list)
    for a in antigens:
        for c in a.get("components", []):
            c2a[c].append(a["canonical_name"])
    def crossreact(q):
        a = name2a[norm(q)]
        out = set()
        for c in a.get("components", []):
            out |= {x for x in c2a[c] if x != a["canonical_name"]}
        return out
    # 새우 → tropomyosin 공유로 진드기·다른 갑각/연체 후보(음식↔음식 + 진드기↔갑각류)
    sh = crossreact("Shrimp")
    assert "Dermatophagoides pteronyssinus" in sh and "Lobster" in sh, f"새우 교차반응 파생 실패: {sh}"
    # 셀러리 → PR-10/nsLTP 공유로 당근·사과·복숭아
    ce = crossreact("Celery")
    assert "Carrot" in ce and "Apple" in ce, f"셀러리 교차반응 파생 실패: {ce}"
    # 대구 → parvalbumin 공유로 고등어·연어
    cod = crossreact("Cod")
    assert "Mackerel" in cod and "Salmon" in cod, f"대구 교차반응 파생 실패: {cod}"
    print("✓ P0 항원 레지스트리 + 성분 교차반응 파생(새우↔진드기·갑각, 셀러리↔당근·사과, 대구↔생선)")


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


def test_cardnews_quest_theme():
    """카드뉴스 도감 테마: 판정 도장·카테고리 스탬프·서사 문구 + 기존 마커 유지"""
    from services.cardnews_service import get_cardnews_service, _CATEGORY_STAMP_SVG
    rs = get_relevance_service()
    res = rs.build_assessments(build_case(), None)
    for a in res.assessments:
        a.answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "yes", Q_REPRODUCIBLE: "yes"}
    rs.classify_all(res)
    html = get_cardnews_service().generate_html(res, {"name": "테스트", "test_date": "2026-06-01"})
    assert "알러젠 탐험 리포트" in html, "표지 서사 문구 누락"
    assert 'class="stamp-verdict' in html and "진범 확정" in html, "진범 확정 도장 누락"
    assert "무혐의 · 감작만" in html and "감작은 남아 있어 추적 필요" in html, "무혐의 도장/추적 부연 누락"
    assert 'class="cat-stamp' in html and "<svg" in html, "카테고리 스탬프 SVG 누락"
    assert "Do+Hyeon" in html, "디스플레이 폰트 링크 누락"
    for c in ("mite", "animal", "pollen_tree", "pollen_grass", "pollen_weed", "mold", "insect", "food", "other"):
        assert _CATEGORY_STAMP_SVG[c].startswith("<svg"), c
    # 기존 마커 유지
    assert "카드뉴스" in html and "테스트" in html
    print("✓ 카드뉴스 도감 테마(도장·스탬프·서사) + 기존 마커 유지")


def test_fhir_observation_codes_methods_and_absent_values():
    """FHIR Observation 정밀화: 검증된 SNOMED CT 코드(code/method) + 0·N/A·undetectable 값 표현.
    코드 검증: tx.fhir.org SNOMED International 20250201 — 37968009 Prick test(procedure),
    399788006 Allergen specific IgE antibody measurement, MAST type, 397691009 ... quantitative,
    703446000 Immunoblot assay(technique), 703447009 Enzyme immunoassay technique, 703444002 Immunofluorescence technique."""
    from services.fhir_service import FHIRService
    fs = FHIRService()
    SCT = "http://snomed.info/sct"

    def codes(obs, key):
        return {c["code"]: c for c in obs.get(key, {}).get("coding", []) if c.get("system") == SCT}

    # --- MAST: 0 값·N/A·undetectable 모두 Observation 으로 표현
    rows = [
        _mast("Dermatophagoides farinae", "집먼지진드기", 17.6, 4, AllergenCategory.MITE, idx=1),
        _mast("Dog dander", "개 비듬", 0.0, 0, AllergenCategory.ANIMAL, interp=InterpretationType.NEGATIVE, idx=2),
        AllergenResult(index=3, raw_text="Egg white <0.35", allergen_name="Egg white", korean_name="난백",
                       value=None, value_text="<0.35", unit="kU/L", class_value=0,
                       interpretation=InterpretationType.NEGATIVE),
        AllergenResult(index=4, raw_text="Peanut N/A", allergen_name="Peanut", korean_name="땅콩",
                       value=None, value_text="N/A", unit="kU/L", class_value=None, interpretation=None),
        AllergenResult(index=5, raw_text="Soybean undetectable", allergen_name="Soybean", korean_name="대두",
                       value=None, value_text="undetectable", unit="kU/L", class_value=0,
                       interpretation=InterpretationType.NEGATIVE),
    ]
    ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="코드", test_date="2026-06-01"), results=rows)
    b = fs.create_observation_bundle(ocr)
    obs = [e["resource"] for e in b["entry"]]
    assert len(obs) == 5, "0·N/A·undetectable 행도 Observation 으로 포함되어야 함"
    o = {x["code"]["text"].split(" - ")[-1]: x for x in obs}
    # code: MAST type
    assert "399788006" in codes(obs[0], "code"), f"MAST Observation.code SCTID 누락: {obs[0]['code']}"
    assert "165967004" not in codes(obs[0], "code"), "존재하지 않는 SCTID 165967004 사용 금지"
    # method: Immunoblot assay (technique)
    assert "703446000" in codes(obs[0], "method"), f"MAST method 703446000 누락: {obs[0].get('method')}"
    # 0 값은 valueQuantity 0
    assert o["Dog dander"]["valueQuantity"]["value"] == 0.0
    # class component (0-6)
    cls = [c for c in o["Dog dander"].get("component", []) if c["code"].get("text", "").startswith("IgE class")]
    assert cls and cls[0]["valueInteger"] == 0, "IgE class component 누락"
    # <0.35 → comparator
    vq = o["Egg white"]["valueQuantity"]
    assert vq.get("comparator") == "<" and vq["value"] == 0.35, f"comparator 표현 실패: {vq}"
    # undetectable → 검출한계 미만(comparator <, 0.35 kU/L)
    vq2 = o["Soybean"]["valueQuantity"]
    assert vq2.get("comparator") == "<" and vq2["value"] == 0.35, f"undetectable 표현 실패: {vq2}"
    # N/A → dataAbsentReason, valueQuantity 없음
    na = o["Peanut"]
    assert "valueQuantity" not in na and na["dataAbsentReason"]["coding"][0]["code"] in ("not-performed", "unknown"), na

    # --- UniCAP: quantitative + FEIA method (EIA technique 1순위, immunofluorescence 2순위)
    ocr_u = OCRResult(test_type=TestType.UNICAP, patient=PatientInfo(name="유"), results=[rows[0]])
    ou = fs.create_observation_bundle(ocr_u)["entry"][0]["resource"]
    assert "397691009" in codes(ou, "code"), ou["code"]
    m = codes(ou, "method")
    assert "703447009" in m and "703444002" in m, f"UniCAP method 누락: {ou.get('method')}"

    # --- SPT: Prick test(procedure) + 값 없으면 dataAbsentReason, 기존 OMOP 히스타민 대조 코딩은 유지
    spt_rows = [
        AllergenResult(index=1, raw_text="D.f 5x3", allergen_name="Dermatophagoides farinae", korean_name="집먼지진드기",
                       size_text="5x3", unit="mm", interpretation=InterpretationType.POSITIVE),
        AllergenResult(index=2, raw_text="Cat -", allergen_name="Cat dander", korean_name="고양이 비듬",
                       size_text=None, mean_mm=None, unit="mm", interpretation=None),
    ]
    ocr_s = OCRResult(test_type=TestType.SPT, patient=PatientInfo(name="에스", histamine_mean_mm=3.0), results=spt_rows)
    so = [e["resource"] for e in fs.create_observation_bundle(ocr_s)["entry"]]
    assert "37968009" in codes(so[0], "code"), so[0]["code"]
    assert "398166005" not in codes(so[0], "code"), "'Performed'(398166005) 를 SPT 코드로 쓰면 안 됨"
    assert so[0]["valueQuantity"]["value"] == 4.0
    assert "valueQuantity" not in so[1] and so[1].get("dataAbsentReason"), so[1]
    assert any(c.get("system", "").startswith("https://athena") for c in so[0]["method"]["coding"]), "OMOP 히스타민 대조 코딩 유지"
    print("✓ FHIR Observation 정밀화: 검증된 SCTID code/method + 0·<LoD·N/A 값 표현 + class component")


def test_mold_vs_mite_discrimination():
    """곰팡이·집먼지진드기 동시 양성: '습할 때 악화' 만으로는 구분 불가 → 보류.
    곰팡이 특이 단서(늘 젖은 공간·실외 포자·제습 후 호전)가 있어야 원인으로 인정한다."""
    from services.questionnaire_service import (
        get_questionnaire_engine, Q_INDOOR_TIMING, Q_MITE_DUST, Q_MOLD_DAMP,
        Q_MOLD_SPACE, Q_MOLD_OUTDOOR, Q_MOLD_DEHUM_TRIAL, Q_MITE_BEDDING_TRIAL, mold_habitat,
    )
    rs = get_relevance_service()

    def build():
        ocr = OCRResult(
            test_type=TestType.MAST,
            patient=PatientInfo(name="곰"),
            results=[
                _mast("Dermatophagoides farinae", "집먼지진드기", 20.0, 4, AllergenCategory.MITE, idx=1),
                _mast("Alternaria alternata", "얼터나리아", 5.0, 3, AllergenCategory.MOLD, idx=2),
            ],
        )
        res = rs.build_assessments(ocr, None)
        get_questionnaire_engine().build(res, None)
        return res

    def mold_of(res):
        return [a for a in res.assessments if "lternaria" in a.allergen_name][0]

    def mite_of(res):
        return [a for a in res.assessments if "farinae" in a.allergen_name][0]

    eng = get_questionnaire_engine()

    # 문진에 감별 문항이 실제로 생성되는가
    res0 = build()
    q = eng.build(res0, None)
    ids = {qq["id"] for sec in q["sections"] for qq in sec["questions"]}
    for qid in (Q_MOLD_SPACE, Q_MOLD_OUTDOOR, Q_MITE_BEDDING_TRIAL, Q_MOLD_DEHUM_TRIAL):
        assert qid in ids, f"감별 문항 누락: {qid}"

    # ① 습할 때 악화만 → 진드기와 구분 불가 → 곰팡이는 보류
    res = build()
    eng.classify(res, {Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes", Q_MOLD_DAMP: "yes"}, None)
    assert mold_of(res).relevance == ClinicalRelevance.INDETERMINATE, "구분 불가인데 곰팡이를 단정함"
    assert "구분할 수 없습니다" in mold_of(res).rationale_ko
    assert mite_of(res).relevance == ClinicalRelevance.CLINICALLY_RELEVANT

    # ② 실외 포자 단서(낙엽·비 온 뒤) → 곰팡이 원인 확정
    res = build()
    eng.classify(res, {Q_INDOOR_TIMING: "yes", Q_MITE_DUST: "yes", Q_MOLD_DAMP: "yes",
                       Q_MOLD_OUTDOOR: ["leaves", "rain"]}, None)
    assert mold_of(res).relevance == ClinicalRelevance.CLINICALLY_RELEVANT
    assert "실외" in mold_of(res).rationale_ko

    # ③ 늘 젖은 공간 단서 → 곰팡이 원인 확정
    res = build()
    eng.classify(res, {Q_MOLD_DAMP: "yes", Q_MOLD_SPACE: ["bathroom", "water_damage"]}, None)
    assert mold_of(res).relevance == ClinicalRelevance.CLINICALLY_RELEVANT

    # ④ 침구 관리로만 호전 + 제습 미시도 → 진드기 쪽으로 기울되 곰팡이는 보류
    res = build()
    eng.classify(res, {Q_MOLD_DAMP: "yes", Q_MITE_BEDDING_TRIAL: "better",
                       Q_MOLD_DEHUM_TRIAL: "never"}, None)
    assert mold_of(res).relevance == ClinicalRelevance.INDETERMINATE
    assert "집먼지진드기로 설명될 가능성" in mold_of(res).rationale_ko
    assert mite_of(res).relevance == ClinicalRelevance.CLINICALLY_RELEVANT, "침구 조치 호전이 진드기 근거로 반영되지 않음"

    # ⑤ 제습 후 호전 → 곰팡이 원인 확정
    res = build()
    eng.classify(res, {Q_MOLD_DAMP: "unsure", Q_MOLD_DEHUM_TRIAL: "better"}, None)
    assert mold_of(res).relevance == ClinicalRelevance.CLINICALLY_RELEVANT

    # ⑥ 명시적 부정 → 감작만 + 진드기로 설명된다는 안내
    res = build()
    eng.classify(res, {Q_INDOOR_TIMING: "yes", Q_MOLD_DAMP: "no", Q_MOLD_SPACE: ["none"]}, None)
    assert mold_of(res).relevance == ClinicalRelevance.SENSITIZED_ONLY
    assert "집먼지진드기로 설명" in mold_of(res).rationale_ko

    # 서식지 구분(실외/실내)에 따라 회피 조언이 갈린다
    assert mold_habitat(mold_of(res)) == "outdoor"
    print("✓ 곰팡이 ↔ 집먼지진드기 감별(공간·실외 포자·환경 조치 반응 기반)")


def test_real_sctid_coverage_expanded():
    """실제 SNOMED CT SCTID 확장(22 → 139종). 코드는 tx.fhir.org(SNOMED International)로
    존재·활성 확인된 것만 넣는다. 미매핑 항원은 OMOP(CDM) 로 폴백하되 SNOMED 로 위장하지 않는다."""
    import json
    from utils.allergen_mapper import get_allergen_mapper
    root = Path(__file__).resolve().parent
    reg = json.loads((root / "data" / "allergens.json").read_text())["antigens"]
    unmapped = {x["canonical_name"] for x in
                json.loads((root / "data" / "snomed_ct_unmapped.json").read_text())["items"]}
    m = get_allergen_mapper()

    covered, omop = [], []
    for a in reg:
        c = m.get_coding(a["canonical_name"], a.get("korean_name") or "")
        assert c, f"코딩 없음: {a['canonical_name']}"
        (covered if c["system"] == "http://snomed.info/sct" else omop).append(a["canonical_name"])
    assert len(covered) >= 135, f"SCTID 커버리지 부족: {len(covered)}/148"
    # 폴백 항원은 명시적으로 미매핑 목록에 있어야 한다(조용한 누락 방지)
    assert set(omop) <= unmapped, f"미매핑 목록에 없는 폴백 항원: {set(omop) - unmapped}"

    # 카테고리별 대표값 — 꽃가루는 식물이 아니라 'pollen' 개념, 동물은 비듬 개념
    cases = {
        "Mugwort": ("256293000", "Mugwort pollen"),
        "Oak": ("256270006", "Oak pollen"),
        "Birch": ("256262001", "European white birch pollen"),
        "Cat dander": ("260152009", "Cat dander"),
        "Chicken": ("260165000", "Chicken feathers"),
        "Alternaria alternata": ("36703000", "Alternaria alternata"),
        "Hazelnut": ("256353000", "Hazelnut"),   # 성분(Cor a 8) 아님
        "Shrimp": ("278840001", "Shrimp"),
    }
    for name, (code, disp) in cases.items():
        c = m.get_coding(name, "")
        assert c["system"] == "http://snomed.info/sct" and c["code"] == code and c["display"] == disp, \
            f"{name} SCTID 매핑 오류: {c}"
    # OCR 수식어 흡수: 'Birch pollen' 도 같은 SCTID 로 해석
    assert m.get_coding("Birch pollen", "")["code"] == "256262001"
    print(f"✓ 실제 SNOMED CT SCTID 확장 ({len(covered)}/148 매핑, {len(omop)}종 OMOP 폴백)")


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
