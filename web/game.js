/* =========================================================================
   알러젠 탐험 퀘스트 — 게임 레이어 (XP·레벨·스테이지·도감 카드·배지·연출)
   판정 로직과 무관한 프레젠테이션 상태만 다룬다. 답변 내용으로 XP 를 차등하지 않는다.
   UMD: 브라우저에서는 window.Game, Node 에서는 module.exports (단위 테스트용)
   ========================================================================= */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Game = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const XP = { STEP: 50, DISCOVER: 10, DISCOVER_CAP: 100, ANSWER: 10, CHAPTER: 30, STAGE: 5 };
  const LEVELS = [
    { min: 0, title: '새싹 탐험가' },
    { min: 200, title: '숙련 탐험가' },
    { min: 500, title: '알러젠 마스터' },
  ];
  const VERDICT = {
    clinically_relevant: { stamp: '진범 확정', tone: 'relevant', note: '노출 시 증상이 재현되는 알러젠' },
    sensitized_only: { stamp: '무혐의 · 감작만', tone: 'sensitized', note: '감작은 남아 있어 추적 필요' },
    indeterminate: { stamp: '관찰 대상', tone: 'indet', note: '노출 시 증상을 기록해 확인' },
    // 약물 항원 — 서버가 relevance 는 'indeterminate', verdict 는 'clinician_review' 로 보낸다(진범 확정도 무혐의도 아니다)
    clinician_review: { stamp: '진료 확인 필요', tone: 'indet', note: '약물 — 피할지, 다시 써도 되는지는 진료에서 결정' },
    not_assessed: { stamp: '미확인', tone: 'na', note: '' },
    pending: { stamp: '판정 대기', tone: 'na', note: '단서를 모으는 중' },   // 문진 전(도감 등록 직후)
    control: { stamp: '검사 대조', tone: 'na', note: '알러젠이 아닌 검사 확인용 항목' },   // 양성·음성 대조 — 판정 대상이 아니다
  };
  // 판정이 '어느 쪽으로든' 가려진 상태. 기록 등급은 이것과 기록 항목 수만 본다(양성·중증과 무관).
  const RESOLVED = new Set(['clinically_relevant', 'sensitized_only']);
  const VERDICT_ORDER = { clinically_relevant: 0, indeterminate: 1, sensitized_only: 2, not_assessed: 3, pending: 4 };
  // 도감 바인더 순서. 서버의 valid_categories(data/category_rules.json) 전부를 담는다 — 검사 대조(control)는 알러젠이 아니라 맨 뒤.
  const CATEGORY_ORDER = ['mite', 'animal', 'pollen_tree', 'pollen_grass', 'pollen_weed', 'mold', 'insect', 'venom', 'food', 'latex', 'drug', 'other', 'control'];
  const STRENGTH_STARS = { weak: 1, moderate: 2, strong: 3 };
  const SEVERE_ANSWERS = new Set(['anaphylaxis']);
  const SEVERE_LEVELS = new Set(['severe', 'anaphylaxis']);
  const SEVERE_CROSS = new Set(['systemic', 'anaphylaxis']);

  const svg = (inner) => `<svg viewBox="0 0 40 40" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
  // 카테고리 스탬프(손도장 느낌의 라인 아이콘). cardnews_service._CATEGORY_STAMP_SVG 와 동일 소스.
  const STAMPS = {
    mite: svg('<circle cx="20" cy="22" r="8"/><path d="M12 18l-5-4M28 18l5-4M11 24l-6 1M29 24l6 1M13 29l-4 4M27 29l4 4M17 20h6"/>'),
    animal: svg('<circle cx="20" cy="26" r="6"/><circle cx="11" cy="18" r="3"/><circle cx="17" cy="12" r="3"/><circle cx="23" cy="12" r="3"/><circle cx="29" cy="18" r="3"/>'),
    pollen_tree: svg('<path d="M20 35V24"/><path d="M11 24h18L20 8z"/><path d="M14 18h12"/>'),
    pollen_grass: svg('<path d="M20 35V13M14 35c0-8 2-13 4-17M26 35c0-8-2-13-4-17M9 35c0-5 2-9 5-11M31 35c0-5-2-9-5-11"/>'),
    pollen_weed: svg('<path d="M20 35V15"/><path d="M20 25c-7 0-10-5-11-11 6 0 10 4 11 11zM20 20c7 0 10-5 11-11-6 0-10 4-11 11z"/>'),
    mold: svg('<circle cx="15" cy="23" r="6"/><circle cx="25" cy="18" r="5"/><circle cx="25" cy="28" r="4"/><path d="M15 23h.01M25 18h.01"/>'),
    insect: svg('<ellipse cx="20" cy="22" rx="7" ry="10"/><path d="M13 17l-5-6M27 17l5-6M12 24H6M28 24h6M14 30l-4 5M26 30l4 5M20 12v20"/>'),
    venom: svg('<ellipse cx="20" cy="23" rx="7" ry="9"/><path d="M13.6 20h12.8M13.2 25h13.6M20 32v5M15 16c-6-1-9-5-8-9 5 0 8 3 9 7M25 16c6-1 9-5 8-9-5 0-8 3-9 7"/>'),
    food: svg('<circle cx="20" cy="22" r="10"/><circle cx="20" cy="22" r="4"/><path d="M6 12v8M34 12v8"/>'),
    latex: svg('<path d="M13 35V23l-4.6-6.4a2.2 2.2 0 0 1 3.6-2.6l2.6 3.4V8.5a2 2 0 0 1 4 0V17M18.6 17V6.5a2 2 0 0 1 4 0V17M22.6 17V8a2 2 0 0 1 4 0v10M26.6 18v-5.5a2 2 0 0 1 4 0V26c0 3.6-1.4 6.4-3.6 9M13 31h14"/>'),
    drug: svg('<path d="M10.6 29.4a7 7 0 0 1 0-9.9l8.9-8.9a7 7 0 0 1 9.9 9.9l-8.9 8.9a7 7 0 0 1-9.9 0zM15 15l10 10"/><path d="M22.5 12.5a3.5 3.5 0 0 1 4.5.5"/>'),
    control: svg('<circle cx="13" cy="20" r="8"/><path d="M13 16v8M9 20h8"/><circle cx="30" cy="20" r="6"/><path d="M27 20h6"/>'),
    other: svg('<path d="M15 16a5 5 0 1 1 7 4.6c-1.5.8-2 1.8-2 3.4"/><circle cx="20" cy="29" r="1.3" fill="currentColor"/>'),
  };
  const BADGES = [
    { id: 'finisher', name: '완주', desc: '다섯 단계를 모두 마쳤어요', icon: '🏁' },
    { id: 'honest', name: '정직한 탐험가', desc: "'잘 모르겠어요'도 소중한 단서예요", icon: '🧭' },
    { id: 'crosshunter', name: '교차반응 헌터', desc: '증상으로 확인된 교차반응 음식을 찾았어요', icon: '🕵️' },
    { id: 'oas', name: 'OAS 탐지', desc: '꽃가루-음식 교차반응(OAS)을 확인했어요', icon: '🍎' },
    { id: 'reviewer', name: '꼼꼼한 검토자', desc: 'OCR 결과를 직접 고쳐 정확도를 높였어요', icon: '🔍' },
    { id: 'dex', name: '도감 완성', desc: '모든 양성 알러젠에 판정이 붙었어요', icon: '📖' },
  ];

  function createState() {
    return { xp: 0, stepsDone: {}, stagesDone: {}, chaptersDone: {}, answered: {}, discovered: {}, edits: 0, unsureUsed: false, badges: [], severeFlag: false };
  }
  function levelFor(xp) {
    let i = 0;
    LEVELS.forEach((l, k) => { if (xp >= l.min) i = k; });
    const cur = LEVELS[i], nxt = LEVELS[i + 1];
    return { index: i, title: cur.title, min: cur.min, next: nxt ? nxt.min : null };
  }
  function progressPct(xp) {
    const l = levelFor(xp);
    if (l.next == null) return 100;
    return Math.round((xp - l.min) / (l.next - l.min) * 100);
  }
  function completeStep(g, step) {
    if (g.stepsDone[step]) return 0;
    g.stepsDone[step] = true; g.xp += XP.STEP; return XP.STEP;
  }
  // 퀘스트 안의 작은 단계(스테이지)를 넘길 때 주는 보상. 무엇을 골랐는지와 무관하게 1회만.
  function completeStage(g, id) {
    if (!g.stagesDone) g.stagesDone = {};
    if (g.stagesDone[id]) return 0;
    g.stagesDone[id] = true; g.xp += XP.STAGE; return XP.STAGE;
  }
  // 감작 강도 별: MAST/UniCAP 은 class(1-2/3-4/5-6), SPT 는 팽진 mm(3~5/5~8/8+)
  function starsFor(row, testType) {
    if (testType === 'SPT') {
      const v = parseFloat(row.value ?? row.mean_mm);
      if (!isNaN(v)) return v >= 8 ? 3 : v >= 5 ? 2 : 1;
      return 1;
    }
    const c = parseInt(row.class_value);
    if (!isNaN(c)) return c >= 5 ? 3 : c >= 3 ? 2 : 1;
    return 1;
  }
  // catOf: OCR 행에 category 가 없을 때 쓸 분류 함수(선택). 카드 앞면에 쓸 검사값도 함께 적어 둔다.
  function discover(g, rows, testType, catOf) {
    let gained = 0;
    rows.forEach(r => {
      const key = r.allergen_name; if (!key || g.discovered[key]) return;
      g.discovered[key] = { name: r.korean_name || r.allergen_name, category: r.category || (catOf ? catOf(r) : null) || 'other', stars: starsFor(r, testType), verdict: null,
        en: r.allergen_name, value: r.value ?? r.mean_mm ?? null, unit: r.unit || null, cls: r.class_value ?? null, no: Object.keys(g.discovered).length + 1 };
      if (gained < XP.DISCOVER_CAP) gained += XP.DISCOVER;
    });
    g.xp += gained; return gained;
  }
  function hasAnswer(v) { return Array.isArray(v) ? v.length > 0 : (v !== undefined && v !== null && v !== ''); }
  function answer(g, qid, value) {
    if (value === 'unsure' || (Array.isArray(value) && value.includes('unsure'))) g.unsureUsed = true;
    if (g.answered[qid]) return 0;
    g.answered[qid] = true; g.xp += XP.ANSWER; return XP.ANSWER;
  }
  function chapterProgress(section, answers, isVisible) {
    const vis = section.questions.filter(q => isVisible(q));
    const ans = vis.filter(q => hasAnswer(answers[q.id])).length;
    return { answered: ans, visible: vis.length, done: vis.length > 0 && ans === vis.length };
  }
  function completeChapter(g, id) {
    if (g.chaptersDone[id]) return 0;
    g.chaptersDone[id] = true; g.xp += XP.CHAPTER; return XP.CHAPTER;
  }
  function noteEdit(g) { g.edits += 1; }
  function applyResults(g, classify, answers) {
    const as = (classify && classify.assessments) || [];
    let severe = false;
    as.forEach(a => {
      const d = g.discovered[a.allergen_name] || (g.discovered[a.allergen_name] = { name: a.korean_name || a.allergen_name, category: a.category || 'other', stars: 1, verdict: null });
      d.verdict = a.relevance || 'not_assessed';
      if (a.strength && STRENGTH_STARS[a.strength]) d.stars = STRENGTH_STARS[a.strength];
      if (SEVERE_LEVELS.has(a.severity) || SEVERE_CROSS.has(a.crossreact_severity)) severe = true;
    });
    Object.values(answers || {}).forEach(v => {
      if (SEVERE_ANSWERS.has(v) || (Array.isArray(v) && v.some(x => SEVERE_ANSWERS.has(x)))) severe = true;
    });
    g.severeFlag = severe;
    const earned = [];
    const award = (id, cond) => { if (cond && !g.badges.includes(id)) { g.badges.push(id); earned.push(id); } };
    award('finisher', true);
    award('honest', g.unsureUsed);
    award('crosshunter', as.some(a => (a.crossreact_confirmed || []).length > 0));
    award('oas', as.some(a => (a.oas_foods || []).length > 0));
    award('reviewer', g.edits > 0);
    award('dex', as.length > 0 && as.every(a => a.relevance && a.relevance !== 'not_assessed'));
    return { newBadges: earned };
  }
  function stampSvg(category) { return STAMPS[category] || STAMPS.other; }

  /* ---------------- 도감 카드 로직 (순수 함수) ---------------- */
  // 기록 등급: 카드의 광택(포일)을 정한다. '기록이 얼마나 채워졌는가'만 보며,
  // 양성 여부·수치 크기·중증도는 등급에 들어가지 않는다 — 양성 결과가 '좋은 카드'처럼 보이지 않게 하기 위해서다.
  //   2 완성 기록: 판정이 가려졌고(진범 확정이든 무혐의든) 기록 항목 6개 중 5개 이상
  //   1 조사 기록: 문진을 거쳤고(확정·무혐의·관찰) 기록 항목 3개 이상
  //   0 기본 기록: 그 밖(검사 결과만 있는 상태)
  function recordGrade(a) {
    a = a || {};
    if (a.category === 'control') return { grade: 0, filled: 0, total: 6, facets: {} };   // 검사 대조는 기록 대상이 아니다
    const has = (v) => Array.isArray(v) ? v.length > 0 : !!v;
    const facets = {
      measured: a.test_value != null || a.class_value != null,
      verdict: RESOLVED.has(a.relevance),
      season: has(a.season_label_ko),
      exposure: has(a.exposure_environment_ko) || has(a.biology_ko),
      guidance: has(a.avoidance_control_ko),
      cross: has(a.cross_reactivity_ko) || has(a.oas_foods) || has(a.crossreact_confirmed),
    };
    const filled = Object.values(facets).filter(Boolean).length;
    const asked = facets.verdict || a.relevance === 'indeterminate';
    const grade = facets.verdict && filled >= 5 ? 2 : (asked && filled >= 3 ? 1 : 0);
    return { grade, filled, total: 6, facets };
  }
  // 감작 강도 눈금: class(0~6) 가 있으면 6칸, 없으면 SPT 팽진/강도로 3칸
  function levelPips(a, testType) {
    a = a || {};
    const c = parseInt(a.class_value);
    if (!isNaN(c)) return { n: Math.max(0, Math.min(6, c)), max: 6, kind: 'class' };
    const v = parseFloat(a.test_value);
    if ((testType === 'SPT' || a.test_unit === 'mm') && !isNaN(v)) return { n: v >= 8 ? 3 : v >= 5 ? 2 : 1, max: 3, kind: 'wheal' };
    return { n: STRENGTH_STARS[a.strength] || 1, max: 3, kind: 'strength' };
  }
  function strengthScore(a) {
    const c = parseInt(a.class_value), v = parseFloat(a.test_value);
    return (isNaN(c) ? (STRENGTH_STARS[a.strength] || 0) * 2 : c) * 1000 + (isNaN(v) ? 0 : Math.min(v, 999));
  }
  const catRank = (c) => { const i = CATEGORY_ORDER.indexOf(c); return i < 0 ? CATEGORY_ORDER.length : i; };
  const cardName = (a) => String(a.korean_name || a.allergen_name || '');
  // 정렬은 화면 표시 순서만 바꾼다(원본 배열은 건드리지 않는다)
  function sortCards(list, mode) {
    const out = (list || []).slice();
    const vRank = (a) => a.category === 'control' ? 9 : (VERDICT_ORDER[a.relevance] ?? 3);   // 검사 대조는 판정 순서의 맨 뒤
    const byVerdict = (a, b) => vRank(a) - vRank(b);
    const cmp = {
      verdict: (a, b) => byVerdict(a, b) || strengthScore(b) - strengthScore(a),
      strength: (a, b) => strengthScore(b) - strengthScore(a) || byVerdict(a, b),
      name: (a, b) => cardName(a).localeCompare(cardName(b)),
      category: (a, b) => catRank(a.category) - catRank(b.category) || byVerdict(a, b),
    }[mode] || byVerdict;
    return out.map((a, i) => [a, i]).sort((x, y) => cmp(x[0], y[0]) || x[1] - y[1]).map(x => x[0]);
  }
  // 검사 대조(control)는 종류 목록(byCategory)에는 나오지만 등록 수·판정 수에는 세지 않는다 — 알러젠이 아니다.
  function dexSummary(list) {
    const byVerdict = { clinically_relevant: 0, sensitized_only: 0, indeterminate: 0, not_assessed: 0 };
    const cats = {}; let controls = 0;
    (list || []).forEach(a => {
      const c = a.category || 'other'; cats[c] = (cats[c] || 0) + 1;
      if (c === 'control') { controls += 1; return; }
      const rel = byVerdict[a.relevance] !== undefined ? a.relevance : 'not_assessed';
      byVerdict[rel] += 1;
    });
    const byCategory = Object.keys(cats).sort((a, b) => catRank(a) - catRank(b)).map(c => ({ category: c, count: cats[c] }));
    return { total: (list || []).length - controls, controls, resolved: byVerdict.clinically_relevant + byVerdict.sensitized_only, byVerdict, byCategory };
  }
  function starsHtml(n) {
    n = Math.max(1, Math.min(3, n | 0));
    return `<span class="stars" aria-label="${tr('stars.aria', { n }, `감작 강도 ${n}/3`)}">${'★'.repeat(n)}${n < 3 ? `<i>${'★'.repeat(3 - n)}</i>` : ''}</span>`;
  }

  /* ---------------- DOM 연출 (브라우저 전용) ---------------- */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  // 번역 훅: 브라우저에 I18N(web/i18n.js) 이 있으면 사용, 없으면(Node 테스트) 한국어 기본값
  const tr = (key, vars, fallback) => {
    const i = (typeof I18N !== 'undefined') ? I18N : null;
    if (!i) return fallback;
    const v = i.t(key, vars); return v === key ? fallback : v;
  };
  const artLib = () => api.art || (typeof QuestArt !== 'undefined' ? QuestArt : null);
  function pipsHtml(p, aria) {
    let h = '';
    for (let i = 0; i < p.max; i++) h += `<i class="${i < p.n ? 'on' : ''}"></i>`;
    return `<span class="pips pips-${p.max}" role="img" aria-label="${esc(aria || `${p.n} / ${p.max}`)}">${h}</span>`;
  }
  // 작은 도감 카드(등록 연출·바인더용). m = { name, category, plateName, pips, pipsAria, tone, status, grade, key, i, tag }
  function miniCardHtml(m) {
    const a = artLib();
    const tag = m.tag || 'div';
    const attrs = tag === 'button' ? ` type="button" aria-label="${esc(m.aria || `${m.name} — ${m.status || ''}`)}"` : '';
    return `<${tag} class="mini-card v-${m.tone || 'na'}" data-cat="${esc(m.category || 'other')}" data-grade="${m.grade | 0}"${m.key != null ? ` data-key="${esc(m.key)}"` : ''} style="--i:${m.i | 0}"${attrs}>
        <span class="mc-art">${a ? a.plate(m.category, m.plateName || m.name) : stampSvg(m.category)}</span>
        <span class="mc-name">${esc(m.name)}</span>
        ${m.pips ? pipsHtml(m.pips, m.pipsAria) : ''}
        ${m.status ? `<span class="mc-status tone-${m.tone || 'na'}">${esc(m.status)}</span>` : ''}
      </${tag}>`;
  }
  function bindTilt(card) {
    if (reduced() || card._tilt) return;
    card._tilt = true;
    let raf = 0, touching = false;
    const move = (e) => {
      if (e.pointerType === 'touch' && !touching) return;
      const r = card.getBoundingClientRect(); if (!r.width) return;
      const px = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), py = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const flip = card.classList.contains('flip') ? -1 : 1;
        card.style.setProperty('--ry', `${((px - .5) * 14 * flip).toFixed(2)}deg`);
        card.style.setProperty('--rx', `${((.5 - py) * 12).toFixed(2)}deg`);
        card.style.setProperty('--mx', `${(px * 100).toFixed(1)}%`);
        card.style.setProperty('--my', `${(py * 100).toFixed(1)}%`);
        card.classList.add('tilting');
      });
    };
    const reset = () => { touching = false; cancelAnimationFrame(raf); card.classList.remove('tilting'); card.style.removeProperty('--rx'); card.style.removeProperty('--ry'); };
    card.addEventListener('pointermove', move);
    card.addEventListener('pointerdown', (e) => { if (e.pointerType === 'touch') { touching = true; move(e); } });
    card.addEventListener('pointerup', (e) => { if (e.pointerType === 'touch') reset(); });
    card.addEventListener('pointerleave', reset);
    card.addEventListener('pointercancel', reset);
  }
  const ui = {
    pipsHtml, miniCardHtml,
    // 퀘스트 안의 작은 단계 표시. o = { aria, stages:[{label, state:'done'|'cur'|'todo', reachable, meta}], onGoto }
    renderStageRail(el, o) {
      if (!el) return;
      const st = o.stages || [];
      el.innerHTML = `<ol class="srail" aria-label="${esc(o.aria || '')}">${st.map((s, i) => `<li class="sdot ${s.state}">
          <button type="button" data-i="${i}" title="${esc(s.label)}" aria-label="${esc(`${i + 1}. ${s.label}${s.meta ? ` (${s.meta})` : ''}${s.state === 'done' ? ` — ${tr('stage.done', null, '완료')}` : ''}`)}"
            ${s.state === 'cur' ? 'aria-current="step"' : ''} ${s.reachable && s.state !== 'cur' ? '' : 'tabindex="-1" aria-disabled="true"'}>
            <span class="sd-mark" aria-hidden="true">${s.state === 'done' ? '✓' : i + 1}</span><span class="sd-label" aria-hidden="true">${esc(s.label)}</span>${s.meta ? `<span class="sd-meta" aria-hidden="true">${esc(s.meta)}</span>` : ''}</button></li>`).join('')}</ol>`;
      el.querySelectorAll('button[data-i]').forEach(b => b.addEventListener('click', () => {
        const i = parseInt(b.dataset.i); const s = st[i];
        if (s && s.reachable && s.state !== 'cur' && o.onGoto) o.onGoto(i);
      }));
    },
    // 도감 카드: 클릭·Enter·Space 로 뒤집기 + 포인터 기울임(홀로). 모션 축소 설정이면 기울임을 붙이지 않는다.
    bindCards(root) {
      if (!root) return;
      root.querySelectorAll('.dex-card').forEach(card => {
        if (card._bound) return; card._bound = true;
        const flip = () => { const on = card.classList.toggle('flip'); card.setAttribute('aria-expanded', on); };
        card.addEventListener('click', flip);
        card.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flip(); } });
        bindTilt(card);
      });
    },
    // 모달 오버레이 공통: 배경 inert, Esc 닫기, 닫을 때 포커스 복귀. 닫는 함수를 돌려준다.
    openOverlay(overlay, html, opts) {
      opts = opts || {};
      if (!overlay) { if (opts.onClose) opts.onClose(); return () => {}; }
      const prevFocus = document.activeElement;
      const bg = [document.querySelector('header'), document.querySelector('main'), document.getElementById('questBar')].filter(Boolean);
      bg.forEach(el => { el.inert = true; });
      overlay.innerHTML = html; overlay.classList.remove('hidden');
      let closed = false;
      const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
      const onClick = (e) => { if (opts.dismissOnBackdrop && e.target === overlay) close(); };
      function close() {
        if (closed) return; closed = true;
        overlay.removeEventListener('keydown', onKey); overlay.removeEventListener('click', onClick);
        overlay.classList.add('hidden'); overlay.innerHTML = ''; bg.forEach(el => { el.inert = false; });
        if (opts.onClose) opts.onClose();
        if (prevFocus && prevFocus.isConnected && prevFocus.focus) { try { prevFocus.focus(); } catch (_) {} }
      }
      overlay.addEventListener('keydown', onKey); overlay.addEventListener('click', onClick);
      const f = overlay.querySelector(opts.focus || 'button');
      if (f && f.focus) f.focus();
      return close;
    },
    renderHud(el, g) {
      if (!el) return;
      const l = levelFor(g.xp), pct = progressPct(g.xp);
      const title = tr(`level.${l.index}`, null, l.title);
      el.innerHTML = `<div class="hud-top"><span class="hud-title">${esc(tr('hud.level', { n: l.index + 1, title }, `Lv.${l.index + 1} ${title}`))}</span><span class="hud-xp">${esc(tr('hud.xp', { xp: g.xp }, `${g.xp} XP`))}${l.next != null ? ` <small>/ ${l.next}</small>` : ''}</span></div>
        <div class="hud-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><div class="hud-fill" style="--p:${pct / 100}"></div></div>`;
    },
    renderTrail(el, steps, subs, step, maxReached, onGoto) {
      if (!el) return;
      el.innerHTML = `<svg class="tpath" viewBox="0 0 1000 60" preserveAspectRatio="none" aria-hidden="true">
          <path class="tpath-bg" d="M20 30 C 200 5, 300 55, 500 30 S 800 5, 980 30"/>
          <path class="tpath-fg" pathLength="1" d="M20 30 C 200 5, 300 55, 500 30 S 800 5, 980 30" style="--prog:${step / (steps.length - 1)}"/>
        </svg>
        <ol class="tnodes">${steps.map((label, i) => {
          const cls = i === step ? 'active' : (i < step ? 'done' : (i <= maxReached ? 'reach' : 'locked'));
          return `<li class="tnode ${cls}" data-step="${i}" ${i === step ? 'aria-current="step"' : ''} ${i <= maxReached && i !== step ? 'tabindex="0" role="button"' : ''}>
            <span class="tnum">${i < step ? '✓' : i + 1}</span>
            <span class="tlabel">${esc(label)}<small>${esc(subs[i] || '')}</small></span></li>`;
        }).join('')}</ol>`;
      el.querySelectorAll('.tnode[role="button"]').forEach(n => {
        const go = () => onGoto(parseInt(n.dataset.step));
        n.addEventListener('click', go);
        n.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
      });
    },
    floatXp(anchor, n) {
      if (!n || typeof document === 'undefined') return;
      const f = document.createElement('span');
      f.className = 'xp-float'; f.textContent = tr('xp.float', { n }, `+${n} XP`);
      const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
      const hud = document.getElementById('hud');
      const hr = (!r && hud) ? hud.getBoundingClientRect() : null;
      const x = r ? r.left + r.width / 2 : (hr ? hr.left + hr.width / 2 : window.innerWidth / 2);
      const y = r ? r.top : (hr ? hr.bottom + 6 : 80);
      f.style.left = `${x}px`; f.style.top = `${y}px`;
      document.body.appendChild(f);
      setTimeout(() => f.remove(), reduced() ? 50 : 800);
    },
    sparkle(el) {
      if (!el || reduced()) return;
      for (let i = 0; i < 6; i++) {
        const s = document.createElement('i'); s.className = 'sparkle';
        s.style.setProperty('--dx', `${(Math.random() * 2 - 1) * 60}px`);
        s.style.setProperty('--dy', `${-30 - Math.random() * 60}px`);
        s.style.left = `${20 + Math.random() * 60}%`;
        el.appendChild(s); setTimeout(() => s.remove(), 900);
      }
    },
    renderQuestBar(el, answered, visible) {
      if (!el) return;
      const pct = visible ? Math.round(answered / visible * 100) : 0;
      el.innerHTML = `<div class="qb-text">${tr('questbar.text', { a: answered, v: visible }, `<b>진범 감별 진행</b> ${answered} / ${visible} 단서`)}</div>
        <div class="qb-track" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><div class="qb-fill" style="--p:${pct / 100}"></div></div>`;
    },
    // 도감 등록 연출: 카드가 뒷면으로 깔렸다가 한 장씩 뒤집혀 앞면(판정 대기)이 드러난다.
    // cards = [{name, category, stars, plateName?, pips?}]. 모션 축소 설정이면 처음부터 앞면을 보여준다.
    showDiscovery(overlay, cards, onDone) {
      if (!overlay) { onDone(); return; }
      const shown = cards.slice(0, 12), extra = cards.length - shown.length;
      const pending = tr('verdict.pending.stamp', null, VERDICT.pending.stamp);
      const a = artLib();
      const html = `<div class="discover" role="dialog" aria-modal="true" aria-label="${esc(tr('discover.aria', null, '발견한 알러젠'))}">
          <div class="d-eyebrow">${esc(tr('discover.eyebrow', null, '발견!'))}</div>
          <h2>${tr('discover.h2', { n: cards.length }, `양성 흔적 ${cards.length}종을 도감에 등록했어요`)}</h2>
          <p>${tr('discover.p', null, '아직 판정은 <b>미확인</b>입니다. 다음 퀘스트에서 진범을 가려냅니다.')}</p>
          <div class="discover-deck">${shown.map((c, i) => `<div class="dcard" style="--i:${i}"><div class="dc-in">
              <div class="dc-back" aria-hidden="true">${a ? a.emblem() : ''}</div>
              <div class="dc-front">${miniCardHtml({ name: c.name, category: c.category, plateName: c.plateName, tone: 'na', status: pending, i,
                pips: c.pips || { n: Math.max(1, Math.min(3, c.stars | 0)), max: 3 }, pipsAria: tr('stars.aria', { n: c.stars }, `감작 강도 ${c.stars}/3`) })}</div>
            </div></div>`).join('')}
            ${extra > 0 ? `<div class="dcard more" style="--i:${shown.length}">${tr('discover.more', { n: extra }, `+${extra}종`)}</div>` : ''}</div>
          <button class="btn primary" id="dGo">${tr('discover.go', null, '진범 감별 퀘스트로 →')}</button></div>`;
      const close = ui.openOverlay(overlay, html, { onClose: onDone, focus: '#dGo' });
      overlay.querySelector('#dGo').addEventListener('click', close);
    },
  };

  const api = { XP, LEVELS, VERDICT, STAMPS, BADGES, CATEGORY_ORDER, createState, levelFor, progressPct, completeStep, completeStage, starsFor, discover,
                hasAnswer, answer, chapterProgress, completeChapter, noteEdit, applyResults, stampSvg, starsHtml,
                recordGrade, levelPips, strengthScore, sortCards, dexSummary, ui, art: null };
  return api;
});
