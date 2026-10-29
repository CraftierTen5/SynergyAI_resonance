import pytest

from app.agent.contracts import (
    AssignmentContext,
    ModelRecommendation,
    NormalizedInput,
    TrustedUserContext,
    TutorCommand,
)
from app.agent.orchestrator import AgentOrchestrator
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


class AssignmentStub:
    def __init__(self, calls, policy_level):
        self.calls = calls
        self.policy_level = policy_level

    async def get_authorized_assignment(self, assignment_id, user):
        self.calls.append("assignment")
        return AssignmentContext(
            assignment_id=assignment_id,
            institution_id=user.institution_id,
            title="Test assignment",
            policy_level=self.policy_level,
            policy_version="v1",
        )


class PolicySpy:
    def __init__(self, calls):
        self.calls = calls
        self.service = PolicyActionService()

    def preflight(self, assignment, disclosure_acknowledged):
        self.calls.append("preflight")
        return self.service.preflight(assignment, disclosure_acknowledged)

    def select_action(self, assignment, intent, recommended_action):
        self.calls.append("select_action")
        return self.service.select_action(assignment, intent, recommended_action)


class InputProcessorStub:
    def __init__(self, calls):
        self.calls = calls

    async def normalize(self, message, language_preference, document_ids, user, assignment):
        self.calls.append("normalize")
        return NormalizedInput(
            original_message=message,
            normalized_message=message,
            original_language="en",
            response_language=language_preference or "en",
        )


class ModelClientStub:
    def __init__(self, calls):
        self.calls = calls

    async def classify(self, normalized):
        self.calls.append("classify")
        return ModelRecommendation(
            intent="CONCEPT_EXPLANATION",
            recommended_action="EXPLAIN_CONCEPT",
            model_version="test-model",
        )


class ResponseGeneratorStub:
    def __init__(self, calls):
        self.calls = calls

    async def generate(self, normalized, assignment, selected_action, mode):
        self.calls.append("generate")
        return "A short explanation."


class GuardSpy:
    def __init__(self, calls):
        self.calls = calls
        self.guard = ResponseGuard()

    def validate(self, *, policy_level, mode, response_text):
        self.calls.append("validate")
        return self.guard.validate(
            policy_level=policy_level, mode=mode, response_text=response_text
        )


class AuditStub:
    def __init__(self, calls):
        self.calls = calls

    async def record_run(self, *, user, assignment, result, metadata):
        self.calls.append("audit")


def build_orchestrator(policy_level):
    calls = []
    orchestrator = AgentOrchestrator(
        assignments=AssignmentStub(calls, policy_level),
        policy_actions=PolicySpy(calls),
        input_processor=InputProcessorStub(calls),
        model_client=ModelClientStub(calls),
        response_generator=ResponseGeneratorStub(calls),
        response_guard=GuardSpy(calls),
        audit_service=AuditStub(calls),
    )
    return orchestrator, calls


def command(*, acknowledged=False):
    return TutorCommand(
        request_id="request-1",
        assignment_id="assignment-1",
        message="Explain this concept",
        disclosure_acknowledged=acknowledged,
    )


def user():
    return TrustedUserContext(user_id="user-1", institution_id="institution-1")


@pytest.mark.asyncio
async def test_red_short_circuits_before_input_processing_model_and_generation():
    orchestrator, calls = build_orchestrator("RED")

    result = await orchestrator.handle(command(acknowledged=True), user())

    assert result.status == "refused"
    assert calls == ["assignment", "preflight", "audit"]


@pytest.mark.asyncio
async def test_yellow_without_acknowledgment_short_circuits_before_ai_processing():
    orchestrator, calls = build_orchestrator("YELLOW")

    result = await orchestrator.handle(command(), user())

    assert result.status == "needs_clarification"
    assert calls == ["assignment", "preflight", "audit"]


@pytest.mark.asyncio
async def test_green_flow_obeys_required_call_order_and_validates_before_audit():
    orchestrator, calls = build_orchestrator("GREEN")

    result = await orchestrator.handle(command(), user())

    assert result.status == "completed"
    assert calls == [
        "assignment",
        "preflight",
        "normalize",
        "classify",
        "select_action",
        "generate",
        "validate",
        "audit",
    ]
