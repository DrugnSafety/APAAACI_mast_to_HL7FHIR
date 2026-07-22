#!/usr/bin/env python3
"""
P0 — 단일 항원 레지스트리 빌드 (읽기전용 생성)

4개 사일로를 병합해 data/allergens.json 을 생성한다.
  - instructions/allergen_map_prompt_v2.json : 분류·별칭·SNOMED (기준, 121종)
  - data/cdm_snomed_mapping.json             : OMOP concept_id (FHIR 권위 코드)
  - data/allergen_knowledge_base.json        : 심층 backdata(kb_ref)
  - data/component_membership.json           : 성분(component family) 태깅

이 단계는 앱 소비자 코드를 바꾸지 않는다(P2+에서 위임). 생성물은 git diff 로 검수한다.
사용법:  python3 scripts/build_allergen_registry.py [--check]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP = ROOT / "instructions" / "allergen_map_prompt_v2.json"
CDM = ROOT / "data" / "cdm_snomed_mapping.json"
KB = ROOT / "data" / "allergen_knowledge_base.json"
MEMBERSHIP = ROOT / "data" / "component_membership.json"
COMPONENTS = ROOT / "data" / "allergen_components.json"
OUT = ROOT / "data" / "allergens.json"

# prompt_v2 의 Title-case category(+subcategory) → 통합 소문자 enum
CATEGORY_MAP = {
    ("mite", None): "mite",
    ("animal", None): "animal",
    ("mold", None): "mold",
    ("insect", None): "insect",
    ("food", None): "food",
    ("control", None): "control",
    ("mixture", None): "other",
}
POLLEN_SUB = {"tree": "pollen_tree", "grass": "pollen_grass", "weed": "pollen_weed",
              "tree pollen mix": "pollen_tree", "mold mix": "mold"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def unify_category(cat, sub):
    c = (cat or "").strip().lower()
    s = (sub or "").strip().lower()
    if c == "pollen":
        return POLLEN_SUB.get(s, "pollen_tree")
    return CATEGORY_MAP.get((c, None), c if c in
                            {"mite", "animal", "mold", "insect", "food", "control"} else "other")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return s or "unknown"


def main():
    check = "--check" in sys.argv
    mp = json.loads(MAP.read_text(encoding="utf-8"))["entries"]
    cdm = json.loads(CDM.read_text(encoding="utf-8"))
    kb = json.loads(KB.read_text(encoding="utf-8"))["entries"]
    mem = json.loads(MEMBERSHIP.read_text(encoding="utf-8"))["membership"]
    # 성분 카탈로그(WHO/IUIS ingest, P1)는 families 를 리스트로 보관 → id→family dict 로 인덱싱
    _fam_raw = json.loads(COMPONENTS.read_text(encoding="utf-8"))["families"]
    comp_cat = {f["id"]: f for f in _fam_raw} if isinstance(_fam_raw, list) else _fam_raw

    # CDM 역인덱스: 정규화 문자열 → concept
    cdm_lookup = cdm.get("lookup", {})
    cdm_entries = cdm.get("entries", [])

    def cdm_coding(*names):
        for n in names:
            k = norm(n)
            if k and k in cdm_lookup:
                e = cdm_entries[cdm_lookup[k]]
                return {"omop_concept_id": str(e["concept_id"]),
                        "concept_name": e.get("concept_name"),
                        "vocabulary": e.get("vocabulary", "SNOMED")}
        return None

    # KB 인덱스: 정규화 이름 → canonical
    kb_index = {}
    for e in kb:
        for nm in [e.get("canonical_name"), e.get("korean_name")] + (e.get("aliases") or []):
            if nm:
                kb_index.setdefault(norm(nm), e["canonical_name"])

    # 레지스트리 antigen 레코드 구축(중복 canonical 제거)
    registry = {}
    for e in mp:
        cn = e["canonical_name"]
        if cn in registry:
            continue
        aliases = e.get("aliases", []) or []
        korean = e.get("korean_name")
        coding = cdm_coding(cn, korean, *aliases)
        rec = {
            "id": slugify(cn),
            "canonical_name": cn,
            "korean_name": korean,
            "aliases": aliases,
            "ocr_aliases": e.get("ocr_aliases", []) or [],
            "category": unify_category(e.get("category"), e.get("subcategory")),
            "subcategory": e.get("subcategory"),
            "coding": {
                "omop_concept_id": (coding or {}).get("omop_concept_id"),
                "snomed": e.get("snomed"),
                "vocabulary": (coding or {}).get("vocabulary", "SNOMED"),
                "coding_source": "cdm" if coding else ("snomed" if e.get("snomed") else "none"),
                "concept_name": (coding or {}).get("concept_name"),
            },
            "kb_ref": None,
            "components": [],
        }
        # kb_ref 연결
        for nm in [cn, korean] + aliases:
            if norm(nm) in kb_index:
                rec["kb_ref"] = kb_index[norm(nm)]
                break
        registry[cn] = rec

    # 성분(component) 태깅 — membership 키를 registry 항원의 canonical/alias/korean 에 매칭
    name_to_canon = {}
    for cn, rec in registry.items():
        for nm in [cn, rec.get("korean_name")] + rec.get("aliases", []):
            if nm:
                name_to_canon.setdefault(norm(nm), cn)
    unmatched, tagged = [], 0
    unknown_family = set()
    for key, fams in mem.items():
        canon = name_to_canon.get(norm(key))
        if not canon:
            unmatched.append(key)
            continue
        for f in fams:
            if f not in comp_cat:
                unknown_family.add(f)
            if f not in registry[canon]["components"]:
                registry[canon]["components"].append(f)
        tagged += 1

    # category 보정: 'other' 인데 음식/식물성 성분이 태깅된 항원은 food 로 교정
    # (Apple·Celery·Lobster 등이 map_v2 에서 'Other' 로 잘못 분류돼 음식 문진에서 누락되던 문제)
    FOOD_COMPONENTS = {"pr10", "nsltp", "storage_2s_albumin", "thaumatin_pr5",
                       "parvalbumin", "profilin", "tropomyosin", "arginine_kinase"}
    corrected = []
    for rec in registry.values():
        if rec["category"] == "other" and set(rec["components"]) & FOOD_COMPONENTS:
            rec["category"] = "food"
            corrected.append(rec["canonical_name"])

    antigens = sorted(registry.values(), key=lambda r: r["canonical_name"])
    out = {
        "version": "1.0",
        "generated_by": "scripts/build_allergen_registry.py (P0 read-only merge)",
        "note_ko": "4개 사일로 병합 단일 항원 레지스트리. 아직 앱에 연결되지 않음(P2+에서 위임). 성분 태깅은 data/component_membership.json 에서 관리.",
        "count": len(antigens),
        "antigens": antigens,
    }

    # 리포트
    with_code = sum(1 for a in antigens if a["coding"]["omop_concept_id"] or a["coding"]["snomed"])
    with_kb = sum(1 for a in antigens if a["kb_ref"])
    with_comp = sum(1 for a in antigens if a["components"])
    from collections import Counter
    cat_dist = Counter(a["category"] for a in antigens)
    print(f"항원: {len(antigens)}  | 코드보유: {with_code}  | kb_ref: {with_kb}  | 성분태깅: {with_comp}")
    print(f"category 분포: {dict(cat_dist)}")
    print(f"membership 태깅 성공: {tagged}  | 미매칭 키({len(unmatched)}): {unmatched}")
    print(f"category 보정(other→food, 성분 근거) {len(corrected)}종: {corrected}")
    print("  ↑ 미매칭 키 중 검사 항원명은 base map(allergen_map_prompt_v2)에 없는 것 — 레지스트리 확장 후보")
    if unknown_family:
        print(f"⚠️ 카탈로그에 없는 family 참조: {sorted(unknown_family)}")

    if check:
        if OUT.exists():
            cur = json.loads(OUT.read_text(encoding="utf-8"))
            same = cur.get("antigens") == antigens
            print("check:", "동일(변경없음)" if same else "차이 있음")
            sys.exit(0 if same else 1)
        print("check: 아직 생성 안 됨")
        sys.exit(1)

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ 생성: {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
