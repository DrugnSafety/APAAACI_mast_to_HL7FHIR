
import json
from datetime import datetime

def generate_allergy_management_report(fhir_json_path: str, output_path: str):
    """
    Generates a personalized allergy management report in Korean based on FHIR AllergyIntolerance Bundle JSON.
    """
    with open(fhir_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    patient_name = "알 수 없음"
    test_date = None
    allergens = []

    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "AllergyIntolerance":
            continue
        category = ", ".join(resource.get("category", []))
        patient_ref = resource.get("patient", {}).get("display")
        if patient_ref:
            patient_name = patient_ref
        test_date = resource.get("recordedDate", test_date)

        allergen_names = [c.get("display") for c in resource.get("code", {}).get("coding", []) if c.get("display")]
        allergens.append({
            "category": category,
            "allergens": allergen_names
        })

    # Group allergens by category
    grouped = {}
    for item in allergens:
        grouped.setdefault(item["category"], []).extend(item["allergens"])

    # Construct report text
    report = []
    report.append(f"**맞춤형 알레르기 관리 플랜 — {patient_name} ({test_date})**\n")
    report.append("⸻\n")
    report.append("🩺 1. 핵심 요약\n")

    # Identify dominant allergen
    dominant_category = max(grouped.keys(), key=lambda c: len(grouped[c])) if grouped else "Environmental"
    main_allergens = ", ".join(grouped.get(dominant_category, []))
    report.append(f"• 주 알레르겐: {main_allergens}\n")
    report.append(f"• 주요 카테고리: {dominant_category}\n")
    report.append("👉 따라서 해당 알레르겐 중심의 알레르기 관리가 필요합니다.\n\n")

    # Environment plan
    if "Environmental" in grouped or "환경" in dominant_category:
        report.append("🧹 2. 환경 관리 플랜 (Environmental Control)\n")
        report.append("① 침실 관리\n• 진드기 방지 커버 사용, 60℃ 온수 세탁, 완전 건조\n• 습도 40~50% 유지, HEPA 필터형 공기청정기\n\n")
        report.append("② 청소 습관\n• 물걸레 청소 중심, 청소 후 환기 30분 이상\n\n")

    # Medical plan
    report.append("💊 3. 의학적 관리 플랜\n• 항히스타민제 및 비강 스테로이드 스프레이 사용\n• 필요 시 면역치료(SLIT/SCIT) 고려\n\n")

    # Lifestyle plan
    report.append("🧘‍♀️ 4. 생활습관 및 예방\n• 애완동물 침실 출입 금지\n• 주 2회 이상 환기\n• 환절기에는 증상 악화 가능 → 사전 약물 복용\n\n")

    # Follow-up plan
    report.append("📊 5. 추적 관리 계획\n• 알레르기 증상 평가: 3~6개월 간격\n• 총 IgE: 1~2년마다 재검\n• 면역치료 중인 경우, 효과 평가 및 부작용 점검\n\n")

    # Final summary
    report.append("🔍 6. 요약 결론\n")
    report.append(f"핵심 알레르겐: {main_allergens}\n관리 목표: 노출 최소화 + 면역치료 고려\n예상 효과: 증상 완화, 알레르기 체질 개선, 삶의 질 향상\n")

    # Write to file
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(report))

    print(f"✅ Allergy management report generated: {output_path}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate a personalized allergy management report from FHIR JSON.")
    parser.add_argument("--input", required=True, help="Path to FHIR AllergyIntolerance Bundle JSON")
    parser.add_argument("--output", default="allergy_management_report.txt", help="Output report file path")
    args = parser.parse_args()

    generate_allergy_management_report(args.input, args.output)
