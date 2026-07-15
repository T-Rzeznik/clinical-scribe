"""Seed the database with dev users and a default SOAP template.

Local-dev / demo data only. Creates one admin, three providers (all sharing a
known dev password so you can log in during the demo), and one active SOAP
template for generation to run against.

Idempotent by natural key: users are keyed by email, the template by name. Each
record is created only if it's missing, so re-running never duplicates and can
safely finish a partial seed.

Run from the backend/ directory (same as init_db.py):
    .venv\\Scripts\\python seed.py
"""

import asyncio

from sqlmodel import select

from app.db import SessionLocal
from app.models import Role, Template, User
from app.security import hash_password

# Shared dev password for every seeded account. Fine for a local take-home;
# NEVER a pattern for production (there, accounts self-register / use Secrets).
DEV_PASSWORD = "password123"

# (email, first_name, last_name, role) — 1 admin + 3 providers per the spec.
SEED_USERS = [
    ("admin@scribe.local", "Ava", "Admin", Role.admin),
    ("schen@scribe.local", "Sarah", "Chen", Role.provider),
    ("jpatel@scribe.local", "Jamal", "Patel", Role.provider),
    ("mgarcia@scribe.local", "Maria", "Garcia", Role.provider),
]

DEFAULT_TEMPLATE_NAME = "Standard SOAP Note"

# The system prompt every note is generated against. Snapshot-on-use means the
# exact text is copied onto each note_version, so notes stay reproducible even if
# this template is later edited.
DEFAULT_TEMPLATE_PROMPT = """\
You are an expert clinical scribe. Convert the provided patient encounter \
transcript into a structured SOAP note.

Produce four sections:
- Subjective: the patient's reported symptoms, history, and concerns in their own \
framing (chief complaint, history of present illness, relevant past/social history).
- Objective: measurable, observed findings only — vital signs, exam findings, and \
test results explicitly stated in the transcript.
- Assessment: the clinician's diagnostic impression(s), reasoning from the \
subjective and objective findings.
- Plan: next steps — treatments, medications, tests ordered, referrals, and \
follow-up instructions.

Rules:
- Ground every statement strictly in the transcript. Do NOT invent symptoms, \
vitals, diagnoses, medications, or history that were not stated or clearly implied.
- If a section has no supporting information in the transcript, write \
"Not documented" for that section rather than guessing.
- If the transcript contains no meaningful clinical content (e.g. it is empty, \
off-topic, or not a medical encounter), do NOT fabricate a note. Instead respond \
that there is insufficient clinical information to generate a SOAP note.
- Use concise, professional clinical language. Prefer standard medical \
abbreviations where a clinician would.
- Do not include patient-identifying commentary beyond what belongs in the note.
"""


async def main() -> None:
    async with SessionLocal() as session:
        # --- Users ---
        for email, first_name, last_name, role in SEED_USERS:
            existing = await session.execute(select(User).where(User.email == email))
            if existing.scalar_one_or_none() is not None:
                print(f"  user exists, skipping: {email}")
                continue
            session.add(
                User(
                    email=email,
                    password_hash=hash_password(DEV_PASSWORD),
                    first_name=first_name,
                    last_name=last_name,
                    role=role,
                )
            )
            print(f"  created user: {email} ({role.value})")

        # Commit users first so the admin row gets its DB-assigned id, which the
        # template's created_by foreign key needs.
        await session.commit()

        # --- Default template (owned by the admin) ---
        admin_result = await session.execute(
            select(User).where(User.email == "admin@scribe.local")
        )
        admin = admin_result.scalar_one()  # guaranteed to exist after the loop above

        template_result = await session.execute(
            select(Template).where(Template.name == DEFAULT_TEMPLATE_NAME)
        )
        if template_result.scalar_one_or_none() is None:
            session.add(
                Template(
                    name=DEFAULT_TEMPLATE_NAME,
                    description="Default general-purpose SOAP note template.",
                    prompt_body=DEFAULT_TEMPLATE_PROMPT,
                    created_by=admin.id,
                )
            )
            await session.commit()
            print(f"  created template: {DEFAULT_TEMPLATE_NAME}")
        else:
            print(f"  template exists, skipping: {DEFAULT_TEMPLATE_NAME}")

    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(main())
