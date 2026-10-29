import pytest

from app.agent.contracts import (
    AgentResult,
    AssignmentContext,
    ModelRecommendation,
    NormalizedInput,
    TrustedUserContext,
    TutorCommand,
)
from app.dependencies import build_agent_orchestrator
from app.services.policy.contracts import ActionDecision, PreflightDecision
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


class FakeAssignmentService:
    def __init__(self, calls, level):
        self.calls = calls
        self.level = level

    async def get_authorized_assignment(self, assignment_id, user):
        self.calls.append("assignment")
        return AssignmentContext(
            assignment_id=assignment_id,
            institution_id=user.institution_id,
            title="Integration assignment",
            policy_level=self.level,
            policy_version="policy-v1",
        )


class RecordingPolicyService:
    def __init__(self, calls):
        self.calls = calls
        self.policy = PolicyActionService()

    def preflight(self, assignment, disclosure_acknowledged) -> PreflightDecision:
        self.calls.append("preflight")
        return self.policy.preflight(assignment, disclosure_acknowledged)

    def select_action(self, assignment, intent, recommended_action) -> ActionDecision:
        self.calls.append("select_action")
        return self.policy.select_action(assignment, intent, recommended_action)


class RecordingInputProcessor:
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


class RecordingModelClient:
    def __init__(self, calls, recommendation):
        self.calls = calls
        self.recommendation = recommendation

    async def classify(self, normalized):
        self.calls.append("model")
        return self.recommendation


class RecordingResponseGenerator:
    def __init__(self, calls):
        self.calls = calls
        self.selected_actions = []
        self.modes = []

    async def generate(self, normalized, assignment, selected_action, mode):
        self.calls.append("generate")
        self.selected_actions.append(selected_action)
        self.modes.append(mode)
        return "Here is a guided explanation."


class RecordingGuard:
    def __init__(self, calls):
        self.calls = calls
        self.guard = ResponseGuard()

    def validate(self, *, policy_level, mode, response_text):
        self.calls.append("output_guard")
        return self.guard.validate(
            policy_level=policy_level, mode=mode, response_text=response_text
        )


class RecordingAuditService:
    def __init__(self, calls):
        self.calls = calls
        self.records = []

    async def record_run(self, *, user, assignment, result: AgentResult, metadata):
        self.calls.append("audit")
        self.records.append((assignment, result, metadata))


def setup_agent(level, recommendation=None):
    calls = []
    generator = RecordingResponseGenerator(calls)
    audit = RecordingAuditService(calls)
    orchestrator = build_agent_orchestrator(
        assignments=FakeAssignmentService(calls, level),
        policy_actions=RecordingPolicyService(calls),
        input_processor=RecordingInputProcessor(calls),
        model_client=RecordingModelClient(
            calls,
            recommendation
            or ModelRecommendation(
                intent="CONCEPT_EXPLANATION",
                recommended_action="EXPLAIN_CONCEPT",
                model_version="qwen-test-v1",
            ),
        ),
        response_generator=generator,
        response_guard=RecordingGuard(calls),
        audit_service=audit,
    )
    return orchestrator, calls, generator, audit


def make_command(*, acknowledged=False):
    return TutorCommand(
        request_id="request-1",
        assignment_id="assignment-1",
        message="Help me understand this",
        disclosure_acknowledged=acknowledged,
    )


def make_user():
    return TrustedUserContext(user_id="student-1", institution_id="school-1")


@pytest.mark.asyncio
async def test_red_policy_refuses_and_halts_before_input_or_model_services():
    orchestrator, calls, _, audit = setup_agent("RED")

    result = await orchestrator.handle(make_command(acknowledged=True), make_user())

    assert result.status == "refused"
    assert result.policy_level == "RED"
    assert calls == ["assignment", "preflight", "audit"]
    assert audit.records[0][2]["reason_code"] == "POLICY_RED"


@pytest.mark.asyncio
async def test_yellow_without_acknowledgment_halts_before_optional_processing():
    orchestrator, calls, _, audit = setup_agent("YELLOW")

    result = await orchestrator.handle(make_command(), make_user())

    assert result.status == "needs_clarification"
    assert result.policy_level == "YELLOW"
    assert calls == ["assignment", "preflight", "audit"]
    assert audit.records[0][2]["reason_code"] == "DISCLOSURE_REQUIRED"


@pytest.mark.asyncio
async def test_green_policy_runs_full_assistance_workflow_in_order():
    orchestrator, calls, generator, _ = setup_agent("GREEN")

    result = await orchestrator.handle(make_command(), make_user())

    assert result.status == "completed"
    assert result.selected_action == "EXPLAIN_CONCEPT"
    assert generator.modes == ["FULL_ASSISTANCE"]
    assert calls == [
        "assignment",
        "preflight",
        "normalize",
        "model",
        "select_action",
        "generate",
        "output_guard",
        "audit",
    ]


@pytest.mark.asyncio
async def test_yellow_model_recommendation_cannot_override_guided_policy():
    recommendation = ModelRecommendation(
        intent="ANSWER_REQUEST",
        recommended_action="CHECK_POLICY",
        model_version="qwen-test-v1",
    )
    orchestrator, calls, generator, _ = setup_agent("YELLOW", recommendation)

    result = await orchestrator.handle(make_command(acknowledged=True), make_user())

    assert result.status == "completed"
    assert result.selected_action == "GUIDE_PLANNING"
    assert generator.selected_actions == ["GUIDE_PLANNING"]
    assert generator.modes == ["SOCRATIC"]
    assert "FULL_ASSISTANCE" not in generator.modes
    assert calls == [
        "assignment",
        "preflight",
        "normalize",
        "model",
        "select_action",
        "generate",
        "output_guard",
        "audit",
    ]
