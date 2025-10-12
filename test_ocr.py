"""
OCR 기능 테스트 스크립트
샘플 이미지를 사용하여 OCR 서비스 테스트
"""

import os
import sys
from pathlib import Path

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import Settings
from services.ocr_service import get_ocr_service
from PIL import Image
import json


def test_ocr():
    """OCR 서비스 테스트"""
    
    # 설정 초기화
    settings = Settings()
    
    # API 키 확인
    if not settings.openai_api_key or settings.openai_api_key == "your_openai_api_key_here":
        print("❌ OpenAI API 키를 설정해주세요!")
        print("다음 내용으로 .env 파일을 생성하세요:")
        print("-" * 50)
        print("OPENAI_API_KEY=your_actual_api_key_here")
        print("-" * 50)
        return
    
    # 샘플 이미지 찾기
    input_dir = Path("input_images")
    sample_images = list(input_dir.glob("*.png")) + list(input_dir.glob("*.jpg"))
    
    if not sample_images:
        print(f"❌ {input_dir} 폴더에 테스트 이미지가 없습니다.")
        return
    
    # 첫 번째 이미지로 테스트
    test_image = sample_images[0]
    print(f"✅ 테스트 이미지: {test_image.name}")
    
    try:
        # OCR 서비스 초기화
        ocr_service = get_ocr_service()
        print("✅ OCR 서비스 초기화 완료")
        
        # OCR 실행
        print("🔄 OCR 처리 중...")
        result = ocr_service.extract_from_image(str(test_image))
        
        # 결과 출력
        print("\n📊 OCR 결과:")
        print(f"- 검사 종류: {result.test_type.value}")
        print(f"- 환자 이름: {result.patient.name or '미확인'}")
        print(f"- 검사 날짜: {result.patient.test_date or '미확인'}")
        print(f"- 검출된 알레르겐 수: {len(result.results)}")
        
        # 양성 알레르겐 출력
        positive_allergens = [
            r for r in result.results 
            if r.interpretation and r.interpretation.value == "Positive"
        ]
        
        if positive_allergens:
            print(f"\n🔴 양성 알레르겐 ({len(positive_allergens)}개):")
            for allergen in positive_allergens[:5]:  # 처음 5개만 출력
                print(f"  - {allergen.allergen_name}: {allergen.mean_mm or allergen.value} {allergen.unit or 'mm'}")
                if allergen.korean_name:
                    print(f"    (한국어: {allergen.korean_name})")
        
        # JSON 파일로 저장
        output_path = Path("output") / f"test_ocr_result.json"
        output_path.parent.mkdir(exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result.model_dump(), f, ensure_ascii=False, indent=2)
        
        print(f"\n✅ 결과가 {output_path}에 저장되었습니다.")
        
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_ocr()
