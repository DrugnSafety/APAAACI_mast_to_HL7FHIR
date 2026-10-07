'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const Art = require('./art.js');

const all = () => [
  ...Art.SCENE_IDS.map(id => ['scene:' + id, Art.scene(id)]),
  ...['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'venom', 'food', 'latex', 'drug', 'control', 'other'].map(c => ['plate:' + c, Art.plate(c, '')]),
  ['plate:cat', Art.plate('animal', 'Cat dander 고양이 비듬')], ['plate:dog', Art.plate('animal', 'Dog dander 개 비듬')],
  ...Art.ICON_IDS.map(id => ['icon:' + id, Art.icon(id)]),
  ['emblem', Art.emblem()],
];

test('every illustration is a self-contained, decorative inline svg', () => {
  for (const [name, svg] of all()) {
    assert.ok(svg.startsWith('<svg class="art '), name);
    assert.ok(svg.includes('aria-hidden="true"'), name + ' must be hidden from assistive tech');
    assert.ok(!/(href=|url\(|<image|<script|<foreignObject)/i.test(svg), name + ' must not reference external assets');
    assert.ok(!/(NaN|undefined|null)/.test(svg), name + ' has a broken value');
    for (const d of svg.match(/ d="[^"]*"/g) || []) assert.ok(!/--|[^\d\s.,a-zA-Z-]/.test(d.slice(4, -1)), `${name} malformed path ${d.slice(0, 60)}`);
  }
});

test('each questionnaire/profile stage has its own scene, with a fallback for unknown chapters', () => {
  for (const id of ['brief', 'upload', 'scan', 'review', 'profile_id', 'profile_place', 'profile_disease', 'profile_meds', 'profile_organs', 'profile_pets', 'summary', 'judge', 'results'])
    assert.ok(Art.SCENE_IDS.includes(id), id);
  for (const sec of ['pattern', 'season', 'pollen', 'indoor', 'animal', 'venom', 'food']) assert.ok(Art.chapterScene(sec).includes(`scene-ch_${sec}`), sec);
  assert.ok(Art.chapterScene('something_new').includes('scene-ch_generic'));
  assert.ok(Art.scene('nope').includes('scene-ch_generic'));
});

test('plates: distinct art per category, cat/dog variants, unknown falls back to other', () => {
  const cats = ['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'venom', 'food', 'latex', 'drug', 'control', 'other'];
  assert.equal(new Set(cats.map(c => Art.plate(c, ''))).size, cats.length);
  for (const c of cats) assert.equal(Art.plateId(c, ''), c, `${c} has its own plate`);
  assert.equal(Art.plateId('animal', 'Cat dander'), 'animal_cat');
  assert.equal(Art.plateId('animal', '개 비듬'), 'animal_dog');
  assert.equal(Art.plateId('animal', 'Horse'), 'animal');
  assert.equal(Art.plateId('mystery', ''), 'other');
});
