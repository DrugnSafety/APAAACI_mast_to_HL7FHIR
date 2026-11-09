"""다중 선택 문진의 '배타 선택지'(없음·해당 없음·잘 모르겠어요) 플래그.

프론트는 option.exclusive === true 일 때 다른 선택을 비운다(web/shared.js). 값 이름(none/no)으로
짐작하지 않도록 서버가 플래그를 직접 싣는다. ① 모든 다중 선택 질문에서 배타처럼 보이는 선택지에
플래그가 있다 ② 번역을 거쳐도 플래그가 살아 있고 번역되지 않는다 ③ 배타 선택지가 다른 선택지와 함께
들어온 payload 는 서버가 정리한다(일반 선택지가 남고 배타 선택지는 버림).
실제 LLM 은 부르지 않는다(번역기는 가짜로 바꾼다).
"""
import json

import pytest

from models.schemas import OCRResult, ScreeningProfile
from services import translation_service
from services.knowledge_service import get_knowledge_service
from services.questionnaire_service import (
    Q_ANIMAL_WORK, Q_ANIMAL_WORK_ANIMALS, Q_FOOD_GENERAL, Q_MOLD_SPACE, Q_SEASONS,
    QP_FOOD_SYMPTOMS, QP_OTHER_SYMPTOM, _key, get_questionnaire_engine,
)
from services.relevance_service import get_relevance_service

# 배타처럼 보이는 값/문구 — 이 중 하나라도 해당하는 다중 선택 선택지는 플래그가 있어야 한다
LOOKS_EXCLUSIVE_VALUES = {"none", "never", "no", "unsure", "unknown"}
LOOKS_EXCLUSIVE_LABELS = ("해당 없음", "증상 없음", "잘 모르겠", "문제 없", "차이 없음", "그런 공간은 없")

ROWS = [
    ("Dermatophagoides farinae", "미국 집먼지진드기"), ("Cockroach", "바퀴"),
    ("Alternaria alternata", "알터나리아"), ("Aspergillus fumigatus", "아스퍼길루스"),
    ("Cat dander", "고양이 비듬"), ("Dog dander", "개 비듬"),
    ("Birch pollen", "자작나무 꽃가루"), ("Ragweed pollen", "돼지풀 꽃가루"),
    ("Peanut", "땅콩"), ("Egg white", "달걀 흰자"), ("Shrimp", "새우"),
    ("Latex", "라텍스"), ("Penicillin G", "페니실린"), ("Honey bee venom", "꿀벌 독"),
]


@pytest.fixture(autouse=True)
def _no_web_lookup():
    ks = get_knowledge_service()
    before, ks.enable_web = ks.enable_web, False
    yield
    ks.enable_web = before


def _result(rows=ROWS):
    from server import ocr_demo
    d = json.loads(ocr_demo().body)
    tpl = d["results"][0]
    d["results"] = [dict(tpl, allergen_name=en, korean_name=ko, category=None, class_value=3,
                         value=5.0, value_text=None, interpretation="Positive") for en, ko in rows]
    return get_relevance_service().build_assessments(OCRResult(**d), ScreeningProfile())


def _multi_questions(q):
    return [x for s in q["sections"] for x in s["questions"] if x["type"] == "multi"]


def test_every_exclusive_looking_multi_option_is_flagged():
    res = _result()
    q = get_questionnaire_engine().build(res, ScreeningProfile())
    multis = _multi_questions(q)
    ids = {x["id"] for x in multis}
    # 이 후보 세트가 실제로 문제의 질문들을 모두 만든다(세트가 줄어 테스트가 빈 껍데기가 되지 않게)
    assert {Q_SEASONS, Q_MOLD_SPACE, Q_ANIMAL_WORK, Q_ANIMAL_WORK_ANIMALS, Q_FOOD_GENERAL} <= ids
    assert any(i.startswith(QP_FOOD_SYMPTOMS) for i in ids)
    assert any(i.startswith(QP_OTHER_SYMPTOM) for i in ids)
    missing = []
    for x in multis:
        for o in x["options"]:
            looks = o["value"] in LOOKS_EXCLUSIVE_VALUES or any(t in o["label"] for t in LOOKS_EXCLUSIVE_LABELS)
            if looks and o.get("exclusive") is not True:
                missing.append((x["id"], o["value"]))
            if o.get("exclusive") is True:
                assert looks or (x["id"], o["value"]) == (Q_ANIMAL_WORK_ANIMALS, "other"), (x["id"], o["value"])
    assert not missing, missing
    by_id = {x["id"]: x for x in multis}
    flagged = lambda qid: {o["value"] for o in by_id[qid]["options"] if o.get("exclusive")}
    assert flagged(Q_ANIMAL_WORK) == {"none"}
    assert flagged(Q_ANIMAL_WORK_ANIMALS) == {"other"}
    # 단일 선택 질문에는 플래그가 없다(의미 없음) — 그리고 '그 밖의 음식'은 다른 음식과 함께 고를 수 있다
    assert not any(o.get("exclusive") for s in q["sections"] for x in s["questions"]
                   if x["type"] == "single" for o in x["options"])
    sys_foods = by_id.get("food_systemic_foods")
    if sys_foods:
        assert not any(o.get("exclusive") for o in sys_foods["options"])


def test_flag_survives_localisation(monkeypatch):
    import server
    svc = translation_service.get_translation_service()
    monkeypatch.setattr(svc, "translate_batch", lambda texts, lang: [f"EN:{t}" for t in texts])
    q = get_questionnaire_engine().build(_result(), ScreeningProfile())
    before = {(x["id"], o["value"]) for x in _multi_questions(q) for o in x["options"] if o.get("exclusive")}
    out = server._localize({"questionnaire": q}, "en", server.QUESTION_TEXT_KEYS)["questionnaire"]
    after = {(x["id"], o["value"]) for x in _multi_questions(out) for o in x["options"] if o.get("exclusive")}
    assert before and after == before
    assert all(o["exclusive"] is True for x in _multi_questions(out) for o in x["options"] if "exclusive" in o)
    # 번역은 실제로 돌았고(label 이 바뀜), 원본 상수는 오염되지 않았다
    assert any(o["label"].startswith("EN:") for x in _multi_questions(out) for o in x["options"])
    assert all(not o["label"].startswith("EN:") for x in _multi_questions(q) for o in x["options"])


def test_exclusive_with_others_is_normalised_substantive_wins():
    res = _result()
    eng = get_questionnaire_engine()
    animals = [i for i, a in enumerate(res.assessments) if "dander" in a.allergen_name]
    k0 = _key(animals[0])
    peanut = _key(next(i for i, a in enumerate(res.assessments) if a.allergen_name == "Peanut"))
    answers = {
        Q_ANIMAL_WORK: ["vet", "none"],
        Q_ANIMAL_WORK_ANIMALS: [k0, "other"],
        Q_SEASONS: ["spring", "none"],
        Q_MOLD_SPACE: ["bathroom", "none"],
        QP_FOOD_SYMPTOMS + peanut: ["none", "anaphylaxis"],
        Q_FOOD_GENERAL: "none",                      # 다중 선택에 스칼라가 오면 한 칸짜리 목록으로 읽는다
    }
    orig = json.loads(json.dumps(answers))
    n = eng.normalize_answers(res, answers)
    assert answers == orig                           # 입력은 고치지 않는다
    assert n[Q_ANIMAL_WORK] == ["vet"]
    assert n[Q_ANIMAL_WORK_ANIMALS] == [k0]
    assert n[Q_SEASONS] == ["spring"]
    assert n[Q_MOLD_SPACE] == ["bathroom"]
    assert n[QP_FOOD_SYMPTOMS + peanut] == ["anaphylaxis"]
    assert n[Q_FOOD_GENERAL] == ["none"]
    # 배타 하나만 오면 그대로
    only = eng.normalize_answers(res, {Q_ANIMAL_WORK: ["none"]})
    assert only[Q_ANIMAL_WORK] == ["none"]
    # 판정: '증상 없음'과 함께 온 아나필락시스 병력이 지워져 '감작만'이 되지 않는다
    eng.classify(res, answers, ScreeningProfile())
    a = res.assessments[int(peanut[3:])]
    assert a.relevance.value == "clinically_relevant"
