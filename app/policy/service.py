"""Layer 5 facade for policy checks, action routing, and response generation."""

from typing import Protocol

from app.generation.provider_interface import GenerationProvider, GenerationRequest
from app.policy.action_router import ActionRouter
from app.policy.engine import PolicyEngine
from app.policy.schemas import ExecutionResult, PreflightResult
from app.schemas.agent import (
    AssignmentSnapshot,
    AssistanceMode,
    ExecutableAction,
    ModelPrediction,
    PolicyLevel,
    PreparedInput,
    Principal,
)


class _PolicySnapshotGuard(Protocol):
    async def assert_current(
        self, *, expected: AssignmentSnapshot, principal: Principal
    ) -> None: ...


class _ValidatedResponse(Protocol):
    text: str


class _ResponseValidator(Protocol):
    def validate(
        self,
        *,
        text: str,
        policy_level: PolicyLevel,
        assistance_mode: AssistanceMode,
        action: ExecutableAction,
    ) -> _ValidatedResponse: ...


class _InstructionBuilder(Protocol):
    def build(
        self,
        *,
        policy_level: PolicyLevel,
        assistance_mode: AssistanceMode,
        action: ExecutableAction,
        response_language: str,
    ) -> str: ...


class PolicyActionService:
    """Enforce policy at the generation boundary and return typed outcomes."""

    def __init__(
        self,
        *,
        policy_engine: PolicyEngine,
        action_router: ActionRouter,
        generator: GenerationProvider,
        response_validator: _ResponseValidator,
        policy_snapshot_guard: _PolicySnapshotGuard,
        instruction_builder: _InstructionBuilder,
    ) -> None:
        self.policy_engine = policy_engine
        self.action_router = action_router
        self.generator = generator
        self.response_validator = response_validator
        self.policy_snapshot_guard = policy_snapshot_guard
        self.instruction_builder = instruction_builder

    def preflight(
        self,
        *,
        policy_level: PolicyLevel,
        disclosure_acknowledged: bool,
    ) -> PreflightResult:
        return self.policy_engine.preflight(
            policy_level=policy_level,
            disclosure_acknowledged=disclosure_acknowledged,
        )

    async def execute(
        self,
        *,
        assignment: AssignmentSnapshot,
        prepared: PreparedInput,
        prediction: ModelPrediction,
        disclosure_acknowledged: bool,
        principal: Principal,
        request_id: str,
    ) -> ExecutionResult:
        gate = self.preflight(
            policy_level=assignment.policy_level,
            disclosure_acknowledged=disclosure_acknowledged,
        )
        if not gate.allowed:
            return ExecutionResult(
                status=gate.status.value,
                response=gate.message,
                action=None,
                assistance_mode=gate.assistance_mode,
                provider=None,
            )

        plan = self.action_router.route(
            policy_level=assignment.policy_level,
            prediction=prediction,
        )

        await self.policy_snapshot_guard.assert_current(
            expected=assignment,
            principal=principal,
        )

        system_instruction = self.instruction_builder.build(
            policy_level=assignment.policy_level,
            assistance_mode=plan.assistance_mode,
            action=plan.action,
            response_language=prepared.response_language,
        )
        generation_request = GenerationRequest(
            request_id=request_id,
            assignment_id=assignment.id,
            policy_level=assignment.policy_level,
            policy_version=assignment.policy_version,
            action=plan.action,
            assistance_mode=plan.assistance_mode,
            original_message=prepared.original_message,
            normalized_message=prepared.normalized_message,
            response_language=prepared.response_language,
            assignment_context=prepared.assignment_context,
            document_context=prepared.ocr_text,
            system_instruction=system_instruction,
        )
        generated = await self.generator.generate(generation_request)

        await self.policy_snapshot_guard.assert_current(
            expected=assignment,
            principal=principal,
        )

        validated = self.response_validator.validate(
            text=generated.text,
            policy_level=assignment.policy_level,
            assistance_mode=plan.assistance_mode,
            action=plan.action,
        )
        return ExecutionResult(
            status="COMPLETED",
            response=validated.text,
            action=plan.action,
            assistance_mode=plan.assistance_mode,
            provider=generated.provider,
        )
