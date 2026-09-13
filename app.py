"""
알레르기 검사결과 환자용 리포트 플랫폼 - Streamlit 메인 애플리케이션

흐름:
  1) 검사지 업로드 & OCR + OCR 점검/수정
  2) 환자 정보 & 스크리닝 문진 (알레르기 질환력·약제·증상 패턴/침범 장기)
  3) 양성 알러젠 backdata + 임상적 의미 감별 (감작 vs 실제 알레르기)
  4) 환자 맞춤 리포트 & 카드뉴스
  5) HL7 FHIR 표준 변환 (Observation / AllergyIntolerance)
"""

import json
import logging
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from config.settings import settings
from models.schemas import (
    TestType, InterpretationType, ScreeningProfile, SymptomSeasonPattern,
    ClinicalRelevance, SymptomFeedback,
)
from services.ocr_service import get_ocr_service
from services.fhir_service import get_fhir_service
from services.report_service import get_report_service
from services.knowledge_service import get_knowledge_service
from services.relevance_service import (
    get_relevance_service, ANSWER_OPTIONS, ANSWER_LABELS_KO,
)
from services.screening_service import (
    get_screening_service, MONTH_LABELS_KO, SEASON_PATTERN_LABELS_KO,
)
from services.cardnews_service import get_cardnews_service
from utils.file_manager import get_file_manager
from utils.pdf_generator import PDFGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(page_title="알레르기 검사결과 환자 리포트", page_icon="🌿",
                   layout="wide", initial_sidebar_state="expanded")

# ---------------- 세션 상태 ----------------
_DEFAULTS = {
    "current_step": 0,
    "uploaded_image": None,
    "ocr_result": None,
    "edited_ocr_result": None,
    "patient_info": {},
    "screening": None,
    "relevance_result": None,
    "patient_report": None,
    "cardnews_html": None,
    "fhir_observation": None,
    "fhir_allergy": None,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

CATEGORY_KO = {
    "mite": "🛏️ 집먼지진드기", "animal": "🐾 동물", "pollen_tree": "🌳 나무 꽃가루",
    "pollen_grass": "🌾 잔디 꽃가루", "pollen_weed": "🍂 잡초 꽃가루", "mold": "🍄 곰팡이",
    "insect": "🪳 곤충", "food": "🍽️ 음식", "other": "📌 기타",
}
RELEVANCE_META = {
    ClinicalRelevance.CLINICALLY_RELEVANT: ("🔴 실제 주의", "#d1373a"),
    ClinicalRelevance.SENSITIZED_ONLY: ("⚪ 감작만", "#5a6472"),
    ClinicalRelevance.INDETERMINATE: ("🟡 관찰 필요", "#b6842a"),
    ClinicalRelevance.NOT_ASSESSED: ("⬜ 미평가", "#999999"),
}

# ---------------- 스타일 ----------------
st.markdown("""
<style>
.main-header{text-align:center;padding:1.6rem;background:linear-gradient(135deg,#5b6ff0 0%,#8a4dd6 100%);
color:#fff;border-radius:12px;margin-bottom:1.2rem;}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700;color:#fff;}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="main-header">
  <h1>🌿 알레르기 검사결과 환자용 리포트</h1>
  <p>검사 양성이 곧 알레르기는 아닙니다 — 실제 증상과 연결해 드립니다</p>
</div>
""", unsafe_allow_html=True)


def _api_ready():
    key = st.session_state.get("api_key")
    return key and key != "your_openai_api_key_here"


# ---------------- 사이드바 ----------------
with st.sidebar:
    st.header("⚙️ 설정")
    if "api_key" not in st.session_state:
        st.session_state.api_key = settings.openai_api_key
    status = "✅ 설정됨" if _api_ready() else "❌ 미설정"
    st.info(f"OpenAI API Key: {status}")
    api_key_input = st.text_input("OpenAI API Key 입력", type="password",
                                  value=st.session_state.api_key if _api_ready() else "",
                                  help="OCR 및 리포트 생성에 사용됩니다 (sk-...)")
    if api_key_input and api_key_input != st.session_state.api_key:
        st.session_state.api_key = api_key_input
        settings.openai_api_key = api_key_input
        st.success("API 키가 설정되었습니다!")
        st.rerun()

    st.markdown("---")
    st.header("📊 진행 단계")
    steps = ["1️⃣ 검사지 업로드 & OCR", "2️⃣ 환자정보 & 스크리닝",
             "3️⃣ 양성 알러젠 감별", "4️⃣ 리포트 & 카드뉴스", "5️⃣ FHIR 변환"]
    for idx, name in enumerate(steps):
        if idx < st.session_state.current_step:
            st.write(f"✅ ~~{name}~~")
        elif idx == st.session_state.current_step:
            st.write(f"▶️ **{name}**")
        else:
            st.write(f"⏸️ {name}")

    st.markdown("---")
    kb = get_knowledge_service()
    st.caption(f"📚 알러젠 지식베이스: {kb.stats()['total']}종 (v{kb.stats().get('version')})")
    if st.button("🔄 처음부터 다시", use_container_width=True):
        for k, v in _DEFAULTS.items():
            st.session_state[k] = v.copy() if isinstance(v, dict) else v
        st.rerun()

tab_titles = ["1️⃣ 업로드 & OCR", "2️⃣ 환자정보 & 스크리닝",
              "3️⃣ 양성 알러젠 감별", "4️⃣ 리포트 & 카드뉴스", "5️⃣ FHIR"]
tabs = st.tabs(tab_titles)


# =====================================================================
# Step 1: 업로드 & OCR + 점검
# =====================================================================
with tabs[0]:
    st.header("📷 1단계. 검사지 업로드 및 OCR")
    col1, col2 = st.columns([2, 1])
    with col1:
        uploaded_file = st.file_uploader("알레르기 검사 결과지 이미지",
                                         type=["png", "jpg", "jpeg"],
                                         help="피부반응검사(SPT), MAST, UniCAP 결과지")
        if uploaded_file is not None:
            image = Image.open(uploaded_file)
            st.session_state.uploaded_image = image
            st.image(image, caption="업로드된 검사지", use_container_width=True)
            if st.button("🚀 OCR 분석 시작", type="primary", use_container_width=True):
                if not _api_ready():
                    st.error("먼저 OpenAI API 키를 설정해주세요.")
                    st.stop()
                with st.spinner("이미지에서 검사 결과를 추출하는 중..."):
                    try:
                        ocr = get_ocr_service(api_key=st.session_state.api_key)
                        st.session_state.ocr_result = ocr.extract_from_image(image)
                        st.session_state.edited_ocr_result = None
                        st.success("OCR 분석 완료! 아래에서 결과를 점검해주세요.")
                        if st.session_state.current_step < 1:
                            st.session_state.current_step = 1
                        st.rerun()
                    except Exception as e:
                        st.error(f"OCR 분석 오류: {e}")
                        logger.error(f"OCR error: {e}")
    with col2:
        st.info("**지원 검사**\n- 피부반응검사(SPT)\n- MAST (혈청 특이 IgE)\n- UniCAP/ImmunoCAP\n\n"
                "**권장**\n- 표 전체가 선명하게 보이는 이미지\n- 1~10MB")

    # OCR 점검/수정
    if st.session_state.ocr_result:
        st.markdown("---")
        st.subheader("🔍 OCR 결과 점검 및 수정")
        st.caption("OCR이 잘못 읽은 값이 있으면 아래 표에서 직접 수정하세요. 양성/음성 판정도 조정할 수 있습니다.")
        ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result

        c1, c2, c3 = st.columns(3)
        _tt_options = [t.value for t in TestType]
        _tt_index = _tt_options.index(ocr_result.test_type.value) if ocr_result.test_type.value in _tt_options else 0
        selected_test_type = c1.selectbox(
            "검사 종류", _tt_options, index=_tt_index,
            help="OCR이 검사 종류를 잘못 인식했다면 여기서 바로잡으세요. (SPT=팽진 mm, MAST/UniCAP=특이 IgE kU/L)")
        c2.metric("추출 항목", len(ocr_result.results))
        rs = get_relevance_service()
        pos0 = sum(1 for r in ocr_result.results if rs._is_positive(r, ocr_result.test_type))
        c3.metric("양성(자동판정)", pos0)

        _table_columns = ["No.", "알러젠", "한글명", "수치", "단위", "Class", "판정"]
        rows = []
        for r in ocr_result.results:
            value = r.value if r.value is not None else r.mean_mm
            rows.append({
                "No.": r.index, "알러젠": r.allergen_name, "한글명": r.korean_name or "",
                "수치": value if value is not None else 0.0, "단위": r.unit or "",
                "Class": str(r.class_value) if r.class_value is not None else "",
                "판정": "양성" if rs._is_positive(r, ocr_result.test_type) else "음성",
            })

        if not rows:
            st.error("⚠️ OCR이 이미지에서 알러젠 항목을 하나도 인식하지 못했습니다. "
                     "이미지가 선명한지, 표 전체가 잘리지 않고 보이는지 확인 후 1단계에서 다시 업로드해주세요.")
            edited_df = pd.DataFrame(columns=_table_columns)
        else:
            df = pd.DataFrame(rows, columns=_table_columns)
            edited_df = st.data_editor(
                df, use_container_width=True, hide_index=True, num_rows="fixed",
                column_config={
                    "No.": st.column_config.NumberColumn("No.", disabled=True),
                    "수치": st.column_config.NumberColumn("수치", format="%.2f"),
                    "판정": st.column_config.SelectboxColumn("판정", options=["양성", "음성"], required=True),
                },
            )
            pos_rows = edited_df[edited_df["판정"] == "양성"]
            if not pos_rows.empty:
                st.warning(f"🔴 양성 알러젠 {len(pos_rows)}개: " +
                           ", ".join(f"{row['알러젠']}" for _, row in pos_rows.iterrows()))

        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("↩️ 수정 취소", use_container_width=True):
                st.session_state.edited_ocr_result = None
                st.rerun()
        with cc2:
            if st.button("✅ 확정하고 다음 단계로", type="primary", use_container_width=True,
                         disabled=not rows):
                from copy import deepcopy
                edited = deepcopy(ocr_result)
                edited.test_type = TestType(selected_test_type)
                for idx, row in edited_df.iterrows():
                    if idx < len(edited.results):
                        edited.results[idx].allergen_name = row["알러젠"]
                        edited.results[idx].korean_name = row["한글명"] or None
                        edited.results[idx].value = row["수치"]
                        edited.results[idx].unit = row["단위"] or None
                        edited.results[idx].interpretation = (
                            InterpretationType.POSITIVE if row["판정"] == "양성"
                            else InterpretationType.NEGATIVE)
                st.session_state.edited_ocr_result = edited
                # 이후 단계 결과 초기화(재평가)
                st.session_state.relevance_result = None
                st.session_state.patient_report = None
                st.session_state.cardnews_html = None
                st.session_state.fhir_observation = None
                st.session_state.fhir_allergy = None
                st.session_state.current_step = max(st.session_state.current_step, 1)
                st.success("OCR 결과가 확정되었습니다. '2단계' 탭으로 이동하세요.")
                st.rerun()


# =====================================================================
# Step 2: 환자 정보 & 스크리닝
# =====================================================================
with tabs[1]:
    st.header("🧑‍⚕️ 2단계. 환자 정보 & 스크리닝 문진")
    if st.session_state.current_step < 1:
        st.warning("먼저 1단계(OCR)를 완료해주세요.")
    else:
        sc_svc = get_screening_service()
        with st.form("screening_form"):
            st.subheader("👤 기본 정보")
            b1, b2, b3, b4 = st.columns(4)
            name = b1.text_input("이름", value=st.session_state.patient_info.get("name", ""))
            age = b2.number_input("나이", 1, 120, value=st.session_state.patient_info.get("age", 30))
            gender = b3.selectbox("성별", ["M", "F"],
                                  format_func=lambda x: "남성" if x == "M" else "여성",
                                  index=0 if st.session_state.patient_info.get("gender", "M") == "M" else 1)
            test_date = b4.date_input("검사일", value=datetime.now().date())

            st.markdown("---")
            st.subheader("🩺 알레르기 질환력")
            st.caption("현재 진단받았거나 의심되는 알레르기 질환을 모두 선택하세요.")
            disease_opts = sc_svc.disease_options()
            disease_sel = st.multiselect(
                "질환", options=[o["code"] for o in disease_opts],
                format_func=lambda c: next((o["label"] for o in disease_opts if o["code"] == c), c),
                default=st.session_state.screening.allergic_diseases if st.session_state.screening else [],
            )

            st.subheader("💊 현재 복용 약제")
            st.caption("특히 항히스타민제는 피부반응검사(SPT) 결과에 영향을 줄 수 있습니다.")
            med_opts = sc_svc.medication_options()
            med_sel = st.multiselect(
                "약제", options=[o["code"] for o in med_opts],
                format_func=lambda c: next((o["label"] for o in med_opts if o["code"] == c), c),
                default=st.session_state.screening.current_medications if st.session_state.screening else [],
            )
            antihist_recent = st.checkbox("최근 5~7일 내 항히스타민제를 복용했다",
                                          value=bool(st.session_state.screening.antihistamine_recent) if st.session_state.screening else False)

            st.markdown("---")
            st.subheader("🤧 증상 특성")
            symptom_present = st.radio("현재 알레르기 증상이 있나요?", [True, False],
                                       format_func=lambda x: "있음" if x else "없음", horizontal=True,
                                       index=0 if (st.session_state.screening.symptom_present if st.session_state.screening else True) else 1)
            season_pattern = st.selectbox(
                "증상의 계절 패턴", list(SEASON_PATTERN_LABELS_KO.keys()),
                format_func=lambda c: SEASON_PATTERN_LABELS_KO[c],
                index=list(SEASON_PATTERN_LABELS_KO.keys()).index(
                    st.session_state.screening.season_pattern.value) if st.session_state.screening else 3,
            )
            worse_months = st.multiselect(
                "증상이 특히 심해지는 달 (계절성인 경우)", options=list(range(1, 13)),
                format_func=lambda m: MONTH_LABELS_KO[m - 1],
                default=st.session_state.screening.worse_months if st.session_state.screening else [],
            )
            organ_opts = sc_svc.organ_system_options()
            organ_sel = st.multiselect(
                "노출 시 나타나는 증상 부위(계열)", options=[o["code"] for o in organ_opts],
                format_func=lambda c: next((o["label"] for o in organ_opts if o["code"] == c), c),
                default=st.session_state.screening.organ_systems if st.session_state.screening else [],
            )
            severity = st.select_slider("증상 정도", options=["mild", "moderate", "severe"],
                                        format_func=lambda s: {"mild": "경증", "moderate": "중등도", "severe": "중증"}[s],
                                        value=st.session_state.screening.symptom_severity if (st.session_state.screening and st.session_state.screening.symptom_severity) else "moderate")
            triggers = st.text_area("스스로 느끼는 유발 요인(자유 기재)",
                                    value=st.session_state.screening.triggers_free_text if st.session_state.screening else "")

            submitted = st.form_submit_button("✅ 저장하고 다음 단계로", type="primary", use_container_width=True)
            if submitted:
                st.session_state.patient_info = {
                    "name": name or "환자", "age": int(age), "gender": gender,
                    "test_date": test_date.strftime("%Y-%m-%d"),
                }
                st.session_state.screening = ScreeningProfile(
                    allergic_diseases=disease_sel, current_medications=med_sel,
                    antihistamine_recent=antihist_recent, symptom_present=bool(symptom_present),
                    season_pattern=SymptomSeasonPattern(season_pattern), worse_months=worse_months,
                    organ_systems=organ_sel, symptom_severity=severity,
                    perennial_symptom=(season_pattern in ("perennial", "both")),
                    triggers_free_text=triggers or None,
                )
                # 환자정보를 OCR 결과에도 반영
                ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
                if ocr_result:
                    ocr_result.patient.name = st.session_state.patient_info["name"]
                    ocr_result.patient.age = st.session_state.patient_info["age"]
                    ocr_result.patient.gender = gender
                    ocr_result.patient.test_date = st.session_state.patient_info["test_date"]
                # 감별 초기화(스크리닝 반영 재계산)
                st.session_state.relevance_result = None
                st.session_state.current_step = max(st.session_state.current_step, 2)
                st.success("저장되었습니다. '3단계' 탭으로 이동하세요.")
                st.rerun()

        # 스크리닝 요약/주의사항
        if st.session_state.screening:
            summ = sc_svc.summarize(st.session_state.screening)
            for flag in summ["flags"]:
                st.warning(f"⚠️ {flag}")


# =====================================================================
# Step 3: 양성 알러젠 backdata + 감별
# =====================================================================
with tabs[2]:
    st.header("🧬 3단계. 양성 알러젠 backdata & 임상적 의미 감별")
    if st.session_state.current_step < 2:
        st.warning("먼저 2단계(환자정보 & 스크리닝)를 완료해주세요.")
    else:
        ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
        rs = get_relevance_service()

        if st.session_state.relevance_result is None:
            st.session_state.relevance_result = rs.build_assessments(ocr_result, st.session_state.screening)

        result = st.session_state.relevance_result
        assessments = result.assessments

        if not assessments:
            st.warning("양성으로 판정된 알러젠이 없습니다. 1단계에서 OCR 결과의 판정을 확인해주세요.")
        else:
            st.info("💡 **핵심:** 검사 양성은 '감작'을 뜻할 뿐입니다. 각 알러젠에 대해 아래 질문에 답하면, "
                    "**실제로 증상을 유발하는 알러젠**과 **감작만 된 알러젠**을 구분해 드립니다.")
            st.markdown(f"**양성 알러젠 {len(assessments)}개**를 감별합니다.")

            for i, a in enumerate(assessments):
                kb = a.kb or {}
                cat_label = CATEGORY_KO.get(a.category, "📌 기타")
                strength_ko = {"weak": "약한 양성", "moderate": "중등도 양성", "strong": "강한 양성"}.get(a.strength or "", "")
                title = f"{cat_label} · {a.korean_name or a.allergen_name}"
                if strength_ko:
                    title += f"  ({strength_ko})"
                with st.expander(title, expanded=(i == 0)):
                    # backdata
                    meta = []
                    if kb.get("season_label_ko"):
                        meta.append(f"🗓️ 주요 시기: {kb['season_label_ko']}")
                    io = {"indoor": "실내", "outdoor": "실외", "both": "실내·외"}.get(kb.get("indoor_outdoor"), "")
                    if io:
                        meta.append(f"📍 노출: {io}")
                    if a.season_overlap is True:
                        meta.append("🔗 환자 악화 시기와 시즌 일치")
                    if meta:
                        st.caption(" · ".join(meta))
                    if kb.get("biology_ko"):
                        st.markdown(f"**특성·생활사:** {kb['biology_ko']}")
                    if kb.get("exposure_environment_ko"):
                        st.markdown(f"**노출 환경:** {kb['exposure_environment_ko']}")
                    if kb.get("cross_reactivity_ko"):
                        st.markdown(f"**교차반응:** {kb['cross_reactivity_ko']}")
                    if kb.get("oral_allergy_syndrome_ko"):
                        st.markdown(f"**구강알레르기증후군:** {kb['oral_allergy_syndrome_ko']}")
                    src = kb.get("source")
                    if src == "wikipedia":
                        st.caption("ℹ️ 일부 정보는 외부 검색(Wikipedia)으로 보강되었습니다.")
                    elif src == "category_default":
                        st.caption("ℹ️ 지식베이스에 없는 알러젠이라 카테고리 기본 정보를 사용합니다.")

                    st.markdown("**감별 질문**")
                    hints = rs.probe_hints(a)
                    if hints:
                        st.caption("참고: " + " / ".join(hints[:3]))
                    questions = rs.build_questions(a)
                    for q in questions:
                        cur = a.answers.get(q["id"], "unsure")
                        choice = st.radio(
                            q["question"], ANSWER_OPTIONS,
                            format_func=lambda c: ANSWER_LABELS_KO[c],
                            index=ANSWER_OPTIONS.index(cur) if cur in ANSWER_OPTIONS else 2,
                            horizontal=True, key=f"q_{i}_{q['id']}",
                        )
                        a.answers[q["id"]] = choice

            st.markdown("---")
            if st.button("🧠 임상적 의미 판정하기", type="primary", use_container_width=True):
                rs.classify_all(result)
                st.session_state.patient_report = None
                st.session_state.cardnews_html = None
                st.session_state.current_step = max(st.session_state.current_step, 3)
                st.rerun()

            # 판정 결과 표시
            if any(a.relevance != ClinicalRelevance.NOT_ASSESSED for a in assessments):
                st.markdown("### 📋 감별 결과")
                summary = rs.summarize(result)
                s1, s2, s3 = st.columns(3)
                s1.metric("🔴 실제 주의", summary["counts"]["clinically_relevant"])
                s2.metric("⚪ 감작만", summary["counts"]["sensitized_only"])
                s3.metric("🟡 관찰 필요", summary["counts"]["indeterminate"])
                for a in assessments:
                    label, color = RELEVANCE_META.get(a.relevance, ("", "#999"))
                    st.markdown(
                        f'<span class="pill" style="background:{color}">{label}</span> '
                        f'**{a.korean_name or a.allergen_name}**', unsafe_allow_html=True)
                    if a.rationale_ko:
                        st.caption(a.rationale_ko)
                st.success("판정 완료! '4단계' 탭에서 맞춤 리포트와 카드뉴스를 생성하세요.")


# =====================================================================
# Step 4: 리포트 & 카드뉴스
# =====================================================================
with tabs[3]:
    st.header("📄 4단계. 환자 맞춤 리포트 & 카드뉴스")
    result = st.session_state.relevance_result
    assessed = result and any(a.relevance != ClinicalRelevance.NOT_ASSESSED for a in result.assessments)
    if st.session_state.current_step < 3 or not assessed:
        st.warning("먼저 3단계에서 '임상적 의미 판정하기'를 완료해주세요.")
    else:
        colA, colB = st.columns(2)
        with colA:
            if st.button("📝 맞춤 리포트 생성", type="primary", use_container_width=True):
                with st.spinner("리포트를 작성하는 중..."):
                    try:
                        report_svc = get_report_service(api_key=st.session_state.get("api_key")) if _api_ready() else None
                    except Exception:
                        report_svc = None
                    try:
                        if report_svc is None:
                            # API 없이 결정론적 리포트
                            from services.report_service import ReportService
                            svc = ReportService.__new__(ReportService)
                            svc.api_key = None
                            md = ReportService.build_patient_report_markdown(
                                svc, result, st.session_state.patient_info, st.session_state.screening)
                            report = ReportService._parse_patient_markdown(
                                svc, md, result, st.session_state.patient_info)
                        else:
                            report = report_svc.generate_patient_report(
                                result, st.session_state.patient_info, st.session_state.screening,
                                use_llm=_api_ready())
                        st.session_state.patient_report = report
                        st.success("리포트 생성 완료!")
                    except Exception as e:
                        st.error(f"리포트 생성 오류: {e}")
                        logger.error(f"report error: {e}")
        with colB:
            if st.button("🎨 카드뉴스 생성", use_container_width=True):
                cn = get_cardnews_service()
                st.session_state.cardnews_html = cn.generate_html(
                    result, st.session_state.patient_info, st.session_state.screening)
                st.success("카드뉴스 생성 완료!")

        # 카드뉴스
        if st.session_state.cardnews_html:
            st.markdown("### 🎨 카드뉴스")
            components.html(st.session_state.cardnews_html, height=520, scrolling=True)
            st.download_button("📥 카드뉴스 HTML 다운로드", data=st.session_state.cardnews_html,
                               file_name=f"{st.session_state.patient_info.get('name','환자')}_카드뉴스.html",
                               mime="text/html")

        # 리포트
        if st.session_state.patient_report:
            report = st.session_state.patient_report
            st.markdown("### 📄 맞춤 리포트")
            st.markdown(report.markdown_content)

            d1, d2 = st.columns(2)
            with d1:
                st.download_button("📥 Markdown 다운로드", data=report.markdown_content or "",
                                   file_name=f"{report.patient_name}_리포트_{datetime.now():%Y%m%d}.md",
                                   mime="text/markdown")
            with d2:
                try:
                    pdf_bytes = PDFGenerator().markdown_to_pdf(report.markdown_content or "")
                    if pdf_bytes:
                        st.download_button("📥 PDF 다운로드", data=pdf_bytes,
                                           file_name=f"{report.patient_name}_리포트_{datetime.now():%Y%m%d}.pdf",
                                           mime="application/pdf")
                except Exception as e:
                    st.caption(f"PDF 생성 불가: {e}")
            st.session_state.current_step = max(st.session_state.current_step, 4)


# =====================================================================
# Step 5: FHIR 변환
# =====================================================================
def _relevance_to_symptom_feedback(result, patient_info) -> SymptomFeedback:
    """감별 결과 → SymptomFeedback (기존 FHIR 빌더 호환).
    clinically_relevant → symptomatic, sensitized_only → asymptomatic, indeterminate → unknown_exposure"""
    sym, asym, unk = [], [], []
    for a in result.assessments:
        nm = a.allergen_name
        if a.relevance == ClinicalRelevance.CLINICALLY_RELEVANT:
            sym.append(nm)
        elif a.relevance == ClinicalRelevance.SENSITIZED_ONLY:
            asym.append(nm)
        else:
            unk.append(nm)
    return SymptomFeedback(
        patient_id=patient_info.get("name", "환자"),
        test_date=patient_info.get("test_date") or datetime.now().strftime("%Y-%m-%d"),
        patient_name=patient_info.get("name"), patient_age=patient_info.get("age"),
        patient_gender=patient_info.get("gender"),
        exposure_feedback={"symptomatic": sym, "asymptomatic": asym, "unknown_exposure": unk},
    )


with tabs[4]:
    st.header("🏥 5단계. HL7 FHIR 표준 변환")
    result = st.session_state.relevance_result
    assessed = result and any(a.relevance != ClinicalRelevance.NOT_ASSESSED for a in result.assessments)
    if not assessed:
        st.warning("먼저 3단계 감별을 완료해주세요.")
    else:
        ocr_result = st.session_state.edited_ocr_result or st.session_state.ocr_result
        fhir = get_fhir_service()
        feedback = _relevance_to_symptom_feedback(result, st.session_state.patient_info)

        if st.session_state.fhir_observation is None:
            st.session_state.fhir_observation = fhir.create_observation_bundle(
                ocr_result, patient_id=st.session_state.patient_info.get("name"))
        if st.session_state.fhir_allergy is None:
            st.session_state.fhir_allergy = fhir.create_allergy_intolerance_bundle(feedback, ocr_result)

        st.caption("AllergyIntolerance는 '실제 주의(임상적으로 의미 있는)'로 판정된 알러젠만 confirmed로 생성됩니다.")
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("📊 Observation Bundle")
            st.json(st.session_state.fhir_observation, expanded=False)
            st.download_button("📥 Observation 다운로드",
                               data=json.dumps(st.session_state.fhir_observation, ensure_ascii=False, indent=2),
                               file_name=f"{st.session_state.patient_info.get('name','환자')}_observation.json",
                               mime="application/json")
        with col2:
            st.subheader("🔴 AllergyIntolerance Bundle")
            st.json(st.session_state.fhir_allergy, expanded=False)
            st.download_button("📥 AllergyIntolerance 다운로드",
                               data=json.dumps(st.session_state.fhir_allergy, ensure_ascii=False, indent=2),
                               file_name=f"{st.session_state.patient_info.get('name','환자')}_allergy.json",
                               mime="application/json")

        if st.button("💾 결과 파일 저장(output/)", use_container_width=True):
            fm = get_file_manager()
            fm.save_json(st.session_state.fhir_observation, st.session_state.patient_info.get("name", "환자"),
                         ocr_result.test_type.value, "observation")
            fm.save_json(st.session_state.fhir_allergy, st.session_state.patient_info.get("name", "환자"),
                         ocr_result.test_type.value, "allergy")
            st.success("output/ 폴더에 저장되었습니다.")


# ---------------- 진행률 ----------------
if st.session_state.current_step > 0:
    progress = min(st.session_state.current_step / 5, 1.0)
    st.progress(progress, text=f"전체 진행률: {int(progress * 100)}%")

st.markdown("---")
st.markdown('<div style="text-align:center;color:gray">알레르기 검사결과 환자 리포트 플랫폼 · '
            '검사 양성 ≠ 알레르기, 실제 증상과 연결합니다</div>', unsafe_allow_html=True)
