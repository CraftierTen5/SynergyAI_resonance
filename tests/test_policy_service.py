import pytest

from app.services.policy.contracts import AssignmentPolicyContext
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


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


def test_yellow_defaults_to_allowed_without_confirmation():
    service = PolicyActionService()

    missing = service.preflight(assignment("YELLOW"), disclosure_acknowledged=False)
    truthy_non_boolean = service.preflight(assignment("YELLOW"), disclosure_acknowledged="yes")
    acknowledged = service.preflight(assignment("YELLOW"), disclosure_acknowledged=True)

    assert missing.allowed
    assert missing.reason_code == "POLICY_YELLOW_ASSUMED_ALLOWED"
    assert truthy_non_boolean.allowed
    assert acknowledged.allowed
    assert missing.mode == acknowledged.mode == "FULL_ASSISTANCE"


def test_green_preflight_allows_full_assistance_without_external_dependencies():
    service = PolicyActionService()
    result = service.preflight(assignment("GREEN"), disclosure_acknowledged=False)

    assert result.allowed
    assert result.mode == "FULL_ASSISTANCE"
    assert result.reason_code == "POLICY_GREEN"
    # The deterministic service has no provider/OCR collaborators to invoke.
    assert not hasattr(service, "provider")
    assert not hasattr(service, "ocr")


def test_invalid_model_labels_use_best_effort_explanation_on_green_policy():
    decision = PolicyActionService().select_action(
        assignment("GREEN"), "UNKNOWN_INTENT", "FULL_ASSISTANCE"
    )

    assert decision.allowed
    assert not decision.needs_clarification
    assert decision.selected_action == "EXPLAIN_CONCEPT"
    assert decision.mode == "FULL_ASSISTANCE"


def test_yellow_answer_request_is_allowed_without_confirmation():
    decision = PolicyActionService().select_action(
        assignment("YELLOW"), "ANSWER_REQUEST", "CHECK_POLICY"
    )

    assert decision.allowed
    assert decision.selected_action == "FULL_ASSISTANCE"
    assert decision.mode == "FULL_ASSISTANCE"


def test_response_guard_rejects_red_empty_and_oversized_socratic_outputs():
    guard = ResponseGuard(max_socratic_chars=10)

    assert guard.validate(policy_level="RED", mode="NO_AI", response_text="No").reason_code == "RED_OUTPUT_FORBIDDEN"
    assert guard.validate(policy_level="GREEN", mode="FULL_ASSISTANCE", response_text="  ").reason_code == "EMPTY_RESPONSE"
    assert guard.validate(policy_level="YELLOW", mode="SOCRATIC", response_text="x" * 11).reason_code == "YELLOW_OUTPUT_TOO_LONG"


def test_response_guard_allows_direct_answer_for_yellow_policy():
    result = ResponseGuard().validate(
        policy_level="YELLOW", mode="FULL_ASSISTANCE", response_text="Answer"
    )

    assert result.allowed
