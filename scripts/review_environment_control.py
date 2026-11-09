#!/usr/bin/env python3
"""알레르겐 환경관리 조치(data/allergen_environment_control.json)의 검토·승인.

조치마다 `review.status` 를 둔다: candidate(기본) → approved / revised(문장을 고쳐 승인) / rejected(환자 화면에서 뺌).
기본 게이트(candidate_ok)에서는 검토 전 조치도 '검토 전' 표시와 함께 나가고, 환경변수
`ENV_CONTROL_PATIENT_GATE=approved_only` 를 켜면 승인·수정된 조치만 환자 화면(리포트·카드뉴스·챗봇)에 나간다.
rejected 는 게이트와 무관하게 항상 빠진다. 모든 변경은 data/environment_control_review_log.json 에 남는다.

사용법:
    python scripts/review_environment_control.py --stats
    python scripts/review_environment_control.py --list [--group mite] [--status candidate]
    python scripts/review_environment_control.py --show mite_encasing
    python scripts/review_environment_control.py --approve mite_encasing mite_hot_wash --by "홍길동"
    python scripts/review_environment_control.py --reject cat_tannic_acid --by "홍길동" --note "국내 유통 없음"
    python scripts/review_environment_control.py --revise mite_humidity --text "새 문장" [--short "짧은 수칙"] --by "홍길동"
    python scripts/review_environment_control.py --approve-group mite --by "홍길동"
    python scripts/review_environment_control.py --import review.json --by "홍길동"
        # 검토 HTML(docs/review_*.html)의 '검토 결과 복사(JSON)' 를 파일로 저장해 넘긴다.
        # 체크한 조치(m:/f:)는 승인, 군 메모(note:<군>)는 군의 review_note 로, 나머지 메모·체크는 로그와 화면에만.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from services.environment_control_service import (REVIEW_STATUSES, EnvironmentControlService,  # noqa: E402
                                                  patient_gate)


def _owners(svc):
    items = [(k, g, "군") for k, g in svc.groups.items()]
    items += [(k, f, "악화 요인") for k, f in (svc.data.get("aggravating_factors") or {}).items()]
    return items


def cmd_stats(svc):
    st = svc.review_stats()
    print(f"환자 화면 게이트: {st['patient_gate']}  (ENV_CONTROL_PATIENT_GATE)")
    print(f"전체: " + " · ".join(f"{s} {n}" for s, n in st["total"].items()))
    for k, c in st["per_owner"].items():
        print(f"  {k:22s} " + " ".join(f"{s[:4]}={n}" for s, n in c.items()))


def cmd_list(svc, group, status):
    for key, owner, kind in _owners(svc):
        if group and key != group:
            continue
        rows = [m for m in owner.get("measures") or [] if not status or svc.measure_status(m) == status]
        if not rows:
            continue
        print(f"\n[{kind}] {key} — {owner.get('label_ko', '')}")
        for m in rows:
            rev = m.get("review") or {}
            print(f"  {svc.measure_status(m):9s} {m['tier']:15s} {m['id']:34s} {m.get('short_ko', '')}"
                  + (f"   ← {rev.get('reviewed_by', '')} {rev.get('reviewed_on', '')}" if rev.get("reviewed_by") else ""))


def cmd_show(svc, mid):
    hit = svc.find_measure(mid)
    if not hit:
        sys.exit(f"조치를 찾을 수 없음: {mid}")
    m = hit["measure"]
    print(json.dumps({"owner": hit["owner"], **m}, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", help="데이터 파일(기본 data/allergen_environment_control.json)")
    ap.add_argument("--log", help="로그 파일(기본 data/environment_control_review_log.json; --path 를 주면 그 옆의 *_review_log.json)")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--group", metavar="KEY")
    ap.add_argument("--status", choices=REVIEW_STATUSES)
    ap.add_argument("--show", metavar="MEASURE_ID")
    ap.add_argument("--approve", nargs="+", metavar="MEASURE_ID")
    ap.add_argument("--reject", nargs="+", metavar="MEASURE_ID")
    ap.add_argument("--revise", metavar="MEASURE_ID")
    ap.add_argument("--text", metavar="TEXT", help="--revise 의 새 본문")
    ap.add_argument("--short", metavar="TEXT", help="--revise 의 새 짧은 수칙")
    ap.add_argument("--approve-group", metavar="KEY")
    ap.add_argument("--import", dest="import_file", metavar="FILE.json")
    ap.add_argument("--by", default="", metavar="NAME", help="검토자 이름")
    ap.add_argument("--note", default="", metavar="TEXT")
    args = ap.parse_args()

    data_path = Path(args.path) if args.path else None
    log_path = Path(args.log) if args.log else (
        data_path.with_name(data_path.stem + "_review_log.json") if data_path else None)
    svc = EnvironmentControlService(path=data_path, log_path=log_path)
    if not svc.available:
        sys.exit("데이터를 읽지 못했습니다.")

    if args.stats:
        cmd_stats(svc)
    if args.list:
        cmd_list(svc, args.group, args.status)
    if args.show:
        cmd_show(svc, args.show)

    changed = False
    for mid in args.approve or []:
        svc.set_review(mid, "approved", by=args.by, note=args.note)
        print(f"approved  {mid}")
        changed = True
    for mid in args.reject or []:
        svc.set_review(mid, "rejected", by=args.by, note=args.note)
        print(f"rejected  {mid}")
        changed = True
    if args.revise:
        if not (args.text or args.short):
            sys.exit("--revise 에는 --text 또는 --short 가 필요합니다")
        svc.set_review(args.revise, "revised", by=args.by, note=args.note, text_ko=args.text, short_ko=args.short)
        print(f"revised   {args.revise}")
        changed = True
    if args.approve_group:
        n = 0
        for m in svc._owner_measures(args.approve_group):
            if svc.measure_status(m) == "candidate":
                svc.set_review(m["id"], "approved", by=args.by, note=args.note)
                n += 1
        print(f"approved  {args.approve_group}: {n}건")
        changed = True
    if args.import_file:
        export = json.loads(Path(args.import_file).read_text(encoding="utf-8"))
        out = svc.import_review_export(export, by=args.by)
        print(f"반영 {len(out['applied'])}건: {', '.join(out['applied'][:12])}{' …' if len(out['applied']) > 12 else ''}")
        if out["unknown"]:
            print(f"알 수 없는 키 {len(out['unknown'])}건: {out['unknown']}")
        manual = out["manual"]
        if manual.get("checked"):
            print(f"사람이 처리할 체크 {len(manual['checked'])}건(쟁점·설계·다음 단계): {manual['checked']}")
        for k, v in (manual.get("notes") or {}).items():
            print(f"사람이 처리할 메모 [{k}]: {v}")
        changed = True

    if changed:
        svc.save()
        print(f"저장: {svc.path}  (로그: {svc.log_path})")
        cmd_stats(svc)
    elif not (args.stats or args.list or args.show):
        ap.print_help()
    if changed and patient_gate() == "candidate_ok":
        print("참고: 게이트가 candidate_ok 라 검토 전 조치도 환자 화면에 나갑니다. "
              "검토가 끝나면 ENV_CONTROL_PATIENT_GATE=approved_only 로 켜세요.")


if __name__ == "__main__":
    main()
