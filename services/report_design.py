"""
Report Design — 맞춤 리포트 디자인 스킬 (단일 소스)

목적:
  화면 표시용 HTML 과 인쇄(PDF)용 문서를 '하나의 템플릿'으로 만들어, 두 버전이 항상
  동일하고 일관된 디자인을 갖도록 한다. (사용자 요구: PDF/HTML 2버전 + 보기 편하고 일관된 보고서)

설계 원칙(리포트 디자인 시스템):
  - A4 인쇄 기준. @page 여백, 표지 헤더, 상태 타일(실제주의/감작/관찰), 알러젠 카드.
  - 색 토큰: 실제주의=레드, 감작=그레이, 관찰=앰버 (앱 UI 와 동일 의미).
  - 카드/섹션은 page-break-inside:avoid 로 페이지 중간에서 잘리지 않게.
  - 자체 완결형(embedded CSS) → 다운로드한 .html 파일이 어디서나 동일하게 열림.
  - 화면·인쇄 모두 대응: 화면은 카드형 라이트 UI, 인쇄는 잉크 절약형 흑백 친화.

공개 함수:
  render_report_document(title, meta, summary, body_html, *, print_mode=False) -> str
"""

from typing import Any, Dict, List, Optional
from html import escape


BRAND = "#6d5efb"


def _meta_row(meta: Dict[str, Any]) -> str:
    items = []
    order = [
        ("name", "👤 이름"), ("test_date", "🗓️ 검사일"), ("facility", "🏥 검사기관"),
        ("age", "🎂 나이"), ("gender", "⚧ 성별"), ("report_date", "📄 보고일"),
    ]
    for key, label in order:
        v = meta.get(key)
        if v:
            items.append(
                f'<div class="meta-item"><span class="meta-k">{escape(label)}</span>'
                f'<span class="meta-v">{escape(str(v))}</span></div>')
    return "".join(items)


def _tiles(summary: Dict[str, Any]) -> str:
    c = (summary or {}).get("counts", {}) or {}
    defs = [
        ("relevant", "🔴 실제 주의", c.get("clinically_relevant", 0), "증상을 실제로 유발"),
        ("sensitized", "⚪ 감작만", c.get("sensitized_only", 0), "양성이나 과도한 회피 불필요"),
        ("indeterminate", "🟡 관찰 필요", c.get("indeterminate", 0), "추가 관찰로 판정"),
    ]
    cells = []
    for cls, label, n, sub in defs:
        cells.append(
            f'<div class="tile {cls}"><div class="tile-n">{n}</div>'
            f'<div class="tile-l">{escape(label)}</div>'
            f'<div class="tile-s">{escape(sub)}</div></div>')
    total = (summary or {}).get("total_positive", 0)
    return (f'<div class="tiles" role="group" aria-label="감별 요약">{"".join(cells)}</div>'
            f'<p class="tiles-cap">검사 양성 {total}개를 증상 문진과 대조해 감별했습니다. '
            f'양성(감작)이 곧 알레르기는 아닙니다 — 실제 증상 유발 여부로 구분합니다.</p>')


REPORT_CSS = """
:root{ --brand:#6d5efb; --ink:#1f2430; --ink-2:#4b5162; --ink-3:#8a90a2;
  --line:#e7e9f0; --bg:#ffffff; --soft:#f6f7fb;
  --rel:#e5484d; --rel-bg:#fdecec; --sens:#8a90a2; --sens-bg:#f2f3f7; --ind:#e0a400; --ind-bg:#fbf3da; }
*{ box-sizing:border-box; }
html,body{ margin:0; padding:0; }
body{ font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',system-ui,-apple-system,sans-serif;
  color:var(--ink); background:var(--soft); font-size:14.5px; line-height:1.7; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.doc{ max-width:820px; margin:0 auto; padding:28px 20px 60px; }
.sheet{ background:var(--bg); border:1px solid var(--line); border-radius:16px; overflow:hidden; box-shadow:0 8px 30px rgba(31,36,48,.06); }
.cover{ background:linear-gradient(135deg,var(--brand),#8f7bff); color:#fff; padding:30px 34px 26px; }
.cover .eyebrow{ font-size:12.5px; letter-spacing:.14em; text-transform:uppercase; opacity:.85; font-weight:700; }
.cover h1{ margin:6px 0 4px; font-size:26px; font-weight:800; letter-spacing:-.01em; }
.cover .sub{ opacity:.9; font-size:13.5px; }
.meta{ display:flex; flex-wrap:wrap; gap:8px 22px; margin-top:16px; }
.meta-item{ font-size:13px; }
.meta-k{ opacity:.82; font-weight:700; margin-right:6px; }
.meta-v{ font-weight:600; }
.body{ padding:26px 34px 34px; }
.tiles{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:2px 0 6px; }
.tile{ border:1px solid var(--line); border-radius:13px; padding:15px 14px; background:#fff; }
.tile-n{ font-size:30px; font-weight:800; line-height:1; }
.tile-l{ font-size:13.5px; font-weight:700; margin-top:7px; }
.tile-s{ font-size:11.5px; color:var(--ink-3); margin-top:3px; }
.tile.relevant{ background:var(--rel-bg); } .tile.relevant .tile-n{ color:var(--rel); }
.tile.sensitized{ background:var(--sens-bg); } .tile.sensitized .tile-n{ color:var(--sens); }
.tile.indeterminate{ background:var(--ind-bg); } .tile.indeterminate .tile-n{ color:#a97b00; }
.tiles-cap{ font-size:12.5px; color:var(--ink-2); background:var(--soft); border-radius:10px; padding:10px 13px; margin:12px 0 20px; }
.content h2{ font-size:18.5px; font-weight:800; margin:26px 0 10px; padding-bottom:7px; border-bottom:2px solid var(--line); letter-spacing:-.01em; page-break-after:avoid; }
.content h3{ font-size:15.5px; font-weight:700; margin:18px 0 8px; color:var(--ink); page-break-after:avoid; }
.content p{ margin:8px 0; color:var(--ink-2); }
.content ul,.content ol{ margin:8px 0 8px; padding-left:20px; }
.content li{ margin:5px 0; color:var(--ink-2); }
.content strong{ color:var(--ink); }
.content blockquote{ margin:12px 0; padding:11px 15px; background:var(--soft); border-left:4px solid var(--brand); border-radius:0 8px 8px 0; color:var(--ink-2); }
.content hr{ border:0; border-top:1px solid var(--line); margin:22px 0; }
.content table{ border-collapse:collapse; width:100%; margin:12px 0; font-size:13px; }
.content th,.content td{ border:1px solid var(--line); padding:8px 10px; text-align:left; }
.content th{ background:var(--soft); font-weight:700; }
.content h2,.content h3,.content ul,.content table,.content blockquote{ page-break-inside:avoid; }
.disclaimer{ margin-top:26px; padding:13px 16px; border:1px dashed var(--line); border-radius:10px; font-size:12px; color:var(--ink-3); background:var(--soft); }
.foot{ text-align:center; color:var(--ink-3); font-size:11.5px; margin-top:20px; }
.print-hint{ position:sticky; top:0; z-index:5; display:flex; gap:10px; align-items:center; justify-content:flex-end;
  background:#fff; border:1px solid var(--line); border-radius:12px; padding:9px 12px; margin:0 auto 14px; max-width:820px; }
.print-hint button{ font:inherit; font-weight:700; font-size:13px; cursor:pointer; border-radius:9px; padding:8px 14px; border:1px solid var(--line); background:#fff; color:var(--ink); }
.print-hint button.primary{ background:var(--brand); border-color:var(--brand); color:#fff; }
@media (max-width:640px){ .tiles{ grid-template-columns:1fr; } .cover,.body{ padding-left:20px; padding-right:20px; } }
@media print{
  @page{ size:A4; margin:14mm 12mm; }
  body{ background:#fff; font-size:11.5pt; }
  .doc{ padding:0; max-width:none; }
  .sheet{ border:0; border-radius:0; box-shadow:none; }
  .print-hint{ display:none !important; }
  .cover{ background:#fff !important; color:var(--ink) !important; border-bottom:3px solid var(--brand); padding:0 0 12px; }
  .cover .eyebrow{ color:var(--brand); opacity:1; }
  .cover .meta-k{ opacity:1; color:var(--ink-3); }
  .body{ padding:14px 0 0; }
  .tile{ box-shadow:none; }
}
"""


def render_report_document(
    title: str,
    meta: Dict[str, Any],
    summary: Dict[str, Any],
    body_html: str,
    *,
    print_hint: bool = True,
) -> str:
    """맞춤 리포트를 인쇄(PDF)·화면 겸용의 자체 완결형 HTML 문서로 렌더링."""
    name = meta.get("name") or "환자"
    test_date = meta.get("test_date") or ""
    hint = (
        '<div class="print-hint no-print">'
        '<span style="margin-right:auto;font-size:12.5px;color:#8a90a2">PDF로 저장하려면 인쇄 → 대상을 ‘PDF로 저장’</span>'
        '<button onclick="window.print()" class="primary">🖨️ PDF로 저장 / 인쇄</button>'
        '</div>'
    ) if print_hint else ""
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<style>{REPORT_CSS}</style></head>
<body>
<div class="doc">
{hint}
<div class="sheet">
  <header class="cover">
    <div class="eyebrow">알레르기 감별 리포트 · 감작 vs 실제 알레르기</div>
    <h1>{escape(name)}님의 맞춤 리포트</h1>
    <div class="sub">검사 결과를 증상과 대조해 실제 임상적 의미를 정리했습니다{(' · ' + escape(str(test_date))) if test_date else ''}.</div>
    <div class="meta">{_meta_row(meta)}</div>
  </header>
  <div class="body">
    {_tiles(summary)}
    <div class="content">
{body_html}
    </div>
    <div class="disclaimer">본 리포트는 교육용 참고 자료이며 의학적 진단·치료를 대체하지 않습니다.
      정확한 판단과 치료는 담당 의료진과 상담하세요. 약물 알레르기는 본 리포트 범위 밖입니다.</div>
    <div class="foot">APAAACI · MAST/SPT/UniCAP → 환자 맞춤 리포트 &amp; HL7 FHIR</div>
  </div>
</div>
</div>
</body></html>"""
