import { createRequire } from 'node:module';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require('/Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, 'validation');
await mkdir(out, { recursive: true });
const data = JSON.parse(await readFile(resolve(here, 'evaluation.json'), 'utf8'));
const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--disable-component-update', '--disable-features=OptimizationGuideModelDownloading,OptimizationHints,MediaRouter'] });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 }, acceptDownloads: true });
const errors = [], external = [], checks = [];
context.on('page', page => page.on('pageerror', e => errors.push(String(e))));
await context.route('**/*', async route => {
  const url = route.request().url();
  if (/^https?:/.test(url)) { external.push(url); await route.abort(); }
  else await route.continue();
});
const page = await context.newPage();
const go = name => page.goto(pathToFileURL(resolve(here, name)).href);
const check = (label, value) => { assert.ok(value, label); checks.push(label); };
const text = id => page.locator('#' + id).innerText();

try {
  await go('index.html');
  check('summary has both models', await page.locator('.model-card').count() === 2);
  check('summary numerator and denominator', (await text('mainTable')).includes('1,178 / 1,188'));
  check('summary Class recovery', (await text('mainTable')).includes('1,112 / 1,204'));
  check('source hashes with original scans and previews', await page.locator('#provenance .source-row').count() === 148);
  check('uncertain positives shown at main cards', (await text('modelCards')).includes('불확실 전사 5행 포함'));
  for (const [type, count] of [['MAST', 25], ['SPT', 4], ['UniCAP', 0], ['all', 29]]) {
    await page.selectOption('#scope', type);
    check('summary scope ' + type, (await text('scopeLabel')).startsWith(count + '장'));
    check('summary empty/recover ' + type, await page.locator('.model-card').count() === (count ? 2 : 0));
    if (count) check('summary explanation restored ' + type, await page.locator('#resultArea .note').count() === 1);
  }
  await page.screenshot({ path: resolve(out, 'summary-desktop.png'), fullPage: true });

  await page.goto(pathToFileURL(resolve(here, 'test_types.html')).href + '?type=SPT');
  check('SPT selected via query', await page.locator('[data-type="SPT"]').getAttribute('aria-selected') === 'true');
  check('SPT histamine comparison', (await text('typeContent')).includes('히스타민'));
  check('SPT missing control explained', (await text('typeContent')).includes('미판독이 양성 판정 차이 10행'));
  check('SPT has 4 document links', await page.locator('#typeContent a[href^="cases.html?id="]').count() === 8);
  await page.screenshot({ path: resolve(out, 'spt-desktop.png'), fullPage: true });
  await page.click('[data-type="UniCAP"]');
  check('UniCAP no estimate', (await text('typeContent')).includes('계산하지 않습니다'));
  await page.click('[data-type="MAST"]');
  check('MAST scope 25', (await text('typeContent')).includes('MAST · 25장'));

  await go('models.html');
  for (const [type, count] of [['all', 29], ['MAST', 25], ['SPT', 4], ['all', 29]]) {
    await page.selectOption('#scope', type);
    check('model scatter ' + type, await page.locator('#scatterPlot circle').count() === count);
    check('model time list ' + type, await page.locator('#times .time-row').count() === count);
  }
  await page.screenshot({ path: resolve(out, 'models-desktop.png'), fullPage: true });

  await page.goto(pathToFileURL(resolve(here, 'cases.html')).href + '?id=A03&filter=missing');
  check('29 scan choices', await page.locator('[data-doc]').count() === 29);
  check('A03 query selection', (await text('caseHeading')).startsWith('A03'));
  check('A03 missing row filter', await page.locator('#rowBody tr[data-row]').count() === data.documents.find(d => d.id === 'A03').rows.filter(r => ['openai', 'ollama'].some(k => r[k]?.errors.includes('missed_row'))).length);
  await page.selectOption('#rowFilter', 'all');
  await page.locator('[data-doc="A01"]').click();
  const zeroRow = data.documents.find(d => d.id === 'A01').rows.find(r => r.reference.class_value === 0);
  check('Class zero visible', zeroRow && /Class\s*0/.test(await page.locator(`[data-row="${zeroRow.uid}"] td`).nth(1).innerText()));
  check('reference app label visible', (await page.locator(`[data-row="${zeroRow.uid}"] td`).nth(1).innerText()).includes('앱 규칙: 음성'));
  let totalReferenceRows = 0;
  for (const d of data.documents) {
    await page.locator(`[data-doc="${d.id}"]`).click();
    const count = await page.locator('#rowBody tr[data-row]').count();
    check('reference rows ' + d.id, count === d.truth_rows);
    totalReferenceRows += count;
    await page.waitForFunction(() => { const i = document.getElementById('scanImage'); return i.complete && i.naturalWidth > 0; });
    check('scan embedded ' + d.id, (await page.locator('#scanImage').getAttribute('src')).startsWith('data:image/jpeg;base64,'));
  }
  check('all reference rows rendered', totalReferenceRows === 1419);
  await page.locator('[data-doc="A03"]').click();
  await page.selectOption('#rowFilter', 'issues');
  await page.locator('#rowBody tr[data-row]').first().press('Enter');
  check('keyboard row detail', (await text('rowDetail')).includes('행 연결'));
  await page.click('#zoomIn');
  check('photo zoom', (await text('zoomValue')) === '150%');
  await page.click('#fitPhoto');
  check('photo fit', (await text('zoomValue')) === '100%');
  await page.click('#openPhoto');
  check('large scan dialog', await page.locator('#photoDialog').evaluate(el => el.open));
  await page.selectOption('#dialogZoom', '200');
  check('large scan zoom', await page.locator('#dialogImage').evaluate(el => el.style.width) === '200%');
  await page.press('#closePhoto', 'Escape');
  check('dialog keyboard close', !(await page.locator('#photoDialog').evaluate(el => el.open)));
  await page.screenshot({ path: resolve(out, 'case-a03-desktop.png'), fullPage: true });

  await page.selectOption('#rowFilter', 'all');
  await page.locator('[data-doc="B11"]').click();
  await page.selectOption('#rowFilter', 'uncertain');
  check('B11 uncertain 11', await page.locator('#rowBody tr[data-row]').count() === 11);
  await page.selectOption('#rowFilter', 'all');
  await page.click('[data-row-tab="extra"]');
  check('extra/duplicate rows', await page.locator('#rowBody tr[data-row]').count() === ['openai','ollama'].reduce((n,k) => n + data.documents.find(d => d.id === 'B11').extras[k].length, 0));
  await page.click('[data-row-tab="reference"]');
  await page.fill('#rowSearch', 'no_such_allergen_1826');
  check('row search empty', await page.locator('#rowBody tr[data-row]').count() === 0);
  await page.fill('#rowSearch', '');
  await page.locator('[data-doc="O03"]').click();
  check('SPT controls shown', (await text('controlsInfo')).includes('히스타민'));
  const downloadPromise = page.waitForEvent('download');
  await page.click('#downloadCase');
  const download = await downloadPromise;
  const json = JSON.parse(await readFile(await download.path(), 'utf8'));
  check('download case JSON', json.id === 'O03' && json.rows.length === data.documents.find(d => d.id === 'O03').truth_rows);
  check('download excludes patient fields', !/"(?:patient|patient_id|patient_name|birth_date|raw_text|notes?)"\s*:/.test(JSON.stringify(json)));
  await page.screenshot({ path: resolve(out, 'case-o03-desktop.png'), fullPage: true });
  await page.selectOption('#caseType', 'SPT');
  check('SPT document filter', await page.locator('[data-doc]').count() === 4);
  await page.selectOption('#caseType', 'UniCAP');
  check('empty document filter', !(await page.locator('#workspace').isVisible()));
  await page.selectOption('#caseType', 'all');
  check('restore document filter', await page.locator('#workspace').isVisible());
  await page.fill('#caseSearch', 'no_such_case_1826');
  check('case search empty', !(await page.locator('#workspace').isVisible()));
  await page.fill('#caseSearch', 'A12');
  check('case search one', await page.locator('[data-doc]').count() === 1 && (await text('caseHeading')).startsWith('A12'));
  await page.fill('#caseSearch', '');
  await page.selectOption('#caseLab', data.documents.find(d => d.id === 'O03').lab);
  check('lab filter', await page.locator('[data-doc]').count() === data.documents.filter(d => d.lab === data.documents.find(d => d.id === 'O03').lab).length);
  await page.selectOption('#caseLab', 'all');
  await page.locator('[data-doc="A01"]').click();
  await page.click('#nextCase');
  check('next case', (await text('caseHeading')).startsWith('A02'));
  await page.click('#prevCase');
  check('previous case', (await text('caseHeading')).startsWith('A01'));

  const proseFiles = [];
  for (const file of ['index.html', 'test_types.html', 'models.html', 'cases.html']) {
    await go(file);
    const missingLinks = await page.locator('a[href]').evaluateAll(as => as.map(a => a.getAttribute('href')).filter(h => h.startsWith('#')).filter(h => !document.getElementById(h.slice(1))));
    check('anchors ' + file, !missingLinks.length);
    await page.setViewportSize({ width: 390, height: 844 });
    check('mobile viewport ' + file, await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: resolve(out, file.replace('.html', '-mobile.png')), fullPage: true });
    await page.setViewportSize({ width: 1500, height: 1000 });
    // Extract displayed prose without table labels, source paths, or executable code.
    const prose = await page.evaluate(() => {
      document.querySelectorAll('details').forEach(d => { d.open = true; });
      const walker = document.createTreeWalker(document.querySelector('main'), NodeFilter.SHOW_TEXT);
      const lines = [];
      while (walker.nextNode()) {
        const node = walker.currentNode, p = node.parentElement;
        if (p.closest('script,style,pre,code,table,.source-list,.row-card,.photo-card,.case-sidebar')) continue;
        const t = node.textContent.trim().replace(/\s+/g, ' ');
        if (t && /[가-힣]/.test(t)) lines.push(t);
      }
      return [...new Set(lines)].join('\n\n');
    });
    const prosePath = resolve(out, file.replace('.html', '-prose.txt'));
    await writeFile(prosePath, prose);
    proseFiles.push(prosePath);
  }
  await go('index.html');
  await page.emulateMedia({ media: 'print' });
  await page.pdf({ path: resolve(out, 'summary-print.pdf'), format: 'A4', printBackground: true });
  check('no JavaScript exceptions', errors.length === 0);
  check('no external requests', external.length === 0);
  const report = { verified_at: new Date().toISOString(), passed: checks.length, checks, errors, external_requests: external.length, scan_count: 29, reference_rows: totalReferenceRows, prose_files: proseFiles, privacy: 'Screenshots with scans contain identifiers. Keep validation assets local.' };
  await writeFile(resolve(out, 'verification.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ passed: checks.length, errors, external_requests: external.length, output: out }));
} finally { await browser.close(); }
