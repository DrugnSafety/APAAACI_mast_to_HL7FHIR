"""
OCR Service
OpenAI GPT Vision API를 사용한 알레르기 검사 결과 이미지 OCR
"""

import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
from PIL import Image
import io

from openai import OpenAI
from pydantic import ValidationError

from config.settings import Settings
from models.schemas import OCRResult, AllergenResult, TestType, PatientInfo, InterpretationType
from utils.allergen_mapper import get_allergen_mapper

# 로거 설정
logger = logging.getLogger(__name__)


class OCRService:
    """OpenAI GPT Vision API를 사용한 OCR 서비스"""
    
    def __init__(self, settings: Settings):
        """
        OCR 서비스 초기화
        
        Args:
            settings: 애플리케이션 설정 객체
        """
        self.settings = settings
        # API 키는 settings.openai_api_key로 접근
        self.client = OpenAI(api_key=settings.openai_api_key or os.getenv('OPENAI_API_KEY', ''))
        self.allergen_mapper = get_allergen_mapper()
        
        # OCR 프롬프트 로드
        self.ocr_prompt = self._load_ocr_prompt()
        
    def _load_ocr_prompt(self) -> str:
        """OCR 프롬프트 파일 로드"""
        # 일단 기본 프롬프트 사용 (OCR_prompt.md가 너무 복잡할 수 있음)
        return self._get_default_prompt()
        
        # 나중에 사용할 때는 아래 코드 활성화
        # try:
        #     prompt_path = self.settings.get_instruction_path("OCR_prompt.md")
        #     with open(prompt_path, 'r', encoding='utf-8') as f:
        #         return f.read()
        # except FileNotFoundError:
        #     logger.warning("OCR_prompt.md를 찾을 수 없어 기본 프롬프트 사용")
        #     return self._get_default_prompt()
    
    def _get_default_prompt(self) -> str:
        """기본 OCR 프롬프트 반환"""
        return """Extract allergy test results from this image and return JSON with the following structure:
        {
            "test_type": "SPT, MAST, or UniCAP",
            "patient": {
                "name": "patient name or null",
                "test_date": "YYYY-MM-DD or null"
            },
            "results": [
                {
                    "index": 1,
                    "allergen_name": "allergen name",
                    "size_text": "for SPT: original size text like '12.5x12' or null",
                    "mean_mm": "for SPT: average of two dimensions in mm (e.g., (12.5+12)/2 = 12.25)",
                    "value": "numeric value",
                    "unit": "unit (mm for SPT, kU/L for MAST/UniCAP)",
                    "class": "for MAST/UniCAP: class value (0-6) or null",
                    "interpretation": "Positive or Negative"
                }
            ]
        }

        Determine test_type from the report:
        - "SPT" / "Skin Prick" / "피부단자검사" -> SPT (wheal size in mm)
        - "MAST" / "AdvanSure" / "Allergy screen" panels -> MAST (specific IgE, class + kU/L)
        - "UniCAP" / "ImmunoCAP" / "specific IgE (kU/L)" quantitative single-allergen reports -> UniCAP

        IMPORTANT for SPT (Skin Prick Test):
        - If size is written as "12.5x12" or "12.5×12", extract as size_text: "12.5x12"
        - Calculate mean_mm as the average: (12.5 + 12) / 2 = 12.25
        - Set value = mean_mm
        - Set unit = "mm"
        - Positive if mean_mm >= 3.0

        IMPORTANT for MAST / UniCAP (specific IgE):
        - value = numeric IgE concentration in kU/L (e.g., 3.52); unit = "kU/L"
        - class = reported class 0-6 if shown; Positive if class >= 1 or value >= 0.35 kU/L

        Extract all allergens visible in the image."""
    
    def _encode_image(self, image_source: Union[str, Path, Image.Image, bytes]) -> str:
        """
        이미지를 base64로 인코딩
        
        Args:
            image_source: 이미지 파일 경로, PIL Image, 또는 bytes
            
        Returns:
            base64 인코딩된 이미지 문자열
        """
        try:
            if isinstance(image_source, (str, Path)):
                # 파일 경로인 경우
                with open(image_source, "rb") as f:
                    return base64.b64encode(f.read()).decode('utf-8')
                    
            elif isinstance(image_source, Image.Image):
                # PIL Image인 경우
                buffer = io.BytesIO()
                image_source.save(buffer, format='PNG')
                return base64.b64encode(buffer.getvalue()).decode('utf-8')
                
            elif isinstance(image_source, bytes):
                # bytes인 경우
                return base64.b64encode(image_source).decode('utf-8')
                
            else:
                raise ValueError(f"지원하지 않는 이미지 타입: {type(image_source)}")
                
        except Exception as e:
            logger.error(f"이미지 인코딩 실패: {e}")
            raise
    
    def extract_from_image(
        self,
        image_source: Union[str, Path, Image.Image, bytes],
        custom_prompt: Optional[str] = None
    ) -> OCRResult:
        """
        이미지에서 알레르기 검사 데이터 추출
        
        Args:
            image_source: 이미지 소스 (경로, PIL Image, 또는 bytes)
            custom_prompt: 커스텀 프롬프트 (선택사항)
            
        Returns:
            OCRResult 객체
        """
        try:
            # 이미지 인코딩
            base64_image = self._encode_image(image_source)
            
            # 프롬프트 설정
            prompt = custom_prompt or self.ocr_prompt
            
            # OpenAI v2 API 호출
            try:
                # JSON 모드로 시도 (gpt-4o에서 지원)
                response = self.client.chat.completions.create(
                    model=self.settings.openai_vision_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You are a medical image OCR specialist. Extract data from allergy test images and return ONLY valid JSON."
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
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
                    temperature=0.1,  # 낮은 temperature로 일관성 있는 결과 유도
                    response_format={"type": "json_object"}  # JSON 모드 강제
                )
            except Exception as e:
                # JSON 모드가 실패하면 일반 모드로 재시도
                logger.warning(f"JSON 모드 실패, 일반 모드로 재시도: {e}")
                response = self.client.chat.completions.create(
                    model=self.settings.openai_vision_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "Extract allergy test data and return ONLY a valid JSON object."
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt + "\n\nReturn ONLY JSON, no markdown or explanation."},
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
                    temperature=0.1
                )
            
            # 응답 파싱
            content = response.choices[0].message.content
            logger.info(f"OCR API 응답 길이: {len(content)} 문자")
            
            # 디버깅: 응답 저장
            try:
                from pathlib import Path
                debug_dir = Path("output/debug")
                debug_dir.mkdir(exist_ok=True, parents=True)
                debug_file = debug_dir / f"ocr_response_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                with open(debug_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                logger.debug(f"OCR 응답 저장: {debug_file}")
            except:
                pass
            
            # JSON 추출
            ocr_data = self._extract_json(content)
            
            # OCRResult 생성
            ocr_result = self._parse_ocr_result(ocr_data)
            
            # 알레르겐 매핑 적용
            self._apply_allergen_mapping(ocr_result)
            
            return ocr_result
            
        except Exception as e:
            logger.error(f"OCR 추출 실패: {e}")
            raise
    
    def _extract_json(self, content: str) -> Dict[str, Any]:
        """
        응답 내용에서 JSON 추출
        
        Args:
            content: GPT 응답 텍스트
            
        Returns:
            파싱된 JSON 딕셔너리
        """
        import re
        
        # 먼저 content를 정리
        content = content.strip()
        
        # 시도 1: 직접 JSON 파싱
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        # 시도 2: JSON 블록 찾기
        json_match = re.search(r'```json\s*([\s\S]*?)```', content, re.DOTALL)
        if json_match:
            json_str = json_match.group(1).strip()
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.warning(f"JSON 블록 파싱 실패: {e}")
        
        # 시도 3: 가장 바깥쪽 중괄호 찾기
        brace_start = content.find('{')
        if brace_start != -1:
            brace_count = 0
            brace_end = -1
            for i in range(brace_start, len(content)):
                if content[i] == '{':
                    brace_count += 1
                elif content[i] == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        brace_end = i + 1
                        break
            
            if brace_end > brace_start:
                json_str = content[brace_start:brace_end]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as e:
                    logger.warning(f"중괄호 내용 파싱 실패: {e}")
                    # 일반적인 오류 수정 시도
                    json_str = self._fix_common_json_errors(json_str)
                    try:
                        return json.loads(json_str)
                    except:
                        pass
        
        # 시도 4: 기본 구조 반환
        logger.error("JSON 추출 실패, 기본 구조 반환")
        return {
            "test_type": "SPT",
            "patient": {},
            "results": []
        }
    
    def _safe_float(self, value: Any) -> Optional[float]:
        """안전한 float 변환"""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    
    def _fix_common_json_errors(self, json_str: str) -> str:
        """일반적인 JSON 오류 수정"""
        import re
        
        # 문자열 내부의 줄바꿈 처리 (문자열 리터럴 안에서만)
        # 먼저 문자열 리터럴을 찾아서 그 안의 줄바꿈만 이스케이프
        def escape_string_newlines(match):
            s = match.group(1)
            s = s.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            # 이스케이프 되지 않은 따옴표 처리
            s = re.sub(r'(?<!\\)"', r'\"', s)
            return f'"{s}"'
        
        # 문자열 리터럴 찾기 (이미 이스케이프된 따옴표 고려)
        json_str = re.sub(r'"((?:[^"\\]|\\.)*)(?<!\\)"', escape_string_newlines, json_str)
        
        # 불필요한 쉼표 제거
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)
        
        # 누락된 쉼표 추가 (숫자나 문자열 뒤에 "가 오는 경우)
        json_str = re.sub(r'([}\]])(\s*)(["{[])', r'\1,\2\3', json_str)
        json_str = re.sub(r'(\d)(\s*)"', r'\1,\2"', json_str)
        json_str = re.sub(r'"(\s*)"', r'",\1"', json_str)
        
        # 중복 쉼표 제거
        json_str = re.sub(r',\s*,', ',', json_str)
        
        return json_str
    
    def _parse_ocr_result(self, data: Dict[str, Any]) -> OCRResult:
        """
        OCR 데이터를 OCRResult 객체로 파싱
        
        Args:
            data: OCR API 응답 딕셔너리
            
        Returns:
            OCRResult 객체
        """
        try:
            # 기본 구조 확인
            if not isinstance(data, dict):
                logger.warning("OCR 응답이 딕셔너리가 아님, 기본값 사용")
                data = {}
            
            # TestType 파싱 (SPT / MAST / UniCAP)
            test_type_str = str(data.get('test_type', 'SPT')).upper()
            if 'SPT' in test_type_str or 'PRICK' in test_type_str or '피부' in test_type_str:
                test_type = TestType.SPT
            elif 'UNICAP' in test_type_str or 'IMMUNOCAP' in test_type_str or 'CAP' in test_type_str:
                test_type = TestType.UNICAP
            else:
                test_type = TestType.MAST
            
            # PatientInfo 파싱
            patient_data = data.get('patient', {})
            patient = PatientInfo(
                name=patient_data.get('name'),
                age=patient_data.get('age'),
                gender=patient_data.get('gender'),
                test_date=patient_data.get('test_date'),
                histamine_mean_mm=patient_data.get('histamine_mean_mm'),
                negative_control_mean_mm=patient_data.get('negative_control_mean_mm')
            )
            
            # AllergenResult 리스트 파싱
            results = []
            results_data = data.get('results', [])
            
            # results가 리스트인지 확인
            if not isinstance(results_data, list):
                logger.warning("results가 리스트가 아님")
                results_data = []
            
            for idx, item in enumerate(results_data):
                try:
                    if not isinstance(item, dict):
                        continue
                    
                    # interpretation 파싱
                    interp_str = str(item.get('interpretation', 'Unknown'))
                    if any(x in interp_str for x in ['Positive', 'P', '+', '양성', 'positive']):
                        interpretation = InterpretationType.POSITIVE
                    elif any(x in interp_str for x in ['Negative', 'N', '-', '음성', 'negative']):
                        interpretation = InterpretationType.NEGATIVE
                    else:
                        interpretation = InterpretationType.UNKNOWN
                    
                    # 필수 필드 확인
                    allergen_name = item.get('allergen_name', '')
                    if not allergen_name:
                        allergen_name = item.get('name', '') or item.get('allergen', '') or f"Unknown_{idx+1}"
                    
                    # SPT의 경우 size_text 처리
                    size_text = item.get('size_text')
                    mean_mm = self._safe_float(item.get('mean_mm'))
                    
                    # size_text가 "12.5x12" 형태인 경우 mean_mm 계산
                    if size_text and 'x' in str(size_text).lower() and not mean_mm:
                        try:
                            parts = str(size_text).lower().replace('×', 'x').split('x')
                            if len(parts) == 2:
                                val1 = float(parts[0].strip())
                                val2 = float(parts[1].strip())
                                mean_mm = (val1 + val2) / 2
                                logger.info(f"size_text에서 mean_mm 계산: {size_text} -> {mean_mm}")
                        except:
                            pass
                    
                    # SPT의 경우 value가 없으면 mean_mm 사용
                    value = self._safe_float(item.get('value'))
                    if test_type == TestType.SPT and not value and mean_mm:
                        value = mean_mm
                    
                    result = AllergenResult(
                        index=item.get('index', idx + 1),
                        raw_text=item.get('raw_text', ''),
                        allergen_name=allergen_name,
                        korean_name=item.get('korean_name'),
                        size_text=size_text,
                        mean_mm=mean_mm,
                        value=value,
                        unit=item.get('unit', 'mm' if test_type == TestType.SPT else 'kU/L'),
                        class_value=item.get('class') or item.get('class_value'),
                        interpretation=interpretation
                    )
                    results.append(result)
                except Exception as e:
                    logger.warning(f"알레르겐 결과 파싱 오류 (index {idx}): {e}")
                    continue
            
            return OCRResult(
                test_type=test_type,
                patient=patient,
                results=results,
                metadata={'source': 'openai_vision'}
            )
            
        except (KeyError, ValueError, ValidationError) as e:
            logger.error(f"OCR 결과 파싱 실패: {e}")
            raise ValueError(f"OCR 데이터 파싱 실패: {e}")
    
    def _apply_allergen_mapping(self, ocr_result: OCRResult) -> None:
        """
        알레르겐 매핑 적용 (SNOMED 코드, 한국어명, 카테고리 추가)
        
        Args:
            ocr_result: OCR 결과 객체 (in-place 수정)
        """
        for result in ocr_result.results:
            # 알레르겐 매핑 조회
            mapping = self.allergen_mapper.find_allergen(result.allergen_name)
            if mapping:
                # 한국어 이름 업데이트 (없는 경우만)
                if not result.korean_name:
                    result.korean_name = mapping.korean_name
                
                # 카테고리 정보 추가
                if mapping.category:
                    try:
                        from models.schemas import AllergenCategory
                        result.category = AllergenCategory(mapping.category)
                    except ValueError:
                        pass
                
                # 서브카테고리 정보 추가
                if mapping.subcategory:
                    try:
                        from models.schemas import AllergenSubcategory
                        result.subcategory = AllergenSubcategory(mapping.subcategory)
                    except ValueError:
                        pass
            else:
                logger.warning(f"알레르겐 매핑을 찾을 수 없음: {result.allergen_name}")


# 싱글톤 인스턴스
_ocr_service: Optional[OCRService] = None
_last_api_key: Optional[str] = None


def get_ocr_service(api_key: Optional[str] = None) -> OCRService:
    """OCR 서비스 싱글톤 인스턴스 반환 (API 키 변경 시 재생성)"""
    global _ocr_service, _last_api_key
    
    from config.settings import Settings
    settings = Settings()
    
    # API 키가 제공되면 설정에 업데이트
    if api_key:
        settings.openai_api_key = api_key
    
    # API 키가 변경되었거나 서비스가 없으면 새로 생성
    current_key = settings.openai_api_key
    if _ocr_service is None or _last_api_key != current_key:
        _ocr_service = OCRService(settings)
        _last_api_key = current_key
        
    return _ocr_service