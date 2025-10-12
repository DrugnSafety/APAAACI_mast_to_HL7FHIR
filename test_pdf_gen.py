#!/usr/bin/env python
"""
PDF 생성 테스트
"""

from utils.pdf_generator import PDFGenerator
from pathlib import Path

def test_pdf_generation():
    """PDF 생성 테스트"""
    
    # 테스트 Markdown 내용
    markdown_content = """
# Personalized Allergy Management Report
**Patient ID:** PENDING  
**Test Date:** 2021-05-03  
**Generated:** 2025-10-12  
**Primary Source:** FHIR AllergyIntolerance (confirmed symptomatic allergens)

---

## 0️⃣ Executive Summary
You show **clinically significant allergic sensitization** to **house dust mites, birch pollen, and animal dander (dog, cat)**.  
These allergens explain both perennial (year-round) and seasonal symptoms such as rhinitis, sneezing, or itchy eyes.

---

## 1️⃣ Key Allergen Overview
| Category | Allergen | Type | Clinical Meaning | Severity |
|-----------|-----------|------|------------------|-----------|
| **Mite** | *Dermatophagoides farinae* / *pteronyssinus* | Indoor | Major trigger for **perennial allergic rhinitis** and **asthma risk** | 🔴 High |
| **Tree pollen** | *Birch pollen* | Outdoor (spring) | **Cross-reactivity** possible with apple, hazelnut, and some fruits (oral allergy syndrome) | 🟠 Moderate |
| **Animal dander** | Dog / Cat | Indoor | Causes **nasal, ocular, or skin symptoms**; prolonged exposure worsens asthma | 🟠 Moderate |

---

## 2️⃣ Environmental Management Plan

### 🏠 Indoor Environment
- **Dust mites**  
  - Use **mite-proof covers** on pillows, mattresses, and duvets.  
  - Wash bedding weekly at ≥60 °C.  
  - Keep **indoor humidity 40–50 %**.  
  - Remove carpets, heavy drapes, and plush toys if possible.  

- **Mold & humidity**  
  - Ventilate bathrooms and kitchens daily.  
  - Avoid indoor humidity >60 %.  
  - Clean A/C filters monthly.  

- **Animal dander**  
  - Keep pets **out of the bedroom**.  
  - Use **HEPA-filter vacuum** and air purifier.  
  - Bathe pets weekly if tolerated.  

### 🌳 Outdoor / Seasonal
- During **spring (March–May)**, birch pollen is highest.  
  - Close windows early morning and windy days.  
  - Use **sunglasses and masks** outdoors.  
  - Shower and change clothes immediately after outdoor activity.  

---

## 3️⃣ Pharmacologic Management (for discussion with physician)
*(Drug class only; no dosage guidance)*

| Category | First-line | Notes |
|-----------|-------------|-------|
| Antihistamine | Non-sedating oral antihistamines (cetirizine, loratadine, fexofenadine) | For rhinitis, sneezing, itching |
| Nasal corticosteroid | Fluticasone, mometasone nasal sprays | For persistent nasal congestion |
| Ocular therapy | Antihistamine or mast-cell stabilizer eye drops | For itchy/red eyes |
| Rescue | Short-acting β2 agonist inhaler | Only if asthma-like symptoms appear; under supervision |

> ⚠️ **Always consult your physician** before starting or changing medication.  

---

## 4️⃣ Allergen Immunotherapy (AIT) Consideration
| Aspect | Recommendation |
|---------|----------------|
| Candidate | Strongly indicated for **house dust mite** allergy (≥Class 3 equivalent SPT) |
| Type | SCIT (Subcutaneous) or SLIT (Sublingual tablet/drops) |
| Duration | 3–5 years continuous |
| Expected benefit | 60–80 % symptom reduction; may prevent asthma progression |
| Risks | Local redness/swelling; rare systemic reaction (requires medical supervision) |

---

## 5️⃣ Follow-up & Monitoring Plan
| Interval | Action |
|-----------|---------|
| Every 6 months | Symptom review + environmental control check |
| Every 12 months | Reassess medication efficacy and tolerance |
| Every 18–24 months | Repeat allergy test or total IgE (if clinical change) |
| Anytime | Seek urgent care for severe breathing difficulty, generalized hives, or anaphylaxis signs |

---

## 6️⃣ Patient Education Highlights
- Keep a **symptom diary** (season, weather, exposure, symptom score).  
- Use a **humidifier + hygrometer** to control room humidity.  
- For pet exposure: consistent cleaning is better than occasional deep-cleaning.  
- Wear a **mask during house cleaning** or around pets.  
- Consider **HEPA filter** in both bedroom and workspace.  

---

## 7️⃣ Summary Table (FHIR-aligned)
| Field | Value |
|-------|--------|
| **FHIR Resource Type** | `AllergyIntolerance` |
| **Clinical Status** | Active |
| **Verification Status** | Confirmed |
| **Reaction count** | 5 (mites, birch, dog, cat) |
| **Manifestation** | `165014009 – Allergy test positive` |
| **Category** | Environmental |
| **Criticality** | High |
| **Recorded Date** | 2021-05-03 |

---

## 8️⃣ References
- EAACI Position Paper: *Allergen immunotherapy for respiratory allergy* (2022)  
- AAAAI Practice Parameters: *Environmental control and allergen avoidance* (2023)  
- WHO/IUIS Allergen Nomenclature Database (updated 2024)

    """
    
    # PDF 생성기 인스턴스
    generator = PDFGenerator()
    
    # PDF 생성
    print("📄 PDF 생성 중...")
    
    try:
        # PDF 바이트 생성
        pdf_bytes = generator.markdown_to_pdf(markdown_content)
        
        # 파일로 저장
        output_path = Path("output/test_report.pdf")
        output_path.parent.mkdir(exist_ok=True, parents=True)
        output_path.write_bytes(pdf_bytes)
        
        print(f"✅ PDF 생성 성공!")
        print(f"   파일 위치: {output_path}")
        print(f"   파일 크기: {len(pdf_bytes):,} bytes")
        
        return True
        
    except Exception as e:
        print(f"❌ PDF 생성 실패: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    test_pdf_generation()
