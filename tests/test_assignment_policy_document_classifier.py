import json

import pytest

from app.services.policy.document_classifier import AssignmentPolicyDocumentClassifier


class FakeProvider:
    def __init__(self, payload):
        self.payload = payload
        self.prompt = ""

    async def generate_structured(self, prompt, images, schema=None):
        self.prompt = prompt
        assert images == []
        assert schema is not None
        return json.dumps(self.payload)


@pytest.mark.asyncio
async def test_explicit_ban_is_red_with_verbatim_evidence():
    provider = FakeProvider({
        "decision": "RED",
        "reason": "AI assistance is prohibited.",
        "evidence": ["Do not use AI tools for this assignment."],
        "requires_human_review": False,
        "confidence": 0.99,
    })
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "Do not use AI tools for this assignment."
    )
    assert result.policy_tier == "RED"
    assert result.requires_human_review is False
    assert result.evidence == ("Do not use AI tools for this assignment.",)


@pytest.mark.asyncio
async def test_explicit_permission_can_be_green():
    provider = FakeProvider({
        "decision": "GREEN",
        "reason": "AI tools are explicitly permitted.",
        "evidence": ["You may use generative AI tools for any part of this assignment."],
        "requires_human_review": False,
        "confidence": 0.98,
    })
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "You may use generative AI tools for any part of this assignment."
    )
    assert result.policy_tier == "GREEN"


@pytest.mark.asyncio
async def test_missing_policy_is_yellow_and_requires_review():
    provider = FakeProvider({
        "decision": "YELLOW",
        "reason": "No AI policy was found.",
        "evidence": [],
        "requires_human_review": True,
        "confidence": 0.99,
    })
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "Write a 1000-word report about photosynthesis."
    )
    assert result.policy_tier == "YELLOW"
    assert result.requires_human_review is True


@pytest.mark.asyncio
async def test_silent_policy_is_yellow_without_forcing_review():
    provider = FakeProvider({
        "decision": "YELLOW",
        "extracted_text": "",
        "reason": "The readable assignment does not mention AI use.",
        "evidence": [],
        "requires_human_review": False,
        "confidence": 0.99,
    })
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "Write a short report about photosynthesis."
    )
    assert result.policy_tier == "YELLOW"
    assert result.requires_human_review is False


@pytest.mark.asyncio
async def test_low_confidence_green_is_downgraded_to_yellow():
    provider = FakeProvider({
        "decision": "GREEN",
        "reason": "Permission may be limited to brainstorming.",
        "evidence": ["AI may be used for brainstorming."],
        "requires_human_review": False,
        "confidence": 0.42,
    })
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "AI may be used for brainstorming."
    )
    assert result.policy_tier == "YELLOW"
    assert result.requires_human_review is True


@pytest.mark.asyncio
async def test_invalid_or_empty_provider_response_fails_closed():
    empty = FakeProvider({
        "decision": "GREEN",
        "reason": "Permission",
        "evidence": [],
        "requires_human_review": False,
        "confidence": 1.0,
    })
    result = await AssignmentPolicyDocumentClassifier(empty).classify("some text")
    assert result.policy_tier == "YELLOW"
    assert result.requires_human_review is True

    class BrokenProvider:
        async def generate_structured(self, prompt, images, schema=None):
            return "not json"

    result = await AssignmentPolicyDocumentClassifier(BrokenProvider()).classify("some text")
    assert result.policy_tier == "YELLOW"
    assert result.reason == "INVALID_CLASSIFIER_RESPONSE"


@pytest.mark.asyncio
async def test_empty_document_fails_closed_without_calling_provider():
    class NeverCalled:
        async def generate_structured(self, prompt, images, schema=None):
            raise AssertionError("provider must not be called")

    result = await AssignmentPolicyDocumentClassifier(NeverCalled()).classify("  ")
    assert result.policy_tier == "YELLOW"
    assert result.reason == "NO_READABLE_ASSIGNMENT_TEXT"


@pytest.mark.asyncio
async def test_pdf_attachment_is_passed_to_policy_classifier():
    class PdfProvider:
        def __init__(self):
            self.documents = None

        async def generate_structured(self, prompt, documents, schema=None):
            self.documents = documents
            assert "attached assignment document" in prompt
            assert schema is not None
            return json.dumps({
                "decision": "RED",
                "extracted_text": "Do not use AI for this assignment.",
                "reason": "AI use is prohibited.",
                "evidence": ["Do not use AI for this assignment."],
                "requires_human_review": False,
                "confidence": 0.99,
            })

    provider = PdfProvider()
    pdf_bytes = b"%PDF-1.7\npolicy"
    result = await AssignmentPolicyDocumentClassifier(provider).classify(
        "", documents=[("application/pdf", pdf_bytes)]
    )
    assert provider.documents == [("application/pdf", pdf_bytes)]
    assert result.policy_tier == "RED"
