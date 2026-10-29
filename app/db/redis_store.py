"""Redis cache and stream store with tenant-prefixed keys.

Redis holds only hot, short-lived state: sessions, rate-limit counters, policy
cache entries, in-flight conversation buffers, the short-lived raw process-log
buffer and the step-event stream. Nothing here is a system of record, so losing
Redis degrades the experience but never loses a student's durable work
(DL-4).

Tenant separation in Redis is by key prefix: every key is namespaced with a
digest of the institution id, so a bug that forgets a filter still cannot read
across institutions in normal operation (Project Report §6.1).
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

KEY_NAMESPACE = "resonanceai"


def tenant_prefix(institution_id: str) -> str:
    """Stable, non-reversible key namespace for one institution."""
    digest = hashlib.sha256(institution_id.encode("utf-8")).hexdigest()[:24]
    return f"{KEY_NAMESPACE}:{{{digest}}}"


def tenant_key(institution_id: str, *parts: str) -> str:
    """Build a tenant-scoped key, e.g. ``resonanceai:{<digest>}:session:abc``."""
    suffix = ":".join(part.strip(":") for part in parts if part)
    prefix = tenant_prefix(institution_id)
    return f"{prefix}:{suffix}" if suffix else prefix


class RedisStore:
    """Own the async Redis client used by every cache and stream consumer."""

    def __init__(self, settings: Settings | None = None, *, client: Any | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("RedisStore is not connected; call connect() first")
        return self._client

    async def connect(self, dsn: str | None = None) -> None:
        if self._client is not None:
            return
        target = dsn or self.settings.redis_dsn
        self._client = redis.from_url(
            target,
            socket_timeout=self.settings.redis_socket_timeout_seconds,
            socket_connect_timeout=self.settings.redis_socket_timeout_seconds,
            decode_responses=False,
            health_check_interval=30,
        )

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def health_check(self) -> bool:
        try:
            return bool(await self.client.ping())
        except Exception:
            logger.warning("redis health check failed", exc_info=True)
            return False
