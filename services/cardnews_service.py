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

logger = logging.getLogger(__name__)

_CATEGORY_EMOJI = {
    "mite": "🛏️",
    "animal": "🐾",
    "pollen_tree": "🌳",
    "pollen_grass": "🌾",
    "pollen_weed": "🍂",
    "mold": "🍄",
    "insect": "🪳",
    "food": "🍽️",
    "other": "•",
}


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
        cards.append(self._cover_card(name, test_date, relevant, sensitized, indeterminate))
        cards.append(self._relevant_card(relevant))
        # 실제 주의 알러젠별 상세 카드 (생활사·노출·환경관리·면역치료)
        for a in relevant[:4]:
            cards.append(self._allergen_detail_card(a))
        # 구강알레르기증후군(OAS) 카드 (환자가 보고한 교차반응 음식이 있을 때)
        oas_items = [a for a in result.assessments if getattr(a, "oas_foods", None)]
        if oas_items:
            cards.append(self._oas_card(oas_items))
        cards.append(self._sensitized_card(sensitized, indeterminate))
        cards.append(self._prevention_card(relevant))
        cards.append(self._treatment_card(relevant, screening))
        cards.append(self._closing_card(name))

        cards_html = "\n".join(f'<div class="card">{c}</div>' for c in cards)
        return self._wrap(cards_html, name)

    # ---------- 개별 카드 ----------
    def _cover_card(self, name, test_date, relevant, sensitized, indeterminate) -> str:
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
          </div>
          <p class="headline">{headline}</p>
        </div>
        """

    def _chip(self, a: AllergenAssessment) -> str:
        emoji = _CATEGORY_EMOJI.get(a.category, "•")
        nm = _esc(a.korean_name or a.allergen_name)
        season = _esc((a.kb or {}).get("season_label_ko", ""))
        season_html = f'<span class="chip-season">{season}</span>' if season else ""
        return f'<div class="chip">{emoji} <b>{nm}</b>{season_html}</div>'

    def _relevant_card(self, relevant: List[AllergenAssessment]) -> str:
        if not relevant:
            body = '<p class="empty">이번 문진에서는 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. 증상이 있을 때 노출 상황을 기록해 두면 도움이 됩니다.</p>'
        else:
            chips = "\n".join(self._chip(a) for a in relevant)
            body = f'<div class="chips">{chips}</div>'
        return f"""
        <div class="section relevant">
          <div class="tag">🔴 실제 주의</div>
          <h2>증상을 유발하는<br/>알러젠</h2>
          <p class="desc">검사 양성이면서 <b>노출/해당 계절에 증상이 실제로 나타나는</b> 항목입니다. 회피와 관리가 가장 중요합니다.</p>
          {body}
        </div>
        """

    def _sensitized_card(self, sensitized, indeterminate) -> str:
        parts = []
        if sensitized:
            chips = "\n".join(self._chip(a) for a in sensitized)
            parts.append(f'<div class="chips">{chips}</div>')
        else:
            parts.append('<p class="empty">감작만 된 항목은 없습니다.</p>')
        ind_html = ""
        if indeterminate:
            chips = "\n".join(self._chip(a) for a in indeterminate)
            ind_html = f'<div class="mini-title">🟡 관찰 필요 (노출 시 확인)</div><div class="chips">{chips}</div>'
        return f"""
        <div class="section sensitized">
          <div class="tag">⚪ 감작만</div>
          <h2>검사만 양성,<br/>증상은 없는 항목</h2>
          <p class="desc">감작은 되어 있지만 <b>노출해도 증상이 없는</b> 항목입니다. 과도한 회피는 필요하지 않으며, 증상이 새로 생기면 재평가하세요.</p>
          {''.join(parts)}
          {ind_html}
        </div>
        """

    def _prevention_card(self, relevant: List[AllergenAssessment]) -> str:
        tips: List[str] = []
        seen = set()
        for a in relevant:
            for tip in (a.kb or {}).get("avoidance_control_ko", [])[:2]:
                key = tip.strip()
                if key and key not in seen:
                    seen.add(key)
                    tips.append(f"{_CATEGORY_EMOJI.get(a.category, '•')} {tip}")
            if len(tips) >= 6:
                break
        if not tips:
            tips = [
                "🛏️ 침구는 주 1회 55~60℃ 뜨거운 물로 세탁",
                "💧 실내 습도 50% 이하로 유지",
                "🌫️ 꽃가루 많은 날은 외출·환기 자제",
                "🧼 외출 후 샤워·세안으로 알러젠 제거",
            ]
        items = "\n".join(f"<li>{_esc(t)}</li>" for t in tips[:6])
        return f"""
        <div class="section prevention">
          <div class="tag">🛡️ 예방·관리</div>
          <h2>우선 실천할<br/>생활 수칙</h2>
          <ul class="tips">{items}</ul>
        </div>
        """

    def _allergen_detail_card(self, a: AllergenAssessment) -> str:
        kb = a.kb or {}
        emoji = _CATEGORY_EMOJI.get(a.category, "•")
        nm = _esc(a.korean_name or a.allergen_name)
        bio = _esc(self._short(kb.get("biology_ko", ""), 90))
        expo = _esc(self._short(kb.get("exposure_environment_ko", ""), 80))
        tips = [t for t in (kb.get("avoidance_control_ko") or [])[:3]]
        tips_html = "".join(f"<li>{_esc(t)}</li>" for t in tips)
        try:
            imt = get_knowledge_service().immunotherapy_info(a.category, a.allergen_name)
        except Exception:
            imt = {"eligible": False}
        imt_html = (f'<div class="imt">💉 면역치료(근본치료) 가능 대상 — 증상이 심하거나 약으로 조절이 '
                    f'어려우면 전문의와 상의하세요.</div>') if imt.get("eligible") else ""
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

    def _oas_card(self, oas_items: List[AllergenAssessment]) -> str:
        rows = "".join(
            f'<div class="chip">🌳 <b>{_esc(a.korean_name or a.allergen_name)}</b> '
            f'<span class="chip-season">↔ {_esc(", ".join(a.oas_foods))}</span></div>'
            for a in oas_items)
        return f"""
        <div class="section oas">
          <div class="tag">🍎 구강알레르기증후군</div>
          <h2>꽃가루와 엮인<br/>음식 주의</h2>
          <p class="desc">아래 꽃가루에 감작되어 있어, <b>교차반응</b>으로 특정 생과일·채소를 먹으면
          <b>입·입술·목이 가렵거나 붓는</b> 구강알레르기증후군(OAS)이 생길 수 있습니다.</p>
          <div class="chips">{rows}</div>
          <p class="desc" style="margin-top:12px">💡 대부분 <b>익히면 증상이 줄어듭니다.</b> 목·호흡기까지
          번지거나 심하면 즉시 진료를 받으세요.</p>
        </div>
        """

    def _treatment_card(self, relevant: List[AllergenAssessment], screening) -> str:
        eligible = []
        ks = get_knowledge_service()
        for a in relevant:
            try:
                if ks.immunotherapy_info(a.category, a.allergen_name).get("eligible"):
                    eligible.append(a.korean_name or a.allergen_name)
            except Exception:
                pass
        imt_line = (
            f'<li>💉 <b>면역치료 후보:</b> {_esc(", ".join(dict.fromkeys(eligible)))} — 원인 알러젠에 '
            f'대한 근본치료(설하/피하)를 전문의와 상의할 수 있어요.</li>' if eligible else ''
        )
        return f"""
        <div class="section treatment">
          <div class="tag">🩺 치료와 연결하기</div>
          <h2>기존 치료와<br/>어떻게 함께 갈까</h2>
          <ul class="tips">
            <li>💊 <b>증상 조절:</b> 항히스타민제·비강 스테로이드 등은 증상을 빠르게 줄여줍니다. 증상 시기에 맞춰 예방적으로 쓰면 더 효과적입니다.</li>
            <li>🛡️ <b>회피가 기본:</b> 실제 주의 알러젠의 노출을 줄이는 것이 약물 효과를 높입니다.</li>
            {imt_line}
            <li>🧑‍⚕️ <b>정기 점검:</b> 증상 변화·약물 반응을 담당 의료진과 3~12개월 간격으로 재평가하세요.</li>
          </ul>
        </div>
        """

    @staticmethod
    def _short(text: str, n: int) -> str:
        text = (text or "").strip()
        return text if len(text) <= n else text[:n].rstrip() + "…"

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
    def _wrap(self, cards_html: str, name: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(name)}님 알레르기 카드뉴스</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Pretendard','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif;
         background:#eef1f7; padding:16px; color:#1f2430; }}
  .deck {{ display:flex; gap:16px; overflow-x:auto; padding:8px 2px 20px; scroll-snap-type:x mandatory; }}
  .card {{ flex:0 0 auto; width:340px; height:440px; border-radius:22px; overflow:hidden;
          scroll-snap-align:center; box-shadow:0 10px 30px rgba(40,50,90,.16); background:#fff; position:relative; }}
  .card > div {{ height:100%; padding:26px 24px; display:flex; flex-direction:column; }}
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
  .oas .chip {{ background:#fff8ee; border-color:#f3dcae; }}
  .detail .desc b, .treatment .tips b {{ color:#2a3350; }}
  .imt {{ margin-top:12px; font-size:12.5px; background:#f0edff; color:#5a34c9; border-radius:10px; padding:10px 12px; line-height:1.5; }}
  .section.detail, .section.treatment {{ overflow-y:auto; }}
  .section h2 {{ font-size:24px; line-height:1.3; margin:14px 0 10px; font-weight:800; }}
  .desc {{ font-size:13px; line-height:1.6; color:#4a5060; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; overflow:auto; }}
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
</style></head>
<body>
  <div class="deck">{cards_html}</div>
  <p class="hint">← 좌우로 넘겨 보세요 · 캡처하여 공유할 수 있습니다 →</p>
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
