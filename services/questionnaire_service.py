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
Q_OAS_FOODS = "oas_foods"               # 교차반응으로 증상 유발하는 음식(다중) - 양성 꽃가루 기반
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
Q_OAS_SYSTEMIC = "oas_systemic"         # OAS에서 아나필락시스/전신 두드러기 발생 여부
Q_FOOD_SYSTEMIC_FOODS = "food_systemic_foods"  # 전신반응 유발 음식(다중)
# 동적 id 접두사
QP_POLLEN = "pollen_season__"           # + group(spring/summer_grass/fall)
QP_ANIMAL_CONTACT = "animal_contact__"  # + key
QP_ANIMAL_WORSE = "animal_worse__"      # + key
QP_FOOD_REACT = "food_react__"          # + key
QP_SEVERITY = "severity__"              # + key/scope : 증상 중증도(경증/중등증/중증/아나필락시스)
QP_FOOD_SYSTEMIC_SEV = "food_systemic_sev__"   # + key : 전신반응 음식별 중증도
QP_CROSSREACT = "crossreact__"          # + key : 성분기반 교차반응 음식(다중) — 데이터 파생

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
                indoor_qs.append({
                    "id": Q_MOLD_DAMP,
                    "type": "single",
                    "title": "장마철·습한 곳·곰팡이가 보이는 공간에서 증상이 심해지나요?",
                    "options": YNU,
                    "applies_to": [_key(i) for i, a in enumerate(assessments) if _cat(a) == "mold"],
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
        if foods or pollen_oas or has_mite:
            food_qs = []
            # (1) 꽃가루 OAS 게이트
            if pollen_oas or foods:
                food_qs.append({
                    "id": Q_OAS, "type": "single",
                    "title": "생과일·생채소·견과를 먹으면 입·입술·혀·목이 가렵거나 붓나요?",
                    "help": "구강알레르기증후군(OAS)일 수 있습니다. 꽃가루 알레르기와 관련이 깊습니다.",
                    "options": YNU, "applies_to": [],
                })
                pfas_opts, _ = self._pfas_options(assessments)
                if pfas_opts:
                    food_qs.append({
                        "id": Q_OAS_FOODS, "type": "multi",
                        "title": "그렇다면, 아래 음식 중 먹었을 때 입·목 증상이 생기는 것을 모두 선택하세요.",
                        "help": "양성으로 나온 꽃가루와 교차반응이 알려진 음식들입니다. 실제로 증상이 있었던 것만 고르세요. "
                                "(대부분 익히면 괜찮아지지만, 견과·콩·셀러리 등은 전신 반응 가능성도 있어 주의)",
                        "options": pfas_opts + [{"value": "none", "label": "해당 없음 / 문제된 음식 없음"}],
                        "applies_to": [], "reveal_if": {"question": Q_OAS, "equals": YES},
                    })
                    # OAS 에서 아나필락시스/전신 두드러기 발생 여부 — 대개 국소지만 견과·콩·셀러리 등은 전신 가능
                    food_qs.append({
                        "id": Q_OAS_SYSTEMIC, "type": "single",
                        "title": "위 음식으로 입·목을 넘어 전신 두드러기·호흡곤란·어지럼(아나필락시스)이 있었던 적이 있나요?",
                        "help": "대부분의 구강알레르기증후군은 입·목에 국한되지만, 일부(견과·콩·셀러리 등)는 전신 반응이 올 수 있어 확인합니다.",
                        "options": [
                            {"value": "no", "label": "아니오 — 입·입술·목 증상만 있었어요"},
                            {"value": "systemic", "label": "예 — 전신 두드러기가 있었어요"},
                            {"value": "anaphylaxis", "label": "예 — 호흡곤란·어지럼 등 아나필락시스가 있었어요"},
                        ],
                        "applies_to": [], "reveal_if": {"question": Q_OAS, "equals": YES},
                    })
            # (2) 환경 항원(진드기·바퀴 등) → 공유 성분(트로포마이오신 등) 기반 음식 교차반응
            #     (데이터 파생 — 하드코딩 진드기↔갑각류 특수 블록을 성분 엔진으로 대체.
            #      갑각류가 이미 양성이면 후보에서 제외되어 자동으로 묻지 않는다.)
            for i, a in enumerate(assessments):
                if _cat(a) in ("mite", "insect"):
                    self._append_crossreact_q(food_qs, a, _key(i), assessments)
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
                self._append_crossreact_q(food_qs, a, _key(i), assessments)
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
                self._append_crossreact_q(food_qs, a, _key(i), assessments)
            # (5) 전신 반응 게이트 → 유발 음식(다중) + 음식별 중증도
            food_qs.append({
                "id": Q_FOOD_SYSTEMIC, "type": "single",
                "title": "특정 음식을 먹은 뒤 두드러기·호흡곤란·복통 등 전신 증상이 있었나요?",
                "help": "전신 반응(아나필락시스 포함)은 응급 상황일 수 있어 반드시 확인합니다.",
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
        """양성 꽃가루들의 교차반응 음식을 모아 (다중선택 옵션, en->꽃가루 연결맵) 반환."""
        ks = get_knowledge_service()
        seen = {}
        link = {}
        for a in assessments:
            if _cat(a) not in POLLEN_GROUPS:
                continue
            canonical = a.allergen_name
            pf = ks.pfas_foods_for(canonical, _cat(a))
            for food in pf.get("foods", []):
                en, ko = food.get("en"), food.get("ko")
                if not en:
                    continue
                if en not in seen:
                    seen[en] = {"value": en, "label": ko or en}
                link.setdefault(en, [])
                pname = _name(a)
                if pname not in link[en]:
                    link[en].append(pname)
        return list(seen.values()), link

    def oas_selected_foods(self, assessments, answers):
        """OAS='예'이고 사용자가 고른 교차반응 음식 목록을 반환.
        반환: [{"en","ko","pollens":[...]}]"""
        if (answers or {}).get(Q_OAS) != YES:
            return []
        selected = [v for v in (answers.get(Q_OAS_FOODS, []) or []) if v and v != "none"]
        if not selected:
            return []
        opts, link = self._pfas_options(assessments)
        label = {o["value"]: o["label"] for o in opts}
        return [{"en": v, "ko": label.get(v, v), "pollens": link.get(v, [])} for v in selected]

    def _append_crossreact_q(self, food_qs, a, key, assessments):
        """양성 음식 항원에 대해 성분(component) 공유 교차반응 음식 문항을 데이터에서 생성.
        (하드코딩 대신 crossreactivity_service 파생. 이미 양성인 음식은 제외 — 직접 문진됨.)"""
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            return
        if not svc.has_data():
            return
        positive_names = set()
        for x in assessments:
            positive_names.add(x.allergen_name)
            if x.korean_name:
                positive_names.add(x.korean_name)
        cands = svc.candidate_foods(a.allergen_name, a.korean_name or "",
                                    exclude_names=positive_names, limit=8)
        if not cands:
            return
        nm = _name(a)
        risk = svc.worst_risk(a.allergen_name, a.korean_name or "")
        warn = ("이 중 일부는 전신 반응(두드러기·호흡곤란) 위험이 있어 특히 주의가 필요합니다. "
                if risk in ("systemic", "raw_systemic") else "대개 입·목 증상이지만 개인차가 있습니다. ")
        opts = [{"value": c["name"], "label": c["korean"] or c["name"]} for c in cands]
        opts.append({"value": "none", "label": "해당 없음 / 문제된 것 없음"})
        food_qs.append({
            "id": QP_CROSSREACT + key, "type": "multi",
            "title": f"{nm}와(과) 교차반응이 알려진 아래 음식 중, 드셨을 때 증상이 있었던 것을 모두 고르세요.",
            "help": f"{nm}와 같은 단백질(성분)을 공유해 교차반응할 수 있는 음식들입니다. {warn}"
                    f"실제로 증상이 있었던 것만 고르세요(감작만이면 불필요한 제한은 피합니다).",
            "options": opts, "applies_to": [key],
        })

    def component_crossreact_items(self, assessments, answers):
        """성분기반 교차반응 문항에서 '증상 있음'으로 선택된 음식을 FHIR 매핑용 항목으로 반환.
        반환: [{en, ko, source, severity, trigger}]"""
        answers = answers or {}
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
        except Exception:
            return []
        items, seen = [], set()
        oas_sys = answers.get(Q_OAS_SYSTEMIC)
        for i, a in enumerate(assessments):
            sel = [v for v in (answers.get(QP_CROSSREACT + _key(i), []) or []) if v and v != "none"]
            if not sel:
                continue
            trig = _name(a)
            risk = svc.worst_risk(a.allergen_name, a.korean_name or "")
            sev = "systemic" if risk in ("systemic", "raw_systemic") else "oral"
            if oas_sys == "anaphylaxis":
                sev = "anaphylaxis"
            for en in sel:
                rec = svc.find(en)
                ko = (rec.get("korean_name") if rec else None) or en
                key = en.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append({"en": en, "ko": ko, "source": "component",
                              "severity": sev, "pollens": [], "trigger": trig})
        return items

    def crossreactive_food_items(self, assessments, answers):
        """FHIR 매핑용 교차반응 음식 통합 목록 (꽃가루 OAS + 진드기↔갑각류).
        반환: [{"en","ko","source","severity","pollens"?}]
        severity: oral(국소)/systemic(전신 두드러기)/anaphylaxis — OAS 전신 게이트 반영"""
        items = []
        answers = answers or {}
        oas_sys = answers.get(Q_OAS_SYSTEMIC)  # no / systemic / anaphylaxis
        oas_sev = "anaphylaxis" if oas_sys == "anaphylaxis" else ("systemic" if oas_sys == "systemic" else "oral")
        for f in self.oas_selected_foods(assessments, answers):
            items.append({**f, "source": "pollen", "severity": oas_sev})
        # 진드기↔갑각류 등 환경↔음식 교차반응은 이제 성분(component) 엔진이 처리(아래 통합)
        # 성분(component) 기반 교차반응(데이터 파생) — 셀러리↔당근, 새우↔게, 진드기↔갑각류, 우유↔소고기 등
        seen = {(_norm_key(it.get("en")), _norm_key(it.get("ko"))) for it in items}
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
                self._classify_mold(a, mold_damp, worse_months, pattern, answers)
            elif cat == "animal":
                self._classify_animal(a, _key(i), answers)
            elif cat == "food":
                if ks.is_shellfish(a.allergen_name, a.korean_name):
                    self._classify_shellfish(a, _key(i), answers, has_mite="mite" in [_cat(x) for x in result.assessments])
                else:
                    self._classify_food_symptoms(a, _key(i), answers, food_systemic)
            else:
                self._classify_generic(a, pattern)
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
        pos_names = set()
        for x in assessments:
            pos_names.add(x.allergen_name)
            if x.korean_name:
                pos_names.add(x.korean_name)
        for i, a in enumerate(assessments):
            cands = svc.candidate_foods(a.allergen_name, a.korean_name or "",
                                        exclude_names=pos_names, limit=8)
            if not cands:
                continue
            sel = set(v for v in (answers.get(QP_CROSSREACT + _key(i), []) or []) if v and v != "none")
            # OAS(꽃가루-음식)로 이미 기록된 음식은 중복 표기하지 않음
            oas_ko = set(a.oas_foods or [])
            confirmed, risk = [], []
            for c in cands:
                ko = c["korean"] or c["name"]
                if ko in oas_ko:
                    continue
                (confirmed if c["name"] in sel else risk).append(ko)
            a.crossreact_confirmed = confirmed
            a.crossreact_risk = risk

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
        strong = any(x == YES for x in (timing, away, specific))
        all_no = all(x == NO for x in (timing, away, specific) if x is not None) and \
            any(x is not None for x in (timing, away, specific))
        label = "집먼지진드기" if kind == "mite" else "바퀴 등 실내 곤충"
        if strong:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            trg = []
            if timing == YES:
                trg.append("저녁·새벽·이른 아침 악화")
            if away == YES:
                trg.append("집을 비우면 호전")
            if specific == YES:
                trg.append("먼지·해당 환경 노출 시 악화" if kind == "mite" else "해당 환경 노출 시 악화")
            a.rationale_ko = (
                f"{('·'.join(trg)) or '노출 시 악화'} 패턴이 확인되어, {label} 알레르기가 실제 증상의 "
                f"원인으로 작용하는 것으로 판단됩니다. 침구·실내 환경 관리가 핵심입니다.")
            sev = self._pick_severity(answers or {}, QP_SEVERITY + "indoor", default="moderate")
            self._set_symptoms(a, [f"{label} 실내 노출 시 코·눈·호흡기 증상 악화(" + ("·".join(trg) or "노출 시 악화") + ")"], sev)
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

    def _classify_mold(self, a, damp, worse_months, pattern, answers=None):
        peak = set((a.kb or {}).get("peak_months_korea", []) or [7, 8, 9])
        overlap = bool(peak & worse_months) if worse_months else None
        if damp == YES or overlap is True:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                "습한 환경·곰팡이 노출 시 증상이 악화되어, 곰팡이 알레르기가 임상적으로 의미 있는 "
                "것으로 판단됩니다. 제습·환기·곰팡이 제거가 중요합니다.")
            sev = self._pick_severity(answers or {}, QP_SEVERITY + "indoor", default="moderate")
            self._set_symptoms(a, ["습한 환경·곰팡이 노출 시 코·호흡기 증상 악화"], sev)
        elif damp == NO or overlap is False:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                "검사는 양성이지만 습한 환경에서 증상 악화가 뚜렷하지 않습니다. → 현재는 감작 위주로 "
                "보이며 과도한 회피는 필요하지 않습니다.")
        else:
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = "습한 환경에서의 증상 변화 정보가 부족해 판정을 보류합니다."

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
        if key in (answers.get(Q_FOOD_SYSTEMIC_FOODS, []) or []):
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
        picked_systemic = key in (answers.get(Q_FOOD_SYSTEMIC_FOODS, []) or [])
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
