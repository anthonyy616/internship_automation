"""
FastAPI application for the Internship Automation Bot.
Provides REST API endpoints and WebSocket for real-time updates.
"""

import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from backend.config import settings
from backend.models import (
    StartBotRequest, StopBotRequest,
    JobResponse, StatsResponse, BotStatusResponse, HealthResponse,
    SessionConfig, BotStatus, LogLevel, LogAction
)
from backend.database import db
from backend.websocket_manager import ws_manager
from backend.services.orchestrator import orchestrator


# Paths
BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    print("[*] Starting Internship Automation Bot...")
    print(f"[*] Frontend directory: {FRONTEND_DIR}")
    print(f"[*] Supabase connected: {db.is_connected}")
    yield
    # Shutdown
    print("[*] Shutting down...")
    if orchestrator.is_running:
        await orchestrator.stop("Server shutdown")


# Create FastAPI app
app = FastAPI(
    title="Internship Automation Bot",
    description="AI-powered autonomous internship application system",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== STATIC FILES ====================

# Mount frontend static files
if FRONTEND_DIR.exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")


@app.get("/")
async def serve_frontend():
    """Serve the main HTML page."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {"error": "Frontend not found. Run from project root."},
        status_code=404
    )


# ==================== HEALTH CHECK ====================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        database_connected=db.is_connected
    )


# ==================== BOT CONTROL ====================

@app.post("/api/start")
async def start_bot(request: StartBotRequest):
    """Start the autonomous bot."""
    
    # Build session config
    config = SessionConfig(
        regions=[r.value for r in request.regions],
        contact_email=request.contact_email,
        portfolio_url=request.portfolio_url or settings.user_profile.portfolio_url,
        keywords=request.keywords or settings.search_criteria.keywords,
        max_applications=request.max_applications or 50,
        max_emails=request.max_emails or 50,
        dry_run=request.dry_run,
        user_name=settings.user_profile.name,
        user_major=settings.user_profile.major,
        user_year=settings.user_profile.university_year,
        user_skills=settings.user_profile.skills,
        user_university=settings.user_profile.university
    )
    
    result = await orchestrator.start(config)
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    
    return result


@app.post("/api/stop")
async def stop_bot(request: Optional[StopBotRequest] = None):
    """Stop the bot."""
    reason = request.reason if request else "User requested stop"
    result = await orchestrator.stop(reason)
    
    if not result.get('success'):
        raise HTTPException(status_code=400, detail=result.get('error'))
    
    return result


@app.get("/api/status", response_model=BotStatusResponse)
async def get_status():
    """Get current bot status."""
    status = await orchestrator.get_status()
    return BotStatusResponse(
        status=BotStatus(status['status']),
        session_id=status.get('session_id'),
        jobs_found=status.get('jobs_found', 0),
        applications_sent=status.get('applications_sent', 0),
        emails_sent=status.get('emails_sent', 0)
    )


# ==================== DATA ENDPOINTS ====================

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    """Get overall statistics."""
    stats = await db.get_stats()
    return StatsResponse(
        total_jobs=stats.get('total_jobs', 0),
        total_applications=stats.get('total_applications', 0),
        total_emails=stats.get('total_emails', 0),
        jobs_by_region=stats.get('jobs_by_region', {}),
        jobs_by_status=stats.get('jobs_by_status', {}),
        session_active=orchestrator.is_running,
        current_status=BotStatus(orchestrator.status.value)
    )


@app.get("/api/jobs")
async def get_jobs(
    region: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100
):
    """Get jobs with optional filters."""
    jobs = await db.get_jobs(region=region, status=status, limit=limit)
    return {"jobs": jobs, "total": len(jobs)}


@app.get("/api/logs")
async def get_logs(
    region: Optional[str] = None,
    limit: int = 50
):
    """Get recent activity logs."""
    logs = await db.get_recent_logs(limit=limit, region=region)
    return {"logs": logs, "total": len(logs)}


@app.get("/api/config")
async def get_config():
    """Get current configuration (excluding sensitive data)."""
    return {
        "user_profile": {
            "name": settings.user_profile.name,
            "university_year": settings.user_profile.university_year,
            "major": settings.user_profile.major,
            "skills": settings.user_profile.skills,
            "university": settings.user_profile.university,
            "portfolio_url": settings.user_profile.portfolio_url
        },
        "search_criteria": {
            "keywords": settings.search_criteria.keywords
        },
        "regions": list(settings.regions.keys()),
        "safety": {
            "max_actions_per_day": settings.safety.max_actions_per_day
        }
    }


# ==================== WEBSOCKET ====================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await ws_manager.connect(websocket)
    
    try:
        # Send initial status
        status = await orchestrator.get_status()
        await websocket.send_json({
            "type": "status",
            "status": status['status'],
            "details": "Connected to server"
        })
        
        # Send initial stats
        stats = await db.get_stats()
        await websocket.send_json({
            "type": "stats",
            "data": stats
        })
        
        # Keep connection alive and receive messages
        while True:
            try:
                data = await websocket.receive_text()
                # Handle ping/pong or commands from client
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
    
    print("\n" + "="*60)
    print("INTERNSHIP AUTOMATION BOT")
    print("="*60)
    print(f"Server: http://localhost:8000")
    print(f"API Docs: http://localhost:8000/docs")
    print(f"WebSocket: ws://localhost:8000/ws")
    print("="*60 + "\n")
    
    uvicorn.run(
        "backend.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["backend", "frontend"]
    )
