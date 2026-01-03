# 🚀 Internship Automation Bot

An AI-powered autonomous agent system that automates the internship application process across multiple regions. The bot discovers opportunities, submits applications, and sends personalized cold emails — all while you focus on preparing for interviews.

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![CrewAI](https://img.shields.io/badge/CrewAI-Agents-FF6B6B?style=for-the-badge)
![Playwright](https://img.shields.io/badge/Playwright-Automation-2EAD33?style=for-the-badge&logo=playwright&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-Database-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)

---

## ✨ Features

- **🌍 Multi-Region Support** — Target EU, UK, Nigeria, and Türkiye simultaneously
- **🤖 Fully Autonomous** — No manual intervention required once started
- **🔍 Smart Scraping** — Region-specific job boards + Google Jobs integration
- **📝 Auto-Apply** — Playwright fills application forms with stealth mode
- **📧 Cold Email Outreach** — Personalized emails sent after each application
- **📊 Real-Time Dashboard** — Live activity logs via WebSocket
- **💾 Persistent Storage** — All data saved to Supabase for analytics

---

## 🏗️ Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Web UI        │────▶│  FastAPI        │────▶│  CrewAI Agents  │
│   Dashboard     │◀────│  Backend        │◀────│  (5 Agents)     │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                              │                        │
                              ▼                        ▼
                        ┌─────────────────┐     ┌─────────────────┐
                        │   Supabase      │     │   Playwright    │
                        │   (PostgreSQL)  │     │   (Scraping)    │
                        └─────────────────┘     └─────────────────┘
```

---

## 🛠️ Tech Stack

| Category | Technology |
|----------|------------|
| **Backend** | Python 3.11+, FastAPI, WebSockets |
| **AI/Agents** | CrewAI, LangChain, OpenAI GPT-4o |
| **Web Scraping** | Playwright, BeautifulSoup, playwright-stealth |
| **Frontend** | HTML5, CSS3, JavaScript (Vanilla) |
| **Database** | Supabase (PostgreSQL) |
| **Email** | SMTP (Gmail/Custom) |
| **Styling** | Custom CSS with Glassmorphism, Dark Mode |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+ (for Playwright browsers)
- OpenAI API Key
- Supabase Account
- Gmail App Password (for SMTP)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/internship-automation-bot.git
cd internship-automation-bot

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### Configuration

1. **Copy the environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Fill in your credentials in `.env`:**
   ```env
   # OpenAI
   OPENAI_API_KEY=sk-your-key-here

   # SMTP (Gmail)
   SMTP_USER=your-email@gmail.com
   SMTP_PASSWORD=your-app-password
   SMTP_SERVER=smtp.gmail.com
   SMTP_PORT=587

   # Supabase
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=your-anon-key
   ```

3. **Update `config/config.yaml` with your profile:**
   ```yaml
   user_profile:
     name: "Your Name"
     university: "Your University"
     # ... etc
   ```

4. **Add your resume to `data/resume.pdf`**

### Run the Application

```bash
# Start the backend server
python -m backend.app

# Open http://localhost:8000 in your browser
```

---

## 📊 Dashboard Preview

The web dashboard provides:
- **Region Selector** — Choose which regions to target
- **Real-Time Logs** — See every action as it happens
- **Statistics Cards** — Jobs found, applications sent, emails delivered
- **Jobs Table** — Full history with status tracking

---

## 🌍 Supported Job Boards

| Region | Platforms |
|--------|-----------|
| **EU** | LinkedIn, Indeed (DE/FR/NL), Glassdoor, Graduateland |
| **UK** | Indeed UK, Graduate-Jobs, RateMyPlacement, Prospects, Milkround |
| **Nigeria** | Jobberman, MyJobMag, HotNigerianJobs, NgCareers |
| **Türkiye** | Kariyer.net, LinkedIn TR, Indeed TR, Yenibiris |

Plus **Google Jobs** integration for all regions!

---

## 🤖 AI Agents

The system uses 5 specialized CrewAI agents:

| Agent | Role |
|-------|------|
| **Researcher** | Discovers internship opportunities using advanced search |
| **Scraper** | Extracts job details and validates eligibility |
| **Email Writer** | Crafts personalized cold emails |
| **Applier** | Fills application forms automatically |
| **Coordinator** | Orchestrates the entire pipeline |

---

## 📧 Cold Email System

Emails are dynamically personalized with:
- Current date and time
- Company name and position title
- Hiring manager name (if found)
- AI-generated hook based on job requirements
- Your portfolio and contact information

---

## ⚠️ Safety Features

- **Daily Limits** — Configurable max applications/emails per day
- **Random Delays** — Human-like timing between actions
- **Stealth Mode** — Playwright configured to avoid bot detection
- **Duplicate Prevention** — Won't apply to the same job twice

---

## 📁 Project Structure

```
internship-automation-bot/
├── backend/              # FastAPI server
│   ├── app.py           # Main application
│   ├── services/        # Business logic
│   └── ...
├── frontend/            # Web UI
│   ├── index.html
│   ├── css/
│   └── js/
├── src/                 # CrewAI agents
│   ├── agents.py
│   └── tools.py
├── config/              # Configuration
├── data/                # Resume & local DB
└── templates/           # Email templates
```

---

## 🤝 Contributing

Contributions are welcome! Please read our contributing guidelines before submitting a PR.

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

**Anthony Ogbuah**  
Computer Science Junior | AI & Systems Engineering

---

*Built with ❤️ to automate the tedious parts of job hunting*
