# 알러젠 탐험 퀘스트 — 게임화 UI 재설계 스펙

- 작성일: 2026-09-08
- 대상 브랜치: `claude/allergy-test-report-x4lgqh`
- 범위: 웹앱 5단계 워크플로우 + 결과 화면 + 카드뉴스 (임상 리포트 HTML/PDF는 제외)
- 상태: 사용자 승인 완료 (2026-09-08)

## 1. 목표와 비목표

### 목표
- 전형적인 SaaS 스텝퍼 UI를 **탐험 퀘스트 + 알러젠 도감** 메타포로 재설계해 동적이고 재미있게 만든다.
- 플랫폼의 핵심 철학(검사 양성 = 감작 흔적, 임상 알레르기 = 증상으로 확정)을 게임 서사로 그대로 옮긴다.
- 기존 API 계약·판정 로직·회귀 테스트(`test_relevance_engine.py` 24종)를 변경하지 않는다.

### 비목표
- 임상 리포트(`services/report_design.py`, HTML/PDF)는 손대지 않는다. 진지한 문서 톤을 유지한다.
- 프론트 프레임워크 도입, 빌드 도구 도입은 하지 않는다(바닐라 JS 유지).
- localStorage 영속화(배지 컬렉션 누적 등)는 하지 않는다. 상태는 세션 메모리만 사용한다.
- 사운드는 넣지 않는다.

## 2. 서사(메타포) 매핑

| 도메인 개념 | 게임 표현 | 문구 |
|---|---|---|
| 5단계 워크플로우 | 퀘스트 트레일(지도형 노드 5개) | 흔적 수집 → 증거 확인 → 탐험가 프로필 → 진범 감별 → 도감 완성 |
| 검사 양성 알러젠 | 발견된 흔적 → 도감 카드(판정 미확정 `?`) | "발견!" |
| 감작 강도(weak/moderate/strong) | 별 ★ 1~3개 | |
| 문진 섹션 | 퀘스트 챕터 | 챕터별 완료 링 |
| reveal 게이트로 열린 문항 | 새 단서 | "새 단서 발견" |
| clinically_relevant | 도장 **진범 확정** (주홍) | |
| sensitized_only | 도장 **무혐의 · 감작만** (슬레이트) + "감작은 남아 있어 추적 필요" 부연 | |
| indeterminate | 도장 **관찰 대상** (호박) | |
| 진행 완료 행동 | XP, 레벨 칭호 | 새싹 탐험가 → 숙련 탐험가 → 알러젠 마스터 |

## 3. 비주얼 시스템 (`web/styles.css` 전면 재작성)

### 토큰
- 배경: 크림 종이(`#FBF7EE`), 상승면 흰색, 잉크 텍스트(`#1F2A24`).
- 브랜드: 숲 녹색(`#2F8F5B`), 액센트: 호박(`#F2A33A`).
- 판정: relevant 주홍(`#E4572E`), sensitized 슬레이트(`#6B7A8F`), indeterminate 호박(`#D9860A`), ok 녹색.
- 다크 모드: 깊은 녹청 배경(`#0F1A16`) 계열로 동일 토큰 재정의. 기존 `data-theme` 토글·OS 선호 규칙 유지.
- 형태: 카드 라운드 유지(12~24px), 도장은 살짝 회전(-4°~+3°)한 테두리 스탬프 스타일.

### 타이포
- 디스플레이(제목·퀘스트명·도장): **Do Hyeon** (Google Fonts, `index.html`에 link 추가).
- 본문: Pretendard 유지.

### 아이코노그래피
- 카테고리 9종(mite, animal, pollen_tree, pollen_grass, pollen_weed, mold, insect, food, other)의 **인라인 SVG 스탬프**를 `web/game.js`의 `STAMPS` 맵으로 정의. 카드뉴스는 같은 SVG 문자열을 Python 쪽 `_CATEGORY_STAMP_SVG` 로 복제(동일 소스 주석 표기). 외부 이미지 없음. 렌더 실패 시 기존 이모지 폴백.

### 모션
- CSS keyframes만 사용: 카드 뒤집기(`flip-in`), XP 바 채움(`width` transition), 배지 팝(`pop`), 스파클(의사요소 3~5개), 트레일 현재 노드 맥동(`pulse`).
- `@media (prefers-reduced-motion: reduce)` 에서 모든 애니메이션 비활성.

## 4. 게임 레이어 (`web/game.js` 신규)

### 상태
```js
G = { xp: 0, level: 0, discovered: {name → {stars, category, verdict|null}}, badges: Set, severeFlag: false }
```
`S`(app.js 상태)와 분리하되 `S.game = G` 로 참조 연결. 재시작(`처음부터 다시`) 시 `G` 초기화.

### XP 규칙 (완주 행동에만 부여, 답변 내용으로 차등 금지)
| 행동 | XP |
|---|---|
| 단계 완료(각) | +50 |
| OCR 검토 확정 시 양성 알러젠 1종 발견 | +10/종 (최대 +100) |
| 문진 문항 1개 응답(모든 선택지 동일, '잘 모르겠어요' 포함) | +10 |
| 문진 챕터 완료 | +30 |
레벨 임계: 0 / 200 / 500. 칭호는 헤더 HUD에 표시.

### 배지 (결과 화면에서 부여)
- **완주**: 5단계 완료.
- **정직한 탐험가**: '잘 모르겠어요'를 1회 이상 선택.
- **교차반응 헌터**: confirmed 교차반응 음식 1개 이상.
- **OAS 탐지**: OAS 음식 확인.
- **꼼꼼한 검토자**: OCR 표에서 값을 1회 이상 수정.
- **도감 완성**: 모든 양성 알러젠에 판정 부여(not_assessed 0).
배지 문구는 중립적으로, 건강 상태를 칭찬·평가하는 표현 금지.

### 훅 포인트 (`web/app.js` 최소 수정)
- `goto(step)` → `Game.onStepEnter(step)` (단계 완료 XP, 트레일 갱신).
- `renderStepper()` → `Game.renderTrail()` 로 교체.
- Step 1 → 2 전환(OCR 검토 확정, 0-index) → `Game.discover(positiveRows)` 발견 연출 후 다음 단계.
- 문진 답변 핸들러(`[data-single]`, `[data-multi]`) → `Game.onAnswer(qid, prevAnswered)`; reveal 로 새로 열린 블록에 `.clue-new` 클래스 부여.
- `renderQuestionnaire()` → 챕터 링·하단 고정 진행바 렌더 헬퍼 호출.
- `submitClassify()` 성공 → `Game.onResults(S.classify)` 로 판정·배지·severeFlag 계산.
- `renderResults()`/`renderResultTab('allergens')` → 스코어보드 + 도감 카드(앞/뒤 뒤집기) + 판정 필터 칩.

### 화면별 상세
1. **헤더 HUD**: 로고 + XP 바(레벨 칭호, 현재/다음 임계) + 테마 토글. `aria-live="polite"` 로 XP 변화 안내.
2. **퀘스트 트레일**: 5 노드를 곡선 경로(SVG path)로 연결. done=채움, active=맥동, locked=회색. 기존 `maxReached` 클릭 이동 규칙 유지.
3. **Step 0 업로드**: 드롭존을 "흔적 수집 상자"로. 데모 버튼 = "연습 탐험 시작".
4. **Step 1 OCR 검토**: 표 UI 로직 유지, 양성 행에 스탬프 미니 아이콘 + 별. 확정 버튼 = "발견 등록 →". 확정 시 발견 카드가 순차 뒤집히는 오버레이(최대 12장 표시, 초과분은 "+N종") 후 자동 다음 단계.
5. **Step 2 스크리닝**: 폼 로직 유지. 패널 제목 "탐험가 프로필". 완료 시 칩 요약을 프로필 카드로.
6. **Step 3 문진**: 챕터 카드(제목 + 완료 링 `answered/visible`), 문항 카드, 답변 시 체크 애니메이션 + `+10` 플로팅. 하단 고정 진행바(전체 visible 문항 기준). 새로 reveal 된 문항은 `.clue-new` 슬라이드 인 + "새 단서" 라벨 3초.
7. **Step 4 결과**: 스코어보드(3 판정 카운트 트로피 타일) → 배지 행 → 판정 필터 칩 → 도감 카드 그리드(앞: 스탬프·이름·별·도장 / 뒤: 판정 근거·지식베이스·회피 수칙). 탭(리포트/카드뉴스/FHIR)은 유지, 리포트·FHIR 탭 내부는 기존 그대로.

### 의료 안전 가드레일
- `severeFlag`: 문진에서 아나필락시스/전신 중증 답변이 있거나 결과 assessment 에 severity ≥ severe 가 있으면 true. true 면 완료 축하 연출(스파클·"도감 완성!" 헤드라인)을 억제하고 차분한 경고 콜아웃("중증 반응 이력이 있어 의료진 상담이 필요합니다")을 스코어보드 상단에 배치.
- 면책 문구(`.disclaimer`) 유지. 무혐의 도장에는 항상 "감작은 남아 있어 추적 필요" 부연.
- XP·배지는 진행/행동 기반만. 판정 결과(진범 수 등)로 점수·등급을 만들지 않는다.

## 5. 카드뉴스 (`services/cardnews_service.py`)
- 변경 범위: `_wrap()` 의 `<style>` 블록 + 각 `_*_card()` 의 마크업 클래스/래퍼. 데이터 선택 로직·`_collapse()`·`_label()` 은 변경 금지.
- 시각언어: 크림 배경 + 숲 녹색/호박, Do Hyeon(Google Fonts link 인라인) + Pretendard 폴백, 카테고리 SVG 스탬프.
- 카드 구성 유지(표지 → 진범 확정 → 감작만/관찰 → 예방 → 음식 경계 → 치료 → 마무리). 제목 문구를 서사에 맞게 교체:
  - 표지: "OOO님의 알러젠 탐험 리포트"
  - 실제 주의: "진범 확정" 도장
  - 감작만: "무혐의 · 감작만" 도장 + 추적 부연
  - 음식 경계: "경계 목록" + 교차반응 범위 배지(국소/전신/아나필락시스)
- 기존 회귀 테스트가 확인하는 chip 텍스트·중복 제거 동작은 유지.

## 6. 파일 변경 목록
| 파일 | 변경 |
|---|---|
| `web/index.html` | 폰트 link, 헤더 HUD 슬롯, 트레일 nav, 하단 진행바 슬롯, `game.js` 로드 |
| `web/styles.css` | 전면 재작성 |
| `web/game.js` | 신규 |
| `web/app.js` | 훅 포인트·문구·결과 렌더 수정(로직 불변) |
| `services/cardnews_service.py` | 스타일·마크업 재작성 |
| `docs/superpowers/specs/2026-09-08-gamified-quest-ui-design.md` | 본 문서 |

## 7. 검증
- `python3 test_relevance_engine.py` 24종 전량 통과(변경 없음).
- Playwright 스모크(uvicorn 로컬 기동 후): 데모 OCR → 발견 오버레이 등장·자동 진행 → 스크리닝 제출 → 문진 답변 시 XP 증가·챕터 링 갱신·reveal 문항 `.clue-new` → 결과 스코어보드·도감 카드 뒤집기·필터 칩·배지 → 카드뉴스 iframe 내 도장 요소 존재. 모바일(390px)과 다크 모드 스크린샷 각 1회.
- `prefers-reduced-motion` 에뮬레이션에서 애니메이션 없음 확인.
- 마무리: gstack `design-review` 스킬로 시각 일관성·AI slop 패턴 점검.

## 8. 사용 스킬
- 구현: `frontend-design`(디자인 방향·타이포·모션 가이드), `playwright-skill`(스모크).
- QA: `design-review`.
