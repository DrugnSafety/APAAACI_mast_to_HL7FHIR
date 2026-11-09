# ThermoFisher Phadia / ImmunoCAP 기준 영어 명칭 검토표 (item 8)

> 목적: UniCAP(=ImmunoCAP, ThermoFisher) 검사를 표준으로 삼아, 레지스트리
> (`data/allergens.json`, 147종)의 **영어 canonical 명칭**을 ThermoFisher Phadia
> allergen catalog (https://www.thermofisher.com/phadia/wo/en/) 명명 규칙에 맞춘다.
>
> ✅ **최종 확정 반영(2026-07-23, 사용자 행별 결정)** — base map(`allergen_map_prompt_v2.json`)에
> 반영 후 레지스트리 재생성. canonical rename 시 **기존 명칭을 aliases 로 보존**(OCR·성분 membership 하위호환).
>
> **행별 확정 결과:**
> - **1·2·3·5·6·7·8·9·12·13·17·18** → TF 제안 수록.
> - **4** `Cockroach protein` → **`Cockroach, Mix`**(2·3번=독일·미국바퀴가 모두 포함된 혼합 항원).
> - **10 `Cow milk`·11 `Ragweed`·14 `Hen's egg`·15 `Casein`·16 `Lactalbumin`** → **현행 명칭 유지**(개명 안 함).
> - **17** `False acacia` → **`Acacia`**(TF 수락; `False acacia`·`Robinia pseudoacacia`·아까시나무 alias 보존).
> - **19 `Sole`** → 별도 항원 미추가(넙치·가자미류는 기존 `Plaice`가 커버) — 검토 리스트에서 제외.
> - **D1** Pineapple 오류수정 / **D2** `Grass` ko=`잔디 꽃가루 혼합` / **D3** `정어리` / **D4** `실외 곰팡이 혼합` /
>   **D5** `Oat`(귀리가루,food)·`Cultivated oat`(귀리,pollen)는 **서로 다른 항원으로 별도 유지** /
>   **D6** category=food / **D7** 꽃가루로 분류. → `other` 30종 **→ 1종(Latex만)**.

---

## A. 접미사 " protein" 제거 (ThermoFisher는 학명/일반명만 사용)

레지스트리에 OMOP/CDM 원본의 ` protein` 접미사가 그대로 남아있는 항목. ImmunoCAP은
학명 또는 일반명만 표기합니다.

| # | 현재 명칭 | ThermoFisher 표준(제안) | ImmunoCAP | 비고 |
|---|-----------|------------------------|-----------|------|
| 1 | `Tyrophagus putrescentiae protein` | **Tyrophagus putrescentiae** | d72 | 긴털가루진드기, 학명만 |
| 2 | `German cockroach protein` | **Cockroach, German** (*Blattella germanica*) | i6 | |
| 3 | `American cockroach protein` | **Cockroach, American** (*Periplaneta americana*) | i206 | |
| 4 | `Cockroach protein` | **Cockroach, Mix** ✅확정 | i6/i206 | 2·3번(독일·미국바퀴)이 모두 포함된 혼합 항원 |

## B. 불필요한 수식어(grass/fruit/– fruit) 제거 → ImmunoCAP 일반명

| # | 현재 명칭 | ThermoFisher 표준(제안) | ImmunoCAP | 비고 |
|---|-----------|------------------------|-----------|------|
| 5 | `Timothy grass` | **Timothy** (*Phleum pratense*) | g6 | ImmunoCAP 표기는 "Timothy" |
| 6 | `Kiwi fruit` | **Kiwi** | f84 | |
| 7 | `Orange - fruit` | **Orange** (*Citrus sinensis*) | f33 | 하이픈 표기 비표준 |
| 8 | `Rye grass` | **Rye grass, perennial** (*Lolium perenne*) | g5 | 곡물 Rye(f5)와 구분 |
| 9 | `Bermuda grass` | **Bermuda grass** (*Cynodon dactylon*) | g2 | 확인용(정상) |

## C. 일반명 표준화(ImmunoCAP catalog 표기와 상이)

| # | 현재 명칭 | ThermoFisher 표준(제안) | ImmunoCAP | 비고 |
|---|-----------|------------------------|-----------|------|
| 10 | `Cow milk` | ~~Milk~~ → **현행 유지(`Cow milk`)** | f2 | 사용자 확정: 개명 안 함 |
| 11 | `Ragweed` | ~~Common ragweed~~ → **현행 유지(`Ragweed`)** | w1 | 사용자 확정: 개명 안 함 |
| 12 | `Lentils` | **Lentil** ✅ | f235 | 단수 |
| 13 | `Maize` | **Maize (corn)** ✅ | f8 | |
| 14 | `Hen's egg` | ~~Egg white/yolk 분리~~ → **현행 유지(`Hen's egg`)** | f245 | 사용자 확정: 유지(category=food) |
| 15 | `Casein` | **현행 유지(`Casein`)** | f78 (m) | category=food(D6) |
| 16 | `Lactalbumin` | ~~Alpha-lactalbumin~~ → **현행 유지(`Lactalbumin`)** | f76 | 사용자 확정: 유지(category=food) |
| 17 | `False acacia` | **Acacia** ✅ (alias: False acacia·Robinia pseudoacacia) | ? | 사용자 수락 |
| 18 | `Plaice` | **Plaice** (*Pleuronectes platessa*) | f254 | 명칭 유지(category=food) |
| ~~19~~ | ~~`Sole`(미보유)~~ | 리스트 제외 | — | 넙치·가자미류는 `Plaice`가 커버 |

## D. 검토 중 발견한 데이터 품질 이슈(명칭 외 — 별도 확인 요청)

명칭 표준화와 별개로, 검토 중 발견한 **명백한 오류**입니다(카테고리/한글/오타).

| # | 항목 | 문제 | 제안 |
|---|------|------|------|
| D1 | `Pineapple` | **pollen_tree 로 오분류** (파인애플은 음식) | category → food |
| D2 | `Grass` (pollen_grass) | 한글이 `나무 꽃가루 혼합`(tree pollen mix) — 오기 | ko → "목초/잔디 혼합" 또는 항목 명확화 |
| D3 | `Sardine` | 한글이 `생선 혼합`(fish mix) — 오기 | ko → "정어리", en=Pilchard (Sardine) f61 |
| D4 | `Outdoor mold mixture` | 한글 `실외 곰팡이 홉합` 오타(홉합→혼합) | ko 오타 수정 |
| D5 | `Cultivated oat` + `Oat` | 중복(귀리 vs 귀리가루) | 하나로 통합 또는 명확화 |
| D6 | `Casein`/`Lactalbumin`/`Egg white`/`Egg yolk`/`Avocado`/`Barley`/`Onion`/`Pepper`/`Rice`/`Spinach`/`Mustard`/`Sheep` | category=**other** 인데 실제 음식 | category → food (category_resolver 이관 대상) |
| D7 | `False acacia`/`Goldenrod`/`Meadow fescue` | category=other 인데 실제 꽃가루 | tree/weed/grass 로 재분류 |

---

## 적용 방법(확정 후)

"구조화된 데이터, 하드코딩 금지" 원칙에 따라 코드 변경 없이 데이터로 반영합니다.

1. **canonical rename**: `instructions/allergen_map_prompt_v2.json` 의 `canonical_name` 수정 +
   기존 명칭을 `aliases`(또는 `ocr_aliases`)로 보존 → OCR 하위호환 유지.
2. **category 교정**(D1/D6/D7): `data/category_rules.json` 의 alias_map/regex 에 규칙 추가
   (category_resolver 가 자동 반영).
3. **한글/오타 교정**(D2~D4): base map 한글 필드 수정.
4. 이후 `python3 scripts/build_allergen_registry.py` 재생성 → `git diff` 로 검수.

> ImmunoCAP 코드는 참고용이며, HL7 FHIR coding 은 계속 실제 SNOMED CT SCTID →
> CDM(OMOP) concept 우선순위를 사용합니다(명칭 표준화가 코딩을 바꾸지 않음).
