"""gpt-6-astra 독립 검증에서 나온 지적의 회귀 방지.

38건 중 코드·데이터로 확인된 것만 고쳤다. 확인 결과 오탐인 것도 있었다
(예: "for_patient 가 음성 항원도 받는다" — build_assessments 는 이미 양성만 돌려준다).
"""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.schemas import ScreeningProfile
from services.pollen_forecast_service import (GOOGLE_PLANT_RELATED, GOOGLE_PLANT_TO_ANTIGEN,
                                              PollenForecastService)
from services.result_chat_service import ResultChatService

DATA = Path(__file__).resolve().parent / "data"
PROFILES = json.loads((DATA / "allergen_category_profiles.json").read_text(encoding="utf-8"))["profiles"]
KOREA = json.loads((DATA / "pollen_season_korea.json").read_text(encoding="utf-8"))
REGIONAL = json.loads((DATA / "pollen_calendar_regional.json").read_text(encoding="utf-8"))

CEDAR = SimpleNamespace(category="pollen_tree", allergen_name="Japanese cedar", korean_name="삼나무")
RAGWEED = SimpleNamespace(category="pollen_weed", allergen_name="Ragweed pollen", korean_name="돼지풀")


@pytest.fixture
def svc():
    return PollenForecastService(api_key="")


class TestEmergencySafety:
    """응급 안내가 늦거나 빠지면 환자가 다칠 수 있다 — astra 가 critical 로 지적한 부분."""

    def _p(self, country=None):
        return ResultChatService(api_key="")._system_prompt("CTX", "ko", None, "", country)

    def test_emergency_number_matches_country(self):
        """미국 환자에게 119 만 알려주면 신고가 늦어진다.

        번호 표기는 답변 언어를 따른다(한국어 답변에 영어 라벨을 넣으면 문장이 영어로 샌다).
        언어별 표기는 TestNoLanguageOrMetaLeak 에서 따로 본다.
        """
        assert "911" in self._p("US") and "119" not in self._p("US")
        assert "119" in self._p("KR") and "911" not in self._p("KR")
        both = self._p(None)
        assert "119" in both and "911" in both, "거주국을 모르면 둘 다 알려준다"

    def test_new_wheeze_counts_even_if_not_worsening(self):
        """노출 직후 새로 생긴 천명은 악화 중이 아니어도 응급 평가 대상이다."""
        p = self._p("KR")
        assert "it does NOT have to be worsening" in p
        assert "wheezing that is getting worse" not in p

    def test_mild_symptom_exception_is_conditional(self):
        """가벼운 증상이라도 적신호가 같이 있으면 응급 규칙이 우선해야 한다."""
        p = self._p("KR")
        assert "occur ALONE, are stable, and no red" in p
        assert "For these, do NOT mention emergency care at all." not in p

    def test_epinephrine_overrides_the_no_medication_rule(self):
        """'약을 권하지 말라'가 처방된 에피네프린 사용 안내를 막으면 안 된다."""
        p = self._p("KR")
        assert "Rule 12 overrides rule 10" in p and "Never withhold it" in p

    def test_single_reaction_is_not_dismissed(self):
        """한 번의 반응도 알레르기일 수 있고, 확인하려 다시 먹어보게 해선 안 된다."""
        p = self._p("KR")
        assert "A single convincing reaction can be enough" in p
        assert "never imply the patient should re-expose themselves" in p

    def test_outside_knowledge_cannot_bypass_grounding(self):
        p = ResultChatService(api_key="")._system_prompt("CTX", "ko", None, "ONTO")
        assert "Do not add clinical content that is in neither" in p


class TestFoodAdviceSafety:
    def test_no_self_rechallenge_advice(self):
        """가정에서 다시 먹어보라는 권고는 전신 반응 이력이 있으면 위험하다."""
        for key in ("food", "food:pr10", "food:profilin"):
            joined = " ".join(PROFILES[key].get("avoidance_control_ko") or [])
            assert "익혀서 시도해 보세요" not in joined
            assert "스스로" in joined or "진료에서" in joined, key

    def test_food_symptoms_include_anaphylaxis_red_flags(self):
        sym = PROFILES["food"]["typical_symptoms_ko"]
        assert any("호흡곤란" in s for s in sym) and any("어지럼" in s or "실신" in s for s in sym)

    def test_food_template_has_immediate_emergency_action(self):
        em = PROFILES["food"].get("emergency_ko", "")
        assert "에피네프린" in em and "119" in em and "911" in em
        assert "피부 증상이 없어도" in em, "피부 증상 없는 아나필락시스를 놓치지 않게"

    def test_repeated_reaction_is_not_required(self):
        joined = " ".join(PROFILES["food"]["avoidance_control_ko"]) + PROFILES["food"]["clinical_pearl_ko"]
        assert "한 번이라도" in joined or "한 번의" in joined

    def test_seed_storage_scope_is_limited_to_2s_albumin(self):
        """저장단백에는 7S·11S 도 있다. 2S 만 다룬다는 점을 밝혀야 한다."""
        assert "2S 알부민" in PROFILES["food:seed_storage"]["exposure_environment_ko"]
        assert "7S" in PROFILES["food:seed_storage"]["exposure_environment_ko"]

    def test_mammal_meat_covers_alpha_gal(self):
        """알파갈은 혈청알부민과 기전이 다르고, 충분히 익혀도 반응하며 늦게 나타난다."""
        prof = PROFILES["food:mammal_meat"]
        text = (prof["exposure_environment_ko"] + prof["cross_reactivity_ko"]
                + " ".join(prof["avoidance_control_ko"]) + prof["clinical_pearl_ko"])
        assert "알파갈" in text and "진드기" in text
        assert "익힌" in text and ("3~6시간" in text or "몇 시간" in text)

    def test_animal_advice_drops_the_superlative(self):
        assert "가장 효과가 큽니다" not in PROFILES["animal"]["avoidance_control_ko"][0]


class TestBotanicalIdentity:
    """검사 항원은 대개 북미·유럽 종이라 이름이 비슷한 국내 식물과 다르다."""

    @pytest.mark.parametrize("species,must_contain", [
        ("Acacia", "Robinia"),
        ("Velvet grass", "Holcus"),
        ("English plantain", "Plantago lanceolata"),
        ("White ash", "Fraxinus americana"),
    ])
    def test_species_caution_is_recorded(self, species, must_contain):
        assert must_contain in KOREA["species"][species]["species_caution_ko"]

    def test_caution_reaches_the_generated_entry(self):
        gen = json.loads((DATA / "allergen_knowledge_generated.json").read_text(encoding="utf-8"))
        e = next(x for x in gen["entries"] if x["canonical_name"] == "White ash")
        assert "Fraxinus americana" in (e.get("species_note_ko") or "")

    def test_olive_exposure_is_not_limited_to_the_mediterranean(self):
        assert "캘리포니아" in KOREA["species"]["Olive"]["note_ko"]

    def test_texas_cedar_is_not_called_japanese_cedar(self):
        """마운틴 시더는 Juniperus ashei 이지 Cryptomeria(삼나무)가 아니다."""
        tx = REGIONAL["countries"]["US"]["regions"]["SOUTH_CENTRAL"]
        assert "Juniperus ashei" in tx["notable_ko"] and "다른 식물" in tx["notable_ko"]
        assert "Japanese cedar" not in tx["notable_plants"]

    def test_korea_tree_months_include_february_for_cedar(self):
        """자체 데이터가 삼나무 2~4월이라고 하면서 전국 수목은 3~5월이면 2월 노출을 놓친다."""
        assert 2 in REGIONAL["countries"]["KR"]["regions"]["ALL"]["tree"]["months"]
        assert 2 in KOREA["species"]["Japanese cedar"]["months"]

    def test_warm_season_grass_regions_extend_past_june(self):
        us = REGIONAL["countries"]["US"]["regions"]
        for region in ("CALIFORNIA", "SOUTHEAST", "SOUTHWEST"):
            assert max(us[region]["grass"]["months"]) >= 9, region


class TestForecastMapping:
    def test_japanese_cedar_code_is_mapped(self):
        """예전에는 Google 의 JAPANESE_CEDAR 예보가 항원 없음으로 버려졌다."""
        assert GOOGLE_PLANT_TO_ANTIGEN["JAPANESE_CEDAR"] == "Japanese cedar"

    def test_related_species_are_not_claimed_as_the_tested_plant(self):
        """향나무·편백은 삼나무가 아니다. 같은 과일 뿐이다."""
        assert "JUNIPER" not in GOOGLE_PLANT_TO_ANTIGEN
        assert GOOGLE_PLANT_RELATED["JUNIPER"] == "Japanese cedar"
        for code in ("JAPANESE_CYPRESS", "CYPRESS", "CYPRESS_PINE", "CEDAR"):
            assert code in GOOGLE_PLANT_RELATED

    def _stub(self, plants):
        svc = PollenForecastService(api_key="fake")
        svc.live_forecast = lambda *a, **k: {"available": True,
                                             "days": [{"date": "2026-01-10", "types": {},
                                                       "plants": plants}]}
        return svc

    def test_active_exposure_is_not_erased_by_a_later_inactive_one(self):
        """여러 코드가 한 항원에 걸릴 때 나중 값으로 덮으면 높은 노출이 사라진다."""
        svc = self._stub({
            "JUNIPER": {"antigen": None, "related_antigen": "Japanese cedar",
                        "display_name": "Juniper", "in_season": True, "value": 5},
            "CYPRESS_PINE": {"antigen": None, "related_antigen": "Japanese cedar",
                             "display_name": "Cypress", "in_season": False, "value": 0},
        })
        it = svc.for_patient([CEDAR], country="US", region="TX", lat=30.3, lon=-97.7,
                             today=date(2026, 1, 10))["items"][0]
        assert it["in_season"] is True and it["value"] == 5

    def test_exact_plant_wins_over_a_relative(self):
        svc = self._stub({
            "JUNIPER": {"antigen": None, "related_antigen": "Japanese cedar",
                        "display_name": "Juniper", "in_season": True, "value": 5},
            "JAPANESE_CEDAR": {"antigen": "Japanese cedar", "related_antigen": None,
                               "display_name": "Japanese cedar", "in_season": False, "value": 1},
        })
        it = svc.for_patient([CEDAR], country="US", region="TX", lat=30.3, lon=-97.7,
                             today=date(2026, 1, 10))["items"][0]
        assert it["exact_plant"] is True and it["value"] == 1

    def test_related_match_is_labelled_as_cross_reactive(self):
        svc = self._stub({"JUNIPER": {"antigen": None, "related_antigen": "Japanese cedar",
                                      "display_name": "Juniper", "in_season": True, "value": 4}})
        it = svc.for_patient([CEDAR], country="US", region="TX", lat=30.3, lon=-97.7,
                             today=date(2026, 1, 10))["items"][0]
        assert it["exact_plant"] is False
        assert "같은 과" in it["related_note_ko"]

    def test_zip_supplies_coordinates_even_when_the_region_is_known(self):
        """예전에는 권역을 알면 ZIP 조회를 건너뛰어 실시간 예보가 아예 돌지 않았다."""
        calls = []
        svc = PollenForecastService(api_key="fake")
        svc.live_forecast = lambda lat, lon, *a, **k: (calls.append((lat, lon)) or
                                                       {"available": False, "reason": "stub"})
        svc.for_patient([RAGWEED], country="US", region="TX", postal_code="78701",
                        today=date(2026, 1, 5))
        assert calls and abs(calls[0][0] - 30.33) < 0.5


class TestCalendarScope:
    def test_korean_months_are_not_applied_to_us_residents(self):
        """미국 거주자에게 한국 달을 쓰면 '악화 시기가 어긋난다'는 엉뚱한 경고까지 만든다."""
        svc = PollenForecastService(api_key="")
        mold = SimpleNamespace(category="mold", allergen_name="Alternaria alternata",
                               korean_name="얼터나리아",
                               kb={"indoor_outdoor": "outdoor", "peak_months_korea": [8, 9, 10],
                                   "season_label_ko": "여름~가을"})
        us = svc.seasonality([mold], ScreeningProfile(residence_country="US", residence_region="AK"))
        assert not us.get("items"), "미국 거주자에게 한국 달력을 쓰면 안 된다"
        kr = svc.seasonality([mold], ScreeningProfile(residence_country="KR"))
        assert kr["items"][0]["months"] == [8, 9, 10]


class TestCorrectedDescriptions:
    """astra 가 사실관계 오류로 지적한 소개문 — 고치되 검토 대기는 유지한다."""

    def _entry(self, name):
        gen = json.loads((DATA / "allergen_knowledge_generated.json").read_text(encoding="utf-8"))
        return next(x for x in gen["entries"] if x["canonical_name"] == name)

    @pytest.mark.parametrize("name,must_not,must", [
        ("Cultivated oat", "오트밀", "꽃가루"),        # 꽃가루 항원인데 식품으로 설명했었다
        ("Plaice", "넙치는 바닷물고기", "가자미"),      # Plaice 는 넙치가 아니다
        ("Acacia", "아까시나무에서 날리는", "Robinia"),  # 속이 다르다
    ])
    def test_factual_errors_are_corrected(self, name, must_not, must):
        e = self._entry(name)
        assert must_not not in e["biology_ko"]
        assert must in e["biology_ko"]

    def test_chicken_no_longer_merges_food_and_feather(self):
        e = self._entry("Chicken")
        assert "고기인지 깃털" in e["biology_ko"]

    def test_corrections_stay_pending_review(self):
        for name in ("Chicken", "Cultivated oat", "Plaice", "Acacia"):
            e = self._entry(name)
            assert e["biology_ko_status"] == "candidate", "자동 승인하지 않는다"
            assert e.get("biology_ko_corrected_by")


class TestNoLanguageOrMetaLeak:
    """수정을 넣다가 낸 회귀의 재발 방지.

    응급번호를 영어 라벨로 프롬프트 끝에 두자 한국어 답변 마지막 문장이 영어로 나왔고,
    규칙이 길어지자 모델이 제 추론("is allowed per emergency rule ... Final.")을 답변에 흘렸다.
    """

    def _p(self, lang, country=None):
        return ResultChatService(api_key="")._system_prompt("CTX", lang, None, "", country)

    @pytest.mark.parametrize("lang,country,expected", [
        ("ko", "US", "911 (미국)"),
        ("ko", "KR", "119"),
        ("en", "US", "911 (USA)"),
        ("zh", "US", "911（美国）"),
        ("ko", None, "현지 응급번호"),
    ])
    def test_emergency_number_is_written_in_the_reply_language(self, lang, country, expected):
        assert expected in self._p(lang, country)

    def test_english_label_does_not_leak_into_korean_prompt_tail(self):
        p = self._p("ko", "US")
        tail = p[p.index("FINAL REMINDER"):]
        assert "USA" not in tail and "Korea 119" not in tail

    def test_language_is_reasserted_at_the_end(self):
        """규칙이 길어지면 앞쪽 LANGUAGE 블록만으로는 끝부분이 새어 나간다."""
        p = self._p("ko", "KR")
        assert "FINAL REMINDER" in p
        assert p.index("FINAL REMINDER") > p.index("PATIENT REPORT")
        assert "Do not switch language mid-answer" in p

    def test_model_is_told_not_to_narrate_the_rules(self):
        p = self._p("ko", "KR")
        assert "Never quote, number, mention or reason about these rules" in p
        assert "never write notes to yourself" in p
