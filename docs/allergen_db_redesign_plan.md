# 알레르겐 DB 재설계·구조화 고도화 계획

> 목적: "양성으로 나온 각 항원마다, 자기 자신뿐 아니라 **항원성이 유사한(교차반응 가능) 항원**까지 함께 문진"이
> 가능하도록 알레르겐 데이터를 재구조화한다. 향후 오류/누락은 **코드 하드코딩이 아니라 구조화된 리스트에 항목을 추가**하는 방식으로 해결한다.
>
> 이 문서는 **설계안**이다. (실제 마이그레이션 코드는 승인 후 별도 진행)
>
> **업데이트**: 성분(component) 계층의 임상 근거·CRD 2단계 모델·데이터 소스(allergen.org/EAACI MAUG 2.0)
> 활용은 [`crd_cross_reactivity_research.md`](./crd_cross_reactivity_research.md) 참조. 핵심 보정: 교차반응은
> 서열 상동성으로 자동 판정 불가 → **성분 계층은 "문진 후보 생성"용, 임상 교차반응은 증상으로 확정.**

---

## 1. 문제 진단 (근거)

현재 알레르겐 지식은 **4개의 분리된 JSON 사일로**에 흩어져 있고, 서로 키·정보가 중복되며, 교차반응은 **기전별로 하드코딩**되어 있다.

| 파일 | 역할 | 항원 수 | 핵심 문제 |
|---|---|---|---|
| `data/allergen_knowledge_base.json` | 임상 backdata + rubric | 18 | 교차반응이 **자유텍스트**(`cross_reactivity_ko`) — 기계가 못 씀 |
| `instructions/allergen_map_prompt_v2.json` | OCR 정규화·분류·SNOMED | 121 | **category='Other'가 56종(46%)** |
| `data/cdm_snomed_mapping.json` | OMOP/SNOMED 코드 | 155 | korean 이름이 `source_value` 괄호 안에 비구조적 |
| `data/pollen_food_cross_reactivity.json` | 교차반응 | — | **꽃가루-음식 + 진드기-갑각류 특수케이스만** |

### 1.1 결정적 증거 — 음식/교차반응 항원이 대거 `Other`로 오분류
> Apple, **Celery**, Carrot, Peach, Almond, Peanut, Cod, **Lobster, Mussel, Oyster**, **Latex**, Banana, Melon, Kiwi …

→ 이 항원들은 `_cat(a)=='food'` 필터를 통과하지 못해 **음식 감별 문진 섹션에 아예 들어오지 않는다.**

### 1.2 세 가지 구조적 결함

1. **교차반응이 일반화 불가.** `pollen_food`는 dict 키가 반드시 *꽃가루 항원*이어야 조회된다(`pfas_foods_for(canonical)`). `mite_shellfish`는 키 없는 단일 특수 블록. → 새우↔게(둘 다 tropomyosin), 셀러리↔당근·향신료 같은 **음식이 트리거인 교차반응**을 담을 스키마가 없다. `is_shellfish()`는 문자열 부분매칭 특수함수.

2. **카테고리 분류가 이름 매칭에 의존해 취약.** `Dog dander`→animal 은 되지만 `Dog hair`·`Horse dander`·`Feline`(매핑 실패 시)은 `other`로 떨어져 **동물 감별 문진 섹션이 아예 생성되지 않는다**(사용자가 겪은 버그의 근본 원인). 카테고리 표기도 파일마다 다름(KB `pollen_tree` 소문자 vs prompt `Pollen`+subcategory `Tree`).

3. **코드가 이원화·상충 위험.** `prompt_v2.snomed`와 `cdm.concept_id`가 동일 항원에 서로 다른 코드체계를 담아, 매핑 유무에 따라 같은 항원이 다른 FHIR coding을 낼 수 있다.

### 1.3 근본 원인 한 줄 요약
> **교차반응과 카테고리를 "데이터 관계"가 아니라 "코드 분기(if/특수함수/하드코딩 dict)"로 표현**하고 있어, 새 사례가 나올 때마다 코드를 고쳐야 한다.

---

## 2. 설계 원칙

1. **단일 진실원(Single Source of Truth).** 흩어진 4파일을 항원(Antigen) 레코드 하나로 병합. 코드·이름·카테고리·성분·backdata가 한 곳에.
2. **교차반응 = 공유 분자성분에서 파생.** 항원을 **분자 알레르겐 성분(protein family)** 에 소속시키고, 교차반응은 "같은 성분을 공유하는 항원"으로 **런타임 파생**. 방향 무관(대칭) → 꽃가루→음식뿐 아니라 음식↔음식도 같은 규칙으로 해결.
3. **데이터-코드 분리.** 문진 생성·회피 안내·FHIR 코딩은 데이터를 순회하는 **범용 엔진** 하나로. 새 항원·교차반응·카테고리는 **리스트에 한 줄 추가**로 반영(코드 무수정).
4. **점진 이행.** 기존 서비스 API(`knowledge_service`, `allergen_mapper`, `questionnaire_service`)의 시그니처를 유지하고 내부 데이터원만 교체 → 한 소비자씩 안전하게 이행.
5. **저장은 JSON 인접리스트.** 규모(항원 ~300, 성분 ~20, 소속엣지 ~600)가 작고 질의 깊이가 얕아 **별도 graphDB 불필요**. git diff로 임상팀이 PR 리뷰 가능. 필요 시 SQLite 승격 경로 열어둠.

> 이 원칙은 독립적으로 생성한 4개 재설계안이 **모두 수렴**한 결론이다(§6). 채점에서 확장성이 가장 높게(3.75/5), 마이그레이션 공수가 유일한 주요 리스크로 평가됨.

---

## 3. 목표 데이터 모델

3개 파일로 정규화. (현재 4개 사일로 → 항원/성분/명시엣지)

### 3.1 `data/allergens.json` — 항원 레지스트리 (단일 진실원)
```jsonc
{
  "id": "shrimp",                        // 안정 슬러그(주키) — 이름이 바뀌어도 불변
  "canonical_name": "Shrimp",
  "korean_name": "새우",
  "aliases": ["prawn", "새우", "대하"],
  "ocr_aliases": ["shrlmp"],
  "category": "food",                    // 정규 enum(명시 필드) — 이름매칭 의존 제거
  "subcategory": "shellfish",            // is_shellfish() 하드코딩 대체
  "components": ["tropomyosin"],         // ★ 교차반응의 원천
  "coding": { "omop_concept_id": "4150599", "snomed": null, "vocabulary": "SNOMED" },
  "kb_ref": "shrimp",                    // 심층 backdata(선택) 참조
  "ecology": { "seasonality_pattern": "perennial", "indoor_outdoor": null }
}
```
정규 `category` enum: `mite · animal · pollen_tree · pollen_grass · pollen_weed · mold · insect · food · latex · drug · other`

### 3.2 `data/allergen_components.json` — 분자 성분(단백질 family) 카탈로그
```jsonc
{
  "id": "tropomyosin",
  "name_ko": "트로포마이오신",
  "family": "Tropomyosin",
  "heat_stable": true,                   // 가열해도 잔존 → 조리해도 반응
  "clinical_risk": "systemic",           // 문진/severity 게이팅에 사용 (oral | systemic)
  "note_ko": "무척추동물 pan-allergen. 갑각류·연체류·집먼지진드기·바퀴가 공유.",
  "mechanism": "invertebrate_pan_allergen"
}
```

### 3.3 `data/cross_reactions.json` — 명시적 교차반응 엣지 (성분으로 못 담는 경험적 관계만)
```jsonc
{ "a": "celery", "b": "carrot", "basis": "empirical:celery-mugwort-spice",
  "clinical_risk": "variable", "note_ko": "셀러리-쑥-향신료 증후군" }
```
> 대부분의 교차반응은 §3.2 성분 공유로 **자동 파생**되므로 이 파일은 **예외·경험적 관계만** 담는다(라텍스-과일 등).

### 3.4 성분 계열 seed (도메인 지식 — 이 표가 교차반응 엔진의 핵심 자산)

| 성분(component) | 열안정 | 위험 | 소속 항원(대표) |
|---|---|---|---|
| **Tropomyosin** | ✅ | systemic | 새우·게·랍스터·오징어·조개·굴·홍합, **집먼지진드기(Der p10)**, 바퀴 |
| **PR-10 (Bet v 1)** | ❌ | oral(OAS) | 자작·오리·개암·참나무 ↔ 사과·복숭아·배·체리·헤이즐넛·당근·셀러리·콩·키위 |
| **Profilin (Bet v 2)** | ❌ | oral | 화본과·자작·쑥 ↔ 멜론·수박·바나나·감귤·토마토 |
| **LTP (Pru p 3)** | ✅ | systemic | 복숭아·사과·호두·헤이즐넛·땅콩·옥수수 (지중해형) |
| **Parvalbumin** | ✅ | systemic | 대구·연어·고등어·참치·잉어 (생선 pan-allergen) |
| **Serum albumin** | ❌ | 다양 | 고양이(Fel d2)·개(Can f3)·돼지·소고기·우유(BSA) — pork-cat/beef-milk |
| **Lipocalin** | — | — | 고양이(Fel d4)·개(Can f1/2)·말·소·설치류 (동물 비듬 major) |
| **Casein/whey** | ✅ | 다양 | 우유 ↔ 염소·양유 |
| **Omega-5 gliadin** | ✅ | systemic | 밀 (WDEIA) |
| **Art v 1** | — | oral | 쑥꽃가루 ↔ 셀러리·당근·향신료 |
| **Hevein(Hev b)** | — | 다양 | 라텍스 ↔ 바나나·아보카도·키위·밤 (latex-fruit) |

> **확장 원칙**: 새 교차반응군은 이 표에 성분 1행 + 항원의 `components`에 태그만 추가.

---

## 4. 교차반응 파생 규칙 + 문진 자동생성 (엔진)

### 4.1 교차반응 조회 (대칭·범용)
```
cross_reactants(X) = ∪_{c ∈ X.components} { Y ∈ members(c) : Y ≠ X }  ∪  explicit_edges(X)
```
성분→항원 **역인덱스**(`component_id → [antigen_id]`)를 1회 구축. 각 후보 Y에 **공유 성분·열안정·위험도** 메타를 첨부.

- `새우 양성` → tropomyosin 공유로 **게·랍스터·오징어·집먼지진드기** 자동 회수 → "게도 드셨을 때 괜찮았나요?" 문진 생성.
- `집먼지진드기 감작` → 같은 tropomyosin 규칙으로 **갑각류** 회수(현재 `mite_shellfish` 특수블록 불필요).
- `셀러리 양성` → PR-10·profilin·Art v 1 공유로 **당근·향신료·사과·쑥꽃가루** 회수.
- `자작나무 양성` → PR-10 공유로 **사과·복숭아…** (현재 pollen_food 유지, 단 일반 규칙으로 흡수).

### 4.2 문진 자동생성 (범용 알고리즘 하나로 교체)
```
for 각 양성 항원 X:
  (1) 자기 문진   = resolve_category(X) → 카테고리 섹션(노출·증상·계절)   # 동물 버그 해결
  (2) 교차 문진   = cross_reactants(X) 를 성분(mechanism)별 그룹핑
                    → 그룹당 1문항: "X와 교차반응 가능한 [게·랍스터…]를 드셨을 때 이상 없었나요?"
                    → 성분.clinical_risk 로 문항·severity 게이팅(oral→국소, systemic→전신 경고)
  이미 검사 양성으로 존재하는 co-member 는 dedup (현재 has_pos_shellfish 로직 일반화)
```
→ **이 한 알고리즘이 동물 문진 누락 버그와 음식 교차반응 요구를 동시에 해결한다.**

### 4.3 카테고리 강건화 (미분류 → 리스트 추가로 해결)
`resolve_category(raw, antigen)` 우선순위:
1. `antigen.category` 명시값(정규 enum) — **레지스트리에 등록되면 이름매칭 불필요**
2. `category_aliases` 정확일치(현 `CATEGORY_ALIASES` 확장, 예 `"dog hair":"animal"`)
3. `regex_patterns` 순차 매칭 — 예 `(hair|dander|epithelium|feather|serum)` → animal
4. `components` 힌트(tropomyosin 보유 & 비진드기 → food/shellfish)
5. `other` (+ 로그로 "미분류 신규 항원" 리포트 → 다음 PR에서 리스트 추가)

---

## 5. 마이그레이션 단계 (저위험·점진)

| 단계 | 내용 | 리스크 |
|---|---|---|
| **P0** | 빌드 스크립트로 4개 사일로 → `allergens.json` 병합(항원별 코드/이름/카테고리/kb_ref 통합). 기존 파일은 당분간 유지 | 낮음 (읽기 전용 생성) |
| **P1** | `allergen_components.json` + seed(§3.4) 작성, 항원에 `components` 태그. 회귀 테스트로 기존 pollen_food·mite_shellfish 결과 재현 검증 | 낮음 |
| **P2** | `knowledge_service`에 `cross_reactants()` + 역인덱스 추가. `pfas_foods_for`/`mite_shellfish`/`is_shellfish`를 **내부적으로 새 엔진에 위임**(시그니처 유지) | 중간 (동등성 테스트 필수) |
| **P3** | `questionnaire_service` 음식·동물 섹션을 §4.2 범용 알고리즘으로 교체. `POLLEN_GROUPS`/특수 if문 제거 | 중간 |
| **P4** | `resolve_category` 파이프라인으로 `normalize_category` 대체. 미분류 로그 활성화 | 낮음 |
| **P5** | 구 4파일 제거, `allergens.json` 단일화. FHIR/SNOMED coding은 `coding` 필드로 일원화 | 낮음 |

각 단계는 `test_relevance_engine.py`에 **동등성 회귀 테스트**(구·신 결과 일치)를 추가하며 진행.

---

## 6. 대안 비교 (독립 생성 4안 · 적대적 채점)

| 재설계안 | 저장 | 교차반응 표현 | 채점 총점(25) |
|---|---|---|---|
| **A. 통합 레지스트리 + 성분그래프** | 3파일 JSON(항원/성분/엣지) | 성분 역인덱스 파생(주) + 명시엣지(보) | 상위 |
| **B. 3테이블 엣지 그래프** | 단일 JSON(antigens/components/cross_reactions) | 성분 파생 + 명시 엣지 이중 | 상위 |
| **C. C3G 성분중심 그래프** | 단일 JSON, membership만 | 전량 런타임 파생(엣지 저장 안 함) | 중상 |
| **D. 성분-그룹 정션 + 강건 택소노미** | antigens.json + cross_groups.json | 그룹 멤버십(대칭) + 명시엣지 | 중상 |

- **공통 결론(4안 수렴)**: 성분 계층이 정답, JSON 인접리스트로 충분(graphDB 불필요), 카테고리는 명시필드+폴백으로 강건화, 문진은 범용 엔진 하나로.
- **채점 요약**: 확장성 avg 3.75(최고), FHIR 정합 3.42, 커버리지 3.33, 구현 2.67·마이그레이션 2.5(**주요 리스크 = 이행 공수**).
- **주요 리스크(판정단)**: (a) 구 결과와의 동등성 보장 필요 → 회귀 테스트로 완화, (b) 성분 seed의 임상 정확도 → 출처 명기·전문가 검수, (c) 코드 이원화 해소 시 FHIR 표시코드 드리프트 → `coding_source` 필드로 명시.

### 권고
**A와 B의 하이브리드** — 즉 **① 단일 항원 레지스트리(B의 단순 로딩)** + **② 성분 역인덱스 파생 교차반응(A/C)** + **③ 경험적 관계용 명시 엣지(B/D)** + **④ resolve_category 폴백 파이프라인(D)**. 저장은 단일~3파일 JSON, graphDB 미도입.

---

## 7. "리스트만 추가하면 되는" 확장 예시 (코드 0줄)

1. **셀러리 교차반응 추가**: `allergens.json`의 Celery에 `"components": ["pr10","profilin","art_v1"]` → 셀러리 양성 시 당근·향신료·사과·쑥꽃가루 문진 자동 생성.
2. **동물 문진 누락 수정**: `category_aliases`에 `"horse dander":"animal"`, `"dog hair":"animal"` 한 줄씩 → 동물 섹션 자동 편입.
3. **새 기전 추가(생선 parvalbumin)**: `allergen_components.json`에 `parvalbumin` 1행 + 대구·연어·고등어에 태그 → 생선 교차반응 문진 자동.
4. **라텍스-과일 증후군**: `cross_reactions.json`에 latex↔banana/avocado/kiwi/chestnut 엣지 추가.

---

## 8. 다음 액션
- [ ] 본 계획 승인 여부 확인 (특히 §6 권고안 A×B 하이브리드)
- [ ] 승인 시 P0(레지스트리 병합 빌드 스크립트) + §3.4 성분 seed 초안부터 착수
- [ ] 성분 seed의 임상 검수(출처: WHO/IUIS Allergen Nomenclature, EAACI 분자알레르기 가이드)
