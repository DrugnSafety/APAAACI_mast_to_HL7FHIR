"""
Application Settings and Configuration
환경 변수 및 전역 설정 관리
"""

import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()

# 프로젝트 루트 경로
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """애플리케이션 전역 설정"""
    
    # OpenAI API 설정
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o", alias="OPENAI_MODEL")
    # OCR 모델: 합성 결과지 21장 비교(2026-09-22) — gpt-4o 양성 누락 56/200 → gpt-5.x 계열 1~5/200.
    # 2026-09-25 사용자 결정으로 gpt-5.4(옛 프롬프트 비교 시 양성 누락 1/200, 수치 100%, class 85.5%).
    # 그 전에는 gpt-5.6-luna(09-24), gpt-5.5(09-22). 환경변수 OPENAI_VISION_MODEL 로 바꿀 수 있다.
    openai_vision_model: str = Field(default="gpt-5.4", alias="OPENAI_VISION_MODEL")
    # OCR 고도화(2026-09-22): 전처리(조명 평탄화·확대)와 두 번 읽기(전체 + 위·아래 절반 확대본)
    # LLM 백엔드: openai(클라우드) 또는 ollama(연구실 DGX Spark). 화면에서 요청마다 바꿀 수도 있다.
    llm_backend: str = Field(default="openai", alias="LLM_BACKEND")
    ollama_base_url: str = Field(default="", alias="OLLAMA_BASE_URL")
    ollama_api_key: str = Field(default="", alias="OLLAMA_API_KEY")
    ollama_vision_model: str = Field(default="qwen3.8:27b", alias="OLLAMA_VISION_MODEL")
    ollama_chat_model: str = Field(default="gpt-oss:120b", alias="OLLAMA_CHAT_MODEL")
    ollama_timeout: int = Field(default=600, alias="OLLAMA_TIMEOUT")
    # 생각(thinking) 모델의 추론 단계. OCR 은 끈다 — qwen3.8:27b 로 69행 보고서가 19분 → 수십 초.
    # (Ollama OpenAI 호환 API 에서는 reasoning_effort="none" 만 먹는다. extra_body think=false 는 무시됨)
    ollama_ocr_reasoning: str = Field(default="none", alias="OLLAMA_OCR_REASONING")
    ollama_chat_reasoning: str = Field(default="low", alias="OLLAMA_CHAT_REASONING")
    ocr_preprocess: bool = Field(default=True, alias="OCR_PREPROCESS")
    ocr_double_read: bool = Field(default=True, alias="OCR_DOUBLE_READ")
    ocr_reasoning_effort: str = Field(default="low", alias="OCR_REASONING_EFFORT")
    # 전용 OCR 텍스트 층(현재 Tesseract) — 행 순서 확인용 참고. 측정 결과에 따라 기본 끔
    ocr_text_layer: bool = Field(default=False, alias="OCR_TEXT_LAYER")
    # 리포트 생성용 고급 모델 - 자세한 분석과 추론을 위한 설정
    openai_report_model: str = Field(default="gpt-4o", alias="OPENAI_REPORT_MODEL")  # 리포트 생성용
    # 결과 상담 챗봇용. 2026-09 후보 7종 벤치마크(docs/llm_model_and_cost.md)에서 응급 인지·용량 미언급·
    # 언어 혼입 0건을 모두 통과한 모델 중 답변 품질이 가장 좋았다. 비용을 더 줄이려면 gpt-4o-mini(안전성은 통과, 말투·개인화는 약함).
    # 2026-09-25 사용자 결정으로 OCR 과 같은 gpt-5.4 로 통일(그 전 gpt-5.6-luna).
    openai_chat_model: str = Field(default="gpt-5.4", alias="OPENAI_CHAT_MODEL")
    # gpt-5 계열(추론 모델)에만 쓰인다: none/minimal/low/medium/high. 상담 답변은 깊은 추론보다
    # 지연·비용이 중요해 낮게 둔다. 모델이 지원하지 않는 값이면 빼고 다시 호출한다.
    openai_chat_reasoning_effort: str = Field(default="low", alias="OPENAI_CHAT_REASONING_EFFORT")
    report_temperature: float = Field(default=0.8, alias="REPORT_TEMPERATURE")  # 창의성 설정 (0.7~0.9)
    report_max_tokens: int = Field(default=9600, alias="REPORT_MAX_TOKENS")  # 더 긴 리포트를 위한 토큰
    # GPT-5가 출시되면 아래 주석을 해제하고 사용
    # openai_model: str = Field(default="gpt-5", alias="OPENAI_MODEL")  
    # openai_vision_model: str = Field(default="gpt-image-1", alias="OPENAI_VISION_MODEL")
    
    # 애플리케이션 설정
    app_env: str = Field(default="development", alias="APP_ENV")
    debug: bool = Field(default=True, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    
    # 파일 및 디렉토리 설정
    output_dir: Path = Field(default=BASE_DIR / "output", alias="OUTPUT_DIR")
    max_file_size_mb: int = Field(default=10, alias="MAX_FILE_SIZE_MB")
    allowed_image_extensions: list = Field(
        default=[".jpg", ".jpeg", ".png", ".pdf"],
        alias="ALLOWED_IMAGE_EXTENSIONS"
    )
    
    # API 설정
    api_timeout: int = Field(default=60, alias="API_TIMEOUT")
    max_retries: int = Field(default=3, alias="MAX_RETRIES")
    rate_limit_delay: float = Field(default=1.0, alias="RATE_LIMIT_DELAY")
    
    # OCR 설정
    ocr_confidence_threshold: float = Field(default=0.7, alias="OCR_CONFIDENCE_THRESHOLD")
    ocr_detail_level: str = Field(default="high", alias="OCR_DETAIL_LEVEL")  # "low", "high", "auto"
    
    # FHIR 설정
    fhir_version: str = Field(default="R4", alias="FHIR_VERSION")
    
    # PDF 설정
    pdf_font_family: str = Field(default="NanumGothic", alias="PDF_FONT_FAMILY")
    pdf_page_size: str = Field(default="A4", alias="PDF_PAGE_SIZE")
    
    # 챗봇 설정
    chatbot_max_history: int = Field(default=10, alias="CHATBOT_MAX_HISTORY")
    chatbot_temperature: float = Field(default=0.7, alias="CHATBOT_TEMPERATURE")
    
    # 리포트 설정
    report_language: str = Field(default="ko", alias="REPORT_LANGUAGE")
    report_include_english: bool = Field(default=True, alias="REPORT_INCLUDE_ENGLISH")

    # ---- 저장소(SQLite) — 건강정보가 들어가므로 파일은 git 에 올리지 않는다(data/store/ 는 .gitignore) ----
    storage_enabled: bool = Field(default=True, alias="STORAGE_ENABLED")
    # 본 DB: 사용자·세션(입력 결과+문진)·상담 대화·메일 발송 기록
    app_db_path: Path = Field(default=BASE_DIR / "data" / "store" / "app.sqlite3", alias="APP_DB_PATH")
    # 별도 DB: 세션별 ko/en/zh 산출물(리포트·카드뉴스)
    i18n_db_path: Path = Field(default=BASE_DIR / "data" / "store" / "localized.sqlite3", alias="I18N_DB_PATH")
    # 요청 언어 외 나머지 두 언어를 백그라운드로 만들지(번역 LLM 비용이 든다)
    i18n_background: bool = Field(default=True, alias="I18N_BACKGROUND")

    # 템플릿 항원 지식 파일. /review 의 승인·수정·삭제가 이 파일을 고쳐 쓴다 — 시험 서버는 임시 복사본을 가리키게 한다.
    knowledge_generated_path: Path = Field(default=BASE_DIR / "data" / "allergen_knowledge_generated.json",
                                           alias="KNOWLEDGE_GENERATED_PATH")
    # 레지스트리·지식베이스에 없는 항원 이름을 Wikipedia 에서 찾아 소개문으로 쓸지. 기본은 끔 — 검증되지 않은
    # 글이 환자 화면에 나가고, 환자가 입력한 이름이 외부로 나간다(services/knowledge_service.py 참고).
    allergen_wikipedia_lookup: bool = Field(default=False, alias="ALLERGEN_WIKIPEDIA_LOOKUP")

    # ---- 관리자 — 비밀번호가 비어 있으면 관리자 기능 전체가 꺼진다(열리지 않는다) ----
    admin_username: str = Field(default="admin", alias="ADMIN_USERNAME")
    admin_password: str = Field(default="", alias="ADMIN_PASSWORD")
    # 세션 쿠키 서명에 쓰는 서버 비밀값. 비우면 서버가 난수를 만들어 본 DB 에 둔다(저장소가 꺼져 있으면
    # 프로세스마다 새로 만든다 — 그 경우 재시작·다중 프로세스에서 로그인이 풀리므로 직접 지정할 것).
    admin_session_secret: str = Field(default="", alias="ADMIN_SESSION_SECRET")
    admin_session_hours: int = Field(default=8, alias="ADMIN_SESSION_HOURS")
    # 관리자 화면을 다른 주소(예: 별도 도메인의 프록시)에서 띄울 때만 쓴다. 쉼표로 구분한 출처 목록.
    # 비워 두면 요청의 Host 와 같은 출처만 상태 변경 요청을 보낼 수 있다.
    admin_allowed_origins: str = Field(default="", alias="ADMIN_ALLOWED_ORIGINS")

    # ---- 메일(SMTP) — SMTP_HOST 와 SMTP_FROM 이 있어야 '결과 메일로 받기' 가 켜진다 ----
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", alias="SMTP_FROM")
    smtp_from_name: str = Field(default="", alias="SMTP_FROM_NAME")
    smtp_tls: str = Field(default="starttls", alias="SMTP_TLS")   # starttls | ssl | none
    smtp_timeout: int = Field(default=20, alias="SMTP_TIMEOUT")
    email_max_per_session_hour: int = Field(default=3, alias="EMAIL_MAX_PER_SESSION_HOUR")
    email_max_per_recipient_day: int = Field(default=10, alias="EMAIL_MAX_PER_RECIPIENT_DAY")
    # 접속 IP 당 하루, 서비스 전체 하루 발송 수. 세션은 누구나 새로 만들 수 있어 세션·수신자 한도만으로는
    # 남의 주소로 대량 발송하는 것을 막지 못한다. 0 이하면 그 한도를 끈다.
    email_max_per_ip_day: int = Field(default=20, alias="EMAIL_MAX_PER_IP_DAY")
    email_max_global_day: int = Field(default=500, alias="EMAIL_MAX_GLOBAL_DAY")

    # ---- 남용 방지(인증 없는 엔드포인트) — 접속 IP 당 1분 요청 수. 0 이면 그 한도를 끈다 ----
    # IP 는 uvicorn 이 정한 접속 주소다. 프록시 뒤에서는 FORWARDED_ALLOW_IPS 를 맞게 넣어야 사용자별로 센다.
    rate_limit_ocr_per_min: int = Field(default=6, alias="RATE_LIMIT_OCR_PER_MIN")            # 비전 LLM
    rate_limit_classify_per_min: int = Field(default=20, alias="RATE_LIMIT_CLASSIFY_PER_MIN")  # 번역 LLM·저장
    rate_limit_chat_per_min: int = Field(default=30, alias="RATE_LIMIT_CHAT_PER_MIN")          # 상담 LLM
    rate_limit_api_per_min: int = Field(default=120, alias="RATE_LIMIT_API_PER_MIN")           # 그 밖의 쓰기 요청
    # 요청 본문 상한(KB). /api/ocr 는 따로 MAX_FILE_SIZE_MB 를 쓴다.
    max_request_body_kb: int = Field(default=1024, alias="MAX_REQUEST_BODY_KB")
    # /api/chat 의 messages 상한
    chat_max_messages: int = Field(default=40, alias="CHAT_MAX_MESSAGES")
    chat_max_message_chars: int = Field(default=4000, alias="CHAT_MAX_MESSAGE_CHARS")
    # 세션 보존 기간(일). 마지막 저장 뒤 이 기간이 지난 세션·산출물·대화·발송 기록을 지운다. 0 이면 지우지 않는다.
    session_retention_days: int = Field(default=0, alias="SESSION_RETENTION_DAYS")
    # 보안 응답 헤더(CSP·X-Frame-Options·nosniff). 문제가 생겼을 때 임시로 끄는 용도.
    security_headers: bool = Field(default=True, alias="SECURITY_HEADERS")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
    
    def validate_settings(self) -> bool:
        """설정 유효성 검증"""
        errors = []
        
        # OpenAI API 키 확인
        if not self.openai_api_key or self.openai_api_key == "your_openai_api_key_here":
            errors.append("OpenAI API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
        
        # 출력 디렉토리 확인 및 생성
        if not self.output_dir.exists():
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"출력 디렉토리 생성 실패: {e}")
        
        if errors:
            print("\n⚠️ 설정 오류:")
            for error in errors:
                print(f"  - {error}")
            return False
        
        return True
    
    def get_instruction_path(self, filename: str) -> Path:
        """instructions 폴더 내 파일 경로 반환"""
        return BASE_DIR / "instructions" / filename
    
    def get_allergen_map_path(self) -> Path:
        """알레르겐 매핑 파일 경로 반환"""
        return BASE_DIR / "instructions" / "allergen_map_prompt_v2.json"
    
    def get_ocr_prompt_path(self) -> Path:
        """OCR 프롬프트 파일 경로 반환"""
        return BASE_DIR / "instructions" / "OCR_prompt.md"
    
    def get_chatbot_prompt_path(self) -> Path:
        """챗봇 프롬프트 파일 경로 반환"""
        return BASE_DIR / "instructions" / "chatbot_prompt_messages.json"
    
    def get_report_template_path(self) -> Path:
        """리포트 템플릿 파일 경로 반환"""
        return BASE_DIR / "instructions" / "personalized_allergy_management_report_generator.md"
    
    def get_output_path(self, patient_name: str, test_type: str, extension: str = "json") -> Path:
        """출력 파일 경로 생성"""
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{patient_name}_{test_type}_{timestamp}.{extension}"
        # 파일명에서 특수문자 제거
        filename = "".join(c for c in filename if c.isalnum() or c in "._-")
        return self.output_dir / filename


# 싱글톤 인스턴스
settings = Settings()

# 설정 검증
if not settings.validate_settings():
    print("\n💡 도움말:")
    print("1. 프로젝트 루트에 .env 파일을 생성하세요")
    print("2. config/env_sample.txt 파일을 참고하여 필요한 환경 변수를 설정하세요")
    print("3. OpenAI API 키를 https://platform.openai.com에서 발급받으세요")


# 유틸리티 함수
def get_settings() -> Settings:
    """설정 인스턴스 반환"""
    return settings
