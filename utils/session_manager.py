"""
Session Manager
Streamlit 세션 상태 관리 유틸리티
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List
import streamlit as st

from models.schemas import (
    ProcessingStep, ProcessingStatus, ProcessingResult,
    OCRResult, PatientInfo, SymptomFeedback,
    FHIRBundle, AllergyManagementReport
)

logger = logging.getLogger(__name__)


class SessionManager:
    """Streamlit 세션 상태 관리자"""
    
    # 세션 상태 키
    KEYS = {
        'session_id': 'session_id',
        'current_step': 'current_step', 
        'processing_status': 'processing_status',
        'uploaded_images': 'uploaded_images',
        'ocr_results': 'ocr_results',
        'patient_info': 'patient_info',
        'chat_messages': 'chat_messages',
        'symptom_feedback': 'symptom_feedback',
        'fhir_observations': 'fhir_observations',
        'fhir_allergies': 'fhir_allergies',
        'report': 'report',
        'errors': 'errors',
        'processing_history': 'processing_history'
    }
    
    @classmethod
    def initialize(cls):
        """세션 상태 초기화"""
        # 세션 ID 생성
        if cls.KEYS['session_id'] not in st.session_state:
            st.session_state[cls.KEYS['session_id']] = f"session_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        
        # 처리 상태 초기화
        if cls.KEYS['processing_status'] not in st.session_state:
            st.session_state[cls.KEYS['processing_status']] = ProcessingStatus(
                current_step=ProcessingStep.UPLOAD,
                completed_steps=[],
                errors=[],
                warnings=[],
                progress=0.0,
                message="시작 대기 중..."
            )
        
        # 데이터 저장소 초기화
        for key in ['uploaded_images', 'ocr_results', 'chat_messages', 
                   'errors', 'processing_history']:
            if cls.KEYS[key] not in st.session_state:
                st.session_state[cls.KEYS[key]] = []
        
        # 단일 객체 초기화
        for key in ['patient_info', 'symptom_feedback', 'fhir_observations',
                   'fhir_allergies', 'report']:
            if cls.KEYS[key] not in st.session_state:
                st.session_state[cls.KEYS[key]] = None
        
        # 현재 스텝 초기화
        if cls.KEYS['current_step'] not in st.session_state:
            st.session_state[cls.KEYS['current_step']] = 0
    
    @classmethod
    def get_session_id(cls) -> str:
        """현재 세션 ID 반환"""
        return st.session_state.get(cls.KEYS['session_id'], '')
    
    @classmethod
    def get_current_step(cls) -> int:
        """현재 처리 단계 반환"""
        return st.session_state.get(cls.KEYS['current_step'], 0)
    
    @classmethod
    def set_current_step(cls, step: int):
        """현재 처리 단계 설정"""
        st.session_state[cls.KEYS['current_step']] = step
    
    @classmethod
    def get_processing_status(cls) -> ProcessingStatus:
        """처리 상태 반환"""
        return st.session_state.get(cls.KEYS['processing_status'])
    
    @classmethod
    def update_processing_status(
        cls,
        step: Optional[ProcessingStep] = None,
        progress: Optional[float] = None,
        message: Optional[str] = None,
        error: Optional[str] = None,
        warning: Optional[str] = None
    ):
        """처리 상태 업데이트"""
        status = st.session_state[cls.KEYS['processing_status']]
        
        if step:
            status.current_step = step
            if step not in status.completed_steps:
                status.completed_steps.append(step)
        
        if progress is not None:
            status.progress = progress
        
        if message:
            status.message = message
        
        if error:
            status.errors.append(error)
            st.session_state[cls.KEYS['errors']].append({
                'timestamp': datetime.now(),
                'error': error
            })
        
        if warning:
            status.warnings.append(warning)
    
    @classmethod
    def save_ocr_result(cls, result: OCRResult):
        """OCR 결과 저장"""
        if cls.KEYS['ocr_results'] not in st.session_state:
            st.session_state[cls.KEYS['ocr_results']] = []
        st.session_state[cls.KEYS['ocr_results']].append(result)
        cls.update_processing_status(
            step=ProcessingStep.OCR,
            progress=30.0,
            message="OCR 완료"
        )
    
    @classmethod
    def save_patient_info(cls, patient_info: PatientInfo):
        """환자 정보 저장"""
        st.session_state[cls.KEYS['patient_info']] = patient_info
        cls.update_processing_status(
            step=ProcessingStep.PATIENT_INFO,
            progress=40.0,
            message="환자 정보 저장 완료"
        )
    
    @classmethod
    def save_symptom_feedback(cls, feedback: SymptomFeedback):
        """증상 피드백 저장"""
        st.session_state[cls.KEYS['symptom_feedback']] = feedback
        cls.update_processing_status(
            step=ProcessingStep.SYMPTOM_FEEDBACK,
            progress=50.0,
            message="증상 피드백 수집 완료"
        )
    
    @classmethod
    def save_fhir_observations(cls, bundle: Dict[str, Any]):
        """FHIR Observation Bundle 저장"""
        st.session_state[cls.KEYS['fhir_observations']] = bundle
        cls.update_processing_status(
            step=ProcessingStep.FHIR_OBSERVATION,
            progress=60.0,
            message="FHIR Observation 생성 완료"
        )
    
    @classmethod
    def save_fhir_allergies(cls, bundle: Dict[str, Any]):
        """FHIR AllergyIntolerance Bundle 저장"""
        st.session_state[cls.KEYS['fhir_allergies']] = bundle
        cls.update_processing_status(
            step=ProcessingStep.FHIR_ALLERGY,
            progress=70.0,
            message="FHIR AllergyIntolerance 생성 완료"
        )
    
    @classmethod
    def save_report(cls, report: AllergyManagementReport):
        """리포트 저장"""
        st.session_state[cls.KEYS['report']] = report
        cls.update_processing_status(
            step=ProcessingStep.REPORT_GENERATION,
            progress=90.0,
            message="리포트 생성 완료"
        )
    
    @classmethod
    def mark_completed(cls):
        """전체 처리 완료 표시"""
        cls.update_processing_status(
            step=ProcessingStep.COMPLETED,
            progress=100.0,
            message="모든 처리가 완료되었습니다!"
        )
        
        # 처리 이력에 추가
        history_entry = {
            'session_id': cls.get_session_id(),
            'completed_at': datetime.now(),
            'patient_name': st.session_state[cls.KEYS['patient_info']].name if st.session_state[cls.KEYS['patient_info']] else 'Unknown'
        }
        st.session_state[cls.KEYS['processing_history']].append(history_entry)
    
    @classmethod
    def reset_session(cls):
        """세션 초기화 (새 처리 시작)"""
        keys_to_keep = [cls.KEYS['session_id'], cls.KEYS['processing_history']]
        
        for key in cls.KEYS.values():
            if key not in keys_to_keep and key in st.session_state:
                del st.session_state[key]
        
        # 재초기화
        cls.initialize()
    
    @classmethod
    def export_session_data(cls) -> Dict[str, Any]:
        """세션 데이터 내보내기"""
        data = {}
        for key_name, key_value in cls.KEYS.items():
            if key_value in st.session_state:
                value = st.session_state[key_value]
                # Pydantic 모델인 경우 dict로 변환
                if hasattr(value, 'model_dump'):
                    value = value.model_dump()
                data[key_name] = value
        return data
    
    @classmethod
    def save_to_file(cls, filepath: Path):
        """세션 데이터를 파일로 저장"""
        try:
            data = cls.export_session_data()
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            logger.info(f"세션 데이터 저장 완료: {filepath}")
            return True
        except Exception as e:
            logger.error(f"세션 데이터 저장 실패: {e}")
            return False
    
    @classmethod
    def can_proceed_to_step(cls, step: int) -> bool:
        """특정 단계로 진행 가능한지 확인"""
        current = cls.get_current_step()
        
        # 이전 단계가 완료되지 않으면 진행 불가
        if step > current + 1:
            return False
        
        # 각 단계별 전제 조건 확인
        if step == 1:  # OCR
            return bool(st.session_state.get(cls.KEYS['uploaded_images']))
        elif step == 2:  # Patient Info
            return bool(st.session_state.get(cls.KEYS['ocr_results']))
        elif step == 3:  # Symptom Feedback
            return bool(st.session_state.get(cls.KEYS['patient_info']))
        elif step == 4:  # FHIR Generation
            return bool(st.session_state.get(cls.KEYS['symptom_feedback']))
        elif step == 5:  # Report Generation
            return bool(st.session_state.get(cls.KEYS['fhir_observations'])) and \
                   bool(st.session_state.get(cls.KEYS['fhir_allergies']))
        
        return True
    
    @classmethod
    def get_progress_percentage(cls) -> float:
        """전체 진행률 계산"""
        status = cls.get_processing_status()
        return status.progress if status else 0.0
    
    @classmethod
    def get_error_messages(cls) -> List[str]:
        """에러 메시지 목록 반환"""
        status = cls.get_processing_status()
        return status.errors if status else []
    
    @classmethod
    def get_warning_messages(cls) -> List[str]:
        """경고 메시지 목록 반환"""
        status = cls.get_processing_status()
        return status.warnings if status else []


# 데코레이터 함수
def requires_step(required_step: int):
    """특정 단계 완료를 요구하는 데코레이터"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not SessionManager.can_proceed_to_step(required_step):
                st.warning(f"이전 단계를 먼저 완료해주세요.")
                return None
            return func(*args, **kwargs)
        return wrapper
    return decorator
