"""Fail-closed policy evaluation and hash-chained audit hooks."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Callable, Mapping, TypedDict


class PolicyLevel(str, Enum):
    RED = "NO_AI"
    YELLOW = "PROCESS_DISCLOSURE"
    GREEN = "FULL_INTEGRATION"


MANDATORY_YELLOW_INSTRUCTION = (
    "You are an academic mentor. Do NOT provide direct complete solutions. "
    "Guide the student using Socratic questions in their preferred language."
)


class PolicyDecision(TypedDict):
    allowed: bool
    reason: str
    system_instruction: str
    log_required: bool


@dataclass(frozen=True)
class PolicyAuditEvent:
    """Privacy-minimised audit record; prompt content is represented by a digest."""

    timestamp: str
    course_id: str | None
    assignment_id: str | None
    policy_version: str | None
    policy_level: str
    allowed: bool
    reason: str
    log_required: bool
    prompt_sha256: str
    previous_event_hash: str
    event_hash: str


class PolicyAuditWriteError(RuntimeError):
    """Raised when the audit sink cannot accept a decision event."""


AuditHook = Callable[[PolicyAuditEvent], None]


class PolicyGuardEngine:
    """Evaluate assignment policy before any prompt is sent to an AI provider.

    ``tier_definitions`` maps a PolicyLevel (or its enum name/value) to either
    an instruction string or a mapping with ``system_instruction`` and
    ``log_required``. An optional synchronous audit hook should append the
    hash-chained event to the institution's durable, append-only audit store.
    """

    def __init__(
        self,
        course_policy: Mapping[str, object],
        tier_definitions: Mapping[PolicyLevel | str, object],
        audit_hook: AuditHook | None = None,
    ) -> None:
        self.course_policy = dict(course_policy)
        self.tier_definitions = dict(tier_definitions)
        self.audit_hook = audit_hook
        self._audit_events: list[PolicyAuditEvent] = []
        self._previous_hash = "0" * 64
        self._audit_lock = threading.Lock()

    def validate_request(self, student_prompt: str) -> PolicyDecision:
        """Return exactly the access flags required by the policy contract.

        Missing or malformed policy metadata is denied. The raw prompt is not
        persisted; only its SHA-256 digest is included in the audit event.
        """
        level = self._policy_level()
        tier_config = self._tier_config(level) if level is not None else {}

        if level is None:
            decision: PolicyDecision = {
                "allowed": False,
                "reason": "Policy tier is missing or invalid; AI access is denied.",
                "system_instruction": "",
                "log_required": True,
            }
            audit_level = "INVALID"
        elif level is PolicyLevel.RED:
            decision = {
                "allowed": False,
                "reason": "AI use is prohibited for this assignment.",
                "system_instruction": "",
                "log_required": bool(tier_config.get("log_required", False)),
            }
            audit_level = level.value
        elif level is PolicyLevel.YELLOW:
            configured = str(tier_config.get("system_instruction", "")).strip()
            instruction = "\n\n".join(
                part for part in (configured, MANDATORY_YELLOW_INSTRUCTION) if part
            )
            decision = {
                "allowed": True,
                "reason": "AI use is allowed with process disclosure; guided tutoring applies.",
                "system_instruction": instruction,
                "log_required": True,
            }
            audit_level = level.value
        else:
            decision = {
                "allowed": True,
                "reason": "AI use is allowed under the assignment policy.",
                "system_instruction": str(tier_config.get("system_instruction", "")),
                "log_required": bool(tier_config.get("log_required", False)),
            }
            audit_level = level.value

        self._record_evaluation(student_prompt, audit_level, decision)
        return decision

    def audit_events(self) -> tuple[PolicyAuditEvent, ...]:
        """Return a read-only snapshot of locally retained audit events."""
        with self._audit_lock:
            return tuple(self._audit_events)

    def _policy_level(self) -> PolicyLevel | None:
        raw = next(
            (
                self.course_policy[key]
                for key in ("policy_level", "policy_tier", "tier", "level")
                if key in self.course_policy
            ),
            None,
        )
        if isinstance(raw, PolicyLevel):
            return raw
        if isinstance(raw, str):
            try:
                return PolicyLevel[raw.strip().upper()]
            except KeyError:
                try:
                    return PolicyLevel(raw.strip().upper())
                except ValueError:
                    return None
        return None

    def _tier_config(self, level: PolicyLevel) -> dict[str, object]:
        candidates: tuple[PolicyLevel | str, ...] = (
            level,
            level.name,
            level.name.lower(),
            level.value,
        )
        value = next((self.tier_definitions[key] for key in candidates if key in self.tier_definitions), None)
        if isinstance(value, str):
            return {"system_instruction": value}
        if isinstance(value, Mapping):
            return dict(value)
        return {}

    def _record_evaluation(
        self, student_prompt: str, level: str, decision: PolicyDecision
    ) -> None:
        prompt_digest = hashlib.sha256(student_prompt.encode("utf-8")).hexdigest()
        with self._audit_lock:
            event_data = {
                "timestamp": datetime.now(UTC).isoformat(),
                "course_id": self._metadata_string("course_id"),
                "assignment_id": self._metadata_string("assignment_id"),
                "policy_version": self._metadata_string("policy_version"),
                "policy_level": level,
                "allowed": decision["allowed"],
                "reason": decision["reason"],
                "log_required": decision["log_required"],
                "prompt_sha256": prompt_digest,
                "previous_event_hash": self._previous_hash,
            }
            canonical = json.dumps(event_data, sort_keys=True, separators=(",", ":"))
            event_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            event = PolicyAuditEvent(**event_data, event_hash=event_hash)
            if self.audit_hook is not None:
                try:
                    self.audit_hook(event)
                except Exception as exc:
                    # Do not return an authorization decision that could not be audited.
                    raise PolicyAuditWriteError("policy audit event could not be recorded") from exc
            self._audit_events.append(event)
            self._previous_hash = event_hash

    def _metadata_string(self, key: str) -> str | None:
        value = self.course_policy.get(key)
        return str(value) if value is not None else None
