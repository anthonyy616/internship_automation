"""
Milkround Adapter (UK).

UK graduate jobs board (owned by StepStone, shares the totaljobs.com
listing pool). Job cards are rendered client-side, so this adapter drives
a real headless browser with Playwright instead of parsing raw HTML.

Listing URL: https://www.milkround.com/jobs?what=<keyword>
Job links render as https://www.totaljobs.com/job/<slug>/<company>-job<id>

Requires browser binaries:
    playwright install chromium
"""

import re
from typing import List, Optional
from urllib.parse import quote

from backend.services.sources.base import SourceAdapter, JobListing

NEGATIVE_TITLE_TOKENS = ["senior", "lead", "manager", "director", "principal", "head of", "vp", "executive"]
POSITIVE_TITLE_TOKENS = ["intern", "trainee", "junior", "entry", "graduate", "student", "apprentice", "placement", "year in industry", "new grad"]

LOCATION_PATTERN = re.compile(r"([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)*)\s\(([A-Z0-9]{2,4}\d*)\)")
COMPANY_SLUG_PATTERN = re.compile(r"/([a-z0-9-]+)-job(\d+)$")


def _company_from_slug(slug: str) -> str:
    """'s-merrick-ltd' -> 'S Merrick Ltd'."""
    return slug.replace("-", " ").title()


class MilkroundAdapter(SourceAdapter):
    """Milkround UK — Playwright (headless browser) adapter."""

    LISTINGS_URL = "https://www.milkround.com/jobs"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

    @property
    def name(self) -> str:
        return "milkround"

    @property
    def source_type(self) -> str:
        return "scrape"

    @property
    def base_url(self) -> str:
        return "https://www.milkround.com"

    async def search(self, keywords: List[str], region: str) -> List[JobListing]:
        jobs: List[JobListing] = []
        keyword = keywords[0] if keywords else "internship"

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return jobs

        async with async_playwright() as p:
            browser = await self._launch(p)
            try:
                context = await browser.new_context(
                    user_agent=self.USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                    locale="en-GB",
                )
                page = await context.new_page()

                # Optional stealth (best-effort; ignore if unavailable)
                try:
                    from playwright_stealth import stealth_async
                    await stealth_async(page)
                except Exception:
                    pass

                # The internships vertical is the most reliable entry point;
                # fall back to the general search page if it fails.
                encoded = quote(keyword)
                target_url = f"{self.LISTINGS_URL}/internships?what={encoded}"
                loaded = False
                try:
                    await page.goto(target_url, timeout=45000, wait_until="domcontentloaded")
                    loaded = True
                except Exception:
                    pass
                if not loaded:
                    try:
                        await page.goto(f"{self.LISTINGS_URL}?what={encoded}", timeout=45000, wait_until="domcontentloaded")
                        loaded = True
                    except Exception:
                        return jobs

                # Wait for the client-rendered job cards
                try:
                    await page.wait_for_selector('a[href*="/job/"]', timeout=30000)
                except Exception:
                    return jobs

                await page.wait_for_timeout(1500)

                cards = await page.eval_on_selector_all(
                    'a[href*="/job/"]',
                    """els => els.slice(0, 30).map(e => {
                        let c = e;
                        for (let i = 0; i < 4 && c.parentElement; i++) c = c.parentElement;
                        return {
                            href: e.href,
                            title: (e.innerText || '').trim(),
                            card: (c.innerText || '').replace(/\\s+/g, ' ').trim()
                        };
                    })""",
                )

                seen_ids = set()
                for card in cards:
                    href = card.get("href", "")
                    match = COMPANY_SLUG_PATTERN.search(href)
                    if not match:
                        continue

                    company_slug, job_id = match.group(1), match.group(2)
                    if job_id in seen_ids:
                        continue

                    title = card.get("title", "") or ""
                    title_lower = title.lower()
                    if any(neg in title_lower for neg in NEGATIVE_TITLE_TOKENS):
                        continue
                    if keywords and not any(kw.lower() in title_lower for kw in keywords):
                        if not any(pos in title_lower for pos in POSITIVE_TITLE_TOKENS):
                            continue

                    location = ""
                    loc_match = LOCATION_PATTERN.search(card.get("card", ""))
                    if loc_match:
                        location = f"{loc_match.group(1)} ({loc_match.group(2)})"

                    seen_ids.add(job_id)
                    jobs.append(JobListing(
                        source=self.name,
                        external_id=job_id,
                        title=title,
                        company=_company_from_slug(company_slug),
                        url=href,
                        description="",
                        region=region,
                        location=location,
                        tags=["UK"],
                    ))

            finally:
                await browser.close()

        return jobs

    async def _launch(self, playwright):
        """Launch chromium, falling back to an installed Chrome if the
        Playwright browser binaries are missing (common on dev machines)."""
        launch_args = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
        try:
            return await playwright.chromium.launch(**launch_args)
        except Exception:
            try:
                return await playwright.chromium.launch(channel="chrome", **launch_args)
            except Exception:
                raise

    async def health_check(self) -> bool:
        """Verify the listings page is reachable (HTTP-level check)."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(self.LISTINGS_URL)
                return response.status_code == 200
        except Exception:
            return False