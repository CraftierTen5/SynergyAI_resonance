"""Shared, validated contracts for the tutoring agent workflow."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for API and internal contracts: reject coercion and unknown fields."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class PolicyLevel(str, Enum):
    RED = "RED"
    YELLOW = "YELLOW"
    GREEN = "GREEN"


class Intent(str, Enum):
    CONCEPT_EXPLANATION = "CONCEPT_EXPLANATION"
    BRAINSTORMING = "BRAINSTORMING"
    REQUEST_FOR_HINT = "REQUEST_FOR_HINT"
    CODE_FEEDBACK = "CODE_FEEDBACK"
    ANSWER_REQUEST = "ANSWER_REQUEST"
    LANGUAGE_SUPPORT = "LANGUAGE_SUPPORT"
    SUMMARY_REQUEST = "SUMMARY_REQUEST"
    AMBIGUOUS = "AMBIGUOUS"


class RecommendedAction(str, Enum):
    EXPLAIN_CONCEPT = "EXPLAIN_CONCEPT"
    GUIDE_PLANNING = "GUIDE_PLANNING"
    GIVE_HINT = "GIVE_HINT"
    REVIEW_CODE = "REVIEW_CODE"
    CHECK_POLICY = "CHECK_POLICY"
    CLARIFY_REQUEST = "CLARIFY_REQUEST"
    TRANSLATE_OR_EXPLAIN = "TRANSLATE_OR_EXPLAIN"
    SUMMARIZE = "SUMMARIZE"
    GENERATE_SOLUTION = "GENERATE_SOLUTION"
    PROVIDE_DIRECT_ANSWER = "PROVIDE_DIRECT_ANSWER"
    PROVIDE_HINT = "PROVIDE_HINT"
    ASK_CLARIFYING_QUESTION = "ASK_CLARIFYING_QUESTION"


class ExecutableAction(str, Enum):
    REFUSE = "REFUSE"
    EXPLAIN_CONCEPT = "EXPLAIN_CONCEPT"
    GUIDE_PLANNING = "GUIDE_PLANNING"
    GIVE_HINT = "GIVE_HINT"
    PROVIDE_HINT = "PROVIDE_HINT"
    ASK_CLARIFYING_QUESTION = "ASK_CLARIFYING_QUESTION"
    GENERATE_SOLUTION = "GENERATE_SOLUTION"
    PROVIDE_DIRECT_ANSWER = "PROVIDE_DIRECT_ANSWER"
    GUIDED_FEEDBACK = "GUIDED_FEEDBACK"
    LANGUAGE_SUPPORT = "LANGUAGE_SUPPORT"
    SUMMARIZE = "SUMMARIZE"
    CLARIFY = "CLARIFY"
    FULL_ASSISTANCE = "FULL_ASSISTANCE"


class AssistanceMode(str, Enum):
    DIRECT_ANSWER = "DIRECT_ANSWER"
    GUIDED_EXPLANATION = "GUIDED_EXPLANATION"
    SOCRATIC_PROMPT = "SOCRATIC_PROMPT"
    REFUSAL = "REFUSAL"


class Principal(StrictModel):
    """Authenticated identity supplied by the server, never by request JSON."""

    user_id: str = Field(min_length=1, max_length=128)
    institution_id: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=list)


class TutorChatRequest(StrictModel):
    assignment_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=12_000)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_ids: list[str] = Field(default_factory=list, max_length=8)
    response_language: str | None = Field(default=None, min_length=1, max_length=32)
    disclosure_acknowledged: bool = False


class AssignmentSnapshot(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    institution_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    subject: str | None = Field(default=None, max_length=256)
    policy_level: PolicyLevel
    policy_version: int = Field(ge=0)


class PreparedInput(StrictModel):
    original_message: str = Field(min_length=1, max_length=12_000)
    normalized_message: str = Field(min_length=1, max_length=24_000)
    detected_language: str = Field(min_length=1, max_length=32)
    response_language: str = Field(min_length=1, max_length=32)
    ocr_text: str = Field(default="", max_length=100_000)
    assignment_context: str = Field(default="", max_length=20_000)
    normalization_version: str = Field(default="input-v1", min_length=1, max_length=64)


class ModelPrediction(StrictModel):
    intent: Intent
    recommended_action: RecommendedAction
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    model_version: str = Field(min_length=1, max_length=128)


class TutorChatResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    status: str = Field(min_length=1, max_length=64)
    assignment_id: str = Field(min_length=1, max_length=128)
    policy_level: PolicyLevel
    assistance_mode: AssistanceMode | None = None
    intent: Intent | None = None
    action: ExecutableAction | None = None
    response: str = Field(max_length=20_000)
    model_version: str | None = Field(default=None, max_length=128)
    provider: str | None = Field(default=None, max_length=128)
