# 알레르기 검사 자동 분석 시스템 설정 가이드

## 🚀 빠른 시작

### 1. 환경 설정

#### 1.1 가상환경 활성화
```bash
source .venv/bin/activate
```

#### 1.2 `.env` 파일 생성
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 다음 내용을 입력하세요:

```env
# OpenAI API Configuration
OPENAI_API_KEY=your_actual_openai_api_key_here

# Application Settings
APP_ENV=development
DEBUG=True
```

⚠️ **중요**: `your_actual_openai_api_key_here`를 실제 OpenAI API 키로 교체하세요!

OpenAI API 키는 다음에서 발급받을 수 있습니다: https://platform.openai.com/api-keys

### 2. 애플리케이션 실행

#### 2.1 Streamlit 앱 실행
```bash
streamlit run app.py
```

브라우저가 자동으로 열리며, http://localhost:8501 에서 애플리케이션에 접속할 수 있습니다.

### 3. 테스트

#### 3.1 OCR 기능 테스트
```bash
python test_ocr.py
```

#### 3.2 샘플 이미지 테스트
`input_images/` 폴더의 이미지를 사용하여 테스트할 수 있습니다:
- `mast_1.png` - MAST 검사 결과
- `skin_prick_test_1.jpg` - SPT 검사 결과

## 📋 사용 방법

### Step 1: 이미지 업로드
- 알레르기 검사 결과 이미지를 업로드합니다
- 지원 형식: PNG, JPG, JPEG

### Step 2: OCR 결과 확인
- 자동으로 추출된 검사 결과를 확인합니다
- 필요시 수정 가능합니다

### Step 3: 환자 정보 입력
- 환자 이름, 나이, 성별을 입력합니다

### Step 4: 증상 피드백
- 챗봇과 대화하며 알레르기 증상 경험을 입력합니다
- 각 알레르겐에 대한 실제 증상 유무를 확인합니다

### Step 5: FHIR 리소스 생성
- HL7 FHIR Observation 및 AllergyIntolerance 리소스가 자동 생성됩니다
- JSON 형식으로 다운로드 가능합니다

### Step 6: 리포트 생성
- 맞춤형 알레르기 관리 리포트가 한국어로 생성됩니다
- PDF 형식으로 다운로드 가능합니다

## 🔧 문제 해결

### OpenAI API 키 오류
```
❌ OpenAI API 키를 설정해주세요!
```
→ `.env` 파일에 유효한 OpenAI API 키를 입력하세요

### 패키지 설치 오류
```bash
pip install -r requirements.txt
```

### 포트 충돌
기본 포트(8501)가 사용 중인 경우:
```bash
streamlit run app.py --server.port 8502
```

## 📁 생성된 파일 위치

모든 결과물은 `output/` 폴더에 저장됩니다:
- `{환자이름}_{검사종류}_{날짜}_ocr.json` - OCR 결과
- `{환자이름}_{검사종류}_{날짜}_observation.json` - FHIR Observation
- `{환자이름}_{검사종류}_{날짜}_allergy.json` - FHIR AllergyIntolerance  
- `{환자이름}_{검사종류}_{날짜}_report.pdf` - 알레르기 관리 리포트

## 💡 추가 정보

- **지원 검사 종류**: SPT (Skin Prick Test), MAST, UniCAP
- **지원 언어**: 한국어, 영어
- **FHIR 표준**: HL7 FHIR R4
- **알레르겐 매핑**: SNOMED CT 코드 자동 매핑

## 📞 문의

문제가 발생하거나 추가 지원이 필요한 경우, 프로젝트 관리자에게 문의하세요.
