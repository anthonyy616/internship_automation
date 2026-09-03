"""
TelegramBot — long-polling service for Category-A question escalation.

Flow:
    1. A confirmation is created (applier paused, application is
       paused_awaiting_input) with telegram_message_id = NULL.
    2. The poller's flush step sends each unsent confirmation to
       TELEGRAM_CHAT_ID. Multiple-choice questions get inline answer
       buttons; every message also says "or reply with your answer".
    3. The user answers by tapping an inline button (callback query) or
       by replying to the message with free text.
    4. The answer is saved to profile_answers, the confirmation is
       marked answered, and the paused application is resumed
       (job -> queued, immediate enqueue when Redis is reachable).

Run standalone (no web server needed):
    python -m backend.services.telegram.bot [--once] [--send-test]
"""

import asyncio
import logging
import sys
from typing import Callable, List, Optional

import httpx

from backend.config import settings

# Windows: use the Selector event loop (see backend/app.py for why) so
# httpx / redis connections don't hit the Proactor connect/cancel race.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

# Truncation guard: callback_data is limited to 64 bytes by Telegram.
MAX_CALLBACK_BYTES = 50


class TelegramBot:
    """Poll the Bot API and drive the confirmation flow."""

    def __init__(
        self,
        token: Optional[str] = None,
        chat_id: Optional[str] = None,
        repo=None,
        logger_=None,
        http: Optional[httpx.AsyncClient] = None,
        on_answer: Optional[Callable] = None,
    ):
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.repo = repo
        self.log = logger_
        self._offset: Optional[int] = None
        self._http = http  # injectable for tests; built lazily otherwise
        # on_answer(confirmation_id, answer) -> resumes the application
        self.on_answer = on_answer or self._default_on_answer

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30)
        return self._http

    async def _post(self, method: str, **payload) -> Optional[dict]:
        if not self.enabled():
            return None
        client = await self._client()
        url = f"{API_BASE}/bot{self.token}/{method}"
        try:
            response = await client.post(url, json=payload)
            data = response.json()
            if not data.get("ok"):
                logger.warning("telegram %s failed: %s", method, data.get("description"))
                return None
            return data.get("result")
        except Exception as e:
            logger.warning("telegram %s error: %s", method, e)
            return None

    async def _send(self, chat_id: str, text: str, reply_markup: Optional[dict] = None) -> Optional[int]:
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._post("sendMessage", **payload)
        if result:
            return result.get("message_id")
        return None

    # ------------------------------------------------------------------
    # Confirmation delivery
    # ------------------------------------------------------------------

    async def send_confirmation(self, conf) -> bool:
        """Send one confirmation question and record the Telegram message id."""
        if self.repo is None or not self.enabled():
            return False

        question = conf.question_text or "Answer the following question:"
        options = conf.options or []
        if options:
            # One inline button per option; callback carries conf id + index
            buttons = []
            for idx, option in enumerate(options):
                data = f"{conf.id}:{idx}"
                if len(data.encode()) > MAX_CALLBACK_BYTES:
                    continue
                buttons.append([{"text": str(option)[:60], "callback_data": data}])
            reply_markup = {"inline_keyboard": buttons}
            text = f"❓ {question}\n\n(Or reply to this message with your answer.)"
        else:
            reply_markup = None
            text = f"❓ {question}\n\nReply to this message with your answer."

        message_id = await self._send(str(self.chat_id), text, reply_markup)
        if message_id is None:
            return False

        try:
            await self.repo.update_confirmation_telegram_id(str(conf.id), str(message_id))
        except Exception as e:
            logger.warning("could not record telegram message id: %s", e)
        return True

    async def flush_unsent(self) -> int:
        """Deliver every pending confirmation that hasn't been sent yet."""
        if self.repo is None:
            return 0
        try:
            pending = await self.repo.get_unsent_pending_confirmations(limit=20)
        except Exception as e:
            logger.warning("flush_unsent: %s", e)
            return 0

        sent = 0
        for conf in pending:
            if await self.send_confirmation(conf):
                sent += 1
            await asyncio.sleep(0.3)  # gentle rate limit
        return sent

    # ------------------------------------------------------------------
    # Incoming updates
    # ------------------------------------------------------------------

    async def handle_update(self, update: dict) -> bool:
        """Process one update. Returns True if it answered a confirmation."""
        if not self.enabled() or self.repo is None:
            return False

        # Inline button answer: callback_data = "<conf_id>:<option_index>"
        callback = update.get("callback_query")
        if callback:
            data = (callback.get("data") or "").split(":")
            if len(data) == 2:
                conf_id, option_idx = data[0], data[1]
                conf = await self.repo.get_confirmation(conf_id)
                if conf is not None and conf.status == "pending":
                    options = conf.options or []
                    try:
                        answer = str(options[int(option_idx)])
                    except (ValueError, IndexError):
                        answer = option_idx
                    await self._answer(conf, answer)
                    await self._post("answerCallbackQuery", callback_query_id=callback.get("id"))
                    return True
            return False

        # Text reply: user replied to one of our question messages
        message = update.get("message") or {}
        reply_to = message.get("reply_to_message") or {}
        text = (message.get("text") or "").strip()
        reply_id = reply_to.get("message_id")
        if text and reply_id is not None:
            conf = await self.repo.get_confirmation_by_telegram_message_id(str(reply_id))
            if conf is not None and conf.status == "pending":
                await self._answer(conf, text)
                return True

        return False

    async def _answer(self, conf, answer: str):
        """Save the answer and resume the paused application."""
        try:
            await self.repo.answer_confirmation(str(conf.id), answer)
        except Exception as e:
            logger.warning("answer_confirmation failed: %s", e)
            return

        # Notify the chat that the answer was recorded
        await self._send(
            str(self.chat_id),
            f"✅ Answer saved: {answer}\nResuming the application…",
        )

        try:
            await self.on_answer(str(conf.id))
        except Exception as e:
            logger.warning("resume failed for confirmation %s: %s", conf.id, e)

        if self.log is not None:
            try:
                await self.log.success(
                    "system", "telegram_answered",
                    metadata={"confirmation_id": str(conf.id), "answer": answer},
                )
            except Exception:
                pass

    async def _default_on_answer(self, confirmation_id: str):
        """Resume via the orchestrator helper (job -> queued + enqueue)."""
        from backend.services.orchestrator import resume_paused_application

        if self.repo is None:
            return
        conf = await self.repo.get_confirmation(confirmation_id)
        if conf is None or not conf.application_id:
            return
        app = await self.repo.get_application(str(conf.application_id))
        if app is None or not app.job_id:
            return
        await resume_paused_application(str(app.job_id))

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def poll_once(self) -> int:
        """Fetch and process one batch of updates. Returns count handled."""
        if not self.enabled():
            return 0
        payload = {"timeout": 5, "allowed_updates": ["message", "callback_query"]}
        if self._offset is not None:
            payload["offset"] = self._offset
        result = await self._post("getUpdates", **payload)
        if not result:
            return 0

        handled = 0
        for update in result:
            update_id = update.get("update_id")
            if update_id is not None:
                self._offset = update_id + 1
            try:
                if await self.handle_update(update):
                    handled += 1
            except Exception as e:
                logger.warning("update handling error: %s", e)
        return handled

    async def run(self) -> None:
        """Infinite polling loop. Runs until cancelled."""
        if not self.enabled():
            logger.warning(
                "Telegram bot disabled — set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID in .env"
            )
            return

        if self.log is not None:
            try:
                await self.log.success(
                    "system", "telegram_bot_started",
                    metadata={"chat_id": str(self.chat_id)[:12]},
                )
            except Exception:
                pass

        while True:
            try:
                await self.flush_unsent()
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("poll cycle error: %s", e)
                await asyncio.sleep(5)
            await asyncio.sleep(1)


# ----------------------------------------------------------------------
# Standalone entry point: python -m backend.services.telegram.bot
# ----------------------------------------------------------------------

async def _amain(args: List[str]) -> None:
    from backend.database import init_db, close_db
    from backend.services.event_logger import EventLogger
    from backend.websocket_manager import ws_manager

    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print("ERROR: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env first.")
        return

    repo = await init_db()
    bot = TelegramBot(repo=repo, logger_=EventLogger(repo, ws_manager))

    if "--send-test" in args:
        ok = await bot._send(str(bot.chat_id), "🤖 Internship bot is online. Telegram escalation is wired up.")
        print("test message sent" if ok else "test message FAILED (check token/chat id)")
    elif "--once" in args:
        sent = await bot.flush_unsent()
        handled = await bot.poll_once()
        print(f"flush sent: {sent}, updates handled: {handled}")
    else:
        print(f"Polling for questions in chat {settings.telegram_chat_id}… Ctrl+C to stop.")
        await bot.run()

    await close_db()


def main():
    import sys
    asyncio.run(_amain(sys.argv[1:]))


if __name__ == "__main__":
    main()