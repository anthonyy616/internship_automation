"""
Generic applier (Tier 2) — LLM-assisted filling of unknown/arbitrary forms.

Extracts every visible form control (including controls living inside
iframes and popups, e.g. embedded Ashby/Lever/Typeform apply forms), asks
the LLM to map each input to a profile/answer key, fills what it can, and
escalates what it can't:

    - required text/select questions with no known answer become Telegram
      confirmations (Category-A escalation) so the user can answer once and
      the answer is banked in profile_answers for every future form;
    - when escalation isn't available (no repo/application) or too many
      fields remain unfilled it bails to Tier 3 with a "low confidence"
      failure instead of guessing;
    - if no form is visible at all (many job posts render the application
      behind a button or a late-loading iframe), it waits briefly, then
      clicks through ATS application links (Lever, Greenhouse, Ashby, ...)
      before giving up.
"""

import asyncio
from typing import Dict, List, Optional

from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)

MAX_INPUTS_FOR_LLM = 60
MAX_UNFILLED_BEFORE_BAIL = 3
MAX_ESCALATIONS_PER_FORM = 8

# HTML input types we know how to fill with free text
TEXTUAL_INPUT_TYPES = {"", "text", "email", "tel", "number", "url", "date", "search", "password"}

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
        let options = [];
        if (el.tagName === 'SELECT') {
            options = Array.from(el.options)
                .map(o => (o.text || o.value || '').trim())
                .filter(Boolean);
        }
        return {
            idx: i, tag: el.tagName, type: el.type || '', name: name || '', id: id || '',
            placeholder: ph || '', label: label, visible: visible,
            required: el.required === true
                || el.getAttribute('aria-required') === 'true'
                || (el.closest && !!el.closest('[aria-required="true"]')),
            checked: el.checked === true, options: options
        };
    });
}"""


# ----------------------------------------------------------------------
# Pure decision helpers (unit-testable without a browser)
# ----------------------------------------------------------------------

# Required checkboxes whose label asks for consent/opt-in are safe to tick
# — every applicant must accept them to submit. Real forms word these in a
# dozen ways ("I confirm…", "I have read the data protection information",
# "I acknowledge…"), so the token list is broad. Everything else is never
# auto-checked, and an unresolved *required* checkbox blocks submission.
CONSENT_TOKENS = (
    "agree", "consent", "terms", "privacy", "policy", "accept", "opt-in", "opt in",
    "confirm", "acknowledg", "declar", "understand", "gdpr", "data protection",
    "authoriz", "conditions", "permission",
)


# LLM failure messages we have already surfaced to the operator (per worker
# process), so a dead OpenAI key doesn't spam the event log 40 times.
_LLM_ERRORS_SEEN: set = set()


def is_consent_label(label: str) -> bool:
    """True when a checkbox label asks for consent/opt-in that every
    applicant must give before the form can be submitted."""
    lower = (label or "").lower()
    return any(tok in lower for tok in CONSENT_TOKENS)

FORM_WAIT_ROUNDS = 6      # poll up to ~9s for late-rendering forms/iframes
FORM_WAIT_SECONDS = 1.5

def field_kind(field: Dict) -> str:
    """Classify a control into 'text' | 'select' | 'checkbox' | 'radio' |
    | 'file' | 'button' | 'hidden'."""
    tag = (field.get("tag") or "").lower()
    ftype = (field.get("type") or "").lower()
    if tag == "select":
        return "select"
    if tag == "textarea":
        return "text"
    if ftype == "checkbox":
        return "checkbox"
    if ftype == "radio":
        return "radio"
    if ftype == "file":
        return "file"
    if ftype in ("submit", "button", "reset", "image"):
        return "button"
    if ftype == "hidden":
        return "hidden"
    return "text"


def question_text(field: Dict) -> str:
    """Best human-readable question for a control (label > placeholder > name > id)."""
    for key in ("label", "placeholder", "name", "id"):
        value = (field.get(key) or "").strip()
        if value:
            return value[:120]
    return ""


def decide_field(field: Dict, mapping: Dict, ctx: ApplyContext) -> Dict:
    """
    Decide what to do with one extracted control.

    Returns one of:
        {"action": "fill",    "key": ..., "value": ...}
        {"action": "escalate", "question": ..., "field_type": ..., "options": ...}
        {"action": "skip",    "reason": ...}

    Escalation happens only for *required* free-text/select questions that
    have no stored answer — exactly the facts only the user can supply.
    Optional fields, consent-style checkboxes, radios, and buttons are
    skipped (never guessed, never escalated).
    """
    kind = field_kind(field)
    if kind in ("file", "hidden", "button"):
        return {"action": "skip", "reason": f"non-fillable kind {kind}"}

    required = bool(field.get("required"))
    question = question_text(field)

    # 1) LLM mapping: name/id -> profile/answer key
    key = ""
    if mapping:
        key = (mapping.get(field.get("name")) or mapping.get(field.get("id")) or "").strip()

    # 2) Resolve a value from the profile or the Q&A bank
    value = ""
    if key:
        if key in ctx.profile:
            value = str(ctx.profile.get(key) or "").strip()
        elif key in ctx.answers:
            value = str(ctx.answers.get(key) or "").strip()

    if not value and question:
        found = ctx.answer_for(question, field_hint=question)
        if found is not None:
            value = str(found).strip()

    if value:
        return {
            "action": "fill",
            "key": key or (question or field.get("name") or field.get("id"))[:60],
            "value": value,
        }

    # Consent/opt-in checkboxes are required for every application — tick them
    if kind == "checkbox" and required:
        if is_consent_label(question):
            return {"action": "check", "key": question[:60] or "consent"}
        return {"action": "skip", "reason": "required checkbox without consent wording"}

    # 3) Unknown required text/select question -> escalate to the user
    if kind in ("text", "select"):
        if required and question:
            field_type = "select" if kind == "select" else "text"
            return {
                "action": "escalate",
                "question": question,
                "field_type": field_type,
                "options": (field.get("options") or None) if kind == "select" else None,
            }
        if required:
            # Required but nothing to label it by — count as unfillable
            return {"action": "skip", "reason": "required but unlabelled"}

    return {"action": "skip", "reason": "optional or unresolvable"}


class GenericApplier(ApplierAdapter):
    platform = "generic"

    async def _scan_controls(self, page):
        """Collect visible controls from every page (incl. popups) and frame."""
        pages = [page]
        try:
            pages = list(page.context.pages) or [page]
        except Exception:
            pass
        controls = []
        for p in pages:
            for frame in p.frames:
                try:
                    found = await frame.evaluate(EXTRACT_JS)
                except Exception:
                    continue
                for c in found:
                    c["frame"] = frame
                controls.extend(found)
        return controls

    async def _find_visible_controls(self, page):
        """Poll for a rendered form (late-loading iframes/modals) up to ~9s."""
        for _ in range(FORM_WAIT_ROUNDS):
            controls = await self._scan_controls(page)
            visible = [c for c in controls if c.get("visible")]
            if visible:
                return visible
            await page.wait_for_timeout(int(FORM_WAIT_SECONDS * 1000))
        return []

    async def _click_ats_link(self, page) -> bool:
        """Click an "Apply" link pointing at a known ATS domain, if any."""
        try:
            for p in list(page.context.pages) or [page]:
                for frame in p.frames:
                    try:
                        link = frame.locator(
                            "a[href*='lever.co'], a[href*='greenhouse.io'], "
                            "a[href*='ashbyhq.com'], a[href*='myworkdayjobs.com'], "
                            "a[href*='smartrecruiters.com'], a[href*='workable.com'], "
                            "a[href*='recruitee.com'], a[href*='bamboohr.com']"
                        ).first
                        if await link.count():
                            await link.click(timeout=8000)
                            await page.wait_for_timeout(2500)
                            return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        # Poll for the form: many job pages render the application inside a
        # late-loading iframe or after JS kicks in.
        visible = await self._find_visible_controls(page)
        if not visible:
            # Form may live on the ATS domain behind an "Apply" link
            if await self._click_ats_link(page):
                await page.wait_for_timeout(1500)
                visible = await self._find_visible_controls(page)
        if not visible:
            # Screenshot the dead end so the operator can SEE why (cookie
            # wall? login wall? expired listing?) instead of guessing.
            shot = await self._screenshot(page, ctx, "generic_no_form")
            result.screenshot_url = shot
            host = getattr(job, "url", "") or ""
            try:
                from urllib.parse import urlparse
                host = urlparse(host).netloc or host
            except Exception:
                pass
            result.error = f"no visible form fields found ({host or 'page'})"
            return result

        # Build the simplified form context for the LLM
        simplified = [
            f"Tag: {i['tag']}, Type: {i['type']}, Name: {i['name'][:40]}, Label: {i['label'][:50]}, Required: {i['required']}"
            for i in visible[:MAX_INPUTS_FOR_LLM]
        ]
        form_context = "\n".join(simplified)

        user_keys = list(ctx.profile.keys()) + list(ctx.answers.keys())

        # LLM mapping (sync langchain engine, run off the event loop). If it
        # fails or returns nothing, degrade gracefully: known profile/Q&A
        # fields are still filled via answer_for and unknown required
        # questions are still escalated — only browser-level problems bail.
        mapping = {}
        from backend.services.inference import inference
        try:
            mapping = await asyncio.to_thread(
                inference.map_form_fields, form_context, user_keys
            )
        except Exception as e:
            if ctx.logger is not None:
                try:
                    await ctx.logger.failed(
                        "apply", "llm_mapping_failed",
                        application_id=ctx.application_id,
                        error_text=f"LLM field mapping failed: {e}",
                    )
                except Exception:
                    pass
        mapping = mapping or {}

        # Surface LLM outages (quota exhausted, bad key…) once per distinct
        # message instead of silently mapping every form with {}.
        llm_error = getattr(inference, "last_error", None)
        if llm_error and llm_error not in _LLM_ERRORS_SEEN and ctx.logger is not None:
            _LLM_ERRORS_SEEN.add(llm_error)
            try:
                await ctx.logger.failed(
                    "system", "llm_unavailable",
                    application_id=ctx.application_id,
                    error_text=(
                        f"LLM unavailable: {llm_error} — field mapping degraded. "
                        "Add credits or check the API key."
                    ),
                )
            except Exception:
                pass

        can_escalate = ctx.repo is not None and bool(ctx.application_id)

        unfilled = 0          # unresolved required fields with no escalation path
        escalated = 0         # confirmations created this run
        needs_input = False

        for field in visible:
            decision = decide_field(field, mapping, ctx)

            if decision["action"] == "fill":
                selector = self._selector_for(field)
                if not selector:
                    if field.get("required"):
                        unfilled += 1
                    continue
                value = decision["value"]
                locator = field["frame"].locator(selector).first

                try:
                    filled = await self._fill_locator(locator, value)
                except Exception:
                    filled = False
                if filled:
                    result.filled_fields[decision["key"]] = value
                    await self._log_field(ctx, decision["key"][:40], value)
                elif field.get("required"):
                    unfilled += 1
                continue

            if decision["action"] == "check":
                selector = self._selector_for(field)
                if selector:
                    try:
                        locator = field["frame"].locator(selector).first
                        if await locator.count():
                            await locator.check()
                            result.filled_fields[decision["key"]] = "checked"
                    except Exception:
                        if field.get("required"):
                            unfilled += 1
                elif field.get("required"):
                    unfilled += 1
                continue

            if decision["action"] == "escalate":
                if can_escalate and escalated < MAX_ESCALATIONS_PER_FORM:
                    needs_input = True
                    escalated += 1
                    await self._escalate(ctx, decision["question"],
                                         decision["field_type"], decision.get("options"))
                else:
                    if field.get("required"):
                        unfilled += 1
                continue

            # skip — any unresolved REQUIRED field (except file inputs, which
            # the resume uploader handles above) counts against the confidence
            # gate so we never submit a knowingly-incomplete form.
            if field.get("required") and field_kind(field) != "file":
                unfilled += 1
            continue

        # Resume upload (file inputs across every frame)
        if ctx.resume_path:
            for field in visible:
                if field_kind(field) != "file":
                    continue
                try:
                    file_input = field["frame"].locator(
                        f"{field['tag'].lower()}[type='file']"
                    ).first
                    if await file_input.count():
                        await file_input.set_input_files(ctx.resume_path)
                        result.filled_fields["resume"] = "uploaded"
                except Exception:
                    pass

        # Pre-submit consent sweep: tick every visible unchecked consent box
        # (labels vary wildly) and never submit while a required checkbox is
        # unresolved — an application sent without accepting T&C is worse
        # than no application at all.
        _, consent_blocked = await self._ensure_consent_checked(visible)

        screenshot = await self._screenshot(page, ctx, "generic_before_submit")
        result.screenshot_url = screenshot

        # Escalation happened — pause and let the user answer via Telegram
        if needs_input:
            result.success = False
            result.needs_input = True
            result.error = "Category-A question encountered — awaiting user input"
            return result

        # A required checkbox that we could not accept (not consent wording,
        # or consent wording we failed to click) blocks submission entirely.
        if consent_blocked:
            result.success = False
            result.error = f"required checkbox not accepted — {consent_blocked}"
            return result

        # Confidence gate: too many required fields left unfilled -> Tier 3
        if unfilled > MAX_UNFILLED_BEFORE_BAIL:
            result.success = False
            result.error = f"low confidence — {unfilled} required fields unfilled"
            return result

        if ctx.dry_run:
            result.success = True
            result.dry_run = True
            result.error = "dry_run"
            return result

        # Submit (best effort, across frames)
        try:
            submit = await self._find_submit(page)
            if submit is None:
                result.error = "submit button not found"
                return result
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _ensure_consent_checked(self, visible) -> tuple:
        """Tick every visible unchecked consent/opt-in checkbox.

        Returns (fixed_labels, blocker): `blocker` is the label of a
        REQUIRED checkbox that could not be resolved — when set, the form
        must NOT be submitted.
        """
        fixed: List[str] = []
        blocker: Optional[str] = None
        for field in visible:
            if field_kind(field) != "checkbox":
                continue
            if field.get("checked"):
                continue
            label = question_text(field)
            if is_consent_label(label):
                selector = self._selector_for(field)
                if selector:
                    try:
                        locator = field["frame"].locator(selector).first
                        if await locator.count():
                            await locator.check()
                            fixed.append(label[:40])
                            continue
                    except Exception:
                        pass
                blocker = blocker or (label or "unlabelled consent checkbox")
            elif field.get("required"):
                blocker = blocker or (label or "required checkbox")
        return fixed, blocker

    async def _escalate(self, ctx: ApplyContext, question: str, field_type: str,
                        options: List[str] | None = None):
        """Create a pending confirmation so the user can answer later."""
        if ctx.repo is None or not ctx.application_id:
            return
        try:
            conf = await ctx.repo.create_confirmation(
                application_id=ctx.application_id,
                question_text=question,
                field_type=field_type,
                options=options,
            )
            if ctx.logger is not None:
                await ctx.logger.escalated(
                    "apply", "question_escalated",
                    application_id=ctx.application_id,
                    metadata={"question": question, "confirmation_id": str(conf.id) if conf else None},
                )
        except Exception:
            pass

    async def _fill_locator(self, locator, value: str) -> bool:
        """Fill a single resolved control (text/select/textarea) by value."""
        if not value:
            return False
        try:
            if not await locator.count():
                return False
            tag = (await locator.evaluate("e => e.tagName")).lower()
            el_type = (await locator.evaluate("e => (e.type || '').toLowerCase()")) if tag == "input" else ""
            if tag == "select":
                try:
                    await locator.select_option(label=value)
                    return True
                except Exception:
                    await locator.select_option(value=value)
                    return True
            if tag == "textarea" or el_type in TEXTUAL_INPUT_TYPES:
                await locator.fill(str(value))
                return True
            if el_type == "checkbox":
                if str(value).lower() in ("true", "yes", "1", "on", "y"):
                    await locator.check()
                else:
                    await locator.uncheck()
                return True
            await locator.fill(str(value))
            return True
        except Exception:
            return False

    async def _find_submit(self, page):
        """Locate a submit button, searching every frame."""
        for frame in page.frames:
            try:
                submit = frame.locator(
                    "input[type='submit'], button[type='submit'], "
                    "button:has-text('Submit'), button:has-text('Apply'), "
                    "button:has-text('Send')"
                ).first
                if await submit.count():
                    return submit
            except Exception:
                continue
        return None

    @staticmethod
    def _selector_for(field: Dict) -> str:
        if field.get("name"):
            return f"{field['tag'].lower()}[name='{field['name']}']"
        if field.get("id"):
            return f"#{field['id']}"
        return ""
