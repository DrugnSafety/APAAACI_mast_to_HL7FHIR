#!/usr/bin/env python3
"""Build a de-identified OCR comparison dataset from saved evaluation runs."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "output" / "ocr_eval_2026-10-09"
SOURCE_DOCS = ROOT / "docs" / "ocr_eval_2026-10-09"
OUTPUT = Path(__file__).with_name("evaluation.json")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import eval_real_ocr_report as e  # noqa: E402
from models.schemas import normalize_class_token  # noqa: E402


RUNS = ("openai", "ollama")
DROP_ROW_FIELDS = {"note", "raw_text"}
DROP_SCORE_FIELDS = {"rows", "patient", "pos_pairs", "review", "double_read"}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def clean_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in DROP_ROW_FIELDS}


def truth_key(row: dict[str, Any]) -> str:
    """Use the complete scored truth row, not printed_no alone."""
    return json.dumps(e._slim(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def projected_score_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ocr": clean_row(row.get("ocr")),
        "errors": row.get("errors") or [],
        "how": row.get("how"),
        "sim": row.get("sim"),
        "truth_pos": row.get("truth_pos"),
        "ocr_pos": row.get("ocr_pos"),
        "flagged": bool(row.get("flagged")),
    }


def align_rows(
    doc_id: str,
    truth_rows: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    aligned: dict[str, dict[int, dict[str, Any]]] = {}
    extras: dict[str, list[dict[str, Any]]] = {}
    for run in RUNS:
        queues: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        extras[run] = []
        for row in scores[run]["rows"]:
            if row.get("how") in {"duplicate", "extra"}:
                extras[run].append(projected_score_row(row))
            elif row.get("truth") is not None:
                queues[truth_key(row["truth"])].append(row)
        aligned[run] = {}
        for index, truth_row in enumerate(truth_rows):
            queue = queues.get(truth_key(truth_row))
            if not queue:
                raise AssertionError(f"{doc_id}/{run}: reference row {index + 1} was not aligned")
            aligned[run][index] = queue.popleft()
        leftovers = sum(len(q) for q in queues.values())
        if leftovers:
            raise AssertionError(f"{doc_id}/{run}: {leftovers} scored reference rows remain")

    rows = []
    for index, truth_row in enumerate(truth_rows):
        rows.append({
            "uid": f"{doc_id}-r{index + 1:04d}",
            "reference": clean_row(e._slim(truth_row)),
            "openai": projected_score_row(aligned["openai"][index]),
            "ollama": projected_score_row(aligned["ollama"][index]),
        })
    return rows, extras, aligned


def percentage(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 1) if den else None


def strict_score(
    truth: dict[str, Any],
    aligned: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    numeric_all_den = numeric_den = numeric_ok = 0
    class_all_den = class_matched = class_present = class_ok = 0
    if truth["test_type"] == "MAST":
        for index, ref in enumerate(truth.get("results") or []):
            if ref.get("uncertain"):
                continue
            scored = aligned[index]
            got = scored.get("ocr")
            has_numeric = ref.get("value") is not None or ref.get("value_text") not in (None, "")
            if has_numeric:
                numeric_all_den += 1
                if got is not None:
                    numeric_den += 1
                    numeric_ok += int(e.values_equal(ref, got))
            if ref.get("class_value") is not None:
                class_all_den += 1
                if got is not None:
                    class_matched += 1
                    got_class = normalize_class_token(got.get("class_value"))
                    if got_class is not None:
                        class_present += 1
                        class_ok += int(got_class == normalize_class_token(ref.get("class_value")))
    return {
        "numeric_all_den": numeric_all_den,
        "numeric_den": numeric_den,
        "numeric_ok": numeric_ok,
        "numeric_missing": numeric_all_den - numeric_den,
        "numeric_accuracy_matched": percentage(numeric_ok, numeric_den),
        "numeric_accuracy_overall": percentage(numeric_ok, numeric_all_den),
        "class_all_den": class_all_den,
        "class_matched": class_matched,
        "class_present": class_present,
        "class_ok": class_ok,
        "class_unmatched": class_all_den - class_matched,
        "class_missing": class_all_den - class_present,
        "class_ocr_missing": class_matched - class_present,
        "class_accuracy_present": percentage(class_ok, class_present),
        "class_accuracy_overall": percentage(class_ok, class_all_den),
    }


def strict_aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    count_keys = (
        "numeric_all_den", "numeric_den", "numeric_ok", "numeric_missing",
        "class_all_den", "class_matched", "class_present", "class_ok",
        "class_unmatched", "class_missing", "class_ocr_missing",
    )
    out = {key: sum(item[key] for item in items) for key in count_keys}
    out.update({
        "numeric_accuracy_matched": percentage(out["numeric_ok"], out["numeric_den"]),
        "numeric_accuracy_overall": percentage(out["numeric_ok"], out["numeric_all_den"]),
        "class_accuracy_present": percentage(out["class_ok"], out["class_present"]),
        "class_accuracy_overall": percentage(out["class_ok"], out["class_all_den"]),
    })
    return out


def timing_summary(scores: list[dict[str, Any]]) -> dict[str, float | None]:
    values = sorted(float(s["seconds"]) for s in scores if s.get("seconds") is not None and "error" not in s)
    if not values:
        return {"seconds_median": None, "seconds_p90": None, "seconds_max": None}
    p90 = values[max(0, math.ceil(len(values) * 0.9) - 1)]
    return {
        "seconds_median": round(statistics.median(values), 1),
        "seconds_p90": round(p90, 1),
        "seconds_max": round(max(values), 1),
    }


def public_aggregate(
    scores: list[dict[str, Any]],
    stricts: list[dict[str, Any]],
    uncertain_positive_rows: int,
) -> dict[str, Any]:
    out = {k: v for k, v in e.aggregate(scores).items() if k != "patient"}
    out.update(timing_summary(scores))
    out["uncertain_positive_rows"] = uncertain_positive_rows
    out["strict"] = strict_aggregate(stricts)
    return out


def public_score(
    score: dict[str, Any],
    strict: dict[str, Any],
    uncertain_positive_rows: int,
) -> dict[str, Any]:
    out = {k: v for k, v in score.items() if k not in DROP_SCORE_FIELDS}
    out["uncertain_positive_rows"] = uncertain_positive_rows
    out["strict"] = strict
    return out


def controls(truth: dict[str, Any], runs: dict[str, dict[str, Any]]) -> dict[str, float | None]:
    def histamine(obj: dict[str, Any]) -> float | None:
        value = e._num((obj.get("patient") or {}).get("histamine_mean_mm"))
        return value if value is not None else None

    if truth["test_type"] != "SPT":
        return {"truth": None, "openai": None, "ollama": None}
    return {
        "truth": histamine(truth),
        "openai": histamine(runs["openai"].get("result") or {}),
        "ollama": histamine(runs["ollama"].get("result") or {}),
    }


def agreement_aggregate(
    per_document: dict[str, dict[str, Any]],
    aligned_by_document: dict[str, dict[str, dict[int, dict[str, Any]]]],
) -> dict[str, Any]:
    valid = [a for a in per_document.values() if not a.get("error")]
    pos_pairs = [pair for a in valid for pair in a.get("pos_pairs", [])]
    both_right = one_right = both_wrong = 0
    for aligned in aligned_by_document.values():
        for index in aligned["openai"]:
            a = aligned["openai"][index]
            b = aligned["ollama"][index]
            if a.get("ocr") is None or b.get("ocr") is None:
                continue
            a_right = a.get("truth_pos") == a.get("ocr_pos")
            b_right = b.get("truth_pos") == b.get("ocr_pos")
            both_right += int(a_right and b_right)
            both_wrong += int(not a_right and not b_right)
            one_right += int(a_right != b_right)

    def ratio_sum(num: str, den: str) -> float | None:
        numerator = sum(a[num] for a in valid)
        denominator = sum(a[den] for a in valid)
        return percentage(numerator, denominator)

    return {
        "images": len(valid),
        "matched": sum(a["matched"] for a in valid),
        "row_jaccard": ratio_sum("matched", "union"),
        "value_agree": ratio_sum("same_val", "val_pairs"),
        "class_agree": ratio_sum("same_cls", "cls_pairs"),
        "size_agree": ratio_sum("same_size", "size_pairs"),
        "cls_one_missing": sum(a["cls_one_missing"] for a in valid),
        "positivity_agree": percentage(sum(x == y for x, y in pos_pairs), len(pos_pairs)),
        "positivity_kappa": e.kappa(pos_pairs),
        "both_right": both_right,
        "one_right": one_right,
        "both_wrong": both_wrong,
    }


def error_history() -> list[dict[str, Any]]:
    history = []
    for directory in sorted(p for p in (SOURCE / "runs").iterdir() if p.is_dir()):
        errors: Counter[str] = Counter()
        double_read_errors = 0
        files = sorted(directory.glob("*.json"))
        for path in files:
            record = read_json(path)
            if record.get("error"):
                label = str(record["error"]).split(":", 1)[0][:80]
                errors[label] += 1
            dr = (((record.get("result") or {}).get("metadata") or {}).get("double_read") or {})
            double_read_errors += int(isinstance(dr, dict) and bool(dr.get("error")))
        history.append({
            "run": directory.name,
            "documents": len(files),
            "top_errors": [{"error": key, "count": count} for key, count in errors.most_common(5)],
            "double_read_errors": double_read_errors,
        })
    return history


def experiments() -> list[dict[str, Any]]:
    excluded = {"openai", "ollama", "ollama_attempt1_errors", "ollama_firstpass_413"}
    out = []
    for directory in sorted(p for p in (SOURCE / "runs").iterdir() if p.is_dir() and p.name not in excluded):
        files = sorted(directory.glob("*.json"))
        models = sorted({read_json(path).get("model") for path in files if read_json(path).get("model")})
        out.append({"key": directory.name, "documents": len(files), "model": ", ".join(models) or None})
    return out


def main() -> None:
    manifest_path = SOURCE / "manifest.json"
    source_summary_path = SOURCE_DOCS / "summary.json"
    manifest = read_json(manifest_path)["items"]
    source_summary = read_json(source_summary_path)
    matcher = e.Matcher()
    documents = []
    raw_scores: dict[str, list[dict[str, Any]]] = {run: [] for run in RUNS}
    strict_scores: dict[str, list[dict[str, Any]]] = {run: [] for run in RUNS}
    per_doc_scores: dict[str, dict[str, dict[str, Any]]] = {}
    per_doc_strict: dict[str, dict[str, dict[str, Any]]] = {}
    agreements: dict[str, dict[str, Any]] = {}
    aligned_all: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
    provenance_paths = [manifest_path, source_summary_path, ROOT / "scripts" / "eval_real_ocr_report.py"]

    for item in manifest:
        doc_id = item["id"]
        truth_path = SOURCE / "truth" / f"{doc_id}.json"
        truth = read_json(truth_path)
        if truth.get("file") and truth["file"] != item["file"]:
            raise AssertionError(f"{doc_id}: manifest/truth file mismatch")
        runs = {run: read_json(SOURCE / "runs" / run / f"{doc_id}.json") for run in RUNS}
        provenance_paths.extend([
            truth_path,
            *(SOURCE / "runs" / run / f"{doc_id}.json" for run in RUNS),
            ROOT / "input_images" / item["file"],
            SOURCE_DOCS / "img" / f"{doc_id}.jpg",
        ])
        scores = {run: e.score_image(truth, runs[run], matcher) for run in RUNS}
        rows, extra_rows, aligned = align_rows(doc_id, truth.get("results") or [], scores)
        strict = {run: strict_score(truth, aligned[run]) for run in RUNS}
        agreement = e.agreement(truth, runs["openai"], runs["ollama"], matcher)
        lab = truth.get("lab") or ("EMR" if doc_id in {"O02", "O03", "O04", "O05"} else "?")
        lab_key = lab if lab in e.LAYOUT else ("EMR" if "EMR" in lab or (doc_id.startswith("O0") and doc_id != "O01") else lab)
        histamine = e._num((truth.get("patient") or {}).get("histamine_mean_mm"))
        uncertain_positive_rows = sum(
            bool(row.get("uncertain")) and e.is_positive(row, truth["test_type"], histamine) is True
            for row in truth.get("results") or []
        )
        documents.append({
            "id": doc_id,
            "file": item["file"],
            "test_type": truth["test_type"],
            "lab": lab,
            "layout": e.LAYOUT.get(lab_key, ""),
            "page_kind": truth.get("page_kind"),
            "truth_rows": len(truth.get("results") or []),
            "truth_positive": sum(e.is_positive(row, truth["test_type"], histamine) is True for row in truth.get("results") or []),
            "uncertain_rows": sum(bool(row.get("uncertain")) for row in truth.get("results") or []),
            "uncertain_positive_rows": uncertain_positive_rows,
            "controls": controls(truth, runs),
            "scores": {run: public_score(scores[run], strict[run], uncertain_positive_rows) for run in RUNS},
            "rows": rows,
            "extras": extra_rows,
        })
        per_doc_scores[doc_id] = scores
        per_doc_strict[doc_id] = strict
        aligned_all[doc_id] = aligned
        agreements[doc_id] = agreement
        for run in RUNS:
            raw_scores[run].append(scores[run])
            strict_scores[run].append(strict[run])

    overall_uncertain_positive = sum(doc["uncertain_positive_rows"] for doc in documents)
    aggregate = {
        run: public_aggregate(raw_scores[run], strict_scores[run], overall_uncertain_positive)
        for run in RUNS
    }
    for run in RUNS:
        rebuilt = e.aggregate(raw_scores[run])
        if rebuilt != source_summary["aggregate"][run]:
            raise AssertionError(f"{run}: rebuilt aggregate differs from source summary")

    by_type: dict[str, Any] = {}
    for test_type in ("MAST", "SPT"):
        ids = [doc["id"] for doc in documents if doc["test_type"] == test_type]
        uncertain_positive_rows = sum(doc["uncertain_positive_rows"] for doc in documents if doc["id"] in ids)
        by_type[test_type] = {
            "documents": len(ids),
            "reference_rows": sum(doc["truth_rows"] for doc in documents if doc["id"] in ids),
            "uncertain_positive_rows": uncertain_positive_rows,
            "aggregate": {
                run: public_aggregate(
                    [per_doc_scores[doc_id][run] for doc_id in ids],
                    [per_doc_strict[doc_id][run] for doc_id in ids],
                    uncertain_positive_rows,
                ) for run in RUNS
            },
        }
    by_type["UniCAP"] = {
        "documents": 0,
        "reference_rows": 0,
        "uncertain_positive_rows": 0,
        "aggregate": None,
    }

    by_lab: dict[str, Any] = {}
    for lab in dict.fromkeys(doc["lab"] for doc in documents):
        ids = [doc["id"] for doc in documents if doc["lab"] == lab]
        uncertain_positive_rows = sum(doc["uncertain_positive_rows"] for doc in documents if doc["id"] in ids)
        by_lab[lab] = {
            "documents": len(ids),
            "uncertain_positive_rows": uncertain_positive_rows,
            "aggregate": {
                run: public_aggregate(
                    [per_doc_scores[doc_id][run] for doc_id in ids],
                    [per_doc_strict[doc_id][run] for doc_id in ids],
                    uncertain_positive_rows,
                ) for run in RUNS
            },
        }

    public_agreements = {
        doc_id: {k: v for k, v in agreement.items() if k != "pos_pairs"}
        for doc_id, agreement in agreements.items()
    }
    sample_runs = {run: read_json(SOURCE / "runs" / run / f"{manifest[0]['id']}.json") for run in RUNS}
    run_conditions = {
        run: {
            "backend": sample_runs[run].get("backend"),
            "model": sample_runs[run].get("model"),
            "rotate": sample_runs[run].get("rotate"),
            "double_read": sample_runs[run].get("double_read"),
            "preprocess": sample_runs[run].get("preprocess"),
            "text_layer": sample_runs[run].get("text_layer"),
            "documents": len(raw_scores[run]),
        } for run in RUNS
    }
    provenance = [
        {"path": str(path.relative_to(ROOT)), "sha256": sha256(path)}
        for path in sorted(set(provenance_paths))
    ]
    data = {
        "meta": {
            "generated": date.today().isoformat(),
            "reference_note": "Reference rows are human transcriptions with lead review, not independently adjudicated clinical ground truth. Eighteen uncertain rows are retained and excluded where the scorer specifies.",
            "source_generated": source_summary.get("generated"),
            "uncertain_rows": sum(doc["uncertain_rows"] for doc in documents),
            "uncertain_positive_rows": overall_uncertain_positive,
            "run_conditions": run_conditions,
            "provenance": provenance,
            "truth_rows": sum(doc["truth_rows"] for doc in documents),
        },
        "models": [
            {"key": "openai", "name": "API 모델", "model": "gpt-5.4"},
            {"key": "ollama", "name": "Local 모델", "model": "qwen3.8:27b"},
        ],
        "documents": documents,
        "aggregate": aggregate,
        "by_type": by_type,
        "by_lab": by_lab,
        "agreement": {
            "overall": agreement_aggregate(agreements, aligned_all),
            "by_document": public_agreements,
        },
        "history": error_history(),
        "experiments": experiments(),
    }
    if (
        data["meta"]["truth_rows"] != 1419
        or data["meta"]["uncertain_rows"] != 18
        or data["meta"]["uncertain_positive_rows"] != 5
        or len(data["meta"]["provenance"]) != 148
    ):
        raise AssertionError("reference totals differ from the audited totals")
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
