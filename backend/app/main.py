from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

# FastAPI is the ASGI application object. uvicorn (the server) imports this
# `app` and drives it. Everything we build — routes, middleware, startup hooks —
# hangs off this object.
app = FastAPI(title="Clinical Scribe API")


@app.get("/health")
def health() -> dict:
    """Liveness check: confirms the API process is up and serving requests."""
    return {"status": "ok", "service": "clinical-scribe-api"}


@app.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> dict:
    """Readiness check: borrows a pooled connection and runs a trivial query.

    `Depends(get_session)` is FastAPI dependency injection — it calls get_session()
    for us, hands the yielded AsyncSession in as `session`, and cleans it up after.
    """
    result = await session.execute(text("SELECT 1"))
    return {"status": "ok", "db": result.scalar_one()}
