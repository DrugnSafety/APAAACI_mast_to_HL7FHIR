
# 🧬 README_AllergyIntolerance_Mapping.md  
**HL7 FHIR AllergyIntolerance 리소스 매핑 가이드**

---

## 📘 1. 개요

본 문서는 알레르기 검사 결과(OCR → FHIR Observation)와 사용자 증상 입력을 바탕으로,  
표준 HL7 FHIR **AllergyIntolerance** 리소스를 자동으로 생성하기 위한 매핑 규칙을 정의한다.

이 문서는 다음의 세 단계를 전제로 한다:

1. **OCR 단계** — 검사 결과에서 알레르겐명과 결과 추출  
2. **FHIR Observation 단계** — 검사결과를 FHIR Observation Bundle로 변환  
3. **사용자 피드백 단계** — 실제 노출 후 증상 여부 입력  
4. **AllergyIntolerance 변환 단계** — 해당 정보를 FHIR AllergyIntolerance로 구조화

---

## 🧠 2. AllergyIntolerance 리소스 개요

FHIR 공식 문서: [https://hl7.org/fhir/allergyintolerance.html](https://hl7.org/fhir/allergyintolerance.html)

### 핵심 필드 구조

| 필드 | 설명 |
|------|------|
| `clinicalStatus` | 알레르기 상태 (active, inactive 등) |
| `verificationStatus` | 검증 상태 (confirmed, unconfirmed 등) |
| `type` | allergy / intolerance 구분 |
| `category` | Food / Medication / Environmental 등 |
| `criticality` | high / low / unable-to-assess 등 |
| `code` | 알레르겐 개념 코드 (SNOMED 등) |
| `reaction` | 반응(증상) 정보 |
| `patient` | 대상 환자 정보 |
| `recordedDate` | 기록 일자 |
| `participant` | 작성자 정보 (optional) |

---

## ⚙️ 3. AllergyIntolerance 매핑 규칙

### 🔸 (1) Verification Status

- **값:** `confirmed`  
- **고정 코드:**
```json
"verificationStatus": {
  "coding": [
    {
      "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
      "code": "confirmed",
      "display": "Confirmed"
    }
  ]
}
```

---

### 🔸 (2) Clinical Status

- **값:** `active`  
- **고정 코드:**
```json
"clinicalStatus": {
  "coding": [
    {
      "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
      "code": "active",
      "display": "Active"
    }
  ]
}
```

---

### 🔸 (3) Substance (알레르겐 항목)

- **입력값:** OCR 결과에서 매칭된 알레르겐 항목  
  - `reaction.substance.coding.display` → 알레르겐 명칭 (canonical_name)  
  - `reaction.substance.coding.code` → SNOMED 코드

예:
```json
"reaction": [
  {
    "substance": [
      {
        "coding": [
          {
            "code": "419474003",
            "display": "Dermatophagoides farinae protein"
          }
        ]
      }
    ]
  }
]
```

---

### 🔸 (4) Category

- **분류 규칙:**
  | 알레르겐 분류 | category 값 |
  |----------------|-------------|
  | 음식물 관련 알레르겐 | `"Food"` |
  | 약물 | `"Medication"` |
  | 곰팡이 / 진드기 / 동물털 / 꽃가루 / 벌 / 환경항원 | `"Environmental"` |

예:
```json
"category": ["Environmental"]
```

---

### 🔸 (5) Criticality

- **모든 항목:** `"high"`
```json
"criticality": ["high"]
```

---

### 🔸 (6) Type

- **모든 항목:** `"allergy"`
```json
"type": "allergy"
```

---

### 🔸 (7) Manifestation (증상 정보)

- **모든 항목 공통으로 고정 입력**
- 검사 양성(positive test)을 의미하는 SNOMED CT 코드: `165014009`
- ISO8601 UTC 시간으로 기록 시점 표시

```json
"manifestation": [
  {
    "coding": [
      {
        "code": "165014009",
        "display": "Allergy test positive",
        "date": "2025-10-11T12:46:36.531Z",
        "system": "http://snomed.info/sct"
      }
    ]
  }
]
```

---

### 🔸 (8) Patient & Recorded Date

- **사용자 입력 기반**
  - `patient.reference` → `"Patient/{사용자 ID}"`  
  - `recordedDate` → 검사일자 (예: `"2025-10-11"`)

예:
```json
"patient": { "reference": "Patient/P001" },
"recordedDate": "2025-10-11"
```

---

## 🧩 4. AllergyIntolerance 리소스 전체 예시

(예시 내용 생략 — 상단 대화 참조)

---

## 🧩 5. 생성 규칙 요약 테이블

| 필드 | 입력 방식 | 값 / 규칙 |
|------|------------|------------|
| `clinicalStatus` | 고정 | `"active"` |
| `verificationStatus` | 고정 | `"confirmed"` |
| `type` | 고정 | `"allergy"` |
| `category` | 알레르겐 분류 기반 | `"Food"`, `"Environmental"`, `"Medication"` |
| `criticality` | 고정 | `"high"` |
| `reaction.substance.coding.display` | OCR 매칭 결과 | 알레르겐명 (canonical_name) |
| `reaction.substance.coding.code` | SNOMED 코드 | 표준 알레르겐 코드 |
| `manifestation` | 고정 | `"165014009"`, `"Allergy test positive"` |
| `patient.reference` | 사용자 입력 | `"Patient/{id}"` |
| `recordedDate` | 검사일자 | `"YYYY-MM-DD"` |

---

## ✅ 6. 향후 확장 계획

| 항목 | 설명 |
|------|------|
| **증상별 Manifestation 추가** | “비염”, “피부발진” 등 구체 증상 → SNOMED 코드 매핑 |
| **반응 심각도 분류** | mild / moderate / severe 분류 로직 추가 |
| **FHIR Bundle 통합** | Observation + AllergyIntolerance 통합 저장 |
| **FHIR Server 연동** | HAPI FHIR REST API 전송 자동화 |
