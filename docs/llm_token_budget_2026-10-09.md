# OpenAI 모델 변경·토큰 예산 라우팅·키 묶음·초과 알림 (2026-10-09/10)

## 결정
- OpenAI 기본 모델(OCR·상담·번역): **gpt-5.4 → gpt-6-luna**. 이미지 입력·JSON 모드 확인, 실제 결과지 3장(A10·B03·O03)에서 gpt-5.4 와 같은 정확도(모두 100%), 장당 37~40초(gpt-5.4 58~64초).
- **토큰 예산 라우팅**: 누적 **1,000만 토큰까지는 gpt-5.6-luna**, 넘으면 gpt-6-luna. OCR·상담·번역 모두 같은 규칙.

## 설정 (`config/settings.py` / `.env` / `render.yaml`)

| 변수 | 기본값 | 뜻 |
|---|---|---|
| `OPENAI_VISION_MODEL` | gpt-6-luna | 예산 소진 뒤 OCR 모델 |
| `OPENAI_CHAT_MODEL` | gpt-6-luna | 예산 소진 뒤 상담·번역 모델 |
| `OPENAI_BUDGET_MODEL` | gpt-5.6-luna | 예산 안에서 먼저 쓰는 모델. 빈 값이면 라우팅 끔 |
| `OPENAI_BUDGET_TOKENS` | 10000000 | 예산(prompt+completion, 추론 토큰 포함) |
| `LLM_USAGE_LEDGER` | data/store/llm_usage.json | 사용량 장부(gitignored) |
| `OPENAI_REPORT_MODEL` / `OPENAI_MODEL` | gpt-4o | 레거시 리포트·챗봇 경로(변경 없음) |

gpt-5·gpt-6 계열은 `temperature`/`max_tokens` 를 거부하고 `max_completion_tokens`·`reasoning_effort` 를 받는다. 추론 모델 판별은
`services/ocr_service.py`, `services/llm_backend.py`, `services/result_chat_service.py` 세 곳(`gpt-5`, `gpt-6`, `o1/o3/o4` 접두사).

## 동작 (`services/llm_usage.py`)
- 성공한 OpenAI 호출마다 `usage` 를 모델별로 누적(prompt·completion·reasoning·total·requests). 실패 호출·로컬(Ollama) 호출은 세지 않는다.
- `llm_backend.vision_model()` / `chat_model()` 이 `llm_usage.choose()` 로 모델을 고른다: 예산 모델 누적 total < 예산 → 예산 모델, 아니면 기본 모델.
- 한 요청 안에서는 처음 고른 모델을 끝까지 쓴다(두 번 읽기의 1·2차 판독이 같은 모델).
- 화면 'AI 엔진' 정보(`llm_backend.describe()`)에 `budget`(사용량·잔여·소진 여부)이 함께 나간다.
- 상태 보기: `python3 -m services.llm_usage`

## 한계
- OpenAI 사용량 API 는 프로젝트 키로 읽을 수 없어(권한 `api.usage.read` 없음) 우리 장부가 기준이다. 장부 도입 전 호출(2026-10-09 평가 84회 ≈ 60만~80만 토큰 추정)은 들어 있지 않다.
- 여러 워커 프로세스가 같은 파일을 갱신하면 몇 건 어긋날 수 있다. Render 처럼 디스크가 휘발성이면 배포마다 0 에서 다시 센다 — 영구 디스크를 붙이거나 `LLM_USAGE_LEDGER` 를 그쪽으로 둔다.

## API 키를 바꿨을 때
1. 프로젝트 루트 `.env` 의 `OPENAI_API_KEY=` 값을 새 키로 바꾼다(`config/env_sample.txt` 가 템플릿, `.env` 는 gitignored — 커밋되지 않는다).
2. 서버를 재시작한다(설정은 시작 시 읽는다). 확인: `python3 check_setup.py`, 또는 `python3 -c "from services import llm_backend as lb; print(lb.is_available('openai'))"`.
3. 배포(Render)는 대시보드 환경변수 `OPENAI_API_KEY` 를 바꾸고 재배포한다(`render.yaml` 에는 `sync: false` 로 값이 없다).
4. 연구실 Ollama 토큰은 `OLLAMA_API_KEY`, 주소는 `OLLAMA_BASE_URL` — 같은 `.env`.

## 테스트
`python3 -m pytest test_llm_budget.py test_result_chat.py -q` — 장부 누적·예산 전환·백엔드 모델 선택·complete_chat 기록.

## 키 묶음 round-robin (2026-10-10)
- `.env` 에 `OPENAI_API_KEY_1` ~ `OPENAI_API_KEY_8` 로 여러 키를 두면 OpenAI 호출마다 번호순으로 돌아가며 쓴다
  (`config/settings.py` 의 `openai_api_key_1..8` → `services/llm_backend.openai_keys` / `client("openai")`).
  번호 키가 하나라도 있으면 `OPENAI_API_KEY` 는 쓰지 않는다. 이름은 정확히 이 형식이어야 하고(다른 이름은 무시),
  빈 값·자리표시자·중복 값은 뺀다. 테스트(conftest)는 번호 키를 비워 실제 호출이 나가지 않게 한다.
- OCR(`ocr_service`)·상담(`result_chat_service`)·번역(`translation_service`→`complete_chat`)·리포트(`report_service`)·레거시 챗봇(`chatbot_service`) 모두 호출 때마다 다음 키를 받는다.
- 키 값은 로그·화면 어디에도 안 나간다. 상태는 키 개수와 키별 요청 수만(`llm_backend.key_stats()`, 화면 'AI 엔진' 정보의 `keys`).
- 키를 추가·교체하면 서버를 재시작한다(.env 는 시작 시 읽는다).

## 무료 한도 초과 알림 메일 (2026-10-10)
- 예산 모델(gpt-5.6-luna)의 누적이 `OPENAI_BUDGET_TOKENS` 를 넘기는 호출에서 장부에 `budget_crossed_at` 을 적고,
  `OPENAI_BUDGET_NOTIFY_TO`(기본 `irreversibly@gmail.com,kintel@kintel.kr`)로 "무료 토큰 한도 초과 — 유료 모델(gpt-6-luna) 사용 중" 메일을 **한 번** 보낸다.
- 메일은 기존 `services/email_service.py`(SMTP) 로 나간다. **`SMTP_HOST`·`SMTP_FROM`(+`SMTP_USER`/`SMTP_PASSWORD`)이 `.env` 에 없으면 보내지 못하고**
  장부에 `smtp_unconfigured` 로 남겼다가 1시간마다 다시 시도한다. 예: Gmail 은 `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_TLS=starttls`,
  `SMTP_USER=<gmail 주소>`, `SMTP_PASSWORD=<앱 비밀번호>`, `SMTP_FROM=<gmail 주소>`.
- 확인: `python3 -m services.llm_usage --notify-test` (수신자에게 '[테스트]' 메일), `--notify-now` (못 보낸 알림 즉시 발송), 인자 없이 → 상태.
