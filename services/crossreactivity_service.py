"""
Crossreactivity Service — 성분(component) 기반 교차반응 파생 (데이터 주도)

data/allergens.json(항원 레지스트리) + data/allergen_components.json(단백질 family 카탈로그)
+ data/component_membership.json(항원→family) 을 로드해, 양성 항원명으로부터
"같은 성분 family 를 공유하는 교차반응 후보 항원"을 파생한다.

핵심 원칙(docs/crd_cross_reactivity_research.md):
  성분 공유는 '문진 후보'를 생성할 뿐이며, 실제 임상 교차반응은 증상으로 확정한다
  (서열 상동성으로 자동 판정 불가). 위험도(risk)·열안정성은 문항 문구·경고 게이팅에만 쓴다.

하드코딩(is_shellfish, mite_shellfish 특수블록 등)을 대체하는 범용 엔진.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict

logger = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parent.parent / "data"

# 위험도 정렬용 순위(전신 위험 family 를 먼저 노출)
_RISK_RANK = {"systemic": 3, "raw_systemic": 3, "oral_to_moderate": 2,
              "variable": 1, "oral": 1, "usually_low": 0, "marker": -1, None: 0}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


class CrossreactivityService:
    def __init__(self):
        self._load()

    def _load(self):
        self.antigens: List[Dict[str, Any]] = []
        self.families: Dict[str, Dict[str, Any]] = {}
        self.by_name: Dict[str, Dict[str, Any]] = {}
        self.comp_to_antigens: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        try:
            reg = json.loads((_DATA / "allergens.json").read_text(encoding="utf-8"))
            self.antigens = reg.get("antigens", [])
            fam_raw = json.loads((_DATA / "allergen_components.json").read_text(encoding="utf-8"))["families"]
            self.families = {f["id"]: f for f in fam_raw} if isinstance(fam_raw, list) else fam_raw
            for a in self.antigens:
                for nm in [a.get("canonical_name"), a.get("korean_name")] + a.get("aliases", []):
                    if nm:
                        self.by_name.setdefault(_norm(nm), a)
                for c in a.get("components", []):
                    self.comp_to_antigens[c].append(a)
            logger.info(f"교차반응 서비스 로드: 항원 {len(self.antigens)}, family {len(self.families)}")
        except Exception as e:
            logger.warning(f"교차반응 데이터 로드 실패(교차반응 문진 비활성): {e}")

    def find(self, name: str, korean: str = "") -> Optional[Dict[str, Any]]:
        for cand in (name, korean):
            a = self.by_name.get(_norm(cand))
            if a:
                return a
        return None

    def _fam_risk_rank(self, fam_id: str) -> int:
        return _RISK_RANK.get((self.families.get(fam_id) or {}).get("risk"), 0)

    def cross_reactants(
        self, name: str, korean: str = "",
        exclude_names: Optional[Set[str]] = None,
        per_family: int = 5, total_limit: int = 12,
    ) -> List[Dict[str, Any]]:
        """양성 항원의 교차반응 후보를 공유 family 별로 묶어 반환.
        반환: [{family_id, family_ko, family_en, risk, heat_stable, note_ko,
                candidates:[{name, korean}]}] (위험도 높은 family 우선).
        per_family: family 당 후보 상한(한 family 가 전체를 잠식하지 않도록).
        total_limit: 전체 후보 상한.
        exclude_names: 이미 양성으로 패널에 있는 항원명 — 제외(직접 문진되므로).
        """
        a = self.find(name, korean)
        if not a:
            return []
        exclude = {_norm(x) for x in (exclude_names or set())}
        exclude.add(_norm(a.get("canonical_name")))
        exclude.add(_norm(a.get("korean_name")))

        fam_ids = sorted(a.get("components", []), key=self._fam_risk_rank, reverse=True)
        groups: List[Dict[str, Any]] = []
        seen_cand: Set[str] = set()
        total = 0
        for fid in fam_ids:
            fam = self.families.get(fid) or {}
            cands = []
            for x in self.comp_to_antigens.get(fid, []):
                if total + len(cands) >= total_limit or len(cands) >= per_family:
                    break
                key = _norm(x.get("canonical_name"))
                if key in exclude or key in seen_cand:
                    continue
                seen_cand.add(key)
                cands.append({"name": x.get("canonical_name"), "korean": x.get("korean_name")})
            if not cands:
                continue
            total += len(cands)
            groups.append({
                "family_id": fid,
                "family_ko": fam.get("name_ko") or fid,
                "family_en": fam.get("name_en") or fid,
                "risk": fam.get("risk"),
                "heat_stable": fam.get("heat_stable"),
                "note_ko": fam.get("note_ko"),
                "candidates": cands,
            })
        return groups

    def _category_of(self, name: str) -> Optional[str]:
        a = self.by_name.get(_norm(name))
        return a.get("category") if a else None

    def candidate_foods(
        self, name: str, korean: str = "",
        exclude_names: Optional[Set[str]] = None, limit: int = 10,
        categories: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """환자 문진용 평면 후보 목록(중복 제거, 위험도 우선). 각 후보에 대표 family·위험 메타 부착.
        categories: 후보를 이 카테고리로 제한(기본 {'food'}) — '먹었을 때 교차반응' 문진은 음식만.
        반환: [{name, korean, family_id, risk, heat_stable, category}]"""
        cats = categories if categories is not None else {"food"}
        groups = self.cross_reactants(name, korean, exclude_names, per_family=8, total_limit=limit * 3)
        # family 를 라운드로빈으로 섞어 각 family 가 고르게 대표되게 함(한 family 가 목록을 잠식하지 않도록)
        flat: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        queues = [[dict(c, _g=g) for c in g["candidates"]] for g in groups]
        i = 0
        while any(queues) and len(flat) < limit:
            q = queues[i % len(queues)] if queues else []
            if q:
                c = q.pop(0)
                g = c["_g"]
                key = _norm(c["name"])
                cat = self._category_of(c["name"])
                if key not in seen and (not cats or cat in cats):
                    seen.add(key)
                    flat.append({"name": c["name"], "korean": c["korean"], "category": cat,
                                 "family_id": g["family_id"], "risk": g["risk"],
                                 "heat_stable": g["heat_stable"]})
            i += 1
            if all(not q for q in queues):
                break
        return flat[:limit]

    def worst_risk(self, name: str, korean: str = "") -> Optional[str]:
        """항원의 성분 중 가장 높은 위험 tier(systemic > oral 등) — 문항 경고 게이팅용."""
        a = self.find(name, korean)
        if not a:
            return None
        best, rank = None, -99
        for fid in a.get("components", []):
            r = self._fam_risk_rank(fid)
            if r > rank:
                rank, best = r, (self.families.get(fid) or {}).get("risk")
        return best

    def has_data(self) -> bool:
        return bool(self.antigens and self.families)


_svc: Optional[CrossreactivityService] = None


def get_crossreactivity_service() -> CrossreactivityService:
    global _svc
    if _svc is None:
        _svc = CrossreactivityService()
    return _svc
