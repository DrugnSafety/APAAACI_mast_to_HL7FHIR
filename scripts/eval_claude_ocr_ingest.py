#!/usr/bin/env python3
"""Claude(Aside 에이전트)가 직접 읽은 OCR JSON 을 앱의 OCR 후처리에 그대로 통과시켜 평가용 run 으로 저장한다.

배경(2026-10-29): OpenAI 키 일부가 401 로 거절되고 크레딧 문제가 있어, API 호출 없이 Claude 가
앱 프롬프트(services/ocr_service._get_default_prompt)를 그대로 받아 사진을 읽었다(맹검: 정답·기존 결과 미열람).
여기서는 모델 호출(_call_vision_model)만 그 JSON 으로 바꾸고, 파싱·항원 매핑·검증 규칙은 앱 코드 그대로 쓴다.
두 번 읽기는 하지 않는다(모델 호출이 없으므로) — 기존 gpt-5.4·qwen 실행과의 차이로 기록한다.

사용
  python3 scripts/eval_claude_ocr_ingest.py --src <claude_ocr 폴더> [--tag claude_opus55_20261029] [--model claude-opus-5-5]
출력: output/ocr_eval_2026-10-09/runs/<tag>/<id>.json  (eval_real_ocr.py 와 같은 형식)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
EVAL_DIR = ROOT / "output" / "ocr_eval_2026-10-09"
IMG_DIR = ROOT / "input_images"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="<ID>.json 이 있는 폴더(Claude 판독 원본)")
    ap.add_argument("--tag", default="claude_opus55_20261029")
    ap.add_argument("--model", default="claude-opus-5-5")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)

    from services.ocr_service import get_ocr_service
    svc = get_ocr_service()
    items = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))["items"]
    out_dir = EVAL_DIR / "runs" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(args.src)
    done = 0
    for it in items:
        p = src / f"{it['id']}.json"
        rec = {"id": it["id"], "file": it["file"], "backend": "claude", "model": args.model,
               "rotate": False, "double_read": False, "preprocess": False, "text_layer": False,
               "seconds": None, "source": "Aside agent direct read (no API call)"}
        if not p.exists():
            rec["error"] = "MissingTranscription: Claude 판독 파일 없음"
        else:
            content = p.read_text(encoding="utf-8")
            json.loads(content)  # 형식 확인
            svc._call_vision_model = lambda model, prompt, images, backend=None, _c=content: _c  # noqa: E731
            t0 = time.time()
            try:
                got = svc.extract_from_image(IMG_DIR / it["file"], model=args.model,
                                             double_read=False, preprocess=False, text_layer=False)
                res = json.loads(got.model_dump_json(by_alias=False))
                res.setdefault("metadata", {})["backend"] = "claude"
                rec["result"] = res
                rec["postprocess_seconds"] = round(time.time() - t0, 2)
                done += 1
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        tmp = out_dir / f"{it['id']}.json.tmp"
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out_dir / f"{it['id']}.json")
        n = len((rec.get("result") or {}).get("results") or [])
        print(f"  {it['id']}  {'ERROR ' + rec['error'] if 'error' in rec else f'{n:3d} rows'}")
    print(f"{done}/{len(items)} → {out_dir}")


if __name__ == "__main__":
    main()
