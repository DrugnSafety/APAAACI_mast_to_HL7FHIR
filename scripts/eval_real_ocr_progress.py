#!/usr/bin/env python3
"""실제 결과지 OCR 평가의 진행 내역 페이지 — 로그·결과 파일·수정 시각에서 자동으로 만든다.

사용: python3 scripts/eval_real_ocr_progress.py  → docs/ocr_eval_2026-10-09/progress.html (단일 파일, 이미지 없음)
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "output" / "ocr_eval_2026-10-09"
DOC = ROOT / "docs" / "ocr_eval_2026-10-09"
RUNS = {"openai": "API · OpenAI gpt-5.4", "ollama": "Local · Ollama qwen3.8:27b",
        "ollama_firstpass_413": "Local 1차(413 실패분)", "openai_rotated": "실험 · 회전 보정(수동)",
        "openai_v2": "실험 · 자동 회전+중복 제거"}


def mtime(p: Path) -> Optional[dt.datetime]:
    return dt.datetime.fromtimestamp(p.stat().st_mtime) if p.exists() else None


def hm(t: Optional[dt.datetime]) -> str:
    return t.strftime("%H:%M") if t else "—"


def load_runs() -> Dict[str, Dict[str, Dict[str, Any]]]:
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for run_dir in sorted((EVAL / "runs").iterdir()):
        if not run_dir.is_dir():
            continue
        recs = {}
        for p in sorted(run_dir.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            res = d.get("result") or {}
            dr = (res.get("metadata") or {}).get("double_read") or {}
            recs[p.stem] = {"seconds": d.get("seconds"), "rows": len(res.get("results") or []),
                            "error": d.get("error"), "done_at": mtime(p),
                            "double_read_error": dr.get("error") if isinstance(dr, dict) else None,
                            "notes": (res.get("metadata") or {}).get("preprocess")}
        out[run_dir.name] = recs
    return out


def main():
    manifest = json.loads((EVAL / "manifest.json").read_text(encoding="utf-8"))["items"]
    runs = load_runs()
    truth = {p.stem: mtime(p) for p in (EVAL / "truth").glob("*.json")}
    summary_p = DOC / "summary.json"
    summary = json.loads(summary_p.read_text(encoding="utf-8")) if summary_p.exists() else {}
    now = dt.datetime.now()

    def span(run):
        ts = [r["done_at"] for r in runs.get(run, {}).values() if r.get("done_at")]
        return (min(ts), max(ts)) if ts else (None, None)

    # ---- 타임라인 (파일 시각 기반 + 작업 메모)
    o_s, o_e = span("openai")
    f_s, f_e = span("ollama_firstpass_413")
    l_s, l_e = span("ollama")
    r_s, r_e = span("openai_rotated")
    v_s, v_e = span("openai_v2")
    t_s = min(truth.values()) if truth else None
    t_e = max(truth.values()) if truth else None
    steps = [
        (mtime(EVAL / "manifest.json"), "입력 확인·목록화", "input_images/ 29장(10-41 배치 12 = A01~A12, 10-42 배치 12 = B01~B12, 기존 5 = O01~O05)에 id 부여 (scripts/eval_real_ocr.py manifest). 누운 사진 5장, 저화질 2장, 요약페이지 3장 확인."),
        (o_s, "API 모델 OCR 시작", f"OpenAI gpt-5.4, 운영 파이프라인(전처리+두 번 읽기+검증), 3병렬. {hm(o_s)}~{hm(o_e)} 완료 29/29."),
        (f_s, "Local 모델 1차 실행 → 413 발견", f"연구실 Ollama qwen3.8:27b. 첫 판독은 되지만 2차 판독(절반 확대본 2장, base64 1.07MB)이 nginx 1MB 한도에 걸려 전부 413. {hm(f_s)}~{hm(f_e)} A 배치 12장이 '2차 판독 실패' 상태로 저장 → runs/ollama_firstpass_413/ 로 보관."),
        (t_s, "정답 전사 (사람·에이전트 6명)", f"4분할 확대본으로 행마다 전사, 규칙서 truth_brief.md. {hm(t_s)}~{hm(t_e)} 29장 완료(정답 1,418행, 불확실 17행)."),
        (mtime(ROOT / "test_ollama_payload_budget.py"), "수정 ① payload 예산", "OLLAMA_MAX_PAYLOAD_KB=900 (utils/image_preprocess.fit_payload) + 테스트 4건. Local 런 재시작."),
        (l_s, "Local 모델 재실행(전 파이프라인)", f"{hm(l_s)}~ 장당 225~294초. A05 1,706초, A06 3,086초(51분) 뒤 서버 연결 끊김 → 나머지 즉시 실패."),
        (mtime(ROOT / "scripts" / "eval_real_ocr_report.py"), "채점기·리포트 생성기 작성", "행 대조(번호→별칭→유사도), 수치/class/팽진/양성 판정 비교, 모델 간 일치도(Jaccard·κ), 오류 분류, HTML. 성능 최적화 중 캐시 키 버그(id() 재사용) 발견·수정."),
        (truth.get("A05"), "정답 판정·보정", "OCR 과 어긋난 행을 다시 확대해 판정: A05 26·42 전사 누락(0.00) 보정, B12 3행 불확실 표시, B10 채취일 보완."),
        (r_s, "실험 · 회전 보정(수동)", f"누운 5장(A01~A05)을 세워서 재실행 {hm(r_s)}~{hm(r_e)}: 정밀도 74~87% → 97~100%, A05 수치 90.2→100%, 행번호 밀림 22→0."),
        (mtime(ROOT / "test_ocr_real_sheet_fixes.py"), "수정 ②③④ 자동 회전·병합 키·폭주 가드", "OCR_AUTO_ROTATE(가로 사진만, Tesseract OSD 90/270), name_core 병합 키 정규화(성분 항원 매퍼 우회), Local max_tokens 16000, --retry-errors. 회귀 테스트 18건 추가, 기존 243건 통과."),
        (v_s, "실험 · 자동 회전+중복 제거(v2)", f"A01·A02·A04·A05·B02·B11·B12·O03 재실행 {hm(v_s)}~{hm(v_e)}: 6장 정밀도 100%, 중복행 0. B12 중복 28→7."),
        (mtime(EVAL / "logs" / "ollama_retry.log"), "Local 모델 실패분 재시도", "서버 복구 확인 후 --retry-errors 로 23장 재실행 중."),
        (mtime(DOC / "findings.json"), "오류 포인트·고도화 방안 서술", "docs/ocr_eval_2026-10-09/findings.json → 리포트 6·7절."),
        (mtime(DOC / "index.html"), "리포트 생성·렌더링 확인", "docs/ocr_eval_2026-10-09/index.html — 헤드리스 Chrome 으로 JS 오류 1건(ERR 선언 순서) 잡아 수정."),
    ]
    steps = [s for s in steps if s[0]]
    steps.sort(key=lambda s: s[0])

    # ---- 사진별 현황
    def cell(run, img_id):
        r = runs.get(run, {}).get(img_id)
        if not r:
            return '<td class="pending">대기</td>'
        if r.get("error"):
            return f'<td class="err" title="{html.escape(r["error"])}">실패 {hm(r["done_at"])}<br><small>{html.escape(r["error"][:40])}</small></td>'
        extra = ' · <span class="warn">2차판독 실패</span>' if r.get("double_read_error") else ""
        notes = " · ".join(r.get("notes") or [])
        return f'<td class="ok">{r["rows"]}행 · {r["seconds"]:.0f}초 · {hm(r["done_at"])}{extra}{("<br><small>" + html.escape(notes) + "</small>") if notes else ""}</td>'

    run_cols = ["openai", "ollama_firstpass_413", "ollama", "openai_rotated", "openai_v2"]
    rows_html = ""
    for it in manifest:
        img_id = it["id"]
        tr = truth.get(img_id)
        rows_html += (f'<tr><td class="mono">{img_id}</td><td><small>{html.escape(it["file"])}</small>'
                      f'{" <span class=warn>누움</span>" if it["landscape"] else ""}</td>'
                      f'<td class="{"ok" if tr else "pending"}">{"완료 " + hm(tr) if tr else "대기"}</td>'
                      + "".join(cell(r, img_id) for r in run_cols) + "</tr>")

    counts = {r: (sum(1 for x in runs.get(r, {}).values() if not x.get("error")),
                  sum(1 for x in runs.get(r, {}).values() if x.get("error"))) for r in run_cols}

    # ---- 요약 지표
    agg = summary.get("aggregate", {})
    def tiles(run):
        a = agg.get(run)
        if not a:
            return ""
        return (f'<div class="tiles">' + "".join(
            f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in [
                ("처리 장수", f'{a["images"] - a["errors"]}/{a["images"]}'), ("행 재현율", f'{a["recall"]}%'), ("행 정밀도", f'{a["precision"]}%'),
                ("수치 정확도", f'{a["value_acc"]}%'), ("class 정확도", f'{a["class_acc"]}%'), ("팽진 크기", f'{a["size_acc"]}%' if a.get("size_acc") is not None else "—"),
                ("양성 누락", f'{a["missed_positive"]}/{a["truth_positive"]}'), ("위양성", a["false_positive"]), ("장당 시간", f'{a["seconds_mean"]}초')]) + "</div>")

    ag = summary.get("agreement_aggregate", {})
    pending = [it["id"] for it in manifest if not runs.get("ollama", {}).get(it["id"]) or runs["ollama"][it["id"]].get("error")]

    page = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>OCR 평가 진행 내역</title>
<style>
:root{{--bg:#f7f7f5;--surface:#fff;--ink:#1b1b1f;--ink2:#4f5560;--muted:#8a8f99;--line:#e3e4e8;--c1:#2f6fed;--good:#1f8a4c;--warn:#b7791f;--bad:#c6362f;--good-bg:#e8f5ec;--bad-bg:#fdecea;--warn-bg:#fff6e0}}
:root:not([data-theme="light"]){{color-scheme:light dark}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#121316;--surface:#1b1d21;--ink:#ecedf0;--ink2:#b7bbc4;--muted:#80858f;--line:#2c2f36;--c1:#7aa2ff;--good:#5fc784;--warn:#e0b45a;--bad:#f07a73;--good-bg:#1c3326;--bad-bg:#3a1f1e;--warn-bg:#3a2f18}}}}
:root[data-theme="dark"]{{--bg:#121316;--surface:#1b1d21;--ink:#ecedf0;--ink2:#b7bbc4;--muted:#80858f;--line:#2c2f36;--c1:#7aa2ff;--good:#5fc784;--warn:#e0b45a;--bad:#f07a73;--good-bg:#1c3326;--bad-bg:#3a1f1e;--warn-bg:#3a2f18}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:24px 16px 80px}}h1{{font-size:24px;margin:8px 0}}h2{{font-size:19px;margin:36px 0 10px;padding-top:10px;border-top:1px solid var(--line)}}
p,li{{color:var(--ink2)}}small{{color:var(--muted)}}.mono{{font-family:ui-monospace,Menlo,monospace;font-size:12px}}
table{{border-collapse:collapse;width:100%;background:var(--surface);font-size:13px}}th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}}th{{background:var(--bg);color:var(--ink2);position:sticky;top:0}}
.tbl{{overflow:auto;border:1px solid var(--line);border-radius:10px;max-height:75vh}}
td.ok{{background:var(--good-bg)}}td.err{{background:var(--bad-bg)}}td.pending{{color:var(--muted)}}.warn{{color:var(--warn)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin:8px 0 14px}}.tile{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.tile .k{{font-size:12px;color:var(--muted)}}.tile .v{{font-size:20px;font-weight:600}}
.timeline{{list-style:none;padding:0;margin:0}}.timeline li{{display:grid;grid-template-columns:64px 1fr;gap:12px;padding:8px 0;border-bottom:1px dashed var(--line)}}.timeline .t{{font-family:ui-monospace,Menlo,monospace;color:var(--c1);font-size:13px;padding-top:2px}}.timeline b{{display:block}}.timeline span{{color:var(--ink2);font-size:14px}}
.status{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:13px;border:1px solid var(--line)}}.status.run{{background:var(--warn-bg);color:var(--warn)}}.status.done{{background:var(--good-bg);color:var(--good)}}
a{{color:var(--c1)}}
</style></head><body><main>
<h1>실제 결과지 OCR 평가 — 진행 내역</h1>
<p>생성 {now.strftime("%Y-%m-%d %H:%M")} · 입력 29장 · 정답 전사 {len(truth)}/29 · API 모델 {counts["openai"][0]}/29 · Local 모델 {counts["ollama"][0]}/29 완료
{"<span class=status run>Local 재시도 진행 중 — 남은 " + str(len(pending)) + "장: " + ", ".join(pending) + "</span>" if pending else "<span class=status done>모든 실행 완료</span>"}
· 결과 리포트: <a href="index.html">index.html</a> (과정·지표·사진별 상세·오류 포인트·고도화 방안)</p>

<h2>1. 타임라인</h2>
<ul class="timeline">
{"".join(f'<li><div class="t">{hm(t)}</div><div><b>{html.escape(title)}</b><span>{html.escape(body)}</span></div></li>' for t, title, body in steps)}
</ul>

<h2>2. 현재 요약 지표 (정답 대조, summary.json 기준)</h2>
<h3>API 모델 — OpenAI gpt-5.4</h3>{tiles("openai")}
<h3>Local 모델 — 연구실 Ollama qwen3.8:27b <small>(완료분만, 재시도 진행 중이면 부분 집계)</small></h3>{tiles("ollama")}
<p>두 모델 간 일치도(공통 행): 행 Jaccard {ag.get("row_jaccard")}% · 수치 {ag.get("value_agree")}% · class {ag.get("class_agree")}% · 양성 판정 {ag.get("positivity_agree")}% (κ={ag.get("positivity_kappa")})</p>

<h2>3. 사진별 처리 현황</h2>
<p>셀: 읽은 행 수 · 소요 초 · 완료 시각. 초록 = 완료, 빨강 = 실패(서버 끊김 등), 회색 = 대기. '2차판독 실패' = 첫 판독은 성공했으나 절반 확대본 판독이 413/타임아웃.</p>
<div class="tbl"><table><tr><th>id</th><th>파일</th><th>정답 전사</th>{"".join(f"<th>{html.escape(RUNS.get(r, r))}<br><small>완료 {counts[r][0]} · 실패 {counts[r][1]}</small></th>" for r in run_cols)}</tr>{rows_html}</table></div>

<h2>4. 적용한 코드 수정</h2>
<ul>
<li><b>config/settings.py</b> — <code>OLLAMA_MAX_PAYLOAD_KB</code>(기본 900), <code>OCR_AUTO_ROTATE</code>(기본 true)</li>
<li><b>utils/image_preprocess.py</b> — <code>fit_payload</code>(요청 크기 예산), <code>detect_rotation</code>/<code>auto_rotate</code>(Tesseract OSD, 가로 사진·90/270 만), <code>prepare(rotate=)</code></li>
<li><b>services/ocr_validation.py</b> — <code>name_core</code>/<code>_latin_tokens</code>/<code>_row_key</code> 병합 키 정규화, 성분 항원 매퍼 우회, <code>validate</code> 중복 판정도 같은 키 사용</li>
<li><b>services/ocr_service.py</b> — Ollama 경로 payload 예산 적용, max_tokens 32000→16000, prepare 에 자동 회전 전달</li>
<li><b>scripts/eval_real_ocr.py</b> — 실행 러너(manifest/run, --rotate, --tag, --force, --retry-errors) · <b>scripts/eval_real_ocr_report.py</b> — 채점·일치도·HTML · <b>scripts/eval_real_ocr_progress.py</b> — 이 페이지</li>
<li><b>테스트</b> — test_ollama_payload_budget.py(4), test_ocr_real_sheet_fixes.py(18) 통과 · 기존 test_relevance_engine/test_release_gate_fixes/test_verifier_defects 243 통과</li>
</ul>

<h2>5. 남은 일</h2>
<ul>
<li>Local 모델 재시도 완료 후 최종 채점·리포트 재생성 (현재 남은 {len(pending)}장)</li>
<li>독립 코드 리뷰(채점기·파이프라인 수정) 결과 반영</li>
<li>findings.json 의 Local 모델 수치를 최종값으로 갱신</li>
</ul>
<p><small>자료: 실제 검사 결과지 29장 — 환자 이름은 마스킹한 상태로 작업했고, 정답·실행 결과는 저장소 밖(output/, gitignored)에 둔다.</small></p>
<p><small>산출물 위치: output/ocr_eval_2026-10-09/ (manifest·truth·runs·logs, gitignored) · docs/ocr_eval_2026-10-09/ (index.html·summary.json·findings.json·progress.html·img/)</small></p>
</main></body></html>"""
    DOC.mkdir(parents=True, exist_ok=True)
    (DOC / "progress.html").write_text(page, encoding="utf-8")
    print(f"진행 내역: {DOC / 'progress.html'}  (API {counts['openai'][0]}/29, Local {counts['ollama'][0]}/29, 남은 {len(pending)})")


if __name__ == "__main__":
    main()
