/* =========================================================================
   알레르기 리포트 플랫폼 — 프론트엔드 (vanilla JS SPA)
   단계: 0 업로드 → 1 OCR검토 → 2 스크리닝 → 3 문진 → 4 결과
   ========================================================================= */
'use strict';

// FastAPI 오류 detail(문자열 또는 검증오류 배열)을 사람이 읽을 수 있는 문장으로 변환
function fmtErr(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (typeof detail.message === 'string') return detail.message;   // {code, message} 형식
  if (Array.isArray(detail)) {
    return detail.map(e => {
      const loc = Array.isArray(e.loc) ? e.loc.slice(-2).join('.') : '';
      return (loc ? loc + ': ' : '') + (e.msg || JSON.stringify(e));
    }).join(' / ');
  }
  try { return JSON.stringify(detail); } catch (_) { return fallback; }
}

// 서버 오류는 {detail: {code, message}} — 화면이 코드별 안내문을 고를 수 있게 code·status·Retry-After(초)를 함께 넘긴다
async function apiError(r, fallback) {
  const e = await r.json().catch(() => ({}));
  return Object.assign(new Error(fmtErr(e.detail, fallback)), {
    status: r.status, code: (e.detail && e.detail.code) || null, retryAfter: parseInt(r.headers.get('Retry-After'), 10) || null });
}

const API = {
  async health(lang) { return (await fetch('/api/health?lang=' + encodeURIComponent(lang || 'ko'), { headers: llmHeaders() })).json(); },
  async demo() { return (await fetch('/api/ocr/demo')).json(); },
  async allergen(name) { return (await fetch('/api/allergen?name=' + encodeURIComponent(name))).json(); },
  async allergens() { const r = await fetch('/api/allergens'); if (!r.ok) throw new Error('/api/allergens ' + r.status); return r.json(); },
  async ocr(file) {
    const fd = new FormData(); fd.append('file', file);
    const r = await fetch('/api/ocr', { method: 'POST', body: fd, headers: llmHeaders() });
    if (!r.ok) throw await apiError(r, 'OCR 실패');
    return r.json();
  },
  async post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: llmHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body) });
    if (!r.ok) throw await apiError(r, path + ' 실패');
    return r.json();
  },
};

const UI_NAME = 'classic';   // 서버에 보내는 ui 값 — 함께 쓰는 코드(web/shared.js)가 읽는다
// 알러젠 자동완성·결과 언어 맞추기·이메일 받기·결과 상담·언어 전환·카드뉴스 체크 저장은 퀘스트 UI 와 함께 쓴다 → web/shared.js

const S = {
  step: 0,
  maxReached: 0,
  ocr: null,
  screening: null,
  options: null,
  questionnaire: null,
  answers: {},
  classify: null,
  chat: { messages: [], suggestions: null, busy: false, hasKey: null },  // 결과 상담
  qLang: null, qState: null,   // 문진을 받아 온 언어와 그 번역 상태('ok' | 'partial' | 'none')
  sessionId: null,   // 서버 저장 세션 — 판정(classify)이 발급하고, 이후 재판정·상담·메일이 같은 id 를 쓴다. 새 결과지를 올리면 비운다
  mail: newMail(),   // 결과 이메일 받기 패널 상태
  outputs: {},       // 이번 판정의 언어별 산출물(lang → classify 모양). S.classify 는 지금 화면에 보이는 언어의 것
  langSync: null,    // 화면 언어의 결과를 아직 못 받았을 때: { lang, state: 'preparing' | 'failed' }
};

let REVIEW_TAB = 'measured';  // OCR 검토 표 탭: 'measured'(수치>0) / 'zero'(수치 0·미측정)
const t = (k, v) => I18N.t(k, v);   // 화면 문구 번역 (web/i18n.js). 서버 생성 콘텐츠는 한국어.
const STEPS = () => [0, 1, 2, 3, 4].map(i => t(`c.step.${i}`));
const catLabel = (c) => { const k = `cat.${c}`; const v = t(k); return v === k ? (c || '') : v; };
// 비한국어 화면의 번역 안내. '기계 번역했다'는 문구는 실제로 번역됐을 때만 쓴다 — 문진(scope 'q')은 받아 온 문항의
// 번역 상태(S.qState)로, 아직 받은 내용이 없는 화면은 번역 엔진을 쓸 수 있는지(/api/health has_api_key)로 고른다.
function partialNotice(scope) {
  const lang = I18N.getLang();
  let state = null, shown = 'ko';
  if (scope === 'q' && S.questionnaire && S.qLang) {
    if (S.qLang === lang) state = S.qState || 'ok';
    else { state = 'none'; shown = S.qState === 'none' ? 'ko' : S.qLang; }   // 문항은 받아 온 언어 그대로다
  }
  const key = I18N.noticeKey(lang, state, !!(S.options && S.options.has_api_key));
  if (!key) return '';
  return `<div class="notice ${key === 'notice.partial' ? 'info' : 'warn'}" id="trNotice" data-tr="${key.split('.')[1]}" style="margin-bottom:14px">${t(key, { target: esc(langName(lang)), shown: esc(langName(shown)) })}</div>`;
}
const CAT_EMOJI = { mite: '🛏️', animal: '🐾', pollen_tree: '🌳', pollen_grass: '🌾', pollen_weed: '🍂', mold: '🍄', insect: '🪳', venom: '🐝', food: '🍽️', latex: '🧤', drug: '💊', control: '🧪', other: '•' };
const REL_LABEL = { clinically_relevant: '실제 주의', sensitized_only: '감작만', indeterminate: '관찰 필요', not_assessed: '미평가' };
const CAT_LABEL = { mite: '집먼지진드기', animal: '동물', pollen_tree: '나무 꽃가루', pollen_grass: '잔디 꽃가루', pollen_weed: '잡초 꽃가루', mold: '곰팡이', insect: '곤충', venom: '벌독', food: '음식', latex: '라텍스', drug: '약물', control: '검사 대조', other: '기타' };

/* ---------------- utils ---------------- */
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const view = () => $('#view');
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), Math.min(8000, Math.max(2200, String(msg).length * 70))); }   // 긴 안내(대기 시간 등)는 읽을 만큼 더 띄운다
const fmtRich = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');   // 서버 문구의 **굵게** 만 허용
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
/* ---------------- AI 엔진 선택 (OpenAI / 연구실 Ollama) ----------------
   선택은 브라우저에 기억하고, 모든 API 호출에 X-LLM-Backend 헤더로 보낸다.
   서버는 이 헤더로 OCR(비전 모델)·상담·번역(텍스트 모델)의 백엔드를 고른다. */
function getEngine() { try { return localStorage.getItem('llmBackend') || ''; } catch (_) { return ''; } }
function llmHeaders(extra) { const b = getEngine(); return Object.assign({}, extra || {}, b ? { 'X-LLM-Backend': b } : {}); }
function initEngine() {
  const sel = document.getElementById('engineSel');
  const llm = S.options && S.options.llm;
  if (!sel || !llm) { if (sel) sel.classList.add('hidden'); return; }
  const usable = (k) => !!(llm.backends[k] && llm.backends[k].available);
  const cur = usable(getEngine()) ? getEngine() : llm.default;   // 골라 둔 엔진을 쓸 수 없으면 서버가 실제로 쓰는 쪽을 보인다
  const anyUsable = Object.keys(llm.backends).some(usable);
  // 쓸 수 없는 엔진은 모델 이름 대신 '사용 불가'로 적고 고를 수 없게 한다
  sel.innerHTML = Object.entries(llm.backends).map(([k, b]) =>
    `<option value="${k}" ${k === cur ? 'selected' : ''} ${b.available ? '' : 'disabled'}>` +
    `${esc(k === 'ollama' ? t('eng.ollama') : 'OpenAI')}` +
    (b.available ? ` · ${esc(b.vision_model)}${b.encrypted ? '' : ' ⚠️'}` : ` (${t('eng.unavailable')})`) + `</option>`).join('');
  const b = llm.backends[cur];
  sel.disabled = !anyUsable;
  sel.classList.toggle('unavail', !usable(cur));
  sel.title = !usable(cur) ? t('eng.none')
    : `${t('eng.label')}: OCR ${b.vision_model} / ${t('eng.chat')} ${b.chat_model}` + (b.encrypted ? '' : `\n${t('eng.insecure')}`);
  sel.setAttribute('aria-label', usable(cur) ? t('eng.label') : `${t('eng.label')}: ${t('eng.unavailable')}`);
  if (!sel._bound) {
    sel._bound = true;
    sel.addEventListener('change', () => {
      try { localStorage.setItem('llmBackend', sel.value); } catch (_) {}
      const nb = llm.backends[sel.value];
      toast(t('eng.switched', { name: sel.value === 'ollama' ? t('eng.ollama') : 'OpenAI' }) + (nb && !nb.encrypted ? ' — ' + t('eng.insecure') : ''));
      initEngine();
    });
  }
}

/* ---------------- navigation ---------------- */
function goto(step) {
  S.step = step; S.maxReached = Math.max(S.maxReached, step);
  if (step === 3) reloadQuestionnaire();   // 다른 언어로 받아 둔 문진이면 화면 언어로 다시 받는다
  window.scrollTo({ top: 0, behavior: 'smooth' });
  render();
}
function renderStepper() {
  const el = $('#stepper');
  el.innerHTML = STEPS().map((label, i) => {
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
  acClose();
  renderStepper();
  document.body.setAttribute('data-appstep', S.step);  // 모바일 전용 UI(스크리닝=2/문진=3) 스코프용
  const draw = [renderUpload, renderReview, renderScreening, renderQuestionnaire, renderResults][S.step];
  // 문진을 보던 중에 다시 그릴 때(언어 전환 등)는 보던 문항이 화면의 같은 자리에 남게 한다
  if (S.step === 3 && view().querySelector('.q-block[data-qid]')) keepPlace(draw); else { PLACE = null; draw(); }
}
// 화면 위쪽에 보이던 문항을 기억했다가, 다시 그린 뒤 그 문항이 같은 높이에 오도록 스크롤을 맞춘다.
// (문항 문구가 다른 언어로 바뀌면 길이가 달라져, 그대로 두면 보던 문항이 밀려난다)
let PLACE = null;   // 언어를 바꾸기 직전에 잡아 둔 자리 — 머리글 문구가 바뀌어 화면이 밀리기 전의 위치다
function markPlace() {
  const blocks = [...view().querySelectorAll('.q-block[data-qid]:not(.hidden)')];
  // 기준 문항: 머리글(80px) 아래에서 제목이 보이는 첫 문항. 긴 문항 하나가 화면을 다 덮고 있으면 그 문항.
  const cur = blocks.find(b => viewTop(b) >= 80 && viewTop(b) < window.innerHeight * .6) || blocks.find(b => b.getBoundingClientRect().bottom > 80);
  return cur ? { qid: cur.dataset.qid, top: viewTop(cur) } : null;
}
// 화면에서의 세로 위치. 다시 그린 직후에는 패널이 떠오르는 등장 애니메이션(translateY) 중이라
// getBoundingClientRect 가 아직 움직이는 위치를 준다 — 패널에 걸려 있는 이동분을 빼서 자리 잡을 위치를 구한다.
function viewTop(el) {
  const panel = el.closest('.panel');
  const moved = panel ? new DOMMatrixReadOnly(getComputedStyle(panel).transform).m42 : 0;
  return el.getBoundingClientRect().top - moved;
}
function keepPlace(draw) {
  const place = PLACE || markPlace(); PLACE = null;
  draw();
  const again = place && [...view().querySelectorAll('.q-block[data-qid]')].find(b => b.dataset.qid === place.qid);
  if (again) window.scrollBy({ top: viewTop(again) - place.top, behavior: 'instant' });
}

/* =========================================================================
   STEP 0 — 업로드
   ========================================================================= */
function renderUpload() {
  const hasKey = S.options && S.options.has_api_key;
  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('c.s0.eyebrow')}</div>
        <h1>${t('c.s0.h1')}</h1>
        <p>${t('c.s0.p')}</p>
      </div>
      <div class="dropzone" id="dz">
        <div class="icon">📄</div>
        <h3>${t('s0.dz_h')}</h3>
        <p>${t('s0.dz_p')} ${hasKey ? '' : t('s0.dz_nokey')}</p>
        <input type="file" id="file" accept="image/*" class="hidden" />
      </div>
      <img id="preview" class="preview-img hidden" alt="${t('s0.preview_alt')}" />
      <div id="uploadMsg"></div>
      <div class="upload-alt">
        <button class="btn secondary" id="btnDemo">${t('c.s0.btn_demo')}</button>
        <button class="btn secondary" id="btnManual">${t('s0.btn_manual')}</button>
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
  msg.innerHTML = `<div class="notice info" style="margin-top:16px"><span class="spinner" style="border-color:var(--brand-soft);border-top-color:var(--brand)"></span> ${t('s0.ocr_reading')}</div>`;
  try {
    const ocr = await API.ocr(f);
    S.ocr = normalizeOcr(ocr); resetSession();
    S.ocrConfirmed = false;
    REVIEW_TAB = flaggedCount() ? 'flag' : 'measured';
    goto(1);
  } catch (e) {
    // 잠시 뒤 다시 보낼 수 있는 오류(요청 과다)는 고른 파일을 그대로 다시 보내는 버튼을 준다
    msg.innerHTML = `<div class="notice warn" style="margin-top:16px"><div>⚠️ ${esc(I18N.errText(e) || e.message)}<br/>${t('s0.ocr_fail_hint')}
      ${e.code === 'rate_limited' ? `<div style="margin-top:8px"><button type="button" class="btn subtle sm" id="ocrRetry">${t('err.retry')}</button></div>` : ''}</div></div>`;
    const rb = $('#ocrRetry'); if (rb) rb.addEventListener('click', () => handleFile(f));
  }
}
async function loadDemo() {
  try { S.ocr = normalizeOcr(await API.demo()); resetSession(); REVIEW_TAB = 'measured'; toast(t('s0.demo_loaded')); goto(1); }
  catch (e) { toast(t('s0.demo_fail') + e.message); }
}
function startManual() {
  S.ocr = { test_type: 'MAST', patient: { name: '', age: null, gender: 'M', test_date: '' }, results: [] };
  // 새 행은 수치가 비어 있어 '수치 0 항목' 탭에 들어간다 — 그 탭을 열고 알러젠 이름 칸에서 바로 입력을 시작하게 한다
  resetSession(); addRow(); REVIEW_TAB = 'zero'; goto(1);
  const inp = view().querySelector('#tbody input[data-f="allergen_name"]'); if (inp) inp.focus({ preventScroll: true });
}
function normalizeOcr(ocr) {
  ocr.patient = ocr.patient || {};
  // 원래 필드를 모두 남긴다(...r). 예전에는 몇 개만 골라 담아서 value_text('<0.15')·size_text('4.5x3')·
  // 검증 표시(review_flags)가 화면을 거치며 사라졌고, 리포트·FHIR 에서 미만 표기와 팽진 크기가 빠졌다.
  ocr.results = (ocr.results || []).map((r, i) => ({
    ...r,
    review_flags: Array.isArray(r.review_flags) ? r.review_flags : [],
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
// 양성 수 — 빈 행(직접 입력으로 막 추가한 행)과 검사 대조(양성·음성 대조선)는 세지 않는다
function posCount() { return S.ocr.results.filter(r => !rowBlank(r) && !isControl(r) && isPositive(r, S.ocr.test_type)).length; }

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
  if (ps) ps.innerHTML = n ? `<div class="pos-summary">${t('c.s1.pos_summary', { n })}</div>` : '';
  const nx = $('#next'); if (nx) nx.disabled = !n || !reviewGateOpen();
  refreshRowNotes();
}

/* ---------------- OCR 확인 화면(필수) ----------------
   서버 검증(services/ocr_validation.py)이 의심스러운 행에 review_flags 를 단다.
   표시된 행을 하나씩 확인(또는 수정)하고, 원본과 대조했다는 확인란을 체크해야 다음으로 넘어간다.
   데모·직접 입력은 OCR 메타데이터가 없으므로 막지 않는다. */
function reviewInfo() { return (S.ocr && S.ocr.metadata && S.ocr.metadata.review) || null; }
function flagText(code) {
  const k = `flag.${code}`; const v = t(k);
  if (v !== k) return v;
  const rv = reviewInfo();
  return (rv && rv.flag_text_ko && rv.flag_text_ko[code]) || code;
}
function flaggedCount() { return (S.ocr.results || []).filter(r => (r.review_flags || []).length).length; }
function reviewGateOpen() { return !reviewInfo() || (flaggedCount() === 0 && !!S.ocrConfirmed); }
function refreshReviewGate() {
  const nx = $('#next'); if (nx) nx.disabled = !posCount() || !reviewGateOpen();
  const el = $('#rvRemain'); if (el) el.textContent = flaggedCount();
  const hint = $('#rvGateHint');
  if (hint) hint.textContent = reviewGateOpen() ? '' : (flaggedCount() ? t('rv.need_rows') : t('rv.need_check'));
}
function reviewBannerHtml() {
  const rv = reviewInfo(); if (!rv) return '';
  const n = flaggedCount();
  const docs = (rv.doc_flags || []).map(c => `<li>${esc(flagText(c))}${c === 'missing_rows' && (rv.missing_row_numbers || []).length ? ' — No. ' + esc(rv.missing_row_numbers.join(', ')) : ''}</li>`).join('');
  const model = (S.ocr.metadata || {}).model;
  return `<div class="notice ${n ? 'warn' : 'info'} rv-gate" style="margin-bottom:14px">
    <div><b>${t('rv.title')}</b> — ${t('rv.remaining', { n: `<span id="rvRemain">${n}</span>` })}</div>
    ${docs ? `<ul style="margin:6px 0 0 18px;font-size:13px">${docs}</ul>` : ''}
    <label style="display:flex;gap:8px;align-items:center;margin-top:10px;font-weight:700;cursor:pointer">
      <input type="checkbox" id="rvConfirm" ${S.ocrConfirmed ? 'checked' : ''} /> ${t('rv.confirm')}</label>
    ${model ? `<div class="hint" style="margin-top:6px">OCR: ${esc(model)}</div>` : ''}
  </div>`;
}
function flagCellHtml(r) {
  const fl = r.review_flags || [];
  if (!fl.length) return '';
  const why = fl.map(flagText).join(' · ') + (r.note ? ` (${r.note})` : '');
  return `<div class="rv-why" style="font-size:11.5px;color:var(--warn-ink, #b25e00);margin-top:3px">⚠️ ${esc(why)}</div>`;
}

function renderReview() {
  acClose(); loadAllergenRegistry();
  const tt = S.ocr.test_type;
  const isSPT = tt === 'SPT';
  const valueLabel = isSPT ? t('s1.col_value_spt') : t('s1.col_value_ige');
  const allRows = S.ocr.results.map((r, i) => ({ r, i }));
  const { measured: measuredN, zero: zeroN } = reviewCounts(S.ocr.results);
  const flagN = allRows.filter(({ r }) => (r.review_flags || []).length).length;
  if (REVIEW_TAB === 'flag' && !flagN) REVIEW_TAB = 'measured';
  const shown = allRows.filter(({ r }) => REVIEW_TAB === 'flag' ? (r.review_flags || []).length
    : REVIEW_TAB === 'zero' ? rowIsZero(r) : !rowIsZero(r));
  const rows = shown.map(({ r, i }) => {
    const on = isPositive(r, tt);
    // SPT 는 Class 개념이 없으므로 비활성화(—). MAST/UniCAP 만 Class 편집.
    const classCell = isSPT
      ? `<td style="width:66px;text-align:center;color:var(--text-3)">—</td>`
      : `<td style="width:66px"><input data-f="class_value" value="${r.class_value ?? ''}" placeholder="0-6" title="${t('s1.class_title')}" /></td>`;
    const flagged = (r.review_flags || []).length > 0;
    return `<tr data-i="${i}" class="${flagged ? 'row-flag' : ''}">
      <td style="color:var(--text-3);width:34px">${i + 1}</td>
      <td><input data-f="allergen_name" value="${esc(r.allergen_name)}" placeholder="${t('s1.ph_allergen')}" aria-label="${t('s1.th_allergen')}" ${AC_ATTRS} />${flagCellHtml(r)}${rowNotesHtml(r)}</td>
      <td><input data-f="korean_name" value="${esc(r.korean_name)}" placeholder="${t('s1.ph_korean')}" aria-label="${t('s1.th_korean')}" ${AC_ATTRS} /></td>
      <td class="num" style="width:96px"><input data-f="value" type="number" step="0.01" value="${r.value ?? ''}" /></td>
      <td style="width:78px"><input data-f="unit" value="${esc(r.unit || '')}" /></td>
      ${classCell}
      <td style="width:78px"><button class="pos-toggle ${on ? 'on' : 'off'}" data-toggle="${i}">${on ? t('s1.pos') : t('s1.neg')}</button></td>
      <td style="width:40px;white-space:nowrap">${flagged ? `<button class="btn subtle sm" data-ok="${i}" title="${t('rv.ok_title')}">${t('rv.ok')}</button>` : ''}<button class="btn danger-ghost" data-del="${i}" title="${t('s1.del')}">🗑️</button></td>
    </tr>`;
  }).join('');
  const emptyMsg = REVIEW_TAB === 'zero'
    ? t('s1.empty_zero')
    : t('s1.empty_measured');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('c.s1.eyebrow')}</div>
        <h1>${t('c.s1.h1')}</h1>
        <p>${t('c.s1.p')}</p>
      </div>

      ${renderExtractedMeta(S.ocr.patient)}

      <div class="field-inline" style="margin-bottom:14px">
        <label style="font-weight:700;font-size:13.5px">${t('s1.test_type')}</label>
        <select class="input" id="testType" style="width:auto">
          ${['SPT', 'MAST', 'UniCAP'].map(ty => `<option value="${ty}" ${tt === ty ? 'selected' : ''}>${ty}</option>`).join('')}
        </select>
        <span class="hint">${isSPT ? t('s1.hint_spt') : t('s1.hint_ige')}</span>
      </div>

      ${reviewBannerHtml()}

      <div class="rv-tabs" role="tablist">
        ${flagN ? `<button class="rv-tab ${REVIEW_TAB === 'flag' ? 'active' : ''}" data-rvtab="flag" style="border-color:#e8a33d">⚠️ ${t('rv.tab')} <span class="rv-count">${flagN}</span></button>` : ''}
        <button class="rv-tab ${REVIEW_TAB === 'measured' ? 'active' : ''}" data-rvtab="measured">${t('s1.tab_measured')} <span class="rv-count">${measuredN}</span></button>
        <button class="rv-tab ${REVIEW_TAB === 'zero' ? 'active' : ''}" data-rvtab="zero">${t('s1.tab_zero')} <span class="rv-count">${zeroN}</span></button>
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
      <p class="hint ac-hint">${t('s1.ac_hint')}</p>

      <div id="posSummary">${posCount() ? `<div class="pos-summary">${t('c.s1.pos_summary', { n: posCount() })}</div>` : ''}</div>

      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <span class="hint" id="rvGateHint" style="margin-right:8px"></span>
        <button class="btn primary" id="next" ${posCount() && reviewGateOpen() ? '' : 'disabled'}>${t('c.s1.btn_next')}</button>
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
    let v = e.target.value;
    if (f === 'value') v = v === '' ? null : parseFloat(v);
    // 사용자가 값을 고치면 인쇄 원문 표기는 더 이상 이 값을 설명하지 못한다 — 서버가 옛 표기('<0.15',
    // '4.5x3')로 되돌리지 않도록 지운다. 값·Class 를 고친 행은 검토한 것으로 보고 표시를 내린다.
    if (['value', 'class_value'].includes(f)) {
      const row = S.ocr.results[i];
      if (f === 'value') { row.value_text = null; row.value_raw = null; if (S.ocr.test_type === 'SPT') { row.size_text = null; row.mean_mm = v; } }
      if ((row.review_flags || []).length) { row.review_flags = []; row.reviewed = true; tr.classList.remove('row-flag'); refreshReviewGate && refreshReviewGate(); }
    }
    if (f === 'class_value') v = v === '' ? null : v;
    S.ocr.results[i][f] = v;
    if (AC_FIELDS.includes(f)) {
      if (f === 'allergen_name') { S.ocr.results[i].category = null; UNKNOWN_NAMES.delete(S.ocr.results[i]); }   // 이름을 직접 고치면 이전 분류·'목록에 없는 이름' 안내는 더 이상 맞지 않을 수 있다
      acUpdate(e.target);
      refreshReviewSummary();   // 빈 행에 이름이 들어가면 양성 수에 잡힌다
    }
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
  $('#tbody').addEventListener('keydown', acKeydown);
  $('#tbody').addEventListener('blur', e => {
    // 제안 목록은 mousedown 을 막아 두었으므로(acListEl) 목록을 누를 때는 blur 가 오지 않는다 — 여기 오면 정말 칸을 떠난 것이다.
    if (e.target === AC.input) acClose();
    if (e.target.dataset.f === 'allergen_name' && e.target.value.trim()) {
      const tr = e.target.closest('tr');
      lookupAllergenName(S.ocr.results[+tr.dataset.i], e.target.value.trim());   // 한글명 채우기·목록에 없는 이름 안내 → web/shared.js
    }
  }, true);
  $('#tbody').addEventListener('click', e => {
    const del = e.target.closest('[data-del]'); const tog = e.target.closest('[data-toggle]');
    const ok = e.target.closest('[data-ok]');
    if (ok) { const r = S.ocr.results[+ok.dataset.ok]; r.review_flags = []; r.reviewed = true; renderReview(); return; }
    if (del) { S.ocr.results.splice(+del.dataset.del, 1); renderReview(); }
    if (tog) { const i = +tog.dataset.toggle; const on = isPositive(S.ocr.results[i], S.ocr.test_type);
      S.ocr.results[i].interpretation = on ? 'Negative' : 'Positive'; renderReview(); }
  });
  // 새 항목은 수치 0(미측정)로 추가되므로 '수치 0 항목' 탭으로 전환해 보여준다.
  $('#btnAdd').addEventListener('click', () => { REVIEW_TAB = 'zero'; addRow(); renderReview(); setTimeout(() => { const inp = view().querySelector('tbody tr:last-child input'); inp && inp.focus(); }, 0); });
  view().querySelectorAll('[data-rvtab]').forEach(b => b.addEventListener('click', () => {
    REVIEW_TAB = b.dataset.rvtab; renderReview();
  }));
  const rvc = $('#rvConfirm');
  if (rvc) rvc.addEventListener('change', () => { S.ocrConfirmed = rvc.checked; refreshReviewGate(); });
  refreshReviewGate();
  $('#back').addEventListener('click', () => goto(0));
  $('#next').addEventListener('click', () => { S.ocr.results = S.ocr.results.filter(r => !rowBlank(r)); goto(2); });
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
  if (/(venom|\bbee\b|honey ?bee|bumble ?bee|wasp|hornet|yellow ?jacket|vespula|polistes|apis mellifera|fire ant|벌독|벌 독|꿀벌|말벌|땅벌|쌍살벌|불개미)/.test(s)) return 'venom';   // 서버 data/category_rules.json 의 벌독 규칙과 같은 낱말
  if (/(cockroach|바퀴|roach)/.test(s)) return 'insect';
  if (/(shrimp|crab|lobster|prawn|새우|게|랍스터|가재|대하|꽃게)/.test(s)) return 'shellfish';
  if (/(egg|milk|peanut|wheat|soy|nut|fish|계란|우유|땅콩|밀|콩|견과|생선|food|음식|과일|fruit)/.test(s)) return 'food';
  return 'other';
}
const SCREEN_CAT_LABEL = (c) => t(`scat.${c}`);
const SCREEN_HINTS = (c) => { const k = `shint.${c}`; const v = t(k); return v === k ? '' : v; };
function screeningContext() {
  const tt = S.ocr.test_type;
  const pos = (S.ocr.results || []).filter(r => isPositive(r, tt) && !isControl(r));
  const cats = {};
  pos.forEach(r => { const c = guessCategory(r.allergen_name, r.korean_name); (cats[c] = cats[c] || []).push(r.korean_name || r.allergen_name); });
  return { pos, cats };
}

// 거주 지역 — 꽃가루 시기가 지역마다 달라서 받는다(퀘스트 UI 와 같은 /api/pollen/* 사용).
// 예전 클래식 화면에는 이 입력이 없어서 지역별 계절성이 리포트에 들어갈 수 없었다.
function regionOptions(sc) {
  const country = (S.pollenRegions || []).find(c => c.code === sc.residence_country);
  if (!country) return `<option value="">${t('s2.region_none')}</option>`;
  if (country.single_region) {
    return `<option value="">${esc(country.regions[0] ? placeLabel('region', country.regions[0].code, country.regions[0].label_ko) : '')}</option>`;
  }
  return `<option value="">${t('s2.region_none')}</option>` + country.regions.map(r => {
    const states = (r.states || []).length ? ` (${r.states.join(', ')})` : '';
    return `<option value="${esc(r.code)}" ${sc.residence_region === r.code ? 'selected' : ''}>${esc(placeLabel('region', r.code, r.label_ko))}${esc(states)}</option>`;
  }).join('');
}
// 서버는 국가·권역 이름을 한국어(label_ko)로만 준다 — 코드로 사전에서 찾고, 사전에 없는 코드만 서버 이름을 쓴다.
function placeLabel(kind, code, fallback) { const k = `${kind}.${code}`; const v = t(k); return v === k ? (fallback || code || '') : v; }
async function loadPollenRegions() {
  try {
    const r = await fetch('/api/pollen/regions');
    S.pollenRegions = (await r.json()).countries || [];
  } catch (_) { S.pollenRegions = []; }
}
function bindResidence(sc) {
  const resC = $('#resCountry'), resR = $('#resRegion'), resZip = $('#resZip');
  if (resC) resC.addEventListener('change', () => {
    sc.residence_country = resC.value || null;
    sc.residence_region = null; sc.residence_postal_code = null; sc.residence_lat = sc.residence_lon = null;
    resR.innerHTML = regionOptions(sc);
    const zf = $('#zipField'); if (zf) zf.classList.toggle('hidden', sc.residence_country !== 'US');
  });
  if (resR) resR.addEventListener('change', () => { sc.residence_region = resR.value || null; });
  if (resZip) resZip.addEventListener('change', async () => {
    const code = (resZip.value || '').trim();
    sc.residence_postal_code = code || null;
    const msg = $('#zipMsg');
    if (!code) { sc.residence_lat = sc.residence_lon = null; msg.textContent = t('s2.zip_hint'); return; }
    try {
      const r = await fetch('/api/pollen/zip?country=US&postal_code=' + encodeURIComponent(code));
      const d = await r.json();
      if (!d.ok) { msg.textContent = t('s2.zip_bad'); return; }
      sc.residence_region = d.state; sc.residence_lat = d.lat; sc.residence_lon = d.lon;
      msg.textContent = t('s2.zip_ok', { region: placeLabel('region', d.region_code, d.region_label_ko || d.state) });
      if (resR) resR.innerHTML = regionOptions(sc);
    } catch (_) { msg.textContent = t('s2.zip_bad'); }
  });
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
      <div style="font-weight:800;font-size:14px;margin-bottom:6px">${t('c.s2.banner_title', { n: ctx.pos.length })}</div>
      <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
        ${catKeys.map(c => `<span class="chip-opt sel" style="cursor:default">${SCREEN_CAT_LABEL(c)} ${ctx.cats[c].length}</span>`).join('')}
      </div>
      <div style="font-size:12.5px;color:var(--text-2)">${t('s2.banner_note')}</div>
      ${catKeys.map(c => SCREEN_HINTS(c) ? `<div class="q-help" style="margin-top:8px">${SCREEN_HINTS(c)}</div>` : '').join('')}
    </div>` : '';

  const chipList = (items, selected, key) => `<div class="chips" data-chipgroup="${key}">` +
    items.map(it => `<button type="button" class="chip-opt ${selected.includes(it.code) ? 'sel' : ''}" data-code="${it.code}">${esc(it.label)}</button>`).join('') + `</div>`;

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('c.s2.eyebrow')}</div>
        <h1>${t('c.s2.h1')}</h1>
        <p>${t('c.s2.p')}</p>
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

      <div class="card soft" style="margin-bottom:20px">
        <div class="field" style="margin-bottom:8px">
          <label>${t('s2.residence')} <span class="hint">${t('s2.residence_hint')}</span></label>
        </div>
        <div class="grid-3">
          <div class="field"><label>${t('s2.country')}</label>
            <select class="input" id="resCountry">
              <option value="">${t('s2.region_none')}</option>
              ${(S.pollenRegions || []).map(c => `<option value="${esc(c.code)}" ${sc.residence_country === c.code ? 'selected' : ''}>${esc(placeLabel('country', c.code, c.label_ko))}</option>`).join('')}
            </select></div>
          <div class="field"><label>${t('s2.region')}</label>
            <select class="input" id="resRegion">${regionOptions(sc)}</select></div>
          <div class="field ${sc.residence_country === 'US' ? '' : 'hidden'}" id="zipField">
            <label>${t('s2.zip')}</label>
            <input class="input" id="resZip" placeholder="${t('s2.zip_ph')}" value="${esc(sc.residence_postal_code || '')}" />
            <div class="hint" id="zipMsg">${t('s2.zip_hint')}</div>
          </div>
        </div>
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
            `<button type="button" class="chip-opt ${(sc.pets||[]).includes(code) ? 'sel' : ''}" data-code="${code}">${label}</button>`).join('')}
        </div>
        <input class="input ${(sc.pets||[]).includes('other') ? '' : 'hidden'}" id="petsOther" style="margin-top:8px" placeholder="${t('s2.pets_other_ph')}" value="${esc(sc.pets_other||'')}" />
      </div>

      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">${t('c.s2.btn_next')}</button>
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

  bindResidence(sc);
  // 입력 즉시 상태에 반영 — 이 화면에서 언어를 바꿔 다시 그려도 적어 둔 값이 남는다
  $('#pName').addEventListener('input', e => { p.name = e.target.value; });
  $('#pAge').addEventListener('input', e => { p.age = e.target.value ? parseInt(e.target.value) : null; });
  $('#pGender').addEventListener('change', e => { p.gender = e.target.value; });
  $('#pDate').addEventListener('change', e => { p.test_date = e.target.value || null; });
  const petsOther = $('#petsOther'); if (petsOther) petsOther.addEventListener('input', () => { sc.pets_other = petsOther.value; });
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
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s2.preparing')}`;
  try {
    const lang = I18N.getLang();
    const res = await API.post('/api/questionnaire', { ocr: S.ocr, screening: sc, lang });
    S.questionnaire = res.questionnaire; S.assessments = res.assessments;
    S.qLang = lang; S.qState = I18N.questionnaireState(lang, res);   // 문항이 실제로 번역됐는지 — 번역 안내문을 고른다
    S.answers = Object.assign({}, res.questionnaire.answer_prefill || {});
    goto(3);
  } catch (e) { toast(I18N.errText(e) || t('s2.q_fail') + e.message); btn.disabled = false; btn.textContent = t('c.s2.btn_next'); }
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
  const qById = {}; q.sections.forEach(sec => sec.questions.forEach(qq => { qById[qq.id] = qq; }));
  const applyTags = (arr) => (arr && arr.length)
    ? `<div class="q-applies">${arr.filter(k => idx[k]).map(k => `<span class="tag">${CAT_EMOJI[refineCategory(idx[k].category, idx[k].name, idx[k].korean_name)] || '•'} ${esc(idx[k].korean_name || idx[k].name)}</span>`).join('')}</div>` : '';

  const questionHtml = (qq) => {
    const val = S.answers[qq.id];
    let control;
    if (qq.type === 'multi') {
      control = `<div class="chips" data-multi="${qq.id}">` +
        qq.options.map(o => `<button type="button" aria-pressed="${(val || []).includes(o.value)}" class="chip-opt ${(val || []).includes(o.value) ? 'sel' : ''}" data-v="${esc(o.value)}">${esc(o.label)}</button>`).join('') + `</div>`;
    } else {
      const rowClass = qq.options.length <= 3 && qq.options.every(o => o.label.length <= 12) ? 'choice-row' : 'choice-list';
      control = `<div class="${rowClass}" role="radiogroup" data-single="${qq.id}">` +
        qq.options.map(o => `<button type="button" role="radio" aria-checked="${val === o.value}" class="choice ${val === o.value ? 'sel' : ''}" data-v="${esc(o.value)}">
          <span class="radio"></span><span class="body"><span class="t">${esc(o.label)}</span>${o.hint ? `<span class="h">${esc(o.hint)}</span>` : ''}</span></button>`).join('') + `</div>`;
    }
    // reveal 조건: reveal_if({question, equals|any|includes_any}) 또는 reveal_if_any([...])
    const cond = qq.reveal_if_any ? { any_of: qq.reveal_if_any } : (qq.reveal_if || null);
    let revealAttr = '', hiddenCls = '';
    if (cond) {
      revealAttr = ` data-reveal='${esc(JSON.stringify(cond))}'`;
      if (!condMet(cond)) hiddenCls = ' hidden';
    }
    return `<div class="q-block${hiddenCls}" data-qid="${esc(qq.id)}"${revealAttr}>
      <div class="q-title">${fmtRich(qq.title)}</div>
      ${qq.help ? `<div class="q-help">${fmtRich(qq.help)}</div>` : ''}
      ${applyTags(qq.applies_to)}
      ${control}</div>`;
  };
  const applyReveals = () => view().querySelectorAll('[data-reveal]').forEach(b => {
    let c; try { c = JSON.parse(b.dataset.reveal); } catch (_) { return; }
    b.classList.toggle('hidden', !condMet(c));
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

  const sections = q.sections.map(sec => `
    <div class="q-section" data-sec="${esc(sec.id || '')}">
      <div class="q-shead"><h2>${esc(sec.title)}</h2></div>
      ${sec.subtitle ? `<div class="q-sub">${esc(sec.subtitle)}</div>` : ''}
      ${sec.questions.map(questionHtml).join('')}
    </div>`).join('');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">${t('c.s3.eyebrow')}</div>
        <h1>${t('c.s3.h1')}</h1>
        <p>${t('c.s3.p')}</p>
      </div>
      ${partialNotice('q')}
      ${sections}
      <div class="actions">
        <button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">${t('c.s3.btn_next')}</button>
      </div>
    </div>`;

  view().querySelectorAll('[data-single]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.choice'); if (!b) return;
    S.answers[g.dataset.single] = b.dataset.v;
    g.querySelectorAll('.choice').forEach(c => { c.classList.toggle('sel', c === b); c.setAttribute('aria-checked', c === b); });
    applyReveals(); refreshFoodGeneralOptions();
  }));
  view().querySelectorAll('[data-multi]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.chip-opt'); if (!b) return;
    const id = g.dataset.multi, v = b.dataset.v;
    const arr = toggleMulti((qById[id] || {}).options, S.answers[id], v);   // 단독 선택지('해당 없음')는 다른 선택을 지운다 → web/shared.js
    S.answers[id] = arr;
    g.querySelectorAll('.chip-opt').forEach(c => { const on = arr.includes(c.dataset.v); c.classList.toggle('sel', on); c.setAttribute('aria-pressed', on); });
    applyReveals(); refreshFoodGeneralOptions();
  }));
  refreshFoodGeneralOptions();
  $('#back').addEventListener('click', () => goto(2));
  $('#next').addEventListener('click', submitClassify);
}
async function submitClassify() {
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('c.s3.analyzing')}`;
  try {
    const lang = I18N.getLang();
    const out = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers, ui: UI_NAME, lang, session_id: S.sessionId || undefined });
    out.lang = out.lang || lang; normalizeResult(out);
    S.classify = out; S.outputs = { [out.lang]: out };
    S.sessionId = out.session_id || S.sessionId;   // 같은 결과지의 재판정은 같은 세션에 덮어쓴다
    syncResultLang();   // 판정을 기다리는 사이 언어를 바꿨다면 그 언어의 저장분을 이어서 받는다
    goto(4);
  } catch (e) { toast(I18N.errText(e) || t('s3.classify_fail') + e.message); btn.disabled = false; btn.textContent = t('c.s3.btn_next'); }
}

/* =========================================================================
   STEP 4 — 결과
   ========================================================================= */
let RESULT_TAB = 'allergens';
function renderResults() {
  const c = S.classify; const rs = resultSummary(c); const cnt = rs.counts; const p = S.ocr.patient;   // 검사 대조는 개수에서 뺀다
  view().innerHTML = `
    <div class="result-hero">
      <h1>${t('c.s4.h1', { name: esc(p.name || t('c.patient')) })}</h1>
      <p>${t('c.s4.p', { date: esc(p.test_date || '-'), n: rs.total })}</p>
      <div class="stat-row">
        <div class="stat"><div class="n">${cnt.clinically_relevant}</div><div class="l">${t('c.s4.stat_rel')}</div></div>
        <div class="stat"><div class="n">${cnt.sensitized_only}</div><div class="l">${t('c.s4.stat_sens')}</div></div>
        <div class="stat"><div class="n">${cnt.indeterminate}</div><div class="l">${t('c.s4.stat_indet')}</div></div>
      </div>
    </div>

    <div id="langSync" role="status" aria-live="polite">${langSyncHtml()}</div>

    <div class="result-tabs">
      <button data-tab="allergens" class="${RESULT_TAB === 'allergens' ? 'active' : ''}">${t('c.s4.tab_allergens')}</button>
      <button data-tab="report" class="${RESULT_TAB === 'report' ? 'active' : ''}">${t('s4.tab_report')}</button>
      <button data-tab="cardnews" class="${RESULT_TAB === 'cardnews' ? 'active' : ''}">${t('s4.tab_cardnews')}</button>
      <button data-tab="chat" class="${RESULT_TAB === 'chat' ? 'active' : ''}">${t('s4.tab_chat')}</button>
      <button data-tab="fhir" class="${RESULT_TAB === 'fhir' ? 'active' : ''}">${t('s4.tab_fhir')}</button>
    </div>
    <div id="tabBody"></div>
    <div id="mailBox"></div>

    <div class="actions">
      <button class="btn secondary" id="back">${t('s4.back_edit')}</button>
      <span class="spacer"></span>
      <button class="btn secondary" id="restart">${t('c.s4.restart')}</button>
    </div>`;

  view().querySelectorAll('.result-tabs button').forEach(b => b.addEventListener('click', () => { RESULT_TAB = b.dataset.tab; renderResults(); }));
  $('#back').addEventListener('click', () => goto(3));
  bindLangRetry();
  $('#restart').addEventListener('click', () => { S.ocr = null; S.screening = null; S.questionnaire = null; S.answers = {}; S.classify = null; S.maxReached = 0; resetSession();
    RESULT_TAB = 'allergens'; S.chat = { messages: [], suggestions: null, busy: false, hasKey: null }; goto(0); });
  renderResultTab();
  renderMailPanel();
}

const ORDER = { clinically_relevant: 0, indeterminate: 1, sensitized_only: 2, not_assessed: 3 };
const relRank = (a) => a.category === 'control' ? 9 : (ORDER[a.relevance] ?? 3);   // 검사 대조는 판정 대상이 아니라 맨 뒤
function renderResultTab() {
  const body = $('#tabBody'); const c = S.classify;
  if (RESULT_TAB === 'allergens') {
    const sorted = [...c.assessments].sort((a, b) => relRank(a) - relRank(b));
    body.innerHTML = sorted.map(cardForAllergen).join('') || `<p class="q-help">${t('c.s4.empty')}</p>`;
    body.querySelectorAll('.ac-head').forEach(h => h.addEventListener('click', () => h.closest('.allergen-card').classList.toggle('open')));
  } else if (RESULT_TAB === 'report') {
    const doc = c.report_document_html || `<div class="report-render">${c.report_html}</div>`;
    // 리포트는 스크립트가 필요 없다 — 스크립트를 막고(allow-scripts 없음), 부모가 인쇄만 부를 수 있게 둔다
    body.innerHTML = `<iframe class="report-frame" id="rpt" sandbox="allow-same-origin allow-modals" title="${esc(t('s4.tab_report'))}"></iframe>
      <div class="download-row">
        ${pdfButtonsHtml()}
        <button class="btn subtle sm" id="dlHtml">${t('s4.dl_html')}</button>
        <button class="btn subtle sm" id="dlMd">${t('s4.dl_md')}</button>
      </div>
      <div id="pdfMsg" role="status" aria-live="polite"></div>`;
    const nm = (S.ocr.patient.name || 'patient');
    // 'PDF 받기'(#dlPdfFile)는 서버가 만든 PDF 를 받는다(web/shared.js). 인쇄(#dlPdf)는 리포트 iframe 자체를 인쇄한다 → HTML 과 100% 동일 레이아웃
    const printReport = () => { const f = $('#rpt'); if (f && f.contentWindow) { f.contentWindow.focus(); f.contentWindow.print(); } };
    // 문서 안의 인쇄 버튼(onclick="window.print()")은 sandbox 가 막는다 — 같은 동작을 부모 쪽에서 붙여 준다
    $('#rpt').addEventListener('load', e => {
      const d = e.target.contentDocument;
      if (d) d.querySelectorAll('[onclick*="print"]').forEach(b => b.addEventListener('click', printReport));
    });
    $('#rpt').srcdoc = doc;
    $('#dlPdf').addEventListener('click', printReport);
    bindReportPdf();
    $('#dlHtml').addEventListener('click', () => download(`${nm}_allergy_report.html`, doc, 'text/html'));
    $('#dlMd').addEventListener('click', () => download(`${nm}_allergy_report.md`, c.report_markdown, 'text/markdown'));
  } else if (RESULT_TAB === 'cardnews') {
    // 카드뉴스는 넘기기용 인라인 스크립트가 필요하다 — 실행은 허용하되 앱과 다른 출처(불투명 출처)에 가둔다.
    // 그 안에서는 localStorage 가 막히므로 '내 실천 체크'는 앱이 대신 들고 있는다(mountDeckBridge — web/shared.js).
    body.innerHTML = `<iframe class="cardnews-frame" id="cn" sandbox="allow-scripts" title="${esc(t('s4.tab_cardnews'))}"></iframe>
      <div class="download-row"><button class="btn subtle sm" id="dlCn">${t('s4.dl_cardnews')}</button></div>`;
    mountDeckBridge($('#cn'));
    $('#cn').srcdoc = c.cardnews_html;
    $('#dlCn').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_cardnews.html`, c.cardnews_html, 'text/html'));
  } else if (RESULT_TAB === 'chat') {
    renderChat(body);
  } else if (RESULT_TAB === 'fhir') {
    body.innerHTML = `<div class="card soft">
      <p class="q-help" style="margin-bottom:12px">${t('s4.fhir_help')}</p>
      <div id="fhirSummary" style="margin-bottom:12px"></div>
      <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap">
        <button class="btn ${FHIR_VIEW==='allergy'?'primary':'secondary'} sm" id="vAllergy">AllergyIntolerance</button>
        <button class="btn ${FHIR_VIEW==='obs'?'primary':'secondary'} sm" id="vObs">Observation</button>
        <button class="btn ${FHIR_VIEW==='screening'?'primary':'secondary'} sm" id="vScreening">Condition · Questionnaire</button>
        <span class="spacer" style="flex:1"></span>
        <button class="btn subtle sm" id="dlObs">⬇️ Observation</button>
        <button class="btn subtle sm" id="dlAllergy">⬇️ AllergyIntolerance</button>
        <button class="btn subtle sm" id="dlScreening">⬇️ Condition · Questionnaire</button>
      </div>
      <pre id="fhirPreview" style="max-height:420px;overflow:auto;background:var(--bg-subtle);padding:14px;border-radius:12px;font-size:12px">${t('common.loading')}</pre></div>`;
    loadFhir();
  }
}
function cardForAllergen(a) {
  const hasOas = (a.oas_foods && a.oas_foods.length);
  const kb = [];
  if (a.season_label_ko) kb.push([t('dex.season'), esc(a.season_label_ko)]);
  if (hasOas) kb.push([t('dex.oas_foods'), esc(a.oas_foods.join(', ')) + t('dex.oas_note')]);
  if (a.crossreact_confirmed && a.crossreact_confirmed.length) kb.push([t('dex.crossreact_confirmed'), esc(a.crossreact_confirmed.join(', '))]);
  if (a.biology_ko) kb.push([t('dex.biology'), esc(a.biology_ko)]);
  if (a.exposure_environment_ko) kb.push([t('dex.exposure'), esc(a.exposure_environment_ko)]);
  if (a.cross_reactivity_ko) kb.push([t('dex.crossreact'), esc(a.cross_reactivity_ko)]);
  if (a.oral_allergy_syndrome_ko) kb.push([t('c.dex.oas_general'), esc(a.oral_allergy_syndrome_ko)]);
  const av = (a.avoidance_control_ko || []).slice(0, 5);
  const kbHtml = kb.map(([k, v]) => `<div class="kb-item"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('') +
    (av.length ? `<div class="kb-item"><div class="k">${t('dex.avoidance')}</div><ul>${av.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '');
  const srcTag = a.source && a.source !== 'knowledge_base' ? `<span class="src-tag">${t('dex.source')}${a.source === 'wikipedia' ? 'Wikipedia' : t('dex.source_default')}</span>` : '';
  const oasBadge = hasOas ? `<span class="rel-badge" style="background:var(--indet-soft);color:var(--indet);border:1px solid var(--indet-border)">🍎 OAS</span>` : '';
  const sev = a.severity && a.severity !== 'none' && a.severity !== 'mild' ? t('dex.severity', { s: ({ moderate: t('dex.sev_moderate'), severe: t('dex.sev_severe'), anaphylaxis: t('dex.sev_ana') })[a.severity] || esc(a.severity) }) : '';
  // 검사 대조(양성·음성 대조선)는 알러젠이 아니다 — 서버가 판정을 붙여 보내도 판정 대신 그 사실을 적는다
  const control = a.category === 'control';
  return `<div class="allergen-card${control ? ' is-control' : ''}" data-cat="${esc(a.category || 'other')}">
    <div class="ac-head">
      <span class="emoji">${CAT_EMOJI[a.category] || '•'}</span>
      <div class="ac-title">
        <div class="nm">${esc(a.korean_name || a.allergen_name)}</div>
        <div class="meta">${catLabel(a.category)} · ${a.test_value ?? '-'}${a.test_unit ? ' ' + esc(a.test_unit) : ''}${a.class_value != null ? ` · class ${esc(a.class_value)}` : ''}${a.strength && !control ? ` · ${t('dex.strength', { s: t(`strength.${a.strength}`) })}` : ''}${control ? '' : sev}${srcTag}</div>
      </div>
      ${control ? '' : oasBadge}
      ${control ? `<span class="rel-badge not_assessed">${t('verdict.control.stamp')}</span>` : `<span class="rel-badge ${esc(a.relevance)}">${t(`c.rel.${a.verdict === 'clinician_review' ? a.verdict : a.relevance}`)}</span>`}
      <span class="chevron">▾</span>
    </div>
    <div class="ac-body">
      ${control ? `<div class="rationale">${t('dex.control_back')}</div>` : `<div class="rationale">${esc(a.rationale_ko || '')}</div>
      <div class="kb-grid">${kbHtml}</div>`}
    </div>
  </div>`;
}
let FHIR_VIEW = 'allergy';
function fhirRenderPreview() {
  const f = S.fhir; if (!f) return;
  const bundle = FHIR_VIEW === 'obs' ? f.observation_bundle
    : FHIR_VIEW === 'screening' ? (f.screening_bundle || { resourceType: 'Bundle', type: 'collection', entry: [] })
    : f.allergy_intolerance_bundle;
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
    <span class="chip-opt sel" style="cursor:default">confirmed ${conf}</span>
    <span class="chip-opt sel" style="cursor:default">Condition ${((f.screening_bundle || {}).entry || []).filter(e => e.resource.resourceType === 'Condition').length}</span></div>`;
}
async function loadFhir() {
  try {
    const f = await API.post('/api/fhir', { ocr: S.ocr, screening: S.screening, answers: S.answers });
    S.fhir = f;
    fhirRenderPreview();
    $('#vAllergy').addEventListener('click', () => { FHIR_VIEW = 'allergy'; renderResultTab(); });
    $('#vObs').addEventListener('click', () => { FHIR_VIEW = 'obs'; renderResultTab(); });
    $('#vScreening')?.addEventListener('click', () => { FHIR_VIEW = 'screening'; renderResultTab(); });
    $('#dlObs').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_observation.json`, JSON.stringify(f.observation_bundle, null, 2)));
    $('#dlAllergy').addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_allergyintolerance.json`, JSON.stringify(f.allergy_intolerance_bundle, null, 2)));
    // 문진(기저 질환·증상) — 문진을 건너뛰면 서버가 번들을 만들지 않는다
    $('#dlScreening')?.addEventListener('click', () => download(`${(S.ocr.patient.name || 'patient')}_screening.json`, JSON.stringify(f.screening_bundle || { resourceType: 'Bundle', type: 'collection', entry: [] }, null, 2)));
  } catch (e) { const el = $('#fhirPreview'); if (el) el.textContent = I18N.errText(e) || t('s4.fhir_fail') + e.message; }
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
    // 사전 문구는 바로 바뀌고, 서버가 만든 내용(선택지·문진 문항·추천 질문·결과 저장분)은 새 언어로 다시 받는다 → web/shared.js
    sel.addEventListener('change', () => { PLACE = S.step === 3 ? markPlace() : null; changeLang(sel.value); });
  }
}

(async function () {
  initTheme(); initLang();
  await Promise.all([loadOptions(), loadPollenRegions()]);
  initEngine();
  render();
})();
