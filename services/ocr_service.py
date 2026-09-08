"""
OCR Service
OpenAI GPT Vision API를 사용한 알레르기 검사 결과 이미지 OCR
"""

import base64
import json
import logging
import re
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
from PIL import Image
import io

from openai import OpenAI
from pydantic import ValidationError

from config.settings import Settings
from models.schemas import (
    OCRResult, AllergenResult, TestType, PatientInfo, InterpretationType,
    determine_interpretation,
)
from utils.allergen_mapper import get_allergen_mapper

# 알러젠이 아닌 요약/대조 행 (결과에서 제외)
_NON_ALLERGEN_ROWS = ("total ige", "총 ige", "total-ige", "totalige")

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
        return """You are extracting an allergy test result table from an image. Return ONLY a valid JSON object.

REQUIRED JSON STRUCTURE:
{
  "test_type": "SPT" | "MAST" | "UniCAP",
  "patient": {
    "name": "or null", "age": number or null, "gender": "M" | "F" | null,
    "test_date": "YYYY-MM-DD or null", "report_date": "YYYY-MM-DD or null",
    "facility": "testing lab / hospital / clinic name or null",
    "ordering_provider": "ordering doctor or referring org or null",
    "patient_id_external": "printed chart/patient number or null"
  },
  "results": [
    { "index": 1, "allergen_name": "as printed (keep English + any Korean in parentheses)",
      "class": 0-6 or null, "value": number or null, "unit": "IU/ml | kU/L | mm" }
  ]
}

PATIENT / FACILITY: read any printed patient demographics (name, age, sex) and the
testing institution (병원/검사실/laboratory name), ordering doctor, chart/patient number,
and dates (collection/report). Use null when a field is not printed. Do NOT invent values.

READ EVERY ROW — do not stop early:
- Tables are OFTEN laid out in TWO COLUMNS (e.g. No 1–31 on the left, No 32–62 on the right).
  Read the LEFT column top-to-bottom, THEN the RIGHT column top-to-bottom. Include ALL numbered rows.
- Keep the allergen name exactly as printed, including the Korean in parentheses,
  e.g. "D. pteronyssinus (진드기 Dp)", "Peanut (땅콩)".
- Preserve the value's unit as shown on the report (this report uses "IU/ml"; some use "kU/L").

DETERMINE test_type:
- Title/labels contain "MAST" or columns "Class" + "IU/ml"(IgE) -> "MAST"
- "UniCAP"/"ImmunoCAP" quantitative specific IgE -> "UniCAP"
- "SPT"/"Skin Prick"/"피부단자검사" with wheal size in mm -> "SPT"

FIELD RULES:
- MAST/UniCAP: read the "Class" number (0–6) AND the numeric IgE value with its unit.
  Do NOT invent a Positive/Negative column if the report has none — leave interpretation out;
  positivity is derived from Class (>=1) or value (>=0.35 kU/L).
- SPT: put wheal size in "value" with unit "mm".
- EXCLUDE the summary row "Total IgE" / "총 IgE" from results (it is not an allergen).

Be exhaustive and accurate. Return the JSON only."""
    
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
                    max_tokens=16384,
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
                    max_tokens=16384,
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
        
        # 시도 4: 잘린 응답에서 완성된 결과 객체만이라도 건져낸다 (대형 패널 대비)
        salvaged = self._salvage_partial_json(content)
        if salvaged and salvaged.get("results"):
            logger.warning(f"JSON이 잘렸으나 {len(salvaged['results'])}개 항목을 복구했습니다.")
            return salvaged

        # 시도 5: 기본 구조 반환 (test_type 은 원문에서 추정)
        logger.error("JSON 추출 실패, 기본 구조 반환")
        return {
            "test_type": self._sniff_test_type(content) or "MAST",
            "patient": {},
            "results": []
        }

    @staticmethod
    def _sniff_test_type(text: str) -> Optional[str]:
        """원문 텍스트에서 검사 종류 추정."""
        low = (text or "").lower()
        if "unicap" in low or "immunocap" in low:
            return "UniCAP"
        if "mast" in low or "iu/ml" in low or "ku/l" in low or "class" in low:
            return "MAST"
        if "spt" in low or "prick" in low or "피부" in low:
            return "SPT"
        return None

    def _salvage_partial_json(self, content: str) -> Optional[Dict[str, Any]]:
        """잘린 JSON에서 test_type 과 완성된 result 객체들을 정규식/괄호매칭으로 복구."""
        import re
        if not content:
            return None
        test_type = self._sniff_test_type(content) or "MAST"

        # "results" 배열 시작 위치 이후에서 완성된 {...} 객체를 순서대로 추출
        start = content.find('"results"')
        scan_from = content.find('[', start) if start != -1 else content.find('[')
        if scan_from == -1:
            scan_from = 0
        objs: list = []
        i = scan_from
        n = len(content)
        while i < n:
            if content[i] == '{':
                depth = 0
                j = i
                in_str = False
                esc = False
                while j < n:
                    c = content[j]
                    if in_str:
                        if esc:
                            esc = False
                        elif c == '\\':
                            esc = True
                        elif c == '"':
                            in_str = False
                    else:
                        if c == '"':
                            in_str = True
                        elif c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                            if depth == 0:
                                frag = content[i:j + 1]
                                try:
                                    obj = json.loads(frag)
                                    if isinstance(obj, dict) and (
                                        obj.get("allergen_name") or obj.get("name") or obj.get("allergen")):
                                        objs.append(obj)
                                except Exception:
                                    pass
                                break
                    j += 1
                i = j + 1
            else:
                i += 1
        if not objs:
            return None
        return {"test_type": test_type, "patient": {}, "results": objs}
    
    def _safe_float(self, value: Any) -> Optional[float]:
        """안전한 float 변환"""
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _safe_int(self, value: Any) -> Optional[int]:
        """안전한 int 변환 (문자열 '34세' 등에서 숫자만 추출)"""
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            import re
            m = re.search(r"\d+", str(value))
            return int(m.group()) if m else None
    
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
            _g = patient_data.get('gender')
            if isinstance(_g, str):
                _g = _g.strip().upper()[:1]
                _g = {"M": "M", "F": "F", "남": "M", "여": "F"}.get(_g) or (
                    "M" if _g in ("남",) else "F" if _g in ("여",) else None)
            patient = PatientInfo(
                name=patient_data.get('name'),
                age=self._safe_int(patient_data.get('age')),
                gender=_g,
                test_date=patient_data.get('test_date'),
                report_date=patient_data.get('report_date'),
                facility=patient_data.get('facility'),
                ordering_provider=patient_data.get('ordering_provider'),
                patient_id_external=patient_data.get('patient_id_external'),
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
                    
                    # 필수 필드 확인
                    allergen_name = item.get('allergen_name', '')
                    if not allergen_name:
                        allergen_name = item.get('name', '') or item.get('allergen', '') or f"Unknown_{idx+1}"

                    # 알러젠이 아닌 요약 행(Total IgE 등)은 제외
                    _norm_name = allergen_name.strip().lower().replace(" ", "")
                    if _norm_name.startswith("totalige") or _norm_name.startswith("total-ige") \
                            or "총ige" in _norm_name:
                        continue

                    # interpretation: 명시적 Positive/Negative 가 있으면 사용,
                    # 없으면 Class/수치로부터 결정론적으로 유도
                    interp_str = str(item.get('interpretation') or '')
                    if any(x in interp_str for x in ['Positive', '양성', 'positive']):
                        interpretation = InterpretationType.POSITIVE
                    elif any(x in interp_str for x in ['Negative', '음성', 'negative']):
                        interpretation = InterpretationType.NEGATIVE
                    else:
                        interpretation = None  # 아래에서 수치 기반으로 채움
                    
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
                    raw_value = item.get('value')
                    value = self._safe_float(raw_value)
                    # 숫자가 아닌 값('<0.35', 'N/A', 'undetectable' 등)은 원문을 보존해
                    # FHIR 에서 comparator / dataAbsentReason 으로 표현한다(결과를 버리지 않음).
                    value_text = None
                    if value is None and raw_value not in (None, ""):
                        value_text = str(raw_value).strip()
                    if test_type == TestType.SPT and not value and mean_mm:
                        value = mean_mm

                    class_value = item.get('class')
                    if class_value is None:
                        class_value = item.get('class_value')

                    # interpretation 이 명시되지 않았으면 Class/수치로부터 유도
                    if interpretation is None:
                        interpretation = determine_interpretation(
                            test_type=test_type,
                            mean_mm=mean_mm,
                            class_value=class_value,
                            value=value,
                            histamine_control=patient.histamine_mean_mm,
                        )

                    result = AllergenResult(
                        index=item.get('index', idx + 1),
                        raw_text=item.get('raw_text', ''),
                        allergen_name=allergen_name,
                        korean_name=item.get('korean_name'),
                        size_text=size_text,
                        mean_mm=mean_mm,
                        value=value,
                        value_text=value_text,
                        unit=item.get('unit') or ('mm' if test_type == TestType.SPT else 'kU/L'),
                        class_value=class_value,
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