"""Tenant-scoped data access for the relational store.

Every call carries an institution identifier. A call without one is rejected
before it reaches PostgreSQL, and the identifier is bound as the transaction's
``app.institution_id`` so row-level security applies on top of the explicit
filter. Two independent checks means a missing ``WHERE`` clause still cannot
leak another tenant's rows (FR-AUT-03, NFR-SCL-02).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)

_INSTITUTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

# Schemas the application role may reach. The identity schema is deliberately
# absent: the pseudonym mapping is not on the application path.
APPLICATION_SCHEMAS = ("core", "assessment", "analytics", "audit")


class TenantScopeError(PermissionError):
    """Raised when a data-layer call omits or forges its tenant scope."""


def validate_institution_id(institution_id: object) -> str:
    if not isinstance(institution_id, str) or not _INSTITUTION_ID.match(institution_id):
        raise TenantScopeError("a valid institution_id is required for every data-layer call")
    return institution_id


class TenantScopedStore:
    """Execute queries under a single institution's scope.

    Instantiate per request with the caller's institution id; the scope is fixed
    for the lifetime of the object so it cannot be changed mid-transaction.
    """

    def __init__(self, store: PostgresStore, institution_id: str) -> None:
        self.store = store
        self.institution_id = validate_institution_id(institution_id)

    @asynccontextmanager
    async def scoped_connection(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield a connection inside a transaction bound to this institution.

        Use this for multi-statement work (for example reading a chain head and
        inserting the next link) that must observe one consistent tenant scope.
        """
        async with self.store.pool.acquire() as connection:
            async with connection.transaction():
                # set_config with is_local=true scopes the value to this
                # transaction; it is reset when the transaction ends.
                await connection.execute(
                    "SELECT set_config('app.institution_id', $1, true)", self.institution_id
                )
                yield connection

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self.scoped_connection() as connection:
            return list(await connection.fetch(query, *args))

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self.scoped_connection() as connection:
            return await connection.fetchrow(query, *args)

    async def fetchval(self, query: str, *args: Any) -> Any:
        async with self.scoped_connection() as connection:
            return await connection.fetchval(query, *args)

    async def execute(self, query: str, *args: Any) -> str:
        async with self.scoped_connection() as connection:
            return await connection.execute(query, *args)

    async def executemany(self, query: str, args: Sequence[Sequence[Any]]) -> None:
        async with self.scoped_connection() as connection:
            await connection.executemany(query, args)
