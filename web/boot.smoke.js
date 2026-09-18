/* app.js 부팅 스모크 테스트 — 브라우저 없이 런타임 오류를 잡는다.
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

async function fakeFetch(url) {
  const p = String(url).replace(BASE, '');
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

function run(file) {
  const src = fs.readFileSync(path.join(WEB, file), 'utf8');
  vm.runInContext(src, sandbox, { filename: file });
}

(async () => {
  try {
    run('i18n.js');
    run('game.js');
    run('app.js');
  } catch (e) {
    errors.push(`부팅 중 예외: ${e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n') : e}`);
  }

  // 부팅 IIFE 의 비동기 작업이 끝날 때까지 잠깐 기다린다
  await new Promise((r) => setTimeout(r, 400));

  // 화면 함수가 실제로 호출 가능한지 (문진 화면이 가장 최근에 손댄 곳)
  const missing = ['renderScreening', 'regionOptions', 'loadPollenRegions', 'knowledgeSources']
    .filter((fn) => typeof sandbox[fn] !== 'function');
  if (missing.length) errors.push(`전역 함수 없음: ${missing.join(', ')}`);

  if (errors.length) {
    console.error('FAIL — app.js 부팅 오류\n' + errors.map((e) => '  - ' + e).join('\n'));
    process.exit(1);
  }
  console.log(`OK — app.js 부팅 이상 없음 (${LIVE ? '실서버' : '스텁'} 응답)`);
})();
