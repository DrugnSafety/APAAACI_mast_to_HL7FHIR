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

각 양성 알러젠에 대해 3가지를 확인합니다.

1. **노출 경험** (`exposed`) — 이 알러젠 환경/음식에 노출된 적이 있는가
2. **노출/시즌 시 증상** (`symptom_on_exposure`) — 노출될 때(또는 그 계절에) 증상이 생기거나 심해지는가
3. **재현성** (`reproducible`) — 반복적으로 나타나는가

판정 규칙:

- `symptom_on_exposure = 예` → **clinically_relevant** (실제 알레르기)
- `exposed = 예` & `symptom_on_exposure = 아니오` → **sensitized_only** (감작만, 과도한 회피 불필요)
- 그 외(노출 없음/정보 부족) → **indeterminate** (관찰 필요)

**계절 자동 대조:** 스크리닝에서 입력한 증상 악화 월과 알러젠의 한국 시즌(예: 봄 나무꽃가루 3~5월,
가을 잡초 8~10월)이 겹치면 증상악화 응답을 자동 제안합니다. 예) 봄철 악화 환자가 자작나무 강양성 →
"봄철 증상 악화" 자동 제안, 반대로 봄철에 증상이 없다면 감작만으로 안내.

관련 코드: `services/relevance_service.py`, 지식베이스: `data/allergen_knowledge_base.json`

---

## 🚀 빠른 시작

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# OpenAI API 키 설정 (.env)
echo "OPENAI_API_KEY=sk-..." > .env

streamlit run app.py
```

브라우저에서 `http://localhost:8501` 로 접속합니다. (API 키는 앱 사이드바에서도 입력 가능)

### 엔진 회귀 테스트 (API 불필요)

```bash
python test_relevance_engine.py
```

---

## 📁 프로젝트 구조 (핵심)

```
app.py                              # Streamlit 5단계 플랫폼
data/allergen_knowledge_base.json   # 알러젠 backdata + 감별 rubric
models/schemas.py                   # 스크리닝/감별/리포트 데이터 모델
services/
  ├─ ocr_service.py                 # GPT Vision OCR
  ├─ knowledge_service.py           # 지식베이스 조회 + Wikipedia 외부검색 보강
  ├─ screening_service.py           # 스크리닝 문진 라벨/요약
  ├─ relevance_service.py           # 임상적 의미 감별 엔진 (핵심)
  ├─ report_service.py              # 환자 맞춤 리포트(결정론 + GPT 다듬기)
  ├─ cardnews_service.py            # 카드뉴스 HTML 생성
  └─ fhir_service.py                # HL7 FHIR 변환
test_relevance_engine.py            # 감별 엔진 테스트
```

---

## ⚠️ 주의사항

- 본 리포트/카드뉴스는 **교육용 참고 자료**이며, 정확한 진단·치료는 담당 의료진과 상담해야 합니다.
- 환자 정보는 로컬에서만 처리됩니다. API 키(.env)는 공유하지 마세요.
- 최근 항히스타민제 복용은 SPT 위음성을 유발할 수 있어, 스크리닝에서 자동으로 경고합니다.

---

**Version:** 2.0 · 환자 리포트 중심 재설계
