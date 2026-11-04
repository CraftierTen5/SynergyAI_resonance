import pytest

from app.agent.contracts import AssignmentContext, NormalizedInput
from app.agent.provider_response import ProviderResponseGenerator
from app.core.config import Settings
from app.schemas.ai import AITutorResponse
from app.services.ai.groq import GroqAdapter


@pytest.mark.asyncio
async def test_uploaded_document_text_is_sent_to_the_generation_provider(monkeypatch):
    captured = []

    async def fake_completion(self, request):
        captured.append(request)
        return AITutorResponse(
            request_id=request.request_id,
            content="The PDF describes a relational database model.",
            provider="groq",
            model="test-model",
        )

    monkeypatch.setattr(GroqAdapter, "generate_completion", fake_completion)
    settings = Settings(_env_file=None, GROQ_API_KEY="unit-test-key")
    normalized = NormalizedInput(
        original_message="Explain this attachment",
        normalized_message="Explain this attachment",
        original_language="en",
        response_language="en",
        extracted_document_text="Chapter 7 explains entity relationship models and database design.",
    )
    assignment = AssignmentContext(
        assignment_id="assignment-1",
        institution_id="school-1",
        title="Database homework",
        subject="Databases",
        policy_level="YELLOW",
        policy_version="v1",
    )

    result = await ProviderResponseGenerator(settings).generate(
        normalized, assignment, "EXPLAIN_CONCEPT", "FULL_ASSISTANCE", "CONCEPT_EXPLANATION"
    )

    assert result == "The PDF describes a relational database model."
    assert "entity relationship models" in captured[0].messages[1].content
    assert "Explain this attachment" in captured[0].messages[1].content
