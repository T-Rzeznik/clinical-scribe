"""Encounter routes: start a visit (patient + transcript + template).

An encounter ties a patient, the provider who owns it, and (optionally) the
template, and holds the pasted transcript plus the draft/finalized status. The
SOAP generation route (built next) streams against a stored encounter.
"""

import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db import get_session
from app.deps import get_current_user
from app.generation import stream_soap_note
from app.models import Encounter, Patient, Template, User
from app.schemas import EncounterCreate, EncounterRead

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/encounters", tags=["encounters"])

# Matches the template created in seed.py — used as the fallback when an encounter
# has no explicit template_id.
DEFAULT_TEMPLATE_NAME = "Standard SOAP Note"

# Cheap, deterministic guard: a transcript shorter than this is treated as "no real
# input" and rejected BEFORE we ever call (and pay for) the AI. Semantic junk that's
# long enough to pass this is caught by Rule 3 in the template prompt instead.
MIN_TRANSCRIPT_CHARS = 15


async def _get_or_create_patient(
    session: AsyncSession, first_name: str, last_name: str, dob: date
) -> Patient:
    """Deduplicate a patient by identity (name + DOB), the UNIQUE key on the table.

    Patients are GLOBAL and shared across providers, so one person's history spans
    every visit. We look them up by the three identity fields; if they're new we
    stage the row and `flush` — which sends the INSERT and gets Postgres to assign
    `patient.id` — WITHOUT committing, so it lands in the same transaction as the
    encounter the caller is about to create. (`commit` here would be premature.)
    """
    result = await session.execute(
        select(Patient).where(
            Patient.first_name == first_name,
            Patient.last_name == last_name,
            Patient.dob == dob,
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        patient = Patient(first_name=first_name, last_name=last_name, dob=dob)
        session.add(patient)
        await session.flush()  # assigns patient.id; still inside this transaction
    return patient


@router.post("", response_model=EncounterRead, status_code=status.HTTP_201_CREATED)
async def create_encounter(
    body: EncounterCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Encounter:
    """Create a draft encounter owned by the requesting provider."""
    patient = await _get_or_create_patient(
        session, body.patient_first_name, body.patient_last_name, body.patient_dob
    )

    encounter = Encounter(
        patient_id=patient.id,
        provider_id=current_user.id,  # ownership: this provider's encounter, not global
        template_id=body.template_id,
        transcript_text=body.transcript_text,
        # status defaults to draft in the model
    )
    session.add(encounter)
    await session.commit()  # commits BOTH the new patient (if any) and the encounter
    await session.refresh(encounter)  # reload so id/created_at are populated
    return encounter


async def _resolve_template_prompt(
    session: AsyncSession, template_id: int | None
) -> str:
    """Return the prompt_body to generate against.

    If the encounter names a template, use it; otherwise fall back to the seeded
    default. Either way we return the raw prompt text — the caller feeds it to
    Claude as the system prompt.
    """
    if template_id is not None:
        template = await session.get(Template, template_id)
    else:
        result = await session.execute(
            select(Template).where(Template.name == DEFAULT_TEMPLATE_NAME)
        )
        template = result.scalar_one_or_none()

    if template is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No template available to generate against",
        )
    return template.prompt_body


@router.post("/{encounter_id}/generate")
async def generate_note(
    encounter_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Stream a SOAP note for one encounter as Server-Sent Events (SSE).

    Everything that could fail cleanly (not found, empty transcript, no template)
    is checked UP FRONT and raised as a normal HTTP error — before we open the
    stream. Once streaming starts, the response is committed, so a mid-stream
    failure can only be reported as an error *event* inside the stream.
    """
    # 1. Load the encounter, scoped to its owner. A provider can only generate for
    #    their OWN encounters; anything else is a 404 (don't reveal it exists).
    encounter = await session.get(Encounter, encounter_id)
    if encounter is None or encounter.provider_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Encounter not found"
        )

    # 2. Empty-transcript guard — reject trivially-empty input deterministically,
    #    before spending an API call. (The "non-clinical but non-empty" case is the
    #    prompt's job, not ours.)
    transcript = (encounter.transcript_text or "").strip()
    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Transcript is empty or too short to generate a note",
        )

    # 3. Resolve the template prompt (explicit, else the seeded default).
    system_prompt = await _resolve_template_prompt(session, encounter.template_id)

    # 4. The SSE body: wrap each text chunk from Claude in a `data: {...}\n\n` frame.
    #    We JSON-encode the chunk so newlines/quotes in the note can't break the
    #    SSE framing (blank line = end of one event).
    async def event_stream():
        try:
            async for chunk in stream_soap_note(system_prompt, transcript):
                yield f"data: {json.dumps({'text': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"  # signal clean completion
        except Exception:  # noqa: BLE001 — the stream already started; can't send an HTTP error now
            logger.exception("SOAP generation failed for encounter %s", encounter_id)
            yield f"data: {json.dumps({'error': 'Generation failed. Please try again.'})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",  # never cache a live stream
            "X-Accel-Buffering": "no",    # tell nginx (prod) not to buffer the stream
        },
    )
