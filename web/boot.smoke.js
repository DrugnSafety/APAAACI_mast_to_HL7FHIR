/* app.js(퀘스트)·classic/app.js(클래식) 부팅 스모크 테스트 — 브라우저 없이 런타임 오류를 잡는다.
 *
 * 왜 필요한가: `new Function(src)` 은 문법만 본다. 참조 오류·잘못된 호출은 실제로 실행해야
 * 드러나는데, 이 저장소에는 헤드리스 브라우저가 없다. 그래서 DOM·fetch 를 최소한으로 흉내 내고
 * app.js 를 통째로 실행해 본다. 화면이 안 그려지는 회귀를 CI 에서 먼저 잡는 것이 목적이다.
 *
 * 실행: node web/boot.smoke.js            (서버 없이, 스텁 응답 사용)
 *       node web/boot.smoke.js --live     (127.0.0.1:8100 실서버 응답 사용)
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const WEB = __dirname;
const LIVE = process.argv.includes('--live');
const BASE = 'http://127.0.0.1:8100';

const errors = [];
const logs = [];

// ---- 최소 DOM ----------------------------------------------------------
function makeEl(tag = 'div') {
  const el = {
    tagName: String(tag).toUpperCase(),
    style: {}, dataset: {}, classList: {
      add() {}, remove() {}, toggle() {}, contains: () => false,
    },
    children: [], attributes: {},
    _html: '',
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return ''; },
    set textContent(v) {},
    value: '', checked: false, disabled: false, scrollTop: 0, scrollHeight: 0,
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {}, focus() {}, blur() {}, click() {},
    setAttribute(k, v) { this.attributes[k] = v; }, getAttribute(k) { return this.attributes[k]; },
    removeAttribute(k) { delete this.attributes[k]; },
    addEventListener() {}, removeEventListener() {},
    querySelector() { return makeEl(); },
    querySelectorAll() { return []; },
    closest() { return null; },
    insertAdjacentHTML() {}, scrollIntoView() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    animate() { return { finished: Promise.resolve(), cancel() {} }; },
  };
  return el;
}

const document = {
  documentElement: makeEl('html'),
  body: makeEl('body'),
  head: makeEl('head'),
  title: '',
  readyState: 'complete',
  createElement: (t) => makeEl(t),
  createDocumentFragment: () => makeEl('fragment'),
  getElementById: () => makeEl(),
  querySelector: () => makeEl(),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
};

// ---- fetch: 실서버 또는 스텁 -------------------------------------------
const STUBS = {
  '/api/health': {
    ok: true, has_api_key: true, build: { commit: 'smoke' },
    kb: { total: 18 }, allergen_knowledge: { total: 128, llm_candidates: 0 },
    screening_options: {
      diseases: [{ code: 'asthma', label: '천식' }],
      medications: [{ code: 'antihistamine', label: '항히스타민제' }],
      organ_systems: [{ code: 'nasal', label: '코' }],
    },
  },
  '/api/pollen/regions': {
    live_forecast: false,
    countries: [
      { code: 'KR', label_ko: '대한민국', single_region: true,
        regions: [{ code: 'ALL', label_ko: '전국', states: [] }] },
      { code: 'US', label_ko: '미국', single_region: false,
        regions: [{ code: 'SOUTH_CENTRAL', label_ko: '남중부', states: ['TX', 'OK'] }] },
    ],
  },
};

let FETCHED = [];
async function fakeFetch(url) {
  const p = String(url).replace(BASE, '');
  FETCHED.push(p.split('?')[0]);
  if (LIVE) {
    const r = await fetch(BASE + p);
    const body = await r.text();
    return { ok: r.ok, status: r.status, json: async () => JSON.parse(body), text: async () => body };
  }
  const key = p.split('?')[0];
  const data = STUBS[key];
  if (!data) return { ok: false, status: 404, json: async () => ({}), text: async () => '' };
  return { ok: true, status: 200, json: async () => data, text: async () => JSON.stringify(data) };
}

// ---- 샌드박스 ----------------------------------------------------------
// 화면(퀘스트·클래식)마다 새 전역을 만든다 — 두 app.js 는 같은 이름의 전역(S, render …)을 쓴다.
function makeSandbox() {
const store = {};
const sandbox = {
  document,
  console: { log: (...a) => logs.push(a.join(' ')), warn() {}, error: (...a) => errors.push(a.join(' ')),
             info() {}, debug() {} },
  fetch: fakeFetch,
  localStorage: { getItem: (k) => (k in store ? store[k] : null),
                  setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: (f) => setTimeout(f, 0),
  navigator: { language: 'ko-KR', languages: ['ko-KR'], userAgent: 'smoke' },
  location: { href: BASE + '/', pathname: '/', search: '', hash: '', origin: BASE },
  history: { pushState() {}, replaceState() {} },
  FormData: class { append() {} },
  Blob: class {},
  URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
  alert() {}, scrollTo() {},
  addEventListener() {}, removeEventListener() {},
  performance: { now: () => Date.now() },
  crypto: { randomUUID: () => 'smoke-uuid' },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
return sandbox;
}

// 화면 하나를 index.html 의 <script> 순서대로 싣고 부팅시킨다
async function boot(name, files) {
  const sandbox = makeSandbox();
  FETCHED = [];
  try {
    for (const file of files) vm.runInContext(fs.readFileSync(path.join(WEB, file), 'utf8'), sandbox, { filename: file });
  } catch (e) {
    errors.push(`[${name}] 부팅 중 예외: ${e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n') : e}`);
  }

  // 부팅 IIFE 의 비동기 작업이 끝날 때까지 잠깐 기다린다
  await new Promise((r) => setTimeout(r, 400));

  // 화면 함수가 실제로 호출 가능한지 — 각 app.js 의 것과, 두 화면이 함께 쓰는 web/shared.js 의 것
  const missing = ['renderScreening', 'regionOptions', 'loadPollenRegions', 'knowledgeSources',
    'changeLang', 'syncResultLang', 'renderMailPanel', 'renderChat', 'acUpdate', 'mountDeckBridge', 'loadOptions']
    .filter((fn) => typeof sandbox[fn] !== 'function');
  if (missing.length) errors.push(`[${name}] 전역 함수 없음: ${missing.join(', ')}`);

  // 거주 지역 목록은 부팅 때 받아야 한다(언어를 바꿀 때만 받던 회귀: 첫 방문자의 국가 목록이 비었다)
  if (!FETCHED.includes('/api/pollen/regions')) errors.push(`[${name}] 부팅 때 /api/pollen/regions 를 받지 않음 — 거주 국가 목록이 비게 된다`);
  // 스크리닝 선택지는 화면 언어로 받아야 한다(health?lang=)
  if (!FETCHED.includes('/api/health')) errors.push(`[${name}] 부팅 때 /api/health 를 받지 않음`);
}

// index.html 이 싣는 스크립트 목록과 어긋나면 이 테스트가 다른 것을 검사하게 된다 — 화면 파일에서 직접 읽는다
function scriptsOf(htmlFile) {
  const html = fs.readFileSync(path.join(WEB, htmlFile), 'utf8');
  return [...html.matchAll(/<script src="\/([^"]+)"><\/script>/g)].map((m) => m[1]);
}

(async () => {
  const quest = scriptsOf('index.html'), classic = scriptsOf('classic/index.html');
  for (const [name, files] of [['퀘스트', quest], ['클래식', classic]]) {
    if (!files.includes('shared.js') || files.indexOf('shared.js') > files.findIndex((f) => f.endsWith('app.js'))) {
      errors.push(`[${name}] shared.js 는 app.js 보다 먼저 실려야 한다: ${files.join(', ')}`);
    }
    await boot(name, files);
  }

  if (errors.length) {
    console.error('FAIL — app.js 부팅 오류\n' + errors.map((e) => '  - ' + e).join('\n'));
    process.exit(1);
  }
  console.log(`OK — app.js(퀘스트)·classic/app.js(클래식) 부팅 이상 없음 (${LIVE ? '실서버' : '스텁'} 응답)`);
})();
