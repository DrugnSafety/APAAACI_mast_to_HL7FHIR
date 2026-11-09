# WHO/IUIS Allergen Nomenclature — Component Family 시드 (교차반응 네트워크 원재료)

> 출처: 사용자가 제공한 **allergen.org (WHO/IUIS Allergen Nomenclature)** 공식 export
> (`allergentable.csv` 1,160개 component / `isotable.csv` 1,624 isoform / `jointtable.csv` / `idmapping.csv`)
>
> `BioNames` 컬럼 = **단백질 family(= 분자 component 계열)**. 같은 family를 공유하는 종(species)이
> 곧 **교차반응 가능 알레르겐 네트워크**다. 아래는 그 추출 결과.
>
> ⚠️ **상태**: 이 표는 Linux 세션에서 CSV를 파싱해 추출한 것이다. 목록은 표시 편의상 상위 28종까지만
> 적혀 있으므로, **실제 DB 구축 시 원본 CSV로 전체 목록을 재추출**해야 한다. (§재현 방법 참조)

---

## 1. 왜 이게 중요한가 (CRD 관점)

현행 플랫폼의 교차반응 데이터 출처는 `"seed:Korean allergy practice"` — **수작업 시드**다.
반면 WHO/IUIS는 **공식 명명법 DB**이고, `BioNames`가 곧 component family이므로:

```
항원 A 양성  →  A가 보유한 component family(들)  →  같은 family를 가진 다른 종 = 교차반응 후보
```

이 한 규칙이 현재 하드코딩된 두 케이스(꽃가루-음식 `pollen_food`, 진드기-갑각류 `mite_shellfish`)와
`is_shellfish()` 문자열 매칭을 **전부 대체**한다.

**CRD(Component-Resolved Diagnostics) 핵심**: 추출물 검사('고양이 양성')는 뭉뚱그린 결과이고,
임상 의미는 *어떤 component에 감작됐는가*로 갈린다.
- 고양이 **Fel d 1**(secretoglobin) = 진짜 고양이 알레르기 마커
- 고양이 **Fel d 2**(serum albumin) = pork-cat 증후군(돼지고기 교차)
- 고양이 **Fel d 4**(lipocalin) = 개·말 등과 교차

---

## 2. 추출된 Component Family → 공유 종(교차반응 네트워크)

| Family (BioNames) | 종 수 | 공유 종 (일부) | 예상 임상 성격* |
|---|---|---|---|
| **Tropomyosin** | 41 | 새우(Black tiger/Brown/King prawn/North Sea/Northern), 게(Blue swimmer/Green mud/Crucifix), 랍스터, 크릴, 오징어, 문어, 굴(Pacific/Portuguese), 전복, 달팽이, 가재, **집먼지진드기(American/European)**, **바퀴(American/German)**, 저장진드기, 아니사키스(Herring worm), 회충, 흰개미, 깔따구, 틸라피아, 연어 | 무척추동물 pan-allergen · 열/소화 안정 → **전신 위험** |
| **Profilin** | 60 | 사과, 바나나, 셀러리, 당근, 헤이즐넛, 아몬드, 호두, 리치, 망고, 가지, 고추, 보리, 옥수수, 대추야자, **자작나무**, 돼지풀, 명아주, 질경이, 버뮤다그래스, 양버즘나무, 검은포플러 | 범-꽃가루/음식 · **열 불안정 → 대개 경증 OAS** |
| **PR-10 (Bet v 1 family)** | 30 | **자작·오리나무·서어나무·너도밤나무·참나무(홍/졸참)**, 사과, 살구, 복숭아, 배, 체리, 딸기, 산딸기, 당근, 셀러리, 헤이즐넛, 아몬드, 밤, 키위, 망고, **땅콩**, **콩**, 녹두, 인삼 | 열 불안정 → **OAS(국소)** 전형 |
| **nsLTP (Pru p 3 family)** | 53 | **복숭아**, 사과, 살구, 자두, 포도, 레몬, 바나나, 뽕나무, 호두, 헤이즐넛, 아몬드, 밤, 밀(듀럼), 옥수수, 렌틸, 강낭콩, 루핀, 상추, 아스파라거스, 양배추, 셀러리, **쑥**, 양버즘나무 | **열/소화 안정 → 전신·아나필락시스 위험** (지중해형) |
| **Parvalbumin** | 22 | **대구(Atlantic/Baltic)**, 연어, 송어, 고등어(Atlantic/Indian), 참치, 청어, 정어리, 잉어, 붕어, 넙치, 서대, 황새치, 메기, 농어, 바라문디, 갈치, **닭**, 개구리, 악어 | 생선 pan-allergen · 열 안정 → **전신 위험** |
| **Serum albumin** | 7 | **고양이(Fel d 2)**, **개(Can f 3)**, 소, 말, **돼지**, 닭, 기니피그 | pork-cat / beef-milk-dander 증후군 |
| **Lipocalin** | 12–13 | **고양이(Fel d 4)**, **개(Can f 1/2)**, 소, 말, 햄스터(골든/시리아/시베리안), 생쥐, 쥐, 토끼, 기니피그, 바퀴 | 동물 비듬 **major allergen** |
| **2S albumin (storage protein)** | 22 | **땅콩(Ara h 2/6)**, 호두(영/흑), 캐슈, 피스타치오, 피칸, 헤이즐넛, 잣, 참깨, 겨자(황/동양), 유채, 아마씨, 해바라기, 호박씨, 메밀, 콩, 피마자 | 견과/종자 저장단백 · 열/소화 안정 → **전신·아나필락시스 위험** |
| **Polcalcin** | 15 | 자작, 오리나무, 쑥(여러 종), 돼지풀, 올리브, 티모시, 버뮤다그래스, 명아주, 라일락, 노간주, 수송나물, 개물통이 | 꽃가루 **pan-marker**(범꽃가루 감작 지표) |
| **Arginine kinase** | 15 | 새우(여러), 게, **집먼지진드기(American/European)**, **바퀴(American/German)**, 저장진드기, 굴, 가재, 나방(누에/화랑곡) | 또 다른 무척추동물 pan-allergen |
| **Ole e 1-like** | 14 | 물푸레나무(ash), 올리브, 라일락, 쥐똥나무, 질경이, 호밀풀, 티모시, 명아주, 비름, 사탕무, 메스키트, 사프란 | 수목/화본과 |
| **Thaumatin-like (PR-5)** | 12 | 사과, 바나나, 체리, 복숭아, 키위, 고추, 올리브, **삼나무(Sugi)**, 편백/측백, 향나무 | 과일 + 삼나무 |

\* 임상 성격 열은 **예비 주석**이다. 열/소화 안정성과 국소 vs 전신 위험은 진행 중인 deep-research
(EAACI MAUG 2.0 근거)로 **확정 후 갱신**할 것.

---

## 3. 이 시드가 즉시 해결하는 것

| 현재 문제 | 이 시드로 해결 |
|---|---|
| 새우 양성 → 게 질문 불가 (음식↔음식 구조 없음) | Tropomyosin 공유 → 게·랍스터·오징어·굴 자동 회수 |
| 진드기→갑각류가 `mite_shellfish` 특수 블록 | 같은 Tropomyosin 규칙으로 흡수 (특수 블록 삭제) |
| 셀러리 양성 → 교차식품 조회 경로 없음 | Profilin + PR-10 + nsLTP 3개 family 공유 → 당근·사과·쑥·향신료 자동 회수 |
| `is_shellfish()` 문자열 매칭 하드코딩 | family 소속으로 대체 |
| 개·고양이 교차(pork-cat 등) 미지원 | Serum albumin / Lipocalin family로 표현 |

---

## 4. 재현 방법 (원본 CSV 필요)

⚠️ **CSV가 현재 Mac에 없다.** 전체 목록 재추출 및 DB 구축을 위해 다음 중 하나 필요:
1. allergen.org → Downloads 페이지에서 재다운로드 (`allergentable`, `isotable`, `idmapping`, `jointtable`)
2. 또는 채팅에 4개 CSV 재첨부

추출 스크립트(참고):
```python
import csv, re
from collections import defaultdict
rows = list(csv.DictReader(open('allergentable.csv')))
FAM = {'tropomyosin': r'tropomyosin', 'profilin': r'profilin',
       'PR-10': r'bet v 1|PR-10', 'nsLTP': r'lipid transfer',
       'parvalbumin': r'parvalbumin', 'serum albumin': r'serum albumin',
       'lipocalin': r'lipocalin', '2S albumin': r'2s albumin',
       'polcalcin': r'polcalcin', 'arginine kinase': r'arginine kinase',
       'Ole e 1-like': r'ole e 1', 'PR-5': r'thaumatin'}
fam_species = defaultdict(set)
for r in rows:
    bn = r['BioNames'].strip().lower()
    for fam, pat in FAM.items():
        if re.search(pat, bn):
            fam_species[fam].add((r['Common'].strip(), r['Species'].strip(), r['Name'].strip()))
# r['Name'] = component 이름 (예: 'Fel d 1', 'Der p 10') → CRD marker
```

`Name` 컬럼이 **IUIS component 명칭**(Fel d 1, Der p 10, Ara h 2…)이므로,
이걸로 **component 단위 레코드**를 만들면 CRD 검사(ImmunoCAP ISAC/ALEX) 결과까지 그대로 수용 가능하다.

---

## 5. 다음 단계
- [ ] deep-research 완료 → 각 family의 열/소화 안정성·국소 vs 전신 위험 **근거 확정**(EAACI MAUG 2.0)
- [ ] allergen.org/EAACI **라이선스 검토** (임상 SW에 데이터 내장 가능 여부)
- [ ] 원본 CSV 확보 → `data/allergen_components.json` + `data/allergens.json` 생성 (재설계 계획 P0/P1)
- [ ] 추출물 검사 항원 → 후보 component 매핑 규칙 (추출물은 component를 모르므로 '가능 component 집합'으로)
