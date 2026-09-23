"""OCR 결과 검증 — 모델이 읽은 값을 규칙으로 다시 보고, 의심스러운 곳에 표시를 단다.

왜 필요한가 (2026-09-14 평가)
  글자 정확도는 높아도 임상적으로 중요한 오류가 남았다.
    - 영어 결과지 '.48' → 4.8 (10배), 막대 그래프 'Class 0/1' → class 1 (음성이 양성으로)
    - 촬영본에서 값이 이웃 행으로 밀림 (class 와 수치가 서로 안 맞게 됨)
    - 환자명 자리에 검사자 이름
  이런 오류는 모델을 바꿔도 0 이 되지 않는다. 그래서 값을 '고치지' 않고 **표시만** 한다 —
  무엇이 맞는지는 원본을 보는 사람이 정한다. 표시된 행은 확인 화면에서 강조되고,
  사용자가 확인하기 전에는 리포트를 만들지 않는다(확인 화면 필수화).

규칙은 결정론적이고 외부 호출이 없다. 같은 입력이면 같은 표시가 나온다.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from models.schemas import OCRResult, TestType, normalize_class_token

# ImmunoCAP / MAST 공통 class 경계(kU/L = IU/mL)
CLASS_BANDS = (0.35, 0.7, 3.5, 17.5, 50.0, 100.0)

# 사람이 읽는 표시 문구(ko). 코드는 프론트에서 번역 키로도 쓴다.
FLAG_TEXT_KO = {
    "class_value_mismatch": "class 와 수치가 서로 맞지 않아요(행이 밀렸거나 한쪽을 잘못 읽었을 수 있어요)",
    "lod_with_class": "'<' 미만 표기인데 class 가 1 이상이에요",
    "value_raw_mismatch": "인쇄된 값과 읽은 숫자가 달라요(소수점 확인)",
    "value_out_of_range": "수치가 흔한 범위를 벗어나요",
    "size_unparsed": "팽진 크기 표기를 해석하지 못했어요",
    "size_out_of_range": "팽진 크기가 비정상적으로 커요",
    "unmapped_name": "항원 이름을 목록에서 찾지 못했어요",
    "duplicate_allergen": "같은 항원이 두 번 나오고 값이 달라요",
    "double_read_mismatch": "두 번 읽은 값이 서로 달라요",
    "second_read_only": "두 번째 판독에서만 찾은 행이에요",
    "first_read_only": "두 번째 판독에서는 이 행을 찾지 못했어요",
}
DOC_FLAG_TEXT_KO = {
    "missing_rows": "인쇄된 번호 중 빠진 행이 있어요",
    "patient_name_from_staff": "환자명이 검사자·의사 칸에서 읽혔을 수 있어요",
    "histamine_not_read": "히스타민(양성 대조) 크기를 읽지 못했어요 — 피부반응 판정 기준에 영향",
    "identity_confirm": "환자명·검사일은 원본과 대조해 주세요",
}

_STAFF_LABEL = re.compile(r"검사자|보고자|판독|의사|담당|examiner|reported|physician|doctor|检验|审核|医生", re.I)
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:[x×X*]\s*(\d+(?:\.\d+)?))?\s*(?:mm)?\s*$")


def class_from_value(v: float) -> int:
    for i, edge in enumerate(CLASS_BANDS):
        if v < edge:
            return i
    return 6


def parse_printed_number(raw: Optional[str]) -> Optional[float]:
    """인쇄 문자열 → 숫자. 앞자리 0 없는 '.48'·쉼표 소수점 '0,48' 도 읽는다. '<' 표기는 숫자가 아니다."""
    if raw is None:
        return None
    t = str(raw).strip().replace(",", ".").replace(" ", "")
    if not t or t.startswith(("<", ">", "≤", "≥")):
        return None
    m = re.fullmatch(r"(\d*\.\d+|\d+(?:\.\d+)?)", t)
    return float(m.group(1)) if m else None


def _flag(row, code: str) -> None:
    flags = list(getattr(row, "review_flags", None) or [])
    if code not in flags:
        flags.append(code)
    row.review_flags = flags


def validate(ocr: OCRResult, raw_rows: Optional[List[Dict[str, Any]]] = None,
             raw_patient: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """행마다 review_flags 를 달고, 문서 단위 표시를 metadata['review'] 로 돌려준다(in-place)."""
    from utils.allergen_mapper import get_allergen_mapper
    mapper = get_allergen_mapper()
    tt = ocr.test_type
    doc: List[str] = []

    seen: Dict[str, Any] = {}
    for r in ocr.results:
        # 1) 항원 이름이 목록에 붙는가
        hit = mapper.find_allergen(r.allergen_name or "") if (r.allergen_name or "").strip() else None
        if hit is None and r.korean_name:
            hit = mapper.find_allergen(r.korean_name)
        if hit is None:
            _flag(r, "unmapped_name")
        else:
            key = hit.canonical_name
            prev = seen.get(key)
            if prev is not None and (prev.value, prev.size_text, str(prev.class_value)) != \
                    (r.value, r.size_text, str(r.class_value)):
                _flag(prev, "duplicate_allergen")
                _flag(r, "duplicate_allergen")
            seen.setdefault(key, r)

        if tt == TestType.SPT:
            st = (r.size_text or "").strip()
            if st:
                m = _SIZE.match(st)
                if not m:
                    _flag(r, "size_unparsed")
                elif max(float(m.group(1)), float(m.group(2) or 0)) > 30:
                    _flag(r, "size_out_of_range")
            continue

        # 2) MAST/UniCAP — class 와 수치가 서로 맞는가
        c = normalize_class_token(r.class_value)
        vt = (r.value_text or "").strip()
        if vt.startswith("<") and c is not None and c >= 1:
            _flag(r, "lod_with_class")
        if r.value is not None:
            if r.value < 0 or r.value > 1000:
                _flag(r, "value_out_of_range")
            elif c is not None and c != class_from_value(r.value):
                _flag(r, "class_value_mismatch")

    # 3) 인쇄 문자열과 읽은 숫자 — 파서가 이미 인쇄 문자열을 우선했으면 여기서 다시 보지 않는다
    for r in ocr.results:
        if getattr(r, "value_raw_conflict", False):
            _flag(r, "value_raw_mismatch")

    # 4) 빠진 행 — 인쇄된 번호가 있으면 연속성을 본다
    nums = sorted({int(n) for n in (getattr(r, "printed_no", None) for r in ocr.results)
                   if n is not None and str(n).strip().isdigit()})
    missing: List[int] = []
    if len(nums) >= 5:
        full = set(range(nums[0], nums[-1] + 1))
        missing = sorted(full - set(nums))
        # SPT 대조액(히스타민·식염수)처럼 번호 없이 끼는 행은 없으니, 빠진 번호는 곧 빠진 행이다
        if missing:
            doc.append("missing_rows")

    # 5) 환자명 출처
    label = (raw_patient or {}).get("name_label") if raw_patient else None
    if label and _STAFF_LABEL.search(str(label)):
        doc.append("patient_name_from_staff")
    if ocr.patient and (ocr.patient.name or ocr.patient.test_date):
        doc.append("identity_confirm")

    if tt == TestType.SPT and getattr(ocr.patient, "histamine_mean_mm", None) is None:
        doc.append("histamine_not_read")

    flagged = [r for r in ocr.results if getattr(r, "review_flags", None)]
    review = {
        "flagged_rows": len(flagged),
        "doc_flags": doc,
        "missing_row_numbers": missing[:30],
        "flag_text_ko": {**FLAG_TEXT_KO, **DOC_FLAG_TEXT_KO},
        # 확인이 필요한가 — 표시된 행이 있거나, 문서 단위 문제가 있으면
        "needs_review": bool(flagged) or any(d for d in doc if d != "identity_confirm"),
    }
    md = dict(ocr.metadata or {})
    md["review"] = review
    ocr.metadata = md
    return review


# ---------------------------------------------------------------------------
# 두 번 읽기 비교
# ---------------------------------------------------------------------------
def _row_key(r, mapper) -> str:
    hit = mapper.find_allergen(r.allergen_name or "") if (r.allergen_name or "").strip() else None
    if hit is None and r.korean_name:
        hit = mapper.find_allergen(r.korean_name)
    if hit is not None:
        return hit.canonical_name.lower()
    return re.sub(r"\W+", "", (r.allergen_name or r.korean_name or "").lower())


def _reading(r, tt) -> tuple:
    """두 판독을 비교할 값 — 표기 차이(0.4 vs 0.40, '4.5x3' vs '4.5 x 3')는 같은 것으로 본다."""
    if tt == TestType.SPT:
        st = (r.size_text or "").lower().replace(" ", "").replace("×", "x").replace("mm", "")
        return ("size", st)
    vt = (r.value_text or "").strip().replace(" ", "")
    lod = vt.startswith("<")
    v = None if lod or r.value is None else round(float(r.value), 2)
    return ("sige", lod, v, normalize_class_token(r.class_value))


def _describe(r, tt) -> str:
    if tt == TestType.SPT:
        return r.size_text or "반응 없음"
    val = r.value_text or (f"{r.value:g}" if r.value is not None else "-")
    cls = f", class {r.class_value}" if r.class_value not in (None, "") else ""
    return f"{val}{cls}"


def merge_double_read(first: OCRResult, second: OCRResult) -> Dict[str, Any]:
    """첫 판독(전체 이미지)을 기준으로 두 번째 판독(위·아래 절반 확대본)과 행마다 비교한다.

    - 둘 다 있는데 값이 다르면 double_read_mismatch — 어느 쪽이 맞는지 정하지 않고, 다른 판독값을
      note 에 남겨 확인 화면에서 원본과 대조하게 한다.
    - 두 번째에만 있는 행은 추가하고 second_read_only 로 표시한다(첫 판독이 빠뜨린 행 복구).
    - 첫 번째에만 있는 행은, 두 번째 판독이 충분히 읽었을 때만(80% 이상) first_read_only 로 표시한다.
    """
    from utils.allergen_mapper import get_allergen_mapper
    mapper = get_allergen_mapper()
    tt = first.test_type
    a = {}
    for r in first.results:
        a.setdefault(_row_key(r, mapper), r)
    b = {}
    for r in second.results:
        b.setdefault(_row_key(r, mapper), r)
    stats = {"both": 0, "mismatch": 0, "added": 0, "first_only": 0}
    for k, ra in a.items():
        rb = b.get(k)
        if rb is None:
            continue
        stats["both"] += 1
        if _reading(ra, tt) != _reading(rb, tt):
            stats["mismatch"] += 1
            _flag(ra, "double_read_mismatch")
            note = f"다른 판독: {_describe(rb, tt)}"
            ra.note = f"{ra.note} · {note}" if ra.note else note
    b_cover = len(set(a) & set(b)) / max(1, len(a))
    for k, ra in a.items():
        if k not in b and b_cover >= 0.8:
            stats["first_only"] += 1
            _flag(ra, "first_read_only")
    next_index = max([r.index for r in first.results] or [0]) + 1
    for k, rb in b.items():
        if k in a:
            continue
        rb.index = next_index
        next_index += 1
        _flag(rb, "second_read_only")
        first.results.append(rb)
        stats["added"] += 1
    # 환자 정보가 첫 판독에 비어 있으면 두 번째에서 채운다(값이 다르면 문서 표시)
    for fld in ("name", "age", "gender", "test_date", "report_date", "facility"):
        va, vb = getattr(first.patient, fld, None), getattr(second.patient, fld, None)
        if va in (None, "") and vb not in (None, ""):
            setattr(first.patient, fld, vb)
        elif va not in (None, "") and vb not in (None, "") and str(va) != str(vb):
            stats.setdefault("patient_mismatch", []).append(fld)
    if first.test_type == TestType.SPT and getattr(first.patient, "histamine_mean_mm", None) is None:
        first.patient.histamine_mean_mm = getattr(second.patient, "histamine_mean_mm", None)
    return stats
