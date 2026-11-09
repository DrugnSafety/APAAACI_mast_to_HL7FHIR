"""Environment Control Service — 알레르겐 환경관리·회피요법의 구조화 지식(교과서 근거).

원본은 `data/allergen_environment_control.json` 이다. 한글 알레르기 교과서 '환경관리 및 회피요법' 장과
Middleton's Allergy 26·27·28·86장을 읽고 알레르겐군(집먼지진드기·고양이·개·바퀴·실내/실외 곰팡이·
꽃가루·설치류)별로 정리했다.

  - 조치(measure)마다 **단계(tier)** 와 **근거 등급(evidence_grade)** 과 **출처(sources)** 가 붙어 있다.
    단계는 한글 교과서 표 1-1 의 3단계 구분(필수적·경제적 / 도움 되나 비용 큼 / 효과 제한적·미증명)을
    모든 알레르겐군에 확장한 것이다.
  - 이 지식은 **알레르겐군의 일반 정보**다. 어떤 알레르겐이 이 환자에게 실제로 문제인지는 검사 결과와
    노출-증상 문진(relevance_service·questionnaire_service)이 정한다. 그래서 이 모듈은 판정 결과를 받아
    '실제 주의' 항원의 군에만 안내를 붙인다.
  - 약물에 관한 지시는 넣지 않는다(교과서에 있어도). 약은 진료에서 정한다.

공개 함수
  group_for(category, name, korean_name)  : 항원 → 알레르겐군 키(없으면 None)
  measures(group, tiers, limit)           : 단계 순으로 정렬된 조치
  tiered_tips(group, limit)               : 환자용 짧은 수칙(1단계 우선)
  summary(group)                          : 환자용 요약·근거 요약·입자·역치
  search_groups(text)                     : 질문 문구에서 관련 알레르겐군 찾기(챗봇·온톨로지)
  measures_matching(group, text, limit)   : 질문 키워드(공기청정기·세탁 …)에 걸리는 조치부터
  for_assessments(assessments)            : '실제 주의' 판정 항원들의 군별 안내 블록
  sources(ids)                            : 출처 상세
  factors_for_question(text)              : 질문에 악화 요인(미세먼지·황사·오존·담배·새집 …)이 있으면 그 키
  factor_summary(key)                     : 악화 요인 요약(성분별 역치·국내 자료·조치)
  mechanisms() / etiology_context()       : 대기오염 기전·발생 배경(최신판 5장) — 챗봇 설명·그래프용

v1.2(2026-10-13): 최신판 교과서 제1부 제5장 '알레르기질환과 환경'을 읽어 악화 요인(aggravating_factors)을
기후변화·실외 대기오염(성분별 components)·실내공기오염(VOC)·담배연기(전자담배)로 넓히고 mechanisms·etiology_context
를 더했다. 악화 요인은 알레르겐군과 달리 검사 결과로 판정되지 않으므로 리포트에는 붙지 않고, 질문에 그 말이
있을 때 챗봇과 온톨로지에서만 쓴다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from config.settings import BASE_DIR

logger = logging.getLogger(__name__)

DATA_PATH = BASE_DIR / "data" / "allergen_environment_control.json"
REVIEW_LOG_PATH = BASE_DIR / "data" / "environment_control_review_log.json"

# 조치의 검토 상태. candidate(기본) → approved / revised(검토자가 문장을 고침, 승인과 같이 취급) / rejected(환자 화면에서 뺌)
REVIEW_STATUSES = ("candidate", "approved", "revised", "rejected")
PATIENT_VISIBLE_WHEN_GATED = ("approved", "revised")
# 환자 화면 게이트. candidate_ok(기본): 검토 전 조치도 '검토 전' 표시와 함께 내보낸다(기존 임상 근거 데이터와 같은 정책).
# approved_only: 승인·수정된 조치만 내보낸다. 검토가 끝난 뒤 환경변수로 켠다.
PATIENT_GATE_ENV = "ENV_CONTROL_PATIENT_GATE"


def patient_gate() -> str:
    v = (os.getenv(PATIENT_GATE_ENV) or "candidate_ok").strip().lower()
    return v if v in ("candidate_ok", "approved_only") else "candidate_ok"

# 카테고리 → 기본 알레르겐군. 동물·곰팡이는 이름으로 더 좁힌다.
_CATEGORY_DEFAULT = {
    "mite": "mite",
    "insect": "cockroach",
    "mold": "mold_indoor",
    "pollen_tree": "pollen",
    "pollen_grass": "pollen",
    "pollen_weed": "pollen",
}
# 동물 종 판별(이름·한글명). 말·소처럼 데이터가 없는 종은 None 으로 두고 지식베이스 문장을 쓴다.
_ANIMAL_WORDS = {
    "cat": ["cat", "고양이", "fel d"],
    "dog": ["dog", "개 ", "개비듬", "개 비듬", "강아지", "can f"],
    "rodent": ["mouse", "mice", "rat ", "rat(", "rat$", "rodent", "guinea", "hamster", "rabbit", "gerbil",
               "쥐", "기니픽", "햄스터", "토끼", "설치류"],
}
# 실외 곰팡이로 분류하는 속(屬). 그 밖의 곰팡이는 실내 관리 안내를 쓴다(실내 조치가 더 넓게 적용된다).
_OUTDOOR_MOLD_WORDS = ["alternaria", "cladosporium", "hormodendrum", "helminthosporium", "fusarium",
                       "알테르나리아", "클라도스포리움"]

# 질문에서 '관리·회피 의도'로 보는 말. 알레르겐 이름만 나오는 질문("제 진드기 수치는?")은 이 환자의
# 보고서를 묻는 것이므로 일반지식을 붙이지 않는다.
MANAGEMENT_INTENT_WORDS = [
    "관리", "회피", "피하", "피할", "줄이", "줄일", "없애", "치우", "청소", "세탁", "빨", "커버", "침구", "이불",
    "매트리스", "카펫", "소파", "인형", "공기청정기", "청정기", "필터", "hepa", "헤파", "습도", "제습", "환기",
    "목욕", "씻", "키워", "키우", "기르", "내보내", "분리", "침실", "방제", "살충", "미끼", "덫", "박멸",
    "마스크", "고글", "창문", "외출", "예방", "어떻게", "방법", "도움", "효과", "괜찮", "되나요", "될까요",
    "해야", "수칙", "조치", "환경", "노출", "얼마나 오래", "남아", "사라지", "없어지", "곰팡이 제거", "락스",
    "avoid", "control", "clean", "wash", "cover", "encas", "filter", "purifier", "humid", "ventilat",
    "remove", "get rid", "keep", "should i", "can i", "how to", "how do", "measure", "reduce", "exposure",
    "避免", "清洁", "清洗", "空气净化", "湿度", "怎么", "如何", "措施", "养",
]
# 이 말이 있으면 환자 보고서의 값·판정을 묻는 질문으로 보고 일반지식을 붙이지 않는다.
REPORT_VALUE_WORDS = [
    "수치", "결과", "얼마였", "얼마나 나왔", "나왔", "등급", "class", "ku/l", "ku", "팽진", "mm", "양성이에요",
    "양성인가요", "음성", "점수", "제 결과", "내 결과", "my result", "my level", "my value", "what was my",
    "数值", "结果",
]


# 질문에 이 말이 있으면 해당 악화 요인(aggravating_factors 키)의 일반 지식을 붙인다. 알레르겐군과 달리
# 검사 결과로 판정되는 것이 아니므로 환자의 항원군으로 좁히지 않고 질문의 낱말만 본다.
FACTOR_WORDS = {
    "outdoor_air_pollution": ["미세먼지", "초미세", "황사", "오존", "대기오염", "공기오염", "공기질", "이산화질소", "이산화황",
                              "스모그", "매연", "배기가스", "디젤", "pm10", "pm2.5", "pm2_5", "air pollution", "ozone",
                              "dust storm", "yellow dust", "fine dust", "particulate", "雾霾", "沙尘", "臭氧", "空气污染"],
    "tobacco_smoke": ["담배", "흡연", "전자담배", "연초", "니코틴", "smok", "cigarette", "vap", "e-cig", "吸烟", "香烟", "电子烟"],
    "indoor_combustion": ["가스레인지", "가스렌지", "난로", "장작", "석유난로", "연탄", "가스불", "stove", "wood burning", "kerosene"],
    "indoor_air_pollution": ["새집", "새 집", "신축", "새 가구", "새가구", "도배", "페인트", "폼알데하이드", "포름알데히드",
                             "휘발성", "voc", "접착제", "유기용매", "리모델링", "인테리어", "실내공기", "실내 공기",
                             "formaldehyde", "new house", "renovat", "甲醛", "装修"],
    "climate_change": ["기후변화", "기후 변화", "지구온난화", "온난화", "폭염", "climate", "warming", "气候"],
}

# 질문의 입말 → 조치 문장에 쓰인 낱말. 접두어로 맞춘다('빨아야'·'빨면' → 세탁).
_QUERY_SYNONYMS = {
    "빨아": "세탁", "빨면": "세탁", "빨래": "세탁", "빨까": "세탁", "나가": "외출", "나갈": "외출", "외출": "외출",
    "강아지": "개", "냥이": "고양이", "청정기": "공기청정기", "제습": "습도", "환풍": "환기", "청소기": "청소",
    "천둥": "뇌우", "번개": "뇌우", "소나기": "뇌우", "이불": "침구", "베개": "침구", "매트": "매트리스",
    "wash": "목욕", "vacuum": "청소", "filter": "공기청정기", "purifier": "공기청정기", "humid": "습도",
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


class EnvironmentControlService:
    def __init__(self, path: Optional[Path] = None, log_path: Optional[Path] = None):
        self.path = Path(path or DATA_PATH)
        self.log_path = Path(log_path or REVIEW_LOG_PATH)
        self._data: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None

    # ------------------------------------------------------------------
    # 적재
    # ------------------------------------------------------------------
    @property
    def data(self) -> Dict[str, Any]:
        if self._data is None and self._error is None:
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                self._error = f"환경관리 데이터를 읽지 못함({self.path.name}): {e}"
                logger.warning(self._error)
                self._data = {}
        return self._data or {}

    @property
    def available(self) -> bool:
        return bool(self.data.get("allergen_groups"))

    @property
    def groups(self) -> Dict[str, Any]:
        return self.data.get("allergen_groups") or {}

    @property
    def review_status(self) -> str:
        return self.data.get("review_status") or ""

    def tier_label(self, tier: str) -> str:
        return ((self.data.get("tiers") or {}).get(tier) or {}).get("label_ko") or tier

    def tier_order(self, tier: str) -> int:
        return int(((self.data.get("tiers") or {}).get(tier) or {}).get("order") or 9)

    def grade_label(self, grade: str) -> str:
        return (self.data.get("evidence_grades") or {}).get(grade) or grade

    # ------------------------------------------------------------------
    # 항원 → 알레르겐군
    # ------------------------------------------------------------------
    def group_for(self, category: Optional[str], name: str = "", korean_name: str = "") -> Optional[str]:
        """항원의 카테고리·이름으로 알레르겐군 키를 고른다. 자료가 없는 종(말·소·벌독·음식 …)은 None."""
        if not self.available:
            return None
        from services.knowledge_service import normalize_category
        cat = normalize_category(category)
        text = _norm(f"{name} {korean_name}")
        # 레지스트리 이름이 어느 군의 registry_names 와 정확히 같으면 그 군
        for key, g in self.groups.items():
            for rn in g.get("registry_names") or []:
                if rn and _norm(rn) == _norm(name or ""):
                    return key
        if cat == "animal":
            for key, words in _ANIMAL_WORDS.items():
                if key in self.groups and any(w.strip() and w.strip() in text for w in words):
                    return key
            return None
        if cat == "mold":
            if any(w in text for w in _OUTDOOR_MOLD_WORDS) and "mold_outdoor" in self.groups:
                return "mold_outdoor"
            return "mold_indoor" if "mold_indoor" in self.groups else None
        key = _CATEGORY_DEFAULT.get(cat)
        return key if key in self.groups else None

    def group(self, key: str) -> Dict[str, Any]:
        return self.groups.get(key) or {}

    # ------------------------------------------------------------------
    # 조치
    # ------------------------------------------------------------------
    @staticmethod
    def measure_status(m: Dict[str, Any]) -> str:
        return ((m.get("review") or {}).get("status") or "candidate")

    def _owner_measures(self, key: str) -> List[Dict[str, Any]]:
        """알레르겐군 또는 악화 요인의 조치 원본 목록(쓰기용 참조)."""
        g = self.groups.get(key)
        if g is None:
            g = (self.data.get("aggravating_factors") or {}).get(key)
        return (g or {}).get("measures") or []

    def _decorate(self, key: str, m: Dict[str, Any]) -> Dict[str, Any]:
        g = self.groups.get(key) or (self.data.get("aggravating_factors") or {}).get(key) or {}
        rev = m.get("review") or {}
        item = dict(m)
        # 검토자가 문장을 고쳤으면(revised) 그 문장을 쓴다. 원문은 review.original_* 에 남아 있다.
        if rev.get("status") == "revised":
            if rev.get("text_ko"):
                item["text_ko"] = rev["text_ko"]
            if rev.get("short_ko"):
                item["short_ko"] = rev["short_ko"]
        item["group"] = key
        item["group_label_ko"] = g.get("label_ko", key)
        item["tier_ko"] = self.tier_label(m.get("tier", ""))
        item["evidence_grade_ko"] = self.grade_label(m.get("evidence_grade", ""))
        # 판 추적(2026-10-13): 이전 판 한글 교과서에만 기대는 조치는 최신판 미수록으로 표시된다
        item["edition_support"] = m.get("edition_support", "")
        item["edition_support_ko"] = self.edition_label(m.get("edition_support", ""))
        item["citations"] = [self._citation(s) for s in m.get("sources") or []]
        item["review_status"] = self.measure_status(m)
        item["review"] = dict(rev)
        return item

    def measures(self, key: str, tiers: Optional[Iterable[str]] = None,
                 limit: Optional[int] = None, include_rejected: bool = False,
                 patient_facing: bool = False) -> List[Dict[str, Any]]:
        """단계(필수 → 도움 → 제한적 → 비권장) 순으로 정렬한 조치. 각 항목에 단계·근거 등급의 한글 라벨을 붙인다.

        rejected 조치는 기본적으로 뺀다. `patient_facing=True` 이고 게이트가 approved_only 면 승인·수정된 조치만 남는다.
        """
        want = set(tiers) if tiers else None
        gated = patient_facing and patient_gate() == "approved_only"
        out = []
        for m in self._owner_measures(key):
            status = self.measure_status(m)
            if want and m.get("tier") not in want:
                continue
            if status == "rejected" and not include_rejected:
                continue
            if gated and status not in PATIENT_VISIBLE_WHEN_GATED:
                continue
            out.append(self._decorate(key, m))
        out.sort(key=lambda m: self.tier_order(m.get("tier", "")))
        return out[:limit] if limit else out

    def tiered_tips(self, key: str, limit: int = 5) -> List[str]:
        """환자용 짧은 수칙. 1단계(필수)부터, 권하지 않는 조치는 뺀다."""
        tips = [m["short_ko"] for m in self.measures(key, tiers=("essential", "helpful"), patient_facing=True)
                if m.get("short_ko")]
        return tips[:limit]

    def measures_matching(self, key: str, text: str, limit: int = 4,
                          patient_facing: bool = True) -> List[Dict[str, Any]]:
        """질문 문구에 걸리는 조치부터 돌려준다(예: '공기청정기' → mite_air_cleaner). 걸리는 게 없으면 1단계."""
        q = _norm(text)
        all_m = self.measures(key, patient_facing=patient_facing)
        if not q:
            return all_m[:limit]
        hays = [_norm(" ".join([m.get("short_ko", ""), m.get("text_ko", ""), m.get("id", "")])) for m in all_m]
        words = re.findall(r"[가-힣a-z0-9]{2,}", q)
        # 입말 → 조치 문장의 낱말('빨아야' → 세탁, '나가도' → 외출). 접두어가 맞으면 동의어를 덧붙인다.
        for w in list(words):
            for k, v in _QUERY_SYNONYMS.items():
                if w.startswith(k) and v not in words:
                    words.append(v)

        def best_prefix(word: str, hay: str) -> str:
            # 한국어는 조사가 붙으므로('목욕시키면'·'공기청정기가') 앞에서부터 가장 긴 접두어로 맞춘다
            for n in range(len(word), 1, -1):
                if word[:n] in hay:
                    return word[:n]
            return ""

        # 거의 모든 조치에 나오는 말('고양이')보다 한두 조치에만 나오는 말('목욕')이 질문의 핵심이다 — 희소성 가중
        import math
        scored = []
        for m, hay in zip(all_m, hays):
            score = 0.0
            for w in words:
                pre = best_prefix(w, hay)
                if not pre:
                    continue
                df = sum(1 for h in hays if pre in h)
                score += len(pre) * (1.0 + math.log(len(hays) / max(1, df)))
            scored.append((score, m))
        hits = [m for s, m in sorted(scored, key=lambda x: -x[0]) if s > 0]
        if hits:
            return hits[:limit]
        return [m for m in all_m if m.get("tier") == "essential"][:limit]

    # ------------------------------------------------------------------
    # 요약·출처
    # ------------------------------------------------------------------
    def summary(self, key: str) -> Dict[str, Any]:
        g = self.group(key)
        if not g:
            return {}
        return {
            "group": key,
            "label_ko": g.get("label_ko"),
            "label_en": g.get("label_en"),
            "categories": g.get("categories") or [],
            "diseases": g.get("diseases") or [],
            "patient_summary_ko": g.get("patient_summary_ko", ""),
            "evidence_summary_ko": g.get("evidence_summary_ko", ""),
            "carrier_particle": g.get("carrier_particle") or {},
            "thresholds": g.get("thresholds") or [],
            "reservoirs_ko": g.get("reservoirs_ko") or [],
            "persistence_ko": g.get("persistence_ko", ""),
            "korea_ko": g.get("korea_ko") or [],
            "cross_reactivity": g.get("cross_reactivity") or [],
            "major_allergens": g.get("major_allergens") or [],
            "korea_calendar": g.get("korea_calendar") or {},
            "measures_total": len(g.get("measures") or []),
            "review_status": self.review_status,
            "review_counts": self._counts(g.get("measures") or []),
            "review_note": g.get("review_note", ""),
            "patient_gate": patient_gate(),
        }

    # ------------------------------------------------------------------
    # 검토·승인
    # ------------------------------------------------------------------
    def _counts(self, measures: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        c = {s: 0 for s in REVIEW_STATUSES}
        for m in measures:
            c[self.measure_status(m)] = c.get(self.measure_status(m), 0) + 1
        return c

    def edition_label(self, tag: str) -> str:
        return ((self.data.get("edition_tracking") or {}).get("labels_ko") or {}).get(tag, tag)

    def edition_tracking(self) -> Dict[str, Any]:
        """최신판 대조 결과: 조치별 edition_support 집계와 이전 판에만 근거한 조치 목록."""
        return dict(self.data.get("edition_tracking") or {})

    def review_stats(self) -> Dict[str, Any]:
        per = {k: self._counts(g.get("measures") or []) for k, g in self.groups.items()}
        per.update({k: self._counts(f.get("measures") or [])
                    for k, f in (self.data.get("aggravating_factors") or {}).items()})
        total = {s: sum(c[s] for c in per.values()) for s in REVIEW_STATUSES}
        return {"per_owner": per, "total": total, "patient_gate": patient_gate()}

    def find_measure(self, measure_id: str) -> Optional[Dict[str, Any]]:
        """조치 id 로 (소유자 키, 원본 dict) 를 찾는다. 알레르겐군과 악화 요인 모두 본다."""
        owners = list(self.groups.items()) + list((self.data.get("aggravating_factors") or {}).items())
        for key, owner in owners:
            for m in owner.get("measures") or []:
                if m.get("id") == measure_id:
                    return {"owner": key, "measure": m}
        return None

    def set_review(self, measure_id: str, status: str, by: str = "", note: str = "",
                   text_ko: Optional[str] = None, short_ko: Optional[str] = None) -> Dict[str, Any]:
        """조치 하나의 검토 상태를 바꾼다(저장은 save()). revised 는 바뀐 문장을 review 에 둔다."""
        if status not in REVIEW_STATUSES:
            raise ValueError(f"알 수 없는 검토 상태: {status}")
        hit = self.find_measure(measure_id)
        if not hit:
            raise KeyError(f"조치를 찾을 수 없음: {measure_id}")
        m = hit["measure"]
        rev = dict(m.get("review") or {})
        rev.update({"status": status, "reviewed_by": by or rev.get("reviewed_by", ""),
                    "reviewed_on": datetime.now(timezone.utc).date().isoformat()})
        if note:
            rev["note"] = note
        if status == "revised":
            rev.setdefault("original_text_ko", m.get("text_ko", ""))
            rev.setdefault("original_short_ko", m.get("short_ko", ""))
            if text_ko:
                rev["text_ko"] = text_ko
            if short_ko:
                rev["short_ko"] = short_ko
            if not rev.get("text_ko") and not rev.get("short_ko"):
                raise ValueError("revised 에는 바뀐 text_ko 또는 short_ko 가 필요합니다")
        m["review"] = rev
        self._log({"event": status, "measure": measure_id, "owner": hit["owner"], "by": by, "note": note})
        return rev

    def set_group_note(self, key: str, note: str, by: str = "") -> None:
        owner = self.groups.get(key) or (self.data.get("aggravating_factors") or {}).get(key)
        if owner is None:
            raise KeyError(f"알 수 없는 군: {key}")
        owner["review_note"] = note
        self._log({"event": "group_note", "owner": key, "by": by, "note": note})

    def import_review_export(self, export: Dict[str, Any], by: str = "") -> Dict[str, Any]:
        """검토 HTML 의 '검토 결과 복사(JSON)' 출력을 반영한다.

        키 규칙(docs/review_*.html 과 같다): m:<조치id>·f:<조치id> 체크 → approved,
        note:<군> → 군 검토 메모, 나머지(note:profile:·note:kb:·note:disagreements·note:design·
        note:next, dis:·dd:·next: 체크)는 로그에만 남기고 사람이 처리하도록 돌려준다.
        """
        applied, unknown, manual = [], [], {}
        for key in export.get("checked") or []:
            if key.startswith(("m:", "f:")):
                mid = key.split(":", 1)[1]
                if self.find_measure(mid):
                    self.set_review(mid, "approved", by=by, note=(export.get("notes") or {}).get(key, ""))
                    applied.append(mid)
                else:
                    unknown.append(key)
            else:
                manual.setdefault("checked", []).append(key)
        for key, text in (export.get("notes") or {}).items():
            if not (text or "").strip():
                continue
            if key.startswith("note:") and key.split(":", 1)[1] in self.groups:
                self.set_group_note(key.split(":", 1)[1], text.strip(), by=by)
                applied.append(key)
            elif key.startswith("note:") and key.split(":", 1)[1] in (self.data.get("aggravating_factors") or {}):
                self.set_group_note(key.split(":", 1)[1], text.strip(), by=by)
                applied.append(key)
            else:
                manual.setdefault("notes", {})[key] = text.strip()
        self._log({"event": "import", "by": by, "applied": len(applied), "manual": manual, "unknown": unknown})
        return {"applied": applied, "unknown": unknown, "manual": manual}

    def _log(self, event: Dict[str, Any]) -> None:
        event = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **event}
        try:
            log = json.loads(self.log_path.read_text(encoding="utf-8")) if self.log_path.exists() else {"events": []}
        except Exception:  # noqa: BLE001
            log = {"events": []}
        log.setdefault("events", []).append(event)
        self.log_path.write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")

    def save(self) -> None:
        d = self.data
        c = self.review_stats()["total"]
        d["review_summary"] = {"updated_on": datetime.now(timezone.utc).date().isoformat(), **c}
        self.path.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

    def _citation(self, source_id: str) -> Dict[str, Any]:
        s = (self.data.get("sources") or {}).get(source_id) or {}
        return {"id": source_id, "citation": s.get("citation", source_id), "type": s.get("type", ""),
                "pages": s.get("pages", ""), "verified": s.get("verified", ""),
                "pmid": s.get("pmid", ""), "doi": s.get("doi", "")}

    def sources(self, ids: Iterable[str]) -> List[Dict[str, Any]]:
        seen, out = set(), []
        for i in ids:
            if i and i not in seen:
                seen.add(i)
                out.append(self._citation(i))
        return out

    def principles(self) -> List[Dict[str, Any]]:
        return list(self.data.get("principles") or [])

    def aggravating_factors(self) -> Dict[str, Any]:
        return dict(self.data.get("aggravating_factors") or {})

    def primary_prevention(self) -> Dict[str, Any]:
        return dict(self.data.get("primary_prevention") or {})

    def mechanisms(self) -> List[Dict[str, Any]]:
        """대기오염 → 알레르기질환 기전(최신판 5장). 환자 문장에는 patient_ko 만 쓴다."""
        return list((self.data.get("mechanisms") or {}).get("items") or [])

    def etiology_context(self) -> List[Dict[str, Any]]:
        """알레르기 발생의 환경 배경(위생가설·미생물총·유전-환경). 지시가 아니라 설명이다."""
        return list((self.data.get("etiology_context") or {}).get("items") or [])

    # ------------------------------------------------------------------
    # 악화 요인(대기오염·담배·실내공기·기후) — 질문의 낱말로만 고른다
    # ------------------------------------------------------------------
    def factors_for_question(self, text: str, max_factors: int = 2) -> List[str]:
        """질문에 악화 요인의 말(미세먼지·황사·담배·새집 …)이 있으면 그 키를 돌려준다.

        알레르겐군과 같은 게이트를 쓴다: 관리·회피 의도가 없거나 보고서 값을 묻는 질문에는 비운다.
        악화 요인은 검사로 판정되는 것이 아니므로 '이 환자의 항원군'으로 좁히지 않는다.
        """
        if not self.available or self.asks_report_value(text):
            return []
        q = _norm(text)
        if not q:
            return []
        factors = self.data.get("aggravating_factors") or {}
        hits = [key for key, words in FACTOR_WORDS.items()
                if key in factors and any(w in q for w in words)]
        # 요인 이름이 있으면 관리 의도가 약해도 붙인다('미세먼지가 천식에 어떤 영향이 있나요?'). 단, 알레르겐 이름만
        # 있는 질문과 달리 요인 이름 자체가 일반 지식 질문이므로 의도어가 없어도 허용한다.
        return hits[:max_factors]

    def factor_summary(self, key: str, question: str = "", limit: int = 4) -> Dict[str, Any]:
        """챗봇·그래프용 악화 요인 요약: 설명·성분(역치·근거)·국내 자료·단계순 조치·출처."""
        f = (self.data.get("aggravating_factors") or {}).get(key)
        if not f:
            return {}
        measures = (self.measures_matching(key, question, limit=limit) if question
                    else self.measures(key, limit=limit, patient_facing=True))
        comps = []
        for ck, c in (f.get("components") or {}).items():
            comps.append({"key": ck, "label_ko": c.get("label_ko", ck), "text_ko": c.get("text_ko", ""),
                          "thresholds": c.get("thresholds") or [], "evidence_ko": c.get("evidence_ko") or [],
                          "korea_ko": c.get("korea_ko") or [], "sources": c.get("sources") or []})
        src_ids = list(f.get("sources") or []) + [s for m in measures for s in (m.get("sources") or [])]
        return {"factor": key, "label_ko": f.get("label_ko", key), "text_ko": f.get("text_ko", ""),
                "ecigarette_ko": f.get("ecigarette_ko", ""), "components": comps,
                "korea_ko": f.get("korea_ko") or [], "measures": measures,
                "measures_total": len(f.get("measures") or []),
                "review_status": self.review_status, "review_counts": self._counts(f.get("measures") or []),
                "sources": self.sources(src_ids), "patient_gate": patient_gate()}

    # ------------------------------------------------------------------
    # 질문 → 알레르겐군 (챗봇·온톨로지 검색)
    # ------------------------------------------------------------------
    def search_groups(self, text: str) -> List[str]:
        """문구에 알레르겐군의 이름·별칭이 들어 있으면 그 군을 돌려준다(의도 판단은 하지 않는다)."""
        q = _norm(text)
        if not q:
            return []
        hits = []
        for key, g in self.groups.items():
            names = [g.get("label_ko", ""), g.get("label_en", "")] + list(g.get("aliases_ko") or []) + \
                    list(g.get("aliases_en") or [])
            if any(n and _norm(n) in q for n in names):
                hits.append(key)
        return hits

    @staticmethod
    def has_management_intent(text: str) -> bool:
        q = _norm(text)
        return any(w in q for w in MANAGEMENT_INTENT_WORDS)

    @staticmethod
    def asks_report_value(text: str) -> bool:
        q = _norm(text)
        return any(w in q for w in REPORT_VALUE_WORDS)

    def groups_for_question(self, text: str, patient_groups: Optional[Iterable[str]] = None,
                            max_groups: int = 2) -> List[str]:
        """질문에 **관리·회피 의도**가 있을 때만 알레르겐군을 고른다.

        '제 집먼지진드기 수치가 얼마였죠?' 는 알레르겐 이름이 있어도 이 환자의 보고서를 묻는 질문이므로
        비운다. 이름 없이 의도만 있으면('공기청정기가 도움이 되나요?') 환자의 '실제 주의' 항원군으로 좁힌다.
        """
        if not self.available or not self.has_management_intent(text) or self.asks_report_value(text):
            return []
        hits = self.search_groups(text)
        if not hits:
            hits = [g for g in (patient_groups or []) if g in self.groups]
        return hits[:max_groups]

    # ------------------------------------------------------------------
    # 판정 결과 → 군별 안내
    # ------------------------------------------------------------------
    def groups_for_assessments(self, assessments, relevant_only: bool = True) -> List[str]:
        """판정 목록에서 알레르겐군 키를 모은다(기본: '실제 주의' 항원만)."""
        keys: List[str] = []
        for a in assessments or []:
            rel = getattr(a, "relevance", None)
            rel = getattr(rel, "value", rel)
            if relevant_only and rel != "clinically_relevant":
                continue
            key = self.group_for(getattr(a, "category", None), getattr(a, "allergen_name", "") or "",
                                 getattr(a, "korean_name", "") or "")
            if key and key not in keys:
                keys.append(key)
        return keys

    def for_assessments(self, assessments, limit_per_group: int = 5) -> List[Dict[str, Any]]:
        """리포트·카드뉴스용: '실제 주의' 항원의 군마다 환자 요약 + 단계별 조치 + 출처."""
        out = []
        for key in self.groups_for_assessments(assessments):
            s = self.summary(key)
            ms = self.measures(key, tiers=("essential", "helpful"), limit=limit_per_group, patient_facing=True)
            src_ids = [sid for m in ms for sid in (m.get("sources") or [])]
            out.append({**s, "measures": ms, "sources": self.sources(src_ids)})
        return out


_singleton: Optional[EnvironmentControlService] = None


def get_environment_control_service() -> EnvironmentControlService:
    global _singleton
    if _singleton is None:
        _singleton = EnvironmentControlService()
    return _singleton
