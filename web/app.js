/* =========================================================================
   알레르기 리포트 플랫폼 — 프론트엔드 (vanilla JS SPA)
   단계: 0 업로드 → 1 OCR검토 → 2 스크리닝 → 3 문진 → 4 결과
   ========================================================================= */
'use strict';

// FastAPI 오류 detail(문자열 또는 검증오류 배열)을 사람이 읽을 수 있는 문장으로 변환
function fmtErr(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail.map(e => {
      const loc = Array.isArray(e.loc) ? e.loc.slice(-2).join('.') : '';
      return (loc ? loc + ': ' : '') + (e.msg || JSON.stringify(e));
    }).join(' / ');
  }
  try { return JSON.stringify(detail); } catch (_) { return fallback; }
}

const API = {
  async health() { return (await fetch('/api/health')).json(); },
  async demo() { return (await fetch('/api/ocr/demo')).json(); },
  async allergen(name) { return (await fetch('/api/allergen?name=' + encodeURIComponent(name))).json(); },
  async ocr(file) {
    const fd = new FormData(); fd.append('file', file);
    const r = await fetch('/api/ocr', { method: 'POST', body: fd });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(fmtErr(e.detail, 'OCR 실패')); }
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(fmtErr(e.detail, path + ' 실패')); }
    return r.json();
  },
};

const S = {
  step: 0,
  maxReached: 0,
  ocr: null,
  screening: null,
  options: null,
  questionnaire: null,
  answers: {},
  classify: null,
  game: Game.createState(),   // 게임 레이어 상태(XP·도감·배지) — 판정 로직과 무관
};

let REVIEW_TAB = 'measured';  // OCR 검토 표 탭: 'measured'(수치>0) / 'zero'(수치 0·미측정)
let DEX_FILTER = 'all';       // 결과 도감 필터: all | clinically_relevant | indeterminate | sensitized_only
const t = (k, v) => I18N.t(k, v);   // 화면 문구 번역 (web/i18n.js). 서버 생성 콘텐츠는 한국어.
const STEPS = () => [0, 1, 2, 3, 4].map(i => t(`step.${i}.name`));
const STEP_SUB = () => [0, 1, 2, 3, 4].map(i => t(`step.${i}.sub`));
const catLabel = (c) => { const k = `cat.${c}`; const v = t(k); return v === k ? (c || '') : v; };
// 서버 생성 콘텐츠(문진·KB·리포트)가 한국어임을 비한국어 UI 에서 안내
const partialNotice = () => I18N.getLang() === 'ko' ? '' : `<div class="notice info" style="margin-bottom:14px">${t('notice.partial')}</div>`;
const CAT_EMOJI = { mite: '🛏️', animal: '🐾', pollen_tree: '🌳', pollen_grass: '🌾', pollen_weed: '🍂', mold: '🍄', insect: '🪳', food: '🍽️', other: '•' };
const REL_LABEL = { clinically_relevant: '실제 주의', sensitized_only: '감작만', indeterminate: '관찰 필요', not_assessed: '미평가' };
const CAT_LABEL = { mite: '집먼지진드기', animal: '동물', pollen_tree: '나무 꽃가루', pollen_grass: '잔디 꽃가루', pollen_weed: '잡초 꽃가루', mold: '곰팡이', insect: '곤충', food: '음식', other: '기타' };

/* ---------------- utils ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = () => $('#view');
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2200); }
function download(name, text, type = 'application/json') {
  const blob = new Blob([text], { type }); const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);
}

function isPositive(r, testType) {
  if (r.interpretation === 'Positive' || r.interpretation === '양성') return true;
  if (r.interpretation === 'Negative' || r.interpretation === '음성') return false;
  return deriveInterp(r, testType) === 'Positive';
}
// 수치/Class 로부터 양성/음성 유도 (사용자가 표 값을 수정하면 판정도 갱신)
// MAST/UniCAP: Class>=1 '또는' 수치>=0.35 이면 양성 (수치를 양성 이상으로 올리면 자동 양성)
function deriveInterp(r, testType) {
  if (testType === 'SPT') return ((r.mean_mm ?? r.value ?? 0) >= 3) ? 'Positive' : 'Negative';
  const cls = (r.class_value != null && String(r.class_value).trim() !== '') ? parseInt(r.class_value) : null;
  const clsPos = (cls != null && !isNaN(cls)) ? cls >= 1 : false;
  const valPos = (parseFloat(r.value) || 0) >= 0.35;
  return (clsPos || valPos) ? 'Positive' : 'Negative';
}
// MAST/UniCAP 특이 IgE 수치(kU/L) → Class(0-6). 표준 CAP 구간.
function valueToClass(v) {
  v = parseFloat(v); if (isNaN(v)) return null;
  if (v < 0.35) return 0;
  if (v < 0.70) return 1;
  if (v < 3.50) return 2;
  if (v < 17.5) return 3;
  if (v < 50.0) return 4;
  if (v < 100) return 5;
  return 6;
}
// 검사종류별 기본 단위
function defaultUnit(testType) { return testType === 'SPT' ? 'mm' : 'kU/L'; }
// 검토 탭 분류: 수치>0 이면 measured, 그 외(0/빈값)는 zero
function rowIsZero(r) {
  const v = parseFloat(r.value);
  return isNaN(v) || v === 0;
}

/* ---------------- navigation ---------------- */
function goto(step) {
  // 앞으로 전진할 때 현재 단계 완료 XP(행동 기반, 1회)
  if (step > S.step) { const g = Game.completeStep(S.game, S.step); if (g) Game.ui.floatXp(null, g); }
  S.step = step; S.maxReached = Math.max(S.maxReached, step);
  window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  render();
}
function renderStepper() {
  Game.ui.renderTrail($('#stepper'), STEPS(), STEP_SUB(), S.step, S.maxReached, goto);
}
function render() {
  renderStepper();
  Game.ui.renderHud($('#hud'), S.game);
  $('#questBar').classList.toggle('hidden', S.step !== 3);
  document.body.setAttribute('data-appstep', S.step);  // 모바일 전용 UI(스크리닝=2/문진=3) 스코프용
  [renderUpload, renderReview, renderScreening, renderQuestionnaire, renderResults][S.step]();
}

/* =========================================================================
   STEP 0 — 업로드
   ========================================================================= */
function renderUpload() {
  const hasKey = S.options && S.options.has_api_key;
  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('s0.eyebrow')}</div>
        <h1>${t('s0.h1')}</h1>
        <p>${t('s0.p')}</p>
      </div>
      <div class="dropzone" id="dz" role="button" tabindex="0" aria-label="${t('s0.dz_aria')}">
        <div class="icon">🗂️</div>
        <h2>${t('s0.dz_h')}</h2>
        <p>${t('s0.dz_p')} ${hasKey ? '' : t('s0.dz_nokey')}</p>
        <input type="file" id="file" accept="image/*" class="hidden" />
      </div>
      <img id="preview" class="preview-img hidden" alt="${t('s0.preview_alt')}" />
      <div id="uploadMsg"></div>
      <div class="upload-alt">
        <button class="btn secondary" id="btnDemo">${t('s0.btn_demo')}</button>
        <button class="btn secondary" id="btnManual">${t('s0.btn_manual')}</button>
      </div>
    </div>`;

  const dz = $('#dz'), file = $('#file');
  dz.addEventListener('click', () => file.click());
  dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } });
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); });
  file.addEventListener('change', () => { if (file.files[0]) handleFile(file.files[0]); });
  $('#btnDemo').addEventListener('click', loadDemo);
  $('#btnManual').addEventListener('click', startManual);
}

async function handleFile(f) {
  const prev = $('#preview'); prev.src = URL.createObjectURL(f); prev.classList.remove('hidden');
  const msg = $('#uploadMsg');
  msg.innerHTML = `<div class="notice info" style="margin-top:16px"><span class="spinner" style="border-color:var(--brand-soft);border-top-color:var(--brand)"></span> ${t('s0.ocr_reading')}</div>`;
  try {
    const ocr = await API.ocr(f);
    S.ocr = normalizeOcr(ocr);
    goto(1);
  } catch (e) {
    msg.innerHTML = `<div class="notice warn" style="margin-top:16px">⚠️ ${esc(e.message)}<br/>${t('s0.ocr_fail_hint')}</div>`;
  }
}
async function loadDemo() {
  try { S.ocr = normalizeOcr(await API.demo()); toast(t('s0.demo_loaded')); goto(1); }
  catch (e) { toast(t('s0.demo_fail') + e.message); }
}
function startManual() {
  S.ocr = { test_type: 'MAST', patient: { name: '', age: null, gender: 'M', test_date: '' }, results: [] };
  addRow(); goto(1);
}
function normalizeOcr(ocr) {
  ocr.patient = ocr.patient || {};
  ocr.results = (ocr.results || []).map((r, i) => ({
    index: r.index ?? i + 1, raw_text: r.raw_text || r.allergen_name || '',
    allergen_name: r.allergen_name || '', korean_name: r.korean_name || '',
    value: r.value ?? r.mean_mm ?? null, mean_mm: r.mean_mm ?? null,
    unit: r.unit || (ocr.test_type === 'SPT' ? 'mm' : 'kU/L'),
    class_value: r.class_value ?? null, category: r.category || null,
    interpretation: r.interpretation || (isPositive(r, ocr.test_type) ? 'Positive' : 'Negative'),
  }));
  return ocr;
}

/* =========================================================================
   STEP 1 — OCR 검토 (편집 가능한 표 + 행 추가/삭제)
   ========================================================================= */
function addRow(data = {}) {
  S.ocr.results.push({
    index: S.ocr.results.length + 1, raw_text: data.allergen_name || '',
    allergen_name: data.allergen_name || '', korean_name: data.korean_name || '',
    value: data.value ?? null, mean_mm: null, unit: S.ocr.test_type === 'SPT' ? 'mm' : 'kU/L',
    class_value: data.class_value ?? null, category: data.category || null,
    interpretation: 'Positive',
  });
}
function posCount() { return S.ocr.results.filter(r => isPositive(r, S.ocr.test_type)).length; }

// 검사지에서 추출된 기관/날짜/환자정보를 정보카드로 표시 (있을 때만)
function renderExtractedMeta(p) {
  p = p || {};
  const items = [];
  if (p.facility) items.push([t('s1.meta_facility'), p.facility]);
  if (p.ordering_provider) items.push([t('s1.meta_provider'), p.ordering_provider]);
  if (p.test_date) items.push([t('s1.meta_date'), p.test_date]);
  if (p.report_date) items.push([t('s1.meta_report_date'), p.report_date]);
  if (p.patient_id_external) items.push([t('s1.meta_chart'), p.patient_id_external]);
  if (!items.length) return '';
  return `<div class="card soft" style="margin-bottom:16px;display:flex;flex-wrap:wrap;gap:8px 22px">
    ${items.map(([k, v]) => `<div style="font-size:13px"><span style="color:var(--text-3);font-weight:700">${k}</span> <span style="color:var(--text)">${esc(v)}</span></div>`).join('')}
    <div style="flex-basis:100%;font-size:11.5px;color:var(--text-3)">${t('s1.meta_note')}</div>
  </div>`;
}

function rowMeasured(r) {
  // '측정된 값'이 있는 행: 수치>0 또는 Class>=1 또는 양성. (0/음성 행은 기본 숨김)
  const v = parseFloat(r.value); const cls = parseInt(r.class_value);
  return (v > 0) || (!isNaN(cls) && cls >= 1) || isPositive(r, S.ocr.test_type);
}
// 표를 다시 그리지 않고 요약(양성 수·안내 문구·다음 버튼)만 제자리 갱신.
// 값/Class 를 편집하는 동안 표가 통째로 재렌더되지 않게 하여 편집 중인 셀이 사라지거나
// 포커스가 튀는 문제를 방지한다. (측정값만 보기 필터는 다음 전체 렌더 때 반영)
function refreshReviewSummary() {
  const n = posCount();
  const pn = $('#posN'); if (pn) pn.textContent = n;
  const ps = $('#posSummary');
  if (ps) ps.innerHTML = n ? `<div class="pos-summary">${t('s1.pos_summary', { n })}</div>` : '';
  const nx = $('#next'); if (nx) nx.disabled = !n;
}
function renderReview() {
  const tt = S.ocr.test_type;
  const isSPT = tt === 'SPT';
  const valueLabel = isSPT ? t('s1.col_value_spt') : t('s1.col_value_ige');
  const allRows = S.ocr.results.map((r, i) => ({ r, i }));
  const zeroN = allRows.filter(({ r }) => rowIsZero(r)).length;
  const measuredN = allRows.length - zeroN;
  const shown = allRows.filter(({ r }) => REVIEW_TAB === 'zero' ? rowIsZero(r) : !rowIsZero(r));
  const rows = shown.map(({ r, i }) => {
    const on = isPositive(r, tt);
    // SPT 는 Class 개념이 없으므로 비활성화(—). MAST/UniCAP 만 Class 편집.
    const classCell = isSPT
      ? `<td style="width:66px;text-align:center;color:var(--text-3)">—</td>`
      : `<td style="width:66px"><input data-f="class_value" value="${r.class_value ?? ''}" placeholder="0-6" title="${t('s1.class_title')}" /></td>`;
    return `<tr data-i="${i}">
      <td style="color:var(--text-3);width:34px">${i + 1}</td>
      <td><input data-f="allergen_name" value="${esc(r.allergen_name)}" placeholder="${t('s1.ph_allergen')}" /></td>
      <td><input data-f="korean_name" value="${esc(r.korean_name)}" placeholder="${t('s1.ph_korean')}" /></td>
      <td class="num" style="width:96px"><input data-f="value" type="number" step="0.01" value="${r.value ?? ''}" /></td>
      <td style="width:78px"><input data-f="unit" value="${esc(r.unit || '')}" /></td>
      ${classCell}
      <td style="width:78px"><button class="pos-toggle ${on ? 'on' : 'off'}" data-toggle="${i}">${on ? t('s1.pos') : t('s1.neg')}</button></td>
      <td style="width:40px"><button class="btn danger-ghost" data-del="${i}" title="${t('s1.del')}">🗑️</button></td>
    </tr>`;
  }).join('');
  const emptyMsg = REVIEW_TAB === 'zero'
    ? t('s1.empty_zero')
    : t('s1.empty_measured');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('s1.eyebrow')}</div>
        <h1>${t('s1.h1')}</h1>
        <p>${t('s1.p')}</p>
      </div>

      ${renderExtractedMeta(S.ocr.patient)}

      <div class="field-inline" style="margin-bottom:14px">
        <label style="font-weight:700;font-size:13.5px">${t('s1.test_type')}</label>
        <select class="input" id="testType" style="width:auto">
          ${['SPT', 'MAST', 'UniCAP'].map(ty => `<option value="${ty}" ${tt === ty ? 'selected' : ''}>${ty}</option>`).join('')}
        </select>
        <span class="hint">${isSPT ? t('s1.hint_spt') : t('s1.hint_ige')}</span>
      </div>

      <div class="rv-tabs" role="tablist">
        <button role="tab" aria-selected="${REVIEW_TAB === 'measured'}" class="rv-tab ${REVIEW_TAB === 'measured' ? 'active' : ''}" data-rvtab="measured">${t('s1.tab_measured')} <span class="rv-count">${measuredN}</span></button>
        <button role="tab" aria-selected="${REVIEW_TAB === 'zero'}" class="rv-tab ${REVIEW_TAB === 'zero' ? 'active' : ''}" data-rvtab="zero">${t('s1.tab_zero')} <span class="rv-count">${zeroN}</span></button>
        <span class="hint" style="margin-left:auto">${REVIEW_TAB === 'zero' ? t('s1.tab_hint_zero') : t('s1.tab_hint_measured')}</span>
      </div>

      <div class="tbl-wrap">
        <table class="grid">
          <thead><tr>
            <th>#</th><th>${t('s1.th_allergen')}</th><th>${t('s1.th_korean')}</th><th>${valueLabel}</th><th>${t('s1.th_unit')}</th><th>${t('s1.th_class')}</th><th>${t('s1.th_interp')}</th><th></th>
          </tr></thead>
          <tbody id="tbody">${rows || `<tr><td colspan="8" style="text-align:center;color:var(--text-3);padding:26px">${emptyMsg}</td></tr>`}</tbody>
        </table>
      </div>

      <div class="tbl-toolbar">
        <button class="btn subtle sm" id="btnAdd">${t('s1.add_row')}</button>
        <span class="spacer"></span>
        <span class="hint">${t('s1.pos_count')} <span class="badge-count" id="posN">${posCount()}</span></span>
      </div>

      <div id="posSummary">${posCount() ? `<div class="pos-summary">${t('s1.pos_summary', { n: posCount() })}</div>` : ''}</div>

      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next" ${posCount() ? '' : 'disabled'}>${t('s1.btn_register')}</button>
      </div>
    </div>`;

  // bindings
  // 검사종류 변경: 단위를 새 종류 기본값으로 바꾸고, SPT↔MAST 판정 기준으로 재계산 후 표를 다시 그린다.
  // (MAST→SPT 로 바꿔도 이전 값·Class·판정이 그대로 남던 문제 수정)
  $('#testType').addEventListener('change', e => {
    const nt = e.target.value; S.ocr.test_type = nt;
    const nu = defaultUnit(nt);
    S.ocr.results.forEach(r => {
      if (!r.unit || r.unit === 'mm' || r.unit === 'kU/L' || r.unit === 'IU/mL') r.unit = nu;
      if (nt === 'SPT') { r.class_value = null; }               // SPT 는 Class 없음
      else if (r.value != null && r.value !== '') { r.class_value = valueToClass(r.value); }  // 수치→Class
      r.interpretation = deriveInterp(r, nt);                    // 판정 재계산
    });
    renderReview();
  });
  $('#tbody').addEventListener('input', e => {
    const tr = e.target.closest('tr'); if (!tr) return;
    const i = +tr.dataset.i, f = e.target.dataset.f; if (f == null) return;
    Game.noteEdit(S.game);   // '꼼꼼한 검토자' 배지 — 직접 수정 행동만 기록
    let v = e.target.value;
    if (f === 'value') v = v === '' ? null : parseFloat(v);
    if (f === 'class_value') v = v === '' ? null : v;
    S.ocr.results[i][f] = v;
    // 수치 수정 → (MAST/UniCAP) Class 자동 계산 + 판정 재계산. Class 직접 수정 → 판정만 재계산.
    if (['value', 'class_value', 'mean_mm'].includes(f)) {
      const r = S.ocr.results[i];
      if (f === 'value' && S.ocr.test_type !== 'SPT') {
        r.class_value = (v == null) ? null : valueToClass(v);
        const cinp = tr.querySelector('input[data-f="class_value"]');
        if (cinp) cinp.value = r.class_value ?? '';               // Class 셀 즉시 반영
      }
      const on = deriveInterp(r, S.ocr.test_type);
      r.interpretation = on;
      const tog = tr.querySelector('.pos-toggle');
      if (tog) { const pos = on === 'Positive'; tog.textContent = pos ? t('s1.pos') : t('s1.neg'); tog.classList.toggle('on', pos); tog.classList.toggle('off', !pos); }
      refreshReviewSummary();
    }
  });
  $('#tbody').addEventListener('blur', async e => {
    if (e.target.dataset.f === 'allergen_name' && e.target.value.trim()) {
      const tr = e.target.closest('tr'); const i = +tr.dataset.i;
      if (!S.ocr.results[i].korean_name) {
        try { const kb = await API.allergen(e.target.value.trim());
          if (kb.korean_name) { S.ocr.results[i].korean_name = kb.korean_name; S.ocr.results[i].category = kb.category; renderReview(); }
        } catch (_) {}
      }
    }
  }, true);
  $('#tbody').addEventListener('click', e => {
    const del = e.target.closest('[data-del]'); const tog = e.target.closest('[data-toggle]');
    if (del) { S.ocr.results.splice(+del.dataset.del, 1); renderReview(); }
    if (tog) { const i = +tog.dataset.toggle; const on = isPositive(S.ocr.results[i], S.ocr.test_type);
      S.ocr.results[i].interpretation = on ? 'Negative' : 'Positive'; renderReview(); }
  });
  // 새 항목은 수치 0(미측정)로 추가되므로 '수치 0 항목' 탭으로 전환해 보여준다.
  $('#btnAdd').addEventListener('click', () => { REVIEW_TAB = 'zero'; addRow(); renderReview(); setTimeout(() => { const inp = view().querySelector('tbody tr:last-child input'); inp && inp.focus(); }, 0); });
  view().querySelectorAll('[data-rvtab]').forEach(b => b.addEventListener('click', () => {
    REVIEW_TAB = b.dataset.rvtab; renderReview();
  }));
  $('#back').addEventListener('click', () => goto(0));
  $('#next').addEventListener('click', confirmDiscovery);
}

// 양성 흔적을 도감에 등록(발견 연출) 후 다음 퀘스트로
function confirmDiscovery() {
  const tt = S.ocr.test_type;
  const rows = S.ocr.results.filter(r => isPositive(r, tt));
  const gained = Game.discover(S.game, rows, tt);
  Game.ui.renderHud($('#hud'), S.game);
  if (gained) Game.ui.floatXp($('#next'), gained);
  const cards = rows.map(r => ({ name: r.korean_name || r.allergen_name, category: r.category || guessDexCategory(r), stars: Game.starsFor(r, tt) }));
  Game.ui.showDiscovery($('#overlay'), cards, () => goto(2));
}
// OCR 행에 category 가 없을 때 스탬프용 대략 분류 (guessCategory 는 screening 용 분류이므로 별도 세분화)
function guessDexCategory(r) {
  const s = `${r.allergen_name || ''} ${r.korean_name || ''}`;
  const c = guessCategory(r.allergen_name, r.korean_name);
  if (c === 'pollen') return /(tree|birch|oak|alder|자작|참나무|오리나무|나무)/i.test(s) ? 'pollen_tree'
    : /(weed|ragweed|mugwort|hop|돼지풀|쑥|환삼|잡초)/i.test(s) ? 'pollen_weed' : 'pollen_grass';
  if (c === 'shellfish') return 'food';
  return ['mite', 'animal', 'mold', 'food', 'insect'].includes(c) ? c : 'other';
}

/* =========================================================================
   STEP 2 — 스크리닝 (환자정보 + 질환력 + 약제 + 침범 장기)
   ========================================================================= */
// 알러젠 이름으로 대략적 카테고리 추정 (스크리닝 개인화 배너용)
function guessCategory(name, korean) {
  const s = `${name || ''} ${korean || ''}`.toLowerCase();
  if (/(dermatophagoides|mite|진드기|farinae|pteronyss)/.test(s)) return 'mite';
  if (/(pollen|birch|oak|alder|ragweed|mugwort|hop|timothy|grass|자작|참나무|오리나무|돼지풀|쑥|환삼|잔디|꽃가루)/.test(s)) return 'pollen';
  if (/(cat|dog|dander|고양이|개 |비듬|animal|반려)/.test(s)) return 'animal';
  if (/(alternaria|aspergillus|cladosporium|mold|penicillium|곰팡이)/.test(s)) return 'mold';
  if (/(cockroach|바퀴|roach)/.test(s)) return 'insect';
  if (/(shrimp|crab|lobster|prawn|새우|게|랍스터|가재|대하|꽃게)/.test(s)) return 'shellfish';
  if (/(egg|milk|peanut|wheat|soy|nut|fish|계란|우유|땅콩|밀|콩|견과|생선|food|음식|과일|fruit)/.test(s)) return 'food';
  return 'other';
}
const SCREEN_CAT_LABEL = (c) => t(`scat.${c}`);
const SCREEN_HINTS = (c) => { const k = `shint.${c}`; const v = t(k); return v === k ? '' : v; };
function screeningContext() {
  const tt = S.ocr.test_type;
  const pos = (S.ocr.results || []).filter(r => isPositive(r, tt));
  const cats = {};
  pos.forEach(r => { const c = guessCategory(r.allergen_name, r.korean_name); (cats[c] = cats[c] || []).push(r.korean_name || r.allergen_name); });
  return { pos, cats };
}

function renderScreening() {
  const o = S.options || { screening_options: { diseases: [], medications: [], organ_systems: [] } };
  const opt = o.screening_options;
  const sc = S.screening || (S.screening = { allergic_diseases: [], current_medications: [], organ_systems: [], pets: [], symptom_present: true });
  if (!sc.pets) sc.pets = [];
  const p = S.ocr.patient;
  const ctx = screeningContext();
  const catKeys = Object.keys(ctx.cats).filter(c => c !== 'other');
  const banner = ctx.pos.length ? `
    <div class="card soft" style="margin-bottom:18px;border-left:4px solid var(--brand)">
      <div style="font-weight:800;font-size:14px;margin-bottom:6px">${t('s2.banner_title', { n: ctx.pos.length })}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
        ${catKeys.map(c => `<span class="chip-opt sel" style="cursor:default">${SCREEN_CAT_LABEL(c)} ${ctx.cats[c].length}</span>`).join('')}
      </div>
      <div style="font-size:12.5px;color:var(--text-2)">${t('s2.banner_note')}</div>
      ${catKeys.map(c => SCREEN_HINTS(c) ? `<div class="q-help" style="margin-top:8px">${SCREEN_HINTS(c)}</div>` : '').join('')}
    </div>` : '';

  const chipList = (items, selected, key) => `<div class="chips" data-chipgroup="${key}">` +
    items.map(it => `<button type="button" aria-pressed="${selected.includes(it.code)}" class="chip-opt ${selected.includes(it.code) ? 'sel' : ''}" data-code="${it.code}">${esc(it.label)}</button>`).join('') + `</div>`;

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('s2.eyebrow')}</div>
        <h1>${t('s2.h1')}</h1>
        <p>${t('s2.p')}</p>
      </div>
      ${partialNotice()}
      ${banner}

      <div class="card soft" style="margin-bottom:20px">
        <div class="grid-3">
          <div class="field"><label>${t('s2.name')}</label><input class="input" id="pName" value="${esc(p.name || '')}" placeholder="${t('s2.name_ph')}" /></div>
          <div class="field"><label>${t('s2.age')}</label><input class="input" id="pAge" type="number" value="${p.age ?? ''}" placeholder="34" /></div>
          <div class="field"><label>${t('s2.gender')}</label>
            <select class="input" id="pGender">
              <option value="M" ${p.gender === 'M' || p.gender === '남' ? 'selected' : ''}>${t('s2.male')}</option>
              <option value="F" ${p.gender === 'F' || p.gender === '여' ? 'selected' : ''}>${t('s2.female')}</option>
            </select></div>
        </div>
        <div class="field" style="margin:0"><label>${t('s2.test_date')}</label><input class="input" id="pDate" type="date" value="${esc(p.test_date || '')}" /></div>
      </div>

      <div class="field"><label>${t('s2.diseases')} <span class="hint">${t('s2.multi')}</span></label>
        ${chipList(opt.diseases, sc.allergic_diseases, 'allergic_diseases')}</div>

      <div class="field"><label>${t('s2.meds')} <span class="hint">${t('s2.multi')}</span></label>
        ${chipList(opt.medications, sc.current_medications, 'current_medications')}
        <div id="ahWarn"></div></div>

      <div class="field"><label>${t('s2.organs')} <span class="hint">${t('s2.multi')}</span></label>
        ${chipList(opt.organ_systems, sc.organ_systems, 'organ_systems')}</div>

      <div class="field"><label>${t('s2.pets')} <span class="hint">${t('s2.multi')}</span></label>
        <div class="chips" data-chipgroup="pets">
          ${[['cat', t('s2.cat')], ['dog', t('s2.dog')], ['other', t('s2.other')], ['none', t('s2.none')]].map(([code,label]) =>
            `<button type="button" aria-pressed="${(sc.pets||[]).includes(code)}" class="chip-opt ${(sc.pets||[]).includes(code) ? 'sel' : ''}" data-code="${code}">${label}</button>`).join('')}
        </div>
        <input class="input ${(sc.pets||[]).includes('other') ? '' : 'hidden'}" id="petsOther" style="margin-top:8px" placeholder="${t('s2.pets_other_ph')}" value="${esc(sc.pets_other||'')}" />
      </div>

      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">${t('s2.btn_next')}</button>
      </div>
    </div>`;

  view().querySelectorAll('[data-chipgroup]').forEach(group => {
    group.addEventListener('click', e => {
      const chip = e.target.closest('.chip-opt'); if (!chip) return;
      const key = group.dataset.chipgroup, code = chip.dataset.code;
      let arr = sc[key];
      if (code === 'none') { arr = chip.classList.contains('sel') ? [] : ['none']; }
      else { arr = arr.filter(c => c !== 'none'); arr = arr.includes(code) ? arr.filter(c => c !== code) : [...arr, code]; }
      sc[key] = arr;
      if (key === 'current_medications') {
        $('#ahWarn').innerHTML = arr.includes('antihistamine')
          ? `<div class="notice flag" style="margin-top:10px">${t('s2.ah_warn')}</div>` : '';
      }
      if (key === 'pets') {
        const po = $('#petsOther'); if (po) po.classList.toggle('hidden', !arr.includes('other'));
      }
      renderScreeningChips(sc);
    });
  });
  if (sc.current_medications.includes('antihistamine'))
    $('#ahWarn').innerHTML = `<div class="notice flag" style="margin-top:10px">${t('s2.ah_warn')}</div>`;

  $('#back').addEventListener('click', () => goto(1));
  $('#next').addEventListener('click', submitScreening);
}
function renderScreeningChips(sc) {
  view().querySelectorAll('[data-chipgroup]').forEach(group => {
    const key = group.dataset.chipgroup;
    group.querySelectorAll('.chip-opt').forEach(c => { const on = sc[key].includes(c.dataset.code); c.classList.toggle('sel', on); c.setAttribute('aria-pressed', on); });
  });
}
async function submitScreening() {
  const sc = S.screening; const p = S.ocr.patient;
  p.name = $('#pName').value.trim(); p.age = $('#pAge').value ? parseInt($('#pAge').value) : null;
  p.gender = $('#pGender').value; p.test_date = $('#pDate').value || null;
  sc.antihistamine_recent = sc.current_medications.includes('antihistamine');
  const po = $('#petsOther'); sc.pets_other = (po && sc.pets.includes('other')) ? po.value.trim() : null;
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s2.preparing')}`;
  try {
    const res = await API.post('/api/questionnaire', { ocr: S.ocr, screening: sc });
    S.questionnaire = res.questionnaire; S.assessments = res.assessments;
    S.answers = Object.assign({}, res.questionnaire.answer_prefill || {});
    goto(3);
  } catch (e) { toast(t('s2.q_fail') + e.message); btn.disabled = false; btn.textContent = t('s2.btn_next'); }
}

/* =========================================================================
   STEP 3 — 적응형 감별 문진
   ========================================================================= */
// reveal 조건 평가: {any_of:[...]} | {question, equals} | {question, any:[...]} | {question, includes_any:[...]}
function condMet(cond) {
  if (!cond) return true;
  if (cond.any_of) return cond.any_of.some(condMet);
  const a = S.answers[cond.question];
  if (cond.equals !== undefined) return a === cond.equals;
  if (cond.any) return cond.any.includes(a);
  if (cond.includes_any) return Array.isArray(a) && a.some(x => cond.includes_any.includes(x));
  return true;
}
function renderQuestionnaire() {
  const q = S.questionnaire; const idx = q.allergen_index;
  const applyTags = (arr) => (arr && arr.length)
    ? `<div class="q-applies">${arr.map(k => `<span class="tag">${CAT_EMOJI[idx[k].category] || '•'} ${esc(idx[k].korean_name || idx[k].name)}</span>`).join('')}</div>` : '';
  const qCond = (qq) => qq.reveal_if_any ? { any_of: qq.reveal_if_any } : (qq.reveal_if || null);
  const isVisible = (qq) => condMet(qCond(qq));

  const questionHtml = (qq) => {
    const val = S.answers[qq.id];
    let control;
    if (qq.type === 'multi') {
      control = `<div class="chips" data-multi="${qq.id}">` +
        qq.options.map(o => `<button type="button" aria-pressed="${(val || []).includes(o.value)}" class="chip-opt ${(val || []).includes(o.value) ? 'sel' : ''}" data-v="${o.value}">${esc(o.label)}</button>`).join('') + `</div>`;
    } else {
      const rowClass = qq.options.length <= 3 && qq.options.every(o => o.label.length <= 12) ? 'choice-row' : 'choice-list';
      control = `<div class="${rowClass}" role="radiogroup" aria-label="${esc(qq.title)}" data-single="${qq.id}">` +
        qq.options.map(o => `<button type="button" role="radio" aria-checked="${val === o.value}" class="choice ${val === o.value ? 'sel' : ''}" data-v="${o.value}">
          <span class="radio"></span><span class="body"><span class="t">${esc(o.label)}</span>${o.hint ? `<span class="h">${esc(o.hint)}</span>` : ''}</span></button>`).join('') + `</div>`;
    }
    // reveal 조건: reveal_if({question, equals|any|includes_any}) 또는 reveal_if_any([...])
    const cond = qCond(qq);
    let revealAttr = '', hiddenCls = '';
    if (cond) { revealAttr = ` data-reveal='${esc(JSON.stringify(cond))}'`; if (!condMet(cond)) hiddenCls = ' hidden'; }
    return `<div class="q-block${hiddenCls}${Game.hasAnswer(val) ? ' answered' : ''}" data-qid="${qq.id}"${revealAttr}>
      <h3 class="q-title">${esc(qq.title)}</h3>
      ${qq.help ? `<div class="q-help">${esc(qq.help)}</div>` : ''}
      ${applyTags(qq.applies_to)}
      ${control}</div>`;
  };

  // reveal 재평가 + 새로 열린 문항에 '새 단서' 연출
  const applyReveals = () => view().querySelectorAll('[data-reveal]').forEach(b => {
    let c; try { c = JSON.parse(b.dataset.reveal); } catch (_) { return; }
    const wasHidden = b.classList.contains('hidden'); const show = condMet(c);
    b.classList.toggle('hidden', !show);
    if (wasHidden && show && !b.dataset.seen) {
      b.dataset.seen = '1'; b.classList.add('clue-new');
      const tag = document.createElement('span'); tag.className = 'clue-tag'; tag.textContent = t('s3.clue_new'); b.appendChild(tag);
      setTimeout(() => { tag.remove(); b.classList.remove('clue-new'); }, 3000);
    }
  });

  // 마무리 catch-all(food_general_react)에서, 이미 항원별 교차반응 문항으로 판정된
  // 음식(있다/없다 답변 완료)은 옵션에서 제거한다 — 중복 질문 방지.
  const CROSSREACT_PREFIX = 'crossreact__';
  const FOOD_GENERAL_ID = 'food_general_react';
  const refreshFoodGeneralOptions = () => {
    const block = view().querySelector(`[data-multi="${FOOD_GENERAL_ID}"]`);
    if (!block) return;
    const judged = new Set();
    for (const sec of q.sections) {
      for (const qq of sec.questions) {
        if (qq.type === 'multi' && qq.id.startsWith(CROSSREACT_PREFIX)) {
          const ans = S.answers[qq.id];
          if (ans && ans.length) qq.options.forEach(o => { if (o.value !== 'none') judged.add(o.value); });
        }
      }
    }
    block.querySelectorAll('.chip-opt').forEach(btn => {
      const v = btn.dataset.v;
      const isJudged = v !== 'none' && judged.has(v);
      btn.classList.toggle('hidden', isJudged);
      if (isJudged && (S.answers[FOOD_GENERAL_ID] || []).includes(v)) {
        S.answers[FOOD_GENERAL_ID] = S.answers[FOOD_GENERAL_ID].filter(x => x !== v);
        btn.classList.remove('sel');
      }
    });
  };

  // 챕터 링·하단 진행바 갱신 + 챕터 완료 XP(1회)
  const refreshProgress = () => {
    let totA = 0, totV = 0;
    q.sections.forEach((sec, si) => {
      const p = Game.chapterProgress(sec, S.answers, isVisible);
      totA += p.answered; totV += p.visible;
      const ring = view().querySelector(`.chapter[data-si="${si}"] .ring`);
      if (ring) { const pct = p.visible ? Math.round(p.answered / p.visible * 100) : 0; ring.style.setProperty('--p', pct); ring.dataset.label = `${p.answered}/${p.visible}`; ring.setAttribute('aria-valuenow', pct); ring.setAttribute('aria-valuetext', t('questbar.ring', { a: p.answered, v: p.visible })); }
      if (p.done) { const g = Game.completeChapter(S.game, sec.id || `sec${si}`); if (g) { Game.ui.floatXp(ring, g); Game.ui.sparkle(ring && ring.closest('.chapter')); } }
    });
    Game.ui.renderQuestBar($('#questBar'), totA, totV);
    Game.ui.renderHud($('#hud'), S.game);
  };

  const sections = q.sections.map((sec, si) => `
    <div class="chapter" data-si="${si}">
      <div class="ch-head"><div class="ring" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" data-label="0/0" style="--p:0"></div><h2 class="ch-title">${esc(sec.title)}</h2></div>
      ${sec.subtitle ? `<div class="q-sub">${esc(sec.subtitle)}</div>` : ''}
      ${sec.questions.map(questionHtml).join('')}
    </div>`).join('');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('s3.eyebrow')}</div>
        <h1>${t('s3.h1')}</h1>
        <p>${t('s3.p')}</p>
      </div>
      ${partialNotice()}
      ${sections}
      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">${t('s3.btn_next')}</button>
      </div>
    </div>`;

  const onAnswered = (block, btn, id) => {
    const g = Game.answer(S.game, id, S.answers[id]);
    if (g) Game.ui.floatXp(btn, g);
    block.classList.toggle('answered', Game.hasAnswer(S.answers[id]));
    applyReveals(); refreshFoodGeneralOptions(); refreshProgress();
  };
  view().querySelectorAll('[data-single]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.choice'); if (!b) return;
    S.answers[g.dataset.single] = b.dataset.v;
    g.querySelectorAll('.choice').forEach(c => { c.classList.toggle('sel', c === b); c.setAttribute('aria-checked', c === b); });
    onAnswered(g.closest('.q-block'), b, g.dataset.single);
  }));
  view().querySelectorAll('[data-multi]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.chip-opt'); if (!b) return;
    const id = g.dataset.multi, v = b.dataset.v; let arr = S.answers[id] || [];
    if (v === 'none' || v === 'no') arr = arr.includes(v) ? [] : [v];
    else { arr = arr.filter(x => x !== 'none' && x !== 'no'); arr = arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]; }
    S.answers[id] = arr;
    g.querySelectorAll('.chip-opt').forEach(c => { const on = arr.includes(c.dataset.v); c.classList.toggle('sel', on); c.setAttribute('aria-pressed', on); });
    onAnswered(g.closest('.q-block'), b, id);
  }));
  // 초기 상태: 이미 보이는 문항은 '새 단서' 연출 대상에서 제외
  view().querySelectorAll('[data-reveal]:not(.hidden)').forEach(b => { b.dataset.seen = '1'; });
  refreshFoodGeneralOptions(); refreshProgress();
  $('#back').addEventListener('click', () => goto(2));
  $('#next').addEventListener('click', submitClassify);
}
async function submitClassify() {
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s3.analyzing')}`;
  try {
    S.classify = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers, ui: 'quest', lang: I18N.getLang() });
    Game.applyResults(S.game, S.classify, S.answers);   // 판정→도감·배지·severeFlag (프레젠테이션 상태)
    goto(4);
  } catch (e) { toast(t('s3.classify_fail') + e.message); btn.disabled = false; btn.textContent = t('s3.btn_next'); }
}

/* =========================================================================
   STEP 4 — 결과
   ========================================================================= */
let RESULT_TAB = 'allergens';
function renderResults() {
  const c = S.classify; const cnt = c.summary.counts; const p = S.ocr.patient; const g = S.game;
  const lvl = Game.levelFor(g.xp);
  const severe = g.severeFlag;
  view().innerHTML = `
    <div class="scoreboard ${severe ? 'calm' : ''}" id="scoreboard">
      <div class="sb-eyebrow">${t('s4.eyebrow')}</div>
      <h1>${t(severe ? 's4.h1_calm' : 's4.h1_done', { name: esc(p.name || t('common.patient')) })}</h1>
      <p>${t('s4.p', { date: esc(p.test_date || '-'), n: c.summary.total_positive, lv: lvl.index + 1, title: esc(t(`level.${lvl.index}`)), xp: g.xp })}</p>
      <div class="trophies">
        <div class="trophy relevant"><div class="n">${cnt.clinically_relevant}</div><div class="l">${t('s4.trophy_rel')}</div></div>
        <div class="trophy sensitized"><div class="n">${cnt.sensitized_only}</div><div class="l">${t('s4.trophy_sens')}</div></div>
        <div class="trophy indet"><div class="n">${cnt.indeterminate}</div><div class="l">${t('s4.trophy_indet')}</div></div>
      </div>
      ${severe ? `<div class="alert-severe">${t('s4.severe')}</div>` : ''}
    </div>

    <div class="badges">${Game.BADGES.map((b, i) => `<div class="badge-item ${g.badges.includes(b.id) ? 'earned' : ''}" style="--i:${i}" title="${esc(t(`badge.${b.id}.desc`))}">
        <span class="b-icon">${b.icon}</span><span><div class="b-name">${esc(t(`badge.${b.id}.name`))}</div><div class="b-desc">${esc(t(`badge.${b.id}.desc`))}</div></span></div>`).join('')}</div>

    <div class="result-tabs">
      <button data-tab="allergens" class="${RESULT_TAB === 'allergens' ? 'active' : ''}">${t('s4.tab_dex')}</button>
      <button data-tab="report" class="${RESULT_TAB === 'report' ? 'active' : ''}">${t('s4.tab_report')}</button>
      <button data-tab="cardnews" class="${RESULT_TAB === 'cardnews' ? 'active' : ''}">${t('s4.tab_cardnews')}</button>
      <button data-tab="fhir" class="${RESULT_TAB === 'fhir' ? 'active' : ''}">${t('s4.tab_fhir')}</button>
    </div>
    <div id="tabBody"></div>

    <div class="actions">
      <button class="btn secondary" id="back">${t('s4.back_edit')}</button>
      <span class="spacer"></span>
      <button class="btn secondary" id="restart">${t('s4.restart')}</button>
    </div>`;

  if (!severe) Game.ui.sparkle($('#scoreboard'));   // 중증 시 축하 연출 억제
  view().querySelectorAll('.result-tabs button').forEach(b => b.addEventListener('click', () => { RESULT_TAB = b.dataset.tab; renderResults(); }));
  $('#back').addEventListener('click', () => goto(3));
  $('#restart').addEventListener('click', () => { S.ocr = null; S.screening = null; S.questionnaire = null; S.answers = {}; S.classify = null; S.maxReached = 0; S.game = Game.createState(); DEX_FILTER = 'all'; goto(0); });
  renderResultTab();
}

const ORDER = { clinically_relevant: 0, indeterminate: 1, sensitized_only: 2, not_assessed: 3 };
function renderResultTab() {
  const body = $('#tabBody'); const c = S.classify;
  if (RESULT_TAB === 'allergens') {
    const sorted = [...c.assessments].sort((a, b) => ORDER[a.relevance] - ORDER[b.relevance]);
    const filtered = DEX_FILTER === 'all' ? sorted : sorted.filter(a => a.relevance === DEX_FILTER);
    const cntOf = (k) => sorted.filter(a => a.relevance === k).length;
    body.innerHTML = `<div class="filter-chips">
        ${[['all', t('s4.filter_all', { n: sorted.length })], ['clinically_relevant', t('s4.filter_rel', { n: cntOf('clinically_relevant') })], ['indeterminate', t('s4.filter_indet', { n: cntOf('indeterminate') })], ['sensitized_only', t('s4.filter_sens', { n: cntOf('sensitized_only') })]]
          .map(([k, l]) => `<button type="button" aria-pressed="${DEX_FILTER === k}" class="chip-opt ${DEX_FILTER === k ? 'sel' : ''}" data-f="${k}">${l}</button>`).join('')}
      </div>
      <div class="dex-grid">${filtered.map(dexCard).join('') || `<p class="q-help">${t('s4.dex_empty')}</p>`}</div>`;
    body.querySelectorAll('.filter-chips .chip-opt').forEach(b => b.addEventListener('click', () => { DEX_FILTER = b.dataset.f; renderResultTab(); }));
    body.querySelectorAll('.dex-card').forEach(card => {
      const flip = () => { const on = card.classList.toggle('flip'); card.setAttribute('aria-expanded', on); };
      card.addEventListener('click', flip);
      card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); } });
    });
  } else if (RESULT_TAB === 'report') {
    const doc = c.report_document_html || `<div class="report-render">${c.report_html}</div>`;
    body.innerHTML = `<iframe class="report-frame" id="rpt"></iframe>
      <div class="download-row">
        <button class="btn primary sm" id="dlPdf">${t('s4.dl_pdf')}</button>
        <button class="btn subtle sm" id="dlHtml">${t('s4.dl_html')}</button>
        <button class="btn subtle sm" id="dlMd">${t('s4.dl_md')}</button>
      </div>`;
    const nm = (S.ocr.patient.name || 'patient');
    $('#rpt').srcdoc = doc;
    // PDF: 리포트 iframe 자체를 인쇄(대상 'PDF로 저장') → HTML 과 100% 동일 레이아웃
    $('#dlPdf').addEventListener('click', () => {
      const f = $('#rpt'); if (f && f.contentWindow) { f.contentWindow.focus(); f.contentWindow.print(); }
    });
    $('#dlHtml').addEventListener('click', () => download(`${nm}_allergy_report.html`, doc, 'text/html'));
    $('#dlMd').addEventListener('click', () => download(`${nm}_allergy_report.md`, c.report_markdown, 'text/markdown'));
  } else if (RESULT_TAB === 'cardnews') {
    body.innerHTML = `<iframe class="cardnews-frame" id="cn"></iframe>
      <div class="download-row"><button class="btn subtle sm" id="dlCn">${t('s4.dl_cardnews')}</button></div>`;
    $('#cn').srcdoc = c.cardnews_html;
    $('#dlCn').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_cardnews.html`, c.cardnews_html, 'text/html'));
  } else if (RESULT_TAB === 'fhir') {
    body.innerHTML = `<div class="card soft">
      <p class="q-help" style="margin-bottom:12px">${t('s4.fhir_help')}</p>
      <div id="fhirSummary" style="margin-bottom:12px"></div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <button class="btn ${FHIR_VIEW==='allergy'?'primary':'secondary'} sm" id="vAllergy">AllergyIntolerance</button>
        <button class="btn ${FHIR_VIEW==='obs'?'primary':'secondary'} sm" id="vObs">Observation</button>
        <span class="spacer" style="flex:1"></span>
        <button class="btn subtle sm" id="dlObs">⬇️ Observation</button>
        <button class="btn subtle sm" id="dlAllergy">⬇️ AllergyIntolerance</button>
      </div>
      <pre id="fhirPreview" style="max-height:420px;overflow:auto;background:var(--bg-subtle);padding:14px;border-radius:12px;font-size:12px">${t('common.loading')}</pre></div>`;
    loadFhir();
  }
}
// 도감 카드: 앞면(스탬프·이름·별·판정 도장) / 뒷면(판정 근거·지식베이스). 클릭·Enter 로 뒤집기.
function dexCard(a, i) {
  const rel = Game.VERDICT[a.relevance] ? a.relevance : 'not_assessed';
  const v = { tone: Game.VERDICT[rel].tone, stamp: t(`verdict.${rel}.stamp`), note: t(`verdict.${rel}.note`) };
  const d = S.game.discovered[a.allergen_name];
  const stars = d ? d.stars : ({ weak: 1, moderate: 2, strong: 3 }[a.strength] || 1);
  const hasOas = (a.oas_foods && a.oas_foods.length);
  const kb = [];
  if (a.season_label_ko) kb.push([t('dex.season'), esc(a.season_label_ko)]);
  if (hasOas) kb.push([t('dex.oas_foods'), esc(a.oas_foods.join(', ')) + t('dex.oas_note')]);
  if (a.crossreact_confirmed && a.crossreact_confirmed.length) kb.push([t('dex.crossreact_confirmed'), esc(a.crossreact_confirmed.join(', '))]);
  if (a.biology_ko) kb.push([t('dex.biology'), esc(a.biology_ko)]);
  if (a.exposure_environment_ko) kb.push([t('dex.exposure'), esc(a.exposure_environment_ko)]);
  if (a.cross_reactivity_ko) kb.push([t('dex.crossreact'), esc(a.cross_reactivity_ko)]);
  const av = (a.avoidance_control_ko || []).slice(0, 5);
  const kbHtml = kb.map(([k, val]) => `<div class="kb-item"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('') +
    (av.length ? `<div class="kb-item"><div class="k">${t('dex.avoidance')}</div><ul>${av.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '');
  const srcTag = a.source && a.source !== 'knowledge_base' ? `<span class="src-tag">${t('dex.source')}${a.source === 'wikipedia' ? 'Wikipedia' : t('dex.source_default')}</span>` : '';
  const sev = a.severity && a.severity !== 'none' && a.severity !== 'mild' ? t('dex.severity', { s: ({ moderate: t('dex.sev_moderate'), severe: t('dex.sev_severe'), anaphylaxis: t('dex.sev_ana') })[a.severity] || esc(a.severity) }) : '';
  return `<div class="dex-card" style="--i:${i}" tabindex="0" role="button" aria-expanded="false" aria-label="${t('dex.aria', { name: esc(a.korean_name || a.allergen_name), stamp: esc(v.stamp) })}">
    <div class="dex-inner">
      <div class="face front">
        <div class="stamp tone-${v.tone}">${Game.stampSvg(a.category)}</div>
        <h3 class="dex-name">${esc(a.korean_name || a.allergen_name)}</h3>
        <div class="dex-meta">${catLabel(a.category)} · ${a.test_value ?? '-'}${a.test_unit ? ' ' + esc(a.test_unit) : ''}${a.class_value != null ? ` · class ${esc(a.class_value)}` : ''}${sev}</div>
        ${Game.starsHtml(stars)}${hasOas ? `<div class="dex-meta">${t('dex.oas_badge')}</div>` : ''}
        <div class="verdict-stamp tone-${v.tone}">${esc(v.stamp)}</div>
        ${v.note ? `<div class="dex-note">${esc(v.note)}</div>` : ''}
        <span class="dex-flip-hint">${t('dex.flip_hint')}</span>
      </div>
      <div class="face back">
        <div class="rationale">${esc(a.rationale_ko || '')}${srcTag}</div>
        <div class="kb-grid">${kbHtml}</div>
      </div>
    </div>
  </div>`;
}
let FHIR_VIEW = 'allergy';
function fhirRenderPreview() {
  const f = S.fhir; if (!f) return;
  const bundle = FHIR_VIEW === 'obs' ? f.observation_bundle : f.allergy_intolerance_bundle;
  $('#fhirPreview').textContent = JSON.stringify(bundle, null, 2);
  const ai = (f.allergy_intolerance_bundle.entry || []).map(e => e.resource);
  const env = ai.filter(r => (r.category || []).includes('environment')).length;
  const food = ai.filter(r => (r.category || []).includes('food')).length;
  const obsN = (f.observation_bundle.entry || []).length;
  const conf = ai.filter(r => (r.verificationStatus?.coding?.[0]?.code) === 'confirmed').length;
  const el = $('#fhirSummary');
  if (el) el.innerHTML = `<div style="display:flex;flex-wrap:wrap;gap:8px">
    <span class="chip-opt sel" style="cursor:default">Observation ${obsN}</span>
    <span class="chip-opt sel" style="cursor:default">${t('s4.fhir_env', { n: env })}</span>
    <span class="chip-opt sel" style="cursor:default">${t('s4.fhir_food', { n: food })}</span>
    <span class="chip-opt sel" style="cursor:default">confirmed ${conf}</span></div>`;
}
async function loadFhir() {
  try {
    const f = await API.post('/api/fhir', { ocr: S.ocr, screening: S.screening, answers: S.answers });
    S.fhir = f;
    fhirRenderPreview();
    $('#vAllergy').addEventListener('click', () => { FHIR_VIEW = 'allergy'; renderResultTab(); });
    $('#vObs').addEventListener('click', () => { FHIR_VIEW = 'obs'; renderResultTab(); });
    $('#dlObs').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_observation.json`, JSON.stringify(f.observation_bundle, null, 2)));
    $('#dlAllergy').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_allergyintolerance.json`, JSON.stringify(f.allergy_intolerance_bundle, null, 2)));
  } catch (e) { const el = $('#fhirPreview'); if (el) el.textContent = t('s4.fhir_fail') + e.message; }
}

/* ---------------- theme ---------------- */
function initTheme() {
  const saved = localStorage.getItem('theme');
  if (saved) document.documentElement.setAttribute('data-theme', saved);
  updateThemeIcon();
  $('#themeToggle').addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme')
      || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next); localStorage.setItem('theme', next); updateThemeIcon();
  });
}
function updateThemeIcon() {
  const cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  $('#themeToggle').textContent = cur === 'dark' ? '☀️' : '🌙';
}

/* ---------------- boot ---------------- */
function initLang() {
  I18N.init(); I18N.setLang(I18N.getLang());
  const sel = $('#langSel');
  if (sel) {
    sel.innerHTML = I18N.LANGS.map(l => `<option value="${l.code}" ${l.code === I18N.getLang() ? 'selected' : ''}>${l.label}</option>`).join('');
    sel.addEventListener('change', () => { I18N.setLang(sel.value); render(); });
  }
}

(async function () {
  initTheme(); initLang();
  try { S.options = await API.health(); } catch (_) { S.options = { has_api_key: false, screening_options: { diseases: [], medications: [], organ_systems: [] } }; }
  render();
})();
