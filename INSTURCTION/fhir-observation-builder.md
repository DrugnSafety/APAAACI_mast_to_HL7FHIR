# 🏥 FHIR Observation Bundle 생성 가이드

## 1. Bundle 구조

### 1.1 Bundle 메타데이터
```json
{
  "resourceType": "Bundle",
  "type": "collection",
  "identifier": {
    "system": "urn:ietf:rfc:3986",
    "value": "urn:uuid:bundle-id"
  },
  "timestamp": "2025-10-12T10:00:00Z",
  "entry": [...]
}
```

### 1.2 개별 Observation 구조

#### 필수 요소
```json
{
  "resourceType": "Observation",
  "id": "unique-observation-id",
  "status": "final",
  "code": {
    "coding": [{
      "system": "http://snomed.info/sct",
      "code": "snomed-code",
      "display": "allergen-name"
    }]
  },
  "subject": {
    "reference": "Patient/patient-id"
  },
  "effectiveDateTime": "2025-10-11T14:30:00Z",
  "valueQuantity": {
    "value": 4.5,
    "unit": "mm",
    "system": "http://unitsofmeasure.org",
    "code": "mm"
  },
  "interpretation": [{
    "coding": [{
      "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
      "code": "POS",
      "display": "Positive"
    }]
  }]
}
```

## 2. 데이터 변환 규칙

### 2.1 SPT → Observation
| SPT 필드 | FHIR 경로 | 변환 규칙 |
|---------|----------|----------|
| 알레르겐명 | code.coding[0].display | 직접 매핑 |
| 팽진 크기 | valueQuantity.value | 평균값 계산 |
| 양성/음성 | interpretation[0].coding[0].code | POS/NEG |

### 2.2 MAST → Observation
| MAST 필드 | FHIR 경로 | 변환 규칙 |
|----------|----------|----------|
| Class | interpretation[0].text | "Class X" 형식 |
| 정량값 | valueQuantity.value | 숫자값 직접 매핑 |
| 단위 | valueQuantity.unit | kU/L 표준화 |

## 3. 검증 규칙

### 3.1 필수 필드 검증
```javascript
const requiredFields = [
  'resourceType',
  'status',
  'code',
  'subject',
  'effectiveDateTime'
];

function validateObservation(obs) {
  return requiredFields.every(field => 
    field.split('.').reduce((obj, key) => obj?.[key], obs) !== undefined
  );
}
```

### 3.2 코드 시스템 검증
- SNOMED CT 코드는 9자리 숫자
- LOINC 코드는 "XXXXX-X" 형식
- 로컬 코드는 네임스페이스 포함

## 4. Bundle 조립 예시

```javascript
function createObservationBundle(testResults) {
  const bundle = {
    resourceType: "Bundle",
    type: "collection",
    timestamp: new Date().toISOString(),
    entry: []
  };
  
  testResults.forEach(result => {
    const observation = createObservation(result);
    bundle.entry.push({
      fullUrl: `urn:uuid:${observation.id}`,
      resource: observation
    });
  });
  
  return bundle;
}
```

## 5. 실제 예시: SPT 결과 Bundle

```json
{
  "resourceType": "Bundle",
  "type": "collection",
  "timestamp": "2025-10-11T14:30:00Z",
  "entry": [
    {
      "fullUrl": "urn:uuid:obs-001",
      "resource": {
        "resourceType": "Observation",
        "id": "obs-001",
        "status": "final",
        "code": {
          "coding": [{
            "system": "http://snomed.info/sct",
            "code": "419474003",
            "display": "Dermatophagoides farinae"
          }],
          "text": "집먼지진드기"
        },
        "subject": {
          "reference": "Patient/P001"
        },
        "effectiveDateTime": "2025-10-11T14:30:00Z",
        "valueQuantity": {
          "value": 4.5,
          "unit": "mm",
          "system": "http://unitsofmeasure.org",
          "code": "mm"
        },
        "interpretation": [{
          "coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": "POS",
            "display": "Positive"
          }]
        }]
      }
    }
  ]
}
```

## 6. 품질 보증

### 6.1 FHIR 검증
- FHIR 프로파일 준수
- 필수 요소 존재
- 데이터 타입 일치

### 6.2 임상 검증
- 양성 판정 기준 정확성
- 대조군 데이터 포함
- 검사일 유효성