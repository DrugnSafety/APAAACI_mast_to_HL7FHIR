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


    # ==================================================================
    # 환자 맞춤형 리포트 (증상·예방 중심, 감작 vs 실제 알레르기 감별 반영)
    # ==================================================================

    _STRENGTH_KO = {"weak": "약한 양성", "moderate": "중등도 양성", "strong": "강한 양성"}

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

        # 0. 핵심 요약
        md.append("## 0️⃣ 한눈에 보기")
        md.append(
            f"- 🔴 **실제 주의가 필요한 알러젠(증상 유발): {len(relevant)}개** — {names(relevant)}\n"
            f"- ⚪ **감작만 된 알러젠(증상 없음): {len(sensitized)}개** — {names(sensitized)}\n"
            f"- 🟡 **관찰이 필요한 알러젠: {len(indeterminate)}개** — {names(indeterminate)}"
        )

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
            for a in relevant:
                md.append(self._allergen_detail_md(a, detailed=True))

        md.append("\n---\n")

        # 2. 감작만 된 알러젠
        md.append("## 2️⃣ ⚪ 감작만 된 알러젠 (과도한 회피 불필요)")
        if not sensitized:
            md.append("감작만 된 항목은 없습니다.")
        else:
            md.append("아래 항목은 검사에서 양성이지만 **노출에도 증상이 없어** 현재는 임상적 의미가 낮습니다. "
                      "지나친 회피나 식이 제한은 필요하지 않으며, 새로운 증상이 생기면 재평가하세요.")
            for a in sensitized:
                nm = a.korean_name or a.allergen_name
                md.append(f"- **{nm}** — {a.rationale_ko or '노출에도 증상이 없어 감작만 된 상태로 판단됩니다.'}")

        # 3. 관찰 필요
        if indeterminate:
            md.append("\n---\n")
            md.append("## 🟡 관찰이 필요한 알러젠")
            md.append("노출 경험이 없거나 정보가 부족해 판정을 보류한 항목입니다. 노출 시 증상 발생 여부를 관찰하세요.")
            for a in indeterminate:
                nm = a.korean_name or a.allergen_name
                probes = (a.kb or {}).get("relevance_probes_ko", [])
                probe = f" (확인 포인트: {probes[0]})" if probes else ""
                md.append(f"- **{nm}** — {a.rationale_ko or '노출-증상 관계 관찰이 필요합니다.'}{probe}")

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

    def _allergen_detail_md(self, a: "AllergenAssessment", detailed: bool = True) -> str:
        kb = a.kb or {}
        nm = a.korean_name or a.allergen_name
        en = a.allergen_name if a.allergen_name != nm else ""
        head = f"### {nm}" + (f" ({en})" if en else "")
        strength = self._STRENGTH_KO.get(a.strength or "", "")
        meta = []
        if strength:
            meta.append(f"감작 강도: **{strength}**")
        if kb.get("season_label_ko"):
            meta.append(f"주요 시기: {kb['season_label_ko']}")
        if kb.get("indoor_outdoor"):
            io = {"indoor": "실내", "outdoor": "실외", "both": "실내·외"}.get(kb["indoor_outdoor"], "")
            if io:
                meta.append(f"노출: {io}")
        lines = [head]
        if meta:
            lines.append(" · ".join(meta))
        if kb.get("biology_ko"):
            lines.append(f"- **특성·생활사:** {kb['biology_ko']}")
        if kb.get("exposure_environment_ko"):
            lines.append(f"- **주로 노출되는 환경:** {kb['exposure_environment_ko']}")
        if kb.get("cross_reactivity_ko"):
            lines.append(f"- **교차반응:** {kb['cross_reactivity_ko']}")
        if kb.get("oral_allergy_syndrome_ko"):
            lines.append(f"- **구강알레르기증후군:** {kb['oral_allergy_syndrome_ko']}")
        if a.rationale_ko:
            lines.append(f"- **판정 근거:** {a.rationale_ko}")
        avoid = kb.get("avoidance_control_ko", [])
        if avoid:
            lines.append("- **회피·관리 수칙:**")
            for tip in avoid[:5]:
                lines.append(f"    - {tip}")
        return "\n".join(lines)

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

        if use_llm and self.api_key and self.api_key != "your_openai_api_key_here":
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
