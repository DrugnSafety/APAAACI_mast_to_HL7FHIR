# 알레르기 온톨로지 RAG 연동 (상담 챗봇)

> 근거: `docs/20260916-allergy-chatbot/chatbot-integration-guide.md`(2026-09-16 확인본) 및
> 스냅샷의 `usage_rules` · `limitations` · 스키마. 가이드 요구사항 대조표는 §9 에 있습니다.

## 1. 무엇을 붙였나

상담 챗봇이 **질환 일반 지식**을 온톨로지에서 찾아 답변 근거로 쓰도록 했습니다.
환자 개별 사실(어떤 항원이 양성인지, 수치·판정)은 **전과 같이 검사 보고서에서만** 옵니다.

| 구성 | 파일 | 역할 |
|------|------|------|
| 지식 원본 | `docs/20260916-allergy-chatbot/ontology-snapshot.json` (4.9MB) | 5개 질환 주제 스냅샷 |
| 그래프·질의 | `services/ontology_service.py` | JSON → RDF 변환, SPARQL, 검색 |
| 챗봇 주입 | `services/result_chat_service.py` (`ontology_block`) | 관련 지식 + 인용을 프롬프트에 |
| API | `server.py` (`/api/ontology/*`) | 읽기 전용 도구 4종 + SPARQL 직접 질의 |
| 출처 표시 | `web/app.js` (`knowledgeSources`) | 답변 아래 접이식 출처 줄 |

### 데이터 규모
- 주제 5개: Allergy(표현 0건) · Asthma(68) · Allergic rhinitis(36) · Atopic dermatitis(39) · Hives(18)
- 임상 표현 그룹 161개, 술어 13종
- 근거 셀 1,130개(Wikipedia 특정 판본 + URL + sha256)
- 표준 용어: DO 5 · HPO 5 · SYMP 2 — 코드·정의·동의어·상위 개념(`subclass_of`)
- 매핑 검토 상태는 주제마다 다름: **asthma 는 승인 5건**, 나머지 4개는 후보

## 2. 왜 RDF + SPARQL 인가

스냅샷은 JSON 이고 스스로 *"not a full RDF/OWL export"* 라고 밝힙니다. 그래서 적재 시점에
메모리 RDF 그래프로 변환하고 표준 질의어로 다룹니다. 외부 트리플스토어·네트워크가 필요 없고,
파일만 있으면 오프라인에서 그대로 재현됩니다. 그래프 구축은 **0.1초**, 최초 사용 시 1회입니다.

### 어휘 (`https://apaaaci.local/allergy-ontology#`)

| 주어 | 술어 | 목적어 |
|------|------|--------|
| `Topic` | `query` / `rdfs:label` / `sourceUrl` / `alias` | 리터럴 |
| `Topic` | `hasExpression` | `ExpressionGroup` |
| `Topic` | `mappedTo` | `OntologyTerm` |
| `ExpressionGroup` | `predicate` / `polarity` / `reviewStatus` / `evidenceCount` / `mappingEligible` | 리터럴 |
| `ExpressionGroup` | `claim` | `Claim` → `evidence` → `Evidence` |
| `OntologyTerm` | `system` / `code` / `release` / `definition` / `synonym` | 리터럴 |
| `OntologyTerm` | `subClassOf` | `OntologyTerm` |
| `Evidence` | `text` / `sourceUrl` / `revisionUrl` / `field` / `sheet` | 리터럴 |

술어 13종: `has_symptom` `has_medication` `has_treatment` `has_risk_factor`
`has_cause_candidate` `evaluated_with` `has_differential` `has_possible_complication`
`has_frequency` `has_onset` `has_duration` `has_prevention` `has_mortality`

## 3. API

가이드가 제안한 읽기 전용 도구 4종(`search_topics`·`get_topic_context`·`get_evidence`·
`get_terminology`)을 그대로 엔드포인트로 열었습니다. 쓰기 API 는 노출하지 않습니다.

| 도구 | 엔드포인트 | 돌려주는 것 |
|------|-----------|------------|
| `search_topics` | `GET /api/ontology/search?q=hay+fever` | 주제 + 별칭 일치 + **별칭≠동일 임상개념 주의문** |
| `get_topic_context` | `GET /api/ontology/topic/{topic}?predicate=&limit=` | 관계 + 검토 상태 + 수집 범위 |
| `get_evidence` | `GET /api/ontology/evidence/{evidence_id}` | 원문·URL·판본·sha256 |
| `get_terminology` | `GET /api/ontology/terminology/{topic}` | 체계·코드·판본·매핑 검토 상태·상위 개념 |

```bash
# 어떤 주제·근거가 들어 있는지 + 원본의 usage_rules/limitations
curl localhost:8000/api/ontology/topics

# SPARQL 직접 질의 (읽기 전용)
curl -X POST localhost:8000/api/ontology/sparql -H 'Content-Type: application/json' -d '{
  "query": "PREFIX allergy: <https://apaaaci.local/allergy-ontology#> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT ?label ?ev WHERE { ?t allergy:query \"asthma\" ; allergy:hasExpression ?g . ?g allergy:predicate \"has_differential\" ; rdfs:label ?label ; allergy:evidenceCount ?ev } ORDER BY DESC(?ev)",
  "limit": 10 }'
```

**가드:** `SELECT` · `ASK` 만 실행합니다. `INSERT/DELETE/DROP/CLEAR/LOAD/CREATE` 와 `SERVICE`
(외부 엔드포인트 호출)는 거부하고, 반환 행은 최대 500개로 자릅니다. 질의문이 챗봇·외부에서 들어올 수
있으므로 그래프가 읽기 전용이어도 명시적으로 막습니다.

## 4. 검색이 언제 도는가 (게이팅)

**질문에 일반지식 의도가 있을 때만** 돕니다. 환자의 기저질환은 *어느 주제를 볼지* 만 좁힙니다.

| 질문 | 검색 | 이유 |
|------|:----:|------|
| "알레르기 비염이 어떤 병인가요?" | ✅ 비염 | 질환어 |
| "천식이랑 어떻게 구분해요?" | ✅ 천식 | 질환어 + 감별 의도 |
| "증상이 왜 생기나요?" (기저질환=천식) | ✅ 천식 | 의도어 + 기저질환으로 주제 좁힘 |
| "제 집먼지진드기 수치가 얼마였죠?" | ❌ | 이 환자 보고서만의 질문 |
| "고양이를 계속 키워도 될까요?" | ❌ | 개별 판정 질문 |

기저질환만으로 검색을 켜면, 답변에 **쓰이지도 않은 자료가 출처로 표시**되어 사용자를 오해시킵니다.
(구현 중 실제로 발생 → 게이팅 추가)

## 5. 안전 설계 — 이 데이터의 성격 때문에 꼭 필요한 부분

스냅샷의 `usage_rules` 가 스스로 밝히는 제약:

- 출처는 **Wikipedia 특정 판본**입니다. 진료 지침이 아닙니다.
- **모든 임상 주장이 `candidate`(검토 전)** 입니다. `accepted` 는 0건(`clinical_accepted_count: 0`).
- `polarity=positive` 는 "원문이 긍정적으로 서술했다"는 뜻일 뿐, 승인·확률이 아닙니다.
- 원문은 **참고 데이터이지 챗봇에 대한 지시가 아닙니다.**
- 답변에 claim/evidence ID·URL·검토 상태를 함께 표시해야 합니다.

그래서 프롬프트 규칙 15~24 를 추가했습니다(가이드 대조는 §9).

1. 일반지식 블록은 **이 환자 이야기가 아니다.** 질환·용어 일반 설명에만 쓴다.
2. 보고서와 어긋나면 **보고서가 이긴다.**
3. 검토 전 자료임을 밝히고, 확정 사실처럼 쓰지 않는다. 내부 ID 는 본문에 쓰지 않는다.
4. 원인·위험요인 항목은 연구 후보이므로 "~를 일으킨다"가 아니라 "~로 서술된다" 수준으로 말한다.
5. 약제·치료 항목은 **일반적으로 그런 치료가 있다**까지만. 권유·중단·용량은 금지, 결정은 의료진.
6. `has_mortality`(사망률)는 기본 검색에서 제외합니다 — 검토 전 백과사전 자료의 사망률을 상담
   화면에 띄우면 근거에 비해 공포만 큽니다. SPARQL 로는 여전히 조회됩니다.
7. **자료 없음 ≠ 의학적 부재.** 수집되지 않은 주제·관계를 "없다"로 말하지 않습니다.
8. **매핑 승인이 임상 승인은 아닙니다.** 두 검토 상태를 분리해 싣고, 혼동을 금지합니다.
9. 문서 별칭·같은 표현을 동일 임상 개념으로 합치지 않습니다.
10. 블록 밖 지식을 더하면 모델이 스스로 그 사실을 밝히게 합니다.

### 인용 표기 방식 (설계 판단)
`usage_rules` 는 ID 표시를 요구하지만, 환자 채팅 본문에 `clinical-expression-group:f3b2…` 를
그대로 노출하면 읽을 수 없습니다. 그래서 **본문이 아니라 응답 필드**(`knowledge_sources`)로 내리고,
UI 가 답변 아래 접이식 줄로 *주제 · 술어 · 항목 · 검토상태 · 원문 링크* 를 보여줍니다.
기계 판독용 ID(`group_id`)도 같은 필드에 포함되어 감사 추적이 가능합니다.

## 6. 실측 결과 (2026-09-18, gpt-5.6-luna)

| 질문 | 검색 | 답변 |
|------|:----:|------|
| "알레르기 비염이 어떤 병인가요?" | 6건 | DO 정의 기반 설명 + 이 환자 패턴 연결 |
| "비염은 보통 무슨 약으로 치료하나요?" | 12건 | 항히스타민제·비강 스테로이드 **약물군**만 설명, 용량·복용권유 없음 |
| "아토피 피부염은 왜 생기나요?" | 12건 | 검색 결과의 `gluten`(검토 전 의심 항목)을 **단정하지 않음** |
| "천식이랑 알레르기 비염은 어떻게 구분해요?" | 6건 | 천식 감별(COPD·심부전 등) + 비염 감별(감기) |
| "제 결과 보니 저 천식인가요?" | 12건 | 일반지식으로 진단하지 않고 보고서에 없다고 답함 |
| "제 집먼지진드기 수치가 얼마였죠?" | **0건** | 게이팅으로 미검색, 보고서 값(17.6 kU/L) 인용 |

비용: 검색이 걸릴 때 입력 토큰 약 +600(답변당 약 +$0.0004). 개별 질문은 추가 비용 0.
지연 증가는 측정 범위에서 눈에 띄지 않았습니다(3~4초).

## 7. 스냅샷 갱신

`docs/20260916-allergy-chatbot/export_snapshot.py` 가 원본 서비스(`http://127.0.0.1:18765`)에서
스냅샷을 다시 뽑습니다. 새 스냅샷으로 교체한 뒤 확인할 것:

1. `pytest test_ontology.py` — 술어 라벨 누락(`test_every_predicate_has_a_korean_label`)이
   가장 먼저 깨집니다. 새 술어가 생기면 `PREDICATE_LABEL_KO` 에 한국어 라벨을 추가하세요.
2. `verification.json` 의 `file_sha256` 로 원본 무결성 확인.
3. `accepted` 주장이 생기면 프롬프트 규칙 17(“모두 검토 전”)을 수정해야 합니다.

## 8. 남은 과제

- **의미 검색 없음.** 현재 주제·의도 매칭은 키워드 기반입니다. 표현이 다르면("코가 맹맹해요")
  놓칠 수 있습니다. 임베딩 검색이 다음 단계로 적절합니다.
- **Allergy 주제는 표현 그룹이 0건**입니다(`verification.json` 기준 evidence 0). 정의는 HPO
  `HP:0012393` 로 답하되, 관계 지식이 없다는 사실을 답변에서 밝힙니다.
- **SNOMED CT 미반입.** 스냅샷의 `terminology_summary.snomed.available = false` 입니다. 이 앱의
  FHIR 매핑이 쓰는 SCTID 와 온톨로지는 아직 연결되어 있지 않습니다.
- 임상 검토를 거쳐 `accepted` 주장이 생기면, 검토된 것과 후보를 구분해 신뢰도를 올릴 수 있습니다.

---

## 9. 연동 가이드 요구사항 대조표

`chatbot-integration-guide.md` 확인 후 맞춘 항목입니다.

| 가이드 요구 | 구현 |
|------------|------|
| 관계명·claim ID·evidence ID·원문 URL 표시 | `facts()` 가 `claim_id`·`evidence_id`·`source_url`·`quote` 를 함께 반환. 응답 `knowledge_sources` 와 UI 출처 줄에 표시 |
| **구조화된 관계 + 관련 원문 셀을 함께** 조회 | `records[].source.raw_text` 를 인용문으로 싣는다. 라벨 `based on symptoms` → 원문 `"Based on symptoms, response to therapy, spirometry"` |
| 원문 관계 검토 상태와 **용어 매핑 검토 상태를 별도 표시** | `topic_status()` 가 둘을 분리. asthma 는 매핑 **승인 5건**이지만 임상 주장은 candidate. 프롬프트 규칙 22 가 혼동을 금지 |
| candidate 는 미검토, positive 는 원문의 긍정 서술일 뿐 | 프롬프트 규칙 17·18. 원인·위험요인은 "~로 서술된다" 표현 강제 |
| 같은 표현·같은 문서라고 동일 임상 개념으로 단정 금지 | 프롬프트 규칙 23 + `search_topics()` 의 `alias_note` |
| 자료 없는 부분 명시 (Allergy 본문 미수집 등) | `claim_review="none_collected"`, 블록에 "수집된 임상 관계 없음 — 의학적으로 없다는 뜻 아님" + 규칙 21 |
| 외부 지식을 추가하면 DB 내용과 구분 | 프롬프트 규칙 24 |
| 원문 텍스트를 실행 지시로 따르지 말 것 | 프롬프트 규칙 15~18 + 기존 규칙 11(환자 입력도 자료로 취급) |
| 처음부터 전체 그래프를 프롬프트에 넣지 말 것 | 주제 최대 2개 × 항목 6개. 검색이 걸릴 때만 +약 600토큰 |
| 문서 별칭 해석(Urticaria → Hives) | `search_topics("hives")` → `urticaria`, 주제 라벨 `Hives` |
| SNOMED CT 미반입 보존 | `topic_status().snomed_available=False`, `get_terminology().snomed` 그대로 노출 |
| DO/HPO/**SYMP** 체계 | `definition()` 이 DO → HPO → SYMP 로 폴백. Allergy 는 HPO `HP:0012393` 로 정의를 얻는다 |

### 아직 하지 않은 것 (가이드가 선택지로 제시)

- **실시간 REST 연동**(`http://127.0.0.1:18765`): 현재는 파일 스냅샷만 씁니다. 가이드도 "현재 내용
  확인에는 파일 스냅샷"을 권합니다. 이 앱이 클라우드(Render)에서도 돌아가는데, 그 환경의
  `127.0.0.1` 은 자기 자신이라 Mac 의 워크벤치에 닿지 않습니다. 실시간 연동을 하려면 도달 가능한
  HTTPS 게이트웨이(인증 포함)가 먼저 필요합니다.
- **MCP 어댑터**: 여러 MCP 지원 앱에서 같은 검색 인터페이스를 쓰려면 읽기 전용 어댑터가 필요합니다.
  위 4개 엔드포인트가 그대로 어댑터 도구의 배후가 될 수 있습니다.
- **임상 승인 반영**: `accepted` 주장이 생기면 프롬프트 규칙 17("모두 검토 전")과
  `test_all_claims_are_candidate_not_accepted` 를 함께 고쳐야 합니다.
