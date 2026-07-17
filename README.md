# 🩺 AI Clinical Scribe

**Turn a messy visit transcript into a clean, structured medical note — in seconds.**

A doctor pastes in what was said during a patient visit. The app reads it, and an AI
writes it up as a proper clinical note, live on the screen, and even suggests the official
billing codes that go with it. The doctor reviews, tweaks, and saves.

<p>
  <img alt="Status" src="https://img.shields.io/badge/status-deployed%20%26%20verified-brightgreen">
  <img alt="Backend" src="https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white">
  <img alt="Frontend" src="https://img.shields.io/badge/frontend-React-61DAFB?logo=react&logoColor=black">
  <img alt="Database" src="https://img.shields.io/badge/database-PostgreSQL-4169E1?logo=postgresql&logoColor=white">
  <img alt="AI" src="https://img.shields.io/badge/AI-Claude-D97757?logo=anthropic&logoColor=white">
  <img alt="Cloud" src="https://img.shields.io/badge/cloud-AWS-232F3E?logo=amazonwebservices&logoColor=white">
  <img alt="Tests" src="https://img.shields.io/badge/E2E%20tests-17%20passing-brightgreen?logo=playwright&logoColor=white">
</p>

> **Tags:** `healthcare` · `clinical-documentation` · `soap-notes` · `icd-10` · `generative-ai`
> · `llm` · `fastapi` · `react` · `postgresql` · `aws` · `full-stack`

> 🌐 **Live demo:** **https://trz-clinical-scribe.duckdns.org** — deployed on AWS via Terraform
> (EC2 + private RDS + nginx + a real Let's Encrypt certificate) and verified end-to-end. To
> avoid idle cloud costs the instance is spun up on demand and torn down between sessions
> (`terraform apply` / `terraform destroy`); the URL is stable across re-deploys.

---

## 💡 What problem does this solve?

Doctors spend a huge chunk of their day on paperwork — writing up notes after every visit.
It's slow, it's tedious, and it pulls their attention away from patients.

This app does the writing for them. It takes the raw conversation and produces a **SOAP
note** — the standard four-part format every clinician uses:

| Section | What it captures |
|---|---|
| **S** — Subjective | What the patient reports (symptoms, history, how they feel) |
| **O** — Objective | What was measured (vitals, exam findings, test results) |
| **A** — Assessment | The clinician's diagnosis or interpretation |
| **P** — Plan | What happens next (medications, follow-ups, referrals) |

The doctor stays in control the whole time — the AI writes a first draft, the human reviews
and edits it, and nothing is saved until they approve it.

---

## ✨ What it can do

- 📝 **Instant note-writing.** Paste a transcript, watch the note appear word-by-word as the
  AI writes it (like watching someone type).
- 🏷️ **Smart billing codes.** It suggests the right **ICD-10 diagnosis codes** automatically
  and double-checks them against an official list, so made-up codes get filtered out.
- 🧠 **Remembers the patient.** When writing a new note, the AI can pull in that patient's
  *own* past visits for context — but only visits that *this* doctor has seen (strict privacy).
- 👥 **Patient look-up.** Start typing a name and it finds the right patient; browse their
  full visit history and past notes.
- 💾 **Never lose your work.** Drafts save automatically as you type, and if your session
  times out mid-note, it quietly logs you back in without losing a word.
- 🔐 **Sign-in & roles.** Secure login with two kinds of users: **providers** (doctors) and
  **admins**.
- 📊 **Admin dashboard.** Admins can add or deactivate users, change roles, review activity
  logs, oversee every visit across the practice, and manage note templates.
- 🧾 **A permanent record.** Saved notes are *append-only* — like a legal medical record,
  history can never be secretly edited, only added to.

---

## 🎬 How it works (the 30-second version)

```
   Doctor pastes                AI reads it and                Doctor reviews,
   the transcript      ─────▶   writes the note      ─────▶    edits, and saves
   into the app                 live on screen                 the final version
                                     │
                                     └── also suggests ICD-10 billing codes
                                         (and looks up the patient's past visits)
```

Behind the scenes: a **React** web page talks to a **Python (FastAPI)** server, which calls
**Anthropic's Claude** AI to write the note and stores everything in a **PostgreSQL**
database.

---

## 🧰 Tech stack

| Layer | Technology | Why |
|---|---|---|
| **Frontend** | React + Vite | Fast, modern web UI; streams the note in live |
| **Backend** | FastAPI (Python) | Async API server, great for streaming responses |
| **Database** | PostgreSQL (AWS RDS, private) | Reliable, normalized storage; append-only note history |
| **ICD search** | Local MiniLM embeddings (`fastembed`, in-process cosine) | Semantic match on meaning, not keywords; no external API (pgvector-ready) |
| **AI** | Anthropic Claude (`claude-sonnet-5`) | Writes the SOAP note + looks up patient history |
| **Testing** | Playwright (E2E) + `scripts/prod_smoke.py` (live API) | Browser tests green; smoke test exercises every endpoint in prod |
| **Cloud** | AWS (EC2, RDS, Secrets Manager) via Terraform | Production deployment, defined as code, **applied & verified** |

---

## 🚀 Getting started (for developers)

**Prerequisites:** Python 3.14, Node 22, and a local PostgreSQL. This project uses a
**portable Postgres install** (no Docker needed — chosen to run light on a low-RAM laptop).

```powershell
# 1. Start the local database (portable Postgres — start it each session)
powershell -File scripts/pg-start.ps1

# 2. Start the backend API  →  http://127.0.0.1:8000  (visit /docs for the API explorer)
cd backend
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. Start the frontend  →  http://localhost:5173
cd frontend
npm install
npm run dev

# When you're done for the day, stop the database:
powershell -File scripts/pg-stop.ps1
```

**Demo logins** (local dev only):

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `password` |
| Provider | `schen@scribe.local` | `password123` |
| Provider | `jpatel@scribe.local` | `password123` |
| Provider | `mgarcia@scribe.local` | `password123` |

**Run the tests:**

```powershell
cd frontend
npm run test:e2e        # Playwright end-to-end tests (both servers must be running)
```

**Smoke-test the live deployment** (every backend feature, against the deployed URL):

```bash
python scripts/prod_smoke.py    # health, auth+RBAC, semantic ICD, generation, versioning, admin, refresh
```

---

## 📁 Project structure

```
Clinical Scribe/
├── backend/      FastAPI server — auth, note generation, ICD codes, admin, database
├── frontend/     React app — login, workspace, admin dashboard, E2E tests
├── infra/        AWS deployment as Terraform (applied & verified; see infra/README.md)
├── scripts/      Helper scripts (start/stop the local database)
└── docs/         Architecture, ERD, and design decisions
```

Deeper documentation lives in [`docs/architecture.md`](docs/architecture.md) (the system
design and database diagram) and [`docs/PROGRESS.md`](docs/PROGRESS.md) (current build state).

---

## 🔒 Security & privacy

Because this handles health-style data, safety is built in from the start:

- **Data is scoped per provider** — a doctor can only ever see their own patients and notes.
  This boundary is enforced on the server, not just hidden in the UI.
- **Passwords are hashed** (bcrypt), never stored in plain text.
- **Secure sessions** using short-lived access tokens plus refresh tokens.
- **Every important action is logged** (logins, note saves, admin changes) for an audit trail.
- **No secrets in the code** — configuration comes from environment variables (locally from a
  gitignored `.env`; in production from AWS Secrets Manager).
- **Real HTTPS** in the production design (a genuine Let's Encrypt certificate, not self-signed).

---

## 📌 Project status

This is a **portfolio / take-home project** built to demonstrate a full-stack, AI-powered
application end-to-end.

- ✅ **Feature-complete** — everything above works and is covered by automated tests.
- ☁️ **Deployed & verified on AWS** — the full Terraform stack in [`infra/`](infra/README.md)
  was applied to a live account and smoke-tested end-to-end over HTTPS: EC2 behind nginx,
  **private** RDS (VPC-only), Secrets Manager, connection pooling, and a real Let's Encrypt
  certificate. The instance is torn down between sessions to avoid idle billing and re-applied
  on demand (stable URL).

---

<sub>Built as a learning-focused engineering project. SOAP notes, ICD-10 codes, and the
clinical workflow are modeled after real-world clinical documentation practice.</sub>
