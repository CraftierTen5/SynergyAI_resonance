"""Step-event stream between the AI service and the assessment engine.

3B publishes each tutoring step onto a Redis stream; 3C consumes it. The stream
is capped with an approximate max length so it cannot grow without bound, and it
is tenant-scoped. A consumer group lets 3C resume after a restart without
replaying work it has already acknowledged (FR-WSP-03, NFR-REL-03).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings, get_settings
from app.db.redis_store import RedisStore, tenant_key

logger = logging.getLogger(__name__)

CONSUMER_GROUP = "assessment"


@dataclass(frozen=True)
class StreamEntry:
    entry_id: str
    event: dict[str, Any]


class StepEventStream:
    """Publish and consume step events for one institution."""

    def __init__(self, redis_store: RedisStore, settings: Settings | None = None) -> None:
        self.redis_store = redis_store
        self.settings = settings or get_settings()

    def stream_key(self, institution_id: str) -> str:
        return tenant_key(institution_id, "stepevents")

    async def publish(
        self, institution_id: str, event: Mapping[str, Any], *, maxlen: int | None = None
    ) -> str:
        fields = {str(key): self._encode(value) for key, value in event.items()}
        entry_id = await self.redis_store.client.xadd(
            self.stream_key(institution_id),
            fields,
            maxlen=maxlen or self.settings.ai_log_stream_maxlen,
            # Exact trimming: this stream is a work queue, so the cap must be a
            # firm bound rather than a node-block approximation.
            approximate=False,
        )
        return entry_id.decode("utf-8") if isinstance(entry_id, bytes) else str(entry_id)

    async def ensure_group(self, institution_id: str) -> None:
        try:
            await self.redis_store.client.xgroup_create(
                self.stream_key(institution_id), CONSUMER_GROUP, id="0", mkstream=True
            )
        except Exception as exc:  # group already exists
            if "BUSYGROUP" not in str(exc):
                raise

    async def consume(
        self,
        institution_id: str,
        consumer: str,
        *,
        count: int = 50,
        block_ms: int = 1_000,
    ) -> list[StreamEntry]:
        """Read new events for a consumer and acknowledge them on return."""
        key = self.stream_key(institution_id)
        response = await self.redis_store.client.xreadgroup(
            CONSUMER_GROUP, consumer, {key: ">"}, count=count, block=block_ms
        )
        entries: list[StreamEntry] = []
        for _stream, messages in response or []:
            for entry_id, fields in messages:
                if isinstance(entry_id, bytes):
                    entry_id = entry_id.decode("utf-8")
                entries.append(StreamEntry(str(entry_id), self._decode_fields(fields)))
        return entries

    async def acknowledge(self, institution_id: str, *entry_ids: str) -> int:
        if not entry_ids:
            return 0
        return int(
            await self.redis_store.client.xack(
                self.stream_key(institution_id), CONSUMER_GROUP, *entry_ids
            )
        )

    async def length(self, institution_id: str) -> int:
        return int(await self.redis_store.client.xlen(self.stream_key(institution_id)))

    @staticmethod
    def _encode(value: Any) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, sort_keys=True, default=str)

    @staticmethod
    def _decode_fields(fields: Mapping[Any, Any]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for raw_key, raw_value in fields.items():
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else str(raw_key)
            value = raw_value.decode("utf-8") if isinstance(raw_value, bytes) else raw_value
            decoded[key] = value
        return decoded
