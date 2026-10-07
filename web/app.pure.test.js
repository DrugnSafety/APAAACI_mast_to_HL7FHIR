'use strict';
// 퀘스트·클래식 화면이 함께 쓰는 순수 로직(web/shared.js) 단위 테스트:
// 언어 전환 폴링 판단 · 빈 행 판별 · 상담 창 자르기 · 카드뉴스 체크 메시지 검증.
const test = require('node:test');
const assert = require('node:assert/strict');
const { LangSync, rowBlank, rowIsZero, reviewCounts, chatWindow, CHAT_MAX_MESSAGES, CHAT_MAX_CHARS, DeckBridge } = require('./shared.js');
const Shared = require('./shared.js');
const I18N = require('./i18n.js');

const status = (langs) => ({ langs: Object.fromEntries(Object.entries(langs).map(([k, s]) => [k, { status: s }])) });

test('verdict: ready 만 받아 올 수 있다', () => {
  assert.equal(LangSync.verdict(status({ ko: 'ready', en: 'ready' }), 'en'), 'ready');
});

test('verdict: pending·running 은 기다린다', () => {
  assert.equal(LangSync.verdict(status({ en: 'pending' }), 'en'), 'wait');
  assert.equal(LangSync.verdict(status({ en: 'running' }), 'en'), 'wait');
});

test('verdict: ready·대기 외의 상태는 모두 받을 수 없음으로 본다(failed·partial·skipped·missing·모르는 값)', () => {
  for (const s of ['failed', 'partial', 'skipped', 'missing', 'something_new', '', null, undefined]) {
    assert.equal(LangSync.verdict(status({ en: s }), 'en'), 'failed', String(s));
  }
});

test('verdict: 응답에 그 언어가 없거나 응답이 깨졌으면 받을 수 없음', () => {
  assert.equal(LangSync.verdict(status({ ko: 'ready' }), 'zh'), 'failed');
  assert.equal(LangSync.verdict({}, 'en'), 'failed');
  assert.equal(LangSync.verdict(null, 'en'), 'failed');
});

test('delay: 간격이 줄어들지 않고 10초에서 멈춘다', () => {
  const ds = Array.from({ length: LangSync.MAX_ATTEMPTS }, (_, i) => LangSync.delay(i));
  assert.equal(ds[0], 1500);
  ds.forEach((d, i) => { if (i) assert.ok(d >= ds[i - 1], `attempt ${i}`); });
  assert.equal(ds[ds.length - 1], 10000);
  assert.equal(LangSync.delay(999), 10000);
  const total = ds.reduce((a, b) => a + b, 0);
  assert.ok(total > 120000 && total < 600000, `기다리는 총 시간 ${total}ms 는 2~10분 사이`);
});

test('rowBlank: 이름·수치·Class 가 모두 빈 행만 빈 행이다', () => {
  assert.equal(rowBlank({ allergen_name: '', korean_name: '', value: null, mean_mm: null, class_value: null, interpretation: 'Positive' }), true);
  assert.equal(rowBlank({ allergen_name: '   ', korean_name: '', value: '', class_value: '' }), true);
  assert.equal(rowBlank(null), true);
  assert.equal(rowBlank({ allergen_name: 'Cat', value: null }), false);
  assert.equal(rowBlank({ allergen_name: '', korean_name: '고양이' }), false);
  assert.equal(rowBlank({ allergen_name: '', value: 0 }), false);          // 수치 0 은 입력한 값이다
  assert.equal(rowBlank({ allergen_name: '', class_value: 2 }), false);
  assert.equal(rowBlank({ allergen_name: '', mean_mm: 4 }), false);
});

const turns = (n) => Array.from({ length: n }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `m${i}` }));

test('chatWindow: 한도 안의 대화는 그대로(역할·내용만) 보낸다', () => {
  const out = chatWindow([{ role: 'user', content: 'q', extra: 1 }, { role: 'assistant', content: 'a', sources: [{}] }], 40, 4000);
  assert.deepEqual(JSON.parse(JSON.stringify(out)), [{ role: 'user', content: 'q' }, { role: 'assistant', content: 'a' }]);
});

test('chatWindow: 서버 한도(40개)를 넘으면 최근 것만, 사용자 질문으로 시작하고 마지막 질문으로 끝난다', () => {
  const msgs = turns(101);                       // user 로 끝난다(방금 보낸 질문)
  const out = chatWindow(msgs, CHAT_MAX_MESSAGES, CHAT_MAX_CHARS);
  assert.ok(out.length <= 40, `길이 ${out.length}`);
  assert.equal(out[0].role, 'user');
  assert.equal(out[out.length - 1].content, 'm100');
  assert.equal(out.length, 39);                  // 최근 40개는 답변으로 시작하므로 하나를 더 뗀다
});

test('chatWindow: 긴 내용은 서버 한도(4000자)로 자르고, 다른 역할·빈 항목은 뺀다', () => {
  const out = chatWindow([null, { role: 'system', content: 'x' }, { role: 'user', content: 'q' }, { role: 'assistant', content: 'a'.repeat(9000) }, { role: 'user', content: null }], 40, 4000);
  assert.equal(out.length, 3);
  assert.equal(out[1].content.length, 4000);
  assert.equal(out[2].content, '');
  assert.deepEqual(Array.from(chatWindow(null, 40, 4000)), []);
});

test('I18N.errText: 공통 오류 코드는 세 언어 모두 현지화 문구를 낸다', () => {
  for (const lang of ['ko', 'en', 'zh']) {
    I18N.setLang(lang);
    assert.match(I18N.errText({ code: 'rate_limited', retryAfter: 12 }), /12/);
    assert.equal(I18N.errText({ code: 'rate_limited', retryAfter: null }), I18N.t('err.rate_limited_soon'));
    assert.equal(I18N.errText({ code: 'rate_limited', retryAfter: 'abc' }), I18N.t('err.rate_limited_soon'));
    for (const code of ['file_too_large', 'unsupported_file', 'chat_full']) {
      assert.equal(I18N.errText({ code }), I18N.DICT[lang]['err.' + code]);
    }
    assert.equal(I18N.errText({ code: 'payload_too_large' }), I18N.DICT[lang]['err.file_too_large']);
    assert.equal(I18N.errText({ code: 'something_else' }), null);
    assert.equal(I18N.errText(null), null);
  }
  I18N.setLang('ko');
});

test('reviewCounts: 빈 행은 측정값·수치 0 어느 탭에도 세지 않는다', () => {
  const blank = { allergen_name: '', korean_name: '', value: null, mean_mm: null, class_value: null, interpretation: 'Positive' };
  const named = (n, value) => ({ allergen_name: n, korean_name: '', value, class_value: null });
  // 직접 입력: 이름만 적은 두 행 + 막 추가한 빈 행 → 수치 0 항목은 2
  assert.deepEqual({ ...reviewCounts([named('Cat', null), named('Dog', null), blank]) }, { measured: 0, zero: 2 });
  assert.deepEqual({ ...reviewCounts([named('Cat', 3.2), named('Dog', 0), named('Mite', ''), blank, blank]) }, { measured: 1, zero: 2 });
  assert.deepEqual({ ...reviewCounts([blank]) }, { measured: 0, zero: 0 });
  assert.deepEqual({ ...reviewCounts([]) }, { measured: 0, zero: 0 });
  assert.deepEqual({ ...reviewCounts(null) }, { measured: 0, zero: 0 });
  assert.equal(rowIsZero(blank), true, '빈 행은 수치 0 탭에 그대로 보인다(입력할 수 있게)');
  assert.equal(rowIsZero(named('Cat', '0.35')), false);
});

/* ---------------- 카드뉴스 '내 실천 체크' 메시지 검증 ---------------- */
const hello = (cards) => ({ source: 'allergy-cardnews', v: 1, type: 'hello', cards });
const setMsg = (id, on) => ({ source: 'allergy-cardnews', v: 1, type: 'set', id, on });

test('DeckBridge.parse: 틀에 맞는 hello·set 만 받는다', () => {
  assert.deepEqual(DeckBridge.parse(hello({ 6: 2, 9: 5 })), { type: 'hello', cards: { 6: 2, 9: 5 } });
  assert.deepEqual(DeckBridge.parse(setMsg('6:0', true)), { type: 'set', id: '6:0', on: true });
  assert.deepEqual(DeckBridge.parse(setMsg('120:39', false)), { type: 'set', id: '120:39', on: false });
  // 받은 객체를 그대로 돌려주지 않는다 — 덧붙은 값은 버려진다
  assert.deepEqual(DeckBridge.parse({ ...setMsg('6:0', true), html: '<img src=x onerror=alert(1)>' }), { type: 'set', id: '6:0', on: true });
});

test('DeckBridge.parse: 출처 표식·버전·종류가 다르거나 모양이 틀리면 버린다', () => {
  for (const bad of [null, undefined, 'hello', 42, [], () => {},
    { type: 'hello', cards: { 6: 2 } },                                   // source 없음
    { ...hello({ 6: 2 }), source: 'allergy-app' }, { ...hello({ 6: 2 }), v: 2 }, { ...hello({ 6: 2 }), v: '1' },
    { ...hello({ 6: 2 }), type: 'state' }, { ...hello({ 6: 2 }), type: 'eval' },
    hello(null), hello([2, 3]), hello('6:2'), hello({}),                  // 카드가 없거나 객체가 아님
    hello({ 0: 2 }), hello({ '06': 2 }), hello({ 1000: 2 }), hello({ a: 2 }), hello({ '6:1': 2 }), hello({ __proto__x: 1 }),
    hello({ 6: 0 }), hello({ 6: 41 }), hello({ 6: 1.5 }), hello({ 6: '2' }), hello({ 6: true }), hello({ 6: null }),
    setMsg('6', true), setMsg('6:', true), setMsg(':1', true), setMsg('6:100', true), setMsg('6:01', true), setMsg('0:1', true),
    setMsg('6:1;alert(1)', true), setMsg('<b>6:1</b>', true), setMsg(6, true), setMsg(null, true),
    setMsg('6:1', 'true'), setMsg('6:1', 1), setMsg('6:1', null), setMsg('6:1', undefined),
  ]) assert.equal(DeckBridge.parse(bad), null, JSON.stringify(bad));
});

test('DeckBridge.parse: 크기 한도 — 카드 60장, 카드당 40항목', () => {
  const many = (n) => Object.fromEntries(Array.from({ length: n }, (_, i) => [String(i + 1), 3]));
  assert.ok(DeckBridge.parse(hello(many(DeckBridge.MAX_CARDS))));
  assert.equal(DeckBridge.parse(hello(many(DeckBridge.MAX_CARDS + 1))), null);
  assert.ok(DeckBridge.parse(hello({ 1: DeckBridge.MAX_ITEMS })));
  assert.equal(DeckBridge.parse(hello({ 1: DeckBridge.MAX_ITEMS + 1 })), null);
});

test('DeckBridge.set: 알려 준 항목만 저장하고, 돌려주는 상태는 그 항목 전부의 불리언이다', () => {
  let store = DeckBridge.hello(undefined, { 6: 2, 9: 3 });
  assert.equal(DeckBridge.set(store, '6:1', true), true);
  assert.equal(DeckBridge.set(store, '9:2', true), true);
  assert.equal(DeckBridge.set(store, '9:2', false), true);
  for (const [id, on] of [['6:2', true], ['7:0', true], ['9:3', true], ['6:1', 'yes'], ['constructor', true], ['__proto__', true], ['6', true]]) {
    assert.equal(DeckBridge.set(store, id, on), false, id);
  }
  assert.equal(DeckBridge.set(undefined, '6:1', true), false, 'hello 를 받기 전에는 저장하지 않는다');
  const msg = DeckBridge.stateMessage(store);
  assert.deepEqual(msg, { source: 'allergy-app', v: 1, type: 'state', checks: { '6:0': false, '6:1': true, '9:0': false, '9:1': false, '9:2': false } });
  assert.deepEqual(DeckBridge.stateMessage(undefined).checks, {});
});

test('DeckBridge.hello: 같은 카드 구성이면 체크를 이어 쓰고(언어 전환), 달라졌으면 새로 시작한다', () => {
  let store = DeckBridge.hello(undefined, { 6: 2 });
  DeckBridge.set(store, '6:0', true);
  const same = DeckBridge.hello(store, { 6: 2 });
  assert.equal(same, store);
  assert.equal(DeckBridge.stateMessage(same).checks['6:0'], true);
  const changed = DeckBridge.hello(store, { 6: 3 });
  assert.notEqual(changed, store);
  assert.deepEqual(DeckBridge.stateMessage(changed).checks, { '6:0': false, '6:1': false, '6:2': false });
  assert.deepEqual(DeckBridge.stateMessage(DeckBridge.hello(store, { 7: 2 })).checks, { '7:0': false, '7:1': false });
});

test('MAIL 오류 코드마다 세 언어 안내문이 있다', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, 'shared.js'), 'utf8');
  const codes = src.match(/const MAIL_ERRORS = \[([^\]]*)\]/)[1].match(/'([a-z_]+)'/g).map(s => s.slice(1, -1));
  assert.ok(codes.length >= 9);
  for (const lang of ['ko', 'en', 'zh']) for (const c of [...codes, 'unknown', 'rate_limited_wait']) assert.ok(I18N.DICT[lang][`mail.err.${c}`], `${lang}: mail.err.${c}`);
});

test('toggleMulti: 단독 선택지는 다른 선택과 함께 고를 수 없다 — 서버 표식(exclusive)이 없으면 none·no 가 단독이다', () => {
  const work = [{ value: 'vet' }, { value: 'lab' }, { value: 'other' }, { value: 'none' }];
  assert.deepEqual(Shared.toggleMulti(work, undefined, 'vet'), ['vet']);
  assert.deepEqual(Shared.toggleMulti(work, ['vet'], 'lab'), ['vet', 'lab']);
  assert.deepEqual(Shared.toggleMulti(work, ['vet', 'lab'], 'vet'), ['lab'], '다시 누르면 빠진다');
  assert.deepEqual(Shared.toggleMulti(work, ['vet', 'lab'], 'none'), ['none'], '해당 없음을 고르면 나머지가 지워진다');
  assert.deepEqual(Shared.toggleMulti(work, ['none'], 'lab'), ['lab'], '다른 것을 고르면 해당 없음이 빠진다');
  assert.deepEqual(Shared.toggleMulti(work, ['none'], 'none'), []);
  assert.deepEqual(Shared.toggleMulti([{ value: 'yes' }, { value: 'no' }], ['yes'], 'no'), ['no']);
  const before = ['vet']; Shared.toggleMulti(work, before, 'none');
  assert.deepEqual(before, ['vet'], '넘겨받은 배열은 바꾸지 않는다');
});

test('toggleMulti: 서버가 exclusive 표식을 주면 값 이름이 아니라 표식을 따른다', () => {
  const opts = [{ value: 'agn0' }, { value: 'agn1' }, { value: 'only_others', exclusive: true }, { value: 'none', exclusive: false }];
  assert.equal(Shared.isExclusiveOption(opts[2]), true);
  assert.equal(Shared.isExclusiveOption(opts[3]), false, 'exclusive:false 로 온 none 은 단독이 아니다');
  assert.equal(Shared.isExclusiveOption({ value: 'none' }), true);
  assert.equal(Shared.isExclusiveOption({ value: 'agn0' }), false);
  assert.equal(Shared.isExclusiveOption(null), false);
  assert.deepEqual(Shared.toggleMulti(opts, ['agn0', 'agn1'], 'only_others'), ['only_others']);
  assert.deepEqual(Shared.toggleMulti(opts, ['only_others'], 'agn1'), ['agn1']);
  assert.deepEqual(Shared.toggleMulti(opts, ['agn0'], 'none'), ['agn0', 'none']);
  assert.deepEqual(Shared.toggleMulti(undefined, ['a'], 'b'), ['a', 'b'], '선택지를 모르면 보통 다중 선택으로 다룬다');
});

test('suggestionCounts: 추천 질문에 실려 온 번역 수가 응답 전체의 수보다 먼저다', () => {
  const whole = { segments: 10, untranslated: 2, errors: 0 }, own = { segments: 3, untranslated: 0, errors: 0 };
  assert.equal(Shared.suggestionCounts({ text: 'q', translation: own }, whole), own);
  assert.equal(Shared.suggestionCounts({ text: 'q' }, whole), whole);
  assert.equal(Shared.suggestionCounts({ text: 'q' }, null), null);
});

test('PDF 오류 코드마다 세 언어 안내문이 있다', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, 'shared.js'), 'utf8');
  const codes = src.match(/const PDF_ERRORS = \[([^\]]*)\]/)[1].match(/'([a-z_]+)'/g).map(s => s.slice(1, -1));
  assert.ok(['not_ready', 'lang_unavailable', 'pdf_unavailable'].every(c => codes.includes(c)));
  for (const lang of ['ko', 'en', 'zh']) {
    for (const code of [...codes, 'unknown']) assert.ok(I18N.DICT[lang][`pdf.err.${code}`], `${lang}: pdf.err.${code}`);
    for (const code of ['not_ready', 'lang_unavailable']) assert.ok(I18N.DICT[lang][`pdf.err.${code}`].includes('{target}'), `${lang}: ${code} 는 준비되지 않은 언어를 밝힌다`);
  }
});
