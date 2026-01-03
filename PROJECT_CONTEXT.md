# Internship Automation Bot - Project Context

## 🎯 Project Overview

An AI-powered internship application automation bot that:
1. **Scrapes** internship listings from multiple job APIs (Remotive, Arbeitnow, HackerNews, Jobicy)
2. **Marks applications** as submitted (form filling placeholder for future)
3. **Sends personalized cold emails** to companies after applying
4. **Logs all activity** in real-time to a web UI and persists data to Supabase

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         WEB UI (Frontend)                           │
│   HTML/CSS/JavaScript - Modern Dark Theme Dashboard                 │
│   - Region selection (EU, UK, Nigeria, Türkiye)                     │
│   - Contact email input (for companies to reach you)                │
│   - Portfolio URL input                                              │
│   - Start/Stop controls with real-time status                       │
│   - Real-time activity logs via WebSocket                           │
│   - Stats: Jobs Found, Applications, Emails Sent                    │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   │ REST API + WebSocket
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      BACKEND (FastAPI + Python)                     │
│   - /api/start, /api/stop - Bot control                             │
│   - /api/status, /api/stats - Status and statistics                 │
│   - /api/jobs, /api/logs - Data retrieval                           │
│   - /ws - WebSocket for real-time log streaming                     │
│   - Orchestrator coordinates scraping → applying → emailing         │
└─────────────────────────────────────────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   JOB SCRAPER    │    │   ORCHESTRATOR   │    │   EMAIL SERVICE  │
│   (API-based)    │    │  (Coordinates)   │    │   (SMTP)         │
│ - Remotive API   │    │ - Phase 1: Scrape│    │ - Yahoo/Gmail    │
│ - Arbeitnow API  │    │ - Phase 2: Save  │    │ - Personalized   │
│ - HackerNews API │    │ - Phase 3: Apply │    │ - Resume attach  │
│ - Jobicy API     │    │ - Phase 4: Email │    └──────────────────┘
└──────────────────┘    └──────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         SUPABASE (PostgreSQL)                        │
│   - jobs: All found internships                                      │
│   - applications: Submitted applications                             │
│   - emails: Sent cold emails with status                             │
│   - activity_logs: Real-time activity stream                         │
│   - sessions: Bot session tracking                                   │
└──────────────────────────────────────────────────────────────────────┘
```

---

## ✅ Implementation Status

### Completed
| Component | File | Status |
|-----------|------|--------|
| Configuration | `backend/config.py` | ✅ Done |
| Database Service | `backend/database.py` | ✅ Done |
| WebSocket Manager | `backend/websocket_manager.py` | ✅ Done |
| Pydantic Models | `backend/models.py` | ✅ Done |
| Job Scraper | `backend/services/job_scraper.py` | ✅ Done (API-based) |
| Email Service | `backend/services/email_service.py` | ✅ Done |
| Orchestrator | `backend/services/orchestrator.py` | ✅ Done |
| FastAPI App | `backend/app.py` | ✅ Done |
| Dashboard HTML | `frontend/index.html` | ✅ Done |
| Styles (Dark Theme) | `frontend/css/styles.css` | ✅ Done |
| WebSocket Client | `frontend/js/websocket.js` | ✅ Done |
| Main App Logic | `frontend/js/app.js` | ✅ Done |

### Known Issues (To Fix Later)
| Issue | Description |
|-------|-------------|
| Email SMTP | Yahoo SMTP requires app password setup - currently failing auth |
| Form Filling | Actual form automation is a placeholder (marked as applied) |

---

## 🌍 Job Sources (API-Based Scraping)

Instead of scraping LinkedIn/Indeed (which block bots), the scraper uses these APIs:

| Source | API | Coverage |
|--------|-----|----------|
| Remotive | `remotive.com/api/remote-jobs` | Global remote tech jobs |
| Arbeitnow | `arbeitnow.com/api/job-board-api` | EU tech jobs |
| HackerNews | `hn.algolia.com/api/v1/search` | HN Who's Hiring posts |
| Jobicy | `jobicy.com/api/v2/remote-jobs` | Remote positions |
| Fallback | Generated sample jobs | Demo mode when APIs fail |

---

## 📁 Project File Structure

```
job_applier_bot/
├── .env                        # Environment variables (SMTP, Supabase)
├── .env.example                # Template for .env
├── .gitignore                  # Git ignore rules
├── PROJECT_CONTEXT.md          # This file
├── requirements.txt            # Python dependencies
├── supabase_schema.sql         # SQL for creating tables
│
├── backend/                    # FastAPI Backend
│   ├── __init__.py
│   ├── app.py                  # FastAPI app, routes, WebSocket
│   ├── config.py               # Configuration from yaml + env
│   ├── models.py               # Pydantic models
│   ├── database.py             # Supabase client
│   ├── websocket_manager.py    # Real-time log broadcasting
│   └── services/
│       ├── __init__.py
│       ├── job_scraper.py      # API-based job scraping
│       ├── email_service.py    # Email composition & sending
│       └── orchestrator.py     # Coordinates the workflow
│
├── frontend/                   # Web UI
│   ├── index.html              # Dashboard HTML
│   ├── css/
│   │   └── styles.css          # Dark theme + glassmorphism
│   └── js/
│       ├── app.js              # Main application logic
│       └── websocket.js        # WebSocket client
│
├── config/
│   └── config.yaml             # User profile & search criteria (gitignored)
│
├── data/
│   └── resume.pdf              # User's resume
│
└── templates/
    └── email_template.txt      # Email template
```

---

## 🔐 Environment Variables (.env)

```env
# OpenAI (optional - for future AI features)
OPENAI_API_KEY=sk-...

# SMTP for Email Sending
# For Yahoo Mail:
SMTP_USER=your-email@yahoo.com
SMTP_PASSWORD=your-16-char-app-password
SMTP_SERVER=smtp.mail.yahoo.com
SMTP_PORT=587

# For Gmail:
# SMTP_USER=your-email@gmail.com
# SMTP_PASSWORD=your-app-password
# SMTP_SERVER=smtp.gmail.com
# SMTP_PORT=587

# Supabase (for persistent storage)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key
```

---

## 🚀 How to Run

### 1. Install Dependencies
```bash
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Configure Environment
- Copy `.env.example` to `.env`
- Add your Supabase credentials
- Add SMTP credentials (Gmail/Yahoo app password)

### 3. Setup Database
- Run `supabase_schema.sql` in Supabase SQL Editor

### 4. Start the Server
```bash
python -m backend.app
```

### 5. Open Dashboard
Navigate to http://localhost:8000

---

## 📧 Email Configuration

### Yahoo Mail Setup
1. Enable 2-Step Verification at https://login.yahoo.com/account/security
2. Generate App Password: Security → Generate app password → "Other app"
3. Add to `.env`:
   ```
   SMTP_SERVER=smtp.mail.yahoo.com
   SMTP_PORT=587
   SMTP_USER=your@yahoo.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx  (no spaces)
   ```

### Gmail Setup
1. Enable 2-Step Verification
2. Generate App Password at https://myaccount.google.com/apppasswords
3. Add to `.env`:
   ```
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your@gmail.com
   SMTP_PASSWORD=xxxx xxxx xxxx xxxx
   ```

---

## 💾 Database Schema

See `supabase_schema.sql` for full schema. Main tables:
- `jobs` - Found internships
- `applications` - Application records
- `emails` - Sent emails
- `activity_logs` - Real-time logs
- `sessions` - Bot session history

---

## ⚙️ User Configuration (config.yaml)

```yaml
user_profile:
  name: "Anthony Ogbuah"
  email: "anthonyogbuah@gmail.com"
  university: "European University of Lefke"
  university_year: "Junior"
  major: "Computer Engineering"
  skills:
    - Python
    - TypeScript
    - SQL
    - C/C++
    - Machine Learning
    - Deep Learning
    - Docker
    - Git
  portfolio_url: "https://anthonyy616.vercel.app"

search_criteria:
  keywords:
    - "Software Engineer Intern"
    - "Machine Learning Intern"
    - "AI Intern"
    - "Data Engineer Intern"
    - "Backend Engineer Intern"
```

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI (Python 3.12+) |
| Web Scraping | httpx + APIs (Playwright available) |
| Frontend | Vanilla HTML/CSS/JavaScript |
| Real-time | WebSockets |
| Database | Supabase (PostgreSQL) |
| Email | SMTP (Yahoo/Gmail) |
| Styling | Custom CSS (dark glassmorphism) |

---

*Last Updated: January 3, 2026*
