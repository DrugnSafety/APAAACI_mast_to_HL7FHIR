#!/usr/bin/env python3
"""추가 OCR(Claude 직접 판독, gpt-5.6-luna 부분 실행)을 기존 gpt-5.4·qwen 결과와 같은 채점기로 비교한다.

입력: output/ocr_eval_2026-10-09/{truth,runs/<run>}/<id>.json  (채점기: scripts/eval_real_ocr_report.py)
출력: evaluation_claude.json (환자 정보·사진 없음), claude_compare.html (로컬 비교용, 사진 포함 — gitignore)
기존 evaluation.json·index/cases/models/test_types.html 은 건드리지 않는다.
"""
from __future__ import annotations

import ast
import base64
import html
import json
import sys
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_eval_data as b  # noqa: E402  (e = 채점기, 정렬·엄격 지표 함수 재사용)

e = b.e
ROOT, SOURCE = b.ROOT, b.SOURCE

RUNS = [
    {"key": "openai", "dir": "openai", "label": "gpt-5.4", "vendor": "OpenAI", "color": "#146d9d",
     "note": "2026-10-09 기준 실행 · 전처리 + 두 번 읽기"},
    {"key": "ollama", "dir": "ollama", "label": "qwen3.8:27b", "vendor": "Local", "color": "#a96716",
     "note": "2026-10-09 기준 실행 · 연구실 Ollama · 두 번 읽기"},
    {"key": "luna", "dir": "openai_gpt56luna_20261029", "label": "gpt-5.6-luna", "vendor": "OpenAI", "color": "#6b5bb5",
     "note": "2026-10-29 추가 실행 · 앱 현재 예산 모델 · 23/29장(키 401로 6장 실패)"},
    {"key": "claude", "dir": "claude_opus55_20261029", "label": "Claude 직접 판독", "vendor": "Claude", "color": "#d38a5c",
     "note": "2026-10-29 · Aside 에이전트(opus-5-5)가 앱 프롬프트로 직접 판독(맹검) → 앱 후처리 · 한 번 읽기"},
    {"key": "claude_cli", "dir": "claude_cli_opus_20261029", "label": "Claude opus-5-5 (앱 파이프라인)", "vendor": "Claude", "color": "#b5442a",
     "note": "2026-10-29 · 사용자 Claude 계정(Claude Code CLI) · 앱 OCR 경로 그대로(전처리 + 두 번 읽기)"},
]
PRIMARY = "claude_cli"
KEYS = [r["key"] for r in RUNS]
WRONG = {"value", "class", "class_missing", "size", "missed_positive", "false_positive", "missed_row", "x10", "lod_dropped"}


def read(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def align(truth_rows, score):
    """정답 행 i → 채점 행. 정답 행 전체(_slim) 를 열쇠로 — build_eval_data.align_rows 와 같은 방식."""
    if "error" in score:
        return {i: {"ocr": None, "errors": ["doc_error"], "how": "doc_error"} for i in range(len(truth_rows))}, []
    queues = defaultdict(deque)
    extras = []
    for row in score["rows"]:
        if row.get("how") in {"duplicate", "extra"}:
            extras.append(b.projected_score_row(row))
        elif row.get("truth") is not None:
            queues[b.truth_key(row["truth"])].append(row)
    out = {}
    for i, t in enumerate(truth_rows):
        q = queues.get(b.truth_key(t))
        if not q:
            raise AssertionError(f"reference row {i + 1} not aligned")
        out[i] = q.popleft()
    assert not sum(len(q) for q in queues.values())
    return out, extras


def row_ok(r: dict[str, Any] | None) -> bool | None:
    if r is None or r.get("how") == "doc_error":
        return None
    errs = set(r.get("errors") or [])
    if "unverifiable" in errs:
        return None
    return r.get("ocr") is not None and not (errs & WRONG)


def agg(scores, stricts):
    out = {k: v for k, v in e.aggregate(scores).items() if k != "patient"}
    out["strict"] = b.strict_aggregate(stricts) if stricts else None
    return out


def main() -> None:
    manifest = read(SOURCE / "manifest.json")["items"]
    matcher = e.Matcher()
    docs = []
    per = {k: {} for k in KEYS}           # doc → score
    per_strict = {k: {} for k in KEYS}
    available = {}
    for it in manifest:
        did = it["id"]
        truth = read(SOURCE / "truth" / f"{did}.json")
        t_rows = truth.get("results") or []
        hist = e._num((truth.get("patient") or {}).get("histamine_mean_mm"))
        runs = {}
        for r in RUNS:
            p = SOURCE / "runs" / r["dir"] / f"{did}.json"
            runs[r["key"]] = read(p) if p.exists() else {"id": did, "error": "not run"}
        scores = {k: e.score_image(truth, runs[k], matcher) for k in KEYS}
        aligned, extras, strict = {}, {}, {}
        for k in KEYS:
            aligned[k], extras[k] = align(t_rows, scores[k])
            strict[k] = b.strict_score(truth, aligned[k])
            per[k][did] = scores[k]
            per_strict[k][did] = strict[k]
        rows = []
        for i, t in enumerate(t_rows):
            row = {"uid": f"{did}-r{i + 1:04d}", "reference": b.clean_row(e._slim(t)),
                   "truth_pos": e.is_positive(t, truth["test_type"], hist)}
            for k in KEYS:
                a = aligned[k][i]
                row[k] = None if a.get("how") == "doc_error" else b.projected_score_row(a)
            rows.append(row)
        lab = truth.get("lab") or ("EMR" if did in {"O02", "O03", "O04", "O05"} else "?")
        ctrl = {"truth": hist}
        for k in KEYS:
            ctrl[k] = e._num(((runs[k].get("result") or {}).get("patient") or {}).get("histamine_mean_mm")) if truth["test_type"] == "SPT" else None
        docs.append({
            "id": did, "file": it["file"], "test_type": truth["test_type"], "lab": lab,
            "truth_rows": len(t_rows),
            "truth_positive": sum(e.is_positive(r, truth["test_type"], hist) is True for r in t_rows),
            "uncertain_rows": sum(bool(r.get("uncertain")) for r in t_rows),
            "controls": ctrl,
            "scores": {k: ({kk: vv for kk, vv in scores[k].items() if kk not in b.DROP_SCORE_FIELDS} | {"strict": strict[k]}) for k in KEYS},
            "rows": rows, "extras": extras,
        })
        available[did] = [k for k in KEYS if "error" not in scores[k]]

    ids_all = [d["id"] for d in docs]
    common = [d for d in ids_all if set(available[d]) == set(KEYS)]

    def group(ids):
        return {k: agg([per[k][d] for d in ids], [per_strict[k][d] for d in ids if "error" not in per[k][d]]) for k in KEYS}

    by_type = {t: {"documents": [d["id"] for d in docs if d["test_type"] == t]} for t in ("MAST", "SPT")}
    for t, g in by_type.items():
        g["aggregate"] = group(g["documents"])

    # 행 단위 맞대결: Claude 가 맞고 다른 모델이 틀린 행 / 반대
    head2head = {}
    for other in [k for k in KEYS if k != PRIMARY]:
        c = {"both_ok": 0, "claude_only_ok": 0, "other_only_ok": 0, "both_wrong": 0}
        for d in docs:
            for r in d["rows"]:
                a, o = row_ok(r[PRIMARY]), row_ok(r[other])
                if a is None or o is None:
                    continue
                c["both_ok" if a and o else "claude_only_ok" if a else "other_only_ok" if o else "both_wrong"] += 1
        head2head[other] = c

    claude_errors = []
    for d in docs:
        for r in d["rows"]:
            cr = r[PRIMARY]
            if cr is None:
                continue
            errs = [x for x in (cr.get("errors") or []) if x in WRONG]
            if errs:
                claude_errors.append({"doc": d["id"], "uid": r["uid"], "errors": errs})
        for x in d["extras"][PRIMARY]:
            claude_errors.append({"doc": d["id"], "uid": None, "errors": x["errors"], "extra": x["ocr"]})

    meta_runs = []
    for r in RUNS:
        sample = None
        for d in ids_all:
            p = SOURCE / "runs" / r["dir"] / f"{d}.json"
            if p.exists():
                sample = read(p)
                if "error" not in sample:
                    break
        meta_runs.append(r | {"model": (sample or {}).get("model"), "documents_ok": sum(1 for d in ids_all if r["key"] in available[d]),
                              "double_read": (sample or {}).get("double_read"), "folder": f"output/ocr_eval_2026-10-09/runs/{r['dir']}"})

    data = {
        "meta": {"generated": date.today().isoformat(), "truth_rows": sum(d["truth_rows"] for d in docs),
                 "documents": len(docs), "common_documents": common, "runs": meta_runs, "primary": PRIMARY,
                 "reference_note": b.read_json(HERE / "evaluation.json")["meta"]["reference_note"]},
        "aggregate_all": group(ids_all),
        "aggregate_common": group(common),
        "by_type": by_type,
        "head2head": head2head,
        "claude_errors": claude_errors,
        "documents": docs,
    }
    (HERE / "evaluation_claude.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ---- 페이지: 기존 페이지와 같은 CSS(guide_base.css + build_pages.CSS) ----
    tree = ast.parse((HERE / "build_pages.py").read_text(encoding="utf-8"))
    consts = {n.targets[0].id: n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    css = (HERE / "guide_base.css").read_text(encoding="utf-8") + "\n" + consts["CSS"] + "\n" + EXTRA_CSS
    page = dict(data)
    page["images"] = {d["id"]: "data:image/jpeg;base64," + base64.b64encode((ROOT / "docs/ocr_eval_2026-10-09/img" / f"{d['id']}.jpg").read_bytes()).decode() for d in docs}
    blob = json.dumps(page, ensure_ascii=False, separators=(",", ":")).replace("</", r"<\/")
    nav = ''.join(f'<a href="{f}">{l}</a>' for f, l in [("index.html", "전체 성능"), ("cases.html", "사진 ↔ OCR"), ("test_types.html", "검사유형별"), ("models.html", "모델 비교")])
    nav += '<a href="claude_compare.html" aria-current="page">추가 OCR 비교</a>'
    doc = f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>추가 OCR(Claude·gpt-5.6-luna) 비교 · APAAACI OCR</title><style>{css}</style></head>
<body><a class="skip" href="#content">본문으로 이동</a><header class="topbar"><div class="topinner"><div class="brand"><span>A</span> APAAACI · OCR</div><nav class="topnav" aria-label="평가 페이지">{nav}</nav></div></header>
<main class="page" id="content">{BODY}</main>
<script type="application/json" id="evaluation-data">{blob}</script><script>{JS}</script></body></html>'''
    (HERE / "claude_compare.html").write_text(doc, encoding="utf-8")
    print("claude_compare.html", f"{len(doc.encode()):,} bytes", "| common docs", len(common))
    for k in KEYS:
        a = data["aggregate_all"][k]
        print(f"  {k:7s} ok {a['images'] - a['errors']}/29  recall {a['recall']}  value {a['value_acc']}  class {a['class_acc']}  missed+ {a['missed_positive']}/{a['truth_positive']}  dup {a['duplicate_rows']}  extra {a['extra_rows']}")


EXTRA_CSS = r'''
.cmp-table{width:100%;border-collapse:collapse;font-size:13px}.cmp-table th,.cmp-table td{border-bottom:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}.cmp-table td.num,.cmp-table th.num{text-align:right;font-variant-numeric:tabular-nums}
.cmp-table thead th{background:#f4f8fb;font-size:12px;position:sticky;top:0;z-index:1}.best{font-weight:800;color:#177b76}.worst{color:#ac303b}
.eng{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.cards4{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0}.card4{background:#fff;border:1px solid var(--line);border-radius:14px;padding:14px 16px;border-top:4px solid var(--c)}.card4 h3{margin:0;font-size:16px}.card4 small{color:var(--muted);display:block;min-height:34px}.card4 .big{font-size:26px;font-weight:800;color:var(--c)}.card4 .kv{display:flex;justify-content:space-between;font-size:12px;border-top:1px dashed var(--line);padding-top:5px;margin-top:5px}
.viewer{display:grid;grid-template-columns:minmax(260px,.75fr) minmax(0,1.6fr);gap:14px;align-items:start}.viewer .ph{position:sticky;top:80px;background:#fff;border:1px solid var(--line);border-radius:12px;overflow:auto;max-height:calc(100vh - 100px)}.viewer .ph img{width:100%;display:block}
.vt{max-height:calc(100vh - 100px);overflow:auto;border:1px solid var(--line);border-radius:12px;background:#fff}.vt td{font-size:12px}.vt td .v{font-weight:700}.vt tr.bad td{background:#fff7f0}.vt tr.crit td{background:#fff0f1}
.tagx{display:inline-block;font-size:10.5px;padding:1px 6px;border-radius:999px;background:#fdecec;color:#ac303b;margin:2px 2px 0 0}.tagok{background:#e4f3eb;color:#1f7a4d}
.ctrls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:10px 0}.ctrls select,.ctrls input{padding:6px 10px;border:1px solid var(--line);border-radius:8px}
.h2h{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.h2h div{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px}
@media(max-width:1000px){.cards4{grid-template-columns:repeat(2,1fr)}.viewer{grid-template-columns:1fr}.viewer .ph{position:static;max-height:420px}.h2h{grid-template-columns:1fr}}
'''

BODY = r'''
<header class="hero"><div class="eyebrow">APAAACI · OCR EVALUATION · 추가 실행 2026-10-29</div><h1>추가 OCR을 기존 결과와 비교합니다.</h1><p>Claude(사용자 계정, 앱 파이프라인)·Claude 직접 판독·앱의 현재 예산 모델(gpt-5.6-luna)을 기존 gpt-5.4·qwen3.8:27b 결과와 같은 참조 전사·같은 채점기로 비교했습니다.</p><div class="chips" id="heroChips"></div></header>
<div class="note privacy">아래 사진 비교에는 이름·번호 등 개인 식별정보가 남은 원본 사진이 포함됩니다. 로컬 확인용이며 외부 공유 전 가리세요.</div>
<section class="section"><h2>엔진 요약</h2><p class="lede" id="cardsLede"></p><div class="cards4" id="cards"></div></section>
<section class="section"><div class="section-head"><h2>지표 비교</h2><div class="ctrls"><label>범위 <select id="scope"><option value="common">공통 성공 사진(공정 비교)</option><option value="all">전체 29장(실패 사진 제외 집계)</option><option value="MAST">MAST만</option><option value="SPT">SPT만</option></select></label></div></div><div class="tablewrap" id="metricTable"></div><p class="tiny muted" id="metricNote"></p></section>
<section class="section"><h2>행 단위 맞대결: Claude(앱 파이프라인) vs 다른 엔진</h2><p class="lede">같은 참조 행에서 한쪽만 맞았는지 셉니다. ‘맞음’은 행이 연결되고 수치·Class(또는 SPT 크기)·양성 판정이 모두 참조와 같은 경우입니다. 불확실 전사 행과 실행 실패 사진은 뺐습니다.</p><div class="h2h" id="h2h"></div></section>
<section class="section"><h2>사진별 결과</h2><p class="lede">각 칸: 대응 행/참조 행 · 수치 또는 크기 오류 · 양성 누락. 사진 ID를 누르면 아래 비교 화면이 열립니다.</p><div class="tablewrap" id="docTable"></div></section>
<section class="section" id="viewerSec"><div class="section-head"><h2 id="vTitle">사진 ↔ 엔진별 행 비교</h2></div>
<div class="ctrls"><label>사진 <select id="vDoc"></select></label><label>행 <select id="vFilter"><option value="all">전체 행</option><option value="issues">어느 엔진이든 오류</option><option value="claude">Claude(파이프라인) 오류</option><option value="claudeonly">Claude(파이프라인)만 다름</option></select></label><input type="search" id="vSearch" placeholder="항원 이름 검색"><span class="tiny muted" id="vCount"></span></div>
<div class="viewer"><div class="ph"><img id="vImg" alt="검사지 사진"></div><div class="vt"><table class="cmp-table" id="vTable"></table></div></div><div id="vExtras"></div></section>
<section class="section"><h2>방법과 한계</h2><ul class="lede" id="method"></ul></section>
<footer class="footer"><span>추가 OCR 비교 · 2026-10-29</span><a href="cases.html">기존 2개 모델 비교로 →</a></footer>
'''

JS = r'''
'use strict';
const D=JSON.parse(document.getElementById('evaluation-data').textContent);
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=(v,d=0)=>v===null||v===undefined?'—':Number(v).toLocaleString('ko-KR',{maximumFractionDigits:d,minimumFractionDigits:d});
const pct=v=>v===null||v===undefined?'—':fmt(v,1)+'%';
const R=D.meta.runs, K=R.map(r=>r.key), RM=Object.fromEntries(R.map(r=>[r.key,r]));
const dot=k=>'<span class="eng" style="background:'+RM[k].color+'"></span>';
const LBL={missed_positive:'양성 누락',false_positive:'위양성',missed_row:'행 누락',value:'수치',class:'Class',class_missing:'Class 미판독',size:'크기',x10:'10배',lod_dropped:'< 소실',duplicate_row:'중복',duplicate_conflict:'중복값 충돌',extra_row:'없는 행',name_misread:'이름 오독',name_fuzzy:'이름 유사',no_mismatch:'번호 차이',unverifiable:'불확실 전사',duplicate_name_misread:'중복 이름 오독'};
const WRONG=new Set(['value','class','class_missing','size','missed_positive','false_positive','missed_row','x10','lod_dropped']);
$('heroChips').innerHTML='<span>실제 스캔 '+D.meta.documents+'장</span><span>참조 전사 '+fmt(D.meta.truth_rows)+'행</span><span>공통 성공 '+D.meta.common_documents.length+'장</span>'+R.map(r=>'<span>'+esc(r.label)+' '+r.documents_ok+'/29</span>').join('');
const C=D.aggregate_common;
$('cardsLede').textContent='공통 성공 사진 '+D.meta.common_documents.length+'장 기준(gpt-5.6-luna가 6장 실패해 공정 비교를 위해 공통 사진만 셉니다).';
$('cards').innerHTML=R.map(r=>{const a=C[r.key];return '<div class="card4" style="--c:'+r.color+'"><h3>'+esc(r.label)+'</h3><small>'+esc(r.note)+'</small><div class="big">'+pct(a.recall)+'</div><div class="tiny muted">행 재현율 '+fmt(a.matched)+'/'+fmt(a.truth_rows)+'</div><div class="kv"><span>수치 일치</span><b>'+pct(a.value_acc)+'</b></div><div class="kv"><span>Class 일치</span><b>'+pct(a.class_acc)+'</b></div><div class="kv"><span>양성 누락</span><b>'+a.missed_positive+' / '+a.truth_positive+'</b></div><div class="kv"><span>위양성</span><b>'+a.false_positive+'</b></div><div class="kv"><span>중복·없는 행</span><b>'+a.duplicate_rows+' · '+a.extra_rows+'</b></div></div>'}).join('');
const METRICS=[['성공 사진',a=>(a.images-a.errors)+' / '+a.images,null],['행 재현율',a=>a.recall,'hi'],['누락 행',a=>a.missed_rows,'lo'],['중복 행',a=>a.duplicate_rows,'lo'],['없는 행(추가)',a=>a.extra_rows,'lo'],['수치 일치율(대응 행)',a=>a.value_acc,'hi'],['참조 수치 복원율(누락 포함)',a=>a.strict?.numeric_accuracy_overall,'hi'],['Class 일치율(읽은 행)',a=>a.class_acc,'hi'],['참조 Class 복원율(누락 포함)',a=>a.strict?.class_accuracy_overall,'hi'],['Class 미판독',a=>a.class_missing,'lo'],['SPT 크기 일치율',a=>a.size_acc,'hi'],['양성 누락',a=>a.missed_positive,'lo'],['위양성',a=>a.false_positive,'lo'],['양성 판정 일치율',a=>a.positivity_agree,'hi'],['κ',a=>a.positivity_kappa,'hi'],['10배 수치 오류',a=>a.x10,'lo'],['검사유형 판정 정답',a=>a.test_type_ok,'hi'],['평균 시간(초)',a=>a.seconds_mean,null]];
const isPct=n=>/율/.test(n);
function metricTable(){const s=$('scope').value;const A=s==='common'?D.aggregate_common:s==='all'?D.aggregate_all:D.by_type[s].aggregate;
 const body=METRICS.map(([n,f,dir])=>{const vals=K.map(k=>f(A[k]));const nums=vals.filter(v=>typeof v==='number');const best=dir==='hi'?Math.max(...nums):dir==='lo'?Math.min(...nums):null;return '<tr><td>'+n+'</td>'+vals.map(v=>'<td class="num '+(dir&&typeof v==='number'&&nums.length>1&&v===best&&new Set(nums).size>1?'best':'')+'">'+(typeof v==='number'?(isPct(n)?pct(v):n==='κ'?fmt(v,3):fmt(v,n.includes('시간')?1:0)):esc(v??'—'))+'</td>').join('')+'</tr>'}).join('');
 $('metricTable').innerHTML='<table class="cmp-table"><thead><tr><th>지표</th>'+K.map(k=>'<th class="num">'+dot(k)+esc(RM[k].label)+'</th>').join('')+'</tr></thead><tbody>'+body+'</tbody></table>';
 $('metricNote').textContent=(s==='common'?'공통 성공 사진: '+D.meta.common_documents.join(', ')+'. ':'')+'굵은 초록 = 해당 범위 최고값. Claude 직접 판독은 에이전트가 읽어 시간이 없습니다. Claude(앱 파이프라인) 시간은 CLI 호출 2회(두 번 읽기) 포함 벽시계 시간이며 4장씩 병렬로 돌렸습니다. ‘전체 29장’에서 gpt-5.6-luna는 성공한 23장만 집계됩니다.'}
$('scope').addEventListener('change',metricTable);metricTable();
$('h2h').innerHTML=Object.entries(D.head2head).map(([o,c])=>{const n=c.both_ok+c.claude_only_ok+c.other_only_ok+c.both_wrong;return '<div><b>'+dot(D.meta.primary)+esc(RM[D.meta.primary].label)+' vs '+dot(o)+esc(RM[o].label)+'</b><div class="tiny muted">비교 행 '+fmt(n)+'</div><div class="kv"><span>둘 다 맞음</span><b>'+fmt(c.both_ok)+'</b></div><div class="kv"><span>'+esc(RM[D.meta.primary].label)+'만 맞음</span><b>'+fmt(c.claude_only_ok)+'</b></div><div class="kv"><span>'+esc(RM[o].label)+'만 맞음</span><b>'+fmt(c.other_only_ok)+'</b></div><div class="kv"><span>둘 다 틀림</span><b>'+fmt(c.both_wrong)+'</b></div></div>'}).join('');
function docCell(s){if(s.error)return '<td class="num worst">실패</td>';const ve=(s.value_n?Math.round(s.value_n*(100-(s.value_acc??100))/100):0)+(s.size_n?Math.round(s.size_n*(100-(s.size_acc??100))/100):0);return '<td class="num">'+s.matched+'/'+s.truth_rows+' · '+ve+' · <span class="'+(s.missed_positive?'worst':'')+'">'+s.missed_positive+'</span></td>'}
$('docTable').innerHTML='<table class="cmp-table"><thead><tr><th>사진</th><th>유형</th><th>기관</th><th class="num">참조 행</th>'+K.map(k=>'<th class="num">'+dot(k)+esc(RM[k].label)+'</th>').join('')+'</tr></thead><tbody>'+D.documents.map(d=>'<tr><td><a href="#viewerSec" data-open="'+d.id+'">'+d.id+'</a></td><td>'+d.test_type+'</td><td>'+esc(d.lab)+'</td><td class="num">'+d.truth_rows+'</td>'+K.map(k=>docCell(d.scores[k])).join('')+'</tr>').join('')+'</tbody></table>';
document.querySelectorAll('[data-open]').forEach(a=>a.addEventListener('click',()=>{$('vDoc').value=a.dataset.open;renderViewer()}));
$('vDoc').innerHTML=D.documents.map(d=>'<option value="'+d.id+'">'+d.id+' · '+d.test_type+' · '+esc(d.lab)+'</option>').join('');
function val(o,tt){if(!o)return '<span class="muted">—</span>';if(tt==='SPT')return '<span class="v">'+esc(o.size_text??(o.mean_mm??o.value??'—'))+'</span>';const v=o.value_text??o.value;return '<span class="v">'+esc(v??'—')+'</span>'+(o.class_value!==undefined&&o.class_value!==null?' · C'+esc(o.class_value):'')}
function cellFor(r,k,tt){const a=r[k];if(a===null)return '<td class="muted">실행 실패</td>';if(!a.ocr)return '<td><span class="tagx">행 누락</span></td>';const errs=(a.errors||[]).filter(e=>WRONG.has(e));return '<td>'+esc(a.ocr.allergen_name||'')+'<br>'+val(a.ocr,tt)+'<br>'+(errs.length?errs.map(e=>'<span class="tagx">'+(LBL[e]||e)+'</span>').join(''):'<span class="tagx tagok">일치</span>')+'</td>'}
function bad(a){return a&&(a.ocr===null||(a.errors||[]).some(e=>WRONG.has(e)))}
function renderViewer(){const d=D.documents.find(x=>x.id===$('vDoc').value);const f=$('vFilter').value,q=$('vSearch').value.trim().toLowerCase();$('vImg').src=D.images[d.id];$('vTitle').textContent=d.id+' · '+d.lab+' · '+d.test_type+' · 참조 '+d.truth_rows+'행';
 let rows=d.rows.filter(r=>{const anyBad=K.some(k=>bad(r[k]));if(f==='issues'&&!anyBad)return false;const P=D.meta.primary;if(f==='claude'&&!bad(r[P]))return false;if(f==='claudeonly'&&!(bad(r[P])&&K.filter(k=>k!==P).every(k=>r[k]===null||!bad(r[k]))))return false;if(q&&!JSON.stringify([r.reference,...K.map(k=>r[k]?.ocr?.allergen_name)]).toLowerCase().includes(q))return false;return true});
 $('vCount').textContent=rows.length+' / '+d.rows.length+'행';
 $('vTable').innerHTML='<thead><tr><th>#</th><th>참조 전사</th>'+K.map(k=>'<th>'+dot(k)+esc(RM[k].label)+'</th>').join('')+'</tr></thead><tbody>'+(rows.length?rows.map((r,i)=>{const crit=K.some(k=>(r[k]?.errors||[]).some(e=>e==='missed_positive'||e==='false_positive'));const anyBad=K.some(k=>bad(r[k]));return '<tr class="'+(crit?'crit':anyBad?'bad':'')+'"><td>'+esc(r.reference.printed_no??(d.rows.indexOf(r)+1))+'</td><td><b>'+esc(r.reference.allergen_name)+'</b>'+(r.reference.korean_name?'<br><span class="muted">'+esc(r.reference.korean_name)+'</span>':'')+'<br>'+val(r.reference,d.test_type)+(r.truth_pos?' <span class="tagx">양성</span>':'')+(r.reference.uncertain?' <span class="tagx">불확실</span>':'')+'</td>'+K.map(k=>cellFor(r,k,d.test_type)).join('')+'</tr>'}).join(''):'<tr><td colspan="8">해당 행 없음</td></tr>')+'</tbody>';
 const ex=K.map(k=>{const xs=d.extras[k]||[];return xs.length?'<p><b>'+dot(k)+esc(RM[k].label)+' 추가·중복 '+xs.length+'행</b>: '+xs.map(x=>esc(x.ocr?.allergen_name)+' ('+((x.errors||[]).map(e=>LBL[e]||e).join(','))+')').join(' · ')+'</p>':''}).join('');
 const ctrl=d.test_type==='SPT'?'<p class="tiny">히스타민 대조 mm · 참조 '+fmt(d.controls.truth,2)+' · '+K.map(k=>esc(RM[k].label)+' '+(d.controls[k]===null?'미판독':fmt(d.controls[k],2))).join(' · ')+'</p>':'';
 $('vExtras').innerHTML=ctrl+ex;history.replaceState(null,'','?id='+d.id)}
['vDoc','vFilter'].forEach(id=>$(id).addEventListener('change',renderViewer));$('vSearch').addEventListener('input',renderViewer);
const pid=new URLSearchParams(location.search).get('id');if(pid&&D.documents.some(d=>d.id===pid))$('vDoc').value=pid;renderViewer();
$('method').innerHTML=['참조 전사와 채점기(scripts/eval_real_ocr_report.py)는 기존 비교와 같습니다. 참조 전사는 사람 전사이며 독립 판정된 임상 정답은 아닙니다.','gpt-5.4·qwen3.8:27b는 2026-10-09 저장 결과를 그대로 다시 채점했습니다(기존 페이지 수치와 같음).','gpt-5.6-luna: 2026-10-29 같은 하네스(scripts/eval_real_ocr.py, 전처리+두 번 읽기, 회전 보정 없음)로 실행. OpenAI 키 4개 중 2개가 401로 거절돼 6장(A03·A04·A05·A07·B08·B12)이 실패했습니다.','Claude(앱 파이프라인): 사용자 Claude 구독 계정(drugnsafety@gmail.com, Max)으로 로그인한 Claude Code CLI(claude-opus-5-5)를 앱의 모델 호출 자리에 넣고, 앱 OCR 경로(전처리·두 번 읽기·병합·검증)를 그대로 돌렸습니다(scripts/eval_real_ocr_claude_cli.py). 입력 조건이 gpt-5.4 기준 실행과 같습니다.','Claude 직접 판독(opus-5-5): Aside 에이전트 8개가 앱 OCR 프롬프트(ocr_service._get_default_prompt)를 그대로 받아 사진(전체 + 2배 확대 타일)을 직접 읽었습니다. 참조 전사·기존 결과는 열람하지 않았습니다(맹검). 결과 JSON은 앱의 파싱·항원 매핑·검증 코드에 그대로 통과시켰습니다(scripts/eval_claude_ocr_ingest.py). 두 번 읽기·처리 시간은 해당 없음.','Claude 직접 판독은 사진을 세워서(회전) 읽었고 한 번만 읽었습니다. 나머지 엔진은 앱 하네스 그대로(회전 보정 없음, 두 번 읽기) 읽었습니다.','Claude 직접 판독에서는 환자 이름·차트번호·의뢰의사를 일부러 비웠습니다. 환자 정보 지표는 이 페이지에서 비교하지 않습니다.'].map(x=>'<li>'+esc(x)+'</li>').join('');
'''

if __name__ == "__main__":
    main()
