"""
Allergy Test Analysis System - Streamlit Main Application
알레르기 검사 자동 분석 및 FHIR 변환 시스템
"""

import streamlit as st
import json
import base64
from pathlib import Path
from datetime import datetime
from PIL import Image
import io
import logging
import pandas as pd

# 서비스 임포트
from config.settings import settings
from services.ocr_service import get_ocr_service
from services.fhir_service import get_fhir_service
from services.chatbot_service import get_chatbot_service
from services.report_service import get_report_service
from models.schemas import ProcessingStep, ProcessingStatus, SymptomFeedback, PatientInfo, AllergenResult, InterpretationType, TestType
from utils.file_manager import get_file_manager
from utils.pdf_generator import PDFGenerator

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =============== 페이지 설정 ===============
st.set_page_config(
    page_title="Allergy Test Analysis System",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded"
)

# =============== 세션 상태 초기화 ===============
if 'current_step' not in st.session_state:
    st.session_state.current_step = 0
if 'ocr_result' not in st.session_state:
    st.session_state.ocr_result = None
if 'edited_ocr_result' not in st.session_state:
    st.session_state.edited_ocr_result = None
if 'patient_info' not in st.session_state:
    st.session_state.patient_info = {}
if 'chat_session' not in st.session_state:
    st.session_state.chat_session = None
if 'symptom_feedback' not in st.session_state:
    st.session_state.symptom_feedback = None
if 'fhir_observation' not in st.session_state:
    st.session_state.fhir_observation = None
if 'fhir_allergy' not in st.session_state:
    st.session_state.fhir_allergy = None
if 'report' not in st.session_state:
    st.session_state.report = None
if 'uploaded_image' not in st.session_state:
    st.session_state.uploaded_image = None
if 'chat_messages' not in st.session_state:
    st.session_state.chat_messages = []

# =============== 스타일 설정 ===============
st.markdown("""
<style>
    .main-header {
        text-align: center;
        padding: 2rem;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-radius: 10px;
        margin-bottom: 2rem;
    }
    .step-indicator {
        display: flex;
        justify-content: space-between;
        margin: 2rem 0;
    }
    .step {
        flex: 1;
        text-align: center;
        padding: 1rem;
        border: 2px solid #e0e0e0;
        border-radius: 5px;
        margin: 0 0.5rem;
    }
    .step.active {
        background-color: #667eea;
        color: white;
        border-color: #667eea;
    }
    .step.completed {
        background-color: #48bb78;
        color: white;
        border-color: #48bb78;
    }
    .chat-message {
        padding: 1rem;
        margin: 0.5rem 0;
        border-radius: 10px;
    }
    .user-message {
        background-color: #e3f2fd;
        text-align: right;
    }
    .assistant-message {
        background-color: #f5f5f5;
    }
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {
        background-color: #667eea;
        color: white;
    }
</style>
""", unsafe_allow_html=True)

# =============== 헤더 ===============
st.markdown("""
<div class="main-header">
    <h1>🏥 Allergy Test Analysis System</h1>
    <p>AI-powered OCR Analysis and FHIR Standard Conversion</p>
</div>
""", unsafe_allow_html=True)

# =============== 사이드바 ===============
with st.sidebar:
    st.header("⚙️ Settings")
    
    # 세션 상태에 API 키 저장
    if 'api_key' not in st.session_state:
        st.session_state.api_key = settings.openai_api_key
    
    # API 키 확인
    api_key_status = "✅ Configured" if st.session_state.api_key and st.session_state.api_key != "your_openai_api_key_here" else "❌ Not configured"
    st.info(f"OpenAI API Key: {api_key_status}")
    
    # API 키 입력 필드
    api_key_input = st.text_input(
        "Enter OpenAI API Key", 
        value=st.session_state.api_key if st.session_state.api_key and st.session_state.api_key != "your_openai_api_key_here" else "",
        type="password",
        help="Enter your API key starting with sk-"
    )
    
    if api_key_input and api_key_input != st.session_state.api_key:
        st.session_state.api_key = api_key_input
        settings.openai_api_key = api_key_input
        st.success("✅ API key has been configured!")
        st.rerun()
    
    st.markdown("---")
    
    # 진행 단계 표시
    st.header("📊 Progress")
    steps = [
        "1️⃣ Image Upload & OCR",
        "2️⃣ OCR Result Review/Edit",
        "3️⃣ Patient Information",
        "4️⃣ Symptom Feedback",
        "5️⃣ FHIR Generation",
        "6️⃣ Report Generation"
    ]
    
    for idx, step_name in enumerate(steps):
        if idx < st.session_state.current_step:
            st.write(f"✅ ~~{step_name}~~")
        elif idx == st.session_state.current_step:
            st.write(f"▶️ **{step_name}**")
        else:
            st.write(f"⏸️ {step_name}")
    
    st.markdown("---")
    
    # 리셋 버튼
    if st.button("🔄 Start Over", type="secondary", use_container_width=True):
        for key in ['current_step', 'ocr_result', 'edited_ocr_result', 'patient_info', 'chat_session', 
                   'symptom_feedback', 'fhir_observation', 'fhir_allergy', 'report', 
                   'uploaded_image', 'chat_messages']:
            if key in st.session_state:
                st.session_state[key] = None if key != 'current_step' else 0
                if key == 'patient_info':
                    st.session_state[key] = {}
                elif key == 'chat_messages':
                    st.session_state[key] = []
        st.rerun()

# =============== 메인 컨텐츠 ===============
# 탭 생성
tab_titles = ["1️⃣ OCR Analysis", "2️⃣ Result Review", "3️⃣ Patient Info", "4️⃣ Symptom Feedback", "5️⃣ FHIR Generation", "6️⃣ Report"]
tabs = st.tabs(tab_titles)

# =============== Tab 1: OCR 분석 ===============
with tabs[0]:
    st.header("📷 Step 1: Image Upload and OCR Analysis")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        uploaded_file = st.file_uploader(
            "Select allergy test result image",
            type=['png', 'jpg', 'jpeg'],
            help="SPT (Skin Prick Test) or MAST/UniCAP test result image"
        )
        
        if uploaded_file is not None:
            # 이미지 표시
            image = Image.open(uploaded_file)
            st.session_state.uploaded_image = image
            st.image(image, caption="Uploaded Image", use_container_width=True)
            
            # OCR 분석 버튼
            if st.button("🚀 Start OCR Analysis", type="primary", use_container_width=True):
                # API 키 확인
                if not st.session_state.get('api_key') or st.session_state.api_key == "your_openai_api_key_here":
                    st.error("⚠️ Please set your OpenAI API key first!")
                    st.stop()
                
                with st.spinner("Analyzing image..."):
                    try:
                        # OCR 서비스 호출
                        ocr_service = get_ocr_service(api_key=st.session_state.api_key)
                        ocr_result = ocr_service.extract_from_image(image)
                        
                        # 결과 저장
                        st.session_state.ocr_result = ocr_result
                        st.session_state.edited_ocr_result = None  # 수정된 결과 초기화
                        st.success("✅ OCR analysis completed!")
                        st.session_state.current_step = 1
                        st.rerun()
                    
                    except Exception as e:
                        st.error(f"Error during OCR analysis: {e}")
                        logger.error(f"OCR error: {e}")
    
    with col2:
        st.info("""
        **Supported Formats:**
        - SPT (Skin Prick Test)
        - MAST/UniCAP (Serum Specific IgE)
        
        **Recommendations:**
        - Clear image quality
        - Full table visible
        - 1MB ~ 10MB size
        """)

# =============== Tab 2: OCR 결과 검토 및 수정 ===============
with tabs[1]:
    st.header("📝 Step 2: OCR Result Review and Edit")
    
    if not st.session_state.ocr_result:
        st.warning("Please complete OCR analysis first.")
    else:
        ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
        
        # 기본 정보 표시
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Test Type", ocr_result.test_type.value)
        with col2:
            st.metric("Extracted Items", len(ocr_result.results))
        with col3:
            positive_count = sum(1 for r in ocr_result.results 
                               if r.interpretation and str(r.interpretation).lower() in ['positive', 'p'])
            st.metric("Positive Items", positive_count)
        
        st.markdown("### 🔍 Test Result Edit")
        st.info("You can edit incorrectly recognized data in the table below.")
        
        # 결과를 수정 가능한 데이터프레임으로 변환
        if ocr_result.results:
            results_data = []
            for r in ocr_result.results:
                # 수치 값 결정
                value = r.value or r.mean_mm or 0.0
                
                # 판정 기준 적용
                # SPT: mean_mm 또는 value >= 3.0이면 양성
                # MAST: class >= 1 또는 value >= 0.35면 양성
                if ocr_result.test_type == TestType.SPT:
                    is_positive = value >= 3.0
                elif ocr_result.test_type == TestType.MAST:
                    # MAST의 경우 class 값이나 value로 판정
                    class_val = r.class_value
                    is_positive = False
                    if class_val:
                        try:
                            is_positive = int(class_val) >= 1
                        except:
                            pass
                    if not is_positive and value >= 0.35:
                        is_positive = True
                else:
                    # 기존 interpretation 사용
                    is_positive = r.interpretation and str(r.interpretation).lower() in ['positive', 'p']
                
                results_data.append({
                    "No.": r.index,
                    "Allergen": r.allergen_name,
                    "Korean Name": r.korean_name or "",
                    "Value": value,
                    "Unit": r.unit or "",
                    "Result": "Positive" if is_positive else "Negative"
                })
            
            # 수정 가능한 데이터프레임 생성
            df = pd.DataFrame(results_data)
            
            # 데이터 에디터로 수정 가능하게
            edited_df = st.data_editor(
                df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "No.": st.column_config.NumberColumn("No.", disabled=True),
                    "Allergen": st.column_config.TextColumn("Allergen"),
                    "Korean Name": st.column_config.TextColumn("Korean Name"),
                    "Value": st.column_config.NumberColumn("Value", format="%.2f"),
                    "Unit": st.column_config.TextColumn("Unit"),
                    "Result": st.column_config.SelectboxColumn(
                        "Result",
                        options=["Positive", "Negative"],
                        required=True
                    )
                },
                num_rows="fixed"
            )
            
            # 양성 알레르겐 하이라이트
            positive_rows = edited_df[edited_df["Result"] == "Positive"]
            if not positive_rows.empty:
                st.warning(f"🔴 Positive Allergens ({len(positive_rows)} items):")
                for _, row in positive_rows.iterrows():
                    st.write(f"- {row['Allergen']}: {row['Value']} {row['Unit']}")
            
            # 수정 사항 저장
            col1, col2 = st.columns([1, 1])
            with col2:
                if st.button("✅ Save Changes and Proceed", type="primary", use_container_width=True):
                    # 수정된 데이터를 OCRResult로 변환
                    from copy import deepcopy
                    edited_ocr = deepcopy(ocr_result)
                    
                    # 수정된 결과 반영
                    positive_count = 0
                    for idx, row in edited_df.iterrows():
                        if idx < len(edited_ocr.results):
                            edited_ocr.results[idx].allergen_name = row["Allergen"]
                            edited_ocr.results[idx].korean_name = row["Korean Name"]
                            edited_ocr.results[idx].value = row["Value"]
                            edited_ocr.results[idx].unit = row["Unit"]
                            edited_ocr.results[idx].interpretation = InterpretationType.POSITIVE if row["Result"] == "Positive" else InterpretationType.NEGATIVE
                            
                            # 디버깅: 저장되는 값 확인
                            if row["Result"] == "Positive":
                                positive_count += 1
                                logger.info(f"Step 2 - Saving positive: {row['Allergen']}, interpretation: {edited_ocr.results[idx].interpretation}")
                    
                    logger.info(f"Step 2 - Total positive allergens saved: {positive_count}")
                    st.session_state.edited_ocr_result = edited_ocr
                    st.session_state.current_step = 2
                    
                    # 세션에 저장 확인
                    if hasattr(st.session_state, 'edited_ocr_result'):
                        logger.info(f"Step 2 - edited_ocr_result saved to session_state")
                    else:
                        logger.error("Step 2 - Failed to save edited_ocr_result to session_state")
                    
                    # Step 4를 위한 채팅 세션 초기화 (수정된 결과 반영)
                    if 'chat_session' in st.session_state:
                        st.session_state.chat_session = None
                    if 'chat_messages' in st.session_state:
                        st.session_state.chat_messages = []
                    st.success(f"✅ Changes saved. (Positive: {positive_count} items)")
                    st.rerun()
            
            with col1:
                if st.button("↩️ Reset Changes", type="secondary", use_container_width=True):
                    st.session_state.edited_ocr_result = None
                    st.rerun()

# =============== Tab 3: 환자 정보 ===============
with tabs[2]:
    st.header("👤 Step 3: Patient Information")
    
    if st.session_state.current_step < 1:
        st.warning("Please complete the previous steps first.")
    else:
        # 환자 정보 입력 폼
        with st.form("patient_info_form"):
            col1, col2 = st.columns(2)
            
            with col1:
                patient_name = st.text_input(
                    "Patient Name", 
                    value=st.session_state.patient_info.get('name', ''),
                    placeholder="John Doe"
                )
                patient_age = st.number_input(
                    "Age",
                    min_value=1,
                    max_value=120,
                    value=st.session_state.patient_info.get('age', 30)
                )
            
            with col2:
                patient_gender = st.selectbox(
                    "Gender",
                    options=["M", "F"],
                    format_func=lambda x: 'Male' if x == 'M' else 'Female',
                    index=0 if st.session_state.patient_info.get('gender', 'M') == 'M' else 1
                )
                test_date = st.date_input(
                    "Test Date",
                    value=datetime.now().date()
                )
            
            submitted = st.form_submit_button("✅ Save and Proceed", type="primary", use_container_width=True)
            
            if submitted:
                # 환자 정보 저장
                st.session_state.patient_info = {
                    'name': patient_name,
                    'age': patient_age,
                    'gender': patient_gender,
                    'test_date': test_date.strftime("%Y-%m-%d")
                }
                
                # OCR 결과에 환자 정보 업데이트
                ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
                if ocr_result:
                    ocr_result.patient.name = patient_name
                    ocr_result.patient.age = patient_age
                    ocr_result.patient.gender = patient_gender
                    ocr_result.patient.test_date = test_date.strftime("%Y-%m-%d")
                
                st.session_state.current_step = 3
                st.success("✅ Patient information saved.")
                st.rerun()

# =============== Tab 4: 증상 피드백 ===============
with tabs[3]:
    st.header("💬 Step 4: Symptom Feedback Collection")
    
    if st.session_state.current_step < 3:
        st.warning("Please complete the previous steps first.")
    else:
        # Step 2에서 검토/수정된 OCR 결과 사용
        edited_result = st.session_state.get('edited_ocr_result')
        original_result = st.session_state.get('ocr_result')
        
        # 디버깅: 세션 상태 확인
        logger.info(f"Step 4 - edited_result exists: {edited_result is not None}")
        logger.info(f"Step 4 - original_result exists: {original_result is not None}")
        
        # 수정된 결과가 있으면 사용, 없으면 원본 사용
        ocr_result = edited_result if edited_result else original_result
        
        # 디버깅: 어떤 결과를 사용하는지 확인
        if edited_result:
            logger.info("Step 4 - Using edited OCR result")
        else:
            logger.info("Step 4 - Using original OCR result")
        
        if not ocr_result:
            st.error("OCR 결과를 찾을 수 없습니다. Step 1부터 다시 시작해주세요.")
            st.stop()
        
        # 환자 정보 표시
        patient_info = st.session_state.patient_info
        st.info(f"""
        👤 **환자 정보**
        - 이름: {patient_info['name']}
        - 나이: {patient_info['age']}세
        - 성별: {'남성' if patient_info['gender'] == 'M' else '여성'}
        - 검사일: {patient_info['test_date']}
        """)
        
        # 양성 알레르겐 수집 및 표시
        positive_allergens = []
        grouped_allergens = {}
        
        if ocr_result and ocr_result.results:
            # 디버깅: 전체 결과 확인
            logger.info(f"Step 4 - OCR results count: {len(ocr_result.results)}")
            
            for r in ocr_result.results:
                # 디버깅: 각 결과의 interpretation 확인
                logger.info(f"Allergen: {r.allergen_name}, Interpretation: {r.interpretation}, Type: {type(r.interpretation)}")
                
                # InterpretationType.POSITIVE 또는 문자열 "Positive", "양성" 등 확인
                is_positive = False
                if r.interpretation:
                    if isinstance(r.interpretation, InterpretationType):
                        is_positive = r.interpretation == InterpretationType.POSITIVE
                    else:
                        # 문자열로 된 경우
                        interp_str = str(r.interpretation).lower()
                        is_positive = interp_str in ['positive', 'p', '양성']
                
                if is_positive:
                    # 카테고리 추출
                    category = r.category or "기타"
                    if category not in grouped_allergens:
                        grouped_allergens[category] = []
                    
                    grouped_allergens[category].append({
                        'name': r.allergen_name,
                        'korean': r.korean_name,
                        'value': r.value or r.mean_mm,
                        'unit': r.unit
                    })
                    
                    positive_allergens.append(r.allergen_name)
                    logger.info(f"Added positive allergen: {r.allergen_name}")
        
        logger.info(f"Total positive allergens found: {len(positive_allergens)}")
        
        # 양성 알레르겐 표시
        if grouped_allergens:
            st.markdown("### 🔴 양성 반응 알레르겐 (그룹별)")
            
            cols = st.columns(2)
            col_idx = 0
            
            category_map = {
                "Mite": "🕷️ 진드기",
                "Pollen": "🌸 꽃가루",
                "Mold": "🍄 곰팡이",
                "Animal": "🐾 동물",
                "Insect": "🐛 곤충",
                "Food": "🍽️ 음식",
                "Other": "📌 기타"
            }
            
            for category, allergens in grouped_allergens.items():
                with cols[col_idx % 2]:
                    category_label = category_map.get(category, f"📌 {category}")
                    st.markdown(f"**{category_label}**")
                    for allergen in allergens:
                        korean = f" ({allergen['korean']})" if allergen['korean'] else ""
                        value_str = f" - {allergen['value']} {allergen['unit']}" if allergen['value'] else ""
                        st.write(f"• {allergen['name']}{korean}{value_str}")
                col_idx += 1
            
            st.markdown("---")
        else:
            st.warning("양성 반응을 보인 알레르겐이 없습니다. Step 2로 돌아가서 OCR 결과를 확인해주세요.")
            
            # 디버깅 정보 표시
            with st.expander("🔍 디버깅 정보"):
                if ocr_result and ocr_result.results:
                    st.write(f"전체 알레르겐 수: {len(ocr_result.results)}")
                    for r in ocr_result.results[:5]:  # 처음 5개만 표시
                        st.write(f"- {r.allergen_name}: {r.interpretation} (Type: {type(r.interpretation).__name__})")
                else:
                    st.write("OCR 결과가 없습니다.")
        
        # 채팅 세션 및 메시지 초기화
        if positive_allergens:
            # chat_messages 초기화
            if 'chat_messages' not in st.session_state:
                st.session_state.chat_messages = []
            
            # 챗봇 세션이 없거나, 초기 메시지가 없으면 생성
            if not st.session_state.get('chat_session') or not st.session_state.chat_messages:
                chatbot_service = get_chatbot_service(api_key=st.session_state.get('api_key'))
                
                # 챗봇 세션 시작
                st.session_state.chat_session = chatbot_service.create_session(
                    patient_id=st.session_state.patient_info['name'],
                    ocr_result=ocr_result,
                    patient_name=st.session_state.patient_info['name'],
                    patient_age=st.session_state.patient_info['age'],
                    patient_gender=st.session_state.patient_info['gender']
                )
                
                # 초기 메시지 생성
                initial_msg = f"""안녕하세요, {st.session_state.patient_info['name']}님!
                
위에 표시된 양성 반응 알레르겐들을 확인해주셨나요?

이제 몇 가지 중요한 질문을 드리겠습니다:

**🔍 질문 1:** 위의 양성 반응을 보인 알레르겐 중에서 **실제로 노출되었을 때 증상이 있었던 것**이 있나요?
있다면 어떤 항목인지, 어떤 증상이 있었는지 알려주세요.

예시:
- "진드기에 노출되면 재채기와 콧물이 심해요"
- "고양이를 만지면 눈이 가렵고 충혈돼요"
- "계란을 먹으면 두드러기가 나요"

증상이 없었던 것이나 노출 경험이 없는 것도 함께 알려주시면 더 정확한 관리 계획을 세울 수 있습니다."""
                
                # 초기 메시지 추가
                st.session_state.chat_messages = [
                    {"role": "assistant", "content": initial_msg}
                ]
        
        # 채팅 인터페이스 (양성 알레르겐이 있을 때만)
        if positive_allergens:
            st.markdown("### 💬 증상 확인 대화")
            
            # 기존 메시지 표시
            for message in st.session_state.get('chat_messages', []):
                with st.chat_message(message["role"]):
                    st.write(message["content"])
            
            # 사용자 입력
            user_input = st.chat_input("답변을 입력해주세요...")
            
            if user_input:
                # 사용자 메시지 추가
                st.session_state.chat_messages.append({"role": "user", "content": user_input})
                
                # 챗봇 응답 생성
                with st.spinner("답변 생성 중..."):
                    chatbot_service = get_chatbot_service(api_key=st.session_state.get('api_key'))
                    
                    # 세션이 없으면 생성
                    if not st.session_state.get('chat_session'):
                        st.session_state.chat_session = chatbot_service.create_session(
                            patient_id=st.session_state.patient_info['name'],
                            ocr_result=ocr_result,
                            patient_name=st.session_state.patient_info['name'],
                            patient_age=st.session_state.patient_info['age'],
                            patient_gender=st.session_state.patient_info['gender']
                        )
                    
                    response, symptom_feedback = chatbot_service.process_user_response(
                        st.session_state.chat_session,
                        user_input,
                        ocr_result
                    )
                    
                    # 응답 추가
                    st.session_state.chat_messages.append({"role": "assistant", "content": response})
                    
                    # 증상 피드백 저장
                    if symptom_feedback:
                        st.session_state.symptom_feedback = symptom_feedback
                
                st.rerun()
        else:
            st.info("양성 반응을 보인 알레르겐이 없어서 증상 피드백을 수집할 수 없습니다.")
        
        # 증상 피드백 완료 확인
        if st.session_state.get('symptom_feedback'):
            st.success("✅ 증상 피드백 수집이 완료되었습니다.")
            
            # 수집된 정보 표시
            with st.expander("📋 수집된 증상 정보 확인", expanded=True):
                feedback = st.session_state.symptom_feedback
                st.json({
                    "증상 있음": feedback.exposure_feedback.get("symptomatic", []),
                    "증상 없음": feedback.exposure_feedback.get("asymptomatic", []),
                    "노출 경험 없음": feedback.exposure_feedback.get("unknown_exposure", [])
                })
            
            # 재시작 버튼 추가
            if st.button("🔄 증상 피드백 다시 수집", help="채팅을 다시 시작하여 증상 정보를 재수집합니다."):
                # 증상 관련 세션 데이터 리셋
                st.session_state.symptom_feedback = None
                st.session_state.chat_session = None
                st.session_state.chat_messages = []
                st.rerun()
        
        # 다음 단계 버튼
        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("✅ 다음 단계로 진행", type="primary", use_container_width=True):
                if not st.session_state.get('symptom_feedback'):
                    # 기본 피드백 생성 (모든 양성 알레르겐을 symptomatic으로)
                    if positive_allergens:
                        st.session_state.symptom_feedback = SymptomFeedback(
                            patient_id=st.session_state.patient_info['name'],
                            test_date=st.session_state.patient_info['test_date'],
                            patient_name=st.session_state.patient_info['name'],
                            patient_age=st.session_state.patient_info['age'],
                            patient_gender=st.session_state.patient_info['gender'],
                            exposure_feedback={
                                "symptomatic": positive_allergens,
                                "asymptomatic": [],
                                "unknown_exposure": []
                            }
                        )
                
                st.session_state.current_step = 4
                st.rerun()

# =============== Tab 5: FHIR 생성 ===============
with tabs[4]:
    st.header("🏥 Step 5: FHIR 리소스 생성")
    
    if st.session_state.current_step < 4:
        st.warning("먼저 이전 단계들을 완료해주세요.")
    else:
        fhir_service = get_fhir_service()
        ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
        
        # FHIR Observation Bundle 생성
        if not st.session_state.fhir_observation and ocr_result:
            with st.spinner("FHIR Observation 생성 중..."):
                st.session_state.fhir_observation = fhir_service.create_observation_bundle(
                    ocr_result,
                    patient_id=st.session_state.patient_info['name']
                )
        
        # FHIR AllergyIntolerance Bundle 생성
        if not st.session_state.fhir_allergy and st.session_state.symptom_feedback:
            with st.spinner("FHIR AllergyIntolerance 생성 중..."):
                st.session_state.fhir_allergy = fhir_service.create_allergy_intolerance_bundle(
                    st.session_state.symptom_feedback,
                    ocr_result
                )
        
        # FHIR 리소스 표시
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📊 FHIR Observation Bundle")
            if st.session_state.fhir_observation:
                st.json(st.session_state.fhir_observation)
                
                # 다운로드 버튼
                json_str = json.dumps(st.session_state.fhir_observation, ensure_ascii=False, indent=2)
                st.download_button(
                    label="📥 Observation Bundle 다운로드",
                    data=json_str,
                    file_name=f"{st.session_state.patient_info['name']}_observation_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json"
                )
        
        with col2:
            st.subheader("🔴 FHIR AllergyIntolerance Bundle")
            if st.session_state.fhir_allergy:
                st.json(st.session_state.fhir_allergy)
                
                # 다운로드 버튼
                json_str = json.dumps(st.session_state.fhir_allergy, ensure_ascii=False, indent=2)
                st.download_button(
                    label="📥 AllergyIntolerance Bundle 다운로드",
                    data=json_str,
                    file_name=f"{st.session_state.patient_info['name']}_allergy_{datetime.now().strftime('%Y%m%d')}.json",
                    mime="application/json"
                )
        
        # 다음 단계 버튼
        if st.button("✅ 저장 및 다음 단계로", type="primary", use_container_width=True):
            # 파일 저장
            if st.session_state.fhir_observation:
                file_manager = get_file_manager()
                file_manager.save_json(
                    data=st.session_state.fhir_observation,
                    patient_name=st.session_state.patient_info['name'],
                    test_type=ocr_result.test_type.value,
                    file_type='observation'
                )
            
            if st.session_state.fhir_allergy:
                file_manager = get_file_manager()
                file_manager.save_json(
                    data=st.session_state.fhir_allergy,
                    patient_name=st.session_state.patient_info['name'],
                    test_type=ocr_result.test_type.value,
                    file_type='allergy'
                )
            
            st.session_state.current_step = 5
            st.rerun()

# =============== Tab 6: 리포트 생성 ===============
with tabs[5]:
    st.header("📄 Step 6: 맞춤형 알레르기 관리 리포트")
    
    if st.session_state.current_step < 5:
        st.warning("먼저 이전 단계들을 완료해주세요.")
    else:
        if not st.session_state.report:
            with st.spinner("맞춤형 리포트를 생성하고 있습니다..."):
                try:
                    report_service = get_report_service(api_key=st.session_state.get('api_key'))
                    report = report_service.generate_report(
                        symptom_feedback=st.session_state.symptom_feedback,
                        fhir_allergy_bundle=st.session_state.fhir_allergy
                    )
                    st.session_state.report = report
                    st.success("✅ 리포트 생성이 완료되었습니다!")
                except Exception as e:
                    st.error(f"리포트 생성 중 오류가 발생했습니다: {e}")
                    logger.error(f"Report generation error: {e}")
        
        if st.session_state.report:
            # 리포트 표시
            report = st.session_state.report
            ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
            
            # 환자 정보
            st.markdown(f"""
            ### 환자 정보
            - **이름**: {report.patient_name}
            - **나이**: {report.patient_age}세
            - **성별**: {report.patient_gender}
            - **검사일**: {report.test_date}
            """)
            
            # 주요 알레르겐
            if report.key_allergens:
                st.markdown("### 🔴 주요 양성 알레르겐")
                for allergen in report.key_allergens:
                    st.write(f"- {allergen}")
            
            # 리포트 섹션들
            for section in report.sections:
                st.markdown(f"### {section.icon} {section.title}" if section.icon else f"### {section.title}")
                for content in section.content:
                    st.write(content)
            
            # 다운로드 버튼들
            col1, col2 = st.columns(2)
            
            with col1:
                # Markdown 다운로드
                if report.markdown_content:
                    st.download_button(
                        label="📥 Markdown 리포트 다운로드",
                        data=report.markdown_content,
                        file_name=f"{report.patient_name}_리포트_{datetime.now().strftime('%Y%m%d')}.md",
                        mime="text/markdown"
                    )
            
            with col2:
                # PDF 다운로드
                if report.markdown_content:
                    try:
                        pdf_generator = PDFGenerator()
                        # markdown_to_pdf 메서드는 이제 bytes를 반환
                        pdf_bytes = pdf_generator.markdown_to_pdf(report.markdown_content)
                        
                        if pdf_bytes:
                            st.download_button(
                                label="📥 PDF 리포트 다운로드",
                                data=pdf_bytes,
                                file_name=f"{report.patient_name}_리포트_{datetime.now().strftime('%Y%m%d')}.pdf",
                                mime="application/pdf"
                            )
                            
                            # 파일 저장
                            file_manager = get_file_manager()
                            file_manager.save_pdf(
                                pdf_bytes=pdf_bytes,
                                patient_name=st.session_state.patient_info['name'],
                                test_type=ocr_result.test_type.value
                            )
                    except Exception as e:
                        st.warning(f"PDF 생성 중 오류: {e}")
                        logger.error(f"PDF generation error: {e}")
            
            # 완료 메시지
            st.success("""
            ✅ **모든 처리가 완료되었습니다!**
            
            생성된 파일들:
            - FHIR Observation Bundle
            - FHIR AllergyIntolerance Bundle  
            - 맞춤형 알레르기 관리 리포트 (MD/PDF)
            
            모든 파일은 `output/` 폴더에 저장되었습니다.
            """)

# =============== 진행 상태 표시 ===============
if st.session_state.current_step > 0:
    progress = st.session_state.current_step / 6
    st.progress(progress, text=f"전체 진행률: {int(progress * 100)}%")

# =============== 푸터 ===============
st.markdown("---")
st.markdown("""
<div style="text-align: center; color: gray;">
    <p>Allergy Test Analysis System v1.0 | Powered by OpenAI GPT-4</p>
</div>
""", unsafe_allow_html=True)