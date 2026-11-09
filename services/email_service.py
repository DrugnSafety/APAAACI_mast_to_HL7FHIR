"""Email Service — 결과(리포트·카드뉴스·FHIR)를 SMTP 로 보낸다.

SMTP_HOST 와 SMTP_FROM 이 없으면 `configured` 가 False 다. 호출부(server.py)는 그때 메일을 보내지 않고
'설정되지 않음' 오류를 돌려주며, /api/health 의 services.email 로 화면이 메뉴를 숨길 수 있다.
본문·첨부 내용은 로그에 남기지 않는다.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import List, Optional, Tuple

from config.settings import settings

logger = logging.getLogger(__name__)

# (파일명, MIME 타입, 내용)
Attachment = Tuple[str, str, bytes]


class EmailNotConfigured(RuntimeError):
    pass


class EmailService:
    @property
    def configured(self) -> bool:
        return bool(settings.smtp_host.strip() and settings.smtp_from.strip())

    def send(self, to: str, subject: str, html: str, text: str,
             attachments: Optional[List[Attachment]] = None) -> None:
        """한 통을 보낸다. 실패하면 smtplib/OSError 예외가 그대로 올라간다."""
        if not self.configured:
            raise EmailNotConfigured("SMTP is not configured")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = (formataddr((settings.smtp_from_name, settings.smtp_from))
                       if settings.smtp_from_name else settings.smtp_from)
        msg["To"] = to
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1] or None)
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        for filename, mime, data in attachments or []:
            maintype, _, subtype = mime.partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)

        mode = (settings.smtp_tls or "starttls").strip().lower()
        host, port, timeout = settings.smtp_host.strip(), settings.smtp_port, settings.smtp_timeout
        if mode == "ssl":
            smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=timeout)
        try:
            if mode == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        finally:
            try:
                smtp.quit()
            except Exception:  # noqa: BLE001
                pass
        logger.info("결과 메일 발송 완료 (첨부 %d개)", len(attachments or []))


_svc: Optional[EmailService] = None


def get_email_service() -> EmailService:
    global _svc
    if _svc is None:
        _svc = EmailService()
    return _svc
