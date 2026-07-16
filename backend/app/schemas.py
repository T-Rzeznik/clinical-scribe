"""API request/response shapes (Pydantic models, NOT database tables).

These are the public contract of the endpoints: what the client must send and
what it gets back. Keeping them separate from the `models.py` tables means the
DB shape and the wire shape can differ — e.g. we never expose `password_hash`.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

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


class AdminStats(BaseModel):
    """Dashboard overview counts for GET /admin/stats."""

    users: int
    providers: int
    admins: int
    patients: int
    encounters: int
    note_versions: int


class RoleUpdate(BaseModel):
    """Body for PATCH /admin/users/{id}/role — the new role to assign."""

    role: Role


class AuditEntry(BaseModel):
    """One row of the audit log for GET /admin/audit."""

    id: int
    created_at: datetime
    actor_email: str | None  # null for system/anonymous events
    action: str
    entity_type: str
    entity_id: int | None


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

    `saved_by_email`/`saved_by_name` identify the human who approved this version
    (joined from `users` via note_versions.saved_by). Attribution matters in a
    clinical trail: every immutable version says who stands behind it, not just
    what it says. `saved_at` (from NoteVersionRead) is the "when".
    """

    icd_codes: list[IcdCodeIn] = []
    saved_by_email: str
    saved_by_name: str


class EncounterSummary(BaseModel):
    """One row in a patient's encounter history: the visit plus how many note
    versions it has and which is latest. Used by the read-only history views.
    """

    id: int
    created_at: datetime
    status: EncounterStatus
    version_count: int
    latest_version_number: int | None


# --- Draft persistence (cross-device draft restore) ---


class EncounterTranscriptUpdate(BaseModel):
    """Body for PATCH /encounters/{id} — autosave the working transcript.

    Only the transcript is patchable here (identity/template are set at creation);
    the encounter stays a draft. Sent repeatedly as the provider types so the draft
    survives a browser refresh or a switch to another device — it lives in RDS, not
    in the browser, which is the whole point of persisting it server-side.
    """

    transcript_text: str


class EncounterTranscriptRead(BaseModel):
    """Echo returned by PATCH /encounters/{id}: confirms exactly what was stored,
    so the client can reconcile its optimistic local copy against the server.
    """

    id: int
    transcript_text: str


class DraftEncounterRead(BaseModel):
    """The provider's resumable draft for GET /encounters/draft (or `null`).

    Flattens encounter + patient identity (joined from `patients`) into one object
    so the client can repopulate the entire 'start a visit' form in a single load,
    without a second round-trip to resolve the patient's name/DOB from patient_id.
    """

    id: int
    patient_id: int
    patient_first_name: str
    patient_last_name: str
    patient_dob: date
    transcript_text: str
    template_id: int | None


# --- Templates ---


class TemplateOption(BaseModel):
    """One selectable template for the provider's generate picker (GET /templates).

    Deliberately tiny — the provider only needs to choose by name; the prompt body
    is admin-facing detail they neither see nor edit.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TemplateRead(BaseModel):
    """Full admin view of a template (GET/POST/PATCH /admin/templates)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prompt_body: str
    is_active: bool
    created_by: int


class TemplateCreate(BaseModel):
    """Body for POST /admin/templates. `is_active` defaults true so a newly
    authored template is immediately offered to providers unless withheld.
    """

    name: str
    prompt_body: str
    is_active: bool = True


class TemplateUpdate(BaseModel):
    """Body for PATCH /admin/templates/{id} — any subset of the editable fields.

    Every field defaults to None (= 'not provided'); the handler uses
    `model_dump(exclude_unset=True)` to touch ONLY the keys the caller actually
    sent, so a PATCH that omits a field leaves it unchanged rather than nulling it.
    """

    name: str | None = None
    prompt_body: str | None = None
    is_active: bool | None = None


class TemplateGenerateRequest(BaseModel):
    """Body for POST /admin/templates/generate — the admin's short, plain-English
    description of the encounter type they want a template for (e.g. "orthopedic
    follow-up"). `min_length` blocks a trivially-empty prompt before we spend an
    AI call; `max_length` keeps it a description, not a pasted essay.
    """

    description: str = Field(min_length=3, max_length=200)


class TemplateGenerateResponse(BaseModel):
    """What POST /admin/templates/generate returns: a DRAFT (nothing is persisted).

    The admin reviews/edits these in the form and then Saves through the normal
    audited POST /admin/templates — so the AI never writes to the DB directly.
    """

    name: str
    prompt_body: str


# --- Provider management (admin) ---


class AdminUserCreate(BaseModel):
    """Body for POST /admin/users — provision a provider or admin directly.

    Unlike public signup (which forces role=provider so nobody self-promotes), this
    is an admin-only surface, so it CAN set `role`. `full_name` is split into the
    stored first/last columns by the handler.
    """

    email: str
    full_name: str
    password: str
    role: Role


class AdminUserActiveUpdate(BaseModel):
    """Body for PATCH /admin/users/{id}/active — enable or disable a login."""

    is_active: bool


class AdminUserRead(BaseModel):
    """Admin roster view of a user. Adds `is_active` and a composed `full_name`
    (the DB keeps first/last split) and — like UserRead — NEVER exposes the hash.
    """

    id: int
    email: str
    full_name: str
    role: Role
    is_active: bool


# --- Encounter oversight (admin) ---


class AdminEncounterRead(BaseModel):
    """One row of the cross-provider oversight table (GET /admin/encounters).

    Admins see EVERY provider's encounters (no ownership filter), so each row
    carries the owning provider's identity plus the patient identity and a
    denormalized `version_count`, all resolved server-side in one query.
    """

    id: int
    provider_email: str
    provider_name: str
    patient_first_name: str
    patient_last_name: str
    patient_dob: date
    created_at: datetime
    status: EncounterStatus
    version_count: int
