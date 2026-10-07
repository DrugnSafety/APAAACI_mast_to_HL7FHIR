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
  // 약물 항원의 판정 — 진범 확정도 무혐의도 아닌 자기 도장
  assert.equal(Game.VERDICT.clinician_review.stamp, '진료 확인 필요');
  assert.equal(Game.VERDICT.clinician_review.tone, 'indet');
  for (const c of ['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'venom', 'food', 'latex', 'drug', 'control', 'other'])
    assert.ok(Game.STAMPS[c].startsWith('<svg'), c);
  assert.ok(Game.stampSvg('unknown').startsWith('<svg'));
  assert.equal(Game.stampSvg('unknown'), Game.STAMPS.other);
  for (const c of ['latex', 'drug', 'control']) assert.notEqual(Game.STAMPS[c], Game.STAMPS.other, `${c} has its own stamp`);
  assert.equal(Game.starsHtml(2), '<span class="stars" aria-label="감작 강도 2/3">★★<i>★</i></span>');
});

test('completeStage: 5xp once per stage, independent of what was chosen', () => {
  const g = Game.createState();
  assert.equal(Game.completeStage(g, 'profile.id'), 5);
  assert.equal(Game.completeStage(g, 'profile.id'), 0);
  assert.equal(Game.completeStage(g, 'profile.place'), 5);
  assert.equal(g.xp, 10);
});

test('discover: keeps test values for the card and uses catOf when the row has no category', () => {
  const g = Game.createState();
  Game.discover(g, [{ allergen_name: 'Cat dander', korean_name: '고양이 비듬', value: 1.4, unit: 'kU/L', class_value: 2 }], 'MAST', () => 'animal');
  assert.deepEqual(g.discovered['Cat dander'], { name: '고양이 비듬', category: 'animal', stars: 1, verdict: null, en: 'Cat dander', value: 1.4, unit: 'kU/L', cls: 2, no: 1 });
});

test('recordGrade: foil follows record completeness, never positivity or strength', () => {
  const full = { test_value: 17.6, class_value: 4, season_label_ko: '연중', exposure_environment_ko: '침실', avoidance_control_ko: ['세탁'], cross_reactivity_ko: '새우' };
  // 진범 확정과 무혐의(감작만)는 같은 등급 — 양성 결과가 더 '좋은 카드'가 되지 않는다
  assert.equal(Game.recordGrade({ ...full, relevance: 'clinically_relevant' }).grade, 2);
  assert.equal(Game.recordGrade({ ...full, relevance: 'sensitized_only' }).grade, 2);
  // 수치·class·강도는 등급을 바꾸지 않는다
  assert.equal(Game.recordGrade({ ...full, relevance: 'sensitized_only', class_value: 1, test_value: 0.4, strength: 'weak' }).grade,
               Game.recordGrade({ ...full, relevance: 'sensitized_only', class_value: 6, test_value: 120, strength: 'strong' }).grade);
  assert.equal(Game.recordGrade({ ...full, relevance: 'indeterminate' }).grade, 1);
  assert.equal(Game.recordGrade({ ...full, relevance: 'not_assessed' }).grade, 0);
  assert.equal(Game.recordGrade({ test_value: 3, relevance: 'clinically_relevant' }).grade, 0);
  assert.deepEqual(Game.recordGrade({}).facets, { measured: false, verdict: false, season: false, exposure: false, guidance: false, cross: false });
});

test('levelPips: class 0-6, SPT wheal 1-3, otherwise strength', () => {
  assert.deepEqual(Game.levelPips({ class_value: 4 }, 'MAST'), { n: 4, max: 6, kind: 'class' });
  assert.deepEqual(Game.levelPips({ class_value: '9' }, 'MAST'), { n: 6, max: 6, kind: 'class' });
  assert.deepEqual(Game.levelPips({ test_value: 6, test_unit: 'mm' }, 'SPT'), { n: 2, max: 3, kind: 'wheal' });
  assert.deepEqual(Game.levelPips({ strength: 'strong' }, 'UniCAP'), { n: 3, max: 3, kind: 'strength' });
  assert.deepEqual(Game.levelPips({}, 'MAST'), { n: 1, max: 3, kind: 'strength' });
});

test('sortCards and dexSummary', () => {
  const list = [
    { allergen_name: 'B', korean_name: '나', category: 'food', relevance: 'sensitized_only', class_value: 5 },
    { allergen_name: 'A', korean_name: '가', category: 'mite', relevance: 'clinically_relevant', class_value: 2 },
    { allergen_name: 'C', korean_name: '다', category: 'animal', relevance: 'indeterminate', class_value: 3 },
  ];
  const names = (m) => Game.sortCards(list, m).map(a => a.allergen_name).join('');
  assert.equal(names('verdict'), 'ACB');
  assert.equal(names('strength'), 'BCA');
  assert.equal(names('name'), 'ABC');
  assert.equal(names('category'), 'ACB');
  assert.equal(list[0].allergen_name, 'B', 'does not mutate the input');
  const s = Game.dexSummary(list);
  assert.equal(s.total, 3); assert.equal(s.resolved, 2);
  assert.deepEqual(s.byCategory.map(c => c.category), ['mite', 'animal', 'food']);
  assert.deepEqual(s.byVerdict, { clinically_relevant: 1, sensitized_only: 1, indeterminate: 1, not_assessed: 0 });
});

test('control lines: own verdict stamp, never counted or graded, sorted and bound last', () => {
  assert.equal(Game.VERDICT.control.tone, 'na');
  assert.equal(Game.VERDICT.control.stamp, '검사 대조');
  const full = { test_value: 5, class_value: 3, season_label_ko: '해당 없음', exposure_environment_ko: 'x', avoidance_control_ko: ['y'], cross_reactivity_ko: 'z' };
  assert.equal(Game.recordGrade({ ...full, relevance: 'clinically_relevant', category: 'control' }).grade, 0);
  const list = [
    { allergen_name: 'Histamine', category: 'control', relevance: 'clinically_relevant', class_value: 6 },
    { allergen_name: 'Latex', category: 'latex', relevance: 'indeterminate', class_value: 2 },
    { allergen_name: 'Amoxicillin', category: 'drug', relevance: 'sensitized_only', class_value: 1 },
    { allergen_name: 'Mystery', category: 'other', relevance: 'not_assessed' },
    { allergen_name: 'Egg', category: 'food', relevance: 'clinically_relevant', class_value: 3 },
  ];
  const s = Game.dexSummary(list);
  assert.equal(s.total, 4, 'the control line is not a registered allergen');
  assert.equal(s.controls, 1);
  assert.equal(s.resolved, 2);
  assert.deepEqual(s.byVerdict, { clinically_relevant: 1, sensitized_only: 1, indeterminate: 1, not_assessed: 1 });
  assert.deepEqual(s.byCategory.map(c => c.category), ['food', 'latex', 'drug', 'other', 'control']);
  assert.equal(Game.sortCards(list, 'verdict').map(a => a.allergen_name).join(','), 'Egg,Latex,Amoxicillin,Mystery,Histamine');
  assert.equal(Game.sortCards(list, 'category').map(a => a.allergen_name).join(','), 'Egg,Latex,Amoxicillin,Mystery,Histamine');
});

test('pending verdict exists for cards registered before the questionnaire', () => {
  assert.equal(Game.VERDICT.pending.stamp, '판정 대기');
  assert.equal(Game.VERDICT.pending.tone, 'na');
});
