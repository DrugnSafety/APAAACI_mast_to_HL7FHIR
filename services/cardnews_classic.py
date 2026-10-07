"""
Card News Service (CLASSIC) — 재설계 이전 디자인·문구의 카드뉴스

`/classic/` UI 전용. 커밋 d5eb5f4 시점 구현을 그대로 보존한다(비교 기준선).
원본: services/cardnews_service.py (현재는 도감 테마)

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
    NO_RELEVANT_NOTE_KO, NO_RELEVANT_TIPS_KO, allocated_tips, animal_guidance)
from services import care_guidance_service as care_guidance
from services.cardnews_service import CardNewsService

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


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""))


class ClassicCardNewsService:
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
        # 문진에서 답한 동반 질환·주증상, 거주 지역 기준 계절성 — 퀘스트 카드뉴스와 같은 카드를 쓴다.
        # (클래식 카드뉴스는 문진을 치료 카드에만 쓰고 있어, 환자가 답한 내용 대부분이 빠졌다)
        from services.cardnews_service import get_cardnews_service
        quest = get_cardnews_service()
        profile_card = quest._profile_card(screening, relevant, getattr(result, "test_type", None))
        if profile_card:
            cards.append(profile_card)
        cards.append(self._relevant_card(relevant, screening))
        # 실제 주의 알러젠별 상세 카드 (Df/Dp 등은 그룹으로 1회만 — A1)
        for a in self._collapse(relevant)[:4]:
            cards.append(self._allergen_detail_card(a))
        cards.extend(quest._animal_cards(relevant, screening))
        # 🍽️ 주의할 음식 카드 — OAS·교차반응을 '음식 중심'으로 통합(D, 주객전도 수정)
        food_card = self._food_alert_card(result.assessments)
        if food_card:
            cards.append(food_card)
        # 약물은 '실제 주의'에도 '감작만'에도 넣지 않는다 — '진료 확인 필요' 카드 한 장(퀘스트 카드뉴스와 같은 내용)
        drug_card = care_guidance.drug_review_card(result.assessments, screening)
        if drug_card:
            cards.append(drug_card)
        cards.append(self._sensitized_card(sensitized, indeterminate, screening, result.assessments))
        # 감작만·관찰 필요 항원의 예방과 관찰(동물은 함께 사는지에 따라 따로)
        cards.extend(care_guidance.prevention_cards(result.assessments, screening))
        seasonality_card = quest._seasonality_card(result.assessments, screening)
        if seasonality_card:
            cards.append(seasonality_card)
        cards.append(self._prevention_card(relevant, screening))
        # 문진에서 고른 주증상·쓰는 약에 맞춘 카드(고르지 않은 것은 만들지 않는다)
        cards.extend(care_guidance.symptom_cards(result.assessments, screening))
        treatment_card = self._treatment_card(relevant, screening)
        if treatment_card:
            cards.append(treatment_card)
        cards.extend(care_guidance.medication_cards(result.assessments, screening))
        imt_card = quest._immunotherapy_card(result.assessments, screening)
        if imt_card:
            cards.append(imt_card)
        knowledge_card = quest._disease_knowledge_card(screening)
        if knowledge_card:
            cards.append(knowledge_card)
        cards.append(self._closing_card(name))

        cards_html = "\n".join(f'<div class="card">{c}</div>' for c in cards)
        return self._wrap(cards_html, name)

    # ---------- 개별 카드 ----------
    def _cover_card(self, name, test_date, relevant, sensitized, indeterminate, control_check=None,
                    review=None) -> str:
        # 검사 대조(히스타민·생리식염수)는 알러젠이 아니라 개수에 넣지 않는다 — 검사를 읽을 수 있는지만 한 줄로
        control_html = ""
        if control_check and control_check.get("line_ko"):
            icon = "⚠️" if control_check.get("status") == "caution" else "🧪"
            control_html = f'<p class="sub">{icon} {_esc(control_check["line_ko"])}</p>'
        headline = (
            f"실제 주의가 필요한 알러젠 <b>{len(relevant)}개</b>"
            if relevant
            else "실제 증상과 연관된 알러젠을 확인해 보세요"
        )
        return f"""
        <div class="cover">
          <div class="badge">ALLERGY REPORT · 카드뉴스</div>
          <h1>나의 알레르기<br/>검사 결과 요약</h1>
          <p class="sub">{_esc(name)}님 · 검사일 {_esc(test_date) or '-'}</p>
          <div class="cover-stat">
            <div><span class="num">{len(relevant)}</span><span class="lbl">실제 주의</span></div>
            <div><span class="num">{len(sensitized)}</span><span class="lbl">감작만</span></div>
            <div><span class="num">{len(indeterminate)}</span><span class="lbl">관찰 필요</span></div>
            {CardNewsService._review_stat(review)}
          </div>
          <p class="headline">{headline}</p>
          {control_html}
        </div>
        """

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
        emoji = _CATEGORY_EMOJI.get(a.category, "•")
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
        return f'<div class="chip">{emoji} <b>{nm}</b>{season_html}{sev_html}</div>'

    def _relevant_card(self, relevant: List[AllergenAssessment], screening=None) -> str:
        if not relevant:
            body = '<p class="empty">이번 문진에서는 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. 증상이 있을 때 노출 상황을 기록해 두면 도움이 됩니다.</p>'
        else:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(relevant))  # Df/Dp 등은 한 번만(A1)
            body = f'<div class="chips">{chips}</div>'
        return f"""
        <div class="section relevant">
          <div class="tag">🔴 실제 주의</div>
          <h2>증상을 유발하는<br/>알러젠</h2>
          <p class="desc">검사 양성이면서 <b>노출/해당 계절에 증상이 실제로 나타나는</b> 항목입니다. 회피와 관리가 가장 중요합니다.</p>
          {body}
        </div>
        """

    def _sensitized_card(self, sensitized, indeterminate, screening=None, assessments=None) -> str:
        from services.cardnews_service import CardNewsService
        desc = CardNewsService._sensitized_desc(assessments if assessments is not None else list(sensitized), screening)
        parts = []
        if sensitized:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(sensitized))  # Df/Dp 등은 한 번만(A1)
            parts.append(f'<div class="chips">{chips}</div>')
        else:
            parts.append('<p class="empty">감작만 된 항목은 없습니다.</p>')
        ind_html = ""
        if indeterminate:
            chips = "\n".join(self._chip(a, screening) for a in self._collapse(indeterminate))
            ind_html = f'<div class="mini-title">🟡 관찰 필요 (판정 보류)</div><div class="chips">{chips}</div>'
        return f"""
        <div class="section sensitized">
          <div class="tag">⚪ 감작만</div>
          <h2>검사만 양성,<br/>증상은 없는 항목</h2>
          <p class="desc">{desc}</p>
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
          <div class="tag">🍽️ 반드시 주의할 음식</div>
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

    def _treatment_card(self, relevant: List[AllergenAssessment], screening) -> str:
        """'치료와 연결하기' 카드 — 퀘스트 카드뉴스의 카드를 그대로 쓴다.

        예전에는 이 카드를 따로 써 두었는데, 퀘스트 쪽에만 질환별 줄(천식 조절제·피부 보습·아나필락시스 병력의
        응급 대처 계획과 에피네프린 자가주사기)이 더해져 클래식 카드뉴스에서는 그 안내가 빠졌다. 응급 안내가
        화면 테마에 따라 달라지면 안 되므로 한 곳에서 만든다."""
        from services.cardnews_service import get_cardnews_service
        return get_cardnews_service()._treatment_card(relevant, screening)

    @staticmethod
    def _short(text: str, n: int) -> str:
        # 글자수로 자르면 '온도 20~2…' 처럼 문장 중간에서 끊긴다 — 문장 경계에서만 줄인다
        return trim_sentences(text, n)

    def _closing_card(self, name) -> str:
        return f"""
        <div class="closing">
          <div class="tag light">✅ 정리</div>
          <h2>{_esc(name)}님,<br/>핵심은 '증상과의 연결'</h2>
          <p class="desc light">검사 양성은 <b>감작</b>을 뜻할 뿐입니다. 실제로 <b>노출될 때 증상이 나타나는 알러젠</b>에 집중해 관리하세요.</p>
          <p class="note">본 카드뉴스는 교육용 참고 자료이며, 정확한 진단·치료는 담당 의료진과 상담하세요.</p>
        </div>
        """

    # ---------- 래퍼(스타일) ----------
    # 넘기기 셸 — 꾸밈 없이 이전/다음 버튼·몇 번째 카드인지·긴 카드의 '아래로 더 있어요' 만 붙인다.
    # 고정 스크립트다(환자 값·언어와 무관 — CSP 가 해시로 허용한다). 스크립트가 없으면 카드는 내용만큼 자라 그대로 읽힌다.
    _SHELL_JS = """
(function () {
  var doc = document, deck = doc.getElementById('deck');
  if (!deck || !deck.querySelectorAll) return;
  var cards = [].slice.call(deck.querySelectorAll('.card')); if (!cards.length) return;
  doc.documentElement.className += ' js';
  var T = {}; [].forEach.call(doc.querySelectorAll('#cnText [data-k]'), function (e) { T[e.getAttribute('data-k')] = e.textContent; });
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var prev = doc.getElementById('cnPrev'), next = doc.getElementById('cnNext'), pos = doc.getElementById('cnI');
  var cur = -1, want = 0, wantAt = 0;
  function go(i) {
    i = Math.max(0, Math.min(cards.length - 1, i));
    want = i; wantAt = Date.now();   // 넘어가는 중에 또 누르면 그다음 카드로 이어 간다
    var c = cards[i];
    deck.scrollTo({ left: c.offsetLeft - (deck.clientWidth - c.offsetWidth) / 2, behavior: reduce ? 'auto' : 'smooth' });
  }
  function sync() {
    var mid = deck.scrollLeft + deck.clientWidth / 2, best = 0, bestD = 1e9;
    cards.forEach(function (c, i) { var d = Math.abs(c.offsetLeft + c.offsetWidth / 2 - mid); if (d < bestD) { bestD = d; best = i; } });
    if (Date.now() - wantAt > 700) want = best;   // 손으로 넘긴 경우
    if (best === cur) return;
    cur = best; pos.textContent = cur + 1;
    prev.disabled = cur === 0; next.disabled = cur === cards.length - 1;
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
  // 긴 카드: 본문이 카드 높이를 넘으면 '아래로 더 있어요' 버튼을 띄우고, 누르면 한 화면 내린다. 끝까지 내리면 사라진다.
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
  cnChecklist(deck, T.check || '');
  sync(); checkMore(); setTimeout(checkMore, 700);   // 글꼴이 늦게 실리면 높이가 달라진다
})();
"""

    def _wrap(self, cards_html: str, name: str) -> str:
        # 카드에 번호를 달고(넘기기·체크 저장용), 생활 수칙·동물 관리 카드에는 '내 실천 체크' 표시를 붙인다.
        # 카드 본문은 건드리지 않는다. 카드 종류 판별과 체크 스크립트는 퀘스트 카드뉴스의 것을 그대로 쓴다.
        from services.cardnews_service import CardNewsService
        bodies = cards_html.split('<div class="card">')
        total = len(bodies) - 1
        cards_html = bodies[0] + "".join(
            f'<div class="card" data-i="{i}" role="group" aria-roledescription="slide" aria-label="{i} / {total}"'
            + (' data-check="1"' if CardNewsService._card_kind(body) in CardNewsService._CHECK_KINDS else "")
            + f'>{body}' for i, body in enumerate(bodies[1:], 1))
        return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(name)}님 알레르기 카드뉴스</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Pretendard','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif;
         background:#eef1f7; padding:16px; color:#1f2430; }}
  /* 스크립트가 없을 때(메일 미리보기·스크립트 차단)는 카드가 내용만큼 자란다 — 잘리는 내용도, 숨은 스크롤도 없다.
     스크립트가 켜지면 아래 .js 규칙이 높이를 고정하고, 넘치는 본문은 카드 안에서 세로로 스크롤한다(잘리지 않는다). */
  .deck {{ display:flex; align-items:flex-start; gap:16px; overflow-x:auto; padding:8px 2px 20px; scroll-snap-type:x mandatory; }}
  .card {{ flex:0 0 auto; width:min(340px, calc(100vw - 48px)); min-height:440px; display:flex; flex-direction:column; border-radius:22px; overflow:hidden;
          scroll-snap-align:center; box-shadow:0 10px 30px rgba(40,50,90,.16); background:#fff; position:relative; }}
  .card > div {{ flex:1 0 auto; padding:26px 24px; display:flex; flex-direction:column; overflow-y:auto; }}
  /* 표지 */
  .cover {{ background:linear-gradient(150deg,#5b6ff0 0%,#8a4dd6 100%); color:#fff; justify-content:space-between; }}
  .badge {{ font-size:11px; letter-spacing:2px; font-weight:700; opacity:.85; }}
  .cover h1 {{ font-size:30px; line-height:1.25; margin-top:8px; font-weight:800; }}
  .cover .sub {{ font-size:14px; opacity:.9; margin-top:10px; }}
  .cover-stat {{ display:flex; gap:10px; margin-top:auto; }}
  .cover-stat > div {{ flex:1; background:rgba(255,255,255,.16); border-radius:14px; padding:12px 6px; text-align:center; }}
  .cover-stat .num {{ display:block; font-size:26px; font-weight:800; }}
  .cover-stat .lbl {{ display:block; font-size:11px; opacity:.9; margin-top:2px; }}
  .cover .headline {{ margin-top:14px; font-size:14px; font-weight:600; background:rgba(0,0,0,.14); padding:10px 12px; border-radius:12px; }}
  /* 섹션 */
  .section {{ background:#fff; }}
  .tag {{ align-self:flex-start; font-size:12px; font-weight:800; padding:6px 12px; border-radius:999px; }}
  .relevant .tag {{ background:#ffe3e3; color:#d1373a; }}
  .sensitized .tag {{ background:#eef0f4; color:#5a6472; }}
  .prevention .tag {{ background:#e4f5ec; color:#1f9d5b; }}
  .detail .tag {{ background:#e8ecfd; color:#4a4fe0; }}
  .treatment .tag {{ background:#e5f1fb; color:#1f6fb2; }}
  .oas .tag {{ background:#fdf3e3; color:#d9860a; }}
  .profile .tag {{ background:#e8f4fd; color:#1c6ea4; }}
  .seasonality .tag {{ background:#fff0e0; color:#c25a00; }}
  .section.profile, .section.seasonality, .section.knowledge {{ overflow-y:auto; }}
  .knowledge .tag {{ background:#eef3e8; color:#4f7a28; }}
  .knowledge .desc {{ margin-top:10px; }}
  .knowledge .src {{ font-size:11px; color:#8a8f98; margin-top:12px; line-height:1.5; }}
  .lead {{ font-size:13.5px; line-height:1.55; color:#3a4150; margin-top:4px; }}
  .month-strip {{ display:flex; gap:4px; flex-wrap:wrap; margin:14px 0; }}
  .month-strip .m {{ flex:1 1 0; min-width:22px; text-align:center; padding:7px 0; border-radius:7px;
    background:rgba(0,0,0,.05); font-size:12px; font-weight:700; color:#8a8f98; }}
  .month-strip .m.on {{ background:#ffd8a8; color:#7a4100; }}
  .month-strip .m.now {{ outline:2px solid #e8590c; outline-offset:1px; }}
  .oas .chip {{ background:#fff8ee; border-color:#f3dcae; }}
  .detail .desc b, .treatment .tips b {{ color:#2a3350; }}
  .imt {{ margin-top:12px; font-size:12.5px; background:#f0edff; color:#5a34c9; border-radius:10px; padding:10px 12px; line-height:1.5; }}
  .section.detail, .section.treatment, .section.oas {{ overflow-y:auto; }}
  .section h2 {{ font-size:24px; line-height:1.3; margin:14px 0 10px; font-weight:800; }}
  .desc {{ font-size:13px; line-height:1.6; color:#4a5060; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; overflow:visible; }}
  /* 음식 경고 카드: 항목이 많아도 한눈에 보이도록 2열 그리드로 표기 */
  .oas .chips {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; overflow:visible; }}
  .chip {{ font-size:13px; background:#f4f6fb; border:1px solid #e5e9f2; border-radius:12px; padding:8px 12px; }}
  .chip-season {{ display:inline-block; margin-left:6px; font-size:11px; color:#6b7280; }}
  .relevant .chip {{ background:#fff5f5; border-color:#ffd4d4; }}
  .mini-title {{ font-size:12px; font-weight:700; color:#b6842a; margin-top:16px; }}
  .empty {{ font-size:13px; color:#6b7280; margin-top:16px; line-height:1.6; }}
  .tips {{ list-style:none; margin-top:12px; display:flex; flex-direction:column; gap:9px; }}
  .tips li {{ font-size:13px; line-height:1.5; background:#f4faf6; border-left:4px solid #35c07d; padding:9px 12px; border-radius:8px; }}
  /* 마무리 */
  .closing {{ background:linear-gradient(150deg,#232a45 0%,#3a2d63 100%); color:#fff; justify-content:center; }}
  .closing h2 {{ font-size:26px; }}
  .tag.light {{ background:rgba(255,255,255,.2); color:#fff; }}
  .desc.light {{ color:#e7e9f5; }}
  .note {{ margin-top:auto; font-size:11px; color:#c3c6dd; line-height:1.5; }}
  .hint {{ text-align:center; color:#8a90a6; font-size:12px; margin-top:6px; }}
  /* 넘기기·체크 — 스크립트가 켜졌을 때만(.js) 보인다 */
  .cn-ui, .cn-more, .cn-js, .cn-check-t {{ display:none; }}
  .js .cn-js {{ display:inline; }}
  .js .deck {{ padding-left:calc(50% - min(170px, 50vw - 24px)); padding-right:calc(50% - min(170px, 50vw - 24px)); outline:none; }}
  .js .deck:focus-visible {{ box-shadow:inset 0 0 0 2px #5b6ff0; border-radius:12px; }}
  .js .card {{ height:min(540px, max(380px, calc(100vh - 150px))); min-height:0; scroll-snap-stop:always; }}
  .js .card > div {{ flex:1 1 0; min-height:0; scrollbar-width:thin; scrollbar-color:#b9c0d6 transparent; }}
  .js .card > div::-webkit-scrollbar {{ width:6px; }}
  .js .card > div::-webkit-scrollbar-thumb {{ background:#b9c0d6; border-radius:3px; }}
  .js .card.more .cn-more {{ display:block; position:absolute; left:50%; bottom:12px; transform:translateX(-50%); z-index:2;
    font:inherit; font-size:12px; font-weight:700; color:#fff; background:#3a4160; border:0; border-radius:999px; padding:7px 14px; min-height:32px; cursor:pointer; }}
  .js .cn-nav {{ display:flex; align-items:center; justify-content:center; gap:14px; margin-top:2px; }}
  .cn-nav button {{ font:inherit; font-size:13px; font-weight:700; color:#1f2430; background:#fff; border:1px solid #cfd5e6; border-radius:10px;
    padding:9px 14px; min-height:40px; cursor:pointer; }}
  .cn-nav button:disabled {{ opacity:.4; cursor:default; }}
  .cn-pos {{ font-size:13px; font-weight:700; color:#4a5060; min-width:56px; text-align:center; }}
  .js button:focus-visible, .js [role="checkbox"]:focus-visible {{ outline:2px solid #5b6ff0; outline-offset:2px; }}
  .js .cn-check-t {{ display:flex; justify-content:space-between; align-items:center; font-size:12px; font-weight:800; color:#1f9d5b; margin-top:12px; }}
  .js [data-check] .tips {{ margin-top:8px; }}
  .js [data-check] .tips li {{ cursor:pointer; position:relative; padding-left:38px; user-select:none; -webkit-user-select:none; }}
  .js [data-check] .tips li::before {{ content:""; position:absolute; left:11px; top:10px; width:16px; height:16px; border-radius:4px; border:2px solid #35c07d; background:#fff; }}
  .js [data-check] .tips li[aria-checked="true"] {{ background:#e4f5ec; }}
  .js [data-check] .tips li[aria-checked="true"]::before {{ background:#35c07d; }}
  .js [data-check] .tips li[aria-checked="true"]::after {{ content:"✓"; position:absolute; left:14px; top:8px; color:#fff; font-size:13px; font-weight:900; }}
  @media print {{
    body {{ background:#fff; padding:0; }}
    .cn-ui, .js .cn-ui, .cn-more, .hint {{ display:none !important; }}
    .deck, .js .deck {{ display:block; overflow:visible; padding:0; }}
    .card, .js .card {{ display:block; width:auto; height:auto; min-height:0; margin:0 0 12px; box-shadow:none; border:1px solid #d9dde8; break-inside:avoid; page-break-inside:avoid; }}
    .card > div, .js .card > div, .section {{ overflow:visible !important; height:auto; }}
    * {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }}
  }}
</style></head>
<body>
  <div class="deck" id="deck" tabindex="0">{cards_html}</div>
  <div class="cn-nav cn-ui">
    <button type="button" id="cnPrev">← 이전 카드</button>
    <span class="cn-pos"><span id="cnI">1</span> / {total}</span>
    <button type="button" id="cnNext">다음 카드 →</button>
  </div>
  <p class="hint">← 좌우로 넘겨 보세요 · <span class="cn-js">긴 카드는 카드 안에서 위아래로 넘겨 보세요 · </span>캡처하여 공유할 수 있습니다 →</p>
  <div id="cnText" hidden><span data-k="more">↓ 아래로 더 있어요</span><span data-k="check">내 실천 체크</span></div>
  <script>{CardNewsService._CHECK_JS}{self._SHELL_JS}</script>
</body></html>"""

    def save_html(self, html_str: str, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_str)


_classic_cardnews_service: Optional[ClassicCardNewsService] = None


def get_classic_cardnews_service() -> ClassicCardNewsService:
    global _classic_cardnews_service
    if _classic_cardnews_service is None:
        _classic_cardnews_service = ClassicCardNewsService()
    return _classic_cardnews_service
