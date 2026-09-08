#!/usr/bin/env bash
# 알러젠 탐험 퀘스트 UI 스모크 — gstack browse 로 데모 흐름을 끝까지 돌린다.
# 사전: uvicorn server:app --port 8765 실행 중.
set -euo pipefail
B=${B:-$HOME/.claude/skills/gstack/browse/dist/browse}
URL=${URL:-http://127.0.0.1:8765}
OUT=${OUT:-/tmp/quest-smoke}; mkdir -p "$OUT"
fail() { echo "✗ $1"; exit 1; }
ok() { echo "✓ $1"; }
js() { $B js "$1" | tail -1; }

# browse 데몬이 정적 파일을 캐시하므로 최신 app.js/game.js/styles.css 를 강제 재수신 후 재로드
$B goto "$URL/" >/dev/null
js "Promise.all(['/app.js','/game.js','/styles.css'].map(u=>fetch(u,{cache:'reload'}))).then(()=>'refetched')" >/dev/null
$B reload >/dev/null; $B wait '#stepper .tnode.active' >/dev/null
[ "$(js "document.querySelectorAll('#stepper .tnode').length")" = "5" ] || fail "트레일 노드 5개"
[ "$(js "document.querySelector('#hud .hud-title').textContent")" = "Lv.1 새싹 탐험가" ] || fail "HUD 초기 레벨"
ok "Step0 트레일·HUD"
$B screenshot "$OUT/00-upload.png" >/dev/null

$B click '#btnDemo' >/dev/null; $B wait '#next' >/dev/null
[ "$(js "document.querySelector('#hud .hud-xp').textContent.startsWith('50 XP')")" = "true" ] || fail "Step0 완료 XP 50"
ok "Step1 OCR 검토 진입 + XP"
$B screenshot "$OUT/01-review.png" >/dev/null

$B click '#next' >/dev/null; $B wait '#dGo' >/dev/null
[ "$(js "document.querySelectorAll('#overlay .dcard').length > 0")" = "true" ] || fail "발견 오버레이 카드"
ok "발견 오버레이"
$B screenshot "$OUT/02-discover.png" >/dev/null
$B click '#dGo' >/dev/null; $B wait '#pName' >/dev/null
ok "Step2 스크리닝 진입"

$B click '#next' >/dev/null; $B wait '.chapter[data-si="0"]' >/dev/null
[ "$($B is visible '#questBar' | tail -1)" = "true" ] || fail "하단 퀘스트바 표시"
XP0=$(js "parseInt(document.querySelector('#hud .hud-xp').textContent)")
js "document.querySelector('.q-block:not(.hidden) .choice, .q-block:not(.hidden) .chip-opt').click()" >/dev/null
sleep 0.3
XP1=$(js "parseInt(document.querySelector('#hud .hud-xp').textContent)")
[ "$XP1" -gt "$XP0" ] || fail "답변 시 XP 증가 ($XP0 → $XP1)"
[ "$(js "document.querySelectorAll('.q-block.answered').length >= 1")" = "true" ] || fail "answered 표시"
ok "Step3 문진 XP·챕터"
$B screenshot "$OUT/03-quest.png" >/dev/null

# 모든 보이는 단일선택 문항에 첫 선택지로 답해 챕터 완료·결과 진행(reveal 로 열리는 문항 포함, 6회 반복)
js "(function(){ let n=0; for (let k=0;k<6;k++){ document.querySelectorAll('.q-block:not(.hidden) [data-single]').forEach(g=>{ if(!g.querySelector('.choice.sel')) { g.querySelector('.choice').click(); n++; } }); } return n; })()" >/dev/null
$B click '#next' >/dev/null; $B wait '#scoreboard' >/dev/null
[ "$(js "document.querySelectorAll('.dex-card').length > 0")" = "true" ] || fail "도감 카드"
[ "$(js "document.querySelectorAll('.badge-item.earned').length >= 1")" = "true" ] || fail "배지 1개 이상"
[ "$(js "!!document.querySelector('.verdict-stamp')")" = "true" ] || fail "판정 도장"
js "document.querySelector('.dex-card').click()" >/dev/null
[ "$(js "document.querySelector('.dex-card').classList.contains('flip')")" = "true" ] || fail "도감 카드 뒤집기"
ok "Step4 스코어보드·도감·배지"
$B screenshot "$OUT/04-dex.png" >/dev/null

js "document.querySelector('.result-tabs [data-tab=cardnews]').click()" >/dev/null; $B wait '#cn' >/dev/null; sleep 0.8
[ "$(js "document.querySelector('#cn').contentDocument.querySelector('.stamp-verdict') !== null")" = "true" ] || fail "카드뉴스 도장 요소"
ok "카드뉴스 iframe 도장"
$B screenshot "$OUT/05-cardnews.png" >/dev/null

ERRS=$($B console --errors | grep -v "^---\|^(no console errors)" || true)
[ -z "$ERRS" ] || { echo "$ERRS"; fail "콘솔 오류"; }
echo "ALL SMOKE PASSED — screenshots in $OUT"
