"""카드뉴스(퀘스트) 인터랙티브 덱 셸 — 본문은 그대로, 상호작용은 덧붙이기만 한다."""
import re

from models.schemas import ClinicalRelevance, ScreeningProfile
from services.cardnews_service import CardNewsService, get_cardnews_service
from services.relevance_service import get_relevance_service
from test_relevance_engine import build_case


def _html(screening=None):
    res = get_relevance_service().build_assessments(build_case(), screening)
    for i, a in enumerate(res.assessments):
        a.relevance = ClinicalRelevance.CLINICALLY_RELEVANT if i % 2 == 0 else ClinicalRelevance.SENSITIZED_ONLY
    return get_cardnews_service().generate_html(res, {"name": "테스트", "test_date": "2026-06-01"}, screening)


def _cards(html):
    deck = html[html.index('id="deck"'):html.index('<div class="cn-nav')]
    return re.split(r'<div class="card" ', deck)[1:]


def test_every_card_is_tagged_and_kept_in_order():
    scr = ScreeningProfile(allergic_diseases=["asthma"], organ_systems=["nasal"])
    cards = _cards(_html(scr))
    kinds = [re.search(r'data-kind="(\w+)"', c).group(1) for c in cards]
    assert kinds[0] == "cover" and kinds[-1] == "closing"
    assert "other" not in kinds, kinds
    assert [int(re.search(r'data-i="(\d+)"', c).group(1)) for c in cards] == list(range(1, len(cards) + 1))
    # 판정 카드 순서: 진범 확정 → 감작만 → 예방 → 치료
    order = [k for k in kinds if k in ("relevant", "sensitized", "prevention", "treatment")]
    assert order == ["relevant", "sensitized", "prevention", "treatment"]


def test_quiz_only_on_its_own_card_and_readable_without_js():
    # 쓰는 약을 알려 준 환자 — '약을 쓰고 있다면…' 퀴즈까지 붙는다
    scr = ScreeningProfile(allergic_diseases=["asthma"], current_medications=["inhaled_steroid"])
    for card in _cards(_html(scr)):
        kind = re.search(r'data-kind="(\w+)"', card).group(1)
        quiz = CardNewsService._QUIZ.get(kind)
        assert ('class="cn-quiz' in card) == bool(quiz), kind
        if quiz:
            question, options, why = quiz
            assert sum(1 for _, ok in options if ok) == 1, "정답은 하나"
            assert card.count('data-ok="1"') == 1 and "정답 해설" in card   # JS 없이도 정답·해설이 보인다
            assert card.index('class="cn-quiz') > card.rindex("</h2>")       # 본문 뒤에 붙는다


def test_quiz_answers_quote_the_card_text():
    """정답은 그 카드에 이미 적힌 문장에서 온다(새 임상 정보를 만들지 않는다)."""
    html = _html(ScreeningProfile(allergic_diseases=["asthma"], current_medications=["inhaled_steroid"]))
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html))
    for phrase in ("증상이 실제로 나타나는", "과도한 회피는 필요", "노출 줄이기는 약을 대신하지 않고 약과 함께 하는 관리입니다",
                   "감작"):
        assert text.count(phrase) >= 2, phrase   # 카드 본문 + 퀴즈


def test_medication_quiz_only_for_patients_who_reported_medication():
    """'약을 쓰고 있다면 노출을 줄이는 일은 그만해도 될까요?'는 쓰는 약을 알려 준 환자에게만 묻는다."""
    question = CardNewsService._QUIZ["treatment"][0]
    for meds in ([], ["none"]):
        html = _html(ScreeningProfile(allergic_diseases=["asthma"], current_medications=meds))
        assert "치료와 연결하기" in html and question not in html, meds
    assert question in _html(ScreeningProfile(allergic_diseases=["asthma"], current_medications=["antihistamine"]))


def test_no_quiz_or_card_asserts_unsourced_certainty():
    """출처가 없는 단정('약물 효과를 높입니다'·'약만큼 중요')과, 근거수준이 매우 낮은 '시즌 2주 전'을 정답으로 삼는
    퀴즈를 내보내지 않는다(data/treatment_guidance.json 의 문장과 근거수준을 그대로 쓴다)."""
    scr = ScreeningProfile(allergic_diseases=["asthma", "allergic_rhinitis"], current_medications=["nasal_steroid"])
    html = _html(scr)
    for banned in ("약물 효과를 높", "약만큼 중요", "약이 더 잘 듣", "3~12개월", "조절이 쉬워요"):
        assert banned not in html, banned
    for _q, options, why in CardNewsService._QUIZ.values():
        assert not any("2주" in text and ok for text, ok in options)
    assert "근거수준은 매우 낮" in CardNewsService._QUIZ["seasonality"][2]


def test_deck_is_self_contained_and_survives_translation_markup_rules():
    html = _html()
    assert "<script src" not in html and "<img" not in html and "import(" not in html
    # 외부 요청은 예전부터 있던 웹폰트 스타일시트 2개뿐
    assert sorted(set(re.findall(r'href="(https?://[^"]+)"', html))) == sorted({
        "https://fonts.googleapis.com", "https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap",
        "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css"})
    # translate_html 은 <b> 의 속성을 버린다 — 스크립트가 찾는 id 는 <b> 에 두지 않는다
    assert not re.search(r"<b [^>]*id=", html)
    for el in ("cnI", "cnQ", "cnPrev", "cnNext", "cnDots", "deck", "cnText"):
        assert f'id="{el}"' in html, el
    assert "@media print" in html and "prefers-reduced-motion" in html


def test_checklist_and_fold_flags():
    cards = _cards(_html(ScreeningProfile(allergic_diseases=["asthma"])))
    by_kind = {}
    for c in cards:
        by_kind.setdefault(re.search(r'data-kind="(\w+)"', c).group(1), []).append(c)
    assert all('data-check="1"' in c for c in by_kind["prevention"])
    assert all('data-fold="1"' in c for c in by_kind.get("detail", []))
    for kind in ("relevant", "sensitized", "oas", "treatment", "cover", "closing"):   # 판정·경고 카드는 접지 않는다
        assert all("data-fold" not in c for c in by_kind.get(kind, [])), kind
