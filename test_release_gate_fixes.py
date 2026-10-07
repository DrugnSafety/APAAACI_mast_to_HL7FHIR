"""출시 점검에서 걸린 환자용 문구 모순과 후속 항목의 회귀 테스트 — 모두 /api/classify·/api/chat·/api/fhir 로 재현한다.

  B1 약물 항원은 '진범 확정'도 '무혐의'도 아닌 자기 판정('진료 확인 필요')을 어느 화면에서나 갖는다.
  B2 주증상 카드는 문진에서 확인된 증상 부위로 알러젠을 잇는다(라텍스 두드러기 ↔ 피부 증상 카드).
  B3 이미 답한 것을 다시 묻지 않는다(벌에 쏘인 자리만 부었다 → '벌에 쏘인 적이 있나요?'를 싣지 않는다).
  B4 응급 안내(아나필락시스·에피네프린·응급실·119)는 두 카드뉴스와 리포트에 똑같이 실린다.
  F1 출처 없는 단정을 싣지 않는다 / F2 이름이 닮은 다른 항원의 지식을 붙이지 않는다 /
  F3 벌독·말·소 항원 / F4 SPT 전용 주의는 SPT 에만 / F6 잘못된 answers 로 500 이 나지 않는다 /
  F5·F7 문구 / F8 인용.

LLM 은 부르지 않는다(키 없음). DB·번역 캐시는 임시 경로다.
"""
import html as html_lib
import json
import re

import pytest
from fastapi.testclient import TestClient

import server
from config.settings import settings
from services.category_resolver import resolve_category
from services.knowledge_service import get_knowledge_service
from utils.text_utils import josa, with_josa

ORIGIN = {"Origin": "http://testserver"}
REGISTRY = json.load(open("data/allergens.json", encoding="utf-8"))["antigens"]


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
    return TestClient(server.app, headers=ORIGIN, raise_server_exceptions=False)


def _row(i, name, value=5.0, cls=3, **kw):
    return {"index": i, "raw_text": name, "allergen_name": name, "value": value, "class": cls,
            "interpretation": "Positive", **kw}


def _body(rows, answers=None, screening=None, test_type="MAST", **extra):
    scr = {"allergic_diseases": [], "current_medications": [], "organ_systems": [], "pets": [], **(screening or {})}
    return {"ocr": {"test_type": test_type, "patient": {"name": "김환자", "test_date": "2026-06-15"}, "results": rows},
            "screening": scr, "answers": answers or {}, **extra}


def _post(client, path, body):
    r = client.post(path, json=body)
    assert r.status_code == 200, (r.status_code, r.text[:400])
    return r.json()


def _text(html_text):
    body = html_text[html_text.find("<body"):]
    body = re.sub(r"<(script|style).*?</\1>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", " ", body)))


def _surfaces(client, body):
    """(리포트 Markdown, 퀘스트 카드뉴스 글, 클래식 카드뉴스 글, 응답)."""
    quest = _post(client, "/api/classify", {**body, "ui": "quest"})
    classic = _post(client, "/api/classify", {**body, "ui": "classic"})
    return quest["report_markdown"], _text(quest["cardnews_html"]), _text(classic["cardnews_html"]), quest


def _chat(client, body):
    out = _post(client, "/api/chat", {**body, "messages": [{"role": "user", "content": "안녕하세요"}]})
    return {s["key"]: s for s in out["suggestions"]}


# 검증에 쓴 환자 (iii): 계란 감작만 · 벌독(쏘인 자리만) · 라텍스 두드러기 · 페니실린 G 반응 + 아목시실린 문제 없음
CASE3_ROWS = [_row(1, "Egg white", 2.0, 2), _row(2, "Honey bee venom", 4.0, 3), _row(3, "Latex", 6.0, 3),
              _row(4, "Penicillin G", 1.2, 2), _row(5, "Amoxicillin", 0.9, 2)]
CASE3_ANSWERS = {"symptom_pattern": "none", "worse_seasons": ["none"], "sting_reaction": "local",
                 "oral_allergy_syndrome": "no", "food_symptoms__agn0": ["none"], "food_systemic": "no",
                 "other_symptom__agn2": ["skin"], "severity__agn2": "mild",
                 "other_symptom__agn3": ["skin"], "severity__agn3": "severe", "other_symptom__agn4": ["none"]}
CASE3 = _body(CASE3_ROWS, CASE3_ANSWERS, {"organ_systems": ["skin"]})


# ---------------------------------------------------------------------------
# B1. 약물 항원 — 한 가지 판정 '진료 확인 필요'
# ---------------------------------------------------------------------------
class TestDrugVerdict:
    def test_api_payload_keeps_the_legacy_vocabulary_and_adds_verdict(self, client):
        out = _post(client, "/api/classify", CASE3)
        by = {a["allergen_name"]: a for a in out["assessments"]}
        for name in ("Penicillin G", "Amoxicillin"):
            assert by[name]["relevance"] == "indeterminate", "기존 소비자가 아는 값만 — 진범 확정도 무혐의도 아니다"
            assert by[name]["verdict"] == "clinician_review" and by[name]["verdict_label_ko"] == "진료 확인 필요"
        # 약물이 아닌 항원의 verdict 는 relevance 와 같다
        assert (by["Latex"]["relevance"], by["Latex"]["verdict"]) == ("clinically_relevant", "clinically_relevant")
        assert (by["Egg white"]["verdict"], by["Honey bee venom"]["verdict"]) == ("sensitized_only", "indeterminate")
        s = out["summary"]
        assert s["clinically_relevant"] == ["라텍스"] and s["sensitized_only"] == ["계란 흰자"]
        assert s["clinician_review"] == ["페니실린 G", "아목시실린"]
        assert s["verdict_counts"] == {"clinically_relevant": 1, "sensitized_only": 1, "indeterminate": 1,
                                       "clinician_review": 2}
        assert s["counts"] == {"clinically_relevant": 1, "sensitized_only": 1, "indeterminate": 3}
        assert sum(s["counts"].values()) == s["total_positive"] == 5

    def test_no_surface_calls_a_drug_confirmed_or_cleared(self, client):
        md, quest, classic, out = _surfaces(client, CASE3)
        # 리포트: 약물은 자기 줄·자기 절에만
        table = md[md.index("| 구분 |"):md.index("**🔴 지금 우선")]
        row = {line.split("|")[1].strip(): line for line in table.splitlines() if line.startswith("| ")}
        assert "페니실린" not in row["🔴 실제 주의"] and "아목시실린" not in row["⚪ 감작만"]
        assert "페니실린 G, 아목시실린" in row["🩺 진료 확인 필요"] and "| 2 |" in row["🩺 진료 확인 필요"]
        assert "## 💊 🩺 진료 확인이 필요한 약물" in md
        for a, b in (("## 1️⃣", "## 💊 🩺"), ("## 2️⃣", "## 🟡"), ("## 🟡", "## 3️⃣")):
            section = md[md.index(a):md.index(b)]
            assert "페니실린" not in section and "아목시실린" not in section, a
        assert "실제로 증상을 일으키는 것으로 확인된 알러젠은 1개" in md
        # 카드뉴스: '진범 확정'·'감작만' 카드에 약물이 없고, 표지에 따로 센다
        for deck, confirmed, cleared in ((quest, "진범 확정 증상으로 확인된", "무혐의 · 감작만 검사만 양성"),
                                         (classic, "🔴 실제 주의 증상을 유발하는", "⚪ 감작만 검사만 양성")):
            card = deck[deck.index(confirmed):]
            card = card[:card.index("알러젠 알아보기")]
            assert "페니실린" not in card and "아목시실린" not in card
            sens = deck[deck.index(cleared):]
            sens = sens[:sens.index("예방과 관찰")]
            assert "페니실린" not in sens and "아목시실린" not in sens
            assert "2 진료 확인 필요" in deck
            drug_card = deck[deck.index("약물은 진료에서 확인합니다"):deck.index(cleared)]
            assert "페니실린 G" in drug_card and "아목시실린" in drug_card
            assert "스스로 끊거나 다시 쓰지 마세요" in drug_card
            for banned in ("회피와 관리가 가장 중요", "과도한 회피", "진범", "무혐의"):
                assert banned not in drug_card, banned
        assert "1 진범 확정 1 무혐의·감작만 1 관찰 대상 2 진료 확인 필요" in quest
        assert "1 실제 주의 1 감작만 1 관찰 필요 2 진료 확인 필요" in classic
        # 인쇄용 문서의 표지 타일
        doc = _text(out["report_document_html"])
        assert "2 🩺 진료 확인 필요 약물 — 진료에서 결정" in doc and "1 🔴 실제 주의" in doc

    def test_same_class_reaction_changes_related_drug_advice(self, client):
        md, quest, classic, out = _surfaces(client, CASE3)
        amox = next(a for a in out["assessments"] if a["allergen_name"] == "Amoxicillin")["rationale_ko"]
        assert "페니실린 G에 반응이 있었다고 답하셨습니다" in amox and "쓰기 전에 진료에서 상의하세요" in amox
        for text in (amox, md, quest, classic):
            assert "피하지 마세요" not in text and "피하지 말고" not in text
            assert "관련 약은 쓰기 전에 진료에서 상의하세요" in text
        # 같은 계열에 반응이 없으면 예전 문장 그대로
        alone = _post(client, "/api/classify", _body([_row(1, "Amoxicillin", 0.9, 2)], {"other_symptom__agn0": ["none"]}))
        assert "이 결과만으로 약을 스스로 끊거나 피하지 말고, 진료에서 확인하세요" in alone["assessments"][0]["rationale_ko"]
        # 다른 계열(표에 없는 약)은 묶지 않는다
        from services.questionnaire_service import drug_class
        from models.schemas import AllergenAssessment
        assert drug_class(AllergenAssessment(allergen_name="Cefaclor", korean_name="세파클러"))[0] == "beta_lactam"
        assert drug_class(AllergenAssessment(allergen_name="Ibuprofen")) is None

    def test_chat_answers_drugs_separately(self, client):
        sug = _chat(client, CASE3)
        assert "약물 항원(페니실린 G, 아목시실린)은 ‘진료 확인 필요’입니다" in sug["why_relevant"]["answer"]
        assert "라텍스" in sug["why_relevant"]["answer"].split("약물 항원")[0]
        assert "페니실린" not in sug["why_relevant"]["answer"].split("약물 항원")[0]
        drug = sug["drug_review"]["answer"]
        assert "‘알레르기’라고도 ‘괜찮다’고도 판정하지 않습니다" in drug and "스스로 끊거나 다시 쓰지 마세요" in drug
        assert "페니실린" not in sug["sensitized_only"]["answer"] and "아목시실린" not in sug["sensitized_only"]["answer"]
        assert "페니실린" not in sug["indeterminate"]["answer"]
        # 약물만 양성인 환자: '연관된 알러젠이 없다'로 끝내지 않고 약물을 따로 말한다
        only = _chat(client, _body(CASE3_ROWS[3:4], {"other_symptom__agn0": ["skin"]}))
        assert "약물 항원(페니실린 G)은 ‘진료 확인 필요’" in only["why_relevant"]["answer"]
        # 상담 모델에게 주는 판정·규칙
        from services.result_chat_service import ResultChatService, _VERDICT_KO
        from models.schemas import ClinicalRelevance
        assert "진료 확인 필요" in _VERDICT_KO[ClinicalRelevance.CLINICIAN_REVIEW]
        assert "9d. DRUG ALLERGENS" in ResultChatService(api_key="")._system_prompt("ctx", "ko")

    def test_fhir_stays_unconfirmed_and_does_not_assert_criticality(self, client):
        for scr in ({}, {"allergic_diseases": ["anaphylaxis"], "organ_systems": ["systemic"]}):
            body = _body(CASE3_ROWS, {**CASE3_ANSWERS, "severity__agn3": "anaphylaxis"}, scr)
            entries = [e["resource"] for e in _post(client, "/api/fhir", body)["allergy_intolerance_bundle"]["entry"]]
            for name in ("Penicillin G", "Amoxicillin"):
                r = next(x for x in entries if x["code"]["text"].startswith(name))
                assert r["category"] == ["medication"]
                assert r["verificationStatus"]["coding"][0]["code"] == "unconfirmed"
                assert r["criticality"] == "unable-to-assess", "문진만으로 약물의 위험도를 단정하지 않는다"
                assert "판정: 진료 확인 필요" in r["note"][0]["text"]
            pen = next(x for x in entries if x["code"]["text"].startswith("Penicillin G"))
            assert pen["reaction"][0]["severity"] == "severe" and "환자가 문진에서 보고" in pen["reaction"][0]["description"]
            assert "reaction" not in next(x for x in entries if x["code"]["text"].startswith("Amoxicillin"))
            latex = next(x for x in entries if x["code"]["text"].startswith("Latex"))
            assert latex["verificationStatus"]["coding"][0]["code"] == "confirmed"

    def test_legacy_three_question_path_does_not_confirm_a_drug_either(self):
        from models.schemas import AllergenAssessment, ClinicalRelevance
        from services.relevance_service import get_relevance_service
        a = AllergenAssessment(allergen_name="Penicillin G", korean_name="페니실린 G", category="drug",
                               answers={"exposed": "yes", "symptom_on_exposure": "yes"})
        assert get_relevance_service().classify(a).relevance == ClinicalRelevance.CLINICIAN_REVIEW


# ---------------------------------------------------------------------------
# B2. 주증상 카드 ↔ 문진에서 확인된 증상 부위
# ---------------------------------------------------------------------------
UNLINKED = "이 증상과 연결된 알러젠은 확인되지 않았습니다"


class TestSymptomLinkage:
    def test_latex_urticaria_links_to_the_skin_card(self, client):
        md, quest, classic, _out = _surfaces(client, CASE3)
        for text in (md, quest, classic):
            assert UNLINKED not in text
            assert "이 증상과 이어질 수 있는 것: 라텍스." in text
            # 약물은 '연결된 알러젠'이 아니라 따로 적는다
            assert "페니실린 G 사용 뒤 이 부위에 반응이 있었다고 답하셨습니다. 약물 때문인지는 진료에서 확인합니다." in text
        assert "이 노출로 생기거나 심해질 수 있는 것(처음에 알려주신 질환·증상): 피부 증상(두드러기·가려움)" in md

    @pytest.mark.parametrize("organ,rows,answers,linked", [
        # 음식 두드러기 → 피부 / 음식의 입·목 증상만 → 피부에 잇지 않는다
        ("skin", [_row(1, "Egg white")], {"food_symptoms__agn0": ["skin"]}, "계란 흰자"),
        ("skin", [_row(1, "Egg white")], {"food_symptoms__agn0": ["oral"]}, None),
        ("gi", [_row(1, "Egg white")], {"food_symptoms__agn0": ["gi"]}, "계란 흰자"),
        ("gi", [_row(1, "Egg white")], {"food_symptoms__agn0": ["skin"]}, None),
        ("lower_airway", [_row(1, "Latex")], {"other_symptom__agn0": ["breathing"]}, "라텍스"),
        ("nasal", [_row(1, "Latex")], {"other_symptom__agn0": ["breathing"]}, "라텍스"),
        ("nasal", [_row(1, "Latex")], {"other_symptom__agn0": ["skin"]}, None),
        ("systemic", [_row(1, "Latex")], {"other_symptom__agn0": ["anaphylaxis"]}, "라텍스"),
        ("systemic", [_row(1, "Honey bee venom")], {"sting_reaction": "systemic"}, "꿀벌 독"),
        ("skin", [_row(1, "Honey bee venom")], {"sting_reaction": "systemic"}, None),
        # 흡입 알러젠은 여러 부위를 한 문장으로 묻는다 — 항원군의 기본 부위 그대로
        ("lower_airway", [_row(1, "Birch")], {"pollen_season__spring_tree": "yes"}, "자작나무"),
        ("skin", [_row(1, "Cat dander")], {"animal_contact__agn0": "live", "animal_worse__agn0": "yes"}, "고양이"),
        ("gi", [_row(1, "Cat dander")], {"animal_contact__agn0": "live", "animal_worse__agn0": "yes"}, None),
    ])
    def test_every_organ_card_follows_confirmed_sites(self, client, organ, rows, answers, linked):
        md, quest, classic, out = _surfaces(client, _body(rows, answers, {"organ_systems": [organ]}))
        assert out["summary"]["counts"]["clinically_relevant"] == 1
        for text in (md, quest, classic):
            if linked:
                assert UNLINKED not in text and re.search(rf"이어질 수 있는 것: [^.]*{linked}", text), text[-800:]
            else:
                assert UNLINKED in text

    def test_drug_only_reaction_is_not_called_non_allergic(self, client):
        body = _body(CASE3_ROWS[3:4], {"other_symptom__agn0": ["skin"]}, {"organ_systems": ["skin"]})
        md, quest, classic, _out = _surfaces(client, body)
        for text in (md, quest, classic):
            assert "알레르기가 아닌 원인일 수 있으니" not in text
            assert "약물 외에 이 증상과 연결된 알러젠은 이번 검사에서 확인되지 않았습니다." in text


# ---------------------------------------------------------------------------
# B3. 이미 답한 것을 다시 묻지 않는다
# ---------------------------------------------------------------------------
class TestAnsweredQuestionsAreNotAskedAgain:
    STING = {"local": "쏘인 자리만 붓고 아팠습니다", "large_local": "쏘인 자리 주변이 넓게 부은 적은 있지만",
             "never": "벌에 쏘인 적이 없어", "unsure": "벌에 쏘였을 때의 반응 정보가 부족해"}

    @pytest.mark.parametrize("answer", ["local", "large_local", "never", "unsure", None])
    def test_venom_wording_follows_the_sting_answer(self, client, answer):
        body = _body([_row(1, "Honey bee venom")], {"sting_reaction": answer} if answer else {})
        md, quest, classic, out = _surfaces(client, body)
        section = md[md.index("## 🟡 관찰이 필요한 알러젠"):md.index("## 3️⃣")]
        assert "노출 경험이 없거나 정보가 부족해" not in md
        assert self.STING[answer or "unsure"] in section
        asked_again = "확인 포인트: 벌(꿀벌·말벌 등)에 쏘인 적이 있다면" in section
        assert asked_again == (answer in ("unsure", None)), "답한 환자에게 같은 질문을 다시 싣지 않는다"
        assert "벌에 쏘인 적이 있나요?" not in md
        chat = _chat(client, body)["indeterminate"]["answer"]
        assert self.STING[answer or "unsure"] in chat
        assert "노출 경험이나 정보가 부족해" not in chat and "먹은 음식" not in chat
        assert ("아직 답하지 못한 항목은" in chat) == (answer in ("unsure", None))
        # 응급 안내는 카드뉴스와 리포트가 같다
        for text in (md, quest, classic):
            assert "쏘인 뒤 이런 증상이 생기면 바로 응급실로 가세요." in text
        assert "바로 진료를 받으세요" not in out["assessments"][0]["rationale_ko"]

    def test_no_check_point_for_any_allergen_once_its_questions_are_answered(self, client):
        """판정을 미룬 항목의 '확인 포인트'는 문진에 실제로 있는 문항 가운데 답이 비어 있는 것만이다."""
        rows = [_row(1, "Dermatophagoides farinae"), _row(2, "Cat dander"), _row(3, "Egg white"),
                _row(4, "Alternaria alternata"), _row(5, "Birch"), _row(6, "Shrimp")]
        q = _post(client, "/api/questionnaire", {"ocr": _body(rows)["ocr"]})["questionnaire"]
        titles = {x["id"]: re.sub(r"\*\*", "", x["title"]) for s in q["sections"] for x in s["questions"]}
        # 아무것도 답하지 않으면 전부 판정 보류 + 확인 포인트(문진의 문항 제목 그대로)
        md = _post(client, "/api/classify", _body(rows))["report_markdown"]
        points = re.findall(r"\(확인 포인트: ([^(?]*)", md[md.index("## 🟡"):])
        assert len(points) == 6, points                      # 양성 항목 여섯 개 모두 판정 보류
        assert all(any(t.startswith(p) for t in titles.values()) for p in points), points
        # 판정을 미루게 답하되(모호한 답) 문항은 모두 답한 경우 — 확인 포인트가 없다
        answered = {"indoor_timing": "no", "indoor_away": "yes", "mite_dust": "no",       # 아래에서 덮어쓴다
                    "animal_contact__agn1": "rare", "animal_worse__agn1": "no", "animal_work": ["none"],
                    "food_symptoms__agn2": ["none"], "mold_damp": "yes", "mold_space": ["none"],
                    "mold_outdoor": ["none"], "mite_bedding_trial": "never", "mold_dehum_trial": "never",
                    "pollen_season__spring_tree": "no", "shellfish_react__agn5": "never"}
        answered.update({"indoor_timing": "no", "indoor_away": "no", "mite_dust": "no"})
        out = _post(client, "/api/classify", _body(rows, answered))
        md = out["report_markdown"]
        pending = [a["korean_name"] for a in out["assessments"] if a["verdict"] == "indeterminate"]
        assert pending, "판정을 미룬 항목이 있어야 이 검사가 의미가 있다"
        assert "확인 포인트" not in md
        assert "연중 코·눈 증상이 지속되나요" not in md and "그 음식을 먹으면" not in md

    def test_unsure_answer_keeps_the_question_as_a_check_point(self, client):
        body = _body([_row(1, "Cat dander")], {"animal_contact__agn0": "occasional", "animal_worse__agn0": "unsure",
                                               "animal_work": ["none"]})
        md = _post(client, "/api/classify", body)["report_markdown"]
        assert "(확인 포인트: 고양이 비듬과 접촉이 늘면 코·눈·피부·호흡기 증상이 심해지나요?)" in md
        assert "얼마나 접촉하나요" not in md


# ---------------------------------------------------------------------------
# B4. 응급 안내는 두 카드뉴스와 리포트에 똑같이
# ---------------------------------------------------------------------------
SAFETY = re.compile(r"아나필락시스|에피네프린|응급|119")
PARITY_MATRIX = {
    "anaphylaxis_history_everything": _body(
        [_row(1, "Peanut", 20, 4), _row(2, "Honey bee venom"), _row(3, "Latex"), _row(4, "Shrimp"), _row(5, "Birch"),
         _row(6, "Penicillin G", 1.2, 2)],
        {"symptom_pattern": "seasonal", "worse_seasons": ["spring"], "pollen_season__spring_tree": "yes",
         "food_symptoms__agn0": ["anaphylaxis", "breathing"], "severity__agn0": "anaphylaxis",
         "sting_reaction": "systemic", "severity__venom": "anaphylaxis", "other_symptom__agn2": ["anaphylaxis"],
         "shellfish_react__agn3": "systemic", "food_systemic": "yes", "food_systemic_foods": ["agn0"],
         "crossreact__agn4": ["apple"], "crossreact_sev__agn4": "anaphylaxis",
         "other_symptom__agn5": ["anaphylaxis"], "severity__agn5": "anaphylaxis"},
        {"allergic_diseases": ["anaphylaxis", "asthma", "food_allergy"], "current_medications": ["antihistamine"],
         "organ_systems": ["systemic", "skin", "gi", "lower_airway"]}),
    "anaphylaxis_history_no_relevant_allergen": _body(
        [_row(1, "Dermatophagoides farinae")], {"indoor_timing": "no", "indoor_away": "no", "mite_dust": "no"},
        {"allergic_diseases": ["anaphylaxis"]}),
    "anaphylaxis_history_mite_rhinitis": _body(
        [_row(1, "Dermatophagoides farinae")], {"indoor_timing": "yes", "mite_dust": "yes"},
        {"allergic_diseases": ["anaphylaxis", "allergic_rhinitis"], "organ_systems": ["nasal"]}),
    "case3_latex_venom_drug": CASE3,
    "asthma_ics_cat_owner": _body(
        [_row(1, "Dermatophagoides farinae"), _row(2, "Cat dander"), _row(3, "Birch")],
        {"indoor_timing": "yes", "animal_contact__agn1": "live", "animal_worse__agn1": "yes",
         "pollen_season__spring_tree": "yes"},
        {"allergic_diseases": ["asthma", "allergic_rhinitis"], "organ_systems": ["nasal", "lower_airway"],
         "current_medications": ["inhaled_steroid", "nasal_steroid"], "pets": ["cat"]}),
    "sensitized_food_and_untried_shellfish": _body(
        [_row(1, "Egg white"), _row(2, "Shrimp")], {"food_symptoms__agn0": ["none"], "shellfish_react__agn1": "never"},
        {"organ_systems": ["gi", "skin"]}),
    "venom_never_stung_with_systemic_organ": _body(
        [_row(1, "Yellow jacket venom")], {"sting_reaction": "never"}, {"organ_systems": ["systemic"]}),
    "nothing_positive_but_anaphylaxis_history": _body(
        [_row(1, "Birch", 0.1, 0, interpretation="Negative")], {}, {"allergic_diseases": ["anaphylaxis"]}),
}


def _safety_sentences(text, imperative_only=False):
    """응급 안내 문장(아나필락시스·에피네프린·응급·119 가 든 문장). 머리말('…:', '… —')은 떼고 본다."""
    text = re.sub(r"</?(b|span|i|em|strong|code)\b[^>]*>", "", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", text))
    out = set()
    for line in re.sub(r"[*`_>#|]", "", text).split("\n"):
        for s in re.split(r"(?<=[.!?])\s+|\s+—\s+|:\s+", line):
            s = re.sub(r"[^\w가-힣\s()·,~/%-]", "", s)
            s = re.sub(r"\s+", " ", s).strip(" -·0123456789")
            if SAFETY.search(s) and (not imperative_only or "세요" in s):
                out.add(s)
    return out


class TestSafetyLinesAreTheSameEverywhere:
    def test_classic_deck_carries_the_anaphylaxis_line(self, client):
        line = "아나필락시스 병력이 있다고 하셨어요: 응급 대처 계획과 에피네프린 자가주사기 처방 여부를 반드시 확인하세요."
        md, quest, classic, _out = _surfaces(client, PARITY_MATRIX["anaphylaxis_history_mite_rhinitis"])
        for text in (md.replace("**", ""), quest, classic):
            assert line in re.sub(r"\s+", " ", text)

    @pytest.mark.parametrize("name", sorted(PARITY_MATRIX))
    def test_safety_lines_match_across_decks_and_report(self, client, name):
        body = PARITY_MATRIX[name]
        quest = _post(client, "/api/classify", {**body, "ui": "quest"})
        classic = _post(client, "/api/classify", {**body, "ui": "classic"})
        q, c = _safety_sentences(quest["cardnews_html"]), _safety_sentences(classic["cardnews_html"])
        # 퀴즈는 퀘스트 카드뉴스에만 있다(문구는 그 카드 본문에서 온다) — 본문만 견준다
        assert q == c, {"quest_only": sorted(q - c), "classic_only": sorted(c - q)}
        report = _safety_sentences(quest["report_markdown"])
        missing = sorted(_safety_sentences(quest["cardnews_html"], imperative_only=True) - report)
        assert missing == [], f"카드뉴스에만 있는 응급 안내: {missing}"
        if "anaphylaxis" in body["screening"]["allergic_diseases"]:
            assert any("에피네프린 자가주사기" in s for s in q) and any("에피네프린 자가주사기" in s for s in report)


# ---------------------------------------------------------------------------
# F2. 이름이 닮은 다른 항원 — 다섯 조회 경로 모두에서
# ---------------------------------------------------------------------------
# (들어온 이름, 붙으면 안 되는 레지스트리 항원들, 기대 카테고리)
HARD_NEGATIVES = [
    ("Pea", ["Peanut", "Peach", "Pear"], "food"), ("완두콩", ["Peanut", "Soybean", "Bean"], "other"),
    ("Chickpea", ["Chicken", "Peanut"], "food"), ("Chick pea", ["Chicken", "Peanut"], "food"),
    ("Eggplant", ["Egg white", "Egg yolk"], "food"), ("가지", ["Egg white"], "other"),
    ("Cattle epithelium", ["Cat dander", "Rat epithelium"], "animal"),
    ("Cow", ["Cow milk", "Beef"], "animal"), ("Cow hair", ["Cow milk"], "animal"),
    ("Milkweed", ["Cow milk"], "other"), ("Soy milk", ["Cow milk"], "food"), ("Goat milk", ["Cow milk"], "food"),
    ("Coconut milk", ["Cow milk"], "food"), ("Almond milk", ["Cow milk", "Almond"], "food"),
    ("Oat grass", ["Oat"], "pollen_grass"), ("Wheat grass", ["Wheat"], "pollen_grass"),
    ("Rice weevil", ["Rice"], "other"), ("Crab apple", ["Crab", "Apple"], "food"),
    ("Catfish", ["Cat dander", "Crayfish"], "food"), ("Dogfish", ["Dog dander"], "food"),
    ("Dogwood", ["Dog dander"], "pollen_tree"), ("Maple", ["Apple"], "pollen_tree"),
    ("Horse chestnut", ["Horse dander"], "pollen_tree"), ("Pine nut", ["Pine", "Pineapple"], "food"),
    ("Walnut tree", ["Walnut"], "pollen_tree"), ("Peanut butter", ["Peanut"], "food"),
    ("Sweet potato", ["Potato"], "food"), ("Popcorn", ["Maize (corn)"], "food"),
    ("Olive oil", ["Olive"], "food"), ("Green bean", ["Bean", "Soybean"], "food"),
    ("Rye", ["Rye grass, perennial", "Rye flour"], "pollen_grass"), ("Beet", ["Beef", "Beech"], "food"),
    ("Bee", ["Beech", "Beef", "Honey bee venom"], "venom"), ("벌", ["Cockroach, German", "Cockroach, Mix"], "venom"),
    ("Hornet venom", ["Yellow hornet venom", "White-faced hornet venom"], "venom"),
    ("Penicilin mold", ["Penicillin G", "Penicillium"], "mold"),
]
BY_NAME = {a["canonical_name"]: a for a in REGISTRY}
# 레지스트리의 항원 자신인 이름(2026-10 별칭 추가) — 자기 항원으로 풀려야 한다
OWN = {"Cow": "Cow dander", "Cow hair": "Cow dander", "Cattle epithelium": "Cow dander"}


class TestLookalikeNamesNeverBorrowAnotherAntigen:
    @pytest.mark.parametrize("name,forbidden,category", HARD_NEGATIVES, ids=[n for n, _f, _c in HARD_NEGATIVES])
    def test_hard_negative_in_every_resolver(self, name, forbidden, category):
        from utils.allergen_mapper import get_allergen_mapper
        from services.crossreactivity_service import get_crossreactivity_service
        from services.allergen_search_service import get_allergen_search_service
        assert all(f in BY_NAME for f in forbidden), "목록의 항원 이름은 레지스트리에 있어야 한다"
        own = OWN.get(name)
        hit = get_allergen_mapper().find_allergen(name)
        assert (hit.canonical_name if hit else None) == own, f"mapper: {name} → {hit and hit.canonical_name}"
        assert resolve_category(name, "") == category
        kb = get_knowledge_service().get_backdata(name, None, None)
        if own:
            assert kb["canonical_name"] == own
        else:
            assert kb["source"] == "category_default" and kb["canonical_name"] == name, kb["canonical_name"]
            # 다른 항원의 개별 문장이 붙지 않는다(카테고리 기본 안내뿐)
            for f in forbidden:
                other = get_knowledge_service().get_backdata(f, None, None)
                bio = other.get("biology_ko") or ""
                assert not bio or kb["biology_ko"] != bio
        assert kb["category"] == category
        cross = get_crossreactivity_service().find(name, "")
        assert (cross["canonical_name"] if cross else None) == own
        search = get_allergen_search_service()
        assert search.is_known_name(name) == bool(own)

    def test_registry_names_sharing_a_token_or_prefix_stay_apart(self):
        """레지스트리 안에서 낱말·앞머리를 나눠 쓰는 이름 쌍(Pine/Pineapple, Egg white/Egg yolk, Cow milk/Cow dander,
        Hazel/Hazelnut, Oak/Oat …)을 훑어, 어느 쪽 이름도 다른 쪽 항원의 지식으로 풀리지 않는지 본다."""
        ks = get_knowledge_service()
        names = [(a, n) for a in REGISTRY for n in [a["canonical_name"], a["korean_name"]] if n]
        tok = lambda s: set(re.findall(r"[a-z가-힣]{3,}", s.lower()))  # noqa: E731
        pairs = 0
        for a, n in names:
            for b, m in names:
                if a is b or not (tok(n) & tok(m) or m.lower().startswith(n.lower()[:4])):
                    continue
                pairs += 1
                got = ks.get_backdata(n, None, None)
                assert got["canonical_name"] in (a["canonical_name"], a.get("kb_ref")), (n, m, got["canonical_name"])
        assert pairs > 100

    def test_pea_eggplant_cattle_in_a_report(self, client):
        rows = [_row(1, "Pea"), _row(2, "Eggplant"), _row(3, "Cattle epithelium"), _row(4, "Milkweed")]
        answers = {"food_symptoms__agn0": ["skin"], "food_symptoms__agn1": ["skin"],
                   "animal_contact__agn2": "frequent", "animal_worse__agn2": "yes",
                   "other_symptom__agn3": ["skin"]}
        md, quest, classic, out = _surfaces(client, _body(rows, answers))
        assert out["summary"]["counts"]["clinically_relevant"] == 4
        for text in (md, quest, classic):
            for borrowed in ("땅콩", "아나필락시스)을 일으킬 수 있는 대표적인", "철저히 확인·회피", "난백", "계란",
                             "고양이 비듬", "우유", "유제품"):
                assert borrowed not in text, borrowed
        assert "소 비듬" in md and "축사" in md, "Cattle epithelium 은 소 비듬(e4)으로 풀린다"

    def test_typos_and_qualifiers_still_resolve(self):
        ks = get_knowledge_service()
        for name, want in (("Birch polen", "Birch pollen"), ("Birch", "Birch pollen"), ("Altenaria", "Alternaria alternata"),
                           ("Dermatophagoides farinea", "Dermatophagoides farinae"),
                           ("Dermatophagoides farinae (Df)", "Dermatophagoides farinae"), ("Cat epithelium", "Cat dander")):
            assert ks.get_backdata(name, None, None)["canonical_name"] == want, name


# ---------------------------------------------------------------------------
# F3. 벌독 · 말 · 소
# ---------------------------------------------------------------------------
class TestVenomAndFarmAnimalAntigens:
    VENOMS = {"Honey bee venom": ("꿀벌 독", "256439001"), "Yellow jacket venom": ("땅벌 독(Yellow jacket)", "260193001"),
              "Paper wasp venom": ("쌍살벌 독", "260194007"),
              "White-faced hornet venom": ("말벌 독(White-faced hornet)", "260191004"),
              "Yellow hornet venom": ("말벌 독(Yellow hornet)", "260195008")}

    def test_registry_entries_with_provenance_and_verified_codes(self):
        source = {e["canonical_name"]: e for e in
                  json.load(open("instructions/allergen_map_prompt_v2.json", encoding="utf-8"))["entries"]}
        for name, (korean, code) in self.VENOMS.items():
            a = BY_NAME[name]
            assert (a["category"], a["korean_name"], a["coding"]["snomed_ct"]) == ("venom", korean, code)
            assert source[name]["provenance"] == "added_2026_10: unverified against the clinic's actual panel"
            assert a["provenance"] == source[name]["provenance"]
        for name in ("Horse dander", "Cow dander"):
            assert source[name]["provenance"].startswith("added_2026_10: aliases")
            assert source[name]["provenance"].endswith("unverified against the clinic's actual panel")

    def test_search_finds_bees_and_farm_animals(self, client):
        def names(q):
            return [i["canonical_name"] for i in client.get("/api/allergens/search", params={"q": q, "limit": 20}).json()["items"]]
        assert "Honey bee venom" in names("bee")
        assert set(names("벌")) >= set(self.VENOMS)
        assert names("Horse")[0] == "Horse dander" and names("말")[0] == "Horse dander"
        assert names("Cattle")[0] == "Cow dander" and "Cow dander" in names("소")

    def test_korean_name_and_knowledge_in_the_report(self, client):
        body = _body([_row(1, "Honey bee venom"), _row(2, "Horse"), _row(3, "Cattle")],
                     {"sting_reaction": "systemic", "severity__venom": "severe",
                      "animal_contact__agn1": "frequent", "animal_worse__agn1": "yes",
                      "animal_contact__agn2": "frequent", "animal_worse__agn2": "yes"})
        md, quest, classic, out = _surfaces(client, body)
        assert [a["korean_name"] for a in out["assessments"]] == ["꿀벌 독", "말 비듬", "소 비듬"]
        for text in (md, quest, classic):
            assert "Honey bee venom'에 대한" not in text and "준비 중입니다" not in text
            assert "꿀벌 독" in text and "말 비듬" in text and "소 비듬" in text
        assert "꿀벌(Apis mellifera)이 쏠 때 몸에 넣는 독입니다" in md
        assert "마구간" in md and "축사" in md
        # 벌독 면역치료 목록이 레지스트리 항원으로 이어진다(전신 반응 병력이 있을 때만)
        assert "| **벌독(꿀벌·말벌)** |" in md
        local = _post(client, "/api/classify", _body([_row(1, "Yellow jacket venom")], {"sting_reaction": "local"}))
        assert "## 💉" not in local["report_markdown"] and local["assessments"][0]["korean_name"] == "땅벌 독(Yellow jacket)"

    def test_unknown_bee_names_get_category_text_not_a_placeholder(self):
        for name in ("벌독", "Hornet venom", "Bumble bee"):
            kb = get_knowledge_service().get_backdata(name, None, None)
            assert kb["category"] == "venom" and kb["source"] == "category_default"
            assert "준비 중" not in kb["biology_ko"] and "벌이 쏠 때" in kb["biology_ko"]


# ---------------------------------------------------------------------------
# F4. 항히스타민제 위음성 주의는 피부반응검사에만
# ---------------------------------------------------------------------------
class TestSkinTestCautionOnlyForSkinTests:
    SCR = {"allergic_diseases": ["allergic_rhinitis"], "organ_systems": ["nasal"],
           "current_medications": ["antihistamine", "systemic_steroid"]}

    @pytest.mark.parametrize("test_type,shown", [("MAST", False), ("UniCAP", False), ("SPT", True)])
    def test_flag_follows_the_test_type(self, client, test_type, shown):
        row = (_row(1, "Dermatophagoides farinae", None, None, mean_mm=7.0, unit="mm") if test_type == "SPT"
               else _row(1, "Dermatophagoides farinae"))
        body = _body([row], {"indoor_timing": "yes"}, self.SCR, test_type=test_type)
        md, quest, classic, _out = _surfaces(client, body)
        for text in (md, quest, classic):
            assert ("위음성" in text) == shown, test_type
        assert ("전신 스테로이드 복용은 피부반응검사 결과에 영향" in md) == shown
        from services.result_chat_service import ResultChatService
        from services.relevance_service import get_relevance_service
        from models.schemas import OCRResult, ScreeningProfile
        ocr = OCRResult(**body["ocr"])
        res = get_relevance_service().build_assessments(ocr, ScreeningProfile(**self.SCR))
        ctx = ResultChatService(api_key="").build_context(res, {"test_type": ocr.test_type}, ScreeningProfile(**self.SCR))
        assert ("위음성" in ctx) == shown

    def test_screening_summary_without_a_test_type_still_warns(self, client):
        out = client.post("/api/screening/summary", json=self.SCR).json()
        assert any("위음성" in f for f in out["flags"]), "검사 종류를 모르는 문진 요약에서는 그대로 알린다"


# ---------------------------------------------------------------------------
# F6. 잘못된 answers 로 500 이 나지 않는다
# ---------------------------------------------------------------------------
FUZZ_ROWS = [_row(1, "Dermatophagoides farinae"), _row(2, "Alternaria alternata"), _row(3, "Cockroach, German"),
             _row(4, "Cat dander"), _row(5, "Birch"), _row(6, "Timothy"), _row(7, "Ragweed"), _row(8, "Egg white"),
             _row(9, "Shrimp"), _row(10, "Honey bee venom"), _row(11, "Latex"), _row(12, "Penicillin G"),
             _row(13, "Zzyzx extract")]
BAD_VALUES = ["zzz", ["zzz"], 7, 1.5, True, None, {}, {"a": 1}, [], [[]], [{"x": 1}], [1, 2], "", ["yes", "zzz"],
              "x" * 5000, [None], ["none", "none"], {"value": "yes"}]


class TestMalformedAnswers:
    @pytest.fixture
    def questions(self, client):
        q = _post(client, "/api/questionnaire", {"ocr": _body(FUZZ_ROWS)["ocr"]})["questionnaire"]
        return [x for s in q["sections"] for x in s["questions"]]

    def test_reported_reproduction(self, client):
        out = _post(client, "/api/classify", _body(FUZZ_ROWS[:2], {"mold_outdoor": ["zzz"]}))
        assert out["ignored_answers"] == [{"id": "mold_outdoor", "reason": "invalid_value", "dropped": 1}]
        assert _post(client, "/api/classify", _body(FUZZ_ROWS[:2]))["ignored_answers"] == []

    def test_every_question_id_survives_every_bad_value(self, client, questions):
        """문진의 모든 문항 id × 잘못된 값 — /api/classify·/api/chat·/api/fhir 어디서도 500 이 나지 않고,
        버린 답은 '답하지 않음'과 같은 판정을 낸다."""
        ids = [q["id"] for q in questions]
        assert len(ids) >= 45 and {"mold_outdoor", "sting_reaction", "food_general_react"} <= set(ids)
        baseline = [a["verdict"] for a in _post(client, "/api/classify", _body(FUZZ_ROWS))["assessments"]]
        valid = {q["id"]: {o["value"] for o in q["options"]} for q in questions}
        checked = 0
        for qid in ids:
            for bad in BAD_VALUES:
                body = _body(FUZZ_ROWS, {qid: bad})
                r = client.post("/api/classify", json=body)
                assert r.status_code == 200, (qid, bad if len(str(bad)) < 40 else "long", r.status_code, r.text[:200])
                out = r.json()
                kept_some = isinstance(bad, list) and any(isinstance(v, str) and v in valid[qid] for v in bad)
                if not kept_some:
                    assert [a["verdict"] for a in out["assessments"]] == baseline, (qid, bad)
                if bad not in (None, []):
                    assert kept_some and "zzz" not in bad or any(i["id"] == qid for i in out["ignored_answers"]), (qid, bad)
                checked += 1
        assert checked == len(ids) * len(BAD_VALUES)
        # 같은 판정 코드를 부르는 나머지 두 경로 — 문항마다 대표 값 두 가지
        for qid in ids:
            for bad in (["zzz"], {"a": 1}):
                body = _body(FUZZ_ROWS, {qid: bad})
                assert client.post("/api/fhir", json=body).status_code == 200, (qid, bad)
                r = client.post("/api/chat", json={**body, "messages": [{"role": "user", "content": "안녕"}]})
                assert r.status_code == 200, (qid, bad)

    def test_whole_payload_shapes(self, client):
        everything_bad = {q: ["zzz", {"a": [1]}] for q in ("mold_outdoor", "mold_space", "worse_seasons")}
        everything_bad.update({"symptom_pattern": ["both"], "food_symptoms__agn7": "skin", "unknown_question": "yes",
                               "animal_contact__agn99": "live", "": "yes", "food_general_react": {"Apple": True}})
        out = _post(client, "/api/classify", _body(FUZZ_ROWS, everything_bad))
        reasons = {i["id"]: i["reason"] for i in out["ignored_answers"]}
        assert reasons["unknown_question"] == reasons["animal_contact__agn99"] == reasons[""] == "unknown_question"
        assert reasons["mold_outdoor"] == reasons["symptom_pattern"] == reasons["food_general_react"] == "invalid_value"
        assert "food_symptoms__agn7" not in reasons, "다중 선택에 온 문자열 하나는 한 칸짜리 목록으로 읽는다"
        egg = next(a for a in out["assessments"] if a["allergen_name"] == "Egg white")
        assert egg["verdict"] == "clinically_relevant"
        for bad in ([], "x", 3, None):          # answers 자체가 객체가 아니면 요청 형식 오류다(500 이 아니다)
            r = client.post("/api/classify", json={**_body(FUZZ_ROWS), "answers": bad})
            assert r.status_code == 422, bad

    def test_option_values_are_matched_ignoring_case(self, client):
        """교차반응 음식의 선택지 값은 레지스트리 대표 이름이다('Apple'). 예전 값('apple')으로 저장된 답도 읽는다."""
        body = _body([_row(1, "Birch")], {"pollen_season__spring_tree": "yes", "crossreact__agn0": ["apple", "PEACH"]})
        out = _post(client, "/api/classify", body)
        assert out["assessments"][0]["oas_foods"] == ["사과", "복숭아"] and out["ignored_answers"] == []


# ---------------------------------------------------------------------------
# F1 · F5 · F7 · F8. 문구
# ---------------------------------------------------------------------------
D1_SCR = {"allergic_diseases": ["allergic_rhinitis", "asthma"], "organ_systems": ["nasal", "lower_airway"],
          "current_medications": ["inhaled_steroid", "nasal_steroid", "antihistamine"], "pets": ["cat"]}
D1_ROWS = [_row(1, "Dermatophagoides farinae", 25, 4), _row(2, "Dermatophagoides pteronyssinus", 20, 4),
           _row(3, "Birch"), _row(4, "Cat dander"), _row(5, "Ragweed", 1.0, 2)]
D1_ANSWERS = {"symptom_pattern": "both", "worse_seasons": ["spring"], "pollen_season__spring_tree": "yes",
              "pollen_season__fall_weed": "no", "indoor_timing": "yes", "indoor_away": "yes", "mite_dust": "yes",
              "animal_contact__agn3": "live", "animal_worse__agn3": "yes", "severity__agn3": "mild",
              "animal_work": ["none"]}


class TestCalibratedWording:
    def test_no_unsourced_certainty_anywhere(self, client):
        md, quest, classic, _out = _surfaces(client, _body(D1_ROWS, D1_ANSWERS, D1_SCR))
        tg = json.load(open("data/treatment_guidance.json", encoding="utf-8"))["medication_text"]
        for text in (md, quest, classic):
            for banned in ("약물 효과를 높", "약만큼 중요", "약이 더 잘 듣", "회피가 기본", "3~12개월", "6~12개월",
                           "조절이 쉬워요", "조절에 유리"):
                assert banned not in text, banned
            # 시즌 전에 미리 쓰는 방법은 어디서나 근거수준과 함께
            assert all("근거수준은 매우 낮" in s for s in re.findall(r"[^.]*2주 전[^.]*\.", text)), text.count("2주 전")
            assert "2주 전" in text
        assert tg["pollen_prophylaxis_general_ko"] in md and tg["pollen_prophylaxis_general_ko"] in classic
        assert tg["exposure_tail_ko"] in md and tg["exposure_tail_ko"] in quest and tg["exposure_tail_ko"] in classic
        assert tg["review_interval_ko"] in quest and "정기 점검 간격은 진료에서 정합니다" in md

    def test_citation_year_matches_the_volume(self):
        src = json.load(open("data/treatment_guidance.json", encoding="utf-8"))["sources"]["aria_eaaci_2025"]
        assert "Allergy. 2026;81(4):954-976" in src["citation"] and "2025;81(4)" not in json.dumps(src, ensure_ascii=False).replace(
            src["citation_check_ko"], "")
        assert src["pmid"] == "41324154" and "PubMed" in src["citation_check_ko"]

    def test_bathing_label_does_not_recommend_what_the_body_calls_unproven(self):
        steps = json.load(open("data/animal_allergen_management.json", encoding="utf-8"))["owner"]["steps"]
        wash = next(s for s in steps if s["key"] == "washing")
        assert "자주" not in wash["short_ko"] and "증명되지 않았" in wash["short_ko"] and "증명되지 않았" in wash["text_ko"]

    def test_mite_heading_depends_on_what_was_positive(self, client):
        both = _post(client, "/api/classify", _body(D1_ROWS, D1_ANSWERS, D1_SCR))["report_markdown"]
        assert "### 집먼지진드기(유럽·미국 두 종)" in both and "두 종이 함께 양성인 경우가 흔하며" in both
        for ui in ("quest", "classic"):
            one = _post(client, "/api/classify", {**_body([D1_ROWS[0]], D1_ANSWERS, D1_SCR), "ui": ui})
            for text in (one["report_markdown"], _text(one["cardnews_html"])):
                assert "두 종" not in text, "한 종만 양성인데 '두 종'이라고 부르지 않는다"
                assert "집먼지진드기(미국집먼지진드기)" in text
        # 다른 종이 음성으로 검사된 경우도 같다
        neg = _row(2, "Dermatophagoides pteronyssinus", 0.1, 0, interpretation="Negative")
        md = _post(client, "/api/classify", _body([D1_ROWS[0], neg], D1_ANSWERS, D1_SCR))["report_markdown"]
        assert "두 종" not in md and "집먼지진드기(미국집먼지진드기)" in md

    def test_no_double_bullet_and_no_duplicate_food_options(self, client):
        md = _post(client, "/api/classify", _body([_row(1, "Zzyzx extract")]))["report_markdown"]
        assert "- •" not in md and "- 🔎 기타: Zzyzx extract" in md
        q = _post(client, "/api/questionnaire", {"ocr": _body(D1_ROWS + [_row(6, "Mugwort"), _row(7, "Latex")])["ocr"]})
        for question in (x for s in q["questionnaire"]["sections"] for x in s["questions"] if x["type"] == "multi"):
            labels = [o["label"] for o in question["options"]]
            values = [o["value"].lower() for o in question["options"]]
            assert len(labels) == len(set(labels)) and len(values) == len(set(values)), question["id"]
            assert not {"셀러리", "샐러리"} <= set(labels), question["id"]

    def test_crossreactivity_section_lists_only_reported_links_and_says_what_it_is(self, client):
        md = _post(client, "/api/classify", _body(D1_ROWS, D1_ANSWERS, D1_SCR))["report_markdown"]
        assert "앞으로 주의해서 관찰할 음식" not in md
        sec = md[md.index("## 🍽️ 교차반응 — 알아 둘 음식 (지금 알레르기가 있다는 뜻이 아닙니다)"):md.index("## 3️⃣")]
        assert "지금 알레르기가 있다는 뜻이 아니며, 문제없이 드시고 있다면 끊을 필요가 없습니다" in sec
        cat = sec[sec.index("「고양이 비듬」과 교차반응이 보고된 음식"):].split("**🔗")[0]
        assert "돼지고기" in cat and "소고기" not in cat and "우유" not in cat
        mite = sec[sec.index("「집먼지진드기」와 교차반응이 보고된 음식"):].split("**🔗")[1 - 1].split("「자작")[0]
        assert "새우" in mite and "아니사키스" not in mite and "전복" not in mite
        assert "아니사키스" not in md

    def test_patient_header_appears_once_in_the_printed_document(self, client):
        out = _post(client, "/api/classify", _body(D1_ROWS, D1_ANSWERS, D1_SCR))
        doc = _text(out["report_document_html"])
        assert doc.count("2026-06-15") == 2, "표지의 부제와 검사일 칸 — 본문에서 한 번 더 나오지 않는다"
        assert "리포트 작성일" in doc and doc.count("리포트 작성일") == 1
        assert out["report_markdown"].startswith("# 🌿 김환자님"), "화면용 Markdown 의 머리말은 그대로"

    def test_deck_text_is_gated_on_what_the_patient_reported(self, client):
        rows, answers = [D1_ROWS[0]], {"indoor_timing": "yes"}
        bare = _text(_post(client, "/api/classify", _body(rows, answers, {"organ_systems": ["nasal"]}))["cardnews_html"])
        assert "이미 앓고 있는 질환과" not in bare and "처음 알려주신 증상과 검사 결과를 이어봤어요" in bare
        assert "약을 쓰고 있다면" not in bare and "기존 치료와" not in bare
        assert "노출과 증상, 이렇게 이어집니다" in bare
        full = _text(_post(client, "/api/classify", _body(rows, answers, D1_SCR))["cardnews_html"])
        assert "이미 앓고 있는 질환과 검사 결과를 이어봤어요" in full and "약을 쓰고 있다면" in full
        assert "기존 치료와 어떻게 함께 갈까" in full


class TestKoreanParticles:
    @pytest.mark.parametrize("word,kind,want", [
        ("고양이", "과와", "고양이와"), ("땅콩", "과와", "땅콩과"), ("계란 흰자", "을를", "계란 흰자를"),
        ("쌀", "으로", "쌀로"), ("땅콩", "으로", "땅콩으로"), ("새우", "으로", "새우로"),
        ("집먼지진드기(유럽·미국 두 종)", "은는", "집먼지진드기(유럽·미국 두 종)는"),
        ("땅벌 독(Yellow jacket)", "은는", "땅벌 독(Yellow jacket)은"),
        ("Latex", "은는", "Latex는"), ("Cat", "과와", "Cat과"), ("Dog", "과와", "Dog와"), ("Apple", "을를", "Apple을"),
        ("Peanut", "을를", "Peanut을"), ("Penicillin G", "을를", "Penicillin G를"),
        ("Der p 1", "이가", "Der p 1이"), ("Der p 2", "이가", "Der p 2가"), ("‘라텍스’", "은는", "‘라텍스’는"),
    ])
    def test_particle_follows_the_final_sound(self, word, kind, want):
        assert with_josa(word, kind) == want and josa(word, kind) == want[len(word):]

    def test_no_parenthesised_particle_fallbacks_in_any_output(self, client):
        fallbacks = re.compile(r"은\(는\)|는\(은\)|을\(를\)|를\(을\)|과\(와\)|와\(과\)|이\(가\)|\(으\)로|\(이\)")
        rows = D1_ROWS + [_row(6, "Shrimp"), _row(7, "Egg white"), _row(8, "Zzyzx extract"), _row(9, "Dog dander")]
        answers = {**D1_ANSWERS, "food_systemic": "yes", "food_systemic_foods": ["agn5"],
                   "animal_contact__agn8": "occasional", "animal_worse__agn8": "no", "animal_work": ["vet"],
                   "animal_work_animals": ["agn3"], "animal_work_symptoms": "yes"}
        body = _body(rows, answers, {**D1_SCR, "disease_other": "편두통"})
        q = _post(client, "/api/questionnaire", {"ocr": body["ocr"], "screening": body["screening"]})
        assert not fallbacks.search(json.dumps(q, ensure_ascii=False))
        md, quest, classic, out = _surfaces(client, body)
        for text in (md, quest, classic, json.dumps(out["assessments"], ensure_ascii=False)):
            assert not fallbacks.search(text), fallbacks.search(text).group(0)
        for s in _chat(client, body).values():
            assert not fallbacks.search(s["text"] + (s["answer"] or ""))
        fhir = json.dumps(_post(client, "/api/fhir", body), ensure_ascii=False)
        assert not fallbacks.search(fhir)
        assert "고양이 비듬과 얼마나 접촉하나요?" in json.dumps(q, ensure_ascii=False)
        assert "새우를 실제로 먹었을 때 어떤가요?" in json.dumps(q, ensure_ascii=False)
