# 🚀 알레르기 검사 처리 시스템 설치 및 실행 가이드

## 📋 시스템 요구사항

### 필수 소프트웨어
- Python 3.8 이상
- Tesseract OCR (이미지에서 텍스트 추출용)

### 운영체제별 Tesseract 설치

#### Windows
```bash
# 1. Tesseract 다운로드
# https://github.com/UB-Mannheim/tesseract/wiki 에서 설치파일 다운로드

# 2. 설치 후 시스템 PATH에 추가
# 일반적으로 C:\Program Files\Tesseract-OCR
```

#### macOS
```bash
brew install tesseract
brew install tesseract-lang  # 한국어 지원
```

#### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install tesseract-ocr
sudo apt install tesseract-ocr-kor  # 한국어 지원
```

## 🛠️ Python 환경 설정

### 1. 가상환경 생성 (권장)
```bash
python -m venv allergy_env

# Windows
allergy_env\Scripts\activate

# macOS/Linux
source allergy_env/bin/activate
```

### 2. 필수 패키지 설치
```bash
pip install -r requirements.txt
```

**requirements.txt 내용:**
```
pytesseract>=0.3.10
Pillow>=10.0.0
numpy>=1.24.0
requests>=2.31.0
google-cloud-vision>=3.4.0  # Google Vision API 사용시
azure-cognitiveservices-vision-computervision>=0.9.0  # Azure 사용시
```

## 📁 디렉토리 구조 설정

```
allergy_processing/
│
├── integrated_pipeline.py      # 메인 실행 파일
├── ocr_processor.py            # OCR 처리 모듈
├── requirements.txt            # 패키지 목록
│
├── config/                     # 설정 파일
│   ├── ocr_extraction_config.json
│   ├── fhir_observation_mapper.json
│   ├── chatbot_conversation_flow.json
│   ├── allergyintolerance_config.json
│   └── master_allergen_database.json
│
├── input_images/               # 입력 이미지 폴더
│   └── (검사 결과 이미지 파일들)
│
├── output/                     # 결과 출력 폴더
│   └── (처리된 JSON 파일들)
│
└── temp/                       # 임시 파일 폴더
    └── (중간 처리 파일들)
```

## 🎯 실행 방법

### 방법 1: 테스트 실행 (Mock 데이터)
```bash
# OCR 엔진 없이 테스트 데이터로 전체 파이프라인 실행
python integrated_pipeline.py test.jpg --engine mock --auto
```

### 방법 2: 단일 이미지 처리
```bash
# Tesseract OCR 사용, 수동 증상 입력
python integrated_pipeline.py input_images/allergy_test.jpg

# 자동 모드 (증상 자동 할당)
python integrated_pipeline.py input_images/allergy_test.jpg --auto
```

### 방법 3: 폴더 일괄 처리
```bash
# input_images 폴더의 모든 이미지 처리
python integrated_pipeline.py input_images/ --auto
```

### 방법 4: Google Vision API 사용
```bash
# 환경 변수 설정
export GOOGLE_APPLICATION_CREDENTIALS="path/to/credentials.json"

# 실행
python integrated_pipeline.py image.jpg --engine google_vision
```

## 💻 대화형 실행 예시

### 자동 모드 OFF (기본값)
```bash
$ python integrated_pipeline.py test_image.jpg

🏥 Allergy Test Processing Pipeline v1.0
========================================================

Step 1: Extracting text from image...
Step 2: Processing OCR text...
Step 3: Converting to FHIR Observation...
Step 4: Collecting symptom feedback...

========================================================
🤖 알레르기 증상 피드백 수집 챗봇
========================================================

검사에서 양성으로 나온 항목들입니다:
  1) Dermatophagoides farinae
  2) Cat dander
  3) Birch pollen
  4) Egg white
  5) Peanut

실제로 노출 시 증상이 있었던 항목의 번호를 입력해주세요.
(예: 1,3,5 또는 1 3 5)

증상 있음: 1,2,5

나머지 2개 항목에 대해:

Birch pollen:
  A) 노출되었지만 증상 없음
  B) 아직 노출 경험 없음
선택 (A/B): B

Egg white:
  A) 노출되었지만 증상 없음
  B) 아직 노출 경험 없음
선택 (A/B): A

Step 5: Generating AllergyIntolerance resources...

✅ Pipeline completed successfully!
Result saved to: output/test_image_pipeline_result.json
```

## 📊 출력 파일 설명

### 1. 중간 파일 (temp/ 폴더)
- `ocr_text.txt` - 추출된 원본 텍스트
- `ocr_result.json` - 구조화된 OCR 결과
- `observation_bundle.json` - FHIR Observation Bundle
- `symptom_feedback.json` - 증상 피드백 데이터
- `allergy_intolerance.json` - 최종 AllergyIntolerance Bundle

### 2. 최종 결과 (output/ 폴더)
- `{이미지명}_pipeline_result.json` - 전체 파이프라인 결과

**결과 파일 구조:**
```json
{
  "image_path": "input_images/test.jpg",
  "timestamp": "2025-10-12T15:30:00",
  "status": "success",
  "steps": {
    "ocr_text": "...",
    "ocr_result": {...},
    "observation_bundle": {...},
    "symptom_feedback": {...},
    "allergy_intolerance": {...}
  }
}
```

## 🔧 문제 해결

### 1. Tesseract를 찾을 수 없음
```python
# Windows에서 경로 직접 지정
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
```

### 2. 한국어 인식 안됨
```bash
# 한국어 언어팩 설치 확인
tesseract --list-langs

# kor이 없으면 설치 필요
# Ubuntu: sudo apt install tesseract-ocr-kor
# macOS: brew install tesseract-lang
```

### 3. 이미지 품질 문제
- 최소 300 DPI 권장
- 선명한 스캔 이미지 사용
- 기울어진 이미지는 자동 보정됨

## 🎨 커스터마이징

### OCR 엔진 변경
```python
# integrated_pipeline.py 수정
PipelineConfig.OCR_ENGINE = "google_vision"  # 또는 "azure"
```

### 자동 증상 할당 비율 조정
```python
# SymptomFeedbackCollector._auto_generate_feedback() 수정
symptomatic_count = int(count * 0.8)  # 80%로 변경
```

### 출력 형식 변경
```python
# JSON 대신 CSV로 저장
import csv
# ... CSV 저장 코드 추가
```

## 📝 API 모드 (추가 개발 필요)

Flask/FastAPI를 사용한 웹 API 구현 예시:
```python
from flask import Flask, request, jsonify
from integrated_pipeline import AllergyTestPipeline

app = Flask(__name__)
pipeline = AllergyTestPipeline()

@app.route('/process', methods=['POST'])
def process_image():
    file = request.files['image']
    result = pipeline.process_image(file)
    return jsonify(result)

if __name__ == '__main__':
    app.run(debug=True)
```

## 📞 지원 및 문의

문제가 발생하거나 추가 기능이 필요한 경우:
1. GitHub Issues에 등록
2. 로그 파일 첨부 (temp/logs/)
3. 사용 환경 정보 제공 (OS, Python 버전 등)