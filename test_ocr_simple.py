#!/usr/bin/env python
"""
간단한 OCR 테스트
JSON 모드로 이미지 분석 테스트
"""

import os
import sys
import json
from pathlib import Path
from openai import OpenAI
import base64

def test_ocr_simple():
    """간단한 OCR 테스트"""
    
    # API 키 설정
    api_key = os.getenv('OPENAI_API_KEY', 'YOUR_OPENAI_API_KEY_HERE')
    
    # 이미지 찾기
    input_dir = Path("input_images")
    test_image = input_dir / "mast_1.png"
    
    if not test_image.exists():
        print(f"❌ 이미지를 찾을 수 없습니다: {test_image}")
        return
    
    print(f"✅ 테스트 이미지: {test_image}")
    
    # 이미지를 base64로 인코딩
    with open(test_image, 'rb') as f:
        base64_image = base64.b64encode(f.read()).decode('utf-8')
    
    # OpenAI 클라이언트
    client = OpenAI(api_key=api_key)
    
    # 간단한 프롬프트
    simple_prompt = """
    이 이미지는 알레르기 검사 결과입니다.
    다음 JSON 형식으로 응답하세요:
    {
        "test_type": "SPT 또는 MAST",
        "patient": {
            "name": "환자 이름 또는 null",
            "test_date": "날짜 또는 null"
        },
        "results": [
            {
                "index": 1,
                "allergen_name": "알레르겐 영문명",
                "value": 수치,
                "interpretation": "Positive 또는 Negative"
            }
        ]
    }
    
    최대한 많은 알레르겐을 추출하세요. 정확한 JSON만 반환하세요.
    """
    
    try:
        print("🔄 API 호출 중...")
        
        # API 호출 (JSON 모드)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are an OCR specialist. Extract data and return ONLY valid JSON."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": simple_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=4096,
            temperature=0.1,
            response_format={"type": "json_object"}  # JSON 모드
        )
        
        # 응답 파싱
        content = response.choices[0].message.content
        print(f"✅ 응답 받음: {len(content)} 문자")
        
        # JSON 파싱
        try:
            data = json.loads(content)
            print(f"✅ JSON 파싱 성공!")
            print(f"   - 검사 종류: {data.get('test_type')}")
            print(f"   - 결과 수: {len(data.get('results', []))}")
            
            # 처음 3개 결과 출력
            for i, result in enumerate(data.get('results', [])[:3]):
                print(f"   - {result.get('allergen_name')}: {result.get('value')} ({result.get('interpretation')})")
            
            # 파일로 저장
            output_dir = Path("output/debug")
            output_dir.mkdir(exist_ok=True, parents=True)
            output_file = output_dir / "test_ocr_simple_result.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"\n✅ 결과 저장: {output_file}")
            
        except json.JSONDecodeError as e:
            print(f"❌ JSON 파싱 실패: {e}")
            print(f"응답 내용:")
            print(content[:500])
            
            # 오류 파일 저장
            error_file = Path("output/debug/error_response.txt")
            error_file.parent.mkdir(exist_ok=True, parents=True)
            with open(error_file, 'w', encoding='utf-8') as f:
                f.write(content)
            print(f"오류 응답 저장: {error_file}")
    
    except Exception as e:
        print(f"❌ API 호출 실패: {e}")


if __name__ == "__main__":
    test_ocr_simple()
