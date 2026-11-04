#!/usr/bin/env python3
"""Package authored drafts without copying source consent forms or patient scans."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

IRB = Path(__file__).resolve().parent
DOCS = IRB.parent
COOP = DOCS / "medical_information_cooperation_2026-10-29"
VALIDATION = IRB / "validation"
VALIDATION.mkdir(exist_ok=True)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(text):
    return re.sub(r"[^\w가-힣]", "", unicodedata.normalize("NFKC", text))


def docx_check(source):
    target = source.with_suffix(".docx")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            if name.endswith(".xml"):
                ET.fromstring(archive.read(name))
        doc = ET.fromstring(archive.read("word/document.xml"))
        styles = ET.fromstring(archive.read("word/styles.xml"))
        style_ids = [x.attrib[W + "styleId"] for x in styles.findall(W + "style")]
        assert len(style_ids) == len(set(style_ids)), "Duplicate Word style IDs"
    text = "".join(doc.itertext())
    haystack = normalized(text)
    checked, missing = 0, []
    in_code = False
    for line in source.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        visible = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        visible = re.sub(r"^\s*(?:#{1,6}|>|[-*]|\d+\.)\s*", "", visible)
        visible = re.sub(r"^\[[ xX]\]\s*", "", visible)
        if not in_code:
            visible = visible.replace("`", "").replace("**", "")
        needle = normalized(visible)
        if len(needle) < 4 or not re.search(r"[가-힣a-zA-Z]", needle):
            continue
        checked += 1
        if needle not in haystack:
            missing.append(visible[:110])
    for table in doc.iter(W + "tbl"):
        width = table.find(f"{W}tblPr/{W}tblW")
        grid = table.find(W + "tblGrid")
        assert width is not None and width.attrib[W + "type"] == "dxa"
        assert grid is not None
        assert sum(int(c.attrib[W + "w"]) for c in grid) == int(width.attrib[W + "w"])
    return {"source": str(source.relative_to(DOCS)), "checked_lines": checked,
            "missing_lines": missing, "xml_valid": True,
            "layout": "Word/LibreOffice 직접 렌더 미확인. HTML과 PDF는 별도 검증."}


sources = sorted((IRB / "content").glob("*.md")) + [COOP / "cooperation_request.md"]
assert len(sources) == 13
checks = [docx_check(s) for s in sources]
assert not any(c["missing_lines"] for c in checks), json.dumps(checks, ensure_ascii=False)

for source in sources:
    html = source.with_suffix(".html") if source.parent.name == "content" else COOP / "index.html"
    assert html.exists() and source.with_suffix(".docx").stat().st_size > 5000

groups = {
    "irb": [IRB / n for n in ("index.html", "README.md", "study_config.json", "document_manifest.json")]
    + [p for p in (IRB / "content").iterdir() if p.suffix in {".md", ".html", ".docx", ".pdf"}],
    "cooperation": [COOP / n for n in ("index.html", "cooperation_request.md", "cooperation_request.docx", "implementation_inventory.json", "notion_page.json")]
    + ([COOP / "cooperation_request.pdf"] if (COOP / "cooperation_request.pdf").exists() else []),
}
packages = []
for group, paths in groups.items():
    target = IRB / "APAAACI_IRB_draft_2026-10-29.zip" if group == "irb" else COOP / "APAAACI_medical_information_cooperation.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for item in sorted(paths):
            archive.write(item, item.relative_to(DOCS))
        archive.writestr("README.txt", "index.html은 각 문서 폴더 안에 있습니다.\n다른 자료 묶음 링크는 원래 프로젝트 폴더에서 사용하십시오.\n원본 환자 스캔과 기존 연구 문서 원문은 포함하지 않았습니다.\n모든 서류는 승인 전 초안입니다.\n")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all("references/" not in n and "input_images/" not in n for n in archive.namelist())
    packages.append({"path": str(target.relative_to(DOCS)), "bytes": target.stat().st_size,
                     "sha256": digest(target), "members": len(paths) + 1})

files = [p for group in groups.values() for p in group]
report = {"date": "2026-10-29", "docx_files": len(checks), "docx_checks": checks,
          "packages": packages, "files": [{"path": str(p.relative_to(DOCS)),
          "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(files)]}
(VALIDATION / "document_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"docx_xml": len(checks), "text_lines": sum(c["checked_lines"] for c in checks), "missing_lines": 0, "packages": packages}, ensure_ascii=False))
