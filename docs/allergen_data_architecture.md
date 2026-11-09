# 알레르겐 데이터 아키텍처 (재설계 완료 상태)

CRD 재설계(P0~P5) 후 확립된 데이터 흐름과 **단일 권위 소스(single authority)** 정리.
핵심: 교차반응·카테고리·코딩이 **코드 분기(하드코딩)가 아니라 데이터**로 구동된다.
누락/오류는 **구조화된 리스트에 한 줄 추가**로 해결(코드 무수정).

---

## 1. 런타임 데이터 흐름

```
[큐레이션 소스 파일]                          [빌드]                 [런타임 서비스]
instructions/allergen_map_prompt_v2.json ─┐
data/cdm_snomed_mapping.json             ─┤  scripts/                data/allergens.json
data/allergen_knowledge_base.json        ─┼─ build_allergen_    ──▶  (통합 항원 레지스트리)
data/component_membership.json           ─┤   registry.py             · id/이름/별칭
data/allergen_components.json (WHO/IUIS) ─┘                           · category(+보정)
                                                                      · coding(OMOP/SNOMED)
                                                                      · components[]
                                                                      · kb_ref
```

레지스트리(`data/allergens.json`)는 **생성 산출물**이다. 소스 변경 시 재생성:
```bash
python3 scripts/build_allergen_registry.py     # + --check 로 변경 확인
```

---

## 2. 관심사별 단일 권위 (일원화 결과)

| 관심사 | 단일 권위 | add-to-list 표면 |
|---|---|---|
| **카테고리** 결정 | `services/category_resolver.py` `resolve_category()` (명시→base map→alias→regex→other+로그) | `data/category_rules.json` |
| **교차반응** 파생 | `services/crossreactivity_service.py` (성분 공유 → 후보) | `data/component_membership.json` |
| **FHIR 코딩** | `utils/allergen_mapper.py` `get_coding()` (CDM concept 우선) | `data/cdm_snomed_mapping.json` |
| **OCR 정규화/별칭** | `utils/allergen_mapper.py` `find_allergen()` | `instructions/allergen_map_prompt_v2.json` |
| **성분 family 메타** | `data/allergen_components.json` (WHO/IUIS ingest, 열안정·위험) | 동 파일 |
| **심층 backdata** | `services/knowledge_service.py` `get_backdata()` | `data/allergen_knowledge_base.json` |

> "일원화"는 **서비스 계층**에서 달성됐다. 카테고리·교차반응·코딩은 각각 **하나의 함수**가 권위다.
> 소스 데이터가 여러 큐레이션 파일에 나뉘어 있는 것은 정상(용도별 큐레이션 + git PR 리뷰 용이).

---

## 3. "리스트만 추가하면 되는" 확장 (코드 0줄)

| 하고 싶은 것 | 추가할 파일 | 예 |
|---|---|---|
| 새 교차반응(예: 셀러리↔당근) | `component_membership.json` | `"Celery": ["pr10","profilin","nsltp"]` |
| 미분류 항원 카테고리 교정 | `category_rules.json`(alias/regex) 또는 base map | `"horse dander":"animal"` |
| 새 항원(코드 자동연결) | `allergen_map_prompt_v2.json` | canonical/korean/category/snomed |
| 새 성분 family | `allergen_components.json` | family + heat_stable + risk |

미분류 항원은 런타임 로그(`[미분류 항원] category=other …`)로 노출 → 다음 add-to-list 후보.

---

## 4. CRD 2단계 원칙 (설계 불변식)

성분 공유는 **문진 후보 생성**용일 뿐, 임상 교차반응은 **증상으로 확정**한다
(서열 상동성으로 임상 교차반응 자동 판정 불가 — `docs/crd_cross_reactivity_research.md`).
위험도(risk)·열안정성은 문항 문구·경고 게이팅에만 쓰고, 판정은 증상이 한다.

---

## 5. 재설계 단계 이력

| 단계 | 내용 | 상태 |
|---|---|---|
| 계획·조사 | 재설계 계획서 + CRD 딥리서치 | ✅ |
| P0 | 단일 항원 레지스트리 병합 + 성분 seed | ✅ |
| P1 | WHO/IUIS allergen.org 전체 ingest(성분 카탈로그) | ✅ |
| P2 | 성분 기반 교차반응 문진을 엔진에 연결 | ✅ |
| P3 | 진드기↔갑각류 하드코딩 → 성분 엔진으로 통합 | ✅ |
| P4 | 카테고리 resolve 파이프라인 정식화 | ✅ |
| P5 | 데드코드 정리 + 서비스 계층 일원화 문서화 | ✅ |

### P5에서 제거/정리
- `knowledge_service.mite_shellfish()` 메서드(미사용) 제거.
- `Q_MITE_SHELLFISH` 문진 상수(미사용) 제거.
- `pollen_food_cross_reactivity.json` 의 `mite_shellfish` 블록은 이제 orphan(참고용) —
  진드기↔갑각류는 성분 엔진(tropomyosin)이 처리. `pollen_food`(OAS PFAS)와
  `shellfish_food_names`(is_shellfish 판별)는 계속 사용.

### 남은 선택 정리(비필수)
- `is_shellfish()`(이름 매칭)를 레지스트리 기반(subcategory=shellfish 또는 tropomyosin
  보유 해양 무척추)으로 이관 가능 — 현재도 정상 동작하므로 선택 사항.
- OAS `pollen_food`(PFAS)를 성분 엔진으로 완전 이관 가능하나, 현재 OAS 전용 UX가
  임상적으로 잘 조정돼 있어 유지.
