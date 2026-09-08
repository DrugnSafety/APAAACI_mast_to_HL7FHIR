# 2026-09-08 고도화: FHIR Observation 정밀화 + 듀얼 UI(탐험 퀘스트 / 클래식) 동시 운영

> **요약** — ① FHIR Observation 이 이제 **0·검출한계 미만(<LoD)·N/A 결과도 빠짐없이** 담고, `code`/`method` 에는 **공식 SNOMED CT 서버로 존재·활성을 검증한 코드**만 쓴다(기존 2개 코드는 잘못된 코드였음). ② 게임화 UI(알러젠 탐험 퀘스트)와 재설계 이전 클래식 UI를 **같은 서버에서 동시에** 제공하며 헤더 링크로 서로 전환한다. 두 UI 는 같은 `/api` 를 쓰므로 판정·리포트·FHIR 결과는 완전히 동일하다.
>
> English version: [`release_2026-09-08_fhir_dual_ui.en.md`](release_2026-09-08_fhir_dual_ui.en.md)

| 항목 | 값 |
|---|---|
| 브랜치 / 커밋 | `claude/allergy-test-report-x4lgqh` · `42aa3be`(FHIR) · `3bf4ea4`(듀얼 UI) |
| PR | [#1](https://github.com/DrugnSafety/APAAACI_mast_to_HL7FHIR/pull/1) |
| 검증 | 회귀 26종 통과(`python3 test_relevance_engine.py`), 게임 로직 9종(`node --test web/game.test.js`), 두 UI 데모 흐름 실브라우저 캡처 |

---

## 1. FHIR Observation 정밀화

### 1-1. 무엇이 문제였나
- **값이 없는 결과가 누락될 수 있었다.** MAST/UniCAP 에서 수치가 `None` 이면 `valueQuantity` 없이 Observation 이 만들어졐고(FHIR 규칙 위반: value 또는 dataAbsentReason 중 하나는 있어야 함), 보고서의 `<0.35`·`N/A`·`undetectable` 같은 표기는 숫자 변환에 실패해 **원문이 버려졌다**.
- **SNOMED CT 코드 2개가 잘못되어 있었다.**
  - SPT 의 `398166005` 는 "Skin prick test" 가 아니라 **"Performed (qualifier value)"** 다.
  - MAST/UniCAP 의 `165967004` 는 **SNOMED CT International 에 존재하지 않는 코드**다.
- MAST/UniCAP 에 `Observation.method`(검사 기법) 가 없었다.

### 1-2. 코드 검증 방법과 결과
모든 SCTID 는 HL7 공식 터미널로지 서버 **tx.fhir.org** 의 `CodeSystem/$lookup` 으로 확인했다(SNOMED CT International Edition **2025-02-01**, `property=inactive`). 외부 AI 가 제안한 코드도 같은 방법으로 재검증했고, 일부는 **틀린 것으로 판명**되어 채택하지 않았다.

| 용도 | 채택한 코드 | tx.fhir.org display | 활성 | 비고 |
|---|---|---|---|---|
| SPT `Observation.code` | **37968009** | Prick test (procedure) | ✔ | 구 `398166005`("Performed") 교체 |
| MAST `Observation.code` | **399788006** | Allergen specific IgE antibody measurement, MAST type | ✔ | 구 `165967004`(미존재) 교체 |
| UniCAP `Observation.code` | **397691009** | Allergen specific IgE antibody measurement, quantitative | ✔ | ImmunoCAP 은 정량 특이 IgE |
| MAST `Observation.method` | **703446000** | Immunoblot assay (qualifier value) | ✔ | Technique(272394005) 하위. MAST(AlloScreen 등)는 면역블롯 기반 |
| UniCAP `Observation.method` 1순위 | **703447009** | Enzyme immunoassay technique (qualifier value) | ✔ | FEIA 는 효소면역측정의 한 형태 |
| UniCAP `Observation.method` 2순위 | **703444002** | Immunofluorescence technique (qualifier value) | ✔ | 형광 판독 관점. 제안된 명칭 "Fluorescent immunoassay technique" 은 실제 FSN 이 아님 |
| SPT `Observation.method` | OMOP concept `36703747` (Athena) + 텍스트 | Skin prick test histamine positive control | — | SNOMED Technique 계층에 prick 전용 코드가 없어(검색 0건) 기존 CDM 코딩 유지 |

**검증 과정에서 기각한 제안**

| 제안 | 실제 | 판단 |
|---|---|---|
| LOINC `32614-0` "Allergen D1 Specific IgE Ab … by Fluoenzymeimmunoassay" | NLM Clinical Tables: **Glutamate [Moles/volume] in Body fluid** | 기각 |
| LOINC `62464-3` "Allergen IgE Ab panel in Serum by Immunoblot" | NLM Clinical Tables: **Enterovirus RNA … by NAA with probe detection** | 기각 |
| `703444002` = "Fluorescent immunoassay technique" | 실제 display: **Immunofluorescence technique** | 코드는 활성이므로 2순위로만 채택, 명칭 교정 |
| `37310002` Immunoblot assay (procedure) | 활성이지만 **procedure** 계층 | `method` 에는 technique(qualifier) 가 맞으므로 미채택 |

LOINC 는 알러젠별로 코드가 분리되어 있어(예: 112129-2 American house dust mite IgE Ab … by Immunoassay) 항원별 매핑 표가 필요하다. 이번 라운드에서는 `Observation.code` 를 SNOMED 절차 코드로 통일하고, 항원 식별은 기존처럼 **CDM(OMOP) SNOMED 기매핑 `component`** 로 유지했다. LOINC 항원별 매핑은 후속 과제.

### 1-3. 값 표현 규칙 (`services/fhir_service.py`)

| 보고서 표기 | FHIR 표현 | 예 |
|---|---|---|
| 숫자 (0 포함) | `valueQuantity` | `0.0 kU/L`, `17.6 kU/L` |
| `<0.35`, `< 0.10` | `valueQuantity` + `comparator: "<"` | `{"comparator":"<","value":0.35}` |
| `undetectable`, `ND`, `검출안됨` | `comparator "<"` + 검출한계 **0.35 kU/L** + note | 특이 IgE class 0/1 경계 |
| `N/A`, 빈값 | `dataAbsentReason` (`not-performed`, 그 외 문자열은 `unknown` + text) | 값 없이 |
| SPT 팽진 없음 | `dataAbsentReason` | size_text·mean_mm 모두 없음 |
| MAST/UniCAP class (0–6) | `component` `valueInteger` | class 0 도 보존 |

원문 보존을 위해 `AllergenResult.value_text` 필드를 추가했고, OCR 파서는 숫자 변환에 실패한 값을 이 필드에 그대로 남긴다(결과를 버리지 않음).

### 1-4. 실제 출력 예시
데모 데이터(MAST, 6항목 중 음성 1)를 두 UI 에서 각각 실행해 FHIR 탭에서 확인한 결과다. 두 UI 의 JSON 은 동일하다.

![탐험 퀘스트 UI · FHIR 탭 Observation](screenshots/2026-09-08/quest-05-fhir-observation.png)
*탐험 퀘스트 UI — `code` 399788006(MAST type), `method` 703446000(Immunoblot assay). Observation 6 = 양성 5 + 음성 1(개 비듬 0.1 kU/L, class 0).*

![클래식 UI · FHIR 탭 Observation](screenshots/2026-09-08/classic-05-fhir.png)
*클래식 UI — 같은 API 이므로 같은 번들.*

전체 샘플(MAST 4행: 양성·0·`<0.35`·`N/A`, UniCAP, SPT 2행): [`screenshots/2026-09-08/fhir_observation_samples.json`](screenshots/2026-09-08/fhir_observation_samples.json)

```json
{ "code": {"coding": [{"system": "http://snomed.info/sct", "code": "399788006",
             "display": "Allergen specific IgE antibody measurement, MAST type"}]},
  "method": {"coding": [{"system": "http://snomed.info/sct", "code": "703446000",
             "display": "Immunoblot assay (qualifier value)"}], "text": "Immunoblot (MAST)"},
  "valueQuantity": {"value": 0.35, "comparator": "<", "unit": "kU/L",
                    "system": "http://unitsofmeasure.org", "code": "kU/L"},
  "interpretation": [{"coding": [{"code": "NEG"}]}],
  "component": [{"code": {"text": "IgE class (0-6, report semi-quantitative class)"}, "valueInteger": 0}] }
```

### 1-5. 검사 종류별 입력 알고리즘 (검토 결과)
| 검사 | 입력 필드 | 양성 판정 | 강도(별) | Observation |
|---|---|---|---|---|
| SPT | `size_text`(장×단) → `mean_mm`, `wheal_major/minor`, 히스타민 대조 | 평균 ≥ 3 mm 또는 히스타민의 50% 이상 | 3–5 / 5–8 / ≥8 mm | code 37968009, value = 평균 mm, component 장축·단축·평균·A/H(CDM qualifier) |
| MAST | `value`(kU/L 또는 IU/mL), `class`(0–6) | class ≥ 1 또는 ≥ 0.35 kU/L | class 1–2 / 3–4 / 5–6 | code 399788006, method 703446000, class component |
| UniCAP | `value`(kU/L), `class` | MAST 와 동일 | 동일 | code 397691009, method 703447009 + 703444002 |

OCR 프롬프트(`instructions/OCR_prompt.md`)와 파서(`services/ocr_service.py`)는 Total IgE 요약행만 제외하고 **모든 행을 보존**한다. 프론트엔드 OCR 검토 표의 "수치 0 항목" 탭에 있는 행도 FHIR 로 나간다.

---

## 2. 듀얼 UI 동시 운영

| | 알러젠 탐험 퀘스트 UI | 클래식 UI |
|---|---|---|
| 경로 | `/` | `/classic/` |
| 소스 | `web/index.html`, `web/app.js`, `web/game.js`, `web/styles.css` | `web/classic/*` (커밋 `d5eb5f4` 시점 UI 를 그대로 보존, 자산 경로만 `/classic/` 로) |
| 전환 | 헤더 **「클래식 UI」** 링크 | 헤더 **「🧭 탐험 퀘스트 UI」** 링크 |
| 데이터 | 동일 `/api/*`, 동일 판정·리포트·카드뉴스·FHIR | 동일 |
| 확인 | `GET /api/health` → `build.features.ui_modes = ["quest","classic"]` | |

서버(`server.py`)는 `/classic` 을 먼저 마운트하고 `/` 를 마지막에 마운트한다. 클래식 UI 는 유지보수 대상이 아니라 **비교·회귀 기준선**이며, 환자 피드백에 따라 어느 쪽을 기본으로 둘지 결정한다.

### 화면 비교 (같은 데모 데이터)

| 단계 | 탐험 퀘스트 UI | 클래식 UI |
|---|---|---|
| 시작 | ![](screenshots/2026-09-08/quest-00-upload.png) | ![](screenshots/2026-09-08/classic-00-upload.png) |
| 발견 / OCR 검토 | ![](screenshots/2026-09-08/quest-02-discover.png) | ![](screenshots/2026-09-08/classic-01-review.png) |
| 문진 | ![](screenshots/2026-09-08/quest-03-questionnaire.png) | ![](screenshots/2026-09-08/classic-03-questionnaire.png) |
| 결과 | ![](screenshots/2026-09-08/quest-04-dex.png) | ![](screenshots/2026-09-08/classic-04-results.png) |

다크 모드·모바일(탐험 퀘스트 UI): `screenshots/2026-09-08/quest-dark-dex.png`, `quest-mobile-dex.png`

---

## 3. 실행 방법
```bash
uvicorn server:app --port 8787      # 8000 이 사용 중이면 다른 포트
# 탐험 퀘스트 UI  http://127.0.0.1:8787/
# 클래식 UI       http://127.0.0.1:8787/classic/
python3 test_relevance_engine.py    # 26종
node --test web/game.test.js        # 9종
```

## 4. 남은 과제
- LOINC 항원별 코드(예: 112129-2) 매핑 표 → `Observation.code` 에 LOINC 병기.
- `interpretation` 에 `<`(Off scale low) 추가 여부 검토(현재 NEG 유지).
- 클래식 UI 의 기본/보조 여부 결정(환자 피드백).
