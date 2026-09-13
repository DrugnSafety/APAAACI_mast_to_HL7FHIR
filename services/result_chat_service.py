"""Result Chat Service — 환자가 자기 검사 결과에 대해 묻고 답을 받는 상담 챗봇.

설계 원칙
  1) **근거 고정(grounding)**: 답변은 이 환자의 판정 결과·지식베이스·문진 답변에서만 나온다.
     컨텍스트에 없는 내용은 지어내지 않고 "이 결과만으로는 알 수 없다"고 답한다.
  2) **진료를 대신하지 않는다**: 진단·처방·약 용량 조절을 하지 않는다.
  3) **응급 신호 우선**: 호흡곤란·아나필락시스 등이 언급되면 다른 설명보다 먼저 응급 안내를 한다.
  4) **키가 없어도 쓸 수 있다**: 자주 묻는 질문은 LLM 없이 판정 데이터에서 결정론적으로 답한다.

기존 `chatbot_service.py` 는 용도가 다르다(FHIR 번들에서 노출 피드백을 수집해 JSON 을 만드는 도구).
이 모듈은 판정이 끝난 뒤 환자 질문에 답하는 상담용이다.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from models.schemas import ClinicalRelevance
from services.knowledge_service import normalize_category
from config.settings import settings

logger = logging.getLogger(__name__)

MAX_HISTORY = 12          # 최근 대화만 유지(비용·표류 방지)
MAX_QUESTION_CHARS = 600

# 응급 신호 — 질문에 이 표현이 있으면 설명보다 먼저 안내한다
EMERGENCY_PATTERNS = re.compile(
    r"호흡곤란|숨이\s*안|숨쉬기|숨을 못|아나필락시스|기도\s*막|목이 붓|의식|쓰러|혈압.*떨어|"
    r"anaphyla|can'?t breathe|trouble breathing|throat clos|passed out|"
    r"呼吸困难|喘不上气|过敏性休克|窒息", re.I)

EMERGENCY_TEXT = {
    "ko": ("🚨 지금 호흡이 힘들거나 온몸 두드러기·어지럼이 함께 있다면 아나필락시스일 수 있습니다. "
           "이 채팅으로 시간을 쓰지 마시고 즉시 119에 연락하거나 응급실로 가세요. "
           "에피네프린 자가주사기를 처방받았다면 지금 사용하세요."),
    "en": ("🚨 If you are having trouble breathing, or have hives all over with dizziness, this may be "
           "anaphylaxis. Do not spend time on this chat — call emergency services or go to an emergency "
           "room now. If you have an epinephrine auto-injector, use it now."),
    "zh": ("🚨 如果现在呼吸困难，或全身荨麻疹伴头晕，可能是过敏性休克。请不要在此聊天上耽误时间，"
           "立即拨打急救电话或前往急诊。如果您有肾上腺素自动注射笔，请立即使用。"),
}

NO_KEY_TEXT = {
    "ko": ("자유 질문에 답하려면 서버에 OpenAI API 키가 필요합니다. 키 없이도 아래 추천 질문은 "
           "검사 결과 데이터에서 바로 답해 드립니다."),
    "en": ("Free-text questions need an OpenAI API key on the server. Even without a key, the suggested "
           "questions below are answered directly from your result data."),
    "zh": ("回答自由提问需要服务器配置 OpenAI API 密钥。即使没有密钥，下面的推荐问题也会直接根据"
           "您的检测结果数据作答。"),
}

DISCLAIMER = {
    "ko": "이 답변은 교육용 참고 자료입니다. 진단과 치료는 담당 의료진과 상담하세요.",
    "en": "This answer is educational reference material. Discuss diagnosis and treatment with your physician.",
    "zh": "此回答仅供教育参考。诊断与治疗请与主治医生商议。",
}

LANG_NAME = {"ko": "Korean", "en": "English", "zh": "Simplified Chinese"}

_VERDICT_KO = {
    ClinicalRelevance.CLINICALLY_RELEVANT: "실제 증상을 일으키는 것으로 판단(진범 확정)",
    ClinicalRelevance.SENSITIZED_ONLY: "검사만 양성이고 증상은 없음(감작만)",
    ClinicalRelevance.INDETERMINATE: "정보가 부족해 판정 보류(관찰 대상)",
    ClinicalRelevance.NOT_ASSESSED: "평가하지 않음",
}


class ResultChatService:
    """판정 결과를 근거로 환자 질문에 답한다."""

    def __init__(self, api_key: Optional[str] = None):
        # api_key=None → 설정에서 읽는다. api_key="" → 키 없음(테스트·오프라인 경로).
        self.api_key = (settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
                        if api_key is None else api_key)
        self.client = None
        if self.api_key and self.api_key != "your_openai_api_key_here":
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=self.api_key)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"OpenAI 클라이언트 초기화 실패: {e}")

    # ------------------------------------------------------------------
    # 1) 근거 컨텍스트
    # ------------------------------------------------------------------
    def build_context(self, relevance_result, patient_info: Dict[str, Any],
                      screening=None, answers: Optional[Dict[str, Any]] = None) -> str:
        """LLM 에 넘길 근거 텍스트. 판정 결과 밖의 내용은 담지 않는다."""
        p = patient_info or {}
        lines: List[str] = ["[환자 검사 결과 요약]"]
        if p.get("name"):
            lines.append(f"이름: {p['name']}")
        if p.get("age"):
            lines.append(f"나이: {p['age']}")
        if p.get("test_date"):
            lines.append(f"검사일: {p['test_date']}")
        tt = p.get("test_type") or getattr(relevance_result, "test_type", None)
        lines.append(f"검사 종류: {getattr(tt, 'value', tt) or '미상'}")

        buckets = {
            "실제 주의(증상 유발)": ClinicalRelevance.CLINICALLY_RELEVANT,
            "감작만(증상 없음)": ClinicalRelevance.SENSITIZED_ONLY,
            "관찰 필요(판정 보류)": ClinicalRelevance.INDETERMINATE,
        }
        for label, rel in buckets.items():
            items = relevance_result.by_relevance(rel)
            lines.append(f"\n[{label}] {len(items)}건")
            for a in items:
                lines.append(self._allergen_block(a))

        if screening is not None:
            try:
                from services.screening_service import get_screening_service
                sc = get_screening_service().summarize(screening)
                lines.append("\n[문진 요약]")
                if sc.get("diseases_ko"):
                    lines.append(f"진단/의심 질환: {', '.join(sc['diseases_ko'])}")
                if sc.get("organ_systems_ko"):
                    lines.append(f"증상 부위: {', '.join(sc['organ_systems_ko'])}")
                if sc.get("season_pattern_ko"):
                    lines.append(f"증상 패턴: {sc['season_pattern_ko']}")
                for f in sc.get("flags", []):
                    lines.append(f"주의: {f}")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"문진 요약 실패: {e}")
        return "\n".join(lines)

    def _allergen_block(self, a) -> str:
        nm = a.korean_name or a.allergen_name
        kb = a.kb or {}
        parts = [f"- {nm} ({a.allergen_name})"]
        meta = []
        if a.test_value is not None:
            meta.append(f"수치 {a.test_value}{a.test_unit or ''}")
        if a.class_value is not None:
            meta.append(f"class {a.class_value}")
        if a.strength:
            meta.append({"weak": "약한 감작", "moderate": "중등도 감작", "strong": "강한 감작"}.get(a.strength, a.strength))
        if getattr(a, "severity", None):
            meta.append(f"증상 정도 {a.severity}")
        if meta:
            parts.append("  " + " · ".join(meta))
        parts.append(f"  판정: {_VERDICT_KO.get(a.relevance, str(a.relevance))}")
        if a.rationale_ko:
            parts.append(f"  판정 근거: {a.rationale_ko}")
        if kb.get("season_label_ko"):
            parts.append(f"  시기: {kb['season_label_ko']}")
        if kb.get("exposure_environment_ko"):
            parts.append(f"  노출 환경: {kb['exposure_environment_ko']}")
        tips = kb.get("avoidance_control_ko") or []
        if tips:
            parts.append(f"  회피 수칙: {' / '.join(tips[:4])}")
        if getattr(a, "oas_foods", None):
            parts.append(f"  구강알레르기증후군(OAS) 확인된 음식: {', '.join(a.oas_foods)}")
        if getattr(a, "crossreact_confirmed", None):
            parts.append(f"  증상 확인된 교차반응 음식: {', '.join(a.crossreact_confirmed)}")
        if getattr(a, "crossreact_risk", None):
            parts.append(f"  같은 성분 계열(관찰 대상): {', '.join(a.crossreact_risk[:8])}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 2) 추천 질문 — 일부는 LLM 없이 바로 답한다
    # ------------------------------------------------------------------
    def suggestions(self, relevance_result, lang: str = "ko") -> List[Dict[str, Any]]:
        rel = relevance_result.by_relevance(ClinicalRelevance.CLINICALLY_RELEVANT)
        ind = relevance_result.by_relevance(ClinicalRelevance.INDETERMINATE)
        sens = relevance_result.by_relevance(ClinicalRelevance.SENSITIZED_ONLY)
        L = lang if lang in ("ko", "en", "zh") else "ko"

        def q(key, ko, en, zh, answer=None):
            return {"key": key, "text": {"ko": ko, "en": en, "zh": zh}[L], "answer": answer}

        out = [q("why_relevant",
                 "제 결과에서 지금 가장 조심해야 할 것은 뭔가요?",
                 "What should I be most careful about in my results?",
                 "在我的结果中，现在最需要注意什么？",
                 answer=self._answer_top_priority(rel, L))]
        if rel:
            first = self._collapse(rel)[0]
            nm = self._label(first)
            out.append(q("rationale",
                         f"‘{nm}’은(는) 왜 실제 원인으로 판단됐나요?",
                         f"Why was '{nm}' judged to be an actual cause?",
                         f"为什么判定“{nm}”是实际原因？",
                         answer=self._answer_rationale(first, L)))
        if sens:
            out.append(q("sensitized_only",
                         "검사에서 양성인데 피하지 않아도 된다는 건 무슨 뜻인가요?",
                         "What does it mean that a positive test doesn't need avoidance?",
                         "检测阳性却不需要回避，是什么意思？",
                         answer=self._answer_sensitized(sens, L)))
        if ind:
            out.append(q("indeterminate",
                         "‘관찰 필요’로 나온 항목은 어떻게 해야 하나요?",
                         "What should I do about items marked 'under watch'?",
                         "标记为“需观察”的项目该怎么办？",
                         answer=self._answer_indeterminate(ind, L)))
        foods = self._all_foods(relevance_result)
        if foods:
            out.append(q("foods",
                         "제가 조심해야 할 음식은 무엇인가요?",
                         "Which foods should I be careful with?",
                         "我需要注意哪些食物？",
                         answer=self._answer_foods(foods, L)))
        out.append(q("immunotherapy",
                     "면역치료(알레르기 근본치료)를 받아야 하나요?",
                     "Should I consider allergen immunotherapy?",
                     "我需要做免疫治疗吗？", answer=None))
        return out

    def _answer_top_priority(self, rel, lang):
        if not rel:
            return {"ko": "이번 문진에서는 노출 시 실제 증상과 뚜렷이 연관된 알러젠이 확인되지 않았습니다. "
                          "증상이 있을 때의 상황을 기록해 두면 다음 평가에 도움이 됩니다.",
                    "en": "No allergen was clearly linked to your symptoms in this questionnaire. "
                          "Recording the situation when symptoms occur will help the next assessment.",
                    "zh": "本次问卷中没有发现与症状明确相关的过敏原。记录出现症状时的情况有助于下次评估。"}[lang]
        # Df/Dp 처럼 임상적으로 같은 그룹은 한 번만 세고 한 번만 조언한다
        grouped = self._collapse(rel)
        names = ", ".join(self._label(a) for a in grouped[:5])
        tips = []
        for a in grouped[:2]:
            for t in (a.kb or {}).get("avoidance_control_ko", [])[:2]:
                if not self._dup_tip(t, tips):
                    tips.append(t)
        tip_txt = ("\n\n먼저 할 일\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(tips, 1))) if tips else ""
        n = len(grouped)
        return {"ko": f"노출될 때 실제로 증상이 나타나는 알러젠은 {n}가지입니다: {names}.{tip_txt}",
                "en": f"{n} allergen(s) actually cause symptoms on exposure: {names}.{tip_txt}",
                "zh": f"有 {n} 种过敏原在暴露时确实会引起症状：{names}。{tip_txt}"}[lang]

    @staticmethod
    def _label(a) -> str:
        """임상 그룹은 통합 라벨로(예: '집먼지진드기(유럽·미국 두 종)')."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return get_clinical_group_service().label_of(a)
        except Exception:
            return a.korean_name or a.allergen_name

    @staticmethod
    def _collapse(items):
        """임상 그룹(Df/Dp 등)을 하나로 접는다 — 같은 조언을 두 번 하지 않기 위해."""
        try:
            from services.clinical_group_service import get_clinical_group_service
            return [g["members"][0][1] for g in get_clinical_group_service().collapse(items)]
        except Exception:
            return list(items)

    @staticmethod
    def _dup_tip(tip: str, seen: List[str]) -> bool:
        """표현만 다른 같은 수칙을 걸러낸다(예: '침구 주 1회 55~60℃ 세탁' vs '침구는 55~60℃ … 주 1회 세탁')."""
        def key(t):
            return set(re.findall(r"[가-힣a-zA-Z0-9]+", (t or "").lower())) - {"는", "은", "이", "가", "로", "으로"}
        k = key(tip)
        if not k:
            return True
        for prev in seen:
            kp = key(prev)
            if not kp:
                continue
            if len(k & kp) / max(1, min(len(k), len(kp))) >= 0.6:
                return True
        return False

    def _answer_rationale(self, a, lang):
        nm = self._label(a)
        if not a.rationale_ko:
            return None
        pre = {"ko": f"‘{nm}’ 판정 근거입니다.\n\n", "en": f"Here is the basis for '{nm}'.\n\n",
               "zh": f"这是“{nm}”的判定依据。\n\n"}[lang]
        return pre + a.rationale_ko

    def _answer_sensitized(self, sens, lang):
        names = ", ".join(self._label(a) for a in self._collapse(sens)[:6])
        return {"ko": ("검사 양성은 몸이 그 물질에 반응할 준비가 되어 있다는 뜻(감작)일 뿐입니다. "
                       "알레르기 질환은 노출될 때 증상이 되풀이되어야 성립합니다. "
                       f"다음 항목은 노출해도 증상이 없어 지금은 피하지 않아도 됩니다: {names}. "
                       "새 증상이 생기면 다시 평가하세요."),
                "en": ("A positive test only means your body is sensitized. An allergic disease requires "
                       "symptoms to recur on exposure. These items caused no symptoms on exposure, so you "
                       f"do not need to avoid them now: {names}. Re-evaluate if new symptoms appear."),
                "zh": ("检测阳性只表示身体已致敏。过敏性疾病需要在暴露时反复出现症状才能成立。"
                       f"以下项目暴露后没有症状，目前不需要回避：{names}。若出现新症状请重新评估。")}[lang]

    def _answer_indeterminate(self, ind, lang):
        names = ", ".join(self._label(a) for a in self._collapse(ind)[:6])
        return {"ko": (f"{names}은(는) 노출 경험이나 정보가 부족해 판정을 보류했습니다. "
                       "해당 알러젠에 노출되는 상황(계절·장소·먹은 음식)과 그때 증상이 있었는지를 "
                       "기록해 두었다가 다음 진료 때 보여주세요."),
                "en": (f"{names} were left undetermined because exposure information was insufficient. "
                       "Record when you are exposed (season, place, food) and whether symptoms occurred, "
                       "then show it at your next visit."),
                "zh": (f"{names} 因暴露信息不足而暂缓判定。请记录接触的情形（季节、场所、food）"
                       "以及当时是否出现症状，下次就诊时提供给医生。")}[lang]

    def _all_foods(self, relevance_result):
        foods: Dict[str, List[str]] = {}
        for a in relevance_result.assessments:
            src = a.korean_name or a.allergen_name
            for f in (getattr(a, "oas_foods", None) or []) + (getattr(a, "crossreact_confirmed", None) or []):
                foods.setdefault(f, [])
                if src not in foods[f]:
                    foods[f].append(src)
            if normalize_category(a.category) == "food" and a.relevance == ClinicalRelevance.CLINICALLY_RELEVANT:
                foods.setdefault(a.korean_name or a.allergen_name, [])
        return foods

    def _answer_foods(self, foods, lang):
        rows = []
        for f, trg in foods.items():
            rows.append(f"- {f}" + (f" ({', '.join(trg)} 교차반응)" if trg else ""))
        body = "\n".join(rows)
        return {"ko": ("증상이 확인된 음식입니다. 검사에서 직접 양성으로 나온 알러젠과 같은 수준으로 "
                       f"주의하세요.\n{body}\n\n외식·가공식품에서는 원재료 표시를 꼭 확인하세요."),
                "en": ("These foods caused symptoms. Treat them as seriously as a directly positive "
                       f"allergen.\n{body}\n\nAlways check ingredient labels when eating out or buying "
                       "processed food."),
                "zh": ("以下食物已确认引起症状，请与检测直接阳性的过敏原同等重视。\n"
                       f"{body}\n\n外出就餐或购买加工食品时，务必查看配料表。")}[lang]

    # ------------------------------------------------------------------
    # 3) 대화
    # ------------------------------------------------------------------
    def _system_prompt(self, context: str, lang: str) -> str:
        return (
            "You are a patient-education assistant for an allergy test report. "
            f"Answer ONLY in {LANG_NAME.get(lang, 'Korean')}.\n\n"
            "GROUNDING RULES (strict):\n"
            "1. Use ONLY the patient result context below. Do not add allergens, numbers, or findings "
            "that are not in it.\n"
            "2. If the answer is not in the context, say plainly that this result cannot tell, and "
            "suggest asking the treating clinician. Do not speculate.\n"
            "3. Never diagnose, never prescribe, never suggest starting/stopping/changing a medication "
            "or its dose. You may explain what a drug class is generally for.\n"
            "4. Keep the core distinction straight: a positive test means sensitization; an allergy "
            "requires symptoms that recur on exposure.\n"
            "5. If the user describes breathing difficulty, throat swelling, fainting or anaphylaxis, "
            "tell them to seek emergency care first, before any other explanation.\n"
            "6. Be brief and concrete: 2-6 short sentences, or a short numbered list of actions. "
            "One idea per sentence. No filler.\n\n"
            f"PATIENT RESULT CONTEXT:\n{context}\n"
        )

    def answer(self, relevance_result, patient_info: Dict[str, Any], messages: List[Dict[str, str]],
               screening=None, answers: Optional[Dict[str, Any]] = None,
               lang: str = "ko") -> Dict[str, Any]:
        lang = lang if lang in ("ko", "en", "zh") else "ko"
        user_msgs = [m for m in (messages or []) if m.get("role") == "user"]
        last = (user_msgs[-1].get("content") if user_msgs else "") or ""
        last = last.strip()[:MAX_QUESTION_CHARS]

        if EMERGENCY_PATTERNS.search(last):
            return {"reply": EMERGENCY_TEXT[lang], "source": "emergency", "disclaimer": DISCLAIMER[lang]}
        if not last:
            return {"reply": {"ko": "궁금한 점을 입력해 주세요.", "en": "Please type your question.",
                              "zh": "请输入您的问题。"}[lang], "source": "empty",
                    "disclaimer": DISCLAIMER[lang]}
        if not self.client:
            return {"reply": NO_KEY_TEXT[lang], "source": "no_api_key", "disclaimer": DISCLAIMER[lang]}

        context = self.build_context(relevance_result, patient_info, screening, answers)
        convo = [{"role": "system", "content": self._system_prompt(context, lang)}]
        for m in (messages or [])[-MAX_HISTORY:]:
            role = m.get("role")
            if role in ("user", "assistant") and m.get("content"):
                convo.append({"role": role, "content": str(m["content"])[:MAX_QUESTION_CHARS]})
        try:
            resp = self.client.chat.completions.create(
                model=getattr(settings, "openai_chat_model", None) or "gpt-4o-mini",
                messages=convo,
                temperature=0.3,
                max_tokens=600,
            )
            reply = (resp.choices[0].message.content or "").strip()
            return {"reply": reply, "source": "llm", "disclaimer": DISCLAIMER[lang]}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"상담 응답 생성 실패: {e}")
            return {"reply": {"ko": "지금은 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                              "en": "Could not generate an answer right now. Please try again shortly.",
                              "zh": "当前无法生成回答，请稍后再试。"}[lang],
                    "source": "error", "disclaimer": DISCLAIMER[lang]}


_result_chat_service: Optional[ResultChatService] = None
_last_key: Optional[str] = None


def get_result_chat_service(api_key: Optional[str] = None) -> ResultChatService:
    global _result_chat_service, _last_key
    key = api_key or settings.openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if _result_chat_service is None or _last_key != key:
        _result_chat_service = ResultChatService(api_key=key)
        _last_key = key
    return _result_chat_service
