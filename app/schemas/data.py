"""Schemas for data-layer records exchanged between services and the stores.

These models describe what the data layer accepts and returns. They deliberately
exclude any direct identifier from assessment records: an assessment row refers
to a student only through a pseudonymous id (NFR-PRV-01).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PolicyTier = Literal["RED", "YELLOW", "GREEN"]
InstructionProfile = Literal["guided", "direct"]
MeasureType = Literal["equity", "compliance", "mastery", "device_parity"]

_PSEUDONYM_PATTERN = r"^stu_[0-9a-f]{32}$"


class Institution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=256)
    data_region: str = Field(default="ap-south-1", min_length=1, max_length=64)
    retention_profile: dict[str, Any] = Field(default_factory=dict)


class Course(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_id: str | None = None
    institution_id: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    academic_term: str = Field(min_length=1, max_length=64)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: str | None = None
    institution_id: str = Field(min_length=1, max_length=128)
    course_id: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=256)
    policy_tier: PolicyTier
    due_at: datetime | None = None


class PolicyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_version_id: str | None = None
    institution_id: str = Field(min_length=1, max_length=128)
    assignment_id: str = Field(min_length=1)
    version_label: str = Field(min_length=1, max_length=128)
    policy_tier: PolicyTier
    instruction_profile: InstructionProfile
    system_instruction: str = Field(default="", max_length=16_000)
    log_required: bool = False


class ProcessLogRecord(BaseModel):
    """An anonymised process-log row; never carries a direct identifier."""

    model_config = ConfigDict(extra="forbid")

    institution_id: str = Field(min_length=1, max_length=128)
    assignment_id: str = Field(min_length=1)
    pseudonymous_id: str = Field(pattern=_PSEUDONYM_PATTERN)
    step_number: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=120)
    language: str | None = Field(default=None, max_length=35)
    prompt_digest: str | None = Field(default=None, max_length=128)
    summary: str | None = Field(default=None, max_length=4_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime

    @field_validator("summary")
    @classmethod
    def reject_inline_email(cls, value: str | None) -> str | None:
        if value is not None and "@" in value:
            raise ValueError("process-log summaries must not contain an email address")
        return value


class MeasureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution_id: str = Field(min_length=1, max_length=128)
    measure_type: MeasureType
    dimension: str = Field(min_length=1, max_length=128)
    dimension_value: str = Field(min_length=1, max_length=256)
    period: str = Field(min_length=1, max_length=32)
    cohort_size: int = Field(ge=0)
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float | None = Field(default=None, ge=0, le=1)


class AuditEventRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    institution_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    actor_pseudonymous_id: str | None = Field(default=None, pattern=_PSEUDONYM_PATTERN)
    course_id: str | None = None
    assignment_id: str | None = None
    policy_version: str | None = None
    policy_level: str | None = None
    allowed: bool | None = None
    reason: str | None = Field(default=None, max_length=1_000)
    prompt_sha256: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
