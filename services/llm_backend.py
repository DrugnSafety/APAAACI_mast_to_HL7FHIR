"""LLM 백엔드 선택 — OpenAI(클라우드) 또는 연구실 Ollama(DGX Spark, OpenAI 호환 /v1).

모델은 두 종류로 나눠 쓴다.
  vision — 결과지 OCR (이미지 입력이 되는 모델만)
  chat   — 결과 상담 챗봇·번역·리포트 문장 다듬기 (텍스트)

어느 백엔드를 쓸지는 서버 기본값(LLM_BACKEND)과 요청별 선택(화면의 'AI 엔진')으로 정한다.
요청별 선택은 contextvar 로 들고 다닌다 — 리포트 안에서 부르는 번역처럼 깊은 호출까지
인자를 일일이 넘기지 않아도 같은 백엔드를 쓰게 하려는 것이다.

Ollama 모델 구분(2026-09-24, /api/show 의 capabilities 기준)
  vision 가능: qwen3.8:27b, qwen3.5:35b, gemma4:31b·26b·12b·e4b·e2b, llama4, qwen2.5vl:7b
  chat 전용:   gpt-oss:120b, qwen3:latest, qwen3-coder-next(코딩용)
  해당 없음:   nomic-embed-text-v2-moe(임베딩), x/flux2-klein(이미지 생성)
"""
from __future__ import annotations

import contextvars
import logging
import threading
from contextlib import contextmanager
from typing import Any, Dict, Optional, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)

BACKENDS = ("openai", "ollama")
_current: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("llm_backend", default=None)
_clients: Dict[str, Any] = {}
_lock = threading.Lock()


def normalize(name: Optional[str]) -> Optional[str]:
    n = (name or "").strip().lower()
    if n in ("local", "lab", "dgx"):
        n = "ollama"
    return n if n in BACKENDS else None


def is_available(backend: str) -> bool:
    if backend == "openai":
        key = settings.openai_api_key or ""
        return bool(key) and key != "your_openai_api_key_here"
    if backend == "ollama":
        return bool(settings.ollama_base_url and settings.ollama_api_key)
    return False


def active() -> str:
    """이번 요청이 쓸 백엔드. 요청 선택 → 서버 기본값 → 쓸 수 있는 쪽 순서."""
    for cand in (_current.get(), normalize(settings.llm_backend), "openai", "ollama"):
        if cand and is_available(cand):
            return cand
    return normalize(settings.llm_backend) or "openai"


@contextmanager
def use(backend: Optional[str]):
    """with use('ollama'): ... — 이 블록 안의 LLM 호출이 해당 백엔드를 쓴다."""
    token = _current.set(normalize(backend))
    try:
        yield
    finally:
        _current.reset(token)


def client(backend: Optional[str] = None):
    """OpenAI SDK 클라이언트. Ollama 는 OpenAI 호환 엔드포인트(/v1)라 같은 SDK 를 쓴다."""
    from openai import OpenAI
    b = normalize(backend) or active()
    if not is_available(b):
        return None
    with _lock:
        if b not in _clients:
            if b == "ollama":
                _clients[b] = OpenAI(base_url=settings.ollama_base_url.rstrip("/") + "/v1",
                                     api_key=settings.ollama_api_key,
                                     timeout=float(settings.ollama_timeout),
                                     # SDK 기본 재시도(2회)가 붙으면 멈춘 모델 하나에 30분 넘게 묶인다
                                     max_retries=0)
            else:
                _clients[b] = OpenAI(api_key=settings.openai_api_key)
        return _clients[b]


def vision_model(backend: Optional[str] = None) -> str:
    b = normalize(backend) or active()
    return settings.ollama_vision_model if b == "ollama" else settings.openai_vision_model


def chat_model(backend: Optional[str] = None) -> str:
    b = normalize(backend) or active()
    return settings.ollama_chat_model if b == "ollama" else settings.openai_chat_model


def describe() -> Dict[str, Any]:
    """화면의 'AI 엔진' 선택지. 키·주소는 내보내지 않는다."""
    base = settings.ollama_base_url or ""
    return {
        "default": active(),
        "backends": {
            "openai": {"available": is_available("openai"), "label": "OpenAI",
                       "vision_model": settings.openai_vision_model,
                       "chat_model": settings.openai_chat_model, "encrypted": True},
            "ollama": {"available": is_available("ollama"), "label": "연구실 서버 (Ollama)",
                       "vision_model": settings.ollama_vision_model,
                       "chat_model": settings.ollama_chat_model,
                       "encrypted": base.startswith("https://")},
        },
    }


def complete_chat(messages, backend: Optional[str] = None, max_tokens: int = 700,
                  temperature: float = 0.3, json_mode: bool = False, **extra):
    """chat 모델 1회 호출 — 백엔드·모델 계열에 맞는 파라미터로.

    gpt-5 계열은 temperature 를 받지 않고 max_completion_tokens(추론 토큰 포함)를 쓴다.
    Ollama 의 생각(thinking) 모델도 추론 토큰이 한도를 먹으므로 한도를 넉넉히 준다.
    """
    b = normalize(backend) or active()
    c = client(b)
    if c is None:
        raise RuntimeError(f"LLM 백엔드({b})를 쓸 수 없다 — 키/주소 확인")
    model = extra.pop("model", None) or chat_model(b)
    kw: Dict[str, Any] = {"model": model, "messages": messages}
    if json_mode:
        kw["response_format"] = {"type": "json_object"}
    if b == "openai" and model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
        kw["max_completion_tokens"] = max(4000, max_tokens * 4)
        if extra.get("reasoning_effort"):
            kw["reasoning_effort"] = extra["reasoning_effort"]
    elif b == "ollama":
        kw["max_tokens"] = max(4000, max_tokens * 4)
        kw["temperature"] = temperature
        if extra.get("reasoning_effort"):
            kw["reasoning_effort"] = extra["reasoning_effort"]
    else:
        kw["max_tokens"] = max_tokens
        kw["temperature"] = temperature
    try:
        return c.chat.completions.create(**kw)
    except Exception as e:  # noqa: BLE001
        if "reasoning" in str(e).lower() and kw.pop("reasoning_effort", None):
            return c.chat.completions.create(**kw)
        raise
