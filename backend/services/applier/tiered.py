"""
TieredApplier — orchestrates the three auto-apply tiers:

    Tier 1: deterministic ATS adapters (Greenhouse, Lever, Ashby, Workday)
    Tier 2: generic LLM-assisted form filling with a confidence threshold
    Tier 3: fail honestly -> the apply worker marks the job
            failed_needs_manual and the email worker still follows up

Dry-run mode: fills everything but never clicks submit (config key 'apply'
-> {"dry_run": true}).
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from backend.config import settings
from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult, launch_browser,
)
from backend.services.applier.detector import ats_detector
from backend.services.applier.greenhouse import GreenhouseAdapter
from backend.services.applier.lever import LeverAdapter
from backend.services.applier.ashby import AshbyAdapter
from backend.services.applier.workday import WorkdayAdapter
from backend.services.applier.generic import GenericApplier

logger = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class TieredApplier:
    """Applies to a job through the tiered strategy."""

    def __init__(
        self,
        config_service=None,
        repo=None,
        event_logger=None,
        resume_path: Optional[str] = None,
        screenshot_dir: Optional[str] = None,
        headless: bool = True,
    ):
        from backend.services.config_service import ConfigService

        self.config_service = config_service or ConfigService()
        self.repo = repo
        self.logger = event_logger
        self.resume_path = resume_path or settings.resume_path
        self.screenshot_dir = screenshot_dir or settings.screenshot_dir
        self.headless = headless

        self.tier1: Dict[str, ApplierAdapter] = {}
        for adapter in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter(), WorkdayAdapter()):
            self.tier1[adapter.platform] = adapter

    # ------------------------------------------------------------------

    async def apply(self, job, application) -> ApplyResult:
        """Run the full tiered apply flow for one job."""
        ctx = await self._build_context(job, application)

        platform = ats_detector.detect(job.url)
        adapter = self.tier1.get(platform)

        if self.logger is not None:
            await self.logger.started(
                "apply", "browser_session",
                application_id=str(application.id),
                target_url=job.url,
                metadata={"platform": platform or "generic", "dry_run": ctx.dry_run},
            )

        p, browser = await launch_browser(headless=self.headless)
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            await page.goto(job.url, timeout=60000, wait_until="domcontentloaded")

            result = None
            if adapter is not None:
                result = await adapter.fill_and_submit(page, job, ctx)
                if not result.success and not result.needs_input:
                    # Tier 1 failed structurally -> try Tier 2 on the same page
                    if self.logger is not None:
                        await self.logger.failed(
                            "apply", "tier1_failed",
                            application_id=str(application.id),
                            target_url=job.url,
                            error_text=result.error or "tier 1 failure",
                            metadata={"platform": platform},
                        )
                    generic = GenericApplier()
                    result = await generic.fill_and_submit(page, job, ctx)
            else:
                generic = GenericApplier()
                result = await generic.fill_and_submit(page, job, ctx)

            return result
        finally:
            await browser.close()
            await p.stop()

    async def _build_context(self, job, application) -> ApplyContext:
        """Assemble profile + answers + settings into an ApplyContext."""
        profile_cfg = await self.config_service.get_profile()
        profile = {
            "name": profile_cfg.name,
            "first_name": (profile_cfg.name.split(" ", 1) + [""])[0],
            "last_name": (profile_cfg.name.split(" ", 1) + ["", ""])[1],
            "email": profile_cfg.email,
            "university": profile_cfg.university,
            "major": profile_cfg.major,
            "skills": ", ".join(profile_cfg.skills),
            "portfolio_url": profile_cfg.portfolio_url,
            "phone": "",
        }

        answers: Dict[str, str] = {}
        if self.repo is not None:
            try:
                for a in await self.repo.get_all_answers():
                    answers[a.question_text] = a.answer_text
            except Exception as e:
                logger.warning(f"Could not load profile answers: {e}")

        apply_cfg = await self.config_service.get_apply_config()
        dry_run = bool(apply_cfg.get("dry_run", True))

        resume_path = None
        if self.resume_path:
            p = Path(self.resume_path)
            if p.exists():
                resume_path = str(p)

        prefilled = dict(application.filled_fields or {}) if hasattr(application, "filled_fields") else {}

        return ApplyContext(
            profile=profile,
            answers=answers,
            prefilled=prefilled,
            resume_path=resume_path,
            dry_run=dry_run,
            screenshot_dir=self.screenshot_dir,
            logger=self.logger,
            repo=self.repo,
            application_id=str(application.id),
            job=job,
        )