"""Create all database tables from the SQLModel classes.

No Alembic yet, so this is our table-creation mechanism (Option A): import every
model so it registers on `SQLModel.metadata`, then issue CREATE TABLE for each.

`create_all` is idempotent — it only creates tables that don't already exist, so
re-running is safe. It does NOT alter existing tables (that's what migrations are
for; we'll add Alembic if/when the schema needs to evolve in place).

Run from the backend/ directory:
    .venv\\Scripts\\python init_db.py
"""

import asyncio

from sqlmodel import SQLModel

from app import models  # noqa: F401 — importing registers every table on SQLModel.metadata
from app.db import engine


async def main() -> None:
    # create_all is a synchronous SQLAlchemy call; run_sync bridges it onto our
    # async connection so we reuse the one engine/pool the app already owns.
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    tables = ", ".join(SQLModel.metadata.tables)
    print(f"Created/verified {len(SQLModel.metadata.tables)} tables: {tables}")


if __name__ == "__main__":
    asyncio.run(main())
