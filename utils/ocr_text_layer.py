"""전용 OCR 엔진의 텍스트 층 — 비전 모델에 '행 순서 확인용' 참고로 붙인다(선택 기능).

왜 선택 기능인가
  전용 OCR 은 글자 위치(행)를 기계적으로 잡아 준다. 비전 모델이 값을 이웃 행으로 밀어 읽는
  실수를 잡는 데 쓸 수 있다. 다만 지금 쓸 수 있는 엔진은 로컬 Tesseract 뿐이고, 측정해 보니
  항원명을 뭉개고 **소수점을 잃는다**('<0.35' → '<035', '0.23' → '023'). 그대로 숫자 근거로
  쓰면 오히려 틀린다. 그래서 모델에게 '행 순서만 참고하고 숫자는 이미지를 믿으라'고 못 박고,
  기본값은 끈다(OCR_TEXT_LAYER). 상용 엔진(Google Document AI·Azure Document Intelligence·
  Upstage)이 붙으면 같은 자리에 엔진만 바꿔 끼우면 된다.
"""
from __future__ import annotations

import base64
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TESSERACT_LANGS = "kor+eng+chi_sim"

TEXT_LAYER_PROMPT = """
SECOND OCR ENGINE (reference only): a separate OCR engine read the sheet line by line. Its text is
below. It often garbles names and DROPS DECIMAL POINTS (e.g. "023" for "0.23", "<035" for "<0.35").
Use it ONLY to check which values sit on the same line as which allergen (row alignment) and to
notice rows you might have skipped. Take every digit, decimal point and name from the IMAGE, never
from this text.
----- OCR TEXT LAYER -----
{text}
----- END -----"""


def available() -> bool:
    return shutil.which("tesseract") is not None


def tesseract_text(image_b64: str, timeout: int = 60) -> Optional[str]:
    """base64 이미지 → 줄 단위 텍스트. 실패하면 None (본 판독을 막지 않는다)."""
    if not available():
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page.jpg"
            p.write_bytes(base64.b64decode(image_b64))
            out = subprocess.run(["tesseract", str(p), "-", "-l", TESSERACT_LANGS, "--psm", "6"],
                                 capture_output=True, text=True, timeout=timeout)
        lines = [" ".join(l.split()) for l in out.stdout.splitlines()]
        text = "\n".join(l for l in lines if l)
        return text[:12000] or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"텍스트 층 생성 실패: {e}")
        return None
