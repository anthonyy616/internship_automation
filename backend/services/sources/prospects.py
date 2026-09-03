"""
Prospects.ac.uk Adapter (UK).

The UK's busiest graduate careers site. Although the public pages are
JS-rendered, the search front-end calls an internal JSON API that works
without authentication:

    GET https://www.prospects.ac.uk/api/jobs?search=<keywords>

Job detail URLs follow the pattern:
    /employer-profiles/{employerSlug}-{employerId}/jobs/{jobSlug}-{jobId}
"""

from typing import List

import httpx

from backend.services.sources.base import SourceAdapter, JobListing

NEGATIVE_TITLE_TOKENS = ["senior", "lead", "manager", "director", "principal", "head of", "vp", "executive"]
POSITIVE_TITLE_TOKENS = ["intern", "trainee", "junior", "entry", "graduate", "student", "apprentice", "placement", "year in industry", "new grad"]


class ProspectsAdapter(SourceAdapter):
    """Prospects.ac.uk — JSON API adapter."""

    API_URL = "https://www.prospects.ac.uk/api/jobs"
    SITE_URL = "https://www.prospects.ac.uk"

    @property
    def name(self) -> str:
        return "prospects"

    @property
    def source_type(self) -> str:
        return "api"

    @property
    def base_url(self) -> str:
        return self.SITE_URL

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs: List[JobListing] = []
        seen_ids = set()

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        ) as client:
            # The API takes a single free-text search term
            for keyword in keywords[:2]:
                try:
                    response = await client.get(self.API_URL, params={"search": keyword})
                    if response.status_code != 200:
                        continue

                    data = response.json()

                    for job in data.get("jobs", []):
                        job_id = job.get("id")
                        if job_id is None or job_id in seen_ids:
                            continue

                        title = (job.get("title") or "").strip()
                        if not title:
                            continue

                        title_lower = title.lower()
                        if any(neg in title_lower for neg in NEGATIVE_TITLE_TOKENS):
                            continue
                        if keywords and not any(kw.lower() in title_lower for kw in keywords):
                            if not any(pos in title_lower for pos in POSITIVE_TITLE_TOKENS):
                                continue

                        employer = job.get("employer") or {}
                        company = (employer.get("name") or "").strip()

                        locations = job.get("location") or []
                        location = locations[0].get("text", "") if locations else ""

                        tags: List[str] = []
                        job_type = (job.get("typeOfJob") or {}).get("text")
                        if job_type:
                            tags.append(job_type)
                        salary = (job.get("salary") or {}).get("text")
                        if salary:
                            tags.append(salary)

                        employer_slug = job.get("employerSlug") or "employer"
                        # The search API usually leaves employer.id null; the
                        # employer's public id lives in employerKeyword.tnr
                        employer_keyword = job.get("employerKeyword") or {}
                        employer_id = employer.get("id") or employer_keyword.get("tnr") or job_id
                        job_slug = job.get("jobSlug") or str(job_id)
                        url = (
                            f"{self.SITE_URL}/employer-profiles/{employer_slug}-{employer_id}"
                            f"/jobs/{job_slug}-{job_id}"
                        )

                        seen_ids.add(job_id)
                        jobs.append(JobListing(
                            source=self.name,
                            external_id=str(job_id),
                            title=title,
                            company=company,
                            url=url,
                            description="",
                            region=region,
                            location=location,
                            tags=tags,
                        ))

                except Exception:
                    continue

        return jobs

    async def health_check(self) -> bool:
        """Verify the search API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.API_URL, params={"search": "intern"})
                return response.status_code == 200 and "jobs" in response.text
        except Exception:
            return False