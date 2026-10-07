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
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from config.settings import BASE_DIR, settings

logger = logging.getLogger(__name__)

KB_PATH = BASE_DIR / "data" / "allergen_knowledge_base.json"
# 카테고리·성분군 템플릿으로 만든 항원 지식(레지스트리 148종 중 개별 지식이 없는 128종).
# 개별 지식(KB_PATH, 사람이 쓴 18종)이 있으면 그쪽이 언제나 우선이다.
# 경로는 KNOWLEDGE_GENERATED_PATH 로 바꿀 수 있다(검토 화면이 이 파일을 고쳐 쓰므로, 시험은 임시 복사본으로 한다).
GENERATED_KB_PATH = Path(settings.knowledge_generated_path)
PFAS_PATH = BASE_DIR / "data" / "pollen_food_cross_reactivity.json"
# 항원 단위 면역치료 가능 목록(경로·국내 가용성·근거·출처). 카테고리 표보다 언제나 우선한다.
IMMUNOTHERAPY_PATH = BASE_DIR / "data" / "immunotherapy_allergens.json"

# shellfish(조개·갑각) 판별용 무척추 근육 범알레르겐 성분 — 어류(parvalbumin)와 구분
_SHELLFISH_MARKER_COMPONENTS = {"tropomyosin"}

# 알레르겐 면역치료(SCIT/SLIT) 적용 가능성 (카테고리 기준)
# 항원 이름 없이 카테고리만 물을 때의 설명문이다. 환자별 판단은 항원 단위 목록
# (data/immunotherapy_allergens.json, KnowledgeService.immunotherapy_entry)으로 한다.
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
               "ko": "곤충은 종류에 따라 다릅니다. 벌독(꿀벌·말벌)에 쏘여 전신 반응이 있었던 경우의 "
                     "벌독 면역치료는 효과가 확립된 치료입니다(국내 제품 허가·공급은 진료에서 확인). "
                     "바퀴 같은 실내 곤충 흡입 알레르기는 근거가 제한적이라 환경 관리가 우선입니다."},
    # 벌독은 감작만으로는 대상이 아니다 — 쏘인 뒤 전신 반응 병력이 확인돼야 한다(항원 단위 목록의 requires).
    "venom": {"eligible": False,
              "ko": "벌독 면역치료는 벌에 쏘인 뒤 전신 반응이 있었고 벌독 감작이 확인된 경우에 고려하는 "
                    "치료입니다. 쏘인 자리의 국소 반응만 있었다면 대상이 아닙니다(국내 제품 허가·공급은 진료에서 확인)."},
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
    # 벌독처럼 '쏘여서' 들어오는 곤충 독. 바퀴 같은 흡입 곤충(insect)과 노출 경로·관리가 다르다.
    "venom": "venom",
    "insect_venom": "venom",
    "food": "food",
    # 라텍스(접촉)·약물·검사 대조는 흡입 항원도 음식도 아니다. 예전에는 셋 다 'other' 로 뭉쳐 보내
    # 검사 대조(히스타민·생리식염수)가 알러젠처럼 판정·문진·리포트에 올랐다.
    "latex": "latex",
    "drug": "drug",
    "medication": "drug",
    "control": "control",
    "other": "other",
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
    # 곤충 독(벌독). 꽃가루가 아니므로 비산 시기·악화 월을 두지 않는다 — 계절 달력에 넣지 않기 위해서다.
    # 아래 문장은 개별 지식(KB)이 없을 때 쓰는 최소 안내이며 전문의 검토 전(candidate)이다.
    "venom": {
        "seasonality_pattern": "episodic", "indoor_outdoor": "outdoor",
        "season_label_ko": "",
        # 어느 벌인지 알 수 없는 이름('벌독'·'Hornet venom')에 쓰는 소개문 — '상세 정보는 준비 중입니다' 대신
        "biology_ko": "벌이 쏠 때 몸에 넣는 독입니다. 숨으로 들이마시거나 먹어서 생기는 알레르기가 아니라, "
                      "쏘였을 때의 반응이 문제입니다.",
        "exposure_environment_ko": "벌에 쏘일 때 독이 몸에 들어옵니다. 숨으로 들이마시거나 먹어서 생기는 "
                                   "알레르기가 아닙니다. 등산·벌초·성묘·캠핑 같은 야외 활동과 벌집 근처에서 쏘이는 일이 많습니다.",
        "avoidance_control_ko": [
            "벌집 근처에 가지 않기, 벌이 다가오면 팔을 휘두르지 말고 천천히 자리 피하기",
            "야외에서는 긴 옷을 입고 향이 강한 향수·화장품 피하기",
            "야외에서 단 음료·음식은 뚜껑을 덮어 두기",
            "쏘인 뒤 온몸 두드러기·숨참·어지럼이 오면 바로 119에 연락하기",
        ],
        "relevance_probes_ko": [
            "벌에 쏘인 적이 있나요? 쏘였을 때 쏘인 자리만 부었나요, 온몸에 반응이 있었나요?",
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
    # 라텍스는 닿아서(장갑·풍선·콘돔·의료기기) 들어오는 항원이다. 계절이 없고, 흡입 항원의 노출 줄이기
    # 문장을 붙이지 않는다. 문장은 data/allergen_category_profiles.json 의 latex 템플릿과 같다.
    "latex": {
        "seasonality_pattern": "none", "indoor_outdoor": "n/a",
        "season_label_ko": "",
        "exposure_environment_ko": "천연고무(라텍스)로 만든 제품에 닿을 때 노출됩니다. 고무장갑·풍선·콘돔, "
                                   "그리고 병원·치과에서 쓰는 장갑·카테터 같은 의료기기가 대표적입니다.",
        "avoidance_control_ko": [
            "병원·치과 진료나 시술 전에 라텍스에 반응한 적이 있다고 미리 알리기",
            "고무장갑·풍선처럼 라텍스로 만든 제품은 라텍스가 없는 제품(니트릴·비닐)으로 바꾸기",
        ],
        "relevance_probes_ko": [
            "고무장갑·풍선·콘돔 같은 라텍스 제품에 닿은 뒤 그 자리가 가렵거나 부은 적이 있나요?",
            "치과·병원 진료 뒤 입술·얼굴이 붓거나 숨이 찬 적이 있나요?",
        ],
    },
    # 약물. 특이 IgE 양성만으로는 약물 알레르기라고 하지 않는다. 회피 수칙을 두지 않는다 —
    # 그 약을 피할지, 다시 써도 되는지는 진료에서 정한다.
    "drug": {
        "seasonality_pattern": "none", "indoor_outdoor": "n/a",
        "season_label_ko": "",
        "exposure_environment_ko": "이 약(같은 계열의 약 포함)을 먹거나 주사로 맞을 때 노출됩니다.",
        "avoidance_control_ko": [],
        "relevance_probes_ko": [
            "이 약을 쓴 뒤 두드러기·얼굴 부기·숨참 같은 반응이 있었나요?",
        ],
    },
    # 검사 대조(히스타민·생리식염수). 알러젠이 아니다 — 판정·문진·회피 수칙의 대상이 아니다.
    "control": {
        "seasonality_pattern": "none", "indoor_outdoor": "n/a",
        "season_label_ko": "",
        "exposure_environment_ko": "이 항목은 알레르겐이 아니라 검사가 제대로 되었는지 확인하는 대조입니다.",
        "avoidance_control_ko": [],
        "relevance_probes_ko": [],
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

    def __init__(self, kb_path: Path = KB_PATH, enable_web: Optional[bool] = None,
                 generated_path: Path = GENERATED_KB_PATH, include_candidates: bool = False):
        self.kb_path = kb_path
        self.generated_path = generated_path
        # 검토 전(candidate) 문장을 환자 화면에 내보낼지. 기본은 False —
        # 승인 전에는 보이지 않는다. 검토 화면만 True 로 읽는다.
        self.include_candidates = include_candidates
        # 모르는 이름을 Wikipedia 에서 찾을지. 기본은 설정값(ALLERGEN_WIKIPEDIA_LOOKUP, 기본 끔)이다.
        # 켜 두면 레지스트리에 없는 이름(오타·환자가 직접 친 글)이 그대로 외부로 나가고, 돌아온 글이
        # 항원과 무관해도(동음이의 문서 '말은 다음을 가리킨다', 사람 이름 문서 등) 환자용 소개문이 된다.
        self.enable_web = settings.allergen_wikipedia_lookup if enable_web is None else enable_web
        self.data: Dict[str, Any] = {}
        self.entries: List[Dict[str, Any]] = []
        self.rubric: Dict[str, Any] = {}
        self._lookup: Dict[str, Dict[str, Any]] = {}
        self.generated: List[Dict[str, Any]] = []
        self._generated_lookup: Dict[str, Dict[str, Any]] = {}
        self._web_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._load()
        self._load_generated()

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

    def _load_generated(self):
        """템플릿 기반 항원 지식 적재. 파일이 없어도 앱은 그대로 돈다."""
        try:
            with open(self.generated_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.generated = data.get("entries", [])
            # 별칭을 먼저 넣고 정식 이름(영문·한글)으로 덮는다 — 한 항원의 별칭이 다른 항원의 정식 이름과
            # 같을 때(옥수수의 별칭 '옥수수가루' = Cornflour 의 한글명) 정식 이름 쪽이 이기게 한다.
            # 예전에는 파일에 적힌 순서대로 뒤 항목이 이겨서, 조회 결과가 항목 순서에 달려 있었다.
            self._generated_lookup = {}
            for e in self.generated:
                for k in e.get("aliases") or []:
                    if k:
                        self._generated_lookup[_norm(k)] = e
            for e in self.generated:
                for k in (e.get("canonical_name"), e.get("korean_name")):
                    if k:
                        self._generated_lookup[_norm(k)] = e
            logger.info(f"템플릿 항원 지식 로드: {len(self.generated)}종 "
                        f"(v{data.get('version')}, LLM 후보 {data.get('llm_candidates', 0)}건)")
        except FileNotFoundError:
            logger.info(f"템플릿 항원 지식 없음: {self.generated_path} (개별 KB·기본값으로 동작)")
            self.generated, self._generated_lookup = [], {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"템플릿 항원 지식 로드 실패: {e}")
            self.generated, self._generated_lookup = [], {}

    def lookup_generated(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        if not name:
            return None
        return self._generated_lookup.get(_norm(name))

    def _strip_candidates(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """검토 전 문장을 걷어낸다.

        `<field>_status == "candidate"` 인 필드는 승인 전까지 환자에게 보이지 않는다.
        LLM 이 쓴 소개문이 여기에 해당한다.
        """
        if self.include_candidates:
            return entry
        out = dict(entry)
        for field in [k for k in list(out) if not k.endswith("_status")]:
            if out.get(f"{field}_status") == "candidate":
                out.pop(field, None)
                out.pop(f"{field}_status", None)
                out.pop(f"{field}_model", None)
                out.pop(f"{field}_generated_at", None)
        return out

    def _build_lookup(self):
        self._lookup = {}
        for entry in self.entries:
            keys = [entry.get("canonical_name"), entry.get("korean_name")]
            keys += entry.get("aliases", []) or []
            for k in keys:
                if k:
                    self._lookup[_norm(k)] = entry

    # ---------- 조회 ----------
    def lookup_exact(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        """이름이 정확히 일치하는 개별 지식만. 부분·퍼지 매칭을 쓰지 않는다."""
        if not name:
            return None
        return self._lookup.get(_norm(name))

    def lookup(self, name: str, category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """지식베이스에서 알레르겐 항목 조회 — 정확 일치, 아니면 '같은 항원의 다른 표기'일 때만.

        같은 항원의 다른 표기 = 수식어·띄어쓰기만 다르거나('Birch' / 'Birch pollen'), 5글자 이상 낱말에서
        OCR 오타 한 번(utils.allergen_mapper.same_antigen_name). 글자가 겹친다는 것만으로는 잇지 않는다.
        예전에는 부분 문자열(key in name / name in key)과 전체 문자열 유사도(0.82)로 이어, 다른 항원의
        설명·회피 수칙이 붙었다: Pea(완두콩) → 땅콩('적은 양으로도 심한 반응(아나필락시스)… 철저히
        확인·회피'), Eggplant → 난백, Cattle epithelium → 고양이 비듬, Cow·Milkweed → 우유, 벌 → 바퀴벌레.
        이름이 그 항원이 아니면 None 을 돌려주고, 호출한 쪽이 카테고리 기본 안내로 떨어진다.
        category: 이 이름의 카테고리를 알고 있으면(other 제외) 카테고리가 다른 항목은 잇지 않는다
        (약물 Penicillin 의 오타가 곰팡이 Penicillium 에 붙지 않게)."""
        if not name:
            return None
        n = _norm(name)
        if n in self._lookup:
            return self._lookup[n]
        # 일반 낱말만으로 된 이름('Dust'·'Pollen'·'Mix')은 어떤 항원도 가리키지 않는다.
        from utils.allergen_mapper import is_generic_name, same_antigen_name
        if is_generic_name(name):
            return None
        want = normalize_category(category) if category else "other"
        found = None
        for key, entry in self._lookup.items():
            if not same_antigen_name(n, key):
                continue
            if want != "other" and normalize_category(entry.get("category")) != want:
                continue
            if found is not None and found is not entry:
                return None        # 두 항원에 걸리는 이름 — 어느 것인지 알 수 없다
            found = entry
        return found

    def generated_stats(self) -> Dict[str, Any]:
        cand = sum(1 for e in self.generated
                   if any(k.endswith("_status") and v == "candidate" for k, v in e.items()))
        by_profile: Dict[str, int] = {}
        for e in self.generated:
            k = e.get("profile_key", "?")
            by_profile[k] = by_profile.get(k, 0) + 1
        return {"total": len(self.generated), "llm_candidates": cand, "by_profile": by_profile}

    def get_backdata(
        self,
        name: str,
        category: Optional[str] = None,
        korean_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """알레르겐 backdata 반환. 지식베이스 → 외부검색 → 기본값 순으로 보강한다.
        항상 표준 스키마의 dict 를 반환하며 'source' 필드로 출처를 표시한다.
        """
        # 조회 순서가 중요하다.
        #   1) 개별 지식 **정확 일치**  2) 템플릿 지식 **정확 일치**  3) 개별 지식 부분/퍼지  4) 기본값
        # 2 와 3 의 순서를 바꾸면 퍼지 매칭이 엉뚱한 항원을 집어온다. 실제로 '굴'(Oyster)이
        # '환삼덩굴 꽃가루'의 부분 문자열이라 음식에 꽃가루 지식(가을 시즌·야외 회피)이 붙었고,
        # '콩'은 '땅콩', '토끼 상피'는 '고양이 비듬'으로 붙었다.
        # 검사 대조(히스타민·생리식염수·양성/음성 대조)는 알러젠이 아니다. 이름 매칭(부분·퍼지)으로
        # 넘기면 다른 항원의 지식이 붙는다 — 여기서 끝낸다.
        from services.category_resolver import control_kind, resolve_category
        if control_kind(name, korean_name or "", category):
            own = self._registry_knowledge(name, korean_name)
            if own and normalize_category(own.get("category")) == "control":
                own["category"] = "control"
                return own
            return self._default_backdata(name, korean_name, "control")

        entry = self.lookup_exact(name) or self.lookup_exact(korean_name)
        if entry:
            result = dict(entry)
            result.setdefault("source", "knowledge_base")
            result["category"] = normalize_category(result.get("category") or category)
            return result

        # 개별 지식이 없으면 카테고리·성분군 템플릿으로 답한다(128종). 빈 화면보다 낫고,
        # 회피 수칙처럼 실행에 쓰이는 문장은 사람이 쓴 템플릿이라 검증돼 있다.
        gen = self.lookup_generated(name) or self.lookup_generated(korean_name)
        if gen:
            result = self._strip_candidates(gen)
            result.setdefault("source", "category_profile")
            result["category"] = normalize_category(result.get("category") or category)
            return result

        # 레지스트리의 별칭·OCR 변형으로 들어온 이름은 그 항원 자신의 지식으로 보낸다. 여기서 못 잡으면
        # 아래 부분/퍼지 매칭이 다른 항원을 집어온다('Egg yo1k' → 난백, '집먼지' → 집먼지진드기).
        own = self._registry_knowledge(name, korean_name)
        if own:
            own["category"] = normalize_category(own.get("category") or category)
            return own

        # 결과지는 항원명을 괄호와 함께 인쇄한다('Dermatophagoides farinae (Df)', 'Birch (t3)', 'Alder (오리나무)').
        # 괄호 밖·안의 조각 가운데 어느 항원의 이름과 정확히 같은 것이 있으면 그 항원이다.
        from utils.allergen_mapper import AllergenMapper
        for part in AllergenMapper._split_parenthetical(name or ""):
            entry = self.lookup_exact(part)
            if entry:
                result = dict(entry)
                result.setdefault("source", "knowledge_base")
                result["category"] = normalize_category(result.get("category") or category)
                return result
            gen = self.lookup_generated(part)
            if gen:
                result = self._strip_candidates(gen)
                result.setdefault("source", "category_profile")
                result["category"] = normalize_category(result.get("category") or category)
                return result
            own = self._registry_knowledge(part, None)
            if own:
                own["category"] = normalize_category(own.get("category") or category)
                return own

        # 카테고리 결정 파이프라인(P4): 명시값 → base map → alias → regex → 미분류 로그.
        # (Celery·Apple 등 음식이 KB 18종에 없어 'other' 로 떨어지던 문제 + Dog hair·Horse dander
        #  같은 이름변형이 접미사 regex 로 흡수되어 동물 문진 누락 버그도 해결)
        cat = resolve_category(name, korean_name or "", category)

        # 레지스트리에 없는 이름은 '같은 항원의 다른 표기'(수식어·띄어쓰기·OCR 오타 한 번)일 때만 개별 지식으로
        # 구제한다. 그 항원이 아닌 이름은 여기서 잡히지 않고 아래의 카테고리 기본 안내로 간다.
        entry = self.lookup(name, cat) or (self.lookup(korean_name, cat) if korean_name else None)
        if entry:
            result = dict(entry)
            result.setdefault("source", "knowledge_base_fuzzy")
            result["category"] = normalize_category(result.get("category") or category)
            return result

        # 외부검색 (Wikipedia) 보강
        web = None
        if self.enable_web:
            web = self._wikipedia_lookup(korean_name or name)
        return self._default_backdata(name, korean_name, cat, web)

    def _registry_knowledge(self, name: str, korean_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """이름이 레지스트리 항원의 이름·별칭과 정확히 같으면 그 항원의 지식(개별 → 템플릿)."""
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            hit = get_crossreactivity_service().find_exact(name or "", korean_name or "")
        except Exception:  # noqa: BLE001
            hit = None
        if not hit:
            return None
        for key in (hit.get("kb_ref"), hit.get("canonical_name"), hit.get("korean_name")):
            entry = self.lookup_exact(key)
            if entry:
                result = dict(entry)
                result.setdefault("source", "knowledge_base")
                return result
        for key in (hit.get("canonical_name"), hit.get("korean_name")):
            gen = self.lookup_generated(key)
            if gen:
                result = self._strip_candidates(gen)
                result.setdefault("source", "category_profile")
                return result
        return None

    @staticmethod
    def _default_backdata(name: str, korean_name: Optional[str], cat: str,
                          web: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """개별 지식·템플릿이 없는 이름의 최소 backdata(카테고리 기본값)."""
        base = dict(CATEGORY_DEFAULTS.get(cat, CATEGORY_DEFAULTS["other"]))
        if cat == "control":
            biology = ""
            pearl = "검사가 제대로 되었는지 보는 대조 항목입니다. 알레르기 여부를 뜻하지 않으며 피할 대상이 아닙니다."
        else:
            biology = (web or {}).get("summary") or base.get("biology_ko") or (
                f"'{korean_name or name}'에 대한 상세 정보는 준비 중입니다. 담당 의료진과 상담을 권장합니다.")
            pearl = "검사 양성은 감작을 뜻하며, 노출 시 실제 증상이 재현되는지 확인이 필요합니다."
        result: Dict[str, Any] = {
            "canonical_name": name,
            # 모르는 이름의 한글명은 비워 둔다. 입력한 글자를 한글명으로 되돌려주면 '직접 입력' 화면이
            # 영문 오타를 한글 이름 칸에 그대로 옮겨 적는다. 표시할 때는 영문명(canonical_name)을 쓴다.
            "korean_name": korean_name or None,
            "category": cat,
            "aliases": [],
            "biology_ko": biology,
            "exposure_environment_ko": base.get("exposure_environment_ko", ""),
            "distribution_korea_ko": "",
            "seasonality_pattern": base.get("seasonality_pattern", "perennial"),
            "peak_months_korea": base.get("peak_months_korea", []),
            "season_label_ko": base.get("season_label_ko", "연중"),
            "indoor_outdoor": base.get("indoor_outdoor", "both"),
            "cross_reactivity_ko": "",
            "oral_allergy_syndrome_ko": "",
            "typical_symptoms_ko": [],
            "avoidance_control_ko": list(base.get("avoidance_control_ko", [])),
            "relevance_probes_ko": base.get("relevance_probes_ko", []),
            "clinical_pearl_ko": pearl,
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
        query = query.strip()
        # 항원 이름으로 볼 수 없는 값은 보내지 않는다(경로 조각·너무 긴 글).
        if not query or len(query) > 60 or any(ch in query for ch in "/\\?#%\n\r"):
            return None
        if query in self._web_cache:
            return self._web_cache[query]

        result = None
        try:
            import httpx

            headers = {"User-Agent": "APAAACI-Allergy-Report/1.0 (patient education)"}
            for lang in ("ko", "en"):
                url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(query, safe='')}"
                try:
                    resp = httpx.get(url, headers=headers, timeout=6.0, follow_redirects=True)
                    if resp.status_code == 200:
                        data = resp.json()
                        extract = data.get("extract")
                        # 동음이의 문서('말은 다음을 가리킨다')는 항원 설명이 아니다
                        if extract and data.get("type") == "standard":
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
    def _load_immunotherapy(self) -> Dict[str, Any]:
        if getattr(self, "_imt", None) is None:
            try:
                with open(IMMUNOTHERAPY_PATH, "r", encoding="utf-8") as f:
                    self._imt = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"면역치료 항원 목록 로드 실패: {e}")
                self._imt = {"entries": [], "sources": {}}
        return self._imt

    def immunotherapy_entry(self, name: Optional[str], korean_name: str = "",
                            include_needs_review: bool = False) -> Optional[Dict[str, Any]]:
        """이 항원에 면역치료가 가능한지 — 항원 단위 목록에서 찾는다. 없으면 None.

        레지스트리(data/allergens.json) id 로 먼저 맞추고, 레지스트리에 없는 이름은
        match_names 로 맞춘다. needs_review 항목은 검토 전이라 기본으로는 돌려주지 않는다.
        """
        if not name and not korean_name:
            return None
        reg_id = None
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            hit = get_crossreactivity_service().find(name or "", korean_name or "")
            reg_id = (hit or {}).get("id")
        except Exception:  # noqa: BLE001
            pass
        keys = {_norm(x) for x in (name, korean_name) if x}
        for e in self._load_immunotherapy().get("entries", []):
            if (reg_id and reg_id in (e.get("registry_ids") or [])) or \
                    keys & {_norm(m) for m in (e.get("match_names") or [])}:
                if e.get("needs_review") and not include_needs_review:
                    return None
                return e
        return None

    @staticmethod
    def immunotherapy_requirement_met(entry: Dict[str, Any], assessment) -> bool:
        """목록 항목이 요구하는 병력(requires)이 이 환자에게서 확인됐는가.

        벌독 면역치료는 벌에 쏘인 뒤 전신 반응이 있었던 환자가 대상이다. 감작과 '증상 있음'만으로
        열면, 봄철 코 증상에 '예'라고 답한 환자에게 벌독 면역치료를 안내하게 된다.
        알 수 없는 요구 조건은 충족되지 않은 것으로 본다.
        """
        req = entry.get("requires")
        if not req:
            return True
        if req == "systemic_sting_reaction":
            return (getattr(assessment, "answers", None) or {}).get("sting_reaction") == "systemic"
        return False

    def immunotherapy_candidates(self, assessments) -> List[Dict[str, Any]]:
        """감작 + 증상이 함께 확인된(임상적으로 의미 있는) 항원 중 면역치료 가능 항원.

        같은 목록 항목에 걸리는 항원(유럽·미국 집먼지진드기 등)은 하나로 묶는다.
        반환: [{"entry": 목록 항목, "allergens": [환자 검사지의 항원 이름]}]
        """
        out: Dict[str, Dict[str, Any]] = {}
        for a in assessments or []:
            rel = getattr(a, "relevance", None)
            if getattr(rel, "value", rel) != "clinically_relevant":
                continue
            e = self.immunotherapy_entry(getattr(a, "allergen_name", None),
                                         getattr(a, "korean_name", None) or "")
            if not e or not self.immunotherapy_requirement_met(e, a):
                continue
            row = out.setdefault(e["key"], {"entry": e, "allergens": []})
            nm = getattr(a, "korean_name", None) or getattr(a, "allergen_name", "")
            if nm and nm not in row["allergens"]:
                row["allergens"].append(nm)
        return list(out.values())

    def immunotherapy_sources(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """목록 항목들이 근거로 든 출처(중복 없이, 처음 나온 순서)."""
        src = self._load_immunotherapy().get("sources", {})
        ids: List[str] = []
        for e in entries:
            for sid in e.get("sources") or []:
                if sid in src and sid not in ids:
                    ids.append(sid)
        return [dict(src[i], id=i) for i in ids]

    def immunotherapy_info(self, category: Optional[str], name: str = "",
                           korean_name: str = "", assessment=None) -> Dict[str, Any]:
        """면역치료 가능 여부. 항원 이름이 있으면 항원 단위 목록으로 판단한다.

        카테고리만으로 답하면 같은 '진드기'·'곤충' 안에서 면역치료 추출물이 없는 항원
        (긴털가루진드기 등)까지 대상으로 안내하게 된다. 이름이 없을 때만 카테고리 표를 쓴다.
        병력 조건(requires)이 있는 항목은 assessment 로 그 병력이 확인될 때만 대상이다.
        """
        cat = normalize_category(category)
        info = IMMUNOTHERAPY_BY_CATEGORY.get(cat, {"eligible": False, "ko": ""})
        if name or korean_name:
            entry = self.immunotherapy_entry(name, korean_name)
            if entry and not self.immunotherapy_requirement_met(entry, assessment):
                entry = None
            return {"eligible": entry is not None, "ko": info.get("ko", "") if entry else "",
                    "category": cat, "entry": entry}
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

    def is_shellfish(self, name: str, korean_name: str = "") -> bool:
        """알러젠이 조개·갑각류(무척추 수산물)인지 판별 — 레지스트리(성분) 기반.

        규칙: 항원 레지스트리에서 category=food 이면서 무척추 근육 범알레르겐
        tropomyosin 을 보유하면 shellfish(새우·게·조개·오징어 등).
        어류(Cod·Salmon 등)는 parvalbumin 을 가지므로 제외, 진드기·바퀴는
        tropomyosin 을 갖지만 food 가 아니므로 제외된다.
        레지스트리 미로드 시 기존 이름 리스트(shellfish_food_names)로 폴백한다.
        """
        try:
            from services.crossreactivity_service import get_crossreactivity_service
            svc = get_crossreactivity_service()
            if svc.has_data():
                a = svc.find(name, korean_name or "")
                if a:
                    comps = set(a.get("components", []))
                    return a.get("category") == "food" and bool(comps & _SHELLFISH_MARKER_COMPONENTS)
        except Exception:
            pass
        # 폴백: 레지스트리에 없는 항원 → 이름 기반 리스트
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
