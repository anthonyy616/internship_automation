"""
Application settings — env-driven configuration.

Secrets live in .env (gitignored); everything here reads from environment
variables with sensible defaults for local development.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent


def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, "true" if default else "false").strip().lower() != "false"


@dataclass
class Settings:
    # LLM
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o"))

    # Database
    neon_database_url: str = field(default_factory=lambda: os.getenv("NEON_DATABASE_URL", ""))

    # Redis / task queue
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379"))

    # Email (SMTP)
    smtp_user: str = field(default_factory=lambda: os.getenv("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.getenv("SMTP_PASSWORD", ""))
    smtp_server: str = field(default_factory=lambda: os.getenv("SMTP_SERVER", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(os.getenv("SMTP_PORT", "587")))

    # Telegram
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # Browser automation
    browser_headless: bool = field(default_factory=lambda: _env_bool("BROWSER_HEADLESS", True))
    resume_path: str = field(default_factory=lambda: os.getenv("RESUME_PATH", "./data/resume.pdf"))
    screenshot_dir: str = field(default_factory=lambda: os.getenv("SCREENSHOT_DIR", "./data/screenshots"))

    # Admin
    admin_password: str = field(default_factory=lambda: os.getenv("ADMIN_PASSWORD", ""))
    admin_totp_secret: str = field(default_factory=lambda: os.getenv("ADMIN_TOTP_SECRET", ""))

    # Deployment
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    app_host: str = field(default_factory=lambda: os.getenv("APP_HOST", "0.0.0.0"))
    app_port: int = field(default_factory=lambda: int(os.getenv("APP_PORT", "8000")))


settings = Settings()