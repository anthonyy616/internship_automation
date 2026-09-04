"""
Applier base — shared types and field-filling helpers for the tiered
auto-apply pipeline.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class ApplyResult:
    """Outcome of a single application attempt."""
    success: bool
    applied_via: str = "form"
    platform: str = "unknown"
    error: str = ""
    screenshot_url: str = ""
    filled_fields: Dict[str, str] = field(default_factory=dict)
    needs_input: bool = False          # a Category-A question was hit
    pending_confirmation_id: str = ""
    dry_run: bool = False              # form filled + screenshotted but NOT submitted


@dataclass
class ApplyContext:
    """
    Everything a tier adapter needs to fill a form.

    profile:   user profile dict (name, email, university, major, skills, ...)
    answers:   question -> answer bank (from profile_answers + profile fields)
    prefilled: question/field -> value already filled (for restart-and-refill)
    resume_path: absolute path to the resume file, or None
    dry_run:   fill everything but do NOT click submit
    screenshot_dir: where to store screenshots
    logger:    EventLogger (or compatible fake)
    repo:      Repository (for pending confirmations)
    application_id: applications.id
    job:       JobListing
    """
    profile: Dict[str, str]
    answers: Dict[str, str]
    prefilled: Dict[str, str] = field(default_factory=dict)
    resume_path: Optional[str] = None
    dry_run: bool = True
    screenshot_dir: str = "data/screenshots"
    logger: object = None
    repo: object = None
    application_id: str = ""
    job: object = None

    def answer_for(self, question: str, field_hint: str = "") -> Optional[str]:
        """Find an answer for a question using normalized matching."""
        import unicodedata

        import re as _re

        def norm(s: str) -> str:
            s = s.lower().strip()
            s = "".join(ch for ch in unicodedata.normalize("NFD", s) if not unicodedata.combining(ch))
            # strip punctuation and collapse whitespace so label variants match
            s = _re.sub(r"[^a-z0-9 ]", " ", s)
            return _re.sub(r"\s+", " ", s).strip()

        q = norm(question)
        if q in self.prefilled:
            return self.prefilled[q]
        if q in self.answers:
            return self.answers[q]

        hint = norm(field_hint)
        for key, value in self.answers.items():
            k = norm(key)
            if k and (k in q or q in k or (hint and (k in hint or hint in k))):
                return value
        return None


class ApplierAdapter(ABC):
    """One fill-and-submit strategy for a platform or generic form."""

    platform: str = "unknown"

    @abstractmethod
    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    async def _log_field(self, ctx: ApplyContext, field_name: str, value: str):
        if ctx.logger is not None:
            try:
                await ctx.logger.success(
                    "apply", "filled_field",
                    application_id=ctx.application_id,
                    target_url=getattr(ctx.job, "url", None),
                    metadata={"field": field_name, "value": value},
                )
            except Exception:
                pass

    async def _screenshot(self, page, ctx: ApplyContext, name: str) -> str:
        """Save a screenshot and return its URL path (screenshots/...)."""
        try:
            app_dir = Path(ctx.screenshot_dir) / str(ctx.application_id or "unknown")
            app_dir.mkdir(parents=True, exist_ok=True)
            path = app_dir / f"{name}.png"
            await page.screenshot(path=str(path), full_page=True)
            return f"screenshots/{ctx.application_id}/{name}.png"
        except Exception:
            return ""

    async def _fill_by_selector(self, page, selector: str, value: str) -> bool:
        """Fill a text/select/checkbox element by selector, dispatching on type."""
        if not value:
            return False
        try:
            el = page.locator(selector).first
            if not await el.count():
                return False
            tag = (await el.evaluate("e => e.tagName")).lower()
            el_type = (await el.evaluate("e => (e.type || '').toLowerCase()")) if tag == "input" else ""
            if tag == "select":
                try:
                    await el.select_option(label=value)
                    return True
                except Exception:
                    await el.select_option(value=value)
                    return True
            if el_type == "checkbox":
                truthy = str(value).lower() in ("true", "yes", "1", "on", "y")
                if truthy:
                    await el.check()
                else:
                    await el.uncheck()
                return True
            if el_type == "radio":
                try:
                    await el.check()
                    return True
                except Exception:
                    await page.locator(f"{selector}[value='{value}']").first.click()
                    return True
            if el_type == "file":
                return False  # handled by upload helpers
            await el.fill(str(value))
            return True
        except Exception:
            return False

    async def _upload(self, page, selector: str, path: Optional[str]) -> bool:
        if not path:
            return False
        try:
            el = page.locator(selector).first
            if await el.count():
                await el.set_input_files(path)
                return True
        except Exception:
            pass
        return False


async def launch_browser(headless: bool = True):
    """Launch chromium, falling back to installed Chrome when Playwright
    browser binaries are missing."""
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    launch_args = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    try:
        browser = await p.chromium.launch(**launch_args)
    except Exception:
        browser = await p.chromium.launch(channel="chrome", **launch_args)
    return p, browser