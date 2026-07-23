# ThermoFisher Phadia / ImmunoCAP 기준 영어 명칭 검토표 (item 8)

> 목적: UniCAP(=ImmunoCAP, ThermoFisher) 검사를 표준으로 삼아, 레지스트리
> (`data/allergens.json`, 147종)의 **영어 canonical 명칭**을 ThermoFisher Phadia
> allergen catalog (https://www.thermofisher.com/phadia/wo/en/) 명명 규칙에 맞춘다.
>
> ✅ **적용 완료(2026-07-23)** — 사용자 확정에 따라 A/B/C 명칭 표준화 + D그룹 데이터 오류
> 교정을 base map(`allergen_map_prompt_v2.json`)에 반영하고 레지스트리를 재생성했습니다.
> canonical rename 시 **기존 명칭을 aliases 로 보존**해 OCR·성분 membership 하위호환을 유지합니다.
> 예외: `False acacia`(→Acacia 개명은 속(genus) 혼동 위험으로 **개명 보류**, 카테고리만 tree 로
> 교정) / `Hen's egg`(전란 분리 대신 `Egg, whole` 로 개명, 데이터 손실 없음).
> ImmunoCAP 코드(d/e/g/w/t/f/i/m/k…)는 확신 항목만 표기, 불확실한 것은 `?`.

---

## A. 접미사 " protein" 제거 (ThermoFisher는 학명/일반명만 사용)

레지스트리에 OMOP/CDM 원본의 ` protein` 접미사가 그대로 남아있는 항목. ImmunoCAP은
학명 또는 일반명만 표기합니다.

| # | 현재 명칭 | ThermoFisher 표준(제안) | ImmunoCAP | 비고 |
|---|-----------|------------------------|-----------|------|
| 1 | `Tyrophagus putrescentiae protein` | **Tyrophagus putrescentiae** | d72 | 긴털가루진드기, 학명만 |
| 2 | `German cockroach protein` | **Cockroach, German** (*Blattella germanica*) | i6 | |
| 3 | `American cockroach protein` | **Cockroach, American** (*Periplaneta americana*) | i206 | |
| 4 | `Cockroach protein` | **Cockroach, German** 로 통합 검토 | i6 | 종 불명 → 국내 우점종(독일바퀴) 기준 |

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
| 10 | `Cow milk` | **Milk** (또는 Cow's milk) | f2 | ImmunoCAP catalog는 "Milk" |
| 11 | `Ragweed` | **Common ragweed** (*Ambrosia artemisiifolia*) | w1 | |
| 12 | `Lentils` | **Lentil** | f235 | 단수 |
| 13 | `Maize` | **Maize (corn)** | f8 | `Cornflour`(옥수수가루)와 중복 정리 필요 |
| 14 | `Hen's egg` | **Egg white**(f1) / **Egg yolk**(f75) 로 분리 | f1/f75 | ImmunoCAP에 "전란" 단일 코드 없음 |
| 15 | `Casein` | **Casein** (nComponent) | f78 (m) | 우유 성분(component), 카테고리=food |
| 16 | `Lactalbumin` | **Alpha-lactalbumin** | f76 | 우유 성분 |
| 17 | `False acacia` | **Acacia** / *Robinia pseudoacacia* | ? | 명칭 확인 필요 |
| 18 | `Plaice` | **Plaice** (*Pleuronectes platessa*) | f254 | 확인용(정상) |
| 19 | `Sole`(미보유) | 필요 시 추가 | — | 참고 |

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
