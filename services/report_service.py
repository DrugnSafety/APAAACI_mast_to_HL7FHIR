"""
Report Service
GPT 기반 맞춤형 알레르기 관리 리포트 생성 서비스
"""

import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
from pathlib import Path
from openai import OpenAI

from config.settings import settings
from models.schemas import (
    SymptomFeedback, AllergyManagementReport,
    AllergyManagementSection, FHIRBundle,
    RelevanceAssessmentResult, AllergenAssessment, ClinicalRelevance,
    ScreeningProfile,
)
from services.screening_service import get_screening_service
from services.knowledge_service import normalize_category, get_knowledge_service

# 로거 설정
logger = logging.getLogger(__name__)

# 교차반응 증상 범위 순위(강한 것 우선 표기)
_CR_SEV_RANK = {"oral": 1, "systemic": 2, "anaphylaxis": 3}


class ReportService:
    """맞춤형 알레르기 관리 리포트 생성 서비스"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: OpenAI API 키 (None일 경우 settings에서 가져옴)
        """
        self.api_key = api_key or settings.openai_api_key
        # API 키가 없어도 결정론적 리포트(build_patient_report_markdown)는 동작해야 하므로
        # 여기서 예외를 던지지 않는다. LLM 다듬기 단계에서만 client 가 필요하다.
        if self.api_key and self.api_key != "your_openai_api_key_here":
            try:
                self.client = OpenAI(api_key=self.api_key)
            except Exception as e:
                logger.warning(f"OpenAI 클라이언트 초기화 실패(결정론적 리포트만 사용): {e}")
                self.client = None
        else:
            self.client = None
        self._load_report_template()
    
    def _load_report_template(self):
        """리포트 템플릿 로드"""
        try:
            template_path = settings.get_report_template_path()
            with open(template_path, 'r', encoding='utf-8') as f:
                content = f.read()
                # JSON 형식으로 파싱 시도
                try:
                    self.report_template = json.loads(content)
                except:
                    # JSON이 아닌 경우 텍스트로 사용
                    self.report_template = [
                        {
                            "role": "system",
                            "content": content
                        }
                    ]
            logger.info("리포트 템플릿 로드 완료")
        except Exception as e:
            logger.error(f"리포트 템플릿 로드 실패: {e}")
            self.report_template = self._get_default_template()
    
    def _get_default_template(self) -> List[Dict[str, str]]:
        """기본 리포트 템플릿 - 상세한 구조와 형식"""
        return [
            {
                "role": "system",
                "content": """You are an allergy specialist creating a personalized allergy management plan for the patient.

**Writing Principles:**
1. Write in English with medical terminology clearly explained
2. Use a patient-friendly tone that is easy to understand
3. Provide specific and actionable advice
4. Consider the patient's age and gender for personalized content
5. Summarize test results briefly and detail management strategies

**Report Structure (Please follow this format):**

# Personalized Allergy Management Plan for {Patient Name}

**Patient Information:** {Name} ({Age} years, {Gender})  
**Test Date:** {Test Date}  
**Report Date:** {Today's Date}  

---

## 0️⃣ Executive Summary
- Summarize main allergy causes in 1-2 sentences
- Present the most important management points
- Example: "Allergies to house dust mites, birch pollen, and animal dander have been confirmed."

---

## 1️⃣ Key Allergen Overview

| Category | Allergen | Type | Clinical Significance | Importance |
|----------|----------|------|----------------------|------------|
| **Mites** | D.farinae, D.pteronyssinus | Indoor | Year-round rhinitis, asthma risk | 🔴 High |
| **Tree Pollen** | Birch | Outdoor (Spring) | Cross-reactivity possible | 🟠 Medium |
| **Animals** | Dog, Cat | Indoor | Rhinitis, conjunctivitis trigger | 🟠 Medium |

---

## 2️⃣ Environmental Management Plan

### 🏠 Indoor Environment
**Dust Mite Control:**
- Bedding: Use mite-proof covers, wash weekly in hot water (60°C or higher)
- Humidity control: Maintain indoor humidity at 40-50% (use hygrometer)
- Cleaning: Vacuum 2-3 times per week with HEPA filter vacuum
- Carpets/Curtains: Remove if possible, otherwise wash regularly

**Animal Allergen Management:**
- Bedroom restriction: Keep pets out of bedroom
- Air purifier: Run HEPA filter air purifier 24/7
- Regular bathing: Bathe pets at least once weekly

### 🌳 Outdoor Environment (Pollen Season)
**Spring Management (March-May):**
- Going out: Wear mask and sunglasses
- After returning: Shower immediately and change clothes
- Windows: Keep windows closed during morning hours (high pollen concentration)
- Weather check: Avoid going out on windy days

---

## 3️⃣ Medical Management Plan

### 💊 Medication (Use after consulting with doctor)

| Symptom | First-line | Second-line | Precautions |
|---------|------------|-------------|-------------|
| Runny nose/Sneezing | Oral antihistamines | Nasal steroid spray | Watch for drowsiness |
| Nasal congestion | Nasal steroids | Leukotriene modifiers | Regular use needed |
| Eye itching | Antihistamine eye drops | Mast cell stabilizers | Remove contact lenses before use |

### 💉 Immunotherapy Considerations
- **Target:** House dust mite allergy (Class 3 or higher)
- **Methods:** SCIT (subcutaneous injection) or SLIT (sublingual tablets)
- **Duration:** 3-5 years continuous treatment
- **Effectiveness:** 60-80% symptom improvement, asthma prevention
- **Side effects:** Local swelling/redness (rarely systemic reactions)

---

## 4️⃣ 생활습관 및 예방 (Lifestyle & Prevention)

### 🧘‍♀️ 일상 관리
- **운동:** 실내 운동 권장 (꽃가루 시즌)
- **식단:** 항산화 식품 섭취 증가 (비타민 C, E 풍부 식품)
- **스트레스:** 명상, 요가 등으로 스트레스 관리
- **수면:** 충분한 수면 (7-8시간)

### 🏠 가정 내 체크리스트
□ 공기청정기 필터 월 1회 교체  
□ 침구 주 1회 온수 세탁  
□ 습도계로 실내 습도 확인  
□ 진드기 방지 커버 3개월마다 세탁  
□ 에어컨 필터 월 1회 청소  

---

## 5️⃣ 추적 관리 계획 (Follow-up Plan)

| 시기 | 검사/상담 내용 | 목적 |
|------|----------------|------|
| 3개월 후 | 증상 평가 + 약물 조정 | 치료 반응 확인 |
| 6개월 후 | 폐기능 검사 | 천식 발생 모니터링 |
| 12개월 후 | 알레르기 재검사 | 감작 변화 확인 |
| 응급 시 | 즉시 내원 | 호흡곤란, 아나필락시스 |

### 📱 증상 일지 작성법
- 매일 증상 점수 기록 (0-10점)
- 약물 복용 여부 기록
- 특별한 노출 상황 메모
- 월별로 의사와 공유

---

## 6️⃣ 요약 및 권고사항 (Summary & Recommendations)

### ✅ 핵심 실천 사항 (Top 3)
1. **집먼지진드기 관리가 최우선** - 침구 관리와 습도 조절
2. **꽃가루 시즌 대비** - 3-5월 외출 시 주의
3. **규칙적인 약물 복용** - 증상 있을 때만 X, 예방적 사용 O

### 💪 격려 메시지
{환자이름}님, 알레르기는 충분히 관리 가능한 질환입니다. 
꾸준한 환경 관리와 적절한 치료로 삶의 질을 크게 개선할 수 있습니다.
작은 실천부터 시작해보세요. 항상 응원하겠습니다!

---

## 📚 참고 자료
- 대한천식알레르기학회 환자 교육 자료
- EAACI 알레르기 관리 가이드라인 (2023)
- WHO 알레르기 예방 권고사항"""
            }
        ]
    
    def generate_report(
        self,
        symptom_feedback: SymptomFeedback,
        fhir_allergy_bundle: Optional[Dict[str, Any]] = None
    ) -> AllergyManagementReport:
        """
        알레르기 관리 리포트 생성
        
        Args:
            symptom_feedback: 증상 피드백 결과
            fhir_allergy_bundle: FHIR AllergyIntolerance Bundle
            
        Returns:
            AllergyManagementReport 인스턴스
        """
        try:
            # 리포트 생성을 위한 프롬프트 구성
            prompt = self._build_report_prompt(symptom_feedback, fhir_allergy_bundle)
            
            # o1 모델용 메시지 구성 (system role을 지원하지 않음)
            # system 메시지를 user 메시지로 변환
            messages = []
            system_content = ""
            
            # 템플릿에서 system 메시지 추출 및 변환
            for msg in self.report_template:
                if msg["role"] == "system":
                    system_content += msg["content"] + "\n\n"
                else:
                    messages.append(msg)
            
            # system 프롬프트와 사용자 프롬프트를 하나의 user 메시지로 결합
            if system_content:
                combined_prompt = f"""[System Instructions]
{system_content}

[User Request]
{prompt}"""
            else:
                combined_prompt = prompt
                
            messages.append({
                "role": "user",
                "content": combined_prompt
            })
            
            # 고급 리포트 생성 모델 사용
            logger.info(f"리포트 생성 중 - 모델: {settings.openai_report_model}")
            logger.info(f"Temperature: {settings.report_temperature}, Max Tokens: {settings.report_max_tokens}")
            response = self.client.chat.completions.create(
                model=settings.openai_report_model,  # gpt-4o for detailed report
                messages=messages,
                temperature=settings.report_temperature,  # 0.8 for creativity
                max_tokens=settings.report_max_tokens  # 4000 for longer reports
            )
            
            markdown_content = response.choices[0].message.content
            
            # 구조화된 리포트 생성
            report = self._parse_markdown_to_report(
                markdown_content,
                symptom_feedback
            )
            
            return report
            
        except Exception as e:
            logger.error(f"리포트 생성 실패: {e}")
            raise
    
    def _build_report_prompt(
        self,
        feedback: SymptomFeedback,
        fhir_bundle: Optional[Dict[str, Any]] = None
    ) -> str:
        """리포트 생성 프롬프트 구성"""
        # 환자 정보
        patient_info = f"""환자 정보:
- 이름: {feedback.patient_name or '미제공'}
- 나이: {feedback.patient_age or '미제공'}세
- 성별: {self._format_gender(feedback.patient_gender)}
- 검사일: {feedback.test_date}
"""
        
        # 알레르겐 정보
        symptomatic = feedback.exposure_feedback.get("symptomatic", [])
        asymptomatic = feedback.exposure_feedback.get("asymptomatic", [])
        unknown = feedback.exposure_feedback.get("unknown_exposure", [])
        
        allergen_info = f"""
알레르기 검사 결과:

증상이 있는 알레르겐 (주의 필요):
{', '.join(symptomatic) if symptomatic else '없음'}

노출되었지만 증상이 없는 알레르겐:
{', '.join(asymptomatic) if asymptomatic else '없음'}

노출 경험이 없는 알레르겐:
{', '.join(unknown) if unknown else '없음'}
"""
        
        # FHIR 정보 (있는 경우)
        fhir_info = ""
        if fhir_bundle:
            entry_count = len(fhir_bundle.get("entry", []))
            fhir_info = f"\nFHIR AllergyIntolerance 리소스: {entry_count}개 생성됨"
        
        prompt = f"""{patient_info}
{allergen_info}
{fhir_info}

위 정보를 바탕으로 환자를 위한 맞춤형 알레르기 관리 리포트를 작성해주세요.

요구사항:
1. 환자의 나이와 성별을 고려한 맞춤형 조언
2. 증상이 있는 알레르겐을 중심으로 한 관리 계획
3. 실생활에서 실천 가능한 구체적인 방법
4. 따뜻하고 격려하는 어조
5. Markdown 형식으로 작성 (제목은 #, ##, ### 사용)
"""
        
        return prompt
    
    def _format_gender(self, gender: Optional[str]) -> str:
        """성별 포맷팅"""
        if not gender:
            return "미제공"
        if gender in ["M", "남"]:
            return "남성"
        elif gender in ["F", "여"]:
            return "여성"
        return gender
    
    def _parse_markdown_to_report(
        self,
        markdown_content: str,
        feedback: SymptomFeedback
    ) -> AllergyManagementReport:
        """마크다운을 구조화된 리포트로 파싱"""
        # 섹션 분리
        sections = []
        current_section = None
        current_content = []
        
        lines = markdown_content.split('\n')
        for line in lines:
            if line.startswith('##'):
                # 새 섹션 시작
                if current_section:
                    sections.append(AllergyManagementSection(
                        title=current_section,
                        content=current_content,
                        icon=self._get_section_icon(current_section)
                    ))
                current_section = line.replace('##', '').strip()
                current_content = []
            elif line.strip():
                current_content.append(line.strip())
        
        # 마지막 섹션 추가
        if current_section:
            sections.append(AllergyManagementSection(
                title=current_section,
                content=current_content,
                icon=self._get_section_icon(current_section)
            ))
        
        # 리포트 객체 생성
        report = AllergyManagementReport(
            patient_name=feedback.patient_name or "환자",
            patient_age=feedback.patient_age,
            patient_gender=feedback.patient_gender,
            test_date=feedback.test_date,
            key_allergens=feedback.exposure_feedback.get("symptomatic", []),
            sections=sections,
            markdown_content=markdown_content
        )
        
        return report
    
    def _get_section_icon(self, title: str) -> str:
        """섹션 제목에 따른 아이콘 반환"""
        icon_map = {
            "핵심": "🩺",
            "환경": "🧹",
            "의학": "💊",
            "생활": "🧘",
            "추적": "📊",
            "요약": "🔍"
        }
        
        for keyword, icon in icon_map.items():
            if keyword in title:
                return icon
        return "📋"
    
    def save_report_to_file(
        self,
        report: AllergyManagementReport,
        output_dir: Optional[Path] = None
    ) -> Path:
        """
        리포트를 파일로 저장
        
        Args:
            report: 알레르기 관리 리포트
            output_dir: 출력 디렉토리 (None일 경우 settings 사용)
            
        Returns:
            저장된 파일 경로
        """
        try:
            if not output_dir:
                output_dir = settings.output_dir
            
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"allergy_report_{report.patient_name}_{timestamp}.md"
            filepath = output_dir / filename
            
            # 마크다운 저장
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(report.markdown_content or self._generate_markdown(report))
            
            logger.info(f"리포트 저장 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"리포트 저장 실패: {e}")
            raise
    
    def _generate_markdown(self, report: AllergyManagementReport) -> str:
        """리포트 객체에서 마크다운 생성"""
        markdown = f"""# 맞춤형 알레르기 관리 플랜

**환자명:** {report.patient_name}  
**검사일:** {report.test_date}  
"""
        
        if report.patient_age:
            markdown += f"**나이:** {report.patient_age}세  \n"
        if report.patient_gender:
            markdown += f"**성별:** {self._format_gender(report.patient_gender)}  \n"
        
        markdown += "\n---\n\n"
        
        # 핵심 알레르겐
        if report.key_allergens:
            markdown += f"**핵심 알레르겐:** {', '.join(report.key_allergens)}\n\n"
        
        # 각 섹션
        for section in report.sections:
            markdown += f"\n## {section.icon} {section.title}\n\n"
            for content in section.content:
                if content.startswith('-') or content.startswith('•'):
                    markdown += f"{content}\n"
                else:
                    markdown += f"{content}\n\n"
        
        # 푸터
        markdown += f"""
---

*이 리포트는 {datetime.now().strftime('%Y년 %m월 %d일')}에 생성되었습니다.*
*AI 기반 분석 결과로 참고용으로만 사용하시고, 정확한 진단과 치료는 의료진과 상담하세요.*
"""
        
        return markdown
    
    def generate_summary_statistics(
        self,
        feedback: SymptomFeedback
    ) -> Dict[str, Any]:
        """
        리포트 요약 통계 생성
        
        Args:
            feedback: 증상 피드백
            
        Returns:
            통계 딕셔너리
        """
        symptomatic = feedback.exposure_feedback.get("symptomatic", [])
        asymptomatic = feedback.exposure_feedback.get("asymptomatic", [])
        unknown = feedback.exposure_feedback.get("unknown_exposure", [])
        
        total = len(symptomatic) + len(asymptomatic) + len(unknown)
        
        return {
            "total_allergens": total,
            "symptomatic_count": len(symptomatic),
            "asymptomatic_count": len(asymptomatic),
            "unknown_count": len(unknown),
            "symptomatic_rate": len(symptomatic) / total * 100 if total > 0 else 0,
            "primary_allergens": symptomatic[:3] if symptomatic else [],
            "management_priority": "high" if len(symptomatic) >= 3 else "moderate" if len(symptomatic) >= 1 else "low"
        }


    # ==================================================================
    # 환자 맞춤형 리포트 (증상·예방 중심, 감작 vs 실제 알레르기 감별 반영)
    # ==================================================================

    _STRENGTH_KO = {"weak": "약한 양성", "moderate": "중등도 양성", "strong": "강한 양성"}
    _ONE_LINE_ACTION = {
        "mite": "침구 관리와 실내 습도 조절이 핵심",
        "animal": "해당 동물과의 접촉·침실 노출 줄이기",
        "pollen_tree": "봄철 외출·환기 관리",
        "pollen_grass": "초여름 잔디·풀밭 노출 주의",
        "pollen_weed": "가을철 야외 노출 주의",
        "mold": "실내 습도 낮추고 곰팡이 제거",
        "insect": "주방·서식처 위생 관리",
        "food": "해당 음식 섭취 주의(전신 반응 시 응급)",
    }

    # 교차반응 증상 범위 배지 — 항원 노출 중증도와 구분해서 표기
    _CR_SEVERITY_BADGE = {
        "oral": "🟢 입·목 국소", "systemic": "🔴 **전신 반응**",
        "anaphylaxis": "🚨 **아나필락시스(응급)**",
    }

    # 증상 중증도 배지(B) — 중증 이상은 눈에 띄게
    _SEVERITY_BADGE = {
        "mild": "🟢 경증", "moderate": "🟠 중등증",
        "severe": "🔴 **중증**", "anaphylaxis": "🚨 **아나필락시스(응급)**",
    }
    # 실내/실외 구분(C) — 노출 관리 방식이 근본적으로 다르다
    _ENV_GROUP = {
        "mite": "indoor", "insect": "indoor", "mold": "indoor", "animal": "indoor",
        "pollen_tree": "outdoor", "pollen_grass": "outdoor", "pollen_weed": "outdoor",
    }
    _ENV_LABEL = {"indoor": "🏠 실내 항원", "outdoor": "🌳 실외(계절성) 항원",
                  "food": "🍽️ 음식 항원", "other": "기타 항원"}

    def _severity_badge(self, a) -> str:
        return self._SEVERITY_BADGE.get(getattr(a, "severity", None) or "", "")

    def _env_group_of(self, a) -> str:
        cat = normalize_category(a.category)
        if cat == "food":
            return "food"
        return self._ENV_GROUP.get(cat, "other")

    def _display_name(self, a, detail: bool = False) -> str:
        """임상 그룹(Df/Dp 등)은 통합 라벨로 표시(A1)."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return get_clinical_group_service().label_of(a, detail=detail)
        except Exception:
            return a.korean_name or a.allergen_name

    def _collapse(self, items):
        """임상 그룹 단위로 접어 중복 서술 제거(A1). 반환: [(대표 assessment, 그룹정보)]"""
        try:
            from services.clinical_group_service import get_clinical_group_service
            cg = get_clinical_group_service()
            return [(g["members"][0][1], g) for g in cg.collapse(items)]
        except Exception:
            return [(a, None) for a in items]

    def _one_line_relevant(self, a) -> str:
        """실제 주의 알러젠 한 줄 요약 (한눈에 보기용)."""
        nm = self._display_name(a)
        cat = normalize_category(a.category)
        action = self._ONE_LINE_ACTION.get(cat, "노출 상황 관리 필요")
        season = (a.kb or {}).get("season_label_ko", "")
        seg = f" · {season}" if season and cat.startswith("pollen") else ""
        sev = self._severity_badge(a)
        sev_seg = f" · {sev}" if sev else ""
        return f"**{nm}**{seg}{sev_seg} — {action}"

    def _confirmed_food_alerts(self, relevance_result):
        """확인된 OAS·교차반응 음식을 '검사 양성 알러젠과 동급'으로 경고(C).
        음식은 우발적으로 다량 노출되기 쉬워 별도 강조가 필요하다."""
        by_food: Dict[str, Dict[str, Any]] = {}
        for a in relevance_result.assessments:
            src = self._display_name(a)
            # 음식 경고는 '교차반응 자체의 증상 범위'를 쓴다(항원 노출 증상과 별개)
            sev = getattr(a, "crossreact_severity", None)
            for f in (getattr(a, "oas_foods", None) or []) + (getattr(a, "crossreact_confirmed", None) or []):
                e = by_food.setdefault(f, {"triggers": [], "severity": sev})
                if src not in e["triggers"]:
                    e["triggers"].append(src)
                if _CR_SEV_RANK.get(sev, 0) > _CR_SEV_RANK.get(e.get("severity"), 0):
                    e["severity"] = sev
        return by_food

    def build_patient_report_markdown(
        self,
        relevance_result: "RelevanceAssessmentResult",
        patient_info: Dict[str, Any],
        screening: Optional["ScreeningProfile"] = None,
    ) -> str:
        """지식베이스+감별결과로 환자용 리포트 Markdown을 결정론적으로 구성(API 불필요)."""
        name = patient_info.get("name") or relevance_result.patient_name or "환자"
        age = patient_info.get("age")
        gender = self._format_gender(patient_info.get("gender"))
        test_date = patient_info.get("test_date") or relevance_result.test_date or "-"
        today = datetime.now().strftime("%Y-%m-%d")

        relevant = relevance_result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        sensitized = relevance_result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        indeterminate = relevance_result.by_relevance(ClinicalRelevance.INDETERMINATE)

        def names(items):
            return ", ".join(a.korean_name or a.allergen_name for a in items) or "없음"

        md: List[str] = []
        md.append(f"# 🌿 {name}님 맞춤 알레르기 검사 결과 리포트")
        info = f"**{name}**"
        if age:
            info += f" · {age}세"
        if gender and gender != "미제공":
            info += f" · {gender}"
        md.append(info + f"  \n**검사일:** {test_date}  \n**리포트 작성일:** {today}")
        md.append("\n---\n")

        # 0. 핵심 요약 (구조화)
        total = len(relevant) + len(sensitized) + len(indeterminate)
        md.append("## 0️⃣ 한눈에 보기")
        md.append(
            f"> **{name}님**은 이번 검사에서 총 **{total}개** 항목에 양성(감작)으로 나왔고, "
            f"그중 문진 결과 **실제로 증상을 일으키는 것으로 확인된 알러젠은 {len(relevant)}개**입니다."
        )
        md.append(
            "| 구분 | 개수 | 의미 | 대상 |\n"
            "|---|---|---|---|\n"
            f"| 🔴 실제 주의 | **{len(relevant)}** | 노출 시 실제 증상 유발 → 적극 관리 | {names(relevant)} |\n"
            f"| ⚪ 감작만 | {len(sensitized)} | 검사만 양성, 증상 없음 → 과도한 회피 불필요 | {names(sensitized)} |\n"
            f"| 🟡 관찰 필요 | {len(indeterminate)} | 노출·정보 부족 → 경과 관찰 | {names(indeterminate)} |"
        )
        if relevant:
            md.append("**🔴 지금 우선 관리할 알러젠 요약**")
            for a in relevant:
                md.append(f"- {self._one_line_relevant(a)}")
        # 🍽️ 확인된 교차반응·OAS 음식 — 검사 양성 알러젠과 '동급'으로 경고(C)
        alerts = self._confirmed_food_alerts(relevance_result)
        if alerts:
            md.append(
                "**🍽️ 반드시 함께 주의할 음식 (교차반응·OAS로 증상이 확인됨)** — 아래 음식은 검사에서 "
                "직접 양성으로 나온 알러젠과 **똑같은 수준으로 주의**해야 합니다. 음식은 꽃가루·진드기와 달리 "
                "**한 번에 많은 양이 몸에 들어가고, 외식·가공식품에서 모르는 사이 섭취되기 쉬워** 위험이 큽니다.")
            for food, info in alerts.items():
                sev = self._CR_SEVERITY_BADGE.get(info.get("severity") or "", "")
                sev_seg = f" · {sev}" if sev else ""
                md.append(f"- 🚫 **{food}** — {', '.join(info['triggers'])} 교차반응{sev_seg}")
            md.append(
                "  - 외식·가공식품에서는 **원재료 표시를 반드시 확인**하고, 조리 과정에서 섞여 들어갈 수 있음을 "
                "알려주세요. 전신 증상(호흡곤란·어지럼) 병력이 있으면 응급약 처방을 상의하세요.")

        # 스크리닝 요약
        if screening is not None:
            sc = get_screening_service().summarize(screening)
            if sc["diseases_ko"]:
                md.append(f"- 🩺 진단/의심 질환: {', '.join(sc['diseases_ko'])}")
            if sc["season_pattern_ko"]:
                extra = f" (악화 시기: {', '.join(sc['worse_months_ko'])})" if sc["worse_months_ko"] else ""
                md.append(f"- 📆 증상 패턴: {sc['season_pattern_ko']}{extra}")
            if sc["organ_systems_ko"]:
                md.append(f"- 👃 침범 부위: {', '.join(sc['organ_systems_ko'])}")
            for flag in sc["flags"]:
                md.append(f"- ⚠️ {flag}")

        md.append("\n---\n")

        # 교육: 감작 vs 알레르기
        md.append("## 🧭 검사 양성이 곧 알레르기는 아닙니다")
        md.append(
            "알레르기 검사에서 '양성'은 우리 몸이 그 물질에 **감작(sensitization)** 되어 있다는 뜻입니다. "
            "하지만 실제 **알레르기 질환**은 그 물질에 **노출될 때(또는 그 계절에) 증상이 반복적으로 나타날 때** 성립합니다. "
            "그래서 이 리포트는 검사 수치뿐 아니라 **‘노출 시 실제로 증상이 있었는지’**를 함께 반영하여, "
            "정말로 주의해야 할 알러젠에 집중할 수 있도록 도와드립니다."
        )
        md.append("\n---\n")

        # 1. 실제 주의 알러젠
        md.append("## 1️⃣ 🔴 실제 주의가 필요한 알러젠 (증상 유발)")
        if not relevant:
            md.append("이번 문진에서는 노출 시 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. "
                      "증상이 있을 때 어떤 상황이었는지 기록해 두면 다음 평가에 도움이 됩니다.")
        else:
            # 실내/실외/음식으로 묶어 노출 관리 방식이 같은 것끼리 설명(C)
            for env in ("indoor", "outdoor", "food", "other"):
                bucket = [a for a in relevant if self._env_group_of(a) == env]
                if not bucket:
                    continue
                md.append(f"### {self._ENV_LABEL[env]}")
                for a, _g in self._collapse(bucket):   # Df/Dp 등은 한 번만 설명(A1)
                    md.append(self._allergen_detail_md(a, detailed=True))

        md.append("\n---\n")

        # 2. 감작만 된 알러젠
        md.append("## 2️⃣ ⚪ 감작만 된 알러젠 (과도한 회피 불필요)")
        if not sensitized:
            md.append("감작만 된 항목은 없습니다.")
        else:
            md.append("아래 항목은 검사에서 양성이지만 **노출에도 증상이 없어** 현재는 임상적 의미가 낮습니다. "
                      "지나친 회피나 식이 제한은 필요하지 않으며, 새로운 증상이 생기면 재평가하세요.")
            for a, _g in self._collapse(sensitized):
                nm = self._display_name(a, detail=True)
                md.append(f"- **{nm}** — {a.rationale_ko or '노출에도 증상이 없어 감작만 된 상태로 판단됩니다.'}")

        # 3. 관찰 필요
        if indeterminate:
            md.append("\n---\n")
            md.append("## 🟡 관찰이 필요한 알러젠")
            md.append("노출 경험이 없거나 정보가 부족해 판정을 보류한 항목입니다. 노출 시 증상 발생 여부를 관찰하세요.")
            for a, _g in self._collapse(indeterminate):
                nm = self._display_name(a, detail=True)
                probes = (a.kb or {}).get("relevance_probes_ko", [])
                probe = f" (확인 포인트: {probes[0]})" if probes else ""
                md.append(f"- **{nm}** — {a.rationale_ko or '노출-증상 관계 관찰이 필요합니다.'}{probe}")

        # 교차반응 관찰 음식(원인 항원별 그룹 + 확대 경고)
        md += self._crossreact_watchlist_md(relevance_result)

        md.append("\n---\n")

        # 3. 예방·관리 플랜
        md.append("## 3️⃣ 🛡️ 나를 위한 예방·관리 플랜")
        plan = self._prevention_plan_md(relevant, screening)
        md.append(plan)

        md.append("\n---\n")

        # 4. 추적 관리
        md.append("## 4️⃣ 📅 추적 관리")
        md.append(
            "- **증상 일지**: 증상이 있는 날의 날짜·상황(장소/계절/접촉물)·심한 정도(0~10)를 기록하세요.\n"
            "- **재평가**: 증상이 새로 생기거나 변하면, 또는 6~12개월 후 담당 의료진과 재평가를 권장합니다.\n"
            "- **응급 상황**: 호흡곤란·전신 두드러기·어지럼 등 아나필락시스 의심 시 즉시 병원에 가세요."
        )

        md.append("\n---\n")
        md.append(
            f"> 💛 {name}님, 알레르기는 **원인을 정확히 알고 관리하면 충분히 조절**할 수 있습니다. "
            "정말 중요한 알러젠에 집중해 하나씩 실천해 보세요.\n\n"
            "*본 리포트는 교육용 참고 자료이며, 정확한 진단과 치료는 담당 의료진과 상담하시기 바랍니다.*"
        )
        return "\n\n".join(md)

    def build_patient_report_html_document(
        self,
        relevance_result: "RelevanceAssessmentResult",
        patient_info: Dict[str, Any],
        screening: Optional["ScreeningProfile"] = None,
    ) -> str:
        """맞춤 리포트를 인쇄(PDF)·화면 겸용의 자체 완결형 HTML 문서로 생성.
        report_design 스킬(단일 디자인 소스)로 감싸 HTML/PDF 두 버전의 일관성을 보장한다."""
        from services.report_design import render_report_document
        from services.relevance_service import RelevanceService

        md = self.build_patient_report_markdown(relevance_result, patient_info, screening)
        # 본문 마크다운 → HTML (표지·요약 타일은 문서 템플릿이 별도로 그림)
        try:
            import markdown as md_lib
            body_html = md_lib.markdown(md, extensions=["extra", "sane_lists", "nl2br"])
        except Exception:
            body_html = "<pre>" + md + "</pre>"
        # 표지 헤더에 이미 이름/검사일이 있으므로 본문 첫 H1(제목)은 중복 제거
        import re
        body_html = re.sub(r"<h1>.*?</h1>", "", body_html, count=1, flags=re.S)

        name = patient_info.get("name") or relevance_result.patient_name or "환자"
        meta = {
            "name": name,
            "age": patient_info.get("age"),
            "gender": self._format_gender(patient_info.get("gender")) if patient_info.get("gender") else None,
            "test_date": patient_info.get("test_date") or relevance_result.test_date,
            "facility": patient_info.get("facility"),
            "report_date": patient_info.get("report_date"),
        }
        summary = RelevanceService.summarize(relevance_result)
        return render_report_document(f"{name}님 맞춤 알레르기 리포트", meta, summary, body_html)

    def _allergen_detail_md(self, a: "AllergenAssessment", detailed: bool = True) -> str:
        kb = a.kb or {}
        # 임상 그룹(Df/Dp 등)은 통합 라벨로 한 번만 설명(A1)
        grp_label = self._display_name(a, detail=True)
        nm = a.korean_name or a.allergen_name
        grouped = grp_label != nm
        en = "" if grouped else (a.allergen_name if a.allergen_name != nm else "")
        head = f"### {grp_label}" + (f" ({en})" if en else "")
        strength = self._STRENGTH_KO.get(a.strength or "", "")
        meta = []
        if strength:
            meta.append(f"감작 강도: **{strength}**")
        sev = self._severity_badge(a)
        if sev:
            meta.append(f"증상 정도: {sev}")
        if kb.get("season_label_ko"):
            meta.append(f"주요 시기: {kb['season_label_ko']}")
        if kb.get("indoor_outdoor"):
            io = {"indoor": "실내", "outdoor": "실외", "both": "실내·외"}.get(kb["indoor_outdoor"], "")
            if io:
                meta.append(f"노출: {io}")
        lines = [head]
        if meta:
            lines.append(" · ".join(meta))
        if grouped:
            try:
                from services.clinical_group_service import get_clinical_group_service
                note = get_clinical_group_service().note_of(a)
                if note:
                    lines.append(f"*{note}*")
            except Exception:
                pass
        if getattr(a, "severity", None) in ("severe", "anaphylaxis"):
            lines.append("- 🚨 **중증 반응 병력:** 노출을 적극적으로 피하고, 응급 상황 대비 계획(응급약·병원 동선)을 "
                         "담당 의료진과 반드시 상의하세요.")
        if kb.get("biology_ko"):
            lines.append(f"- **특성·생활사:** {kb['biology_ko']}")
        if kb.get("exposure_environment_ko"):
            lines.append(f"- **주로 노출되는 환경:** {kb['exposure_environment_ko']}")
        if kb.get("cross_reactivity_ko"):
            lines.append(f"- **교차반응:** {kb['cross_reactivity_ko']}")
        if getattr(a, "oas_foods", None):
            lines.append(
                f"- **🍎 구강알레르기증후군(OAS) 주의:** {', '.join(a.oas_foods)} 섭취 시 입·목 가려움/부종이 "
                f"나타난다고 하셨습니다. 이 알러젠은 **구강알레르기증후군에 해당**하므로 해당 음식을 생으로 먹을 때 "
                f"주의하고(대개 익히면 완화), 증상이 심하거나 목·호흡기까지 번지면 즉시 진료를 받으세요.")
        elif kb.get("oral_allergy_syndrome_ko"):
            lines.append(f"- **구강알레르기증후군:** {kb['oral_allergy_syndrome_ko']}")
        # 교차반응 음식은 원인 항원별로 묶어 별도 '관찰할 음식' 섹션에서 안내(R-1) — 여기선 생략
        if a.rationale_ko:
            lines.append(f"- **판정 근거:** {a.rationale_ko}")
        avoid = kb.get("avoidance_control_ko", [])
        if avoid:
            lines.append("- **회피·관리 수칙:**")
            for tip in avoid[:5]:
                lines.append(f"    - {tip}")
        # 면역치료 가능 여부
        try:
            imt = get_knowledge_service().immunotherapy_info(a.category, a.allergen_name)
            if imt.get("eligible") and imt.get("ko"):
                lines.append(f"- **💉 면역치료(알레르기 근본치료) 가능:** {imt['ko']}")
        except Exception:
            pass
        return "\n".join(lines)

    def _crossreact_watchlist_md(self, relevance_result) -> List[str]:
        """R-1/R-2: 교차반응 가능 음식을 '원인 항원(카테고리)별'로 그룹화 + 확대 가능성 경고.
        개별 음식을 흩어 나열하지 않고, 어떤 항원과 엮여 있는지를 중심으로 배치한다."""
        groups = []
        for a in relevance_result.assessments:
            confirmed = list(dict.fromkeys(
                (getattr(a, "oas_foods", None) or []) + (getattr(a, "crossreact_confirmed", None) or [])))
            risk = getattr(a, "crossreact_risk", None) or []
            if confirmed or risk:
                groups.append((a, confirmed, risk))
        if not groups:
            return []
        md = ["\n---\n", "## 🍽️ 앞으로 주의해서 관찰할 음식 (교차반응)"]
        md.append(
            "아래는 **감작된 항원과 성분(단백질)을 공유해 교차반응이 나타날 수 있는 음식**을 "
            "원인 항원별로 정리한 것입니다. 개별 음식을 하나씩 외우기보다 **‘어떤 항원과 엮여 있는지’**로 "
            "기억하면 관리가 쉽습니다. (성분 공유는 *가능성*이며, 실제 반응은 증상으로 확인됩니다.)")
        for a, confirmed, risk in groups:
            nm = a.korean_name or a.allergen_name
            md.append(f"**🔗 「{nm}」과(와) 교차반응 가능**")
            if confirmed:
                md.append(f"- ✅ **현재 반응 확인:** {', '.join(confirmed)} — 함께 주의하세요.")
            if risk:
                shown = ", ".join(risk[:8]) + (" 등" if len(risk) > 8 else "")
                md.append(f"- 👀 **같은 계열(관찰 대상):** {shown} — 아직 증상은 없지만 같은 성분을 공유합니다.")
            # R-2: 확대 가능성 경고(최우선 서술) — 지금 반응 음식은 일부일 뿐, 같은 계열로 확대될 수 있음
            if confirmed:
                if risk:
                    md.append(
                        f"  - ⏳ **확대 가능성(중요):** 지금은 {', '.join(confirmed[:3])} 등에 반응하지만, "
                        f"같은 성분을 공유하는 **{', '.join(risk[:3])} 등 같은 계열 음식**에서도 시간이 지나며 "
                        f"교차반응이 **새로 생길 수 있습니다.** 새 음식을 처음 먹을 때 입·목 증상을 관찰하세요.")
                else:
                    md.append(
                        "  - ⏳ **확대 가능성(중요):** 같은 성분을 공유하는 다른 과일·채소·견과에서도 향후 "
                        "교차반응이 새로 생길 수 있으니, 새 음식을 처음 먹을 때 입·목 증상을 관찰하세요.")
        return md

    def _prevention_plan_md(
        self, relevant: List["AllergenAssessment"], screening: Optional["ScreeningProfile"]
    ) -> str:
        blocks: List[str] = []
        # 알러젠별 회피 수칙 통합
        tips: List[str] = []
        seen = set()
        for a in relevant:
            for tip in (a.kb or {}).get("avoidance_control_ko", []):
                key = tip.strip()
                if key and key not in seen:
                    seen.add(key)
                    tips.append(tip)
        if tips:
            blocks.append("### 🎯 우선 실천 회피 수칙")
            blocks.append("\n".join(f"- {t}" for t in tips[:10]))
        else:
            blocks.append("### 🎯 기본 생활 수칙")
            blocks.append(
                "- 침구는 주 1회 55~60℃ 뜨거운 물로 세탁하고 진드기 차단 커버를 사용하세요.\n"
                "- 실내 습도는 50% 이하로 유지하세요.\n"
                "- 꽃가루가 많은 날에는 외출·환기를 줄이고, 외출 후 샤워·세안하세요."
            )

        # 질환 기반 약제/치료 안내
        diseases = set(screening.allergic_diseases) if screening else set()
        med_lines = ["### 💊 증상 관리(의료진과 상담 후 사용)"]
        med_lines.append("- **비염(재채기·콧물·코막힘):** 경구 항히스타민제, 코막힘 위주면 비강 스테로이드 스프레이")
        med_lines.append("- **눈 증상:** 항히스타민 점안액")
        if "asthma" in diseases:
            med_lines.append("- **천식:** 흡입 스테로이드 등 조절제의 꾸준한 사용이 중요합니다(임의 중단 금지).")
        if "atopic_dermatitis" in diseases or "chronic_urticaria" in diseases:
            med_lines.append("- **피부 증상:** 보습제 기본 관리, 필요 시 국소 스테로이드/항히스타민제")
        blocks.append("\n".join(med_lines))

        # 면역치료 안내 (강한 감작 + 임상적 의미 있는 흡입 알러젠)
        strong_inhalant = [
            a for a in relevant
            if a.category in ("mite", "pollen_tree", "pollen_grass", "pollen_weed", "animal", "mold")
            and (a.strength in ("moderate", "strong"))
        ]
        if strong_inhalant:
            names = ", ".join(a.korean_name or a.allergen_name for a in strong_inhalant)
            blocks.append(
                "### 💉 면역치료(알레르기 주사/설하) 고려\n"
                f"증상을 유발하는 흡입 알러젠({names})에 대해, 회피와 약물로 조절이 어렵다면 "
                "**알레르겐 면역치료(3~5년)**를 담당 의료진과 상의해볼 수 있습니다. "
                "장기적으로 증상 완화와 천식 예방 효과가 보고됩니다."
            )
        return "\n\n".join(blocks)

    def generate_patient_report(
        self,
        relevance_result: "RelevanceAssessmentResult",
        patient_info: Dict[str, Any],
        screening: Optional["ScreeningProfile"] = None,
        use_llm: bool = True,
    ) -> AllergyManagementReport:
        """환자 맞춤형 리포트 생성.
        기본은 지식베이스 기반 결정론적 Markdown을 만들고, use_llm=True이고 API가 있으면
        GPT로 자연스러운 톤으로 다듬는다(실패 시 결정론적 결과 사용)."""
        base_md = self.build_patient_report_markdown(relevance_result, patient_info, screening)
        final_md = base_md

        if use_llm and self.client and self.api_key and self.api_key != "your_openai_api_key_here":
            try:
                prompt = (
                    "너는 한국의 알레르기 전문의다. 아래 [초안]은 환자의 알레르기 검사 결과와 "
                    "감작 vs 실제 알레르기 감별 결과를 정리한 리포트다. 내용(판정·수치·알러젠 분류)은 "
                    "절대 바꾸지 말고, 환자가 읽기 쉽게 문장을 자연스럽고 따뜻하게 다듬어라. "
                    "Markdown 구조(제목/불릿)와 핵심 정보는 유지하고, 새로운 의학적 사실을 지어내지 마라.\n\n"
                    f"[초안]\n{base_md}"
                )
                response = self.client.chat.completions.create(
                    model=settings.openai_report_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=settings.report_max_tokens,
                )
                polished = response.choices[0].message.content
                if polished and len(polished) > 200:
                    final_md = polished
            except Exception as e:
                logger.warning(f"환자 리포트 LLM 다듬기 실패(결정론적 결과 사용): {e}")

        report = self._parse_patient_markdown(final_md, relevance_result, patient_info)
        return report

    def _parse_patient_markdown(
        self,
        markdown_content: str,
        relevance_result: "RelevanceAssessmentResult",
        patient_info: Dict[str, Any],
    ) -> AllergyManagementReport:
        sections: List[AllergyManagementSection] = []
        current_title, current_content = None, []
        for line in markdown_content.split("\n"):
            if line.startswith("## "):
                if current_title:
                    sections.append(AllergyManagementSection(
                        title=current_title, content=current_content,
                        icon=self._get_section_icon(current_title)))
                current_title, current_content = line[3:].strip(), []
            elif line.strip():
                current_content.append(line.rstrip())
        if current_title:
            sections.append(AllergyManagementSection(
                title=current_title, content=current_content,
                icon=self._get_section_icon(current_title)))

        relevant = relevance_result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        return AllergyManagementReport(
            patient_name=patient_info.get("name") or relevance_result.patient_name or "환자",
            patient_age=patient_info.get("age"),
            patient_gender=patient_info.get("gender"),
            test_date=patient_info.get("test_date") or relevance_result.test_date or "-",
            key_allergens=[a.korean_name or a.allergen_name for a in relevant],
            sections=sections,
            markdown_content=markdown_content,
        )


# 싱글톤 인스턴스
_report_service: Optional[ReportService] = None
_last_api_key: Optional[str] = None


def get_report_service(api_key: Optional[str] = None) -> ReportService:
    """리포트 서비스 싱글톤 인스턴스 반환 (API 키 변경 시 재생성)"""
    global _report_service, _last_api_key
    
    # API 키 결정
    current_key = api_key or settings.openai_api_key
    
    # API 키가 변경되었거나 서비스가 없으면 새로 생성
    if _report_service is None or _last_api_key != current_key:
        _report_service = ReportService(api_key=current_key)
        _last_api_key = current_key
        
    return _report_service
