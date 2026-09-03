# Internship Automation Bot

An AI-powered autonomous agent system that discovers internship and entry-level opportunities, automatically applies via web forms, and sends personalized cold emails — all while you focus on interview prep.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/Neon-PostgreSQL-00E599?style=for-the-badge&logo=postgresql&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Automation-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)

---

## Features

- **Multi-Region Discovery** — EU, UK, Nigeria, Türkiye + global remote sources
- **Tiered Auto-Apply** — Known ATS platforms (Greenhouse, Lever, Workday, Ashby) with templated selectors; LLM-assisted fallback for unknown forms
- **Cold Email Outreach** — Autonomous sending with LLM self-check, kill switch, and warm-up ramp
- **Real-Time Dashboard** — Live event feed, per-application replay with screenshots, review queues
- **Admin Panel** — Profile management, source health, email config, blocklists (htmx + Tailwind, password + TOTP 2FA)
- **Structured Observability** — Every action logged as a replayable event with screenshots and timing

## Architecture

```
┌──────────────────┐        ┌───────────────────────┐        ┌─────────────────────┐
│   Admin Panel     │◀──────▶│   FastAPI Backend      │◀──────▶│   Neon (Postgres +   │
│  (htmx + Tailwind│        │  (REST + WebSocket)    │        │   pgvector)          │
│   + TOTP 2FA)    │        └───────────┬───────────┘        └─────────────────────┘
└──────────────────┘                    │
        ▲                     ┌─────────▼──────────┐
        │                     │   arq Task Queue    │
        │  Telegram Bot       │   (Redis-backed)    │
        │  (escalation)       └─────────┬──────────┘
        ▲                               │
        │              ┌────────────────┼────────────────┐
        │              ▼                ▼                ▼
        │     ┌────────────────┐ ┌──────────────┐ ┌──────────────┐
        │     │ Source Adapters │ │ Applier      │ │ Email Worker  │
        └─────│ (API + scrape) │ │ Workers      │ │ (SMTP +       │
              │                │ │ (Playwright, │ │  self-check + │
              └───────┬────────┘ │  tiered)     │ │  kill switch) │
                      │          └──────┬───────┘ └──────┬───────┘
                      ▼                 ▼                 ▼
              ┌────────────────────────────────────────────────────┐
              │        agent_events (structured, replayable)        │
              └────────────────────────────────────────────────────┘
```

## Job Sources

| Source | Type | Coverage |
|--------|------|----------|
| Remotive | API | Global remote tech jobs |
| Arbeitnow | API | EU tech jobs |
| HackerNews | API | Who's Hiring posts |
| Jobicy | API | Remote positions |
| Jobberman | Scrape | Nigeria |
| MyJobMag | Scrape | Nigeria |
| Eleman.net | Scrape | Türkiye |
| Prospects | API | UK graduate jobs |
| Milkround | Scrape (Playwright) | UK graduate jobs |
| LinkedIn / Indeed | **Manual only** | Surfaced as dashboard links — no scraping |

> **Why not kariyer.net or JobTeaser/Graduateland?** kariyer.net returns HTTP 403
to non-browser clients and JobTeaser gates listings behind an anti-bot
interstitial. Both are skipped; Eleman.net covers Türkiye and Arbeitnow/Jobicy
cover EU/remote.

## Project Status

All implementation phases are complete:

| Phase | Status |
|-------|--------|
| 1. Foundation (Neon schema, event logging, config service) | ✅ |
| 2. Source adapters (4 global API + 5 regional) | ✅ |
| 3. arq task queue + job state machine | ✅ |
| 4. Tiered auto-apply (Greenhouse, Lever, Ashby, Workday, generic LLM) | ✅ |
| 5. Email safety (self-check, kill switch, warm-up) | ✅ |
| 6. Admin panel (auth, CRUD, replay, review queue) | ✅ |
| 7. Docker + deployment | ✅ |

## Auto-Apply Tiers

| Tier | Trigger | Approach | Reliability |
|------|---------|----------|-------------|
| 1 | Known ATS (Greenhouse, Lever, Workday, Ashby) | Templated selectors per platform | ~80-90% |
| 2 | Unknown/custom form | LLM reads screenshot + DOM, fills with confidence threshold | Moderate |
| 3 | Below confidence threshold | Skip auto-apply, send cold email, flag for manual review | Fallback |

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for Playwright browsers)
- Redis (for arq task queue)
- Neon account (Postgres + pgvector)

### Installation

```bash
git clone https://github.com/anthonyy616/internship_automation.git
cd internship_automation
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### Configuration

```bash
cp .env.example .env
# Edit .env with your credentials (Neon, SMTP, OpenAI, Telegram, Redis)
```

### Database Setup

```bash
python -m backend.db.migrate
```

### Running

```bash
# Start the web server
python run.py

# Start the task worker (in a separate terminal)
arq backend.workers.settings.WorkerSettings
```

Open http://localhost:8000 for the dashboard.
Open http://localhost:8000/admin for the admin panel.

### Docker

```bash
docker compose up --build
```

This starts three services: the FastAPI app (port 8000), the arq worker, and
Redis. `data/` and `config/` are mounted from the host so your resume,
screenshots, and config survive container rebuilds.

### First Run (important)

1. **Apply the migration** (creates tables + seeds config):
   ```bash
   docker compose exec app python -m backend.db.migrate
   ```
2. **Set admin credentials** in `.env`: `ADMIN_PASSWORD`, and optionally
   `ADMIN_TOTP_SECRET` (generate with `python -c "import pyotp; print(pyotp.random_base32())"`).
   Scan the URI shown on `/admin/2fa` into your authenticator app.
3. **Auto-apply starts in dry-run mode** (`apply.dry_run = true` in the DB
   `config` table). It fills forms, takes screenshots, and never clicks submit
   until you flip the flag — via `POST /api/config/apply` with
   `{"dry_run": false}` or by toggling it through the API. Run a few dry-run
   cycles and review the screenshots in the admin panel before going live.
4. **Email sending** stays disabled until `SMTP_USER`/`SMTP_PASSWORD` are set
   and at least one job exposes a contact email (domain guessing is opt-in via
   `email.allow_domain_guess`).

## Configuration

All configuration is managed via the admin panel or the `config` database table:

- **Profile** — Name, university, skills, resume, portfolio
- **Keywords** — Search terms for job discovery
- **Regions** — Enable/disable target regions
- **Sources** — Enable/disable job board adapters
- **Email** — Template, daily caps, kill switch thresholds
- **Blocklist** — Companies or domains to skip

## Safety Features

- **Rate Limiting** — Configurable daily caps for applications and emails
- **Human-Like Delays** — Randomized delays between actions (5-15s)
- **Duplicate Prevention** — Won't apply to the same job twice
- **Email Self-Check** — LLM validates drafts before sending (no hallucinated claims, correct company name)
- **Kill Switch** — Auto-pauses sending if bounce rate exceeds threshold
- **Warm-Up Ramp** — Gradual daily increase from 5 to cap
- **Per-Domain Caps** — Max 3 emails per company domain per day
- **Dry-Run Mode** — Simulate full flow without submitting or sending

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.11+, FastAPI, WebSockets |
| Task Queue | arq (Redis-backed, async) |
| LLM | OpenAI GPT-4o via langchain |
| Browser | Playwright (async) + stealth |
| Database | Neon (PostgreSQL + pgvector) |
| Frontend | Vanilla HTML/CSS/JS (dashboard) |
| Admin Panel | htmx + Tailwind CSS + Jinja2 |
| Email | SMTP (Gmail/Custom) |

## Project Structure

```
internship_automation/
├── backend/
│   ├── app.py                  # FastAPI routes + WebSocket
│   ├── config.py               # Config management
│   ├── database.py             # asyncpg + Repository
│   ├── models.py               # Pydantic models
│   ├── websocket_manager.py    # WS broadcast
│   ├── auth.py                 # Password + TOTP 2FA
│   ├── admin/                  # Admin panel (routes + templates)
│   ├── db/                     # Neon schema + migrations
│   ├── workers/                # arq workers + cron scheduler
│   └── services/
│       ├── sources/            # Job source adapters
│       ├── applier/            # Tiered auto-apply
│       ├── email/              # Email compose + send + safety
│       ├── event_logger.py     # Structured event logging
│       ├── config_service.py   # DB-backed config
│       ├── filter.py           # Dedup + eligibility
│       ├── orchestrator.py     # arq enqueue + worker control
│       └── inference.py        # LLM form-field mapping
├── frontend/                   # Dashboard UI
├── data/                       # Screenshots, resume (gitignored)
├── config/                     # User config (gitignored)
├── .env.example                # Environment variables template
├── docker-compose.yml          # Docker setup
└── requirements.txt            # Python dependencies
```

## Author

**Anthony Ogbuah** — [anthonyy616.vercel.app](https://anthonyy616.vercel.app)
Computer Engineering (Major) Junior | AI Engineer

---

*Built to automate the stressful parts of job hunting*
