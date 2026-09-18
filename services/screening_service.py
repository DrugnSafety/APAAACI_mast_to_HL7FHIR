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

# 화면 표시 언어별 라벨 — 고정된 임상 어휘라 기계 번역 대신 손으로 확정한다.
# (번역 캐시를 타면 API 키가 없을 때 한국어로 새어 나가고, 약제 분류명이 흔들린다)
DISEASE_LABELS_EN: Dict[str, str] = {
    "allergic_rhinitis": "Allergic rhinitis",
    "asthma": "Asthma",
    "atopic_dermatitis": "Atopic dermatitis",
    "allergic_conjunctivitis": "Allergic conjunctivitis",
    "chronic_urticaria": "Chronic urticaria",
    "food_allergy": "Food allergy",
    "anaphylaxis": "History of anaphylaxis",
    "drug_allergy": "Drug allergy",
    "sinusitis": "Sinusitis",
    "none": "None of these",
}

DISEASE_LABELS_ZH: Dict[str, str] = {
    "allergic_rhinitis": "过敏性鼻炎",
    "asthma": "哮喘",
    "atopic_dermatitis": "特应性皮炎",
    "allergic_conjunctivitis": "过敏性结膜炎",
    "chronic_urticaria": "慢性荨麻疹",
    "food_allergy": "食物过敏",
    "anaphylaxis": "过敏性休克病史",
    "drug_allergy": "药物过敏",
    "sinusitis": "鼻窦炎",
    "none": "以上均无",
}

MEDICATION_LABELS_EN: Dict[str, str] = {
    "antihistamine": "Antihistamine",
    "nasal_steroid": "Nasal steroid spray",
    "inhaled_steroid": "Inhaled steroid",
    "leukotriene": "Leukotriene modifier (e.g. montelukast)",
    "systemic_steroid": "Oral/injected steroid",
    "immunotherapy": "Immunotherapy (sublingual/subcutaneous)",
    "biologics": "Biologic agent (injection)",
    "decongestant": "Decongestant (nasal spray/oral)",
    "none": "Not taking any",
}

MEDICATION_LABELS_ZH: Dict[str, str] = {
    "antihistamine": "抗组胺药",
    "nasal_steroid": "鼻用糖皮质激素喷雾",
    "inhaled_steroid": "吸入型糖皮质激素",
    "leukotriene": "白三烯调节剂（如孟鲁司特）",
    "systemic_steroid": "口服/注射糖皮质激素",
    "immunotherapy": "免疫治疗（舌下/皮下）",
    "biologics": "生物制剂（注射）",
    "decongestant": "减充血剂（鼻喷/口服）",
    "none": "未服用",
}

ORGAN_SYSTEM_LABELS_EN: Dict[str, str] = {
    "nasal": "Nose (sneezing, runny or blocked nose)",
    "ocular": "Eyes (itching, redness, watering)",
    "lower_airway": "Lower airway (cough, wheeze, shortness of breath)",
    "skin": "Skin (hives, itching, eczema)",
    "gi": "Digestive (abdominal pain, diarrhoea, vomiting)",
    "systemic": "Whole body (dizziness, anaphylaxis)",
}

ORGAN_SYSTEM_LABELS_ZH: Dict[str, str] = {
    "nasal": "鼻部（打喷嚏·流涕·鼻塞）",
    "ocular": "眼部（发痒·充血·流泪）",
    "lower_airway": "下呼吸道（咳嗽·喘鸣·呼吸困难）",
    "skin": "皮肤（荨麻疹·瘙痒·湿疹）",
    "gi": "消化道（腹痛·腹泻·呕吐）",
    "systemic": "全身（头晕·过敏性休克）",
}

# code -> {lang: label}. ko 는 기존 사전을 그대로 쓴다.
_LABEL_SETS = {
    "diseases": {"ko": DISEASE_LABELS_KO, "en": DISEASE_LABELS_EN, "zh": DISEASE_LABELS_ZH},
    "medications": {"ko": MEDICATION_LABELS_KO, "en": MEDICATION_LABELS_EN, "zh": MEDICATION_LABELS_ZH},
    "organ_systems": {"ko": ORGAN_SYSTEM_LABELS_KO, "en": ORGAN_SYSTEM_LABELS_EN,
                      "zh": ORGAN_SYSTEM_LABELS_ZH},
}


def normalize_lang(lang: Optional[str]) -> str:
    """'en-US', 'zh-CN', None 등을 ko/en/zh 로 정규화한다."""
    code = (lang or "ko").lower().replace("_", "-").split("-")[0]
    return code if code in ("ko", "en", "zh") else "ko"


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

    @staticmethod
    def _options(kind: str, codes: List[str], lang: Optional[str]) -> List[Dict[str, str]]:
        """선택지를 화면 언어로 낸다. 해당 언어에 라벨이 없으면 한국어로 떨어진다(빈칸 금지)."""
        sets = _LABEL_SETS[kind]
        table = sets[normalize_lang(lang)]
        fallback = sets["ko"]
        return [{"code": c, "label": table.get(c) or fallback.get(c, c)} for c in codes]

    def disease_options(self, lang: Optional[str] = "ko") -> List[Dict[str, str]]:
        return self._options("diseases", ALLERGIC_DISEASE_OPTIONS, lang)

    def medication_options(self, lang: Optional[str] = "ko") -> List[Dict[str, str]]:
        return self._options("medications", MEDICATION_OPTIONS, lang)

    def organ_system_options(self, lang: Optional[str] = "ko") -> List[Dict[str, str]]:
        return self._options("organ_systems", ORGAN_SYSTEM_OPTIONS, lang)

    @staticmethod
    def _residence_label(profile: ScreeningProfile) -> str:
        """거주 지역을 사람이 읽는 문구로. 꽃가루 시기 안내의 근거가 된다."""
        country = (getattr(profile, "residence_country", None) or "").upper()
        region = (getattr(profile, "residence_region", None) or "").upper()
        if not country:
            return ""
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            r = get_pollen_forecast_service().resolve_region(country, region)
            if r:
                return f"{country} · {r.get('label_ko') or r.get('code')}"
        except Exception:  # noqa: BLE001
            pass
        return f"{country}{(' · ' + region) if region else ''}"

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
            "residence_ko": self._residence_label(profile),
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
