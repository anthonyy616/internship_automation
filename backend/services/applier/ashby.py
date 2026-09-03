"""
Ashby adapter (Tier 1) — deterministic selectors for the Ashby ATS.

Ashby career pages open an application modal. Common stable attributes:
    button[data-qa='apply-button']          (opens the modal)
    input[name='name'], input[name='email'],
    input[name='phone'], input[name='linkedinUrl'],
    input[name='portfolioUrl'], input[type='file'] (resume),
    textarea[name='notes']
"""

from typing import Dict, List

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)
from backend.services.applier.question_classifier import question_classifier


class AshbyAdapter(ApplierAdapter):
    platform = "ashby"

    FIELD_MAP: Dict[str, List[str]] = {
        "name": ["input[name='name']"],
        "email": ["input[name='email']"],
        "phone": ["input[name='phone']"],
        "linkedin": ["input[name='linkedinUrl']"],
        "website": ["input[name='portfolioUrl']"],
    }

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        # Open the apply modal
        opened = False
        for selector in ["button[data-qa='apply-button']", "button:has-text('Apply for this job')",
                         "button:has-text('Apply now')", "a:has-text('Apply')"]:
            try:
                btn = page.locator(selector).first
                if await btn.count():
                    await btn.click(timeout=8000)
                    opened = True
                    break
            except Exception:
                continue

        if not opened:
            result.error = "apply button not found"
            return result

        # Wait for the form
        try:
            await page.wait_for_selector("input[name='name'], input[name='email']", timeout=15000)
        except Exception:
            result.error = "apply form not found after opening modal"
            return result

        profile = ctx.profile
        name = profile.get("name", "").strip()
        for profile_key, field_name in [("name", "name"), ("email", "email"), ("phone", "phone"),
                                        ("linkedin", "linkedin"), ("portfolio_url", "website")]:
            value = profile.get(profile_key, "")
            if not value:
                continue
            for selector in self.FIELD_MAP[field_name]:
                if await self._fill_by_selector(page, selector, value):
                    result.filled_fields[field_name] = value
                    break

        # Resume
        if ctx.resume_path:
            try:
                file_input = page.locator("input[type='file']").first
                if await file_input.count():
                    await file_input.set_input_files(ctx.resume_path)
                    result.filled_fields["resume"] = "uploaded"
            except Exception:
                pass

        # Additional questions in the modal
        fields = page.locator(".ashby-form-field, [data-testid*='field'], form .field")
        count = await fields.count()
        needs_input = False
        for i in range(count):
            field = fields.nth(i)
            try:
                if not await field.is_visible():
                    continue
            except Exception:
                continue
            label = ""
            try:
                l = field.locator("label").first
                if await l.count():
                    label = (await l.inner_text()).strip()
            except Exception:
                pass

            input_el = field.locator("input:not([type='file']):not([type='hidden']), select, textarea").first
            try:
                if not await input_el.count():
                    continue
                el_type = (await input_el.evaluate("e => (e.type || e.tagName).toLowerCase()"))
            except Exception:
                continue

            already_filled = False
            try:
                val = await input_el.input_value()
                already_filled = bool(val.strip())
            except Exception:
                pass
            if already_filled:
                continue

            if label:
                answer = ctx.answer_for(label, field_hint=label)
                if answer is None:
                    cat = question_classifier.classify(label, answer_available=False)
                    if cat == "A":
                        needs_input = True
                        await self._escalate(ctx, label, "text")
                    continue
                if await self._fill_by_selector(page, str(input_el), str(answer)):
                    result.filled_fields[label[:40]] = str(answer)

        screenshot = await self._screenshot(page, ctx, "before_submit")
        result.screenshot_url = screenshot

        if needs_input:
            result.success = False
            result.needs_input = True
            result.error = "Category-A question encountered — awaiting user input"
            return result

        if ctx.dry_run:
            result.success = True
            result.error = "dry_run"
            return result

        try:
            submit = page.locator("button[type='submit'], button:has-text('Submit application'), button:has-text('Submit')").first
            await submit.click(timeout=10000)
            await page.wait_for_timeout(3000)
            shot2 = await self._screenshot(page, ctx, "after_submit")
            result.screenshot_url = shot2 or screenshot
            result.success = True
            result.applied_via = "form"
        except Exception as e:
            result.success = False
            result.error = f"submit failed: {e}"

        return result

    async def _escalate(self, ctx: ApplyContext, question: str, field_type: str):
        if ctx.repo is not None and ctx.application_id:
            try:
                conf = await ctx.repo.create_confirmation(
                    application_id=ctx.application_id,
                    question_text=question,
                    field_type=field_type,
                )
                if ctx.logger is not None:
                    await ctx.logger.escalated(
                        "apply", "question_escalated",
                        application_id=ctx.application_id,
                        metadata={"question": question, "confirmation_id": str(conf.id) if conf else None},
                    )
            except Exception:
                pass