"""
Screening Service — 검사 전/기저 스크리닝 (알레르기 질환력, 약제, 증상 특성)

역할:
- 문진 항목의 한글 라벨 제공 (UI 렌더링용)
- 수집된 ScreeningProfile 의 요약/해석 (예: 항히스타민제 복용에 따른 SPT 위음성 주의)
- 증상 계절 패턴/악화 월/침범 장기 정보를 감별 엔진이 쓸 수 있게 정리
"""

import logging
from typing import Any, Dict, List, Optional

from models.schemas import (
    ScreeningProfile,
    SymptomSeasonPattern,
    ALLERGIC_DISEASE_OPTIONS,
    MEDICATION_OPTIONS,
    ORGAN_SYSTEM_OPTIONS,
)

logger = logging.getLogger(__name__)

DISEASE_LABELS_KO: Dict[str, str] = {
    "allergic_rhinitis": "알레르기 비염",
    "asthma": "천식",
    "atopic_dermatitis": "아토피 피부염",
    "allergic_conjunctivitis": "알레르기 결막염",
    "chronic_urticaria": "만성 두드러기",
    "food_allergy": "음식 알레르기",
    "anaphylaxis": "아나필락시스 병력",
    "drug_allergy": "약물 알레르기",
    "sinusitis": "부비동염(축농증)",
    "none": "해당 없음",
}

MEDICATION_LABELS_KO: Dict[str, str] = {
    "antihistamine": "항히스타민제",
    "nasal_steroid": "비강 스테로이드 스프레이",
    "inhaled_steroid": "흡입 스테로이드",
    "leukotriene": "류코트리엔 조절제(몬테루카스트 등)",
    "systemic_steroid": "먹는/주사 스테로이드",
    "immunotherapy": "면역치료(설하/피하)",
    "biologics": "생물학적 제제(주사)",
    "decongestant": "충혈완화제(코 스프레이/먹는약)",
    "none": "복용 안 함",
}

ORGAN_SYSTEM_LABELS_KO: Dict[str, str] = {
    "nasal": "코 (재채기·콧물·코막힘)",
    "ocular": "눈 (가려움·충혈·눈물)",
    "lower_airway": "하기도 (기침·천명·호흡곤란)",
    "skin": "피부 (두드러기·가려움·습진)",
    "gi": "소화기 (복통·설사·구토)",
    "systemic": "전신 (어지럼·아나필락시스)",
}

SEASON_PATTERN_LABELS_KO: Dict[str, str] = {
    "perennial": "연중 (사철 내내)",
    "seasonal": "계절성 (특정 시기에만)",
    "both": "연중 + 특정 계절 악화",
    "none": "뚜렷한 패턴 없음 / 증상 없음",
}

MONTH_LABELS_KO = ["1월", "2월", "3월", "4월", "5월", "6월",
                   "7월", "8월", "9월", "10월", "11월", "12월"]


class ScreeningService:
    """스크리닝 문진 도우미"""

    def disease_options(self) -> List[Dict[str, str]]:
        return [{"code": c, "label": DISEASE_LABELS_KO.get(c, c)} for c in ALLERGIC_DISEASE_OPTIONS]

    def medication_options(self) -> List[Dict[str, str]]:
        return [{"code": c, "label": MEDICATION_LABELS_KO.get(c, c)} for c in MEDICATION_OPTIONS]

    def organ_system_options(self) -> List[Dict[str, str]]:
        return [{"code": c, "label": ORGAN_SYSTEM_LABELS_KO.get(c, c)} for c in ORGAN_SYSTEM_OPTIONS]

    def summarize(self, profile: ScreeningProfile) -> Dict[str, Any]:
        """스크리닝 요약 + 임상적 주의사항(flags) 생성."""
        flags: List[str] = []

        # 항히스타민제 복용은 SPT 위음성을 유발할 수 있음
        if profile.antihistamine_recent or ("antihistamine" in profile.current_medications):
            flags.append(
                "최근 항히스타민제를 복용했다면 피부반응검사(SPT)에서 위음성(실제 양성인데 음성)이 "
                "나올 수 있습니다. 결과 해석 시 주의가 필요합니다."
            )
        if "systemic_steroid" in profile.current_medications:
            flags.append("전신 스테로이드 복용은 피부반응검사 결과에 영향을 줄 수 있습니다.")

        # 증상-질환 정합성
        diseases = [DISEASE_LABELS_KO.get(d, d) for d in profile.allergic_diseases if d != "none"]

        return {
            "diseases_ko": diseases,
            "medications_ko": [MEDICATION_LABELS_KO.get(m, m) for m in profile.current_medications if m != "none"],
            "organ_systems_ko": [ORGAN_SYSTEM_LABELS_KO.get(o, o) for o in profile.organ_systems],
            "season_pattern_ko": SEASON_PATTERN_LABELS_KO.get(profile.season_pattern.value, profile.season_pattern.value),
            "worse_months_ko": [MONTH_LABELS_KO[m - 1] for m in profile.worse_months if 1 <= m <= 12],
            "flags": flags,
            "has_symptoms": profile.symptom_present,
        }

    @staticmethod
    def infer_season_pattern(worse_months: List[int]) -> SymptomSeasonPattern:
        """악화 월 개수로 계절 패턴을 추정 (사용자 미입력 시 보조)."""
        n = len(set(worse_months))
        if n == 0:
            return SymptomSeasonPattern.NONE
        if n >= 9:
            return SymptomSeasonPattern.PERENNIAL
        return SymptomSeasonPattern.SEASONAL


_screening_service: Optional[ScreeningService] = None


def get_screening_service() -> ScreeningService:
    global _screening_service
    if _screening_service is None:
        _screening_service = ScreeningService()
    return _screening_service
