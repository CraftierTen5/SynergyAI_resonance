"""Policy cache in front of the core schema.

3A reads the current tier and version from the cache; on a miss it reads the
core schema and refills the cache. A policy change writes a new version row and
then invalidates the cache entry, so a stale tier can never outlive the change
that superseded it (Project Report §6.2, FR-POL-01 to 08).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

from app.core.config import Settings, get_settings
from app.db.redis_store import RedisStore, tenant_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedPolicy:
    assignment_id: str
    policy_version: str
    policy_tier: str
    instruction_profile: str
    system_instruction: str
    log_required: bool


class PolicyCache:
    """Read-through cache for assignment policy decisions."""

    def __init__(self, redis_store: RedisStore, settings: Settings | None = None) -> None:
        self.redis_store = redis_store
        self.settings = settings or get_settings()

    @staticmethod
    def _key(institution_id: str, assignment_id: str) -> str:
        return tenant_key(institution_id, "policy", assignment_id)

    async def get(self, institution_id: str, assignment_id: str) -> CachedPolicy | None:
        if self.settings.policy_cache_ttl_seconds == 0:
            return None
        raw = await self.redis_store.client.get(self._key(institution_id, assignment_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return CachedPolicy(**json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            logger.warning("discarding malformed policy cache entry")
            return None

    async def set(self, institution_id: str, policy: CachedPolicy) -> None:
        ttl = self.settings.policy_cache_ttl_seconds
        if ttl == 0:
            return
        await self.redis_store.client.set(
            self._key(institution_id, policy.assignment_id),
            json.dumps(asdict(policy), sort_keys=True),
            ex=ttl,
        )

    async def invalidate(self, institution_id: str, assignment_id: str) -> None:
        await self.redis_store.client.delete(self._key(institution_id, assignment_id))

    async def get_or_load(self, institution_id: str, assignment_id: str, loader) -> CachedPolicy:
        """Return the cached policy or load it from the core schema on a miss."""
        cached = await self.get(institution_id, assignment_id)
        if cached is not None:
            return cached
        policy = await loader(institution_id, assignment_id)
        if policy is not None:
            await self.set(institution_id, policy)
        return policy
