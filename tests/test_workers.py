"""
Offline tests for the arq workers (no Redis, no database).

Exercises the job state machine transitions through scrape_source,
apply_to_job, and send_email with a fake worker context.

Usage:
    python -m tests.test_workers
"""

import asyncio
import sys
from types import SimpleNamespace

from backend.services.sources.base import JobListing
from backend.services.config_service import LimitsConfig, EmailConfig, BlocklistConfig
from backend.workers.scrape_worker import scrape_source
from backend.workers.apply_worker import apply_to_job
from backend.workers.email_worker import send_email


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class FakeJob:
    def __init__(self, id, source="fake", title="Intern", company="Acme",
                 region="UK", url="https://example.com/job", description="", status="discovered"):
        self.id = id
        self.source = source
        self.title = title
        self.company = company
        self.region = region
        self.url = url
        self.description = description
        self.status = status

    def __str__(self):
        return str(self.id)


class FakeApplication:
    def __init__(self, id, job_id, status="queued"):
        self.id = id
        self.job_id = job_id
        self.status = status


class FakeRepo:
    def __init__(self):
        self.jobs = {}            # id -> FakeJob
        self.applications = {}    # id -> FakeApplication
        self.events = []
        self.job_statuses = {}
        self.app_statuses = {}
        self.source_results = {}

    async def get_job_urls(self):
        return [j.url for j in self.jobs.values()]

    async def upsert_job(self, source, external_id, title, company, region, url, description=""):
        if url in {j.url for j in self.jobs.values()}:
            return None
        job_id = f"job-{len(self.jobs) + 1}"
        job = FakeJob(job_id, source, title, company, region, url, description)
        self.jobs[job_id] = job
        return job

    async def update_job_status(self, job_id, status):
        self.job_statuses[job_id] = status
        if job_id in self.jobs:
            self.jobs[job_id].status = status
        return True

    async def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def get_jobs_by_status(self, statuses, limit=50):
        return [j for j in self.jobs.values() if j.status in statuses][:limit]

    async def get_today_application_count(self):
        return sum(1 for a in self.applications.values() if a.status != "failed")

    async def get_application_by_job(self, job_id):
        for a in self.applications.values():
            if a.job_id == job_id:
                return a
        return None

    async def claim_application(self, job_id, resumable_statuses):
        """Mirror the DB's atomic per-job claim (see database.py)."""
        existing = [a for a in self.applications.values() if a.job_id == job_id]
        if existing:
            app = existing[-1]
            if app.status not in resumable_statuses:
                return "skip", app
            app.status = "filling"
            return "proceed", app
        app_id = f"app-{len(self.applications) + 1}"
        app = FakeApplication(app_id, job_id)
        self.applications[app_id] = app
        return "proceed", app

    async def create_application(self, job_id):
        app_id = f"app-{len(self.applications) + 1}"
        app = FakeApplication(app_id, job_id)
        self.applications[app_id] = app
        return app

    async def create_confirmation(self, application_id, question_text, field_type=None,
                                  options=None, telegram_message_id=None):
        if not hasattr(self, "confirmations"):
            self.confirmations = []
        conf = SimpleNamespace(
            id=f"conf-{len(self.confirmations) + 1}",
            application_id=application_id,
            question_text=question_text,
            field_type=field_type,
            status="pending",
        )
        self.confirmations.append(conf)
        return conf

    async def get_application(self, application_id):
        return self.applications.get(application_id)

    async def update_application_status(self, application_id, status):
        self.app_statuses[application_id] = status
        if application_id in self.applications:
            self.applications[application_id].status = status
        return True

    async def save_filled_fields(self, application_id, fields):
        if application_id in self.applications:
            self.applications[application_id].filled_fields = fields
        return True

    async def get_today_email_count(self):
        return 0

    async def log_event(self, **kwargs):
        self.events.append(kwargs)
        return None

    async def record_source_success(self, name):
        self.source_results[name] = "success"

    async def record_source_error(self, name):
        self.source_results[name] = "error"


class FakeLogger:
    """Drop-in for EventLogger with the same async surface."""

    def __init__(self):
        self.events = []

    async def log(self, stage, action, status, **kwargs):
        self.events.append({"stage": stage, "action": action, "status": status, **kwargs})

    async def started(self, stage, action, **kwargs):
        await self.log(stage, action, "started", **kwargs)

    async def success(self, stage, action, **kwargs):
        await self.log(stage, action, "success", **kwargs)

    async def failed(self, stage, action, error_text="", **kwargs):
        await self.log(stage, action, "failed", error_text=error_text, **kwargs)

    async def escalated(self, stage, action, **kwargs):
        await self.log(stage, action, "escalated", **kwargs)


class FakeConfig:
    async def get_blocklist(self):
        return BlocklistConfig({})

    async def get_limits(self):
        return LimitsConfig({"max_applications_per_day": 5})

    async def get_email_config(self):
        return EmailConfig({})

    async def get_sources_config(self):
        return {"fake": True, "blocked": False}

    async def get_keywords(self):
        return ["Software Engineer Intern"]

    async def get_regions(self):
        return ["UK"]


class FakeAdapter:
    name = "fake"
    source_type = "api"

    def __init__(self, jobs):
        self.jobs = jobs

    async def search(self, keywords, region):
        return self.jobs


class FakeRegistry:
    def __init__(self, adapters):
        self._adapters = {a.name: a for a in adapters}

    def get(self, name):
        return self._adapters.get(name)

    @property
    def adapter_names(self):
        return list(self._adapters.keys())


def build_ctx(repo=None, registry=None, applier=None, email_sender=None):
    repo = repo or FakeRepo()
    return {
        "repo": repo,
        "registry": registry,
        "event_logger": FakeLogger(),
        "config_service": FakeConfig(),
        "applier": applier,
        "email_sender": email_sender,
        "job_filter": None,
    }


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

async def test_scrape_worker_persists_filtered_jobs():
    repo = FakeRepo()
    jobs = [
        JobListing(source="fake", external_id="1", title="Software Engineer Intern",
                   company="Acme", url="https://example.com/intern", region="UK"),
        JobListing(source="fake", external_id="2", title="Senior Engineer",
                   company="Acme", url="https://example.com/senior", region="UK"),
    ]
    ctx = build_ctx(repo=repo, registry=FakeRegistry([FakeAdapter(jobs)]))

    result = await scrape_source(ctx, "fake", ["Software Engineer Intern"], "UK")

    assert result["status"] == "ok", result
    assert result["found"] == 2
    assert result["new"] == 1, "senior title should be filtered out"
    assert len(repo.jobs) == 1
    job = list(repo.jobs.values())[0]
    assert job.status == "filtered", "job must land in 'filtered' state"
    assert repo.source_results.get("fake") == "success"


async def test_scrape_worker_skips_duplicate_urls():
    repo = FakeRepo()
    existing = JobListing(source="fake", external_id="1", title="Software Engineer Intern",
                          company="Acme", url="https://example.com/intern", region="UK")
    # Pre-seed: run once
    ctx = build_ctx(repo=repo, registry=FakeRegistry([FakeAdapter([existing])]))
    await scrape_source(ctx, "fake", ["Software Engineer Intern"], "UK")
    # Run again with same URL — should dedup to zero new
    result = await scrape_source(ctx, "fake", ["Software Engineer Intern"], "UK")
    assert result["new"] == 0
    assert len(repo.jobs) == 1


async def test_apply_worker_no_applier_marks_needs_manual():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]))  # no applier

    result = await apply_to_job(ctx, "job-1")

    assert result["status"] == "failed"
    assert result["reason"] == "no_applier"
    assert repo.job_statuses["job-1"] == "failed_needs_manual"
    assert repo.app_statuses["app-1"] == "failed"
    # The failure must be visible in the event log, not silent
    failed_events = [e for e in ctx["event_logger"].events if e["status"] == "failed"]
    assert len(failed_events) >= 1


class StubApplier:
    def __init__(self, success=True, error="", dry_run=False, challenge=False):
        self.success = success
        self.error = error
        self.dry_run = dry_run
        self.challenge = challenge

    async def apply(self, job, application):
        class R:
            pass
        r = R()
        r.success = self.success
        r.applied_via = "form"
        r.error = self.error
        r.needs_input = False
        r.filled_fields = {}
        r.dry_run = self.dry_run
        r.challenge = self.challenge
        return r


async def test_apply_worker_success_transitions():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=True))

    result = await apply_to_job(ctx, "job-1")

    assert result["status"] == "applied"
    assert repo.job_statuses["job-1"] == "applied"
    assert repo.app_statuses["app-1"] == "applied"


async def test_apply_worker_failure_transitions():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=False, error="form too complex"))

    result = await apply_to_job(ctx, "job-1")

    assert result["status"] == "failed"
    # Failed applications land in the manual-review queue, not a dead end
    assert repo.job_statuses["job-1"] == "failed_needs_manual"
    assert repo.app_statuses["app-1"] == "failed"


async def test_apply_worker_dry_run_is_not_recorded_as_applied():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=True, dry_run=True))

    result = await apply_to_job(ctx, "job-1")

    # A dry-run fill is evidence the form was filled, NOT proof of a real
    # application — it must never be recorded as 'applied'.
    assert result["status"] == "dry_run"
    assert repo.job_statuses["job-1"] == "dry_run"
    assert repo.app_statuses["app-1"] == "dry_run"
    actions = [e["action"] for e in ctx["event_logger"].events]
    assert "dry_run_completed" in actions
    assert "applied" not in actions


async def test_apply_worker_resumes_dry_run_and_filling_applications():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    repo.applications["app-1"] = FakeApplication("app-1", "job-1", "dry_run")
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=True))

    result = await apply_to_job(ctx, "job-1")

    # A previously dry-run-filled (or crashed) application can be re-applied
    assert result["status"] == "applied"
    assert repo.app_statuses["app-1"] == "applied"
    assert repo.job_statuses["job-1"] == "applied"


async def test_apply_worker_transient_failure_retries_not_fails():
    """A transient (blocked/network) failure must re-queue with backoff via
    arq Retry — never mark the job failed. This guards the bug where the
    broad `except Exception` swallowed arq's Retry and turned a retryable
    failure into a permanent failed_needs_manual."""
    from arq.worker import Retry

    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(
        repo=repo, registry=FakeRegistry([]),
        applier=StubApplier(success=False, error="blocked by site (HTTP 403)"),
    )
    ctx["job_try"] = 1

    try:
        await apply_to_job(ctx, "job-1")
    except Retry as retry:
        assert retry.defer_score > 0, "backoff must be positive"
    else:
        raise AssertionError("expected arq Retry to be raised for a transient failure")

    # The job must NOT be marked failed — it is waiting for the retry.
    assert repo.job_statuses.get("job-1") in (None, "applying"), repo.job_statuses.get("job-1")
    assert repo.app_statuses.get("app-1") in (None, "filling"), repo.app_statuses.get("app-1")


async def test_apply_worker_escalates_challenge_and_pauses():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=False, challenge=True))

    result = await apply_to_job(ctx, "job-1")

    # A CAPTCHA/login wall must pause + ask the operator, not fail blindly
    assert result["status"] == "paused"
    assert result["reason"] == "challenge"
    assert repo.app_statuses["app-1"] == "paused_awaiting_input"
    assert repo.job_statuses["job-1"] == "paused_awaiting_input", \
        "job must be paused too so stale-recovery can't re-open paid sessions"
    assert any(c.field_type == "challenge" for c in repo.confirmations), "challenge confirmation must be created"
    escalated = [e for e in ctx["event_logger"].events if e["action"] == "challenge_escalated"]
    assert len(escalated) == 1


async def test_apply_worker_respects_daily_limit():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]), applier=StubApplier(success=True))
    # Saturate the daily cap
    for i in range(5):
        repo.applications[f"app-x{i}"] = FakeApplication(f"app-x{i}", "other", "applied")

    result = await apply_to_job(ctx, "job-1")

    assert result["status"] == "skipped"
    assert result["reason"] == "daily_limit"
    assert repo.job_statuses.get("job-1") is None, "job must stay queued"


async def test_email_worker_without_sender_is_pending_not_sent():
    repo = FakeRepo()
    job = FakeJob("job-1", url="https://example.com/job-1")
    repo.jobs["job-1"] = job
    repo.applications["app-1"] = FakeApplication("app-1", "job-1", "applied")
    ctx = build_ctx(repo=repo, registry=FakeRegistry([]))

    result = await send_email(ctx, "app-1")

    assert result["status"] == "skipped"
    assert result["reason"] == "no_email_sender"
    # No 'sent' status may be recorded without a real sender
    sent_events = [e for e in ctx["event_logger"].events if e["action"] == "sent"]
    assert sent_events == []


async def test_scheduler_imports_and_cron_exists():
    from backend.workers import settings as worker_settings
    assert worker_settings.WorkerSettings is not None
    # scrape_source, apply_to_job, send_email, process_queue_now
    assert len(worker_settings.WorkerSettings.functions) == 4
    assert len(worker_settings.WorkerSettings.cron_jobs) == 2
    names = [getattr(f, "__name__", None) for f in worker_settings.WorkerSettings.functions]
    assert "process_queue_now" in names, names


def main():
    tests = [
        test_scrape_worker_persists_filtered_jobs,
        test_scrape_worker_skips_duplicate_urls,
        test_apply_worker_no_applier_marks_needs_manual,
        test_apply_worker_success_transitions,
        test_apply_worker_failure_transitions,
        test_apply_worker_dry_run_is_not_recorded_as_applied,
        test_apply_worker_resumes_dry_run_and_filling_applications,
        test_apply_worker_transient_failure_retries_not_fails,
        test_apply_worker_escalates_challenge_and_pauses,
        test_apply_worker_respects_daily_limit,
        test_email_worker_without_sender_is_pending_not_sent,
        test_scheduler_imports_and_cron_exists,
    ]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
            print(f"  PASS  {test.__name__}")
        except Exception as e:
            failures += 1
            print(f"  FAIL  {test.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()