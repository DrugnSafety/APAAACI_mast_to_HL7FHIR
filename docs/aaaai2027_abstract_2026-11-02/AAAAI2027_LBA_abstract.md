# AAAAI 2027 Late-Breaking Abstract — 제출본

> **Beyond sensitization: real-world validation of an ontology-grounded large language model pipeline converting allergy test reports into patient-centered, interoperable records**

제출 화면(Submission Summary) 기준으로 옮긴 원문입니다. 확인 시각: 2026-11-02 23:45 CT (최종 제출본).

## 제출 정보

| 항목 | 내용 |
|---|---|
| 학회 | 2027 AAAAI Annual Meeting (New Orleans, 2027-02-19 – 22) · Late-Breaking Abstract |
| Submission ID | 1492109 |
| 상태 | Submitted for Review (2026-11-02 마감 전 수정·재제출 완료) |
| Interest Section | 600 – Health Outcomes, Education, Technology and Quality (HETQ) |
| Keyword | 610. EHR/EMR/Health Informatics |
| 발표자 | Primary Author = Presenting Author (late-breaking 가이드: "First Author: Presenting author") |
| 분량 | 본문 248 / 250 words (제목·섹션 제목 제외) |
| 결제 | $50 Payment Received |

## 저자 및 소속 (제출 순서)

| 순서 | 저자 | 역할 | 소속 |
|---|---|---|---|
| 1 | Mingyu Kang, MD | Primary · Presenting author, Submitter | Chungbuk National University and Hospital, Cheongju, Korea; Mass General Brigham, Boston, MA, USA (Dept.: Allergy and Clinical Immunology; CAMCA, Radiology) |
| 2 | Ha-Kyeong Won, MD | Co-Author | Chungbuk National University and Chungbuk National University Hospital, Cheongju, Republic of Korea (Division of Allergy and Clinical Immunology) |
| 3 | Kicheol Yu | Co-Author | Chungbuk National University Hospital, Cheongju, Republic of Korea (Division of Allergy and Clinical Immunology) |
| 4 | Hyunju Kwon | Co-Author | 위와 같음 |
| 5 | Younjung Bae, MD | Co-Author | 위와 같음 |
| 6 | Seoyeon Yoon | Co-Author | 위와 같음 |
| 7 | Minjeong Ko | Co-Author | 위와 같음 |
| 8 | Hyungki Kim | Co-Author | Chungbuk National University Hospital, Cheongju, Republic of Korea; Seowoo Soft |

## Abstract

**Title:** Beyond sensitization: real-world validation of an ontology-grounded large language model pipeline converting allergy test reports into patient-centered, interoperable records

**Rationale:** Allergy tests detect sensitization, not clinical allergy, yet patients receive uninterpreted image reports. Language models can digitize these reports, but misreads and hallucinations threaten safety.

**Methods:** Multimodal language models extracted results from report images with double reading, rule-based flags and mandatory human confirmation. For each sensitized allergen, allergen-specific questions (exposure, seasonality, reproducibility) and component-based cross-reactivity screening (e.g., pollen-food) classified clinical relevance. Reports and infographic cards were assembled from an expert-reviewed ontology knowledge graph (12,030 triples; 159 allergens), confining language models to extraction. Results were encoded as HL7 Fast Healthcare Interoperability Resources (FHIR) Observations and symptom status as AllergyIntolerance resources. Extraction was tested against reference transcriptions on 21 synthetic reports (six models) and 29 real-world report pages (seven formats; identifiers masked). Content completeness and concordance were evaluated, and FHIR bundles were checked with the official HL7 FHIR Validator. Primary endpoint: missed positives (exact McNemar test).

**Results:** Verified real-world data yielded 29 personalized questionnaires (431 items), 29 patient reports, 188 infographic cards and 1,517 FHIR resources, viewable at https://allergy.myadr.care/. Synthetic missed-positive rates ranged 0.5–28.0% across models (P<.001). On real-world reports, gpt-5.4 missed 1/98 positives (sensitivity 99.0%; 95% CI 94.4–100) without false positives, whereas an on-premise open-weight model missed 26/98 (P<.001). Content from gpt-5.4 extraction retained 86/89 positive allergens (5 spurious) versus 65/89 for the on-premise model (P<.001). After automated correction of four systematic error types, all 174 FHIR bundles validated without errors.

**Conclusions:** Validated extraction, allergen-specific questioning and knowledge-graph-grounded generation turn sensitization results into patient-centered, interoperable allergy records. Model choice determines extraction safety.

| 섹션 | 단어 수 |
|---|---|
| Rationale | 25 |
| Methods | 118 |
| Results | 85 |
| Conclusions | 20 |
| **합계** | **248 / 250** |

## 제출 양식 기재값

| 항목 | 기재값 |
|---|---|
| Use of Generative AI | Yes, I have used Generative AI |
| Generative AI 설명(Comments) | A generative AI assistant (Anthropic Claude) was used to help draft and edit the abstract text and check the word count. The authors designed the study, produced and verified all data and results, and take full responsibility for the content. Large language models (gpt-5.4 and an on-premise open-weight model) are also the subject of the study, as described in Methods. |
| Phase 1 abstract | No |
| Case report 아님 확인 | 확인함 |
| Primary author 거주 국가 | Korea, Republic of |
| Funding | Self-Funded (기관명 칸: CBNUH) |
| Award category (primary author) | None of the Above |
| IRB Review | Yes |
| Informed Consent | N/A |

**Late-breaking 사유 (제출 원문):** All reported results were generated after the regular deadline (September 8 to November 2, 2026): real-world validation of language-model extraction on 29 allergy test reports from seven laboratory and hospital formats, regeneration and evaluation of patient-facing content, and official HL7 FHIR Validator testing. The work addresses a timely safety question for AI in allergy care, namely whether automated digitization misses positive test results, and shows how sensitization can be translated into patient-centered, interoperable allergy records.

## 초록 수치의 출처 (논문화 때 원자료 확인용)

| 초록 수치 | 원자료 |
|---|---|
| 합성 결과지 21장 · 6개 모델 · 양성 누락률 0.5–28.0% | `docs/ocr_improvement_2026-09-22.md` |
| 실제 결과지 페이지 29장(7개 양식) · 양성 98건 · gpt-5.4 1/98 누락 · 온프레미스 26/98 누락 | `docs/ocr_eval_2026-10-09/summary.json` |
| 콘텐츠 양성 항원 보존 86/89(가짜 5) vs 65/89 | `docs/generated_materials_2026-10-29/` 저장 생성물에서 항원 정규명 기준으로 재계산(2026-11-02). 이전 집계 92/93 vs 66/93은 저장 파일로 재현되지 않음 |
| 문진 29건(431문항) · 리포트 29건 · 카드뉴스 188장 · FHIR 1,517개 | `docs/generated_materials_2026-10-29/` (materials/*/reference) |
| FHIR 번들 174개 · 수정 전 오류 18,785건 → 0건 | `output/fhir_validation_2026-11-02/validation_summary.md` |
| 온톨로지 12,030 트리플 | `docs/overview_2026-10-13_온톨로지_지식그래프_구조.html` |
| 항원 레지스트리 159종 | `data/allergens.json` |
| 95% CI 94.4–100 | Clopper–Pearson (97/98) |

## 메모

- late-breaking 가이드상 1저자가 발표자입니다. 공동 1저자·교신저자를 표시하는 칸은 없습니다.
- 공저자 이메일은 제출 시스템에만 입력했고 이 문서에는 넣지 않았습니다.
- Late-breaking 사유 칸은 "29 allergy test reports"로 제출되어 있습니다(본문은 "29 real-world report pages").
