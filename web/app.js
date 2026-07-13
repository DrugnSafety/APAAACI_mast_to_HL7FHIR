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
};

let SHOW_ALL_ROWS = false;  // OCR 검토 표: 측정값만 보기(false) / 전체 보기(true)
const STEPS = ['검사지 업로드', 'OCR 검토', '문진·스크리닝', '증상 감별 문진', '결과 리포트'];
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

/* ---------------- navigation ---------------- */
function goto(step) {
  S.step = step; S.maxReached = Math.max(S.maxReached, step);
  window.scrollTo({ top: 0, behavior: 'smooth' });
  render();
}
function renderStepper() {
  const el = $('#stepper');
  el.innerHTML = STEPS.map((label, i) => {
    const cls = i === S.step ? 'active' : (i < S.step ? 'done' : '');
    const clickable = i <= S.maxReached && i !== S.step ? 'clickable' : '';
    return `<div class="step ${cls} ${clickable}" data-step="${i}">
      <div class="dot-row"><span class="num">${i < S.step ? '✓' : i + 1}</span><span class="line"></span></div>
      <span class="label">${esc(label)}</span></div>`;
  }).join('');
  el.querySelectorAll('.step.clickable').forEach(s =>
    s.addEventListener('click', () => goto(parseInt(s.dataset.step))));
}
function render() {
  renderStepper();
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
        <div class="eyebrow">STEP 1 · 검사지 업로드</div>
        <h1>알레르기 검사 결과지를 올려주세요</h1>
        <p>피부반응검사(SPT), MAST, UniCAP(ImmunoCAP) 결과지를 지원합니다. 사진이나 스캔 이미지를 올리면 자동으로 항목을 읽어냅니다.</p>
      </div>
      <div class="dropzone" id="dz">
        <div class="icon">📄</div>
        <h3>여기로 이미지를 끌어다 놓거나 클릭해서 선택</h3>
        <p>JPG · PNG · 10MB 이하 ${hasKey ? '' : '· (OCR을 쓰려면 서버에 OpenAI API 키가 필요합니다)'}</p>
        <input type="file" id="file" accept="image/*" class="hidden" />
      </div>
      <img id="preview" class="preview-img hidden" alt="업로드 미리보기" />
      <div id="uploadMsg"></div>
      <div class="upload-alt">
        <button class="btn secondary" id="btnDemo">✨ 데모 데이터로 체험하기</button>
        <button class="btn secondary" id="btnManual">⌨️ 결과를 직접 입력하기</button>
      </div>
    </div>`;

  const dz = $('#dz'), file = $('#file');
  dz.addEventListener('click', () => file.click());
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
  msg.innerHTML = `<div class="notice info" style="margin-top:16px"><span class="spinner" style="border-color:var(--brand-soft);border-top-color:var(--brand)"></span> OCR로 검사 항목을 읽는 중입니다…</div>`;
  try {
    const ocr = await API.ocr(f);
    S.ocr = normalizeOcr(ocr);
    goto(1);
  } catch (e) {
    msg.innerHTML = `<div class="notice warn" style="margin-top:16px">⚠️ ${esc(e.message)}<br/>아래 <b>데모 데이터</b>나 <b>직접 입력</b>으로 계속 진행할 수 있어요.</div>`;
  }
}
async function loadDemo() {
  try { S.ocr = normalizeOcr(await API.demo()); toast('데모 데이터를 불러왔어요'); goto(1); }
  catch (e) { toast('데모 로드 실패: ' + e.message); }
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
  if (p.facility) items.push(['🏥 검사기관', p.facility]);
  if (p.ordering_provider) items.push(['👨‍⚕️ 의뢰', p.ordering_provider]);
  if (p.test_date) items.push(['🗓️ 검사일', p.test_date]);
  if (p.report_date) items.push(['📄 보고일', p.report_date]);
  if (p.patient_id_external) items.push(['🔖 차트번호', p.patient_id_external]);
  if (!items.length) return '';
  return `<div class="card soft" style="margin-bottom:16px;display:flex;flex-wrap:wrap;gap:8px 22px">
    ${items.map(([k, v]) => `<div style="font-size:13px"><span style="color:var(--text-3);font-weight:700">${k}</span> <span style="color:var(--text)">${esc(v)}</span></div>`).join('')}
    <div style="flex-basis:100%;font-size:11.5px;color:var(--text-3)">↑ 검사지에서 자동 추출된 정보입니다. FHIR 변환 시 함께 매핑됩니다.</div>
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
  if (ps) ps.innerHTML = n ? `<div class="pos-summary">🔴 양성 알러젠 ${n}개 — 다음 단계에서 이 항목들의 실제 임상적 의미를 감별합니다.</div>` : '';
  const nx = $('#next'); if (nx) nx.disabled = !n;
}
function renderReview() {
  const tt = S.ocr.test_type;
  const allRows = S.ocr.results.map((r, i) => ({ r, i }));
  const shown = SHOW_ALL_ROWS ? allRows : allRows.filter(({ r }) => rowMeasured(r));
  const hiddenN = allRows.length - shown.length;
  const rows = shown.map(({ r, i }) => {
    const on = isPositive(r, tt);
    return `<tr data-i="${i}">
      <td style="color:var(--text-3);width:34px">${i + 1}</td>
      <td><input data-f="allergen_name" value="${esc(r.allergen_name)}" placeholder="예: Dermatophagoides farinae" /></td>
      <td><input data-f="korean_name" value="${esc(r.korean_name)}" placeholder="한글명(선택)" /></td>
      <td class="num" style="width:96px"><input data-f="value" type="number" step="0.01" value="${r.value ?? ''}" /></td>
      <td style="width:78px"><input data-f="unit" value="${esc(r.unit || '')}" /></td>
      <td style="width:66px"><input data-f="class_value" value="${r.class_value ?? ''}" placeholder="0-6" /></td>
      <td style="width:78px"><button class="pos-toggle ${on ? 'on' : 'off'}" data-toggle="${i}">${on ? '양성' : '음성'}</button></td>
      <td style="width:40px"><button class="btn danger-ghost" data-del="${i}" title="삭제">🗑️</button></td>
    </tr>`;
  }).join('');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">STEP 2 · OCR 검토</div>
        <h1>읽어온 결과를 확인·수정하세요</h1>
        <p>잘못 읽힌 값은 표에서 직접 고치고, <b>누락된 알러젠은 아래 ‘＋ 항목 추가’</b>로 넣을 수 있습니다. 양성/음성 판정도 클릭으로 바꿀 수 있어요.</p>
      </div>

      ${renderExtractedMeta(S.ocr.patient)}

      <div class="field-inline" style="margin-bottom:16px">
        <label style="font-weight:700;font-size:13.5px">검사 종류</label>
        <select class="input" id="testType" style="width:auto">
          ${['SPT', 'MAST', 'UniCAP'].map(t => `<option value="${t}" ${tt === t ? 'selected' : ''}>${t}</option>`).join('')}
        </select>
        <span class="hint">SPT=팽진(mm) · MAST/UniCAP=특이 IgE(kU/L)</span>
      </div>

      <div class="tbl-wrap">
        <table class="grid">
          <thead><tr>
            <th>#</th><th>알러젠</th><th>한글명</th><th>수치</th><th>단위</th><th>Class</th><th>판정</th><th></th>
          </tr></thead>
          <tbody id="tbody">${rows || `<tr><td colspan="8" style="text-align:center;color:var(--text-3);padding:26px">아직 항목이 없습니다. ‘＋ 항목 추가’로 넣어주세요.</td></tr>`}</tbody>
        </table>
      </div>

      <div class="tbl-toolbar">
        <button class="btn subtle sm" id="btnAdd">＋ 항목 추가</button>
        <button class="btn secondary sm" id="btnShowAll">${SHOW_ALL_ROWS ? '측정값만 보기' : `전체 보기${hiddenN ? ` (음성/0값 ${hiddenN}개)` : ''}`}</button>
        <span class="hint">${SHOW_ALL_ROWS ? '수치 0/음성 항목까지 모두 표시 중' : '수치가 측정된 항목만 표시 중'}</span>
        <span class="spacer"></span>
        <span class="hint">양성 <span class="badge-count" id="posN">${posCount()}</span></span>
      </div>

      <div id="posSummary">${posCount() ? `<div class="pos-summary">🔴 양성 알러젠 ${posCount()}개 — 다음 단계에서 이 항목들의 실제 임상적 의미를 감별합니다.</div>` : ''}</div>

      <div class="actions">
        <button class="btn secondary" id="back">← 이전</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next" ${posCount() ? '' : 'disabled'}>양성 항목 감별 시작 →</button>
      </div>
    </div>`;

  // bindings
  $('#testType').addEventListener('change', e => {
    S.ocr.test_type = e.target.value;
    S.ocr.results.forEach(r => {
      if (!r.unit || r.unit === 'mm' || r.unit === 'kU/L') r.unit = e.target.value === 'SPT' ? 'mm' : 'kU/L';
      r.interpretation = deriveInterp(r, S.ocr.test_type); // 검사종류 바뀌면 판정 재계산
    });
    renderReview();
  });
  $('#tbody').addEventListener('input', e => {
    const tr = e.target.closest('tr'); if (!tr) return;
    const i = +tr.dataset.i, f = e.target.dataset.f; if (f == null) return;
    let v = e.target.value;
    if (f === 'value') v = v === '' ? null : parseFloat(v);
    if (f === 'class_value') v = v === '' ? null : v;
    S.ocr.results[i][f] = v;
    // 수치/Class 를 수정하면 즉시 양성/음성 판정을 다시 계산하고, 그 행의 판정 버튼·요약을 제자리 갱신
    if (['value', 'class_value', 'mean_mm'].includes(f)) {
      const on = deriveInterp(S.ocr.results[i], S.ocr.test_type);
      S.ocr.results[i].interpretation = on;
      const tog = tr.querySelector('.pos-toggle');
      if (tog) { const pos = on === 'Positive'; tog.textContent = pos ? '양성' : '음성'; tog.classList.toggle('on', pos); tog.classList.toggle('off', !pos); }
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
  $('#btnAdd').addEventListener('click', () => { SHOW_ALL_ROWS = true; addRow(); renderReview(); setTimeout(() => { const inp = view().querySelector('tbody tr:last-child input'); inp && inp.focus(); }, 0); });
  $('#btnShowAll').addEventListener('click', () => { SHOW_ALL_ROWS = !SHOW_ALL_ROWS; renderReview(); });
  $('#back').addEventListener('click', () => goto(0));
  $('#next').addEventListener('click', () => goto(2));
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
const SCREEN_CAT_LABEL = { mite: '집먼지진드기', pollen: '꽃가루', animal: '동물', mold: '곰팡이', insect: '곤충(바퀴)', shellfish: '갑각류', food: '음식', other: '기타' };
const SCREEN_HINTS = {
  pollen: '🌳 꽃가루 양성 — 증상이 <b>특정 계절</b>에 심해지는지가 핵심입니다. 다음 단계에서 시즌별로 확인합니다.',
  mite: '🛏️ 집먼지진드기 양성 — <b>연중·아침·먼지 노출</b> 시 증상, 그리고 <b>새우·게 교차반응</b>을 다음 단계에서 확인합니다.',
  animal: '🐾 동물 양성 — 해당 동물 <b>접촉 시 증상</b> 여부가 중요합니다.',
  shellfish: '🦐 갑각류 양성 — <b>실제로 먹었을 때</b> 반응하는지(강양성이어도 잘 먹으면 감작만)를 확인합니다.',
  food: '🍽️ 음식 양성 — 먹었을 때 <b>어떤 증상</b>(입·목/피부/소화기/호흡/전신)이 나오는지 확인합니다.',
  mold: '🍄 곰팡이 양성 — <b>습한 환경</b>에서 악화되는지 확인합니다.',
  insect: '🪳 바퀴 양성 — 실내 환경과의 연관을 확인합니다.',
};
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
      <div style="font-weight:800;font-size:14px;margin-bottom:6px">🔬 검사에서 양성으로 확인된 알러젠 ${ctx.pos.length}개</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
        ${catKeys.map(c => `<span class="chip-opt sel" style="cursor:default">${SCREEN_CAT_LABEL[c]} ${ctx.cats[c].length}</span>`).join('')}
      </div>
      <div style="font-size:12.5px;color:var(--text-2)">아래 문진과 <b>다음 단계 감별 질문</b>이 이 결과에 맞춰 자동 구성됩니다.</div>
      ${catKeys.map(c => SCREEN_HINTS[c] ? `<div class="q-help" style="margin-top:8px">${SCREEN_HINTS[c]}</div>` : '').join('')}
    </div>` : '';

  const chipList = (items, selected, key) => `<div class="chips" data-chipgroup="${key}">` +
    items.map(it => `<button type="button" class="chip-opt ${selected.includes(it.code) ? 'sel' : ''}" data-code="${it.code}">${esc(it.label)}</button>`).join('') + `</div>`;

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">STEP 3 · 문진·스크리닝</div>
        <h1>몇 가지만 알려주세요</h1>
        <p>기저 알레르기 질환과 복용 약제, 증상이 나타나는 부위를 확인합니다. 이 정보로 감별 정확도가 올라갑니다.</p>
      </div>

      ${banner}

      <div class="card soft" style="margin-bottom:20px">
        <div class="grid-3">
          <div class="field"><label>이름</label><input class="input" id="pName" value="${esc(p.name || '')}" placeholder="홍길동" /></div>
          <div class="field"><label>나이</label><input class="input" id="pAge" type="number" value="${p.age ?? ''}" placeholder="34" /></div>
          <div class="field"><label>성별</label>
            <select class="input" id="pGender">
              <option value="M" ${p.gender === 'M' || p.gender === '남' ? 'selected' : ''}>남</option>
              <option value="F" ${p.gender === 'F' || p.gender === '여' ? 'selected' : ''}>여</option>
            </select></div>
        </div>
        <div class="field" style="margin:0"><label>검사일</label><input class="input" id="pDate" type="date" value="${esc(p.test_date || '')}" /></div>
      </div>

      <div class="field"><label>진단받았거나 앓고 있는 알레르기 질환 <span class="hint">(복수 선택)</span></label>
        ${chipList(opt.diseases, sc.allergic_diseases, 'allergic_diseases')}</div>

      <div class="field"><label>최근 복용 중인 약 <span class="hint">(복수 선택)</span></label>
        ${chipList(opt.medications, sc.current_medications, 'current_medications')}
        <div id="ahWarn"></div></div>

      <div class="field"><label>알레르기 증상이 나타나는 부위 <span class="hint">(복수 선택)</span></label>
        ${chipList(opt.organ_systems, sc.organ_systems, 'organ_systems')}</div>

      <div class="field"><label>반려동물을 키우거나 자주 접촉하나요? <span class="hint">(복수 선택)</span></label>
        <div class="chips" data-chipgroup="pets">
          ${[['cat','🐱 고양이'],['dog','🐶 강아지'],['other','기타'],['none','키우지 않음']].map(([code,label]) =>
            `<button type="button" class="chip-opt ${(sc.pets||[]).includes(code) ? 'sel' : ''}" data-code="${code}">${label}</button>`).join('')}
        </div>
        <input class="input ${(sc.pets||[]).includes('other') ? '' : 'hidden'}" id="petsOther" style="margin-top:8px" placeholder="기타 동물을 입력하세요 (예: 햄스터, 토끼, 새)" value="${esc(sc.pets_other||'')}" />
      </div>

      <div class="actions">
        <button class="btn secondary" id="back">← 이전</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">감별 문진으로 →</button>
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
          ? `<div class="notice flag" style="margin-top:10px">⚠️ 항히스타민제 복용 중이라면 피부반응검사(SPT)에서 <b>위음성</b>이 나올 수 있어, 결과 해석에 주의가 필요합니다.</div>` : '';
      }
      if (key === 'pets') {
        const po = $('#petsOther'); if (po) po.classList.toggle('hidden', !arr.includes('other'));
      }
      renderScreeningChips(sc);
    });
  });
  if (sc.current_medications.includes('antihistamine'))
    $('#ahWarn').innerHTML = `<div class="notice flag" style="margin-top:10px">⚠️ 항히스타민제 복용 중이라면 피부반응검사(SPT)에서 <b>위음성</b>이 나올 수 있어, 결과 해석에 주의가 필요합니다.</div>`;

  $('#back').addEventListener('click', () => goto(1));
  $('#next').addEventListener('click', submitScreening);
}
function renderScreeningChips(sc) {
  view().querySelectorAll('[data-chipgroup]').forEach(group => {
    const key = group.dataset.chipgroup;
    group.querySelectorAll('.chip-opt').forEach(c => c.classList.toggle('sel', sc[key].includes(c.dataset.code)));
  });
}
async function submitScreening() {
  const sc = S.screening; const p = S.ocr.patient;
  p.name = $('#pName').value.trim(); p.age = $('#pAge').value ? parseInt($('#pAge').value) : null;
  p.gender = $('#pGender').value; p.test_date = $('#pDate').value || null;
  sc.antihistamine_recent = sc.current_medications.includes('antihistamine');
  const po = $('#petsOther'); sc.pets_other = (po && sc.pets.includes('other')) ? po.value.trim() : null;
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 문진 준비 중…';
  try {
    const res = await API.post('/api/questionnaire', { ocr: S.ocr, screening: sc });
    S.questionnaire = res.questionnaire; S.assessments = res.assessments;
    S.answers = Object.assign({}, res.questionnaire.answer_prefill || {});
    goto(3);
  } catch (e) { toast('문진 생성 실패: ' + e.message); btn.disabled = false; btn.textContent = '감별 문진으로 →'; }
}

/* =========================================================================
   STEP 3 — 적응형 감별 문진
   ========================================================================= */
function renderQuestionnaire() {
  const q = S.questionnaire; const idx = q.allergen_index;
  const applyTags = (arr) => (arr && arr.length)
    ? `<div class="q-applies">${arr.map(k => `<span class="tag">${CAT_EMOJI[idx[k].category] || '•'} ${esc(idx[k].korean_name || idx[k].name)}</span>`).join('')}</div>` : '';

  const questionHtml = (qq) => {
    const val = S.answers[qq.id];
    let control;
    if (qq.type === 'multi') {
      control = `<div class="chips" data-multi="${qq.id}">` +
        qq.options.map(o => `<button type="button" class="chip-opt ${(val || []).includes(o.value) ? 'sel' : ''}" data-v="${o.value}">${esc(o.label)}</button>`).join('') + `</div>`;
    } else {
      const rowClass = qq.options.length <= 3 && qq.options.every(o => o.label.length <= 12) ? 'choice-row' : 'choice-list';
      control = `<div class="${rowClass}" data-single="${qq.id}">` +
        qq.options.map(o => `<button type="button" class="choice ${val === o.value ? 'sel' : ''}" data-v="${o.value}">
          <span class="radio"></span><span class="body"><span class="t">${esc(o.label)}</span>${o.hint ? `<span class="h">${esc(o.hint)}</span>` : ''}</span></button>`).join('') + `</div>`;
    }
    // reveal_if: 특정 답변일 때만 노출 (예: OAS='예'일 때 교차반응 음식 질문)
    let revealAttr = '', hiddenCls = '';
    if (qq.reveal_if) {
      revealAttr = ` data-revealq="${esc(qq.reveal_if.question)}" data-revealv="${esc(qq.reveal_if.equals)}"`;
      if (S.answers[qq.reveal_if.question] !== qq.reveal_if.equals) hiddenCls = ' hidden';
    }
    return `<div class="q-block${hiddenCls}"${revealAttr}>
      <div class="q-title">${esc(qq.title)}</div>
      ${qq.help ? `<div class="q-help">${esc(qq.help)}</div>` : ''}
      ${applyTags(qq.applies_to)}
      ${control}</div>`;
  };
  const applyReveals = () => view().querySelectorAll('[data-revealq]').forEach(b => {
    b.classList.toggle('hidden', S.answers[b.dataset.revealq] !== b.dataset.revealv);
  });

  const sections = q.sections.map(sec => `
    <div class="q-section">
      <div class="q-shead"><h2>${esc(sec.title)}</h2></div>
      ${sec.subtitle ? `<div class="q-sub">${esc(sec.subtitle)}</div>` : ''}
      ${sec.questions.map(questionHtml).join('')}
    </div>`).join('');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">STEP 4 · 증상 감별 문진</div>
        <h1>증상과 알러젠을 연결해볼게요</h1>
        <p>검사 양성이 <b>실제 알레르기</b>인지 <b>감작(양성이지만 증상 없음)</b>인지 가리는 핵심 단계입니다. 아는 만큼만 답하시고, 모르면 ‘잘 모르겠어요’를 선택하세요.</p>
      </div>
      ${sections}
      <div class="actions">
        <button class="btn secondary" id="back">← 이전</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">결과 리포트 생성 →</button>
      </div>
    </div>`;

  view().querySelectorAll('[data-single]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.choice'); if (!b) return;
    S.answers[g.dataset.single] = b.dataset.v;
    g.querySelectorAll('.choice').forEach(c => c.classList.toggle('sel', c === b));
    applyReveals();
  }));
  view().querySelectorAll('[data-multi]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.chip-opt'); if (!b) return;
    const id = g.dataset.multi, v = b.dataset.v; let arr = S.answers[id] || [];
    if (v === 'none') arr = arr.includes('none') ? [] : ['none'];
    else { arr = arr.filter(x => x !== 'none'); arr = arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]; }
    S.answers[id] = arr;
    g.querySelectorAll('.chip-opt').forEach(c => c.classList.toggle('sel', arr.includes(c.dataset.v)));
  }));
  $('#back').addEventListener('click', () => goto(2));
  $('#next').addEventListener('click', submitClassify);
}
async function submitClassify() {
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 분석 중…';
  try {
    S.classify = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers });
    goto(4);
  } catch (e) { toast('분석 실패: ' + e.message); btn.disabled = false; btn.textContent = '결과 리포트 생성 →'; }
}

/* =========================================================================
   STEP 4 — 결과
   ========================================================================= */
let RESULT_TAB = 'allergens';
function renderResults() {
  const c = S.classify; const cnt = c.summary.counts; const p = S.ocr.patient;
  view().innerHTML = `
    <div class="result-hero">
      <h1>${esc(p.name || '환자')}님의 알레르기 결과 요약</h1>
      <p>검사일 ${esc(p.test_date || '-')} · 양성 ${c.summary.total_positive}개 항목을 증상과 대조해 감별했습니다.</p>
      <div class="stat-row">
        <div class="stat"><div class="n">${cnt.clinically_relevant}</div><div class="l">🔴 실제 주의</div></div>
        <div class="stat"><div class="n">${cnt.sensitized_only}</div><div class="l">⚪ 감작만</div></div>
        <div class="stat"><div class="n">${cnt.indeterminate}</div><div class="l">🟡 관찰 필요</div></div>
      </div>
    </div>

    <div class="result-tabs">
      <button data-tab="allergens" class="${RESULT_TAB === 'allergens' ? 'active' : ''}">알러젠별 감별</button>
      <button data-tab="report" class="${RESULT_TAB === 'report' ? 'active' : ''}">맞춤 리포트</button>
      <button data-tab="cardnews" class="${RESULT_TAB === 'cardnews' ? 'active' : ''}">카드뉴스</button>
      <button data-tab="fhir" class="${RESULT_TAB === 'fhir' ? 'active' : ''}">FHIR 내보내기</button>
    </div>
    <div id="tabBody"></div>

    <div class="actions">
      <button class="btn secondary" id="back">← 문진 수정</button>
      <span class="spacer"></span>
      <button class="btn secondary" id="restart">처음부터 다시</button>
    </div>`;

  view().querySelectorAll('.result-tabs button').forEach(b => b.addEventListener('click', () => { RESULT_TAB = b.dataset.tab; renderResults(); }));
  $('#back').addEventListener('click', () => goto(3));
  $('#restart').addEventListener('click', () => { S.ocr = null; S.screening = null; S.questionnaire = null; S.answers = {}; S.classify = null; S.maxReached = 0; goto(0); });
  renderResultTab();
}

const ORDER = { clinically_relevant: 0, indeterminate: 1, sensitized_only: 2, not_assessed: 3 };
function renderResultTab() {
  const body = $('#tabBody'); const c = S.classify;
  if (RESULT_TAB === 'allergens') {
    const sorted = [...c.assessments].sort((a, b) => ORDER[a.relevance] - ORDER[b.relevance]);
    body.innerHTML = sorted.map(cardForAllergen).join('') || '<p class="q-help">양성 알러젠이 없습니다.</p>';
    body.querySelectorAll('.ac-head').forEach(h => h.addEventListener('click', () => h.closest('.allergen-card').classList.toggle('open')));
  } else if (RESULT_TAB === 'report') {
    body.innerHTML = `<div class="card"><div class="report-render">${c.report_html}</div></div>
      <div class="download-row"><button class="btn subtle sm" id="dlMd">📝 Markdown 저장</button></div>`;
    $('#dlMd').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_allergy_report.md`, c.report_markdown, 'text/markdown'));
  } else if (RESULT_TAB === 'cardnews') {
    body.innerHTML = `<iframe class="cardnews-frame" id="cn"></iframe>
      <div class="download-row"><button class="btn subtle sm" id="dlCn">🖼️ 카드뉴스 HTML 저장</button></div>`;
    $('#cn').srcdoc = c.cardnews_html;
    $('#dlCn').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_cardnews.html`, c.cardnews_html, 'text/html'));
  } else if (RESULT_TAB === 'fhir') {
    body.innerHTML = `<div class="card soft">
      <p class="q-help" style="margin-bottom:12px"><b>Observation</b> = 전체 검사결과(양성+음성), <b>AllergyIntolerance</b> = 양성/의심 알러젠(교차반응 음식 포함). verificationStatus: 임상적 유발 확인=confirmed, 감작만/미확정=unconfirmed.</p>
      <div id="fhirSummary" style="margin-bottom:12px"></div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <button class="btn ${FHIR_VIEW==='allergy'?'primary':'secondary'} sm" id="vAllergy">AllergyIntolerance</button>
        <button class="btn ${FHIR_VIEW==='obs'?'primary':'secondary'} sm" id="vObs">Observation</button>
        <span class="spacer" style="flex:1"></span>
        <button class="btn subtle sm" id="dlObs">⬇️ Observation</button>
        <button class="btn subtle sm" id="dlAllergy">⬇️ AllergyIntolerance</button>
      </div>
      <pre id="fhirPreview" style="max-height:420px;overflow:auto;background:var(--bg-subtle);padding:14px;border-radius:12px;font-size:12px">불러오는 중…</pre></div>`;
    loadFhir();
  }
}
function cardForAllergen(a) {
  const hasOas = (a.oas_foods && a.oas_foods.length);
  const kb = [];
  if (a.season_label_ko) kb.push(['시즌', esc(a.season_label_ko)]);
  if (hasOas) kb.push(['🍎 구강알레르기증후군(OAS) 유발 음식', esc(a.oas_foods.join(', ')) + ' — 생것 섭취 시 입·목 증상 주의, 대개 익히면 완화']);
  if (a.biology_ko) kb.push(['특성·생활사', esc(a.biology_ko)]);
  if (a.exposure_environment_ko) kb.push(['주요 노출 환경', esc(a.exposure_environment_ko)]);
  if (a.cross_reactivity_ko) kb.push(['교차반응', esc(a.cross_reactivity_ko)]);
  if (a.oral_allergy_syndrome_ko) kb.push(['구강알레르기증후군(일반)', esc(a.oral_allergy_syndrome_ko)]);
  const av = (a.avoidance_control_ko || []).slice(0, 5);
  const kbHtml = kb.map(([k, v]) => `<div class="kb-item"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('') +
    (av.length ? `<div class="kb-item"><div class="k">회피·관리 수칙</div><ul>${av.map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>` : '');
  const srcTag = a.source && a.source !== 'knowledge_base' ? `<span class="src-tag"> · 출처: ${a.source === 'wikipedia' ? 'Wikipedia' : '기본값'}</span>` : '';
  const oasBadge = hasOas ? `<span class="rel-badge" style="background:var(--indet-soft);color:var(--indet);border:1px solid var(--indet-border)">🍎 OAS</span>` : '';
  return `<div class="allergen-card">
    <div class="ac-head">
      <span class="emoji">${CAT_EMOJI[a.category] || '•'}</span>
      <div class="ac-title">
        <div class="nm">${esc(a.korean_name || a.allergen_name)}</div>
        <div class="meta">${CAT_LABEL[a.category] || a.category} · ${a.test_value ?? '-'}${a.test_unit ? ' ' + esc(a.test_unit) : ''}${a.class_value != null ? ` · class ${esc(a.class_value)}` : ''}${a.strength ? ` · 감작 ${({weak:'약',moderate:'중',strong:'강'})[a.strength] || a.strength}` : ''}${srcTag}</div>
      </div>
      ${oasBadge}
      <span class="rel-badge ${a.relevance}">${REL_LABEL[a.relevance]}</span>
      <span class="chevron">▾</span>
    </div>
    <div class="ac-body">
      <div class="rationale">${esc(a.rationale_ko || '')}</div>
      <div class="kb-grid">${kbHtml}</div>
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
    <span class="chip-opt sel" style="cursor:default">환경 알러젠 ${env}</span>
    <span class="chip-opt sel" style="cursor:default">음식 알러젠 ${food}</span>
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
  } catch (e) { const el = $('#fhirPreview'); if (el) el.textContent = 'FHIR 생성 실패: ' + e.message; }
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
(async function () {
  initTheme();
  try { S.options = await API.health(); } catch (_) { S.options = { has_api_key: false, screening_options: { diseases: [], medications: [], organ_systems: [] } }; }
  render();
})();
