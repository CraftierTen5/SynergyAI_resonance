from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ProcessingStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILURE = "failure"


class PreprocessingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_text: str = Field(default="", max_length=500_000)
    language_hint: str | None = Field(default=None, max_length=35)
    response_language: str = Field(default="en", min_length=2, max_length=35)
    assignment_context: str | None = Field(default=None, max_length=50_000)
    ocr_text: str | None = Field(default=None, max_length=50_000)
    images: list[str] = Field(default_factory=list, max_length=10, description="Base64 encoded image bytes")


class PreprocessingResult(BaseModel):
    original_text: str
    processed_text: str
    detected_language: str = "unknown"
    requested_response_language: str = "en"
    translation_performed: bool = False
    translation_status: str = "not_required"
    extracted_text: str = ""
    extraction_performed: bool = False
    extraction_status: str = "not_requested"
    provenance: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    processing_status: ProcessingStatus


class PolicyEvidence(BaseModel):
    source_text: str = Field(max_length=1_000)
    statement: str
    source_type: str = "assignment_instructions"
    source_reference: str | None = None
    scope: str | None = None
    ambiguous: bool = False
    requires_human_review: bool = False


class PolicyResolution(BaseModel):
    policy_flag: str
    canonical_policy: str | None = None
    policy_source: str | None = None
    policy_version: str | None = None
    policy_evidence: list[PolicyEvidence] = Field(default_factory=list)
    requires_human_review: bool = False
    resolution_reason: str
    required_conditions: list[str] = Field(default_factory=list)


class PreprocessResponse(PreprocessingResult):
    policy: PolicyResolution
