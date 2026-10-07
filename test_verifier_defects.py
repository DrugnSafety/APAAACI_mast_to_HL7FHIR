"""독립 검증에서 나온 결함 A~H 의 회귀 테스트.

  A. 벌독(Honey bee venom)이 수목 꽃가루로 분류돼 꽃가루 달력·환기 안내·벌독 면역치료가 봄철 코 증상으로 열렸다.
  B. 시기가 항원이 아니라 분류군 달력에서 와서 한 리포트에 범위가 세 가지였고, 삼나무 메모가 누구에게나 붙었다.
  C. 문진의 증상 패턴·악화 계절이 계절성 입력에 닿지 않아 머리말이 '증상 없음'을 찍었다.
  D. '문진에서 확인된 내용'이 환자가 말하지 않은 증상 부위를 적었다.
  E. '우선 실천 회피 수칙'이 첫 알러젠(진드기) 수칙으로만 찼고 같은 수칙이 두 번씩 나왔다.
  F. 이름·항원명 등 사용자/OCR 값이 리포트 Markdown·HTML 에 그대로 실렸다(저장형 HTML 주입).
  G. 면역치료 추천 질문에 키 없는 답이 없었고, 개수와 이름 목록이 어긋났고, 별칭이 목록에 닿지 않았다.
  H. 긴 카드가 고정 높이 카드 안에서 단서 없이 잘렸다.
"""
import json
import re
from html.parser import HTMLParser

import markdown as md_lib
import pytest

from models.schemas import ClinicalRelevance, OCRResult, ScreeningProfile, SymptomSeasonPattern
from services.cardnews_classic import get_classic_cardnews_service
from services.cardnews_service import get_cardnews_service
from services.category_resolver import resolve_category
from services.exposure_guidance_service import allocated_tips, is_duplicate_tip
from services.knowledge_service import get_knowledge_service, normalize_category
from services.pollen_forecast_service import PollenForecastService
from services.questionnaire_service import apply_answers_to_screening, get_questionnaire_engine
from services.relevance_service import get_relevance_service
from services.report_service import get_report_service, md_text
from services.result_chat_service import ResultChatService

REL = ClinicalRelevance.CLINICALLY_RELEVANT
IND = ClinicalRelevance.INDETERMINATE

MITE_F = ("Dermatophagoides farinae", "미국 집먼지진드기")
MITE_P = ("Dermatophagoides pteronyssinus", "유럽 집먼지진드기")
BIRCH = ("Birch pollen", "자작나무 꽃가루")
RAGWEED = ("Ragweed pollen", "돼지풀 꽃가루")
CEDAR = ("Japanese cedar", "삼나무")
CAT = ("Cat dander", "고양이 비듬")
VENOM = ("Honey bee venom", "꿀벌독")


@pytest.fixture(autouse=True)
def _no_web_lookup():
    """레지스트리에 없는 이름(벌독 등)은 Wikipedia 를 찾는다 — 테스트에서는 네트워크를 타지 않는다."""
    ks = get_knowledge_service()
    before, ks.enable_web = ks.enable_web, False
    yield
    ks.enable_web = before


def _ocr(rows, name="홍길동"):
    from server import ocr_demo
    d = json.loads(ocr_demo().body)
    tpl = d["results"][0]
    d["results"] = [dict(tpl, allergen_name=en, korean_name=ko, category=None, class_value=3, value=5.0,
                         value_text=None, interpretation="Positive") for en, ko in rows]
    d["patient"]["name"] = name
    return OCRResult(**d)


def _run(rows, answers, screening, name="홍길동"):
    """실제 경로(판정 → 리포트·카드뉴스). 반환: (결과, 문진, 리포트 md, 퀘스트 카드, 클래식 카드)"""
    res = get_relevance_service().build_assessments(_ocr(rows, name), screening)
    engine = get_questionnaire_engine()
    q = engine.build(res, screening)
    engine.classify(res, answers, screening)
    info = {"name": name}
    md = get_report_service().build_patient_report_markdown(res, info, screening)
    quest = get_cardnews_service().generate_html(res, info, screening)
    classic = get_classic_cardnews_service().generate_html(res, info, screening)
    return res, q, md, quest, classic


def _text(card_html):
    """카드뉴스 본문 텍스트(스타일·스크립트 제외)."""
    body = card_html[card_html.find("<body"):]
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))


def _kr(**kw):
    kw.setdefault("allergic_diseases", ["allergic_rhinitis"])
    kw.setdefault("organ_systems", ["nasal"])
    return ScreeningProfile(residence_country="KR", **kw)


# ---------------------------------------------------------------------------
# A. 카테고리 결정 — 벌독은 곤충 독, 레지스트리 항원은 레지스트리 카테고리
# ---------------------------------------------------------------------------
class TestCategoryResolution:
    REGISTRY = json.load(open("data/allergens.json", encoding="utf-8"))["antigens"]

    def test_every_registry_antigen_resolves_to_its_registry_category(self):
        """레지스트리 전부: 이름으로 찾은 카테고리(분류 파이프라인·지식베이스)가 레지스트리와 같다.
        control·latex·drug 도 뭉치지 않고 그대로 나온다."""
        ks = get_knowledge_service()
        wrong = []
        for a in self.REGISTRY:
            want = normalize_category(a["category"])
            got = normalize_category(resolve_category(a["canonical_name"], a["korean_name"]))
            kb = ks.get_backdata(a["canonical_name"], None, a["korean_name"])["category"]
            if (got, kb) != (want, want):
                wrong.append((a["id"], want, got, kb))
        assert wrong == []

    def test_every_registry_alias_resolves_to_its_registry_category(self):
        """별칭·OCR 변형만으로 들어와도 같은 카테고리여야 한다.
        ('oat' 가 재배 귀리 꽃가루의 별칭이자 음식 Oat 의 이름이어서 음식 귀리가 잔디 꽃가루로 풀렸다)"""
        wrong = []
        for a in self.REGISTRY:
            want = normalize_category(a["category"])
            for alias in a["aliases"] + a.get("ocr_aliases", []):
                got = normalize_category(resolve_category(alias, ""))
                if got != want:
                    wrong.append((a["id"], alias, want, got))
        assert wrong == []

    # 같은 글자가 두 항원에 걸려 있는 이름(레지스트리 원본의 진짜 충돌). 대표 이름·한글명이 별칭을 이긴다.
    # 여기 없는 충돌이 생기면 아래 테스트가 실패한다 — 추측으로 풀지 말고 원본(allergen_map_prompt_v2.json)을 고친다.
    #   '옥수수가루' = Cornflour 의 한글명이자 Maize (corn) 의 별칭 → 어디서나 Cornflour 로 풀린다
    KNOWN_COLLISIONS = {("Maize (corn)", "옥수수가루"): "Cornflour"}

    def test_every_registry_name_resolves_to_its_own_entry_in_every_resolver(self):
        """대표 이름·한글명·별칭·OCR 변형 전부가, 쓰이는 모든 조회 경로에서 자기 항원으로 풀린다.

        경로: allergen_mapper(SNOMED·FHIR), category_resolver(카테고리), knowledge_service(지식),
        crossreactivity_service(레지스트리·교차반응), allergen_search_service(자동완성).
        예전에는 'House dust' 가 미국집먼지진드기로, 'Egg yo1k' 가 난백으로, 'Histamine control' 이
        음성 대조로 풀렸다."""
        from utils.allergen_mapper import get_allergen_mapper
        from services.crossreactivity_service import get_crossreactivity_service
        from services.allergen_search_service import get_allergen_search_service
        mapper, ks = get_allergen_mapper(), get_knowledge_service()
        cross, search = get_crossreactivity_service(), get_allergen_search_service()
        by_name = {a["canonical_name"]: a for a in self.REGISTRY}
        wrong, collisions = [], {}
        for a in self.REGISTRY:
            names = [a["canonical_name"], a["korean_name"]] + a["aliases"] + a.get("ocr_aliases", [])
            for name in dict.fromkeys(n for n in names if n):
                owner = by_name[self.KNOWN_COLLISIONS.get((a["canonical_name"], name), a["canonical_name"])]
                if owner is not a:
                    collisions[(a["canonical_name"], name)] = owner["canonical_name"]
                got = {}
                hit = mapper.find_allergen(name)
                got["mapper"] = hit.canonical_name if hit else None
                got["category"] = resolve_category(name, "")
                kb = ks.get_backdata(name, None, None)
                got["knowledge"] = (kb.get("canonical_name"), kb.get("category"))
                hit = cross.find(name, "")
                got["crossreactivity"] = hit["id"] if hit else None
                found = search.search(name, 50)
                got["search"] = owner["id"] in [r["id"] for r in found] and search.is_known_name(name)
                want = {"mapper": owner["canonical_name"], "category": owner["category"],
                        "knowledge": (kb.get("canonical_name"), owner["category"]),
                        "crossreactivity": owner["id"], "search": True}
                if kb.get("canonical_name") not in (owner["canonical_name"], owner.get("kb_ref")):
                    want["knowledge"] = (owner["canonical_name"], owner["category"])
                if got != want:
                    wrong.append((a["canonical_name"], name, {k: (got[k], want[k]) for k in got if got[k] != want[k]}))
                # 대표 이름·한글명으로 찾으면 자동완성 맨 위가 자기 항원이다
                if name in (owner["canonical_name"], owner["korean_name"]) and owner is a:
                    assert found[0]["id"] == a["id"], (name, found[0]["canonical_name"])
        assert wrong == []
        assert collisions == self.KNOWN_COLLISIONS

    @pytest.mark.parametrize("name,category", [
        ("Mix", "other"), ("mix", "other"), ("Mixture", "other"), ("혼합", "other"), ("Dust", "other"),
        ("Tree", "other"), ("House", "other"), ("Mold mix", "mold"), ("Pollen", "pollen_tree"), ("Hair", "animal")])
    def test_bare_generic_word_matches_no_antigen(self, name, category):
        """'Mix' 한 낱말이 'Cockroach, Mix'(바퀴)에 붙어 곤충으로 분류됐고, 'Dust' 에는 집먼지진드기 지식이,
        'Pollen' 에는 자작나무 지식이 붙었다. 일반 낱말만으로는 어떤 항원도 아니다 — 종류(카테고리)는
        낱말 규칙으로 정해질 수 있어도, 특정 항원의 이름·지식·코드가 붙어서는 안 된다."""
        from utils.allergen_mapper import get_allergen_mapper
        from services.crossreactivity_service import get_crossreactivity_service
        assert get_allergen_mapper().find_allergen(name) is None
        assert get_crossreactivity_service().find(name, "") is None
        assert resolve_category(name, "") == category
        kb = get_knowledge_service().get_backdata(name, None, None)
        assert kb["source"] == "category_default" and kb["category"] == category and kb["canonical_name"] == name

    def test_house_dust_is_not_a_mite_species(self):
        """집먼지(house dust 추출물)는 진드기 종이 아니다. 퍼지 매칭(0.8)으로 미국집먼지진드기에 붙었다."""
        from utils.allergen_mapper import get_allergen_mapper
        m, ks = get_allergen_mapper(), get_knowledge_service()
        for en, ko in (("House dust", ""), ("House dust", "집먼지"), ("", "집먼지"), ("Housedust", "")):
            hit = m.find_allergen(en or ko)
            assert hit and hit.canonical_name == "House dust", (en, ko)
            assert resolve_category(en, ko) == "other"
            kb = ks.get_backdata(en or ko, None, ko)
            assert kb["canonical_name"] == "House dust" and kb["category"] == "other"
            assert m.get_coding(en, ko) is None, "진드기 코드를 빌려 쓰지 않는다"
        assert m.find_allergen("House dust mite").canonical_name == "Dermatophagoides farinae"
        # 낱말이 통째로 빠지거나 더해진 이름은 오타가 아니다 — 퍼지 매칭(0.8)이 'house dust' 를
        # 별칭 'House dust mite' 에 붙이던 경로를 레지스트리 항목 없이도 막는다.
        from models.schemas import AllergenDatabase
        mites_only = type(m).__new__(type(m))
        mites_only.database = AllergenDatabase(version="t", locale="ko-KR", global_rules={}, entries=[
            e for e in m.database.entries if e.canonical_name == "Dermatophagoides farinae"])
        assert mites_only._fuzzy_match("house dust") is None
        # 오타 구제는 5글자 이상 낱말에서 글자 하나가 다를 때만 한다
        assert mites_only._fuzzy_match("houze dust mite").canonical_name == "Dermatophagoides farinae", "오타 구제는 그대로"
        assert mites_only._fuzzy_match("house dust mitee") is None, "짧은 낱말(mite)의 한 글자 차이는 다른 이름일 수 있다"

    @pytest.mark.parametrize("en,ko", [
        VENOM, ("Honey bee", "꿀벌"), ("Bee venom", ""), ("Wasp venom", "말벌독"),
        ("Yellow jacket venom", "땅벌독"), ("Paper wasp", "쌍살벌"), ("Hornet", ""), ("", "벌독"),
    ])
    def test_hymenoptera_venom_is_venom_not_pollen(self, en, ko):
        assert resolve_category(en, ko) == "venom"
        kb = get_knowledge_service().get_backdata(en or ko, None, ko)
        assert kb["category"] == "venom"
        assert not kb.get("peak_months_korea") and not kb.get("season_label_ko"), "벌독에는 꽃가루 시기가 없다"

    def test_fuzzy_knowledge_lookup_does_not_borrow_another_species(self):
        """'뽕나무 꽃가루' 는 지식베이스에 없다. 공통 수식어 때문에 '참나무 꽃가루' 의 지식이 붙으면 안 된다."""
        ks = get_knowledge_service()
        kb = ks.get_backdata("Mulberry pollen", None, "뽕나무 꽃가루")
        assert kb["category"] == "pollen_tree" and kb["source"] == "category_default"
        assert kb["canonical_name"] == "Mulberry pollen"
        # OCR 변형 구제는 그대로 된다
        assert ks.get_backdata("Birch polen", None, "")["canonical_name"] == "Birch pollen"
        assert ks.get_backdata("Birch", None, "")["canonical_name"] == "Birch pollen"

    def test_venom_labelled_insect_by_the_sheet_is_still_venom(self):
        """결과지·OCR 이 벌독을 'Insect' 로 적어 보내도 바퀴(흡입 곤충) 문항·안내로 가지 않는다."""
        assert resolve_category("Honey bee venom", "꿀벌독", "Insect") == "venom"
        assert resolve_category("Cockroach, German", "독일바퀴", "Insect") == "insect"
        assert get_knowledge_service().get_backdata("Wasp venom", "insect", "말벌독")["category"] == "venom"

    def test_lookalikes_keep_their_own_category(self):
        """'bee' 를 낱말로만 본다 — Beech·Beef 는 그대로다. 바퀴'벌레'도 벌독이 아니다."""
        assert resolve_category("Beech", "너도밤나무") == "pollen_tree"
        assert resolve_category("Beef", "소고기") == "food"
        assert resolve_category("Cockroach", "바퀴벌레") == "insect"

    def test_mapper_no_longer_matches_by_substring(self):
        from utils.allergen_mapper import get_allergen_mapper
        m = get_allergen_mapper()
        # 'bee' 는 부분 문자열로는 Beech, 퍼지로는 Beef(0.86)에 붙었다. 2026-10 에 벌독 항원을 레지스트리에
        # 올린 뒤로는 벌 이름이 자기 항원으로 풀린다. 어느 벌인지 알 수 없는 이름('bee'·'Hornet')은 여전히
        # 어떤 항원에도 붙지 않는다(카테고리만 벌독).
        for name, want in (("Honey bee venom", "Honey bee venom"), ("Honey bee", "Honey bee venom"),
                           ("Yellow jacket", "Yellow jacket venom"), ("Wasp", "Yellow jacket venom")):
            hit = m.find_allergen(name)
            assert hit and hit.canonical_name == want, f"{name} → {hit and hit.canonical_name}"
        for name in ("bee", "Bee", "Hornet", "Hornet venom", "Bumble bee"):
            hit = m.find_allergen(name)
            assert hit is None, f"{name} → {hit.canonical_name}"
            assert resolve_category(name, "") == "venom"
        assert m.find_allergen("Beech").canonical_name == "Beech"
        # 노이즈 토큰만으로 된 이름은 빈 문자열로 정규화돼 첫 항목(점박이응애)에 붙었다
        assert m.find_allergen("Histamine").canonical_name == "Histamine"
        assert resolve_category("Histamine", "히스타민") != "mite"


class TestVenomOutputs:
    SPRING_NASAL = {"symptom_pattern": "seasonal", "worse_seasons": ["spring"],
                    "pollen_season__spring_tree": "yes"}

    def test_spring_nasal_answer_does_not_make_venom_a_pollen_allergy(self):
        """검증자가 본 그대로: 벌독 + '봄철 코 증상 예'. 꽃가루 달력·환기 안내·벌독 면역치료가 나오면 안 된다."""
        res, q, md, quest, classic = _run([VENOM], self.SPRING_NASAL, _kr())
        a = res.assessments[0]
        assert a.category == "venom" and a.relevance == IND
        assert "pollen" not in [s["id"] for s in q["sections"]], "벌독만 있는 환자에게 꽃가루 시즌 문항을 묻지 않는다"
        assert "venom" in [s["id"] for s in q["sections"]]
        for out in (md, _text(quest), _text(classic)):
            assert "증상의 계절성" not in out and "몇 월에" not in out
            assert "창문을 열어 둔 환기" not in out and "2~5월" not in out and "3~5월" not in out
            assert "벌독(꿀벌·말벌)" not in out, "전신 쏘임 반응이 확인되지 않았는데 벌독 면역치료가 나왔다"
        assert "면역치료 — 진료에서" not in md

    def test_venom_stays_out_of_the_pollen_calendar_even_when_relevant(self):
        res, _q, md, quest, _classic = _run([VENOM, BIRCH], dict(self.SPRING_NASAL, sting_reaction="systemic"), _kr())
        out = PollenForecastService(api_key="").seasonality(res.assessments, _kr())
        assert [i["name"] for i in out["items"]] == ["자작나무 꽃가루"]
        season = md[md.find("증상의 계절성"):md.find("### 🔗")]
        assert "꿀벌독" not in season
        assert "🐝 곤충 독(쏘임)" in md and "꿀벌독" not in md[md.find("### 🌳 실외(계절성) 항원"):md.find("### 🐝")]
        assert "- 노출되는 상황: 벌에 쏘일 때" in md
        assert "꿀벌독: 벌에 쏘일 때" in _text(quest)

    @pytest.mark.parametrize("reaction,shown", [
        ("systemic", True), ("large_local", False), ("local", False), ("never", False),
        ("unsure", False), (None, False),
    ])
    def test_venom_immunotherapy_needs_a_systemic_sting_reaction(self, reaction, shown):
        """목록 항목 자신의 말: '벌에 쏘인 뒤 전신 반응이 있었고 벌독 특이 IgE 가 확인된 환자가 대상'."""
        answers = dict(self.SPRING_NASAL)
        if reaction:
            answers["sting_reaction"] = reaction
        res, _q, md, quest, classic = _run([VENOM], answers, _kr())
        a = res.assessments[0]
        assert (a.relevance == REL) is shown
        rows = get_knowledge_service().immunotherapy_candidates(res.assessments)
        assert bool(rows) is shown
        chat = [s["answer"] for s in ResultChatService(api_key="").suggestions(res)
                if s["key"] == "immunotherapy"][0]
        for out in (md, _text(quest), _text(classic), chat):
            assert ("벌독(꿀벌·말벌)" in out) is shown
        info = get_knowledge_service().immunotherapy_info("venom", *VENOM, assessment=a)
        assert info["eligible"] is shown
        assert get_knowledge_service().immunotherapy_info("venom", *VENOM)["eligible"] is False, \
            "병력을 모르면 대상이 아니다"

    def test_relevance_alone_is_not_enough(self):
        """판정이 '실제 주의'여도 쏘임 병력 답이 없으면 벌독 면역치료를 열지 않는다(문진 밖 경로 방어)."""
        res, *_ = _run([VENOM], {}, _kr())
        res.assessments[0].relevance = REL
        assert get_knowledge_service().immunotherapy_candidates(res.assessments) == []


# ---------------------------------------------------------------------------
# B. 시기 — 항원마다 한 가지, 리포트와 카드가 같게
# ---------------------------------------------------------------------------
class TestSeasonPerAllergen:
    BIRCH_YES = {"pollen_season__spring_tree": "yes"}

    def test_birch_only_patient_sees_one_range_everywhere(self):
        _res, _q, md, quest, classic = _run([BIRCH], self.BIRCH_YES, _kr())
        one = "봄 (4~5월, 지역에 따라 3월부터)"
        for out in (md, _text(quest), _text(classic)):
            assert one in out
            assert "2~5월" not in out and "봄 (3~5월)" not in out, "같은 항원에 다른 범위가 섞였다"
            assert "삼나무" not in out, "자작나무 환자에게 삼나무 지역 메모가 붙었다"
        strip = re.search(r"주의가 필요한 달: (.*)", md).group(1)
        assert re.findall(r"\*\*(\d+)월\*\*", strip) == ["4", "5"], "2월·3월이 강조됐다"
        assert f"| 자작나무 꽃가루 | {one} |" in md
        assert f"자작나무 꽃가루 — {one}" in _text(quest)

    def test_cedar_note_only_for_cedar_patients(self):
        _res, _q, md, quest, _c = _run([RAGWEED], {"pollen_season__fall_weed": "yes"}, _kr())
        assert "삼나무" not in md and "삼나무" not in _text(quest)
        _res, _q, md, quest, _c = _run([CEDAR], self.BIRCH_YES, _kr())
        assert "삼나무는 제주·남부에서" in md and "삼나무는 제주·남부에서" in _text(quest)
        assert "| 삼나무 | 이른 봄 (2~4월) |" in md

    def test_no_relevant_pollen_means_no_section_in_report_or_cards(self):
        scr = _kr(season_pattern=SymptomSeasonPattern.SEASONAL)
        _res, _q, md, quest, classic = _run(
            [RAGWEED, MITE_F], {"pollen_season__fall_weed": "no", "indoor_timing": "yes",
                                "symptom_pattern": "seasonal"}, scr)
        assert "증상의 계절성" not in md and "시즌 대비" not in md and "삼나무" not in md
        assert "몇 월에" not in _text(quest) and "몇 월에" not in _text(classic)

    def test_category_level_season_is_labelled_as_an_estimate(self):
        """종별 자료가 없는 꽃가루는 분류군 달력으로 답하되 추정이라고 밝힌다(리포트·카드 모두)."""
        rows = [("Mulberry pollen", "뽕나무 꽃가루")]      # 레지스트리·종별 표에 없는 수목 꽃가루
        res, _q, md, quest, _c = _run(rows, self.BIRCH_YES, _kr())
        assert res.assessments[0].category == "pollen_tree" and res.assessments[0].relevance == REL
        label = "이른 봄~봄 (2~5월) · 수목 꽃가루 전체 기준 추정"
        assert f"| 뽕나무 꽃가루 | {label} |" in md and f"뽕나무 꽃가루 — {label}" in _text(quest)
        assert "같은 분류군" in md and "같은 분류군" in _text(quest)

    def test_species_data_is_not_used_outside_korea(self):
        """미국 거주자에게 한국 종별 달을 쓰지 않는다 — 지역 분류군 달력 + 추정 표시."""
        scr = ScreeningProfile(residence_country="US", residence_region="NY", organ_systems=["nasal"])
        _res, _q, md, quest, _c = _run([BIRCH], self.BIRCH_YES, scr)
        label = "봄 (3~5월) · 수목 꽃가루 전체 기준 추정"
        assert f"| 자작나무 꽃가루 | {label} |" in md and label in _text(quest)
        assert "4~5월, 지역에 따라 3월부터" not in md and "4~5월, 지역에 따라 3월부터" not in _text(quest)

    def test_service_reports_the_level(self):
        res, *_ = _run([BIRCH], self.BIRCH_YES, _kr())
        svc = PollenForecastService(api_key="")
        assert svc.allergen_season(res.assessments[0], "KR", None) == {
            "months": [4, 5], "label_ko": "봄 (4~5월, 지역에 따라 3월부터)", "level": "species"}
        item = svc.for_patient(res.assessments, country="KR")["items"][0]
        assert item["months"] == [4, 5] and item["season_level"] == "species"
        assert svc.for_patient(res.assessments, country="KR")["notable_ko"] is None


# ---------------------------------------------------------------------------
# C. 문진의 증상 패턴·악화 계절 → 계절성 입력
# ---------------------------------------------------------------------------
class TestQuestionnaireFeedsSeasonality:
    ANSWERS = {"symptom_pattern": "seasonal", "worse_seasons": ["spring"],
               "pollen_season__spring_tree": "yes"}

    def test_answers_reach_the_screening_profile(self):
        scr = _kr()
        apply_answers_to_screening(scr, self.ANSWERS)
        assert scr.season_pattern == SymptomSeasonPattern.SEASONAL and scr.worse_months == [3, 4, 5]
        apply_answers_to_screening(scr, self.ANSWERS)      # 언어별로 여러 번 불려도 같다
        assert scr.worse_months == [3, 4, 5]
        apply_answers_to_screening(scr, {"worse_seasons": ["none"]})
        assert scr.worse_months == [] and scr.season_pattern == SymptomSeasonPattern.SEASONAL
        apply_answers_to_screening(None, self.ANSWERS)     # 스크리닝이 없어도 죽지 않는다

    def test_overlap_sentence_is_reachable(self):
        scr = _kr()
        res, _q, md, quest, _c = _run([BIRCH], self.ANSWERS, scr)
        assert "- 📆 증상 패턴: 계절성 (특정 시기에만) (악화 시기: 3월, 4월, 5월)" in md
        assert "답해주신 악화 시기(3월, 4월, 5월)가 위 알러젠 시즌과 **4월, 5월** 에서 겹칩니다" in md
        assert "말씀하신 악화 시기가 4월, 5월 에서 겹쳐요" in _text(quest)
        assert res.assessments[0].season_overlap is True
        assert "알려주신 악화 시기가 이 꽃가루 시즌과 겹칩니다." in md

    def test_mismatch_is_reported_too(self):
        _res, _q, md, _quest, _c = _run([BIRCH], dict(self.ANSWERS, worse_seasons=["fall"]), _kr())
        assert "답해주신 악화 시기(9월, 10월, 11월)가 위 알러젠 시즌과 겹치지 않습니다" in md

    @pytest.mark.parametrize("answers", [
        {"pollen_season__spring_tree": "yes"},                                  # 패턴 문항을 건너뜀
        {"pollen_season__spring_tree": "yes", "symptom_pattern": "none"},       # '뚜렷한 증상이 없어요'
    ])
    def test_never_says_no_symptoms_beside_confirmed_symptoms(self, answers):
        scr = _kr()
        res, _q, md, _quest, _c = _run([BIRCH], answers, scr)
        assert res.assessments[0].relevance == REL
        assert "뚜렷한 패턴 없음" not in md and "/ 증상 없음" not in md and "증상 패턴:" not in md
        ctx = ResultChatService(api_key="").build_context(res, {"name": "t"}, scr, answers)
        assert "증상 패턴:" not in ctx and "/ 증상 없음" not in ctx


# ---------------------------------------------------------------------------
# D. '문진에서 확인된 내용'은 환자가 말한 부위만
# ---------------------------------------------------------------------------
class TestConfirmedSymptomsAreOnlyReportedSites:
    ROWS = [BIRCH, CAT, MITE_F]
    ANSWERS = {"pollen_season__spring_tree": "yes", "animal_worse__agn1": "yes",
               "animal_contact__agn1": "yes", "indoor_timing": "yes"}

    def _confirmed(self, organs):
        res, _q, md, _quest, _c = _run(self.ROWS, self.ANSWERS, _kr(organ_systems=organs, pets=["cat"]))
        assert all(a.relevance == REL for a in res.assessments)
        return [l for l in md.split("\n") if l.startswith("- 문진에서 확인된 내용:")], res

    def test_nasal_only_patient_gets_no_eye_skin_or_airway_claims(self):
        lines, res = self._confirmed(["nasal"])
        assert lines == [
            "- 문진에서 확인된 내용: 봄철 나무 꽃가루 시기 코 증상 악화",
            "- 문진에서 확인된 내용: 고양이 비듬 접촉 시 코 증상 악화",
            "- 문진에서 확인된 내용: 집먼지진드기 실내 노출 시 코 증상 악화(저녁과 새벽, 이른 아침에 증상이 심해집니다)",
        ]
        for a in res.assessments:      # FHIR manifestation 으로도 나가는 값이다
            assert not re.search("눈|피부|호흡기", " ".join(a.reported_symptoms))

    def test_reported_sites_are_all_stated(self):
        lines, _ = self._confirmed(["nasal", "ocular", "skin", "lower_airway"])
        assert "코·눈 증상 악화" in lines[0]
        assert "코·눈·피부·호흡기 증상 악화" in lines[1]
        assert "코·눈·호흡기 증상 악화" in lines[2]

    def test_no_reported_site_means_no_site_is_named(self):
        lines, _ = self._confirmed([])
        assert lines[0].endswith("봄철 나무 꽃가루 시기 증상 악화")
        assert not re.search("코|눈|피부|호흡기", lines[1])


# ---------------------------------------------------------------------------
# E. 우선 실천 회피 수칙 — 알러젠마다 고르게, 겹치지 않게
# ---------------------------------------------------------------------------
class TestAvoidanceTipsAreAllocated:
    ROWS = [MITE_F, MITE_P, BIRCH, CAT]
    ANSWERS = {"indoor_timing": "yes", "pollen_season__spring_tree": "yes",
               "animal_worse__agn3": "yes", "animal_contact__agn3": "yes"}

    def _outputs(self):
        return _run(self.ROWS, self.ANSWERS, _kr(pets=["cat"]))

    def test_report_covers_every_relevant_allergen_without_duplicates(self):
        res, _q, md, _quest, _c = self._outputs()
        assert all(a.relevance == REL for a in res.assessments)
        start = md.find("### 🎯 우선 실천 회피 수칙")
        block = md[start:md.find("\n##", start + 5)]     # 다음 제목 앞까지
        tips = re.findall(r"^- \*\*(.+?):\*\* (.+)$", block, flags=re.M)
        assert 3 <= len(tips) <= 10
        labels = [l for l, _ in tips]
        assert set(labels) == {"집먼지진드기", "자작나무 꽃가루", "고양이 비듬"}
        assert labels[:3] == ["집먼지진드기", "자작나무 꽃가루", "고양이 비듬"], "첫 줄부터 알러젠을 돌아가며 뽑는다"
        assert max(labels.count(l) for l in set(labels)) - min(labels.count(l) for l in set(labels)) <= 1
        seen = []
        for _label, tip in tips:
            assert not is_duplicate_tip(tip, seen), f"겹치는 수칙: {tip}"
            seen.append(tip)
        assert sum("55~60℃" in t for _l, t in tips) == 1 and sum("차단" in t for _l, t in tips) == 1

    @pytest.mark.parametrize("classic", [False, True])
    def test_card_covers_every_relevant_allergen(self, classic):
        _res, _q, _md, quest, classic_html = self._outputs()
        text = _text(classic_html if classic else quest)
        card = text[text.find("우선 실천할 생활 수칙"):text.find("🩺 치료와 연결하기")]
        assert "🛏️" in card and "🌳" in card and "🐾" in card
        assert card.count("55~60℃") == 1, "유럽·미국 진드기의 같은 수칙이 두 번 나왔다"
        assert len(re.findall("🛏️|🌳|🐾", card)) <= 6

    def test_near_duplicates_are_recognised_but_seasons_are_not_merged(self):
        seen = ["침구는 55~60℃ 이상 뜨거운 물로 주 1회 세탁", "진드기 차단(anti-mite) 커버로 매트리스·베개·이불 감싸기"]
        assert is_duplicate_tip("침구 주 1회 55~60℃ 이상 세탁", seen)
        assert is_duplicate_tip("진드기 차단 커버 사용", seen)
        assert not is_duplicate_tip("외출 시 마스크·안경 착용", seen)
        assert not is_duplicate_tip("가을철 꽃가루 농도 높은 날 외출 자제",
                                    ["봄철 꽃가루 농도 높은 날 외출 자제(오전·바람 부는 날 주의)"])

    def test_single_allergen_keeps_its_own_order(self):
        res, *_ = _run([BIRCH], {"pollen_season__spring_tree": "yes"}, _kr())
        a = res.assessments[0]
        tips = allocated_tips([("자작나무 꽃가루", a)], None, limit=3)
        assert [t["tip"] for t in tips] == a.kb["avoidance_control_ko"][:3]


# ---------------------------------------------------------------------------
# F. 저장형 HTML 주입 — 사용자·OCR 값은 글자 그대로만 보인다
# ---------------------------------------------------------------------------
PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    "<script>alert(2)</script>",
    "[x](javascript:alert(3))",
    "{: onmouseover=alert(4) }",
    '"><svg onload=alert(5)>',
    "</td></tr></table><script>alert(6)</script>",
    "a|b|<b>c</b>\n\n# h\n<script>alert(7)</script>",
    "![i](x){: onerror=alert(8) }",
    '<a href="javascript:alert(9)">k</a>',
]


class _Active(HTMLParser):
    """실행되거나 외부로 나가는 것: 이벤트 속성, javascript: 주소, 주입된 태그, 스크립트 본문."""

    def __init__(self, allowed_tags=()):
        super().__init__()
        self.allowed, self.found, self.scripts, self._in_script = set(allowed_tags), [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._in_script += 1
        for k, v in attrs:
            if k.startswith("on") or "javascript:" in (v or "").lower():
                self.found.append((tag, k))
        if tag in ("img", "svg", "iframe", "object", "embed", "base", "form") and tag not in self.allowed:
            self.found.append((tag,))
        # 링크는 우리가 쓴 출처 링크(https)만 있어야 한다
        if tag == "a" and not (dict(attrs).get("href") or "").startswith("https://"):
            self.found.append((tag, "href"))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script -= 1

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)


def _active(html_text, allowed_tags=()):
    p = _Active(allowed_tags)
    p.feed(html_text)
    return p


def _hostile_outputs(payload):
    """모든 사용자·OCR 값에 payload 를 넣은 환자."""
    from server import ocr_demo
    d = json.loads(ocr_demo().body)
    tpl = d["results"][0]
    pos = dict(tpl, category=None, class_value=3, value=5.0, value_text=None, interpretation="Positive")
    rows = [dict(pos, allergen_name=f"{en} {payload}", korean_name=f"{ko} {payload}")
            for en, ko in (MITE_F, BIRCH, CAT, ("Peach", "복숭아"), VENOM)]
    rows.append(dict(pos, allergen_name=payload, korean_name=payload, category="mite"))
    rows.append(dict(pos, allergen_name=f"Neg {payload}", korean_name=f"음성 {payload}", class_value=0, value=0.0,
                     value_text=payload, size_text=payload, interpretation="Negative"))
    rows.append(dict(pos, allergen_name=f"Eq {payload}", korean_name=f"경계 {payload}", class_value=payload,
                     value=None, mean_mm=2.5, value_text=payload, size_text=payload,
                     interpretation="Equivocal"))
    d["results"] = rows
    d["patient"]["name"] = payload
    ocr = OCRResult(**d)
    scr = ScreeningProfile(
        allergic_diseases=["allergic_rhinitis", payload], disease_other=payload, organ_systems=["nasal", payload],
        current_medications=[payload, "immunotherapy"], medication_note=payload, triggers_free_text=payload,
        pets=["cat", "other"], pets_other=payload, notes=payload, residence_country="KR",
        residence_region=payload, oas_foods=[payload], food_reaction_foods=[payload])
    res = get_relevance_service().build_assessments(ocr, scr)
    engine = get_questionnaire_engine()
    q = engine.build(res, scr)
    answers = {"symptom_pattern": "seasonal", "worse_seasons": ["spring", payload], "indoor_timing": "yes",
               "pollen_season__spring_tree": "yes", "sting_reaction": "systemic", "oral_allergy_syndrome": "yes",
               "food_general_react": [payload]}
    for i in range(len(res.assessments)):
        answers.update({f"animal_worse__agn{i}": "yes", f"animal_contact__agn{i}": "yes",
                        f"food_symptoms__agn{i}": ["oral", "skin", payload],
                        f"other_symptom__agn{i}": ["skin", payload], f"severity__agn{i}": payload})
    for sec in q["sections"]:
        for item in sec["questions"]:
            if item["id"].startswith("crossreact__") and item.get("options"):
                answers[item["id"]] = [o["value"] for o in item["options"][:3]] + [payload]
    engine.classify(res, answers, scr)
    info = {"name": payload, "age": payload, "gender": payload, "test_date": payload,
            "facility": payload, "report_date": payload}
    svc = get_report_service()
    md = svc.build_patient_report_markdown(res, info, scr)
    return {
        "report_markdown": md,
        # server.py 가 report_html 을 만드는 방식 그대로
        "report_html": md_lib.markdown(md, extensions=["extra", "sane_lists"]),
        "report_document_html": svc.build_patient_report_html_document(res, info, scr),
        "cardnews_html": get_cardnews_service().generate_html(res, info, scr),
        "cardnews_classic_html": get_classic_cardnews_service().generate_html(res, info, scr),
    }


class TestNoStoredHtmlInjection:
    def test_detector_sees_a_raw_payload(self):
        """양성 대조 — 탐지기가 이스케이프되지 않은 값을 실제로 잡는다."""
        raw = md_lib.markdown("# " + PAYLOADS[0] + "\n\n" + PAYLOADS[2] + "\n\n| a |\n|---|\n| x |\n{: onclick=alert(1) }",
                              extensions=["extra", "sane_lists"])
        found = _active(raw).found
        assert ("img", "onerror") in found and ("a", "href") in found
        assert any(attr == "onclick" for _tag, *rest in found for attr in rest), "attr_list 로 붙은 이벤트 속성"

    @pytest.mark.parametrize("payload", PAYLOADS)
    def test_payload_in_every_field_stays_inert(self, payload):
        out = _hostile_outputs(payload)
        md = out["report_markdown"]
        assert "alert(" in md, "payload 가 리포트에 닿지 않으면 이 테스트는 아무것도 검사하지 않는다"
        for raw in ("<img", "<script", "<svg", "<a ", "<b>", "</td>"):
            assert raw not in md, f"리포트 Markdown 에 원시 HTML 이 남았다: {raw}"
        assert "](javascript:" not in md and "{:" not in md
        assert not any(line.lstrip().startswith("# h") for line in md.split("\n")), "줄바꿈으로 새 블록이 열렸다"

        for key in ("report_html", "report_document_html"):
            p = _active(out[key])
            assert p.found == [] and p.scripts == [], (key, p.found[:3])
            assert "alert(" in out[key], "값은 글자로는 보여야 한다"
        for key in ("cardnews_html", "cardnews_classic_html"):
            p = _active(out[key], allowed_tags=("svg",))      # 카테고리 스탬프는 우리 SVG
            assert p.found == [], (key, p.found[:3])
            assert not any("alert(" in s for s in p.scripts)
        # 스크립트는 덱마다 하나이고, 본문은 코드에 적힌 고정 문자열과 글자 하나까지 같다 — 어떤 사용자 값도
        # 스크립트에 닿지 않는다('하나뿐'만 보면 그 하나에 값이 섞여도 통과한다).
        from services.cardnews_service import CardNewsService
        from services.cardnews_classic import ClassicCardNewsService
        for key, script in (("cardnews_html", CardNewsService._CHECK_JS + CardNewsService._DECK_JS),
                            ("cardnews_classic_html", CardNewsService._CHECK_JS + ClassicCardNewsService._SHELL_JS)):
            assert out[key].count("<script") == 1, key
            assert "".join(_active(out[key]).scripts) == script, key
            assert "alert(" not in script
        # 표 구조가 값 때문에 깨지지 않는다(| 는 칸을 나누지 못한다)
        assert out["report_html"].count("<table>") == md.count("|---|---|") > 0

    def test_report_document_forbids_scripts(self):
        doc = _hostile_outputs(PAYLOADS[1])["report_document_html"]
        csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', doc)
        assert csp and doc.index(csp.group(0)) < doc.index("<style>"), "CSP 는 머리말 맨 앞에 둔다"
        policy = csp.group(1)
        assert "default-src 'none'" in policy and "script-src" not in policy and "base-uri 'none'" in policy
        assert "<script" not in doc and "onclick" not in doc and "window.print" not in doc

    def test_md_text_neutralises_markup_but_keeps_ordinary_names(self):
        for plain in ("자작나무 꽃가루", "Rye grass, perennial", "봄 (4~5월, 지역에 따라 3월부터)", "나무 꽃가루 혼합-1"):
            assert md_text(plain) == plain
        assert md_text("Hen's egg") == "Hen's egg"
        assert md_text("<b>x</b>") == "&lt;b&gt;x&lt;/b&gt;"
        assert md_text("[x](y) *a* _b_ `c` {: k=v } a|b \\") == \
            "&#91;x&#93;(y) &#42;a&#42; &#95;b&#95; &#96;c&#96; &#123;: k=v &#125; a&#124;b &#92;"
        assert md_text("a\n\n# b\r\n- c") == "a # b - c"
        assert md_text(None) == "" and md_text(3) == "3"


# ---------------------------------------------------------------------------
# G. 면역치료 — 키 없는 답, 카드의 '이미 치료 중' 안내, 개수와 목록, 별칭
# ---------------------------------------------------------------------------
class TestImmunotherapyConsistency:
    ROWS = [MITE_F, MITE_P, BIRCH, CAT]
    ANSWERS = {"indoor_timing": "yes", "pollen_season__spring_tree": "yes",
               "animal_worse__agn3": "yes", "animal_contact__agn3": "yes"}

    def test_suggested_question_has_a_keyless_answer(self):
        scr = _kr(pets=["cat"], allergic_diseases=["asthma"], current_medications=["immunotherapy"])
        res, *_ = _run(self.ROWS, self.ANSWERS, scr)
        svc = ResultChatService(api_key="")
        answer = [s["answer"] for s in svc.suggestions(res, "ko", screening=scr) if s["key"] == "immunotherapy"][0]
        listed = re.findall(r"^- (.+?) \(", answer, flags=re.M)
        assert listed == ["집먼지진드기", "자작나무 꽃가루", "고양이"]
        assert f"면역치료가 가능한 항원은 {len(listed)}가지입니다" in answer, "개수와 나열한 항원 수가 같다"
        assert "담당 의료진이 판단" in answer and "권고가 아니라" in answer
        assert "천식이 있다고 하셨습니다" in answer and "이미 면역치료를 받고 있다고 하셨습니다" in answer
        for lang in ("en", "zh"):       # server 는 screening 없이 부른다 — 그 경로도 답이 있어야 한다
            assert [s["answer"] for s in svc.suggestions(res, lang) if s["key"] == "immunotherapy"][0]

    def test_keyless_answer_when_nothing_qualifies(self):
        res, *_ = _run([("Peach", "복숭아")], {"food_symptoms__agn0": ["oral"]}, _kr())
        answer = [s["answer"] for s in ResultChatService(api_key="").suggestions(res)
                  if s["key"] == "immunotherapy"][0]
        assert "해당하는 것이 없습니다" in answer and "담당 의료진이 판단" in answer and "\n- " not in answer

    @pytest.mark.parametrize("classic", [False, True])
    def test_card_carries_the_already_on_immunotherapy_line(self, classic):
        scr = _kr(pets=["cat"], current_medications=["immunotherapy"])
        _res, _q, md, quest, classic_html = _run(self.ROWS, self.ANSWERS, scr)
        line = "이미 면역치료를 받고 있다고 하셨습니다. 치료 중인 항원이 위와 같은지 진료에서 확인하세요."
        assert line in md and line in _text(classic_html if classic else quest)
        _res, _q, _md, quest, classic_html = _run(self.ROWS, self.ANSWERS, _kr(pets=["cat"]))
        assert "이미 면역치료" not in _text(classic_html if classic else quest)

    def test_counts_agree_with_the_names_listed(self):
        """유럽·미국 집먼지진드기는 이름을 하나로 적는다 — 개수도 하나로 센다."""
        scr = _kr(pets=["cat"])
        res, _q, md, quest, classic = _run(self.ROWS, self.ANSWERS, scr)
        assert len(res.by_relevance(REL)) == 4
        row = re.search(r"\| 🔴 실제 주의 \| \*\*(\d+)\*\* \| [^|]+ \| ([^|]+) \|", md)
        assert int(row.group(1)) == len(row.group(2).split(", ")) == 3
        assert "총 **4개** 항목에 양성" in md and "확인된 알러젠은 3개**입니다" in md
        assert "하나로 묶어 센 것입니다" in md
        for html_text, label in ((quest, "진범 확정"), (classic, "실제 주의")):
            num = int(re.search(rf'<span class="num">(\d+)</span><span class="lbl">{label}</span>', html_text).group(1))
            relevant_card = html_text[html_text.find('class="section relevant"'):]
            relevant_card = relevant_card[:relevant_card.find('<div class="card"')]
            assert num == relevant_card.count('class="chip"') == 3
        assert "실제 증상과 연결된 알러젠은 <b>3가지</b>" in quest
        doc = get_report_service().build_patient_report_html_document(res, {"name": "t"}, scr)
        assert '<div class="tile relevant"><div class="tile-n">3</div>' in doc and "하나로 묶어 센 것입니다" in doc

    def test_no_grouping_note_when_nothing_is_grouped(self):
        res, _q, md, _quest, _c = _run([BIRCH, CAT], self.ANSWERS | {"animal_worse__agn1": "yes"}, _kr())
        assert "하나로 묶어" not in md
        doc = get_report_service().build_patient_report_html_document(res, {"name": "t"}, _kr())
        assert "하나로 묶어" not in doc

    @pytest.mark.parametrize("name,key", [
        ("White birch", "birch"), ("Silver birch", "birch"), ("Short ragweed", "ragweed"),
        ("Common ragweed", "ragweed"), ("Birch pollen", "birch"),
    ])
    def test_registry_aliases_reach_the_immunotherapy_list(self, name, key):
        """한글 이름 없이 영문 별칭만 와도 레지스트리가 같은 항원이라고 하면 목록에 닿는다."""
        entry = get_knowledge_service().immunotherapy_entry(name, "")
        assert entry and entry["key"] == key

    def test_bermuda_grass_stays_unlisted(self):
        ks = get_knowledge_service()
        assert ks.immunotherapy_entry("Bermuda grass", "우산잔디") is None
        res, _q, md, _quest, _c = _run([("Bermuda grass", "우산잔디"), ("White birch", ""), ("Short ragweed", "")],
                                       {"pollen_season__summer_grass": "yes", "pollen_season__spring_tree": "yes",
                                        "pollen_season__fall_weed": "yes"}, _kr())
        assert [r["entry"]["key"] for r in ks.immunotherapy_candidates(res.assessments)] == ["birch", "ragweed"]
        imt = md[md.find("## 💉"):]
        assert "자작나무 꽃가루" in imt and "돼지풀 꽃가루" in imt and "우산잔디" not in imt and "잔디(화본과)" not in imt


# ---------------------------------------------------------------------------
# H. 긴 카드 — 스크롤 단서(스크립트 없이도), 인쇄에서는 펼침
# ---------------------------------------------------------------------------
class TestLongCardsShowAScrollCue:
    def _html(self):
        _res, _q, _md, quest, _c = _run([CAT, MITE_F, BIRCH], TestAvoidanceTipsAreAllocated.ANSWERS
                                        | {"animal_worse__agn0": "yes", "animal_contact__agn0": "yes"},
                                        _kr(pets=["cat"]))
        return quest

    def test_without_javascript_cards_grow_instead_of_clipping(self):
        """스크립트가 없으면(메일 미리보기 등) 카드가 내용만큼 자란다 — 숨은 스크롤이 없다."""
        html_text = self._html()
        style = html_text[html_text.find("<style>"):html_text.find("</style>")]
        base = style[:style.find(".js body")]         # <html class="js"> 없이도 적용되는 규칙
        card = re.search(r"\n  \.card \{(.*?)\}", base, flags=re.S).group(1)
        assert "min-height:440px" in card and not re.search(r"(?<!-)height:\s*\d", card), "고정 높이가 남아 있다"
        assert "align-items:flex-start" in re.search(r"\n  \.deck \{(.*?)\}", base, flags=re.S).group(1)
        assert ".card > div { flex:1 0 auto;" in base

    def test_fixed_height_cards_carry_a_scroll_shadow(self):
        """스크립트가 켜지면 고정 높이 카드가 된다 — 아래에 내용이 더 있을 때만 보이는 그림자를 깐다."""
        html_text = self._html()
        style = html_text[html_text.find("<style>"):html_text.find("</style>")]
        assert ".js .card > div { flex:1 1 0; min-height:0; overflow-y:auto; }" in style
        rule = re.search(r"\.card > \.section \{(.*?)\}", style, flags=re.S).group(1)
        assert "background-attachment:local, local, scroll, scroll" in rule
        assert "radial-gradient" in rule and "scrollbar-width:thin" in rule

    def test_script_adds_a_more_pill_and_hides_it_at_the_end(self):
        html_text = self._html()
        script = html_text[html_text.find("<script>"):]
        assert "cn-more" in script and "scrollHeight - box.scrollTop - box.clientHeight" in script
        assert '<span data-k="more">↓ 아래로 더 있어요</span>' in html_text
        assert ".js .card.more .cn-more { display:block;" in html_text and ".cn-more, .cn-js { display:none; }" in html_text

    def test_print_shows_everything_and_no_cue(self):
        html_text = self._html()
        printed = html_text[html_text.find("@media print"):]
        printed = printed[:printed.find("</style>")]
        assert ".cn-more { display:none !important; }" in printed
        assert "overflow:visible !important" in printed and "height:auto" in printed
        assert "background-image:none !important" in printed

    def test_shell_is_self_contained_and_says_long_cards_scroll(self):
        html_text = self._html()
        # 고정 높이(스크립트 켜짐)일 때만 보이는 안내 — 스크립트가 없으면 카드가 자라므로 숨긴다
        assert '<span class="cn-js">긴 카드는 카드 안에서 위아래로 넘겨 보세요 · </span>' in html_text
        assert ".cn-more, .cn-js { display:none; }" in html_text and ".js .cn-js { display:inline; }" in html_text
        assert "캡처하여 공유할 수 있습니다" in html_text
        assert html_text.count("<script") == 1 and "<script src" not in html_text

    def test_card_order_and_clinical_text_are_untouched(self):
        """셸만 바꿨다 — 카드의 종류·순서는 그대로다."""
        kinds = re.findall(r'<div class="card" data-kind="([a-z]+)" data-i="(\d+)"', self._html())
        assert [int(i) for _k, i in kinds] == list(range(1, len(kinds) + 1))
        assert [k for k, _i in kinds] == [
            "cover", "profile", "relevant", "detail", "detail", "detail", "animal", "sensitized",
            "seasonality", "prevention",
            "detail",          # 주증상(코) 카드 — 문진에서 고른 부위마다 하나
            "treatment",
            "detail",          # 쓰는 약 카드 — 이 환자는 질환만 있고 약은 고르지 않았다
            "immunotherapy", "knowledge", "closing"]
