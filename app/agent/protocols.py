"""Typed boundaries between the agent orchestrator and its dependencies."""

from typing import Protocol

from app.policy.schemas import ExecutionResult, PreflightResult
from app.schemas.agent import (
    AssignmentSnapshot,
    ModelPrediction,
    PolicyLevel,
    PreparedInput,
    Principal,
    TutorChatRequest,
)


class AssignmentService(Protocol):
    async def get_authorized_snapshot(
        self, assignment_id: str, principal: Principal
    ) -> AssignmentSnapshot: ...


class InputProcessingService(Protocol):
    async def prepare(
        self,
        *,
        request: TutorChatRequest,
        assignment: AssignmentSnapshot,
        principal: Principal,
    ) -> PreparedInput: ...


class ModelClient(Protocol):
    async def classify(
        self, *, prepared: PreparedInput, policy_level: PolicyLevel
    ) -> ModelPrediction: ...


class PolicyActionService(Protocol):
    def preflight(
        self, *, policy_level: PolicyLevel, disclosure_acknowledged: bool
    ) -> PreflightResult: ...

    async def execute(
        self,
        *,
        assignment: AssignmentSnapshot,
        prepared: PreparedInput,
        prediction: ModelPrediction,
        disclosure_acknowledged: bool,
        principal: Principal,
        request_id: str,
    ) -> ExecutionResult: ...


class AgentRunRepository(Protocol):
    async def record(self, **fields: object) -> None: ...
