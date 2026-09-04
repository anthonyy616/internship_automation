"""
Event Logger — structured, replayable action logging.

Writes every meaningful action to the agent_events table and delivers it
to the operator in real time:

    - console  : colored line on stdout (visible in the server terminal
                 and in the worker terminal — this is the "what is the
                 bot doing right now" output)
    - dashboard: if THIS process has WebSocket clients (the API server)
                 the event is broadcast straight to them; otherwise (the
                 arq worker, which has no clients) it is published to a
                 Redis channel that the server's EventBridge subscribes
                 to and forwards to every connected browser.

Delivery is best-effort in every direction: a logging failure never
breaks the scrape/apply pipeline.

    Usage:
        logger = EventLogger(repo, ws_manager)

        # Simple log
        await logger.log("scrape", "found_jobs", "success", metadata={"count": 12})

        # Timing context
        async with logger.timed("apply", "submit_form", application_id="xxx") as timer:
            result = await do_application()
            if not result.success:
                timer.fail("Form submission rejected")
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from backend.database import Repository
from backend.websocket_manager import ConnectionManager

# Redis channel the worker publishes agent events to; the API server's
# EventBridge subscribes to it and forwards events to browser clients.
AGENT_EVENT_CHANNEL = "agent:events"

# ANSI colors for console output
_COLORS = {
    "INFO": "\033[94m",
    "SUCCESS": "\033[92m",
    "WARNING": "\033[93m",
    "ERROR": "\033[91m",
    "ESCALATED": "\033[95m",
    "STARTED": "\033[96m",
    "DEBUG": "\033[90m",
}
_RESET = "\033[0m"

_publisher = None  # process-wide redis.asyncio client used for pub/sub


def _get_publisher():
    """Lazily create a redis.asyncio client for pub/sub publishing."""
    global _publisher
    if _publisher is None:
        try:
            from redis import asyncio as aioredis
            from backend.config import settings
            _publisher = aioredis.from_url(settings.redis_url)
        except Exception:
            return None
    return _publisher


class EventLogger:
    """
    Unified event logger that writes to DB, console, and WebSocket clients
    (directly when they are in this process, via Redis pub/sub otherwise).
    """

    def __init__(self, repo: Repository, ws_manager: Optional[ConnectionManager] = None):
        self.repo = repo
        self.ws = ws_manager

    # ------------------------------------------------------------------
    # Core log method
    # ------------------------------------------------------------------

    async def log(
        self,
        stage: str,
        action: str,
        status: str,
        application_id: Optional[str] = None,
        target_url: Optional[str] = None,
        screenshot_url: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Log an event to DB and deliver it live (console + dashboard)."""
        metadata = metadata or {}

        # 1. Console — the "terminal in my IDE" view
        self._console(stage, action, status, error_text=error_text, metadata=metadata)

        # 2. Database — the replayable record
        try:
            event = await self.repo.log_event(
                stage=stage,
                action=action,
                status=status,
                application_id=application_id,
                target_url=target_url,
                screenshot_url=screenshot_url,
                duration_ms=duration_ms,
                error_text=error_text,
                metadata=metadata,
            )
        except Exception as e:
            # Logging must never break the pipeline
            print(f"\033[91m[event-logger] DB write failed: {e}\033[0m", file=sys.stderr)
            return None

        if event is None:
            return None

        payload = {
            "type": "agent_event",
            "event": {
                "id": str(event.id),
                "application_id": str(event.application_id) if event.application_id else None,
                "stage": event.stage,
                "action": event.action,
                "status": event.status,
                "target_url": event.target_url,
                "screenshot_url": event.screenshot_url,
                "duration_ms": event.duration_ms,
                "error_text": event.error_text,
                "metadata": event.metadata or {},
                "created_at": event.created_at.isoformat() if event.created_at else None,
            },
        }

        # 3. Dashboard — broadcast locally if we have clients in this
        #    process (API server), else publish for the server's bridge.
        await self._deliver(payload)
        return event

    async def _deliver(self, payload: Dict[str, Any]):
        """Broadcast to local WS clients, or publish to the Redis channel."""
        has_local_clients = self.ws is not None and len(self.ws.active_connections) > 0
        if has_local_clients:
            try:
                await self.ws.broadcast(payload)
                return
            except Exception as e:
                print(f"\033[90m[event-logger] ws broadcast failed: {e}\033[0m", file=sys.stderr)

        # No local clients -> this is the worker (or ws is down).
        # Publish so the server's EventBridge forwards it to browsers.
        publisher = _get_publisher()
        if publisher is None:
            return
        try:
            await publisher.publish(AGENT_EVENT_CHANNEL, json.dumps(payload, default=str))
        except Exception:
            pass  # Redis hiccup — the DB record is still there

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _console(self, stage: str, action: str, status: str,
                 error_text: Optional[str] = None, metadata: Optional[Dict] = None):
        """Compact, colored one-liner for the terminal."""
        color = _COLORS.get(status.upper(), _COLORS.get("INFO"))
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        meta = metadata or {}
        detail = ""
        for key in ("company", "source", "title"):
            if meta.get(key):
                detail += f" {key}={meta[key]}"
                break
        if error_text:
            detail += f" err={error_text[:160]}"
        elif meta.get("found") is not None:
            detail += f" found={meta['found']}"
        elif meta.get("new") is not None:
            detail += f" new={meta['new']}"
        print(f"{color}[{ts}] [{status.upper():<9}] {stage}/{action}{detail}{_RESET}")

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    async def started(self, stage: str, action: str, **kwargs):
        """Log a 'started' event."""
        return await self.log(stage, action, "started", **kwargs)

    async def success(self, stage: str, action: str, **kwargs):
        """Log a 'success' event."""
        return await self.log(stage, action, "success", **kwargs)

    async def failed(self, stage: str, action: str, error_text: str = "", **kwargs):
        """Log a 'failed' event."""
        return await self.log(stage, action, "failed", error_text=error_text, **kwargs)

    async def escalated(self, stage: str, action: str, **kwargs):
        """Log an 'escalated' event."""
        return await self.log(stage, action, "escalated", **kwargs)

    # Console-only helpers (no DB write). Used for operational detail and
    # inside except blocks where a DB failure would mask the real error.
    async def info(self, message: str, *args, **meta):
        if args:
            message = message % args if args else message
        self._console("system", "info", "INFO", metadata=meta or None)
        print(f"  {message}")

    async def warning(self, message: str, *args, **meta):
        if args:
            message = message % args if args else message
        self._console("system", "warning", "WARNING", metadata=meta or None)
        print(f"\033[93m  {message}\033[0m")

    async def error(self, message: str, *args, **meta):
        if args:
            message = message % args if args else message
        self._console("system", "error", "ERROR", metadata=meta or None)
        print(f"\033[91m  {message}\033[0m", file=sys.stderr)

    def timed(self, stage: str, action: str, application_id: Optional[str] = None, **extra):
        """Context manager that auto-logs started/success/failed with timing."""
        return _TimedEvent(self, stage, action, application_id, **extra)


class _TimedEvent:
    """Context manager for timed event logging."""

    def __init__(self, logger: EventLogger, stage: str, action: str,
                 application_id: Optional[str] = None, **extra):
        self.logger = logger
        self.stage = stage
        self.action = action
        self.application_id = application_id
        self.extra = extra
        self.start_time: float = 0
        self._error: Optional[str] = None

    async def __aenter__(self):
        self.start_time = time.monotonic()
        await self.logger.started(self.stage, self.action,
                                  application_id=self.application_id, **self.extra)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.monotonic() - self.start_time) * 1000)

        if exc_type is not None:
            await self.logger.failed(
                self.stage, self.action,
                application_id=self.application_id,
                duration_ms=duration_ms,
                error_text=str(exc_val),
                **self.extra,
            )
        elif self._error:
            await self.logger.failed(
                self.stage, self.action,
                application_id=self.application_id,
                duration_ms=duration_ms,
                error_text=self._error,
                **self.extra,
            )
        else:
            await self.logger.success(
                self.stage, self.action,
                application_id=self.application_id,
                duration_ms=duration_ms,
                **self.extra,
            )

        return False  # Don't suppress exceptions

    def fail(self, error_text: str):
        """Mark this event as failed (checked on exit)."""
        self._error = error_text
