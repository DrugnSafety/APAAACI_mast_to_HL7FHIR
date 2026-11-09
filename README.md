# 🌿 알레르기 검사결과 환자용 리포트 플랫폼

알레르기 검사(피부반응검사·MAST·UniCAP) 결과를 업로드하면, **검사 양성이 실제 알레르기 증상을 유발하는지**를
환자 문진과 알러젠 지식베이스로 감별하여 **환자 맞춤형 리포트와 카드뉴스**를 생성하는 플랫폼입니다.

> **핵심 철학:** 검사 양성은 "감작(sensitization)"을 뜻할 뿐입니다. 실제 알레르기 질환은
> **해당 알러젠에 노출될 때(또는 그 계절에) 증상이 재현성 있게 나타날 때** 성립합니다.
> 이 플랫폼은 그 둘을 구분해, 정말 주의해야 할 알러젠에 집중하도록 돕습니다.

---

## 📋 주요 기능

1. **🔍 OCR 추출 + 점검** — GPT Vision으로 SPT/MAST/UniCAP 결과지를 읽고, 사용자가 표에서 직접 수정·확정
2. **🧑‍⚕️ 스크리닝 문진** — 알레르기 질환력, 복용 약제(항히스타민제의 SPT 위음성 주의), 증상의 계절/연중 패턴, 침범 장기(코·눈·하기도·피부·소화기·전신)
3. **📚 알러젠 backdata** — 양성 알러젠별 특성·생활사·노출환경·계절성·교차반응·회피수칙을 지식베이스에서 제공(없는 알러젠은 Wikipedia 외부검색으로 보강)
4. **🧬 임상적 의미 감별 (핵심)** — 알러젠별 노출·시즌·재현성 질문으로 **실제 알레르기(clinically_relevant) / 감작만(sensitized_only) / 관찰 필요(indeterminate)** 판정
5. **📄 맞춤 리포트** — 증상 유발 알러젠 중심 + 예방·관리 플랜(회피수칙·약물·면역치료·추적) Markdown/PDF
6. **🎨 카드뉴스** — 주요 결과를 공유용 카드뉴스(HTML)로 생성
7. **💬 결과 상담 챗봇** — 본인 판정 결과·지식베이스·문진 답변에만 근거해 답변, 진단·처방 금지, 응급 표현은 즉시 응급 안내
8. **🏥 HL7 FHIR 변환** — Observation(전체) + AllergyIntolerance(임상적으로 의미 있는 항목만 confirmed)

---

## 🔄 사용 흐름 (5단계)

| 단계 | 화면 | 내용 |
|------|------|------|
| 1 | 흔적 수집 (업로드 & OCR) | 검사지 이미지 업로드 → OCR → 결과 점검/수정 → 양성 항목을 도감에 '발견' 등록 |
| 2 | 탐험가 프로필 (환자정보 & 스크리닝) | 기본정보 + 알레르기 질환력·약제·증상 패턴·침범 장기 |
| 3 | 진범 감별 (양성 알러젠 감별 문진) | 알러젠별 backdata 확인 + 노출/시즌/재현성 질문 → 임상적 의미 판정 (챕터별 진행 링·XP) |
| 4 | 도감 완성 (리포트 & 카드뉴스) | 판정 도장(진범 확정/무혐의·감작만/관찰 대상) 도감 카드 + 맞춤 리포트(MD/PDF) + 카드뉴스(HTML) |
| 5 | FHIR | Observation(전체 결과: 양성·음성·0·<LoD·N/A) / AllergyIntolerance 번들 생성/다운로드 |

> 웹앱 UI는 **"알러젠 탐험 퀘스트 + 도감"** 메타포로 구성됩니다. XP·레벨·배지는 진행 행동에만 부여되며 판정 결과로 점수를 매기지 않습니다.
> 중증·아나필락시스 이력이 있으면 축하 연출을 억제하고 의료진 상담 안내를 우선합니다.
> 설계: `docs/superpowers/specs/2026-09-08-gamified-quest-ui-design.md` · UI 스모크: `scripts/ui_smoke.sh`(gstack browse) · 게임 로직 테스트: `node --test web/game.test.js`

### 🔀 듀얼 UI · 🏥 FHIR Observation 정밀화 (2026-09-08)
- **두 UI 동시 운영**: `/` = 알러젠 탐험 퀘스트 UI, `/classic/` = 재설계 이전 클래식 UI. 헤더 링크로 전환, 같은 `/api` 를 쓰므로 결과는 동일. (`/api/health` → `ui_modes`)
- **FHIR Observation**: 0·검출한계 미만(`<0.35` → `comparator "<"`)·N/A(`dataAbsentReason`) 결과도 모두 포함, MAST/UniCAP class 를 component 로 보존. `code`/`method` 는 tx.fhir.org(SNOMED CT 2025-02)로 검증한 SCTID 만 사용 — SPT 37968009 Prick test, MAST 399788006 + method 703446000 Immunoblot assay, UniCAP 397691009 + method 703447009 Enzyme immunoassay technique(+703444002).
- 상세(스크린샷·검증표·샘플 JSON): [`docs/release_2026-09-08_fhir_dual_ui.ko.md`](docs/release_2026-09-08_fhir_dual_ui.ko.md) · [English](docs/release_2026-09-08_fhir_dual_ui.en.md)
- **언어 선택(한/영/中)**: 두 UI 모두 헤더 선택기로 화면 문구 전환(`web/i18n.js`, 자동 감지·localStorage 저장).
- **맞춤 리포트 가독성**: 알러젠 블록을 배지 → 결론 → 지금 할 일 3가지 → 더 알아보기 순서로 재구성, 짧은 문장(humanizer 원칙).
- **웹 서비스 배포**: `Dockerfile`/`render.yaml` 포함. 컨테이너 1개 + `OPENAI_API_KEY` 로 Render·Railway·Fly.io·VPS 어디서나 — [`docs/deploy.ko.md`](docs/deploy.ko.md). 런타임 모델: OCR `gpt-4o`(vision), 리포트(선택) `gpt-4o`, 그 외 단계는 LLM 미사용.

### 🧬 SNOMED 147종 · 곰팡이 감별 · 결과 챗봇 · 서버 콘텐츠 다국어화 (2026-09-08 2차)
- **실제 SCTID 22 → 147종**: 항원 148종을 tx.fhir.org `$expand`/`$lookup` 으로 조회·활성 확인 후 카테고리 규칙(꽃가루는 `X pollen`, 동물은 dander/epithelium, 성분코드 `Cor a 8` 배제)으로 정교화. 혼합 항원은 구성 종이 패널 인서트에만 있어 성분 분해가 불가능하므로 더 넓지만 실재하는 개념(`782576004` Tree pollen, `722071008` Mold antigen)을 쓴다. 코드를 붙이지 않는 것은 음성 대조 1종뿐이며, OMOP 폴백은 `https://athena.ohdsi.org/search-terms/terms` 로 구분해 SNOMED 로 위장하지 않는다.
- **곰팡이 ↔ 집먼지진드기 감별**: '습할 때 악화'는 진드기도 양성이라 변별력이 없음. 공간 단서(욕실·지하실·누수·에어컨)·실외 포자 단서(낙엽·퇴비·비 온 뒤)·환경 조치 반응(침구 vs 제습) 문항을 추가하고, 구분 불가한 조합은 **판정 보류**로 남깁니다. 실내/실외 곰팡이는 회피법이 달라 `mold_habitat()` 으로 갈라 조언.
- **카드뉴스 분리**: `/classic/` 은 재설계 이전 카드뉴스, `/` 는 도감 테마. 판정·리포트·FHIR 는 동일.
- **결과 상담 챗봇**: `POST /api/chat`. 컨텍스트 밖 질문은 "이 결과로는 알 수 없다"로 답하고, 호흡곤란·아나필락시스 표현은 LLM 이전에 응급 안내로 가로챕니다. 추천 질문 5종은 API 키 없이도 판정 데이터로 답변.
- **서버 생성 콘텐츠 다국어화**: 문진·지식베이스·리포트·카드뉴스를 한/영/중으로. LLM 번역 + 영구 캐시(`data/i18n_cache.json`, 702건 동봉), `lang=ko` 는 번역 경로 미사용, 키가 없으면 원문 유지.
- 상세: [`docs/release_2026-09-08b_snomed_mold_chat_i18n.ko.md`](docs/release_2026-09-08b_snomed_mold_chat_i18n.ko.md) · [English](docs/release_2026-09-08b_snomed_mold_chat_i18n.en.md)

### 📚 알레르겐 환경관리 교과서 층 · 온톨로지 확장 (2026-10-09)
- **교과서 2종 반영**: 한글 알레르기 교과서 '환경관리 및 회피요법' 장(스캔)과 Middleton's Allergy 26·27·28·86장을 읽고, 집먼지진드기·고양이·개·바퀴·실내/실외 곰팡이·꽃가루·설치류 8개 알레르겐군의 생물학·운반 입자·노출 역치·관리 조치를 **단계(필수/도움/제한적/비권장)·근거 등급·출처**와 함께 `data/allergen_environment_control.json` 에 정리. 담배연기·실내 연소·대기오염·기후변화(악화 요인)와 1차 예방 코호트 요약 포함. 두 교과서가 갈리는 항목은 `disagreements_ko` 에 적고 환자 문장은 보수적으로 썼다.
- **기존 지식 교정**: 템플릿(`allergen_category_profiles.json` v1.1)과 손으로 쓴 지식베이스 14종(`allergen_knowledge_base.json` v1.1)의 노출 환경·회피 수칙·교차반응을 교과서 기준으로 고침 — 예: 진드기 번식 조건(25℃·RH 60~75%), 배설물 입자 10~35 µm → 공기청정기 효과 의문, 고양이 제거 후 3~6개월 잔류, 바퀴 미끼형 우선, 뇌우 천식.
- **온톨로지 그래프 확장**: RDF 그래프에 `AllergenGroup`·`ControlMeasure`(tier·evidenceGrade)·`ExposureThreshold`·`CrossReactivity`·`TextbookSource` 노드를 얹고 `associatedWithDisease` 로 기존 질환 Topic(천식·비염·아토피)에 연결. SPARQL 한 번으로 "진드기 → 1단계 조치 → 관련 질환" 조인이 된다. 검토 상태는 `textbook_curated` 로 Wikipedia 스냅샷(candidate)과 분리.
- **챗봇·리포트·API**: 관리·회피 질문("공기청정기가 도움이 되나요?")에 교과서 블록을 주입하되 '실제 주의' 항원군으로 범위를 좁히고 보고서 값 질문에는 붙이지 않음(프롬프트 규칙 25~28). 리포트 '우선 실천 회피 수칙' 아래 **근거 요약** 블록. `GET /api/knowledge/environment-control?group=mite&q=공기청정기` · `/overview` · `/api/ontology/topics` 의 `allergen_topics`.
- **검토·승인 워크플로**: 조치마다 `review.status`(candidate → approved/revised/rejected). `scripts/review_environment_control.py --import review.json` 으로 검토 HTML(`docs/review_2026-10-09_*.html`)의 체크·메모를 반영하고, 검토가 끝나면 `ENV_CONTROL_PATIENT_GATE=approved_only` 로 승인된 조치만 환자 화면(리포트·카드뉴스·챗봇)에 내보낸다. `rejected` 는 항상 빠진다.
- 모든 문장은 **알레르기 전문의 검토 전**. 상세: [`docs/allergen_environment_textbook_integration_2026-10-09.md`](docs/allergen_environment_textbook_integration_2026-10-09.md) · 테스트 `test_environment_control.py`(48건) · `test_environment_control_review.py`(9건)

### 🗄️ 저장·관리자·이메일·PDF · 보안 강화 (2026-10-07)
- **저장**: 결과를 SQLite에 저장한다(`services/store_service.py`). 연결 풀로 SQLite 3.51.1 교착을 피한다.
- **관리자**: `/admin` 대시보드와 관리자 API(`services/admin_api.py`). 지식 검수 API는 관리자 인증을 요구한다.
- **전달**: 한·영·중 결과를 함께 생성한다. 이메일 발송(`services/email_service.py`)과 한글 PDF(WeasyPrint, `services/report_pdf.py`)를 지원한다.
- **임상 로직**: 계절성 안내는 임상적으로 관련 있는 항원에만 붙인다. 면역치료·동물항원·약물치료·감작 예방 섹션을 리포트와 카드뉴스에 넣었다. 검사 대조는 항원 목록에서 분리했다. 약물 항원은 '진료 확인 필요'로만 판정한다.
- **근거 데이터**: 면역치료 항원(KAAACI 2023/2024), 동물항원 관리(출처 32건), 감작 예방, 약물·증상 안내. 모두 전문의 검토 전 후보다.
- **보안**: 리포트 HTML 이스케이프, CSP(Content Security Policy), 요청 수 제한. 이메일 한도는 원자적으로 예약한다. 번역 요청에서는 환자 식별값을 마스킹한다.
- **웹**: 단계별 장면, 수집형 도감 카드, 알러젠 자동완성, 결과 언어 전환, 이메일 패널, PDF 받기. 퀘스트·클래식 공용 로직은 `web/shared.js`에 둔다.

### 🔬 실제 결과지 OCR 평가 · LLM 토큰 예산 (2026-10-09 ~ 10)
실제 결과지 사진 29장(7개 기관 양식, 정답 1,419행)으로 운영 파이프라인을 채점했다. 환자 이름은 마스킹했다. 사진·정답·평가 산출물은 저장소에 넣지 않는다.

| 모델 | 재현율 | 정밀도 | 수치 | 양성 누락 | 장당 시간 |
|---|---|---|---|---|---|
| OpenAI gpt-5.4 | 99.9% | 91.5% | 99.2% | 1/98 | 74초 |
| 연구실 Ollama qwen3.8:27b | 98.9% | 95.0% | 99.3% | 26/98 | 363초 |

- gpt-5.4 정밀도를 낮춘 주원인은 두 번 읽기 병합에서 생긴 중복행(128행)이다.
- **결함 수정**: 병합 키 정규화로 중복행 제거, 성분 항원의 매퍼 우회, 가로 사진 자동 회전(Tesseract OSD). 회귀 테스트 `test_ocr_real_sheet_fixes.py`.
- **평가 스크립트**: `scripts/eval_real_ocr.py`(실행) · `scripts/eval_real_ocr_report.py`(채점·리포트).
- **모델·예산**: OpenAI 기본 모델은 gpt-6-luna다. 누적 1,000만 토큰까지는 gpt-5.6-luna를 먼저 쓴다(`services/llm_usage.py`). 키 여러 개를 호출마다 번갈아 쓴다. 예산을 넘으면 알림 메일을 1회 보낸다. 상세: [`docs/llm_token_budget_2026-10-09.md`](docs/llm_token_budget_2026-10-09.md)
- **연구실 서버**: Ollama 요청의 이미지 크기를 `OLLAMA_MAX_PAYLOAD_KB`(기본 900)로 제한한다. 이 제한으로 nginx 413 오류를 피한다.

### 🧾 ImmunoCAP 카탈로그 매핑 · HIS 연동 계획 (2026-10-13)
- ThermoFisher ImmunoCAP 2023 공식 카탈로그 PDF에서 569종을 파싱했다. 단일 402·성분 105·혼합 62종이다.
- 레지스트리 항원 159종을 ImmunoCAP 코드와 대조했다(자동 105·수동 54). 레지스트리에는 `immunocap_codes`만 추가했다.
- 레지스트리에 없는 단일 항원 262종에 SNOMED CT 후보를 1차 매핑했다. 신뢰도는 high 203·manual 29·medium 11·low 8·none 11이다. 이 262종의 레지스트리 반영은 파일럿 패널 확정 뒤 결정한다.
- 스크립트 `scripts/map_immunocap_catalog.py` · 보고서 [`docs/review_2026-10-13_ImmunoCAP_카탈로그_SNOMED_1차매핑.html`](docs/review_2026-10-13_ImmunoCAP_카탈로그_SNOMED_1차매핑.html)
- **HIS 연동 PRD 초안**: 검사 결과 자동 수신 → 알림톡 문진 → 리포트 자동 전달. 병원 내부망과 로컬 LLM을 전제로 한다. [`tasks/prd-his-integration-pipeline.md`](tasks/prd-his-integration-pipeline.md)

### 📑 연구 문서 (2026-10-29 ~ 11-04)
- IRB 검토용 초안 v0.2(문서 12종, 승인 전): [`docs/irb_2026-10-29/`](docs/irb_2026-10-29/)
- 사용자·연구자 매뉴얼(합성 샘플 결과지만 사용): [`docs/manuals/`](docs/manuals/)
- 환자 만족도 설문 v0.1: [`docs/patient_satisfaction_2026-10-29/`](docs/patient_satisfaction_2026-10-29/)
- 의료정보센터 협조 요청서: [`docs/medical_information_cooperation_2026-10-29/`](docs/medical_information_cooperation_2026-10-29/)
- AAAAI 2027 Late-Breaking Abstract 제출본: [`docs/aaaai2027_abstract_2026-11-02/`](docs/aaaai2027_abstract_2026-11-02/)
- 실제 결과지 사진, OCR 평가 산출물, 교재 원문은 `.gitignore`로 제외한다.

---

## 🧠 감별 로직 (sensitization vs true allergy)

알러젠마다 같은 질문을 반복하지 않고, **큰 그림 → 카테고리별 핵심 포인트** 순의 적응형 문진을 씁니다.

1. **증상 패턴** — 연중(통년성) / 계절성 / 둘 다 / 없음 을 먼저 묻습니다.
2. **악화 계절** — 계절성/둘 다이거나 꽃가루 양성이면 봄·여름·가을·겨울 중 선택.
3. **음식·구강 알레르기** — 구강알레르기증후군(OAS), 음식 섭취 후 전신 반응 여부.
4. **카테고리별 감별 포인트** (양성 알러젠에 따라 동적으로 생성)
   - **계절성 꽃가루** — 시즌 그룹(봄 나무 / 초여름 잔디 / 가을 잡초)당 1문항으로 악화 여부 확인
   - **실내 통년성(진드기·바퀴)** — 저녁·새벽·이른 아침 악화(실내 알러젠의 전형적 시그니처),
     집을 비우면 호전, 먼지·이불 정리 시 악화
   - **곰팡이** — 습한 곳·장마철·곰팡이 공간에서 악화
   - **동물** — 사육/접촉 여부, 접촉 증가 시 증상 악화
   - **음식** — 그 음식 섭취 시 증상 재현 여부

판정: 위 그룹 답변을 알러젠 특성과 대조하여
**clinically_relevant** / **sensitized_only** / **indeterminate** 로 분류합니다. 계절성 알러젠은
악화 계절과 한국 내 피크 시즌을 자동 대조하며, 구체적 시즌 질문이 코스한 계절 선택보다 우선합니다.

관련 코드: `services/questionnaire_service.py`(문진·판정), `services/relevance_service.py`(양성 판정·감작 강도),
지식베이스: `data/allergen_knowledge_base.json`

---

## 🚀 빠른 시작 (v2 웹앱 · 권장)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# OpenAI API 키 설정 (.env) — OCR 사용 시 필요, 없어도 데모/직접입력으로 체험 가능
cp config/env_sample.txt .env      # 그리고 OPENAI_API_KEY=sk-... 입력

uvicorn server:app --reload        # http://127.0.0.1:8000
```

세련된 반응형 웹앱(라이트/다크)이 열립니다. **API 키가 없어도** 상단의 ‘데모 데이터로 체험’ 또는
‘결과를 직접 입력’으로 전체 흐름(문진·감별·리포트·카드뉴스·FHIR)을 확인할 수 있습니다.

> 레거시 Streamlit UI 는 별도 설치가 필요합니다(웹 서비스 이미지에서 분리됨):
> `pip install -r requirements-legacy.txt && streamlit run app.py` (http://localhost:8501)

### OCR 정확도 측정 (합성 픽스처)

실제 결과지는 환자 정보를 담고 있어 모아 두기 어렵다. 한국어·영어·중국어 결과지 **7계열**을
조사해 **가짜 환자 정보로 이미지를 합성**하고, 이미지마다 정답을 붙여 OCR 정확도를 숫자로 잰다.

```bash
python3 scripts/generate_result_sheets.py --out tests/fixtures/ocr --count 21
python3 scripts/score_ocr.py --limit 14       # OPENAI_API_KEY 필요
```

전체 평균(gpt-4o): 재현율 95.1% · 정밀도 96.7% · 수치 80.6% · class 91.0% · 팽진 98.9%.

조사·방법·측정 결과: [`docs/ocr_fixtures_survey.md`](docs/ocr_fixtures_survey.md)

### 회귀 테스트 (API 불필요)

```bash
pip install -r requirements-dev.txt
python -m pytest -q            # 1,042 통과 · 3 건너뜀 (2026-11-09)
node --test web/*.test.js      # 64 통과
```

macOS에서 PDF 테스트가 WeasyPrint `OSError`로 실패하면, 아래 변수를 설정하고 다시 실행하십시오.

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
```

---

## 📁 프로젝트 구조 (핵심)

```
server.py                           # ✨ FastAPI 백엔드 (v2, 권장)
web/                                # ✨ 프론트엔드 (index.html · styles.css · app.js)
app.py                              # 레거시 Streamlit 5단계 플랫폼
data/allergen_knowledge_base.json   # 알러젠 backdata + 감별 rubric
models/schemas.py                   # 스크리닝/감별/리포트 데이터 모델
services/
  ├─ ocr_service.py                 # GPT Vision OCR (SPT/MAST/UniCAP)
  ├─ knowledge_service.py           # 지식베이스 조회 + Wikipedia 외부검색 보강
  ├─ screening_service.py           # 스크리닝 문진 라벨/요약
  ├─ relevance_service.py           # 임상적 의미 감별(감작 강도·양성 판정)
  ├─ questionnaire_service.py       # ✨ 적응형(그룹화) 문진 엔진 (핵심)
  ├─ report_service.py              # 환자 맞춤 리포트(결정론 + GPT 다듬기)
  ├─ cardnews_service.py            # 카드뉴스 HTML 생성
  └─ fhir_service.py                # HL7 FHIR 변환
test_relevance_engine.py            # 감별·문진 엔진 테스트
```

---

## ⚠️ 주의사항

- 본 리포트/카드뉴스는 **교육용 참고 자료**이며, 정확한 진단·치료는 담당 의료진과 상담해야 합니다.
- 환자 정보는 로컬에서만 처리됩니다. API 키(.env)는 공유하지 마세요.
- 최근 항히스타민제 복용은 SPT 위음성을 유발할 수 있어, 스크리닝에서 자동으로 경고합니다.

---

**Version:** 2.0 · 환자 리포트 중심 재설계
