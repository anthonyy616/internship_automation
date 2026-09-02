"""
Jobicy API Adapter.

Remote job listings API.
Docs: https://jobicy.com/api
"""

import httpx
from typing import List

from backend.services.sources.base import SourceAdapter, JobListing


class JobicyAdapter(SourceAdapter):
    """Jobicy — remote job listings."""

    API_URL = "https://jobicy.com/api/v2/remote-jobs"

    @property
    def name(self) -> str:
        return "jobicy"

    @property
    def source_type(self) -> str:
        return "api"

    @property
    def base_url(self) -> str:
        return "https://jobicy.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs = []

        async with httpx.AsyncClient(timeout=30) as client:
            for keyword in keywords[:2]:
                try:
                    response = await client.get(
                        self.API_URL,
                        params={"count": 20, "tag": keyword},
                        headers={"User-Agent": "InternshipBot/2.0"},
                    )

                    if response.status_code != 200:
                        continue

                    data = response.json()

                    for job in data.get("jobs", []):
                        title = job.get("jobTitle", "")
                        company = job.get("companyName", "")
                        url = job.get("url", "")

                        if not (title and company and url):
                            continue

                        title_lower = title.lower()

                        # Filter: relevant keywords
                        is_relevant = any(
                            kw.lower() in title_lower
                            for kw in keywords + ["intern", "internship", "junior", "entry"]
                        )

                        # Exclude senior
                        if any(neg in title_lower for neg in ["senior", "lead", "manager", "director"]):
                            continue

                        if keywords and not is_relevant:
                            continue

                        jobs.append(JobListing(
                            source=self.name,
                            external_id=str(job.get("jobId", "")),
                            title=title,
                            company=company,
                            url=url,
                            description="",
                            region=region,
                            location=job.get("jobGeo", "Remote"),
                            tags=[job.get("jobType", ""), job.get("jobIndustry", "")],
                        ))

                except Exception:
                    continue

        return jobs
