# 알레르겐 환경관리 교과서 반영 (2026-10-09)

`input_textbook/` 에 들어온 교과서 자료를 읽고, 기존 지식베이스·템플릿·온톨로지 그래프를 교과서 근거로 고도화한 기록.

## 1. 읽은 자료

| 자료 | 분량 | 읽은 방법 |
|---|---|---|
| 한글 알레르기 교과서 — 제4부 알레르기질환의 치료원칙과 약물 · 제1장 **환경관리 및 회피요법** (pp.571–584) | 스캔 16면(중복 2면) | 전면 판독. 책 제목·판·저자는 스캔 면에 없어 미확인(참고문헌 최신 2010년) |
| Middleton's Allergy 8판(추정) ch.86 **Allergen Control for Prevention and Management of Allergic Diseases** (Custovic, Tovey; pp.1406–1415) | 10면 | 전문 판독 |
| ch.28 **Indoor Allergens** (Platts-Mills; pp.453–469) | 17면 | 전문 판독 |
| ch.27 **Aerobiology of Outdoor Allergens** (Weber; pp.430–452) | 23면 | 보조 에이전트가 구조화 추출(216개 인용을 원문 줄과 대조) → 본문과 재대조 |
| ch.26 **The Structure and Function of Allergens** (Stewart 외; pp.398–429) | 32면 | 보조 에이전트가 구조화 추출(993개 줄 참조·355개 인용 대조) → 본문과 재대조 |

`input_images/` 에 같은 날 들어온 JPEG 24장은 검사 결과지(OCR 용)로 보여 이번 작업 범위에 넣지 않았다.

## 2. 기존 내용과 비교해 고친 것

### 2.1 사실 관계 교정

| 항목 | 기존 | 교과서 | 조치 |
|---|---|---|---|
| 진드기 번식 조건 | 20~25℃, 습도 70% 이상 | 25℃ 최적(18~27℃), 상대습도 60%(21℃)~75%(16℃), 절대습도 7 g/kg | 지식베이스·템플릿 문장 교정 |
| 진드기 알레르겐 운반 | 언급 없음 | 배설물 알갱이 10~35 µm, 교란 시에만 수 분 부유 → 공기청정기 효과 의문 | 노출 환경·수칙·핵심 포인트에 반영 |
| 노출 역치 | 없음 | Der p 1 2 µg/g 감작·10 µg/g 천식, Fel d 1 8 µg/g 증상, Bla g 2 U/g 감작·8~10 U/g 질병 | 지식베이스 핵심 포인트 + 데이터셋 `thresholds` |
| 고양이 알레르겐 | "입자가 매우 작고 가벼워" | 15~40%가 5 µm 미만, 수 시간 부유, 끈적해 옷·벽에 부착, 제거 후 3~6개월(매트리스 수년) | 교정·수치화 |
| 고양이 관리 | 침실 금지·HEPA·목욕·청소 (동급 나열) | 증상 있으면 **동물 제거가 유일하게 확실**; 목욕은 주 2회 이상 해야 유지, 단독 효과 작음; 공기청정기는 개인 노출 감소 근거 약함(Cochrane 소규모 2건 무효) | 수칙 순서·표현을 단계별로 재배열 |
| 바퀴 | 방제·트랩 일반 언급 | 미끼형(겔·베이트) 우선, 분무형은 효과 적고 천식 자극; 방제 1주 뒤 사체·배설물 물청소; 다세대 건물 전체 방제; 국내 소아 12.5%·성인 20% 감작 | 수칙 교정 |
| 곰팡이 | 실내 중심 | 실내(번식지 제거·습도·살진균제)와 실외(Alternaria·Cladosporium 건조한 오후 / 담자포자 비 온 뒤·습한 밤, 수확·낙엽·퇴비) 분리; 국내 대기 포자 6~12월 | 템플릿 분리 서술, 데이터셋에 `mold_indoor`·`mold_outdoor` |
| 꽃가루 | 일반 수칙 | 풍매화 17~58 µm; 1 cm 이상 지속 강우 80~99% 세척 vs 뇌우 25~33% + 꽃가루 파열 → **뇌우 천식**; 습도↑ 시 잔디 꽃밥 닫힘; priming; 20~100 grains/m³ 증상 유발 | 수칙에 '뇌우 전후 실내' 추가, 비 온 뒤 문장을 '꾸준한 비'로 한정 |
| 국내 꽃가루 | 잡초 8~10월 | 잡초가 국내 호흡기 알레르기에서 가장 중요, 8월 초~10월(9~10월 피크), 쑥>환삼덩굴>돼지풀; 삼나무 제주·남해안 3월; 목초 4~10월(양 적음) | 환삼덩굴·쑥·돼지풀 항목에 반영 |
| 교차반응 | 과(科) 단위 일반 서술 | 진드기 group 2 >90% 동일(두 종 양성 = 하나의 감작); 트로포마이오신 ~80%(진드기·바퀴·새우·아니사키스); 측백나무과 강한 교차; 온대 잔디 ↔ 우산잔디 교차 없음; 쑥 LTP 경로는 전신 반응 가능; 알파갈 → 고양이 검사 거짓 양성 | 14종 교차반응 문장 교정 |

### 2.2 두 교과서가 다르게 말하는 곳 (데이터셋 `disagreements_ko`)

- **회피 효과 전반**: 한글 교과서는 집먼지진드기 회피 효과를 '확실하다'고 적고(1980년대 격리 연구), Middleton 86장은 Cochrane 무효(54개 시험 3,002명)를 들어 단일 조치를 권하지 않으며, 같은 책 28장은 그 메타분석이 결함이 있다고 비판한다. 환자 문장은 "단일 조치는 효과가 없고 포괄적 관리는 도움이 될 수 있다"로 통일했다.
- 실내 습도 목표(40% 미만 vs 45~50% 미만) → "50% 미만, 가능하면 45% 이하".
- 고양이 작은 입자 비율(15% vs 10~40%) → "약 15~40%".
- 고양이 제거 후 감소 기간(4~6개월 vs 12~16주) → "보통 3~6개월, 매트리스는 더 오래".
- 바퀴 역치(2/8 U/g vs >10 U/g) → 병기.
- Alternaria 포자 크기는 27장 안에서도 두 값(12~150 µm / 20~60×9~18 µm).
- 26장 표의 인쇄 오류(돼지고기-고양이 증후군 방향, 호두·브라질너트 이름 바뀜 등)는 인용하지 않았다.

### 2.3 환자 문장에 넣지 않은 것

- 대기오염이 심한 날의 흡입스테로이드 용량 조정(한글 교과서) — 약 조정은 진료에서. 데이터셋에는 "교과서는 언급하지만 약 조정은 반드시 진료에서"로만 적었다.
- 1차 예방 조언 — 7개 코호트의 결과가 엇갈려(MAAS 는 감작 2.85배 증가, CAPS 는 습진 증가) 근거 기반 조언이 없다. "증상 없는 가족을 위해 집을 바꾸는 조치는 근거가 없다"는 문장만 둔다.

## 3. 새로 만든 것

### 3.1 `data/allergen_environment_control.json` (v1.1)

알레르겐군 8개(집먼지진드기·고양이·개·바퀴·실내 곰팡이·실외 곰팡이·꽃가루·설치류) + 악화 요인 4개(담배연기·실내 연소·대기오염·기후변화) + 1차 예방 요약.

군마다: 생물학 · 주요 알레르겐(단백군·출처) · 운반 입자(크기·부유 시간·관리 함의) · 저장소 · 노출 역치 · 국내 자료 · 잔류 기간 · 교차반응 · **조치(measures)** · 근거 요약 · 환자 요약.

조치 1건 = `tier`(1단계 필수 / 2단계 도움 / 3단계 제한적 / 비권장 — 한글 교과서 표 1-1 의 3단계를 전 군에 확장) + `evidence_grade`(RCT 양성/엇갈림/무효, 체계적 고찰 무효, 관찰, 기전, 교과서 권고) + `evidence_ko`(어떤 연구가 무엇을 보였나) + `caveat_ko` + `sources`(39개 서지; 1차 문헌은 "교과서 인용 — 원문 미열람"으로 표시).

예: 진드기 15개 조치 가운데 `mite_encasing` 은 1단계이지만 근거 등급은 `rct_mixed`(성인 1,000명 RCT 무효·소아 60명 RCT 양성)로, "커버만으로 충분하다고 기대하지 말라"는 caveat 가 붙는다.

### 3.2 `services/environment_control_service.py`

항원 → 군 판별(`group_for`), 단계순 조치(`measures`), 환자용 짧은 수칙(`tiered_tips`), 질문 키워드 매칭(`measures_matching`: 한국어 조사 처리를 위한 접두어 매칭 + 희소성 가중), 질문 게이팅(`groups_for_question`: 관리·회피 의도가 있고 보고서 값 질문이 아닐 때만), 판정 결과 → 군별 안내(`for_assessments`: '실제 주의' 항원만).

### 3.3 온톨로지 그래프 확장 (`services/ontology_service.py`)

기존 Wikipedia 질환 스냅샷(RDF) 위에 교과서 알레르겐 층을 얹었다. 트리플 약 11,450개(기존 약 10,400개).

| 노드 | 술어 | 비고 |
|---|---|---|
| `AllergenGroup` | `hasControlMeasure` `hasMajorAllergen` `hasThreshold` `hasCrossReactivity` `hasReservoir` `hasCarrierParticle` `peakMonthKorea` **`associatedWithDisease` → 기존 질환 `Topic`** | 질환 스냅샷과 한 그래프에서 조인된다 |
| `ControlMeasure` | `tier` `tierOrder` `evidenceGrade` `evidenceNote` `caveat` `citedIn` | `reviewStatus = textbook_curated` |
| `ExposureThreshold` `MajorAllergen` `CrossReactivity` `TextbookSource` `AggravatingFactor`(`aggravates` → 천식·비염) `Principle` | | |

```sparql
PREFIX allergy: <https://apaaaci.local/allergy-ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?measure ?tier ?disease WHERE {
  ?g allergy:query "mite" ; a allergy:AllergenGroup ;
     allergy:hasControlMeasure ?m ; allergy:associatedWithDisease ?d .
  ?m rdfs:label ?measure ; allergy:tier ?tier ; allergy:tierOrder ?o .
  ?d allergy:query ?disease } ORDER BY ?o
```

읽기 전용 도구도 확장: `search_topics("집먼지진드기")` → `kind: allergen`, `get_topic_context("cat")`, `get_evidence("textbook:gotzsche_2008")`, `retrieve()` 의 `allergen_topics`. 질환 주제 5개와 그 검색 결과(`hay fever` → 비염만)는 그대로다.

### 3.4 챗봇 주입 (`services/result_chat_service.py`)

관리·회피 질문("고양이를 계속 키워도 될까요?", "공기청정기가 도움이 되나요?")에 `[알레르겐 환경관리 일반 지식 — 교과서 정리]` 블록을 넣는다. 알레르겐 이름이 없는 질문은 **이 환자의 '실제 주의' 항원군**으로 좁힌다. 프롬프트 규칙 25~28 추가: 실제 주의 항원에만 적용, 감작만 항목에는 '엄격한 회피 불필요', 동물 제거·이사·기기 구매를 지시하지 않음, 약 지시 없음. "제 집먼지진드기 수치가 얼마였죠?" 같은 보고서 값 질문에는 여전히 아무것도 붙지 않는다.

### 3.5 리포트 · API

- 리포트 '우선 실천 회피 수칙' 아래에 **"왜 이렇게 하나요 — 환경관리 근거 요약"** 블록(실제 주의 항원의 군마다 환자 요약·근거 요약·우선순위 3개). 음식·벌독·약물은 비운다.
- `GET /api/knowledge/environment-control?group=mite|…` 또는 `?category=&name=` (+`q=` 질문 키워드) · `GET /api/knowledge/environment-control/overview` · `GET /api/ontology/topics` 에 `allergen_topics`.

### 3.6 기존 데이터 갱신

- `data/allergen_category_profiles.json` v1.1 — mite·animal·insect·mold·pollen_tree/grass/weed 템플릿의 노출 환경·회피 수칙(단계 순, UI 는 앞 5개 표시)·교차반응·핵심 포인트.
- `data/allergen_knowledge_base.json` v1.1 — 흡입 알레르겐 14종(`review_status: textbook_updated`, `environment_control_group`, 출처 4종) + 새우 트로포마이오신 문장.
- `data/allergen_knowledge_generated.json` 재생성(139종, LLM 소개문 candidate 124·approved 2 유지).
- `web/i18n.js` 출처 안내 문구: "백과사전·교과서 기반".

## 4. 검증

- `test_environment_control.py` 47건 — 데이터 무결성(출처 실재·단계·등급·약 지시 금지), 군 판별, 질문 게이팅, 그래프 조인(진드기 → 천식·비염·아토피), 챗봇 주입·프롬프트 규칙, 리포트 블록, 지식베이스 갱신 확인.
- 기존 `test_ontology.py`·`test_allergen_knowledge.py`·`test_result_chat.py` 113건 통과(질환 주제 5개·검색·게이팅 불변식 유지).

## 5. 2차 작업 (같은 날, 검토 보고서 이후)

검토용 HTML(`docs/review_2026-10-09_알레르겐_환경관리_교과서반영_검토보고서.html`)을 만든 뒤 권장 후보 ①·④·② 를 진행했다.

### 5.1 검토·승인 워크플로 (후보 ①)
- 조치마다 `review.status`: `candidate`(기본) → `approved` / `revised`(검토자가 문장을 고침, 승인과 같이 취급) / `rejected`(환자 화면에서 뺌). `reviewed_by`·`reviewed_on`·`note`, revised 는 `original_*` 보존.
- `scripts/review_environment_control.py` — `--stats` `--list [--group] [--status]` `--show` `--approve` `--reject` `--revise --text/--short` `--approve-group` `--import review.json --by 이름`. `--import` 는 검토 HTML 의 "검토 결과 복사(JSON)" 를 그대로 받아 체크한 조치(`m:`/`f:`)를 승인하고 군 메모(`note:<군>`)를 `review_note` 로 넣으며, 쟁점·설계·지식베이스 메모는 로그와 화면에 돌려준다.
- **환자 화면 게이트** `ENV_CONTROL_PATIENT_GATE`: `candidate_ok`(기본, 검토 전 조치도 '검토 전' 표기와 함께 노출 — 기존 임상 근거 데이터와 같은 정책) / `approved_only`(승인·수정만 노출). `rejected` 는 게이트와 무관하게 항상 빠진다. 게이트는 리포트·카드뉴스·챗봇(`measures(patient_facing=True)`)에만 걸리고 `/api/knowledge/environment-control` 개발 조회에는 걸리지 않는다(`review_status` 로 구분, `include_rejected=` 선택).
- 변경 로그 `data/environment_control_review_log.json`, 데이터 파일의 `review_summary`. 테스트 `test_environment_control_review.py` 9건.

### 5.2 카드뉴스 근거 메모 (후보 ②)
- 도감·클래식 카드뉴스의 '우선 실천할 생활 수칙' 카드 **안에** "📚 왜 이렇게 하나요" 메모(군당 첫 문장, 최대 2군, 전문의 검토 전 표기)를 붙였다. 새 카드를 만들면 덱 길이(12장)와 카드 순서 회귀 테스트가 깨지므로 카드 수는 그대로다.

### 5.3 레지스트리 정리 (후보 ④)
- `2-spotted spider mite`(점박이응애, 식물 해충)를 `instructions/allergen_map_prompt_v2.json` 에서 `Other` 로 바꾸고 레지스트리를 재생성 — 집먼지진드기 수칙이 붙지 않는다.
- `animal` 템플릿에 종별 `overrides`(닭·소 비듬·말 비듬·기니피그·햄스터·생쥐·토끼·쥐): 양계장·깃털 침구, 축사 직업 노출, 승마·마구간, 케이지 바닥재·실험동물 수칙과 교차반응(조류-계란, 소 알부민, 리포칼린). "집에서 내보내기" 문장은 반려 설치류에만 남는다.

## 6. 남은 과제

- **알레르기 전문의 검토**: 데이터셋 조치 62개(+악화 요인 4개)는 모두 `candidate`. 검토 HTML 로 체크·메모 → `--import` 로 반영 → 검토가 끝나면 `ENV_CONTROL_PATIENT_GATE=approved_only`. 지식베이스·템플릿 문장(`textbook_updated`)은 아직 승인 체계 밖이다.
- 한글 교과서의 **서지(책 제목·판·저자)** 확인 후 `sources.kaaaci_textbook_env.citation` 보완.
- 꽃가루 하루 중 방출 시간대·국내 꽃가루 농도 자료·환삼덩굴 교차반응은 읽은 교과서에 없다(27장은 북미 중심). 국내 공중화분 보고(오재원 등)를 추가로 읽으면 보강할 수 있다.
- 알레르겐군 ↔ SNOMED/레지스트리 코드 연결, 환경관리 층의 의미 검색(임베딩).
