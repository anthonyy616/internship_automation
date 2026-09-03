"""
Jobberman Nigeria Adapter.

Nigeria's largest job board. No public read API — the Angular front-end
server-renders the job cards into the HTML, so we parse them with
BeautifulSoup.

URL pattern: https://www.jobberman.com/listings/<slug>
Search page:  https://www.jobberman.com/jobs?q=<keyword>
"""

import re
from typing import List

import httpx
from bs4 import BeautifulSoup

from backend.services.sources.base import SourceAdapter, JobListing

# A few title tokens that are obviously not relevant for an intern/junior profile
NEGATIVE_TITLE_TOKENS = ["senior", "lead", "manager", "director", "principal", "head of", "vp", "executive"]
POSITIVE_TITLE_TOKENS = ["intern", "trainee", "junior", "entry", "graduate", "associate", "student", "new grad"]


class JobbermanAdapter(SourceAdapter):
    """Jobberman Nigeria — HTML scrape adapter."""

    LISTINGS_URL = "https://www.jobberman.com/jobs"

    @property
    def name(self) -> str:
        return "jobberman"

    @property
    def source_type(self) -> str:
        return "scrape"

    @property
    def base_url(self) -> str:
        return "https://www.jobberman.com"

    async def _parse_page(self, soup: BeautifulSoup, region: str, keywords: List[str]) -> List[JobListing]:
        """Parse job cards out of a Jobberman listings page."""
        jobs: List[JobListing] = []
        seen_slugs = set()

        for card in soup.select('a[href*="/listings/"]'):
            href = card.get("href", "")
            slug = href.rstrip("/").split("/")[-1]
            if not slug or slug in seen_slugs:
                continue

            title_el = card.select_one('p[class*="text-lg"]')
            title = (title_el.get_text(strip=True) if title_el else card.get_text(strip=True)) or ""

            if not title:
                continue

            # Keyword / seniority filter (adapter-level; JobFilter runs later too)
            title_lower = title.lower()
            if any(neg in title_lower for neg in NEGATIVE_TITLE_TOKENS):
                continue
            if keywords and not any(kw.lower() in title_lower for kw in keywords):
                # Allow generic intern-ish titles even without an exact keyword match
                if not any(pos in title_lower for pos in POSITIVE_TITLE_TOKENS):
                    continue

            # Company (only present on the rich card layout)
            company_el = card.select_one('p[class*="text-blue-700"]')
            company = company_el.get_text(strip=True) if company_el else ""

            # Location / type / salary spans inside the meta row
            location = ""
            tags: List[str] = []
            meta = card.select_one("div[class*='flex-wrap']")
            if meta:
                spans = meta.select("span")
                if spans:
                    location = spans[0].get_text(strip=True)
                for span in spans[1:3]:
                    txt = span.get_text(strip=True)
                    if txt:
                        tags.append(txt)

            # Category row (last gray p inside the card)
            gray_ps = card.select('p[class*="text-gray-500"]')
            if gray_ps:
                category = gray_ps[-1].get_text(strip=True)
                if category:
                    tags.append(category)

            seen_slugs.add(slug)
            jobs.append(JobListing(
                source=self.name,
                external_id=slug,
                title=title,
                company=company,
                url=f"https://www.jobberman.com/listings/{slug}",
                description="",
                region=region,
                location=location,
                tags=tags,
            ))

        return jobs

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs: List[JobListing] = []
        seen_slugs = set()

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"},
        ) as client:
            urls = [self.LISTINGS_URL]
            if keywords:
                # Jobberman's search takes a single `q` param; use the first keyword.
                # The rich card layout (company/location) only appears on the
                # unfiltered page, so fetch both and merge by slug.
                urls.insert(0, f"{self.LISTINGS_URL}?q={keywords[0]}")

            for url in urls:
                try:
                    response = await client.get(url)
                    if response.status_code != 200:
                        continue
                    soup = BeautifulSoup(response.text, "html.parser")
                    page_jobs = await self._parse_page(soup, region, keywords)
                    for job in page_jobs:
                        if job.external_id and job.external_id not in seen_slugs:
                            seen_slugs.add(job.external_id)
                            jobs.append(job)
                except Exception:
                    continue

        return jobs

    async def health_check(self) -> bool:
        """Verify the listings page is reachable and contains job cards."""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.LISTINGS_URL)
                return response.status_code == 200 and "/listings/" in response.text
        except Exception:
            return False