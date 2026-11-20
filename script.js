(() => {
  'use strict';
  const panel = document.querySelector('[data-player]');
  if (!panel) return;
  const video = panel.querySelector('video');
  const languageButtons = [...panel.querySelectorAll('[data-lang]')];
  const chapterButtons = [...panel.querySelectorAll('[data-seek]')];
  const title = panel.querySelector('[data-film-title]');
  const description = panel.querySelector('[data-film-description]');
  const download = panel.querySelector('[data-film-download]');
  const chaptersHeading = panel.querySelector('[data-chapter-heading]');
  const film = {
    ko: {
      title: '검사표 한 장이 결과지가 되기까지',
      description: '합성 검사표에서 숫자를 대조하고, 증상 문진을 거쳐 환자 자료와 FHIR 기록을 만드는 과정을 따라갑니다.',
      chapters: ['검사표 입력', 'OCR 대조와 확인', '증상 문진', '환자 결과지', '교육 카드뉴스', 'FHIR 내보내기'],
      chapterLabel: '장면 선택', download: '한국어 영상 받기', caption: '한국어', mediaLabel: '한국어 과정 영상'
    },
    en: {
      title: 'From a test sheet to a patient report',
      description: 'Follow one synthetic test sheet through OCR review, symptom history, patient education and FHIR export.',
      chapters: ['Source test sheet', 'OCR and review', 'Symptom questions', 'Patient report', 'Education cards', 'FHIR export'],
      chapterLabel: 'Chapters', download: 'Download English video', caption: 'English', mediaLabel: 'English workflow film'
    }
  };
  let lang = 'ko';
  let revision = 0;
  function highlightChapter() {
    const time = video.currentTime || 0;
    let current = 0;
    chapterButtons.forEach((button, i) => { if (+button.dataset.seek <= time + 0.12) current = i; });
    chapterButtons.forEach((button, i) => {
      if (i === current) button.setAttribute('aria-current', 'step');
      else button.removeAttribute('aria-current');
    });
  }
  function switchFilm(next, keepTime) {
    if (!film[next] || next === lang) return;
    const id = ++revision;
    const time = keepTime ? video.currentTime || 0 : 0;
    const playing = !video.paused && !video.ended;
    const captionsEnabled = video.textTracks[0]?.mode === 'showing';
    lang = next;
    const f = film[next], base = 'motion/workflow-' + next;
    video.pause();
    video.setAttribute('lang', next);
    video.setAttribute('aria-label', f.mediaLabel);
    video.poster = base + '.jpg';
    video.src = base + '.mp4';
    const track = document.createElement('track');
    Object.assign(track, {kind: 'captions', srclang: next, label: f.caption, src: base + '.vtt'});
    video.querySelectorAll('track').forEach(prev => prev.remove());
    video.append(track);
    if (captionsEnabled) track.track.mode = 'showing';
    [title, description, download, chaptersHeading].forEach(el => el.lang = next);
    title.textContent = f.title; description.textContent = f.description;
    chaptersHeading.textContent = f.chapterLabel;
    const transcriptHeading = panel.querySelector('[data-transcript-heading]');
    transcriptHeading.lang = next;
    transcriptHeading.textContent = next === 'en' ? 'Read the transcript' : '영상 대본 읽기';
    panel.querySelectorAll('[data-transcript]').forEach(list => { list.hidden = list.lang !== next; });
    const status = panel.querySelector('[data-film-status]');
    status.lang = next;
    status.textContent = next === 'en' ? 'English film selected.' : '한국어 영상을 선택했습니다.';
    download.href = base + '.mp4'; download.querySelector('span').textContent = f.download;
    languageButtons.forEach(b => b.setAttribute('aria-pressed', String(b.dataset.lang === next)));
    chapterButtons.forEach((b, i) => {
      b.lang = next; b.querySelector('[data-chapter-text]').textContent = f.chapters[i];
    });
    video.addEventListener('loadedmetadata', () => {
      if (id !== revision) return;
      if (time > 0 && Number.isFinite(video.duration)) video.currentTime = Math.min(time, Math.max(0, video.duration - .1));
      if (playing) video.play().catch(() => {});
      highlightChapter();
    }, {once: true});
    video.load();
    highlightChapter();
    const url = new URL(location.href);
    if (next === 'en') url.searchParams.set('film', 'en'); else url.searchParams.delete('film');
    history.replaceState(null, '', url);
  }
  languageButtons.forEach(b => b.addEventListener('click', () => switchFilm(b.dataset.lang, true)));
  chapterButtons.forEach(b => b.addEventListener('click', () => {
    const start = Number(b.dataset.seek);
    const play = () => {
      video.currentTime = start;
      video.play().catch(() => {});
      highlightChapter();
    };
    if (video.readyState) play(); else {
      video.addEventListener('loadedmetadata', play, {once: true}); video.load();
    }
  }));
  video.addEventListener('timeupdate', highlightChapter);
  const initial = new URL(location.href).searchParams.get('film');
  if (initial === 'en') switchFilm('en', false);
  highlightChapter();
})();
