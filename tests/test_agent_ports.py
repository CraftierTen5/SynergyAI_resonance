from collections.abc import Mapping
from typing import Literal

import pytest

from app.agent.contracts import (
    AgentResult,
    AssignmentContext,
    ModelRecommendation,
    NormalizedInput,
    TrustedUserContext,
)
from app.agent.ports import (
    AssignmentService,
    ConversationAuditService,
    InputProcessor,
    ModelClient,
    PolicyActionService as PolicyActionPort,
    ResponseGenerator,
)
from app.services.policy.contracts import (
    ActionDecision,
    AssignmentPolicyContext,
    PreflightDecision,
)
from app.services.policy.service import PolicyActionService


class MockAssignments:
    async def get_authorized_assignment(
        self, assignment_id: str, user: TrustedUserContext
    ) -> AssignmentContext:
        return AssignmentContext(
            assignment_id=assignment_id,
            institution_id=user.institution_id,
            title="Test assignment",
            policy_level="GREEN",
            policy_version="v1",
        )


class MockInputProcessor:
    async def normalize(
        self,
        message: str,
        language_preference: str | None,
        document_ids: list[str],
        user: TrustedUserContext,
        assignment: AssignmentContext,
    ) -> NormalizedInput:
        return NormalizedInput(
            original_message=message,
            normalized_message=message,
            original_language=language_preference or "en",
            response_language=language_preference or "en",
        )


class MockModelClient:
    async def classify(self, normalized: NormalizedInput) -> ModelRecommendation:
        return ModelRecommendation(
            intent="CONCEPT_EXPLANATION",
            recommended_action="EXPLAIN_CONCEPT",
            model_version="test-model",
        )


class MockResponseGenerator:
    async def generate(
        self,
        normalized: NormalizedInput,
        assignment: AssignmentContext,
        selected_action: Literal[
            "REFUSE",
            "EXPLAIN_CONCEPT",
            "GUIDE_PLANNING",
            "GIVE_HINT",
            "REVIEW_CODE",
            "TRANSLATE_OR_EXPLAIN",
            "SUMMARIZE",
            "CLARIFY_REQUEST",
            "FULL_ASSISTANCE",
        ],
        mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"],
    ) -> str:
        return "Test response"


class MockAuditService:
    async def record_run(
        self,
        *,
        user: TrustedUserContext,
        assignment: AssignmentContext,
        result: AgentResult,
        metadata: Mapping[str, object],
    ) -> None:
        return None


@pytest.mark.asyncio
async def test_mock_adapters_conform_to_layer_three_ports():
    user = TrustedUserContext(user_id="u1", institution_id="i1")
    assignment_port: AssignmentService = MockAssignments()
    input_port: InputProcessor = MockInputProcessor()
    model_port: ModelClient = MockModelClient()
    policy_port: PolicyActionPort = PolicyActionService()
    generator_port: ResponseGenerator = MockResponseGenerator()
    audit_port: ConversationAuditService = MockAuditService()

    assert isinstance(assignment_port, AssignmentService)
    assert isinstance(input_port, InputProcessor)
    assert isinstance(model_port, ModelClient)
    assert isinstance(policy_port, PolicyActionPort)
    assert isinstance(generator_port, ResponseGenerator)
    assert isinstance(audit_port, ConversationAuditService)

    assignment = await assignment_port.get_authorized_assignment("a1", user)
    normalized = await input_port.normalize("Explain this", None, [], user, assignment)
    recommendation = await model_port.classify(normalized)
    preflight: PreflightDecision = policy_port.preflight(assignment, False)
    decision: ActionDecision = policy_port.select_action(
        assignment, recommendation.intent, recommendation.recommended_action
    )
    response = await generator_port.generate(
        normalized, assignment, decision.selected_action, decision.mode
    )
    result = AgentResult(
        request_id="r1",
        status="completed",
        message=response,
        policy_level=assignment.policy_level,
        intent=recommendation.intent,
        selected_action=decision.selected_action,
        model_version=recommendation.model_version,
    )
    await audit_port.record_run(
        user=user, assignment=assignment, result=result, metadata={"stage": "test"}
    )

    assert preflight.allowed
    assert decision.allowed
    assert response == "Test response"


def test_assignment_context_is_a_policy_context_subtype():
    assignment = AssignmentContext(
        assignment_id="a1",
        institution_id="i1",
        title="Assignment",
        policy_level="NO_AI",
        policy_version="v1",
    )

    policy_context: AssignmentPolicyContext = assignment
    assert policy_context.policy_level == "RED"
