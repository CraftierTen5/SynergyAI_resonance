#!/usr/bin/env python3
"""Apply the data-layer migrations.

For a fresh local database, run with a PostgreSQL administrator: the initial
migration creates roles and enables pgcrypto. Provision a restricted migration
identity separately for deployments.

    POSTGRES_DSN=postgresql://postgres:...@localhost/resonanceai \
        python scripts/bootstrap_data_layer.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.postgres import PostgresStore  # noqa: E402


async def main() -> int:
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if dsn:
        store = PostgresStore(dsn=dsn)
    else:
        # An empty shell variable masks the value in .env; let Settings load it.
        os.environ.pop("POSTGRES_DSN", None)
        store = PostgresStore()
    await store.connect()
    try:
        applied = await store.migrate()
        print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")
        for schema in ("core", "identity", "assessment", "audit", "analytics"):
            tables = await store.table_names(schema)
            print(f"  {schema}: {', '.join(tables) or '(none)'}")
    finally:
        await store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
