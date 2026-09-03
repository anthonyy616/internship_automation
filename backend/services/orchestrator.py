"""
Orchestrator — thin wrapper over the arq task queue.

No pipeline logic lives here anymore (that moved into the arq workers in
backend/workers/). This service is responsible for:

    - enqueuing scrape / apply / email tasks
    - spawning and stopping the arq worker process (for the API
      /api/start and /api/stop endpoints)

Run the worker standalone with:
    python -m arq backend.workers.settings.WorkerSettings
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import List, Optional

from arq.connections import RedisSettings, create_pool

PROJECT_ROOT = Path(__file__).parent.parent.parent


class Orchestrator:
    """Enqueue tasks and manage the arq worker subprocess."""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self._pool = None
        self._worker_proc: Optional[subprocess.Popen] = None

    # ------------------------------------------------------------------
    # Connection pool
    # ------------------------------------------------------------------

    async def _get_pool(self):
        """Lazily create the arq Redis pool."""
        if self._pool is None:
            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def close(self):
        """Close the Redis pool (call on app shutdown)."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # ------------------------------------------------------------------
    # Enqueue helpers
    # ------------------------------------------------------------------

    async def enqueue_scrape(self, source_name: str, keywords: List[str], regions: List[str]) -> int:
        """Enqueue one scrape_source task per region. Returns count."""
        pool = await self._get_pool()
        count = 0
        for region in regions:
            await pool.enqueue_job("scrape_source", source_name, keywords, region)
            count += 1
        return count

    async def enqueue_scrape_all(self, keywords: List[str], regions: List[str],
                                 enabled_sources: Optional[List[str]] = None) -> int:
        """Enqueue scraping for every registered source. Returns count."""
        from backend.services.sources.registry import build_default_registry

        registry = build_default_registry()
        names = enabled_sources or registry.adapter_names
        count = 0
        for name in names:
            if registry.get(name) is None:
                continue
            count += await self.enqueue_scrape(name, keywords, regions)
        return count

    async def enqueue_apply(self, job_id: str):
        pool = await self._get_pool()
        await pool.enqueue_job("apply_to_job", job_id)

    async def enqueue_email(self, application_id: str):
        pool = await self._get_pool()
        await pool.enqueue_job("send_email", application_id)

    # ------------------------------------------------------------------
    # Worker subprocess management
    # ------------------------------------------------------------------

    def start_worker(self) -> bool:
        """
        Spawn the arq worker as a subprocess.

        Returns True if a worker was started, False if one is already
        running (or the process could not be started).
        """
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            return False

        cmd = [sys.executable, "-m", "arq", "backend.workers.settings.WorkerSettings"]
        try:
            self._worker_proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            print(f"[-] Failed to start worker: {e}")
            self._worker_proc = None
            return False

    def stop_worker(self) -> bool:
        """Stop the arq worker subprocess. Returns True if it was running."""
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            self._worker_proc.terminate()
            return True
        return False

    def is_worker_running(self) -> bool:
        return self._worker_proc is not None and self._worker_proc.poll() is None


# Global instance used by the FastAPI app
orchestrator = Orchestrator()