"""Audit logging helper.

Clinical software needs a "who did what, when" trail. This writes rows into the
`audit_log` table. The caller owns the transaction — we only stage the row (via
`session.add`), so the audit entry commits atomically with the action it records
(e.g. the save_version row and its "save_version" audit entry land together, or
neither does).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


def record_event(
    session: AsyncSession,
    *,
    actor_user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    metadata: dict | None = None,
) -> None:
    """Stage an audit_log row. The caller must commit (keeps it in one transaction
    with the action being audited). `actor_user_id` is nullable for system events.
    """
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            event_metadata=metadata,
        )
    )
