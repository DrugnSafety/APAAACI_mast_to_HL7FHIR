#!/usr/bin/env python3
"""실제 결과지 OCR 을 Claude Code CLI(사용자 Claude 구독 계정)로 돌린다 — API 키 없이.

앱의 OCR 경로(services/ocr_service.extract_from_image: 전처리 → 1차 판독 → 위·아래 절반 2차 판독 →
병합 → 검증)를 그대로 쓰고, 모델 호출(_call_vision_model)만 `claude -p` 로 바꾼다.
이미지(base64)는 임시 파일로 써서 CLI 의 Read 도구로 읽게 한다. 프롬프트·시스템 문구는 앱과 같다.

사용
  python3 scripts/eval_real_ocr_claude_cli.py --workers 4 [--ids A01,A02] [--model opus] [--tag claude_cli_opus_20261029]
전제: `claude auth status` 가 loggedIn(claude.ai) 이어야 한다.
출력: output/ocr_eval_2026-10-09/runs/<tag>/<id>.json  (eval_real_ocr.py 와 같은 형식, 이미 있으면 건너뜀)
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "output" / "ocr_eval_2026-10-09"
IMG_DIR = ROOT / "input_images"
CLAUDE = shutil.which("claude") or "/opt/homebrew/bin/claude"

_usage_lock = threading.Lock()


def _ext(raw: bytes) -> str:
    return ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"


def make_caller(cli_model: str, timeout: int, usage: dict):
    from services.ocr_service import OCRService

    def call(model, prompt, images_b64, backend=None):
        with tempfile.TemporaryDirectory(prefix="ccocr_") as td:
            names = []
            for i, b64 in enumerate(images_b64, 1):
                raw = base64.b64decode(b64)
                name = f"image{i}{_ext(raw)}"
                (Path(td) / name).write_bytes(raw)
                names.append("./" + name)
            full_prompt = (prompt + "\n\nIMAGE FILES: " + ", ".join(names) +
                           "\nOpen every listed image with the Read tool (in the order listed) before answering. "
                           "Reply with the JSON object only — no prose, no code fences.")
            cmd = [CLAUDE, "-p", full_prompt, "--model", cli_model, "--tools", "Read", "--allowedTools", "Read",
                   "--system-prompt", OCRService._SYSTEM, "--strict-mcp-config", "--disable-slash-commands",
                   "--no-session-persistence", "--output-format", "json"]
            p = subprocess.run(cmd, cwd=td, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            if p.returncode != 0:
                raise RuntimeError(f"claude CLI exit {p.returncode}: {(p.stderr or p.stdout)[-300:]}")
            out = json.loads(p.stdout)
            if out.get("is_error"):
                raise RuntimeError(f"claude CLI error: {str(out.get('result'))[:300]}")
            with _usage_lock:
                usage["calls"] = usage.get("calls", 0) + 1
                usage["models"] = sorted(set(usage.get("models", [])) | set((out.get("modelUsage") or {}).keys()))
                for k, v in (out.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        usage[k] = usage.get(k, 0) + v
                usage["notional_cost_usd"] = round(usage.get("notional_cost_usd", 0) + (out.get("total_cost_usd") or 0), 4)
            return out.get("result") or ""
    return call


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opus", help="claude CLI 모델 별칭/이름")
    ap.add_argument("--tag", default="claude_cli_opus_20261029")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--ids")
    ap.add_argument("--timeout", type=int, default=900, help="CLI 호출 1회 상한(초)")
    ap.add_argument("--retry-errors", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    from services.ocr_service import get_ocr_service
    svc = get_ocr_service()
    usage: dict = {}
    svc._call_vision_model = make_caller(args.model, args.timeout, usage)

    items = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))["items"]
    if args.ids:
        wanted = set(args.ids.split(","))
        items = [i for i in items if i["id"] in wanted]
    out_dir = EVAL_DIR / "runs" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)

    def needs(it):
        p = out_dir / f"{it['id']}.json"
        if not p.exists():
            return True
        if args.retry_errors:
            d = json.loads(p.read_text(encoding="utf-8"))
            dr = ((d.get("result") or {}).get("metadata") or {}).get("double_read") or {}
            return "error" in d or (isinstance(dr, dict) and bool(dr.get("error")))
        return False

    todo = [i for i in items if needs(i)]
    print(f"claude CLI ({args.model}): {len(todo)}/{len(items)} 장 → {out_dir}", flush=True)

    def one(it):
        rec = {"id": it["id"], "file": it["file"], "backend": "claude-cli", "rotate": False, "double_read": True,
               "preprocess": True, "text_layer": False}
        t0 = time.time()
        try:
            got = svc.extract_from_image(IMG_DIR / it["file"], model=f"claude-cli:{args.model}",
                                         double_read=True, preprocess=True, text_layer=False)
            rec["seconds"] = round(time.time() - t0, 1)
            res = json.loads(got.model_dump_json(by_alias=False))
            res.setdefault("metadata", {})["backend"] = "claude-cli"
            rec["result"] = res
            rec["model"] = ", ".join(usage.get("models") or []) or f"claude-cli:{args.model}"
        except Exception as e:  # noqa: BLE001
            rec["seconds"] = round(time.time() - t0, 1)
            rec["error"] = f"{type(e).__name__}: {str(e)[:400]}"
            rec["traceback"] = traceback.format_exc()[-1500:]
        tmp = out_dir / f"{it['id']}.json.tmp"
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out_dir / f"{it['id']}.json")
        return rec

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(one, it) for it in todo]
        for f in as_completed(futs):
            r = f.result()
            n = len((r.get("result") or {}).get("results") or [])
            dr = ((r.get("result") or {}).get("metadata") or {}).get("double_read") or {}
            status = f"ERROR {r['error'][:160]}" if "error" in r else f"{n:3d} rows" + (" (2차 판독 실패)" if isinstance(dr, dict) and dr.get("error") else "")
            print(f"  {r['id']}  {r['seconds']:6.1f}s  {status}", flush=True)
    (out_dir / "_usage.json").write_text(json.dumps(usage, ensure_ascii=False, indent=1), encoding="utf-8")
    print("usage:", json.dumps(usage, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
