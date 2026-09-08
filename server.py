"""
FastAPI 백엔드 — 알레르기 검사 환자용 리포트 플랫폼 (비-Streamlit 웹앱)

기존 services/ 는 프레임워크에 독립적이므로 그대로 재사용한다.
프론트엔드(web/)는 정적 파일로 서빙한다.

실행:
    uvicorn server:app --reload
    # 또는
    python server.py
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from models.schemas import (
    OCRResult, PatientInfo, AllergenResult, TestType, InterpretationType,
    ScreeningProfile, SymptomFeedback, ClinicalRelevance,
)
from services.knowledge_service import get_knowledge_service, normalize_category
from services.relevance_service import get_relevance_service, RelevanceService
from services.questionnaire_service import get_questionnaire_engine
from services.screening_service import get_screening_service
from services.report_service import get_report_service
from services.cardnews_service import get_cardnews_service
from services.fhir_service import FHIRService
from config.settings import BASE_DIR, settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="알레르기 검사 환자 리포트 플랫폼", version="2.0")


# ============================================================
# 요청 모델
# ============================================================
class AssessRequest(BaseModel):
    ocr: OCRResult
    screening: Optional[ScreeningProfile] = None


class ClassifyRequest(BaseModel):
    ocr: OCRResult
    screening: Optional[ScreeningProfile] = None
    answers: Dict[str, Any] = {}


# ============================================================
# 공통 헬퍼
# ============================================================
def _api_key_ok() -> bool:
    key = getattr(settings, "openai_api_key", None)
    return bool(key and key != "your_openai_api_key_here")


def _assessment_public(a) -> Dict[str, Any]:
    """assessment 를 프론트로 보낼 안전한 dict 로 축약."""
    kb = a.kb or {}
    return {
        "allergen_name": a.allergen_name,
        "korean_name": a.korean_name,
        "category": normalize_category(a.category),
        "test_value": a.test_value,
        "test_unit": a.test_unit,
        "class_value": a.class_value,
        "strength": a.strength,
        "relevance": a.relevance.value if a.relevance else "not_assessed",
        "rationale_ko": a.rationale_ko,
        "oas_foods": a.oas_foods or [],
        "crossreact_confirmed": getattr(a, "crossreact_confirmed", []) or [],
        "crossreact_risk": getattr(a, "crossreact_risk", []) or [],
        "season_label_ko": kb.get("season_label_ko", ""),
        "biology_ko": kb.get("biology_ko", ""),
        "exposure_environment_ko": kb.get("exposure_environment_ko", ""),
        "avoidance_control_ko": kb.get("avoidance_control_ko", []),
        "cross_reactivity_ko": kb.get("cross_reactivity_ko", ""),
        "oral_allergy_syndrome_ko": kb.get("oral_allergy_syndrome_ko", ""),
        "source": kb.get("source", "knowledge_base"),
        "sources": kb.get("sources", []),
    }


# ============================================================
# 엔드포인트
# ============================================================
def _build_info() -> Dict[str, Any]:
    """실행 중인 코드 버전 표식 — '업데이트했는데 반영이 안 된다' 를 즉시 판별하기 위한 진단용.
    git 커밋 + 문진 엔진 기능 플래그를 함께 노출한다."""
    commit = None
    try:
        import subprocess
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR),
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        pass
    feats = {}
    try:
        import services.questionnaire_service as qs
        eng_cls = qs.QuestionnaireEngine
        feats = {
            # 2차 고도화 반영 여부(항원별 구체 OAS·교차반응 중증도·임상그룹 통합)
            "crossreact_severity_q": hasattr(qs, "QP_CROSSREACT_SEV"),
            "food_catchall_q": hasattr(qs, "Q_FOOD_GENERAL"),
            "per_antigen_oas_q": hasattr(eng_cls, "_append_crossreact_questions"),
            # 아래가 True 면 구버전(모호한 통합 OAS 문항이 남아 있음)
            "legacy_merged_oas_q": hasattr(qs, "Q_OAS_FOODS"),
        }
        from services.clinical_group_service import get_clinical_group_service
        feats["clinical_groups"] = len(get_clinical_group_service().groups)
        feats["ui_modes"] = ["quest", "classic"] if (BASE_DIR / "web" / "classic" / "index.html").exists() else ["quest"]
        feats["fhir_observation_v3"] = True   # 검증된 SCTID code/method + 0·<LoD·N/A 값 표현
    except Exception as e:
        feats["error"] = str(e)
    return {"commit": commit, "features": feats}


@app.get("/api/health")
def health():
    ks = get_knowledge_service()
    sc = get_screening_service()
    return {
        "ok": True,
        "has_api_key": _api_key_ok(),
        "build": _build_info(),
        "kb": ks.stats(),
        "screening_options": {
            "diseases": sc.disease_options(),
            "medications": sc.medication_options(),
            "organ_systems": sc.organ_system_options(),
        },
    }


@app.get("/api/allergen")
def allergen_backdata(name: str, category: Optional[str] = None, korean_name: Optional[str] = None):
    """알러젠 이름으로 backdata 조회 (수동 추가 시 자동완성/정보카드용)."""
    ks = get_knowledge_service()
    kb = ks.get_backdata(name=name, category=category, korean_name=korean_name)
    return {
        "korean_name": kb.get("korean_name"),
        "canonical_name": kb.get("canonical_name"),
        "category": normalize_category(kb.get("category")),
        "season_label_ko": kb.get("season_label_ko", ""),
        "seasonality_pattern": kb.get("seasonality_pattern"),
        "indoor_outdoor": kb.get("indoor_outdoor"),
        "biology_ko": kb.get("biology_ko", ""),
        "source": kb.get("source"),
    }


@app.post("/api/ocr")
async def ocr(file: UploadFile = File(...)):
    """업로드 이미지에서 검사결과 추출 (OpenAI Vision 필요)."""
    if not _api_key_ok():
        raise HTTPException(
            status_code=400,
            detail="OpenAI API 키가 설정되지 않았습니다. .env에 OPENAI_API_KEY를 넣거나, "
                   "‘데모 데이터’ 또는 ‘직접 입력’으로 진행하세요.",
        )
    try:
        from services.ocr_service import get_ocr_service
        content = await file.read()
        result = get_ocr_service().extract_from_image(content)
        return JSONResponse(result.model_dump(by_alias=False))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("OCR 실패")
        raise HTTPException(status_code=422, detail=f"OCR 처리 중 오류: {e}")


@app.get("/api/ocr/demo")
def ocr_demo():
    """데모용 검사결과 (API 키 없이 전체 흐름 체험)."""
    demo = OCRResult(
        test_type=TestType.MAST,
        patient=PatientInfo(name="홍길동", age=32, gender="M", test_date="2026-06-15",
                            report_date="2026-06-17", facility="OO대학교병원 진단검사의학과",
                            ordering_provider="알레르기내과 김OO", patient_id_external="C1234567"),
        results=[
            AllergenResult(index=1, raw_text="D. farinae 4", allergen_name="Dermatophagoides farinae",
                           korean_name="집먼지진드기(D.farinae)", value=17.6, unit="kU/L",
                           class_value=4, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=2, raw_text="D. pteronyssinus 3", allergen_name="Dermatophagoides pteronyssinus",
                           korean_name="집먼지진드기(D.pteronyssinus)", value=8.2, unit="kU/L",
                           class_value=3, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=3, raw_text="Birch 3", allergen_name="Birch pollen",
                           korean_name="자작나무 꽃가루", value=6.1, unit="kU/L",
                           class_value=3, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=4, raw_text="Cat 2", allergen_name="Cat dander",
                           korean_name="고양이 비듬", value=1.4, unit="kU/L",
                           class_value=2, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=5, raw_text="Dog 0", allergen_name="Dog dander",
                           korean_name="개 비듬", value=0.1, unit="kU/L",
                           class_value=0, interpretation=InterpretationType.NEGATIVE),
            AllergenResult(index=6, raw_text="Ragweed 2", allergen_name="Ragweed pollen",
                           korean_name="돼지풀 꽃가루", value=1.1, unit="kU/L",
                           class_value=2, interpretation=InterpretationType.POSITIVE),
        ],
    )
    return JSONResponse(demo.model_dump(by_alias=False))


@app.post("/api/screening/summary")
def screening_summary(screening: ScreeningProfile = Body(...)):
    return get_screening_service().summarize(screening)


@app.post("/api/questionnaire")
def questionnaire(req: AssessRequest):
    """양성 알러젠에 대한 적응형 문진 생성."""
    rs = get_relevance_service()
    result = rs.build_assessments(req.ocr, req.screening)
    engine = get_questionnaire_engine()
    q = engine.build(result, req.screening)
    return {
        "assessments": [_assessment_public(a) for a in result.assessments],
        "questionnaire": q,
        "positive_count": len(result.assessments),
    }


@app.post("/api/classify")
def classify(req: ClassifyRequest):
    """문진 답변으로 감별 판정 + 리포트/카드뉴스 생성."""
    rs = get_relevance_service()
    result = rs.build_assessments(req.ocr, req.screening)
    engine = get_questionnaire_engine()
    engine.classify(result, req.answers, req.screening)

    patient_info = {
        "name": req.ocr.patient.name,
        "age": req.ocr.patient.age,
        "gender": req.ocr.patient.gender,
        "test_date": req.ocr.patient.test_date,
        "facility": getattr(req.ocr.patient, "facility", None),
        "report_date": getattr(req.ocr.patient, "report_date", None),
    }

    rsvc = get_report_service()
    report_md = rsvc.build_patient_report_markdown(result, patient_info, req.screening)
    try:
        import markdown as md_lib
        report_html = md_lib.markdown(report_md, extensions=["extra", "sane_lists"])
    except Exception:
        report_html = "<pre>" + report_md + "</pre>"
    # PDF/HTML 겸용 인쇄형 문서(단일 디자인 소스) — 화면 표시 + 다운로드 + 인쇄(PDF)
    report_document_html = rsvc.build_patient_report_html_document(result, patient_info, req.screening)

    cardnews_html = get_cardnews_service().generate_html(result, patient_info, req.screening)

    return {
        "assessments": [_assessment_public(a) for a in result.assessments],
        "summary": RelevanceService.summarize(result),
        "report_markdown": report_md,
        "report_html": report_html,
        "report_document_html": report_document_html,
        "cardnews_html": cardnews_html,
    }


@app.post("/api/fhir")
def fhir(req: ClassifyRequest):
    """FHIR 매핑.
    - Observation: 전체 검사결과(양성+음성) + 검사기관 performer
    - AllergyIntolerance: 양성/의심 알러젠 전부 (임상적 유의=confirmed, 감작·미확정=unconfirmed),
      criticality/clinicalStatus 코딩, 꽃가루-음식 교차반응(OAS) 음식도 포함
    """
    rs = get_relevance_service()
    result = rs.build_assessments(req.ocr, req.screening)
    engine = get_questionnaire_engine()
    engine.classify(result, req.answers, req.screening)
    cross_foods = engine.crossreactive_food_items(result.assessments, req.answers)

    fs = FHIRService()
    return fs.build_bundles_from_relevance(req.ocr, result, req.screening, cross_foods)


# ============================================================
# 정적 프론트엔드 서빙 (맨 마지막에 마운트)
# ============================================================
WEB_DIR = BASE_DIR / "web"
CLASSIC_DIR = WEB_DIR / "classic"
# 두 UI 동시 운영: /classic (재설계 이전 클래식 UI) 를 먼저 마운트하고, / (알러젠 탐험 퀘스트 UI) 를 마지막에.
# 두 UI 는 같은 /api 를 사용하므로 판정·FHIR 결과는 동일하다.
if CLASSIC_DIR.exists():
    app.mount("/classic", StaticFiles(directory=str(CLASSIC_DIR), html=True), name="web-classic")
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
