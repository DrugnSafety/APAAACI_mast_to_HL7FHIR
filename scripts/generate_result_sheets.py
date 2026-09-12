#!/usr/bin/env python3
"""알레르기 검사 결과지(MAST·피부단자검사) 합성 샘플 이미지를 만든다.

왜 필요한가
  OCR 단계는 이 플랫폼에서 LLM 이 반드시 필요한 유일한 구간이다. 그런데 손에 있는 실제
  결과지는 5장뿐이라 회귀 검증이 불가능했다. 실제 결과지는 환자 식별정보를 담고 있어
  모아 두기도 어렵다. 그래서 실제 양식을 관찰해 **가짜 환자 정보로 합성 이미지**를 만들고,
  이미지마다 정답(ground truth) JSON 을 함께 낸다. 정답이 있어야 정확도를 잴 수 있다.

관찰한 실제 양식 5종 (docs/ocr_fixtures_survey.md 참조)
  A. mast_lab_report  인쇄 검사보고서 — 구분/Allergen명/한글/결과(IU/mL)/Class, 2단 배치
  B. mast_photo       인쇄본을 휴대폰으로 찍은 것 — No/Allergen/Class/IU/mL + 세로 구분열
  C. mast_emr_text    EMR 터미널 텍스트 — 어두운 배경, 'NN. English(한글) : CL (값)'
  D. spt_form_light   EMR 피부단자 입력폼 — 카테고리별 색, Size 'AxB', Histamine/Saline
  E. spt_form_dark    같은 폼의 다크 테마 + 상단 툴바

열화(degradation) 단계
  clean  원본 그대로(스캔 PDF 수준)
  scan   약한 블러 + 종이 질감 + 미세 회전 + JPEG 압축
  photo  원근 왜곡 + 그림자 그라데이션 + 조명 불균일 + 블러 + 강한 JPEG 압축

사용
  python3 scripts/generate_result_sheets.py --out tests/fixtures/ocr --count 12
  python3 scripts/generate_result_sheets.py --layout spt_form_dark --degrade photo --count 3
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:  # pragma: no cover
    sys.exit("Pillow 가 필요하다: pip install -r requirements.txt")

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 폰트
# 한글이 들어가므로 한글 글리프가 있는 폰트여야 한다. 없으면 네모(두부)로 렌더된다.
FONT_CANDIDATES = [
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 0),
    ("/System/Library/Fonts/Supplemental/AppleGothic.ttf", 0),
    ("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
]
BOLD_CANDIDATES = [
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 2),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0),
]


def _load(cands, size: int) -> ImageFont.FreeTypeFont:
    for path, idx in cands:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("b" if bold else "r", size)
    if key not in _FONT_CACHE:
        _FONT_CACHE[key] = _load(BOLD_CANDIDATES if bold else FONT_CANDIDATES, size)
        if bold and _FONT_CACHE[key] is None:
            _FONT_CACHE[key] = _load(FONT_CANDIDATES, size)
    return _FONT_CACHE[key]


# ---------------------------------------------------------------- 가짜 신원
# 실제 환자 정보를 쓰지 않는다. 흔한 성 + 조합 이름으로 명백한 가상 인물을 만든다.
SURNAMES = ["김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오", "서", "신", "권"]
GIVEN = ["민준", "서연", "지호", "하윤", "도윤", "서준", "예은", "지우", "수아", "건우",
         "지안", "유진", "채원", "시우", "나윤", "은우", "다인", "현서", "소율", "태민"]
FACILITIES = ["한빛대학교병원 진단검사의학과", "새길병원 진단검사의학과", "미래로의원",
              "푸른숲내과의원", "제일종합병원 알레르기내과", "해든병원 소아청소년과"]
DEPARTMENTS = ["내과", "소아청소년과", "알레르기내과", "이비인후과", "가정의학과"]


def fake_patient(rng: random.Random) -> Dict[str, Any]:
    name = rng.choice(SURNAMES) + rng.choice(GIVEN)
    age = rng.randint(3, 72)
    y = 2026 - age
    birth = f"{y}-{rng.randint(1,12):02d}-{rng.randint(1,28):02d}"
    m = rng.randint(1, 9)
    d = rng.randint(1, 27)
    test_date = f"2026-{m:02d}-{d:02d}"
    report_date = f"2026-{m:02d}-{d+1:02d}"
    return {
        "name": name,
        "age": age,
        "gender": rng.choice(["M", "F"]),
        "birth": birth,
        "test_date": test_date,
        "report_date": report_date,
        "facility": rng.choice(FACILITIES),
        "department": rng.choice(DEPARTMENTS),
        "ordering_provider": rng.choice(SURNAMES) + rng.choice(GIVEN),
        "patient_id_external": f"{rng.randint(10000000, 99999999)}",
        "accession": f"2026{m:02d}{d:02d}-{rng.randint(100,999)}-{rng.randint(1000,9999)}",
    }


# ---------------------------------------------------------------- 패널 구성
GROUP_KO = {
    "mite": "진드기", "mold": "곰팡이", "animal": "동물털", "insect": "곤충",
    "pollen_tree": "수목화분", "pollen_weed": "잡초화분", "pollen_grass": "목초화분",
    "food": "식품", "other": "기타", "control": "대조",
}
INHALANT_CATS = ["mite", "mold", "animal", "insect", "pollen_tree", "pollen_weed", "pollen_grass"]


def load_registry() -> List[Dict[str, Any]]:
    data = json.loads((ROOT / "data" / "allergens.json").read_text(encoding="utf-8"))
    return [a for a in data["antigens"] if a.get("category") != "control"]


def build_panel(reg, rng: random.Random, kind: str, size: int) -> List[Dict[str, Any]]:
    """패널 구성. 실제 결과지처럼 카테고리 순서대로 묶어 배치한다."""
    if kind == "inhalant":
        cats = INHALANT_CATS
    elif kind == "food":
        cats = ["mite", "mold", "animal", "food"]
    else:                                   # integrated
        cats = INHALANT_CATS + ["food"]
    by_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in cats}
    for a in reg:
        c = a.get("category")
        if c in by_cat:
            by_cat[c].append(a)
    panel: List[Dict[str, Any]] = []
    per = max(2, size // max(1, len(cats)))
    for c in cats:
        pool = by_cat[c]
        rng.shuffle(pool)
        panel.extend(pool[:per])
    rng.shuffle(panel)
    # 카테고리 순서로 다시 정렬해 실제 보고서처럼 그룹이 이어지게 한다
    order = {c: i for i, c in enumerate(cats)}
    panel.sort(key=lambda a: order.get(a.get("category"), 99))
    return panel[:size]


# ---------------------------------------------------------------- 결과값 생성
def mast_value(rng: random.Random) -> Tuple[Optional[float], str, int, str]:
    """(값, 표기문자열, class, 판정). 실제 결과지처럼 대부분 음성이다."""
    r = rng.random()
    if r < 0.72:                                   # 음성
        if rng.random() < 0.35:
            return None, "<0.15", 0, "Negative"    # 검출한계 미만 표기
        v = round(rng.uniform(0.0, 0.34), 2)
        return v, f"{v:.2f}", 0, "Negative"
    if r < 0.86:
        v = round(rng.uniform(0.35, 0.69), 2); cls = 1
    elif r < 0.94:
        v = round(rng.uniform(0.7, 3.49), 2); cls = 2
    elif r < 0.975:
        v = round(rng.uniform(3.5, 17.4), 2); cls = 3
    elif r < 0.992:
        v = round(rng.uniform(17.5, 49.9), 2); cls = 4
    else:
        v = round(rng.uniform(50.0, 99.0), 2); cls = 5
    return v, f"{v:.2f}", cls, "Positive"


def spt_size(rng: random.Random) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """피부단자 팽진 크기. 대부분 반응 없음(빈칸)."""
    r = rng.random()
    if r < 0.78:
        return None, None, None
    major = round(rng.choice([2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 8.0, 9.0, 11.0, 12.5]), 1)
    minor = round(max(2.0, major - rng.choice([0.0, 0.5, 1.0, 1.5, 2.0, 3.0])), 1)
    def fmt(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else str(x)
    return f"{fmt(major)}x{fmt(minor)}", major, minor


# ---------------------------------------------------------------- 그리기 헬퍼
# 실제 결과지가 속명을 약어로 쓰는 것은 **학명**뿐이다. 'Russian thistle' 같은 영어
# 일반명은 그대로 인쇄된다. 접미사 규칙(-us/-ae)은 Aspergillus niger·Candida albicans
# 처럼 예외가 많아 못 쓴다. 그래서 속명을 명시적으로 나열한다.
LATIN_GENERA = {
    "Dermatophagoides", "Tyrophagus", "Acarus", "Lepidoglyphus", "Glycyphagus",
    "Alternaria", "Aspergillus", "Cladosporium", "Penicillium", "Candida", "Mucor",
    "Fusarium", "Trichophyton", "Neurospora", "Curvularia", "Helminthosporium",
    "Blattella", "Periplaneta", "Artemisia", "Ambrosia", "Humulus",
}


def display_name(a: Dict[str, Any]) -> str:
    """결과지에 실제로 인쇄되는 표기. 학명은 실물처럼 속명을 약어로 쓴다
    (Dermatophagoides farinae → D. farinae). 글자수로 자르면 한글이 중간에서 깨져
    사람도 읽을 수 없는 이미지가 되므로, 자르는 대신 짧은 표기를 쓴다."""
    n = a["canonical_name"]
    parts = n.split()
    if len(parts) == 2 and parts[0] in LATIN_GENERA and parts[1].islower():
        return f"{parts[0][0]}. {parts[1]}"
    return n


def fit(d: ImageDraw.ImageDraw, s: str, f, max_w: int) -> str:
    """픽셀 폭에 맞춰 줄인다. 넘치면 말줄임표를 붙인다."""
    if d.textlength(s, font=f) <= max_w:
        return s
    while s and d.textlength(s + "…", font=f) > max_w:
        s = s[:-1]
    return s + "…" if s else ""


def text(d: ImageDraw.ImageDraw, xy, s, f, fill=(0, 0, 0), anchor=None):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def text_lm(d, x, y_top, rh, s, f, fill=(0, 0, 0)):
    """행 안에서 왼쪽·세로 가운데 정렬. 좌상단 기준으로 그리면 한글 폰트의 어센더 때문에
    같은 행의 가운데 정렬된 숫자보다 아래로 밀려 보인다."""
    d.text((x, y_top + rh / 2), s, font=f, fill=fill, anchor="lm")


def center(d, box, s, f, fill=(0, 0, 0)):
    x0, y0, x1, y1 = box
    d.text(((x0 + x1) / 2, (y0 + y1) / 2), s, font=f, fill=fill, anchor="mm")


# ---------------------------------------------------------------- 레이아웃 A: 인쇄 검사보고서
def layout_mast_lab_report(rng, reg) -> Tuple[Image.Image, Dict[str, Any]]:
    pat = fake_patient(rng)
    panel = build_panel(reg, rng, "integrated", 88)
    W, H = 1240, 1754                                 # A4 150dpi
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    f_title, f_hdr, f_row, f_small = font(30, True), font(17), font(16), font(13)
    d.rectangle([40, 40, W - 40, 92], fill=(196, 208, 230), outline=(60, 70, 100))
    center(d, (40, 40, W - 40, 92), "MAST Allergy (Integrated) 검사보고서", f_title, (20, 30, 70))

    # 머리말 3열
    top, rowh = 104, 30
    d.rectangle([40, top, W - 40, top + rowh * 4], outline=(90, 90, 90))
    labels = [("병(의)원명", pat["facility"]), ("수 진 자 명", pat["name"]),
              ("생 년 월 일", pat["birth"]), ("차 트 번 호", pat["patient_id_external"])]
    mids = [("진료과/병동", pat["department"]), ("의 사 명", pat["ordering_provider"]),
            ("접 수 번 호", pat["accession"]), ("검 체 종 류", "Serum")]
    rights = [("검체채취일", pat["test_date"]), ("검사의뢰일", pat["test_date"]),
              ("결과보고일", pat["report_date"]), ("기        타", "")]
    for i, ((l1, v1), (l2, v2), (l3, v3)) in enumerate(zip(labels, mids, rights)):
        y = top + rowh * i
        d.line([40, y, W - 40, y], fill=(170, 170, 170))
        text(d, (52, y + 6), l1, f_hdr, (40, 40, 40))
        text(d, (168, y + 6), v1, f_hdr)
        text(d, (452, y + 6), l2, f_hdr, (40, 40, 40))
        text(d, (568, y + 6), v2, f_hdr)
        text(d, (872, y + 6), l3, f_hdr, (40, 40, 40))
        text(d, (990, y + 6), v3, f_hdr)

    # 결과표 2단
    ty = top + rowh * 4 + 22
    bw = (W - 80 - 16) // 2
    # 컬럼 합계가 블록 폭과 정확히 같아야 한다. 넘치면 Class 열이 지면 밖으로 밀린다.
    cols = [52, 236, 128, 96, 60]                     # 구분 / Allergen / 한글 / 결과 / Class
    assert sum(cols) == bw, f"컬럼 합계 {sum(cols)} != 블록 폭 {bw}"
    results: List[Dict[str, Any]] = []
    idx = 0
    per_block = 43

    for b in range(2):
        bx = 40 + b * (bw + 16)
        d.rectangle([bx, ty, bx + bw, ty + 30], fill=(210, 220, 238), outline=(70, 70, 70))
        cx = bx
        for w, name in zip(cols, ["구분", "Allergen명", "", "결과\n(IU/mL)", "Class"]):
            if name:
                center(d, (cx, ty, cx + w, ty + 30), name.replace("\n", " "), f_small, (20, 30, 70))
            cx += w
        y = ty + 30
        rh = 25
        prev_group = None
        if b == 0:
            d.rectangle([bx, y, bx + bw, y + rh], outline=(150, 150, 150))
            text_lm(d, bx + cols[0] + 8, y, rh, "Total IgE", f_row)
            text_lm(d, bx + cols[0] + cols[1] + 8, y, rh, "총 IgE", f_row)
            center(d, (bx + sum(cols[:3]), y, bx + sum(cols[:4]), y + rh), "<50", f_row)
            center(d, (bx + sum(cols[:4]), y, bx + bw, y + rh), "음성", f_row)
            y += rh
        for _ in range(per_block):
            if idx >= len(panel):
                break
            a = panel[idx]; idx += 1
            val, vtext, cls, interp = mast_value(rng)
            grp = GROUP_KO.get(a.get("category"), "")
            d.rectangle([bx, y, bx + bw, y + rh], outline=(178, 178, 178))
            if grp != prev_group:
                text_lm(d, bx + 6, y, rh, grp, f_small, (30, 30, 30))
                prev_group = grp
            disp = display_name(a)
            text_lm(d, bx + cols[0] + 8, y, rh, fit(d, disp, f_row, cols[1] - 14), f_row)
            ko = a.get("korean_name") or ""
            text_lm(d, bx + cols[0] + cols[1] + 6, y, rh, fit(d, ko, f_row, cols[2] - 12), f_row)
            center(d, (bx + sum(cols[:3]), y, bx + sum(cols[:4]), y + rh), vtext, f_row)
            center(d, (bx + sum(cols[:4]), y, bx + bw, y + rh), str(cls), f_row)
            results.append({
                "index": idx, "raw_text": f"{a['canonical_name']} {vtext} {cls}",
                "allergen_name": a["canonical_name"], "korean_name": a.get("korean_name"),
                "display_text": disp,
                "value": val, "value_text": None if val is not None else vtext,
                "unit": "IU/mL", "class_value": cls, "interpretation": interp,
            })
            y += rh

    text(d, (W / 2, H - 120), "1/1", f_small, (90, 90, 90), anchor="mm")
    text(d, (900, H - 90), f"검사자  {rng.choice(SURNAMES)}{rng.choice(GIVEN)}", f_small, (60, 60, 60))
    text(d, (900, H - 68), f"보고자  {rng.choice(SURNAMES)}{rng.choice(GIVEN)}", f_small, (60, 60, 60))
    text(d, (52, H - 68), f"검사기관번호 : {rng.randint(10000000, 99999999)}", f_small, (60, 60, 60))
    return img, {"test_type": "MAST", "patient": pat, "results": results}


# ---------------------------------------------------------------- 레이아웃 B: 사진 촬영본
def layout_mast_photo(rng, reg) -> Tuple[Image.Image, Dict[str, Any]]:
    pat = fake_patient(rng)
    panel = build_panel(reg, rng, "inhalant", 46)
    W, H = 1200, 1500
    img = Image.new("RGB", (W, H), (252, 251, 247))
    d = ImageDraw.Draw(img)
    f_title, f_hdr, f_row, f_small = font(34, True), font(17), font(17), font(14)

    text(d, (60, 48), "MAST Allergy 검사결과보고서", f_title, (15, 15, 15))
    text(d, (W - 250, 62), f"누745나   D{rng.randint(100000,999999)}KZ", f_small, (60, 60, 60))

    top, rowh = 120, 32
    left = [("의뢰기관", pat["facility"]), ("성      명", pat["name"]),
            ("등록번호", pat["patient_id_external"]), ("생년월일", pat["birth"]),
            ("나이/성별", f"{pat['age']}/{pat['gender']}"), ("비      고", "")]
    mid = [("기관기호", f"{rng.randint(10000000,99999999)}"), ("진 료 과", pat["department"]),
           ("병      동", ""), ("의뢰의사", pat["ordering_provider"]),
           ("검체종류", "Serum"), ("", "")]
    right = [("접수번호", pat["accession"]), ("채취일시", pat["test_date"].replace("-", "/")),
             ("접수일시", pat["test_date"].replace("-", "/") + " 17:03"),
             ("검사일시", pat["report_date"].replace("-", "/") + " 03:17"),
             ("보고일시", pat["report_date"].replace("-", "/") + " 15:34"), ("", "")]
    for i, ((l1, v1), (l2, v2), (l3, v3)) in enumerate(zip(left, mid, right)):
        y = top + rowh * i
        d.line([60, y + rowh, W - 60, y + rowh], fill=(120, 120, 120))
        text(d, (70, y + 7), l1, f_hdr, (25, 25, 25))
        text(d, (186, y + 7), v1, f_hdr)
        if l2:
            text(d, (430, y + 7), l2, f_hdr, (25, 25, 25))
            text(d, (556, y + 7), v2, f_hdr)
        if l3:
            text(d, (790, y + 7), l3, f_hdr, (25, 25, 25))
            text(d, (916, y + 7), v3, f_hdr)

    ty = top + rowh * 6 + 30
    bw = (W - 120 - 20) // 2
    cols = [44, 48, 268, 78, 82]                      # 구분 / No / Allergen / Class / IU/mL
    results: List[Dict[str, Any]] = []
    idx = 62
    for b in range(2):
        bx = 60 + b * (bw + 20)
        d.rectangle([bx, ty, bx + bw, ty + 34], fill=(58, 58, 58))
        cx = bx + cols[0]
        for w, name in zip(cols[1:], ["No", "Allergen", "Class", "IU/mL"]):
            center(d, (cx, ty, cx + w, ty + 34), name, f_small, (255, 255, 255))
            cx += w
        y = ty + 34
        rh = 27
        prev = None
        for _ in range(23):
            k = (idx - 62)
            if k >= len(panel):
                break
            a = panel[k]; idx += 1
            val, vtext, cls, interp = mast_value(rng)
            grp = GROUP_KO.get(a.get("category"), "")
            if grp != prev:
                d.line([bx, y, bx + bw, y], fill=(40, 40, 40), width=2)
                prev = grp
            for gx in range(bx + cols[0], bx + bw, 4):
                d.point((gx, y + rh), fill=(190, 190, 190))
            center(d, (bx, y, bx + cols[0], y + rh), grp[:1] if grp else "", f_small, (40, 40, 40))
            center(d, (bx + cols[0], y, bx + cols[0] + cols[1], y + rh), str(idx - 1), f_row)
            label = f"{display_name(a)} ({a.get('korean_name') or ''})"
            text_lm(d, bx + cols[0] + cols[1] + 8, y, rh,
                    fit(d, label, f_row, cols[2] - 16), f_row)
            center(d, (bx + sum(cols[:3]), y, bx + sum(cols[:4]), y + rh), str(cls), f_row)
            center(d, (bx + sum(cols[:4]), y, bx + bw, y + rh), vtext, f_row)
            results.append({
                "index": idx - 1, "raw_text": f"{label} {cls} {vtext}",
                "allergen_name": a["canonical_name"], "korean_name": a.get("korean_name"),
                "display_text": label,
                "value": val, "value_text": None if val is not None else vtext,
                "unit": "IU/mL", "class_value": cls, "interpretation": interp,
            })
            y += rh
    return img, {"test_type": "MAST", "patient": pat, "results": results}


# ---------------------------------------------------------------- 레이아웃 C: EMR 터미널
def layout_mast_emr_text(rng, reg) -> Tuple[Image.Image, Dict[str, Any]]:
    pat = fake_patient(rng)
    panel = build_panel(reg, rng, "food", 58)
    W, H = 1140, 900
    bg = (26, 28, 38)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    fg = (222, 226, 236)
    f = font(19)
    d.rectangle([2, 2, W - 3, H - 3], outline=(120, 126, 140))

    text(d, (24, 12), "MAST Allergy(FOOD)", f, fg)
    text(d, (24, 62), "[검사결과]", f, fg)
    results: List[Dict[str, Any]] = []
    half = (len(panel) + 1) // 2
    for b in range(2):
        bx = 24 + b * 560
        d.line([bx, 104, bx + 528, 104], fill=fg)
        text(d, (bx + 10, 118), "Allergen", f, fg)
        text(d, (bx + 406, 118), "CL", f, fg)
        text(d, (bx + 452, 118), "Unit", f, fg)
        d.line([bx, 156, bx + 528, 156], fill=fg)
        y = 172
        start = b * half
        if b == 0:
            text(d, (bx + 14, y), "01. Total IgE(총 IgE)", f, fg)
            text(d, (bx + 396, y), ":  P", f, fg)
            text(d, (bx + 452, y), f"({rng.uniform(20, 300):.2f})", f, fg)
            y += 27
        for j in range(half):
            i = start + j
            if i >= len(panel) or y > H - 30:
                break
            a = panel[i]
            val, _, cls, interp = mast_value(rng)
            shown = val if val is not None else 0.0
            n = i + 2
            label = f"{n:02d}. {display_name(a)}({a.get('korean_name') or ''})"
            text(d, (bx + 14, y), fit(d, label, f, 374), f, fg)
            text(d, (bx + 396, y), f":  {cls}", f, fg)
            text(d, (bx + 452, y), f"({shown:.2f})", f, fg)
            results.append({
                "index": n, "raw_text": f"{label} : {cls} ({shown:.2f})",
                "allergen_name": a["canonical_name"], "korean_name": a.get("korean_name"),
                "display_text": label,
                "value": shown, "value_text": None, "unit": "IU/mL",
                "class_value": cls, "interpretation": interp,
            })
            y += 27
    return img, {"test_type": "MAST", "patient": pat, "results": results}


# ---------------------------------------------------------------- 레이아웃 D/E: 피부단자 입력폼
CAT_TINT = {
    "mite": (252, 224, 222), "mold": (252, 226, 196), "pollen_weed": (252, 246, 205),
    "pollen_grass": (226, 245, 228), "pollen_tree": (222, 244, 248),
    "animal": (255, 255, 255), "insect": (250, 232, 200), "food": (240, 240, 250),
}


def layout_spt_form(rng, reg, dark: bool) -> Tuple[Image.Image, Dict[str, Any]]:
    pat = fake_patient(rng)
    panel = build_panel(reg, rng, "inhalant", 56)
    W, H = 1500, 1900
    bg = (22, 24, 30) if dark else (255, 255, 255)
    fg = (232, 234, 240) if dark else (25, 25, 25)
    grid = (95, 100, 112) if dark else (140, 140, 150)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    f_title, f_hdr, f_row = font(30, True), font(22), font(21)

    y0 = 20
    if dark:                                            # 상단 툴바
        for i, (lbl, col) in enumerate([("요약보기 ▶", (44, 110, 180)), ("복사", (46, 96, 66)),
                                        ("수정", (60, 68, 78)), ("삭제", (120, 84, 40))]):
            bx = 700 + i * 200
            d.rectangle([bx, 16, bx + 186, 76], fill=col)
            center(d, (bx, 16, bx + 186, 76), lbl, f_hdr, (240, 240, 240))
        y0 = 100

    if dark:
        text(d, (60, y0 + 20), "피부 단자 검사(Allergen skin prick test) - 흡입항원(Inhalent allergen)",
             f_title, fg)
    else:
        d.rectangle([30, y0, W - 30, y0 + 62], fill=(58, 122, 214))
        center(d, (30, y0, W - 30, y0 + 62),
               "피부 단자 검사(Allergen skin prick test) - 흡입항원(Inhalent allergen)",
               f_title, (255, 255, 255))
    y0 += 86

    text(d, (40, y0), "검사자", f_hdr, fg)
    d.rectangle([140, y0 - 6, 430, y0 + 38], outline=grid)
    text(d, (152, y0), pat["ordering_provider"], f_hdr, fg)
    text(d, (470, y0), "검사일자", f_hdr, fg)
    d.rectangle([600, y0 - 6, 890, y0 + 38], outline=grid)
    text(d, (614, y0), pat["test_date"], f_hdr, fg)
    y0 += 58
    text(d, (40, y0), "항히스타민 복용여부", f_hdr, fg)
    took = rng.random() < 0.2
    for i, lbl in enumerate(["No", "Yes"]):
        cx = 350 + i * 130
        d.ellipse([cx, y0 + 2, cx + 26, y0 + 28], outline=fg, width=2,
                  fill=fg if (lbl == "Yes") == took else bg)
        text(d, (cx + 36, y0), lbl, f_hdr, fg)
    text(d, (640, y0), "검사", f_hdr, fg)
    d.rectangle([710, y0 - 6, 800, y0 + 38], outline=grid)
    if took:
        center(d, (710, y0 - 6, 800, y0 + 38), str(rng.randint(1, 7)), f_hdr, fg)
    text(d, (818, y0), "(일전)", f_hdr, fg)
    y0 += 62
    d.line([30, y0, W - 30, y0], fill=fg, width=2)
    y0 += 24

    # 대조 반응 — 히스타민은 대부분 양성
    hist_major = round(rng.choice([3.0, 3.5, 4.0, 4.5, 5.0, 6.0]), 1)
    hist_minor = round(max(2.5, hist_major - rng.choice([0.0, 0.5, 1.0])), 1)

    def fmt(x):
        return str(int(x)) if float(x).is_integer() else str(x)

    bw = (W - 60 - 24) // 2
    ncol, scol = 60, 190
    rh = 46
    results: List[Dict[str, Any]] = []
    rows_per = 29
    seq = 0
    for b in range(2):
        bx = 30 + b * (bw + 24)
        y = y0
        d.rectangle([bx, y, bx + bw, y + rh], outline=grid,
                    fill=(40, 44, 56) if dark else (232, 232, 246))
        center(d, (bx + bw - scol, y, bx + bw, y + rh), "Size", f_row, fg)
        y += rh
        if b == 0:
            for lbl, sz in (("Histamine(0.1%)", f"{fmt(hist_major)}x{fmt(hist_minor)}"),
                            ("Saline", "")):
                d.rectangle([bx, y, bx + bw, y + rh], outline=grid,
                            fill=(40, 44, 56) if dark else (232, 232, 246))
                text_lm(d, bx + 14, y, rh, lbl, f_row, fg)
                center(d, (bx + bw - scol, y, bx + bw, y + rh), sz, f_row, fg)
                y += rh
            avail = rows_per - 2
        else:
            avail = rows_per
        for _ in range(avail):
            if seq >= len(panel):
                break
            a = panel[seq]; seq += 1
            size_text, major, minor = spt_size(rng)
            tint = CAT_TINT.get(a.get("category"), (255, 255, 255))
            fill = (tint if not dark else bg)
            d.rectangle([bx, y, bx + bw, y + rh], outline=grid, fill=fill)
            d.line([bx + ncol, y, bx + ncol, y + rh], fill=grid)
            d.line([bx + bw - scol, y, bx + bw - scol, y + rh], fill=grid)
            center(d, (bx, y, bx + ncol, y + rh), str(seq), f_row, fg)
            label = f"{display_name(a)}({a.get('korean_name') or ''})"
            text_lm(d, bx + ncol + 14, y, rh,
                    fit(d, label, f_row, bw - ncol - scol - 28), f_row, fg)
            if size_text:
                center(d, (bx + bw - scol, y, bx + bw, y + rh), size_text, f_row, fg)
            mean = round((major + minor) / 2, 2) if major else None
            results.append({
                "index": seq, "raw_text": f"{seq} {label} {size_text or ''}".strip(),
                "allergen_name": a["canonical_name"], "korean_name": a.get("korean_name"),
                "display_text": label,
                "size_text": size_text, "wheal_major_mm": major, "wheal_minor_mm": minor,
                "mean_mm": mean, "unit": "mm",
                "interpretation": "Positive" if (mean or 0) >= 3 else "Negative",
            })
            y += rh

    if not dark:
        text(d, (40, y + 24), "Result", font(24, True), fg)
        for i, lbl in enumerate(["Positive", "Negative", "Equivocal"]):
            cx = 80 + i * 330
            d.ellipse([cx, y + 70, cx + 26, y + 96], outline=fg, width=2)
            text(d, (cx + 38, y + 68), lbl, f_row, fg)

    pat = dict(pat)
    pat["histamine_mean_mm"] = round((hist_major + hist_minor) / 2, 2)
    pat["negative_control_mean_mm"] = 0.0
    pat["antihistamine_taken"] = took
    return img, {"test_type": "SPT", "patient": pat, "results": results}


# ---------------------------------------------------------------- 열화 파이프라인
def degrade(img: Image.Image, mode: str, rng: random.Random) -> Image.Image:
    if mode == "clean":
        return img
    W, H = img.size
    if mode == "scan":
        img = img.rotate(rng.uniform(-0.7, 0.7), resample=Image.BICUBIC,
                         fillcolor=(250, 250, 248), expand=False)
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
        grain = Image.effect_noise((W, H), rng.uniform(4, 9)).convert("L")
        img = Image.blend(img, Image.merge("RGB", (grain, grain, grain)), 0.05)
        return _jpeg(img, rng.randint(72, 88))

    # photo — 원근 왜곡 + 그림자 + 조명 불균일
    m = int(min(W, H) * 0.02)
    quad = (rng.randint(0, m), rng.randint(0, m),
            rng.randint(0, m), H - rng.randint(0, m),
            W - rng.randint(0, m), H - rng.randint(0, m),
            W - rng.randint(0, m), rng.randint(0, m))
    img = img.transform((W, H), Image.QUAD, quad, resample=Image.BICUBIC,
                        fillcolor=(238, 236, 230))
    img = img.rotate(rng.uniform(-1.6, 1.6), resample=Image.BICUBIC,
                     fillcolor=(238, 236, 230), expand=False)
    shade = Image.new("L", (W, H), 255)
    sd = ImageDraw.Draw(shade)
    sx = rng.randint(int(W * 0.15), int(W * 0.7))
    sd.polygon([(sx, 0), (W, 0), (W, H), (sx + rng.randint(-200, 200), H)],
               fill=rng.randint(150, 205))
    shade = shade.filter(ImageFilter.GaussianBlur(rng.uniform(40, 90)))
    img = Image.composite(img, Image.new("RGB", (W, H), (0, 0, 0)), shade)
    img = Image.blend(img, Image.new("RGB", (W, H), (255, 252, 240)), 0.06)
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.1)))
    return _jpeg(img, rng.randint(55, 76))


def _jpeg(img: Image.Image, q: int) -> Image.Image:
    import io
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=q)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


# ---------------------------------------------------------------- 엔트리
LAYOUTS = {
    "mast_lab_report": (layout_mast_lab_report, "clean"),
    "mast_photo": (layout_mast_photo, "photo"),
    "mast_emr_text": (layout_mast_emr_text, "clean"),
    "spt_form_light": (lambda r, g: layout_spt_form(r, g, False), "scan"),
    "spt_form_dark": (lambda r, g: layout_spt_form(r, g, True), "clean"),
}


def main():
    ap = argparse.ArgumentParser(description="알레르기 검사 결과지 합성 샘플 생성")
    ap.add_argument("--out", default="tests/fixtures/ocr", help="출력 디렉터리")
    ap.add_argument("--count", type=int, default=10, help="생성할 이미지 수")
    ap.add_argument("--layout", choices=sorted(LAYOUTS), help="한 종류만 생성")
    ap.add_argument("--degrade", choices=["clean", "scan", "photo"], help="열화 모드 고정")
    ap.add_argument("--seed", type=int, default=20260912, help="재현용 시드")
    args = ap.parse_args()

    out = (ROOT / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    reg = load_registry()

    names = [args.layout] if args.layout else list(LAYOUTS)
    manifest = []
    for i in range(args.count):
        name = names[i % len(names)]
        fn, default_deg = LAYOUTS[name]
        mode = args.degrade or default_deg
        img, truth = fn(rng, reg)
        img = degrade(img, mode, rng)
        stem = f"{name}_{mode}_{i+1:02d}"
        ext = "jpg" if mode != "clean" else "png"
        img_path = out / f"{stem}.{ext}"
        img.save(img_path, quality=92) if ext == "jpg" else img.save(img_path)
        truth.update({"layout": name, "degrade": mode, "image": img_path.name,
                      "synthetic": True,
                      "note_ko": "합성 샘플. 환자 정보는 모두 가상이며 실제 인물과 무관하다."})
        (out / f"{stem}.truth.json").write_text(
            json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
        manifest.append({"image": img_path.name, "truth": f"{stem}.truth.json",
                         "layout": name, "degrade": mode,
                         "test_type": truth["test_type"], "rows": len(truth["results"])})
        print(f"  {img_path.name:42s} {truth['test_type']:6s} rows={len(truth['results']):3d}")

    (out / "manifest.json").write_text(json.dumps(
        {"note_ko": "합성 OCR 픽스처 목록. 생성: scripts/generate_result_sheets.py",
         "seed": args.seed, "count": len(manifest), "items": manifest},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(manifest)}건 생성 → {out}")


if __name__ == "__main__":
    main()
