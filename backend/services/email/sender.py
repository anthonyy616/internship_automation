"""
Email sender — the single authority for sending follow-up emails.

Every send passes through:
    1. kill switch    (bounce/failure-rate spike -> auto-pause)
    2. warm-up ramp   (daily cap grows day over day)
    3. per-domain cap (max emails to one company domain per day)
    4. compose        (template or LLM-personalized)
    5. self-check     (LLM validation; blocks hallucinated drafts)

Every attempt is recorded in the emails table.
"""

import asyncio
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Optional
from urllib.parse import urlparse

from backend.config import settings
from backend.services.email.composer import (
    EmailComposer, EmailDraft, SendResult,
)
from backend.services.email.kill_switch import kill_switch
from backend.services.email.self_check import email_self_check
from backend.services.email.warmup import warmup


class EmailSender:
    """Sends follow-up emails with the full safety pipeline."""

    def __init__(
        self,
        config_service=None,
        repo=None,
        event_logger=None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_server: Optional[str] = None,
        smtp_port: Optional[int] = None,
    ):
        self.config_service = config_service
        self.repo = repo
        self.logger = event_logger
        self.smtp_user = smtp_user or settings.smtp_user
        self.smtp_password = smtp_password or settings.smtp_password
        self.smtp_server = smtp_server or settings.smtp_server
        self.smtp_port = smtp_port or settings.smtp_port
        self.composer = EmailComposer(config_service=config_service)

    async def send(self, application, job) -> SendResult:
        """Full pipeline — returns SendResult; never raises for send failures."""
        if self.repo is None:
            return SendResult(False, status="blocked", reason="no repo configured")

        email_cfg = await self._get_email_config()
        profile = await self._get_profile()

        # 1. Kill switch
        paused, reason = await kill_switch.is_paused(self.repo, email_cfg)
        if paused:
            await self._log("email", "kill_switch_paused", application_id=str(application.id),
                            metadata={"reason": reason})
            return SendResult(False, status="blocked", reason=reason)

        # 2. Warm-up daily cap
        over, sent_today, cap = await warmup.over_warmup_cap(self.repo, email_cfg)
        if over:
            return SendResult(False, status="blocked",
                              reason=f"warm-up cap reached ({sent_today}/{cap})")

        # 3. Compose
        draft = await self.composer.compose(application, job, profile)
        if draft is None:
            return SendResult(False, status="blocked", reason="no contact address found")

        # 4. Per-domain cap
        domain = (urlparse("mailto:" + draft.to_address).path or "").split("@")[-1]
        domain_sent = await self.repo.get_domain_send_count(domain)
        if domain_sent >= email_cfg.per_domain_cap:
            return SendResult(False, status="blocked",
                              reason=f"per-domain cap reached for {domain} ({domain_sent}/{email_cfg.per_domain_cap})")

        # 5. Self-check
        check = await email_self_check.validate(draft, job, profile)
        if not check.passed:
            email_row = await self.repo.create_email(
                application_id=str(application.id),
                to_address=draft.to_address,
                subject=draft.subject,
                body=draft.body,
            )
            if email_row:
                await self.repo.update_email_status(
                    str(email_row.id),
                    self_check_status="failed",
                    self_check_notes="; ".join(check.issues),
                )
            await self._log("email", "self_check_failed", application_id=str(application.id),
                            metadata={"to": draft.to_address, "issues": check.issues})
            return SendResult(False, to_address=draft.to_address, status="blocked",
                              reason="self-check failed: " + "; ".join(check.issues))

        # 6. Record + send
        email_row = await self.repo.create_email(
            application_id=str(application.id),
            to_address=draft.to_address,
            subject=draft.subject,
            body=draft.body,
        )
        if email_row:
            await self.repo.update_email_status(str(email_row.id), self_check_status="passed")

        sent = await self._smtp_send(draft)
        if not sent:
            return SendResult(False, to_address=draft.to_address, status="failed",
                              reason="smtp delivery failed")

        if email_row:
            from datetime import datetime
            await self.repo.update_email_status(str(email_row.id), sent_at=datetime.utcnow())

        await self._log("email", "sent", application_id=str(application.id),
                        metadata={"to": draft.to_address})
        return SendResult(True, to_address=draft.to_address, status="sent")

    # ------------------------------------------------------------------

    async def _smtp_send(self, draft: EmailDraft) -> bool:
        if not self.smtp_user or not self.smtp_password:
            return False
        try:
            return await asyncio.to_thread(self._smtp_send_sync, draft)
        except Exception:
            return False

    def _smtp_send_sync(self, draft: EmailDraft) -> bool:
        msg = MIMEText(draft.body, "plain", "utf-8")
        msg["Subject"] = draft.subject
        msg["From"] = formataddr(("Internship Bot", self.smtp_user))
        msg["To"] = draft.to_address

        with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, [draft.to_address], msg.as_string())
        return True

    async def _get_email_config(self):
        if self.config_service is not None:
            return await self.config_service.get_email_config()
        from backend.services.config_service import EmailConfig
        return EmailConfig({})

    async def _get_profile(self):
        if self.config_service is not None:
            return await self.config_service.get_profile()
        from backend.services.config_service import ProfileConfig
        return ProfileConfig({})

    async def _log(self, stage: str, action: str, **kwargs):
        if self.logger is not None:
            try:
                await self.logger.success(stage, action, **kwargs)
            except Exception:
                pass