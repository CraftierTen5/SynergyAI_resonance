from collections.abc import Callable, Mapping, Sequence

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app.schemas.preprocessing import PolicyEvidence, PreprocessResponse, PreprocessingRequest
from app.services.ai.errors import (
    AIProviderError,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.preprocessing import GeminiPreprocessingService
from app.services.policy.resolver import resolve_policy


def create_preprocess_router(
    preprocessing: GeminiPreprocessingService,
    policy_lookup: Callable[[str], Sequence[Mapping[str, object]] | None] | None = None,
) -> APIRouter:
    """Build a mountable API router; lookup must return authoritative policy records."""
    router = APIRouter()

    @router.post("/preprocess", response_model=PreprocessResponse)
    async def preprocess(
        text: str = Form(default=""),
        assignment_id: str | None = Form(default=None, max_length=128),
        language_hint: str | None = Form(default=None, max_length=35),
        response_language: str = Form(default="en", min_length=2, max_length=35),
        assignment_context: str | None = Form(default=None),
        ocr_text: str | None = Form(default=None),
        disclosure_acknowledged: bool = Form(default=False),
        files: list[UploadFile] = File(default=[]),
    ) -> PreprocessResponse:
        if len(files) > preprocessing.max_images:
            raise HTTPException(status_code=413, detail="Too many images")
        if len(text) > preprocessing.max_text_length:
            raise HTTPException(status_code=413, detail="Text exceeds configured size limit")
        records: Sequence[Mapping[str, object]] | None = ()
        if assignment_id:
            if policy_lookup is None:
                records = ()
            else:
                try:
                    records = policy_lookup(assignment_id)
                except Exception as exc:
                    raise HTTPException(status_code=503, detail="Assignment policy lookup is unavailable") from exc
            initial_policy = resolve_policy(assignment_id=assignment_id, authoritative_records=records)
            if initial_policy.policy_flag == "RED":
                raise HTTPException(status_code=403, detail={"code": "AI_USE_PROHIBITED", "reason": initial_policy.resolution_reason})
            if initial_policy.policy_flag == "UNKNOWN":
                raise HTTPException(status_code=409, detail={"code": initial_policy.resolution_reason, "policy_flag": "UNKNOWN"})
            if initial_policy.policy_flag == "YELLOW" and not disclosure_acknowledged:
                raise HTTPException(status_code=428, detail={"code": "DISCLOSURE_ACK_REQUIRED", "required_conditions": initial_policy.required_conditions})
        else:
            initial_policy = None
        encoded_images: list[str] = []
        import base64

        for upload in files:
            content = await upload.read(preprocessing.max_image_bytes + 1)
            if len(content) > preprocessing.max_image_bytes:
                raise HTTPException(status_code=413, detail="Image exceeds configured size limit")
            encoded_images.append(base64.b64encode(content).decode("ascii"))
        try:
            request = PreprocessingRequest(
                original_text=text, language_hint=language_hint, response_language=response_language,
                assignment_context=assignment_context,
                ocr_text=ocr_text, images=encoded_images,
            )
            result = await preprocessing.preprocess(request)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Invalid preprocessing input") from exc
        except AIProviderError as exc:
            if isinstance(exc, ProviderTimeoutError):
                status = 504
            elif isinstance(exc, ProviderRateLimitError):
                status = 503
            elif isinstance(exc, ProviderUnavailableError):
                status = 503
            elif isinstance(exc, ProviderAuthenticationError | ProviderConfigurationError):
                status = 503
            else:
                status = 502
            raise HTTPException(status_code=status, detail="Preprocessing provider failed; original submission was not modified") from exc
        # Only extract candidate policy when there is no authoritative setting to use.
        source = assignment_context or result.extracted_text or ocr_text or ""
        source_type = "assignment_instructions" if assignment_context else "ocr"
        if initial_policy is not None:
            policy = initial_policy
        else:
            try:
                evidence = await preprocessing.extract_policy_evidence(source, source_type=source_type) if source else []
            except AIProviderError:
                evidence = [PolicyEvidence(source_text="", statement="Policy extraction failed", source_type=source_type, source_reference=source_type, ambiguous=True, requires_human_review=True)] if source else []
            policy = resolve_policy(assignment_id=None, evidence=evidence)
        return PreprocessResponse(**result.model_dump(), policy=policy)

    return router
