"""관리자 API (/api/admin/*) — 로그인과 사용자·세션·산출물·상담 대화 조회.

접근 통제
  - ADMIN_PASSWORD 가 비어 있으면 관리자 기능 전체가 꺼진다(503 admin_disabled). 열린 상태가 되지 않는다.
  - 로그인은 아이디·비밀번호를 SHA-256 으로 해시한 뒤 상수 시간 비교한다.
  - 로그인하면 HMAC 서명 토큰을 HttpOnly·SameSite=Strict 쿠키로만 준다(응답 본문에는 넣지 않는다).
  - 서명 키는 서버 비밀값(ADMIN_SESSION_SECRET, 없으면 서버가 만들어 DB 에 둔 난수)으로 만든다.
    비밀번호만으로는 만들 수 없어서, 토큰이 새도 그것으로 비밀번호를 오프라인 대입할 수 없다.
  - 로그아웃하면 그 토큰을 폐기 목록에 올린다(쿠키만 지우는 게 아니다).
  - 상태를 바꾸는 요청(POST/DELETE…)은 Origin/Referer 가 이 서버여야 한다.
  - 로그인 실패는 IP 별로, 그리고 IP 와 무관하게 전체로도 센다(IP 를 바꿔 가며 대입하는 것을 늦춘다).
  - 로그인 외 모든 엔드포인트는 require_admin 을 거친다. 응답은 캐시하지 않는다(no-store).
조회 내용(건강정보)은 로그에 남기지 않는다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from config.settings import settings
from services.store_service import LANGS, Store, get_store, session_tag

logger = logging.getLogger(__name__)

COOKIE_NAME = "admin_session"
# IP 별: 창(window) 안에 실패가 이만큼 쌓이면 그 IP 는 창이 지날 때까지 막는다.
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_SEC = 600
LOGIN_MAX_TRACKED_IPS = 2048          # 실패 기록을 들고 있을 IP 수 상한(넘으면 오래된 것부터 버린다)
# 전체: 창 안의 실패가 이만큼을 넘으면, 넘은 횟수에 따라 다음 시도까지 기다리게 한다(1, 2, 4 … 초, 상한까지).
# IP 를 바꿔 가며 대입해도 초당 시도 수가 묶인다. 완전히 잠그지는 않으므로 관리자를 영구히 내쫓을 수 없다.
LOGIN_GLOBAL_FREE_FAILURES = 10
LOGIN_GLOBAL_MAX_DELAY_SEC = 60
REVOKED_MAX_IN_MEMORY = 4096

_login_lock = threading.Lock()
_failures: "OrderedDict[str, deque]" = OrderedDict()
_global_failures: deque = deque()
_global_next_allowed = 0.0
_revoked: Dict[str, int] = {}                      # 저장소가 꺼져 있을 때의 폐기 목록(jti → 만료 시각)
_PROCESS_SECRET = secrets.token_hex(32)            # 저장소도 ADMIN_SESSION_SECRET 도 없을 때만 쓴다
_secret_cache: Dict[str, str] = {}

# 번역 재시도는 판정을 다시 계산해야 해서 server.py 가 실행 함수를 넣어 준다.
i18n_retry_hook: Optional[Callable[[str], Dict[str, Any]]] = None


def admin_enabled() -> bool:
    return bool(settings.admin_password)


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _server_secret() -> str:
    """토큰 서명에 쓰는 서버 비밀값. 설정값 → DB 에 둔 난수 → 프로세스 난수 순.
    프로세스 난수는 재시작하면 바뀌고 프로세스마다 다르다(그 경우 ADMIN_SESSION_SECRET 을 설정할 것)."""
    if settings.admin_session_secret:
        return settings.admin_session_secret
    store = get_store()
    if store is None:
        return _PROCESS_SECRET
    key = str(store.main_path)
    if key not in _secret_cache:
        try:
            _secret_cache[key] = store.get_or_create_secret("admin_session")
        except Exception as e:  # noqa: BLE001
            logger.error("관리자 서명 키를 저장소에서 읽지 못했습니다: %s", type(e).__name__)
            return _PROCESS_SECRET
    return _secret_cache[key]


def _signing_key() -> bytes:
    """서버 비밀값을 키로, 계정 정보를 메시지로 한 HMAC. 비밀번호를 바꾸면 기존 로그인이 풀리지만,
    서버 비밀값 없이는 토큰에서 비밀번호를 확인할 방법이 없다."""
    return hmac.new(_server_secret().encode("utf-8"),
                    b"admin-session|" + _digest(settings.admin_username) + _digest(settings.admin_password),
                    hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(username: str, ttl_seconds: Optional[int] = None) -> str:
    ttl = ttl_seconds if ttl_seconds is not None else settings.admin_session_hours * 3600
    payload = _b64(f"{username}|{int(time.time()) + ttl}|{secrets.token_hex(12)}".encode())
    sig = _b64(hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _parse_token(token: Optional[str]) -> Optional[Tuple[str, int, str]]:
    """서명·만료·계정이 맞으면 (아이디, 만료 시각, jti). 폐기 여부는 보지 않는다."""
    if not token or not admin_enabled():
        return None
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(_signing_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        username, exp, jti = _unb64(payload).decode().rsplit("|", 2)
        if int(exp) < time.time() or not hmac.compare_digest(_digest(username),
                                                             _digest(settings.admin_username)):
            return None
        return username, int(exp), jti
    except Exception:  # noqa: BLE001
        return None


def _is_revoked(jti: str) -> bool:
    if jti in _revoked:
        return True
    store = get_store()
    try:
        return bool(store and store.is_admin_token_revoked(jti))
    except Exception as e:  # noqa: BLE001
        logger.error("토큰 폐기 목록을 읽지 못했습니다: %s", type(e).__name__)
        return True                     # 확인할 수 없으면 통과시키지 않는다


def revoke_token(token: Optional[str]) -> bool:
    """유효한 토큰이면 폐기 목록에 올린다(만료 시각까지만 보관)."""
    parsed = _parse_token(token)
    if not parsed:
        return False
    _username, exp, jti = parsed
    now = int(time.time())
    for old in [k for k, v in _revoked.items() if v < now]:
        _revoked.pop(old, None)
    while len(_revoked) >= REVOKED_MAX_IN_MEMORY:
        _revoked.pop(next(iter(_revoked)))
    _revoked[jti] = exp
    store = get_store()
    if store is not None:
        try:
            store.revoke_admin_token(jti, exp)
        except Exception as e:  # noqa: BLE001
            logger.error("토큰 폐기를 저장하지 못했습니다(이 프로세스에서는 폐기됨): %s", type(e).__name__)
    return True


def verify_token(token: Optional[str]) -> Optional[str]:
    """유효하고 폐기되지 않았으면 관리자 아이디, 아니면 None."""
    parsed = _parse_token(token)
    if not parsed or _is_revoked(parsed[2]):
        return None
    return parsed[0]


def api_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def require_enabled() -> None:
    if not admin_enabled():
        raise api_error(503, "admin_disabled", "관리자 기능이 꺼져 있습니다. ADMIN_PASSWORD 를 설정하세요.")


def _allowed_origin_hosts(request: Request) -> set:
    hosts = {request.headers.get("host", "").strip().lower()}
    for item in (settings.admin_allowed_origins or "").split(","):
        item = item.strip().lower()
        if item:
            hosts.add(urlsplit(item).netloc or item)
    hosts.discard("")
    return hosts


def require_same_origin(request: Request) -> None:
    """상태를 바꾸는 요청은 이 서버의 화면에서 온 것이어야 한다(CSRF 방어).

    브라우저는 POST/DELETE 에 Origin 을 붙인다. Origin 이 없으면 Referer 를 본다. 둘 다 없거나
    호스트가 이 서버(Host 헤더, 또는 ADMIN_ALLOWED_ORIGINS)와 다르면 거절한다."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    source = request.headers.get("origin") or request.headers.get("referer") or ""
    host = urlsplit(source).netloc.lower() if "://" in source else ""
    if not host or host not in _allowed_origin_hosts(request):
        raise api_error(403, "bad_origin", "다른 출처에서 온 요청은 받지 않습니다.")


def require_admin(request: Request, response: Response) -> str:
    require_enabled()
    user = verify_token(request.cookies.get(COOKIE_NAME))
    if not user:
        raise api_error(401, "unauthorized", "관리자 로그인이 필요합니다.")
    require_same_origin(request)
    response.headers["Cache-Control"] = "no-store"
    return user


def _store() -> Store:
    store = get_store()
    if store is None:
        raise api_error(503, "storage_disabled", "저장소가 꺼져 있습니다(STORAGE_ENABLED).")
    return store


def _is_https(request: Request) -> bool:
    return (request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https")


class LoginRequest(BaseModel):
    username: str
    password: str


auth_router = APIRouter(prefix="/api/admin", tags=["admin"])
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@auth_router.get("/status")
def status(request: Request):
    """관리자 화면이 로그인 폼·비활성 안내·대시보드 중 무엇을 보일지 정하는 데 쓴다(인증 불필요)."""
    token = request.cookies.get(COOKIE_NAME)
    user = verify_token(token)
    return {"enabled": admin_enabled(), "authenticated": bool(user), "username": user}


def _login_retry_after(ip: str, now: float) -> int:
    """지금 로그인 시도를 받을 수 없으면 기다릴 초, 받을 수 있으면 0. _login_lock 안에서 부른다."""
    fails = _failures.get(ip)
    if fails is not None:
        while fails and now - fails[0] > LOGIN_WINDOW_SEC:
            fails.popleft()
        if not fails:
            del _failures[ip]
        elif len(fails) >= LOGIN_MAX_FAILURES:
            return max(1, int(LOGIN_WINDOW_SEC - (now - fails[0])) + 1)
    if now < _global_next_allowed:
        return max(1, int(_global_next_allowed - now) + 1)
    return 0


def _record_login_failure(ip: str, now: float) -> None:
    global _global_next_allowed
    _failures.setdefault(ip, deque()).append(now)
    _failures.move_to_end(ip)
    while len(_failures) > LOGIN_MAX_TRACKED_IPS:
        _failures.popitem(last=False)
    _global_failures.append(now)
    while _global_failures and now - _global_failures[0] > LOGIN_WINDOW_SEC:
        _global_failures.popleft()
    excess = len(_global_failures) - LOGIN_GLOBAL_FREE_FAILURES
    if excess > 0:
        _global_next_allowed = now + min(2 ** min(excess - 1, 10), LOGIN_GLOBAL_MAX_DELAY_SEC)


def reset_login_throttle() -> None:
    """실패 기록을 모두 지운다(테스트용)."""
    global _global_next_allowed
    with _login_lock:
        _failures.clear()
        _global_failures.clear()
        _global_next_allowed = 0.0


@auth_router.post("/login")
def login(req: LoginRequest, request: Request, response: Response):
    require_enabled()
    require_same_origin(request)
    # request.client 는 uvicorn 이 정한 접속 주소다. 신뢰하는 프록시(FORWARDED_ALLOW_IPS)가 붙인
    # X-Forwarded-For 만 반영되므로, 클라이언트가 헤더를 꾸며 남의 IP 를 잠그거나 한도를 피할 수 없다.
    ip = request.client.host if request.client else "unknown"
    with _login_lock:           # 확인·판정·기록을 한 번에 — 동시에 들어온 시도가 같은 여유분을 함께 쓰지 못한다
        now = time.time()
        wait = _login_retry_after(ip, now)
        if wait:
            err = api_error(429, "too_many_attempts", "로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.")
            err.headers = {"Retry-After": str(wait)}
            raise err
        ok_user = hmac.compare_digest(_digest(req.username), _digest(settings.admin_username))
        ok_pass = hmac.compare_digest(_digest(req.password), _digest(settings.admin_password))
        if not (ok_user and ok_pass):
            _record_login_failure(ip, now)
            logger.warning("관리자 로그인 실패")
            raise api_error(401, "invalid_credentials", "아이디 또는 비밀번호가 올바르지 않습니다.")
        _failures.pop(ip, None)
    token = make_token(settings.admin_username)
    max_age = settings.admin_session_hours * 3600
    response.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True, samesite="strict",
                        secure=_is_https(request), path="/")
    response.headers["Cache-Control"] = "no-store"
    logger.info("관리자 로그인")
    # 토큰은 HttpOnly 쿠키로만 준다. 본문에 넣으면 화면 스크립트(와 XSS)가 읽을 수 있다.
    return {"ok": True, "username": settings.admin_username, "expires_in": max_age}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    require_same_origin(request)
    revoke_token(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True}


@router.get("/me")
def me(request: Request, user: str = Depends(require_admin)):
    """client_ip 는 서버가 본 접속 주소다 — 프록시 뒤에서 FORWARDED_ALLOW_IPS 가 맞는지 확인하는 데 쓴다."""
    return {"username": user, "client_ip": request.client.host if request.client else None}


@router.get("/stats")
def stats(days: int = 30, tz_offset: int = 0):
    """집계. tz_offset 은 관리자 브라우저의 UTC 대비 분(한국 540) — 일자 구분에 쓴다."""
    return _store().stats(days, tz_offset)


@router.get("/users")
def users(q: str = "", limit: int = 50, offset: int = 0):
    return _store().list_users(q, max(1, min(limit, 200)), max(0, offset))


@router.get("/users/{user_id}")
def user_detail(user_id: int):
    u = _store().get_user(user_id)
    if not u:
        raise api_error(404, "not_found", "사용자를 찾을 수 없습니다.")
    return u


@router.delete("/users/{user_id}")
def user_delete(user_id: int):
    if not _store().delete_user(user_id):
        raise api_error(404, "not_found", "사용자를 찾을 수 없습니다.")
    logger.info("관리자: 사용자 삭제 id=%s", user_id)
    return {"ok": True}


@router.get("/sessions")
def sessions(user_id: Optional[int] = None, anonymous: bool = False, q: str = "",
             limit: int = 50, offset: int = 0):
    return _store().list_sessions(user_id, anonymous, q, max(1, min(limit, 200)), max(0, offset))


@router.get("/sessions/{session_id}")
def session_detail(session_id: str):
    """입력한 검사결과·문진, 판정 요약, 언어별 산출물 상태, 상담 대화, 메일 발송 기록."""
    store = _store()
    s = store.get_session(session_id)
    if not s:
        raise api_error(404, "not_found", "세션을 찾을 수 없습니다.")
    st = store.localized_status(session_id)
    s["i18n"] = {lang: st.get(lang, {"status": "missing", "error": None, "updated_at": None})
                 for lang in LANGS}
    s["chat"] = store.list_chat(session_id)
    s["emails"] = store.list_emails(session_id)
    return s


@router.delete("/sessions/{session_id}")
def session_delete(session_id: str):
    if not _store().delete_session(session_id):
        raise api_error(404, "not_found", "세션을 찾을 수 없습니다.")
    logger.info("관리자: 세션 삭제 session#%s", session_tag(session_id))
    return {"ok": True}


@router.get("/sessions/{session_id}/outputs/{lang}")
def session_output(session_id: str, lang: str):
    """한 언어의 저장된 산출물 전체(리포트 마크다운·HTML·인쇄형 문서·카드뉴스·판정)."""
    out = _store().get_localized(session_id, lang)
    if not out:
        raise api_error(404, "not_found", "이 언어의 산출물이 없습니다.")
    return out


@router.post("/sessions/{session_id}/i18n/retry")
def session_i18n_retry(session_id: str):
    """ready 가 아닌 언어를 다시 만든다(서버 재시작으로 pending 에 멈춘 경우 등). 끝난 뒤 상태를 돌려준다."""
    if not _store().session_exists(session_id):
        raise api_error(404, "not_found", "세션을 찾을 수 없습니다.")
    if i18n_retry_hook is None:
        raise api_error(503, "unavailable", "재시도를 실행할 수 없습니다.")
    return i18n_retry_hook(session_id)
