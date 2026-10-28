from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


PolicyLevel = Literal["RED", "YELLOW", "GREEN", "NO_AI", "PROCESS_DISCLOSURE", "FULL_INTEGRATION"]


def _canonical_policy_level(value):
    if isinstance(value, str):
        normalized = value.strip().upper()
        return {"NO_AI": "RED", "PROCESS_DISCLOSURE": "YELLOW",
                "FULL_INTEGRATION": "GREEN"}.get(normalized, normalized)
    return value


class PolicyCheck(BaseModel):
    policy_level: str = Field(..., description="RED, YELLOW, or GREEN")


class AssignmentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    policy_level: PolicyLevel
    course: str = Field(default="", max_length=200)
    institution_id: str | None = Field(default=None, max_length=100)
    creator_id: str | None = Field(default=None, max_length=100)
    due_date: datetime | None = None

    @field_validator("policy_level", mode="before")
    @classmethod
    def normalize_policy_level(cls, value):
        return _canonical_policy_level(value)


class AssignmentUpdate(BaseModel):
    policy_level: PolicyLevel

    @field_validator("policy_level", mode="before")
    @classmethod
    def normalize_policy_level(cls, value):
        return _canonical_policy_level(value)


class Assignment(AssignmentCreate):
    id: str
    created_at: datetime
    storage: str


class TutorRequest(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=8000)
    language: str = Field(default="en", min_length=2, max_length=35)
    session_id: str | None = Field(default=None, max_length=100)
    disclosure_acknowledged: bool = False
    provider: Literal["groq", "gemini"] | None = None


class TutorStreamRequest(BaseModel):
    """HTTP body for the streaming tutor adapter; policy is never client supplied."""
    model_config = ConfigDict(extra="forbid")
    assignment_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=32_000)
    language: str = Field(default="en", min_length=2, max_length=35)
    session_id: str = Field(min_length=1, max_length=256)
    provider: Literal["groq", "gemini"]
    disclosure_acknowledged: bool = False
    technical_terms: list[str] = Field(default_factory=list, max_length=200)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.4, ge=0, le=2)


class OrchestrationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=32_000)


class AIOrchestrationRequest(BaseModel):
    """Shape-compatible request passed to the orchestrate guidance service."""
    model_config = ConfigDict(extra="forbid")
    request_id: str
    institution_id: str
    conversation_id: str
    messages: list[OrchestrationMessage] = Field(min_length=1, max_length=100)
    language: str
    policy_tier: Literal["yellow", "green"]
    instruction_profile: Literal["guided", "direct"]
    policy_version: str
    system_instruction: str = Field(max_length=16_000)
    technical_terms: list[str] = Field(default_factory=list, max_length=200)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.4, ge=0, le=2)


class VivaRequest(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=100)
    topic: str = Field(min_length=1, max_length=500)
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    number_of_questions: int = Field(default=5, ge=1, le=10)
    language: Literal["english", "hindi", "hinglish"] = "english"


class VivaEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: str = Field(min_length=1, max_length=100)
