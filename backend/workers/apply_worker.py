"""
Apply worker — takes a single job through the application lifecycle.

Status transitions handled here:
    queued -> applying -> applied | paused_awaiting_input | dry_run | failed

The tiered applier (Phase 4) is injected into ctx['applier']. Until it
exists, jobs are marked failed_needs_manual instead of silently skipped.
"""

from urllib.parse import urlparse

from backend.config import settings

PACE_KEY = "apply:pace:{}"


async def _pace_allowed(ctx: dict, host: str) -> bool:
    """T4-lite: don't hammer one domain — a real site flags bursts of
    applications from a single IP. The previous apply to this host must
    be older than APPLY_PACE_SECONDS."""
    if not host:
        return True
    redis = ctx.get("redis")
    if redis is None:
        return True
    try:
        return await redis.get(PACE_KEY.format(host)) is None
    except Exception:
        return True


async def _mark_apply_done(ctx: dict, host: str):
    """Record that an apply attempt to this host just happened, so the
    next job for the same domain waits (pacing)."""
    if not host:
        return
    redis = ctx.get("redis")
    if redis is None:
        return
    try:
        await redis.set(PACE_KEY.format(host), "1", ex=settings.apply_pace_seconds)
    except Exception:
        pass


# Transient failures worth retrying with backoff (site hiccups, bot walls)
# — vs structural failures (no form, low confidence) that never improve.
_TRANSIENT_MARKERS = (
    "blocked by site", "http 429", "http 403", "http 451",
    "navigation failed", "net::", "timeout", "connection reset",
)


def _is_transient(error: str) -> bool:
    low = (error or "").lower()
    return any(m in low for m in _TRANSIENT_MARKERS)


def _raise_retry(ctx: dict):
    """Re-queue this job with exponential backoff via arq Retry (bounded
    by arq's max_tries, so it can never retry forever)."""
    from arq.worker import Retry
    tries = int(ctx.get("job_try", 1) or 1)
    defer = min(60 * (2 ** (tries - 1)), 600)
    print(f"[apply] transient failure — retry #{tries} in {defer}s")
    raise Retry(defer=defer)


async def _enqueue_email(ctx: dict, application_id: str):
    """Best-effort: queue the follow-up email for an applied application."""
    redis = ctx.get("redis")
    if redis is not None and hasattr(redis, "enqueue_job"):
        try:
            await redis.enqueue_job("send_email", application_id)
        except Exception:
            pass


async def apply_to_job(ctx: dict, job_id: str) -> dict:
    """
    Apply to a single job using the tiered applier.

    Args:
        ctx: arq worker context
        job_id: jobs.id UUID as string
    """
    repo = ctx["repo"]
    logger = ctx["event_logger"]
    config = ctx["config_service"]

    job = await repo.get_job(job_id)
    if job is None:
        return {"status": "failed", "reason": "job_not_found"}

    # Daily cap check
    limits = await config.get_limits()
    today_count = await repo.get_today_application_count()
    if today_count >= limits.max_applications_per_day:
        await logger.success(
            "apply", "daily_limit_reached",
            target_url=job.url,
            metadata={"count": today_count, "max": limits.max_applications_per_day},
        )
        return {"status": "skipped", "reason": "daily_limit"}

    # Per-domain pacing: leave the job 'queued' — the cron re-drains it
    # once the pace window has elapsed.
    host = urlparse(job.url).netloc or ""
    if not await _pace_allowed(ctx, host):
        return {"status": "skipped", "reason": "paced"}

    # No duplicate applications — atomically claim the single application
    # row for this job. The advisory lock serialises concurrent runs of the
    # same job (duplicate queue entries, cron + manual click) so they can't
    # both pass the dedupe check and double-submit. Resumable states are
    # restarted-and-refilled: paused_awaiting_input (user is answering a
    # Telegram question), filling (crashed mid-form), dry_run (filled +
    # screenshotted but never submitted).
    claim, application = await repo.claim_application(
        job_id, ("paused_awaiting_input", "filling", "dry_run")
    )
    if claim == "skip":
        return {"status": "skipped", "reason": "already_applied"}

    app_id = str(application.id)
    await repo.update_job_status(job_id, "applying")

    applier = ctx.get("applier")
    if applier is None:
        # Phase 4 injects the tiered applier; until then fail honestly
        await repo.update_application_status(app_id, "failed")
        await repo.update_job_status(job_id, "failed_needs_manual")
        await logger.failed(
            "apply", "no_applier",
            application_id=app_id,
            target_url=job.url,
            error_text="Applier service not implemented yet (Phase 4)",
        )
        return {"status": "failed", "reason": "no_applier"}

    await logger.started(
        "apply", "apply",
        application_id=app_id,
        target_url=job.url,
    )

    try:
        result = await applier.apply(job, application)
        await _mark_apply_done(ctx, host)

        # Transient (blocked / network) — arq requeues with backoff instead
        # of marking the job permanently failed.
        if not result.success and _is_transient(getattr(result, "error", "")):
            _raise_retry(ctx)

        if getattr(result, "challenge", False):
            # CAPTCHA / login wall — pause and ask the operator to solve it
            # in a real browser (their answer resumes the application).
            await repo.save_filled_fields(app_id, dict(result.filled_fields))
            await repo.update_application_status(app_id, "paused_awaiting_input")
            try:
                await repo.create_confirmation(
                    application_id=app_id,
                    question_text=(
                        f"Human verification required on {host or 'this site'}.\n"
                        f"Open this URL in a normal browser, solve the challenge, "
                        f"then reply 'done' (or 'manual' to mark it manual):\n{job.url}"
                    ),
                    field_type="challenge",
                )
            except Exception:
                pass
            await logger.escalated(
                "apply", "challenge_escalated",
                application_id=app_id,
                target_url=job.url,
                screenshot_url=getattr(result, "screenshot_url", "") or None,
                error_text=getattr(result, "error", "") or "human verification required",
                metadata={"company": job.company, "title": job.title},
            )
            return {"status": "paused", "reason": "challenge"}

        if result.needs_input:
            # Category-A question hit — save progress and pause for the user
            await repo.save_filled_fields(app_id, dict(result.filled_fields))
            await repo.update_application_status(app_id, "paused_awaiting_input")
            await logger.escalated(
                "apply", "paused_awaiting_input",
                application_id=app_id,
                target_url=job.url,
                error_text=result.error or "Category-A question encountered",
            )
            return {"status": "paused", "reason": "awaiting_user_input"}
        if getattr(result, "dry_run", False) and result.success:
            # Dry-run: the form was filled and screenshotted but submit was
            # NOT clicked. Record it honestly so dashboards/status don't
            # report a real application that never happened.
            await repo.update_application_status(app_id, "dry_run")
            await repo.update_job_status(job_id, "dry_run")
            await logger.success(
                "apply", "dry_run_completed",
                application_id=app_id,
                target_url=job.url,
                screenshot_url=getattr(result, "screenshot_url", ""),
                metadata={
                    "submitted": False,
                    "filled_fields": len(result.filled_fields or {}),
                },
            )
            return {"status": "dry_run"}
        if result.success:
            await repo.update_application_status(app_id, "applied")
            await repo.update_job_status(job_id, "applied")
            # The screenshot right before submit is the operator's proof the
            # form was genuinely filled by a real browser session.
            await logger.success(
                "apply", "applied",
                application_id=app_id,
                target_url=job.url,
                screenshot_url=getattr(result, "screenshot_url", "") or None,
                metadata={
                    "via": getattr(result, "applied_via", "form"),
                    "ats": getattr(result, "ats_platform", None) or getattr(application, "ats_platform", None),
                    "company": job.company,
                    "title": job.title,
                    "filled_fields": len(getattr(result, "filled_fields", None) or {}),
                },
            )
            await _enqueue_email(ctx, app_id)
            # Tell the operator (Telegram + email) that a real submission
            # happened — their proof the bot is working.
            notifier = ctx.get("notifier")
            if notifier is not None:
                try:
                    await notifier.application_submitted(job, application)
                except Exception as e:
                    logger.warning("notifier failed: %s", e)
            return {"status": "applied"}
        else:
            await repo.update_application_status(app_id, "failed")
            await repo.update_job_status(job_id, "failed_needs_manual")
            # The pre-submit screenshot (or the no-form dead-end shot) is the
            # operator's evidence of what actually happened.
            await logger.failed(
                "apply", "apply_failed",
                application_id=app_id,
                target_url=job.url,
                screenshot_url=getattr(result, "screenshot_url", "") or None,
                error_text=getattr(result, "error", "") or "applier returned failure",
                metadata={"company": job.company, "title": job.title},
            )
            return {"status": "failed", "reason": getattr(result, "error", "")}
    except Exception as e:
        if _is_transient(str(e)):
            _raise_retry(ctx)
        await repo.update_application_status(app_id, "failed")
        await repo.update_job_status(job_id, "failed_needs_manual")
        await logger.failed(
            "apply", "apply_error",
            application_id=app_id,
            target_url=job.url,
            error_text=str(e),
            metadata={"company": job.company, "title": job.title},
        )
        return {"status": "failed", "reason": str(e)}