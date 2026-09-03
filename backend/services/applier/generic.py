"""
Generic applier (Tier 2) — LLM-assisted filling of unknown/arbitrary forms.

Extracts every visible form control, asks the LLM to map each input to a
profile/answer key, fills what it can, and bails to Tier 3 with a
"low confidence" failure when too many fields remain unfilled.
"""

import asyncio
from typing import Dict, List

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)
from backend.services.applier.question_classifier import question_classifier

MAX_INPUTS_FOR_LLM = 60
MAX_UNFILLED_BEFORE_BAIL = 3

EXTRACT_JS = """() => {
    return Array.from(document.querySelectorAll('input, select, textarea')).map((el, i) => {
        const id = el.id, name = el.name, ph = el.placeholder;
        let label = '';
        if (id) {
            const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
            if (l) label = l.innerText.trim();
        }
        if (!label && el.parentElement) label = el.parentElement.innerText.trim().slice(0, 60);
        const rect = el.getBoundingClientRect();
        const visible = el.offsetParent !== null && rect.width > 0 && rect.height > 0;
        return {
            idx: i, tag: el.tagName, type: el.type || '', name: name || '', id: id || '',
            placeholder: ph || '', label: label, visible: visible,
            required: el.required === true
        };
    });
}"""


class GenericApplier(ApplierAdapter):
    platform = "generic"

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        try:
            inputs = await page.evaluate(EXTRACT_JS)
        except Exception as e:
            result.error = f"failed to extract form: {e}"
            return result

        visible = [i for i in inputs if i.get("visible")]
        if not visible:
            result.error = "no visible form fields found"
            return result

        # Build the simplified form context for the LLM
        simplified = [
            f"Tag: {i['tag']}, Type: {i['type']}, Name: {i['name'][:40]}, Label: {i['label'][:50]}, Required: {i['required']}"
            for i in visible[:MAX_INPUTS_FOR_LLM]
        ]
        form_context = "\n".join(simplified)

        user_keys = list(ctx.profile.keys()) + list(ctx.answers.keys())

        # LLM mapping (sync langchain engine, run off the event loop)
        from backend.services.inference import inference
        try:
            mapping = await asyncio.to_thread(
                inference.map_form_fields, form_context, user_keys
            )
        except Exception as e:
            result.error = f"LLM field mapping failed: {e}"
            return result

        if not mapping:
            result.error = "LLM returned no field mapping — low confidence"
            return result

        # Reverse mapping: profile/answer key -> value
        def value_for_key(key: str) -> str:
            if key in ctx.profile:
                v = ctx.profile[key]
                return str(v) if v else ""
            if key in ctx.answers:
                return str(ctx.answers[key])
            return ""

        unfilled = 0
        needs_input = False
        for field in visible:
            key = mapping.get(field.get("name")) or mapping.get(field.get("id"))
            if not key:
                if field.get("required"):
                    unfilled += 1
                continue

            value = value_for_key(key)
            if not value:
                if field.get("required"):
                    unfilled += 1
                continue

            selector = self._selector_for(field)
            if not selector:
                continue

            if field["type"] == "file":
                if ctx.resume_path:
                    try:
                        await page.locator(selector).first.set_input_files(ctx.resume_path)
                        result.filled_fields[key] = "uploaded"
                    except Exception:
                        pass
                continue

            filled = await self._fill_by_selector(page, selector, value)
            if filled:
                result.filled_fields[key] = value
                await self._log_field(ctx, key[:40], value)
            elif field.get("required"):
                unfilled += 1

        screenshot = await self._screenshot(page, ctx, "generic_before_submit")
        result.screenshot_url = screenshot

        # Confidence gate: too many required fields unfilled -> Tier 3
        if unfilled > MAX_UNFILLED_BEFORE_BAIL:
            result.success = False
            result.error = f"low confidence — {unfilled} required fields unfilled"
            return result

        if ctx.dry_run:
            result.success = True
            result.error = "dry_run"
            return result

        # Submit (best effort)
        try:
            submit = page.locator(
                "input[type='submit'], button[type='submit'], button:has-text('Submit'), button:has-text('Apply')"
            ).first
            await submit.click(timeout=10000)
            await page.wait_for_timeout(3000)
            shot2 = await self._screenshot(page, ctx, "generic_after_submit")
            result.screenshot_url = shot2 or screenshot
            result.success = True
            result.applied_via = "form"
        except Exception as e:
            result.success = False
            result.error = f"submit failed: {e}"

        return result

    @staticmethod
    def _selector_for(field: Dict) -> str:
        if field.get("name"):
            return f"{field['tag'].lower()}[name='{field['name']}']"
        if field.get("id"):
            return f"#{field['id']}"
        return ""