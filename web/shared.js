/* =========================================================================
   퀘스트 UI(/) 와 클래식 UI(/classic/) 가 함께 쓰는 코드 — 두 화면의 app.js 보다 먼저 싣는다.
   · 순수 로직: 알러젠 검색, 언어 전환 폴링 판단, 상담 창 자르기, 빈 행 판별, 카드뉴스 체크 메시지 검증
     (Node 에서는 module.exports 로 내보낸다 — web/*.test.js)
   · 화면 로직: 알러젠 자동완성, 결과 언어 맞추기, 이메일 받기, 결과 상담, 언어 전환, 카드뉴스 체크 저장
     화면 로직은 각 app.js 가 전역에 정의한 것을 부를 때 찾아 쓴다(선언만 여기 있고, 호출은 app.js 가 다 실린 뒤다):
       S, t, $, esc, view, API, toast, render, catLabel, initEngine, refreshReviewSummary, RESULT_TAB, UI_NAME('quest'|'classic')
     화면마다 다른 부분은 선택 훅으로 받는다: onAllergenPicked()(자동완성으로 알러젠을 골랐을 때)
   ========================================================================= */
'use strict';

/* ---------------- 알러젠 자동완성 — 순수 검색 로직 ----------------
   GET /api/allergens 로 받은 레지스트리를 화면에서 거른다. 영문명·한글명·별칭·중국어명에서 찾고
   완전 일치 → 앞부분 일치 → 단어 앞부분 일치 → 중간 일치 순으로, 같은 순위면 대표 이름(영문·한글) 일치를 먼저 둔다.
   아래 두 표식 사이는 DOM·전역 상태를 쓰지 않는다(web/allergen.test.js). */
// <allergen-search>
const AllergenSearch = (function () {
  const norm = (s) => String(s ?? '').normalize('NFKC').toLowerCase().replace(/\s+/g, ' ').trim();
  const squash = (s) => s.replace(/ /g, '');
  // 한글 입력 중 끝에 남은 낱자(예: '자ㅈ')는 떼고 찾는다 — 조합이 끝나기 전에도 목록이 유지된다
  // (NFKC 가 낱자를 조합용 자모로 바꾸므로 정규화하기 전에 뗀다)
  function queryKey(q) { return norm(String(q ?? '').replace(/[ㄱ-ㅎㅏ-ㅣ\s]+$/, '')) || norm(q); }
  const RANK = { exact: 0, prefix: 1, word: 2, contains: 3 };
  function index(items) {
    return (items || []).map(item => ({
      item,
      fields: [['canonical_name', item.canonical_name], ['korean_name', item.korean_name],
        ...(item.aliases || []).map(a => ['alias', a]), ...(item.zh_names || []).map(z => ['zh_name', z])]
        .filter(([, text]) => text && String(text).trim())
        .map(([field, text]) => { const key = norm(text); return { field, text: String(text), key, flat: squash(key), words: key.split(/[\s,()\/.\-]+/) }; }),
    }));
  }
  function matchType(f, q, flatQ) {
    if (f.flat === flatQ) return 'exact';
    if (f.flat.startsWith(flatQ)) return 'prefix';
    if (f.words.some(w => w.startsWith(q))) return 'word';
    return f.flat.includes(flatQ) ? 'contains' : null;
  }
  function search(indexed, query, limit) {
    const q = queryKey(query); const flatQ = squash(q);
    if (!flatQ) return [];
    const hits = [];
    for (const { item, fields } of indexed || []) {
      let best = null;
      for (const f of fields) {
        const type = matchType(f, q, flatQ); if (!type) continue;
        const score = RANK[type] * 2 + (f.field === 'canonical_name' || f.field === 'korean_name' ? 0 : 1);
        if (!best || score < best.score || (score === best.score && f.flat.length < best.len)) best = { score, len: f.flat.length, field: f.field, text: f.text, type };
      }
      if (best) hits.push({ item, best });
    }
    hits.sort((a, b) => a.best.score - b.best.score || a.best.len - b.best.len
      || String(a.item.canonical_name).localeCompare(String(b.item.canonical_name)));
    return hits.slice(0, limit || 8).map(({ item, best }) => ({ item, match: { field: best.field, text: best.text, type: best.type } }));
  }
  return { norm, queryKey, index, search };
})();
// </allergen-search>

/* ---------------- 순수 보조 로직 — 언어 전환 폴링·빈 행 판별 ----------------
   아래 두 표식 사이는 DOM·전역 상태를 쓰지 않는다(web/app.pure.test.js). */
// <pure-helpers>
const LangSync = (function () {
  const DELAYS = [1500, 2500, 4000, 6000, 8000];   // 처음에는 자주, 이후 10초 간격으로 묻는다
  const MAX_ATTEMPTS = 30;                          // 모두 합쳐 약 4분 반 — 그 뒤에는 실패로 안내한다
  function delay(attempt) { return attempt >= 0 && attempt < DELAYS.length ? DELAYS[attempt] : 10000; }
  // GET /api/sessions/{id}/status 응답에서 한 언어의 처지:
  // ready(받아 올 수 있다) / wait(만드는 중) / failed(더 기다려도 오지 않는다 — failed·partial·skipped·missing 등 나머지 전부)
  function verdict(status, lang) {
    const s = status && status.langs && status.langs[lang] && status.langs[lang].status;
    if (s === 'ready') return 'ready';
    return (s === 'pending' || s === 'running') ? 'wait' : 'failed';
  }
  return { DELAYS, MAX_ATTEMPTS, delay, verdict };
})();
// /api/chat 은 messages 40개·각 4000자를 넘으면 422 로 거절한다(서버 기본값) — 최근 대화만, 길이를 맞춰 보낸다.
const CHAT_MAX_MESSAGES = 40, CHAT_MAX_CHARS = 4000;
function chatWindow(messages, maxN, maxChars) {
  let out = (messages || []).filter(m => m && (m.role === 'user' || m.role === 'assistant')).slice(-maxN);
  while (out.length && out[0].role !== 'user') out = out.slice(1);   // 잘린 창이 답변으로 시작하지 않게 한다
  return out.map(m => ({ role: m.role, content: String(m.content ?? '').slice(0, maxChars) }));
}
// 직접 입력으로 막 추가한 빈 행(이름·수치·Class 가 모두 비어 있음) — 양성 수에 세지 않고 도감에도 올리지 않는다
function rowBlank(r) {
  const empty = (v) => v == null || String(v).trim() === '';
  return !r || (empty(r.allergen_name) && empty(r.korean_name) && empty(r.value) && empty(r.mean_mm) && empty(r.class_value));
}
// 검토 탭 분류: 수치>0 이면 measured, 그 외(0/빈값)는 zero
function rowIsZero(r) {
  const v = parseFloat(r.value);
  return isNaN(v) || v === 0;
}
// 검토 탭에 적는 건수 — 채우지 않은 빈 행은 어느 탭에도 세지 않는다(표에는 입력할 수 있게 그대로 보인다)
function reviewCounts(rows) {
  const filled = (rows || []).filter(r => !rowBlank(r));
  const zero = filled.filter(rowIsZero).length;
  return { measured: filled.length - zero, zero };
}

/* 알러젠 종류(카테고리). 종류는 서버가 정한다(data/category_rules.json valid_categories — venom·latex·drug·control 포함).
   서버가 종류를 실어 보낸 항목은 'other' 까지 그대로 쓴다. 아직 서버를 거치지 않은 행(직접 입력해 종류가 비어 있는 행)만
   이름으로 대조·라텍스·약물을 가늠한다 — 판정 전에도 검사 대조를 양성 수에서 빼기 위해서다. */
const CATEGORIES = ['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'venom', 'food', 'latex', 'drug', 'control', 'other'];
const CATEGORY_NAME_RULES = [
  // 검사 대조선(양성·음성 대조) — 알러젠이 아니다. 'Histamine'·'Control' 은 서버 알러젠 목록의 대조 항목 이름이다.
  ['control', /^(positive|negative)[\s-]+control\b|^control$|^histamine$|^histamine\s+(control|solution|hcl|dihydrochloride)\b|양성\s*대조|음성\s*대조|阳性对照|阴性对照/i],
  ['latex', /\blatex\b|hevea|라텍스|乳胶/i],
  ['drug', /penicill|amoxicill|ampicill|cefaclor|cephalospor|페니실린|아목시실린|암피실린|세파클러|세팔로스포린|青霉素|阿莫西林|氨苄西林|头孢/i],
];
function refineCategory(category, ...names) {
  if (CATEGORIES.includes(category)) return category;
  const list = names.map(n => String(n == null ? '' : n).trim()).filter(Boolean);
  for (const [cat, re] of CATEGORY_NAME_RULES) if (list.some(n => re.test(n))) return cat;
  return 'other';
}
// 검사 대조 행/항목인가 — 양성 흔적으로 세지 않고, 판정 대상으로 보이지 않게 한다
function isControl(x) { return !!x && refineCategory(x.category, x.allergen_name, x.korean_name) === 'control'; }
// 결과 요약(판정별 개수·양성 수)에서 검사 대조를 뺀다. 서버 요약은 대조 항목까지 센다.
function resultSummary(c) {
  const sum = (c && c.summary) || {}; const src = sum.counts || {};
  const counts = { clinically_relevant: Number(src.clinically_relevant) || 0, sensitized_only: Number(src.sensitized_only) || 0, indeterminate: Number(src.indeterminate) || 0 };
  const list = (c && c.assessments) || []; const controls = list.filter(isControl);
  controls.forEach(a => { if (counts[a.relevance] > 0) counts[a.relevance] -= 1; });
  const total = sum.total_positive != null ? Number(sum.total_positive) || 0 : list.length;
  return { counts, total: Math.max(0, total - controls.length), controls: controls.length, allergens: list.length - controls.length };
}
/* 다중 선택 문항에서 칩 하나를 눌렀을 때의 새 답. 단독 선택지('해당 없음' 등)는 다른 선택과 함께 고를 수 없다.
   서버가 선택지에 exclusive 표식을 주면 그것을 따르고, 표식이 없는 선택지는 값이 'none'·'no' 일 때 단독으로 본다. */
function isExclusiveOption(o) {
  if (!o) return false;
  return typeof o.exclusive === 'boolean' ? o.exclusive : (o.value === 'none' || o.value === 'no');
}
function toggleMulti(options, current, value) {
  const cur = Array.isArray(current) ? current : [];
  const solo = new Set((options || []).filter(isExclusiveOption).map(o => o.value));
  if (solo.has(value)) return cur.includes(value) ? [] : [value];
  const rest = cur.filter(x => !solo.has(x));
  return rest.includes(value) ? rest.filter(x => x !== value) : [...rest, value];
}
// 추천 질문을 눌러 바로 보여 주는 답의 번역 수: 그 질문에 실려 온 수(sg.translation)가 있으면 그것, 없으면 응답 전체의 수
function suggestionCounts(sg, whole) { return (sg && sg.translation) || whole || null; }
// </pure-helpers>

/* ---------------- 카드뉴스 '내 실천 체크' — 메시지 검증(순수) ----------------
   카드뉴스는 sandbox="allow-scripts" iframe(불투명 출처) 안에서 돌아 localStorage 를 쓸 수 없다. 그래서 체크 상태를
   앱이 대신 들고 있는다: 카드뉴스가 postMessage 로 '체크 목록이 있는 카드'(hello)와 '체크 변경'(set)을 알리고,
   앱은 저장해 둔 상태(state)를 돌려준다. 받은 메시지는 아래 틀에 맞을 때만 쓴다 — 크기에 한도가 있고, 값은 불리언뿐이며,
   카드뉴스가 알려 준 항목(id)만 받는다. 내용은 저장만 하고 HTML 로 넣거나 실행하지 않는다.
     카드뉴스 → 앱  { source:'allergy-cardnews', v:1, type:'hello', cards:{ '<카드 번호>': <항목 수> } }
                    { source:'allergy-cardnews', v:1, type:'set', id:'<카드 번호>:<항목 번호>', on:true|false }
     앱 → 카드뉴스  { source:'allergy-app', v:1, type:'state', checks:{ '<카드 번호>:<항목 번호>': true|false } } */
// <deck-bridge>
const DeckBridge = (function () {
  const FRAME = 'allergy-cardnews', APP = 'allergy-app', V = 1;
  const MAX_CARDS = 60, MAX_ITEMS = 40;
  const CARD_RE = /^[1-9]\d{0,2}$/, ID_RE = /^([1-9]\d{0,2}):(0|[1-9]\d?)$/;
  const plain = (o) => !!o && typeof o === 'object' && !Array.isArray(o);
  const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  // 카드뉴스가 보낸 메시지를 읽는다. 틀에 맞지 않으면 null.
  function parse(data) {
    if (!plain(data) || data.source !== FRAME || data.v !== V) return null;
    if (data.type === 'hello') {
      if (!plain(data.cards)) return null;
      const keys = Object.keys(data.cards);
      if (!keys.length || keys.length > MAX_CARDS) return null;
      const cards = {};
      for (const k of keys) {
        const n = data.cards[k];
        if (!CARD_RE.test(k) || !Number.isInteger(n) || n < 1 || n > MAX_ITEMS) return null;
        cards[k] = n;
      }
      return { type: 'hello', cards };
    }
    if (data.type === 'set') {
      if (typeof data.id !== 'string' || !ID_RE.test(data.id) || typeof data.on !== 'boolean') return null;
      return { type: 'set', id: data.id, on: data.on };
    }
    return null;
  }
  const shapeOf = (cards) => Object.keys(cards).sort((a, b) => a - b).map(k => `${k}:${cards[k]}`).join(',');
  function known(store, id) {
    const m = ID_RE.exec(String(id)); if (!m || !store || !store.cards) return false;
    return own(store.cards, m[1]) && Number(m[2]) < store.cards[m[1]];
  }
  // hello 를 받았을 때의 저장분. 카드 구성이 같으면(언어만 바뀐 같은 판정) 체크를 이어 쓰고, 달라졌으면 새로 시작한다.
  function hello(store, cards) {
    const shape = shapeOf(cards);
    return store && store.shape === shape ? store : { shape, cards, checks: {} };
  }
  // 체크 변경을 저장한다. 알려 준 적 없는 항목이면 버린다(false).
  function set(store, id, on) {
    if (!known(store, id) || typeof on !== 'boolean') return false;
    if (on) store.checks[id] = true; else delete store.checks[id];
    return true;
  }
  // 앱이 돌려주는 상태 — 알려 준 항목 전부를 불리언으로 싣는다
  function stateMessage(store) {
    const checks = {};
    Object.keys((store && store.cards) || {}).forEach(k => { for (let i = 0; i < store.cards[k]; i++) checks[`${k}:${i}`] = store.checks[`${k}:${i}`] === true; });
    return { source: APP, v: V, type: 'state', checks };
  }
  return { FRAME, APP, V, MAX_CARDS, MAX_ITEMS, parse, known, hello, set, stateMessage };
})();
// </deck-bridge>

const MAIL_KINDS = ['report', 'cardnews', 'fhir'];
function newMail() { return { email: '', include: { report: true, cardnews: true, fhir: true }, state: 'idle', code: '', detail: '', sent: null }; }
// 새 탐험(새 결과지·데모·직접 입력·다시 시작) = 새 세션. 이전 세션 id 와 메일 입력, 카드뉴스 체크를 버린다.
function resetSession() { S.sessionId = null; S.mail = newMail(); S.outputs = {}; S.langSync = null; SYNC_SEQ++; clearTimeout(SYNC_TIMER); DECK_CHECKS.clear(); }

// 서버 선택지(스크리닝 칩 라벨)는 화면 언어에 따라 달라진다 — 언어를 바꿀 때마다 다시 받는다.
async function loadOptions() {
  try {
    S.options = await API.health(I18N.getLang());
  } catch (_) {
    S.options = S.options || { has_api_key: false, screening_options: { diseases: [], medications: [], organ_systems: [] } };
  }
}

// 받아 온 판정 결과의 알러젠 종류를 화면용으로 다듬는다(refineCategory). 서버로 되돌려 보내는 값이 아니다.
function normalizeResult(out) {
  ((out && out.assessments) || []).forEach(a => { a.category = refineCategory(a.category, a.allergen_name, a.korean_name); });
  return out;
}
// 검토 표의 알러젠 이름 칸 아래에 붙이는 안내 두 가지. 이름을 고치는 동안에는 표를 다시 그리지 않으므로 그 칸만 맞춘다.
//  · 검사 대조 행(ctl-note)
//  · 등록 목록에 없는 이름(unk-note) — /api/allergen 이 known:false 로 답한 행. 막지 않는 안내일 뿐이고, 이름을 고치면 내린다.
const UNKNOWN_NAMES = new WeakSet();   // 행 객체 → 서버로 보내는 행 데이터에는 싣지 않는다
function noteAllergenLookup(row, kb) { if (kb && kb.known === false) UNKNOWN_NAMES.add(row); else UNKNOWN_NAMES.delete(row); }
const controlNoteHtml = (r) => isControl(r) ? `<div class="ctl-note">${t('s1.control_note')}</div>` : '';
const unknownNoteHtml = (r) => UNKNOWN_NAMES.has(r) && !isControl(r) ? `<div class="unk-note">${t('s1.unknown_note')}</div>` : '';
const rowNotesHtml = (r) => controlNoteHtml(r) + unknownNoteHtml(r);
function refreshRowNotes() {
  view().querySelectorAll('#tbody tr[data-i]').forEach(tr => {
    const r = S.ocr && S.ocr.results[+tr.dataset.i]; const cell = tr.querySelector('input[data-f="allergen_name"]');
    if (!r || !cell) return;
    [['.ctl-note', controlNoteHtml(r)], ['.unk-note', unknownNoteHtml(r)]].forEach(([sel, html]) => {
      const old = tr.querySelector(sel);
      if (html && !old) cell.parentNode.insertAdjacentHTML('beforeend', html);
      else if (!html && old) old.remove();
    });
  });
}
// 이름 칸을 떠날 때: 한글명이 비어 있으면 서버에 이름을 물어 한글명·종류를 채우고, 목록에 없는 이름이면 안내를 붙인다.
// 응답을 기다리는 사이 제안을 골랐거나 이름·한글명을 고쳤으면 덮어쓰지 않는다. 표를 다시 그리지 않고 그 칸만 채운다
// (다시 그리면 방금 옮겨 간 칸의 포커스와 열려 있는 제안 목록이 사라진다).
async function lookupAllergenName(row, name) {
  if (!row || row.korean_name) return;
  let kb; try { kb = await API.allergen(name); } catch (_) { return; }
  const i = S.ocr ? S.ocr.results.indexOf(row) : -1;
  if (i < 0 || row.korean_name || String(row.allergen_name || '').trim() !== name) return;
  if (kb.korean_name) {
    row.korean_name = kb.korean_name; row.category = kb.category;
    const kinp = $(`#tbody tr[data-i="${i}"] input[data-f="korean_name"]`); if (kinp) kinp.value = kb.korean_name;
  }
  noteAllergenLookup(row, kb);
  refreshReviewSummary();
}

/* ---------------- 알러젠 자동완성 — 표 입력 칸의 콤보박스 ----------------
   표의 행은 innerHTML 로 다시 그려지므로 제안 목록(#acList)은 body 에 하나만 두고 position:fixed 로
   입력 칸 아래(자리가 없으면 위)에 붙인다 — 가로 스크롤되는 .tbl-wrap 에 잘리지 않는다.
   목록에 없는 이름은 입력한 그대로 남는다(제안은 고를 때만 값을 바꾼다). */
const AC_FIELDS = ['allergen_name', 'korean_name'];
const AC_ATTRS = 'role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="acList" autocomplete="off" autocapitalize="off" spellcheck="false"';
const AC = { input: null, items: [], active: -1 };
let ALLERGEN_INDEX = null, ALLERGEN_LOAD = null;
function loadAllergenRegistry() {
  if (!ALLERGEN_LOAD) {
    ALLERGEN_LOAD = API.allergens().then(d => {
      ALLERGEN_INDEX = AllergenSearch.index(d.items);
      const el = document.activeElement;   // 목록을 받기 전에 이미 입력 중이었다면 바로 제안한다
      if (el && el.dataset && AC_FIELDS.includes(el.dataset.f) && el.value) acUpdate(el);
    }).catch(() => { ALLERGEN_LOAD = null; });   // 실패하면 자동완성 없이 직접 입력만 — 다음 렌더 때 다시 시도
  }
  return ALLERGEN_LOAD;
}
function acListEl() {
  let el = $('#acList');
  if (!el || !el.isConnected) {
    el = document.createElement('ul'); el.id = 'acList'; el.className = 'ac-list hidden'; el.setAttribute('role', 'listbox');
    const status = document.createElement('div'); status.id = 'acStatus'; status.className = 'sr-only'; status.setAttribute('aria-live', 'polite');
    // mousedown 기본 동작(포커스 이동)을 막는다 → 입력 칸이 blur 되지 않아 click 이 먼저 처리된다
    el.addEventListener('mousedown', e => e.preventDefault());
    el.addEventListener('click', e => { const li = e.target.closest('[data-ac]'); if (li) acPick(+li.dataset.ac); });
    document.body.appendChild(el); document.body.appendChild(status);
    window.addEventListener('scroll', acPlace, true); window.addEventListener('resize', acPlace);
    if (window.visualViewport) window.visualViewport.addEventListener('resize', acPlace);
  }
  return el;
}
function acUpdate(inp) {
  const res = ALLERGEN_INDEX ? AllergenSearch.search(ALLERGEN_INDEX, inp.value, 8) : [];
  if (!res.length) return acClose();
  if (AC.input && AC.input !== inp) acClose();
  AC.input = inp; AC.items = res; AC.active = -1;
  const el = acListEl();
  el.setAttribute('aria-label', t('ac.aria'));
  el.innerHTML = res.map(({ item, match }, k) => {
    const via = (match.field === 'alias' || match.field === 'zh_name') && ![item.canonical_name, item.korean_name].includes(match.text) ? ` · ${esc(match.text)}` : '';
    return `<li role="option" id="acOpt${k}" data-ac="${k}" aria-selected="false">
      <span class="ac-main"><b>${esc(item.canonical_name)}</b>${item.korean_name ? `<span>${esc(item.korean_name)}</span>` : ''}</span>
      <span class="ac-sub">${esc(catLabel(refineCategory(item.category, item.canonical_name, item.korean_name)))}${via}</span></li>`;
  }).join('');
  el.classList.remove('hidden'); el.scrollTop = 0;
  inp.setAttribute('aria-expanded', 'true'); inp.removeAttribute('aria-activedescendant');
  const st = $('#acStatus'); if (st) st.textContent = t('ac.count', { n: res.length });
  acPlace();
}
function acPlace() {
  const inp = AC.input; if (!inp) return;
  if (!inp.isConnected) return acClose();
  const el = acListEl(); const r = inp.getBoundingClientRect();
  const vv = window.visualViewport;   // 모바일 키보드가 올라오면 보이는 높이가 줄어든다
  const vh = vv ? vv.height + vv.offsetTop : window.innerHeight; const vw = document.documentElement.clientWidth;
  if (r.bottom < 0 || r.top > vh) return acClose();
  const below = vh - r.bottom - 8, above = r.top - 8;
  const up = below < 170 && above > below;
  const w = Math.min(Math.max(r.width, 260), vw - 16);
  el.style.width = w + 'px';
  el.style.left = Math.max(8, Math.min(r.left, vw - 8 - w)) + 'px';
  el.style.maxHeight = Math.max(96, Math.min(300, up ? above : below)) + 'px';
  el.style.top = up ? 'auto' : (r.bottom + 4) + 'px';
  el.style.bottom = up ? (window.innerHeight - r.top + 4) + 'px' : 'auto';
}
function acSetActive(k) {
  const el = acListEl(); AC.active = k;
  el.querySelectorAll('[role="option"]').forEach((li, j) => li.setAttribute('aria-selected', j === k));
  const cur = k >= 0 ? el.querySelector(`#acOpt${k}`) : null;
  if (cur) { AC.input.setAttribute('aria-activedescendant', cur.id); if (cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' }); }
  else AC.input.removeAttribute('aria-activedescendant');
}
function acPick(k) {
  const hit = AC.items[k]; const inp = AC.input;
  const tr = inp && inp.closest('tr'); const row = tr && S.ocr && S.ocr.results[+tr.dataset.i];
  if (!hit || !row) return acClose();
  row.allergen_name = hit.item.canonical_name || ''; row.korean_name = hit.item.korean_name || ''; row.category = hit.item.category || null;
  UNKNOWN_NAMES.delete(row);
  AC_FIELDS.forEach(f => { const el = tr.querySelector(`input[data-f="${f}"]`); if (el) el.value = row[f]; });
  if (typeof onAllergenPicked === 'function') onAllergenPicked();
  acClose();
  refreshReviewSummary();
}
function acClose() {
  const inp = AC.input; AC.input = null; AC.items = []; AC.active = -1;
  if (inp) { inp.setAttribute('aria-expanded', 'false'); inp.removeAttribute('aria-activedescendant'); }
  const el = $('#acList'); if (el && el.classList) el.classList.add('hidden');
  const st = $('#acStatus'); if (st) st.textContent = '';
}
function acKeydown(e) {
  if (!e.target.dataset || !AC_FIELDS.includes(e.target.dataset.f)) return;
  if (e.isComposing || e.keyCode === 229) return;   // 한글·중국어 조합 중의 키는 입력기가 쓴다
  const open = AC.input === e.target && AC.items.length;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (!open) { if (e.key === 'ArrowDown' && e.target.value.trim()) { acUpdate(e.target); if (AC.items.length) { e.preventDefault(); acSetActive(0); } } return; }
    e.preventDefault();
    const n = AC.items.length, d = e.key === 'ArrowDown' ? 1 : -1;
    acSetActive(AC.active < 0 ? (d > 0 ? 0 : n - 1) : (AC.active + d + n) % n);
  } else if (e.key === 'Enter') {
    if (open && AC.active >= 0) { e.preventDefault(); acPick(AC.active); }
    else if (open) acClose();
  } else if (e.key === 'Escape') {
    if (open) { e.preventDefault(); e.stopPropagation(); acClose(); }
  }
}

/* ---------------- 결과 화면의 언어 맞추기 ----------------
   서버는 판정 때 요청 언어의 산출물을 바로 주고 나머지 두 언어는 뒤에서 만들어 세션에 저장한다.
   화면 언어를 바꾸면 그 언어의 저장분(리포트·카드뉴스·도감·요약)을 받아 통째로 바꾼다. 아직 준비되지 않았으면
   '준비 중' 안내를 띄운 채 상태를 물어보며 기다리고(간격을 늘려 가며), 끝내 못 받으면 지금 보이는 언어를 밝혀 둔다. */
const OUTPUT_KEYS = ['assessments', 'summary', 'report_markdown', 'report_html', 'report_document_html', 'cardnews_html'];
let SYNC_SEQ = 0, SYNC_TIMER = null;
const langName = (code) => (I18N.LANGS.find(l => l.code === code) || {}).label || code;
// 지금 보이는 결과의 번역 상태와 실제 언어. 요청한 언어(c.lang)가 아니라 서버가 센 번역 수(translation)와
// 저장 상태(i18n[lang])로 판단한다 — 번역 엔진이 없으면 lang 이 'en' 이어도 내용은 한국어 원문이다.
function resultLangState(c) {
  const state = I18N.translationState(c.lang, c.translation, c.i18n && c.i18n[c.lang]);
  return { state, shown: state === 'none' ? 'ko' : c.lang };
}
function langSyncHtml() {
  const c = S.classify; if (!c) return '';
  const ls = S.langSync; const ui = I18N.getLang(); const { state, shown } = resultLangState(c);
  const v = { target: esc(langName(ls ? ls.lang : ui)), shown: esc(langName(shown)) };
  const warn = (key, kind) => `<div class="notice warn lang-sync" data-sync="${kind}">⚠️ <span>${t(key, v)}</span> <button type="button" class="btn subtle sm" id="langRetry">${t('lang.retry')}</button></div>`;
  if (ls && ls.state === 'preparing') return `<div class="notice info lang-sync" data-sync="preparing"><span class="spinner" style="border-color:var(--brand-soft);border-top-color:var(--brand)"></span> <span>${t('lang.preparing', v)}</span></div>`;
  if ((ls && ls.state === 'failed') || shown !== ui) return warn('lang.failed', 'failed');   // 보이는 언어가 화면 언어와 다르다
  if (state === 'partial') { v.shown = esc(langName('ko')); return warn('lang.partial', 'partial'); }   // 일부만 번역됐다
  return '';
}
function applyOutputs(lang, out) {
  const c = Object.assign({}, S.classify);
  normalizeResult(out);
  OUTPUT_KEYS.forEach(k => { if (out[k] != null) c[k] = out[k]; });
  c.lang = lang;
  c.translation = out.translation || null; c.i18n = out.i18n || null;   // 저장분(ready)에는 없다 — 다시 판정받은 결과만 번역 수를 싣고 온다
  S.outputs[lang] = c; S.classify = c; S.langSync = null;
}
// 화면 언어로 받았지만 번역되지 않은(또는 일부만 번역된) 결과를 같은 세션에 다시 만든다 — 배너의 '다시 시도'.
async function retranslateResult() {
  const lang = I18N.getLang(); const seq = ++SYNC_SEQ; clearTimeout(SYNC_TIMER);
  S.langSync = { lang, state: 'preparing' }; render();
  let err = null;
  try {
    const out = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers, ui: UI_NAME, lang, session_id: S.sessionId || undefined });
    if (seq !== SYNC_SEQ || !S.classify) return;
    S.sessionId = out.session_id || S.sessionId;
    applyOutputs(out.lang || lang, out);
  } catch (e) { if (seq !== SYNC_SEQ) return; err = e; }
  S.langSync = null;
  if (S.step === 4) { render(); const b = $('#langRetry'); if (b) b.focus({ preventScroll: true }); }
  if (err) toast(I18N.errText(err) || t('lang.retry_fail', { target: langName(lang) }));
  else if (resultLangState(S.classify).state === 'ok') toast(t('lang.switched', { target: langName(lang) }));
}
// 화면 언어와 보이는 결과의 언어를 맞춘다. 이미 받아 둔 언어면 그 자리에서 바꾸고, 아니면 받아 오기 시작한다.
function syncResultLang() {
  const lang = I18N.getLang(); const seq = ++SYNC_SEQ; clearTimeout(SYNC_TIMER);
  S.langSync = null;
  // S.outputs 에 없는 S.classify 는 지난 탐험의 결과다(새 결과지를 올린 뒤) — 건드리지 않는다
  if (!S.classify || S.outputs[S.classify.lang] !== S.classify || S.classify.lang === lang) return;
  if (S.outputs[lang]) { S.classify = S.outputs[lang]; return; }
  S.langSync = { lang, state: 'preparing' };
  pollResultLang(seq, lang, 0, 0);
}
async function pollResultLang(seq, lang, attempt, errs) {
  const alive = () => seq === SYNC_SEQ && !!S.classify;
  const finish = (failed) => {
    if (failed) S.langSync = { lang, state: 'failed' };
    if (S.step === 4) render();
    if (!failed && resultLangState(S.classify).state === 'ok') toast(t('lang.switched', { target: langName(lang) }));   // 번역되지 않은 채 왔으면 배너가 알린다
  };
  const again = (e) => {
    if (attempt + 1 >= LangSync.MAX_ATTEMPTS) return finish(true);
    SYNC_TIMER = setTimeout(() => pollResultLang(seq, lang, attempt + 1, e), LangSync.delay(attempt));
  };
  try {
    const sid = S.sessionId;
    if (!sid) {   // 저장이 꺼져 있거나 세션이 사라졌다 — 저장분이 없으니 새 언어로 다시 판정받는다
      const out = await API.post('/api/classify', { ocr: S.ocr, screening: S.screening, answers: S.answers, ui: UI_NAME, lang });
      if (!alive()) return;
      S.sessionId = out.session_id || null;
      applyOutputs(lang, out); return finish(false);
    }
    const base = `/api/sessions/${encodeURIComponent(sid)}`;
    const sr = await fetch(`${base}/status`);
    if (!alive()) return;
    if (sr.status === 404) { if (S.sessionId === sid) resetSessionId(); return pollResultLang(seq, lang, attempt, errs); }
    if (!sr.ok) throw new Error('status ' + sr.status);
    let verdict = LangSync.verdict(await sr.json(), lang);
    let out = null;
    if (verdict === 'ready') {
      const or = await fetch(`${base}/outputs/${encodeURIComponent(lang)}`);
      if (or.status === 409) {   // 상태 조회와 그 사이에 다시 만들기 시작했다
        const d = (await or.json().catch(() => ({}))).detail || {};
        verdict = LangSync.verdict({ langs: { [lang]: { status: d.status } } }, lang);
      } else if (!or.ok) throw new Error('outputs ' + or.status);
      else out = await or.json();
    }
    if (!alive()) return;
    if (out) { applyOutputs(lang, out); return finish(false); }
    if (verdict === 'wait') return again(0);
    finish(true);
  } catch (_) {
    if (!alive()) return;
    if (errs + 1 >= 3) return finish(true);   // 연달아 세 번 닿지 못하면 그만 기다린다
    again(errs + 1);
  }
}
// 세션이 서버에서 사라졌을 때: 그 id 에 기대는 메일 패널도 함께 비운다
function resetSessionId() { S.sessionId = null; S.mail = newMail(); }
// 배너의 '다시 시도' — 결과 화면을 그린 뒤 부른다
function bindLangRetry() {
  const lr = $('#langRetry');
  if (lr) lr.addEventListener('click', () => {
    if (S.classify.lang === I18N.getLang()) retranslateResult();   // 이 언어로 받았지만 번역이 덜 됐다 — 다시 만든다
    else { syncResultLang(); render(); }
  });
}

/* ---------------- 결과 이메일 받기 ----------------
   메일 발송이 켜져 있으면(health.services.email) 입력한 주소로 리포트·카드뉴스·FHIR 를 보낸다.
   발송이 꺼져 있고 저장만 켜져 있으면 같은 칸으로 기록만 계정에 연결한다(identify). 둘 다 아니면 숨긴다. */
const MAIL_ERRORS = ['email_not_configured', 'storage_disabled', 'invalid_email', 'rate_limited', 'not_ready', 'send_failed', 'nothing_to_send', 'session_not_found', 'network', 'recipient_mismatch', 'email_already_bound'];
function mailMode() {
  const sv = (S.options && S.options.services) || {};
  if (!S.sessionId || !sv.storage) return null;
  return sv.email ? 'send' : 'link';
}
function mailMsgHtml() {
  const m = S.mail;
  if (m.state === 'err') {
    const known = MAIL_ERRORS.includes(m.code);
    if (m.code === 'rate_limited' && m.retryAfter) return `<div class="notice warn">⚠️ <span>${t('mail.err.rate_limited_wait', { s: m.retryAfter })}</span></div>`;
    // 안내문이 가리키는 '다시 시작' 버튼의 이름은 화면마다 다르다(퀘스트: 새 탐험 시작 / 클래식: 처음부터 다시)
    const restart = esc(t(UI_NAME === 'classic' ? 'c.s4.restart' : 's4.restart'));
    return `<div class="notice warn">⚠️ <span>${t(known ? `mail.err.${m.code}` : 'mail.err.unknown', { restart })}${!known && m.detail ? ` (${esc(m.detail)})` : ''}</span></div>`;
  }
  if (m.state !== 'ok' || !m.sent) return '';
  const s = m.sent;
  if (s.mode === 'link') return `<div class="notice info">✅ <span>${t('mail.linked', { to: esc(s.to) })}</span></div>`;
  return `<div class="notice info">✅ <div><div>${t('mail.sent', { to: esc(s.to) })}</div>
    ${s.attachments.length ? `<div class="mail-files"><span>${t('mail.sent_files')} (${s.attachments.length})</span><ul>${s.attachments.map(a => `<li><code>${esc(a)}</code></li>`).join('')}</ul></div>` : ''}
    ${s.fallback ? `<div class="hint" id="mailFallback">${t('mail.sent_lang', { asked: esc(langName(s.asked)), lang: esc(langName(s.lang)) })}</div>` : ''}</div></div>`;
}
// 상태가 바뀔 때는 패널을 다시 만들지 않고 안내·버튼만 바꾼다(입력 포커스와 aria-live 알림 유지)
function mailRefresh() {
  const m = S.mail; const busy = m.state === 'sending'; const mode = mailMode();
  const msg = $('#mailMsg'); if (!msg || !mode) return;
  msg.innerHTML = mailMsgHtml();
  const btn = $('#mailSend'); btn.disabled = busy;
  btn.innerHTML = busy ? `<span class="spinner"></span> ${t(mode === 'send' ? 'mail.sending' : 'mail.linking')}` : t(mode === 'send' ? 'mail.send' : 'mail.link_send');
  const inp = $('#mailTo'); inp.disabled = busy;
  if (m.state === 'err' && m.code === 'invalid_email') inp.setAttribute('aria-invalid', 'true'); else inp.removeAttribute('aria-invalid');
  $('#mailBox').querySelectorAll('[data-kind]').forEach(cb => { cb.disabled = busy; });
}
function renderMailPanel() {
  const box = $('#mailBox'); if (!box) return;
  const mode = mailMode();
  if (!mode) { box.innerHTML = ''; return; }
  const m = S.mail;
  const withPdf = !!(S.options && S.options.services && S.options.services.pdf);   // 리포트가 PDF 로도 첨부되는 서버
  box.innerHTML = `<section class="card soft mail-panel" aria-labelledby="mailTitle">
    <h2 id="mailTitle">${t(mode === 'send' ? 'mail.title' : 'mail.link_title')}</h2>
    <p class="q-help">${t(mode === 'send' ? (withPdf ? 'mail.desc_pdf' : 'mail.desc') : 'mail.link_desc')}</p>
    <form id="mailForm" novalidate>
      ${mode === 'send' ? `<fieldset class="mail-kinds"><legend>${t('mail.include')}</legend>
        ${MAIL_KINDS.map(k => `<label class="mail-kind"><input type="checkbox" data-kind="${k}" ${m.include[k] ? 'checked' : ''} /> <span>${t(k === 'report' && withPdf ? 'mail.kind.report_pdf' : `mail.kind.${k}`)}</span></label>`).join('')}
      </fieldset>` : ''}
      <label class="mail-label" for="mailTo">${t('mail.label')}</label>
      <div class="mail-row">
        <input class="input" id="mailTo" type="email" inputmode="email" autocomplete="email" autocapitalize="off" spellcheck="false"
          placeholder="name@example.com" value="${esc(m.email)}" aria-describedby="mailNote" />
        <button type="submit" class="btn primary sm" id="mailSend"></button>
      </div>
    </form>
    <div id="mailMsg" role="status" aria-live="polite"></div>
    <p class="hint" id="mailNote">${t(mode === 'send' ? 'mail.note' : 'mail.link_note')}</p>
  </section>`;
  mailRefresh();
  $('#mailTo').addEventListener('input', e => {
    m.email = e.target.value;
    if (m.state === 'err' || m.state === 'ok') { m.state = 'idle'; mailRefresh(); }
  });
  box.querySelectorAll('[data-kind]').forEach(cb => cb.addEventListener('change', () => {
    m.include[cb.dataset.kind] = cb.checked;
    if (m.state === 'err' || m.state === 'ok') { m.state = 'idle'; mailRefresh(); }
  }));
  $('#mailForm').addEventListener('submit', e => { e.preventDefault(); submitMail(); });
}
async function submitMail() {
  const m = S.mail; const mode = mailMode(); const sid = S.sessionId;
  if (!mode || m.state === 'sending') return;
  const fail = (code, detail, retryAfter) => { m.state = 'err'; m.code = code; m.detail = detail || ''; m.retryAfter = retryAfter || null; mailRefresh(); if (code === 'invalid_email') { const inp = $('#mailTo'); if (inp) inp.focus(); } };
  const email = m.email.trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return fail('invalid_email');
  const include = MAIL_KINDS.filter(k => m.include[k]);
  if (mode === 'send' && !include.length) return fail('nothing_to_send');
  m.state = 'sending'; mailRefresh();
  const asked = I18N.getLang();
  try {
    const r = mode === 'send'
      ? await API.post(`/api/sessions/${encodeURIComponent(sid)}/email`, { email, lang: asked, include })
      : await API.post(`/api/sessions/${encodeURIComponent(sid)}/identify`, { email });
    if (S.mail !== m) return;   // 기다리는 사이 새 탐험을 시작했다
    m.state = 'ok'; // 서버가 실제로 보낸 언어(lang)와 요청한 언어(requested_lang)가 다르면 fallback — 다른 언어로 갔다고 알린다
    const sentLang = r.lang || null; const wanted = r.requested_lang || asked;
    m.sent = { mode, to: r.to || r.email || email, lang: sentLang, asked: wanted, attachments: r.attachments || [],
      fallback: mode === 'send' && !!sentLang && (r.fallback != null ? !!r.fallback : sentLang !== wanted) };
    mailRefresh();
  } catch (e) {
    if (S.mail !== m) return;
    // 422 는 주소 형식 오류(서버 검증), status 가 없으면 서버에 닿지 못한 것
    fail(e.code || (e.status === 422 ? 'invalid_email' : (e.status ? 'unknown' : 'network')), e.status ? e.message : '', e.retryAfter);
  }
}

/* ---------------- 리포트 PDF 받기 ----------------
   서버가 PDF 를 만들 수 있고(health.services.pdf) 저장 세션이 있을 때만 버튼을 보인다. 화면 언어의 리포트를 받는다 —
   그 언어가 준비되지 않았으면 다른 언어를 대신 받지 않고 그 사실을 알린다: 만드는 중이면 not_ready, 만들지 못했거나
   번역을 건너뛰었으면 lang_unavailable. 세션 상태를 먼저 물어 가르고, 그 사이 상태가 바뀌어 PDF 요청이 409 로 오면 not_ready 로 본다. */
const PDF_ERRORS = ['not_ready', 'lang_unavailable', 'pdf_unavailable', 'session_not_found', 'network'];
function pdfAvailable() { const sv = (S.options && S.options.services) || {}; return !!(sv.pdf && S.sessionId); }
// 리포트 탭의 내려받기 줄 맨 앞 두 버튼: 'PDF 받기'(쓸 수 있을 때) + 브라우저 인쇄
function pdfButtonsHtml() {
  const on = pdfAvailable();
  return `${on ? `<button type="button" class="btn primary sm" id="dlPdfFile">${t('s4.dl_pdf_file')}</button>` : ''}
        <button type="button" class="btn ${on ? 'subtle' : 'primary'} sm" id="dlPdf">${t(on ? 's4.print' : 's4.dl_pdf')}</button>`;
}
function pdfErrorText(e, lang) {
  const code = e && (e.code || (e.status ? 'unknown' : 'network'));
  return I18N.errText(e) || t(PDF_ERRORS.includes(code) ? `pdf.err.${code}` : 'pdf.err.unknown', { target: esc(langName(lang)) });
}
async function downloadReportPdf() {
  const btn = $('#dlPdfFile'); const sid = S.sessionId;
  if (!btn || btn.disabled || !sid) return;
  const lang = I18N.getLang();
  const say = (html) => { const m = $('#pdfMsg'); if (m) m.innerHTML = html; };
  btn.disabled = true; btn.innerHTML = `<span class="spinner"></span> ${t('s4.pdf_making')}`; say('');
  let err = null, blob = null;
  try {
    const base = `/api/sessions/${encodeURIComponent(sid)}`;
    const sr = await fetch(`${base}/status`);
    if (sr.status === 404) throw await apiError(sr, '');
    if (sr.ok) {
      const v = LangSync.verdict(await sr.json(), lang);
      if (v !== 'ready') throw Object.assign(new Error(''), { status: 409, code: v === 'wait' ? 'not_ready' : 'lang_unavailable' });
    }
    const r = await fetch(`${base}/report.pdf?lang=${encodeURIComponent(lang)}`);
    if (!r.ok) throw await apiError(r, '');
    blob = await r.blob();
  } catch (e) { err = e; }
  if (S.sessionId !== sid) return;   // 기다리는 사이 새 탐험을 시작했다
  if (blob) download(`${(S.ocr.patient.name || 'patient')}_allergy_report_${lang}.pdf`, blob, 'application/pdf');
  const now = $('#dlPdfFile');       // 그 사이 탭을 다시 그렸을 수 있다
  if (now) { now.disabled = false; now.textContent = t('s4.dl_pdf_file'); }
  if (err) say(`<div class="notice warn">⚠️ <span>${pdfErrorText(err, lang)}</span></div>`);
}
function bindReportPdf() { const b = $('#dlPdfFile'); if (b) b.addEventListener('click', downloadReportPdf); }

/* =========================================================================
   결과 상담 챗봇 — 답변 근거는 서버가 이 환자의 판정 결과로 고정한다.
   ========================================================================= */
function renderChat(body) {
  const c = S.chat;
  const bubbles = c.messages.map(m => `
    <div class="chat-msg ${m.role}">
      <div class="chat-who">${m.role === 'user' ? t('chat.you') : t('chat.bot')}</div>
      <div class="chat-bubble">${m.role === 'user' ? esc(m.content) : mdLite(m.content)}</div>
      ${m.role === 'assistant' ? chatTrNote(m) + knowledgeSources(m.sources) : ''}
    </div>`).join('');
  const sugg = (c.suggestions || []).map((sg, i) =>
    `<button type="button" class="chip-opt" data-sg="${i}">${esc(sg.text)}</button>`).join('');
  body.innerHTML = `<div class="card soft chat-panel">
      <p class="q-help" style="margin-bottom:10px">${t('chat.intro')}</p>
      ${c.hasKey === false ? `<div class="notice warn" style="margin-bottom:10px">${t('chat.nokey')}</div>` : ''}
      ${sugg && c.sugState && c.sugState !== 'ok' && c.sugLang === I18N.getLang() ? `<div class="notice warn" id="chatTrNotice" data-tr="${c.sugState}" style="margin-bottom:10px">${t('chat.tr_notice', { target: esc(langName(c.sugLang)) })}</div>` : ''}
      ${sugg ? `<div class="chat-sugg"><div class="chat-sugg-t">${t('chat.suggested')}</div><div class="chips">${sugg}</div></div>` : ''}
      <div class="chat-log" id="chatLog" aria-live="polite">${bubbles}
        ${c.busy ? `<div class="chat-msg assistant"><div class="chat-who">${t('chat.bot')}</div><div class="chat-bubble"><span class="spinner"></span> ${t('chat.thinking')}</div></div>` : ''}
      </div>
      ${c.full ? `<div class="notice info" style="margin-top:10px">${esc(t('err.chat_full'))}</div>` : ''}
      ${c.error ? `<div class="notice warn" id="chatErr" role="alert" style="margin-top:10px">⚠️ <span>${esc(c.error)}</span></div>` : ''}
      <div class="chat-input">
        <input class="input" id="chatQ" placeholder="${t('chat.placeholder')}" value="${esc(c.draft || '')}" ${c.error ? 'aria-describedby="chatErr"' : ''} ${c.busy ? 'disabled' : ''} />
        <button class="btn primary sm" id="chatSend" ${c.busy ? 'disabled' : ''}>${t('chat.send')}</button>
      </div>
      <div class="chat-foot">
        <span>${t('app.disclaimer')}</span>
        ${c.messages.length ? `<button class="btn subtle sm" id="chatReset">${t('chat.reset')}</button>` : ''}
      </div>
    </div>`;

  const log = $('#chatLog'); if (log) log.scrollTop = log.scrollHeight;
  const send = () => {
    const inp = $('#chatQ'); const v = (inp.value || '').trim();
    if (!v || c.busy) return;
    if (v.length > CHAT_MAX_CHARS) {   // 서버가 422 로 거절할 길이 — 보내지 않고, 입력은 그대로 둔 채 알린다
      c.error = t('chat.too_long', { n: CHAT_MAX_CHARS, len: v.length }); c.draft = v; renderChat(body);
      const again = $('#chatQ'); if (again) again.focus();
      return;
    }
    c.error = null; c.draft = ''; askChat(v);
  };
  $('#chatQ').addEventListener('input', e => { c.draft = e.target.value; });
  $('#chatSend').addEventListener('click', send);
  $('#chatQ').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); send(); } });
  const rst = $('#chatReset');
  if (rst) rst.addEventListener('click', () => { c.messages = []; c.error = null; renderChat(body); });
  body.querySelectorAll('[data-sg]').forEach(b => b.addEventListener('click', () => {
    const sg = (c.suggestions || [])[parseInt(b.dataset.sg)];
    if (!sg) return;
    if (sg.answer) {   // 서버가 판정 데이터에서 바로 만든 답 — API 호출 불필요
      c.messages.push({ role: 'user', content: sg.text });
      c.messages.push({ role: 'assistant', content: sg.answer, tr: I18N.answerState(c.sugLang, sg.answer, suggestionCounts(sg, c.sugTr)), trLang: c.sugLang });
      logChatTurn(sg.text, sg.answer);
      renderChat(body);
    } else { askChat(sg.text); }
  }));
  if (c.suggestions === null && !c.busy) askChat(null);   // 최초 진입: 추천 질문만 받아온다
}

// 화면에서 바로 답한 추천 질문도 세션의 상담 기록에 남긴다. 기다리지 않고 보내며, 실패(엔드포인트가 아직 없는
// 서버·세션 없음·네트워크 오류 포함)해도 상담은 그대로 이어진다.
function logChatTurn(question, answer) {
  const sid = S.sessionId; if (!sid) return;
  try {
    fetch(`/api/sessions/${encodeURIComponent(sid)}/chat/turn`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
      body: JSON.stringify({ question, answer, lang: I18N.getLang(), source: 'suggestion' }),
    }).then(r => (r.status === 429 ? r.json() : null)).then(d => {
      if (!d || !d.detail || d.detail.code !== 'chat_full' || S.chat.full) return;
      S.chat.full = true;   // 저장 한도 도달 — 상담은 이어지지만 기록되지 않는다고 한 번 알린다
      if (S.step === 4 && RESULT_TAB === 'chat') renderChat($('#tabBody'));
    }).catch(() => {});
  } catch (_) {}
}

// 일반 질환 지식(온톨로지)을 참고한 답변에는 출처를 밝힌다.
// 스냅샷 usage_rules 가 '검토 상태·원문 URL 표시'를 요구한다. 환자에게 내부 ID 를 그대로 읽히는
// 대신, 주제·항목·검토상태를 보여주고 원문(Wikipedia 문서)으로 링크한다.
function knowledgeSources(sources) {
  if (!sources || !sources.length) return '';
  const byTopic = {};
  sources.forEach(s => { (byTopic[s.topic] = byTopic[s.topic] || { url: s.url, items: [] }).items.push(s); });
  const groups = Object.keys(byTopic).map(topic => {
    const g = byTopic[topic];
    // 연동 가이드: 관계명·claim ID·evidence ID·원문 URL·검토 상태를 표시할 것.
    // 원문 인용을 함께 보여줘야 'based on symptoms' 같은 라벨의 맥락을 알 수 있다.
    const items = g.items.slice(0, 6).map(s => `
      <li>
        <span class="src-pred">${esc(s.predicate_ko)}</span> ${esc(s.label)}
        ${s.quote ? `<div class="src-quote">“${esc(s.quote)}”</div>` : ''}
        <div class="src-ids">${esc(s.review_status || '')}${s.claim_id ? ' · ' + esc(s.claim_id) : ''}${s.evidence_id ? ' · ' + esc(s.evidence_id) : ''}</div>
      </li>`).join('');
    const link = g.url ? `<a href="${esc(g.url)}" target="_blank" rel="noopener noreferrer">${esc(topic)}</a>` : esc(topic);
    const mapping = g.items[0] && g.items[0].mapping_state
      ? `<span class="src-map">${t('chat.src_mapping', { s: esc(g.items[0].mapping_state) })}</span>` : '';
    return `<div class="src-item">${link} ${mapping}<ul class="src-list">${items}</ul></div>`;
  }).join('');
  return `<details class="kb-src"><summary>${t('chat.src_summary', { n: sources.length })}</summary>
    ${groups}<div class="src-note">${t('chat.src_note')}</div></details>`;
}

// 화면 언어로 오지 않은 답변(전부 또는 일부가 한국어 원문)에 붙이는 표시
function chatTrNote(m) {
  if (!m.tr || m.tr === 'ok') return '';
  return `<div class="chat-tr" data-tr="${m.tr}">${t(`chat.tr_${m.tr}`, { target: esc(langName(m.trLang)), shown: esc(langName('ko')) })}</div>`;
}

// 마크다운 최소 렌더(굵게·줄바꿈·번호목록) — 답변은 서버 생성 텍스트다
function mdLite(txt) {
  const lines = esc(txt).split('\n');
  return lines.map(l => l.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')).join('<br/>');
}

async function askChat(question) {
  const c = S.chat; const body = $('#tabBody');
  if (question) c.messages.push({ role: 'user', content: question });
  c.busy = true; renderChat(body);
  const lang = I18N.getLang();
  try {
    // 판정이 발급한 세션 id 를 실어야 이번 차례가 그 세션의 상담 기록에 남는다(없으면 서버는 답만 하고 저장하지 않는다)
    const r = await API.post('/api/chat', {
      ocr: S.ocr, screening: S.screening, answers: S.answers, lang, ui: UI_NAME, session_id: S.sessionId || undefined,
      messages: chatWindow(c.messages, CHAT_MAX_MESSAGES, CHAT_MAX_CHARS),
    });
    c.suggestions = lang === I18N.getLang() ? (r.suggestions || []) : null;   // 기다리는 사이 언어가 바뀌었으면 새 언어로 다시 받는다
    c.hasKey = !!r.has_api_key;
    // 추천 질문·준비된 답이 화면 언어로 왔는지. 질문마다 실려 온 번역 수 → 응답 전체의 번역 수(translation) → 문장에 남은 한글 순으로 판단한다
    const tr = r.translation || null;
    c.sugLang = lang; c.sugTr = tr;
    c.sugState = I18N.suggestionsState(lang, r.suggestions, tr);
    if (question) c.messages.push({ role: 'assistant', content: r.reply, sources: r.knowledge_sources || [], tr: I18N.answerState(lang, r.reply, tr), trLang: lang });
  } catch (e) {
    // 보내지 못한 질문은 대화에서 빼고 입력 칸에 되돌려 둔다 — 다시 누르기만 하면 된다(요청 과다 429 포함)
    if (question) {
      if (c.messages.length && c.messages[c.messages.length - 1].content === question) c.messages.pop();
      c.draft = question; c.error = I18N.errText(e) || t('chat.fail') + e.message;
    }
    c.suggestions = c.suggestions || [];
  } finally {
    c.busy = false;
    if (S.chat === c && S.step === 4 && RESULT_TAB === 'chat') renderChat($('#tabBody'));
  }
}

/* ---------------- 언어 전환 ----------------
   사전(web/i18n.js)에 있는 화면 문구는 바로 바뀐다. 서버가 만든 내용은 새 언어로 다시 받는다:
   스크리닝 선택지(health)·문진 문항(questionnaire)·추천 질문(chat)·결과 산출물(세션 저장분).
   답변·현재 문항·입력값은 코드로 들고 있으므로 그대로 남는다. */
let LANG_SEQ = 0;
async function changeLang(code) {
  I18N.setLang(code);
  const seq = ++LANG_SEQ;
  S.chat.suggestions = null;          // 추천 질문은 언어별로 만든다 — 상담 탭을 그릴 때 다시 받는다
  syncResultLang();                   // 결과가 있으면 그 언어의 저장분으로 바꾸거나 받아 오기 시작한다
  render();                           // 사전 기반 화면 문구는 즉시 반영
  await loadOptions();                // 스크리닝 칩 라벨
  if (seq !== LANG_SEQ) return;
  initEngine();
  if (S.step === 2) render();
  if (S.step === 3) reloadQuestionnaire();
}
// 문진 문항을 화면 언어로 다시 받는다(문진 화면에 있을 때, 또는 다른 언어로 받아 둔 문진으로 돌아올 때).
// S.answers·S.sub.q 는 코드라서 그대로다 — 같은 문항이 새 언어로 다시 그려진다.
async function reloadQuestionnaire() {
  const lang = I18N.getLang(); const q = S.questionnaire;
  if (!q || S.qLang === lang) return;
  try {
    const res = await API.post('/api/questionnaire', { ocr: S.ocr, screening: S.screening, lang });
    if (S.questionnaire !== q || I18N.getLang() !== lang) return;   // 그 사이 새 문진을 받았거나 언어를 또 바꿨다
    S.questionnaire = res.questionnaire; S.assessments = res.assessments; S.qLang = lang; S.qState = I18N.questionnaireState(lang, res);
    if (S.step === 3) render();
  } catch (_) { if (S.questionnaire === q && I18N.getLang() === lang) toast(t('lang.q_fail')); }
}

/* ---------------- 카드뉴스 '내 실천 체크' — 앱 쪽 저장 ----------------
   카드뉴스 iframe 을 띄울 때 mountDeckBridge(iframe) 를 부른다. 보낸 창이 지금 화면의 카드뉴스 iframe 이고
   (event.source === iframe.contentWindow), 출처가 sandbox 의 불투명 출처("null")인 메시지만 받는다.
   상태는 세션별로 들고 있어, 탭을 옮기거나 언어를 바꿔 카드뉴스를 다시 띄워도 체크가 남는다(새 탐험을 시작하면 비운다). */
const DECK_CHECKS = new Map();   // 세션 id('' = 저장 세션 없음) → { shape, cards, checks }
let DECK_FRAME = null, DECK_BOUND = false;
function onDeckMessage(e) {
  const f = DECK_FRAME;
  if (!f || !f.isConnected || !f.contentWindow || e.source !== f.contentWindow || e.origin !== 'null') return;
  const m = DeckBridge.parse(e.data); if (!m) return;
  const key = S.sessionId || '';
  if (m.type === 'hello') {
    const store = DeckBridge.hello(DECK_CHECKS.get(key), m.cards);
    DECK_CHECKS.set(key, store);
    // 불투명 출처는 targetOrigin 으로 지정할 수 없다 — 받는 창을 위에서 확인했고, 싣는 것은 불리언뿐이다
    f.contentWindow.postMessage(DeckBridge.stateMessage(store), '*');
  } else if (m.type === 'set') {
    DeckBridge.set(DECK_CHECKS.get(key), m.id, m.on);
  }
}
function mountDeckBridge(iframe) {
  DECK_FRAME = iframe || null;
  if (!DECK_BOUND) { DECK_BOUND = true; window.addEventListener('message', onDeckMessage); }
}

if (typeof module === 'object' && module.exports) {
  module.exports = { AllergenSearch, LangSync, CHAT_MAX_MESSAGES, CHAT_MAX_CHARS, chatWindow, rowBlank, rowIsZero, reviewCounts,
    CATEGORIES, refineCategory, isControl, resultSummary, DeckBridge, isExclusiveOption, toggleMulti, suggestionCounts };
}
