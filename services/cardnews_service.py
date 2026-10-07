"""
Card News Service — 주요 결과를 카드뉴스(공유용 시각 요약)로 생성

산출물: 인라인 CSS를 포함한 자체 완결 HTML (Streamlit 렌더링/다운로드 가능).
카드 구성:
  1) 표지        - 환자/검사일/핵심 한 줄
  2) 실제 주의   - 임상적으로 의미 있는 알러젠(진짜 알레르기)
  3) 감작만      - 감작은 됐지만 증상은 없는 알러젠(과도한 회피 불필요)
  4) 예방/관리   - 우선 실천 회피 수칙
  5) 마무리      - 정리 + 상담 권고
"""

import html
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from models.schemas import (
    RelevanceAssessmentResult,
    AllergenAssessment,
    ClinicalRelevance,
    ScreeningProfile,
)
from services.knowledge_service import get_knowledge_service
from utils.text_utils import trim_sentences
from services.exposure_guidance_service import (
    NO_RELEVANT_NOTE_KO, NO_RELEVANT_TIPS_KO, allocated_tips, animal_guidance, exposure_links)
from services import care_guidance_service as care_guidance

logger = logging.getLogger(__name__)

_CATEGORY_EMOJI = {
    "mite": "🛏️",
    "animal": "🐾",
    "pollen_tree": "🌳",
    "pollen_grass": "🌾",
    "pollen_weed": "🍂",
    "mold": "🍄",
    "insect": "🪳",
    "venom": "🐝",
    "food": "🍽️",
    "latex": "🧤",
    "drug": "💊",
    "control": "🧪",
    "other": "•",
}

# 카테고리 스탬프(라인 SVG) — web/game.js STAMPS 와 동일 소스. 변경 시 양쪽 함께 수정.
def _svg(inner: str) -> str:
    return ('<svg viewBox="0 0 40 40" fill="none" stroke="currentColor" stroke-width="2.2" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>')


_CATEGORY_STAMP_SVG = {
    "mite": _svg('<circle cx="20" cy="22" r="8"/><path d="M12 18l-5-4M28 18l5-4M11 24l-6 1M29 24l6 1M13 29l-4 4M27 29l4 4M17 20h6"/>'),
    "animal": _svg('<circle cx="20" cy="26" r="6"/><circle cx="11" cy="18" r="3"/><circle cx="17" cy="12" r="3"/><circle cx="23" cy="12" r="3"/><circle cx="29" cy="18" r="3"/>'),
    "pollen_tree": _svg('<path d="M20 35V24"/><path d="M11 24h18L20 8z"/><path d="M14 18h12"/>'),
    "pollen_grass": _svg('<path d="M20 35V13M14 35c0-8 2-13 4-17M26 35c0-8-2-13-4-17M9 35c0-5 2-9 5-11M31 35c0-5-2-9-5-11"/>'),
    "pollen_weed": _svg('<path d="M20 35V15"/><path d="M20 25c-7 0-10-5-11-11 6 0 10 4 11 11zM20 20c7 0 10-5 11-11-6 0-10 4-11 11z"/>'),
    "mold": _svg('<circle cx="15" cy="23" r="6"/><circle cx="25" cy="18" r="5"/><circle cx="25" cy="28" r="4"/><path d="M15 23h.01M25 18h.01"/>'),
    "insect": _svg('<ellipse cx="20" cy="22" rx="7" ry="10"/><path d="M13 17l-5-6M27 17l5-6M12 24H6M28 24h6M14 30l-4 5M26 30l4 5M20 12v20"/>'),
    "food": _svg('<circle cx="20" cy="22" r="10"/><circle cx="20" cy="22" r="4"/><path d="M6 12v8M34 12v8"/>'),
    "venom": _svg('<ellipse cx="20" cy="23" rx="7" ry="9"/><path d="M13.6 20h12.8M13.2 25h13.6M20 32v5M15 16c-6-1-9-5-8-9 5 0 8 3 9 7M25 16c6-1 9-5 8-9-5 0-8 3-9 7"/>'),
    "latex": _svg('<path d="M13 35V23l-4.6-6.4a2.2 2.2 0 0 1 3.6-2.6l2.6 3.4V8.5a2 2 0 0 1 4 0V17M18.6 17V6.5a2 2 0 0 1 4 0V17M22.6 17V8a2 2 0 0 1 4 0v10M26.6 18v-5.5a2 2 0 0 1 4 0V26c0 3.6-1.4 6.4-3.6 9M13 31h14"/>'),
    "drug": _svg('<path d="M10.6 29.4a7 7 0 0 1 0-9.9l8.9-8.9a7 7 0 0 1 9.9 9.9l-8.9 8.9a7 7 0 0 1-9.9 0zM15 15l10 10"/><path d="M22.5 12.5a3.5 3.5 0 0 1 4.5.5"/>'),
    "control": _svg('<circle cx="13" cy="20" r="8"/><path d="M13 16v8M9 20h8"/><circle cx="30" cy="20" r="6"/><path d="M27 20h6"/>'),
    "other": _svg('<path d="M15 16a5 5 0 1 1 7 4.6c-1.5.8-2 1.8-2 3.4"/><circle cx="20" cy="29" r="1.3" fill="currentColor"/>'),
}


def _stamp(category: str) -> str:
    return f'<span class="cat-stamp">{_CATEGORY_STAMP_SVG.get(category, _CATEGORY_STAMP_SVG["other"])}</span>'


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


class CardNewsService:
    """카드뉴스 HTML 생성기"""

    def generate_html(
        self,
        result: RelevanceAssessmentResult,
        patient_info: Optional[Dict[str, Any]] = None,
        screening: Optional[ScreeningProfile] = None,
    ) -> str:
        patient_info = patient_info or {}
        name = patient_info.get("name") or result.patient_name or "환자"
        test_date = patient_info.get("test_date") or result.test_date or ""

        relevant = result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        sensitized = result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        indeterminate = result.by_relevance(ClinicalRelevance.INDETERMINATE)

        cards: List[str] = []
        # 표지의 개수는 뒤 카드의 이름 목록과 같은 단위(임상 그룹)로 센다 — 유럽·미국 집먼지진드기는 하나
        review = result.by_relevance(ClinicalRelevance.CLINICIAN_REVIEW)   # 약물 — '진료 확인 필요'
        cards.append(self._cover_card(name, test_date, self._collapse(relevant),
                                      self._collapse(sensitized), self._collapse(indeterminate),
                                      getattr(result, "control_check", None), review=self._collapse(review)))
        # 처음에 물어본 동반 질환·주증상을 카드에 반영한다.
        # (이전에는 screening 을 인자로 받기만 하고 어떤 카드에도 쓰지 않았다)
        profile_card = self._profile_card(screening, relevant, getattr(result, "test_type", None))
        if profile_card:
            cards.append(profile_card)
        cards.append(self._relevant_card(relevant, screening))
        # 실제 주의 알러젠별 상세 카드 (Df/Dp 등은 그룹으로 1회만 — A1)
        for a in self._collapse(relevant)[:4]:
            cards.append(self._allergen_detail_card(a))
        cards.extend(self._animal_cards(relevant, screening))
        # 🍽️ 주의할 음식 카드 — OAS·교차반응을 '음식 중심'으로 통합(D, 주객전도 수정)
        food_card = self._food_alert_card(result.assessments)
        if food_card:
            cards.append(food_card)
        # 약물은 '진범 확정'에도 '무혐의 · 감작만'에도 넣지 않는다 — 자기 도장('진료 확인 필요')을 찍은 카드 한 장
        drug_card = care_guidance.drug_review_card(result.assessments, screening, stamp=True)
        if drug_card:
            cards.append(drug_card)
        cards.append(self._sensitized_card(sensitized, indeterminate, screening, result.assessments))
        # 감작만·관찰 필요 항원의 예방과 관찰(동물은 함께 사는지에 따라 따로)
        cards.extend(care_guidance.prevention_cards(result.assessments, screening))
        seasonality_card = self._seasonality_card(result.assessments, screening)
        if seasonality_card:
            cards.append(seasonality_card)
        cards.append(self._prevention_card(relevant, screening))
        # 문진에서 고른 주증상·쓰는 약에 맞춘 카드(고르지 않은 것은 만들지 않는다)
        cards.extend(care_guidance.symptom_cards(result.assessments, screening))
        treatment_card = self._treatment_card(relevant, screening)
        if treatment_card:
            cards.append(treatment_card)
        cards.extend(care_guidance.medication_cards(result.assessments, screening))
        imt_card = self._immunotherapy_card(result.assessments, screening)
        if imt_card:
            cards.append(imt_card)
        knowledge_card = self._disease_knowledge_card(screening)
        if knowledge_card:
            cards.append(knowledge_card)
        cards.append(self._closing_card(name))

        return self._wrap(self._deck_markup(cards, screening), name)

    # ---------- 덱 마크업(셸) ----------
    # 카드 본문(임상 문구)은 위 메서드들이 만든 그대로 두고, 카드 종류만 읽어 그 뒤에 상호작용 블록을 덧붙인다.
    # 카드의 개수·순서·본문은 바꾸지 않는다. JS 가 없으면 퀴즈는 정답·해설이 함께 보이는 읽을거리로 남는다.
    _KIND_BY_CLASS = (("cover", "cover"), ("closing", "closing"), ("section profile", "profile"),
                      ("section relevant", "relevant"), ("section oas", "oas"), ("section review", "review"),
                      ("section sensitized", "sensitized"), ("section seasonality", "seasonality"),
                      ("section season", "season"), ("section prevention", "prevention"),
                      ("section knowledge", "knowledge"))

    @classmethod
    def _card_kind(cls, card_html: str) -> str:
        """카드 최상위 요소의 class(와 태그 이모지)로 종류를 읽는다."""
        head = card_html.lstrip()[:400]
        for marker, kind in cls._KIND_BY_CLASS:
            if f'class="{marker}"' in head:
                return kind
        if 'class="section treatment"' in head:
            return "immunotherapy" if "💉" in head else "treatment"
        if 'class="section detail"' in head:
            # 동물 항원의 '알러젠 알아보기' 카드도 🐾 로 시작하므로 태그 문구로 가른다
            return "detail" if "알러젠 알아보기" in head else ("animal" if "🐾" in head else "detail")
        return "other"

    # 확인 퀴즈 — 질문·정답·해설 모두 '그 카드에 이미 적힌 문장'에서만 가져온다(새 임상 정보 없음).
    # (질문, [(보기, 정답 여부), ...], 해설)
    _QUIZ = {
        "relevant": ("'진범 확정' 도장은 어떤 항목에 찍힐까요?",
                     [("검사 수치가 높게 나온 항목", False),
                      ("검사 양성이면서 노출·해당 계절에 증상이 실제로 나타나는 항목", True)],
                     "검사 양성만으로는 정해지지 않아요. 증상이 실제로 나타나는지가 기준입니다."),
        "oas": ("이 카드의 음식은 얼마나 주의해야 할까요?",
                [("검사에서 직접 양성으로 나온 알러젠과 똑같은 수준으로", True),
                 ("검사 항목이 아니었으니 가볍게 봐도 된다", False)],
                "드셨을 때 실제로 증상이 확인된 음식입니다. 원재료 표시를 꼭 확인하세요."),
        "sensitized": ("'감작만'인 항목은 어떻게 하면 될까요?",
                       [("증상이 없어도 평생 완전히 피해야 한다", False),
                        ("과도한 회피는 필요 없지만, 증상이 새로 생기면 재평가한다", True)],
                       "감작은 남아 있어 추적이 필요합니다."),
        # 시즌 전에 약을 미리 쓰는 방법은 근거수준이 매우 낮다(data/treatment_guidance.json). 퀴즈가 '시즌 2주 전'을
        # 정답으로 단정하지 않는다 — 카드에 적힌 그대로, 시점은 진료에서 정한다는 것을 묻는다.
        "seasonality": ("꽃가루 시즌에 약을 언제부터 쓸지는 어떻게 정할까요?",
                        [("진료에서 정한다", True), ("달력을 보고 스스로 정한다", False)],
                        "약 2주 전부터 미리 쓰는 방법을 국내 지침이 제안하지만 근거수준은 매우 낮아요. "
                        "언제부터 쓸지는 진료에서 정하세요."),
        # 약을 쓰고 있다고 답한 환자의 덱에만 붙는다(_quiz_html)
        "treatment": ("약을 쓰고 있다면 노출을 줄이는 일은 그만해도 될까요?",
                      [("네, 약이 대신해 준다", False),
                       ("아니요, 노출 줄이기는 약과 함께 하는 관리다", True)],
                      "노출 줄이기는 약을 대신하지 않고 약과 함께 하는 관리입니다. "
                      "증상 변화와 약물 반응은 담당 의료진과 정기적으로 재평가하세요."),
        "immunotherapy": ("이 카드는 면역치료를 시작하라는 권고일까요?",
                          [("아니요, 증상과 약 조절 정도를 보고 담당 의료진이 판단한다", True),
                           ("네, 검사가 양성이면 바로 시작한다", False)],
                          "진료에서 상의해 볼 수 있는 선택지를 알려 드리는 카드입니다."),
        "closing": ("검사 '양성'이 뜻하는 것은 무엇일까요?",
                    [("그 알러젠에 반드시 알레르기가 있다", False),
                     ("감작 — 실제 알레르기인지는 노출될 때의 증상으로 확인한다", True)],
                    "노출될 때 증상이 나타나는 알러젠에 집중해 관리하세요."),
    }
    _CHECK_KINDS = ("prevention", "animal")     # '내 실천 체크' 를 붙일 카드
    _FOLD_KINDS = ("detail", "knowledge")       # 배경 설명을 눌러서 펼치는 카드(경고·판정 카드는 접지 않는다)

    def _quiz_html(self, kind: str, screening=None) -> str:
        quiz = self._QUIZ.get(kind)
        if not quiz:
            return ""
        if kind == "treatment":
            # '약을 쓰고 있다면…'은 쓰는 약을 알려 준 환자에게만 묻는다
            meds = [m for m in (getattr(screening, "current_medications", None) or []) if m != "none"]
            if not meds:
                return ""
        question, options, why = quiz
        opts = "".join(f'<li data-ok="{1 if ok else 0}">{_esc(text)}</li>' for text, ok in options)
        light = " light" if kind == "closing" else ""
        return (f'<div class="cn-quiz{light}"><div class="cn-quiz-t">🔎 확인 퀴즈</div>'
                f'<p class="cn-q">{_esc(question)}</p><ul class="cn-opts">{opts}</ul>'
                f'<p class="cn-exp"><b>정답 해설</b> {_esc(why)}</p></div>')

    def _deck_markup(self, cards: List[str], screening=None) -> str:
        total = len(cards)
        out = []
        for i, card in enumerate(cards, 1):
            kind = self._card_kind(card)
            extra = self._quiz_html(kind, screening)
            if extra:      # 카드 최상위 div 를 닫기 직전에 덧붙인다(본문 뒤)
                cut = card.rstrip().rfind("</div>")
                card = card[:cut] + extra + card[cut:]
            flags = (' data-check="1"' if kind in self._CHECK_KINDS else "") + \
                    (' data-fold="1"' if kind in self._FOLD_KINDS else "")
            out.append(f'<div class="card" data-kind="{kind}" data-i="{i}" role="group" '
                       f'aria-roledescription="slide" aria-label="{i} / {total}"{flags}>{card}</div>')
        return "\n".join(out)

    # ---------- 개별 카드 ----------
    def _cover_card(self, name, test_date, relevant, sensitized, indeterminate, control_check=None,
                    review=None) -> str:
        # 검사 대조(히스타민·생리식염수)는 알러젠이 아니라 개수에 넣지 않는다 — 검사를 읽을 수 있는지만 한 줄로
        control_html = ""
        if control_check and control_check.get("line_ko"):
            icon = "⚠️" if control_check.get("status") == "caution" else "🧪"
            control_html = f'<p class="sub">{icon} {_esc(control_check["line_ko"])}</p>'
        headline = (
            f"진범으로 확정된 알러젠 <b>{len(relevant)}개</b>"
            if relevant
            else "실제 증상과 연관된 알러젠을 확인해 보세요"
        )
        return f"""
        <div class="cover">
          <div class="badge">ALLERGEN QUEST · 카드뉴스</div>
          <h1>{_esc(name)}님의<br/>알러젠 탐험 리포트</h1>
          <p class="sub">검사일 {_esc(test_date) or '-'} · 검사 양성은 흔적일 뿐, 진범은 증상으로 확정</p>
          <div class="cover-stat">
            <div><span class="num">{len(relevant)}</span><span class="lbl">진범 확정</span></div>
            <div><span class="num">{len(sensitized)}</span><span class="lbl">무혐의·감작만</span></div>
            <div><span class="num">{len(indeterminate)}</span><span class="lbl">관찰 대상</span></div>
            {self._review_stat(review)}
          </div>
          <p class="headline">{headline}</p>
          {control_html}
        </div>
        """

    @staticmethod
    def _review_stat(review) -> str:
        """표지의 네 번째 칸 — 약물 항원('진료 확인 필요')이 있을 때만. 진범·감작만·관찰 어디에도 세지 않는다."""
        if not review:
            return ""
        return (f'<div><span class="num">{len(review)}</span>'
                f'<span class="lbl">{_esc(care_guidance.drug_review_label())}</span></div>')

    # 중증도 배지(B) — 중증 이상은 카드에서 강조
    _SEV_BADGE = {"mild": "", "moderate": "",
                  "severe": "🔴 중증", "anaphylaxis": "🚨 아나필락시스"}

    def _label(self, a: AllergenAssessment, detail: bool = False) -> str:
        """임상 그룹(Df/Dp 등)은 통합 라벨(A1)."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return get_clinical_group_service().label_of(a, detail=detail)
        except Exception:
            return a.korean_name or a.allergen_name

    def _collapse(self, items):
        """임상 그룹 단위로 접어 중복 표기 제거(A1)."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return [g["members"][0][1] for g in get_clinical_group_service().collapse(items)]
        except Exception:
            return list(items)

    def _chip(self, a: AllergenAssessment, screening=None) -> str:
        nm = _esc(self._label(a))
        # 시기는 계절성 카드·리포트와 같은 값 한 가지를 쓴다(종별 자료 우선, 없으면 분류군 추정)
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            season = get_pollen_forecast_service().season_label(a, screening)
        except Exception:  # noqa: BLE001
            season = (a.kb or {}).get("season_label_ko", "")
        season = _esc(season)
        season_html = f'<span class="chip-season">{season}</span>' if season else ""
        sev = self._SEV_BADGE.get(getattr(a, "severity", None) or "", "")
        sev_html = f'<span class="chip-season"><b>{_esc(sev)}</b></span>' if sev else ""
        return f'<div class="chip">{_stamp(a.category)} <b>{nm}</b>{season_html}{sev_html}</div>'

    def _relevant_card(self, relevant: List[AllergenAssessment], screening=None) -> str:
        if not relevant:
            body = '<p class="empty">이번 문진에서는 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. 증상이 있을 때 노출 상황을 기록해 두면 도움이 됩니다.</p>'
        else:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(relevant))  # Df/Dp 등은 한 번만(A1)
            body = f'<div class="chips">{chips}</div>'
        return f"""
        <div class="section relevant">
          <div class="stamp-verdict relevant">진범 확정</div>
          <h2>증상으로 확인된<br/>진짜 범인</h2>
          <p class="desc">검사 양성이면서 <b>노출/해당 계절에 증상이 실제로 나타나는</b> 항목입니다. 회피와 관리가 가장 중요합니다.</p>
          {body}
        </div>
        """

    @staticmethod
    def _sensitized_desc(assessments, screening) -> str:
        """'감작만' 카드의 설명 — 리포트 2절·상담 답변과 같은 한 가지 입장(care_guidance.sensitized_lead)."""
        lead = [_esc(x) for x in care_guidance.sensitized_lead(assessments, screening)]
        if lead:
            lead[0] = lead[0].replace("노출해도 증상이 없는", "<b>노출해도 증상이 없는</b>")
        return " ".join(lead)

    def _sensitized_card(self, sensitized, indeterminate, screening=None, assessments=None) -> str:
        parts = []
        if sensitized:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(sensitized))  # Df/Dp 등은 한 번만(A1)
            parts.append(f'<div class="chips">{chips}</div>')
        else:
            parts.append('<p class="empty">감작만 된 항목은 없습니다.</p>')
        ind_html = ""
        if indeterminate:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(indeterminate))
            ind_html = f'<div class="stamp-verdict indet small">관찰 대상</div><div class="chips">{chips}</div>'
        return f"""
        <div class="section sensitized">
          <div class="stamp-verdict sensitized">무혐의 · 감작만</div>
          <h2>검사만 양성,<br/>증상은 없는 항목</h2>
          <p class="desc">{self._sensitized_desc(assessments if assessments is not None else list(sensitized), screening)}</p>
          {''.join(parts)}
          {ind_html}
        </div>
        """

    def _prevention_card(self, relevant: List[AllergenAssessment], screening=None) -> str:
        # 실제 주의 알러젠마다 돌아가며 한 개씩, 표현만 다른 같은 수칙은 한 번만(리포트와 같은 규칙).
        # 예전에는 앞선 알러젠이 여섯 칸을 다 채워, 진드기 두 종의 같은 수칙이 두 번씩 나오고 뒤 알러젠이 빠졌다.
        tips = [f"{_CATEGORY_EMOJI.get(t['category'], '•')} {t['tip']}" for t in allocated_tips(
            [(self._label(a), a) for a in self._collapse(relevant)], screening, limit=6)]
        note = ""
        if not tips:
            # 증상이 확인된 알러젠이 없으면 진드기·꽃가루 수칙을 지어내지 않는다(exposure_guidance_service 의 주석)
            note = f'<p class="desc">{_esc(NO_RELEVANT_NOTE_KO)}</p>'
            tips = [f"📝 {NO_RELEVANT_TIPS_KO[0]}", f"🩺 {NO_RELEVANT_TIPS_KO[1]}"]
        items = "\n".join(f"<li>{_esc(t)}</li>" for t in tips[:6])
        return f"""
        <div class="section prevention">
          <div class="tag">🛡️ 예방·관리</div>
          <h2>우선 실천할<br/>생활 수칙</h2>
          {note}
          <ul class="tips">{items}</ul>
        </div>
        """

    def _allergen_detail_card(self, a: AllergenAssessment) -> str:
        kb = a.kb or {}
        emoji = _CATEGORY_EMOJI.get(a.category, "•")
        nm = _esc(self._label(a, detail=True))
        bio = _esc(self._short(kb.get("biology_ko", ""), 300))
        expo = _esc(self._short(kb.get("exposure_environment_ko", ""), 300))
        tips = [t for t in (kb.get("avoidance_control_ko") or [])[:3]]
        if animal_guidance(a, None):
            tips = []   # 동물 항원은 바로 뒤의 관리 카드가 함께 사는지에 맞춰 안내한다
        tips_html = "".join(f"<li>{_esc(t)}</li>" for t in tips)
        try:
            imt = get_knowledge_service().immunotherapy_info(
                a.category, a.allergen_name, a.korean_name or "", assessment=a)
        except Exception:
            imt = {"eligible": False}
        imt_html = ('<div class="imt">💉 면역치료를 진료에서 상의해 볼 수 있는 항원이에요. '
                    '뒤쪽 면역치료 카드를 보세요.</div>') if imt.get("eligible") else ""
        return f"""
        <div class="section detail">
          <div class="tag">{emoji} 알러젠 알아보기</div>
          <h2>{nm}</h2>
          {f'<p class="desc"><b>어떤 알러젠?</b> {bio}</p>' if bio else ''}
          {f'<p class="desc"><b>어디서 노출?</b> {expo}</p>' if expo else ''}
          {f'<div class="mini-title">🏠 이렇게 관리하세요</div><ul class="tips">{tips_html}</ul>' if tips_html else ''}
          {imt_html}
        </div>
        """

    def _food_alert_card(self, assessments: List[AllergenAssessment]) -> str:
        """🍽️ 주의할 음식 카드(D) — 주인공은 '음식'. 원인 항원은 괄호로 부연.
        OAS·성분 교차반응을 하나로 통합하고, 검사 양성 알러젠과 동급으로 경고한다."""
        by_food = {}
        worst = None
        rank = {"oral": 1, "systemic": 2, "anaphylaxis": 3}
        for a in assessments:
            src = self._label(a)
            # 음식 경고는 교차반응 자체의 증상 범위를 사용
            sev = getattr(a, "crossreact_severity", None)
            if sev and rank.get(sev, 0) > rank.get(worst or "", 0):
                worst = sev
            for f in (getattr(a, "oas_foods", None) or []) + (getattr(a, "crossreact_confirmed", None) or []):
                e = by_food.setdefault(f, [])
                if src not in e:
                    e.append(src)
        if not by_food:
            return ""
        rows = "".join(
            f'<div class="chip">🚫 <b>{_esc(food)}</b>'
            f'<span class="chip-season">{_esc(" · ".join(trigs))} 교차반응</span></div>'
            for food, trigs in by_food.items())
        sev_html = ""
        if worst in ("systemic", "anaphylaxis"):
            sev_html = ('<p class="desc" style="margin-top:12px">🚨 <b>전신 반응 병력이 있습니다.</b> '
                        '반드시 피하시고, 응급약·응급 대처 계획을 담당 의료진과 상의하세요.</p>')
        return f"""
        <div class="section oas">
          <div class="tag food">🍽️ 반드시 주의할 음식</div>
          <h2>이 음식들을<br/>조심하세요</h2>
          <p class="desc">아래 음식은 드셨을 때 <b>실제로 증상이 확인된</b> 음식입니다.
          검사에서 직접 양성으로 나온 알러젠과 <b>똑같은 수준으로 주의</b>해야 합니다.</p>
          <div class="chips">{rows}</div>
          <p class="desc" style="margin-top:12px">⚠️ 음식은 꽃가루·진드기와 달리 <b>한 번에 많은 양</b>이
          몸에 들어가고, <b>외식·가공식품에서 모르는 사이에</b> 먹게 되기 쉽습니다.
          <b>원재료 표시를 꼭 확인</b>하세요.</p>
          <p class="desc" style="margin-top:8px">💡 생과일·생채소는 <b>익히면 증상이 줄어드는</b> 경우가 많지만,
          견과·콩류는 익혀도 남을 수 있습니다.</p>
          {sev_html}
        </div>
        """

    # 질환 코드 → 카드에 쓸 문구. 환자가 이미 앓고 있다고 답한 질환에 맞춰 연결한다.
    _DISEASE_CARD_KO = {
        "allergic_rhinitis": ("알레르기 비염", "코 증상은 원인 알러젠에 노출될 때 생기거나 심해져요. 아래 알러젠과의 관계를 살펴봤어요."),
        # 조절제를 어떻게 쓰는지는 여기서 말하지 않는다 — '내 약 이해하기' 카드(GINA: 매일 또는 증상이 있을 때)가 맡는다
        "asthma": ("천식", "흡입 알러젠 노출은 천식 증상을 일으키거나 악화시킬 수 있어요. 아래 알러젠과의 관계를 살펴봤어요."),
        "atopic_dermatitis": ("아토피 피부염", "보습이 기본이고, 확인된 알러젠 노출이 겹치면 더 나빠질 수 있어요."),
        "allergic_conjunctivitis": ("알레르기 결막염", "눈을 비비면 더 심해져요. 외출 후 세안이 도움이 됩니다."),
        "chronic_urticaria": ("만성 두드러기", "두드러기는 알레르겐 외 원인도 많아, 유발 상황을 기록해 두면 좋아요."),
        "food_allergy": ("음식 알레르기", "먹고 반복해서 증상이 났던 음식이 핵심이에요. 검사 수치만으로 끊지 않아요."),
        # 리포트·'치료와 연결하기' 카드와 같은 문장(care_guidance.ANAPHYLAXIS_HISTORY_KO)
        "anaphylaxis": ("아나필락시스 병력", care_guidance.ANAPHYLAXIS_HISTORY_KO[1]),
        "drug_allergy": ("약물 알레르기", "약 이름과 당시 증상을 기록해 진료 때마다 알리세요."),
        "sinusitis": ("부비동염", "코 증상이 오래가면 부비동염이 겹쳤는지 확인이 필요해요."),
    }
    _ORGAN_CARD_KO = {
        "nasal": "코(재채기·콧물·코막힘)",
        "ocular": "눈(가려움·충혈·눈물)",
        "lower_airway": "하기도(기침·천명·숨참)",
        "skin": "피부(두드러기·가려움·습진)",
        "gi": "소화기(복통·설사·구토)",
        "systemic": "전신(어지럼·아나필락시스)",
    }

    def _profile_card(self, screening, relevant: List[AllergenAssessment], test_type=None) -> str:
        """처음 문진에서 답한 동반 질환·주증상 부위를 검사 결과와 연결한다.
        test_type: 검사 종류 — 항히스타민제의 위음성 주의는 피부반응검사(SPT)에만 해당한다."""
        if screening is None:
            return ""
        diseases = [d for d in (getattr(screening, "allergic_diseases", None) or []) if d != "none"]
        organs = list(getattr(screening, "organ_systems", None) or [])
        if not diseases and not organs:
            return ""

        disease_items = "".join(
            f"<li>🩺 <b>{_esc(self._DISEASE_CARD_KO[d][0])}</b> — {_esc(self._DISEASE_CARD_KO[d][1])}</li>"
            for d in diseases if d in self._DISEASE_CARD_KO)
        organ_txt = ", ".join(self._ORGAN_CARD_KO.get(o, o) for o in organs)
        organ_line = (f'<li>📍 <b>주로 나타나는 부위:</b> {_esc(organ_txt)} — '
                      f'이 부위 증상이 아래 알러젠 노출과 함께 움직이는지가 판정의 핵심이었어요.</li>'
                      if organ_txt else "")
        # 하기도 증상이 있는데 천식 진단이 없으면 확인을 권한다(진단하지는 않는다)
        airway_line = ""
        if "lower_airway" in organs and "asthma" not in diseases:
            airway_line = ('<li>🫁 기침·천명·숨참이 있다고 하셨어요. 천식 여부는 이 검사로 알 수 없으니 '
                           '진료에서 폐기능검사가 필요한지 확인해 보세요.</li>')
        ah = ""
        meds = getattr(screening, "current_medications", None) or []
        from services.screening_service import skin_test_caution_applies
        if skin_test_caution_applies(test_type) and (
                "antihistamine" in meds or getattr(screening, "antihistamine_recent", None)):
            ah = ('<li>⚠️ 항히스타민제를 최근 복용하셨다면 피부반응검사(SPT)에서 '
                  '<b>위음성</b>이 나올 수 있어 해석에 주의가 필요해요.</li>')
        n = len(self._collapse(relevant))    # 이름 목록과 같은 단위(임상 그룹)
        # 질환을 알려 주지 않은 환자에게 '이미 앓고 있는 질환과…'라고 쓰지 않는다
        title_head = "이미 앓고 있는 질환과" if disease_items else "처음 알려주신 증상과"
        return f"""
        <div class="section profile">
          <div class="tag">📋 처음 알려주신 정보</div>
          <h2>{title_head}<br/>검사 결과를 이어봤어요</h2>
          <ul class="tips">
            {disease_items}
            {organ_line}
            {airway_line}
            {ah}
            <li>🔎 이번 검사에서 실제 증상과 연결된 알러젠은 <b>{n}가지</b>였어요.</li>
          </ul>
        </div>"""

    def _disease_knowledge_card(self, screening) -> str:
        """📚 질환 알아보기 — 환자가 고른 기저 질환의 일반 정보(온톨로지, 검토 전).
        검사 결과 카드와 섞이지 않게 치료 카드 뒤에 둔다."""
        try:
            from services.ontology_service import disease_summaries_for
            items = disease_summaries_for(screening, limit=2)
        except Exception:  # noqa: BLE001
            return ""
        if not items:
            return ""
        blocks = []
        for it in items:
            lis = []
            if it.get("symptoms"):
                lis.append(f"<li>🤧 <b>흔한 증상:</b> {_esc(', '.join(x['ko'] for x in it['symptoms']))}</li>")
            if it.get("management"):
                lis.append(f"<li>🩹 <b>일반적인 관리:</b> {_esc(', '.join(x['ko'] for x in it['management']))}"
                           f" — 선택은 의료진과</li>")
            if it.get("related"):
                lis.append(f"<li>🔗 <b>함께 나타날 수 있어요:</b> {_esc(', '.join(x['ko'] for x in it['related']))}</li>")
            blocks.append(f'<p class="desc"><b>{_esc(it["title_ko"])}</b> — {_esc(it.get("definition_ko") or "")}</p>'
                          + (f'<ul class="tips">{"".join(lis)}</ul>' if lis else ""))
        return f"""
        <div class="section knowledge">
          <div class="tag">📚 질환 알아보기</div>
          <h2>알아두면 좋은<br/>질환 정보</h2>
          {"".join(blocks)}
          <p class="src">위키백과 기반 온톨로지에서 고른 일반 정보예요. 전문가 검토 전이며, 내 검사 결과가 아니에요.</p>
        </div>"""

    def _seasonality_card(self, assessments: List[AllergenAssessment], screening) -> str:
        """증상이 계절을 탄다면 달력으로 보여준다. 거주 지역이 없어도 만든다 —
        계절성은 알러젠 자체의 성질이라 지역 없이도 말할 수 있다."""
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            s = get_pollen_forecast_service().seasonality(assessments, screening)
        except Exception:  # noqa: BLE001
            return ""
        if not s.get("available") or not s.get("predicted_months"):
            return ""

        labels = s["month_labels_ko"]
        months = set(s["predicted_months"])
        strip = "".join(
            f'<span class="m {"on" if m in months else ""}{" now" if m == s["current_month"] else ""}">'
            f'{_esc(labels[m - 1].replace("월", ""))}</span>' for m in range(1, 13))
        rows = "".join(
            f'<li>🌿 <b>{_esc(it["name"])}</b> — {_esc(it["season_label_ko"] or "-")}</li>'
            for it in s["items"][:5])
        now_line = (f'<p class="lead">지금은 <b>{_esc(", ".join(s["in_season_now"]))}</b> 시즌이에요.</p>'
                    if s.get("in_season_now") else "")
        # 지역 메모는 그 메모가 말하는 식물에 이 환자의 증상이 확인됐을 때만 온다(seasonality 가 가린다)
        notable = f'<li>📌 {_esc(s["notable_ko"])}</li>' if s.get("notable_ko") else ""
        if s.get("category_estimate"):
            notable += ('<li>ℹ️ ‘전체 기준 추정’은 그 식물만의 자료가 없어 같은 분류군 전체의 범위로 '
                        '대신한 시기예요.</li>')

        note = ""
        if s.get("mismatch"):
            rl = ", ".join(labels[m - 1] for m in s["reported_months"])
            note = (f'<li>🔎 말씀하신 악화 시기({_esc(rl)})가 위 시즌과 겹치지 않아요. '
                    f'계절과 무관한 원인이 함께 있을 수 있어요.</li>')
        elif s.get("overlap_months"):
            ol = ", ".join(labels[m - 1] for m in s["overlap_months"])
            note = (f'<li>✅ 말씀하신 악화 시기가 <b>{_esc(ol)}</b> 에서 겹쳐요. '
                    f'계절성 알레르기로 볼 근거예요.</li>')
        excluded = ""
        if s.get("excluded"):
            excluded = (f'<li>⚪ {_esc(", ".join(s["excluded"]))} — 검사만 양성이고 그 시기 증상이 '
                        f'확인되지 않아 달력에 넣지 않았어요.</li>')
        return f"""
        <div class="section seasonality">
          <div class="tag">🍂 증상의 계절성{(" · " + _esc(s["region_label_ko"]) + " 기준") if s.get("region_label_ko") else ""}</div>
          <h2>몇 월에<br/>조심해야 할까</h2>
          {now_line}
          <div class="month-strip">{strip}</div>
          <ul class="tips">
            {rows}
            {excluded}
            {note}
            {notable}
            <li>⏰ <b>시즌 대비</b> {_esc(care_guidance.treatment_text("pollen_prophylaxis_general_ko"))}</li>
          </ul>
        </div>"""

    def _season_card(self, assessments: List[AllergenAssessment], screening) -> str:
        """거주 지역 기준으로 지금 조심할 꽃가루를 짚어준다.

        꽃가루 시기는 지역을 탄다. 거주지를 모르면 이 카드는 만들지 않는다 —
        틀린 시기를 알려주느니 말하지 않는 편이 낫다.
        """
        if screening is None or not getattr(screening, "residence_country", None):
            return ""
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            out = get_pollen_forecast_service().for_patient(
                assessments,
                country=getattr(screening, "residence_country", None),
                region=getattr(screening, "residence_region", None),
                lat=getattr(screening, "residence_lat", None),
                lon=getattr(screening, "residence_lon", None),
                postal_code=getattr(screening, "residence_postal_code", None))
        except Exception:  # noqa: BLE001
            return ""
        if not out.get("available") or not out.get("items"):
            return ""

        now = out.get("in_season_now") or []
        rows = "".join(
            f'<li>{"🔴" if i.get("in_season") else "⚪"} <b>{_esc(i.get("korean_name") or i.get("allergen_name"))}</b>'
            f' [{_esc(i.get("type_ko"))}] — {_esc(i.get("season_label_ko") or ("지금 " + str(i.get("level"))) or "")}</li>'
            for i in out["items"])
        head = (f'지금은 <b>{_esc(", ".join(i.get("korean_name") or "" for i in now))}</b> 시즌이에요.'
                if now else "지금은 시즌이 아닌 꽃가루들이에요.")
        notable = f'<li>📌 {_esc(out["notable_ko"])}</li>' if out.get("notable_ko") else ""
        live = ('<li>📡 실시간 꽃가루 예보를 반영했어요.</li>' if out.get("live") else "")
        return f"""
        <div class="section season">
          <div class="tag">🗓️ {_esc(out.get("region_label_ko") or "거주 지역")} 기준</div>
          <h2>지금 조심할<br/>꽃가루는</h2>
          <p class="lead">{head}</p>
          <ul class="tips">
            {rows}
            {notable}
            {live}
          </ul>
        </div>"""

    def _exposure_link_items(self, relevant: List[AllergenAssessment], screening, limit: int = 3) -> str:
        """노출 → 증상 → 질환 연결을 카드 항목으로. 환자가 알려준 질환·증상만 쓴다."""
        out = exposure_links([(self._label(a), a) for a in self._collapse(relevant)], screening)
        items = []
        for ln in out["links"][:limit]:
            target = (f' → 알려주신 <b>{_esc(", ".join(ln["targets"]))}</b>' if ln["targets"]
                      else (f' → {_esc(ln["confirmed"])}' if ln["confirmed"] else ""))
            note = f' {_esc(ln["notes"][0])}' if ln["notes"] else ""
            items.append(f'<li>🔗 <b>{_esc(ln["label"])}:</b> {_esc(ln["exposure"])}{target}.{note}</li>')
        return "".join(items)

    def _animal_cards(self, relevant: List[AllergenAssessment], screening) -> List[str]:
        """🐾 동물 항원 관리 카드 — 함께 사는지(문진의 반려동물 답)에 맞춘 구체적 안내."""
        cards = []
        for a in self._collapse(relevant):
            g = animal_guidance(a, screening)
            if not g or not g["steps"]:
                continue
            steps = "".join(f"<li>{_esc(s['text'])}</li>" for s in g["steps"])
            extra = "".join(f'<p class="desc" style="margin-top:8px">{_esc(t)}</p>' for t in g["extra"])
            cards.append(f"""
        <div class="section detail">
          <div class="tag">🐾 {_esc(self._label(a))} 관리</div>
          <h2>{_esc(g["headline"])}</h2>
          <ul class="tips">{steps}</ul>
          {extra}
          <p class="desc" style="margin-top:8px">{_esc(g["caveat"])}</p>
        </div>""")
            work = g.get("occupational")
            if work:      # 일하면서 다루는 동물 — 직장 노출 안내는 따로 한 장
                steps = "".join(f"<li>{_esc(s['text'])}</li>" for s in work["steps"])
                cards.append(f"""
        <div class="section detail">
          <div class="tag">🐾 {_esc(self._label(a))} · 직장</div>
          <h2>{_esc(work["headline"])}</h2>
          <ul class="tips">{steps}</ul>
        </div>""")
        return cards

    _IMT_ROUTE_KO = {"SCIT": "피하주사", "SLIT": "설하"}

    def _immunotherapy_card(self, assessments: List[AllergenAssessment], screening) -> str:
        """💉 면역치료 카드 — 감작과 증상이 함께 확인된 항원 중 면역치료 가능 항원이 있을 때만."""
        try:
            rows = get_knowledge_service().immunotherapy_candidates(assessments)
        except Exception:  # noqa: BLE001
            return ""
        if not rows:
            return ""
        items = ""
        for r in rows:
            e = r["entry"]
            routes = "·".join(self._IMT_ROUTE_KO.get(x, x) for x in e.get("routes", []))
            items += f'<li><b>💉 {_esc(e["label_ko"])}</b> — {_esc(routes)}. {_esc(e["korea"]["note_ko"])}</li>'
        asthma = ""
        if "asthma" in set(getattr(screening, "allergic_diseases", None) or []):
            asthma = '<li>🫁 천식이 있다고 하셨어요. 면역치료는 천식이 잘 조절될 때 시작해요.</li>'
        # 리포트의 면역치료 절과 같은 안내 — 카드에는 빠져 있었다
        if "immunotherapy" in (getattr(screening, "current_medications", None) or []):
            asthma += ('<li>💬 이미 면역치료를 받고 있다고 하셨습니다. 치료 중인 항원이 위와 같은지 '
                       '진료에서 확인하세요.</li>')
        return f"""
        <div class="section treatment">
          <div class="tag">💉 면역치료</div>
          <h2>진료에서 상의할 수 있는<br/>선택지</h2>
          <p class="desc">검사 양성이면서 <b>증상까지 확인된</b> 알러젠 가운데 면역치료가 가능한 항원이에요.</p>
          <ul class="tips">
            {items}
            {asthma}
            <li>🧑‍⚕️ 시작하라는 권고가 아니에요. 증상과 약 조절 정도를 보고 <b>담당 의료진이 판단</b>해요. 보통 3년 이상 이어 가는 치료예요.</li>
          </ul>
          <p class="desc" style="margin-top:8px;font-size:11px">출처: KAAACI 알레르겐 면역치료 진료지침(2023)·설하면역치료 지침(2024). 국내 사용 가능 여부는 발간 시점 기준.</p>
        </div>"""

    def _treatment_card(self, relevant: List[AllergenAssessment], screening) -> str:
        # 이 카드는 노출 줄이기와 기존 치료를 잇는 이야기다. 약물 항원에는 맞지 않는다 —
        # 그 약을 피할지·다시 쓸지는 진료에서 정한다(약물 카드가 따로 적는다).
        relevant = [a for a in relevant if a.category != "drug"]
        link_lines = self._exposure_link_items(relevant, screening)
        diseases = set(getattr(screening, "allergic_diseases", None) or [])
        disease_lines = ""
        # 천식 줄: 노출과의 관계는 '처음 알려주신 정보' 카드에, 흡입 스테로이드의 쓰임은 '내 약 이해하기' 카드에
        # 있다. 여기서는 그 약 카드가 없는 환자에게만 조절제 문장을 싣는다(같은 덱에 같은 사실은 한 번).
        asthma_line = care_guidance.asthma_card_line(screening) if "asthma" in diseases else ""
        if asthma_line:
            disease_lines += f'<li>🫁 <b>천식이 있다고 하셨어요:</b> {_esc(asthma_line)}</li>'
        if diseases & {"atopic_dermatitis", "chronic_urticaria"}:
            disease_lines += ('<li>🧴 <b>피부 증상이 있다고 하셨어요:</b> 보습이 기본이고, 필요 시 국소 치료를 '
                              '진료에서 상의하세요.</li>')
        if care_guidance.has_anaphylaxis_history(screening):
            # 리포트·클래식 카드뉴스와 같은 문장(care_guidance.ANAPHYLAXIS_HISTORY_KO)
            head, body = care_guidance.ANAPHYLAXIS_HISTORY_KO
            disease_lines += f'<li>🚨 <b>{_esc(head)}</b> {_esc(body)}</li>'
        if not link_lines.strip() and not disease_lines:
            # 이을 노출도, 알려 준 질환도 없으면 노출 줄이기에 관한 일반 문장만 남는다 — 실제 주의 알러젠이
            # 없는 환자에게는 가리킬 것이 없는 문장이라 카드를 만들지 않는다
            return ""
        # 쓰는 약을 알려 주지 않은 환자에게 '기존 치료'·'약을 바꾸기 전에'라고 말하지 않는다
        meds = [m for m in (getattr(screening, "current_medications", None) or []) if m != "none"]
        if meds:
            title = "기존 치료와<br/>어떻게 함께 갈까"
            tail = (f'<li>🛡️ <b>노출 줄이기와 약:</b> {_esc(care_guidance.treatment_text("exposure_tail_ko"))}</li>'
                    f'<li>🧑‍⚕️ <b>정기 점검:</b> {_esc(care_guidance.treatment_text("review_interval_ko"))}</li>')
        else:
            title = "노출과 증상,<br/>이렇게 이어집니다"
            tail = '<li>🧑‍⚕️ <b>다시 평가:</b> 증상이 새로 생기거나 달라지면 담당 의료진과 다시 평가하세요.</li>'
        return f"""
        <div class="section treatment">
          <div class="tag">🩺 치료와 연결하기</div>
          <h2>{title}</h2>
          <ul class="tips">
            {link_lines}
            {disease_lines}
            {tail}
          </ul>
        </div>
        """

    @staticmethod
    def _short(text: str, n: int) -> str:
        # 글자수로 자르면 '온도 20~2…' 처럼 문장 중간에서 끊긴다 — 문장 경계에서만 줄인다
        return trim_sentences(text, n)

    def _closing_card(self, name) -> str:
        return f"""
        <div class="closing">
          <div class="tag light">✅ 정리</div>
          <h2>{_esc(name)}님,<br/>탐험의 핵심은 '증상과의 연결'</h2>
          <p class="desc light">검사 양성은 <b>감작</b>을 뜻할 뿐입니다. 실제로 <b>노출될 때 증상이 나타나는 알러젠</b>에 집중해 관리하세요.</p>
          <p class="note">본 카드뉴스는 교육용 참고 자료이며, 정확한 진단·치료는 담당 의료진과 상담하세요.</p>
        </div>
        """

    # ---------- 래퍼(스타일) ----------
    _KNOWLEDGE_CSS = """
      .section.knowledge { overflow-y:auto; }
      .knowledge .tag { background:#eef3e8; color:#4f7a28; }
      .knowledge .desc { margin-top:10px; }
      .knowledge .src { font-size:11px; color:#8a8f98; margin-top:12px; line-height:1.5; }
    """
    _MONTH_STRIP_CSS = """
      .month-strip { display:flex; gap:4px; flex-wrap:wrap; margin:14px 0; }
      .month-strip .m { flex:1 1 0; min-width:26px; text-align:center; padding:7px 0;
        border-radius:7px; background:rgba(0,0,0,.05); font-size:13px; font-weight:700;
        color:#8a8f98; }
      .month-strip .m.on { background:#ffd8a8; color:#7a4100; }
      .month-strip .m.now { outline:2px solid #e8590c; outline-offset:1px; }
    """

    # 인터랙티브 덱 — 전부 인라인(외부 스크립트·이미지 없음). JS 가 켜지면 <html class="js"> 가 붙고 그때만 적용된다.
    _DECK_CSS = """
      .sr { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap; }
      .cn-ui { display:none; }
      /* 퀴즈 — JS 없이는 정답 표시와 해설이 그대로 보이는 읽을거리 */
      .cn-quiz { margin-top:16px; padding:12px 12px 10px; border-radius:14px; background:#fdf6e6; border:1.5px dashed #e3c98c; }
      .cn-quiz.light { background:rgba(255,255,255,.12); border-color:rgba(255,255,255,.4); }
      .cn-quiz-t { font-size:11.5px; font-weight:800; letter-spacing:.5px; color:var(--amber-ink); }
      .cn-quiz.light .cn-quiz-t { color:#ffe1a8; }
      .cn-q { font-size:13.5px; font-weight:800; line-height:1.45; margin-top:4px; }
      .cn-opts { list-style:none; display:flex; flex-direction:column; gap:6px; margin-top:8px; }
      .cn-opts li, .cn-opts button { font:inherit; font-size:12.5px; line-height:1.45; text-align:left; width:100%; color:var(--ink);
        background:var(--elev); border:1.5px solid var(--line); border-radius:10px; padding:8px 10px; }
      html:not(.js) .cn-opts li[data-ok="1"] { border-color:var(--forest); font-weight:700; }
      html:not(.js) .cn-opts li[data-ok="1"]::after { content:" ✓"; color:var(--forest-d); font-weight:800; }
      .cn-exp { font-size:12px; line-height:1.5; margin-top:8px; color:var(--ink2); }
      .cn-quiz.light .cn-exp { color:#e7ece8; }
      .cn-check-t { display:none; }

      .js body { padding:10px 0 12px; min-height:100vh; display:flex; flex-direction:column; }
      .js .cn-ui { display:flex; }
      .js .cn-top { flex-direction:column; gap:6px; padding:0 16px 4px; max-width:560px; width:100%; margin:0 auto; }
      .cn-bar { height:8px; border-radius:999px; background:#efe6d2; overflow:hidden; border:1px solid var(--line); }
      .cn-bar i { display:block; height:100%; width:100%; transform-origin:left center; transform:scaleX(0); border-radius:inherit;
        background:linear-gradient(90deg,var(--forest),var(--amber)); transition:transform .4s cubic-bezier(.2,.8,.2,1); }
      .cn-meta { display:flex; align-items:center; gap:12px; font-size:12.5px; font-weight:700; color:var(--ink2); }
      .cn-meta .cn-num { font-family:var(--display); font-weight:400; font-size:16px; color:var(--ink); }
      .cn-meta .cn-done { color:var(--forest-d); }
      .cn-meta button { margin-left:auto; font:inherit; font-size:12px; font-weight:800; color:var(--ink2); background:var(--elev);
        border:1.5px solid var(--line); border-radius:999px; padding:6px 12px; min-height:34px; cursor:pointer; }
      .js .deck { flex:1 1 auto; align-items:center; gap:14px; padding:10px calc(50% - min(44vw, 180px)) 14px; scroll-padding:0 calc(50% - min(44vw, 180px));
        scrollbar-width:none; overscroll-behavior-x:contain; outline:none; }
      .js .deck::-webkit-scrollbar { display:none; }
      .js .deck:focus-visible { box-shadow:inset 0 0 0 3px var(--amber); border-radius:12px; }
      .js .card { width:min(88vw, 360px); height:min(540px, calc(100vh - 150px)); min-height:380px; scroll-snap-stop:always;
        transition:transform .35s cubic-bezier(.2,.8,.2,1), opacity .35s; transform:scale(.94); opacity:.6; }
      .js .card.active { transform:none; opacity:1; }
      .js .card > div { flex:1 1 0; min-height:0; overflow-y:auto; }
      /* 긴 카드 — 아래에 내용이 더 있으면 '아래로 더 있어요' 알약을 띄운다(끝까지 내리면 사라진다) */
      .cn-more, .cn-js { display:none; }
      .js .cn-js { display:inline; }
      .js .card.more .cn-more { display:block; position:absolute; left:50%; bottom:14px; transform:translateX(-50%); z-index:2;
        font:inherit; font-size:12px; font-weight:800; color:#fff; background:var(--forest-d); border:0; border-radius:999px;
        padding:7px 14px; min-height:32px; cursor:pointer; box-shadow:0 4px 12px rgba(0,0,0,.25); }
      /* 점선 테두리를 스크롤되는 본문이 아니라 카드에 고정한다 */
      .js .card > div::before { display:none; }
      .js .card::after { content:''; position:absolute; inset:9px; border:1.5px dashed rgba(0,0,0,.12); border-radius:15px; pointer-events:none; }
      .js .card[data-kind="cover"]::after, .js .card[data-kind="closing"]::after { border-color:rgba(255,255,255,.35); }
      /* 등장 애니메이션은 카드가 화면에 들어올 때(.in) 시작한다. 가운데에 올 때(.active)까지 미루면
         옆에 걸쳐 보이는 다음 카드가 빈 틀로 보인다. */
      .js .card:not(.in) > div > * { opacity:0; }
      .js .card.in > div > * { animation:cn-in .5s cubic-bezier(.2,.8,.2,1) both; }
      .js .card.in > div > *:nth-child(2) { animation-delay:.07s; } .js .card.in > div > *:nth-child(3) { animation-delay:.14s; }
      .js .card.in > div > *:nth-child(4) { animation-delay:.21s; } .js .card.in > div > *:nth-child(5) { animation-delay:.28s; }
      .js .card.in > div > *:nth-child(n+6) { animation-delay:.35s; }
      .js .card.in .stamp-verdict { animation-name:cn-stamp; }
      @keyframes cn-in { from { opacity:0; transform:translateY(12px); } to { opacity:1; transform:none; } }
      @keyframes cn-stamp { 0% { opacity:0; transform:rotate(-4deg) scale(1.6); } 60% { opacity:1; transform:rotate(-4deg) scale(.96); } 100% { opacity:1; transform:rotate(-4deg) scale(1); } }
      .js .cn-nav { align-items:center; justify-content:center; gap:10px; padding:0 12px; }
      .cn-nav button { width:46px; height:46px; border-radius:50%; border:1.5px solid var(--line); background:var(--elev); color:var(--ink);
        font-size:18px; cursor:pointer; flex:none; }
      .cn-nav button:disabled { opacity:.35; cursor:default; }
      .cn-dots { display:flex; gap:5px; flex-wrap:wrap; justify-content:center; max-width:260px; }
      .cn-dots button { width:12px; height:12px; min-width:0; border-radius:50%; padding:0; border:1.5px solid #cdbf9f; background:transparent; }
      .cn-dots button.seen { background:var(--forest); border-color:var(--forest); }
      .cn-dots button.on { box-shadow:0 0 0 3px var(--amber); }
      .js button:focus-visible, .js [role="checkbox"]:focus-visible, .js .cn-fold:focus-visible { outline:3px solid var(--amber); outline-offset:2px; }
      /* 퀴즈(상호작용) */
      .js .cn-opts button { cursor:pointer; display:flex; gap:8px; align-items:flex-start; }
      .js .cn-opts button::before { content:"○"; font-weight:800; color:var(--ink3); flex:none; }
      .js .cn-opts button.right { border-color:var(--forest); background:#e3f2e9; font-weight:700; }
      .js .cn-opts button.right::before { content:"✓"; color:var(--forest-d); }
      .js .cn-opts button.wrong { border-style:dashed; color:var(--ink3); }
      .js .cn-opts button.wrong::before { content:"✕"; }
      .js .cn-quiz:not(.solved) .cn-exp { display:none; }
      .js .cn-quiz .cn-try { font-size:12px; font-weight:700; margin-top:8px; color:var(--amber-ink); min-height:1em; }
      .js .cn-quiz.light .cn-try { color:#ffe1a8; }
      /* 내 실천 체크 */
      .js .cn-check-t { display:flex; justify-content:space-between; align-items:center; font-size:12px; font-weight:800; color:var(--forest-d); margin-top:12px; }
      .js [data-check] .tips { margin-top:8px; }
      .js [data-check] .tips li { cursor:pointer; position:relative; padding-left:38px; user-select:none; -webkit-user-select:none; }
      .js [data-check] .tips li::before { content:""; position:absolute; left:11px; top:10px; width:16px; height:16px; border-radius:5px; border:2px solid var(--forest); background:var(--elev); }
      .js [data-check] .tips li[aria-checked="true"] { background:#e3f2e9; }
      .js [data-check] .tips li[aria-checked="true"]::before { background:var(--forest); }
      .js [data-check] .tips li[aria-checked="true"]::after { content:"✓"; position:absolute; left:14px; top:8px; color:#fff; font-size:13px; font-weight:900; }
      /* 눌러서 펼치기 — 배경 설명만 접는다. 내용은 DOM 에 그대로 있어 보조기기·인쇄에서는 전부 읽힌다 */
      .js .cn-fold { cursor:pointer; position:relative; padding:9px 30px 9px 12px; border-radius:10px; background:#f7f1e3; border:1px solid var(--line); margin-top:8px; }
      .js .cn-fold::after { content:"＋"; position:absolute; right:10px; top:8px; font-weight:800; color:var(--amber-ink); }
      .js .cn-fold.open::after { content:"－"; }
      .js .cn-fold:not(.open) { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      @media (prefers-reduced-motion: reduce) {
        .js .card, .cn-bar i { transition:none; }
        .js .card:not(.in) > div > * { opacity:1; }
        .js .card.in > div > * { animation:none; }
      }
      @media print {
        body, .js body { display:block; padding:0; background:#fff; min-height:0; }
        .cn-ui, .js .cn-ui, .hint, .cn-try { display:none !important; }
        .deck, .js .deck { display:block; overflow:visible; padding:0; }
        .card, .js .card { display:block; width:auto; height:auto; min-height:0; margin:0 0 12px; box-shadow:none; transform:none !important; opacity:1 !important; break-inside:avoid; page-break-inside:avoid; }
        .card > div, .js .card > div, .section { overflow:visible !important; height:auto; }
        .card > div > *, .js .card > div > * { opacity:1 !important; animation:none !important; }
        .js .cn-fold { white-space:normal !important; overflow:visible !important; }
        .js .cn-quiz .cn-exp { display:block !important; }
        .cn-more { display:none !important; }
        .section, .card > .section { background-image:none !important; }
        * { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
      }
    """
    # '내 실천 체크' — 퀘스트·클래식 카드뉴스가 함께 쓰는 고정 스크립트(환자 값·언어와 무관 — CSP 가 해시로 허용한다).
    # 저장한 HTML 파일을 직접 열면 선택은 그 브라우저의 localStorage 에 남는다. 앱 안에서는 sandbox iframe(불투명 출처)이라
    # localStorage 가 막히므로 부모 창(앱)이 대신 들고 있는다: 체크 목록이 있는 카드를 알리고(hello), 바뀔 때마다 보내고(set),
    # 부모가 돌려준 상태(state)를 반영한다. 메시지 틀은 web/shared.js 의 DeckBridge 와 같다.
    # 부모가 보낸 메시지만 받고(event.source === parent), 아는 항목의 불리언 값만 쓴다 — 받은 내용을 HTML 로 넣거나 실행하지 않는다.
    _CHECK_JS = """
function cnChecklist(deck, labelText) {
  var doc = document, known = {}, cards = {}, paints = [];
  var KEY = 'cardnews:' + doc.title + ':';
  var store = { get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
                set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) {} } };
  var framed = false; try { framed = !!window.parent && window.parent !== window; } catch (e) {}
  function post(m) { if (!framed) return; m.source = 'allergy-cardnews'; m.v = 1; try { window.parent.postMessage(m, '*'); } catch (e) {} }
  [].forEach.call(deck.querySelectorAll('.card[data-check]'), function (card) {
    var list = card.querySelector('.tips'), ci = card.getAttribute('data-i') || '';
    if (!list || !/^[1-9][0-9]{0,2}$/.test(ci)) return;
    var items = [].slice.call(list.children, 0, 40); if (!items.length) return;
    var head = doc.createElement('div'); head.className = 'cn-check-t';
    var label = doc.createElement('span'), count = doc.createElement('span'); label.textContent = labelText; head.appendChild(label); head.appendChild(doc.createTextNode(' ')); head.appendChild(count);
    list.parentNode.insertBefore(head, list);
    function paint() { var n = items.filter(function (li) { return li.getAttribute('aria-checked') === 'true'; }).length; count.textContent = n + ' / ' + items.length; }
    items.forEach(function (li, k) {
      var id = ci + ':' + k;
      li.setAttribute('role', 'checkbox'); li.tabIndex = 0;
      li.setAttribute('aria-checked', store.get(KEY + id) === '1' ? 'true' : 'false');
      function toggle() { var on = li.getAttribute('aria-checked') !== 'true'; li.setAttribute('aria-checked', on ? 'true' : 'false'); store.set(KEY + id, on ? '1' : '0'); post({ type: 'set', id: id, on: on }); paint(); }
      li.addEventListener('click', toggle);
      li.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggle(); } });
      known[id] = li;
    });
    cards[ci] = items.length; paints.push(paint); paint();
  });
  if (!framed || !paints.length) return;
  var has = Object.prototype.hasOwnProperty;
  window.addEventListener('message', function (e) {
    if (e.source !== window.parent) return;
    var d = e.data; if (!d || typeof d !== 'object' || d.source !== 'allergy-app' || d.v !== 1 || d.type !== 'state') return;
    var c = d.checks; if (!c || typeof c !== 'object' || Array.isArray(c)) return;
    for (var id in known) { if (has.call(known, id) && has.call(c, id) && typeof c[id] === 'boolean') known[id].setAttribute('aria-checked', c[id] ? 'true' : 'false'); }
    paints.forEach(function (f) { f(); });
  });
  post({ type: 'hello', cards: cards });
}
"""
    _DECK_JS = """
(function () {
  var doc = document, root = doc.documentElement, deck = doc.getElementById('deck');
  if (!deck || !deck.querySelectorAll) return;
  var cards = [].slice.call(deck.querySelectorAll('.card')); if (!cards.length) return;
  root.className += ' js';
  var T = {}; [].forEach.call(doc.querySelectorAll('#cnText [data-k]'), function (e) { T[e.getAttribute('data-k')] = e.textContent; });
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var cur = -1, want = 0, wantAt = 0, seen = {}, solved = 0;
  var elI = doc.getElementById('cnI'), elQ = doc.getElementById('cnQ'), bar = doc.querySelector('.cn-bar'), fill = doc.querySelector('.cn-bar i');
  var prev = doc.getElementById('cnPrev'), next = doc.getElementById('cnNext'), dotsBox = doc.getElementById('cnDots'), done = doc.getElementById('cnDone');
  var dots = cards.map(function (c, i) {
    var b = doc.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', (i + 1) + ' / ' + cards.length);
    b.onclick = function () { go(i); }; dotsBox.appendChild(b); return b;
  });
  function go(i) {
    i = Math.max(0, Math.min(cards.length - 1, i));
    want = i; wantAt = Date.now();   // 부드럽게 넘기는 중에 또 누르면 그다음 카드로 이어 간다
    var c = cards[i];
    deck.scrollTo({ left: c.offsetLeft - (deck.clientWidth - c.offsetWidth) / 2, behavior: reduce ? 'auto' : 'smooth' });
  }
  function sync() {
    var mid = deck.scrollLeft + deck.clientWidth / 2, best = 0, bestD = 1e9;
    cards.forEach(function (c, i) {
      var d = Math.abs(c.offsetLeft + c.offsetWidth / 2 - mid);
      if (d < bestD) { bestD = d; best = i; }
    });
    if (Date.now() - wantAt > 700) want = best;   // 손으로 넘긴 경우
    // 화면에 조금이라도 걸친 카드는 내용을 드러낸다(가운데 카드는 덱 너비를 못 재는 순간에도 드러낸다)
    var left = deck.scrollLeft, right = left + deck.clientWidth;
    cards.forEach(function (c, i) { if (i === best || (c.offsetLeft < right && c.offsetLeft + c.offsetWidth > left)) c.classList.add('in'); });
    if (best === cur) return;
    cur = best; seen[cur] = true;
    cards.forEach(function (c, i) { c.classList.toggle('active', i === cur); if (i === cur) c.classList.add('seen'); });
    dots.forEach(function (d, i) { d.classList.toggle('on', i === cur); d.classList.toggle('seen', !!seen[i]); if (i === cur) d.setAttribute('aria-current', 'true'); else d.removeAttribute('aria-current'); });
    var n = Object.keys(seen).length;
    elI.textContent = cur + 1; fill.style.transform = 'scaleX(' + (n / cards.length) + ')';
    bar.setAttribute('aria-valuenow', n);
    prev.disabled = cur === 0; next.disabled = cur === cards.length - 1;
    if (done) done.hidden = n < cards.length;
  }
  var raf = 0;
  deck.addEventListener('scroll', function () { if (raf) return; raf = requestAnimationFrame(function () { raf = 0; sync(); }); });
  window.addEventListener('resize', sync);
  prev.onclick = function () { go(want - 1); }; next.onclick = function () { go(want + 1); };
  doc.addEventListener('keydown', function (e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); go(want + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); go(want - 1); }
    else if (e.key === 'Home') { e.preventDefault(); go(0); }
    else if (e.key === 'End') { e.preventDefault(); go(cards.length - 1); }
  });
  function keyAct(el, fn) {
    el.addEventListener('click', fn);
    el.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); fn(e); } });
  }

  // 긴 카드: 본문이 카드 높이를 넘으면 '아래로 더 있어요' 알약을 띄우고, 누르면 한 화면 내린다.
  // 접힌 설명을 펼치거나 창 크기가 바뀌면 다시 잰다. 끝까지 내리면 사라진다.
  var moreChecks = cards.map(function (card) {
    var box = card.firstElementChild; if (!box) return function () {};
    var b = doc.createElement('button'); b.type = 'button'; b.className = 'cn-more'; b.textContent = T.more || '↓';
    b.onclick = function () { box.scrollBy({ top: Math.max(120, box.clientHeight - 80), behavior: reduce ? 'auto' : 'smooth' }); };
    card.appendChild(b);
    function check() { card.classList.toggle('more', box.scrollHeight - box.scrollTop - box.clientHeight > 12); }
    box.addEventListener('scroll', check);
    return check;
  });
  function checkMore() { moreChecks.forEach(function (f) { f(); }); }
  window.addEventListener('resize', checkMore);
  deck.addEventListener('click', function () { setTimeout(checkMore, 60); });
  deck.addEventListener('keyup', function () { setTimeout(checkMore, 60); });

  // 확인 퀴즈: 보기 목록을 버튼으로 바꾼다. 틀려도 다시 고를 수 있고, 맞히면 해설이 열린다.
  [].forEach.call(deck.querySelectorAll('.cn-quiz'), function (q) {
    var note = doc.createElement('p'); note.className = 'cn-try'; note.setAttribute('aria-live', 'polite');
    [].forEach.call(q.querySelectorAll('.cn-opts li'), function (li) {
      var b = doc.createElement('button'); b.type = 'button'; b.textContent = li.textContent; var ok = li.getAttribute('data-ok') === '1';
      li.textContent = ''; li.removeAttribute('data-ok'); li.style.cssText = 'padding:0;border:0;background:none'; li.appendChild(b);
      b.onclick = function () {
        if (q.classList.contains('solved')) return;
        if (ok) { b.classList.add('right'); q.classList.add('solved'); note.textContent = T.right || ''; solved++; elQ.textContent = solved;
          [].forEach.call(q.querySelectorAll('button'), function (x) { if (x !== b) x.disabled = true; }); }
        else { b.classList.add('wrong'); b.disabled = true; note.textContent = T.wrong || ''; }
      };
    });
    q.insertBefore(note, q.querySelector('.cn-exp'));
  });

  // 내 실천 체크: 수칙 목록을 체크 항목으로(위 cnChecklist — 클래식 카드뉴스와 함께 쓴다).
  cnChecklist(deck, T.check || '');

  // 눌러서 펼치기: 배경 설명 문단만. 굵은 머리말이 있는 문단을 한 줄로 접어 두고 누르면 펼친다.
  var folds = [];
  [].forEach.call(deck.querySelectorAll('.card[data-fold] .desc'), function (p) {
    if (!p.querySelector('b') || p.textContent.length < 60) return;
    p.classList.add('cn-fold'); p.setAttribute('role', 'button'); p.tabIndex = 0; p.setAttribute('aria-expanded', 'false');
    keyAct(p, function () { var on = p.classList.toggle('open'); p.setAttribute('aria-expanded', on ? 'true' : 'false'); });
    folds.push(p);
  });
  var openAll = doc.getElementById('cnOpen');
  if (openAll) {
    if (!folds.length) openAll.hidden = true;
    openAll.onclick = function () {
      var on = !folds.every(function (p) { return p.classList.contains('open'); });
      folds.forEach(function (p) { p.classList.toggle('open', on); p.setAttribute('aria-expanded', on ? 'true' : 'false'); });
      openAll.textContent = on ? (T.closeAll || '') : (T.openAll || '');
    };
  }
  sync();
  checkMore(); setTimeout(checkMore, 700);   // 글꼴이 늦게 실리면 높이가 달라진다
})();
"""

    def _wrap(self, cards_html: str, name: str) -> str:
        n_cards = cards_html.count('aria-roledescription="slide"')
        n_quiz = cards_html.count('class="cn-quiz-t"')
        return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(name)}님 알레르기 카드뉴스</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap"/>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css"/>
<style>
  {self._MONTH_STRIP_CSS}
  {self._KNOWLEDGE_CSS}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{ --cream:#fbf7ee; --elev:#fffdf8; --ink:#1f2a24; --ink2:#4d5a52; --ink3:#5f6d63; --line:#e6dcc6;
           --forest:#2f8f5b; --forest-d:#1f5f3f; --amber:#f2a33a; --amber-soft:#fdeed6; --amber-ink:#8a5a12;
           --red:#a83616; --red-soft:#fde9e2; --slate:#4a5768; --slate-soft:#eef1f5; --indet:#8a5405; --indet-soft:#fdf3e3;
           --display:'Do Hyeon','Pretendard','Apple SD Gothic Neo','Noto Sans KR',sans-serif; }}
  body {{ font-family:'Pretendard','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif; color:var(--ink); padding:16px;
         background:var(--cream) radial-gradient(rgba(47,143,91,.07) 1px, transparent 1px); background-size:22px 22px; }}
  h1,h2 {{ font-family:var(--display); font-weight:400; }}
  /* 스크립트가 없을 때(메일 미리보기·스크립트 차단)는 카드가 내용만큼 자란다 — 잘리는 내용도, 숨은 스크롤도 없다.
     스크립트가 켜지면 아래 .js 규칙이 넘겨 보는 고정 높이 카드로 바꾸고 스크롤 단서를 붙인다. */
  .deck {{ display:flex; align-items:flex-start; gap:16px; overflow-x:auto; padding:8px 2px 20px; scroll-snap-type:x mandatory; }}
  .card {{ flex:0 0 auto; width:340px; min-height:440px; display:flex; flex-direction:column; border-radius:22px; overflow:hidden; scroll-snap-align:center;
          box-shadow:0 12px 32px rgba(60,48,20,.16); background:var(--elev); position:relative; border:1px solid var(--line); }}
  .card > div {{ flex:1 0 auto; padding:26px 24px; display:flex; flex-direction:column; position:relative; }}
  .card > div::before {{ content:''; position:absolute; inset:9px; border:1.5px dashed rgba(0,0,0,.12); border-radius:15px; pointer-events:none; }}
  /* 표지 */
  .cover {{ background:linear-gradient(150deg,var(--forest-d) 0%,var(--forest) 60%,#6fb98a 100%); color:#fff; justify-content:space-between; }}
  .cover::before {{ border-color:rgba(255,255,255,.35) !important; }}
  .badge {{ font-size:11px; letter-spacing:2px; font-weight:800; opacity:.9; }}
  .cover h1 {{ font-size:32px; line-height:1.2; margin-top:8px; }}
  .cover .sub {{ font-size:13px; opacity:.92; margin-top:10px; line-height:1.5; }}
  .cover-stat {{ display:flex; gap:10px; margin-top:auto; }}
  .cover-stat > div {{ flex:1; background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.3); border-radius:14px; padding:12px 6px; text-align:center; }}
  .cover-stat .num {{ display:block; font-family:var(--display); font-size:30px; line-height:1; }}
  .cover-stat .lbl {{ display:block; font-size:11px; opacity:.95; margin-top:4px; font-weight:700; }}
  .cover .headline {{ margin-top:14px; font-size:14px; font-weight:700; background:rgba(0,0,0,.16); padding:10px 12px; border-radius:12px; }}
  /* 섹션 */
  .section {{ background:var(--elev); }}
  .tag {{ align-self:flex-start; font-size:12px; font-weight:800; padding:6px 12px; border-radius:999px; background:var(--amber-soft); color:var(--amber-ink); }}
  .stamp-verdict {{ align-self:flex-start; font-family:var(--display); font-size:17px; letter-spacing:1px; padding:5px 12px;
                    border:2.5px solid currentColor; border-radius:8px; transform:rotate(-4deg); margin:2px 0 4px 2px; }}
  .stamp-verdict.relevant {{ color:var(--red); }} .stamp-verdict.sensitized {{ color:var(--slate); }} .stamp-verdict.indet {{ color:var(--indet); }}
  .stamp-verdict.small {{ font-size:14px; margin-top:16px; }}
  .cat-stamp {{ display:inline-grid; place-items:center; width:22px; height:22px; border-radius:50%; background:var(--elev); border:1.5px solid var(--line); color:var(--forest-d); vertical-align:middle; margin-right:2px; }}
  .cat-stamp svg {{ width:16px; height:16px; }}
  .detail .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .treatment .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .prevention .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .oas .tag {{ background:var(--red-soft); color:var(--red); }}
  .oas .chip {{ background:#fff5f0; border-color:#f6c3b3; }}
  .detail .desc b, .treatment .tips b {{ color:var(--ink); }}
  .imt {{ margin-top:12px; font-size:12.5px; background:#e3f2e9; color:var(--forest-d); border-radius:10px; padding:10px 12px; line-height:1.5; }}
  .section {{ overflow-y:auto; }}  /* 항목이 늘어도 어떤 카드든 잘리지 않고 스크롤 */
  /* 스크롤 단서(고정 높이 카드일 때) — 아래(위)에 내용이 더 있을 때만 가장자리에 그림자가 생기고,
     끝에 닿으면 본문과 함께 움직이는 가림막(local)이 그림자를 덮는다. 고정 높이 카드에서 긴 본문이
     잘린 것처럼 보이던 문제(동물 카드: 538px 카드에 1,069px)를 눈에 보이게 한다. */
  .card > .section {{
    background-image:
      linear-gradient(var(--elev) 40%, rgba(255,253,248,0)), linear-gradient(rgba(255,253,248,0), var(--elev) 60%),
      radial-gradient(farthest-side at 50% 0, rgba(31,42,36,.38), rgba(31,42,36,0)),
      radial-gradient(farthest-side at 50% 100%, rgba(31,42,36,.38), rgba(31,42,36,0));
    background-repeat:no-repeat; background-position:0 0, 0 100%, 0 0, 0 100%;
    background-size:100% 44px, 100% 44px, 100% 16px, 100% 16px;
    background-attachment:local, local, scroll, scroll;
    scrollbar-width:thin; scrollbar-color:var(--forest) transparent; }}
  .card > .section::-webkit-scrollbar {{ width:6px; }}
  .card > .section::-webkit-scrollbar-thumb {{ background:var(--forest); border-radius:3px; }}
  .section h2 {{ font-size:26px; line-height:1.25; margin:12px 0 10px; }}
  .desc {{ font-size:13px; line-height:1.6; color:var(--ink2); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; overflow:visible; }}
  /* 음식 경고 카드: 항목이 많아도 한눈에 보이도록 2열 그리드로 표기 */
  .oas .chips {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; overflow:visible; }}
  .chip {{ font-size:13px; background:#f7f1e3; border:1px solid var(--line); border-radius:12px; padding:8px 12px; }}
  .chip-season {{ display:inline-block; margin-left:6px; font-size:11px; color:var(--ink3); }}
  .relevant .chip {{ background:var(--red-soft); border-color:#f6c3b3; }}
  .sensitized .chip {{ background:var(--slate-soft); }}
  .mini-title {{ font-size:12px; font-weight:800; color:var(--amber-ink); margin-top:16px; }}
  .empty {{ font-size:13px; color:var(--ink3); margin-top:16px; line-height:1.6; }}
  .tips {{ list-style:none; margin-top:12px; display:flex; flex-direction:column; gap:9px; }}
  .tips li {{ font-size:13px; line-height:1.5; background:#f4faf6; border-left:4px solid var(--forest); padding:9px 12px; border-radius:8px; }}
  /* 마무리 */
  .closing {{ background:linear-gradient(150deg,#1f2a24 0%,#2b3a44 100%); color:#fff; justify-content:center; }}
  .closing::before {{ border-color:rgba(255,255,255,.3) !important; }}
  .closing h2 {{ font-size:28px; }}
  .tag.light {{ background:rgba(255,255,255,.2); color:#fff; }}
  .desc.light {{ color:#e7ece8; }}
  .note {{ margin-top:auto; font-size:11px; color:#c3cdc6; line-height:1.5; }}
  .hint {{ text-align:center; color:var(--ink3); font-size:12px; margin-top:6px; }}
  {self._DECK_CSS}
</style></head>
<body>
  <div class="cn-top cn-ui">
    <div class="cn-bar" role="progressbar" aria-valuemin="0" aria-valuemax="{n_cards}" aria-valuenow="0"><i></i></div>
    <div class="cn-meta">
      <span><span class="cn-num" id="cnI">1</span> / {n_cards} <span class="sr">카드</span></span>
      {f'<span>퀴즈 <span class="cn-num" id="cnQ">0</span> / {n_quiz}</span>' if n_quiz else '<span hidden><span id="cnQ">0</span></span>'}
      <span class="cn-done" id="cnDone" hidden>모든 카드를 읽었어요</span>
      <button type="button" id="cnOpen">설명 모두 펼치기</button>
    </div>
  </div>
  <div class="deck" id="deck" tabindex="0">{cards_html}</div>
  <div class="cn-nav cn-ui">
    <button type="button" id="cnPrev"><span aria-hidden="true">←</span><span class="sr">이전 카드</span></button>
    <div class="cn-dots" id="cnDots"></div>
    <button type="button" id="cnNext"><span aria-hidden="true">→</span><span class="sr">다음 카드</span></button>
  </div>
  <p class="hint">← 좌우로 넘겨 보세요 · <span class="cn-js">긴 카드는 카드 안에서 위아래로 넘겨 보세요 · </span>캡처하여 공유할 수 있습니다 →</p>
  <div id="cnText" hidden><span data-k="more">↓ 아래로 더 있어요</span><span data-k="right">맞아요!</span><span data-k="wrong">다시 골라 보세요.</span><span data-k="check">내 실천 체크</span><span data-k="openAll">설명 모두 펼치기</span><span data-k="closeAll">설명 모두 접기</span></div>
  <script>{self._CHECK_JS}{self._DECK_JS}</script>
</body></html>"""

    def save_html(self, html_str: str, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_str)


_cardnews_service: Optional[CardNewsService] = None


def get_cardnews_service() -> CardNewsService:
    global _cardnews_service
    if _cardnews_service is None:
        _cardnews_service = CardNewsService()
    return _cardnews_service
