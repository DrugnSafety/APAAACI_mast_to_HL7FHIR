#!/usr/bin/env python3
"""
Advanced OCR Processor for Allergy Test Results
Version: 3.0
Date: 2025-10-12
Author: Clinical Data Systems Team
"""

import json
import re
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from enum import Enum
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============= Enums and Constants =============

class TestType(Enum):
    SPT = "SPT"
    MAST = "MAST"
    UNICAP = "UniCAP"
    IMMUNOCAP = "ImmunoCAP"
    UNKNOWN = "Unknown"


class InterpretationResult(Enum):
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    EQUIVOCAL = "Equivocal"
    BORDERLINE = "Borderline"
    UNKNOWN = "Unknown"


class ClinicalSignificance(Enum):
    HIGH = "High"
    MODERATE = "Moderate"
    LOW = "Low"
    NONE = "None"


class AllergenCategory(Enum):
    MITE = "Mite"
    POLLEN = "Pollen"
    MOLD = "Mold"
    ANIMAL = "Animal"
    FOOD = "Food"
    INSECT = "Insect"
    LATEX = "Latex"
    DRUG = "Drug"
    CONTROL = "Control"
    MIXTURE = "Mixture"
    OTHER = "Other"


# ============= Data Classes =============

@dataclass
class AllergenInfo:
    """Allergen identification and naming"""
    canonical_name: str
    local_name: Optional[str] = None
    korean_name: Optional[str] = None
    english_name: Optional[str] = None
    latin_name: Optional[str] = None
    common_names: List[str] = field(default_factory=list)


@dataclass
class SPTMeasurement:
    """SPT-specific measurements"""
    wheal: Optional[str] = None
    flare: Optional[str] = None
    mean_diameter: Optional[float] = None


@dataclass
class IgEMeasurement:
    """MAST/UniCAP IgE measurements"""
    value: Optional[float] = None
    unit: Optional[str] = None
    class_level: Optional[str] = None
    grade: Optional[str] = None


@dataclass
class QualityMetrics:
    """Extraction quality metrics"""
    ocr_confidence: float = 0.0
    data_completeness: float = 0.0
    extraction_notes: Optional[str] = None


# ============= Allergen Database =============

class AllergenDatabase:
    """Comprehensive allergen normalization database"""
    
    def __init__(self):
        self.normalization_map = {
            # Mites
            "d.f|d.farinae|df|집먼지진드기|미국집먼지진드기|dermatophagoides farinae": {
                "canonical": "Dermatophagoides farinae",
                "korean": "미국집먼지진드기",
                "category": AllergenCategory.MITE,
                "subcategory": "House dust mite",
                "snomed": "419474003",
                "loinc": "6088-5"
            },
            "d.p|d.pteronyssinus|dp|유럽집먼지진드기|dermatophagoides pteronyssinus": {
                "canonical": "Dermatophagoides pteronyssinus",
                "korean": "유럽집먼지진드기",
                "category": AllergenCategory.MITE,
                "subcategory": "House dust mite",
                "snomed": "419801006",
                "loinc": "6087-7"
            },
            
            # Animals
            "cat|고양이|cat dander|cat epithelium|fel d|felis domesticus": {
                "canonical": "Cat dander",
                "korean": "고양이 비듬",
                "category": AllergenCategory.ANIMAL,
                "subcategory": "Cat",
                "snomed": "256259004",
                "loinc": "6833-8"
            },
            "dog|개|dog dander|dog epithelium|can f|canis familiaris": {
                "canonical": "Dog dander",
                "korean": "개 비듬",
                "category": AllergenCategory.ANIMAL,
                "subcategory": "Dog",
                "snomed": "313030008",
                "loinc": "6098-4"
            },
            
            # Tree Pollens
            "birch|자작나무|betula|bet v|silver birch": {
                "canonical": "Birch pollen",
                "korean": "자작나무 꽃가루",
                "category": AllergenCategory.POLLEN,
                "subcategory": "Tree pollen",
                "snomed": "256277009",
                "loinc": "15283-2"
            },
            "oak|참나무|떡갈나무|quercus|white oak": {
                "canonical": "Oak pollen",
                "korean": "참나무 꽃가루",
                "category": AllergenCategory.POLLEN,
                "subcategory": "Tree pollen",
                "snomed": "256286003",
                "loinc": "6189-1"
            },
            
            # Grass Pollens
            "timothy|티모시|phleum|phl p|timothy grass": {
                "canonical": "Timothy grass",
                "korean": "티모시 잔디",
                "category": AllergenCategory.POLLEN,
                "subcategory": "Grass pollen",
                "snomed": "256306007",
                "loinc": "6265-1"
            },
            
            # Weed Pollens
            "ragweed|돼지풀|ambrosia|amb a|short ragweed": {
                "canonical": "Ragweed pollen",
                "korean": "돼지풀 꽃가루",
                "category": AllergenCategory.POLLEN,
                "subcategory": "Weed pollen",
                "snomed": "256307001",
                "loinc": "6183-4"
            },
            "mugwort|쑥|artemisia|art v": {
                "canonical": "Mugwort pollen",
                "korean": "쑥 꽃가루",
                "category": AllergenCategory.POLLEN,
                "subcategory": "Weed pollen",
                "snomed": "256304000",
                "loinc": "6183-2"
            },
            
            # Molds
            "alternaria|알터나리아|alt a|alternaria alternata": {
                "canonical": "Alternaria alternata",
                "korean": "알터나리아",
                "category": AllergenCategory.MOLD,
                "subcategory": "Outdoor mold",
                "snomed": "419209009",
                "loinc": "6020-8"
            },
            "aspergillus|아스페르길루스|asp f|aspergillus fumigatus": {
                "canonical": "Aspergillus fumigatus",
                "korean": "아스페르길루스",
                "category": AllergenCategory.MOLD,
                "subcategory": "Indoor mold",
                "snomed": "419644008",
                "loinc": "6025-7"
            },
            
            # Foods
            "milk|우유|cow's milk|cow milk|bos d": {
                "canonical": "Cow milk",
                "korean": "우유",
                "category": AllergenCategory.FOOD,
                "subcategory": "Dairy",
                "snomed": "412071004",
                "loinc": "6150-3"
            },
            "egg|계란|달걀|egg white|gal d": {
                "canonical": "Egg white",
                "korean": "계란 흰자",
                "category": AllergenCategory.FOOD,
                "subcategory": "Egg",
                "snomed": "303299009",
                "loinc": "6106-5"
            },
            "peanut|땅콩|ara h|arachis hypogaea": {
                "canonical": "Peanut",
                "korean": "땅콩",
                "category": AllergenCategory.FOOD,
                "subcategory": "Nuts",
                "snomed": "412163001",
                "loinc": "6206-3"
            },
            "shrimp|새우|pen a|penaeus": {
                "canonical": "Shrimp",
                "korean": "새우",
                "category": AllergenCategory.FOOD,
                "subcategory": "Seafood",
                "snomed": "412238003",
                "loinc": "6246-7"
            },
            
            # Controls
            "histamine|히스타민|positive control": {
                "canonical": "Histamine",
                "korean": "히스타민",
                "category": AllergenCategory.CONTROL,
                "subcategory": "Positive control",
                "snomed": "373492002",
                "loinc": None
            },
            "saline|생리식염수|negative control|음성대조": {
                "canonical": "Saline",
                "korean": "생리식염수",
                "category": AllergenCategory.CONTROL,
                "subcategory": "Negative control",
                "snomed": "373873005",
                "loinc": None
            }
        }
    
    def normalize_allergen(self, raw_text: str) -> Dict[str, Any]:
        """Normalize allergen name to canonical form"""
        raw_lower = raw_text.lower().strip()
        
        for pattern, info in self.normalization_map.items():
            patterns = pattern.split("|")
            for p in patterns:
                if p in raw_lower:
                    return info
        
        # If no match found, return basic structure
        return {
            "canonical": raw_text,
            "korean": None,
            "category": AllergenCategory.OTHER,
            "subcategory": None,
            "snomed": None,
            "loinc": None
        }


# ============= OCR Error Correction =============

class OCRCorrector:
    """Handle common OCR errors and typos"""
    
    def __init__(self):
        self.number_corrections = {
            'O': '0', 'o': '0', 'Q': '0',
            'I': '1', 'l': '1', '|': '1',
            'S': '5', 's': '5',
            'b': '6', 'G': '6',
            'B': '8', '&': '8'
        }
        
        self.symbol_corrections = {
            '×': 'x', 'X': 'x', '*': 'x',
            ',': '.', '·': '.'
        }
    
    def correct_text(self, text: str, context: str = "general") -> str:
        """Correct OCR errors based on context"""
        if not text:
            return text
        
        corrected = text
        
        # Apply number corrections for numeric contexts
        if context in ["size", "value", "class"]:
            for old, new in self.number_corrections.items():
                corrected = corrected.replace(old, new)
        
        # Apply symbol corrections
        if context == "size":
            for old, new in self.symbol_corrections.items():
                corrected = corrected.replace(old, new)
        
        # Remove extra spaces
        corrected = re.sub(r'\s+', ' ', corrected).strip()
        
        return corrected


# ============= Main Processor =============

class AllergyTestOCRProcessor:
    """Main OCR processing engine for allergy test results"""
    
    def __init__(self):
        self.allergen_db = AllergenDatabase()
        self.ocr_corrector = OCRCorrector()
        self.warnings = []
        
    def detect_test_type(self, text: str) -> TestType:
        """Detect the type of allergy test from text content"""
        text_lower = text.lower()
        
        # SPT indicators
        spt_keywords = ['size', 'wheal', 'flare', '팽진', '크기', 'mm']
        if any(keyword in text_lower for keyword in spt_keywords):
            return TestType.SPT
        
        # MAST/UniCAP indicators
        mast_keywords = ['class', 'ku/l', '클래스', 'ige', 'unicap', 'immunocap']
        if any(keyword in text_lower for keyword in mast_keywords):
            if 'immunocap' in text_lower:
                return TestType.IMMUNOCAP
            elif 'unicap' in text_lower:
                return TestType.UNICAP
            else:
                return TestType.MAST
        
        return TestType.UNKNOWN
    
    def parse_spt_measurement(self, size_text: str) -> SPTMeasurement:
        """Parse SPT wheal/flare measurements"""
        measurement = SPTMeasurement()
        
        # Correct OCR errors
        size_text = self.ocr_corrector.correct_text(size_text, "size")
        
        # Extract measurements (e.g., "4x5", "3.5x4.0")
        pattern = r'(\d+\.?\d*)\s*[xX×]\s*(\d+\.?\d*)'
        match = re.search(pattern, size_text)
        
        if match:
            wheal = float(match.group(1))
            flare = float(match.group(2))
            measurement.wheal = f"{wheal}x{flare}"
            measurement.mean_diameter = (wheal + flare) / 2
        else:
            # Try single number
            single_pattern = r'(\d+\.?\d*)'
            single_match = re.search(single_pattern, size_text)
            if single_match:
                diameter = float(single_match.group(1))
                measurement.wheal = str(diameter)
                measurement.mean_diameter = diameter
        
        return measurement
    
    def parse_ige_measurement(self, text: str) -> IgEMeasurement:
        """Parse MAST/UniCAP IgE measurements"""
        measurement = IgEMeasurement()
        
        # Correct OCR errors
        text = self.ocr_corrector.correct_text(text, "value")
        
        # Extract class
        class_pattern = r'class\s*[:=]?\s*([0-6])|클래스\s*[:=]?\s*([0-6])'
        class_match = re.search(class_pattern, text, re.IGNORECASE)
        if class_match:
            measurement.class_level = class_match.group(1) or class_match.group(2)
        
        # Extract numeric value and unit
        value_pattern = r'(\d+\.?\d*)\s*(ku/l|iu/ml|kua/l)'
        value_match = re.search(value_pattern, text, re.IGNORECASE)
        if value_match:
            measurement.value = float(value_match.group(1))
            measurement.unit = value_match.group(2).upper()
        
        # Determine grade based on class
        if measurement.class_level:
            class_int = int(measurement.class_level)
            grades = {
                0: "Negative",
                1: "Low positive",
                2: "Moderate positive",
                3: "High positive",
                4: "Very high positive",
                5: "Very high positive",
                6: "Extremely high positive"
            }
            measurement.grade = grades.get(class_int, "Unknown")
        
        return measurement
    
    def determine_interpretation(self, 
                                measurement: Any,
                                test_type: TestType,
                                controls: Dict) -> Tuple[InterpretationResult, ClinicalSignificance]:
        """Determine clinical interpretation of results"""
        
        if test_type == TestType.SPT:
            # SPT interpretation
            if isinstance(measurement, SPTMeasurement) and measurement.mean_diameter:
                mean = measurement.mean_diameter
                histamine_control = controls.get('positive_control', {}).get('value', 3.0)
                
                if mean >= 3.0 or mean >= (histamine_control * 0.5):
                    significance = ClinicalSignificance.HIGH if mean >= 5.0 else ClinicalSignificance.MODERATE
                    return InterpretationResult.POSITIVE, significance
                elif mean >= 2.0:
                    return InterpretationResult.BORDERLINE, ClinicalSignificance.LOW
                else:
                    return InterpretationResult.NEGATIVE, ClinicalSignificance.NONE
        
        elif test_type in [TestType.MAST, TestType.UNICAP, TestType.IMMUNOCAP]:
            # MAST/UniCAP interpretation
            if isinstance(measurement, IgEMeasurement):
                if measurement.class_level and int(measurement.class_level) >= 1:
                    class_int = int(measurement.class_level)
                    if class_int >= 3:
                        significance = ClinicalSignificance.HIGH
                    elif class_int == 2:
                        significance = ClinicalSignificance.MODERATE
                    else:
                        significance = ClinicalSignificance.LOW
                    return InterpretationResult.POSITIVE, significance
                
                elif measurement.value and measurement.value >= 0.35:
                    if measurement.value >= 3.5:
                        significance = ClinicalSignificance.HIGH
                    elif measurement.value >= 0.7:
                        significance = ClinicalSignificance.MODERATE
                    else:
                        significance = ClinicalSignificance.LOW
                    return InterpretationResult.POSITIVE, significance
                
                else:
                    return InterpretationResult.NEGATIVE, ClinicalSignificance.NONE
        
        return InterpretationResult.UNKNOWN, ClinicalSignificance.NONE
    
    def calculate_confidence(self, 
                            ocr_clarity: float,
                            data_completeness: float,
                            value_plausibility: float,
                            format_consistency: float) -> float:
        """Calculate overall confidence score"""
        weights = {
            'ocr': 0.3,
            'completeness': 0.3,
            'plausibility': 0.2,
            'consistency': 0.2
        }
        
        confidence = (
            ocr_clarity * weights['ocr'] +
            data_completeness * weights['completeness'] +
            value_plausibility * weights['plausibility'] +
            format_consistency * weights['consistency']
        )
        
        return min(max(confidence, 0.0), 1.0)
    
    def validate_results(self, results: List[Dict]) -> Tuple[float, List[str]]:
        """Validate extracted results and generate warnings"""
        warnings = []
        confidence_deduction = 0.0
        
        for result in results:
            # Check for missing required fields
            if not result.get('allergen', {}).get('canonical_name'):
                warnings.append(f"Missing allergen name at index {result.get('index', '?')}")
                confidence_deduction += 0.1
            
            # Check for unrealistic values
            if result.get('measurement', {}).get('spt_size', {}).get('mean_diameter'):
                mean = result['measurement']['spt_size']['mean_diameter']
                if mean > 20:
                    warnings.append(f"Unusually large wheal size ({mean}mm) for {result['allergen']['canonical_name']}")
                    confidence_deduction += 0.05
                elif mean < 0:
                    warnings.append(f"Invalid negative size for {result['allergen']['canonical_name']}")
                    confidence_deduction += 0.1
            
            # Check IgE values
            if result.get('measurement', {}).get('ige_level', {}).get('value'):
                value = result['measurement']['ige_level']['value']
                if value > 5000:
                    warnings.append(f"Unusually high IgE value ({value}) for {result['allergen']['canonical_name']}")
                    confidence_deduction += 0.05
                elif value < 0:
                    warnings.append(f"Invalid negative IgE value for {result['allergen']['canonical_name']}")
                    confidence_deduction += 0.1
        
        final_confidence = max(1.0 - confidence_deduction, 0.1)
        return final_confidence, warnings
    
    def process_ocr_text(self, ocr_text: str) -> Dict:
        """Main processing function"""
        timestamp = datetime.now().isoformat()
        
        # Initialize result structure
        result = {
            "metadata": {
                "extraction_version": "3.0",
                "extraction_timestamp": timestamp,
                "ocr_confidence": 0.0,
                "warnings": []
            },
            "test_info": {
                "test_type": None,
                "test_subtype": None,
                "laboratory": None,
                "report_id": None
            },
            "patient": {
                "name": None,
                "patient_id": None,
                "date_of_birth": None,
                "gender": None,
                "test_date": None,
                "referrer": None
            },
            "controls": {
                "positive_control": {
                    "name": None,
                    "value": None,
                    "unit": None
                },
                "negative_control": {
                    "name": None,
                    "value": None,
                    "unit": None
                }
            },
            "results": [],
            "summary": {
                "total_tested": 0,
                "positive_count": 0,
                "negative_count": 0,
                "equivocal_count": 0,
                "primary_sensitivities": [],
                "clinical_notes": None
            }
        }
        
        # Detect test type
        test_type = self.detect_test_type(ocr_text)
        result["test_info"]["test_type"] = test_type.value
        
        # Process based on test type
        lines = ocr_text.strip().split('\n')
        
        for idx, line in enumerate(lines):
            # Skip empty lines
            if not line.strip():
                continue
            
            # Check for control values
            if 'histamine' in line.lower() or '히스타민' in line:
                if test_type == TestType.SPT:
                    measurement = self.parse_spt_measurement(line)
                    if measurement.mean_diameter:
                        result["controls"]["positive_control"] = {
                            "name": "Histamine",
                            "value": measurement.mean_diameter,
                            "unit": "mm"
                        }
                continue
            
            if 'saline' in line.lower() or '생리식염수' in line:
                if test_type == TestType.SPT:
                    measurement = self.parse_spt_measurement(line)
                    if measurement.mean_diameter:
                        result["controls"]["negative_control"] = {
                            "name": "Saline",
                            "value": measurement.mean_diameter or 0,
                            "unit": "mm"
                        }
                continue
            
            # Process allergen results
            allergen_info = self.allergen_db.normalize_allergen(line)
            
            # Skip if this is a control
            if allergen_info['category'] == AllergenCategory.CONTROL:
                continue
            
            # Create result entry
            result_entry = {
                "index": idx + 1,
                "raw_text": line,
                "allergen": {
                    "canonical_name": allergen_info['canonical'],
                    "local_name": None,
                    "korean_name": allergen_info.get('korean'),
                    "english_name": allergen_info['canonical'],
                    "latin_name": None,
                    "common_names": []
                },
                "measurement": {
                    "spt_size": None,
                    "ige_level": None
                },
                "classification": {
                    "category": allergen_info['category'].value,
                    "subcategory": allergen_info.get('subcategory'),
                    "allergen_group": None,
                    "source": "natural"
                },
                "interpretation": {
                    "result": InterpretationResult.UNKNOWN.value,
                    "clinical_significance": ClinicalSignificance.NONE.value,
                    "confidence": 0.0
                },
                "quality_metrics": {
                    "ocr_confidence": 0.9,
                    "data_completeness": 0.8,
                    "extraction_notes": None
                }
            }
            
            # Parse measurements based on test type
            if test_type == TestType.SPT:
                measurement = self.parse_spt_measurement(line)
                result_entry["measurement"]["spt_size"] = {
                    "wheal": measurement.wheal,
                    "flare": measurement.flare,
                    "mean_diameter": measurement.mean_diameter
                }
            else:
                measurement = self.parse_ige_measurement(line)
                result_entry["measurement"]["ige_level"] = {
                    "value": measurement.value,
                    "unit": measurement.unit,
                    "class": measurement.class_level,
                    "grade": measurement.grade
                }
            
            # Determine interpretation
            interpretation, significance = self.determine_interpretation(
                measurement, test_type, result["controls"]
            )
            result_entry["interpretation"]["result"] = interpretation.value
            result_entry["interpretation"]["clinical_significance"] = significance.value
            
            # Calculate entry confidence
            entry_confidence = self.calculate_confidence(0.9, 0.8, 0.9, 0.85)
            result_entry["interpretation"]["confidence"] = entry_confidence
            
            result["results"].append(result_entry)
        
        # Generate summary
        positive_allergens = []
        for r in result["results"]:
            if r["interpretation"]["result"] == InterpretationResult.POSITIVE.value:
                result["summary"]["positive_count"] += 1
                positive_allergens.append(r["allergen"]["canonical_name"])
            elif r["interpretation"]["result"] == InterpretationResult.NEGATIVE.value:
                result["summary"]["negative_count"] += 1
            else:
                result["summary"]["equivocal_count"] += 1
        
        result["summary"]["total_tested"] = len(result["results"])
        result["summary"]["primary_sensitivities"] = positive_allergens[:5]  # Top 5
        
        # Validate and generate warnings
        overall_confidence, warnings = self.validate_results(result["results"])
        result["metadata"]["ocr_confidence"] = overall_confidence
        result["metadata"]["warnings"] = warnings
        
        return result


# ============= Utility Functions =============

def format_json_output(data: Dict) -> str:
    """Format result as pretty JSON"""
    return json.dumps(data, indent=2, ensure_ascii=False)


def save_results(data: Dict, filename: str):
    """Save results to file"""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Results saved to {filename}")


# ============= Main Execution =============

if __name__ == "__main__":
    # Example usage
    processor = AllergyTestOCRProcessor()
    
    # Sample OCR text (would come from actual OCR)
    sample_ocr_text = """
    Patient: 김민수
    Test Date: 2025-10-11
    
    Histamine(0.1%) 5x5
    Saline 0x0
    
    D.farinae(미국집먼지진드기) 4x5
    D.pteronyssinus(유럽집먼지진드기) 3x4
    Cat dander(고양이) 2x2
    Dog dander(개) 0x0
    Birch pollen(자작나무) 6x7
    Oak pollen(참나무) 1x1
    Alternaria(알터나리아) 3x3
    Milk(우유) 0x0
    Egg white(계란) 2x3
    Peanut(땅콩) 5x6
    """
    
    # Process the text
    result = processor.process_ocr_text(sample_ocr_text)
    
    # Output JSON
    print(format_json_output(result))
    
    # Save to file
    save_results(result, "ocr_extraction_result.json")