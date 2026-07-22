#!/usr/bin/env python3
"""
P0 검증 데모 — 성분(component) 공유 기반 교차반응 후보 파생.

새 데이터(data/allergens.json + data/allergen_components.json)만으로
"양성 항원 → 같은 성분 family 를 공유하는 교차반응 '후보' 항원"을 파생한다.
(아직 앱에 연결 안 됨. 데이터 모델이 의도대로 동작하는지 확인하는 용도.)

핵심 원칙(docs/crd_cross_reactivity_research.md): 성분 공유는 '문진 후보'를 만들 뿐,
임상 교차반응은 증상으로 확정한다. 위험도(risk)는 family 의 열안정성 기반 '경향'.

사용법:  python3 scripts/crossreact_demo.py [항원명 ...]
"""
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
REG = json.loads((ROOT / "data" / "allergens.json").read_text(encoding="utf-8"))
COMP = json.loads((ROOT / "data" / "allergen_components.json").read_text(encoding="utf-8"))["families"]
ANTIGENS = REG["antigens"]


def norm(s):
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


# 이름 → 항원레코드
BY_NAME = {}
for a in ANTIGENS:
    for nm in [a["canonical_name"], a.get("korean_name")] + a.get("aliases", []):
        if nm:
            BY_NAME.setdefault(norm(nm), a)

# 성분 → [항원] 역인덱스 (cross_reactive family 만)
COMP_TO_ANTIGENS = defaultdict(list)
for a in ANTIGENS:
    for c in a.get("components", []):
        if COMP.get(c, {}).get("cross_reactive"):
            COMP_TO_ANTIGENS[c].append(a)


def cross_reactants(query):
    a = BY_NAME.get(norm(query))
    if not a:
        return None, []
    groups = []
    for c in a.get("components", []):
        fam = COMP.get(c, {})
        if not fam.get("cross_reactive"):
            continue
        others = [x for x in COMP_TO_ANTIGENS[c] if x["canonical_name"] != a["canonical_name"]]
        if others:
            groups.append({
                "component": c, "family": fam.get("family"),
                "risk": fam.get("clinical_risk"), "heat_stable": fam.get("heat_stable"),
                "note_ko": fam.get("note_ko"),
                "candidates": [x.get("korean_name") or x["canonical_name"] for x in others],
            })
    return a, groups


def main():
    queries = sys.argv[1:] or ["Shrimp", "Birch", "Celery", "Cat dander", "Peach", "Cod"]
    for q in queries:
        a, groups = cross_reactants(q)
        print("=" * 72)
        if not a:
            print(f"[{q}] 레지스트리에 없음 (확장 후보)")
            continue
        print(f"[{a['canonical_name']} / {a.get('korean_name')}]  category={a['category']}  components={a['components']}")
        if not groups:
            print("  교차반응 후보 없음(성분 미태깅 또는 marker-only)")
        for g in groups:
            hs = "열안정(전신주의)" if g["heat_stable"] else ("열불안정" if g["heat_stable"] is False else "안정성미상")
            print(f"  ▸ {g['family']} [{g['risk']}, {hs}]")
            print(f"     교차반응 문진 후보: {', '.join(g['candidates'])}")


if __name__ == "__main__":
    main()
