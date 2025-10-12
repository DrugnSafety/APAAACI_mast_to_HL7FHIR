# ============================================================
# File: allergyintolerance_builder.py
# Purpose: Generate HL7 FHIR AllergyIntolerance resource
# Author: ChatGPT (GPT-5)
# Version: 1.0
# Date: 2025-10-11
# ============================================================

import json
from datetime import datetime

def create_allergyintolerance(patient_id: str, allergen_name: str, snomed_code: str,
                              category: str = "Environmental",
                              recorded_date: str = None,
                              practitioner_ref: str = "Practitioner/example") -> dict:
    """Generate a FHIR AllergyIntolerance resource for a given allergen."""
    recorded_date = recorded_date or datetime.utcnow().strftime("%Y-%m-%d")
    return {
        "resourceType": "AllergyIntolerance",
        "id": allergen_name.replace(" ", "_").lower(),
        "clinicalStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
            "code": "active",
            "display": "Active"}]},
        "verificationStatus": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/allergyintolerance-verification",
            "code": "confirmed",
            "display": "Confirmed"}]},
        "type": "allergy",
        "category": [category],
        "criticality": ["high"],
        "code": {"coding": [{
            "system": "http://snomed.info/sct",
            "code": snomed_code,
            "display": allergen_name}]},
        "reaction": [{
            "substance": [{"coding": [{
                "code": snomed_code,
                "display": allergen_name}]}],
            "manifestation": [{"coding": [{
                "code": "165014009",
                "display": "Allergy test positive",
                "date": datetime.utcnow().isoformat(),
                "system": "http://snomed.info/sct"}]}]}],
        "patient": {"reference": f"Patient/{patient_id}"},
        "recordedDate": recorded_date,
        "participant": [{
            "function": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                "code": "author",
                "display": "Author"}]},
            "actor": {"reference": practitioner_ref}}]
    }


def build_allergyintolerance_bundle(patient_id: str,
                                    exposure_feedback_path: str,
                                    allergen_map_path: str,
                                    output_path: str):
    """Build a bundle of AllergyIntolerance resources from user feedback and allergen map."""
    feedback = json.load(open(exposure_feedback_path, encoding="utf-8"))
    allergen_map = json.load(open(allergen_map_path, encoding="utf-8"))
    entries = allergen_map.get("entries", [])

    snomed_lookup = {e["canonical_name"].lower(): e["snomed"] for e in entries}
    category_lookup = {e["canonical_name"].lower(): "Food" if e["category"].lower() == "food" else "Environmental"
                       for e in entries}

    symptomatic = feedback["exposure_feedback"].get("symptomatic", [])
    fhir_resources = []

    for allergen in symptomatic:
        canon = allergen.lower()
        snomed_code = snomed_lookup.get(canon, "000000000")
        category = category_lookup.get(canon, "Environmental")
        resource = create_allergyintolerance(
            patient_id=patient_id,
            allergen_name=allergen,
            snomed_code=snomed_code,
            category=category,
            recorded_date=feedback.get("test_date", None))
        fhir_resources.append(resource)

    bundle = {"resourceType": "Bundle", "type": "collection",
              "entry": [{"resource": r} for r in fhir_resources]}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
    print(f"[✅] AllergyIntolerance bundle saved to {output_path}")
    return bundle


if __name__ == "__main__":
    build_allergyintolerance_bundle(
        patient_id="P001",
        exposure_feedback_path="exposure_feedback.json",
        allergen_map_path="allergen_map_prompt_v2.json",
        output_path="allergyintolerance_bundle.json"
    )
