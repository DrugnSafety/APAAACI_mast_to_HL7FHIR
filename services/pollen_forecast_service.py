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
ZIP3_PATH = BASE_DIR / "data" / "us_zip3_centroids.json"
GOOGLE_ENDPOINT = "https://pollen.googleapis.com/v1/forecast:lookup"

# 우리 항원 카테고리 → 지역 달력의 꽃가루 타입
CATEGORY_TO_TYPE = {"pollen_tree": "tree", "pollen_grass": "grass", "pollen_weed": "weed"}

# Google Pollen API 의 식물 코드 → 이 앱의 항원 canonical_name (같은 식물).
GOOGLE_PLANT_TO_ANTIGEN = {
    "ALDER": "Alder", "BIRCH": "Birch pollen", "HAZEL": "Hazel", "HORNBEAM": "Hornbeam",
    "BEECH": "Beech", "OAK": "Oak pollen", "ELM": "Elm", "ASH": "White ash",
    "PINE": "Pine", "OLIVE": "Olive", "MAPLE": "Tree mixture 1", "COTTONWOOD": "Poplar",
    "JAPANESE_CEDAR": "Japanese cedar",
    "GRAMINALES": "Grass", "RAGWEED": "Ragweed pollen", "MUGWORT": "Mugwort pollen",
}

# 같은 식물은 아니지만 같은 과(科)라 참고가 되는 코드. 예보를 보여주되 '검사한 그 식물'로
# 말하지 않는다 — 측백나무과 교차반응은 가능성이지 동일 항원이 아니다.
# (텍사스의 마운틴 시더 Juniperus ashei 가 여기 해당한다)
GOOGLE_PLANT_RELATED = {
    "JUNIPER": "Japanese cedar", "CYPRESS_PINE": "Japanese cedar",
    "CYPRESS": "Japanese cedar", "JAPANESE_CYPRESS": "Japanese cedar", "CEDAR": "Japanese cedar",
}

TYPE_LABEL_KO = {"tree": "수목", "grass": "잔디", "weed": "잡초"}
# 종별 자료가 없어 분류군(수목·잔디·잡초) 달력으로 답할 때 시기 뒤에 붙이는 말
CATEGORY_ESTIMATE_KO = {"tree": "수목 꽃가루 전체 기준 추정", "grass": "잔디 꽃가루 전체 기준 추정",
                        "weed": "잡초 꽃가루 전체 기준 추정"}
# 지식베이스의 시기가 그 항원(종) 자신의 값이 아니라 카테고리 기본값인 출처
_CATEGORY_LEVEL_SOURCES = {"category_default", "category_profile", "wikipedia"}
UPI_CATEGORY_KO = {
    "NONE": "없음", "VERY_LOW": "매우 낮음", "LOW": "낮음",
    "MODERATE": "보통", "HIGH": "높음", "VERY_HIGH": "매우 높음",
}


def _is_clinically_relevant(a) -> bool:
    """감작과 증상 발생이 함께 확인된 항원인가.

    검사 양성(감작)만으로는 그 꽃가루 시즌이 이 환자에게 의미가 있다고 말할 수 없다.
    '감작만'·'판정 보류'·아직 판정하지 않은 항원은 계절 안내에서 뺀다.
    """
    rel = getattr(a, "relevance", None)
    return getattr(rel, "value", rel) == "clinically_relevant"


def _stronger(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """예보 항목 비교 — 같은 식물(exact)이 우선, 그다음 시즌 여부, 그다음 지수."""
    if bool(a.get("exact")) != bool(b.get("exact")):
        return bool(a.get("exact"))
    if bool(a.get("in_season")) != bool(b.get("in_season")):
        return bool(a.get("in_season"))
    return (a.get("value") or 0) > (b.get("value") or 0)



class PollenForecastService:
    def __init__(self, calendar_path: Optional[Path] = None, api_key: Optional[str] = None,
                 zip3_path: Optional[Path] = None):
        self.path = Path(calendar_path or CALENDAR_PATH)
        self.zip3_path = Path(zip3_path or ZIP3_PATH)
        self._zip3: Optional[Dict[str, Any]] = None
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

    # ------------------------------------------------------------------
    # 우편번호
    # ------------------------------------------------------------------
    @property
    def zip3(self) -> Dict[str, Any]:
        if self._zip3 is None:
            with self._lock:
                if self._zip3 is None:
                    try:
                        self._zip3 = json.loads(
                            self.zip3_path.read_text(encoding="utf-8")).get("zip3", {})
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"ZIP3 표 로드 실패: {e}")
                        self._zip3 = {}
        return self._zip3

    def lookup_zip(self, country: Optional[str], postal_code: Optional[str]) -> Dict[str, Any]:
        """우편번호 → 주·권역·대표좌표.

        미국만 지원한다. 한국은 단일 권역이라 우편번호가 필요 없다.
        전체 우편번호(3만 3천 개) 대신 앞 3자리(911개)를 쓴다 — 권역 판단과 예보 좌표에는 충분하고
        저장소에 52KB 만 더한다.
        """
        code = "".join(ch for ch in str(postal_code or "") if ch.isdigit())
        if (country or "").upper() != "US":
            return {"ok": False, "reason": "unsupported_country"}
        if len(code) < 5:
            return {"ok": False, "reason": "invalid_zip"}
        hit = self.zip3.get(code[:3])
        if not hit:
            return {"ok": False, "reason": "unknown_zip"}
        region = self.resolve_region("US", hit["state"])
        return {"ok": True, "zip": code[:5], "state": hit["state"],
                "lat": hit["lat"], "lon": hit["lon"],
                "region_code": region.get("code") if region else None,
                "region_label_ko": region.get("label_ko") if region else None,
                "precision_note_ko": "우편번호 앞 3자리 기준이라 수십 km 오차가 있습니다."}

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
    # 항원 한 건의 시기 — 리포트·카드뉴스·달력이 모두 이 값 하나를 쓴다
    # ------------------------------------------------------------------
    def residence(self, screening) -> tuple:
        """문진의 거주지 → (국가, 지역). 지역을 못 찾으면 우편번호로 주(州)를 보탠다."""
        country = getattr(screening, "residence_country", None)
        region = getattr(screening, "residence_region", None)
        postal = getattr(screening, "residence_postal_code", None)
        if country and not self.resolve_region(country, region) and postal:
            z = self.lookup_zip(country, postal)
            if z.get("ok"):
                region = z["state"]
        return country, region

    def allergen_season(self, a, country: Optional[str] = None,
                        region: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """이 항원의 시기. 계절성 항원(꽃가루·실외 곰팡이)이 아니거나 알 수 없으면 None.

        반환: {"months", "label_ko"(화면에 쓰는 말), "level": "species" | "category"}

        한국 거주자는 종별 시기(data/pollen_season_korea.json 과 개별 지식베이스)를 먼저 쓴다.
        예전에는 지역 달력의 분류군 범위(수목 2~5월)를 종에 그대로 붙여, 자작나무만 있는 환자에게
        삼나무 때문에 넓어진 2월까지 '주의할 달'로 칠했고 같은 리포트 안에서 시기가 세 가지로 나왔다.
        종별 자료가 없거나 한국 밖이면 분류군 달력으로 답하되 추정이라고 밝힌다. 한국 달력을
        다른 나라 거주자에게 쓰지는 않는다.
        """
        cat = getattr(a, "category", None) or ""
        kb = getattr(a, "kb", None) or {}
        korea = (country or "KR").upper() == "KR"
        months = list(kb.get("peak_months_korea") or [])
        label = (kb.get("season_label_ko") or "").strip()
        if cat in CATEGORY_TO_TYPE:
            t = CATEGORY_TO_TYPE[cat]
            if korea and months and kb.get("source") not in _CATEGORY_LEVEL_SOURCES:
                return {"months": months, "label_ko": label, "level": "species"}
            regional = self.season_for(cat, country, region) if country else None
            if regional and regional.get("months"):
                months, label = list(regional["months"]), (regional.get("label_ko") or "").strip()
            elif not (korea and months):
                return None
            est = CATEGORY_ESTIMATE_KO[t]
            return {"months": months, "label_ko": f"{label} · {est}" if label else est,
                    "level": "category"}
        if cat == "mold" and korea and (kb.get("indoor_outdoor") or "") == "outdoor" and months:
            return {"months": months, "label_ko": label, "level": "species"}
        return None

    def season_label(self, a, screening=None) -> str:
        """칩·한 줄 요약·노출 문장에 쓰는 시기 라벨. 꽃가루는 allergen_season 과 같은 값이다."""
        if (getattr(a, "category", None) or "") in CATEGORY_TO_TYPE:
            season = self.allergen_season(a, *self.residence(screening))
            return season["label_ko"] if season else ""
        return ((getattr(a, "kb", None) or {}).get("season_label_ko") or "").strip()

    def regional_note(self, assessments, country: Optional[str], region: Optional[str]) -> str:
        """그 지역에서 알아둘 점 — 이 환자에게 **증상까지 확인된** 항원에 해당할 때만.

        달력의 notable_ko 는 특정 식물 이야기다(한국: 삼나무). 돼지풀이나 자작나무에만 증상이 있는
        환자에게 삼나무 안내를 붙이지 않도록 notable_applies_to 로 가린다. 값은 두 가지다.
          - 항원 이름: 그 항원에 증상이 확인된 환자에게 보여준다.
          - tree/grass/weed: 그 분류군 달력으로 **추정한** 시기를 받은 환자에게 보여준다. 메모가 그
            추정 범위가 왜 그런지(텍사스 수목 달력의 겨울은 마운틴 시더)를 설명하기 때문이다.
            종별 시기를 받은 항원에는 해당하지 않는다.
        """
        r = self.resolve_region(country, region) if country else None
        if not r or not r.get("notable_ko"):
            return ""
        applies = {str(x).lower().replace(" pollen", "") for x in (r.get("notable_applies_to") or [])}
        if not applies:
            return ""
        for a in assessments or []:
            cat = getattr(a, "category", None) or ""
            if cat not in CATEGORY_TO_TYPE or not _is_clinically_relevant(a):
                continue
            names = {getattr(a, "allergen_name", None), (getattr(a, "kb", None) or {}).get("canonical_name")}
            keys = {str(n).lower().replace(" pollen", "") for n in names if n}
            season = self.allergen_season(a, country, region)
            if season and season["level"] == "category":
                keys.add(CATEGORY_TO_TYPE[cat])
            if keys & applies:
                return r["notable_ko"]
        return ""

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
                    "related_antigen": GOOGLE_PLANT_RELATED.get(code),
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
                    today: Optional[date] = None,
                    postal_code: Optional[str] = None) -> Dict[str, Any]:
        """이 환자에게 **증상까지 확인된** 꽃가루만 골라 '지금 시즌인지'를 붙인다.
        검사만 양성인 꽃가루를 넣으면 증상과 무관한 시즌을 '지금 조심할 꽃가루'로 안내하게 된다.

        실시간 예보가 있으면 그 값을 쓰고, 없으면 지역 달력으로 답한다.
        지역 정보가 아예 없으면 available=False — 화면에서 이 블록을 숨기면 된다.
        """
        pollens = [a for a in (assessments or [])
                   if CATEGORY_TO_TYPE.get(getattr(a, "category", None) or "")
                   and _is_clinically_relevant(a)]
        if not pollens:
            return {"available": False, "reason": "no_pollen_allergen"}

        region_info = self.resolve_region(country, region)
        # 우편번호는 권역을 모를 때뿐 아니라 **좌표가 없을 때도** 쓴다. 예전에는 권역을 이미
        # 알면 ZIP 조회를 건너뛰어, 키가 있어도 실시간 예보가 한 번도 돌지 않았다.
        if postal_code and (region_info is None or lat is None or lon is None):
            z = self.lookup_zip(country, postal_code)
            if z.get("ok"):
                if region_info is None:
                    region = z["state"]
                    region_info = self.resolve_region(country, region)
                lat = lat if lat is not None else z["lat"]
                lon = lon if lon is not None else z["lon"]
        live = {}
        if lat is not None and lon is not None and self.live_available:
            fc = self.live_forecast(lat, lon)
            if fc.get("available") and fc.get("days"):
                today_day = fc["days"][0]
                # 여러 식물 코드가 한 항원에 걸릴 수 있다(측백나무과). 나중 값으로 덮어쓰면
                # 높은 노출이 낮은 값에 지워진다 — 더 강한 쪽을 남긴다.
                for code, p in (today_day.get("plants") or {}).items():
                    for key, exact in ((p.get("antigen"), True), (p.get("related_antigen"), False)):
                        if not key:
                            continue
                        cur = live.get(key)
                        cand = dict(p, exact=exact, plant_code=code)
                        if cur is None or _stronger(cand, cur):
                            live[key] = cand
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
                              "value": hit.get("value"),
                              "exact_plant": bool(hit.get("exact")),
                              "forecast_plant": hit.get("display_name") or hit.get("plant_code")})
                if not hit.get("exact"):
                    entry["related_note_ko"] = (
                        f"검사한 식물이 아니라 같은 과(科)인 {hit.get('display_name') or ''} 예보입니다. "
                        "교차반응 가능성을 참고하는 용도입니다.")
            else:
                season = self.allergen_season(a, country, region)
                if season:
                    entry.update({"source": ("species_calendar" if season["level"] == "species"
                                             else "regional_calendar"),
                                  "in_season": self.in_season(season["months"], today),
                                  "season_label_ko": season["label_ko"],
                                  "season_level": season["level"],
                                  "months": season["months"]})
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
            "notable_ko": self.regional_note(pollens, country, region) or None,
            "live": bool(live),
            "live_types": live_types,
            "items": items,
            "in_season_now": [i for i in items if i.get("in_season")],
        }


    # ------------------------------------------------------------------
    # 계절성 요약
    # ------------------------------------------------------------------
    MONTH_KO = ["1월", "2월", "3월", "4월", "5월", "6월",
                "7월", "8월", "9월", "10월", "11월", "12월"]

    def seasonality(self, assessments, screening=None, today: Optional[date] = None) -> Dict[str, Any]:
        """이 환자의 증상이 계절을 타는지, 탄다면 어느 달인지.

        세 가지를 합친다.
          1) **증상까지 확인된** 계절성 알러젠(꽃가루·실외 곰팡이)의 시기
             — 거주 지역이 있으면 지역 달력, 없으면 기본값
          2) 환자가 문진에서 답한 증상 패턴·악화 월
          3) 둘의 일치 여부 — 어긋나면 그 사실을 알려준다(다른 원인이 섞였을 수 있다)

        시기는 항원마다 allergen_season() 한 곳에서 정한다(종별 자료 우선, 없으면 분류군 추정).

        검사만 양성이고 그 시기에 증상이 확인되지 않은 계절성 알러젠은 달력에 넣지 않고
        excluded 로 따로 돌려준다. 자작나무에만 증상이 있는 환자에게 감작만 된 돼지풀의
        가을 시즌을 '지금 시즌'이라고 안내하던 오류가 여기서 나왔다.

        증상이 확인된 계절성 알러젠이 없으면 available=False — 환자가 계절성을 말했더라도 그렇다.
        알릴 시기가 없는데 절만 남으면 지역 메모와 '시즌 대비' 문장만 덩그러니 인쇄된다.
        """
        country, region = self.residence(screening)

        months: Dict[int, List[str]] = {}
        items = []
        excluded: List[str] = []
        for a in (assessments or []):
            cat = getattr(a, "category", None) or ""
            name = getattr(a, "korean_name", None) or getattr(a, "allergen_name", None)
            season = self.allergen_season(a, country, region)
            if not season:
                continue
            if not _is_clinically_relevant(a):
                if name not in excluded:
                    excluded.append(name)
                continue
            m = season["months"]
            items.append({"name": name, "category": cat, "months": m,
                          "season_label_ko": season["label_ko"], "season_level": season["level"]})
            for mm in m:
                months.setdefault(mm, []).append(name)

        reported_pattern = getattr(getattr(screening, "season_pattern", None), "value", None)
        reported_months = sorted(set(getattr(screening, "worse_months", None) or []))

        if not items:
            return {"available": False, "reason": "not_seasonal"}

        predicted = sorted(months)
        overlap = sorted(set(predicted) & set(reported_months))
        mismatch = bool(reported_months) and bool(predicted) and not overlap
        now = (today or date.today()).month
        return {
            "available": True,
            "items": items,
            "excluded": excluded,
            "months": {m: months[m] for m in predicted},
            "predicted_months": predicted,
            "reported_pattern": reported_pattern,
            "reported_months": reported_months,
            "overlap_months": overlap,
            "mismatch": mismatch,
            "current_month": now,
            "in_season_now": sorted(set(months.get(now, []))),
            "region_label_ko": (self.resolve_region(country, region) or {}).get("label_ko"),
            "notable_ko": self.regional_note(assessments, country, region),
            "category_estimate": any(i["season_level"] == "category" for i in items),
            "month_labels_ko": self.MONTH_KO,
        }


_pollen_service: Optional[PollenForecastService] = None


def get_pollen_forecast_service() -> PollenForecastService:
    global _pollen_service
    if _pollen_service is None:
        _pollen_service = PollenForecastService()
    return _pollen_service
