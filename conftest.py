"""pytest 공통 설정 — 테스트가 실제 저장소(data/store)나 실제 SMTP·관리자 설정을 건드리지 않게 한다.

config.settings 가 import 되기 전에 환경변수를 바꿔야 해서 모듈 최상단에서 처리한다.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="allergy-test-store-")
os.environ["APP_DB_PATH"] = os.path.join(_TMP, "app.sqlite3")
os.environ["I18N_DB_PATH"] = os.path.join(_TMP, "localized.sqlite3")
# 번역 캐시(data/i18n_cache.json)도 임시 파일로 돌린다. 실제 캐시를 읽으면 로컬에 쌓인 번역 유무에 따라
# 결과가 달라지고(영어 산출물이 '준비됨'이 되기도, 안 되기도 한다), 쓰면 추적 중인 파일이 테스트로 바뀐다.
os.environ["I18N_CACHE_PATH"] = os.path.join(_TMP, "i18n_cache.json")
for _name in ("ADMIN_PASSWORD", "ADMIN_SESSION_SECRET", "SMTP_HOST", "SMTP_FROM", "SMTP_USER", "SMTP_PASSWORD"):
    os.environ[_name] = ""
# 테스트가 연구실 LLM 서버를 부르지 않게 한다. .env 가 LLM_BACKEND=ollama 를 가리키고 있으면, 테스트가
# OPENAI_API_KEY 만 비워서는 번역·상담 호출이 그쪽으로 나간다 — 백엔드와 주소를 함께 비운다.
# (OPENAI_API_KEY 는 건드리지 않는다: 클라이언트 생성에 키가 필요한 기존 테스트가 있다. 서버 테스트는
#  각자 fixture 에서 키를 비운다.)
os.environ["LLM_BACKEND"] = "openai"
os.environ["OLLAMA_BASE_URL"] = ""
# IP 당 요청 수 제한은 기본으로 끈다(테스트는 한 주소에서 수백 번 부른다). 제한을 보는 테스트가 직접 켠다.
for _name in ("RATE_LIMIT_OCR_PER_MIN", "RATE_LIMIT_CLASSIFY_PER_MIN", "RATE_LIMIT_CHAT_PER_MIN",
              "RATE_LIMIT_API_PER_MIN", "SESSION_RETENTION_DAYS"):
    os.environ[_name] = "0"


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_translation_cache():
    """테스트마다 번역기 싱글턴을 새로 만든다 — 앞 테스트가 메모리 캐시에 남긴 번역이 다음 테스트로 새지 않게."""
    from services import translation_service
    assert str(translation_service.CACHE_PATH).startswith(_TMP), "테스트가 실제 번역 캐시를 가리키고 있습니다"
    translation_service._svc = None
    translation_service._last_key = None
    try:
        translation_service.CACHE_PATH.unlink()
    except FileNotFoundError:
        pass
    yield


@pytest.fixture(autouse=True)
def _reset_throttles():
    """요청 수 제한·로그인 실패 기록은 프로세스 전역이다 — 테스트 사이에 넘어가지 않게 비운다."""
    import server
    from services import admin_api
    server.rate_limiter.reset()
    admin_api.reset_login_throttle()
    admin_api._revoked.clear()
    yield
