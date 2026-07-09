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
7. **🏥 HL7 FHIR 변환** — Observation(전체) + AllergyIntolerance(임상적으로 의미 있는 항목만 confirmed)

---

## 🔄 사용 흐름 (5단계)

| 단계 | 화면 | 내용 |
|------|------|------|
| 1 | 업로드 & OCR | 검사지 이미지 업로드 → OCR → 결과 점검/수정/확정 |
| 2 | 환자정보 & 스크리닝 | 기본정보 + 알레르기 질환력·약제·증상 패턴·침범 장기 |
| 3 | 양성 알러젠 감별 | 알러젠별 backdata 확인 + 노출/시즌/재현성 질문 → 임상적 의미 판정 |
| 4 | 리포트 & 카드뉴스 | 맞춤 리포트(MD/PDF) + 카드뉴스(HTML) 생성/다운로드 |
| 5 | FHIR | Observation / AllergyIntolerance 번들 생성/다운로드 |

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

> 레거시 Streamlit UI: `streamlit run app.py` (http://localhost:8501)

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
