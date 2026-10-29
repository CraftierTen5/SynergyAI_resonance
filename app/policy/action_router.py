"""Map validated model proposals to a finite set of policy-approved actions."""

from dataclasses import dataclass

from app.schemas.agent import (
    AssistanceMode,
    ExecutableAction,
    Intent,
    ModelPrediction,
    PolicyLevel,
    RecommendedAction,
)


@dataclass(frozen=True)
class ActionPlan:
    action: ExecutableAction
    assistance_mode: AssistanceMode


class ActionRouter:
    """Apply policy boundaries to model intent and action recommendations."""

    _YELLOW_ACTIONS = {
        RecommendedAction.EXPLAIN_CONCEPT: ExecutableAction.EXPLAIN_CONCEPT,
        RecommendedAction.GUIDE_PLANNING: ExecutableAction.GUIDE_PLANNING,
        RecommendedAction.GIVE_HINT: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.PROVIDE_HINT: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.GENERATE_SOLUTION: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.PROVIDE_DIRECT_ANSWER: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.CHECK_POLICY: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.REVIEW_CODE: ExecutableAction.PROVIDE_HINT,
        RecommendedAction.CLARIFY_REQUEST: ExecutableAction.ASK_CLARIFYING_QUESTION,
        RecommendedAction.ASK_CLARIFYING_QUESTION: ExecutableAction.ASK_CLARIFYING_QUESTION,
        RecommendedAction.TRANSLATE_OR_EXPLAIN: ExecutableAction.EXPLAIN_CONCEPT,
        RecommendedAction.SUMMARIZE: ExecutableAction.EXPLAIN_CONCEPT,
    }

    _GREEN_ACTIONS = {
        **_YELLOW_ACTIONS,
        RecommendedAction.GENERATE_SOLUTION: ExecutableAction.GENERATE_SOLUTION,
        RecommendedAction.PROVIDE_DIRECT_ANSWER: ExecutableAction.PROVIDE_DIRECT_ANSWER,
        RecommendedAction.CHECK_POLICY: ExecutableAction.PROVIDE_DIRECT_ANSWER,
    }

    def route(
        self,
        *,
        policy_level: PolicyLevel,
        prediction: ModelPrediction,
    ) -> ActionPlan:
        if not isinstance(policy_level, PolicyLevel):
            raise TypeError("policy_level must be a PolicyLevel")

        if policy_level is PolicyLevel.RED:
            return ActionPlan(
                action=ExecutableAction.REFUSE,
                assistance_mode=AssistanceMode.REFUSAL,
            )

        if not isinstance(prediction, ModelPrediction):
            return self._safe_default(policy_level)
        if not isinstance(prediction.intent, Intent) or not isinstance(
            prediction.recommended_action, RecommendedAction
        ):
            return self._safe_default(policy_level)

        if policy_level is PolicyLevel.YELLOW:
            action = self._YELLOW_ACTIONS.get(prediction.recommended_action)
            if prediction.intent is Intent.ANSWER_REQUEST:
                action = ExecutableAction.PROVIDE_HINT
            if action is None:
                action = ExecutableAction.EXPLAIN_CONCEPT
            return ActionPlan(
                action=action,
                assistance_mode=AssistanceMode.SOCRATIC_PROMPT,
            )

        action = self._GREEN_ACTIONS.get(prediction.recommended_action)
        if action is None:
            action = self._GREEN_INTENT_ACTIONS.get(prediction.intent)
        if action is None:
            action = ExecutableAction.REFUSE
        return ActionPlan(
            action=action,
            assistance_mode=(
                AssistanceMode.REFUSAL
                if action is ExecutableAction.REFUSE
                else AssistanceMode.DIRECT_ANSWER
            ),
        )

    @staticmethod
    def _safe_default(policy_level: PolicyLevel) -> ActionPlan:
        if policy_level is PolicyLevel.RED:
            return ActionPlan(
                action=ExecutableAction.REFUSE,
                assistance_mode=AssistanceMode.REFUSAL,
            )
        if policy_level is PolicyLevel.YELLOW:
            return ActionPlan(
                action=ExecutableAction.EXPLAIN_CONCEPT,
                assistance_mode=AssistanceMode.SOCRATIC_PROMPT,
            )
        return ActionPlan(
            action=ExecutableAction.REFUSE,
            assistance_mode=AssistanceMode.REFUSAL,
        )

    _GREEN_INTENT_ACTIONS = {
        Intent.ANSWER_REQUEST: ExecutableAction.PROVIDE_DIRECT_ANSWER,
        Intent.CONCEPT_EXPLANATION: ExecutableAction.EXPLAIN_CONCEPT,
        Intent.BRAINSTORMING: ExecutableAction.GUIDE_PLANNING,
        Intent.REQUEST_FOR_HINT: ExecutableAction.PROVIDE_HINT,
        Intent.CODE_FEEDBACK: ExecutableAction.GUIDED_FEEDBACK,
        Intent.LANGUAGE_SUPPORT: ExecutableAction.LANGUAGE_SUPPORT,
        Intent.SUMMARY_REQUEST: ExecutableAction.SUMMARIZE,
        Intent.AMBIGUOUS: ExecutableAction.ASK_CLARIFYING_QUESTION,
    }
