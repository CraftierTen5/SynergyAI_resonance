"""Fixed-window rate limiting in Redis.

Counters are tenant-scoped and window-scoped, and they reset automatically on
window rollover via a key TTL. The counter is incremented and its TTL set in a
single Lua script so a burst of concurrent requests cannot race the expiry and
leave a key without a TTL (Project Report §6.2).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.core.config import Settings, get_settings
from app.db.redis_store import RedisStore, tenant_key

logger = logging.getLogger(__name__)

_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimiter:
    """Count requests per subject per window and report the decision."""

    def __init__(self, redis_store: RedisStore, settings: Settings | None = None) -> None:
        self.redis_store = redis_store
        self.settings = settings or get_settings()

    @staticmethod
    def _key(institution_id: str, subject: str, window: int) -> str:
        return tenant_key(institution_id, "ratelimit", subject, str(window))

    async def check(
        self,
        institution_id: str,
        subject: str,
        *,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> RateLimitDecision:
        if not institution_id or not subject:
            raise ValueError("rate limiting requires an institution and subject")
        limit = limit or self.settings.default_rate_limit_requests
        window = window_seconds or self.settings.default_rate_limit_window_seconds
        window_id = int(time.time() // window)
        key = self._key(institution_id, subject, window_id)
        count = int(await self.redis_store.client.eval(_INCREMENT_SCRIPT, 1, key, window))
        remaining = max(0, limit - count)
        retry_after = window - int(time.time() % window)
        return RateLimitDecision(
            allowed=count <= limit,
            limit=limit,
            remaining=remaining,
            retry_after_seconds=max(1, retry_after),
        )

    async def reset(self, institution_id: str, subject: str) -> None:
        window = self.settings.default_rate_limit_window_seconds
        window_id = int(time.time() // window)
        await self.redis_store.client.delete(self._key(institution_id, subject, window_id))
