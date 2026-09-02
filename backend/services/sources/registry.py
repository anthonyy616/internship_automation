"""
Source Adapter Registry.

Manages all registered adapters and provides fan-out search across
multiple sources and regions concurrently.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Callable, Awaitable

from backend.services.sources.base import SourceAdapter, JobListing

logger = logging.getLogger(__name__)


class SourceRegistry:
    """
    Registry of all job source adapters.

    Usage:
        registry = SourceRegistry()
        registry.register(RemotiveAdapter())
        registry.register(ArbeitnowAdapter())

        results = await registry.search_all(
            keywords=['Software Engineer Intern'],
            regions=['EU', 'UK'],
            enabled_sources=['remotive', 'arbeitnow'],
        )
    """

    def __init__(self):
        self._adapters: Dict[str, SourceAdapter] = {}
        self._on_search: Optional[Callable] = None  # callback for event logging

    def register(self, adapter: SourceAdapter):
        """Register a source adapter."""
        self._adapters[adapter.name] = adapter
        logger.info(f"Registered source adapter: {adapter.name} ({adapter.source_type})")

    def get(self, name: str) -> Optional[SourceAdapter]:
        """Get an adapter by name."""
        return self._adapters.get(name)

    @property
    def adapter_names(self) -> List[str]:
        """List all registered adapter names."""
        return list(self._adapters.keys())

    def set_search_callback(self, callback: Callable[..., Awaitable]):
        """Set a callback for search events (used by EventLogger)."""
        self._on_search = callback

    async def search_source(
        self,
        adapter: SourceAdapter,
        keywords: List[str],
        region: str,
    ) -> List[JobListing]:
        """Search a single adapter, handling errors gracefully."""
        try:
            jobs = await adapter.search(keywords, region)
            logger.info(f"[{adapter.name}] Found {len(jobs)} jobs in {region}")
            return jobs
        except Exception as e:
            logger.warning(f"[{adapter.name}] Search failed in {region}: {e}")
            return []

    async def search_all(
        self,
        keywords: List[str],
        regions: List[str],
        enabled_sources: Optional[List[str]] = None,
        max_concurrent: int = 5,
    ) -> Dict[str, List[JobListing]]:
        """
        Fan out search to all enabled adapters across all regions concurrently.

        Args:
            keywords: Search terms
            regions: Target regions
            enabled_sources: If provided, only search these sources. If None, search all.
            max_concurrent: Max concurrent searches (rate limiting)

        Returns:
            Dict mapping source_name -> list of JobListing
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        results: Dict[str, List[JobListing]] = {}

        async def _bounded_search(adapter: SourceAdapter, region: str):
            async with semaphore:
                jobs = await self.search_source(adapter, keywords, region)
                return adapter.name, region, jobs

        tasks = []
        for name, adapter in self._adapters.items():
            if enabled_sources and name not in enabled_sources:
                continue
            for region in regions:
                tasks.append(_bounded_search(adapter, region))

        completed = await asyncio.gather(*tasks, return_exceptions=True)

        for result in completed:
            if isinstance(result, Exception):
                logger.warning(f"Search task failed: {result}")
                continue

            source_name, region, jobs = result
            if source_name not in results:
                results[source_name] = []
            results[source_name].extend(jobs)

        # Log summary
        total = sum(len(j) for j in results.values())
        logger.info(f"Search complete: {total} jobs from {len(results)} sources")

        return results

    async def health_check_all(self) -> Dict[str, bool]:
        """Check health of all registered adapters."""
        results = {}
        for name, adapter in self._adapters.items():
            try:
                results[name] = await adapter.health_check()
            except Exception:
                results[name] = False
        return results
