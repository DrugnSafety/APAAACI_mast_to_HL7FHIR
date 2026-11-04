# 알레르기 교과서 최신판 제5장 반영 — 악화 요인 층 확장 (2026-10-13)

`input_textbook/` 에 2026-10-12 새로 들어온 스캔 12면을 읽고 `data/allergen_environment_control.json` 을 v1.1 → v1.2 로 올린 기록.
1차 작업(2026-10-09, [allergen_environment_textbook_integration_2026-10-09.md](allergen_environment_textbook_integration_2026-10-09.md))의 알레르겐군 8개·조치 62개는 바꾸지 않았다.

## 1. 바뀐 내용이 있는지 — 결론

**새 스캔은 최신판에서 이전 판 '환경관리 및 회피요법' 장을 대체·재편한 장이다**(사용자 확인 2026-10-13). 장 제목·위치·내용 구성이 모두 바뀌었다.

| | 2026-10-09 (v1.1) | 2026-10-12 입수 (v1.2) |
|---|---|---|
| 장 | 제4부 알레르기질환의 치료원칙과 약물 · 제1장 **환경관리 및 회피요법** (pp.571–584, 16면) | 제1부 면역과 알레르기학 · 제5장 **알레르기질환과 환경** (송대진, pp.77–88, 12면) |
| 내용 | 집먼지진드기·동물·바퀴·곰팡이·꽃가루별 회피 수칙(표 1-1~1-3) | 기후변화 · 실외 대기오염(미세먼지·황사·SO₂·NO₂·오존) · 실내공기오염(간접흡연·전자담배·VOC) · 기전 · 위생가설 · 미생물총 · 유전-환경 상호작용 |
| 판 | 참고문헌 최신 2010 | IPCC AR6(2021/2023)·국민건강보험 2014–2016·서울 황사일수 2013–2023 인용 → 2023년 이후 간행 추정 |

따라서 바뀐 점은 두 가지다. **(1) 악화 요인 층이 장의 중심이 됐다.** 이전 장이 한 문단으로 다룬 대기오염·담배·기후변화가 최신판에서는 성분별 역치·국내 자료·기전까지 갖춘 한 장이 됐다. 이전 장과 수치가 다른 곳(담배 화학물질 4,000+ vs 3,800 등)은 `disagreements_ko` 에 병기했다. **(2) 알레르겐별 회피 수칙(이전 판 표 1-1~1-3)은 최신판 해당 장에 없다.** 최신판이 회피 요법을 부정한 것이 아니라 다루지 않은 것이므로(Middleton 86장은 포괄적 관리를 권함) 알레르겐군 조치 62개는 삭제하지 않되, 출처별로 대조해 **이전 판 한글 교과서에만 기대는 16개**를 `edition_support = previous_ko_only` 로 표시해 전문의 검토 대상으로 올렸다(§3.4).

## 2. 읽은 자료

| 면 | 내용 |
|---|---|
| p.77 | 서론(유전 × 환경, 이주 역학, 국내 30년 증가 요인) · 1. 기후변화(IPCC AR6 1.1℃, 2021–2040 1.5℃) |
| p.78 | 그림 5-1(온실가스·지표온도), 그림 5-2(CO₂ ↔ 꽃가루 양) · 뇌우 천식 기전 · 꽃가루 농도·항원성·개화 시기 |
| p.79 | 2. 공기오염 가. 실외 — 배출원, 그림 5-3(1963–2000 국내 소아천식 유병률 3.2→12.8% vs 아파트·자동차·핵가족) |
| p.80 | 1) 미세먼지 — PM10/2.5/0.1, 2차 오염물질, 그림 5-4(PM2.5 추세 1998–2018), 국민건강보험 당일~4일, 캘리포니아 1,759명 |
| p.81 | 그림 5-5(입자 크기 ↔ PEF) · PM2.5 FEV1 1.6% vs 17.9% · 비염(베이징·국내 도로) · 아토피(5일 전 PM) · 발생 연관은 불일치 · 2) 황사 |
| p.82 | 그림 5-6(서울 황사일수) · 황사 독성·3일 영향 · 3) 이산화황(0.5/0.25 ppm, 2분·5–10분·30분, 비강호흡) · 4) 이산화질소 |
| p.83 | NO₂ 4.0/0.4 ppm, 0.2 SO₂+0.4 NO₂, 10 µg/m³당 1.9% · 5) 오존(토론토, 캘리포니아 3.3배, 1–2일 지연, 운동 최소화 권고) · 나. 실내공기오염 |
| p.84 | 실내 오염원·취약군 · 1) 간접흡연(3,800종, 38%, 15개 코호트, Th2 기전) |
| p.85 | 전자담배 · 2) 휘발성 유기화합물(배출원·증상·임산부 Th2·폼알데하이드) · 다. 기전(ROS) |
| p.86 | 그림 5-7(기전 모식도) · MAPK/NF-κB/AP-1 · TLR · GSTP1 · 운반체·DEP 보조제 · 후생유전(FOXP3·IL10) · 3. 위생가설 |
| p.87 | 위생가설 한계 · 미생물 노출 다양성 · 4. 미생물총(유익/해로운 요인) · 5. 유전인자와 환경인자의 상호작용 |
| p.88 | 세대 간 후생유전 · 유전자-유전자/환경-숙주/환경-환경 상호작용 |

## 3. 데이터셋 v1.2 — 무엇을 어디에 넣었나

### 3.1 악화 요인(`aggravating_factors`) 4 → 5

| 키 | 변경 |
|---|---|
| `tobacco_smoke` | 문장 교체(3,800종·38%·15개 코호트·응급실·Th2 기전) + `ecigarette_ko` + 조치 `smoke_ecigarette_not_substitute`(1단계·관찰) |
| `indoor_combustion` | 문장 보강(저개발국 연소, 국내 환기 부족 건물) |
| `outdoor_air_pollution` | 문장 교체 + **`components`**(pm · asian_dust · so2 · no2 · ozone: 설명·`thresholds`·`evidence_ko`·`korea_ko`·출처) + `korea_ko`(그림 5-3 수치) + 조치 4건 `pollution_check_forecast`(1단계) · `ozone_limit_exercise`(1단계, 교과서 명시 권고) · `dust_storm_reduce_exposure`(1단계, 교과서 자료 + 일반 권고) · `so2_nasal_breathing`(2단계·기전) ; 기존 `pollution_stay_indoors` 문장 갱신 |
| `climate_change` | 문장 교체(IPCC AR6·뇌우 기전·CO₂↔꽃가루·황사 증가) + 조치 `climate_thunderstorm_pollen_season`(꽃가루 군 수칙과 같은 내용, 리포트 중복 방지 caveat) |
| `indoor_air_pollution` **신설** | VOC·폼알데하이드·신축·환기 부족. 조치 `voc_ventilate_new_materials`(1단계·교과서 권고) · `voc_limit_sprays_solvents`(2단계) · `voc_pregnancy_exposure`(2단계·관찰) |

역치는 진드기 Der p 1 과 같은 모양(`marker`·`value`·`unit`·`meaning_ko`)으로 넣었다: SO₂ 0.5·0.25 ppm, NO₂ 4.0·0.4 ppm·10 µg/m³.

### 3.2 신설 섹션

- **`mechanisms`** 7건 — ROS·산화스트레스(MAPK/PI3K/NF-κB/AP-1), TLR 등 PRR, GSTP1 취약성, 미세먼지 운반체·DEP 보조제, 상피·피부 장벽 손상, 후생유전(FOXP3·IL10 메틸화), Th2/Th17. 항목마다 전문 설명 `text_ko` 와 환자용 한 줄 `patient_ko`.
- **`etiology_context`** 4건 — 서구화·이주 역학, 위생가설과 한계(Strachan 1989), 미생물총(`beneficial_ko`/`harmful_ko`), 유전-환경 상호작용(TLR·CD14, 세대 간 후생유전). **지시 문장 없음** — 농장·반려동물·생우유를 권하지 않는다(테스트로 고정).

### 3.3 출처 +5, 쟁점 +7

출처: `kaaaci_textbook_ch5_env`(본문), `ipcc_ar6_2021`, `hammer_2020_pm25`(Environ Sci Technol 2020;54:7879), `strachan_1989`, `nhis_korea_2014_2016_pm_asthma`(교과서 인용·원문 미열람 표시).
쟁점: 담배 화학물질 수 · SO₂ 역치(일치 + 0.25 ppm 추가) · 대기오염 서술 초점(집단 지표 vs 개인 노출) · 기후변화 근거 · 1차 예방 vs 미생물총(충돌 아님, 지침 아님) · 미세먼지와 '발생'(악화에 한정).

### 3.4 판 대조(`edition_tracking`) — 알레르겐군 조치 62개 + 악화 요인 조치 13개

| edition_support | 뜻 | 개수 |
|---|---|---|
| `latest_ko` | 최신판 5장 근거 | 12 |
| `previous_ko_plus_middleton` | 이전 판 한글 교과서 + Middleton | 32 |
| `middleton_only` | Middleton 만 | 15 |
| `previous_ko_only` | **이전 판 한글 교과서(또는 그 장이 인용한 국내 문헌)에만 근거 — 최신판 미수록** | **16** |

이전 판 전용 16개: 진드기 베개 소재·옷장 관리, 고양이/개 만진 뒤 손·얼굴 씻기, 바퀴 건물 전체 방제, 실내 곰팡이 화분·진드기 연계·공기청정기, 실외 곰팡이 발생지 회피·배수/낙엽·귀가 후 샤워, 꽃가루 마스크/고글·샤워/옷 갈아입기·실내 건조·잔디 깎기 회피, 실내 연소 환기. 모두 무해한 생활 수칙이라 그대로 내보내되, 출처 `kaaaci_textbook_env` 는 `edition_status = superseded`, 조치 응답·그래프(`editionSupport`)·API(`edition_tracking`)에서 구분된다. 검토 워크플로의 approved/revised/rejected 가 정해지면 그 상태가 우선한다.

## 4. 코드

| 파일 | 변경 |
|---|---|
| `services/environment_control_service.py` | `edition_label`·`edition_tracking`, 조치 응답에 `edition_support(_ko)` · `FACTOR_WORDS`(요인별 질문 낱말, 한·영·중) · `factors_for_question(text)`(보고서 값 질문 제외, 환자 항원군으로 좁히지 않음) · `factor_summary(key, question)`(성분·역치·단계순 조치·출처) · `mechanisms()` · `etiology_context()` |
| `services/ontology_service.py` | `_add_environment_layer`: `Pollutant`(`hasComponent`, `hasThreshold` → `ExposureThreshold`) · `Mechanism`(`hasMechanism`, 대기오염·실내공기·담배 요인에) · `EtiologyFactor`(`hasEtiologyContext`, 천식·비염·아토피 Topic 에; `protectiveFactor`/`riskFactorContext`) · 미세먼지·VOC·담배는 아토피피부염에도 `aggravates`. 새 메서드 `factor_context`(SPARQL 로 성분 역치 조회) · `mechanism_notes`. `retrieve()` 결과에 `factor_topics`·`mechanisms`('왜/이유/기전' 질문에만). `ControlMeasure` 에 `editionSupport`. `PREDICATE_LABEL_KO` 5개 추가. 트리플 약 11,450 → 12,030 |
| `services/result_chat_service.py` | `ontology_block` 에 `(악화 요인: …)` 하위 블록(요약·성분·역치·근거·국내·조치·기전 한 줄). 프롬프트 규칙 25 보강(요인은 양성 항원과 무관하게 적용, 역치는 집단 수치) |
| `server.py` | `/api/knowledge/environment-control/overview` 에 `mechanisms`·`etiology_context`·`edition_tracking` |
| `test_environment_control.py` | `TestChapter5Layer` 10건 + `TestEditionTracking` 3건 |

리포트·카드뉴스는 바꾸지 않았다. 악화 요인은 검사 결과로 판정되는 것이 아니라 천식·비염·아토피 환자 누구에게나 해당하는 일반 지식이므로, 질문에 그 말이 있을 때 챗봇에서만 쓰고 리포트의 '실제 주의' 항원 블록에는 섞지 않는다.

```text
"황사 올 때 외출해도 되나요?"  → factors_for_question → ['outdoor_air_pollution']
"제 미세먼지 수치가 얼마였죠?" → []   (보고서 값 질문)
"공기청정기가 도움이 되나요"    → []   (알레르겐군 질문 — 기존 경로)
"왜 미세먼지가 천식에 안 좋은가요?" → factor_topics + mechanisms[ros_oxidative_stress …]
```

```sparql
PREFIX allergy: <https://apaaaci.local/allergy-ontology#>
PREFIX rdfs: <http://www.w3.org/2000/if/rdf-schema#>
SELECT ?pollutant ?marker ?value ?unit ?mechanism ?disease WHERE {
  ?f allergy:query "outdoor_air_pollution" ; a allergy:AggravatingFactor ;
     allergy:hasComponent ?p ; allergy:hasMechanism ?m ; allergy:aggravates ?d .
  ?p rdfs:label ?pollutant ; allergy:hasThreshold ?t .
  ?t allergy:marker ?marker ; allergy:value ?value ; allergy:unit ?unit .
  ?m rdfs:label ?mechanism . ?d rdfs:label ?disease }
```

## 5. 검증

- `test_environment_control.py` 46건(+13) · `test_environment_control_review.py` 9건 · `test_ontology.py` 35건 · `test_result_chat.py` — 160건 통과.
- 전체 회귀: 아래 검토 보고서 §00-G 참조.
- 검토 보고서 renewal: `docs/review_2026-10-13_알레르겐_환경관리_교과서반영_검토보고서.html` — §00 에 이전 판 대비 표, 성분 역치 표(검토 E), 신규·갱신 조치 12건(검토 F), 쟁점 6건(검토 G), 기전(검토 H), 발생 배경(검토 I), 이전 판 전용 조치 16건(검토 J). 체크 키 `m:`·`c:`·`mech:`·`etio:`·`ed:`·`dis:12~18` 은 `scripts/review_environment_control.py --import` 와 호환(`m:` 만 승인 처리, 나머지는 메모로 돌려줌).

## 6. 남은 과제

- 검토 보고서 §00-H 의 **이전 판 전용 조치 16개** 유지·수정·삭제 결정 → `scripts/review_environment_control.py --revise/--reject` 반영.
- 책 제목·판차·출판사 확인 → `sources.kaaaci_textbook_env`·`kaaaci_textbook_ch5_env` 서지 보완.
- 미세먼지·황사·오존 **예보 연동**(에어코리아·기상청) → 리포트 '오늘의 주의' 블록. 현재는 질문에 답하는 일반 지식만 있다.
- 악화 요인 조치 12건도 알레르겐군 조치와 같은 검토·승인 워크플로(`--approve`/`--import`) 대상이다(모두 `candidate`).
