"""지역별 꽃가루 시기 + 초기 문진의 리포트·카드뉴스 반영.

배경
  ① 꽃가루 시기는 지역을 탄다. 한국은 전국 하나로 충분하지만 미국은 같은 수목 시즌이
     남동부 1월, 알래스카 4월로 석 달까지 차이 난다. 전국 평균을 쓰면 조언이 무의미해진다.
  ② 처음 물어본 동반 질환·주증상은 카드뉴스에 **전혀** 반영되지 않았다
     (`screening` 을 인자로 받기만 하고 어떤 카드도 쓰지 않았다).
"""
from datetime import date
from types import SimpleNamespace

import pytest

from models.schemas import ScreeningProfile
from services.pollen_forecast_service import (GOOGLE_PLANT_TO_ANTIGEN, PollenForecastService)


@pytest.fixture(scope="module")
def svc() -> PollenForecastService:
    return PollenForecastService(api_key="")      # 실시간 예보 끔


def _pollen(cat, name, kr):
    return SimpleNamespace(category=cat, allergen_name=name, korean_name=kr)


RAGWEED = _pollen("pollen_weed", "Ragweed pollen", "돼지풀 꽃가루")
BIRCH = _pollen("pollen_tree", "Birch pollen", "자작나무 꽃가루")
MITE = _pollen("mite", "Dermatophagoides farinae", "집먼지진드기")


class TestRegionalCalendar:
    def test_tree_season_differs_by_us_region(self, svc):
        """지역을 무시하면 안 되는 이유 — 같은 '수목'이 지역마다 다르다."""
        months = {r: svc.season_for("pollen_tree", "US", r)["months"]
                  for r in ("TX", "FL", "AK", "NY")}
        assert 12 in months["TX"], "텍사스는 겨울에 마운틴 시더 시즌이 있다"
        assert 1 in months["FL"] and 1 not in months["NY"]
        assert min(months["AK"]) == 4, "알래스카는 4월에야 시작한다"
        assert months["NY"] == [3, 4, 5]

    def test_state_code_resolves_to_region(self, svc):
        assert svc.resolve_region("US", "TX")["code"] == "SOUTH_CENTRAL"
        assert svc.resolve_region("US", "CA")["code"] == "CALIFORNIA"
        assert svc.resolve_region("US", "SOUTHEAST")["code"] == "SOUTHEAST"

    def test_korea_is_single_region(self, svc):
        r = svc.resolve_region("KR", None)
        assert r and r["code"] == "ALL"
        assert svc.season_for("pollen_tree", "KR", None)["months"] == [3, 4, 5]

    def test_unknown_country_returns_nothing(self, svc):
        assert svc.resolve_region("ZZ", None) is None
        assert svc.season_for("pollen_tree", None, None) is None

    def test_in_season_depends_on_today(self, svc):
        tx = svc.season_for("pollen_tree", "US", "TX")
        assert svc.in_season(tx["months"], date(2026, 12, 20)) is True
        assert svc.in_season(tx["months"], date(2026, 6, 20)) is False

    def test_every_us_state_maps_to_a_region(self, svc):
        states = {s for c in svc.countries() if c["code"] == "US"
                  for r in c["regions"] for s in r["states"]}
        assert len(states) == 50, f"주 매핑 누락: {50 - len(states)}개"
        for st in states:
            assert svc.resolve_region("US", st), st


class TestPatientCombination:
    def test_only_the_patients_pollens_are_listed(self, svc):
        out = svc.for_patient([RAGWEED, BIRCH, MITE], country="US", region="NY",
                              today=date(2026, 9, 15))
        names = {i["allergen_name"] for i in out["items"]}
        assert names == {"Ragweed pollen", "Birch pollen"}, "진드기는 꽃가루가 아니다"

    def test_in_season_now_is_computed(self, svc):
        out = svc.for_patient([RAGWEED, BIRCH], country="US", region="NY", today=date(2026, 9, 15))
        assert [i["korean_name"] for i in out["in_season_now"]] == ["돼지풀 꽃가루"]

    def test_no_region_means_no_claim(self, svc):
        """지역을 모르면 틀린 시기를 말하느니 아무 말도 하지 않는다."""
        assert svc.for_patient([RAGWEED]) == {"available": False, "reason": "no_region"}

    def test_no_pollen_allergen_means_no_block(self, svc):
        assert svc.for_patient([MITE], country="US", region="NY")["available"] is False


class TestLiveForecastAdapter:
    def test_no_key_degrades_quietly(self, svc):
        assert svc.live_forecast(40.7, -74.0) == {"available": False, "reason": "no_api_key"}

    def test_google_response_is_mapped_to_our_antigens(self):
        """Google 의 식물 코드를 이 앱의 항원 이름으로 옮긴다."""
        payload = {"regionCode": "US", "dailyInfo": [{
            "date": {"year": 2026, "month": 9, "day": 18},
            "pollenTypeInfo": [{"code": "WEED", "inSeason": True,
                                "indexInfo": {"value": 4, "category": "High"},
                                "healthRecommendations": ["창문을 닫으세요"]}],
            "plantInfo": [
                {"code": "RAGWEED", "displayName": "Ragweed", "inSeason": True,
                 "indexInfo": {"value": 4, "category": "High"}},
                {"code": "BIRCH", "displayName": "Birch", "inSeason": False,
                 "indexInfo": {"value": 0, "category": "None"}},
            ]}]}
        out = PollenForecastService._parse_google(payload)
        assert out["available"] and out["days"][0]["date"] == "2026-09-18"
        plants = out["days"][0]["plants"]
        assert plants["RAGWEED"]["antigen"] == "Ragweed pollen"
        assert plants["BIRCH"]["antigen"] == "Birch pollen"
        assert out["days"][0]["types"]["weed"]["in_season"] is True

    def test_juniper_and_cedar_map_to_the_cupressaceae_antigen(self):
        """마운틴 시더(Juniperus ashei)는 측백나무과라 삼나무 항원과 같은 과다."""
        for code in ("JUNIPER", "CYPRESS_PINE", "CEDAR"):
            assert GOOGLE_PLANT_TO_ANTIGEN[code] == "Japanese cedar"

    def test_live_values_win_over_the_calendar(self, monkeypatch):
        svc = PollenForecastService(api_key="fake")
        monkeypatch.setattr(svc, "live_forecast", lambda *a, **k: {
            "available": True, "days": [{"date": "2026-01-10", "types": {},
                                         "plants": {"RAGWEED": {
                                             "antigen": "Ragweed pollen", "in_season": True,
                                             "category_ko": "높음", "value": 4}}}]})
        out = svc.for_patient([RAGWEED], country="US", region="NY",
                              lat=40.7, lon=-74.0, today=date(2026, 1, 10))
        item = out["items"][0]
        assert out["live"] is True and item["source"] == "live"
        assert item["in_season"] is True, "1월이지만 실시간 예보가 시즌이라고 하면 그쪽을 따른다"
        assert item["level"] == "높음"


class TestScreeningReachesOutputs:
    """처음 물어본 정보가 실제로 산출물에 나타나는지."""

    def _result(self):
        from models.schemas import OCRResult
        from services.relevance_service import get_relevance_service
        from server import ocr_demo
        import json as _json
        ocr = OCRResult(**_json.loads(ocr_demo().body))
        return ocr, get_relevance_service().build_assessments(ocr, None)

    def test_cardnews_reflects_diseases_and_symptoms(self):
        """이전에는 screening 을 받기만 하고 어떤 카드에도 쓰지 않았다."""
        from services.cardnews_service import get_cardnews_service
        ocr, res = self._result()
        scr = ScreeningProfile(allergic_diseases=["asthma", "atopic_dermatitis"],
                               organ_systems=["nasal", "lower_airway"],
                               current_medications=["antihistamine"])
        html = get_cardnews_service().generate_html(res, {"name": "테스트"}, screening=scr)
        assert "처음 알려주신 정보" in html
        assert "천식" in html and "아토피 피부염" in html
        assert "하기도" in html
        assert "위음성" in html, "항히스타민 복용 시 SPT 주의가 나와야 한다"

    def test_cardnews_without_screening_skips_the_card(self):
        from services.cardnews_service import get_cardnews_service
        ocr, res = self._result()
        html = get_cardnews_service().generate_html(res, {"name": "테스트"}, screening=None)
        assert "처음 알려주신 정보" not in html

    def test_cardnews_season_card_needs_a_region(self):
        from services.cardnews_service import get_cardnews_service
        ocr, res = self._result()
        no_region = ScreeningProfile(allergic_diseases=["allergic_rhinitis"])
        assert "마운틴 시더" not in get_cardnews_service().generate_html(
            res, {"name": "t"}, screening=no_region)
        with_region = ScreeningProfile(allergic_diseases=["allergic_rhinitis"],
                                       residence_country="US", residence_region="TX")
        html = get_cardnews_service().generate_html(res, {"name": "t"}, screening=with_region)
        assert "증상의 계절성" in html and "마운틴 시더" in html

    def test_report_connects_symptom_sites_and_region(self):
        from services.report_service import get_report_service
        ocr, res = self._result()
        scr = ScreeningProfile(organ_systems=["lower_airway"], allergic_diseases=[],
                               residence_country="US", residence_region="TX")
        md = get_report_service().build_patient_report_markdown(res, {"name": "테스트"}, scr)
        assert "주증상 부위" in md
        assert "폐기능검사" in md, "하기도 증상인데 천식 진단이 없으면 확인을 권해야 한다"
        assert "증상의 계절성" in md and "마운틴 시더" in md

    def test_report_without_region_has_no_season_section(self):
        from services.report_service import get_report_service
        ocr, res = self._result()
        md = get_report_service().build_patient_report_markdown(
            res, {"name": "테스트"}, ScreeningProfile(organ_systems=["nasal"]))
        assert "마운틴 시더" not in md


class TestZipLookup:
    """우편번호로 지역을 정한다. 주 이름을 몰라도 되고 예보 좌표도 함께 얻는다."""

    @pytest.mark.parametrize("zip_code,state,region", [
        ("78701", "TX", "SOUTH_CENTRAL"),     # 오스틴
        ("33101", "FL", "SOUTHEAST"),          # 마이애미
        ("99501", "AK", "ALASKA"),             # 앵커리지
        ("10001", "NY", "NORTHEAST"),          # 뉴욕
        ("98101", "WA", "PACIFIC_NORTHWEST"),  # 시애틀
    ])
    def test_zip_resolves_state_and_region(self, svc, zip_code, state, region):
        out = svc.lookup_zip("US", zip_code)
        assert out["ok"] and out["state"] == state and out["region_code"] == region
        assert -180 <= out["lon"] <= 180 and -90 <= out["lat"] <= 90

    @pytest.mark.parametrize("bad", ["", "abc", "12", None, "0000"])
    def test_invalid_zip_is_rejected(self, svc, bad):
        assert svc.lookup_zip("US", bad)["ok"] is False

    def test_korea_does_not_use_zip(self, svc):
        """한국은 단일 권역이라 우편번호가 필요 없다."""
        assert svc.lookup_zip("KR", "06236") == {"ok": False, "reason": "unsupported_country"}

    def test_zip_alone_drives_the_calendar(self, svc):
        out = svc.for_patient([RAGWEED], country="US", postal_code="78701",
                              today=date(2026, 9, 15))
        assert out["available"] and out["region_code"] == "SOUTH_CENTRAL"

    def test_china_is_no_longer_offered(self, svc):
        """근거가 충분치 않아 중국은 제외했다 — 한국·미국만 지원."""
        assert {c["code"] for c in svc.countries()} == {"KR", "US"}
        assert svc.resolve_region("CN", "NORTH") is None


class TestSeasonality:
    def _screening(self, **kw):
        return ScreeningProfile(**kw)

    def test_agreement_with_reported_months_is_stated(self, svc):
        from models.schemas import SymptomSeasonPattern
        scr = self._screening(residence_country="US", residence_postal_code="78701",
                              season_pattern=SymptomSeasonPattern.SEASONAL, worse_months=[9, 10])
        out = svc.seasonality([RAGWEED, BIRCH], scr, today=date(2026, 9, 15))
        assert out["available"] and out["overlap_months"] and not out["mismatch"]

    def test_mismatch_is_flagged(self, svc):
        """환자가 말한 악화 시기가 알러젠 시즌과 어긋나면 다른 원인을 의심해야 한다."""
        from models.schemas import SymptomSeasonPattern
        scr = self._screening(residence_country="US", residence_postal_code="78701",
                              season_pattern=SymptomSeasonPattern.SEASONAL, worse_months=[6, 7])
        out = svc.seasonality([RAGWEED], scr, today=date(2026, 9, 15))
        assert out["mismatch"] is True and out["overlap_months"] == []

    def test_not_seasonal_when_no_seasonal_allergen_and_no_report(self, svc):
        out = svc.seasonality([MITE], self._screening())
        assert out == {"available": False, "reason": "not_seasonal"}

    def test_works_without_a_region(self, svc):
        """계절성은 알러젠 자체의 성질이라 거주지를 몰라도 말할 수 있다."""
        from models.schemas import SymptomSeasonPattern
        out = svc.seasonality([RAGWEED], self._screening(
            season_pattern=SymptomSeasonPattern.SEASONAL))
        assert out["available"] is True

    def test_report_and_cardnews_have_one_seasonality_section(self):
        """예전엔 계절성 절과 지역 절이 같은 말을 두 번 했다."""
        from models.schemas import OCRResult, SymptomSeasonPattern
        from services.relevance_service import get_relevance_service
        from services.report_service import get_report_service
        from services.cardnews_service import get_cardnews_service
        from server import ocr_demo
        import json as _json
        ocr = OCRResult(**_json.loads(ocr_demo().body))
        scr = ScreeningProfile(residence_country="US", residence_postal_code="78701",
                               season_pattern=SymptomSeasonPattern.SEASONAL, worse_months=[9])
        res = get_relevance_service().build_assessments(ocr, scr)
        md = get_report_service().build_patient_report_markdown(res, {"name": "t"}, scr)
        assert md.count("증상의 계절성") == 1
        assert "거주 지역 기준 꽃가루 시기" not in md
        assert "2주 전부터" in md, "시즌 대비 조언이 있어야 한다"
        html = get_cardnews_service().generate_html(res, {"name": "t"}, screening=scr)
        assert html.count("증상의 계절성") == 1
        assert "month-strip" in html
