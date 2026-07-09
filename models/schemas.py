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
    SPT = "SPT"       # 피부단자검사 (Skin Prick Test), 팽진 크기 mm
    MAST = "MAST"     # 다중 알레르겐 동시검사, 특이 IgE (class / kU/L)
    UNICAP = "UniCAP"  # ImmunoCAP 정량 특이 IgE (kU/L), MAST 와 동일하게 해석


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
    # category/subcategory 는 OCR·매핑·KB·프론트에서 다양한 표기(대문자 enum, 소문자
    # 정규화값 'pollen_tree' 등)로 들어오므로 관대하게 문자열로 받는다(다운스트림에서 normalize).
    category: Optional[str] = None
    subcategory: Optional[str] = None
    interpretation: Optional[InterpretationType] = None
    confidence: float = Field(1.0, description="OCR 신뢰도 (0-1)")
    note: Optional[str] = Field(None, description="추가 메모")

    class Config:
        populate_by_name = True  # 'class' alias 와 'class_value' 필드명 모두 허용


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


# ============= Screening & Clinical Relevance Models =============

class ClinicalRelevance(str, Enum):
    """알레르겐의 임상적 의미 판정
    - CLINICALLY_RELEVANT: 감작 + 노출 시 실제 증상 유발 → 진짜 알레르기
    - SENSITIZED_ONLY: 감작은 되어 있으나 노출에도 증상 없음 → 임상적으로 무의미
    - INDETERMINATE: 노출 경험/증상 정보가 불충분하여 판정 보류
    - NOT_ASSESSED: 아직 평가하지 않음
    """
    CLINICALLY_RELEVANT = "clinically_relevant"
    SENSITIZED_ONLY = "sensitized_only"
    INDETERMINATE = "indeterminate"
    NOT_ASSESSED = "not_assessed"


class SymptomSeasonPattern(str, Enum):
    """증상의 계절 패턴"""
    PERENNIAL = "perennial"   # 연중
    SEASONAL = "seasonal"     # 계절성
    BOTH = "both"             # 연중 + 계절 악화
    NONE = "none"             # 뚜렷한 패턴 없음/무증상


# 표준 코드 목록 (UI 및 검증용)
ALLERGIC_DISEASE_OPTIONS = [
    "allergic_rhinitis",       # 알레르기 비염
    "asthma",                  # 천식
    "atopic_dermatitis",       # 아토피 피부염
    "allergic_conjunctivitis", # 알레르기 결막염
    "chronic_urticaria",       # 만성 두드러기
    "food_allergy",            # 음식 알레르기
    "anaphylaxis",             # 아나필락시스 병력
    "drug_allergy",            # 약물 알레르기
    "sinusitis",               # 부비동염
    "none",                    # 없음
]

MEDICATION_OPTIONS = [
    "antihistamine",     # 항히스타민제 (검사 전 5-7일 중단 필요)
    "nasal_steroid",     # 비강 스테로이드
    "inhaled_steroid",   # 흡입 스테로이드
    "leukotriene",       # 류코트리엔 조절제
    "systemic_steroid",  # 전신 스테로이드
    "immunotherapy",     # 면역치료(SCIT/SLIT)
    "biologics",         # 생물학적 제제
    "decongestant",      # 충혈완화제
    "none",              # 복용 안 함
]

ORGAN_SYSTEM_OPTIONS = [
    "nasal",         # 코 (재채기/콧물/코막힘)
    "ocular",        # 눈 (가려움/충혈/눈물)
    "lower_airway",  # 하기도 (기침/천명/호흡곤란)
    "skin",          # 피부 (두드러기/가려움/습진)
    "gi",            # 소화기 (복통/설사/구토)
    "systemic",      # 전신 (아나필락시스/어지럼)
]


class ScreeningProfile(BaseModel):
    """검사 전/기저 스크리닝 정보 (알레르기 질환력, 약제, 증상 패턴)"""
    # 기저 알레르기 질환
    allergic_diseases: List[str] = Field(default_factory=list)
    disease_other: Optional[str] = None
    # 약제 사용 (SPT 해석에 영향)
    current_medications: List[str] = Field(default_factory=list)
    medication_note: Optional[str] = None
    antihistamine_recent: Optional[bool] = Field(
        None, description="최근 5-7일 내 항히스타민제 복용 여부 (SPT 위음성 가능)"
    )
    # 증상 특성
    symptom_present: bool = Field(True, description="현재 알레르기 증상 유무")
    season_pattern: SymptomSeasonPattern = SymptomSeasonPattern.NONE
    worse_months: List[int] = Field(default_factory=list, description="증상 악화 월 (1-12)")
    organ_systems: List[str] = Field(default_factory=list)
    symptom_severity: Optional[str] = Field(None, description="mild/moderate/severe")
    perennial_symptom: Optional[bool] = Field(None, description="연중 지속 증상 여부")
    triggers_free_text: Optional[str] = Field(None, description="스스로 인지한 유발요인")
    # 음식/구강 알레르기 스크리닝
    oral_allergy_syndrome: Optional[bool] = Field(
        None, description="과일·채소·견과 섭취 시 입·입술·목 가려움/부종 (구강알레르기증후군, OAS)"
    )
    oas_foods: List[str] = Field(default_factory=list, description="OAS 유발 음식(자유 기재)")
    food_systemic_reaction: Optional[bool] = Field(
        None, description="특정 음식 섭취 후 두드러기·호흡곤란·복통 등 전신 반응"
    )
    food_reaction_foods: List[str] = Field(default_factory=list, description="전신 반응 유발 음식")
    notes: Optional[str] = None

    @validator('worse_months', each_item=True)
    def _valid_month(cls, v):
        if v < 1 or v > 12:
            raise ValueError("월은 1-12 사이여야 합니다")
        return v


class AllergenAssessment(BaseModel):
    """개별 양성 알레르겐에 대한 임상적 의미 평가"""
    allergen_name: str
    korean_name: Optional[str] = None
    category: Optional[str] = None
    # 검사 수치
    test_value: Optional[float] = None
    test_unit: Optional[str] = None
    class_value: Optional[Union[int, str]] = None
    strength: Optional[str] = Field(None, description="weak/moderate/strong 감작 강도")
    # 지식베이스 backdata
    kb: Optional[Dict[str, Any]] = Field(None, description="알레르겐 지식베이스 항목")
    # 감별 질문 응답 (probe_id -> yes/no/unsure)
    answers: Dict[str, str] = Field(default_factory=dict)
    # 자동 계산: 알레르겐 시즌과 환자 악화 시즌의 겹침
    season_overlap: Optional[bool] = None
    # 판정
    relevance: ClinicalRelevance = ClinicalRelevance.NOT_ASSESSED
    rationale_ko: Optional[str] = None


class RelevanceAssessmentResult(BaseModel):
    """전체 양성 알레르겐 감별 결과"""
    patient_name: Optional[str] = None
    test_date: Optional[str] = None
    assessments: List[AllergenAssessment] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    def by_relevance(self, relevance: ClinicalRelevance) -> List[AllergenAssessment]:
        return [a for a in self.assessments if a.relevance == relevance]


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
    
    elif test_type in (TestType.MAST, TestType.UNICAP):
        # MAST/UniCAP 양성 기준: Class 1 이상 또는 0.35 kU/L 이상
        if class_value is not None and str(class_value).strip() != "":
            token = str(class_value).strip().upper()
            try:
                c = int(float(token))
                if c >= 1:
                    return InterpretationType.POSITIVE
                if c == 0:
                    return InterpretationType.NEGATIVE
            except (ValueError, TypeError):
                if token in ("P", "POSITIVE", "양성"):
                    return InterpretationType.POSITIVE
                if token in ("N", "NEGATIVE", "음성"):
                    return InterpretationType.NEGATIVE

        if value is not None:
            if value >= 0.35:
                return InterpretationType.POSITIVE
            else:
                return InterpretationType.NEGATIVE

    return InterpretationType.UNKNOWN
