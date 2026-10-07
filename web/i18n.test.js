'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const I18N = require('./i18n.js');

test('every language has exactly the same key set as ko', () => {
  const koKeys = Object.keys(I18N.DICT.ko).sort();
  for (const lang of ['en', 'zh']) {
    const keys = Object.keys(I18N.DICT[lang]).sort();
    const missing = koKeys.filter(k => !keys.includes(k));
    const extra = keys.filter(k => !koKeys.includes(k));
    assert.deepEqual(missing, [], `${lang} missing keys`);
    assert.deepEqual(extra, [], `${lang} extra keys`);
  }
});

test('placeholders match across languages', () => {
  const ph = (s) => (String(s).match(/\{\w+\}/g) || []).sort().join(',');
  for (const [k, v] of Object.entries(I18N.DICT.ko)) {
    for (const lang of ['en', 'zh']) assert.equal(ph(I18N.DICT[lang][k]), ph(v), `${lang}:${k} placeholders`);
  }
});

test('t() interpolates and falls back to ko, then key', () => {
  I18N.setLang('en');
  assert.equal(I18N.t('hud.level', { n: 2, title: 'Seasoned Explorer' }), 'Lv.2 Seasoned Explorer');
  assert.equal(I18N.t('nonexistent.key'), 'nonexistent.key');
  I18N.setLang('zh');
  assert.equal(I18N.t('s1.pos_summary', { n: 3 }), '🧭 发现 3 个阳性线索 — 下一任务将鉴别其中的真正元凶。');
  assert.equal(I18N.setLang('xx'), 'zh', 'unknown language keeps current');
  I18N.setLang('ko');
  assert.equal(I18N.t('verdict.clinically_relevant.stamp'), '진범 확정');
});

test('LANGS covers ko/en/zh with html lang tags', () => {
  assert.deepEqual(I18N.LANGS.map(l => l.code), ['ko', 'en', 'zh']);
  assert.equal(I18N.LANGS[2].html, 'zh-CN');
});

test('translationState: decided from the translation counts and stored status, not the requested language', () => {
  const st = I18N.translationState;
  assert.equal(st('ko', { segments: 0, untranslated: 0, errors: 0 }, 'ready'), 'ok');
  assert.equal(st('ko', { segments: 5, untranslated: 5 }, 'skipped'), 'ok', 'Korean is the original');
  assert.equal(st('en', { segments: 639, untranslated: 0, errors: 0 }, 'ready'), 'ok');
  assert.equal(st('en', { segments: 639, untranslated: 639, errors: 0 }, 'skipped'), 'none');
  assert.equal(st('zh', { segments: 444, untranslated: 159, errors: 0 }, 'skipped'), 'partial');
  assert.equal(st('en', { segments: 10, untranslated: 1, errors: 0 }, 'failed'), 'partial');
  assert.equal(st('en', { segments: 10, untranslated: 0, errors: 2 }, 'failed'), 'partial', 'errors without a count');
  assert.equal(st('en', { segments: 0, untranslated: 3 }), 'none', 'more missing than counted');
  // storage off: no i18n status, counts only
  assert.equal(st('en', { segments: 503, untranslated: 503, errors: 0 }, undefined), 'none');
  // stored outputs (ready) carry neither
  assert.equal(st('en', null, undefined), 'ok');
  assert.equal(st('en', null, 'ready'), 'ok');
  assert.equal(st('en', null, 'skipped'), 'none');
  assert.equal(st('en', null, 'failed'), 'partial');
  assert.equal(st('en', { segments: 10, untranslated: 0, errors: 0 }, 'pending'), 'ok');
});

test('textState / questionnaireState: Hangul left in the shown strings', () => {
  assert.equal(I18N.textState('en', ['When do symptoms appear?', 'Yes', 'No']), 'ok');
  assert.equal(I18N.textState('en', ['증상은 언제 나타나나요?', '예']), 'none');
  assert.equal(I18N.textState('zh', ['症状什么时候出现？', '예', '否']), 'partial', 'Han characters are not Hangul');
  assert.equal(I18N.textState('ko', ['증상은 언제 나타나나요?']), 'ok');
  assert.equal(I18N.textState('en', [null, '', '  ', undefined]), 'ok');
  assert.equal(I18N.textState('en', null), 'ok');
  const q = (title, label) => ({ sections: [{ title: 'Timing', subtitle: null, questions: [{ title, help: undefined, options: [{ label, hint: null }] }] }] });
  assert.deepEqual(I18N.questionnaireTexts(q('A?', 'Yes')).filter(Boolean), ['Timing', 'A?', 'Yes']);
  assert.deepEqual(I18N.questionnaireTexts(null), []);
  assert.equal(I18N.questionnaireState('en', { questionnaire: q('A?', 'Yes') }), 'ok');
  assert.equal(I18N.questionnaireState('en', { questionnaire: q('언제인가요?', 'Yes') }), 'partial');
  assert.equal(I18N.questionnaireState('en', { questionnaire: { sections: [{ title: '시기', questions: [{ title: '언제인가요?', options: [{ label: '예' }] }] }] } }), 'none');
  // a translation count from the server wins over the text heuristic
  assert.equal(I18N.questionnaireState('en', { questionnaire: q('언제인가요?', '예'), translation: { segments: 3, untranslated: 0, errors: 0 } }), 'ok');
  assert.equal(I18N.questionnaireState('en', { questionnaire: q('A?', 'Yes'), translation: { segments: 3, untranslated: 3, errors: 0 } }), 'none');
});

test('noticeKey: the machine-translated notice only when translation happened', () => {
  assert.equal(I18N.noticeKey('ko', 'none', false), null);
  assert.equal(I18N.noticeKey('en', 'ok', false), 'notice.partial', 'served from the translation cache');
  assert.equal(I18N.noticeKey('en', 'ok', true), 'notice.partial');
  assert.equal(I18N.noticeKey('en', 'partial', true), 'notice.mixed');
  assert.equal(I18N.noticeKey('zh', 'none', false), 'notice.untranslated');
  assert.equal(I18N.noticeKey('zh', 'none', true), 'notice.untranslated');
  assert.equal(I18N.noticeKey('en', null, false), 'notice.unavailable', 'nothing fetched yet and no engine');
  assert.equal(I18N.noticeKey('en', null, true), 'notice.partial');
  for (const lang of ['ko', 'en', 'zh']) {
    for (const k of ['notice.mixed', 'notice.untranslated', 'notice.unavailable', 'lang.partial', 'lang.retry_fail', 'eng.unavailable', 'eng.none']) {
      assert.ok(I18N.DICT[lang][k], `${lang}:${k}`);
    }
  }
});


test('answerState: a chat answer that came back in Korean under a non-Korean screen is flagged', () => {
  const st = I18N.answerState;
  const ko = '고양이 비듬 검사가 양성이지만 접촉과 증상에 관한 정보가 부족해 판단을 미뤘습니다.';
  const mixed = '집먼지진드기, 고양이 비듬 were left under watch because the answers so far do not show whether exposure brings on symptoms.';
  const en = 'No allergen was clearly linked to your symptoms in this questionnaire.';
  assert.equal(st('ko', ko, null), 'ok', 'Korean screen: Korean is the original');
  assert.equal(st('en', en, null), 'ok');
  assert.equal(st('zh', '猫皮屑检测呈阳性，但信息不足。', null), 'ok', 'Han characters are not Hangul');
  // translation 이 없을 때(지금 서버): 글자로 판단
  assert.equal(st('en', ko, null), 'none');
  assert.equal(st('en', mixed, undefined), 'partial');
  // translation 이 실려 오면 그것을 먼저 믿는다
  assert.equal(st('en', mixed, { segments: 9, untranslated: 0, errors: 0 }), 'ok', 'all translated: leftover Hangul are proper names');
  assert.equal(st('en', ko, { segments: 9, untranslated: 0, errors: 0 }), 'none', 'a wholly Korean answer is flagged even if the count says otherwise');
  assert.equal(st('en', mixed, { segments: 9, untranslated: 9, errors: 0 }), 'none');
  assert.equal(st('en', en, { segments: 9, untranslated: 9, errors: 0 }), 'ok', 'nothing Korean in this answer');
  assert.equal(st('en', mixed, { segments: 9, untranslated: 2, errors: 0 }), 'partial');
  assert.equal(st('en', ko, { segments: 9, untranslated: 2, errors: 0 }), 'none');
  assert.equal(st('en', mixed, { segments: 9, untranslated: 0, errors: 1 }), 'partial');
  assert.equal(st('en', null, null), 'ok');
  assert.equal(st('en', '', { segments: 1, untranslated: 1 }), 'ok');
  for (const lang of ['ko', 'en', 'zh']) for (const k of ['chat.tr_notice', 'chat.tr_partial', 'chat.tr_none']) assert.ok(I18N.DICT[lang][k], `${lang}:${k}`);
});

test('suggestionsState: per-suggestion counts first, then the whole-response counts, then the Hangul heuristic', () => {
  const S = I18N.suggestionsState;
  const ok = { segments: 2, untranslated: 0, errors: 0 }, miss = { segments: 2, untranslated: 2, errors: 0 }, half = { segments: 2, untranslated: 1, errors: 0 };
  // 질문마다 실려 온 수 — 응답 전체의 수(답변까지 센 값)보다 먼저 본다
  assert.equal(S('en', [{ text: 'a', translation: ok }, { text: 'b', translation: ok }], miss), 'ok');
  assert.equal(S('en', [{ text: 'a', translation: ok }, { text: 'b', translation: miss }], ok), 'partial');
  assert.equal(S('en', [{ text: 'a', translation: miss }, { text: 'b', translation: miss }], ok), 'none');
  assert.equal(S('zh', [{ text: 'a', translation: half }], ok), 'partial');
  // 번역할 문장이 없던 질문(0/0 — 이미 화면 언어로 준비된 문구)은 번역된 것으로 센다
  assert.equal(S('en', [{ text: 'a', translation: { segments: 0, untranslated: 0 } }, { text: 'b', translation: { segments: 4, untranslated: 4 } }], miss), 'partial');
  assert.equal(S('en', [{ text: 'a', translation: { segments: 0, untranslated: 0 } }], miss), 'ok');
  // 일부 질문에만 실려 왔으면 믿지 않고 응답 전체의 수를 쓴다
  assert.equal(S('en', [{ text: 'a', translation: ok }, { text: 'b' }], miss), 'none');
  assert.equal(S('en', [{ text: 'a' }], half), 'partial');
  // 둘 다 없으면 문장에 남은 한글
  assert.equal(S('en', [{ text: 'What matters most?', answer: 'Cat dander' }], null), 'ok');
  assert.equal(S('en', [{ text: '무엇이 가장 중요한가요?', answer: '고양이 비듬' }], null), 'none');
  assert.equal(S('en', [{ text: 'What?', answer: '고양이' }], undefined), 'partial');
  assert.equal(S('ko', [{ text: '질문', translation: miss }], miss), 'ok');
  assert.equal(S('en', [], null), 'ok');
  assert.equal(S('en', null, miss), 'none');
});
