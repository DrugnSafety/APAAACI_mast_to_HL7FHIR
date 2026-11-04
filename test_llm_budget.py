"""토큰 예산 라우팅(services/llm_usage.py) — 누적 1,000만 토큰까지 gpt-5.6-luna, 넘으면 gpt-6-luna.

장부는 임시 파일로 돌리고 설정은 monkeypatch 로 바꾼다. 실제 API 는 부르지 않는다.
"""
from types import SimpleNamespace

import pytest

from config.settings import settings
from services import llm_backend, llm_usage


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "usage.json"
    monkeypatch.setattr(settings, "llm_usage_ledger", str(p))
    monkeypatch.setattr(settings, "openai_budget_model", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "openai_budget_tokens", 1000)
    monkeypatch.setattr(settings, "openai_vision_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "openai_chat_model", "gpt-6-luna")
    monkeypatch.setattr(settings, "llm_backend", "openai")
    return p


def _usage(prompt, completion, reasoning=0):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion,
                           completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))


def test_record_accumulates_and_persists(ledger):
    llm_usage.record("gpt-5.6-luna", _usage(100, 50, 20))
    m = llm_usage.record("gpt-5.6-luna", _usage(10, 5))
    assert m["total"] == 165 and m["requests"] == 2 and m["reasoning"] == 20
    assert ledger.exists() and llm_usage.used("gpt-5.6-luna") == 165
    assert llm_usage.record("gpt-5.6-luna", None) is None, "usage 가 없으면 세지 않는다"
    assert llm_usage.record("qwen3.8:27b", _usage(1, 1), backend="ollama") is None, "로컬 모델은 세지 않는다"


def test_choose_switches_at_budget(ledger):
    assert llm_usage.choose("gpt-6-luna") == "gpt-5.6-luna"
    llm_usage.record("gpt-5.6-luna", _usage(600, 399))        # 999 < 1000
    assert llm_usage.choose("gpt-6-luna") == "gpt-5.6-luna"
    llm_usage.record("gpt-5.6-luna", _usage(1, 0))            # 1000 == 예산 → 전환
    assert llm_usage.choose("gpt-6-luna") == "gpt-6-luna"
    assert llm_usage.status()["budget_exhausted"] is True


def test_choose_disabled_when_no_budget(ledger, monkeypatch):
    monkeypatch.setattr(settings, "openai_budget_model", "")
    assert llm_usage.choose("gpt-6-luna") == "gpt-6-luna"
    monkeypatch.setattr(settings, "openai_budget_model", "gpt-5.6-luna")
    monkeypatch.setattr(settings, "openai_budget_tokens", 0)
    assert llm_usage.choose("gpt-6-luna") == "gpt-6-luna"


def test_backend_model_choice_honours_budget(ledger):
    assert llm_backend.vision_model("openai") == "gpt-5.6-luna"
    assert llm_backend.chat_model("openai") == "gpt-5.6-luna"
    llm_usage.record("gpt-5.6-luna", _usage(1000, 0))
    assert llm_backend.vision_model("openai") == "gpt-6-luna"
    assert llm_backend.chat_model("openai") == "gpt-6-luna"
    assert llm_backend.vision_model("ollama") == settings.ollama_vision_model, "로컬 백엔드는 예산과 무관"


def test_complete_chat_records_usage_and_uses_budget_model(ledger, monkeypatch):
    seen = {}

    class _Client:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return SimpleNamespace(usage=_usage(30, 12), model=kw["model"],
                                           choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(llm_backend, "client", lambda backend=None: _Client())
    monkeypatch.setattr(llm_backend, "is_available", lambda b: True)
    llm_backend.complete_chat([{"role": "user", "content": "hi"}], backend="openai", max_tokens=50)
    assert seen["model"] == "gpt-5.6-luna" and "max_completion_tokens" in seen and "temperature" not in seen
    assert llm_usage.used("gpt-5.6-luna") == 42


# ---------------------------------------------------------------- 키 묶음 round-robin (2026-10-10)
def _clear_keys(monkeypatch):
    for i in range(1, 9):
        monkeypatch.setattr(settings, f"openai_api_key_{i}", "")


def test_openai_keys_collected_in_number_order_and_rotated(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key_3", "sk-c")
    monkeypatch.setattr(settings, "openai_api_key_1", "sk-a")
    monkeypatch.setattr(settings, "openai_api_key_2", "sk-b")
    monkeypatch.setattr(settings, "openai_api_key_4", "your_openai_api_key_here")   # 자리표시자는 뺀다
    monkeypatch.setattr(settings, "openai_api_key_5", "sk-a")                       # 중복 값은 한 번만
    monkeypatch.setattr(settings, "openai_api_key", "sk-plain")
    assert llm_backend.openai_keys() == ["sk-a", "sk-b", "sk-c"], "번호 키가 있으면 OPENAI_API_KEY 는 쓰지 않는다"
    assert llm_backend.is_available("openai")

    made = []

    class FakeOpenAI:
        def __init__(self, api_key=None, **kw):
            self.api_key = api_key
            made.append(api_key)
    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    picks = [llm_backend.client("openai").api_key for _ in range(7)]
    assert picks == ["sk-a", "sk-b", "sk-c", "sk-a", "sk-b", "sk-c", "sk-a"]
    assert made == ["sk-a", "sk-b", "sk-c"], "키마다 클라이언트 하나"
    assert llm_backend.key_stats() == {"count": 3, "requests": [3, 2, 2]}


def test_openai_keys_fall_back_to_single_key(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setattr(settings, "openai_api_key", "sk-only")
    assert llm_backend.openai_keys() == ["sk-only"]
    monkeypatch.setattr(settings, "openai_api_key", "")
    assert llm_backend.openai_keys() == [] and not llm_backend.is_available("openai")


# ---------------------------------------------------------------- 예산 초과 메일
def test_budget_crossing_sends_one_email_to_both_recipients(ledger, monkeypatch):
    sent = []

    class FakeMail:
        configured = True

        def send(self, to, subject, html, text, attachments=None):
            sent.append((to, subject, text))
    monkeypatch.setattr("services.email_service.get_email_service", lambda: FakeMail())
    monkeypatch.setattr(settings, "openai_budget_notify_to", "irreversibly@gmail.com, kintel@kintel.kr")
    llm_usage.record("gpt-5.6-luna", _usage(900, 0))
    assert sent == [], "한도 전에는 보내지 않는다"
    llm_usage.record("gpt-5.6-luna", _usage(100, 0))          # 1000 == 예산 → 넘김
    assert [t for t, _, _ in sent] == ["irreversibly@gmail.com", "kintel@kintel.kr"]
    assert "무료 토큰 한도 초과" in sent[0][1] and "gpt-6-luna" in sent[0][1] and "1,000" in sent[0][2]
    llm_usage.record("gpt-5.6-luna", _usage(5, 0))
    assert len(sent) == 2, "한 번만 보낸다"
    assert llm_usage.status()["notify"]["status"] == "sent"


def test_budget_crossing_without_smtp_is_marked_pending_and_retried_later(ledger, monkeypatch):
    class NoMail:
        configured = False

        def send(self, *a, **k):
            raise AssertionError("보내면 안 된다")
    monkeypatch.setattr("services.email_service.get_email_service", lambda: NoMail())
    llm_usage.record("gpt-5.6-luna", _usage(1000, 0))
    st = llm_usage.status()["notify"]
    assert st["status"] == "smtp_unconfigured" and st["tries"] == 1
    llm_usage.record("gpt-5.6-luna", _usage(1, 0))
    assert llm_usage.status()["notify"]["tries"] == 1, "1시간 안에는 다시 시도하지 않는다"
    # 1시간이 지난 것처럼 — 다시 시도
    data = llm_usage.load(); data["budget_notify"]["last_try"] = "2000-01-01T00:00:00"; llm_usage._save(data)
    sent = []

    class OkMail:
        configured = True

        def send(self, to, subject, html, text, attachments=None):
            sent.append(to)
    monkeypatch.setattr("services.email_service.get_email_service", lambda: OkMail())
    llm_usage.record("gpt-5.6-luna", _usage(1, 0))
    assert sent == ["irreversibly@gmail.com", "kintel@kintel.kr"] and llm_usage.status()["notify"]["status"] == "sent"
