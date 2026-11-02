"""Gemini preprocessing adapter used only after assignment policy preflight."""

from app.agent.contracts import AssignmentContext, NormalizedInput, TrustedUserContext
from app.schemas.preprocessing import PreprocessingRequest
from app.services.ai.preprocessing import GeminiPreprocessingService


class GeminiInputProcessor:
    """Normalize student input and extract uploaded image text for ResQwen."""

    def __init__(self, service: GeminiPreprocessingService) -> None:
        self.service = service

    async def normalize(
        self,
        message: str,
        language_preference: str | None,
        document_ids: list[str],
        user: TrustedUserContext,
        assignment: AssignmentContext,
        document_text: str = "",
        images: list[str] | None = None,
    ) -> NormalizedInput:
        # IDs remain metadata for future document-store resolution; never treat
        # them as document contents or allow them to supply policy.
        del document_ids, user
        response_language = language_preference or "en"
        assignment_context = (
            f"Assignment: {assignment.title}\n"
            f"Subject: {assignment.subject or 'General'}"
        )
        result = await self.service.preprocess(
            PreprocessingRequest(
                original_text=message,
                response_language=response_language,
                assignment_context=assignment_context,
                ocr_text=document_text or None,
                images=images or [],
            )
        )
        return NormalizedInput(
            original_message=message,
            normalized_message=result.processed_text or message,
            original_language=result.detected_language or "unknown",
            response_language=response_language,
            # Always use the policy retrieved from the authorized assignment.
            policy_flag=assignment.policy_level,
            extracted_document_text=result.extracted_text[:3_800],
            assignment_context=assignment_context,
            normalization_method="gemini_preprocessing",
        )
