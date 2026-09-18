#!/usr/bin/env python3
"""LLM 이 만든 항원 소개문 검토 — 승인해야 환자 화면에 나간다.

`scripts/build_allergen_knowledge.py --llm` 이 만든 `biology_ko` 는 `biology_ko_status: candidate`
로 저장된다. KnowledgeService 는 기본적으로 candidate 필드를 걷어내고 내보내므로, 승인 전에는
환자에게 보이지 않는다. 이 스크립트로 검토·승인한다.

사용법:
    python scripts/review_allergen_knowledge.py --list              # 검토 대기 목록
    python scripts/review_allergen_knowledge.py --show Walnut       # 한 건 보기
    python scripts/review_allergen_knowledge.py --approve Walnut Pecan
    python scripts/review_allergen_knowledge.py --approve-all       # 전체 승인(검토 후에만)
    python scripts/review_allergen_knowledge.py --reject Latex      # 문장 삭제(템플릿만 남음)
    python scripts/review_allergen_knowledge.py --edit Walnut --text "새 문장"
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PATH = BASE / "data" / "allergen_knowledge_generated.json"
FIELD = "biology_ko"


def load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def save(d):
    d["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def find(entries, name: str):
    key = name.strip().lower()
    for e in entries:
        if key in {(e.get("canonical_name") or "").lower(), (e.get("korean_name") or "").lower()}:
            return e
    return None


def status(e) -> str:
    return e.get(f"{FIELD}_status") or ("approved" if e.get(FIELD) else "none")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", metavar="NAME")
    ap.add_argument("--approve", nargs="+", metavar="NAME")
    ap.add_argument("--approve-all", action="store_true")
    ap.add_argument("--reject", nargs="+", metavar="NAME")
    ap.add_argument("--edit", metavar="NAME")
    ap.add_argument("--text", metavar="TEXT")
    args = ap.parse_args()

    d = load()
    entries = d["entries"]
    pending = [e for e in entries if status(e) == "candidate"]

    if args.list or not any([args.show, args.approve, args.approve_all, args.reject, args.edit]):
        print(f"검토 대기 {len(pending)}건 / 전체 {len(entries)}종\n")
        for e in pending:
            print(f"  [{e.get('category'):12}] {e.get('korean_name'):16} {e.get('canonical_name'):24}")
            print(f"      {e.get(FIELD)}")
        if pending:
            print("\n승인: --approve <이름> … / --approve-all   |   삭제: --reject <이름>")
        return

    if args.show:
        e = find(entries, args.show)
        if not e:
            print("없음:", args.show)
            return
        print(json.dumps(e, ensure_ascii=False, indent=1))
        return

    changed = 0
    if args.edit:
        e = find(entries, args.edit)
        if not e or not args.text:
            print("--edit 에는 존재하는 이름과 --text 가 필요합니다.")
            return
        e[FIELD] = args.text.strip()
        e[f"{FIELD}_status"] = "approved"
        e[f"{FIELD}_reviewed_by"] = "human_edit"
        changed = 1

    for name in (args.approve or []):
        e = find(entries, name)
        if not e:
            print("  없음:", name)
            continue
        if not e.get(FIELD):
            print("  문장 없음:", name)
            continue
        e[f"{FIELD}_status"] = "approved"
        e[f"{FIELD}_reviewed_by"] = "human"
        changed += 1

    if args.approve_all:
        for e in pending:
            e[f"{FIELD}_status"] = "approved"
            e[f"{FIELD}_reviewed_by"] = "human_bulk"
        changed += len(pending)

    for name in (args.reject or []):
        e = find(entries, name)
        if not e:
            print("  없음:", name)
            continue
        for k in (FIELD, f"{FIELD}_status", f"{FIELD}_model", f"{FIELD}_generated_at"):
            e.pop(k, None)
        changed += 1

    if changed:
        save(d)
        left = sum(1 for e in entries if status(e) == "candidate")
        print(f"{changed}건 반영. 검토 대기 {left}건 남음 → {PATH.relative_to(BASE)}")
    else:
        print("변경 없음")


if __name__ == "__main__":
    main()
