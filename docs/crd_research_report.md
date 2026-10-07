# CRD(Component-Resolved Diagnostics) 기반 교차반응 DB — 조사 리포트

> deep-research 하네스: 5개 검색축 · 19개 소스 fetch · 90개 주장 추출 · 25개 적대적 검증(3표 중 2표 반증 시 폐기) → **15 confirmed / 10 refuted** · 101 에이전트
>
> ⚠️ 이 리포트의 **반증된 주장(§3)은 절대 코드에 넣지 말 것.** 몇 개는 내가 처음에 설계에 넣으려던 것이다.

---

## 1. 핵심 결론 — CRD는 채택해야 하지만, "family = 교차반응"은 틀렸다

**채택 근거 (confirmed, 3-0)**
- 감작(whole-extract 양성) ≠ 임상 알레르기. CRD는 정제된 단일 분자로 component별 IgE를 측정해 **임상적으로 유의한 sIgE와 무의미한 sIgE를 (부분적으로) 감별**한다. 땅콩이 원형 사례 — 꽃가루 감작자는 Ara h 8/profilin/CCD 교차반응 때문에 땅콩 IgE 양성이지만 **자유롭게 먹는 위양성이 흔하다**.
- **Component 정체성이 위험도를 결정**한다: 땅콩 **Ara h 2**(2S albumin) 조기감작 = 전신반응 위험↑ / **Ara h 8**(PR-10) = 대개 OAS. MAUG 2.0은 2S albumin(Ara h 2/6, Cor a 14, Gly m 8)·종자저장단백·nsLTP(Pru p 3, Cor a 8, Ara h 9)를 **risk/severity-associated molecules**로 분류.
- **열/소화 안정성**이 국소(OAS) vs 전신 위험을 가르는 핵심 필드: **nsLTP=열안정**(조리해도 IgE 인식 유지 → 전신), **PR-10=열불안정**(가열 시 변성 → 조리식품 무증상, 주로 OAS).

**그러나 (중요):**
- **family 내 교차반응은 균일하지 않다(patchy).** nsLTP Pru p 3는 Rosaceae Mal d 3와 강하게 교차하지만 밀 Tri a 14 / 땅콩 Ara h 9와는 덜 교차. → **family 엣지는 확정이 아니라 가중치 있는 후보**로 다뤄야 한다.
- **교차반응은 family 밖에서도 일어난다**: 콩 vicilin Gly m 5 ↔ 우유 alpha-casein Bos d 9 (유사 펩타이드 공유).
- **IgE 교차반응 ≠ 임상 교차반응.** 상동 단백질 간 아미노산 몇 개 공유만으로 IgE는 교차결합하지만 임상 증상으로 이어지지 않을 수 있다.

---

## 2. marker vs cross-reactive — 데이터 모델의 핵심 축

| | marker allergen | cross-reactive allergen |
|---|---|---|
| 정의 | 종/속/과에 국한, 밖과 epitope 비공유 | 다수 소스에 걸쳐 epitope 공유 |
| 의미 | IgE 결합 = **genuine 감작 확정** (회피·면역치료 표적) | 교차반응일 뿐, 진짜 원인 아닐 수 있음 |
| 예 | **Fel d 1**(고양이), Phl p 1(잔디), Api m 1(꿀벌), Cup a 1 | Fel d 2(serum albumin), Bet v 2(profilin), Der p 10 |

**스키마 함의 (confirmed):**
- marker는 **boolean이 아니다.** `(component × 분류학적 scope)` 속성이며 **개정 가능**하다 — Vespula group-5는 상동체(Sco m 5/Tab y 5) 발견 후 marker에서 cross-reactive로 재분류됨. → `(component, scope, marker_confidence, last_reviewed)` 구조 권장.
- **marker ≠ major**: Can f 5는 marker지만 minor allergen. Hazelnut Cor a 1은 major지만 marker로는 무용.

---

## 3. ⛔ 반증된 주장 — 코드화 금지 (내가 넣으려던 것 포함)

| 반증된 주장 | 왜 위험한가 |
|---|---|
| **">70% 서열동일성 = 교차반응, <50% = 드묾"** 규칙 | **보편 cutoff 없음.** 코드화 금지 |
| **nsLTP 양성 → 자동 전신(systemic) 티어 게이팅** | 자동 게이팅 부적절 |
| **allergen.org의 숫자(Fel d **1**)를 family 키로 재사용** | 번호는 family 기반이 아님. BioNames 텍스트는 쓰되 불완전한 신호로 취급 |
| Scala 2011 유병률 수치(profilin 48.8% 등) | 단일센터 이탈리아 코호트 — **한국 유병률로 전이 금지**(한국은 tropomyosin/HDM-새우 높고 birch/PR-10 낮음). 구조만 재사용 |
| MAUG의 "정확히 3가지 기능" | 과장 |

**추가 경계:**
- **Ara h 8은 "zero-risk 하드게이트"가 아니다.** Asarnoj 2012(n=144): 89.5% 관용이지만 **~10% 구강증상, 1명 전신증상**. → epinephrine 상담을 억제하는 게이트로 쓰면 안 됨.
- **Panallergen(profilin/PR-10/tropomyosin) 인식은 대체로 상호배타적** (ISAC 3,113명: 86.4%가 1개 group만, 1.2%만 3개 모두). → **profilin 양성이 자동으로 PR-10/tropomyosin 문진을 트리거하면 안 된다.**
- **HDM↔새우는 예측 규칙이 아니라 확률적 스크리닝 경로.** Der p 10은 **minor** allergen이고 다수 HDM 감작자는 새우를 관용한다.
- **CCD와 alpha-Gal을 한 덩어리로 묶지 말 것.** CCD(MUXF3 등 고전 식물/곤충 글리칸)는 임상 의미 제한적이지만 **alpha-Gal은 적색육 지연 아나필락시스로 임상적으로 진짜 위험**하다. 둘 다 단백질 family가 아닌 **glycan-epitope 엣지**로 별도 모델링.
- **CRD는 병력·유발검사의 보조이지 대체가 아니다.** component 양성을 임상 알레르기 확정으로 취급 금지.

---

## 4. 데이터 소스 — 구조와 라이선스

### WHO/IUIS Allergen Nomenclature (allergen.org)
- 구조: **종(Species) → component(속 3글자+종 1글자+생화학 그룹 번호, 예 `Fel d 1`) → isoallergen → 서열(UniProt/GenBank)**
- `BioNames` = 단백질명(자유텍스트) → family 정규화의 재료. 단 **자유텍스트라 정규화 규칙 필요**(우리는 `data/component_family_rules.json`으로 처리).
- 교차반응 서술(Allergenicity/IsoAllergenicity)은 **확정이 아닌 '가능성'** 텍스트.

### EAACI Molecular Allergology User's Guide 2.0 (MAUG 2.0)
- Pediatr Allergy Immunol 2023;34(Suppl 28):e13854 (PMID 37186333). >95인 EAACI Taskforce 2판.
- component별 marker vs cross-reactive 구분·위험도를 제공하는 **citable 권위서**.
- ⚠️ **라이선스: CC BY-NC 4.0 (비상업).** "무료 열람 ≠ 자유 재사용".
  → **상업 임상 소프트웨어에 텍스트/표를 그대로 내장하려면 Wiley/EAACI 허가 필요.**
  → **안전책: 저작권 대상이 아닌 '사실'(family·marker·안정성·위험도)만 자체 표현으로 재구성.** (본 프로젝트는 이 방식을 따름)
  - 라이선스 메타데이터가 소스마다 불일치(PubMed non-OA / OpenAlex bronze / Crossref CC BY-NC) → 상업 배포 전 **Wiley PDF 실제 라이선스 직접 확인 또는 허가 취득 필요**.

### 검사 가능성(testability) — 별도 플래그 필수
- IUIS 등록 알레르겐 **~1,108개(2024.2) → ~1,148개(2026)** 이나 singleplex/multiplex(ISAC/ALEX)로 **실제 검사 가능한 것은 200개 미만**.
- → DB는 **'존재(exists)'와 '검사가능(testable)'을 분리 플래그**해야 한다. 이것이 CRD 추가검사 권고 로직의 기반.

### 이해상충
- Vitte 2024(marker/cross-reactive 정의의 주 근거)는 **Thermo Fisher(ISAC/ImmunoCAP 판매) 관계 공저자 포함** → "CRD 추가검사 권고" 로직의 상업적 중립성 근거로 **단독 인용 금지**.

---

## 5. 우리 구현에 미치는 구체적 영향 (실측)

`data/allergen_components.json` 빌드 후 순수 family 공유로 교차반응을 파생해 보니 **연구가 경고한 실패 모드가 그대로 재현**됐다:

| 문제 | 실측 |
|---|---|
| 임상 무의미 엣지 | **새우 → 모기·흰개미·누에·깔따구** (tropomyosin 공유) |
| | **고양이 → 모기** (lipocalin 공유) |
| 후보 폭발 | **땅콩 → 122종**, 셀러리 → 93종, 자작 → 88종 |
| panallergen 캐스케이드 | 자작 → 땅콩 (profilin/PR-10 경유) — 연구상 **자동 트리거 금지** 대상 |

→ **순수 family 공유는 그대로 쓰면 안 된다.** 다음 보정 계층이 필수:
1. **노출경로/맥락 필터** — 음식 문진에는 `route=ingestion`인 종만 (모기·흰개미 제거)
2. **분류학적 근접도 가중** — 같은 목(Decapoda: 새우↔게) = 高, 계통이 먼 공유 = 低
3. **family 임상 우선순위 + panallergen 플래그** — profilin/polcalcin/cyclophilin은 낮은 우선순위, **자동 캐스케이드 금지**
4. **후보 상한(top-N) + 확률적 표현** — "가능성 있는 항원"이지 "교차반응 확정"이 아님을 문구에 반영

---

## 6. 인용 소스 (주요)
- EAACI Molecular Allergology User's Guide 2.0 — https://onlinelibrary.wiley.com/doi/10.1111/pai.13854 (PMID 37186333, CC BY-NC 4.0)
- Vitte J. et al. 2024, JACI Global (PRISMA 체계적 문헌고찰) — https://www.jaci-global.org/article/S2772-8293(24)00026-2/fulltext
- Treudler R, Simon JC. Component resolved diagnosis. Curr Allergy Asthma Rep 2013 — https://link.springer.com/article/10.1007/s11882-012-0318-8
- Radauer C. et al. JACI 2008 (AllFam: 707 알레르겐 → 134 family)
- Asarnoj A. et al. 2012 (Ara h 8 단독감작 관용 89.5%, ~10% 구강증상)
- Scala E. et al. 2011 (ISAC 3,113명 panallergen 인식 패턴) — PMC3174236
- WHO/IUIS Allergen Nomenclature — https://www.allergen.org/downloads.php
