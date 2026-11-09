"""OpenAI 토큰 사용량 장부 + 예산 기반 모델 선택 (2026-10-09).

왜
  사용자 결정: 누적 1,000만 토큰까지는 gpt-5.6-luna 를 쓰고, 그 뒤에는 gpt-6-luna 로 넘어간다.
  OpenAI 사용량 API 는 프로젝트 키로 읽을 수 없어(api.usage.read 권한 없음) 우리가 직접 센다.

무엇을 세나
  OpenAI 호출(OCR 비전, 상담, 번역)이 돌려주는 usage 의 prompt_tokens + completion_tokens(= total_tokens,
  추론 토큰 포함)를 모델별로 누적한다. 실패한 호출은 세지 않는다. 장부는 JSON 한 파일 —
  data/store/llm_usage.json (건강정보는 아니지만 data/store/ 는 gitignored 라 저장소에 안 들어간다).

선택 규칙 (choose)
  OPENAI_BUDGET_MODEL 이 비어 있거나 OPENAI_BUDGET_TOKENS 가 0 이면 기본 모델 그대로.
  예산 모델의 누적 total 이 예산 미만이면 예산 모델, 아니면 기본 모델(OPENAI_VISION_MODEL / OPENAI_CHAT_MODEL).
  한 요청 안에서는 한 번 고른 모델을 끝까지 쓴다(두 번 읽기의 1·2차 판독이 다른 모델이 되지 않게 호출부가 model 을 들고 다닌다).

한계
  프로세스 안에서는 락으로 직렬화하지만, 여러 워커 프로세스가 같은 파일을 갱신하면 몇 건 어긋날 수 있다.
  Render 처럼 디스크가 휘발성이면 배포마다 0 에서 다시 센다 — 영구 디스크를 붙이거나 LLM_USAGE_LEDGER 를 그쪽으로 둔다.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def ledger_path() -> Path:
    p = Path(getattr(settings, "llm_usage_ledger", "") or "data/store/llm_usage.json")
    return p if p.is_absolute() else Path(__file__).resolve().parent.parent / p


def load() -> Dict[str, Any]:
    p = ledger_path()
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — 장부가 깨져도 호출을 막지 않는다
        logger.warning(f"토큰 장부를 읽지 못함({p}): {e}")
    return {"models": {}, "updated": None}


def _save(data: Dict[str, Any]) -> None:
    p = ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _n(usage: Any, name: str) -> int:
    v = getattr(usage, name, None) if not isinstance(usage, dict) else usage.get(name)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def record(model: Optional[str], usage: Any, backend: str = "openai") -> Optional[Dict[str, Any]]:
    """성공한 호출의 usage 를 모델별로 누적한다. usage 가 없으면 건너뛴다."""
    if backend != "openai" or not model or usage is None:
        return None
    prompt, completion = _n(usage, "prompt_tokens"), _n(usage, "completion_tokens")
    total = _n(usage, "total_tokens") or (prompt + completion)
    details = getattr(usage, "completion_tokens_details", None) if not isinstance(usage, dict) else usage.get("completion_tokens_details")
    reasoning = _n(details, "reasoning_tokens") if details is not None else 0
    bm, limit = budget()
    with _lock:
        data = load()
        m = data.setdefault("models", {}).setdefault(model, {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0, "requests": 0})
        before = m["total"]
        m["prompt"] += prompt
        m["completion"] += completion
        m["reasoning"] += reasoning
        m["total"] += total
        m["requests"] += 1
        data["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        # 예산 모델이 이번 호출로 한도를 넘었다 → 유료 모델로 넘어간다는 알림 대상
        if bm and limit > 0 and model == bm and before < limit <= m["total"] and not data.get("budget_crossed_at"):
            data["budget_crossed_at"] = data["updated"]
            data["budget_notify"] = {"status": "pending", "tries": 0}
        try:
            _save(data)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"토큰 장부 저장 실패: {e}")
        snapshot = dict(m)
    _maybe_notify()
    return snapshot


# ---------------------------------------------------------------------------
# 예산 초과 알림 — 무료 한도를 넘겨 유료 모델(기본 모델)로 넘어가면 메일을 보낸다 (2026-10-10 사용자 요청)
# ---------------------------------------------------------------------------
def notify_recipients() -> list[str]:
    raw = getattr(settings, "openai_budget_notify_to", "") or ""
    return [a.strip() for a in raw.replace(";", ",").split(",") if a.strip()]


def budget_notice(test: bool = False) -> tuple[str, str, str]:
    """(제목, 텍스트 본문, HTML 본문)."""
    s = status()
    bm, limit, used_ = s["budget_model"], s["budget_tokens"], s["budget_used"]
    paid = settings.openai_vision_model
    when = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = "[테스트] " if test else ""
    subject = f"{head}[알레르기 OCR] OpenAI 무료 토큰 한도 초과 — 유료 모델({paid}) 사용 중"
    lines = [
        f"{'※ 테스트 메일입니다 — 실제로 한도를 넘은 것은 아닙니다.' if test else ''}",
        f"무료 모델 {bm} 의 누적 사용량이 한도를 넘었습니다.",
        f"- 한도: {limit:,} 토큰",
        f"- 누적 사용: {used_:,} 토큰 (prompt+completion, 추론 토큰 포함)",
        f"- 넘은 시각: {s.get('crossed_at') or when}",
        f"- 지금부터 OCR·상담·번역은 유료 모델 {paid} 로 처리됩니다(설정 OPENAI_VISION_MODEL / OPENAI_CHAT_MODEL).",
        "",
        "조치: 무료 한도를 늘리려면 OPENAI_BUDGET_TOKENS, 다시 무료 모델로 돌리려면 장부(LLM_USAGE_LEDGER)를 초기화하세요.",
        f"장부: {s['ledger']}",
        "",
        "— APAAACI 알레르기 결과지 OCR 서비스(자동 발송)",
    ]
    text = "\n".join(l for l in lines if l is not None)
    html = "<p>" + "<br>".join(html_escape(l) for l in lines if l) + "</p>"
    return subject, text, html


def html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_budget_notice(test: bool = False) -> Dict[str, Any]:
    """메일을 보낸다. SMTP 가 없으면 보내지 않고 상태만 돌려준다."""
    from services.email_service import get_email_service
    svc = get_email_service()
    to = notify_recipients()
    if not to:
        return {"status": "no_recipients"}
    if not svc.configured:
        return {"status": "smtp_unconfigured", "to": to}
    subject, text, html = budget_notice(test=test)
    sent, failed = [], {}
    for addr in to:
        try:
            svc.send(addr, subject, html, text)
            sent.append(addr)
        except Exception as e:  # noqa: BLE001
            failed[addr] = f"{type(e).__name__}: {str(e)[:120]}"
    return {"status": "sent" if sent and not failed else ("partial" if sent else "failed"), "sent": sent, "failed": failed}


def _maybe_notify() -> None:
    """한도를 넘긴 뒤 아직 못 보낸 알림을 보낸다(실패하면 1시간 뒤 다시). 호출을 막지 않는다."""
    try:
        data = load()
        n = data.get("budget_notify")
        if not data.get("budget_crossed_at") or not n or n.get("status") == "sent":
            return
        last = n.get("last_try")
        if last and (dt.datetime.now() - dt.datetime.fromisoformat(last)).total_seconds() < 3600:
            return
        result = send_budget_notice()
        with _lock:
            data = load()
            n = data.setdefault("budget_notify", {"status": "pending", "tries": 0})
            n.update({"status": result["status"], "last_try": dt.datetime.now().isoformat(timespec="seconds"),
                      "tries": int(n.get("tries", 0)) + 1, "detail": {k: v for k, v in result.items() if k != "status"}})
            _save(data)
        if result["status"] == "sent":
            logger.warning("OpenAI 무료 토큰 한도 초과 — 유료 모델 사용 알림 메일 발송")
        else:
            logger.warning(f"OpenAI 무료 토큰 한도 초과 알림을 보내지 못함: {result}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"예산 초과 알림 처리 실패: {e}")


def used(model: str) -> int:
    return int(((load().get("models") or {}).get(model) or {}).get("total", 0))


def budget() -> tuple[str, int]:
    return (getattr(settings, "openai_budget_model", "") or "").strip(), int(getattr(settings, "openai_budget_tokens", 0) or 0)


def choose(default_model: str) -> str:
    """예산 모델이 예산 안이면 예산 모델, 아니면 기본 모델."""
    bm, limit = budget()
    if not bm or limit <= 0 or bm == default_model:
        return default_model
    return bm if used(bm) < limit else default_model


def status() -> Dict[str, Any]:
    """화면·관리자용 요약. 키·주소는 없다."""
    bm, limit = budget()
    data = load()
    u = used(bm) if bm else 0
    return {
        "budget_model": bm or None, "budget_tokens": limit, "budget_used": u,
        "budget_remaining": max(0, limit - u) if (bm and limit) else None,
        "budget_exhausted": bool(bm and limit and u >= limit),
        "active_vision_model": choose(settings.openai_vision_model),
        "active_chat_model": choose(settings.openai_chat_model),
        "crossed_at": data.get("budget_crossed_at"), "notify": data.get("budget_notify"),
        "notify_to": notify_recipients(),
        "models": data.get("models") or {}, "updated": data.get("updated"),
        "ledger": str(ledger_path()),
    }


if __name__ == "__main__":
    import sys
    if "--notify-test" in sys.argv:      # SMTP 설정 확인용 — 수신자에게 '[테스트]' 메일을 보낸다
        print(json.dumps(send_budget_notice(test=True), ensure_ascii=False, indent=1))
    elif "--notify-now" in sys.argv:     # 한도를 넘긴 뒤 못 보낸 알림을 지금 보낸다
        print(json.dumps(send_budget_notice(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(status(), ensure_ascii=False, indent=1))
