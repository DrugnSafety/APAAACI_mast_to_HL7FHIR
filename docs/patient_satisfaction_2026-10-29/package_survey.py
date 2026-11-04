#!/usr/bin/env python3
"""Validate and package the authored survey without copying reference forms."""
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
Q = json.loads((ROOT / "survey_definition.json").read_text())
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
assert len(Q["items"]) == 6
assert (ROOT / "patient_satisfaction.doc").read_bytes()[:8].hex() == "d0cf11e0a1b11ae1"
with zipfile.ZipFile(ROOT / "patient_satisfaction.docx") as archive:
    assert archive.testzip() is None
    for name in archive.namelist():
        if name.endswith(".xml"):
            ET.fromstring(archive.read(name))
    document = ET.fromstring(archive.read("word/document.xml"))
    docx_text = "".join(document.itertext())
    for table in document.iter(W + "tbl"):
        width = table.find(f"{W}tblPr/{W}tblW")
        columns = table.find(W + "tblGrid")
        assert width.attrib[W + "type"] == "dxa"
        assert sum(int(c.attrib[W + "w"]) for c in columns) == int(width.attrib[W + "w"])
doc_text = subprocess.check_output(["/usr/bin/textutil", "-convert", "txt", "-stdout", str(ROOT / "patient_satisfaction.doc")], text=True)
pdf_text = subprocess.check_output(["pdftotext", str(ROOT / "patient_satisfaction.pdf"), "-"], text=True)
texts = {"DOC": doc_text, "DOCX": docx_text, "PDF": pdf_text,
         "HTML": (ROOT / "patient_satisfaction.html").read_text(),
         "Markdown": (ROOT / "questionnaire.md").read_text()}
for kind, text in texts.items():
    for item in Q["items"]:
        assert re.sub(r"\s", "", item["text"]) in re.sub(r"\s", "", text), (kind, item["id"])
irb = json.loads((ROOT.parent / "irb_2026-10-29/study_config.json").read_text())["patient_satisfaction_survey"]
assert {k: v for k, v in irb.items() if k != "source_definition"} == Q
notion = json.loads((ROOT / "notion_page.json").read_text())
assert notion["id"] and notion["parent_page_id"] == "40602c77-e2c9-81aa-958c-df0410540324"
browser = json.loads((ROOT / "validation/browser_validation.json").read_text())
assert browser["failures"] == 0

names = ["index.html", "patient_satisfaction.html", "patient_satisfaction.doc",
         "patient_satisfaction.docx", "patient_satisfaction.pdf", "questionnaire.md",
         "implementation_guide.html", "implementation_guide.md", "survey_definition.json",
         "sources.json", "notion_page.json", "notion_task.md", "README.md",
         "survey.js", "survey.template", "build_survey.mjs", "verify_survey.mjs",
         "package_survey.py", "validation/browser_validation.json"]
manifest = {"survey_id": Q["id"], "version": Q["version"], "items": 6,
            "binary_doc_valid": True, "word_native_render": False,
            "pdf_source": "HTML print layout", "clinical_deployment": False,
            "files": [{"path": n, "bytes": (ROOT / n).stat().st_size,
                       "sha256": hashlib.sha256((ROOT / n).read_bytes()).hexdigest()} for n in names]}
(ROOT / "document_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
target = ROOT / "APAAACI_patient_satisfaction.zip"
with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in names + ["document_manifest.json"]:
        archive.write(ROOT / name, f"{ROOT.name}/{name}")
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    members = len(archive.namelist())
result = {"items_consistent": 6, "formats_checked": list(texts), "irb_definition_matches": True,
          "binary_doc_valid": True, "browser_checks": browser["checks"],
          "zip_members": members, "zip_bytes": target.stat().st_size,
          "zip_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
          "word_native_render": False, "hospital_transmission_tested": False}
(ROOT / "validation/package_validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(result, ensure_ascii=False))
