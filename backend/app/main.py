from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import router as auth_router
from app.db import get_session
from app.encounters import router as encounters_router
from app.icd import router as icd_router
from app.patients import router as patients_router

# FastAPI is the ASGI application object. uvicorn (the server) imports this
# `app` and drives it. Everything we build — routes, middleware, startup hooks —
# hangs off this object.
app = FastAPI(title="Clinical Scribe API")

# The React frontend runs on a separate origin (Vite dev server on :5173), so the
# browser needs CORS permission to call this API cross-origin. Local dev only —
# in prod the SPA is served same-origin behind nginx, so this list tightens.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the feature routers onto the app.
app.include_router(auth_router)
app.include_router(encounters_router)
app.include_router(icd_router)
app.include_router(patients_router)


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
