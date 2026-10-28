"""Short-lived raw process buffer and the anonymised persistence path.

Raw process data is the most sensitive thing the platform touches. It is held in
Redis only, under an enforced TTL, and is never written to PostgreSQL verbatim.
The persistence service consumes buffered events, anonymises them through the
3C boundary, writes the anonymised row to the assessment schema, and then
removes the raw entry. If the process dies between the write and the removal the
raw entry simply expires; it is never copied to durable storage (Project Report
§6.2, NFR-PRV-01).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from app.core.config import Settings, get_settings
from app.db.access import TenantScopedStore
from app.db.redis_store import RedisStore, tenant_key
from app.services.assessment.anonymization import ProcessLogAnonymizer

logger = logging.getLogger(__name__)

_INSERT_LOG = """
INSERT INTO assessment.process_logs (
    institution_id, assignment_id, pseudonymous_id, step_number, event_type,
    language, prompt_digest, summary, metadata, occurred_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10)
RETURNING log_id
"""


@dataclass(frozen=True)
class PersistResult:
    written: int
    skipped: int


class ProcessLogBuffer:
    """Buffer raw process events in Redis under a mandatory TTL."""

    def __init__(self, redis_store: RedisStore, settings: Settings | None = None) -> None:
        self.redis_store = redis_store
        self.settings = settings or get_settings()

    @staticmethod
    def _key(institution_id: str, assignment_id: str, pseudonymous_id: str) -> str:
        return tenant_key(institution_id, "processbuf", assignment_id, pseudonymous_id)

    async def append(
        self,
        institution_id: str,
        assignment_id: str,
        pseudonymous_id: str,
        event: Mapping[str, Any],
        *,
        ttl_seconds: int | None = None,
    ) -> int:
        """Append a raw event and (re)arm the buffer's expiry."""
        ttl = ttl_seconds or self.settings.process_log_buffer_ttl_seconds
        key = self._key(institution_id, assignment_id, pseudonymous_id)
        payload = json.dumps(dict(event), sort_keys=True, default=str)
        async with self.redis_store.client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, payload)
            pipe.expire(key, ttl)
            results = await pipe.execute()
        return int(results[0])

    async def read(
        self, institution_id: str, assignment_id: str, pseudonymous_id: str
    ) -> list[dict[str, Any]]:
        raw_entries = await self.redis_store.client.lrange(
            self._key(institution_id, assignment_id, pseudonymous_id), 0, -1
        )
        events: list[dict[str, Any]] = []
        for raw in raw_entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning("discarding malformed raw process event")
                continue
            if isinstance(parsed, dict):
                events.append(parsed)
        return events

    async def time_to_live(
        self, institution_id: str, assignment_id: str, pseudonymous_id: str
    ) -> int:
        ttl = await self.redis_store.client.ttl(
            self._key(institution_id, assignment_id, pseudonymous_id)
        )
        return int(ttl)

    async def clear(self, institution_id: str, assignment_id: str, pseudonymous_id: str) -> None:
        await self.redis_store.client.delete(
            self._key(institution_id, assignment_id, pseudonymous_id)
        )


class ProcessLogPersistenceService:
    """Anonymise buffered raw events and persist only the sanitised form."""

    def __init__(
        self,
        buffer: ProcessLogBuffer,
        anonymizer: ProcessLogAnonymizer,
        store: TenantScopedStore,
    ) -> None:
        self.buffer = buffer
        self.anonymizer = anonymizer
        self.store = store

    async def persist(
        self,
        institution_id: str,
        assignment_id: str,
        pseudonymous_id: str,
        *,
        clear_buffer: bool = True,
    ) -> PersistResult:
        if institution_id != self.store.institution_id:
            raise PermissionError("buffer tenant does not match the store tenant")
        events = await self.buffer.read(institution_id, assignment_id, pseudonymous_id)
        written = 0
        skipped = 0
        step_number = await self._next_step_number(assignment_id, pseudonymous_id)
        for event in events:
            try:
                sanitized = self.anonymizer.anonymize(event)
            except (TypeError, ValueError):
                # Without institution scope an event cannot be pseudonymised, so
                # it must not be persisted at all.
                skipped += 1
                continue
            await self.store.execute(
                _INSERT_LOG,
                institution_id,
                assignment_id,
                sanitized.get("pseudonymous_id", pseudonymous_id),
                step_number,
                str(sanitized.get("event_type") or sanitized.get("type") or "step")[:120],
                self._optional_text(sanitized.get("language"), 35),
                self._optional_text(sanitized.get("prompt_digest"), 128),
                self._optional_text(
                    sanitized.get("summary")
                    or sanitized.get("response_summary")
                    or sanitized.get("response"),
                    4_000,
                ),
                json.dumps(self._metadata(sanitized)),
                self._occurred_at(sanitized),
            )
            step_number += 1
            written += 1
        if clear_buffer and events:
            await self.buffer.clear(institution_id, assignment_id, pseudonymous_id)
        return PersistResult(written=written, skipped=skipped)

    async def _next_step_number(self, assignment_id: str, pseudonymous_id: str) -> int:
        current = await self.store.fetchval(
            """
            SELECT COALESCE(max(step_number), 0) FROM assessment.process_logs
            WHERE institution_id = $1 AND assignment_id = $2 AND pseudonymous_id = $3
            """,
            self.store.institution_id,
            assignment_id,
            pseudonymous_id,
        )
        return int(current or 0) + 1

    @staticmethod
    def _optional_text(value: Any, limit: int) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()[:limit]

    @staticmethod
    def _metadata(event: Mapping[str, Any]) -> dict[str, Any]:
        reserved = {
            "institution_id",
            "pseudonymous_id",
            "event_type",
            "type",
            "language",
            "prompt_digest",
            "summary",
            "response_summary",
            "response",
            "occurred_at",
            "timestamp",
        }
        metadata: dict[str, Any] = {}
        for key, value in event.items():
            if key in reserved:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                metadata[str(key)] = value
            elif isinstance(value, (list, dict)):
                metadata[str(key)] = value
        return metadata

    @staticmethod
    def _occurred_at(event: Mapping[str, Any]) -> datetime:
        raw = event.get("occurred_at") or event.get("timestamp")
        if isinstance(raw, str):
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
        return datetime.now(UTC)
