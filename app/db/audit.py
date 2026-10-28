"""Append-only, hash-chained audit store.

Each institution's audit trail is a chain: every row commits to the hash of the
row before it, so editing or removing any event is detectable even by an
operator with database access. The database additionally rejects UPDATE, DELETE
and TRUNCATE at the trigger level, so the chain cannot be quietly rewritten
(NFR-SEC-07, BR-19).

The chain is computed from a canonical JSON serialisation of the event fields
and verified by replaying the same function over stored rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.db.access import TenantScopedStore

logger = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64

_INSERT_EVENT = """
INSERT INTO audit.audit_events (
    institution_id, event_type, actor_pseudonymous_id, course_id, assignment_id,
    policy_version, policy_level, allowed, reason, payload, prompt_sha256,
    previous_event_hash, event_hash, occurred_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11, $12, $13, $14)
RETURNING event_id, event_hash
"""

_HEAD = """
SELECT event_hash FROM audit.audit_events
WHERE institution_id = $1
ORDER BY event_id DESC
LIMIT 1
"""

_CHAIN = """
SELECT event_id, event_hash, previous_event_hash, event_type, actor_pseudonymous_id,
       course_id, assignment_id, policy_version, policy_level, allowed, reason,
       payload, prompt_sha256, occurred_at
FROM audit.audit_events
WHERE institution_id = $1
ORDER BY event_id ASC
"""


@dataclass(frozen=True)
class AuditRecord:
    event_id: int
    event_hash: str
    previous_event_hash: str


@dataclass(frozen=True)
class AuditVerificationResult:
    verified: bool
    events_checked: int
    first_bad_event_id: int | None = None
    detail: str = ""


def canonical_event_bytes(fields: Mapping[str, Any]) -> bytes:
    """Stable serialisation used for both hashing and verification."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_event_hash(fields: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_event_bytes(fields)).hexdigest()


def _hashable_fields(
    *,
    institution_id: str,
    event_type: str,
    actor_pseudonymous_id: str | None,
    course_id: str | None,
    assignment_id: str | None,
    policy_version: str | None,
    policy_level: str | None,
    allowed: bool | None,
    reason: str | None,
    payload: Mapping[str, Any],
    prompt_sha256: str | None,
    previous_event_hash: str,
    occurred_at: str,
) -> dict[str, Any]:
    return {
        "institution_id": institution_id,
        "event_type": event_type,
        "actor_pseudonymous_id": actor_pseudonymous_id,
        "course_id": course_id,
        "assignment_id": assignment_id,
        "policy_version": policy_version,
        "policy_level": policy_level,
        "allowed": allowed,
        "reason": reason,
        "payload": dict(payload),
        "prompt_sha256": prompt_sha256,
        "previous_event_hash": previous_event_hash,
        "occurred_at": occurred_at,
    }


class AuditStore:
    """Write and verify a tenant's append-only audit chain."""

    def __init__(self, store: TenantScopedStore) -> None:
        self.store = store

    async def append(
        self,
        *,
        event_type: str,
        actor_pseudonymous_id: str | None = None,
        course_id: str | None = None,
        assignment_id: str | None = None,
        policy_version: str | None = None,
        policy_level: str | None = None,
        allowed: bool | None = None,
        reason: str | None = None,
        payload: Mapping[str, Any] | None = None,
        prompt_sha256: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditRecord:
        """Append one event, linking it to the current chain head.

        The read of the head and the insert happen inside one transaction that
        holds a per-institution advisory lock, so concurrent writers cannot fork
        the chain.
        """
        if not event_type or not event_type.strip():
            raise ValueError("audit events require an event_type")
        timestamp = (occurred_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        body = _hashable_fields(
            institution_id=self.store.institution_id,
            event_type=event_type,
            actor_pseudonymous_id=actor_pseudonymous_id,
            course_id=course_id,
            assignment_id=assignment_id,
            policy_version=policy_version,
            policy_level=policy_level,
            allowed=allowed,
            reason=reason,
            payload=payload or {},
            prompt_sha256=prompt_sha256,
            previous_event_hash="",  # replaced per attempt below
            occurred_at=timestamp,
        )

        async with self.store.scoped_connection() as connection:
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))",
                f"audit:{self.store.institution_id}",
            )
            previous = await connection.fetchval(_HEAD, self.store.institution_id) or GENESIS_HASH
            body["previous_event_hash"] = previous
            event_hash = compute_event_hash(body)
            row = await connection.fetchrow(
                _INSERT_EVENT,
                self.store.institution_id,
                event_type,
                actor_pseudonymous_id,
                course_id,
                assignment_id,
                policy_version,
                policy_level,
                allowed,
                reason,
                json.dumps(dict(payload or {}), sort_keys=True),
                prompt_sha256,
                previous,
                event_hash,
                datetime.fromisoformat(timestamp),
            )
        return AuditRecord(event_id=row["event_id"], event_hash=row["event_hash"],
                           previous_event_hash=previous)

    async def verify(self) -> AuditVerificationResult:
        """Replay the chain and report the first event that breaks it."""
        rows: Sequence[Any] = await self.store.fetch(_CHAIN, self.store.institution_id)
        previous = GENESIS_HASH
        for row in rows:
            if row["previous_event_hash"] != previous:
                return AuditVerificationResult(
                    verified=False,
                    events_checked=len(rows),
                    first_bad_event_id=row["event_id"],
                    detail="chain link does not match the previous event hash",
                )
            body = _hashable_fields(
                institution_id=self.store.institution_id,
                event_type=row["event_type"],
                actor_pseudonymous_id=row["actor_pseudonymous_id"],
                course_id=row["course_id"],
                assignment_id=row["assignment_id"],
                policy_version=row["policy_version"],
                policy_level=row["policy_level"],
                allowed=row["allowed"],
                reason=row["reason"],
                payload=json.loads(row["payload"]) if isinstance(row["payload"], str) else dict(row["payload"]),
                prompt_sha256=row["prompt_sha256"],
                previous_event_hash=row["previous_event_hash"],
                occurred_at=row["occurred_at"].astimezone(UTC).isoformat(),
            )
            if compute_event_hash(body) != row["event_hash"]:
                return AuditVerificationResult(
                    verified=False,
                    events_checked=len(rows),
                    first_bad_event_id=row["event_id"],
                    detail="stored event hash does not match its contents",
                )
            previous = row["event_hash"]
        return AuditVerificationResult(verified=True, events_checked=len(rows))

    async def count(self) -> int:
        return int(
            await self.store.fetchval(
                "SELECT count(*) FROM audit.audit_events WHERE institution_id = $1",
                self.store.institution_id,
            )
            or 0
        )
