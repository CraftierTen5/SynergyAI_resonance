from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI

from app.agent.contracts import NormalizedInput, TutorCommand, TrustedUserContext
from app.agent.orchestrator import AgentOrchestrator
from app.agent.local_placeholder import (
    LocalAssignmentService,
    LocalAuditService,
    LocalInputProcessor,
    LocalResponseGenerator,
)
from app.api.local_tutor import create_local_tutor_router
from app.core.config import Settings
from app.dependencies import build_agent_orchestrator
from app.services.ai.errors import ProviderResponseError, ProviderTimeoutError, ProviderUnavailableError
from app.services.ai.qwen_inference import QwenInferenceModelClient
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


def make_settings() -> Settings:
    return Settings(
        MODEL_INFERENCE_URL="https://gpu.example.test/predict",
        MODEL_INFERENCE_API_KEY="test-api-key",
        MODEL_INFERENCE_TIMEOUT=120,
    )


def normalized(message: str = "Explain Newton's second law", policy_flag: str = "GREEN") -> NormalizedInput:
    return NormalizedInput(
        original_message=message,
        normalized_message=message,
        original_language="en",
        response_language="en",
        policy_flag=policy_flag,
        assignment_context="Physics assignment",
    )


def response(payload: object) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_successful_concept_prediction_uses_documented_request_contract():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"intent": "CONCEPT_EXPLANATION", "recommended_action": "EXPLAIN_CONCEPT"},
        )

    client = QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler))
    prediction = await client.classify(normalized())

    assert prediction.intent == "CONCEPT_EXPLANATION"
    assert prediction.recommended_action == "EXPLAIN_CONCEPT"
    assert prediction.model_version == "qwen3.5-4b-lora"
    assert captured[0].url.path == "/predict"
    assert captured[0].headers["X-API-Key"] == "test-api-key"
    assert json.loads(captured[0].content) == {
        "user_message": "Explain Newton's second law",
        "policy_flag": "GREEN",
        "context": "Physics assignment",
    }


@pytest.mark.asyncio
async def test_answer_request_cannot_override_yellow_assignment_policy():
    observed: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"intent": "ANSWER_REQUEST", "recommended_action": "CHECK_POLICY"},
        )

    qwen = QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler))
    audit = LocalAuditService()
    orchestrator = build_agent_orchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=qwen,
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=audit,
    )
    result = await orchestrator.handle(
        TutorCommand(
            request_id="yellow-answer",
            assignment_id="demo-yellow",
            message="Give me the final answer",
            disclosure_acknowledged=True,
        ),
        TrustedUserContext(user_id="demo-student", institution_id="demo-school"),
    )

    assert observed[0]["policy_flag"] == "YELLOW"
    assert result.status == "completed"
    assert result.policy_level == "YELLOW"
    assert result.selected_action == "GUIDE_PLANNING"
    assert result.model_version == "qwen3.5-4b-lora"


@pytest.mark.asyncio
async def test_ambiguous_prediction_becomes_clarification_not_generation():
    client = QwenInferenceModelClient(
        make_settings(),
        transport=response({"intent": "AMBIGUOUS", "recommended_action": "CLARIFY_REQUEST"}),
    )
    orchestrator = build_agent_orchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=client,
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=LocalAuditService(),
    )
    result = await orchestrator.handle(
        TutorCommand(request_id="ambiguous", assignment_id="demo-green", message="help"),
        TrustedUserContext(user_id="demo-student", institution_id="demo-school"),
    )

    assert result.status == "needs_clarification"
    assert result.intent == "AMBIGUOUS"
    assert result.selected_action == "CLARIFY_REQUEST"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "NOT_A_LABEL", "recommended_action": "EXPLAIN_CONCEPT"},
        {"intent": "CONCEPT_EXPLANATION", "recommended_action": "RUN_ARBITRARY_TOOL"},
    ],
)
async def test_invalid_intent_or_action_is_rejected(payload):
    client = QwenInferenceModelClient(make_settings(), transport=response(payload))
    with pytest.raises(ProviderResponseError):
        await client.classify(normalized())


@pytest.mark.asyncio
async def test_malformed_json_response_is_rejected():
    client = QwenInferenceModelClient(
        make_settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})
        ),
    )
    with pytest.raises(ProviderResponseError):
        await client.classify(normalized())


@pytest.mark.asyncio
async def test_inference_timeout_is_mapped_to_provider_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderTimeoutError):
        await client.classify(normalized())


@pytest.mark.asyncio
async def test_unavailable_inference_server_is_mapped_without_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderUnavailableError):
        await client.classify(normalized())


@pytest.mark.asyncio
async def test_tutor_route_returns_safe_unavailable_response_without_mock_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    audit = LocalAuditService()
    orchestrator = AgentOrchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler)),
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=audit,
    )
    app = FastAPI()
    app.include_router(create_local_tutor_router(orchestrator))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/dev/tutor/chat",
            json={
                "request_id": "unavailable",
                "assignment_id": "demo-green",
                "message": "Explain the concept",
            },
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Model inference is unavailable"}
    assert len(audit.records) == 0


@pytest.mark.asyncio
async def test_policy_gate_runs_before_model_and_red_never_calls_inference():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append("inference")
        return httpx.Response(
            200,
            json={"intent": "CONCEPT_EXPLANATION", "recommended_action": "EXPLAIN_CONCEPT"},
        )

    model = QwenInferenceModelClient(make_settings(), transport=httpx.MockTransport(handler))
    audit = LocalAuditService()
    orchestrator = build_agent_orchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=model,
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=audit,
    )
    result = await orchestrator.handle(
        TutorCommand(request_id="red", assignment_id="demo-red", message="Explain this"),
        TrustedUserContext(user_id="demo-student", institution_id="demo-school"),
    )

    assert result.status == "refused"
    assert calls == []
