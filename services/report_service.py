"""
Report Service
GPT 기반 맞춤형 알레르기 관리 리포트 생성 서비스
"""

import html
import json
import logging
import re
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
from utils.text_utils import josa, trim_sentences
from services.knowledge_service import normalize_category, get_knowledge_service
from services.exposure_guidance_service import (
    NO_RELEVANT_NOTE_KO, allocated_tips, animal_guidance, exposure_links)
from services import care_guidance_service as care_guidance

# 로거 설정
logger = logging.getLogger(__name__)

# 교차반응 증상 범위 순위(강한 것 우선 표기)
_CR_SEV_RANK = {"oral": 1, "systemic": 2, "anaphylaxis": 3}

# Markdown 에서 서식·링크·속성으로 해석되는 글자. 백슬래시 대신 숫자 엔티티로 바꾼다 —
# 어떤 Markdown 렌더러에서도 글자 그대로 보이고, 표 칸 구분(|)이나 attr_list({: ...})로도 읽히지 않는다.
_MD_ENTITY = {"\\": "&#92;", "`": "&#96;", "*": "&#42;", "_": "&#95;", "[": "&#91;", "]": "&#93;",
              "{": "&#123;", "}": "&#125;", "|": "&#124;"}


def md_text(value: Any) -> str:
    """환자·OCR 에서 온 값을 리포트 Markdown 에 넣기 전에 글자 그대로 보이게 만든다.

    리포트 Markdown 은 python-markdown 으로 HTML 이 되는데 원시 HTML 을 그대로 통과시킨다.
    이름 칸에 `<img src=x onerror=…>` 를 넣으면 리포트·인쇄용 문서·메일 첨부에 그대로 실렸다.
    HTML 특수문자는 엔티티로, Markdown 메타문자(링크 `[..](javascript:…)`, 속성 `{: onclick=…}`,
    표 구분 `|` 등)도 엔티티로 바꾸고, 줄바꿈은 공백으로 접어 새 블록을 열지 못하게 한다.
    """
    text = re.sub(r"\s+", " ", str("" if value is None else value)).strip()
    text = html.escape(text, quote=False)
    return "".join(_MD_ENTITY.get(ch, ch) for ch in text)


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
        "venom": "벌에 쏘이지 않기 · 응급 대처 계획 확인",
        "food": "해당 음식 섭취 주의(전신 반응 시 응급)",
        "latex": "라텍스 제품 접촉 줄이기 · 진료·시술 전에 알리기",
        # 약물에는 회피 지시를 쓰지 않는다 — 피할지·다시 쓸지는 진료에서 정한다
        "drug": "이 약의 사용은 진료에서 확인(스스로 끊거나 다시 쓰지 않기)",
    }

    # 알러젠 블록 '결론' 문장(완결형) — 짧게, 행동을 먼저
    _ACTION_SENTENCE = {
        "mite": "침구와 실내 습도부터 관리하세요.",
        "animal": "그 동물과의 접촉을 줄이고 침실에는 들이지 마세요.",
        "pollen_tree": "봄철에는 외출과 환기를 줄이세요.",
        "pollen_grass": "초여름에는 잔디와 풀밭을 피하세요.",
        "pollen_weed": "가을에는 야외 활동을 줄이세요.",
        "mold": "실내 습도를 낮추고 곰팡이를 제거하세요.",
        "insect": "주방과 서식처 위생을 관리하세요.",
        "venom": "벌에 쏘이지 않도록 하고, 응급 대처 계획을 담당 의료진과 정해 두세요.",
        "food": "그 음식은 피하세요. 온몸 반응이 있었다면 응급 계획이 필요합니다.",
        "latex": "라텍스로 만든 제품과의 접촉을 줄이고, 진료·시술을 받기 전에 미리 알리세요.",
        "drug": "이 약을 계속 피할지, 다시 써도 되는지는 진료에서 정합니다. 처방·조제를 받을 때 이 결과와 "
                "겪은 반응을 알리세요.",
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
        "venom": "sting",     # 벌독은 들이마시는 항원이 아니다 — 실내·실외(계절성) 어느 쪽에도 넣지 않는다
        "latex": "contact",   # 닿아서 들어온다
        "drug": "drug",
    }
    _ENV_LABEL = {"indoor": "🏠 실내 항원", "outdoor": "🌳 실외(계절성) 항원",
                  "sting": "🐝 곤충 독(쏘임)", "food": "🍽️ 음식 항원", "contact": "🧤 접촉 항원(라텍스)",
                  "drug": "💊 약물", "other": "기타 항원"}

    def _severity_badge(self, a) -> str:
        return self._SEVERITY_BADGE.get(getattr(a, "severity", None) or "", "")

    def _env_group_of(self, a) -> str:
        cat = normalize_category(a.category)
        if cat == "food":
            return "food"
        return self._ENV_GROUP.get(cat, "other")

    def _raw_display_name(self, a, detail: bool = False) -> str:
        """임상 그룹(Df/Dp 등)은 통합 라벨로 표시(A1). 이스케이프 전 값 — 비교용."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return get_clinical_group_service().label_of(a, detail=detail)
        except Exception:
            return a.korean_name or a.allergen_name

    def _display_name(self, a, detail: bool = False) -> str:
        """리포트에 쓰는 항원 이름. 항원명은 OCR·사용자 입력에서 오므로 여기서 이스케이프한다."""
        return md_text(self._raw_display_name(a, detail))

    @staticmethod
    def _season_label(a, screening=None) -> str:
        """이 항원의 시기 라벨 — 계절성 절·카드뉴스와 같은 값 한 가지."""
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            return get_pollen_forecast_service().season_label(a, screening)
        except Exception:  # noqa: BLE001
            return (getattr(a, "kb", None) or {}).get("season_label_ko", "") or ""

    def _collapse(self, items):
        """임상 그룹 단위로 접어 중복 서술 제거(A1). 반환: [(대표 assessment, 그룹정보)]"""
        try:
            from services.clinical_group_service import get_clinical_group_service
            cg = get_clinical_group_service()
            return [(g["members"][0][1], g) for g in cg.collapse(items)]
        except Exception:
            return [(a, None) for a in items]

    def _one_line_relevant(self, a, screening=None) -> str:
        """실제 주의 알러젠 한 줄 요약 (한눈에 보기용)."""
        nm = self._display_name(a)
        cat = normalize_category(a.category)
        action = self._ONE_LINE_ACTION.get(cat, "노출 상황 관리 필요")
        animal = animal_guidance(a, screening)
        if animal and animal["one_line"]:
            action = animal["one_line"]   # 함께 사는지에 따라 할 일이 다르다
        season = md_text(self._season_label(a, screening))
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
                e = by_food.setdefault(md_text(f), {"triggers": [], "severity": sev})
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
        lang: str = "ko",
        include_header: bool = True,
    ) -> str:
        """지식베이스+감별결과로 환자용 리포트 Markdown을 결정론적으로 구성(API 불필요).
        lang 이 ko 가 아니면 마지막에 블록 단위로 번역한다(캐시 사용).
        include_header=False 면 제목과 환자 정보 줄(이름·나이·성별·검사일·작성일)을 뺀다 — 인쇄용 문서는
        표지에 같은 내용이 있어, 넣으면 첫 쪽에 환자 정보가 두 번 나온다."""
        # 이름·나이·성별·검사일은 결과지 OCR 과 사용자 입력에서 온다 — 여기서 한 번 이스케이프해 아래 전부에 쓴다
        name = md_text(patient_info.get("name") or relevance_result.patient_name or "환자")
        age = md_text(patient_info.get("age")) if patient_info.get("age") else ""
        gender = md_text(self._format_gender(patient_info.get("gender")))
        test_date = md_text(patient_info.get("test_date") or relevance_result.test_date or "-")
        today = datetime.now().strftime("%Y-%m-%d")

        relevant = relevance_result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        sensitized = relevance_result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        indeterminate = relevance_result.by_relevance(ClinicalRelevance.INDETERMINATE)
        # 약물 항원 — 위 세 가지 어디에도 넣지 않는다('진료 확인 필요')
        review = relevance_result.by_relevance(ClinicalRelevance.CLINICIAN_REVIEW)
        drug = care_guidance.drug_review(relevance_result.assessments, screening)

        tested = list(getattr(relevance_result, "tested_negatives", None) or [])
        negatives = [n for n in tested if n.status == "negative"]
        equivocal = [n for n in tested if n.status == "equivocal"]

        def names(items):
            # Df/Dp 등 임상 그룹은 한 번만 표기(A1)
            return ", ".join(self._display_name(a) for a, _g in self._collapse(items)) or "없음"

        md: List[str] = []
        if include_header:
            md.append(f"# 🌿 {name}님 맞춤 알레르기 검사 결과 리포트")
            info = f"**{name}**"
            if age:
                info += f" · {age}세"
            if gender and gender != "미제공":
                info += f" · {gender}"
            md.append(info + f"  \n**검사일:** {test_date}  \n**리포트 작성일:** {today}")
            md.append("\n---\n")

        # 0. 핵심 요약 (구조화)
        # 개수는 이름 목록과 같은 단위(임상 그룹)로 센다. 검사 항목 수로 세면 유럽·미국 집먼지진드기가
        # 2개로 세어져 '4개'라고 쓰고 이름은 3개만 적게 된다.
        total = len(relevant) + len(sensitized) + len(indeterminate) + len(review)
        n_rel, n_sens, n_ind, n_rev = (len(self._collapse(x)) for x in (relevant, sensitized, indeterminate, review))
        grouped_note = ("" if n_rel + n_sens + n_ind + n_rev == total else
                        " 아래 개수는 임상적으로 같은 항원(집먼지진드기 두 종 등)을 하나로 묶어 센 것입니다.")
        review_note = (f" 약물 항원 {n_rev}개는 이 리포트가 판정하지 않고 ‘{md_text(drug['label'])}’로 따로 두었습니다."
                       if drug else "")
        md.append("## 0️⃣ 한눈에 보기")
        md.append(
            f"> **{name}님**은 이번 검사에서 총 **{total}개** 항목에 양성(감작)으로 나왔고, "
            f"그중 문진 결과 **실제로 증상을 일으키는 것으로 확인된 알러젠은 {n_rel}개**입니다."
            f"{review_note}{grouped_note}"
        )
        md.append(
            "| 구분 | 개수 | 의미 | 대상 |\n"
            "|---|---|---|---|\n"
            f"| 🔴 실제 주의 | **{n_rel}** | 노출 시 실제 증상 유발 → 적극 관리 | {names(relevant)} |\n"
            f"| ⚪ 감작만 | {n_sens} | 검사만 양성, 증상 없음 → 과도한 회피 불필요 · 지켜보기 | {names(sensitized)} |\n"
            f"| 🟡 관찰 필요 | {n_ind} | 판정 보류 → 경과 관찰 | {names(indeterminate)} |"
            # 약물은 '실제 주의'도 '감작만'도 아니다 — 피할지·다시 쓸지는 진료에서 정한다
            + (f"\n| 🩺 {md_text(drug['label'])} | {n_rev} | 약물 — 이 리포트가 판정하지 않음 · 피할지, 다시 써도 "
               f"되는지는 진료에서 결정 | {names(review)} |" if drug else "")
            + (f"\n| ⚫ 음성 | {len(negatives)} | 검사했고 감작 없음 | {self._negative_names(negatives, 12)} |"
               if negatives else "")
        )
        # 검사 대조(히스타민·생리식염수)는 알러젠이 아니라 위 표에 넣지 않는다. 검사를 읽을 수 있는지만 한 줄로 적는다.
        check = getattr(relevance_result, "control_check", None)
        if check and check.get("line_ko"):
            icon = "⚠️" if check.get("status") == "caution" else "🧪"
            md.append(f"- {icon} {md_text(check['line_ko'])}")
        if relevant:
            md.append("**🔴 지금 우선 관리할 알러젠 요약**")
            for a, _g in self._collapse(relevant):   # Df/Dp 등은 한 번만(A1)
                md.append(f"- {self._one_line_relevant(a, screening)}")
        if drug:
            md.append(f"**🩺 {md_text(drug['label'])} — 약물**")
            for it in drug["items"]:
                sev = f" · {it['severity_label']}" if it["severity_label"] else ""
                md.append(f"- **{md_text(it['label'])}**{sev} — {md_text(it['one_line'])}")
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
                "알려주세요.")
            if any(info.get("severity") in ("systemic", "anaphylaxis") for info in alerts.values()):
                # 카드뉴스의 '반드시 주의할 음식' 카드와 같은 문장
                md.append("  - 🚨 **전신 반응 병력이 있습니다.** 반드시 피하시고, 응급약·응급 대처 계획을 "
                          "담당 의료진과 상의하세요.")

        # 스크리닝 요약
        if screening is not None:
            sc = get_screening_service().summarize(
                screening, test_type=getattr(relevance_result, "test_type", None))
            listed = lambda xs: ", ".join(md_text(x) for x in xs)  # noqa: E731 — 코드값도 사용자 입력이다
            if sc["diseases_ko"]:
                md.append(f"- 🩺 진단/의심 질환: {listed(sc['diseases_ko'])}")
            if care_guidance.has_anaphylaxis_history(screening):
                # 두 카드뉴스의 '치료와 연결하기' 카드와 같은 문장 — 응급 안내는 문서마다 달라지면 안 된다
                head, body = care_guidance.ANAPHYLAXIS_HISTORY_KO
                md.append(f"- 🚨 **{head}** {body}")
            # 패턴이 none 이면 줄을 쓰지 않는다. none 은 '답하지 않음'의 기본값이기도 해서, 증상이 확인된
            # 환자에게 '뚜렷한 패턴 없음 / 증상 없음'이라고 찍히던 자리다.
            pattern = getattr(getattr(screening, "season_pattern", None), "value", None)
            if sc["season_pattern_ko"] and pattern != "none":
                extra = f" (악화 시기: {listed(sc['worse_months_ko'])})" if sc["worse_months_ko"] else ""
                md.append(f"- 📆 증상 패턴: {md_text(sc['season_pattern_ko'])}{extra}")
            elif sc["worse_months_ko"]:
                md.append(f"- 📆 증상이 심해지는 시기: {listed(sc['worse_months_ko'])}")
            if sc["organ_systems_ko"]:
                md.append(f"- 👃 주로 나타나는 부위: {listed(sc['organ_systems_ko'])}")
            if sc.get("residence_ko"):
                md.append(f"- 📍 거주 지역: {md_text(sc['residence_ko'])} (꽃가루 시기 안내의 기준)")
            for flag in sc["flags"]:
                md.append(f"- ⚠️ {flag}")

            # 주증상 부위를 검사 결과와 이어준다. 나열만 하면 왜 물었는지 알 수 없다.
            organ_note = self._organ_focus_md(screening, relevant)
            if organ_note:
                md.append(organ_note)

        md.append("\n---\n")

        # 교육: 감작 vs 알레르기
        md.append("## 🧭 검사 양성이 곧 알레르기는 아닙니다")
        md.append(
            "검사 '양성'은 몸이 그 물질에 **감작**되었다는 뜻입니다. 알레르기 질환은 다릅니다. "
            "그 물질에 **노출될 때 증상이 되풀이되어야** 알레르기입니다. "
            "이 리포트는 검사 수치와 **노출 시 실제 증상**을 함께 봅니다. 그래서 정말 조심할 알러젠만 남깁니다."
        )
        md.append("\n---\n")

        # 1. 실제 주의 알러젠
        md.append("## 1️⃣ 🔴 실제 주의가 필요한 알러젠 (증상 유발)")
        if not relevant:
            md.append("이번 문진에서는 노출 시 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. "
                      "증상이 있을 때 어떤 상황이었는지 기록해 두면 다음 평가에 도움이 됩니다.")
        else:
            # 실내/실외/음식으로 묶어 노출 관리 방식이 같은 것끼리 설명(C)
            for env in ("indoor", "outdoor", "sting", "food", "contact", "drug", "other"):
                bucket = [a for a in relevant if self._env_group_of(a) == env]
                if not bucket:
                    continue
                md.append(f"### {self._ENV_LABEL[env]}")
                for a, _g in self._collapse(bucket):   # Df/Dp 등은 한 번만 설명(A1)
                    md.append(self._allergen_detail_md(a, detailed=True, lang=lang, screening=screening))

        # 약물 — '실제 주의'에도 '감작만'에도 '관찰 필요'에도 넣지 않는다
        drug_md = care_guidance.drug_review_md(relevance_result.assessments, screening)
        if drug_md:
            md.append("\n---\n")
            md.append(drug_md)

        md.append("\n---\n")

        # 2. 감작만 된 알러젠
        # 입장은 한 가지다(data/sensitization_prevention.json 의 stance_note_ko): 진단이 아니고 과도한 회피는
        # 필요 없다 + 부담이 적은 범위의 노출 줄이기는 도움이 될 수 있다(증명되지는 않음) + 증상이 생기면 재평가.
        # 예전에는 이 절이 "지금은 피하지 않아도 됩니다", 바로 아래 절이 "노출은 줄여 두세요"라고 했다.
        md.append("## 2️⃣ ⚪ 감작만 된 알러젠 (과도한 회피 불필요 · 예방과 관찰)")
        if not sensitized:
            md.append("감작만 된 항목은 없습니다.")
        else:
            md.append(" ".join(md_text(x) for x in care_guidance.sensitized_lead(
                relevance_result.assessments, screening)))
            for a, _g in self._collapse(sensitized):
                nm = self._display_name(a, detail=True)
                md.append(f"- **{nm}** — "
                          f"{md_text(a.rationale_ko) or '노출에도 증상이 없어 감작만 된 상태로 판단됩니다.'}")
            # 항목별 예방과 관찰(실제 주의·음성 항원에는 나오지 않는다)
            prevention_md = care_guidance.prevention_md(relevance_result.assessments, screening)
            if prevention_md:
                md.append(prevention_md)

        # 3. 관찰 필요
        if indeterminate:
            md.append("\n---\n")
            md.append("## 🟡 관찰이 필요한 알러젠")
            # 미룬 이유는 항목마다 다르다(노출 경험이 없음 / 답이 없음 / 답은 있지만 그것만으로 가를 수 없음).
            # 예전에는 '노출 경험이 없거나 정보가 부족해'라고 한 가지로 적어, 벌에 쏘인 자리만 부었다고 답한
            # 환자의 벌독 항목에도 그렇게 실렸다.
            md.append(md_text(care_guidance.observation_intro()))
            for a, _g in self._collapse(indeterminate):
                nm = self._display_name(a, detail=True)
                md.append(f"- **{nm}** — {md_text(a.rationale_ko) or '노출-증상 관계 관찰이 필요합니다.'}"
                          f"{self._open_point(a)}")
            # 판정을 미룬 항목은 '감작만'이 아니다 — 지켜볼 증상은 이 절에 적는다
            observation_md = care_guidance.observation_md(relevance_result.assessments, screening)
            if observation_md:
                md.append(observation_md)

        # 음성·경계 — 음성도 결과다. '검사 안 함'과 구분되도록 무엇을 검사해서 음성이었는지 남긴다
        if negatives or equivocal:
            md.append("\n---\n")
            md.append("## ⚫ 검사했고 음성인 항목")
            if negatives:
                md.append("아래 항목은 **이번에 검사했고 감작이 확인되지 않았습니다.** 지금 겪는 알레르기 증상의 "
                          "원인일 가능성은 낮습니다. 다만 음성이 평생 괜찮다는 보증은 아니므로, 특정 노출 때마다 "
                          "증상이 뚜렷하게 되풀이되면 의료진에게 알려 주세요.")
                md.append(f"- {self._negative_names(negatives)}")
            if equivocal:
                md.append("**경계값·판독 확인 필요** — 양성·음성을 단정하기 어려운 항목입니다. 원본 결과지를 "
                          "의료진과 함께 확인하세요.")
                md.append(f"- {self._negative_names(equivocal, with_value=True)}")

        # 교차반응 관찰 음식(원인 항원별 그룹 + 확대 경고)
        md += self._crossreact_watchlist_md(relevance_result)

        md.append("\n---\n")

        # 3. 예방·관리 플랜
        md.append("## 3️⃣ 🛡️ 나를 위한 예방·관리 플랜")
        seasonality_md = self._seasonality_md(relevance_result.assessments, screening)
        if seasonality_md:
            md.append(seasonality_md)

        plan = self._prevention_plan_md(relevant, screening)
        md.append(plan)

        # 문진에서 고른 주증상·쓰는 약에 맞춘 안내(고르지 않은 것은 말하지 않는다)
        symptoms_md = care_guidance.symptoms_md(relevance_result.assessments, screening)
        if symptoms_md:
            md.append(symptoms_md)
        medication_md = care_guidance.medication_md(relevance_result.assessments, screening)
        if medication_md:
            md.append("\n---\n")
            md.append(medication_md)

        imt_md = self._immunotherapy_md(relevance_result.assessments, screening)
        if imt_md:
            md.append("\n---\n")
            md.append(imt_md)

        md.append("\n---\n")

        # 질환 일반 정보(온톨로지) — 환자가 고른 기저 질환 기준. 검사 결과와 섞이지 않게 따로 둔다
        disease_md = self._disease_knowledge_md(screening)
        if disease_md:
            md.append(disease_md)
            md.append("\n---\n")

        # 4. 추적 관리
        md.append("## 4️⃣ 📅 추적 관리")
        md.append(
            "- **증상 일지**: 증상이 있던 날의 날짜, 장소나 계절, 닿았던 것, 심한 정도(0~10)를 적어 두세요.\n"
            "- **재평가**: 증상이 새로 생기거나 달라지면 담당 의료진을 다시 만나세요. 변화가 없을 때의 정기 점검 간격은 진료에서 정합니다.\n"
            "- **응급 상황**: 호흡곤란, 온몸 두드러기, 어지럼이 오면 아나필락시스일 수 있습니다. 바로 병원에 가세요."
        )

        md.append("\n---\n")
        md.append(
            f"> 💛 {name}님, 원인을 알면 알레르기는 조절할 수 있습니다. "
            "정말 중요한 알러젠부터 하나씩 실천해 보세요.\n\n"
            "*본 리포트는 교육용 참고 자료이며, 정확한 진단과 치료는 담당 의료진과 상담하시기 바랍니다.*"
        )
        out = "\n\n".join(md)
        if (lang or "ko") != "ko":
            from services.translation_service import get_translation_service
            out = get_translation_service().translate_markdown(out, lang)
        return out

    def build_patient_report_html_document(
        self,
        relevance_result: "RelevanceAssessmentResult",
        patient_info: Dict[str, Any],
        screening: Optional["ScreeningProfile"] = None,
        lang: str = "ko",
    ) -> str:
        """맞춤 리포트를 인쇄(PDF)·화면 겸용의 자체 완결형 HTML 문서로 생성.
        report_design 스킬(단일 디자인 소스)로 감싸 HTML/PDF 두 버전의 일관성을 보장한다."""
        from services.report_design import render_report_document
        from services.relevance_service import RelevanceService

        # 표지에 이름·나이·성별·검사일이 있으므로 본문에서는 제목과 환자 정보 줄을 뺀다. 예전에는 제목(H1)만
        # 지워서, 표지 바로 아래에 이름·나이·성별·검사일이 한 번 더 나왔다.
        md = self.build_patient_report_markdown(relevance_result, patient_info, screening, lang,
                                                include_header=False)
        # 본문 마크다운 → HTML (표지·요약 타일은 문서 템플릿이 별도로 그림)
        try:
            import markdown as md_lib
            body_html = md_lib.markdown(md, extensions=["extra", "sane_lists", "nl2br"])
        except Exception:
            body_html = "<pre>" + md + "</pre>"

        name = patient_info.get("name") or relevance_result.patient_name or "환자"
        meta = {
            "name": name,
            "age": patient_info.get("age"),
            "gender": self._format_gender(patient_info.get("gender")) if patient_info.get("gender") else None,
            "test_date": patient_info.get("test_date") or relevance_result.test_date,
            "facility": patient_info.get("facility"),
            "report_date": patient_info.get("report_date"),
            "created": datetime.now().strftime("%Y-%m-%d"),
        }
        summary = RelevanceService.summarize(relevance_result)
        # 표지 타일은 판정 그대로 센다(약물은 '진료 확인 필요'로 따로) — 본문 표와 같은 단위(임상 그룹)로
        by = relevance_result.by_relevance
        grouped = {
            "clinically_relevant": len(self._collapse(by(ClinicalRelevance.CLINICALLY_RELEVANT))),
            "sensitized_only": len(self._collapse(by(ClinicalRelevance.SENSITIZED_ONLY))),
            "indeterminate": len(self._collapse(by(ClinicalRelevance.INDETERMINATE))),
            "clinician_review": len(self._collapse(by(ClinicalRelevance.CLINICIAN_REVIEW))),
        }
        summary = dict(summary, counts=grouped, review_label=care_guidance.drug_review_label(),
                       grouped=sum(grouped.values()) != summary["total_positive"])
        doc = render_report_document(f"{name}님 맞춤 알레르기 리포트", meta, summary, body_html)
        if (lang or "ko") != "ko":
            # 본문은 이미 번역됐고, 표지·타일·안내문 등 문서 템플릿의 고정 문구만 남는다(캐시 적중률 높음)
            from services.translation_service import get_translation_service
            doc = get_translation_service().translate_html(doc, lang)
        return doc

    @staticmethod
    def _open_point(a) -> str:
        """판정을 미룬 항목의 '확인 포인트' 한 가지 — 문진에서 아직 답이 없는 문항만.

        open_questions_ko 가 None 이면 적응형 문진을 거치지 않은 평가(예전 3문항 경로)라 지식베이스의 일반
        질문을 쓴다. 빈 목록이면 물을 것을 이미 다 물은 것이므로 아무것도 적지 않는다 — 답한 것을 다시 묻지 않는다."""
        open_q = getattr(a, "open_questions_ko", None)
        if open_q is None:
            open_q = (a.kb or {}).get("relevance_probes_ko", [])
        return f" (확인 포인트: {md_text(open_q[0])})" if open_q else ""

    @staticmethod
    def _disease_knowledge_md(screening) -> str:
        """온톨로지에서 고른 질환 일반 정보. 모든 항목은 검토 전(candidate)임을 밝히고 출처를 단다.
        약은 계열 이름만, 선택은 의료진에게 — 챗봇과 같은 사용 규칙을 따른다."""
        try:
            from services.ontology_service import disease_summaries_for, disease_summary_notice
            items = disease_summaries_for(screening)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"질환 일반 정보 생략: {e}")
            return ""
        if not items:
            return ""
        md = ["## 📚 알아두면 좋은 질환 정보 (일반 참고)"]
        for it in items:
            md.append(f"### {it['title_ko']}")
            if it.get("definition_ko"):
                md.append(it["definition_ko"])
            rows = [("흔한 증상", it.get("symptoms")), ("확인 방법", it.get("evaluation")),
                    ("일반적인 관리", it.get("management")), ("함께 나타날 수 있는 질환", it.get("related"))]
            lines = [f"- **{label}:** {', '.join(x['ko'] for x in vals)}"
                     + (" — 어떤 치료를 할지는 담당 의료진과 정하세요." if label == "일반적인 관리" else "")
                     for label, vals in rows if vals]
            if lines:
                md.append("\n".join(lines))
            if it.get("source_url"):
                src = it.get("definition_source") or {}
                code = " ".join(x for x in (src.get("system"), src.get("code")) if x)
                md.append(f"*출처: [Wikipedia — {src.get('label') or it['title_ko']}]({it['source_url']})"
                          + (f" · 표준 정의 {code}" if code else "") + "*")
        md.append(f"> ℹ️ {disease_summary_notice()}")
        return "\n\n".join(md)

    @staticmethod
    def _negative_names(items, limit: Optional[int] = None, with_value: bool = False) -> str:
        """음성·경계 항목 이름 나열. 같은 한글명(예: 진드기 두 종)은 영문을 붙여 구분한다."""
        out: List[str] = []
        for n in items:
            # 이름·수치·class 는 모두 OCR 이 읽은 글자다
            nm = md_text(n.korean_name or n.allergen_name)
            if with_value:
                val = md_text(n.size_text or n.value_text
                              or (f"{n.test_value}" if n.test_value is not None else ""))
                cls = f", class {md_text(n.class_value)}" if n.class_value not in (None, "") else ""
                if val or cls:
                    nm += f" ({val}{cls})".replace("(, ", "(")
            if nm not in out:
                out.append(nm)
        if limit is not None and len(out) > limit:
            return ", ".join(out[:limit]) + f" 외 {len(out) - limit}개"
        return ", ".join(out)

    @staticmethod
    def _first_sentences(text: str, n: int = 2, max_len: int = 110) -> str:
        """설명문을 앞 n문장(최대 max_len자)으로 줄인다 — 리포트 본문은 짧게, 나머지는 '더 알아보기'로."""
        text = (text or "").strip()
        if not text:
            return ""
        # 글자수로 자르면 문장 중간에서 끊긴다 — 온전한 문장만 모은다
        return trim_sentences(text, max_len, max_sentences=n)

    def _allergen_detail_md(self, a: "AllergenAssessment", detailed: bool = True,
                            lang: str = "ko", screening=None) -> str:
        """알러젠 1건의 리포트 블록. 읽는 순서를 강제한다:
        ① 이름 + 배지(칩) → ② 한 줄 결론 → ③ 지금 할 일(최대 3개) → ④ 더 알아보기(근거·특성·교차반응·면역치료).
        문장은 짧게, 쉼표는 줄이고, 행동을 먼저 쓴다(humanizer 원칙)."""
        kb = a.kb or {}
        grp_label = self._display_name(a, detail=True)
        nm = a.korean_name or a.allergen_name
        grouped = self._raw_display_name(a, detail=True) != nm
        en = "" if grouped else (md_text(a.allergen_name) if a.allergen_name != nm else "")
        # 비한국어 리포트에서는 제목의 영문 병기를 뺀다. 한글 이름이 번역되면 같은 영문이
        # 두 번 나와 "Birch pollen (Birch pollen)" 이 된다.
        if (lang or "ko") != "ko":
            en = ""
        head = f"### {grp_label}" + (f" ({en})" if en else "")
        cat = normalize_category(a.category)

        # ① 배지 라인 — 코드 스팬으로 칩처럼 보이게(report_design .content code)
        chips = []
        strength = self._STRENGTH_KO.get(a.strength or "", "")
        if strength:
            chips.append(f"`{strength}`")
        sev = self._severity_badge(a)
        if sev:
            chips.append(f"`증상 {sev.replace('**', '')}`")
        season = self._season_label(a, screening)
        if season:
            chips.append(f"`{md_text(season)}`")
        if kb.get("indoor_outdoor"):
            io = {"indoor": "🏠 실내", "outdoor": "🌳 실외", "both": "실내·외"}.get(kb["indoor_outdoor"], "")
            if io:
                chips.append(f"`{io}`")
        lines = [head]
        if chips:
            lines.append(" ".join(chips))

        # ② 한 줄 결론 — 무엇이 문제이고 무엇을 하면 되는지 한 문장씩
        action = self._ACTION_SENTENCE.get(cat, "노출되는 상황을 줄이는 것이 우선입니다.")
        # 동물 항원은 함께 사는지에 따라 관리가 전혀 다르다 — 문진의 반려동물 답으로 맞춘다
        animal = animal_guidance(a, screening)
        if animal and animal["action_sentence"]:
            action = animal["action_sentence"]
        if cat == "drug":
            # 약물: 검사 양성 + 환자가 말한 반응. 확정도 회피 지시도 여기서 하지 않는다.
            lines.append(f"**결론.** 검사가 양성이고, 이 약을 쓴 뒤 반응이 있었다고 답하신 약물입니다. {action}")
            if getattr(a, "severity", None) in ("severe", "anaphylaxis"):
                lines.append("- 🚨 **중증 반응 병력:** 빠른 시일 안에 진료를 받아 이 약에 대한 평가와 응급 대처 계획을 상의하세요.")
        else:
            lines.append(f"**결론.** 노출될 때 증상이 실제로 나타나는 알러젠입니다. {action}")
            if getattr(a, "severity", None) in ("severe", "anaphylaxis"):
                lines.append("- 🚨 **중증 반응 병력:** 노출을 적극적으로 피하세요. 응급약과 병원 동선은 담당 의료진과 미리 정해 두세요.")

        # ③ 지금 할 일 — 회피 수칙 중 앞 3개만 번호로
        avoid = [t.strip() for t in kb.get("avoidance_control_ko", []) if t and t.strip()]
        if animal and animal["steps"]:
            lines.append(f"**지금 할 일 — {animal['headline']}**")
            for i, step in enumerate(animal["steps"], 1):
                lines.append(f"{i}. {step['text']}")
            for text in animal["extra"]:
                lines.append(f"- {text}")
            if animal["caveat"]:
                lines.append(f"- {animal['caveat']}")
            work = animal.get("occupational")
            if work:      # 일하면서 다루는 동물 — 직장 노출 안내를 따로 적는다
                lines.append(f"**{work['headline']}**")
                for i, step in enumerate(work["steps"], 1):
                    lines.append(f"{i}. {step['text']}")
            avoid = []   # 일반 수칙('침실 출입 금지' 등)은 위 안내가 대신한다
        elif avoid:
            lines.append("**지금 할 일**")
            for i, tip in enumerate(avoid[:3], 1):
                lines.append(f"{i}. {md_text(tip)}")
        if getattr(a, "oas_foods", None):
            lines.append(
                f"- 🍎 **입·목 증상이 있었던 음식:** {', '.join(md_text(f) for f in a.oas_foods)}. 생으로 먹을 때 주의하세요. "
                f"익히면 대개 괜찮아지지만 목이나 호흡기까지 번지면 바로 진료를 받으세요.")

        # ④ 더 알아보기 — 근거와 배경. 본문 흐름을 끊지 않도록 보조 블록으로 묶는다
        more: List[str] = []
        if a.rationale_ko:
            # 판정 근거 문장에는 항원 이름(OCR)이 들어 있다
            more.append(f"- **왜 이렇게 판단했나:** {md_text(a.rationale_ko)}")
        if grouped:
            try:
                from services.clinical_group_service import get_clinical_group_service
                note = get_clinical_group_service().note_of(a)
                if note:
                    more.append(f"- **왜 하나로 묶었나:** {note}")
            except Exception:
                pass
        # 지식 문장은 대부분 사람이 쓴 자료지만, 레지스트리에 없는 항원은 외부(Wikipedia) 요약과
        # 항원 이름이 섞인 기본 문장이 들어온다 — 같은 규칙으로 이스케이프한다
        if kb.get("biology_ko"):
            more.append(f"- **어떤 알러젠인가:** {md_text(self._first_sentences(kb['biology_ko']))}")
        if kb.get("exposure_environment_ko"):
            more.append("- **어디서 노출되나:** "
                        f"{md_text(self._first_sentences(kb['exposure_environment_ko'], 1, 90))}")
        if kb.get("cross_reactivity_ko"):
            more.append(f"- **교차반응:** {md_text(self._first_sentences(kb['cross_reactivity_ko'], 2, 120))}")
        if not getattr(a, "oas_foods", None) and kb.get("oral_allergy_syndrome_ko"):
            more.append("- **구강알레르기증후군:** "
                        f"{md_text(self._first_sentences(kb['oral_allergy_syndrome_ko'], 1, 100))}")
        if len(avoid) > 3:
            more.append("- **그 밖의 관리 수칙:** " + " / ".join(md_text(t) for t in avoid[3:6]))
        try:
            imt = get_knowledge_service().immunotherapy_info(
                a.category, a.allergen_name, a.korean_name or "", assessment=a)
            if imt.get("eligible"):
                more.append("- **💉 면역치료:** 진료에서 상의할 수 있는 선택지입니다. 아래 '면역치료' 절에 정리했습니다.")
        except Exception:
            pass
        if more:
            lines.append('<div class="detail-more" markdown="1">')
            lines.append("**더 알아보기**\n")
            lines.extend(more)
            lines.append("\n</div>")
        return "\n".join(lines)

    def _crossreact_watchlist_md(self, relevance_result) -> List[str]:
        """R-1/R-2: 교차반응 가능 음식을 '원인 항원(카테고리)별'로 그룹화 + 확대 가능성 경고.
        개별 음식을 흩어 나열하지 않고, 어떤 항원과 엮여 있는지를 중심으로 배치한다."""
        groups = []
        for a, _g in self._collapse(relevance_result.assessments):   # Df/Dp 등은 한 번만(A1)
            confirmed = [md_text(f) for f in dict.fromkeys(
                (getattr(a, "oas_foods", None) or []) + (getattr(a, "crossreact_confirmed", None) or []))]
            risk = [md_text(f) for f in (getattr(a, "crossreact_risk", None) or [])]
            if confirmed or risk:
                groups.append((a, confirmed, risk))
        if not groups:
            return []
        # 이 절의 음식은 '지금 알레르기가 있는 음식'이 아니다. 예전 제목('앞으로 주의해서 관찰할 음식')과 문구는
        # 증상이 없는 후보까지 주의 대상처럼 읽혔고, 성분만 공유하는 음식(고양이–소고기·우유, 진드기–아니사키스)도
        # 실었다. 지금은 보고된 교차반응 증후군이 있는 음식만 싣고(questionnaire_service._evidence_backed),
        # 증상이 확인된 음식과 '알아만 둘' 음식을 문장으로 가른다.
        only_watch = not any(confirmed for _a, confirmed, _r in groups)
        md = ["\n---\n", "## 🍽️ 교차반응 — 알아 둘 음식" + (" (지금 알레르기가 있다는 뜻이 아닙니다)" if only_watch else "")]
        md.append(
            "아래는 이번 검사에서 양성으로 나온 항원과 **교차반응이 보고된 음식**을 원인 항원별로 정리한 것입니다. "
            "‘알아 둘 음식’으로 적은 것은 **지금 알레르기가 있다는 뜻이 아니며, 문제없이 드시고 있다면 끊을 필요가 "
            "없습니다.** 먹은 뒤 입·목 가려움이나 두드러기가 새로 생기면 그때 그 음식을 적어 진료에서 알려 주세요.")
        for a, confirmed, risk in groups:
            nm = self._display_name(a)
            md.append(f"**🔗 「{nm}」{josa(self._raw_display_name(a), '과와')} 교차반응이 보고된 음식**")
            if confirmed:
                md.append(f"- ✅ **현재 반응 확인:** {', '.join(confirmed)} — 함께 주의하세요.")
            if risk:
                shown = ", ".join(risk[:8]) + (" 등" if len(risk) > 8 else "")
                md.append(f"- 👀 **알아 둘 음식(증상 없음 · 관찰만):** {shown} — 지금 반응이 있는 음식이 아닙니다.")
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

    _ORGAN_FOCUS_KO = {
        "nasal": ("코 증상", "코 증상은 흡입 알러젠(진드기·꽃가루·동물) 노출과 함께 움직이는지가 핵심입니다."),
        "ocular": ("눈 증상", "눈 증상은 꽃가루 시즌·동물 접촉과 겹치는지 확인하세요."),
        "lower_airway": ("하기도 증상", "기침·천명·숨참은 천식과 연관될 수 있어 별도 평가가 필요합니다."),
        "skin": ("피부 증상", "피부 증상은 음식·접촉 알러젠과의 시간 관계를 기록해 두면 판정에 도움이 됩니다."),
        "gi": ("소화기 증상", "복통·설사는 음식 섭취와의 시간 관계가 중요합니다."),
        "systemic": ("전신 증상", "전신 증상 병력은 응급 대처 계획이 필요한지 판단하는 근거가 됩니다."),
    }

    def _organ_focus_md(self, screening, relevant) -> str:
        """처음에 답한 '주증상 부위'를 이번 판정과 연결한다."""
        organs = list(getattr(screening, "organ_systems", None) or [])
        if not organs:
            return ""
        lines = []
        for o in organs:
            if o in self._ORGAN_FOCUS_KO:
                label, note = self._ORGAN_FOCUS_KO[o]
                lines.append(f"  - **{label}**: {note}")
        if not lines:
            return ""
        diseases = set(getattr(screening, "allergic_diseases", None) or [])
        if "lower_airway" in organs and "asthma" not in diseases:
            lines.append("  - 기침·천명·숨참이 있다고 답하셨는데 천식 진단은 없습니다. "
                         "이 검사로는 천식 여부를 알 수 없으니 진료에서 폐기능검사가 필요한지 확인하세요.")
        return "\n".join(["", "처음 알려주신 **주증상 부위**를 이번 결과와 이어보면:"] + lines)

    def _seasonality_md(self, assessments, screening) -> str:
        """증상이 계절을 타는지, 탄다면 어느 달에 대비해야 하는지.

        꽃가루·실외 곰팡이는 노출이 계절로 몰린다. 시즌을 알면 '언제부터 약을 준비할지'가 정해져서
        회피 수칙보다 실행에 옮기기 쉽다. 환자가 답한 악화 시기와 어긋나면 그 사실도 알린다.
        """
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            s = get_pollen_forecast_service().seasonality(assessments, screening)
        except Exception:  # noqa: BLE001
            return ""
        if not s.get("available"):
            return ""

        md = ["\n## 🍂 증상의 계절성"]
        region = f" ({s['region_label_ko']} 기준)" if s.get("region_label_ko") else ""
        if s.get("items"):
            md.append(f"검사에서 양성이고 **그 시기에 증상도 확인된** 알러젠 가운데 "
                      f"계절을 타는 것이 있습니다{region}.")
            md.append("")
            md.append("| 알러젠 | 시기 |")
            md.append("|---|---|")
            for it in s["items"]:
                md.append(f"| {md_text(it['name'])} | {md_text(it['season_label_ko']) or '-'} |")
            if s.get("category_estimate"):
                md.append("\n‘전체 기준 추정’이라고 적은 시기는 그 식물만의 자료가 없어, 같은 분류군"
                          "(수목·잔디·잡초) 전체의 범위로 대신한 값입니다. 실제 시기는 더 짧을 수 있습니다.")

        if s.get("excluded"):
            # 감작만 된 계절성 알러젠은 달력에 넣지 않는다 — 넣지 않았다는 사실은 분명히 적는다
            md.append(f"\n{', '.join(md_text(x) for x in s['excluded'])}{josa(s['excluded'][-1], '은는')} "
                      "검사만 양성이고 그 시기의 증상이 확인되지 않아 여기에 넣지 않았습니다.")

        months = s.get("predicted_months") or []
        if months:
            labels = s["month_labels_ko"]
            strip = " ".join(("**" + labels[m - 1] + "**") if m in months else labels[m - 1]
                             for m in range(1, 13))
            md.append(f"\n주의가 필요한 달: {strip}")

        if s.get("in_season_now"):
            md.append(f"\n**지금({s['month_labels_ko'][s['current_month'] - 1]})은 "
                      f"{', '.join(md_text(x) for x in s['in_season_now'])} 시즌입니다.**")

        reported = s.get("reported_months") or []
        if reported:
            rl = ", ".join(s["month_labels_ko"][m - 1] for m in reported)
            if s.get("mismatch"):
                md.append(f"\n> 답해주신 악화 시기({rl})가 위 알러젠 시즌과 겹치지 않습니다. "
                          "계절과 무관한 원인(집먼지진드기·동물·실내 곰팡이)이 함께 있을 수 있어 "
                          "진료에서 확인이 필요합니다.")
            elif s.get("overlap_months"):
                ol = ", ".join(s["month_labels_ko"][m - 1] for m in s["overlap_months"])
                md.append(f"\n> 답해주신 악화 시기({rl})가 위 알러젠 시즌과 **{ol}** 에서 겹칩니다. "
                          "계절성 알레르기로 볼 근거가 됩니다.")
        elif s.get("reported_pattern") in ("seasonal", "both"):
            md.append("\n> 증상이 계절을 탄다고 답하셨습니다. 어느 달에 심한지 기록해 두면 "
                      "다음 진료에서 원인을 좁히는 데 도움이 됩니다.")

        # 지역 메모는 그 메모가 말하는 식물에 이 환자의 증상이 확인됐을 때만 온다(seasonality 가 가린다)
        if s.get("notable_ko"):
            md.append(f"\n> {s['notable_ko']}")

        # 시즌 전에 약을 미리 쓰는 방법 — 출처가 있는 문장(data/treatment_guidance.json) 한 가지를 근거수준과 함께
        # 쓴다. 예전에는 여기('조절에 유리합니다')와 카드('조절이 쉬워요'), 약 안내('근거수준은 매우 낮음')가
        # 같은 내용을 서로 다른 확신으로 적었다.
        prophylaxis = care_guidance.treatment_text("pollen_prophylaxis_general_ko")
        if prophylaxis:
            md.append(f"\n**시즌 대비:** {md_text(prophylaxis)}")
        return "\n".join(md)

    def _regional_season_md(self, assessments, screening) -> str:
        """거주 지역 기준 꽃가루 시기. 지역을 모르면 아무 말도 하지 않는다."""
        if screening is None or not getattr(screening, "residence_country", None):
            return ""
        try:
            from services.pollen_forecast_service import get_pollen_forecast_service
            out = get_pollen_forecast_service().for_patient(
                assessments,
                country=getattr(screening, "residence_country", None),
                region=getattr(screening, "residence_region", None),
                lat=getattr(screening, "residence_lat", None),
                lon=getattr(screening, "residence_lon", None),
                postal_code=getattr(screening, "residence_postal_code", None))
        except Exception:  # noqa: BLE001
            return ""
        if not out.get("available") or not out.get("items"):
            return ""

        md = [f"\n## 🗓️ 거주 지역 기준 꽃가루 시기 — {out.get('region_label_ko') or ''}"]
        now = out.get("in_season_now") or []
        if now:
            md.append(f"**지금은 {', '.join(md_text(i.get('korean_name') or '') for i in now)} 시즌입니다.**")
        else:
            md.append("지금은 해당하는 꽃가루 시즌이 아닙니다.")
        md.append("")
        md.append("| 알러젠 | 종류 | 시기 | 지금 |")
        md.append("|---|---|---|---|")
        for i in out["items"]:
            mark = "🔴 시즌" if i.get("in_season") else ("⚪ 비시즌" if i.get("in_season") is False else "–")
            label = md_text(i.get("season_label_ko") or (f"실시간 {i.get('level')}" if i.get("level") else "–"))
            md.append(f"| {md_text(i.get('korean_name') or i.get('allergen_name'))} | {i.get('type_ko')} "
                      f"| {label} | {mark} |")
        if out.get("notable_ko"):
            md.append(f"\n> {out['notable_ko']}")
        if out.get("live"):
            md.append("\n> 실시간 꽃가루 예보를 반영한 값입니다.")
        else:
            md.append("\n> 지역 달력 기준이며 해마다 1~3주 차이가 납니다.")
        return "\n".join(md)

    def _prevention_plan_md(
        self, relevant: List["AllergenAssessment"], screening: Optional["ScreeningProfile"]
    ) -> str:
        blocks: List[str] = []
        link_md = self._exposure_link_md(relevant, screening)
        if link_md:
            blocks.append(link_md)
        # 알러젠별 회피 수칙 — 실제 주의 알러젠마다 돌아가며 뽑고, 표현만 다른 같은 수칙은 한 번만
        tips = allocated_tips([(self._display_name(a), a) for a, _g in self._collapse(relevant)],
                              screening, limit=10)
        if tips:
            blocks.append("### 🎯 우선 실천 회피 수칙")
            blocks.append("\n".join(f"- **{t['label']}:** {md_text(t['tip'])}" for t in tips))
        else:
            blocks.append("### 🎯 지금 꼭 지켜야 할 회피 수칙은 없습니다")
            # 증상 기록·재평가는 바로 아래 '추적 관리' 절에 있으므로 여기서 되풀이하지 않는다
            blocks.append(NO_RELEVANT_NOTE_KO)

        # 약물 안내는 여기에 두지 않는다. 예전의 '증상 관리' 목록은 질환도 증상도 없는 환자에게까지 비염·눈
        # 약을 적었고, 바로 뒤의 '주증상별 안내'(고른 증상의 약물 선택지)·'지금 쓰는 약'(고른 약)과 겹쳤다.
        return "\n\n".join(blocks)

    def _exposure_link_md(self, relevant, screening) -> str:
        """노출 → 증상 → 질환. 환자가 알려준 질환·증상을 알러젠 노출 상황과 잇는다."""
        out = exposure_links([(self._display_name(a), a) for a, _g in self._collapse(relevant)],
                             screening)
        if not out["links"]:
            return ""
        md = ["### 🔗 노출 → 증상 → 질환, 이렇게 이어집니다",
              "증상은 알러젠에 노출될 때 생깁니다. 처음에 알려주신 질환과 증상을, "
              "문진에서 확인된 노출 상황과 이어 보았습니다."]
        for ln in out["links"]:
            # label 은 _display_name 에서 이미 이스케이프됐다. 나머지 문장에는 항원 이름(OCR)과
            # 환자가 직접 적은 글(유발 요인·기타 질환)이 들어 있다.
            rows = [f"**{ln['label']}**\n", f"- 노출되는 상황: {md_text(ln['exposure'])}"]
            if ln["targets"]:
                rows.append(f"- 이 노출로 생기거나 심해질 수 있는 것(처음에 알려주신 질환·증상): "
                            f"{', '.join(ln['targets'])}")
            if ln["confirmed"]:
                rows.append(f"- 문진에서 확인된 내용: {md_text(ln['confirmed'])}")
            for note in ln["notes"]:
                rows.append(f"- {md_text(note)}")
            md.append("\n".join(rows))
        for text in out["free_text"]:
            md.append(f"- {md_text(text)}")
        return "\n\n".join(md)

    _IMT_ROUTE_KO = {"SCIT": "피하주사(SCIT)", "SLIT": "설하(SLIT)"}
    _IMT_EVIDENCE_KO = {"established": "효과 확립", "supported": "근거 있음(항원 단독 시험은 적음)"}

    def _immunotherapy_md(self, assessments, screening) -> str:
        """💉 면역치료 절. 감작과 증상이 함께 확인된 항원 중 면역치료 가능 항원이 있을 때만 만든다.
        권고가 아니라 '진료에서 상의할 수 있는 선택지'로만 적는다."""
        ks = get_knowledge_service()
        rows = ks.immunotherapy_candidates(assessments)
        if not rows:
            return ""
        md = ["## 💉 면역치료 — 진료에서 상의할 수 있는 선택지",
              "검사에서 양성이고 **노출될 때 증상도 확인된** 알러젠 가운데, 알레르겐 면역치료가 "
              "가능한 항원이 있습니다. 면역치료는 원인 알러젠을 조금씩 늘려 투여해 몸이 덜 반응하게 "
              "만드는 치료이고, 보통 3년 이상 이어 갑니다. 아래는 치료를 시작하라는 권고가 아닙니다. "
              "시작할지는 증상의 정도, 약으로 조절되는 정도, 천식 조절 상태를 보고 "
              "**담당 의료진이 판단**합니다.",
              "| 알러젠 | 방법 | 국내 사용 | 근거 |\n|---|---|---|---|"]
        for r in rows:
            e = r["entry"]
            routes = " · ".join(self._IMT_ROUTE_KO.get(x, x) for x in e.get("routes", []))
            md[-1] += (f"\n| **{e['label_ko']}** | {routes} | {e['korea']['note_ko']} | "
                       f"{self._IMT_EVIDENCE_KO.get(e.get('evidence'), '')}. {e.get('evidence_note_ko', '')} |")
        notes = []
        diseases = set(getattr(screening, "allergic_diseases", None) or [])
        if "asthma" in diseases:
            notes.append("- 천식이 있다고 하셨습니다. 조절되지 않는 중증 천식에서는 면역치료를 하지 않으므로, "
                         "천식 조절 상태를 먼저 확인합니다.")
        if "immunotherapy" in (getattr(screening, "current_medications", None) or []):
            notes.append("- 이미 면역치료를 받고 있다고 하셨습니다. 치료 중인 항원이 위와 같은지 진료에서 확인하세요.")
        if notes:
            md.append("\n".join(notes))
        cites = "; ".join(f"{x['citation']} doi:{x['doi']}" for x in
                          ks.immunotherapy_sources([r["entry"] for r in rows]))
        md.append(f"*출처: {cites}. 국내 사용 가능 여부는 지침 발간 시점 기준이며 이후 달라졌을 수 있습니다.*")
        return "\n\n".join(md)

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
