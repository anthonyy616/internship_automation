"""
Remotive.io API Adapter.

Clean, public API for remote tech jobs.
Docs: https://remotive.com/api-documentation
"""

import httpx
from typing import List

from backend.services.sources.base import SourceAdapter, JobListing


class RemotiveAdapter(SourceAdapter):
    """Remotive.io — global remote tech jobs."""

    API_URL = "https://remotive.com/api/remote-jobs"

    @property
    def name(self) -> str:
        return "remotive"

    @property
    def source_type(self) -> str:
        return "api"

    @property
    def base_url(self) -> str:
        return "https://remotive.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs = []

        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in keywords[:3]:  # limit to 3 keywords per source
                try:
                    response = await client.get(
                        self.API_URL,
                        params={"search": keyword, "limit": 20},
                        headers={"User-Agent": "InternshipBot/2.0"},
                    )

                    if response.status_code != 200:
                        continue

                    data = response.json()

                    for job in data.get("jobs", []):
                        title = job.get("title", "")
                        company = job.get("company_name", "")
                        url = job.get("url", "")

                        if not (title and company and url):
                            continue

                        # Filter: only intern/junior/entry-level
                        title_lower = title.lower()
                        if any(neg in title_lower for neg in ["senior", "lead", "manager", "director", "principal", "staff"]):
                            continue

                        # Extract tags
                        tags = job.get("tags", [])

                        jobs.append(JobListing(
                            source=self.name,
                            external_id=str(job.get("id", "")),
                            title=title,
                            company=company,
                            url=url,
                            description=(job.get("description") or "")[:500],
                            region=region,
                            location=job.get("candidate_required_location", "Remote"),
                            tags=tags,
                        ))

                except Exception as e:
                    continue  # skip failed keywords silently

        return jobs
