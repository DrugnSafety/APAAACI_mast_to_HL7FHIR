"""저장소·3개 언어 산출물·관리자·메일·항원 검색 엔드포인트 테스트.

LLM·SMTP 는 전부 가짜로 바꿔서 돈다(실제 번역 호출·실제 메일 발송 없음). DB 는 테스트마다 임시 폴더에 만든다.
"""
import json
import smtplib
import sqlite3

import pytest
from fastapi.testclient import TestClient

import server
from config.settings import settings
from services import admin_api
from services.store_service import get_store, normalize_email
from services.translation_service import TranslationService


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_db_path", tmp_path / "app.sqlite3")
    monkeypatch.setattr(settings, "i18n_db_path", tmp_path / "localized.sqlite3")
    monkeypatch.setattr(settings, "storage_enabled", True)
    monkeypatch.setattr(settings, "i18n_background", True)
    monkeypatch.setattr(settings, "openai_api_key", "")        # 실제 LLM 호출 차단
    monkeypatch.setenv("OPENAI_API_KEY", "")                   # 번역기는 settings 가 비면 환경변수로 되돌아간다
    monkeypatch.setattr(settings, "ollama_base_url", "")
    monkeypatch.setattr(settings, "admin_password", "")
    monkeypatch.setattr(settings, "admin_session_secret", "")
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from", "")
    admin_api.reset_login_throttle()
    # 브라우저처럼 Origin 을 붙인다 — 관리자 API 는 출처가 다른 상태 변경 요청을 받지 않는다
    return TestClient(server.app, headers={"Origin": "http://testserver"})


def _admin_get(client, path, token):
    """토큰을 쿠키로 실어 보낸다(관리자 API 는 쿠키만 받는다)."""
    return client.get(path, headers={"Cookie": f"{admin_api.COOKIE_NAME}={token}"})


@pytest.fixture
def fake_translation(monkeypatch):
    """번역기를 '[lang] …' 표식을 붙이는 가짜로 바꾼다."""
    monkeypatch.setattr(TranslationService, "translate_markdown",
                        lambda self, md, lang: f"# [{lang}] translated report\n\nAll clear.")
    monkeypatch.setattr(TranslationService, "translate_html",
                        lambda self, html, lang: f"<!doctype html><html><body>[{lang}] translated</body></html>")
    monkeypatch.setattr(TranslationService, "translate_obj", lambda self, obj, lang, keys: obj)
    monkeypatch.setattr(TranslationService, "translate_text", lambda self, text, lang: f"[{lang}] {text}")
    monkeypatch.setattr(TranslationService, "translate_batch",
                        lambda self, texts, lang: [f"[{lang}] t" for _ in texts])


def _payload(client, **extra):
    return {"ocr": client.get("/api/ocr/demo").json(), "answers": {},
            "screening": {"allergic_diseases": ["allergic_rhinitis"]}, **extra}


# ============================================================
# A. 저장
# ============================================================
class TestStorage:
    def test_classify_stores_input_outputs_and_user(self, client):
        body = _payload(client, email="  Patient@Example.com ")
        r = client.post("/api/classify", json=body)
        assert r.status_code == 200
        out = r.json()
        sid = out["session_id"]
        assert sid and out["user_email"] == "patient@example.com"

        session = get_store().get_session(sid)
        assert session["email"] == "patient@example.com"
        assert session["input"]["ocr"]["results"] == body["ocr"]["results"]
        assert session["input"]["screening"]["allergic_diseases"] == ["allergic_rhinitis"]
        assert session["result_count"] == len(body["ocr"]["results"])
        assert session["positive_count"] == len(out["assessments"])
        assert {a["allergen_name"] for a in session["allergens"]} == \
               {a["allergen_name"] for a in out["assessments"]}

        saved = client.get(f"/api/sessions/{sid}/outputs/ko").json()
        assert saved["report_markdown"] == out["report_markdown"]
        assert saved["cardnews_html"] == out["cardnews_html"]
        assert saved["report_document_html"] == out["report_document_html"]

    def test_without_email_is_anonymous_then_identify(self, client):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        assert get_store().get_session(sid)["user_id"] is None
        assert client.get(f"/api/sessions/{sid}/status").json()["has_email"] is False

        assert client.post(f"/api/sessions/{sid}/identify", json={"email": "nope"}).status_code == 422
        r = client.post(f"/api/sessions/{sid}/identify", json={"email": "a@b.co"})
        assert r.status_code == 200
        assert get_store().get_session(sid)["email"] == "a@b.co"

    def test_same_session_id_overwrites_instead_of_duplicating(self, client):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        again = client.post("/api/classify", json=_payload(client, session_id=sid)).json()
        assert again["session_id"] == sid
        assert get_store().list_sessions()["total"] == 1

    def test_unknown_session_id_gets_a_fresh_server_generated_id(self, client):
        out = client.post("/api/classify", json=_payload(client, session_id="chosen-by-client")).json()
        assert out["session_id"] != "chosen-by-client" and len(out["session_id"]) == 32

    def test_chat_turns_are_stored_under_the_session(self, client):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        for q in ("검사 결과가 무슨 뜻인가요?", "집먼지진드기는 어떻게 피하나요?"):
            r = client.post("/api/chat", json=_payload(
                client, session_id=sid, messages=[{"role": "user", "content": q}]))
            assert r.status_code == 200 and r.json()["session_id"] == sid
        msgs = client.get(f"/api/sessions/{sid}/chat").json()["messages"]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
        assert msgs[0]["content"] == "검사 결과가 무슨 뜻인가요?"
        assert msgs[1]["content"]

    def test_chat_without_session_answers_but_does_not_create_one(self, client):
        """세션은 /api/classify 만 만든다. 예전에는 session_id 없는 상담 호출마다 세션이 하나씩 늘었다."""
        for messages in ([{"role": "user", "content": "안녕하세요"}], []):       # [] = 추천 질문만 받는 호출
            r = client.post("/api/chat", json=_payload(client, messages=messages))
            assert r.status_code == 200 and r.json()["session_id"] is None
            assert r.json()["suggestions"]
        r = client.post("/api/chat", json=_payload(client, session_id="no-such-session",
                                                   messages=[{"role": "user", "content": "안녕하세요"}]))
        assert r.status_code == 200 and r.json()["session_id"] is None and r.json()["reply"]
        assert get_store().list_sessions()["total"] == 0

    def test_storage_failure_does_not_fail_the_request(self, client, monkeypatch):
        def boom():
            raise RuntimeError("disk full")
        monkeypatch.setattr(server, "get_store", boom)
        r = client.post("/api/classify", json=_payload(client))
        assert r.status_code == 200 and r.json()["session_id"] is None and r.json()["report_markdown"]

    def test_storage_disabled(self, client, monkeypatch):
        monkeypatch.setattr(settings, "storage_enabled", False)
        r = client.post("/api/classify", json=_payload(client))
        assert r.status_code == 200 and r.json()["session_id"] is None
        assert client.get("/api/sessions/x/status").status_code == 503

    def test_unknown_session_is_404(self, client):
        assert client.get("/api/sessions/nope/status").status_code == 404
        assert client.get("/api/sessions/nope/outputs/ko").status_code == 404

    def test_email_normalization_rejects_header_injection(self):
        assert normalize_email("A@B.com") == "a@b.com"
        assert normalize_email("a@b.com\nBcc: x@y.com") is None
        assert normalize_email("a b@c.com") is None
        assert normalize_email("") is None


# ============================================================
# B. 3개 언어 산출물(별도 DB) — 번역은 가짜
# ============================================================
class TestTrilingual:
    def test_other_languages_are_generated_in_background(self, client, fake_translation, tmp_path):
        out = client.post("/api/classify", json=_payload(client, lang="ko")).json()
        sid = out["session_id"]
        assert out["lang"] == "ko"
        # 응답 시점: 요청 언어는 준비됨, 나머지는 대기
        assert out["i18n"] == {"ko": "ready", "en": "pending", "zh": "pending"}

        # TestClient 는 백그라운드 작업까지 끝낸 뒤 돌아온다
        st = client.get(f"/api/sessions/{sid}/status").json()
        assert st["done"] is True and sorted(st["ready"]) == ["en", "ko", "zh"]
        for lang in ("en", "zh"):
            saved = client.get(f"/api/sessions/{sid}/outputs/{lang}").json()
            assert f"[{lang}] translated report" in saved["report_markdown"]
            assert f"[{lang}] translated" in saved["cardnews_html"]
            assert saved["report_html"] and saved["assessments"]

        # 산출물은 본 DB 가 아닌 별도 DB 파일에 있다
        i18n = sqlite3.connect(tmp_path / "localized.sqlite3")
        assert sorted(r[0] for r in i18n.execute(
            "SELECT lang FROM localized_outputs WHERE session_id=?", (sid,))) == ["en", "ko", "zh"]
        main = sqlite3.connect(tmp_path / "app.sqlite3")
        tables = {r[0] for r in main.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "localized_outputs" not in tables and "sessions" in tables

    def test_request_in_english_backfills_korean_and_chinese(self, client, fake_translation):
        out = client.post("/api/classify", json=_payload(client, lang="en")).json()
        assert "[en] translated report" in out["report_markdown"]
        st = client.get(f"/api/sessions/{out['session_id']}/status").json()
        assert sorted(st["ready"]) == ["en", "ko", "zh"]
        ko = client.get(f"/api/sessions/{out['session_id']}/outputs/ko").json()
        assert "translated" not in ko["report_markdown"]

    def test_translation_failure_never_fails_the_request(self, client, monkeypatch):
        def boom(self, md, lang):
            raise RuntimeError("translation backend down")
        monkeypatch.setattr(TranslationService, "translate_markdown", boom)
        r = client.post("/api/classify", json=_payload(client, lang="ko"))
        assert r.status_code == 200 and r.json()["report_markdown"]
        sid = r.json()["session_id"]
        st = client.get(f"/api/sessions/{sid}/status").json()
        assert st["done"] is True
        assert st["langs"]["ko"]["status"] == "ready"
        assert st["langs"]["en"] == {**st["langs"]["en"], "status": "failed", "error": "RuntimeError"}
        r = client.get(f"/api/sessions/{sid}/outputs/en")
        assert r.status_code == 409 and r.json()["detail"]["status"] == "failed"

    def test_untranslated_output_is_not_stored_as_ready(self, client, monkeypatch):
        """번역기가 원문(한국어)을 그대로 돌려주면 그 언어는 ready 가 아니다."""
        monkeypatch.setattr(TranslationService, "translate_markdown", lambda self, md, lang: md)
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        st = client.get(f"/api/sessions/{sid}/status").json()["langs"]
        assert st["en"]["status"] == "skipped" and st["en"]["error"] == "translation_unavailable"
        assert get_store().get_localized(sid, "en")["report_markdown"] is None

    def test_background_can_be_turned_off(self, client, monkeypatch):
        monkeypatch.setattr(settings, "i18n_background", False)
        out = client.post("/api/classify", json=_payload(client)).json()
        assert out["i18n"] == {"ko": "ready", "en": "missing", "zh": "missing"}

    def test_admin_retry_regenerates_failed_languages(self, client, monkeypatch, fake_translation):
        monkeypatch.setattr(settings, "admin_password", "pw")
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        get_store().save_localized(sid, "en", "pending")        # 서버 재시작으로 멈춘 상태 흉내
        client.post("/api/admin/login", json={"username": "admin", "password": "pw"})
        r = client.post(f"/api/admin/sessions/{sid}/i18n/retry")
        assert r.status_code == 200 and r.json()["retried"] == ["en"]
        assert r.json()["langs"]["en"]["status"] == "ready"


# ============================================================
# C. 관리자
# ============================================================
ADMIN_GETS = ["/api/admin/me", "/api/admin/stats", "/api/admin/users", "/api/admin/users/1",
              "/api/admin/sessions", "/api/admin/sessions/abc", "/api/admin/sessions/abc/outputs/ko"]


class TestAdmin:
    def test_disabled_when_no_password(self, client):
        assert client.get("/api/health").json()["services"]["admin"] is False
        assert client.get("/api/admin/status").json() == {
            "enabled": False, "authenticated": False, "username": None}
        for path in ADMIN_GETS:
            r = client.get(path)
            assert r.status_code == 503 and r.json()["detail"]["code"] == "admin_disabled", path
        r = client.post("/api/admin/login", json={"username": "admin", "password": ""})
        assert r.status_code == 503
        # 비밀번호가 없을 때 만든 토큰은 통하지 않는다
        assert _admin_get(client, "/api/admin/users", admin_api.make_token("admin")).status_code == 503

    def test_401_without_login(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_password", "s3cret-pw")
        for path in ADMIN_GETS:
            assert client.get(path).status_code == 401, path
        assert client.delete("/api/admin/users/1").status_code == 401
        assert client.delete("/api/admin/sessions/abc").status_code == 401
        assert client.post("/api/admin/sessions/abc/i18n/retry").status_code == 401

    def test_wrong_credentials_and_lockout(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_password", "s3cret-pw")
        for _ in range(admin_api.LOGIN_MAX_FAILURES):
            r = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
            assert r.status_code == 401
        assert client.post("/api/admin/login", json={"username": "root", "password": "s3cret-pw"}
                           ).status_code == 429
        # 잠긴 동안에는 올바른 비밀번호도 받지 않는다
        assert client.post("/api/admin/login", json={"username": "admin", "password": "s3cret-pw"}
                           ).status_code == 429

    def test_forged_or_expired_token_is_rejected(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_password", "s3cret-pw")
        good = admin_api.make_token("admin")
        assert _admin_get(client, "/api/admin/me", good).status_code == 200
        payload, sig = good.split(".")
        forged = admin_api._b64(b"admin|9999999999|abc") + "." + sig
        expired = admin_api.make_token("admin", ttl_seconds=-1)
        for bad in (forged, expired, payload + ".AAAA", "garbage"):
            assert _admin_get(client, "/api/admin/me", bad).status_code == 401
        # 비밀번호를 바꾸면 이전 토큰은 무효
        monkeypatch.setattr(settings, "admin_password", "rotated")
        assert _admin_get(client, "/api/admin/me", good).status_code == 401

    def test_login_then_browse_users_sessions_outputs_chat(self, client, monkeypatch, fake_translation):
        monkeypatch.setattr(settings, "admin_password", "s3cret-pw")
        monkeypatch.setattr(settings, "admin_username", "boss")
        sid = client.post("/api/classify", json=_payload(client, email="kim@example.com")).json()["session_id"]
        client.post("/api/classify", json=_payload(client))                      # 익명 세션
        client.post("/api/chat", json=_payload(client, session_id=sid,
                                               messages=[{"role": "user", "content": "자작나무가 뭔가요?"}]))

        r = client.post("/api/admin/login", json={"username": "boss", "password": "s3cret-pw"})
        assert r.status_code == 200
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert client.get("/api/admin/status").json()["authenticated"] is True

        users = client.get("/api/admin/users", params={"q": "kim"})
        assert users.headers["cache-control"] == "no-store"
        assert users.json()["total"] == 1
        user = users.json()["items"][0]
        assert user["email"] == "kim@example.com" and user["session_count"] == 1 and user["chat_count"] == 1
        assert client.get("/api/admin/users", params={"q": "zzz"}).json()["total"] == 0

        detail = client.get(f"/api/admin/users/{user['id']}").json()
        assert [s["id"] for s in detail["sessions"]] == [sid]
        assert detail["sessions"][0]["i18n"] == {"ko": "ready", "en": "ready", "zh": "ready"}

        assert client.get("/api/admin/sessions", params={"anonymous": True}).json()["total"] == 1
        assert client.get("/api/admin/sessions").json()["total"] == 2

        s = client.get(f"/api/admin/sessions/{sid}").json()
        assert s["email"] == "kim@example.com"
        assert len(s["input"]["ocr"]["results"]) == 6 and s["input"]["screening"]
        assert [m["role"] for m in s["chat"]] == ["user", "assistant"]
        assert s["i18n"]["zh"]["status"] == "ready" and s["emails"] == []

        zh = client.get(f"/api/admin/sessions/{sid}/outputs/zh").json()
        assert "[zh] translated report" in zh["report_markdown"]

        stats = client.get("/api/admin/stats", params={"days": 7, "tz_offset": 540}).json()
        assert stats["totals"]["users"] == 1 and stats["totals"]["sessions"] == 2
        assert stats["totals"]["anonymous_sessions"] == 1 and stats["totals"]["chat_messages"] == 1
        assert len(stats["sessions_per_day"]) == 7 and stats["sessions_per_day"][-1]["sessions"] == 2
        assert stats["languages"] == {"ko": 2, "en": 0, "zh": 0}
        assert stats["top_allergens"][0]["sessions"] == 2
        assert stats["localized_outputs"]["ready"] == 6

        assert client.delete(f"/api/admin/users/{user['id']}").json() == {"ok": True}
        assert client.get(f"/api/admin/sessions/{sid}").status_code == 404
        assert get_store().get_localized(sid, "ko") is None and get_store().list_chat(sid) == []

        client.post("/api/admin/logout")
        assert client.get("/api/admin/users").status_code == 401

    def test_admin_page_is_served(self, client):
        r = client.get("/admin/")
        assert r.status_code == 200 and "text/html" in r.headers["content-type"]


# ============================================================
# D. 메일 — SMTP 는 가짜
# ============================================================
class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout=None, **kw):
        self.host, self.port, self.tls, self.auth = host, port, False, None

    def starttls(self, context=None):
        self.tls = True

    def login(self, user, password):
        self.auth = (user, password)

    def send_message(self, msg):
        if FakeSMTP.fail:
            raise smtplib.SMTPRecipientsRefused({})
        FakeSMTP.sent.append((self, msg))

    def quit(self):
        pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_from", "noreply@clinic.test")
    monkeypatch.setattr(settings, "smtp_user", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "pw")
    monkeypatch.setattr(settings, "smtp_tls", "starttls")
    return FakeSMTP


class TestEmail:
    def test_not_configured(self, client):
        assert client.get("/api/health").json()["services"]["email"] is False
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "a@b.co"})
        assert r.status_code == 503 and r.json()["detail"]["code"] == "email_not_configured"
        assert get_store().list_emails(sid) == []

    def test_sends_report_cardnews_and_fhir(self, client, smtp):
        assert client.get("/api/health").json()["services"]["email"] is True
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "Patient@Example.com"})
        assert r.status_code == 200, r.text
        assert r.json()["to"] == "patient@example.com" and r.json()["lang"] == "ko"

        (conn, msg), = smtp.sent
        assert conn.host == "smtp.test" and conn.tls and conn.auth == ("mailer", "pw")
        assert msg["To"] == "patient@example.com" and msg["From"] == "noreply@clinic.test"
        files = {p.get_filename(): p for p in msg.iter_attachments()}
        assert set(files) == {"allergy-report-ko.pdf", "allergy-report-ko.html", "allergy-cardnews-ko.html",
                              "allergy-fhir-bundle.json"}
        assert files["allergy-report-ko.pdf"].get_payload(decode=True)[:5] == b"%PDF-"
        assert r.json()["attachments"] == list(files)
        bundle = json.loads(files["allergy-fhir-bundle.json"].get_payload(decode=True))
        assert bundle["observation_bundle"]["resourceType"] == "Bundle"
        assert b"<html" in files["allergy-report-ko.html"].get_payload(decode=True)[:200].lower()
        assert msg.get_body(("html",)) is not None and msg.get_body(("plain",)) is not None

        log = get_store().list_emails(sid)
        assert [(e["to_email"], e["status"]) for e in log] == [("patient@example.com", "sent")]
        # 익명 세션은 받는 주소의 사용자로 연결된다
        assert get_store().get_session(sid)["email"] == "patient@example.com"

    def test_include_subset_and_ready_language_fallback(self, client, smtp):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/email",
                        json={"email": "a@b.co", "lang": "en", "include": ["fhir"]})
        # en 은 번역 엔진이 없어 준비되지 않았으므로 준비된 ko 로 보낸다
        assert r.status_code == 200 and r.json()["lang"] == "ko"
        assert r.json()["attachments"] == ["allergy-fhir-bundle.json"]

    def test_invalid_address_and_unknown_session(self, client, smtp):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        for bad in ("nope", "a@b", "a@b.co\r\nBcc: x@y.zz", ""):
            r = client.post(f"/api/sessions/{sid}/email", json={"email": bad})
            assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_email", bad
        assert client.post("/api/sessions/nope/email", json={"email": "a@b.co"}).status_code == 404
        assert smtp.sent == []

    def test_rate_limited_per_session(self, client, smtp, monkeypatch):
        monkeypatch.setattr(settings, "email_max_per_session_hour", 2)
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        codes = [client.post(f"/api/sessions/{sid}/email", json={"email": "u@b.co"}).status_code
                 for i in range(3)]
        assert codes == [200, 200, 429] and len(smtp.sent) == 2

    def test_rate_limited_per_recipient(self, client, smtp, monkeypatch):
        monkeypatch.setattr(settings, "email_max_per_recipient_day", 1)
        codes = []
        for _ in range(2):
            sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
            codes.append(client.post(f"/api/sessions/{sid}/email", json={"email": "same@b.co"}).status_code)
        assert codes == [200, 429]

    def test_smtp_failure_is_reported_and_logged(self, client, smtp):
        smtp.fail = True
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "a@b.co"})
        assert r.status_code == 502 and r.json()["detail"]["code"] == "send_failed"
        log = get_store().list_emails(sid)
        assert log[0]["status"] == "failed" and log[0]["error"] == "SMTPRecipientsRefused"


# ============================================================
# E. 항원 자동완성
# ============================================================
class TestAllergenSearch:
    def test_full_list(self, client):
        r = client.get("/api/allergens")
        data = r.json()
        assert data["count"] == 159 == len(data["items"])      # 2026-10: 벌독 5종 추가(154 → 159)
        assert set(data["items"][0]) == {"id", "canonical_name", "korean_name", "category", "aliases", "zh_names"}
        assert "max-age" in r.headers["cache-control"]

    def test_prefix_ranks_before_substring(self, client):
        items = client.get("/api/allergens/search", params={"q": "ap", "limit": 50}).json()["items"]
        names = [i["canonical_name"] for i in items]
        assert names[0] == "Apple"
        kinds = [i["match"]["type"] for i in items]
        assert "substring" in kinds
        assert kinds == sorted(kinds, key=lambda k: k != "prefix")       # prefix 가 전부 앞에 온다
        # 화면에 보이는 필드에 없으면 OCR 별칭으로 걸린 것이다(match.text 에 근거가 남는다)
        assert all("ap" in i["match"]["text"].lower() for i in items)

    def test_korean_name_alias_and_chinese(self, client):
        def first(q):
            return client.get("/api/allergens/search", params={"q": q}).json()["items"][0]
        assert first("자작")["canonical_name"] == "Birch"
        assert first("Tetranychus")["canonical_name"] == "2-spotted spider mite"
        assert first("tetranychus")["match"]["field"] == "alias"
        assert first("花生")["canonical_name"] == "Peanut"
        assert first("d farinae")["canonical_name"] == "Dermatophagoides farinae" or \
            first("farinae")["canonical_name"] == "Dermatophagoides farinae"

    def test_limit_and_empty(self, client):
        assert len(client.get("/api/allergens/search", params={"q": "a", "limit": 3}).json()["items"]) == 3
        assert client.get("/api/allergens/search", params={"q": "   "}).json() == {
            "query": "   ", "count": 0, "items": []}
        assert client.get("/api/allergens/search", params={"q": "zzzzqqq"}).json()["count"] == 0
