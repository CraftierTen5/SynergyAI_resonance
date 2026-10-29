import pytest

from app.agent.orchestrator import TutorOrchestrator
from app.exceptions import DocumentAccessDenied, PolicyChangedError
from app.policy.schemas import ExecutionResult, PreflightResult, PreflightStatus
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
    TutorChatRequest,
)


def make_assignment(policy=PolicyLevel.GREEN, version=1):
    return AssignmentSnapshot(
        id="assignment-1", institution_id="school-1", title="Test",
        policy_level=policy, policy_version=version,
    )


def make_request(acknowledged=False):
    return TutorChatRequest(
        assignment_id="assignment-1", message="Explain this", 
        disclosure_acknowledged=acknowledged,
    )


PRINCIPAL = Principal(user_id="student-1", institution_id="school-1")
PREPARED = PreparedInput(
    original_message="Explain this", normalized_message="Explain this",
    detected_language="en", response_language="en",
)
PREDICTION = ModelPrediction(
    intent=Intent.CONCEPT_EXPLANATION,
    recommended_action=RecommendedAction.EXPLAIN_CONCEPT,
    model_version="qwen-v1",
)


class Assignments:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    async def get_authorized_snapshot(self, assignment_id, principal):
        self.calls += 1
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]


class InputProcessor:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error

    async def prepare(self, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return PREPARED


class Model:
    def __init__(self):
        self.calls = 0

    async def classify(self, **kwargs):
        self.calls += 1
        return PREDICTION


class Layer5:
    def __init__(self, gate=None, error=None):
        self.gate = gate or PreflightResult(
            allowed=True, status=PreflightStatus.ALLOWED,
            message="allowed", assistance_mode=AssistanceMode.DIRECT_ANSWER,
        )
        self.error = error
        self.execute_calls = 0

    def preflight(self, **kwargs):
        return self.gate

    async def execute(self, **kwargs):
        self.execute_calls += 1
        if self.error:
            raise self.error
        return ExecutionResult(
            status="COMPLETED", response="Guidance", action=ExecutableAction.EXPLAIN_CONCEPT,
            assistance_mode=AssistanceMode.DIRECT_ANSWER, provider="fake",
        )


class Runs:
    def __init__(self):
        self.records = []

    async def record(self, **fields):
        self.records.append(fields)


def make_orchestrator(*, policy=PolicyLevel.GREEN, gate=None, input_error=None, layer5_error=None, snapshots=None):
    assignments = Assignments(snapshots or [make_assignment(policy)])
    inputs, model, layer5, runs = InputProcessor(input_error), Model(), Layer5(gate, layer5_error), Runs()
    orch = TutorOrchestrator(
        assignment_service=assignments,
        input_processing_service=inputs,
        model_client=model,
        policy_action_service=layer5,
        agent_run_repository=runs,
    )
    return orch, assignments, inputs, model, layer5, runs


@pytest.mark.asyncio
async def test_preflight_refusal_stops_before_input_and_model():
    gate = PreflightResult(
        allowed=False, status=PreflightStatus.REFUSED,
        message="Not allowed", assistance_mode=AssistanceMode.REFUSAL,
    )
    orch, _, inputs, model, _, runs = make_orchestrator(policy=PolicyLevel.RED, gate=gate)
    response = await orch.handle(make_request(True), PRINCIPAL, "request-1")
    assert response.status == "refused"
    assert inputs.calls == 0
    assert model.calls == 0
    assert runs.records[-1]["status"] == "REFUSED"


@pytest.mark.asyncio
async def test_success_path_rechecks_assignment_and_records_completion():
    orch, assignments, inputs, model, layer5, runs = make_orchestrator()
    response = await orch.handle(make_request(), PRINCIPAL, "request-2")
    assert response.status == "completed"
    assert assignments.calls == 2
    assert inputs.calls == model.calls == layer5.execute_calls == 1
    assert runs.records[-1]["status"] == "COMPLETED"
    assert runs.records[-1]["model_version"] == "qwen-v1"


@pytest.mark.asyncio
async def test_policy_change_between_inference_and_execution_suppresses_execution():
    first = make_assignment(PolicyLevel.GREEN, 1)
    updated = make_assignment(PolicyLevel.RED, 2)
    orch, _, inputs, model, layer5, runs = make_orchestrator(snapshots=[first, updated])
    response = await orch.handle(make_request(), PRINCIPAL, "request-3")
    assert response.status == "policy_changed"
    assert inputs.calls == model.calls == 1
    assert layer5.execute_calls == 0
    assert runs.records[-1]["status"] == "POLICY_CHANGED"


@pytest.mark.asyncio
async def test_document_access_error_is_audited_then_reraised():
    error = DocumentAccessDenied("document not owned")
    orch, _, _, _, _, runs = make_orchestrator(input_error=error)
    with pytest.raises(DocumentAccessDenied):
        await orch.handle(make_request(), PRINCIPAL, "request-4")
    assert runs.records[-1]["status"] == "FORBIDDEN"
