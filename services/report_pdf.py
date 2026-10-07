"""
Report PDF — 환자용 리포트(인쇄형 HTML 문서)를 PDF 로 만든다.

왜 WeasyPrint 인가
  리포트는 이미 '화면·인쇄 겸용 HTML 문서'(services/report_design.py) 한 벌로 만들어진다. WeasyPrint 는 그 HTML 과
  @media print 스타일을 그대로 조판하므로 표·타일·인용 상자가 화면과 같은 모양으로 나오고, PDF 용 문서를 따로
  유지할 필요가 없다. 헤드리스 브라우저 없이 파이썬 프로세스 안에서 돈다(시스템 라이브러리 Pango 가 필요하다 —
  Dockerfile 이 설치한다. macOS 는 `brew install pango`).
  검토한 다른 방법: reportlab·fpdf2 는 순수 파이썬이지만 HTML/CSS 를 조판하지 못해 리포트를 PDF 전용 코드로
  다시 그려야 하고(표·카드 모양이 화면과 어긋난다), 예전 utils/pdf_generator.py 의 reportlab 경로는 한글 글꼴을
  등록하지 않아 한글이 빈 상자로 나왔다.

글꼴
  한글·한자·가나를 모두 가진 Noto Sans CJK(SIL OFL 1.1 — 재배포·PDF 임베드 허용)를 쓴다. Dockerfile 이
  fonts-noto-cjk 를 설치한다. 중국어 리포트는 간체 자형(Noto Sans CJK SC)을 먼저 쓴다.
  글꼴이 없는 환경에서 빈 상자가 찍힌 PDF 를 내보내지 않도록, 조판에 한글·한자를 가진 글꼴이 실제로
  쓰였는지 확인하고 아니면 PdfUnavailable 을 낸다(메일은 HTML 만 보내고, 내려받기는 503 을 돌려준다).

이모지
  리포트 본문의 이모지는 컬러 이모지 글꼴이 있어야 그려진다. 서버 환경마다 그 글꼴의 유무·형식이 달라서
  (없으면 빈 상자, 있으면 파일이 수 MB 로 커진다) PDF 에서는 글꼴에 기대지 않는 표시로 바꾼다:
  판정 색 동그라미(🔴⚪🟡…)는 CSS 로 그린 점, 번호 키캡(1️⃣)은 숫자, 경고(⚠️🚨)는 느낌표 표시, 나머지 장식은 뺀다.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)
# WeasyPrint 는 조판 단계마다 INFO 를, 쓰지 않는 CSS 속성마다 WARNING 을 남긴다 — 요청마다 수십 줄이 된다.
for _name in ("weasyprint", "weasyprint.progress", "fontTools", "fontTools.subset"):
    logging.getLogger(_name).setLevel(logging.ERROR)


class PdfUnavailable(RuntimeError):
    """이 서버에서 PDF 를 만들 수 없다(WeasyPrint·시스템 라이브러리 없음, 또는 한글 글꼴 없음)."""


# 한글·한자를 가진 글꼴(재배포 가능한 것을 앞에). 뒤쪽 이름은 개발용 macOS·Windows 의 시스템 글꼴이다.
_FONTS_KO = ("'Noto Sans CJK KR','Noto Sans KR','NanumGothic','NanumBarunGothic',"
             "'Apple SD Gothic Neo','Malgun Gothic'")
_FONTS_ZH = ("'Noto Sans CJK SC','Noto Sans SC','PingFang SC','Microsoft YaHei',"
             "'Noto Sans CJK KR','Noto Sans KR'")
# 조판에 실제로 쓰인 글꼴 가운데 이 이름이 하나는 있어야 한글·한자가 글자로 찍힌 것이다. 한글 글꼴이 없는
# 서버에서는 다른 글꼴의 빈 상자(.notdef)로 채워지고, 그때는 이 이름들이 쓰인 글꼴 목록에 나타나지 않는다.
_CJK_FAMILIES = ("noto sans cjk", "noto serif cjk", "noto sans kr", "noto sans sc", "nanum", "source han",
                 "apple sd gothic", "malgun", "pingfang", "yahei", "hiragino")
_CJK_TEXT = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힣一-鿿]")

_PDF_CSS = """
@page{ size:A4; margin:14mm 12mm 16mm; @bottom-center{ content: counter(page) " / " counter(pages); font-size:8.5pt; color:#8a90a2; } }
html,body{ font-family:%(fonts)s,sans-serif !important; }
body{ font-size:10.5pt; }
.print-hint{ display:none !important; }
.content h3 + p code, code{ font-family:inherit; }
.content tr, .tile, .content li{ page-break-inside:avoid; }
.pdf-dot{ display:inline-block; width:.72em; height:.72em; border-radius:50%%; margin-right:.3em; vertical-align:-.02em; border:.6pt solid rgba(0,0,0,.18); }
.pdf-num{ display:inline-block; min-width:1.35em; padding:0 .3em; margin-right:.35em; border-radius:.3em; background:#6d5efb; color:#fff; font-weight:700; text-align:center; }
.pdf-warn{ display:inline-block; width:1.25em; height:1.25em; line-height:1.25em; margin-right:.3em; border-radius:50%%; background:#e5484d; color:#fff; font-weight:800; text-align:center; font-size:.85em; }
"""

_DOTS = {"🔴": "#e5484d", "🟠": "#f08c00", "🟡": "#e0a400", "🟢": "#2f9e44", "🔵": "#1c7ed6", "🟣": "#7048e8",
         "🟤": "#8d6e63", "⚫": "#1f2430", "⚪": "#c9cdd8"}
_KEYCAP = re.compile("([0-9#*])\ufe0f?\u20e3")
_WARN = re.compile("(?:\u26a0|\U0001F6A8|\u2757|\u203c)\ufe0f?")
# 그 밖의 이모지·그림 문자(장식). 화살표(→)·가운뎃점·℃ 같은 글자는 건드리지 않는다.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u26FF\u2700-\u27BF\u2B05-\u2B07\u2B1B-\u2B1C\u2B50\u2B55\u231A-\u23FF\u2139]"
    "[\ufe0f\u200d\U0001F3FB-\U0001F3FF]*"
    "(?:\u200d[\U0001F000-\U0001FAFF\u2600-\u27BF][\ufe0f\U0001F3FB-\U0001F3FF]*)*")
_KEEP = {"✓", "✔", "✗", "✘"}       # 글꼴에 있는 기호는 남긴다


def emoji_fallback(html: str) -> str:
    """<body> 안의 이모지를 글꼴에 기대지 않는 표시로 바꾼다(모듈 설명 참고). 태그·속성은 건드리지 않는다."""
    head, sep, body = html.partition("<body")
    if not sep:
        head, body = "", html

    def text_only(segment: str) -> str:
        segment = _KEYCAP.sub(lambda m: f'<span class="pdf-num">{m.group(1)}</span>', segment)
        for ch, color in _DOTS.items():
            segment = segment.replace(ch, f'<span class="pdf-dot" style="background:{color}"></span>')
        segment = _WARN.sub('<span class="pdf-warn">!</span>', segment)
        segment = _EMOJI.sub(lambda m: m.group(0) if m.group(0) in _KEEP else "", segment)
        return segment.replace("️", "")

    # 태그(<…>)는 그대로 두고 그 사이의 글만 바꾼다
    parts = re.split(r"(<[^>]*>)", body)
    body = "".join(p if p.startswith("<") else text_only(p) for p in parts)
    return head + sep + body


def pdf_available() -> bool:
    """WeasyPrint 와 그 시스템 라이브러리를 불러올 수 있는가(글꼴은 만들 때 확인한다)."""
    try:
        import weasyprint  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — ImportError 뿐 아니라 Pango 를 찾지 못한 OSError 도 온다
        return False


def render_report_pdf(document_html: str, lang: Optional[str] = "ko") -> bytes:
    """리포트 HTML 문서(report_document_html) → PDF 바이트.

    PdfUnavailable: WeasyPrint 를 쓸 수 없거나, 한글·한자를 그릴 글꼴이 없어 빈 상자가 찍혔을 때."""
    try:
        from weasyprint import CSS, HTML
        from weasyprint.urls import URLFetcher
    except Exception as e:  # noqa: BLE001
        raise PdfUnavailable(f"WeasyPrint 를 불러오지 못했습니다({type(e).__name__})") from e
    fonts = _FONTS_ZH if (lang or "ko").lower().startswith("zh") else _FONTS_KO
    html = emoji_fallback(document_html or "")
    try:
        # 리포트 문서는 자체 완결형이다. 외부 주소·로컬 파일(file://)을 읽지 않는다 — 문서 안의 data: 만 허용한다.
        fetcher = URLFetcher(allowed_protocols=("data",), timeout=1)
        document = HTML(string=html, url_fetcher=fetcher, base_url=None).render(
            stylesheets=[CSS(string=_PDF_CSS % {"fonts": fonts})])
        pdf = document.write_pdf()
        used = {str(getattr(f, "family", "") or "").lower() for f in (document.fonts or {}).values()}
    except Exception as e:  # noqa: BLE001
        raise PdfUnavailable(f"PDF 조판에 실패했습니다({type(e).__name__})") from e
    if _CJK_TEXT.search(html) and not any(name in family for family in used for name in _CJK_FAMILIES):
        raise PdfUnavailable("한글·한자 글꼴이 없어 PDF 를 만들지 않았습니다(fonts-noto-cjk 를 설치하세요)")
    return pdf
