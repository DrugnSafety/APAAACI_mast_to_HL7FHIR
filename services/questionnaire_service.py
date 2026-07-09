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
from services.knowledge_service import normalize_category

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
Q_INDOOR_TIMING = "indoor_timing"       # 저녁/새벽/이른아침 악화
Q_INDOOR_AWAY = "indoor_away"           # 집 비우면 호전
Q_MITE_DUST = "mite_dust"               # 먼지·이불 정리 시 악화
Q_MOLD_DAMP = "mold_damp"               # 습한 곳 악화
Q_ROACH_ENV = "roach_env"               # 오래된 건물·주방
# 동적 id 접두사
QP_POLLEN = "pollen_season__"           # + group(spring/summer_grass/fall)
QP_ANIMAL_CONTACT = "animal_contact__"  # + key
QP_ANIMAL_WORSE = "animal_worse__"      # + key
QP_FOOD_REACT = "food_react__"          # + key

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
            sections.append({
                "id": "animal",
                "title": "동물 알러젠",
                "subtitle": "접촉 여부와 접촉 시 증상 변화를 확인합니다.",
                "questions": animal_qs,
            })

        # ---- 섹션 6: 음식 / 구강알레르기 ----
        if foods or pollen_oas:
            food_qs = [
                {
                    "id": Q_OAS,
                    "type": "single",
                    "title": "생과일·생채소·견과를 먹으면 입·입술·혀·목이 가렵거나 붓나요?",
                    "help": "구강알레르기증후군(OAS)일 수 있습니다. 꽃가루 알레르기와 관련이 깊습니다.",
                    "options": YNU,
                    "applies_to": [],
                },
                {
                    "id": Q_FOOD_SYSTEMIC,
                    "type": "single",
                    "title": "특정 음식을 먹은 뒤 두드러기·호흡곤란·복통 등 전신 증상이 있었나요?",
                    "help": "전신 반응(아나필락시스 포함)은 응급 상황일 수 있어 반드시 확인합니다.",
                    "options": YNU,
                    "applies_to": [],
                },
            ]
            for i, a in foods:
                nm = _name(a)
                food_qs.append({
                    "id": QP_FOOD_REACT + _key(i),
                    "type": "single",
                    "title": f"{nm}을(를) 먹으면 반복적으로 증상이 생기나요?",
                    "help": "현재 문제없이 먹고 있다면 ‘아니오’를 선택하세요.",
                    "options": [
                        {"value": YES, "label": "예, 먹으면 증상이 생겨요"},
                        {"value": NO, "label": "아니오, 문제없이 먹어요"},
                        {"value": UNSURE, "label": "먹어본 적 없음 / 잘 모르겠어요"},
                    ],
                    "applies_to": [_key(i)],
                })
            sections.append({
                "id": "food",
                "title": "음식·구강 알레르기",
                "subtitle": "먹었을 때의 반응으로 실제 음식 알레르기를 가립니다.",
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

        for i, a in enumerate(result.assessments):
            cat = _cat(a)
            # 답변 흔적을 assessment.answers 에도 남겨 리포트/디버깅에 활용
            if cat in POLLEN_GROUPS:
                self._classify_pollen(a, cat, answers, worse_months, oas)
            elif cat == "mite":
                self._classify_indoor(a, "mite", indoor_timing, indoor_away, mite_dust, pattern)
            elif cat == "insect":
                self._classify_indoor(a, "insect", indoor_timing, indoor_away, roach_env, pattern)
            elif cat == "mold":
                self._classify_mold(a, mold_damp, worse_months, pattern)
            elif cat == "animal":
                self._classify_animal(a, _key(i), answers)
            elif cat == "food":
                self._classify_food(a, _key(i), answers, oas, food_systemic)
            else:
                self._classify_generic(a, pattern)
        return result

    # ---- 카테고리별 판정 ----
    def _classify_pollen(self, a, cat, answers, worse_months, oas):
        g = POLLEN_GROUPS[cat]
        season_ans = answers.get(QP_POLLEN + g["group"])
        peak = set((a.kb or {}).get("peak_months_korea", []) or g["months"])
        overlap = bool(peak & worse_months) if worse_months else None
        name = _name(a)
        oas_note = ""
        if oas == YES and (a.kb or {}).get("oral_allergy_syndrome_ko"):
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

    def _classify_indoor(self, a, kind, timing, away, specific, pattern):
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

    def _classify_mold(self, a, damp, worse_months, pattern):
        peak = set((a.kb or {}).get("peak_months_korea", []) or [7, 8, 9])
        overlap = bool(peak & worse_months) if worse_months else None
        if damp == YES or overlap is True:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            a.rationale_ko = (
                "습한 환경·곰팡이 노출 시 증상이 악화되어, 곰팡이 알레르기가 임상적으로 의미 있는 "
                "것으로 판단됩니다. 제습·환기·곰팡이 제거가 중요합니다.")
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

    def _classify_food(self, a, key, answers, oas, food_systemic):
        name = _name(a)
        react = answers.get(QP_FOOD_REACT + key)
        if react == YES or food_systemic == YES:
            a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT
            sys_note = " 특히 전신 반응(두드러기·호흡곤란 등) 병력이 있다면 반드시 전문의 평가가 필요합니다." \
                if food_systemic == YES else ""
            a.rationale_ko = (
                f"{name} 섭취 시 증상이 재현되어 임상적으로 의미 있는 음식 알레르기로 판단됩니다.{sys_note}")
        elif react == NO:
            a.relevance = ClinicalRelevance.SENSITIZED_ONLY
            a.rationale_ko = (
                f"검사는 양성이지만 {name}을(를) 현재 문제없이 섭취하고 있습니다. → 감작만 된 상태로 "
                f"보이며, 불필요한 식이 제한은 오히려 해로울 수 있습니다.")
        else:
            oas_hint = ""
            if oas == YES:
                oas_hint = " 생것 섭취 시 입·목 증상(구강알레르기증후군) 여부도 함께 확인하세요."
            a.relevance = ClinicalRelevance.INDETERMINATE
            a.rationale_ko = (
                f"{name} 섭취 경험·증상 정보가 부족해 판정을 보류합니다.{oas_hint}")

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
