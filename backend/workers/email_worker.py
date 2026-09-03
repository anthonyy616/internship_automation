"""
Email worker — sends the follow-up cold email for an application.

Phase 5 injects ctx['email_sender'] (composer + self-check + kill switch).
Until then, the worker records that emailing is pending and leaves the
application in the 'applied' state — no fake 'sent' statuses.
"""


async def send_email(ctx: dict, application_id: str) -> dict:
    """
    Compose and send the follow-up email for an applied application.

    Args:
        ctx: arq worker context
        application_id: applications.id UUID as string
    """
    repo = ctx["repo"]
    logger = ctx["event_logger"]
    config = ctx["config_service"]

    application = await repo.get_application(application_id)
    if application is None:
        return {"status": "failed", "reason": "application_not_found"}

    job = None
    if application.job_id:
        job = await repo.get_job(str(application.job_id))

    sender = ctx.get("email_sender")
    if sender is None:
        await logger.success(
            "email", "email_pending",
            application_id=application_id,
            target_url=job.url if job else None,
            metadata={"note": "Email sender not implemented yet (Phase 5)"},
        )
        return {"status": "skipped", "reason": "no_email_sender"}

    # Daily + per-domain caps (Phase 5 refines this with warm-up and kill switch)
    email_cfg = await config.get_email_config()
    today_emails = await repo.get_today_email_count()
    if today_emails >= email_cfg.effective_daily_cap:
        await logger.success(
            "email", "daily_cap_reached",
            application_id=application_id,
            metadata={"count": today_emails, "max": email_cfg.effective_daily_cap},
        )
        return {"status": "skipped", "reason": "daily_cap"}

    await logger.started(
        "email", "send_email",
        application_id=application_id,
        target_url=job.url if job else None,
    )

    try:
        result = await sender.send(application, job)
        if getattr(result, "success", False):
            await logger.success(
                "email", "sent",
                application_id=application_id,
                metadata={"to": getattr(result, "to_address", "")},
            )
            return {"status": "sent"}
        else:
            await logger.failed(
                "email", "send_failed",
                application_id=application_id,
                error_text=getattr(result, "error", "") or "sender returned failure",
            )
            return {"status": "failed", "reason": getattr(result, "error", "")}
    except Exception as e:
        await logger.failed(
            "email", "send_error",
            application_id=application_id,
            error_text=str(e),
        )
        return {"status": "failed", "reason": str(e)}