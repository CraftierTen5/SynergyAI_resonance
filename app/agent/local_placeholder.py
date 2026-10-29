"""Deterministic local adapters for exercising the tutoring workflow.

Each adapter implements an existing orchestration port so a trained model or
production service can replace it without changing the workflow contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.agent.contracts import (
    AgentResult,
    AssignmentContext,
    ModelRecommendation,
    NormalizedInput,
    TrustedUserContext,
)
from app.services.policy.contracts import PermittedAction


class LocalInputProcessor:
    """Pass text through unchanged for predictable offline runs."""

    async def normalize(
        self,
        message: str,
        language_preference: str | None,
        document_ids: list[str],
        user: TrustedUserContext,
        assignment: AssignmentContext,
    ) -> NormalizedInput:
        del document_ids, user
        language = language_preference or "en"
        return NormalizedInput(
            original_message=message,
            normalized_message=message,
            original_language=language,
            response_language=language,
            assignment_context=f"{assignment.title}: {assignment.subject or 'General'}",
            normalization_method="local_passthrough",
        )


class LocalModelClient:
    """Return stable intent/action labels using simple ordered keyword rules."""

    model_version = "local-placeholder-v1"

    async def classify(self, normalized: NormalizedInput) -> ModelRecommendation:
        message = normalized.normalized_message.casefold()
        if any(term in message for term in ("answer", "solve", "final result", "do this")):
            intent, action = "ANSWER_REQUEST", "CHECK_POLICY"
        elif any(term in message for term in ("hint", "clue", "where do i start")):
            intent, action = "REQUEST_FOR_HINT", "GIVE_HINT"
        elif any(term in message for term in ("brainstorm", "ideas", "plan")):
            intent, action = "BRAINSTORMING", "GUIDE_PLANNING"
        elif any(term in message for term in ("review my code", "debug", "bug", "code")):
            intent, action = "CODE_FEEDBACK", "REVIEW_CODE"
        elif any(term in message for term in ("translate", "language")):
            intent, action = "LANGUAGE_SUPPORT", "TRANSLATE_OR_EXPLAIN"
        elif any(term in message for term in ("summarize", "summary")):
            intent, action = "SUMMARY_REQUEST", "SUMMARIZE"
        elif message.strip():
            intent, action = "CONCEPT_EXPLANATION", "EXPLAIN_CONCEPT"
        else:
            intent, action = "AMBIGUOUS", "CLARIFY_REQUEST"
        return ModelRecommendation(
            intent=intent,
            recommended_action=action,
            model_version=self.model_version,
        )


class LocalResponseGenerator:
    """Produce predictable action-aware text without external model calls."""

    async def generate(
        self,
        normalized: NormalizedInput,
        assignment: AssignmentContext,
        selected_action: PermittedAction,
        mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"],
    ) -> str:
        del assignment
        if mode == "SOCRATIC":
            return (
                f"Let's work through this together. For your question, "
                f"{normalized.normalized_message!r}, what have you tried so far? "
                "Share your first step and I’ll help you reason through it."
            )
        templates = {
            "EXPLAIN_CONCEPT": "A clear explanation is ready for this concept.",
            "GUIDE_PLANNING": "Start by listing the goal, constraints, and one small first step.",
            "GIVE_HINT": "Hint: identify the key idea in the question, then apply it to one small example.",
            "REVIEW_CODE": "Review result: check the expected input, edge cases, and the exact failing line.",
            "TRANSLATE_OR_EXPLAIN": "Language support is ready; the source text was preserved for processing.",
            "SUMMARIZE": "Summary: identify the main claim, supporting points, and the conclusion.",
            "CLARIFY_REQUEST": "Could you clarify what kind of help you need?",
            "FULL_ASSISTANCE": "Full assistance is enabled for this demo assignment.",
        }
        return templates.get(selected_action, "The local placeholder completed this request.")


class LocalAssignmentService:
    """Server-owned demo assignments; policy is never accepted from request data."""

    _assignments: Mapping[str, tuple[str, str]] = {
        "demo-green": ("GREEN", "Full assistance demo"),
        "demo-yellow": ("YELLOW", "Guided assistance demo"),
        "demo-red": ("RED", "No AI demo"),
    }

    async def get_authorized_assignment(
        self, assignment_id: str, user: TrustedUserContext
    ) -> AssignmentContext:
        details = self._assignments.get(assignment_id)
        if details is None:
            raise LookupError("Demo assignment not found")
        level, title = details
        return AssignmentContext(
            assignment_id=assignment_id,
            institution_id=user.institution_id,
            title=title,
            subject="Local pipeline demo",
            policy_level=level,
            policy_version="demo-policy-v1",
        )


class LocalAuditService:
    """Retain metadata in process memory for local inspection only."""

    def __init__(self) -> None:
        self.records: list[tuple[AgentResult, dict[str, object]]] = []

    async def record_run(
        self,
        *,
        user: TrustedUserContext,
        assignment: AssignmentContext,
        result: AgentResult,
        metadata: Mapping[str, object],
    ) -> None:
        del user, assignment
        self.records.append((result, dict(metadata)))

