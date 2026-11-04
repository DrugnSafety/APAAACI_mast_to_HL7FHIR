"""ImmunoCAP 카탈로그 대조 산출물(data/immunocap_*.json)의 정합성 — 레지스트리 코드가 카탈로그에 실재하고,
SNOMED 후보가 활성 SCTID 형식이며, 성분·혼합은 검색 대상에서 빠져 있는지 확인한다."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CAT = json.loads((ROOT / "data" / "immunocap_catalog_2023.json").read_text(encoding="utf-8"))
RMAP = json.loads((ROOT / "data" / "immunocap_registry_map.json").read_text(encoding="utf-8"))
CAND = json.loads((ROOT / "data" / "immunocap_snomed_candidates.json").read_text(encoding="utf-8"))
REG = json.loads((ROOT / "data" / "allergens.json").read_text(encoding="utf-8"))["antigens"]
CODES = {x["code"]: x for x in CAT["items"]}


def test_catalog_codes_unique_and_typed():
    assert len(CODES) == CAT["count"] == len(CAT["items"])
    assert all(re.fullmatch(r"[a-z]{1,2}x?\d{1,3}[a-z]?", c) for c in CODES)
    assert {x["kind"] for x in CAT["items"]} == {"whole", "component", "mix"}


def test_registry_map_codes_exist_in_catalog_and_registry():
    reg_ids = {a["id"] for a in REG}
    assert {i["id"] for i in RMAP["items"]} == reg_ids
    for it in RMAP["items"]:
        for c in it["immunocap_codes"]:
            assert c in CODES, (it["id"], c)
    # 레지스트리(allergens.json)에 immunocap_codes 가 그대로 전파됐는지
    by_id = {i["id"]: i for i in RMAP["items"]}
    for a in REG:
        assert (a.get("immunocap_codes") or []) == by_id[a["id"]]["immunocap_codes"], a["id"]


def test_registry_map_category_gate():
    gate = {"pollen_grass": "g", "pollen_weed": "w", "pollen_tree": "t", "mold": "m", "animal": "e",
            "mite": "d", "insect": "i", "venom": "i", "drug": "c", "food": "f", "latex": "k"}
    for it in RMAP["items"]:
        allowed = gate.get(it["category"])
        # 수동 확정은 게이트를 넘을 수 있다(예: anisakis 는 레지스트리상 food 지만 카탈로그는 기생충 p4)
        if allowed and it["how"] == "auto":
            for c in it["immunocap_codes"]:
                assert c[0] == allowed, (it["id"], c)


def test_candidates_cover_unmapped_whole_only():
    mapped = {c for it in RMAP["items"] for c in it["immunocap_codes"]}
    expected = {x["code"] for x in CAT["items"] if x["kind"] == "whole" and x["code"] not in mapped}
    assert {r["code"] for r in CAND["items"]} == expected
    assert len(CAND["components"]) == sum(1 for x in CAT["items"] if x["kind"] == "component")
    assert len(CAND["mixes"]) == sum(1 for x in CAT["items"] if x["kind"] == "mix")


def test_candidate_chosen_codes_are_active_sctids():
    for r in CAND["items"]:
        assert r["confidence"] in {"high", "medium", "low", "manual", "none", "skipped"}
        if r["chosen"]:
            assert re.fullmatch(r"\d{6,18}", r["chosen"]["code"]), r["code"]
            assert r["chosen"]["inactive"] is not True, r["code"]
            assert "immunoglobulin" not in r["chosen"]["display"].lower(), r["code"]
        else:
            assert r["confidence"] in {"none", "skipped"}
