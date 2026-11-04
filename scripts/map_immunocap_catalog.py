#!/usr/bin/env python3
"""ImmunoCAP(ThermoFisher Phadia) 카탈로그 ↔ 항원 레지스트리 대조 + 누락 항원 SNOMED CT 1차 매핑.

입력
  data/immunocap_catalog_2023.json   공식 PDF 카탈로그(2023-02, EN) 파싱본 — 569 항목
  data/allergens.json                항원 레지스트리(159종)
출력
  data/immunocap_registry_map.json   레지스트리 항원 → ImmunoCAP 코드(카테고리 게이트 + 수동 확정)
  data/immunocap_snomed_candidates.json
                                     레지스트리에 없는 카탈로그 단일 항원(whole)의 SNOMED CT 후보
                                     (tx.fhir.org 검색·$lookup 활성 검증, confidence 등급)
주의
  - 분자 성분(component, 예 rBet v 1)은 SNOMED CT 에 단일 개념이 거의 없어 검색하지 않고 목록만 남긴다.
  - 혼합(mix)은 구성 코드만 기록한다(단일 SNOMED 없음).
  - 이 스크립트는 레지스트리를 수정하지 않는다. 결과는 전문의 검토 후 반영한다.
사용법  python3 scripts/map_immunocap_catalog.py [--no-snomed] [--limit N] [--refresh-manual]
"""
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lookup_snomed_sctids as L  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "immunocap_catalog_2023.json"
REGISTRY = ROOT / "data" / "allergens.json"
OUT_MAP = ROOT / "data" / "immunocap_registry_map.json"
OUT_CAND = ROOT / "data" / "immunocap_snomed_candidates.json"

# 레지스트리 category → 허용되는 ImmunoCAP 코드 접두어(카테고리 게이트)
GATE = {
    "pollen_grass": "g", "pollen_weed": "w", "pollen_tree": "t", "mold": "m", "animal": "e",
    "mite": "d", "insect": "i", "venom": "i", "drug": "c", "food": "f", "latex": "k",
    "other": "hdoi", "control": "",
}
# 자동 매칭이 불가능하거나 틀리는 항원의 수동 확정(카탈로그 코드). None = 대응 코드 없음.
MANUAL = {
    "alder": ["t2"], "beech": ["t5"], "cod": ["f3"], "hazelnut": ["f17"], "hornbeam": ["t209"],
    "plane": ["t11"], "poplar": ["t14"], "sesame": ["f10"], "mussel": ["f37"],
    "penicillium": ["m1"], "fusarium": ["m9"], "neurospora": [],
    "kentucky_blue_grass": ["g8"], "orchard_grass": ["g3"], "grass": ["gx1"],
    "bean": ["f15"], "mung_bean": [], "prawn": ["f24"], "trout": [], "carp": [],
    "krill": [], "casein": ["f78"], "lactalbumin": ["f76"], "chrysanthemum": [],
    "citrus_fruit": ["f33"], "cockroach_american": ["i206"], "cockroach_german": ["i6"],
    "house_dust": ["h1", "h2"], "indoor_mold_mixture": ["mx1"], "outdoor_mold_mixture": ["mx2"],
    "tree_mixture_1": ["tx9"], "tree_mixture_2": ["tx5"], "2_spotted_spider_mite": [],
    "control": [], "histamine": [],
    "anisakis": ["p4"], "cow_milk": ["f2"], "pecan": ["f201"], "sheep": ["f88"],
    # 모호 해소
    "cornflour": ["f8"], "maize_corn": ["f8"], "barley": ["f6"], "acacia": ["t19"], "walnut": ["f256"],
    "chicken": ["e85"], "mouse": ["e71"], "rabbit_epithelium": ["e82"], "rat_epithelium": ["e73"],
    "dermatophagoides_farinae": ["d2"], "dermatophagoides_pteronyssinus": ["d1"], "cockroach_mix": ["i6", "i206"],
    "sardine": ["f61"], "wheat": ["f4"], "wholemeal_flour": ["f79"],
}


# 자동 검색이 못 찾거나 잘못 고른 항목의 수동 확정 SCTID(모두 실행 시 $lookup 으로 활성 여부를 재검증한다).
# 값: (SCTID, 근거 메모)
MANUAL_SNOMED = {
    "t206": ("710725003", "Castanea sativa → European chestnut pollen (Horse chestnut 아님)"),
    "t18": ("260131001", "Gum pollen — SNOMED 의 유칼립투스(gum tree) 꽃가루 개념. 검토 필요"),
    "t23": ("260136006", "Italian cypress pollen"),
    "t41": ("1284941009", "Carya tomentosa = Mockernut hickory pollen"),
    "t44": ("472743003", "Celtis pollen (속 수준)"),
    "t210": ("472739002", "Ligustrum pollen (속 수준)"),
    "t217": ("472734007", "Schinus pollen (속 수준)"),
    "w206": ("710902006", "Chamomile pollen"),
    "m8": ("76951009", "Setomelanomma rostrata = Exserohilum rostratum (유기체)"),
    "m10": ("711255003", "Stemphylium botryosum protein antigen"),
    "m14": ("415907005", "Epicoccum purpurascens = E. nigrum (유기체)"),
    "m202": ("243484001", "Acremonium kiliense = Sarocladium kiliense (유기체)"),
    "m227": ("18097004", "Malassezia (속)"),
    "e200": ("1268879004", "Common canary dropping"),
    "i205": ("288460008", "Bumble bee venom"),
    "k76": ("255772008", "Diphenylmethane di-isocyanate (MDI)"),
    "k77": ("256123000", "1,6-hexamethylene di-isocyanate (HDI)"),
    "k80": ("111095003", "Formaldehyde"),
    "o1": ("89889006", "Cotton fiber (Cotton seed 아님)"),
    "o215": ("788778006", "Galactose-alpha-1,3 galactose"),
    "f58": ("1263989003", "Todarodes pacificus = Japanese flying squid"),
    "f93": ("901000161107", "Cocoa (Cacao tree 아님)"),
    "f204": ("54955003", "Oncorhynchus mykiss = Salmo gairdneri (무지개송어) — 카탈로그 행 줄바꿈으로 학명이 잘림"),
    "f271": ("227376006", "Anise seed (oil 아님)"),
    "f299": ("260188004", "Chestnut (식품)"),
    "f302": ("256316006", "Tangerine = Mandarin"),
    "f317": ("227391008", "Coriander seed (oil 아님)"),
    "f342": ("227436000", "Olives (식품)"),
    "f415": ("22670002", "Stizostedion vitreum vitreum = Walleye"),
}


def apply_manual(results):
    n = 0
    for r in results:
        if r["code"] in MANUAL_SNOMED:
            sct, note = MANUAL_SNOMED[r["code"]]
            info = L.lookup(sct)
            if info.get("fsn") and info.get("inactive") is not True:
                r["chosen"] = {"code": sct, "display": info["fsn"], "inactive": info.get("inactive")}
                r["confidence"] = "manual"
                r["manual_note_ko"] = note
                n += 1
            else:
                r["manual_note_ko"] = f"수동 지정 {sct} 가 $lookup 에서 확인되지 않음: {info}"
    return n


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(spp|sp|pollen|dander|epithelium|whole|flour|meat|protein|proteins)\b", " ", s)
    return " ".join(s.split())


def name_variants(x):
    out = set()
    for n in (x.get("name") or "", x.get("latin") or ""):
        for part in re.split(r",|/", n):
            k = norm(part)
            if len(k) > 2:
                out.add(k)
        for m in re.findall(r"\((.*?)\)", n):
            k = norm(m)
            if len(k) > 2:
                out.add(k)
        k = norm(n)
        if len(k) > 2:
            out.add(k)
    return out


def match_registry(cat, reg):
    """카테고리 게이트 + 정규화 동일 → 수동 확정으로 덮어쓴다."""
    idx = {}
    for a in reg:
        for n in [a["canonical_name"]] + (a.get("aliases") or []):
            k = norm(n)
            if k:
                idx.setdefault(k, set()).add(a["id"])
    by_code = {x["code"]: x for x in cat}
    reg_by_id = {a["id"]: a for a in reg}
    result = {a["id"]: set() for a in reg}
    for x in cat:
        if x["kind"] != "whole":
            continue
        hits = set()
        for k in name_variants(x):
            hits |= idx.get(k, set())
        for h in hits:
            cat_ok = GATE.get(reg_by_id[h]["category"], "")
            if x["code"][0] in cat_ok:
                result[h].add(x["code"])
    for rid, codes in MANUAL.items():
        if rid in result:
            result[rid] = set(codes)
    items = []
    for a in reg:
        codes = sorted(result[a["id"]], key=lambda c: (c[0], int(re.sub(r"\D", "", c) or 0)))
        items.append({
            "id": a["id"], "canonical_name": a["canonical_name"], "korean_name": a.get("korean_name"),
            "category": a["category"],
            "immunocap_codes": codes,
            "immunocap_names": [by_code[c]["name"] or by_code[c].get("members") for c in codes if c in by_code],
            "how": "manual" if a["id"] in MANUAL else ("auto" if codes else "none"),
            "existing_immunocap_code": a.get("immunocap_code"),
        })
    return items


def queries_for_catalog(x):
    g, name, latin = x["group"], x["name"] or "", x["latin"] or ""
    parts = [p.strip() for p in re.split(r",", name) if p.strip()]
    inner = re.findall(r"\((.*?)\)", name)
    base = re.sub(r"\(.*?\)", "", parts[0]).strip() if parts else name
    lat = re.sub(r"\(.*?\)", "", latin.split(",")[0]).replace(" spp.", "").strip()
    q = []
    if g in ("pollen_grass", "pollen_weed", "pollen_tree"):
        q += [f"{base} pollen"] + [f"{p} pollen" for p in parts[1:]] + ([f"{lat} pollen"] if lat else [])
        q += ([lat] if lat else []) + [base]
    elif g == "animal":
        q += [name] + ([lat] if lat else [])
    elif g in ("mold", "mite", "parasite"):
        q += ([lat] if lat else []) + [base]
    elif g == "insect_venom":
        q += [name] + ([f"{base} venom"] if "venom" not in name.lower() else []) + ([lat] if lat else [])
    else:
        # 식품의 'Bos spp.' 처럼 속명만 남는 학명은 유기체 개념(Bos taurus)으로 새므로 쓰지 않는다
        q += [base] + inner + parts[1:] + ([lat] if lat and len(lat.split()) >= 2 else [])
    seen = set()
    return [s for s in q if s and not (s.lower() in seen or seen.add(s.lower()))]


# 음식 후보에서 조리·가공 개념(커리·통조림·건조·오일)과 관찰값(IgE)은 항원 물질이 아니다
BAD_EXTRA = ("curry", "dishes", "dish", "dried", "tinned", "canned", "juice", "soup", "cooked", "roast",
             "boiled", "fried", "sauce", "stuffed", "ige", "virus", "tar", "pudding", "pie", "jam", "wine", "bhaji",
             "and", "with")


SYN = {"cow": "cattle", "cows": "cattle", "hen": "chicken", "hens": "chicken", "swine": "pig", "pork": "pig"}


def stem(tok):
    tok = SYN.get(tok, tok)
    if tok.endswith("ies") and len(tok) > 4:
        return tok[:-3] + "y"
    if tok.endswith("es") and len(tok) > 4 and tok[-3] in "sxz":
        return tok[:-2]
    if tok.endswith("s") and not tok.endswith("ss") and len(tok) > 3:
        return tok[:-1]
    return tok


def stokens(s):
    return {stem(t) for t in L.tokens(s)}


def score2(query, display):
    """lookup_snomed_sctids.score 와 같되 단·복수를 무시하고 조리·관찰값 단어를 배제한다."""
    if any(w in L.tokens(display) for w in BAD_EXTRA):
        return 0.0
    q, d = stokens(query), stokens(display)
    if not q or not d or not (q & d):
        return 0.0
    if any(w in (display or "").lower() for w in L.BAD):
        return 0.0
    if q == d:
        return 1.0
    # 여분 단어 감점을 원본(0.04)보다 세게 둔다 — 'Aubergine and potato bhaji' 류가 0.9 를 받지 않도록
    return max(0.0, min(0.99, len(q & d) / len(q) - 0.12 * max(0, len(d) - len(q))))


def key_tokens(x):
    """카탈로그 항목의 핵심 토큰: 영문명 각 부분의 비-불용어 + 학명 속명."""
    out = set()
    # 쉼표 뒤의 별칭('Eucalyptus, Gum-tree' 의 Gum-tree)은 검색어로만 쓰고 핵심 토큰에는 넣지 않는다
    part = re.split(r",|/", x["name"] or "")[0]
    out |= L.tokens(re.sub(r"\(.*?\)", " ", part)) | L.tokens(" ".join(re.findall(r"\((.*?)\)", part)))
    lat = (x["latin"] or "").split(",")[0].strip()
    if lat:
        out.add(L.norm(lat).split()[0] if L.norm(lat) else "")
    return {t for t in out if t and t not in {"pollen", "dander", "epithelium", "venom", "seed", "nut", "fish", "milk", "flour"}}


def alias_tokens(x):
    out = set()
    for part in re.split(r",|/", x["name"] or "")[1:]:
        out |= L.tokens(part)
    return out


def snomed_candidates(x):
    pool = {}
    for qtext in queries_for_catalog(x)[:5]:
        for parent_name, parent in L.PARENTS:
            for code, disp in L.search(parent, qtext, 10):
                sc = score2(qtext, disp)
                # 이름의 핵심 토큰(첫 단어·속명)이 후보 표시명에 없으면 사실상 다른 개념이다
                # (예: Eucalyptus → 'Sweet gum tree pollen' 에 medium 이 매겨지는 것을 막는다)
                if not ({stem(t) for t in key_tokens(x)} & stokens(disp)):
                    # 쉼표 별칭('Aubergine, eggplant' 의 eggplant)으로만 맞으면 low 로 내려 검토 대상으로 남긴다
                    sc *= 0.6 if ({stem(t) for t in alias_tokens(x)} & stokens(disp)) else 0.3
                if x["group"].startswith("pollen") and "pollen" not in disp.lower():
                    sc *= 0.5
                if x["group"] == "animal" and not re.search(r"dander|epithel|feather|urine|serum|saliva|hair|protein|dropping", disp.lower()):
                    sc *= 0.6
                if x["group"] == "insect_venom" and "venom" in x["name"].lower() and "venom" not in disp.lower():
                    sc *= 0.5
                prev = pool.get(code)
                if not prev or sc > prev["score"]:
                    pool[code] = {"code": code, "display": disp, "score": round(sc, 3), "query": qtext, "hierarchy": parent_name}
        if any(v["score"] >= 0.99 for v in pool.values()):
            break
    cands = sorted(pool.values(), key=lambda v: -v["score"])[:5]
    chosen, conf = None, "none"
    if cands and cands[0]["score"] >= 0.55:
        top = cands[0]
        info = L.lookup(top["code"])
        if info.get("inactive") is False or info.get("fsn"):
            chosen = {"code": top["code"], "display": info.get("fsn") or top["display"], "inactive": info.get("inactive")}
            conf = "high" if top["score"] >= 0.99 else "medium" if top["score"] >= 0.75 else "low"
    return cands, chosen, conf


def main():
    no_snomed = "--no-snomed" in sys.argv
    limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    cat = json.load(open(CATALOG, encoding="utf-8"))["items"]
    reg = json.load(open(REGISTRY, encoding="utf-8"))["antigens"]
    items = match_registry(cat, reg)
    mapped_codes = {c for it in items for c in it["immunocap_codes"]}
    summary = Counter(it["how"] for it in items)
    OUT_MAP.write_text(json.dumps({
        "version": "1.0", "generated_at": time.strftime("%Y-%m-%d"), "catalog": CATALOG.name,
        "note_ko": "레지스트리 항원 → ImmunoCAP 코드. how=auto 는 정규화 이름 일치+카테고리 게이트, manual 은 스크립트 MANUAL 표. none 은 카탈로그에 대응 단일 항원이 없음(혼합·대조·국내 전용).",
        "count": len(items), "summary": dict(summary), "items": items,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("registry map:", dict(summary), flush=True)

    whole = [x for x in cat if x["kind"] == "whole" and x["code"] not in mapped_codes]
    comps = [x for x in cat if x["kind"] == "component"]
    mixes = [x for x in cat if x["kind"] == "mix"]
    if limit:
        whole = whole[:limit]
    if "--refresh-manual" in sys.argv and OUT_CAND.exists():
        # 검색을 다시 돌리지 않고 기존 결과에 수동 확정만 재적용한다
        prev = json.loads(OUT_CAND.read_text(encoding="utf-8"))
        results = prev["items"]
        print("manual applied:", apply_manual(results), flush=True)
        prev["summary"]["confidence"] = dict(Counter(r["confidence"] for r in results))
        prev["generated_at"] = time.strftime("%Y-%m-%d")
        OUT_CAND.write_text(json.dumps(prev, ensure_ascii=False, indent=1), encoding="utf-8")
        print("SUMMARY", prev["summary"]["confidence"], flush=True)
        return
    results = []
    for i, x in enumerate(whole, 1):
        rec = dict(x)
        if no_snomed:
            rec.update({"candidates": [], "chosen": None, "confidence": "skipped"})
        else:
            cands, chosen, conf = snomed_candidates(x)
            rec.update({"candidates": cands, "chosen": chosen, "confidence": conf})
            print(f"[{i}/{len(whole)}] {x['code']} {x['name']}: {conf}"
                  + (f" -> {chosen['code']} {chosen['display']}" if chosen else ""), flush=True)
        results.append(rec)
    if not no_snomed:
        print("manual applied:", apply_manual(results), flush=True)
    OUT_CAND.write_text(json.dumps({
        "version": "1.0", "generated_at": time.strftime("%Y-%m-%d"),
        "source": "tx.fhir.org SNOMED CT International (ValueSet/$expand isa/105590001 substance, isa/410607006 organism; CodeSystem/$lookup)",
        "note_ko": "레지스트리에 없는 ImmunoCAP 단일 항원의 SNOMED CT 1차 후보. confidence high=정규화 완전일치, medium/low=검토 필요, manual=스크립트 MANUAL_SNOMED 수동 확정($lookup 재검증), none=후보 없음(CDM/OMOP 폴백 또는 상위 개념 검토). 분자 성분·혼합은 검색하지 않았다.",
        "summary": {"whole_unmapped": len(results), "confidence": dict(Counter(r["confidence"] for r in results)),
                    "components_not_searched": len(comps), "mixes_not_searched": len(mixes)},
        "items": results,
        "components": [{k: x[k] for k in ("code", "name", "latin", "group")} for x in comps],
        "mixes": [{k: x.get(k) for k in ("code", "members", "latin", "group")} for x in mixes],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SUMMARY", Counter(r["confidence"] for r in results), flush=True)


if __name__ == "__main__":
    main()
