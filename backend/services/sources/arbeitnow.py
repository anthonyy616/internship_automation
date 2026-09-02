"""
Arbeitnow.com API Adapter.

Clean API for EU-based tech jobs.
Docs: https://www.arbeitnow.com/api
"""

import httpx
from typing import List

from backend.services.sources.base import SourceAdapter, JobListing


class ArbeitnowAdapter(SourceAdapter):
    """Arbeitnow — EU tech jobs."""

    API_URL = "https://www.arbeitnow.com/api/job-board-api"

    @property
    def name(self) -> str:
        return "arbeitnow"

    @property
    def source_type(self) -> str:
        return "api"

    @property
    def base_url(self) -> str:
        return "https://www.arbeitnow.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs = []

        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.get(
                    self.API_URL,
                    headers={"User-Agent": "InternshipBot/2.0"},
                )

                if response.status_code != 200:
                    return jobs

                data = response.json()

                for job in data.get("data", []):
                    title = job.get("title", "")
                    company = job.get("company_name", "")
                    url = job.get("url", "")

                    if not (title and company and url):
                        continue

                    title_lower = title.lower()

                    # Filter: must be relevant (intern/junior/entry)
                    is_relevant = any(
                        kw.lower() in title_lower
                        for kw in keywords + ["intern", "internship", "junior", "entry"]
                    )

                    # Exclude senior roles
                    is_senior = any(
                        neg in title_lower
                        for neg in ["senior", "lead", "manager", "director", "principal", "staff"]
                    )

                    if is_senior:
                        continue

                    # If keywords provided, require at least one match
                    if keywords and not is_relevant:
                        continue

                    jobs.append(JobListing(
                        source=self.name,
                        external_id=str(job.get("id", "")),
                        title=title,
                        company=company,
                        url=url,
                        description=(job.get("description") or "")[:500],
                        region=region,
                        location=job.get("location", ""),
                        tags=job.get("tags", []),
                    ))

            except Exception:
                pass

        return jobs
