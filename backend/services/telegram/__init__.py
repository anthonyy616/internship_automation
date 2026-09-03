"""
Telegram escalation service.

Polls Telegram for pending confirmation questions (Category-A escalations
from the applier), delivers them to the configured chat with inline
answer buttons, and resumes the paused application when the user answers
— either by tapping an option or by replying to the message with text.
"""

from backend.services.telegram.bot import TelegramBot

__all__ = ["TelegramBot"]