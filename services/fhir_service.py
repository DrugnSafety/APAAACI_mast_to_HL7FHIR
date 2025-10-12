"""
FHIR Service
HL7 FHIR Observation 및 AllergyIntolerance 리소스 생성 서비스
"""

import json
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from uuid import uuid4

from models.schemas import (
    OCRResult, AllergenResult, TestType, InterpretationType,
    SymptomFeedback, ExposureStatus, FHIRBundle
)
from utils.allergen_mapper import get_allergen_mapper

# 로거 설정
logger = logging.getLogger(__name__)


class FHIRService:
    """FHIR 리소스 생성 및 관리 서비스"""
    
    def __init__(self):
        self.allergen_mapper = get_allergen_mapper()
    
    def create_observation(
        self,
        allergen_result: AllergenResult,
        patient_id: str,
        test_date: Optional[str] = None,
        test_type: TestType = TestType.SPT
    ) -> Dict[str, Any]:
        """
        알레르겐 검사 결과를 FHIR Observation으로 변환
        
        Args:
            allergen_result: 알레르겐 검사 결과
            patient_id: 환자 ID
            test_date: 검사 날짜
            test_type: 검사 종류
            
        Returns:
            FHIR Observation 딕셔너리
        """
        try:
            # SNOMED 코드 조회
            snomed_code = self.allergen_mapper.get_snomed_code(allergen_result.allergen_name)
            
            # Observation 딕셔너리 생성
            observation = {
                "resourceType": "Observation",
                "id": f"obs-{uuid4().hex[:8]}",
                "status": "final"
            }
            
            # 검사 코드 설정
            if test_type == TestType.SPT:
                test_code = "398166005"  # Skin prick test
                test_display = "Skin prick test"
            else:
                test_code = "165967004"  # Specific IgE measurement
                test_display = "Specific IgE measurement"
            
            observation["code"] = {
                "coding": [
                    {
                        "system": "http://snomed.info/sct",
                        "code": test_code,
                        "display": test_display
                    }
                ],
                "text": f"{test_display} - {allergen_result.allergen_name}"
            }
            
            # 환자 참조
            observation["subject"] = {
                "reference": f"Patient/{patient_id}"
            }
            
            # 검사 날짜
            if test_date:
                observation["effectiveDateTime"] = test_date
            
            # 검사 결과 값
            if test_type == TestType.SPT and allergen_result.mean_mm is not None:
                # SPT 결과: wheal size in mm
                observation["valueQuantity"] = {
                    "value": allergen_result.mean_mm,
                    "unit": "mm",
                    "system": "http://unitsofmeasure.org",
                    "code": "mm"
                }
                
            elif test_type == TestType.MAST and allergen_result.value is not None:
                # MAST 결과: IgE value in kU/L
                observation["valueQuantity"] = {
                    "value": allergen_result.value,
                    "unit": allergen_result.unit or "kU/L",
                    "system": "http://unitsofmeasure.org",
                    "code": "kU/L"
                }
            
            # 해석 (Positive/Negative)
            if allergen_result.interpretation:
                interpretation_code = "POS" if allergen_result.interpretation == InterpretationType.POSITIVE else "NEG"
                interpretation_display = allergen_result.interpretation.value
                
                observation["interpretation"] = [
                    {
                        "coding": [
                            {
                                "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                                "code": interpretation_code,
                                "display": interpretation_display
                            }
                        ]
                    }
                ]
            
            # 알레르겐 정보를 component로 추가
            if snomed_code:
                observation["component"] = [
                    {
                        "code": {
                            "coding": [
                                {
                                    "system": "http://snomed.info/sct",
                                    "code": snomed_code,
                                    "display": allergen_result.allergen_name
                                }
                            ],
                            "text": f"{allergen_result.allergen_name} ({allergen_result.korean_name})"
                        }
                    }
                ]
            
            # 메모 추가
            if allergen_result.note:
                observation["note"] = [{"text": allergen_result.note}]
            
            return observation
            
        except Exception as e:
            logger.error(f"Observation 생성 실패: {e}")
            raise
    
    def create_observation_bundle(
        self,
        ocr_result: OCRResult,
        patient_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        OCR 결과 전체를 FHIR Observation Bundle로 변환
        
        Args:
            ocr_result: OCR 결과
            patient_id: 환자 ID (None일 경우 OCR 결과에서 추출)
            
        Returns:
            FHIR Bundle 딕셔너리
        """
        try:
            # 환자 ID 결정
            if not patient_id:
                patient_id = ocr_result.patient.name or f"patient-{uuid4().hex[:8]}"
            
            # Bundle 생성
            bundle = {
                "resourceType": "Bundle",
                "id": f"bundle-obs-{uuid4().hex[:8]}",
                "type": "collection",
                "entry": []
            }
            
            # 각 알레르겐 결과를 Observation으로 변환
            for allergen_result in ocr_result.results:
                observation = self.create_observation(
                    allergen_result=allergen_result,
                    patient_id=patient_id,
                    test_date=ocr_result.patient.test_date,
                    test_type=ocr_result.test_type
                )
                
                # Bundle entry로 추가
                bundle["entry"].append({
                    "resource": observation
                })
            
            logger.info(f"Observation Bundle 생성 완료: {len(bundle['entry'])}개 항목")
            return bundle
            
        except Exception as e:
            logger.error(f"Observation Bundle 생성 실패: {e}")
            raise
    
    def create_allergy_intolerance(
        self,
        allergen_name: str,
        patient_id: str,
        patient_name: Optional[str] = None,
        test_date: Optional[str] = None,
        exposure_status: ExposureStatus = ExposureStatus.SYMPTOMATIC
    ) -> Dict[str, Any]:
        """
        AllergyIntolerance 리소스 생성
        
        Args:
            allergen_name: 알레르겐 이름
            patient_id: 환자 ID
            patient_name: 환자 이름
            test_date: 검사/기록 날짜
            exposure_status: 노출 후 증상 상태
            
        Returns:
            FHIR AllergyIntolerance 딕셔너리
        """
        try:
            # 알레르겐 정보 조회
            mapping = self.allergen_mapper.find_allergen(allergen_name)
            snomed_code = mapping.snomed if mapping else None
            korean_name = mapping.korean_name if mapping else None
            category = mapping.category if mapping else "environment"
            
            # AllergyIntolerance 생성
            allergy = {
                "resourceType": "AllergyIntolerance",
                "id": f"allergy-{uuid4().hex[:8]}"
            }
            
            # Clinical Status (active/inactive)
            if exposure_status == ExposureStatus.SYMPTOMATIC:
                clinical_status = "active"
            elif exposure_status == ExposureStatus.ASYMPTOMATIC:
                clinical_status = "inactive"
            else:
                clinical_status = "resolved"
            
            allergy["clinicalStatus"] = {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                        "code": clinical_status,
                        "display": clinical_status.capitalize()
                    }
                ]
            }
            
            # Verification Status
            if exposure_status == ExposureStatus.SYMPTOMATIC:
                verification = "confirmed"
            elif exposure_status == ExposureStatus.UNKNOWN_EXPOSURE:
                verification = "unconfirmed"
            else:
                verification = "refuted"
            
            allergy["verificationStatus"] = {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                        "code": verification,
                        "display": verification.capitalize()
                    }
                ]
            }
            
            # Type
            allergy["type"] = "allergy"
            
            # Category
            category_map = {
                "Food": "food",
                "Mite": "environment",
                "Pollen": "environment",
                "Mold": "environment",
                "Animal": "environment",
                "Insect": "environment",
                "Other": "environment"
            }
            allergy["category"] = [category_map.get(category, "environment")]
            
            # Criticality
            allergy["criticality"] = "high" if exposure_status == ExposureStatus.SYMPTOMATIC else "low"
            
            # Code (allergen)
            allergy["code"] = {}
            if snomed_code:
                allergy["code"]["coding"] = [
                    {
                        "system": "http://snomed.info/sct",
                        "code": snomed_code,
                        "display": allergen_name
                    }
                ]
            allergy["code"]["text"] = f"{allergen_name} ({korean_name})" if korean_name else allergen_name
            
            # Patient
            allergy["patient"] = {
                "reference": f"Patient/{patient_id}"
            }
            if patient_name:
                allergy["patient"]["display"] = patient_name
            
            # Recorded date
            if test_date:
                allergy["recordedDate"] = test_date
            else:
                allergy["recordedDate"] = datetime.now().strftime("%Y-%m-%d")
            
            # Reaction (증상이 있는 경우만)
            if exposure_status == ExposureStatus.SYMPTOMATIC:
                allergy["reaction"] = [
                    {
                        "manifestation": [
                            {
                                "coding": [
                                    {
                                        "system": "http://snomed.info/sct",
                                        "code": "165014009",
                                        "display": "Allergy test positive"
                                    }
                                ],
                                "text": "알레르기 검사 양성"
                            }
                        ],
                        "severity": "moderate"
                    }
                ]
            
            return allergy
            
        except Exception as e:
            logger.error(f"AllergyIntolerance 생성 실패: {e}")
            raise
    
    def create_allergy_intolerance_bundle(
        self,
        symptom_feedback: SymptomFeedback,
        ocr_result: Optional[OCRResult] = None
    ) -> Dict[str, Any]:
        """
        증상 피드백을 기반으로 AllergyIntolerance Bundle 생성
        
        Args:
            symptom_feedback: 증상 피드백 결과
            ocr_result: OCR 결과 (추가 정보용)
            
        Returns:
            FHIR Bundle 딕셔너리
        """
        try:
            # Bundle 생성
            bundle = {
                "resourceType": "Bundle",
                "id": f"bundle-allergy-{uuid4().hex[:8]}",
                "type": "collection",
                "entry": []
            }
            
            # 증상이 있는 알레르겐들에 대해 AllergyIntolerance 생성
            for allergen_name in symptom_feedback.exposure_feedback.get("symptomatic", []):
                allergy = self.create_allergy_intolerance(
                    allergen_name=allergen_name,
                    patient_id=symptom_feedback.patient_id,
                    patient_name=symptom_feedback.patient_name,
                    test_date=symptom_feedback.test_date,
                    exposure_status=ExposureStatus.SYMPTOMATIC
                )
                
                bundle["entry"].append({
                    "resource": allergy
                })
            
            logger.info(f"AllergyIntolerance Bundle 생성 완료: {len(bundle['entry'])}개 항목")
            return bundle
            
        except Exception as e:
            logger.error(f"AllergyIntolerance Bundle 생성 실패: {e}")
            raise
    
    def bundle_to_dict(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        """
        FHIR Bundle을 딕셔너리로 변환 (이미 딕셔너리이므로 그대로 반환)
        
        Args:
            bundle: FHIR Bundle 딕셔너리
            
        Returns:
            딕셔너리 형태의 Bundle
        """
        return bundle
    
    def validate_fhir_resource(self, resource: Dict[str, Any]) -> Dict[str, Any]:
        """
        FHIR 리소스 유효성 검증
        
        Args:
            resource: FHIR 리소스 딕셔너리
            
        Returns:
            검증 결과 딕셔너리
        """
        try:
            issues = []
            warnings = []
            
            # 필수 필드 확인
            if not resource.get('resourceType'):
                issues.append("resourceType이 없습니다")
            
            if not resource.get('id'):
                warnings.append("id가 없습니다")
            
            # Observation 특정 검증
            if resource.get('resourceType') == 'Observation':
                if not resource.get('status'):
                    issues.append("Observation status가 없습니다")
                if not resource.get('code'):
                    issues.append("Observation code가 없습니다")
                if not resource.get('subject'):
                    warnings.append("Observation subject가 없습니다")
            
            # AllergyIntolerance 특정 검증
            elif resource.get('resourceType') == 'AllergyIntolerance':
                if not resource.get('clinicalStatus'):
                    issues.append("AllergyIntolerance clinicalStatus가 없습니다")
                if not resource.get('verificationStatus'):
                    issues.append("AllergyIntolerance verificationStatus가 없습니다")
                if not resource.get('patient'):
                    issues.append("AllergyIntolerance patient가 없습니다")
            
            return {
                "valid": len(issues) == 0,
                "issues": issues,
                "warnings": warnings
            }
            
        except Exception as e:
            logger.error(f"FHIR 리소스 검증 실패: {e}")
            return {
                "valid": False,
                "issues": [str(e)],
                "warnings": []
            }


# 싱글톤 인스턴스
_fhir_service: Optional[FHIRService] = None


def get_fhir_service() -> FHIRService:
    """FHIR 서비스 싱글톤 인스턴스 반환"""
    global _fhir_service
    if _fhir_service is None:
        _fhir_service = FHIRService()
    return _fhir_service