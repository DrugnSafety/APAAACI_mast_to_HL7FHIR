"""세 갈래 작업 사이에 남은 연결 지점의 회귀 테스트.

LLM·SMTP 는 전부 가짜다(test_security_review_fixes 의 대역을 그대로 쓴다).
"""
import html
import re
from html.parser import HTMLParser

import pytest

from config.settings import settings
from test_security_review_fixes import HANGUL, FakeLLM, _payload, client, smtp  # noqa: F401  (fixture)


# ============================================================
# 2. 번역기가 엔티티를 풀거나 마크업·링크·속성을 끼워 넣어도 리포트에 실행 가능한 것이 생기지 않는다
# ============================================================
_ACTIVE_TAGS = {"script", "img", "iframe", "svg", "object", "embed", "form", "base", "math", "video", "audio"}
_URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href", "srcdoc", "data"}


class _Audit(HTMLParser):
    """브라우저처럼 속성값의 엔티티를 푼 뒤, 실행될 수 있는 태그·속성을 모은다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = []

    def handle_starttag(self, tag, attrs):
        if tag in _ACTIVE_TAGS:
            self.found.append(tag)
        for name, value in attrs:
            url = re.sub(r"[\x00-\x20]", "", value or "").lower()      # 브라우저는 URL 의 탭·줄바꿈을 지운다
            if name.startswith("on") or (name in _URL_ATTRS and re.match(r"(javascript|vbscript|data):", url)):
                self.found.append(f"{tag}[{name}]")

    handle_startendtag = handle_starttag


def _active(doc: str):
    audit = _Audit()
    audit.feed(doc)
    audit.close()
    return sorted(audit.found)


_INJECTIONS = [
    " <img src=x onerror=alert(1)>",
    " <script>alert(1)</script>",
    " <svg/onload=alert(1)>",
    " <img src=x onerror=alert(1) ",                       # 닫지 않은 태그
    " <javascript:alert(1)>",
    " [x](javascript:alert(1))",
    " [x]( JaVaScRiPt:alert(1))",
    " [x](&#106;avascript:alert(1))",
    " [x](&#x6A;avascript&#58;alert(1))",
    " [x](java&Tab;script:alert(1))",
    " [x](JAVASCRIPT&colon;alert(1))",
    " [x](<javascript:alert(1)>)",
    " [x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    " [x][evil]\n[evil]: javascript:alert(1)",
    " [x][evil]\n[evil]: &#106;avascript:alert(1)",
    " [x][evil]\n\n[evil]: <javascript:alert(1)>",
    ' {: onclick="alert(1)" }',
    "\n{: onmouseover=alert(1) }",
    " ![a](x){: onerror=alert(1) }",
    " **b**{: onclick=alert(1) }",
    "\n\n```{ .x onclick=alert(1) }\ncode\n```",
]


def _hostile(payload):
    """한글을 'tr' 로 바꾸고, 이스케이프된 엔티티를 풀어 버리고, 공격 문자열을 덧붙이는 번역기."""
    return lambda text: html.unescape(HANGUL.sub("tr", text)) + payload


def _hostile_body(client, lang):
    body = _payload(client, lang=lang)
    body["ocr"]["patient"]["name"] = "<img src=x onerror=alert(9)>"
    # 레지스트리에 없는 항원명(자유 기재 값) — 밑줄이 있어 리포트에는 md_text 로 바뀐 표기가 들어간다
    body["ocr"]["results"][0].update(allergen_name="<svg onload=alert(8)>_dust",
                                     korean_name="<svg onload=alert(8)>_먼지")
    return body


class TestHostileTranslatorReportPath:
    @pytest.mark.parametrize("lang", ["en", "zh"])
    @pytest.mark.parametrize("payload", _INJECTIONS)
    def test_report_outputs_carry_nothing_executable(self, client, smtp, monkeypatch, lang, payload):
        monkeypatch.setattr(settings, "i18n_background", False)
        baseline = client.post("/api/classify", json=_hostile_body(client, "ko")).json()
        assert _active(baseline["report_html"]) == []
        llm = FakeLLM(monkeypatch, reply=_hostile(payload))
        out = client.post("/api/classify", json=_hostile_body(client, lang)).json()
        assert llm.sent, "번역기가 불리지 않았다 — 이 테스트가 번역 경로를 지나지 않는다"
        assert out["lang"] == lang
        assert _active(out["report_html"]) == [], payload
        # 인쇄용 문서는 템플릿이 넣는 것(있다면)만 남는다 — 한국어 문서와 같아야 한다
        assert _active(out["report_document_html"]) == _active(baseline["report_document_html"]), payload
        # 저장분과 메일 첨부도 같은 문자열에서 나온다
        sid = out["session_id"]
        stored = client.get(f"/api/sessions/{sid}/outputs/{lang}")
        if stored.status_code == 200:
            assert _active(stored.json()["report_html"]) == []
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "a@b.co", "lang": lang, "include": ["report"]})
        if out["i18n"][lang] != "ready":          # 원문을 유지한 블록이 있으면 그 언어는 보내지 않는다
            assert r.status_code == 409 and not smtp.sent, r.text
            return
        assert r.status_code == 200, r.text
        pdf, part = list(smtp.sent[-1].iter_attachments())      # 리포트는 PDF 와 HTML 두 벌로 간다
        assert pdf.get_filename() == f"allergy-report-{lang}.pdf" and pdf.get_content()[:5] == b"%PDF-"
        attached = part.get_content()
        attached = attached.decode("utf-8") if isinstance(attached, bytes) else attached
        assert f"allergy-report-{lang}.html" == part.get_filename()
        assert _active(attached) == _active(baseline["report_document_html"]), payload

    def test_translate_markdown_keeps_source_links_and_plain_web_links(self, monkeypatch):
        from services.translation_service import TranslationService
        replies = {
            "[위키](https://en.wikipedia.org/wiki/Allergic_rhinitis) 참고": "See [wiki](https://en.wikipedia.org/wiki/Allergic_rhinitis)",
            "둘째 문단": "Second, see [site](https://example.org/a?b=1&c=2)",
            "셋째 {중괄호} 문단": "Third {braces} paragraph",
        }
        FakeLLM(monkeypatch, reply=lambda t: replies[t])
        out = TranslationService(api_key="").translate_markdown("\n\n".join(replies), "en").split("\n\n")
        assert out[:2] == list(replies.values())[:2]
        assert out[2] == "Third &#123;braces&#125; paragraph"       # 화면에는 중괄호 그대로 보인다


# ============================================================
# 1. 상담 추천 질문 — 면역치료 답에 문진(천식·면역치료 중) 문장이 들어간다
# ============================================================
MITE = {"indoor_timing": "yes", "mite_dust": "yes"}


def _immunotherapy_answer(client, screening):
    body = _payload(client, answers=MITE, screening=screening)
    body["ocr"]["results"] = [r for r in body["ocr"]["results"] if "farinae" in r["allergen_name"]]
    out = client.post("/api/chat", json=body).json()
    return next(s["answer"] for s in out["suggestions"] if s["key"] == "immunotherapy")


class TestChatSuggestionsUseScreening:
    def test_asthma_and_current_immunotherapy_lines(self, client):
        base = {"allergic_diseases": ["allergic_rhinitis"], "organ_systems": ["nasal"]}
        plain = _immunotherapy_answer(client, base)
        assert "면역치료가 가능한 항원은 1가지" in plain, "집먼지진드기가 후보로 잡혀야 이 테스트가 성립한다"
        assert "천식이 있다고 하셨습니다" not in plain and "이미 면역치료를 받고 있다고" not in plain
        full = _immunotherapy_answer(client, {"allergic_diseases": ["allergic_rhinitis", "asthma"],
                                              "organ_systems": ["nasal"],
                                              "current_medications": ["immunotherapy"]})
        assert "천식이 있다고 하셨습니다" in full and "이미 면역치료를 받고 있다고" in full


# ============================================================
# 5. FHIR 의 면역치료 메모 — 리포트가 면역치료를 보여 주는 항원에만 붙는다
# ============================================================
class TestFhirImmunotherapyNoteMatchesReport:
    IMT_NOTE = "면역치료(SCIT/SLIT) 고려 가능 대상"

    @staticmethod
    def _assessments(names):
        from models.schemas import AllergenResult, InterpretationType, OCRResult, PatientInfo, TestType
        from services.relevance_service import get_relevance_service
        ocr = OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="t"), results=[
            AllergenResult(index=i, raw_text=n, allergen_name=n, korean_name=k, value=17.6, unit="kU/L",
                           class_value=4, interpretation=InterpretationType.POSITIVE)
            for i, (n, k) in enumerate(names, 1)])
        return {a.allergen_name: a for a in get_relevance_service().build_assessments(ocr, None).assessments}

    @pytest.mark.parametrize("name,korean,relevance,answers,expected", [
        ("Dermatophagoides farinae", "집먼지진드기(D.farinae)", "clinically_relevant", {}, True),
        ("Dermatophagoides farinae", "집먼지진드기(D.farinae)", "sensitized_only", {}, False),
        ("Dermatophagoides farinae", "집먼지진드기(D.farinae)", "indeterminate", {}, False),
        ("Tyrophagus putrescentiae", "긴털가루진드기", "clinically_relevant", {}, False),
        ("Honey bee venom", "꿀벌독", "clinically_relevant", {"sting_reaction": "systemic"}, True),
        ("Honey bee venom", "꿀벌독", "clinically_relevant", {"sting_reaction": "local"}, False),
        ("Honey bee venom", "꿀벌독", "clinically_relevant", {}, False),
        ("Honey bee venom", "꿀벌독", "sensitized_only", {"sting_reaction": "systemic"}, False),
    ])
    def test_note_follows_the_report(self, name, korean, relevance, answers, expected):
        from models.schemas import ClinicalRelevance
        from services.fhir_service import FHIRService
        from services.knowledge_service import get_knowledge_service
        from services.report_service import get_report_service
        a = self._assessments([(name, korean)])[name]
        a.relevance = ClinicalRelevance(relevance)
        a.answers = answers
        res = FHIRService().build_allergy_intolerance_from_assessment(a, "p1", "t", "2026-10-06")
        note = " ".join(n["text"] for n in res.get("note", []))
        assert (self.IMT_NOTE in note) is expected
        # 리포트 쪽 기준: 면역치료 절의 후보(증상 확인 + 항원 단위 목록 + 병력 조건)와 항원 상세의 면역치료 줄
        in_section = bool(get_knowledge_service().immunotherapy_candidates([a]))
        in_detail = (relevance == "clinically_relevant"
                     and "💉 면역치료" in get_report_service()._allergen_detail_md(a, detailed=True))
        assert in_section is expected and in_detail is expected


# ============================================================
# 3·4. 항원 레지스트리 — 손으로 고친 별칭이 원본에도 있고, 대표 이름이 별칭에 가려지지 않는다
# ============================================================
class TestRegistryNames:
    @pytest.mark.parametrize("canonical", ["Cultivated oat", "Birch", "Ragweed"])
    def test_hand_edited_aliases_are_in_the_generator_source(self, canonical):
        """data/allergens.json 은 instructions/allergen_map_prompt_v2.json 에서 생성된다 — 원본이 달라지면
        다시 생성할 때 고친 별칭이 되돌아간다(생성기는 같은 canonical_name 의 첫 항목만 쓴다)."""
        import json
        from config.settings import BASE_DIR
        src = json.loads((BASE_DIR / "instructions" / "allergen_map_prompt_v2.json").read_text(encoding="utf-8"))
        reg = json.loads((BASE_DIR / "data" / "allergens.json").read_text(encoding="utf-8"))
        source = next(e for e in src["entries"] if e["canonical_name"] == canonical)
        built = next(a for a in reg["antigens"] if a["canonical_name"] == canonical)
        assert source["aliases"] == built["aliases"]
        if canonical == "Cultivated oat":
            assert "oat" not in [x.lower() for x in built["aliases"]]

    def test_every_canonical_and_korean_name_resolves_to_its_own_entry(self):
        from services.crossreactivity_service import CrossreactivityService, _norm
        svc = CrossreactivityService()
        assert len(svc.antigens) > 100
        wrong = [(a["id"], field, a[field], svc.by_name[_norm(a[field])]["id"])
                 for a in svc.antigens for field in ("canonical_name", "korean_name")
                 if a.get(field) and svc.by_name[_norm(a[field])] is not a]
        assert wrong == []
        for a in svc.antigens:
            assert svc.find(a["canonical_name"]) is a
            if a.get("korean_name"):
                assert svc.find("", a["korean_name"]) is a
        assert svc.find("Oat")["category"] == "food" and svc.find("oat")["id"] == "oat"

    def test_names_claimed_by_two_entries_are_only_the_known_ones(self):
        """한 이름을 두 항목이 쓰는 경우(별칭 대 한글 이름). 어느 쪽이 맞는지는 임상 판단이라 여기서는
        목록만 고정한다 — 새 충돌이 생기면 이 테스트가 알려 준다."""
        from collections import defaultdict
        from services.crossreactivity_service import CrossreactivityService, _norm
        owners = defaultdict(set)
        for a in CrossreactivityService().antigens:
            for nm in [a.get("canonical_name"), a.get("korean_name")] + a.get("aliases", []):
                if nm:
                    owners[_norm(nm)].add(a["id"])
        assert {k: sorted(v) for k, v in owners.items() if len(v) > 1} == {
            "옥수수가루": ["cornflour", "maize_corn"],   # Cornflour 의 한글 이름 · Maize (corn) 의 별칭
        }

    def test_kong_resolves_to_soybean_not_bean(self):
        """'콩' 은 대두(Soybean)다 — 병원 SPT 표기도 '콩(Soy)'. Bean(콩류)은 이 이름을 쓰지 않는다."""
        from services.crossreactivity_service import CrossreactivityService
        from utils.allergen_mapper import AllergenMapper
        svc, mapper = CrossreactivityService(), AllergenMapper()
        assert svc.find("", "콩")["id"] == "soybean" and svc.find("콩")["id"] == "soybean"
        assert svc.find("Bean")["id"] == "bean" and svc.find("", "콩류")["id"] == "bean"
        assert mapper.find_allergen("콩").canonical_name == "Soybean"
        assert mapper.find_allergen("콩(Soy)").canonical_name == "Soybean"
        assert mapper.find_allergen("Bean").canonical_name == "Bean"
        assert mapper.get_coding("", "콩")["code"] == mapper.get_coding("Soybean")["code"] != mapper.get_coding("Bean")["code"]

    def test_an_earlier_alias_does_not_shadow_a_later_canonical_name(self, tmp_path, monkeypatch):
        import json
        import shutil
        from services import crossreactivity_service as crs
        shutil.copy(crs._DATA / "allergen_components.json", tmp_path / "allergen_components.json")
        (tmp_path / "allergens.json").write_text(json.dumps({"antigens": [
            {"id": "cultivated_oat", "canonical_name": "Cultivated oat", "korean_name": "귀리",
             "aliases": ["Cultivated oat", "oat", "귀리가루"], "category": "pollen_grass", "components": []},
            {"id": "oat", "canonical_name": "Oat", "korean_name": "귀리가루", "aliases": ["Oat"],
             "category": "food", "components": []},
        ]}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(crs, "_DATA", tmp_path)
        svc = crs.CrossreactivityService()
        assert svc.find("Oat")["id"] == "oat" and svc.find("oat")["id"] == "oat"
        assert svc.find("", "귀리가루")["id"] == "oat", "한글 이름도 앞 항목의 별칭에 가려지지 않는다"
        assert svc.find("Cultivated oat")["id"] == "cultivated_oat" and svc.find("", "귀리")["id"] == "cultivated_oat"
        assert svc._category_of("Oat") == "food"


# ============================================================
# 6·7. 카드뉴스 — 옆에 걸친 카드가 빈 틀로 보이지 않고, 클래식 카드는 넘친 내용이 잘리지 않는다
# ============================================================
def _deck_inputs():
    from test_exposure_immunotherapy_animal import _classified
    from models.schemas import ScreeningProfile
    scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
    res, _ = _classified(MITE, scr)
    return res, scr


class TestDeckNeighbourCards:
    def test_content_is_revealed_on_entering_the_viewport_not_only_when_active(self):
        from services.cardnews_service import get_cardnews_service
        res, scr = _deck_inputs()
        doc = get_cardnews_service().generate_html(res, {"name": "t"}, scr)
        style = doc[doc.find("<style"):doc.rfind("</style>")]
        script = doc[doc.rfind("<script"):doc.rfind("</script>")]
        # 방문(.seen)·가운데(.active) 여부로 내용을 숨기지 않는다 — 숨김은 '아직 화면에 들어오지 않음'(.in 없음)뿐
        assert ".card:not(.seen) > div > *" not in style and ".card:not(.active) > div > *" not in style
        assert ".js .card:not(.in) > div > * { opacity:0; }" in style
        assert ".js .card.in > div > * { animation:cn-in" in style, "등장 애니메이션은 그대로 둔다"
        # 화면에 걸친 카드마다 .in 을 붙이는 코드가, '가운데 카드가 그대로면 돌아간다'는 분기보다 앞에 있다
        reveal = script.find("c.classList.add('in')")
        assert 0 < reveal < script.find("if (best === cur) return;")
        assert "c.offsetLeft < right && c.offsetLeft + c.offsetWidth > left" in script
        # 스크립트 없이(.js 없음)·인쇄에서는 숨기는 규칙이 걸리지 않는다
        hiding = [ln for ln in style.splitlines() if "opacity:0" in ln and "> div > *" in ln]
        assert hiding and all(ln.strip().startswith(".js ") for ln in hiding)
        printing = style[style.find("@media print"):]
        assert ".js .card > div > * { opacity:1 !important; animation:none !important; }" in printing


class TestClassicCardsDoNotClip:
    def test_every_card_body_scrolls_inside_the_fixed_height_card(self):
        """카드는 높이 440px·overflow:hidden 이다. 본문이 스크롤되지 않는 카드(생활 수칙 등)는 길어지면
        아래쪽 수칙이 잘려 읽을 방법이 없었다."""
        from services.cardnews_classic import get_classic_cardnews_service
        res, scr = _deck_inputs()
        doc = get_classic_cardnews_service().generate_html(res, {"name": "t"}, scr)
        style = doc[doc.find("<style"):doc.find("</style>")]
        body_rule = re.search(r"\.card > div \{([^}]*)\}", style).group(1)
        assert "overflow-y:auto" in body_rule
