"""쓰는 약 · 주증상 · 감작만 항원 안내에서 독립 검증이 찾은 결함의 회귀 테스트.

  ① '감작만' 항목의 입장은 한 가지다 — 리포트 2절, 두 카드뉴스의 '감작만' 카드, 상담 답변이 같은 문장을 쓰고
     "피하지 않아도 됩니다" 옆에 "줄여 두세요"가 놓이지 않는다. 근거 강도(unproven)를 넘는 동사를 쓰지 않는다.
  ② 음식·벌독만 있는 환자에게 흡입 알러젠의 통계·노출 줄이기·호흡기 증상을 붙이지 않는다.
  ③ 동물: 자주 접촉하는 비사육자, 판정을 미룬 항목, 다른 알레르기 질환이 있는 환자.
  ④ 약물 선택지의 이름과 주의 문장을 섞지 않는다. 문장 이음의 구두점.
  ⑤ 약이 조절하는 증상을 환자가 알려 주지 않은 증상으로 쓰지 않는다.
  ⑥ 예전 문장이 새 문장과 어긋나거나 겹치지 않는다.
  ⑦ 문장은 출처가 말한 만큼만.
  ⑧ 약이 없는 환자의 제목, 문서마다 실제로 실린 수칙만 가리키기.
  ⑨ 카드 수.
"""
import html as html_lib
import json
import re

import pytest

from config.settings import BASE_DIR
from models.schemas import ClinicalRelevance
from services import care_guidance_service as cg
from services.exposure_guidance_service import allocated_tips, is_duplicate_tip
from services.result_chat_service import ResultChatService
from test_care_guidance import (  # noqa: F401 — _no_web_lookup 은 autouse fixture
    BIRCH, CAT, DOG, MITE, RAGWEED, _no_web_lookup, _ocr, _run, _scr, _section)

SENS = ClinicalRelevance.SENSITIZED_ONLY
IND = ClinicalRelevance.INDETERMINATE
REL = ClinicalRelevance.CLINICALLY_RELEVANT

EGG = ("Egg white", "난백")
BEE = ("Honey bee venom", "꿀벌독")
ALT = ("Alternaria", "알터나리아")
ROACH = ("Cockroach", "바퀴벌레")
MITE_P = ("Dermatophagoides pteronyssinus", "유럽 집먼지진드기")

STANCE = "‘감작만’은 진단이 아닙니다. 과도한 회피는 필요하지 않고, 음식을 끊을 필요도 없습니다."
REDUCE = "부담이 적은 범위에서 노출을 줄여 두면 도움이 될 수 있습니다(임상시험으로 증명된 것은 아닙니다)."
WATCH = "감작은 남아 있어 추적이 필요합니다. 증상이 새로 생기면 다시 평가받으세요."
# 한 가지 입장과 어긋나는 예전 문장, 근거(unproven)를 넘는 문장, 환자에게 알레르기 질환이 없다고 단정하는 문장
FLAT = ("피하지 않아도 됩니다", "줄여 두세요", "줄여 두기를 권합니다", "부담이 적고 해가 없는",
        "지금은 알레르기 질환이 아닙니다", "알레르기 질환이 아니고", "알레르기 질환으로 판정하지 않았습니다",
        "따로 피할 것은 없습니다")

MITE_YES = {"indoor_timing": "yes"}
NO_DISEASE = dict(allergic_diseases=["none"], current_medications=["none"], organ_systems=[], pets=["none"],
                  symptom_present=False)
ALL_DISEASES = ["allergic_rhinitis", "asthma", "atopic_dermatitis", "allergic_conjunctivitis", "chronic_urticaria",
                "food_allergy", "anaphylaxis", "drug_allergy", "sinusitis"]
ALL_ORGANS = ["nasal", "ocular", "lower_airway", "skin", "gi", "systemic"]


def _pets(*pairs):
    """[(항원 영문명, 접촉 답, 악화 답)] → 문진 답 사전을 만드는 함수."""
    def build(keys):
        out = dict(MITE_YES)
        for name, contact, worse in pairs:
            out["animal_contact__" + keys[name]] = contact
            if worse:
                out["animal_worse__" + keys[name]] = worse
        return out
    return build


# 검증 행렬 — (항원, 문진 답, 스크리닝)
MATRIX = {
    "mite_rhinitis_two_drugs": ([MITE, MITE_P], MITE_YES, dict(
        allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"], pets=["none"],
        current_medications=["nasal_steroid", "antihistamine"])),
    "asthma_rhinitis_ics_nasal_only": ([MITE], MITE_YES, dict(
        allergic_diseases=["asthma", "allergic_rhinitis"], organ_systems=["nasal"], pets=["none"],
        current_medications=["inhaled_steroid"])),
    "asthma_ics_cat_owner_sens": ([MITE, CAT], _pets(("Cat dander", "yes", "no")), dict(
        allergic_diseases=["asthma"], organ_systems=["lower_airway"], pets=["cat"],
        current_medications=["inhaled_steroid"])),
    "dog_sens_frequent_contact": ([DOG], _pets(("Dog dander", "yes", "no")), NO_DISEASE),
    "dog_no_contact": ([DOG], _pets(("Dog dander", "no", None)), NO_DISEASE),
    "cat_unsure": ([MITE, CAT], _pets(("Cat dander", "yes", "unsure")), dict(
        allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"], pets=["cat"],
        current_medications=["nasal_steroid"])),
    "egg_sens_only": ([EGG], {}, NO_DISEASE),
    "venom_local_only": ([BEE], {"sting_reaction": "local"}, NO_DISEASE),
    "cat_relevant_dog_sens": ([CAT, DOG], _pets(("Cat dander", "yes", "yes"), ("Dog dander", "yes", "no")), dict(
        allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal", "ocular"], pets=["cat", "dog"],
        current_medications=["antihistamine"])),
    "systemic_gi_antihistamine": ([EGG, BEE], {"sting_reaction": "local"}, dict(
        allergic_diseases=["food_allergy"], organ_systems=["gi", "systemic"], pets=["none"],
        current_medications=["antihistamine"])),
    "mite_sens_no_disease_no_med": ([MITE], {}, NO_DISEASE),
    "all_conditions": (
        [MITE, MITE_P, CAT, DOG, EGG, BEE, BIRCH, ALT, ROACH, RAGWEED],
        lambda k: {**_pets(("Cat dander", "yes", "yes"), ("Dog dander", "yes", "no"))(k),
                   "pollen_season__spring_tree": "yes", "pollen_season__fall_weed": "no", "sting_reaction": "local"},
        dict(allergic_diseases=ALL_DISEASES, organ_systems=ALL_ORGANS, pets=["cat"],
             current_medications=list(cg.MEDICATION_ORDER), medication_note="졸레어 4주마다")),
}


def _answer_all(rows, scr, over):
    """문진의 모든 질문에 '아니오/없음'으로 답하고, over 로 준 답만 바꾼다(실제 화면에서 끝까지 답한 환자)."""
    from services.questionnaire_service import get_questionnaire_engine
    from services.relevance_service import get_relevance_service
    q = get_questionnaire_engine().build(get_relevance_service().build_assessments(_ocr(rows), scr), scr)

    def build(keys):
        answers = {}
        for section in q["sections"]:
            for item in section["questions"]:
                values = [o["value"] for o in item.get("options", [])]
                if "none" in values:
                    answers[item["id"]] = ["none"] if item["type"] == "multi" else "none"
                elif "no" in values:
                    answers[item["id"]] = "no"
                elif "never" in values:
                    answers[item["id"]] = "never"
        answers.update(over(keys) if callable(over) else over)
        return answers
    return build


def _patient(name):
    rows, answers, screening = MATRIX[name]
    scr = _scr(**screening)
    res, md, quest, classic = _run(rows, _answer_all(rows, scr, answers), scr)
    return res, md, quest, classic, scr


def _chat(res, scr):
    return {s["key"]: s["answer"] for s in ResultChatService(api_key="").suggestions(res, "ko", screening=scr)}


def _plain(card_html):
    """카드 HTML → 화면에 보이는 글. 굵은 글씨 같은 줄 안 태그는 붙여 읽고, 블록 태그만 줄을 바꾼다."""
    text = re.sub(r"</?(b|span|i|em|strong)\b[^>]*>", "", card_html)
    return html_lib.unescape(re.sub(r"\s*<[^>]+>\s*", "\n", text))


def _relevance(res):
    return {a.allergen_name: a.relevance for a in res.assessments}


def _data(name):
    return json.loads((BASE_DIR / "data" / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# ① 한 가지 입장
# ---------------------------------------------------------------------------
class TestOneStanceForSensitizedOnly:
    @pytest.mark.parametrize("name,reducible", [
        ("mite_sens_no_disease_no_med", True),      # 흡입 알러젠
        ("asthma_ics_cat_owner_sens", True),        # 함께 사는 동물
        ("dog_sens_frequent_contact", True),        # 자주 접촉하는 동물
        ("egg_sens_only", False),                   # 음식 — 노출을 줄이는 대상이 아니다
    ])
    def test_report_cards_and_chat_say_the_same_thing(self, name, reducible):
        res, md, quest, classic, scr = _patient(name)
        assert SENS in _relevance(res).values()
        chat = _chat(res, scr)
        for text in (_section(md, "2️⃣ ⚪ 감작만 된 알러젠"), quest, classic, chat["sensitized_only"],
                     chat["sensitized_prevention"]):
            assert STANCE in text and WATCH in text
            assert (REDUCE in text) == reducible
        for text in (md, quest, classic, *chat.values()):
            for phrase in FLAT:
                assert phrase not in (text or ""), phrase

    @pytest.mark.parametrize("name", sorted(MATRIX))
    def test_no_patient_gets_the_old_flat_sentences(self, name):
        res, md, quest, classic, scr = _patient(name)
        for text in (md, quest, classic, *_chat(res, scr).values()):
            for phrase in FLAT:
                assert phrase not in (text or ""), (name, phrase)

    def test_heading_and_table_no_longer_stop_at_no_avoidance(self):
        _res, md, _q, _c, _scr_ = _patient("asthma_ics_cat_owner_sens")
        assert "## 2️⃣ ⚪ 감작만 된 알러젠 (과도한 회피 불필요 · 예방과 관찰)" in md
        assert "과도한 회피 불필요 · 지켜보기" in md
        # 예방과 관찰은 2절 안에 있다(예전에는 '피하지 않아도 됩니다' 절과 '줄여 두세요' 절이 따로 있었다)
        sec = _section(md, "2️⃣ ⚪ 감작만 된 알러젠")
        assert "### 🌱 예방과 관찰" in sec and "함께 사는 고양이" in sec

    def test_only_low_burden_steps_carry_the_low_burden_label(self):
        P = _data("sensitization_prevention.json")["patient_text"]
        hard = ("55~60℃", "60℃", "HEPA", "차단 커버", "주 1회", "바깥 활동 줄이기", "외출 자제", "마스크", "제습기")
        for key, g in P["groups"].items():
            if g["kind"] != "inhalant":
                assert "low_burden" not in g, f"{key}: 흡입 알러젠이 아니면 노출 줄이기 목록이 없다"
                continue
            assert g["low_burden"], key
            for step in g["low_burden"]:
                assert not any(w in step for w in hard), (key, step)
        steps = {(grp, s["key"]): s["short_ko"] for grp in ("owner", "non_owner")
                 for s in _data("animal_allergen_management.json")[grp]["steps"]}
        for status in ("owner", "frequent"):
            for k in P["animal"][status]["low_keys"]:
                assert not any(w in steps[tuple(k)] for w in hard), (status, k)
        assert ["owner", "cleaning"] in P["animal"]["owner"]["optional_keys"], "주 1회 청소·60℃ 세탁은 선택 조치"

    def test_reduce_exposure_wording_stays_within_the_unproven_rating(self):
        """exposure_reduction_prevents_onset = unproven. 노출을 줄이라는 문장은 '도움이 될 수 있습니다'까지만 쓰고,
        같은 묶음 안에서 증명되지 않았다는 점을 밝힌다."""
        d = _data("sensitization_prevention.json")
        assert {e["id"]: e["strength"] for e in d["evidence"]}["exposure_reduction_prevents_onset"] == "unproven"
        P = d["patient_text"]
        A = P["animal"]
        blocks = {"lead": [P["lead_reduce_ko"]], "inhalant": [P["inhalant"]["why_ko"], P["inhalant"]["optional_note_ko"]]}
        for status in ("owner", "frequent", "non_owner", "unknown", "no_contact", "unsure", "no_info"):
            blocks[status] = [v for k, v in A[status].items() if k.endswith("_ko")]
        strong = re.compile(r"권합니다|줄여 두세요|줄이세요|막아 줍니다|막을 수 있습니다|예방됩니다|해가 없는")
        for name, texts in blocks.items():
            joined = " ".join(texts)
            hit = strong.search(joined.replace("내보내라고 권하지 않습니다", ""))
            assert not hit, (name, hit.group(0))
            if "줄여 두면" in joined and name not in ("unknown",):
                assert re.search(r"증명되지는 않았|증명된 것은 아니|증명된 것은 아닙|시험한 연구는 아직 없", joined), name
        for status in ("owner", "frequent"):
            assert "도움이 될 수 있" in A[status]["do_ko"] and "도움이 될 수 있" in A[status]["card_ko"]


# ---------------------------------------------------------------------------
# ② 음식·벌독만 있는 환자
# ---------------------------------------------------------------------------
INHALANT_ONLY = ("꽃가루 비염", "한 해 약 5%", "노출을 줄여 두면", "노출을 줄이고", "기침·쌕쌕거림·숨참은 미루지",
                 "기침·쌕쌕거림·숨참이 생기면", "침구", "진드기 차단", "꽃가루가 많은 날", "꽃가루 많은 날")


class TestFoodAndVenomGetTheirOwnText:
    def test_egg_only_patient(self):
        res, md, quest, classic, scr = _patient("egg_sens_only")
        assert _relevance(res) == {"Egg white": SENS}
        out = cg.sensitized_prevention(res.assessments, scr)
        assert out["intro"] == [] and out["action"] == "" and REDUCE not in " ".join(out["lead"])
        for text in (md, quest, classic, *_chat(res, scr).values()):
            for phrase in INHALANT_ONLY:
                assert phrase not in (text or ""), phrase
        for text in (md, quest, classic):
            assert "증상 없이 드시던 음식은 이 결과만으로 끊지 않습니다" in text
            assert "먹고 2시간 안에 생기는 두드러기, 입술·얼굴 부기, 구토·복통, 숨참" in text
        assert "근거:" not in _section(md, "2️⃣ ⚪ 감작만 된 알러젠"), "음식만 있으면 흡입 알러젠 코호트를 근거로 대지 않는다"

    def test_venom_only_patient(self):
        res, md, quest, classic, scr = _patient("venom_local_only")
        assert _relevance(res) == {"Honey bee venom": IND}
        for text in (md, quest, classic, *_chat(res, scr).values()):
            for phrase in INHALANT_ONLY + (REDUCE,):
                assert phrase not in (text or ""), phrase
        assert "### 🌱 예방과 관찰" not in md, "판정 문장이 이미 지켜볼 증상을 말한다 — 흡입 알러젠용 절을 덧붙이지 않는다"
        for text in (quest, classic):
            assert "관찰이 필요한 항목, 이렇게 지켜보세요" in text and "감작만 된 항목, 이렇게 지켜보세요" not in text
            assert "벌에 쏘인 뒤 쏘인 자리를 넘어 온몸에 퍼지는 두드러기, 숨참, 어지럼" in text
            assert "쏘인 뒤 이런 증상이 생기면 바로 응급실로 가세요" in text

    def test_no_relevant_allergen_means_no_made_up_mite_and_pollen_rules(self):
        """예전에는 '기본 생활 수칙'이 누구에게나 침구 고온 세탁·외출 자제를 적었다 — 달걀만 양성인 환자에게도,
        '과도한 회피는 필요하지 않습니다'라고 판정한 진드기 감작 환자에게도."""
        for name in ("egg_sens_only", "venom_local_only", "mite_sens_no_disease_no_med"):
            _res, md, quest, classic, _s = _patient(name)
            plan = md[md.find("## 3️⃣"):]
            assert "꼭 지켜야 할 회피 수칙은 없습니다" in plan
            for text in (plan, quest[quest.find("우선 실천할"):], classic[classic.find("우선 실천할"):]):
                for phrase in ("55~60℃", "꽃가루 많은 날", "꽃가루가 많은 날", "습도 50%"):
                    assert phrase not in text, (name, phrase)


# ---------------------------------------------------------------------------
# ③ 동물
# ---------------------------------------------------------------------------
class TestAnimalUsesWhatThePatientEntered:
    def test_contact_answers_are_carried_on_the_assessment(self):
        res, *_ = _patient("dog_sens_frequent_contact")
        assert res.assessments[0].answers["animal_contact"] == "yes"
        assert res.assessments[0].answers["animal_worse"] == "no"
        res, *_ = _patient("dog_no_contact")
        assert res.assessments[0].answers["animal_contact"] == "no"

    def test_frequent_contact_non_owner_is_told_to_reduce_and_retest(self):
        res, md, quest, classic, scr = _patient("dog_sens_frequent_contact")
        assert _relevance(res) == {"Dog dander": SENS}
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "frequent" and an["low"]
        for text in (md, quest, classic):
            assert "자주 만나는 개 — 노출을 줄여 두면 도움이 될 수 있습니다" in text
            assert "개 가까이에서 되풀이되면 진료를 받아 다시 평가하세요. 필요하면 재검사를 합니다" in text
            assert "지금 할 일은 많지 않습니다" not in text and "그런 접촉이 이미 잦다면" not in text
        assert "개를 키우지는 않지만 자주 접촉한다고 하셨습니다" in md

    @pytest.mark.parametrize("name,headline,status", [
        ("dog_no_contact", "개 — 접촉이 적어 판단을 미뤘습니다", "no_contact"),
        ("cat_unsure", "고양이 — 증상과의 관계를 더 지켜봐야 합니다", "unsure"),
    ])
    def test_indeterminate_animal_has_its_own_wording_under_its_own_heading(self, name, headline, status):
        res, md, quest, classic, scr = _patient(name)
        animal = "Dog dander" if "dog" in name else "Cat dander"
        assert _relevance(res)[animal] == IND
        out = cg.sensitized_prevention(res.assessments, scr)
        assert out["animals"] == [] and [an["status"] for an in out["watch_animals"]] == [status]
        assert out["lead"] == [], "감작만 항목이 없으면 '감작만'의 입장 문장을 붙이지 않는다"
        sens, watch = _section(md, "2️⃣ ⚪ 감작만 된 알러젠"), _section(md, "## 🟡 관찰이 필요한 알러젠")
        assert "감작만 된 항목은 없습니다" in sens and headline not in sens and "🌱 예방과 관찰" not in md
        assert headline in watch and "‘감작만’으로 정한 것도, 알레르기로 정한 것도 아닙니다" in watch
        cards = "".join(_plain(c) for c in cg.prevention_cards(res.assessments, scr))
        for text in (watch, cards):
            # 판정을 미뤘는데 '증상이 없다'고 말하지 않는다
            for wrong in ("증상은 없지만", "접촉해도 증상이 뚜렷하지 않아", "함께 지내도 증상이 심해지지 않는다고",
                          "접촉해도 증상이 심해지지 않는다고"):
                assert wrong not in text, wrong
        for text in (quest, classic):
            assert "🟡 관찰 필요" in text and "감작만 된 항목, 이렇게 지켜보세요" not in text
        if status == "no_contact":
            assert "접촉이 거의 없다고 하셔서" in watch and "노출을 줄여" not in watch
        else:
            assert "증상이 심해지는지는 분명하지 않다고 하셨습니다" in watch

    def test_patient_with_asthma_is_never_told_they_have_no_allergic_disease(self):
        res, md, quest, classic, scr = _patient("asthma_ics_cat_owner_sens")
        assert _relevance(res) == {"Dermatophagoides farinae": REL, "Cat dander": SENS}
        for text in (md, quest, classic, *_chat(res, scr).values()):
            assert not re.search(r"알레르기 질환(이|은) 아닙니다|알레르기 질환이 아니", text or "")
        assert "고양이 때문에 생기는 알레르기라고 볼 근거는 아직 없습니다" in md
        P = _data("sensitization_prevention.json")["patient_text"]
        texts = json.dumps(P, ensure_ascii=False)
        assert "알레르기 질환이 아닙니다" not in texts and "알레르기 질환으로 판정하지" not in texts

    def test_second_animal_does_not_repeat_the_first_animals_steps_or_point_at_missing_tips(self):
        """고양이·개 모두 감작만(함께 삶), 실제 주의 알러젠 없음 — 개 항목은 고양이 항목의 수칙을 가리키고,
        실리지도 않은 '우선 실천 수칙'을 가리키지 않는다."""
        scr = _scr(allergic_diseases=["allergic_rhinitis"], pets=["cat", "dog"])
        rows = [CAT, DOG]
        res, md, quest, classic = _run(rows, _answer_all(rows, scr, _pets(("Cat dander", "yes", "no"),
                                                                          ("Dog dander", "yes", "no"))), scr)
        cat, dog = cg.sensitized_prevention(res.assessments, scr)["animals"]
        assert cat["low"] and dog["low"] == dog["optional"] == []
        assert dog["same"] == "다른 동물 항목에 적은 수칙을 개에게도 똑같이 적용하면 됩니다."
        for text in (md, quest, classic):
            assert text.count("침실에는 들이지 말고 문을 닫아 두세요") == 1
            assert "에 적은 동물 수칙을" not in text
        assert md.count("감작된 사람은 감작되지 않은 사람보다 가슴 증상이 약 4배") == 1, "근거 문단도 한 번만"


# ---------------------------------------------------------------------------
# ④ 약물 선택지와 주의 문장, 구두점
# ---------------------------------------------------------------------------
class TestDrugOptionsAndPunctuation:
    def test_antihistamine_is_never_marked_as_the_patients_anaphylaxis_drug(self):
        res, md, quest, classic, scr = _patient("systemic_gi_antihistamine")
        by = {s["organ"]: s for s in cg.symptom_guidance(res.assessments, scr)}
        assert [d["name"] for d in by["systemic"]["drugs"]] == ["에피네프린 자가주사기"]
        assert not any(d["taking"] for d in by["systemic"]["drugs"])
        assert by["systemic"]["notes"] == ["항히스타민제는 가려움과 두드러기를 덜어 줄 뿐, 아나필락시스의 치료를 대신하지 못합니다."]
        assert by["gi"]["drugs"] == [] and len(by["gi"]["notes"]) == 1
        cards = "".join(_plain(c) for c in cg.symptom_cards(res.assessments, scr))
        assert "지금 사용 중" not in cards and "✔" not in cards
        assert "해당하는 것: 항히스타민제" not in quest + classic
        assert "💊 에피네프린 자가주사기. 항히스타민제는 가려움과 두드러기를 덜어 줄 뿐" in cards
        assert "지금 사용 중" not in md[md.find("### 🩺 주증상별 안내"):md.find("## 💊")]

    def test_drug_data_keeps_names_apart_from_sentences(self):
        d = _data("treatment_guidance.json")
        for organ, s in d["symptoms"].items():
            for drug in s["drugs"]:
                name = drug["name_ko"]
                assert name and not re.search(r"[.。]|니다|세요", name), (organ, name)
                assert " — " not in drug["text_ko"], "이름은 name_ko 에만"
            for note in s["drug_notes"]:
                assert "name_ko" not in note and note["text_ko"].endswith(".")
        assert d["symptoms"]["systemic"]["drug_notes"][0]["med"] == "antihistamine"
        assert all(x["med"] != "antihistamine" for x in d["symptoms"]["systemic"]["drugs"])

    @pytest.mark.parametrize("name", sorted(MATRIX))
    def test_generated_text_has_no_broken_punctuation(self, name):
        res, _md, _q, _c, scr = _patient(name)
        a = res.assessments
        md = "\n\n".join([cg.prevention_md(a, scr), cg.observation_md(a, scr), cg.symptoms_md(a, scr),
                          cg.medication_md(a, scr)])
        cards = "\n".join(_plain(c) for c in cg.prevention_cards(a, scr) + cg.symptom_cards(a, scr)
                          + cg.medication_cards(a, scr))
        chat = "\n".join(v or "" for v in _chat(res, scr).values())
        for label, text in (("md", md), ("cards", cards), ("chat", chat)):
            for pattern in (r"\.\.", r"[가-힣)] \.", r"✔\.", r"\. \.", r";\s*\.", r":\s*\.", r"\.;"):
                hit = re.search(pattern, text)
                assert not hit, (name, label, text[max(0, hit.start() - 40):hit.end() + 20])


# ---------------------------------------------------------------------------
# ⑤ 약이 조절하는 증상
# ---------------------------------------------------------------------------
class TestExposureLineUsesOnlyReportedSymptoms:
    def _line(self, organs, diseases, med="inhaled_steroid"):
        scr = _scr(allergic_diseases=diseases, organ_systems=organs, current_medications=[med], pets=["none"])
        res, md, quest, classic = _run([MITE], MITE_YES, scr)
        item = cg.medication_guidance(res.assessments, scr)["items"][0]
        return next(ln["text"] for ln in item["lines"] if ln["head"] == "내 알러젠과의 관계"), md, quest, classic

    def test_diagnosis_without_the_symptom_is_described_in_general(self):
        line, md, quest, classic = self._line(["nasal"], ["asthma", "allergic_rhinitis"])
        assert "노출될 때 생기는 기침·쌕쌕거림·숨참을 이 약이 조절합니다" not in md + quest + classic
        assert line.startswith("천식에서는 이번 검사에서 증상과 연결된 집먼지진드기 같은 흡입 알러젠에 노출되면 "
                               "기침·쌕쌕거림·숨참이 생기거나 심해질 수 있고, 이 약이 그런 증상을 조절합니다.")
        assert "주증상으로 고르신 증상은 아니므로 지금 겪고 계신다는 뜻은 아닙니다." in line
        assert line in md and line in quest and line in classic

    def test_reported_symptom_is_described_as_the_patients(self):
        line, *_ = self._line(["lower_airway"], ["asthma"])
        assert "집먼지진드기에 노출될 때 생기는 기침·쌕쌕거림·숨참을 이 약이 조절합니다." in line
        assert "지금 겪고 계신다는 뜻은 아닙니다" not in line

    def test_drug_for_two_diseases_splits_reported_from_unreported(self):
        line, *_ = self._line(["nasal"], ["asthma", "allergic_rhinitis"], med="leukotriene")
        assert "노출될 때 생기는 코 증상을 이 약이 조절합니다." in line
        assert "천식에서는" in line and "기침·쌕쌕거림·숨참이 생기거나 심해질 수 있고" in line
        assert "노출될 때 생기는 코 증상, 기침" not in line

    def test_no_reported_organ_at_all(self):
        line, *_ = self._line([], ["allergic_rhinitis"], med="nasal_steroid")
        assert line.startswith("알레르기 비염에서는") and "노출될 때 생기는" not in line


# ---------------------------------------------------------------------------
# ⑥ 예전 문장과의 충돌·중복
# ---------------------------------------------------------------------------
class TestOlderLinesAgreeWithTheSourcedText:
    OLD = ("증상을 빠르게 줄여줍니다", "증상이 없을 때도 꾸준히 쓰는 약", "증상이 없어도 꾸준히 쓰는 약", "임의 중단 금지",
           "발작과 직결", "### 💊 증상 관리")

    @pytest.mark.parametrize("name", sorted(MATRIX))
    def test_old_lines_are_gone(self, name):
        _res, md, quest, classic, _s = _patient(name)
        for text in (md, quest, classic):
            for phrase in self.OLD:
                assert phrase not in text, (name, phrase)

    def test_asthma_controller_sentence_has_one_source_per_deck(self):
        # 흡입 스테로이드 카드가 있으면 '치료와 연결하기' 카드는 조절제를 말하지 않는다
        _res, _md, quest, _classic, _s = _patient("asthma_ics_cat_owner_sens")
        assert quest.count("매일 쓰는 처방도 있고 증상이 있을 때 쓰는 처방도 있으니") == 1
        assert "천식이 있다고 하셨어요:" not in quest
        # 약 카드가 없으면 같은 지침 문장(매일 또는 증상이 있을 때)으로 말한다
        scr = _scr(allergic_diseases=["asthma"], organ_systems=["lower_airway"], pets=["none"])
        _res, _md, quest, _classic = _run([MITE], MITE_YES, scr)
        assert "천식이 있다고 하셨어요: 조절제는 처방받은 방식대로 이어 가는 약이에요. 매일 쓰는 처방도 있고 " \
               "증상이 있을 때 쓰는 처방도 있으니" in quest

    def test_patient_without_disease_or_symptoms_gets_no_drug_lines(self):
        _res, md, quest, classic, _s = _patient("mite_sens_no_disease_no_med")
        for text in (md, quest, classic):
            for drug in ("항히스타민", "비강 스테로이드", "점안액", "흡입 스테로이드"):
                assert drug not in text, drug

    def test_each_fact_appears_once_per_document(self):
        _res, md, quest, classic, _s = _patient("mite_rhinitis_two_drugs")
        assert md.count("1차 치료") == 1, "비강 스테로이드의 1차 치료 문장은 '지금 쓰는 약'에만"
        # 혈액검사(MAST) 결과지 — 피부반응검사에만 해당하는 항히스타민제 위음성 주의는 싣지 않는다
        assert md.count("피부반응검사") == 0 and quest.count("피부반응검사") == 0 and classic.count("피부반응검사") == 0
        assert md.count("집먼지진드기에 노출될 때 생기는") == 1, "알러젠 이름은 첫 약에서만 다 적는다"
        assert "위와 같은 알러젠에 노출될 때 생기는 재채기·콧물·가려움을 이 약이 조절합니다." in md
        assert md.count("(지금 사용 중)") == 2 and "설명은 아래 ‘지금 쓰는 약’ 절에 있습니다." in md


# ---------------------------------------------------------------------------
# ⑦ 출처가 말한 만큼만
# ---------------------------------------------------------------------------
class TestWordingMatchesTheSources:
    def test_rat_worker_figure_compares_sensitized_with_non_sensitized(self):
        d = _data("sensitization_prevention.json")
        assert "감작되지 않은 사람보다 가슴 증상이 생길 가능성이 약 4배" in d["sources"]["nieuwenhuijsen_2003"]["finding_ko"]
        A = d["patient_text"]["animal"]
        for status in ("owner", "frequent"):
            why = A[status]["why_ko"]
            assert "감작된 사람은 감작되지 않은 사람보다 가슴 증상이 약 4배" in why
            assert "노출이 계속될 때" not in why and "4배 더 많이 생겼습니다" not in why
            assert "그대로 들어맞는 수치는 아닙니다" in why
        assert "직업성 천식으로 추정되는 경우가 약 4배" in A["non_owner"]["why_ko"]
        assert "한 코호트" in A["non_owner"]["why_ko"]

    def test_observational_study_is_tagged_as_a_study_and_gated_on_asthma(self):
        m = _data("treatment_guidance.json")["medications"]["inhaled_steroid"]
        assert "사망" not in m["consistency"]["text_ko"] and m["consistency"]["sources"] == ["gina_2021"]
        study = [x for x in m["extra"] if "사망" in x["text_ko"]]
        assert len(study) == 1
        assert study[0]["basis"] == "study" and study[0]["sources"] == ["suissa_2000"] and study[0]["disease"] == "asthma"
        assert "관찰 연구" in study[0]["text_ko"] and "증명한 것은 아닙니다" in study[0]["text_ko"]
        assert m["consistency"]["disease"] == "asthma" and "악화" not in m["consistency_unmatched"]["text_ko"]

    def test_inhaled_steroid_user_without_asthma_gets_no_asthma_statements(self):
        scr = _scr(allergic_diseases=["none"], organ_systems=["nasal"], current_medications=["inhaled_steroid"],
                   pets=["none"])
        _res, md, quest, classic = _run([MITE], MITE_YES, scr)
        sec = _section(md, "지금 쓰는 약")
        for text in (sec, quest[quest.find("내 약 이해하기"):], classic[classic.find("내 약 이해하기"):]):
            for phrase in ("천식 사망", "심한 악화", "천식 환자", "완전히 끊지는"):
                assert phrase not in text, phrase
        assert "처방받은 방식대로 이어 가는 것이 중요합니다" in sec and "Suissa" not in sec
        # 천식을 알려 준 환자에게는 리포트에만(카드에는 싣지 않는다)
        _res, md, quest, _c, _s = _patient("asthma_ics_cat_owner_sens")
        assert "캐나다의 관찰 연구 한 건에서" in md and "Suissa" in md and "캐나다의 관찰 연구" not in quest

    def test_recommendation_strength_is_kept(self):
        m = _data("treatment_guidance.json")["medications"]
        for key in ("text_ko", "card_ko"):
            text = m["decongestant"]["consistency"][key]
            assert "조건부 권고, 근거 확실성은 매우 낮음" in text and "권하지 않습니다" not in text
            assert "최대 효과를 얻으려면 3년 이상(3~5년)" in m["immunotherapy"]["consistency"][key]
        assert "성인·청소년 천식 환자" in m["inhaled_steroid"]["diseases"]["asthma"]["text_ko"]


# ---------------------------------------------------------------------------
# ⑧ 제목, 실제로 실린 수칙만 가리키기
# ---------------------------------------------------------------------------
class TestTitlesAndPointers:
    def test_title_follows_the_medications_chosen(self):
        def title(meds, diseases=("allergic_rhinitis",)):
            scr = _scr(allergic_diseases=list(diseases), current_medications=meds, pets=["none"])
            res, md, quest, _c = _run([MITE], MITE_YES, scr)
            return cg.medication_guidance(res.assessments, scr)["title"], md, quest, _chat(res, scr)
        t, md, quest, chat = title([])
        assert t == "약물 치료 — 진료에서 확인할 것" and "꾸준히" not in t and "medication" not in chat
        assert "왜 꾸준히 써야 할까" not in md + quest
        t, md, _q, chat = title(["decongestant"])
        assert t == "지금 쓰는 약 — 어떻게 쓰는 약일까", "짧게 쓰는 약뿐인데 '왜 꾸준히'라고 쓰지 않는다"
        assert "꾸준히 써야" not in chat["medication"].split("\n")[0] and "왜 꾸준히 써야 할까" not in md
        t, *_ = title(["decongestant", "nasal_steroid"])
        assert t == "지금 쓰는 약 — 왜 꾸준히 써야 할까"

    @staticmethod
    def _mite_cat_dog_args():
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"], pets=["cat", "dog"])
        rows = [MITE, CAT, DOG]
        return rows, _answer_all(rows, scr, _pets(("Cat dander", "yes", "yes"), ("Dog dander", "yes", "no"))), scr

    def _mite_cat_dog(self):
        rows, answers, scr = self._mite_cat_dog_args()
        res = _run(rows, answers, scr)[0]
        assert _relevance(res) == {"Dermatophagoides farinae": REL, "Cat dander": REL, "Dog dander": SENS}
        return res, scr

    @pytest.mark.parametrize("tips", [cg.REPORT_TIPS, cg.CARD_TIPS])
    def test_steps_are_dropped_only_when_that_document_really_shows_them(self, tips):
        """집먼지진드기·고양이(함께 삶)가 실제 주의, 개(함께 삶)는 감작만.
        카드의 '우선 실천 수칙'은 6줄뿐이라 리포트(10줄)에는 있는 고양이 수칙이 카드에는 없다."""
        res, scr = self._mite_cat_dog()
        limit, name = tips
        relevant = cg._relevant(res.assessments)
        shown = [t["tip"] for t in allocated_tips([(cg._label(a), a) for a in relevant], scr, limit=limit)]
        dog = cg.sensitized_prevention(res.assessments, scr, tips)["animals"][0]
        for step in ("침실에는 들이지 말고 문을 닫아 두세요", "만진 뒤에는 손을 씻고 옷을 갈아입으세요"):
            assert (step in dog["low"]) != is_duplicate_tip(step, shown), (name, step)
        if dog["same"]:
            assert name in dog["same"] and not dog["low"]

    def test_card_and_report_limits_differ_for_this_patient(self):
        """위 테스트가 실제로 두 경우를 가르는지 — 카드에는 남고 리포트에서는 빠지는 수칙이 있어야 한다.
        예전에는 문서와 상관없이 10줄을 기준으로 삼아, 카드가 6줄에 없는 수칙을 '그대로입니다'라고 가리켰다."""
        res, scr = self._mite_cat_dog()
        report = cg.sensitized_prevention(res.assessments, scr, cg.REPORT_TIPS)["animals"][0]
        card = cg.sensitized_prevention(res.assessments, scr, cg.CARD_TIPS)["animals"][0]
        assert report["low"] == [] and "‘우선 실천 회피 수칙’에 적은 동물 수칙을 개에게도" in report["same"]
        assert card["low"] == ["만진 뒤에는 손을 씻고 옷을 갈아입으세요"] and card["same"] == ""
        quest = _run(*self._mite_cat_dog_args())[2]
        tips_card = quest[quest.find("우선 실천할 생활 수칙"):quest.find("내 주증상")]
        assert "만진 뒤에는 손을 씻고 옷을 갈아입으세요" not in tips_card, "카드의 6줄에는 이 수칙이 없다"
        assert "만진 뒤에는 손을 씻고 옷을 갈아입으세요" in quest, "그래서 개 카드가 직접 싣는다"


# ---------------------------------------------------------------------------
# ⑨ 카드 수
# ---------------------------------------------------------------------------
class TestDeckLength:
    @staticmethod
    def _cards(name):
        res, _md, _quest, _classic, scr = _patient(name)
        from services.cardnews_service import get_cardnews_service
        from services.cardnews_classic import get_classic_cardnews_service
        quest = get_cardnews_service().generate_html(res, {"name": "t"}, scr)
        classic = get_classic_cardnews_service().generate_html(res, {"name": "t"}, scr)
        return res, scr, len(re.findall(r'<div class="card"', quest)), len(re.findall(r'<div class="card"', classic))

    def test_all_conditions_patient(self):
        res, scr, n_quest, n_classic = self._cards("all_conditions")
        # 예전: 31장(주증상 6장 + 약 8장 + 예방 2장). 지금: 주증상 3장 이하, 약 3장 이하
        assert n_quest == n_classic <= 24
        assert len(cg.symptom_cards(res.assessments, scr)) <= 3
        assert len(cg.medication_cards(res.assessments, scr)) <= 3
        assert len(cg.prevention_cards(res.assessments, scr)) <= 2
        # 줄였어도 고른 증상과 약은 모두 카드에 남아 있다
        symptoms = "".join(cg.symptom_cards(res.assessments, scr))
        for s in cg.symptom_guidance(res.assessments, scr):
            assert s["label"] in symptoms or s["short"] in symptoms
            for d in s["drugs"]:
                assert d["name"] in html_lib.unescape(symptoms)
        meds = html_lib.unescape("".join(cg.medication_cards(res.assessments, scr)))
        for it in cg.medication_guidance(res.assessments, scr)["items"]:
            assert it["label"] in meds and it["short"] in meds

    def test_simple_patient(self):
        _res, _scr_, n_quest, n_classic = self._cards("mite_rhinitis_two_drugs")
        assert n_quest == n_classic == 12      # 예전 13장(약 카드 2장 → 1장)

    def test_no_pointer_only_cards(self):
        """가리킬 것이 없는 '치료와 연결하기' 카드, 가리키기만 하는 '노출 줄이기' 줄을 만들지 않는다."""
        _res, md, quest, classic, _s = _patient("mite_sens_no_disease_no_med")
        for text in (quest, classic):
            assert "치료와 연결하기" not in text
        _res, md, quest, classic, _s = _patient("all_conditions")
        for text in (md, quest, classic):
            assert "적은 내용 그대로입니다" not in text
        assert "치료와 연결하기" in quest
