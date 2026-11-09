# APAAACI 검사값 기반 생성 자료

이 폴더에는 29개 사례와 87개 입력 세트가 있습니다.
자료 파일은 1131개입니다.

## 확인 순서

1. `index.html`을 여세요.
2. 사례 ID를 고르세요.
3. 입력 종류를 고르세요.
4. `values.csv`에서 검사값을 확인하세요.
5. HTML과 PDF 리포트를 확인하세요.
6. 카드뉴스 PDF와 FHIR 파일을 확인하세요.

환자 이름과 날짜는 포함하지 않았습니다.
원본 사진은 포함하지 않았습니다.
문진 답변은 제공되지 않았습니다.
임상 판정 전에 원본과 다시 대조하세요.
각 입력 세트는 생성 파일 13개와 세트 목록을 제공합니다.

## 전체 자료 재생성

```bash
PYTHONPATH=.venv/lib/python3.12/site-packages /Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 docs/generated_materials_2026-10-29/build_materials.py
node docs/generated_materials_2026-10-29/render_pdfs.mjs
PYTHONPATH=.venv/lib/python3.12/site-packages /Users/mingyukang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 docs/generated_materials_2026-10-29/build_catalog.py
```
