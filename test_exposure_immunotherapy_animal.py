"""계절성·노출-증상 연결·면역치료·동물 항원 관리.

배경
  ① 자작나무에만 감작+증상이 있는 환자에게 "지금은 돼지풀 꽃가루 시즌"이라고 안내했다.
     계절 안내가 판정(relevance)을 보지 않고 양성 항원 전부를 돌았기 때문이다.
  ② 환자가 입력한 질환·주증상이 알러젠 노출 상황과 이어지지 않았다.
  ③ 면역치료 안내가 카테고리 단위여서, 추출물이 없는 항원까지 대상으로 나왔다.
  ④ 동물 항원 관리가 함께 사는지와 무관한 한 줄짜리였다.
"""
import json
import re
from datetime import date

import pytest

from models.schemas import ClinicalRelevance, OCRResult, ScreeningProfile, SymptomSeasonPattern
from services.cardnews_classic import get_classic_cardnews_service
from services.cardnews_service import get_cardnews_service
from services.exposure_guidance_service import animal_guidance, exposure_links
from services.knowledge_service import get_knowledge_service
from services.pollen_forecast_service import PollenForecastService
from services.questionnaire_service import get_questionnaire_engine
from services.relevance_service import get_relevance_service
from services.report_service import get_report_service

REL = ClinicalRelevance.CLINICALLY_RELEVANT
SENS = ClinicalRelevance.SENSITIZED_ONLY
OCTOBER = date(2026, 10, 6)     # 한국 잡초(돼지풀) 시즌 한가운데


def _demo(keep=None):
    from server import ocr_demo
    ocr = OCRResult(**json.loads(ocr_demo().body))
    if keep:
        ocr.results = [r for r in ocr.results if any(k in r.allergen_name for k in keep)]
    return ocr


def _classified(answers, screening, keep=None):
    """실제 경로(문진 엔진)로 판정한다. 반환: (결과, {항원명: assessment})"""
    res = get_relevance_service().build_assessments(_demo(keep), screening)
    engine = get_questionnaire_engine()
    engine.build(res, screening)
    engine.classify(res, answers, screening)
    return res, {a.allergen_name: a for a in res.assessments}


def _outputs(res, screening):
    """리포트·카드뉴스·클래식 카드뉴스의 본문 텍스트."""
    md = get_report_service().build_patient_report_markdown(res, {"name": "t"}, screening)
    quest = get_cardnews_service().generate_html(res, {"name": "t"}, screening)
    classic = get_classic_cardnews_service().generate_html(res, {"name": "t"}, screening)
    strip = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h[h.find("<body"):]))  # noqa: E731
    return md, strip(quest), strip(classic)


def _animal_key(res, name):
    from services.questionnaire_service import _key
    return _key(next(i for i, a in enumerate(res.assessments) if a.allergen_name == name))


# ---------------------------------------------------------------------------
# (1) 계절성 — 감작과 증상이 함께 확인된 항원만
# ---------------------------------------------------------------------------
class TestSeasonalityFollowsRelevance:
    BIRCH_ONLY = {"pollen_season__spring_tree": "yes", "pollen_season__fall_weed": "no"}

    def _birch_only(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"],
                               residence_country="KR")
        res, by = _classified(self.BIRCH_ONLY, scr, keep=("Birch", "Ragweed"))
        assert by["Birch pollen"].relevance == REL and by["Ragweed pollen"].relevance == SENS
        return res, scr

    def test_service_lists_only_the_symptomatic_pollen(self):
        res, scr = self._birch_only()
        out = PollenForecastService(api_key="").seasonality(res.assessments, scr, today=OCTOBER)
        assert [i["name"] for i in out["items"]] == ["자작나무 꽃가루"]
        assert out["in_season_now"] == [], "10월은 자작나무 시즌이 아니다"
        assert not set(out["predicted_months"]) & {8, 9, 10}
        assert out["excluded"] == ["돼지풀 꽃가루"], "뺀 항원은 숨기지 않고 따로 돌려준다"

    def test_birch_only_patient_is_not_told_it_is_ragweed_season(self, monkeypatch):
        import services.pollen_forecast_service as pfs

        class _October(date):
            @classmethod
            def today(cls):
                return OCTOBER
        monkeypatch.setattr(pfs, "date", _October)

        res, scr = self._birch_only()
        for text in _outputs(res, scr):
            assert "증상의 계절성" in text
            season = text[text.find("증상의 계절성"):][:900]
            assert "자작나무 꽃가루" in season
            assert "돼지풀 꽃가루 시즌" not in text
            assert "돼지풀 꽃가루 — 늦여름" not in season and "| 돼지풀 꽃가루 |" not in season
            assert "넣지 않았" in season, "감작만 된 꽃가루는 뺐다는 사실을 적는다"

    def test_no_symptomatic_seasonal_allergen_means_no_calendar(self):
        """증상이 확인된 계절성 항원이 없으면 감작만 된 꽃가루로 달력을 채우지 않는다."""
        scr = ScreeningProfile(residence_country="KR")
        res, by = _classified({"pollen_season__spring_tree": "no", "pollen_season__fall_weed": "no"},
                              scr, keep=("Birch", "Ragweed"))
        assert all(a.relevance == SENS for a in by.values())
        out = PollenForecastService(api_key="").seasonality(res.assessments, scr, today=OCTOBER)
        assert out == {"available": False, "reason": "not_seasonal"}
        md, quest, classic = _outputs(res, scr)
        assert "증상의 계절성" not in md and "몇 월에" not in quest and "몇 월에" not in classic

    def test_reported_seasonal_pattern_without_a_culprit_prints_no_section(self):
        """환자가 '계절을 탄다'고 답했어도, 증상이 확인된 계절성 알러젠이 없으면 계절성 절을 만들지 않는다.

        예전에는 이 경우 리포트만 절을 찍었다(카드뉴스는 생략). 알릴 시기가 없어서 지역 메모(삼나무)와
        '시즌 대비' 문장만 남았고, 리포트와 카드가 서로 달랐다. 감작만 된 꽃가루는 '감작만' 절이 설명한다.
        """
        scr = ScreeningProfile(residence_country="KR", season_pattern=SymptomSeasonPattern.SEASONAL)
        res, _ = _classified({"pollen_season__spring_tree": "no", "pollen_season__fall_weed": "no"},
                             scr, keep=("Birch", "Ragweed"))
        md, quest, classic = _outputs(res, scr)
        assert "증상의 계절성" not in md and "시즌 대비" not in md and "삼나무" not in md
        assert "몇 월에" not in quest and "몇 월에" not in classic
        assert "자작나무 꽃가루" in md[md.find("감작만 된 알러젠"):], "감작만 된 꽃가루는 그 절에서 설명한다"

    def test_regional_forecast_skips_sensitized_only_pollen(self):
        res, scr = self._birch_only()
        out = PollenForecastService(api_key="").for_patient(
            res.assessments, country="KR", today=OCTOBER)
        assert [i["allergen_name"] for i in out["items"]] == ["Birch pollen"]
        assert out["in_season_now"] == []


# ---------------------------------------------------------------------------
# (2) 노출 → 증상 → 질환
# ---------------------------------------------------------------------------
class TestExposureSymptomLink:
    MITE = {"indoor_timing": "yes", "mite_dust": "yes"}

    def test_links_use_the_diseases_and_symptoms_the_patient_reported(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        res, by = _classified(self.MITE, scr, keep=("farinae",))
        link = exposure_links([("집먼지진드기", by["Dermatophagoides farinae"])], scr)["links"][0]
        assert "침구" in link["exposure"]
        assert link["targets"] == ["알레르기 비염"], "비염이 코 증상을 대표하므로 코를 또 적지 않는다"
        assert "이른 아침" in link["confirmed"], "문진에서 확인된 문장을 그대로 쓴다"
        assert any("밤새 침구" in n for n in link["notes"])

    def test_unreported_diseases_and_symptoms_are_not_invented(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], organ_systems=["nasal"])
        res, by = _classified(self.MITE, scr, keep=("farinae",))
        md, quest, classic = _outputs(res, scr)
        block = md[md.find("노출 → 증상 → 질환"):md.find("우선 실천 회피 수칙")]
        assert "알레르기 비염" in block
        for word in ("천식", "결막염", "아토피", "눈 증상", "피부 증상"):
            assert word not in block, f"환자가 말하지 않은 '{word}'"
        for card in (quest, classic):
            treat = card[card.find("치료와 연결하기"):][:600]
            assert "집먼지진드기" in treat and "침구" in treat and "알레르기 비염" in treat

    def test_reported_asthma_and_eye_symptoms_appear_when_reported(self):
        scr = ScreeningProfile(allergic_diseases=["asthma"], organ_systems=["ocular", "gi"])
        res, by = _classified(self.MITE, scr, keep=("farinae",))
        link = exposure_links([("집먼지진드기", by["Dermatophagoides farinae"])], scr)["links"][0]
        assert link["targets"] == ["천식", "눈 증상(가려움·충혈·눈물)"]
        assert not any("소화기" in t for t in link["targets"]), "진드기 노출로 설명되지 않는 부위는 잇지 않는다"

    def test_only_clinically_relevant_allergens_are_linked(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        res, by = _classified({"pollen_season__spring_tree": "yes", "pollen_season__fall_weed": "no"},
                              scr, keep=("Birch", "Ragweed"))
        out = exposure_links([(a.korean_name, a) for a in res.assessments], scr)
        assert [ln["label"] for ln in out["links"]] == ["자작나무 꽃가루"]

    def test_free_text_is_quoted_not_interpreted(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"],
                               triggers_free_text="이불 털 때", disease_other="비용종")
        res, by = _classified(self.MITE, scr, keep=("farinae",))
        md = get_report_service().build_patient_report_markdown(res, {"name": "t"}, scr)
        assert "“이불 털 때”" in md and "“비용종”" in md

    def test_no_link_block_without_a_culprit(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        res, _ = _classified({"indoor_timing": "no", "indoor_away": "no", "mite_dust": "no"},
                             scr, keep=("farinae",))
        md = get_report_service().build_patient_report_markdown(res, {"name": "t"}, scr)
        assert "노출 → 증상 → 질환" not in md


# ---------------------------------------------------------------------------
# (3) 면역치료 — 항원 단위, 감작 + 증상일 때만
# ---------------------------------------------------------------------------
class TestImmunotherapy:
    def test_every_entry_matches_the_registry_and_cites_a_source(self):
        ks = get_knowledge_service()
        data = ks._load_immunotherapy()
        registry = {a["id"] for a in json.load(open("data/allergens.json", encoding="utf-8"))["antigens"]}
        assert data["entries"]
        for e in data["entries"]:
            assert set(e["registry_ids"]) <= registry, e["key"]
            assert e["sources"] and all(s in data["sources"] for s in e["sources"]), e["key"]
            assert all(data["sources"][s]["content_verified"] for s in e["sources"]), \
                f"{e['key']}: 본문을 확인하지 못한 문헌을 근거로 쓰면 안 된다"
            assert set(e["routes"]) <= {"SCIT", "SLIT"} and e["evidence"] in data["evidence_levels"]
            assert isinstance(e["needs_review"], bool)

    @pytest.mark.parametrize("name,korean,key", [
        ("Dermatophagoides farinae", "집먼지진드기(D.farinae)", "house_dust_mite"),
        ("Birch pollen", "자작나무 꽃가루", "birch"),
        ("Cat dander", "고양이 비듬", "cat"),
        ("Timothy grass pollen", "", "grass"),
        ("Honey bee venom", "", "hymenoptera_venom"),
    ])
    def test_listed_antigens_resolve(self, name, korean, key):
        assert get_knowledge_service().immunotherapy_entry(name, korean)["key"] == key

    @pytest.mark.parametrize("name,korean", [
        ("Tyrophagus putrescentiae", "긴털가루진드기"),   # 진드기지만 면역치료 추출물이 없다
        ("Japanese hop pollen", "환삼덩굴 꽃가루"),        # 국내 추출물 없음(KAAACI 2023)
        ("Bermuda grass", "우산잔디"),                     # 다른 잔디와 교차항원성 없음
        ("German cockroach", "바퀴벌레(독일바퀴)"),        # needs_review — 환자에게 내보내지 않는다
        ("Peanut", "땅콩"), ("Hazelnut", "헤이즐넛"),      # 음식은 SCIT·SLIT 대상이 아니다
    ])
    def test_unlisted_or_unreviewed_antigens_do_not_qualify(self, name, korean):
        ks = get_knowledge_service()
        assert ks.immunotherapy_entry(name, korean) is None
        assert ks.immunotherapy_info(None, name, korean)["eligible"] is False

    def test_venom_is_not_lumped_with_cockroach(self):
        ks = get_knowledge_service()
        venom = ks.immunotherapy_entry("Honey bee venom")
        assert venom["evidence"] == "established" and venom["korea"]["scit"] == "not_approved"
        roach = ks.immunotherapy_entry("German cockroach", include_needs_review=True)
        assert roach["needs_review"] is True
        assert "벌독" in ks.immunotherapy_info("insect")["ko"]

    def test_section_names_only_sensitized_and_symptomatic_antigens(self):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], residence_country="KR")
        res, by = _classified({"pollen_season__spring_tree": "yes", "pollen_season__fall_weed": "no"},
                              scr, keep=("Birch", "Ragweed"))
        assert [r["entry"]["key"] for r in get_knowledge_service().immunotherapy_candidates(
            res.assessments)] == ["birch"]
        md, quest, classic = _outputs(res, scr)
        section = md[md.find("## 💉 면역치료"):md.find("## 4️⃣")]
        assert "진료에서 상의할 수 있는 선택지" in section and "권고가 아닙니다" in section
        assert "자작나무 꽃가루" in section and "돼지풀" not in section, "돼지풀은 감작만 됐다"
        assert "10.4168/aair.2023.15.6.725" in section, "출처를 단다"
        for card in (quest, classic):
            imt = card[card.find("💉 면역치료 진료에서"):][:700]
            assert "자작나무 꽃가루" in imt and "돼지풀" not in imt and "권고가 아니에요" in imt

    def test_no_section_when_nothing_qualifies(self):
        """면역치료 가능 항원에 감작돼 있어도 증상이 없으면 절·카드를 만들지 않는다."""
        scr = ScreeningProfile(residence_country="KR")
        res, by = _classified({"pollen_season__spring_tree": "no", "pollen_season__fall_weed": "no"},
                              scr, keep=("Birch", "Ragweed"))
        assert get_knowledge_service().immunotherapy_candidates(res.assessments) == []
        for text in _outputs(res, scr):
            assert "면역치료 — 진료에서" not in text and "💉 면역치료" not in text

    def test_two_mite_species_are_one_row(self):
        scr = ScreeningProfile()
        res, _ = _classified({"indoor_timing": "yes"}, scr, keep=("farinae", "pteronyssinus"))
        rows = get_knowledge_service().immunotherapy_candidates(res.assessments)
        assert len(rows) == 1 and len(rows[0]["allergens"]) == 2
        md = get_report_service().build_patient_report_markdown(res, {"name": "t"}, scr)
        assert md.count("| **집먼지진드기** |") == 1


# ---------------------------------------------------------------------------
# (4) 동물 항원 — 함께 사는 경우 / 키우지 않는 경우
# ---------------------------------------------------------------------------
class TestAnimalGuidance:
    def _cat(self, pets, pets_other=None):
        scr = ScreeningProfile(allergic_diseases=["allergic_rhinitis"], pets=pets, pets_other=pets_other)
        res = get_relevance_service().build_assessments(_demo(("Cat",)), scr)
        key = _animal_key(res, "Cat dander")
        engine = get_questionnaire_engine()
        engine.build(res, scr)
        engine.classify(res, {f"animal_contact__{key}": "yes", f"animal_worse__{key}": "yes"}, scr)
        assert res.assessments[0].relevance == REL
        return res, scr

    def test_owner_and_non_owner_get_different_guidance(self):
        res, own = self._cat(["cat"])
        owner = animal_guidance(res.assessments[0], own)
        res2, non = self._cat(["none"])
        non_owner = animal_guidance(res2.assessments[0], non)
        assert owner["status"] == "owner" and non_owner["status"] == "non_owner"
        owner_text = " ".join(s["text"] for s in owner["steps"]) + " ".join(owner["extra"])
        non_text = " ".join(s["text"] for s in non_owner["steps"]) + " ".join(non_owner["extra"])
        # 공기청정기: 시험 결과가 엇갈린다는 점과, 효과가 없었던 시험·있었던 시험을 함께 적는다(2026-10 문헌 검토)
        for word in ("침실에는 들이지", "60℃", "1주가 되기 전", "20~24주", "결과가 엇갈렸",
                     "증상과 약 사용이 줄지 않았", "기도 과민성이 나아졌"):
            assert word in owner_text, word
        for word in ("고양이가 없는 집 40곳 중 38곳", "옷에 묻어", "다녀오면 옷을 갈아입어"):
            assert word in non_text, word
        assert "침실에는 들이지" not in non_text and "목욕" not in non_text, \
            "키우지 않는 환자에게 침실 분리·목욕을 말하지 않는다"

    def test_outputs_follow_the_pets_answer(self):
        res, own = self._cat(["cat"])
        res2, non = self._cat(["dog"])        # 개는 키우지만 고양이는 키우지 않는다
        for a, b in zip(_outputs(res, own), _outputs(res2, non)):
            assert "함께 살고 있다면" in a and "20~24주" in a
            assert "키우지 않아도 노출됩니다" in b and "함께 살고 있다면" not in b
            assert "침실에 반려동물 출입 금지" not in b, "일반 한 줄 수칙이 남아 있으면 안 된다"

    def test_unknown_ownership_does_not_guess(self):
        res, scr = self._cat([])
        g = animal_guidance(res.assessments[0], scr)
        assert g["status"] == "unknown" and "진료에서" in g["action_sentence"]
        assert not any("20~24주" in t for t in g["extra"])

    def test_species_specific_notes(self):
        from types import SimpleNamespace
        dog = SimpleNamespace(allergen_name="Dog dander", korean_name="개 비듬", category="animal")
        g = animal_guidance(dog, ScreeningProfile(pets=["dog"]))
        text = " ".join(s["text"] for s in g["steps"]) + " ".join(g["extra"])
        assert "주 2회" in text and "저알레르기 품종" in text
        assert "고양이 연구입니다" in text, "분리 후 기간은 고양이 자료라는 한계를 밝힌다"

    def test_other_pet_is_matched_from_free_text_only_when_named(self):
        from types import SimpleNamespace
        hamster = SimpleNamespace(allergen_name="Hamster", korean_name="햄스터", category="animal")
        assert animal_guidance(hamster, ScreeningProfile(
            pets=["other"], pets_other="햄스터 2마리"))["status"] == "owner"
        assert animal_guidance(hamster, ScreeningProfile(
            pets=["other"], pets_other="말티즈"))["status"] == "unknown"
        assert animal_guidance(hamster, ScreeningProfile(pets=["cat"]))["status"] == "non_owner"

    def test_every_statement_cites_a_known_source(self):
        data = json.load(open("data/animal_allergen_management.json", encoding="utf-8"))
        known = set(data["sources"])
        blocks = [data[blk] for blk in ("owner", "non_owner", "occupational")]
        blocks += [sp["contact_block"] for sp in data["species"].values() if sp.get("contact_block")]
        steps = [st for blk in blocks for st in blk["steps"]]
        steps += [st for sp in data["species"].values() for st in sp.get("steps", [])]
        cited = [s for st in steps for s in st["sources"]]
        # 문장마다 근거 강도를 적는다. 출처가 없는 문장은 진료 관행으로 표시돼 있어야 한다
        statements = steps + [data["owner"]["separation"], data["owner"]["timeline"]] + \
            [n for sp in data["species"].values() for n in sp["notes"]]
        assert all(st["strength"] in data["evidence_levels"] for st in statements)
        assert all(st["sources"] or st["strength"] == "expert_practice" for st in statements)
        assert all(v.get("verified") for v in data["sources"].values()), "읽은 범위(전문/초록)를 적는다"
        cited += data["owner"]["separation"]["sources"] + data["owner"]["timeline"]["sources"]
        cited += [s for sp in data["species"].values() for n in sp["notes"] for s in n["sources"]]
        assert cited and set(cited) <= known
        assert all(v.get("pmid") and v.get("doi") for v in data["sources"].values())

    def test_non_animal_allergen_has_no_animal_guidance(self):
        from types import SimpleNamespace
        mite = SimpleNamespace(allergen_name="Dermatophagoides farinae", korean_name="집먼지진드기",
                               category="mite")
        assert animal_guidance(mite, ScreeningProfile(pets=["cat"])) is None
