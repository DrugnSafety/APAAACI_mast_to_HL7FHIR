#!/usr/bin/env python
"""
Setup Checker
프로젝트 설정 확인 스크립트
"""

import os
import sys
from pathlib import Path
from typing import Tuple, List

# 색상 코드
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    ENDC = '\033[0m'


def print_header():
    """헤더 출력"""
    print("\n" + "="*60)
    print("  알레르기 검사 자동 분석 시스템 - 설정 확인")
    print("="*60 + "\n")


def check_python_version() -> Tuple[bool, str]:
    """Python 버전 확인"""
    version = sys.version_info
    if version.major == 3 and version.minor >= 7:
        return True, f"Python {version.major}.{version.minor}.{version.micro}"
    return False, f"Python {version.major}.{version.minor}.{version.micro} (3.7+ 필요)"


def check_env_file() -> Tuple[bool, str]:
    """환경 파일 확인"""
    env_path = Path(".env")
    if not env_path.exists():
        return False, ".env 파일이 없습니다"
    
    # API 키 확인
    with open(env_path, 'r') as f:
        content = f.read()
        if "OPENAI_API_KEY" not in content:
            return False, "OPENAI_API_KEY가 설정되지 않았습니다"
        if "your_openai_api_key_here" in content or "your_actual" in content:
            return False, "OPENAI_API_KEY를 실제 값으로 변경해주세요"
    
    return True, ".env 파일 설정 완료"


def check_required_packages() -> Tuple[bool, List[str]]:
    """필수 패키지 확인"""
    missing = []
    
    # 웹 서비스(server.py) 런타임 필수 패키지. streamlit 은 레거시 앱 전용이라 제외한다
    # (requirements-legacy.txt). 없다고 해서 설정이 잘못된 것이 아니다.
    required_packages = [
        "openai",
        "fastapi",
        "uvicorn",
        "pydantic",
        "pandas",
        "numpy",
        "PIL",
        "markdown",
        "reportlab",
        "httpx",
        "aiofiles",
        "dotenv"
    ]
    
    for package in required_packages:
        try:
            if package == "PIL":
                __import__("PIL")
            elif package == "dotenv":
                __import__("dotenv")
            else:
                __import__(package)
        except ImportError:
            missing.append(package)
    
    if missing:
        return False, missing
    return True, []


def check_directory_structure() -> Tuple[bool, List[str]]:
    """디렉토리 구조 확인"""
    required_dirs = [
        "services",
        "utils",
        "models",
        "config",
        "components",
        "instructions",
        "input_images",
        "output"
    ]
    
    missing = []
    for dir_name in required_dirs:
        if not Path(dir_name).exists():
            missing.append(dir_name)
    
    if missing:
        return False, missing
    return True, []


def check_required_files() -> Tuple[bool, List[str]]:
    """필수 파일 확인"""
    required_files = [
        "app.py",
        "requirements.txt",
        "config/settings.py",
        "models/schemas.py",
        "services/ocr_service.py",
        "services/fhir_service.py",
        "services/chatbot_service.py",
        "services/report_service.py",
        "utils/allergen_mapper.py",
        "utils/pdf_generator.py"
    ]
    
    missing = []
    for file_path in required_files:
        if not Path(file_path).exists():
            missing.append(file_path)
    
    if missing:
        return False, missing
    return True, []


def check_allergen_mapping() -> Tuple[bool, str]:
    """알레르겐 매핑 파일 확인"""
    mapping_path = Path("instructions/allergen_map_prompt_v2.json")
    if not mapping_path.exists():
        return False, "allergen_map_prompt_v2.json 파일이 없습니다"
    
    # 파일 크기 확인
    size = mapping_path.stat().st_size
    if size < 1000:
        return False, "allergen_map_prompt_v2.json 파일이 비어있거나 너무 작습니다"
    
    return True, f"알레르겐 매핑 파일 확인 ({size:,} bytes)"


def check_sample_images() -> Tuple[bool, str]:
    """샘플 이미지 확인"""
    input_dir = Path("input_images")
    if not input_dir.exists():
        return False, "input_images 디렉토리가 없습니다"
    
    images = list(input_dir.glob("*.png")) + \
             list(input_dir.glob("*.jpg")) + \
             list(input_dir.glob("*.jpeg"))
    
    if not images:
        return False, "샘플 이미지가 없습니다"
    
    return True, f"{len(images)}개의 샘플 이미지 발견"


def print_result(name: str, success: bool, message: str):
    """결과 출력"""
    if success:
        status = f"{Colors.GREEN}✓{Colors.ENDC}"
    else:
        status = f"{Colors.RED}✗{Colors.ENDC}"
    
    print(f"  {status} {name}: {message}")


def main():
    """메인 실행 함수"""
    print_header()
    
    all_success = True
    
    # 1. Python 버전 확인
    success, message = check_python_version()
    print_result("Python 버전", success, message)
    all_success = all_success and success
    
    # 2. 환경 파일 확인
    success, message = check_env_file()
    print_result("환경 설정", success, message)
    all_success = all_success and success
    
    # 3. 패키지 확인
    success, missing = check_required_packages()
    if success:
        print_result("필수 패키지", True, "모든 패키지 설치됨")
    else:
        print_result("필수 패키지", False, f"누락된 패키지: {', '.join(missing)}")
        print(f"      {Colors.YELLOW}→ pip install -r requirements.txt 실행 필요{Colors.ENDC}")
    all_success = all_success and success
    
    # 4. 디렉토리 구조 확인
    success, missing = check_directory_structure()
    if success:
        print_result("디렉토리 구조", True, "모든 디렉토리 존재")
    else:
        print_result("디렉토리 구조", False, f"누락된 디렉토리: {', '.join(missing)}")
    all_success = all_success and success
    
    # 5. 필수 파일 확인
    success, missing = check_required_files()
    if success:
        print_result("필수 파일", True, "모든 파일 존재")
    else:
        print_result("필수 파일", False, f"누락된 파일 {len(missing)}개")
        for file in missing[:5]:  # 처음 5개만 표시
            print(f"      - {file}")
    all_success = all_success and success
    
    # 6. 알레르겐 매핑 확인
    success, message = check_allergen_mapping()
    print_result("알레르겐 매핑", success, message)
    all_success = all_success and success
    
    # 7. 샘플 이미지 확인
    success, message = check_sample_images()
    print_result("샘플 이미지", success, message)
    
    # 최종 결과
    print("\n" + "-"*60)
    
    if all_success:
        print(f"{Colors.GREEN}✅ 모든 설정이 완료되었습니다!{Colors.ENDC}")
        print(f"\n다음 명령으로 애플리케이션을 실행하세요:")
        print(f"  {Colors.BLUE}uvicorn server:app --reload --port 8787{Colors.ENDC}")
        print(f"  탐험 퀘스트 UI http://127.0.0.1:8787/  ·  클래식 UI http://127.0.0.1:8787/classic/")
        print(f"  (레거시 Streamlit 앱: pip install -r requirements-legacy.txt && "
              f"streamlit run app.py)")
    else:
        print(f"{Colors.RED}❌ 일부 설정이 누락되었습니다.{Colors.ENDC}")
        print(f"\n위의 문제를 해결한 후 다시 실행해주세요.")
        
        if not check_env_file()[0]:
            print(f"\n{Colors.YELLOW}💡 .env 파일 생성 방법:{Colors.ENDC}")
            print("1. 프로젝트 루트에 .env 파일 생성")
            print("2. 다음 내용 입력:")
            print("   OPENAI_API_KEY=your_actual_api_key_here")
            print("3. 실제 OpenAI API 키로 교체")
    
    print("\n" + "="*60 + "\n")
    
    return 0 if all_success else 1


if __name__ == "__main__":
    sys.exit(main())
