"""Authentication routes: signup and login.

An APIRouter is a mountable group of routes; main.py includes it under /auth.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.audit import record_event
from app.config import settings
from app.db import get_session
from app.deps import get_current_user
from app.models import RefreshToken, Role, User, utcnow
from app.schemas import (
    LoginRequest,
    RefreshRequest,
    SignupRequest,
    TokenResponse,
    UserRead,
)
from app.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _issue_refresh_token(session: AsyncSession, user_id: int) -> str:
    """Create a refresh_tokens row (storing only the hash) and return the RAW token.

    The caller is responsible for committing — we just stage the row so it commits
    in the same transaction as whatever else the handler is doing.
    """
    raw_token = generate_refresh_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hash_refresh_token(raw_token),
            expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return raw_token


@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, session: AsyncSession = Depends(get_session)) -> User:
    """Register a new provider. Rejects a duplicate email up front."""
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),  # hash before it ever touches the DB
        first_name=body.first_name,
        last_name=body.last_name,
        role=Role.provider,  # forced — signup can't create an admin
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)  # reload so user.id (assigned by Postgres) is populated
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Verify credentials and return a signed access token."""
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    # One identical error for "no such email" AND "wrong password" — never reveal
    # which, or an attacker can enumerate valid accounts.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    access_token = create_access_token(user_id=user.id, role=user.role.value)
    refresh_token = await _issue_refresh_token(session, user.id)
    record_event(session, actor_user_id=user.id, action="login", entity_type="user", entity_id=user.id)
    await session.commit()  # persist the refresh-token row + audit entry
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Exchange a valid refresh token for a new access token.

    Looks the token up by HASH, rejecting it if missing, revoked, or expired. This
    is the DB check the stateless access token can't do — it's how a revoked session
    (e.g. a deactivated provider) stops working.
    """
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(body.refresh_token))
    )
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked_at is not None or stored.expires_at <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user = await session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    access_token = create_access_token(user_id=user.id, role=user.role.value)
    # Keep the same refresh token (no rotation for now); hand it back for convenience.
    return TokenResponse(access_token=access_token, refresh_token=body.refresh_token)


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> User:
    """Return the authenticated user — a protected route that exercises the JWT gate."""
    return current_user
