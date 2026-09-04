"""
FastAPI application for the Internship Automation Bot v2.
Provides REST API, WebSocket, and admin panel.
"""

import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from backend.database import init_db, close_db, get_repo
from backend.websocket_manager import ws_manager
from backend.services.config_service import ConfigService
from backend.services.event_logger import EventLogger
from backend.services.orchestrator import orchestrator


# Paths
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
ADMIN_DIR = Path(__file__).parent / "admin"
SCREENSHOTS_DIR = BASE_DIR / "data" / "screenshots"

# Config service (initialized after DB)
config_service = ConfigService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — init DB on startup, close on shutdown."""
    # Startup
    print("=" * 60)
    print("INTERNSHIP AUTOMATION BOT v2")
    print("=" * 60)

    try:
        repo = await init_db()
        print(f"[+] Database connected.")
    except Exception as e:
        print(f"[-] Database connection failed: {e}")
        print("[-] Some features will be unavailable.")
        repo = None

    # Telegram escalation bot (long-polling task)
    telegram_task = None
    try:
        if repo is not None:
            from backend.services.telegram.bot import TelegramBot
            bot = TelegramBot(repo=repo, logger_=EventLogger(repo, ws_manager))
            if bot.enabled():
                telegram_task = asyncio.create_task(bot.run())
                print("[+] Telegram bot polling started.")
            else:
                print("[-] Telegram bot disabled (set TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
    except Exception as e:
        print(f"[-] Telegram bot failed to start: {e}")
        telegram_task = None

    # Event bridge: the arq worker publishes events to Redis; this task
    # subscribes and forwards them to every connected browser in real time.
    bridge_task = None
    try:
        from backend.services.event_bridge import EventBridge
        bridge = EventBridge(ws_manager)
        bridge_task = asyncio.create_task(bridge.run())
    except Exception as e:
        print(f"[-] Event bridge failed to start: {e}")
        bridge_task = None

    yield

    # Shutdown
    print("[*] Shutting down...")
    if bridge_task is not None:
        bridge_task.cancel()
        try:
            await bridge_task
        except (asyncio.CancelledError, Exception):
            pass
    if telegram_task is not None:
        telegram_task.cancel()
        try:
            await telegram_task
        except (asyncio.CancelledError, Exception):
            pass
    await orchestrator.close()
    await close_db()


# Create FastAPI app
app = FastAPI(
    title="Internship Automation Bot",
    description="AI-powered autonomous internship application system",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Jinja2 templates (for admin panel)
ADMIN_TEMPLATES_DIR = ADMIN_DIR / "templates"
if ADMIN_TEMPLATES_DIR.exists():
    templates = Jinja2Templates(directory=str(ADMIN_TEMPLATES_DIR))
else:
    templates = None


# ==================== STATIC FILES ====================

# Frontend dashboard
if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

# Screenshots (for replay view)
if SCREENSHOTS_DIR.exists():
    app.mount("/screenshots", StaticFiles(directory=str(SCREENSHOTS_DIR)), name="screenshots")


# ==================== DASHBOARD (Frontend) ====================

@app.get("/")
async def serve_frontend():
    """Serve the main dashboard HTML page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"error": "Frontend not found. Run from project root."},
        status_code=404,
    )


# ==================== HEALTH CHECK ====================

@app.get("/health", response_model=dict)
async def health_check():
    """Health check endpoint."""
    db_connected = False
    try:
        repo = get_repo()
        async with repo.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_connected = True
    except Exception:
        pass

    return {
        "status": "healthy" if db_connected else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "database_connected": db_connected,
        "version": "2.0.0",
    }


# ==================== BOT CONTROL ====================

@app.post("/api/start")
async def start_bot(request: Optional[dict] = None):
    """
    Start the autonomous bot from the dashboard: persist the form settings
    (regions, contact email, daily caps, dry-run toggle), spawn the arq
    worker, kick off an immediate scrape of all enabled sources, and drain
    the apply queue right away so results appear within minutes.

    The dashboard sends the whole form; older no-body callers still work
    and just use whatever is already in the DB config.
    """
    body = request or {}
    try:
        config = ConfigService()

        if body.get("regions"):
            await config.update_regions(list(body["regions"]))
        if body.get("contact_email"):
            await config.update_profile(email=str(body["contact_email"]).strip())
        if body.get("portfolio_url") is not None:
            await config.update_profile(portfolio_url=str(body["portfolio_url"]).strip())

        limits_kwargs = {}
        if body.get("max_applications") is not None:
            limits_kwargs["max_applications_per_day"] = int(body["max_applications"])
        if body.get("max_emails") is not None:
            limits_kwargs["max_emails_per_day"] = int(body["max_emails"])
        if limits_kwargs:
            await config.update_limits(**limits_kwargs)

        if "dry_run" in body:
            # Unchecking the box in the browser turns on real submissions
            await config.update("apply", {"dry_run": bool(body["dry_run"])})
    except Exception as e:
        return {
            "success": False,
            "worker_started": False,
            "warning": f"Could not save dashboard settings: {e}",
        }

    started = orchestrator.start_worker()

    enqueued = 0
    drained = 0
    try:
        config = ConfigService()
        keywords = await config.get_keywords()
        regions = await config.get_regions()
        enqueued = await orchestrator.enqueue_scrape_all(keywords, regions)
        # Drain discovered/filtered/queued jobs into the apply queue now,
        # instead of waiting up to an hour for the cron sweep.
        drained = await orchestrator.enqueue_process_queue()
    except Exception as e:
        return {
            "success": True,
            "worker_started": started,
            "scrape_enqueued": 0,
            "warning": f"Worker started but initial scrape failed: {e}",
        }

    return {
        "success": True,
        "worker_started": started,
        "already_running": not started,
        "scrape_tasks_enqueued": enqueued,
        "apply_tasks_enqueued": drained,
    }


@app.post("/api/stop")
async def stop_bot(request: Optional[dict] = None):
    """Stop the bot: terminate the arq worker subprocess."""
    stopped = orchestrator.stop_worker()
    return {
        "success": True,
        "worker_stopped": stopped,
        "was_running": stopped,
    }


@app.post("/api/jobs/{job_id}/apply")
async def apply_to_job_now(job_id: str):
    """
    Force one job through the apply queue right now (used by the dashboard
    "Apply" button). If a previous attempt failed, it is reset so the job
    can be restarted-and-refilled.
    """
    try:
        repo = get_repo()
        job = await repo.get_job(job_id)
        if job is None:
            return JSONResponse({"error": "Job not found"}, status_code=404)

        # A previous failed attempt would otherwise block a retry
        existing = await repo.get_application_by_job(job_id)
        if existing is not None and existing.status == "failed":
            await repo.update_application_status(str(existing.id), "filling")

        await repo.update_job_status(job_id, "queued")
        try:
            await orchestrator.enqueue_apply(job_id)
            enqueued = True
        except Exception:
            # Redis down — the cron sweep will pick the queued job up later
            enqueued = False

        return {
            "success": True,
            "job_id": job_id,
            "enqueued": enqueued,
            "title": job.title,
            "company": job.company,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _worker_health() -> tuple:
    """
    Check the arq worker heartbeat in Redis.

    Returns (alive: bool, health_text: Optional[str]). The worker re-sets
    this key every 15s with a ~16s TTL (see backend/workers/settings.py),
    so a dead worker is detected within seconds instead of the 1-hour
    default arq health-check interval.
    """
    try:
        pool = await orchestrator._get_pool()
        value = await pool.get("job-agent:worker-health")
        if not value:
            return False, None
        ttl = await pool.ttl("job-agent:worker-health")
        if ttl is not None and ttl <= 0:
            return False, None
        text = value.decode() if isinstance(value, bytes) else str(value)
        return True, text
    except Exception:
        return False, None


async def _status_payload() -> dict:
    """Single source of truth for dashboard numbers.

    'applications_sent' means REAL submissions only (submit was clicked).
    Dry-run fills, failures and paused applications are reported as their
    own numbers so the dashboard can never be mistaken for the truth.
    """
    repo = get_repo()
    job_stats = await repo.get_job_counts()
    apps = await repo.get_application_counts()
    async with repo.pool.acquire() as conn:
        email_count = await conn.fetchval("SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL")

    def c(status: str) -> int:
        return apps.get(status, 0)

    by_status = job_stats["jobs_by_status"]
    awaiting = sum(by_status.get(s, 0) for s in ("discovered", "filtered", "queued"))

    return {
        "jobs_found": job_stats["total_jobs"],
        "awaiting": awaiting,
        "applications_sent": c("applied"),
        "dry_run_completed": c("dry_run"),
        "paused_awaiting_input": c("paused_awaiting_input"),
        "attempts_failed": c("failed"),
        "in_progress": c("filling") + sum(by_status.get(s, 0) for s in ("applying",)),
        "emails_sent": email_count or 0,
    }


@app.get("/api/status")
async def get_status():
    """Get current bot status and honest pipeline numbers."""
    spawned = orchestrator.is_worker_running()
    alive, health_text = await _worker_health()
    worker_running = spawned or alive
    try:
        payload = await _status_payload()
        payload.update(
            {
                "status": "running" if worker_running else "idle",
                "worker_running": worker_running,
                "worker_spawned": spawned,
                "worker_heartbeat": health_text,
            }
        )
        return payload
    except Exception as e:
        return {
            "status": "running" if worker_running else "idle",
            "worker_running": worker_running,
            "worker_spawned": spawned,
            "jobs_found": 0,
            "applications_sent": 0,
            "dry_run_completed": 0,
            "emails_sent": 0,
            "error": str(e),
        }


@app.get("/api/queue")
async def get_queue_status():
    """What the task queue is doing right now (worker, queued, in-flight)."""
    repo = get_repo()
    alive, health_text = await _worker_health()

    queue_depth = 0
    in_progress = 0
    retry_count = 0
    try:
        pool = await orchestrator._get_pool()

        # Queued jobs: the arq queue list(s)
        for key in await pool.keys("arq:queue*"):
            key = key.decode() if isinstance(key, bytes) else key
            if key.endswith(":health-check"):
                continue
            qtype = await pool.type(key)
            if qtype == "list":
                queue_depth += await pool.llen(key)
            elif qtype == "zset":
                retry_count += await pool.zcard(key)

        # In-flight jobs: arq:in-progress* hashes
        for key in await pool.keys("arq:in-progress*"):
            qtype = await pool.type(key)
            if qtype == "hash":
                in_progress += await pool.hlen(key)
    except Exception:
        pass  # Redis down — still report the DB-side picture

    app_counts = await repo.get_application_counts()
    job_stats = await repo.get_job_counts()
    by_status = job_stats["jobs_by_status"]
    last_event = await repo.get_last_event_at()

    try:
        apply_cfg = await ConfigService().get_apply_config()
        dry_run = bool(apply_cfg.get("dry_run", True))
    except Exception:
        dry_run = True

    return {
        "worker_alive": alive,
        "worker_heartbeat": health_text,
        "queued_tasks": queue_depth,
        "in_progress_tasks": in_progress,
        "retry_tasks": retry_count,
        "last_activity": last_event.isoformat() if last_event else None,
        "pending_confirmations": app_counts.get("paused_awaiting_input", 0),
        "jobs_waiting": sum(by_status.get(s, 0) for s in ("discovered", "filtered", "queued")),
        "dry_run_mode": dry_run,
    }


# ==================== DATA ENDPOINTS ====================

@app.get("/api/stats")
async def get_stats():
    """Get overall statistics from the database (honest breakdown)."""
    try:
        repo = get_repo()
        job_stats = await repo.get_job_counts()
        payload = await _status_payload()
        payload.update(
            {
                "total_jobs": payload.pop("jobs_found", 0),
                "applications_submitted": payload["applications_sent"],
                "total_emails": payload["emails_sent"],
                "jobs_by_region": job_stats["jobs_by_region"],
                "jobs_by_status": job_stats["jobs_by_status"],
                "current_status": "running" if await _worker_health() else "idle",
            }
        )
        return payload
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/jobs")
async def get_jobs(
    region: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
):
    """Get jobs with optional filters."""
    try:
        repo = get_repo()
        jobs = await repo.get_jobs(region=region, status=status, limit=limit)
        return {"jobs": [j.model_dump() for j in jobs], "total": len(jobs)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/events")
async def get_events(
    application_id: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 100,
):
    """Get agent events (structured activity log)."""
    try:
        repo = get_repo()
        events = await repo.get_events(
            application_id=application_id, stage=stage, limit=limit
        )
        return {"events": [e.model_dump() for e in events], "total": len(events)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/applications")
async def get_applications(
    status: Optional[str] = None,
    limit: int = 100,
):
    """Get applications with optional status filter."""
    try:
        repo = get_repo()
        apps = await repo.get_applications(status=status, limit=limit)
        return {"applications": [a.model_dump() for a in apps], "total": len(apps)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/applications/{application_id}/timeline")
async def get_application_timeline(application_id: str):
    """Get the full event timeline for a single application (replay view)."""
    try:
        repo = get_repo()
        timeline = await repo.get_application_timeline(application_id)
        return {
            "application_id": application_id,
            "events": [e.model_dump() for e in timeline],
            "total": len(timeline),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/config")
async def get_config():
    """Get current configuration."""
    try:
        profile = await config_service.get_profile()
        keywords = await config_service.get_keywords()
        regions = await config_service.get_regions()
        limits = await config_service.get_limits()
        email_cfg = await config_service.get_email_config()
        blocklist = await config_service.get_blocklist()
        sources = await config_service.get_sources_config()

        return {
            "profile": {
                "name": profile.name,
                "email": profile.email,
                "university": profile.university,
                "major": profile.major,
                "skills": profile.skills,
                "portfolio_url": profile.portfolio_url,
            },
            "keywords": keywords,
            "regions": regions,
            "limits": {
                "max_applications_per_day": limits.max_applications_per_day,
                "max_emails_per_day": limits.max_emails_per_day,
                "min_delay_seconds": limits.min_delay_seconds,
                "max_delay_seconds": limits.max_delay_seconds,
            },
            "email": {
                "daily_cap": email_cfg.daily_cap,
                "per_domain_cap": email_cfg.per_domain_cap,
                "warmup_day": email_cfg.warmup_day,
                "kill_switch_bounce_threshold": email_cfg.kill_switch_bounce_threshold,
            },
            "blocklist": {
                "companies": blocklist.companies,
                "domains": blocklist.domains,
            },
            "sources": sources,
            "apply": {"dry_run": (await config_service.get_apply_config()).get("dry_run", True)},
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.put("/api/config/{key}")
async def update_config(key: str, request: Request):
    """Update a config key."""
    try:
        body = await request.json()
        await config_service.update(key, body)
        return {"success": True, "key": key}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/sources")
async def get_sources():
    """Get all source adapters and their health status."""
    try:
        repo = get_repo()
        sources = await repo.get_all_sources()
        return {"sources": [s.model_dump() for s in sources]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/pending-confirmations")
async def get_pending_confirmations(limit: int = 50):
    """Get pending Telegram confirmations (Category-A questions)."""
    try:
        repo = get_repo()
        confirmations = await repo.get_pending_confirmations(limit=limit)
        return {
            "confirmations": [c.model_dump() for c in confirmations],
            "total": len(confirmations),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/confirmations/{confirmation_id}/answer")
async def answer_confirmation(confirmation_id: str, request: Request):
    """
    Answer a pending confirmation (restart-and-refill): the answer is saved
    to the profile_answers bank and the application is requeued so the
    worker resumes filling from where it paused.
    """
    try:
        body = await request.json()
        answer = body.get("answer", "")
        repo = get_repo()

        confirmation = await repo.get_confirmation(confirmation_id)
        if confirmation is None:
            return JSONResponse({"error": "Confirmation not found"}, status_code=404)

        success = await repo.answer_confirmation(confirmation_id, answer)
        if not success:
            return JSONResponse({"error": "Could not save answer"}, status_code=500)

        # Requeue the paused application (restart-and-refill)
        requeued = False
        if confirmation.application_id:
            application = await repo.get_application(str(confirmation.application_id))
            if application and application.job_id:
                from backend.services.orchestrator import resume_paused_application
                requeued = await resume_paused_application(str(application.job_id))

        return {"success": True, "application_requeued": requeued}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== ADMIN PANEL ====================

# Admin panel (auth + full CRUD — Phase 6)
from backend.admin.routes import router as admin_router
app.include_router(admin_router)


# ==================== WEBSOCKET ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws_manager.connect(websocket)

    try:
        # Send initial connection ack
        await websocket.send_json({
            "type": "status",
            "status": "idle",
            "details": "Connected to server",
        })

        # Send initial stats + the last N agent events (so a freshly
        # opened dashboard immediately shows what the bot has been doing)
        try:
            repo = get_repo()
            payload = await _status_payload()
            spawned = orchestrator.is_worker_running()
            alive, _health = await _worker_health()
            payload["worker_running"] = spawned or alive
            payload["status"] = "running" if (spawned or alive) else "idle"
            await websocket.send_json({"type": "stats", "data": payload})

            events = await repo.get_events(limit=40)
            await websocket.send_json({
                "type": "history",
                "events": [e.model_dump(mode="json") for e in events],
            })
        except Exception:
            pass

        # Keep connection alive
        while True:
            try:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        await ws_manager.disconnect(websocket)


# ==================== RUN SERVER ====================

if __name__ == "__main__":
    import uvicorn

    print("\n" + "=" * 60)
    print("INTERNSHIP AUTOMATION BOT v2")
    print("=" * 60)
    print(f"Server:    http://localhost:8000")
    print(f"API Docs:  http://localhost:8000/docs")
    print(f"Admin:     http://localhost:8000/admin")
    print(f"WebSocket: ws://localhost:8000/ws")
    print("=" * 60 + "\n")

    uvicorn.run(
        "backend.app:app",
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "8000")),
        reload=os.getenv("APP_ENV", "development") == "development",
        reload_dirs=["backend", "frontend"],
    )
