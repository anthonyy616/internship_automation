"""
MyJobMag Nigeria Adapter.

One of Nigeria's most popular job boards. Jobs are server-rendered into
the HTML as cards whose links use the pattern /job/<slug>. The link text
itself is "Title at Company", which gives us both fields in one parse.

Search page: https://www.myjobmag.com/jobs
"""

import re
from typing import List

import httpx
from bs4 import BeautifulSoup

from backend.services.sources.base import SourceAdapter, JobListing

NEGATIVE_TITLE_TOKENS = ["senior", "lead", "manager", "director", "principal", "head of", "vp", "executive"]
POSITIVE_TITLE_TOKENS = ["intern", "trainee", "junior", "entry", "graduate", "associate", "student", "new grad", "nysc"]


class MyJobMagAdapter(SourceAdapter):
    """MyJobMag Nigeria — HTML scrape adapter."""

    LISTINGS_URL = "https://www.myjobmag.com/jobs"

    @property
    def name(self) -> str:
        return "myjobmag"

    @property
    def source_type(self) -> str:
        return "scrape"

    @property
    def base_url(self) -> str:
        return "https://www.myjobmag.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs: List[JobListing] = []
        seen_slugs = set()

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        ) as client:
            response = await client.get(self.LISTINGS_URL)
            if response.status_code != 200:
                return jobs

            soup = BeautifulSoup(response.text, "html.parser")

            for a in soup.select('a[href*="/job/"]'):
                href = a.get("href", "")
                slug = href.rstrip("/").split("/")[-1]
                if not slug or slug in seen_slugs:
                    continue

                text = a.get_text(strip=True)
                if not text:
                    continue

                # Link text is "<Title> at <Company>"
                title, company = text, ""
                if " at " in text:
                    title, company = text.rsplit(" at ", 1)
                    title = title.strip()
                    company = company.strip()

                title_lower = title.lower()
                if any(neg in title_lower for neg in NEGATIVE_TITLE_TOKENS):
                    continue
                if keywords and not any(kw.lower() in title_lower for kw in keywords):
                    if not any(pos in title_lower for pos in POSITIVE_TITLE_TOKENS):
                        continue

                seen_slugs.add(slug)
                jobs.append(JobListing(
                    source=self.name,
                    external_id=slug,
                    title=title,
                    company=company,
                    url=f"https://www.myjobmag.com{'' if href.startswith('http') else href}",
                    description="",
                    region=region,
                    location="Nigeria",
                    tags=["Nigeria"],
                ))

        return jobs

    async def health_check(self) -> bool:
        """Verify the listings page is reachable and contains job cards."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.LISTINGS_URL)
                return response.status_code == 200 and "/job/" in response.text
        except Exception:
            return False