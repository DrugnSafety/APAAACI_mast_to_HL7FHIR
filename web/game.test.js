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
