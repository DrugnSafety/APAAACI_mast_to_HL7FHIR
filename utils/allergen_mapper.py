"""
Allergen Mapping Utilities
알레르겐 명칭 정규화 및 SNOMED 코드 매핑
"""

import json
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from functools import lru_cache
import logging

from models.schemas import AllergenMapping, AllergenDatabase

# 로거 설정
logger = logging.getLogger(__name__)


class AllergenMapper:
    """알레르겐 매핑 및 정규화 클래스"""
    
    def __init__(self, mapping_file_path: Optional[Path] = None):
        """
        Args:
            mapping_file_path: allergen_map_prompt_v2.json 파일 경로
        """
        if mapping_file_path is None:
            from config.settings import settings
            mapping_file_path = settings.get_allergen_map_path()
        
        self.mapping_file_path = mapping_file_path
        self.database = self._load_database()
        self._build_lookup_tables()
    
    def _load_database(self) -> AllergenDatabase:
        """알레르겐 매핑 데이터베이스 로드"""
        try:
            with open(self.mapping_file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Pydantic 모델로 변환
            database = AllergenDatabase(**data)
            logger.info(f"알레르겐 데이터베이스 로드 완료: {len(database.entries)}개 항목")
            return database
        
        except FileNotFoundError:
            logger.error(f"매핑 파일을 찾을 수 없습니다: {self.mapping_file_path}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 오류: {e}")
            raise
        except Exception as e:
            logger.error(f"데이터베이스 로드 실패: {e}")
            raise
    
    def _build_lookup_tables(self):
        """빠른 검색을 위한 룩업 테이블 생성"""
        self.canonical_lookup = {}
        self.korean_lookup = {}
        self.alias_lookup = {}
        self.snomed_lookup = {}
        
        for entry in self.database.entries:
            # Canonical name 룩업
            self.canonical_lookup[entry.canonical_name.lower()] = entry
            
            # Korean name 룩업
            if entry.korean_name:
                self.korean_lookup[entry.korean_name] = entry
            
            # Aliases 룩업
            for alias in entry.aliases + entry.ocr_aliases:
                self.alias_lookup[alias.lower()] = entry
            
            # SNOMED 코드 룩업
            if entry.snomed:
                self.snomed_lookup[entry.snomed] = entry
    
    def normalize_text(self, text: str) -> str:
        """텍스트 정규화"""
        # Global rules 적용
        rules = self.database.global_rules
        
        # 제거할 문자들
        for char in rules.get('strip_chars', []):
            text = text.replace(char, '')
        
        # 문자 정규화
        for old, new in rules.get('normalize', []):
            text = text.replace(old, new)
        
        # 노이즈 토큰 제거
        noise_tokens = rules.get('noise_tokens', [])
        words = text.split()
        words = [w for w in words if w.lower() not in [t.lower() for t in noise_tokens]]
        text = ' '.join(words)
        
        # 공백 정규화
        text = ' '.join(text.split())
        
        return text.strip()
    
    @lru_cache(maxsize=1000)
    def find_allergen(self, name: str) -> Optional[AllergenMapping]:
        """
        알레르겐 이름으로 매핑 정보 검색
        
        Args:
            name: 검색할 알레르겐 이름
            
        Returns:
            AllergenMapping 또는 None
        """
        # 특별 처리: 동물 털/깃털 매핑
        special_mappings = {
            'dog hair': 'dog',
            'cat hair': 'cat',
            'cow hair': 'cow',
            'horse hair': 'horse',
            'rabbit fur': 'rabbit',
            'sheep wool': 'sheep',
            'chicken feathers': 'chicken',
            'guinea pig hair': 'guinea pig',
            'hamster': 'hamster',
            'honey bee': 'bee',
            'yellow jacket': 'wasp',
            'wasp': 'wasp'
        }
        
        # 정규화 전에 특별 매핑 확인
        name_lower = name.lower().strip()
        for pattern, replacement in special_mappings.items():
            if pattern in name_lower:
                name = replacement
                logger.info(f"특별 매핑: {name_lower} -> {replacement}")
                break
        
        # 정규화
        normalized = self.normalize_text(name)
        normalized_lower = normalized.lower()
        
        # 1. 정확한 매칭 시도
        if normalized_lower in self.canonical_lookup:
            return self.canonical_lookup[normalized_lower]
        
        # 2. 한국어 이름 매칭
        if normalized in self.korean_lookup:
            return self.korean_lookup[normalized]
        
        # 3. Alias 매칭
        if normalized_lower in self.alias_lookup:
            return self.alias_lookup[normalized_lower]
        
        # 4. 부분 매칭 시도
        for entry in self.database.entries:
            # 부분 문자열 매칭
            if normalized_lower in entry.canonical_name.lower():
                return entry
            if entry.korean_name and normalized in entry.korean_name:
                return entry
        
        # 5. 퍼지 매칭 (편집 거리)
        best_match = self._fuzzy_match(normalized_lower)
        if best_match:
            return best_match
        
        logger.warning(f"알레르겐 매핑 실패: {name}")
        return None
    
    def _fuzzy_match(self, text: str, threshold: float = 0.8) -> Optional[AllergenMapping]:
        """
        편집 거리 기반 퍼지 매칭
        
        Args:
            text: 검색할 텍스트
            threshold: 유사도 임계값 (0-1)
        """
        from difflib import SequenceMatcher
        
        best_score = 0
        best_match = None
        
        # 모든 항목과 비교
        for entry in self.database.entries:
            # Canonical name과 비교
            score = SequenceMatcher(None, text, entry.canonical_name.lower()).ratio()
            if score > best_score and score >= threshold:
                best_score = score
                best_match = entry
            
            # Aliases와 비교
            for alias in entry.aliases + entry.ocr_aliases:
                score = SequenceMatcher(None, text, alias.lower()).ratio()
                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = entry
        
        if best_match:
            logger.info(f"퍼지 매칭 성공: {text} -> {best_match.canonical_name} (score: {best_score:.2f})")
        
        return best_match
    
    def get_snomed_code(self, allergen_name: str) -> Optional[str]:
        """
        알레르겐 이름으로 SNOMED 코드 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            SNOMED 코드 또는 None
        """
        mapping = self.find_allergen(allergen_name)
        return mapping.snomed if mapping else None
    
    def get_korean_name(self, allergen_name: str) -> Optional[str]:
        """
        알레르겐의 한국어 이름 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            한국어 이름 또는 None
        """
        mapping = self.find_allergen(allergen_name)
        return mapping.korean_name if mapping else None
    
    def get_category(self, allergen_name: str) -> Tuple[Optional[str], Optional[str]]:
        """
        알레르겐의 카테고리 정보 조회
        
        Args:
            allergen_name: 알레르겐 이름
            
        Returns:
            (category, subcategory) 튜플
        """
        mapping = self.find_allergen(allergen_name)
        if mapping:
            return mapping.category, mapping.subcategory
        return None, None
    
    def extract_allergen_from_text(self, text: str) -> List[AllergenMapping]:
        """
        텍스트에서 알레르겐 추출
        
        Args:
            text: 분석할 텍스트
            
        Returns:
            발견된 알레르겐 매핑 리스트
        """
        found_allergens = []
        text_lower = text.lower()
        
        # 모든 알레르겐 항목 검사
        for entry in self.database.entries:
            # Canonical name 검사
            if entry.canonical_name.lower() in text_lower:
                found_allergens.append(entry)
                continue
            
            # Korean name 검사
            if entry.korean_name and entry.korean_name in text:
                found_allergens.append(entry)
                continue
            
            # Aliases 검사
            for alias in entry.aliases:
                if alias.lower() in text_lower:
                    found_allergens.append(entry)
                    break
        
        # 중복 제거
        unique_allergens = []
        seen = set()
        for allergen in found_allergens:
            if allergen.canonical_name not in seen:
                unique_allergens.append(allergen)
                seen.add(allergen.canonical_name)
        
        return unique_allergens
    
    def process_ocr_result(self, allergen_name: str) -> Dict[str, Any]:
        """
        OCR 결과의 알레르겐 이름을 처리하고 매핑 정보 반환
        
        Args:
            allergen_name: OCR에서 추출된 알레르겐 이름
            
        Returns:
            매핑 정보 딕셔너리
        """
        mapping = self.find_allergen(allergen_name)
        
        if mapping:
            return {
                "original": allergen_name,
                "canonical_name": mapping.canonical_name,
                "korean_name": mapping.korean_name,
                "snomed_code": mapping.snomed,
                "category": mapping.category,
                "subcategory": mapping.subcategory,
                "matched": True
            }
        else:
            return {
                "original": allergen_name,
                "canonical_name": allergen_name,
                "korean_name": None,
                "snomed_code": None,
                "category": "Other",
                "subcategory": None,
                "matched": False
            }
    
    def get_statistics(self) -> Dict[str, Any]:
        """데이터베이스 통계 반환"""
        categories = {}
        for entry in self.database.entries:
            category = entry.category
            if category not in categories:
                categories[category] = 0
            categories[category] += 1
        
        return {
            "total_entries": len(self.database.entries),
            "categories": categories,
            "version": self.database.version,
            "locale": self.database.locale,
            "with_snomed": sum(1 for e in self.database.entries if e.snomed),
            "with_korean": sum(1 for e in self.database.entries if e.korean_name)
        }


# 싱글톤 인스턴스
_mapper_instance: Optional[AllergenMapper] = None


def get_allergen_mapper() -> AllergenMapper:
    """알레르겐 매퍼 싱글톤 인스턴스 반환"""
    global _mapper_instance
    if _mapper_instance is None:
        _mapper_instance = AllergenMapper()
    return _mapper_instance


# 편의 함수들
def find_allergen(name: str) -> Optional[AllergenMapping]:
    """알레르겐 검색 편의 함수"""
    return get_allergen_mapper().find_allergen(name)


def get_snomed_code(name: str) -> Optional[str]:
    """SNOMED 코드 조회 편의 함수"""
    return get_allergen_mapper().get_snomed_code(name)


def get_korean_name(name: str) -> Optional[str]:
    """한국어 이름 조회 편의 함수"""
    return get_allergen_mapper().get_korean_name(name)
