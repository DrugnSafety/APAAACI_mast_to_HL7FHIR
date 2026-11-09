#!/usr/bin/env python3
"""실제 결과지 사진으로 OCR 을 돌려 결과를 저장한다 — 백엔드(OpenAI / 연구실 Ollama)별로.

합성 픽스처 평가(scripts/score_ocr.py)와 달리 정답이 함께 오지 않는다. 여기서는 OCR 만 돌려
JSON 으로 남기고, 채점·비교는 scripts/eval_real_ocr_report.py 가 정답 전사(truth/)와 맞춰서 한다.

사용
  python3 scripts/eval_real_ocr.py manifest                 # input_images/ → manifest.json (id 부여)
  python3 scripts/eval_real_ocr.py run --backend openai --workers 3
  python3 scripts/eval_real_ocr.py run --backend ollama --workers 1
  python3 scripts/eval_real_ocr.py run --backend openai --rotate --ids A01,A02 --tag rotated

출력: output/ocr_eval_2026-10-09/runs/<tag or backend>/<id>.json
  이미 있는 결과는 건너뛴다(중단 뒤 이어서 돌릴 수 있다).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EVAL_DIR = ROOT / "output" / "ocr_eval_2026-10-09"
IMG_DIR = ROOT / "input_images"


def build_manifest() -> List[Dict[str, Any]]:
    """input_images/ 의 사진에 짧은 id 를 붙인다. A = 10-41 배치, B = 10-42 배치, O = 기존 샘플."""
    from PIL import Image, ImageOps
    items: List[Dict[str, Any]] = []
    files = sorted(IMG_DIR.iterdir())
    counters = {"A": 0, "B": 0, "O": 0}
    for f in files:
        if f.name.startswith(".") or f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        if "10-41" in f.name:
            group = "A"
        elif "10-42" in f.name:
            group = "B"
        else:
            group = "O"
        counters[group] += 1
        im = ImageOps.exif_transpose(Image.open(f))
        items.append({
            "id": f"{group}{counters[group]:02d}",
            "file": f.name,
            "width": im.width, "height": im.height,
            "landscape": im.width > im.height,
        })
    return items


def load_manifest() -> List[Dict[str, Any]]:
    p = EVAL_DIR / "manifest.json"
    if not p.exists():
        sys.exit(f"manifest 가 없다: {p} — 먼저 `manifest` 를 실행")
    return json.loads(p.read_text(encoding="utf-8"))["items"]


def _rotated_copy(src: Path, item: Dict[str, Any], tmp_dir: Path) -> Path:
    """누운 사진(가로)을 세워서 임시 파일로 — 회전 보정 실험용. 10-41 배치는 시계 방향 90도가 정방향."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    if im.width > im.height and item["id"].startswith("A"):
        im = im.rotate(-90, expand=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out = tmp_dir / (src.stem + ".jpg")
    im.save(out, quality=95)
    return out


def run_one(item: Dict[str, Any], backend: str, model: Optional[str], out_dir: Path,
            rotate: bool, double_read: bool, preprocess: bool, text_layer: bool,
            guard: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from services.ocr_service import get_ocr_service
    svc = get_ocr_service()
    src = IMG_DIR / item["file"]
    if rotate:
        src = _rotated_copy(src, item, EVAL_DIR / "rotated_inputs")
    t0 = time.time()
    rec: Dict[str, Any] = {"id": item["id"], "file": item["file"], "backend": backend,
                           "rotate": rotate, "double_read": double_read, "preprocess": preprocess,
                           "text_layer": text_layer}
    try:
        got = svc.extract_from_image(src, backend=backend, model=model,
                                     double_read=double_read, preprocess=preprocess,
                                     text_layer=text_layer)
        rec["seconds"] = round(time.time() - t0, 1)
        rec["result"] = json.loads(got.model_dump_json(by_alias=False))
        rec["model"] = (got.metadata or {}).get("model")
    except Exception as e:  # noqa: BLE001 — 한 장의 실패가 전체를 막지 않는다
        rec["seconds"] = round(time.time() - t0, 1)
        rec["error"] = f"{type(e).__name__}: {str(e)[:400]}"
        rec["traceback"] = traceback.format_exc()[-2000:]
    if guard and guard.get("cancelled"):
        return rec          # 벽시계 상한을 넘겨 이미 실패로 기록된 장 — 늦게 끝난 결과로 덮어쓰지 않는다
    _write_json(out_dir / f"{item['id']}.json", rec)
    return rec


def _write_json(path: Path, rec: Dict[str, Any]) -> None:
    """원자적 쓰기 — 채점기가 쓰는 도중의 파일을 읽지 않게."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser(description="실제 결과지 OCR 실행")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("manifest")
    r = sub.add_parser("run")
    r.add_argument("--backend", choices=["openai", "ollama"], required=True)
    r.add_argument("--model")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--ids", help="쉼표로 구분한 id 목록(기본: 전체)")
    r.add_argument("--tag", help="출력 폴더 이름(기본: backend)")
    r.add_argument("--rotate", action="store_true", help="누운 사진을 세워서 넣는다(회전 보정 실험)")
    r.add_argument("--no-double-read", action="store_true")
    r.add_argument("--no-preprocess", action="store_true")
    r.add_argument("--text-layer", action="store_true")
    r.add_argument("--force", action="store_true", help="이미 있는 결과도 다시 돌린다")
    r.add_argument("--retry-errors", action="store_true", help="실패(error)로 저장된 결과만 다시 돌린다")
    r.add_argument("--max-seconds", type=int, default=1200, help="장당 벽시계 상한(초, workers=1 일 때). 넘기면 실패 처리")
    args = ap.parse_args()

    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    if args.cmd == "manifest":
        items = build_manifest()
        (EVAL_DIR / "manifest.json").write_text(
            json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
        for it in items:
            print(f"  {it['id']}  {it['width']}x{it['height']}{'  (누움)' if it['landscape'] else ''}  {it['file']}")
        return

    logging.basicConfig(level=logging.WARNING)
    from services import llm_backend
    if not llm_backend.is_available(args.backend):
        sys.exit(f"{args.backend} 백엔드의 키/주소가 없다")
    items = load_manifest()
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if i["id"] in wanted]
    out_dir = EVAL_DIR / "runs" / (args.tag or args.backend)
    out_dir.mkdir(parents=True, exist_ok=True)
    def _needs_run(i):
        p = out_dir / f"{i['id']}.json"
        if args.force or not p.exists():
            return True
        if args.retry_errors:
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                dr = ((d.get("result") or {}).get("metadata") or {}).get("double_read") or {}
                # 두 번째 판독만 실패한 결과(413·타임아웃)도 다시 돌린다
                return "error" in d or (isinstance(dr, dict) and bool(dr.get("error")))
            except Exception:  # noqa: BLE001
                return True
        return False
    todo = [i for i in items if _needs_run(i)]
    print(f"{args.backend} ({args.model or llm_backend.vision_model(args.backend)}): "
          f"{len(todo)}/{len(items)} 장 → {out_dir}", flush=True)

    kw = dict(backend=args.backend, model=args.model, out_dir=out_dir, rotate=args.rotate,
              double_read=not args.no_double_read, preprocess=not args.no_preprocess,
              text_layer=args.text_layer)

    def report(rec):
        n = len((rec.get("result") or {}).get("results") or [])
        status = f"ERROR {rec['error']}" if "error" in rec else f"{n:3d} rows"
        print(f"  {rec['id']}  {rec['seconds']:6.1f}s  {status}", flush=True)

    if args.workers == 1:
        # 장당 벽시계 상한 — 연구실 서버가 응답 없이 연결만 쥐고 있으면 SDK 타임아웃이 안 걸린다
        # (2026-10-09 실측: 한 요청이 1시간 50분 멈춤). 넘기면 실패로 적고 다음 장으로 간다.
        import threading
        for it in todo:
            box: Dict[str, Any] = {}
            guard: Dict[str, Any] = {"cancelled": False}
            t = threading.Thread(target=lambda: box.update(run_one(it, guard=guard, **kw)), daemon=True)
            t0 = time.time()
            t.start()
            t.join(args.max_seconds)
            if t.is_alive():
                guard["cancelled"] = True        # 늦게 끝나도 결과를 쓰지 않는다
                rec = {"id": it["id"], "file": it["file"], "backend": args.backend,
                       "seconds": round(time.time() - t0, 1),
                       "error": f"WallClockTimeout: {args.max_seconds}s 안에 응답 없음(서버 멈춤)"}
                _write_json(out_dir / f"{it['id']}.json", rec)
                report(rec)
                # 버린 스레드는 SDK 타임아웃(OLLAMA_TIMEOUT)까지 서버 응답을 기다리다 죽는다 — 그동안은 다음 장과 겹친다
                print("  (멈춘 요청은 버리고 다음 장으로 간다)", flush=True)
                continue
            report(box)
        return

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, it, **kw): it for it in todo}
        for fut in as_completed(futs):
            report(fut.result())


if __name__ == "__main__":
    main()
