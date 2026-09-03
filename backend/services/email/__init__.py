"""
Email safety package — compose, self-check, send, and guardrails.

Pipeline (see sender.EmailSender):
    kill switch -> warm-up cap -> per-domain cap -> compose -> self-check -> SMTP
"""

from backend.services.email.sender import EmailSender
from backend.services.email.composer import EmailComposer, EmailDraft, SendResult
from backend.services.email.self_check import EmailSelfCheck, SelfCheckResult
from backend.services.email.kill_switch import KillSwitch
from backend.services.email.warmup import Warmup

__all__ = [
    "EmailSender",
    "EmailComposer",
    "EmailDraft",
    "SendResult",
    "EmailSelfCheck",
    "SelfCheckResult",
    "KillSwitch",
    "Warmup",
]