"""검사 대조·라텍스·약물 — 화면 담당이 /api/classify 로 재현한 결함의 회귀 테스트.

  ① 검사 대조(히스타민·생리식염수·양성/음성 대조)는 알러젠이 아니다: 판정·개수·문진·리포트·카드·상담·FHIR 어디에도
     알러젠으로 나오지 않고, 검사를 읽을 수 있는지(SPT)만 한 줄로 나온다.
  ② latex·drug·control 카테고리가 실제로 나온다. 행에 적힌 category 는 레지스트리에 없는 이름에만 따른다.
  ④ /api/chat 추천 질문마다 번역 상태가 붙는다(전체 집계의 모양은 그대로).
  ⑤ 클래식 카드뉴스의 스크립트는 환자·언어와 무관한 고정 문자열이고 CSP 해시에 들어 있다.

LLM 은 부르지 않는다(키 없음 또는 가짜 번역기). DB·번역 캐시는 임시 경로다.
"""
import json
import re

import pytest
from fastapi.testclient import TestClient

import server
from config.settings import settings
from test_security_review_fixes import FakeLLM

ORIGIN = {"Origin": "http://testserver"}
VALID = json.load(open("data/category_rules.json", encoding="utf-8"))["valid_categories"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_db_path", tmp_path / "app.sqlite3")
    monkeypatch.setattr(settings, "i18n_db_path", tmp_path / "localized.sqlite3")
    monkeypatch.setattr(settings, "storage_enabled", True)
    monkeypatch.setattr(settings, "i18n_background", False)
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "llm_backend", "openai")
    monkeypatch.setattr(settings, "ollama_base_url", "")
    monkeypatch.setattr(settings, "smtp_host", "")
    return TestClient(server.app, headers=ORIGIN)


def _row(i, name, **kw):
    return {"index": i, "raw_text": name, "allergen_name": name, **kw}


def _body(test_type, rows, patient=None, **extra):
    return {"ocr": {"test_type": test_type, "patient": {"name": "김환자", **(patient or {})}, "results": rows},
            "answers": {}, **extra}


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _text(html_text):
    body = html_text[html_text.find("<body"):]
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


# 대조가 들어오는 표기: 레지스트리 이름·별칭, 한국어·영어·중국어 결과지, OCR 변형
CONTROL_NAMES = [
    ("Histamine", "positive"), ("Positive control", "positive"), ("Histamine (wheal/flare)", "positive"),
    ("Histamine control", "positive"), ("양성대조", "positive"), ("양성 대조", "positive"), ("히스타민", "positive"),
    ("양성 대조용 0.1% 히스타민 용액", "positive"), ("阳性对照", "positive"), ("组胺", "positive"),
    ("Negative control", "negative"), ("Saline", "negative"), ("음성대조", "negative"), ("음성 대조", "negative"),
    ("생리식염수", "negative"), ("음성 대조용 생리식염수", "negative"), ("阴性对照", "negative"), ("生理盐水", "negative"),
    ("Control", "unspecified"), ("Contro1", "unspecified"), ("대조", "unspecified"), ("对照", "unspecified"),
]


# ---------------------------------------------------------------------------
# ① 검사 대조
# ---------------------------------------------------------------------------
class TestControlsAreNotAllergens:
    @pytest.mark.parametrize("name,kind", CONTROL_NAMES)
    def test_control_row_never_becomes_an_allergen(self, client, name, kind):
        rows = [_row(1, name, mean_mm=6, size_text="6x6"), _row(2, "Birch", mean_mm=5)]
        body = _body("SPT", rows)
        out = _post(client, "/api/classify", body)

        assert [a["allergen_name"] for a in out["assessments"]] == ["Birch"]
        assert out["summary"]["total_positive"] == 1
        assert sum(out["summary"]["counts"].values()) == 1
        assert [(c["name"], c["kind"], c["category"]) for c in out["controls"]] == [(name, kind, "control")]
        every_name = sum((out["summary"][k] for k in ("clinically_relevant", "sensitized_only", "indeterminate")), [])
        assert every_name == ["자작나무 꽃가루"]

        # 리포트·카드뉴스: 대조는 '검사 대조' 한 줄에만 나온다(알러젠 표·절·수칙에 없다)
        for ui in ("quest", "classic"):
            deck = _text(_post(client, "/api/classify", dict(body, ui=ui))["cardnews_html"])
            assert deck.count("검사 대조:") == 1
            assert "히스타민 용액" not in deck and "생리식염수" not in deck.replace("음성 대조(생리식염수)", "")
        md = out["report_markdown"]
        assert md.count("검사 대조:") == 1 and "총 **1개** 항목에 양성" in md
        table = [ln for ln in md.split("\n") if ln.startswith("| ")]
        assert not any(name in ln or "대조" in ln for ln in table), "요약 표에 대조가 알러젠으로 들어갔다"
        assert "검사 대조:" in _text(out["report_document_html"])

        # 문진: 대조에 대해 묻지 않는다
        q = _post(client, "/api/questionnaire", {"ocr": body["ocr"]})
        assert q["positive_count"] == 1 and len(q["assessments"]) == 1
        assert [v["name"] for v in q["questionnaire"]["allergen_index"].values()] == ["Birch"]
        assert name not in json.dumps(q["questionnaire"], ensure_ascii=False)
        assert "other" not in [s["id"] for s in q["questionnaire"]["sections"]]
        assert q["controls"] == out["controls"]

        # 상담: 추천 답변이 대조를 '관찰 필요' 항목으로 말하지 않는다
        chat = _post(client, "/api/chat", dict(body, messages=[]))
        answers = "\n".join(s["answer"] or "" for s in chat["suggestions"])
        assert name not in answers and "히스타민" not in answers and "생리식염수" not in answers

        # FHIR: 알레르기가 아니고, 알러젠 검사 Observation 도 아니다
        fhir = _post(client, "/api/fhir", body)
        ai = [e["resource"]["code"]["text"] for e in fhir["allergy_intolerance_bundle"]["entry"]]
        obs = [e["resource"]["code"]["text"] for e in fhir["observation_bundle"]["entry"]]
        assert len(ai) == 1 and ai[0].startswith("Birch")
        assert obs == ["Prick test - Birch"]

    @pytest.mark.parametrize("name,kind", CONTROL_NAMES)
    def test_negative_reading_control_is_not_a_tested_negative_allergen(self, client, name, kind):
        from models.schemas import OCRResult
        from services.relevance_service import get_relevance_service
        ocr = OCRResult(**_body("SPT", [_row(1, name, mean_mm=0), _row(2, "Cat dander", mean_mm=0)])["ocr"])
        res = get_relevance_service().build_assessments(ocr, None)
        assert [n.allergen_name for n in res.tested_negatives] == ["Cat dander"]
        assert [c.kind for c in res.controls] == [kind]

    def test_explicit_control_category_cannot_hide_a_registry_allergen(self, client):
        """행에 category=control 을 적어도 레지스트리의 알러젠은 판정에서 빠지지 않는다."""
        out = _post(client, "/api/classify", _body("MAST", [
            _row(1, "Birch", value=5.0, category="control", **{"class": 3}),
            _row(2, "QC strip", value=1.0, category="control", **{"class": 2})]))
        assert [(a["allergen_name"], a["category"]) for a in out["assessments"]] == [("Birch", "pollen_tree")]
        assert [(c["name"], c["kind"]) for c in out["controls"]] == [("QC strip", "unspecified")]

    def test_autocomplete_offers_controls_as_controls(self, client):
        for q, expected in (("hist", "Histamine"), ("control", "Control"), ("대조", "Control"), ("히스타민", "Histamine"),
                            ("saline", "Control"), ("阳性对照", "Histamine"), ("组胺", "Histamine")):
            items = client.get("/api/allergens/search", params={"q": q}).json()["items"]
            hit = {i["canonical_name"]: i["category"] for i in items}
            assert hit.get(expected) == "control", (q, hit)
        cats = {i["canonical_name"]: i["category"] for i in client.get("/api/allergens").json()["items"]}
        assert cats["Histamine"] == cats["Control"] == "control"
        for name in ("Histamine", "Positive control", "음성대조", "阴性对照"):
            assert client.get("/api/allergen", params={"name": name}).json()["category"] == "control"

    def test_legacy_symptom_feedback_bundle_skips_controls(self):
        from models.schemas import SymptomFeedback
        from services.fhir_service import FHIRService
        fb = SymptomFeedback(patient_id="p", test_date="2026-10-01",
                             exposure_feedback={"symptomatic": ["Histamine", "Negative control", "Birch"]})
        bundle = FHIRService().create_allergy_intolerance_bundle(fb)
        assert [e["resource"]["code"]["text"].split(" ")[0] for e in bundle["entry"]] == ["Birch"]


class TestControlValidityLine:
    ROWS = [_row(3, "Dermatophagoides farinae", mean_mm=7, size_text="7x7"), _row(4, "Birch", mean_mm=5)]

    def _check(self, client, controls, patient=None):
        out = _post(client, "/api/classify", _body("SPT", controls + self.ROWS, patient))
        assert out["summary"]["total_positive"] == 2
        return out["control_check"], out

    def test_valid_test_says_so_plainly(self, client):
        check, out = self._check(client, [_row(1, "Histamine", mean_mm=6), _row(2, "Negative control", mean_mm=0)])
        assert check["status"] == "ok"
        assert check["line_ko"] == "검사 대조: 양성 대조 반응 확인(6mm) / 음성 대조 음성(0mm)."
        assert "- 🧪 검사 대조: 양성 대조 반응 확인(6mm) / 음성 대조 음성(0mm)." in out["report_markdown"]
        assert "🧪 검사 대조: 양성 대조 반응 확인(6mm)" in _text(out["cardnews_html"])

    def test_same_line_when_controls_come_from_the_patient_block(self, client):
        """OCR 은 SPT 대조를 결과 행이 아니라 환자 정보 칸(histamine_mean_mm 등)으로 돌려준다."""
        check, out = self._check(client, [], {"histamine_mean_mm": 6, "negative_control_mean_mm": 0})
        assert check["status"] == "ok" and check["line_ko"].startswith("검사 대조: 양성 대조 반응 확인(6mm)")
        assert [c["kind"] for c in out["controls"]] == ["positive", "negative"]

    def test_weak_positive_control_is_a_caution(self, client):
        check, out = self._check(client, [_row(1, "Histamine", mean_mm=2), _row(2, "Saline", mean_mm=0)])
        assert check["status"] == "caution"
        assert "양성 대조(히스타민) 반응이 약하거나 없습니다(2mm)" in check["line_ko"]
        assert "항히스타민제" in check["line_ko"] and check["line_ko"].endswith("검사 해석에 주의, 진료에서 확인하세요.")
        assert "- ⚠️ 검사 대조:" in out["report_markdown"]

    def test_reactive_negative_control_is_a_caution(self, client):
        check, _ = self._check(client, [_row(1, "Histamine", mean_mm=6), _row(2, "Saline", mean_mm=4)])
        assert check["status"] == "caution"
        assert "음성 대조에도 반응이 있습니다(4mm)" in check["line_ko"] and "피부묘기증" in check["line_ko"]
        assert "양성 대조 반응 확인(6mm)" in check["line_ko"]

    def test_missing_positive_control_is_a_caution(self, client):
        check, _ = self._check(client, [])
        assert check["status"] == "caution" and "양성 대조(히스타민) 값이 기록되지 않았습니다" in check["line_ko"]

    def test_thresholds_are_the_ones_allergen_rows_already_use(self):
        """새 기준을 만들지 않는다 — 3mm 이상 양성, 2mm 미만 음성(RelevanceService.result_status)."""
        from models.schemas import TestControl, TestType
        from services.relevance_service import RelevanceService

        def status(pos_mm, neg_mm):
            from models.schemas import AllergenResult
            mk = lambda kind, mm: TestControl(kind=kind, name=kind, value=mm, unit="mm", status=RelevanceService.result_status(
                AllergenResult(index=0, raw_text="", allergen_name=kind, mean_mm=mm), TestType.SPT))
            return RelevanceService.control_check([mk("positive", pos_mm), mk("negative", neg_mm)], TestType.SPT)["status"]
        assert status(3.0, 1.9) == "ok"
        assert status(2.9, 0) == "caution" and status(3.0, 2.0) == "caution" and status(3.0, 3.0) == "caution"

    def test_histamine_row_still_feeds_the_ah_ratio_component(self, client):
        body = _body("SPT", [_row(1, "Histamine", mean_mm=5)] + self.ROWS)
        fhir = _post(client, "/api/fhir", body)
        obs = fhir["observation_bundle"]["entry"]
        assert [e["resource"]["code"]["text"] for e in obs] == ["Prick test - Dermatophagoides farinae",
                                                                 "Prick test - Birch"]
        ratios = [c["valueQuantity"]["value"] for e in obs for c in e["resource"]["component"]
                  if "A/H" in c["code"]["text"]]
        assert ratios == [1.4, 1.0]

    def test_mast_control_row_is_shown_without_a_verdict(self, client):
        out = _post(client, "/api/classify", _body("MAST", [
            _row(1, "Positive control", **{"class": 4}), _row(2, "Birch", value=5.0, **{"class": 3})]))
        check = out["control_check"]
        assert check["status"] == "shown" and "양성 대조 class 4" in check["line_ko"]
        assert "히스타민" not in check["line_ko"] and "주의" not in check["line_ko"]
        assert out["summary"]["total_positive"] == 1
        assert _post(client, "/api/classify", _body("MAST", [_row(1, "Birch", value=5.0)]))["control_check"] is None


# ---------------------------------------------------------------------------
# ② latex · drug · control 카테고리
# ---------------------------------------------------------------------------
class TestCategoriesAreEmitted:
    def _cats(self, client, rows):
        out = _post(client, "/api/classify", _body("MAST", rows))
        return {a["allergen_name"]: a["category"] for a in out["assessments"]}, out

    def test_registry_latex_and_drugs(self, client):
        cats, _ = self._cats(client, [
            _row(1, "Latex", value=5.0), _row(2, "라텍스", value=5.0), _row(3, "Penicillin", value=5.0),
            _row(4, "Penicillin G", value=5.0), _row(5, "Amoxicillin", value=5.0), _row(6, "세파클러", value=5.0),
            _row(7, "Ampicilloyl", value=5.0), _row(8, "青霉素", value=5.0), _row(9, "Penicillium", value=5.0)])
        assert cats == {"Latex": "latex", "라텍스": "latex", "Penicillin": "drug", "Penicillin G": "drug",
                        "Amoxicillin": "drug", "세파클러": "drug", "Ampicilloyl": "drug", "青霉素": "drug",
                        "Penicillium": "mold"}

    def test_drug_is_decided_by_registry_membership_not_by_name_guessing(self, client):
        """레지스트리에 없는 약 이름은 이름만으로 drug 가 되지 않는다. 행에 drug 로 적혀 있으면 그대로 따른다."""
        cats, _ = self._cats(client, [_row(1, "Ibuprofen", value=5.0), _row(2, "Ketoprofen", value=5.0, category="drug"),
                                      _row(3, "Cefuroxime", value=5.0, category="Drug")])
        assert cats == {"Ibuprofen": "other", "Ketoprofen": "drug", "Cefuroxime": "drug"}

    def test_explicit_category_rule(self, client):
        """행의 category: 레지스트리 항원이면 무시(레지스트리가 기준), 레지스트리에 없으면 유효한 값만 따른다."""
        cats, _ = self._cats(client, [
            _row(1, "Latex", value=5.0, category="food"),           # 레지스트리 → latex
            _row(2, "Birch", value=5.0, category="drug"),           # 레지스트리 → pollen_tree
            _row(3, "Amoxicillin", value=5.0, category="other"),    # 레지스트리 → drug
            _row(4, "Rubber glove extract", value=5.0, category="latex"),   # 모르는 이름 + 유효한 값
            _row(5, "Mystery", value=5.0, category="not-a-category"),       # 유효하지 않은 값
            _row(6, "Hevea brasiliensis", value=5.0)])                       # 이름 규칙(라텍스)
        assert cats == {"Latex": "latex", "Birch": "pollen_tree", "Amoxicillin": "drug",
                        "Rubber glove extract": "latex", "Mystery": "other", "Hevea brasiliensis": "latex"}

    def test_every_valid_category_has_a_stamp_and_emoji_in_both_decks(self):
        from services import cardnews_classic, cardnews_service
        for cat in VALID:
            assert cat in cardnews_service._CATEGORY_STAMP_SVG, cat
            assert cat in cardnews_service._CATEGORY_EMOJI and cat in cardnews_classic._CATEGORY_EMOJI, cat
        stamps = cardnews_service._CATEGORY_STAMP_SVG
        assert len({stamps[c] for c in ("latex", "drug", "control", "venom", "other")}) == 5, "기타 스탬프로 뭉치지 않는다"
        assert cardnews_service._CATEGORY_EMOJI["latex"] == cardnews_classic._CATEGORY_EMOJI["latex"] == "🧤"
        assert cardnews_service._CATEGORY_EMOJI["drug"] == cardnews_classic._CATEGORY_EMOJI["drug"] == "💊"

    def test_normalize_category_keeps_the_three(self):
        from services.knowledge_service import normalize_category
        assert [normalize_category(c) for c in ("Latex", "drug", "Control", "Drug", "mixture")] == \
            ["latex", "drug", "control", "drug", "other"]
        assert {normalize_category(c) for c in VALID} == set(VALID)


class TestLatex:
    BODY = dict(screening={"allergic_diseases": ["allergic_rhinitis"], "organ_systems": ["nasal"],
                           "residence_country": "KR"})

    def test_latex_is_a_contact_antigen_not_a_seasonal_inhalant(self, client):
        body = _body("MAST", [_row(1, "Latex", value=5.2, **{"class": 3}), _row(2, "Birch", value=5.0, **{"class": 3})],
                     answers={"other_symptom__agn0": ["skin"], "pollen_season__spring_tree": "yes"}, **self.BODY)
        out = _post(client, "/api/classify", body)
        latex = out["assessments"][0]
        assert latex["category"] == "latex" and latex["relevance"] == "clinically_relevant"
        assert latex["season_label_ko"] == "" and "접촉" in latex["rationale_ko"]
        assert "고무장갑" in latex["exposure_environment_ko"] and "의료기기" in latex["exposure_environment_ko"]
        assert "라텍스-과일 증후군" in latex["cross_reactivity_ko"]
        md = out["report_markdown"]
        assert "### 🧤 접촉 항원(라텍스)" in md and "라텍스 제품 접촉 줄이기" in md
        season = md[md.index("## 3️⃣"):]
        season_rows = [ln for ln in season.split("\n") if ln.startswith("|")]
        assert season_rows and not any("라텍스" in ln for ln in season_rows), "계절 달력에 라텍스가 들어갔다"
        # 흡입 알러젠과 질환을 잇는 문장(비염 ← 꽃가루)에 라텍스를 넣지 않는다
        link = md[md.index("노출 → 증상 → 질환"):md.index("우선 실천 회피 수칙")]
        latex_block = link[link.index("**라텍스**"):]
        assert "닿을 때" in latex_block.split("**", 3)[2] and "알레르기 비염" not in latex_block.split("\n\n")[0]

    def test_sensitized_latex_gets_contact_wording_not_inhalant_prevention(self, client):
        body = _body("MAST", [_row(1, "Latex", value=5.2, **{"class": 3})],
                     answers={"other_symptom__agn0": ["none"]}, **self.BODY)
        out = _post(client, "/api/classify", body)
        assert out["assessments"][0]["relevance"] == "sensitized_only"
        md = out["report_markdown"]
        block = md[md.index("### 🌱 예방과 관찰"):md.index("## 3️⃣")]
        assert "**🧤 라텍스**" in block and "라텍스 제품에 닿은 뒤" in block
        for inhalant_only in ("부담이 적은", "침구", "환기", "습도"):
            assert inhalant_only not in block, inhalant_only

    def test_latex_question_asks_about_contact(self, client):
        q = _post(client, "/api/questionnaire", {"ocr": _body("MAST", [_row(1, "Latex", value=5.2)])["ocr"]})
        section = next(s for s in q["questionnaire"]["sections"] if s["id"] == "other")
        first = section["questions"][0]
        assert "고무장갑" in first["title"] and "드셨을 때" not in first["title"]
        assert [o["value"] for o in first["options"]] == ["skin", "oral", "breathing", "anaphylaxis", "none", "never"]
        assert "자동 분류" not in section["subtitle"] + first["help"]


class TestDrug:
    ROWS = [_row(1, "Amoxicillin", value=1.2, **{"class": 2}), _row(2, "Penicillin", value=0.9, **{"class": 2}),
            _row(3, "Cefaclor", value=4.0, **{"class": 3})]
    ANSWERS = {"other_symptom__agn0": ["skin", "breathing"], "other_symptom__agn1": ["none"]}

    def _out(self, client, **extra):
        return _post(client, "/api/classify", _body("MAST", self.ROWS, answers=self.ANSWERS, **extra))

    def test_positive_drug_ige_alone_is_not_a_drug_allergy(self, client):
        """약물은 반응 병력이 있든(아목시실린) 없든(페니실린) 모르든(세파클러) 이 앱이 판정하지 않는다.
        relevance 는 기존 세 값 가운데 indeterminate 로, 정확한 판정은 verdict='clinician_review' 로 나간다 —
        'clinically_relevant'(진범 확정)도 'sensitized_only'(무혐의)도 아니다."""
        out = self._out(client)
        by = {a["allergen_name"]: a for a in out["assessments"]}
        for n in ("Amoxicillin", "Penicillin", "Cefaclor"):
            assert (by[n]["relevance"], by[n]["verdict"], by[n]["verdict_label_ko"]) == \
                ("indeterminate", "clinician_review", "진료 확인 필요"), n
        s = out["summary"]
        assert s["counts"] == {"clinically_relevant": 0, "sensitized_only": 0, "indeterminate": 3}
        assert s["verdict_counts"] == {"clinically_relevant": 0, "sensitized_only": 0, "indeterminate": 0,
                                       "clinician_review": 3}
        assert s["clinician_review"] == ["아목시실린", "페니실린 G", "세파클러"] and s["clinically_relevant"] == []
        for name in ("Penicillin", "Cefaclor"):
            assert "검사 양성만으로 약물 알레르기라고 하지 않습니다" in by[name]["rationale_ko"]
        assert "진료에서 정합니다" in by["Amoxicillin"]["rationale_ko"]
        assert all(a["avoidance_control_ko"] == [] and a["season_label_ko"] == "" for a in out["assessments"])

    def test_tolerated_drug_wording_without_a_same_class_reaction(self, client):
        """같은 계열에 반응이 없을 때의 문장은 그대로다: 스스로 끊거나 피하지 말고 진료에서 확인."""
        out = _post(client, "/api/classify", _body("MAST", self.ROWS[1:2], answers={"other_symptom__agn0": ["none"]}))
        a = out["assessments"][0]
        assert a["verdict"] == "clinician_review"
        assert "스스로 끊거나 피하지 말고, 진료에서 확인하세요" in a["rationale_ko"]

    def test_same_class_reaction_changes_the_advice_for_related_drugs(self, client):
        """아목시실린에 반응이 있었다고 답한 환자의 페니실린·세파클러(같은 베타락탐 계열)에는
        '스스로 끊거나 피하지 마세요'라고 하지 않고, 관련 약은 쓰기 전에 진료에서 상의하라고 안내한다."""
        out = self._out(client)
        by = {a["allergen_name"]: a for a in out["assessments"]}
        for name in ("Penicillin", "Cefaclor"):
            text = by[name]["rationale_ko"]
            assert "피하지 말고" not in text and "피하지 마세요" not in text, name
            assert "같은 계열(베타락탐계 항생제(페니실린·세팔로스포린 계열))인 아목시실린에 반응이 있었다고 답하셨습니다" in text
            assert "관련 약은 쓰기 전에 진료에서 상의하세요" in text
            assert "약을 스스로 끊거나 다시 쓰지 말고, 어떻게 할지는 진료에서 정합니다" in text
        for surface in (out["report_markdown"], _text(out["cardnews_html"]),
                        _text(self._out(client, ui="classic")["cardnews_html"])):
            assert "피하지 마세요" not in surface and "피하지 말고" not in surface
            assert "관련 약은 쓰기 전에 진료에서 상의하세요" in surface

    @pytest.mark.parametrize("ui", ["quest", "classic"])
    def test_no_avoidance_tips_or_immunotherapy_for_drugs(self, client, ui):
        out = self._out(client, ui=ui)
        md = out["report_markdown"]
        assert "## 💊 🩺 진료 확인이 필요한 약물" in md
        assert "지금 꼭 지켜야 할 회피 수칙은 없습니다" in md and "우선 실천 회피 수칙" not in md
        block = md[md.index("## 💊 🩺 진료 확인이 필요한 약물"):md.index("## 2️⃣")]
        assert all(f"**{name}**" in block for name in ("아목시실린", "페니실린 G", "세파클러"))
        assert "**지금 할 일**" not in block and "노출될 때 증상이 실제로 나타나는 알러젠" not in block
        assert "이 약을 계속 피할지, 다시 써도 되는지는 진료에서 정합니다" in block
        assert "스스로 끊거나 다시 쓰지 마세요" in block
        assert "면역치료" not in block and "## 💉" not in md
        # 약물은 '실제 주의'·'감작만'·'관찰 필요' 어느 절에도 들어가지 않는다
        for name in ("아목시실린", "페니실린 G", "세파클러"):
            assert name not in md[md.index("## 1️⃣"):md.index("## 💊 🩺")], name
            assert name not in md[md.index("## 2️⃣"):md.index("## 3️⃣")], name
        assert "### 🌱 예방과 관찰" not in md and "## 🟡" not in md
        deck = _text(out["cardnews_html"])
        assert "💊 알러젠 알아보기" not in deck and "이렇게 관리하세요" not in deck
        assert "회피가 기본" not in deck and "치료와 연결하기" not in deck
        assert "면역치료를 진료에서 상의" not in deck

    def test_reported_drug_allergy_is_tied_to_the_result(self, client):
        plain = self._out(client)
        tied = self._out(client, screening={"allergic_diseases": ["drug_allergy"]})
        note = "문진에서 약물 알레르기 병력을 알려 주셨습니다"
        assert all(note not in a["rationale_ko"] for a in plain["assessments"])
        assert all(note in a["rationale_ko"] for a in tied["assessments"])

    def test_drug_question_asks_about_reaction_history(self, client):
        q = _post(client, "/api/questionnaire", {"ocr": _body("MAST", self.ROWS)["ocr"]})
        section = next(s for s in q["questionnaire"]["sections"] if s["id"] == "other")
        first = section["questions"][0]
        assert "먹거나 주사로 맞은 뒤" in first["title"] and "검사 양성만으로 약물 알레르기라고 하지 않아요" in first["help"]
        assert [o["value"] for o in first["options"]] == ["skin", "oral", "breathing", "anaphylaxis", "none", "never"]

    def test_fhir_marks_drugs_as_unconfirmed_medication(self, client):
        fhir = _post(client, "/api/fhir", _body("MAST", self.ROWS, answers=self.ANSWERS))
        rows = [(e["resource"]["code"]["text"].split(" ")[0], e["resource"]["category"],
                 e["resource"]["verificationStatus"]["coding"][0]["code"], e["resource"]["code"]["coding"])
                for e in fhir["allergy_intolerance_bundle"]["entry"]]
        assert rows == [("Amoxicillin", ["medication"], "unconfirmed", []),
                        ("Penicillin", ["medication"], "unconfirmed", []),
                        ("Cefaclor", ["medication"], "unconfirmed", [])], "곰팡이(Penicillium) 코드를 빌려 쓰지 않는다"
        note = fhir["allergy_intolerance_bundle"]["entry"][0]["resource"]["note"][0]["text"]
        assert "회피·재투여는 진료에서 판단" in note and "회피·관리:" not in note


# ---------------------------------------------------------------------------
# ④ /api/chat — 추천 질문별 번역 상태
# ---------------------------------------------------------------------------
class TestSuggestionTranslationFlags:
    ROWS = [_row(1, "Dermatophagoides farinae", value=17.6, **{"class": 4}), _row(2, "Birch", value=6.0, **{"class": 3}),
            _row(3, "Zzunknownium", value=2.0, **{"class": 2})]
    ANSWERS = {"mite_dust": "yes"}

    def _chat(self, client, lang):
        return _post(client, "/api/chat", _body("MAST", self.ROWS, answers=self.ANSWERS, lang=lang, messages=[]))

    def test_korean_is_all_zero_and_the_whole_response_shape_is_unchanged(self, client):
        chat = self._chat(client, "ko")
        assert chat["translation"] == {"segments": 0, "untranslated": 0, "errors": 0}
        assert chat["suggestions"]
        for s in chat["suggestions"]:
            assert set(s) == {"key", "text", "answer", "translation"}
            assert s["translation"] == {"segments": 0, "untranslated": 0}

    def test_without_a_backend_each_untranslated_answer_is_flagged(self, client):
        chat = self._chat(client, "en")
        assert set(chat["translation"]) == {"segments", "untranslated", "errors"}
        assert chat["translation"]["untranslated"] > 0
        hangul = re.compile(r"[가-힣]")
        for s in chat["suggestions"]:
            tr = s["translation"]
            assert tr["untranslated"] <= tr["segments"]
            has_korean = bool(hangul.search(f"{s['text']}\n{s['answer'] or ''}"))
            assert bool(tr["untranslated"]) == has_korean, s["key"]
        assert any(s["translation"]["untranslated"] for s in chat["suggestions"])

    def test_only_the_answer_holding_the_failed_fragment_is_flagged(self, client, monkeypatch):
        """번역기가 한 조각만 놓치면, 그 조각이 들어간 답변만 표시된다."""
        rationale = "봄철 나무 꽃가루 시기"        # 자작나무 판정 근거(관찰 필요 답변에는 이름만 들어간다)
        FakeLLM(monkeypatch, fail=lambda text: "침구" in text)
        chat = self._chat(client, "en")
        flagged = {s["key"] for s in chat["suggestions"] if s["translation"]["untranslated"]}
        clean = {s["key"] for s in chat["suggestions"] if not s["translation"]["untranslated"]}
        assert flagged and "why_relevant" in flagged, flagged
        assert "indeterminate" in clean and "immunotherapy" in clean, (flagged, clean)
        by = {s["key"]: s for s in chat["suggestions"]}
        assert "침구" in by["why_relevant"]["answer"] and rationale not in by["indeterminate"]["answer"]
        assert not re.search(r"[가-힣]", by["indeterminate"]["answer"])
        total = chat["translation"]
        assert 0 < total["untranslated"] < total["segments"]

    def test_fully_translated_response_flags_nothing(self, client, monkeypatch):
        FakeLLM(monkeypatch)
        chat = self._chat(client, "en")
        assert chat["translation"]["untranslated"] == 0
        assert all(s["translation"]["untranslated"] == 0 for s in chat["suggestions"])
        assert any(s["translation"]["segments"] > 0 for s in chat["suggestions"])


# ---------------------------------------------------------------------------
# ⑤ 클래식 카드뉴스의 스크립트 — 고정 문자열, CSP 해시
# ---------------------------------------------------------------------------
class TestClassicDeckScript:
    HOSTILE = "</script><script>alert(1)</script>\"'`${x}<img src=x onerror=alert(2)>"

    def _deck(self, client, ui, lang="ko", hostile=False):
        body = {"ocr": client.get("/api/ocr/demo").json(), "answers": {"mite_dust": "yes"}, "ui": ui, "lang": lang,
                "screening": {"allergic_diseases": ["allergic_rhinitis"]}}
        if hostile:
            h = self.HOSTILE
            body["ocr"]["patient"].update(name=h, facility=h, ordering_provider=h)
            for r in body["ocr"]["results"]:
                r.update(allergen_name=f"{r['allergen_name']} {h}", korean_name=f"{r['korean_name']} {h}", note=h)
            body["ocr"]["results"].append(_row(9, h, value=5.0, korean_name=h, value_text=h, size_text=h))
            body["ocr"]["results"].append(_row(10, "Histamine " + h, value=5.0))
            body["screening"] = {"allergic_diseases": ["allergic_rhinitis", h], "disease_other": h,
                                 "medication_note": h, "triggers_free_text": h, "pets_other": h, "notes": h,
                                 "oas_foods": [h], "current_medications": ["antihistamine"]}
            body["answers"] = {"mite_dust": "yes", "food_general_react": [h], h: h}
        return _post(client, "/api/classify", body)["cardnews_html"]

    @pytest.mark.parametrize("ui", ["quest", "classic"])
    def test_script_is_byte_identical_for_every_patient_and_language(self, client, monkeypatch, ui):
        from services.cardnews_classic import ClassicCardNewsService
        from services.cardnews_service import CardNewsService
        FakeLLM(monkeypatch)
        fixed = CardNewsService._CHECK_JS + (CardNewsService._DECK_JS if ui == "quest" else ClassicCardNewsService._SHELL_JS)
        assert "{" + "self" not in fixed and "alert(" not in fixed
        decks = [self._deck(client, ui), self._deck(client, ui, hostile=True),
                 self._deck(client, ui, "en"), self._deck(client, ui, "zh", hostile=True)]
        assert "alert(1)" in decks[1], "payload 가 카드에 닿지 않으면 이 테스트는 아무것도 검사하지 않는다"
        for deck in decks:
            bodies = server._INLINE_SCRIPT.findall(deck)
            assert bodies == [fixed], "스크립트는 하나이고 코드에 적힌 고정 문자열 그대로다"
            assert deck.count("<script") == 1 and "<script src" not in deck
            assert not re.search(r"<[a-z][^>]*\son[a-z]+\s*=", deck[deck.find("<body"):], re.I), "이벤트 속성"
        hashes = {tuple(server._script_hashes(d)) for d in decks}
        assert len(hashes) == 1 and len(next(iter(hashes))) == 1

    def test_classic_script_hash_is_in_the_page_csp(self, client):
        csp = client.get("/").headers["content-security-policy"]
        script_src = csp.split("script-src")[1].split(";")[0]
        assert "'unsafe-inline'" not in script_src
        classic = server._script_hashes(self._deck(client, "classic", hostile=True))
        quest = server._script_hashes(self._deck(client, "quest"))
        assert len(classic) == 1 and classic[0] in script_src
        assert classic != quest and quest[0] in script_src, "두 덱의 스크립트는 다르고 둘 다 허용돼 있다"
        classic_page = client.get("/classic/")
        if classic_page.status_code == 200:
            assert classic[0] in classic_page.headers["content-security-policy"]
        # 주입된 스크립트의 해시는 허용 목록에 없다
        import base64
        import hashlib
        injected = "'sha256-" + base64.b64encode(hashlib.sha256(b"alert(1)").digest()).decode() + "'"
        assert injected not in csp
