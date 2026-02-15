# Internship Automation Bot - Project Context (Agentic Mode)

## 🎯 Project Overview

An autonomous, adaptive internship application bot that learns from user input.

1. **Scrapes** internship listings intelligently.
2. **Adapts** to different application forms (Greenhouse, Lever, Workday, Custom) using LLM inference.
3. **Learns** from user answers to build a comprehensive "Bio-Data Map".
4. **Applies** automatically once confidence is high.
5. **Falls back** to user input via UI when uncertain, then memorizes the answer.

---

## 🏗️ Adaptive Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         WEB UI (Frontend)                           │
2026 Update:                                                          │
│   - "Human-in-the-Loop" Form Filler Modal                           │
│   - Live View of Bot filling forms                                  │
│   - Question & Answer interface for missing data                    │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      BACKEND (Adaptive Core)                        │
│   - /api/answer_question - Endpoint for user to supply missing info │
│   - /ws - Stream "Need Input" events                                │
│   - QA Knowledge Base (Vector + Key/Value Store)                    │
└─────────────────────────────────────────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   JOB SCRAPER    │    │   ADAPTIVE APPLIER │    │   EMAIL SERVICE  │
│   (unchanged)    │    │  (The "Brain")   │    │   (unchanged)    │
│                  │    │ - Playwright     │    │                  │
│                  │    │ - DOM Analysis   │    │                  │
│                  │    │ - LLM Inference  │    │                  │
│                  │    └─────────┬────────┘    └──────────────────┘
└──────────────────┘              │
                                  ▼
                        ┌──────────────────┐
                        │  QA MEMORY MAP   │
                        │  (YAML/JSON)     │
                        │  "Question" ->   │
                        │  "Answer"        │
                        └──────────────────┘
```

---

## 🧠 Adaptive Logic (The "Map")

The bot maintains a `user_data_map.yaml` (or similar).
When it encounters a form field:

1. **Extract**: Get label, placeholder, start context using LLM.
2. **Search**: Look up in `user_data_map`.
3. **Infer**: If exact match missing, use LLM to check if existing answers suffice (e.g., "Years of Exp" calculated from "Grad Date").
4. **Decide**:
   - High Confidence -> Auto-fill.
   - Low Confidence -> Pause & Ask User via WebSocket.
5. **Learn**: Save User Answer -> `user_data_map` for future.

---

## ✅ Implementation Status

### Completed

| Component | Status |
|-----------|--------|
| Scraper (API-based) | ✅ Done |
| Database & Backend | ✅ Done |
| Basic Frontend | ✅ Done |

### In Progress (The "Fix")

| Component | Status |
|-----------|--------|
| **Adaptive Form Filler** | 🚧 To Do |
| **User QA Map** | 🚧 To Do |
| **Interactive UI Mode** | 🚧 To Do |
| **LLM Field Inference** | 🚧 To Do |

---

## 📁 File Structure Updates

```
job_applier_bot/
├── .agent/                     # Agent & Architecture Docs
│   ├── new_implementation.md   # The Plan
│   ├── fixes_and_problems.md   # Current Issues
│   └── project_context.md      # This File
├── config/
│   ├── config.yaml             # Basic Config
│   └── user_data_map.yaml      # THE BRAIN (Learned QA pairs)
├── backend/
│   ├── services/
│   │   └── applier_service.py  # Adaptive Logic
```
