"""Pydantic contracts shared across the Layer 3 orchestration ports."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.policy.contracts import (
    AssignmentPolicyContext,
    Intent,
    PermittedAction,
    PolicyLevel,
    RecommendedAction,
)


class TrustedUserContext(BaseModel):
    """Authenticated server-side identity; never construct from request JSON."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    roles: list[str] = Field(default_factory=list)


class AssignmentContext(AssignmentPolicyContext):
    """Authorized assignment details and authoritative stored policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignment_id: str = Field(min_length=1)
    institution_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    subject: str | None = None
    policy_version: str = Field(min_length=1)


class TutorCommand(BaseModel):
    """Validated tutoring command passed from the API boundary to Layer 3."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=12_000)
    language_preference: str | None = None
    disclosure_acknowledged: bool = False
    document_ids: list[str] = Field(default_factory=list)


class NormalizedInput(BaseModel):
    """Preserved source and normalized text ready for model input formatting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    original_message: str
    normalized_message: str
    original_language: str
    response_language: str
    extracted_document_text: str = ""
    assignment_context: str = ""
    normalization_method: str = "none"


class ModelRecommendation(BaseModel):
    """Untrusted intent and action labels returned by the model client."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: Intent
    recommended_action: RecommendedAction
    model_version: str = Field(min_length=1)


class AgentResult(BaseModel):
    """Stable application result returned by the orchestrator to the API."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    status: Literal["completed", "refused", "needs_clarification", "failed"]
    message: str
    response_language: str | None = None
    intent: Intent | None = None
    selected_action: PermittedAction | None = None
    policy_level: PolicyLevel
    model_version: str | None = None
