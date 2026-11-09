#!/usr/bin/env python3
"""실제 결과지 OCR 평가 — 정답 전사와 대조해 채점하고, 백엔드 간 일치도를 재고, HTML 리포트를 만든다.

입력
  output/ocr_eval_2026-10-09/manifest.json          사진 목록(id)
  output/ocr_eval_2026-10-09/truth/<id>.json        사람이 읽은 정답(reference)
  output/ocr_eval_2026-10-09/runs/<run>/<id>.json   OCR 결과(scripts/eval_real_ocr.py)
  docs/ocr_eval_2026-10-09/findings.json            (선택) 오류 포인트·고도화 방안 서술

출력
  docs/ocr_eval_2026-10-09/summary.json             채점 결과(행 단위 대조 포함)
  docs/ocr_eval_2026-10-09/index.html               전체 과정·결과 확인 페이지
  docs/ocr_eval_2026-10-09/img/<id>.jpg             축소 사본(리포트용)

행 대조 방식
  1) 인쇄된 행 번호가 양쪽에 있고 이름이 비슷하면 번호로 짝을 짓는다
  2) 남은 행은 항원 레지스트리 별칭으로 정규화한 id 가 같으면 짝
  3) 그래도 남으면 이름 유사도(difflib) 0.75 이상인 것끼리 짝
  짝지은 행마다 수치·class·팽진 크기·양성 판정을 비교한다. 값은 고치지 않고 그대로 센다.

사용
  python3 scripts/eval_real_ocr_report.py --runs openai,ollama
"""
from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from models.schemas import normalize_class_token  # noqa: E402
from score_ocr import build_alias_index, norm  # noqa: E402

EVAL_DIR = ROOT / "output" / "ocr_eval_2026-10-09"
DOC_DIR = ROOT / "docs" / "ocr_eval_2026-10-09"

RUN_LABELS = {
    "openai": "API 모델 — OpenAI gpt-5.4",
    "ollama": "Local 모델 — 연구실 Ollama qwen3.8:27b",
    "openai_rotated": "OpenAI gpt-5.4 (회전 보정)",
    "ollama_rotated": "Ollama qwen3.8:27b (회전 보정)",
}


# ----------------------------------------------------------------------------
# 값 정규화
# ----------------------------------------------------------------------------
def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def value_of(row: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """(수치, 미만표기). '<0.35' 는 (None, '<0.35')."""
    vt = row.get("value_text")
    if isinstance(vt, str) and vt.strip().startswith(("<", "≤", "<=")):
        return None, re.sub(r"\s+", "", vt.strip())
    v = _num(row.get("value"))
    if v is None and isinstance(vt, str) and vt.strip():
        # 'N', '100이하' 같은 비수치 표기
        return None, vt.strip()
    return v, None


def values_equal(t: Dict[str, Any], g: Dict[str, Any]) -> bool:
    tv, tl = value_of(t)
    gv, gl = value_of(g)
    if tl or gl:
        return (tl or "") == (gl or "")
    if tv is None and gv is None:
        return True
    if tv is None or gv is None:
        return False
    return abs(tv - gv) <= max(0.005, abs(tv) * 0.01)


def size_norm(s) -> str:
    if s is None:
        return ""
    s = str(s).lower().replace("×", "x").replace("*", "x").replace(" ", "")
    if s in (".", "-", "—"):          # EMR 양식의 '.' 는 빈칸 표시 — 반응 없음과 같게 본다
        return ""
    parts = s.split("x")
    out = []
    for p in parts:
        try:
            f = float(p)
            out.append(str(int(f)) if f.is_integer() else str(f))
        except ValueError:
            out.append(p)
    return "x".join(out)


def is_positive(row: Dict[str, Any], test_type: str, histamine: Optional[float] = None) -> Optional[bool]:
    """임상 판정 — 앱(models/schemas.determine_interpretation)과 같은 기준.
    MAST: class 있으면 class≥1, 없으면 수치≥0.35. SPT: 평균 팽진≥3mm 또는 히스타민 대조의 50% 이상."""
    if test_type == "SPT":
        st = row.get("size_text")
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(st or ""))]
        mm = sum(nums) / len(nums) if nums else (_num(row.get("mean_mm")) or _num(row.get("value")))
        if not mm:
            return False
        return mm >= 3 or bool(histamine and mm >= histamine * 0.5)
    c = normalize_class_token(row.get("class_value"))
    if c is not None:
        return c >= 1
    v, lod = value_of(row)
    if lod:
        if lod.startswith(">"):                      # '>100' 은 상한 초과 — 양성
            return True
        return lod.strip().lower() in ("양성", "positive", "p", "阳性")
    if v is None:
        return None
    return v >= 0.35


_LATIN2 = re.compile(r"[a-z]{2}")


def no_key(v) -> Optional[str]:
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return str(int(m.group())) if m else None


# ----------------------------------------------------------------------------
# 행 대조
# ----------------------------------------------------------------------------
class Matcher:
    def __init__(self):
        self.idx = build_alias_index()
        self._feat: Dict[Tuple[str, str], Tuple[str, str, str, str]] = {}

    def canon(self, name, korean) -> str:
        for cand in (name, korean):
            n = norm(cand)
            if n and n in self.idx:
                return self.idx[n]
        return ""

    def feat(self, r: Dict[str, Any]) -> Tuple[str, str, str, str]:
        """행의 (영문, 한글, 괄호 풀어쓴 원문, 레지스트리 id) — 행마다 한 번만 계산한다."""
        k = (str(r.get("allergen_name") or ""), str(r.get("korean_name") or ""))   # id(dict) 는 이미지가 바뀌면 재사용된다
        f = self._feat.get(k)
        if f is None:
            f = (norm(r.get("allergen_name")), norm(r.get("korean_name")),
                 norm(re.sub(r"[()]", " ", str(r.get("allergen_name") or ""))),
                 self.canon(r.get("allergen_name"), r.get("korean_name")))
            self._feat[k] = f
        return f

    def sim(self, t: Dict[str, Any], g: Dict[str, Any]) -> float:
        """이름 유사도 0~1. 영문 완전일치·레지스트리 id 일치 = 1.0, 한글명 일치 = 0.95,
        포함 관계 = 0.9, 단어 집합 포함 = 0.85, 그 밖에는 difflib 비율."""
        t_en, t_ko, _, tc = self.feat(t)
        g_en, g_ko, g_raw, gc = self.feat(g)       # OCR 은 'Alder (오리나무)(T2)' 처럼 한 칸에 모두 적기도 한다
        if t_en and t_en in (g_en, g_raw):
            return 1.0
        if tc and tc == gc and not (_LATIN2.search(t_en) and _LATIN2.search(g_en)):
            return 1.0          # 레지스트리 id 는 한쪽이 한글만일 때만 — 영문끼리는 별칭 공유(Wasp↔Yellow jacket)로 접힌다
        best = 0.0
        if t_ko and t_ko in (g_ko, g_en, g_raw):
            best = 0.95
        for a in (t_en, t_ko):
            if not a:
                continue
            for b in dict.fromkeys((g_en, g_ko, g_raw)):      # 같은 문자열은 한 번만 비교
                if not b:
                    continue
                if (a in b or b in a) and min(len(a), len(b)) >= 3:
                    best = max(best, 0.9)
                ta, tb = set(a.split()), set(b.split())
                if ta and tb and len(ta & tb) == min(len(ta), len(tb)) and any(len(w) >= 3 for w in ta & tb):
                    best = max(best, 0.85)
                if best < 0.75:
                    sm = difflib.SequenceMatcher(None, a, b)
                    if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6:
                        best = max(best, sm.ratio())
        return best

    def match(self, t_rows: List[Dict[str, Any]], g_rows: List[Dict[str, Any]]):
        """탐욕적 짝짓기 — 점수 = 이름 유사도 + 인쇄 번호 일치 보너스(0.15) + 번호 근접 보너스(0.05).
        이름 유사도 0.75 이상이면 짝, 0.6~0.75 는 번호까지 같을 때만 짝."""
        cands = []
        for i, t in enumerate(t_rows):
            tn = no_key(t.get("printed_no"))
            for j, g in enumerate(g_rows):
                s = self.sim(t, g)
                if s < 0.6:
                    continue
                gn = no_key(g.get("printed_no"))
                same_no = bool(tn and gn and tn == gn)
                near_no = bool(tn and gn and abs(int(tn) - int(gn)) <= 1)
                if s < 0.75 and not same_no:
                    continue
                score = s + (0.15 if same_no else 0.0) + (0.05 if near_no else 0.0)
                cands.append((score, s, same_no, i, j))
        pairs: List[Tuple[int, int, str, float]] = []
        used_t, used_g = set(), set()
        for score, s, same_no, i, j in sorted(cands, key=lambda x: -x[0]):
            if i in used_t or j in used_g:
                continue
            how = ("no+name" if same_no and s >= 0.999 else "name" if s >= 0.999 else "no" if same_no else "fuzzy")
            pairs.append((i, j, how, s))
            used_t.add(i); used_g.add(j)
        return pairs, used_t, used_g


# ----------------------------------------------------------------------------
# 채점
# ----------------------------------------------------------------------------
def ocr_rows(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    res = (run.get("result") or {}).get("results") or []
    return res


def score_image(truth: Dict[str, Any], run: Dict[str, Any], matcher: Matcher) -> Dict[str, Any]:
    tt = truth["test_type"]
    t_rows = truth.get("results") or []
    g_rows = ocr_rows(run)
    out: Dict[str, Any] = {"truth_rows": len(t_rows), "ocr_rows": len(g_rows)}
    if "error" in run:
        out.update({"error": run["error"], "matched": 0, "rows": []})
        return out
    pairs, used_t, used_g = matcher.match(t_rows, g_rows)
    # SPT 양성 판정은 앱처럼 히스타민 대조(50% 규칙)를 쓴다 — 정답은 정답의, OCR 은 OCR 이 읽은 히스타민으로
    t_h = _num((truth.get("patient") or {}).get("histamine_mean_mm"))
    g_h = _num(((run.get("result") or {}).get("patient") or {}).get("histamine_mean_mm"))
    rows = []
    val_ok = val_n = cls_ok = cls_n = cls_missing = size_ok = size_n = unverifiable = no_mismatch = 0
    t_pos = missed_pos = false_pos = x10 = 0
    pos_pairs: List[Tuple[bool, bool]] = []
    flagged_err = flagged_ok = err_rows = ok_rows = 0
    for i, j, how, sim_s in pairs:
        t, g = t_rows[i], g_rows[j]
        r: Dict[str, Any] = {"no": t.get("printed_no"), "truth": _slim(t), "ocr": _slim(g), "how": how, "sim": round(sim_s, 2), "errors": []}
        unsure = bool(t.get("uncertain"))        # 사람도 확신 못 한 행 — 값 비교에서 통째로 뺀다('검증 불가')
        if how == "no" and sim_s < 0.75:
            r["errors"].append("name_misread")   # 번호는 같지만 이름이 다르게 읽힘(리뷰 L2)
        tn, gn = no_key(t.get("printed_no")), no_key(g.get("printed_no"))
        if tn and gn and tn != gn:
            r["errors"].append("no_mismatch"); no_mismatch += 1
        if unsure:
            r["errors"].append("unverifiable"); unverifiable += 1
        elif tt == "SPT":
            size_n += 1
            if size_norm(t.get("size_text")) == size_norm(g.get("size_text")):
                size_ok += 1
            else:
                r["errors"].append("size")
        else:
            val_n += 1
            if values_equal(t, g):
                val_ok += 1
            else:
                r["errors"].append("value")
                tv, tl = value_of(t); gv, gl = value_of(g)
                if tl and not gl and gv is not None:
                    r["errors"].append("lod_dropped")      # '<0.35' 를 숫자로
                if tv and gv and tv > 0 and gv > 0 and (0.09 <= gv / tv <= 0.11 or 9 <= gv / tv <= 11):
                    r["errors"].append("x10"); x10 += 1
            if t.get("class_value") is not None:
                gc = normalize_class_token(g.get("class_value"))
                if gc is None:
                    cls_missing += 1; r["errors"].append("class_missing")   # class 칸을 안 읽음(막대그래프 등)
                else:
                    cls_n += 1
                    if gc == normalize_class_token(t.get("class_value")):
                        cls_ok += 1
                    else:
                        r["errors"].append("class")
        tp, gp = is_positive(t, tt, t_h), is_positive(g, tt, g_h)
        r["truth_pos"], r["ocr_pos"] = tp, gp
        if tp:
            t_pos += 1
        if tp and not gp:
            r["errors"].append("missed_positive"); missed_pos += 1
        if gp and not tp:
            r["errors"].append("false_positive"); false_pos += 1
        if tp is not None and gp is not None:
            pos_pairs.append((tp, gp))
        if how == "fuzzy":
            r["errors"].append("name_fuzzy")
        flagged = bool(g.get("review_flags"))
        r["flagged"] = flagged
        clinical_err = (tp != gp) or ("x10" in r["errors"])
        if clinical_err:
            err_rows += 1; flagged_err += flagged
        else:
            ok_rows += 1; flagged_ok += flagged
        rows.append(r)
    missed = [t_rows[i] for i in range(len(t_rows)) if i not in used_t]
    extra = [g_rows[j] for j in range(len(g_rows)) if j not in used_g]
    for t in missed:
        if is_positive(t, tt, t_h):
            t_pos += 1; missed_pos += 1
        rows.append({"no": t.get("printed_no"), "truth": _slim(t), "ocr": None, "how": "missed",
                     "errors": ["missed_row"] + (["missed_positive"] if is_positive(t, tt, t_h) else []),
                     "truth_pos": is_positive(t, tt, t_h), "ocr_pos": None, "flagged": False})
    duplicates = 0
    for g in extra:
        gp = is_positive(g, tt, g_h)
        # 이미 짝지어진 정답 행과 같은 항원이면 '중복행'(두 번 읽기 병합이 만든 복제) — 위양성으로 세지 않는다
        dup_of = None
        best = 0.0
        same_no_val = None
        gk = no_key(g.get("printed_no"))
        for i, j, _, _s in pairs:
            s = matcher.sim(t_rows[i], g)
            if s > best:
                best, dup_of = s, t_rows[i]
            if gk and no_key(t_rows[i].get("printed_no")) == gk and (
                    size_norm(t_rows[i].get("size_text")) == size_norm(g.get("size_text")) if tt == "SPT" else values_equal(t_rows[i], g)):
                same_no_val = t_rows[i]
        if dup_of is not None and best < 0.75 and same_no_val is not None:
            dup_of, best = same_no_val, 0.75            # 같은 번호·같은 값, 이름만 다르게 읽힌 복제(리뷰 L1)
            name_misread_dup = True
        else:
            name_misread_dup = False
        if dup_of is not None and best >= 0.75:
            duplicates += 1
            errs = ["duplicate_row"] + (["duplicate_name_misread"] if name_misread_dup else [])
            if tt == "SPT":
                if size_norm(dup_of.get("size_text")) != size_norm(g.get("size_text")):
                    errs.append("duplicate_conflict")
            elif not values_equal(dup_of, g) or (dup_of.get("class_value") is not None and normalize_class_token(g.get("class_value")) not in (None, normalize_class_token(dup_of.get("class_value")))):
                errs.append("duplicate_conflict")
            rows.append({"no": g.get("printed_no"), "truth": None, "ocr": _slim(g), "how": "duplicate",
                         "errors": errs, "truth_pos": None, "ocr_pos": gp, "flagged": bool(g.get("review_flags"))})
            continue
        rows.append({"no": g.get("printed_no"), "truth": None, "ocr": _slim(g), "how": "extra",
                     "errors": ["extra_row"] + (["false_positive"] if gp else []),
                     "truth_pos": None, "ocr_pos": gp, "flagged": bool(g.get("review_flags"))})
        if gp:
            false_pos += 1

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else None

    rows.sort(key=lambda r: (int(no_key(r["no"]) or 10**6), str(r.get("how"))))
    got_tt = (run.get("result") or {}).get("test_type")
    out.update({
        "matched": len(pairs), "missed_rows": len(missed), "extra_rows": len(extra) - duplicates,
        "duplicate_rows": duplicates, "unverifiable": unverifiable, "no_mismatch": no_mismatch,
        "recall": pct(len(pairs), len(t_rows)), "precision": pct(len(pairs), len(g_rows)),
        "value_acc": pct(val_ok, val_n), "value_n": val_n,
        "class_acc": pct(cls_ok, cls_n), "class_n": cls_n, "class_missing": cls_missing,
        "size_acc": pct(size_ok, size_n), "size_n": size_n,
        "truth_positive": t_pos, "missed_positive": missed_pos, "false_positive": false_pos, "x10": x10,
        "pos_pairs": pos_pairs,
        "clin_err_rows": err_rows, "clin_err_flagged": flagged_err, "ok_rows": ok_rows, "ok_flagged": flagged_ok,
        "test_type_got": got_tt, "test_type_ok": (got_tt == truth["test_type"]),
        "patient": _score_patient(truth, run),
        "seconds": run.get("seconds"), "model": run.get("model"),
        "double_read": ((run.get("result") or {}).get("metadata") or {}).get("double_read"),
        "review": ((run.get("result") or {}).get("metadata") or {}).get("review"),
        "rows": rows,
    })
    return out


def _slim(r: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("printed_no", "code", "allergen_name", "korean_name", "class_value", "value", "value_text",
            "size_text", "mean_mm", "uncertain", "note", "review_flags", "value_raw")
    # 0 == False 라서 `not in (None, "", [], False)` 로 거르면 class 0·수치 0.00 이 사라진다(리뷰 지적) — 빈 값만 거른다
    def empty(v):
        return v is None or v is False or (isinstance(v, (str, list)) and len(v) == 0)
    return {k: r.get(k) for k in keep if not empty(r.get(k))}


def _mask_person(v):
    """실제 결과지의 환자명·의뢰의사명을 리포트에 싣지 않는다 — 한글 이름은 가운데를 ○ 로 가린다(홍길동 → 홍○동).
    2026-10-13: 평가 자료는 실제 검사 결과지이며 환자명을 마스킹한 상태로 작업한다."""
    if not isinstance(v, str):
        return v
    t = v.strip()
    if re.fullmatch(r"[가-힣]{2,4}", t):
        return t[0] + "○" * (len(t) - 2) + t[-1] if len(t) > 2 else t[0] + "○"
    return v


def _mask_facility(v, providers):
    if not isinstance(v, str):
        return v
    for prov in providers:
        if prov and len(prov) >= 2 and prov in v:
            v = v.replace(prov, _mask_person(prov))
    return v


def _mask_notes(notes, truth):
    """전사 메모(자유 서술)에 적힌 환자명·의뢰의사명도 가린다. 모든 정답 파일의 이름을 모아 치환한다."""
    if not isinstance(notes, str):
        return notes
    global _ALL_PERSONS
    try:
        _ALL_PERSONS
    except NameError:
        _ALL_PERSONS = set()
        for f in sorted((EVAL_DIR / "truth").glob("*.json")):
            try:
                tp = (json.loads(f.read_text(encoding="utf-8")).get("patient") or {})
            except Exception:
                continue
            for k in ("name", "ordering_provider"):
                v = tp.get(k)
                if isinstance(v, str) and re.fullmatch(r"[가-힣]{2,4}", v.strip()):
                    _ALL_PERSONS.add(v.strip())
    for n in sorted(_ALL_PERSONS, key=len, reverse=True):
        notes = notes.replace(n, _mask_person(n))
    return notes


def _score_patient(truth: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    tp = truth.get("patient") or {}
    gp = ((run.get("result") or {}).get("patient")) or {}

    def s(v):
        return re.sub(r"\s+", "", str(v)).replace("/", "-").replace(".", "-") if v not in (None, "") else ""
    name_ok = (s(gp.get("name")) == s(tp.get("name"))) if tp.get("name") else (not gp.get("name"))
    date_ok = s(gp.get("test_date")) in {s(tp.get("test_date")), s(tp.get("collect_date"))} - {""} if (tp.get("test_date") or tp.get("collect_date")) else (not gp.get("test_date"))
    rep_ok = (s(gp.get("report_date")) == s(tp.get("report_date"))) if tp.get("report_date") else (not gp.get("report_date"))
    id_ok = s(gp.get("patient_id_external")) in {s(tp.get("patient_id_external")), s(tp.get("accession"))} - {""} if (tp.get("patient_id_external") or tp.get("accession")) else (not gp.get("patient_id_external"))
    gender_ok = (s(gp.get("gender")) == s(tp.get("gender"))) if tp.get("gender") else (not gp.get("gender"))
    age_ok = (_num(gp.get("age")) == _num(tp.get("age"))) if tp.get("age") is not None else (gp.get("age") is None)
    fac = s(gp.get("facility")).lower()
    lab_alias = {"씨젠의료재단": ["seegene", "씨젠"], "삼광의료재단": ["samkwang", "삼광", "sml"], "GC Labs": ["gclabs", "gc labs", "녹십자", "gc"],
                 "이원의료재단": ["eone", "이원"], "신원의료재단": ["shinwon", "신원"], "분당서울대학교병원": ["snuh", "분당서울대", "서울대"]}
    cands = [s(x).lower() for x in (truth.get("lab"), tp.get("ordering_facility")) if x] + [a.replace(" ", "") for a in lab_alias.get(truth.get("lab") or "", [])]
    fac_ok = bool(fac) and any(c and (c in fac or fac in c) for c in cands) if cands else True
    return {"name": name_ok, "test_date": date_ok, "report_date": rep_ok, "patient_id": id_ok,
            "gender": gender_ok, "age": age_ok, "facility": fac_ok,
            "truth": _mask_patient({k: tp.get(k) for k in ("name", "age", "gender", "test_date", "collect_date", "report_date", "patient_id_external", "accession", "ordering_facility", "ordering_provider", "histamine_mean_mm", "negative_control_mean_mm")}, tp),
            "ocr": _mask_patient({k: gp.get(k) for k in ("name", "age", "gender", "test_date", "report_date", "patient_id_external", "facility", "ordering_provider", "histamine_mean_mm", "negative_control_mean_mm")}, tp)}


def _mask_patient(d: Dict[str, Any], tp: Dict[str, Any]) -> Dict[str, Any]:
    providers = [tp.get("ordering_provider"), d.get("ordering_provider")]
    out = dict(d)
    for k in ("name", "ordering_provider"):
        out[k] = _mask_person(out.get(k))
    for k in ("facility", "ordering_facility"):
        out[k] = _mask_facility(out.get(k), providers)
    return out


def agreement(truth: Dict[str, Any], a: Dict[str, Any], b: Dict[str, Any], matcher: Matcher) -> Dict[str, Any]:
    """두 OCR 결과끼리의 일치도 — a 를 기준으로 b 를 대조한다(정답 없이도 잴 수 있는 지표)."""
    if "error" in a or "error" in b:
        return {"error": True}
    tt = truth["test_type"]
    ra, rb = ocr_rows(a), ocr_rows(b)
    pairs, used_a, used_b = matcher.match(ra, rb)
    ha = _num(((a.get("result") or {}).get("patient") or {}).get("histamine_mean_mm"))
    hb = _num(((b.get("result") or {}).get("patient") or {}).get("histamine_mean_mm"))
    same_val = same_cls = same_size = cls_pairs = cls_one_missing = val_pairs = size_pairs = 0
    pos_pairs = []
    for i, j, _, _s in pairs:
        x, y = ra[i], rb[j]
        if tt == "SPT":
            size_pairs += 1
            same_size += size_norm(x.get("size_text")) == size_norm(y.get("size_text"))
        else:
            val_pairs += 1
            same_val += values_equal(x, y)
            cx, cy = normalize_class_token(x.get("class_value")), normalize_class_token(y.get("class_value"))
            if cx is None or cy is None:
                cls_one_missing += (cx is None) != (cy is None)   # 한쪽만 class 를 안 읽음 — 불일치가 아니라 미판독
            else:
                cls_pairs += 1
                same_cls += cx == cy
        px, py = is_positive(x, tt, ha), is_positive(y, tt, hb)
        if px is not None and py is not None:
            pos_pairs.append((px, py))
    n = len(pairs)
    union = len(ra) + len(rb) - n
    return {"rows_a": len(ra), "rows_b": len(rb), "matched": n, "union": union,
            "row_jaccard": round(100.0 * n / union, 1) if union else None,
            "value_agree": round(100.0 * same_val / val_pairs, 1) if val_pairs else None,
            "class_agree": round(100.0 * same_cls / cls_pairs, 1) if cls_pairs else None,
            "size_agree": round(100.0 * same_size / size_pairs, 1) if size_pairs else None,
            "same_val": same_val, "val_pairs": val_pairs, "same_cls": same_cls, "cls_pairs": cls_pairs,
            "cls_one_missing": cls_one_missing, "same_size": same_size, "size_pairs": size_pairs,
            "pos_pairs": pos_pairs}


def kappa(pairs: List[Tuple[bool, bool]]) -> Optional[float]:
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


# ----------------------------------------------------------------------------
# 집계
# ----------------------------------------------------------------------------
def aggregate(scores: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [s for s in scores if "error" not in s]
    def tot(k):
        return sum(s.get(k) or 0 for s in ok)
    def ratio(a, b):
        return round(100.0 * a / b, 1) if b else None
    val_ok = sum(round((s["value_acc"] or 0) * s["value_n"] / 100) for s in ok if s.get("value_n"))
    cls_ok = sum(round((s["class_acc"] or 0) * s["class_n"] / 100) for s in ok if s.get("class_n"))
    size_ok = sum(round((s["size_acc"] or 0) * s["size_n"] / 100) for s in ok if s.get("size_n"))
    pos_pairs = [p for s in ok for p in s.get("pos_pairs", [])]
    secs = [s["seconds"] for s in ok if s.get("seconds")]
    pat = [s["patient"] for s in ok]
    return {
        "images": len(scores), "errors": len(scores) - len(ok),
        "truth_rows": tot("truth_rows"), "ocr_rows": tot("ocr_rows"), "matched": tot("matched"),
        "missed_rows": tot("missed_rows"), "extra_rows": tot("extra_rows"), "duplicate_rows": tot("duplicate_rows"),
        "unverifiable": tot("unverifiable"), "no_mismatch": tot("no_mismatch"), "class_missing": tot("class_missing"),
        "recall": ratio(tot("matched"), tot("truth_rows")),
        "precision": ratio(tot("matched"), tot("ocr_rows")),
        "value_acc": ratio(val_ok, tot("value_n")), "value_n": tot("value_n"),
        "class_acc": ratio(cls_ok, tot("class_n")), "class_n": tot("class_n"),
        "size_acc": ratio(size_ok, tot("size_n")), "size_n": tot("size_n"),
        "truth_positive": tot("truth_positive"), "missed_positive": tot("missed_positive"),
        "false_positive": tot("false_positive"), "x10": tot("x10"),
        "positivity_agree": ratio(sum(1 for a, b in pos_pairs if a == b), len(pos_pairs)),
        "positivity_kappa": kappa(pos_pairs),
        "test_type_ok": sum(1 for s in ok if s["test_type_ok"]),
        "clin_err_rows": tot("clin_err_rows"), "clin_err_flagged": tot("clin_err_flagged"),
        "ok_rows": tot("ok_rows"), "ok_flagged": tot("ok_flagged"),
        "seconds_mean": round(sum(secs) / len(secs), 1) if secs else None,
        "seconds_total": round(sum(secs), 0) if secs else None,
        "patient": {k: ratio(sum(1 for p in pat if p.get(k)), len(pat)) for k in ("name", "test_date", "report_date", "patient_id", "gender", "age", "facility")},
    }


def error_taxonomy(scores: Dict[str, Dict[str, Dict[str, Any]]], runs: List[str]) -> Dict[str, Any]:
    """오류 종류별 집계(백엔드별) + 예시."""
    tax: Dict[str, Dict[str, Any]] = {}
    for run in runs:
        c: Counter = Counter()
        ex: Dict[str, List[str]] = defaultdict(list)
        for img, sc in scores.items():
            s = sc.get(run)
            if not s:
                continue
            if "error" in s:
                c["doc_error"] += 1; ex["doc_error"].append(f"{img}: {s['error'][:80]}")
                continue
            if not s["test_type_ok"]:
                c["test_type"] += 1; ex["test_type"].append(f"{img}: {s['test_type_got']}")
            for r in s["rows"]:
                for e in r["errors"]:
                    c[e] += 1
                    if len(ex[e]) < 6:
                        t, g = r.get("truth") or {}, r.get("ocr") or {}
                        ex[e].append(f"{img} #{r.get('no') or '-'} {t.get('allergen_name') or g.get('allergen_name')}: "
                                     f"정답 {t.get('value_text') or t.get('value') if t else '—'}/{t.get('class_value') if t else '—'}"
                                     f"{'/' + str(t.get('size_text')) if t and t.get('size_text') else ''} → 읽음 "
                                     f"{g.get('value_text') or g.get('value') if g else '—'}/{g.get('class_value') if g else '—'}"
                                     f"{'/' + str(g.get('size_text')) if g and g.get('size_text') else ''}")
            dr = s.get("double_read") or {}
            if isinstance(dr, dict) and dr.get("error"):
                c["double_read_error"] += 1; ex["double_read_error"].append(f"{img}: {str(dr['error'])[:60]}")
        tax[run] = {"counts": dict(c), "examples": dict(ex)}
    return tax


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------
def render_html(data: Dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", payload)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>실제 결과지 OCR 평가</title>
<style>
:root{--bg:#f7f7f5;--surface:#ffffff;--ink:#1b1b1f;--ink2:#4f5560;--muted:#8a8f99;--line:#e3e4e8;
 --c1:#2f6fed;--c2:#e07b1a;--c3:#5a5f6a;--good:#1f8a4c;--warn:#b7791f;--bad:#c6362f;--bad-bg:#fdecea;--good-bg:#e8f5ec;--warn-bg:#fff6e0;
 --mono:ui-monospace,SFMono-Regular,Menlo,monospace}
:root:not([data-theme="light"]){color-scheme:light dark}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#121316;--surface:#1b1d21;--ink:#ecedf0;--ink2:#b7bbc4;--muted:#80858f;--line:#2c2f36;
 --c1:#7aa2ff;--c2:#f0a050;--c3:#9aa0ab;--good:#5fc784;--warn:#e0b45a;--bad:#f07a73;--bad-bg:#3a1f1e;--good-bg:#1c3326;--warn-bg:#3a2f18}}
:root[data-theme="dark"]{--bg:#121316;--surface:#1b1d21;--ink:#ecedf0;--ink2:#b7bbc4;--muted:#80858f;--line:#2c2f36;
 --c1:#7aa2ff;--c2:#f0a050;--c3:#9aa0ab;--good:#5fc784;--warn:#e0b45a;--bad:#f07a73;--bad-bg:#3a1f1e;--good-bg:#1c3326;--warn-bg:#3a2f18}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}
h1{font-size:26px;margin:8px 0 4px}h2{font-size:20px;margin:40px 0 12px;padding-top:12px;border-top:1px solid var(--line)}h3{font-size:16px;margin:20px 0 8px}
p,li{color:var(--ink2)}.lede{font-size:16px;color:var(--ink2);max-width:860px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line);color:var(--ink2);margin-right:6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{font-size:12px;color:var(--muted)}.tile .v{font-size:24px;font-weight:600;letter-spacing:-.01em}.tile .s{font-size:12px;color:var(--ink2)}
.tile.c1{border-top:3px solid var(--c1)}.tile.c2{border-top:3px solid var(--c2)}
table{border-collapse:collapse;width:100%;background:var(--surface);font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;color:var(--ink2);background:var(--bg);position:sticky;top:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.tbl-wrap{overflow:auto;border:1px solid var(--line);border-radius:10px;max-height:70vh}
.bad{background:var(--bad-bg)}.good{color:var(--good)}.warn{background:var(--warn-bg)}
.chip{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;background:var(--bad-bg);color:var(--bad);margin:1px 2px 1px 0;white-space:nowrap}
.chip.ok{background:var(--good-bg);color:var(--good)}.chip.info{background:var(--warn-bg);color:var(--warn)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--ink2);margin:6px 0}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.chart{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
svg text{fill:var(--ink2);font-size:11px}svg .axis{stroke:var(--line)}svg .grid{stroke:var(--line);stroke-dasharray:2 3}
details{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:8px 14px;margin:8px 0}
summary{cursor:pointer;font-weight:600}summary .meta{font-weight:400;color:var(--muted);font-size:13px;margin-left:8px}
.detail{display:grid;grid-template-columns:260px 1fr;gap:16px;margin-top:10px}
.detail img{width:100%;border:1px solid var(--line);border-radius:6px;background:#fff}
@media (max-width:760px){.detail{grid-template-columns:1fr}}
.kv{font-size:12px;color:var(--ink2)}.kv b{color:var(--ink)}
.mono{font-family:var(--mono);font-size:12px}
.tooltip{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);display:none;z-index:9}
.finding{background:var(--surface);border:1px solid var(--line);border-left:4px solid var(--c2);border-radius:8px;padding:10px 14px;margin:10px 0}
.finding h4{margin:0 0 4px;font-size:15px}.finding p{margin:4px 0}.finding .ev{font-size:12px;color:var(--muted)}
.rec{border-left-color:var(--c1)}
.toc a{margin-right:12px;font-size:13px}
a{color:var(--c1)}
.flow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:13px}.flow span{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:6px 10px}.flow em{color:var(--muted);font-style:normal}
small{color:var(--muted)}
</style>
</head>
<body>
<main>
<h1>실제 알레르기 결과지 OCR 평가</h1>
<p class="lede" id="lede"></p>
<div class="toc" id="toc"></div>

<h2 id="s1">1. 과정</h2>
<div class="flow" id="flow"></div>
<p id="method"></p>

<h2 id="s2">2. 전체 결과</h2>
<div id="tiles"></div>
<div class="charts" id="charts"></div>

<h2 id="s3">3. 두 모델 간 일치도 (정답 없이 서로 비교)</h2>
<div id="agree"></div>

<h2 id="s4">4. 사진별 결과</h2>
<p>행을 누르면 아래 5번의 상세로 이동합니다. 재현율 = 정답 행 중 찾은 비율, 정밀도 = 읽은 행 중 정답에 있는 비율, 수치 = 짝지은 행의 수치(미만 표기 포함) 정확도.</p>
<div class="tbl-wrap"><table id="per-image"></table></div>

<h2 id="s5">5. 사진별 상세 — 정답 vs API vs Local</h2>
<div class="legend"><span><i style="background:var(--bad-bg);border:1px solid var(--bad)"></i>불일치</span><span><i style="background:var(--good-bg)"></i>일치</span><span><i style="background:var(--warn-bg)"></i>검증 표시(앱이 확인 요청)</span></div>
<div id="details"></div>

<h2 id="s6">6. 오류 발생 포인트</h2>
<div id="errors"></div>

<h2 id="s7">7. OCR 고도화 방안</h2>
<div id="recs"></div>
<h3 id="s7b">7-1. 개선 실험 — 같은 사진을 고친 파이프라인으로 다시 읽은 결과 (before → after)</h3>
<div id="experiments"></div>
<h3 id="s7c">7-2. 독립 코드 리뷰 — 채점기·파이프라인 수정분에서 나온 결함과 처리</h3>
<div id="review"></div>

<h2 id="s8">8. 재현</h2>
<pre class="mono" id="repro"></pre>
</main>
<div class="tooltip" id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const RUNS = D.runs, LBL = D.run_labels, COL = {openai:'var(--c1)', ollama:'var(--c2)', openai_rotated:'var(--c1)', ollama_rotated:'var(--c2)'};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => v == null ? '—' : (typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(1)) : v);
const pct = v => v == null ? '—' : v.toFixed(1) + '%';
const tip = document.getElementById('tip');
const ERR = {value:'수치', lod_dropped:'< 미만표기 누락', x10:'10배', class:'class', class_missing:'class 미판독', size:'팽진크기', missed_positive:'양성 누락', false_positive:'위양성', name_fuzzy:'이름 유사매칭', name_misread:'이름 오독(번호 일치)', missed_row:'행 누락', extra_row:'정답에 없는 행', duplicate_row:'중복행', duplicate_conflict:'중복행 값 다름', duplicate_name_misread:'중복행(이름 오독)', no_mismatch:'행번호 다름', unverifiable:'검증불가(정답 불확실)'};
function showTip(e, html){tip.innerHTML = html; tip.style.display='block'; tip.style.left=(e.clientX+12)+'px'; tip.style.top=(e.clientY+12)+'px';}
function hideTip(){tip.style.display='none';}

// ---- 1. 과정
document.getElementById('lede').textContent = D.lede;
document.getElementById('toc').innerHTML = ['과정','전체 결과','모델 간 일치도','사진별 결과','사진별 상세','오류 포인트','고도화 방안','재현'].map((t,i)=>`<a href="#s${i+1}">${i+1}. ${t}</a>`).join('');
document.getElementById('flow').innerHTML = D.flow.map((s,i)=>`<span>${esc(s)}</span>${i<D.flow.length-1?'<em>→</em>':''}`).join('');
document.getElementById('method').innerHTML = D.method;

// ---- 2. 전체 결과
const A = D.aggregate;
const tiles = document.getElementById('tiles');
RUNS.forEach((r,ri)=>{
  const a = A[r]; if(!a) return;
  const h = `<h3><span class="pill" style="border-color:${COL[r]};color:${COL[r]}">${esc(LBL[r])}</span><small>${a.images}장 · 오류 ${a.errors}장 · 장당 ${fmt(a.seconds_mean)}초</small></h3><div class="tiles">`
   + tile('행 재현율', pct(a.recall), `${a.matched}/${a.truth_rows}행`, r)
   + tile('행 정밀도', pct(a.precision), `${a.matched}/${a.ocr_rows}행 · 중복 ${a.duplicate_rows} · 없는행 ${a.extra_rows}`, r)
   + tile('수치 정확도', pct(a.value_acc), `${a.value_n}행 (MAST) · 검증불가 ${a.unverifiable}`, r)
   + tile('class 정확도', pct(a.class_acc), `${a.class_n}행 · 미판독 ${a.class_missing}`, r)
   + tile('팽진 크기 정확도', pct(a.size_acc), `${a.size_n}행 (SPT)`, r)
   + tile('양성 누락', `${a.missed_positive}/${a.truth_positive}`, '정답 양성 중 놓친 행', r)
   + tile('위양성', `${a.false_positive}`, `10배 오독 ${a.x10}`, r)
   + tile('양성 판정 일치', pct(a.positivity_agree), `κ = ${fmt(a.positivity_kappa)}`, r)
   + tile('검사종류', `${a.test_type_ok}/${a.images - a.errors}`, 'MAST/SPT 구분', r)
   + tile('환자명', pct(a.patient.name), `검사일 ${pct(a.patient.test_date)} · 번호 ${pct(a.patient.patient_id)}`, r)
   + `</div>`;
  tiles.insertAdjacentHTML('beforeend', h);
});
function tile(k,v,s,r){return `<div class="tile ${r==='openai'||r==='openai_rotated'?'c1':'c2'}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`}

// 두 런 모두 성공한 사진만의 집계 — 성공 장수가 다를 때 나란히 비교하려면 이것을 본다(리뷰 M4)
const AC = D.aggregate_common || {}; const hasC = Object.keys(AC).length > 0 && (D.common_images||[]).length < D.images.length;
if (hasC) {
  const M2 = [['recall','행 재현율',1],['precision','행 정밀도',1],['value_acc','수치',1],['class_acc','class',1],['size_acc','팽진',1],['missed_positive','양성 누락',0],['false_positive','위양성',0],['duplicate_rows','중복행',0],['seconds_mean','장당 초',0]];
  tiles.insertAdjacentHTML('beforeend', `<h3>두 백엔드 모두 성공한 ${D.common_images.length}장 기준 비교 <small>(${esc(D.common_images.join(', '))})</small></h3><div class="tbl-wrap"><table><tr><th>지표</th>${RUNS.map(r=>`<th class="num" style="color:${COL[r]}">${esc(LBL[r])}</th>`).join('')}</tr>` +
    M2.map(([k,l,p]) => `<tr><td>${l}</td>${RUNS.map(r => `<td class="num">${AC[r] ? (p ? pct(AC[r][k]) : (k==='missed_positive' ? AC[r][k]+'/'+AC[r].truth_positive : fmt(AC[r][k]))) : '—'}</td>`).join('')}</tr>`).join('') + `</table></div>`);
}
const CA = hasC ? AC : A;
// charts: grouped bars per metric (2 series: fixed order openai, ollama)
const charts = document.getElementById('charts');
charts.insertAdjacentHTML('beforeend', barChart(hasC ? `핵심 지표 (공통 ${D.common_images.length}장, %)` : '핵심 지표 (전체, %)', ['recall','precision','value_acc','class_acc','size_acc','positivity_agree'],
  ['행 재현율','행 정밀도','수치','class','팽진크기','양성판정'], m => RUNS.map(r => CA[r] ? CA[r][m] : null)));
const groups = D.groups;
charts.insertAdjacentHTML('beforeend', barChart('양식(검사기관)별 수치·팽진 정확도 (%)', groups.map(g=>g.key), groups.map(g=>g.label),
  g => RUNS.map(r => { const x = D.group_agg[g] && D.group_agg[g][r]; return x ? (x.value_acc ?? x.size_acc) : null; })));
charts.insertAdjacentHTML('beforeend', barChart('양식별 행 재현율 (%)', groups.map(g=>g.key), groups.map(g=>g.label),
  g => RUNS.map(r => { const x = D.group_agg[g] && D.group_agg[g][r]; return x ? x.recall : null; })));
charts.insertAdjacentHTML('beforeend', barChart('양식별 양성 누락 (건)', groups.map(g=>g.key), groups.map(g=>g.label),
  g => RUNS.map(r => { const x = D.group_agg[g] && D.group_agg[g][r]; return x ? x.missed_positive : null; }), false));

function barChart(title, keys, labels, getter, isPct=true){
  const W=520, H=40+keys.length*34, left=120, right=40;
  const vals = keys.map(k=>getter(k));
  const max = isPct ? 100 : Math.max(1, ...vals.flat().filter(v=>v!=null));
  const x = v => left + (W-left-right) * (v/max);
  let s = `<div class="chart"><h3 style="margin:0 0 6px">${esc(title)}</h3><svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(title)}">`;
  [0,25,50,75,100].forEach(t=>{ if(!isPct && t>0) return; const v = isPct ? t : 0; s += `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="20" y2="${H-14}"/><text x="${x(v)}" y="${H-2}" text-anchor="middle">${isPct?t:''}</text>`;});
  keys.forEach((k,i)=>{
    const y0 = 24 + i*34;
    s += `<text x="${left-8}" y="${y0+16}" text-anchor="end">${esc(labels[i])}</text>`;
    RUNS.forEach((r,ri)=>{
      const v = vals[i][ri]; if(v==null) return;
      const y = y0 + ri*14, w = Math.max(0, x(v)-left);
      s += `<rect x="${left}" y="${y}" width="${w}" height="12" rx="3" fill="${COL[r]}" data-t="${esc(LBL[r])}: ${isPct?pct(v):fmt(v)}"/>`
         + `<text x="${left+w+4}" y="${y+10}">${isPct?pct(v):fmt(v)}</text>`;
    });
  });
  s += `</svg><div class="legend">${RUNS.map(r=>`<span><i style="background:${COL[r]}"></i>${esc(LBL[r])}</span>`).join('')}</div></div>`;
  return s;
}
document.addEventListener('mousemove', e => { const t = e.target.closest('[data-t]'); if(t) showTip(e, esc(t.dataset.t)); else hideTip(); });

// ---- 3. 일치도
const ag = D.agreement_aggregate;
document.getElementById('agree').innerHTML = `<div class="tiles">
 ${tile('행 집합 일치(Jaccard)', pct(ag.row_jaccard), `${ag.matched}행 공통`, 'x')}
 ${tile('수치 일치', pct(ag.value_agree), 'MAST 공통 행', 'x')}
 ${tile('class 일치', pct(ag.class_agree), 'MAST 공통 행', 'x')}
 ${tile('팽진 크기 일치', pct(ag.size_agree), 'SPT 공통 행', 'x')}
 ${tile('양성 판정 일치', pct(ag.positivity_agree), `κ = ${fmt(ag.positivity_kappa)} · 공통 행만`, 'x')}
 ${tile('한쪽만 class 미판독', fmt(ag.cls_one_missing), 'class 일치 분모에서 제외', 'x')}
 ${tile('양쪽 다 맞음 / 한쪽만 / 둘 다 틀림', `${ag.both_right} / ${ag.one_right} / ${ag.both_wrong}`, '정답 기준 양성 판정, 공통 행', 'x')}
</div><p>${esc(D.agreement_note)}</p>`;

// ---- 4. 사진별 표
const T = document.getElementById('per-image');
let th = `<tr><th>id</th><th>양식</th><th>종류</th><th class="num">정답행</th>`;
RUNS.forEach(r => th += `<th class="num" style="color:${COL[r]}">읽은행</th><th class="num" style="color:${COL[r]}">재현율</th><th class="num" style="color:${COL[r]}">정밀도</th><th class="num" style="color:${COL[r]}">수치/크기</th><th class="num" style="color:${COL[r]}">class</th><th class="num" style="color:${COL[r]}">양성누락/위양성</th><th class="num" style="color:${COL[r]}">초</th>`);
th += `</tr>`;
let rows = '';
D.images.forEach(im => {
  rows += `<tr onclick="location.hash='d-${im.id}'" style="cursor:pointer"><td class="mono">${im.id}</td><td>${esc(im.lab)}<br><small>${esc(im.layout)}</small></td><td>${im.test_type}${im.page_kind==='summary'?'<br><small>요약페이지</small>':''}</td><td class="num">${im.truth_rows}</td>`;
  RUNS.forEach(r => {
    const s = im.scores[r];
    if(!s){ rows += `<td colspan="7"><small>없음</small></td>`; return; }
    if(s.error){ rows += `<td colspan="7" class="bad"><small>${esc(s.error)}</small></td>`; return; }
    const acc = im.test_type==='SPT' ? s.size_acc : s.value_acc;
    rows += `<td class="num">${s.ocr_rows}</td><td class="num ${cls(s.recall)}">${pct(s.recall)}</td><td class="num ${cls(s.precision)}">${pct(s.precision)}</td><td class="num ${cls(acc)}">${pct(acc)}</td><td class="num ${cls(s.class_acc)}">${pct(s.class_acc)}</td><td class="num ${(s.missed_positive||s.false_positive)?'bad':''}">${s.missed_positive}/${s.truth_positive} · ${s.false_positive}</td><td class="num">${fmt(s.seconds)}</td>`;
  });
  rows += `</tr>`;
});
T.innerHTML = th + rows;
function cls(v){ return v==null?'':(v>=99?'good':(v<90?'bad':'')); }

// ---- 5. 상세
const det = document.getElementById('details');
D.images.forEach(im => {
  const runsHere = RUNS.filter(r => im.scores[r] && !im.scores[r].error);
  const allRows = {};
  // union of rows keyed by truth no/name
  runsHere.forEach(r => im.scores[r].rows.forEach(row => {
    const key = row.truth ? ('t:' + (row.truth.printed_no||'') + '|' + (row.truth.allergen_name||'')) : ('x:' + r + '|' + (row.ocr.printed_no||'') + '|' + (row.ocr.allergen_name||''));
    allRows[key] = allRows[key] || {no: row.no, truth: row.truth, ocr: {}, errs: {}, flagged:{}};
    allRows[key].ocr[r] = row.ocr; allRows[key].errs[r] = row.errors; allRows[key].flagged[r] = row.flagged;
    if(row.truth) allRows[key].truth = row.truth;
  }));
  const list = Object.values(allRows).sort((a,b)=> (parseInt(a.no)||1e6) - (parseInt(b.no)||1e6));
  const p = im.scores[runsHere[0]] ? im.scores[runsHere[0]].patient : null;
  let t = `<table><tr><th>No</th><th>정답 (사람 전사)</th>`;
  RUNS.forEach(r => t += `<th style="color:${COL[r]}">${esc(LBL[r].split(' — ')[0])}</th>`);
  t += `</tr>`;
  list.forEach(row => {
    t += `<tr><td class="mono">${esc(row.no||'')}</td><td>${cell(row.truth, im.test_type, [])}</td>`;
    RUNS.forEach(r => {
      const o = row.ocr[r], e = row.errs[r] || [];
      if(o === undefined && !(im.scores[r] && !im.scores[r].error)) { t += `<td><small>—</small></td>`; return; }
      t += `<td class="${e.length? 'bad' : (row.flagged[r] ? 'warn' : '')}">${o ? cell(o, im.test_type, e) : '<span class="chip">행 못 찾음</span>'}${e.filter(x=>x!=='missed_row').map(x=>`<span class="chip">${esc(ERR[x]||x)}</span>`).join('')}</td>`;
    });
    t += `</tr>`;
  });
  t += `</table>`;
  let pt = '';
  if(p){
    pt = `<table><tr><th>환자 정보</th><th>정답</th>` + RUNS.map(r=>`<th style="color:${COL[r]}">${esc(LBL[r].split(' — ')[0])}</th>`).join('') + `</tr>`;
    ['name','age','gender','test_date','report_date','patient_id_external','facility'].forEach(k => {
      const tk = k==='patient_id_external' ? 'patient_id' : k;
      const tv = k==='test_date' ? (p.truth.test_date || p.truth.collect_date) : (k==='patient_id_external' ? (p.truth.patient_id_external) : (k==='facility' ? (im.lab + (p.truth.ordering_facility? ' / '+p.truth.ordering_facility:'')) : p.truth[k]));
      pt += `<tr><td>${k}</td><td>${esc(fmt(tv))}</td>` + RUNS.map(r => { const s = im.scores[r]; if(!s||s.error) return '<td>—</td>'; const ok = s.patient[tk]; return `<td class="${ok?'':'bad'}">${esc(fmt(s.patient.ocr[k]))}</td>`; }).join('') + `</tr>`;
    });
    pt += `</table>`;
  }
  const meta = RUNS.map(r => { const s = im.scores[r]; if(!s) return ''; if(s.error) return `<div class="kv" style="color:var(--bad)">${esc(LBL[r])}: 오류 — ${esc(s.error)}</div>`;
      const dr = s.double_read && s.double_read.error ? ' · 두 번째 판독 실패' : ''; const rv = s.review ? ` · 검증표시 ${s.review.flagged_rows}행` : '';
      return `<div class="kv"><b style="color:${COL[r]}">${esc(LBL[r].split(' — ')[0])}</b> ${esc(s.model||'')} · ${fmt(s.seconds)}초 · 검사종류 ${esc(s.test_type_got)}${s.test_type_ok?'':' ✗'}${dr}${rv}</div>`; }).join('');
  const noteShort = im.notes ? (im.notes.length > 90 ? im.notes.slice(0, 90) + '…' : im.notes) : '';
  det.insertAdjacentHTML('beforeend', `<details id="d-${im.id}"><summary>${im.id} · ${esc(im.lab)} · ${esc(im.layout)}<span class="meta">${im.test_type} · 정답 ${im.truth_rows}행 · 양성 ${im.truth_positive}건${noteShort? ' · '+esc(noteShort):''}</span></summary>
   <div class="detail"><div><img loading="lazy" src="img/${im.id}.jpg" alt="${im.id}"><div class="kv mono">${esc(im.file)}</div>${meta}${im.notes?`<div class="kv" style="margin-top:6px"><b>전사 메모</b> ${esc(im.notes)}</div>`:''}</div><div>${pt}<div style="height:8px"></div><div class="tbl-wrap">${t}</div></div></div></details>`);
});
function cell(o, tt, errs){
  if(!o) return '<small>—</small>';
  const name = esc(o.allergen_name||'') + (o.korean_name? ` <small>${esc(o.korean_name)}</small>`:'') + (o.code? ` <small class="mono">${esc(o.code)}</small>`:'');
  const val = tt==='SPT' ? (o.size_text ? `<b>${esc(o.size_text)}</b>` + (o.mean_mm!=null?` <small>(${o.mean_mm})</small>`:'') : '<small>빈칸</small>')
     : `<b>${esc(o.value_text ?? (o.value!=null ? o.value : '—'))}</b>` + (o.class_value!=null ? ` <small>class ${esc(o.class_value)}</small>`:'');
  const extra = (o.uncertain? ' <span class="chip info">불확실</span>':'') + (o.note? ` <small>${esc(o.note)}</small>`:'') + (o.review_flags? ` <small class="mono">${esc(o.review_flags.join(','))}</small>`:'');
  return `${name}<br>${val}${extra}`;
}

// ---- 6/7. 오류 포인트 · 고도화 방안
const EX = D.taxonomy;
const errDiv = document.getElementById('errors');
let et = `<div class="tbl-wrap"><table><tr><th>오류 종류</th>` + RUNS.map(r=>`<th class="num" style="color:${COL[r]}">${esc(LBL[r].split(' — ')[0])}</th>`).join('') + `<th>예시</th></tr>`;
const keys = [...new Set(RUNS.flatMap(r => Object.keys(EX[r].counts)))].sort((a,b)=> (EX[RUNS[0]].counts[b]||0)-(EX[RUNS[0]].counts[a]||0));
const TAXL = {doc_error:'문서 처리 실패', test_type:'검사종류 오판', double_read_error:'두 번째 판독 실패', missed_row:'행 누락', extra_row:'정답에 없는 행(지어낸 행)', duplicate_row:'중복행(두 번 읽기 병합 복제)', duplicate_conflict:'중복행인데 값이 다름', value:'수치 오독', lod_dropped:"'<' 미만표기를 숫자로", x10:'10배 오독', class:'class 오독', class_missing:'class 미판독(칸을 비움)', size:'팽진 크기 오독', missed_positive:'양성 누락(임상)', false_positive:'위양성(임상)', name_fuzzy:'항원명 불일치(유사 매칭)', no_mismatch:'행 번호 밀림', unverifiable:'검증 불가(정답 전사도 불확실)', name_misread:'이름 오독(번호는 일치)', duplicate_name_misread:'중복행(번호·값 같고 이름만 오독)'};
keys.forEach(k => { et += `<tr><td>${esc(TAXL[k]||k)}<br><small class="mono">${k}</small></td>` + RUNS.map(r=>`<td class="num">${EX[r].counts[k]||0}</td>`).join('') + `<td><small>${RUNS.map(r => (EX[r].examples[k]||[]).slice(0,2).map(x=>esc(x)).join('<br>')).filter(Boolean).join('<br>')}</small></td></tr>`; });
et += `</table></div>`;
errDiv.innerHTML = et + (D.findings||[]).map(f => `<div class="finding"><h4>${esc(f.title)}</h4><p>${f.body}</p>${f.evidence?`<div class="ev">근거: ${f.evidence}</div>`:''}</div>`).join('');
document.getElementById('recs').innerHTML = (D.recommendations||[]).map((f,i) => `<div class="finding rec"><h4>${i+1}. ${esc(f.title)}</h4><p>${f.body}</p>${f.evidence?`<div class="ev">${f.evidence}</div>`:''}</div>`).join('');
document.getElementById('repro').textContent = D.repro;

// ---- 7-2. 코드 리뷰
const RV = D.review || [];
if (RV.length) {
  const SEV = {'높음':'bad','중간':'warn','낮음':''};
  document.getElementById('review').innerHTML = `<div class="tbl-wrap"><table><tr><th>심각도</th><th>결함</th><th>영향</th><th>처리</th></tr>` +
    RV.map(r => `<tr><td class="${SEV[r.severity]||''}">${esc(r.severity)}</td><td><b>${esc(r.title)}</b><br><small class="mono">${esc(r.where||'')}</small></td><td>${r.body}</td><td class="${r.status&&r.status.startsWith('고침')?'good':''}">${esc(r.status||'')}</td></tr>`).join('') + `</table></div>` + (D.review_note ? `<p>${D.review_note}</p>` : '');
}

// ---- 7-1. 개선 실험
const EXP = D.experiments || {}, EXL = D.experiment_labels || {};
const expRuns = Object.keys(EXP);
if (expRuns.length) {
  const baseOf = r => r.startsWith('ollama') ? 'ollama' : 'openai';
  const bases = [...new Set(expRuns.map(baseOf))];
  let h = '';
  bases.forEach(base => {
  const myRuns = expRuns.filter(r => baseOf(r) === base);
  const ids = [...new Set(myRuns.flatMap(r => Object.keys(EXP[r])))].sort();
  const METR = [['precision','정밀도'],['recall','재현율'],['value_acc','수치'],['duplicate_rows','중복행'],['extra_rows','없는행'],['no_mismatch','행번호 밀림'],['missed_positive','양성누락'],['false_positive','위양성']];
  h += `<div class="tbl-wrap"><table><tr><th>id</th><th>지표</th><th>기존 (${esc(LBL[base]||base)})</th>` + myRuns.map(r=>`<th>${esc(EXL[r]||r)}</th>`).join('') + `</tr>`;
  ids.forEach(id => {
    const im = D.images.find(x => x.id === id); const b = im && im.scores[base];
    METR.forEach(([k,label], i) => {
      const isPct = k.endsWith('_acc') || k==='precision' || k==='recall';
      const bv = b && !b.error ? b[k] : null;
      h += `<tr>${i===0?`<td rowspan="${METR.length}" class="mono">${id}</td>`:''}<td>${label}</td><td class="num">${isPct?pct(bv):fmt(bv)}</td>`
        + myRuns.map(r => { const s = EXP[r][id]; if(!s) return '<td class="num">—</td>'; const v = s[k]; const better = v!=null && bv!=null && (isPct ? v>bv : v<bv); const worse = v!=null && bv!=null && (isPct ? v<bv : v>bv); return `<td class="num ${better?'good':(worse?'bad':'')}">${isPct?pct(v):fmt(v)}</td>`; }).join('') + `</tr>`;
    });
  });
  h += `</table></div>`;
  });
  document.getElementById('experiments').innerHTML = h;
}
</script>
</body>
</html>
"""


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
LAYOUT = {
    "이원의료재단": "2단 표 · 코드 괄호 · 결과(IU/mL)+Class · 촬영(누움)",
    "GC Labs": "2단 표 No 1~108 · Class+IU/mL · 1/3 요약페이지 포함",
    "씨젠의료재단": "No+코드[H1]+막대그래프 class (또는 세부항목 저화질)",
    "삼광의료재단": "한글명+영문명 2단 · 108/120종 · '<0.35' 표기",
    "신원의료재단": "구분·No·Code·한글·영문·IU/mL·Class · 성분/약물 항원",
    "분당서울대학교병원": "SPT 손기입 양식 [a]X[b] 평균",
    "EMR": "EMR 화면 캡처(텍스트/표)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="openai,ollama")
    ap.add_argument("--out", default=str(DOC_DIR))
    args = ap.parse_args()
    runs = [r.strip() for r in args.runs.split(",") if r.strip()]
    out_dir = Path(args.out)
    (out_dir / "img").mkdir(parents=True, exist_ok=True)

    manifest = json.loads((EVAL_DIR / "manifest.json").read_text(encoding="utf-8"))["items"]
    matcher = Matcher()
    images: List[Dict[str, Any]] = []
    scores: Dict[str, Dict[str, Any]] = {}
    agree: Dict[str, Any] = {}
    from PIL import Image, ImageOps
    for it in manifest:
        tp = EVAL_DIR / "truth" / f"{it['id']}.json"
        if not tp.exists():
            print(f"  정답 없음: {it['id']} — 건너뜀")
            continue
        truth = json.loads(tp.read_text(encoding="utf-8"))
        if truth.get("file") and truth["file"] != it["file"]:
            sys.exit(f"정답 {it['id']} 의 file({truth['file']}) 이 manifest({it['file']}) 와 다르다 — 사진을 추가한 뒤 id 가 밀렸는지 확인")
        run_data = {}
        for r in runs:
            rp = EVAL_DIR / "runs" / r / f"{it['id']}.json"
            if rp.exists():
                run_data[r] = json.loads(rp.read_text(encoding="utf-8"))
        sc = {r: score_image(truth, run_data[r], matcher) for r in run_data}
        scores[it["id"]] = sc
        if len(runs) >= 2 and runs[0] in run_data and runs[1] in run_data:
            agree[it["id"]] = agreement(truth, run_data[runs[0]], run_data[runs[1]], matcher)
        lab = truth.get("lab") or ("EMR" if it["id"] in ("O02", "O03", "O04", "O05") else "?")
        if lab not in LAYOUT:
            lab_key = "EMR" if "EMR" in lab or it["id"].startswith("O0") and it["id"] != "O01" else lab
        else:
            lab_key = lab
        images.append({
            "id": it["id"], "file": it["file"], "lab": lab, "layout": LAYOUT.get(lab_key, ""),
            "group": lab_key, "test_type": truth["test_type"], "page_kind": truth.get("page_kind"),
            "truth_rows": len(truth.get("results") or []),
            "truth_positive": sum(1 for r in truth.get("results") or [] if is_positive(r, truth["test_type"], _num((truth.get("patient") or {}).get("histamine_mean_mm")))),
            "uncertain_rows": sum(1 for r in truth.get("results") or [] if r.get("uncertain")),
            "notes": _mask_notes(truth.get("notes"), truth), "landscape": it["landscape"],
            "scores": {r: {k: v for k, v in s.items() if k != "pos_pairs"} for r, s in sc.items()},
        })
        # 리포트용 축소 사본(세로 정규화)
        im = ImageOps.exif_transpose(Image.open(ROOT / "input_images" / it["file"])).convert("RGB")
        if it["id"].startswith("A") and im.width > im.height:
            im = im.rotate(-90, expand=True)
        im.thumbnail((1000, 1400), Image.LANCZOS)
        im.save(out_dir / "img" / f"{it['id']}.jpg", quality=80)

    agg = {r: aggregate([scores[i][r] for i in scores if r in scores[i]]) for r in runs}
    common_ids = [i for i in scores if all(r in scores[i] and "error" not in scores[i][r] for r in runs)]
    agg_common = {r: aggregate([scores[i][r] for i in common_ids]) for r in runs} if len(runs) >= 2 else {}
    group_keys = []
    group_agg: Dict[str, Dict[str, Any]] = {}
    for im in images:
        if im["group"] not in group_keys:
            group_keys.append(im["group"])
    for g in group_keys:
        group_agg[g] = {r: aggregate([scores[im["id"]][r] for im in images if im["group"] == g and r in scores[im["id"]]]) for r in runs}

    # 두 백엔드 일치도 집계
    ag_ok = [a for a in agree.values() if not a.get("error")]
    pos_pairs = [p for a in ag_ok for p in a["pos_pairs"]]
    both_right = one_right = both_wrong = 0
    if len(runs) >= 2:
        for img, sc in scores.items():
            a, b = sc.get(runs[0]), sc.get(runs[1])
            if not a or not b or "error" in a or "error" in b:
                continue
            # 정답 기준: 같은 정답 행을 두 백엔드가 모두 찾은 경우의 양성 판정
            ra = {(_rk(r["truth"])): r for r in a["rows"] if r.get("truth") and r.get("ocr")}
            rb = {(_rk(r["truth"])): r for r in b["rows"] if r.get("truth") and r.get("ocr")}
            for k in set(ra) & set(rb):
                x = ra[k]["truth_pos"] == ra[k]["ocr_pos"]
                y = rb[k]["truth_pos"] == rb[k]["ocr_pos"]
                both_right += x and y; both_wrong += (not x) and (not y); one_right += x != y
    def ratio_sum(num, den):
        a_, b_ = sum(a[num] for a in ag_ok), sum(a[den] for a in ag_ok)
        return round(100.0 * a_ / b_, 1) if b_ else None
    agreement_aggregate = {
        "images": len(ag_ok), "matched": sum(a["matched"] for a in ag_ok),
        "row_jaccard": ratio_sum("matched", "union"), "value_agree": ratio_sum("same_val", "val_pairs"),
        "class_agree": ratio_sum("same_cls", "cls_pairs"), "size_agree": ratio_sum("same_size", "size_pairs"),
        "cls_one_missing": sum(a["cls_one_missing"] for a in ag_ok),
        "positivity_agree": round(100.0 * sum(1 for x, y in pos_pairs if x == y) / len(pos_pairs), 1) if pos_pairs else None,
        "positivity_kappa": kappa(pos_pairs),
        "both_right": both_right, "one_right": one_right, "both_wrong": both_wrong,
    }

    # 개선 실험(before/after) — 같은 사진을 회전 보정/중복 제거 파이프라인으로 다시 돌린 결과
    experiments: Dict[str, Dict[str, Any]] = {}
    for exp in ("openai_rotated", "openai_v2", "openai_luna", "ollama_v2"):
        exp_dir = EVAL_DIR / "runs" / exp
        if not exp_dir.exists():
            continue
        for p in sorted(exp_dir.glob("*.json")):
            img_id = p.stem
            tp = EVAL_DIR / "truth" / f"{img_id}.json"
            if not tp.exists():
                continue
            truth = json.loads(tp.read_text(encoding="utf-8"))
            sc = score_image(truth, json.loads(p.read_text(encoding="utf-8")), matcher)
            experiments.setdefault(exp, {})[img_id] = {k: v for k, v in sc.items() if k not in ("rows", "pos_pairs", "patient", "review", "double_read")}

    taxonomy = error_taxonomy(scores, runs)
    findings_path = out_dir / "findings.json"
    findings = json.loads(findings_path.read_text(encoding="utf-8")) if findings_path.exists() else {}

    data = {
        "generated": __import__("datetime").date.today().isoformat(),
        "runs": runs, "run_labels": {r: RUN_LABELS.get(r, r) for r in runs},
        "lede": findings.get("lede") or f"실제 결과지 사진 {len(images)}장을 두 OCR 백엔드로 읽고, 사람이 전사한 정답과 행 단위로 대조했다.",
        "flow": findings.get("flow") or ["input_images/ 29장", "정답 전사(사람, 4분할 확대본)", "OCR ×2 백엔드(전처리+두 번 읽기+검증)",
                                         "행 대조(번호→별칭→유사도)", "채점·일치도", "이 리포트"],
        "method": findings.get("method") or "",
        "aggregate": agg, "aggregate_common": agg_common, "common_images": common_ids,
        "groups": [{"key": g, "label": g} for g in group_keys], "group_agg": group_agg,
        "agreement_aggregate": agreement_aggregate, "agreement": agree,
        "agreement_note": findings.get("agreement_note") or "κ(코헨 카파)는 우연히 일치할 확률을 뺀 일치도다. 두 모델이 같은 행에서 같은 양성/음성 판정을 내린 비율과 함께 본다.",
        "images": images, "taxonomy": taxonomy, "experiments": experiments,
        "experiment_labels": {"openai_rotated": "회전 보정만(수동)", "openai_v2": "자동 회전 + 병합 중복 제거", "openai_luna": "gpt-6-luna (수정 파이프라인, 모델 교체)", "ollama_v2": "Local: 자동 회전 + 중복 제거"},
        "findings": findings.get("findings") or [], "recommendations": findings.get("recommendations") or [],
        "review": findings.get("review") or [], "review_note": findings.get("review_note") or "",
        "repro": findings.get("repro") or "python3 scripts/eval_real_ocr.py manifest\npython3 scripts/eval_real_ocr.py run --backend openai --workers 3\npython3 scripts/eval_real_ocr.py run --backend ollama --workers 1\npython3 scripts/eval_real_ocr_report.py --runs openai,ollama",
    }
    (out_dir / "summary.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "index.html").write_text(render_html(data), encoding="utf-8")

    print(f"\n리포트: {out_dir / 'index.html'}")
    for r in runs:
        a = agg[r]
        print(f"\n[{RUN_LABELS.get(r, r)}] {a['images']}장 (오류 {a['errors']})")
        print(f"  재현율 {a['recall']}%  정밀도 {a['precision']}%  수치 {a['value_acc']}% ({a['value_n']})  class {a['class_acc']}% ({a['class_n']})  팽진 {a['size_acc']}% ({a['size_n']})")
        print(f"  양성 누락 {a['missed_positive']}/{a['truth_positive']}  위양성 {a['false_positive']}  10배 {a['x10']}  양성판정 일치 {a['positivity_agree']}% κ={a['positivity_kappa']}")
        print(f"  검사종류 {a['test_type_ok']}/{a['images'] - a['errors']}  환자명 {a['patient']['name']}%  검사일 {a['patient']['test_date']}%  장당 {a['seconds_mean']}초")
        print(f"  검증표시: 임상오류 {a['clin_err_flagged']}/{a['clin_err_rows']} 포착 · 맞는 행 {a['ok_flagged']}/{a['ok_rows']} 표시")
    print(f"\n[모델 간 일치] 행 Jaccard {agreement_aggregate['row_jaccard']}%  수치 {agreement_aggregate['value_agree']}%  class {agreement_aggregate['class_agree']}%  팽진 {agreement_aggregate['size_agree']}%  양성판정 {agreement_aggregate['positivity_agree']}% κ={agreement_aggregate['positivity_kappa']}  둘다맞음/한쪽/둘다틀림 {both_right}/{one_right}/{both_wrong}")


def _rk(t: Dict[str, Any]) -> str:
    return f"{no_key(t.get('printed_no')) or ''}|{norm(t.get('allergen_name'))}"


if __name__ == "__main__":
    main()
