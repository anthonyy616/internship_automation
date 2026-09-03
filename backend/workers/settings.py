"""
arq WorkerSettings — entry point for the task queue worker.

Run the worker:
    arq backend.workers.settings.WorkerSettings
or:
    python -m arq backend.workers.settings.WorkerSettings
"""

import asyncio
import sys

from arq import cron
from arq.connections import RedisSettings

from backend.config import settings
from backend.workers.scrape_worker import scrape_source

# Windows: use the Selector event loop (see backend/app.py for why) so
# arq's redis connections don't hit the Proactor connect/cancel race.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from backend.workers.apply_worker import apply_to_job
from backend.workers.email_worker import send_email
from backend.workers.scheduler import schedule_scraping, process_queue


async def startup(ctx: dict):
    """Build shared services once per worker process and stash them in ctx."""
    from backend.database import init_db
    from backend.services.config_service import ConfigService
    from backend.services.event_logger import EventLogger
    from backend.services.sources.registry import build_default_registry
    from backend.websocket_manager import ws_manager

    repo = await init_db()
    ctx["repo"] = repo
    ctx["config_service"] = ConfigService()
    ctx["registry"] = build_default_registry()
    ctx["event_logger"] = EventLogger(repo, ws_manager)

    # Tiered auto-apply (Phase 4)
    from backend.services.applier.tiered import TieredApplier
    ctx["applier"] = TieredApplier(
        config_service=ctx["config_service"],
        repo=repo,
        event_logger=ctx["event_logger"],
    )

    # Email safety pipeline (Phase 5)
    from backend.services.email.sender import EmailSender
    ctx["email_sender"] = EmailSender(
        config_service=ctx["config_service"],
        repo=repo,
        event_logger=ctx["event_logger"],
    )


async def shutdown(ctx: dict):
    """Close the database pool on worker shutdown."""
    from backend.database import close_db
    await close_db()


class WorkerSettings:
    functions = [
        scrape_source,
        apply_to_job,
        send_email,
    ]
    on_startup = startup
    on_shutdown = shutdown

    # Read from backend.config.settings so .env is loaded (a bare os.getenv
    # here returns None -> arq's default host "localhost", which hangs on
    # Windows ::1 — see backend/config.py).
    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    # Max jobs processed per worker before restarting (safety valve)
    max_jobs = 500
    # Job timeout — long browser sessions may take a while
    job_timeout = 600

    cron_jobs = [
        cron(schedule_scraping, minute={0, 30}, run_at_startup=False),
        cron(process_queue, minute=5, run_at_startup=False),
    ]