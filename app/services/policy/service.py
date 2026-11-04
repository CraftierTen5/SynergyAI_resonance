"""Deterministic policy preflight and action selection (Layer 5)."""

from app.services.policy.contracts import (
    ActionDecision,
    AssignmentPolicyContext,
    Intent,
    PermittedAction,
    PreflightDecision,
    RecommendedAction,
)


VALID_INTENTS = frozenset(
    {
        "CONCEPT_EXPLANATION",
        "BRAINSTORMING",
        "REQUEST_FOR_HINT",
        "CODE_FEEDBACK",
        "ANSWER_REQUEST",
        "LANGUAGE_SUPPORT",
        "SUMMARY_REQUEST",
        "POLICY_EXTRACTION",
        "AMBIGUOUS",
    }
)

VALID_RECOMMENDATIONS = frozenset(
    {
        "EXPLAIN_CONCEPT",
        "GUIDE_PLANNING",
        "GIVE_HINT",
        "REVIEW_CODE",
        "CHECK_POLICY",
        "CLARIFY_REQUEST",
        "TRANSLATE_OR_EXPLAIN",
        "SUMMARIZE",
        "EXTRACT_POLICY",
        "REQUEST_HUMAN_REVIEW",
    }
)

YELLOW_ACTIONS: dict[str, PermittedAction] = {
    "EXPLAIN_CONCEPT": "EXPLAIN_CONCEPT",
    "GUIDE_PLANNING": "GUIDE_PLANNING",
    "GIVE_HINT": "GIVE_HINT",
    "REVIEW_CODE": "REVIEW_CODE",
    "CLARIFY_REQUEST": "CLARIFY_REQUEST",
    "TRANSLATE_OR_EXPLAIN": "TRANSLATE_OR_EXPLAIN",
    "SUMMARIZE": "SUMMARIZE",
}


class PolicyActionService:
    """Evaluate trusted assignment policy without external dependencies."""

    def preflight(
        self,
        assignment: AssignmentPolicyContext,
        disclosure_acknowledged: bool,
    ) -> PreflightDecision:
        """Block tutoring only when policy explicitly prohibits AI use."""
        if assignment.policy_level == "RED":
            return PreflightDecision(
                allowed=False,
                mode="NO_AI",
                user_message="AI help is not permitted for this assignment.",
                reason_code="POLICY_RED",
            )

        if assignment.policy_level == "YELLOW":
            return PreflightDecision(
                allowed=True,
                mode="FULL_ASSISTANCE",
                reason_code="POLICY_YELLOW_ASSUMED_ALLOWED",
            )

        if assignment.policy_level == "GREEN":
            return PreflightDecision(
                allowed=True,
                mode="FULL_ASSISTANCE",
                reason_code="POLICY_GREEN",
            )

        # Defensive fail-closed path for corrupted/bypassed model validation.
        return PreflightDecision(
            allowed=False,
            mode="NO_AI",
            user_message="The assignment policy could not be verified.",
            reason_code="POLICY_INVALID",
        )

    def select_action(
        self,
        assignment: AssignmentPolicyContext,
        intent: Intent | str,
        recommended_action: RecommendedAction | str,
    ) -> ActionDecision:
        """Validate untrusted model labels, then return a backend action."""
        level = assignment.policy_level
        mode = "FULL_ASSISTANCE"

        if level == "RED":
            return ActionDecision(
                allowed=False,
                selected_action="REFUSE",
                mode="NO_AI",
                user_message="AI assistance is not permitted for this assignment.",
                reason_code="POLICY_RED",
            )

        if not isinstance(intent, str) or intent not in VALID_INTENTS:
            if mode == "FULL_ASSISTANCE":
                return self._best_effort_explanation("INVALID_INTENT")
            return self._clarification(
                mode, "INVALID_INTENT", "Which part would you like help with?"
            )

        if not isinstance(recommended_action, str) or recommended_action not in VALID_RECOMMENDATIONS:
            if mode == "FULL_ASSISTANCE":
                return self._best_effort_explanation("INVALID_RECOMMENDED_ACTION")
            return self._clarification(
                mode, "INVALID_RECOMMENDED_ACTION", "Which part would you like help with?"
            )

        if (
            intent == "AMBIGUOUS"
            or intent == "POLICY_EXTRACTION"
            or recommended_action in {"CLARIFY_REQUEST", "EXTRACT_POLICY", "REQUEST_HUMAN_REVIEW"}
        ):
            if mode == "FULL_ASSISTANCE":
                return self._best_effort_explanation("BEST_EFFORT_EXPLANATION")
            return self._clarification(
                mode,
                "MODEL_REQUIRES_CLARIFICATION",
                "Which part would you like help with?",
            )

        if level == "GREEN" and intent == "CONCEPT_EXPLANATION":
            return self._best_effort_explanation("CONCEPT_EXPLANATION")

        if level == "GREEN" and intent == "ANSWER_REQUEST":
            return ActionDecision(
                allowed=True,
                selected_action="FULL_ASSISTANCE",
                mode="FULL_ASSISTANCE",
                reason_code="ANSWER_REQUEST_GREEN",
            )

        if recommended_action == "CHECK_POLICY":
            if intent == "ANSWER_REQUEST":
                action: PermittedAction = "FULL_ASSISTANCE"
                reason = "ANSWER_REQUEST_ALLOWED"
            else:
                action = "EXPLAIN_CONCEPT"
                reason = "POLICY_CHECK_RESOLVED"
            return ActionDecision(
                allowed=True,
                selected_action=action,
                mode=mode,
                reason_code=reason,
            )

        # Both YELLOW (unmentioned/unclear) and GREEN (explicitly permitted)
        # receive the requested answer. RED was rejected above.
        selected = recommended_action
        if selected not in {
            "EXPLAIN_CONCEPT",
            "GUIDE_PLANNING",
            "GIVE_HINT",
            "REVIEW_CODE",
            "TRANSLATE_OR_EXPLAIN",
            "SUMMARIZE",
            "CLARIFY_REQUEST",
        }:
            return self._best_effort_explanation("ACTION_DEFAULTED_TO_EXPLANATION")
        return ActionDecision(
            allowed=True,
            selected_action=selected,  # type: ignore[arg-type]
            mode="FULL_ASSISTANCE",
            reason_code="GREEN_ACTION_ALLOWED",
        )

    @staticmethod
    def _best_effort_explanation(reason_code: str) -> ActionDecision:
        return ActionDecision(
            allowed=True,
            selected_action="EXPLAIN_CONCEPT",
            mode="FULL_ASSISTANCE",
            reason_code=reason_code,
        )

    @staticmethod
    def _clarification(mode: str, reason_code: str, message: str) -> ActionDecision:
        return ActionDecision(
            allowed=False,
            needs_clarification=True,
            selected_action="CLARIFY_REQUEST",
            mode=mode,  # type: ignore[arg-type]
            user_message=message,
            reason_code=reason_code,
        )
