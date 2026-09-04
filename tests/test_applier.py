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


def test_generic_honeypot_is_never_filled_or_counted():
    from backend.services.applier.generic import decide_field
    from backend.services.applier.base import ApplyContext
    ctx = ApplyContext(profile={}, answers={})
    d = decide_field(
        {"tag": "INPUT", "type": "text", "name": "website", "required": True, "honeypot": True},
        {}, ctx,
    )
    assert d == {"action": "skip", "reason": "honeypot"}


def test_generic_profile_hints_without_llm():
    from backend.services.applier.generic import profile_key_for_question, decide_field
    from backend.services.applier.base import ApplyContext
    # Standard questions resolve deterministically when the LLM is down
    assert profile_key_for_question("Full name") == "name"
    assert profile_key_for_question("What is your email address?") == "email"
    assert profile_key_for_question("University") == "university"
    assert profile_key_for_question("Degree / major") == "major"
    assert profile_key_for_question("Some random question") == ""
    ctx = ApplyContext(profile={"name": "Anthony Ogbuah", "email": "a@example.com"}, answers={})
    d = decide_field(
        {"tag": "INPUT", "type": "text", "label": "Full name", "required": True}, {}, ctx,
    )
    assert d["action"] == "fill"
    assert d["value"] == "Anthony Ogbuah"


def test_generic_to_iso_date():
    from backend.services.applier.generic import _to_iso_date
    assert _to_iso_date("2024-05-01") == "2024-05-01"
    assert _to_iso_date("01/05/2024") == "2024-05-01"
    assert _to_iso_date("May 1, 2024") == "2024-05-01"
    assert _to_iso_date("not a date") == ""


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


def test_proxy_rotator_parsing():
    from backend.services.applier.stealth import ProxyRotator
    r = ProxyRotator([
        "http://user1:pass1@proxy-a.example.com:8080",
        "socks5://proxy-b.example.com:1080",
        "https://proxy-c.example.com:3128",
        "not a url",
        "",
        "ftp://bad.example.com:21",
    ])
    # Invalid / unsupported entries are dropped
    assert len(r._entries) == 3
    e0 = r._entries[0]
    assert e0["server"] == "http://proxy-a.example.com:8080"
    assert e0["username"] == "user1"
    assert e0["password"] == "pass1"
    # No creds -> no username/password keys at all
    assert "username" not in r._entries[1]
    assert r._entries[1]["server"] == "socks5://proxy-b.example.com:1080"
    assert r._entries[2]["server"] == "https://proxy-c.example.com:3128"
    # Empty pool
    assert ProxyRotator([])._entries == []


def test_proxy_rotator_rotation_and_cooldown():
    import asyncio
    from backend.services.applier.stealth import ProxyRotator

    async def scenario():
        r = ProxyRotator(
            ["http://p1:8080", "http://p2:8080"],
            fail_threshold=2, cooldown_seconds=3600,
        )
        # Round-robin order
        first, second, third = await r.next(), await r.next(), await r.next()
        assert first["server"] != second["server"]
        assert third["server"] == first["server"]
        # Two failures on p1 -> cooldown, p1 is skipped
        await r.report_failure("http://p1:8080")
        await r.report_failure("http://p1:8080")
        seen = {(await r.next())["server"] for _ in range(3)}
        assert seen == {"http://p2:8080"}
        # Success resets the counter but cooldown is still active
        await r.report_success("http://p1:8080")
        assert (await r.next())["server"] == "http://p2:8080"
        # All proxies on cooldown -> None (caller falls back to direct)
        await r.report_failure("http://p2:8080")
        await r.report_failure("http://p2:8080")
        assert await r.next() is None
        return True

    assert asyncio.run(scenario())


def test_build_context_options_proxy_and_localhost():
    import asyncio
    from unittest.mock import patch
    from backend.config import settings
    from backend.services.applier import stealth

    with patch.object(settings, "apply_proxy_urls", ["http://user:pass@p.example.com:8080"]):
        # Localhost is never proxied
        opts = stealth.build_context_options("127.0.0.1:8734", {"server": "http://p.example.com:8080"})
        assert "proxy" not in opts
        # Real host gets the parsed proxy (creds split out)
        opts = stealth.build_context_options("jobs.acme.com", {"server": "http://p.example.com:8080", "username": "user", "password": "pass"})
        assert opts["proxy"]["server"] == "http://p.example.com:8080"
        assert opts["proxy"]["username"] == "user"
        assert opts["proxy"]["password"] == "pass"
        # No proxy passed -> no proxy key
        opts = stealth.build_context_options("jobs.acme.com")
        assert "proxy" not in opts


def test_is_proxy_relevant_failure():
    from backend.services.applier.stealth import is_proxy_relevant_failure
    assert is_proxy_relevant_failure("blocked by site (HTTP 403)")
    assert is_proxy_relevant_failure("navigation failed: net::ERR_PROXY_CONNECTION_FAILED")
    assert is_proxy_relevant_failure("", http_blocked=[403])
    assert not is_proxy_relevant_failure("no visible form fields found")
    assert not is_proxy_relevant_failure("validation error: please fix email")


def test_hyperbrowser_session_creation_payload():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from backend.config import settings
    from backend.services.applier import stealth

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "sess-1", "wsEndpoint": "wss://browser.hyperbrowser.ai/x", "liveUrl": "https://live/x"}

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=FakeResp())

    class FakeACMQ:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return fake_client

        async def __aexit__(self, *a):
            return False

    with patch.object(settings, "hyperbrowser_api_key", "test-key"), \
         patch("httpx.AsyncClient", FakeACMQ):
        result = asyncio.run(stealth.create_hyperbrowser_session())

    assert result["id"] == "sess-1"
    assert result["ws_endpoint"] == "wss://browser.hyperbrowser.ai/x"
    assert result["live_url"] == "https://live/x"
    call = fake_client.post.call_args
    assert call.args[0] == f"{stealth.HYPERBROWSER_API_BASE}/session"
    assert call.kwargs["headers"]["x-api-key"] == "test-key"
    body = call.kwargs["json"]
    assert body["useStealth"] is True
    assert body["acceptCookies"] is True
    # useProxy is opt-in (free plan rejects it with 402), so by default it
    # must NOT be in the payload.
    assert "useProxy" not in body

    # With the env flag on, useProxy is included.
    with patch.object(stealth, "HYPERBROWSER_USE_PROXY", True), \
         patch.object(settings, "hyperbrowser_api_key", "test-key"), \
         patch("httpx.AsyncClient", FakeACMQ):
        fake_client.post.reset_mock()
        result2 = asyncio.run(stealth.create_hyperbrowser_session())
    assert result2["id"] == "sess-1"
    body2 = fake_client.post.call_args.kwargs["json"]
    assert body2["useProxy"] is True
    assert body["timeoutMinutes"] == stealth.HYPERBROWSER_TIMEOUT_MINUTES


def test_hyperbrowser_session_creation_falls_back_gracefully():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from backend.config import settings
    from backend.services.applier import stealth

    class FakeResp:
        status_code = 402
        text = "no credits"

    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=FakeResp())

    class FakeACMQ:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return fake_client

        async def __aexit__(self, *a):
            return False

    with patch.object(settings, "hyperbrowser_api_key", "test-key"), \
         patch("httpx.AsyncClient", FakeACMQ):
        assert asyncio.run(stealth.create_hyperbrowser_session()) is None


def test_hyperbrowser_stop_session():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from backend.config import settings
    from backend.services.applier import stealth

    class FakeResp:
        status_code = 204

    fake_client = AsyncMock()
    fake_client.put = AsyncMock(return_value=FakeResp())

    class FakeACMQ:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return fake_client

        async def __aexit__(self, *a):
            return False

    with patch.object(settings, "hyperbrowser_api_key", "test-key"), \
         patch("httpx.AsyncClient", FakeACMQ):
        ok = asyncio.run(stealth.stop_hyperbrowser_session("sess-1"))
    assert ok is True
    call = fake_client.put.call_args
    assert call.args[0] == f"{stealth.HYPERBROWSER_API_BASE}/session/sess-1/stop"
    assert call.kwargs["headers"]["x-api-key"] == "test-key"
    # Empty session id -> no request, no crash
    assert asyncio.run(stealth.stop_hyperbrowser_session(None)) is False


def main():
    tests = [
        test_ats_detector,
        test_question_classifier,
        test_apply_context_answer_matching,
        test_generic_field_kind,
        test_generic_question_text_fallbacks,
        test_generic_consent_label_tokens,
        test_generic_honeypot_is_never_filled_or_counted,
        test_generic_profile_hints_without_llm,
        test_generic_to_iso_date,
        test_generic_decide_escalates_unknown_required,
        test_generic_decide_escalates_select_with_options,
        test_generic_decide_fills_from_profile_and_answer_bank,
        test_generic_decide_skips_optional_and_controls,
        test_tiered_context_build,
        test_tiered_registers_tier1_adapters,
        test_proxy_rotator_parsing,
        test_proxy_rotator_rotation_and_cooldown,
        test_build_context_options_proxy_and_localhost,
        test_is_proxy_relevant_failure,
        test_hyperbrowser_session_creation_payload,
        test_hyperbrowser_session_creation_falls_back_gracefully,
        test_hyperbrowser_stop_session,
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