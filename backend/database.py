"""
Database service for Neon (PostgreSQL + pgvector).
Provides asyncpg connection pool and typed Repository methods.
"""

import os
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

import asyncpg
from dotenv import load_dotenv

from backend.models import (
    Job, Application, AgentEvent, ProfileAnswer,
    PendingConfirmation, EmailRecord, Source,
)

load_dotenv()

NEON_DATABASE_URL = os.getenv("NEON_DATABASE_URL", "")


def _row(row) -> dict:
    """Convert an asyncpg row to a plain dict with UUIDs as strings."""
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in dict(row).items()}


class Repository:
    """Typed database repository with asyncpg."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    # =========================================================================
    # JOBS
    # =========================================================================

    async def upsert_job(
        self,
        source: str,
        external_id: Optional[str],
        title: str,
        company: str,
        region: str,
        url: str,
        description: str = "",
    ) -> Optional[Job]:
        """Insert a job, skip if URL already exists. Returns the job or None."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO jobs (source, external_id, title, company, region, url, description)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (url) DO NOTHING
                   RETURNING *""",
                source, external_id, title, company, region, url, description,
            )
            return Job(**_row(row)) if row else None

    async def get_job(self, job_id: str) -> Optional[Job]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
            return Job(**_row(row)) if row else None

    async def get_jobs(
        self,
        region: Optional[str] = None,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 100,
    ) -> List[Job]:
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM jobs WHERE 1=1"
            args: list = []
            idx = 1

            if region:
                query += f" AND region = ${idx}"
                args.append(region)
                idx += 1
            if status:
                query += f" AND status = ${idx}"
                args.append(status)
                idx += 1
            if source:
                query += f" AND source = ${idx}"
                args.append(source)
                idx += 1

            query += " ORDER BY discovered_at DESC"
            if limit:
                query += f" LIMIT {limit}"

            rows = await conn.fetch(query, *args)
            return [Job(**_row(r)) for r in rows]

    async def update_job_status(self, job_id: str, status: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE jobs SET status = $1, updated_at = NOW() WHERE id = $2",
                status, job_id,
            )
            return result == "UPDATE 1"

    async def job_exists(self, url: str) -> bool:
        async with self.pool.acquire() as conn:
            return await conn.fetchval("SELECT EXISTS(SELECT 1 FROM jobs WHERE url = $1)", url)

    async def get_job_urls(self) -> List[str]:
        """All known job URLs (for dedup during scraping)."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT url FROM jobs")
            return [r["url"] for r in rows]

    async def get_jobs_by_status(self, statuses: List[str], limit: int = 50) -> List[Job]:
        """Get jobs in one of the given states, oldest first."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM jobs WHERE status = ANY($1::text[]) "
                "ORDER BY discovered_at ASC LIMIT $2",
                statuses, limit,
            )
            return [Job(**_row(r)) for r in rows]

    async def get_job_counts(self) -> Dict[str, Any]:
        """Get job counts by region and status."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT region, status, COUNT(*) as cnt FROM jobs GROUP BY region, status")
            by_region: Dict[str, int] = {}
            by_status: Dict[str, int] = {}
            for row in rows:
                by_region[row["region"]] = by_region.get(row["region"], 0) + row["cnt"]
                by_status[row["status"]] = by_status.get(row["status"], 0) + row["cnt"]
            total = sum(by_status.values())
            return {
                "total_jobs": total,
                "jobs_by_region": by_region,
                "jobs_by_status": by_status,
            }

    # =========================================================================
    # APPLICATIONS
    # =========================================================================

    async def create_application(
        self,
        job_id: str,
        applied_via: Optional[str] = None,
        ats_platform: Optional[str] = None,
    ) -> Optional[Application]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO applications (job_id, applied_via, ats_platform)
                   VALUES ($1, $2, $3) RETURNING *""",
                job_id, applied_via, ats_platform,
            )
            return Application(**_row(row)) if row else None

    async def get_application(self, application_id: str) -> Optional[Application]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM applications WHERE id = $1", application_id)
            return Application(**_row(row)) if row else None

    async def update_application_status(self, application_id: str, status: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE applications SET status = $1, updated_at = NOW() WHERE id = $2",
                status, application_id,
            )
            return result == "UPDATE 1"

    async def save_filled_fields(self, application_id: str, fields: Dict[str, Any]) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE applications SET filled_fields = $1, updated_at = NOW() WHERE id = $2",
                json.dumps(fields), application_id,
            )
            return result == "UPDATE 1"

    async def get_application_by_job(self, job_id: str) -> Optional[Application]:
        """Most recent application for a job (prevents duplicate applications)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM applications WHERE job_id = $1 ORDER BY created_at DESC LIMIT 1",
                job_id,
            )
            return Application(**_row(row)) if row else None

    async def get_today_application_count(self) -> int:
        """Applications created today (for daily caps)."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM applications WHERE created_at > CURRENT_DATE"
            ) or 0

    async def get_today_email_count(self) -> int:
        """Emails sent today (for daily caps)."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM emails WHERE sent_at > CURRENT_DATE"
            ) or 0

    async def get_applications(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Application]:
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM applications WHERE 1=1"
            args: list = []
            idx = 1

            if job_id:
                query += f" AND job_id = ${idx}"
                args.append(job_id)
                idx += 1
            if status:
                query += f" AND status = ${idx}"
                args.append(status)
                idx += 1

            query += " ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit}"

            rows = await conn.fetch(query, *args)
            return [Application(**_row(r)) for r in rows]

    # =========================================================================
    # AGENT EVENTS
    # =========================================================================

    async def log_event(
        self,
        stage: str,
        action: str,
        status: str,
        application_id: Optional[str] = None,
        target_url: Optional[str] = None,
        screenshot_url: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentEvent]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO agent_events
                   (application_id, stage, action, target_url, status,
                    screenshot_url, duration_ms, error_text, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                   RETURNING *""",
                application_id, stage, action, target_url, status,
                screenshot_url, duration_ms, error_text,
                json.dumps(metadata or {}),
            )
            return AgentEvent(**_row(row)) if row else None

    async def get_events(
        self,
        application_id: Optional[str] = None,
        stage: Optional[str] = None,
        limit: int = 100,
    ) -> List[AgentEvent]:
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM agent_events WHERE 1=1"
            args: list = []
            idx = 1

            if application_id:
                query += f" AND application_id = ${idx}"
                args.append(application_id)
                idx += 1
            if stage:
                query += f" AND stage = ${idx}"
                args.append(stage)
                idx += 1

            query += " ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit}"

            rows = await conn.fetch(query, *args)
            return [AgentEvent(**_row(r)) for r in rows]

    async def get_application_timeline(self, application_id: str) -> List[AgentEvent]:
        """Get all events for a single application, in chronological order."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM agent_events WHERE application_id = $1 ORDER BY created_at ASC",
                application_id,
            )
            return [AgentEvent(**_row(r)) for r in rows]

    # =========================================================================
    # PROFILE ANSWERS
    # =========================================================================

    async def get_answer_by_key(self, key: str) -> Optional[ProfileAnswer]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM profile_answers WHERE question_text = $1", key
            )
            return ProfileAnswer(**_row(row)) if row else None

    async def search_answer_semantic(self, embedding: List[float], threshold: float = 0.90) -> Optional[ProfileAnswer]:
        """Find the closest profile answer by cosine similarity."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT *, 1 - (question_embedding <=> $1::vector) as similarity
                   FROM profile_answers
                   WHERE question_embedding IS NOT NULL
                   ORDER BY question_embedding <=> $1::vector
                   LIMIT 1""",
                str(embedding),
            )
            if row and row["similarity"] >= threshold:
                answer = ProfileAnswer(**_row(row))
                return answer
            return None

    async def save_answer(
        self,
        question_text: str,
        answer_text: str,
        category: str = "B",
        embedding: Optional[List[float]] = None,
    ) -> Optional[ProfileAnswer]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO profile_answers (question_text, answer_text, category, question_embedding)
                   VALUES ($1, $2, $3, $4::vector)
                   ON CONFLICT (question_text) DO UPDATE
                   SET answer_text = $2, category = $3, question_embedding = $4::vector
                   RETURNING *""",
                question_text, answer_text, category, str(embedding) if embedding else None,
            )
            return ProfileAnswer(**_row(row)) if row else None

    async def increment_answer_usage(self, answer_id: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE profile_answers
                   SET times_used = times_used + 1, last_used_at = NOW()
                   WHERE id = $1""",
                answer_id,
            )
            return result == "UPDATE 1"

    async def get_all_answers(self, category: Optional[str] = None) -> List[ProfileAnswer]:
        async with self.pool.acquire() as conn:
            if category:
                rows = await conn.fetch(
                    "SELECT * FROM profile_answers WHERE category = $1 ORDER BY times_used DESC", category
                )
            else:
                rows = await conn.fetch("SELECT * FROM profile_answers ORDER BY times_used DESC")
            return [ProfileAnswer(**_row(r)) for r in rows]

    # =========================================================================
    # PENDING CONFIRMATIONS
    # =========================================================================

    async def create_confirmation(
        self,
        application_id: str,
        question_text: str,
        field_type: Optional[str] = None,
        options: Optional[List[str]] = None,
        telegram_message_id: Optional[str] = None,
    ) -> Optional[PendingConfirmation]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO pending_confirmations
                   (application_id, question_text, field_type, options, telegram_message_id)
                   VALUES ($1, $2, $3, $4, $5) RETURNING *""",
                application_id, question_text, field_type,
                json.dumps(options) if options else None,
                telegram_message_id,
            )
            return PendingConfirmation(**_row(row)) if row else None

    async def get_confirmation(self, confirmation_id: str) -> Optional[PendingConfirmation]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_confirmations WHERE id = $1", confirmation_id
            )
            return PendingConfirmation(**_row(row)) if row else None

    async def answer_confirmation(self, confirmation_id: str, answer: str) -> bool:
        """Mark a confirmation as answered and save the answer."""
        async with self.pool.acquire() as conn:
            # Get the confirmation to find the question_text
            conf = await conn.fetchrow(
                "SELECT * FROM pending_confirmations WHERE id = $1", confirmation_id
            )
            if not conf:
                return False

            # Mark answered
            await conn.execute(
                """UPDATE pending_confirmations
                   SET status = 'answered', answered_at = NOW()
                   WHERE id = $1""",
                confirmation_id,
            )

            # Save answer to profile_answers for future reuse
            await conn.execute(
                """INSERT INTO profile_answers (question_text, answer_text, category)
                   VALUES ($1, $2, 'A')
                   ON CONFLICT (question_text) DO UPDATE
                   SET answer_text = $2, times_used = times_used + 1, last_used_at = NOW()""",
                conf["question_text"], answer,
            )
            return True

    async def get_pending_confirmations(self, limit: int = 50) -> List[PendingConfirmation]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM pending_confirmations
                   WHERE status = 'pending' ORDER BY created_at DESC LIMIT $1""",
                limit,
            )
            return [PendingConfirmation(**_row(r)) for r in rows]

    # =========================================================================
    # EMAILS
    # =========================================================================

    async def create_email(
        self,
        application_id: Optional[str],
        to_address: str,
        subject: str,
        body: str,
    ) -> Optional[EmailRecord]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO emails (application_id, to_address, subject, body)
                   VALUES ($1, $2, $3, $4) RETURNING *""",
                application_id, to_address, subject, body,
            )
            return EmailRecord(**_row(row)) if row else None

    async def update_email_status(
        self,
        email_id: str,
        self_check_status: Optional[str] = None,
        sent_at: Optional[datetime] = None,
        bounced_at: Optional[datetime] = None,
    ) -> bool:
        async with self.pool.acquire() as conn:
            sets = []
            args: list = []
            idx = 1

            if self_check_status is not None:
                sets.append(f"self_check_status = ${idx}")
                args.append(self_check_status)
                idx += 1
            if sent_at is not None:
                sets.append(f"sent_at = ${idx}")
                args.append(sent_at)
                idx += 1
            if bounced_at is not None:
                sets.append(f"bounced_at = ${idx}")
                args.append(bounced_at)
                idx += 1

            if not sets:
                return False

            args.append(email_id)
            result = await conn.execute(
                f"UPDATE emails SET {', '.join(sets)} WHERE id = ${idx}", *args
            )
            return result == "UPDATE 1"

    async def get_email_stats_last_hour(self) -> Dict[str, int]:
        """Get email send/bounce/fail counts for the last hour (for kill switch)."""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT
                     COUNT(*) as total,
                     COUNT(*) FILTER (WHERE sent_at IS NOT NULL AND bounced_at IS NULL) as sent,
                     COUNT(*) FILTER (WHERE bounced_at IS NOT NULL) as bounced,
                     COUNT(*) FILTER (WHERE self_check_status = 'failed') as failed
                   FROM emails
                   WHERE created_at > NOW() - INTERVAL '1 hour'"""
            )
            return dict(row) if row else {"total": 0, "sent": 0, "bounced": 0, "failed": 0}

    async def get_domain_send_count(self, domain: str) -> int:
        """How many emails sent to this domain today."""
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                """SELECT COUNT(*) FROM emails
                   WHERE to_address LIKE $1
                   AND sent_at IS NOT NULL
                   AND sent_at > CURRENT_DATE""",
                f"%@{domain}",
            )

    # =========================================================================
    # SOURCES
    # =========================================================================

    async def get_source(self, name: str) -> Optional[Source]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM sources WHERE name = $1", name)
            return Source(**_row(row)) if row else None

    async def upsert_source(self, name: str, source_type: str, base_url: str = "") -> Optional[Source]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO sources (name, type, base_url)
                   VALUES ($1, $2, $3)
                   ON CONFLICT (name) DO UPDATE SET type = $2, base_url = $3
                   RETURNING *""",
                name, source_type, base_url,
            )
            return Source(**_row(row)) if row else None

    async def record_source_success(self, source_name: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE sources
                   SET last_success_at = NOW(), error_count = 0, consecutive_failures = 0
                   WHERE name = $1""",
                source_name,
            )
            return result == "UPDATE 1"

    async def record_source_error(self, source_name: str) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE sources
                   SET last_error_at = NOW(), error_count = error_count + 1,
                       consecutive_failures = consecutive_failures + 1
                   WHERE name = $1""",
                source_name,
            )
            return result == "UPDATE 1"

    async def get_all_sources(self) -> List[Source]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM sources ORDER BY name")
            return [Source(**_row(r)) for r in rows]

    async def get_enabled_source_names(self) -> List[str]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT name FROM sources WHERE enabled = true ORDER BY name")
            return [r["name"] for r in rows]


# =============================================================================
# CONNECTION POOL + GLOBAL INSTANCE
# =============================================================================

_pool: Optional[asyncpg.Pool] = None
_repo: Optional[Repository] = None


async def init_db(dsn: str = None) -> Repository:
    """Initialize the connection pool and return the repository."""
    global _pool, _repo

    dsn = dsn or NEON_DATABASE_URL
    if not dsn:
        raise RuntimeError("NEON_DATABASE_URL not set. Copy .env.example to .env and fill in your Neon connection string.")

    _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    _repo = Repository(_pool)

    # Verify connection
    async with _pool.acquire() as conn:
        version = await conn.fetchval("SELECT version()")
        print(f"[+] Connected to: {version[:60]}...")

    return _repo


async def close_db():
    """Close the connection pool."""
    global _pool
    if _pool:
        await _pool.close()
        print("[+] Database connection pool closed.")


def get_repo() -> Repository:
    """Get the global repository instance. Call init_db() first."""
    if _repo is None:
        raise RuntimeError("Database not initialized. Call await init_db() first.")
    return _repo


def get_pool() -> asyncpg.Pool:
    """Get the global connection pool. Call init_db() first."""
    if _pool is None:
        raise RuntimeError("Database not initialized. Call await init_db() first.")
    return _pool
