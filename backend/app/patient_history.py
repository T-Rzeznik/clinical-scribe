"""Patient-history retrieval for the model-driven tool call.

During SOAP generation, Claude can call `get_patient_history` to pull this
patient's PRIOR notes into its context — grounding the new note in real history
(ongoing conditions, prior diagnoses) instead of guessing.

The security boundary lives here, NOT in the model: the tool takes no patient
argument. The executor is built as a closure over the encounter's real
patient_id and the REQUESTING provider's id, so Claude can only ever see history
for (a) the patient being documented and (b) notes THIS provider authored. The
model literally cannot request another patient or another provider's notes.
"""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Encounter, NoteVersion


async def get_patient_history(
    session: AsyncSession,
    patient_id: int,
    provider_id: int,
    exclude_encounter_id: int | None = None,
) -> str:
    """Return a text summary of this patient's prior saved notes, provider-scoped.

    Joins note_versions to their encounter and keeps only rows where the encounter
    belongs to BOTH this patient and this provider. We exclude the current
    encounter (it has no saved versions yet, and we don't want the model reading
    its own in-progress work). For each prior encounter we keep the LATEST version
    (highest version_number) — the note the provider ultimately stood behind.
    """
    stmt = (
        select(NoteVersion, Encounter.created_at)
        .join(Encounter, NoteVersion.encounter_id == Encounter.id)
        .where(Encounter.patient_id == patient_id)
        .where(Encounter.provider_id == provider_id)
    )
    if exclude_encounter_id is not None:
        stmt = stmt.where(Encounter.id != exclude_encounter_id)
    # Order so that, per encounter, the highest version_number comes first.
    stmt = stmt.order_by(desc(Encounter.created_at), desc(NoteVersion.version_number))

    result = await session.execute(stmt)
    rows = result.all()

    if not rows:
        return "No prior encounters on record for this patient under your care."

    # Keep only the latest version per encounter (first one seen, thanks to the sort).
    latest_by_encounter: dict[int, tuple[NoteVersion, object]] = {}
    for version, created_at in rows:
        if version.encounter_id not in latest_by_encounter:
            latest_by_encounter[version.encounter_id] = (version, created_at)

    blocks: list[str] = []
    for version, created_at in latest_by_encounter.values():
        date_str = created_at.date().isoformat() if created_at else "unknown date"
        blocks.append(
            f"--- Encounter on {date_str} ---\n"
            f"Subjective: {version.subjective}\n"
            f"Objective: {version.objective}\n"
            f"Assessment: {version.assessment}\n"
            f"Plan: {version.plan}"
        )
    return "\n\n".join(blocks)
