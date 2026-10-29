"""Async SQLAlchemy repository for privacy-minimized agent run metadata."""

from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.protocols import AgentRunRepository
from app.database.models import AgentRun


class SQLAlchemyAgentRunRepository(AgentRunRepository):
    """Persist only an allowlist of audit metadata to the ``agent_runs`` table.

    ``session_factory`` is injected by the application composition layer so this
    repository does not create or own a database engine or connection.
    """

    def __init__(self, session_factory: Callable[[], Any]) -> None:
        self.session_factory = session_factory

    async def record(self, **fields: object) -> None:
        assignment = fields.get("assignment")
        assignment_id = fields.get("assignment_id") or getattr(assignment, "id", None)
        if not isinstance(assignment_id, str) or not assignment_id:
            raise ValueError("assignment_id is required to record an agent run")

        request_id = fields.get("request_id")
        status = fields.get("status")
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request_id is required to record an agent run")
        if not isinstance(status, str) or not status:
            raise ValueError("status is required to record an agent run")

        policy_version = fields.get(
            "policy_version", getattr(assignment, "policy_version", None)
        )
        if policy_version is not None and (
            isinstance(policy_version, bool) or not isinstance(policy_version, int)
        ):
            raise ValueError("policy_version must be an integer or None")

        latency_ms = fields.get("latency_ms")
        if latency_ms is not None and (
            isinstance(latency_ms, bool)
            or not isinstance(latency_ms, int)
            or latency_ms < 0
        ):
            raise ValueError("latency_ms must be a non-negative integer or None")

        row = AgentRun(
            request_id=request_id,
            assignment_id=assignment_id,
            policy_version=policy_version,
            model_version=self._optional_string(fields.get("model_version")),
            action=self._enum_value(fields.get("action")),
            assistance_mode=self._enum_value(fields.get("assistance_mode")),
            status=status,
            latency_ms=latency_ms,
            provider=self._optional_string(fields.get("provider")),
            error_code=self._optional_string(fields.get("error_code")),
        )

        async with self.session_factory() as session:
            if not isinstance(session, AsyncSession):
                raise TypeError("session_factory must create SQLAlchemy AsyncSession instances")
            session.add(row)
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @staticmethod
    def _enum_value(value: object) -> str | None:
        if value is None:
            return None
        enum_value = getattr(value, "value", value)
        if not isinstance(enum_value, str):
            raise ValueError("audit enum fields must be strings or string enums")
        return enum_value

    @staticmethod
    def _optional_string(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("audit metadata fields must be strings or None")
        return value
