"""
Hacker News "Who's Hiring" Adapter.

Uses the Algolia HN search API to find internship-relevant posts.
Docs: https://hn.algolia.com/api
"""

import re
import httpx
from typing import List

from backend.services.sources.base import SourceAdapter, JobListing


class HackerNewsAdapter(SourceAdapter):
    """Hacker News Who's Hiring — tech job posts."""

    API_URL = "https://hn.algolia.com/api/v1/search_by_date"

    @property
    def name(self) -> str:
        return "hackernews"

    @property
    def source_type(self) -> str:
        return "api"

    @property
    def base_url(self) -> str:
        return "https://news.ycombinator.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs = []

        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in keywords[:2]:  # limit keywords
                try:
                    response = await client.get(
                        self.API_URL,
                        params={
                            "query": f"{keyword} intern",
                            "tags": "job",
                            "hitsPerPage": 20,
                        },
                        headers={"User-Agent": "InternshipBot/2.0"},
                    )

                    if response.status_code != 200:
                        continue

                    data = response.json()

                    for hit in data.get("hits", []):
                        title_text = hit.get("title") or hit.get("story_title") or ""
                        if not title_text:
                            continue

                        title_lower = title_text.lower()

                        # Must be relevant
                        is_relevant = any(
                            kw.lower() in title_lower
                            for kw in keywords + ["intern", "internship"]
                        )
                        if not is_relevant:
                            continue

                        # Exclude senior roles
                        if any(neg in title_lower for neg in ["senior", "lead", "manager", "director"]):
                            continue

                        # Parse company from HN format: "Company | Title"
                        parts = title_text.split("|")
                        company = parts[0].strip() if len(parts) > 1 else "Unknown"
                        title = parts[1].strip() if len(parts) > 1 else title_text

                        # URL: prefer the job URL, fall back to HN item
                        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

                        jobs.append(JobListing(
                            source=self.name,
                            external_id=hit.get("objectID", ""),
                            title=title[:100],
                            company=company[:100],
                            url=url,
                            description="",
                            region=region,
                            tags=["hackernews", "who-is-hiring"],
                        ))

                except Exception:
                    continue

        return jobs
