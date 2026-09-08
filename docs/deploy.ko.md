# 웹 서비스 배포 가이드 (탐험 퀘스트 UI `/` + 클래식 UI `/classic/` + API)

> 한 개의 FastAPI 프로세스가 API 와 두 UI 정적 파일을 함께 서빙합니다. 데이터베이스·세션 저장이 없고(무상태), 외부 의존은 **OpenAI API 키 1개**뿐입니다. 컨테이너 1개로 어디서나 띄울 수 있습니다.

## 0. 런타임 구성 (현재 코드 기준)

| 구성 | 값 | 위치 |
|---|---|---|
| 서버 | FastAPI + uvicorn, Python 3.12 | `server.py` |
| OCR(VLM) | **OpenAI `gpt-4o`** (vision, `detail: "high"`), Chat Completions API | `config/settings.py` `OPENAI_VISION_MODEL`, `services/ocr_service.py` |
| 리포트 내러티브(선택) | **OpenAI `gpt-4o`**, Chat Completions API — 기본은 결정론적 리포트(LLM 미사용) | `OPENAI_REPORT_MODEL`, `services/report_service.py` |
| 문진·판정·교차반응·FHIR·카드뉴스 | 규칙/데이터 엔진 (LLM 미사용) | `services/*` |
| API 클라이언트 | `openai` Python SDK (`client.chat.completions.create`) | |
| 필수 환경변수 | `OPENAI_API_KEY` (없으면 데모/직접입력만 동작) | `.env` 또는 플랫폼 시크릿 |
| 선택 환경변수 | `OPENAI_VISION_MODEL`, `OPENAI_REPORT_MODEL`, `APP_ENV`, `DEBUG`, `PORT` | |
| 헬스체크 | `GET /api/health` → `build.commit`, `features.ui_modes` | |

모델 교체는 코드 수정 없이 환경변수로 합니다(예: `OPENAI_VISION_MODEL=gpt-4.1` 또는 `gpt-4o-mini`). 단계별 모델 추천·비용은 `docs/llm_model_and_cost.md` 참고(환자 1인 표준 실행 ≈ $0.024).

## 1. 가장 빠른 길 — Render (Docker, Blueprint)
1. GitHub 저장소를 Render 에 연결 → **New + → Blueprint** → 저장소 선택. 루트의 `render.yaml` 이 읽힙니다.
2. 환경변수 `OPENAI_API_KEY` 를 시크릿으로 입력.
3. 배포 완료 후 `https://<서비스명>.onrender.com/` (퀘스트 UI), `/classic/` (클래식 UI), `/api/health`.
4. 커스텀 도메인은 Render 설정에서 추가(자동 HTTPS).

무료 플랜은 15분 유휴 시 슬립되어 첫 요청이 30초쯤 걸립니다. 시연용이면 충분하고, 상시 서비스면 starter 이상을 권합니다.

## 2. Railway / Fly.io (Docker 자동 인식)
- **Railway**: New Project → Deploy from GitHub → Dockerfile 자동 감지 → Variables 에 `OPENAI_API_KEY` → Settings 에서 Public Networking 활성화. `PORT` 는 Railway 가 주입하며 Dockerfile 이 그대로 사용합니다.
- **Fly.io**: `fly launch --no-deploy` → `fly secrets set OPENAI_API_KEY=...` → `fly deploy`. 리전 `nrt`(도쿄) 또는 `sin`.

## 3. 직접 운영(VPS: Ubuntu + Docker + Caddy)
```bash
git clone https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR.git && cd APAAACI_mast_to_HL7FHIR
cp config/env_sample.txt .env && nano .env            # OPENAI_API_KEY 입력
docker build -t allergy-report .
docker run -d --name allergy --restart unless-stopped -p 127.0.0.1:8000:8000 --env-file .env allergy-report
```
Caddy 로 HTTPS 리버스 프록시(자동 인증서):
```
# /etc/caddy/Caddyfile
allergy.example.org {
    reverse_proxy 127.0.0.1:8000
    request_body { max_size 12MB }      # 검사지 이미지 업로드(기본 10MB 제한)
}
```
Docker 없이: `python -m venv .venv && pip install -r requirements.txt && uvicorn server:app --host 0.0.0.0 --port 8000 --workers 2` 를 systemd 서비스로 등록.

## 4. 로컬 확인 (배포 전)
```bash
docker build -t allergy-report . && docker run --rm -p 8000:8000 --env-file .env allergy-report
curl -s localhost:8000/api/health | python3 -m json.tool | head
```

## 5. 운영 체크리스트
- **개인정보**: 서버는 저장하지 않지만(무상태) 업로드 이미지·환자명이 요청 본문으로 오갑니다. 반드시 HTTPS, 접근 로그에 본문 미기록, 필요 시 IP 허용 목록/기본 인증(Caddy `basicauth`) 추가. 실제 환자 데이터로 운영하려면 기관 보안 검토·OpenAI 데이터 처리 조건(API 입력은 학습에 사용되지 않음, 30일 보관 정책) 확인.
- **비용 가드**: OpenAI 대시보드에서 월 사용 한도 설정. OCR 1장 ≈ 1.1K 이미지 토큰 + 출력 1.4~3K 토큰.
- **정적 자산 캐시**: 배포 후 브라우저가 이전 `app.js` 를 캐시할 수 있어 강력 새로고침 안내(또는 프록시에서 `/app.js`,`/styles.css` 에 `Cache-Control: no-cache`).
- **모니터링**: `/api/health` 를 업타임 체크에 등록. 응답의 `build.commit` 으로 배포 버전 확인.
- **스케일**: 무상태이므로 `--workers` 증가나 인스턴스 수 증가로 수평 확장. OCR 호출은 외부 API 지연(5~20초)이 지배적.
- **비활성화 옵션**: 키 없이 배포하면 OCR 만 비활성(업로드 시 안내), 데모·직접입력·문진·리포트·FHIR 는 모두 동작.

## 6. 자주 묻는 질문
- **클래식 UI 만 노출하고 싶다** → 프록시에서 `/` 를 `/classic/` 로 리다이렉트하거나, `server.py` 마운트 순서를 바꿔 기본을 클래식으로.
- **API 만 쓰고 프론트는 다른 곳에서** → `/api/*` 만 프록시하고 CORS 미들웨어를 추가(현재는 동일 출처 가정으로 CORS 없음).
- **Streamlit 레거시(`app.py`)** 는 배포 대상이 아닙니다.
