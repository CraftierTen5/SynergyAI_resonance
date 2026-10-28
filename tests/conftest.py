"""Shared fixtures for data-layer tests.

The tests exercise real PostgreSQL and real Redis. They are skipped when the
services are not reachable so the suite still runs on a machine without them.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.core.config import Settings
from app.db.postgres import PostgresStore
from app.db.redis_store import RedisStore

MIGRATOR_DSN = os.environ.get(
    "TEST_POSTGRES_MIGRATOR_DSN",
    "postgresql://resonance_migrator:migrator_local_dev_password@localhost:5432/resonanceai",
)
APP_DSN = os.environ.get(
    "TEST_POSTGRES_APP_DSN",
    "postgresql://resonance_app:app_local_dev_password@localhost:5432/resonanceai",
)
REDIS_DSN = os.environ.get("TEST_REDIS_DSN", "redis://localhost:6379/15")

SCHEMAS = ("core", "assessment", "analytics", "audit", "identity")


def pytest_configure(config):
    config.addinivalue_line("markers", "data_layer: requires live PostgreSQL and Redis")


@pytest_asyncio.fixture
async def pg_migrator():
    store = PostgresStore(dsn=MIGRATOR_DSN)
    try:
        await store.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
    await store.migrate()
    try:
        yield store
    finally:
        await store.close()


@pytest_asyncio.fixture
async def pg_app():
    store = PostgresStore(dsn=APP_DSN)
    try:
        await store.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL application role is not reachable: {exc}")
    try:
        yield store
    finally:
        await store.close()


@pytest_asyncio.fixture
async def clean_db(pg_migrator):
    """Empty every data-layer table, working around the audit guard triggers."""
    async with pg_migrator.pool.acquire() as connection:
        await connection.execute(
            "ALTER TABLE audit.audit_events DISABLE TRIGGER audit_events_no_truncate"
        )
        for schema in SCHEMAS:
            tables = await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = $1", schema
            )
            for row in tables:
                await connection.execute(
                    f'TRUNCATE TABLE "{schema}"."{row["tablename"]}" RESTART IDENTITY CASCADE'
                )
        await connection.execute(
            "ALTER TABLE audit.audit_events ENABLE TRIGGER audit_events_no_truncate"
        )
    return pg_migrator


@pytest_asyncio.fixture
async def redis_store():
    store = RedisStore(client=None)
    try:
        await store.connect(REDIS_DSN)
        await store.client.ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Redis is not reachable: {exc}")
    await store.client.flushdb()
    try:
        yield store
    finally:
        try:
            await store.client.flushdb()
        finally:
            await store.close()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        POSTGRES_DSN=MIGRATOR_DSN,
        REDIS_DSN="redis://localhost:6379/15",
        PROCESS_LOG_PSEUDONYM_KEY="test-pseudonym-key-that-is-at-least-32-bytes-long",
        PROCESS_LOG_BUFFER_TTL_SECONDS=120,
        ANALYTICS_MIN_COHORT_SIZE=5,
    )


async def seed_institution(store: PostgresStore, institution_id: str, name: str = "Test Institute"):
    async with store.pool.acquire() as connection:
        await connection.execute(
            "INSERT INTO core.institutions (institution_id, name) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING",
            institution_id,
            name,
        )
