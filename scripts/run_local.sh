#!/usr/bin/env bash
# 로컬 실행 — 화면을 열어 확인할 때 쓴다.
#
#   ./scripts/run_local.sh          # 127.0.0.1:8100
#   PORT=9000 ./scripts/run_local.sh
#
# 포트가 이미 쓰이고 있으면 알려주고 멈춘다(엉뚱한 프로세스에 접속해 "화면이 안 나온다"고
# 헤매는 일을 막기 위해서다).
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8100}"
HOST="${HOST:-127.0.0.1}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "포트 $PORT 가 이미 사용 중입니다. 다른 포트로 실행하세요:"
  echo "  PORT=9000 $0"
  exit 1
fi

if [ ! -f .env ]; then
  echo "경고: .env 가 없습니다. OCR·챗봇·번역은 OPENAI_API_KEY 가 있어야 동작합니다."
fi

echo "다음 주소에서 확인하세요:"
echo "  화면          http://$HOST:$PORT"
echo "  검토(의료진)   http://$HOST:$PORT/review"
echo "  클래식 UI      http://$HOST:$PORT/classic"
echo "  API 문서       http://$HOST:$PORT/docs"
echo

exec python -m uvicorn server:app --host "$HOST" --port "$PORT" "$@"
