import pytest

from app.policy.engine import PolicyEngine
from app.policy.schemas import PreflightStatus
from app.schemas.agent import AssistanceMode, PolicyLevel


@pytest.mark.parametrize("acknowledged", [False, True])
def test_red_always_refuses(acknowledged):
    result = PolicyEngine().preflight(
        policy_level=PolicyLevel.RED,
        disclosure_acknowledged=acknowledged,
    )
    assert not result.allowed
    assert result.status is PreflightStatus.REFUSED
    assert result.assistance_mode is AssistanceMode.REFUSAL
    assert "not permitted" in result.message.lower()


def test_yellow_requires_disclosure():
    result = PolicyEngine().preflight(
        policy_level=PolicyLevel.YELLOW,
        disclosure_acknowledged=False,
    )
    assert not result.allowed
    assert result.status is PreflightStatus.DISCLOSURE_REQUIRED
    assert result.assistance_mode is AssistanceMode.SOCRATIC_PROMPT


def test_yellow_allows_acknowledged_guided_assistance():
    result = PolicyEngine().preflight(
        policy_level=PolicyLevel.YELLOW,
        disclosure_acknowledged=True,
    )
    assert result.allowed
    assert result.status is PreflightStatus.ALLOWED
    assert result.assistance_mode is AssistanceMode.SOCRATIC_PROMPT


def test_green_allows_direct_answers_without_disclosure():
    result = PolicyEngine().preflight(
        policy_level=PolicyLevel.GREEN,
        disclosure_acknowledged=False,
    )
    assert result.allowed
    assert result.status is PreflightStatus.ALLOWED
    assert result.assistance_mode is AssistanceMode.DIRECT_ANSWER


def test_preflight_rejects_non_contract_inputs():
    with pytest.raises(TypeError):
        PolicyEngine().preflight(policy_level="GREEN", disclosure_acknowledged=False)
    with pytest.raises(TypeError):
        PolicyEngine().preflight(policy_level=PolicyLevel.GREEN, disclosure_acknowledged=1)
