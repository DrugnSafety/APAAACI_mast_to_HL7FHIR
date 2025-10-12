"""
PDF Generator Utility
Markdown을 PDF로 변환하는 유틸리티
"""

import logging
from pathlib import Path
from typing import Optional
import markdown
from io import BytesIO

# WeasyPrint는 선택적 의존성으로 처리
try:
    from weasyprint import HTML, CSS
    from weasyprint.text.fonts import FontConfiguration
    WEASYPRINT_AVAILABLE = True
except ImportError:
    WEASYPRINT_AVAILABLE = False
    HTML = None
    CSS = None
    FontConfiguration = None

# ReportLab은 기본 PDF 생성기
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)


class PDFGenerator:
    """Markdown to PDF 변환기"""
    
    def __init__(self):
        if WEASYPRINT_AVAILABLE:
            self.font_config = FontConfiguration()
        else:
            self.font_config = None
        self.css = self._get_default_css()
    
    def _get_default_css(self) -> str:
        """기본 CSS 스타일"""
        return """
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;700&display=swap');
        
        body {
            font-family: 'Noto Sans KR', sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 800px;
            margin: 0 auto;
            padding: 40px;
        }
        
        h1 {
            color: #667eea;
            border-bottom: 3px solid #667eea;
            padding-bottom: 10px;
            margin-bottom: 30px;
        }
        
        h2 {
            color: #764ba2;
            margin-top: 30px;
            margin-bottom: 20px;
        }
        
        h3 {
            color: #555;
            margin-top: 20px;
        }
        
        ul, ol {
            margin-left: 20px;
            margin-bottom: 20px;
        }
        
        li {
            margin-bottom: 8px;
        }
        
        p {
            margin-bottom: 15px;
        }
        
        strong {
            color: #222;
            font-weight: 600;
        }
        
        blockquote {
            border-left: 4px solid #667eea;
            padding-left: 20px;
            margin-left: 0;
            color: #666;
            font-style: italic;
        }
        
        code {
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 20px;
        }
        
        th, td {
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }
        
        th {
            background-color: #f8f9fa;
            font-weight: 600;
        }
        
        hr {
            border: none;
            border-top: 2px solid #e0e0e0;
            margin: 30px 0;
        }
        
        .page-break {
            page-break-after: always;
        }
        
        @media print {
            body {
                padding: 20px;
            }
        }
        """
    
    def markdown_to_pdf(
        self,
        markdown_content: str,
        output_path: Optional[Path] = None
    ) -> bytes:
        """
        Markdown을 PDF로 변환
        
        Args:
            markdown_content: Markdown 텍스트
            output_path: 출력 PDF 파일 경로 (선택)
            
        Returns:
            PDF 바이트 데이터
        """
        # ReportLab을 기본으로 사용
        return self.markdown_to_pdf_reportlab(markdown_content, output_path)
    
    def markdown_to_pdf_weasyprint(
        self,
        markdown_content: str,
        output_path: Optional[Path] = None,
        custom_css: Optional[str] = None
    ) -> bytes:
        """
        WeasyPrint를 사용한 PDF 생성
        
        Args:
            markdown_content: Markdown 텍스트
            output_path: 출력 PDF 파일 경로 (선택)
            custom_css: 사용자 정의 CSS (선택)
            
        Returns:
            PDF 바이트 데이터
        """
        if not WEASYPRINT_AVAILABLE:
            logger.warning("WeasyPrint가 설치되지 않아 ReportLab을 사용합니다.")
            return self.markdown_to_pdf_reportlab(markdown_content, output_path)
        
        try:
            # Markdown to HTML 변환
            html_content = markdown.markdown(
                markdown_content,
                extensions=[
                    'markdown.extensions.tables',
                    'markdown.extensions.fenced_code',
                    'markdown.extensions.nl2br',
                    'markdown.extensions.toc'
                ]
            )
            
            # HTML 템플릿
            full_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>알레르기 관리 리포트</title>
            </head>
            <body>
                {html_content}
            </body>
            </html>
            """
            
            # CSS 적용
            css_to_use = custom_css or self.css
            
            # PDF 생성
            html = HTML(string=full_html)
            css = CSS(string=css_to_use, font_config=self.font_config)
            
            # PDF 바이트 생성
            pdf_bytes = html.write_pdf(
                stylesheets=[css],
                font_config=self.font_config
            )
            
            # 파일로 저장 (경로가 제공된 경우)
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(pdf_bytes)
                logger.info(f"PDF 생성 완료: {output_path}")
            
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"WeasyPrint PDF 생성 실패, ReportLab 사용: {e}")
            return self.markdown_to_pdf_reportlab(markdown_content, output_path)
    
    def markdown_to_pdf_reportlab(
        self,
        markdown_content: str,
        output_path: Optional[Path] = None
    ) -> bytes:
        """
        ReportLab을 사용한 PDF 생성
        
        Args:
            markdown_content: Markdown 텍스트
            output_path: 출력 PDF 파일 경로 (선택)
            
        Returns:
            PDF 바이트 데이터
        """
        import re
        
        try:
            # BytesIO 버퍼 생성
            buffer = BytesIO()
            
            # 문서 생성
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=18,
            )
            
            # 스타일 설정
            styles = getSampleStyleSheet()
            styles.add(ParagraphStyle(
                name='KoreanBody',
                parent=styles['Normal'],
                fontName='Helvetica',
                fontSize=10,
                leading=14,
                alignment=TA_LEFT
            ))
            styles.add(ParagraphStyle(
                name='KoreanHeading1',
                parent=styles['Heading1'],
                fontName='Helvetica-Bold',
                fontSize=16,
                leading=20,
                alignment=TA_LEFT,
                spaceAfter=20
            ))
            styles.add(ParagraphStyle(
                name='KoreanHeading2',
                parent=styles['Heading2'],
                fontName='Helvetica-Bold',
                fontSize=14,
                leading=18,
                alignment=TA_LEFT,
                spaceAfter=15
            ))
            styles.add(ParagraphStyle(
                name='KoreanHeading3',
                parent=styles['Heading3'],
                fontName='Helvetica-Bold',
                fontSize=12,
                leading=16,
                alignment=TA_LEFT,
                spaceAfter=10
            ))
            
            # 스토리 생성
            story = []
            
            # Markdown을 처리
            lines = markdown_content.split('\n')
            in_list = False
            list_counter = 0
            
            for line in lines:
                line = line.strip()
                
                if not line:
                    # 빈 줄
                    if in_list:
                        in_list = False
                        list_counter = 0
                    story.append(Spacer(1, 0.1*inch))
                    continue
                
                # 제목 처리
                if line.startswith('#'):
                    level = len(line) - len(line.lstrip('#'))
                    text = line.lstrip('#').strip()
                    
                    # HTML 이스케이프
                    text = text.replace('&', '&amp;')
                    text = text.replace('<', '&lt;')
                    text = text.replace('>', '&gt;')
                    
                    if level == 1:
                        p = Paragraph(f"<b>{text}</b>", styles['KoreanHeading1'])
                    elif level == 2:
                        p = Paragraph(f"<b>{text}</b>", styles['KoreanHeading2'])
                    else:
                        p = Paragraph(f"<b>{text}</b>", styles['KoreanHeading3'])
                    story.append(p)
                    story.append(Spacer(1, 0.1*inch))
                
                # 리스트 처리
                elif line.startswith('- ') or line.startswith('* '):
                    in_list = True
                    text = line[2:].strip()
                    text = text.replace('&', '&amp;')
                    text = text.replace('<', '&lt;')
                    text = text.replace('>', '&gt;')
                    
                    # 불릿 포인트 추가
                    p = Paragraph(f"• {text}", styles['KoreanBody'])
                    story.append(p)
                    story.append(Spacer(1, 0.05*inch))
                
                elif line[0:1].isdigit() and line[1:3] in ['. ', ') ']:
                    in_list = True
                    text = line[3:].strip() if line[1] == '.' else line[2:].strip()
                    text = text.replace('&', '&amp;')
                    text = text.replace('<', '&lt;')
                    text = text.replace('>', '&gt;')
                    
                    list_counter += 1
                    p = Paragraph(f"{list_counter}. {text}", styles['KoreanBody'])
                    story.append(p)
                    story.append(Spacer(1, 0.05*inch))
                
                # 일반 텍스트
                else:
                    # HTML 이스케이프
                    text = line.replace('&', '&amp;')
                    text = text.replace('<', '&lt;')
                    text = text.replace('>', '&gt;')
                    
                    # 마크다운 포맷 처리 (간단한 변환)
                    # 볼드 처리 - **text** 또는 __text__
                    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
                    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
                    
                    # 이탤릭 처리 - *text* 또는 _text_
                    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
                    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
                    
                    p = Paragraph(text, styles['KoreanBody'])
                    story.append(p)
                    story.append(Spacer(1, 0.1*inch))
            
            # PDF 생성
            doc.build(story)
            
            # 바이트 데이터 가져오기
            pdf_bytes = buffer.getvalue()
            buffer.close()
            
            # 파일로 저장 (경로가 제공된 경우)
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(pdf_bytes)
                logger.info(f"PDF 생성 완료 (ReportLab): {output_path}")
            
            return pdf_bytes
            
        except Exception as e:
            logger.error(f"PDF 생성 실패 (ReportLab): {e}")
            raise


# 싱글톤 인스턴스
_pdf_generator: Optional[PDFGenerator] = None


def get_pdf_generator() -> PDFGenerator:
    """PDF 생성기 싱글톤 인스턴스 반환"""
    global _pdf_generator
    if _pdf_generator is None:
        _pdf_generator = PDFGenerator()
    return _pdf_generator