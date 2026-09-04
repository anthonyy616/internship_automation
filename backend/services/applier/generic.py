"""
Generic applier (Tier 2) — LLM-assisted filling of unknown/arbitrary forms.

Extracts every form control (including controls inside iframes, popups,
open/closed shadow roots, and contenteditable fields), asks the LLM to map
each input to a profile/answer key, fills what it can, and escalates what
it can't:

    - required text/select questions with no known answer become Telegram
      confirmations (Category-A escalation) so the user can answer once and
      the answer is banked in profile_answers for every future form;
    - when escalation isn't available (no repo/application) or too many
      fields remain unfilled it bails to Tier 3 with a "low confidence"
      failure instead of guessing;
    - multi-step forms (Next/Continue pages) are walked step by step;
    - consent/T&C checkboxes are a hard pre-submit gate;
    - submission is only recorded as success when the page actually
      confirms it (thank-you / navigation) — a validation error, CAPTCHA
      or ambiguous outcome fails honestly with a screenshot.

If no form is visible at all (many job posts render the application
behind a button or a late-loading iframe), it waits briefly, then clicks
through ATS application links (Lever, Greenhouse, Ashby, ...) before
giving up with a screenshot of the dead end.
"""

import asyncio
import random
import re
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from backend.config import settings
from backend.services.applier.base import (
    ApplierAdapter, ApplyContext, ApplyResult,
)
from backend.services.applier.stealth import dismiss_cookie_banners

MAX_INPUTS_FOR_LLM = 60
MAX_UNFILLED_BEFORE_BAIL = 3
MAX_ESCALATIONS_PER_FORM = 8

# HTML input types we know how to fill with free text
TEXTUAL_INPUT_TYPES = {"", "text", "email", "tel", "number", "url", "date", "search", "password"}

SUBMIT_TEXTS = ("Submit Application", "Submit application", "Submit", "Send Application", "Apply Now")
NEXT_TEXTS = ("Next", "Continue", "Save and continue", "Save & continue", "Review")

# ----------------------------------------------------------------------
# Extraction script v2: frames + shadow roots + editable + honeypots.
# Each collected control gets a stable data-botidx attribute so filling
# never has to fight with quirky/duplicated name/id attributes.
# ----------------------------------------------------------------------

EXTRACT_JS = """() => {
    const out = [];
    const HONEYPOT_NAMES = ['website', 'company', 'url', 'fax', 'phone2',
                            'confirm_email', 'email2', 'homepage', 'your_website', 'address2'];
    const BOTID = 'botidx';
    let counter = 0;

    const labelFor = (el, root) => {
        if (el.id) {
            const l = root.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (l) return l.innerText.trim();
        }
        if (el.closest && el.closest('label')) return el.closest('label').innerText.trim();
        const prev = el.previousElementSibling;
        if (prev && prev.tagName === 'LABEL') return prev.innerText.trim();
        if (el.parentElement && el.parentElement.tagName === 'LABEL') return el.parentElement.innerText.trim();
        if (el.placeholder) return el.placeholder.trim();
        // Last resort: container text minus controls (buttons, other inputs)
        if (el.parentElement) {
            let t = '';
            for (const n of el.parentElement.childNodes) {
                if (n.nodeType === 3) t += n.textContent + ' ';
                else if (n.nodeType === 1 && !['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(n.tagName)) {
                    if (n.tagName === 'LABEL') t += n.innerText + ' ';
                }
            }
            return t.trim().slice(0, 60);
        }
        return '';
    };

    const collect = (root) => {
        root.querySelectorAll('input, select, textarea, [contenteditable="true"], [role="textbox"]')
            .forEach((el) => {
                const id = el.id, name = el.name, ph = el.placeholder;
                let label = labelFor(el, root);
                const rect = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                const visible = el.offsetParent !== null && rect.width > 0 && rect.height > 0
                    && cs.visibility !== 'hidden' && cs.display !== 'none';
                const hn = (name || '').toLowerCase();
                const honeypot = el.type === 'hidden'
                    || (cs.display === 'none' && el.tabIndex < 0)
                    || el.getAttribute('aria-hidden') === 'true'
                    || (hn && HONEYPOT_NAMES.indexOf(hn) !== -1);
                let options = [];
                if (el.tagName === 'SELECT') {
                    options = Array.from(el.options)
                        .map(o => (o.text || o.value || '').trim()).filter(Boolean);
                }
                counter += 1;
                el.setAttribute('data-' + BOTID, String(counter));
                out.push({
                    idx: counter, tag: el.tagName, type: el.type || '', name: name || '', id: id || '',
                    placeholder: ph || '', label: label, visible: visible, honeypot: honeypot,
                    required: el.required === true
                        || el.getAttribute('aria-required') === 'true'
                        || (el.closest && !!el.closest('[aria-required="true"]')),
                    checked: el.checked === true, options: options,
                    editable: el.isContentEditable === true || el.getAttribute('role') === 'textbox'
                });
            });
        root.querySelectorAll('*').forEach((el) => {
            if (el.shadowRoot) collect(el.shadowRoot);
        });
    };
    collect(document);
    return out;
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

# Labels that hint at autocomplete/typeahead comboboxes (need option-click)
COMBOBOX_HINTS = (
    "city", "school", "university", "country", "location", "institution",
    "skills", "language", "college",
)

FORM_WAIT_ROUNDS = 6      # poll up to ~9s for late-rendering forms/iframes
FORM_WAIT_SECONDS = 1.5

# LLM failure messages we have already surfaced to the operator (per worker
# process), so a dead OpenAI key doesn't spam the event log 40 times.
_LLM_ERRORS_SEEN: set = set()


def _to_iso_date(value: str) -> str:
    """Normalise common date formats to YYYY-MM-DD for input[type=date]."""
    v = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %B %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def field_kind(field: Dict) -> str:
    """Classify a control into 'text' | 'select' | 'checkbox' | 'radio' |
    | 'file' | 'button' | 'hidden'."""
    tag = (field.get("tag") or "").lower()
    ftype = (field.get("type") or "").lower()
    if field.get("editable"):
        return "text"
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


def is_consent_label(label: str) -> bool:
    """True when a checkbox label asks for consent/opt-in that every
    applicant must give before the form can be submitted."""
    lower = (label or "").lower()
    return any(tok in lower for tok in CONSENT_TOKENS)


# Deterministic question -> profile-key hints. This is the no-LLM safety
# net: standard questions (name, email, university…) are matched to the
# profile even when OpenAI credits are exhausted and mapping returns {}.
PROFILE_HINTS = (
    (("full name", "first name", "last name", "your name", "candidate name", "what is your name"), "name"),
    (("email", "e-mail", "email address", "e-mail address", "contact email"), "email"),
    (("phone", "telephone", "mobile", "phone number", "contact number", "whatsapp"), "phone"),
    (("university", "college", "institution", "school", "education"), "university"),
    (("major", "degree", "field of study", "course", "programme", "program", "study"), "major"),
    (("skills", "technologies", "tech stack", "programming languages"), "skills"),
    (("portfolio", "github", "personal website", "website url", "linkedin"), "portfolio_url"),
)


def profile_key_for_question(question: str) -> str:
    """Map a common question to its profile key, or '' when unknown."""
    q = (question or "").lower()
    for tokens, key in PROFILE_HINTS:
        if any(tok in q for tok in tokens):
            return key
    return ""


def decide_field(field: Dict, mapping: Dict, ctx: ApplyContext) -> Dict:
    """
    Decide what to do with one extracted control.

    Returns one of:
        {"action": "fill",    "key": ..., "value": ...}
        {"action": "escalate", "question": ..., "field_type": ..., "options": ...}
        {"action": "check",   "key": ...}
        {"action": "skip",    "reason": ...}

    Escalation happens only for *required* free-text/select questions that
    have no stored answer. Optional fields, honeypots, and consent-style
    checkboxes are skipped (never guessed, never escalated).
    """
    # Honeypots must never be filled or counted
    if field.get("honeypot"):
        return {"action": "skip", "reason": "honeypot"}

    kind = field_kind(field)
    if kind in ("file", "hidden", "button"):
        return {"action": "skip", "reason": f"non-fillable kind {kind}"}

    required = bool(field.get("required"))
    question = question_text(field)

    # 1) LLM mapping: name/id -> profile/answer key
    key = ""
    if mapping:
        key = (mapping.get(field.get("name")) or mapping.get(field.get("id")) or "").strip()

    # 1b) No-LLM safety net: deterministic question -> profile key
    if not key and question:
        key = profile_key_for_question(question)

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

    # ------------------------------------------------------------------
    # Control scanning
    # ------------------------------------------------------------------

    async def _scan_controls(self, page):
        """Collect controls from every page (incl. popups) and frame,
        including shadow-DOM controls (EXTRACT_JS walks shadow roots)."""
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
            visible = [c for c in controls if c.get("visible") and not c.get("honeypot")]
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

    async def _click_apply_button(self, page) -> bool:
        """Click a visible "Apply" / "Apply for this position" button that
        reveals a modal/embedded form (remotive, jobicy, most job boards).

        Many job pages render the actual form only after the Apply button is
        clicked — previously those pages failed with "no visible form fields".
        Known-ATS links are left for _click_ats_link; here we prefer buttons
        (which open in-page modals) over anchors (which navigate away).
        """
        APPLY_RE = re.compile(r"apply\s*(for this position|now|here|today)?\s*$", re.I)
        ATS_HINTS = (
            "lever.co", "greenhouse.io", "ashbyhq.com", "workdayjobs",
            "smartrecruiters", "workable.com", "recruitee", "bamboohr",
        )
        try:
            for p in list(page.context.pages) or [page]:
                for frame in p.frames:
                    for sel in ("button", "a"):
                        try:
                            loc = frame.locator(sel).filter(has_text=APPLY_RE)
                            total = await loc.count()
                            for i in range(min(total, 6)):
                                candidate = loc.nth(i)
                                try:
                                    if not await candidate.is_visible():
                                        continue
                                    href = await candidate.get_attribute("href") or ""
                                    if any(h in href for h in ATS_HINTS):
                                        continue  # handled by _click_ats_link
                                    await candidate.click(timeout=8000)
                                    await page.wait_for_timeout(2500)
                                    return True
                                except Exception:
                                    continue
                        except Exception:
                            continue
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    # Button finding
    # ------------------------------------------------------------------

    async def _find_button(self, page, texts: tuple):
        """Find the first VISIBLE button whose text matches any of `texts`,
        searching every page in the context (popups included) and every
        frame. Returns a locator or None.

        Visibility filtering is essential: job-board pages are littered with
        hidden "Submit"/"Apply" buttons inside closed modals (e.g. Remotive's
        dead-link-report modal), and matching those turns submission into a
        click-on-an-invisible-element timeout.
        """
        for p in list(page.context.pages) or [page]:
            for frame in p.frames:
                for text in texts:
                    try:
                        locator = frame.locator(
                            f"button:visible:has-text('{text}'), "
                            f"input[type='submit']:visible[value*='{text}']"
                        ).first
                        if await locator.count():
                            return locator
                    except Exception:
                        continue
        return None

    async def _find_submit(self, page):
        """Locate a submit button (kept for compatibility with tier-1 callers)."""
        return await self._find_button(page, SUBMIT_TEXTS)

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------

    async def fill_and_submit(self, page, job, ctx: ApplyContext) -> ApplyResult:
        result = ApplyResult(success=False, platform=self.platform)

        # A 403/429 on the main document = blocked before we even start
        blocked = getattr(ctx, "http_blocked", None)
        if blocked:
            shot = await self._screenshot(page, ctx, "generic_blocked")
            result.screenshot_url = shot
            result.error = f"blocked by site (HTTP {'/'.join(map(str, sorted(set(blocked))))})"
            return result

        await dismiss_cookie_banners(page)

        # Poll for the form: many job pages render the application inside a
        # late-loading iframe or after JS kicks in.
        visible = await self._find_visible_controls(page)
        if not visible:
            # Form may live on the ATS domain behind an "Apply" link
            if await self._click_ats_link(page):
                await page.wait_for_timeout(1500)
                visible = await self._find_visible_controls(page)
        if not visible:
            # ... or behind an in-page "Apply" button (modal/embedded form)
            if await self._click_apply_button(page):
                await page.wait_for_timeout(1500)
                visible = await self._find_visible_controls(page)
        if not visible:
            # Screenshot the dead end so the operator can SEE why (cookie
            # wall? login wall? expired listing?) instead of guessing.
            shot = await self._screenshot(page, ctx, "generic_no_form")
            result.screenshot_url = shot
            host = urlparse(getattr(job, "url", "") or "").netloc or (getattr(job, "url", "") or "")
            result.error = f"no visible form fields found ({host or 'page'})"
            return result

        # Build the simplified form context for the LLM
        simplified = [
            f"Tag: {i['tag']}, Type: {i['type']}, Name: {i['name'][:40]}, Label: {i['label'][:50]}, Required: {i['required']}"
            for i in visible[:MAX_INPUTS_FOR_LLM]
        ]
        form_context = "\n".join(simplified)
        user_keys = list(ctx.profile.keys()) + list(ctx.answers.keys())

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
        escalated: List[int] = [0]
        before_url = page.url
        step = 0

        while True:
            step += 1
            if step > settings.apply_max_steps:
                result.success = False
                result.error = f"form has too many steps (> {settings.apply_max_steps})"
                return result

            await dismiss_cookie_banners(page)
            visible = await self._find_visible_controls(page)
            if not visible:
                result.success = False
                result.error = "no visible form fields found"
                return result

            outcome = await self._fill_page(page, ctx, visible, result, mapping, can_escalate, escalated)

            if outcome["needs_input"]:
                # Category-A question hit — pause and let the user answer
                result.success = False
                result.needs_input = True
                result.error = "Category-A question encountered — awaiting user input"
                return result

            screenshot = await self._screenshot(page, ctx, "generic_before_submit")
            result.screenshot_url = screenshot

            if outcome["consent_blocked"]:
                # A required checkbox we could not accept blocks submission
                result.success = False
                result.error = f"required checkbox not accepted — {outcome['consent_blocked']}"
                return result

            if outcome["unfilled"] > MAX_UNFILLED_BEFORE_BAIL:
                result.success = False
                result.error = f"low confidence — {outcome['unfilled']} required fields unfilled"
                return result

            # Dry-run: fill + screenshot, never click submit
            if ctx.dry_run:
                result.success = True
                result.dry_run = True
                result.error = "dry_run"
                return result

            submit_btn = await self._find_button(page, SUBMIT_TEXTS)
            next_btn = await self._find_button(page, NEXT_TEXTS)

            if submit_btn is not None and next_btn is None:
                return await self._submit(page, ctx, result)
            if next_btn is not None:
                # The step lives on the page that owns the button (may be a
                # popup opened by the job board's Apply button).
                step_page = next_btn.page
                try:
                    await next_btn.click(timeout=10000)
                except Exception as e:
                    result.success = False
                    result.error = f"next-step click failed: {e}"
                    return result
                await step_page.wait_for_timeout(1200)
                err = await self._first_validation_error(step_page)
                if err:
                    result.success = False
                    result.error = f"form validation on step {step}: {err}"
                    return result
                continue
            if submit_btn is not None:
                return await self._submit(page, ctx, result)

            result.success = False
            result.error = "submit/next button not found"
            return result

    async def _submit(self, page, ctx, result: ApplyResult) -> ApplyResult:
        """Click submit and verify the outcome instead of assuming success.

        The form (and its post-submit confirmation) may live in a popup
        opened by the job board — the submit button's own page is used for
        verification, never the original page.
        """
        submit = await self._find_button(page, SUBMIT_TEXTS)
        if submit is None:
            result.success = False
            result.error = "submit button not found"
            return result
        form_page = submit.page
        before_url = form_page.url
        try:
            await submit.click(timeout=10000)
        except Exception as e:
            result.success = False
            result.error = f"submit failed: {e}"
            return result

        state = await self.detect_submission_state(form_page, before_url)
        shot2 = await self._screenshot(form_page, ctx, "generic_after_submit")
        result.screenshot_url = shot2 or result.screenshot_url

        if state == "challenge":
            result.success = False
            result.challenge = True
            result.error = "human verification required (captcha/login wall)"
            return result
        if state == "validation_error":
            result.success = False
            result.error = "submission validation error: " + (
                await self._first_validation_error(form_page) or "see screenshot"
            )
            return result
        if state == "success":
            result.success = True
            result.applied_via = "form"
            return result
        result.success = False
        result.error = "submission outcome unknown — verify via email"
        return result

    # ------------------------------------------------------------------
    # One page of the form
    # ------------------------------------------------------------------

    async def _fill_page(self, page, ctx, visible, result, mapping, can_escalate, escalated) -> Dict:
        """Fill everything decidable on the current step.

        Returns {"needs_input", "unfilled", "consent_blocked", "escalated"}.
        """
        unfilled = 0
        needs_input = False

        for field in visible:
            decision = decide_field(field, mapping, ctx)
            action = decision["action"]

            if action == "fill":
                filled = await self._fill_field(field, decision["value"], ctx)
                if filled:
                    result.filled_fields[decision["key"]] = decision["value"]
                    await self._log_field(ctx, decision["key"][:40], decision["value"])
                elif field.get("required"):
                    unfilled += 1
                continue

            if action == "check":
                ok = await self._check_field(field)
                if ok:
                    result.filled_fields[decision["key"]] = "checked"
                elif field.get("required"):
                    unfilled += 1
                continue

            if action == "escalate":
                if can_escalate and escalated[0] < MAX_ESCALATIONS_PER_FORM:
                    needs_input = True
                    escalated[0] += 1
                    await self._escalate(ctx, decision["question"],
                                         decision["field_type"], decision.get("options"))
                elif field.get("required"):
                    unfilled += 1
                continue

            # skip — any unresolved REQUIRED field (except file inputs and
            # honeypots) counts against the confidence gate so we never
            # submit a knowingly-incomplete form.
            if field.get("required") and field_kind(field) != "file" and decision.get("reason") != "honeypot":
                unfilled += 1

        await self._upload_resume(page, ctx, visible, result)
        _, consent_blocked = await self._ensure_consent_checked(visible)

        return {
            "needs_input": needs_input,
            "unfilled": unfilled,
            "consent_blocked": consent_blocked,
            "escalated": escalated[0],
        }

    # ------------------------------------------------------------------
    # Field filling (per control type)
    # ------------------------------------------------------------------

    async def _fill_field(self, field, value: str, ctx) -> bool:
        selector = self._selector_for(field)
        if not selector or not value:
            return False
        try:
            locator = field["frame"].locator(selector).first
            if not await locator.count():
                return False
            kind = field_kind(field)
            if kind == "select":
                try:
                    await locator.select_option(label=str(value))
                    return True
                except Exception:
                    await locator.select_option(value=str(value))
                    return True
            if kind == "radio":
                try:
                    await locator.check()
                    return True
                except Exception:
                    await field["frame"].locator(f"{selector}[value='{value}']").first.click()
                    return True
            if kind == "checkbox":
                if str(value).lower() in ("true", "yes", "1", "on", "y"):
                    await locator.check()
                else:
                    await locator.uncheck()
                return True
            if kind == "file":
                return False  # handled by _upload_resume

            el_type = ""
            try:
                el_type = (await locator.evaluate("e => (e.type || '').toLowerCase()")) or ""
            except Exception:
                pass

            if el_type == "date":
                iso = _to_iso_date(str(value))
                if iso:
                    await locator.fill(iso)
                    return True
                return False

            question = question_text(field)
            if el_type == "text" and any(h in question.lower() for h in COMBOBOX_HINTS):
                return await self._fill_combobox(locator, str(value))

            # textarea, contenteditable, plain text, email, tel, ...
            return await self._human_type(locator, str(value))
        except Exception:
            return False

    async def _fill_combobox(self, locator, value: str) -> bool:
        """Typeahead fields: click, type, pick the matching option."""
        try:
            await locator.click()
            await locator.fill("")
            await locator.type(str(value), delay=random.randint(40, 90))
            await locator.page.wait_for_timeout(600)
            try:
                option = locator.page.locator("[role='option']").filter(has_text=str(value)[:25]).first
                if await option.count():
                    await option.click()
                    return True
            except Exception:
                pass
            await locator.press("Enter")
            await locator.page.wait_for_timeout(300)
            return True
        except Exception:
            return False

    async def _check_field(self, field) -> bool:
        selector = self._selector_for(field)
        if not selector:
            return False
        try:
            locator = field["frame"].locator(selector).first
            if await locator.count():
                await locator.check()
                return True
        except Exception:
            pass
        return False

    async def _upload_resume(self, page, ctx, visible, result) -> None:
        """Upload the resume to file inputs, with a file-chooser fallback
        for drag-drop-only widgets."""
        if not ctx.resume_path:
            return
        for field in visible:
            if field_kind(field) != "file":
                continue
            try:
                file_input = field["frame"].locator("input[type='file']").first
                if await file_input.count():
                    await file_input.set_input_files(ctx.resume_path)
                    result.filled_fields["resume"] = "uploaded"
            except Exception:
                pass
        if "resume" in result.filled_fields:
            return
        try:
            btn = page.get_by_role("button", name=re.compile(r"upload|attach|add resume|cv", re.I)).first
            async with page.expect_file_chooser(timeout=2500) as fc:
                if await btn.count():
                    await btn.click(timeout=2000)
            chooser = await fc.value
            await chooser.set_files(ctx.resume_path)
            result.filled_fields["resume"] = "uploaded"
        except Exception:
            pass

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
                if await self._check_field(field):
                    fixed.append(label[:40])
                    continue
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

    @staticmethod
    def _selector_for(field: Dict) -> str:
        if field.get("name"):
            return f"{field['tag'].lower()}[name='{field['name']}']"
        if field.get("id"):
            return f"#{field['id']}"
        # Stable attribute set by EXTRACT_JS — never collides, works for
        # contenteditable/shadow controls too.
        if field.get("idx") is not None:
            return f"[data-botidx='{field['idx']}']"
        return ""