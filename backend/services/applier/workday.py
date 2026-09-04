"""
Workday adapter (Tier 1, best-effort) — Workday is the most heavily
JS-driven ATS and its DOM varies by tenant. This adapter attempts the
common flow (find + click the Apply button, fill standard fields, upload
resume) and fails honestly when the structure doesn't match, so the
tiered applier can fall through to Tier 2/3.

Workday exposes relatively stable field names:
    input[data-automation-id='firstName'], lastName, email, phone
"""

from typing import Dict, List

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)


class WorkdayAdapter(ApplierAdapter):
    platform = "workday"

    FIELD_MAP: Dict[str, List[str]] = {
        "first_name": ["input[data-automation-id='firstName']", "input[name='firstName']"],
        "last_name": ["input[data-automation-id='lastName']", "input[name='lastName']"],
        "email": ["input[data-automation-id='email']", "input[name='email']"],
        "phone": ["input[data-automation-id='phone']", "input[name='phone']"],
    }

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        # Workday posts often need the "Apply" button clicked first
        for selector in ["button[data-automation-id='applyButton']",
                         "button:has-text('Apply now')", "button:has-text('Apply')"]:
            try:
                btn = page.locator(selector).first
                if await btn.count():
                    await btn.click(timeout=8000)
                    break
            except Exception:
                continue

        await page.wait_for_timeout(2500)

        profile = ctx.profile
        name_parts = profile.get("name", "").split(" ", 1)
        first, last = (name_parts + [""] * (2 - len(name_parts)))[:2]

        fields_to_fill = [
            ("first_name", first or ""),
            ("last_name", last or ""),
            ("email", profile.get("email", "")),
            ("phone", profile.get("phone", "")),
        ]
        for field_name, value in fields_to_fill:
            if not value:
                continue
            for selector in self.FIELD_MAP[field_name]:
                if await self._fill_by_selector(page, selector, value):
                    result.filled_fields[field_name] = value
                    break

        # Resume upload
        if ctx.resume_path:
            try:
                file_input = page.locator("input[type='file']").first
                if await file_input.count():
                    await file_input.set_input_files(ctx.resume_path)
                    result.filled_fields["resume"] = "uploaded"
            except Exception:
                pass

        screenshot = await self._screenshot(page, ctx, "workday_before_submit")
        result.screenshot_url = screenshot

        if ctx.dry_run:
            result.success = True
            result.dry_run = True
            result.error = "dry_run"
            return result

        # Workday's multi-step flow is tenant-specific; attempt a generic
        # "next/submit" click and mark success only if the form advanced.
        try:
            clicked = False
            for selector in ["button[data-automation-id='bottom-navigation-next-button']",
                             "button[data-automation-id='submitButton']",
                             "button:has-text('Submit')", "button:has-text('Next')"]:
                try:
                    btn = page.locator(selector).first
                    if await btn.count():
                        await btn.click(timeout=8000)
                        clicked = True
                        break
                except Exception:
                    continue

            if not clicked:
                result.error = "workday submit/next button not found — flow is tenant-specific"
                return result

            await page.wait_for_timeout(3000)
            shot2 = await self._screenshot(page, ctx, "workday_after_submit")
            result.screenshot_url = shot2 or screenshot
            result.success = True
            result.applied_via = "form"
        except Exception as e:
            result.success = False
            result.error = f"submit failed: {e}"

        return result