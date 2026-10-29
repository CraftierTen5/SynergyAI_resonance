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
        """Gate tutoring before input processing, OCR, or model/provider calls."""
        if assignment.policy_level == "RED":
            return PreflightDecision(
                allowed=False,
                mode="NO_AI",
                user_message=(
                    "AI assistance is not permitted for this assignment. "
                    "Please follow your instructor's guidance."
                ),
                reason_code="POLICY_RED",
            )

        if assignment.policy_level == "YELLOW":
            if disclosure_acknowledged is not True:
                return PreflightDecision(
                    allowed=False,
                    mode="SOCRATIC",
                    user_message=(
                        "Please acknowledge the AI-use disclosure before "
                        "continuing with guided assistance."
                    ),
                    reason_code="DISCLOSURE_REQUIRED",
                )
            return PreflightDecision(
                allowed=True,
                mode="SOCRATIC",
                reason_code="POLICY_YELLOW_ACKNOWLEDGED",
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
        mode = "SOCRATIC" if level == "YELLOW" else "FULL_ASSISTANCE"

        if level == "RED":
            return ActionDecision(
                allowed=False,
                selected_action="REFUSE",
                mode="NO_AI",
                user_message="AI assistance is not permitted for this assignment.",
                reason_code="POLICY_RED",
            )

        if not isinstance(intent, str) or intent not in VALID_INTENTS:
            return self._clarification(
                mode, "INVALID_INTENT", "I could not confidently classify the request. Could you rephrase it?"
            )

        if not isinstance(recommended_action, str) or recommended_action not in VALID_RECOMMENDATIONS:
            return self._clarification(
                mode, "INVALID_RECOMMENDED_ACTION", "Could you clarify what kind of help you need?"
            )

        if recommended_action == "CHECK_POLICY":
            if intent == "ANSWER_REQUEST":
                action: PermittedAction = (
                    "GUIDE_PLANNING" if level == "YELLOW" else "FULL_ASSISTANCE"
                )
                reason = "ANSWER_REQUEST_GUIDED" if level == "YELLOW" else "ANSWER_REQUEST_GREEN"
            else:
                action = "GIVE_HINT" if level == "YELLOW" else "EXPLAIN_CONCEPT"
                reason = "POLICY_CHECK_RESOLVED"
            return ActionDecision(
                allowed=True,
                selected_action=action,
                mode=mode,
                reason_code=reason,
            )

        if level == "YELLOW":
            if recommended_action not in YELLOW_ACTIONS:
                return ActionDecision(
                    allowed=True,
                    needs_clarification=True,
                    selected_action="CLARIFY_REQUEST",
                    mode="SOCRATIC",
                    user_message="Let's work through the problem step by step. What have you tried so far?",
                    reason_code="YELLOW_ACTION_RESTRICTED",
                )
            return ActionDecision(
                allowed=True,
                selected_action=YELLOW_ACTIONS[recommended_action],
                mode="SOCRATIC",
                reason_code="YELLOW_ACTION_ALLOWED",
            )

        # GREEN may use any recognized recommendation, but the returned
        # action is still validated against the separate permitted-action set.
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
            return self._clarification(
                "FULL_ASSISTANCE", "ACTION_NOT_PERMITTED", "Could you clarify what kind of help you need?"
            )
        return ActionDecision(
            allowed=True,
            selected_action=selected,  # type: ignore[arg-type]
            mode="FULL_ASSISTANCE",
            reason_code="GREEN_ACTION_ALLOWED",
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
