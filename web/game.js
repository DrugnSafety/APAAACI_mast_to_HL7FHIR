/* =========================================================================
   알러젠 탐험 퀘스트 — 게임 레이어 (XP·레벨·도감·배지·연출)
   판정 로직과 무관한 프레젠테이션 상태만 다룬다. 답변 내용으로 XP 를 차등하지 않는다.
   UMD: 브라우저에서는 window.Game, Node 에서는 module.exports (단위 테스트용)
   ========================================================================= */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.Game = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const XP = { STEP: 50, DISCOVER: 10, DISCOVER_CAP: 100, ANSWER: 10, CHAPTER: 30 };
  const LEVELS = [
    { min: 0, title: '새싹 탐험가' },
    { min: 200, title: '숙련 탐험가' },
    { min: 500, title: '알러젠 마스터' },
  ];
  const VERDICT = {
    clinically_relevant: { stamp: '진범 확정', tone: 'relevant', note: '노출 시 증상이 재현되는 알러젠' },
    sensitized_only: { stamp: '무혐의 · 감작만', tone: 'sensitized', note: '감작은 남아 있어 추적 필요' },
    indeterminate: { stamp: '관찰 대상', tone: 'indet', note: '노출 시 증상을 기록해 확인' },
    not_assessed: { stamp: '미확인', tone: 'na', note: '' },
  };
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
    food: svg('<circle cx="20" cy="22" r="10"/><circle cx="20" cy="22" r="4"/><path d="M6 12v8M34 12v8"/>'),
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
    return { xp: 0, stepsDone: {}, chaptersDone: {}, answered: {}, discovered: {}, edits: 0, unsureUsed: false, badges: [], severeFlag: false };
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
  function discover(g, rows, testType) {
    let gained = 0;
    rows.forEach(r => {
      const key = r.allergen_name; if (!key || g.discovered[key]) return;
      g.discovered[key] = { name: r.korean_name || r.allergen_name, category: r.category || 'other', stars: starsFor(r, testType), verdict: null };
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
  const ui = {
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
    // 발견 오버레이: cards = [{name, category, stars}], 순차 뒤집기 후 onDone
    showDiscovery(overlay, cards, onDone) {
      if (!overlay) { onDone(); return; }
      const shown = cards.slice(0, 12), extra = cards.length - shown.length;
      const prevFocus = document.activeElement;
      const bg = [document.querySelector('header'), document.querySelector('main')].filter(Boolean);
      bg.forEach(el => { el.inert = true; });
      overlay.innerHTML = `<div class="discover" role="dialog" aria-modal="true" aria-label="${esc(tr('discover.aria', null, '발견한 알러젠'))}">
          <div class="d-eyebrow">${esc(tr('discover.eyebrow', null, '발견!'))}</div>
          <h2>${tr('discover.h2', { n: cards.length }, `양성 흔적 ${cards.length}종을 도감에 등록했어요`)}</h2>
          <p>${tr('discover.p', null, '아직 판정은 <b>미확인</b>입니다. 다음 퀘스트에서 진범을 가려냅니다.')}</p>
          <div class="discover-deck">${shown.map((c, i) => `<div class="dcard" style="--i:${i}">
              <div class="dstamp">${stampSvg(c.category)}</div>
              <div class="dname">${esc(c.name)}</div>${starsHtml(c.stars)}
              <div class="dq">?</div></div>`).join('')}
            ${extra > 0 ? `<div class="dcard more" style="--i:${shown.length}">${tr('discover.more', { n: extra }, `+${extra}종`)}</div>` : ''}</div>
          <button class="btn primary" id="dGo">${tr('discover.go', null, '진범 감별 퀘스트로 →')}</button></div>`;
      overlay.classList.remove('hidden');
      const done = () => { overlay.classList.add('hidden'); overlay.innerHTML = ''; bg.forEach(el => { el.inert = false; }); onDone(); if (prevFocus && prevFocus.focus) { try { prevFocus.focus(); } catch (_) {} } };
      overlay.addEventListener('keydown', e => { if (e.key === 'Escape') done(); }, { once: true });
      overlay.querySelector('#dGo').addEventListener('click', done);
      overlay.querySelector('#dGo').focus();
    },
  };

  return { XP, LEVELS, VERDICT, STAMPS, BADGES, createState, levelFor, progressPct, completeStep, starsFor, discover,
           hasAnswer, answer, chapterProgress, completeChapter, noteEdit, applyResults, stampSvg, starsHtml, ui };
});
