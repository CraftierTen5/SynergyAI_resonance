"""End-to-end policy gate coverage through FastAPI and the tutor orchestrator."""

from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.orchestrator import TutorOrchestrator
from app.api.dependencies import get_principal
from app.api.routes_tutor import router as tutor_router
from app.exceptions import PolicyChangedError
from app.generation.provider_interface import GenerationResult
from app.policy.action_router import ActionRouter
from app.policy.engine import PolicyEngine
from app.policy.service import PolicyActionService
from app.schemas.agent import (
    AssignmentSnapshot,
    AssistanceMode,
    ExecutableAction,
    Intent,
    ModelPrediction,
    PolicyLevel,
    PreparedInput,
    Principal,
    RecommendedAction,
)


PRINCIPAL = Principal(user_id="student-1", institution_id="school-1")


def assignment(policy_level: PolicyLevel, policy_version: int = 1) -> AssignmentSnapshot:
    return AssignmentSnapshot(
        id="assignment-1",
        institution_id="school-1",
        title="Test assignment",
        policy_level=policy_level,
        policy_version=policy_version,
    )


class FakeAssignments:
    def __init__(self, snapshot: AssignmentSnapshot):
        self.current_snapshot = snapshot
        self.stale_second_read = False
        self.read_count = 0
        self.policy_changed_errors = 0

    async def get_authorized_snapshot(self, assignment_id, principal):
        self.read_count += 1
        # Simulate a briefly stale authorized read after inference. The Layer 5
        # freshness guard then observes the updated authoritative snapshot.
        if self.stale_second_read and self.read_count == 2:
            return assignment(PolicyLevel.YELLOW, policy_version=1)
        return self.current_snapshot


class FakeInputProcessor:
    def __init__(self):
        self.calls = 0
        self.external_ai_calls = 0

    async def prepare(self, *, request, assignment, principal):
        self.calls += 1
        return PreparedInput(
            original_message=request.message,
            normalized_message=request.message,
            detected_language="en",
            response_language="en",
        )


class FakeModelClient:
    def __init__(self, assignments: FakeAssignments | None = None):
        self.calls = 0
        self.assignments = assignments

    async def classify(self, *, prepared, policy_level):
        self.calls += 1
        if self.assignments is not None and self.assignments.stale_second_read:
            self.assignments.current_snapshot = assignment(PolicyLevel.RED, policy_version=2)
        return ModelPrediction(
            intent=Intent.ANSWER_REQUEST,
            recommended_action=RecommendedAction.PROVIDE_DIRECT_ANSWER,
            model_version="qwen-test-v1",
        )


class FakeGenerator:
    provider_name = "gemini"

    def __init__(self):
        self.calls = 0
        self.gemini_calls = 0
        self.groq_calls = 0
        self.last_request = None

    async def generate(self, request):
        self.calls += 1
        self.gemini_calls += 1
        self.last_request = request
        return GenerationResult(
            text="MUST NOT LEAK" if request.policy_level is PolicyLevel.RED else "Let's reason through it step by step.",
            provider="gemini",
            tokens_used=12,
        )


class FakePolicySnapshotGuard:
    def __init__(self, assignments: FakeAssignments):
        self.assignments = assignments
        self.calls = 0

    async def assert_current(self, *, expected, principal):
        self.calls += 1
        current = await self.assignments.get_authorized_snapshot(expected.id, principal)
        if (
            current.policy_level != expected.policy_level
            or current.policy_version != expected.policy_version
        ):
            self.assignments.policy_changed_errors += 1
            raise PolicyChangedError("Policy changed during request")


@dataclass(frozen=True)
class ValidatedText:
    text: str


class FakeResponseValidator:
    def validate(self, *, text, policy_level, assistance_mode, action):
        return ValidatedText(text=text.strip())


class FakeInstructionBuilder:
    def build(self, *, policy_level, assistance_mode, action, response_language):
        return f"Use {assistance_mode.value} assistance in {response_language}."


class FakeRunRepository:
    def __init__(self):
        self.records = []

    async def record(self, **fields):
        self.records.append(fields)


def make_client(policy_level: PolicyLevel):
    assignments = FakeAssignments(assignment(policy_level))
    input_processor = FakeInputProcessor()
    model_client = FakeModelClient(assignments)
    generator = FakeGenerator()
    audit = FakeRunRepository()
    layer5 = PolicyActionService(
        policy_engine=PolicyEngine(),
        action_router=ActionRouter(),
        generator=generator,
        response_validator=FakeResponseValidator(),
        policy_snapshot_guard=FakePolicySnapshotGuard(assignments),
        instruction_builder=FakeInstructionBuilder(),
    )
    orchestrator = TutorOrchestrator(
        assignment_service=assignments,
        input_processing_service=input_processor,
        model_client=model_client,
        policy_action_service=layer5,
        agent_run_repository=audit,
    )
    app = FastAPI()
    app.state.tutor_orchestrator = orchestrator
    app.dependency_overrides[get_principal] = lambda: PRINCIPAL
    app.include_router(tutor_router)
    return TestClient(app), assignments, input_processor, model_client, generator, audit


def post_chat(client: TestClient, *, acknowledged: bool = False):
    return client.post(
        "/tutor/chat",
        headers={"X-Request-ID": "e2e-request-1"},
        json={
            "assignment_id": "assignment-1",
            "message": "Can you help me solve this?",
            "disclosure_acknowledged": acknowledged,
        },
    )


def test_red_policy_refuses_without_any_ai_calls_and_audits_refusal():
    client, _, input_processor, model_client, generator, audit = make_client(PolicyLevel.RED)

    response = post_chat(client, acknowledged=True)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert "not permitted" in body["response"].lower()
    assert input_processor.calls == 0
    assert input_processor.external_ai_calls == 0
    assert model_client.calls == 0
    assert generator.gemini_calls == 0
    assert generator.groq_calls == 0
    assert audit.records[-1]["status"] == "REFUSED"


def test_yellow_without_acknowledgment_requires_disclosure_before_ai_calls():
    client, _, input_processor, model_client, generator, _ = make_client(PolicyLevel.YELLOW)

    response = post_chat(client, acknowledged=False)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "disclosure_required"
    assert "acknowledge" in body["response"].lower()
    assert input_processor.calls == 0
    assert input_processor.external_ai_calls == 0
    assert model_client.calls == 0
    assert generator.calls == 0
    assert generator.gemini_calls == 0
    assert generator.groq_calls == 0


def test_yellow_with_acknowledgment_uses_guided_socratic_assistance():
    client, _, input_processor, model_client, generator, _ = make_client(PolicyLevel.YELLOW)

    response = post_chat(client, acknowledged=True)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["assistance_mode"] == AssistanceMode.SOCRATIC_PROMPT.value
    assert body["action"] == ExecutableAction.PROVIDE_HINT.value
    assert body["response"] == "Let's reason through it step by step."
    assert input_processor.calls == 1
    assert model_client.calls == 1
    assert generator.calls == 1


def test_green_policy_runs_direct_completion_path():
    client, _, _, model_client, generator, _ = make_client(PolicyLevel.GREEN)

    response = post_chat(client, acknowledged=False)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["assistance_mode"] == AssistanceMode.DIRECT_ANSWER.value
    assert body["action"] == ExecutableAction.PROVIDE_DIRECT_ANSWER.value
    assert model_client.calls == 1
    assert generator.calls == 1
    assert generator.last_request.assistance_mode is AssistanceMode.DIRECT_ANSWER


def test_policy_change_during_classification_is_caught_and_output_is_suppressed():
    client, assignments, _, model_client, generator, audit = make_client(PolicyLevel.YELLOW)
    assignments.stale_second_read = True

    response = post_chat(client, acknowledged=True)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "policy_changed"
    assert body["policy_level"] == PolicyLevel.RED.value
    assert body["response"] != "MUST NOT LEAK"
    assert "MUST NOT LEAK" not in body["response"]
    assert model_client.calls == 1
    assert assignments.policy_changed_errors == 1
    assert generator.calls == 0
    assert audit.records[-1]["status"] == "POLICY_CHANGED"
