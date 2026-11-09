/* Capture the real local UI with synthetic inputs. Only /api/ocr is a fixed fixture. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const BASE = process.env.MANUAL_APP_URL || 'http://127.0.0.1:18125';
const DIR = path.join(__dirname, 'assets');
const fixture = JSON.parse(fs.readFileSync(path.join(DIR, 'sample_ocr.json')));
const manifest = { date: '2026-10-29', viewport: { width: 1440, height: 1000 }, origin: BASE, method: 'Real application UI; synthetic inputs; only the OCR response uses a fixed training fixture. All subsequent APIs run locally.', scenes: [], errors: [] };
let page, count = 0;
const imageHashes = new Map();
const saveManifest = () => fs.writeFileSync(path.join(DIR, 'screens.json'), JSON.stringify(manifest, null, 2));
async function settle() {
  await page.evaluate(async () => {
    await document.fonts.ready;
    for (const a of document.getAnimations()) { try { a.finish(); } catch (_) {} }
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  });
}
async function shot(tag) {
  await settle();
  await page.mouse.move(10, 990);
  const filename = `screen_${String(++count).padStart(3, '0')}_${tag}.jpg`;
  const pixels = await page.screenshot({ type: 'jpeg', quality: 88, animations: 'disabled' });
  const hash = crypto.createHash('sha256').update(pixels).digest('hex');
  if (imageHashes.has(hash)) return imageHashes.get(hash);
  fs.writeFileSync(path.join(DIR, filename), pixels);
  imageHashes.set(hash, filename);
  return filename;
}
async function scene(meta, actions = [], nav) {
  if (meta.scroll) await page.locator(meta.scroll).first().evaluate(el => el.scrollIntoView({block:'start'}));
  else await page.evaluate(() => window.scrollTo(0, 0));
  await settle();
  const s = { ...meta, number: manifest.scenes.length + 1, actions: [], image: await shot('before') };
  for (const a of actions) {
    const loc = a.frame ? page.frameLocator(a.frame).locator(a.selector).first() : page.locator(a.selector).first();
    await loc.waitFor({ state: 'visible' });
    await loc.scrollIntoViewIfNeeded();
    const box = await loc.boundingBox();
    if (box.y < 0 || box.y + box.height > 1000) throw new Error('Offscreen: ' + a.selector);
    // If an input caused scrolling, use the actual current screen as the animation base.
    const before = await shot('focus');
    if (a.kind === 'select') await loc.selectOption(a.value);
    else if (a.kind === 'type') await loc.fill(String(a.value));
    else if (a.kind === 'check') await loc.check();
    else if (a.kind === 'upload') await loc.setInputFiles(path.join(DIR, 'sample_test_sheet.png'));
    else if (a.kind === 'inspect') { /* Actual screen, highlighted without a state change. */ }
    else {
      if (a.waitPath) await Promise.all([page.waitForResponse(r=>r.url().includes(a.waitPath)), loc.click()]);
      else await loc.click();
      if (['#vObs','#vAllergy','#vScreening'].includes(a.selector)) await page.locator('#fhirPreview').filter({hasText:'resourceType'}).waitFor();
    }
    await settle();
    s.actions.push({ ...a, box, before, after: await shot('after') });
  }
  if (nav) {
    const loc = page.locator(nav.selector).first();
    await loc.scrollIntoViewIfNeeded();
    s.actions.push({ kind: 'navigate', ...nav, box: await loc.boundingBox(), before: await shot('next'), after: null });
  }
  manifest.scenes.push(s);
  saveManifest();
  console.log(`Captured ${s.number}: ${s.title} (${s.actions.length})`);
  if (nav) await page.locator(nav.selector).first().click();
}
const next = (value = '다음 화면으로 이동') => ({ selector: '#next', label: '다음 버튼', value, tip: '입력을 확인한 뒤 누르세요.' });
async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: false, args: ['--disable-component-update', '--disable-features=OptimizationGuideModelDownloading,OptimizationHints,MediaRouter'] });
  try {
    const context = await browser.newContext({ viewport: manifest.viewport, reducedMotion: 'reduce', colorScheme: 'light', locale: 'ko-KR' });
    page = await context.newPage();
    page.on('pageerror', e => manifest.errors.push(e.message));
    await page.route(BASE + '/api/ocr', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(fixture) }));
    await page.goto(BASE);
    await page.locator('#btnStart').waitFor();
    await scene({ group: '시작', title: '탐험을 시작하세요', lead: '첫 화면에서 시작 버튼을 누르세요.', notes: ['이 매뉴얼은 기본 퀘스트 화면을 사용합니다.', '상단 언어 선택에서 한국어를 고르세요.', '점수는 진행 상황을 나타냅니다. 검사 수치가 아닙니다.'], source: 'web/app.js:279' }, [], { selector: '#btnStart', label: '탐험 시작', value: '탐험 시작 →', tip: '이 버튼을 눌러 검사 결과를 입력하세요.' });
    await page.locator('#file').waitFor({state:'attached'});
    // The upload response is the only controlled API response in this capture.
    await scene({ group: '검사 입력', title: '검사 결과지 사진을 올리세요', lead: 'JPG 또는 PNG 사진을 선택하세요.', notes: ['기본 파일 한도는 10MB입니다.', '글자와 수치가 모두 보이게 촬영하세요.', 'PDF는 이미지로 바꾼 뒤 올리세요.', '사진 인식을 사용할 수 없으면 데모 또는 직접 입력을 사용하세요.'], source: 'server.py:612 · web/app.js:298', sample: true }, [ { selector: '#dz', kind: 'inspect', label: '사진 선택', value: 'sample_test_sheet.png', tip: '업로드 영역을 누른 뒤 사진을 선택하세요.' }, { selector: '#btnDemo', kind: 'inspect', label: '연습 방법', value: '연습 탐험 시작 (데모 데이터)', tip: '검사지가 없어도 같은 흐름을 연습할 수 있습니다.' } ]);
    await page.locator('#file').setInputFiles(path.join(DIR, 'sample_test_sheet.png'));
    await page.locator('#testType').waitFor();
    await scene({ group: '검사 확인', title: '소수점과 항원 이름을 확인하세요', lead: '원본을 보고 잘못 읽은 값만 고치세요.', scroll: '#tbody', notes: ['이 예제의 원본 수치는 17.6입니다.', '연습용 OCR 응답에는 1.76이 들어 있습니다.', '수치를 고치면 Class와 양성 표시가 다시 계산됩니다.', '검토 표시가 남은 행은 원본과 대조하세요.'], source: 'web/app.js:463 · services/ocr_validation.py:87' }, [ { selector: '#testType', kind: 'inspect', label: '검사 종류', value: 'MAST', tip: '원본 검사지의 검사 종류와 같아야 합니다.' }, { selector: '#tbody tr[data-i="0"] input[data-f="value"]', kind: 'type', label: '집먼지진드기 수치', value: '17.6', tip: '1.76을 17.6으로 고치세요. 소수점을 확인하세요.' } ]);
    await page.locator('[data-rvtab="measured"]').click();
    // Remaining flagged names are checked against the synthetic original sheet.
    while (await page.locator('[data-ok]').count()) {
      const row = await page.locator('[data-ok]').first().getAttribute('data-ok');
      const rowName = row === '0' ? '집먼지진드기 수치와 Class' : 'Ragweed pollen 이름과 수치';
      await scene({ group: '검사 확인', title: '검토 표시가 있는 행을 확인하세요', lead: '원본과 같은 항목이면 확인을 누르세요.', scroll: '[data-ok]', notes: ['목록에서 이름을 찾지 못해도 검사 오류로 확정하지 않습니다.', '항원 이름과 수치를 원본에서 확인하세요.', '원본을 확인한 뒤 확인 버튼을 누르세요.'], source: 'web/app.js:597' }, [{ selector: '[data-ok]', kind: 'click', label: '행 검토', value: '확인', tip: rowName + '를 원본과 대조한 뒤 누르세요.' }]);
    }
    await scene({ group: '검사 확인', title: '원본 대조를 마치고 등록하세요', lead: '행 확인과 원본 대조 확인을 모두 마치세요.', scroll: '#rvConfirm', notes: ['양성 항목만 보지 말고 모든 행을 확인하세요.', '수치 0·미측정 탭도 확인하세요.', '빈 행과 중복 행을 확인하세요.', '양성 항목이 1개 이상 있어야 다음으로 이동합니다.'], source: 'web/app.js:424' }, [{ selector: '#rvConfirm', kind: 'check', label: '원본 대조 확인', value: '원본과 대조했습니다', tip: '환자 정보, 검사일, 항원, 수치를 대조한 뒤 체크하세요.' }], next('발견 등록 →'));
    await page.locator('#overlay:not(.hidden)').waitFor();
    console.log('Discovery buttons:', await page.locator('#overlay button').allTextContents());
    await page.locator('#overlay button').last().click();
    await page.locator('#pName').waitFor();
    await scene({ group: '기본 문진', title: '기본 정보를 입력하세요', lead: '검사를 받은 사람의 정보를 입력하세요.', notes: ['화면의 예시는 모두 합성 정보입니다.', '검사일은 오늘 날짜가 아니라 실제 검사일입니다.', '이름을 바꾸어도 검사 수치는 바뀌지 않습니다.'], source: 'web/app.js:717' }, [
      { selector: '#pName', kind: 'type', label: '이름', value: '연습사용자', tip: '검사를 받은 사람을 구분할 이름을 입력하세요.' },
      { selector: '#pAge', kind: 'type', label: '나이', value: '34', tip: '검사를 받은 사람의 나이를 입력하세요.' },
      { selector: '#pGender', kind: 'select', label: '성별', value: 'F', display: '여', tip: '검사지와 같은 성별을 선택하세요.' },
      { selector: '#pDate', kind: 'type', label: '검사일', value: '2026-10-20', tip: '검사지에 적힌 검사일을 선택하세요.' },
    ], next());
    await page.locator('#resCountry').waitFor();
    await scene({ group: '기본 문진', title: '현재 사는 지역을 선택하세요', lead: '거주 지역은 꽃가루 계절을 해석할 때 사용합니다.', notes: ['예시는 미국 보스턴 지역입니다.', '대한민국을 고르면 전국 기준을 사용합니다.', '지역을 모르면 미선택으로 진행할 수 있습니다.', '화면의 계절 정보는 실시간 꽃가루 관측값이 아닙니다.'], source: 'web/app.js:732 · data/pollen_calendar_regional.json' }, [
      { selector: '#resCountry', kind: 'select', label: '국가', value: 'US', display: '미국', tip: '현재 사는 나라를 선택하세요.' },
      { selector: '#resRegion', kind: 'select', label: '권역', value: 'NORTHEAST', display: '북동부', tip: '현재 사는 권역을 선택하세요.' },
      { selector: '#resZip', kind: 'type', label: '미국 우편번호', value: '02446', tip: '미국은 5자리 ZIP 코드를 입력할 수 있습니다.' },
    ], next());
    await scene({ group: '기본 문진', title: '진단받은 질환을 선택하세요', lead: '이미 진단받은 질환만 선택하세요.', notes: ['여러 항목을 함께 선택할 수 있습니다.', '검사 양성만으로 질환을 선택하지 마세요.', '질환이 없으면 해당 없음을 선택하세요.'], source: 'services/screening_service.py:23' }, [{ selector: '[data-chipgroup="allergic_diseases"] [data-code="allergic_rhinitis"]', kind: 'click', label: '질환', value: '알레르기 비염', tip: '연습 사례는 알레르기 비염을 진단받았습니다.' }], next());
    await scene({ group: '기본 문진', title: '현재 사용하는 약을 선택하세요', lead: '지금 사용하는 약을 모두 선택하세요.', notes: ['약 이름을 모르면 처방전이나 약 봉투를 확인하세요.', '복용 안 함은 다른 약 항목과 함께 선택하지 않습니다.', '약을 중단하거나 바꾸는 결정은 의료진과 상의하세요.'], source: 'web/app.js:755 · services/screening_service.py:38' }, [
      { selector: '[data-chipgroup="current_medications"] [data-code="antihistamine"]', kind: 'click', label: '먹는 약', value: '항히스타민제', tip: '연습 사례는 항히스타민제를 사용합니다.' },
      { selector: '[data-chipgroup="current_medications"] [data-code="nasal_steroid"]', kind: 'click', label: '코 스프레이', value: '비강 스테로이드 스프레이', tip: '현재 사용하는 코 스프레이도 선택하세요.' },
    ], next());
    await scene({ group: '기본 문진', title: '증상이 나타나는 부위를 선택하세요', lead: '현재 증상이 있는 부위를 모두 선택하세요.', notes: ['예시는 코와 눈에 증상이 있습니다.', '검사에서 양성인 부위를 고르는 화면이 아닙니다.', '실제로 경험한 증상에 맞춰 선택하세요.'], source: 'web/app.js:757' }, [
      { selector: '[data-chipgroup="organ_systems"] [data-code="nasal"]', kind: 'click', label: '증상 부위 1', value: '코 (재채기·콧물·코막힘)', tip: '재채기와 콧물이 있으므로 코를 선택합니다.' },
      { selector: '[data-chipgroup="organ_systems"] [data-code="ocular"]', kind: 'click', label: '증상 부위 2', value: '눈 (가려움·충혈·눈물)', tip: '눈 가려움이 있으므로 눈도 선택합니다.' },
    ], next());
    await scene({ group: '기본 문진', title: '함께 사는 동물을 선택하세요', lead: '집에서 함께 사는 동물을 선택하세요.', notes: ['예시는 집에 동물이 없습니다.', '동물을 키우지 않아도 외부에서 노출될 수 있습니다.', '외부 노출은 뒤의 항원별 문진에서 답하세요.'], source: 'web/app.js:760' }, [{ selector: '[data-chipgroup="pets"] [data-code="none"]', kind: 'click', label: '반려동물', value: '없음', tip: '예제에서는 없음을 선택합니다.' }], next('감별 문진으로 이동'));
    await page.locator('[data-qid], .quest-summary').first().waitFor();
    fs.writeFileSync(path.join(DIR, 'questionnaire.json'), JSON.stringify(await page.evaluate(() => ({ questionnaire: S.questionnaire, screening: S.screening })),null,2));
    const choices = {
      symptom_pattern: ['both', '증상이 나타나는 시기를 선택하세요', '연습 사례는 연중 증상이 있고 봄에 더 심합니다.'],
      worse_seasons: ['spring', '증상이 심한 계절을 선택하세요', '연습 사례는 봄에 증상이 더 심합니다.'],
      pollen_season__spring_tree: ['yes', '봄철 코·눈 증상을 답하세요', '연습 사례는 봄에 재채기와 눈 가려움이 늘어납니다.'],
      severity__pollen_spring_tree: ['mild', '봄철 증상의 정도를 선택하세요', '연습 사례는 일상에 큰 지장이 없는 경증입니다.'],
      pollen_season__fall_weed: ['no', '가을철 코·눈 증상을 답하세요', '연습 사례는 가을에 더 심해지지 않습니다.'],
      indoor_timing: ['yes', '실내 증상의 시간대를 답하세요', '연습 사례는 자는 동안과 아침에 증상이 더 심합니다.'],
      indoor_away: ['yes', '집을 떠났을 때의 변화를 답하세요', '연습 사례는 며칠 집을 비우면 증상이 줄어듭니다.'],
      mite_dust: ['yes', '먼지 청소 때의 증상을 답하세요', '연습 사례는 침구를 털 때 재채기가 늘어납니다.'],
      severity__indoor: ['moderate', '실내 증상의 정도를 선택하세요', '연습 사례는 증상 때문에 수면에 지장이 있습니다.'],
      animal_contact__agn3: ['rare', '동물과의 접촉 빈도를 선택하세요', '연습 사례는 고양이를 1년에 몇 번 이하로 만납니다.'],
      animal_worse__agn3: ['unsure', '동물 접촉 후의 변화를 답하세요', '확실히 기억하지 못하면 잘 모르겠어요를 고르세요.'],
      animal_work: ['none', '일할 때의 동물 노출을 답하세요', '연습 사례는 업무로 동물을 다루지 않습니다.'],
      oral_allergy_syndrome: ['no', '음식을 먹은 뒤 입·목 증상을 답하세요', '연습 사례는 해당 증상을 경험하지 않았습니다.'],
      crossreact__agn0: ['none', '집먼지진드기와 관련한 음식 문진입니다', '연습 사례는 나열된 음식으로 증상을 겪지 않았습니다.'],
      crossreact__agn2: ['none', '자작나무와 관련한 음식 문진입니다', '먹었을 때 증상이 있었던 음식만 선택하세요.'],
      crossreact__agn3: ['none', '고양이와 관련한 음식 문진입니다', '연습 사례는 나열된 음식으로 증상을 겪지 않았습니다.'],
      food_general_react: ['none', '추가로 반응한 음식이 있는지 답하세요', '연습 사례는 추가로 반응한 음식이 없습니다.'],
      food_systemic: ['no', '음식 후 전신 증상을 답하세요', '연습 사례는 음식으로 전신 증상을 겪지 않았습니다.'],
    };
    for (let limit=0;limit<60;limit++) {
      const block = page.locator('[data-qid]').first();
      if (!(await block.count())) break;
      const id = await block.getAttribute('data-qid');
      const q = await page.evaluate(id => S.questionnaire.sections.flatMap(s=>s.questions).find(q=>q.id===id), id);
      const chosen = choices[id];
      if (!chosen) throw new Error('Unspecified question: ' + id);
      const opt = q.options.find(o=>o.value===chosen[0]);
      const selector = `[data-${q.type === 'multi' ? 'multi' : 'single'}="${id}"] [data-v="${chosen[0]}"]`;
      await scene({ group: '감별 문진', title: chosen[1], lead: chosen[2], notes: [q.type === 'multi' ? '복수 선택입니다. 해당 항목을 모두 고르세요.' : '단일 선택입니다. 1개만 고르세요.', '예제의 답을 정답으로 사용하지 마세요.', '질문은 검사 결과와 앞선 답에 따라 달라집니다.'], questionId: id, source: 'web/app.js:939 · services/questionnaire_service.py' }, [{ selector, kind: 'click', label: q.type === 'multi' ? '복수 선택' : '답변', value: opt.label, tip: chosen[2] }], next());
    }
    await scene({ group: '결과 만들기', title: '답변 요약을 확인하세요', lead: '입력한 답을 읽고 수정할 항목을 확인하세요.', scroll: '#stageTitle', notes: ['수정할 답을 누르면 해당 질문으로 돌아갑니다.', '건너뛴 항목은 확실한 답으로 처리하지 않습니다.', '다 확인했으면 도감 완성하기를 누르세요.'], source: 'web/app.js:1063' }, [], next('도감 완성하기 →'));
    await page.locator('.result-tabs').waitFor({timeout:60000});
    const outputs = await page.evaluate(() => ({ summary: S.classify.summary, assessments: S.classify.assessments, screening: S.screening, answers: S.answers }));
    fs.writeFileSync(path.join(DIR, 'sample_result.json'), JSON.stringify(outputs,null,2));
    await scene({ group: '결과 보기', title: '결과 요약을 읽으세요', lead: '화면의 3가지 분류를 각각 확인하세요.', notes: ['화면의 진범 확정은 앱의 규칙에 따른 분류입니다.', '의료진의 최종 진단을 뜻하지 않습니다.', '집먼지진드기 2종은 1개 묶음으로 표시할 수 있습니다.', '관찰 대상은 정보가 부족하거나 답이 불확실한 항목입니다.'], source: 'web/app.js:1123 · services/questionnaire_service.py' }, [{ selector: '.trophies', kind: 'inspect', label: '분류 요약', value: '실제 주의 · 감작만 · 관찰 필요', tip: '검사 수치와 문진 답을 함께 해석한 결과입니다.' }]);
    await scene({ group: '결과 보기', title: '항원별 도감 카드를 확인하세요', lead: '카드에서 판정 이유와 생활 안내를 읽으세요.', scroll: '.result-tabs', notes: ['카드를 선택하면 자세한 내용을 볼 수 있습니다.', '감작 수치가 높아도 증상이 심하다는 뜻은 아닙니다.', '생활 안내를 의료진과 함께 검토하세요.'], source: 'web/app.js:1343' }, [{ selector: '.dex-card', kind: 'inspect', label: '항원별 결과', value: '판정 이유와 관리 안내', tip: '카드에 적힌 항원 이름과 판정 근거를 확인하세요.' }]);
    await page.locator('.result-tabs [data-tab="report"]').click();
    await page.locator('#rpt').waitFor();
    await scene({ group: '리포트', title: '맞춤 리포트를 읽으세요', lead: '검사 결과와 문진을 한 문서에서 확인하세요.', scroll: '.result-tabs', notes: ['먼저 요약과 지금 할 일을 읽으세요.', '검사 수치와 판정 이유를 확인하세요.', '리포트는 의료진 상담 때 함께 보여 주세요.'], source: 'web/app.js:1194 · services/report_service.py' }, [{ selector: '#rpt', kind: 'inspect', label: '리포트', value: '요약 → 판정 이유 → 관리 안내', tip: '문서 안에서 아래로 내려가며 읽으세요.' }]);
    await scene({ group: '리포트', title: '리포트를 파일로 저장하세요', lead: '보관할 형식의 저장 버튼을 누르세요.', scroll: '.download-row', notes: ['HTML은 브라우저에서 다시 열 수 있습니다.', 'Markdown은 글을 편집할 때 사용합니다.', '인쇄 화면에서는 PDF로 저장을 선택할 수 있습니다.', '서버 PDF 기능이 켜지면 PDF 받기도 표시됩니다.'], source: 'web/app.js:1190 · web/shared.js:566' }, [{ selector: '#dlHtml', kind: 'inspect', label: 'HTML 저장', value: 'HTML 받기', tip: '원본 레이아웃으로 리포트를 보관하세요.' }, { selector: '#dlMd', kind: 'inspect', label: 'Markdown 저장', value: 'Markdown 받기', tip: '텍스트 편집이 필요할 때 선택하세요.' }, { selector: '#dlPdf', kind: 'inspect', label: '인쇄', value: '인쇄 / PDF', tip: '인쇄 화면에서 PDF로 저장을 선택하세요.' }]);
    await page.locator('.result-tabs [data-tab="cardnews"]').click();
    await page.frameLocator('#cn').locator('#cnNext').waitFor();
    await scene({ group: '카드뉴스', title: '카드뉴스를 한 장씩 넘겨 보세요', lead: '짧은 카드에서 결과와 관리 안내를 확인하세요.', scroll: '.result-tabs', notes: ['다음 화살표로 카드를 넘기세요.', '퀴즈와 실천 항목은 카드 안내를 따라 선택하세요.', 'HTML로 저장하면 같은 카드뉴스를 다시 볼 수 있습니다.'], source: 'web/app.js:1219 · services/cardnews_service.py' }, [{ frame: '#cn', selector: '#cnNext', kind: 'click', label: '다음 카드', value: '다음 화살표 →', tip: '화살표를 누르면 실제 카드가 바뀝니다.' }, { frame: '#cn', selector: '#cnNext', kind: 'click', label: '다음 카드', value: '한 번 더 넘기기 →', tip: '내 결과와 연결한 설명을 순서대로 읽으세요.' }, { selector: '#dlCn', kind: 'inspect', label: '카드뉴스 저장', value: '카드뉴스 HTML 받기', tip: '가족이나 의료진과 공유하기 전에 내용을 확인하세요.' }]);
    await page.locator('.result-tabs [data-tab="chat"]').click();
    await page.locator('[data-sg]').first().waitFor({timeout:30000});
    const suggestion = await page.locator('[data-sg]').first().innerText();
    await scene({ group: '결과 상담', title: '추천 질문으로 결과를 확인하세요', lead: '궁금한 추천 질문을 누르세요.', scroll: '.result-tabs', notes: ['추천 질문은 이 결과를 바탕으로 답합니다.', '자유 질문은 서비스에서 AI 상담을 켠 경우에 답합니다.', '상담 답변으로 진단이나 처방을 바꾸지 마세요.'], source: 'web/shared.js:622' }, [{ selector: '[data-sg]', kind: 'click', label: '추천 질문', value: suggestion, tip: '질문을 누르면 결과에 맞춘 설명이 나타납니다.' }]);
    await scene({ group: '선택 기능', title: '이메일로 기록을 연결할 수 있습니다', lead: '기록 연결이 필요하면 본인 이메일을 입력하세요.', scroll: '#mailBox', notes: ['예시는 발송하지 않는 연습용 주소입니다.', '기록 연결 버튼은 이메일을 발송하지 않습니다.', '메일 기능이 켜진 서비스는 이메일 받기를 표시합니다.', '기능을 사용하지 않으면 파일 저장으로 마칠 수 있습니다.'], source: 'web/shared.js:481' }, [ { selector: '#mailTo', kind: 'type', label: '이메일 주소', value: 'practice@example.test', tip: '실제 사용할 때는 본인 이메일을 확인해 입력하세요.' }, { selector: '#mailSend', kind: 'click', label: '기록 연결', value: '기록 연결', waitPath: '/identify', tip: '이 예제에서는 임시 저장소의 기록만 연결합니다.' } ]);
    await page.locator('.result-tabs [data-tab="fhir"]').click();
    await page.locator('#fhirPreview').filter({hasText:'resourceType'}).waitFor();
    await scene({ group: 'FHIR 내보내기', title: '의료기관이 요청하면 JSON을 저장하세요', lead: '필요한 FHIR 결과의 저장 버튼을 누르세요.', scroll: '.result-tabs', notes: ['FHIR는 의료정보를 교환하는 형식입니다.', 'Observation은 검사 측정 결과를 담습니다.', 'AllergyIntolerance는 앱의 알레르기 위험 평가를 담습니다.', '파일 저장만으로 병원 전송을 완료하지 않습니다.'], source: 'web/app.js:1230 · services/fhir_service.py' }, [{ selector: '#vObs', kind: 'click', label: '검사 결과 보기', value: 'Observation', tip: '양성과 음성을 포함한 검사 결과를 확인하세요.' }, { selector: '#dlObs', kind: 'inspect', label: '검사 결과 저장', value: 'Observation JSON', tip: '의료기관이 요청한 경우 파일로 저장하세요.' }, { selector: '#vAllergy', kind: 'click', label: '위험 평가 보기', value: 'AllergyIntolerance', tip: '앱이 만든 위험 평가와 상태를 확인하세요.' }, { selector: '#dlAllergy', kind: 'inspect', label: '위험 평가 저장', value: 'AllergyIntolerance JSON', tip: '의료진의 검토 후 사용하세요.' }]);
    await page.locator('#vScreening').click();
    await page.locator('#fhirPreview').filter({hasText:'Questionnaire'}).waitFor();
    await scene({ group: 'FHIR 내보내기', title: '문진 결과도 함께 저장할 수 있습니다', lead: '문진 자료가 필요하면 해당 JSON을 저장하세요.', scroll: '.result-tabs', notes: ['Condition과 Questionnaire 관련 자료를 확인하세요.', '다운로드한 파일의 보관 위치를 확인하세요.', '진료 때 리포트와 함께 의료진에게 보여 주세요.'], source: 'web/app.js:1449' }, [{ selector: '#vScreening', kind: 'inspect', label: '문진 결과 보기', value: 'Condition · Questionnaire', tip: '입력한 질환과 문진 자료를 확인하세요.' }, { selector: '#dlScreening', kind: 'inspect', label: '문진 결과 저장', value: 'Condition · Questionnaire JSON', tip: '요청받은 경우 파일을 저장하세요.' }]);
    // Verify real downloads without publishing or sending the synthetic data.
    for (const id of ['dlObs','dlAllergy','dlScreening']) {
      const wait = page.waitForEvent('download');
      await page.locator('#'+id).click();
      const download = await wait;
      await download.saveAs(path.join(DIR,'sample_'+id+'.json'));
    }
    manifest.downloadsVerified = true;
    manifest.completed = true;
    saveManifest();
  } finally { await browser.close(); }
}
main().catch(e => { saveManifest(); console.error(e); process.exitCode = 1; });
