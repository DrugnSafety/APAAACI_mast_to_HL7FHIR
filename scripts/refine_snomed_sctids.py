#!/usr/bin/env python3
"""1차 조회(lookup_snomed_sctids.py) 결과를 카테고리 규칙으로 정교화하고 최종 매핑을 만든다.

1차 조회의 한계:
  - 꽃가루 항원은 canonical_name 에 'pollen' 이 없어(ThermoFisher 명칭 표준화) 식물 개념에 매칭됨
    예) Mugwort → 'Mugwort'(식물) 이 'Mugwort pollen'(256293000) 보다 높은 점수를 받음
  - 동물 항원은 'dander/epithelium'(알러젠) 대신 동물 개념에 매칭될 수 있음
  - 성분 코드(Cor a 8 등)는 항원 전체가 아니라 단백질 성분이라 부적합
  - 혼합·대조 항원은 대응하는 단일 SNOMED 개념이 없음

출력: data/snomed_ct_map.json (기존 22종 + 신규), data/snomed_ct_unmapped.json
"""
import json
import re
from pathlib import Path

import lookup_snomed_sctids as L  # noqa: E402  (같은 디렉터리)

ROOT = Path(__file__).resolve().parent.parent

# 성분(component) 명명 규칙: 'Cor a 8', 'Bet v 1' — 항원 전체가 아니므로 제외
COMPONENT_RE = re.compile(r"^[A-Z][a-z]{2} [a-z] \d+", re.I)
# 대응 개념이 없어 매핑하지 않는 항원(혼합·대조·비알러젠)
SKIP_IDS = {"control", "tree_mixture_1", "tree_mixture_2",
            "indoor_mold_mixture", "outdoor_mold_mixture",
            "cornflour",              # 옥수수가루: Corn starch 와 다른 개념, 대응 코드 없음
            "2_spotted_spider_mite"}  # 점박이응애: SNOMED 물질/유기체 계층에 개념 없음

# 자동 매칭이 어려운 항원의 수동 확정값(검토 후 tx.fhir.org 로 재검증한다)
MANUAL = {
    # 꽃가루 — 식물이 아니라 'X pollen' 개념을 쓴다
    "birch": "256262001",             # European white birch pollen (자작나무)
    "hornbeam": "256302001",          # Hornbeam pollen (서어나무)
    "orchard_grass": "256278004",     # Dactylis (orchardgrass) pollen (오리새)
    "grass": "256277009",             # Grass pollen (잔디 꽃가루 혼합)
    "lamb_s_quarters": "260110004",   # Lamb's quarters pollen (명아주)
    "rye_grass_perennial": "1264573001",  # Perennial rye grass pollen (호밀풀)
    # 음식 — 요리·주스가 아니라 원재료 개념
    "maize_corn": "2911006",          # Zea mays - corn (옥수수, food)
    "pepper": "1137359008",           # Bell pepper (피망)
    "potato": "1590002",              # Irish potato (감자)
    "salmon": "39947003",             # Salmo salar (연어)
    "sheep": "226942002",             # Lamb (양고기)
    "grape": "256317002",             # Grapes (포도)
    "hazelnut": "256353000",          # Hazelnut (헤이즐넛) — 성분 Cor a 8 아님
    "walnut": "256352005",            # Walnut - nut (호두)
    "mung_bean": "227355001",         # Mung beans (녹두)
    # 동물 — 알러젠은 비듬·깃털
    "chicken": "260165000",           # Chicken feathers (닭, animal 카테고리)
    "guinea_pig": "703927007",        # Guinea pig dander (기니피그)
}


def prefer(cands, must_have=(), avoid=()):
    """후보 중 must_have 단어를 포함한 것을 우선, avoid 는 배제."""
    def ok(c):
        d = (c.get("display") or "").lower()
        if COMPONENT_RE.match(c.get("display") or ""):
            return False
        if any(a in d for a in avoid):
            return False
        return True

    pool = [c for c in cands if ok(c)]
    if must_have:
        hit = [c for c in pool if any(m in (c.get("display") or "").lower() for m in must_have)]
        if hit:
            return sorted(hit, key=lambda x: -x["score"])[0]
    return sorted(pool, key=lambda x: -x["score"])[0] if pool else None


def search_best(query, must_have=(), avoid=()):
    pool = {}
    for pid in ("105590001", "410607006"):
        for code, disp in L.search(pid, query):
            sc = L.score(query, disp)
            if sc <= 0:
                continue
            if code not in pool or sc > pool[code]["score"]:
                pool[code] = {"code": code, "display": disp, "score": round(sc, 3)}
    return prefer(list(pool.values()), must_have, avoid)


def main():
    cand = json.loads((ROOT / "data" / "snomed_ct_candidates.json").read_text())["results"]
    reg = {a["id"]: a for a in json.loads((ROOT / "data" / "allergens.json").read_text())["antigens"]}
    existing = json.loads((ROOT / "data" / "snomed_ct_map.json").read_text())

    final = dict(existing["map"])
    unmapped = []
    changed = 0

    for r in cand:
        aid, canon, cat = r["id"], r["canonical_name"], r["category"] or ""
        if r["confidence"] == "already-mapped" or aid in SKIP_IDS or cat == "control":
            if aid in SKIP_IDS or cat == "control":
                unmapped.append({"id": aid, "canonical_name": canon,
                                 "reason": "대응하는 단일 SNOMED 개념 없음(혼합·대조 항원)"})
            continue

        chosen = None
        if aid in MANUAL:
            code = MANUAL[aid]
            info = L.lookup(code)
            if info.get("inactive") is False:
                chosen = {"code": code, "display": info.get("fsn") or code, "source": "manual"}
        if not chosen:
            if cat.startswith("pollen"):
                base = re.sub(r"\s*pollen\s*$", "", canon, flags=re.I)
                best = search_best(f"{base} pollen", must_have=("pollen",))
                if best and "pollen" not in (best["display"] or "").lower():
                    best = None
            elif cat == "animal":
                best = search_best(canon, must_have=("dander", "epithelium", "hair"))
            else:
                best = prefer(r["candidates"], avoid=("mold",) if cat == "mold" else ())
            if best:
                info = L.lookup(best["code"])
                if info.get("inactive") is False:
                    chosen = {"code": best["code"],
                              "display": info.get("fsn") or best["display"],
                              "source": "auto"}

        if not chosen:
            unmapped.append({"id": aid, "canonical_name": canon, "reason": "적합한 활성 개념 미발견"})
            print(f"  UNMAPPED {canon}", flush=True)
            continue

        entry = {"code": chosen["code"], "display": chosen["display"]}
        final[canon] = entry
        # 별칭도 같은 코드로 (get_coding 은 canonical/alias 를 모두 조회한다)
        for alias in (reg.get(aid, {}).get("aliases") or []):
            final.setdefault(alias, entry)
        changed += 1
        print(f"  {canon:34s} -> {chosen['code']:>16s} {chosen['display']} [{chosen['source']}]", flush=True)

    existing["map"] = final
    existing["version"] = "2.0"
    existing["note_ko"] = (
        "실제 SNOMED CT SCTID. 22종은 사용자 검토본(2026-07), 나머지는 tx.fhir.org "
        "(SNOMED CT International) $expand/$lookup 으로 조회·활성 확인 후 카테고리 규칙으로 정교화(2026-09). "
        "꽃가루는 'X pollen', 동물은 'dander/epithelium' 개념을 우선한다. 혼합·대조 항원은 "
        "대응 개념이 없어 매핑하지 않는다(snomed_ct_unmapped.json)."
    )
    (ROOT / "data" / "snomed_ct_map.json").write_text(json.dumps(existing, ensure_ascii=False, indent=1))
    (ROOT / "data" / "snomed_ct_unmapped.json").write_text(
        json.dumps({"note_ko": "SNOMED CT 단일 개념이 없어 CDM(OMOP) 코딩으로 폴백하는 항원",
                    "items": unmapped}, ensure_ascii=False, indent=1))
    print(f"\nDONE: +{changed} mapped, {len(unmapped)} unmapped, total keys={len(final)}")


if __name__ == "__main__":
    main()
