"""Admin-only routes: dashboard stats, audit log, and user list.

Every route sits behind `require_admin` (which builds on `get_current_user`, then
checks `role == admin`), so a provider's token gets a 403 here. This is the
server-side half of RBAC; the frontend also hides the Admin nav from non-admins,
but the API is the real gate.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
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
    User,
)
from app.schemas import AdminStats, AuditEntry, RoleUpdate, UserRead

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


@router.get("/users", response_model=list[UserRead])
async def users(session: AsyncSession = Depends(get_session)) -> list[User]:
    """All users/providers in the system, for the admin roster view."""
    result = await session.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


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
