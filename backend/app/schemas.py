"""API request/response shapes (Pydantic models, NOT database tables).

These are the public contract of the endpoints: what the client must send and
what it gets back. Keeping them separate from the `models.py` tables means the
DB shape and the wire shape can differ — e.g. we never expose `password_hash`.
"""

from pydantic import BaseModel, ConfigDict

from app.models import Role


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
