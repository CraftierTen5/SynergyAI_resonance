"""Typed, provider-independent contracts for policy and action decisions.

Model recommendations and backend-approved actions deliberately use separate
types: a recommendation is input to policy evaluation, never authorization.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator


PolicyLevel = Literal["RED", "YELLOW", "GREEN"]

Intent = Literal[
    "CONCEPT_EXPLANATION",
    "BRAINSTORMING",
    "REQUEST_FOR_HINT",
    "CODE_FEEDBACK",
    "ANSWER_REQUEST",
    "LANGUAGE_SUPPORT",
    "SUMMARY_REQUEST",
    "AMBIGUOUS",
]

RecommendedAction = Literal[
    "EXPLAIN_CONCEPT",
    "GUIDE_PLANNING",
    "GIVE_HINT",
    "REVIEW_CODE",
    "CHECK_POLICY",
    "CLARIFY_REQUEST",
    "TRANSLATE_OR_EXPLAIN",
    "SUMMARIZE",
]

PermittedAction = Literal[
    "REFUSE",
    "EXPLAIN_CONCEPT",
    "GUIDE_PLANNING",
    "GIVE_HINT",
    "REVIEW_CODE",
    "TRANSLATE_OR_EXPLAIN",
    "SUMMARIZE",
    "CLARIFY_REQUEST",
    "FULL_ASSISTANCE",
]

_POLICY_LEVEL_ALIASES = {
    "RED": "RED",
    "NO_AI": "RED",
    "YELLOW": "YELLOW",
    "PROCESS_DISCLOSURE": "YELLOW",
    "GREEN": "GREEN",
    "FULL_INTEGRATION": "GREEN",
}


def normalize_policy_level(value: object) -> object:
    """Normalize canonical or stored wire policy names to RED/YELLOW/GREEN.

    Unknown values are returned unchanged so Pydantic's ``PolicyLevel``
    validation rejects them with its normal validation error.
    """
    if isinstance(value, str):
        normalized = _POLICY_LEVEL_ALIASES.get(value.strip().upper())
        if normalized is not None:
            return normalized
    return value


class AssignmentPolicyContext(BaseModel):
    """Trusted assignment policy data supplied by the assignment service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: str
    institution_id: str
    policy_level: PolicyLevel
    policy_version: str

    @field_validator("policy_level", mode="before")
    @classmethod
    def normalize_policy_aliases(cls, value: object) -> object:
        return normalize_policy_level(value)


class PreflightDecision(BaseModel):
    """Deterministic policy gate result returned before optional AI work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"]
    user_message: str | None = None
    reason_code: str


class ActionDecision(BaseModel):
    """Backend-selected action; it is not the model's recommended action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    needs_clarification: bool = False
    selected_action: PermittedAction
    mode: Literal["NO_AI", "SOCRATIC", "FULL_ASSISTANCE"]
    user_message: str | None = None
    reason_code: str
