"""Provider-facing template route: list the templates a provider can generate against.

Deliberately separate from the admin template-management routes (which live in
admin.py behind `require_admin`): ANY authenticated user may READ the active
templates so they can pick one on the generate form, but only admins may
create/edit/delete them. Splitting the read here keeps that access line obvious —
this router has no admin gate, the admin one does.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.db import get_session
from app.deps import get_current_user
from app.models import Template, User
from app.schemas import TemplateOption

router = APIRouter(prefix="/templates", tags=["templates"])


@router.get("", response_model=list[TemplateOption])
async def list_templates(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Template]:
    """Active templates (id + name only), for the provider's generate picker.

    Inactive templates are hidden from providers — an admin retires a template by
    flipping is_active off, which removes it from this list without deleting it (so
    old notes generated against it stay reproducible via their snapshot).
    """
    result = await session.execute(
        select(Template)
        .where(Template.is_active.is_(True))
        .order_by(Template.name)
    )
    return result.scalars().all()
