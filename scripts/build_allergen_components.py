#!/usr/bin/env python3
"""
WHO/IUIS Allergen Nomenclature(allergen.org) export → component-resolved 알레르겐 DB 빌드

입력 (data/source/):
  - allergentable.csv : 항원 분자(component) 마스터 1,160행
      TaxSource/TaxOrder/Species/Common/AllergenID/Name/BioNames/MolecularMass/Allergenicity/Exposure
  - isotable.csv      : isoallergen(서열 수준) — IsoAllergenicity(교차반응 서술) 보유
  - idmapping.csv / jointtable.csv : 참고

규칙 (data/component_family_rules.json):
  BioNames(자유텍스트 단백질명) → 표준 component family. 순서대로 첫 일치 채택.
  * 새 family/표기 변형은 이 JSON에 항목만 추가하면 된다 (코드 수정 불필요).

출력 (data/allergen_components.json):
  {
    "families":   [{id, name_en, name_ko, heat_stable, risk, note_ko,
                    member_component_ids[], member_species[]}]
    "components": [{id, iuis_name(예 'Der p 10'), allergen_id, species, common,
                    family_id, biona me, molecular_mass, route, category, allergenicity}]
    "species":    [{id, species, common, tax_source, tax_order, routes[], category,
                    component_ids[], family_ids[]}]
    "species_index": { 정규화문자열 → species_id }   # 추출물 검사 항원 → 후보 component 브리지
  }

핵심 개념(CRD, Component-Resolved Diagnostics):
  추출물 검사('고양이 양성')는 어떤 component에 감작됐는지 모른다. 따라서 species → 그 종이
  배출하는 '후보 component 집합'으로 다루고, 교차반응은 component 의 family 공유로 파생한다.
      cross_reactants(X) = ∪_{c ∈ X.components} { Y ∈ family(c).members : Y.species ≠ X.species }

사용:
  python3 scripts/build_allergen_components.py
  (프로젝트 밖 CSV를 쓰려면 --source 로 디렉터리 지정)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── WHO/IUIS Exposure 코드 (allergentable.Exposure) ────────────────────────────
# 실제 데이터로 검증한 의미 (분포/샘플 기준)
ROUTE_BY_CODE = {
    "0": "autoallergen",   # human autoallergen (5)
    "1": "ingestion",      # 섭취 — 땅콩·키위·호두·새우(Decapoda)·소·닭 (408)
    "2": "inhalation",     # 흡입 — 집먼지진드기·곰팡이·바퀴·꽃가루 (601)
    "3": "contact",        # 접촉 — 라텍스·피부사상균 (37)
    "4": "sting",          # 주입/자상 — 벌·말벌·모기·개미 (109)
}

# ── 카테고리 추론 (route + 분류학) ────────────────────────────────────────────
# TaxOrder 만으로는 모호하다(Fagales = 자작나무 꽃가루 + 헤이즐넛 식품).
# 따라서 route(흡입 vs 섭취)로 먼저 가른 뒤 분류학으로 세분한다.
POLLEN_TREE_ORDERS = {
    "Fagales", "Lamiales", "Proteales", "Cupressales", "Pinales", "Sapindales",
    "Myrtales", "Malpighiales", "Arecales", "Saxifragales", "Rosales",
}
POLLEN_WEED_ORDERS = {"Asterales", "Caryophyllales", "Urticales", "Rosales", "Ranunculales", "Brassicales"}
POLLEN_GRASS_ORDERS = {"Poales"}
MITE_ORDERS = {"Sarcoptiformes", "Trombidiformes", "Astigmata"}
INSECT_INHALANT_ORDERS = {"Blattodea", "Diptera", "Lepidoptera", "Orthoptera", "Ephemeroptera", "Trichoptera"}
ANIMAL_ORDERS = {
    "Carnivora", "Cetartiodactyla", "Perissodactyla", "Rodentia", "Lagomorpha",
    "Primates", "Artiodactyla", "Galliformes",
}


def infer_category(tax_source: str, tax_order: str, route: str) -> str:
    """WHO/IUIS 분류학 + 노출경로 → 플랫폼 표준 카테고리.
    이름 매칭(‘Dog hair’ 같은 문자열)에 의존하지 않으므로 미분류가 근본적으로 줄어든다."""
    ts, to = (tax_source or "").strip(), (tax_order or "").strip()
    is_plant = ts.startswith("Plantae")
    is_fungi = ts.startswith("Fungi")
    is_animal = ts.startswith("Animalia")

    if route == "sting":
        return "venom"
    if route == "contact":
        return "contact"       # latex, 피부사상균 등
    if route == "ingestion":
        return "food"          # 섭취 경로면 식품 (동물성/식물성 무관)
    if route == "autoallergen":
        return "other"

    # route == inhalation (또는 미상) → 흡입 알러젠 세분
    if is_fungi:
        return "mold"
    if is_animal:
        if to in MITE_ORDERS:
            return "mite"
        if to in INSECT_INHALANT_ORDERS:
            return "insect"
        if to in ANIMAL_ORDERS:
            return "animal"
        if to in {"Ascaridida", "Rhabditida"}:
            return "other"     # 기생충
        return "animal"
    if is_plant:
        if to in POLLEN_GRASS_ORDERS:
            return "pollen_grass"
        if to in POLLEN_WEED_ORDERS:
            return "pollen_weed"
        if to in POLLEN_TREE_ORDERS:
            return "pollen_tree"
        return "pollen_tree"
    return "other"


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")
    return s or "unknown"


def norm_key(s: str) -> str:
    """조회용 정규화 키 (영문/숫자/한글만)."""
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def load_rules(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data["rules"]
    for r in rules:
        r["_re"] = re.compile(r["pattern"], re.I)
    return rules


def match_family(bioname: str, rules: list[dict]) -> str | None:
    """BioNames → family id. 순서대로 첫 일치(specific → generic)."""
    if not bioname:
        return None
    for r in rules:
        if r["_re"].search(bioname):
            return r["id"]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(ROOT / "data" / "source"))
    ap.add_argument("--out", default=str(ROOT / "data" / "allergen_components.json"))
    ap.add_argument("--rules", default=str(ROOT / "data" / "component_family_rules.json"))
    args = ap.parse_args()

    src = Path(args.source)
    rules = load_rules(Path(args.rules))
    rule_by_id = {r["id"]: r for r in rules}

    with open(src / "allergentable.csv", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    # isotable 의 IsoAllergenicity(교차반응 서술)를 AllergenID 로 집계 — 근거 텍스트로 보관
    iso_notes: dict[str, list[str]] = defaultdict(list)
    iso_path = src / "isotable.csv"
    if iso_path.exists():
        with open(iso_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                txt = (r.get("Allergenicity") or "").strip()
                aid = (r.get("AllergenID") or "").strip()
                if txt and aid and txt not in iso_notes[aid]:
                    iso_notes[aid].append(txt)

    components: list[dict] = []
    species_map: dict[str, dict] = {}
    unmatched_bionames: dict[str, int] = defaultdict(int)

    for r in rows:
        iuis = (r["Name"] or "").strip()             # 예: 'Der p 10', 'Fel d 1'
        if not iuis:
            continue
        species = (r["Species"] or "").strip()
        common = (r["Common"] or "").strip()
        bioname = (r["BioNames"] or "").strip()
        route = ROUTE_BY_CODE.get((r["Exposure"] or "").strip(), "unknown")
        tax_source, tax_order = (r["TaxSource"] or "").strip(), (r["TaxOrder"] or "").strip()
        category = infer_category(tax_source, tax_order, route)
        fam = match_family(bioname, rules)
        if bioname and not fam:
            unmatched_bionames[bioname] += 1

        cid = slug(iuis)
        aid = (r["AllergenID"] or "").strip()
        components.append({
            "id": cid,
            "iuis_name": iuis,
            "allergen_id": aid,
            "species": species,
            "common": common,
            "family_id": fam,
            "bioname": bioname,
            "molecular_mass": (r["MolecularMass"] or "").strip(),
            "route": route,
            "category": category,
            "allergenicity": (r["Allergenicity"] or "").strip()[:600],
            "iso_allergenicity": iso_notes.get(aid, [])[:3],
        })

        sid = slug(species)
        sp = species_map.setdefault(sid, {
            "id": sid, "species": species, "common": common,
            "tax_source": tax_source, "tax_order": tax_order,
            "routes": [], "categories": [], "component_ids": [], "family_ids": [],
        })
        if route not in sp["routes"]:
            sp["routes"].append(route)
        if category not in sp["categories"]:
            sp["categories"].append(category)
        sp["component_ids"].append(cid)
        if fam and fam not in sp["family_ids"]:
            sp["family_ids"].append(fam)

    # 종의 대표 카테고리: 흡입/섭취 둘 다면(밀 등) 우선순위로 하나 고르되 categories 는 보존
    CAT_PRIORITY = ["mite", "animal", "insect", "mold", "pollen_tree", "pollen_grass",
                    "pollen_weed", "venom", "contact", "food", "other"]
    for sp in species_map.values():
        sp["category"] = next((c for c in CAT_PRIORITY if c in sp["categories"]), "other")

    # families: 멤버 집계
    families = []
    comp_by_id_local = {c["id"]: c for c in components}
    comp_by_fam: dict[str, list[dict]] = defaultdict(list)
    for c in components:
        if c["family_id"]:
            comp_by_fam[c["family_id"]].append(c)
    for r in rules:
        members = comp_by_fam.get(r["id"], [])
        if not members:
            continue
        member_species = sorted({m["common"] or m["species"] for m in members if (m["common"] or m["species"])})
        families.append({
            "id": r["id"], "name_en": r["name_en"], "name_ko": r["name_ko"],
            "heat_stable": r.get("heat_stable"), "risk": r.get("risk"),
            "note_ko": r.get("note_ko", ""),
            "member_component_ids": [m["id"] for m in members],
            "member_species": member_species,
            "member_count": len(members),
            "species_count": len(member_species),
        })

    # species_index: 추출물 검사 항원명 → species_id (common / species / 각 토큰)
    species_index: dict[str, str] = {}
    for sp in species_map.values():
        for key in (sp["common"], sp["species"]):
            k = norm_key(key)
            if k:
                species_index.setdefault(k, sp["id"])
        # 'Common' 이 쉼표로 여러 이름을 담는 경우 분리 (예: 'Mold mite, cheese mite')
        for part in re.split(r"[,/;]", sp["common"] or ""):
            k = norm_key(part)
            if k:
                species_index.setdefault(k, sp["id"])

    # ── 교차반응 엣지 (가중·필터) ────────────────────────────────────────────
    # deep-research 보정:
    #   · family 공유는 '확정'이 아니라 '가중 후보'(family 내 교차반응은 patchy)
    #   · ask_routes 로 노출경로를 제한 → 새우→모기/흰개미, 고양이→모기 같은 임상 무의미 엣지 제거
    #   · panallergen(profilin/polcalcin/cyclophilin) 은 clinical_priority=low → 자동 문진 생성 제외
    #   · 분류학적 근접도로 가중 (같은 目=강, 같은 界/門=중, 그 외=약)
    PRIO_W = {"high": 1.0, "medium": 0.6, "low": 0.25}

    def taxon_weight(a: dict, b: dict) -> float:
        if a["tax_order"] and a["tax_order"] == b["tax_order"]:
            return 1.0          # 예: 새우↔게 (Decapoda)
        if a["tax_source"] and a["tax_source"] == b["tax_source"]:
            return 0.6          # 예: 새우↔진드기 (Animalia Arthropoda)
        return 0.3              # 계통이 먼 공유 (예: 진드기↔틸라피아)

    fam_by_id_local = {f["id"]: f for f in families}
    rule_meta = {r["id"]: r for r in rules}
    cross_index: dict[str, list[dict]] = {}

    for sp in species_map.values():
        cands: dict[str, dict] = {}
        for fid in sp["family_ids"]:
            meta = rule_meta.get(fid, {})
            prio = meta.get("clinical_priority", "low")
            ask_routes = meta.get("ask_routes", []) or []
            if prio == "low" or not ask_routes:
                continue                       # 자동 문진 대상 아님 (panallergen 등)
            for cid in fam_by_id_local[fid]["member_component_ids"]:
                c = comp_by_id_local[cid]
                if c["species"] == sp["species"]:
                    continue
                other = species_map.get(slug(c["species"]))
                if not other:
                    continue
                # 노출경로 필터: 이 family 로 물어볼 수 있는 경로의 종만
                if not (set(other["routes"]) & set(ask_routes)):
                    continue
                w = PRIO_W.get(prio, 0.25) * taxon_weight(sp, other)
                e = cands.setdefault(other["id"], {
                    "species_id": other["id"],
                    "common": other["common"] or other["species"],
                    "category": other["category"],
                    "via_families": [], "risk": None, "heat_stable": None, "weight": 0.0,
                })
                if fid not in e["via_families"]:
                    e["via_families"].append(fid)
                if w > e["weight"]:
                    e["weight"] = round(w, 3)
                    e["risk"] = meta.get("risk")
                    e["heat_stable"] = meta.get("heat_stable")
        ranked = sorted(cands.values(), key=lambda x: -x["weight"])[:12]   # 상한 12
        if ranked:
            cross_index[sp["id"]] = ranked

    out = {
        "source": "WHO/IUIS Allergen Nomenclature (allergen.org) — allergentable/isotable export",
        "source_url": "https://www.allergen.org/downloads.php",
        "attribution": "Allergen names and protein family (BioNames) data © WHO/IUIS Allergen Nomenclature Sub-Committee.",
        "generated_by": "scripts/build_allergen_components.py",
        "concept": "Component-Resolved Diagnostics: 추출물 검사 항원(species) → 후보 component 집합 → "
                   "component family 공유로 교차반응 파생.",
        "caveats_ko": [
            "family 공유는 교차반응 '확정'이 아니라 '가중 후보'다 (family 내 교차반응은 patchy).",
            "IgE 교차반응 ≠ 임상 교차반응. 실제 증상이 있었던 항원만 임상적으로 유의하다.",
            "panallergen(profilin/polcalcin/cyclophilin) 인식은 대체로 상호배타적 → 자동 캐스케이드하지 않는다.",
            "CCD/alpha-Gal 은 단백질 family 가 아닌 glycan epitope — 본 그래프에 미포함(별도 모델링 필요).",
            "서열동일성 >70% = 교차반응 규칙은 근거 부족으로 채택하지 않음.",
        ],
        "counts": {
            "components": len(components), "species": len(species_map),
            "families": len(families), "index_keys": len(species_index),
            "species_with_cross": len(cross_index),
        },
        "families": sorted(families, key=lambda f: -f["species_count"]),
        "components": components,
        "species": sorted(species_map.values(), key=lambda s: s["id"]),
        "species_index": species_index,
        "cross_reactivity": cross_index,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"✓ wrote {args.out}")
    print(f"  components={len(components)}  species={len(species_map)}  families={len(families)}")
    fam_hit = sum(1 for c in components if c["family_id"])
    print(f"  family 매칭: {fam_hit}/{len(components)} ({fam_hit*100//max(1,len(components))}%)")
    if unmatched_bionames:
        top = sorted(unmatched_bionames.items(), key=lambda x: -x[1])[:12]
        print(f"  ⚠ family 미매칭 BioNames {len(unmatched_bionames)}종 (상위):")
        for name, n in top:
            print(f"      {n:3d}  {name}")
        print("     → data/component_family_rules.json 에 규칙을 추가하면 편입됩니다(코드 수정 불필요).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
