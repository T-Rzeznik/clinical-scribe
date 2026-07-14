"""Shared FastAPI dependencies for authenticated routes.

`get_current_user` is the gate every protected endpoint sits behind: it turns an
`Authorization: Bearer <jwt>` header into the actual User row, or a 401.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import Role, User
from app.security import decode_access_token

# Extracts the "Authorization: Bearer <token>" header (and powers the Authorize
# button in /docs). Returns 403 automatically if the header is missing.
bearer_scheme = HTTPBearer()

# One shared 401 — never reveal WHY (expired vs forged vs unknown user).
_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """Decode the access token, load the user, ensure they're still active."""
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:  # covers expired AND invalid/tampered — one 401 for both
        raise _credentials_error

    user_id = payload.get("sub")
    if user_id is None:
        raise _credentials_error

    # We re-load the user every request rather than trusting the token blindly, so a
    # user deactivated mid-session (is_active=False) loses access immediately.
    user = await session.get(User, int(user_id))
    if user is None or not user.is_active:
        raise _credentials_error

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Dependency for admin-only routes. Builds on get_current_user, then checks role."""
    if current_user.role != Role.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
