"""
Error Handler
에러 처리 및 재시도 메커니즘
"""

import logging
import traceback
from functools import wraps
from typing import Any, Callable, Optional, Type, Union, Tuple
from time import sleep
import streamlit as st

logger = logging.getLogger(__name__)


class ApplicationError(Exception):
    """애플리케이션 기본 에러"""
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class OCRError(ApplicationError):
    """OCR 처리 에러"""
    pass


class FHIRError(ApplicationError):
    """FHIR 생성 에러"""
    pass


class ReportError(ApplicationError):
    """리포트 생성 에러"""
    pass


class APIError(ApplicationError):
    """API 호출 에러"""
    pass


class ValidationError(ApplicationError):
    """데이터 검증 에러"""
    pass


class ErrorHandler:
    """에러 처리 클래스"""
    
    @staticmethod
    def handle_error(
        error: Exception,
        context: str = "처리",
        show_ui: bool = True
    ) -> Tuple[bool, Optional[str]]:
        """
        에러 처리
        
        Args:
            error: 발생한 에러
            context: 에러 컨텍스트
            show_ui: UI에 에러 표시 여부
            
        Returns:
            (재시도 가능 여부, 에러 메시지)
        """
        error_msg = f"{context} 중 오류 발생: {str(error)}"
        logger.error(error_msg)
        logger.error(traceback.format_exc())
        
        # 에러 타입별 처리
        retry_possible = False
        user_message = ""
        
        if isinstance(error, OCRError):
            user_message = "📷 이미지 인식 중 문제가 발생했습니다. 다른 이미지를 시도해보세요."
            retry_possible = True
            
        elif isinstance(error, APIError):
            user_message = "🌐 API 호출 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."
            retry_possible = True
            
        elif isinstance(error, FHIRError):
            user_message = "🏥 FHIR 리소스 생성 중 문제가 발생했습니다."
            retry_possible = False
            
        elif isinstance(error, ReportError):
            user_message = "📄 리포트 생성 중 문제가 발생했습니다."
            retry_possible = True
            
        elif isinstance(error, ValidationError):
            user_message = f"⚠️ 데이터 검증 실패: {str(error)}"
            retry_possible = False
            
        elif "API" in str(error) or "api" in str(error):
            user_message = "🔑 API 키를 확인해주세요."
            retry_possible = False
            
        elif "timeout" in str(error).lower():
            user_message = "⏱️ 요청 시간이 초과되었습니다. 다시 시도해주세요."
            retry_possible = True
            
        else:
            user_message = f"❌ {error_msg}"
            retry_possible = False
        
        # UI 표시
        if show_ui:
            st.error(user_message)
            
            if retry_possible:
                st.info("다시 시도할 수 있습니다.")
            
            # 상세 에러 표시 (디버그 모드)
            with st.expander("🔍 상세 오류 정보"):
                st.code(traceback.format_exc())
        
        return retry_possible, user_message
    
    @staticmethod
    def log_error(
        error: Exception,
        context: str = "처리",
        additional_info: Optional[dict] = None
    ):
        """
        에러 로깅
        
        Args:
            error: 발생한 에러
            context: 에러 컨텍스트
            additional_info: 추가 정보
        """
        error_info = {
            'context': context,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'traceback': traceback.format_exc()
        }
        
        if additional_info:
            error_info.update(additional_info)
        
        logger.error(f"Error in {context}: {error_info}")
    
    @staticmethod
    def safe_execute(
        func: Callable,
        *args,
        context: str = "작업",
        default_value: Any = None,
        show_error: bool = True,
        **kwargs
    ) -> Any:
        """
        안전한 함수 실행
        
        Args:
            func: 실행할 함수
            context: 실행 컨텍스트
            default_value: 에러 시 반환할 기본값
            show_error: 에러 표시 여부
            
        Returns:
            함수 실행 결과 또는 기본값
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ErrorHandler.handle_error(e, context, show_error)
            return default_value


def retry_on_error(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    재시도 데코레이터
    
    Args:
        max_retries: 최대 재시도 횟수
        delay: 재시도 간 대기 시간
        backoff: 대기 시간 증가 비율
        exceptions: 재시도할 예외 타입들
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            current_delay = delay
            
            while retry_count <= max_retries:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    retry_count += 1
                    
                    if retry_count > max_retries:
                        logger.error(f"최대 재시도 횟수 초과: {func.__name__}")
                        raise
                    
                    logger.warning(
                        f"재시도 {retry_count}/{max_retries}: {func.__name__} - {str(e)}"
                    )
                    
                    sleep(current_delay)
                    current_delay *= backoff
                    
            return None
        return wrapper
    return decorator


def handle_streamlit_error(func: Callable) -> Callable:
    """
    Streamlit 함수용 에러 핸들링 데코레이터
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            ErrorHandler.handle_error(e, f"{func.__name__} 실행")
            return None
    return wrapper


def validate_required_fields(**fields) -> bool:
    """
    필수 필드 검증
    
    Args:
        **fields: 검증할 필드들 (이름=값)
        
    Returns:
        모든 필드가 유효한 경우 True
    """
    missing_fields = []
    
    for field_name, field_value in fields.items():
        if field_value is None or (isinstance(field_value, str) and not field_value.strip()):
            missing_fields.append(field_name)
    
    if missing_fields:
        error_msg = f"필수 필드가 누락되었습니다: {', '.join(missing_fields)}"
        raise ValidationError(error_msg)
    
    return True


def validate_api_key(api_key: str) -> bool:
    """
    API 키 검증
    
    Args:
        api_key: 검증할 API 키
        
    Returns:
        유효한 경우 True
    """
    if not api_key or api_key == "your_openai_api_key_here":
        raise APIError("유효한 OpenAI API 키를 설정해주세요")
    
    if not api_key.startswith("sk-"):
        raise APIError("올바른 형식의 OpenAI API 키가 아닙니다")
    
    return True


class ErrorContext:
    """에러 컨텍스트 매니저"""
    
    def __init__(
        self,
        operation: str,
        show_spinner: bool = True,
        spinner_text: Optional[str] = None
    ):
        """
        에러 컨텍스트 초기화
        
        Args:
            operation: 작업 이름
            show_spinner: 스피너 표시 여부
            spinner_text: 스피너 텍스트
        """
        self.operation = operation
        self.show_spinner = show_spinner
        self.spinner_text = spinner_text or f"{operation} 중..."
        self.spinner = None
    
    def __enter__(self):
        if self.show_spinner:
            self.spinner = st.spinner(self.spinner_text)
            self.spinner.__enter__()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.spinner:
            self.spinner.__exit__(exc_type, exc_val, exc_tb)
        
        if exc_type is not None:
            ErrorHandler.handle_error(exc_val, self.operation)
            return True  # 예외 전파 방지


# 에러 복구 전략
class RecoveryStrategy:
    """에러 복구 전략"""
    
    @staticmethod
    def recover_ocr_error(error: OCRError) -> Optional[dict]:
        """OCR 에러 복구"""
        logger.info("OCR 에러 복구 시도")
        
        # 기본 데이터 반환
        return {
            'test_type': 'SPT',
            'patient': {},
            'results': []
        }
    
    @staticmethod
    def recover_api_error(error: APIError) -> Optional[Any]:
        """API 에러 복구"""
        logger.info("API 에러 복구 시도")
        
        # 캐시된 데이터나 기본값 반환
        return None
    
    @staticmethod
    def recover_validation_error(error: ValidationError) -> Optional[Any]:
        """검증 에러 복구"""
        logger.info("검증 에러 복구 시도")
        
        # 기본값으로 채우기
        return None
