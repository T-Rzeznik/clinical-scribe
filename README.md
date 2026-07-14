# AI Clinical Scribe Platform

Provider-facing AI clinical documentation tool. A provider pastes an encounter
transcript or freeform notes; the AI streams back a structured SOAP note with
suggested ICD-10 codes.

## Stack
- **Backend:** FastAPI (Python), SQLModel over async SQLAlchemy, psycopg/asyncpg
- **Frontend:** React + Vite
- **Database:** PostgreSQL + pgvector (local: Docker; prod: AWS RDS)
- **AI:** Anthropic Claude (SOAP generation + patient-history tool use)
- **Infra:** AWS EC2 behind nginx, HTTPS, Secrets Manager, private RDS

## Local development

Prerequisites: Python 3.14, Node 22. Postgres runs as a **portable local install**
at `%USERPROFILE%\scribe-pg` (no Docker — chosen for a low-RAM laptop).

```powershell
# 1. Start the local database (portable Postgres, not a service)
powershell -File scripts/pg-start.ps1
# ...stop it when you're done for the day:
powershell -File scripts/pg-stop.ps1

# (backend / frontend run steps added as we build them)
```

A `docker-compose.yml` is kept for later use (porting to a container / prod parity),
but day-to-day dev uses the portable Postgres above.

## Architecture

See `docs/` (added during the build) for the ERD and design decisions.
