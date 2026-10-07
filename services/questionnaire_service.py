"""
Questionnaire Service — 적응형(그룹화) 문진 엔진

문제:
  기존 감별 문진은 양성 알러젠마다 동일한 3가지 질문(노출/증상/재현성)을 반복해서
  물어보아 지루하고 비직관적이었다.

개선:
  1) 먼저 큰 그림을 묻는다 — 증상이 통년성(연중)인지, 계절성인지, 둘 다인지.
  2) 계절성이면 악화 계절을 고른다.
  3) 음식 알레르기 / 구강알레르기증후군(OAS) 여부를 묻는다.
  4) 이후에는 알러젠을 카테고리로 묶어, 각 특성에 맞는 '감별 포인트'만 질문한다.
       - 계절성 꽃가루  : 꽃가루 시즌(봄/초여름/가을)별로 1문항씩 (알러젠 개수와 무관)
       - 실내 통년성    : 저녁·새벽·이른 아침 악화 여부(실내 알러젠의 전형적 시그니처),
                          집을 비우면 호전되는지, 먼지·이불 정리 시 악화(진드기),
                          습한 곳 악화(곰팡이), 오래된 건물·주방(바퀴)
       - 동물           : 그 동물과 얼마나 접촉하는지(함께 삶 / 자주 / 가끔 / 거의 없음),
                          접촉 증가 시 증상 악화 여부, 일하면서 동물을 다루는지(직업 노출)
       - 음식           : 그 음식 섭취 시 증상 재현 여부

  이렇게 모은 '그룹 답변'을 알러젠별 특성과 대조하여
  clinically_relevant / sensitized_only / indeterminate 로 판정한다.

산출물(build):  UI 렌더링용 dict (sections + allergen_index)
판정(classify): RelevanceAssessmentResult 의 각 assessment.relevance/rationale_ko 갱신
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from models.schemas import (
    RelevanceAssessmentResult,
    AllergenAssessment,
    ClinicalRelevance,
    ScreeningProfile,
    SymptomSeasonPattern,
)
from services.knowledge_service import normalize_category, get_knowledge_service
from utils.text_utils import josa

logger = logging.getLogger(__name__)

# 답변 값
YES, NO, UNSURE = "yes", "no", "unsure"
YNU = [
    {"value": YES, "label": "예"},
    {"value": NO, "label": "아니오"},
    {"value": UNSURE, "label": "잘 모르겠어요"},
]

# 전역 질문 id
Q_PATTERN = "symptom_pattern"           # perennial / seasonal / both / none
Q_SEASONS = "worse_seasons"             # multi: spring/summer/fall/winter
Q_OAS = "oral_allergy_syndrome"         # 구강알레르기증후군
Q_FOOD_SYSTEMIC = "food_systemic"       # 음식 전신 반응
QP_SHELLFISH = "shellfish_react__"      # + key : 갑각류 섭취 시 반응(양방향 감별)
QP_FOOD_SYMPTOMS = "food_symptoms__"    # + key : 일반 음식 섭취 시 증상 유형(다중)

# 음식/갑각류 섭취 반응 유형 (단일)
FOOD_REACT_OPTIONS = [
    {"value": "none", "label": "잘 먹어요 (증상 없음)"},
    {"value": "oral", "label": "입·입술·목만 가렵거나 부어요"},
    {"value": "systemic", "label": "두드러기·호흡곤란·복통 등 전신 증상"},
    {"value": "never", "label": "먹어본 적 없음 / 잘 모르겠어요"},
]
# 일반 음식 증상 유형 (다중)
FOOD_SYMPTOM_OPTIONS = [
    {"value": "oral", "label": "입·목 가려움/부종"},
    {"value": "skin", "label": "두드러기·피부 발진"},
    {"value": "gi", "label": "복통·구토·설사"},
    {"value": "breathing", "label": "호흡곤란·기침·쌕쌕"},
    {"value": "anaphylaxis", "label": "어지럼·아나필락시스"},
    {"value": "none", "label": "먹어도 증상 없음", "exclusive": True},
]
# 라텍스·약물은 '기타 항원' 문항(QP_OTHER_SYMPTOM)을 쓰되 문구와 선택지를 노출 경로에 맞춘다.
# 선택지 값(oral·skin·breathing·anaphylaxis·none)은 판정(_classify_other)이 읽는 값이라 그대로 둔다.
LATEX_SYMPTOM_OPTIONS = [
    {"value": "skin", "label": "닿은 자리 두드러기·가려움·발진"},
    {"value": "oral", "label": "입술·얼굴·입안 부기(풍선 불기·치과 진료 뒤 등)"},
    {"value": "breathing", "label": "재채기·콧물·기침·쌕쌕거림·숨참"},
    {"value": "anaphylaxis", "label": "어지럼·아나필락시스"},
    {"value": "none", "label": "닿아도 증상 없음", "exclusive": True},
    {"value": "never", "label": "닿은 적이 없거나 잘 모르겠어요", "exclusive": True},
]
DRUG_SYMPTOM_OPTIONS = [
    {"value": "skin", "label": "두드러기·발진·가려움"},
    {"value": "oral", "label": "입술·얼굴·목 부기"},
    {"value": "breathing", "label": "숨참·쌕쌕거림"},
    {"value": "anaphylaxis", "label": "어지럼·실신·아나필락시스"},
    {"value": "none", "label": "써도 문제 없었어요", "exclusive": True},
    {"value": "never", "label": "써 본 적이 없거나 잘 모르겠어요", "exclusive": True},
]
_CONTACT_QUESTION = {
    "latex": {
        "title": "「{nm}」 제품(고무장갑·풍선·콘돔·의료용 장갑 등)에 닿았을 때 어떤 증상이 있었나요?",
        "help": "여러 개 선택 가능. 닿아도 괜찮았다면 ‘닿아도 증상 없음’.",
        "options": LATEX_SYMPTOM_OPTIONS,
        "severity_title": "{nm} 제품에 닿아 증상이 있었을 때, 가장 심했던 정도는?",
    },
    "drug": {
        "title": "「{nm}」(같은 계열 약 포함)을 먹거나 주사로 맞은 뒤 어떤 반응이 있었나요?",
        "help": "여러 개 선택 가능. 검사 양성만으로 약물 알레르기라고 하지 않아요 — 실제로 반응이 있었는지를 여쭙니다.",
        "options": DRUG_SYMPTOM_OPTIONS,
        "severity_title": "{nm} 사용 뒤 반응이 있었을 때, 가장 심했던 정도는?",
    },
}
# 판정 뒤에도 남겨 두는 약물 문진 답(assessment.answers 의 키)
A_DRUG_HISTORY = "drug_history"          # reaction(쓴 뒤 반응) / tolerated(써도 문제 없음) / never(써 본 적 없음·모름) / unknown(답 없음)
A_DRUG_CLASS_ALERT = "drug_class_alert"  # 같은 계열의 다른 약에 반응이 있었을 때 — 그 약 이름(쉼표로 이음)
A_DRUG_CLASS = "drug_class"              # 그 계열의 이름
# 같은 계열로 묶어 보는 약물. 한 약에 반응이 있었다고 답했으면, 같은 계열의 다른 약에는 '스스로 끊거나 피하지
# 마세요'라고 하지 않고 '쓰기 전에 진료에서 상의'로 안내한다(계열 안의 약이 모두 함께 반응하는 것은 아니다).
DRUG_CLASSES = [
    ("beta_lactam", "베타락탐계 항생제(페니실린·세팔로스포린 계열)",
     re.compile(r"penicill|amoxicill|ampicill|cef[a-z]|ceph|carbapenem|[a-z]penem|"
                r"페니실린|아목시실린|암피실린|세파|세프|세팔로|青霉素|阿莫西林|氨苄西林|头孢", re.I)),
]


def drug_class(a) -> Optional[Tuple[str, str]]:
    """약물 항원의 계열 (키, 이름). 표에 없으면 None."""
    text = f"{getattr(a, 'allergen_name', '') or ''} {getattr(a, 'korean_name', '') or ''}"
    for key, label, rx in DRUG_CLASSES:
        if rx.search(text):
            return key, label
    return None


# 문진의 증상 선택지 → 주증상 부위(ORGAN_SYSTEM_OPTIONS). '입·목'(oral)은 주증상 부위 선택지에 없다.
_SYMPTOM_SITES = {"skin": ["skin"], "gi": ["gi"], "breathing": ["lower_airway"], "anaphylaxis": ["systemic"]}
# 라텍스의 호흡기 선택지는 '재채기·콧물·기침·쌕쌕거림·숨참' — 코와 기관지를 함께 묻는다
_LATEX_SYMPTOM_SITES = dict(_SYMPTOM_SITES, breathing=["nasal", "lower_airway"])


def _sites_of(symptoms, table=_SYMPTOM_SITES) -> List[str]:
    out: List[str] = []
    for s in ("skin", "gi", "breathing", "anaphylaxis"):
        if s in symptoms:
            out += [x for x in table.get(s, []) if x not in out]
    return out


Q_INDOOR_TIMING = "indoor_timing"       # 저녁/새벽/이른아침 악화
Q_INDOOR_AWAY = "indoor_away"           # 집 비우면 호전
Q_MITE_DUST = "mite_dust"               # 먼지·이불 정리 시 악화
Q_MOLD_DAMP = "mold_damp"               # 습한 곳 악화
Q_ROACH_ENV = "roach_env"               # 오래된 건물·주방
# --- 곰팡이 ↔ 집먼지진드기 감별(둘 다 통년성 실내 항원이라 일반 질문으로는 구분되지 않는다) ---
Q_MOLD_SPACE = "mold_space"             # 어떤 공간에서 악화되나(욕실·지하실·누수·에어컨 vs 침실)
Q_MOLD_OUTDOOR = "mold_outdoor"         # 실외 곰팡이 단서(낙엽·퇴비·비 온 뒤·건초)
Q_MITE_BEDDING_TRIAL = "mite_bedding_trial"   # 침구 관리 후 호전 여부(진드기 특이)
Q_MOLD_DEHUM_TRIAL = "mold_dehum_trial"       # 제습·곰팡이 제거 후 호전 여부(곰팡이 특이)

# 공간 단서 — 앞의 4개는 곰팡이 특이, bedroom 은 진드기 특이
MOLD_SPACE_OPTIONS = [
    {"value": "bathroom", "label": "욕실·샤워실 등 늘 젖어 있는 곳"},
    {"value": "basement", "label": "지하실·창고·오래된 건물"},
    {"value": "water_damage", "label": "누수·결로·물에 젖었던 벽지나 가구 근처"},
    {"value": "aircon", "label": "에어컨·가습기·환기구를 켤 때"},
    {"value": "bedroom", "label": "침실 잠자리(이불·매트리스) 주변"},
    {"value": "none", "label": "특별히 그런 공간은 없어요", "exclusive": True},
]
MOLD_SPACE_SPECIFIC = {"bathroom", "basement", "water_damage", "aircon"}

# 실외 곰팡이(얼터나리아·클라도스포리움) 단서
MOLD_OUTDOOR_OPTIONS = [
    {"value": "leaves", "label": "낙엽 더미·풀 깎기·잔디밭"},
    {"value": "soil", "label": "퇴비·흙·화분을 다룰 때"},
    {"value": "rain", "label": "비 온 직후·천둥번개가 친 뒤"},
    {"value": "farm", "label": "농장·창고·건초·곡물 주변"},
    {"value": "none", "label": "해당 없음", "exclusive": True},
]

# 환경 조치 후 반응 — 무엇을 바꿨을 때 좋아졌는지가 가장 확실한 감별 근거다
TRIAL_OPTIONS = [
    {"value": "better", "label": "해봤고, 증상이 좋아졌어요"},
    {"value": "same", "label": "해봤지만 변화가 없었어요"},
    {"value": "never", "label": "해본 적 없어요"},
]

# 곰팡이 속(genus)별 실내/실외 구분 — 회피 조언이 완전히 다르다
MOLD_OUTDOOR_GENERA = ("alternaria", "cladosporium", "helminthosporium", "fusarium",
                       "epicoccum", "curvularia", "botrytis", "outdoor")
MOLD_INDOOR_GENERA = ("aspergillus", "penicillium", "candida", "mucor", "rhizopus",
                      "neurospora", "indoor")


def mold_habitat(a) -> str:
    """곰팡이 항원이 주로 실외성인지 실내성인지 — 문진 해석과 회피 조언을 가른다."""
    txt = f"{getattr(a, 'allergen_name', '')} {getattr(a, 'korean_name', '')}".lower()
    if any(g in txt for g in MOLD_OUTDOOR_GENERA):
        return "outdoor"
    if any(g in txt for g in MOLD_INDOOR_GENERA):
        return "indoor"
    return "both"
Q_FOOD_SYSTEMIC_FOODS = "food_systemic_foods"  # 전신반응 유발 음식(다중)
# 벌독(곤충 독) — 숨으로 들이마시는 항원이 아니라 '쏘여서' 들어온다. 꽃가루·실내 곤충 문항으로는
# 물을 수 없고, 벌독 면역치료 대상 여부도 쏘인 뒤 전신 반응이 있었는지로 갈린다.
Q_STING = "sting_reaction"
STING_OPTIONS = [
    {"value": "systemic", "label": "쏘인 자리 말고 온몸에 반응이 있었어요",
     "hint": "온몸 두드러기, 숨참·쌕쌕거림, 어지럼·실신, 목이 붓는 느낌"},
    {"value": "large_local", "label": "쏘인 자리 주변이 넓게(손바닥보다 크게) 부었어요"},
    {"value": "local", "label": "쏘인 자리만 붓고 아팠어요"},
    {"value": "never", "label": "쏘인 적이 없어요"},
    {"value": UNSURE, "label": "잘 모르겠어요"},
]
# 동적 id 접두사
QP_POLLEN = "pollen_season__"           # + group(spring/summer_grass/fall)
QP_ANIMAL_CONTACT = "animal_contact__"  # + key
QP_ANIMAL_WORSE = "animal_worse__"      # + key
# 판정 뒤에도 남겨 두는 동물 문진 답(assessment.answers 의 키). 함께 살지는 않지만 자주 접촉하는 사람과
# 거의 접촉이 없는 사람은 안내가 달라야 해서, '감작만'·'관찰 필요' 안내가 이 값을 본다.
A_ANIMAL_CONTACT = "animal_contact"
A_ANIMAL_WORSE = "animal_worse"
A_ANIMAL_FREQ = "animal_contact_freq"
A_ANIMAL_OCCUPATIONAL = "animal_occupational"      # yes / no — 이 동물을 일하면서 다루는가
A_ANIMAL_WORK_TYPES = "animal_work_types"          # 쉼표로 이은 일의 종류(vet,lab…)
A_ANIMAL_WORK_SYMPTOMS = "animal_work_symptoms"    # 일하는 날 심해지고 쉬는 날 좋아지는가(yes/no/unsure)
# 동물과의 접촉 정도. 예전에는 '키우거나 자주 접촉' 한 가지로 물어, 함께 사는 사람과 가끔 만나는 사람,
# 일하면서 다루는 사람을 가르지 못했다. 저장된 세션의 예전 답(yes/no)은 계속 읽는다(LEGACY_CONTACT).
ANIMAL_CONTACT_OPTIONS = [
    {"value": "live", "label": "함께 살아요", "hint": "집에서 키우거나 같은 집에 있어요"},
    {"value": "frequent", "label": "함께 살지는 않지만 자주 만나요", "hint": "주 1회 이상"},
    {"value": "occasional", "label": "가끔 만나요", "hint": "한 달에 1~3번"},
    {"value": "rare", "label": "거의 만나지 않아요", "hint": "1년에 몇 번 이하"},
]
LEGACY_CONTACT = {YES: "legacy_yes", NO: "rare"}
QP_ANIMAL_FREQ = "animal_contact_freq__"  # + key : 자주 만나는 경우 얼마나 자주
ANIMAL_FREQ_OPTIONS = [
    {"value": "daily", "label": "거의 매일"},
    {"value": "several", "label": "주 2~6회"},
    {"value": "weekly", "label": "주 1회쯤"},
]
Q_ANIMAL_WORK = "animal_work"                     # 일하면서 동물을 다루는가(다중)
Q_ANIMAL_WORK_ANIMALS = "animal_work_animals"     # 그 일에서 다루는 동물(양성 동물 항원 가운데, 다중)
Q_ANIMAL_WORK_SYMPTOMS = "animal_work_symptoms"   # 일하는 날 심해지고 쉬는 날 좋아지는가
ANIMAL_WORK_OPTIONS = [
    {"value": "vet", "label": "동물병원·수의 진료"},
    {"value": "lab", "label": "실험동물을 다루는 연구실·동물실"},
    {"value": "farm", "label": "축산·농장·승마장"},
    {"value": "pet_shop", "label": "반려동물 판매·분양"},
    {"value": "grooming", "label": "미용·호텔·훈련"},
    {"value": "care", "label": "보호소·동물원·돌봄"},
    {"value": "other", "label": "그 밖에 정기적으로 동물을 다루는 일"},
    {"value": "none", "label": "해당 없음", "exclusive": True},
]
ANIMAL_WORK_TYPES = [o["value"] for o in ANIMAL_WORK_OPTIONS if o["value"] != "none"]
QP_FOOD_REACT = "food_react__"          # + key
QP_SEVERITY = "severity__"              # + key/scope : 증상 중증도(경증/중등증/중증/아나필락시스)
QP_FOOD_SYSTEMIC_SEV = "food_systemic_sev__"   # + key : 전신반응 음식별 중증도
QP_CROSSREACT = "crossreact__"          # + key : 성분기반 교차반응 음식(다중) — 데이터 파생
QP_OTHER_SYMPTOM = "other_symptom__"    # + key : 미분류(other) 양성 항원 노출·섭취 시 증상(다중)
Q_FOOD_GENERAL = "food_general_react"   # 종합 음식반응 catch-all(다중) — 교차반응 재활성 트리거
QP_CROSSREACT_SEV = "crossreact_sev__"  # + key : 교차반응 증상 범위(구강만/전신/아나필락시스)

# 교차반응(OAS 포함) 증상 범위 — 같은 교차반응도 목 가려움만 vs 전신으로 갈린다
CROSSREACT_SEVERITY_OPTIONS = [
    {"value": "oral", "label": "입·입술·목만 가렵거나 부었어요 (국소)"},
    {"value": "systemic", "label": "전신 두드러기·복통 등 전신 증상이 있었어요"},
    {"value": "anaphylaxis", "label": "호흡곤란·어지럼 등 아나필락시스가 있었어요"},
]

# 증상 중증도 (단일)
SEVERITY_OPTIONS = [
    {"value": "mild", "label": "경증 — 가벼운 국소 증상, 일상에 큰 지장 없음"},
    {"value": "moderate", "label": "중등증 — 증상이 뚜렷하고 일상·수면에 지장"},
    {"value": "severe", "label": "중증 — 증상이 심해 약 없이는 조절이 어려움"},
    {"value": "anaphylaxis", "label": "아나필락시스 — 호흡곤란·전신 두드러기·어지럼(응급 병력)"},
]


def _severity_question(qid, applies, reveal, title="증상이 있을 때, 가장 심했던 정도는 어느 쪽에 가깝나요?"):
    """알러젠별 증상 중증도 추가 질의. reveal 조건이 충족될 때만 노출."""
    q = {
        "id": qid, "type": "single", "title": title,
        "help": "가장 심했던 에피소드 기준으로 골라주세요. 중증·아나필락시스는 전문의 평가가 필요합니다.",
        "options": SEVERITY_OPTIONS, "applies_to": applies,
    }
    q.update(reveal)
    return q

# 꽃가루 시즌 그룹 정의
POLLEN_GROUPS = {
    "pollen_tree": {
        "group": "spring_tree",
        "label": "봄철 나무 꽃가루",
        "months": [3, 4, 5],
        "season_ko": "봄 (3~5월)",
        "question": "봄철(3~5월)에 코·눈 증상(재채기·콧물·코막힘·눈 가려움)이 뚜렷하게 심해지나요?",
    },
    "pollen_grass": {
        "group": "summer_grass",
        "label": "늦봄~초여름 잔디(화본과) 꽃가루",
        "months": [5, 6],
        "season_ko": "늦봄~초여름 (5~6월)",
        "question": "늦봄~초여름(5~6월), 특히 잔디밭·풀밭 근처에서 코·눈 증상이 심해지나요?",
    },
    "pollen_weed": {
        "group": "fall_weed",
        "label": "가을철 잡초 꽃가루",
        "months": [8, 9, 10],
        "season_ko": "늦여름~가을 (8~10월)",
        "question": "늦여름~가을(8~10월)에 코·눈 증상이 뚜렷하게 심해지나요?",
    },
}

SEASON_MONTHS = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "fall": [9, 10, 11],
    "winter": [12, 1, 2],
}


# 문진 문항은 여러 부위를 한 문장으로 묻는다("코·눈 증상이 심해지나요?"). '예'는 그중 어느 부위인지
# 말해 주지 않으므로, 확인된 증상 문장에는 환자가 처음에 고른 주증상 부위만 적는다.
_SITE_KO = {"nasal": "코", "ocular": "눈", "skin": "피부", "lower_airway": "호흡기"}


def _site_phrase(organs, asked) -> str:
    """asked(그 문항이 물은 부위) 가운데 환자가 실제로 고른 부위만. 없으면 부위를 적지 않는다."""
    sites = [_SITE_KO[o] for o in asked if o in (organs or [])]
    return ("·".join(sites) + " 증상") if sites else "증상"


def apply_answers_to_screening(screening: Optional[ScreeningProfile], answers: Dict[str, Any]) -> None:
    """문진의 '증상 패턴'·'악화 계절' 답을 스크리닝 프로필의 계절성 입력으로 옮긴다.

    화면은 이 두 문항을 문진(answers)으로만 받고 ScreeningProfile.season_pattern·worse_months 는
    비워 둔다. 그래서 리포트 머리말이 증상이 확인된 환자에게 '뚜렷한 패턴 없음 / 증상 없음'을 찍었고,
    '답해주신 악화 시기가 시즌과 겹칩니다' 로직에는 값이 닿지 않았다. 답이 있을 때만 덮어쓴다.
    """
    if screening is None or not answers:
        return
    pattern = answers.get(Q_PATTERN)
    if isinstance(pattern, str) and pattern in {p.value for p in SymptomSeasonPattern}:
        screening.season_pattern = SymptomSeasonPattern(pattern)
    seasons = [x for x in _multi(answers.get(Q_SEASONS)) if isinstance(x, str) and x]
    if seasons:
        months = set()
        for season in seasons:
            months |= set(SEASON_MONTHS.get(season, []))
        screening.worse_months = sorted(months)   # '계절 차이 없음'만 고르면 빈 목록


def _multi(value) -> list:
    """다중 선택 답변을 항상 리스트로 읽는다.

    브라우저는 배열을 보내지만 `/api` 는 `answers: Dict[str, Any]` 라 스칼라도 들어올 수 있다.
    문자열을 그대로 순회하면 "apple" 이 ["a","p","p","l","e"] 가 되어, 리포트에
    존재하지 않는 교차반응 음식이 글자 단위로 찍힌다(실제로 발견된 버그).
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value]
    return [value]


def _key(i: int) -> str:
    return f"agn{i}"


def _norm_key(s) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def _cat(a: AllergenAssessment) -> str:
    return normalize_category(a.category or (a.kb or {}).get("category"))


def _name(a: AllergenAssessment) -> str:
    return a.korean_name or a.allergen_name


class QuestionnaireEngine:
    """양성 알러젠 집합에 대해 적응형 문진을 생성하고, 답변으로 임상적 의미를 판정한다."""

    # ============================================================
    # 1) 문진 생성
    # ============================================================
    def build(
        self,
        result: RelevanceAssessmentResult,
        screening: Optional[ScreeningProfile] = None,
    ) -> Dict[str, Any]:
        assessments = result.assessments
        cats = [_cat(a) for a in assessments]

        has_pollen = any(c in POLLEN_GROUPS for c in cats)
        has_mite = "mite" in cats
        has_mold = "mold" in cats
        has_insect = "insect" in cats
        has_indoor_perennial = has_mite or has_insect
        animals = [(i, a) for i, a in enumerate(assessments) if _cat(a) == "animal"]
        foods = [(i, a) for i, a in enumerate(assessments) if _cat(a) == "food"]
        # OAS 관련 꽃가루(자작·오리·쑥·돼지풀·티모시 등)가 있으면 음식 섹션을 유도
        pollen_oas = any((a.kb or {}).get("oral_allergy_syndrome_ko") for a in assessments)

        sections: List[Dict[str, Any]] = []

        # ---- 섹션 1: 증상 패턴 (항상) ----
        sections.append({
            "id": "pattern",
            "title": "증상은 언제 나타나나요?",
            "subtitle": "가장 큰 그림부터 알려주세요. 이후 질문이 여기에 맞춰 달라집니다.",
            "questions": [{
                "id": Q_PATTERN,
                "type": "single",
                "title": "알레르기 증상의 시기 패턴을 골라주세요.",
                "options": [
                    {"value": "perennial", "label": "연중 계속돼요", "hint": "특정 계절과 무관하게 1년 내내"},
                    {"value": "seasonal", "label": "특정 계절에만 심해요", "hint": "예: 봄·가을에만"},
                    {"value": "both", "label": "연중 있는데 특정 계절에 더 심해요"},
                    {"value": "none", "label": "뚜렷한 증상이 없어요"},
                ],
                "applies_to": [],
            }],
        })

        # ---- 섹션 2: 악화 계절 (계절성/both 이거나 꽃가루 있을 때) ----
        season_qs = [{
            "id": Q_SEASONS,
            "type": "multi",
            "title": "증상이 더 심해지는 계절을 모두 선택하세요.",
            "help": "‘연중 계속’을 고르셨어도, 특히 심한 계절이 있으면 선택해 주세요.",
            "options": [
                {"value": "spring", "label": "봄 (3~5월)"},
                {"value": "summer", "label": "여름 (6~8월)"},
                {"value": "fall", "label": "가을 (9~11월)"},
                {"value": "winter", "label": "겨울 (12~2월)"},
                {"value": "none", "label": "계절 차이 없음", "exclusive": True},
            ],
            "applies_to": [],
        }]
        sections.append({
            "id": "season",
            "title": "악화되는 계절",
            "subtitle": "계절성 알러젠(꽃가루 등)의 실제 관련성을 가리는 핵심 단서입니다.",
            "questions": season_qs,
            "show_if": {"any_of_pattern": ["seasonal", "both"], "or_has_pollen": has_pollen},
        })

        # ---- 섹션 3: 꽃가루 시즌 확인 (그룹당 1문항) ----
        if has_pollen:
            seen_groups = set()
            pollen_qs = []
            for c in ("pollen_tree", "pollen_grass", "pollen_weed"):
                if c in cats and c not in seen_groups:
                    seen_groups.add(c)
                    g = POLLEN_GROUPS[c]
                    applies = [_key(i) for i, a in enumerate(assessments) if _cat(a) == c]
                    pollen_qs.append({
                        "id": QP_POLLEN + g["group"],
                        "type": "single",
                        "title": g["question"],
                        "help": f"{g['label']} · {g['season_ko']}",
                        "options": YNU,
                        "applies_to": applies,
                    })
                    # 증상 있음(예) → 해당 시즌 증상 중증도 추가 질의
                    pollen_qs.append(_severity_question(
                        QP_SEVERITY + "pollen_" + g["group"], applies,
                        {"reveal_if": {"question": QP_POLLEN + g["group"], "any": [YES]}},
                        title=f"{g['season_ko']} 증상이 있을 때, 가장 심했던 정도는?"))
            sections.append({
                "id": "pollen",
                "title": "꽃가루 시즌 증상",
                "subtitle": "같은 시즌의 꽃가루는 한 번에 묶어 여쭤봅니다.",
                "questions": pollen_qs,
            })

        # ---- 섹션 4: 실내(통년성) 알러젠 ----
        if has_indoor_perennial or has_mold:
            indoor_qs = []
            if has_indoor_perennial:
                applies_indoor = [_key(i) for i, a in enumerate(assessments)
                                  if _cat(a) in ("mite", "insect")]
                indoor_qs.append({
                    "id": Q_INDOOR_TIMING,
                    "type": "single",
                    "title": "증상이 주로 저녁·자는 동안·이른 아침에 더 심한가요?",
                    "help": "집먼지진드기·바퀴 등 실내 알러젠은 보통 실내에 머무는 저녁·새벽·아침에 악화됩니다.",
                    "options": YNU,
                    "applies_to": applies_indoor,
                })
                indoor_qs.append({
                    "id": Q_INDOOR_AWAY,
                    "type": "single",
                    "title": "여행 등으로 집을 며칠 비우면 증상이 좋아지나요?",
                    "help": "집을 떠나면 호전된다면 실내 알러젠이 원인일 가능성이 높습니다.",
                    "options": YNU,
                    "applies_to": applies_indoor,
                })
            if has_mite:
                indoor_qs.append({
                    "id": Q_MITE_DUST,
                    "type": "single",
                    "title": "이불·카펫을 털거나 먼지 청소를 할 때 바로 증상이 심해지나요?",
                    "help": "집먼지진드기 알레르기의 특징적 신호입니다.",
                    "options": YNU,
                    "applies_to": [_key(i) for i, a in enumerate(assessments) if _cat(a) == "mite"],
                })
            if has_insect:
                indoor_qs.append({
                    "id": Q_ROACH_ENV,
                    "type": "single",
                    "title": "오래된 건물·주방 등 바퀴가 있을 만한 곳에서 코·호흡기 증상이 심해지나요?",
                    "options": YNU,
                    "applies_to": [_key(i) for i, a in enumerate(assessments) if _cat(a) == "insect"],
                })
            if has_mold:
                mold_keys = [_key(i) for i, a in enumerate(assessments) if _cat(a) == "mold"]
                molds = [a for a in assessments if _cat(a) == "mold"]
                habitats = {mold_habitat(a) for a in molds}
                indoor_qs.append({
                    "id": Q_MOLD_DAMP,
                    "type": "single",
                    "title": "장마철·습한 곳·곰팡이가 보이는 공간에서 증상이 심해지나요?",
                    "help": ("집먼지진드기도 습할 때 늘어나므로, 이 질문만으로는 둘을 가르지 못합니다. "
                             "아래에서 어떤 공간·상황인지 구체적으로 여쭤봅니다."
                             if has_mite else None),
                    "options": YNU,
                    "applies_to": mold_keys,
                })
                # 공간 단서 — 욕실·지하실·누수·에어컨(곰팡이) vs 침실 잠자리(진드기)
                space_opts = [o for o in MOLD_SPACE_OPTIONS
                              if o["value"] != "bedroom" or has_mite]
                indoor_qs.append({
                    "id": Q_MOLD_SPACE,
                    "type": "multi",
                    "title": "증상이 특히 심해지는 공간을 모두 골라주세요.",
                    "help": ("욕실·지하실·누수 자국·에어컨은 곰팡이 쪽 단서이고, 침실 잠자리는 "
                             "집먼지진드기 쪽 단서입니다. 어느 쪽인지 갈라내기 위한 질문입니다."
                             if has_mite else
                             "곰팡이는 늘 젖어 있는 공간에서 자랍니다."),
                    "options": space_opts,
                    "applies_to": mold_keys,
                })
                # 실외 곰팡이(얼터나리아·클라도스포리움) 단서 — 실내 진드기와 확실히 갈린다
                if habitats & {"outdoor", "both"}:
                    indoor_qs.append({
                        "id": Q_MOLD_OUTDOOR,
                        "type": "multi",
                        "title": "야외에서 다음 상황에 증상이 심해진 적이 있나요?",
                        "help": ("얼터나리아·클라도스포리움은 실외 곰팡이입니다. 낙엽·퇴비·비 온 뒤에 "
                                 "포자가 크게 늘어납니다. 집 안에서만 사는 집먼지진드기와는 확실히 구분됩니다."),
                        "options": MOLD_OUTDOOR_OPTIONS,
                        "applies_to": mold_keys,
                    })
                # 환경 조치 반응 — 둘 다 양성일 때 가장 확실한 감별 근거
                if has_mite:
                    indoor_qs.append({
                        "id": Q_MITE_BEDDING_TRIAL,
                        "type": "single",
                        "title": "침구를 뜨거운 물로 세탁하거나 진드기 차단 커버를 써본 뒤 증상이 좋아졌나요?",
                        "help": "좋아졌다면 집먼지진드기 쪽 근거입니다.",
                        "options": TRIAL_OPTIONS,
                        "applies_to": [_key(i) for i, a in enumerate(assessments) if _cat(a) == "mite"],
                    })
                    indoor_qs.append({
                        "id": Q_MOLD_DEHUM_TRIAL,
                        "type": "single",
                        "title": "제습기를 쓰거나 곰팡이를 제거한 뒤 증상이 좋아졌나요?",
                        "help": "좋아졌다면 곰팡이 쪽 근거입니다.",
                        "options": TRIAL_OPTIONS,
                        "applies_to": mold_keys,
                    })
            # 실내 알러젠 증상 있음(어느 항목이든 예) → 증상 중증도 추가 질의
            indoor_reveal = []
            if has_indoor_perennial:
                indoor_reveal += [{"question": Q_INDOOR_TIMING, "any": [YES]},
                                  {"question": Q_INDOOR_AWAY, "any": [YES]}]
            if has_mite:
                indoor_reveal.append({"question": Q_MITE_DUST, "any": [YES]})
            if has_insect:
                indoor_reveal.append({"question": Q_ROACH_ENV, "any": [YES]})
            if has_mold:
                indoor_reveal.append({"question": Q_MOLD_DAMP, "any": [YES]})
                indoor_reveal.append({"question": Q_MOLD_SPACE,
                                      "includes_any": sorted(MOLD_SPACE_SPECIFIC)})
                indoor_reveal.append({"question": Q_MOLD_OUTDOOR,
                                      "includes_any": ["leaves", "soil", "rain", "farm"]})
            if indoor_reveal:
                applies_all = [_key(i) for i, a in enumerate(assessments)
                               if _cat(a) in ("mite", "insect", "mold")]
                indoor_qs.append(_severity_question(
                    QP_SEVERITY + "indoor", applies_all,
                    {"reveal_if_any": indoor_reveal},
                    title="실내 환경 노출로 증상이 있을 때, 가장 심했던 정도는?"))
            sections.append({
                "id": "indoor",
                "title": "실내 환경 알러젠",
                "subtitle": "집먼지진드기·곰팡이·바퀴 등은 노출 상황과 시간대가 감별의 열쇠입니다.",
                "questions": indoor_qs,
            })

        # ---- 섹션 5: 동물 알러젠 (동물별) ----
        if animals:
            animal_qs = []
            for i, a in animals:
                nm = _name(a)
                animal_qs.append({
                    "id": QP_ANIMAL_CONTACT + _key(i),
                    "type": "single",
                    "title": f"{nm}{josa(nm, '과와')} 얼마나 접촉하나요?",
                    "help": "함께 사는지, 따로 살지만 얼마나 자주 만나는지 골라주세요. 일하면서 다루는 경우는 아래에서 따로 여쭤봅니다.",
                    "options": ANIMAL_CONTACT_OPTIONS,
                    "applies_to": [_key(i)],
                })
                animal_qs.append({
                    "id": QP_ANIMAL_FREQ + _key(i),
                    "type": "single",
                    "title": f"{nm}{josa(nm, '을를')} 얼마나 자주 만나나요?",
                    "options": ANIMAL_FREQ_OPTIONS,
                    "applies_to": [_key(i)],
                    "reveal_if": {"question": QP_ANIMAL_CONTACT + _key(i), "any": ["frequent"]},
                })
                animal_qs.append({
                    "id": QP_ANIMAL_WORSE + _key(i),
                    "type": "single",
                    "title": f"{nm}{josa(nm, '과와')} 접촉이 늘면 코·눈·피부·호흡기 증상이 심해지나요?",
                    "help": "접촉 직후~수십 분 내 증상이 생기는지 떠올려 보세요.",
                    "options": YNU,
                    "applies_to": [_key(i)],
                })
                animal_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_ANIMAL_WORSE + _key(i), "any": [YES]}},
                    title=f"{nm} 접촉 시 증상이 있을 때, 가장 심했던 정도는?"))
            # 직업 노출 — 동물 항원이 양성일 때만, 동물별 질문 뒤에 한 번 묻는다
            animal_keys = [_key(i) for i, _a in animals]
            animal_qs.append({
                "id": Q_ANIMAL_WORK,
                "type": "multi",
                "title": "일이나 실습으로 동물을 정기적으로 다루나요? 해당하는 것을 모두 골라주세요.",
                "help": "직업으로 동물을 다루면 집에서보다 노출이 훨씬 큽니다. 해당하지 않으면 ‘해당 없음’을 고르세요.",
                "options": ANIMAL_WORK_OPTIONS,
                "applies_to": animal_keys,
            })
            animal_qs.append({
                "id": Q_ANIMAL_WORK_ANIMALS,
                "type": "multi",
                "title": "그 일에서 다루는 동물을 모두 골라주세요.",
                "help": "이번 검사에서 양성으로 나온 동물 가운데 일하면서 다루는 것을 고르세요.",
                "options": [{"value": _key(i), "label": _name(a)} for i, a in animals]
                           + [{"value": "other", "label": "검사에서 양성이 아닌 다른 동물만 다뤄요", "exclusive": True}],
                "applies_to": animal_keys,
                "reveal_if": {"question": Q_ANIMAL_WORK, "includes_any": ANIMAL_WORK_TYPES},
            })
            animal_qs.append({
                "id": Q_ANIMAL_WORK_SYMPTOMS,
                "type": "single",
                "title": "코·눈·기침·숨참 같은 증상이 일하는 날에 심해지고, 쉬는 날이나 휴가 때 좋아지나요?",
                "help": "직업과 관련된 알레르기인지 가늠하는 질문입니다.",
                "options": YNU,
                "applies_to": animal_keys,
                "reveal_if": {"question": Q_ANIMAL_WORK, "includes_any": ANIMAL_WORK_TYPES},
            })
            sections.append({
                "id": "animal",
                "title": "동물 알러젠",
                "subtitle": "얼마나 접촉하는지, 접촉할 때 증상이 달라지는지 확인합니다.",
                "questions": animal_qs,
            })

        # ---- 섹션 5.5: 벌독(곤충 독) — 쏘였을 때의 반응 ----
        venom_keys = [_key(i) for i, a in enumerate(assessments) if _cat(a) == "venom"]
        if venom_keys:
            sections.append({
                "id": "venom",
                "title": "벌에 쏘였을 때",
                "subtitle": "벌독은 쏘여서 몸에 들어옵니다. 쏘였을 때 어떤 반응이 있었는지가 판단의 기준입니다.",
                "questions": [
                    {
                        "id": Q_STING,
                        "type": "single",
                        "title": "벌(꿀벌·말벌 등)에 쏘인 적이 있다면, 가장 심했던 반응은 어느 쪽인가요?",
                        "help": "쏘인 자리가 붓고 아픈 것은 누구에게나 생기는 반응입니다. "
                                "쏘인 자리를 벗어난 온몸 반응이 있었는지가 중요합니다.",
                        "options": STING_OPTIONS,
                        "applies_to": venom_keys,
                    },
                    _severity_question(
                        QP_SEVERITY + "venom", venom_keys,
                        {"reveal_if": {"question": Q_STING, "any": ["systemic"]}},
                        title="쏘인 뒤 온몸 반응이 있었을 때, 가장 심했던 정도는?"),
                ],
            })

        # ---- 섹션 6: 음식 / 구강알레르기 / 교차반응 ----
        ks = get_knowledge_service()
        shellfish_foods = [(i, a) for i, a in foods if ks.is_shellfish(a.allergen_name, a.korean_name)]
        other_foods = [(i, a) for i, a in foods if (i, a) not in shellfish_foods]
        # 음식/교차반응 섹션: 직접 음식·OAS 뿐 아니라 교차반응 후보가 있는 환경/동물 항원도 포함
        # (예: 고양이만 양성이어도 소고기·돼지고기 교차반응 문진이 필요)
        if foods or pollen_oas or has_mite or has_insect or has_mold or animals:
            food_qs = []
            # (1) OAS 스크리닝 게이트 — 구체적인 음식은 아래 항원별 문항에서 직접 확인
            if pollen_oas or foods:
                food_qs.append({
                    "id": Q_OAS, "type": "single",
                    "title": "생과일·생채소·견과를 먹으면 입·입술·혀·목이 가렵거나 붓나요?",
                    "help": "구강알레르기증후군(OAS)일 수 있습니다. ‘예/잘 모르겠어요’면 아래에서 "
                            "어떤 음식인지 항원별로 구체적으로 여쭤봅니다.",
                    "options": YNU, "applies_to": [],
                })
            # (3) 갑각류 양성 → 실제 섭취 반응(양방향 감별) + 증상 있으면 중증도
            for i, a in shellfish_foods:
                nm = _name(a)
                food_qs.append({
                    "id": QP_SHELLFISH + _key(i), "type": "single",
                    "title": f"{nm}{josa(nm, '을를')} 실제로 먹었을 때 어떤가요?",
                    "help": "검사에 강양성이어도 실제로 먹으면 아무렇지 않은 경우(감작만)가 흔합니다. "
                            "무증상이면 불필요하게 끊을 필요가 없습니다.",
                    "options": FOOD_REACT_OPTIONS, "applies_to": [_key(i)],
                })
                food_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_SHELLFISH + _key(i), "any": ["oral", "systemic"]}},
                    title=f"{nm} 섭취 시 증상이 있을 때, 가장 심했던 정도는?"))
            # (4) 일반 음식 양성 → 증상 유형(다중) + 증상 있으면 중증도
            for i, a in other_foods:
                nm = _name(a)
                food_qs.append({
                    "id": QP_FOOD_SYMPTOMS + _key(i), "type": "multi",
                    "title": f"{nm}{josa(nm, '을를')} 먹으면 어떤 증상이 생기나요?",
                    "help": "여러 개 선택 가능. 현재 문제없이 먹고 있다면 ‘먹어도 증상 없음’을 고르세요.",
                    "options": FOOD_SYMPTOM_OPTIONS, "applies_to": [_key(i)],
                })
                food_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_FOOD_SYMPTOMS + _key(i),
                                   "includes_any": ["oral", "skin", "gi", "breathing", "anaphylaxis"]}},
                    title=f"{nm} 섭취 시 증상이 있을 때, 가장 심했던 정도는?"))
            # (4.5) 항원(임상그룹)별 교차반응·OAS 문항 — 증상 게이트(Q-3) + 구체 음식 목록(A2)
            self._append_crossreact_questions(food_qs, assessments)
            # (5) 마무리 확인 — 그 밖의 음식 catch-all(재활성 트리거) → 전신 반응(A4)
            self._append_food_wrapup_catchall(food_qs, assessments)
            food_qs.append({
                "id": Q_FOOD_SYSTEMIC, "type": "single",
                "title": "마지막으로, 특정 음식을 먹은 뒤 두드러기·호흡곤란·복통 등 **전신** 증상이 있었나요?",
                "help": "위에서 고른 교차반응 음식이든 그 밖의 음식이든, 입·목을 넘어선 전신 반응은 "
                        "응급 상황일 수 있어 반드시 확인합니다.",
                "options": YNU, "applies_to": [],
            })
            sys_food_opts = [{"value": _key(i), "label": _name(a)} for i, a in foods]
            sys_food_opts.append({"value": "other", "label": "그 밖의 음식(검사에 없던 음식)"})
            food_qs.append({
                "id": Q_FOOD_SYSTEMIC_FOODS, "type": "multi",
                "title": "전신 증상을 일으켰던 음식을 모두 선택하세요.",
                "help": "여러 개일 수 있습니다. 각 음식마다 아래에서 심했던 정도를 여쭤봅니다.",
                "options": sys_food_opts,
                "applies_to": [], "reveal_if": {"question": Q_FOOD_SYSTEMIC, "equals": YES},
            })
            for i, a in foods:
                nm = _name(a)
                food_qs.append(_severity_question(
                    QP_FOOD_SYSTEMIC_SEV + _key(i), [_key(i)],
                    {"reveal_if": {"question": Q_FOOD_SYSTEMIC_FOODS, "includes_any": [_key(i)]}},
                    title=f"{nm}{josa(nm, '으로')} 인한 전신 증상은 어느 정도였나요?"))
            sections.append({
                "id": "food",
                "title": "음식·구강 알레르기 · 교차반응",
                "subtitle": "먹었을 때의 반응으로 실제 음식 알레르기와 교차반응을 가립니다.",
                "questions": food_qs,
            })

        # ---- 섹션 7: 기타(미분류) 양성 항원 — 질문 누락 원천 차단(Q-1) ----
        HANDLED = set(POLLEN_GROUPS) | {"mite", "insect", "mold", "animal", "food", "venom"}
        others = [(i, a) for i, a in enumerate(assessments)
                  if _cat(a) not in HANDLED and _cat(a) != "control"]
        if others:
            other_qs = []
            for i, a in others:
                nm = _name(a)
                spec = _CONTACT_QUESTION.get(_cat(a)) or {
                    "title": "「{nm}」에 노출되거나(음식이면 드셨을 때) 어떤 증상이 있나요?",
                    "help": "이 항원은 자동 분류가 어려워 직접 여쭤봅니다. 여러 개 선택 가능. 문제없으면 ‘증상 없음’.",
                    "options": FOOD_SYMPTOM_OPTIONS,
                    "severity_title": "{nm} 노출/섭취 시 증상이 있을 때, 가장 심했던 정도는?",
                }
                other_qs.append({
                    "id": QP_OTHER_SYMPTOM + _key(i), "type": "multi",
                    "title": spec["title"].replace("{nm}", nm),
                    "help": spec["help"],
                    "options": spec["options"], "applies_to": [_key(i)],
                })
                other_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_OTHER_SYMPTOM + _key(i),
                                   "includes_any": ["oral", "skin", "gi", "breathing", "anaphylaxis"]}},
                    title=spec["severity_title"].replace("{nm}", nm)))
            unclassified = any(_cat(a) not in _CONTACT_QUESTION for _i, a in others)
            sections.append({
                "id": "other",
                "title": "기타 항원",
                "subtitle": "자동 분류가 어려운 양성 항원도 빠짐없이 노출·증상을 확인합니다." if unclassified
                else "라텍스·약물처럼 들이마시거나 먹는 항원이 아닌 것은 닿거나 썼을 때의 반응을 확인합니다.",
                "questions": other_qs,
            })

        allergen_index = {
            _key(i): {
                "name": a.allergen_name,
                "korean_name": a.korean_name,
                "category": _cat(a),
                "season_label": (a.kb or {}).get("season_label_ko", ""),
                "strength": a.strength,
                "test_value": a.test_value,
                "test_unit": a.test_unit,
            }
            for i, a in enumerate(assessments)
        }

        return {
            "sections": sections,
            "allergen_index": allergen_index,
            "answer_prefill": self._prefill(assessments, screening),
        }

    def _pfas_options(self, assessments):
        """양성 꽃가루들의 교차반응 음식을 모아 (다중선택 옵션, en->꽃가루 연결맵) 반환.

        두 소스를 통합한다(하나의 교차반응 엔진으로 수렴 — item4):
          1) 큐레이션 PFAS 데이터셋(pollen_food_cross_reactivity.json) — 임상 특이 PFAS 증후군
             (자작-사과, 쑥-셀러리·향신료 등)을 우선(먼저 나열).
          2) 성분(component) 교차반응 엔진 — 레지스트리에 성분(PR-10·profilin·nsLTP)이
             태깅된 꽃가루면 자동으로 음식 후보를 파생(신규 꽃가루도 데이터만 있으면 확장).
        중복은 정규화 키로 제거하고, 옵션의 en 값은 소스별 표기를 보존한다.
        """
        ks = get_knowledge_service()
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            svc = None
        # 이미 양성으로 패널에 있는 항원은 교차반응 후보에서 제외(직접 문진되므로)
        positive_names = set()
        for x in assessments:
            positive_names.add(x.allergen_name)
            if x.korean_name:
                positive_names.add(x.korean_name)

        options: List[Dict[str, str]] = []
        link: Dict[str, List[str]] = {}
        seen_norm: Dict[str, str] = {}  # 정규화(ko/en) → 대표 en(옵션 value)

        def _add(en, ko, pollen_name):
            if not en:
                return
            key = _norm_key(ko) or _norm_key(en)
            rep = seen_norm.get(key)
            if rep is None:
                rep = en
                seen_norm[key] = rep
                options.append({"value": rep, "label": ko or en})
                link[rep] = []
            if pollen_name and pollen_name not in link[rep]:
                link[rep].append(pollen_name)

        for a in assessments:
            if _cat(a) not in POLLEN_GROUPS:
                continue
            pname = _name(a)
            # (1) 큐레이션 PFAS 우선
            pf = ks.pfas_foods_for(a.allergen_name, _cat(a))
            for food in pf.get("foods", []):
                _add(food.get("en"), food.get("ko"), pname)
            # (2) 성분 엔진 파생(레지스트리 성분 태깅 기반) — 통합
            if svc and svc.has_data():
                for c in svc.candidate_foods(a.allergen_name, a.korean_name or "",
                                             exclude_names=positive_names, limit=8):
                    _add(c["name"], c["korean"] or c["name"], pname)
        return options, link

    def oas_selected_foods(self, assessments, answers):
        """꽃가루(OAS) 교차반응으로 '증상 있음' 선택된 음식 목록.
        A2 이후 꽃가루도 항원별 교차반응 문항(QP_CROSSREACT)에서 구체적으로 받는다.
        반환: [{"en","ko","pollens":[...],"severity":...}]"""
        out = []
        for g in self._group_selected(assessments, answers):
            sp = g["spec"]
            if not sp["is_pollen"]:
                continue
            label = {c["name"]: (c["korean"] or c["name"]) for c in sp["cands"]}
            for en in g["selected"]:
                out.append({"en": en, "ko": label.get(en, en),
                            "pollens": [sp["label"]], "severity": g["severity"]})
        return out

    def _symptom_reveal_conds(self, a, key, assessments):
        """항원 X 가 '증상 있음/불명'일 때의 reveal 조건 목록(교차반응 게이트 Q-3).
        명확히 무증상(no)이면 조건이 충족되지 않아 교차반응 질문이 숨겨진다."""
        cat = _cat(a)
        yn = [YES, UNSURE]
        if cat in POLLEN_GROUPS:
            g = POLLEN_GROUPS[cat]["group"]
            return [{"question": QP_POLLEN + g, "any": yn}]
        if cat == "mite":
            return [{"question": Q_MITE_DUST, "any": yn},
                    {"question": Q_INDOOR_TIMING, "any": yn},
                    {"question": Q_INDOOR_AWAY, "any": yn}]
        if cat == "insect":
            return [{"question": Q_ROACH_ENV, "any": yn},
                    {"question": Q_INDOOR_TIMING, "any": yn},
                    {"question": Q_INDOOR_AWAY, "any": yn}]
        if cat == "mold":
            return [{"question": Q_MOLD_DAMP, "any": yn},
                    {"question": Q_MOLD_SPACE, "includes_any": sorted(MOLD_SPACE_SPECIFIC)},
                    {"question": Q_MOLD_OUTDOOR, "includes_any": ["leaves", "soil", "rain", "farm"]}]
        if cat == "animal":
            return [{"question": QP_ANIMAL_WORSE + key, "any": yn}]
        if cat == "food":
            return [{"question": QP_FOOD_SYMPTOMS + key,
                     "includes_any": ["oral", "skin", "gi", "breathing", "anaphylaxis"]},
                    {"question": QP_SHELLFISH + key, "any": ["oral", "systemic"]}]
        if cat in ("other", "latex", "drug"):
            return [{"question": QP_OTHER_SYMPTOM + key,
                     "includes_any": ["oral", "skin", "gi", "breathing", "anaphylaxis"]}]
        return []

    def _crossreact_specs(self, assessments):
        """임상 그룹 단위 교차반응 문항 스펙을 만든다(A1/A2).
        - Df/Dp 처럼 동일 임상 그룹은 하나로 합쳐 질문 1회만(중복 질의 제거).
        - 꽃가루는 큐레이션 PFAS(자작→사과·복숭아…) + 성분 엔진을 합쳐 '구체적인' 목록 제공.
        반환: [{lead_i, lead, label, keys[], cands[], is_pollen}]"""
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            return []
        if not svc.has_data():
            return []
        from services.clinical_group_service import get_clinical_group_service
        cg = get_clinical_group_service()
        ks = get_knowledge_service()

        positive_names = set()
        for x in assessments:
            positive_names.add(x.allergen_name)
            if x.korean_name:
                positive_names.add(x.korean_name)

        specs = []
        for grp in cg.collapse(assessments):
            lead_i, lead = grp["members"][0]
            keys = [_key(i) for i, _ in grp["members"]]
            is_pollen = _cat(lead) in POLLEN_GROUPS
            # 같은 음식은 한 번만 — 큐레이션 PFAS('apple'·'셀러리')와 성분 엔진('Apple'·'샐러리')이 같은 음식을
            # 다른 표기로 내어, 한 문항 안에 '바나나'가 두 번, 마무리 문항에 '사과'·'당근'이 두 번씩 나왔다.
            merged: Dict[str, Dict[str, Any]] = {}     # 대표 값 → 후보
            seen: Dict[str, str] = {}                  # 음식의 정체(레지스트리 id·정규화 이름) → 대표 값

            def add(en, ko, family=None, pfas=False):
                value, label, idkeys = self._food_identity(svc, ks, en, ko)
                rep = next((seen[k] for k in idkeys if k in seen), None)
                if rep is None:
                    rep = value
                    merged[rep] = {"name": value, "korean": label, "family_id": family, "pfas": False}
                for k in idkeys:
                    seen.setdefault(k, rep)
                merged[rep]["pfas"] = merged[rep]["pfas"] or pfas
                merged[rep]["family_id"] = merged[rep]["family_id"] or family

            for _, a in grp["members"]:
                # 꽃가루: 큐레이션 PFAS 를 먼저(임상 특이 증후군 보존)
                if _cat(a) in POLLEN_GROUPS:
                    for f in (ks.pfas_foods_for(a.allergen_name, _cat(a)) or {}).get("foods", []):
                        if f.get("en"):
                            add(f["en"], f.get("ko"), pfas=True)
                for c in svc.candidate_foods(a.allergen_name, a.korean_name or "",
                                             exclude_names=positive_names, limit=8):
                    add(c["name"], c["korean"], family=c.get("family_id"))
            if not merged:
                continue
            cands = list(merged.values())[:12]
            members = [a for _, a in grp["members"]]
            for c in cands:
                # watch: 리포트의 '교차반응 — 알아 둘 음식'에 실을 만한 근거(보고된 임상 증후군)가 있는가.
                # 성분(단백질 family)을 공유한다는 것만으로는 싣지 않는다(문항 후보로만 쓴다).
                c["watch"] = self._evidence_backed(svc, ks, members, c)
            label = grp["label"] if grp["grouped"] else _name(lead)
            specs.append({"lead_i": lead_i, "lead": lead, "label": label,
                          "keys": keys, "cands": cands, "is_pollen": is_pollen,
                          "group_note": grp["note"]})
        return specs

    def _pfas_labels(self, svc, ks) -> Dict[str, str]:
        """레지스트리 음식 id → 큐레이션 PFAS 의 한글 표기. 같은 음식은 어느 문항에서나 같은 이름으로 보이게 한다."""
        cached = getattr(self, "_pfas_label_cache", None)
        if cached is None:
            cached = {}
            for entry in (ks._load_pfas().get("pollen_food") or {}).values():
                for f in entry.get("foods", []) or []:
                    rec = svc.find(f.get("en") or "")
                    if rec and rec.get("category") == "food" and f.get("ko"):
                        cached.setdefault(rec["id"], f["ko"])
            self._pfas_label_cache = cached
        return cached

    def _food_identity(self, svc, ks, en, ko):
        """교차반응 후보 음식의 (선택지 값, 표시 이름, 같은 음식인지 가리는 키들).
        레지스트리에 있는 음식은 레지스트리 대표 이름이 값이다('apple' → 'Apple')."""
        rec = svc.find(en or "") if en else None
        if rec and rec.get("category") == "food":
            value = rec["canonical_name"]
            label = self._pfas_labels(svc, ks).get(rec["id"]) or rec.get("korean_name") or ko or value
            return value, label, ["id:" + rec["id"], _norm_key(value), _norm_key(label)]
        return en, ko or en, [k for k in (_norm_key(en), _norm_key(ko)) if k]

    @staticmethod
    def _evidence_backed(svc, ks, members, cand) -> bool:
        """이 후보 음식과 환자의 양성 항원 사이에 보고된 교차반응 증후군이 있는가
        (data/pollen_food_cross_reactivity.json 의 pollen_food · evidence_links)."""
        ev = ks._load_pfas().get("evidence_links") or {}
        by_category = ev.get("by_lead_category") or {}
        by_lead = {_norm_key(k): v for k, v in (ev.get("by_lead") or {}).items()}
        groups = ev.get("food_groups") or {}
        name = cand["name"]
        for a in members:
            cat = _cat(a)
            if cand.get("pfas") and cat in POLLEN_GROUPS:
                return True
            rec = svc.find(a.allergen_name, a.korean_name or "")
            canon = (rec or {}).get("canonical_name") or a.allergen_name
            if name in (by_category.get(cat) or {}).get("foods", []):
                return True
            if name in (by_lead.get(_norm_key(canon)) or {}).get("foods", []):
                return True
            if cat == "food" and any(canon in g.get("foods", []) and name in g.get("foods", [])
                                     for g in groups.values()):
                return True
        return False

    @staticmethod
    def _picked(answers, qid, cands) -> List[str]:
        """교차반응 문항에서 고른 음식(후보의 대표 값으로). 표기만 다른 값('apple' ↔ 'Apple')도 같은 음식이다."""
        by_norm = {_norm_key(c["name"]): c["name"] for c in cands}
        out = []
        for v in _multi((answers or {}).get(qid)):
            v = by_norm.get(_norm_key(v)) if isinstance(v, str) else None
            if v and v not in out:
                out.append(v)
        return out

    def _append_crossreact_questions(self, food_qs, assessments):
        """항원(임상그룹)별 교차반응·OAS 문항 + 증상 범위(중증도) 후속 질문.
        - Q-3 게이트: 원인 항원이 '증상 있음/불명'일 때만 proactive 노출
        - Q-5 재활성: 마무리 catch-all 에서 후보 음식을 지목하면 다시 노출
        - A3: 교차반응도 국소(입·목)~전신으로 갈리므로 선택 시 증상 범위를 묻는다"""
        from services.crossreactivity_service import get_crossreactivity_service
        svc = get_crossreactivity_service()
        for sp in self._crossreact_specs(assessments):
            lead, label, keys, cands = sp["lead"], sp["label"], sp["keys"], sp["cands"]
            risk = svc.worst_risk(lead.allergen_name, lead.korean_name or "")
            warn = ("이 중 일부는 전신 반응(두드러기·호흡곤란) 위험이 있어 특히 주의가 필요합니다. "
                    if risk in ("systemic", "raw_systemic") else "대개 입·목 증상이지만 개인차가 있습니다. ")
            grp_note = f"{sp['group_note']} " if sp.get("group_note") else ""
            opts = [{"value": c["name"], "label": c["korean"] or c["name"]} for c in cands]
            opts.append({"value": "none", "label": "해당 없음 / 문제된 것 없음", "exclusive": True})
            qid = QP_CROSSREACT + _key(sp["lead_i"])
            reveal = self._symptom_reveal_conds(lead, _key(sp["lead_i"]), assessments)
            if sp["is_pollen"]:
                reveal.append({"question": Q_OAS, "any": [YES, UNSURE]})
            reveal.append({"question": Q_FOOD_GENERAL, "includes_any": [c["name"] for c in cands]})
            kind = "구강알레르기증후군(OAS)" if sp["is_pollen"] else "교차반응"
            food_qs.append({
                "id": qid, "type": "multi",
                "title": f"「{label}」에 감작되어 있습니다. 아래 음식 중 **드셨을 때 입·목이 가렵거나 붓는 등 "
                         f"증상이 있었던 것**을 모두 고르세요.",
                "help": f"{grp_note}{label}{josa(label, '과와')} 같은 단백질(성분)을 공유해 {kind}{josa(kind, '이가')} 나타날 수 있는 음식입니다. "
                        f"{warn}실제로 증상이 있었던 것만 고르세요(감작만이면 불필요한 제한은 피합니다).",
                "options": opts, "applies_to": keys,
                "reveal_if_any": reveal,
            })
            # A3: 교차반응 증상 범위(국소/전신/아나필락시스)
            food_qs.append({
                "id": QP_CROSSREACT_SEV + _key(sp["lead_i"]), "type": "single",
                "title": f"위에서 고른 「{label}」 교차반응 음식의 증상은 어디까지였나요?",
                "help": "같은 교차반응이어도 입·목에만 그치는 분이 있고 전신 반응이 오는 분이 있어 확인합니다.",
                "options": CROSSREACT_SEVERITY_OPTIONS, "applies_to": keys,
                "reveal_if": {"question": qid,
                              "includes_any": [c["name"] for c in cands]},
            })

    def _append_food_wrapup_catchall(self, food_qs, assessments):
        """마무리 catch-all(A4) — 항원별로 물어본 음식을 제외한 '그 밖의' 후보만 제시.
        여기서 지목하면 해당 항원의 교차반응 문항이 재활성된다(Q-5)."""
        specs = self._crossreact_specs(assessments)
        if not specs:
            return
        union: Dict[str, str] = {}
        for sp in specs:
            for c in sp["cands"]:
                union.setdefault(c["name"], c["korean"] or c["name"])
        if not union:
            return
        food_qs.append({
            "id": Q_FOOD_GENERAL, "type": "multi",
            "title": "위에서 다루지 못한 음식 중, 드셨을 때 이상반응(입·목 가려움·두드러기 등)이 "
                     "있었던 것이 더 있나요?",
            "help": "감작된 항원과 성분을 공유해 교차반응할 수 있는 음식 목록입니다. 여기서 고르시면 "
                    "관련 항원의 교차반응 항목을 다시 확인합니다. 없으면 ‘해당 없음’.",
            "options": [{"value": en, "label": ko} for en, ko in union.items()]
                       + [{"value": "none", "label": "해당 없음", "exclusive": True}],
            "applies_to": [],
        })

    def _group_selected(self, assessments, answers):
        """임상그룹별 교차반응 선택 결과를 모은다(문항이 그룹 대표 key 로 생성되므로).
        반환: [{spec, selected:[en], severity}] — catch-all 재활성(Q-5) 병합 포함."""
        answers = answers or {}
        out = []
        for sp in self._crossreact_specs(assessments):
            qid = QP_CROSSREACT + _key(sp["lead_i"])
            # 마무리 catch-all 에서 이 그룹 후보를 지목 → 재활성/병합
            sel = list(dict.fromkeys(self._picked(answers, qid, sp["cands"])
                                     + self._picked(answers, Q_FOOD_GENERAL, sp["cands"])))
            if not sel:
                continue
            sev = answers.get(QP_CROSSREACT_SEV + _key(sp["lead_i"]))
            # 전신 음식반응 게이트에서 전신/아나필락시스로 답했으면 승격
            if not isinstance(sev, str) or sev not in ("oral", "systemic", "anaphylaxis"):
                sev = "oral"
            out.append({"spec": sp, "selected": sel, "severity": sev})
        return out

    def component_crossreact_items(self, assessments, answers):
        """교차반응 문항에서 '증상 있음'으로 선택된 음식을 FHIR 매핑용 항목으로 반환(비-꽃가루).
        반환: [{en, ko, source, severity, trigger}]"""
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            return []
        items, seen = [], set()
        for g in self._group_selected(assessments, answers):
            sp = g["spec"]
            if sp["is_pollen"]:
                continue  # 꽃가루는 OAS(source=pollen)로 별도 처리
            label = {c["name"]: (c["korean"] or c["name"]) for c in sp["cands"]}
            for en in g["selected"]:
                key = en.lower()
                if key in seen:
                    continue
                seen.add(key)
                rec = svc.find(en)
                ko = label.get(en) or (rec.get("korean_name") if rec else None) or en
                items.append({"en": en, "ko": ko, "source": "component",
                              "severity": g["severity"], "pollens": [], "trigger": sp["label"]})
        return items

    def crossreactive_food_items(self, assessments, answers):
        """FHIR 매핑용 교차반응 음식 통합 목록 (꽃가루 OAS + 성분 교차반응).
        severity 는 항원(그룹)별 교차반응 증상범위 문항(A3)에서 직접 받는다."""
        items = []
        seen = set()
        for f in self.oas_selected_foods(assessments, answers):
            k = (_norm_key(f.get("en")), _norm_key(f.get("ko")))
            if k in seen:
                continue
            seen.add(k)
            items.append({**f, "source": "pollen"})
        for it in self.component_crossreact_items(assessments, answers):
            k = (_norm_key(it.get("en")), _norm_key(it.get("ko")))
            if k in seen:
                continue
            seen.add(k)
            items.append(it)
        return items

    def _prefill(
        self, assessments: List[AllergenAssessment], screening: Optional[ScreeningProfile]
    ) -> Dict[str, Any]:
        """스크리닝 정보로 일부 답변을 미리 채운다(사용자가 수정 가능)."""
        pre: Dict[str, Any] = {}
        if not screening:
            return pre
        if screening.season_pattern and screening.season_pattern != SymptomSeasonPattern.NONE:
            pre[Q_PATTERN] = screening.season_pattern.value
        # 악화 월 → 계절
        if screening.worse_months:
            seasons = set()
            for s, months in SEASON_MONTHS.items():
                if set(months) & set(screening.worse_months):
                    seasons.add(s)
            if seasons:
                pre[Q_SEASONS] = sorted(seasons)
        if screening.oral_allergy_syndrome is not None:
            pre[Q_OAS] = YES if screening.oral_allergy_syndrome else NO
        if screening.food_systemic_reaction is not None:
            pre[Q_FOOD_SYSTEMIC] = YES if screening.food_systemic_reaction else NO
        # 반려동물 사육 → 동물 접촉 질문 자동 제안
        pets = screening.pets or []
        if pets:
            for i, a in enumerate(assessments):
                if _cat(a) != "animal":
                    continue
                nm = f"{a.allergen_name} {a.korean_name or ''}".lower()
                species = "cat" if ("cat" in nm or "고양이" in nm) else ("dog" if ("dog" in nm or "개" in nm) else None)
                # '키우는 동물'로 고른 종은 '함께 살아요'를 미리 골라 둔다. '키우는 동물 없음'은 접촉이 없다는
                # 뜻이 아니므로(자주 만나거나 일하면서 다룰 수 있다) 미리 채우지 않는다.
                if species and species in pets:
                    pre[QP_ANIMAL_CONTACT + _key(i)] = "live"
        return pre

    # ============================================================
    # 2) 판정
    # ============================================================
    def normalize_answers(
        self,
        result: RelevanceAssessmentResult,
        answers: Optional[Dict[str, Any]],
        screening: Optional[ScreeningProfile] = None,
    ) -> Dict[str, Any]:
        """배타 선택지(`exclusive: True`)가 다른 선택지와 함께 온 다중 선택 답을 정리한다(사본을 돌려줌).

        규칙: 배타 선택지와 일반 선택지가 섞여 있으면 **일반 선택지가 남고 배타 선택지는 버린다.**
        브라우저는 둘을 함께 보내지 않지만 `/api` 는 임의 payload 를 받는다. '증상 없음'을 이기게 하면
        함께 온 아나필락시스·호흡곤란 같은 실제 증상 병력이 지워져 안전하지 않고, 판정 코드도 이미
        `none` 을 일반 선택지와 함께 오면 무시하도록 짜여 있어 그 동작과 같다. 배타 선택지끼리만 있으면
        (예: none+never) 그대로 둔다. 거부(오류)하지 않는다 — 저장된 세션·오래된 클라이언트도 읽어야 한다."""
        out, _ignored = self.sanitize_answers(result, answers, screening)
        exclusive = self.exclusive_values(result, screening)
        for qid, excl in exclusive.items():
            if qid not in out or out[qid] is None:
                continue
            picked = _multi(out[qid])
            if any(v in excl for v in picked) and any(v not in excl for v in picked):
                out[qid] = [v for v in picked if v not in excl]
        return out

    # 이 환자의 문진에 문항이 만들어지지 않았더라도 값의 모양은 정해져 있는 전역 문항(예전 클라이언트·저장된
    # 세션이 보낼 수 있다). 문진에 없는 그 밖의 id 는 판정이 읽지 않으므로 버린다.
    _STATIC_SPECS = {
        Q_PATTERN: ("single", ["perennial", "seasonal", "both", "none"]),
        Q_SEASONS: ("multi", ["spring", "summer", "fall", "winter", "none"]),
        Q_OAS: ("single", [YES, NO, UNSURE]),
        Q_FOOD_SYSTEMIC: ("single", [YES, NO, UNSURE]),
        Q_INDOOR_TIMING: ("single", [YES, NO, UNSURE]),
        Q_INDOOR_AWAY: ("single", [YES, NO, UNSURE]),
        Q_MITE_DUST: ("single", [YES, NO, UNSURE]),
        Q_MOLD_DAMP: ("single", [YES, NO, UNSURE]),
        Q_ROACH_ENV: ("single", [YES, NO, UNSURE]),
        Q_MOLD_SPACE: ("multi", [o["value"] for o in MOLD_SPACE_OPTIONS]),
        Q_MOLD_OUTDOOR: ("multi", [o["value"] for o in MOLD_OUTDOOR_OPTIONS]),
        Q_MITE_BEDDING_TRIAL: ("single", [o["value"] for o in TRIAL_OPTIONS]),
        Q_MOLD_DEHUM_TRIAL: ("single", [o["value"] for o in TRIAL_OPTIONS]),
        Q_STING: ("single", [o["value"] for o in STING_OPTIONS]),
        Q_ANIMAL_WORK: ("multi", [o["value"] for o in ANIMAL_WORK_OPTIONS]),
        Q_ANIMAL_WORK_SYMPTOMS: ("single", [YES, NO, UNSURE]),
    }
    _MAX_IGNORED = 50      # 응답에 싣는 '버린 답' 목록의 상한(임의 payload 를 그대로 되비추지 않는다)

    def question_specs(self, result, screening=None) -> Dict[str, Dict[str, Any]]:
        """문항 id → {type, values:[선택지 값], exclusive:set}. 이 환자에게 만들어진 문진이 기준이고,
        만들어지지 않은 전역 문항은 _STATIC_SPECS 의 고정 선택지로 본다."""
        specs: Dict[str, Dict[str, Any]] = {
            qid: {"type": kind, "values": list(values), "exclusive": set()}
            for qid, (kind, values) in self._STATIC_SPECS.items()}
        for sec in self.build(result, screening).get("sections", []):
            for q in sec.get("questions", []):
                opts = q.get("options", [])
                specs[q["id"]] = {
                    "type": q.get("type") or "single",
                    "values": [o["value"] for o in opts],
                    "exclusive": {o["value"] for o in opts if o.get("exclusive") is True}}
        for qid, spec in specs.items():
            # 예전 문진의 동물 접촉 답(yes=키우거나 자주 접촉 / no=거의 접촉 없음)은 계속 읽는다
            if qid.startswith(QP_ANIMAL_CONTACT):
                spec["values"] = spec["values"] + [v for v in (YES, NO) if v not in spec["values"]]
        return specs

    def sanitize_answers(
        self,
        result: RelevanceAssessmentResult,
        answers: Optional[Dict[str, Any]],
        screening: Optional[ScreeningProfile] = None,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """`/api` 로 들어온 answers 를 이 환자의 문진에 맞춰 정리한다. (정리된 답, 버린 답 목록)을 돌려준다.

        `answers` 는 `Dict[str, Any]` 라 무엇이든 들어온다 — 선택지에 없는 값(`mold_outdoor: ["zzz"]`), 숫자·
        객체·중첩 목록, 문진에 없는 문항 id. 예전에는 판정 코드가 이것들을 그대로 읽다가 KeyError·TypeError 로
        500 을 냈다(선택지 라벨 조회, set() 변환, 집합 포함 검사). 규칙은 한 가지다 — **거부하지 않고 버린다**:
          - 문진에 없는 문항 id            → 버린다(unknown_question)
          - 선택지에 없는 값·문자열이 아닌 값 → 그 값만 버린다(invalid_value). 남은 값이 없으면 문항째 뺀다
          - 단일 선택은 문자열 하나여야 한다(목록·숫자·객체는 버린다). 다중 선택에 문자열이 오면 한 칸짜리 목록으로
          - 선택지 값의 대소문자·공백만 다른 값('apple' ↔ 'Apple')은 선택지의 값으로 맞춘다
        버린 답은 판정에 쓰지 않으므로 '답하지 않음'과 같다. 저장된 세션·오래된 클라이언트도 읽어야 해서
        422 로 막지 않는다."""
        clean: Dict[str, Any] = {}
        ignored: List[Dict[str, Any]] = []
        if not isinstance(answers, dict) or not answers:
            return clean, ignored
        specs = self.question_specs(result, screening)

        def note(qid, reason, dropped=1):
            if len(ignored) < self._MAX_IGNORED:
                ignored.append({"id": str(qid)[:80], "reason": reason, "dropped": dropped})

        for qid, raw in answers.items():
            spec = specs.get(qid) if isinstance(qid, str) else None
            if spec is None:
                note(qid, "unknown_question")
                continue
            if raw is None:
                continue
            by_norm = {_norm_key(v): v for v in spec["values"] if _norm_key(v)}
            is_list = isinstance(raw, (list, tuple, set))
            if spec["type"] != "multi" and is_list:
                note(qid, "invalid_value", max(1, len(raw)))      # 단일 선택에 목록 — 어느 값인지 알 수 없다
                continue
            items = list(raw) if is_list else [raw]
            kept: List[str] = []
            for v in items:
                if isinstance(v, str):
                    v = v if v in spec["values"] else by_norm.get(_norm_key(v))
                    if v is not None and v not in kept:
                        kept.append(v)
            if spec["type"] == "multi":
                if kept:
                    clean[qid] = kept
            elif kept:
                clean[qid] = kept[0]
            if len(items) > len(kept):
                note(qid, "invalid_value", len(items) - len(kept))
        return clean, ignored

    def exclusive_values(self, result, screening=None) -> Dict[str, set]:
        """다중 선택 질문 id → 배타 선택지 값 집합(문진을 만들어 `exclusive` 플래그에서 읽는다)."""
        return {qid: spec["exclusive"] for qid, spec in self.question_specs(result, screening).items()
                if spec["type"] == "multi" and spec["exclusive"]}

    def classify(
        self,
        result: RelevanceAssessmentResult,
        answers: Dict[str, Any],
        screening: Optional[ScreeningProfile] = None,
    ) -> RelevanceAssessmentResult:
        answers = self.normalize_answers(result, answers, screening)
        apply_answers_to_screening(screening, answers)
        organs = list(getattr(screening, "organ_systems", None) or [])
        pattern = answers.get(Q_PATTERN)
        worse_seasons = set(_multi(answers.get(Q_SEASONS)))
        worse_months = set()
        for s in worse_seasons:
            worse_months |= set(SEASON_MONTHS.get(s, []))

        oas = answers.get(Q_OAS)
        food_systemic = answers.get(Q_FOOD_SYSTEMIC)
        # 진드기 동시 양성 여부 — 곰팡이 판정에서 '구분 불가' 를 가려내는 데 쓴다
        has_mite_pos = any(_cat(x) == "mite" for x in result.assessments)
        indoor_timing = answers.get(Q_INDOOR_TIMING)
        indoor_away = answers.get(Q_INDOOR_AWAY)
        mite_dust = answers.get(Q_MITE_DUST)
        mold_damp = answers.get(Q_MOLD_DAMP)
        roach_env = answers.get(Q_ROACH_ENV)

        # OAS 로 선택된 교차반응 음식을 꽃가루별로 정리
        oas_by_pollen: Dict[str, List[str]] = {}
        for f in self.oas_selected_foods(result.assessments, answers):
            for p in f.get("pollens", []):
                oas_by_pollen.setdefault(p, []).append(f["ko"])

        ks = get_knowledge_service()
        for i, a in enumerate(result.assessments):
            cat = _cat(a)
            if cat in POLLEN_GROUPS:
                oas_foods_here = oas_by_pollen.get(_name(a), [])
                a.oas_foods = oas_foods_here  # 결과카드/리포트/카드뉴스/FHIR 로 전파
                self._classify_pollen(a, cat, answers, worse_months, oas, oas_foods_here, organs)
            elif cat == "mite":
                self._classify_indoor(a, "mite", indoor_timing, indoor_away, mite_dust, pattern, answers,
                                      organs)
            elif cat == "insect":
                self._classify_indoor(a, "insect", indoor_timing, indoor_away, roach_env, pattern, answers,
                                      organs)
            elif cat == "mold":
                self._classify_mold(a, mold_damp, worse_months, pattern, answers,
                                    has_mite=has_mite_pos, organs=organs)
            elif cat == "animal":
                self._classify_animal(a, _key(i), answers, organs)
            elif cat == "venom":
                self._classify_venom(a, answers)
            elif cat == "food":
                if ks.is_shellfish(a.allergen_name, a.korean_name):
                    self._classify_shellfish(a, _key(i), answers, has_mite="mite" in [_cat(x) for x in result.assessments])
                else:
                    self._classify_food_symptoms(a, _key(i), answers, food_systemic)
            elif cat == "drug":
                self._classify_drug(a, _key(i), answers, screening)
            else:
                self._classify_other(a, _key(i), answers, pattern)
        # 성분 교차반응을 confirmed(증상 확인) / risk(가능성)로 나눠 각 유발 항원에 전파
        self._populate_crossreact(result.assessments, answers)
        self._apply_drug_class_notes(result.assessments, screening)
        # 판정을 미룬 항목의 '확인 포인트' — 이 환자의 문진에서 아직 답이 없는 문항만
        open_q = self._open_questions(result, answers, screening)
        for i, a in enumerate(result.assessments):
            a.open_questions_ko = open_q.get(_key(i), [])
        return result

    def _open_questions(self, result, answers, screening) -> Dict[str, List[str]]:
        """항원 key → 그 항원에 해당하는 문항 가운데 답이 없거나 '잘 모르겠어요'인 것의 제목.

        리포트는 판정을 미룬 항목에 '확인 포인트'를 하나 적는다. 예전에는 지식베이스의 일반 질문을 문진 답과
        무관하게 그대로 적어, '쏘인 자리만 붓고 아팠어요'라고 답한 환자에게 '벌에 쏘인 적이 있나요?'를 다시
        물었다. 여기서는 문진에 실제로 있는 문항만, 답이 비어 있을 때만 돌려준다. 다른 답에 따라 열리는
        후속 문항(중증도·교차반응·접촉 빈도)과 특정 항원에 딸리지 않은 전역 문항은 뺀다."""
        out: Dict[str, List[str]] = {}
        for sec in self.build(result, screening).get("sections", []):
            for q in sec.get("questions", []):
                if not q.get("applies_to") or q.get("reveal_if") or q.get("reveal_if_any"):
                    continue
                v = answers.get(q["id"])
                if v not in (None, UNSURE) and v != []:
                    continue
                title = re.sub(r"\*\*", "", q.get("title") or "").strip()
                for key in q["applies_to"]:
                    out.setdefault(key, []).append(title)
        return out

    @staticmethod
    def _drug_history_sentence(screening) -> str:
        """처음 문진에서 '약물 알레르기' 병력을 알려 준 환자에게 덧붙이는 문장."""
        if "drug_allergy" in (getattr(screening, "allergic_diseases", None) or []):
            return " 문진에서 약물 알레르기 병력을 알려 주셨습니다. 어떤 약이었는지 진료에서 이 결과와 함께 확인하세요."
        return ""

    def _apply_drug_class_notes(self, assessments, screening=None) -> None:
        """같은 계열의 약에 반응이 있었으면, 그 계열의 다른 약의 안내를 '쓰기 전에 진료에서 상의'로 바꾼다.

        약마다 따로 판정하면 페니실린 G 에 반응이 있었다는 환자의 아목시실린에 '스스로 끊거나 피하지 마세요'가
        붙는다. 같은 계열의 약이 모두 함께 반응하는 것은 아니지만, 그 판단은 진료에서 한다."""
        drugs = [a for a in assessments if _cat(a) == "drug"]
        for a in drugs:
            cls = drug_class(a)
            if not cls or a.answers.get(A_DRUG_HISTORY) == "reaction":
                continue
            others = [_name(x) for x in drugs if x is not a and drug_class(x) and drug_class(x)[0] == cls[0]
                      and x.answers.get(A_DRUG_HISTORY) == "reaction"]
            if not others:
                continue
            name = _name(a)
            a.answers[A_DRUG_CLASS_ALERT] = ", ".join(others)
            a.answers[A_DRUG_CLASS] = cls[1]
            head = {
                "tolerated": f"{name} 검사는 양성이고, 이 약을 쓴 뒤에는 문제가 없었다고 답하셨습니다.",
            }.get(a.answers.get(A_DRUG_HISTORY),
                  f"{name} 검사는 양성이지만 이 약을 쓴 뒤의 반응은 알 수 없습니다.")
            a.rationale_ko = (
                f"{head} 검사 양성만으로 약물 알레르기라고 하지 않습니다. "
                f"다만 같은 계열({cls[1]})인 {', '.join(others)}에 반응이 있었다고 답하셨습니다. "
                f"같은 계열의 약은 함께 반응하는 경우가 있어(모든 약이 그런 것은 아닙니다), "
                f"{name}{josa(name, '을를')} 포함한 관련 약은 쓰기 전에 진료에서 상의하세요. "
                f"약을 스스로 끊거나 다시 쓰지 말고, 어떻게 할지는 진료에서 정합니다."
                + self._drug_history_sentence(screening))

    def _populate_crossreact(self, assessments, answers):
        """양성 항원별로 교차반응 후보를 confirmed/risk 로 분류해 assessment 에 기록.
        confirmed = QP_CROSSREACT 에서 증상 보고한 음식, risk = 나머지 후보(가능성만)."""
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            return
        if not svc.has_data():
            return
        answers = answers or {}
        for sp in self._crossreact_specs(assessments):
            qid = QP_CROSSREACT + _key(sp["lead_i"])
            cr_sev = answers.get(QP_CROSSREACT_SEV + _key(sp["lead_i"]))
            # 마무리 catch-all 에서 이 그룹의 후보로 지목된 음식도 confirmed 로 병합(재활성 Q-5)
            sel = set(self._picked(answers, qid, sp["cands"])) | set(self._picked(answers, Q_FOOD_GENERAL, sp["cands"]))
            confirmed, risk = [], []
            for c in sp["cands"]:
                ko = c["korean"] or c["name"]
                if c["name"] in sel:
                    confirmed.append(ko)
                elif c.get("watch"):
                    # 증상이 없는 후보는 보고된 교차반응 증후군이 있는 음식만 '알아 둘 음식'으로 남긴다.
                    # 예전에는 성분만 공유하면 모두 실어, 고양이에 소고기·우유가, 진드기에 아니사키스가 올랐다.
                    risk.append(ko)
            # 그룹의 모든 멤버 항원에 동일하게 기록(Df/Dp 중복 서술 방지는 리포트 단계에서 처리)
            for idx, a in enumerate(assessments):
                if _key(idx) in sp["keys"]:
                    oas_ko = set(a.oas_foods or [])
                    a.crossreact_confirmed = [x for x in confirmed if x not in oas_ko]
                    a.crossreact_risk = [x for x in risk if x not in oas_ko]
                    if confirmed or oas_ko:
                        a.crossreact_severity = cr_sev if isinstance(cr_sev, str) and cr_sev in (
                            "oral", "systemic", "anaphylaxis") else "oral"

    # ---- 중증도/증상 보조 ----
    _SEV_RANK = {"mild": 1, "moderate": 2, "severe": 3, "anaphylaxis": 4}

    def _pick_severity(self, answers, *qids, default=None):
        """여러 severity 질문 중 답변된 것들에서 가장 높은 중증도를 반환."""
        best, best_rank = None, 0
        for qid in qids:
            v = (answers or {}).get(qid)
            r = self._SEV_RANK.get(v, 0)
            if r > best_rank:
                best, best_rank = v, r
        return best or default

    def _set_symptoms(self, a, manifestations, severity):
        """문진에서 확인된 증상(reaction.manifestation)과 중증도를 assessment 에 기록."""
        a.reported_symptoms = [m for m in (manifestations or []) if m]
        if severity:
            a.severity = severity

    _FOOD_SYMPTOM_TEXT = {
        "oral": "입·입술·목 가려움/부종",
        "skin": "두드러기·피부 발진",
        "gi": "복통·구토·설사",
        "breathing": "호흡곤란·기침·쌕쌕거림",
        "anaphylaxis": "아나필락시스(어지럼·전신 반응)",
    }

    # ---- 카테고리별 판정 ----
    def _classify_pollen(self, a, cat, answers, worse_months, oas, oas_foods=None, organs=None):
        g = POLLEN_GROUPS[cat]
        season_ans = answers.get(QP_POLLEN + g["group"])
        peak = set((a.kb or {}).get("peak_months_korea", []) or g["months"])
        overlap = bool(peak & worse_months) if worse_months else None
        if overlap is not None:
            a.season_overlap = overlap    # 문진에서 답한 악화 계절 기준(리포트의 '시즌과 겹칩니다' 문장)
        name = _name(a)
        # 판정 근거에는 문항이 물은 '시기 묶음'을 달 없이 적는다. 달은 그 항원 자신의 시기 한 가지만
        # (pollen_forecast_service.allergen_season) 리포트·카드에 나가야 한다.
        when = f"{g['label']} 시기"
        oas_note = ""
        if oas_foods:
            oas_note = (f" 또한 {name}{josa(name, '과와')} 교차반응으로 {', '.join(oas_foods)} 섭취 시 입·목 증상"
                        f"(구강알레르기증후군)이 나타난다고 하셨습니다. 해당 음식은 생으로 먹을 때 특히 주의하고, "
                        f"익히면 대개 증상이 줄어듭니다.")
        elif oas == YES and (a.kb or {}).get("oral_allergy_syndrome_ko"):
            oas_note = (f" 또한 {name} 감작은 일부 생과일·채소와 교차반응(구강알레르기증후군)을 "
                        f"일으킬 수 있어, 해당 음식 섭취 시 입·목 증상에 유의하세요.")

        # 구체적인 꽃가루-시즌 질문(season_ans)이 코스한 계절 선택(overlap)보다 우선한다.
        if season_ans == YES:
            verdict = "relevant"
        elif season_ans == NO:
            verdict = "sensitized"
        elif overlap is True:
            verdict = "relevant"
        elif overlap is False:
            verdict = "sensitized"
        else:
            verdict = "indeterminate"

        if verdict == "relevant":
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{when}에 증상이 실제로 악화되어, 검사 양성이 임상적으로 의미 있는 "
                f"꽃가루 알레르기로 판단됩니다. 해당 시즌 외출·환기 관리가 중요합니다.{oas_note}")
            sev = self._pick_severity(answers, QP_SEVERITY + "pollen_" + g["group"], default="moderate")
            self._set_symptoms(a, [f"{when} {_site_phrase(organs, ('nasal', 'ocular'))} 악화"], sev)
        elif verdict == "sensitized":
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성(감작)이지만 {when}에 증상 악화가 뚜렷하지 않습니다. "
                f"→ 현재는 감작만 되어 있고 실제 증상은 유발하지 않는 것으로 보이며, 과도한 회피는 "
                f"필요하지 않습니다.{oas_note}")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{when}의 증상 변화 정보가 부족해 판정을 보류합니다. 다음 시즌에 증상 "
                f"악화 여부를 관찰해 보세요.{oas_note}")

    def _classify_indoor(self, a, kind, timing, away, specific, pattern, answers=None, organs=None):
        name = _name(a)
        ans = answers or {}
        # 침구 관리 후 호전 = 집먼지진드기 특이 근거(공간·시간대 질문보다 강하다)
        bedding = ans.get(Q_MITE_BEDDING_TRIAL) if kind == "mite" else None
        space = _multi(ans.get(Q_MOLD_SPACE))
        bedroom_cue = kind == "mite" and "bedroom" in space
        strong = any(x == YES for x in (timing, away, specific)) or bedding == "better" or bedroom_cue
        all_no = all(x == NO for x in (timing, away, specific) if x is not None) and \
            any(x is not None for x in (timing, away, specific))
        label = "집먼지진드기" if kind == "mite" else "바퀴 등 실내 곤충"
        if strong:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            # 단서마다 방향(악화/호전)이 달라서 '·' 로 이어 붙이면 안 된다.
            # '저녁 악화·집을 비우면 호전' 을 한 덩어리로 읽으면 호전이 악화로 뒤집힌다
            # (영어 번역에서 실제로 뒤집혔다). 각 단서를 완결된 절로 쓰고 쉼표로 나눈다.
            trg = []
            if timing == YES:
                trg.append("저녁과 새벽, 이른 아침에 증상이 심해집니다")
            if away == YES:
                trg.append("집을 비우면 증상이 좋아집니다")
            if specific == YES:
                trg.append("먼지를 만지거나 해당 환경에 노출되면 심해집니다" if kind == "mite"
                           else "해당 환경에 노출되면 심해집니다")
            if bedroom_cue:
                trg.append("침실 잠자리 주변에서 심해집니다")
            if bedding == "better":
                trg.append("침구를 관리한 뒤 좋아졌습니다")
            cues = ". ".join(trg) if trg else "노출될 때 증상이 심해집니다"
            a.rationale_ko = (
                f"{cues}. 이 패턴이 확인되어 {label} 알레르기가 실제 증상의 원인으로 판단됩니다. "
                f"침구와 실내 환경 관리가 핵심입니다.")
            sev = self._pick_severity(answers or {}, QP_SEVERITY + "indoor", default="moderate")
            sites = _site_phrase(organs, ("nasal", "ocular", "lower_airway"))
            self._set_symptoms(a, [f"{label} 실내 노출 시 {sites} 악화({cues})"], sev)
        elif all_no:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 실내 노출·시간대와 증상의 연관이 뚜렷하지 않습니다. "
                f"→ 현재는 감작 위주로 보이며, 증상이 새로 생기거나 악화되면 재평가하세요.")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{label} 관련 노출·증상 정보가 부족해 판정을 보류합니다. 저녁·아침 증상, 청소·이불 "
                f"정리 시 변화, 외박 시 호전 여부를 기록해 보세요.")

    def _classify_mold(self, a, damp, worse_months, pattern, answers=None, has_mite=False, organs=None):
        """곰팡이 판정. 집먼지진드기와 함께 양성이면 '습할 때 악화' 만으로는 구분되지 않는다.
        곰팡이에만 해당하는 단서(늘 젖은 공간·실외 포자·제습 후 호전)가 있어야 원인으로 인정하고,
        그런 단서가 없으면 감작만으로 단정하지 않고 '구분 보류'로 남긴다."""
        ans = answers or {}
        space = _multi(ans.get(Q_MOLD_SPACE))
        outdoor = [x for x in _multi(ans.get(Q_MOLD_OUTDOOR)) if x != "none"]
        dehum = ans.get(Q_MOLD_DEHUM_TRIAL)
        bedding = ans.get(Q_MITE_BEDDING_TRIAL)
        habitat = mold_habitat(a)

        space_cues = [x for x in space if x in MOLD_SPACE_SPECIFIC]
        cues = []
        if space_cues:
            labels = {o["value"]: o["label"] for o in MOLD_SPACE_OPTIONS}
            cues.append("·".join(labels[x] for x in space_cues) + "에서 악화")
        if outdoor:
            labels = {o["value"]: o["label"] for o in MOLD_OUTDOOR_OPTIONS}
            cues.append("실외에서 " + "·".join(labels[x] for x in outdoor) + " 시 악화")
        if dehum == "better":
            cues.append("제습·곰팡이 제거 후 호전")
        mold_specific = bool(cues)

        peak = set((a.kb or {}).get("peak_months_korea", []) or [7, 8, 9])
        overlap = bool(peak & worse_months) if worse_months else None
        if overlap is not None:
            a.season_overlap = overlap
        sites = _site_phrase(organs, ("nasal", "lower_airway"))

        habitat_tip = {
            "outdoor": "이 곰팡이는 실외 포자입니다. 낙엽·잔디·퇴비 작업을 피하고, 비 온 뒤와 "
                       "늦여름~가을에 창문을 닫고 마스크를 쓰세요.",
            "indoor": "이 곰팡이는 실내에서 자랍니다. 습도를 50% 아래로 낮추고 누수·결로를 고치세요. "
                      "에어컨·가습기 필터도 정기적으로 청소하세요.",
        }.get(habitat, "실내는 제습·환기로, 실외는 낙엽·퇴비 노출을 줄여 관리하세요.")

        if mold_specific:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{', '.join(cues)} 패턴이 확인되었습니다. 이는 집먼지진드기로는 설명되지 않는 "
                f"곰팡이 특유의 노출 상황이라, 곰팡이가 실제 증상의 원인으로 판단됩니다. {habitat_tip}")
            sev = self._pick_severity(ans, QP_SEVERITY + "indoor", default="moderate")
            self._set_symptoms(a, [f"곰팡이 노출 시 {sites} 악화(" + ", ".join(cues) + ")"], sev)
            return

        # 곰팡이 특이 단서가 없는데 진드기도 양성 → 둘을 가를 수 없다. 단정하지 않는다.
        if has_mite and (damp == YES or overlap is True):
            a.relevance = ClinicalRelevance.INDETERMINATE
            extra = ""
            if bedding == "better" and dehum in (None, "never"):
                extra = ("침구 관리로는 좋아졌지만 제습·곰팡이 제거는 시도한 적이 없어, "
                         "현재 증상은 집먼지진드기로 설명될 가능성이 더 큽니다. ")
            a.rationale_ko = (
                "습할 때 증상이 심해지지만, 집먼지진드기도 습도가 높으면 함께 늘어나기 때문에 "
                "이 단서만으로는 둘을 구분할 수 없습니다. " + extra +
                "욕실·지하실·누수 부위처럼 늘 젖어 있는 공간에서만 심해지는지, 제습기나 곰팡이 제거 "
                "뒤 좋아지는지를 확인하면 구분됩니다. 판정은 보류합니다.")
            return

        if damp == YES or overlap is True:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                "습한 환경·곰팡이 노출 시 증상이 악화되어, 곰팡이 알레르기가 임상적으로 의미 있는 "
                f"것으로 판단됩니다. {habitat_tip}")
            sev = self._pick_severity(ans, QP_SEVERITY + "indoor", default="moderate")
            self._set_symptoms(a, [f"습한 환경·곰팡이 노출 시 {sites} 악화"], sev)
            return

        explicit_no = (damp == NO) or ("none" in space) or (dehum == "same")
        if explicit_no or overlap is False:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            note = ""
            if has_mite:
                note = "같은 실내 증상은 집먼지진드기로 설명됩니다. "
            a.rationale_ko = (
                "검사는 양성이지만 습한 공간·실외 포자 노출과 증상의 연관이 뚜렷하지 않습니다. "
                + note + "→ 현재는 감작 위주로 보이며 과도한 회피는 필요하지 않습니다.")
            return

        a.relevance = ClinicalRelevance.INDETERMINATE
        a.rationale_ko = (
            "곰팡이 노출과 증상의 관계를 판단할 정보가 부족합니다. 욕실·지하실·누수 부위에서의 변화, "
            "비 온 뒤나 낙엽·퇴비 작업 시 변화를 기록해 보세요.")

    def _classify_animal(self, a, key, answers, organs=None):
        """동물 항원. 접촉이 늘 때 증상이 심해지면 임상적으로 의미 있는 알레르기로 본다(예전과 같다).

        접촉 정도(함께 삶 / 자주 / 가끔 / 거의 없음)와 직업 노출은 '노출이 충분했는가'를 정한다.
          - 접촉이 있거나 일하면서 다루는데 증상 악화가 없다 → 감작만
          - 거의 접촉이 없고 일하면서 다루지도 않는다        → 판정 보류(노출이 부족하다)
          - 일하면서 다루고, 일하는 날 심해지고 쉬는 날 좋아진다고 답했다 → 접촉 시 악화를 '아니오'라고
            답했더라도 '감작만'으로 정하지 않고 판정을 미룬다(직업과 관련된 증상일 수 있다).
        답은 a.answers 에 남긴다 — 감작만·관찰 필요 안내, 동물 관리 안내, FHIR note, 상담이 이 값을 본다.
        """
        name = _name(a)
        contact = answers.get(QP_ANIMAL_CONTACT + key)
        contact = contact if isinstance(contact, str) else None
        worse = answers.get(QP_ANIMAL_WORSE + key)
        if contact in (YES, NO) or contact in [o["value"] for o in ANIMAL_CONTACT_OPTIONS]:
            a.answers[A_ANIMAL_CONTACT] = contact
        if worse in (YES, NO, UNSURE):
            a.answers[A_ANIMAL_WORSE] = worse
        level = LEGACY_CONTACT.get(contact, contact if A_ANIMAL_CONTACT in a.answers else None)
        freq = answers.get(QP_ANIMAL_FREQ + key)
        if level == "frequent" and freq in [o["value"] for o in ANIMAL_FREQ_OPTIONS]:
            a.answers[A_ANIMAL_FREQ] = freq
        work_types = [t for t in _multi(answers.get(Q_ANIMAL_WORK)) if t in ANIMAL_WORK_TYPES]
        occupational = bool(work_types) and key in _multi(answers.get(Q_ANIMAL_WORK_ANIMALS))
        work_symptoms = answers.get(Q_ANIMAL_WORK_SYMPTOMS) if occupational else None
        if _multi(answers.get(Q_ANIMAL_WORK)):          # 직업 질문에 답한 경우에만 남긴다
            a.answers[A_ANIMAL_OCCUPATIONAL] = YES if occupational else NO
        if occupational:
            a.answers[A_ANIMAL_WORK_TYPES] = ",".join(work_types)
            if work_symptoms in (YES, NO, UNSURE):
                a.answers[A_ANIMAL_WORK_SYMPTOMS] = work_symptoms
        exposed = level in ("live", "frequent", "occasional", "legacy_yes") or occupational
        work_tail = (" 일하면서 다루는 동물이므로 직업과 관련된 알레르기인지 평가가 필요합니다. "
                     "진료에서 하는 일을 알려 주세요.") if occupational else ""

        if worse == YES:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 접촉이 늘 때 증상이 악화되어, 임상적으로 의미 있는 동물 알레르기로 판단됩니다. "
                f"접촉을 줄이고 침실 등 생활공간 노출을 관리하세요.{work_tail}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="moderate")
            sites = _site_phrase(organs, ("nasal", "ocular", "skin", "lower_airway"))
            self._set_symptoms(a, [f"{name} 접촉 시 {sites} 악화"], sev)
        elif occupational and work_symptoms == YES:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name}{josa(name, '을를')} 일하면서 다루고, 일하는 날 증상이 심해지고 쉬는 날 좋아진다고 하셨습니다. "
                f"{name} 때문인지 직장의 다른 노출 때문인지는 문진만으로 가릴 수 없어 판정을 보류합니다. "
                f"→ 진료에서 하는 일을 알리고 직업과 관련된 알레르기인지 평가받으세요.")
        elif occupational and worse == NO:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"{name}{josa(name, '을를')} 일하면서 다루는데도 증상 악화가 없습니다. → 현재는 감작만 되어 있는 것으로 "
                f"보입니다. 다만 감작된 채 일하면서 노출이 이어지면 증상이 생길 수 있어, 직장에서 노출을 줄이고 "
                f"증상이 없더라도 정기적으로 확인받으세요.")
        elif level == "occasional" and worse == NO:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"{name}{josa(name, '과와')} 가끔 만날 때 증상 악화가 없습니다. → 현재는 감작만 되어 있는 것으로 보입니다. "
                f"접촉이 훨씬 잦아지거나 함께 살게 되면 달라질 수 있으니, 그때 증상이 생기는지 살펴보세요.")
        elif exposed and worse == NO:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"{name}{josa(name, '과와')} 접촉이 있는데도 증상 악화가 없습니다. → 현재는 감작만 되어 있는 것으로 "
                f"보이며, 반드시 분리·파양할 필요는 없습니다. 증상이 생기면 재평가하세요.")
        elif level == "rare" and not occupational:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name}{josa(name, '과와')} 접촉 경험이 거의 없어 실제 알레르기 여부를 판단하기 어렵습니다. "
                f"→ 새로 기르기 전 노출 시 증상을 관찰하는 것이 좋습니다.")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 접촉·증상 정보가 부족해 판정을 보류합니다.{work_tail}"

    def _classify_venom(self, a, answers):
        """벌독 — 쏘인 뒤 전신 반응이 있었을 때만 임상적으로 의미 있는 벌독 알레르기로 본다.

        쏘인 자리의 국소 반응은 독 자체의 작용이라 알레르기 근거가 아니고, 넓게 붓는 반응도
        그것만으로는 벌독 면역치료 대상이 아니다. 쏘인 적이 없으면 판정할 수 없다.
        답은 a.answers[Q_STING] 에 남긴다 — 벌독 면역치료 안내가 이 값을 보고 열린다.
        """
        name = _name(a)
        react = answers.get(Q_STING)
        if react in ("systemic", "large_local", "local", "never", UNSURE):
            a.answers[Q_STING] = react
        if react == "systemic":
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"벌에 쏘인 뒤 쏘인 자리를 벗어난 온몸 반응이 있었고, 검사에서 {name} 감작이 확인되었습니다. "
                "다시 쏘이면 심한 반응이 올 수 있습니다. 알레르기 전문의 평가를 받고, 응급 대처 계획과 "
                "에피네프린 자가주사기 처방 여부를 확인하세요.")
            sev = self._pick_severity(answers, QP_SEVERITY + "venom", default="severe")
            self._set_symptoms(a, ["벌에 쏘인 뒤 전신 반응(쏘인 자리를 벗어난 온몸 반응)"], sev)
            a.symptom_sites = ["systemic"]
        elif react == "large_local":
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                "벌에 쏘인 자리 주변이 넓게 부은 적은 있지만 온몸 반응은 없었습니다. 이 반응만으로는 "
                "벌독 알레르기(전신 반응)로 판단하지 않습니다. 다음에 쏘였을 때 온몸 두드러기·숨참·어지럼이 "
                "생기면 바로 응급실로 가세요.")
        elif react == "local":
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                "벌에 쏘였을 때 쏘인 자리만 붓고 아팠습니다. 이는 독 자체의 작용으로 누구에게나 생기는 반응이라 "
                "알레르기의 근거가 되지 않습니다. 검사 양성은 감작을 뜻할 뿐이며, 쏘인 뒤 온몸 반응이 생기면 "
                "바로 응급실로 가세요.")
        elif react == "never":
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                "검사는 양성(감작)이지만 벌에 쏘인 적이 없어 실제 벌독 알레르기인지 판단할 수 없습니다. "
                "쏘인 뒤 온몸 두드러기·숨참·어지럼이 생기면 바로 응급실로 가세요.")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                "벌에 쏘였을 때의 반응 정보가 부족해 판정을 보류합니다. 쏘인 적이 있다면 그때 쏘인 자리만 "
                "부었는지, 온몸에 반응이 있었는지를 진료에서 알려 주세요.")

    def _classify_shellfish(self, a, key, answers, has_mite=False):
        """갑각류(새우·게) — 양방향 감별: 강양성이어도 무증상이면 감작만."""
        name = _name(a)
        react = answers.get(QP_SHELLFISH + key)
        # 전신반응 게이트에서 이 음식을 지목했으면 전신으로 승격
        if key in _multi(answers.get(Q_FOOD_SYSTEMIC_FOODS)):
            react = "systemic"
        trop = " (집먼지진드기와의 트로포마이오신 교차반응 가능성도 함께 고려됩니다.)" if has_mite else ""
        if react == "systemic":
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 섭취 시 두드러기·호흡곤란 등 전신 증상이 있어, 임상적으로 의미 있는 갑각류 "
                f"알레르기로 판단됩니다. 섭취를 피하고 전문의 평가·응급계획이 필요합니다.{trop}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, QP_FOOD_SYSTEMIC_SEV + key, default="severe")
            self._set_symptoms(a, [f"{name} 섭취 시 전신 증상(두드러기·호흡곤란 등)"], sev)
        elif react == "oral":
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 섭취 시 입·목에 국소 증상이 있습니다(구강알레르기증후군형). 생물·많은 양에서 특히 "
                f"주의하고, 증상이 심해지면 전문의와 상의하세요.{trop}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="mild")
            self._set_symptoms(a, [f"{name} 섭취 시 입·목 국소 증상"], sev)
            a.symptom_sites = []          # 입·목 증상만 — 주증상 부위(피부·소화기·호흡기)에 잇지 않는다
        elif react == "none":
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성(때로 강양성)이지만 {name}{josa(name, '을를')} 실제로 문제없이 드시고 있습니다. → 감작만 된 "
                f"상태로, 불필요하게 갑각류를 끊을 필요가 없습니다. 새 증상이 생기면 재평가하세요.{trop}")
        else:  # never / 정보부족
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name}{josa(name, '을를')} 먹어본 경험·증상 정보가 부족해 판정을 보류합니다. 소량부터 섭취해 반응을 "
                f"관찰하되, 과거 전신 반응이 있었다면 전문의와 먼저 상의하세요.{trop}")

    def _classify_food_symptoms(self, a, key, answers, food_systemic):
        """일반 음식 — 증상 유형(다중)으로 판정·중증도 구분."""
        name = _name(a)
        syms = set(_multi(answers.get(QP_FOOD_SYMPTOMS + key)))
        systemic_syms = syms & {"skin", "gi", "breathing", "anaphylaxis"}
        # 전신반응 게이트에서 이 음식을 지목했는지
        picked_systemic = key in _multi(answers.get(Q_FOOD_SYSTEMIC_FOODS))
        manifest = [self._FOOD_SYMPTOM_TEXT[s] for s in
                    ("oral", "skin", "gi", "breathing", "anaphylaxis") if s in syms]
        if "none" in syms and not (syms - {"none"}) and not picked_systemic:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 {name}{josa(name, '을를')} 현재 문제없이 섭취하고 있습니다. → 감작만 된 상태로 보이며, "
                f"불필요한 식이 제한은 오히려 해로울 수 있습니다.")
        elif systemic_syms or food_systemic == YES or picked_systemic:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            severe = ("anaphylaxis" in syms) or ("breathing" in syms) or picked_systemic
            systemic_wording = severe or (food_systemic == YES)
            note = " 전신 반응(호흡곤란·아나필락시스) 병력은 응급 위험이 있어 반드시 전문의 평가와 응급계획이 필요합니다." if severe else ""
            a.rationale_ko = (
                f"{name} 섭취 시 {'전신 ' if systemic_wording else ''}알레르기 증상이 재현되어 임상적으로 의미 있는 "
                f"음식 알레르기로 판단됩니다.{note}")
            default_sev = "severe" if severe else "moderate"
            sev = self._pick_severity(answers, QP_SEVERITY + key, QP_FOOD_SYSTEMIC_SEV + key, default=default_sev)
            self._set_symptoms(a, manifest or [f"{name} 섭취 시 전신 알레르기 증상"], sev)
            # 어떤 증상이었는지 고른 경우에만 부위를 정한다. '전신 반응이 있었다'만 답했으면 부위를 알 수 없다(None).
            a.symptom_sites = _sites_of(syms) if manifest else None
        elif "oral" in syms:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 섭취 시 입·목에 국소 증상이 있습니다(구강알레르기증후군형). 생물에서 특히 주의하고, "
                f"대개 가열 시 증상이 줄어듭니다.")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="mild")
            self._set_symptoms(a, manifest or [f"{name} 섭취 시 입·목 국소 증상"], sev)
            a.symptom_sites = []
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 섭취 경험·증상 정보가 부족해 판정을 보류합니다."

    def _classify_other(self, a, key, answers, pattern):
        """미분류(other) 양성 항원 — 범용 노출·섭취 증상으로 판정(Q-1: 질문 누락 차단)."""
        name = _name(a)
        how = "접촉" if _cat(a) == "latex" else "노출/섭취"   # 라텍스는 닿아서 들어온다
        syms = set(_multi(answers.get(QP_OTHER_SYMPTOM + key)))
        real = syms & {"oral", "skin", "gi", "breathing", "anaphylaxis"}
        if not syms:
            return self._classify_generic(a, pattern)
        if "none" in syms and not real:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 {name} {how} 시 증상이 없어 감작만 된 상태로 보입니다. "
                f"과도한 회피는 불필요하며 새 증상이 생기면 재평가하세요.")
        elif real:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            severe = bool(syms & {"breathing", "anaphylaxis"})
            manifest = [self._FOOD_SYMPTOM_TEXT[s] for s in
                        ("oral", "skin", "gi", "breathing", "anaphylaxis") if s in syms]
            note = " 전신 반응 병력은 응급 위험이 있어 반드시 전문의 평가가 필요합니다." if severe else ""
            a.rationale_ko = (f"{name} {how} 시 알레르기 증상이 재현되어 임상적으로 의미 있는 것으로 "
                              f"판단됩니다.{note}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="severe" if severe else "moderate")
            self._set_symptoms(a, manifest or [f"{name} {how} 시 증상"], sev)
            # 닿거나 먹었을 때 '어느 부위에' 증상이 있었는지 — 주증상별 안내가 이 값으로 알러젠을 잇는다
            a.symptom_sites = _sites_of(syms, _LATEX_SYMPTOM_SITES if _cat(a) == "latex" else _SYMPTOM_SITES)
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 노출·증상 정보가 부족해 판정을 보류합니다."

    def _classify_drug(self, a, key, answers, screening):
        """약물 항원 — 이 앱은 판정하지 않는다. 언제나 '진료 확인 필요'(CLINICIAN_REVIEW)다.

        약물 특이 IgE 양성은 그것만으로 약물 알레르기의 근거가 되지 않고, 그 약을 피할지·다시 써도 되는지는
        진료에서 정한다. 예전에는 쓴 뒤 반응이 있었다고 답하면 '실제 주의'(카드뉴스의 '진범 확정'·'회피와 관리가
        가장 중요합니다'), 반응이 없었다고 답하면 '감작만'('무혐의'·'과도한 회피 불필요')으로 넣어, 같은 문서의
        '진료에서 정합니다'와 어긋났다. 환자가 말한 반응은 a.answers[A_DRUG_HISTORY]·reported_symptoms·severity
        에 그대로 남긴다 — 진료에서 볼 정보다. 어느 문장에도 회피·재투여 지시를 넣지 않는다."""
        name = _name(a)
        syms = set(_multi(answers.get(QP_OTHER_SYMPTOM + key)))
        real = syms & {"oral", "skin", "breathing", "anaphylaxis"}
        history = self._drug_history_sentence(screening)
        a.relevance = ClinicalRelevance.CLINICIAN_REVIEW
        a.answers[A_DRUG_HISTORY] = ("reaction" if real else "tolerated" if "none" in syms
                                     else "never" if "never" in syms else "unknown")
        if real:
            severe = bool(real & {"breathing", "anaphylaxis"})
            a.rationale_ko = (
                f"{name} 검사가 양성이고, 이 약을 쓴 뒤 반응이 있었다고 답하셨습니다. 약물 알레르기일 가능성이 있어 "
                f"진료에서 확인이 필요합니다. 이 약을 계속 피할지, 다시 써도 되는지는 진료에서 정합니다.{history}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="severe" if severe else "moderate")
            self._set_symptoms(a, [self._FOOD_SYMPTOM_TEXT[s] for s in ("oral", "skin", "breathing", "anaphylaxis")
                                   if s in real], sev)
            a.symptom_sites = _sites_of(real)
        elif "none" in syms:
            a.rationale_ko = (
                f"{name} 검사는 양성이지만 이 약을 문제없이 쓰셨습니다. 검사 양성만으로 약물 알레르기라고 하지 "
                f"않습니다. 이 결과만으로 약을 스스로 끊거나 피하지 말고, 진료에서 확인하세요.{history}")
        else:
            a.rationale_ko = (
                f"{name} 검사는 양성이지만 이 약을 쓴 뒤의 반응을 알 수 없습니다. 검사 양성만으로 약물 알레르기라고 "
                f"하지 않습니다. 약물 알레르기 여부와 이 약의 사용은 진료에서 확인하세요.{history}")

    def _classify_generic(self, a, pattern):
        a.relevance = ClinicalRelevance.INDETERMINATE
        a.rationale_ko = (
            "이 알러젠은 노출 상황과 증상의 관계를 좀 더 관찰한 뒤 재평가가 필요합니다.")


_questionnaire_engine: Optional[QuestionnaireEngine] = None


def get_questionnaire_engine() -> QuestionnaireEngine:
    global _questionnaire_engine
    if _questionnaire_engine is None:
        _questionnaire_engine = QuestionnaireEngine()
    return _questionnaire_engine
