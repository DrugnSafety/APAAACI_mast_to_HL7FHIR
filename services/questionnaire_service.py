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
       - 동물           : 그 동물 접촉/사육 여부, 접촉 증가 시 증상 악화 여부
       - 음식           : 그 음식 섭취 시 증상 재현 여부

  이렇게 모은 '그룹 답변'을 알러젠별 특성과 대조하여
  clinically_relevant / sensitized_only / indeterminate 로 판정한다.

산출물(build):  UI 렌더링용 dict (sections + allergen_index)
판정(classify): RelevanceAssessmentResult 의 각 assessment.relevance/rationale_ko 갱신
"""

import logging
from typing import Any, Dict, List, Optional

from models.schemas import (
    RelevanceAssessmentResult,
    AllergenAssessment,
    ClinicalRelevance,
    ScreeningProfile,
    SymptomSeasonPattern,
)
from services.knowledge_service import normalize_category, get_knowledge_service

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
    {"value": "none", "label": "먹어도 증상 없음"},
]
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
    {"value": "none", "label": "특별히 그런 공간은 없어요"},
]
MOLD_SPACE_SPECIFIC = {"bathroom", "basement", "water_damage", "aircon"}

# 실외 곰팡이(얼터나리아·클라도스포리움) 단서
MOLD_OUTDOOR_OPTIONS = [
    {"value": "leaves", "label": "낙엽 더미·풀 깎기·잔디밭"},
    {"value": "soil", "label": "퇴비·흙·화분을 다룰 때"},
    {"value": "rain", "label": "비 온 직후·천둥번개가 친 뒤"},
    {"value": "farm", "label": "농장·창고·건초·곡물 주변"},
    {"value": "none", "label": "해당 없음"},
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
# 동적 id 접두사
QP_POLLEN = "pollen_season__"           # + group(spring/summer_grass/fall)
QP_ANIMAL_CONTACT = "animal_contact__"  # + key
QP_ANIMAL_WORSE = "animal_worse__"      # + key
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
                {"value": "none", "label": "계절 차이 없음"},
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
                    "title": f"{nm}을(를) 키우거나 자주 접촉하나요?",
                    "options": [
                        {"value": YES, "label": "예 (키우거나 자주 접촉)"},
                        {"value": NO, "label": "아니오 (거의 접촉 없음)"},
                    ],
                    "applies_to": [_key(i)],
                })
                animal_qs.append({
                    "id": QP_ANIMAL_WORSE + _key(i),
                    "type": "single",
                    "title": f"{nm}과(와) 접촉이 늘면 코·눈·피부·호흡기 증상이 심해지나요?",
                    "help": "접촉 직후~수십 분 내 증상이 생기는지 떠올려 보세요.",
                    "options": YNU,
                    "applies_to": [_key(i)],
                })
                animal_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_ANIMAL_WORSE + _key(i), "any": [YES]}},
                    title=f"{nm} 접촉 시 증상이 있을 때, 가장 심했던 정도는?"))
            sections.append({
                "id": "animal",
                "title": "동물 알러젠",
                "subtitle": "접촉 여부와 접촉 시 증상 변화를 확인합니다.",
                "questions": animal_qs,
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
                    "title": f"{nm}을(를) 실제로 먹었을 때 어떤가요?",
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
                    "title": f"{nm}을(를) 먹으면 어떤 증상이 생기나요?",
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
                    title=f"{nm}로 인한 전신 증상은 어느 정도였나요?"))
            sections.append({
                "id": "food",
                "title": "음식·구강 알레르기 · 교차반응",
                "subtitle": "먹었을 때의 반응으로 실제 음식 알레르기와 교차반응을 가립니다.",
                "questions": food_qs,
            })

        # ---- 섹션 7: 기타(미분류) 양성 항원 — 질문 누락 원천 차단(Q-1) ----
        HANDLED = set(POLLEN_GROUPS) | {"mite", "insect", "mold", "animal", "food"}
        others = [(i, a) for i, a in enumerate(assessments)
                  if _cat(a) not in HANDLED and _cat(a) != "control"]
        if others:
            other_qs = []
            for i, a in others:
                nm = _name(a)
                other_qs.append({
                    "id": QP_OTHER_SYMPTOM + _key(i), "type": "multi",
                    "title": f"「{nm}」에 노출되거나(음식이면 드셨을 때) 어떤 증상이 있나요?",
                    "help": "이 항원은 자동 분류가 어려워 직접 여쭤봅니다. 여러 개 선택 가능. 문제없으면 ‘증상 없음’.",
                    "options": FOOD_SYMPTOM_OPTIONS, "applies_to": [_key(i)],
                })
                other_qs.append(_severity_question(
                    QP_SEVERITY + _key(i), [_key(i)],
                    {"reveal_if": {"question": QP_OTHER_SYMPTOM + _key(i),
                                   "includes_any": ["oral", "skin", "gi", "breathing", "anaphylaxis"]}},
                    title=f"{nm} 노출/섭취 시 증상이 있을 때, 가장 심했던 정도는?"))
            sections.append({
                "id": "other",
                "title": "기타 항원",
                "subtitle": "자동 분류가 어려운 양성 항원도 빠짐없이 노출·증상을 확인합니다.",
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
        if cat == "other":
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
            merged: Dict[str, str] = {}
            for _, a in grp["members"]:
                # 꽃가루: 큐레이션 PFAS 를 먼저(임상 특이 증후군 보존)
                if _cat(a) in POLLEN_GROUPS:
                    for f in (ks.pfas_foods_for(a.allergen_name, _cat(a)) or {}).get("foods", []):
                        if f.get("en"):
                            merged.setdefault(f["en"], f.get("ko") or f["en"])
                for c in svc.candidate_foods(a.allergen_name, a.korean_name or "",
                                             exclude_names=positive_names, limit=8):
                    merged.setdefault(c["name"], c["korean"] or c["name"])
            if not merged:
                continue
            cands = [{"name": en, "korean": ko} for en, ko in list(merged.items())[:12]]
            label = grp["label"] if grp["grouped"] else _name(lead)
            specs.append({"lead_i": lead_i, "lead": lead, "label": label,
                          "keys": keys, "cands": cands, "is_pollen": is_pollen,
                          "group_note": grp["note"]})
        return specs

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
            opts.append({"value": "none", "label": "해당 없음 / 문제된 것 없음"})
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
                "help": f"{grp_note}{label}와(과) 같은 단백질(성분)을 공유해 {kind}이 나타날 수 있는 음식입니다. "
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
                       + [{"value": "none", "label": "해당 없음"}],
            "applies_to": [],
        })

    def _group_selected(self, assessments, answers):
        """임상그룹별 교차반응 선택 결과를 모은다(문항이 그룹 대표 key 로 생성되므로).
        반환: [{spec, selected:[en], severity}] — catch-all 재활성(Q-5) 병합 포함."""
        answers = answers or {}
        general = [v for v in _multi(answers.get(Q_FOOD_GENERAL)) if v and v != "none"]
        out = []
        for sp in self._crossreact_specs(assessments):
            qid = QP_CROSSREACT + _key(sp["lead_i"])
            sel = [v for v in _multi(answers.get(qid)) if v and v != "none"]
            cand_names = {c["name"] for c in sp["cands"]}
            if general:  # 마무리 catch-all 에서 이 그룹 후보를 지목 → 재활성/병합
                sel = list(dict.fromkeys(sel + [g for g in general if g in cand_names]))
            if not sel:
                continue
            sev = answers.get(QP_CROSSREACT_SEV + _key(sp["lead_i"]))
            # 전신 음식반응 게이트에서 전신/아나필락시스로 답했으면 승격
            if sev not in ("oral", "systemic", "anaphylaxis"):
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
                if species and species in pets:
                    pre[QP_ANIMAL_CONTACT + _key(i)] = YES
                elif "none" in pets:
                    pre[QP_ANIMAL_CONTACT + _key(i)] = NO
        return pre

    # ============================================================
    # 2) 판정
    # ============================================================
    def classify(
        self,
        result: RelevanceAssessmentResult,
        answers: Dict[str, Any],
        screening: Optional[ScreeningProfile] = None,
    ) -> RelevanceAssessmentResult:
        answers = answers or {}
        pattern = answers.get(Q_PATTERN)
        worse_seasons = set(answers.get(Q_SEASONS, []) or [])
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
                self._classify_pollen(a, cat, answers, worse_months, oas, oas_foods_here)
            elif cat == "mite":
                self._classify_indoor(a, "mite", indoor_timing, indoor_away, mite_dust, pattern, answers)
            elif cat == "insect":
                self._classify_indoor(a, "insect", indoor_timing, indoor_away, roach_env, pattern, answers)
            elif cat == "mold":
                self._classify_mold(a, mold_damp, worse_months, pattern, answers,
                                    has_mite=has_mite_pos)
            elif cat == "animal":
                self._classify_animal(a, _key(i), answers)
            elif cat == "food":
                if ks.is_shellfish(a.allergen_name, a.korean_name):
                    self._classify_shellfish(a, _key(i), answers, has_mite="mite" in [_cat(x) for x in result.assessments])
                else:
                    self._classify_food_symptoms(a, _key(i), answers, food_systemic)
            else:
                self._classify_other(a, _key(i), answers, pattern)
        # 성분 교차반응을 confirmed(증상 확인) / risk(가능성)로 나눠 각 유발 항원에 전파
        self._populate_crossreact(result.assessments, answers)
        return result

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
        general = set(v for v in _multi(answers.get(Q_FOOD_GENERAL)) if v and v != "none")
        for sp in self._crossreact_specs(assessments):
            qid = QP_CROSSREACT + _key(sp["lead_i"])
            cr_sev = answers.get(QP_CROSSREACT_SEV + _key(sp["lead_i"]))
            sel = set(v for v in _multi(answers.get(qid)) if v and v != "none")
            # 마무리 catch-all 에서 이 그룹의 후보로 지목된 음식도 confirmed 로 병합(재활성 Q-5)
            sel |= (general & {c["name"] for c in sp["cands"]})
            confirmed, risk = [], []
            for c in sp["cands"]:
                ko = c["korean"] or c["name"]
                (confirmed if c["name"] in sel else risk).append(ko)
            # 그룹의 모든 멤버 항원에 동일하게 기록(Df/Dp 중복 서술 방지는 리포트 단계에서 처리)
            for idx, a in enumerate(assessments):
                if _key(idx) in sp["keys"]:
                    oas_ko = set(a.oas_foods or [])
                    a.crossreact_confirmed = [x for x in confirmed if x not in oas_ko]
                    a.crossreact_risk = [x for x in risk if x not in oas_ko]
                    if confirmed or oas_ko:
                        a.crossreact_severity = cr_sev if cr_sev in (
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
    def _classify_pollen(self, a, cat, answers, worse_months, oas, oas_foods=None):
        g = POLLEN_GROUPS[cat]
        season_ans = answers.get(QP_POLLEN + g["group"])
        peak = set((a.kb or {}).get("peak_months_korea", []) or g["months"])
        overlap = bool(peak & worse_months) if worse_months else None
        name = _name(a)
        oas_note = ""
        if oas_foods:
            oas_note = (f" 또한 {name}와 교차반응으로 **{', '.join(oas_foods)}** 섭취 시 입·목 증상"
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
                f"{g['season_ko']}에 증상이 실제로 악화되어, 검사 양성이 임상적으로 의미 있는 "
                f"꽃가루 알레르기로 판단됩니다. 해당 시즌 외출·환기 관리가 중요합니다.{oas_note}")
            sev = self._pick_severity(answers, QP_SEVERITY + "pollen_" + g["group"], default="moderate")
            self._set_symptoms(a, [f"{g['season_ko']} 코·눈 증상 악화(재채기·콧물·코막힘·눈 가려움)"], sev)
        elif verdict == "sensitized":
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성(감작)이지만 {g['season_ko']}에 증상 악화가 뚜렷하지 않습니다. "
                f"→ 현재는 감작만 되어 있고 실제 증상은 유발하지 않는 것으로 보이며, 과도한 회피는 "
                f"필요하지 않습니다.{oas_note}")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{g['season_ko']} 증상 변화 정보가 부족해 판정을 보류합니다. 다음 시즌에 증상 "
                f"악화 여부를 관찰해 보세요.{oas_note}")

    def _classify_indoor(self, a, kind, timing, away, specific, pattern, answers=None):
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
            self._set_symptoms(a, [f"{label} 실내 노출 시 코·눈·호흡기 증상 악화({cues})"], sev)
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

    def _classify_mold(self, a, damp, worse_months, pattern, answers=None, has_mite=False):
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
            self._set_symptoms(a, ["곰팡이 노출 시 코·호흡기 증상 악화(" + ", ".join(cues) + ")"], sev)
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
            self._set_symptoms(a, ["습한 환경·곰팡이 노출 시 코·호흡기 증상 악화"], sev)
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

    def _classify_animal(self, a, key, answers):
        name = _name(a)
        contact = answers.get(QP_ANIMAL_CONTACT + key)
        worse = answers.get(QP_ANIMAL_WORSE + key)
        if worse == YES:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 접촉이 늘 때 증상이 악화되어, 임상적으로 의미 있는 동물 알레르기로 판단됩니다. "
                f"접촉을 줄이고 침실 등 생활공간 노출을 관리하세요.")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="moderate")
            self._set_symptoms(a, [f"{name} 접촉 시 코·눈·피부·호흡기 증상 악화"], sev)
        elif contact == YES and worse == NO:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"{name}과(와) 접촉이 있는데도 증상 악화가 없습니다. → 현재는 감작만 되어 있는 것으로 "
                f"보이며, 반드시 분리·파양할 필요는 없습니다. 증상이 생기면 재평가하세요.")
        elif contact == NO:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name}과(와) 접촉 경험이 거의 없어 실제 알레르기 여부를 판단하기 어렵습니다. "
                f"→ 새로 기르기 전 노출 시 증상을 관찰하는 것이 좋습니다.")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 접촉·증상 정보가 부족해 판정을 보류합니다."

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
        elif react == "none":
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성(때로 강양성)이지만 {name}을(를) 실제로 문제없이 드시고 있습니다. → 감작만 된 "
                f"상태로, 불필요하게 갑각류를 끊을 필요가 없습니다. 새 증상이 생기면 재평가하세요.{trop}")
        else:  # never / 정보부족
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name}을(를) 먹어본 경험·증상 정보가 부족해 판정을 보류합니다. 소량부터 섭취해 반응을 "
                f"관찰하되, 과거 전신 반응이 있었다면 전문의와 먼저 상의하세요.{trop}")

    def _classify_food_symptoms(self, a, key, answers, food_systemic):
        """일반 음식 — 증상 유형(다중)으로 판정·중증도 구분."""
        name = _name(a)
        syms = set(answers.get(QP_FOOD_SYMPTOMS + key, []) or [])
        systemic_syms = syms & {"skin", "gi", "breathing", "anaphylaxis"}
        # 전신반응 게이트에서 이 음식을 지목했는지
        picked_systemic = key in _multi(answers.get(Q_FOOD_SYSTEMIC_FOODS))
        manifest = [self._FOOD_SYMPTOM_TEXT[s] for s in
                    ("oral", "skin", "gi", "breathing", "anaphylaxis") if s in syms]
        if "none" in syms and not (syms - {"none"}) and not picked_systemic:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 {name}을(를) 현재 문제없이 섭취하고 있습니다. → 감작만 된 상태로 보이며, "
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
        elif "oral" in syms:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                f"{name} 섭취 시 입·목에 국소 증상이 있습니다(구강알레르기증후군형). 생물에서 특히 주의하고, "
                f"대개 가열 시 증상이 줄어듭니다.")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="mild")
            self._set_symptoms(a, manifest or [f"{name} 섭취 시 입·목 국소 증상"], sev)
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 섭취 경험·증상 정보가 부족해 판정을 보류합니다."

    def _classify_other(self, a, key, answers, pattern):
        """미분류(other) 양성 항원 — 범용 노출·섭취 증상으로 판정(Q-1: 질문 누락 차단)."""
        name = _name(a)
        syms = set(answers.get(QP_OTHER_SYMPTOM + key, []) or [])
        real = syms & {"oral", "skin", "gi", "breathing", "anaphylaxis"}
        if not syms:
            return self._classify_generic(a, pattern)
        if "none" in syms and not real:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 {name} 노출/섭취 시 증상이 없어 감작만 된 상태로 보입니다. "
                f"과도한 회피는 불필요하며 새 증상이 생기면 재평가하세요.")
        elif real:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            severe = bool(syms & {"breathing", "anaphylaxis"})
            manifest = [self._FOOD_SYMPTOM_TEXT[s] for s in
                        ("oral", "skin", "gi", "breathing", "anaphylaxis") if s in syms]
            note = " 전신 반응 병력은 응급 위험이 있어 반드시 전문의 평가가 필요합니다." if severe else ""
            a.rationale_ko = (f"{name} 노출/섭취 시 알레르기 증상이 재현되어 임상적으로 의미 있는 것으로 "
                              f"판단됩니다.{note}")
            sev = self._pick_severity(answers, QP_SEVERITY + key, default="severe" if severe else "moderate")
            self._set_symptoms(a, manifest or [f"{name} 노출/섭취 시 증상"], sev)
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = f"{name} 노출·증상 정보가 부족해 판정을 보류합니다."

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
