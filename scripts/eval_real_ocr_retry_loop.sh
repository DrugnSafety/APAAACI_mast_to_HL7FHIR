#!/bin/zsh
# 연구실 Ollama 서버가 살아나길 기다렸다가, 실패(error·2차 판독 실패)로 남은 사진만 최대 2회 더 돌린다.
# 2026-10-09 실제 결과지 평가에서 서버가 두 번 끊겨(응답 없는 연결 1시간 50분 포함) 만든 보조 스크립트.
cd "$(dirname "$0")/.." || exit 1
LOG=output/ocr_eval_2026-10-09/logs/ollama_retry.log
PY=.venv/bin/python
while pgrep -f "eval_real_ocr.py run --backend ollama" >/dev/null; do sleep 30; done
for pass in 1 2; do
  left=$($PY -c "import json,glob;print(sum(1 for f in glob.glob('output/ocr_eval_2026-10-09/runs/ollama/*.json') if 'error' in (d:=json.load(open(f))) or (((d.get('result') or {}).get('metadata') or {}).get('double_read') or {}).get('error')))")
  echo "[loop] pass $pass: $left remaining $(date +%H:%M)" | tee -a "$LOG"
  [ "$left" = "0" ] && break
  for i in {1..120}; do   # 서버 복구 대기(최대 60분)
    if $PY - <<'PYEOF' 2>/dev/null
import httpx, sys
from config.settings import settings
r = httpx.get(settings.ollama_base_url.rstrip('/') + "/api/ps", headers={"Authorization": f"Bearer {settings.ollama_api_key}"}, timeout=15)
sys.exit(0 if r.status_code == 200 else 1)
PYEOF
    then break; fi
    sleep 30
  done
  echo "[loop] server up, retrying $(date +%H:%M)" | tee -a "$LOG"
  OLLAMA_TIMEOUT=480 $PY scripts/eval_real_ocr.py run --backend ollama --workers 1 --retry-errors --max-seconds 1200 >> "$LOG" 2>&1
done
echo "[loop] done $(date +%H:%M)" | tee -a "$LOG"
