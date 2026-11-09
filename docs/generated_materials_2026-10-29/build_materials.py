#!/usr/bin/env python3
"""Generate 87 de-identified research-preview material sets from saved OCR inputs."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import socket
import sys
from contextlib import contextmanager
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
MATERIALS = OUT / "materials"
SOURCE = ROOT / "output" / "ocr_eval_2026-10-09"
EVAL = ROOT / "docs" / "ocr_comparison_2026-10-29" / "evaluation.json"
PREVIEW_DIR = ROOT / "docs" / "ocr_eval_2026-10-09" / "img"
GENERATED_AT = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
BANNER = "검사값 기반 연구용 미리보기 · 문진 미제공 · 원본 대조/임상 검토 전"
NO_SCREENING_SENTENCE = "문진 답변이 없어 임상적 관련성을 평가하지 않았습니다."

SOURCES = (
    {"key": "reference", "label": "참조 전사", "model": None},
    {"key": "openai", "label": "저장 OpenAI OCR", "model": "gpt-5.4"},
    {"key": "ollama", "label": "저장 Local OCR", "model": "qwen3.8:27b"},
)
SOURCE_BY_KEY = {x["key"]: x for x in SOURCES}

SERVICE_AND_KNOWLEDGE_PATHS = (
    "models/schemas.py",
    "services/relevance_service.py",
    "services/report_service.py",
    "services/report_design.py",
    "services/cardnews_service.py",
    "services/fhir_service.py",
    "services/questionnaire_service.py",
    "services/knowledge_service.py",
    "services/environment_control_service.py",
    "services/ontology_service.py",
    "utils/allergen_mapper.py",
    "instructions/allergen_map_prompt_v2.json",
    "data/allergen_knowledge_base.json",
    "data/allergen_knowledge_generated.json",
    "data/allergen_environment_control.json",
    "data/cdm_snomed_mapping.json",
    "data/snomed_ct_map.json",
    "docs/20260916-allergy-chatbot/ontology-snapshot.json",
)

CSV_FIELDS = (
    "index", "printed_no", "allergen_name", "korean_name", "category", "subcategory",
    "value", "value_text", "value_raw", "unit", "class_value", "size_text", "mean_mm",
    "wheal_major_mm", "wheal_minor_mm", "ah_ratio", "interpretation", "confidence",
    "review_flags", "raw_text",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


@contextmanager
def network_guard():
    """Reject every socket connection while local services generate materials."""
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_create = socket.create_connection

    def blocked(*_args, **_kwargs):
        raise RuntimeError("Network access is disabled for generated materials")

    socket.socket.connect = blocked  # type: ignore[method-assign]
    socket.socket.connect_ex = blocked  # type: ignore[method-assign]
    socket.create_connection = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[method-assign]
        socket.create_connection = original_create  # type: ignore[assignment]


def dump_model(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=False)
    return obj.dict(by_alias=False)


def clean_row(source: dict[str, Any], index: int, uncertain: bool = False) -> dict[str, Any]:
    """Preserve test fields and remove source free text."""
    flags = list(source.get("review_flags") or [])
    if uncertain and "reference_uncertain" not in flags:
        flags.append("reference_uncertain")
    return {
        "index": source.get("index") if source.get("index") is not None else index,
        "raw_text": "",
        "allergen_name": source.get("allergen_name") or source.get("korean_name") or "",
        "korean_name": source.get("korean_name"),
        "size_text": source.get("size_text"),
        "mean_mm": source.get("mean_mm"),
        "wheal_major_mm": source.get("wheal_major_mm"),
        "wheal_minor_mm": source.get("wheal_minor_mm"),
        "ah_ratio": source.get("ah_ratio"),
        "value": source.get("value"),
        "value_text": source.get("value_text"),
        "unit": source.get("unit"),
        "class_value": source.get("class_value", source.get("class")),
        "category": source.get("category"),
        "subcategory": source.get("subcategory"),
        "interpretation": source.get("interpretation"),
        "confidence": source.get("confidence", 1.0),
        "note": None,
        "printed_no": source.get("printed_no"),
        "value_raw": source.get("value_raw"),
        "value_raw_conflict": bool(source.get("value_raw_conflict", False)),
        "review_flags": flags,
    }


def derive_reference_fields(
    rows: list[dict[str, Any]], test_type: str, histamine_control: float | None
) -> dict[str, Any]:
    """Derive missing app fields for reference rows without changing printed values."""
    from models.schemas import TestType, determine_interpretation
    from services.fhir_service import FHIRService

    parsed_type = TestType(test_type)
    mean_rows: list[int] = []
    interpretation_rows: list[int] = []
    for position, row in enumerate(rows, 1):
        if parsed_type == TestType.SPT and row.get("mean_mm") is None and row.get("size_text"):
            major, minor = FHIRService._parse_size_text(row["size_text"])
            if major is not None:
                row["mean_mm"] = round(major if minor is None else (major + minor) / 2, 2)
                mean_rows.append(position)
        if row.get("interpretation") is None:
            row["interpretation"] = determine_interpretation(
                test_type=parsed_type,
                mean_mm=row.get("mean_mm"),
                class_value=row.get("class_value"),
                value=row.get("value"),
                histamine_control=histamine_control,
            )
            interpretation_rows.append(position)
    return {
        "mean_mm": {"count": len(mean_rows), "row_positions": mean_rows},
        "interpretation": {"count": len(interpretation_rows), "row_positions": interpretation_rows},
        "method": "FHIRService._parse_size_text + models.schemas.determine_interpretation",
        "histamine_control": histamine_control,
        "printed_values_changed": False,
    }


def source_payload_path(doc_id: str, source: str) -> Path:
    if source == "reference":
        return SOURCE / "truth" / f"{doc_id}.json"
    return SOURCE / "runs" / source / f"{doc_id}.json"


def make_ocr(doc_id: str, source: str):
    from models.schemas import OCRResult, PatientInfo, TestType

    payload = read_json(source_payload_path(doc_id, source))
    if source == "reference":
        base = payload
        rows = [clean_row(row, i, bool(row.get("uncertain"))) for i, row in enumerate(base.get("results") or [], 1)]
        patient_source = base.get("patient") or {}
        test_type = base["test_type"]
        derivations = derive_reference_fields(rows, test_type, patient_source.get("histamine_mean_mm"))
        uncertain_indices = [row["index"] for row in rows if "reference_uncertain" in row["review_flags"]]
        metadata = {
            "material_source": "reference_transcription",
            "reference_uncertain_count": len(uncertain_indices),
            "reference_uncertain_indices": uncertain_indices,
            "reference_derivations": derivations,
            "free_text_exported": False,
        }
    else:
        base = payload.get("result") or {}
        rows = [clean_row(row, i) for i, row in enumerate(base.get("results") or [], 1)]
        source_meta = base.get("metadata") or {}
        metadata = {
            "material_source": "saved_ocr_run",
            "backend": payload.get("backend"),
            "model": payload.get("model"),
            "rotate": payload.get("rotate"),
            "double_read_requested": payload.get("double_read"),
            "preprocess_requested": payload.get("preprocess"),
            "text_layer_requested": payload.get("text_layer"),
            "pipeline": {
                key: source_meta.get(key)
                for key in ("source", "model", "backend", "preprocess", "text_layer", "double_read", "review")
                if key in source_meta
            },
            "free_text_exported": False,
        }
        patient_source = base.get("patient") or {}
        test_type = base["test_type"]

    patient = PatientInfo(
        name=doc_id,
        histamine_mean_mm=patient_source.get("histamine_mean_mm"),
        negative_control_mean_mm=patient_source.get("negative_control_mean_mm"),
    )
    return OCRResult(test_type=TestType(test_type), patient=patient, results=rows, metadata=metadata)


def write_values_csv(path: Path, ocr: Any) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in ocr.results:
            row = dump_model(result)
            row["interpretation"] = getattr(result.interpretation, "value", result.interpretation)
            row["review_flags"] = "|".join(result.review_flags or [])
            row["raw_text"] = ""
            writer.writerow({key: row.get(key) for key in CSV_FIELDS})


def edit_no_screening_claim(text: str) -> str:
    text = re.sub(
        r"그중 문진 결과 \*\*실제로 증상을 일으키는 것으로 확인된 알러젠은 \d+개\*\*입니다\.",
        NO_SCREENING_SENTENCE,
        text,
    )
    text = re.sub(
        r"그중 문진 결과\s*(?:<[^>]+>)*실제로 증상을 일으키는 것으로 확인된 알러젠은\s*\d+개(?:<[^>]+>)*입니다\.",
        NO_SCREENING_SENTENCE,
        text,
        flags=re.I,
    )
    return text


def offline_html(text: str) -> str:
    text = re.sub(
        r"<link\b[^>]*(?:rel=[\"']?preconnect|fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"@import\s+(?:url\()?[^;]*(?:fonts\.googleapis|fonts\.gstatic)[^;]*;", "", text, flags=re.I)
    text = edit_no_screening_claim(text)
    banner = (
        '<div role="note" style="position:relative;z-index:9999;padding:12px 18px;'
        'background:#fff4cc;color:#4a3200;border-bottom:2px solid #d9a800;'
        'font:700 15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,\'Segoe UI\',sans-serif;">'
        f"{BANNER}</div>"
    )
    if re.search(r"<body\b[^>]*>", text, flags=re.I):
        text = re.sub(r"(<body\b[^>]*>)", r"\1" + banner, text, count=1, flags=re.I)
    else:
        text = banner + text
    return text


def questions_payload(questionnaire: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "not_answered",
        "screening": None,
        "answers": {},
        "questionnaire": questionnaire,
    }


def evidence_payload(result: Any, mapper: Any, env: Any, ontology: Any) -> dict[str, Any]:
    entries = []
    for assessment in result.assessments:
        mapping = mapper.find_allergen(assessment.allergen_name)
        mapping_data = dump_model(mapping) if mapping is not None else {}
        coding = mapper.get_coding(assessment.allergen_name, assessment.korean_name or "") or {}
        kb = assessment.kb or {}
        group = env.group_for(assessment.category, assessment.allergen_name, assessment.korean_name or "")
        env_summary = env.summary(group) if group else {}
        measures = env.measures(group, limit=5) if group else []
        source_ids = [source_id for measure in measures for source_id in (measure.get("sources") or [])]
        entries.append({
            "allergen_name": assessment.allergen_name,
            "korean_name": assessment.korean_name,
            "relevance": getattr(assessment.relevance, "value", assessment.relevance),
            "kb": kb,
            "kb_source": kb.get("source") or "none",
            "kb_sources": kb.get("sources") or [],
            "kb_review_status": kb.get("review_status") or "none",
            "mapping": mapping_data,
            "coding": coding,
            "environment": {
                "group": group,
                "summary": env_summary,
                "measures": measures,
                "sources": env.sources(source_ids) if group else [],
                "status": env_summary.get("review_status") if env_summary else "none",
            },
            "ontology_environment": ontology.allergen_context(group, per_topic=5) if group else {},
        })
    return {
        "scope": "positive_assessments_only",
        "candidate_policy": "Candidate and review-status fields are retained. Empty objects and 'none' mean no evidence was available.",
        "assessments": entries,
    }


def dependency_provenance() -> list[dict[str, Any]]:
    out = []
    for relative in SERVICE_AND_KNOWLEDGE_PATHS:
        path = ROOT / relative
        if path.exists():
            out.append({"path": relative, "sha256": file_sha256(path)})
    return out


def manifest_file(kind: str, label: str, path: Path, mime: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "path": str(path.relative_to(OUT)),
        "mime": mime,
        "bytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def source_hashes(doc: dict[str, Any], source: str, evaluation_hashes: dict[str, str]) -> dict[str, str]:
    payload = source_payload_path(doc["id"], source)
    original = ROOT / "input_images" / doc["file"]
    preview = PREVIEW_DIR / f"{doc['id']}.jpg"
    for path in (payload, original, preview):
        expected = evaluation_hashes.get(rel(path))
        actual = file_sha256(path)
        if expected is None or expected != actual:
            raise AssertionError(f"Provenance mismatch: {rel(path)}")
    return {
        "source_payload_path": rel(payload),
        "source_payload_sha256": evaluation_hashes[rel(payload)],
        "original_path": rel(original),
        "original_sha256": evaluation_hashes[rel(original)],
        "preview_path": rel(preview),
        "preview_sha256": evaluation_hashes[rel(preview)],
    }


def count_summary(result: Any, bundles: dict[str, Any]) -> dict[str, int]:
    from models.schemas import ClinicalRelevance

    counts = Counter(getattr(a.relevance, "value", str(a.relevance)) for a in result.assessments)
    negatives = Counter(n.status for n in result.tested_negatives)
    return {
        "positive": len(result.assessments),
        "indeterminate": counts[ClinicalRelevance.INDETERMINATE.value],
        "clinician_review": counts[ClinicalRelevance.CLINICIAN_REVIEW.value],
        "negative": negatives["negative"],
        "equivocal": negatives["equivocal"],
        "observation_count": len((bundles["observation_bundle"].get("entry") or [])),
        "allergy_count": len((bundles["allergy_intolerance_bundle"].get("entry") or [])),
    }


def validate_set(ocr: Any, result: Any, bundles: dict[str, Any], report_md: str, report_html: str, card_html: str) -> None:
    from models.schemas import ClinicalRelevance

    if any(a.relevance == ClinicalRelevance.CLINICALLY_RELEVANT for a in result.assessments):
        raise AssertionError("No unanswered questionnaire case may be clinically_relevant")
    allergy_entries = bundles["allergy_intolerance_bundle"].get("entry") or []
    for entry in allergy_entries:
        coding = (((entry.get("resource") or {}).get("verificationStatus") or {}).get("coding") or [])
        if not coding or coding[0].get("code") != "unconfirmed":
            raise AssertionError("Every AllergyIntolerance must be unconfirmed")
    if "screening_bundle" in bundles:
        raise AssertionError("Screening FHIR must not be generated")
    for text in (report_md, report_html, card_html):
        if BANNER not in text:
            raise AssertionError("Research-preview banner is missing")
        if "그중 문진 결과" in text:
            raise AssertionError("Report claims questionnaire confirmation")
    for text in (report_html, card_html):
        low = text.lower()
        if "fonts.googleapis.com" in low or "fonts.gstatic.com" in low or "rel=\"preconnect\"" in low:
            raise AssertionError("External font dependency remains")
    if ocr.patient.test_date or ocr.patient.patient_id_external or ocr.patient.ordering_provider:
        raise AssertionError("Patient identifiers or dates remain in input")


def main() -> None:
    from services.knowledge_service import KnowledgeService
    import services.knowledge_service as knowledge_module
    from services.relevance_service import RelevanceService
    from services.report_service import ReportService
    from services.cardnews_service import CardNewsService
    from services.fhir_service import FHIRService
    from services.questionnaire_service import get_questionnaire_engine
    from services.environment_control_service import get_environment_control_service
    from services.ontology_service import get_ontology_service
    from utils.allergen_mapper import get_allergen_mapper

    evaluation = read_json(EVAL)
    evaluation_hashes = {item["path"]: item["sha256"] for item in evaluation["meta"]["provenance"]}
    documents = evaluation["documents"]
    if len(documents) != 29:
        raise AssertionError("Expected 29 source documents")

    if MATERIALS.exists():
        shutil.rmtree(MATERIALS)
    MATERIALS.mkdir(parents=True)

    knowledge = KnowledgeService(enable_web=False)
    knowledge_module._knowledge_service = knowledge
    relevance_service = RelevanceService()
    relevance_service.kb = knowledge
    report_service = ReportService(api_key="your_openai_api_key_here")
    card_service = CardNewsService()
    fhir_service = FHIRService()
    questionnaire_engine = get_questionnaire_engine()
    env_service = get_environment_control_service()
    ontology_service = get_ontology_service()
    mapper = get_allergen_mapper()
    dependencies = dependency_provenance()
    records = []

    with network_guard():
        for doc in documents:
            for source_info in SOURCES:
                source = source_info["key"]
                record_dir = MATERIALS / doc["id"] / source
                record_dir.mkdir(parents=True)
                ocr = make_ocr(doc["id"], source)
                result = relevance_service.build_assessments(ocr, None)
                relevance_service.classify_all(result)
                questionnaire = questionnaire_engine.build(result, None)
                patient_info = {"name": doc["id"]}
                bundles = fhir_service.build_bundles_from_relevance(ocr, result, None, [], {})

                report_md = report_service.build_patient_report_markdown(result, patient_info, None, lang="ko")
                report_md = f"> **{BANNER}**\n\n" + edit_no_screening_claim(report_md)
                report_html = offline_html(
                    report_service.build_patient_report_html_document(result, patient_info, None, lang="ko")
                )
                card_html = offline_html(card_service.generate_html(result, patient_info, None))

                input_path = record_dir / "input.json"
                values_path = record_dir / "values.csv"
                assessment_path = record_dir / "assessment.json"
                report_md_path = record_dir / "report.md"
                report_html_path = record_dir / "report.html"
                card_path = record_dir / "cardnews.html"
                observation_path = record_dir / "fhir_observation.json"
                allergy_path = record_dir / "fhir_allergy.json"
                evidence_path = record_dir / "evidence.json"
                questions_path = record_dir / "questions.json"
                provenance_path = record_dir / "provenance.json"

                write_json(input_path, dump_model(ocr))
                write_values_csv(values_path, ocr)
                write_json(assessment_path, dump_model(result))
                report_md_path.write_text(report_md + "\n", encoding="utf-8")
                report_html_path.write_text(report_html, encoding="utf-8")
                card_path.write_text(card_html, encoding="utf-8")
                write_json(observation_path, bundles["observation_bundle"])
                write_json(allergy_path, bundles["allergy_intolerance_bundle"])
                write_json(evidence_path, evidence_payload(result, mapper, env_service, ontology_service))
                write_json(questions_path, questions_payload(questionnaire))

                hashes = source_hashes(doc, source, evaluation_hashes)
                provenance = {
                    "generated_at": GENERATED_AT,
                    "material_status": "current_local_service_generation",
                    "case_id": doc["id"],
                    "source": source,
                    "model": source_info["model"],
                    **hashes,
                    "dependencies": dependencies,
                    "transformations": [
                        "Patient identity, birth data, chart number, source dates, and source free text were removed.",
                        "Patient display was replaced with the case ID.",
                        "Reference rows received required index/raw_text fields; raw_text is an empty string.",
                        "Reference uncertain rows were retained with reference_uncertain review_flags and metadata.",
                        "Reference-only missing mean_mm was derived from printed size_text with FHIRService._parse_size_text.",
                        "Reference-only missing interpretation was derived with determine_interpretation and the reference histamine control.",
                        "Saved OCR values were not corrected against the reference transcription.",
                        "Screening was None and questionnaire answers were empty.",
                        f"Export copy replaced questionnaire-confirmation wording with: {NO_SCREENING_SENTENCE}",
                        "External font links and preconnect tags were removed from generated HTML.",
                    ],
                    "interpretation_note": (
                        "Reference-transcription OCR performance and current-service relevance classification are different evaluations. "
                        "These materials must not be interpreted as clinical validation."
                    ),
                    "network": "blocked_by_socket_guard",
                    "screening": None,
                    "answers": {},
                }
                if source == "reference":
                    provenance["reference_derivations"] = ocr.metadata.get("reference_derivations")
                write_json(provenance_path, provenance)

                validate_set(ocr, result, bundles, report_md, report_html, card_html)
                output_files = (
                    ("input_json", "익명화 실제 입력", input_path, "application/json"),
                    ("values_csv", "전체 검사값", values_path, "text/csv"),
                    ("assessment_json", "전체 판정 구조", assessment_path, "application/json"),
                    ("report_markdown", "연구용 리포트 Markdown", report_md_path, "text/markdown"),
                    ("report_html", "연구용 리포트 HTML", report_html_path, "text/html"),
                    ("cardnews_html", "연구용 카드뉴스 HTML", card_path, "text/html"),
                    ("fhir_observation", "FHIR Observation Bundle", observation_path, "application/fhir+json"),
                    ("fhir_allergy", "FHIR AllergyIntolerance Bundle", allergy_path, "application/fhir+json"),
                    ("evidence_json", "근거·매핑·온톨로지", evidence_path, "application/json"),
                    ("questions_json", "미응답 문진 질문", questions_path, "application/json"),
                    ("provenance_json", "생성 출처", provenance_path, "application/json"),
                )
                summary = count_summary(result, bundles)
                if source == "reference" and doc["test_type"] == "SPT":
                    expected_positive = doc["truth_positive"]
                    if summary["positive"] != expected_positive:
                        raise AssertionError(
                            f"Reference SPT positive mismatch for {doc['id']}: "
                            f"service={summary['positive']} evaluator={expected_positive}"
                        )
                records.append({
                    "key": f"{doc['id']}__{source}",
                    "id": doc["id"],
                    "source": source,
                    "model": source_info["model"],
                    "test_type": doc["test_type"],
                    "lab": doc["lab"],
                    "input_rows": len(ocr.results),
                    "uncertain_rows": sum("reference_uncertain" in row.review_flags for row in ocr.results),
                    "summary": summary,
                    **hashes,
                    "files": [manifest_file(*spec) for spec in output_files],
                })

    manifest = {
        "meta": {
            "generated_at": GENERATED_AT,
            "material_status": "current_local_service_generation",
            "generation_note_ko": "저장된 29사례의 실제 참조 전사와 두 OCR 결과를 현재 로컬 서비스로 생성한 87세트입니다.",
            "documents": 29,
            "variants": len(records),
            "sources": list(SOURCES),
            "network": "blocked_by_socket_guard",
            "screening": None,
            "answers": {},
        },
        "records": records,
    }
    if len(records) != 87:
        raise AssertionError(f"Expected 87 variants, got {len(records)}")
    write_json(OUT / "manifest.json", manifest)


if __name__ == "__main__":
    main()
