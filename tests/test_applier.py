"""
Offline tests for the tiered auto-apply components (no browser, no DB).

Usage:
    python -m tests.test_applier
"""

import asyncio
import sys

from backend.services.applier.detector import ATSDetector
from backend.services.applier.question_classifier import QuestionClassifier
from backend.services.applier.base import ApplyContext


def test_ats_detector():
    cases = {
        "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://jobs.lever.co/acme/xyz": "lever",
        "https://wd5.myworkdayjobs.com/acme/job/1": "workday",
        "https://jobs.ashbyhq.com/acme/123": "ashby",
        "https://jobs.smartrecruiters.com/Acme/123": "smartrecruiters",
        "https://www.example.com/careers/123": None,
    }
    for url, expected in cases.items():
        assert ATSDetector.detect(url) == expected, f"{url} -> {ATSDetector.detect(url)}"


def test_question_classifier():
    c = QuestionClassifier()
    # Category A — must escalate
    assert c.classify("Will you require visa sponsorship?") == "A"
    assert c.classify("What are your salary expectations?") == "A"
    assert c.classify("What is your notice period?") == "A"
    assert c.classify("What is your gender?") == "A"
    # Category B — auto-answer when an answer is available
    assert c.classify("Which university did you attend?", answer_available=True) == "B"
    assert c.classify("What is your major?", answer_available=True) == "B"
    # Unknown question with no stored answer must escalate, not guess
    assert c.classify("Some random question") == "A"
    # ...but is safe once an answer exists
    assert c.classify("Some random question", answer_available=True) == "B"


def test_apply_context_answer_matching():
    ctx = ApplyContext(
        profile={"name": "Anthony Ogbuah", "email": "anthony@example.com"},
        answers={
            "Which university did you attend?": "European University of Lefke",
            "What is your major?": "Computer Engineering",
        },
    )
    # Exact match
    assert ctx.answer_for("Which university did you attend?") == "European University of Lefke"
    # Fuzzy / normalized match (punctuation differences)
    assert ctx.answer_for("Which university did you attend ?") == "European University of Lefke"
    # Hint-based match
    assert ctx.answer_for("University", field_hint="University") == "European University of Lefke"
    # Profile value via prefilled
    ctx.prefilled["visa"] = "Yes, citizen"
    assert ctx.answer_for("visa") == "Yes, citizen"
    # Unknown
    assert ctx.answer_for("Totally unknown question") is None


async def test_tiered_context_build():
    from backend.services.applier.tiered import TieredApplier
    from backend.services.config_service import ProfileConfig

    class FakeConfig:
        async def get_profile(self):
            return ProfileConfig({
                "name": "Anthony Ogbuah", "email": "a@example.com",
                "university": "EUL", "major": "CompEng", "skills": ["Python"],
                "portfolio_url": "https://x.dev",
            })

        async def get_apply_config(self):
            return {"dry_run": True}

    class FakeRepo:
        async def get_all_answers(self):
            class A:
                question_text = "What is your major?"
                answer_text = "Computer Engineering"
            return [A()]

    class FakeJob:
        url = "https://boards.greenhouse.io/acme/jobs/1"
        title = "Intern"
        company = "Acme"

    class FakeApp:
        id = "app-1"
        filled_fields = {"first_name": "Anthony"}

    applier = TieredApplier(
        config_service=FakeConfig(),
        repo=FakeRepo(),
        resume_path=None,
        screenshot_dir="data/screenshots",
    )
    # Monkeypatch browser launch so no real browser is opened
    ctx = await applier._build_context(FakeJob(), FakeApp())

    assert ctx.profile["name"] == "Anthony Ogbuah"
    assert ctx.profile["skills"] == "Python"
    assert ctx.answers["What is your major?"] == "Computer Engineering"
    assert ctx.dry_run is True
    assert ctx.prefilled == {"first_name": "Anthony"}
    # Default resume path points at the repo's resume (exists) or None
    if ctx.resume_path is not None:
        from pathlib import Path
        assert Path(ctx.resume_path).exists()


def test_tiered_registers_tier1_adapters():
    from backend.services.applier.tiered import TieredApplier
    t = TieredApplier(config_service=None)
    assert "greenhouse" in t.tier1
    assert "lever" in t.tier1
    assert "ashby" in t.tier1
    assert "workday" in t.tier1


def main():
    tests = [
        test_ats_detector,
        test_question_classifier,
        test_apply_context_answer_matching,
        test_tiered_context_build,
        test_tiered_registers_tier1_adapters,
    ]
    failures = 0
    for test in tests:
        try:
            if asyncio.iscoroutinefunction(test):
                asyncio.run(test())
            else:
                test()
            print(f"  PASS  {test.__name__}")
        except Exception as e:
            failures += 1
            import traceback
            print(f"  FAIL  {test.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()