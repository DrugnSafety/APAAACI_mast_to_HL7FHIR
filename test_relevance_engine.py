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
