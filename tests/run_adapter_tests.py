"""
Live smoke tests for all job source adapters.

Hits the real endpoints and prints how many jobs each adapter found.
Milkround requires a browser (Playwright chromium, or Chrome via fallback).

Usage:
    python -m tests.run_adapter_tests            # live network tests
    python -m tests.run_adapter_tests --offline  # filter/registry tests only
"""

import asyncio
import sys
from typing import Dict, List

from backend.services.sources.base import JobListing
from backend.services.sources.registry import SourceRegistry, build_default_registry
from backend.services.sources import (
    JobbermanAdapter, MyJobMagAdapter, ElemanAdapter,
    ProspectsAdapter, MilkroundAdapter,
)
from backend.services.filter import job_filter


REGIONAL_TESTS: List[Dict] = [
    {"adapter": JobbermanAdapter, "name": "jobberman", "keywords": ["Software Engineer", "Data Analyst"], "region": "Nigeria"},
    {"adapter": MyJobMagAdapter, "name": "myjobmag", "keywords": ["Software Engineer", "Data Analyst"], "region": "Nigeria"},
    {"adapter": ElemanAdapter, "name": "eleman", "keywords": ["Software Engineer Intern", "Data Analyst"], "region": "Turkiye"},
    {"adapter": ProspectsAdapter, "name": "prospects", "keywords": ["Software Engineer", "Data Analyst"], "region": "UK"},
    {"adapter": MilkroundAdapter, "name": "milkround", "keywords": ["Software Engineer", "Data Analyst"], "region": "UK"},
]


async def test_adapter(adapter, keywords: List[str], region: str) -> List[JobListing]:
    jobs = await adapter.search(keywords, region)
    return jobs


async def run_live_tests():
    print("=" * 70)
    print("LIVE ADAPTER TESTS")
    print("=" * 70)

    results = {}
    for t in REGIONAL_TESTS:
        adapter = t["adapter"]()
        try:
            jobs = await test_adapter(adapter, t["keywords"], t["region"])
            results[t["name"]] = jobs
            print(f"\n[{t['name']}] found {len(jobs)} jobs (region={t['region']})")
            for j in jobs[:3]:
                print(f"   - {j.title[:60]} | {j.company[:30]} | {j.location[:25]} | {j.url[:70]}")
            if not jobs:
                print("   !!! EMPTY RESULT — adapter returned nothing")
        except Exception as e:
            results[t["name"]] = []
            print(f"\n[{t['name']}] FAILED: {type(e).__name__}: {str(e)[:160]}")

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for name, jobs in results.items():
        print(f"  {name:<12} -> {len(jobs):>3} jobs {'OK' if jobs else 'EMPTY'}")

    ok = all(jobs for jobs in results.values())
    print(f"\n{'ALL REGIONAL ADAPTERS PASSED' if ok else 'SOME ADAPTERS RETURNED EMPTY'}")
    return ok


def run_offline_tests():
    print("=" * 70)
    print("OFFLINE TESTS (filter + registry)")
    print("=" * 70)

    # --- Registry ---
    registry = build_default_registry()
    names = registry.adapter_names
    print(f"registry adapters ({len(names)}): {sorted(names)}")
    assert "jobberman" in names and "eleman" in names and "prospects" in names
    assert len(names) == 9, f"expected 9 adapters, got {len(names)}"

    # --- JobFilter ---
    eligible = JobListing(source="test", title="Software Engineer Intern", company="Acme", url="https://a.com/1", region="UK")
    senior = JobListing(source="test", title="Senior Engineer", company="Acme", url="https://a.com/2", region="UK")
    blocked = JobListing(source="test", title="Junior Dev", company="Evil Corp", url="https://a.com/3", region="UK")

    assert job_filter.is_eligible(eligible) is True, "intern title should be eligible"
    assert job_filter.is_eligible(senior) is False, "senior title should be filtered"
    assert job_filter.check_blocklist(blocked, blocked_companies=["Evil Corp"]) is True

    dupes = [eligible, eligible]
    unique = job_filter.deduplicate(dupes, seen_urls=set())
    assert len(unique) == 1, f"expected 1 unique job, got {len(unique)}"

    print("filter tests passed (eligibility, blocklist, dedup)")

    # --- Search callback + full pipeline ---
    async def fake_cb(**kwargs):
        return None

    async def async_offline():
        registry.set_search_callback(fake_cb)
        # filter_jobs pipeline: senior dropped by eligibility
        passed = await job_filter.filter_jobs([eligible, senior], seen_urls=set())
        assert len(passed) == 1, f"expected 1 job after pipeline, got {len(passed)}"
        print("search callback set + filter pipeline passed")

    asyncio.run(async_offline())
    print("ALL OFFLINE TESTS PASSED")


if __name__ == "__main__":
    if "--offline" in sys.argv:
        run_offline_tests()
    else:
        offline_ok = run_offline_tests()
        live_ok = asyncio.run(run_live_tests())
        sys.exit(0 if (offline_ok and live_ok) else 1)