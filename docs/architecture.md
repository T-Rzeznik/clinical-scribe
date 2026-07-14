# Architecture & Design Decisions

Design reference for the AI Clinical Scribe platform. Each major decision is recorded
with its rationale.

## Stack

| Layer | Choice |
|---|---|
| Backend | FastAPI (Python 3.14), SQLModel over async SQLAlchemy, asyncpg driver |
| Frontend | React + Vite (not built yet) |
| Database | PostgreSQL 16 + pgvector (local: portable install; prod: AWS RDS) |
| AI | Anthropic Claude — `claude-sonnet-5` for SOAP generation |
| Infra (prod) | AWS EC2 behind nginx, HTTPS, Secrets Manager, private RDS |

## System shape (request flow)

```
Browser (React)  --HTTPS-->  nginx (:443, SSL)  --http-->  FastAPI (uvicorn, 127.0.0.1:8000)
   |  SSE stream  <----------------------------------------  /generate (SSE)
   |                                                          |-- auth (JWT + refresh)
   |                                                          |-- tool call: get_patient_history --> RDS
   |                                                          |-- connection pool --> RDS (private, VPC-only)
                                                              '-- Anthropic API (streaming + tool use)
```

Five infrastructure priorities and where they live:
1. **Streaming** — `/generate` streams SSE to the browser; FastAPI also streams *from* Anthropic (no full-buffering).
2. **Patient-history tool call** — happens server-side during generation, queries RDS. Never in the frontend prompt.
3. **RDS private** — no public IP; security group only accepts connections from the EC2 security group.
4. **Connection pool** — one pool in the FastAPI process, reused across requests (not per-request connections).
5. **nginx** — owns :443 + SSL; FastAPI is never directly on 80/443.

## Locked decisions

- **Users & roles:** single `users` table with a `role` column (`provider` | `admin`). NOT a roles/user_roles join table — two fixed roles need only a column.
- **Patients:** global `patients` table, `UNIQUE(first_name, last_name, dob)`. Patient identity is shared; per-provider note *visibility* is enforced separately on `encounters.provider_id`.
- **Note versioning:** `note_versions` only, no parent `notes` table. Append-only; "current" = highest `version_number`. `UNIQUE(encounter_id, version_number)`. SOAP stored as **four columns** (subjective/objective/assessment/plan).
- **Drafts / session persistence:** stored on the `encounters` row via a `status` column (`draft` -> `finalized`). Survives refresh + restores cross-device because it's in the DB.
- **Templates:** mutable + **snapshot-on-use** — the prompt used is copied onto the generated `note_version` (`template_snapshot`) so old notes remember how they were made even if the template later changes.
- **ICD-10 search:** **pgvector** in Postgres/RDS. Embed 200-300 codes; embed the query at search time; `ORDER BY embedding <=> query LIMIT k`.
- **Patient-history injection:** **model-driven tool use** — Claude calls `get_patient_history(patient_id)`; backend runs the RDS query and returns results; scope = **only the requesting provider's own** prior notes for that patient. Fallback to deterministic inject if the model doesn't call the tool reliably.
- **AI model:** `claude-sonnet-5` — quality/latency/cost balance suitable for real-time streaming.
- **Auth:** short-lived **JWT access token** (~20 min) + **DB-stored refresh token** (revocable). Enables the two edge cases below.
- **Streaming transport:** **SSE**, consumed in the browser via `fetch()` + `ReadableStream` (NOT native `EventSource`, which can't POST a body or send an auth header).
- **Two non-happy-path scenarios:** (1) **session expired on save** — 401 -> refresh/preserve draft, no data loss; (2) **empty / non-clinical transcript** — AI returns a graceful "insufficient clinical information" instead of hallucinating a note.

### Deferred build-time sub-decisions
- Browser token storage: httpOnly cookie vs `Authorization` header.
- Embedding model: local `sentence-transformers` vs a hosted embeddings API.
- Table creation mechanism (no Alembic): `create_all` init script vs startup hook vs hand-written DDL.

## ERD (9 tables)

One-sentence rationale per table:

1. **users** — everyone who logs in; `role` gates provider vs admin.
   `(id PK, email UQ, password_hash, first_name, last_name, role, is_active, created_at, updated_at)`
2. **patients** — the real person, deduped by name+DOB so history spans visits.
   `(id PK, first_name, last_name, dob, created_at, UNIQUE(first_name,last_name,dob))`
3. **templates** — admin-managed generation prompts.
   `(id PK, name, description, prompt_body, is_active, created_by FK->users, created_at, updated_at)`
4. **encounters** — one visit; links patient+provider+template, holds transcript + draft state.
   `(id PK, patient_id FK, provider_id FK->users, template_id FK->templates NULL, status, transcript_text, draft fields, created_at, updated_at)`
5. **note_versions** — immutable SOAP snapshots; append-only provides versioning + audit trail.
   `(id PK, encounter_id FK, version_number, subjective, objective, assessment, plan, template_snapshot, saved_by FK->users, saved_at, UNIQUE(encounter_id,version_number))`
6. **note_version_icd_codes** — M:N linking a version to its diagnoses (structured chips).
   `(note_version_id FK, icd10_code_id FK, source, PK(note_version_id, icd10_code_id))`
7. **icd10_codes** — embedded code catalog with vectors for semantic search.
   `(id PK, code UQ, description, embedding VECTOR(n))`
8. **refresh_tokens** — server-side half of auth; enables revoking a deactivated provider.
   `(id PK, user_id FK, token_hash UQ, expires_at, revoked_at NULL, created_at)`
9. **audit_log** — who did what, when, across the system.
   `(id PK, actor_user_id FK->users NULL, action, entity_type, entity_id, metadata JSONB, created_at)`

Build order note: tables 7 & 8's vector column depends on pgvector (installed with the ICD
search feature), so `icd10_codes` + `note_version_icd_codes` are built then; the other 7 come first.
