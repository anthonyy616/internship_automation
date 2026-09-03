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
    """Move discovered/filtered/queued jobs into the apply queue (with cap)."""
    repo = ctx["repo"]
    config = ctx["config_service"]
    logger = ctx["event_logger"]

    limits = await config.get_limits()
    today_count = await repo.get_today_application_count()
    remaining = max(0, limits.max_applications_per_day - today_count)
    if remaining == 0:
        return {"status": "skipped", "reason": "daily_limit_reached"}

    jobs = await repo.get_jobs_by_status(["discovered", "filtered", "queued"], limit=remaining)
    if not jobs:
        return {"status": "ok", "enqueued": 0}

    redis = ctx.get("redis")
    enqueued = 0
    for job in jobs:
        await repo.update_job_status(str(job.id), "queued")
        if redis is not None and hasattr(redis, "enqueue_job"):
            try:
                await redis.enqueue_job("apply_to_job", str(job.id))
                enqueued += 1
            except Exception:
                pass

    await logger.success(
        "system", "queue_processed",
        metadata={"jobs_moved": len(jobs), "enqueued": enqueued, "remaining_cap": remaining},
    )
    return {"status": "ok", "enqueued": enqueued}