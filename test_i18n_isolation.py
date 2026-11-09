"""언어 간 오염 회귀 테스트.

영어(또는 중국어) 요청이 한 번 지나간 뒤 한국어 요청에 번역문이 섞여 나오던 문제를 막는다.
원인은 `translate_obj` 가 응답을 제자리에서 고쳤는데, 그 응답이 프로세스 공용 객체
(지식베이스 항목의 `avoidance_control_ko` 리스트, 문진 선택지 상수)를 그대로 참조하고 있던 것.

번역기는 `⟪lang⟫…` 표식을 붙이는 가짜로 바꾼다(LLM 호출 없음, 번역 캐시 파일 사용 없음).
"""
import copy
import hashlib
import re
import sys
import threading

import pytest
from fastapi.testclient import TestClient

import server
from config.settings import settings
from services.translation_service import TranslationService

TAG = re.compile(r"⟪(en|zh)⟫")
_PLACEHOLDER = re.compile(r"⟦\d+⟧")

# 요청 사이에 값이 바뀌면 안 되는 공용 상태(모듈 상수 + 서비스 싱글턴)를 가진 모듈
SHARED_MODULES = (
    "knowledge_service", "questionnaire_service", "relevance_service", "crossreactivity_service",
    "clinical_group_service", "exposure_guidance_service", "screening_service", "report_service",
    "report_design", "cardnews_service", "cardnews_classic", "result_chat_service",
    "category_resolver", "ontology_service", "allergen_search_service",
)


def _fake_batch(self, texts, lang):
    """한국어가 든 문자열만 '⟪lang⟫ T<해시>' 로 바꾼다. 줄바꿈 자리표시자는 실제 번역기처럼 보존한다."""
    if lang not in ("en", "zh"):
        return list(texts)
    out = []
    for t in texts:
        if not self._needs_translation(t):
            out.append(t)
            continue
        digest = hashlib.sha1(t.encode("utf-8")).hexdigest()[:8]
        out.append(f"⟪{lang}⟫ T{digest}" + "".join(_PLACEHOLDER.findall(t)))
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_db_path", tmp_path / "app.sqlite3")
    monkeypatch.setattr(settings, "i18n_db_path", tmp_path / "localized.sqlite3")
    monkeypatch.setattr(settings, "storage_enabled", True)
    monkeypatch.setattr(settings, "i18n_background", True)
    monkeypatch.setattr(settings, "openai_api_key", "")        # 실제 LLM 호출 차단
    monkeypatch.setattr(settings, "ollama_base_url", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(TranslationService, "translate_batch", _fake_batch)
    return TestClient(server.app)


def _freeze(value, seen=None, depth=0):
    """공용 상태를 비교 가능한 불변 값으로 바꾼다(서비스 객체 안의 dict/list 까지 따라 들어간다)."""
    seen = set() if seen is None else seen
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if id(value) in seen or depth > 12:
        return "<seen>"
    seen = seen | {id(value)}
    if isinstance(value, dict):
        return tuple((repr(k), _freeze(v, seen, depth + 1)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v, seen, depth + 1) for v in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(repr(v) for v in value))
    if getattr(type(value), "__module__", "").startswith("services.") and hasattr(value, "__dict__"):
        return (type(value).__name__, _freeze(vars(value), seen, depth + 1))
    return f"<{type(value).__name__}>"


def _shared_state():
    state = {}
    for short in SHARED_MODULES:
        mod = sys.modules.get(f"services.{short}")
        if mod is None:
            continue
        for name, value in vars(mod).items():
            if name.startswith("__") or callable(value) or isinstance(value, type(sys)):
                continue
            state[f"{short}.{name}"] = _freeze(value)
    from services import exposure_guidance_service
    state["exposure_guidance_service._animal_data()"] = _freeze(exposure_guidance_service._animal_data())
    return state


def _diff(before, after):
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _tags(obj):
    """obj 안의 모든 문자열에서 번역 표식 언어를 모은다."""
    found = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            found |= _tags(k) | _tags(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            found |= _tags(v)
    elif isinstance(obj, str):
        found |= set(TAG.findall(obj))
    return found


def _ocr(client):
    return client.get("/api/ocr/demo").json()


def _answers(client, ocr):
    """모든 문항에 답한다 — 교차반응 음식·OAS 목록까지 채워져 리스트 필드가 전부 번역 경로를 탄다."""
    q = client.post("/api/questionnaire", json={"ocr": ocr, "lang": "ko"}).json()["questionnaire"]
    answers = {}
    for section in q["sections"]:
        for item in section["questions"]:
            values = [o["value"] for o in item.get("options", [])]
            if values:
                answers[item["id"]] = values if item["type"].startswith("multi") else values[0]
    return answers


_VOLATILE = ("session_id", "user_email", "i18n")


def _stable(body):
    return {k: v for k, v in body.items() if k not in _VOLATILE}


class TestTranslateObjDoesNotMutate:
    def test_input_object_is_left_alone(self, monkeypatch):
        monkeypatch.setattr(TranslationService, "translate_batch", _fake_batch)
        svc = TranslationService(api_key="")
        shared_list = ["침구는 주 1회 세탁", "실내 습도 50% 이하"]
        shared_option = {"value": "yes", "label": "예"}
        payload = {"assessments": [{"avoidance_control_ko": shared_list, "biology_ko": "집먼지진드기"}],
                   "questions": [{"title": "증상이 있나요?", "options": [shared_option]}]}
        original = copy.deepcopy(payload)

        out = svc.translate_obj(payload, "en", {"avoidance_control_ko", "biology_ko", "title", "label"})

        assert _tags(out) == {"en"}
        assert payload == original and not _tags(payload)
        assert shared_list == ["침구는 주 1회 세탁", "실내 습도 50% 이하"]
        assert shared_option == {"value": "yes", "label": "예"}
        assert out["assessments"][0]["avoidance_control_ko"] is not shared_list


class TestNoCrossLanguageContamination:
    def test_ko_after_en_and_zh_matches_ko_before(self, client, monkeypatch):
        monkeypatch.setattr(settings, "i18n_background", False)
        ocr = _ocr(client)
        answers = _answers(client, ocr)
        screening = {"allergic_diseases": ["allergic_rhinitis"], "pets": ["cat"]}

        def call(lang):
            base = {"ocr": ocr, "screening": screening, "lang": lang}
            out = {
                "questionnaire": client.post("/api/questionnaire", json=base).json(),
                "classify_quest": _stable(client.post(
                    "/api/classify", json={**base, "answers": answers, "ui": "quest"}).json()),
                "classify_classic": _stable(client.post(
                    "/api/classify", json={**base, "answers": answers, "ui": "classic"}).json()),
                "chat": _stable(client.post("/api/chat", json={
                    **base, "answers": answers,
                    "messages": [{"role": "user", "content": "집먼지진드기는 어떻게 피하나요?"}]}).json()),
                "health": client.get("/api/health", params={"lang": lang}).json()["screening_options"],
                "allergen": client.get("/api/allergen", params={"name": "Birch pollen"}).json(),
            }
            return out

        ko_before = call("ko")
        state_before = _shared_state()
        assert not _tags(ko_before)

        en = call("en")
        zh = call("zh")
        ko_after = call("ko")
        en_again = call("en")

        # 번역 경로가 실제로 돌았는지(가짜 번역기가 헛돌면 이 테스트는 의미가 없다)
        for name in ("questionnaire", "classify_quest", "classify_classic"):
            assert _tags(en[name]) == {"en"}, name
            assert _tags(zh[name]) == {"zh"}, name
        guidance = [a["avoidance_control_ko"] for a in en["classify_quest"]["assessments"]]
        assert any(guidance) and all(TAG.match(x) for items in guidance for x in items)

        assert _diff(state_before, _shared_state()) == [], "번역이 공용 지식/상수 객체를 바꿨다"
        assert not _tags(ko_after)
        for name in ko_before:
            assert ko_after[name] == ko_before[name], f"영어·중국어 요청 뒤 한국어 {name} 응답이 달라졌다"
        assert en_again == en, "같은 언어를 다시 요청했는데 결과가 달라졌다(이중 번역)"

    def test_knowledge_base_entries_keep_their_korean(self, client, monkeypatch):
        monkeypatch.setattr(settings, "i18n_background", False)
        from services.knowledge_service import get_knowledge_service
        ks = get_knowledge_service()
        entries = copy.deepcopy(ks.entries)
        generated = copy.deepcopy(ks.generated)
        ocr = _ocr(client)
        for lang in ("en", "zh", "ko"):
            assert client.post("/api/questionnaire", json={"ocr": ocr, "lang": lang}).status_code == 200
            assert client.post("/api/classify", json={"ocr": ocr, "answers": {}, "lang": lang}).status_code == 200
        assert ks.entries == entries
        assert ks.generated == generated
        assert not _tags(ks.entries) and not _tags(ks.generated)

    @pytest.mark.parametrize("first", ["en", "zh", "ko"])
    def test_background_generation_stores_each_language_cleanly(self, client, first):
        """요청 언어는 바로, 나머지 두 언어는 백그라운드에서 만들어 저장한다 — 저장본끼리 섞이면 안 된다."""
        ocr = _ocr(client)
        answers = _answers(client, ocr)
        state_before = _shared_state()
        r = client.post("/api/classify", json={"ocr": ocr, "answers": answers, "lang": first})
        assert r.status_code == 200
        sid = r.json()["session_id"]
        assert sid

        status = client.get(f"/api/sessions/{sid}/status").json()
        assert sorted(status["ready"]) == ["en", "ko", "zh"], status["langs"]
        stored = {lang: client.get(f"/api/sessions/{sid}/outputs/{lang}").json() for lang in ("ko", "en", "zh")}
        assert not _tags(stored["ko"]), "저장된 한국어 산출물에 번역문이 섞였다"
        assert _tags(stored["en"]) == {"en"}
        assert _tags(stored["zh"]) == {"zh"}
        assert _diff(state_before, _shared_state()) == []

        # 같은 입력을 한국어만으로 만든 결과와 저장된 한국어가 같아야 한다
        ko_only = server._generate_outputs(
            server.ClassifyRequest(ocr=ocr, answers=answers, lang="ko"), "ko")
        for key in ("assessments", "report_markdown", "report_document_html", "cardnews_html"):
            assert stored["ko"][key] == ko_only[key], key

    def test_three_languages_generated_concurrently(self, client):
        ocr = _ocr(client)
        answers = _answers(client, ocr)
        req = server.ClassifyRequest(ocr=ocr, answers=answers)
        baseline = {lang: server._generate_outputs(req, lang) for lang in ("ko", "en", "zh")}
        state_before = _shared_state()

        results, errors = [], []
        start = threading.Barrier(9)

        def work(lang):
            try:
                start.wait(timeout=30)
                results.append((lang, server._generate_outputs(req, lang)))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=work, args=(lang,)) for lang in ("ko", "en", "zh") * 3]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert errors == [] and len(results) == 9
        for lang, out in results:
            assert _tags(out) == ({lang} if lang != "ko" else set()), lang
            assert out == baseline[lang], f"동시 생성한 {lang} 산출물이 단독 생성 결과와 다르다"
        assert _diff(state_before, _shared_state()) == []
