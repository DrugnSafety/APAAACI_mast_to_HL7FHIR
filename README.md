# 🏥 알레르기 검사 자동 분석 및 FHIR 변환 시스템

AI 기반 알레르기 검사 결과 자동 분석, HL7 FHIR 표준 변환, 맞춤형 관리 리포트 생성 시스템

## 📋 주요 기능

- **🔍 OCR 자동 추출**: OpenAI GPT Vision API를 사용한 검사 결과 자동 인식
- **🏥 FHIR 표준 변환**: HL7 FHIR R4 Observation 및 AllergyIntolerance 리소스 생성
- **💬 증상 피드백 챗봇**: GPT 기반 대화형 증상 수집
- **📄 맞춤형 리포트**: 한국어 알레르기 관리 계획 자동 생성
- **🖥️ 직관적 UI**: Streamlit 기반 웹 인터페이스

## 🚀 빠른 시작

### 1. 환경 설정

```bash
# 프로젝트 클론
cd /Users/mingyukang/python/APAAACI_mast_to_HL7FHIR

# 가상환경 생성 및 활성화
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# 또는
.venv\Scripts\activate  # Windows

# 패키지 설치
pip install -r requirements.txt
```

### 2. OpenAI API 키 설정

프로젝트 루트에 `.env` 파일을 생성하고 다음 내용을 추가:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

> 💡 API 키는 [OpenAI Platform](https://platform.openai.com)에서 발급받을 수 있습니다.

### 3. 애플리케이션 실행

```bash
streamlit run app.py
```

브라우저가 자동으로 열리며 `http://localhost:8501`에서 실행됩니다.

## 📁 프로젝트 구조

```
APAAACI_mast_to_HL7FHIR/
│
├── app.py                    # Streamlit 메인 애플리케이션
├── requirements.txt          # Python 패키지 의존성
├── .env                      # 환경 변수 (생성 필요)
│
├── config/
│   ├── settings.py          # 애플리케이션 설정
│   └── env_sample.txt       # .env 파일 예제
│
├── models/
│   └── schemas.py           # Pydantic 데이터 모델
│
├── services/
│   ├── ocr_service.py       # OCR 처리 서비스
│   ├── fhir_service.py      # FHIR 리소스 생성
│   ├── chatbot_service.py   # 증상 피드백 챗봇
│   └── report_service.py    # 리포트 생성 서비스
│
├── utils/
│   └── allergen_mapper.py   # 알레르겐 매핑 유틸리티
│
├── instructions/            # 프롬프트 및 템플릿
│   ├── OCR_prompt.md
│   ├── chatbot_prompt_messages.json
│   ├── allergen_map_prompt_v2.json
│   └── ...
│
├── input_images/           # 샘플 이미지
├── output/                 # 생성된 결과물
└── tasks/                  # PRD 및 작업 목록
```

## 🔄 사용 방법

### Step 1: 이미지 업로드
- SPT(피부단자검사) 또는 MAST 검사 결과지 이미지 업로드
- 지원 형식: JPG, PNG

### Step 2: OCR 결과 확인
- 자동으로 추출된 데이터 확인
- 필요시 수동 편집 가능

### Step 3: 환자 정보 입력
- 이름, 나이, 성별 입력
- 검사 날짜 확인

### Step 4: 증상 피드백
- AI 챗봇과 대화하며 증상 경험 입력
- 양성 알레르겐 중 실제 증상 분류

### Step 5: FHIR Observation 생성
- 검사 결과를 FHIR 표준으로 변환
- JSON 파일 다운로드 가능

### Step 6: FHIR AllergyIntolerance 생성
- 증상이 있는 알레르겐만 선별
- 임상적 의미를 갖는 FHIR 리소스 생성

### Step 7: 리포트 생성
- 맞춤형 한국어 관리 계획
- Markdown 및 PDF 형식 지원

## 📊 샘플 데이터

`input_images/` 폴더에 테스트용 샘플 이미지가 포함되어 있습니다:
- `mast_1.png`, `MAST_2.png` - MAST 검사 결과
- `skin_prick_test_1.jpg`, `skin_prick_test_2.jpg` - SPT 검사 결과

## 🔧 고급 설정

`config/settings.py`에서 다음 설정을 조정할 수 있습니다:

- OCR 신뢰도 임계값
- API 타임아웃
- 리포트 언어 설정
- PDF 생성 옵션

## ⚠️ 주의사항

1. **API 키 보안**: `.env` 파일을 공유하지 마세요
2. **개인정보**: 환자 정보는 로컬에만 저장됩니다
3. **의료 조언**: AI 생성 리포트는 참고용이며, 의료진 상담이 필요합니다

## 🐛 문제 해결

### "OpenAI API 키가 설정되지 않았습니다" 오류
- `.env` 파일이 프로젝트 루트에 있는지 확인
- API 키가 올바른지 확인

### OCR 추출 실패
- 이미지가 선명하고 전체 표가 보이는지 확인
- 이미지 크기가 10MB 이하인지 확인

### Streamlit 실행 오류
- 가상환경이 활성화되어 있는지 확인
- 모든 패키지가 설치되어 있는지 확인: `pip install -r requirements.txt`

## 📝 라이선스

이 프로젝트는 연구 및 교육 목적으로 제작되었습니다.

## 👥 기여

문의사항이나 개선 제안은 이슈를 생성해주세요.

## 📞 지원

- 기술 지원: [GitHub Issues](https://github.com/your-repo/issues)
- 이메일: your-email@example.com

---

**Version:** 1.0.0  
**Last Updated:** 2025-10-12  
**Developed by:** AI Assistant & User

---

## 🎯 다음 단계

시스템이 정상적으로 실행되면:

1. **실제 검사 결과로 테스트**: 본인의 알레르기 검사 결과로 테스트
2. **리포트 검토**: 생성된 리포트를 의료진과 공유
3. **피드백 수집**: 시스템 개선을 위한 의견 제공

감사합니다! 🙏
