"""
Category Resolver — 카테고리 결정 파이프라인 (데이터 기반, P4)

이름 매칭 하드코딩(특별 dict·산발적 if)의 취약성을 제거하고, 우선순위 파이프라인으로
항원 카테고리를 강건하게 결정한다. 규칙은 data/category_rules.json 에서 관리(add-to-list).

우선순위:
  1) 명시된 raw category 가 유효 enum(비-other) → 사용
  2) allergen_mapper(base map)의 category → 사용
  3) alias_map 정확일치(정규화 이름/한글명)
  4) regex_patterns 순차 매칭(이름/한글명) — 접미사(dander·hair·비듬·상피 등)로 미분류 흡수
  5) 그래도 미분류 → 'other' + 로그(미분류 신규 항원 = 다음 add-to-list 후보)
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "category_rules.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


class CategoryResolver:
    def __init__(self):
        self.valid = set()
        self.alias_map = {}
        self.patterns = []  # [(compiled_regex, category)]
        self._unclassified_logged = set()
        self._load()

    def _load(self):
        try:
            data = json.loads(_RULES_PATH.read_text(encoding="utf-8"))
            self.valid = set(data.get("valid_categories", []))
            self.alias_map = {_norm(k): v for k, v in (data.get("alias_map") or {}).items()}
            for r in data.get("regex_patterns", []):
                try:
                    self.patterns.append((re.compile(r["pattern"], re.I), r["category"]))
                except re.error as e:
                    logger.warning(f"category_rules regex 오류 무시: {r.get('pattern')} ({e})")
            logger.info(f"카테고리 규칙 로드: alias {len(self.alias_map)}, regex {len(self.patterns)}")
        except Exception as e:
            logger.warning(f"category_rules 로드 실패(기본 동작): {e}")

    # base map 의 Pollen+subcategory(Tree/Grass/Weed) → 세분 enum
    _POLLEN_SUB = {"tree": "pollen_tree", "grass": "pollen_grass", "weed": "pollen_weed",
                   "tree pollen mix": "pollen_tree"}

    def _valid_or_none(self, cat: Optional[str], sub: Optional[str] = None) -> Optional[str]:
        from services.knowledge_service import normalize_category
        c = normalize_category(cat) if cat else None
        # Pollen 은 subcategory 로 tree/grass/weed 세분(없으면 tree 기본은 부정확 → sub 우선)
        if c and c.startswith("pollen") and sub:
            refined = self._POLLEN_SUB.get(sub.strip().lower())
            if refined:
                c = refined
        return c if (c and c != "other" and c in self.valid) else None

    def resolve(self, name: str, korean: str = "", raw_category: Optional[str] = None) -> str:
        # 1) 명시된 raw category
        c = self._valid_or_none(raw_category)
        if c:
            return c
        # 2) base map(allergen_mapper) category
        try:
            from utils.allergen_mapper import get_allergen_mapper
            mp = get_allergen_mapper().find_allergen(name) or (
                get_allergen_mapper().find_allergen(korean) if korean else None)
            if mp:
                c = self._valid_or_none(mp.category, getattr(mp, "subcategory", None))
                if c:
                    return c
        except Exception:
            pass
        # 3) alias_map 정확일치
        for key in (_norm(name), _norm(korean)):
            if key and key in self.alias_map:
                cc = self.alias_map[key]
                if cc in self.valid:
                    return cc
        # 4) regex_patterns 순차 매칭 (원문 이름/한글명 대상)
        hay = f"{name or ''} {korean or ''}"
        for rx, cat in self.patterns:
            if rx.search(hay):
                return cat
        # 5) 미분류 → other + 로그(중복 억제)
        k = _norm(name) or _norm(korean)
        if k and k not in self._unclassified_logged:
            self._unclassified_logged.add(k)
            logger.info(f"[미분류 항원] category=other: '{name}'({korean}) — "
                        f"data/category_rules.json 또는 base map 에 추가 권장")
        return "other"


_resolver: Optional[CategoryResolver] = None


def get_category_resolver() -> CategoryResolver:
    global _resolver
    if _resolver is None:
        _resolver = CategoryResolver()
    return _resolver


def resolve_category(name: str, korean: str = "", raw_category: Optional[str] = None) -> str:
    return get_category_resolver().resolve(name, korean, raw_category)
