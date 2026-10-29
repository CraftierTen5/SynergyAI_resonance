import pytest

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


ASSIGNMENT = AssignmentSnapshot(
    id="assignment-1", institution_id="school-1", title="Test",
    policy_level=PolicyLevel.YELLOW, policy_version=1,
)
PREPARED = PreparedInput(
    original_message="Help", normalized_message="Help", detected_language="en",
    response_language="en",
)
PRINCIPAL = Principal(user_id="student-1", institution_id="school-1")
PREDICTION = ModelPrediction(
    intent=Intent.ANSWER_REQUEST,
    recommended_action=RecommendedAction.PROVIDE_DIRECT_ANSWER,
    model_version="qwen-v1",
)


class Generator:
    def __init__(self):
        self.calls = 0
        self.request = None

    async def generate(self, request):
        self.calls += 1
        self.request = request
        return GenerationResult(text="Guided hint", provider="fake", tokens_used=3)


class Guard:
    def __init__(self, error_on_call=None):
        self.calls = 0
        self.error_on_call = error_on_call

    async def assert_current(self, **kwargs):
        self.calls += 1
        if self.calls == self.error_on_call:
            raise PolicyChangedError("policy changed")


class Validator:
    def __init__(self):
        self.calls = 0

    def validate(self, *, text, policy_level, assistance_mode, action):
        self.calls += 1
        return type("Validated", (), {"text": text.strip()})()


class Instructions:
    def build(self, **kwargs):
        return "Use guided assistance."


def make_service(*, guard=None):
    generator, validator = Generator(), Validator()
    service = PolicyActionService(
        policy_engine=PolicyEngine(), action_router=ActionRouter(), generator=generator,
        response_validator=validator, policy_snapshot_guard=guard or Guard(),
        instruction_builder=Instructions(),
    )
    return service, generator, validator


@pytest.mark.asyncio
async def test_preflight_denial_returns_without_generator_or_validator_calls():
    service, generator, validator = make_service()
    red_assignment = ASSIGNMENT.model_copy(update={"policy_level": PolicyLevel.RED})
    result = await service.execute(
        assignment=red_assignment, prepared=PREPARED, prediction=PREDICTION,
        disclosure_acknowledged=True, principal=PRINCIPAL, request_id="request-1",
    )
    assert result.status == "REFUSED"
    assert result.assistance_mode is AssistanceMode.REFUSAL
    assert generator.calls == validator.calls == 0


@pytest.mark.asyncio
async def test_allowed_execution_checks_freshness_before_and_after_generation():
    guard = Guard()
    service, generator, validator = make_service(guard=guard)
    result = await service.execute(
        assignment=ASSIGNMENT, prepared=PREPARED, prediction=PREDICTION,
        disclosure_acknowledged=True, principal=PRINCIPAL, request_id="request-2",
    )
    assert result.status == "COMPLETED"
    assert result.action is ExecutableAction.PROVIDE_HINT
    assert result.assistance_mode is AssistanceMode.SOCRATIC_PROMPT
    assert generator.calls == validator.calls == 1
    assert guard.calls == 2
    assert generator.request.policy_version == ASSIGNMENT.policy_version


@pytest.mark.asyncio
async def test_second_freshness_check_prevents_returning_generated_text():
    guard = Guard(error_on_call=2)
    service, generator, validator = make_service(guard=guard)
    with pytest.raises(PolicyChangedError):
        await service.execute(
            assignment=ASSIGNMENT, prepared=PREPARED, prediction=PREDICTION,
            disclosure_acknowledged=True, principal=PRINCIPAL, request_id="request-3",
        )
    assert generator.calls == 1
    assert validator.calls == 0
