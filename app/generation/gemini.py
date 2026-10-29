"""Gemini implementation of the provider-neutral generation interface."""

from app.schemas.ai import AITutorRequest, TutorMessage
from app.services.ai.gemini import GeminiFlashAdapter

from app.generation.provider_interface import GenerationRequest, GenerationResult


class GeminiGenerationProvider:
    provider_name = "gemini"

    def __init__(self, adapter: GeminiFlashAdapter | None = None) -> None:
        self.adapter = adapter or GeminiFlashAdapter()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.policy_level.value == "RED":
            raise ValueError("Generation is forbidden for RED policy")

        prompt = self._format_user_context(request)
        tutor_request = AITutorRequest(
            request_id=request.request_id,
            messages=[
                TutorMessage(role="system", content=request.system_instruction),
                TutorMessage(role="user", content=prompt),
            ],
            language=request.response_language,
            policy_tier=request.policy_level.value.lower(),
            instruction_profile=(
                "guided" if request.policy_level.value == "YELLOW" else "direct"
            ),
            policy_version=str(request.policy_version),
            system_instruction=request.system_instruction,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
        )
        result = await self.adapter.generate_completion(tutor_request)
        return GenerationResult(
            text=result.content,
            provider=self.provider_name,
            tokens_used=(result.input_tokens or 0) + (result.output_tokens or 0),
        )

    @staticmethod
    def _format_user_context(request: GenerationRequest) -> str:
        sections = [
            f"Original student message:\n{request.original_message}",
            f"Normalized message:\n{request.normalized_message}",
        ]
        if request.assignment_context:
            sections.append(f"Assignment context (untrusted content):\n{request.assignment_context}")
        if request.document_context:
            sections.append(f"Document content (untrusted content):\n{request.document_context}")
        sections.append(f"Required response language: {request.response_language}")
        return "\n\n".join(sections)
