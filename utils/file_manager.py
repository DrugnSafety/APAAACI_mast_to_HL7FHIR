"""
File Manager
파일 저장 및 네이밍 관리 유틸리티
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union
import pandas as pd

from models.schemas import TestType

logger = logging.getLogger(__name__)


class FileManager:
    """파일 저장 및 관리 클래스"""
    
    def __init__(self, output_dir: Path = None):
        """
        파일 매니저 초기화
        
        Args:
            output_dir: 출력 디렉토리 경로
        """
        self.output_dir = output_dir or Path("output")
        self.output_dir.mkdir(exist_ok=True, parents=True)
        
        # 서브 디렉토리 생성
        self.subdirs = {
            'ocr': self.output_dir / 'ocr',
            'fhir': self.output_dir / 'fhir',
            'reports': self.output_dir / 'reports',
            'logs': self.output_dir / 'logs',
            'temp': self.output_dir / 'temp'
        }
        
        for subdir in self.subdirs.values():
            subdir.mkdir(exist_ok=True, parents=True)
    
    def generate_filename(
        self,
        patient_name: str,
        test_type: Union[TestType, str],
        file_type: str,
        date: Optional[datetime] = None,
        extension: str = 'json'
    ) -> str:
        """
        표준화된 파일명 생성
        
        Args:
            patient_name: 환자 이름
            test_type: 검사 종류
            file_type: 파일 타입 (ocr, observation, allergy, report 등)
            date: 날짜 (없으면 현재 시간)
            extension: 파일 확장자
            
        Returns:
            생성된 파일명
        """
        # 환자 이름 정규화 (공백, 특수문자 제거)
        safe_name = "".join(c for c in patient_name if c.isalnum() or c in ['_', '-'])
        if not safe_name:
            safe_name = "Unknown"
        
        # 검사 종류 문자열 변환
        if isinstance(test_type, TestType):
            test_str = test_type.value
        else:
            test_str = str(test_type)
        
        # 날짜 포맷
        date = date or datetime.now()
        date_str = date.strftime("%Y%m%d")
        
        # 파일명 생성
        filename = f"{safe_name}_{test_str}_{date_str}_{file_type}.{extension}"
        
        return filename
    
    def get_file_path(
        self,
        filename: str,
        file_category: str = 'temp'
    ) -> Path:
        """
        카테고리별 파일 경로 반환
        
        Args:
            filename: 파일명
            file_category: 파일 카테고리 (ocr, fhir, reports, logs, temp)
            
        Returns:
            전체 파일 경로
        """
        if file_category in self.subdirs:
            return self.subdirs[file_category] / filename
        else:
            return self.output_dir / filename
    
    def save_json(
        self,
        data: Union[Dict, Any],
        patient_name: str,
        test_type: Union[TestType, str],
        file_type: str,
        date: Optional[datetime] = None
    ) -> Path:
        """
        JSON 파일 저장
        
        Args:
            data: 저장할 데이터
            patient_name: 환자 이름
            test_type: 검사 종류
            file_type: 파일 타입
            date: 날짜
            
        Returns:
            저장된 파일 경로
        """
        try:
            # 파일명 생성
            filename = self.generate_filename(
                patient_name=patient_name,
                test_type=test_type,
                file_type=file_type,
                date=date,
                extension='json'
            )
            
            # 카테고리 결정
            if file_type == 'ocr':
                category = 'ocr'
            elif file_type in ['observation', 'allergy']:
                category = 'fhir'
            else:
                category = 'temp'
            
            # 파일 경로
            filepath = self.get_file_path(filename, category)
            
            # Pydantic 모델인 경우 dict로 변환
            if hasattr(data, 'model_dump'):
                data = data.model_dump()
            elif hasattr(data, 'dict'):
                data = data.dict()
            
            # JSON 저장
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            
            logger.info(f"JSON 파일 저장 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"JSON 파일 저장 실패: {e}")
            raise
    
    def save_text(
        self,
        content: str,
        patient_name: str,
        test_type: Union[TestType, str],
        file_type: str,
        date: Optional[datetime] = None,
        extension: str = 'txt'
    ) -> Path:
        """
        텍스트 파일 저장
        
        Args:
            content: 저장할 텍스트
            patient_name: 환자 이름
            test_type: 검사 종류
            file_type: 파일 타입
            date: 날짜
            extension: 파일 확장자
            
        Returns:
            저장된 파일 경로
        """
        try:
            # 파일명 생성
            filename = self.generate_filename(
                patient_name=patient_name,
                test_type=test_type,
                file_type=file_type,
                date=date,
                extension=extension
            )
            
            # 카테고리 결정
            if file_type == 'report':
                category = 'reports'
            else:
                category = 'temp'
            
            # 파일 경로
            filepath = self.get_file_path(filename, category)
            
            # 텍스트 저장
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"텍스트 파일 저장 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"텍스트 파일 저장 실패: {e}")
            raise
    
    def save_pdf(
        self,
        pdf_bytes: bytes,
        patient_name: str,
        test_type: Union[TestType, str],
        date: Optional[datetime] = None
    ) -> Path:
        """
        PDF 파일 저장
        
        Args:
            pdf_bytes: PDF 바이트 데이터
            patient_name: 환자 이름
            test_type: 검사 종류
            date: 날짜
            
        Returns:
            저장된 파일 경로
        """
        try:
            # 파일명 생성
            filename = self.generate_filename(
                patient_name=patient_name,
                test_type=test_type,
                file_type='report',
                date=date,
                extension='pdf'
            )
            
            # 파일 경로
            filepath = self.get_file_path(filename, 'reports')
            
            # PDF 저장
            with open(filepath, 'wb') as f:
                f.write(pdf_bytes)
            
            logger.info(f"PDF 파일 저장 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"PDF 파일 저장 실패: {e}")
            raise
    
    def save_dataframe(
        self,
        df: pd.DataFrame,
        patient_name: str,
        test_type: Union[TestType, str],
        file_type: str,
        date: Optional[datetime] = None,
        format: str = 'csv'
    ) -> Path:
        """
        DataFrame 저장
        
        Args:
            df: 저장할 DataFrame
            patient_name: 환자 이름
            test_type: 검사 종류
            file_type: 파일 타입
            date: 날짜
            format: 저장 형식 (csv, excel)
            
        Returns:
            저장된 파일 경로
        """
        try:
            # 파일명 생성
            extension = 'xlsx' if format == 'excel' else 'csv'
            filename = self.generate_filename(
                patient_name=patient_name,
                test_type=test_type,
                file_type=file_type,
                date=date,
                extension=extension
            )
            
            # 파일 경로
            filepath = self.get_file_path(filename, 'temp')
            
            # DataFrame 저장
            if format == 'excel':
                df.to_excel(filepath, index=False)
            else:
                df.to_csv(filepath, index=False, encoding='utf-8-sig')
            
            logger.info(f"DataFrame 저장 완료: {filepath}")
            return filepath
            
        except Exception as e:
            logger.error(f"DataFrame 저장 실패: {e}")
            raise
    
    def list_files(
        self,
        category: Optional[str] = None,
        pattern: Optional[str] = None
    ) -> list:
        """
        저장된 파일 목록 반환
        
        Args:
            category: 파일 카테고리
            pattern: 파일명 패턴 (glob 패턴)
            
        Returns:
            파일 경로 리스트
        """
        if category and category in self.subdirs:
            search_dir = self.subdirs[category]
        else:
            search_dir = self.output_dir
        
        if pattern:
            files = list(search_dir.glob(pattern))
        else:
            files = list(search_dir.glob("*"))
        
        # 디렉토리 제외
        files = [f for f in files if f.is_file()]
        
        return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
    
    def clean_temp_files(self, days_old: int = 7):
        """
        오래된 임시 파일 정리
        
        Args:
            days_old: 며칠 이상 된 파일 삭제
        """
        try:
            from datetime import timedelta
            
            temp_dir = self.subdirs['temp']
            cutoff_time = datetime.now() - timedelta(days=days_old)
            
            count = 0
            for file in temp_dir.glob("*"):
                if file.is_file():
                    file_time = datetime.fromtimestamp(file.stat().st_mtime)
                    if file_time < cutoff_time:
                        file.unlink()
                        count += 1
            
            logger.info(f"{count}개의 임시 파일 삭제 완료")
            
        except Exception as e:
            logger.error(f"임시 파일 정리 실패: {e}")
    
    def get_file_info(self, filepath: Path) -> Dict[str, Any]:
        """
        파일 정보 반환
        
        Args:
            filepath: 파일 경로
            
        Returns:
            파일 정보 딕셔너리
        """
        if not filepath.exists():
            return None
        
        stat = filepath.stat()
        return {
            'name': filepath.name,
            'path': str(filepath),
            'size': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'created': datetime.fromtimestamp(stat.st_ctime),
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'extension': filepath.suffix
        }


# 싱글톤 인스턴스
_file_manager: Optional[FileManager] = None


def get_file_manager(output_dir: Optional[Path] = None) -> FileManager:
    """파일 매니저 싱글톤 인스턴스 반환"""
    global _file_manager
    if _file_manager is None:
        _file_manager = FileManager(output_dir)
    return _file_manager
