'use strict';
// 서버가 내보낼 수 있는 알러젠 종류(data/category_rules.json valid_categories) 전부가 화면의 표·그림·문구·색에 있는지 확인한다.
// 종류가 하나 늘었는데 화면 쪽 표에 빠지면 도감에서 '•'/기타로 떨어진다 — 그 누락을 여기서 잡는다.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Game = require('./game.js');
const Art = require('./art.js');
const I18N = require('./i18n.js');
const Shared = require('./shared.js');

const VALID = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'data', 'category_rules.json'), 'utf8')).valid_categories;
const read = (f) => fs.readFileSync(path.join(__dirname, f), 'utf8');

test('the server category list is the one the screens know', () => {
  assert.deepEqual([...VALID].sort(), [...Shared.CATEGORIES].sort());
  assert.deepEqual([...VALID].sort(), [...Game.CATEGORY_ORDER].sort());
  assert.equal(Game.CATEGORY_ORDER[Game.CATEGORY_ORDER.length - 1], 'control', 'the test control line is bound last — it is not an allergen');
});

test('every category has a label in ko/en/zh, a stamp, a plate, an emoji and a type colour', () => {
  const css = read('styles.css');
  const emoji = (file) => { const m = read(file).match(/const CAT_EMOJI = \{([^}]*)\}/); assert.ok(m, `${file}: CAT_EMOJI`); return m[1]; };
  const maps = { 'app.js': emoji('app.js'), 'classic/app.js': emoji('classic/app.js') };
  for (const c of VALID) {
    for (const lang of ['ko', 'en', 'zh']) assert.ok(I18N.DICT[lang][`cat.${c}`], `${lang}: cat.${c}`);
    assert.ok(Game.STAMPS[c], `stamp ${c}`);
    assert.equal(Art.plateId(c, ''), c, `plate ${c}`);
    for (const [file, src] of Object.entries(maps)) assert.ok(new RegExp(`\\b${c}: '`).test(src), `${file}: CAT_EMOJI.${c}`);
    assert.equal((css.match(new RegExp(`--type-${c}:`, 'g')) || []).length, 3, `--type-${c} in light, dark and OS-dark tokens`);
    assert.ok(css.includes(`[data-cat="${c}"] { --tc: var(--type-${c}); }`), `[data-cat="${c}"] rule`);
  }
  for (const c of VALID.filter(c => c !== 'other')) {
    assert.notEqual(Game.STAMPS[c], Game.STAMPS.other, `${c}: own stamp`);
    assert.notEqual(Art.plate(c, ''), Art.plate('other', ''), `${c}: own plate`);
  }
});

test('type colours are distinct from each other and from the verdict colours', () => {
  const css = read('styles.css');
  const light = css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf('--type-control:')));
  const hex = (name) => { const m = light.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`)); assert.ok(m, name); return m[1].toLowerCase(); };
  const rgb = (h) => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16));
  const dist = (a, b) => Math.hypot(...rgb(a).map((v, i) => v - rgb(b)[i]));
  const types = VALID.map(c => [c, hex(`type-${c}`)]);
  assert.equal(new Set(types.map(t => t[1])).size, types.length);
  for (const v of ['relevant', 'relevant-ink', 'sensitized', 'sensitized-ink', 'indet', 'indet-ink']) {
    for (const c of ['latex', 'drug', 'control']) assert.ok(dist(hex(v), hex(`type-${c}`)) > 40, `--type-${c} too close to --${v}`);
  }
});

test('refineCategory: a category sent by the server is kept — including "other"; only unclassified rows are guessed by name', () => {
  const r = Shared.refineCategory;
  // 서버가 정한 종류는 이름과 어긋나 보여도 그대로 쓴다
  assert.equal(r('mite', 'Latex mite'), 'mite');
  assert.equal(r('drug', 'Whatever'), 'drug');
  assert.equal(r('control', 'x'), 'control');
  assert.equal(r('latex', 'Hevea brasiliensis'), 'latex');
  assert.equal(r('venom', 'Honey bee venom'), 'venom');
  assert.equal(r('food', 'Latex fruit'), 'food');
  assert.equal(r('mold', 'Cephalosporium acremonium'), 'mold');
  assert.equal(r('other', 'Latex', '라텍스'), 'other');
  assert.equal(r('other', 'Amoxicillin', '아목시실린'), 'other');
  assert.equal(r('other', 'Positive control', '양성 대조'), 'other');
  // 아직 서버를 거치지 않은 행(종류가 비어 있음)만 이름으로 가늠한다
  for (const none of [null, undefined, '']) {
    assert.equal(r(none, 'Natural rubber latex'), 'latex');
    assert.equal(r(none, 'Hevea brasiliensis'), 'latex');
    assert.equal(r(none, '', '乳胶'), 'latex');
    assert.equal(r(none, 'Amoxicillin', '아목시실린'), 'drug');
    assert.equal(r(none, 'Penicilloyl G'), 'drug');
    assert.equal(r(none, 'Positive control', '양성 대조'), 'control');
    assert.equal(r(none, 'Negative control'), 'control');
    assert.equal(r(none, 'Histamine', '양성 대조용 0.1% 히스타민 용액'), 'control');
    assert.equal(r(none, 'Control', '음성 대조용 생리식염수'), 'control');
    assert.equal(r(none, 'Unknown thing'), 'other');
    // 이름에 낱말이 들어 있다고 대조로 보지 않는다
    assert.equal(r(none, 'Birth control pill'), 'other');
    assert.equal(r(none, 'Histamine releasing fish'), 'other');
  }
  assert.equal(r('not_a_category', 'Unknown thing'), 'other', 'a value outside the server list counts as unclassified');
  assert.equal(r('not_a_category', 'Latex'), 'latex');
  assert.equal(r(undefined), 'other');
});

test('isControl / resultSummary: control lines are not positive allergens', () => {
  assert.equal(Shared.isControl({ allergen_name: 'Histamine', korean_name: '', category: null }), true);
  assert.equal(Shared.isControl({ allergen_name: 'Cat dander', category: 'animal' }), false);
  assert.equal(Shared.isControl(null), false);
  assert.equal(Shared.isControl({ allergen_name: 'x', category: 'control' }), true, 'the server category decides');
  assert.equal(Shared.isControl({ allergen_name: 'Histamine', category: 'other' }), false, 'a row the server classified is not re-guessed by name');
  const c = { summary: { total_positive: 4, counts: { clinically_relevant: 1, sensitized_only: 1, indeterminate: 2 } },
    assessments: [
      { allergen_name: 'Cat dander', category: 'animal', relevance: 'clinically_relevant' },
      { allergen_name: 'Birch', category: 'pollen_tree', relevance: 'sensitized_only' },
      { allergen_name: 'Latex', category: 'latex', relevance: 'indeterminate' },
      { allergen_name: 'Positive control', category: 'control', relevance: 'indeterminate' }] };
  assert.deepEqual({ ...Shared.resultSummary(c), counts: { ...Shared.resultSummary(c).counts } },
    { counts: { clinically_relevant: 1, sensitized_only: 1, indeterminate: 1 }, total: 3, controls: 1, allergens: 3 });
  // 대조 항목이 없으면 서버 요약 그대로
  const plain = { summary: { total_positive: 2, counts: { clinically_relevant: 2, sensitized_only: 0, indeterminate: 0 } }, assessments: c.assessments.slice(0, 2) };
  assert.deepEqual({ ...Shared.resultSummary(plain).counts }, { clinically_relevant: 2, sensitized_only: 0, indeterminate: 0 });
  assert.equal(Shared.resultSummary(plain).total, 2);
  assert.deepEqual({ ...Shared.resultSummary(null).counts }, { clinically_relevant: 0, sensitized_only: 0, indeterminate: 0 });
  assert.equal(Shared.resultSummary({ assessments: c.assessments }).total, 3, 'no summary: counted from the list');
});
