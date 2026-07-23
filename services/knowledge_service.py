"""
Knowledge Service
알레르겐 backdata(특성·생활사·노출환경·계절성·회피수칙·감별질문) 지식베이스 관리 서비스.

우선순위:
1) data/allergen_knowledge_base.json 에 정리된 curated 지식베이스
2) 매칭 실패 시 Wikipedia(한국어→영어) 외부 검색으로 최소 backdata 보강
3) 그래도 없으면 카테고리 기반 기본값
"""

import json
import logging
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

KB_PATH = BASE_DIR / "data" / "allergen_knowledge_base.json"
PFAS_PATH = BASE_DIR / "data" / "pollen_food_cross_reactivity.json"

# 알레르겐 면역치료(SCIT/SLIT) 적용 가능성 (카테고리 기준)
IMMUNOTHERAPY_BY_CATEGORY: Dict[str, Dict[str, Any]] = {
    "mite": {"eligible": True,
             "ko": "집먼지진드기는 알레르겐 면역치료(설하/피하)의 근거가 가장 확실한 대표 대상입니다. "
                   "3~5년 꾸준히 받으면 증상·약물 필요를 줄이고 천식 진행을 예방할 수 있습니다."},
    "pollen_tree": {"eligible": True,
                    "ko": "꽃가루 알레르기는 면역치료(설하/피하)로 증상을 줄일 수 있는 대표 대상입니다. "
                          "특히 약물로 조절이 어렵거나 증상이 심할 때 고려합니다."},
    "pollen_grass": {"eligible": True,
                     "ko": "잔디(화본과) 꽃가루는 설하면역치료(정제) 근거가 잘 확립된 대상입니다."},
    "pollen_weed": {"eligible": True,
                    "ko": "잡초 꽃가루도 면역치료 대상이 될 수 있어, 증상이 심하면 전문의와 상의하세요."},
    "animal": {"eligible": True,
               "ko": "고양이 등 동물 알레르기도 면역치료가 가능하나, 접촉 회피가 우선입니다. "
                     "직업상 회피가 어렵거나 증상이 심할 때 고려합니다."},
    "mold": {"eligible": True,
             "ko": "알테르나리아 등 일부 곰팡이는 면역치료가 가능하지만 표준화·근거가 제한적입니다."},
    "insect": {"eligible": False,
               "ko": "바퀴 등 실내 곤충 알레르기의 면역치료는 근거가 제한적이라, 환경 관리가 우선입니다."},
    "food": {"eligible": False,
             "ko": "음식 알레르기는 일반적 면역치료(SCIT/SLIT) 대상이 아니며, 회피가 기본입니다. "
                   "일부(우유·계란·땅콩)는 전문기관의 경구면역치료(OIT) 대상이 될 수 있습니다."},
}

# 카테고리 정규화: 다양한 표기를 표준 키로
CATEGORY_ALIASES = {
    "mite": "mite",
    "house_dust_mite": "mite",
    "pollen": "pollen_tree",           # 세분류 없을 때 기본 (season_label로 보정)
    "pollen_tree": "pollen_tree",
    "tree": "pollen_tree",
    "pollen_grass": "pollen_grass",
    "grass": "pollen_grass",
    "pollen_weed": "pollen_weed",
    "weed": "pollen_weed",
    "mold": "mold",
    "fungus": "mold",
    "animal": "animal",
    "insect": "insect",
    "cockroach": "insect",
    "food": "food",
    "other": "other",
    "control": "other",
    "mixture": "other",
}

# 카테고리 기본 backdata (지식베이스/외부검색 모두 실패 시 최소 정보 제공)
CATEGORY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "mite": {
        "seasonality_pattern": "perennial", "indoor_outdoor": "indoor",
        "season_label_ko": "연중",
        "relevance_probes_ko": [
            "특정 계절과 무관하게 연중 코·눈 증상이 지속되나요?",
            "청소·이불정리·먼지 노출 시 증상이 심해지나요?",
            "아침 기상 직후 증상이 특히 심한가요?",
        ],
    },
    "animal": {
        "seasonality_pattern": "perennial", "indoor_outdoor": "indoor",
        "season_label_ko": "연중",
        "relevance_probes_ko": [
            "해당 동물과 접촉하면 수분 내로 코·눈·피부·호흡기 증상이 생기나요?",
            "그 동물이 있는 공간에 가면 증상이 악화되나요?",
        ],
    },
    "pollen_tree": {
        "seasonality_pattern": "seasonal", "indoor_outdoor": "outdoor",
        "season_label_ko": "봄 (3~5월)", "peak_months_korea": [3, 4, 5],
        "relevance_probes_ko": [
            "봄철(3~5월)에 코·눈 증상이 뚜렷하게 심해지나요?",
            "봄철 야외활동 시 증상이 악화되나요?",
            "다른 계절에는 증상이 줄어드나요?",
        ],
    },
    "pollen_grass": {
        "seasonality_pattern": "seasonal", "indoor_outdoor": "outdoor",
        "season_label_ko": "늦봄~초여름 (5~6월)", "peak_months_korea": [5, 6],
        "relevance_probes_ko": [
            "늦봄~초여름(5~6월)에 증상이 심해지나요?",
            "잔디밭·풀밭 노출 시 증상이 악화되나요?",
        ],
    },
    "pollen_weed": {
        "seasonality_pattern": "seasonal", "indoor_outdoor": "outdoor",
        "season_label_ko": "늦여름~가을 (8~10월)", "peak_months_korea": [8, 9, 10],
        "relevance_probes_ko": [
            "늦여름~가을(8~10월)에 증상이 뚜렷하게 심해지나요?",
            "가을철 야외(하천변·풀밭) 노출 시 증상이 악화되나요?",
        ],
    },
    "mold": {
        "seasonality_pattern": "seasonal", "indoor_outdoor": "both",
        "season_label_ko": "여름~가을·습한 시기", "peak_months_korea": [7, 8, 9],
        "relevance_probes_ko": [
            "습한 날·장마철·곰팡이 있는 공간에서 증상이 심해지나요?",
            "여름~가을에 증상이 악화되나요?",
        ],
    },
    "insect": {
        "seasonality_pattern": "perennial", "indoor_outdoor": "indoor",
        "season_label_ko": "연중",
        "relevance_probes_ko": [
            "해당 곤충(바퀴 등)이 있는 환경에서 만성 비염·천식 증상이 있나요?",
        ],
    },
    "food": {
        "seasonality_pattern": "perennial", "indoor_outdoor": "indoor",
        "season_label_ko": "연중 (섭취 시)",
        "relevance_probes_ko": [
            "해당 음식을 먹으면 반복적으로 증상이 생기나요?",
            "증상은 섭취 후 얼마 만에 나타나나요(수분~2시간)?",
            "현재 그 음식을 문제없이 섭취하나요?",
        ],
    },
    "other": {
        "seasonality_pattern": "perennial", "indoor_outdoor": "both",
        "season_label_ko": "연중",
        "relevance_probes_ko": [
            "이 물질에 노출될 때 알레르기 증상이 생기거나 심해지나요?",
        ],
    },
}


def normalize_category(raw: Optional[str]) -> str:
    if not raw:
        return "other"
    key = str(raw).strip().lower().replace(" ", "_")
    return CATEGORY_ALIASES.get(key, key if key in CATEGORY_DEFAULTS else "other")


def _norm(text: str) -> str:
    """비교용 정규화: 소문자, 특수문자 제거, 공백 축소"""
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w가-힣\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class KnowledgeService:
    """알레르겐 지식베이스 조회 + 외부검색 보강"""

    def __init__(self, kb_path: Path = KB_PATH, enable_web: bool = True):
        self.kb_path = kb_path
        self.enable_web = enable_web
        self.data: Dict[str, Any] = {}
        self.entries: List[Dict[str, Any]] = []
        self.rubric: Dict[str, Any] = {}
        self._lookup: Dict[str, Dict[str, Any]] = {}
        self._web_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._load()

    def _load(self):
        try:
            with open(self.kb_path, "r", encoding="utf-8") as f:
                self.data = json.load(f)
            self.entries = self.data.get("entries", [])
            self.rubric = self.data.get("rubric", {})
            self._build_lookup()
            logger.info(f"지식베이스 로드 완료: {len(self.entries)}개 항목 (v{self.data.get('version')})")
        except FileNotFoundError:
            logger.warning(f"지식베이스 파일 없음: {self.kb_path} (외부검색/기본값으로 동작)")
            self.data, self.entries, self.rubric = {}, [], {}
        except Exception as e:
            logger.error(f"지식베이스 로드 실패: {e}")
            self.data, self.entries, self.rubric = {}, [], {}

    def _build_lookup(self):
        self._lookup = {}
        for entry in self.entries:
            keys = [entry.get("canonical_name"), entry.get("korean_name")]
            keys += entry.get("aliases", []) or []
            for k in keys:
                if k:
                    self._lookup[_norm(k)] = entry

    # ---------- 조회 ----------
    def lookup(self, name: str) -> Optional[Dict[str, Any]]:
        """지식베이스에서 알레르겐 항목 조회 (정확→부분→퍼지)"""
        if not name:
            return None
        n = _norm(name)
        if n in self._lookup:
            return self._lookup[n]
        # 부분 매칭
        for key, entry in self._lookup.items():
            if key and (key in n or n in key) and len(key) >= 2:
                return entry
        # 퍼지 매칭
        best, best_score = None, 0.0
        for key, entry in self._lookup.items():
            score = SequenceMatcher(None, n, key).ratio()
            if score > best_score:
                best_score, best = score, entry
        if best is not None and best_score >= 0.82:
            return best
        return None

    def get_backdata(
        self,
        name: str,
        category: Optional[str] = None,
        korean_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """알레르겐 backdata 반환. 지식베이스 → 외부검색 → 기본값 순으로 보강한다.
        항상 표준 스키마의 dict 를 반환하며 'source' 필드로 출처를 표시한다.
        """
        entry = self.lookup(name) or (self.lookup(korean_name) if korean_name else None)
        if entry:
            result = dict(entry)
            result.setdefault("source", "knowledge_base")
            result["category"] = normalize_category(result.get("category") or category)
            return result

        # 카테고리 결정 파이프라인(P4): 명시값 → base map → alias → regex → 미분류 로그.
        # (Celery·Apple 등 음식이 KB 18종에 없어 'other' 로 떨어지던 문제 + Dog hair·Horse dander
        #  같은 이름변형이 접미사 regex 로 흡수되어 동물 문진 누락 버그도 해결)
        from services.category_resolver import resolve_category
        cat = resolve_category(name, korean_name or "", category)

        # 외부검색 (Wikipedia) 보강
        web = None
        if self.enable_web:
            web = self._wikipedia_lookup(korean_name or name)

        base = dict(CATEGORY_DEFAULTS.get(cat, CATEGORY_DEFAULTS["other"]))
        result: Dict[str, Any] = {
            "canonical_name": name,
            "korean_name": korean_name or name,
            "category": cat,
            "aliases": [],
            "biology_ko": (web or {}).get("summary")
            or f"'{korean_name or name}'에 대한 상세 정보는 준비 중입니다. 담당 의료진과 상담을 권장합니다.",
            "exposure_environment_ko": "",
            "distribution_korea_ko": "",
            "seasonality_pattern": base.get("seasonality_pattern", "perennial"),
            "peak_months_korea": base.get("peak_months_korea", []),
            "season_label_ko": base.get("season_label_ko", "연중"),
            "indoor_outdoor": base.get("indoor_outdoor", "both"),
            "cross_reactivity_ko": "",
            "oral_allergy_syndrome_ko": "",
            "typical_symptoms_ko": [],
            "avoidance_control_ko": [],
            "relevance_probes_ko": base.get("relevance_probes_ko", []),
            "clinical_pearl_ko": "검사 양성은 감작을 뜻하며, 노출 시 실제 증상이 재현되는지 확인이 필요합니다.",
            "sources": (web or {}).get("sources", []),
            "source": "wikipedia" if web else "category_default",
        }
        return result

    # ---------- 외부검색 (Wikipedia) ----------
    def _wikipedia_lookup(self, query: str) -> Optional[Dict[str, Any]]:
        """Wikipedia REST summary API 로 최소 backdata 조회 (키 불필요).
        네트워크 제약 환경에서는 조용히 실패한다.
        """
        if not query:
            return None
        if query in self._web_cache:
            return self._web_cache[query]

        result = None
        try:
            import httpx

            headers = {"User-Agent": "APAAACI-Allergy-Report/1.0 (patient education)"}
            for lang in ("ko", "en"):
                url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{query}"
                try:
                    resp = httpx.get(url, headers=headers, timeout=6.0, follow_redirects=True)
                    if resp.status_code == 200:
                        data = resp.json()
                        extract = data.get("extract")
                        if extract:
                            result = {
                                "summary": extract,
                                "sources": [data.get("content_urls", {})
                                            .get("desktop", {})
                                            .get("page", f"https://{lang}.wikipedia.org/wiki/{query}")],
                            }
                            break
                except Exception:
                    continue
        except Exception as e:
            logger.info(f"Wikipedia 조회 불가(무시): {e}")

        self._web_cache[query] = result
        return result

    # ---------- 룰(감별 규칙) ----------
    def get_rubric(self) -> Dict[str, Any]:
        return self.rubric or {}

    def get_category_rule(self, category: str) -> Optional[Dict[str, Any]]:
        cat = normalize_category(category)
        for rule in self.rubric.get("category_rules", []):
            if normalize_category(rule.get("category")) == cat:
                return rule
        return None

    # ---------- 면역치료 적용 가능성 ----------
    def immunotherapy_info(self, category: Optional[str], name: str = "") -> Dict[str, Any]:
        cat = normalize_category(category)
        info = IMMUNOTHERAPY_BY_CATEGORY.get(cat, {"eligible": False, "ko": ""})
        return {"eligible": bool(info.get("eligible")), "ko": info.get("ko", ""), "category": cat}

    # ---------- 꽃가루-음식 교차반응(PFAS/OAS) ----------
    def _load_pfas(self) -> Dict[str, Any]:
        if getattr(self, "_pfas", None) is None:
            try:
                with open(PFAS_PATH, "r", encoding="utf-8") as f:
                    self._pfas = json.load(f)
            except Exception as e:
                logger.warning(f"PFAS 데이터셋 로드 실패: {e}")
                self._pfas = {"pollen_food": {}, "category_fallback": {}}
        return self._pfas

    def pfas_foods_for(self, canonical_name: str, category: Optional[str] = None) -> Dict[str, Any]:
        """양성 꽃가루에 대해 교차반응 가능 음식 목록 반환.
        반환: {"group_ko": str, "foods": [{"ko","en"}...]} 또는 빈 dict."""
        data = self._load_pfas()
        pf = data.get("pollen_food", {})
        entry = pf.get(canonical_name)
        # 이름 매칭 실패 시 카테고리 대표값으로 폴백
        if not entry and category:
            fb = data.get("category_fallback", {}).get(normalize_category(category))
            entry = pf.get(fb) if fb else None
        if not entry:
            return {}
        # same_as 참조 해소
        if entry.get("same_as"):
            ref = pf.get(entry["same_as"], {})
            foods = ref.get("foods", [])
            return {"group_ko": entry.get("group_ko", ""), "foods": foods}
        return {"group_ko": entry.get("group_ko", ""), "foods": entry.get("foods", [])}

    def mite_shellfish(self) -> Dict[str, Any]:
        """진드기↔갑각류(트로포마이오신) 교차반응 정보."""
        return self._load_pfas().get("mite_shellfish", {}) or {}

    def is_shellfish(self, name: str, korean_name: str = "") -> bool:
        """알러젠 이름이 갑각류(새우·게 등)인지 판별."""
        data = self._load_pfas()
        needles = [s.lower() for s in data.get("shellfish_food_names", [])]
        hay = f"{name or ''} {korean_name or ''}".lower()
        return any(s in hay for s in needles)

    def stats(self) -> Dict[str, Any]:
        cats: Dict[str, int] = {}
        for e in self.entries:
            c = normalize_category(e.get("category"))
            cats[c] = cats.get(c, 0) + 1
        return {"total": len(self.entries), "by_category": cats, "version": self.data.get("version")}


# 싱글톤
_knowledge_service: Optional[KnowledgeService] = None


@lru_cache(maxsize=1)
def _singleton() -> KnowledgeService:
    return KnowledgeService()


def get_knowledge_service() -> KnowledgeService:
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = _singleton()
    return _knowledge_service
