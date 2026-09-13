"""
Clinical Group Service — 동종 항원 임상 그룹핑 (데이터 주도)

Df/Dp(유럽·미국 집먼지진드기)처럼 **임상적으로 구분해 설명·관리할 실익이 없는** 항원을
하나의 그룹으로 묶는다. 목적:
  - 문진: 그룹당 교차반응 질문 1회만(같은 질문 2번 반복 방지)
  - 리포트/카드뉴스: 중복 서술 대신 "집먼지진드기(유럽·미국)" 처럼 통합 설명

규칙은 data/clinical_groups.json 에서 관리(add-to-list). 그룹이 없으면 항원 자체가 단독 그룹.
"""
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
_PATH = Path(__file__).resolve().parent.parent / "data" / "clinical_groups.json"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


class ClinicalGroupService:
    def __init__(self):
        self.groups: Dict[str, Dict[str, Any]] = {}
        self._member_index: Dict[str, str] = {}   # 정규화 항원명 → group id
        try:
            data = json.loads(_PATH.read_text(encoding="utf-8"))
            for g in data.get("groups", []):
                self.groups[g["id"]] = g
                for m in g.get("members", []):
                    self._member_index[_norm(m)] = g["id"]
            logger.info(f"임상 그룹 로드: {len(self.groups)}개")
        except Exception as e:
            logger.warning(f"임상 그룹 로드 실패(그룹핑 비활성): {e}")

    def group_id(self, name: str, korean: str = "") -> Optional[str]:
        for cand in (name, korean):
            gid = self._member_index.get(_norm(cand))
            if gid:
                return gid
        return None

    def group_of(self, a) -> Optional[Dict[str, Any]]:
        gid = self.group_id(getattr(a, "allergen_name", "") or "",
                            getattr(a, "korean_name", "") or "")
        return self.groups.get(gid) if gid else None

    def key_of(self, a) -> str:
        """그룹이 있으면 group id, 없으면 항원 고유키(단독 그룹)."""
        gid = self.group_id(getattr(a, "allergen_name", "") or "",
                            getattr(a, "korean_name", "") or "")
        return gid or _norm(getattr(a, "allergen_name", "") or getattr(a, "korean_name", "") or "")

    def label_of(self, a, detail: bool = False) -> str:
        """표시용 이름. 그룹이면 통합 라벨(detail=True 면 '집먼지진드기(유럽·미국 두 종)')."""
        g = self.group_of(a)
        if g:
            return (g.get("detail_ko") if detail else g.get("label_ko")) or g.get("label_ko") or ""
        return getattr(a, "korean_name", None) or getattr(a, "allergen_name", "") or ""

    def note_of(self, a) -> str:
        g = self.group_of(a)
        return (g or {}).get("note_ko", "")

    def collapse(self, assessments) -> List[Dict[str, Any]]:
        """assessment 리스트를 임상 그룹 단위로 접는다.
        반환: [{key, label, detail_label, note, members:[(idx, assessment)], lead: assessment}]
        (lead = 그룹 대표 = 가장 먼저 등장한 항원)"""
        out: List[Dict[str, Any]] = []
        by_key: Dict[str, Dict[str, Any]] = {}
        for i, a in enumerate(assessments):
            k = self.key_of(a)
            if k not in by_key:
                entry = {
                    "key": k,
                    "label": self.label_of(a),
                    "detail_label": self.label_of(a, detail=True),
                    "note": self.note_of(a),
                    "grouped": self.group_of(a) is not None,
                    "members": [],
                    "lead": a,
                }
                by_key[k] = entry
                out.append(entry)
            by_key[k]["members"].append((i, a))
        return out

    def has_data(self) -> bool:
        return bool(self.groups)


_svc: Optional[ClinicalGroupService] = None


def get_clinical_group_service() -> ClinicalGroupService:
    global _svc
    if _svc is None:
        _svc = ClinicalGroupService()
    return _svc
