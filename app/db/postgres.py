"""Async PostgreSQL pool and schema-as-code migration runner."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MIGRATION_LOCK_KEY = 8_140_197_301_551_889


class MigrationError(RuntimeError):
    pass


def migration_files(directory: Path | None = None) -> list[Path]:
    """Return the ordered ``*.sql`` migration files for the data layer."""
    root = directory or MIGRATIONS_DIR
    return sorted(path for path in root.glob("*.sql") if path.is_file())


class PostgresStore:
    """Own the connection pool and apply versioned schema migrations.

    Schema is defined as code and applied through the release pipeline
    (Project Report §8). Migrations run under an advisory lock so a rolling
    deployment cannot apply the same file twice.
    """

    def __init__(self, settings: Settings | None = None, *, dsn: str | None = None) -> None:
        self.settings = settings or get_settings()
        if dsn is None:
            secret = self.settings.postgres_dsn
            dsn = secret.get_secret_value() if secret is not None else None
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresStore is not connected; call connect() first")
        return self._pool

    async def connect(self, dsn: str | None = None) -> None:
        if self._pool is not None:
            return
        target = dsn or self._dsn
        if not target:
            raise RuntimeError("POSTGRES_DSN is not configured")
        self._pool = await asyncpg.create_pool(
            dsn=target,
            min_size=self.settings.postgres_pool_min_size,
            max_size=self.settings.postgres_pool_max_size,
            command_timeout=self.settings.postgres_command_timeout_seconds,
            statement_cache_size=self.settings.postgres_statement_cache_size,
            max_inactive_connection_lifetime=300.0,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            yield connection

    async def migrate(self, directory: Path | None = None) -> list[str]:
        """Apply every pending migration file in lexical order."""
        files = migration_files(directory)
        applied: list[str] = []
        async with self.pool.acquire() as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    filename   text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            # The backup role must be able to read the migration ledger, or a
            # logical dump cannot lock it (NFR-REL-02).
            await connection.execute(
                """
                DO $$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'resonance_backup') THEN
                        GRANT SELECT ON public.schema_migrations TO resonance_backup;
                    END IF;
                END
                $$
                """
            )
            await connection.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_KEY)
            try:
                for path in files:
                    name = path.name
                    already = await connection.fetchval(
                        "SELECT 1 FROM public.schema_migrations WHERE filename = $1", name
                    )
                    if already:
                        continue
                    script = path.read_text(encoding="utf-8")
                    async with connection.transaction():
                        await connection.execute(script)
                        await connection.execute(
                            "INSERT INTO public.schema_migrations (filename) VALUES ($1)", name
                        )
                    applied.append(name)
                    logger.info("applied data-layer migration %s", name)
            finally:
                await connection.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_KEY)
        return applied

    async def health_check(self) -> bool:
        try:
            return bool(await self.pool.fetchval("SELECT 1"))
        except Exception:
            logger.warning("postgres health check failed", exc_info=True)
            return False

    async def table_names(self, schema: str) -> Sequence[str]:
        rows = await self.pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename", schema
        )
        return [row["tablename"] for row in rows]
