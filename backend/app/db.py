from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# --- The engine (and its connection pool) ---
#
# Create ONE engine for the whole application, at import time — never per request.
# The engine owns a pool of live connections to Postgres. When a request needs the
# DB, it BORROWS a connection from the pool and RETURNS it when done. Without this,
# every request would pay for a full TCP handshake + auth round-trip to Postgres,
# which is exactly what the requirements forbid.
#
# Because our URL is `postgresql+asyncpg://...`, SQLAlchemy uses its async engine
# and an async-aware connection pool automatically.
engine = create_async_engine(
    settings.database_url,
    echo=False,          # True -> log every SQL statement (handy when debugging, noisy otherwise)
    pool_size=5,         # persistent connections kept open in the pool at steady state
    max_overflow=10,     # extra temporary connections allowed during bursts (max 15 total)
    pool_pre_ping=True,  # cheaply ping a connection before use; silently replace a dead one
                         # (matters behind RDS / NAT timeouts that can drop idle connections)
    pool_recycle=1800,   # proactively recycle a connection after 30 min so we never hand out
                         # one the server has already closed
)

# --- The session factory ---
#
# A "session" is a unit of work: it wraps a borrowed connection and tracks the
# objects/changes in one request. This factory stamps out AsyncSession objects
# bound to the engine above.
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep object attributes readable after commit (standard for async)
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields one database session per request.

    `async with` guarantees the session is closed when the request ends, which
    returns its connection to the pool (the connection is reused, NOT closed).
    Routes are responsible for calling commit()/rollback() on the work they do.
    """
    async with SessionLocal() as session:
        yield session
