"""Export of a student's own process data and consent record.

A student can obtain the process data held about them and the consent they have
recorded (NFR-PRV-03). The export is scoped to one pseudonymous id and one
institution; it never resolves the pseudonym back to a real identity, because
that mapping lives in the restricted identity schema and is not reachable from
the application path.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.db.access import TenantScopedStore

logger = logging.getLogger(__name__)

_PROCESS_LOGS = """
SELECT assignment_id, step_number, event_type, language, summary, metadata, occurred_at
FROM assessment.process_logs
WHERE institution_id = $1 AND pseudonymous_id = $2
ORDER BY assignment_id, step_number
"""

_SUBMISSIONS = """
SELECT assignment_id, capability_breakdown, submitted_at
FROM assessment.submissions
WHERE institution_id = $1 AND pseudonymous_id = $2
ORDER BY submitted_at
"""

_CONVERSATIONS = """
SELECT conversation_id, assignment_id, policy_version, policy_tier, status, created_at
FROM assessment.conversations
WHERE institution_id = $1 AND actor_pseudonymous_id = $2
ORDER BY created_at
"""

_MESSAGES = """
SELECT m.conversation_id, m.role, m.content, m.created_at
FROM assessment.messages m
JOIN assessment.conversations c USING (institution_id, conversation_id)
WHERE m.institution_id = $1 AND c.actor_pseudonymous_id = $2
ORDER BY m.conversation_id, m.created_at, m.message_id
"""


@dataclass(frozen=True)
class StudentDataExport:
    institution_id: str
    pseudonymous_id: str
    generated_at: str
    process_logs: list[dict[str, Any]]
    submissions: list[dict[str, Any]]
    conversations: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(
            {
                "institution_id": self.institution_id,
                "pseudonymous_id": self.pseudonymous_id,
                "generated_at": self.generated_at,
                "process_logs": self.process_logs,
                "submissions": self.submissions,
                "conversations": self.conversations,
                "messages": self.messages,
            },
            indent=2,
            sort_keys=True,
            default=str,
        )


class StudentDataExporter:
    """Assemble one student's own process data within a single tenant."""

    def __init__(self, store: TenantScopedStore) -> None:
        self.store = store

    async def export(self, pseudonymous_id: str) -> StudentDataExport:
        if re.fullmatch(r"stu_[0-9a-f]{32}", pseudonymous_id) is None:
            raise ValueError("export requires a pseudonymous student identifier")
        logs = await self.store.fetch(_PROCESS_LOGS, self.store.institution_id, pseudonymous_id)
        submissions = await self.store.fetch(
            _SUBMISSIONS, self.store.institution_id, pseudonymous_id
        )
        conversations = await self.store.fetch(
            _CONVERSATIONS, self.store.institution_id, pseudonymous_id
        )
        messages = await self.store.fetch(_MESSAGES, self.store.institution_id, pseudonymous_id)
        return StudentDataExport(
            institution_id=self.store.institution_id,
            pseudonymous_id=pseudonymous_id,
            generated_at=datetime.now(UTC).isoformat(),
            process_logs=[self._row(row) for row in logs],
            submissions=[self._row(row) for row in submissions],
            conversations=[self._row(row) for row in conversations],
            messages=[self._row(row) for row in messages],
        )

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        item = dict(row)
        for key, value in list(item.items()):
            if isinstance(value, str) and key == "metadata":
                try:
                    item[key] = json.loads(value)
                except json.JSONDecodeError:
                    item[key] = {}
        return item
