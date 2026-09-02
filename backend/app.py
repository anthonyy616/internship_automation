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

    yield

    # Shutdown
    print("[*] Shutting down...")
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
async def start_bot(request: dict):
    """Start the autonomous bot. (Placeholder — wired in Phase 3)"""
    # TODO: Wire to arq task queue in Phase 3
    return {"success": False, "error": "Not yet implemented — Phase 3"}


@app.post("/api/stop")
async def stop_bot(request: Optional[dict] = None):
    """Stop the bot. (Placeholder — wired in Phase 3)"""
    # TODO: Wire to arq task queue in Phase 3
    return {"success": False, "error": "Not yet implemented — Phase 3"}


@app.get("/api/status")
async def get_status():
    """Get current bot status."""
    return {
        "status": "idle",
        "session_id": None,
        "jobs_found": 0,
        "applications_sent": 0,
        "emails_sent": 0,
    }


# ==================== DATA ENDPOINTS ====================

@app.get("/api/stats")
async def get_stats():
    """Get overall statistics from the database."""
    try:
        repo = get_repo()
        job_stats = await repo.get_job_counts()

        # Count applications and emails
        async with repo.pool.acquire() as conn:
            app_count = await conn.fetchval("SELECT COUNT(*) FROM applications")
            email_count = await conn.fetchval("SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL")

        return {
            "total_jobs": job_stats["total_jobs"],
            "total_applications": app_count or 0,
            "total_emails": email_count or 0,
            "jobs_by_region": job_stats["jobs_by_region"],
            "jobs_by_status": job_stats["jobs_by_status"],
            "session_active": False,  # TODO: track via arq
            "current_status": "idle",
        }
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
    """Answer a pending confirmation."""
    try:
        body = await request.json()
        answer = body.get("answer", "")
        repo = get_repo()
        success = await repo.answer_confirmation(confirmation_id, answer)
        if success:
            return {"success": True}
        return JSONResponse({"error": "Confirmation not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ==================== ADMIN PANEL ====================

@app.get("/admin")
async def admin_dashboard(request: Request):
    """Admin panel dashboard. (Full implementation in Phase 6)"""
    if templates:
        return templates.TemplateResponse("dashboard.html", {"request": request})
    return JSONResponse({
        "message": "Admin panel — Phase 6 implementation pending",
        "endpoints": {
            "dashboard": "/admin",
            "profile": "/admin/profile",
            "sources": "/admin/sources",
            "email": "/admin/email",
            "applications": "/admin/applications",
            "review": "/admin/review",
            "events": "/admin/events",
        },
    })


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

        # Send initial stats
        try:
            repo = get_repo()
            job_stats = await repo.get_job_counts()
            async with repo.pool.acquire() as conn:
                app_count = await conn.fetchval("SELECT COUNT(*) FROM applications")
                email_count = await conn.fetchval("SELECT COUNT(*) FROM emails WHERE sent_at IS NOT NULL")
            await websocket.send_json({
                "type": "stats",
                "data": {
                    "total_jobs": job_stats["total_jobs"],
                    "total_applications": app_count or 0,
                    "total_emails": email_count or 0,
                },
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
