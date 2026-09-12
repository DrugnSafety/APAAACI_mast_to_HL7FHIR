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

실제 결과지는 환자 정보를 담고 있어 모아 두기 어렵다. 실제 양식 5계열을 관찰해 **가짜 환자
정보로 결과지 이미지를 합성**하고, 이미지마다 정답을 붙여 OCR 정확도를 숫자로 잰다.

```bash
python3 scripts/generate_result_sheets.py --out tests/fixtures/ocr --count 15
python3 scripts/score_ocr.py --limit 5        # OPENAI_API_KEY 필요
```

조사·방법·측정 결과: [`docs/ocr_fixtures_survey.md`](docs/ocr_fixtures_survey.md)

### 엔진 회귀 테스트 (API 불필요)

```bash
python test_relevance_engine.py
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
