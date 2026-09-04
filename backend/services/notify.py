"""
Notify — tells the operator when the bot does something real.

Right now this covers one event: a real application was submitted (the
submit button was clicked, not a dry-run fill). The operator is notified
two ways so there is always proof:

    - Telegram message to TELEGRAM_CHAT_ID (instant)
    - Email to the profile's contact email (the same inbox the ATS will
      send its own confirmation to)

Both are best-effort: a notification failure never fails the application,
it is just logged as an event. If SMTP or Telegram isn't configured the
channel is skipped silently.
"""

import asyncio
import smtplib
import logging
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import List

from backend.config import settings

logger = logging.getLogger(__name__)


class Notifier:
    """Sends operator-facing notifications for real bot actions."""

    def __init__(self, repo=None, config_service=None, event_logger=None, telegram_bot=None):
        self.repo = repo
        self.config_service = config_service
        self.logger = event_logger
        self.telegram_bot = telegram_bot  # optional; built lazily if repo present

    # ------------------------------------------------------------------

    async def application_submitted(self, job, application) -> List[str]:
        """Notify the operator that a real application went through.

        Returns the list of channels that delivered (e.g. ['telegram', 'email']).
        """
        profile = await self._profile()
        contact_email = (profile.get("email") or "").strip()
        if not contact_email:
            return []

        channels = []

        # 1. Telegram
        try:
            bot = self.telegram_bot
            if bot is None and self.repo is not None:
                from backend.services.telegram.bot import TelegramBot
                bot = TelegramBot(repo=self.repo)
            if bot is not None and bot.enabled():
                ok = await bot.notify(
                    f"✅ Application submitted — {job.company}\n\n"
                    f"{job.title}\n{job.url}"
                )
                if ok:
                    channels.append("telegram")
        except Exception as e:
            logger.warning("telegram submit notification failed: %s", e)

        # 2. Email to the operator's own inbox
        if settings.smtp_user and settings.smtp_password and contact_email:
            try:
                ok = await asyncio.to_thread(
                    self._send_email, job, contact_email,
                )
                if ok:
                    channels.append("email")
            except Exception as e:
                logger.warning("email submit notification failed: %s", e)

        if self.logger is not None and channels:
            try:
                await self.logger.success(
                    "system", "submission_notified",
                    application_id=str(application.id),
                    target_url=getattr(job, "url", None),
                    metadata={"channels": channels, "company": job.company},
                )
            except Exception:
                pass
        return channels

    # ------------------------------------------------------------------

    def _send_email(self, job, to_address: str) -> bool:
        msg = MIMEText(
            "Your bot submitted an application.\n\n"
            f"Company : {job.company}\n"
            f"Role    : {job.title}\n"
            f"Link    : {job.url}\n\n"
            "This email is the bot's own confirmation. If the employer's "
            "ATS sends an acknowledgement, it will arrive at this inbox too.",
            "plain", "utf-8",
        )
        msg["Subject"] = f"✅ Application submitted — {job.company}"
        msg["From"] = formataddr(("Internship Bot", settings.smtp_user))
        msg["To"] = to_address

        with smtplib.SMTP(settings.smtp_server, settings.smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_user, [to_address], msg.as_string())
        return True

    async def _profile(self) -> dict:
        if self.config_service is not None:
            try:
                profile = await self.config_service.get_profile()
                return {
                    "name": profile.name,
                    "email": profile.email,
                    "portfolio_url": profile.portfolio_url,
                }
            except Exception:
                pass
        return {}
