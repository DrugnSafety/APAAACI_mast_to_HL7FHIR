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
