# 🔍 OCR 데이터 추출 가이드 v2.0

## 1. 개요
알레르기 검사 결과 이미지를 정확하게 인식하고 구조화된 JSON으로 변환하는 프로세스 가이드입니다.

## 2. 지원 검사 유형

### 2.1 Skin Prick Test (SPT)
**특징:**
- 팽진(wheal) 및 발적(flare) 크기 측정
- "3x4mm" 형태의 크기 표기
- 히스타민 대조군 포함

**양성 판정 기준:**
```
IF (평균 직경 ≥ 3.0mm) OR (평균 직경 ≥ 히스타민 대조군 × 0.5)
THEN 양성
```

### 2.2 MAST/UniCAP/ImmunoCAP
**특징:**
- Class (0-6) 또는 정량 수치 제공
- 단위: kU/L, IU/mL
- Total IgE 포함 가능

**양성 판정 기준:**
```
IF (Class ≥ 1) OR (값 ≥ 0.35 kU/L)
THEN 양성
```

## 3. 데이터 추출 프로세스

### Step 1: 이미지 전처리
1. **해상도 확인**: 최소 300 DPI 권장
2. **기울기 보정**: ±5도 이내 자동 보정
3. **노이즈 제거**: 가우시안 필터 적용

### Step 2: 텍스트 인식
1. **테이블 구조 감지**
2. **열(column) 헤더 식별**
3. **행(row) 단위 텍스트 추출**

### Step 3: 데이터 정규화
1. **알레르겐명 표준화**
   - 약어 → 전체 이름 (예: "D.f" → "Dermatophagoides farinae")
   - 한/영 병기 분리

2. **수치 데이터 파싱**
   - SPT: "4.5x3" → mean: 3.75
   - MAST: "Class 2 (0.89)" → class: 2, value: 0.89

### Step 4: 품질 검증
- 필수 필드 존재 여부
- 수치 데이터 유효성
- 알레르겐명 데이터베이스 대조

## 4. 출력 JSON 스키마

```json
{
  "test_metadata": {
    "test_type": "SPT|MAST",
    "test_date": "YYYY-MM-DD",
    "patient_id": "string",
    "extraction_timestamp": "ISO8601"
  },
  "quality_metrics": {
    "overall_confidence": 0.95,
    "extraction_warnings": []
  },
  "results": [
    {
      "allergen_id": "unique_id",
      "allergen_name": "canonical_name",
      "korean_name": "한글명",
      "category": "category",
      "subcategory": "subcategory",
      "test_value": {
        "raw": "original_text",
        "numeric": 3.75,
        "unit": "mm|kU/L",
        "class": "0-6|null"
      },
      "interpretation": "Positive|Negative|Equivocal",
      "confidence": 0.98
    }
  ]
}
```

## 5. 에러 처리

### 일반적인 오류와 해결방법
| 오류 유형 | 원인 | 해결방법 |
|----------|------|----------|
| 낮은 신뢰도 | 이미지 품질 불량 | 재촬영 요청 |
| 알레르겐 미인식 | 데이터베이스 미등록 | 수동 검토 플래그 |
| 수치 파싱 실패 | 비표준 형식 | 패턴 규칙 추가 |

## 6. 검증 체크리스트
- [ ] 모든 알레르겐 항목 추출 완료
- [ ] 대조군 데이터 정확히 식별
- [ ] 양성/음성 판정 기준 적용
- [ ] 신뢰도 임계값 충족 (>0.8)