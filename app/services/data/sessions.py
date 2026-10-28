"""Session and rate-limit state in Redis.

Sessions are created at sign-in, expire on their own, and are revoked at
logout. A session record holds only what the gateway needs to authorise a
request: the tenant, the account reference and the role. No credential material
is stored here (Project Report §6.2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.config import Settings, get_settings
from app.db.redis_store import RedisStore, tenant_key

logger = logging.getLogger(__name__)

_SESSION_FIELD = "session"


class SessionScopeError(PermissionError):
    pass


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    institution_id: str
    account_ref: str
    role: str
    created_at: str


class SessionStore:
    """Create, read, refresh and revoke tenant-scoped sessions."""

    def __init__(self, redis_store: RedisStore, settings: Settings | None = None) -> None:
        self.redis_store = redis_store
        self.settings = settings or get_settings()

    @staticmethod
    def _key(institution_id: str, session_id: str) -> str:
        token = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return tenant_key(institution_id, "session", token)

    async def create(
        self,
        institution_id: str,
        account_ref: str,
        role: str,
        *,
        session_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> SessionRecord:
        if not institution_id or not account_ref or role not in {"student", "faculty", "admin", "auditor"}:
            raise SessionScopeError("session requires institution, account and a valid role")
        session_id = session_id or secrets.token_urlsafe(32)
        record = SessionRecord(
            session_id=session_id,
            institution_id=institution_id,
            account_ref=account_ref,
            role=role,
            created_at=datetime.now(UTC).isoformat(),
        )
        ttl = ttl_seconds or self.settings.session_ttl_seconds
        await self.redis_store.client.set(
            self._key(institution_id, session_id),
            json.dumps(record.__dict__, sort_keys=True),
            ex=ttl,
        )
        return record

    async def get(self, institution_id: str, session_id: str) -> SessionRecord | None:
        raw = await self.redis_store.client.get(self._key(institution_id, session_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("discarding malformed session record")
            return None
        if data.get("institution_id") != institution_id:
            # Defence in depth: a key collision or prefix bug must not admit a
            # session belonging to another institution.
            logger.error("session tenant mismatch rejected")
            return None
        return SessionRecord(**data)

    async def revoke(self, institution_id: str, session_id: str) -> bool:
        removed = await self.redis_store.client.delete(self._key(institution_id, session_id))
        return bool(removed)
