"""Tenant-scoped lookup of authoritative assignments for tutor requests."""

from __future__ import annotations

from uuid import UUID
from collections.abc import Mapping

from app.agent.contracts import AgentResult, AssignmentContext, TrustedUserContext
from app.db.access import TenantScopedStore
from app.db.audit import AuditStore
from app.db.postgres import PostgresStore


class PostgresAssignmentService:
    """Load assignment policy only from the authenticated user's institution."""

    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    async def get_authorized_assignment(
        self, assignment_id: str, user: TrustedUserContext
    ) -> AssignmentContext:
        try:
            parsed_assignment_id = UUID(assignment_id)
        except (ValueError, TypeError) as exc:
            raise LookupError("Assignment not found") from exc

        tenant = TenantScopedStore(self.store, user.institution_id)
        row = await tenant.fetchrow(
            """
            SELECT a.assignment_id, a.institution_id, a.title,
                   c.title AS subject,
                   COALESCE(policy.policy_tier, a.policy_tier) AS policy_level,
                   COALESCE(policy.version_label, 'assignment-default') AS policy_version
            FROM core.assignments AS a
            JOIN core.courses AS c
              ON c.institution_id = a.institution_id
             AND c.course_id = a.course_id
            LEFT JOIN LATERAL (
                SELECT pv.policy_tier, pv.version_label
                FROM core.policy_versions AS pv
                WHERE pv.institution_id = a.institution_id
                  AND pv.assignment_id = a.assignment_id
                  AND pv.retired_at IS NULL
                ORDER BY pv.effective_from DESC, pv.created_at DESC
                LIMIT 1
            ) AS policy ON TRUE
            WHERE a.assignment_id = $1
              AND a.archived_at IS NULL
              AND c.archived_at IS NULL
            """,
            parsed_assignment_id,
        )
        if row is None:
            raise LookupError("Assignment not found")

        return AssignmentContext(
            assignment_id=str(row["assignment_id"]),
            institution_id=row["institution_id"],
            title=row["title"],
            subject=row["subject"],
            policy_level=row["policy_level"],
            policy_version=row["policy_version"],
        )


class PostgresTutorAuditService:
    """Write response metadata to the existing append-only tenant audit chain."""

    def __init__(self, store: PostgresStore) -> None:
        self.store = store

    async def record_run(
        self,
        *,
        user: TrustedUserContext,
        assignment: AssignmentContext,
        result: AgentResult,
        metadata: Mapping[str, object],
    ) -> None:
        scope = TenantScopedStore(self.store, user.institution_id)
        await AuditStore(scope).append(
            event_type="tutor_response",
            assignment_id=assignment.assignment_id,
            policy_version=assignment.policy_version,
            policy_level=assignment.policy_level,
            allowed=result.status == "completed",
            reason=str(metadata.get("policy_reason_code") or result.status),
            payload={
                "status": result.status,
                "intent": result.intent,
                "selected_action": result.selected_action,
                "model_version": result.model_version,
                "stage": metadata.get("stage"),
            },
        )
