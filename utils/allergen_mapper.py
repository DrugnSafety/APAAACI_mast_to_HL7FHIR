"""
Allergen Mapping Utilities
알레르겐 명칭 정규화 및 SNOMED 코드 매핑
"""

import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from functools import lru_cache
import logging

from models.schemas import AllergenMapping, AllergenDatabase

# 로거 설정
logger = logging.getLogger(__name__)


# 항원 이름에 붙는 일반 낱말. 이것만으로 된 이름('Mix', 'Pollen', '혼합')은 어떤 항원도 가리키지 않는다 —
# 부분·퍼지 매칭에 넘기면 'Mix' 가 'Cockroach, Mix' 에 붙는다.
GENERIC_NAME_TOKENS = frozenset({
    "mix", "mixed", "mixture", "혼합", "믹스", "pollen", "pollens", "꽃가루", "dander", "비듬", "epithelium",
    "epithelia", "상피", "hair", "fur", "털", "feather", "feathers", "깃털", "dust", "먼지", "mold", "mould",
    "곰팡이", "extract", "추출물", "allergen", "protein", "tree", "grass", "weed", "나무", "잔디", "잡초",
    "food", "음식", "indoor", "outdoor", "실내", "실외", "house", "sp", "spp", "species",
})


def _name_tokens(text: str) -> List[str]:
    return [t for t in re.split(r"[\s,;/()·\-]+", (text or "").lower()) if t]


def is_generic_name(text: str) -> bool:
    """일반 낱말(과 숫자)만으로 된 이름인가."""
    tokens = _name_tokens(text)
    return bool(tokens) and all(t in GENERIC_NAME_TOKENS or t.isdigit() for t in tokens)


# ---------------------------------------------------------------------------
# 같은 항원인가 — 이름이 닮았다는 것만으로 다른 항원의 지식·코드를 붙이지 않는다
# ---------------------------------------------------------------------------
# 항원의 정체를 바꾸지 않는 수식어: 'Birch' = 'Birch pollen', 'Cat' = 'Cat dander' = 'Cat epithelium'.
# GENERIC_NAME_TOKENS 보다 좁다 — 'grass'·'tree'·'mix'·'feather'·'dust' 는 정체를 바꾼다
# ('Oat grass' ≠ 'Oat', 'Walnut tree' ≠ 'Walnut', 'House dust' ≠ 'House dust mite').
IDENTITY_QUALIFIERS = frozenset({
    "pollen", "pollens", "꽃가루", "dander", "비듬", "epithelium", "epithelia", "상피", "hair", "fur", "털",
    "allergen", "extract", "추출물", "sp", "spp", "species",
})
_LONG_QUALIFIERS = tuple(q for q in IDENTITY_QUALIFIERS if q.isascii() and len(q) >= 5)
_IDENTITY_TOKEN = re.compile(r"[a-z0-9가-힣一-鿿]+")


@lru_cache(maxsize=50000)
def identity_tokens(text: str) -> Tuple[str, ...]:
    """이름의 정체를 이루는 낱말(소문자, 구두점 제거, 수식어 제외). 수식어의 오타('polen')도 수식어로 본다."""
    return tuple(t for t in _IDENTITY_TOKEN.findall((text or "").lower()) if not _is_qualifier(t))


@lru_cache(maxsize=50000)
def _is_qualifier(token: str) -> bool:
    return token in IDENTITY_QUALIFIERS or any(_one_typo(token, q) for q in _LONG_QUALIFIERS)


@lru_cache(maxsize=200000)
def _one_typo(a: str, b: str) -> bool:
    """두 낱말이 OCR 오타 한 번(글자 하나 바뀜·빠짐·더해짐, 이웃한 두 글자 뒤바뀜) 차이인가.

    5글자 미만은 오타로 잇지 않는다 — 짧은 이름은 한 글자 차이가 다른 항원이다(oat/oak, pea/pear, cod/cow).
    12글자 이상의 학명(Dermatophagoides·pteronyssinus)만 두 번까지 봐준다."""
    if min(len(a), len(b)) < 5 or abs(len(a) - len(b)) > 2:
        return False
    limit = 2 if min(len(a), len(b)) >= 12 else 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[-1] <= limit


def same_antigen_name(a: str, b: str, allow_typo: bool = True) -> bool:
    """두 이름이 같은 항원을 가리키는가 — 낱말 단위로만 본다.

    같다: 수식어만 다르다('Birch' / 'Birch pollen'), 띄어쓰기·낱말 순서만 다르다('Ryegrass' / 'Rye grass',
    'German cockroach' / 'Cockroach, German'), 낱말 수가 같고 5글자 이상 낱말에서 오타 한 번('Altenaria').
    다르다: 낱말이 통째로 더 있거나 없다('Cow' / 'Cow milk', 'Rice weevil' / 'Rice'), 글자만 겹친다
    ('Pea' / 'Peanut', 'Eggplant' / 'Egg white', 'Cattle' / 'Cat', 'Milkweed' / 'Milk').
    예전의 부분 문자열·전체 문자열 유사도 매칭은 뒤의 것들을 같은 항원으로 보아, 완두콩에 땅콩의
    아나필락시스 경고가, 가지에 난백의 설명이, 소 상피에 고양이 비듬의 설명이 붙었다."""
    ta, tb = identity_tokens(a), identity_tokens(b)
    if not ta or not tb:
        return False
    if "".join(ta) == "".join(tb) or sorted(ta) == sorted(tb):
        return True
    if not allow_typo or len(ta) != len(tb):
        return False
    return all(x == y or _one_typo(x, y) for x, y in zip(ta, tb))


class AllergenMapper:
    """알레르겐 매핑 및 정규화 클래스"""
    
    def __init__(self, mapping_file_path: Optional[Path] = None):
        """
        Args:
            mapping_file_path: allergen_map_prompt_v2.json 파일 경로
        """
        if mapping_file_path is None:
            from config.settings import settings
            mapping_file_path = settings.get_allergen_map_path()
        
        self.mapping_file_path = mapping_file_path
        self.database = self._load_database()
        self._build_lookup_tables()
        self._load_chinese_names()
        self._load_cdm_map()
        self._load_snomed_ct_map()

    def _load_snomed_ct_map(self):
        """실제 SNOMED CT SCTID 매핑(사용자 제공) — get_coding 최우선 소스."""
        self.snomed_ct_map = {}
        try:
            path = Path(__file__).resolve().parent.parent / "data" / "snomed_ct_map.json"
            if not path.exists():
                return
            data = json.load(open(path, encoding="utf-8"))
            for name, v in (data.get("map") or {}).items():
                self.snomed_ct_map[self._cdm_norm(name)] = {
                    "system": data.get("system", "http://snomed.info/sct"),
                    "code": str(v["code"]), "display": v.get("display") or name}
            logger.info(f"SNOMED CT SCTID 매핑 로드: {len(self.snomed_ct_map)}개")
        except Exception as e:
            logger.warning(f"SNOMED CT 매핑 로드 실패(무시): {e}")

    # ------------------------------------------------------------------
    # CDM(OMOP) SNOMED 기매핑 — 병원 제공 엑셀(255a467b CDM_SPT_Mapping.xlsx) 기반
    # data/cdm_snomed_mapping.json 을 로드하여 알러젠별 concept_id·concept_name·
    # vocabulary(SNOMED/LOINC) 를 FHIR coding 의 1순위 소스로 사용한다.
    # ------------------------------------------------------------------
    def _cdm_norm(self, x: str) -> str:
        return re.sub(r"[^a-z0-9가-힣]", "", (x or "").lower())

    def _load_cdm_map(self):
        self.cdm_entries: List[Dict[str, Any]] = []
        self.cdm_lookup: Dict[str, int] = {}
        self.cdm_qualifiers: Dict[str, Any] = {}
        self.cdm_spt_method: Dict[str, Any] = {}
        try:
            path = Path(__file__).resolve().parent.parent / "data" / "cdm_snomed_mapping.json"
            if not path.exists():
                return
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.cdm_entries = data.get("entries", [])
            self.cdm_qualifiers = data.get("qualifiers", {})
            self.cdm_spt_method = data.get("spt_method", {})
            # 저장된 lookup(정규화 키 → 인덱스) 재사용
            self.cdm_lookup = {k: v for k, v in (data.get("lookup") or {}).items()}
            logger.info(f"CDM SNOMED 기매핑 로드: {len(self.cdm_entries)}개 항원, {len(self.cdm_lookup)} 키")
        except Exception as e:  # 데이터 파일 문제로 전체 매핑이 죽지 않도록 방어
            logger.warning(f"CDM SNOMED 기매핑 로드 실패(무시): {e}")

    def get_qualifier_coding(self, key: str) -> Optional[Dict[str, str]]:
        """SPT 측정 성분(장축·단축·평균·A/H비)의 CDM qualifier concept 코딩 생성.
        data/cdm_snomed_mapping.json 의 qualifiers[key] 에서 concept_id·display 를 읽는다.
        vocabulary 미지정 시 OMOP 표준개념으로 간주(system=OMOP)."""
        q = (getattr(self, "cdm_qualifiers", {}) or {}).get(key)
        if not q or not q.get("concept_id"):
            return None
        voc = (q.get("vocabulary") or "OMOP").upper()
        system = {"LOINC": "http://loinc.org", "SNOMED": "http://snomed.info/sct"}.get(
            voc, "https://athena.ohdsi.org/search-terms/terms")
        return {"system": system, "code": str(q["concept_id"]), "display": q.get("display") or key}

    def get_spt_method_coding(self) -> Optional[Dict[str, str]]:
        """SPT 방법(히스타민 양성대조) CDM concept 코딩."""
        m = getattr(self, "cdm_spt_method", {}) or {}
        if not m.get("concept_id"):
            return None
        return {"system": "https://athena.ohdsi.org/search-terms/terms",
                "code": str(m["concept_id"]), "display": m.get("display") or "Skin prick test method"}

    def cdm_find(self, name: str, korean: str = "") -> Optional[Dict[str, Any]]:
        """CDM 기매핑에서 항원 concept 를 조회. 실패 시 접미사 제거·부분일치로 재시도."""
        if not self.cdm_entries:
            return None
        for cand in (name, korean):
            k = self._cdm_norm(cand)
            if k and k in self.cdm_lookup:
                return self.cdm_entries[self.cdm_lookup[k]]
        # 'pollen/dander/protein …' 접미사 제거 후 재시도
        stripped = re.sub(r"\b(pollen|dander|epithelium|protein|mix|mixture|allergen|hair|fur|feathers)\b",
                          "", name or "", flags=re.I).strip()
        k = self._cdm_norm(stripped)
        if k and k in self.cdm_lookup:
            return self.cdm_entries[self.cdm_lookup[k]]
        # 부분 일치(정규화 키가 서로 포함) — 짧은 오타/약어 흡수
        if k and len(k) >= 3:
            for key, idx in self.cdm_lookup.items():
                if len(key) >= 3 and (k in key or key in k):
                    return self.cdm_entries[idx]
        return None

    # OMOP(CDM) concept_id 폴백용 system URI — SCTID 와 혼동하지 않기 위해 분리
    OMOP_SYSTEM = "https://athena.ohdsi.org/search-terms/terms"

    def sctid_find(self, name: str, korean: str = "") -> Optional[Dict[str, str]]:
        """실제 SNOMED CT SCTID 매핑 조회. 정확 일치 → 수식어 제거 후 재시도.
        (예: OCR 의 'Birch pollen' → 레지스트리 canonical 'Birch')"""
        m = getattr(self, "snomed_ct_map", {}) or {}
        if not m:
            return None
        for cand in (name, korean):
            k = self._cdm_norm(cand)
            if k and k in m:
                return m[k]
        # 'pollen/dander/protein …' 수식어를 흡수 (CDM 조회와 동일 규칙)
        for cand in (name, korean):
            stripped = re.sub(
                r"\b(pollen|dander|epithelium|protein|mix|mixture|allergen|hair|fur|feathers)\b",
                "", cand or "", flags=re.I).strip()
            k = self._cdm_norm(stripped)
            if k and k in m:
                return m[k]
        return None

    def get_coding(self, name: str, korean: str = "") -> Optional[Dict[str, str]]:
        """FHIR code.coding 1건 생성. 우선순위:
        (1) 실제 SNOMED CT SCTID 매핑(사용자 검토) → (2) CDM(OMOP concept) → (3) 기존 snomed 필드.
        vocabulary 에 따라 SNOMED/LOINC system URI 를 구분한다."""
        sct = self.sctid_find(name, korean)
        if sct:
            return dict(sct)
        cdm = self.cdm_find(name, korean)
        if cdm and cdm.get("concept_id"):
            # concept_id 는 OMOP 식별자이지 SCTID 가 아니다. SNOMED 로 표기하면 수신 측이
            # 잘못된 코드로 검증하게 되므로 Athena(OMOP) system URI 로 구분한다.
            return {"system": self.OMOP_SYSTEM, "code": str(cdm["concept_id"]),
                    "display": cdm.get("concept_name") or name,
                    "vocabulary_hint": (cdm.get("vocabulary") or "SNOMED").upper()}
        code = self.get_snomed_code(name if name else korean)
        if code:
            # 레거시 snomed 필드에는 CDM 에서 복사된 OMOP concept_id 가 섞여 있다.
            # CDM concept_id 집합에 있으면 SNOMED 가 아니라 OMOP 로 표기한다.
            system = self.OMOP_SYSTEM if str(code) in self._cdm_concept_ids() else "http://snomed.info/sct"
            return {"system": system, "code": str(code), "display": name or korean}
        return None

    def _cdm_concept_ids(self):
        ids = getattr(self, "_cdm_id_set", None)
        if ids is None:
            ids = {str(e.get("concept_id")) for e in (self.cdm_entries or []) if e.get("concept_id")}
            self._cdm_id_set = ids
        return ids
    
    def _load_database(self) -> AllergenDatabase:
        """알레르겐 매핑 데이터베이스 로드"""
        try:
            with open(self.mapping_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Pydantic 모델로 변환
            database = AllergenDatabase(**data)
            logger.info(f"알레르겐 데이터베이스 로드 완료: {len(database.entries)}개 항목")
            return database
        
        except FileNotFoundError:
            logger.error(f"매핑 파일을 찾을 수 없습니다: {self.mapping_file_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 오류: {e}")
            raise
        except Exception as e:
            logger.error(f"데이터베이스 로드 실패: {e}")
            raise
    
    def _build_lookup_tables(self):
        """빠른 검색을 위한 룩업 테이블 생성"""
        self.canonical_lookup = {}
        self.korean_lookup = {}
        self.alias_lookup = {}
        self.snomed_lookup = {}
        
        for entry in self.database.entries:
            # Canonical name 룩업
            self.canonical_lookup[entry.canonical_name.lower()] = entry
            
            # Korean name 룩업
            if entry.korean_name:
                self.korean_lookup[entry.korean_name] = entry
            
            # Aliases 룩업
            for alias in entry.aliases + entry.ocr_aliases:
                self.alias_lookup[alias.lower()] = entry
            
            # 학명 속명 약어 — 결과지는 'Alternaria alternata' 를 'A. alternata' 로 인쇄한다
            parts = entry.canonical_name.split()
            if len(parts) == 2 and parts[1].islower() and len(parts[0]) > 3:
                # 조회는 normalize_text 를 거치므로(마침표가 지워진다) 키도 같은 형태로 넣는다
                for form in (f"{parts[0][0]}. {parts[1]}", f"{parts[0][0]}.{parts[1]}"):
                    self.alias_lookup.setdefault(self.normalize_text(form).lower(), entry)

            # SNOMED 코드 룩업
            if entry.snomed:
                self.snomed_lookup[entry.snomed] = entry

    def _load_chinese_names(self):
        """중국어 항원명을 별칭 테이블에 합친다.

        중국 결과지는 항원명을 중국어로만 인쇄한다(户尘螨·猫毛皮屑·交链孢霉 …).
        레지스트리에는 영문·한글만 있어서, OCR 이 중국어를 정확히 읽어도 매핑이 전부
        실패하고 지식베이스 조회·감별 판정·SNOMED 코딩이 함께 무너졌다."""
        self.chinese_lookup = {}
        try:
            path = Path(__file__).resolve().parent.parent / "data" / "allergen_names_zh.json"
            if not path.exists():
                return
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"중국어 항원명 로드 실패: {e}")
            return

        added = 0
        for canonical, info in (data.get("map") or {}).items():
            entry = self.canonical_lookup.get(canonical.lower())
            if entry is None:
                continue
            for zh in (info.get("zh") or []):
                key = zh.strip()
                if not key:
                    continue
                self.chinese_lookup[key] = entry
                self.alias_lookup.setdefault(key.lower(), entry)
                added += 1
        logger.info(f"중국어 항원명 로드: {added}개 표기 / {len(data.get('map') or {})}종")
    
    def normalize_text(self, text: str) -> str:
        """텍스트 정규화"""
        # Global rules 적용
        rules = self.database.global_rules
        
        # 제거할 문자들
        for char in rules.get('strip_chars', []):
            text = text.replace(char, '')
        
        # 문자 정규화
        for old, new in rules.get('normalize', []):
            text = text.replace(old, new)
        
        # 노이즈 토큰 제거
        noise_tokens = rules.get('noise_tokens', [])
        words = text.split()
        words = [w for w in words if w.lower() not in [t.lower() for t in noise_tokens]]
        text = ' '.join(words)
        
        # 공백 정규화
        text = ' '.join(text.split())
        
        return text.strip()
    
    @lru_cache(maxsize=1000)
    def find_allergen(self, name: str) -> Optional[AllergenMapping]:
        """
        알레르겐 이름으로 매핑 정보 검색
        
        Args:
            name: 검색할 알레르겐 이름
            
        Returns:
            AllergenMapping 또는 None
        """
        # 특별 처리: 동물 털/깃털 매핑
        special_mappings = {
            'dog hair': 'dog',
            'cat hair': 'cat',
            'cow hair': 'cow',
            'horse hair': 'horse',
            'rabbit fur': 'rabbit',
            'sheep wool': 'sheep',
            'chicken feathers': 'chicken',
            'guinea pig hair': 'guinea pig',
            'hamster': 'hamster',
        }
        # 벌독(꿀벌·말벌 등)은 이 표에 없다. 예전에는 'honey bee' 를 'bee' 로 줄인 뒤 아래 부분 문자열
        # 매칭이 'Beech'(너도밤나무)에 붙여, 벌독이 수목 꽃가루로 분류됐다. 줄이지 않고 그대로 찾는다.
        
        # 인쇄된 그대로 맞는 이름이 있으면 그것이다. 정규화는 마침표와 노이즈 낱말(histamine·saline)을
        # 지우므로, 먼저 정규화하면 'Histamine control' 이 'control'(음성 대조)이 되고 'D.f.' 는 어디에도 안 맞는다.
        raw = " ".join((name or "").split())
        hit = (self.canonical_lookup.get(raw.lower()) or self.korean_lookup.get(raw)
               or self.alias_lookup.get(raw.lower()))
        if hit:
            return hit

        # 정규화 전에 특별 매핑 확인
        name_lower = name.lower().strip()
        for pattern, replacement in special_mappings.items():
            if pattern in name_lower:
                name = replacement
                logger.info(f"특별 매핑: {name_lower} -> {replacement}")
                break
        
        # 정규화
        normalized = self.normalize_text(name)
        normalized_lower = normalized.lower()
        # 노이즈 토큰만으로 된 이름('Histamine' 등)은 정규화하면 빈 문자열이 된다. 빈 문자열은
        # 모든 이름의 부분 문자열이라 첫 항목(점박이응애)에 붙었다. 원래 이름으로 정확 매칭만 본다.
        if not normalized:
            raw = name.strip()
            return (self.canonical_lookup.get(raw.lower()) or self.korean_lookup.get(raw)
                    or self.alias_lookup.get(raw.lower()))
        
        # 1. 정확한 매칭 시도
        if normalized_lower in self.canonical_lookup:
            return self.canonical_lookup[normalized_lower]
        
        # 2. 한국어 이름 매칭
        if normalized in self.korean_lookup:
            return self.korean_lookup[normalized]
        
        # 3. Alias 매칭
        if normalized_lower in self.alias_lookup:
            return self.alias_lookup[normalized_lower]
        
        # 4. 괄호 표기 분해 후 재시도
        #    실제 결과지는 항원명을 거의 항상 괄호와 함께 인쇄한다:
        #      'Birch (t3)'  'D. farinae(미국집먼지진드기)'  'Alder (오리나무)(T2)'  '户尘螨(尘螨)'
        #    괄호를 붙인 채로는 어느 표에도 없어서 한국어·영어·중국어 결과지 모두에서
        #    행의 3분의 1 가량이 매핑에 실패하고 있었다. 매핑이 실패하면 지식베이스·판정·
        #    SNOMED 코딩이 함께 빈다. 퍼지 매칭으로 넘기기 전에 조각으로 나눠 정확 매칭만
        #    다시 본다(퍼지로 넘기면 't3' 같은 코드가 엉뚱한 항원에 붙는다).
        for part in self._split_parenthetical(name):
            hit = self._find_exact(part)
            if hit:
                return hit

        # 일반 낱말만으로 된 이름('Mix'·'Pollen'·'혼합')은 정확 매칭까지만 본다. 부분 매칭으로 넘기면
        # 'Mix' 가 'Cockroach, Mix'(바퀴)에 붙는다.
        if is_generic_name(normalized):
            logger.warning(f"알레르겐 매핑 실패(일반 낱말뿐인 이름): {name}")
            return None

        # 5. 같은 항원의 다른 표기 — 수식어·띄어쓰기·낱말 순서만 다른 이름('Birch pollen' → Birch).
        #    예전에는 낱말이 이어서 들어 있기만 하면 붙여('Cow' → 'Cow milk', 'Rye' → 'Rye grass, perennial',
        #    'Egg' → 'Egg white'), 덜 구체적인 이름이 목록에서 먼저 나온 항원으로 풀렸다.
        hit = self._same_antigen(normalized_lower, allow_typo=False)
        if hit:
            return hit

        # 6. OCR 오타 구제 — 낱말 수가 같고, 5글자 이상 낱말에서 글자 하나만 다른 경우만
        best_match = self._fuzzy_match(normalized_lower)
        if best_match:
            return best_match
        
        logger.warning(f"알레르겐 매핑 실패: {name}")
        return None
    
    @staticmethod
    def _split_parenthetical(name: str) -> List[str]:
        """'Alder (오리나무)(T2)' → ['Alder', '오리나무', 'T2'] 처럼 조각으로 나눈다.
        괄호 밖 이름이 주 이름이므로 먼저 돌려준다."""
        import re as _re
        raw = (name or "").strip()
        if "(" not in raw and "（" not in raw:
            # 괄호 없이 'Fusarium 붉은점박이곰팡이' 처럼 영문·한글을 붙여 쓴 경우 — OCR 이 괄호를
            # 빠뜨리거나 한글명을 지어내 붙일 때 생긴다. 영문 쪽을 먼저 본다(한글명은 틀렸을 수 있다).
            latin = _re.sub(r"[가-힣]+", " ", raw)
            hangul = " ".join(_re.findall(r"[가-힣][가-힣\s]*[가-힣]|[가-힣]", raw))
            latin = " ".join(latin.split()).strip(" -·,")
            if latin and hangul and _re.search(r"[A-Za-z]", latin):
                return [p for p in (latin, hangul.strip()) if p]
            return []
        norm = raw.replace("（", "(").replace("）", ")")
        outside = _re.sub(r"\([^)]*\)", " ", norm).strip(" -·,")
        inside = [m.strip() for m in _re.findall(r"\(([^)]*)\)", norm)]
        parts, seen = [], set()
        for cand in [outside, *inside]:
            c = " ".join((cand or "").split())
            if c and c.lower() not in seen:
                seen.add(c.lower())
                parts.append(c)
        return parts

    def _find_exact(self, name: str) -> Optional[AllergenMapping]:
        """정확 매칭만 본다(정규명·한국어명·별칭). 부분·퍼지 매칭은 쓰지 않는다."""
        normalized = self.normalize_text(name)
        if not normalized:
            return None
        low = normalized.lower()
        return (self.canonical_lookup.get(low)
                or self.korean_lookup.get(normalized)
                or self.alias_lookup.get(low))

    def _same_antigen(self, text: str, allow_typo: bool) -> Optional[AllergenMapping]:
        """이름·한글명·별칭·OCR 변형 가운데 text 와 같은 항원인 것(same_antigen_name). 둘 이상의 항원에
        걸리면 어느 것인지 알 수 없으므로 None."""
        found = None
        for entry in self.database.entries:
            names = [entry.canonical_name, entry.korean_name] + entry.aliases + entry.ocr_aliases
            if any(n and same_antigen_name(text, n, allow_typo=allow_typo) for n in names):
                if found is not None and found is not entry:
                    return None
                found = entry
        return found

    def _fuzzy_match(self, text: str, threshold: float = 0.8) -> Optional[AllergenMapping]:
        """OCR 오타 구제. 낱말 수가 같고, 5글자 이상 낱말에서 글자 하나만 다를 때만 같은 항원으로 본다
        (same_antigen_name). threshold 는 예전 호출과의 호환을 위해 받기만 한다.

        예전에는 전체 문자열의 유사도(0.8)로 견줘, 낱말 하나가 통째로 다른 이름까지 붙였다:
        'Cattle epithelium' → 쥐 상피, 'Catfish' → 가재, 'Chickpea' → 닭, 'Maple' → 사과."""
        best_match = self._same_antigen(text, allow_typo=True)
        if best_match:
            logger.info(f"퍼지 매칭 성공: {text} -> {best_match.canonical_name}")
        return best_match
    
    def get_snomed_code(self, allergen_name: str) -> Optional[str]:
        """
        알레르겐 이름으로 SNOMED 코드 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            SNOMED 코드 또는 None
        """
        mapping = self.find_allergen(allergen_name)
        return mapping.snomed if mapping else None
    
    def get_korean_name(self, allergen_name: str) -> Optional[str]:
        """
        알레르겐의 한국어 이름 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            한국어 이름 또는 None
        """
        mapping = self.find_allergen(allergen_name)
        return mapping.korean_name if mapping else None
    
    def get_category(self, allergen_name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        알레르겐의 카테고리 정보 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            (category, subcategory) 튜플
        """
        mapping = self.find_allergen(allergen_name)
        if mapping:
            return mapping.category, mapping.subcategory
        return None, None
    
    def extract_allergen_from_text(self, text: str) -> List[AllergenMapping]:
        """
        텍스트에서 알레르겐 추출
        
        Args:
            text: 분석할 텍스트
            
        Returns:
            발견된 알레르겐 매핑 리스트
        """
        found_allergens = []
        text_lower = text.lower()
        
        # 모든 알레르겐 항목 검사
        for entry in self.database.entries:
            # Canonical name 검사
            if entry.canonical_name.lower() in text_lower:
                found_allergens.append(entry)
                continue
            
            # Korean name 검사
            if entry.korean_name and entry.korean_name in text:
                found_allergens.append(entry)
                continue
            
            # Aliases 검사
            for alias in entry.aliases:
                if alias.lower() in text_lower:
                    found_allergens.append(entry)
                    break
        
        # 중복 제거
        unique_allergens = []
        seen = set()
        for allergen in found_allergens:
            if allergen.canonical_name not in seen:
                unique_allergens.append(allergen)
                seen.add(allergen.canonical_name)
        
        return unique_allergens
    
    def process_ocr_result(self, allergen_name: str) -> Dict[str, Any]:
        """
        OCR 결과의 알레르겐 이름을 처리하고 매핑 정보 반환
        
        Args:
            allergen_name: OCR에서 추출된 알레르겐 이름
            
        Returns:
            매핑 정보 딕셔너리
        """
        mapping = self.find_allergen(allergen_name)
        
        if mapping:
            return {
                "original": allergen_name,
                "canonical_name": mapping.canonical_name,
                "korean_name": mapping.korean_name,
                "snomed_code": mapping.snomed,
                "category": mapping.category,
                "subcategory": mapping.subcategory,
                "matched": True
            }
        else:
            return {
                "original": allergen_name,
                "canonical_name": allergen_name,
                "korean_name": None,
                "snomed_code": None,
                "category": "Other",
                "subcategory": None,
                "matched": False
            }
    
    def get_statistics(self) -> Dict[str, Any]:
        """데이터베이스 통계 반환"""
        categories = {}
        for entry in self.database.entries:
            category = entry.category
            if category not in categories:
                categories[category] = 0
            categories[category] += 1
        
        return {
            "total_entries": len(self.database.entries),
            "categories": categories,
            "version": self.database.version,
            "locale": self.database.locale,
            "with_snomed": sum(1 for e in self.database.entries if e.snomed),
            "with_korean": sum(1 for e in self.database.entries if e.korean_name)
        }


# 싱글톤 인스턴스
_mapper_instance: Optional[AllergenMapper] = None


def get_allergen_mapper() -> AllergenMapper:
    """알레르겐 매퍼 싱글톤 인스턴스 반환"""
    global _mapper_instance
    if _mapper_instance is None:
        _mapper_instance = AllergenMapper()
    return _mapper_instance


# 편의 함수들
def find_allergen(name: str) -> Optional[AllergenMapping]:
    """알레르겐 검색 편의 함수"""
    return get_allergen_mapper().find_allergen(name)


def get_snomed_code(name: str) -> Optional[str]:
    """SNOMED 코드 조회 편의 함수"""
    return get_allergen_mapper().get_snomed_code(name)


def get_korean_name(name: str) -> Optional[str]:
    """한국어 이름 조회 편의 함수"""
    return get_allergen_mapper().get_korean_name(name)
