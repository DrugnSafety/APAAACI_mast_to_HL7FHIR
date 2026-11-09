"""전문의 검토 2차 반영분의 회귀 테스트.

  ① 동물 문진: 접촉 정도(함께 삶 / 자주 / 가끔 / 거의 없음)와 직업 노출을 따로 묻고, 판정·안내·FHIR·상담에 반영한다.
     예전 문진의 답(yes/no)으로 저장된 세션도 그대로 읽는다.
  ② 동물 항원 관리 안내: 추가 문헌 검토(지침 전문) 반영 — 종별 안내, 직장 노출, 근거 강도 표기.
  ③ /api/allergen: 모르는 이름의 한글명을 비워 둔다. Wikipedia 조회는 기본으로 끈다.
  ④ 리포트 PDF: 한글이 글자로 찍힌다(빈 상자가 아니다). 메일 첨부와 내려받기.
  ⑤ /api/questionnaire·/api/chat 응답의 translation 집계.
  ⑥ 지식 항목과 레지스트리의 이름 정합(콩 → 대두).
  ⑦ /review: 승인·수정·삭제가 임시 지식 파일에만 쓰이고, 추적 중인 파일은 바뀌지 않는다.

LLM·SMTP 는 전부 가짜다. 외부 네트워크를 쓰지 않는다.
"""
import hashlib
import io
import json
import re
import shutil
from types import SimpleNamespace

import pytest

import server
from config.settings import BASE_DIR, settings
from models.schemas import (AllergenResult, ClinicalRelevance, InterpretationType, OCRResult, PatientInfo,
                            ScreeningProfile, TestType)
from services import care_guidance_service as cg
from services import knowledge_service as ks_module
from services.exposure_guidance_service import (animal_exposure_note, animal_guidance, animal_species,
                                                avoidance_tips, contact_level, ownership_status)
from services.knowledge_service import KnowledgeService, get_knowledge_service
from services.questionnaire_service import get_questionnaire_engine
from services.relevance_service import get_relevance_service
from services.report_service import get_report_service
from test_security_review_fixes import FakeLLM, _login, _payload, client, smtp  # noqa: F401 — fixture

REL = ClinicalRelevance.CLINICALLY_RELEVANT
SENS = ClinicalRelevance.SENSITIZED_ONLY
IND = ClinicalRelevance.INDETERMINATE
CAT, DOG, HAMSTER, HORSE, COW, MOUSE = (("Cat dander", "고양이 비듬"), ("Dog dander", "개 비듬"), ("Hamster", "햄스터"),
                                         ("Horse dander", "말 비듬"), ("Cow dander", "소 비듬"), ("Mouse", "생쥐"))
ANIMAL_DATA = json.loads((BASE_DIR / "data" / "animal_allergen_management.json").read_text(encoding="utf-8"))
PREVENTION = json.loads((BASE_DIR / "data" / "sensitization_prevention.json").read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _no_web_lookup():
    svc = get_knowledge_service()
    before, svc.enable_web = svc.enable_web, False
    yield
    svc.enable_web = before


def _ocr(rows):
    return OCRResult(test_type=TestType.MAST, patient=PatientInfo(name="홍길동", age=32, gender="M"), results=[
        AllergenResult(index=i + 1, raw_text=en, allergen_name=en, korean_name=ko, value=5.0, unit="kU/L",
                       class_value=3, interpretation=InterpretationType.POSITIVE)
        for i, (en, ko) in enumerate(rows)])


def _run(rows, answers, **screening):
    """answers 의 키는 (영문 항원명, 문항 접두사) 또는 전역 문항 id. 값이 항원명 목록이면 문진 키로 바꾼다."""
    scr = ScreeningProfile(**{"allergic_diseases": ["allergic_rhinitis"], "organ_systems": ["nasal"], **screening})
    res = get_relevance_service().build_assessments(_ocr(rows), scr)
    keys = {a.allergen_name: f"agn{i}" for i, a in enumerate(res.assessments)}
    flat = {}
    for k, v in answers.items():
        if isinstance(k, tuple):
            flat[f"{k[1]}__{keys[k[0]]}"] = v
        elif k == "animal_work_animals" and isinstance(v, list):
            flat[k] = [keys.get(x, x) for x in v]
        else:
            flat[k] = v
    engine = get_questionnaire_engine()
    q = engine.build(res, scr)
    engine.classify(res, flat, scr)
    return res, scr, q, flat


def _one(rows, answers, **screening):
    res, scr, q, flat = _run(rows, answers, **screening)
    return res.assessments[0], scr


# ============================================================
# ① 동물 문진 — 접촉 정도와 직업 노출
# ============================================================
class TestAnimalQuestions:
    def test_contact_question_separates_living_together_from_frequency(self):
        _, _, q, _ = _run([CAT, DOG], {})
        animal = next(s for s in q["sections"] if s["id"] == "animal")
        by_id = {x["id"]: x for x in animal["questions"]}
        contact = by_id["animal_contact__agn0"]
        assert [o["value"] for o in contact["options"]] == ["live", "frequent", "occasional", "rare"]
        assert "키우거나 자주" not in json.dumps(animal, ensure_ascii=False), "두 가지를 한 답으로 묻지 않는다"
        freq = by_id["animal_contact_freq__agn0"]
        assert freq["reveal_if"] == {"question": "animal_contact__agn0", "any": ["frequent"]}
        # 직업 질문은 동물별이 아니라 한 번, 동물 절의 끝에
        assert [x["id"] for x in animal["questions"]][-3:] == ["animal_work", "animal_work_animals",
                                                               "animal_work_symptoms"]
        work = by_id["animal_work"]
        assert work["type"] == "multi" and {"vet", "lab", "farm", "pet_shop", "grooming", "care", "none"} <= {
            o["value"] for o in work["options"]}
        which = by_id["animal_work_animals"]
        assert [o["value"] for o in which["options"]] == ["agn0", "agn1", "other"]
        assert which["reveal_if"]["question"] == "animal_work" and "none" not in which["reveal_if"]["includes_any"]
        assert by_id["animal_work_symptoms"]["reveal_if"] == which["reveal_if"]

    def test_occupational_questions_only_when_an_animal_is_positive(self):
        _, _, q, _ = _run([("Dermatophagoides farinae", "집먼지진드기")], {})
        assert "animal_work" not in json.dumps(q)

    def test_prefill_uses_the_new_values(self):
        _, _, q, _ = _run([CAT, DOG], {}, pets=["cat"])
        assert q["answer_prefill"] == {"animal_contact__agn0": "live"}
        _, _, q, _ = _run([CAT], {}, pets=["none"])
        assert q["answer_prefill"] == {}, "'키우는 동물 없음'은 접촉이 없다는 뜻이 아니다"

    @pytest.mark.parametrize("contact,worse,expected", [
        ("live", "yes", REL), ("frequent", "yes", REL), ("occasional", "yes", REL), ("rare", "yes", REL),
        ("live", "no", SENS), ("frequent", "no", SENS), ("occasional", "no", SENS),
        ("rare", "no", IND), ("rare", "unsure", IND), ("live", "unsure", IND), (None, "no", IND),
        # 예전 문진의 답 — 판정은 예전과 같다
        ("yes", "yes", REL), ("yes", "no", SENS), ("no", "no", IND), ("no", "yes", REL), ("yes", "unsure", IND),
    ])
    def test_verdicts(self, contact, worse, expected):
        answers = {(CAT[0], "animal_worse"): worse}
        if contact:
            answers[(CAT[0], "animal_contact")] = contact
        a, _ = _one([CAT], answers)
        assert a.relevance == expected, a.rationale_ko

    def test_occupational_handling_counts_as_exposure(self):
        # 집에서는 거의 만나지 않지만 일하면서 다룬다 → 노출은 충분하다. 증상 악화가 없으면 감작만
        a, _ = _one([CAT], {(CAT[0], "animal_contact"): "rare", (CAT[0], "animal_worse"): "no",
                            "animal_work": ["vet"], "animal_work_animals": [CAT[0]], "animal_work_symptoms": "no"})
        assert a.relevance == SENS and "일하면서 다루는데도" in a.rationale_ko
        assert a.answers["animal_occupational"] == "yes" and a.answers["animal_work_types"] == "vet"

    def test_work_related_symptoms_are_not_called_sensitized_only(self):
        a, _ = _one([CAT], {(CAT[0], "animal_contact"): "rare", (CAT[0], "animal_worse"): "no",
                            "animal_work": ["vet", "grooming"], "animal_work_animals": [CAT[0]],
                            "animal_work_symptoms": "yes"})
        assert a.relevance == IND and "직업과 관련된 알레르기인지 평가" in a.rationale_ko
        assert "감작만" not in a.rationale_ko

    def test_work_with_other_animals_does_not_change_this_animal(self):
        base, _ = _one([CAT], {(CAT[0], "animal_contact"): "rare", (CAT[0], "animal_worse"): "no"})
        a, _ = _one([CAT], {(CAT[0], "animal_contact"): "rare", (CAT[0], "animal_worse"): "no",
                            "animal_work": ["farm"], "animal_work_animals": ["other"], "animal_work_symptoms": "yes"})
        assert a.relevance == base.relevance == IND and a.rationale_ko == base.rationale_ko
        assert a.answers["animal_occupational"] == "no" and "animal_work_symptoms" not in a.answers

    def test_none_work_answer_is_not_occupational(self):
        a, _ = _one([CAT], {(CAT[0], "animal_contact"): "live", (CAT[0], "animal_worse"): "no",
                            "animal_work": ["none"], "animal_work_animals": [CAT[0]]})
        assert a.answers["animal_occupational"] == "no" and a.relevance == SENS

    def test_scalar_and_garbage_answers_do_not_crash(self):
        a, _ = _one([CAT], {(CAT[0], "animal_contact"): ["live"], (CAT[0], "animal_worse"): "no",
                            "animal_work": "vet", "animal_work_animals": "agn0", "animal_work_symptoms": ["x"]})
        assert a.relevance == SENS and a.answers["animal_occupational"] == "yes"
        assert "animal_contact" not in a.answers and "animal_work_symptoms" not in a.answers


class TestAnimalStatus:
    @pytest.mark.parametrize("contact,pets,status", [
        ("live", [], "owner"), ("live", ["none"], "owner"), ("frequent", ["cat"], "frequent"),
        ("occasional", [], "occasional"),
        ("yes", ["cat"], "owner"), ("yes", ["none"], "frequent"), ("yes", [], "frequent"),      # 예전 답
    ])
    def test_sensitized_only_statuses(self, contact, pets, status):
        a, scr = _one([CAT], {(CAT[0], "animal_contact"): contact, (CAT[0], "animal_worse"): "no"}, pets=pets)
        assert a.relevance == SENS and cg._animal_status(a, scr)[0] == status

    @pytest.mark.parametrize("contact,worse,status", [
        ("rare", "no", "no_contact"), ("no", "no", "no_contact"), ("live", "unsure", "unsure"),
        ("occasional", "unsure", "unsure"), ("yes", "unsure", "unsure"), (None, None, "no_info"),
    ])
    def test_indeterminate_statuses(self, contact, worse, status):
        answers = {}
        if contact:
            answers[(CAT[0], "animal_contact")] = contact
        if worse:
            answers[(CAT[0], "animal_worse")] = worse
        a, scr = _one([CAT], answers)
        assert a.relevance == IND and cg._animal_status(a, scr)[0] == status

    def test_occupational_status_wins_and_uses_the_progression_evidence(self):
        res, scr, _, _ = _run([CAT], {(CAT[0], "animal_contact"): "live", (CAT[0], "animal_worse"): "no",
                                      "animal_work": ["lab"], "animal_work_animals": [CAT[0]],
                                      "animal_work_symptoms": "no"}, pets=["cat"])
        out = cg.sensitized_prevention(res.assessments, scr)
        (an,) = out["animals"]
        assert an["status"] == "occupational" and an["work"] is True
        text = " ".join(an["lines"])
        # 직업 노출에서 실제로 확인된 근거(실험동물 종사자 코호트)를, 그 한계와 함께 쓴다
        for phrase in ("약 4배", "폐기능이 더 빨리", "첫 2~3년", "약 30%", "약 9%",
                       "실험동물(주로 쥐) 연구여서 다른 동물과 직종에 그대로 들어맞는 수치는 아닙니다",
                       "일을 그만두어야 한다는 뜻이 아닙니다", "임상시험으로 증명되지는 않았지만"):
            assert phrase in text, phrase
        assert an["low"] == ["직장에서 환기 설비·보호구·작업 방식으로 노출 줄이기", "작업복은 직장에서 갈아입고 머리와 몸 씻기"]
        assert any("일하는 날 심해지고" in w for w in an["watch"]) and "하는 일을 꼭 알려" in an["retest"]
        assert "EAACI 직업성 비염 입장문" in out["source_line"]
        md = cg.prevention_md(res.assessments, scr)
        assert "일하면서 다루는 고양이" in md and "막습니다" not in md and "권합니다" not in md
        assert any("일하면서 다루는 고양이" in c for c in cg.prevention_cards(res.assessments, scr))

    def test_occupational_watch_states_the_work_pattern_only_when_reported(self):
        base = {(MOUSE[0], "animal_contact"): "rare", "animal_work": ["lab"], "animal_work_animals": [MOUSE[0]]}
        res, scr, _, _ = _run([MOUSE], {**base, (MOUSE[0], "animal_worse"): "no", "animal_work_symptoms": "yes"})
        (an,) = cg.sensitized_prevention(res.assessments, scr)["watch_animals"]
        assert an["status"] == "occupational_watch" and "쉬는 날 좋아진다고 하셨습니다" in an["lines"][0]
        assert "생쥐를 일하면서" in an["lines"][0], "종 이름과 조사가 맞아야 한다"
        res, scr, _, _ = _run([MOUSE], {**base, (MOUSE[0], "animal_worse"): "unsure", "animal_work_symptoms": "no"})
        (an,) = cg.sensitized_prevention(res.assessments, scr)["watch_animals"]
        assert an["status"] == "occupational_watch" and "쉬는 날 좋아진다고" not in an["lines"][0]
        assert "분명하지 않다고 하셨습니다" in an["lines"][0]

    def test_occasional_contact_gets_no_statistics_and_no_reduction_advice(self):
        res, scr, _, _ = _run([DOG], {(DOG[0], "animal_contact"): "occasional", (DOG[0], "animal_worse"): "no"})
        out = cg.sensitized_prevention(res.assessments, scr)
        (an,) = out["animals"]
        assert an["status"] == "occasional" and not an["why"] and not an["low"] and not an["optional"]
        assert "가끔 만난다고 하셨습니다" in an["lines"][0] and "약 4배" not in " ".join(an["lines"])
        assert PREVENTION["patient_text"]["lead_reduce_ko"] not in out["lead"]

    def test_every_status_has_text_and_cited_evidence_exists(self):
        A = PREVENTION["patient_text"]["animal"]
        for status in ("owner", "frequent", "occasional", "occupational", "non_owner", "unknown",
                       "occupational_watch", "no_contact", "unsure", "no_info"):
            assert {"headline_ko", "status_ko", "do_ko", "card_ko", "low_keys", "optional_keys"} <= set(A[status])
        ids = {e["id"] for e in PREVENTION["evidence"]}
        assert set(A["evidence"]) <= ids
        for e in PREVENTION["evidence"]:
            assert set(e["supports"] + e["limits"]) <= set(PREVENTION["sources"]), e["id"]
        for key in ("aaaai_furry_2012", "aaaai_rodent_2012", "moscato_2009"):
            assert PREVENTION["sources"][key]["read"].startswith("PMC 전문")
        steps = {s["key"] for s in ANIMAL_DATA["occupational"]["steps"]}
        for block in A.values():
            if isinstance(block, dict):
                for grp, key in block.get("low_keys", []) + block.get("optional_keys", []):
                    assert key in {s["key"] for s in ANIMAL_DATA[grp]["steps"]}, (grp, key)
        assert {"work_controls", "work_clothes"} <= steps


# ============================================================
# ② 동물 항원 관리 안내(실제 주의)
# ============================================================
def _animal(en, ko, **answers):
    return SimpleNamespace(allergen_name=en, korean_name=ko, category="animal", answers=answers,
                           relevance=REL)


class TestAnimalGuidance:
    def test_contact_answer_decides_ownership_before_the_pets_answer(self):
        assert ownership_status(_animal(*CAT, animal_contact="live"), ScreeningProfile(pets=["none"])) == "owner"
        assert ownership_status(_animal(*CAT, animal_contact="frequent"), ScreeningProfile(pets=["cat"])) == "non_owner"
        assert ownership_status(_animal(*CAT, animal_contact="rare"), ScreeningProfile()) == "non_owner"
        # 예전 답은 반려동물 답으로 정한다
        assert ownership_status(_animal(*CAT, animal_contact="yes"), ScreeningProfile(pets=["cat"])) == "owner"
        assert ownership_status(_animal(*CAT, animal_contact="yes"), ScreeningProfile(pets=["dog"])) == "non_owner"
        assert ownership_status(_animal(*CAT, animal_contact="no"), ScreeningProfile()) == "unknown"
        assert contact_level(_animal(*CAT, animal_contact="yes")) == "legacy_yes"
        assert contact_level(_animal(*CAT, animal_contact="no")) == "rare"
        assert contact_level(_animal(*CAT)) == ""

    def test_species_are_recognised_from_registry_names(self):
        names = {"cat": CAT, "dog": DOG, "hamster": HAMSTER, "horse": HORSE, "cow": COW, "mouse": MOUSE,
                 "rat": ("Rat epithelium", "쥐 상피"), "rabbit": ("Rabbit epithelium", "토끼 상피"),
                 "guinea_pig": ("Guinea pig", "기니피그")}
        for species, (en, ko) in names.items():
            assert animal_species(_animal(en, ko)) == species
            assert species in ANIMAL_DATA["species"], species
        # 한 글자 낱말이 다른 이름에 걸리지 않는다
        assert animal_species(_animal("Maltese", "말티즈")) == "other"

    def test_caged_pets_get_cage_advice_and_no_bathing(self):
        g = animal_guidance(_animal(*HAMSTER, animal_contact="live"), ScreeningProfile())
        text = " ".join(s["text"] for s in g["steps"])
        assert g["status"] == "owner" and "케이지는 침실 밖" in text and "깔짚" in text
        assert "목욕" not in text, "햄스터 주인에게 목욕을 안내하지 않는다"
        assert any("종에 따라 알레르겐이 다릅니다" in t for t in g["extra"])
        non = animal_guidance(_animal(*HAMSTER, animal_contact="occasional"), ScreeningProfile())
        assert "케이지" not in " ".join(s["text"] for s in non["steps"])

    def test_horse_and_cow_get_their_own_exposure_setting(self):
        g = animal_guidance(_animal(*HORSE, animal_contact="frequent"), ScreeningProfile(pets=["none"]))
        text = " ".join(s["text"] for s in g["steps"])
        assert "마구간" in g["headline"] and "약 500배" in text and "컬리 호스" in text
        assert "침실에는 들이지" not in text and "고양이가 없는 집" not in text
        cow = animal_guidance(_animal(*COW, animal_contact="live", animal_occupational="yes",
                                      animal_work_types="farm"), ScreeningProfile())
        text = " ".join(s["text"] for s in cow["steps"])
        assert "축사" in cow["headline"] and "작업복은 생활 공간 밖" in text and "약 1,000배" in text
        assert cow["occupational"] is None, "소의 안내가 이미 직업 노출을 다룬다 — 같은 말을 두 번 싣지 않는다"

    def test_occupational_block_is_added_for_handled_animals_only(self):
        scr = ScreeningProfile(pets=["cat"])
        plain = animal_guidance(_animal(*CAT, animal_contact="live", animal_occupational="no"), scr)
        assert plain["occupational"] is None
        g = animal_guidance(_animal(*CAT, animal_contact="live", animal_occupational="yes",
                                    animal_work_types="vet"), scr)
        work = g["occupational"]
        assert work["headline"].startswith("일하면서 다루는 동물") and len(work["steps"]) == 4
        text = " ".join(s["text"] for s in work["steps"])
        for phrase in ("직업성 비염", "오염된 깔짚", "머리덮개", "일자리를 잃을 위험"):
            assert phrase in text, phrase
        assert "그만두" not in text, "일을 그만두라고 쓰지 않는다"
        tips = avoidance_tips(_animal(*CAT, animal_contact="live", animal_occupational="yes"), scr)
        assert tips[0] == "직장에서 환기 설비·보호구·작업 방식으로 노출 줄이기"

    def test_report_and_cards_show_the_workplace_section(self):
        res, scr, _, _ = _run([CAT], {(CAT[0], "animal_contact"): "live", (CAT[0], "animal_worse"): "yes",
                                      "animal_work": ["vet"], "animal_work_animals": [CAT[0]],
                                      "animal_work_symptoms": "yes"}, pets=["cat"])
        assert res.assessments[0].relevance == REL and "하는 일을 알려 주세요" in res.assessments[0].rationale_ko
        md = get_report_service().build_patient_report_markdown(res, {"name": "홍길동"}, scr)
        assert "**일하면서 다루는 동물 — 직장 노출부터 줄입니다**" in md and "함께 살고 있다면" in md
        assert "일하면서 그 동물을 다루는 동안, 그리고 집 안에서 함께 지내는 내내" in md
        from services.cardnews_classic import get_classic_cardnews_service
        from services.cardnews_service import get_cardnews_service
        for svc in (get_cardnews_service(), get_classic_cardnews_service()):
            html = svc.generate_html(res, {"name": "홍길동"}, scr)
            assert "고양이 비듬 · 직장" in html and "직업성 천식보다 먼저" in html

    def test_reviewed_statements_match_what_the_guidelines_say(self):
        owner = {s["key"]: s for s in ANIMAL_DATA["owner"]["steps"]}
        non = {s["key"]: s for s in ANIMAL_DATA["non_owner"]["steps"]}
        # 옷 갈아입기는 지침 권고(등급 C)로 출처가 생겼다. 손 씻기는 여전히 진료 관행이다
        assert "aaaai_furry_2012" in owner["after_contact"]["sources"]
        assert "expert_practice" in owner["after_contact"]["basis_note_ko"]
        # 목욕: 임상 효과는 증명되지 않았다. '알레르기 없는 가족'은 진료 관행으로 표시
        assert "증명되지 않았습니다" in owner["washing"]["text_ko"]
        assert "expert_practice" in owner["washing"]["basis_note_ko"]
        # 방문 전 약: 임상시험 1건(간접) — 약 이름·용량을 환자 문장에 적지 않는다
        before = non["before_visit"]
        assert before["strength"] == "limited" and "berkowitz_2006" in before["sources"]
        for word in ("펙소페나딘", "fexofenadine", "mg", "180"):
            assert word not in before["text_ko"]
        assert "진료에서 정해 두세요" in before["text_ko"]
        # 제조사 연구는 그렇게 적는다. 수컷·암컷 개 연구는 '암컷은 괜찮다'로 쓰지 않는다
        cat_notes = " ".join(n["text_ko"] for n in ANIMAL_DATA["species"]["cat"]["notes"])
        dog_notes = " ".join(n["text_ko"] for n in ANIMAL_DATA["species"]["dog"]["notes"])
        assert "제조사 연구" in cat_notes and "대신할 수 없습니다" in cat_notes
        assert "소규모 연구" in dog_notes and "암컷은 괜찮" not in dog_notes
        assert "저알레르기" in cat_notes and "저알레르기" in dog_notes
        # 전문을 읽은 지침은 그렇게 적혀 있다
        for key in ("aaaai_furry_2012", "aaaai_rodent_2012", "naepp_2020", "kaaaci_ar_2023", "moscato_2009"):
            assert "PMC 전문" in ANIMAL_DATA["sources"][key]["verified"], key
        assert all("초록" in ANIMAL_DATA["sources"][k]["verified"] for k in ("wood_1989", "avner_1997", "hodson_1999"))


class TestExposureNote:
    def test_note_for_fhir_and_chat(self):
        a = _animal(*CAT, animal_contact="frequent", animal_contact_freq="daily", animal_occupational="yes",
                    animal_work_types="vet,grooming", animal_work_symptoms="yes")
        note = animal_exposure_note(a)
        assert note == ("함께 살지는 않지만 자주 접촉(주 1회 이상) — 거의 매일; 직업적으로 다룸(동물병원·수의 진료, "
                        "미용·호텔·훈련), 일하는 날 증상이 심해지고 쉬는 날 좋아짐")
        assert animal_exposure_note(_animal(*CAT, animal_contact="yes")) == "키우거나 자주 접촉"
        assert animal_exposure_note(_animal(*CAT)) == ""

    def test_fhir_and_chat_context_carry_the_exposure(self, client):
        body = _payload(client, answers={"animal_contact__agn3": "frequent", "animal_contact_freq__agn3": "weekly",
                                         "animal_worse__agn3": "yes", "animal_work": ["pet_shop"],
                                         "animal_work_animals": ["agn3"], "animal_work_symptoms": "unsure"})
        bundles = client.post("/api/fhir", json=body).json()
        text = json.dumps(bundles, ensure_ascii=False)
        assert "노출 상황(환자 문진): 함께 살지는 않지만 자주 접촉(주 1회 이상) — 주 1회쯤; 직업적으로 다룸(반려동물 판매·분양)" in text
        assert '"linkId": "allergen-q/animal_work"' in text
        from services.result_chat_service import get_result_chat_service
        req = server.ChatRequest(**body)
        res = get_relevance_service().build_assessments(req.ocr, req.screening)
        get_questionnaire_engine().classify(res, req.answers, req.screening)
        ctx = get_result_chat_service().build_context(res, {"name": "홍길동"}, req.screening, req.answers)
        assert "노출 상황(환자 문진): 함께 살지는 않지만 자주 접촉" in ctx
        assert "반려동물 판매·분양" in ctx and "얼마나 접촉하나요?" in ctx


# ============================================================
# ③ 모르는 항원 이름
# ============================================================
class TestUnknownAllergenName:
    def test_unknown_name_has_no_korean_name(self, client):
        r = client.get("/api/allergen", params={"name": "Zzyzx extract"}).json()
        assert r["korean_name"] is None and r["known"] is False and r["canonical_name"] == "Zzyzx extract"
        assert r["category"] == "other" and r["source"] == "category_default"
        assert "Zzyzx extract" in r["biology_ko"] and "준비 중" in r["biology_ko"]

    def test_known_names_still_resolve(self, client):
        r = client.get("/api/allergen", params={"name": "Cat dander"}).json()
        assert r["known"] is True and r["korean_name"] and r["category"] == "animal"
        r = client.get("/api/allergen", params={"name": "콩"}).json()
        assert r["known"] is True and r["canonical_name"] == "Soybean" and r["korean_name"] == "대두"

    def test_unknown_row_keeps_its_own_name_in_the_assessment(self):
        res, _, _, _ = _run([("Zzyzx extract", None)], {})
        (a,) = res.assessments
        assert a.korean_name is None and a.allergen_name == "Zzyzx extract"
        md = get_report_service().build_patient_report_markdown(res, {"name": "홍길동"}, None)
        assert "Zzyzx extract" in md and "None" not in md

    def test_wikipedia_lookup_is_off_by_default_and_filtered_when_on(self, monkeypatch):
        assert settings.allergen_wikipedia_lookup is False
        assert KnowledgeService().enable_web is False
        calls = []

        class Resp:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        def fake_get(url, **kw):
            calls.append(url)
            if "ko.wikipedia" in url:      # 동음이의 문서
                return Resp({"type": "disambiguation", "extract": "말은 다음을 가리킨다."})
            return Resp({"type": "standard", "extract": "A horse is a mammal.",
                         "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Horse"}}})

        import httpx
        monkeypatch.setattr(httpx, "get", fake_get)
        off = KnowledgeService(enable_web=False)
        # ('말'은 2026-10 부터 말 비듬의 별칭이다 — 레지스트리에 없는 이름으로 본다)
        assert off.get_backdata("눈사람")["source"] == "category_default" and calls == []
        on = KnowledgeService(enable_web=True)
        out = on.get_backdata("Qq zz/..%2f")          # 경로 조각이 든 값은 보내지 않는다
        assert out["source"] == "category_default" and calls == []
        out = on.get_backdata("Unknown beast")
        assert out["source"] == "wikipedia" and out["biology_ko"] == "A horse is a mammal."
        assert calls[0].endswith("/Unknown%20beast"), "이름은 URL 인코딩한다"
        assert "다음을 가리킨다" not in out["biology_ko"], "동음이의 문서는 소개문으로 쓰지 않는다"


# ============================================================
# ④ 리포트 PDF
# ============================================================
def _pdf_text(data: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(io.BytesIO(data)).pages)


def _pdf_fonts(data: bytes) -> set:
    pypdf = pytest.importorskip("pypdf")
    names = set()
    for page in pypdf.PdfReader(io.BytesIO(data)).pages:
        for font in (page["/Resources"].get("/Font") or {}).values():
            names.add(str(font.get_object().get("/BaseFont")))
    return names


pdf_engine = pytest.mark.skipif(not __import__("services.report_pdf", fromlist=["x"]).pdf_available(),
                                reason="WeasyPrint(Pango)가 없는 환경")


@pdf_engine
class TestReportPdf:
    def test_download_has_korean_text_not_boxes(self, client):
        out = client.post("/api/classify", json=_payload(client, answers={"indoor_timing": "yes"})).json()
        r = client.get(f"/api/sessions/{out['session_id']}/report.pdf")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/pdf" and r.headers["cache-control"] == "no-store"
        assert r.headers["content-disposition"] == 'attachment; filename="allergy-report-ko.pdf"'
        assert r.content[:5] == b"%PDF-"
        text = _pdf_text(r.content)
        for phrase in ("홍길동님의 맞춤 리포트", "한눈에 보기", "실제 주의", "감작만", "관찰 필요", "집먼지진드기",
                       "검사했고 음성인 항목", "OO대학교병원"):
            assert phrase in text, phrase
        # 한글을 가진 글꼴이 실제로 들어갔다(없으면 서버가 PDF 를 만들지 않는다)
        fonts = " ".join(_pdf_fonts(r.content)).lower()
        assert any(name in fonts for name in ("noto", "nanum", "gothic", "pingfang", "malgun")), fonts
        assert "�" not in text

    def test_same_access_rule_and_language_handling(self, client):
        assert client.get("/api/sessions/nope/report.pdf").status_code == 404
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        assert client.get(f"/api/sessions/{sid}/report.pdf", params={"lang": "fr"}).status_code == 400
        r = client.get(f"/api/sessions/{sid}/report.pdf", params={"lang": "en"})      # 번역이 안 된 언어
        assert r.status_code == 409 and r.json()["detail"]["code"] == "not_ready"
        assert client.get(f"/api/sessions/{sid}/report.pdf", params={"lang": "ko-KR"}).status_code == 200

    def test_translated_languages_render(self, client, monkeypatch):
        FakeLLM(monkeypatch, reply=lambda t: re.sub(r"[가-힣]+", "译文", t))      # 한글 덩어리 → 간체 한자
        out = client.post("/api/classify", json=_payload(client, lang="zh")).json()
        assert out["i18n"]["zh"] == "ready", out.get("translation")
        r = client.get(f"/api/sessions/{out['session_id']}/report.pdf", params={"lang": "zh"})
        assert r.status_code == 200
        text = _pdf_text(r.content)
        assert "译文" in text and "홍길동" in text, "간체 한자와 번역하지 않는 환자 이름이 함께 찍힌다"
        assert "allergy-report-zh.pdf" in r.headers["content-disposition"]

    def test_email_attaches_the_pdf_with_the_html(self, client, smtp):
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "a@b.co", "include": ["report"]})
        assert r.status_code == 200 and r.json()["attachments"] == ["allergy-report-ko.pdf", "allergy-report-ko.html"]
        pdf, html = list(smtp.sent[0].iter_attachments())
        assert pdf.get_content_type() == "application/pdf" and "홍길동님의 맞춤 리포트" in _pdf_text(pdf.get_content())
        body = smtp.sent[0].get_body(("plain",)).get_content()
        assert "PDF" in body and "allergy-report-ko.pdf" in body

    def test_no_pdf_is_sent_when_the_engine_or_font_is_missing(self, client, smtp, monkeypatch):
        from services import report_pdf

        def boom(html, lang="ko"):
            raise report_pdf.PdfUnavailable("no font")
        monkeypatch.setattr(report_pdf, "render_report_pdf", boom)
        sid = client.post("/api/classify", json=_payload(client)).json()["session_id"]
        r = client.get(f"/api/sessions/{sid}/report.pdf")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "pdf_unavailable"
        r = client.post(f"/api/sessions/{sid}/email", json={"email": "a@b.co", "include": ["report"]})
        assert r.status_code == 200 and r.json()["attachments"] == ["allergy-report-ko.html"]

    def test_missing_cjk_font_is_detected(self, monkeypatch):
        from services import report_pdf
        monkeypatch.setattr(report_pdf, "_CJK_FAMILIES", ("no-such-font-family",))
        with pytest.raises(report_pdf.PdfUnavailable):
            report_pdf.render_report_pdf("<html><body><p>한글</p></body></html>")
        assert report_pdf.render_report_pdf("<html><body><p>latin only</p></body></html>")[:5] == b"%PDF-"

    def test_emoji_become_font_independent_marks(self):
        from services.report_pdf import emoji_fallback
        html = ('<html><head><title>🔴 제목</title></head><body><h2 title="⚠️">1️⃣ 한눈에 보기</h2>'
                '<td>🔴 실제 주의</td><p>⚠️ 주의 🐾 동물 → 관리 ✓ 60℃ · ‘인용’</p></body></html>')
        out = emoji_fallback(html)
        head, body = out.split("<body>")
        assert "🔴 제목" in head and 'title="⚠️"' in body, "머리말과 속성은 건드리지 않는다"
        assert '<span class="pdf-num">1</span> 한눈에 보기' in body
        assert '<span class="pdf-dot" style="background:#e5484d"></span> 실제 주의' in body
        assert '<span class="pdf-warn">!</span> 주의' in body and "🐾" not in body
        assert "→ 관리 ✓ 60℃ · ‘인용’" in body, "화살표·기호·단위는 그대로"

    def test_document_cannot_pull_in_files_or_urls(self, monkeypatch):
        import urllib.request
        from services.report_pdf import render_report_pdf
        opened = []
        real_open = urllib.request.OpenerDirector.open

        def spy(self, fullurl, *args, **kwargs):
            opened.append(getattr(fullurl, "full_url", fullurl))
            return real_open(self, fullurl, *args, **kwargs)
        monkeypatch.setattr(urllib.request.OpenerDirector, "open", spy)
        pixel = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA"
                 "60e6kgAAAABJRU5ErkJggg==")
        html = ('<html><body><p>한글 본문</p><img src="file:///etc/passwd"><img src="http://127.0.0.1:9/x.png">'
                f'<link rel="stylesheet" href="file:///etc/hosts"><img src="{pixel}"></body></html>')
        assert render_report_pdf(html)[:5] == b"%PDF-"       # 불러오지 않고 건너뛴다
        assert opened and all(str(u).startswith("data:") for u in opened), opened

    def test_health_reports_pdf_support(self, client):
        assert client.get("/api/health").json()["services"]["pdf"] is True


# ============================================================
# ⑤ translation 집계
# ============================================================
class TestTranslationCounters:
    def test_korean_responses_report_zero(self, client):
        q = client.post("/api/questionnaire", json=_payload(client)).json()
        assert q["translation"] == {"segments": 0, "untranslated": 0, "errors": 0}
        c = client.post("/api/chat", json=_payload(client)).json()
        assert c["translation"] == {"segments": 0, "untranslated": 0, "errors": 0}

    def test_untranslated_content_is_counted_when_no_backend(self, client):
        q = client.post("/api/questionnaire", json=_payload(client, lang="en")).json()
        tr = q["translation"]
        assert set(tr) == {"segments", "untranslated", "errors"}
        assert tr["segments"] > 20 and tr["untranslated"] == tr["segments"] and tr["errors"] == 0
        c = client.post("/api/chat", json=_payload(client, lang="en")).json()
        assert c["suggestions"] and c["translation"]["untranslated"] > 0
        assert c["translation"]["untranslated"] <= c["translation"]["segments"]

    def test_fully_translated_content_reports_no_gap(self, client, monkeypatch):
        FakeLLM(monkeypatch)
        q = client.post("/api/questionnaire", json=_payload(client, lang="en")).json()
        assert q["translation"]["segments"] > 20 and q["translation"]["untranslated"] == 0
        c = client.post("/api/chat", json=_payload(client, lang="zh")).json()
        assert c["translation"]["segments"] > 0 and c["translation"]["untranslated"] == 0

    def test_partial_failure_is_visible(self, client, monkeypatch):
        FakeLLM(monkeypatch, fail=lambda text: "시기 패턴" in text)
        q = client.post("/api/questionnaire", json=_payload(client, lang="en")).json()
        assert 0 < q["translation"]["untranslated"] < q["translation"]["segments"]


# ============================================================
# ⑥ 지식 항목과 레지스트리의 이름 정합
# ============================================================
class TestKnowledgeRegistrySync:
    def test_korean_kong_reaches_soybean(self):
        svc = KnowledgeService(enable_web=False)
        for kwargs in ({"name": "콩"}, {"name": "Unlisted", "korean_name": "콩"}):
            kb = svc.get_backdata(**kwargs)
            assert kb["canonical_name"] == "Soybean" and kb["korean_name"] == "대두", kwargs
        bean = svc.get_backdata("Bean")
        assert bean["canonical_name"] == "Bean" and bean["korean_name"] == "콩류"
        assert svc.get_backdata("콩류")["canonical_name"] == "Bean"

    def test_generated_names_match_the_registry(self):
        registry = {a["canonical_name"]: a for a in json.loads(
            (BASE_DIR / "data" / "allergens.json").read_text(encoding="utf-8"))["antigens"]}
        generated = json.loads((BASE_DIR / "data" / "allergen_knowledge_generated.json").read_text(
            encoding="utf-8"))["entries"]
        for e in generated:
            reg = registry[e["canonical_name"]]
            assert e["korean_name"] == reg["korean_name"], e["canonical_name"]
            assert set(e["aliases"]) == set(reg["aliases"]), e["canonical_name"]
            assert e["category"] == reg["category"], e["canonical_name"]

    def test_canonical_and_korean_names_beat_another_entrys_alias(self, tmp_path):
        p = tmp_path / "kb.json"
        p.write_text(json.dumps({"entries": [
            {"canonical_name": "Cornflour", "korean_name": "옥수수가루", "aliases": [], "category": "food"},
            {"canonical_name": "Maize", "korean_name": "옥수수", "aliases": ["옥수수가루"], "category": "food"},
        ]}, ensure_ascii=False), encoding="utf-8")
        svc = KnowledgeService(enable_web=False, generated_path=p)
        assert svc.lookup_generated("옥수수가루")["canonical_name"] == "Cornflour"


# ============================================================
# ⑦ /review — 승인·수정·삭제를 임시 지식 파일로
# ============================================================
REAL_KB = BASE_DIR / "data" / "allergen_knowledge_generated.json"


@pytest.fixture
def temp_kb(tmp_path):
    """지식 서비스가 임시 복사본(+ 시험용 항목 3건)을 읽고 쓰게 한다. 끝나면 원래 파일로 되돌린다."""
    before = hashlib.sha256(REAL_KB.read_bytes()).hexdigest()
    path = tmp_path / "kb.json"
    data = json.loads(REAL_KB.read_text(encoding="utf-8"))
    for i in (1, 2, 3):
        data["entries"].append({
            "canonical_name": f"Review test item {i}", "korean_name": f"검토시험항목{i}", "aliases": [],
            "category": "other", "profile_key": "other", "source": "category_profile",
            "avoidance_control_ko": ["손대지 않는 템플릿 문장"],
            "biology_ko": f"시험용 소개문 {i}", "biology_ko_status": "candidate", "biology_ko_model": "test-model",
            "biology_ko_generated_at": "2026-10-06T00:00:00+00:00"})
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    svc = get_knowledge_service()
    original = svc.generated_path
    svc.generated_path = path
    svc._load_generated()
    yield path
    svc.generated_path = original
    svc._load_generated()
    assert hashlib.sha256(REAL_KB.read_bytes()).hexdigest() == before, "추적 중인 지식 파일이 바뀌었습니다"


def _entry(path, name):
    return next(e for e in json.loads(path.read_text(encoding="utf-8"))["entries"] if e["canonical_name"] == name)


class TestReviewFlow:
    def test_path_is_configurable(self):
        assert ks_module.GENERATED_KB_PATH == settings.knowledge_generated_path == REAL_KB
        assert "KNOWLEDGE_GENERATED_PATH" in (BASE_DIR / "config" / "env_sample.txt").read_text(encoding="utf-8")

    def test_unauthenticated_calls_are_refused_and_write_nothing(self, client, temp_kb, monkeypatch):
        before = temp_kb.read_bytes()
        body = {"name": "Review test item 1", "action": "approve"}
        assert client.post("/api/knowledge/review", json=body).status_code == 503        # 관리자 기능 꺼짐
        monkeypatch.setattr(settings, "admin_password", "s3cret-pw")
        assert client.get("/api/knowledge/candidates").status_code == 401
        assert client.post("/api/knowledge/review", json=body).status_code == 401
        r = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
        assert client.post("/api/knowledge/review", json=body).status_code == 401
        assert temp_kb.read_bytes() == before

    def test_approve_edit_reject_exactly_as_the_review_page_calls_them(self, client, temp_kb, monkeypatch):
        _login(client, monkeypatch)
        # 화면이 처음 부르는 목록(검토 대기)
        d = client.get("/api/knowledge/candidates", params={"status": "candidate"}).json()
        names = [it["canonical_name"] for it in d["items"]]
        assert {"Review test item 1", "Review test item 2", "Review test item 3"} <= set(names)
        waiting = d["counts"]["candidate"]
        assert client.get("/api/admin/status").json()["authenticated"] is True
        hidden = get_knowledge_service().get_backdata("Review test item 1")
        assert "biology_ko" not in hidden, "검토 전 문장은 환자 화면에 나가지 않는다"

        def review(body):
            r = client.post("/api/knowledge/review", json=body, headers={"Content-Type": "application/json"})
            assert r.status_code == 200, r.text
            return r.json()

        # 승인
        assert review({"name": "Review test item 1", "action": "approve"}) == {
            "ok": True, "name": "Review test item 1", "action": "approve", "status": "approved"}
        e = _entry(temp_kb, "Review test item 1")
        assert e["biology_ko"] == "시험용 소개문 1" and e["biology_ko_status"] == "approved"
        assert e["biology_ko_reviewed_by"] == "web"
        assert get_knowledge_service().get_backdata("Review test item 1")["biology_ko"] == "시험용 소개문 1"

        # 수정 후 승인(화면은 textarea 의 값을 text 로 보낸다)
        assert review({"name": "Review test item 2", "action": "edit", "text": "  고쳐 쓴 소개문  "})["status"] == "approved"
        e = _entry(temp_kb, "Review test item 2")
        assert e["biology_ko"] == "고쳐 쓴 소개문" and e["biology_ko_reviewed_by"] == "web_edit"
        assert get_knowledge_service().get_backdata("Review test item 2")["biology_ko"] == "고쳐 쓴 소개문"

        # 문장 삭제 — 소개문 관련 필드만 지우고 템플릿 문장은 남긴다
        assert review({"name": "Review test item 3", "action": "reject"})["status"] is None
        e = _entry(temp_kb, "Review test item 3")
        assert not any(k.startswith("biology_ko") for k in e)
        assert e["avoidance_control_ko"] == ["손대지 않는 템플릿 문장"]

        # 목록과 건수가 따라 바뀐다
        d = client.get("/api/knowledge/candidates", params={"status": "candidate"}).json()
        assert d["counts"]["candidate"] == waiting - 3
        assert not any(it["canonical_name"].startswith("Review test item") for it in d["items"])
        approved = client.get("/api/knowledge/candidates", params={"status": "approved"}).json()["items"]
        assert {"Review test item 1", "Review test item 2"} <= {it["canonical_name"] for it in approved}
        assert json.loads(temp_kb.read_text(encoding="utf-8"))["reviewed_at"]

        # 잘못된 요청은 파일을 바꾸지 않는다
        snapshot = temp_kb.read_bytes()
        assert client.post("/api/knowledge/review", json={"name": "Review test item 1", "action": "edit",
                                                          "text": "   "}).status_code == 400
        assert client.post("/api/knowledge/review", json={"name": "Review test item 3",
                                                          "action": "approve"}).status_code == 400
        assert client.post("/api/knowledge/review", json={"name": "Review test item 1",
                                                          "action": "publish"}).status_code == 400
        assert client.post("/api/knowledge/review", json={"name": "Review test item 1", "action": "edit",
                                                          "text": "가" * 2001}).status_code == 400
        assert client.post("/api/knowledge/review", json={"name": "Review test item 1", "action": "approve"},
                           headers={"Origin": "https://evil.example"}).status_code == 403
        assert temp_kb.read_bytes() == snapshot

        # 로그아웃하면 다시 닫힌다
        assert client.post("/api/admin/logout").status_code == 200
        assert client.post("/api/knowledge/review",
                           json={"name": "Review test item 1", "action": "reject"}).status_code == 401
        assert temp_kb.read_bytes() == snapshot
