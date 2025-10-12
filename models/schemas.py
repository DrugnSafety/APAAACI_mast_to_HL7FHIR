"""
Pydantic Models and Schemas
데이터 검증 및 직렬화를 위한 모델 정의
"""

from typing import List, Optional, Dict, Any, Literal, Union
from datetime import datetime, date
from pydantic import BaseModel, Field, validator
from enum import Enum


# ============= Enums =============

class TestType(str, Enum):
    """검사 종류"""
    SPT = "SPT"
    MAST = "MAST"


class InterpretationType(str, Enum):
    """검사 결과 판정"""
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    EQUIVOCAL = "Equivocal"
    UNKNOWN = "Unknown"


class AllergenCategory(str, Enum):
    """알레르겐 카테고리"""
    MITE = "Mite"
    POLLEN = "Pollen"
    MOLD = "Mold"
    ANIMAL = "Animal"
    INSECT = "Insect"
    FOOD = "Food"
    CONTROL = "Control"
    MIXTURE = "Mixture"
    OTHER = "Other"


class AllergenSubcategory(str, Enum):
    """알레르겐 서브카테고리"""
    TREE = "Tree"
    GRASS = "Grass"
    WEED = "Weed"
    INDOOR = "Indoor"
    OUTDOOR = "Outdoor"
    COCKROACH = "Cockroach"
    HOUSE_DUST_MITE = "House dust mite"
    TREE_POLLEN_MIX = "Tree pollen mix"
    MOLD_MIX = "Mold mix"


class ExposureStatus(str, Enum):
    """노출 후 증상 상태"""
    SYMPTOMATIC = "symptomatic"       # 증상 있음
    ASYMPTOMATIC = "asymptomatic"     # 노출되었지만 증상 없음
    UNKNOWN_EXPOSURE = "unknown_exposure"  # 노출 경험 없음


# ============= OCR Models =============

class PatientInfo(BaseModel):
    """환자 정보"""
    name: Optional[str] = Field(None, description="환자 이름")
    age: Optional[int] = Field(None, description="환자 나이")
    gender: Optional[Literal["M", "F", "남", "여"]] = Field(None, description="환자 성별")
    test_date: Optional[str] = Field(None, description="검사 날짜 (YYYY-MM-DD)")
    histamine_mean_mm: Optional[float] = Field(None, description="SPT 히스타민 대조 평균값")
    negative_control_mean_mm: Optional[float] = Field(None, description="SPT 음성 대조 평균값")
    
    @validator('test_date')
    def validate_date_format(cls, v):
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                # 다른 형식 시도
                for fmt in ["%Y/%m/%d", "%Y.%m.%d", "%Y년%m월%d일"]:
                    try:
                        dt = datetime.strptime(v.replace("년", "").replace("월", "").replace("일", ""), "%Y%m%d")
                        return dt.strftime("%Y-%m-%d")
                    except:
                        pass
        return v


class AllergenResult(BaseModel):
    """개별 알레르겐 검사 결과"""
    index: int = Field(..., description="순서 번호")
    raw_text: str = Field(..., description="원본 OCR 텍스트")
    allergen_name: str = Field(..., description="알레르겐 이름")
    korean_name: Optional[str] = Field(None, description="한국어 이름")
    
    # SPT 관련 필드
    size_text: Optional[str] = Field(None, description="SPT 크기 (예: '3x4')")
    mean_mm: Optional[float] = Field(None, description="SPT 평균 직경 (mm)")
    
    # MAST 관련 필드
    value: Optional[float] = Field(None, description="MAST/UniCAP 수치")
    unit: Optional[str] = Field(None, description="단위 (mm, kU/L, IU/mL)")
    class_value: Optional[Union[int, str]] = Field(None, description="Class 값 (0-6, P, N)", alias="class")
    
    # 공통 필드
    category: Optional[AllergenCategory] = None
    subcategory: Optional[AllergenSubcategory] = None
    interpretation: Optional[InterpretationType] = None
    confidence: float = Field(1.0, description="OCR 신뢰도 (0-1)")
    note: Optional[str] = Field(None, description="추가 메모")


class OCRResult(BaseModel):
    """OCR 추출 결과"""
    test_type: TestType
    patient: PatientInfo
    results: List[AllergenResult]
    metadata: Optional[Dict[str, Any]] = None
    
    class Config:
        populate_by_name = True  # alias 사용 허용


# ============= FHIR Models =============

class FHIRCoding(BaseModel):
    """FHIR Coding 요소"""
    system: Optional[str] = None
    code: Optional[str] = None
    display: Optional[str] = None


class FHIRCodeableConcept(BaseModel):
    """FHIR CodeableConcept"""
    coding: Optional[List[FHIRCoding]] = None
    text: Optional[str] = None


class FHIRReference(BaseModel):
    """FHIR Reference"""
    reference: str
    display: Optional[str] = None


class FHIRObservation(BaseModel):
    """FHIR Observation 리소스 (간소화)"""
    resourceType: Literal["Observation"] = "Observation"
    id: str
    status: Literal["final"] = "final"
    code: FHIRCodeableConcept
    subject: FHIRReference
    effectiveDateTime: Optional[str] = None
    valueQuantity: Optional[Dict[str, Any]] = None
    interpretation: Optional[List[FHIRCodeableConcept]] = None
    component: Optional[List[Dict[str, Any]]] = None


class FHIRAllergyIntolerance(BaseModel):
    """FHIR AllergyIntolerance 리소스 (간소화)"""
    resourceType: Literal["AllergyIntolerance"] = "AllergyIntolerance"
    id: str
    clinicalStatus: FHIRCodeableConcept
    verificationStatus: FHIRCodeableConcept
    type: Optional[Literal["allergy", "intolerance"]] = "allergy"
    category: Optional[List[str]] = None
    criticality: Optional[str] = None
    code: FHIRCodeableConcept
    patient: FHIRReference
    recordedDate: Optional[str] = None
    reaction: Optional[List[Dict[str, Any]]] = None


class FHIRBundle(BaseModel):
    """FHIR Bundle"""
    resourceType: Literal["Bundle"] = "Bundle"
    type: Literal["collection"] = "collection"
    entry: List[Dict[str, Any]]


# ============= Chatbot Models =============

class SymptomFeedback(BaseModel):
    """증상 피드백 결과"""
    patient_id: str
    test_date: str
    patient_name: Optional[str] = None
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    exposure_feedback: Dict[str, List[str]]  # symptomatic, asymptomatic, unknown_exposure


class ChatMessage(BaseModel):
    """채팅 메시지"""
    role: Literal["system", "assistant", "user"]
    content: str
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)


class ChatSession(BaseModel):
    """채팅 세션"""
    session_id: str
    patient_id: str
    messages: List[ChatMessage] = []
    symptom_feedback: Optional[SymptomFeedback] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ============= Report Models =============

class AllergyManagementSection(BaseModel):
    """리포트 섹션"""
    title: str
    content: List[str]
    icon: Optional[str] = None


class AllergyManagementReport(BaseModel):
    """알레르기 관리 리포트"""
    patient_name: str
    patient_age: Optional[int] = None
    patient_gender: Optional[str] = None
    test_date: str
    key_allergens: List[str]
    sections: List[AllergyManagementSection]
    markdown_content: Optional[str] = None
    pdf_path: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)


# ============= Allergen Mapping Models =============

class AllergenMapping(BaseModel):
    """알레르겐 매핑 정보"""
    canonical_name: str
    korean_name: str
    aliases: List[str] = []
    ocr_aliases: List[str] = []
    category: str
    subcategory: Optional[str] = None
    snomed: Optional[str] = None


class AllergenDatabase(BaseModel):
    """알레르겐 데이터베이스"""
    version: str
    locale: str
    global_rules: Dict[str, Any]
    entries: List[AllergenMapping]


# ============= Processing Status Models =============

class ProcessingStep(str, Enum):
    """처리 단계"""
    UPLOAD = "upload"
    OCR = "ocr"
    PATIENT_INFO = "patient_info"
    SYMPTOM_FEEDBACK = "symptom_feedback"
    FHIR_OBSERVATION = "fhir_observation"
    FHIR_ALLERGY = "fhir_allergy"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"


class ProcessingStatus(BaseModel):
    """처리 상태"""
    current_step: ProcessingStep
    completed_steps: List[ProcessingStep] = []
    errors: List[str] = []
    warnings: List[str] = []
    progress: float = Field(0.0, ge=0.0, le=100.0)
    message: str = ""


class ProcessingResult(BaseModel):
    """전체 처리 결과"""
    session_id: str
    status: ProcessingStatus
    ocr_result: Optional[OCRResult] = None
    patient_info: Optional[PatientInfo] = None
    symptom_feedback: Optional[SymptomFeedback] = None
    fhir_observations: Optional[FHIRBundle] = None
    fhir_allergy_intolerances: Optional[FHIRBundle] = None
    report: Optional[AllergyManagementReport] = None
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


# ============= Validation Utilities =============

def validate_allergen_name(name: str, allergen_db: AllergenDatabase) -> Optional[AllergenMapping]:
    """알레르겐 이름 검증 및 매핑"""
    name_lower = name.lower().strip()
    
    for entry in allergen_db.entries:
        # 정확한 매칭
        if entry.canonical_name.lower() == name_lower:
            return entry
        if entry.korean_name == name:
            return entry
        
        # Alias 매칭
        for alias in entry.aliases + entry.ocr_aliases:
            if alias.lower() == name_lower:
                return entry
    
    return None


def determine_interpretation(
    test_type: TestType,
    mean_mm: Optional[float] = None,
    class_value: Optional[Union[int, str]] = None,
    value: Optional[float] = None,
    histamine_control: Optional[float] = None
) -> InterpretationType:
    """검사 결과 해석"""
    if test_type == TestType.SPT:
        if mean_mm is None:
            return InterpretationType.UNKNOWN
        
        # SPT 양성 기준: 3mm 이상 또는 히스타민 대조의 50% 이상
        if mean_mm >= 3.0:
            return InterpretationType.POSITIVE
        elif histamine_control and mean_mm >= (histamine_control * 0.5):
            return InterpretationType.POSITIVE
        elif mean_mm >= 2.0:
            return InterpretationType.EQUIVOCAL
        else:
            return InterpretationType.NEGATIVE
    
    elif test_type == TestType.MAST:
        # MAST 양성 기준: Class 1 이상 또는 0.35 kU/L 이상
        if class_value:
            if isinstance(class_value, int) and class_value >= 1:
                return InterpretationType.POSITIVE
            elif class_value == "P":
                return InterpretationType.POSITIVE
            elif class_value == "N" or (isinstance(class_value, int) and class_value == 0):
                return InterpretationType.NEGATIVE
        
        if value is not None:
            if value >= 0.35:
                return InterpretationType.POSITIVE
            else:
                return InterpretationType.NEGATIVE
    
    return InterpretationType.UNKNOWN
