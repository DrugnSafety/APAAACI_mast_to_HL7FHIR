"""쓰는 약 · 주증상 · 감작만 된 항원의 예방과 관찰 (services/care_guidance_service.py).

  ① 약 안내는 환자가 고른 약에만 나오고, 환자 자신의 질환과 '실제 주의' 알러젠 이름을 쓴다.
  ② 약을 시작·중단·증감하라는 문장은 없다.
  ③ 증상 카드는 환자가 고른 주증상 부위에만 나온다.
  ④ 예방 절은 감작만/관찰 필요 항원에만 나오고, 실제 주의·음성 항원에는 나오지 않는다.
  ⑤ 동물: 감작만 + 함께 삶 / 키우지 않음 은 서로 다른 안내, 실제 주의이면 기존 동물 관리 안내만.
  ⑥ 자유 기재 칸의 HTML 은 글자로만 실린다.
  ⑦ 자료 파일의 모든 문장은 읽은 범위가 적힌 출처에 묶여 있다.
"""
import json
import re
from html.parser import HTMLParser

import markdown as md_lib
import pytest

from config.settings import BASE_DIR
from models.schemas import ClinicalRelevance, OCRResult, ScreeningProfile
from services import care_guidance_service as cg
from services.cardnews_classic import get_classic_cardnews_service
from services.cardnews_service import get_cardnews_service
from services.knowledge_service import get_knowledge_service
from services.questionnaire_service import _key, get_questionnaire_engine
from services.relevance_service import get_relevance_service
from services.report_service import get_report_service
from services.result_chat_service import ResultChatService

REL = ClinicalRelevance.CLINICALLY_RELEVANT
SENS = ClinicalRelevance.SENSITIZED_ONLY

MITE = ("Dermatophagoides farinae", "미국 집먼지진드기")
CAT = ("Cat dander", "고양이 비듬")
DOG = ("Dog dander", "개 비듬")
BIRCH = ("Birch pollen", "자작나무 꽃가루")
RAGWEED = ("Ragweed pollen", "돼지풀 꽃가루")

MED_TITLE = "지금 쓰는 약 — 왜 꾸준히 써야 할까"
NO_MED_TITLE = "약물 치료 — 진료에서 확인할 것"
SENS_SECTION = "2️⃣ ⚪ 감작만 된 알러젠"          # 리포트 2절 — 한 가지 입장(lead)과 '예방과 관찰'이 함께 있다
PREV_TITLE = "### 🌱 예방과 관찰"
OWNER_HEAD = "함께 사는 고양이 — 노출을 줄여 두면 도움이 될 수 있습니다"
FREQUENT_HEAD = "자주 만나는 고양이 — 노출을 줄여 두면 도움이 될 수 있습니다"
NON_OWNER_HEAD = "키우지 않는 고양이 — 지금 할 일은 많지 않습니다"


@pytest.fixture(autouse=True)
def _no_web_lookup():
    ks = get_knowledge_service()
    before, ks.enable_web = ks.enable_web, False
    yield
    ks.enable_web = before


def _ocr(rows, negatives=()):
    from server import ocr_demo
    d = json.loads(ocr_demo().body)
    tpl = d["results"][0]
    out = [dict(tpl, allergen_name=en, korean_name=ko, category=None, class_value=3, value=5.0,
                value_text=None, interpretation="Positive") for en, ko in rows]
    out += [dict(tpl, allergen_name=en, korean_name=ko, category=None, class_value=0, value=0.0,
                 value_text=None, interpretation="Negative") for en, ko in negatives]
    d["results"] = out
    return OCRResult(**d)


def _text(html_text):
    body = html_text[html_text.find("<body"):]
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def _run(rows, answers, screening, negatives=()):
    """실제 경로(판정 → 리포트·카드뉴스). answers 의 값이 함수면 항원명→문진 키 사전을 받아 만든다."""
    res = get_relevance_service().build_assessments(_ocr(rows, negatives), screening)
    engine = get_questionnaire_engine()
    engine.build(res, screening)
    keys = {a.allergen_name: _key(i) for i, a in enumerate(res.assessments)}
    engine.classify(res, answers(keys) if callable(answers) else answers, screening)
    info = {"name": "홍길동"}
    md = get_report_service().build_patient_report_markdown(res, info, screening)
    quest = get_cardnews_service().generate_html(res, info, screening)
    classic = get_classic_cardnews_service().generate_html(res, info, screening)
    return res, md, _text(quest), _text(classic)


def _animal(name, contact, worse):
    return lambda k: {"indoor_timing": "yes", "animal_contact__" + k[name]: contact, "animal_worse__" + k[name]: worse}


def _scr(**kw):
    return ScreeningProfile(residence_country="KR", **kw)


def _section(md, title):
    seg = md[md.find(title):]
    return seg[:seg.find("\n---\n")] if "\n---\n" in seg else seg


# ---------------------------------------------------------------------------
# ① ② 쓰는 약
# ---------------------------------------------------------------------------
class TestMedicationGuidance:
    def test_only_reported_medications_with_own_disease_and_allergen(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"],
                   current_medications=["nasal_steroid", "antihistamine"])
        res, md, quest, classic = _run([MITE], {"indoor_timing": "yes"}, scr)
        assert res.assessments[0].relevance == REL
        out = cg.medication_guidance(res.assessments, scr)
        assert [i["code"] for i in out["items"]] == ["nasal_steroid", "antihistamine"]
        sec = _section(md, MED_TITLE)
        assert "### 👃 비강 스테로이드 스프레이" in sec and "### 💊 항히스타민제" in sec
        assert not re.search(r"### \S+ (흡입 스테로이드|류코트리엔|생물학적|충혈완화제|면역치료|먹는/주사)", sec)
        # 환자 자신의 질환과 '실제 주의' 알러젠을 이름으로 잇는다
        assert "**알레르기 비염:**" in sec and "천식" not in sec
        assert "증상과 연결된 집먼지진드기에 노출될 때 생기는 코막힘·콧물·재채기를 이 약이 조절합니다" in sec
        assert "증상이 덜한 날에도 거르지 않는 것이 중요합니다" in sec
        # 같은 묶음의 약 두 가지는 카드 한 장에 — 알러젠과의 관계는 그 카드에 한 번
        assert len(cg.medication_cards(res.assessments, scr)) == 1
        for text in (quest, classic):
            assert "내 약 이해하기" in text and "비강 스테로이드 스프레이" in text and "항히스타민제" in text
            assert text.count("집먼지진드기에 노출될 때 생기는 코 증상을 이 약들이 조절합니다") == 1
            assert "뿌린 직후에 듣는 약이 아니라 며칠에 걸쳐 효과가 나타납니다" in text
            assert "흡입한 뒤에는" not in text and "흡입 스테로이드 완전히" not in text

    def test_sensitized_only_allergen_is_not_named_as_the_drug_target(self):
        scr = _scr(allergic_diseases=["asthma"], organ_systems=["lower_airway"],
                   current_medications=["inhaled_steroid"], pets=["cat"])
        res, md, _q, _c = _run([MITE, CAT], _animal("Cat dander", "yes", "no"), scr)
        sec = _section(md, MED_TITLE)
        assert "증상과 연결된 집먼지진드기에 노출될 때 생기는 기침·쌕쌕거림·숨참을" in sec
        assert "고양이" not in sec, "감작만 된 항원을 약이 조절하는 원인으로 적지 않는다"
        assert "매일 쓰는 처방도 있고 증상이 있을 때 쓰는 처방도 있으니" in sec, "GINA: 매일 또는 증상 시"

    def test_no_relevant_allergen_says_so_instead_of_inventing_one(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], current_medications=["antihistamine"], pets=["none"])
        res, md, _q, _c = _run([DOG], _animal("Dog dander", "yes", "no"), scr)
        sec = _section(md, MED_TITLE)
        assert "연결된 흡입 알러젠은 확인되지 않았습니다" in sec and "개 비듬" not in sec

    def test_disease_without_medication(self):
        for meds in ([], ["none"]):
            scr = _scr(allergic_diseases=["asthma"], current_medications=meds)
            _res, md, quest, classic = _run([MITE], {"indoor_timing": "yes"}, scr)
            line = "천식 진단을 받았다고 하셨고, 지금 쓰는 약은 고르지 않으셨습니다."
            assert line in _section(md, NO_MED_TITLE) and line in quest and line in classic
            assert "### " not in _section(md, NO_MED_TITLE), "고르지 않은 약의 설명은 없다"
            for text in (md, quest, classic):
                assert "왜 꾸준히 써야 할까" not in text, "쓰는 약이 없는데 '왜 꾸준히 써야 할까'라고 묻지 않는다"

    def test_medication_without_matching_disease(self):
        scr = _scr(allergic_diseases=["food_allergy"], current_medications=["inhaled_steroid"])
        _res, md, quest, _c = _run([MITE], {"indoor_timing": "yes"}, scr)
        sec = _section(md, MED_TITLE)
        assert "어떤 증상 때문에 쓰시는지는 문진만으로 알 수 없습니다" in sec and "문진만으로 알 수 없습니다" in quest
        assert "**천식:**" not in sec and "노출될 때 생기는" not in sec, "말하지 않은 질환을 붙이지 않는다"

    def test_partly_covered_diseases_are_pointed_out(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis", "asthma"], current_medications=["nasal_steroid"])
        _res, md, _q, _c = _run([MITE], {"indoor_timing": "yes"}, scr)
        assert "천식: 고르신 약 가운데 이 질환에 흔히 쓰는 약은 없었습니다." in _section(md, MED_TITLE)

    def test_free_text_note_is_quoted_not_interpreted(self):
        scr = _scr(allergic_diseases=["asthma"], current_medications=["inhaled_steroid"],
                   medication_note="심비코트 아침저녁 2번")
        _res, md, quest, classic = _run([MITE], {"indoor_timing": "yes"}, scr)
        quote = "약에 대해 직접 적어 주신 내용: “심비코트 아침저녁 2번”. 이 내용은 해석하지 않고 그대로 옮겼습니다."
        assert quote in md and quote in quest and quote in classic

    def test_immunotherapy_patient(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], current_medications=["immunotherapy"])
        _res, md, quest, _c = _run([MITE], {"indoor_timing": "yes"}, scr)
        sec = _section(md, MED_TITLE)
        line = "최대 효과를 얻으려면 3년 이상(3~5년) 꾸준히 이어 가야 합니다"      # 지침의 '최대'를 빼지 않는다
        assert "### 💉 면역치료(설하/피하)" in sec and line in sec
        assert "치료 중인 항원이 이번 결과의 ‘실제 주의’ 알러젠과 같은지 진료에서 확인하세요" in sec
        assert line in quest

    def test_nothing_reported_means_no_section(self):
        scr = _scr(pets=["none"])
        res, md, quest, classic = _run([MITE], {"indoor_timing": "yes"}, scr)
        assert cg.medication_guidance(res.assessments, scr) is None and cg.medication_cards(res.assessments, scr) == []
        assert MED_TITLE not in md and "내 약 이해하기" not in quest and "내 약 이해하기" not in classic
        assert cg.medication_guidance(res.assessments, None) is None

    # 약을 시작·중단·증감하라는 지시형 문장
    FORBIDDEN = re.compile(
        r"(시작하세요|시작하십시오|복용하세요|드세요|사용하세요|쓰세요(?!\.)|끊으세요|중단하세요|중단하십시오|그만두세요|"
        r"늘리세요|줄이세요|올리세요|낮추세요|용량을 (늘|줄|올|낮)|두 배|[0-9]+ ?(mg|㎎|회 분사|번 뿌))")

    def test_no_start_stop_or_dose_change_wording_in_any_statement(self):
        data = json.loads((BASE_DIR / "data" / "treatment_guidance.json").read_text(encoding="utf-8"))
        texts = [v for v in data["medication_text"].values() if isinstance(v, str)]
        for m in data["medications"].values():
            texts += [x["text_ko"] for x in [m.get("general"), m.get("consistency"), *m.get("extra", []),
                                             *m.get("diseases", {}).values()] if x]
            texts += [x["card_ko"] for x in [m.get("consistency")] if x and x.get("card_ko")]
            texts += [m["consistency_unmatched"]["text_ko"]] if m.get("consistency_unmatched") else []
        for s in data["symptoms"].values():
            texts += [d["text_ko"] for d in s["drugs"] + s["drug_notes"]] + [s.get("no_asthma_ko", "")]
        assert len(texts) > 50
        for t in texts:
            hit = self.FORBIDDEN.search(t)
            # '멸균 식염수를 쓰세요'는 약이 아니라 코 세척 물에 관한 문장이다(selfcare, 위 목록에 없음)
            assert not hit, (hit.group(0), t)

    def test_every_medication_block_ends_with_the_ask_your_clinician_frame(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis", "asthma", "chronic_urticaria", "sinusitis"],
                   current_medications=list(cg.MEDICATION_ORDER))
        res, md, quest, _c = _run([MITE, BIRCH], {"indoor_timing": "yes", "pollen_season__spring_tree": "yes"}, scr)
        out = cg.medication_guidance(res.assessments, scr)
        assert [i["code"] for i in out["items"]] == list(cg.MEDICATION_ORDER)
        sec = _section(md, MED_TITLE)
        assert not self.FORBIDDEN.search(sec), self.FORBIDDEN.search(sec)
        closing = "약을 새로 쓰거나, 그만 쓰거나, 양을 바꾸는 일은 스스로 정하지 말고 진료에서 상의하세요."
        # 약 여덟 가지가 카드 세 장(묶음마다 한 장)에 실리고, 장마다 '진료에서 상의'로 맺는다
        assert len(out["card_groups"]) == 3 and sum(len(g["items"]) for g in out["card_groups"]) == len(out["items"])
        assert closing in sec and quest.count(closing) == len(out["card_groups"])
        assert "5일 넘게 이어 쓰지 않도록 제안합니다(조건부 권고, 근거 확실성은 매우 낮음)" in sec, \
            "충혈완화제는 '꾸준히'가 아니라 '짧게' — 권고 강도를 원문대로"
        assert "꽃가루가 날리기 약 2주 전부터" in sec and "근거수준은 매우 낮음" in sec
        assert sec.count("꽃가루가 날리기 약 2주 전부터") == 1, "같은 문장을 약마다 되풀이하지 않는다"


# ---------------------------------------------------------------------------
# ③ 주증상
# ---------------------------------------------------------------------------
class TestSymptomGuidance:
    LABELS = {"lower_airway": "기침·쌕쌕거림·숨참", "nasal": "코 증상(재채기·콧물·코막힘)",
              "ocular": "눈 증상(가려움·충혈·눈물)", "skin": "피부 증상(두드러기·가려움·습진)",
              "gi": "소화기 증상(복통·설사·구토)", "systemic": "전신 증상(어지럼·아나필락시스)"}

    @pytest.mark.parametrize("organs", [["nasal"], ["lower_airway", "ocular"], ["skin", "gi", "systemic"], []])
    def test_cards_only_for_reported_symptoms(self, organs):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=organs)
        res, md, quest, classic = _run([MITE, BIRCH], {"indoor_timing": "yes", "pollen_season__spring_tree": "yes"}, scr)
        assert [s["organ"] for s in cg.symptom_guidance(res.assessments, scr)] == \
            [o for o in cg.ORGAN_ORDER if o in organs]
        # 이어지는 알러젠이 같은 부위는 카드 한 장으로 모인다. 고른 부위는 모두 실리고, 고르지 않은 부위는 없다
        groups = cg.symptom_groups(res.assessments, scr)
        cards = cg.symptom_cards(res.assessments, scr)
        assert len(cards) == len(groups) <= len(organs)
        assert sorted(s["organ"] for g in groups for s in g) == sorted(organs)
        assert all(len({tuple(s["linked"]) for s in g}) == 1 for g in groups)
        section = md[md.find("### 🩺 주증상별 안내"):md.find("\n---\n", md.find("### 🩺 주증상별 안내"))] if organs else ""
        for organ, label in self.LABELS.items():
            for text in (quest, classic):
                assert (label in text[text.find("내 주증상"):] if "내 주증상" in text else False) == (organ in organs), \
                    (organ, organs)
            assert (label in section) == (organ in organs), (organ, organs)
        assert ("주증상별 안내" in md) == bool(organs)

    def test_links_only_relevant_allergens_that_can_explain_the_symptom(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal", "gi"], pets=["cat"],
                   current_medications=["nasal_steroid"])
        res, md, quest, _c = _run([MITE, CAT], _animal("Cat dander", "yes", "no"), scr)
        nasal, gi = cg.symptom_guidance(res.assessments, scr)
        assert nasal["linked"] == ["집먼지진드기"], "감작만 된 고양이는 잇지 않는다"
        assert gi["linked"] == [] and "연결된 알러젠은 확인되지 않았습니다" in gi["link_text"]
        assert [d["name"] for d in nasal["drugs"] if d["taking"]] == ["비강 스테로이드 스프레이"]
        assert "비강 스테로이드 스프레이(지금 사용 중)" in quest
        assert "**(지금 사용 중)**" in md and md.count("**(지금 사용 중)**") == 1

    def test_avoidance_line_names_the_step_once_and_is_not_a_copy_of_the_priority_tips(self):
        """주증상 묶음은 이어진 알러젠마다 노출 줄이기의 핵심 한 줄을 싣는다 — '수칙에 적은 그대로'라는 가리킴만
        두지 않고, '우선 실천 수칙'의 문장을 그대로 되풀이하지도 않는다. 같은 줄은 묶음마다 한 번만."""
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal", "ocular", "lower_airway"])
        res, md, quest, classic = _run([MITE, BIRCH], {"indoor_timing": "yes", "pollen_season__spring_tree": "yes"}, scr)
        plan = md[md.find("우선 실천 회피 수칙"):md.find("주증상별 안내")]
        shown = [ln.split(":** ", 1)[1] for ln in plan.split("\n") if ln.startswith("- **") and ":** " in ln]
        assert shown
        groups = cg.symptom_groups(res.assessments, scr)
        assert len(groups) == 1 and len(groups[0]) == 3, "세 부위 모두 같은 알러젠에 이어진다 → 한 묶음"
        avoid = groups[0][0]["avoid"]
        assert [t["label"] for t in avoid] == ["집먼지진드기", "자작나무 꽃가루"]
        section = md[md.find("### 🩺 주증상별 안내"):]
        for t in avoid:
            line = f"{t['label']} — {t['tip']}"
            assert t["tip"] and t["tip"] not in shown, "우선 실천 수칙의 문장을 그대로 옮기지 않는다"
            assert section.count(line) == 1 and quest.count(line) == 1 and classic.count(line) == 1, line
        for text in (md, quest, classic):
            assert "적은 내용 그대로입니다" not in text

    def test_airway_symptom_without_asthma_does_not_assume_asthma(self):
        scr = _scr(organ_systems=["lower_airway"])
        _res, md, quest, _c = _run([MITE], {"indoor_timing": "yes"}, scr)
        line = "천식 진단은 없다고 하셨습니다. 이 증상이 천식 때문인지는 이 검사로 알 수 없으므로"
        assert line in md and line in quest
        _res, md, _q, _c = _run([MITE], {"indoor_timing": "yes"}, _scr(organ_systems=["lower_airway"],
                                                                      allergic_diseases=["asthma"]))
        assert "천식 진단은 없다고 하셨습니다" not in md


# ---------------------------------------------------------------------------
# ④ 감작만 된 항원 — 예방과 관찰
# ---------------------------------------------------------------------------
class TestSensitizedPrevention:
    BIRCH_ONLY = {"pollen_season__spring_tree": "yes", "pollen_season__fall_weed": "no"}

    def test_section_lists_only_the_sensitized_only_allergen(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        res, md, quest, classic = _run([BIRCH, RAGWEED], self.BIRCH_ONLY, scr, negatives=[DOG])
        by = {a.allergen_name: a.relevance for a in res.assessments}
        assert by == {"Birch pollen": REL, "Ragweed pollen": SENS}
        out = cg.sensitized_prevention(res.assessments, scr)
        assert [(g["key"], g["names"]) for g in out["groups"]] == [("pollen", ["돼지풀 꽃가루"])]
        assert out["animals"] == [] and out["watch_groups"] == [] and out["watch_animals"] == []
        sec = _section(md, PREV_TITLE)
        assert "돼지풀 꽃가루" in sec and "자작나무" not in sec and "개 비듬" not in sec
        assert "지켜볼 증상: 그 꽃가루 시기에만 되풀이되는" in sec
        for text in (quest, classic):
            card = text[text.find("감작만 된 항목, 이렇게 지켜보세요"):]
            card = card[:card.find("필요하면 재검사를 합니다")]
            assert 100 < len(card) < 900
            assert "돼지풀 꽃가루" in card and "자작나무" not in card and "개 비듬" not in card

    def test_wording_is_calibrated_to_the_evidence(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        _res, md, quest, _c = _run([BIRCH, RAGWEED], self.BIRCH_ONLY, scr)
        sec = _section(md, SENS_SECTION)
        assert "‘감작만’은 진단이 아닙니다" in sec and "과도한 회피는 필요하지 않고" in sec
        assert "증상 없는 감작은 나중에 알레르기 비염이 생길 위험을 높입니다" in sec            # 확립된 사실
        assert "감작된 사람 대부분은 몇 년 동안 증상 없이 지냅니다" in sec                       # 불안을 키우지 않는다
        assert "도움이 될 수 있습니다(임상시험으로 증명된 것은 아닙니다)" in sec                   # 증명되지 않은 것
        assert "직접 시험한 연구는 아직 없습니다" in sec
        for overclaim in ("예방됩니다", "막을 수 있습니다", "반드시 생깁니다", "발병합니다", "피해야 합니다",
                          "줄여 두세요", "줄여 두기를 권합니다", "지금은 알레르기 질환이 아닙니다"):
            assert overclaim not in sec and overclaim not in quest, overclaim
        assert "진료를 받아 다시 평가하세요. 필요하면 재검사를 합니다" in sec

    def test_absent_when_everything_is_relevant_or_negative(self):
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        res, md, quest, classic = _run([MITE, BIRCH], {"indoor_timing": "yes", "pollen_season__spring_tree": "yes"},
                                       scr, negatives=[DOG, CAT])
        assert all(a.relevance == REL for a in res.assessments)
        assert cg.sensitized_prevention(res.assessments, scr) is None
        assert PREV_TITLE not in md
        for text in (quest, classic):
            assert "이렇게 지켜보세요" not in text and "· 예방" not in text

    def test_sensitized_only_food_is_not_told_to_reduce_exposure(self):
        scr = _scr()
        res = get_relevance_service().build_assessments(_ocr([("Egg white", "달걀 흰자"), MITE]), scr)
        for a in res.assessments:
            a.relevance = SENS
        out = cg.sensitized_prevention(res.assessments, scr)
        groups = {g["key"]: g for g in out["groups"]}
        assert groups["food"]["low"] == groups["food"]["optional"] == []
        assert "이 결과만으로 끊지 않습니다" in groups["food"]["keep"]
        data = json.loads((BASE_DIR / "data" / "sensitization_prevention.json").read_text(encoding="utf-8"))
        assert groups["mite"]["low"] == data["patient_text"]["groups"]["mite"]["low_burden"]
        # 손이 많이 가는 조치는 '부담이 적은 것'에 넣지 않는다 — '여력이 되면(선택)'에만
        for hard in ("55~60℃", "차단 커버", "HEPA", "주 1회"):
            assert not any(hard in t for t in groups["mite"]["low"]), hard
        assert any("55~60℃" in t for t in groups["mite"]["optional"])

    def test_reduce_tips_do_not_repeat_the_relevant_allergens_tips(self):
        """유럽 진드기는 실제 주의, 미국 진드기는 감작만일 수는 없다(같은 임상 그룹) — 서로 다른 그룹으로 본다."""
        scr = _scr(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        res, md, _q, _c = _run([BIRCH, RAGWEED], self.BIRCH_ONLY, scr)
        out = cg.sensitized_prevention(res.assessments, scr)
        plan = md[md.find("우선 실천 회피 수칙"):md.find("주증상별 안내")]
        from services.exposure_guidance_service import is_duplicate_tip
        shown = [ln.split(":** ", 1)[1] for ln in plan.split("\n") if ln.startswith("- **") and ":** " in ln]
        tips = out["groups"][0]["low"] + out["groups"][0]["optional"]
        assert tips
        for tip in tips:
            assert not is_duplicate_tip(tip, shown), tip


# ---------------------------------------------------------------------------
# ⑤ 동물 — 함께 사는지에 따라
# ---------------------------------------------------------------------------
class TestAnimalSensitizedOnly:
    def _cat(self, pets, worse="no", **kw):
        scr = _scr(allergic_diseases=["asthma"], organ_systems=["lower_airway"], pets=pets, **kw)
        return (*_run([MITE, CAT], _animal("Cat dander", "yes", worse), scr), scr)

    def _direct(self, pets, relevance=SENS):
        """문진을 거치지 않고 판정만 정해진 경우(접촉 질문의 답이 없다)."""
        scr = _scr(pets=pets)
        res = get_relevance_service().build_assessments(_ocr([CAT]), scr)
        res.assessments[0].relevance = relevance
        return res, get_report_service().build_patient_report_markdown(res, {"name": "홍길동"}, scr), scr

    def test_owner_gets_the_reduce_exposure_message(self):
        res, md, quest, classic, scr = self._cat(["cat"])
        assert {a.allergen_name: a.relevance for a in res.assessments}["Cat dander"] == SENS
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "owner"
        # 부담이 적은 것만 '부담이 적은 것부터'에 — 주 1회 청소·60℃ 세탁·HEPA 는 들어가지 않는다
        assert an["low"] == ["침실에는 들이지 말고 문을 닫아 두세요", "만진 뒤에는 손을 씻고 옷을 갈아입으세요"]
        sec = _section(md, PREV_TITLE)
        for text in (sec, quest, classic):
            assert OWNER_HEAD in text and NON_OWNER_HEAD not in text and FREQUENT_HEAD not in text
            assert "침실에는 들이지 말고 문을 닫아 두세요" in text                       # 기존 동물 관리 자료의 문장
            assert "닿거나 핥인 자리의 두드러기·가려움" in text and "기침·쌕쌕거림·숨참" in text
            assert "고양이 가까이에서 되풀이되면 진료를 받아 다시 평가하세요. 필요하면 재검사를 합니다" in text
        assert "고양이 비듬 검사가 양성이고 고양이와 함께 살고 계십니다" in sec
        assert "고양이 때문에 생기는 알레르기라고 볼 근거는 아직 없습니다" in sec
        assert "실천할 수 있는 범위에서 노출을 줄여 두면 도움이 될 수 있습니다" in sec
        assert "증명되지는 않았습니다" in sec and "가정의 반려동물에 그대로 들어맞는 수치는 아닙니다" in sec
        # 파양을 뜻하지 않는다
        assert "다른 집으로 보내야 한다는 뜻이 아닙니다" in sec
        assert "내보내야 한다는 뜻은 아니에요" in quest and "내보내야 한다는 뜻은 아니에요" in classic
        for alarm in ("파양", "내보내세요", "분리하세요", "키우면 안"):
            assert alarm not in sec and alarm not in quest, alarm

    def test_frequent_contact_without_ownership_gets_the_reduce_and_retest_message(self):
        """키우지는 않지만 '예 (키우거나 자주 접촉)'라고 답한 사람 — '따로 피할 것은 없습니다'가 아니다."""
        res, md, quest, classic, scr = self._cat(["dog"])
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "frequent" and an["low"]
        sec = _section(md, PREV_TITLE)
        for text in (sec, quest, classic):
            assert FREQUENT_HEAD in text and OWNER_HEAD not in text and NON_OWNER_HEAD not in text
            assert "만진 뒤에는 손을 씻고 옷을 갈아입으세요" in text
            assert "고양이 가까이에서 되풀이되면 진료를 받아 다시 평가하세요. 필요하면 재검사를 합니다" in text
            assert "따로 피할 것은 없" not in text and "따로 바꿀 것은 없" not in text
        assert "고양이를 키우지는 않지만 자주 접촉한다고 하셨습니다" in sec
        assert "접촉이 잦은 만큼, 실천할 수 있는 범위에서 노출을 줄여 두면 도움이 될 수 있습니다" in sec
        assert "새로 기르거나 동물을 다루는 일을 시작하려면 그 전에 진료에서 상의하세요" in sec

    def test_frequent_contact_with_unknown_ownership_does_not_guess_ownership(self):
        res, md, _quest, _classic, scr = self._cat([])
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "frequent"
        sec = _section(md, PREV_TITLE)
        assert "고양이를 키우거나 자주 접촉한다고 하셨습니다" in sec
        assert "함께 살고 계십니다" not in sec and "키우지는 않지만" not in sec

    def test_non_owner_without_a_contact_answer_gets_a_different_message(self):
        res, md, scr = self._direct(["dog"])
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "non_owner" and an["low"] == an["optional"] == []
        sec = _section(md, PREV_TITLE)
        assert NON_OWNER_HEAD in sec and OWNER_HEAD not in sec and "침실에는 들이지 말고" not in sec
        assert "고양이를 키우지 않는다고 하셨습니다" in sec
        assert "새로 기르거나, 동물을 다루는 일을 시작하거나" in sec and "그 전에 진료에서 상의하세요" in sec

    def test_unknown_ownership_does_not_guess(self):
        res, md, scr = self._direct([])
        an = cg.sensitized_prevention(res.assessments, scr)["animals"][0]
        assert an["status"] == "unknown"
        sec = _section(md, PREV_TITLE)
        assert "함께 사는지는 문진에서 확인되지 않았습니다" in sec
        assert "함께 살고 계십니다" not in sec and "키우지 않는다고 하셨습니다" not in sec

    def test_clinically_relevant_animal_gets_only_the_existing_guidance(self):
        res, md, quest, classic, scr = self._cat(["cat"], worse="yes")
        assert {a.allergen_name: a.relevance for a in res.assessments}["Cat dander"] == REL
        assert cg.sensitized_prevention(res.assessments, scr) is None
        for text in (md, quest, classic):
            assert OWNER_HEAD not in text and NON_OWNER_HEAD not in text and "🌱 예방과 관찰" not in text
            assert "함께 살고 있다면 — 집 안 노출을 단계적으로 줄입니다" in text, "기존 동물 관리 안내"

    def test_same_species_already_relevant_is_left_to_the_existing_guidance(self):
        scr = _scr(pets=["cat"])
        res = get_relevance_service().build_assessments(
            _ocr([CAT, ("Cat epithelium", "고양이 상피"), ("Dog dander", "개 비듬")]), scr)
        by = {a.allergen_name: a for a in res.assessments}
        by["Cat dander"].relevance = REL
        for other in res.assessments:
            if other is not by["Cat dander"]:
                other.relevance = SENS
        out = cg.sensitized_prevention(res.assessments, scr)
        assert all(an["species"] != "고양이" for an in out["animals"]) if out else True

    def test_card_kinds_stay_within_the_existing_shell(self):
        scr = _scr(allergic_diseases=["asthma"], organ_systems=["lower_airway"], pets=["cat"],
                   current_medications=["inhaled_steroid"])
        res = get_relevance_service().build_assessments(_ocr([MITE, CAT, RAGWEED]), scr)
        engine = get_questionnaire_engine()
        engine.build(res, scr)
        keys = {a.allergen_name: _key(i) for i, a in enumerate(res.assessments)}
        engine.classify(res, {**_animal("Cat dander", "yes", "no")(keys), "pollen_season__fall_weed": "no"}, scr)
        html_text = get_cardnews_service().generate_html(res, {"name": "t"}, scr)
        kinds = re.findall(r'<div class="card" data-kind="([a-z]+)"', html_text)
        assert "other" not in kinds and kinds[0] == "cover" and kinds[-1] == "closing"
        assert [k for k in kinds if k in ("relevant", "sensitized", "prevention", "treatment")] == \
            ["relevant", "sensitized", "prevention", "treatment"]
        # 예방(일반) → 동물 실천(체크 목록) 카드는 '감작만' 카드 바로 뒤, 증상 카드는 치료 카드 앞, 약 카드는 뒤
        i = kinds.index("sensitized")
        assert kinds[i + 1:i + 3] == ["detail", "animal"]
        t = kinds.index("treatment")
        assert kinds[t - 1] == "detail" and kinds[t + 1] == "detail"
        assert html_text.count("<script") == 1


# ---------------------------------------------------------------------------
# ⑥ 자유 기재 칸의 HTML 주입
# ---------------------------------------------------------------------------
PAYLOADS = ['<img src=x onerror=alert(1)>', '"><script>alert(1)</script>', '[x](javascript:alert(1))',
            'a|b\n\n# h\n{: onclick=alert(1) }']


class _Active(HTMLParser):
    def __init__(self):
        super().__init__()
        self.found, self.scripts, self._in = [], [], False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._in = True
            self.scripts.append("")
        if tag in ("img", "iframe", "object", "embed", "form"):
            self.found.append(tag)
        for k, v in attrs:
            if k.startswith("on") or (k in ("href", "src") and str(v).strip().lower().startswith("javascript:")):
                self.found.append((tag, k))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in = False

    def handle_data(self, data):
        if self._in:
            self.scripts[-1] += data


class TestInjection:
    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_payload_in_free_text_and_allergen_names_stays_inert(self, payload):
        scr = ScreeningProfile(
            allergic_diseases=["allergic_rhinitis", "asthma", payload], disease_other=payload,
            organ_systems=["nasal", "lower_airway", payload],
            current_medications=["nasal_steroid", "inhaled_steroid", payload], medication_note=payload,
            triggers_free_text=payload, pets=["cat", "other"], pets_other=payload, notes=payload,
            residence_country="KR")
        rows = [(f"Mite {payload}", f"진드기 {payload}"), (f"Cat {payload}", f"고양이 {payload}"),
                (f"Weed {payload}", f"잡초 {payload}")]
        res = get_relevance_service().build_assessments(_ocr(rows), scr)
        res.assessments[0].relevance = REL
        res.assessments[0].category = "mite"
        res.assessments[1].relevance = SENS
        res.assessments[1].category = "animal"
        res.assessments[2].relevance = ClinicalRelevance.INDETERMINATE
        res.assessments[2].category = "pollen_weed"

        # 세 묶음이 모두 만들어지고, 그 안에 payload 가 실제로 들어 있어야 이 테스트가 의미가 있다
        parts = {"md": "\n\n".join([cg.prevention_md(res.assessments, scr), cg.observation_md(res.assessments, scr),
                                    cg.symptoms_md(res.assessments, scr), cg.medication_md(res.assessments, scr)]),
                 "cards": "".join(cg.prevention_cards(res.assessments, scr) + cg.symptom_cards(res.assessments, scr)
                                  + cg.medication_cards(res.assessments, scr))}
        assert parts["md"].count("alert(") >= 4 and parts["cards"].count("alert(") >= 4
        md = parts["md"]
        for raw in ("<img", "<script", "<a ", "](javascript:", "{:"):
            assert raw not in md, raw
        assert not any(line.lstrip().startswith("# h") for line in md.split("\n"))
        p = _Active()
        p.feed(md_lib.markdown(md, extensions=["extra", "sane_lists"]))
        assert p.found == [] and p.scripts == []
        p = _Active()
        p.feed(parts["cards"])
        assert p.found == [] and p.scripts == []

        info = {"name": payload}
        svc = get_report_service()
        full_md = svc.build_patient_report_markdown(res, info, scr)
        assert MED_TITLE in full_md and PREV_TITLE in full_md
        for doc in (md_lib.markdown(full_md, extensions=["extra", "sane_lists"]),
                    svc.build_patient_report_html_document(res, info, scr)):
            p = _Active()
            p.feed(doc)
            assert p.found == [] and p.scripts == []
        # 카드뉴스의 스크립트는 덱마다 하나이고, 그 본문은 코드에 적힌 고정 문자열과 글자 하나까지 같다.
        # ('스크립트가 하나'만 보면 그 하나에 환자 값이 섞여도 통과한다 — 본문을 통째로 견준다)
        from services.cardnews_service import CardNewsService
        from services.cardnews_classic import ClassicCardNewsService
        fixed = {get_cardnews_service(): CardNewsService._CHECK_JS + CardNewsService._DECK_JS,
                 get_classic_cardnews_service(): CardNewsService._CHECK_JS + ClassicCardNewsService._SHELL_JS}
        for service, script in fixed.items():
            html_text = service.generate_html(res, info, scr)
            p = _Active()
            p.feed(html_text)
            assert p.found == []
            assert html_text.count("<script") == 1 and "".join(p.scripts) == script
            assert "alert(" not in script and "alert(" in html_text, "payload 는 글자로만 보인다"

    def test_unknown_codes_are_ignored(self):
        scr = _scr(allergic_diseases=["<b>x</b>"], organ_systems=["<i>y</i>"], current_medications=["<u>z</u>"])
        res = get_relevance_service().build_assessments(_ocr([MITE]), scr)
        assert cg.medication_guidance(res.assessments, scr) is None
        assert cg.symptom_guidance(res.assessments, scr) == []


# ---------------------------------------------------------------------------
# ⑦ 자료 파일 — 문장마다 출처, 출처마다 읽은 범위
# ---------------------------------------------------------------------------
class TestDataFiles:
    def _load(self, name):
        return json.loads((BASE_DIR / "data" / name).read_text(encoding="utf-8"))

    def test_prevention_evidence_table_is_complete(self):
        d = self._load("sensitization_prevention.json")
        assert d["review_status"].startswith("candidate")
        for key, s in d["sources"].items():
            assert re.fullmatch(r"\d{7,8}", s["pmid"]) and s["doi"].startswith("10."), key
            assert s["read"].startswith(("PMC 전문", "PubMed 초록")) and s["finding_ko"], key
        used = set()
        for e in d["evidence"]:
            assert e["strength"] in d["evidence_levels"] and e["claim_ko"] and e["limit_note_ko"]
            for k in e["supports"] + e["limits"]:
                assert k in d["sources"], k
                used.add(k)
        assert used == set(d["sources"]), set(d["sources"]) - used
        by_id = {e["id"]: e for e in d["evidence"]}
        # 근거가 받쳐 주지 않는 주장은 '증명되지 않음'으로 남아 있어야 한다
        assert by_id["exposure_reduction_prevents_onset"]["strength"] == "unproven"
        assert by_id["pet_keeping_and_prevention"]["strength"] == "unproven"
        assert by_id["sensitization_predicts_rhinitis"]["strength"] == "established"
        P = d["patient_text"]
        for ref in P["lead_evidence"] + P["inhalant"]["evidence"] + P["animal"]["evidence"]:
            assert ref in by_id

    def test_treatment_statements_carry_a_basis(self):
        d = self._load("treatment_guidance.json")
        assert set(d["medications"]) == set(cg.MEDICATION_ORDER)
        from models.schemas import MEDICATION_OPTIONS, ORGAN_SYSTEM_OPTIONS
        assert set(cg.MEDICATION_ORDER) == set(MEDICATION_OPTIONS) - {"none"}, "문진 선택지와 같아야 한다"
        assert set(d["symptoms"]) == set(ORGAN_SYSTEM_OPTIONS) == set(cg.ORGAN_ORDER)
        stmts = []
        for m in d["medications"].values():
            stmts += [x for x in [m.get("general"), m.get("consistency"), m.get("consistency_unmatched"),
                                  *m.get("extra", []), *m.get("diseases", {}).values()] if x]
        for s in d["symptoms"].values():
            stmts += [s["how"], *s["selfcare"], *s["drugs"], *s["drug_notes"]]
        for st in stmts:
            assert st["basis"] in ("guideline", "study", "expert_practice"), st
            assert all(k in d["sources"] for k in st["sources"]), st
            if st["basis"] != "expert_practice":
                assert st["sources"], ("지침·연구 근거라고 적은 문장에는 출처가 있어야 한다", st["text_ko"])
        for key, s in d["sources"].items():
            assert s["read"].startswith(("PMC 전문", "PubMed 초록")) and s["pmid"] and s["doi"], key


# ---------------------------------------------------------------------------
# 상담 추천 질문 — 키 없이 같은 문장으로 답한다
# ---------------------------------------------------------------------------
class TestChatSuggestions:
    def test_keyless_answers_for_the_new_topics(self):
        scr = _scr(allergic_diseases=["asthma"], organ_systems=["lower_airway"], pets=["cat"],
                   current_medications=["inhaled_steroid"], medication_note="<b>메모</b>")
        res, _md, _q, _c = _run([MITE, CAT], _animal("Cat dander", "yes", "no"), scr)
        sg = {s["key"]: s for s in ResultChatService(api_key="").suggestions(res, "ko", screening=scr)}
        med, prev = sg["medication"]["answer"], sg["sensitized_prevention"]["answer"]
        assert "흡입 스테로이드:" in med and "집먼지진드기에 노출될 때" in med and "진료에서 상의하세요" in med
        assert "메모" not in med, "환자가 쓴 글은 답에 넣지 않는다"
        assert not TestMedicationGuidance.FORBIDDEN.search(med)
        assert "고양이와 함께 살고 계십니다" in prev and "증명되지는 않았습니다" in prev
        assert "다른 집으로 보내야 한다는 뜻이 아닙니다" in prev

    def test_absent_without_the_inputs(self):
        scr = _scr()
        res, _md, _q, _c = _run([MITE], {"indoor_timing": "yes"}, scr)
        keys = [s["key"] for s in ResultChatService(api_key="").suggestions(res, "ko", screening=scr)]
        assert "medication" not in keys and "sensitized_prevention" not in keys
