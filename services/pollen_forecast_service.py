"""Pollen Forecast Service — 거주 지역에 맞는 꽃가루 시기·예보.

왜 필요한가
  꽃가루 시기는 지역을 타는데, 한국은 국토가 좁아 전국 하나로 충분하다. 미국은 다르다.
  같은 수목 시즌이 남동부는 1월, 알래스카는 4월에 시작한다. 텍사스는 한겨울(12~2월)에
  마운틴 시더가 정점을 찍는다. 전국 평균을 쓰면 "봄에 조심하세요" 같은 무의미한 조언이 된다.

두 층으로 답한다
  1) **지역 달력**(기본, 키 불필요): `data/pollen_calendar_regional.json`.
     국가+지역(미국은 주)만 있으면 오프라인에서 동작한다.
  2) **실시간 예보**(선택): Google Pollen API. `GOOGLE_POLLEN_API_KEY` 가 있으면
     위경도로 최대 5일 예보를 받아 지역 달력보다 우선한다. 응답의 식물 코드를 이 앱의
     항원 이름으로 매핑해, **이 환자가 양성인 꽃가루만** 골라 보여준다.

실시간 예보가 없어도 기능이 죽지 않는다. 키가 없거나 호출이 실패하면 지역 달력으로 떨어진다.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

CALENDAR_PATH = BASE_DIR / "data" / "pollen_calendar_regional.json"
GOOGLE_ENDPOINT = "https://pollen.googleapis.com/v1/forecast:lookup"

# 우리 항원 카테고리 → 지역 달력의 꽃가루 타입
CATEGORY_TO_TYPE = {"pollen_tree": "tree", "pollen_grass": "grass", "pollen_weed": "weed"}

# Google Pollen API 의 식물 코드 → 이 앱의 항원 canonical_name.
# 주의: JUNIPER/CYPRESS 계열은 측백나무과라 국내 '삼나무' 항원과 같은 과다(마운틴 시더 포함).
GOOGLE_PLANT_TO_ANTIGEN = {
    "ALDER": "Alder", "BIRCH": "Birch pollen", "HAZEL": "Hazel", "HORNBEAM": "Hornbeam",
    "BEECH": "Beech", "OAK": "Oak pollen", "ELM": "Elm", "ASH": "White ash",
    "PINE": "Pine", "OLIVE": "Olive", "MAPLE": "Tree mixture 1", "COTTONWOOD": "Poplar",
    "JUNIPER": "Japanese cedar", "CYPRESS_PINE": "Japanese cedar", "CEDAR": "Japanese cedar",
    "GRAMINALES": "Grass", "RAGWEED": "Ragweed pollen", "MUGWORT": "Mugwort pollen",
}

TYPE_LABEL_KO = {"tree": "수목", "grass": "잔디", "weed": "잡초"}
UPI_CATEGORY_KO = {
    "NONE": "없음", "VERY_LOW": "매우 낮음", "LOW": "낮음",
    "MODERATE": "보통", "HIGH": "높음", "VERY_HIGH": "매우 높음",
}


class PollenForecastService:
    def __init__(self, calendar_path: Optional[Path] = None, api_key: Optional[str] = None):
        self.path = Path(calendar_path or CALENDAR_PATH)
        # api_key=None → 환경변수에서 읽는다. api_key="" → 실시간 예보 끔(테스트·오프라인).
        self.api_key = (os.getenv("GOOGLE_POLLEN_API_KEY", "") if api_key is None else api_key)
        self._lock = threading.Lock()
        self._data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # 지역 달력
    # ------------------------------------------------------------------
    @property
    def data(self) -> Dict[str, Any]:
        if self._data is None:
            with self._lock:
                if self._data is None:
                    try:
                        self._data = json.loads(self.path.read_text(encoding="utf-8"))
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"지역 꽃가루 달력 로드 실패: {e}")
                        self._data = {"countries": {}}
        return self._data

    def countries(self) -> List[Dict[str, Any]]:
        out = []
        for code, c in (self.data.get("countries") or {}).items():
            out.append({"code": code, "label_ko": c.get("label_ko"),
                        "single_region": bool(c.get("single_region")),
                        "note_ko": c.get("regions_note_ko"),
                        "regions": [{"code": rc, "label_ko": r.get("label_ko"),
                                     "states": r.get("states", [])}
                                    for rc, r in (c.get("regions") or {}).items()]})
        return out

    def resolve_region(self, country: Optional[str], region: Optional[str]) -> Optional[Dict[str, Any]]:
        """국가+지역(또는 미국 주 코드)으로 달력을 찾는다."""
        c = (self.data.get("countries") or {}).get((country or "").upper())
        if not c:
            return None
        regions = c.get("regions") or {}
        if c.get("single_region"):
            key = next(iter(regions), None)
            return dict(regions[key], code=key, country=country.upper()) if key else None
        want = (region or "").upper()
        if want in regions:
            return dict(regions[want], code=want, country=country.upper())
        # 미국은 주 코드로도 찾는다(CA → CALIFORNIA)
        for rc, r in regions.items():
            if want in [s.upper() for s in (r.get("states") or [])]:
                return dict(r, code=rc, country=country.upper())
        return None

    def season_for(self, category: str, country: Optional[str],
                   region: Optional[str]) -> Optional[Dict[str, Any]]:
        """이 카테고리의 지역 시기. 지역을 못 찾으면 None(기존 기본값을 쓰라는 뜻)."""
        t = CATEGORY_TO_TYPE.get(category)
        if not t:
            return None
        r = self.resolve_region(country, region)
        if not r or t not in r:
            return None
        out = dict(r[t])
        out.update({"type": t, "region_code": r.get("code"), "region_label_ko": r.get("label_ko"),
                    "notable_ko": r.get("notable_ko"), "source": "regional_calendar"})
        return out

    @staticmethod
    def in_season(months: List[int], today: Optional[date] = None) -> bool:
        return (today or date.today()).month in (months or [])

    # ------------------------------------------------------------------
    # 실시간 예보 (Google Pollen API)
    # ------------------------------------------------------------------
    @property
    def live_available(self) -> bool:
        return bool(self.api_key)

    def live_forecast(self, lat: float, lon: float, days: int = 3,
                      language: str = "ko") -> Dict[str, Any]:
        """Google Pollen API 예보. 키가 없거나 실패하면 available=False 로 조용히 돌아간다."""
        if not self.api_key:
            return {"available": False, "reason": "no_api_key"}
        try:
            import httpx
            params = {"key": self.api_key, "location.latitude": lat, "location.longitude": lon,
                      "days": max(1, min(int(days), 5)), "languageCode": language,
                      "plantsDescription": "false"}
            r = httpx.get(GOOGLE_ENDPOINT, params=params, timeout=10)
            r.raise_for_status()
            return self._parse_google(r.json())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"실시간 꽃가루 예보 실패: {e}")
            return {"available": False, "reason": str(e)[:200]}

    @staticmethod
    def _parse_google(payload: Dict[str, Any]) -> Dict[str, Any]:
        """UPI 지수와 식물별 값을 이 앱이 쓰는 모양으로 줄인다."""
        days = []
        for d in payload.get("dailyInfo", []) or []:
            dt = d.get("date") or {}
            day = {"date": f"{dt.get('year')}-{dt.get('month'):02d}-{dt.get('day'):02d}"
                           if dt.get("year") else None,
                   "types": {}, "plants": {}}
            for t in d.get("pollenTypeInfo", []) or []:
                idx = t.get("indexInfo") or {}
                day["types"][(t.get("code") or "").lower()] = {
                    "in_season": bool(t.get("inSeason")),
                    "value": idx.get("value"), "category": idx.get("category"),
                    "category_ko": UPI_CATEGORY_KO.get((idx.get("category") or "").upper().replace(" ", "_")),
                    "recommendations": (t.get("healthRecommendations") or [])[:2],
                }
            for p in d.get("plantInfo", []) or []:
                idx = p.get("indexInfo") or {}
                code = p.get("code") or ""
                day["plants"][code] = {
                    "antigen": GOOGLE_PLANT_TO_ANTIGEN.get(code),
                    "display_name": p.get("displayName"),
                    "in_season": bool(p.get("inSeason")),
                    "value": idx.get("value"), "category": idx.get("category"),
                    "category_ko": UPI_CATEGORY_KO.get((idx.get("category") or "").upper().replace(" ", "_")),
                }
            days.append(day)
        return {"available": True, "source": "google_pollen_api",
                "region_code": payload.get("regionCode"), "days": days}

    # ------------------------------------------------------------------
    # 환자 결합
    # ------------------------------------------------------------------
    def for_patient(self, assessments, country: Optional[str] = None, region: Optional[str] = None,
                    lat: Optional[float] = None, lon: Optional[float] = None,
                    today: Optional[date] = None) -> Dict[str, Any]:
        """이 환자가 양성인 꽃가루만 골라 '지금 시즌인지'를 붙인다.

        실시간 예보가 있으면 그 값을 쓰고, 없으면 지역 달력으로 답한다.
        지역 정보가 아예 없으면 available=False — 화면에서 이 블록을 숨기면 된다.
        """
        pollens = [a for a in (assessments or [])
                   if CATEGORY_TO_TYPE.get(getattr(a, "category", None) or "")]
        if not pollens:
            return {"available": False, "reason": "no_pollen_allergen"}

        region_info = self.resolve_region(country, region)
        live = {}
        if lat is not None and lon is not None and self.live_available:
            fc = self.live_forecast(lat, lon)
            if fc.get("available") and fc.get("days"):
                today_day = fc["days"][0]
                for code, p in (today_day.get("plants") or {}).items():
                    if p.get("antigen"):
                        live[p["antigen"]] = p
                live_types = today_day.get("types") or {}
            else:
                live_types = {}
        else:
            live_types = {}

        items = []
        for a in pollens:
            cat = a.category
            name = getattr(a, "allergen_name", None)
            entry = {"allergen_name": name,
                     "korean_name": getattr(a, "korean_name", None),
                     "category": cat, "type_ko": TYPE_LABEL_KO.get(CATEGORY_TO_TYPE[cat])}
            hit = live.get(name)
            if hit:
                entry.update({"source": "live", "in_season": hit["in_season"],
                              "level": hit.get("category_ko") or hit.get("category"),
                              "value": hit.get("value")})
            else:
                season = self.season_for(cat, country, region)
                if season:
                    entry.update({"source": "regional_calendar",
                                  "in_season": self.in_season(season.get("months") or [], today),
                                  "season_label_ko": season.get("label_ko"),
                                  "months": season.get("months")})
                else:
                    entry.update({"source": "unknown", "in_season": None})
            items.append(entry)

        if not region_info and not live:
            return {"available": False, "reason": "no_region"}

        return {
            "available": True,
            "country": (country or "").upper() or None,
            "region_code": region_info.get("code") if region_info else None,
            "region_label_ko": region_info.get("label_ko") if region_info else None,
            "notable_ko": region_info.get("notable_ko") if region_info else None,
            "live": bool(live),
            "live_types": live_types,
            "items": items,
            "in_season_now": [i for i in items if i.get("in_season")],
        }


_pollen_service: Optional[PollenForecastService] = None


def get_pollen_forecast_service() -> PollenForecastService:
    global _pollen_service
    if _pollen_service is None:
        _pollen_service = PollenForecastService()
    return _pollen_service
