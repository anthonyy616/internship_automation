"""
EventBridge — the pipe between the arq worker and the browser dashboard.

The worker and the API server are separate processes. The worker's
EventLogger has no WebSocket clients, so it publishes each agent event
to a Redis channel instead. This service runs inside the API server,
subscribes to that channel, and forwards every event to all connected
browser clients via the shared ws_manager.

The bridge is resilient: if Redis is briefly unavailable it retries in
a loop and keeps the server alive. Events written to the database while
Redis is down are still visible on the Activity Logs / replay views; only
the live push is delayed.

    Usage (in FastAPI lifespan):
        bridge = EventBridge(ws_manager)
        task = asyncio.create_task(bridge.run())
        ...
        task.cancel()
"""

import asyncio
import json
import sys
from typing import Optional

from backend.websocket_manager import ConnectionManager

AGENT_EVENT_CHANNEL = "agent:events"


class EventBridge:
    """Subscribe to the agent-events Redis channel and broadcast to WS clients."""

    def __init__(self, ws_manager: ConnectionManager):
        self.ws = ws_manager
        self._running = False

    async def run(self):
        """Subscribe and forward forever; reconnect on failure."""
        self._running = True
        from backend.config import settings
        from redis import asyncio as aioredis

        while self._running:
            try:
                redis = aioredis.from_url(settings.redis_url)
                pubsub = redis.pubsub()
                await pubsub.subscribe(AGENT_EVENT_CHANNEL)
                print(f"[+] Event bridge connected — forwarding worker activity to the dashboard.")
                async for raw in pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    data = raw.get("data")
                    if not data:
                        continue
                    try:
                        message = json.loads(data) if isinstance(data, (bytes, str)) else data
                    except (ValueError, TypeError):
                        continue
                    if isinstance(message, dict) and message.get("type") == "agent_event":
                        await self.ws.broadcast(message)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"\033[90m[bridge] disconnected ({e}) — retrying in 5s…\033[0m", file=sys.stderr)
            finally:
                try:
                    closer = getattr(pubsub, "aclose", None) or pubsub.close
                    await closer()
                    rcloser = getattr(redis, "aclose", None) or redis.close
                    await rcloser()
                except Exception:
                    pass
            await asyncio.sleep(5)

    def stop(self):
        self._running = False
