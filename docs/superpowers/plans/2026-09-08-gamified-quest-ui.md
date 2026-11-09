# 알러젠 탐험 퀘스트 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 바닐라 JS SPA(5단계)와 카드뉴스를 "알러젠 탐험 퀘스트 + 도감" 메타포의 게임화 UI로 재설계한다. API·판정 로직·회귀 테스트는 변경하지 않는다.

**Architecture:** `web/game.js`(신규, UMD)가 XP·레벨·도감·배지 순수 로직과 DOM 연출 헬퍼를 제공하고, `web/app.js`는 기존 렌더 함수에 훅만 추가한다. `web/styles.css`는 새 디자인 시스템으로 전면 재작성한다. 카드뉴스는 `services/cardnews_service.py`의 `_wrap()` 스타일과 카드 마크업만 교체한다.

**Tech Stack:** 바닐라 JS(ES2020), CSS custom properties + keyframes, Do Hyeon(Google Fonts) + Pretendard, Python 3.11 FastAPI(정적 서빙), Node 25(`node:test`로 game.js 단위 테스트), gstack `browse` 헤드리스 브라우저(E2E 스모크).

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-09-08-gamified-quest-ui-design.md`
- API 계약(`/api/health`, `/api/ocr/demo`, `/api/questionnaire`, `/api/classify`, `/api/fhir`) 변경 금지.
- `test_relevance_engine.py` 24종은 수정 없이 통과해야 함(카드뉴스 테스트는 **추가**만).
- 카드뉴스 HTML은 다음 문자열을 반드시 유지: `카드뉴스`(title), `이 음식들을`, `반드시 주의할 음식`, chip 마크업 `<b>{이름}</b>` 1회/항목.
- 프론트 프레임워크·빌드 도구·npm package.json 추가 금지. localStorage는 기존 `theme` 키만 사용.
- XP는 진행 행동에만 부여. 답변 내용(yes/no/unsure)으로 차등 금지. 판정 결과로 점수·등급 생성 금지.
- 아나필락시스/중증 시 축하 연출 억제(`severeFlag`).
- `@media (prefers-reduced-motion: reduce)`에서 모든 애니메이션 비활성.
- 문구: 판정 도장 `진범 확정` / `무혐의 · 감작만`(+ "감작은 남아 있어 추적 필요") / `관찰 대상`. 레벨 `새싹 탐험가`→`숙련 탐험가`→`알러젠 마스터`(임계 0/200/500).
- 임상 리포트(`services/report_design.py`, `report_service.py`)는 변경 금지.
- 커밋 메시지 끝: `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN`

## 파일 구조

| 파일 | 책임 |
|---|---|
| `web/game.js` (신규) | 게임 상태·XP·레벨·별·배지·판정 매핑 순수 로직(`Game.*`) + DOM 연출(`Game.ui.*`). Node에서 `require` 가능(UMD). |
| `web/game.test.js` (신규) | `node --test`로 game.js 순수 로직 검증 |
| `web/styles.css` | 디자인 시스템 전면 재작성 |
| `web/index.html` | 폰트·HUD·트레일·퀘스트바·오버레이 슬롯·스크립트 로드 |
| `web/app.js` | 훅 포인트·문구·결과 렌더 수정(비즈니스 로직 불변) |
| `services/cardnews_service.py` | `_wrap()` CSS + 카드 마크업 재작성 |
| `test_relevance_engine.py` | `test_cardnews_quest_theme` 추가 |
| `scripts/ui_smoke.sh` (신규) | gstack browse 기반 E2E 스모크 |

E2E 도구: `B=~/.claude/skills/gstack/browse/dist/browse` (이미 빌드됨). 주요 명령: `$B goto <url>`, `$B click <sel>`, `$B wait <sel>`, `$B js "<expr>"`, `$B is visible <sel>`, `$B screenshot <path>`, `$B viewport WxH`.

---

### Task 1: game.js 순수 로직 (TDD)

**Files:**
- Create: `web/game.js`
- Create: `web/game.test.js`

**Interfaces:**
- Produces (Task 3에서 사용):
  - `Game.createState() → {xp, stepsDone, chaptersDone, answered, discovered, edits, unsureUsed, badges, severeFlag}`
  - `Game.levelFor(xp) → {index, title, min, next|null}`, `Game.progressPct(xp) → 0..100`
  - `Game.completeStep(g, stepIndex) → gainedXp`
  - `Game.starsFor(row, testType) → 1|2|3`
  - `Game.discover(g, rows, testType) → gainedXp` (rows: `{allergen_name, korean_name, category, value, class_value}`)
  - `Game.answer(g, qid, value) → gainedXp`, `Game.hasAnswer(v) → bool`
  - `Game.chapterProgress(section, answers, isVisible) → {answered, visible, done}`
  - `Game.completeChapter(g, sectionId) → gainedXp`
  - `Game.noteEdit(g)`
  - `Game.applyResults(g, classify, answers) → {newBadges: string[]}` (discovered[*].verdict/stars 갱신, severeFlag, badges)
  - `Game.VERDICT[relevance] → {stamp, tone, note}`, `Game.BADGES`, `Game.STAMPS[category] → svg string`, `Game.stampSvg(category)`, `Game.starsHtml(n)`
  - `Game.ui.renderHud(el, g)`, `Game.ui.renderTrail(el, steps, subs, step, maxReached, onGoto)`, `Game.ui.floatXp(anchorEl|null, n)`, `Game.ui.showDiscovery(overlayEl, cards, onDone)`, `Game.ui.renderQuestBar(el, answered, visible)`, `Game.ui.sparkle(el)`

- [ ] **Step 1: 실패하는 테스트 작성**

`web/game.test.js`:
```js
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Game = require('./game.js');

test('level thresholds 0/200/500', () => {
  assert.equal(Game.levelFor(0).title, '새싹 탐험가');
  assert.equal(Game.levelFor(199).title, '새싹 탐험가');
  assert.equal(Game.levelFor(200).title, '숙련 탐험가');
  assert.equal(Game.levelFor(500).title, '알러젠 마스터');
  assert.equal(Game.levelFor(500).next, null);
  assert.equal(Game.progressPct(100), 50);
  assert.equal(Game.progressPct(500), 100);
});

test('completeStep awards 50 once', () => {
  const g = Game.createState();
  assert.equal(Game.completeStep(g, 0), 50);
  assert.equal(Game.completeStep(g, 0), 0);
  assert.equal(g.xp, 50);
});

test('starsFor: MAST class and SPT mm', () => {
  assert.equal(Game.starsFor({ class_value: 1 }, 'MAST'), 1);
  assert.equal(Game.starsFor({ class_value: 3 }, 'MAST'), 2);
  assert.equal(Game.starsFor({ class_value: 6 }, 'UniCAP'), 3);
  assert.equal(Game.starsFor({ value: 3.5 }, 'SPT'), 1);
  assert.equal(Game.starsFor({ value: 6 }, 'SPT'), 2);
  assert.equal(Game.starsFor({ value: 9 }, 'SPT'), 3);
  assert.equal(Game.starsFor({}, 'MAST'), 1);
});

test('discover: 10xp per new allergen, capped at 100, no duplicates', () => {
  const g = Game.createState();
  const rows = Array.from({ length: 12 }, (_, i) => ({ allergen_name: 'A' + i, korean_name: '항원' + i, category: 'food', class_value: 2 }));
  assert.equal(Game.discover(g, rows, 'MAST'), 100);
  assert.equal(Object.keys(g.discovered).length, 12);
  assert.equal(Game.discover(g, rows.slice(0, 2), 'MAST'), 0);
  assert.equal(g.discovered.A0.stars, 1);
  assert.equal(g.discovered.A0.verdict, null);
});

test('answer: 10xp first time only, unsure is not penalized and sets flag', () => {
  const g = Game.createState();
  assert.equal(Game.answer(g, 'q1', 'yes'), 10);
  assert.equal(Game.answer(g, 'q1', 'no'), 0);
  assert.equal(Game.answer(g, 'q2', 'unsure'), 10);
  assert.equal(g.unsureUsed, true);
  assert.equal(Game.answer(g, 'q3', ['apple']), 10);
  assert.equal(Game.hasAnswer([]), false);
  assert.equal(Game.hasAnswer('yes'), true);
});

test('chapterProgress counts only visible questions; completeChapter awards 30 once', () => {
  const g = Game.createState();
  const section = { id: 's1', questions: [{ id: 'a' }, { id: 'b' }, { id: 'c' }] };
  const answers = { a: 'yes', c: ['x'] };
  const isVisible = (q) => q.id !== 'b';
  const p = Game.chapterProgress(section, answers, isVisible);
  assert.deepEqual(p, { answered: 2, visible: 2, done: true });
  assert.equal(Game.completeChapter(g, 's1'), 30);
  assert.equal(Game.completeChapter(g, 's1'), 0);
});

test('applyResults: verdicts, stars from strength, severeFlag, badges', () => {
  const g = Game.createState();
  Game.discover(g, [{ allergen_name: 'Birch', korean_name: '자작나무', category: 'pollen_tree', class_value: 2 },
                    { allergen_name: 'Shrimp', korean_name: '새우', category: 'food', class_value: 1 }], 'MAST');
  Game.answer(g, 'q', 'unsure');
  Game.noteEdit(g);
  const classify = { assessments: [
    { allergen_name: 'Birch', relevance: 'clinically_relevant', strength: 'strong', oas_foods: ['사과'], crossreact_confirmed: [], severity: 'mild' },
    { allergen_name: 'Shrimp', relevance: 'sensitized_only', strength: 'weak', oas_foods: [], crossreact_confirmed: ['게'], crossreact_severity: 'oral' },
  ] };
  const r = Game.applyResults(g, classify, { q: 'unsure' });
  assert.equal(g.discovered.Birch.verdict, 'clinically_relevant');
  assert.equal(g.discovered.Birch.stars, 3);
  assert.equal(g.severeFlag, false);
  const ids = r.newBadges.slice().sort();
  assert.deepEqual(ids, ['crosshunter', 'dex', 'finisher', 'honest', 'oas', 'reviewer']);
  // 두 번 호출해도 배지 중복 없음
  assert.deepEqual(Game.applyResults(g, classify, {}).newBadges, []);
  assert.equal(g.badges.length, 6);
});

test('applyResults: severeFlag from anaphylaxis answer or severity', () => {
  const g1 = Game.createState();
  Game.applyResults(g1, { assessments: [{ allergen_name: 'X', relevance: 'indeterminate' }] }, { sev: 'anaphylaxis' });
  assert.equal(g1.severeFlag, true);
  const g2 = Game.createState();
  Game.applyResults(g2, { assessments: [{ allergen_name: 'X', relevance: 'clinically_relevant', severity: 'severe' }] }, {});
  assert.equal(g2.severeFlag, true);
  const g3 = Game.createState();
  Game.applyResults(g3, { assessments: [{ allergen_name: 'X', relevance: 'clinically_relevant', crossreact_severity: 'systemic' }] }, {});
  assert.equal(g3.severeFlag, true);
  // dex 배지는 not_assessed 가 있으면 미부여
  const g4 = Game.createState();
  const r4 = Game.applyResults(g4, { assessments: [{ allergen_name: 'X', relevance: 'not_assessed' }] }, {});
  assert.equal(r4.newBadges.includes('dex'), false);
});

test('VERDICT and STAMPS coverage', () => {
  assert.equal(Game.VERDICT.clinically_relevant.stamp, '진범 확정');
  assert.equal(Game.VERDICT.sensitized_only.stamp, '무혐의 · 감작만');
  assert.equal(Game.VERDICT.sensitized_only.note, '감작은 남아 있어 추적 필요');
  assert.equal(Game.VERDICT.indeterminate.stamp, '관찰 대상');
  for (const c of ['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'food', 'other'])
    assert.ok(Game.STAMPS[c].startsWith('<svg'), c);
  assert.ok(Game.stampSvg('unknown').startsWith('<svg'));
  assert.equal(Game.starsHtml(2), '<span class="stars" aria-label="감작 강도 2/3">★★<i>★</i></span>');
});
```

- [ ] **Step 2: 실패 확인**

Run: `node --test web/game.test.js`
Expected: FAIL — `Cannot find module './game.js'`

- [ ] **Step 3: game.js 구현**

`web/game.js`:
```js
/* =========================================================================
   알러젠 탐험 퀘스트 — 게임 레이어 (XP·레벨·도감·배지·연출)
   판정 로직과 무관한 프레젠테이션 상태만 다룬다. 답변 내용으로 XP 를 차등하지 않는다.
   UMD: 브라우저에서는 window.Game, Node 에서는 module.exports (단위 테스트용)
   ========================================================================= */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Game = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const XP = { STEP: 50, DISCOVER: 10, DISCOVER_CAP: 100, ANSWER: 10, CHAPTER: 30 };
  const LEVELS = [
    { min: 0, title: '새싹 탐험가' },
    { min: 200, title: '숙련 탐험가' },
    { min: 500, title: '알러젠 마스터' },
  ];
  const VERDICT = {
    clinically_relevant: { stamp: '진범 확정', tone: 'relevant', note: '노출 시 증상이 재현되는 알러젠' },
    sensitized_only: { stamp: '무혐의 · 감작만', tone: 'sensitized', note: '감작은 남아 있어 추적 필요' },
    indeterminate: { stamp: '관찰 대상', tone: 'indet', note: '노출 시 증상을 기록해 확인' },
    not_assessed: { stamp: '미확인', tone: 'na', note: '' },
  };
  const STRENGTH_STARS = { weak: 1, moderate: 2, strong: 3 };
  const SEVERE_ANSWERS = new Set(['anaphylaxis']);
  const SEVERE_LEVELS = new Set(['severe', 'anaphylaxis']);
  const SEVERE_CROSS = new Set(['systemic', 'anaphylaxis']);

  const svg = (inner) => `<svg viewBox="0 0 40 40" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  // 카테고리 스탬프(손도장 느낌의 라인 아이콘). cardnews_service._CATEGORY_STAMP_SVG 와 동일 소스.
  const STAMPS = {
    mite: svg('<circle cx="20" cy="22" r="8"/><path d="M12 18l-5-4M28 18l5-4M11 24l-6 1M29 24l6 1M13 29l-4 4M27 29l4 4M17 20h6"/>'),
    animal: svg('<circle cx="20" cy="26" r="6"/><circle cx="11" cy="18" r="3"/><circle cx="17" cy="12" r="3"/><circle cx="23" cy="12" r="3"/><circle cx="29" cy="18" r="3"/>'),
    pollen_tree: svg('<path d="M20 35V24"/><path d="M11 24h18L20 8z"/><path d="M14 18h12"/>'),
    pollen_grass: svg('<path d="M20 35V13M14 35c0-8 2-13 4-17M26 35c0-8-2-13-4-17M9 35c0-5 2-9 5-11M31 35c0-5-2-9-5-11"/>'),
    pollen_weed: svg('<path d="M20 35V15"/><path d="M20 25c-7 0-10-5-11-11 6 0 10 4 11 11zM20 20c7 0 10-5 11-11-6 0-10 4-11 11z"/>'),
    mold: svg('<circle cx="15" cy="23" r="6"/><circle cx="25" cy="18" r="5"/><circle cx="25" cy="28" r="4"/><path d="M15 23h.01M25 18h.01"/>'),
    insect: svg('<ellipse cx="20" cy="22" rx="7" ry="10"/><path d="M13 17l-5-6M27 17l5-6M12 24H6M28 24h6M14 30l-4 5M26 30l4 5M20 12v20"/>'),
    food: svg('<circle cx="20" cy="22" r="10"/><circle cx="20" cy="22" r="4"/><path d="M6 12v8M34 12v8"/>'),
    other: svg('<path d="M15 16a5 5 0 1 1 7 4.6c-1.5.8-2 1.8-2 3.4"/><circle cx="20" cy="29" r="1.3" fill="currentColor"/>'),
  };
  const BADGES = [
    { id: 'finisher', name: '완주', desc: '다섯 단계를 모두 마쳤어요', icon: '🏁' },
    { id: 'honest', name: '정직한 탐험가', desc: "'잘 모르겠어요'도 소중한 단서예요", icon: '🧭' },
    { id: 'crosshunter', name: '교차반응 헌터', desc: '증상으로 확인된 교차반응 음식을 찾았어요', icon: '🕵️' },
    { id: 'oas', name: 'OAS 탐지', desc: '꽃가루-음식 교차반응(OAS)을 확인했어요', icon: '🍎' },
    { id: 'reviewer', name: '꼼꼼한 검토자', desc: 'OCR 결과를 직접 고쳐 정확도를 높였어요', icon: '🔍' },
    { id: 'dex', name: '도감 완성', desc: '모든 양성 알러젠에 판정이 붙었어요', icon: '📖' },
  ];

  function createState() {
    return { xp: 0, stepsDone: {}, chaptersDone: {}, answered: {}, discovered: {}, edits: 0, unsureUsed: false, badges: [], severeFlag: false };
  }
  function levelFor(xp) {
    let i = 0;
    LEVELS.forEach((l, k) => { if (xp >= l.min) i = k; });
    const cur = LEVELS[i], nxt = LEVELS[i + 1];
    return { index: i, title: cur.title, min: cur.min, next: nxt ? nxt.min : null };
  }
  function progressPct(xp) {
    const l = levelFor(xp);
    if (l.next == null) return 100;
    return Math.round((xp - l.min) / (l.next - l.min) * 100);
  }
  function completeStep(g, step) {
    if (g.stepsDone[step]) return 0;
    g.stepsDone[step] = true; g.xp += XP.STEP; return XP.STEP;
  }
  // 감작 강도 별: MAST/UniCAP 은 class(1-2/3-4/5-6), SPT 는 팽진 mm(3~5/5~8/8+)
  function starsFor(row, testType) {
    if (testType === 'SPT') {
      const v = parseFloat(row.value ?? row.mean_mm);
      if (!isNaN(v)) return v >= 8 ? 3 : v >= 5 ? 2 : 1;
      return 1;
    }
    const c = parseInt(row.class_value);
    if (!isNaN(c)) return c >= 5 ? 3 : c >= 3 ? 2 : 1;
    return 1;
  }
  function discover(g, rows, testType) {
    let gained = 0;
    rows.forEach(r => {
      const key = r.allergen_name; if (!key || g.discovered[key]) return;
      g.discovered[key] = { name: r.korean_name || r.allergen_name, category: r.category || 'other', stars: starsFor(r, testType), verdict: null };
      if (gained < XP.DISCOVER_CAP) gained += XP.DISCOVER;
    });
    g.xp += gained; return gained;
  }
  function hasAnswer(v) { return Array.isArray(v) ? v.length > 0 : (v !== undefined && v !== null && v !== ''); }
  function answer(g, qid, value) {
    if (value === 'unsure' || (Array.isArray(value) && value.includes('unsure'))) g.unsureUsed = true;
    if (g.answered[qid]) return 0;
    g.answered[qid] = true; g.xp += XP.ANSWER; return XP.ANSWER;
  }
  function chapterProgress(section, answers, isVisible) {
    const vis = section.questions.filter(q => isVisible(q));
    const ans = vis.filter(q => hasAnswer(answers[q.id])).length;
    return { answered: ans, visible: vis.length, done: vis.length > 0 && ans === vis.length };
  }
  function completeChapter(g, id) {
    if (g.chaptersDone[id]) return 0;
    g.chaptersDone[id] = true; g.xp += XP.CHAPTER; return XP.CHAPTER;
  }
  function noteEdit(g) { g.edits += 1; }
  function applyResults(g, classify, answers) {
    const as = (classify && classify.assessments) || [];
    let severe = false;
    as.forEach(a => {
      const d = g.discovered[a.allergen_name] || (g.discovered[a.allergen_name] = { name: a.korean_name || a.allergen_name, category: a.category || 'other', stars: 1, verdict: null });
      d.verdict = a.relevance || 'not_assessed';
      if (a.strength && STRENGTH_STARS[a.strength]) d.stars = STRENGTH_STARS[a.strength];
      if (SEVERE_LEVELS.has(a.severity) || SEVERE_CROSS.has(a.crossreact_severity)) severe = true;
    });
    Object.values(answers || {}).forEach(v => {
      if (SEVERE_ANSWERS.has(v) || (Array.isArray(v) && v.some(x => SEVERE_ANSWERS.has(x)))) severe = true;
    });
    g.severeFlag = severe;
    const earned = [];
    const award = (id, cond) => { if (cond && !g.badges.includes(id)) { g.badges.push(id); earned.push(id); } };
    award('finisher', true);
    award('honest', g.unsureUsed);
    award('crosshunter', as.some(a => (a.crossreact_confirmed || []).length > 0));
    award('oas', as.some(a => (a.oas_foods || []).length > 0));
    award('reviewer', g.edits > 0);
    award('dex', as.length > 0 && as.every(a => a.relevance && a.relevance !== 'not_assessed'));
    return { newBadges: earned };
  }
  function stampSvg(category) { return STAMPS[category] || STAMPS.other; }
  function starsHtml(n) {
    n = Math.max(1, Math.min(3, n | 0));
    return `<span class="stars" aria-label="감작 강도 ${n}/3">${'★'.repeat(n)}${n < 3 ? `<i>${'★'.repeat(3 - n)}</i>` : ''}</span>`;
  }

  /* ---------------- DOM 연출 (브라우저 전용) ---------------- */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const ui = {
    renderHud(el, g) {
      if (!el) return;
      const l = levelFor(g.xp), pct = progressPct(g.xp);
      el.innerHTML = `<div class="hud-top"><span class="hud-title">Lv.${l.index + 1} ${esc(l.title)}</span><span class="hud-xp">${g.xp} XP${l.next != null ? ` <small>/ ${l.next}</small>` : ''}</span></div>
        <div class="hud-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><div class="hud-fill" style="width:${pct}%"></div></div>`;
    },
    renderTrail(el, steps, subs, step, maxReached, onGoto) {
      if (!el) return;
      el.innerHTML = `<svg class="tpath" viewBox="0 0 1000 60" preserveAspectRatio="none" aria-hidden="true">
          <path class="tpath-bg" d="M20 30 C 200 5, 300 55, 500 30 S 800 5, 980 30"/>
          <path class="tpath-fg" d="M20 30 C 200 5, 300 55, 500 30 S 800 5, 980 30" style="--prog:${step / (steps.length - 1)}"/>
        </svg>
        <ol class="tnodes">${steps.map((label, i) => {
          const cls = i === step ? 'active' : (i < step ? 'done' : (i <= maxReached ? 'reach' : 'locked'));
          return `<li class="tnode ${cls}" data-step="${i}" ${i <= maxReached && i !== step ? 'tabindex="0" role="button"' : ''}>
            <span class="tnum">${i < step ? '✓' : i + 1}</span>
            <span class="tlabel">${esc(label)}<small>${esc(subs[i] || '')}</small></span></li>`;
        }).join('')}</ol>`;
      el.querySelectorAll('.tnode[role="button"]').forEach(n => {
        const go = () => onGoto(parseInt(n.dataset.step));
        n.addEventListener('click', go);
        n.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      });
    },
    floatXp(anchor, n) {
      if (!n || typeof document === 'undefined') return;
      const f = document.createElement('span');
      f.className = 'xp-float'; f.textContent = `+${n} XP`;
      const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
      const hud = document.getElementById('hud');
      const hr = (!r && hud) ? hud.getBoundingClientRect() : null;
      const x = r ? r.left + r.width / 2 : (hr ? hr.left + hr.width / 2 : window.innerWidth / 2);
      const y = r ? r.top : (hr ? hr.bottom + 6 : 80);
      f.style.left = `${x}px`; f.style.top = `${y}px`;
      document.body.appendChild(f);
      setTimeout(() => f.remove(), reduced() ? 50 : 1100);
    },
    sparkle(el) {
      if (!el || reduced()) return;
      for (let i = 0; i < 6; i++) {
        const s = document.createElement('i'); s.className = 'sparkle';
        s.style.setProperty('--dx', `${(Math.random() * 2 - 1) * 60}px`);
        s.style.setProperty('--dy', `${-30 - Math.random() * 60}px`);
        s.style.left = `${20 + Math.random() * 60}%`;
        el.appendChild(s); setTimeout(() => s.remove(), 900);
      }
    },
    renderQuestBar(el, answered, visible) {
      if (!el) return;
      const pct = visible ? Math.round(answered / visible * 100) : 0;
      el.innerHTML = `<div class="qb-text"><b>진범 감별 진행</b> ${answered} / ${visible} 단서</div>
        <div class="qb-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><div class="qb-fill" style="width:${pct}%"></div></div>`;
    },
    // 발견 오버레이: cards = [{name, category, stars}], 순차 뒤집기 후 onDone
    showDiscovery(overlay, cards, onDone) {
      if (!overlay) { onDone(); return; }
      const shown = cards.slice(0, 12), extra = cards.length - shown.length;
      overlay.innerHTML = `<div class="discover" role="dialog" aria-label="발견한 알러젠">
          <div class="d-eyebrow">발견!</div>
          <h2>양성 흔적 ${cards.length}종을 도감에 등록했어요</h2>
          <p>아직 판정은 <b>미확인</b>입니다. 다음 퀘스트에서 진범을 가려냅니다.</p>
          <div class="discover-deck">${shown.map((c, i) => `<div class="dcard" style="--i:${i}">
              <div class="dstamp tone-${esc(c.category)}">${stampSvg(c.category)}</div>
              <div class="dname">${esc(c.name)}</div>${starsHtml(c.stars)}
              <div class="dq">?</div></div>`).join('')}
            ${extra > 0 ? `<div class="dcard more" style="--i:${shown.length}">+${extra}종</div>` : ''}</div>
          <button class="btn primary" id="dGo">진범 감별 퀘스트로 →</button></div>`;
      overlay.classList.remove('hidden');
      const done = () => { overlay.classList.add('hidden'); overlay.innerHTML = ''; onDone(); };
      overlay.querySelector('#dGo').addEventListener('click', done);
      overlay.querySelector('#dGo').focus();
    },
  };

  return { XP, LEVELS, VERDICT, STAMPS, BADGES, createState, levelFor, progressPct, completeStep, starsFor, discover,
           hasAnswer, answer, chapterProgress, completeChapter, noteEdit, applyResults, stampSvg, starsHtml, ui };
});
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `node --test web/game.test.js`
Expected: `# pass 9`, `# fail 0`

- [ ] **Step 5: 커밋**

```bash
git add web/game.js web/game.test.js
git commit -m "feat(ui): 게임 레이어 game.js — XP·레벨·도감·배지 순수 로직 + node 테스트

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN"
```

---

### Task 2: 디자인 시스템 — index.html + styles.css 전면 재작성

**Files:**
- Modify: `web/index.html` (전체 교체)
- Modify: `web/styles.css` (전체 교체)

**Interfaces:**
- Produces: 아래 클래스를 Task 3 마크업이 사용한다 — `.hud .hud-top .hud-title .hud-xp .hud-bar .hud-fill`, `.trail .tpath .tpath-bg .tpath-fg .tnodes .tnode(.done/.active/.reach/.locked) .tnum .tlabel`, `.quest-bar .qb-text .qb-track .qb-fill`, `.overlay .discover .d-eyebrow .discover-deck .dcard(.more) .dstamp .dname .dq`, `.stars i`, `.xp-float`, `.sparkle`, `.chapter .ch-head .ring(--p) .ch-title`, `.q-block.clue-new .clue-tag`, `.scoreboard .trophy(.relevant/.sensitized/.indet) .n .l`, `.alert-severe`, `.badges .badge-item(.earned) .b-icon .b-name .b-desc`, `.filter-chips`, `.dex-grid .dex-card(.flip) .dex-inner .face.front/.back .verdict-stamp.tone-relevant/.tone-sensitized/.tone-indet/.tone-na .dex-note`, 기존 클래스(`.panel .panel-head .eyebrow .dropzone .notice .btn .card .field .input .chips .chip-opt .rv-tabs .tbl-wrap table.grid .pos-toggle .tbl-toolbar .pos-summary .actions .spacer .q-section .q-block .q-title .q-help .q-applies .tag .choice-row .choice-list .choice .radio .result-tabs .allergen-card .ac-head .ac-body .rel-badge .kb-grid .kb-item .report-frame .cardnews-frame .download-row .toast .disclaimer .hidden .spinner .grid-3 .badge-count .preview-img .upload-alt .src-tag .rationale`)는 유지.

- [ ] **Step 1: index.html 교체**

`web/index.html`:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>알러젠 탐험 퀘스트 · 알레르기 검사 결과 리포트</title>
  <meta name="description" content="알레르기 검사(SPT·MAST·UniCAP) 결과를 탐험 퀘스트처럼 따라가며 감작과 실제 알레르기를 감별하고 환자 맞춤 리포트를 만들어 드립니다." />
  <link rel="preconnect" href="https://cdn.jsdelivr.net" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css" />
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap" />
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <header class="app-header">
    <div class="bar">
      <div class="logo">
        <span class="mark">🧭</span>
        <span>알러젠 탐험 퀘스트
          <small>검사 양성은 흔적일 뿐 · 진범은 증상으로 확정</small>
        </span>
      </div>
      <div class="hud" id="hud" aria-live="polite"></div>
      <button class="theme-toggle" id="themeToggle" title="라이트/다크 전환" aria-label="테마 전환">🌙</button>
    </div>
  </header>

  <main>
    <nav class="trail" id="stepper" aria-label="퀘스트 진행"></nav>
    <div id="view"><!-- 단계별 화면 렌더링 --></div>
    <p class="disclaimer">
      본 서비스는 교육용 참고 자료이며 의학적 진단·치료를 대체하지 않습니다. 정확한 판단은 담당 의료진과 상담하세요.
    </p>
  </main>

  <div class="quest-bar hidden" id="questBar" aria-live="polite"></div>
  <div class="toast" id="toast"></div>
  <div class="overlay hidden" id="overlay"></div>
  <script src="/game.js"></script>
  <script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: styles.css 교체**

`web/styles.css`:
```css
/* =========================================================================
   알러젠 탐험 퀘스트 — 디자인 시스템
   크림 종이 + 숲 녹색 + 호박색. 도장(stamp)·도감(dex)·트레일(trail) 컴포넌트.
   토큰 기반, 라이트/다크, prefers-reduced-motion 대응.
   ========================================================================= */

:root {
  --bg: #fbf7ee;
  --bg-elev: #fffdf8;
  --bg-subtle: #f3ecdc;
  --surface-2: #f7f1e3;
  --border: #e6dcc6;
  --border-strong: #d2c4a6;
  --text: #1f2a24;
  --text-2: #4d5a52;
  --text-3: #83907f;
  --ink: #1f2a24;

  --brand: #2f8f5b;
  --brand-strong: #24734a;
  --brand-soft: #e3f2e9;
  --brand-contrast: #ffffff;
  --accent: #f2a33a;
  --accent-soft: #fdeed6;
  --accent-ink: #8a5a12;

  --relevant: #e4572e;
  --relevant-soft: #fde9e2;
  --relevant-border: #f6c3b3;
  --sensitized: #6b7a8f;
  --sensitized-soft: #eef1f5;
  --sensitized-border: #d5dce6;
  --indet: #d9860a;
  --indet-soft: #fdf3e3;
  --indet-border: #f3dcae;
  --ok: #2f8f5b;
  --ok-soft: #e3f2e9;

  --shadow-sm: 0 1px 2px rgba(60,48,20,.06), 0 1px 3px rgba(60,48,20,.05);
  --shadow-md: 0 6px 16px rgba(60,48,20,.10), 0 2px 6px rgba(60,48,20,.05);
  --shadow-lg: 0 18px 44px rgba(60,48,20,.16);
  --shadow-brand: 0 8px 24px rgba(47,143,91,.28);

  --r-sm: 8px; --r-md: 12px; --r-lg: 18px; --r-xl: 24px; --r-pill: 999px;

  --font: 'Pretendard', 'Pretendard Variable', -apple-system, BlinkMacSystemFont,
          'Apple SD Gothic Neo', 'Noto Sans KR', 'Malgun Gothic', system-ui, sans-serif;
  --font-display: 'Do Hyeon', 'Pretendard', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif;

  --maxw: 980px;
  --dur: .32s;
  --ease: cubic-bezier(.2,.8,.2,1);
  color-scheme: light;
}

:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f1a16; --bg-elev: #16231e; --bg-subtle: #1c2b25; --surface-2: #1a2822;
  --border: #27392f; --border-strong: #35493e;
  --text: #ecf1ea; --text-2: #b6c3b8; --text-3: #7f8e83; --ink: #ecf1ea;
  --brand: #4fb87f; --brand-strong: #3ea36b; --brand-soft: #173124;
  --accent: #f5b25a; --accent-soft: #3a2a12; --accent-ink: #f5d39a;
  --relevant: #ff7a54; --relevant-soft: #3a1f16; --relevant-border: #5a2e21;
  --sensitized: #9aa9bd; --sensitized-soft: #1f2732; --sensitized-border: #33404f;
  --indet: #f0a33a; --indet-soft: #3a2a12; --indet-border: #5a4320;
  --ok: #4fb87f; --ok-soft: #173124;
  --shadow-sm: 0 1px 2px rgba(0,0,0,.4); --shadow-md: 0 6px 16px rgba(0,0,0,.45); --shadow-lg: 0 18px 44px rgba(0,0,0,.6);
  --shadow-brand: 0 8px 24px rgba(79,184,127,.3);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #0f1a16; --bg-elev: #16231e; --bg-subtle: #1c2b25; --surface-2: #1a2822;
    --border: #27392f; --border-strong: #35493e;
    --text: #ecf1ea; --text-2: #b6c3b8; --text-3: #7f8e83; --ink: #ecf1ea;
    --brand: #4fb87f; --brand-strong: #3ea36b; --brand-soft: #173124;
    --accent: #f5b25a; --accent-soft: #3a2a12; --accent-ink: #f5d39a;
    --relevant: #ff7a54; --relevant-soft: #3a1f16; --relevant-border: #5a2e21;
    --sensitized: #9aa9bd; --sensitized-soft: #1f2732; --sensitized-border: #33404f;
    --indet: #f0a33a; --indet-soft: #3a2a12; --indet-border: #5a4320;
    --ok: #4fb87f; --ok-soft: #173124;
    --shadow-sm: 0 1px 2px rgba(0,0,0,.4); --shadow-md: 0 6px 16px rgba(0,0,0,.45); --shadow-lg: 0 18px 44px rgba(0,0,0,.6);
    --shadow-brand: 0 8px 24px rgba(79,184,127,.3);
  }
}
:root[data-theme="light"] { color-scheme: light; }

/* ---------- base ---------- */
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  font-family: var(--font); color: var(--text); background: var(--bg);
  background-image: radial-gradient(rgba(47,143,91,.06) 1px, transparent 1px);
  background-size: 22px 22px;
  line-height: 1.55; -webkit-font-smoothing: antialiased; font-size: 15px;
}
button { font-family: inherit; cursor: pointer; }
h1, h2, h3 { font-family: var(--font-display); font-weight: 400; letter-spacing: .2px; margin: 0; }
.hidden { display: none !important; }
main { max-width: var(--maxw); margin: 0 auto; padding: 18px 20px 96px; }

/* ---------- header + HUD ---------- */
.app-header { position: sticky; top: 0; z-index: 30; backdrop-filter: blur(10px);
  background: color-mix(in srgb, var(--bg-elev) 90%, transparent); border-bottom: 1px solid var(--border); }
.app-header .bar { max-width: var(--maxw); margin: 0 auto; padding: 10px 20px; display: flex; align-items: center; gap: 14px; }
.logo { display: flex; align-items: center; gap: 10px; font-family: var(--font-display); font-size: 20px; color: var(--text); line-height: 1.1; }
.logo .mark { font-size: 24px; display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px;
  background: var(--brand-soft); border: 1.5px dashed var(--brand); }
.logo small { display: block; font-family: var(--font); font-size: 11.5px; color: var(--text-3); font-weight: 500; margin-top: 2px; }
.hud { margin-left: auto; width: 240px; min-width: 150px; }
.hud-top { display: flex; justify-content: space-between; font-size: 12px; font-weight: 800; color: var(--text-2); }
.hud-title { color: var(--brand-strong); }
.hud-xp small { color: var(--text-3); font-weight: 600; }
.hud-bar { height: 8px; border-radius: var(--r-pill); background: var(--bg-subtle); border: 1px solid var(--border); overflow: hidden; margin-top: 4px; }
.hud-fill { height: 100%; width: 0; border-radius: inherit; transition: width .6s var(--ease);
  background: linear-gradient(90deg, var(--brand), var(--accent)); background-size: 200% 100%; animation: shimmer 2.4s linear infinite; }
.theme-toggle { width: 38px; height: 38px; border-radius: 12px; border: 1px solid var(--border); background: var(--bg-elev); color: var(--text-2); font-size: 16px; }
.theme-toggle:hover { background: var(--bg-subtle); }

/* ---------- 퀘스트 트레일 ---------- */
.trail { position: relative; margin: 8px 0 22px; padding: 6px 0 0; }
.tpath { position: absolute; left: 0; right: 0; top: 24px; width: 100%; height: 60px; overflow: visible; pointer-events: none; }
.tpath path { fill: none; stroke-width: 4; stroke-linecap: round; }
.tpath-bg { stroke: var(--border-strong); stroke-dasharray: 8 10; }
.tpath-fg { stroke: var(--brand); stroke-dasharray: 1; stroke-dashoffset: 1; pathLength: 1;
  stroke-dashoffset: calc(1 - var(--prog, 0)); transition: stroke-dashoffset .8s var(--ease); }
.tnodes { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); position: relative; }
.tnode { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--text-3); position: relative; }
.tnum { width: 42px; height: 42px; border-radius: 50%; display: grid; place-items: center; font-family: var(--font-display); font-size: 18px;
  background: var(--bg-elev); border: 2px solid var(--border-strong); color: var(--text-3); transition: transform var(--dur) var(--ease), background var(--dur), border-color var(--dur); }
.tlabel { font-size: 13px; font-weight: 800; color: var(--text-2); }
.tlabel small { display: block; font-size: 11px; font-weight: 500; color: var(--text-3); }
.tnode.done .tnum { background: var(--brand); border-color: var(--brand); color: #fff; }
.tnode.active .tnum { background: var(--accent); border-color: var(--accent); color: #2b1d05; transform: scale(1.12); box-shadow: 0 0 0 0 rgba(242,163,58,.55); animation: pulse 1.8s ease-out infinite; }
.tnode.active .tlabel { color: var(--text); }
.tnode.locked { opacity: .55; }
.tnode[role="button"] { cursor: pointer; }
.tnode[role="button"]:hover .tnum { transform: translateY(-2px); border-color: var(--brand); }
.tnode[role="button"]:focus-visible .tnum { outline: 3px solid var(--accent); outline-offset: 2px; }

/* ---------- 패널 ---------- */
.panel { background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--r-xl); padding: 28px; box-shadow: var(--shadow-md); position: relative; animation: rise var(--dur) var(--ease) both; }
.panel::before { content: ''; position: absolute; inset: 8px; border: 1.5px dashed var(--border); border-radius: calc(var(--r-xl) - 6px); pointer-events: none; }
.panel-head { margin-bottom: 20px; }
.eyebrow { display: inline-block; font-size: 11.5px; letter-spacing: 2px; font-weight: 800; color: var(--accent-ink); background: var(--accent-soft); padding: 4px 10px; border-radius: var(--r-pill); margin-bottom: 10px; }
.panel-head h1 { font-size: 30px; line-height: 1.2; color: var(--text); }
.panel-head p { margin: 10px 0 0; color: var(--text-2); font-size: 14.5px; }
.card { background: var(--bg-elev); border: 1px solid var(--border); border-radius: var(--r-lg); padding: 18px; }
.card.soft { background: var(--surface-2); }

/* ---------- 버튼 ---------- */
.btn { border: 1.5px solid transparent; border-radius: var(--r-pill); padding: 12px 20px; font-weight: 800; font-size: 14.5px;
  display: inline-flex; align-items: center; gap: 8px; transition: transform .12s var(--ease), box-shadow .2s, background .2s; }
.btn:active { transform: translateY(1px) scale(.99); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn.primary { background: var(--brand); color: var(--brand-contrast); box-shadow: var(--shadow-brand); }
.btn.primary:hover:not(:disabled) { background: var(--brand-strong); transform: translateY(-1px); }
.btn.secondary { background: var(--bg-elev); color: var(--text); border-color: var(--border-strong); }
.btn.secondary:hover { background: var(--bg-subtle); }
.btn.subtle { background: var(--bg-subtle); color: var(--text-2); }
.btn.sm { padding: 8px 14px; font-size: 13px; }
.btn.danger-ghost { background: transparent; color: var(--relevant); padding: 6px; border-radius: 8px; }
.actions { display: flex; align-items: center; gap: 10px; margin-top: 24px; }
.spacer { flex: 1; }
.spinner { width: 16px; height: 16px; border: 2.5px solid var(--brand-soft); border-top-color: var(--brand); border-radius: 50%; display: inline-block; animation: spin .8s linear infinite; vertical-align: middle; }

/* ---------- 업로드 ---------- */
.dropzone { border: 2px dashed var(--border-strong); border-radius: var(--r-lg); padding: 42px 20px; text-align: center; background: var(--surface-2); transition: .2s; cursor: pointer; position: relative; overflow: hidden; }
.dropzone:hover, .dropzone.drag { border-color: var(--brand); background: var(--brand-soft); }
.dropzone .icon { font-size: 44px; display: inline-grid; place-items: center; width: 84px; height: 84px; border-radius: 50%; background: var(--bg-elev); border: 2px solid var(--border); margin-bottom: 8px; animation: bob 3s ease-in-out infinite; }
.dropzone h3 { font-size: 20px; color: var(--text); }
.dropzone p { margin: 6px 0 0; color: var(--text-3); font-size: 13px; }
.preview-img { display: block; max-width: 100%; max-height: 320px; margin: 16px auto 0; border-radius: var(--r-md); border: 1px solid var(--border); }
.upload-alt { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
.notice { border-radius: var(--r-md); padding: 12px 14px; font-size: 13.5px; border: 1px solid var(--border); background: var(--bg-subtle); }
.notice.info { background: var(--brand-soft); border-color: var(--brand); color: var(--text); }
.notice.warn, .notice.flag { background: var(--indet-soft); border-color: var(--indet-border); color: var(--text); }

/* ---------- 폼 ---------- */
.field { margin-bottom: 18px; }
.field label { display: block; font-weight: 800; font-size: 13.5px; margin-bottom: 8px; color: var(--text); }
.field .hint, .hint { color: var(--text-3); font-weight: 500; font-size: 12px; }
.field-inline { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.input { width: 100%; padding: 11px 13px; border-radius: var(--r-md); border: 1.5px solid var(--border-strong); background: var(--bg-elev); color: var(--text); font-size: 14.5px; font-family: inherit; transition: .15s; }
.input:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip-opt { border: 1.5px solid var(--border-strong); background: var(--bg-elev); color: var(--text-2); border-radius: var(--r-pill); padding: 8px 14px; font-size: 13.5px; font-weight: 700; transition: .15s; }
.chip-opt:hover { border-color: var(--brand); color: var(--text); }
.chip-opt.sel { background: var(--brand); border-color: var(--brand); color: #fff; }

/* ---------- OCR 검토 표 ---------- */
.rv-tabs { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.rv-tab { border: 1.5px solid var(--border); background: var(--bg-elev); color: var(--text-2); border-radius: var(--r-pill); padding: 7px 12px; font-size: 13px; font-weight: 800; }
.rv-tab.active { background: var(--accent-soft); border-color: var(--accent); color: var(--accent-ink); }
.rv-count { display: inline-block; min-width: 20px; padding: 0 6px; margin-left: 4px; border-radius: var(--r-pill); background: var(--bg-subtle); font-size: 11px; text-align: center; }
.tbl-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--r-md); }
table.grid { width: 100%; border-collapse: collapse; font-size: 13.5px; min-width: 720px; }
table.grid th { text-align: left; font-size: 11.5px; letter-spacing: .5px; color: var(--text-3); padding: 10px 8px; background: var(--surface-2); border-bottom: 1px solid var(--border); }
table.grid td { padding: 6px 6px; border-bottom: 1px solid var(--border); }
table.grid tr:last-child td { border-bottom: none; }
table.grid input { width: 100%; border: 1px solid transparent; background: transparent; color: var(--text); padding: 7px 8px; border-radius: var(--r-sm); font-size: 14px; font-family: inherit; }
table.grid input:hover { background: var(--bg-subtle); }
table.grid input:focus { background: var(--bg-elev); border-color: var(--brand); outline: none; }
.pos-toggle { border: 1.5px solid var(--border-strong); border-radius: var(--r-pill); padding: 5px 10px; font-size: 12px; font-weight: 800; background: var(--bg-elev); color: var(--text-3); }
.pos-toggle.on { background: var(--relevant-soft); border-color: var(--relevant-border); color: var(--relevant); }
.tbl-toolbar { display: flex; align-items: center; gap: 10px; margin-top: 10px; }
.badge-count { display: inline-block; min-width: 22px; padding: 1px 8px; border-radius: var(--r-pill); background: var(--relevant); color: #fff; font-weight: 800; font-size: 12px; text-align: center; }
.pos-summary { margin-top: 14px; padding: 12px 14px; border-radius: var(--r-md); background: var(--accent-soft); border: 1px dashed var(--accent); color: var(--text); font-size: 13.5px; font-weight: 700; }

/* ---------- 문진(퀘스트 챕터) ---------- */
.chapter { border: 1px solid var(--border); border-radius: var(--r-lg); padding: 18px; margin-bottom: 16px; background: var(--bg-elev); position: relative; }
.ch-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.ring { --p: 0; width: 40px; height: 40px; border-radius: 50%; display: grid; place-items: center; font-size: 11px; font-weight: 800; color: var(--text-2);
  background: conic-gradient(var(--brand) calc(var(--p) * 1%), var(--bg-subtle) 0); transition: background .4s; flex: none; }
.ring::before { content: attr(data-label); width: 30px; height: 30px; border-radius: 50%; background: var(--bg-elev); display: grid; place-items: center; }
.ch-title { font-family: var(--font-display); font-size: 20px; color: var(--text); }
.q-sub { color: var(--text-2); font-size: 13.5px; margin: 0 0 10px 52px; }
.q-block { padding: 14px 16px; border-radius: var(--r-md); border: 1px solid var(--border); background: var(--surface-2); margin-top: 10px; transition: border-color .2s, box-shadow .2s; position: relative; }
.q-block.answered { border-color: var(--brand); box-shadow: inset 3px 0 0 var(--brand); }
.q-block.clue-new { animation: clue-in .5s var(--ease) both; border-color: var(--accent); }
.clue-tag { position: absolute; top: -10px; right: 12px; font-size: 11px; font-weight: 800; background: var(--accent); color: #2b1d05; padding: 2px 10px; border-radius: var(--r-pill); animation: pop .4s var(--ease) both; }
.q-title { font-weight: 800; font-size: 15px; color: var(--text); }
.q-help { color: var(--text-3); font-size: 12.5px; margin-top: 4px; line-height: 1.55; }
.q-applies { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.q-applies .tag { font-size: 11px; font-weight: 700; background: var(--bg-elev); color: var(--text-2); border: 1px solid var(--border); border-radius: var(--r-pill); padding: 3px 9px; }
.choice-row, .choice-list { display: flex; gap: 8px; margin-top: 10px; }
.choice-row { flex-wrap: wrap; }
.choice-list { flex-direction: column; }
.choice { display: flex; align-items: flex-start; gap: 10px; text-align: left; border: 1.5px solid var(--border-strong); border-radius: var(--r-md); background: var(--bg-elev); padding: 10px 12px; color: var(--text); transition: .15s; }
.choice-row .choice { flex: 1 1 140px; }
.choice:hover { border-color: var(--brand); }
.choice .radio { width: 18px; height: 18px; border-radius: 50%; border: 2px solid var(--border-strong); flex: none; margin-top: 2px; display: grid; place-items: center; }
.choice.sel { border-color: var(--brand); background: var(--brand-soft); }
.choice.sel .radio { border-color: var(--brand); background: var(--brand); }
.choice.sel .radio::after { content: '✓'; color: #fff; font-size: 11px; font-weight: 900; animation: pop .25s var(--ease); }
.choice .t { display: block; font-weight: 700; font-size: 14px; }
.choice .h { display: block; font-size: 12px; color: var(--text-3); margin-top: 2px; }
.quest-bar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 25; padding: 10px 20px 14px; background: color-mix(in srgb, var(--bg-elev) 92%, transparent); backdrop-filter: blur(10px); border-top: 1px solid var(--border); }
.qb-text { max-width: var(--maxw); margin: 0 auto 6px; font-size: 12.5px; color: var(--text-2); }
.qb-track { max-width: var(--maxw); margin: 0 auto; height: 8px; border-radius: var(--r-pill); background: var(--bg-subtle); overflow: hidden; }
.qb-fill { height: 100%; background: linear-gradient(90deg, var(--brand), var(--accent)); transition: width .5s var(--ease); }

/* ---------- 결과: 스코어보드·배지·도감 ---------- */
.scoreboard { border-radius: var(--r-xl); padding: 26px; color: #fff; position: relative; overflow: hidden;
  background: linear-gradient(135deg, #1f5f3f 0%, #2f8f5b 55%, #6fb98a 100%); box-shadow: var(--shadow-lg); animation: rise var(--dur) var(--ease) both; }
.scoreboard.calm { background: linear-gradient(135deg, #2b3a44 0%, #3f5563 100%); }
.scoreboard::after { content: ''; position: absolute; inset: 10px; border: 1.5px dashed rgba(255,255,255,.35); border-radius: calc(var(--r-xl) - 8px); pointer-events: none; }
.scoreboard .sb-eyebrow { font-size: 11.5px; letter-spacing: 2px; font-weight: 800; opacity: .85; }
.scoreboard h1 { font-size: 32px; margin-top: 6px; }
.scoreboard p { margin: 6px 0 0; opacity: .92; font-size: 14px; }
.trophies { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 18px; }
.trophy { background: rgba(255,255,255,.14); border-radius: var(--r-lg); padding: 12px; text-align: center; border: 1px solid rgba(255,255,255,.25); }
.trophy .n { font-family: var(--font-display); font-size: 34px; line-height: 1; }
.trophy .l { font-size: 12px; margin-top: 4px; opacity: .95; font-weight: 700; }
.trophy.relevant .n { color: #ffd6c8; } .trophy.sensitized .n { color: #e6ecf5; } .trophy.indet .n { color: #ffe1a8; }
.alert-severe { margin-top: 14px; border-radius: var(--r-md); padding: 12px 14px; background: var(--relevant-soft); border: 1.5px solid var(--relevant); color: var(--text); font-size: 13.5px; font-weight: 700; }
.badges { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 18px 0; }
.badge-item { display: flex; gap: 10px; align-items: center; border: 1.5px dashed var(--border-strong); border-radius: var(--r-lg); padding: 10px 12px; background: var(--bg-elev); opacity: .55; filter: grayscale(1); }
.badge-item.earned { opacity: 1; filter: none; border-style: solid; border-color: var(--accent); background: var(--accent-soft); animation: pop .5s var(--ease) both; animation-delay: calc(var(--i, 0) * 90ms); }
.b-icon { font-size: 24px; }
.b-name { font-weight: 800; font-size: 13px; color: var(--text); }
.b-desc { font-size: 11.5px; color: var(--text-3); line-height: 1.4; }
.result-tabs { display: flex; gap: 6px; margin: 18px 0 14px; flex-wrap: wrap; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.result-tabs button { border: none; background: transparent; color: var(--text-2); font-weight: 800; padding: 8px 14px; border-radius: var(--r-pill); font-size: 14px; }
.result-tabs button.active { background: var(--brand); color: #fff; }
.filter-chips { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 14px; }
.dex-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 14px; }
.dex-card { perspective: 1200px; cursor: pointer; min-height: 250px; animation: rise .4s var(--ease) both; animation-delay: calc(var(--i, 0) * 60ms); }
.dex-card:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; border-radius: var(--r-lg); }
.dex-inner { position: relative; width: 100%; height: 100%; min-height: 250px; transform-style: preserve-3d; transition: transform .6s var(--ease); }
.dex-card.flip .dex-inner { transform: rotateY(180deg); }
.face { position: absolute; inset: 0; backface-visibility: hidden; border-radius: var(--r-lg); border: 1.5px solid var(--border); background: var(--bg-elev); padding: 16px; display: flex; flex-direction: column; box-shadow: var(--shadow-sm); }
.face.back { transform: rotateY(180deg); overflow: auto; font-size: 13px; }
.face.front .stamp { width: 64px; height: 64px; border-radius: 50%; display: grid; place-items: center; background: var(--surface-2); border: 2px solid var(--border-strong); color: var(--brand-strong); margin-bottom: 10px; }
.face.front .stamp svg { width: 40px; height: 40px; }
.dex-name { font-family: var(--font-display); font-size: 21px; color: var(--text); }
.dex-meta { font-size: 12px; color: var(--text-3); margin-top: 2px; }
.stars { color: var(--accent); font-size: 15px; letter-spacing: 2px; margin-top: 6px; display: inline-block; }
.stars i { color: var(--border-strong); font-style: normal; }
.verdict-stamp { margin-top: auto; align-self: flex-start; font-family: var(--font-display); font-size: 15px; padding: 5px 12px; border: 2.5px solid currentColor; border-radius: 8px; transform: rotate(-4deg); letter-spacing: 1px; animation: stamp .45s var(--ease) both; animation-delay: calc(var(--i, 0) * 60ms + .25s); }
.tone-relevant { color: var(--relevant); } .tone-sensitized { color: var(--sensitized); } .tone-indet { color: var(--indet); } .tone-na { color: var(--text-3); }
.dex-note { font-size: 11.5px; color: var(--text-3); margin-top: 6px; }
.dex-flip-hint { position: absolute; right: 12px; bottom: 10px; font-size: 11px; color: var(--text-3); }
.face.back .rationale { font-weight: 700; color: var(--text); margin-bottom: 8px; line-height: 1.5; }
.kb-grid { display: grid; gap: 8px; }
.kb-item .k { font-size: 11px; font-weight: 800; color: var(--accent-ink); }
.kb-item .v, .kb-item ul { color: var(--text-2); margin: 2px 0 0; padding-left: 16px; }
.kb-item .v { padding-left: 0; }
.src-tag { color: var(--text-3); }
.rel-badge { display: inline-block; font-size: 11px; font-weight: 800; padding: 3px 9px; border-radius: var(--r-pill); border: 1px solid; }
.rel-badge.clinically_relevant { background: var(--relevant-soft); color: var(--relevant); border-color: var(--relevant-border); }
.rel-badge.sensitized_only { background: var(--sensitized-soft); color: var(--sensitized); border-color: var(--sensitized-border); }
.rel-badge.indeterminate { background: var(--indet-soft); color: var(--indet); border-color: var(--indet-border); }
.rel-badge.not_assessed { background: var(--bg-subtle); color: var(--text-3); border-color: var(--border); }
.report-frame, .cardnews-frame { width: 100%; border: 1px solid var(--border); border-radius: var(--r-lg); background: #fff; }
.report-frame { height: 720px; } .cardnews-frame { height: 520px; }
.download-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }

/* ---------- 발견 오버레이 ---------- */
.overlay { position: fixed; inset: 0; z-index: 50; display: grid; place-items: center; padding: 20px; background: rgba(20,30,24,.62); backdrop-filter: blur(6px); animation: fade .25s both; }
.discover { background: var(--bg-elev); border-radius: var(--r-xl); padding: 28px; max-width: 760px; width: 100%; text-align: center; border: 1px solid var(--border); box-shadow: var(--shadow-lg); position: relative; overflow: hidden; }
.d-eyebrow { display: inline-block; font-family: var(--font-display); font-size: 22px; color: var(--accent-ink); background: var(--accent-soft); padding: 2px 14px; border-radius: var(--r-pill); animation: pop .5s var(--ease) both; }
.discover h2 { font-size: 26px; margin-top: 10px; color: var(--text); }
.discover p { color: var(--text-2); font-size: 14px; margin: 6px 0 16px; }
.discover-deck { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-bottom: 20px; max-height: 46vh; overflow: auto; padding: 4px; }
.dcard { width: 112px; padding: 12px 8px; border-radius: var(--r-md); border: 1.5px solid var(--border); background: var(--surface-2); position: relative;
  animation: flip-in .55s var(--ease) both; animation-delay: calc(var(--i) * 110ms); }
.dcard.more { display: grid; place-items: center; font-family: var(--font-display); font-size: 20px; color: var(--text-2); }
.dstamp { width: 44px; height: 44px; margin: 0 auto 6px; border-radius: 50%; display: grid; place-items: center; background: var(--bg-elev); border: 2px solid var(--border-strong); color: var(--brand-strong); }
.dstamp svg { width: 28px; height: 28px; }
.dname { font-size: 12.5px; font-weight: 800; color: var(--text); line-height: 1.3; }
.dq { position: absolute; top: 6px; right: 8px; font-family: var(--font-display); color: var(--text-3); font-size: 14px; }

/* ---------- 연출 요소 ---------- */
.xp-float { position: fixed; z-index: 60; transform: translate(-50%, 0); pointer-events: none; font-weight: 900; font-size: 14px; color: var(--accent-ink);
  background: var(--accent-soft); border: 1px solid var(--accent); padding: 2px 10px; border-radius: var(--r-pill); animation: float-up 1s var(--ease) both; }
.sparkle { position: absolute; bottom: 30%; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); pointer-events: none; animation: sparkle .9s var(--ease) both; }
.toast { position: fixed; left: 50%; bottom: 84px; transform: translate(-50%, 20px); opacity: 0; background: var(--ink); color: #fff; padding: 10px 16px; border-radius: var(--r-pill); font-size: 13.5px; z-index: 70; transition: .25s; pointer-events: none; }
:root[data-theme="dark"] .toast { background: #ecf1ea; color: #0f1a16; }
.toast.show { opacity: 1; transform: translate(-50%, 0); }
.disclaimer { color: var(--text-3); font-size: 12px; text-align: center; margin-top: 26px; }

@keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
@keyframes pop { 0% { transform: scale(.6); opacity: 0; } 70% { transform: scale(1.08); opacity: 1; } 100% { transform: scale(1); } }
@keyframes stamp { 0% { transform: rotate(-4deg) scale(1.6); opacity: 0; } 60% { transform: rotate(-4deg) scale(.96); opacity: 1; } 100% { transform: rotate(-4deg) scale(1); } }
@keyframes flip-in { 0% { transform: rotateY(90deg) translateY(10px); opacity: 0; } 100% { transform: none; opacity: 1; } }
@keyframes clue-in { from { transform: translateX(-12px); opacity: 0; } to { transform: none; opacity: 1; } }
@keyframes float-up { 0% { opacity: 0; transform: translate(-50%, 6px); } 20% { opacity: 1; } 100% { opacity: 0; transform: translate(-50%, -36px); } }
@keyframes sparkle { 0% { transform: translate(0,0) scale(1); opacity: 1; } 100% { transform: translate(var(--dx), var(--dy)) scale(0); opacity: 0; } }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(242,163,58,.55); } 100% { box-shadow: 0 0 0 14px rgba(242,163,58,0); } }
@keyframes bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-5px); } }
@keyframes shimmer { from { background-position: 0 0; } to { background-position: 200% 0; } }
@keyframes spin { to { transform: rotate(360deg); } }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
  .dex-card.flip .dex-inner { transform: rotateY(180deg); }
}

/* ---------- 모바일 ---------- */
@media (max-width: 720px) {
  main { padding: 12px 14px 110px; }
  .logo small, .tlabel small { display: none; }
  .hud { width: 130px; }
  .hud-top { font-size: 11px; }
  .panel { padding: 18px; }
  .panel-head h1 { font-size: 24px; }
  .grid-3 { grid-template-columns: 1fr; }
  .tlabel { font-size: 11px; }
  .tnum { width: 34px; height: 34px; font-size: 15px; }
  .trophies { gap: 6px; } .trophy .n { font-size: 26px; }
  .choice-row { flex-direction: column; } .choice-row .choice { flex: 1 1 auto; }
  .choice { padding: 14px; min-height: 52px; }
  body[data-appstep="2"] .actions, body[data-appstep="3"] .actions { position: sticky; bottom: 70px; background: var(--bg-elev); padding: 10px; border-radius: var(--r-lg); box-shadow: var(--shadow-md); z-index: 20; }
  .input, .choice, .chip-opt { font-size: 16px; }
  .dex-grid { grid-template-columns: 1fr 1fr; }
}
```

- [ ] **Step 3: 시각 확인**

```bash
cd /Users/mingyukang/Claude/Projects/APAAACI_mast_to_HL7FHIR
(uvicorn server:app --port 8765 >/tmp/uv.log 2>&1 &) ; sleep 2
B=~/.claude/skills/gstack/browse/dist/browse
$B goto http://127.0.0.1:8765/ && $B wait '#stepper' && $B screenshot /tmp/quest-step0.png
$B css body font-family
```
Expected: 페이지 로드, 스크린샷 생성. (트레일은 Task 3 전까지 기존 `.step` 마크업이어서 비어 보일 수 있음 — 정상. 콘솔 오류 없음: `$B console --errors` 출력 비어 있음.)

- [ ] **Step 4: 커밋**

```bash
git add web/index.html web/styles.css
git commit -m "feat(ui): 알러젠 탐험 퀘스트 디자인 시스템 — 크림/숲녹색/호박 토큰, 트레일·HUD·도감·오버레이 스타일

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN"
```

---

### Task 3: app.js 통합 — 트레일·HUD·발견·챕터·도감

**Files:**
- Modify: `web/app.js` (상수 `web/app.js:30-40`, `goto/renderStepper/render` `web/app.js:98-122`, `renderUpload` 문구, `renderReview` 문구·next·input 훅, `renderScreening` 문구, `renderQuestionnaire` 전체, `submitClassify`, `renderResults`, `renderResultTab('allergens')`, `cardForAllergen`)

**Interfaces:**
- Consumes: Task 1 `Game.*`, Task 2 클래스.
- Produces: 새 함수 `confirmDiscovery()`, `dexCard(a, i)`; 전역 `STEP_SUB`, `DEX_FILTER`.

- [ ] **Step 1: 상수·상태 수정**

`web/app.js:30-40` 을 다음으로 교체:
```js
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
const STEPS = ['흔적 수집', '증거 확인', '탐험가 프로필', '진범 감별', '도감 완성'];
const STEP_SUB = ['검사지 업로드', 'OCR 검토', '스크리닝', '증상 감별 문진', '결과 리포트'];
```
(`CAT_EMOJI`, `REL_LABEL`, `CAT_LABEL` 은 그대로 둔다.)

- [ ] **Step 2: goto / renderStepper / render 교체**

`web/app.js:98-122` 를 다음으로 교체:
```js
function goto(step) {
  // 앞으로 전진할 때 현재 단계 완료 XP(행동 기반, 1회)
  if (step > S.step) { const g = Game.completeStep(S.game, S.step); if (g) Game.ui.floatXp(null, g); }
  S.step = step; S.maxReached = Math.max(S.maxReached, step);
  window.scrollTo({ top: 0, behavior: 'smooth' });
  render();
}
function renderStepper() {
  Game.ui.renderTrail($('#stepper'), STEPS, STEP_SUB, S.step, S.maxReached, goto);
}
function render() {
  renderStepper();
  Game.ui.renderHud($('#hud'), S.game);
  $('#questBar').classList.toggle('hidden', S.step !== 3);
  document.body.setAttribute('data-appstep', S.step);  // 모바일 전용 UI(스크리닝=2/문진=3) 스코프용
  [renderUpload, renderReview, renderScreening, renderQuestionnaire, renderResults][S.step]();
}
```

- [ ] **Step 3: Step 0 업로드 문구**

`renderUpload()` 내 `panel-head` 와 dropzone·버튼 문구를 교체:
```js
      <div class="panel-head">
        <div class="eyebrow">QUEST 1 · 흔적 수집</div>
        <h1>검사 결과지를 가져오면 탐험이 시작됩니다</h1>
        <p>피부반응검사(SPT), MAST, UniCAP(ImmunoCAP) 결과지를 지원합니다. 사진이나 스캔 이미지를 올리면 양성 항목을 자동으로 읽어 <b>흔적</b>으로 등록합니다.</p>
      </div>
      <div class="dropzone" id="dz">
        <div class="icon">🗂️</div>
        <h3>여기로 이미지를 끌어다 놓거나 클릭해서 선택</h3>
        <p>JPG · PNG · 10MB 이하 ${hasKey ? '' : '· (OCR을 쓰려면 서버에 OpenAI API 키가 필요합니다)'}</p>
        <input type="file" id="file" accept="image/*" class="hidden" />
      </div>
```
`upload-alt` 버튼 문구: `✨ 연습 탐험 시작 (데모 데이터)`, `⌨️ 결과를 직접 입력하기`.

- [ ] **Step 4: Step 1 OCR 검토 — 문구·발견 훅·편집 훅**

`renderReview()` 의 `panel-head` 교체:
```js
      <div class="panel-head">
        <div class="eyebrow">QUEST 2 · 증거 확인</div>
        <h1>읽어온 흔적을 확인·수정하세요</h1>
        <p>잘못 읽힌 값은 표에서 직접 고치고, <b>누락된 알러젠은 ‘＋ 항목 추가’</b>로 넣을 수 있습니다. 수치를 고치면 Class·판정이 자동으로 바뀝니다. 양성 항목이 <b>도감에 등록</b>됩니다.</p>
      </div>
```
`pos-summary` 문구(2곳: `refreshReviewSummary` 와 `renderReview` 초기 렌더): `🧭 양성 흔적 ${n}개 발견 — 다음 퀘스트에서 이 항목들의 진범 여부를 가려냅니다.`
`next` 버튼 문구: `발견 등록 →`.
`$('#tbody').addEventListener('input', ...)` 핸들러 첫 줄 뒤(`if (f == null) return;` 다음)에 추가: `Game.noteEdit(S.game);`
`$('#next').addEventListener('click', () => goto(2));` → `$('#next').addEventListener('click', confirmDiscovery);`
`renderReview` 함수 바로 뒤에 추가:
```js
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
// OCR 행에 category 가 없을 때 스탬프용 대략 분류 (guessCategory 는 screening 용 5분류이므로 별도)
function guessDexCategory(r) {
  const c = guessCategory(r.allergen_name, r.korean_name);
  if (c === 'pollen') return /(tree|birch|oak|alder|자작|참나무|오리나무|나무)/i.test(`${r.allergen_name} ${r.korean_name}`) ? 'pollen_tree'
    : /(weed|ragweed|mugwort|hop|돼지풀|쑥|환삼|잡초)/i.test(`${r.allergen_name} ${r.korean_name}`) ? 'pollen_weed' : 'pollen_grass';
  return ['mite', 'animal', 'mold', 'food'].includes(c) ? c : 'other';
}
```
(`guessCategory` 는 기존 함수 `web/app.js:377` — 반환값 mite/pollen/mold/animal/food/other 를 확인하고 없는 분기가 있으면 위 매핑에 맞춘다.)

- [ ] **Step 5: Step 2 스크리닝 문구**

`renderScreening()` `panel-head`:
```js
      <div class="panel-head">
        <div class="eyebrow">QUEST 3 · 탐험가 프로필</div>
        <h1>탐험가 프로필을 작성해 주세요</h1>
        <p>기저 알레르기 질환과 복용 약제, 증상이 나타나는 부위를 확인합니다. 이 정보로 다음 퀘스트의 감별 정확도가 올라갑니다.</p>
      </div>
```
배너 첫 줄 문구: `🧭 도감에 등록된 양성 흔적 ${ctx.pos.length}개`. `next` 버튼: `진범 감별 퀘스트로 →`. `submitScreening` 실패 시 복구 문구도 동일하게 교체.

- [ ] **Step 6: Step 3 문진 — 챕터·진행바·XP·새 단서**

`renderQuestionnaire()` 전체를 다음으로 교체(기존 `refreshFoodGeneralOptions`·`condMet` 로직은 그대로 포함):
```js
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
        qq.options.map(o => `<button type="button" class="chip-opt ${(val || []).includes(o.value) ? 'sel' : ''}" data-v="${o.value}">${esc(o.label)}</button>`).join('') + `</div>`;
    } else {
      const rowClass = qq.options.length <= 3 && qq.options.every(o => o.label.length <= 12) ? 'choice-row' : 'choice-list';
      control = `<div class="${rowClass}" data-single="${qq.id}">` +
        qq.options.map(o => `<button type="button" class="choice ${val === o.value ? 'sel' : ''}" data-v="${o.value}">
          <span class="radio"></span><span class="body"><span class="t">${esc(o.label)}</span>${o.hint ? `<span class="h">${esc(o.hint)}</span>` : ''}</span></button>`).join('') + `</div>`;
    }
    const cond = qCond(qq);
    let revealAttr = '', hiddenCls = '';
    if (cond) { revealAttr = ` data-reveal='${esc(JSON.stringify(cond))}'`; if (!condMet(cond)) hiddenCls = ' hidden'; }
    return `<div class="q-block${hiddenCls}${Game.hasAnswer(val) ? ' answered' : ''}" data-qid="${qq.id}"${revealAttr}>
      <div class="q-title">${esc(qq.title)}</div>
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
      const t = document.createElement('span'); t.className = 'clue-tag'; t.textContent = '🔎 새 단서'; b.appendChild(t);
      setTimeout(() => { t.remove(); b.classList.remove('clue-new'); }, 3000);
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

  // 챕터 링·하단 진행바 갱신 + 챕터 완료 XP
  const refreshProgress = () => {
    let totA = 0, totV = 0;
    q.sections.forEach((sec, si) => {
      const p = Game.chapterProgress(sec, S.answers, isVisible);
      totA += p.answered; totV += p.visible;
      const ring = view().querySelector(`.chapter[data-si="${si}"] .ring`);
      if (ring) { ring.style.setProperty('--p', p.visible ? Math.round(p.answered / p.visible * 100) : 0); ring.dataset.label = `${p.answered}/${p.visible}`; }
      if (p.done) { const g = Game.completeChapter(S.game, sec.id || `sec${si}`); if (g) { Game.ui.floatXp(ring, g); Game.ui.sparkle(ring && ring.closest('.chapter')); } }
    });
    Game.ui.renderQuestBar($('#questBar'), totA, totV);
    Game.ui.renderHud($('#hud'), S.game);
  };

  const sections = q.sections.map((sec, si) => `
    <div class="chapter" data-si="${si}">
      <div class="ch-head"><div class="ring" data-label="0/0" style="--p:0"></div><div class="ch-title">${esc(sec.title)}</div></div>
      ${sec.subtitle ? `<div class="q-sub">${esc(sec.subtitle)}</div>` : ''}
      ${sec.questions.map(questionHtml).join('')}
    </div>`).join('');

  view().innerHTML = `
    <div class="panel">
      <div class="panel-head">
        <div class="eyebrow">QUEST 4 · 진범 감별</div>
        <h1>흔적 중 진짜 범인을 가려냅니다</h1>
        <p>검사 양성이 <b>실제 알레르기</b>인지 <b>감작(양성이지만 증상 없음)</b>인지 가리는 핵심 퀘스트입니다. 아는 만큼만 답하시고, 모르면 ‘잘 모르겠어요’를 선택하세요 — 그것도 소중한 단서입니다.</p>
      </div>
      ${sections}
      <div class="actions">
        <button class="btn secondary" id="back">← 이전</button>
        <span class="spacer"></span>
        <button class="btn primary" id="next">도감 완성하기 →</button>
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
    g.querySelectorAll('.choice').forEach(c => c.classList.toggle('sel', c === b));
    onAnswered(g.closest('.q-block'), b, g.dataset.single);
  }));
  view().querySelectorAll('[data-multi]').forEach(g => g.addEventListener('click', e => {
    const b = e.target.closest('.chip-opt'); if (!b) return;
    const id = g.dataset.multi, v = b.dataset.v; let arr = S.answers[id] || [];
    if (v === 'none' || v === 'no') arr = arr.includes(v) ? [] : [v];
    else { arr = arr.filter(x => x !== 'none' && x !== 'no'); arr = arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]; }
    S.answers[id] = arr;
    g.querySelectorAll('.chip-opt').forEach(c => c.classList.toggle('sel', arr.includes(c.dataset.v)));
    onAnswered(g.closest('.q-block'), b, id);
  }));
  // 초기 상태: 이미 보이는 문항은 '새 단서' 연출 대상에서 제외
  view().querySelectorAll('[data-reveal]:not(.hidden)').forEach(b => { b.dataset.seen = '1'; });
  refreshFoodGeneralOptions(); refreshProgress();
  $('#back').addEventListener('click', () => goto(2));
  $('#next').addEventListener('click', submitClassify);
}
```
`submitClassify()` 의 `S.classify = await API.post(...)` 다음 줄에 추가: `Game.applyResults(S.game, S.classify, S.answers);` (goto(4) 이전). 버튼 로딩 문구: `도감 정리 중…`.

- [ ] **Step 7: Step 4 결과 — 스코어보드·배지·도감**

`renderResults()` 를 다음으로 교체:
```js
function renderResults() {
  const c = S.classify; const cnt = c.summary.counts; const p = S.ocr.patient; const g = S.game;
  const lvl = Game.levelFor(g.xp);
  const severe = g.severeFlag;
  view().innerHTML = `
    <div class="scoreboard ${severe ? 'calm' : ''}" id="scoreboard">
      <div class="sb-eyebrow">QUEST 5 · 도감 완성</div>
      <h1>${esc(p.name || '탐험가')}님의 알러젠 도감${severe ? '' : '이 완성되었습니다'}</h1>
      <p>검사일 ${esc(p.test_date || '-')} · 양성 흔적 ${c.summary.total_positive}개를 증상과 대조해 진범을 가렸습니다. · Lv.${lvl.index + 1} ${esc(lvl.title)} · ${g.xp} XP</p>
      <div class="trophies">
        <div class="trophy relevant"><div class="n">${cnt.clinically_relevant}</div><div class="l">🔴 진범 확정</div></div>
        <div class="trophy sensitized"><div class="n">${cnt.sensitized_only}</div><div class="l">⚪ 무혐의 · 감작만</div></div>
        <div class="trophy indet"><div class="n">${cnt.indeterminate}</div><div class="l">🟡 관찰 대상</div></div>
      </div>
      ${severe ? `<div class="alert-severe">🚨 중증(전신·아나필락시스) 반응 이력이 확인되었습니다. 이 결과는 참고용이며, 응급 대처 계획과 치료는 반드시 담당 의료진과 상의하세요.</div>` : ''}
    </div>

    <div class="badges">${Game.BADGES.map((b, i) => `<div class="badge-item ${g.badges.includes(b.id) ? 'earned' : ''}" style="--i:${i}" title="${esc(b.desc)}">
        <span class="b-icon">${b.icon}</span><span><div class="b-name">${esc(b.name)}</div><div class="b-desc">${esc(b.desc)}</div></span></div>`).join('')}</div>

    <div class="result-tabs">
      <button data-tab="allergens" class="${RESULT_TAB === 'allergens' ? 'active' : ''}">📖 알러젠 도감</button>
      <button data-tab="report" class="${RESULT_TAB === 'report' ? 'active' : ''}">맞춤 리포트</button>
      <button data-tab="cardnews" class="${RESULT_TAB === 'cardnews' ? 'active' : ''}">카드뉴스</button>
      <button data-tab="fhir" class="${RESULT_TAB === 'fhir' ? 'active' : ''}">FHIR 내보내기</button>
    </div>
    <div id="tabBody"></div>

    <div class="actions">
      <button class="btn secondary" id="back">← 문진 수정</button>
      <span class="spacer"></span>
      <button class="btn secondary" id="restart">새 탐험 시작</button>
    </div>`;

  if (!severe) Game.ui.sparkle($('#scoreboard'));
  view().querySelectorAll('.result-tabs button').forEach(b => b.addEventListener('click', () => { RESULT_TAB = b.dataset.tab; renderResults(); }));
  $('#back').addEventListener('click', () => goto(3));
  $('#restart').addEventListener('click', () => { S.ocr = null; S.screening = null; S.questionnaire = null; S.answers = {}; S.classify = null; S.maxReached = 0; S.game = Game.createState(); DEX_FILTER = 'all'; goto(0); });
  renderResultTab();
}
```
`renderResultTab()` 의 `if (RESULT_TAB === 'allergens') {...}` 블록을 다음으로 교체:
```js
  if (RESULT_TAB === 'allergens') {
    const sorted = [...c.assessments].sort((a, b) => ORDER[a.relevance] - ORDER[b.relevance]);
    const filtered = DEX_FILTER === 'all' ? sorted : sorted.filter(a => a.relevance === DEX_FILTER);
    const cntOf = (k) => sorted.filter(a => a.relevance === k).length;
    body.innerHTML = `<div class="filter-chips">
        ${[['all', `전체 ${sorted.length}`], ['clinically_relevant', `🔴 진범 확정 ${cntOf('clinically_relevant')}`], ['indeterminate', `🟡 관찰 대상 ${cntOf('indeterminate')}`], ['sensitized_only', `⚪ 무혐의 ${cntOf('sensitized_only')}`]]
          .map(([k, l]) => `<button type="button" class="chip-opt ${DEX_FILTER === k ? 'sel' : ''}" data-f="${k}">${l}</button>`).join('')}
      </div>
      <div class="dex-grid">${filtered.map(dexCard).join('') || '<p class="q-help">해당 판정의 알러젠이 없습니다.</p>'}</div>`;
    body.querySelectorAll('.filter-chips .chip-opt').forEach(b => b.addEventListener('click', () => { DEX_FILTER = b.dataset.f; renderResultTab(); }));
    body.querySelectorAll('.dex-card').forEach(card => {
      const flip = () => card.classList.toggle('flip');
      card.addEventListener('click', flip);
      card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); } });
    });
  } else if (RESULT_TAB === 'report') {
```
`cardForAllergen(a)` 함수를 `dexCard(a, i)` 로 교체:
```js
function dexCard(a, i) {
  const v = Game.VERDICT[a.relevance] || Game.VERDICT.not_assessed;
  const d = S.game.discovered[a.allergen_name];
  const stars = d ? d.stars : ({ weak: 1, moderate: 2, strong: 3 }[a.strength] || 1);
  const hasOas = (a.oas_foods && a.oas_foods.length);
  const kb = [];
  if (a.season_label_ko) kb.push(['시즌', esc(a.season_label_ko)]);
  if (hasOas) kb.push(['🍎 구강알레르기증후군(OAS) 유발 음식', esc(a.oas_foods.join(', ')) + ' — 생것 섭취 시 입·목 증상 주의, 대개 익히면 완화']);
  if (a.crossreact_confirmed && a.crossreact_confirmed.length) kb.push(['🕵️ 증상으로 확인된 교차반응 음식', esc(a.crossreact_confirmed.join(', '))]);
  if (a.biology_ko) kb.push(['특성·생활사', esc(a.biology_ko)]);
  if (a.exposure_environment_ko) kb.push(['주요 노출 환경', esc(a.exposure_environment_ko)]);
  if (a.cross_reactivity_ko) kb.push(['교차반응', esc(a.cross_reactivity_ko)]);
  const av = (a.avoidance_control_ko || []).slice(0, 5);
  const kbHtml = kb.map(([k, val]) => `<div class="kb-item"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('') +
    (av.length ? `<div class="kb-item"><div class="k">회피·관리 수칙</div><ul>${av.map(t => `<li>${esc(t)}</li>`).join('')}</ul></div>` : '');
  const srcTag = a.source && a.source !== 'knowledge_base' ? `<span class="src-tag"> · 출처: ${a.source === 'wikipedia' ? 'Wikipedia' : '기본값'}</span>` : '';
  const sev = a.severity && a.severity !== 'none' && a.severity !== 'mild' ? ` · 중증도 ${({ moderate: '중등증', severe: '중증', anaphylaxis: '아나필락시스' })[a.severity] || esc(a.severity)}` : '';
  return `<div class="dex-card" style="--i:${i}" tabindex="0" role="button" aria-label="${esc(a.korean_name || a.allergen_name)} 도감 카드, 클릭하면 뒤집기">
    <div class="dex-inner">
      <div class="face front">
        <div class="stamp tone-${v.tone}">${Game.stampSvg(a.category)}</div>
        <div class="dex-name">${esc(a.korean_name || a.allergen_name)}</div>
        <div class="dex-meta">${CAT_LABEL[a.category] || a.category} · ${a.test_value ?? '-'}${a.test_unit ? ' ' + esc(a.test_unit) : ''}${a.class_value != null ? ` · class ${esc(a.class_value)}` : ''}${sev}</div>
        ${Game.starsHtml(stars)}${hasOas ? `<div class="dex-meta">🍎 OAS 교차반응 있음</div>` : ''}
        <div class="verdict-stamp tone-${v.tone}">${esc(v.stamp)}</div>
        ${v.note ? `<div class="dex-note">${esc(v.note)}</div>` : ''}
        <span class="dex-flip-hint">↻ 근거 보기</span>
      </div>
      <div class="face back">
        <div class="rationale">${esc(a.rationale_ko || '')}${srcTag}</div>
        <div class="kb-grid">${kbHtml}</div>
      </div>
    </div>
  </div>`;
}
```

- [ ] **Step 8: 브라우저 E2E 스모크**

`scripts/ui_smoke.sh` 생성:
```bash
#!/usr/bin/env bash
# 알러젠 탐험 퀘스트 UI 스모크 — gstack browse 로 데모 흐름을 끝까지 돌린다.
set -euo pipefail
B=${B:-$HOME/.claude/skills/gstack/browse/dist/browse}
URL=${URL:-http://127.0.0.1:8765}
OUT=${OUT:-/tmp/quest-smoke}; mkdir -p "$OUT"
fail() { echo "✗ $1"; exit 1; }
ok() { echo "✓ $1"; }

$B goto "$URL/" >/dev/null; $B wait '#stepper .tnode' >/dev/null
[ "$($B js "document.querySelectorAll('#stepper .tnode').length")" = "5" ] || fail "트레일 노드 5개"
[ "$($B js "document.querySelector('#hud .hud-title').textContent")" = "Lv.1 새싹 탐험가" ] || fail "HUD 초기 레벨"
ok "Step0 트레일·HUD"
$B screenshot "$OUT/00-upload.png" >/dev/null

$B click '#btnDemo' >/dev/null; $B wait '#next' >/dev/null
[ "$($B js "document.querySelector('#hud .hud-xp').textContent.startsWith('50 XP')")" = "true" ] || fail "Step0 완료 XP 50"
ok "Step1 OCR 검토 진입 + XP"
$B screenshot "$OUT/01-review.png" >/dev/null

$B click '#next' >/dev/null; $B wait '#overlay .dcard' >/dev/null
[ "$($B js "document.querySelectorAll('#overlay .dcard').length > 0")" = "true" ] || fail "발견 오버레이 카드"
ok "발견 오버레이"
$B screenshot "$OUT/02-discover.png" >/dev/null
$B click '#dGo' >/dev/null; $B wait '#pName' >/dev/null
ok "Step2 스크리닝 진입"

$B click '#next' >/dev/null; $B wait '.chapter' >/dev/null
[ "$($B is visible '#questBar')" = "true" ] || fail "하단 퀘스트바 표시"
XP0=$($B js "parseInt(document.querySelector('#hud .hud-xp').textContent)")
$B js "document.querySelector('.q-block:not(.hidden) .choice, .q-block:not(.hidden) .chip-opt').click()" >/dev/null
sleep 0.3
XP1=$($B js "parseInt(document.querySelector('#hud .hud-xp').textContent)")
[ "$XP1" -gt "$XP0" ] || fail "답변 시 XP 증가 ($XP0 → $XP1)"
[ "$($B js "document.querySelectorAll('.q-block.answered').length >= 1")" = "true" ] || fail "answered 표시"
ok "Step3 문진 XP·챕터"
$B screenshot "$OUT/03-quest.png" >/dev/null

# 모든 보이는 단일선택 문항에 첫 선택지로 답해 챕터 완료·결과 진행
$B js "(function(){ let n=0; for (let k=0;k<6;k++){ document.querySelectorAll('.q-block:not(.hidden) [data-single]').forEach(g=>{ if(!g.querySelector('.choice.sel')) { g.querySelector('.choice').click(); n++; } }); } return n; })()" >/dev/null
$B click '#next' >/dev/null; $B wait '#scoreboard' >/dev/null
[ "$($B js "document.querySelectorAll('.dex-card').length > 0")" = "true" ] || fail "도감 카드"
[ "$($B js "document.querySelectorAll('.badge-item.earned').length >= 1")" = "true" ] || fail "배지 1개 이상"
[ "$($B js "!!document.querySelector('.verdict-stamp')")" = "true" ] || fail "판정 도장"
$B js "document.querySelector('.dex-card').click()" >/dev/null
[ "$($B js "document.querySelector('.dex-card').classList.contains('flip')")" = "true" ] || fail "도감 카드 뒤집기"
ok "Step4 스코어보드·도감·배지"
$B screenshot "$OUT/04-dex.png" >/dev/null

$B js "document.querySelector('.result-tabs [data-tab=cardnews]').click()" >/dev/null; $B wait '#cn' >/dev/null; sleep 0.5
[ "$($B js "document.querySelector('#cn').contentDocument.querySelector('.stamp-verdict') !== null")" = "true" ] || fail "카드뉴스 도장 요소"
ok "카드뉴스 iframe 도장"
$B screenshot "$OUT/05-cardnews.png" >/dev/null

[ -z "$($B console --errors)" ] || { $B console --errors; fail "콘솔 오류"; }
echo "ALL SMOKE PASSED — screenshots in $OUT"
```
```bash
chmod +x scripts/ui_smoke.sh
(uvicorn server:app --port 8765 >/tmp/uv.log 2>&1 &) ; sleep 2
scripts/ui_smoke.sh
```
Expected: 카드뉴스 도장 단계 전까지 `✓` 출력 후 `✗ 카드뉴스 도장 요소`(Task 4 전이므로 정상 실패). 나머지는 모두 `✓`.

- [ ] **Step 9: 회귀 테스트 + 커밋**

Run: `python3 test_relevance_engine.py | tail -2` → `ALL TESTS PASSED ✅`
```bash
git add web/app.js scripts/ui_smoke.sh
git commit -m "feat(ui): 퀘스트 트레일·HUD·발견 연출·챕터 진행·도감 카드로 SPA 재구성 (로직 불변)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN"
```

---

### Task 4: 카드뉴스 도감 테마 (TDD)

**Files:**
- Modify: `services/cardnews_service.py` (`_CATEGORY_EMOJI` 아래 상수 추가, `_cover_card`, `_relevant_card`, `_sensitized_card`, `_food_alert_card` 태그 마크업, `_closing_card`, `_wrap`)
- Modify: `test_relevance_engine.py` (`test_cardnews_and_report` 뒤에 테스트 추가)

**Interfaces:**
- Produces: HTML 내 `.stamp-verdict` 요소(진범 확정/무혐의/관찰), `.cat-stamp` SVG. Task 3 스모크가 `.stamp-verdict` 존재를 확인.

- [ ] **Step 1: 실패하는 테스트 작성**

`test_relevance_engine.py` 의 `test_cardnews_and_report` 함수 바로 뒤에 추가:
```python
def test_cardnews_quest_theme():
    """카드뉴스 도감 테마: 판정 도장·카테고리 스탬프·서사 문구 + 기존 마커 유지"""
    from services.cardnews_service import get_cardnews_service, _CATEGORY_STAMP_SVG
    rs = get_relevance_service()
    res = rs.build_assessments(build_case(), None)
    for a in res.assessments:
        a.answers = {Q_EXPOSED: "yes", Q_SYMPTOM: "yes", Q_REPRODUCIBLE: "yes"}
    rs.classify_all(res)
    html = get_cardnews_service().generate_html(res, {"name": "테스트", "test_date": "2026-06-01"})
    assert "알러젠 탐험 리포트" in html, "표지 서사 문구 누락"
    assert 'class="stamp-verdict' in html and "진범 확정" in html, "진범 확정 도장 누락"
    assert "무혐의 · 감작만" in html and "감작은 남아 있어 추적 필요" in html, "무혐의 도장/추적 부연 누락"
    assert 'class="cat-stamp' in html and "<svg" in html, "카테고리 스탬프 SVG 누락"
    assert "Do+Hyeon" in html, "디스플레이 폰트 링크 누락"
    for c in ("mite", "animal", "pollen_tree", "pollen_grass", "pollen_weed", "mold", "insect", "food", "other"):
        assert _CATEGORY_STAMP_SVG[c].startswith("<svg"), c
    # 기존 마커 유지
    assert "카드뉴스" in html and "테스트" in html
    print("✓ 카드뉴스 도감 테마(도장·스탬프·서사) + 기존 마커 유지")
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -c "import test_relevance_engine as t; t.test_cardnews_quest_theme()"`
Expected: `ImportError: cannot import name '_CATEGORY_STAMP_SVG'`

- [ ] **Step 3: cardnews_service.py 구현**

(a) `_CATEGORY_EMOJI` 딕셔너리 바로 아래에 추가:
```python
# 카테고리 스탬프(라인 SVG) — web/game.js STAMPS 와 동일 소스. 변경 시 양쪽 함께 수정.
def _svg(inner: str) -> str:
    return ('<svg viewBox="0 0 40 40" fill="none" stroke="currentColor" stroke-width="2.2" '
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + inner + '</svg>')

_CATEGORY_STAMP_SVG = {
    "mite": _svg('<circle cx="20" cy="22" r="8"/><path d="M12 18l-5-4M28 18l5-4M11 24l-6 1M29 24l6 1M13 29l-4 4M27 29l4 4M17 20h6"/>'),
    "animal": _svg('<circle cx="20" cy="26" r="6"/><circle cx="11" cy="18" r="3"/><circle cx="17" cy="12" r="3"/><circle cx="23" cy="12" r="3"/><circle cx="29" cy="18" r="3"/>'),
    "pollen_tree": _svg('<path d="M20 35V24"/><path d="M11 24h18L20 8z"/><path d="M14 18h12"/>'),
    "pollen_grass": _svg('<path d="M20 35V13M14 35c0-8 2-13 4-17M26 35c0-8-2-13-4-17M9 35c0-5 2-9 5-11M31 35c0-5-2-9-5-11"/>'),
    "pollen_weed": _svg('<path d="M20 35V15"/><path d="M20 25c-7 0-10-5-11-11 6 0 10 4 11 11zM20 20c7 0 10-5 11-11-6 0-10 4-11 11z"/>'),
    "mold": _svg('<circle cx="15" cy="23" r="6"/><circle cx="25" cy="18" r="5"/><circle cx="25" cy="28" r="4"/><path d="M15 23h.01M25 18h.01"/>'),
    "insect": _svg('<ellipse cx="20" cy="22" rx="7" ry="10"/><path d="M13 17l-5-6M27 17l5-6M12 24H6M28 24h6M14 30l-4 5M26 30l4 5M20 12v20"/>'),
    "food": _svg('<circle cx="20" cy="22" r="10"/><circle cx="20" cy="22" r="4"/><path d="M6 12v8M34 12v8"/>'),
    "other": _svg('<path d="M15 16a5 5 0 1 1 7 4.6c-1.5.8-2 1.8-2 3.4"/><circle cx="20" cy="29" r="1.3" fill="currentColor"/>'),
}


def _stamp(category: str) -> str:
    return f'<span class="cat-stamp">{_CATEGORY_STAMP_SVG.get(category, _CATEGORY_STAMP_SVG["other"])}</span>'
```

(b) `_cover_card` 반환 HTML 교체:
```python
        return f"""
        <div class="cover">
          <div class="badge">ALLERGEN QUEST · 카드뉴스</div>
          <h1>{_esc(name)}님의<br/>알러젠 탐험 리포트</h1>
          <p class="sub">검사일 {_esc(test_date) or '-'} · 검사 양성은 흔적일 뿐, 진범은 증상으로 확정</p>
          <div class="cover-stat">
            <div><span class="num">{len(relevant)}</span><span class="lbl">진범 확정</span></div>
            <div><span class="num">{len(sensitized)}</span><span class="lbl">무혐의·감작만</span></div>
            <div><span class="num">{len(indeterminate)}</span><span class="lbl">관찰 대상</span></div>
          </div>
          <p class="headline">{headline}</p>
        </div>
        """
```
`headline` 은 `f"진범으로 확정된 알러젠 <b>{len(relevant)}개</b>"` / `"실제 증상과 연관된 알러젠을 확인해 보세요"` 로 교체.

(c) `_chip` 은 `emoji` 대신 스탬프를 쓰되 `<b>{nm}</b>` 1회 유지:
```python
        return f'<div class="chip">{_stamp(a.category)} <b>{nm}</b>{season_html}{sev_html}</div>'
```

(d) `_relevant_card` 의 `<div class="tag">🔴 실제 주의</div>` → `<div class="stamp-verdict relevant">진범 확정</div>`, `<h2>증상을 유발하는<br/>알러젠</h2>` → `<h2>증상으로 확인된<br/>진짜 범인</h2>`.

(e) `_sensitized_card` 의 `<div class="tag">⚪ 감작만</div>` → `<div class="stamp-verdict sensitized">무혐의 · 감작만</div>`, `desc` 문장 끝에 ` <b>감작은 남아 있어 추적 필요</b>합니다.` 추가. `ind_html` 의 `<div class="mini-title">🟡 관찰 필요 (노출 시 확인)</div>` → `<div class="stamp-verdict indet small">관찰 대상</div>`.

(f) `_food_alert_card` 의 `<div class="tag">🍽️ 반드시 주의할 음식</div>` 은 **문구 유지**(테스트 마커). 클래스만 `tag food` 로.

(g) `_closing_card` 의 `<h2>` → `{_esc(name)}님,<br/>탐험의 핵심은 '증상과의 연결'`.

(h) `_wrap` 의 `<head>` 에 폰트 링크 추가, `<style>` 전체 교체:
```python
    def _wrap(self, cards_html: str, name: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(name)}님 알레르기 카드뉴스</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Do+Hyeon&display=swap"/>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{ --cream:#fbf7ee; --elev:#fffdf8; --ink:#1f2a24; --ink2:#4d5a52; --ink3:#83907f; --line:#e6dcc6;
           --forest:#2f8f5b; --forest-d:#1f5f3f; --amber:#f2a33a; --amber-soft:#fdeed6; --amber-ink:#8a5a12;
           --red:#e4572e; --red-soft:#fde9e2; --slate:#6b7a8f; --slate-soft:#eef1f5; --indet:#d9860a; --indet-soft:#fdf3e3;
           --display:'Do Hyeon','Pretendard','Apple SD Gothic Neo','Noto Sans KR',sans-serif; }}
  body {{ font-family:'Pretendard','Noto Sans KR','Malgun Gothic',-apple-system,sans-serif; color:var(--ink); padding:16px;
         background:var(--cream) radial-gradient(rgba(47,143,91,.07) 1px, transparent 1px); background-size:22px 22px; }}
  h1,h2 {{ font-family:var(--display); font-weight:400; }}
  .deck {{ display:flex; gap:16px; overflow-x:auto; padding:8px 2px 20px; scroll-snap-type:x mandatory; }}
  .card {{ flex:0 0 auto; width:340px; height:440px; border-radius:22px; overflow:hidden; scroll-snap-align:center;
          box-shadow:0 12px 32px rgba(60,48,20,.16); background:var(--elev); position:relative; border:1px solid var(--line); }}
  .card > div {{ height:100%; padding:26px 24px; display:flex; flex-direction:column; position:relative; }}
  .card > div::before {{ content:''; position:absolute; inset:9px; border:1.5px dashed rgba(0,0,0,.12); border-radius:15px; pointer-events:none; }}
  .cover {{ background:linear-gradient(150deg,var(--forest-d) 0%,var(--forest) 60%,#6fb98a 100%); color:#fff; justify-content:space-between; }}
  .cover::before {{ border-color:rgba(255,255,255,.35) !important; }}
  .badge {{ font-size:11px; letter-spacing:2px; font-weight:800; opacity:.9; }}
  .cover h1 {{ font-size:32px; line-height:1.2; margin-top:8px; }}
  .cover .sub {{ font-size:13px; opacity:.92; margin-top:10px; line-height:1.5; }}
  .cover-stat {{ display:flex; gap:10px; margin-top:auto; }}
  .cover-stat > div {{ flex:1; background:rgba(255,255,255,.16); border:1px solid rgba(255,255,255,.3); border-radius:14px; padding:12px 6px; text-align:center; }}
  .cover-stat .num {{ display:block; font-family:var(--display); font-size:30px; line-height:1; }}
  .cover-stat .lbl {{ display:block; font-size:11px; opacity:.95; margin-top:4px; font-weight:700; }}
  .cover .headline {{ margin-top:14px; font-size:14px; font-weight:700; background:rgba(0,0,0,.16); padding:10px 12px; border-radius:12px; }}
  .section {{ background:var(--elev); }}
  .tag {{ align-self:flex-start; font-size:12px; font-weight:800; padding:6px 12px; border-radius:999px; background:var(--amber-soft); color:var(--amber-ink); }}
  .stamp-verdict {{ align-self:flex-start; font-family:var(--display); font-size:17px; letter-spacing:1px; padding:5px 12px;
                    border:2.5px solid currentColor; border-radius:8px; transform:rotate(-4deg); margin:2px 0 4px 2px; }}
  .stamp-verdict.relevant {{ color:var(--red); }} .stamp-verdict.sensitized {{ color:var(--slate); }} .stamp-verdict.indet {{ color:var(--indet); }}
  .stamp-verdict.small {{ font-size:14px; margin-top:16px; }}
  .cat-stamp {{ display:inline-grid; place-items:center; width:22px; height:22px; border-radius:50%; background:var(--elev); border:1.5px solid var(--line); color:var(--forest-d); vertical-align:middle; margin-right:2px; }}
  .cat-stamp svg {{ width:16px; height:16px; }}
  .detail .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .treatment .tag {{ background:#e5f1fb; color:#1f6fb2; }}
  .prevention .tag {{ background:#e3f2e9; color:var(--forest-d); }}
  .oas .tag {{ background:var(--red-soft); color:var(--red); }}
  .oas .chip {{ background:#fff5f0; border-color:#f6c3b3; }}
  .imt {{ margin-top:12px; font-size:12.5px; background:#e3f2e9; color:var(--forest-d); border-radius:10px; padding:10px 12px; line-height:1.5; }}
  .section.detail, .section.treatment, .section.oas {{ overflow-y:auto; }}
  .section h2 {{ font-size:26px; line-height:1.25; margin:12px 0 10px; }}
  .desc {{ font-size:13px; line-height:1.6; color:var(--ink2); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; overflow:visible; }}
  .oas .chips {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; overflow:visible; }}
  .chip {{ font-size:13px; background:#f7f1e3; border:1px solid var(--line); border-radius:12px; padding:8px 12px; }}
  .chip-season {{ display:inline-block; margin-left:6px; font-size:11px; color:var(--ink3); }}
  .relevant .chip {{ background:var(--red-soft); border-color:#f6c3b3; }}
  .sensitized .chip {{ background:var(--slate-soft); }}
  .mini-title {{ font-size:12px; font-weight:800; color:var(--amber-ink); margin-top:16px; }}
  .empty {{ font-size:13px; color:var(--ink3); margin-top:16px; line-height:1.6; }}
  .tips {{ list-style:none; margin-top:12px; display:flex; flex-direction:column; gap:9px; }}
  .tips li {{ font-size:13px; line-height:1.5; background:#f4faf6; border-left:4px solid var(--forest); padding:9px 12px; border-radius:8px; }}
  .closing {{ background:linear-gradient(150deg,#1f2a24 0%,#2b3a44 100%); color:#fff; justify-content:center; }}
  .closing::before {{ border-color:rgba(255,255,255,.3) !important; }}
  .closing h2 {{ font-size:28px; }}
  .tag.light {{ background:rgba(255,255,255,.2); color:#fff; }}
  .desc.light {{ color:#e7ece8; }}
  .note {{ margin-top:auto; font-size:11px; color:#c3cdc6; line-height:1.5; }}
  .hint {{ text-align:center; color:var(--ink3); font-size:12px; margin-top:6px; }}
</style></head>
<body>
  <div class="deck">{cards_html}</div>
  <p class="hint">← 좌우로 넘겨 보세요 · 캡처하여 공유할 수 있습니다 →</p>
</body></html>"""
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python3 test_relevance_engine.py | tail -4`
Expected: `✓ 카드뉴스 도감 테마(도장·스탬프·서사) + 기존 마커 유지` 포함, `ALL TESTS PASSED ✅` (총 25종).

- [ ] **Step 5: 스모크 전체 통과 + 커밋**

```bash
scripts/ui_smoke.sh
```
Expected: `ALL SMOKE PASSED`
```bash
git add services/cardnews_service.py test_relevance_engine.py
git commit -m "feat(cardnews): 도감 테마 — 판정 도장·카테고리 스탬프·탐험 서사 (데이터 로직 불변)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN"
```

---

### Task 5: 시각 QA (다크·모바일·reduced-motion) + design-review + 문서

**Files:**
- Modify: `README.md` (「사용 흐름」표 단계명에 퀘스트명 병기), `web/styles.css`·`web/app.js` (QA 지적 수정 시)
- Modify: PR #1 본문(gh)

- [ ] **Step 1: 다크·모바일·reduced-motion 스크린샷**

```bash
B=~/.claude/skills/gstack/browse/dist/browse
$B goto http://127.0.0.1:8765/ && $B js "document.documentElement.setAttribute('data-theme','dark')" && $B screenshot /tmp/quest-smoke/dark-00.png
$B viewport 390x844 && $B screenshot /tmp/quest-smoke/mobile-00.png
$B click '#btnDemo' && $B wait '#next' && $B click '#next' && $B wait '#dGo' && $B screenshot /tmp/quest-smoke/mobile-discover.png && $B click '#dGo'
$B wait '#pName' && $B click '#next' && $B wait '.chapter' && $B screenshot /tmp/quest-smoke/mobile-quest.png
$B viewport 1280x900
$B cdp Emulation.setEmulatedMedia '{"features":[{"name":"prefers-reduced-motion","value":"reduce"}]}' && $B reload && $B wait '#stepper .tnode'
$B js "getComputedStyle(document.querySelector('.tnode.active .tnum')).animationName"
```
Expected: 스크린샷 4장 생성. 마지막 출력 `none`(reduced-motion 시 애니메이션 없음). (`cdp` 가 allowlist 에 없어 실패하면 `$B js "matchMedia('(prefers-reduced-motion: reduce)').matches"` 로 상태만 확인하고, CSS 규칙 존재를 `grep -c "prefers-reduced-motion" web/styles.css` → `1` 로 검증.)

Read 로 각 PNG 를 열어 확인: 다크 모드 대비(텍스트 가독), 모바일에서 트레일 라벨 겹침 없음, 발견 오버레이가 화면 안에 들어옴, 문진 하단 퀘스트바가 `.actions` 와 겹치지 않음. 문제가 있으면 `web/styles.css` 모바일 블록에서 수정 후 재캡처.

- [ ] **Step 2: design-review 스킬 실행**

Skill 도구로 `design-review` 를 호출해 `http://127.0.0.1:8765/` 를 점검한다(gstack, AI slop 패턴·간격·계층). 지적 사항 중 스펙에 부합하는 것만 수정하고, 수정 후 `scripts/ui_smoke.sh` 와 `node --test web/game.test.js` 재실행.

- [ ] **Step 3: README 갱신**

`README.md` 「🔄 사용 흐름 (5단계)」표의 `화면` 열을 다음으로 교체:
```
| 1 | 흔적 수집 (업로드 & OCR) | ... (기존 내용 유지)
| 2 | 증거 확인 (OCR 검토) | ...
| 3 | 탐험가 프로필 (환자정보 & 스크리닝) | ...
| 4 | 진범 감별 (양성 알러젠 감별 문진) | ...
| 5 | 도감 완성 (리포트·카드뉴스·FHIR) | ...
```
표 아래에 한 줄 추가: `> 웹앱 UI는 "알러젠 탐험 퀘스트 + 도감" 메타포로 구성됩니다(XP·배지는 진행 행동에만 부여, 판정 결과로 점수를 매기지 않음). 설계: docs/superpowers/specs/2026-09-08-gamified-quest-ui-design.md`

- [ ] **Step 4: 최종 검증 + 커밋 + 푸시 + PR 본문 갱신**

```bash
python3 test_relevance_engine.py | tail -1      # ALL TESTS PASSED ✅
node --test web/game.test.js 2>&1 | grep -E "^# (pass|fail)"   # pass 9 / fail 0
scripts/ui_smoke.sh | tail -1                    # ALL SMOKE PASSED
git add README.md web services
git commit -m "docs: README 사용 흐름에 퀘스트 단계명 병기 + 시각 QA 반영

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Xm5HX4AsdMVbi5vyrtqcGN"
git push origin claude/allergy-test-report-x4lgqh
```
PR #1 본문의 `## 테스트` 섹션 바로 위에 다음 섹션을 추가(`gh pr view 1 --json body -q .body` 로 받아 편집 후 `gh pr edit 1 --body-file`):
```
### 게임화 UI 재설계 — 알러젠 탐험 퀘스트 (2026-09-08)
- 퀘스트 트레일(5노드)·XP/레벨 HUD·양성 흔적 발견 오버레이·문진 챕터 링+하단 진행바·판정 도장 도감 카드(뒤집기)·배지 6종.
- 디자인 시스템 전면 교체(크림/숲녹색/호박, Do Hyeon 디스플레이 폰트, 카테고리 SVG 스탬프), 다크 모드·모바일·prefers-reduced-motion 대응.
- 카드뉴스 동일 시각언어 적용(판정 도장·스탬프). 임상 리포트 HTML/PDF 는 변경 없음.
- 가드레일: XP 는 진행 행동에만, 판정 결과로 점수 생성 금지, 중증/아나필락시스 시 축하 연출 억제.
- 검증: `node --test web/game.test.js` 9종, 회귀 25종, `scripts/ui_smoke.sh`(gstack browse) 전 단계 통과.
```

---

## Self-Review

**Spec coverage**
- §3 비주얼 시스템(토큰·폰트·스탬프·모션·reduced-motion) → Task 2, Task 1(STAMPS).
- §4 게임 레이어: 상태·XP 규칙·배지·훅 포인트·화면별 상세(HUD/트레일/업로드/OCR/스크리닝/문진/결과)·가드레일(severeFlag, 무혐의 부연, XP 행동 기반) → Task 1, Task 3.
- §5 카드뉴스 → Task 4. §6 파일 목록 → 각 Task. §7 검증(회귀·E2E·모바일·다크·reduced-motion·design-review) → Task 3 Step 8, Task 5.
- 스펙 "발견 오버레이 최대 12장 + N종" → `showDiscovery`. "localStorage 미사용" → game.js 에 storage 접근 없음.
- 스펙 §4 화면 4 "확정 시 오버레이 후 자동 다음 단계" → 구현은 버튼 클릭으로 진행(자동 전환은 카드 열람 시간을 빼앗아 UX 저하; 버튼 1회 클릭으로 변경). 스펙 의도(연출 후 진행)는 유지.

**Placeholder scan**: TBD/TODO/"similar to" 없음. 모든 코드 스텝에 실제 코드 포함.

**Type consistency**: `Game.discover(g, rows, testType)`(Task1) ↔ `confirmDiscovery`(Task3) 시그니처 일치. `Game.chapterProgress(section, answers, isVisible)` ↔ `refreshProgress`. `Game.ui.renderTrail(el, steps, subs, step, maxReached, onGoto)` ↔ `renderStepper`. `Game.ui.showDiscovery(overlay, cards, onDone)` cards `{name, category, stars}` ↔ `confirmDiscovery`. 카드뉴스 `.stamp-verdict` ↔ 스모크 셀렉터 일치.
