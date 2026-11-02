from types import SimpleNamespace

import pytest

from app.agent.contracts import AssignmentContext, TrustedUserContext
from app.agent.gemini_input import GeminiInputProcessor


class PreprocessingStub:
    def __init__(self):
        self.request = None

    async def preprocess(self, request):
        self.request = request
        return SimpleNamespace(
            processed_text="Explain photosynthesis",
            detected_language="hi",
            extracted_text="Assignment instructions: compare two processes.",
        )


@pytest.mark.asyncio
async def test_gemini_processor_forwards_student_and_document_context_but_keeps_policy_authoritative():
    service = PreprocessingStub()
    processor = GeminiInputProcessor(service)
    assignment = AssignmentContext(
        assignment_id="a-1",
        institution_id="school-1",
        title="Biology worksheet",
        subject="Biology",
        policy_level="YELLOW",
        policy_version="v2",
    )

    normalized = await processor.normalize(
        message="photosynthesis samjhao",
        language_preference="hi",
        document_ids=[],
        user=TrustedUserContext(user_id="u-1", institution_id="school-1"),
        assignment=assignment,
        document_text="OCR text from assignment",
        images=["encoded-image"],
    )

    assert service.request.original_text == "photosynthesis samjhao"
    assert service.request.ocr_text == "OCR text from assignment"
    assert service.request.images == ["encoded-image"]
    assert normalized.normalized_message == "Explain photosynthesis"
    assert normalized.extracted_document_text == "Assignment instructions: compare two processes."
    assert normalized.assignment_context.startswith("Assignment: Biology worksheet")
    assert normalized.policy_flag == "YELLOW"
    assert normalized.original_language == "hi"
    assert normalized.response_language == "hi"
    assert normalized.normalization_method == "gemini_preprocessing"
