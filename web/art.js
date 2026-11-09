/* =========================================================================
   알러젠 탐험 퀘스트 — 일러스트(인라인 SVG) 모음
   · scene(id)        : 스테이지마다 하나씩 쓰는 장면(320×150)
   · plate(cat, name) : 도감 카드의 카테고리별 그림(200×130)
   · icon(id)         : 브리핑·보상에 쓰는 작은 아이콘(48×48)
   전부 직접 그린 도형이며 외부 이미지·라이브러리를 쓰지 않는다. 색은 styles.css 의
   .art .k-* 클래스(토큰)로 칠하므로 라이트/다크가 자동으로 따라온다. 움직임은 .an-* 클래스로만
   붙이고, prefers-reduced-motion 에서는 styles.css 가 모두 끈다. 모든 그림은 장식(aria-hidden)이다.
   UMD: 브라우저에서는 window.QuestArt (+ Game.art), Node 에서는 module.exports
   ========================================================================= */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else { root.QuestArt = api; if (root.Game) root.Game.art = api; }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const wrap = (cls, vb, inner, par) => `<svg class="art ${cls}" viewBox="${vb}"${par ? ` preserveAspectRatio="${par}"` : ''} aria-hidden="true" focusable="false">${inner}</svg>`;
  const wash = (c = 'k-wash') => `<path class="${c}" d="M16 130C0 72 66 14 162 16s158 46 142 114z"/>`;
  const ground = '<path class="ln soft" d="M14 130h292"/>';

  // 새싹 탐험가 — 모든 장면에 나오는 안내 캐릭터(레벨 1 칭호 '새싹 탐험가')
  function sprout(x, y, s, look) {
    const dx = look === 'left' ? -2 : look === 'right' ? 2 : 0;
    return `<g transform="translate(${x} ${y}) scale(${s || 1})">
      <ellipse class="k-shadow" cx="30" cy="72" rx="19" ry="3.5"/>
      <g class="an-bob">
        <g class="an-leaf"><path class="k-leaf ol" d="M30 24c-2-10-10-14-18-12 1 9 8 14 18 12z"/>
          <path class="k-leaf-d ol" d="M30 24c2-11 11-16 20-13-1 10-9 15-20 13z"/></g>
        <rect class="k-body ol" x="9" y="27" width="42" height="44" rx="21"/>
        <g class="an-blink"><circle class="k-ink" cx="${23 + dx}" cy="46" r="2.3"/><circle class="k-ink" cx="${37 + dx}" cy="46" r="2.3"/></g>
        <path class="ln thin" d="M${26 + dx} 53q4 3.5 8 0"/>
        <ellipse class="k-blush" cx="18" cy="52" rx="3.2" ry="2.1"/><ellipse class="k-blush" cx="42" cy="52" rx="3.2" ry="2.1"/>
      </g></g>`;
  }
  const lens = (x, y, r, cls) => `<g class="${cls || ''}"><circle class="k-glass ol" cx="${x}" cy="${y}" r="${r}"/>
      <path class="ln glint" d="M${x - r * .45} ${y - r * .2}a${r * .55} ${r * .55} 0 0 1 ${r * .4}-${r * .4}"/>
      <path class="ln thick" d="M${x + r * .72} ${y + r * .72}l${r * .8} ${r * .8}"/></g>`;
  const sheet = (x, y, w, h, rot, rows) => {
    let l = '';
    for (let i = 0; i < (rows || 4); i++) l += `<path class="ln soft" d="M${x + 8} ${y + 14 + i * 10}h${w - 16 - (i % 2) * 10}"/>`;
    return `<g ${rot ? `transform="rotate(${rot} ${x + w / 2} ${y + h / 2})"` : ''}><rect class="k-paper ol" x="${x}" y="${y}" width="${w}" height="${h}" rx="5"/>${l}</g>`;
  };
  const speck = (x, y, r, d, c) => `<circle class="${c || 'k-amber'} an-drift" style="--d:${d}s" cx="${x}" cy="${y}" r="${r}"/>`;
  const specks = (list, c) => list.map(([x, y, r], i) => speck(x, y, r, (i * .37) % 2.4, c)).join('');
  const foot = (x, y, r, d) => `<g class="k-leaf-d an-step" style="--d:${d}s" transform="rotate(${r} ${x} ${y})"><ellipse cx="${x}" cy="${y}" rx="4" ry="6"/><circle cx="${x - 3}" cy="${y - 9}" r="1.6"/><circle cx="${x + 1}" cy="${y - 10}" r="1.6"/><circle cx="${x + 5}" cy="${y - 8}" r="1.6"/></g>`;
  const miniCard = (x, y, rot, mark, d) => `<g class="an-rise" style="--d:${d}s"><g transform="rotate(${rot} ${x} ${y + 30})">
      <rect class="k-paper ol" x="${x - 21}" y="${y}" width="42" height="58" rx="6"/>
      <rect class="k-wash2" x="${x - 15}" y="${y + 6}" width="30" height="22" rx="4"/>
      <path class="ln soft" d="M${x - 14} ${y + 36}h28M${x - 14} ${y + 44}h18"/>${mark || ''}</g></g>`;

  const SCENES = {
    // Q1-① 임무 브리핑: 새싹 탐험가가 발자국(흔적)을 따라간다
    brief: () => wash() + ground +
      foot(118, 118, 62, .2) + foot(148, 110, 70, .5) + foot(180, 116, 60, .8) + foot(210, 108, 72, 1.1) +
      miniCard(262, 52, 8, '<text class="k-txt" x="262" y="76" text-anchor="middle">?</text>', 1.3) +
      sprout(28, 56, 1, 'right') + lens(92, 96, 13, 'an-peek'),
    // Q1-② 결과지 가져오기
    upload: () => wash('k-wash2') + ground +
      `<path class="k-leaf-d ol" d="M110 130V84a8 8 0 0 1 8-8h84a8 8 0 0 1 8 8v46z"/><path class="ln onfill" d="M110 96h100"/>
       <rect class="k-amber ol" x="146" y="88" width="28" height="14" rx="4"/>` +
      `<g class="an-drop">${sheet(132, 18, 56, 66, -5, 4)}</g>` +
      `<g class="an-float"><path class="ln leaf thick" d="M246 78V50M234 62l12-13 12 13"/></g>` + sprout(30, 58, .95, 'right'),
    // Q1 판독 중(OCR)
    scan: () => wash() + ground + sheet(112, 26, 96, 100, 0, 8) +
      `<rect class="k-scan an-scan" x="112" y="30" width="96" height="10" rx="3"/>` + lens(214, 70, 18, 'an-sweep') + sprout(22, 58, .9, 'right'),
    // Q2 증거 확인: 결과지와 대조표
    review: () => wash() + ground + sheet(62, 30, 84, 96, -4, 7) +
      `<rect class="k-paper ol" x="176" y="24" width="90" height="104" rx="7"/><rect class="k-amber ol" x="204" y="18" width="34" height="13" rx="5"/>
       ${[0, 1, 2].map(i => `<rect class="k-wash" x="188" y="${46 + i * 26}" width="16" height="16" rx="4"/><path class="ln soft" d="M212 ${54 + i * 26}h40"/>
         <path class="ln leaf thick an-tick" style="--d:${.3 + i * .35}s" pathLength="1" d="M191 ${54 + i * 26}l4 5 7-9"/>`).join('')}
       <g class="an-write"><path class="k-amber ol" d="M150 92l22-22 8 8-22 22-11 3z"/><path class="ln" d="M168 74l8 8"/></g>`,
    // Q2 도감 등록
    register: () => wash('k-wash2') + ground + miniCard(110, 50, -10, '', .1) + miniCard(160, 42, 0, '', .35) + miniCard(210, 50, 10, '', .6) +
      `<g class="an-pop" style="--d:.9s"><circle class="k-amber ol" cx="262" cy="42" r="15"/><path class="ln thick" d="M262 34v16M254 42h16"/></g>`,
    // Q3-① 탐험가 수첩
    profile_id: () => wash() + ground +
      `<g class="an-float"><rect class="k-paper ol" x="150" y="34" width="124" height="82" rx="10"/><rect class="k-leaf-d" x="150" y="34" width="124" height="20" rx="10"/><rect class="k-leaf-d" x="150" y="44" width="124" height="10"/>
        <circle class="k-wash ol" cx="182" cy="84" r="16"/><path class="k-leaf ol" d="M182 72c-1-6-6-8-10-7 1 5 4 8 10 7zM182 72c1-6 6-9 11-7-1 5-5 8-11 7z"/>
        <circle class="k-ink" cx="177" cy="86" r="1.6"/><circle class="k-ink" cx="187" cy="86" r="1.6"/>
        <path class="ln soft" d="M210 72h50M210 84h38M210 96h44"/></g>` + sprout(40, 56, 1, 'right'),
    // Q3-② 탐험 지역(거주지)
    profile_place: () => wash() + ground +
      `<path class="k-paper ol" d="M70 44l52-14 60 14 62-14v84l-62 14-60-14-52 14z"/><path class="ln soft" d="M122 30v84M182 44v84"/>
       <path class="ln leaf dash" d="M88 104q30-34 62-18t68-28"/>
       <g class="an-pin"><path class="k-red ol" d="M204 30a15 15 0 0 1 15 15c0 11-15 27-15 27s-15-16-15-27a15 15 0 0 1 15-15z"/><circle class="k-paper" cx="204" cy="45" r="5.5"/></g>` +
      specks([[262, 40, 3], [278, 66, 2.4], [256, 84, 2.8], [286, 100, 2.2]]) +
      `<g class="an-wobble"><circle class="k-paper ol" cx="44" cy="46" r="17"/><path class="k-red" d="M44 31l5 15h-10z"/><path class="k-slate" d="M44 61l-5-15h10z"/></g>`,
    // Q3-③ 진단받은 질환
    profile_disease: () => wash('k-wash2') + ground +
      `<rect class="k-paper ol" x="112" y="26" width="96" height="104" rx="8"/><rect class="k-slate ol" x="140" y="19" width="40" height="14" rx="5"/>
       <path class="ln soft" d="M126 104h68M126 114h46"/>
       <path class="ln red thick an-beat" pathLength="1" d="M124 76h16l7-18 11 34 8-22 5 6h25"/>
       <g class="an-pop" style="--d:.5s"><circle class="k-leaf ol" cx="236" cy="50" r="15"/><path class="ln onfill thick" d="M236 42v16M228 50h16"/></g>` + sprout(32, 58, .95, 'right'),
    // Q3-④ 쓰고 있는 약
    profile_meds: () => wash() + ground +
      `<rect class="k-paper ol" x="84" y="58" width="46" height="72" rx="8"/><rect class="k-leaf-d ol" x="80" y="44" width="54" height="16" rx="5"/><rect class="k-wash" x="92" y="78" width="30" height="26" rx="4"/><path class="ln leaf" d="M107 84v14M100 91h14"/>
       <g class="an-float" style="--d:.2s"><g transform="rotate(-28 176 68)"><rect class="k-amber ol" x="150" y="57" width="52" height="22" rx="11"/><path class="k-paper" d="M176 58.5h15a9.5 9.5 0 0 1 0 19h-15z"/><path class="ln" d="M176 57v22"/></g></g>
       <g class="an-float" style="--d:.9s"><circle class="k-paper ol" cx="168" cy="112" r="11"/><path class="ln soft" d="M160 112h16"/></g>
       <path class="k-slate ol" d="M226 62h30a6 6 0 0 1 6 6v20h-22v42h-20V68a6 6 0 0 1 6-6z"/><rect class="k-paper ol" x="228" y="44" width="26" height="20" rx="4"/>
       <path class="ln soft an-puff" d="M270 76h14M272 86h10M270 96h14"/>`,
    // Q3-⑤ 증상이 나타나는 부위
    profile_organs: () => wash('k-wash2') + ground +
      `<circle class="k-paper ol" cx="160" cy="46" r="24"/><path class="k-paper ol" d="M112 130c0-34 20-52 48-52s48 18 48 52z"/>
       <circle class="k-ink" cx="151" cy="42" r="2"/><circle class="k-ink" cx="169" cy="42" r="2"/><path class="ln thin" d="M160 46v7l-4 2"/><path class="ln thin" d="M153 60q7 4 14 0"/>
       ${[[160, 52, 0], [174, 40, .5], [148, 104, 1], [182, 110, 1.5]].map(([x, y, d]) => `<circle class="k-ping an-ping" style="--d:${d}s" cx="${x}" cy="${y}" r="9"/><circle class="k-red" cx="${x}" cy="${y}" r="3"/>`).join('')}
       <path class="ln dash" d="M196 40h34M200 110h30"/><rect class="k-paper ol" x="232" y="30" width="46" height="20" rx="6"/><rect class="k-paper ol" x="232" y="100" width="46" height="20" rx="6"/>
       <path class="ln soft" d="M240 40h30M240 110h30"/>`,
    // Q3-⑥ 함께 사는 동물
    profile_pets: () => wash() + ground +
      `<g class="an-tail"><path class="ln thick amberln" d="M112 118q-26-4-22-32"/></g>
       <path class="k-amber ol" d="M104 130c-4-30 6-48 26-48s30 18 26 48z"/><path class="k-amber ol" d="M112 62l4-20 14 12h0l14-12 4 20a19 19 0 0 1-36 0z"/>
       <circle class="k-ink" cx="123" cy="62" r="2"/><circle class="k-ink" cx="137" cy="62" r="2"/><path class="ln thin" d="M127 69l3 2 3-2M108 66h-12M110 71l-11 4M152 66h12M150 71l11 4"/>
       <path class="k-body ol" d="M190 130c-5-28 5-44 26-44s31 16 26 44z"/><circle class="k-body ol" cx="216" cy="66" r="21"/>
       <path class="k-slate ol" d="M197 54c-10 2-13 16-9 26 6-2 11-12 9-26zM235 54c10 2 13 16 9 26-6-2-11-12-9-26z"/>
       <circle class="k-ink" cx="209" cy="64" r="2"/><circle class="k-ink" cx="223" cy="64" r="2"/><ellipse class="k-ink" cx="216" cy="72" rx="3.4" ry="2.4"/><path class="ln thin" d="M216 74v4q-4 4-7 1M216 78q4 4 7 1"/>
       <g class="an-tail r"><path class="ln thick" d="M240 120q22-6 20-28"/></g>` +
      `<g class="k-leaf-d an-step" style="--d:.6s"><ellipse cx="282" cy="52" rx="5" ry="4"/><circle cx="275" cy="45" r="2"/><circle cx="281" cy="42" r="2"/><circle cx="287" cy="44" r="2"/></g>
       <g class="k-leaf-d an-step" style="--d:1s"><ellipse cx="48" cy="72" rx="5" ry="4"/><circle cx="41" cy="65" r="2"/><circle cx="47" cy="62" r="2"/><circle cx="53" cy="64" r="2"/></g>`,
    // Q4 챕터: 시기 패턴
    ch_pattern: () => wash() + ground +
      `<rect class="k-paper ol" x="92" y="36" width="104" height="90" rx="9"/><path class="k-red" d="M92 45a9 9 0 0 1 9-9h86a9 9 0 0 1 9 9v13H92z"/><path class="ln" d="M92 58h104M116 28v14M172 28v14"/>
       ${[0, 1, 2].map(r => [0, 1, 2, 3].map(c => `<rect class="${(r * 4 + c) % 5 === 1 ? 'k-amber an-blinkbox' : 'k-wash'}" style="--d:${(r + c) * .2}s" x="${103 + c * 21}" y="${67 + r * 18}" width="14" height="12" rx="3"/>`).join('')).join('')}
       <circle class="k-paper ol" cx="236" cy="82" r="28"/><g class="an-spin" style="transform-box:view-box;transform-origin:236px 82px"><path class="ln thick" d="M236 82V62"/></g><g class="an-spin slow" style="transform-box:view-box;transform-origin:236px 82px"><path class="ln leaf thick" d="M236 82h14"/></g><circle class="k-ink" cx="236" cy="82" r="3"/>` + sprout(22, 60, .85, 'right'),
    // Q4 챕터: 계절
    ch_season: () => wash('k-wash2') + ground +
      `<g class="an-spin slow"><circle class="k-paper ol" cx="160" cy="76" r="50"/><path class="ln soft" d="M160 26v100M110 76h100"/>
        <g transform="translate(136 52)"><circle class="k-blush" r="9"/><circle class="k-amber" r="3.5"/></g>
        <g transform="translate(184 52)"><circle class="k-amber ol" r="8"/><path class="ln amberln" d="M0-13v-4M0 13v4M-13 0h-4M13 0h4"/></g>
        <path class="k-red ol" transform="translate(184 100)" d="M0-11c8 2 10 10 6 16-3 4-9 4-12 0-4-6-2-14 6-16z"/>
        <path class="ln sky thick" transform="translate(136 100)" d="M0-10v20M-9-5l18 10M-9 5l18-10"/></g>
       <path class="k-leaf-d ol" d="M160 14l8 12h-16z"/>` + sprout(24, 60, .85, 'right') + specks([[264, 46, 3], [284, 74, 2.4], [262, 100, 2.6]]),
    // Q4 챕터: 꽃가루
    ch_pollen: () => wash() + ground +
      `<path class="k-body ol" d="M96 130V84h12v46z"/><path class="ln thin" d="M98 96h5M101 108h5M98 120h4"/>
       <path class="k-leaf ol an-sway" d="M102 22c26 0 42 18 42 38s-16 30-42 30-42-10-42-30 16-38 42-38z"/>
       <path class="ln amberln thick" d="M84 70v12M102 74v14M120 70v12"/>` +
      specks([[162, 50, 3.2], [184, 38, 2.6], [176, 74, 3], [206, 58, 2.4], [198, 92, 3], [226, 76, 2.6], [150, 96, 2.4]]) +
      `<path class="ln dash" d="M150 62q40-18 86 8"/>` + sprout(232, 60, .9, 'left') +
      `<path class="ln sky an-puff" d="M226 104h-12M228 112h-10"/>`,
    // Q4 챕터: 실내 환경
    ch_indoor: () => wash('k-wash2') + ground +
      `<path class="k-paper ol" d="M58 130V62l52-36 52 36v68z"/><path class="k-red ol" d="M50 66l60-42 60 42-7 9-53-37-53 37z"/>
       <rect class="k-sky ol" x="96" y="72" width="28" height="24" rx="3"/><path class="ln" d="M110 72v24M96 84h28"/>
       <path class="k-slate ol" d="M186 130V96h96v34z"/><rect class="k-paper ol" x="190" y="82" width="34" height="18" rx="8"/><path class="k-wash ol" d="M224 100h58v14h-96v-6c0-4 3-8 8-8z"/>
       ${[[206, 64, 0], [240, 54, .5], [268, 70, 1], [232, 76, 1.4]].map(([x, y, d]) => `<g class="an-float" style="--d:${d}s"><ellipse class="k-amber ol" cx="${x}" cy="${y}" rx="6" ry="4.5"/><path class="ln thin" d="M${x - 5} ${y - 3}l-3-3M${x + 5} ${y - 3}l3-3M${x - 5} ${y + 3}l-3 3M${x + 5} ${y + 3}l3 3"/></g>`).join('')}
       <path class="ln soft an-puff" d="M176 52h10M172 60h14"/>`,
    // Q4 챕터: 동물
    ch_animal: () => wash() + ground +
      `<g class="an-tail"><path class="ln thick amberln" d="M118 118q-28-4-22-36"/></g>
       <path class="k-amber ol" d="M110 130c-4-32 8-52 30-52s34 20 30 52z"/><path class="k-amber ol" d="M120 58l4-22 16 13 16-13 4 22a21 21 0 0 1-40 0z"/>
       <circle class="k-ink" cx="132" cy="58" r="2.2"/><circle class="k-ink" cx="148" cy="58" r="2.2"/><path class="ln thin" d="M136 66l4 2 4-2M116 62h-13M118 68l-12 4M164 62h13M162 68l12 4"/>` +
      [[204, 108, 0], [228, 90, .3], [250, 104, .6], [272, 84, .9]].map(([x, y, d]) => `<g class="k-leaf-d an-step" style="--d:${d}s"><ellipse cx="${x}" cy="${y}" rx="6" ry="5"/><circle cx="${x - 7}" cy="${y - 8}" r="2.3"/><circle cx="${x}" cy="${y - 11}" r="2.3"/><circle cx="${x + 7}" cy="${y - 8}" r="2.3"/></g>`).join('') +
      sprout(22, 60, .85, 'right'),
    // Q4 챕터: 음식·교차반응
    ch_food: () => wash('k-wash2') + ground +
      `<ellipse class="k-paper ol" cx="160" cy="104" rx="76" ry="22"/><ellipse class="ln soft" cx="160" cy="102" rx="56" ry="14"/>
       <g class="an-bob"><path class="k-red ol" d="M132 60c-14-6-28 4-26 22 2 16 14 24 26 18 12 6 24-2 26-18 2-18-12-28-26-22z"/><path class="ln thick" d="M132 60q0-10 7-15"/><path class="k-leaf ol" d="M139 48c6-8 15-8 20-4-5 6-13 9-20 4z"/></g>
       <g class="an-bob" style="--d:.6s"><path class="k-amber ol" d="M190 64c8-6 20-2 22 8 1 6-3 9-3 14 0 8-8 14-17 11s-12-12-8-19c2-5 2-10 6-14z"/><path class="ln thin" d="M193 76q8 2 14-2M191 88q8 2 14-3"/></g>
       <path class="ln thick" d="M262 44v70M254 44v16a8 8 0 0 0 16 0V44"/><path class="ln dash" d="M160 62q14-16 30-6"/>` + sprout(22, 60, .85, 'right'),
    // Q4 챕터: 벌독 — 꽃밭 위를 나는 벌과 쏘인 자리
    ch_venom: () => wash() + ground +
      `<path class="ln thin" d="M70 130V96M112 130v-26M252 130V100"/>
       <g transform="translate(70 90)"><circle class="k-blush ol" r="9"/><circle class="k-amber" r="3.5"/></g>
       <g transform="translate(112 98)"><circle class="k-paper ol" r="8"/><circle class="k-amber" r="3"/></g>
       <g transform="translate(252 94)"><circle class="k-blush ol" r="8"/><circle class="k-amber" r="3"/></g>
       <path class="ln dash" d="M84 70q40-44 86-14"/>
       <g class="an-bob"><ellipse class="k-glass ol" cx="176" cy="44" rx="15" ry="8" transform="rotate(-24 176 44)"/><ellipse class="k-glass ol" cx="198" cy="42" rx="15" ry="8" transform="rotate(20 198 42)"/>
         <ellipse class="k-amber ol" cx="188" cy="64" rx="22" ry="14"/><path class="ln thick" d="M180 51v26M190 50v28M200 53v22"/>
         <circle class="k-ink" cx="170" cy="61" r="2"/><path class="ln thin" d="M170 52q-6-8-12-6"/><path class="ln thick" d="M210 64l10 3"/></g>
       <g class="an-pulse"><circle class="ln red" cx="232" cy="68" r="7"/><circle class="k-red" cx="232" cy="68" r="2.4"/></g>` + sprout(24, 60, .85, 'right'),
    // Q4 챕터: 그 밖의 단서
    ch_generic: () => wash() + ground + foot(140, 116, 64, .2) + foot(172, 106, 70, .5) + foot(206, 114, 60, .8) + foot(240, 104, 72, 1.1) +
      lens(196, 62, 26, 'an-sweep') + sprout(30, 56, 1, 'right'),
    // Q4 단서 정리: 증거 보드
    summary: () => wash('k-wash2') + ground +
      `<rect class="k-body ol" x="64" y="20" width="192" height="106" rx="8"/>
       ${[[80, 34, -5], [146, 30, 3], [208, 36, -3], [112, 80, 4], [180, 78, -4]].map(([x, y, r], i) => `<g class="an-rise" style="--d:${i * .15}s"><g transform="rotate(${r} ${x + 17} ${y + 15})"><rect class="k-paper ol" x="${x}" y="${y}" width="34" height="30" rx="4"/><path class="ln soft" d="M${x + 6} ${y + 14}h22M${x + 6} ${y + 21}h14"/><circle class="k-red" cx="${x + 17}" cy="${y + 4}" r="3"/></g></g>`).join('')}
       <path class="ln red an-draw" pathLength="1" d="M97 38l66-4 62 6-31 42-68 2z"/>`,
    // Q4 판정 중: 도장 찍기
    judge: () => wash() + ground +
      `<g transform="rotate(-6 160 96)"><rect class="k-paper ol" x="104" y="62" width="112" height="66" rx="8"/><rect class="k-wash" x="114" y="72" width="34" height="46" rx="5"/><path class="ln soft" d="M158 80h46M158 92h34"/>
        <rect class="ln red an-stampmark" x="158" y="100" width="46" height="18" rx="4"/></g>
       <g class="an-stamp"><rect class="k-leaf-d ol" x="150" y="12" width="22" height="30" rx="8"/><path class="k-leaf-d ol" d="M132 42h58a6 6 0 0 1 6 6v8h-70v-8a6 6 0 0 1 6-6z"/><rect class="k-red ol" x="124" y="56" width="74" height="7" rx="2"/></g>` + sprout(24, 60, .85, 'right'),
    // Q5 도감 완성: 펼친 도감
    results: () => wash('k-wash2') + ground +
      `<path class="k-leaf-d ol" d="M52 122V58q54-18 108 0 54-18 108 0v64q-54-16-108 2-54-18-108-2z"/>
       <path class="k-paper ol" d="M60 114V54q50-14 100 2v60q-50-14-100-2zM260 114V54q-50-14-100 2v60q50-14 100-2z"/>
       <path class="ln soft" d="M74 68q36-8 72 0M74 80q36-8 72 0M74 92q30-7 60-2M174 68q36-8 72 0M174 80q36-8 72 0"/>` +
      miniCard(118, 8, -12, '', .2) + miniCard(160, 0, 0, '', .45) + miniCard(202, 8, 12, '', .7),
  };

  const ICONS = {
    trace: '<path class="k-wash" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><g class="k-leaf-d"><ellipse cx="24" cy="29" rx="7" ry="9"/><circle cx="15" cy="16" r="2.6"/><circle cx="21" cy="12" r="2.6"/><circle cx="28" cy="12" r="2.6"/><circle cx="34" cy="16" r="2.6"/></g>',
    card: '<path class="k-wash2" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><rect class="k-paper ol" x="14" y="8" width="20" height="30" rx="4" transform="rotate(-8 24 23)"/><rect class="k-amber" x="17" y="12" width="13" height="10" rx="2" transform="rotate(-8 24 23)"/><path class="ln soft" d="M18 28l12-2M19 33l8-1"/>',
    clue: '<path class="k-wash" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><circle class="k-glass ol" cx="21" cy="21" r="9"/><path class="ln thick" d="M28 28l9 9"/><path class="ln glint" d="M16 20a5 5 0 0 1 4-4"/>',
    stamp: '<path class="k-wash2" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><rect class="k-leaf-d ol" x="20" y="6" width="8" height="14" rx="3"/><path class="k-leaf-d ol" d="M13 20h22a3 3 0 0 1 3 3v5H10v-5a3 3 0 0 1 3-3z"/><rect class="k-red ol" x="9" y="28" width="30" height="5" rx="1.5"/><path class="ln red" d="M13 39h22"/>',
    book: '<path class="k-wash" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><path class="k-leaf-d ol" d="M8 36V14q8-4 16 0 8-4 16 0v22q-8-3-16 1-8-4-16-1z"/><path class="k-paper ol" d="M11 33V14q6-3 13 1v19q-7-3-13-1zM37 33V14q-6-3-13 1v19q7-3 13-1z"/>',
    report: '<path class="k-wash2" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><path class="k-paper ol" d="M14 8h14l8 8v24H14z"/><path class="ln" d="M28 8v8h8"/><path class="ln soft" d="M18 22h14M18 28h14M18 34h9"/>',
    deck: '<path class="k-wash" d="M4 24a20 20 0 1 1 40 0 20 20 0 0 1-40 0z"/><rect class="k-paper ol" x="9" y="14" width="18" height="24" rx="4"/><rect class="k-amber ol" x="15" y="11" width="18" height="24" rx="4"/><rect class="k-paper ol" x="21" y="8" width="18" height="24" rx="4"/><path class="ln soft" d="M25 16h10M25 22h7"/>',
  };

  // ---- 도감 카드 그림(카테고리 타입색 --tc 로 칠한다) ----
  const pBack = (extra) => `<rect class="t-soft" width="200" height="130"/><path class="t-mid" d="M0 104q50-18 100-6t100-10v42H0z"/>${extra || ''}`;
  const pollen = (pts) => pts.map(([x, y, r], i) => `<g class="an-drift" style="--d:${(i * .41) % 2.6}s"><circle class="k-amber ol thin" cx="${x}" cy="${y}" r="${r}"/><circle class="k-paper" cx="${x - r * .3}" cy="${y - r * .3}" r="${r * .28}"/></g>`).join('');
  const PLATES = {
    mite: () => pBack(`<path class="k-paper ol" d="M18 104q-6-30 22-34h120q28 4 22 34z"/><path class="ln soft" d="M40 84h120M34 94h132"/>`) +
      `<g class="an-bob"><path class="ln thick" d="M78 60l-18-12M74 72l-22-2M78 84l-18 12M122 60l18-12M126 72l22-2M122 84l18 12M88 52l-8-14M112 52l8-14"/>
        <ellipse class="t-fill ol" cx="100" cy="72" rx="27" ry="23"/><path class="ln onfill thin" d="M84 64q16-8 32 0M82 76q18-8 36 0"/>
        <ellipse class="t-fill ol" cx="100" cy="48" rx="10" ry="7"/><circle class="k-paper" cx="96" cy="47" r="1.8"/><circle class="k-paper" cx="104" cy="47" r="1.8"/></g>` +
      [[32, 34, 2], [58, 22, 1.6], [150, 26, 2], [172, 44, 1.6], [24, 58, 1.4]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .5}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
    animal_cat: () => pBack() +
      `<g class="an-tail"><path class="ln thick tln" d="M74 104q-30-2-24-40"/></g>
       <path class="t-fill ol" d="M66 118c-6-36 8-58 34-58s40 22 34 58z"/><path class="t-fill ol" d="M76 42l5-26 19 15 19-15 5 26a24 24 0 0 1-48 0z"/>
       <path class="k-paper" d="M84 26l1 9 7-5zM116 26l-1 9-7-5z"/><circle class="k-ink" cx="91" cy="42" r="2.6"/><circle class="k-ink" cx="109" cy="42" r="2.6"/>
       <path class="ln thin" d="M95 51l5 3 5-3M72 46H56M74 53l-15 5M128 46h16M126 53l15 5"/>` +
      [[160, 34, 0], [172, 62, .5], [30, 30, 1]].map(([x, y, d]) => `<g class="t-fill an-step" style="--d:${d}s"><ellipse cx="${x}" cy="${y}" rx="6" ry="5"/><circle cx="${x - 7}" cy="${y - 8}" r="2.2"/><circle cx="${x}" cy="${y - 11}" r="2.2"/><circle cx="${x + 7}" cy="${y - 8}" r="2.2"/></g>`).join(''),
    animal_dog: () => pBack() +
      `<g class="an-tail r"><path class="ln thick tln" d="M132 104q26-6 24-34"/></g>
       <path class="t-fill ol" d="M66 118c-6-34 8-54 34-54s40 20 34 54z"/><circle class="t-fill ol" cx="100" cy="44" r="26"/>
       <path class="k-ink ol" d="M77 28c-13 2-17 21-12 34 8-3 14-16 12-34zM123 28c13 2 17 21 12 34-8-3-14-16-12-34z"/>
       <ellipse class="k-paper" cx="100" cy="54" rx="12" ry="9"/><circle class="k-ink" cx="91" cy="41" r="2.6"/><circle class="k-ink" cx="109" cy="41" r="2.6"/><ellipse class="k-ink" cx="100" cy="50" rx="4" ry="3"/>
       <path class="ln thin" d="M100 53v4q-5 5-9 1M100 57q5 5 9 1"/>` +
      [[164, 36, 0], [30, 34, .6]].map(([x, y, d]) => `<g class="t-fill an-step" style="--d:${d}s"><ellipse cx="${x}" cy="${y}" rx="6" ry="5"/><circle cx="${x - 7}" cy="${y - 8}" r="2.2"/><circle cx="${x}" cy="${y - 11}" r="2.2"/><circle cx="${x + 7}" cy="${y - 8}" r="2.2"/></g>`).join(''),
    animal: () => pBack() +
      [[100, 74, 1.9, 0], [44, 40, 1, .4], [158, 38, 1, .8], [160, 96, .8, 1.2], [40, 96, .8, 1.6]].map(([x, y, s, d]) => `<g class="an-step" style="--d:${d}s"><g transform="translate(${x} ${y}) scale(${s})"><ellipse class="t-fill ol" cx="0" cy="4" rx="11" ry="9"/><circle class="t-fill ol" cx="-13" cy="-9" r="4.4"/><circle class="t-fill ol" cx="-5" cy="-15" r="4.4"/><circle class="t-fill ol" cx="5" cy="-15" r="4.4"/><circle class="t-fill ol" cx="13" cy="-9" r="4.4"/></g></g>`).join(''),
    pollen_tree: () => pBack() +
      `<path class="k-paper ol" d="M92 118V70h16v48z"/><path class="ln thin" d="M95 82h6M100 94h6M95 106h5"/>
       <g class="an-sway"><path class="t-fill ol" d="M100 10c32 0 50 20 50 42s-18 34-50 34-50-12-50-34 18-42 50-42z"/><path class="ln onfill thin" d="M72 44q10-12 22-8M104 30q14-4 22 8M82 66q14 6 30 0"/>
        <path class="ln amberln thick" d="M72 76v14M88 82v16M112 82v16M128 76v14"/></g>` +
      pollen([[160, 30, 4], [176, 54, 3.2], [164, 78, 3.6], [26, 34, 3.6], [36, 64, 3], [182, 96, 3]]),
    pollen_grass: () => pBack() +
      [[52, -8, 0], [78, 4, .4], [100, -2, .8], [124, 6, .2], [150, -6, .6]].map(([x, r, d]) => `<g class="an-sway" style="--d:${d}s"><g transform="rotate(${r} ${x} 120)"><path class="ln tln thick" d="M${x} 120V46"/><path class="ln tln" d="M${x} 96q-16-6-20-22M${x} 86q16-6 20-22"/><ellipse class="t-fill ol" cx="${x}" cy="34" rx="6.5" ry="17"/><path class="ln onfill thin" d="M${x - 4} 26l8 4M${x - 4} 34l8 4M${x - 4} 42l8 4"/></g></g>`).join('') +
      pollen([[28, 28, 3.4], [172, 26, 3.6], [180, 62, 3], [20, 66, 3]]),
    pollen_weed: () => pBack() +
      `<g class="an-sway"><path class="ln tln thick" d="M100 120V28"/>
        ${[[96, 1, 34], [96, -1, 34], [74, 1, 30], [74, -1, 30], [54, 1, 24], [54, -1, 24]].map(([y, s, w]) => `<path class="t-fill ol" d="M100 ${y}q${s * w * .3} ${-w * .62} ${s * w} ${-w * .5}q${-s * w * .18} ${w * .2} ${-s * w * .06} ${w * .3}q${-s * w * .4} ${w * .34} ${-s * w * .94} ${w * .2}z"/><path class="ln onfill thin" d="M100 ${y}q${s * w * .4} ${-w * .3} ${s * w * .8} ${-w * .36}"/>`).join('')}
        <path class="k-amber ol" d="M94 30q-2-14 6-22 8 8 6 22z"/><path class="ln thin" d="M96 20h8M95 26h10"/></g>` +
      pollen([[30, 30, 3.6], [44, 62, 3], [164, 28, 3.4], [178, 58, 3], [156, 84, 2.8]]),
    mold: () => `<rect class="t-soft" width="200" height="130"/><path class="ln soft" d="M0 44h200M0 88h200M50 0v44M150 0v44M100 44v44M30 88v42M130 88v42"/><path class="t-mid" d="M0 130V78q20 14 36 30t60 22z"/>` +
      [[84, 62, 24, 0], [128, 86, 17, .5], [56, 96, 13, .9], [136, 44, 11, 1.3]].map(([x, y, r, d]) => `<g class="an-pulse" style="--d:${d}s"><circle class="ln tln dotted" cx="${x}" cy="${y}" r="${r + 5}"/><circle class="t-fill ol" cx="${x}" cy="${y}" r="${r}"/><circle class="k-paper" cx="${x - r * .3}" cy="${y - r * .3}" r="${r * .22}"/><circle class="k-ink" cx="${x + r * .3}" cy="${y + r * .2}" r="${r * .12}"/></g>`).join('') +
      [[168, 30, 2], [176, 70, 1.6], [28, 34, 2], [100, 20, 1.6], [160, 108, 1.8]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .45}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
    insect: () => pBack() +
      `<g class="an-scuttle"><path class="ln thick" d="M82 52l-24-12-8-14M80 70H52l-12 8M84 88l-22 12-6 14M118 52l24-12 8-14M120 70h28l12 8M116 88l22 12 6 14"/>
        <g class="an-feel"><path class="ln" d="M94 30q-10-18-30-18M106 30q10-18 30-18"/></g>
        <ellipse class="t-fill ol" cx="100" cy="74" rx="21" ry="32"/><path class="ln onfill thin" d="M100 46v58M84 62q16-8 32 0"/>
        <path class="t-fill ol" d="M86 44a14 12 0 0 1 28 0z"/><circle class="k-paper" cx="94" cy="38" r="1.8"/><circle class="k-paper" cx="106" cy="38" r="1.8"/></g>`,
    venom: () => pBack() +
      `<g class="an-bob"><ellipse class="k-glass ol" cx="84" cy="40" rx="20" ry="11" transform="rotate(-28 84 40)"/><ellipse class="k-glass ol" cx="116" cy="40" rx="20" ry="11" transform="rotate(28 116 40)"/>
        <g class="an-feel"><path class="ln" d="M92 34q-6-16-20-16M108 34q6-16 20-16"/></g>
        <ellipse class="t-fill ol" cx="100" cy="74" rx="22" ry="30"/><path class="ln onfill" d="M80 64q20-8 40 0M79 76q21-8 42 0M82 88q18-7 36 0"/>
        <path class="t-fill ol" d="M86 46a14 12 0 0 1 28 0z"/><circle class="k-paper" cx="94" cy="40" r="1.8"/><circle class="k-paper" cx="106" cy="40" r="1.8"/>
        <path class="ln thick" d="M100 104v12"/></g>
       <path class="ln dash" d="M30 96q18-30 44-22"/>` +
      [[36, 34, 2.2], [166, 30, 2], [172, 84, 2.4]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .5}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
    food: () => pBack() +
      `<ellipse class="k-paper ol" cx="100" cy="80" rx="74" ry="34"/><ellipse class="ln soft" cx="100" cy="78" rx="54" ry="22"/>
       <g class="an-bob"><path class="t-fill ol" d="M78 50c-13-6-26 4-24 20 2 15 13 22 24 17 11 5 22-2 24-17 2-16-11-26-24-20z"/><path class="ln thick" d="M78 50q0-9 6-13"/><path class="k-leaf ol" d="M84 40c5-7 13-7 18-4-4 6-12 8-18 4z"/></g>
       <g class="an-bob" style="--d:.5s"><path class="k-amber ol" d="M122 56c7-5 18-2 20 7 1 5-3 8-3 13 0 7-7 13-16 10s-11-11-7-17c2-5 2-9 6-13z"/><path class="ln thin" d="M125 67q7 2 12-2M123 78q7 2 13-3"/></g>
       <path class="ln thick tln" d="M112 96q14 8 26-2 6-6 2-12"/><path class="ln thick" d="M180 30v70M173 30v15a7 7 0 0 0 14 0V30"/>`,
    // 라텍스 — 고무장갑과 풍선
    latex: () => pBack() +
      `<g class="an-bob"><path class="t-fill ol" d="M76 118V82L62 58a6.5 6.5 0 0 1 11-7l9 14V30a6.5 6.5 0 0 1 13 0v26h2V22a6.5 6.5 0 0 1 13 0v34h2V28a6.5 6.5 0 0 1 13 0v32h2V42a6.5 6.5 0 0 1 13 0v40c0 14-4 24-10 36z"/>
        <path class="k-paper ol" d="M72 104h62v14H72z"/><path class="ln onfill thin" d="M95 62v14M110 62v16M125 64v12"/></g>
       <g class="an-float" style="--d:.6s"><ellipse class="t-mid ol" cx="170" cy="40" rx="15" ry="19"/><path class="t-mid ol" d="M166 59h8l-4 6z"/><path class="ln thin" d="M170 65q-6 12 2 22t-2 22"/><path class="ln glint" d="M162 34a8 9 0 0 1 6-7"/></g>` +
      [[30, 36, 2.2], [40, 78, 2], [22, 100, 1.8]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .5}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
    // 약물 — 약병과 캡슐·알약
    drug: () => pBack() +
      `<rect class="k-paper ol" x="34" y="50" width="50" height="66" rx="9"/><rect class="t-fill ol" x="30" y="34" width="58" height="18" rx="6"/><path class="ln onfill thin" d="M40 40v6M50 40v6M60 40v6M70 40v6M80 40v6"/>
       <rect class="t-soft ol thin" x="42" y="68" width="34" height="30" rx="4"/><path class="ln tln thick" d="M59 75v16M51 83h16"/>
       <g class="an-bob"><g transform="rotate(-28 136 58)"><rect class="t-fill ol" x="106" y="45" width="60" height="26" rx="13"/><path class="k-paper" d="M136 46.5h17a11.5 11.5 0 0 1 0 23h-17z"/><path class="ln" d="M136 45v26"/><path class="ln glint" d="M116 52h12"/></g></g>
       <g class="an-float" style="--d:.7s"><circle class="k-paper ol" cx="120" cy="100" r="13"/><path class="ln soft" d="M110 100h20"/></g>
       <g class="an-float" style="--d:1.2s"><circle class="t-mid ol" cx="164" cy="98" r="10"/><path class="ln soft" d="M157 98h14"/></g>`,
    // 검사 대조 — 검사 스트립의 양성(+)·음성(−) 대조선. 알러젠이 아니라 검사가 제대로 됐는지 보는 기준선이다.
    control: () => pBack() +
      `<g class="an-bob"><rect class="k-paper ol" x="26" y="38" width="148" height="56" rx="12"/>
        <rect class="t-soft ol thin" x="38" y="50" width="56" height="32" rx="8"/><rect class="t-soft ol thin" x="106" y="50" width="56" height="32" rx="8"/>
        <circle class="t-fill ol" cx="66" cy="66" r="11"/><path class="ln onfill thick" d="M66 60v12M60 66h12"/>
        <circle class="k-paper ol" cx="134" cy="66" r="11"/><path class="ln thick" d="M128 66h12"/></g>
       <path class="ln soft" d="M38 106h56M106 106h56"/><path class="ln tln thick an-tick" pathLength="1" d="M58 112l5 6 9-11"/><path class="ln tln thick an-tick" style="--d:.4s" pathLength="1" d="M126 112l5 6 9-11"/>
       <path class="ln dash" d="M100 20v14"/>` +
      [[34, 22, 2], [170, 24, 2.2], [182, 110, 1.8]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .55}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
    other: () => pBack() +
      `<g class="an-bob"><rect class="k-slate ol" x="80" y="18" width="40" height="13" rx="4"/><path class="k-glass ol" d="M76 31h48v62a12 12 0 0 1-12 12H88a12 12 0 0 1-12-12z"/>
        <path class="t-mid" d="M79 70q21-10 42 0v23a9 9 0 0 1-9 9H88a9 9 0 0 1-9-9z"/><text class="k-txt big" x="100" y="72" text-anchor="middle">?</text></g>
       <path class="k-paper ol" d="M134 44h30l8 8-8 8h-30z"/><path class="ln soft" d="M140 52h18"/>` +
      [[36, 40, 2.4], [48, 76, 2], [160, 92, 2.2]].map(([x, y, r], i) => `<circle class="t-fill an-drift" style="--d:${i * .6}s" cx="${x}" cy="${y}" r="${r}"/>`).join(''),
  };
  const CH_SCENE = { pattern: 'ch_pattern', season: 'ch_season', pollen: 'ch_pollen', indoor: 'ch_indoor', animal: 'ch_animal', venom: 'ch_venom', food: 'ch_food' };

  function scene(id) { const f = SCENES[id] || SCENES.ch_generic; return wrap(`scene scene-${SCENES[id] ? id : 'ch_generic'}`, '0 0 320 150', f()); }
  function chapterScene(sectionId) { return scene(CH_SCENE[sectionId] || 'ch_generic'); }
  function icon(id) { return wrap('aicon', '0 0 48 48', ICONS[id] || ICONS.clue); }
  // 같은 카테고리라도 이름으로 고를 수 있는 변형(고양이·개)
  function plateId(category, name) {
    if (category === 'animal') {
      const s = String(name || '').toLowerCase();
      if (/(cat|feline|고양이|猫)/.test(s)) return 'animal_cat';
      if (/(dog|canine|강아지|개 |개털|개비듬|개 비듬|犬|狗)/.test(s + ' ')) return 'animal_dog';
      return 'animal';
    }
    return PLATES[category] ? category : 'other';
  }
  function plate(category, name) { const id = plateId(category, name); return wrap(`plate plate-${id}`, '0 0 200 130', PLATES[id](), 'xMidYMid slice'); }
  // 카드 뒷면·로고에 쓰는 나침반 문장
  function emblem() {
    return wrap('emblem', '0 0 48 48', '<circle class="ln" cx="24" cy="24" r="19"/><circle class="ln soft" cx="24" cy="24" r="13"/><path class="k-fillcur" d="M24 8l5 16h-10z"/><path class="ln" d="M24 40l-5-16h10z"/><circle class="k-fillcur" cx="24" cy="24" r="2.2"/>');
  }

  return { scene, chapterScene, icon, plate, plateId, emblem, SCENE_IDS: Object.keys(SCENES), PLATE_IDS: Object.keys(PLATES), ICON_IDS: Object.keys(ICONS) };
});
