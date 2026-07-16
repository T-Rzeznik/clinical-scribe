"""API request/response shapes (Pydantic models, NOT database tables).

These are the public contract of the endpoints: what the client must send and
what it gets back. Keeping them separate from the `models.py` tables means the
DB shape and the wire shape can differ — e.g. we never expose `password_hash`.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models import EncounterStatus, Role


class SignupRequest(BaseModel):
    """Body for POST /auth/signup. Note: no `role` — public signup is always a
    provider; admins are provisioned separately, so a caller can't self-promote.
    """

    email: str
    password: str
    first_name: str
    last_name: str


class LoginRequest(BaseModel):
    """Body for POST /auth/login."""

    email: str
    password: str


class UserRead(BaseModel):
    """Safe public view of a user — deliberately omits `password_hash`."""

    # from_attributes lets Pydantic build this from an ORM object's attributes
    # (FastAPI does UserRead.model_validate(user_row) under the hood).
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    first_name: str
    last_name: str
    role: Role


class TokenResponse(BaseModel):
    """Returned by login and refresh. `access_token` is the short-lived JWT sent as
    `Authorization: Bearer <token>`; `refresh_token` is the long-lived opaque token
    the client stores to obtain new access tokens without re-entering a password.
    """

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """Body for POST /auth/refresh."""

    refresh_token: str


class EncounterCreate(BaseModel):
    """Body for POST /encounters — start a visit.

    We take the patient's identity fields (not a patient_id) because the caller
    is a provider typing in front of a patient; the backend deduplicates them
    into the global `patients` table by name + DOB. `template_id` and
    `transcript_text` are optional so a draft can be started before either is set.
    """

    patient_first_name: str
    patient_last_name: str
    patient_dob: date
    template_id: int | None = None
    transcript_text: str | None = None


class EncounterRead(BaseModel):
    """Public view of an encounter row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    provider_id: int
    template_id: int | None
    status: EncounterStatus
    transcript_text: str | None
    created_at: datetime


class PatientSearchResult(BaseModel):
    """One autocomplete hit for GET /patients/search. Includes DOB so the provider
    can disambiguate two patients with the same name before picking one.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    dob: date


class IcdCodeIn(BaseModel):
    """One ICD-10 code the provider attached to the note (part of the save body)."""

    code: str
    description: str


class NoteVersionCreate(BaseModel):
    """Body for POST /encounters/{id}/versions — the provider-approved SOAP note.

    Four separate fields, not one blob: by the time a note reaches this endpoint a
    human has reviewed and stands behind each section, so it arrives already split
    into S/O/A/P. Parsing the AI's streamed markdown into sections is the frontend's
    (review UI's) job; the backend stores exactly what the provider approved.

    `icd_codes` are the codes the provider selected (AI-suggested and/or searched);
    they're persisted alongside the note as part of the record.
    """

    subjective: str
    objective: str
    assessment: str
    plan: str
    icd_codes: list[IcdCodeIn] = []


class NoteVersionRead(BaseModel):
    """Public view of a saved note version."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    encounter_id: int
    version_number: int
    subjective: str
    objective: str
    assessment: str
    plan: str
    saved_at: datetime


class NoteVersionDetail(NoteVersionRead):
    """A saved version plus the ICD codes attached to it — used when browsing
    history so the reader sees the full record, not just the SOAP text.
    """

    icd_codes: list[IcdCodeIn] = []


class EncounterSummary(BaseModel):
    """One row in a patient's encounter history: the visit plus how many note
    versions it has and which is latest. Used by the read-only history views.
    """

    id: int
    created_at: datetime
    status: EncounterStatus
    version_count: int
    latest_version_number: int | None
