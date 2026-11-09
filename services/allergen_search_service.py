"""Allergen Search — 항원 레지스트리(data/allergens.json) 자동완성.

영문명·한글명·별칭(OCR 별칭, 중국어 표기 포함)에서 앞부분 일치와 중간 일치를 찾고,
앞부분 일치를 먼저 보여 준다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

REGISTRY_PATH = BASE_DIR / "data" / "allergens.json"
ZH_NAMES_PATH = BASE_DIR / "data" / "allergen_names_zh.json"
_SEP = re.compile(r"[\s\-_.,()/·]+")


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _squash(s: str) -> str:
    """공백·구두점을 뺀 비교용 문자열('D. farinae' 와 'd farinae' 를 같게 본다)."""
    return _SEP.sub("", s)


class AllergenSearchService:
    def __init__(self):
        self.version: Optional[str] = None
        self.items: List[Dict[str, Any]] = []
        # 항목별 검색 필드: (필드 종류, 원문, 정규화, 압축)
        self._fields: List[List[Tuple[str, str, str, str]]] = []
        self._known: set = set()
        self._load()

    def _load(self):
        try:
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"항원 레지스트리를 읽지 못했습니다: {e}")
            return
        zh: Dict[str, List[str]] = {}
        try:
            for v in json.loads(ZH_NAMES_PATH.read_text(encoding="utf-8")).get("map", {}).values():
                if v.get("id"):
                    zh[v["id"]] = list(v.get("zh") or [])
        except Exception:  # noqa: BLE001
            pass
        self.version = data.get("version")
        for e in data.get("antigens", []):
            name = e.get("canonical_name") or ""
            if not name:
                continue
            aliases = [a for a in (e.get("aliases") or []) if a]
            zh_names = zh.get(e.get("id"), [])
            self.items.append({"id": e.get("id"), "canonical_name": name,
                               "korean_name": e.get("korean_name") or "", "category": e.get("category"),
                               "aliases": aliases, "zh_names": zh_names})
            fields = [("canonical_name", name), ("korean_name", e.get("korean_name") or "")]
            fields += [("alias", a) for a in aliases + list(e.get("ocr_aliases") or []) + zh_names]
            self._fields.append([(kind, raw, _norm(raw), _squash(_norm(raw))) for kind, raw in fields if raw])
            self._known.update(squashed for _kind, _raw, _norm_, squashed in self._fields[-1])

    def all(self) -> List[Dict[str, Any]]:
        return self.items

    def is_known_name(self, name: Optional[str]) -> bool:
        """레지스트리에 있는 이름·별칭과 (대소문자·공백·구두점만 빼고) 똑같은가.
        사용자가 지어낸 문자열인지 가리는 데 쓴다."""
        squashed = _squash(_norm(name))
        return bool(squashed) and squashed in self._known

    def search(self, q: str, limit: int = 10) -> List[Dict[str, Any]]:
        """순위: 이름 완전 일치 → 이름 앞부분 → 이름 속 단어 앞부분 → 별칭 앞부분 → 이름 중간 → 별칭 중간."""
        nq = _norm(q)
        sq = _squash(nq)
        if not sq:
            return []
        scored = []
        for item, fields in zip(self.items, self._fields):
            best: Optional[Tuple[int, str, str]] = None
            for kind, raw, norm, squashed in fields:
                is_name = kind != "alias"
                if norm == nq or squashed == sq:
                    rank = 0 if is_name else 3
                elif norm.startswith(nq) or squashed.startswith(sq):
                    rank = 1 if is_name else 3
                elif any(w.startswith(nq) for w in _SEP.split(norm)):
                    rank = 2 if is_name else 4
                elif nq in norm:
                    rank = 5 if is_name else 6
                else:
                    continue
                if best is None or rank < best[0]:
                    best = (rank, kind, raw)
            if best:
                scored.append((best[0], len(item["canonical_name"]), item["canonical_name"].lower(),
                               {**item, "match": {"field": best[1], "text": best[2],
                                                  "type": "prefix" if best[0] <= 4 else "substring"}}))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored[:max(1, min(int(limit), 50))]]


_svc: Optional[AllergenSearchService] = None


def get_allergen_search_service() -> AllergenSearchService:
    global _svc
    if _svc is None:
        _svc = AllergenSearchService()
    return _svc
