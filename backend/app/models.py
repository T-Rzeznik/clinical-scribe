from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC value.

    Paired with `sa_type=DateTime(timezone=True)` (Postgres `timestamptz`) so the
    instant is stored unambiguously and compares correctly against other aware
    datetimes — mixing aware and naive values raises at the driver.
    """
    return datetime.now(timezone.utc)


class Role(str, Enum):
    """The two fixed roles in the system.

    Subclassing `str` means each member IS a string ("provider"/"admin"), so it
    serializes to JSON and stores in Postgres as plain text with no extra work.
    """

    provider = "provider"
    admin = "admin"


class User(SQLModel, table=True):
    """Everyone who can log in. `role` gates provider vs admin access."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    first_name: str
    last_name: str
    role: Role = Field(default=Role.provider)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class Patient(SQLModel, table=True):
    """The real person a note is about. Global (shared across providers) and
    deduped by name + date of birth, so one person's history spans every visit.
    """

    __tablename__ = "patients"
    __table_args__ = (
        UniqueConstraint("first_name", "last_name", "dob", name="uq_patient_identity"),
    )

    id: int | None = Field(default=None, primary_key=True)
    first_name: str
    last_name: str
    dob: date
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class Template(SQLModel, table=True):
    """Admin-managed generation prompts. Mutable, but snapshot-on-use: the exact
    prompt body is copied onto each note_version so old notes stay reproducible.
    """

    __tablename__ = "templates"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    description: str | None = None
    prompt_body: str
    is_active: bool = Field(default=True)
    created_by: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class EncounterStatus(str, Enum):
    draft = "draft"
    finalized = "finalized"


class Encounter(SQLModel, table=True):
    """One visit. Links the patient, the provider who owns it, and (optionally)
    the template used. Holds the pasted transcript and the draft/finalized state.
    """

    __tablename__ = "encounters"

    id: int | None = Field(default=None, primary_key=True)
    patient_id: int = Field(foreign_key="patients.id")
    provider_id: int = Field(foreign_key="users.id")
    template_id: int | None = Field(default=None, foreign_key="templates.id")
    status: EncounterStatus = Field(default=EncounterStatus.draft)
    transcript_text: str | None = None
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
    updated_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class NoteVersion(SQLModel, table=True):
    """An immutable SOAP snapshot for an encounter. Append-only: 'current' = the
    highest version_number. SOAP lives as four columns; template_snapshot records
    the exact prompt used to generate it.
    """

    __tablename__ = "note_versions"
    __table_args__ = (
        UniqueConstraint("encounter_id", "version_number", name="uq_encounter_version"),
    )

    id: int | None = Field(default=None, primary_key=True)
    encounter_id: int = Field(foreign_key="encounters.id")
    version_number: int
    subjective: str
    objective: str
    assessment: str
    plan: str
    template_snapshot: str | None = None
    saved_by: int = Field(foreign_key="users.id")
    saved_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class RefreshToken(SQLModel, table=True):
    """The server-side half of auth. We store only a HASH of the token, so a DB
    leak can't be replayed. revoked_at lets us kill a session (e.g. deactivated
    provider) even before it expires.
    """

    __tablename__ = "refresh_tokens"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    token_hash: str = Field(unique=True, index=True)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))


class AuditLog(SQLModel, table=True):
    """Who did what, when. actor is nullable so we can log system/anonymous
    events. event_metadata is JSONB for flexible per-action detail.
    """

    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    actor_user_id: int | None = Field(default=None, foreign_key="users.id")
    action: str
    entity_type: str
    entity_id: int | None = None
    event_metadata: dict | None = Field(default=None, sa_column=Column(JSONB))
    created_at: datetime = Field(default_factory=utcnow, sa_type=DateTime(timezone=True))
