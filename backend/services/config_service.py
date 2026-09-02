"""
Config Service — DB-backed configuration management.

All configuration (profile, keywords, regions, limits, blocklist, email settings,
source toggles) lives in the `config` table as JSONB. This service provides
typed accessors and update methods.
"""

import json
from typing import List, Optional, Dict, Any

import asyncpg

from backend.database import get_pool


class ProfileConfig:
    """User profile configuration."""
    name: str = ""
    email: str = ""
    university: str = ""
    major: str = ""
    skills: List[str] = []
    portfolio_url: str = ""

    def __init__(self, data: Dict[str, Any]):
        self.name = data.get("name", "")
        self.email = data.get("email", "")
        self.university = data.get("university", "")
        self.major = data.get("major", "")
        self.skills = data.get("skills", [])
        self.portfolio_url = data.get("portfolio_url", "")


class LimitsConfig:
    """Daily limits and timing."""
    max_applications_per_day: int = 50
    max_emails_per_day: int = 50
    min_delay_seconds: int = 5
    max_delay_seconds: int = 15

    def __init__(self, data: Dict[str, Any]):
        self.max_applications_per_day = data.get("max_applications_per_day", 50)
        self.max_emails_per_day = data.get("max_emails_per_day", 50)
        self.min_delay_seconds = data.get("min_delay_seconds", 5)
        self.max_delay_seconds = data.get("max_delay_seconds", 15)


class EmailConfig:
    """Email sending configuration."""
    daily_cap: int = 50
    per_domain_cap: int = 3
    warmup_day: int = 1
    warmup_increment: int = 5
    kill_switch_bounce_threshold: int = 15

    def __init__(self, data: Dict[str, Any]):
        self.daily_cap = data.get("daily_cap", 50)
        self.per_domain_cap = data.get("per_domain_cap", 3)
        self.warmup_day = data.get("warmup_day", 1)
        self.warmup_increment = data.get("warmup_increment", 5)
        self.kill_switch_bounce_threshold = data.get("kill_switch_bounce_threshold", 15)

    @property
    def effective_daily_cap(self) -> int:
        """Calculate today's cap based on warm-up schedule."""
        return min(self.daily_cap, self.warmup_day * self.warmup_increment)


class BlocklistConfig:
    """Companies and domains to skip."""
    companies: List[str] = []
    domains: List[str] = []

    def __init__(self, data: Dict[str, Any]):
        self.companies = data.get("companies", [])
        self.domains = data.get("domains", [])

    def is_blocked(self, company: str = "", email: str = "") -> bool:
        """Check if a company or email domain is blocked."""
        company_lower = company.lower()
        for blocked in self.companies:
            if blocked.lower() in company_lower or company_lower in blocked.lower():
                return True

        if email and "@" in email:
            domain = email.split("@")[-1].lower()
            for blocked_domain in self.domains:
                if blocked_domain.lower() == domain:
                    return True

        return False


class ConfigService:
    """
    Typed config service backed by the `config` table.

    Usage:
        config = ConfigService()
        profile = await config.get_profile()
        keywords = await config.get_keywords()
        await config.update("limits", {"max_applications_per_day": 100})
    """

    async def _get(self, key: str) -> Optional[Dict[str, Any]]:
        """Raw fetch of a config value."""
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM config WHERE key = $1", key)
            if row:
                val = row["value"]
                # asyncpg returns pg JSON as dict already
                return val if isinstance(val, dict) else json.loads(val)
            return None

    async def _set(self, key: str, value: Dict[str, Any]):
        """Raw upsert of a config value."""
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO config (key, value, updated_at)
                   VALUES ($1, $2::jsonb, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET value = $2::jsonb, updated_at = NOW()""",
                key, json.dumps(value),
            )

    # =========================================================================
    # Typed accessors
    # =========================================================================

    async def get_profile(self) -> ProfileConfig:
        data = await self._get("profile") or {}
        return ProfileConfig(data)

    async def get_keywords(self) -> List[str]:
        data = await self._get("keywords") or {}
        return data.get("keywords", [])

    async def get_regions(self) -> List[str]:
        data = await self._get("regions") or {}
        return data.get("enabled", [])

    async def get_limits(self) -> LimitsConfig:
        data = await self._get("limits") or {}
        return LimitsConfig(data)

    async def get_email_config(self) -> EmailConfig:
        data = await self._get("email") or {}
        return EmailConfig(data)

    async def get_blocklist(self) -> BlocklistConfig:
        data = await self._get("blocklist") or {}
        return BlocklistConfig(data)

    async def get_sources_config(self) -> Dict[str, bool]:
        data = await self._get("sources_config") or {}
        return data

    # =========================================================================
    # Update methods
    # =========================================================================

    async def update_profile(self, **kwargs):
        """Update profile fields. Pass key=value pairs."""
        current = await self._get("profile") or {}
        current.update(kwargs)
        await self._set("profile", current)

    async def update_keywords(self, keywords: List[str]):
        await self._set("keywords", {"keywords": keywords})

    async def update_regions(self, regions: List[str]):
        await self._set("regions", {"enabled": regions})

    async def update_limits(self, **kwargs):
        current = await self._get("limits") or {}
        current.update(kwargs)
        await self._set("limits", current)

    async def update_email_config(self, **kwargs):
        current = await self._get("email") or {}
        current.update(kwargs)
        await self._set("email", current)

    async def update_blocklist(self, companies: Optional[List[str]] = None, domains: Optional[List[str]] = None):
        current = await self._get("blocklist") or {}
        if companies is not None:
            current["companies"] = companies
        if domains is not None:
            current["domains"] = domains
        await self._set("blocklist", current)

    async def add_to_blocklist_company(self, company: str):
        current = await self._get("blocklist") or {}
        companies = current.get("companies", [])
        if company not in companies:
            companies.append(company)
            current["companies"] = companies
            await self._set("blocklist", current)

    async def remove_from_blocklist_company(self, company: str):
        current = await self._get("blocklist") or {}
        companies = current.get("companies", [])
        if company in companies:
            companies.remove(company)
            current["companies"] = companies
            await self._set("blocklist", current)

    async def update_sources_config(self, **kwargs):
        current = await self._get("sources_config") or {}
        current.update(kwargs)
        await self._set("sources_config", current)

    # =========================================================================
    # Generic update
    # =========================================================================

    async def update(self, key: str, value: Dict[str, Any]):
        """Generic update — set any config key."""
        await self._set(key, value)

    async def get_raw(self, key: str) -> Optional[Dict[str, Any]]:
        """Generic read — get any config key."""
        return await self._get(key)
