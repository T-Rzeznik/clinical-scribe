"""Patient routes: provider-scoped autocomplete search.

The frontend uses this to let a provider pick an EXISTING patient instead of
re-typing name + DOB (a typo would mint a duplicate patient row and silently
fragment that person's history). Results are scoped to patients THIS provider has
actually treated — a provider must not be able to enumerate the global patient
list or another provider's roster.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import get_current_user
from app.models import Encounter, NoteVersion, Patient, User
from app.schemas import EncounterSummary, PatientSearchResult

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get("/search", response_model=list[PatientSearchResult])
async def search_patients(
    q: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Patient]:
    """Return up to 10 patients this provider has treated whose name matches `q`.

    Scoping is the security boundary: we JOIN through `encounters` and filter on
    `provider_id == current_user.id`, so the result can only ever contain patients
    the caller has seen. Matching is case-insensitive against first name, last
    name, or the full "first last" string.
    """
    term = q.strip()
    if not term:
        return []

    pattern = f"%{term.lower()}%"
    full_name = func.lower(Patient.first_name + " " + Patient.last_name)

    result = await session.execute(
        select(Patient)
        .join(Encounter, Encounter.patient_id == Patient.id)
        .where(
            Encounter.provider_id == current_user.id,
            or_(
                func.lower(Patient.first_name).like(pattern),
                func.lower(Patient.last_name).like(pattern),
                full_name.like(pattern),
            ),
        )
        .distinct()
        .order_by(Patient.last_name, Patient.first_name)
        .limit(10)
    )
    return result.scalars().all()


@router.get("/{patient_id}/encounters", response_model=list[EncounterSummary])
async def list_patient_encounters(
    patient_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[EncounterSummary]:
    """List THIS provider's encounters for one patient, newest first.

    Provider-scoped like search: `provider_id == current_user.id`, so a provider
    only ever sees their own visits with the patient. Each row carries a version
    count + the latest version number (LEFT JOIN so encounters with no saved
    version still appear, with count 0).
    """
    result = await session.execute(
        select(
            Encounter.id,
            Encounter.created_at,
            Encounter.status,
            func.count(NoteVersion.id).label("version_count"),
            func.max(NoteVersion.version_number).label("latest_version_number"),
        )
        .outerjoin(NoteVersion, NoteVersion.encounter_id == Encounter.id)
        .where(
            Encounter.provider_id == current_user.id,
            Encounter.patient_id == patient_id,
        )
        .group_by(Encounter.id, Encounter.created_at, Encounter.status)
        .order_by(Encounter.created_at.desc())
    )
    return [
        EncounterSummary(
            id=row.id,
            created_at=row.created_at,
            status=row.status,
            version_count=row.version_count,
            latest_version_number=row.latest_version_number,
        )
        for row in result.all()
    ]
