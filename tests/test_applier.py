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


def test_generic_field_kind():
    from backend.services.applier.generic import field_kind
    assert field_kind({"tag": "INPUT", "type": "text"}) == "text"
    assert field_kind({"tag": "INPUT", "type": "email"}) == "text"
    assert field_kind({"tag": "TEXTAREA"}) == "text"
    assert field_kind({"tag": "SELECT"}) == "select"
    assert field_kind({"tag": "INPUT", "type": "checkbox"}) == "checkbox"
    assert field_kind({"tag": "INPUT", "type": "radio"}) == "radio"
    assert field_kind({"tag": "INPUT", "type": "file"}) == "file"
    assert field_kind({"tag": "INPUT", "type": "hidden"}) == "hidden"
    assert field_kind({"tag": "BUTTON", "type": "submit"}) == "button"


def test_generic_question_text_fallbacks():
    from backend.services.applier.generic import question_text
    assert question_text({"label": "Full name", "name": "name"}) == "Full name"
    assert question_text({"label": "", "placeholder": "e.g. London", "name": "city"}) == "e.g. London"
    assert question_text({"label": "", "name": "salary"}) == "salary"
    assert question_text({"label": "", "name": "", "id": "q_5"}) == "q_5"
    assert question_text({}) == ""


def test_generic_decide_escalates_unknown_required():
    from backend.services.applier.generic import decide_field
    from backend.services.applier.base import ApplyContext
    ctx = ApplyContext(profile={"name": "Anthony"}, answers={})
    field = {
        "tag": "INPUT", "type": "text", "label": "Do you require visa sponsorship?",
        "name": "visa", "required": True,
    }
    d = decide_field(field, {}, ctx)
    assert d["action"] == "escalate", d
    assert d["question"] == "Do you require visa sponsorship?"
    assert d["field_type"] == "text"


def test_generic_decide_escalates_select_with_options():
    from backend.services.applier.generic import decide_field
    from backend.services.applier.base import ApplyContext
    ctx = ApplyContext(profile={}, answers={})
    field = {
        "tag": "SELECT", "label": "How did you hear about this role?",
        "name": "source", "required": True,
        "options": ["LinkedIn", "Referral", "Other"],
    }
    d = decide_field(field, {}, ctx)
    assert d["action"] == "escalate", d
    assert d["field_type"] == "select"
    assert d["options"] == ["LinkedIn", "Referral", "Other"]


def test_generic_decide_fills_from_profile_and_answer_bank():
    from backend.services.applier.generic import decide_field
    from backend.services.applier.base import ApplyContext
    ctx = ApplyContext(
        profile={"name": "Anthony Ogbuah"},
        answers={"What is your major?": "Computer Engineering"},
    )
    # Via the LLM mapping -> profile key
    d = decide_field(
        {"tag": "INPUT", "type": "text", "name": "candidate_name", "required": True},
        {"candidate_name": "name"}, ctx,
    )
    assert d == {"action": "fill", "key": "name", "value": "Anthony Ogbuah"}
    # Via the Q&A bank, matched by question text (no LLM mapping needed)
    d = decide_field(
        {"tag": "INPUT", "type": "text", "label": "What is your major?", "required": True},
        {}, ctx,
    )
    assert d["action"] == "fill"
    assert d["value"] == "Computer Engineering"


def test_generic_consent_label_tokens():
    from backend.services.applier.generic import is_consent_label
    # Real-world consent phrasings must be recognised so the pre-submit
    # sweep ticks them (an unchecked T&C box must never be submitted).
    assert is_consent_label("I agree to the terms and conditions")
    assert is_consent_label("I confirm that I have read the data protection information")
    assert is_consent_label("I accept the privacy policy")
    assert is_consent_label("Consent to process my application data")
    assert is_consent_label("I acknowledge the company's GDPR policy")
    assert is_consent_label("I have read and understand the application conditions")
    # Screening questions and optional marketing must NOT be auto-ticked
    assert not is_consent_label("Are you over 18?")
    assert not is_consent_label("Sign up for our newsletter")
    assert not is_consent_label("Gender")
    assert not is_consent_label("")


def test_generic_decide_skips_optional_and_controls():
    from backend.services.applier.generic import decide_field
    from backend.services.applier.base import ApplyContext
    ctx = ApplyContext(profile={}, answers={})
    # Optional unknown question — never guessed, never escalated
    d = decide_field(
        {"tag": "INPUT", "type": "text", "label": "GitHub URL", "required": False}, {}, ctx,
    )
    assert d["action"] == "skip"
    # File inputs are handled by the resume uploader
    d = decide_field(
        {"tag": "INPUT", "type": "file", "name": "resume", "required": True}, {}, ctx,
    )
    assert d["action"] == "skip"
    # Radio/consent wording gate
    d = decide_field(
        {"tag": "INPUT", "type": "checkbox", "label": "I agree to the privacy policy", "required": True}, {}, ctx,
    )
    assert d["action"] == "check"
    d = decide_field(
        {"tag": "INPUT", "type": "checkbox", "label": "Are you over 18?", "required": True}, {}, ctx,
    )
    assert d["action"] == "skip"
    assert "checkbox" in d["reason"]
    # Required but completely unlabelled text field
    d = decide_field(
        {"tag": "INPUT", "type": "text", "name": "", "id": "", "required": True}, {}, ctx,
    )
    assert d["action"] == "skip"
    assert d["reason"] == "required but unlabelled"


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
        test_generic_field_kind,
        test_generic_question_text_fallbacks,
        test_generic_consent_label_tokens,
        test_generic_decide_escalates_unknown_required,
        test_generic_decide_escalates_select_with_options,
        test_generic_decide_fills_from_profile_and_answer_bank,
        test_generic_decide_skips_optional_and_controls,
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