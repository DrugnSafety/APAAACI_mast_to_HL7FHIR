#!/usr/bin/env python3
"""항원 레지스트리(data/allergens.json)의 각 항원에 대해 실제 SNOMED CT SCTID 후보를 조회한다.

조회처: HL7 공개 터미널로지 서버 tx.fhir.org (SNOMED CT International Edition)
  - 검색: ValueSet/$expand?url=http://snomed.info/sct?fhir_vs=isa/<parent>&filter=<text>
  - 검증: CodeSystem/$lookup (FSN·inactive 확인)

출력: data/snomed_ct_candidates.json  (사람이 검토할 후보 + 자동 채택 신뢰도)
이 스크립트는 네트워크 조회만 하며 매핑 파일을 직접 수정하지 않는다.
"""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://tx.fhir.org/r4"
SCT = "http://snomed.info/sct"
# 검색 대상 계층: 물질 → 유기체(진드기·곰팡이·동물 등 substance 미존재 시)
PARENTS = [("substance", "105590001"), ("organism", "410607006")]
ROOT = Path(__file__).resolve().parent.parent

# 의미 없는 수식어만 제거한다. 'pollen'/'dander' 는 변별력이 있으므로 남긴다
# (없애면 'Birch pollen' 검색이 'Birch tar' 에 매칭된다).
STOP = {"protein", "mix", "allergen", "extract", "sp", "spp", "and", "of", "the"}
# 후보에 이 표현이 들어가면 알러젠 물질이 아니라 검사·항체 개념이므로 배제한다
BAD = ("immunoglobulin", "antibody", "antibodies", "vaccine", "proteinase", "peptidase",
       "measurement", "titer", "level", "allergy", "sensitivity", "poisoning", "sting")


def _get(url: str, tries: int = 3, timeout: int = 40):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            if i == tries - 1:
                return {"_error": str(e)}
            time.sleep(1.5 * (i + 1))
    return {"_error": "unreachable"}


def search(parent: str, text: str, count: int = 12):
    url = (f"{BASE}/ValueSet/$expand?url="
           + urllib.parse.quote(f"{SCT}?fhir_vs=isa/{parent}", safe="")
           + "&filter=" + urllib.parse.quote(text) + f"&count={count}")
    d = _get(url)
    if d.get("resourceType") != "ValueSet":
        return []
    return [(c["code"], c.get("display", "")) for c in d.get("expansion", {}).get("contains", [])]


def lookup(code: str):
    url = (f"{BASE}/CodeSystem/$lookup?system=" + urllib.parse.quote(SCT, safe="")
           + f"&code={code}&property=inactive")
    d = _get(url)
    if d.get("resourceType") != "Parameters":
        return {"error": d.get("_error") or "not found"}
    out = {"display": None, "fsn": None, "inactive": None}
    for p in d.get("parameter", []):
        if p.get("name") == "display":
            out["display"] = p.get("valueString")
        if p.get("name") == "designation":
            val = None
            is_fsn = False
            for part in p.get("part", []):
                if part.get("name") == "value":
                    val = part.get("valueString")
                if part.get("name") == "use" and (part.get("valueCoding") or {}).get("code") == "900000000000003001":
                    is_fsn = True
            if is_fsn and val:
                out["fsn"] = val
        if p.get("name") == "property":
            names = {x.get("name"): x for x in p.get("part", [])}
            if (names.get("code") or {}).get("valueCode") == "inactive":
                out["inactive"] = (names.get("value") or {}).get("valueBoolean")
    if not out["fsn"] and out["display"]:
        out["fsn"] = out["display"]
    return out


def norm(s: str) -> str:
    s = re.sub(r"\(.*?\)", " ", (s or "").lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(s.split())


def tokens(s: str):
    return {t for t in norm(s).split() if t not in STOP and len(t) > 1}


def score(query: str, display: str) -> float:
    q, d = tokens(query), tokens(display)
    if not q or not d:
        return 0.0
    inter = len(q & d)
    if not inter:
        return 0.0
    base = inter / max(len(q), 1)
    # 후보에 불필요한 단어가 많으면 감점(예: '... allergen mix')
    penalty = 0.04 * max(0, len(d) - len(q))
    if any(w in (display or "").lower() for w in BAD):
        return 0.0
    if norm(query) == norm(display):
        return 1.0
    return max(0.0, min(0.99, base - penalty))


def queries_for(a: dict):
    """검색어 우선순위: 학명(라틴) → canonical → CDM concept_name → 핵심어."""
    out = []
    canon = a.get("canonical_name") or ""
    cname = ((a.get("coding") or {}).get("concept_name") or "")
    latin = [x for x in (a.get("aliases") or [])
             if re.fullmatch(r"[A-Z][a-z]+ [a-z]+(?: [a-z]+)?", x or "")]
    for x in latin[:2]:
        out.append(x)
    if canon:
        out.append(canon)
    if cname and norm(cname) != norm(canon):
        out.append(re.sub(r"\(.*?\)", "", cname).strip())
    # 핵심어만 남긴 축약형 (예: 'Birch pollen' → 'Birch')
    short = " ".join(w for w in (canon or "").split() if w.lower() not in STOP)
    if short and norm(short) != norm(canon):
        out.append(short)
    seen, uniq = set(), []
    for q in out:
        k = norm(q)
        if k and k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq


def main():
    reg = json.loads((ROOT / "data" / "allergens.json").read_text())
    antigens = reg["antigens"]
    already = json.loads((ROOT / "data" / "snomed_ct_map.json").read_text())["map"]
    already_norm = {norm(k) for k in already}

    results = []
    for i, a in enumerate(antigens, 1):
        canon = a.get("canonical_name") or ""
        rec = {"id": a["id"], "canonical_name": canon, "korean_name": a.get("korean_name"),
               "category": a.get("category"), "existing": norm(canon) in already_norm,
               "queries": [], "candidates": [], "chosen": None, "confidence": "none"}
        if rec["existing"]:
            rec["confidence"] = "already-mapped"
            results.append(rec)
            print(f"[{i}/{len(antigens)}] {canon}: already mapped", flush=True)
            continue

        pool = {}
        for q in queries_for(a):
            rec["queries"].append(q)
            for pname, pid in PARENTS:
                for code, disp in search(pid, q):
                    sc = score(q, disp)
                    if sc <= 0:
                        continue
                    prev = pool.get(code)
                    if not prev or sc > prev["score"]:
                        pool[code] = {"code": code, "display": disp, "score": round(sc, 3),
                                      "hierarchy": pname, "query": q}
            if any(v["score"] >= 0.99 for v in pool.values()):
                break

        cands = sorted(pool.values(), key=lambda x: -x["score"])[:5]
        rec["candidates"] = cands
        if cands and cands[0]["score"] >= 0.55:
            top = cands[0]
            info = lookup(top["code"])
            top.update({"fsn": info.get("fsn"), "inactive": info.get("inactive")})
            if info.get("inactive") is False:
                rec["chosen"] = {"code": top["code"], "display": info.get("fsn") or top["display"],
                                 "hierarchy": top["hierarchy"], "matched_query": top["query"]}
                rec["confidence"] = ("high" if top["score"] >= 0.99 else
                                     "medium" if top["score"] >= 0.75 else "low")
        results.append(rec)
        best = rec["candidates"][0] if rec["candidates"] else None
        print(f"[{i}/{len(antigens)}] {canon}: {rec['confidence']}"
              + (f" -> {best['code']} {best['display']} ({best['score']})" if best else " (no candidate)"),
              flush=True)

    out = {
        "generated_by": "scripts/lookup_snomed_sctids.py",
        "source": "tx.fhir.org $expand/$lookup · SNOMED CT International",
        "note": "confidence: high=완전일치, medium/low=검토 필요, already-mapped=snomed_ct_map.json 에 존재",
        "results": results,
    }
    (ROOT / "data" / "snomed_ct_candidates.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    from collections import Counter
    print("SUMMARY", Counter(r["confidence"] for r in results), flush=True)


if __name__ == "__main__":
    sys.exit(main())
