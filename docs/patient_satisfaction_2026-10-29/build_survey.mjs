import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';

const root = path.dirname(fileURLToPath(import.meta.url));
const runtime = '/Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const require = createRequire(path.join(runtime, 'docx/package.json'));
const d = require('docx');
const { marked } = await import(pathToFileURL(path.join(runtime, 'marked/lib/marked.esm.js')));
const q = JSON.parse(fs.readFileSync(path.join(root, 'survey_definition.json'), 'utf8'));
const esc = value => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const choices = (name, options) => options.map(o => `<label class="choice"><input type="radio" name="${name}" value="${esc(o.value)}"><span>${esc(o.label)}</span></label>`).join('');
const items = q.items.map(item => `<fieldset class="item"><legend>${item.id}. ${esc(item.text)}</legend><div class="ratings">${q.scale.slice(0, 5).map(o => `<label class="choice"><input type="radio" name="${item.id}" value="${o.value}"><b>${o.value}</b><span>${o.label}</span></label>`).join('')}</div><div class="other-choices">${choices(item.id, q.scale.slice(5))}</div></fieldset>`).join('');
const md = `# ${q.title}

APAAACI-SAT-01 · 설문 v0.1 · 2026-10-29 · IRB 검토용 초안

받으신 결과 보고서와 카드뉴스의 이용 경험을 알려 주세요.
설문은 약 2분이 걸리도록 구성했습니다.
참여는 선택입니다.
답하지 않아도 진료와 자료 제공에 불이익이 없습니다.
연구번호로 다른 연구정보와 연결합니다.

## 1. 이용 경험

누가 답하고 계신가요? 하나를 골라 주세요.

□ 환자 본인  □ 환자 본인 (입력 도움 받음)  □ 보호자

보호자는 자료를 직접 이용한 경험을 답해 주세요.
아이의 느낌을 추측해서 답하지 마세요.

어떤 자료를 보셨나요? 하나를 골라 주세요.

□ 결과 보고서  □ 카드뉴스  □ 둘 다  □ 아직 보지 못했어요

아직 보지 못했다면 아래 이유만 고르고 마치세요.

□ 열기 어려움  □ 볼 시간 없음  □ 보는 곳을 모름
□ 다른 이유  □ 응답하지 않음

## 2. 간단 평가

본 자료를 생각하며 문항마다 하나를 골라 주세요.
평가가 어렵거나 원하지 않으면 건너뛰어도 됩니다.

1 전혀 그렇지 않다 / 2 그렇지 않다 / 3 보통이다
4 그렇다 / 5 매우 그렇다

| 번호 | 문항 | 1 | 2 | 3 | 4 | 5 | 평가 어려움 | 응답하지 않음 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
${q.items.map(i => `| ${i.id} | ${i.text} | □ | □ | □ | □ | □ | □ | □ |`).join('\n')}

## 3. 개선 의견 (선택, 300자 이내)

더 좋게 바꿀 점이 있으면 적어 주세요.
이름, 전화번호, 자세한 건강정보는 적지 마세요.

________________________________________________________________

________________________________________________________________

답변은 연구에서 정한 보관 기간과 권한으로 관리합니다.
문의나 중단은 안내문에 적힌 연구 연락처로 알려 주세요.
`;
fs.writeFileSync(path.join(root, 'questionnaire.md'), md);
const paper = marked.parse(md);
let template = fs.readFileSync(path.join(root, 'survey.template'), 'utf8');
const tokens = { TITLE: q.title, RESPONDENTS: choices('respondent', q.respondents), MATERIALS: choices('material', q.materials), REASONS: choices('nonviewer_reason', q.nonviewer_reasons), ITEMS: items, PAPER: paper, DEFINITION: JSON.stringify(q).replaceAll('<', '\\u003c'), SCRIPT: fs.readFileSync(path.join(root, 'survey.js'), 'utf8') };
template = template.replace(/%%([A-Z]+)%%/g, (_, key) => tokens[key]);
if (/%%[A-Z]+%%/.test(template)) throw new Error('Unresolved template token');
fs.writeFileSync(path.join(root, 'patient_satisfaction.html'), template);

const paragraph = (text, options = {}) => new d.Paragraph({ children: [new d.TextRun({ text, ...(options.run || {}) })], spacing: { after: 75, line: 265 }, ...options });
const heading = text => paragraph(text, { heading: d.HeadingLevel.HEADING_2, spacing: { before: 170, after: 85 }, keepNext: true });
const cols = [480, 4000, 570, 570, 570, 570, 570, 1200, 1576];
const width = cols.reduce((sum, n) => sum + n, 0);
const row = (cells, header = false) => new d.TableRow({ tableHeader: header ? true : undefined, cantSplit: true, children: cells.map((text, i) => new d.TableCell({ width: { size: cols[i], type: d.WidthType.DXA }, margins: { top: 100, bottom: 100, left: 70, right: 70 }, ...(header ? { shading: { fill: 'EAF3FA', type: d.ShadingType.CLEAR } } : {}), children: [paragraph(text, { alignment: i === 1 ? d.AlignmentType.LEFT : d.AlignmentType.CENTER, run: { size: i === 1 ? 21 : 19, bold: header }, spacing: { after: 0, line: 250 } })] })) });
const table = new d.Table({ width: { size: width, type: d.WidthType.DXA }, columnWidths: cols, rows: [row(['번호', '문항', '1', '2', '3', '4', '5', '평가 어려움', '응답하지 않음'], true), ...q.items.map(i => row([i.id, i.text, '□', '□', '□', '□', '□', '□', '□']))] });
const body = [
  paragraph(q.title, { heading: d.HeadingLevel.TITLE, spacing: { after: 140 }, run: { size: 33, bold: true, color: '145D89' } }),
  paragraph('APAAACI-SAT-01 · 설문 v0.1 · 2026-10-29 · IRB 검토용 초안', { run: { size: 17, color: '526D80' } }),
  paragraph('받으신 결과 보고서와 카드뉴스의 이용 경험을 알려 주세요.'),
  paragraph('설문은 약 2분이 걸리도록 구성했습니다. 참여는 선택입니다.'),
  paragraph('답하지 않아도 진료와 자료 제공에 불이익이 없습니다.'),
  paragraph('연구번호로 다른 연구정보와 연결합니다.'),
  heading('1. 이용 경험'),
  paragraph('누가 답하고 계신가요? 하나를 골라 주세요.'),
  paragraph('□ 환자 본인   □ 환자 본인 (입력 도움 받음)   □ 보호자'),
  paragraph('보호자는 자료를 직접 이용한 경험을 답해 주세요.'),
  paragraph('아이의 느낌을 추측해서 답하지 마세요.'),
  paragraph('어떤 자료를 보셨나요? 하나를 골라 주세요.'),
  paragraph('□ 결과 보고서   □ 카드뉴스   □ 둘 다   □ 아직 보지 못했어요'),
  paragraph('아직 보지 못했다면 아래 이유만 고르고 마치세요.'),
  paragraph('□ 열기 어려움   □ 볼 시간 없음   □ 보는 곳을 모름'),
  paragraph('□ 다른 이유   □ 응답하지 않음'),
  heading('2. 간단 평가'),
  paragraph('본 자료를 생각하며 문항마다 하나를 골라 주세요.'),
  paragraph('평가가 어렵거나 원하지 않으면 건너뛰어도 됩니다.'),
  paragraph('1 전혀 그렇지 않다 / 2 그렇지 않다 / 3 보통이다'),
  paragraph('4 그렇다 / 5 매우 그렇다'), table,
  heading('3. 개선 의견 (선택, 300자 이내)'),
  paragraph('더 좋게 바꿀 점이 있으면 적어 주세요.'),
  paragraph('이름, 전화번호, 자세한 건강정보는 적지 마세요.'),
  paragraph('________________________________________________________________'),
  paragraph('________________________________________________________________'),
  paragraph('답변은 연구에서 정한 보관 기간과 권한으로 관리합니다.', { run: { size: 18 } }),
  paragraph('문의나 중단은 안내문에 적힌 연구 연락처로 알려 주세요.', { run: { size: 18 } })
];
const doc = new d.Document({ creator: 'APAAACI 연구팀', title: q.title, description: '사후 만족도 평가용 환자 설문 v0.1', styles: { default: { document: { run: { font: 'Malgun Gothic', size: 21, color: '183348' } }, title: { run: { size: 33, bold: true } }, heading2: { run: { size: 24, bold: true, color: '145D89' }, paragraph: { outlineLevel: 1 } } } }, sections: [{ properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 780, bottom: 780, left: 900, right: 900 } } }, children: body, footers: { default: new d.Footer({ children: [paragraph('APAAACI · 설문 v0.1 · 승인 전 초안', { run: { size: 16, color: '526D80' }, alignment: d.AlignmentType.RIGHT })] }) } }] });
fs.writeFileSync(path.join(root, 'patient_satisfaction.docx'), await d.Packer.toBuffer(doc));
execFileSync('/usr/bin/textutil', ['-convert', 'doc', '-output', path.join(root, 'patient_satisfaction.doc'), path.join(root, 'patient_satisfaction.docx')]);
const docHeader = fs.readFileSync(path.join(root, 'patient_satisfaction.doc')).subarray(0, 8).toString('hex');
if (docHeader !== 'd0cf11e0a1b11ae1') throw new Error('DOC is not a binary Word file');

const css = `*{box-sizing:border-box}body{margin:0;color:#183348;background:#f3f7fa;font:16px/1.8 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}a{color:#145d89;text-underline-offset:3px;overflow-wrap:anywhere}header,main,footer{max-width:1100px;margin:auto;padding:24px}h1{font-size:clamp(27px,4vw,38px);line-height:1.4}h2{font-size:23px;line-height:1.5;margin-top:32px}h3{font-size:19px}.notice{background:#fff3d7;border-left:4px solid #c38a2a;padding:15px 20px}.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card,article{background:white;border:1px solid #cbdde9;border-radius:12px;padding:24px;min-width:0}.card h2{margin-top:0}.actions{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}.button,button{display:inline-block;color:#145d89;border:1px solid #b7cedf;border-radius:8px;padding:9px 15px;background:#fff;text-decoration:none;font:inherit;cursor:pointer}.button.primary{color:white;background:#145d89}.table-wrap{overflow-x:auto}table{width:100%;min-width:500px;border-collapse:collapse;font-size:14px;margin:18px 0}td,th{border:1px solid #cbdde9;padding:9px 12px;vertical-align:top;text-align:left}th{background:#eaf5fc}.small{font-size:14px;color:#526d80}blockquote{background:#eef5fa;border-left:3px solid #a4c4da;margin:18px 0;padding:6px 18px}code{overflow-wrap:anywhere}pre{white-space:pre-wrap}nav{display:flex;gap:12px;flex-wrap:wrap;font-size:14px}a:focus-visible,button:focus-visible,summary:focus-visible{outline:3px solid #cf8d26;outline-offset:3px}li{margin:6px 0}footer{font-size:13px;color:#526d80}@media(max-width:650px){header,main,footer{padding:17px}.cards{grid-template-columns:1fr}.card,article{padding:19px}}@media print{@page{size:A4;margin:16mm}body{background:white;font-size:11pt}nav,.actions{display:none}.cards{display:block}.card,article{border:0;padding:0}header,main,footer{padding:0}h2{break-after:avoid}tr{break-inside:avoid}table{min-width:0;font-size:9pt}.table-wrap{overflow:visible}.notice{background:white}}
`;
const page = (title, body) => `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>${esc(title)} | APAAACI</title><style>${css}</style></head><body><header><nav><a href="../project_documentation_2026-10-29.html">전체 자료</a><a href="index.html">만족도 설문</a><a href="../irb_2026-10-29/index.html">IRB 문서</a><a href="../medical_information_cooperation_2026-10-29/index.html">의료정보센터 협조</a></nav></header>${body}<footer>2026-10-29 · 설문 v0.1 · IRB v0.2 초안 · 실제 병원 발송 전</footer><script>document.querySelectorAll('table').forEach(t=>{const w=document.createElement('div');w.className='table-wrap';t.before(w);w.append(t)});document.querySelectorAll('[data-print]').forEach(b=>b.addEventListener('click',()=>window.print()));</script></body></html>`;
const guide = fs.readFileSync(path.join(root, 'implementation_guide.md'), 'utf8');
fs.writeFileSync(path.join(root, 'implementation_guide.html'), page('환자 만족도 설문 운영 안내', `<main><div class="notice">평가 6문항 · 약 2분 목표 · 자료 수령 후 별도 알림톡 · 승인 전 제안</div><div class="actions"><a class="button primary" href="patient_satisfaction.html">환자 화면</a><a class="button" href="implementation_guide.md" download>운영 안내 원문</a><button data-print>인쇄</button></div><article>${marked.parse(guide)}</article></main>`));
const savedNotion = path.join(root, 'notion_page.json');
const notion = fs.existsSync(savedNotion) ? JSON.parse(fs.readFileSync(savedNotion)) : null;
fs.writeFileSync(path.join(root, 'index.html'), page('환자 만족도 평가 자료', `<main><h1>환자 만족도 평가 자료</h1><p>결과 보고서와 카드뉴스를 받은 뒤 이용 경험을 묻습니다.<br>기존 앱 설문 3종을 참고해 평가 6문항으로 줄였습니다.</p><div class="notice"><strong>약 2분은 설계 목표입니다.</strong><br>연구팀과 IRB가 문항·일정·동의를 확정해야 합니다.</div><div class="actions"><a class="button primary" href="patient_satisfaction.html">환자용 설문 열기</a><a class="button" href="APAAACI_patient_satisfaction.zip" download>전체 자료 ZIP</a>${notion ? `<a class="button" href="${esc(notion.url)}">노션 하위 작업</a>` : ''}</div><div class="cards"><section class="card"><h2>환자용 설문지</h2><p>5점 평가 6문항과 선택 의견란입니다.<br>미열람 분기와 보호자 구분을 제공합니다.</p><div class="actions"><a class="button" download href="patient_satisfaction.doc">Word DOC</a><a class="button" download href="patient_satisfaction.docx">Word DOCX</a><a class="button" download href="patient_satisfaction.pdf">PDF</a></div><p class="small">HTML은 미리보기입니다. 실제 환자정보를 입력하지 마십시오.</p></section><section class="card"><h2>발송과 분석 안내</h2><p>자료 수령 확인 24시간 후 별도 초대를 제안합니다.<br>응답은 최대 7일, 재알림은 1회입니다.</p><p>개인 참여는 검사 결과 확정일부터 30일 안에서 종료합니다.</p><a class="button" href="implementation_guide.html">운영 안내</a></section><section class="card"><h2>IRB 반영 문서</h2><p>연구계획서·설명문·동의서·문진 양식을 v0.2로 갱신합니다.</p><a class="button" href="../irb_2026-10-29/index.html">IRB 문서 모음</a></section><section class="card"><h2>정의와 참고 자료</h2><p>문항·일정·분석을 한 정의 파일로 관리합니다.<br>원문 설문은 운영 안내에서 연결합니다.</p><div class="actions"><a class="button" href="survey_definition.json">설문 정의</a><a class="button" href="implementation_guide.html">참고 범위</a></div></section></div><h2>평가 문항</h2><ol>${q.items.map(i => `<li>${esc(i.text)}</li>`).join('')}</ol><p>평가 어려움·응답 거절·빈 답변은 점수로 바꾸지 않습니다.<br>만족도 응답으로 임상 정확성이나 치료 효과를 판정하지 않습니다.</p></main>`));
fs.writeFileSync(path.join(root, 'sources.json'), JSON.stringify({ reviewed_on: q.date, blank_forms_only: true, copied_patient_responses: false, references: q.references }, null, 2) + '\n');
console.log(JSON.stringify({ html: 3, docx: 1, binary_doc: docHeader === 'd0cf11e0a1b11ae1', items: q.items.length, external_requests: 0 }));
