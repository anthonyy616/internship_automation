"""
Email worker — delegates to the EmailSender safety pipeline.

All guardrails (kill switch, warm-up ramp, per-domain caps, LLM
self-check) live in backend.services.email.sender.EmailSender; this
worker loads the job and hands the application to it, then logs the
outcome.
"""


async def send_email(ctx: dict, application_id: str) -> dict:
    """
    Send the follow-up email for an applied application.

    Args:
        ctx: arq worker context (email_sender injected in Phase 5 startup)
        application_id: applications.id UUID as string
    """
    repo = ctx["repo"]
    logger = ctx["event_logger"]

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
            metadata={"note": "Email sender not configured"},
        )
        return {"status": "skipped", "reason": "no_email_sender"}

    await logger.started(
        "email", "send_email",
        application_id=application_id,
        target_url=job.url if job else None,
    )

    try:
        result = await sender.send(application, job)
        if result.success:
            await logger.success(
                "email", "sent",
                application_id=application_id,
                metadata={"to": result.to_address},
            )
            return {"status": "sent"}
        else:
            await logger.failed(
                "email", "not_sent",
                application_id=application_id,
                error_text=result.reason or result.status,
                metadata={"to": result.to_address, "status": result.status},
            )
            return {"status": result.status, "reason": result.reason}
    except Exception as e:
        await logger.failed(
            "email", "send_error",
            application_id=application_id,
            error_text=str(e),
        )
        return {"status": "failed", "reason": str(e)}