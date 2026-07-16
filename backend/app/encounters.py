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
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.audit import record_event
from app.db import get_session
from app.deps import get_current_user
from app.generation import stream_soap_note
from app.models import (
    Encounter,
    NoteVersion,
    NoteVersionIcdCode,
    Patient,
    Template,
    User,
)
from app.patient_history import get_patient_history
from app.schemas import (
    EncounterCreate,
    EncounterRead,
    IcdCodeIn,
    NoteVersionCreate,
    NoteVersionDetail,
    NoteVersionRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/encounters", tags=["encounters"])

# Matches the template created in seed.py — used as the fallback when an encounter
# has no explicit template_id.
DEFAULT_TEMPLATE_NAME = "Standard SOAP Note"

# Cheap, deterministic guard: a transcript shorter than this is treated as "no real
# input" and rejected BEFORE we ever call (and pay for) the AI. Semantic junk that's
# long enough to pass this is caught by Rule 3 in the template prompt instead.
MIN_TRANSCRIPT_CHARS = 15

# Appended to the template prompt at generation time so the model also SUGGESTS
# ICD-10 codes. We ask for a single machine-parseable last line (codes only, comma
# separated) — the frontend strips this line before showing the SOAP note, then
# validates the codes against our catalog. The model only suggests; the catalog is
# the source of truth for what can actually be stored.
ICD_SUGGESTION_INSTRUCTION = (
    "\n\nAfter the SOAP note, output exactly one final line in this format:\n"
    "SUGGESTED_ICD_CODES: <comma-separated ICD-10 codes>\n"
    "List the ICD-10 codes best supported by this encounter (primary diagnosis "
    "plus any documented comorbidities). Use standard ICD-10 codes only (e.g. "
    "R07.9, E11.9, I10). If none are clearly supported, write "
    "'SUGGESTED_ICD_CODES: none'. Do not add any text after this line."
)


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

    # 3. Resolve the template prompt (explicit, else the seeded default), then
    #    append the ICD-suggestion instruction so the model also proposes codes.
    system_prompt = (
        await _resolve_template_prompt(session, encounter.template_id)
        + ICD_SUGGESTION_INSTRUCTION
    )

    # 3a. Audit the generation request before streaming. Commit now (mid-request is
    #     safe: expire_on_commit=False keeps the ORM objects readable in the closure
    #     below, and the streaming body can't emit an audit row once it's started).
    record_event(
        session,
        actor_user_id=current_user.id,
        action="generate_note",
        entity_type="encounter",
        entity_id=encounter.id,
    )
    await session.commit()

    # 3b. Build the tool executor Claude may call mid-generation. It's a CLOSURE
    #     over the encounter's real patient + the requesting provider, so the
    #     model can't reach another patient's data — it passes no arguments.
    async def tool_executor(name: str, tool_input: dict) -> str:
        if name == "get_patient_history":
            return await get_patient_history(
                session,
                patient_id=encounter.patient_id,
                provider_id=current_user.id,
                exclude_encounter_id=encounter.id,
            )
        return f"Unknown tool: {name}"

    # 4. The SSE body: wrap each text chunk from Claude in a `data: {...}\n\n` frame.
    #    We JSON-encode the chunk so newlines/quotes in the note can't break the
    #    SSE framing (blank line = end of one event).
    async def event_stream():
        try:
            async for event in stream_soap_note(system_prompt, transcript, tool_executor):
                if event["type"] == "reset":
                    # A tool-use turn's narration — tell the client to discard it.
                    yield f"data: {json.dumps({'reset': True})}\n\n"
                else:
                    yield f"data: {json.dumps({'text': event['text']})}\n\n"
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


@router.get("/{encounter_id}/versions", response_model=list[NoteVersionDetail])
async def list_note_versions(
    encounter_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[NoteVersionDetail]:
    """List an encounter's saved note versions, newest first, each with its codes.

    Read-only history. Ownership-scoped exactly like generate/save: your encounter
    or a 404. `note_versions` is append-only, so this is the audit trail of every
    revision the provider approved.
    """
    encounter = await session.get(Encounter, encounter_id)
    if encounter is None or encounter.provider_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Encounter not found"
        )

    versions = (
        await session.execute(
            select(NoteVersion)
            .where(NoteVersion.encounter_id == encounter_id)
            .order_by(NoteVersion.version_number.desc())
        )
    ).scalars().all()

    # Fetch all codes for these versions in one query, then group by version_id so
    # we don't issue a query per version (N+1).
    version_ids = [v.id for v in versions]
    codes_by_version: dict[int, list[IcdCodeIn]] = {}
    if version_ids:
        code_rows = (
            await session.execute(
                select(NoteVersionIcdCode).where(
                    NoteVersionIcdCode.note_version_id.in_(version_ids)
                )
            )
        ).scalars().all()
        for row in code_rows:
            codes_by_version.setdefault(row.note_version_id, []).append(
                IcdCodeIn(code=row.code, description=row.description)
            )

    return [
        NoteVersionDetail(
            id=v.id,
            encounter_id=v.encounter_id,
            version_number=v.version_number,
            subjective=v.subjective,
            objective=v.objective,
            assessment=v.assessment,
            plan=v.plan,
            saved_at=v.saved_at,
            icd_codes=codes_by_version.get(v.id, []),
        )
        for v in versions
    ]


@router.post(
    "/{encounter_id}/versions",
    response_model=NoteVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def save_note_version(
    encounter_id: int,
    body: NoteVersionCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> NoteVersion:
    """Persist a provider-approved SOAP note as the next immutable version.

    `note_versions` is append-only: this never updates an existing row, it writes
    v(N+1). What lands here is the text a human reviewed and stands behind, not raw
    model output. Saving does NOT finalize the encounter — a corrected v2 can still
    follow — so `status` is left untouched; finalizing is a separate action.
    """
    # 1. Ownership guard, identical to generate: your encounter, or a 404 that
    #    doesn't reveal whether someone else's encounter exists.
    encounter = await session.get(Encounter, encounter_id)
    if encounter is None or encounter.provider_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Encounter not found"
        )

    # 2. Next version number = current max for this encounter + 1 (1 if none yet).
    #    Race note: two concurrent saves could compute the same number; the
    #    UNIQUE(encounter_id, version_number) constraint is the backstop (one wins,
    #    the other 500s on the violation). Fine for the demo; real fix is retry.
    result = await session.execute(
        select(func.max(NoteVersion.version_number)).where(
            NoteVersion.encounter_id == encounter_id
        )
    )
    next_version = (result.scalar_one() or 0) + 1

    # 3. Snapshot the prompt this note was generated against so an old version stays
    #    reproducible even if the template is edited later. (Resolved at save time —
    #    generation persists nothing — so a template edit in the gap would show here.)
    template_snapshot = await _resolve_template_prompt(session, encounter.template_id)

    # 4. Write the immutable row, stamped with WHO approved it (saved_by = the human).
    version = NoteVersion(
        encounter_id=encounter_id,
        version_number=next_version,
        subjective=body.subjective,
        objective=body.objective,
        assessment=body.assessment,
        plan=body.plan,
        template_snapshot=template_snapshot,
        saved_by=current_user.id,
    )
    session.add(version)
    await session.flush()  # assign version.id so the code rows can reference it

    # 5. Persist the selected ICD codes as child rows (same transaction). De-dupe by
    #    code so a double-selected code can't violate UNIQUE(version_id, code).
    seen_codes: set[str] = set()
    for c in body.icd_codes:
        if c.code in seen_codes:
            continue
        seen_codes.add(c.code)
        session.add(
            NoteVersionIcdCode(
                note_version_id=version.id, code=c.code, description=c.description
            )
        )

    record_event(
        session,
        actor_user_id=current_user.id,
        action="save_version",
        entity_type="note_version",
        entity_id=version.id,
        metadata={"encounter_id": encounter_id, "version_number": next_version},
    )
    await session.commit()  # commits the version, its codes, AND the audit entry atomically
    await session.refresh(version)
    return version
