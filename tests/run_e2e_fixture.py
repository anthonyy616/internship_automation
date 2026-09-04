"""
E2E acceptance harness for the smart-agent tiers (T0-T3).

Boots the local fixture site (tests/fixture_form/app.py), then drives the
REAL TieredApplier against each scenario and asserts the expected outcome:

    /step1     multi-step walk -> verified success
    /shadow    shadow-DOM form -> verified success
    /slow      late-rendering form (polling) -> verified success
    /form-ok   single-page consent form -> verified success
    /form-bad  profile email rejected -> validation_error (failed)
    /captcha   submit answered with a challenge -> paused (challenge)
    /blocked   HTTP 403 main document -> honest "blocked" failure

Run:    python -m tests.run_e2e_fixture
Skips (exit 0) when no browser can be launched; real failures exit 1.

This hits only localhost — safe to run any time.
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

FIXTURE_PORT = 8734
FIXTURE_BASE = f"http://127.0.0.1:{FIXTURE_PORT}"
RESUME = Path("data/resume.pdf")


class FakeConfig:
    async def get_profile(self):
        from backend.services.config_service import ProfileConfig
        return ProfileConfig({
            "name": "Anthony Ogbuah",
            "email": "anthony@example.com",
            "university": "European University of Lefke",
            "major": "Computer Engineering",
            "skills": ["Python", "TypeScript"],
            "portfolio_url": "https://anthonyy616.vercel.app",
        })

    async def get_apply_config(self):
        return {"dry_run": False}


class FakeRepo:
    """Minimal repo: an answer bank + a no-op confirmation creator."""

    def __init__(self):
        self.answers = {
            "City": "London",
            "University": "European University of Lefke",
            "Date of birth": "2001-05-15",
            "Skills": "Python, TypeScript",
            "Cover letter": "I am a motivated computer engineering student interested in this role.",
        }

    async def get_all_answers(self):
        return [SimpleNamespace(question_text=k, answer_text=v) for k, v in self.answers.items()]

    async def create_confirmation(self, **kwargs):
        return None


def _make_job(path: str):
    return SimpleNamespace(url=f"{FIXTURE_BASE}{path}", title="Fixture Intern", company="FixtureCorp")


async def _run_case(applier, path: str):
    app = SimpleNamespace(id=f"e2e-{path.strip('/')}", filled_fields={}, status="queued", ats_platform=None)
    return await applier.apply(_make_job(path), app)


async def _start_fixture():
    import uvicorn
    from tests.fixture_form.app import app as fixture_app

    config = uvicorn.Config(fixture_app, host="127.0.0.1", port=FIXTURE_PORT, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(40):
        if server.started:
            break
        await asyncio.sleep(0.1)
    return server, task


async def main() -> int:
    server, task = await _start_fixture()

    # Force LOCAL Chromium for the harness — never route fixture runs
    # through paid Hyperbrowser cloud sessions.
    from backend.config import settings
    settings.hyperbrowser_api_key = ""

    # Browser + applier setup (skips gracefully without a browser)
    try:
        from backend.services.applier.tiered import TieredApplier

        applier = TieredApplier(
            config_service=FakeConfig(),
            repo=FakeRepo(),
            resume_path=str(RESUME) if RESUME.exists() else None,
            screenshot_dir="data/screenshots/e2e",
            headless=True,
        )
    except Exception as e:
        print(f"SKIP — could not build applier: {e}")
        server.should_exit = True
        await task
        return 0

    cases = [
        ("/step1",       "success",   None),
        ("/shadow",      "success",   None),
        ("/slow",        "success",   None),
        ("/apply-button", "success",  None),
        ("/form-ok",     "success",   None),
        ("/form-bad",    "failed",    "validation"),
        ("/captcha",     "challenge", None),
        ("/blocked",     "failed",    "blocked"),
    ]

    failures = 0
    for path, expect, expect_sub in cases:
        try:
            result = await _run_case(applier, path)
        except Exception as e:
            print(f"  FAIL  {path}: raised {type(e).__name__}: {e}")
            failures += 1
            continue

        ok = False
        detail = ""
        if expect == "success":
            ok = bool(result.success)
            detail = f"success={result.success} err={result.error[:80]}"
        elif expect == "challenge":
            ok = bool(getattr(result, "challenge", False))
            detail = f"challenge={ok} err={result.error[:80]}"
        elif expect == "failed":
            ok = (not result.success) and (expect_sub in (result.error or "").lower())
            detail = f"success={result.success} err={result.error[:80]}"

        if ok:
            print(f"  PASS  {path}  ({detail})")
        else:
            failures += 1
            print(f"  FAIL  {path}  expected {expect} — {detail}")

    server.should_exit = True
    await task
    print(f"\n{len(cases) - failures}/{len(cases)} E2E cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)