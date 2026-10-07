/* 관리자 대시보드 — 빌드 단계 없는 바닐라 JS.
   데이터는 전부 /api/admin/* (로그인 쿠키 필요)에서 온다. 저장된 리포트·카드뉴스 HTML 은
   스크립트가 막힌 sandbox iframe 안에서만 그리고, 그 밖의 값은 전부 이스케이프해서 넣는다. */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const nf = new Intl.NumberFormat('ko-KR');
  const fmtDate = (iso) => {
    if (!iso) return '–';
    const d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' });
  };
  const LANG_LABEL = { ko: '한국어', en: 'English', zh: '中文' };
  const STATUS_LABEL = { ready: '완료', pending: '대기', running: '생성 중', failed: '실패', skipped: '건너뜀', missing: '없음' };
  const REL_LABEL = { clinically_relevant: '임상적 유의', sensitized_only: '감작만', indeterminate: '미확정', not_assessed: '미평가' };
  const SCREENING_LABEL = {
    allergic_diseases: '알레르기 질환', disease_other: '기타 질환', current_medications: '복용 약', medication_note: '약 메모',
    antihistamine_recent: '최근 항히스타민제', symptom_present: '현재 증상', season_pattern: '계절 양상', worse_months: '악화 월',
    organ_systems: '증상 부위', symptom_severity: '증상 정도', perennial_symptom: '연중 증상', triggers_free_text: '유발 요인',
    oral_allergy_syndrome: '구강알레르기증후군', oas_foods: 'OAS 음식', food_systemic_reaction: '음식 전신 반응',
    food_reaction_foods: '전신 반응 음식', residence_country: '거주 국가', residence_region: '거주 지역',
    residence_postal_code: '우편번호', pets: '반려동물', pets_other: '기타 동물', notes: '메모',
  };

  const state = { days: 30, list: 'users', q: '', offset: 0, limit: 20, stats: null, drawer: null };

  // ---------------- API ----------------
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: opts.body ? { 'Content-Type': 'application/json' } : undefined,
      ...opts,
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* 본문 없음 */ }
    if (!res.ok) {
      const detail = data && data.detail;
      const err = new Error((detail && detail.message) || (typeof detail === 'string' ? detail : `요청 실패 (${res.status})`));
      err.status = res.status;
      err.code = detail && detail.code;
      if (res.status === 401 && !path.endsWith('/login')) showGate('login');
      throw err;
    }
    return data;
  }

  // ---------------- 로그인 ----------------
  function showGate(mode) {
    closeDrawer();
    $('app').classList.add('hidden');
    $('gate').classList.remove('hidden');
    $('loginForm').classList.toggle('hidden', mode !== 'login');
    $('disabledNote').classList.toggle('hidden', mode !== 'disabled');
    if (mode === 'login') $('loginUser').focus();
  }

  function showApp() {
    $('gate').classList.add('hidden');
    $('app').classList.remove('hidden');
    refresh();
  }

  $('loginForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('loginError').textContent = '';
    try {
      await api('/api/admin/login', {
        method: 'POST',
        body: JSON.stringify({ username: $('loginUser').value, password: $('loginPass').value }),
      });
      $('loginPass').value = '';
      showApp();
    } catch (err) {
      $('loginError').textContent = err.message;
    }
  });

  $('logoutBtn').addEventListener('click', async () => {
    try { await api('/api/admin/logout', { method: 'POST' }); } catch (_) { /* 무시 */ }
    showGate('login');
  });

  // ---------------- 집계 ----------------
  function delta(cur, prev) {
    if (!prev) return cur ? '<span class="delta up">신규</span>' : '<span class="muted">변동 없음</span>';
    const pct = Math.round(((cur - prev) / prev) * 100);
    if (pct === 0) return '<span class="muted">변동 없음</span>';
    return `<span class="delta ${pct > 0 ? 'up' : 'down'}">${pct > 0 ? '▲' : '▼'} ${Math.abs(pct)}%</span>`;
  }

  function renderKpis(s) {
    const p = s.period, t = s.totals;
    const prevLabel = `이전 ${s.days}일 대비`;
    const anonPct = t.sessions ? Math.round((t.anonymous_sessions / t.sessions) * 100) : 0;
    const tiles = [
      ['세션', p.sessions, `${delta(p.sessions, p.sessions_prev)} <span>${prevLabel}</span>`],
      ['신규 사용자', p.new_users, `${delta(p.new_users, p.new_users_prev)} <span>누적 ${nf.format(t.users)}명</span>`],
      ['상담 질문', p.chat_messages, `${delta(p.chat_messages, p.chat_messages_prev)} <span>누적 ${nf.format(t.chat_messages)}건</span>`],
      ['메일 발송', p.emails_sent, `${delta(p.emails_sent, p.emails_sent_prev)} <span>실패 누적 ${nf.format(t.emails_failed)}건</span>`],
      ['익명 세션 비율', `${anonPct}%`, `<span>전체 ${nf.format(t.sessions)}건 중 ${nf.format(t.anonymous_sessions)}건</span>`],
    ];
    $('kpis').innerHTML = tiles.map(([label, value, foot]) => `
      <div class="kpi"><div class="kpi-label">${label}</div>
        <div class="kpi-value">${typeof value === 'number' ? nf.format(value) : esc(value)}</div>
        <div class="kpi-foot">${foot}</div></div>`).join('');
  }

  function niceMax(v) {
    if (v <= 4) return 4;
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * pow;
  }

  function renderSeries(s) {
    const box = $('seriesChart');
    const rows = s.sessions_per_day;
    const total = rows.reduce((a, r) => a + r.sessions, 0);
    $('seriesSub').textContent = `최근 ${s.days}일 · 합계 ${nf.format(total)}건`;
    $('seriesTable').innerHTML = `<table><thead><tr><th>날짜</th><th class="r">세션</th></tr></thead><tbody>${
      rows.slice().reverse().map((r) => `<tr><td>${esc(r.date)}</td><td class="r">${nf.format(r.sessions)}</td></tr>`).join('')
    }</tbody></table>`;
    if (!total) { box.innerHTML = '<p class="empty">이 기간에 기록된 세션이 없습니다.</p>'; return; }

    const W = Math.max(box.clientWidth, 240), H = box.clientHeight || 230;
    const m = { l: 34, r: 6, t: 10, b: 24 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const max = niceMax(Math.max(...rows.map((r) => r.sessions)));
    const step = iw / rows.length;
    const gap = step >= 6 ? 2 : 1;
    const bw = Math.max(step - gap, 1);
    const y = (v) => m.t + ih - (v / max) * ih;
    const ticks = [0, max / 2, max];
    const every = Math.ceil(rows.length / Math.max(2, Math.floor(iw / 64)));
    const md = (d) => `${+d.slice(5, 7)}/${+d.slice(8, 10)}`;

    let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`;
    ticks.forEach((t) => {
      svg += `<line class="gridline" x1="${m.l}" x2="${W - m.r}" y1="${y(t)}" y2="${y(t)}"/>` +
             `<text class="axis" x="${m.l - 8}" y="${y(t) + 4}" text-anchor="end">${nf.format(t)}</text>`;
    });
    rows.forEach((r, i) => {
      const x = m.l + i * step + gap / 2;
      if (r.sessions > 0) {
        const top = y(r.sessions), h = m.t + ih - top, rad = Math.min(4, bw / 2, h);
        svg += `<path class="bar" data-i="${i}" d="M${x},${m.t + ih}V${top + rad}Q${x},${top} ${x + rad},${top}` +
               `H${x + bw - rad}Q${x + bw},${top} ${x + bw},${top + rad}V${m.t + ih}Z"/>`;
      }
      if (i % every === 0 && i + every / 2 < rows.length || i === rows.length - 1) {
        const anchor = i === rows.length - 1 ? 'end' : 'middle';
        const lx = i === rows.length - 1 ? W - m.r : x + bw / 2;
        svg += `<text class="axis" x="${lx}" y="${H - 6}" text-anchor="${anchor}">${md(r.date)}</text>`;
      }
    });
    rows.forEach((r, i) => {
      svg += `<rect class="hit" data-i="${i}" x="${m.l + i * step}" y="${m.t}" width="${step}" height="${ih}"/>`;
    });
    box.innerHTML = svg + '</svg>';

    const tip = $('tooltip');
    const bars = {};
    box.querySelectorAll('.bar').forEach((b) => { bars[b.dataset.i] = b; });
    box.querySelectorAll('.hit').forEach((hit) => {
      hit.addEventListener('pointerenter', () => {
        const r = rows[+hit.dataset.i];
        tip.innerHTML = `${esc(r.date)} · <b>${nf.format(r.sessions)}</b>건`;
        tip.classList.remove('hidden');
        const rect = hit.getBoundingClientRect();
        const tw = tip.offsetWidth;
        tip.style.left = `${Math.min(Math.max(rect.left + rect.width / 2 - tw / 2, 8), window.innerWidth - tw - 8)}px`;
        tip.style.top = `${Math.max(rect.top + (bars[hit.dataset.i] ? y(r.sessions) - m.t : ih) - 40, 8)}px`;
        if (bars[hit.dataset.i]) bars[hit.dataset.i].classList.add('on');
      });
      hit.addEventListener('pointerleave', () => {
        tip.classList.add('hidden');
        if (bars[hit.dataset.i]) bars[hit.dataset.i].classList.remove('on');
      });
    });
  }

  /** 구성비: 한 줄 누적 막대 + 값이 적힌 범례(색만으로 구분하지 않는다). */
  function renderSplit(el, items, emptyText) {
    const total = items.reduce((a, i) => a + i.value, 0);
    if (!total) { el.innerHTML = `<p class="empty">${emptyText}</p>`; return; }
    el.innerHTML = `<div class="stack">${items.filter((i) => i.value > 0).map((i) =>
      `<i style="flex:${i.value};background:${i.color}" title="${esc(i.label)} ${nf.format(i.value)}"></i>`).join('')}</div>
      <ul class="legend">${items.map((i) => `<li><span class="sw" style="background:${i.color}"></span>
        <span>${esc(i.label)}</span><span class="v">${nf.format(i.value)}</span>
        <span class="p">${Math.round((i.value / total) * 100)}%</span></li>`).join('')}</ul>`;
  }

  function renderTop(s) {
    const rows = s.top_allergens;
    if (!rows.length) { $('topAllergens').innerHTML = '<p class="empty">이 기간에 양성 항원 기록이 없습니다.</p>'; return; }
    const max = Math.max(...rows.map((r) => r.sessions));
    $('topAllergens').innerHTML = `<div class="hbars">${rows.map((r) => `
      <div class="hbar" title="${esc(r.allergen_name)} · 임상적 유의 ${nf.format(r.relevant)}건">
        <span class="name">${esc(r.korean_name || r.allergen_name)}${r.korean_name ? `<small>${esc(r.allergen_name)}</small>` : ''}</span>
        <span class="track"><span class="fill" style="display:block;width:${(r.sessions / max) * 100}%"></span></span>
        <span class="v">${nf.format(r.sessions)}</span>
      </div>`).join('')}</div>`;
  }

  function renderStatusLists(s) {
    const lo = s.localized_outputs;
    $('i18nStatus').innerHTML = `<ul class="legend">${['ready', 'pending', 'running', 'failed', 'skipped'].map((k) =>
      `<li style="grid-template-columns:minmax(0,1fr) auto"><span><span class="pill ${k}">${STATUS_LABEL[k]}</span></span>
        <span class="v">${nf.format(lo[k] || 0)}</span></li>`).join('')}</ul>`;
    $('mailStatus').innerHTML = `<ul class="legend">
      <li style="grid-template-columns:minmax(0,1fr) auto"><span><span class="pill ready">발송</span></span><span class="v">${nf.format(s.totals.emails_sent)}</span></li>
      <li style="grid-template-columns:minmax(0,1fr) auto"><span><span class="pill failed">실패</span></span><span class="v">${nf.format(s.totals.emails_failed)}</span></li></ul>`;
  }

  async function loadStats() {
    const tz = -new Date().getTimezoneOffset();
    const s = await api(`/api/admin/stats?days=${state.days}&tz_offset=${tz}`);
    state.stats = s;
    renderKpis(s);
    renderSeries(s);
    renderSplit($('langSplit'), ['ko', 'en', 'zh'].map((k) => (
      { label: LANG_LABEL[k], value: s.languages[k] || 0, color: `var(--lang-${k})` })), '이 기간에 기록된 세션이 없습니다.');
    renderSplit($('relSplit'), [
      { label: REL_LABEL.clinically_relevant, value: s.relevance.clinically_relevant || 0, color: 'var(--relevant)' },
      { label: REL_LABEL.indeterminate, value: s.relevance.indeterminate || 0, color: 'var(--indet)' },
      { label: REL_LABEL.sensitized_only, value: s.relevance.sensitized_only || 0, color: 'var(--sensitized)' },
    ], '이 기간에 양성 항원 기록이 없습니다.');
    renderTop(s);
    renderStatusLists(s);
  }

  // ---------------- 목록 ----------------
  const i18nPills = (m) => `<span class="pills">${['ko', 'en', 'zh'].map((l) => {
    const st = (m && m[l]) || 'missing';
    return `<span class="pill ${st}" title="${LANG_LABEL[l]}: ${STATUS_LABEL[st] || st}">${l}</span>`;
  }).join('')}</span>`;

  async function loadList() {
    const el = $('listTable');
    const { list, q, offset, limit } = state;
    let data, html;
    if (list === 'users') {
      data = await api(`/api/admin/users?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`);
      html = `<table><thead><tr><th>이메일</th><th class="r">세션</th><th class="r">상담 질문</th><th>최근 언어</th>
        <th>최근 세션</th><th>처음 등록</th></tr></thead><tbody>${data.items.map((u) => `
        <tr class="click" tabindex="0" data-user="${u.id}"><td><span class="primary-cell">${esc(u.email)}</span></td>
          <td class="r">${nf.format(u.session_count)}</td><td class="r">${nf.format(u.chat_count)}</td>
          <td>${esc(LANG_LABEL[u.lang] || u.lang || '–')}</td><td>${fmtDate(u.last_session_at)}</td>
          <td>${fmtDate(u.created_at)}</td></tr>`).join('')}</tbody></table>`;
    } else {
      const anon = list === 'anonymous' ? '&anonymous=true' : '';
      data = await api(`/api/admin/sessions?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}${anon}`);
      html = `<table><thead><tr><th>일시</th><th>사용자</th><th>검사</th><th class="r">양성/전체</th><th>언어</th>
        <th>번역</th><th class="r">상담</th></tr></thead><tbody>${data.items.map((s) => `
        <tr class="click" tabindex="0" data-session="${esc(s.id)}"><td>${fmtDate(s.created_at)}</td>
          <td><span class="primary-cell">${esc(s.email || '익명')}</span>${s.patient_name ? ` <span class="muted">· ${esc(s.patient_name)}</span>` : ''}</td>
          <td>${esc(s.test_type || '–')}</td><td class="r">${nf.format(s.positive_count)} / ${nf.format(s.result_count)}</td>
          <td>${esc(LANG_LABEL[s.lang] || s.lang)}</td><td>${i18nPills(s.i18n)}</td>
          <td class="r">${nf.format(s.chat_count)}</td></tr>`).join('')}</tbody></table>`;
    }
    if (!data.items.length) {
      html = `<p class="empty">${q ? '검색 결과가 없습니다.' : '아직 기록이 없습니다.'}</p>`;
    }
    el.innerHTML = html;
    const from = data.total ? offset + 1 : 0, to = Math.min(offset + limit, data.total);
    $('pager').innerHTML = `<span>${nf.format(from)}–${nf.format(to)} / ${nf.format(data.total)}</span>
      <div><button class="btn small" id="prevPage" ${offset ? '' : 'disabled'}>이전</button>
      <button class="btn small" id="nextPage" ${to < data.total ? '' : 'disabled'}>다음</button></div>`;
    $('prevPage').onclick = () => { state.offset = Math.max(0, offset - limit); safe(loadList); };
    $('nextPage').onclick = () => { state.offset = offset + limit; safe(loadList); };
  }

  function rowActivate(e) {
    if (e.type === 'keydown' && e.key !== 'Enter' && e.key !== ' ') return;
    const tr = e.target.closest('tr.click');
    if (!tr) return;
    e.preventDefault();
    if (tr.dataset.user) openUser(+tr.dataset.user);
    else openSession(tr.dataset.session);
  }
  $('listTable').addEventListener('click', rowActivate);
  $('listTable').addEventListener('keydown', rowActivate);

  // ---------------- 서랍 ----------------
  let lastFocus = null;
  function openDrawer(title, sub) {
    lastFocus = document.activeElement;
    $('drawerTitle').textContent = title;
    $('drawerSub').textContent = sub || '';
    $('drawerBody').innerHTML = '<p class="empty">불러오는 중…</p>';
    $('drawer').classList.add('open');
    $('drawer').setAttribute('aria-hidden', 'false');
    $('scrim').classList.remove('hidden');
    $('drawer').focus();
  }
  function closeDrawer() {
    $('drawer').classList.remove('open');
    $('drawer').setAttribute('aria-hidden', 'true');
    $('scrim').classList.add('hidden');
    state.drawer = null;
    if (lastFocus && lastFocus.focus) lastFocus.focus();
    lastFocus = null;
  }
  $('drawerClose').addEventListener('click', closeDrawer);
  $('scrim').addEventListener('click', closeDrawer);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('drawer').classList.contains('open')) closeDrawer(); });

  async function openUser(id) {
    openDrawer('사용자', '');
    try {
      const u = await api(`/api/admin/users/${id}`);
      $('drawerTitle').textContent = u.email;
      $('drawerSub').textContent = `처음 등록 ${fmtDate(u.created_at)} · 세션 ${u.sessions.length}건`;
      const body = $('drawerBody');
      body.innerHTML = `
        <div class="row between"><h3 style="font-size:13px;color:var(--text-2)">세션</h3>
          <button class="btn small danger" id="delUser">사용자와 모든 기록 삭제</button></div>
        <div class="session-list">${u.sessions.map((s) => `
          <button class="session-item" data-session="${esc(s.id)}">
            <span><b>${fmtDate(s.created_at)}</b> <span class="muted">· ${esc(s.test_type || '검사')} · 양성 ${s.positive_count}/${s.result_count}</span></span>
            ${i18nPills(s.i18n)}</button>`).join('') || '<p class="empty">세션이 없습니다.</p>'}</div>
        <div id="sessionDetail"></div>`;
      body.querySelectorAll('.session-item').forEach((btn) => btn.addEventListener('click', () => {
        body.querySelectorAll('.session-item').forEach((b) => b.classList.toggle('on', b === btn));
        loadSession(btn.dataset.session, $('sessionDetail'));
      }));
      $('delUser').addEventListener('click', async () => {
        if (!confirm(`${u.email} 사용자의 세션·산출물·상담 대화를 모두 삭제합니다. 되돌릴 수 없습니다.`)) return;
        await safe(() => api(`/api/admin/users/${id}`, { method: 'DELETE' }));
        closeDrawer();
        refresh();
      });
      const first = body.querySelector('.session-item');
      if (first) first.click();
    } catch (err) { drawerError(err); }
  }

  function openSession(id) {
    openDrawer('세션', id);
    $('drawerBody').innerHTML = '<div id="sessionDetail"></div>';
    loadSession(id, $('sessionDetail'));
  }

  function drawerError(err) {
    $('drawerBody').innerHTML = `<p class="banner">${esc(err.message)}</p>`;
  }

  const show = (v) => {
    if (v === null || v === undefined || v === '') return '';
    if (typeof v === 'boolean') return v ? '예' : '아니오';
    if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : x)).join(', ') : '';
    if (typeof v === 'object') return JSON.stringify(v);
    return String(v);
  };
  // pairs: [라벨, 값, (선택) 원래 코드 — 라벨에 마우스를 올리면 보인다]
  const kv = (pairs) => {
    const rows = pairs.filter(([, v]) => show(v) !== '');
    return rows.length
      ? `<dl class="kv">${rows.map(([k, v, code]) => `<dt${code ? ` title="${esc(code)}"` : ''}>${esc(k)}</dt><dd>${esc(show(v))}</dd>`).join('')}</dl>`
      : '<p class="muted">입력된 내용이 없습니다.</p>';
  };

  // ---------------- 코드 → 사람이 읽는 라벨 ----------------
  // 저장된 입력은 코드다(allergic_rhinitis, animal_contact__agn3=yes). 스크리닝 선택지는 /api/health,
  // 문진 문항·선택지는 저장된 입력으로 다시 만든 문진(/api/questionnaire)에서 라벨을 얻는다. 모르는 코드는 그대로 보인다.
  const SCREENING_OPTION_GROUP = { allergic_diseases: 'diseases', current_medications: 'medications', organ_systems: 'organ_systems' };
  const SCREENING_VALUE_LABEL = {
    pets: { cat: '고양이', dog: '개', other: '기타', none: '없음' },
    residence_country: { KR: '대한민국', US: '미국' },
  };
  let screeningLabelsPromise = null;
  function loadScreeningLabels() {
    screeningLabelsPromise = screeningLabelsPromise || api('/api/health?lang=ko').then((h) => {
      const out = Object.assign({}, SCREENING_VALUE_LABEL);
      Object.entries(SCREENING_OPTION_GROUP).forEach(([field, group]) => {
        out[field] = Object.fromEntries(((h.screening_options || {})[group] || []).map((o) => [o.code, o.label]));
      });
      return out;
    }).catch(() => { screeningLabelsPromise = null; return SCREENING_VALUE_LABEL; });
    return screeningLabelsPromise;
  }
  async function loadQuestionLabels(input) {
    const out = {};
    if (!input || !input.ocr || !Object.keys(input.answers || {}).length) return out;
    try {
      const res = await api('/api/questionnaire', { method: 'POST', body: JSON.stringify({ ocr: input.ocr, screening: input.screening, lang: 'ko' }) });
      ((res.questionnaire || {}).sections || []).forEach((sec) => (sec.questions || []).forEach((q) => {
        out[q.id] = { title: String(q.title || '').replace(/\*\*/g, ''), options: Object.fromEntries((q.options || []).map((o) => [o.value, o.label])) };
      }));
    } catch (_) { /* 라벨 없이 코드로 보여 준다 */ }
    return out;
  }
  const labelOf = (map, v) => (Array.isArray(v) ? v.map((x) => labelOf(map, x)) : ((map && typeof v === 'string' && map[v]) || v));

  async function loadSession(id, el) {
    el.innerHTML = '<p class="empty">불러오는 중…</p>';
    let s;
    try { s = await api(`/api/admin/sessions/${encodeURIComponent(id)}`); } catch (err) {
      el.innerHTML = `<p class="banner">${esc(err.message)}</p>`; return;
    }
    const [screeningLabels, questionLabels] = await Promise.all([loadScreeningLabels(), loadQuestionLabels(s.input)]);
    state.drawer = { session: s, tab: 'results', lang: s.lang, view: 'report', fhirView: 'allergy', labels: { screening: screeningLabels, questions: questionLabels } };
    if ($('drawerTitle').textContent === '세션') {
      $('drawerTitle').textContent = s.email || '익명 세션';
      $('drawerSub').textContent = `세션 ${s.id}`;
    }
    el.innerHTML = `
      <div class="box"><div class="row between">
        <span class="sub">${fmtDate(s.created_at)} · ${esc(LANG_LABEL[s.lang] || s.lang)} · ${esc(s.ui)} 화면 · <span class="mono">${esc(s.id.slice(0, 8))}</span></span>
        <button class="btn small danger" id="delSession">세션 삭제</button></div></div>
      <div class="tabs" role="tablist">
        <button role="tab" data-tab="results" class="on">검사결과</button>
        <button role="tab" data-tab="outputs">산출물</button>
        <button role="tab" data-tab="chat">상담 ${s.chat.length ? `(${Math.ceil(s.chat.length / 2)})` : ''}</button>
        <button role="tab" data-tab="fhir">FHIR</button>
      </div>
      <div class="panel" id="tabPanel"></div>`;
    el.style.display = 'grid';
    el.style.gap = '14px';
    el.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => {
      el.querySelectorAll('.tabs button').forEach((x) => x.classList.toggle('on', x === b));
      state.drawer.tab = b.dataset.tab;
      renderTab();
    }));
    $('delSession').addEventListener('click', async () => {
      if (!confirm('이 세션의 입력·산출물·상담 대화를 삭제합니다. 되돌릴 수 없습니다.')) return;
      await safe(() => api(`/api/admin/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }));
      closeDrawer();
      refresh();
    });
    renderTab();
  }

  function renderTab() {
    const d = state.drawer;
    if (!d) return;
    const panel = $('tabPanel');
    if (d.tab === 'results') panel.innerHTML = resultsHtml(d.session, d.labels);
    else if (d.tab === 'chat') panel.innerHTML = chatHtml(d.session);
    else if (d.tab === 'fhir') renderFhir(panel);
    else renderOutputs(panel);
  }

  function resultsHtml(s, labels) {
    const ocr = (s.input && s.input.ocr) || {};
    const p = ocr.patient || {};
    const rel = {};
    (s.allergens || []).forEach((a) => { rel[a.allergen_name] = a.relevance; });
    const rows = ocr.results || [];
    const L = labels || { screening: {}, questions: {} };
    const answers = Object.entries((s.input && s.input.answers) || {}).map(([qid, v]) => {
      const q = L.questions[qid];
      return [q && q.title ? q.title : qid, labelOf(q && q.options, v), qid];
    });
    const screening = Object.entries((s.input && s.input.screening) || {})
      .map(([k, v]) => [SCREENING_LABEL[k] || k, labelOf(L.screening[k], v), k]);
    return `
      <div><h3>환자 정보</h3><div class="box">${kv([['이름', p.name], ['나이', p.age], ['성별', p.gender],
        ['검사일', p.test_date], ['보고일', p.report_date], ['검사 종류', ocr.test_type], ['기관', p.facility],
        ['의뢰', p.ordering_provider]])}</div></div>
      <div><h3>입력한 검사결과 (${rows.length}행)</h3><div class="box"><div class="table-wrap"><table>
        <thead><tr><th>항원</th><th class="r">수치</th><th class="r">Class</th><th>결과</th><th>판정</th></tr></thead>
        <tbody>${rows.map((r) => {
          const rv = rel[r.allergen_name];
          const value = r.value ?? r.value_text ?? r.raw_value ?? '';
          return `<tr><td><span class="primary-cell">${esc(r.korean_name || r.allergen_name)}</span>
            ${r.korean_name ? `<div class="sub">${esc(r.allergen_name)}</div>` : ''}</td>
            <td class="r">${esc(value)}${r.unit && value !== '' ? ` <span class="muted">${esc(r.unit)}</span>` : ''}</td>
            <td class="r">${esc(r.class_value ?? '')}</td><td>${esc(r.interpretation ?? '')}</td>
            <td>${rv ? `<span class="rel ${esc(rv)}">${esc(REL_LABEL[rv] || rv)}</span>` : '<span class="muted">–</span>'}</td></tr>`;
        }).join('')}</tbody></table></div></div></div>
      <div><h3>문진 프로필</h3><div class="box">${kv(screening)}</div></div>
      <div><h3>문진 답변 (${answers.length})</h3><div class="box">${kv(answers)}</div></div>
      <div><h3>메일 발송 기록</h3><div class="box">${s.emails.length ? `<div class="table-wrap"><table>
        <thead><tr><th>일시</th><th>받는 주소</th><th>언어</th><th>상태</th><th>첨부</th></tr></thead><tbody>${
        s.emails.map((e) => `<tr><td>${fmtDate(e.created_at)}</td><td>${esc(e.to_email)}</td><td>${esc(e.lang || '')}</td>
          <td><span class="pill ${e.status === 'sent' ? 'ready' : 'failed'}">${e.status === 'sent' ? '발송' : '실패'}</span>
            ${e.error ? `<span class="sub"> ${esc(e.error)}</span>` : ''}</td>
          <td class="sub">${esc((e.attachments || []).join(', '))}</td></tr>`).join('')}</tbody></table></div>`
        : '<p class="muted">보낸 메일이 없습니다.</p>'}</div></div>`;
  }

  function chatHtml(s) {
    if (!s.chat.length) return '<p class="empty">상담 대화가 없습니다.</p>';
    return `<div class="chat">${s.chat.map((m) => `
      <div class="msg ${m.role === 'user' ? 'user' : 'assistant'}">${esc(m.content)}
        <small>${m.role === 'user' ? '환자' : '상담 답변'} · ${fmtDate(m.created_at)}${m.source ? ` · ${esc(m.source)}` : ''}</small></div>`).join('')}</div>`;
  }

  // FHIR 번들은 세션에 저장하지 않는다 — 저장된 입력(검사결과·프로필·답변)으로 환자 화면과 같은 POST /api/fhir 를 불러 다시 만든다.
  const FHIR_VIEWS = [['allergy', 'AllergyIntolerance', 'allergy_intolerance_bundle'], ['obs', 'Observation', 'observation_bundle'],
    ['screening', 'Condition · Questionnaire', 'screening_bundle']];
  async function renderFhir(panel) {
    const d = state.drawer, s = d.session;
    const input = s.input || {};
    if (!input.ocr) { panel.innerHTML = '<p class="empty">저장된 검사결과 입력이 없어 FHIR 번들을 만들 수 없습니다.</p>'; return; }
    if (!d.fhir) {
      panel.innerHTML = '<p class="empty">FHIR 번들을 만드는 중…</p>';
      try {
        d.fhir = await api('/api/fhir', { method: 'POST', body: JSON.stringify({ ocr: input.ocr, screening: input.screening, answers: input.answers || {} }) });
      } catch (err) { if (state.drawer === d && d.tab === 'fhir') panel.innerHTML = `<p class="banner">FHIR 번들을 만들지 못했습니다: ${esc(err.message)}</p>`; return; }
      if (state.drawer !== d || d.tab !== 'fhir') return;
    }
    const bundleOf = (key) => d.fhir[key] || { resourceType: 'Bundle', type: 'collection', entry: [] };
    const cur = FHIR_VIEWS.find(([k]) => k === d.fhirView) || FHIR_VIEWS[0];
    const json = JSON.stringify(bundleOf(cur[2]), null, 2);
    panel.innerHTML = `
      <div class="row between">
        <div class="seg" id="fhirSeg">${FHIR_VIEWS.map(([k, label, key]) =>
          `<button data-view="${k}" class="${k === cur[0] ? 'on' : ''}">${label} <span class="pill">${(bundleOf(key).entry || []).length}</span></button>`).join('')}</div>
        <button class="btn small" id="fhirDownload">JSON 내려받기</button>
      </div>
      <p class="sub">저장된 입력으로 지금 다시 만든 번들입니다(세션에는 번들을 저장하지 않습니다).</p>
      <pre class="md box" id="fhirJson" tabindex="0"></pre>`;
    $('fhirJson').textContent = json;
    panel.querySelectorAll('#fhirSeg button').forEach((b) => b.addEventListener('click', () => { d.fhirView = b.dataset.view; renderFhir(panel); }));
    $('fhirDownload').addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([json], { type: 'application/fhir+json' }));
      const a = document.createElement('a');
      a.href = url; a.download = `session_${s.id.slice(0, 8)}_${cur[2]}.json`; a.click();
      URL.revokeObjectURL(url);
    });
  }

  async function renderOutputs(panel) {
    const d = state.drawer, s = d.session;
    const st = s.i18n[d.lang] || { status: 'missing' };
    panel.innerHTML = `
      <div class="row between">
        <div class="seg" id="outLang">${['ko', 'en', 'zh'].map((l) =>
          `<button data-lang="${l}" class="${l === d.lang ? 'on' : ''}">${LANG_LABEL[l]}
            <span class="pill ${s.i18n[l].status}">${STATUS_LABEL[s.i18n[l].status] || s.i18n[l].status}</span></button>`).join('')}</div>
        <div class="seg" id="outView">${[['report', '리포트'], ['cardnews', '카드뉴스'], ['markdown', '마크다운']].map(([k, label]) =>
          `<button data-view="${k}" class="${k === d.view ? 'on' : ''}">${label}</button>`).join('')}</div>
      </div>
      <div class="row${st.status === 'ready' ? '' : ' hidden'}" id="outPdfRow">
        <button class="btn small" id="outPdf">PDF 받기</button>
        <span class="sub" id="outPdfMsg" role="status" aria-live="polite"></span>
      </div>
      <div id="outBody"><p class="empty">불러오는 중…</p></div>`;
    panel.querySelectorAll('#outLang button').forEach((b) => b.addEventListener('click', () => { d.lang = b.dataset.lang; renderOutputs(panel); }));
    panel.querySelectorAll('#outView button').forEach((b) => b.addEventListener('click', () => { d.view = b.dataset.view; renderOutputs(panel); }));
    const body = $('outBody');

    // 리포트 PDF — 관리자 전용 경로가 따로 없어 세션 경로(/api/sessions/{id}/report.pdf)를 쓴다. 준비된(ready) 언어만 받을 수 있다.
    const pdfBtn = $('outPdf');
    pdfBtn.addEventListener('click', async () => {
      const lang = d.lang, note = $('outPdfMsg');
      pdfBtn.disabled = true; pdfBtn.textContent = 'PDF 만드는 중…'; note.textContent = '';
      try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(s.id)}/report.pdf?lang=${lang}`, { credentials: 'same-origin' });
        if (!res.ok) {
          const detail = (await res.json().catch(() => ({}))).detail;
          throw new Error((detail && detail.message) || `PDF 를 받지 못했습니다 (${res.status})`);
        }
        const url = URL.createObjectURL(await res.blob());
        const a = document.createElement('a');
        a.href = url; a.download = `session_${s.id.slice(0, 8)}_report_${lang}.pdf`; a.click();
        URL.revokeObjectURL(url);
      } catch (err) { if ($('outPdfMsg')) $('outPdfMsg').textContent = err.message || 'PDF 를 받지 못했습니다.'; }
      if ($('outPdf')) { $('outPdf').disabled = false; $('outPdf').textContent = 'PDF 받기'; }
    });

    if (st.status !== 'ready') {
      body.innerHTML = `<div class="box"><p><b>${LANG_LABEL[d.lang]}</b> 산출물이 없습니다 — 상태:
        <span class="pill ${st.status}">${STATUS_LABEL[st.status] || esc(st.status)}</span>
        ${st.error ? `<span class="sub">(${esc(st.error)})</span>` : ''}</p>
        <p class="sub" style="margin:6px 0 10px">번역 엔진(API 키)이 없으면 '건너뜀', 서버가 중간에 재시작되면 '대기'로 남을 수 있습니다.</p>
        <button class="btn small" id="retryBtn">다시 생성</button></div>`;
      $('retryBtn').addEventListener('click', async (e) => {
        e.target.disabled = true;
        e.target.textContent = '생성 중…';
        try {
          const r = await api(`/api/admin/sessions/${encodeURIComponent(s.id)}/i18n/retry`, { method: 'POST' });
          s.i18n = r.langs;
        } catch (err) { showError(err); }
        if (state.drawer === d) renderOutputs(panel);
      });
      return;
    }
    try {
      d.cache = d.cache || {};
      const out = d.cache[d.lang] || (d.cache[d.lang] = await api(
        `/api/admin/sessions/${encodeURIComponent(s.id)}/outputs/${d.lang}`));
      if (state.drawer !== d || !$('outBody')) return;
      if (d.view === 'markdown') {
        const pre = document.createElement('pre');
        pre.className = 'md box';
        pre.textContent = out.report_markdown || '';
        body.replaceChildren(pre);
      } else {
        const frame = document.createElement('iframe');
        frame.className = 'frame';
        frame.setAttribute('sandbox', '');           // 저장된 HTML 의 스크립트 실행·동일 출처 접근 차단
        frame.setAttribute('referrerpolicy', 'no-referrer');
        frame.title = d.view === 'report' ? '환자용 리포트' : '카드뉴스';
        // sandbox 가 스크립트를 막으므로 <script> 는 미리 떼어 낸다(막힌 실행이 콘솔 오류로 남지 않게) — 카드뉴스는 스크립트 없이도 읽힌다
        const doc = new DOMParser().parseFromString((d.view === 'report' ? out.report_document_html : out.cardnews_html) || '', 'text/html');
        doc.querySelectorAll('script').forEach((el) => el.remove());
        frame.srcdoc = '<!doctype html>' + doc.documentElement.outerHTML;
        body.replaceChildren(frame);
      }
    } catch (err) { body.innerHTML = `<p class="banner">${esc(err.message)}</p>`; }
  }

  // ---------------- 공통 ----------------
  function showError(err) {
    if (err && err.status === 401) return;
    const el = $('pageError');
    el.textContent = (err && err.message) || '오류가 발생했습니다.';
    el.classList.remove('hidden');
  }
  async function safe(fn) {
    try { return await fn(); } catch (err) { showError(err); return null; }
  }
  function refresh() {
    $('pageError').classList.add('hidden');
    safe(loadStats);
    safe(loadList);
  }

  $('rangeSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    state.days = +b.dataset.days;
    $('rangeSeg').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
    safe(loadStats);
  });
  $('listSeg').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    state.list = b.dataset.list;
    state.offset = 0;
    $('listSeg').querySelectorAll('button').forEach((x) => {
      x.classList.toggle('on', x === b);
      x.setAttribute('aria-selected', String(x === b));
    });
    $('searchInput').placeholder = state.list === 'users' ? '이메일 검색' : '이메일 · 이름 · 세션 ID 검색';
    safe(loadList);
  });
  let searchTimer = null;
  $('searchInput').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value.trim(); state.offset = 0; safe(loadList); }, 250);
  });
  $('refreshBtn').addEventListener('click', refresh);
  $('seriesTableBtn').addEventListener('click', (e) => {
    const open = $('seriesTable').classList.toggle('hidden') === false;
    $('seriesChart').classList.toggle('hidden', open);
    e.target.textContent = open ? '그래프로 보기' : '표로 보기';
    e.target.setAttribute('aria-expanded', String(open));
    if (!open && state.stats) renderSeries(state.stats);
  });
  $('themeBtn').addEventListener('click', () => {
    const root = document.documentElement;
    const cur = root.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = cur === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('theme', next); } catch (_) { /* 저장 불가 환경 */ }
  });
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      if (state.stats && !$('seriesChart').classList.contains('hidden') && !$('app').classList.contains('hidden')) renderSeries(state.stats);
    }, 150);
  });

  // ---------------- 시작 ----------------
  (async () => {
    try {
      const st = await api('/api/admin/status');
      if (!st.enabled) showGate('disabled');
      else if (!st.authenticated) showGate('login');
      else showApp();
    } catch (err) {
      showGate('login');
      $('loginError').textContent = err.message;
    }
  })();
})();
