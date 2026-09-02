"""
Event Logger — structured, replayable action logging.

Writes every meaningful action to the agent_events table and broadcasts
it to all connected WebSocket clients in real time.
"""

import time
from typing import Optional, Dict, Any, Callable, Awaitable
from functools import wraps

from backend.database import Repository
from backend.websocket_manager import ConnectionManager


class EventLogger:
    """
    Unified event logger that writes to DB and broadcasts via WebSocket.

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

    def __init__(self, repo: Repository, ws_manager: ConnectionManager):
        self.repo = repo
        self.ws = ws_manager

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
        """Log an event to DB and broadcast to WebSocket clients."""
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

        if event:
            await self.ws.broadcast({
                "type": "agent_event",
                "event": {
                    "id": event.id,
                    "application_id": event.application_id,
                    "stage": event.stage,
                    "action": event.action,
                    "status": event.status,
                    "target_url": event.target_url,
                    "screenshot_url": event.screenshot_url,
                    "duration_ms": event.duration_ms,
                    "error_text": event.error_text,
                    "metadata": event.metadata,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                },
            })

        return event

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
