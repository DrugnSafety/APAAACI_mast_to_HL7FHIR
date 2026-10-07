# 알러젠 탐험 퀘스트 / 클래식 UI + FastAPI API — 단일 컨테이너
# 빌드:  docker build -t allergy-report .
# 실행:  docker run -p 8000:8000 --env-file .env allergy-report
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_ENV=production DEBUG=False PORT=8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

WORKDIR /app

# 리포트 PDF(services/report_pdf.py, WeasyPrint):
#   - libpango·libpangoft2·libharfbuzz-subset: WeasyPrint 가 조판에 쓰는 시스템 라이브러리(헤드리스 브라우저는 쓰지 않는다)
#   - fonts-noto-cjk: 한글·한자(간체 포함)·가나를 가진 Noto Sans CJK. SIL OFL 1.1 — 재배포와 PDF 임베드가 허용된다.
#     한국어 리포트는 'Noto Sans CJK KR', 중국어 리포트는 'Noto Sans CJK SC' 로 찍힌다.
#   - fonts-nanum: 나눔고딕(OFL). Noto 가 빠진 이미지에서의 예비 한글 글꼴.
# 글꼴이 없으면 서버는 빈 상자가 찍힌 PDF 를 내보내지 않고 503 pdf_unavailable 을 돌려준다(/api/health 의 services.pdf).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk fonts-nanum curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p output data/store && useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://127.0.0.1:${PORT}/api/health || exit 1

# 세션·산출물은 SQLite(data/store, APP_DB_PATH/I18N_DB_PATH)에 저장된다 — 보존하려면 볼륨을 붙인다.
# 멀티 프로세스가 필요하면 --workers 2 추가(로그인 시도 제한·IP 당 요청 수 제한은 프로세스별로 센다)
#
# 접속 IP(X-Forwarded-For) 신뢰 범위: FORWARDED_ALLOW_IPS 에 적힌 주소에서 온 연결의 전달 헤더만 믿는다.
# 기본값 127.0.0.1 은 '아무 프록시도 믿지 않음'이다. 예전의 '*' 는 누구나 X-Forwarded-For 를 꾸며
# 로그인 시도 제한을 피하거나 남의 IP 를 잠글 수 있었다. 프록시 뒤에 둘 때는 그 프록시의 주소·대역을
# 넣는다(쉼표 구분, CIDR 가능. 예: 10.0.0.0/8). 넣지 않으면 모든 사용자가 프록시 IP 하나로 보여
# IP 당 한도를 함께 쓴다.
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
