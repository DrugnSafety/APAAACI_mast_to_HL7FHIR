# 맞춤 리포트 디자인 스킬 (Report Design Skill)

맞춤 리포트를 **PDF 버전과 HTML 버전 두 가지로, 항상 동일하고 일관된 디자인**으로 만들기
위한 단일 소스 스킬입니다. 두 버전이 서로 어긋나지 않도록 **하나의 템플릿**에서 화면·인쇄를
모두 처리합니다.

## 왜 스킬로 분리했나
- PDF 와 HTML 을 각각 따로 만들면 폰트·여백·색이 달라져 일관성이 깨진다.
- `services/report_design.py` 하나가 **디자인의 유일한 소스**가 되어, 화면 표시·HTML 다운로드·
  PDF(인쇄) 세 경로가 전부 같은 결과를 낸다.

## 구성
- `services/report_design.py` — `render_report_document(title, meta, summary, body_html)`
  - 표지(cover) 헤더: 이름·검사일·검사기관 등
  - 상태 요약 타일: 🔴 실제 주의 / ⚪ 감작만 / 🟡 관찰 필요 (앱 UI 와 동일한 색 의미)
  - 본문: 감별 결과 마크다운을 HTML 로 렌더 (섹션 page-break-inside:avoid)
  - 자체 완결형(embedded CSS) → 다운로드한 `.html` 이 어디서나 동일하게 열림
  - `@media print`: A4 여백, 표지 흑백 친화, 인쇄 버튼 숨김 → 브라우저 ‘PDF로 저장’으로 PDF 생성
- `services/report_service.build_patient_report_html_document(...)` — 마크다운 리포트를
  위 템플릿으로 감싸 완결형 문서를 반환.
- `server.py /api/classify` → 응답의 `report_document_html`.
- `web/app.js` 리포트 탭 — 문서를 iframe 으로 표시 + 버튼 3개:
  - **🖨️ PDF로 저장 / 인쇄** — iframe 을 그대로 인쇄(대상: ‘PDF로 저장’) → HTML 과 100% 동일 레이아웃
  - **🌐 HTML 저장** — 완결형 HTML 파일 다운로드
  - **📝 Markdown 저장** — 원문 마크다운

## 디자인 토큰
| 의미 | 색 | 용도 |
|------|----|----|
| 실제 주의(clinically_relevant) | `#e5484d` 레드 | 증상 유발 알러젠 |
| 감작만(sensitized_only) | `#8a90a2` 그레이 | 과도한 회피 불필요 |
| 관찰 필요(indeterminate) | `#e0a400` 앰버 | 추가 관찰로 판정 |
| 브랜드 | `#6d5efb` | 표지·강조 |

## 확장 아이디어
- 병원 로고/워터마크를 표지에 삽입(자체 완결형 유지 위해 data: URI 권장).
- 외부 디자인 시스템(MengTo/Skills 등) 참고 시에도 이 파일의 토큰·구조만 교체하면
  두 버전 일관성이 유지된다.
