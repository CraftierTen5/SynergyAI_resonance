"""Validated persistence inputs for assessment and document records."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

_PSEUDONYM_PATTERN = r"^stu_[0-9a-f]{32}$"


class ModelVersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=128)
    registry_ref: str | None = Field(default=None, max_length=2048)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["registered", "active", "retired", "failed"] = "registered"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID | None = None
    uploader_pseudonymous_id: str | None = Field(default=None, pattern=_PSEUDONYM_PATTERN)
    display_name: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0, le=9_223_372_036_854_775_807)
    storage_key: str = Field(min_length=1, max_length=2048)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "processing", "ready", "failed", "expired"] = "pending"
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def reject_url_storage_reference(self) -> "DocumentInput":
        parts = self.storage_key.replace("\\", "/").split("/")
        if "://" in self.storage_key or self.storage_key.startswith("/") or any(
            part in {"", ".", ".."} for part in parts
        ) or re.match(r"^[A-Za-z]:", self.storage_key):
            raise ValueError("storage_key must be a private relative object key, not a path or URL")
        if "/" in self.display_name or "\\" in self.display_name or any(
            ord(char) < 32 for char in self.display_name
        ):
            raise ValueError("display_name must be a safe filename without path components")
        return self


class ExtractionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    status: Literal["pending", "processing", "completed", "failed", "expired"] = "pending"
    extractor_version: str | None = Field(default=None, max_length=128)
    extracted_text: str | None = Field(default=None, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=256)
    message_id: UUID | None = None
    agent_run_id: UUID | None = None
    actor_pseudonymous_id: str | None = Field(default=None, pattern=_PSEUDONYM_PATTERN)
    feedback_type: Literal["helpful", "not_helpful", "report", "rating"]
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_target_and_rating(self) -> "FeedbackInput":
        if self.message_id is None and self.agent_run_id is None:
            raise ValueError("feedback must reference a message or agent run")
        if (self.feedback_type == "rating") != (self.rating is not None):
            raise ValueError("rating is required only for rating feedback")
        return self
