#!/usr/bin/env python3
"""미국 ZIP3(우편번호 앞 3자리) → 주·대표좌표 표를 만든다.

왜 ZIP3 인가
  전체 우편번호는 3만 3천 개가 넘어 2MB 다. 꽃가루 시기·예보에는 그 정밀도가 필요 없다.
  앞 3자리(약 900개)면 주를 정확히 고르고, 대표좌표도 수십 km 오차로 충분하다.
  Google Pollen API 해상도가 1km 여도, 지역 달력과 권역 판단에는 ZIP3 이 적정하다.

원본: https://github.com/midwire/free_zipcode_data (all_us_zipcodes.csv)
사용법:
    curl -sO https://raw.githubusercontent.com/midwire/free_zipcode_data/master/all_us_zipcodes.csv
    python scripts/build_zip3_centroids.py all_us_zipcodes.csv
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "us_zip3_centroids.json"

# 50개 주 + DC. 준주(PR/VI/GU 등)는 꽃가루 권역 표에 없어 제외한다.
STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT "
             "NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "all_us_zipcodes.csv")
    if not src.exists():
        print(f"원본 CSV 가 없습니다: {src}")
        return 1

    groups = defaultdict(lambda: {"states": defaultdict(int), "lat": [], "lon": []})
    rows = 0
    with src.open(encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            code = (r.get("code") or "").strip()
            state = (r.get("state") or "").strip().upper()
            if len(code) != 5 or not code.isdigit() or state not in STATES:
                continue
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lat == 0 and lon == 0):
                continue
            g = groups[code[:3]]
            g["states"][state] += 1
            g["lat"].append(lat)
            g["lon"].append(lon)
            rows += 1

    table = {}
    for z3, g in sorted(groups.items()):
        # 한 ZIP3 가 주 경계에 걸치는 경우가 있어 최빈 주를 쓴다
        state = max(g["states"].items(), key=lambda kv: kv[1])[0]
        table[z3] = {"state": state,
                     "lat": round(statistics.median(g["lat"]), 4),
                     "lon": round(statistics.median(g["lon"]), 4)}

    OUT.write_text(json.dumps({
        "version": "1.0",
        "description": "미국 ZIP3 → 주·대표좌표. 꽃가루 권역 판단과 실시간 예보 좌표에 쓴다.",
        "source": "https://github.com/midwire/free_zipcode_data (all_us_zipcodes.csv)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note_ko": "앞 3자리 기준 중앙값 좌표입니다. 도시 단위 정밀도가 아니며 수십 km 오차가 있습니다.",
        "count": len(table),
        "zip3": table,
    }, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"ZIP {rows}개 → ZIP3 {len(table)}개 기록: {OUT.relative_to(BASE)} "
          f"({OUT.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
