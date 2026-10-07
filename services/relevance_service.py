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
import re
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
    TestedNegative,
    TestControl,
    normalize_class_token,
    public_relevance,
)
from services.category_resolver import control_kind
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
        if test_type in (TestType.MAST, TestType.UNICAP) and value is not None:
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

    # 총 IgE 는 항원이 아니므로 '음성 항원' 목록에 넣지 않는다(검사 대조는 build_assessments 가 먼저 걸러 낸다)
    _NON_ALLERGEN = re.compile(r"total\s*ige|총\s*ige|총ige", re.I)

    @classmethod
    def result_status(cls, r: AllergenResult, test_type: TestType) -> str:
        """행 하나의 판정: positive / negative / equivocal / unknown.
        FHIR interpretation 과 '검사한 음성 항원' 목록이 같은 기준을 쓰도록 한 곳에 둔다."""
        if cls._is_positive(r, test_type):
            return "positive"
        if r.interpretation == InterpretationType.EQUIVOCAL:
            return "equivocal"
        if test_type == TestType.SPT:
            mm = r.mean_mm if r.mean_mm is not None else r.value
            # SPT 입력폼의 빈칸은 '반응 없음'이다(검사는 했다)
            if mm is None or mm < 2.0:
                return "negative"
            return "equivocal"
        # class 는 0 인데 수치는 0.35 이상 — OCR 이 한쪽을 잘못 읽은 경우가 많다. 음성으로 단정하지 않는다
        if r.value is not None and r.value >= 0.35:
            return "equivocal"
        if r.interpretation == InterpretationType.NEGATIVE:
            return "negative"
        vt = (r.value_text or "").strip()
        if vt.startswith("<"):
            return "negative"
        if r.class_value is not None:
            c = normalize_class_token(r.class_value)
            if c is not None:
                return "negative" if c < 1 else "positive"
        if r.value is not None:
            return "negative" if r.value < 0.35 else "positive"
        return "unknown"

    def _tested_negative(self, r: AllergenResult, test_type: TestType) -> Optional[TestedNegative]:
        name = (r.allergen_name or "").strip()
        if not name and not (r.korean_name or "").strip():
            return None
        if self._NON_ALLERGEN.search(f"{name} {r.korean_name or ''}"):
            return None
        try:
            kb = self.kb.get_backdata(name=r.allergen_name, category=str(r.category) if r.category else None,
                                      korean_name=r.korean_name) or {}
        except Exception:  # noqa: BLE001
            kb = {}
        return TestedNegative(
            allergen_name=r.allergen_name,
            korean_name=r.korean_name or kb.get("korean_name"),
            category=normalize_category(kb.get("category")) if kb.get("category") else None,
            status=self.result_status(r, test_type),
            test_value=r.value if r.value is not None else r.mean_mm,
            value_text=r.value_text,
            test_unit=r.unit or ("mm" if test_type == TestType.SPT else "kU/L"),
            class_value=r.class_value,
            size_text=r.size_text,
        )

    # ---------- 검사 대조 ----------
    _CONTROL_NAME_KO = {"positive": "양성 대조(히스타민)", "negative": "음성 대조(생리식염수)", "unspecified": "대조"}

    def _controls(self, ocr_result: OCRResult) -> List[TestControl]:
        """검사 대조 값 — 결과 행으로 온 것과, SPT 결과지에서 환자 정보 칸으로 읽어 온 것(OCR 관례)."""
        tt = ocr_result.test_type
        out: List[TestControl] = []
        for r in ocr_result.results:
            kind = control_kind(r.allergen_name, r.korean_name or "", r.category)
            if not kind:
                continue
            value = r.mean_mm if (tt == TestType.SPT and r.mean_mm is not None) else r.value
            has_value = value is not None or r.class_value is not None or bool(r.size_text or r.value_text)
            out.append(TestControl(
                kind=kind, name=r.allergen_name or r.korean_name or "", korean_name=r.korean_name,
                value=value, unit=r.unit or ("mm" if tt == TestType.SPT else None),
                class_value=r.class_value, size_text=r.size_text,
                # 값이 없는 MAST 대조 행은 '음성'이 아니라 '읽지 못함'이다
                status=self.result_status(r, tt) if (has_value or tt == TestType.SPT) else "unknown"))
        if tt == TestType.SPT:
            patient = ocr_result.patient
            for kind, mm in (("positive", patient.histamine_mean_mm), ("negative", patient.negative_control_mean_mm)):
                if mm is None or any(c.kind == kind for c in out):
                    continue
                row = AllergenResult(index=0, raw_text="", allergen_name=self._CONTROL_NAME_KO[kind], mean_mm=mm)
                out.append(TestControl(kind=kind, name="Histamine" if kind == "positive" else "Saline",
                                       korean_name=self._CONTROL_NAME_KO[kind], value=mm, unit="mm",
                                       status=self.result_status(row, tt)))
        return out

    @staticmethod
    def _control_value_text(c: TestControl) -> str:
        if c.value is not None:
            return f"{c.value:g}{c.unit or ''}"
        if c.class_value is not None:
            return f"class {c.class_value}"
        return c.size_text or ""

    @classmethod
    def control_check(cls, controls: List[TestControl], test_type: TestType) -> Optional[Dict[str, Any]]:
        """검사 대조로 본 검사 해석 가능 여부.

        SPT 는 양성 대조(히스타민)에 반응이 나오고 음성 대조(생리식염수)에 반응이 없어야 결과를 읽을 수 있다.
        기준은 항원 행과 같은 것만 쓴다(result_status: 3mm 이상 양성, 2mm 미만 음성) — 새 기준을 두지 않는다.
        MAST/UniCAP 대조 행은 판단하지 않고 값만 보여 준다.
        반환: {"status": "ok"|"caution"|"shown", "line_ko": 한 줄, "items": [{kind, label_ko, value_text, status}]}
        대조 값이 하나도 없는 MAST/UniCAP 은 None."""
        # 히스타민·생리식염수는 피부반응검사의 대조액이다. 혈액검사(MAST·UniCAP)의 대조는 그렇게 부르지 않는다.
        labels = cls._CONTROL_NAME_KO if test_type == TestType.SPT else {
            "positive": "양성 대조", "negative": "음성 대조", "unspecified": "대조"}
        items = [{"kind": c.kind, "label_ko": labels.get(c.kind, "대조"),
                  "name": c.name, "value_text": cls._control_value_text(c), "status": c.status} for c in controls]
        if test_type != TestType.SPT:
            if not items:
                return None
            shown = " · ".join(f"{i['label_ko']} {i['value_text']}".strip() for i in items)
            return {"status": "shown", "items": items,
                    "line_ko": f"검사 대조: {shown}. 검사가 제대로 되었는지 보는 항목이며 알레르기 결과가 아닙니다."}
        pos = [c for c in controls if c.kind == "positive"]
        neg = [c for c in controls if c.kind == "negative"]
        other = [c for c in controls if c.kind == "unspecified"]
        ok, caution = [], []
        if not pos:
            caution.append("양성 대조(히스타민) 값이 기록되지 않았습니다")
        elif all(c.status == "positive" for c in pos):
            ok.append(f"양성 대조 반응 확인({cls._control_value_text(pos[0])})")
        else:
            caution.append(f"양성 대조(히스타민) 반응이 약하거나 없습니다({cls._control_value_text(pos[0]) or '값 없음'}). "
                           "항히스타민제 복용 등으로 피부 반응이 줄었을 수 있어 음성 결과를 그대로 믿기 어렵습니다")
        if neg:
            if all(c.status == "negative" for c in neg):
                ok.append(f"음성 대조 음성({cls._control_value_text(neg[0]) or '반응 없음'})")
            else:
                caution.append(f"음성 대조에도 반응이 있습니다({cls._control_value_text(neg[0])}). 피부묘기증처럼 "
                               "긁히기만 해도 부푸는 피부에서는 양성 결과가 실제보다 크게 보일 수 있습니다")
        extra = [f"대조 {cls._control_value_text(c)}".strip() for c in other]
        if caution:
            line = "검사 대조: " + " / ".join(caution + ok + extra) + " — 검사 해석에 주의, 진료에서 확인하세요."
            return {"status": "caution", "items": items, "line_ko": line}
        return {"status": "ok", "items": items, "line_ko": "검사 대조: " + " / ".join(ok + extra) + "."}

    @staticmethod
    def _is_positive(r: AllergenResult, test_type: TestType) -> bool:
        # 명시적 양성/음성만 신뢰; UNKNOWN/EQUIVOCAL 은 아래 수치 기반으로 재판정
        if r.interpretation is not None:
            if isinstance(r.interpretation, InterpretationType):
                if r.interpretation == InterpretationType.POSITIVE:
                    return True
                if r.interpretation == InterpretationType.NEGATIVE:
                    return False
                # UNKNOWN/EQUIVOCAL -> 수치 기반 판정으로 폴백
            else:
                s = str(r.interpretation).lower()
                if s in ("positive", "p", "양성"):
                    return True
                if s in ("negative", "n", "음성"):
                    return False
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
        negatives: List[TestedNegative] = []
        worse_months = screening.worse_months if screening else []
        controls = self._controls(ocr_result)

        for r in ocr_result.results:
            # 이름이 비어 있는 행(빈 추가 행 등)은 감별 대상에서 제외
            if not (r.allergen_name or "").strip() and not (r.korean_name or "").strip():
                continue
            # 검사 대조(히스타민·생리식염수)는 알러젠이 아니다 — 양성으로도 음성 항원으로도 세지 않는다
            if control_kind(r.allergen_name, r.korean_name or "", r.category):
                continue
            if not self._is_positive(r, ocr_result.test_type):
                neg = self._tested_negative(r, ocr_result.test_type)
                if neg is not None:
                    negatives.append(neg)
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
                test_unit=r.unit or ("mm" if ocr_result.test_type == TestType.SPT else "kU/L"),
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

        # 같은 임상 그룹(집먼지진드기 두 종 등)에서 이번에 양성으로 나온 항원 수 — 한 종만 양성인데
        # '집먼지진드기(유럽·미국 두 종)'이라고 부르지 않기 위해서다(clinical_group_service.label_of).
        try:
            from services.clinical_group_service import get_clinical_group_service
            for grp in get_clinical_group_service().collapse(assessments):
                for _i, member in grp["members"]:
                    member.clinical_group_count = len(grp["members"])
        except Exception:  # noqa: BLE001
            pass

        return RelevanceAssessmentResult(
            patient_name=ocr_result.patient.name,
            test_date=ocr_result.patient.test_date,
            test_type=ocr_result.test_type,
            assessments=assessments,
            tested_negatives=negatives,
            controls=controls,
            control_check=self.control_check(controls, ocr_result.test_type),
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

        if cat == "drug":
            # 약물 항원은 이 앱이 판정하지 않는다(적응형 문진의 _classify_drug 와 같은 입장)
            assessment.relevance = ClinicalRelevance.CLINICIAN_REVIEW
            assessment.rationale_ko = (
                f"{name} 검사는 양성입니다. 검사 양성만으로 약물 알레르기라고 하지 않으며, 약을 쓴 뒤 반응이 "
                "있었더라도 그 약 때문인지는 진료에서 확인합니다. 이 약을 계속 피할지, 다시 써도 되는지는 "
                "진료에서 정합니다.")
            return assessment

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
        """판정별 이름과 개수.

        `counts` 와 이름 목록 세 가지는 API 의 `relevance` 와 같은 낱말(세 값)로 센다 — 기존 화면·저장된 세션이
        그대로 읽는다. 약물('진료 확인 필요')은 거기서는 indeterminate 에 들어간다.
        `verdict_counts`·`clinician_review` 는 판정 그대로다: 약물을 따로 세고, indeterminate 에서 뺀다.
        리포트·카드뉴스·문서 표지는 verdict_counts 를 쓴다."""
        names = lambda items: [a.korean_name or a.allergen_name for a in items]  # noqa: E731
        rel = result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        sens = result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        ind = result.by_relevance(ClinicalRelevance.INDETERMINATE)
        review = result.by_relevance(ClinicalRelevance.CLINICIAN_REVIEW)
        legacy_ind = [a for a in result.assessments if public_relevance(a.relevance) == "indeterminate"]
        return {
            "total_positive": len(result.assessments),
            "clinically_relevant": names(rel),
            "sensitized_only": names(sens),
            "indeterminate": names(legacy_ind),
            "clinician_review": names(review),
            "counts": {
                "clinically_relevant": len(rel),
                "sensitized_only": len(sens),
                "indeterminate": len(legacy_ind),
            },
            "verdict_counts": {
                "clinically_relevant": len(rel),
                "sensitized_only": len(sens),
                "indeterminate": len(ind),
                "clinician_review": len(review),
            },
        }


# 싱글톤
_relevance_service: Optional[RelevanceService] = None


def get_relevance_service() -> RelevanceService:
    global _relevance_service
    if _relevance_service is None:
        _relevance_service = RelevanceService()
    return _relevance_service
