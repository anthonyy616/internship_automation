-- =============================================================================
-- Internship Automation Bot v2 — Neon Schema
-- =============================================================================
-- Run: python -m backend.db.migrate
-- Or execute this file directly against your Neon database.
-- =============================================================================

-- Extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- =============================================================================
-- JOBS — Discovered internship opportunities
-- =============================================================================
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source TEXT NOT NULL,                    -- 'remotive', 'arbeitnow', 'hackernews', etc.
    external_id TEXT,                        -- source-specific ID for dedup
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    region TEXT NOT NULL,                    -- 'EU', 'UK', 'Nigeria', 'Turkiye'
    url TEXT NOT NULL UNIQUE,
    description TEXT,
    status TEXT DEFAULT 'discovered',        -- discovered|filtered|queued|applied|emailed|failed|failed_needs_manual
    discovered_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_region ON jobs(region);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_external ON jobs(source, external_id) WHERE external_id IS NOT NULL;

-- =============================================================================
-- APPLICATIONS — Tracks each application attempt
-- =============================================================================
CREATE TABLE IF NOT EXISTS applications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'queued',            -- queued|filling|paused_awaiting_input|applied|failed
    applied_via TEXT,                        -- 'form' | 'email'
    ats_platform TEXT,                       -- 'greenhouse', 'lever', 'workday', 'ashby', 'unknown'
    resume_version TEXT,
    filled_fields JSONB DEFAULT '{}',        -- what's been filled (for restart-and-refill)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_applications_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);

-- =============================================================================
-- AGENT EVENTS — Structured, replayable action log (core of observability)
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id UUID REFERENCES applications(id) ON DELETE SET NULL,
    stage TEXT NOT NULL,                     -- 'scrape', 'filter', 'apply', 'email', 'system'
    action TEXT NOT NULL,                    -- 'found_jobs', 'filled_field', 'submitted', 'sent_email', etc.
    target_url TEXT,
    status TEXT NOT NULL,                    -- 'started' | 'success' | 'failed' | 'escalated'
    screenshot_url TEXT,
    duration_ms INTEGER,
    error_text TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_application ON agent_events(application_id);
CREATE INDEX IF NOT EXISTS idx_events_stage ON agent_events(stage);
CREATE INDEX IF NOT EXISTS idx_events_created ON agent_events(created_at DESC);

-- =============================================================================
-- PROFILE ANSWERS — Semantic Q&A bank (pgvector for similarity search)
-- =============================================================================
CREATE TABLE IF NOT EXISTS profile_answers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_text TEXT NOT NULL,
    question_embedding VECTOR(1536),         -- OpenAI embedding dimension
    answer_text TEXT NOT NULL,
    category TEXT DEFAULT 'B',              -- 'A' (fact only you can supply) | 'B' (generatable)
    times_used INTEGER DEFAULT 0,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_answers_category ON profile_answers(category);

-- =============================================================================
-- PENDING CONFIRMATIONS — Telegram escalation for Category-A questions
-- =============================================================================
CREATE TABLE IF NOT EXISTS pending_confirmations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id UUID REFERENCES applications(id) ON DELETE CASCADE,
    question_text TEXT NOT NULL,
    field_type TEXT,                        -- 'text', 'select', 'radio', 'checkbox'
    options JSONB,                          -- for select/radio: ["option1", "option2"]
    status TEXT DEFAULT 'pending',          -- 'pending' | 'answered' | 'timed_out'
    telegram_message_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    answered_at TIMESTAMPTZ
);

-- =============================================================================
-- EMAILS — Sent cold emails with self-check and delivery tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS emails (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    application_id UUID REFERENCES applications(id) ON DELETE SET NULL,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    self_check_status TEXT,                 -- 'passed' | 'failed' | 'skipped'
    self_check_notes TEXT,
    sent_at TIMESTAMPTZ,
    bounced_at TIMESTAMPTZ,
    replied_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- SOURCES — Adapter health tracking
-- =============================================================================
CREATE TABLE IF NOT EXISTS sources (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,                     -- 'api' | 'scrape'
    enabled BOOLEAN DEFAULT true,
    base_url TEXT,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    error_count INTEGER DEFAULT 0,
    consecutive_failures INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================================================
-- CONFIG — Key-value config store (profile, keywords, regions, limits, etc.)
-- =============================================================================
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed default config rows
INSERT INTO config (key, value) VALUES
    ('profile', '{"name": "", "email": "", "university": "", "major": "", "skills": [], "portfolio_url": ""}'),
    ('keywords', '{"keywords": ["Software Engineer Intern", "ML Intern", "AI Intern", "Backend Intern", "Data Engineer Intern"]}'),
    ('regions', '{"enabled": ["EU", "UK"]}'),
    ('limits', '{"max_applications_per_day": 50, "max_emails_per_day": 50, "min_delay_seconds": 5, "max_delay_seconds": 15}'),
    ('blocklist', '{"companies": [], "domains": []}'),
    ('email', '{"daily_cap": 50, "per_domain_cap": 3, "warmup_day": 1, "warmup_increment": 5, "kill_switch_bounce_threshold": 15}'),
    ('sources_config', '{"remotive": true, "arbeitnow": true, "hackernews": true, "jobicy": true, "jobberman": true, "myjobmag": true, "eleman": true, "prospects": true, "milkround": true}')
ON CONFLICT (key) DO NOTHING;

-- Seed the sources table so adapter health tracking has rows
INSERT INTO sources (name, type, base_url) VALUES
    ('remotive', 'api', 'https://remotive.com'),
    ('arbeitnow', 'api', 'https://www.arbeitnow.com'),
    ('hackernews', 'api', 'https://news.ycombinator.com'),
    ('jobicy', 'api', 'https://jobicy.com'),
    ('jobberman', 'scrape', 'https://www.jobberman.com'),
    ('myjobmag', 'scrape', 'https://www.myjobmag.com'),
    ('eleman', 'scrape', 'https://www.eleman.net'),
    ('prospects', 'api', 'https://www.prospects.ac.uk'),
    ('milkround', 'scrape', 'https://www.milkround.com')
ON CONFLICT (name) DO NOTHING;
