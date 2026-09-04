"""
Deterministic local fixture forms for E2E-testing the applier (T0-T3).

Served by the E2E harness (tests/run_e2e_fixture.py) on a local port so
the whole apply pipeline can be exercised without touching real sites:

    /step1      multi-step flow: personal details -> university -> review
                -> thank-you (with combobox, date, consent, honeypot)
    /shadow     form rendered inside an (open) shadow DOM root
    /slow       form that only renders 2s after page load
    /apply-button  form hidden behind an "Apply for this position" button
                (modal) — the remotive.com pattern
    /captcha    submit is answered with a reCAPTCHA-style challenge
    /blocked    main document returns HTTP 403
    /form-ok    single-page form that always confirms on submit
    /form-bad   single-page form that rejects the profile email

Run:  python -m tests.run_e2e_fixture
"""

import re
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, Response

app = FastAPI(title="Applier Fixture Forms")

_STYLE = """
<style>
body { font-family: sans-serif; padding: 40px; }
label { display: block; margin: 14px 0 4px; }
input, select, textarea, [contenteditable="true"] {
  padding: 8px; min-width: 280px; border: 1px solid #999; border-radius: 4px;
}
button { margin-top: 18px; padding: 10px 24px; cursor: pointer; }
[role="option"] { padding: 4px 8px; border-bottom: 1px solid #eee; }
[role="option"]:hover { background: #eef; cursor: pointer; }
.validation-error, .error { color: #b00020; margin-top: 10px; }
.ok { color: #0a7d32; }
</style>
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!DOCTYPE html><html><head><title>{title}</title>{_STYLE}</head>"
                        f"<body><h1>{title}</h1>{body}</body></html>")


# ----------------------------------------------------------------------
# Multi-step flow: personal details -> education -> review -> thank-you
# ----------------------------------------------------------------------

STEP1_BODY = """
<form method="post" action="/step2">
  <label>Full name</label><input name="full_name" required>
  <label>Email</label><input name="email" type="email" required>
  <label>Date of birth</label><input name="dob" type="date" required>
  <label>City</label><input name="city" required>
  <div id="city-options"><div role="option">London</div><div role="option">Lagos</div><div role="option">Istanbul</div></div>
  <input name="website" tabindex="-1" style="display:none"> <!-- honeypot -->
  <label><input type="checkbox" name="consent" required> I agree to the terms and conditions</label>
  <button type="submit">Next</button>
</form>
"""

STEP2_BODY = """
<form method="post" action="/step3">
  <label>University</label><input name="university" required>
  <div id="uni-options"><div role="option">European University of Lefke</div><div role="option">University of Lagos</div></div>
  <label>Skills</label><input name="skills" required>
  <label>Cover letter</label><div contenteditable="true"></div>
  <label>Resume (optional)</label><input type="file" name="resume">
  <button type="submit">Next</button>
</form>
"""

STEP3_BODY = """
<p>Review your application, then submit.</p>
<form method="post" action="/thankyou">
  <button type="submit">Submit Application</button>
</form>
"""

THANKYOU_BODY = """
<p class="ok">Thank you! Your application has been received.</p>
<a href="/">Back</a>
"""


@app.get("/", response_class=HTMLResponse)
async def index():
    links = "".join(f'<li><a href="{p}">{p}</a></li>' for p in
                    ("/step1", "/shadow", "/slow", "/captcha", "/blocked", "/form-ok", "/form-bad"))
    return _page("Fixture index", f"<ul>{links}</ul>")


@app.get("/step1", response_class=HTMLResponse)
async def step1_get():
    return _page("Step 1 — Personal details", STEP1_BODY)


@app.post("/step2", response_class=HTMLResponse)
async def step2_post(
    full_name: str = Form(""), email: str = Form(""), dob: str = Form(""),
    city: str = Form(""), consent: str = Form(""),
):
    if "@" not in email or "." not in email:
        return _page("Step 1 — Personal details",
                     f'<div class="validation-error">Please fix your email address: {email}</div>' + STEP1_BODY)
    return _page("Step 2 — Education", STEP2_BODY)


@app.post("/step3", response_class=HTMLResponse)
async def step3_post():
    return _page("Review", STEP3_BODY)


@app.post("/thankyou", response_class=HTMLResponse)
async def thankyou():
    return _page("Application received", THANKYOU_BODY)


# ----------------------------------------------------------------------
# Shadow DOM form
# ----------------------------------------------------------------------

SHADOW_HTML = """
<div id="host"></div>
<script>
  const host = document.getElementById('host');
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `
    <form method="post" action="/thankyou">
      <label>Full name</label><input name="full_name" required>
      <button type="submit">Submit Application</button>
    </form>`;
</script>
"""


@app.get("/shadow", response_class=HTMLResponse)
async def shadow_form():
    return _page("Shadow DOM form", SHADOW_HTML)


# ----------------------------------------------------------------------
# Late-rendering form
# ----------------------------------------------------------------------

SLOW_HTML = """
<div id="form-slot">Loading form…</div>
<script>
  setTimeout(() => {
    document.getElementById('form-slot').innerHTML = `
      <form method="post" action="/thankyou">
        <label>Full name</label><input name="full_name" required>
        <button type="submit">Submit Application</button>
      </form>`;
  }, 2000);
</script>
"""


@app.get("/slow", response_class=HTMLResponse)
async def slow_form():
    return _page("Slow form", SLOW_HTML)


# ----------------------------------------------------------------------
# Form hidden behind an "Apply for this position" button (remotive pattern)
# ----------------------------------------------------------------------

APPLY_BUTTON_HTML = """
<p>This is the job posting. The form appears once you click Apply.</p>
<button id="apply-btn" onclick="document.getElementById('modal').style.display='block'">
  Apply for this position
</button>
<div id="modal" style="display:none">
  <form method="post" action="/apply-button">
    <label>Full name</label><input name="full_name" required>
    <label>Email</label><input name="email" type="email" required>
    <label><input type="checkbox" name="consent" required> I agree to the terms and conditions</label>
    <button type="submit">Submit Application</button>
  </form>
</div>
"""


@app.get("/apply-button", response_class=HTMLResponse)
async def apply_button_get():
    return _page("Apply button form", APPLY_BUTTON_HTML)


@app.post("/apply-button", response_class=HTMLResponse)
async def apply_button_post():
    return _page("Application received", THANKYOU_BODY)


# ----------------------------------------------------------------------
# Challenge wall (reCAPTCHA-style) after submit
# ----------------------------------------------------------------------

CAPTCHA_BODY = """
<p>Submit to trigger the challenge.</p>
<form method="post" action="/captcha">
  <label>Full name</label><input name="full_name" required>
  <label>Email</label><input name="email" type="email" required>
  <button type="submit">Submit Application</button>
</form>
"""


@app.get("/captcha", response_class=HTMLResponse)
async def captcha_get():
    return _page("Captcha", CAPTCHA_BODY)


@app.post("/captcha", response_class=HTMLResponse)
async def captcha_post():
    return _page("Verify you are human",
                 '<div class="g-recaptcha" data-sitekey="fixture-site-key"></div>'
                 '<p>Please verify you are human to continue.</p>')


# ----------------------------------------------------------------------
# Blocked document (HTTP 403)
# ----------------------------------------------------------------------

@app.get("/blocked")
async def blocked():
    return Response("<h1>Access denied</h1><p>Your request was blocked.</p>", status_code=403)


# ----------------------------------------------------------------------
# Single-page forms: always-confirm vs validation-reject
# ----------------------------------------------------------------------

_SINGLE = """
<!-- Hidden decoy submit inside a closed modal — must NEVER be matched
     (regression: remotive's dead-link-report modal "Submit" button). -->
<div id="deadlink-modal" style="display:none">
  <button type="button">Submit</button>
</div>
<form method="post" action="{action}">
  <label>Full name</label><input name="full_name" required>
  <label>Email</label><input name="email" type="email" required>
  <label><input type="checkbox" name="consent" required> I accept the privacy policy</label>
  <button type="submit">Submit Application</button>
</form>
"""


@app.get("/form-ok", response_class=HTMLResponse)
async def form_ok_get():
    return _page("Form OK", _SINGLE.format(action="/form-ok"))


@app.post("/form-ok", response_class=HTMLResponse)
async def form_ok_post():
    return _page("Application received", THANKYOU_BODY)


@app.get("/form-bad", response_class=HTMLResponse)
async def form_bad_get():
    return _page("Form Bad", _SINGLE.format(action="/form-bad"))


@app.post("/form-bad", response_class=HTMLResponse)
async def form_bad_post(email: str = Form("")):
    if "fixture" not in email:
        return _page("Form Bad",
                     f'<div class="validation-error">Please fix your email address: {email}</div>'
                     + _SINGLE.format(action="/form-bad"))
    return _page("Application received", THANKYOU_BODY)