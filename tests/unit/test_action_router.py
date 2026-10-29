import pytest

from app.policy.action_router import ActionRouter
from app.schemas.agent import (
    AssistanceMode,
    ExecutableAction,
    Intent,
    ModelPrediction,
    PolicyLevel,
    RecommendedAction,
)


def prediction(intent, action):
    return ModelPrediction(intent=intent, recommended_action=action, model_version="test-v1")


def test_red_always_routes_to_refusal():
    result = ActionRouter().route(
        policy_level=PolicyLevel.RED,
        prediction=prediction(Intent.ANSWER_REQUEST, RecommendedAction.PROVIDE_DIRECT_ANSWER),
    )
    assert result.action is ExecutableAction.REFUSE
    assert result.assistance_mode is AssistanceMode.REFUSAL


@pytest.mark.parametrize(
    "recommendation",
    [
        RecommendedAction.PROVIDE_DIRECT_ANSWER,
        RecommendedAction.GENERATE_SOLUTION,
        RecommendedAction.REVIEW_CODE,
    ],
)
def test_yellow_direct_completion_proposals_become_hints(recommendation):
    result = ActionRouter().route(
        policy_level=PolicyLevel.YELLOW,
        prediction=prediction(Intent.ANSWER_REQUEST, recommendation),
    )
    assert result.action is ExecutableAction.PROVIDE_HINT
    assert result.assistance_mode is AssistanceMode.SOCRATIC_PROMPT


def test_yellow_clarification_is_non_completion_action():
    result = ActionRouter().route(
        policy_level=PolicyLevel.YELLOW,
        prediction=prediction(Intent.AMBIGUOUS, RecommendedAction.CLARIFY_REQUEST),
    )
    assert result.action is ExecutableAction.ASK_CLARIFYING_QUESTION


def test_green_allows_direct_completion_actions():
    result = ActionRouter().route(
        policy_level=PolicyLevel.GREEN,
        prediction=prediction(Intent.ANSWER_REQUEST, RecommendedAction.GENERATE_SOLUTION),
    )
    assert result.action is ExecutableAction.GENERATE_SOLUTION
    assert result.assistance_mode is AssistanceMode.DIRECT_ANSWER


def test_unrecognized_recommendation_fails_closed_for_yellow():
    invalid = prediction(Intent.CONCEPT_EXPLANATION, RecommendedAction.EXPLAIN_CONCEPT)
    object.__setattr__(invalid, "recommended_action", "UNRECOGNIZED")
    result = ActionRouter().route(policy_level=PolicyLevel.YELLOW, prediction=invalid)
    assert result.action is ExecutableAction.EXPLAIN_CONCEPT
    assert result.assistance_mode is AssistanceMode.SOCRATIC_PROMPT


def test_router_rejects_untyped_policy_level():
    with pytest.raises(TypeError):
        ActionRouter().route(
            policy_level="YELLOW",
            prediction=prediction(Intent.ANSWER_REQUEST, RecommendedAction.CHECK_POLICY),
        )
