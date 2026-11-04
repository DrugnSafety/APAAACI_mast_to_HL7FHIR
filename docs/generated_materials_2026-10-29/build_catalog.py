#!/usr/bin/env python3
"""Build the offline catalog, per-set manifests, and one complete ZIP package."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MATERIALS = OUT / "materials"
DOWNLOADS = OUT / "downloads"
VALIDATION = OUT / "validation"
EVALUATION = ROOT / "docs" / "ocr_comparison_2026-10-29" / "evaluation.json"
BASE_CSS_SOURCE = ROOT / "docs" / "ocr_comparison_2026-10-29" / "guide_base.css"
MAX_PREVIEW_CHARS = 8_000
MAX_DOWNLOAD_BYTES = 650 * 1024 * 1024

KIND_MAP = {
    "input_json": "input",
    "assessment_json": "assessment",
    "report_markdown": "report_md",
    "evidence_json": "evidence",
    "questions_json": "questions",
    "provenance_json": "provenance",
}
PREVIEW_KINDS = ("fhir_observation", "fhir_allergy", "evidence", "provenance")
ROOT_PACKAGE_FILES = ("index.html", "guide.html", "catalog.csv", "catalog.json", "README.md")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_catalog(template: str, base_css: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return template.replace("__BASE_CSS__", base_css).replace("__DATA__", payload)


def render_guide(template: str, base_css: str, file_count: int, package_mode: bool) -> str:
    return (
        template.replace("__BASE_CSS__", base_css)
        .replace("__FILE_COUNT__", str(file_count))
        .replace("__PACKAGE_MODE__", "true" if package_mode else "false")
    )


def catalog_csv(records: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    fields = (
        "case_id", "source", "model", "test_type", "lab", "input_rows", "uncertain_rows",
        "kind", "label", "filename", "path", "mime", "bytes", "sha256",
    )
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for record in records:
        for item in record["files"]:
            writer.writerow({
                "case_id": record["id"],
                "source": record["source"],
                "model": record["model"],
                "test_type": record["test_type"],
                "lab": record["lab"],
                "input_rows": record["input_rows"],
                "uncertain_rows": record["uncertain_rows"],
                **{key: item[key] for key in ("kind", "label", "filename", "path", "mime", "bytes", "sha256")},
            })
    return output.getvalue()


def readme_text(case_count: int, record_count: int, file_count: int, package_mode: bool) -> str:
    scope = "이 ZIP" if package_mode else "이 폴더"
    return f"""# APAAACI 검사값 기반 생성 자료

{scope}에는 {case_count}개 사례와 {record_count}개 입력 세트가 있습니다.
자료 파일은 {file_count}개입니다.

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
"""


def normalize_records(agent_manifest: dict[str, Any], pdf_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    pdf_by_key: dict[str, list[dict[str, Any]]] = {}
    for item in pdf_manifest["files"]:
        pdf_by_key.setdefault(item["key"], []).append(item)
    records = []
    for source_record in agent_manifest["records"]:
        record = {key: value for key, value in source_record.items() if key != "files"}
        files = []
        for source_file in source_record["files"] + sorted(pdf_by_key.get(record["key"], []), key=lambda x: x["kind"]):
            item = dict(source_file)
            item["kind"] = KIND_MAP.get(item["kind"], item["kind"])
            item["filename"] = PurePosixPath(item["path"]).name
            item.pop("html_sha256", None)
            material_path = OUT / item["path"]
            item["bytes"] = material_path.stat().st_size
            item["sha256"] = sha256(material_path)
            files.append(item)
        expected_files = 13 if pdf_manifest["files"] else 11
        if len(files) != expected_files:
            raise AssertionError(f"Expected {expected_files} files for {record['key']}, got {len(files)}")
        record["files"] = files
        record["manifest_path"] = f"materials/{record['id']}/{record['source']}/set_manifest.json"
        input_data = read_json(OUT / next(item["path"] for item in files if item["kind"] == "input"))
        record["values"] = input_data.get("results") or []
        previews = {}
        for kind in PREVIEW_KINDS:
            item = next(value for value in files if value["kind"] == kind)
            text = (OUT / item["path"]).read_text(encoding="utf-8")
            previews[kind] = {"text": text[:MAX_PREVIEW_CHARS], "truncated": len(text) > MAX_PREVIEW_CHARS}
        record["previews"] = previews
        records.append(record)
    return records


def cases_from_evaluation(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: document[key] for key in ("id", "test_type", "lab", "truth_rows", "uncertain_rows")}
        for document in evaluation["documents"]
    ]


def make_data(
    meta: dict[str, Any], cases: list[dict[str, Any]], records: list[dict[str, Any]],
    packages: list[dict[str, Any]], package_mode: bool, package_type: str | None = None,
    package_key: str | None = None,
) -> dict[str, Any]:
    current_meta = dict(meta)
    current_meta.update({
        "documents": len(cases),
        "variants": len(records),
        "file_count": sum(len(record["files"]) for record in records),
    })
    if package_type:
        current_meta.update({"package_type": package_type, "package_key": package_key})
    return {
        "meta": current_meta,
        "cases": cases,
        "records": records,
        "packages": packages,
        "package_mode": package_mode,
    }


def write_set_manifests(
    records: list[dict[str, Any]], pdf_manifest: dict[str, Any], generated_at: str
) -> None:
    pdf_by_key = {}
    for item in pdf_manifest["files"]:
        pdf_by_key.setdefault(item["key"], []).append(item)
    for record in records:
        provenance = next(item for item in record["files"] if item["kind"] == "provenance")
        pdf_sources = []
        for pdf in sorted(pdf_by_key.get(record["key"], []), key=lambda item: item["kind"]):
            html_path = pdf["path"].removesuffix(".pdf") + ".html"
            pdf_sources.append({
                "pdf_kind": pdf["kind"],
                "pdf_path": pdf["path"],
                "pdf_sha256": pdf["sha256"],
                "source_html_path": html_path,
                "source_html_sha256": pdf["html_sha256"],
            })
        payload = {
            "generated_at": generated_at,
            "key": record["key"],
            "id": record["id"],
            "source": record["source"],
            "model": record["model"],
            "test_type": record["test_type"],
            "lab": record["lab"],
            "source_payload_path": record["source_payload_path"],
            "source_payload_sha256": record["source_payload_sha256"],
            "original_sha256": record["original_sha256"],
            "preview_sha256": record["preview_sha256"],
            "files": record["files"],
            "pdf_sources": pdf_sources,
            "provenance": {key: provenance[key] for key in ("path", "bytes", "sha256")},
        }
        (OUT / record["manifest_path"]).write_text(json_text(payload), encoding="utf-8")


def package_filename(package_type: str, key: str | None = None) -> str:
    if package_type == "all":
        return "APAAACI_all_87_sets.zip"
    if package_type == "case":
        return f"APAAACI_{key}_3_sets.zip"
    return f"APAAACI_{key}.zip"


def add_package_roots(
    archive: zipfile.ZipFile, data: dict[str, Any], catalog_template: str,
    guide_template: str, base_css: str,
) -> None:
    records = data["records"]
    archive.writestr("index.html", render_catalog(catalog_template, base_css, data))
    archive.writestr("guide.html", render_guide(guide_template, base_css, sum(len(r["files"]) for r in records), True))
    archive.writestr("catalog.csv", catalog_csv(records))
    archive.writestr("catalog.json", json_text(data))
    archive.writestr("README.md", readme_text(len(data["cases"]), len(records), sum(len(r["files"]) for r in records), True))


def build_zip(
    path: Path, data: dict[str, Any], catalog_template: str, guide_template: str, base_css: str
) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        add_package_roots(archive, data, catalog_template, guide_template, base_css)
        for record in data["records"]:
            for item in record["files"]:
                archive.write(OUT / item["path"], item["path"])
            archive.write(OUT / record["manifest_path"], record["manifest_path"])


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.external: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            is_asset = key in {"src", "poster"} or (tag == "link" and key == "href")
            if is_asset and value and value.lower().startswith(("http://", "https://", "//")):
                self.external.append((tag, key, value))


def sanitize_material_html() -> int:
    """Remove remote stylesheet links while preserving ordinary reference hyperlinks."""
    changed = 0
    pattern = re.compile(r"<link\b[^>]*href=[\"'](?:https?:)?//[^\"']+[\"'][^>]*>\s*", re.I)
    for path in MATERIALS.rglob("*.html"):
        original = path.read_text(encoding="utf-8")
        sanitized = pattern.sub("", original)
        if sanitized != original:
            path.write_text(sanitized, encoding="utf-8")
            changed += 1
    return changed


def refresh_agent_manifest(
    agent_manifest: dict[str, Any], sanitized_html_files: int
) -> int:
    """Keep the source-generation manifest aligned with offline-sanitized files."""
    prior = int(agent_manifest["meta"].get("offline_font_stylesheet_files_removed") or 0)
    previous_catalog = OUT / "catalog.json"
    if previous_catalog.exists():
        prior = max(
            prior,
            int((read_json(previous_catalog).get("meta") or {}).get("sanitized_external_stylesheet_files") or 0),
        )
    total = max(prior, sanitized_html_files)
    agent_manifest["meta"].update({
        "offline_html_verified": True,
        "offline_font_stylesheet_files_removed": total,
        "offline_font_note_ko": "카드뉴스의 외부 CDN 글꼴 링크를 제거하고 시스템 글꼴로 대체했습니다.",
    })
    for record in agent_manifest["records"]:
        for item in record["files"]:
            path = OUT / item["path"]
            item["bytes"] = path.stat().st_size
            item["sha256"] = sha256(path)
    (OUT / "manifest.json").write_text(json_text(agent_manifest), encoding="utf-8")
    return total


def validate_external_assets(paths: list[Path]) -> list[dict[str, Any]]:
    failures = []
    for path in paths:
        parser = AssetParser()
        parser.feed(path.read_text(encoding="utf-8"))
        if parser.external:
            failures.append({"path": str(path.relative_to(OUT)), "assets": parser.external})
    return failures


def validate_phi(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    hits = []
    for record in records:
        source = read_json(ROOT / record["source_payload_path"])
        base = source if record["source"] == "reference" else source.get("result") or {}
        patient = base.get("patient") or {}
        secrets = []
        for key in ("name", "patient_id_external", "birth", "ordering_provider", "accession"):
            value = patient.get(key)
            if value is not None and len(str(value).strip()) >= 2 and str(value).strip() != record["id"]:
                secrets.append((key, str(value).strip()))
        text_files = [item for item in record["files"] if item["mime"].startswith("text/") or item["mime"].endswith("json")]
        combined = "\n".join((OUT / item["path"]).read_text(encoding="utf-8", errors="ignore") for item in text_files)
        for field, secret in secrets:
            if secret in combined:
                hits.append({"key": record["key"], "field": field, "value": secret})
    return hits


def validate(
    data: dict[str, Any], package_specs: list[tuple[dict[str, Any], list[dict[str, Any]]]]
) -> dict[str, Any]:
    records = data["records"]
    file_errors = []
    for record in records:
        for item in record["files"]:
            path = OUT / item["path"]
            if not path.exists() or path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
                file_errors.append(item["path"])
    zip_errors = []
    zip_members = 0
    for package, selected in package_specs:
        path = OUT / package["path"]
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            names = archive.namelist()
            zip_members += len(names)
            expected = len(ROOT_PACKAGE_FILES) + sum(len(record["files"]) + 1 for record in selected)
            invalid = [
                name for name in names
                if name.startswith("/") or ".." in PurePosixPath(name).parts or name.lower().endswith(".zip")
                or PurePosixPath(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff"}
            ]
            if bad or len(names) != expected or invalid:
                zip_errors.append({"path": package["path"], "bad_crc": bad, "members": len(names), "expected": expected, "invalid": invalid})
    html_paths = [OUT / "index.html", OUT / "guide.html"]
    html_paths.extend(OUT / item["path"] for record in records for item in record["files"] if item["mime"] == "text/html")
    external_assets = validate_external_assets(html_paths)
    phi_hits = validate_phi(records)
    return {
        "status": "pass" if not (file_errors or zip_errors or external_assets or phi_hits) else "fail",
        "record_count": len(records),
        "case_count": len(data["cases"]),
        "material_file_hashes_checked": sum(len(record["files"]) for record in records),
        "material_file_errors": file_errors,
        "package_count": len(package_specs),
        "zip_members_checked": zip_members,
        "zip_errors": zip_errors,
        "external_html_assets": external_assets,
        "phi_hits": phi_hits,
        "download_bytes": sum(package["bytes"] for package, _ in package_specs),
        "package_types": dict(Counter(package["type"] for package, _ in package_specs)),
    }


def main() -> None:
    pdf_path = OUT / "pdf_manifest.json"
    agent_manifest = read_json(OUT / "manifest.json")
    pdf_manifest = read_json(pdf_path) if pdf_path.exists() else {
        "generated_at": agent_manifest["meta"]["generated_at"],
        "renderer": None,
        "network_requests_blocked": [],
        "files": [],
    }
    evaluation = read_json(EVALUATION)
    if len(pdf_manifest.get("files") or []) not in {0, 174}:
        raise AssertionError(f"Expected 0 or 174 PDFs, got {len(pdf_manifest.get('files') or [])}")
    if pdf_manifest.get("network_requests_blocked"):
        raise AssertionError("PDF rendering attempted external requests")

    base_css = BASE_CSS_SOURCE.read_text(encoding="utf-8")
    catalog_template = (OUT / "catalog_template.html").read_text(encoding="utf-8")
    guide_template = (OUT / "guide_template.html").read_text(encoding="utf-8")
    sanitized_html_files = sanitize_material_html()
    sanitized_html_files = refresh_agent_manifest(agent_manifest, sanitized_html_files)
    records = normalize_records(agent_manifest, pdf_manifest)
    cases = cases_from_evaluation(evaluation)
    meta = {
        **agent_manifest["meta"],
        "catalog_generated_at": pdf_manifest["generated_at"],
        "pdf_renderer": pdf_manifest["renderer"],
        "pdf_status": "included" if pdf_manifest["files"] else "omitted_due_to_disk_limit",
        "sanitized_external_stylesheet_files": sanitized_html_files,
        "package_note_ko": (
            "현재 생성본 87세트와 PDF를 오프라인 패키지로 묶었습니다."
            if pdf_manifest["files"]
            else "현재 생성본 87세트를 PDF 없이 오프라인 패키지로 묶었습니다."
        ),
    }
    write_set_manifests(records, pdf_manifest, pdf_manifest["generated_at"])

    if DOWNLOADS.exists():
        shutil.rmtree(DOWNLOADS)
    DOWNLOADS.mkdir(parents=True)
    if VALIDATION.exists():
        shutil.rmtree(VALIDATION)
    VALIDATION.mkdir(parents=True)

    package_specs: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    selections = [("all", "all", records, cases)]

    total_download_bytes = 0
    for package_type, key, selected_records, selected_cases in selections:
        filename = package_filename(package_type, None if package_type == "all" else key)
        package_path = DOWNLOADS / filename
        package_data = make_data(meta, selected_cases, selected_records, [], True, package_type, key)
        build_zip(package_path, package_data, catalog_template, guide_template, base_css)
        total_download_bytes += package_path.stat().st_size
        if total_download_bytes > MAX_DOWNLOAD_BYTES:
            raise AssertionError(f"Downloads exceed 650 MiB: {total_download_bytes}")
        package = {
            "type": package_type,
            "key": key,
            "path": str(package_path.relative_to(OUT)),
            "bytes": package_path.stat().st_size,
            "sha256": sha256(package_path),
        }
        package_specs.append((package, selected_records))

    packages = [package for package, _ in package_specs]
    full_data = make_data(meta, cases, records, packages, False)
    (OUT / "guide_base.css").write_text(base_css, encoding="utf-8")
    (OUT / "index.html").write_text(render_catalog(catalog_template, base_css, full_data), encoding="utf-8")
    file_count = sum(len(record["files"]) for record in records)
    (OUT / "guide.html").write_text(render_guide(guide_template, base_css, file_count, False), encoding="utf-8")
    (OUT / "catalog.json").write_text(json_text(full_data), encoding="utf-8")
    (OUT / "catalog.csv").write_text(catalog_csv(records), encoding="utf-8-sig")
    (OUT / "README.md").write_text(readme_text(len(cases), len(records), file_count, False), encoding="utf-8")

    validation = validate(full_data, package_specs)
    (VALIDATION / "validation.json").write_text(json_text(validation), encoding="utf-8")
    if validation["status"] != "pass":
        raise AssertionError(json.dumps(validation, ensure_ascii=False))
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
