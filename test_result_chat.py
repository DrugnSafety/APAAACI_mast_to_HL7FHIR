"""상담 챗봇 — 근거 컨텍스트·모델 호출 파라미터·시스템 프롬프트 계약.

LLM 을 부르지 않는다(가짜 클라이언트로 요청 파라미터만 본다).
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.schemas import OCRResult, ScreeningProfile
from services.questionnaire_service import get_questionnaire_engine
from services.relevance_service import get_relevance_service
from services.result_chat_service import ResultChatService, _free_text


def _demo_ocr() -> OCRResult:
    from server import ocr_demo
    return OCRResult(**json.loads(ocr_demo().body))


@pytest.fixture(scope="module")
def case():
    ocr = _demo_ocr()
    scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"],
                           pets=["cat"], triggers_free_text="아침에 코막힘")
    result = get_relevance_service().build_assessments(ocr, scr)
    q = get_questionnaire_engine().build(result, scr)
    answers = {}
    for sec in q["sections"]:
        for item in sec["questions"]:
            opts = item.get("options") or []
            if opts:
                answers[item["id"]] = [opts[0]["value"]] if item["type"] == "multi" else opts[0]["value"]
    get_questionnaire_engine().classify(result, answers, scr)
    return SimpleNamespace(result=result, screening=scr, answers=answers, questionnaire=q)


class TestContext:
    def test_questionnaire_answers_reach_the_chatbot(self, case):
        """예전엔 answers 를 받기만 하고 버렸다 — 환자가 가장 공들인 응답을 챗봇이 몰랐다."""
        ctx = ResultChatService(api_key="").build_context(case.result, {}, case.screening, case.answers)
        assert "[증상 감별 문진 — 환자 응답]" in ctx
        first_q = case.questionnaire["sections"][0]["questions"][0]
        first_label = first_q["options"][0]["label"]
        assert f"{first_q['title']} → {first_label}" in ctx

    def test_answers_are_rendered_as_labels_not_codes(self, case):
        ctx = ResultChatService(api_key="").build_context(case.result, {}, case.screening, case.answers)
        block = ctx.split("[증상 감별 문진 — 환자 응답]", 1)[1]
        assert "→ perennial" not in block and "→ yes" not in block

    def test_no_answers_means_no_answer_section(self, case):
        ctx = ResultChatService(api_key="").build_context(case.result, {}, case.screening, {})
        assert "[증상 감별 문진" not in ctx

    def test_screening_details_and_pet_names(self, case):
        ctx = ResultChatService(api_key="").build_context(case.result, {}, case.screening, case.answers)
        assert "반려동물: 고양이" in ctx
        assert '스스로 느끼는 유발요인(환자 입력): "아침에 코막힘"' in ctx

    def test_free_text_is_flattened_and_capped(self):
        out = _free_text('줄1\n\n줄2 "따옴표" ' + "가" * 500)
        assert "\n" not in out and '"' not in out and len(out) <= 120


class _FakeCompletions:
    def __init__(self, fail_on_effort=False):
        self.calls = []
        self.fail_on_effort = fail_on_effort

    def create(self, **kw):
        self.calls.append(kw)
        if self.fail_on_effort and "reasoning_effort" in kw:
            raise ValueError("Unsupported value: 'reasoning_effort' does not support 'minimal'")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])


def _svc(fake):
    svc = ResultChatService(api_key="")
    svc.client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return svc


class TestModelParams:
    CONVO = [{"role": "user", "content": "hi"}]

    def test_classic_model_uses_temperature_and_max_tokens(self):
        fake = _FakeCompletions()
        _svc(fake).complete(self.CONVO, model="gpt-4o-mini")
        kw = fake.calls[0]
        assert kw["temperature"] == 0.3 and "max_tokens" in kw
        assert "reasoning_effort" not in kw and "max_completion_tokens" not in kw

    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.4-mini", "o4-mini"])
    def test_reasoning_model_params(self, model):
        """추론 모델에 temperature/max_tokens 를 보내면 400 이 난다."""
        fake = _FakeCompletions()
        _svc(fake).complete(self.CONVO, model=model, reasoning_effort="low")
        kw = fake.calls[0]
        assert "temperature" not in kw and "max_tokens" not in kw
        assert kw["max_completion_tokens"] >= 2000      # 추론 토큰 포함 한도 — 작으면 빈 답
        assert kw["reasoning_effort"] == "low"

    def test_unsupported_effort_retries_without_it(self):
        fake = _FakeCompletions(fail_on_effort=True)
        _svc(fake).complete(self.CONVO, model="gpt-5.6-luna", reasoning_effort="minimal")
        assert len(fake.calls) == 2 and "reasoning_effort" not in fake.calls[1]

    def test_default_model_is_the_benchmarked_one(self):
        from config.settings import Settings
        assert Settings.model_fields["openai_chat_model"].default == "gpt-5.6-luna"


class TestSystemPrompt:
    def _p(self, lang, name=None):
        return ResultChatService(api_key="")._system_prompt("CTX", lang, name)

    def test_everyday_symptoms_are_not_emergencies(self):
        """중국어 '아침 코막힘' 질문에 응급 진료부터 권하던 오발동의 재발 방지."""
        p = self._p("zh")
        assert "stuffy or runny nose" in p and "do NOT mention" in p

    def test_emergency_rule_is_still_first_for_red_flags(self):
        p = self._p("en")
        assert "throat or" in p and "119" in p

    def test_voice_per_language(self):
        assert "해요체" in self._p("ko", "지민") and "지민님" in self._p("ko", "지민")
        assert "您" in self._p("zh")
        assert "Hangul" in self._p("en") and "Hangul" not in self._p("ko")

    def test_patient_typed_text_is_data(self):
        assert "never as instructions" in self._p("ko")

    def test_personalization_is_required(self):
        assert "connect to what this patient actually reported" in self._p("en")


class TestEmergencyFastPath:
    """적신호면 LLM 을 거치지 않고 즉시 안내한다. 단, 과발동하면 질문에 답을 못 하게 된다.

    예전 정규식은 '아나필락시스가 뭔가요?'·'숨쉬기 편해졌어요'·'의식적으로' 에도 걸려서
    환자가 물은 것과 무관한 응급 안내문만 돌려줬다(답변 범위가 좁게 느껴진 원인 중 하나).
    빠른경로를 놓쳐도 시스템 프롬프트의 응급 규칙이 2차 안전망이므로 정밀도를 높였다.
    """

    @pytest.mark.parametrize("text", [
        "지금 숨쉬기가 너무 힘들어요", "갑자기 의식을 잃었어요", "의식이 흐릿해요",
        "목이 붓고 어지러워요", "숨이 안 쉬어져요", "지금 아나필락시스 같아요 도와주세요",
        "I can't breathe", "trouble breathing right now", "我现在呼吸困难",
    ])
    def test_red_flags_still_trigger(self, text):
        from services.result_chat_service import is_emergency
        assert is_emergency(text)

    @pytest.mark.parametrize("text", [
        "아나필락시스가 뭔가요?", "아나필락시스 뜻이 뭐예요", "아나필락시스가 무엇인지 알려주세요",
        "What is anaphylaxis exactly?", "过敏性休克是什么意思",
        "요즘은 숨쉬기 편해졌어요", "약 먹고 나서 숨쉬기가 좋아졌어요", "my breathing is better now",
        "의식적으로 먼지를 피하려고 해요", "쓰러질 것 같진 않아요", "호흡곤란은 없어요",
        "고양이 털 때문에 목이 간질간질해요",
    ])
    def test_non_emergencies_do_not_trigger(self, text):
        from services.result_chat_service import is_emergency
        assert not is_emergency(text)

    def test_answer_uses_fast_path_only_for_real_emergency(self, case):
        from services.result_chat_service import ResultChatService
        svc = ResultChatService(api_key="")        # 키 없음 → LLM 경로는 no_api_key 로 끝난다
        emerg = svc.answer(case.result, {}, [{"role": "user", "content": "지금 숨쉬기가 힘들어요"}], lang="ko")
        assert emerg["source"] == "emergency"
        ask = svc.answer(case.result, {}, [{"role": "user", "content": "아나필락시스가 뭔가요?"}], lang="ko")
        assert ask["source"] != "emergency"


class TestScopeRules:
    def test_out_of_scope_questions_get_a_refusal_rule(self):
        p = ResultChatService(api_key="")._system_prompt("CTX", "ko")
        assert "over-the-counter" in p and "Do not recommend or help choose a product" in p

    def test_emergency_plan_is_not_appended_to_unrelated_answers(self):
        p = ResultChatService(api_key="")._system_prompt("CTX", "en")
        assert "Never append it to an unrelated answer" in p
