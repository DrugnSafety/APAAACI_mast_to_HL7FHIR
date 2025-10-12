"""
Chatbot Service
GPT 기반 증상 피드백 수집 챗봇 서비스
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from openai import OpenAI

from config.settings import settings
from models.schemas import (
    OCRResult, SymptomFeedback, ChatMessage, ChatSession,
    InterpretationType, ExposureStatus
)

# 로거 설정
logger = logging.getLogger(__name__)


class ChatbotService:
    """증상 피드백 수집을 위한 GPT 기반 챗봇"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: OpenAI API 키 (None일 경우 settings에서 가져옴)
        """
        self.api_key = api_key or settings.openai_api_key
        if not self.api_key:
            raise ValueError("OpenAI API 키가 설정되지 않았습니다.")
        
        self.client = OpenAI(api_key=self.api_key)
        self._load_prompt_template()
    
    def _load_prompt_template(self):
        """챗봇 프롬프트 템플릿 로드"""
        try:
            prompt_path = settings.get_chatbot_prompt_path()
            with open(prompt_path, 'r', encoding='utf-8') as f:
                self.prompt_messages = json.load(f)
            logger.info("챗봇 프롬프트 로드 완료")
        except Exception as e:
            logger.error(f"챗봇 프롬프트 로드 실패: {e}")
            self.prompt_messages = self._get_default_prompts()
    
    def _get_default_prompts(self) -> List[Dict[str, str]]:
        """기본 챗봇 프롬프트"""
        return [
            {
                "role": "system",
                "content": """당신은 알레르기 검사 결과를 바탕으로 환자의 증상 경험을 수집하는 의료 상담 AI입니다.
                친절하고 이해하기 쉬운 한국어로 대화하며, 다음 정보를 수집해야 합니다:
                1. 양성 반응을 보인 알레르겐 중 실제로 증상이 있었던 것
                2. 노출되었지만 증상이 없었던 알레르겐
                3. 아직 노출 경험이 없는 알레르겐
                
                대화는 구조화되고 명확해야 하며, 환자가 쉽게 이해할 수 있도록 설명해야 합니다."""
            }
        ]
    
    def create_session(
        self,
        patient_id: str,
        ocr_result: OCRResult,
        patient_name: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None
    ) -> ChatSession:
        """
        새로운 채팅 세션 생성
        
        Args:
            patient_id: 환자 ID
            ocr_result: OCR 결과
            patient_name: 환자 이름
            patient_age: 환자 나이
            patient_gender: 환자 성별
            
        Returns:
            ChatSession 인스턴스
        """
        session = ChatSession(
            session_id=f"chat-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            patient_id=patient_id,
            messages=[]
        )
        
        # 초기 메시지 생성
        positive_allergens = self._get_positive_allergens(ocr_result)
        
        if positive_allergens:
            initial_message = self._create_initial_message(
                positive_allergens, 
                patient_name or ocr_result.patient.name,
                patient_age or ocr_result.patient.age,
                patient_gender or ocr_result.patient.gender
            )
            
            session.messages.append(ChatMessage(
                role="assistant",
                content=initial_message
            ))
        
        return session
    
    def _get_positive_allergens(self, ocr_result: OCRResult) -> List[Dict[str, str]]:
        """양성 알레르겐 추출"""
        positive = []
        for result in ocr_result.results:
            if result.interpretation == InterpretationType.POSITIVE:
                positive.append({
                    "name": result.allergen_name,
                    "korean": result.korean_name,
                    "category": result.category
                })
        return positive
    
    def _create_initial_message(
        self, 
        positive_allergens: List[Dict[str, str]],
        patient_name: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None
    ) -> str:
        """초기 메시지 생성"""
        # 환자 호칭 결정
        if patient_name:
            greeting = f"{patient_name}님, 안녕하세요!"
        else:
            greeting = "안녕하세요!"
        
        # 나이와 성별 정보 추가
        patient_info = ""
        if patient_age:
            patient_info += f" ({patient_age}세"
            if patient_gender:
                gender_str = "남성" if patient_gender in ["M", "남"] else "여성"
                patient_info += f", {gender_str}"
            patient_info += ")"
        
        message = f"""{greeting}{patient_info}
        
알레르기 검사 결과를 확인해드리겠습니다. 검사에서 **양성 반응**을 보인 항목은 다음과 같습니다:

"""
        
        # 카테고리별로 그룹화
        grouped = {}
        for allergen in positive_allergens:
            category = allergen.get('category', 'Other')
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(allergen)
        
        # 카테고리별로 표시
        for category, allergens in grouped.items():
            category_kr = self._get_category_korean(category)
            message += f"**{category_kr}:**\n"
            for i, allergen in enumerate(allergens, 1):
                korean = f" ({allergen['korean']})" if allergen.get('korean') else ""
                message += f"  {i}. {allergen['name']}{korean}\n"
            message += "\n"
        
        message += """이제 몇 가지 질문을 드리겠습니다. 정확한 알레르기 관리 계획을 세우기 위해 중요한 정보입니다.

**첫 번째 질문입니다:**
위의 양성 반응 항목 중에서 **실제로 노출되었을 때 증상이 있었던 것**이 있나요?
있다면 어떤 항목인지 알려주세요. (예: "진드기와 고양이에서 증상이 있었습니다")

증상의 예시:
- 재채기, 콧물, 코막힘
- 눈 가려움, 충혈
- 피부 발진, 두드러기
- 기침, 호흡곤란
- 복통, 설사 (음식 알레르기)"""
        
        return message
    
    def _get_category_korean(self, category: str) -> str:
        """카테고리 한국어 변환"""
        category_map = {
            "Mite": "진드기",
            "Pollen": "꽃가루",
            "Mold": "곰팡이",
            "Animal": "동물",
            "Insect": "곤충",
            "Food": "음식",
            "Other": "기타"
        }
        return category_map.get(category, category)
    
    def process_user_response(
        self,
        session: ChatSession,
        user_message: str,
        ocr_result: OCRResult
    ) -> Tuple[str, Optional[SymptomFeedback]]:
        """
        사용자 응답 처리
        
        Args:
            session: 채팅 세션
            user_message: 사용자 메시지
            ocr_result: OCR 결과
            
        Returns:
            (봇 응답, 증상 피드백 결과)
        """
        # 사용자 메시지 추가
        session.messages.append(ChatMessage(
            role="user",
            content=user_message
        ))
        
        # GPT API 호출
        try:
            # 컨텍스트 준비
            positive_allergens = self._get_positive_allergens(ocr_result)
            allergen_list = [a['name'] for a in positive_allergens]
            
            # 시스템 메시지 + 대화 히스토리
            messages = self.prompt_messages.copy()
            
            # 알레르겐 목록 추가
            messages.append({
                "role": "system",
                "content": f"양성 알레르겐 목록: {', '.join(allergen_list)}"
            })
            
            # 대화 히스토리 추가
            for msg in session.messages[-10:]:  # 최근 10개 메시지만
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            
            # GPT 호출
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=messages,
                temperature=settings.chatbot_temperature,
                max_tokens=1000
            )
            
            bot_response = response.choices[0].message.content
            
            # 봇 응답 추가
            session.messages.append(ChatMessage(
                role="assistant",
                content=bot_response
            ))
            
            # 대화가 완료되었는지 확인하고 피드백 생성
            symptom_feedback = self._extract_feedback_if_complete(
                session, ocr_result, allergen_list
            )
            
            return bot_response, symptom_feedback
            
        except Exception as e:
            logger.error(f"사용자 응답 처리 실패: {e}")
            error_message = "죄송합니다. 처리 중 오류가 발생했습니다. 다시 시도해주세요."
            return error_message, None
    
    def _extract_feedback_if_complete(
        self,
        session: ChatSession,
        ocr_result: OCRResult,
        allergen_list: List[str]
    ) -> Optional[SymptomFeedback]:
        """
        대화가 완료되었으면 증상 피드백 추출
        
        Args:
            session: 채팅 세션
            ocr_result: OCR 결과
            allergen_list: 양성 알레르겐 목록
            
        Returns:
            SymptomFeedback 또는 None
        """
        # 대화 내용 분석하여 필요한 정보가 모두 수집되었는지 확인
        conversation = "\n".join([msg.content for msg in session.messages])
        
        # GPT를 사용하여 대화 내용에서 정보 추출
        try:
            extraction_prompt = f"""
다음 대화 내용에서 알레르기 증상 정보를 추출하여 JSON 형식으로 정리해주세요.
알레르겐 목록: {allergen_list}

대화 내용:
{conversation}

다음 형식으로 출력하세요:
{{
    "collection_complete": true/false,  # 정보 수집이 완료되었는지
    "symptomatic": ["알레르겐1", "알레르겐2"],  # 증상이 있었던 것
    "asymptomatic": ["알레르겐3"],  # 노출되었지만 증상 없었던 것
    "unknown_exposure": ["알레르겐4"]  # 노출 경험이 없는 것
}}

규칙:
- 모든 양성 알레르겐이 세 카테고리 중 하나에 속해야 collection_complete가 true
- 알레르겐 이름은 원래 목록에 있는 것과 정확히 일치해야 함
- JSON만 출력, 다른 텍스트 없음
"""
            
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                messages=[
                    {"role": "system", "content": "You are a data extraction assistant."},
                    {"role": "user", "content": extraction_prompt}
                ],
                temperature=0.1,
                max_tokens=500
            )
            
            content = response.choices[0].message.content
            
            # JSON 파싱
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            result = json.loads(content.strip())
            
            # 수집 완료 확인
            if result.get("collection_complete", False):
                # SymptomFeedback 생성
                feedback = SymptomFeedback(
                    patient_id=session.patient_id,
                    test_date=ocr_result.patient.test_date or datetime.now().strftime("%Y-%m-%d"),
                    patient_name=ocr_result.patient.name,
                    patient_age=ocr_result.patient.age,
                    patient_gender=ocr_result.patient.gender,
                    exposure_feedback={
                        "symptomatic": result.get("symptomatic", []),
                        "asymptomatic": result.get("asymptomatic", []),
                        "unknown_exposure": result.get("unknown_exposure", [])
                    }
                )
                
                session.symptom_feedback = feedback
                return feedback
            
            return None
            
        except Exception as e:
            logger.error(f"피드백 추출 실패: {e}")
            return None
    
    def create_summary_message(self, feedback: SymptomFeedback) -> str:
        """
        수집된 정보 요약 메시지 생성
        
        Args:
            feedback: 증상 피드백
            
        Returns:
            요약 메시지
        """
        message = "📋 **증상 정보 수집이 완료되었습니다.**\n\n"
        
        # 증상 있음
        symptomatic = feedback.exposure_feedback.get("symptomatic", [])
        if symptomatic:
            message += "✅ **증상이 있었던 알레르겐:**\n"
            for item in symptomatic:
                message += f"  • {item}\n"
            message += "\n"
        
        # 증상 없음
        asymptomatic = feedback.exposure_feedback.get("asymptomatic", [])
        if asymptomatic:
            message += "⚪ **노출되었지만 증상이 없었던 알레르겐:**\n"
            for item in asymptomatic:
                message += f"  • {item}\n"
            message += "\n"
        
        # 노출 없음
        unknown = feedback.exposure_feedback.get("unknown_exposure", [])
        if unknown:
            message += "❓ **아직 노출 경험이 없는 알레르겐:**\n"
            for item in unknown:
                message += f"  • {item}\n"
            message += "\n"
        
        message += """이 정보를 바탕으로 맞춤형 알레르기 관리 계획을 작성해드리겠습니다.
        
다음 단계에서는:
1. HL7 FHIR 표준 형식으로 의료 기록을 생성합니다
2. 개인 맞춤형 알레르기 관리 리포트를 작성합니다

감사합니다! 😊"""
        
        return message


# 싱글톤 인스턴스
_chatbot_service: Optional[ChatbotService] = None
_last_api_key: Optional[str] = None


def get_chatbot_service(api_key: Optional[str] = None) -> ChatbotService:
    """챗봇 서비스 싱글톤 인스턴스 반환 (API 키 변경 시 재생성)"""
    global _chatbot_service, _last_api_key
    
    # API 키 결정
    current_key = api_key or settings.openai_api_key
    
    # API 키가 변경되었거나 서비스가 없으면 새로 생성
    if _chatbot_service is None or _last_api_key != current_key:
        _chatbot_service = ChatbotService(api_key=current_key)
        _last_api_key = current_key
        
    return _chatbot_service
