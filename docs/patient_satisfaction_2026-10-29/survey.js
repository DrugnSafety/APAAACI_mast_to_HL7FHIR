// This offline preview never sends responses to a server.
(() => {
  'use strict';
  const definition = JSON.parse(document.getElementById('survey-definition').textContent);
  const panels = ['entry', 'ratings', 'nonviewer', 'review', 'done', 'stopped'];
  const selected = name => document.querySelector(`input[name="${name}"]:checked`)?.value ?? null;
  const label = (options, value) => options.find(o => String(o.value) === String(value))?.label ?? '응답하지 않음';
  const hasViewed = () => selected('material') !== 'neither';
  const show = id => {
    panels.forEach(name => { document.getElementById(name).hidden = name !== id; });
    const stage = id === 'entry' ? 0 : ['ratings', 'nonviewer'].includes(id) ? 1 : 2;
    document.querySelectorAll('.progress li').forEach((li, i) => {
      if (i === stage) li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
    });
    document.querySelector(`#${id} h2`).focus();
    window.scrollTo({ top: 0, behavior: 'instant' });
  };
  const reset = () => {
    document.querySelectorAll('input[type="radio"]').forEach(input => { input.checked = false; });
    document.getElementById('comment').value = '';
    document.getElementById('comment-count').textContent = '0 / 300자';
    document.getElementById('review-content').replaceChildren();
    document.getElementById('entry-error').hidden = true;
    document.querySelectorAll('details').forEach(details => { details.open = false; });
  };
  const response = () => ({
    schema: 'APAAACI-SAT-PREVIEW-01',
    survey_id: definition.id,
    survey_version: definition.version,
    preview_only: true,
    transmitted_to_hospital: false,
    exported_at: new Date().toISOString(),
    respondent_type: selected('respondent'),
    materials_viewed: selected('material'),
    answers: Object.fromEntries(definition.items.map(item => {
      const value = hasViewed() ? selected(item.id) : null;
      return [item.id, hasViewed()
        ? { status: value === null ? 'blank' : /^\d$/.test(value) ? 'answered' : value,
            value: value !== null && /^\d$/.test(value) ? Number(value) : null }
        : { status: 'not_viewed', value: null }];
    })),
    nonviewer_reason: hasViewed() ? null : selected('nonviewer_reason'),
    comment: hasViewed() ? document.getElementById('comment').value.trim() : null
  });
  const addSummary = (term, answer) => {
    const dt = document.createElement('dt'); dt.textContent = term;
    const dd = document.createElement('dd'); dd.textContent = answer; dd.className = 'answer';
    document.getElementById('review-content').append(dt, dd);
  };
  const review = () => {
    document.getElementById('review-content').replaceChildren();
    addSummary('응답자', label(definition.respondents, selected('respondent')));
    addSummary('본 자료', label(definition.materials, selected('material')));
    if (hasViewed()) {
      definition.items.forEach(item => addSummary(`${item.id}. ${item.text}`,
        selected(item.id) === null ? '응답하지 않음 (빈 답변)' : label(definition.scale, selected(item.id))));
      addSummary('개선 의견', document.getElementById('comment').value.trim() || '작성하지 않음');
    } else {
      addSummary('보지 못한 이유', selected('nonviewer_reason') === null ? '응답하지 않음' : label(definition.nonviewer_reasons, selected('nonviewer_reason')));
      addSummary('평가 문항', '자료 미열람으로 건너뜀');
    }
    show('review');
  };
  document.getElementById('entry-next').addEventListener('click', () => {
    const error = document.getElementById('entry-error');
    if (!selected('respondent') || !selected('material')) {
      error.textContent = '응답자와 본 자료를 골라 주세요. 원하지 않으면 설문을 종료할 수 있습니다.';
      error.hidden = false;
      document.querySelector(`input[name="${!selected('respondent') ? 'respondent' : 'material'}"]`).focus();
      return;
    }
    error.hidden = true;
    show(hasViewed() ? 'ratings' : 'nonviewer');
  });
  document.querySelectorAll('[data-back-entry]').forEach(b => b.addEventListener('click', () => show('entry')));
  document.getElementById('ratings-next').addEventListener('click', review);
  document.getElementById('nonviewer-next').addEventListener('click', review);
  document.getElementById('review-back').addEventListener('click', () => show(hasViewed() ? 'ratings' : 'nonviewer'));
  document.getElementById('finish').addEventListener('click', () => show('done'));
  document.querySelectorAll('[data-exit]').forEach(b => b.addEventListener('click', () => { reset(); show('stopped'); }));
  ['restart', 'restart-stopped'].forEach(id => document.getElementById(id).addEventListener('click', () => { reset(); show('entry'); }));
  document.getElementById('comment').addEventListener('input', e => {
    document.getElementById('comment-count').textContent = `${e.target.value.length} / 300자`;
  });
  document.getElementById('download-response').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(response(), null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'APAAACI_satisfaction_preview.json';
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
})();
