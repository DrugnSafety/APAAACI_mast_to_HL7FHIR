# 📘 README — Allergy Data FHIR Conversion & Interaction Pipeline

1️⃣ 개요

이 프로젝트는 알레르기 검사 결과(SPT, MAST/UniCAP) 를 OCR로 인식하고,
이를 HL7 FHIR Observation 표준 형식으로 변환한 뒤,
사용자와의 대화 인터페이스를 통해 실제 임상적 의미(증상 여부) 를 수집하여
최종적으로 HL7 FHIR AllergyIntolerance 리소스로 구조화하는 것을 목표로 합니다.

⸻

2️⃣ 전체 프로세스 개요

┌────────────┐
│  Image OCR │  ← SPT / MAST 검사 결과지 (이미지)
└──────┬─────┘
       │ OCR Prompt (Vision)
       ▼
┌────────────────────┐
│ JSON Extraction    │ ← FHIR Observation 기반 필드 구조
└──────┬─────────────┘
       │ 정규화 매핑 (allergen_map_prompt_v2.json)
       ▼
┌────────────────────┐
│ FHIR Observation   │ ← spt_mast_to_fhir.py
└──────┬─────────────┘
       │ 양성(Positive) 결과 필터링
       ▼
┌────────────────────┐
│ 사용자 피드백 단계 │ ← OpenAI API 기반 인터페이스
│ ① 실제 증상 있었던 항목 선택  │
│ ② 노출됐지만 증상 없었던 항목 │
│ ③ 아직 노출되지 않은 항목     │
└──────┬─────────────┘
       │ 입력 기반 임상 해석
       ▼
┌────────────────────┐
│ FHIR AllergyIntolerance │
└────────────────────┘

3️⃣ 주요 구성 요소

| **구성 요소** | **역할** | **파일** |
|----------------|-----------|-----------|
| OCR Prompt | SPT/MAST 결과지에서 구조화 JSON 추출 | ocr_prompt.md |
| 표준 알레르겐 맵 | 알레르겐 이름 → SNOMED/FHIR 코드 매핑 | allergen_map_prompt_v2.json |
| FHIR 변환기 | OCR JSON → HL7 FHIR Observation Bundle | spt_mast_to_fhir.py |
| 변환 설명서 | 매핑 규칙·데이터 구조 설명 | README_allergen_map.md |
| 사용자 피드백 인터페이스 | 양성 항목에 대해 증상 경험 입력 받기 (OpenAI API 기반 UI) | — |
| FHIR AllergyIntolerance 매퍼 | 사용자 증상 정보 → FHIR AllergyIntolerance 리소스 생성 | fhir_allergy_builder.py *(추가 예정)* |


4️⃣ 단계별 상세 설명

🔹 Step 1. OCR 추출 (SPT / MAST / UniCAP)
	•	사용 프롬프트: ocr_prompt.md
	•	출력 포맷: JSON
	•	핵심 필드:
	•	test_type: "SPT" or "MAST"
	•	patient.name, patient.test_date
	•	results[].allergen_name, mean_mm, class, value, interpretation

예시:
{
  "test_type": "SPT",
  "patient": { "name": "홍길동", "test_date": "2024-12-26" },
  "results": [
    {"allergen_name": "Dermatophagoides farinae", "mean_mm": 3.5, "interpretation": "Positive"},
    {"allergen_name": "Histamine", "mean_mm": 4.0}
  ]
}


⸻

🔹 Step 2. 정규화 매핑 (allergen_map_prompt_v2.json)
	•	OCR 결과의 allergen_name을 표준 SNOMED 코드로 매핑
	•	다국어/약어/오타에 대응 (aliases, ocr_aliases 기반)
	•	매핑 결과:
	•	canonical_name, korean_name, snomed, category, subcategory

예:
{
  "Dermatophagoides farinae": {
    "canonical_name": "Dermatophagoides farinae protein",
    "korean_name": "미국집먼지진드기",
    "snomed": "419474003",
    "category": "Mite",
    "subcategory": "House dust mite"
  }
}


⸻

🔹 Step 3. FHIR Observation 변환 (spt_mast_to_fhir.py)
	•	mean_mm, value, interpretation 기반으로 FHIR Observation 생성
	•	SPT → component에 wheal size(mm) 기록
	•	MAST → valueQuantity (kU/L), interpretation (class)

예:
{
  "resourceType": "Observation",
  "code": { "text": "Skin prick reaction - Dermatophagoides farinae protein" },
  "valueQuantity": { "value": 3.5, "unit": "mm" },
  "interpretation": [{ "code": "POS" }]
}


⸻

🔹 Step 4. 사용자 인터랙션 (OpenAI API 기반)

목적
	•	FHIR Observation에서 Positive 항목만 필터링
	•	사용자에게 “이 항목에 실제로 증상이 있었는가?” 질문

프로세스

1️⃣ 1단계 질문
	•	“다음 알레르겐에 실제 노출 시 증상이 있었나요?”
	•	예: [진드기, 고양이, 돼지고기, 밀가루]
	•	사용자는 증상 있었던 항목 선택

2️⃣ 2단계 질문
	•	“이 외의 알레르겐 중, 실제 노출되었지만 증상이 없었던 항목이 있나요?”
	•	예: [개털, 소고기, 새우]
	•	사용자는 증상 없었던 항목 입력

3️⃣ 3단계 질문
	•	“아직까지 노출되지 않아 알 수 없는 항목이 있나요?”
	•	예: [러시안 시슬, 세이지]

이 결과를 JSON으로 저장:

{
  "patient_id": "P001",
  "exposure_feedback": {
    "symptomatic": ["Dermatophagoides farinae", "Cat dander"],
    "asymptomatic": ["Dog dander"],
    "unknown_exposure": ["Rye grass"]
  }
}


⸻

🔹 Step 5. FHIR AllergyIntolerance 생성

생성 규칙
| **사용자 입력** | **FHIR AllergyIntolerance.interpretation** | **clinicalStatus** |
|------------------|--------------------------------------------|--------------------|
| symptomatic | confirmed | active |
| asymptomatic | refuted | inactive |
| unknown_exposure | unconfirmed | unknown |

예:
{
  "resourceType": "AllergyIntolerance",
  "clinicalStatus": {"coding":[{"code":"active"}]},
  "verificationStatus": {"coding":[{"code":"confirmed"}]},
  "code": {
    "coding": [{"system":"http://snomed.info/sct","code":"419474003","display":"Dermatophagoides farinae protein"}],
    "text": "House dust mite allergy"
  },
  "patient": {"reference":"Patient/P001"},
  "reaction": [{
    "manifestation":[{"text":"Allergic rhinitis"}],
    "onset":"2024-12-26"
  }]
}

5️⃣ 향후 확장 계획
| **단계** | **목표** | **기술** |
|-----------|-----------|-----------|
| 🔹 LLM Fallback 매핑 | OCR 불확실 항목 자동 보정 | GPT-5 + fuzzy logic |
| 🔹 증상 기록 자동화 | 증상 서술 → SNOMED CT 증상 코드 매핑 | FHIR Condition integration |
| 🔹 FHIR 서버 연동 | Observation / AllergyIntolerance 전송 | HAPI FHIR REST API |
| 🔹 통계 대시보드 | 환자별 알레르기 패턴 시각화 | Streamlit / Grafana |