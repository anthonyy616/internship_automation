"""
Offline tests for the Telegram escalation bot (no network, no DB).

The Telegram API is mocked with an httpx MockTransport; the repository is
a fake. Covers confirmation delivery (inline keyboards), inline-button
answers, reply-to-text answers, and resume triggering.

Usage:
    python -m tests.test_telegram
"""

import asyncio
import json
import sys

import httpx

from backend.config import settings
from backend.services.telegram.bot import TelegramBot


class FakeConf:
    def __init__(self, id="conf-1", application_id="app-1", question_text="Do you require visa sponsorship?",
                 options=None, telegram_message_id=None, status="pending"):
        self.id = id
        self.application_id = application_id
        self.question_text = question_text
        self.options = options
        self.telegram_message_id = telegram_message_id
        self.status = status


class FakeApp:
    def __init__(self, id="app-1", job_id="job-1"):
        self.id = id
        self.job_id = job_id


class FakeRepo:
    def __init__(self, confirmations=None):
        self.confirmations = {c.id: c for c in (confirmations or [])}
        self.applications = {"app-1": FakeApp()}
        self.answered = []          # (conf_id, answer)
        self.updated_telegram_ids = {}

    async def get_unsent_pending_confirmations(self, limit=20):
        return [c for c in self.confirmations.values()
                if c.status == "pending" and c.telegram_message_id is None][:limit]

    async def update_confirmation_telegram_id(self, conf_id, message_id):
        if conf_id in self.confirmations:
            self.confirmations[conf_id].telegram_message_id = message_id
        self.updated_telegram_ids[conf_id] = message_id
        return True

    async def get_confirmation(self, conf_id):
        return self.confirmations.get(conf_id)

    async def get_confirmation_by_telegram_message_id(self, message_id):
        for c in self.confirmations.values():
            if c.telegram_message_id == message_id:
                return c
        return None

    async def answer_confirmation(self, conf_id, answer):
        conf = self.confirmations.get(conf_id)
        if conf is None:
            return False
        conf.status = "answered"
        self.answered.append((conf_id, answer))
        return True

    async def get_application(self, application_id):
        return self.applications.get(application_id)


class FakeTransport:
    """Records outbound Telegram calls; returns ok responses."""

    def __init__(self):
        self.calls = []  # (method_name, payload)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        result = {"message_id": len(self.calls) + 100}
        return httpx.Response(200, json={"ok": True, "result": result})


def _make_bot(repo, transport, on_answer=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(transport.handler))
    return TelegramBot(
        token="TESTTOKEN", chat_id="12345", repo=repo,
        http=client, on_answer=on_answer or (lambda cid: asyncio.sleep(0)),
    )


async def test_disabled_without_credentials():
    # Monkeypatch the settings singleton so the test is hermetic
    # regardless of what .env contains.
    saved = (settings.telegram_bot_token, settings.telegram_chat_id)
    settings.telegram_bot_token = ""
    settings.telegram_chat_id = ""
    try:
        bot = TelegramBot(repo=FakeRepo())
        assert bot.enabled() is False
        await bot.poll_once()
    finally:
        settings.telegram_bot_token, settings.telegram_chat_id = saved


async def test_send_confirmation_records_message_id():
    repo = FakeRepo([FakeConf()])
    transport = FakeTransport()
    bot = _make_bot(repo, transport)

    ok = await bot.send_confirmation(repo.confirmations["conf-1"])
    assert ok is True

    method, payload = transport.calls[0]
    assert method == "sendMessage"
    assert payload["chat_id"] == "12345"
    assert "visa sponsorship" in payload["text"]
    # No options -> no inline keyboard, instructs to reply
    assert "reply_to this message" in payload["text"].lower() or "reply to this message" in payload["text"].lower()
    assert repo.updated_telegram_ids.get("conf-1") == "101"


async def test_send_confirmation_with_options_builds_inline_keyboard():
    repo = FakeRepo([FakeConf(options=["Yes", "No"])])
    transport = FakeTransport()
    bot = _make_bot(repo, transport)

    await bot.send_confirmation(repo.confirmations["conf-1"])

    _, payload = transport.calls[0]
    keyboard = payload["reply_markup"]["inline_keyboard"]
    assert keyboard[0][0]["text"] == "Yes"
    assert keyboard[1][0]["text"] == "No"
    # callback carries conf id + option index
    assert keyboard[0][0]["callback_data"].startswith("conf-1:0")


async def test_flush_unsent_only_sends_unanswered():
    repo = FakeRepo([
        FakeConf(id="c1", telegram_message_id=None),      # send
        FakeConf(id="c2", telegram_message_id="999"),     # already sent — skip
        FakeConf(id="c3", telegram_message_id=None, status="answered"),  # not pending — skip
    ])
    transport = FakeTransport()
    bot = _make_bot(repo, transport)

    sent = await bot.flush_unsent()

    assert sent == 1
    assert len(transport.calls) == 1
    assert repo.updated_telegram_ids.get("c1") is not None


async def test_inline_button_answer_saves_and_resumes():
    repo = FakeRepo([FakeConf(options=["Yes, citizen", "No"])])
    transport = FakeTransport()
    resumed = []

    async def fake_on_answer(conf_id):
        resumed.append(conf_id)

    bot = _make_bot(repo, transport, on_answer=fake_on_answer)

    handled = await bot.handle_update({
        "callback_query": {"id": "cb1", "data": "conf-1:0"},
    })

    assert handled is True
    assert ("conf-1", "Yes, citizen") in repo.answered
    assert resumed == ["conf-1"]


async def test_reply_to_text_answer_saves_and_resumes():
    repo = FakeRepo([FakeConf(telegram_message_id="101")])
    transport = FakeTransport()
    resumed = []

    async def fake_on_answer(conf_id):
        resumed.append(conf_id)

    bot = _make_bot(repo, transport, on_answer=fake_on_answer)

    handled = await bot.handle_update({
        "message": {
            "message_id": 500,
            "text": "Yes, I am a citizen",
            "reply_to_message": {"message_id": 101},
        },
    })

    assert handled is True
    assert ("conf-1", "Yes, I am a citizen") in repo.answered
    assert resumed == ["conf-1"]


async def test_already_answered_confirmation_not_reanswered():
    repo = FakeRepo([FakeConf(status="answered", telegram_message_id="101")])
    transport = FakeTransport()
    bot = _make_bot(repo, transport)

    handled = await bot.handle_update({
        "message": {
            "message_id": 500,
            "text": "another answer",
            "reply_to_message": {"message_id": 101},
        },
    })

    assert handled is False
    assert repo.answered == []


def main():
    tests = [
        test_disabled_without_credentials,
        test_send_confirmation_records_message_id,
        test_send_confirmation_with_options_builds_inline_keyboard,
        test_flush_unsent_only_sends_unanswered,
        test_inline_button_answer_saves_and_resumes,
        test_reply_to_text_answer_saves_and_resumes,
        test_already_answered_confirmation_not_reanswered,
    ]
    failures = 0
    for test in tests:
        try:
            asyncio.run(test())
            print(f"  PASS  {test.__name__}")
        except Exception:
            failures += 1
            import traceback
            print(f"  FAIL  {test.__name__}:")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()