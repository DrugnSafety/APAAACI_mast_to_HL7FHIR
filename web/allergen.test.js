'use strict';
// 알러젠 자동완성의 순수 검색 로직(web/shared.js 의 AllergenSearch) 단위 테스트.
const test = require('node:test');
const assert = require('node:assert/strict');
const { AllergenSearch } = require('./shared.js');
const idsOf = (hits) => Array.from(hits, r => r.item.id);

const ITEMS = [
  { id: 'birch', canonical_name: 'Birch', korean_name: '자작나무', category: 'pollen_tree', aliases: ['Birch', '자작나무'], zh_names: ['桦树', '白桦'] },
  { id: 'silver_birch', canonical_name: 'Silver birch', korean_name: '은자작나무', category: 'pollen_tree', aliases: ['Betula pendula'], zh_names: [] },
  { id: 'cat_dander', canonical_name: 'Cat dander', korean_name: '고양이 표피', category: 'animal', aliases: ['Cat', '고양이', '고양이 비듬'], zh_names: ['猫毛皮屑', '猫毛'] },
  { id: 'der_f', canonical_name: 'Dermatophagoides farinae', korean_name: '미국집먼지진드기', category: 'mite', aliases: ['D. farinae', 'Der f', 'House dust mite'], zh_names: ['粉尘螨'] },
  { id: 'cow_milk', canonical_name: 'Cow milk', korean_name: '우유', category: 'food', aliases: ['Milk', '밀크'], zh_names: ['牛奶'] },
  { id: 'bermuda', canonical_name: 'Bermuda grass', korean_name: '우산잔디', category: 'pollen_grass', aliases: ['Bermuda'], zh_names: [] },
  { id: 'rabbit', canonical_name: 'Rabbit', korean_name: '토끼', category: 'animal', aliases: [], zh_names: null },
];
const IDX = AllergenSearch.index(ITEMS);
const ids = (q, limit) => idsOf(AllergenSearch.search(IDX, q, limit));

test('English prefix: "bir" finds Birch first, word-prefix match after it', () => {
  assert.deepEqual(ids('bir'), ['birch', 'silver_birch']);
  const [first, second] = AllergenSearch.search(IDX, 'bir');
  assert.deepEqual({ ...first.match }, { field: 'canonical_name', text: 'Birch', type: 'prefix' });
  assert.equal(second.match.type, 'word');
});

test('Korean name: "자작" — prefix before mid-word match', () => {
  assert.deepEqual(ids('자작'), ['birch', 'silver_birch']);
  assert.equal(AllergenSearch.search(IDX, '자작')[1].match.type, 'contains');
});

test('aliases and Chinese names are searched; the matched text is reported', () => {
  assert.deepEqual(ids('house dust'), ['der_f']);
  assert.deepEqual({ ...AllergenSearch.search(IDX, 'house dust')[0].match }, { field: 'alias', text: 'House dust mite', type: 'prefix' });
  assert.deepEqual(ids('猫'), ['cat_dander']);
  assert.equal(AllergenSearch.search(IDX, '白桦')[0].match.field, 'zh_name');
  assert.deepEqual(ids('밀크'), ['cow_milk']);
});

test('ranking: exact > prefix > word > contains; name match beats alias match of the same type', () => {
  const idx = AllergenSearch.index([
    { id: 'contains', canonical_name: 'Scatter', korean_name: '', aliases: [] },
    { id: 'word', canonical_name: 'Wild cat', korean_name: '', aliases: [] },
    { id: 'alias_prefix', canonical_name: 'Feline', korean_name: '', aliases: ['Cat hair'] },
    { id: 'prefix', canonical_name: 'Cat dander', korean_name: '', aliases: [] },
    { id: 'exact', canonical_name: 'Cat', korean_name: '', aliases: [] },
  ]);
  assert.deepEqual(idsOf(AllergenSearch.search(idx, 'cat')), ['exact', 'prefix', 'alias_prefix', 'word', 'contains']);
});

test('ties: shorter matched text first, then alphabetical', () => {
  const idx = AllergenSearch.index([
    { id: 'b', canonical_name: 'Oak b', aliases: [] }, { id: 'long', canonical_name: 'Oak tree pollen', aliases: [] }, { id: 'a', canonical_name: 'Oak a', aliases: [] },
  ]);
  assert.deepEqual(idsOf(AllergenSearch.search(idx, 'oak')), ['a', 'b', 'long']);
});

test('case, width and spacing are ignored', () => {
  assert.deepEqual(ids('  BIR '), ['birch', 'silver_birch']);
  assert.deepEqual(ids('ｂｉｒ'), ['birch', 'silver_birch']);       // 전각 → 반각
  assert.deepEqual(ids('고양이비듬'), ['cat_dander']);               // 띄어쓰기 없이
  assert.deepEqual(ids('미국 집먼지'), ['der_f']);                   // 없는 띄어쓰기를 넣어도
  assert.deepEqual(ids('d.farinae'), ['der_f']);
});

test('a trailing Hangul jamo left by the IME is ignored ("자ㅈ" while typing 자작)', () => {
  assert.equal(AllergenSearch.queryKey('자ㅈ'), '자');
  assert.deepEqual(ids('자ㅈ'), ['birch', 'silver_birch']);
  assert.equal(AllergenSearch.queryKey('ㅈ'), AllergenSearch.norm('ㅈ'), 'a lone jamo is kept as the query');
  assert.deepEqual(ids('ㅈ'), []);
});

test('no match and empty query return [] — free text is left alone', () => {
  assert.deepEqual(ids('zzzz-not-an-allergen'), []);
  assert.deepEqual(ids(''), []);
  assert.deepEqual(ids('   '), []);
  assert.equal(AllergenSearch.search(null, 'bir').length, 0);
});

test('limit caps the list (default 8); each item appears once', () => {
  const many = AllergenSearch.index(Array.from({ length: 20 }, (_, i) => ({ id: `p${i}`, canonical_name: `Pollen ${String(i).padStart(2, '0')}`, korean_name: `꽃가루 ${i}`, aliases: ['Pollen'] })));
  assert.equal(AllergenSearch.search(many, 'pollen').length, 8);
  assert.equal(AllergenSearch.search(many, 'pollen', 3).length, 3);
  assert.deepEqual(ids('b'), ['birch', 'bermuda', 'silver_birch', 'rabbit']);   // Birch 는 이름·별칭 둘 다 맞지만 한 번만
});

test('index tolerates missing aliases / zh_names / korean_name', () => {
  assert.deepEqual(ids('토끼'), ['rabbit']);
  assert.deepEqual(idsOf(AllergenSearch.search(AllergenSearch.index([{ id: 'x', canonical_name: 'Xylem' }]), 'xy')), ['x']);
  assert.equal(AllergenSearch.index(undefined).length, 0);
});
