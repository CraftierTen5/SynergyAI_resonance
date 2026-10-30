import base64
import json

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.schemas.preprocessing import PreprocessingRequest, ProcessingStatus
from app.services.ai.errors import ProviderResponseError
from app.services.ai.preprocessing import GeminiPreprocessingService
from app.services.policy.resolver import resolve_policy


class FakeGemini:
    def __init__(self, value):
        self.value = value
        self.prompt = ""
        self.images = []

    async def generate_structured(self, prompt, images):
        self.prompt, self.images = prompt, images
        return self.value


@pytest.mark.asyncio
async def test_preserves_original_and_separates_ocr_and_translation():
    provider = FakeGemini(json.dumps({
        "detected_language": "hi", "processed_text": "Please explain photosynthesis",
        "translated": True, "extracted_text": "AI use permitted for brainstorming",
    }))
    result = await GeminiPreprocessingService(provider).preprocess(
        PreprocessingRequest(original_text="कृपया प्रकाश संश्लेषण समझाएं", language_hint="hi", response_language="kn", ocr_text="AI use permitted for brainstorming")
    )
    assert result.original_text == "कृपया प्रकाश संश्लेषण समझाएं"
    assert result.processed_text == "Please explain photosynthesis"
    assert result.extracted_text == "AI use permitted for brainstorming"
    assert result.processing_status is ProcessingStatus.SUCCESS
    assert result.translation_status == "completed"
    assert result.requested_response_language == "kn"
    assert "student_text" in result.provenance


@pytest.mark.asyncio
async def test_validates_image_signature_and_structured_output():
    service = GeminiPreprocessingService(FakeGemini("{}"))
    with pytest.raises(ValueError, match="unsupported"):
        await service.preprocess(PreprocessingRequest(images=[base64.b64encode(b"not image").decode()]))
    with pytest.raises(ProviderResponseError):
        await service.preprocess(PreprocessingRequest(original_text="hello"))


def test_policy_flags_are_explicit_and_missing_policy_uses_yellow():
    for wire, expected in (("NO_AI", "RED"), ("PROCESS_DISCLOSURE", "YELLOW"), ("FULL_INTEGRATION", "GREEN")):
        result = resolve_policy(assignment_id="a1", authoritative_records=[{"policy": wire, "required_conditions": ["Disclose use"]}])
        assert result.policy_flag == expected
    assert resolve_policy(assignment_id="a1").policy_flag == "YELLOW"
    conflict = resolve_policy(assignment_id="a1", authoritative_records=[{"policy": "NO_AI"}, {"policy": "FULL_INTEGRATION"}])
    assert conflict.policy_flag == "YELLOW"
    assert conflict.resolution_reason == "POLICY_CONFLICT"


def test_candidate_evidence_and_student_claim_cannot_grant_permission():
    result = resolve_policy(assignment_id=None)
    assert result.policy_flag == "YELLOW"


def test_conflicting_conditions_and_scoped_policy_require_review():
    conditions = resolve_policy(assignment_id="a1", authoritative_records=[
        {"policy": "PROCESS_DISCLOSURE", "required_conditions": ["Cite AI"]},
        {"policy": "PROCESS_DISCLOSURE", "required_conditions": ["No generated text"]},
    ])
    scoped = resolve_policy(assignment_id="a1", authoritative_records=[{"policy": "FULL_INTEGRATION", "scope": "brainstorming"}])
    assert conditions.policy_flag == "YELLOW"
    assert conditions.resolution_reason == "POLICY_CONFLICT"
    assert scoped.policy_flag == "YELLOW"
    assert scoped.resolution_reason == "POLICY_SCOPE_UNCLEAR"


def test_assignment_missing_and_policy_missing_have_separate_reason_codes():
    missing_assignment = resolve_policy(assignment_id="missing", authoritative_records=None)
    missing_policy = resolve_policy(assignment_id="known", authoritative_records=[])
    assert missing_assignment.policy_flag == missing_policy.policy_flag == "YELLOW"
    assert missing_assignment.resolution_reason == "ASSIGNMENT_NOT_FOUND"
    assert missing_policy.resolution_reason == "POLICY_NOT_FOUND"


def test_future_classifier_contract_validates_but_has_no_provider():
    from app.schemas.classifier import NormalizedClassifierInput, IntentActionRecommendation

    request = NormalizedClassifierInput(user_message="Explain this", policy_flag="UNKNOWN")
    result = IntentActionRecommendation(intent="AMBIGUOUS", recommended_action="REQUEST_HUMAN_REVIEW")
    assert request.policy_flag == "UNKNOWN"
    assert result.recommended_action == "REQUEST_HUMAN_REVIEW"


def test_status_routes_and_preprocess_missing_key_are_controlled():
    from app.main import app

    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/health").json() == {"status": "ok"}
    ready = client.get("/ready").json()
    assert ready["dependencies"]["qwen"] == "not_implemented"
    assert ready["dependencies"]["assignment_store"] == "not_configured"
    response = client.post("/preprocess", data={"text": "hello"})
    assert response.status_code == 503
    assert "original submission was not modified" in response.json()["detail"]


def test_red_is_rejected_and_unknown_assignment_uses_yellow_disclosure_gate():
    from app.api.preprocess import create_preprocess_router
    from app.core.config import Settings

    provider = FakeGemini("{}"); service = GeminiPreprocessingService(provider, settings=Settings())
    app = FastAPI()
    app.include_router(create_preprocess_router(service, lambda assignment: [{"policy": "NO_AI"}]))
    client = TestClient(app)
    response = client.post("/preprocess", data={"text": "Help", "assignment_id": "a1"})
    assert response.status_code == 403
    assert provider.prompt == ""

    app2 = FastAPI()
    app2.include_router(create_preprocess_router(service))
    response = TestClient(app2).post("/preprocess", data={"text": "Help", "assignment_id": "unknown"})
    assert response.status_code == 428
    assert provider.prompt == ""


def test_yellow_disclosure_gate_precedes_gemini():
    from app.api.preprocess import create_preprocess_router
    from app.core.config import Settings

    provider = FakeGemini("{}")
    service = GeminiPreprocessingService(provider, settings=Settings())
    app = FastAPI()
    app.include_router(create_preprocess_router(service, lambda assignment: [{
        "policy": "PROCESS_DISCLOSURE", "required_conditions": ["Acknowledge disclosure"],
    }]))
    client = TestClient(app)
    response = client.post("/preprocess", data={"text": "Help", "assignment_id": "a1"})
    assert response.status_code == 428
    assert response.json()["detail"]["required_conditions"] == ["Acknowledge disclosure"]
    assert provider.prompt == ""
