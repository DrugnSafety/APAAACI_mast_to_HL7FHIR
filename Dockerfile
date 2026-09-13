# 알러젠 탐험 퀘스트 / 클래식 UI + FastAPI API — 단일 컨테이너
# 빌드:  docker build -t allergy-report .
# 실행:  docker run -p 8000:8000 --env-file .env allergy-report
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production DEBUG=False PORT=8000

WORKDIR /app

# 시스템 폰트(PDF/리포트 한글) + 최소 빌드 도구
RUN apt-get update && apt-get install -y --no-install-recommends fonts-nanum curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p output && useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://127.0.0.1:${PORT}/api/health || exit 1

# 멀티 프로세스가 필요하면 --workers 2 추가(요청은 무상태·세션 저장 없음)
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
