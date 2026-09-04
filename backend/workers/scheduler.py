"""
Scheduler — arq cron jobs that drive the whole pipeline.

    schedule_scraping  — every 30 min: fan out one scrape_source task per
                         enabled source × configured region
    process_queue      — every 5 min: move discovered/filtered jobs into
                         the apply queue, respecting the daily cap
"""


async def schedule_scraping(ctx: dict) -> dict:
    """Enqueue a scrape_source task for every enabled source × region."""
    config = ctx["config_service"]
    registry = ctx["registry"]
    logger = ctx["event_logger"]

    sources_cfg = await config.get_sources_config()
    keywords = await config.get_keywords()
    regions = await config.get_regions()

    enabled = [
        name for name, on in sources_cfg.items()
        if on and registry.get(name) is not None
    ]

    if not enabled or not keywords or not regions:
        await logger.success(
            "system", "schedule_scraping_skipped",
            metadata={"enabled_sources": len(enabled), "keywords": len(keywords), "regions": len(regions)},
        )
        return {"status": "skipped"}

    redis = ctx.get("redis")
    enqueued = 0
    if redis is not None and hasattr(redis, "enqueue_job"):
        for source_name in enabled:
            for region in regions:
                try:
                    await redis.enqueue_job("scrape_source", source_name, keywords, region)
                    enqueued += 1
                except Exception:
                    pass

    await logger.success(
        "system", "scheduled_scrape",
        metadata={"sources": enabled, "regions": regions, "tasks_enqueued": enqueued},
    )
    return {"status": "ok", "enqueued": enqueued}


async def process_queue(ctx: dict) -> dict:
    """Move jobs into the apply queue (with cap).

    Picks up discovered/filtered/queued jobs, requeues stale 'applying'
    jobs (worker was killed mid-run), and — once dry-run mode is disabled —
    re-applies 'dry_run' jobs so they get submitted for real.
    """
    repo = ctx["repo"]
    config = ctx["config_service"]
    logger = ctx["event_logger"]

    limits = await config.get_limits()
    today_count = await repo.get_today_application_count()
    remaining = max(0, limits.max_applications_per_day - today_count)
    if remaining == 0:
        return {"status": "skipped", "reason": "daily_limit_reached"}

    try:
        apply_cfg = await config.get_apply_config()
        dry_run_on = bool(apply_cfg.get("dry_run", True))
    except Exception:
        dry_run_on = True

    statuses = ["discovered", "filtered", "queued"]
    if not dry_run_on:
        # Going live: dry-run-filled jobs are now submitted for real
        statuses.append("dry_run")

    jobs = await repo.get_jobs_by_status(statuses, limit=remaining)

    # Recover applies that were killed mid-browser-session
    stale = await repo.get_stale_applying_jobs(minutes=20, limit=remaining - len(jobs) if len(jobs) < remaining else 0)
    for job in stale:
        if job.id not in {j.id for j in jobs}:
            jobs.append(job)

    if not jobs:
        return {"status": "ok", "enqueued": 0}

    # T4: prioritise domains with a proven auto-apply track record so the
    # queue spends its daily cap where it actually converts.
    try:
        from urllib.parse import urlparse
        stats = await repo.get_domain_apply_stats(limit=100)
        rate: dict = {}
        for r in stats:
            applied = r.get("applied") or 0
            failed = r.get("failed") or 0
            total = applied + failed
            rate[r["domain"]] = applied / total if total else 0.5

        def _host(url: str) -> str:
            try:
                return urlparse(url or "").netloc
            except Exception:
                return ""

        jobs.sort(key=lambda j: rate.get(_host(getattr(j, "url", "")), 0.5), reverse=True)
    except Exception:
        pass

    redis = ctx.get("redis")
    enqueued = 0
    requeued_stale = 0
    for job in jobs:
        was_stale = job.status == "applying"
        if was_stale:
            requeued_stale += 1
        await repo.update_job_status(str(job.id), "queued")
        if redis is not None and hasattr(redis, "enqueue_job"):
            try:
                await redis.enqueue_job("apply_to_job", str(job.id))
                enqueued += 1
            except Exception:
                pass

    await logger.success(
        "system", "queue_processed",
        metadata={
            "jobs_moved": len(jobs),
            "enqueued": enqueued,
            "stale_recovered": requeued_stale,
            "remaining_cap": remaining,
        },
    )
    return {"status": "ok", "enqueued": enqueued}


async def process_queue_now(ctx: dict) -> dict:
    """Enqueueable alias of process_queue — lets the API request an
    immediate drain instead of waiting for the next cron sweep."""
    return await process_queue(ctx)