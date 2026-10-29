import pytest

from app.policy.contracts import AssignmentPolicyContext
from app.policy.output_guard import ResponseGuard
from app.policy.service import PolicyActionService


def assignment(policy_level):
    return AssignmentPolicyContext(
        assignment_id="assignment-1",
        institution_id="institution-1",
        policy_level=policy_level,
        policy_version="v1",
    )


def test_red_preflight_refuses_and_action_selection_never_overrides_it():
    service = PolicyActionService()

    preflight = service.preflight(assignment("RED"), disclosure_acknowledged=True)
    action = service.select_action(assignment("RED"), "ANSWER_REQUEST", "CHECK_POLICY")

    assert not preflight.allowed
    assert preflight.mode == "NO_AI"
    assert preflight.reason_code == "POLICY_RED"
    assert not action.allowed
    assert action.selected_action == "REFUSE"
    assert action.mode == "NO_AI"


def test_yellow_requires_explicit_disclosure_before_processing():
    service = PolicyActionService()

    missing = service.preflight(assignment("YELLOW"), disclosure_acknowledged=False)
    truthy_non_boolean = service.preflight(assignment("YELLOW"), disclosure_acknowledged="yes")
    acknowledged = service.preflight(assignment("YELLOW"), disclosure_acknowledged=True)

    assert not missing.allowed
    assert missing.reason_code == "DISCLOSURE_REQUIRED"
    assert not truthy_non_boolean.allowed
    assert acknowledged.allowed
    assert acknowledged.mode == "SOCRATIC"


def test_green_preflight_allows_full_assistance_without_external_dependencies():
    service = PolicyActionService()
    result = service.preflight(assignment("GREEN"), disclosure_acknowledged=False)

    assert result.allowed
    assert result.mode == "FULL_ASSISTANCE"
    assert result.reason_code == "POLICY_GREEN"
    # The deterministic service has no provider/OCR collaborators to invoke.
    assert not hasattr(service, "provider")
    assert not hasattr(service, "ocr")


def test_invalid_model_labels_only_return_clarification_decisions():
    decision = PolicyActionService().select_action(
        assignment("GREEN"), "UNKNOWN_INTENT", "FULL_ASSISTANCE"
    )

    assert not decision.allowed
    assert decision.needs_clarification
    assert decision.selected_action == "CLARIFY_REQUEST"


def test_yellow_answer_request_is_mapped_to_guidance_not_full_assistance():
    decision = PolicyActionService().select_action(
        assignment("YELLOW"), "ANSWER_REQUEST", "CHECK_POLICY"
    )

    assert decision.allowed
    assert decision.selected_action == "GUIDE_PLANNING"
    assert decision.mode == "SOCRATIC"


def test_response_guard_rejects_red_empty_and_oversized_socratic_outputs():
    guard = ResponseGuard(max_socratic_chars=10)

    assert guard.validate(policy_level="RED", mode="NO_AI", response_text="No").reason_code == "RED_OUTPUT_FORBIDDEN"
    assert guard.validate(policy_level="GREEN", mode="FULL_ASSISTANCE", response_text="  ").reason_code == "EMPTY_RESPONSE"
    assert guard.validate(policy_level="YELLOW", mode="SOCRATIC", response_text="x" * 11).reason_code == "YELLOW_OUTPUT_TOO_LONG"


def test_response_guard_enforces_yellow_mode():
    result = ResponseGuard().validate(
        policy_level="YELLOW", mode="FULL_ASSISTANCE", response_text="Answer"
    )

    assert not result.allowed
    assert result.reason_code == "POLICY_MODE_MISMATCH"
