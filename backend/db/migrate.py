"""
Database migration script for Neon (PostgreSQL + pgvector).

Usage:
    python -m backend.db.migrate              # Run schema migration
    python -m backend.db.migrate --seed       # Run migration + seed data from config.yaml
    python -m backend.db.migrate --reset      # Drop and recreate all tables (DESTRUCTIVE)
"""

import os
import sys
import asyncio
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

import asyncpg

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def get_connection(dsn: str = None) -> asyncpg.Connection:
    """Get a connection to the Neon database."""
    dsn = dsn or os.getenv("NEON_DATABASE_URL")
    if not dsn:
        print("ERROR: NEON_DATABASE_URL not set in .env")
        sys.exit(1)
    return await asyncpg.connect(dsn)


async def run_schema(conn: asyncpg.Connection):
    """Execute the schema SQL file."""
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    await conn.execute(schema_sql)
    print("[+] Schema applied successfully.")


async def seed_from_config(conn: asyncpg.Connection):
    """Seed profile and keywords from config/config.yaml into the config table."""
    import yaml

    config_path = PROJECT_ROOT / "config" / "config.yaml"
    if not config_path.exists():
        print("[-] config/config.yaml not found — skipping seed.")
        return

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    # Seed profile
    user_profile = config.get("user_profile", {})
    if user_profile.get("name"):
        profile = {
            "name": user_profile.get("name", ""),
            "email": user_profile.get("email", ""),
            "university": user_profile.get("university", ""),
            "major": user_profile.get("major", ""),
            "skills": user_profile.get("skills", []),
            "portfolio_url": user_profile.get("portfolio_url", ""),
        }
        await conn.execute(
            """INSERT INTO config (key, value) VALUES ('profile', $1::jsonb)
               ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()""",
            __import__("json").dumps(profile),
        )
        print(f"[+] Seeded profile for: {profile['name']}")

    # Seed keywords
    search_criteria = config.get("search_criteria", {})
    keywords = search_criteria.get("keywords", [])
    if keywords:
        await conn.execute(
            """INSERT INTO config (key, value) VALUES ('keywords', $1::jsonb)
               ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()""",
            __import__("json").dumps({"keywords": keywords}),
        )
        print(f"[+] Seeded {len(keywords)} search keywords")

    # Seed user_answers from user_questions_template.yaml
    qa_path = PROJECT_ROOT / "config" / "user_questions_template.yaml"
    if qa_path.exists():
        with open(qa_path, "r", encoding="utf-8") as f:
            qa_data = yaml.safe_load(f) or {}

        count = 0
        for key, value in qa_data.items():
            if value and str(value).strip():
                # Check if already exists
                exists = await conn.fetchval(
                    "SELECT id FROM profile_answers WHERE question_text = $1", key
                )
                if not exists:
                    await conn.execute(
                        """INSERT INTO profile_answers (question_text, answer_text, category)
                           VALUES ($1, $2, $3)""",
                        key,
                        str(value),
                        "A" if key in (
                            "legally_authorized_to_work_in_us",
                            "require_visa_sponsorship_now_or_future",
                            "legally_authorized_to_work_in_eu",
                            "require_visa_sponsorship_eu",
                            "gender",
                            "race_ethnicity",
                            "veteran_status",
                            "disability_status",
                            "salary_expectations",
                            "notice_period",
                        ) else "B",
                    )
                    count += 1
        print(f"[+] Seeded {count} profile answers")


async def seed_sources(conn: asyncpg.Connection):
    """
    Upsert the sources_config row and the sources health-tracking rows.
    Merges sources_config so user-disabled toggles are preserved.
    """
    import json

    default_sources = {
        "remotive": True, "arbeitnow": True, "hackernews": True, "jobicy": True,
        "jobberman": True, "myjobmag": True, "eleman": True, "prospects": True,
        "milkround": True,
    }

    existing = await conn.fetchval(
        "SELECT value FROM config WHERE key = 'sources_config'"
    )
    if existing:
        merged = dict(existing)
        for name, enabled in default_sources.items():
            merged.setdefault(name, enabled)
        value = merged
    else:
        value = default_sources

    await conn.execute(
        """INSERT INTO config (key, value) VALUES ('sources_config', $1::jsonb)
           ON CONFLICT (key) DO UPDATE SET value = $1::jsonb, updated_at = NOW()""",
        json.dumps(value),
    )
    print(f"[+] Seeded sources_config ({len(value)} sources)")

    source_rows = [
        ("remotive", "api", "https://remotive.com"),
        ("arbeitnow", "api", "https://www.arbeitnow.com"),
        ("hackernews", "api", "https://news.ycombinator.com"),
        ("jobicy", "api", "https://jobicy.com"),
        ("jobberman", "scrape", "https://www.jobberman.com"),
        ("myjobmag", "scrape", "https://www.myjobmag.com"),
        ("eleman", "scrape", "https://www.eleman.net"),
        ("prospects", "api", "https://www.prospects.ac.uk"),
        ("milkround", "scrape", "https://www.milkround.com"),
    ]
    for name, source_type, base_url in source_rows:
        await conn.execute(
            """INSERT INTO sources (name, type, base_url)
               VALUES ($1, $2, $3)
               ON CONFLICT (name) DO UPDATE SET type = $2, base_url = $3""",
            name, source_type, base_url,
        )
    print(f"[+] Seeded {len(source_rows)} source health rows")


async def reset_database(conn: asyncpg.Connection):
    """Drop and recreate all tables. DESTRUCTIVE — use only in development."""
    tables = [
        "pending_confirmations",
        "agent_events",
        "emails",
        "applications",
        "profile_answers",
        "jobs",
        "sources",
        "config",
    ]
    for table in tables:
        await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
    print("[!] All tables dropped.")
    await run_schema(conn)


async def verify_connection(conn: asyncpg.Connection):
    """Verify the database connection works."""
    version = await conn.fetchval("SELECT version()")
    print(f"[+] Connected to: {version[:60]}...")

    # Check extensions
    exts = await conn.fetch("SELECT extname FROM pg_extension WHERE extname IN ('uuid-ossp', 'vector')")
    installed = {row["extname"] for row in exts}
    if "uuid-ossp" not in installed:
        print("[-] WARNING: uuid-ossp extension not found")
    if "vector" not in installed:
        print("[-] WARNING: vector extension not found (needed for pgvector embeddings)")
    else:
        print("[+] pgvector extension found")


async def main():
    args = sys.argv[1:]
    reset = "--reset" in args
    seed = "--seed" in args or not reset  # seed by default unless resetting

    print("=" * 60)
    print("INTERNSHIP AUTOMATION BOT — DATABASE MIGRATION")
    print("=" * 60)

    conn = await get_connection()

    try:
        await verify_connection(conn)

        if reset:
            confirm = input("[!] This will DROP ALL TABLES. Type 'yes' to confirm: ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return
            await reset_database(conn)
        else:
            await run_schema(conn)

        if seed:
            await seed_from_config(conn)

        await seed_sources(conn)

        print("=" * 60)
        print("[+] Migration complete.")
        print("=" * 60)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
