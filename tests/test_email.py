"""
Offline tests for the email safety pipeline (no SMTP, no LLM, no DB).

Usage:
    python -m tests.test_email
"""

import asyncio
import sys

from backend.services.email.composer import EmailComposer, guess_contact_emails
from backend.services.email.kill_switch import KillSwitch
from backend.services.email.warmup import Warmup
from backend.services.email.self_check import EmailSelfCheck
from backend.services.email.composer import EmailDraft
from backend.services.config_service import EmailConfig, ProfileConfig


class FakeJob:
    def __init__(self, url="https://careers.acme.com/123", company="Acme",
                 title="Software Engineer Intern", contact_email=None, description=""):
        self.url = url
        self.company = company
        self.title = title
        self.contact_email = contact_email
        self.description = description


class FakeApplication:
    id = "app-1"
    job_id = "job-1"


class FakeRepo:
    def __init__(self, stats=None, today_emails=0, domain_counts=None):
        self.stats = stats or {"total": 0, "sent": 0, "bounced": 0, "failed": 0}
        self.today_emails = today_emails
        self.domain_counts = domain_counts or {}
        self.emails_created = []

    async def get_email_stats_last_hour(self):
        return self.stats

    async def get_today_email_count(self):
        return self.today_emails

    async def get_domain_send_count(self, domain):
        return self.domain_counts.get(domain, 0)

    async def create_email(self, application_id, to_address, subject, body):
        self.emails_created.append({"to": to_address, "subject": subject})
        class R:
            id = "email-1"
        return R()

    async def update_email_status(self, email_id, **kwargs):
        return True


class FakeConfig:
    def __init__(self, email_data=None, profile_data=None):
        self.email_data = email_data or {}
        self.profile_data = profile_data or {}

    async def get_email_config(self):
        return EmailConfig(self.email_data)

    async def get_profile(self):
        return ProfileConfig(self.profile_data)


def test_kill_switch_math():
    ks = KillSwitch()
    repo = FakeRepo(stats={"total": 20, "bounced": 4, "failed": 0, "sent": 16})
    cfg = EmailConfig({"kill_switch_bounce_threshold": 15})
    paused, reason = asyncio.run(ks.is_paused(repo, cfg))
    assert paused is True, "20% failure rate must trip the switch"
    assert "kill switch" in reason

    repo2 = FakeRepo(stats={"total": 20, "bounced": 2, "failed": 0, "sent": 18})
    paused2, _ = asyncio.run(ks.is_paused(repo2, cfg))
    assert paused2 is False, "10% failure rate is under the threshold"

    repo3 = FakeRepo(stats={"total": 3, "bounced": 3, "failed": 0, "sent": 0})
    paused3, _ = asyncio.run(ks.is_paused(repo3, cfg))
    assert paused3 is False, "too few samples to judge"


def test_warmup_cap():
    w = Warmup()
    cfg = EmailConfig({"daily_cap": 50, "warmup_day": 2, "warmup_increment": 5})
    assert w.today_cap(cfg) == 10, "day 2 cap should be 2x increment"

    cfg2 = EmailConfig({"daily_cap": 50, "warmup_day": 20, "warmup_increment": 5})
    assert w.today_cap(cfg2) == 50, "cap is clamped by daily_cap"

    over, sent, cap = asyncio.run(w.over_warmup_cap(FakeRepo(today_emails=11), cfg))
    assert over is True and cap == 10


def test_contact_guessing():
    job = FakeJob(contact_email="hiring@acme.com")
    emails = guess_contact_emails(job)
    assert emails[0] == "hiring@acme.com"

    # No explicit contact, non-ATS host
    job2 = FakeJob(url="https://careers.acme.com/123", contact_email=None)
    emails2 = guess_contact_emails(job2)
    assert emails2[0] == "careers@careers.acme.com"

    # ATS host must never be used for domain guessing
    job3 = FakeJob(url="https://boards.greenhouse.io/acme/jobs/1", contact_email=None)
    assert guess_contact_emails(job3) == []


async def test_composer_requires_contact():
    cfg = FakeConfig()
    c = EmailComposer(config_service=cfg)
    profile = ProfileConfig({"name": "Anthony Ogbuah"})

    # No explicit contact + guessing off -> None
    job = FakeJob(contact_email=None)
    draft = await c.compose(FakeApplication(), job, profile)
    assert draft is None, "must not invent a recipient"

    # Explicit contact -> draft
    job2 = FakeJob(contact_email="hiring@acme.com", company="Acme", title="Intern")
    draft2 = await c.compose(FakeApplication(), job2, profile)
    assert draft2 is not None
    assert draft2.to_address == "hiring@acme.com"
    assert "Acme" in draft2.subject
    assert "Anthony Ogbuah" in draft2.body


async def test_composer_domain_guess_flag():
    cfg = FakeConfig(email_data={"allow_domain_guess": True})
    c = EmailComposer(config_service=cfg)
    profile = ProfileConfig({"name": "Anthony Ogbuah"})
    job = FakeJob(url="https://careers.acme.com/123", contact_email=None)
    draft = await c.compose(FakeApplication(), job, profile)
    assert draft is not None, "guessing enabled -> careers@careers.acme.com"
    assert "@" in draft.to_address


def test_self_check_local_blocks_placeholders():
    sc = EmailSelfCheck()
    job = FakeJob(company="Acme", title="Intern")
    bad = EmailDraft(to_address="x@acme.com", subject="Hi", body="Dear {{NAME}}, please hire [insert skills].")
    result = asyncio.run(sc.validate(bad, job, None))
    assert result.passed is False
    assert result.issues, "must flag placeholder text"

    good = EmailDraft(to_address="x@acme.com", subject="Hi", body="I would love to join Acme as an Intern.")
    result2 = asyncio.run(sc.validate(good, job, None))
    assert result2.passed is True


async def test_sender_blocked_paths():
    from backend.services.email.sender import EmailSender

    # Kill switch tripped -> blocked before any compose
    repo = FakeRepo(stats={"total": 20, "bounced": 6, "failed": 0, "sent": 14})
    cfg = FakeConfig(email_data={"kill_switch_bounce_threshold": 15})
    sender = EmailSender(config_service=cfg, repo=repo, smtp_user="", smtp_password="")
    result = await sender.send(FakeApplication(), FakeJob(contact_email="h@acme.com"))
    assert result.status == "blocked"
    assert "kill switch" in result.reason

    # No contact -> blocked, no email row created
    repo2 = FakeRepo()
    sender2 = EmailSender(config_service=FakeConfig(), repo=repo2, smtp_user="", smtp_password="")
    result2 = await sender2.send(FakeApplication(), FakeJob(contact_email=None))
    assert result2.status == "blocked"
    assert result2.reason == "no contact address found"
    assert repo2.emails_created == []

    # Per-domain cap reached -> blocked
    repo3 = FakeRepo(domain_counts={"acme.com": 3})
    cfg3 = FakeConfig(email_data={"per_domain_cap": 3})
    sender3 = EmailSender(config_service=cfg3, repo=repo3, smtp_user="", smtp_password="")
    result3 = await sender3.send(FakeApplication(), FakeJob(contact_email="h@acme.com"))
    assert result3.status == "blocked"
    assert "per-domain" in result3.reason


def main():
    tests = [
        test_kill_switch_math,
        test_warmup_cap,
        test_contact_guessing,
        test_composer_requires_contact,
        test_composer_domain_guess_flag,
        test_self_check_local_blocks_placeholders,
        test_sender_blocked_paths,
    ]
    failures = 0
    for test in tests:
        try:
            if asyncio.iscoroutinefunction(test):
                asyncio.run(test())
            else:
                test()
            print(f"  PASS  {test.__name__}")
        except Exception as e:
            failures += 1
            import traceback
            print(f"  FAIL  {test.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()