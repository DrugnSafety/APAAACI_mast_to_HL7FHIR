#!/usr/bin/env python3
"""결과지 이미지 한 장을 끝까지 통과시켜 각 단계가 살아남는지 본다.

왜 필요한가
  `score_ocr.py` 는 OCR 이 글자를 맞게 읽었는지만 잰다. 하지만 글자를 맞게 읽어도
  항원이 레지스트리에 매핑되지 않으면 그 뒤가 전부 무너진다 — 지식베이스 조회,
  감별 문진 생성, 판정, 리포트, SNOMED 코딩까지. 실제로 중국어 결과지가 그랬다.
  그래서 OCR → 매핑 → 문진 → 판정 → 리포트 → FHIR 까지 한 번에 통과시켜 보고,
  각 단계에서 몇 건이 살아남았는지 센다.

사용
  python3 scripts/pipeline_from_image.py tests/fixtures/ocr/zh_sige_report_scan_07.jpg
  python3 scripts/pipeline_from_image.py <이미지> --lang zh --json out.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(image: Path, lang: str) -> Dict[str, Any]:
    from services.ocr_service import get_ocr_service
    from services.relevance_service import get_relevance_service
    from services.questionnaire_service import get_questionnaire_engine
    from services.report_service import get_report_service
    from services.fhir_service import FHIRService
    from utils.allergen_mapper import get_allergen_mapper

    out: Dict[str, Any] = {"image": image.name, "lang": lang, "stages": {}}

    ocr = get_ocr_service().extract_from_image(image)
    rows = ocr.results or []
    out["stages"]["ocr"] = {"test_type": ocr.test_type.value, "rows": len(rows),
                            "patient_name_read": bool(ocr.patient.name)}

    # 매핑: 항원 이름이 레지스트리로 접히는가 (여기서 끊기면 뒤가 전부 빈다)
    mapper = get_allergen_mapper()
    mapped = [r for r in rows if mapper.find_allergen(r.allergen_name) is not None]
    out["stages"]["mapping"] = {"mapped": len(mapped), "of": len(rows),
                                "unmapped_samples": [r.allergen_name for r in rows
                                                     if mapper.find_allergen(r.allergen_name) is None][:6]}

    result = get_relevance_service().build_assessments(ocr, None)
    pos = result.assessments
    with_kb = [a for a in pos if a.kb]
    out["stages"]["assess"] = {"positive": len(pos), "with_knowledge_base": len(with_kb)}

    engine = get_questionnaire_engine()
    q = engine.build(result, None)
    n_q = sum(len(sec.get("questions") or []) for sec in (q.get("sections") or []))
    out["stages"]["questionnaire"] = {"sections": len(q.get("sections") or []),
                                      "questions": n_q}

    # 실제 /api/classify 와 같은 순서로 판정까지 돌린다(기본 답변 사용)
    answers: Dict[str, Any] = {}

    def walk(node):
        if isinstance(node, dict):
            if node.get("id") and node.get("options"):
                o = node["options"][0]
                v = o.get("value") if isinstance(o, dict) else o
                answers[node["id"]] = [v] if node.get("type") == "multi" else v
            for x in node.values():
                walk(x)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(q)
    engine.classify(result, answers, None)
    verdicts: Dict[str, int] = {}
    for a in result.assessments:
        key = getattr(a.relevance, "value", str(a.relevance))
        verdicts[key] = verdicts.get(key, 0) + 1
    out["stages"]["classify"] = {"verdicts": verdicts}

    patient_info = {"name": ocr.patient.name, "age": ocr.patient.age,
                    "gender": ocr.patient.gender, "test_date": ocr.patient.test_date,
                    "facility": ocr.patient.facility}
    md = get_report_service().build_patient_report_markdown(result, patient_info, None, lang)
    out["stages"]["report"] = {"chars": len(md), "sections": md.count("\n## ")}

    cross = engine.crossreactive_food_items(result.assessments, answers)
    bundles = FHIRService().build_bundles_from_relevance(ocr, result, None, cross)
    obs = bundles["observation_bundle"]
    ai = bundles["allergy_intolerance_bundle"]

    systems: Dict[str, int] = {}
    for e in (obs.get("entry") or []):
        for c in ((e["resource"].get("code") or {}).get("coding") or []):
            systems[c["system"]] = systems.get(c["system"], 0) + 1
        for comp in (e["resource"].get("component") or []):
            for c in ((comp.get("code") or {}).get("coding") or []):
                systems[c["system"]] = systems.get(c["system"], 0) + 1
    total = sum(systems.values())
    out["stages"]["fhir"] = {
        "observations": len(obs.get("entry") or []),
        "allergy_intolerance": len(ai.get("entry") or []),
        "coding_systems": systems,
        "snomed_share": round(100.0 * systems.get("http://snomed.info/sct", 0) / max(1, total), 1),
    }
    return out


def main():
    ap = argparse.ArgumentParser(description="결과지 이미지 → 전 단계 통과 점검")
    ap.add_argument("image")
    ap.add_argument("--lang", default="ko", choices=["ko", "en", "zh"])
    ap.add_argument("--json")
    args = ap.parse_args()
    logging.disable(logging.INFO)

    path = Path(args.image)
    if not path.is_absolute():
        path = ROOT / path
    res = run(path, args.lang)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
