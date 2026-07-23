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

    @staticmethod
    def _parse_size_text(size_text: Optional[str]):
        """'3x4'·'3 x 4'·'3*4' → (major, minor) mm. 실패 시 (None, None)."""
        if not size_text:
            return None, None
        import re as _re
        nums = _re.findall(r"\d+(?:\.\d+)?", str(size_text))
        if len(nums) >= 2:
            a, b = float(nums[0]), float(nums[1])
            return max(a, b), min(a, b)
        if len(nums) == 1:
            return float(nums[0]), None
        return None, None

    def _spt_measurement_components(self, ar: AllergenResult,
                                    histamine_mean_mm: Optional[float] = None) -> List[Dict[str, Any]]:
        """SPT 측정을 CDM qualifier concept 기반 Observation.component 로 세분화(item5).
        장축(major)·단축(minor)·평균(average)·A/H비(ah_ratio). 명시값이 없으면
        size_text/평균/히스타민 대조로 파생한다."""
        p_major, p_minor = self._parse_size_text(ar.size_text)
        major = ar.wheal_major_mm if ar.wheal_major_mm is not None else p_major
        minor = ar.wheal_minor_mm if ar.wheal_minor_mm is not None else p_minor
        mean = ar.mean_mm
        if mean is None and major is not None and minor is not None:
            mean = round((major + minor) / 2, 2)
        ah = ar.ah_ratio
        if ah is None and mean is not None and histamine_mean_mm:
            try:
                if histamine_mean_mm > 0:
                    ah = round(mean / histamine_mean_mm, 2)
            except (TypeError, ZeroDivisionError):
                ah = None

        def _mm(qkey, value):
            coding = self.allergen_mapper.get_qualifier_coding(qkey)
            if value is None or coding is None:
                return None
            return {
                "code": {"coding": [coding], "text": coding.get("display", qkey)},
                "valueQuantity": {"value": value, "unit": "mm",
                                  "system": "http://unitsofmeasure.org", "code": "mm"},
            }

        comps: List[Dict[str, Any]] = []
        for qkey, val in (("major_axis", major), ("minor_axis", minor), ("average", mean)):
            c = _mm(qkey, val)
            if c:
                comps.append(c)
        # A/H 비는 무차원(비율)
        ah_coding = self.allergen_mapper.get_qualifier_coding("ah_ratio")
        if ah is not None and ah_coding is not None:
            comps.append({
                "code": {"coding": [ah_coding], "text": ah_coding.get("display", "A/H Ratio")},
                "valueQuantity": {"value": ah, "unit": "ratio",
                                  "system": "http://unitsofmeasure.org", "code": "1"},
            })
        return comps

    def create_observation(
        self,
        allergen_result: AllergenResult,
        patient_id: str,
        test_date: Optional[str] = None,
        test_type: TestType = TestType.SPT,
        histamine_mean_mm: Optional[float] = None
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
            if test_type == TestType.SPT:
                # SPT 대표값 = 팽진 평균(mean). 명시값이 없으면 size_text(장×단)에서 파생
                spt_mean = allergen_result.mean_mm
                if spt_mean is None:
                    mj, mn = self._parse_size_text(allergen_result.size_text)
                    if mj is not None and mn is not None:
                        spt_mean = round((mj + mn) / 2, 2)
                if spt_mean is not None:
                    observation["valueQuantity"] = {
                        "value": spt_mean,
                        "unit": "mm",
                        "system": "http://unitsofmeasure.org",
                        "code": "mm"
                    }

            elif test_type in (TestType.MAST, TestType.UNICAP) and allergen_result.value is not None:
                # MAST/UniCAP 결과: IgE value in kU/L
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
            
            # 알레르겐 정보를 component로 추가 (CDM 기매핑 우선 → concept_name 을 display 로)
            coding = self.allergen_mapper.get_coding(
                allergen_result.allergen_name, allergen_result.korean_name or "")
            components: List[Dict[str, Any]] = []
            if coding:
                components.append({
                    "code": {
                        "coding": [coding],
                        "text": f"{allergen_result.allergen_name} ({allergen_result.korean_name})"
                    }
                })
            # SPT: 측정을 CDM qualifier concept 기반 component 로 세분화(장축·단축·평균·A/H비) — item5
            if test_type == TestType.SPT:
                method_coding = self.allergen_mapper.get_spt_method_coding()
                if method_coding:
                    observation["method"] = {"coding": [method_coding],
                                             "text": method_coding.get("display")}
                components.extend(
                    self._spt_measurement_components(allergen_result, histamine_mean_mm))
            if components:
                observation["component"] = components

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
            hist = getattr(ocr_result.patient, "histamine_mean_mm", None)
            for allergen_result in ocr_result.results:
                observation = self.create_observation(
                    allergen_result=allergen_result,
                    patient_id=patient_id,
                    test_date=ocr_result.patient.test_date,
                    test_type=ocr_result.test_type,
                    histamine_mean_mm=hist
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
            
            # Code (allergen) — CDM 기매핑 우선
            allergy["code"] = {}
            coding = self.allergen_mapper.get_coding(allergen_name, korean_name or "")
            if coding:
                allergy["code"]["coding"] = [coding]
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
    
    # =====================================================================
    # v2: 감별(relevance) 결과 기반 통합 번들 — Observation(전체) + AllergyIntolerance
    # =====================================================================
    def _category_fhir(self, cat: str) -> str:
        """내부 카테고리 → FHIR AllergyIntolerance.category (food|medication|environment|biologic)"""
        cat = (cat or "").lower()
        if cat == "food":
            return "food"
        return "environment"

    def _lookup_mapping(self, name: str, korean: str = ""):
        """SNOMED 매핑 조회 — 실패 시 'pollen/dander/protein' 접미사 제거·한글명으로 재시도."""
        m = self.allergen_mapper.find_allergen(name)
        if m:
            return m
        import re
        stripped = re.sub(r"\b(pollen|dander|epithelium|protein|mix|allergen)\b", "", name, flags=re.I).strip()
        if stripped and stripped.lower() != (name or "").lower():
            m = self.allergen_mapper.find_allergen(stripped)
            if m:
                return m
        if korean:
            m = self.allergen_mapper.find_allergen(korean)
        return m

    def build_allergy_intolerance_from_assessment(
        self, a: Any, patient_id: str, patient_name: Optional[str],
        recorded_date: Optional[str], high_criticality: bool = False,
        extra_note: Optional[str] = None,
    ) -> Dict[str, Any]:
        """AllergenAssessment(감별 결과)를 AllergyIntolerance 로 변환.
        - verificationStatus: confirmed(임상적 유의) / unconfirmed(감작만·미확정=의심)
        - criticality: high(전신·아나필락시스·강감작) / low / unable-to-assess(미확정)
        - clinicalStatus: active
        """
        from models.schemas import ClinicalRelevance
        name = a.allergen_name
        korean = a.korean_name
        coding = self.allergen_mapper.get_coding(name, korean or "")  # CDM 기매핑 우선
        cat = self._category_fhir(str(a.category))

        rel = a.relevance
        severity = getattr(a, "severity", None)
        has_symptoms = bool(getattr(a, "reported_symptoms", None))
        if rel == ClinicalRelevance.CLINICALLY_RELEVANT:
            verification = ("confirmed", "Confirmed")
        else:  # sensitized_only / indeterminate → 의심(미확인)
            verification = ("unconfirmed", "Unconfirmed")

        # criticality: 중증도 기반. 증상이 없었던(감작만) 알러젠은 low, 미확정은 평가불가.
        # 중증·아나필락시스만 high 로 격상한다.
        if rel == ClinicalRelevance.INDETERMINATE:
            criticality = "unable-to-assess"
        elif severity in ("severe", "anaphylaxis") or high_criticality:
            criticality = "high"
        else:  # relevant(경증·중등증) 또는 sensitized_only(무증상)
            criticality = "low"

        coding_text = f"{name}" + (f" ({korean})" if korean and korean != name else "")
        res: Dict[str, Any] = {
            "resourceType": "AllergyIntolerance",
            "id": f"allergy-{uuid4().hex[:8]}",
            "clinicalStatus": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "code": "active", "display": "Active"}]},
            "verificationStatus": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                "code": verification[0], "display": verification[1]}]},
            "type": "allergy",
            "category": [cat],
            "criticality": criticality,
            "code": {"coding": ([coding] if coding else []),
                     "text": coding_text},
            "patient": {"reference": f"Patient/{patient_id}",
                        "display": patient_name or patient_id},
        }
        if recorded_date:
            res["recordedDate"] = recorded_date

        # ── reaction.manifestation.text = 문진에서 확인된 실제 증상(노출 시 발현) ──
        # ── note.text = 맞춤리포트·카드뉴스의 해당 알러젠 코멘트(감별근거·생활사·노출·회피·면역치료·OAS) ──
        kb = a.kb or {}
        notes = []
        if a.rationale_ko:
            notes.append(a.rationale_ko)
        if rel != ClinicalRelevance.CLINICALLY_RELEVANT:
            notes.append("검사 양성이나 임상적 유발은 미확인(감작/의심) 상태 — verificationStatus=unconfirmed.")
        # OAS: 이 꽃가루 감작이 원인이 되어 아래 음식에 교차반응이 나타남을 관련 꽃가루 note 에 명시
        if getattr(a, "oas_foods", None):
            notes.append(
                f"구강알레르기증후군(OAS): {name} 감작으로 인해 {', '.join(a.oas_foods)} 섭취 시 "
                f"입·목 교차반응이 나타납니다(생것 주의, 익히면 대개 완화). 해당 음식은 별도 AllergyIntolerance 로 함께 기록됩니다.")
        # 성분(component) 교차반응 — 증상이 확인된 항목: 별도 AllergyIntolerance 로 기록됨을 명시
        if getattr(a, "crossreact_confirmed", None):
            notes.append(
                f"성분 교차반응(확인됨): {', '.join(a.crossreact_confirmed)} — 실제 섭취 시 증상이 보고되어 "
                f"별도 AllergyIntolerance 로 함께 기록됩니다.")
        # 성분(component) 교차반응 — 가능성만(미확인): FHIR text 에 간단히 언급(별도 항목 생성하지 않음)
        if getattr(a, "crossreact_risk", None):
            notes.append(
                f"성분 교차반응 가능(미확인): {', '.join(a.crossreact_risk)} 등과 성분을 공유해 교차반응 가능성이 있으나 "
                f"증상은 확인되지 않았습니다. 섭취 시 증상 발현 여부에 주의하세요.")
        if kb.get("season_label_ko"):
            notes.append(f"주요 시기: {kb['season_label_ko']}.")
        if kb.get("exposure_environment_ko"):
            notes.append(f"주요 노출 환경: {kb['exposure_environment_ko']}")
        av = kb.get("avoidance_control_ko") or []
        if av:
            notes.append("회피·관리: " + "; ".join(av[:3]) + ".")
        try:
            from services.knowledge_service import get_knowledge_service
            imt = get_knowledge_service().immunotherapy_info(a.category, a.allergen_name)
            if imt.get("eligible"):
                notes.append("면역치료(SCIT/SLIT) 고려 가능 대상.")
        except Exception:
            pass
        if extra_note:
            notes.append(extra_note)
        if notes:
            res["note"] = [{"text": " ".join(notes)}]

        # 문진에서 실제 증상이 확인된 경우에만 reaction 을 기록(감작만/미확정은 reaction 없음)
        manifestations = getattr(a, "reported_symptoms", None) or []
        if rel == ClinicalRelevance.CLINICALLY_RELEVANT and manifestations:
            reaction = {
                "manifestation": [{"text": m} for m in manifestations],
                "severity": self._fhir_reaction_severity(severity),
                "description": "환자 문진에서 확인된 노출 시 증상",
            }
            if severity == "anaphylaxis":
                reaction["manifestation"].append({"text": "아나필락시스 병력(응급)"})
            res["reaction"] = [reaction]
        return res

    @staticmethod
    def _fhir_reaction_severity(severity: Optional[str]) -> str:
        """내부 중증도 → FHIR reaction.severity(mild|moderate|severe). 아나필락시스는 severe."""
        return {"mild": "mild", "moderate": "moderate",
                "severe": "severe", "anaphylaxis": "severe"}.get(severity or "", "moderate")

    def build_bundles_from_relevance(
        self, ocr_result: OCRResult, relevance_result: Any,
        screening: Any = None, oas_foods: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """검사 전체는 Observation 으로, 양성/의심 알러젠은 AllergyIntolerance 로 매핑."""
        from models.schemas import ClinicalRelevance
        patient_id = (ocr_result.patient.name or f"patient-{uuid4().hex[:6]}").replace(" ", "_")
        patient_name = ocr_result.patient.name
        recorded = ocr_result.patient.test_date

        # 고위험(criticality=high) 신호: 아나필락시스/전신 병력
        high = False
        food_systemic = False
        if screening is not None:
            diseases = getattr(screening, "allergic_diseases", []) or []
            organs = getattr(screening, "organ_systems", []) or []
            food_systemic = bool(getattr(screening, "food_systemic_reaction", False))
            if "anaphylaxis" in diseases or "systemic" in organs or food_systemic:
                high = True

        # 1) Observation — 모든 결과(양성+음성) + 검사기관 performer
        obs_bundle = self.create_observation_bundle(ocr_result, patient_id=patient_id)
        facility = getattr(ocr_result.patient, "facility", None)
        if facility:
            for entry in obs_bundle.get("entry", []):
                entry["resource"]["performer"] = [{"display": facility}]

        # 2) AllergyIntolerance — 감별 대상(양성) 전부 + 교차반응 음식
        allergy_bundle = {
            "resourceType": "Bundle", "id": f"bundle-allergy-{uuid4().hex[:8]}",
            "type": "collection", "entry": []
        }
        for a in relevance_result.assessments:
            ai = self.build_allergy_intolerance_from_assessment(
                a, patient_id, patient_name, recorded, high_criticality=high)
            allergy_bundle["entry"].append({"resource": ai})

        # 교차반응 음식 알러젠 추가 — 설문에서 증상이 확인되었으므로 confirmed + SNOMED 코딩
        for f in (oas_foods or []):
            source = f.get("source", "pollen")
            severity = f.get("severity", "oral")  # oral / systemic / anaphylaxis
            en, ko = f.get("en"), f.get("ko")
            # 교차반응 항원도 SNOMED(CDM 기매핑) 코딩
            coding = self.allergen_mapper.get_coding(en or "", ko or "")
            # 증상 표현(reaction.manifestation) + 중증도
            if severity == "anaphylaxis":
                sym_txt, fhir_sev, crit = "섭취 시 아나필락시스(호흡곤란·전신 두드러기·어지럼)", "severe", "high"
            elif severity == "systemic":
                sym_txt, fhir_sev, crit = "섭취 시 전신 두드러기 등 전신 반응", "severe", "high"
            else:  # oral
                sym_txt, fhir_sev, crit = "섭취 시 입·입술·목 가려움/부종(국소)", "mild", "low"
            if source == "mite_tropomyosin":
                trigger = "집먼지진드기(트로포마이오신 교차반응)"
                note = (f"교차반응 원인 항원: {trigger}. 집먼지진드기와 갑각류는 공통 단백질(트로포마이오신)로 "
                        f"교차반응하여, 갑각류 검사가 없거나 음성이어도 새우·게 섭취 시 증상이 나타날 수 있습니다. "
                        f"이번 반응은 이 교차반응으로 인해 발생했습니다.")
                manifestation = f"새우·게(갑각류) {sym_txt}"
            elif source == "component":
                trigger = f.get("trigger") or "교차반응 항원"
                note = (f"교차반응 원인 항원: {trigger}. {trigger}와(과) 공통 단백질 성분을 공유해 교차반응하며, "
                        f"{ko or en} 섭취 시 증상이 발생했습니다(성분 기반 교차반응). "
                        f"열·소화에 안정한 성분은 조리해도 반응이 남을 수 있어 주의가 필요합니다.")
                manifestation = f"{ko or en} {sym_txt}(교차반응)"
            else:
                pollens = ", ".join(f.get("pollens", [])) or "관련 꽃가루"
                trigger = pollens
                note = (f"교차반응 원인 항원: {trigger}. 위 꽃가루 감작과의 교차반응(구강알레르기증후군)으로 인해 "
                        f"{ko or en} 섭취 시 증상이 발생했습니다. 대개 생것에서 증상이 나타나고 익히면 완화되지만, "
                        f"전신 반응 병력이 있으면 전문의 평가가 필요합니다.")
                manifestation = f"{ko or en} {sym_txt}(구강알레르기증후군)"
            ai = {
                "resourceType": "AllergyIntolerance",
                "id": f"allergy-{uuid4().hex[:8]}",
                "clinicalStatus": {"coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    "code": "active", "display": "Active"}]},
                # 설문을 통해 증상이 확인되었으므로 confirmed
                "verificationStatus": {"coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
                    "code": "confirmed", "display": "Confirmed"}]},
                "type": "allergy", "category": ["food"],
                "criticality": crit,
                "code": {"coding": ([coding] if coding else []),
                         "text": ko or en},
                "patient": {"reference": f"Patient/{patient_id}", "display": patient_name or patient_id},
                "reaction": [{
                    "manifestation": [{"text": manifestation}],
                    "severity": fhir_sev,
                    "description": "환자 문진에서 확인된 교차반응 증상",
                }],
                "note": [{"text": note}],
            }
            if recorded:
                ai["recordedDate"] = recorded
            allergy_bundle["entry"].append({"resource": ai})

        return {"observation_bundle": obs_bundle, "allergy_intolerance_bundle": allergy_bundle}

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