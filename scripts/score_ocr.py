#!/usr/bin/env python3
"""합성 픽스처로 OCR 정확도를 잰다.

왜 필요한가
  OCR 은 이 플랫폼에서 LLM 이 반드시 필요한 유일한 구간인데, 지금까지는 눈으로 보고
  "잘 읽는 것 같다"로 끝났다. 정답(ground truth)이 붙은 픽스처가 생겼으니 숫자로 잰다.
  레이아웃·열화 조합별로 점수가 나오면, 프롬프트나 모델을 바꿨을 때 좋아졌는지 나빠졌는지
  바로 알 수 있다.

재는 것
  test_type   검사 종류를 맞혔나 (MAST / SPT / UniCAP)
  recall      정답 항원 중 몇 개를 찾았나
  precision   찾은 항원 중 몇 개가 정답에 있나
  value       찾은 항원의 수치가 맞나 (검출한계 미만 표기 포함)
  class       찾은 항원의 class 가 맞나 (MAST/UniCAP)
  size        찾은 항원의 팽진 크기가 맞나 (SPT)

항원 이름은 레지스트리 별칭으로 정규화해서 맞춘다. 결과지에 'D. farinae' 로 인쇄되고
OCR 이 'Dermatophagoides farinae' 로 읽어도 같은 항원으로 센다.

사용
  python3 scripts/score_ocr.py                       # tests/fixtures/ocr 전체
  python3 scripts/score_ocr.py --limit 3 --json out.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models.schemas import normalize_class_token  # noqa: E402


def norm(s: Optional[str]) -> str:
    s = re.sub(r"\(.*?\)", " ", (s or "").lower())
    # 한자를 지우면 중국어 항원명이 빈 문자열이 되어 전부 매칭 실패한다
    s = re.sub(r"[^a-z0-9가-힣\u4e00-\u9fff ]+", " ", s)
    return " ".join(s.split())


def build_alias_index() -> Dict[str, str]:
    """별칭·한글명을 정규 id 로 접는다. 'D. farinae' 도 접히도록 속명 약어를 함께 넣는다."""
    reg = json.loads((ROOT / "data" / "allergens.json").read_text(encoding="utf-8"))["antigens"]
    idx: Dict[str, str] = {}
    # 중국어 결과지는 항원명이 중국어로만 인쇄된다
    zh_path = ROOT / "data" / "allergen_names_zh.json"
    if zh_path.exists():
        for canonical, info in (json.loads(zh_path.read_text(encoding="utf-8")).get("map") or {}).items():
            for zh in (info.get("zh") or []):
                n = norm(zh)
                if n:
                    idx.setdefault(n, info["id"])
    for a in reg:
        keys = [a.get("canonical_name"), a.get("korean_name"), *(a.get("aliases") or [])]
        cn = a.get("canonical_name") or ""
        parts = cn.split()
        if len(parts) == 2:
            keys.append(f"{parts[0][0]}. {parts[1]}")
            keys.append(f"{parts[0][0]}.{parts[1]}")
        for k in keys:
            n = norm(k)
            if n:
                idx.setdefault(n, a["id"])
    return idx


def canon(name: str, korean: Optional[str], idx: Dict[str, str]) -> str:
    """이름 → 항원 id. 못 찾으면 정규화한 이름 자체를 키로 쓴다."""
    for cand in (name, korean):
        n = norm(cand)
        if n and n in idx:
            return idx[n]
    return norm(name) or norm(korean) or "?"


def value_of(row: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """(수치, 검출한계미만 여부). '<0.15' 는 수치 없이 미만 표기로 센다."""
    vt = row.get("value_text")
    if isinstance(vt, str) and vt.strip().startswith("<"):
        return None, True
    v = row.get("value")
    return (float(v) if isinstance(v, (int, float)) else None), False


def close(a: Optional[float], b: Optional[float], tol: float = 0.02) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tol, abs(b) * 0.01)


def score_one(truth: Dict[str, Any], got: Any, idx: Dict[str, str]) -> Dict[str, Any]:
    t_rows = {canon(r["allergen_name"], r.get("korean_name"), idx): r for r in truth["results"]}
    g_rows: Dict[str, Any] = {}
    for r in (got.results or []):
        g_rows.setdefault(canon(r.allergen_name, r.korean_name, idx), r)

    matched = set(t_rows) & set(g_rows)
    n_t, n_g, n_m = len(t_rows), len(g_rows), len(matched)

    val_ok = cls_ok = size_ok = 0
    val_n = cls_n = size_n = 0
    misses: List[str] = []
    for key in sorted(matched):
        t, g = t_rows[key], g_rows[key]
        if truth["test_type"] == "SPT":
            size_n += 1
            if norm(t.get("size_text") or "") == norm(g.size_text or ""):
                size_ok += 1
            else:
                misses.append(f"size {key}: 정답 {t.get('size_text')!r} → 읽음 {g.size_text!r}")
        else:
            tv, t_lod = value_of(t)
            gv, g_lod = value_of({"value": g.value, "value_text": g.value_text})
            val_n += 1
            if t_lod == g_lod and close(gv, tv):
                val_ok += 1
            else:
                misses.append(f"value {key}: 정답 {t.get('value_text') or tv!r} → 읽음 "
                              f"{g.value_text or gv!r}")
            if t.get("class_value") is not None:
                cls_n += 1
                if normalize_class_token(g.class_value) == normalize_class_token(t["class_value"]):
                    cls_ok += 1
                else:
                    misses.append(f"class {key}: 정답 {t['class_value']} → 읽음 {g.class_value}")

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    # 임상적으로 중요한 오류 — 앱의 양성 판정과 같은 기준(class 가 있으면 class≥1, 없으면 수치≥0.35,
    # SPT 는 평균 팽진≥3mm). 글자 정확도는 높아도 이것이 틀리면 환자에게 틀린 말을 하게 된다.
    def positive(row_class, row_value, row_vt, size):
        if truth["test_type"] == "SPT":
            nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", size or "")]
            return bool(nums) and sum(nums) / len(nums) >= 3
        c = normalize_class_token(row_class)
        if c is not None:
            return c >= 1
        if isinstance(row_vt, str) and row_vt.strip().startswith("<"):
            return False
        return (row_value or 0) >= 0.35
    t_pos = missed_pos = false_pos = x10 = 0
    clin_err = clin_flagged = correct = correct_flagged = 0
    for key, t in t_rows.items():
        tp = positive(t.get("class_value"), t.get("value"), t.get("value_text"), t.get("size_text"))
        t_pos += tp
        g = g_rows.get(key)
        if g is None:
            missed_pos += tp
            continue
        gp = positive(g.class_value, g.value, g.value_text, g.size_text)
        missed_pos += tp and not gp
        false_pos += gp and not tp
        tv, gv = t.get("value"), g.value
        is_x10 = bool(tv and gv and tv > 0 and gv > 0 and (0.09 <= gv / tv <= 0.11 or 9 <= gv / tv <= 11))
        x10 += is_x10
        # 검증 표시가 임상 오류를 잡았나, 맞는 행에 괜히 달렸나
        flagged = bool(getattr(g, "review_flags", None))
        if (tp != gp) or is_x10:
            clin_err += 1
            clin_flagged += flagged
        else:
            correct += 1
            correct_flagged += flagged

    return {
        "truth_positive": t_pos, "missed_positive": missed_pos, "false_positive": false_pos,
        "clin_err": clin_err, "clin_flagged": clin_flagged, "correct": correct,
        "correct_flagged": correct_flagged,
        "x10_error": x10,
        "test_type_ok": got.test_type.value == truth["test_type"],
        "test_type_got": got.test_type.value,
        "truth_rows": n_t, "read_rows": n_g, "matched": n_m,
        "recall": pct(n_m, n_t), "precision": pct(n_m, n_g),
        "value": pct(val_ok, val_n), "class": pct(cls_ok, cls_n), "size": pct(size_ok, size_n),
        "missed_allergens": sorted(set(t_rows) - set(g_rows))[:8],
        "field_errors": misses[:8],
    }


def main():
    ap = argparse.ArgumentParser(description="합성 픽스처로 OCR 정확도 측정")
    ap.add_argument("--fixtures", default="tests/fixtures/ocr")
    ap.add_argument("--limit", type=int, help="앞에서 N건만")
    ap.add_argument("--layout", help="특정 레이아웃만")
    ap.add_argument("--json", help="결과 JSON 저장 경로")
    ap.add_argument("--model", help="비전 모델(기본: 설정값). 예: gpt-5.5, gemini-2.5-pro")
    ap.add_argument("--no-preprocess", action="store_true", help="전처리(조명 평탄화·확대) 끄기")
    ap.add_argument("--no-double-read", action="store_true", help="두 번째 판독(절반 확대본) 끄기")
    ap.add_argument("--text-layer", action="store_true", help="전용 OCR(Tesseract) 텍스트 층을 참고로 붙이기")
    args = ap.parse_args()
    logging.disable(logging.INFO)

    fx = ROOT / args.fixtures if not Path(args.fixtures).is_absolute() else Path(args.fixtures)
    man_path = fx / "manifest.json"
    if not man_path.exists():
        sys.exit(f"픽스처가 없다: {man_path}\n  먼저 python3 scripts/generate_result_sheets.py 실행")
    items = json.loads(man_path.read_text(encoding="utf-8"))["items"]
    if args.layout:
        wanted = set(args.layout.split(","))
        items = [i for i in items if i["layout"] in wanted]
    if args.limit:
        items = items[:args.limit]

    from config.settings import settings
    if not (settings.openai_api_key or "").strip():
        sys.exit("OPENAI_API_KEY 가 없다. OCR 은 LLM 이 필요하므로 측정할 수 없다.")
    from services.ocr_service import get_ocr_service
    svc = get_ocr_service()
    idx = build_alias_index()

    rows, out = [], []
    for it in items:
        truth = json.loads((fx / it["truth"]).read_text(encoding="utf-8"))
        t0 = time.time()
        try:
            got = svc.extract_from_image(fx / it["image"], model=args.model,
                                         preprocess=not args.no_preprocess,
                                         double_read=not args.no_double_read,
                                         text_layer=args.text_layer)
            sc = score_one(truth, got, idx)
        except Exception as e:  # noqa: BLE001
            sc = {"error": str(e)[:160]}
        sc.update({"model": args.model or settings.openai_vision_model,
                   "image": it["image"], "layout": it["layout"], "degrade": it["degrade"],
                   "seconds": round(time.time() - t0, 1)})
        out.append(sc)
        rows.append(sc)
        print(f"  {it['image']:40s} "
              + (f"ERROR {sc['error']}" if "error" in sc else
                 f"recall {sc['recall']:5}%  prec {sc['precision']:5}%  "
                 f"value {sc['value']}  class {sc['class']}  size {sc['size']}  "
                 f"({sc['seconds']}s)"))

    ok = [r for r in rows if "error" not in r]
    if ok:
        def avg(k):
            vals = [r[k] for r in ok if r.get(k) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        print("\n--- 평균 ---")
        print(f"  검사종류 정답  {sum(1 for r in ok if r['test_type_ok'])}/{len(ok)}")
        for k, label in (("recall", "재현율"), ("precision", "정밀도"),
                         ("value", "수치"), ("class", "class"), ("size", "팽진크기")):
            v = avg(k)
            if v is not None:
                print(f"  {label:8s} {v}%")
        tp = sum(r["truth_positive"] for r in ok)
        print(f"  양성 누락  {sum(r['missed_positive'] for r in ok)}/{tp}  "
              f"위양성 {sum(r['false_positive'] for r in ok)}  10배오독 {sum(r['x10_error'] for r in ok)}")
        ce, cf = sum(r['clin_err'] for r in ok), sum(r['clin_flagged'] for r in ok)
        co, cof = sum(r['correct'] for r in ok), sum(r['correct_flagged'] for r in ok)
        print(f"  확인표시   임상오류 {cf}/{ce} 포착 · 맞는 행 {cof}/{co} 괜히 표시")
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n저장: {args.json}")


if __name__ == "__main__":
    main()
