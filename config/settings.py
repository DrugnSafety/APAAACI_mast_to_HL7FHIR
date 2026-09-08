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
    openai_vision_model: str = Field(default="gpt-4o", alias="OPENAI_VISION_MODEL")
    # 리포트 생성용 고급 모델 - 자세한 분석과 추론을 위한 설정
    openai_report_model: str = Field(default="gpt-4o", alias="OPENAI_REPORT_MODEL")  # 리포트 생성용
    openai_chat_model: str = Field(default="gpt-4o-mini", alias="OPENAI_CHAT_MODEL")  # 결과 상담 챗봇용
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
