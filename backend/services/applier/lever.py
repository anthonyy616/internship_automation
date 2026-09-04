"""
Lever adapter (Tier 1) — deterministic selectors for the Lever ATS.

The Lever application form lives inside an iframe on the job page
(jobs.lever.co). Fields are addressed by name inside the iframe:
    name, email, phone, org, resume (file), links[LinkedIn],
    links[Portfolio], comments, and per-post custom questions.
"""

from typing import Dict, List

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)
from backend.services.applier.question_classifier import question_classifier


class LeverAdapter(ApplierAdapter):
    platform = "lever"

    FIELD_MAP: Dict[str, List[str]] = {
        "name": ["input[name='name']", "input#name"],
        "email": ["input[name='email']", "input#email"],
        "phone": ["input[name='phone']", "input#phone"],
        "linkedin": ["input[name='links[LinkedIn]']", "input[name*='LinkedIn']"],
        "website": ["input[name='links[Portfolio]']", "input[name*='Portfolio']"],
    }

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        # Lever's form is in an iframe; wait for it to load
        frame = None
        for _ in range(10):
            try:
                frame = page.frame_locator("#lever_fulltime_form iframe").first
                if await frame.locator("body").count():
                    break
            except Exception:
                pass
            await page.wait_for_timeout(1000)

        if frame is None:
            # Fallback: any iframe whose URL mentions lever
            for f in page.frames:
                if "lever" in (f.url or "").lower() and f != page.main_frame:
                    frame = f
                    break

        if frame is None:
            result.error = "lever iframe not found"
            return result

        profile = ctx.profile
        name = profile.get("name", "").strip()
        parts = name.split(" ", 1)
        first_name, last_name = (parts + [""] * (2 - len(parts)))[:2]

        # Name field on Lever accepts "First Last"
        for selector in self.FIELD_MAP["name"]:
            if await self._fill_by_selector(frame, selector, f"{first_name} {last_name}".strip()):
                result.filled_fields["name"] = name
                break

        for profile_key, field_name in [("email", "email"), ("phone", "phone"),
                                        ("linkedin", "linkedin"), ("portfolio_url", "website")]:
            value = profile.get(profile_key, "")
            if not value:
                continue
            for selector in self.FIELD_MAP[field_name]:
                if await self._fill_by_selector(frame, selector, value):
                    result.filled_fields[field_name] = value
                    break

        # Resume
        if ctx.resume_path:
            try:
                file_input = frame.locator("input[type='file']").first
                if await file_input.count():
                    await file_input.set_input_files(ctx.resume_path)
                    result.filled_fields["resume"] = "uploaded"
            except Exception:
                pass

        # Custom questions: any remaining textarea/input/select with a label
        fields = frame.locator(".application-field, .field, form > div")
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

            input_el = field.locator("input:not([type='file']):not([type='hidden']):not([type='checkbox']), select, textarea").first
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
                if await self._fill_by_selector(frame, str(input_el), str(answer)):
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
            result.dry_run = True
            result.error = "dry_run"
            return result

        try:
            submit = frame.locator("#submit_app, button[type='submit'], button:has-text('Submit')").first
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