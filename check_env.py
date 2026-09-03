"""
check_env.py — verify every .env credential live, without echoing secrets.

Usage:
    python check_env.py

Checks (each makes a real network call where possible):
  - REDIS_URL          -> TCP connect to the configured host:port
  - NEON_DATABASE_URL  -> asyncpg connect + SELECT 1
  - TELEGRAM_BOT_TOKEN -> GET /getMe (404/401 = invalid)
  - TELEGRAM_CHAT_ID   -> must be numeric; if the token works and the chat
                          id looks wrong, tries getUpdates to discover the
                          real one and prints it
  - OPENAI_API_KEY     -> GET /v1/models (401 = invalid)
  - SMTP_HOST/PORT     -> TCP connect (does NOT authenticate or send)
  - RESUME_PATH        -> file exists on disk
"""

import asyncio
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def tcp_check(host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} reachable"
    except Exception as e:
        return False, f"{host}:{port} -> {e}"


def http_json(url: str, headers: dict | None = None, timeout: float = 10.0) -> dict | None:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()[:160]}
    except Exception as e:
        return {"_error": str(e)}


def check_redis() -> tuple[bool, str]:
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    rest = url.split("://", 1)[-1]
    host = rest.split("@")[-1].split(":")[0]
    try:
        port = int(rest.split("@")[-1].split(":")[1].split("/")[0])
    except (IndexError, ValueError):
        port = 6379
    ok, msg = tcp_check(host, port)
    return ok, f"{msg} ({url.split('@')[-1]})"


def check_neon() -> tuple[bool, str]:
    url = os.getenv("NEON_DATABASE_URL", "")
    if not url:
        return False, "empty — get it from your Neon project dashboard"
    try:
        import asyncpg

        async def _probe():
            conn = await asyncpg.connect(url)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()

        asyncio.run(_probe())
        return True, "connected, SELECT 1 OK"
    except Exception as e:
        return False, f"connection failed: {e}"


def check_telegram() -> tuple[bool, str, str | None]:
    """Returns (token_ok, message, discovered_chat_id_or_None)."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token:
        return False, "token empty — create a bot via @BotFather", None

    data = http_json(f"https://api.telegram.org/bot{token}/getMe")
    if data is None or not data.get("ok"):
        code = data.get("_http_error") if data else None
        if code == 404:
            return False, "token invalid (404) — bot doesn't exist; create one via @BotFather", None
        return False, f"token invalid ({data})", None

    bot_name = data["result"].get("username", "?")
    discovered = None
    if not chat_id.isdigit():
        ups = http_json(
            f"https://api.telegram.org/bot{token}/getUpdates?timeout=2&limit=10"
        )
        if ups and ups.get("ok"):
            for u in ups.get("result", []):
                msg = u.get("message") or u.get("callback_query", {}).get("message") or {}
                cid = (msg.get("chat") or {}).get("id")
                if cid is not None:
                    discovered = str(cid)
                    break
        if discovered:
            return (
                True,
                f"token OK (@{bot_name}); chat id placeholder found — discovered real id: {discovered} (paste into .env)",
                discovered,
            )
        return (
            False,
            f"token OK (@{bot_name}) but chat id '{chat_id}' is not numeric — message the bot once, then re-run this",
            None,
        )
    return True, f"token OK (@{bot_name}), chat id numeric", None


def check_openai() -> tuple[bool, str]:
    key = os.getenv("OPENAI_API_KEY", "")
    if not key:
        return False, "empty — create a key at https://platform.openai.com/api-keys"
    data = http_json(
        "https://api.openai.com/v1/models?limit=1",
        headers={"Authorization": f"Bearer {key}"},
    )
    if data is None or "data" in data:
        return True, "valid key"
    code = data.get("_http_error")
    if code == 401:
        return False, "401 Unauthorized — invalid key; create a new one at https://platform.openai.com/api-keys"
    return False, f"error: {data}"


def check_smtp() -> tuple[bool, str]:
    host = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError:
        port = 587
    return tcp_check(host, port)


def main() -> None:
    print("=" * 62)
    print("ENV CHECK — every value verified live (no secrets printed)")
    print("=" * 62)

    checks = [
        ("REDIS", check_redis()),
        ("NEON DATABASE", check_neon()),
        ("TELEGRAM TOKEN", check_telegram()[:2]),
        ("OPENAI", check_openai()),
        ("SMTP", check_smtp()),
    ]

    resume = os.getenv("RESUME_PATH", "./data/resume.pdf")
    checks.append(("RESUME FILE", (Path(resume).exists(), f"{resume} exists" if Path(resume).exists() else f"{resume} MISSING")))

    all_ok = True
    for name, (ok, msg) in checks:
        print(f"  {'✅' if ok else '❌'} {name:<16} {msg}")
        all_ok = all_ok and ok

    if os.getenv("ADMIN_PASSWORD") == "":
        print("  ⚠️  ADMIN_PASSWORD empty — admin panel auth disabled (fine for local dev)")

    print("-" * 62)
    if all_ok:
        print("✅ Everything green — you can run the full flow.")
    else:
        print("❌ Fix the red entries above, then re-run this.")
    print("=" * 62)


if __name__ == "__main__":
    main()