#!/usr/bin/env python
"""
SPT (Skin Prick Test) OCR 테스트
size_text 처리 확인
"""

import os
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from services.ocr_service import get_ocr_service
from PIL import Image
import json

def test_spt_ocr():
    """SPT 이미지 OCR 테스트"""
    
    # API 키 설정
    api_key = os.getenv('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY_HERE')
    
    # 이미지 찾기
    input_dir = Path("input_images")
    test_image = input_dir / "skin_prick_test_1.jpg"
    
    if not test_image.exists():
        # 다른 SPT 이미지 시도
        test_image = input_dir / "그림1.png"
    
    if not test_image.exists():
        print(f"❌ SPT 이미지를 찾을 수 없습니다")
        return
    
    print(f"✅ 테스트 이미지: {test_image}")
    
    try:
        # OCR 서비스 초기화
        ocr_service = get_ocr_service(api_key=api_key)
        print("✅ OCR 서비스 초기화 완료")
        
        # 이미지 로드
        image = Image.open(test_image)
        
        # OCR 실행
        print("🔄 OCR 처리 중...")
        ocr_result = ocr_service.extract_from_image(image)
        
        # 결과 표시
        print("\n📊 OCR 결과:")
        print(f"- 검사 종류: {ocr_result.test_type.value}")
        print(f"- 환자 이름: {ocr_result.patient.name or '미확인'}")
        print(f"- 검사 날짜: {ocr_result.patient.test_date or '미확인'}")
        print(f"- 검출된 알레르겐 수: {len(ocr_result.results)}")
        
        # size_text 처리 확인
        print("\n📝 Size Text 처리 확인:")
        for r in ocr_result.results[:5]:  # 처음 5개만 확인
            if r.size_text:
                print(f"- {r.allergen_name}:")
                print(f"  size_text: {r.size_text}")
                print(f"  mean_mm: {r.mean_mm}")
                print(f"  value: {r.value}")
                print(f"  interpretation: {r.interpretation}")
        
        # 양성 알레르겐
        positive = [r for r in ocr_result.results 
                   if r.interpretation and str(r.interpretation).lower() in ['positive', 'p']]
        if positive:
            print(f"\n🔴 양성 알레르겐 ({len(positive)}개):")
            for r in positive:
                print(f"  - {r.allergen_name}: {r.value or r.mean_mm} {r.unit or 'mm'}")
        
        # 결과 저장
        output_file = Path("output/test_spt_ocr_result.json")
        output_file.parent.mkdir(exist_ok=True, parents=True)
        
        result_dict = {
            "test_type": ocr_result.test_type.value,
            "patient": {
                "name": ocr_result.patient.name,
                "test_date": ocr_result.patient.test_date
            },
            "results": [
                {
                    "index": r.index,
                    "allergen_name": r.allergen_name,
                    "korean_name": r.korean_name,
                    "size_text": r.size_text,
                    "mean_mm": r.mean_mm,
                    "value": r.value,
                    "unit": r.unit,
                    "interpretation": str(r.interpretation) if r.interpretation else None
                }
                for r in ocr_result.results
            ]
        }
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result_dict, f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 결과가 {output_file}에 저장되었습니다.")
    
    except Exception as e:
        print(f"❌ OCR 처리 중 오류: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_spt_ocr()
