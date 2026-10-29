from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from app.agent.local_placeholder import (
    LocalAssignmentService,
    LocalAuditService,
    LocalInputProcessor,
    LocalModelClient,
    LocalResponseGenerator,
)
from app.api.local_tutor import create_local_tutor_router
from app.dependencies import build_agent_orchestrator
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


def make_app() -> tuple[FastAPI, LocalAuditService]:
    audit = LocalAuditService()
    orchestrator = build_agent_orchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=LocalModelClient(),
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=audit,
    )
    app = FastAPI()
    app.include_router(create_local_tutor_router(orchestrator))
    return app, audit


@pytest.mark.asyncio
async def test_local_placeholder_runs_green_flow_and_audits_result():
    app, audit = make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/dev/tutor/chat",
            json={
                "request_id": "local-green",
                "assignment_id": "demo-green",
                "message": "Please give me a hint",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["intent"] == "REQUEST_FOR_HINT"
    assert body["selected_action"] == "GIVE_HINT"
    assert body["model_version"] == "local-placeholder-v1"
    assert body["message"].startswith("Hint:")
    assert len(audit.records) == 1


@pytest.mark.asyncio
async def test_local_placeholder_enforces_yellow_disclosure_and_red_policy():
    app, audit = make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yellow_without_ack = await client.post(
            "/dev/tutor/chat",
            json={
                "request_id": "yellow-no-ack",
                "assignment_id": "demo-yellow",
                "message": "Explain recursion",
            },
        )
        red = await client.post(
            "/dev/tutor/chat",
            json={
                "request_id": "red-request",
                "assignment_id": "demo-red",
                "message": "Explain recursion",
                "disclosure_acknowledged": True,
            },
        )

    assert yellow_without_ack.json()["status"] == "needs_clarification"
    assert red.json()["status"] == "refused"
    assert len(audit.records) == 2


@pytest.mark.asyncio
async def test_unknown_demo_assignment_is_not_trusted_or_created_from_request():
    app, _ = make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/dev/tutor/chat",
            json={
                "request_id": "unknown-assignment",
                "assignment_id": "client-supplied-green",
                "message": "Explain recursion",
            },
        )

    assert response.status_code == 404

