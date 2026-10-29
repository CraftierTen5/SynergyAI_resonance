"""Typed contracts for deterministic policy checks and action execution."""

from enum import Enum

from pydantic import Field

from app.schemas.agent import AssistanceMode, ExecutableAction, StrictModel


class PreflightStatus(str, Enum):
    ALLOWED = "ALLOWED"
    DISCLOSURE_REQUIRED = "DISCLOSURE_REQUIRED"
    REFUSED = "REFUSED"


class PreflightResult(StrictModel):
    allowed: bool
    status: PreflightStatus
    message: str = Field(min_length=1, max_length=2_000)
    assistance_mode: AssistanceMode


class ExecutionResult(StrictModel):
    status: str = Field(min_length=1, max_length=64)
    response: str = Field(max_length=20_000)
    action: ExecutableAction | None = None
    assistance_mode: AssistanceMode
    provider: str | None = Field(default=None, max_length=128)
