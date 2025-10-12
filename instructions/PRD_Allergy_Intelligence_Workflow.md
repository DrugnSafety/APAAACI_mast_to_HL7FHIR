
# 🧩 PRD — Allergy Intelligence Workflow

## 📘 개요
이 문서는 **알레르기 검사 결과 자동 분석 및 FHIR 변환 파이프라인**의 제품 요구사항(PRD, Product Requirement Document)을 정의한다.  
전체 워크플로우는 OCR 기반 데이터 추출부터 HL7 FHIR 변환, 사용자 피드백 수집, 최종 임상 리포트 생성을 포함한다.

---

## 🧠 1. 목적
- 다양한 형식(MAST, SPT, UniCAP)의 알레르기 검사 결과를 자동으로 인식(OCR)하고,
- 알러젠을 SNOMED 코드 기반으로 정규화(mapping),
- HL7 FHIR Observation → AllergyIntolerance 리소스로 자동 변환,
- 사용자 피드백(실제 증상 경험)을 수집하여 임상적 의미를 강화,
- AI를 활용해 맞춤형 알레르기 관리 플랜을 생성한다.

---

## ⚙️ 2. 주요 기능 구성

### 2.1 OCR Layer
| 항목 | 설명 |
|------|------|
| 입력 | 검사 결과 이미지 (SPT, MAST 등) |
| 처리 | OpenAI OCR 모델 또는 Vision API |
| 출력 | 알러젠명, 측정값, 단위, 판정(양성/음성) |
| 매핑 | `allergen_map_prompt_v2.json`을 활용하여 표준화된 항목으로 변환 |

### 2.2 FHIR Transformation Layer
| 리소스 | 설명 |
|---------|------|
| **Observation** | 검사 결과 원본값 저장 (IgE, Class, wheal size 등) |
| **AllergyIntolerance** | 양성 알러젠 중 실제 증상 보고된 항목만 변환 |
| **Structure** | SNOMED CT 코드, 환자 정보, 검사일 포함 |

### 2.3 Chatbot Feedback Layer
| 단계 | 기능 |
|------|------|
| ① | FHIR Observation 기반으로 **양성 알러젠 리스트 출력** |
| ② | 사용자에게 “증상 있었던 알러젠” 선택 요청 |
| ③ | 나머지 항목에 대해 “노출 있었으나 증상 없음 / 미노출” 구분 |
| ④ | JSON으로 정리하여 AllergyIntolerance 빌더에 전달 |

### 2.4 FHIR AllergyIntolerance Builder
- 입력: 사용자의 증상 분류 JSON
- 출력: AllergyIntolerance 리소스 (verificationStatus=Confirmed, clinicalStatus=Active)
- 사용 스크립트: `allergyintolerance_builder.py`

### 2.5 Personalized Management Report Layer
- 입력: AllergyIntolerance Bundle (JSON)
- 출력: 맞춤형 한국어 관리 리포트 (Markdown 또는 PDF)
- 생성 스크립트: `allergy_management_reporter.py`

---

## 🧩 3. 데이터 흐름 (Workflow Diagram)

```
[OCR Extraction]
    ↓
[Allergen Mapping (JSON)]
    ↓
[FHIR Observation 생성]
    ↓
[Chatbot Feedback]
    ↓
[FHIR AllergyIntolerance 변환]
    ↓
[AI Report Generator → Personalized Plan]
```

---

## 🔍 4. 예외 처리
| 예외 상황 | 처리 방식 |
|------------|-----------|
| OCR 결과 불명확 | confidence score < 0.8 시 사용자 확인 요청 |
| SNOMED 매핑 실패 | "unknown_allergen" 태그 부여 |
| 환자 정보 누락 | placeholder ID 생성 (“Patient/anonymous”) |
| FHIR 유효성 오류 | 오류 로그 생성 후 해당 리소스 제외 |

---

## 💡 5. 기술 스택
| 구성요소 | 기술 |
|----------|------|
| OCR | OpenAI Vision API, Tesseract |
| 데이터 파이프라인 | Python, Pandas |
| 표준화 | SNOMED CT, LOINC |
| FHIR 모델 | HL7 FHIR R4, fhir.resources 라이브러리 |
| 대화형 피드백 | OpenAI GPT-5 API |
| 보고서 생성 | Markdown → PDF (ReportLab) |

---

## 📈 6. 향후 확장 계획
- 병원 EMR 연동 (FHIR API POST /Observation, /AllergyIntolerance)
- 사용자 증상 일지 자동 반영
- 약물 알레르기/음식 알레르기 구분 고도화
- 다국어 리포트 자동 번역 지원

---

## 🧾 7. 관련 파일
| 파일명 | 설명 |
|---------|------|
| `allergen_map_prompt_v2.json` | 알러젠 명칭 정규화 매핑 테이블 |
| `allergyintolerance_builder.py` | FHIR AllergyIntolerance 생성기 |
| `chatbot_prompt_messages.json` | 사용자 증상 피드백용 챗봇 프롬프트 |
| `allergy_management_reporter.py` | 맞춤형 관리 리포트 자동 생성기 |
| `README_AllergyData_FHIR_Pipeline.md` | 전체 데이터 파이프라인 설명 문서 |

---

## ✅ 8. 요약
이 워크플로우는 단순한 알레르기 검사 결과를 **임상적 의미를 갖는 데이터(FHIR 표준)** 로 변환하고,  
AI 대화를 통해 **환자 중심의 맞춤형 관리 플랜**을 자동 생성하는 **Allergy Intelligence System**의 핵심 엔진을 정의한다.
