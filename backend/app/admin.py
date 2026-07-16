"""Admin-only routes: dashboard stats, audit log, and user list.

Every route sits behind `require_admin` (which builds on `get_current_user`, then
checks `role == admin`), so a provider's token gets a 403 here. This is the
server-side half of RBAC; the frontend also hides the Admin nav from non-admins,
but the API is the real gate.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_event
from app.db import get_session
from app.deps import require_admin
from app.models import (
    AuditLog,
    Encounter,
    NoteVersion,
    Patient,
    Role,
    Template,
    User,
)
from app.schemas import (
    AdminEncounterRead,
    AdminStats,
    AdminUserActiveUpdate,
    AdminUserCreate,
    AdminUserRead,
    AuditEntry,
    RoleUpdate,
    TemplateCreate,
    TemplateGenerateRequest,
    TemplateGenerateResponse,
    TemplateRead,
    TemplateUpdate,
    UserRead,
)
from app.anthropic_client import MODEL, client
from app.security import hash_password

# System prompt for the template GENERATOR (a prompt that writes a prompt). It
# tells Claude to emit a note-template — itself a system prompt that will later
# steer the scribe. The structural rules (the four exact SOAP headers + the
# no-fabrication / "Not documented" / non-clinical safety rules) are baked in so
# every generated template stays compatible with the scribe's output parser and
# the app's clinical-safety guarantees; only the emphasis/tone adapts to the
# admin's requested encounter type.
_TEMPLATE_GENERATOR_SYSTEM = (
    "You are an expert at writing prompts for an AI clinical scribe. The user "
    "gives you a short description of an encounter type (e.g. \"orthopedic "
    "follow-up\", \"pediatric urgent care\", \"new patient annual physical\").\n\n"
    "Produce a complete NOTE TEMPLATE for that encounter type. A note template is "
    "a system prompt that steers the scribe when it turns a raw encounter "
    "transcript or freeform clinician notes into a structured SOAP note.\n\n"
    "The template you write MUST:\n"
    "- Instruct the scribe to output a SOAP note with four sections, each starting "
    "on its own line with the exact headers \"Subjective:\", \"Objective:\", "
    "\"Assessment:\", \"Plan:\", in that order.\n"
    "- Tailor the emphasis, expected content, and clinical tone to the requested "
    "encounter type — what to foreground, which history and exam elements matter "
    "most, and typical assessment/plan considerations for that visit.\n"
    "- Enforce these safety rules: never invent facts not supported by the input; "
    "if a section has no information, write \"Not documented\" rather than "
    "fabricating; if the input has no clinically meaningful content, state that "
    "there is insufficient clinical information to generate a note instead of "
    "producing one.\n"
    "- Be concise, professional, and something a physician would trust.\n\n"
    "Output ONLY the template prompt text — no preamble, no commentary, no "
    "surrounding quotes, and no markdown code fences."
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _count(session: AsyncSession, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    return (await session.execute(stmt)).scalar_one()


@router.get("/stats", response_model=AdminStats)
async def stats(session: AsyncSession = Depends(get_session)) -> AdminStats:
    """System-wide counts for the dashboard overview cards."""
    return AdminStats(
        users=await _count(session, User),
        providers=await _count(session, User, User.role == Role.provider),
        admins=await _count(session, User, User.role == Role.admin),
        patients=await _count(session, Patient),
        encounters=await _count(session, Encounter),
        note_versions=await _count(session, NoteVersion),
    )


@router.get("/audit", response_model=list[AuditEntry])
async def audit(
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[AuditEntry]:
    """Most recent audit_log entries, newest first, with the actor's email."""
    rows = await session.execute(
        select(AuditLog, User.email)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return [
        AuditEntry(
            id=log.id,
            created_at=log.created_at,
            actor_email=email,
            action=log.action,
            entity_type=log.entity_type,
            entity_id=log.entity_id,
        )
        for log, email in rows.all()
    ]


def _user_read(u: User) -> AdminUserRead:
    """Build the admin roster view of a user, composing full_name from the split
    first/last columns and NEVER touching password_hash. One place so create,
    toggle-active, and list all return an identical shape.
    """
    return AdminUserRead(
        id=u.id,
        email=u.email,
        full_name=f"{u.first_name} {u.last_name}".strip(),
        role=u.role,
        is_active=u.is_active,
    )


@router.get("/users", response_model=list[AdminUserRead])
async def users(session: AsyncSession = Depends(get_session)) -> list[AdminUserRead]:
    """All users/providers in the system, for the admin roster view."""
    result = await session.execute(select(User).order_by(User.created_at))
    return [_user_read(u) for u in result.scalars().all()]


@router.post("/users", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserRead:
    """Provision a provider or admin directly (admins can set role; signup can't).

    Rejects a duplicate email up front (409), hashes the password before it ever
    touches the DB, and splits full_name into the stored first/last columns. The
    response omits the hash. Audited.
    """
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
        )

    # Split on the FIRST space: everything after it is the last name (so "Mary Jo
    # Watson" -> first="Mary", last="Jo Watson"). A single-word name yields "".
    first_name, _, last_name = body.full_name.strip().partition(" ")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),  # hash before storage, like signup
        first_name=first_name,
        last_name=last_name,
        role=body.role,
    )
    session.add(user)
    await session.flush()  # assign user.id so the audit entry + response can reference it

    record_event(
        session,
        actor_user_id=admin.id,
        action="create_user",
        entity_type="user",
        entity_id=user.id,
        metadata={"email": body.email, "role": body.role.value},
    )
    await session.commit()  # user row + audit entry commit together
    await session.refresh(user)
    return _user_read(user)


@router.patch("/users/{user_id}/active", response_model=AdminUserRead)
async def set_user_active(
    user_id: int,
    body: AdminUserActiveUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> AdminUserRead:
    """Enable or disable a login. Audited.

    Guard: an admin cannot deactivate THEMSELVES — that would lock them out of the
    panel mid-session (get_current_user rejects inactive users). Disable yourself
    only via another admin.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id and not body.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    user.is_active = body.is_active
    record_event(
        session,
        actor_user_id=admin.id,
        action="set_user_active",
        entity_type="user",
        entity_id=user.id,
        metadata={"is_active": body.is_active},
    )
    await session.commit()
    await session.refresh(user)
    return _user_read(user)


@router.patch("/users/{user_id}/role", response_model=UserRead)
async def set_user_role(
    user_id: int,
    body: RoleUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Assign a user's role (provider <-> admin). Audited.

    Guard: an admin cannot strip their OWN admin role — that could lock the last
    admin out of the panel mid-session. Demote yourself only via another admin.
    """
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id and body.role != Role.admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot remove your own admin role",
        )

    old_role = user.role.value
    user.role = body.role
    record_event(
        session,
        actor_user_id=admin.id,
        action="change_role",
        entity_type="user",
        entity_id=user.id,
        metadata={"from": old_role, "to": body.role.value},
    )
    await session.commit()
    await session.refresh(user)
    return user


# --- Template management (admin) ---


@router.get("/templates", response_model=list[TemplateRead])
async def list_templates_admin(
    session: AsyncSession = Depends(get_session),
) -> list[Template]:
    """Every template incl. inactive ones — the admin needs to see and re-enable
    retired templates, unlike the provider-facing GET /templates which hides them.
    """
    result = await session.execute(select(Template).order_by(Template.name))
    return result.scalars().all()


@router.post("/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Template:
    """Author a new generation template, stamped with its creator. Audited."""
    template = Template(
        name=body.name,
        prompt_body=body.prompt_body,
        is_active=body.is_active,
        created_by=admin.id,  # provenance: who authored this prompt
    )
    session.add(template)
    await session.flush()  # assign template.id for the audit entry + response

    record_event(
        session,
        actor_user_id=admin.id,
        action="create_template",
        entity_type="template",
        entity_id=template.id,
        metadata={"name": body.name},
    )
    await session.commit()
    await session.refresh(template)
    return template


@router.post("/templates/generate", response_model=TemplateGenerateResponse)
async def generate_template(
    body: TemplateGenerateRequest,
    admin: User = Depends(require_admin),
) -> TemplateGenerateResponse:
    """Draft a note template from a short description, using Claude.

    This is a pure GENERATION helper — it persists NOTHING. The admin gets back a
    suggested name + prompt body to review and edit in the form, then saves via the
    (audited) POST /admin/templates. Keeping a human in the loop means the AI never
    writes a live prompt to the DB on its own. No DB session is needed here.
    """
    # A single non-streaming call: a template body is short, and the admin waits on
    # one result (unlike the token-by-token SOAP stream a provider watches fill in).
    try:
        resp = await client.messages.create(
            model=MODEL,
            max_tokens=1200,
            system=_TEMPLATE_GENERATOR_SYSTEM,
            messages=[{"role": "user", "content": body.description}],
        )
    except Exception as exc:  # network / API failure — surface as a clean 502
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Template generation failed: {exc}",
        )

    # Concatenate the text blocks of the reply (a plain response is one text block,
    # but joining is robust if the SDK splits it).
    prompt_body = "".join(
        block.text for block in resp.content if block.type == "text"
    ).strip()
    if not prompt_body:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The model returned an empty template.",
        )

    # Derive a sensible default name from the description (Title Case, trimmed).
    # The admin can rename it before saving — this is just a helpful pre-fill.
    name = body.description.strip().title()[:60] or "New Template"
    return TemplateGenerateResponse(name=name, prompt_body=prompt_body)


@router.patch("/templates/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Template:
    """Edit a template in place. Any subset of {name, prompt_body, is_active}.

    Editing the live prompt is safe because it's snapshot-on-use: existing note
    versions keep the exact prompt they were generated against (template_snapshot),
    so a later edit never rewrites history. Audited.
    """
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )

    # exclude_unset => only the keys the caller actually sent; an omitted field is
    # left untouched rather than overwritten with its default/None.
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(template, field, value)

    record_event(
        session,
        actor_user_id=admin.id,
        action="update_template",
        entity_type="template",
        entity_id=template.id,
        metadata={"fields": list(changes.keys())},
    )
    await session.commit()
    await session.refresh(template)
    return template


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a template. Audited.

    Encounters may still reference it via template_id (a FK), so we NULL those out
    first to avoid a foreign-key violation. That loses nothing: the actual prompt
    used is already snapshotted on each note_versions row (template_snapshot), and a
    template-less encounter simply falls back to the default at generate time.
    """
    template = await session.get(Template, template_id)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Template not found"
        )

    # Detach any encounters pointing at this template before deleting it.
    await session.execute(
        update(Encounter)
        .where(Encounter.template_id == template_id)
        .values(template_id=None)
    )
    await session.delete(template)

    record_event(
        session,
        actor_user_id=admin.id,
        action="delete_template",
        entity_type="template",
        entity_id=template_id,
        metadata={"name": template.name},
    )
    await session.commit()
    # 204: no body — the response_model is omitted and we return None.


# --- Encounter oversight (admin) ---


@router.get("/encounters", response_model=list[AdminEncounterRead])
async def list_encounters_admin(
    provider_id: int | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> list[AdminEncounterRead]:
    """Cross-provider encounter list, newest first. Admin sees ALL providers.

    Optional filters (applied only when provided): a single provider_id, and a
    created_at date range [start, end] inclusive. version_count is computed with a
    grouped subquery and left-joined, so an encounter with zero versions still
    appears (count 0) rather than being dropped by an inner join.
    """
    # Count versions per encounter once, as a subquery, then LEFT JOIN it in.
    version_counts = (
        select(
            NoteVersion.encounter_id.label("encounter_id"),
            func.count().label("cnt"),
        )
        .group_by(NoteVersion.encounter_id)
        .subquery()
    )

    stmt = (
        select(
            Encounter,
            User,
            Patient,
            func.coalesce(version_counts.c.cnt, 0),
        )
        .join(User, User.id == Encounter.provider_id)
        .join(Patient, Patient.id == Encounter.patient_id)
        .outerjoin(version_counts, version_counts.c.encounter_id == Encounter.id)
        .order_by(Encounter.created_at.desc())
    )

    if provider_id is not None:
        stmt = stmt.where(Encounter.provider_id == provider_id)
    if start is not None:
        # func.date() truncates the timestamptz to a date so we compare date-to-date.
        stmt = stmt.where(func.date(Encounter.created_at) >= start)
    if end is not None:
        stmt = stmt.where(func.date(Encounter.created_at) <= end)

    rows = (await session.execute(stmt)).all()
    return [
        AdminEncounterRead(
            id=encounter.id,
            provider_email=provider.email,
            provider_name=f"{provider.first_name} {provider.last_name}".strip(),
            patient_first_name=patient.first_name,
            patient_last_name=patient.last_name,
            patient_dob=patient.dob,
            created_at=encounter.created_at,
            status=encounter.status,
            version_count=count,
        )
        for encounter, provider, patient, count in rows
    ]
