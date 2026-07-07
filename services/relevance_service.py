"""
Relevance Service — 임상적 의미(감작 vs 실제 알레르기) 감별 엔진

핵심 원리:
- 알레르기 검사 양성은 '감작(sensitization)'을 의미할 뿐이다.
- 실제 임상적 알레르기는 '해당 알러젠에 노출될 때(또는 그 계절에) 증상이 재현성 있게
  유발/악화될 때' 성립한다.
- 따라서 각 양성 알러젠에 대해 (1) 노출경험, (2) 노출/시즌 시 증상악화, (3) 재현성 을
  구조화 질문으로 확인하고, 알러젠의 backdata(계절성·노출환경)와 대조하여 판정한다.

판정:
- clinically_relevant : 노출/시즌 시 증상이 재현성 있게 유발됨 → 진짜 알레르기
- sensitized_only     : 노출은 있으나 증상 없음 → 감작만 됨(임상적 의미 낮음)
- indeterminate       : 노출경험/정보 부족으로 판정 보류
"""

import logging
from typing import Any, Dict, List, Optional

from models.schemas import (
    OCRResult,
    AllergenResult,
    TestType,
    InterpretationType,
    AllergenAssessment,
    RelevanceAssessmentResult,
    ClinicalRelevance,
    ScreeningProfile,
)
from services.knowledge_service import get_knowledge_service, normalize_category

logger = logging.getLogger(__name__)

# 답변 옵션
ANSWER_YES = "yes"
ANSWER_NO = "no"
ANSWER_UNSURE = "unsure"
ANSWER_OPTIONS = [ANSWER_YES, ANSWER_NO, ANSWER_UNSURE]
ANSWER_LABELS_KO = {ANSWER_YES: "예", ANSWER_NO: "아니오", ANSWER_UNSURE: "잘 모르겠음"}

# 구조화 질문 ID
Q_EXPOSED = "exposed"
Q_SYMPTOM = "symptom_on_exposure"
Q_REPRODUCIBLE = "reproducible"


def _month_overlap(peak_months: List[int], worse_months: List[int]) -> Optional[bool]:
    if not peak_months:
        return None
    if not worse_months:
        return None
    return len(set(peak_months) & set(worse_months)) > 0


def _symptom_question_text(category: str, kb: Dict[str, Any]) -> str:
    cat = normalize_category(category)
    season = kb.get("season_label_ko") or "해당 시기"
    if cat in ("pollen_tree", "pollen_grass", "pollen_weed"):
        return f"{season}에 코·눈 증상(재채기·콧물·코막힘·눈가려움)이 뚜렷하게 심해지나요?"
    if cat == "mite":
        return "청소·이불정리·먼지 노출 시, 또는 아침 기상 직후에 증상이 심해지나요?"
    if cat == "animal":
        return "그 동물과 접촉하면 수분 내로 코·눈·피부·호흡기 증상이 생기나요?"
    if cat == "mold":
        return "습한 환경(장마철·곰팡이 있는 공간)에 노출되면 증상이 심해지나요?"
    if cat == "insect":
        return "그 곤충(예: 바퀴벌레)이 있는 환경에서 만성 비염·천식 증상이 있나요?"
    if cat == "food":
        return "그 음식을 먹으면 반복적으로 증상(두드러기·구강·소화기·호흡기)이 생기나요?"
    return "이 알러젠에 노출될 때 알레르기 증상이 생기거나 심해지나요?"


def _exposure_question_text(category: str) -> str:
    cat = normalize_category(category)
    if cat == "food":
        return "이 음식을 먹어본 경험이 있나요?"
    if cat == "animal":
        return "이 동물과 접촉하거나 함께 지낸 경험이 있나요?"
    return "이 알러젠과 관련된 환경에 노출된 경험이 있나요?"


class RelevanceService:
    """양성 알러젠의 임상적 의미 감별 엔진"""

    def __init__(self):
        self.kb = get_knowledge_service()

    # ---------- 강도 판정 ----------
    @staticmethod
    def compute_strength(
        test_type: TestType,
        mean_mm: Optional[float],
        value: Optional[float],
        class_value: Optional[Any],
    ) -> Optional[str]:
        """감작 강도(weak/moderate/strong) 계산"""
        # MAST/UniCAP class
        if class_value is not None:
            try:
                c = int(class_value)
                if c <= 0:
                    return None
                if c <= 1:
                    return "weak"
                if c <= 3:
                    return "moderate"
                return "strong"
            except (ValueError, TypeError):
                pass
        # MAST/UniCAP 정량값 (kU/L)
        if test_type == TestType.MAST and value is not None:
            if value < 0.35:
                return None
            if value < 3.5:
                return "weak"
            if value < 17.5:
                return "moderate"
            return "strong"
        # SPT wheal (mm)
        mm = mean_mm if mean_mm is not None else (value if test_type == TestType.SPT else None)
        if mm is not None:
            if mm < 3:
                return None
            if mm < 5:
                return "weak"
            if mm < 8:
                return "moderate"
            return "strong"
        return None

    @staticmethod
    def _is_positive(r: AllergenResult, test_type: TestType) -> bool:
        if r.interpretation is not None:
            if isinstance(r.interpretation, InterpretationType):
                return r.interpretation == InterpretationType.POSITIVE
            return str(r.interpretation).lower() in ("positive", "p", "양성")
        # interpretation 없을 때 수치 기반
        value = r.value if r.value is not None else r.mean_mm
        if test_type == TestType.SPT:
            return (r.mean_mm or value or 0) >= 3.0
        if r.class_value is not None:
            try:
                return int(r.class_value) >= 1
            except (ValueError, TypeError):
                pass
        return (value or 0) >= 0.35

    # ---------- 평가 초기화 ----------
    def build_assessments(
        self,
        ocr_result: OCRResult,
        screening: Optional[ScreeningProfile] = None,
    ) -> RelevanceAssessmentResult:
        """OCR 결과의 양성 알러젠에 대해 backdata 를 붙이고 초기 평가 객체를 만든다."""
        assessments: List[AllergenAssessment] = []
        worse_months = screening.worse_months if screening else []

        for r in ocr_result.results:
            if not self._is_positive(r, ocr_result.test_type):
                continue

            kb = self.kb.get_backdata(
                name=r.allergen_name,
                category=str(r.category) if r.category else None,
                korean_name=r.korean_name,
            )
            strength = self.compute_strength(
                ocr_result.test_type, r.mean_mm, r.value, r.class_value
            )
            season_overlap = _month_overlap(kb.get("peak_months_korea", []), worse_months)

            assessment = AllergenAssessment(
                allergen_name=r.allergen_name,
                korean_name=r.korean_name or kb.get("korean_name"),
                category=normalize_category(kb.get("category")),
                test_value=r.value if r.value is not None else r.mean_mm,
                test_unit=r.unit or ("mm" if ocr_result.test_type == TestType.SPT else None),
                class_value=r.class_value,
                strength=strength,
                kb=kb,
                answers={},
                season_overlap=season_overlap,
                relevance=ClinicalRelevance.NOT_ASSESSED,
            )
            # 스크리닝 기반 자동 제안 답변
            self.apply_screening_suggestion(assessment, screening)
            assessments.append(assessment)

        return RelevanceAssessmentResult(
            patient_name=ocr_result.patient.name,
            test_date=ocr_result.patient.test_date,
            assessments=assessments,
        )

    # ---------- 질문 생성 ----------
    def build_questions(self, assessment: AllergenAssessment) -> List[Dict[str, Any]]:
        """알러젠별 구조화 감별 질문 목록 반환 (UI 렌더링용)."""
        kb = assessment.kb or {}
        cat = normalize_category(assessment.category)
        questions = [
            {
                "id": Q_EXPOSED,
                "question": _exposure_question_text(cat),
                "options": ANSWER_OPTIONS,
            },
            {
                "id": Q_SYMPTOM,
                "question": _symptom_question_text(cat, kb),
                "options": ANSWER_OPTIONS,
            },
            {
                "id": Q_REPRODUCIBLE,
                "question": "그 증상이 여러 번 반복적으로(재현성 있게) 나타나나요?",
                "options": ANSWER_OPTIONS,
            },
        ]
        return questions

    def probe_hints(self, assessment: AllergenAssessment) -> List[str]:
        """KB 의 자유형 감별 질문(참고용 힌트)."""
        return (assessment.kb or {}).get("relevance_probes_ko", []) or []

    # ---------- 스크리닝 기반 자동 제안 ----------
    def apply_screening_suggestion(
        self, assessment: AllergenAssessment, screening: Optional[ScreeningProfile]
    ) -> None:
        """스크리닝 정보로 답변을 미리 제안(사용자가 수정 가능)."""
        if not screening:
            return
        cat = normalize_category(assessment.category)
        # 계절성 알러젠: 환자 악화 시즌과 알러젠 시즌이 겹치면 증상악화 '예' 제안
        if cat in ("pollen_tree", "pollen_grass", "pollen_weed", "mold"):
            if assessment.season_overlap is True:
                assessment.answers.setdefault(Q_SYMPTOM, ANSWER_YES)
                assessment.answers.setdefault(Q_EXPOSED, ANSWER_YES)
            elif assessment.season_overlap is False and screening.season_pattern.value in ("seasonal", "both"):
                # 다른 계절에 악화되고 이 알러젠 시즌엔 아님 → 증상악화 '아니오' 제안
                assessment.answers.setdefault(Q_SYMPTOM, ANSWER_NO)

    # ---------- 판정 ----------
    def classify(self, assessment: AllergenAssessment) -> AllergenAssessment:
        """구조화 답변으로 임상적 의미를 판정하고 근거 문구를 생성한다."""
        a = assessment.answers or {}
        exposed = a.get(Q_EXPOSED, ANSWER_UNSURE)
        symptom = a.get(Q_SYMPTOM, ANSWER_UNSURE)
        reproducible = a.get(Q_REPRODUCIBLE, ANSWER_UNSURE)
        cat = normalize_category(assessment.category)
        name = assessment.korean_name or assessment.allergen_name

        if symptom == ANSWER_YES:
            assessment.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            extra = ""
            if reproducible == ANSWER_YES:
                extra = " 증상이 반복적으로 재현되어 임상적 의미가 분명합니다."
            elif reproducible == ANSWER_NO:
                extra = " 다만 재현성이 뚜렷하지 않아 경과 관찰이 필요합니다."
            season_note = ""
            if assessment.season_overlap is True:
                season_note = f" 환자분의 증상 악화 시기가 {name}의 노출 시즌과 일치합니다."
            assessment.rationale_ko = (
                f"검사 양성이며, 노출/해당 시즌에 증상이 유발·악화됩니다.{season_note}{extra} "
                f"→ 임상적으로 의미 있는 알레르기로 판단됩니다."
            )
        elif exposed == ANSWER_YES and symptom == ANSWER_NO:
            assessment.relevance = ClinicalRelevance.SENSITIZED_ONLY
            note = ""
            if cat == "food":
                note = " 현재 문제없이 섭취 중이라면 불필요한 식이 제한을 피하는 것이 좋습니다."
            elif cat in ("pollen_tree", "pollen_grass", "pollen_weed"):
                note = " 해당 꽃가루 시즌에도 증상이 없다면 회피가 필수는 아닙니다."
            assessment.rationale_ko = (
                f"검사는 양성(감작)이지만, 노출에도 증상이 나타나지 않았습니다.{note} "
                f"→ 현재는 감작만 되어 있고 실제 알레르기 증상은 유발하지 않는 것으로 보입니다. "
                f"다만 향후 증상이 생기면 재평가가 필요합니다."
            )
        else:
            assessment.relevance = ClinicalRelevance.INDETERMINATE
            if exposed == ANSWER_NO:
                assessment.rationale_ko = (
                    "검사는 양성(감작)이나 아직 뚜렷한 노출 경험이 없어 실제 알레르기 여부를 "
                    "판단하기 어렵습니다. → 노출 시 증상 발생 여부를 관찰하세요."
                )
            else:
                assessment.rationale_ko = (
                    "노출·증상 정보가 불충분하여 임상적 의미를 확정하기 어렵습니다. "
                    "→ 노출 상황과 증상의 관계를 좀 더 관찰한 뒤 재평가가 필요합니다."
                )
        return assessment

    def classify_all(self, result: RelevanceAssessmentResult) -> RelevanceAssessmentResult:
        for a in result.assessments:
            self.classify(a)
        return result

    # ---------- 요약 ----------
    @staticmethod
    def summarize(result: RelevanceAssessmentResult) -> Dict[str, Any]:
        rel = result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        sens = result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        ind = result.by_relevance(ClinicalRelevance.INDETERMINATE)
        return {
            "total_positive": len(result.assessments),
            "clinically_relevant": [a.korean_name or a.allergen_name for a in rel],
            "sensitized_only": [a.korean_name or a.allergen_name for a in sens],
            "indeterminate": [a.korean_name or a.allergen_name for a in ind],
            "counts": {
                "clinically_relevant": len(rel),
                "sensitized_only": len(sens),
                "indeterminate": len(ind),
            },
        }


# 싱글톤
_relevance_service: Optional[RelevanceService] = None


def get_relevance_service() -> RelevanceService:
    global _relevance_service
    if _relevance_service is None:
        _relevance_service = RelevanceService()
    return _relevance_service
