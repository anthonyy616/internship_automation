"""
Scrape worker — polls a single source adapter, filters the results, and
persists new jobs to the database.

Status transitions handled here:
    (new job) -> discovered -> filtered
"""

from typing import List

from backend.services.filter import job_filter


async def scrape_source(
    ctx: dict,
    source_name: str,
    keywords: List[str],
    region: str,
) -> dict:
    """
    Search one source adapter and save new jobs.

    Args:
        ctx: arq worker context (registry, repo, event_logger, config_service)
        source_name: adapter name, e.g. 'remotive' or 'jobberman'
        keywords: search keywords from config
        region: target region, e.g. 'EU', 'UK', 'Nigeria', 'Turkiye'
    """
    registry = ctx["registry"]
    repo = ctx["repo"]
    logger = ctx["event_logger"]
    config = ctx["config_service"]
    flt = ctx.get("job_filter") or job_filter

    adapter = registry.get(source_name)
    if adapter is None:
        await logger.failed(
            "scrape", "source_not_found",
            error_text=f"No adapter registered for '{source_name}'",
            metadata={"source": source_name},
        )
        return {"status": "failed", "reason": "source_not_found"}

    await logger.started(
        "scrape", "search",
        metadata={"source": source_name, "region": region},
    )

    try:
        jobs = await adapter.search(keywords, region)
    except Exception as e:
        await repo.record_source_error(source_name)
        await logger.failed(
            "scrape", "search",
            error_text=str(e),
            metadata={"source": source_name, "region": region},
        )
        return {"status": "failed", "reason": "search_error", "error": str(e)}

    if not jobs:
        await repo.record_source_success(source_name)
        await logger.success(
            "scrape", "search_complete",
            metadata={"source": source_name, "region": region, "found": 0},
        )
        return {"status": "ok", "found": 0, "new": 0}

    # Deterministic filtering: eligibility, blocklist, URL dedup
    blocklist = await config.get_blocklist()
    seen_urls = set(await repo.get_job_urls())
    filtered = await flt.filter_jobs(
        jobs,
        seen_urls=seen_urls,
        blocked_companies=blocklist.companies,
        blocked_domains=blocklist.domains,
    )

    new_count = 0
    for job in filtered:
        saved = await repo.upsert_job(
            source=job.source,
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            region=job.region,
            url=job.url,
            description=job.description,
        )
        if saved:
            new_count += 1
            await repo.update_job_status(str(saved.id), "filtered")
            await logger.success(
                "scrape", "found_job",
                target_url=job.url,
                metadata={"source": source_name, "title": job.title, "job_id": str(saved.id)},
            )

    await repo.record_source_success(source_name)
    await logger.success(
        "scrape", "search_complete",
        metadata={
            "source": source_name,
            "region": region,
            "found": len(jobs),
            "new": new_count,
            "filtered_out": len(jobs) - len(filtered),
        },
    )

    return {"status": "ok", "found": len(jobs), "new": new_count}