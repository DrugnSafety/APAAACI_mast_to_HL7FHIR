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
        cards.append(self._cover_card(name, test_date, relevant, sensitized, indeterminate))
        cards.append(self._relevant_card(relevant))
        # 실제 주의 알러젠별 상세 카드 (Df/Dp 등은 그룹으로 1회만 — A1)
        for a in self._collapse(relevant)[:4]:
            cards.append(self._allergen_detail_card(a))
        # 🍽️ 주의할 음식 카드 — OAS·교차반응을 '음식 중심'으로 통합(D, 주객전도 수정)
        food_card = self._food_alert_card(result.assessments)
        if food_card:
            cards.append(food_card)
        cards.append(self._sensitized_card(sensitized, indeterminate))
        cards.append(self._prevention_card(relevant))
        cards.append(self._treatment_card(relevant, screening))
        cards.append(self._closing_card(name))

        cards_html = "\n".join(f'<div class="card">{c}</div>' for c in cards)
        return self._wrap(cards_html, name)

    # ---------- 개별 카드 ----------
    def _cover_card(self, name, test_date, relevant, sensitized, indeterminate) -> str:
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
          </div>
          <p class="headline">{headline}</p>
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

    def _chip(self, a: AllergenAssessment) -> str:
        nm = _esc(self._label(a))
        season = _esc((a.kb or {}).get("season_label_ko", ""))
        season_html = f'<span class="chip-season">{season}</span>' if season else ""
        sev = self._SEV_BADGE.get(getattr(a, "severity", None) or "", "")
        sev_html = f'<span class="chip-season"><b>{_esc(sev)}</b></span>' if sev else ""
        return f'<div class="chip">{_stamp(a.category)} <b>{nm}</b>{season_html}{sev_html}</div>'

    def _relevant_card(self, relevant: List[AllergenAssessment]) -> str:
        if not relevant:
            body = '<p class="empty">이번 문진에서는 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. 증상이 있을 때 노출 상황을 기록해 두면 도움이 됩니다.</p>'
        else:
            chips = "\n".join(self._chip(a) for a in self._collapse(relevant))  # Df/Dp 등은 한 번만(A1)
            body = f'<div class="chips">{chips}</div>'
        return f"""
        <div class="section relevant">
          <div class="stamp-verdict relevant">진범 확정</div>
          <h2>증상으로 확인된<br/>진짜 범인</h2>
          <p class="desc">검사 양성이면서 <b>노출/해당 계절에 증상이 실제로 나타나는</b> 항목입니다. 회피와 관리가 가장 중요합니다.</p>
          {body}
        </div>
        """

    def _sensitized_card(self, sensitized, indeterminate) -> str:
        parts = []
        if sensitized:
            chips = "\n".join(self._chip(a) for a in self._collapse(sensitized))  # Df/Dp 등은 한 번만(A1)
            parts.append(f'<div class="chips">{chips}</div>')
        else:
            parts.append('<p class="empty">감작만 된 항목은 없습니다.</p>')
        ind_html = ""
        if indeterminate:
            chips = "\n".join(self._chip(a) for a in self._collapse(indeterminate))
            ind_html = f'<div class="stamp-verdict indet small">관찰 대상</div><div class="chips">{chips}</div>'
        return f"""
        <div class="section sensitized">
          <div class="stamp-verdict sensitized">무혐의 · 감작만</div>
          <h2>검사만 양성,<br/>증상은 없는 항목</h2>
          <p class="desc">감작은 되어 있지만 <b>노출해도 증상이 없는</b> 항목입니다. 과도한 회피는 필요하지 않지만, <b>감작은 남아 있어 추적 필요</b>합니다. 증상이 새로 생기면 재평가하세요.</p>
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
        nm = _esc(self._label(a, detail=True))
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

    def _treatment_card(self, relevant: List[AllergenAssessment], screening) -> str:
        eligible = []
        ks = get_knowledge_service()
        for a in relevant:
            try:
                if ks.immunotherapy_info(a.category, a.allergen_name).get("eligible"):
                    eligible.append(self._label(a))
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
          <h2>{_esc(name)}님,<br/>탐험의 핵심은 '증상과의 연결'</h2>
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
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{ --cream:#fbf7ee; --elev:#fffdf8; --ink:#1f2a24; --ink2:#4d5a52; --ink3:#5f6d63; --line:#e6dcc6;
           --forest:#2f8f5b; --forest-d:#1f5f3f; --amber:#f2a33a; --amber-soft:#fdeed6; --amber-ink:#8a5a12;
           --red:#a83616; --red-soft:#fde9e2; --slate:#4a5768; --slate-soft:#eef1f5; --indet:#8a5405; --indet-soft:#fdf3e3;
           --display:'Do Hyeon','Pretendard','Apple SD Gothic Neo','Noto Sans KR',sans-serif; }}
  body {{ font-family:'Pretendard','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif; color:var(--ink); padding:16px;
         background:var(--cream) radial-gradient(rgba(47,143,91,.07) 1px, transparent 1px); background-size:22px 22px; }}
  h1,h2 {{ font-family:var(--display); font-weight:400; }}
  .deck {{ display:flex; gap:16px; overflow-x:auto; padding:8px 2px 20px; scroll-snap-type:x mandatory; }}
  .card {{ flex:0 0 auto; width:340px; height:440px; border-radius:22px; overflow:hidden; scroll-snap-align:center;
          box-shadow:0 12px 32px rgba(60,48,20,.16); background:var(--elev); position:relative; border:1px solid var(--line); }}
  .card > div {{ height:100%; padding:26px 24px; display:flex; flex-direction:column; position:relative; }}
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
  .treatment .tag {{ background:#e5f1fb; color:#1f6fb2; }}
  .prevention .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .oas .tag {{ background:var(--red-soft); color:var(--red); }}
  .oas .chip {{ background:#fff5f0; border-color:#f6c3b3; }}
  .detail .desc b, .treatment .tips b {{ color:var(--ink); }}
  .imt {{ margin-top:12px; font-size:12.5px; background:#e3f2e9; color:var(--forest-d); border-radius:10px; padding:10px 12px; line-height:1.5; }}
  .section.detail, .section.treatment, .section.oas {{ overflow-y:auto; }}
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
