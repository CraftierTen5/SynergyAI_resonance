"""Provider-neutral contracts for policy-approved response generation."""

from typing import Protocol

from pydantic import Field

from app.schemas.agent import (
    AssistanceMode,
    ExecutableAction,
    PolicyLevel,
    StrictModel,
)


class GenerationRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    assignment_id: str = Field(min_length=1, max_length=128)
    policy_level: PolicyLevel
    policy_version: int = Field(ge=0)
    action: ExecutableAction
    assistance_mode: AssistanceMode
    original_message: str = Field(min_length=1, max_length=12_000)
    normalized_message: str = Field(min_length=1, max_length=24_000)
    response_language: str = Field(min_length=1, max_length=32)
    assignment_context: str = Field(default="", max_length=20_000)
    document_context: str = Field(default="", max_length=100_000)
    system_instruction: str = Field(min_length=1, max_length=16_000)
    max_output_tokens: int = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)


class GenerationResult(StrictModel):
    text: str = Field(min_length=1, max_length=20_000)
    provider: str = Field(min_length=1, max_length=64)
    tokens_used: int = Field(ge=0)


class GenerationProvider(Protocol):
    """One configured provider implementation; provider choice stays server-side."""

    provider_name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult: ...
