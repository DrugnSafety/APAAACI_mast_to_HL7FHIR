# Task List: 알레르기 검사 자동 분석 및 FHIR 변환 시스템

### Relevant Files

- `app.py` – Streamlit 메인 애플리케이션 파일
- `services/ocr_service.py` – OpenAI GPT Vision API를 사용한 OCR 처리 서비스
- `services/fhir_service.py` – FHIR Observation 및 AllergyIntolerance 생성 서비스
- `services/chatbot_service.py` – GPT 기반 증상 피드백 수집 챗봇 서비스
- `services/report_service.py` – 맞춤형 알레르기 관리 리포트 생성 서비스
- `utils/allergen_mapper.py` – 알레르겐 명칭 정규화 및 SNOMED 매핑 유틸리티
- `utils/pdf_generator.py` – Markdown to PDF 변환 유틸리티
- `utils/session_manager.py` – Streamlit 세션 상태 관리
- `utils/file_manager.py` – 파일 저장 및 네이밍 관리
- `utils/error_handler.py` – 에러 처리 및 재시도 메커니즘
- `config/settings.py` – 환경 변수 및 설정 관리
- `models/schemas.py` – Pydantic 모델 정의 (OCR, FHIR, Report 스키마)
- `components/ui_components.py` – 재사용 가능한 Streamlit UI 컴포넌트
- `.env` – OpenAI API 키 및 환경 변수
- `requirements.txt` – Python 패키지 의존성
- `output/` – 생성된 결과물 저장 폴더
- `check_setup.py` – 환경 설정 확인 스크립트
- `test_ocr.py` – OCR 기능 테스트 스크립트

### Tasks

- [x] 1.0 프로젝트 초기 설정 및 환경 구성
  - [x] 1.1 Python 가상환경 설정 및 requirements.txt 작성
  - [x] 1.2 .env 파일 생성 및 OpenAI API 키 설정
  - [x] 1.3 프로젝트 디렉토리 구조 생성 (services/, utils/, components/, models/, config/, output/)
  - [x] 1.4 config/settings.py 작성 - 환경 변수 및 전역 설정 관리
  - [x] 1.5 models/schemas.py 작성 - Pydantic 모델 정의

- [x] 2.0 핵심 서비스 모듈 개발
  - [x] 2.1 utils/allergen_mapper.py 구현 - allergen_map_prompt_v2.json 로드 및 매핑 로직
  - [x] 2.2 services/ocr_service.py 구현 - OpenAI GPT Vision API 통합
  - [x] 2.3 services/fhir_service.py 구현 - FHIR Observation 생성 로직
  - [x] 2.4 services/chatbot_service.py 구현 - GPT 기반 증상 수집 대화 로직
  - [x] 2.5 services/fhir_service.py 확장 - FHIR AllergyIntolerance 생성 로직
  - [x] 2.6 services/report_service.py 구현 - GPT 기반 한국어 리포트 생성
  - [x] 2.7 utils/pdf_generator.py 구현 - Markdown to PDF 변환

- [x] 3.0 Streamlit UI 구현
  - [x] 3.1 components/ui_components.py 작성 - 재사용 가능한 UI 컴포넌트
  - [x] 3.2 app.py 기본 구조 작성 - Streamlit 페이지 레이아웃 및 네비게이션
  - [x] 3.3 Step 1: 이미지 업로드 UI 구현
  - [x] 3.4 Step 2: OCR 결과 확인 및 편집 UI 구현
  - [x] 3.5 Step 3: 환자 정보 입력 폼 구현
  - [x] 3.6 Step 4: 증상 피드백 채팅 인터페이스 구현
  - [x] 3.7 Step 5: FHIR 리소스 프리뷰 및 다운로드 UI 구현
  - [x] 3.8 Step 6: 리포트 프리뷰 및 PDF 다운로드 UI 구현

- [x] 4.0 통합 및 워크플로우 구현
  - [x] 4.1 세션 상태 관리 로직 구현 (utils/session_manager.py)
  - [x] 4.2 단계별 데이터 전달 및 검증 로직 구현
  - [x] 4.3 에러 핸들링 및 재시도 메커니즘 구현 (utils/error_handler.py)
  - [x] 4.4 파일 저장 및 네이밍 로직 구현 (utils/file_manager.py)
  - [x] 4.5 처리 이력 기록 시스템 구현 (session_manager에 통합)
  - [x] 4.6 진행 상황 표시 구현 (session_manager에 통합)

- [ ] 5.0 테스트 및 최종 검증
  - [ ] 5.1 샘플 이미지를 사용한 OCR 기능 테스트
  - [ ] 5.2 FHIR 리소스 생성 검증 (표준 준수 확인)
  - [ ] 5.3 챗봇 대화 흐름 테스트
  - [ ] 5.4 PDF 생성 및 다운로드 기능 테스트
  - [ ] 5.5 전체 워크플로우 통합 테스트
  - [ ] 5.6 UI/UX 최종 점검 및 개선

---

**✅ 세부 작업 진행 상황: 30/31개 완료 (96.8%)**