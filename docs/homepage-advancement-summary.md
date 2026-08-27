# PACEN 홈페이지 고도화 — 종합 요약 (2026-08-27)

> 이 문서는 『성인 중증 천식 생물학적 치료제 임상진료지침』(PACEN) 홈페이지의 Decision Support(치료 결정 지원) 모듈 고도화 작업 전체를 한 곳에 정리한 인덱스다. 세부 설계·근거는 하단 "세부 문서" 표의 개별 파일을 참조.
>
> 브랜치: `claude/homepage-menu-content-update-jj1uqr` · 근거: KQ1~KQ8 근거 프로파일, 프로젝트 설명서(I-5~I-7), 연구개발계획서(RS-2025-02303166)

## 1. 무엇이 고도화되었는가 — 한눈에 보기

| 단계 | 내용 |
|---|---|
| **v1** | 병렬 카드 데모: 순위 단정 없는 6약제 비교, 절대효과(95% CI) 표시, MCDA 선호도 점수 |
| **v2** | 의료진 설문 원자료(N=52) 정밀 가중 반영, 확정 규칙 토글 제거, 비용·보험 입력 접힘 |
| **v2.1** | **GRADE 확실성 계수**로 MCID 초과확률 보정(넓은 CI 보상 편향 제거), **편의성 축 규칙화**(투여 횟수 역비례, 가상 점수 폐기), KQ6 OCS·안전성 원문 반영, reslizumab 바이알 내림 확정, 비용 범위 약제비만 확정 |
| **v3** | **의료인용·환자용 페이지 완전 분리**. 환자용은 페르소나 6종 기반 4단계 대화형 플로우(내 상황→우선순위→결과→상담 준비)로 신설 |
| **v3.1** | outcome별 "나의 예측 카드" 카드뉴스(발작·스테로이드·폐기능·조절설문 ACQ·삶의 질 AQLQ — 기저치 입력 시 전후 막대 시각화) |

## 2. 이중 UI 구조

기존 '보기 토글' 방식을 폐기하고 **진입점부터 분리된 두 페이지**로 재구성했다.

- **의료인용** (`prediction-tool-demo.html`) — 전 지표·95% CI·GRADE 등급·MCDA 계산 상세 전부 노출. 선호도 가중치 출처(환자 설문/의료진 설문/균등/직접입력) 토글 제공.
- **환자용** (`prediction-tool-patient.html`) — 대화형 4단계. 검사 수치 **"몰라요" 허용**(모르면 해당 약제를 "검사 후 판단" 상태로 분리 표시), **페르소나 6종**이 입력 기반 자동 추천되며 자유 선택 가능(적격 약제 목록에는 영향 없음 — 정보 배치·문장 톤·숨은 4축 가중치만 조정). 점수·수식·통계 용어는 숨기고 "넉넉히 보면 ○~○" 식 쉬운 범위 문장으로 불확실성을 정직하게 전달.
- **통합 페이지** (`prediction-tool-unified.html`) — 두 화면을 상단 탭(`#pro` / `#pt` 딥링크)으로 오가는 단일 파일. 두 앱을 각각 내장 프레임으로 격리해 스타일·ID 충돌 없이 그대로 탑재.

### 환자용 페르소나 6종

| 페르소나 | 자동 추천 신호 | 핵심 문장 |
|---|---|---|
| 응급실이 무서워요 | 최근 12개월 악화 ≥3회 | "또 새벽에 응급실 갈까 봐 늘 불안해요" |
| 스테로이드를 줄이고 싶어요 | 유지 OCS 사용 중 | "먹는 스테로이드 부작용이 무서워요" |
| 바쁜 일상이 우선이에요 | 악화<3회 & OCS 없음 | "병원 갈 시간이 없어요, 집에서 맞고 싶어요" |
| 비용이 제일 걱정이에요 | 실손보험 정보 입력 | "효과 좋아도 못 버티면 소용없잖아요" |
| 다른 병도 같이 있어요 | 비염·비용종·아토피 체크 | "비염·비용종·피부까지 다 힘들어요" |
| 예전처럼 움직이고 싶어요 | 기본값 | "계단, 등산, 아이랑 뛰는 게 소원이에요" |

설계 근거·조사(브레인스토밍 16개 질문 마스터 리스트, DCE 문헌, 국내 비용 부담 보도 등)는 `patient-ui-personas-research.md` 참조.

## 3. 알고리즘 고도화 (v2.1)

- **MCID 초과확률 × GRADE 확실성 계수** — 지표 점수 `s = Φ(z) × CERT(GRADE)`, CERT = 높음 1.0·중등도 0.9·낮음 0.75·매우낮음 0.55. omalizumab이 넓은 CI 때문에 부당하게 높은 점수를 받던 편향을 보정(효과 축 76→62점).
- **편의성 축 규칙화** — `100 × 6.5 ÷ 연간 유지기 투여 횟수`, 원내 정맥투여(reslizumab)는 ×0.8. 가상 점수를 완전히 대체.
- **KQ6(tezepelumab) 원문 재확인** — OCS 지표는 "≤5mg/day 도달 비율 RR 1.06(0.87–1.30), SOURCE 단일연구, 유의차 없음"이 유일 지표임을 확인해 0점 처리가 정당함을 검증. 안전성 행도 실측치(AE RR 0.95·SAE RR 0.67)로 교체.
- **비용 모델 확정** — 약제비만 계산(행위료·진찰료 제외), reslizumab 100mg 바이알 내림(65kg 예시: 260mg 처방→2바이알 200mg 투여) 확정.

전체 수식·계산 예시·가정 목록은 `cdss-specification.md`(.html) 참조.

## 4. 환자용 예측 카드뉴스 (v3.1)

약제 상세를 Q&A 목록에서 **가로 스냅 스크롤 카드덱**으로 전환. 환자가 1단계에서 기저치를 입력한 지표는 모두 "지금 → 치료 후" 막대로 시각화된다.

| 카드 | 시각화 조건 |
|---|---|
| 발작(악화) | 항상 (연간 악화 횟수는 필수 입력) |
| 먹는 스테로이드 | 유지 OCS 입력 시 |
| 폐기능(FEV1) | FEV1(L) "알아요" 선택 시 |
| 증상 조절(ACQ) | ACQ(0~6점) "알아요" 선택 시 — 유의차 없는 지표는 문장으로 정직 표기 |
| 삶의 질(AQLQ) | AQLQ(1~7점) "알아요" 선택 시 — 메폴리주맙 등 유의차 없는 경우 그대로 표기 |
| 비용 / 투여·안전 / 근거 수준 | 항상 |

## 5. 산출물 — 바로 보기

### 인터랙티브 도구 (HTML)

| 이름 | GitHub (소스) | 인터랙티브 미리보기 |
|---|---|---|
| 의료인용 CDSS 데모 | [prediction-tool-demo.html](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/prediction-tool-demo.html) | [열기](https://claude.ai/code/artifact/0911bb2f-0872-42d9-b836-42c628e1cf9d) |
| 환자용 도구 | [prediction-tool-patient.html](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/prediction-tool-patient.html) | [열기](https://claude.ai/code/artifact/1775faf3-c2b3-496a-9a09-83ac722d4bf7) |
| 통합 페이지(탭 전환) | [prediction-tool-unified.html](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/prediction-tool-unified.html) | [열기](https://claude.ai/code/artifact/4e27e8c2-8ef0-45b8-b277-429adc408e7b) |
| CDSS 사양서 | [cdss-specification.html](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/cdss-specification.html) | [열기](https://claude.ai/code/artifact/2724e2bd-70cd-48c2-9eff-486ff543fc6b) |
| 환자용 UI 디자인 캔버스 | [docs/design/patient-ui/](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/tree/claude/homepage-menu-content-update-jj1uqr/docs/design/patient-ui) | [열기](https://claude.ai/code/artifact/60ff1f4d-18b8-42d3-bce9-a5c9d2e5428a) |

> 인터랙티브 미리보기 링크는 Claude 아티팩트로, 링크를 공유한 경우 공유 시점 버전에 고정된다 — 최신판을 다른 사람도 보게 하려면 공유 메뉴에서 핀을 최신 버전으로 옮겨야 한다.

### 세부 설계 문서

| 문서 | 내용 |
|---|---|
| [cdss-specification.md](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/cdss-specification.md) | 알고리즘 A~I 전체, 가정 A1~A14, 확정 결정 17건, 미결 10건 |
| [cdss-v2-changes.md](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/cdss-v2-changes.md) | v2→v2.1 변경 내역 |
| [patient-ui-personas-research.md](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/patient-ui-personas-research.md) | 환자 질문 브레인스토밍·문헌조사·페르소나 설계 근거 |
| [brochures/patient-brochures-collection.md](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/brochures/patient-brochures-collection.md) | 약제별 제약사 환자용 자료 링크 모음 |
| [homepage-content-enhancement-plan.md](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/blob/claude/homepage-menu-content-update-jj1uqr/docs/homepage-content-enhancement-plan.md) | 사이트맵·콘텐츠 고도화 원안 |

## 6. 미결 사항 (요약)

1. GRADE 확실성 계수(CERT) 값의 운영위 캘리브레이션
2. 편의성 축 속성표 확장(자가투여 허가·투여 시간)
3. 테즈파이어 비급여 예상가 대표값
4. 실손 세대별 프리셋 대표값
5. 페르소나 4축 가중 프리셋 검증(환자위원회)
6. NMA 수행·출판
7. 제약사 6개사 환자용 브로셔 게재 허락 요청

전체 목록은 `cdss-specification.md` §6 참조.

## 7. 저장소 관련 참고

현재 모든 산출물은 이 저장소(`APAAACI_mast_to_HL7FHIR`)의 `claude/homepage-menu-content-update-jj1uqr` 브랜치 `docs/` 폴더에 있다. 이 저장소는 원래 다른 프로젝트(OCR·HL7 FHIR 변환)용이므로, 별도 저장소(`severe_asthma_guidline_CDSS`)로의 이전이 논의된 바 있으나 저장소 생성 권한 문제로 보류 중이다 — 저장소가 생성되면 `doc/` 폴더로 전체 이전 가능.
