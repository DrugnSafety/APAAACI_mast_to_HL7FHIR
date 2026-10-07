"""
FastAPI 백엔드 — 알레르기 검사 환자용 리포트 플랫폼 (비-Streamlit 웹앱)

기존 services/ 는 프레임워크에 독립적이므로 그대로 재사용한다.
프론트엔드(web/)는 정적 파일로 서빙한다.

실행:
    uvicorn server:app --reload
    # 또는
    python server.py
"""

import base64
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Body, BackgroundTasks, Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.datastructures import MutableHeaders

from models.schemas import (
    OCRResult, PatientInfo, AllergenResult, TestType, InterpretationType,
    ScreeningProfile, SymptomFeedback, ClinicalRelevance, public_relevance,
)
from services.knowledge_service import get_knowledge_service, normalize_category
from services.relevance_service import get_relevance_service, RelevanceService
from services.questionnaire_service import get_questionnaire_engine
from services.screening_service import get_screening_service, normalize_lang
from services.report_service import get_report_service
from services.cardnews_service import get_cardnews_service
from services.fhir_service import FHIRService
from services import admin_api
from services.admin_api import api_error
from services.store_service import LANGS, close_store, get_store, hash_ip, normalize_email, session_tag
from services.email_service import get_email_service
from config.settings import BASE_DIR, settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class _RedactSessionIds(logging.Filter):
    """접근 로그의 URL 에서 세션 id 를 짧은 해시로 바꾼다. 세션 id 는 그 자체가 결과 열람 권한이라
    로그를 볼 수 있는 사람이 남의 결과를 열 수 있게 두지 않는다."""
    _PATH = re.compile(r"(/sessions/)([^/?\s\"]+)")

    def _clean(self, value):
        if not isinstance(value, str) or "/sessions/" not in value:
            return value
        return self._PATH.sub(lambda m: m.group(1) + "#" + session_tag(m.group(2)), value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._clean(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._clean(a) for a in record.args)
        return True


for _name in ("uvicorn.access", "httpx"):
    logging.getLogger(_name).addFilter(_RedactSessionIds())

WEB_DIR = BASE_DIR / "web"
RETENTION_INTERVAL_SEC = 6 * 3600


def recover_interrupted_work() -> Dict[str, int]:
    """서버를 띄울 때: 이전 프로세스가 끝내지 못한 언어별 산출물(pending/running)과 발송 예약을
    failed(interrupted) 로 돌린다. 그대로 두면 화면이 끝나지 않을 작업을 계속 기다린다.
    다시 만들려면 관리자 화면의 '번역 재시도'(또는 같은 세션으로 다시 판정)를 쓴다."""
    store = get_store()
    if store is None:
        return {"localized": 0, "emails": 0}
    done = store.fail_interrupted()
    if any(done.values()):
        logger.warning("재시작 복구: 멈춰 있던 언어별 산출물 %d건, 발송 예약 %d건을 실패로 표시",
                       done["localized"], done["emails"])
    return done


def purge_expired_sessions() -> int:
    """SESSION_RETENTION_DAYS 가 지난 세션을 지운다(0 이면 아무것도 지우지 않는다)."""
    days = settings.session_retention_days
    store = get_store()
    if days <= 0 or store is None:
        return 0
    n = store.purge_older_than(days)
    if n:
        logger.info("보존 기간(%d일)이 지난 세션 %d건 삭제", days, n)
    return n


def _retention_loop(stop: threading.Event) -> None:
    while not stop.wait(RETENTION_INTERVAL_SEC):
        try:
            purge_expired_sessions()
        except Exception as e:  # noqa: BLE001
            logger.error("보존 기간 정리 실패: %s", type(e).__name__)


@asynccontextmanager
async def _lifespan(_app):
    stop = threading.Event()
    try:
        recover_interrupted_work()
        purge_expired_sessions()
    except Exception as e:  # noqa: BLE001
        logger.error("시작 정리 실패(서버는 계속 뜬다): %s", type(e).__name__)
    if settings.session_retention_days > 0:
        threading.Thread(target=_retention_loop, args=(stop,), daemon=True, name="retention").start()
    yield
    stop.set()
    close_store()       # 풀의 연결을 닫아 WAL 을 체크포인트한다


app = FastAPI(title="알레르기 검사 환자 리포트 플랫폼", version="2.0", lifespan=_lifespan)


class LLMBackendMiddleware:
    """요청 헤더 X-LLM-Backend(openai|ollama)로 이번 요청의 LLM 백엔드를 정한다.
    화면의 'AI 엔진' 선택이 OCR·상담·번역(리포트 안의 번역 포함)까지 한 번에 따라가게 한다."""

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        from services import llm_backend
        raw = dict(scope.get("headers") or []).get(b"x-llm-backend", b"").decode("latin-1")
        token = llm_backend._current.set(llm_backend.normalize(raw))
        try:
            return await self.app(scope, receive, send)
        finally:
            llm_backend._current.reset(token)


# ---- 보안 응답 헤더 ----
_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
_csp_lock = threading.Lock()
_csp_cache: Dict[str, Any] = {"at": 0.0, "html": None, "deck": None}


def _script_hashes(html: str) -> List[str]:
    return ["'sha256-" + base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode() + "'"
            for body in _INLINE_SCRIPT.findall(html) if body.strip()]


def _deck_script_hashes() -> Optional[List[str]]:
    """카드뉴스에 들어가는 인라인 스크립트의 해시. 화면이 카드뉴스를 srcdoc iframe 으로 띄우고, 그 iframe 은
    부모의 CSP 를 물려받는다. 스크립트는 환자 값이 섞이지 않는 고정 코드라 해시로 허용할 수 있다."""
    try:
        result = get_relevance_service().build_assessments(_demo_ocr(), None)
        get_questionnaire_engine().classify(result, {}, None)
        from services.cardnews_classic import get_classic_cardnews_service
        hashes: List[str] = []
        for svc in (get_cardnews_service(), get_classic_cardnews_service()):
            hashes += _script_hashes(svc.generate_html(result, {"name": "-"}, None))
        return sorted(set(hashes))
    except Exception as e:  # noqa: BLE001
        logger.warning("카드뉴스 스크립트 해시를 만들지 못했습니다(인라인 스크립트를 통째로 허용): %s",
                       type(e).__name__)
        return None


def _csp_script_src() -> str:
    """script-src 값. 정적 화면(web/**/*.html)의 인라인 스크립트와 카드뉴스 스크립트만 해시로 허용한다."""
    now = time.time()
    with _csp_lock:
        if _csp_cache["html"] is None or now - _csp_cache["at"] > 10:     # 화면 파일이 바뀌면 10초 안에 반영
            hashes: List[str] = []
            for f in sorted(WEB_DIR.rglob("*.html")) if WEB_DIR.exists() else []:
                try:
                    hashes += _script_hashes(f.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass
            _csp_cache.update(at=now, html=sorted(set(hashes)))
        if _csp_cache["deck"] is None:
            _csp_cache["deck"] = _deck_script_hashes() or ["'unsafe-inline'"]
        deck, page = _csp_cache["deck"], _csp_cache["html"]
    if "'unsafe-inline'" in deck:
        return "'self' 'unsafe-inline'"
    return " ".join(["'self'", *page, *deck])


_STYLE_SRC = "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com"
_FONT_SRC = "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com"


def _security_headers(path: str) -> Dict[str, str]:
    base = {"X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"}
    if path.startswith("/api/"):
        # JSON 응답: 건강정보가 브라우저·중간 캐시에 남지 않게 한다(항원 목록처럼 스스로 캐시를 정한 응답은 그대로)
        return {**base, "Cache-Control": "no-store", "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"}
    if path == "/admin" or path.startswith("/admin/"):
        return {**base, "X-Frame-Options": "DENY", "Cache-Control": "no-store",
                "Content-Security-Policy": "; ".join([
                    "default-src 'none'", f"script-src {_csp_script_src()}", _STYLE_SRC, _FONT_SRC,
                    "img-src 'self' data:", "connect-src 'self'", "frame-src 'self' about:",
                    "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'"])}
    return {**base, "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "; ".join([
                "default-src 'self'", f"script-src {_csp_script_src()}", _STYLE_SRC, _FONT_SRC,
                "img-src 'self' data: blob:", "media-src 'self' data: blob:", "connect-src 'self'",
                "frame-src 'self' about: blob:", "object-src 'none'", "base-uri 'self'",
                "form-action 'self'", "frame-ancestors 'self'"])}


class SecurityHeadersMiddleware:
    """모든 응답에 nosniff·프레임 제한·CSP 를 붙인다. 엔드포인트가 이미 정한 헤더는 덮어쓰지 않는다."""

    def __init__(self, app_):
        self.app = app_

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not settings.security_headers:
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in _security_headers(path).items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        return await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """요청 본문 크기 상한. Content-Length 로 먼저 거르고, 길이를 밝히지 않은 요청은 읽으면서 센다."""

    def __init__(self, app_):
        self.app = app_

    @staticmethod
    def _limit(path: str) -> int:
        if path == "/api/ocr":       # 파일 + multipart 경계·헤더 여유분
            return settings.max_file_size_mb * 1024 * 1024 + 64 * 1024
        return settings.max_request_body_kb * 1024

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)
        limit = self._limit(scope.get("path") or "")
        state = {"read": 0, "over": False, "started": False}
        declared = dict(scope.get("headers") or []).get(b"content-length", b"").decode("latin-1")
        if declared.isdigit() and int(declared) > limit:
            state["over"] = True
        else:
            async def counted_receive():
                message = await receive()
                if message["type"] == "http.request":
                    state["read"] += len(message.get("body") or b"")
                    if state["read"] > limit:
                        state["over"] = True
                        raise HTTPException(status_code=413, detail="payload too large")
                return message

            async def guarded_send(message):
                if state["over"]:
                    return                      # 앱이 만든 오류 응답은 버리고 아래에서 413 을 보낸다
                if message["type"] == "http.response.start":
                    state["started"] = True
                await send(message)

            try:
                await self.app(scope, counted_receive, guarded_send)
            except Exception:
                if not state["over"]:
                    raise
        if state["over"] and not state["started"]:
            body = json.dumps({"detail": {"code": "payload_too_large",
                                          "message": "요청 본문이 너무 큽니다."}}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"connection", b"close")]})
            await send({"type": "http.response.body", "body": body})


app.add_middleware(LLMBackendMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)      # 가장 바깥 — 413 같은 미들웨어 응답에도 헤더가 붙는다


# ---- 접속 IP 당 요청 수 제한 ----
class _RateLimiter:
    """IP·종류별 최근 요청 시각을 메모리에 둔다(프로세스별). 키 수에 상한을 두어 메모리가 불어나지 않게 한다."""

    def __init__(self, max_keys: int = 20000):
        self._hits: "OrderedDict[tuple, deque]" = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys

    def check(self, bucket: str, ip: str, limit: int, window: float = 60.0) -> int:
        """받을 수 있으면 0(기록함), 한도를 넘었으면 기다릴 초."""
        now = time.monotonic()
        key = (bucket, ip)
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            self._hits.move_to_end(key)
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, int(window - (now - hits[0])) + 1)
            hits.append(now)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = _RateLimiter()


def _client_ip(request: Request) -> str:
    """uvicorn 이 정한 접속 주소. X-Forwarded-For 는 신뢰하는 프록시(FORWARDED_ALLOW_IPS)가 붙인 것만 반영된다."""
    return request.client.host if request.client else "unknown"


def rate_limit(bucket: str):
    """FastAPI 의존성: settings.rate_limit_<bucket>_per_min 을 넘으면 429 rate_limited."""
    def dependency(request: Request) -> None:
        limit = int(getattr(settings, f"rate_limit_{bucket}_per_min", 0) or 0)
        if limit <= 0:
            return
        wait = rate_limiter.check(bucket, _client_ip(request), limit)
        if wait:
            err = api_error(429, "rate_limited", "요청이 너무 잦습니다. 잠시 후 다시 시도하세요.")
            err.headers = {"Retry-After": str(wait)}
            raise err
    return dependency


# ============================================================
# 요청 모델
# ============================================================
class AssessRequest(BaseModel):
    ocr: OCRResult
    screening: Optional[ScreeningProfile] = None
    # 화면 표시 언어(ko/en/zh). 서버 생성 콘텐츠(문진 문항 등) 번역에 쓴다.
    lang: str = "ko"


class ClassifyRequest(BaseModel):
    ocr: OCRResult
    screening: Optional[ScreeningProfile] = None
    answers: Dict[str, Any] = {}
    # 프론트 UI 구분: "quest"(알러젠 탐험 퀘스트, 기본) / "classic"(재설계 이전 UI)
    # 카드뉴스 디자인·문구를 UI 에 맞춰 고른다. 판정·리포트·FHIR 는 동일하다.
    ui: str = "quest"
    # 화면 표시 언어(ko/en/zh). 서버 생성 콘텐츠 번역에 사용한다.
    lang: str = "ko"
    # 저장용(선택). session_id 를 주면 같은 세션에 덮어쓰고, 없으면 새 세션을 만든다.
    # email 을 주면 그 사용자의 세션으로 묶는다(없으면 익명 세션).
    session_id: Optional[str] = None
    email: Optional[str] = None


# ============================================================
# 공통 헬퍼
# ============================================================
# 서버가 만든 한국어 콘텐츠 중 번역 대상 키 (식별자·코드·수치는 제외)
QUESTION_TEXT_KEYS = {"title", "subtitle", "help", "label", "hint"}
ASSESSMENT_TEXT_KEYS = {
    "rationale_ko", "season_label_ko", "biology_ko", "exposure_environment_ko",
    "cross_reactivity_ko", "oral_allergy_syndrome_ko", "korean_name",
    # 아래는 값이 문자열 리스트다. 번역하지 않으면 도감 카드에 한국어가 그대로 남는다.
    "avoidance_control_ko", "oas_foods", "crossreact_confirmed", "crossreact_risk",
    "verdict_label_ko",
}

# 판정의 표시 이름(리포트 표와 같은 낱말). 약물의 이름은 안내 자료(sensitization_prevention.json)에서 읽는다.
_VERDICT_LABEL_KO = {"clinically_relevant": "실제 주의", "sensitized_only": "감작만",
                     "indeterminate": "관찰 필요", "not_assessed": "미평가"}


def _verdict_label_ko(verdict: str) -> str:
    if verdict == "clinician_review":
        from services.care_guidance_service import drug_review_label
        return drug_review_label()
    return _VERDICT_LABEL_KO.get(verdict, "")


def _localize(payload, lang: Optional[str], keys):
    """응답의 한국어 텍스트만 요청 언어로 번역한다(ko 면 그대로, 키 없으면 원문 유지)."""
    lang = normalize_lang(lang)
    if lang == "ko":
        return payload
    from services import translation_service
    try:
        return translation_service.get_translation_service().translate_obj(payload, lang, keys)
    except Exception as e:  # noqa: BLE001
        logger.warning("응답 번역 실패(%s): %s", lang, type(e).__name__)
        scope = translation_service.current_scope()
        if scope is not None:
            scope.errors += 1          # 이 언어의 산출물을 '완료'로 저장하지 않게 한다
        return payload


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _strings(v)


def _user_values(ocr: OCRResult, screening: Optional[ScreeningProfile], answers: Optional[Dict[str, Any]]):
    """요청에 실려 온 값 중 번역기가 가려야 할 것 — translation_scope 의 인자로 쓴다.

    identity : 환자 이름·검사기관·의뢰의·차트번호. 번역하지 않고, 캐시·일반 번역 요청에 넣지 않는다.
    free_text: 결과지 행의 항원명(레지스트리에 없는 것)·문진 주관식·답변의 문자열. 일반 문장과 분리해
               따로 번역하고 캐시하지 않는다. 레지스트리에 있는 항원명은 고를 수 있는 값이 정해져 있어 뺀다."""
    from services.allergen_search_service import get_allergen_search_service
    known = get_allergen_search_service().is_known_name
    patient = ocr.patient
    identity = [patient.name, patient.facility, patient.ordering_provider, patient.patient_id_external]
    raw: List[Any] = []
    for r in ocr.results or []:
        raw += [r.allergen_name, r.korean_name, r.raw_text, r.value_text]
    if screening is not None:
        raw += list(_strings(screening.model_dump(mode="json")))
    raw += list(_strings(answers or {}))
    free = set()
    for v in raw:
        v = v.strip() if isinstance(v, str) else ""
        # 짧은 ASCII 값(선택지 코드 yes/no, 단위 등)은 문장 속 일반 단어와 겹치고 주입에 쓸 수도 없다
        if len(v) < 2 or (v.isascii() and len(v) < 8) or known(v):
            continue
        free.add(v)
    return {"identity": [v for v in identity if v], "free_text": sorted(free), "age": patient.age}


def _translation_counts(scope) -> Dict[str, int]:
    """번역 대상 문장 수 / 번역하지 못한 문장 수 / 번역기 오류 수(ko 는 0/0/0).
    /api/classify·/api/questionnaire·/api/chat 이 같은 모양으로 돌려준다 — 화면이 '번역되지 않은 내용이
    섞여 있다'를 알 수 있게 한다."""
    return {"segments": scope.total, "untranslated": len(scope.untranslated), "errors": scope.errors}


def _api_key_ok() -> bool:
    """이번 요청이 고른 LLM 백엔드(없으면 서버 기본값)를 쓸 수 있는가."""
    from services import llm_backend
    return llm_backend.is_available(llm_backend.active())


def _controls_public(result, lang: Optional[str]) -> Dict[str, Any]:
    """검사 대조(히스타민·생리식염수) — 알러젠이 아니라 assessments·summary 에 들어가지 않는다.
    controls: 대조 값 목록, control_check: 그것으로 본 검사 해석 가능 여부(SPT) 또는 값 표시(MAST)."""
    check = result.control_check
    return {"controls": [{**c.model_dump(mode="json"), "category": "control"} for c in result.controls],
            "control_check": _localize(dict(check), lang, {"line_ko", "label_ko"}) if check else None}


def _assessment_public(a) -> Dict[str, Any]:
    """assessment 를 프론트로 보낼 안전한 dict 로 축약.

    relevance : 기존 소비자가 아는 세 값(+not_assessed)만 나간다. 약물 항원은 'indeterminate' 로 나간다 —
                'clinically_relevant'(진범 확정)도 'sensitized_only'(무혐의)도 아니다.
    verdict   : 판정 그대로. 약물 항원은 'clinician_review'(verdict_label_ko '진료 확인 필요'), 나머지는
                relevance 와 같다. 새 화면은 verdict 를 읽는다."""
    kb = a.kb or {}
    verdict = a.relevance.value if a.relevance else "not_assessed"
    return {
        "allergen_name": a.allergen_name,
        "korean_name": a.korean_name,
        "category": normalize_category(a.category),
        "test_value": a.test_value,
        "test_unit": a.test_unit,
        "class_value": a.class_value,
        "strength": a.strength,
        "relevance": public_relevance(verdict),
        "verdict": verdict,
        "verdict_label_ko": _verdict_label_ko(verdict),
        "rationale_ko": a.rationale_ko,
        "oas_foods": a.oas_foods or [],
        "crossreact_confirmed": getattr(a, "crossreact_confirmed", []) or [],
        "crossreact_risk": getattr(a, "crossreact_risk", []) or [],
        "season_label_ko": kb.get("season_label_ko", ""),
        "biology_ko": kb.get("biology_ko", ""),
        "exposure_environment_ko": kb.get("exposure_environment_ko", ""),
        "avoidance_control_ko": kb.get("avoidance_control_ko", []),
        "cross_reactivity_ko": kb.get("cross_reactivity_ko", ""),
        "oral_allergy_syndrome_ko": kb.get("oral_allergy_syndrome_ko", ""),
        "source": kb.get("source", "knowledge_base"),
        "sources": kb.get("sources", []),
    }


# ============================================================
# 엔드포인트
# ============================================================
def _build_info() -> Dict[str, Any]:
    """실행 중인 코드 버전 표식 — '업데이트했는데 반영이 안 된다' 를 즉시 판별하기 위한 진단용.
    git 커밋 + 문진 엔진 기능 플래그를 함께 노출한다."""
    commit = None
    try:
        import subprocess
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(BASE_DIR),
            stderr=subprocess.DEVNULL, timeout=3).decode().strip()
    except Exception:
        pass
    feats = {}
    try:
        import services.questionnaire_service as qs
        eng_cls = qs.QuestionnaireEngine
        feats = {
            # 2차 고도화 반영 여부(항원별 구체 OAS·교차반응 중증도·임상그룹 통합)
            "crossreact_severity_q": hasattr(qs, "QP_CROSSREACT_SEV"),
            "food_catchall_q": hasattr(qs, "Q_FOOD_GENERAL"),
            "per_antigen_oas_q": hasattr(eng_cls, "_append_crossreact_questions"),
            # 아래가 True 면 구버전(모호한 통합 OAS 문항이 남아 있음)
            "legacy_merged_oas_q": hasattr(qs, "Q_OAS_FOODS"),
        }
        from services.clinical_group_service import get_clinical_group_service
        feats["clinical_groups"] = len(get_clinical_group_service().groups)
        feats["ui_modes"] = ["quest", "classic"] if (BASE_DIR / "web" / "classic" / "index.html").exists() else ["quest"]
        feats["fhir_observation_v3"] = True   # 검증된 SCTID code/method + 0·<LoD·N/A 값 표현
    except Exception as e:
        logger.warning("기능 표식 수집 실패: %s", e)
        feats["error"] = type(e).__name__
    return {"commit": commit, "features": feats}


@app.get("/api/health")
def health(lang: str = "ko"):
    """상태 + 스크리닝 선택지. 선택지 라벨은 화면 언어로 낸다 —
    프론트가 언어를 바꾸면 다시 호출해야 칩이 같이 바뀐다."""
    ks = get_knowledge_service()
    sc = get_screening_service()
    return {
        "ok": True,
        "has_api_key": _api_key_ok(),
        "llm": __import__("services.llm_backend", fromlist=["x"]).describe(),   # 'AI 엔진' 선택지
        # 부가 기능 상태 — 화면이 메뉴를 숨기거나 보이는 데 쓴다(예: email 이 false 면 '메일로 받기' 숨김)
        "services": {
            "storage": get_store() is not None,
            "email": get_email_service().configured,
            "admin": admin_api.admin_enabled(),
            "pdf": __import__("services.report_pdf", fromlist=["x"]).pdf_available(),   # false 면 PDF 내려받기 숨김
            "i18n_background": bool(settings.i18n_background),
            "languages": list(LANGS),
        },
        "build": _build_info(),
        "lang": normalize_lang(lang),
        "kb": ks.stats(),
        "allergen_knowledge": ks.generated_stats(),   # 템플릿으로 덮은 항원 + 검토 대기 건수
        "screening_options": {
            "diseases": sc.disease_options(lang),
            "medications": sc.medication_options(lang),
            "organ_systems": sc.organ_system_options(lang),
        },
    }


@app.get("/api/allergen")
def allergen_backdata(name: str, category: Optional[str] = None, korean_name: Optional[str] = None):
    """알러젠 이름으로 backdata 조회 (수동 추가 시 자동완성/정보카드용)."""
    ks = get_knowledge_service()
    kb = ks.get_backdata(name=name, category=category, korean_name=korean_name)
    return {
        "korean_name": kb.get("korean_name"),
        "canonical_name": kb.get("canonical_name"),
        "category": normalize_category(kb.get("category")),
        "season_label_ko": kb.get("season_label_ko", ""),
        "seasonality_pattern": kb.get("seasonality_pattern"),
        "indoor_outdoor": kb.get("indoor_outdoor"),
        "biology_ko": kb.get("biology_ko", ""),
        "source": kb.get("source"),
        # 레지스트리·지식베이스에서 찾은 이름인가. false 면 korean_name 은 비어 있다(입력을 되돌려주지 않는다).
        "known": kb.get("source") not in ("category_default", "wikipedia"),
    }


# 업로드로 받는 이미지 형식(파일 앞머리로 판별한다 — 확장자·Content-Type 은 믿지 않는다)
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"), (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"),
)


def _sniff_image(content: bytes) -> Optional[str]:
    for magic, mime in _IMAGE_MAGIC:
        if content.startswith(magic):
            return mime
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


@app.post("/api/ocr", dependencies=[Depends(rate_limit("ocr"))])
async def ocr(file: UploadFile = File(...)):
    """업로드 이미지에서 검사결과 추출 (OpenAI Vision 필요).

    오류: 413 file_too_large(MAX_FILE_SIZE_MB 초과) · 415 unsupported_file(이미지가 아님) · 429 rate_limited"""
    if not _api_key_ok():
        raise HTTPException(
            status_code=400,
            detail="OpenAI API 키가 설정되지 않았습니다. .env에 OPENAI_API_KEY를 넣거나, "
                   "‘데모 데이터’ 또는 ‘직접 입력’으로 진행하세요.",
        )
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise api_error(413, "file_too_large", f"파일이 너무 큽니다(최대 {settings.max_file_size_mb}MB).")
    if _sniff_image(content) is None:
        raise api_error(415, "unsupported_file", "이미지 파일(JPEG·PNG·WEBP·GIF·BMP·TIFF)만 올릴 수 있습니다.")
    try:
        from services.ocr_service import get_ocr_service
        # 두 번 읽기로 한 장에 1분 가까이 걸린다 — 이벤트 루프를 막지 않도록 스레드에서 돌린다
        from starlette.concurrency import run_in_threadpool
        from services import llm_backend
        backend = llm_backend.active()      # 스레드로 넘어가기 전에 이번 요청의 백엔드를 확정한다
        result = await run_in_threadpool(get_ocr_service().extract_from_image, content, backend=backend)
        return JSONResponse(result.model_dump(by_alias=False))
    except HTTPException:
        raise
    except Exception:
        logger.exception("OCR 실패")       # 자세한 내용은 서버 로그에만 남긴다
        raise HTTPException(status_code=422, detail="검사결과지를 읽지 못했습니다. 사진을 다시 찍어 올리거나 "
                                                    "‘직접 입력’으로 진행하세요.")


def _demo_ocr() -> OCRResult:
    return OCRResult(
        test_type=TestType.MAST,
        patient=PatientInfo(name="홍길동", age=32, gender="M", test_date="2026-06-15",
                            report_date="2026-06-17", facility="OO대학교병원 진단검사의학과",
                            ordering_provider="알레르기내과 김OO", patient_id_external="C1234567"),
        results=[
            AllergenResult(index=1, raw_text="D. farinae 4", allergen_name="Dermatophagoides farinae",
                           korean_name="집먼지진드기(D.farinae)", value=17.6, unit="kU/L",
                           class_value=4, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=2, raw_text="D. pteronyssinus 3", allergen_name="Dermatophagoides pteronyssinus",
                           korean_name="집먼지진드기(D.pteronyssinus)", value=8.2, unit="kU/L",
                           class_value=3, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=3, raw_text="Birch 3", allergen_name="Birch pollen",
                           korean_name="자작나무 꽃가루", value=6.1, unit="kU/L",
                           class_value=3, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=4, raw_text="Cat 2", allergen_name="Cat dander",
                           korean_name="고양이 비듬", value=1.4, unit="kU/L",
                           class_value=2, interpretation=InterpretationType.POSITIVE),
            AllergenResult(index=5, raw_text="Dog 0", allergen_name="Dog dander",
                           korean_name="개 비듬", value=0.1, unit="kU/L",
                           class_value=0, interpretation=InterpretationType.NEGATIVE),
            AllergenResult(index=6, raw_text="Ragweed 2", allergen_name="Ragweed pollen",
                           korean_name="돼지풀 꽃가루", value=1.1, unit="kU/L",
                           class_value=2, interpretation=InterpretationType.POSITIVE),
        ],
    )


@app.get("/api/ocr/demo")
def ocr_demo():
    """데모용 검사결과 (API 키 없이 전체 흐름 체험)."""
    return JSONResponse(_demo_ocr().model_dump(by_alias=False))


@app.post("/api/screening/summary")
def screening_summary(screening: ScreeningProfile = Body(...)):
    return get_screening_service().summarize(screening)


@app.post("/api/questionnaire", dependencies=[Depends(rate_limit("api"))])
def questionnaire(req: AssessRequest):
    """양성 알러젠에 대한 적응형 문진 생성."""
    from services.translation_service import translation_scope
    rs = get_relevance_service()
    result = rs.build_assessments(req.ocr, req.screening)
    engine = get_questionnaire_engine()
    q = engine.build(result, req.screening)
    out = {
        "assessments": [_assessment_public(a) for a in result.assessments],
        "questionnaire": q,
        "positive_count": len(result.assessments),
    }
    with translation_scope(**_user_values(req.ocr, req.screening, None)) as scope:
        out = _localize(out, req.lang, ASSESSMENT_TEXT_KEYS | QUESTION_TEXT_KEYS)
        out.update(_controls_public(result, req.lang))
    out["translation"] = _translation_counts(scope)
    return out


def _generate_outputs(req: ClassifyRequest, lang: str) -> Dict[str, Any]:
    """판정 + 리포트/카드뉴스를 한 언어로 만든다. /api/classify 와 나머지 언어 백그라운드 생성이 함께 쓴다.

    out["translation"] 에 번역 대상 문장 수와 번역하지 못한 문장 수를 담는다(ko 는 0/0).
    번역기는 이 요청의 환자 값(이름·기관·자유 기재)을 가린 채로 돈다."""
    from services.translation_service import translation_scope
    with translation_scope(**_user_values(req.ocr, req.screening, req.answers)) as scope:
        out = _generate_outputs_in_scope(req, lang)
    out["translation"] = _translation_counts(scope)
    return out


def _classified(req: ClassifyRequest):
    """요청의 검사결과·문진으로 판정한다. (판정 결과, 정리된 답, 버린 답 목록)을 돌려준다.

    `answers` 는 임의의 JSON 이다. 이 환자의 문진에 맞춰 한 번 정리한 뒤(QuestionnaireEngine.sanitize_answers)
    판정·상담·FHIR 가 모두 그 정리된 답만 읽는다 — 선택지에 없는 값 하나로 500 이 나지 않게."""
    rs = get_relevance_service()
    result = rs.build_assessments(req.ocr, req.screening)
    engine = get_questionnaire_engine()
    answers, ignored = engine.sanitize_answers(result, req.answers, req.screening)
    engine.classify(result, answers, req.screening)
    return result, answers, ignored


def _generate_outputs_in_scope(req: ClassifyRequest, lang: str) -> Dict[str, Any]:
    result, answers, ignored = _classified(req)

    patient_info = {
        "name": req.ocr.patient.name,
        "age": req.ocr.patient.age,
        "gender": req.ocr.patient.gender,
        "test_date": req.ocr.patient.test_date,
        "facility": getattr(req.ocr.patient, "facility", None),
        "report_date": getattr(req.ocr.patient, "report_date", None),
    }

    rsvc = get_report_service()
    report_md = rsvc.build_patient_report_markdown(result, patient_info, req.screening, lang)
    try:
        import markdown as md_lib
        report_html = md_lib.markdown(report_md, extensions=["extra", "sane_lists"])
    except Exception:
        report_html = "<pre>" + report_md + "</pre>"
    # PDF/HTML 겸용 인쇄형 문서(단일 디자인 소스) — 화면 표시 + 다운로드 + 인쇄(PDF)
    report_document_html = rsvc.build_patient_report_html_document(
        result, patient_info, req.screening, lang)

    if (req.ui or "quest").lower() == "classic":
        from services.cardnews_classic import get_classic_cardnews_service
        cardnews_html = get_classic_cardnews_service().generate_html(result, patient_info, req.screening)
    else:
        cardnews_html = get_cardnews_service().generate_html(result, patient_info, req.screening)
    if lang != "ko":
        from services.translation_service import get_translation_service
        cardnews_html = get_translation_service().translate_html(cardnews_html, lang)

    out = {
        "assessments": [_assessment_public(a) for a in result.assessments],
        "summary": RelevanceService.summarize(result),
        "report_markdown": report_md,
        "report_html": report_html,
        "report_document_html": report_document_html,
        "cardnews_html": cardnews_html,
        "ui": (req.ui or "quest").lower(),
        "lang": lang,
        # 판정에 쓰지 않고 버린 답(문진에 없는 문항·선택지에 없는 값). 비어 있으면 보낸 답을 모두 읽은 것이다.
        "ignored_answers": ignored,
    }
    # 리포트·카드뉴스는 이미 번역됐고, 도감 카드가 쓰는 assessment 텍스트만 남는다
    out["assessments"] = _localize(out["assessments"], lang, ASSESSMENT_TEXT_KEYS)
    out.update(_controls_public(result, lang))
    return out


# ---- 저장 + 3개 언어 산출물 ----
# 화면·저장·메일이 같은 언어 코드를 쓰도록 한 곳에서 정규화한다('en-US' → 'en', 모르는 값 → 'ko').
_req_lang = normalize_lang


def _input_data(req: ClassifyRequest) -> Dict[str, Any]:
    """세션에 보관할 입력(검사결과 행 + 문진 프로필 + 문진 답변)."""
    return {
        "ocr": req.ocr.model_dump(mode="json", by_alias=False),
        "screening": req.screening.model_dump(mode="json") if req.screening else None,
        "answers": req.answers or {},
        "ui": (req.ui or "quest").lower(),
    }


def _localized_state(out: Dict[str, Any], lang: str, backend: str) -> tuple:
    """(status, error). 번역 대상 문장이 하나라도 번역되지 못했으면 그 언어는 '완료'가 아니다.

    번역기가 직접 센 값(out["translation"])으로 판단한다. 예전에는 한글 비율이 30% 를 넘을 때만 걸러서,
    제목·본문 수십 줄이 한국어로 남은 영어 리포트가 ready 로 저장되고 메일로 나갔다. 환자 이름이나
    환자가 직접 쓴 글은 번역 대상이 아니므로(가린 채 번역한다) 한글로 남아 있어도 여기에 잡히지 않는다."""
    if lang == "ko":
        return "ready", None
    tr = out.get("translation") or {}
    missing = int(tr.get("untranslated") or 0)
    if not missing and not tr.get("errors"):
        return "ready", None
    from services import llm_backend
    if not llm_backend.is_available(backend):
        return "skipped", "translation_unavailable"
    return "failed", f"untranslated:{missing}" if missing else "translation_error"


def _store_localized(store, session_id: str, lang: str, out: Dict[str, Any], backend: str) -> str:
    """내용은 ready 일 때만 저장한다. 상태를 돌려준다."""
    status, error = _localized_state(out, lang, backend)
    store.save_localized(session_id, lang, status, out if status == "ready" else None, error)
    return status


def _run_i18n_job(session_id: str, req: ClassifyRequest, langs: List[str], backend: str) -> None:
    """요청 언어 외 나머지 언어 산출물을 만든다. 응답을 보낸 뒤 돌며, 실패해도 상태만 남긴다."""
    from services import llm_backend
    store = get_store()
    if store is None:
        return
    for lang in langs:
        try:
            store.save_localized(session_id, lang, "running")
            with llm_backend.use(backend):
                out = _generate_outputs(req, lang)
            status = _store_localized(store, session_id, lang, out, backend)
            logger.info("언어별 산출물 %s: session#%s lang=%s", status, session_tag(session_id), lang)
        except Exception as e:  # noqa: BLE001
            logger.warning("언어별 산출물 실패: session#%s lang=%s (%s)", session_tag(session_id), lang,
                           type(e).__name__)
            try:
                store.save_localized(session_id, lang, "failed", None, type(e).__name__)
            except Exception:  # noqa: BLE001
                pass


def _i18n_status(store, session_id: str) -> Dict[str, Dict[str, Any]]:
    st = store.localized_status(session_id)
    return {lang: st.get(lang, {"status": "missing", "error": None, "updated_at": None}) for lang in LANGS}


def _retry_i18n(session_id: str) -> Dict[str, Any]:
    """저장된 입력으로 ready 가 아닌 언어를 다시 만든다(관리자 재시도)."""
    from services import llm_backend
    store = get_store()
    session = store.get_session(session_id) if store else None
    if not session:
        raise api_error(404, "not_found", "세션을 찾을 수 없습니다.")
    data = session["input"]
    req = ClassifyRequest(ocr=data["ocr"], screening=data.get("screening"),
                          answers=data.get("answers") or {}, ui=data.get("ui") or "quest")
    todo = [lang for lang, v in _i18n_status(store, session_id).items() if v["status"] != "ready"]
    _run_i18n_job(session_id, req, todo, llm_backend.active())
    return {"session_id": session_id, "retried": todo, "langs": _i18n_status(store, session_id)}


admin_api.i18n_retry_hook = _retry_i18n


@app.post("/api/classify", dependencies=[Depends(rate_limit("classify"))])
def classify(req: ClassifyRequest, background_tasks: BackgroundTasks):
    """문진 답변으로 감별 판정 + 리포트/카드뉴스 생성.

    요청 언어의 결과를 바로 돌려주고, 세션(입력·산출물)을 저장한다. 나머지 두 언어는 응답 뒤에
    백그라운드로 만들어 별도 DB 에 넣는다(진행 상태: GET /api/sessions/{id}/status).
    저장이나 번역이 실패해도 이 응답은 실패하지 않는다.

    lang 은 /api/health 와 같은 규칙으로 정규화한다('en-US' → 'en'). 응답의 lang 이 실제로 쓴 언어다.
    요청 언어의 번역이 다 되지 않았으면 응답의 i18n[lang] 이 ready 가 아니다(skipped/failed)."""
    lang = stored_lang = normalize_lang(req.lang)
    out = _generate_outputs(req, lang)
    out["session_id"] = None
    try:
        store = get_store()
        if store is not None:
            from services import llm_backend
            backend = llm_backend.active()
            email = normalize_email(req.email)
            sid = store.save_session(session_id=req.session_id, email=email, lang=stored_lang, ui=out["ui"],
                                     input_data=_input_data(req), summary=out["summary"],
                                     assessments=out["assessments"])
            _store_localized(store, sid, stored_lang, out, backend)
            others = [code for code in LANGS if code != stored_lang]
            if settings.i18n_background:
                for code in others:
                    store.save_localized(sid, code, "pending")
                background_tasks.add_task(_run_i18n_job, sid, req, others, backend)
            out["session_id"] = sid
            # 이미 다른 주소에 묶인 세션이면 연결은 바뀌지 않는다 — 그때는 주소를 돌려주지 않는다
            out["user_email"] = email if email and store.session_email(sid) == email else None
            out["i18n"] = {code: v["status"] for code, v in _i18n_status(store, sid).items()}
            logger.info("세션 저장: session#%s lang=%s 양성 %d건", session_tag(sid), stored_lang,
                        len(out["assessments"]))
    except Exception as e:  # noqa: BLE001
        logger.error("세션 저장 실패(응답은 정상 반환): %s", type(e).__name__)
    return out


class ChatRequest(ClassifyRequest):
    """결과 상담 챗봇 — 판정에 필요한 입력(ocr/screening/answers)에 대화 이력을 더한다.
    서버는 상태를 두지 않고 매 요청마다 판정을 다시 계산해 답변 근거로 삼는다."""
    messages: List[Dict[str, Any]] = []

    @field_validator("messages")
    @classmethod
    def _bounded_messages(cls, messages):
        if len(messages) > settings.chat_max_messages:
            raise ValueError(f"messages 는 최대 {settings.chat_max_messages}개입니다.")
        for m in messages:
            content = m.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("messages[].content 는 문자열이어야 합니다.")
            if content and len(content) > settings.chat_max_message_chars:
                raise ValueError(f"messages[].content 는 최대 {settings.chat_max_message_chars}자입니다.")
        return messages


@app.post("/api/chat", dependencies=[Depends(rate_limit("chat"))])
def chat(req: ChatRequest):
    """검사 결과에 대한 환자 질문 답변.
    - 답변 근거는 이 환자의 판정 결과·지식베이스로 고정한다(컨텍스트 밖 내용은 답하지 않음).
    - API 키가 없으면 추천 질문의 결정론적 답변만 제공한다.
    - session_id 가 저장된 세션이면 이번 차례(질문·답변)를 그 세션에 남긴다. session_id 가 없거나
      모르는 값이면 답만 하고 저장하지 않는다(세션은 /api/classify 만 만든다). 응답의 session_id 는
      저장한 세션의 id, 저장하지 않았으면 null 이다.
    """
    from services.result_chat_service import get_result_chat_service
    from services.translation_service import translation_scope
    result, answers, _ignored = _classified(req)

    patient_info = {
        "name": req.ocr.patient.name,
        "age": req.ocr.patient.age,
        "test_date": req.ocr.patient.test_date,
        "test_type": req.ocr.test_type,
    }
    svc = get_result_chat_service()
    lang = normalize_lang(req.lang)
    with translation_scope(**_user_values(req.ocr, req.screening, req.answers)) as scope:
        out = svc.answer(result, patient_info, req.messages, req.screening, answers, lang)
        out["suggestions"] = svc.suggestions(result, lang, screening=req.screening)
    # 추천 질문·준비된 답변(+ 결정론 답변) 전체의 번역 상태. 이 필드의 모양(segments/untranslated/errors)은
    # 바꾸지 않는다 — 어느 답변이 번역되지 않았는지는 suggestions[].translation 에 항목별로 있다.
    out["translation"] = _translation_counts(scope)
    out["has_api_key"] = _api_key_ok()
    out["session_id"] = _store_chat_turn(req, lang, out)
    return out


def _store_chat_turn(req: "ChatRequest", lang: str, out: Dict[str, Any]) -> Optional[str]:
    """이번 차례(마지막 사용자 질문 + 답변)를 세션에 남긴다. 저장한 세션 id 를 돌려준다.

    여기서 세션을 만들지 않는다. 예전에는 session_id 없는 호출마다(추천 질문만 받아 가는 호출 포함)
    새 세션이 생겨, 한 사람의 상담이 여러 세션으로 흩어지고 빈 세션이 쌓였다."""
    try:
        store = get_store()
        if store is None or not store.session_exists(req.session_id):
            return None
        user_msgs = [m for m in (req.messages or []) if m.get("role") == "user"]
        question = str((user_msgs[-1].get("content") if user_msgs else "") or "").strip()
        reply = str(out.get("reply") or "")
        if question and reply:
            store.add_chat_turn(req.session_id, question[:CHAT_QUESTION_MAX], reply[:CHAT_ANSWER_MAX], lang,
                                out.get("source"))
        return req.session_id
    except Exception as e:  # noqa: BLE001
        logger.error("상담 대화 저장 실패(응답은 정상 반환): %s", type(e).__name__)
        return None


class SparqlRequest(BaseModel):
    """온톨로지 SPARQL 질의. 읽기 전용(SELECT/ASK)만 허용한다."""
    query: str
    limit: int = 50


@app.get("/api/ontology/topics")
def ontology_topics():
    """온톨로지에 어떤 질환 주제·근거가 들어 있는지. 연결 상태 확인용."""
    from services.ontology_service import get_ontology_service
    svc = get_ontology_service()
    if not svc.available:
        raise HTTPException(status_code=503, detail="온톨로지 스냅샷을 불러오지 못했습니다.")
    return {"topics": svc.topics(),
            "usage_rules": (svc._raw or {}).get("usage_rules", []),
            "limitations": (svc._raw or {}).get("limitations", []),
            "schema_version": (svc._raw or {}).get("schema_version"),
            "exported_at": (svc._raw or {}).get("export_finished_at")}


KNOWLEDGE_TEXT_MAX = 2000
CHAT_QUESTION_MAX = 4000
CHAT_ANSWER_MAX = 20000


class KnowledgeReviewRequest(BaseModel):
    """항원 소개문 검토. LLM 이 만든 문장은 승인해야 환자에게 나간다."""
    name: str                      # canonical_name 또는 korean_name
    action: str                    # approve | reject | edit
    text: Optional[str] = None     # edit 일 때 새 문장


@app.get("/api/pollen/regions")
def pollen_regions():
    """거주 지역 선택지. 꽃가루 시기가 지역마다 달라 화면에서 고르게 한다."""
    from services.pollen_forecast_service import get_pollen_forecast_service
    svc = get_pollen_forecast_service()
    return {"countries": svc.countries(), "live_forecast": svc.live_available}


@app.get("/api/pollen/zip")
def pollen_zip(postal_code: str, country: str = "US"):
    """우편번호 → 주·권역·대표좌표. 미국만 지원한다(한국은 단일 권역이라 불필요)."""
    from services.pollen_forecast_service import get_pollen_forecast_service
    return get_pollen_forecast_service().lookup_zip(country, postal_code)


@app.get("/api/knowledge/candidates", dependencies=[Depends(admin_api.require_admin)])
def knowledge_candidates(status: str = "candidate"):
    """검토 대기(또는 승인된) 항원 소개문 목록. /review 화면이 쓴다.

    관리자 로그인이 필요하다: 503 admin_disabled(ADMIN_PASSWORD 없음 — 닫혀 있다) · 401 unauthorized."""
    from services.knowledge_service import get_knowledge_service
    ks = get_knowledge_service()
    out = []
    for e in ks.generated:
        st = e.get("biology_ko_status")
        if not e.get("biology_ko"):
            continue
        if status != "all" and st != status:
            continue
        out.append({"canonical_name": e.get("canonical_name"), "korean_name": e.get("korean_name"),
                    "category": e.get("category"), "profile_key": e.get("profile_key"),
                    "biology_ko": e.get("biology_ko"), "status": st,
                    "model": e.get("biology_ko_model"),
                    "generated_at": e.get("biology_ko_generated_at"),
                    "exposure_environment_ko": e.get("exposure_environment_ko"),
                    "season_label_ko": e.get("season_label_ko"),
                    "cross_reactivity_ko": e.get("cross_reactivity_ko")})
    counts = {"candidate": 0, "approved": 0}
    for e in ks.generated:
        st = e.get("biology_ko_status")
        if st in counts:
            counts[st] += 1
    return {"items": out, "counts": counts, "total_generated": len(ks.generated)}


@app.post("/api/knowledge/review", dependencies=[Depends(admin_api.require_admin)])
def knowledge_review(req: KnowledgeReviewRequest):
    """소개문 승인·수정·삭제. 템플릿 필드(회피 수칙 등)는 건드리지 않는다.

    환자에게 나가는 임상 문구를 고치므로 관리자 로그인이 필요하다:
    503 admin_disabled · 401 unauthorized · 403 bad_origin(다른 출처에서 온 요청)."""
    import json as _json
    from services.knowledge_service import get_knowledge_service, _norm

    # 읽고 쓰는 파일은 지식 서비스가 실제로 읽어 들인 그 파일이다(KNOWLEDGE_GENERATED_PATH 로 바꿀 수 있다).
    # 쓰는 곳과 다시 읽는 곳이 다르면 승인한 문장이 화면에 반영되지 않는다.
    kb_path = get_knowledge_service().generated_path
    if req.action not in ("approve", "reject", "edit"):
        raise HTTPException(status_code=400, detail="action 은 approve/reject/edit 중 하나입니다.")
    try:
        data = _json.loads(kb_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("지식 파일을 읽지 못했습니다")
        raise HTTPException(status_code=500, detail="지식 파일을 읽지 못했습니다.")

    key = _norm(req.name)
    target = next((e for e in data.get("entries", [])
                   if _norm(e.get("canonical_name") or "") == key
                   or _norm(e.get("korean_name") or "") == key), None)
    if not target:
        raise HTTPException(status_code=404, detail="항원을 찾을 수 없습니다.")

    if req.action == "approve":
        if not target.get("biology_ko"):
            raise HTTPException(status_code=400, detail="승인할 문장이 없습니다.")
        target["biology_ko_status"] = "approved"
        target["biology_ko_reviewed_by"] = "web"
    elif req.action == "edit":
        if not (req.text or "").strip():
            raise HTTPException(status_code=400, detail="edit 에는 text 가 필요합니다.")
        if len(req.text) > KNOWLEDGE_TEXT_MAX:
            raise HTTPException(status_code=400, detail=f"문장은 최대 {KNOWLEDGE_TEXT_MAX}자입니다.")
        target["biology_ko"] = req.text.strip()
        target["biology_ko_status"] = "approved"
        target["biology_ko_reviewed_by"] = "web_edit"
    else:
        for k in ("biology_ko", "biology_ko_status", "biology_ko_model",
                  "biology_ko_generated_at", "biology_ko_reviewed_by"):
            target.pop(k, None)

    data["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    kb_path.write_text(_json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    get_knowledge_service()._load_generated()      # 즉시 반영
    return {"ok": True, "name": req.name, "action": req.action,
            "status": target.get("biology_ko_status")}


@app.get("/api/ontology/search")
def ontology_search(q: str):
    """주제 검색(별칭 포함). 연동 가이드의 `search_topics`."""
    from services.ontology_service import get_ontology_service
    return {"query": q, "results": get_ontology_service().search_topics(q)}


@app.get("/api/ontology/topic/{topic:path}")
def ontology_topic(topic: str, predicate: Optional[str] = None, limit: int = 20):
    """주제의 임상 관계 + 검토 상태 + 수집 범위. 가이드의 `get_topic_context`."""
    from services.ontology_service import get_ontology_service
    out = get_ontology_service().get_topic_context(topic, predicate, limit)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.get("/api/ontology/evidence/{evidence_id:path}")
def ontology_evidence(evidence_id: str):
    """근거 셀 원문·출처·판본. 가이드의 `get_evidence`."""
    from services.ontology_service import get_ontology_service
    out = get_ontology_service().get_evidence(evidence_id)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.get("/api/ontology/terminology/{topic:path}")
def ontology_terminology(topic: str):
    """표준 용어 매핑(체계·코드·판본·검토 상태). 가이드의 `get_terminology`."""
    from services.ontology_service import get_ontology_service
    out = get_ontology_service().get_terminology(topic)
    if out.get("error"):
        raise HTTPException(status_code=404, detail=out["error"])
    return out


@app.post("/api/ontology/sparql", dependencies=[Depends(rate_limit("api"))])
def ontology_sparql(req: SparqlRequest):
    """온톨로지에 직접 SPARQL 질의.

    그래프는 스냅샷에서 만든 메모리 그래프다. 수정 질의(INSERT/DELETE/DROP…)와 SERVICE(외부 호출)는
    거부한다. 어휘는 services/ontology_service.py 상단 주석과 /api/ontology/topics 참고.
    """
    from services.ontology_service import get_ontology_service
    out = get_ontology_service().sparql(req.query, req.limit)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out.get("error", "질의 실패"))
    return out


@app.post("/api/fhir", dependencies=[Depends(rate_limit("api"))])
def fhir(req: ClassifyRequest):
    """FHIR 매핑.
    - Observation: 전체 검사결과(양성+음성) + 검사기관 performer
    - Observation 은 음성도 포함(interpretation NEG), 경계는 IND
    - Condition/Observation(survey)/QuestionnaireResponse: 환자 문진의 기저 질환·증상(screening_bundle)
    - AllergyIntolerance: 양성/의심 알러젠 전부 (임상적 유의=confirmed, 감작·미확정=unconfirmed),
      criticality/clinicalStatus 코딩, 꽃가루-음식 교차반응(OAS) 음식도 포함
    """
    return _fhir_bundles(req)


def _fhir_bundles(req: ClassifyRequest) -> Dict[str, Any]:
    result, answers, _ignored = _classified(req)
    cross_foods = get_questionnaire_engine().crossreactive_food_items(result.assessments, answers)

    fs = FHIRService()
    return fs.build_bundles_from_relevance(req.ocr, result, req.screening, cross_foods, answers)


# ============================================================
# 세션(저장된 결과) — 세션 id 를 아는 쪽만 접근한다(추측할 수 없는 난수 id)
# ============================================================
class IdentifyRequest(BaseModel):
    email: str = Field(max_length=320)


class EmailResultsRequest(BaseModel):
    email: str = Field(max_length=320)
    # 보낼 언어. 없으면 세션을 만든 언어. 그 언어가 아직 준비되지 않았으면 준비된 언어로 대신 보낸다.
    lang: Optional[str] = Field(default=None, max_length=16)
    # 첨부할 자료: report(리포트 HTML) / cardnews(카드뉴스 HTML) / fhir(FHIR 번들 JSON)
    include: List[str] = Field(default=["report", "cardnews", "fhir"], max_length=8)


class ChatTurnRequest(BaseModel):
    """화면에서 이미 답이 정해진 상담 한 차례(준비된 답변이 있는 추천 질문 등)를 기록으로 남긴다."""
    question: str = Field(min_length=1, max_length=CHAT_QUESTION_MAX)
    answer: str = Field(min_length=1, max_length=CHAT_ANSWER_MAX)
    lang: Optional[str] = Field(default=None, max_length=16)
    source: Optional[str] = Field(default=None, max_length=40, pattern=r"^[A-Za-z0-9_.:-]*$")


def _require_store():
    store = get_store()
    if store is None:
        raise api_error(503, "storage_disabled", "저장 기능이 꺼져 있습니다.")
    return store


def _require_session(store, session_id: str) -> Dict[str, Any]:
    session = store.get_session(session_id)
    if not session:
        raise api_error(404, "session_not_found", "세션을 찾을 수 없습니다.")
    return session


@app.get("/api/sessions/{session_id}/status")
def session_status(session_id: str):
    """세션 요약 + 언어별 산출물 상태(pending/running/ready/failed/skipped/missing).
    done 은 더 기다릴 언어가 없다는 뜻이다(전부 성공했다는 뜻이 아니다)."""
    store = _require_store()
    session = _require_session(store, session_id)
    langs = _i18n_status(store, session_id)
    return {
        "session_id": session_id,
        "lang": session["lang"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "has_email": bool(session.get("email")),
        "langs": langs,
        "ready": [code for code, v in langs.items() if v["status"] == "ready"],
        "done": all(v["status"] not in ("pending", "running") for v in langs.values()),
    }


@app.get("/api/sessions/{session_id}/outputs/{lang}")
def session_outputs(session_id: str, lang: str):
    """저장된 한 언어의 산출물. /api/classify 응답과 같은 키를 쓴다(언어 전환 시 재계산 없이 쓰기 위함)."""
    store = _require_store()
    session = _require_session(store, session_id)
    code = lang.lower().replace("_", "-").split("-")[0]
    if code not in LANGS:
        raise api_error(400, "unsupported_lang", f"지원 언어는 {', '.join(LANGS)} 입니다.")
    lang = code
    row = store.get_localized(session_id, lang)
    status = row["status"] if row else "missing"
    if status != "ready":
        raise HTTPException(status_code=409, detail={
            "code": "not_ready", "status": status,
            "message": "이 언어의 결과가 아직 준비되지 않았습니다."})
    return {
        "session_id": session_id, "lang": lang, "status": status, "updated_at": row["updated_at"],
        "assessments": row["assessments"], "summary": session.get("summary"),
        "report_markdown": row["report_markdown"], "report_html": row["report_html"],
        "report_document_html": row["report_document_html"], "cardnews_html": row["cardnews_html"],
        "ui": session["ui"],
    }


def _report_pdf(document_html: str, lang: str, session_id: str) -> Optional[bytes]:
    """리포트 문서를 PDF 로. 이 서버에서 만들 수 없으면 None(이유는 로그에만 남긴다)."""
    from services.report_pdf import PdfUnavailable, render_report_pdf
    try:
        return render_report_pdf(document_html, lang)
    except PdfUnavailable as e:
        logger.warning("리포트 PDF 를 만들지 못했습니다: session#%s lang=%s (%s)", session_tag(session_id), lang, e)
        return None


@app.get("/api/sessions/{session_id}/report.pdf", dependencies=[Depends(rate_limit("api"))])
def session_report_pdf(session_id: str, lang: Optional[str] = None):
    """저장된 리포트를 PDF 로 내려받는다(한글·중국어 글꼴 포함, A4).

    접근 규칙은 다른 세션 엔드포인트와 같다(세션 id 를 아는 쪽만). lang 을 주지 않으면 세션을 만든 언어다.
    번역이 끝난(ready) 언어만 내준다 — 번역이 덜 된 리포트를 완성본처럼 PDF 로 만들지 않는다.
    응답은 저장하지 않는다(Cache-Control: no-store).

    오류: 404 session_not_found · 400 unsupported_lang · 409 not_ready(그 언어의 리포트가 아직 없음) ·
    503 pdf_unavailable(이 서버에 PDF 엔진 또는 한글 글꼴이 없음) · 429 rate_limited"""
    store = _require_store()
    session = _require_session(store, session_id)
    code = (lang or session["lang"] or "ko").lower().replace("_", "-").split("-")[0]
    if code not in LANGS:
        raise api_error(400, "unsupported_lang", f"지원 언어는 {', '.join(LANGS)} 입니다.")
    row = store.get_localized(session_id, code)
    status = row["status"] if row else "missing"
    if status != "ready" or not row.get("report_document_html"):
        raise HTTPException(status_code=409, detail={
            "code": "not_ready", "status": status,
            "message": "이 언어의 결과가 아직 준비되지 않았습니다."})
    pdf = _report_pdf(row["report_document_html"], code, session_id)
    if pdf is None:
        raise api_error(503, "pdf_unavailable", "이 서버에서는 PDF 를 만들 수 없습니다. HTML 리포트를 인쇄해 주세요.")
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="allergy-report-{code}.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/sessions/{session_id}/chat")
def session_chat(session_id: str):
    """이 세션의 상담 대화 이력(오래된 것부터)."""
    store = _require_store()
    _require_session(store, session_id)
    return {"session_id": session_id, "messages": store.list_chat(session_id)}


@app.post("/api/sessions/{session_id}/chat/turn", dependencies=[Depends(rate_limit("api"))])
def session_chat_turn(session_id: str, req: ChatTurnRequest):
    """상담 한 차례(질문 + 답변)를 세션에 남긴다. /api/chat 을 거치지 않고 화면에서 바로 보여 준
    답변(준비된 답이 있는 추천 질문)이 대화 기록에서 빠지지 않게 한다.

    본문: {question, answer, lang, source} — source 는 답의 출처 표식(예: "suggestion").
    응답: {"ok": true}. 오류: 404 session_not_found · 422(길이·형식) · 429 rate_limited/chat_full"""
    store = _require_store()
    question, answer = req.question.strip(), req.answer.strip()
    if not question or not answer:
        raise api_error(422, "empty_turn", "질문과 답변이 비어 있습니다.")
    saved = store.add_chat_turn(session_id, question, answer, normalize_lang(req.lang), req.source or None)
    if saved == "not_found":
        raise api_error(404, "session_not_found", "세션을 찾을 수 없습니다.")
    if saved == "full":
        raise api_error(429, "chat_full", "이 세션에 남길 수 있는 대화 수를 넘었습니다.")
    return {"ok": True}


@app.post("/api/sessions/{session_id}/identify", dependencies=[Depends(rate_limit("api"))])
def session_identify(session_id: str, req: IdentifyRequest):
    """익명 세션을 이메일 사용자에 연결한다. 이미 연결된 세션의 주소는 바꾸지 않는다.

    오류: 422 invalid_email · 404 session_not_found · 409 email_already_bound(다른 주소에 연결돼 있음)"""
    store = _require_store()
    email = normalize_email(req.email)
    if not email:
        raise api_error(422, "invalid_email", "이메일 주소 형식이 올바르지 않습니다.")
    bound = store.attach_user(session_id, email)
    if bound == "not_found":
        raise api_error(404, "session_not_found", "세션을 찾을 수 없습니다.")
    if bound == "conflict":
        raise api_error(409, "email_already_bound", "이 결과는 이미 다른 이메일 주소에 연결되어 있습니다.")
    return {"ok": True, "session_id": session_id, "email": email}


_EMAIL_TEXT = {
    "ko": {"subject": "알레르기 검사 결과 자료를 보내드립니다",
           "hello": "요청하신 알레르기 검사 결과 자료를 첨부해 보내드립니다.",
           "files": "첨부 파일", "report": "환자용 리포트 (HTML — 브라우저로 여는 화면용)",
           "report_pdf": "환자용 리포트 (PDF — 인쇄·보관용)",
           "cardnews": "카드뉴스 (HTML)", "fhir": "HL7 FHIR 번들 (JSON — 의료기관 전달용)",
           "note": "이 자료는 검사 결과의 이해를 돕기 위한 것으로, 진단이나 처방을 대신하지 않습니다. "
                   "치료 결정은 담당 의료진과 상의하세요.",
           "privacy": "건강정보가 들어 있습니다. 본인이 요청하지 않았다면 이 메일을 삭제해 주세요."},
    "en": {"subject": "Your allergy test results",
           "hello": "Here are the allergy test result materials you requested.",
           "files": "Attachments", "report": "Patient report (HTML — opens in a browser)",
           "report_pdf": "Patient report (PDF — for printing and keeping)",
           "cardnews": "Card news (HTML)", "fhir": "HL7 FHIR bundle (JSON — for your healthcare provider)",
           "note": "These materials are meant to help you understand your test results. They do not replace "
                   "a diagnosis or prescription. Please discuss treatment decisions with your clinician.",
           "privacy": "This message contains health information. If you did not request it, please delete it."},
    "zh": {"subject": "您的过敏检测结果资料",
           "hello": "现将您申请的过敏检测结果资料随附件发送给您。",
           "files": "附件", "report": "患者报告（HTML — 用浏览器打开）",
           "report_pdf": "患者报告（PDF — 便于打印和保存）",
           "cardnews": "卡片新闻（HTML）", "fhir": "HL7 FHIR 资源包（JSON — 供医疗机构使用）",
           "note": "本资料旨在帮助您理解检测结果，不能替代诊断或处方。治疗决定请与主治医生商议。",
           "privacy": "本邮件包含健康信息。如非本人申请，请删除此邮件。"},
}


@app.post("/api/sessions/{session_id}/email", dependencies=[Depends(rate_limit("api"))])
def session_email(session_id: str, req: EmailResultsRequest, request: Request):
    """저장된 결과(리포트·카드뉴스·FHIR 번들)를 입력한 주소로 보낸다.

    언어: 요청 언어 → 세션 언어 → 한국어 순으로, 번역이 끝난(ready) 언어만 보낸다. 번역이 덜 된 언어는
    완성본처럼 보내지 않는다. 응답의 lang 이 실제로 보낸 언어, requested_lang 이 요청한 언어,
    fallback 이 대신 보냈는지 여부다.

    남용 방지: 발송 여유분을 DB 에서 원자적으로 예약한 뒤 보낸다. 한도는 세션당 1시간·받는 주소당 1일·
    접속 IP 당 1일·서비스 전체 1일. 세션은 처음 보낸 주소(또는 이미 연결된 주소)에만 보낼 수 있다.

    오류: 503 email_not_configured(SMTP 미설정) · 422 invalid_email · 404 session_not_found ·
    409 not_ready(보낼 산출물 없음) · 409 recipient_mismatch(다른 주소에 묶인 세션) ·
    429 rate_limited · 502 send_failed"""
    svc = get_email_service()
    if not svc.configured:
        raise api_error(503, "email_not_configured", "메일 발송이 설정되지 않았습니다(SMTP).")
    store = _require_store()
    to = normalize_email(req.email)
    if not to:
        raise api_error(422, "invalid_email", "이메일 주소 형식이 올바르지 않습니다.")
    session = _require_session(store, session_id)
    include = [k for k in ("report", "cardnews", "fhir") if k in (req.include or [])]
    if not include:
        raise api_error(400, "nothing_to_send", "보낼 자료를 하나 이상 고르세요(report/cardnews/fhir).")

    # 요청 언어 → 세션 언어 → 한국어 순으로 준비된(번역이 끝난) 산출물을 고른다
    wanted = _req_lang(req.lang or session["lang"])
    row = None
    for code in dict.fromkeys([wanted, session["lang"], "ko"]):
        cand = store.get_localized(session_id, code)
        if cand and cand["status"] == "ready":
            row = cand
            break
    if row is None:
        raise api_error(409, "not_ready", "보낼 결과가 아직 준비되지 않았습니다.")
    lang = row["lang"]
    txt = _EMAIL_TEXT[lang]

    # 한도 확인과 발송 기록을 한 트랜잭션으로 — 동시에 들어온 요청이 같은 여유분을 함께 쓰지 못한다
    delivery_id, refused = store.reserve_email(
        session_id, to, hash_ip(_client_ip(request)),
        per_session_hour=settings.email_max_per_session_hour,
        per_recipient_day=settings.email_max_per_recipient_day,
        per_ip_day=settings.email_max_per_ip_day, global_day=settings.email_max_global_day)
    if refused == "session_not_found":
        raise api_error(404, "session_not_found", "세션을 찾을 수 없습니다.")
    if refused == "recipient_mismatch":
        raise api_error(409, "recipient_mismatch", "이 결과는 이미 다른 이메일 주소로 보내도록 연결되어 있습니다.")
    if refused:
        raise api_error(429, "rate_limited", "메일을 너무 자주 요청했습니다. 잠시 후 다시 시도하세요.")

    names: List[str] = []
    try:
        attachments = []
        if "report" in include and row.get("report_document_html"):
            pdf = _report_pdf(row["report_document_html"], lang, session_id)
            if pdf:      # PDF 를 만들 수 없는 서버에서는 HTML 만 보낸다(빈 상자가 찍힌 PDF 를 보내지 않는다)
                attachments.append((f"allergy-report-{lang}.pdf", "application/pdf", pdf, "report_pdf"))
            attachments.append((f"allergy-report-{lang}.html", "text/html",
                                row["report_document_html"].encode("utf-8"), "report"))
        if "cardnews" in include and row.get("cardnews_html"):
            attachments.append((f"allergy-cardnews-{lang}.html", "text/html",
                                row["cardnews_html"].encode("utf-8"), "cardnews"))
        if "fhir" in include:
            data = session["input"]
            bundles = _fhir_bundles(ClassifyRequest(ocr=data["ocr"], screening=data.get("screening"),
                                                    answers=data.get("answers") or {}))
            attachments.append(("allergy-fhir-bundle.json", "application/fhir+json",
                                json.dumps(bundles, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                                "fhir"))
        names = [a[0] for a in attachments]
        items = "".join(f"<li>{txt[a[3]]} — <code>{a[0]}</code></li>" for a in attachments)
        html = (f'<div style="font-family:-apple-system,Segoe UI,Apple SD Gothic Neo,Noto Sans KR,sans-serif;'
                f'font-size:15px;line-height:1.7;color:#1f2a24;max-width:560px">'
                f'<p>{txt["hello"]}</p><p style="margin-bottom:4px"><b>{txt["files"]}</b></p><ul>{items}</ul>'
                f'<p style="color:#4d5a52;font-size:13px">{txt["note"]}</p>'
                f'<p style="color:#4d5a52;font-size:13px">{txt["privacy"]}</p></div>')
        text = "\n".join([txt["hello"], "", txt["files"] + ":"]
                         + [f"- {txt[a[3]]} ({a[0]})" for a in attachments] + ["", txt["note"], txt["privacy"]])
        svc.send(to, txt["subject"], html, text, [a[:3] for a in attachments])
    except Exception as e:  # noqa: BLE001
        logger.warning("결과 메일 발송 실패: session#%s (%s)", session_tag(session_id), type(e).__name__)
        store.finish_email(delivery_id, "failed", lang=lang, error=type(e).__name__, attachments=names)
        raise api_error(502, "send_failed", "메일을 보내지 못했습니다. 잠시 후 다시 시도하세요.")
    store.finish_email(delivery_id, "sent", lang=lang, attachments=names)
    store.attach_user(session_id, to)      # 익명 세션이면 받는 주소의 사용자로 연결(이미 연결돼 있으면 그대로)
    return {"ok": True, "session_id": session_id, "to": to, "lang": lang, "requested_lang": wanted,
            "fallback": lang != wanted, "attachments": names}


# ============================================================
# 항원 자동완성
# ============================================================
@app.get("/api/allergens")
def allergens_all(response: Response):
    """레지스트리 전체(148종)의 가벼운 목록 — 한 번 받아 두고 화면에서 걸러 쓰는 용도."""
    from services.allergen_search_service import get_allergen_search_service
    svc = get_allergen_search_service()
    response.headers["Cache-Control"] = "public, max-age=3600"
    return {"version": svc.version, "count": len(svc.items), "items": svc.all()}


@app.get("/api/allergens/search")
def allergens_search(q: str = "", limit: int = 10):
    """영문명·한글명·별칭에서 앞부분/중간 일치 검색(앞부분 일치 우선)."""
    from services.allergen_search_service import get_allergen_search_service
    items = get_allergen_search_service().search(q, limit)
    return {"query": q, "count": len(items), "items": items}


app.include_router(admin_api.auth_router)
app.include_router(admin_api.router)


# ============================================================
# 정적 프론트엔드 서빙 (맨 마지막에 마운트)
# ============================================================
CLASSIC_DIR = WEB_DIR / "classic"
# 두 UI 동시 운영: /classic (재설계 이전 클래식 UI) 를 먼저 마운트하고, / (알러젠 탐험 퀘스트 UI) 를 마지막에.
# 두 UI 는 같은 /api 를 사용하므로 판정·FHIR 결과는 동일하다.
if CLASSIC_DIR.exists():
    app.mount("/classic", StaticFiles(directory=str(CLASSIC_DIR), html=True), name="web-classic")
# 항원 소개문 검토 화면(의료진용). 루트(/) 마운트보다 먼저 걸어야 가려지지 않는다.
REVIEW_DIR = WEB_DIR / "review"
if REVIEW_DIR.exists():
    app.mount("/review", StaticFiles(directory=str(REVIEW_DIR), html=True), name="web-review")
# 관리자 대시보드(정적 껍데기). 데이터는 전부 /api/admin/* 에서 로그인 후에만 나온다.
ADMIN_DIR = WEB_DIR / "admin"
if ADMIN_DIR.exists():
    app.mount("/admin", StaticFiles(directory=str(ADMIN_DIR), html=True), name="web-admin")
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
