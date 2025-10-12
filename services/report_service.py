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
    AllergyManagementSection, FHIRBundle
)

# 로거 설정
logger = logging.getLogger(__name__)


class ReportService:
    """맞춤형 알레르기 관리 리포트 생성 서비스"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: OpenAI API 키 (None일 경우 settings에서 가져옴)
        """
        self.api_key = api_key or settings.openai_api_key
        if not self.api_key:
            raise ValueError("OpenAI API 키가 설정되지 않았습니다.")
        
        self.client = OpenAI(api_key=self.api_key)
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
