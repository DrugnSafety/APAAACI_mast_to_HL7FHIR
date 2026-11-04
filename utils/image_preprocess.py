"""OCR 전 이미지 전처리 — Pillow + numpy 만 쓴다(OpenCV 없이).

평가(2026-09-14)에서 가장 약한 곳은 휴대폰 촬영본이었다(수치 정확도 55%, 양성의 48% 누락).
그림자와 원근 왜곡이 겹치면 오른쪽 열 값이 이웃 행으로 밀린다. 여기서 하는 일:

  1. EXIF 회전 바로잡기, 작은 이미지는 키우기(글자가 작으면 소수점이 뭉개진다)
  2. 촬영본으로 보이면 조명 평탄화 — 크게 흐린 사본으로 나눠 그림자를 걷어낸다
  3. 두 번째 판독용으로 위·아래 절반 확대본을 만든다(겹치게)
     → 첫 판독은 전체 한 장, 두 번째 판독은 절반 두 장. 서로 다른 시점이라
       같은 실수를 되풀이할 가능성이 낮고, 두 결과가 다르면 그 행을 확인 대상으로 표시한다.

색은 버리지 않는다 — 영어 결과지는 class 를 색 막대로, 중국어는 빨간 '+' 로 표시한다.
"""
from __future__ import annotations

import base64
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

_warned_no_tesseract = False

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MIN_WIDTH = 1600          # 이보다 좁으면 키운다


def _to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)      # PNG 는 촬영본에서 2MB 를 넘는다 — 글자 선명도는 충분
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _load(src) -> Image.Image:
    if isinstance(src, Image.Image):
        img = src
    elif isinstance(src, (bytes, bytearray)):
        img = Image.open(io.BytesIO(src))
    else:
        img = Image.open(src)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def looks_like_photo(img: Image.Image) -> bool:
    """조명 얼룩이 있으면 촬영본으로 본다 — 크게 흐린 사본에서 밝은 쪽 밝기 폭(95-50 백분위).
    측정: 합성 촬영본 66, 실제 촬영본(MAST_2) 18, 스캔·화면 캡처 10~13."""
    g = img.convert("L").resize((200, max(1, int(200 * img.height / img.width))))
    bg = np.asarray(g.filter(ImageFilter.GaussianBlur(12)), dtype=np.float32)
    return float(np.percentile(bg, 95) - np.percentile(bg, 50)) > 15.0


def flatten_lighting(img: Image.Image) -> Image.Image:
    """그림자·조명 불균일 제거: 채널별로 크게 흐린 배경으로 나눈다. 색 정보는 남긴다."""
    arr = np.asarray(img, dtype=np.float32)
    radius = max(15, img.width // 40)
    bg = np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32)
    out = np.clip(arr / np.maximum(bg, 1.0) * 235.0, 0, 255).astype(np.uint8)
    return ImageOps.autocontrast(Image.fromarray(out), cutoff=1)


def _upscale(img: Image.Image, width: int) -> Image.Image:
    if img.width >= width:
        return img
    h = int(img.height * width / img.width)
    return img.resize((width, h), Image.LANCZOS)


def detect_rotation(img: Image.Image, timeout: int = 60) -> Optional[Tuple[int, float]]:
    """Tesseract OSD 로 글자 방향을 본다 → (시계 방향으로 돌릴 각도 0/90/180/270, 신뢰도). 못 보면 None.

    실측(2026-10-09, 실제 결과지 29장): 누운 사진 5장은 모두 90°(신뢰도 0.66~4.95)로 맞혔고 바로 선 사진은
    0° 였으나, 세로 사진 한 장(SPT 양식)을 180°(신뢰도 1.5)로 오판했다. 그래서 호출하는 쪽은 가로 사진에만,
    90/270 판정에만 쓴다.
    """
    import re
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path
    if shutil.which("tesseract") is None:
        global _warned_no_tesseract
        if not _warned_no_tesseract:
            logging.getLogger(__name__).warning("tesseract 가 없어 누운 사진 자동 회전을 건너뛴다(brew install tesseract)")
            _warned_no_tesseract = True
        return None
    try:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "osd.jpg"
            small = img.copy()
            small.thumbnail((1600, 1600))
            small.save(p, format="JPEG", quality=85)
            out = subprocess.run(["tesseract", str(p), "-", "--psm", "0"],
                                 capture_output=True, text=True, timeout=timeout)
        m = re.search(r"Rotate:\s*(\d+)", out.stdout)
        c = re.search(r"Orientation confidence:\s*([\d.]+)", out.stdout)
        if not m:
            return None
        return int(m.group(1)) % 360, float(c.group(1)) if c else 0.0
    except Exception:  # noqa: BLE001 — 방향 감지 실패는 본 판독을 막지 않는다
        return None


def auto_rotate(img: Image.Image, min_confidence: float = 0.5) -> Tuple[Image.Image, Optional[int]]:
    """누운(가로) 사진을 세운다. 돌렸으면 (이미지, 각도), 아니면 (이미지, None).

    휴대폰으로 세로 결과지를 가로로 찍어 올리면 모델이 행 번호를 한 칸씩 밀고 없는 항원을 지어냈다
    (A05: 수치 정확도 90%, 행번호 밀림 22행 → 세우면 100%, 0행). 세로 사진은 건드리지 않는다.
    """
    if img.width <= img.height:
        return img, None
    osd = detect_rotation(img)
    if not osd or osd[0] not in (90, 270) or osd[1] < min_confidence:
        return img, None
    # Tesseract 'Rotate: 90' = 시계 방향 90도 → PIL 은 반시계가 양수라 부호를 뒤집는다
    return img.rotate(-osd[0], expand=True), osd[0]


def prepare(src, flatten: Optional[bool] = None, rotate: bool = True) -> Dict[str, Any]:
    """모델에 보낼 이미지를 만든다.

    반환: {"full": b64, "halves": [위 b64, 아래 b64], "notes": [...], "photo": bool}
      full   — 첫 번째 판독용(전처리된 전체)
      halves — 두 번째 판독용. 위·아래로 겹치게 자른 확대본. 양식을 가정하지 않는다
               (가운데 거터로 좌우를 가르는 방법은 한 표 안의 빈 열을 거터로 오인했다).
               한 장에 담긴 행이 절반이 되고 글자가 커져, 첫 판독과 다른 '시점'이 된다.
    """
    img = _load(src)
    notes: List[str] = []
    rotated_raw: Optional[str] = None
    if rotate:
        img, angle = auto_rotate(img)
        if angle:
            notes.append(f"회전 보정 {angle}°")
            rotated_raw = _to_b64(img)      # 전처리를 끈 첫 판독도 같은 방향을 보게(두 판독의 회전 불일치 방지)
    photo = looks_like_photo(img)
    if flatten is None:
        flatten = photo
    if flatten:
        img = flatten_lighting(img)
        notes.append("조명 평탄화")
    if img.width < MIN_WIDTH:
        img = _upscale(img, MIN_WIDTH)
        notes.append("확대")
    h = img.height
    top = img.crop((0, 0, img.width, int(h * 0.56)))
    bottom = img.crop((0, int(h * 0.44), img.width, h))
    halves = [_to_b64(_upscale(top, int(img.width * 1.3))), _to_b64(_upscale(bottom, int(img.width * 1.3)))]
    return {"full": _to_b64(img), "halves": halves, "notes": notes, "photo": photo, "rotated_raw": rotated_raw}


def _to_b64_q(img: Image.Image, quality: int) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def fit_payload(images_b64: List[str], max_total_bytes: int) -> List[str]:
    """한 요청에 실리는 이미지 base64 총량을 한도 아래로 줄인다.

    연구실 서버 앞의 nginx 가 기본 한도(client_max_body_size 1m)로 큰 요청을 413 으로 거절한다
    (2026-10-09 실측: 절반 확대본 2장 각 400KB → base64 1.07MB → 413. 1.3MB 촬영본은 한 장도 안 들어간다).
    해상도를 최대한 지키려고 JPEG 품질을 먼저 낮추고, 그래도 넘으면 15% 씩 줄인다.
    한도 안이면 그대로 돌려준다(OpenAI 쪽에는 쓰지 않는다).
    """
    if max_total_bytes <= 0 or sum(len(b) for b in images_b64) <= max_total_bytes:
        return images_b64
    budget_each = max_total_bytes // max(1, len(images_b64))
    out: List[str] = []
    for b64 in images_b64:
        if len(b64) <= budget_each:
            out.append(b64)
            continue
        img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
        shrunk = b64
        for quality in (85, 78, 70):
            shrunk = _to_b64_q(img, quality)
            if len(shrunk) <= budget_each:
                break
        while len(shrunk) > budget_each and img.width > 800:
            img = img.resize((int(img.width * 0.85), int(img.height * 0.85)), Image.LANCZOS)
            shrunk = _to_b64_q(img, 70)
        out.append(shrunk)
    return out


HALVES_PROMPT_ADDENDUM = """
TWO IMAGES: they are the TOP part and the BOTTOM part of the SAME sheet, enlarged, overlapping in
the middle. Read the title and patient block from the top image. Read every result row from both
images. Rows in the overlap appear twice — list each allergen only once. Each row's name, class and
value are on the SAME horizontal line; follow the line across and never take a value from the row
above or below."""
