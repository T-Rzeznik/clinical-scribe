"""Low-level auth primitives: password hashing and JWT access tokens.

Kept separate from the route handlers so the "how" of crypto lives in one place
and the routes just call these helpers.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

# HS256 = symmetric signing: the SAME secret (settings.jwt_secret) both signs and
# verifies. Fine for a single backend that issues and checks its own tokens.
JWT_ALGORITHM = "HS256"


# --- Passwords ---

def hash_password(plain_password: str) -> str:
    """Hash a plaintext password for storage.

    bcrypt generates a random SALT and mixes it in, so two users with the same
    password get different hashes (defeats rainbow tables). The salt is stored
    INSIDE the returned hash string, so we don't track it separately.
    """
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(plain_password.encode(), salt)
    return hashed.decode()  # store as text in users.password_hash


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Check a login attempt against the stored hash.

    We never decrypt the stored hash (hashing is one-way). Instead bcrypt re-hashes
    the attempt using the salt embedded in `password_hash` and compares.
    """
    return bcrypt.checkpw(plain_password.encode(), password_hash.encode())


# --- JWT access tokens ---

def create_access_token(user_id: int, role: str) -> str:
    """Mint a signed, short-lived access token identifying the user.

    The payload ("claims") is readable by anyone, but the signature means nobody
    can forge or tamper with it without our secret. `exp` makes it self-expiring.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # "subject" — who the token is about (JWT convention: a string)
        "role": role,         # lets us gate admin routes without a DB hit
        "iat": now,           # issued-at
        "exp": now + timedelta(minutes=settings.jwt_access_ttl_minutes),  # expires
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Verify signature + expiry and return the claims.

    Raises jwt.ExpiredSignatureError if past `exp`, or jwt.InvalidTokenError if the
    signature is bad/tampered. Callers turn those into 401s.
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM])


# --- Refresh tokens ---
#
# Unlike a JWT, a refresh token is an OPAQUE random string — it carries no claims,
# it's just a lookup key into the refresh_tokens table. We hand the raw value to the
# client ONCE and store only its hash, so a DB leak can't be replayed.

def generate_refresh_token() -> str:
    """A cryptographically-random, URL-safe token (~256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(raw_token: str) -> str:
    """Hash a refresh token for storage/lookup.

    SHA-256 (fast, one-way) — NOT bcrypt. Bcrypt is deliberately slow to protect
    low-entropy, guessable passwords. A refresh token is already 256 random bits, so
    there's nothing to brute-force; we only need a non-reversible fingerprint. Fast
    also matters because we hash on every refresh to look the row up.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()
