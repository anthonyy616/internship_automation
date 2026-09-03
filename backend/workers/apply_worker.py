"""
Apply worker — takes a single job through the application lifecycle.

Status transitions handled here:
    queued -> applying -> applied | failed | failed_needs_manual

The tiered applier (Phase 4) is injected into ctx['applier']. Until it
exists, jobs are marked failed_needs_manual instead of silently skipped.
"""


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

    # No duplicate applications for the same job
    existing = await repo.get_application_by_job(job_id)
    if existing is not None:
        return {"status": "skipped", "reason": "already_applied"}

    application = await repo.create_application(job_id)
    if application is None:
        return {"status": "failed", "reason": "application_create_failed"}

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
        if result.success:
            await repo.update_application_status(app_id, "applied")
            await repo.update_job_status(job_id, "applied")
            await logger.success(
                "apply", "applied",
                application_id=app_id,
                target_url=job.url,
                metadata={"via": getattr(result, "applied_via", "form")},
            )
            await _enqueue_email(ctx, app_id)
            return {"status": "applied"}
        else:
            await repo.update_application_status(app_id, "failed")
            await repo.update_job_status(job_id, "failed")
            await logger.failed(
                "apply", "apply_failed",
                application_id=app_id,
                target_url=job.url,
                error_text=getattr(result, "error", "") or "applier returned failure",
            )
            return {"status": "failed", "reason": getattr(result, "error", "")}
    except Exception as e:
        await repo.update_application_status(app_id, "failed")
        await repo.update_job_status(job_id, "failed")
        await logger.failed(
            "apply", "apply_error",
            application_id=app_id,
            target_url=job.url,
            error_text=str(e),
        )
        return {"status": "failed", "reason": str(e)}