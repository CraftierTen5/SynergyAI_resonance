"""Structural interfaces used by the Layer 3 orchestrator.

Ports depend only on Pydantic domain contracts and Python abstractions. They
do not import HTTP routing, ORM models, provider clients, or database sessions.
"""

from collections.abc import Mapping
from typing import Literal, Protocol, runtime_checkable

from app.agent.contracts import (
    AgentResult,
    AssignmentContext,
    ModelRecommendation,
    NormalizedInput,
    TrustedUserContext,
)
from app.policy.contracts import (
    ActionDecision,
    AssignmentPolicyContext,
    Intent,
    PermittedAction,
    PreflightDecision,
    RecommendedAction,
)


class OutputValidationResult(Protocol):
    """Small structural result contract returned by the Layer 5 guard."""

    allowed: bool
    reason_code: str
    replacement_message: str | None


@runtime_checkable
class AssignmentService(Protocol):
    async def get_authorized_assignment(
        self,
        assignment_id: str,
        user: TrustedUserContext,
    ) -> AssignmentContext: ...


@runtime_checkable
class InputProcessor(Protocol):
    async def normalize(
        self,
        message: str,
        language_preference: str | None,
        document_ids: list[str],
        user: TrustedUserContext,
        assignment: AssignmentContext,
    ) -> NormalizedInput: ...


@runtime_checkable
class ModelClient(Protocol):
    async def classify(self, normalized: NormalizedInput) -> ModelRecommendation: ...


@runtime_checkable
class PolicyActionService(Protocol):
    def preflight(
        self,
        assignment: AssignmentPolicyContext,
        disclosure_acknowledged: bool,
    ) -> PreflightDecision: ...

    def select_action(
        self,
        assignment: AssignmentPolicyContext,
        intent: Intent | str,
        recommended_action: RecommendedAction | str,
    ) -> ActionDecision: ...


@runtime_checkable
class ResponseGenerator(Protocol):
    async def generate(
        self,
        normalized: NormalizedInput,
        assignment: AssignmentContext,
        selected_action: PermittedAction,
        mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"],
    ) -> str: ...


@runtime_checkable
class ResponseGuardPort(Protocol):
    def validate(
        self,
        *,
        policy_level: str,
        mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"],
        response_text: str,
    ) -> OutputValidationResult: ...


@runtime_checkable
class ConversationAuditService(Protocol):
    async def record_run(
        self,
        *,
        user: TrustedUserContext,
        assignment: AssignmentContext,
        result: AgentResult,
        metadata: Mapping[str, object],
    ) -> None: ...
