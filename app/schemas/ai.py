from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TutorMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=32_000)


class AITutorRequest(BaseModel):
    """Provider-neutral tutoring input; policy approval is supplied by Layer 3A."""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    institution_id: str | None = Field(default=None, min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, min_length=1, max_length=256)
    messages: list[TutorMessage] = Field(min_length=1, max_length=100)
    language: str = Field(default="en", min_length=2, max_length=35)
    policy_tier: Literal["yellow", "green"]
    instruction_profile: Literal["guided", "direct"]
    policy_version: str = Field(min_length=1, max_length=128)
    system_instruction: str = Field(default="", max_length=16_000)
    technical_terms: list[str] = Field(default_factory=list, max_length=200)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.4, ge=0, le=2)


class AITutorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    content: str
    provider: str
    model: str
    finish_reason: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class StreamChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    delta: str = ""
    provider: str
    model: str
    finish_reason: str | None = None
    is_final: bool = False
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
