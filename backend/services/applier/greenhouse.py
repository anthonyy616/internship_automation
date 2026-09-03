"""
Greenhouse adapter (Tier 1) — deterministic selectors for the Greenhouse
ATS. Greenhouse covers a large share of tech company career pages and its
apply form has stable element ids.

Form reference (public knowledge of the Greenhouse job application form):
    #first_name, #last_name, #email, #phone
    #resume_upload                (file input)
    #urls__LinkedIn, #urls__Portfolio
    #cover_letter_text / .field textareas
    additional questions live inside #application_form .field
"""

from typing import Dict, List

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)
from backend.services.applier.question_classifier import question_classifier


class GreenhouseAdapter(ApplierAdapter):
    platform = "greenhouse"

    FIELD_MAP: Dict[str, List[str]] = {
        "first_name": ["#first_name", "input[name='first_name']"],
        "last_name": ["#last_name", "input[name='last_name']"],
        "email": ["#email", "input[name='email']"],
        "phone": ["#phone", "input[name='phone']"],
        "linkedin": ["#urls__LinkedIn", "input[name='urls[LinkedIn]']"],
        "website": ["#urls__Portfolio", "input[name='urls[Portfolio]']"],
        "cover_letter": ["#cover_letter_text", "textarea[name='cover_letter']"],
    }

    # Map profile keys -> selectors in FIELD_MAP
    PROFILE_TO_FIELD = {
        "first_name": "first_name",
        "last_name": "last_name",
        "email": "email",
        "phone": "phone",
        "linkedin": "linkedin",
        "portfolio_url": "website",
    }

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        try:
            await page.wait_for_load_state("domcontentloaded")
        except Exception:
            pass

        # Resume upload first (may unlock sections on some forms)
        uploaded = False
        if ctx.resume_path:
            uploaded = await self._upload(page, "#resume_upload", ctx.resume_path)
            if uploaded:
                await self._log_field(ctx, "resume", "uploaded")
                result.filled_fields["resume"] = "uploaded"

        # Standard fields from profile
        for profile_key, field_name in self.PROFILE_TO_FIELD.items():
            value = ctx.profile.get(profile_key, "")
            if not value:
                continue
            for selector in self.FIELD_MAP[field_name]:
                if await self._fill_by_selector(page, selector, value):
                    result.filled_fields[field_name] = value
                    await self._log_field(ctx, field_name, value)
                    break

        # Additional questions
        needs_input = False
        fields = page.locator("#application_form .field, form .field")
        count = await fields.count()

        for i in range(count):
            field = fields.nth(i)
            try:
                visible = await field.is_visible()
            except Exception:
                visible = False
            if not visible:
                continue

            label = ""
            try:
                label_el = field.locator("label").first
                if await label_el.count():
                    label = (await label_el.inner_text()).strip()
            except Exception:
                pass

            input_el = field.locator("input:not([type='hidden']), select, textarea").first
            try:
                if not await input_el.count():
                    continue
                el_type = (await input_el.evaluate("e => (e.type || e.tagName).toLowerCase()")) if await input_el.count() else ""
            except Exception:
                continue

            question_text = label or (await _placeholder_or_name(page, input_el))

            # Skip already-filled / file inputs
            if el_type in ("file", "hidden"):
                continue

            answer = ctx.answer_for(question_text, field_hint=question_text)
            if answer is None:
                # No stored answer — decide: escalate or auto-generate
                cat = question_classifier.classify(question_text, answer_available=False)
                if cat == "A":
                    needs_input = True
                    await self._escalate(ctx, question_text, "text")
                continue

            filled = False
            try:
                filled = await self._fill_by_selector(page, str(input_el), str(answer))
            except Exception:
                filled = False

            if filled:
                result.filled_fields[question_text] = str(answer)
                await self._log_field(ctx, question_text[:40], str(answer))
            else:
                cat = question_classifier.classify(question_text, answer_available=True)
                if cat == "A":
                    needs_input = True
                    await self._escalate(ctx, question_text, "text")

        # Screenshot before submit
        screenshot = await self._screenshot(page, ctx, "before_submit")
        result.screenshot_url = screenshot

        if needs_input:
            # Category-A question hit — pause and let the user answer
            result.success = False
            result.needs_input = True
            result.error = "Category-A question encountered — awaiting user input"
            return result

        if ctx.dry_run:
            result.success = True
            result.applied_via = "form"
            result.error = "dry_run"
            return result

        # Submit
        try:
            submit = page.locator("input[type='submit'], button[type='submit'], button:has-text('Submit Application')").first
            await submit.click(timeout=10000)
            await page.wait_for_timeout(3000)
            screenshot2 = await self._screenshot(page, ctx, "after_submit")
            result.screenshot_url = screenshot2 or screenshot
            result.success = True
            result.applied_via = "form"
        except Exception as e:
            result.success = False
            result.error = f"submit failed: {e}"

        return result

    async def _escalate(self, ctx: ApplyContext, question: str, field_type: str):
        """Create a pending confirmation so the user can answer later."""
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


async def _placeholder_or_name(page, locator) -> str:
    try:
        return await locator.evaluate(
            "e => e.placeholder || e.name || e.id || ''"
        ) or ""
    except Exception:
        return ""