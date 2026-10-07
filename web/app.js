/* =========================================================================
   알레르기 리포트 플랫폼 — 프론트엔드 (vanilla JS SPA)
   퀘스트(단계): 0 흔적 수집 → 1 증거 확인 → 2 탐험가 프로필 → 3 진범 감별 → 4 도감 완성
   각 퀘스트는 한 번에 한 가지만 묻는 작은 스테이지로 나뉜다(S.sub). 스테이지 그림은 web/art.js,
   XP·도감 카드 로직은 web/game.js. 게임 연출은 진행 행동에만 붙고 임상 내용은 그대로 보여준다.
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

const UI_NAME = 'quest';   // 서버에 보내는 ui 값 — 함께 쓰는 코드(web/shared.js)가 읽는다
// 알러젠 자동완성·결과 언어 맞추기·이메일 받기·결과 상담·언어 전환은 클래식 UI 와 함께 쓴다 → web/shared.js
const onAllergenPicked = () => Game.noteEdit(S.game);   // '꼼꼼한 검토자' 배지 — 자동완성으로 고친 것도 직접 수정이다

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
  chat: { messages: [], suggestions: null, busy: false, hasKey: null },  // 결과 상담
  sessionId: null,            // 서버 저장 세션 — 판정(classify)이 발급, 이후 판정·상담·메일이 같은 id 를 쓴다
  mail: newMail(),            // 결과 이메일 받기 패널 상태
  outputs: {},                // 이번 판정의 언어별 산출물(lang → classify 모양). S.classify 는 지금 화면에 보이는 언어의 것
  langSync: null,             // 화면 언어의 결과를 아직 못 받았을 때: { lang, state: 'preparing' | 'failed' }
  qLang: null,                // S.questionnaire 를 받아 온 언어
  qState: null,               // 그 문항이 실제로 번역됐는지: 'ok' | 'partial' | 'none' (I18N.questionnaireState)
  // 퀘스트 안의 현재 스테이지. upload: 'brief'|'upload' / profile: PROFILE_STAGES 인덱스 / q: 문항 id | '__summary'
  sub: { upload: 'brief', profile: 0, profileMax: 0, q: null, qSi: null },
  briefSeen: false,
};

let REVIEW_TAB = 'measured';  // OCR 검토 표 탭: 'measured'(수치>0) / 'zero'(수치 0·미측정)
let DEX_FILTER = 'all';       // 결과 도감 필터: all | clinically_relevant | indeterminate | sensitized_only
let DEX_CAT = 'all';          // 도감 종류(카테고리) 필터
let DEX_SORT = 'verdict';     // 도감 정렬: verdict | strength | name | category
let DEX_VIEW = 'cards';       // 도감 보기: cards(큰 카드) | binder(종류별 바인더)
let STAGE_DIR = 'none';       // 스테이지 전환 방향: fwd | back | none (다음 렌더 1회에만 적용)
const t = (k, v) => I18N.t(k, v);   // 화면 문구 번역 (web/i18n.js). 서버 생성 콘텐츠는 한국어.
const STEPS = () => [0, 1, 2, 3, 4].map(i => t(`step.${i}.name`));
const STEP_SUB = () => [0, 1, 2, 3, 4].map(i => t(`step.${i}.sub`));
const catLabel = (c) => { const k = `cat.${c}`; const v = t(k); return v === k ? (c || '') : v; };
// 서버 생성 콘텐츠(문진·KB·리포트)가 한국어임을 비한국어 UI 에서 안내
// 거주 지역 선택지. 국가를 고르면 그 나라의 지역 목록만 보여준다.
// 미국은 주(州)까지 받는데, 같은 수목 시즌이 남동부 1월·알래스카 4월로 석 달까지 차이 나기 때문이다.
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

// 비한국어 화면의 번역 안내. '기계 번역했다'는 문구는 실제로 번역됐을 때만 쓴다 — 문진(scope 'q')은 받아 온 문항의
// 번역 상태(S.qState)로, 아직 받은 내용이 없는 화면은 번역 엔진을 쓸 수 있는지(/api/health has_api_key)로 고른다.
function partialNotice(scope) {
  const lang = I18N.getLang();
  let state = null, shown = 'ko';
  if (scope === 'q' && S.questionnaire && S.qLang) {
    if (S.qLang === lang) state = S.qState || 'ok';
    else { state = 'none'; shown = S.qState === 'none' ? 'ko' : S.qLang; }   // 새 언어로 아직 못 받았다 — 이전 언어 그대로다
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
  // 앞으로 전진할 때 현재 단계 완료 XP(행동 기반, 1회)
  if (step > S.step) { const g = Game.completeStep(S.game, S.step); if (g) Game.ui.floatXp(null, g); }
  STAGE_DIR = step > S.step ? 'fwd' : (step < S.step ? 'back' : 'none');
  S.step = step; S.maxReached = Math.max(S.maxReached, step);
  if (step === 3) reloadQuestionnaire();   // 다른 언어로 받아 둔 문진이면 화면 언어로 다시 받는다
  window.scrollTo({ top: 0, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  render();
}
function renderStepper() {
  Game.ui.renderTrail($('#stepper'), STEPS(), STEP_SUB(), S.step, S.maxReached, goto);
}
function render() {
  acClose();
  renderStepper();
  Game.ui.renderHud($('#hud'), S.game);
  $('#questBar').classList.toggle('hidden', S.step !== 3);
  document.body.setAttribute('data-appstep', S.step);  // 모바일 전용 UI(스크리닝=2/문진=3) 스코프용
  [renderUpload, renderReview, renderScreening, renderQuestionnaire, renderResults][S.step]();
}

/* ---------------- 스테이지(퀘스트 안의 작은 단계) 공통 틀 ----------------
   장면(일러스트) + 제목 + 본문 + 하단 버튼. 움직이는 것은 .stage 뿐이고 .actions 는 그 밖에 둔다
   (모바일에서 하단 고정 버튼이 transform 의 영향을 받지 않도록). */
const sceneHtml = (id) => (typeof QuestArt !== 'undefined' ? QuestArt.scene(id) : '');
const artIcon = (id) => (typeof QuestArt !== 'undefined' ? QuestArt.icon(id) : '');
const fmtRich = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');   // 서버 문구의 **굵게** 만 허용
function dexChipHtml() {
  const n = Object.keys(S.game.discovered).length;
  if (!n) return '';
  return `<button type="button" class="dex-chip" id="dexChip" aria-label="${esc(t('dexchip.aria', { n }))}">${artIcon('card')}<span>${t('dexchip.label', { n })}</span></button>`;
}
function stagePanel(o) {
  return `<div class="panel stage-panel ${o.cls || ''}">
    <div class="stage-bar"><div class="stage-rail" id="stageRail"></div>${dexChipHtml()}</div>
    <div class="stage" data-dir="${o.dir || STAGE_DIR}">
      <div class="stage-top">
        <div class="scene-box" id="sceneBox">${o.sceneSvg || sceneHtml(o.scene)}</div>
        <div class="panel-head">
          <div class="eyebrow">${o.eyebrow}</div>
          <h1 id="stageTitle" tabindex="-1">${o.title}</h1>
          ${o.desc ? `<p>${o.desc}</p>` : ''}
        </div>
      </div>
      ${o.body || ''}
    </div>
    ${o.actions ? `<div class="actions">${o.actions}</div>` : ''}
  </div>`;
}
// 스테이지 레일을 그리고, 전환으로 들어온 경우 제목(또는 지정 요소)에 포커스를 옮긴다.
function mountStage(rail, focusSel) {
  Game.ui.renderStageRail($('#stageRail'), rail);
  const chip = $('#dexChip'); if (chip) chip.addEventListener('click', openDexOverlay);
  if (STAGE_DIR !== 'none') { const h = $(focusSel || '#stageTitle'); if (h && h.focus) h.focus({ preventScroll: true }); }
  STAGE_DIR = 'none';
}
// 같은 퀘스트 안에서 스테이지를 바꿀 때: 패널 머리가 화면 밖이면 그 위치로 올린다.
function stageScroll() {
  const el = $('.stage-panel'); if (!el || !el.getBoundingClientRect) return;
  const top = el.getBoundingClientRect().top;
  if (top < 0 || top > window.innerHeight * .5) window.scrollTo({ top: Math.max(0, window.scrollY + top - 84), behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
}
function stageReward(id, anchor) {
  const g = Game.completeStage(S.game, id);
  if (g) { Game.ui.floatXp(anchor || null, g); Game.ui.renderHud($('#hud'), S.game); }
}

/* =========================================================================
   STEP 0 — 업로드
   ========================================================================= */
function uploadRail(cur) {
  return {
    aria: t('stage.aria', { quest: t('step.0.name') }),
    stages: [
      { label: t('s0.st.brief'), state: cur === 0 ? 'cur' : 'done', reachable: true },
      { label: t('s0.st.upload'), state: cur === 1 ? 'cur' : 'todo', reachable: S.briefSeen },
    ],
    onGoto: (i) => { STAGE_DIR = i ? 'fwd' : 'back'; S.sub.upload = i ? 'upload' : 'brief'; render(); },
  };
}
// Q1-① 임무 브리핑 — 이 퀘스트에서 무엇을 하고, 왜 하고, 끝나면 무엇을 받는지
function renderBrief() {
  const beats = [['trace', 1], ['clue', 2], ['stamp', 3]].map(([ic, n], i) => `
    <li class="beat" style="--i:${i}"><span class="beat-ic">${artIcon(ic)}</span>
      <div><div class="beat-t"><span class="beat-n">${n}</span>${t(`s0.brief.b${n}.t`)}</div><p>${t(`s0.brief.b${n}.d`)}</p></div></li>`).join('');
  const loot = [['book', 's0.brief.get1'], ['report', 's0.brief.get2'], ['deck', 's0.brief.get3']]
    .map(([ic, k]) => `<li>${artIcon(ic)}<span>${t(k)}</span></li>`).join('');
  view().innerHTML = stagePanel({
    scene: 'brief', eyebrow: `${t('s0.eyebrow')} · 1/2`, title: t('s0.brief.h1'), desc: t('s0.brief.p'),
    body: `<ol class="beats">${beats}</ol>
      <div class="loot"><div class="loot-t">${t('s0.brief.get')}</div><ul class="loot-list">${loot}</ul>
        <p class="hint">${t('s0.brief.get_note')}</p></div>`,
    actions: `<span class="spacer"></span><button class="btn primary" id="btnStart">${t('s0.brief.start')}</button>`,
  });
  mountStage(uploadRail(0));
  $('#btnStart').addEventListener('click', () => {
    S.briefSeen = true; stageReward('upload.brief', $('#btnStart'));
    STAGE_DIR = 'fwd'; S.sub.upload = 'upload'; render(); stageScroll();
  });
}
function renderUpload() {
  if (S.sub.upload === 'brief') return renderBrief();
  const hasKey = S.options && S.options.has_api_key;
  view().innerHTML = stagePanel({
    scene: 'upload', eyebrow: `${t('s0.eyebrow')} · 2/2`, title: t('s0.h1'), desc: t('s0.p'),
    body: `<div class="dropzone" id="dz" role="button" tabindex="0" aria-label="${t('s0.dz_aria')}">
        <h2>${t('s0.dz_h')}</h2>
        <p>${t('s0.dz_p')} ${hasKey ? '' : t('s0.dz_nokey')}</p>
        <input type="file" id="file" accept="image/*" class="hidden" />
      </div>
      <img id="preview" class="preview-img hidden" alt="${t('s0.preview_alt')}" />
      <div id="uploadMsg" aria-live="polite"></div>
      <div class="upload-alt">
        <button class="btn secondary" id="btnDemo">${t('s0.btn_demo')}</button>
        <button class="btn secondary" id="btnManual">${t('s0.btn_manual')}</button>
      </div>`,
    actions: `<button class="btn secondary" id="back">${t('common.back')}</button><span class="spacer"></span>`,
  });
  mountStage(uploadRail(1));

  const dz = $('#dz'), file = $('#file');
  dz.addEventListener('click', () => file.click());
  dz.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); file.click(); } });
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
  dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('drag'); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); });
  file.addEventListener('change', () => { if (file.files[0]) handleFile(file.files[0]); });
  $('#btnDemo').addEventListener('click', loadDemo);
  $('#btnManual').addEventListener('click', startManual);
  $('#back').addEventListener('click', () => { STAGE_DIR = 'back'; S.sub.upload = 'brief'; render(); });
}

async function handleFile(f) {
  const prev = $('#preview'); prev.src = URL.createObjectURL(f); prev.classList.remove('hidden');
  const msg = $('#uploadMsg');
  msg.innerHTML = `<div class="notice info" style="margin-top:16px"><span class="spinner" style="border-color:var(--brand-soft);border-top-color:var(--brand)"></span> ${t('s0.ocr_reading')}</div>`;
  const box = $('#sceneBox'); if (box) box.innerHTML = sceneHtml('scan');   // 판독 중 장면
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
    const b2 = $('#sceneBox'); if (b2) b2.innerHTML = sceneHtml('upload');
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
// 양성 흔적 수 — 빈 행과 검사 대조(양성·음성 대조선)는 세지 않는다
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
  if (ps) ps.innerHTML = n ? `<div class="pos-summary">${t('s1.pos_summary', { n })}</div>` : '';
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

  const body = `
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
      <p class="hint ac-hint">${t('s1.ac_hint')}</p>

      <div id="posSummary">${posCount() ? `<div class="pos-summary">${t('s1.pos_summary', { n: posCount() })}</div>` : ''}</div>`;
  view().innerHTML = stagePanel({
    scene: 'review', eyebrow: `${t('s1.eyebrow')} · 1/2`, title: t('s1.h1'), desc: t('s1.p'), body,
    actions: `<button class="btn secondary" id="back">${t('common.back')}</button>
        <span class="spacer"></span>
        <span class="hint" id="rvGateHint" style="margin-right:8px"></span>
        <button class="btn primary" id="next" ${posCount() && reviewGateOpen() ? '' : 'disabled'}>${t('s1.btn_register')}</button>`,
  });
  mountStage({
    aria: t('stage.aria', { quest: t('step.1.name') }),
    stages: [{ label: t('s1.st.check'), state: 'cur', reachable: true }, { label: t('s1.st.register'), state: 'todo', reachable: false }],
  });

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
  $('#next').addEventListener('click', confirmDiscovery);
}

// 양성 흔적을 도감에 등록(발견 연출) 후 다음 퀘스트로
function confirmDiscovery() {
  const tt = S.ocr.test_type;
  S.ocr.results = S.ocr.results.filter(r => !rowBlank(r));   // 채우지 않은 빈 행은 버린다
  const rows = S.ocr.results.filter(r => isPositive(r, tt) && !isControl(r));   // 검사 대조는 도감에 올리지 않는다
  const gained = Game.discover(S.game, rows, tt, guessDexCategory);
  Game.ui.renderHud($('#hud'), S.game);
  if (gained) Game.ui.floatXp($('#next'), gained);
  const cards = rows.map(r => ({
    name: r.korean_name || r.allergen_name, category: guessDexCategory(r), stars: Game.starsFor(r, tt),
    plateName: `${r.allergen_name || ''} ${r.korean_name || ''}`,
    pips: Game.levelPips({ class_value: r.class_value, test_value: r.value ?? r.mean_mm, test_unit: r.unit }, tt),
  }));
  Game.ui.showDiscovery($('#overlay'), cards, () => { S.sub.profile = 0; goto(2); });
}
// OCR 행에 category 가 없을 때 스탬프용 대략 분류 (guessCategory 는 screening 용 분류이므로 별도 세분화)
function guessDexCategory(r) {
  const known = refineCategory(r.category, r.allergen_name, r.korean_name);   // 서버가 준 종류, 또는 이름으로 가른 라텍스·약물
  if (known !== 'other') return known;
  const s = `${r.allergen_name || ''} ${r.korean_name || ''}`;
  const c = guessCategory(r.allergen_name, r.korean_name);
  if (c === 'pollen') return /(tree|birch|oak|alder|자작|참나무|오리나무|나무)/i.test(s) ? 'pollen_tree'
    : /(weed|ragweed|mugwort|hop|돼지풀|쑥|환삼|잡초)/i.test(s) ? 'pollen_weed' : 'pollen_grass';
  if (c === 'shellfish') return 'food';
  return ['mite', 'animal', 'mold', 'food', 'insect', 'venom'].includes(c) ? c : 'other';
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

// 한 화면에 한 가지씩: 수첩 → 지역 → 질환 → 약 → 증상 부위 → 동물
const PROFILE_STAGES = ['id', 'place', 'disease', 'meds', 'organs', 'pets'];
function ensureScreening() {
  const sc = S.screening || (S.screening = { allergic_diseases: [], current_medications: [], organ_systems: [], pets: [], symptom_present: true });
  if (!sc.pets) sc.pets = [];
  return sc;
}
function profileGo(to) {
  const cur = S.sub.profile;
  if (to > cur) stageReward('profile.' + PROFILE_STAGES[cur], $('#next'));
  STAGE_DIR = to > cur ? 'fwd' : 'back';
  S.sub.profile = to; S.sub.profileMax = Math.max(S.sub.profileMax || 0, to);
  render(); stageScroll();
}
function renderScreening() {
  const o = S.options || { screening_options: { diseases: [], medications: [], organ_systems: [] } };
  const opt = o.screening_options;
  const sc = ensureScreening();
  const p = S.ocr.patient;
  const n = PROFILE_STAGES.length;
  const i = Math.max(0, Math.min(n - 1, S.sub.profile || 0));
  const id = PROFILE_STAGES[i];
  S.sub.profileMax = Math.max(S.sub.profileMax || 0, i);

  const chipList = (items, selected, key) => `<div class="chips big" data-chipgroup="${key}">` +
    items.map(it => `<button type="button" aria-pressed="${selected.includes(it.code)}" class="chip-opt ${selected.includes(it.code) ? 'sel' : ''}" data-code="${it.code}">${esc(it.label)}</button>`).join('') + `</div>`;
  const multiHint = `<p class="hint stage-hint">${t('s2.multi')}</p>`;

  let body = '';
  if (id === 'id') {
    const ctx = screeningContext();
    const catKeys = Object.keys(ctx.cats).filter(c => c !== 'other');
    const banner = ctx.pos.length ? `
      <div class="card soft trace-banner">
        <div style="font-weight:800;font-size:14px;margin-bottom:6px">${t('s2.banner_title', { n: ctx.pos.length })}</div>
        <div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">
          ${catKeys.map(c => `<span class="chip-opt sel" style="cursor:default">${SCREEN_CAT_LABEL(c)} ${ctx.cats[c].length}</span>`).join('')}
        </div>
        <div style="font-size:12.5px;color:var(--text-2)">${t('s2.banner_note')}</div>
        ${catKeys.map(c => SCREEN_HINTS(c) ? `<div class="q-help" style="margin-top:8px">${SCREEN_HINTS(c)}</div>` : '').join('')}
      </div>` : '';
    body = `${partialNotice()}${banner}
      <div class="card soft">
        <div class="grid-3">
          <div class="field"><label for="pName">${t('s2.name')}</label><input class="input" id="pName" value="${esc(p.name || '')}" placeholder="${t('s2.name_ph')}" autocomplete="off" /></div>
          <div class="field"><label for="pAge">${t('s2.age')}</label><input class="input" id="pAge" type="number" inputmode="numeric" value="${p.age ?? ''}" placeholder="34" /></div>
          <div class="field"><label for="pGender">${t('s2.gender')}</label>
            <select class="input" id="pGender">
              <option value="M" ${p.gender === 'M' || p.gender === '남' ? 'selected' : ''}>${t('s2.male')}</option>
              <option value="F" ${p.gender === 'F' || p.gender === '여' ? 'selected' : ''}>${t('s2.female')}</option>
            </select></div>
        </div>
        <div class="field" style="margin:0"><label for="pDate">${t('s2.test_date')}</label><input class="input" id="pDate" type="date" value="${esc(p.test_date || '')}" /></div>
      </div>`;
  } else if (id === 'place') {
    body = `<div class="card soft">
        <div class="grid-3">
          <div class="field"><label for="resCountry">${t('s2.country')}</label>
            <select class="input" id="resCountry">
              <option value="">${t('s2.region_none')}</option>
              ${(S.pollenRegions || []).map(c => `<option value="${esc(c.code)}" ${sc.residence_country === c.code ? 'selected' : ''}>${esc(placeLabel('country', c.code, c.label_ko))}</option>`).join('')}
            </select></div>
          <div class="field"><label for="resRegion">${t('s2.region')}</label>
            <select class="input" id="resRegion">${regionOptions(sc)}</select></div>
          <div class="field ${sc.residence_country === 'US' ? '' : 'hidden'}" id="zipField">
            <label for="resZip">${t('s2.zip')}</label>
            <input class="input" id="resZip" placeholder="${t('s2.zip_ph')}" value="${esc(sc.residence_postal_code || '')}" />
            <div class="hint" id="zipMsg">${t('s2.zip_hint')}</div>
          </div>
        </div>
        <p class="hint" style="margin:0">${t('s2.residence_hint')}</p>
      </div>`;
  } else if (id === 'disease') {
    body = multiHint + chipList(opt.diseases, sc.allergic_diseases, 'allergic_diseases');
  } else if (id === 'meds') {
    body = multiHint + chipList(opt.medications, sc.current_medications, 'current_medications') + `<div id="ahWarn" aria-live="polite"></div>`;
  } else if (id === 'organs') {
    body = multiHint + chipList(opt.organ_systems, sc.organ_systems, 'organ_systems');
  } else {
    body = `${multiHint}<div class="chips big" data-chipgroup="pets">
        ${[['cat', t('s2.cat')], ['dog', t('s2.dog')], ['other', t('s2.other')], ['none', t('s2.none')]].map(([code, label]) =>
          `<button type="button" aria-pressed="${sc.pets.includes(code)}" class="chip-opt ${sc.pets.includes(code) ? 'sel' : ''}" data-code="${code}">${label}</button>`).join('')}
      </div>
      <input class="input ${sc.pets.includes('other') ? '' : 'hidden'}" id="petsOther" style="margin-top:12px" placeholder="${t('s2.pets_other_ph')}" aria-label="${t('s2.pets_other_ph')}" value="${esc(sc.pets_other || '')}" />`;
  }

  view().innerHTML = stagePanel({
    scene: 'profile_' + id, eyebrow: `${t('s2.eyebrow')} · ${i + 1}/${n}`, title: t(`s2.st.${id}.h`), desc: t(`s2.st.${id}.p`), body,
    actions: `<button class="btn secondary" id="back">${t('common.back')}</button>
      <span class="spacer"></span>
      <button class="btn primary" id="next">${i === n - 1 ? t('s2.btn_next') : t('s2.next')}</button>`,
  });
  mountStage({
    aria: t('stage.aria', { quest: t('step.2.name') }),
    stages: PROFILE_STAGES.map((sid, k) => ({ label: t(`s2.st.${sid}.label`), state: k === i ? 'cur' : (k < S.sub.profileMax || S.game.stagesDone['profile.' + sid] ? 'done' : 'todo'), reachable: k <= S.sub.profileMax })),
    onGoto: profileGo,
  });

  if (id === 'id') {
    // 입력 즉시 상태에 반영 — 스테이지를 오가도 값이 남는다
    p.gender = $('#pGender').value;
    $('#pName').addEventListener('input', e => { p.name = e.target.value; });
    $('#pAge').addEventListener('input', e => { p.age = e.target.value ? parseInt(e.target.value) : null; });
    $('#pGender').addEventListener('change', e => { p.gender = e.target.value; });
    $('#pDate').addEventListener('change', e => { p.test_date = e.target.value || null; });
  }
  if (id === 'place') {
    const resC = $('#resCountry'), resR = $('#resRegion');
    resC.addEventListener('change', () => {
      sc.residence_country = resC.value || null;
      sc.residence_region = null;
      sc.residence_postal_code = null;
      sc.residence_lat = sc.residence_lon = null;
      resR.innerHTML = regionOptions(sc);
      const zf = $('#zipField');
      if (zf) zf.classList.toggle('hidden', sc.residence_country !== 'US');
    });
    resR.addEventListener('change', () => { sc.residence_region = resR.value || null; });
    // 우편번호로 권역을 정한다. 주 이름을 몰라도 되고, 실시간 예보용 좌표까지 함께 받는다.
    const resZip = $('#resZip');
    if (resZip) resZip.addEventListener('change', async () => {
      const code = (resZip.value || '').trim();
      sc.residence_postal_code = code || null;
      const msg = $('#zipMsg');
      if (!code) { sc.residence_lat = sc.residence_lon = null; msg.textContent = t('s2.zip_hint'); return; }
      try {
        const r = await fetch('/api/pollen/zip?country=US&postal_code=' + encodeURIComponent(code));
        const d = await r.json();
        if (!d.ok) { msg.textContent = t('s2.zip_bad'); return; }
        sc.residence_region = d.state;
        sc.residence_lat = d.lat;
        sc.residence_lon = d.lon;
        msg.textContent = t('s2.zip_ok', { region: placeLabel('region', d.region_code, d.region_label_ko || d.state) });
        if (resR) resR.innerHTML = regionOptions(sc);
      } catch (_) { msg.textContent = t('s2.zip_bad'); }
    });
  }
  const showAhWarn = () => { const el = $('#ahWarn'); if (el) el.innerHTML = sc.current_medications.includes('antihistamine') ? `<div class="notice flag" style="margin-top:14px">${t('s2.ah_warn')}</div>` : ''; };
  view().querySelectorAll('[data-chipgroup]').forEach(group => {
    group.addEventListener('click', e => {
      const chip = e.target.closest('.chip-opt'); if (!chip) return;
      const key = group.dataset.chipgroup, code = chip.dataset.code;
      let arr = sc[key];
      if (code === 'none') { arr = chip.classList.contains('sel') ? [] : ['none']; }
      else { arr = arr.filter(c => c !== 'none'); arr = arr.includes(code) ? arr.filter(c => c !== code) : [...arr, code]; }
      sc[key] = arr;
      if (key === 'current_medications') showAhWarn();
      if (key === 'pets') { const po = $('#petsOther'); if (po) po.classList.toggle('hidden', !arr.includes('other')); }
      renderScreeningChips(sc);
    });
  });
  showAhWarn();
  const po = $('#petsOther'); if (po) po.addEventListener('input', () => { sc.pets_other = po.value; });

  $('#back').addEventListener('click', () => { if (i === 0) goto(1); else profileGo(i - 1); });
  $('#next').addEventListener('click', () => { if (i === n - 1) submitScreening(); else profileGo(i + 1); });
}
function renderScreeningChips(sc) {
  view().querySelectorAll('[data-chipgroup]').forEach(group => {
    const key = group.dataset.chipgroup;
    group.querySelectorAll('.chip-opt').forEach(c => { const on = sc[key].includes(c.dataset.code); c.classList.toggle('sel', on); c.setAttribute('aria-pressed', on); });
  });
}
async function submitScreening() {
  const sc = ensureScreening(); const p = S.ocr.patient;
  p.name = (p.name || '').trim();
  sc.antihistamine_recent = sc.current_medications.includes('antihistamine');
  sc.pets_other = sc.pets.includes('other') ? ((sc.pets_other || '').trim() || null) : null;
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s2.preparing')}`;
  try {
    const lang = I18N.getLang();
    const res = await API.post('/api/questionnaire', { ocr: S.ocr, screening: sc, lang });
    S.questionnaire = res.questionnaire; S.assessments = res.assessments; S.qLang = lang; S.qState = I18N.questionnaireState(lang, res);
    S.answers = Object.assign({}, res.questionnaire.answer_prefill || {});
    stageReward('profile.' + PROFILE_STAGES[PROFILE_STAGES.length - 1], btn);
    S.sub.q = null; S.sub.qSi = null; S.qSeen = null;
    goto(3);
  } catch (e) { toast(I18N.errText(e) || t('s2.q_fail') + e.message); btn.disabled = false; btn.textContent = t('s2.btn_next'); }
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
const qCond = (qq) => qq.reveal_if_any ? { any_of: qq.reveal_if_any } : (qq.reveal_if || null);
const qIsVisible = (qq) => condMet(qCond(qq));
function qEntries() {
  const out = [];
  S.questionnaire.sections.forEach((sec, si) => sec.questions.forEach(qq => out.push({ qq, sec, si })));
  return out;
}
const qVisibleEntries = () => qEntries().filter(e => qIsVisible(e.qq));
const SUMMARY = '__summary';

// 마무리 catch-all(food_general_react)에서, 이미 항원별 교차반응 문항으로 판정된
// 음식(있다/없다 답변 완료)은 옵션에서 제거한다 — 중복 질문 방지.
const CROSSREACT_PREFIX = 'crossreact__';
const FOOD_GENERAL_ID = 'food_general_react';
function foodJudged() {
  const judged = new Set();
  for (const sec of S.questionnaire.sections) {
    for (const qq of sec.questions) {
      if (qq.type === 'multi' && qq.id.startsWith(CROSSREACT_PREFIX)) {
        const ans = S.answers[qq.id];
        if (ans && ans.length) qq.options.forEach(o => { if (o.value !== 'none') judged.add(o.value); });
      }
    }
  }
  return judged;
}
function pruneFoodGeneral() {
  const judged = foodJudged(); const cur = S.answers[FOOD_GENERAL_ID];
  if (Array.isArray(cur)) { const kept = cur.filter(v => v === 'none' || !judged.has(v)); if (kept.length !== cur.length) S.answers[FOOD_GENERAL_ID] = kept; }
  return judged;
}
// 챕터 진행(보이는 문항 기준) + 챕터 완료 XP(1회). announce=true 면 방금 끝낸 챕터를 알린다.
function questProgress(announce) {
  let totA = 0, totV = 0; const per = [];
  S.questionnaire.sections.forEach((sec, si) => {
    const p = Game.chapterProgress(sec, S.answers, qIsVisible);
    totA += p.answered; totV += p.visible; per[si] = p;
    if (p.done) {
      const g = Game.completeChapter(S.game, sec.id || `sec${si}`);
      if (g) {
        const ring = view().querySelector(`.chapter[data-si="${si}"] .ring`);
        Game.ui.floatXp(ring, g);
        if (announce) { Game.ui.sparkle(ring && ring.closest('.chapter')); toast(t('s3.chapter_done', { title: sec.title })); }
      }
    }
  });
  Game.ui.renderQuestBar($('#questBar'), totA, totV);
  Game.ui.renderHud($('#hud'), S.game);
  return { per, totA, totV };
}
function questRail(curSi, prog) {
  const secs = S.questionnaire.sections;
  const chapters = secs.map((sec, si) => ({ sec, si })).filter(c => prog.per[c.si].visible > 0);
  return {
    chapters,
    rail: {
      aria: t('s3.chapters_aria'),
      stages: chapters.map(c => ({ label: c.sec.title, state: c.si === curSi ? 'cur' : (prog.per[c.si].done ? 'done' : 'todo'), reachable: true, meta: `${prog.per[c.si].answered}/${prog.per[c.si].visible}` }))
        .concat([{ label: t('s3.sum.label'), state: curSi === SUMMARY ? 'cur' : 'todo', reachable: true }]),
      onGoto: (k) => {
        if (k >= chapters.length) { questGo(SUMMARY, 'fwd'); return; }
        const inCh = qVisibleEntries().filter(e => e.si === chapters[k].si);
        const target = inCh.find(e => !Game.hasAnswer(S.answers[e.qq.id])) || inCh[0];
        if (target) questGo(target.qq.id, curSi === SUMMARY || chapters[k].si < curSi ? 'back' : 'fwd');
      },
    },
  };
}
function questGo(id, dir) { STAGE_DIR = dir || 'fwd'; S.sub.q = id; render(); stageScroll(); }

function renderQuestionnaire() {
  const q = S.questionnaire; const idx = q.allergen_index;
  pruneFoodGeneral();
  const vis = qVisibleEntries();
  if (!S.qSeen) S.qSeen = new Set(vis.map(e => e.qq.id));   // 처음부터 보이던 문항은 '새 단서' 가 아니다
  if (S.sub.q !== SUMMARY && !vis.some(e => e.qq.id === S.sub.q)) S.sub.q = vis.length ? vis[0].qq.id : SUMMARY;
  if (S.sub.q === SUMMARY) return renderQuestSummary(vis);

  const pos = vis.findIndex(e => e.qq.id === S.sub.q);
  const { qq, sec, si } = vis[pos];
  const inCh = vis.filter(e => e.si === si);
  const k = inCh.findIndex(e => e.qq.id === qq.id);
  // 같은 챕터 안에서는 문항 카드만, 챕터가 바뀌면 장면까지 통째로 전환한다
  const sameChapter = S.sub.qSi === si && STAGE_DIR !== 'none';
  const blockDir = sameChapter ? STAGE_DIR : 'none'; S.sub.qSi = si;

  const applyTags = (arr) => (arr && arr.length)
    ? `<div class="q-applies">${arr.filter(a => idx[a]).map(a => `<span class="tag">${CAT_EMOJI[refineCategory(idx[a].category, idx[a].name, idx[a].korean_name)] || '•'} ${esc(idx[a].korean_name || idx[a].name)}</span>`).join('')}</div>` : '';
  const val = S.answers[qq.id];
  let control;
  if (qq.type === 'multi') {
    const judged = qq.id === FOOD_GENERAL_ID ? foodJudged() : null;
    control = `<div class="chips big" data-multi="${qq.id}">` +
      qq.options.filter(o => !(judged && o.value !== 'none' && judged.has(o.value)))
        .map(o => `<button type="button" aria-pressed="${(val || []).includes(o.value)}" class="chip-opt ${(val || []).includes(o.value) ? 'sel' : ''}" data-v="${esc(o.value)}">${esc(o.label)}</button>`).join('') + `</div>`;
  } else {
    const rowClass = qq.options.length <= 3 && qq.options.every(o => o.label.length <= 12) ? 'choice-row' : 'choice-list';
    control = `<div class="${rowClass}" role="radiogroup" aria-labelledby="qTitle" data-single="${qq.id}">` +
      qq.options.map(o => `<button type="button" role="radio" aria-checked="${val === o.value}" class="choice ${val === o.value ? 'sel' : ''}" data-v="${esc(o.value)}">
        <span class="radio"></span><span class="body"><span class="t">${esc(o.label)}</span>${o.hint ? `<span class="h">${esc(o.hint)}</span>` : ''}</span></button>`).join('') + `</div>`;
  }
  const dots = inCh.map(e => `<i class="${e.qq.id === qq.id ? 'cur' : ''} ${Game.hasAnswer(S.answers[e.qq.id]) ? 'on' : ''}"></i>`).join('');
  const body = `${partialNotice('q')}
    <div class="chapter" data-si="${si}">
      <div class="clue-bar">
        <div class="ring" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" data-label="0/0" style="--p:0"></div>
        <span class="clue-count" id="clueCount">${t('s3.clue_n', { i: k + 1, n: inCh.length })}</span>
        <span class="clue-dots" aria-hidden="true">${dots}</span>
      </div>
      <div class="q-block${Game.hasAnswer(val) ? ' answered' : ''}" data-qid="${qq.id}" data-dir="${blockDir}">
        <h2 class="q-title" id="qTitle" tabindex="-1">${fmtRich(qq.title)}</h2>
        ${qq.help ? `<div class="q-help">${fmtRich(qq.help)}</div>` : ''}
        ${applyTags(qq.applies_to)}
        ${control}
      </div>
      <p class="hint clue-tip">${t('s3.tip')}</p>
      <div id="clueNote" class="clue-note" aria-live="polite"></div>
    </div>`;

  const nextLabel = () => pos === vis.length - 1 ? t('s3.to_summary') : (Game.hasAnswer(val) ? t('s3.next_clue') : t('s3.skip'));
  const prog0 = questProgress(false);
  const qr = questRail(si, prog0);
  const chNo = qr.chapters.findIndex(c => c.si === si) + 1;
  view().innerHTML = stagePanel({
    dir: sameChapter ? 'none' : STAGE_DIR,
    sceneSvg: typeof QuestArt !== 'undefined' ? QuestArt.chapterScene(sec.id) : '',
    eyebrow: `${t('s3.eyebrow')} · ${chNo}/${qr.chapters.length}`, title: esc(sec.title), desc: sec.subtitle ? esc(sec.subtitle) : '', body,
    actions: `<button class="btn secondary" id="back">${t('common.back')}</button>
      <span class="spacer"></span>
      <button class="btn primary" id="next">${nextLabel()}</button>`,
  });
  const paintRing = (prog) => {
    const ring = view().querySelector('.ring'); const p = prog.per[si];
    if (ring) { const pct = p.visible ? Math.round(p.answered / p.visible * 100) : 0; ring.style.setProperty('--p', pct); ring.dataset.label = `${p.answered}/${p.visible}`; ring.setAttribute('aria-valuenow', pct); ring.setAttribute('aria-valuetext', t('questbar.ring', { a: p.answered, v: p.visible })); }
  };
  paintRing(prog0);
  mountStage(qr.rail, '#qTitle');

  const block = view().querySelector('.q-block');
  const onAnswered = (btn) => {
    const g = Game.answer(S.game, qq.id, S.answers[qq.id]);
    if (g) Game.ui.floatXp(btn, g);
    block.classList.toggle('answered', Game.hasAnswer(S.answers[qq.id]));
    pruneFoodGeneral();
    const prog = questProgress(true);
    paintRing(prog);
    Game.ui.renderStageRail($('#stageRail'), questRail(si, prog).rail);
    // 이 답으로 새로 열린 문항(새 단서)을 알린다
    const now = qVisibleEntries();
    const fresh = now.filter(e => !S.qSeen.has(e.qq.id)); fresh.forEach(e => S.qSeen.add(e.qq.id));
    const note = $('#clueNote');
    if (note && fresh.length) note.innerHTML = `<span class="clue-tag">${t('s3.new_clues', { n: fresh.length })}</span>`;
    const chNow = now.filter(e => e.si === si);
    const kk = chNow.findIndex(e => e.qq.id === qq.id);
    $('#clueCount').textContent = t('s3.clue_n', { i: kk + 1, n: chNow.length });
    const dotsEl = view().querySelector('.clue-dots');
    if (dotsEl) dotsEl.innerHTML = chNow.map(e => `<i class="${e.qq.id === qq.id ? 'cur' : ''} ${Game.hasAnswer(S.answers[e.qq.id]) ? 'on' : ''}"></i>`).join('');
    const last = now.length && now[now.length - 1].qq.id === qq.id;
    const nx = $('#next'); nx.textContent = last ? t('s3.to_summary') : (Game.hasAnswer(S.answers[qq.id]) ? t('s3.next_clue') : t('s3.skip'));
    nx.classList.toggle('ready', Game.hasAnswer(S.answers[qq.id]));
  };
  view().querySelectorAll('[data-single]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.choice'); if (!b) return;
    S.answers[g.dataset.single] = b.dataset.v;
    g.querySelectorAll('.choice').forEach(c => { c.classList.toggle('sel', c === b); c.setAttribute('aria-checked', c === b); });
    onAnswered(b);
  }));
  view().querySelectorAll('[data-multi]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.chip-opt'); if (!b) return;
    const id = g.dataset.multi, v = b.dataset.v;
    const arr = toggleMulti(qq.options, S.answers[id], v);   // 단독 선택지('해당 없음')는 다른 선택을 지운다 → web/shared.js
    S.answers[id] = arr;
    g.querySelectorAll('.chip-opt').forEach(c => { const on = arr.includes(c.dataset.v); c.classList.toggle('sel', on); c.setAttribute('aria-pressed', on); });
    onAnswered(b);
  }));
  $('#next').classList.toggle('ready', Game.hasAnswer(val));
  $('#back').addEventListener('click', () => {
    const v2 = qVisibleEntries(); const at = v2.findIndex(e => e.qq.id === qq.id);
    if (at > 0) questGo(v2[at - 1].qq.id, 'back');
    else { S.sub.profile = PROFILE_STAGES.length - 1; goto(2); }
  });
  $('#next').addEventListener('click', () => {
    const v2 = qVisibleEntries(); const at = v2.findIndex(e => e.qq.id === qq.id);
    questGo(at >= 0 && at < v2.length - 1 ? v2[at + 1].qq.id : SUMMARY, 'fwd');
  });
}

// 마지막 스테이지 — 모은 단서를 한눈에 보고 고칠 수 있다. 여기서 판정을 요청한다.
function answerText(qq) {
  const v = S.answers[qq.id];
  if (!Game.hasAnswer(v)) return '';
  const lab = (x) => { const o = qq.options.find(o => o.value === x); return o ? o.label : x; };
  return Array.isArray(v) ? v.map(lab).join(', ') : lab(v);
}
function renderQuestSummary(vis) {
  const prog = questProgress(false);
  const qr = questRail(SUMMARY, prog);
  S.sub.qSi = SUMMARY;
  const missing = vis.filter(e => !Game.hasAnswer(S.answers[e.qq.id])).length;
  const groups = qr.chapters.map(c => {
    const items = vis.filter(e => e.si === c.si).map(e => {
      const a = answerText(e.qq);
      return `<li><button type="button" class="sum-item ${a ? '' : 'un'}" data-goto="${esc(e.qq.id)}">
          <span class="sum-q">${fmtRich(e.qq.title)}</span>
          <span class="sum-a">${a ? esc(a) : t('s3.sum.unanswered')}</span>
          <span class="sum-edit" aria-hidden="true">${t('s3.sum.edit')}</span></button></li>`;
    }).join('');
    const p = prog.per[c.si];
    return `<section class="sum-ch chapter" data-si="${c.si}"><h2 class="ch-title">${esc(c.sec.title)} <span class="sum-count">${t('s3.sum.count', { a: p.answered, v: p.visible })}</span></h2><ul class="sum-list">${items}</ul></section>`;
  }).join('');
  view().innerHTML = stagePanel({
    scene: 'summary', eyebrow: `${t('s3.eyebrow')} · ${t('s3.sum.label')}`, title: t('s3.sum.h1'), desc: t('s3.sum.p'),
    body: `<div class="notice ${missing ? 'warn' : 'info'}" style="margin-bottom:14px">${missing ? t('s3.sum.missing', { n: missing }) : t('s3.sum.all')}</div>${groups}`,
    actions: `<button class="btn secondary" id="back">${t('common.back')}</button>
      <span class="spacer"></span>
      <button class="btn primary" id="next">${t('s3.btn_next')}</button>`,
  });
  mountStage(qr.rail);
  view().querySelectorAll('[data-goto]').forEach(b => b.addEventListener('click', () => questGo(b.dataset.goto, 'back')));
  $('#back').addEventListener('click', () => {
    if (vis.length) questGo(vis[vis.length - 1].qq.id, 'back');
    else { S.sub.profile = PROFILE_STAGES.length - 1; goto(2); }
  });
  $('#next').addEventListener('click', submitClassify);
}
async function submitClassify() {
  const btn = $('#next'); btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s3.analyzing')}`;
  const box = $('#sceneBox'); if (box) box.innerHTML = sceneHtml('judge');   // 판정 중 장면
  try {
    const lang = I18N.getLang();
    const out = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers, ui: 'quest', lang, session_id: S.sessionId || undefined });
    out.lang = out.lang || lang; normalizeResult(out);
    S.classify = out; S.outputs = { [out.lang]: out };
    S.sessionId = S.classify.session_id || S.sessionId;   // 같은 탐험의 재판정은 같은 세션에 덮어쓴다
    Game.applyResults(S.game, S.classify, S.answers);   // 판정→도감·배지·severeFlag (프레젠테이션 상태)
    syncResultLang();   // 판정을 기다리는 사이 언어를 바꿨다면 그 언어의 결과를 이어서 받는다
    goto(4);
  } catch (e) {
    toast(I18N.errText(e) || t('s3.classify_fail') + e.message); btn.disabled = false; btn.textContent = t('s3.btn_next');
    if (box) box.innerHTML = sceneHtml('summary');
  }
}

/* =========================================================================
   STEP 4 — 결과
   ========================================================================= */
let RESULT_TAB = 'allergens';
function setResultTab(tab, scroll) {
  RESULT_TAB = tab;
  view().querySelectorAll('.result-tabs button').forEach(b => { const on = b.dataset.tab === tab; b.classList.toggle('active', on); b.setAttribute('aria-pressed', on); });
  view().querySelectorAll('.loot-btn').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  renderResultTab();
  if (scroll) { const el = $('.result-tabs'); if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); }
}
function renderResults() {
  const c = S.classify; const rs = resultSummary(c); const cnt = rs.counts; const p = S.ocr.patient; const g = S.game;   // 검사 대조는 개수에서 뺀다
  const lvl = Game.levelFor(g.xp);
  const severe = g.severeFlag;
  const loot = [['allergens', 'book', t('s4.got_dex', { n: rs.allergens })], ['report', 'report', t('s4.got_report')], ['cardnews', 'deck', t('s4.got_cardnews')]];
  view().innerHTML = `
    <div class="scoreboard ${severe ? 'calm' : ''}" id="scoreboard" data-dir="${STAGE_DIR}">
      <div class="sb-top">
        <div class="sb-text">
          <div class="sb-eyebrow">${t('s4.eyebrow')}</div>
          <h1>${t(severe ? 's4.h1_calm' : 's4.h1_done', { name: esc(p.name || t('common.patient')) })}</h1>
          <p>${t('s4.p', { date: esc(p.test_date || '-'), n: rs.total, lv: lvl.index + 1, title: esc(t(`level.${lvl.index}`)), xp: g.xp })}</p>
        </div>
        <div class="sb-scene">${sceneHtml('results')}</div>
      </div>
      <div class="trophies">
        <div class="trophy relevant"><div class="n">${cnt.clinically_relevant}</div><div class="l">${t('s4.trophy_rel')}</div></div>
        <div class="trophy sensitized"><div class="n">${cnt.sensitized_only}</div><div class="l">${t('s4.trophy_sens')}</div></div>
        <div class="trophy indet"><div class="n">${cnt.indeterminate}</div><div class="l">${t('s4.trophy_indet')}</div></div>
      </div>
      ${severe ? `<div class="alert-severe">${t('s4.severe')}</div>` : ''}
    </div>

    <div class="loot-row" role="group" aria-label="${t('s4.got')}">
      <span class="loot-t">${t('s4.got')}</span>
      ${loot.map(([tab, ic, label]) => `<button type="button" class="loot-btn ${RESULT_TAB === tab ? 'on' : ''}" data-tab="${tab}">${artIcon(ic)}<span>${label}</span></button>`).join('')}
      ${mailMode() ? `<button type="button" class="btn subtle sm" id="mailJump">${t(mailMode() === 'send' ? 'mail.jump' : 'mail.link_jump')}</button>` : ''}
    </div>

    <div class="badges">${Game.BADGES.map((b, i) => `<div class="badge-item ${g.badges.includes(b.id) ? 'earned' : ''}" style="--i:${i}" title="${esc(t(`badge.${b.id}.desc`))}">
        <span class="b-icon">${b.icon}</span><span><div class="b-name">${esc(t(`badge.${b.id}.name`))}</div><div class="b-desc">${esc(t(`badge.${b.id}.desc`))}</div></span></div>`).join('')}</div>

    <div id="langSync" role="status" aria-live="polite">${langSyncHtml()}</div>

    <div class="result-tabs">
      ${[['allergens', 's4.tab_dex'], ['report', 's4.tab_report'], ['cardnews', 's4.tab_cardnews'], ['chat', 's4.tab_chat'], ['fhir', 's4.tab_fhir']]
        .map(([tab, k]) => `<button type="button" data-tab="${tab}" aria-pressed="${RESULT_TAB === tab}" class="${RESULT_TAB === tab ? 'active' : ''}">${t(k)}</button>`).join('')}
    </div>
    <div id="tabBody"></div>
    <div id="mailBox"></div>

    <div class="actions">
      <button class="btn secondary" id="back">${t('s4.back_edit')}</button>
      <span class="spacer"></span>
      <button class="btn secondary" id="restart">${t('s4.restart')}</button>
    </div>`;

  if (!severe && STAGE_DIR === 'fwd') Game.ui.sparkle($('#scoreboard'));   // 완주 순간에만, 중증 시 축하 연출 억제
  STAGE_DIR = 'none';
  view().querySelectorAll('.result-tabs button').forEach(b => b.addEventListener('click', () => setResultTab(b.dataset.tab)));
  view().querySelectorAll('.loot-btn').forEach(b => b.addEventListener('click', () => setResultTab(b.dataset.tab, true)));
  $('#back').addEventListener('click', () => { S.sub.q = SUMMARY; goto(3); });
  $('#restart').addEventListener('click', () => { S.ocr = null; S.screening = null; S.questionnaire = null; S.answers = {}; S.classify = null; S.maxReached = 0; S.game = Game.createState(); resetSession();
    DEX_FILTER = 'all'; DEX_CAT = 'all'; DEX_SORT = 'verdict'; DEX_VIEW = 'cards'; RESULT_TAB = 'allergens';
    S.sub = { upload: S.briefSeen ? 'upload' : 'brief', profile: 0, profileMax: 0, q: null, qSi: null }; S.qSeen = null;
    S.chat = { messages: [], suggestions: null, busy: false, hasKey: null }; goto(0); });
  renderResultTab();
  renderMailPanel();
  bindLangRetry();
  const mj = $('#mailJump');
  if (mj) mj.addEventListener('click', () => {
    const inp = $('#mailTo'); if (!inp) return;
    $('#mailBox').scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    inp.focus({ preventScroll: true });
  });
}

function renderResultTab() {
  const body = $('#tabBody'); const c = S.classify;
  if (RESULT_TAB === 'allergens') {
    renderDexTab(body);
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
    // 카드뉴스는 넘기기·퀴즈용 인라인 스크립트가 필요하다 — 실행은 허용하되 앱과 다른 출처(불투명 출처)에 가둔다.
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

/* =========================================================================
   알러젠 도감 — 수집형 카드
   앞면: 종류 배지 · 그림 · 이름 · 검사 데이터(감작 강도·수치·시즌·증상 확인 음식) · 판정 도장
   뒷면: 판정 근거와 실천 가이드. 카드의 광택(기록 등급)은 Game.recordGrade — 기록이 채워진 정도만 본다.
   판정(진범 확정 / 무혐의·감작만 / 관찰 대상)은 테두리 모양 + 색 + 도장 문구 세 가지로 겹쳐 표시한다.
   ========================================================================= */
// 검사 대조는 서버가 판정을 붙여 보내도 판정 대상이 아니다 — '검사 대조' 도장으로 따로 보인다
const relOf = (a) => a.category === 'control' ? 'control' : (a.verdict === 'clinician_review' ? a.verdict : ((a.relevance !== 'pending' && Game.VERDICT[a.relevance]) ? a.relevance : 'not_assessed'));
const typeBadge = (cat, cls) => `<span class="type-badge ${cls || ''}">${Game.stampSvg(cat)}<span>${esc(catLabel(cat || 'other'))}</span></span>`;
const dexEntries = (list) => list.map(a => ({ a, no: S.classify.assessments.indexOf(a) + 1, pending: false }));
// 문진 전(도감 등록 직후)의 카드: 검사값만 있고 판정은 '판정 대기'
function pendingEntries() {
  return Object.entries(S.game.discovered).map(([key, d], i) => ({
    a: { allergen_name: d.en || key, korean_name: d.name, category: d.category, test_value: d.value, test_unit: d.unit, class_value: d.cls, relevance: 'pending' },
    no: d.no || i + 1, pending: true,
  }));
}
function miniModel(e, i, tag) {
  const a = e.a; const rel = e.pending ? 'pending' : relOf(a);
  const pips = Game.levelPips(a, S.ocr && S.ocr.test_type);
  const status = t(`verdict.${rel}.stamp`);
  return { name: a.korean_name || a.allergen_name, category: a.category || 'other', plateName: `${a.allergen_name || ''} ${a.korean_name || ''}`,
    pips, pipsAria: t('dex.pips_aria', { n: pips.n, max: pips.max }), tone: Game.VERDICT[rel].tone, status,
    grade: e.pending ? 0 : Game.recordGrade(a).grade, key: i, i, tag,
    aria: t('dex.aria', { name: a.korean_name || a.allergen_name, stamp: status }) };
}
function binderHtml(entries) {
  const groups = {};
  entries.forEach((e, i) => { const c = e.a.category || 'other'; (groups[c] = groups[c] || []).push([e, i]); });
  const cats = Object.keys(groups).sort((x, y) => { const o = Game.CATEGORY_ORDER; return (o.indexOf(x) < 0 ? 99 : o.indexOf(x)) - (o.indexOf(y) < 0 ? 99 : o.indexOf(y)); });
  return `<div class="binder">${cats.map(c => `<section class="binder-sec" data-cat="${esc(c)}">
      <h3 class="binder-h">${typeBadge(c)}<span class="binder-n">${groups[c].length}</span></h3>
      <div class="binder-grid">${groups[c].map(([e, i]) => Game.ui.miniCardHtml(miniModel(e, i, 'button'))).join('')}</div>
    </section>`).join('')}</div>`;
}
function dexCard(a, i, opts) {
  opts = opts || {};
  const pending = !!opts.pending;
  const rel = pending ? 'pending' : relOf(a);
  const v = { tone: Game.VERDICT[rel].tone, stamp: t(`verdict.${rel}.stamp`), note: t(`verdict.${rel}.note`) };
  const tt = S.ocr && S.ocr.test_type;
  const pips = Game.levelPips(a, tt);
  const grade = pending ? 0 : Game.recordGrade(a).grade;
  const name = a.korean_name || a.allergen_name;
  const sub = a.korean_name && a.allergen_name && a.korean_name !== a.allergen_name ? a.allergen_name : '';
  const hasOas = (a.oas_foods && a.oas_foods.length);
  const foods = [...(a.oas_foods || []), ...(a.crossreact_confirmed || [])].filter((x, k, arr) => arr.indexOf(x) === k);
  const levelText = pips.kind === 'class' ? t('dex.level_class', { n: pips.n }) : (a.strength ? t('dex.strength', { s: t(`strength.${a.strength}`) }) : '');
  const sev = a.severity && a.severity !== 'none' && a.severity !== 'mild' ? t('dex.severity', { s: ({ moderate: t('dex.sev_moderate'), severe: t('dex.sev_severe'), anaphylaxis: t('dex.sev_ana') })[a.severity] || esc(a.severity) }) : '';
  const dash = '<span class="dim">—</span>';
  const stat = (k, val, cls) => `<div class="stat ${cls || ''}"><dt>${k}</dt><dd>${val}</dd></div>`;

  // 뒷면: 판정 근거 → 실천 가이드 → 나머지 지식
  const kb = [];
  const av = (a.avoidance_control_ko || []).slice(0, 5);
  if (a.season_label_ko) kb.push([t('dex.season'), esc(a.season_label_ko)]);
  if (hasOas) kb.push([t('dex.oas_foods'), esc(a.oas_foods.join(', ')) + t('dex.oas_note')]);
  if (a.crossreact_confirmed && a.crossreact_confirmed.length) kb.push([t('dex.crossreact_confirmed'), esc(a.crossreact_confirmed.join(', '))]);
  if (a.exposure_environment_ko) kb.push([t('dex.exposure'), esc(a.exposure_environment_ko)]);
  if (a.biology_ko) kb.push([t('dex.biology'), esc(a.biology_ko)]);
  if (a.cross_reactivity_ko) kb.push([t('dex.crossreact'), esc(a.cross_reactivity_ko)]);
  const kbHtml = (av.length ? `<div class="kb-item guide"><div class="k">${t('dex.avoidance')}</div><ul>${av.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : '') +
    kb.map(([k, val]) => `<div class="kb-item"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('');
  const srcTag = a.source && a.source !== 'knowledge_base' ? `<span class="src-tag">${t('dex.source')}${a.source === 'wikipedia' ? 'Wikipedia' : t('dex.source_default')}</span>` : '';
  const back = pending
    ? `<p class="rationale">${t('dex.pending_back')}</p>`
    : rel === 'control' ? `<p class="rationale">${t('dex.control_back')}</p>`
    : `<div class="k">${t('dex.rationale')}</div><div class="rationale">${esc(a.rationale_ko || '')}${srcTag}</div><div class="kb-grid">${kbHtml}</div>`;

  return `<div class="dex-card v-${v.tone}" data-cat="${esc(a.category || 'other')}" data-grade="${grade}" data-rel="${rel}" style="--i:${i}" tabindex="0" role="button" aria-expanded="false" aria-label="${t('dex.aria', { name: esc(name), stamp: esc(v.stamp) })}">
    <div class="dex-inner">
      <div class="face front">
        <div class="dc-top">${typeBadge(a.category)}<span class="dc-no">${t('dex.no', { n: String(opts.no || i + 1).padStart(2, '0') })}</span></div>
        <div class="dc-art">${typeof QuestArt !== 'undefined' ? QuestArt.plate(a.category, `${a.allergen_name || ''} ${a.korean_name || ''}`) : Game.stampSvg(a.category)}</div>
        <h3 class="dex-name">${esc(name)}</h3>
        ${sub ? `<div class="dex-sub">${esc(sub)}</div>` : ''}
        <dl class="dc-stats">
          ${stat(t('dex.stat_level'), `${Game.ui.pipsHtml(pips, t('dex.pips_aria', { n: pips.n, max: pips.max }))}${levelText ? `<b>${esc(levelText)}</b>` : ''}`)}
          ${stat(t('dex.stat_value'), a.test_value != null ? `<b>${esc(a.test_value)}</b>${a.test_unit ? ' ' + esc(a.test_unit) : ''}` : dash)}
          ${stat(t('dex.stat_season'), a.season_label_ko ? `<span class="clamp">${esc(a.season_label_ko)}</span>` : dash)}
          ${stat(t('dex.stat_cross'), foods.length ? `<span class="clamp">${hasOas ? '🍎 ' : ''}${esc(foods.join(', '))}</span>` : (pending ? dash : `<span class="dim">${t('dex.none')}</span>`))}
        </dl>
        <div class="dc-verdict tone-${v.tone}">
          <span class="verdict-stamp tone-${v.tone}">${esc(v.stamp)}</span>
          ${v.note || sev ? `<span class="dex-note">${esc(v.note)}${sev}</span>` : ''}
        </div>
        <div class="dc-foot"><span class="grade" title="${esc(t('dex.grade_aria', { label: t(`dex.grade.${grade}`) }))}"><span class="gems" aria-hidden="true">${'◆'.repeat(grade + 1)}${'◇'.repeat(2 - grade)}</span> ${t(`dex.grade.${grade}`)}</span>
          <span class="dex-flip-hint">${t('dex.flip_hint')}</span></div>
        <span class="foil" aria-hidden="true"></span>
      </div>
      <div class="face back">
        <div class="back-head">${typeBadge(a.category, 'sm')}<b>${esc(name)}</b><span class="rel-badge ${rel === 'pending' || rel === 'control' ? 'not_assessed' : rel}">${esc(v.stamp)}</span></div>
        <div class="back-body">${back}</div>
        <span class="dex-flip-hint">${t('dex.flip_back')}</span>
      </div>
    </div>
  </div>`;
}
function renderDexTab(body) {
  const all = S.classify.assessments;
  const sum = Game.dexSummary(all);
  if (DEX_CAT !== 'all' && !sum.byCategory.some(c => c.category === DEX_CAT)) DEX_CAT = 'all';
  let list = Game.sortCards(all, DEX_SORT);
  if (DEX_FILTER !== 'all') list = list.filter(a => relOf(a) === DEX_FILTER);
  if (DEX_CAT !== 'all') list = list.filter(a => (a.category || 'other') === DEX_CAT);
  const entries = dexEntries(list);
  const cntOf = (k) => all.filter(a => relOf(a) === k).length;
  const verdictChips = [['all', t('s4.filter_all', { n: all.length })], ['clinically_relevant', t('s4.filter_rel', { n: cntOf('clinically_relevant') })], ['indeterminate', t('s4.filter_indet', { n: cntOf('indeterminate') })], ['sensitized_only', t('s4.filter_sens', { n: cntOf('sensitized_only') })]]
    .map(([k, l]) => `<button type="button" aria-pressed="${DEX_FILTER === k}" class="chip-opt ${DEX_FILTER === k ? 'sel' : ''}" data-f="${k}">${l}</button>`).join('');
  const catChips = sum.byCategory.length > 1 ? `<div class="filter-chips cat-chips">
      <button type="button" aria-pressed="${DEX_CAT === 'all'}" class="chip-opt ${DEX_CAT === 'all' ? 'sel' : ''}" data-c="all">${t('dex.cat_all')}</button>
      ${sum.byCategory.map(c => `<button type="button" aria-pressed="${DEX_CAT === c.category}" class="chip-opt type-chip ${DEX_CAT === c.category ? 'sel' : ''}" data-c="${esc(c.category)}" data-cat="${esc(c.category)}">${Game.stampSvg(c.category)}${esc(catLabel(c.category))} ${c.count}</button>`).join('')}
    </div>` : '';
  const sortSel = `<label class="sort-sel"><span>${t('dex.sort')}</span><select class="input" id="dexSort">
      ${['verdict', 'strength', 'name', 'category'].map(m => `<option value="${m}" ${DEX_SORT === m ? 'selected' : ''}>${t(`dex.sort.${m}`)}</option>`).join('')}</select></label>`;
  const content = !entries.length ? `<p class="q-help">${t('s4.dex_empty')}</p>`
    : DEX_VIEW === 'binder' ? `<p class="hint">${t('dex.binder_hint')}</p>${binderHtml(entries)}`
    : `<div class="dex-grid">${entries.map((e, i) => dexCard(e.a, i, { no: e.no })).join('')}</div>`;
  body.innerHTML = `<div class="dex-head">
      <div class="dex-progress">${artIcon('book')}<span>${t('dex.progress', { n: sum.total, r: sum.resolved })}</span></div>
      <div class="seg" role="group" aria-label="${t('dex.view_aria')}">
        ${['cards', 'binder'].map(m => `<button type="button" data-view="${m}" aria-pressed="${DEX_VIEW === m}" class="${DEX_VIEW === m ? 'on' : ''}">${t(`dex.view_${m}`)}</button>`).join('')}
      </div>
    </div>
    <div class="filter-chips">${verdictChips}</div>
    <div class="dex-tools">${catChips}${sortSel}</div>
    <p class="dex-legend">${t('dex.legend')}</p>
    ${content}`;
  body.querySelectorAll('[data-f]').forEach(b => b.addEventListener('click', () => { DEX_FILTER = b.dataset.f; renderDexTab(body); }));
  body.querySelectorAll('[data-c]').forEach(b => b.addEventListener('click', () => { DEX_CAT = b.dataset.c; renderDexTab(body); }));
  body.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => { DEX_VIEW = b.dataset.view; renderDexTab(body); }));
  $('#dexSort').addEventListener('change', e => { DEX_SORT = e.target.value; renderDexTab(body); });
  Game.ui.bindCards(body);
  body.querySelectorAll('.mini-card[data-key]').forEach(b => b.addEventListener('click', () => openDexModal(entries, parseInt(b.dataset.key))));
}
// 도감 모달: 바인더(종류별 묶음) ↔ 카드 한 장 크게 보기(←/→ 로 넘김)
function openDexModal(entries, start) {
  if (!entries.length) return;
  let cur = (start == null) ? -1 : start;
  const real = entries.filter(e => e.a.category !== 'control');   // 등록·판정 수에 검사 대조는 세지 않는다
  const resolved = real.filter(e => !e.pending && ['clinically_relevant', 'sensitized_only'].includes(e.a.relevance)).length;
  const close = Game.ui.openOverlay($('#overlay'), `<div class="dex-modal" role="dialog" aria-modal="true" aria-label="${esc(t('dex.overlay_title'))}">
      <div class="dm-head"><h2>${t('dex.overlay_title')}</h2><span class="dm-sub">${t('dex.progress', { n: real.length, r: resolved })}</span>
        <button type="button" class="btn subtle sm" id="dmClose">${t('dex.close')}</button></div>
      <div class="dm-body" id="dmBody"></div></div>`, { focus: '#dmClose', dismissOnBackdrop: true });
  const body = $('#dmBody');
  const paint = (focusCard) => {
    if (cur < 0) {
      body.innerHTML = `<p class="hint">${t('dex.binder_hint')}</p>${binderHtml(entries)}`;
      body.querySelectorAll('.mini-card[data-key]').forEach(b => b.addEventListener('click', () => { cur = parseInt(b.dataset.key); paint(true); }));
      return;
    }
    const e = entries[cur];
    body.innerHTML = `<div class="inspect">
        <button type="button" class="inspect-nav" id="dmPrev" aria-label="${esc(t('dex.prev'))}" ${cur === 0 ? 'disabled' : ''}>‹</button>
        <div class="inspect-card">${dexCard(e.a, 0, { no: e.no, pending: e.pending })}</div>
        <button type="button" class="inspect-nav" id="dmNext" aria-label="${esc(t('dex.next'))}" ${cur === entries.length - 1 ? 'disabled' : ''}>›</button>
      </div>
      <div class="inspect-foot"><button type="button" class="btn secondary sm" id="dmBack">${t('dex.back_to_binder')}</button><span class="hint">${cur + 1} / ${entries.length}</span></div>`;
    Game.ui.bindCards(body);
    $('#dmPrev').addEventListener('click', () => step(-1));
    $('#dmNext').addEventListener('click', () => step(1));
    $('#dmBack').addEventListener('click', () => { cur = -1; paint(); const f = body.querySelector('.mini-card'); if (f) f.focus(); });
    if (focusCard) { const cd = body.querySelector('.dex-card'); if (cd) cd.focus({ preventScroll: true }); }
  };
  const step = (d) => { const n = cur + d; if (cur < 0 || n < 0 || n >= entries.length) return; cur = n; paint(true); };
  $('.dex-modal').addEventListener('keydown', e => { if (e.key === 'ArrowLeft') step(-1); else if (e.key === 'ArrowRight') step(1); });
  $('#dmClose').addEventListener('click', close);
  paint(start != null);
}
// 진행 중 어디서나 여는 '내 도감' — 판정 전이므로 카드에는 '판정 대기'가 찍혀 있다
function openDexOverlay() { openDexModal(pendingEntries(), null); }
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
    sel.addEventListener('change', () => changeLang(sel.value));
  }
}

(async function () {
  initTheme(); initLang();
  // 거주 지역 선택지도 부팅 때 받아야 한다. 예전에는 언어를 바꿀 때만 불러서,
  // 처음 들어온 환자에게는 국가 목록이 비어 있었고 지역별 꽃가루 시기가 리포트에 들어가지 못했다.
  await Promise.all([loadOptions(), loadPollenRegions()]);
  initEngine();
  render();
})();
