# CRD(Component-Resolved Diagnostics) 기반 교차반응 DB 설계 — 조사 리포트

> **관련 문서(병렬 작업 통합됨)**: 실제 WHO/IUIS allergen.org 성분 카탈로그는 `data/allergen_components.json`
> (P1 ingest, 47 family·329 species)와 `data/component_family_rules.json`로 구축돼 있고, 별도 조사 리포트
> [`crd_research_report.md`](./crd_research_report.md)·[`component_family_seed_from_iuis.md`](./component_family_seed_from_iuis.md)도 있다.
> 본 문서는 그와 **동일 결론(family≠임상 교차반응)** 에 독립 도달했으며, 여기서는 §2 family 표와 §5 레지스트리 2단계 모델에 초점을 둔다.

> 딥리서치 결과(에이전트 106, 근거 82개 추출 → 25개 적대적 검증 → 22개 확정·3개 반증, 출처 24). 이 문서는 §`allergen_db_redesign_plan.md`를 **CRD 2단계 모델**로 업그레이드하는 근거·설계다.

---

## 0. 한 줄 결론

교차반응은 **공유 단백질 family(component)** 로 발생하지만, **서열 상동성으로 임상 교차반응을 자동 판정할 수 없다**(3,113명 연구에서 profilin/PR-10/tropomyosin IgE 인식이 86.4% 상호배타적). 따라서 성분 계층은 **"교차반응 문진 후보를 생성하는 스크리닝 레이어"** 로만 쓰고, 실제 임상 교차반응은 **증상 문진으로 확정**한다 — 이는 이 플랫폼의 '감작 vs 실제 알레르기' 철학과 정확히 일치한다.

---

## 1. CRD와 marker vs cross-reactive (감별의 분자적 근거)

- **CRD**는 whole-extract가 아니라 **정제된 단일 분자 component**로 진단해, 환자별 정밀 진단·관리를 가능케 한다. 알레르기 진단은 "정확한 분자 유발원(elicitor) 식별"에 달려 있다 (EAACI MAUG 2.0, Dramburg 2023). → **'고양이 양성'·'새우 양성' 같은 extract 결과는 component로 분해해야 해석 가능**하다. [MAUG 2.0](https://onlinelibrary.wiley.com/doi/10.1111/pai.13854)

- **Marker allergen** = 특정 종/속/과에 한정 발현, 외부 공유 epitope 없음 → 양성이면 **진짜(1차) 감작 확정**. **Cross-reactive allergen(panallergen)** = 여러 소스에 공유 epitope → 교차반응 유발. 그리고 **분자적 IgE 교차반응 ≠ 임상 교차반응**: 자작 Bet v 1은 대개 1차 감작원이고, 사과 Mal d 1 결합은 저친화도라 임상적으로 의미 없을 수 있다 (WHO/IUIS Pomés 2018; JACI Global 2024). [PMC6019191](https://pmc.ncbi.nlm.nih.gov/articles/PMC6019191/), [JACI Global 2024](https://www.jaci-global.org/article/S2772-8293(24)00026-2/fulltext)

- **대표 marker (1차 감작 확정용)**: 자작 **Bet v 1**, 잔디 **Phl p 1**, 올리브/물푸레 **Ole e 1**, 쑥 **Art v 1**, 돼지풀 **Amb a 1**, 땅콩 **Ara h 2**, 복숭아 **Pru p 3**, 고양이 **Fel d 1**, 개 **Can f 1/Can f 5**. [PMC8167734](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8167734/)

### 사례 (component가 임상을 가른다)
- **땅콩**: **Ara h 2**(2S albumin) → 진짜·전신 위험 / **Ara h 8**(PR-10) → 대개 경증 OAS
- **고양이**: **Fel d 1**(secretoglobin, >90% 인식, 교차반응 없음) = 진짜 고양이 알레르기 마커 / **Fel d 2**(serum albumin)만 양성 = 다른 동물 교차반응(1차 감작원 딴 데) / **Fel d 4**(lipocalin) = 개·말 교차. 개 **Can f 1**은 고양이 **Fel d 7**(lipocalin)과 교차. [Springer 2017](https://link.springer.com/article/10.1007/s11882-017-0732-z)
- **새우/진드기**: **tropomyosin**(진드기 Der p 10 ↔ 새우 Pen m 1 ↔ 바퀴 ↔ 식용곤충) — 현재 수작업 시드하던 진드기-갑각류 링크의 분자적 근거. [JACI Global 2024](https://www.jaci-global.org/article/S2772-8293(24)00026-2/fulltext)

---

## 2. 교차반응성 단백질 family 표 (설계 직접 반영용)

> 위험도는 **집단 수준 경향**이며 절대값이 아니다(§3 반증 참조). heat/digest 안정 = 조리·소화에도 남아 **전신 위험** 경향.

| Family | 대표 marker | 열/소화 안정 | 임상 위험 경향 | 공유 알레르겐(allergen.org 실측) |
|---|---|---|---|---|
| **Profilin** (Bet v 2/Phl p 12) | — (범교차, marker 아님) | 불안정 | 대개 **경증 OAS**(단, 절대 아님) | 자작·잔디·쑥·돼지풀 ↔ 멜론·수박·바나나·감귤·토마토·셀러리·당근 (60종) |
| **PR-10** (Bet v 1) | Bet v 1 | 불안정(단 균일치 않음) | **경증~때때로 중증** | 자작·오리·개암 ↔ 사과·복숭아·살구·당근·셀러리·헤이즐넛·**땅콩(Ara h 8)·콩** (30종) |
| **nsLTP** (Pru p 3) | Pru p 3 | **안정** | **전신 위험**(가변·patchy) | 복숭아·사과·호두·헤이즐넛·밀·포도·쑥·아스파라거스 (53종) |
| **Tropomyosin** | Pen m 1/Der p 10 | **안정** | **전신 위험** | 새우·게·랍스터·오징어·굴·전복·**진드기·바퀴**·아니사키스 (41종) |
| **Arginine kinase** | — | 안정 | 전신 가능 | 새우·게·진드기·바퀴·나방 (15종) — 또 다른 무척추 pan-allergen |
| **Parvalbumin** | Gad c 1 등 | **안정** | 전신(생선 범교차) | 대구·연어·고등어·참치·잉어·청어 (22종) — 민감도↑ 특이도↓ |
| **Serum albumin** | Bos d 6/Fel d 2 | **불안정** | 조리 시 관용, **생/덜익힘 위험**(pork-cat ~3%) | 고양이·개·소·말·돼지·닭 (7종) |
| **Lipocalin** | Fel d 4/Can f 1·2 | — | 동물 비듬 주 원인 | 고양이·개·소·말·햄스터·쥐·바퀴 (12종) |
| **2S albumin**(저장단백) | Ara h 2/Ana o 3 | **안정** | **전신 위험**(견과·종자) | 호두·캐슈·땅콩·헤이즐넛·참깨·겨자·콩 (22종) |
| **Polcalcin** (Bet v 4/Phl p 7) | — | — | 꽃가루 pan-marker(음식無) | 자작·쑥·올리브·티모시·돼지풀 (15종) |
| **Ole e 1-like** | Ole e 1 | — | — | 물푸레·올리브·질경이·티모시·privet (14종) |
| **Thaumatin/PR-5** | — | 부분 | 가변 | 사과·바나나·키위·복숭아·체리·삼나무 (12종) |

---

## 3. ⚠️ 핵심 설계 제약 (반증된 가정 — DB에 넣으면 안 되는 것)

딥리서치가 **적대적으로 반증(refute)** 한 사항 — 반드시 반영:

1. **"서열 상동성 → 임상 교차반응 자동 유도"는 불가.** 3,113명 microarray: profilin/PR-10/tropomyosin 세 군 중 **86.4%가 단 하나만 인식**, 셋 다 인식은 1.2%뿐. WHO/IUIS: "서열 동일성 기반 알레르기성 예측 모델은 신뢰할 수 없다." → **family membership은 "교차반응 후보(문진 대상)"를 생성할 뿐, 임상 교차반응·안전성 판정이 아니다.** [PMC3174236](https://pmc.ncbi.nlm.nih.gov/articles/PMC3174236/), [PMC6019191](https://pmc.ncbi.nlm.nih.gov/articles/PMC6019191/)

2. **"PR-10은 균일하게 열불안정 → 조리 시 안전, OAS만"은 반증됨(0-3).** PR-10 위험은 경증~때때로 중증. → PR-10을 "OAS 전용·안전"으로 게이팅하지 말 것.

3. **allergen number는 family를 인코딩하지 않음.** 숫자 8 = 돼지풀에선 profilin(Amb a 8), 땅콩에선 PR-10(Ara h 8). → **DB는 allergen 번호가 아니라 protein family(BioNames)로 키를 잡아야 한다.** [PMC6019191](https://pmc.ncbi.nlm.nih.gov/articles/PMC6019191/)

**결론**: 위험도 게이팅은 **확률적·hedged**로. profilin도 절대 "안전" 플래그가 아님(멜론·수박에서 전신 반응, 특히 잔디꽃가루 고노출 집단).

---

## 4. 데이터 소스 활용 방안과 한계

### allergen.org (WHO/IUIS Allergen Nomenclature)
- **권위**: WHO/IUIS Allergen Nomenclature Sub-Committee가 공식 큐레이션(임의 관행 아님). [all.13693](https://onlinelibrary.wiley.com/doi/10.1111/all.13693)
- **구조(업로드 CSV 실측)**: `allergentable`(1160 component: Species/Common/AllergenID/Name/**BioNames=단백질family**/MolecularMass/Allergenicity/Exposure), `isotable`(isoallergen 서열·UniProt/PDB), `idmapping`, `jointtable`. → **BioNames가 우리 component family 계층의 원천.**
- **한계**: (a) isoallergen/variant 식별 임계값(≥67%/>90%)·번호 형식은 **딥리서치에서 검증 실패(1-2)** → export에서 직접 확인 필요. (b) **재사용/재배포 라이선스 미해결**(open question) — 상용·배포 전 확인 필수.

### EAACI MAUG 2.0
- **성격**: Part C가 plant(LTP/polcalcin/PR-10/profilin)·animal(lipocalin/parvalbumin/serum albumin/tropomyosin) panallergen family를 marker vs cross-reactive + 임상효용으로 정리 — 우리 계층과 정확히 일치. [MAUG 2.0](https://eaaci.org/books/molecular-allergology-users-guide-2-0/)
- **한계**: **narrative textbook(A/B/C), 기계판독 DB 아님** → 텍스트 수동 추출 필요. 라이선스 확인 필요.

### 남은 open questions
1. allergen.org export의 정확한 필드 의미(isoallergen 임계값) — export 직접 검증.
2. allergen.org·MAUG 2.0 **라이선스·재배포 조건** (배포/상용 전 필수).
3. 서열로 못 하면 **정량 교차반응 확률**은 어디서? → 큐레이션된 임상 교차반응 표 / 한국 코호트.
4. **한국 인구 감작 패턴**·국내 orderable component 패널(ISAC/ALEX)이 지중해 코호트와 다름.

---

## 5. 재설계 플랜 업그레이드 — CRD 2단계 모델

기존 `allergen_db_redesign_plan.md`의 "성분 계층"을 다음으로 구체화:

```
whole-extract 항원(양성)  →  ① 후보 component 매핑(species→emitted components)
                          →  ② component의 family(BioNames)
                          →  ③ 같은 family 공유 알레르겐 = 교차반응 "후보"
                          →  ④ 후보에 대해 '드셨을 때 증상 있었나요?' 문진 자동생성   ← 여기까지가 데이터
                          →  ⑤ 증상 있음 → 임상 교차반응 confirmed / 없음 → 감작(관용)  ← 증상이 확정
```

- **①은 확정 불가(component 검사 안 하면 어떤 component인지 모름)** → species가 배출하는 **후보 component 전체**를 제시하고, "이 중 무엇에 감작됐는지는 component 검사 필요" 안내. whole-extract만 있으면 family 후보로 문진.
- **위험 게이팅(④)**: family의 heat/digest 안정성으로 문항 문구·severity 힌트만(안정 family=전신 경고 강조, 불안정=대개 국소지만 hedged). **판정은 증상이.**
- **marker component 지원**: 향후 component 검사 결과(예: Ara h 2, Fel d 1)가 입력되면 marker→진짜 감작 confirmed 로직으로 확장.

### 데이터 파일 추가(계획 §3에 통합)
- `allergen_components.json`: family 카탈로그(family, heat_stable, clinical_risk_tier[hedged], marker_of[], note_ko, source).
- 항원 레코드의 `components[]`: allergen.org BioNames로 태깅(예 shrimp→tropomyosin, arginine_kinase).
- `cross_reactivity_notes`: allergen.org IsoAllergenicity/Allergenicity·MAUG 인용은 **출처 표기**하되 라이선스 확인 전엔 **요약·참조만**(원문 대량 복제 금지).

---

## 6. 출처(주요)
- WHO/IUIS Sub-Committee governing paper (Pomés 2018): https://pmc.ncbi.nlm.nih.gov/articles/PMC6019191/
- JACI Global 2024 systematic review (marker/panallergen): https://www.jaci-global.org/article/S2772-8293(24)00026-2/fulltext
- Scala 2011 microarray n=3,113 (상호배타성): https://pmc.ncbi.nlm.nih.gov/articles/PMC3174236/
- EAACI MAUG 2.0 (Dramburg 2023): https://onlinelibrary.wiley.com/doi/10.1111/pai.13854 · https://eaaci.org/books/molecular-allergology-users-guide-2-0/
- Animal panallergens (Fel d/Can f/serum albumin): https://link.springer.com/article/10.1007/s11882-017-0732-z
- Pollen molecular diagnosis(marker/panallergen): https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8167734/
- allergen.org: https://www.allergen.org/

*임상 판단은 담당 의료진·최신 가이드라인에 따르며, 본 문서는 데이터 설계 근거용이다.*
