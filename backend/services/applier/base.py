"""
Applier base — shared types and field-filling helpers for the tiered
auto-apply pipeline.
"""

import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import settings


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
    challenge: bool = False            # a CAPTCHA/login wall blocked the submit


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
            await self._human_type(el, str(value))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Humanized input (T0)
    # ------------------------------------------------------------------

    async def _human_type(self, locator, value: str) -> bool:
        """Fill a text control with human-like typing (or instant fill when
        APPLY_HUMANIZED is off). The occasional wrong-key-and-backspace
        keeps behavioural fingerprinting from flagging a perfect typist."""
        if not settings.apply_humanized:
            await locator.fill(str(value))
            return True
        try:
            await locator.click()
            await locator.fill("")
            await locator.type(str(value), delay=random.randint(35, 95))
            if random.random() < 0.04 and len(str(value)) > 6:
                await locator.press("Backspace")
                await locator.type(str(value)[-1], delay=random.randint(35, 80))
            try:
                return await locator.input_value() == str(value)
            except Exception:
                return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Post-submit truth (T1)
    # ------------------------------------------------------------------

    async def detect_submission_state(self, page, before_url: str, timeout_ms: int = 7000) -> str:
        """Classify the page state after clicking submit.

        Returns 'success' | 'validation_error' | 'challenge' | 'unknown'.
        Only 'success' may ever be recorded as an applied application.
        """
        # 1) Captcha frames are an instant giveaway
        try:
            for frame in page.frames:
                src = frame.url or ""
                if any(m in src for m in ("recaptcha", "hcaptcha", "challenges.cloudflare", "turnstile")):
                    return "challenge"
        except Exception:
            pass

        success = False
        error = False
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                url = page.url
                if url != before_url and "error" not in url.lower():
                    success = True
                body = await page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 8000) : ''"
                )
                low = (body or "").lower()
                if any(w in low for w in (
                    "thank you", "application received", "we have received",
                    "submitted successfully", "application complete",
                    "your application has been",
                )):
                    success = True
                if any(w in low for w in (
                    "please fix", "please correct", "please enter", "please select",
                    "invalid", "something went wrong", "unable to submit",
                    "required field", "validation",
                )):
                    error = True
                if success and not error:
                    return "success"
                if error:
                    return "validation_error"
                if await page.locator(
                    "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], "
                    "iframe[src*='challenges.cloudflare'], #turnstile-wrapper, "
                    ".g-recaptcha, .h-captcha, #datadome"
                ).count():
                    return "challenge"
            except Exception:
                pass
            await page.wait_for_timeout(400)
        if success:
            return "success"
        if error:
            return "validation_error"
        return "unknown"

    async def _first_validation_error(self, page) -> str:
        """Grab the first on-page validation error message, if any."""
        try:
            return await page.evaluate("""() => {
                const sels = ['[role="alert"]', '.error', '.validation-error',
                              '.field-error', '.invalid-feedback', 'input:invalid'];
                for (const s of sels) {
                    for (const el of document.querySelectorAll(s)) {
                        const t = (el.innerText || el.value || el.title || '').trim();
                        if (t && t.length > 2) return t.slice(0, 220);
                    }
                }
                return '';
            }""")
        except Exception:
            return ""

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
    """Launch a browser for one apply session.

    When HYPERBROWSER_API_KEY is set, a managed cloud session is created
    via the Hyperbrowser REST API and we connect over CDP (stealth + proxy
    rotation handled server-side). Otherwise local Chromium is launched.

    Returns (playwright, browser). In Hyperbrowser mode the session id is
    attached to the browser as ``_hb_session_id`` so the caller can stop
    the session; on any failure we fall back to local Chromium.
    """
    from playwright.async_api import async_playwright

    if settings.hyperbrowser_api_key:
        from backend.services.applier.stealth import (
            create_hyperbrowser_session, stop_hyperbrowser_session,
        )
        session = await create_hyperbrowser_session()
        if session and session.get("ws_endpoint"):
            p = None
            try:
                print(f"[+] Hyperbrowser session {session['id']} — live view: {session.get('live_url')}")
                p = await async_playwright().start()
                browser = await p.chromium.connect_over_cdp(session["ws_endpoint"])
                browser._hb_session_id = session["id"]
                browser._hb_live_url = session.get("live_url")
                return p, browser
            except Exception as e:
                print(f"[-] Hyperbrowser CDP connect failed ({e}) — falling back to local Chromium.")
                if p is not None:
                    try:
                        await p.stop()
                    except Exception:
                        pass
                await stop_hyperbrowser_session(session["id"])

    p = await async_playwright().start()
    launch_args = {"headless": headless, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    try:
        browser = await p.chromium.launch(**launch_args)
    except Exception:
        browser = await p.chromium.launch(channel="chrome", **launch_args)
    return p, browser