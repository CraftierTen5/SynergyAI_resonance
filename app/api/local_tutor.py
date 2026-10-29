"""Opt-in local route for exercising the complete deterministic workflow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent.contracts import AgentResult, TrustedUserContext, TutorCommand
from app.agent.orchestrator import AgentOrchestrator


def create_local_tutor_router(orchestrator: AgentOrchestrator) -> APIRouter:
    router = APIRouter(prefix="/dev", tags=["local-demo"])
    demo_user = TrustedUserContext(
        user_id="local-demo-student", institution_id="local-demo-institution"
    )

    @router.post("/tutor/chat", response_model=AgentResult)
    async def tutor_chat(command: TutorCommand) -> AgentResult:
        try:
            return await orchestrator.handle(command, demo_user)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Demo assignment not found") from exc

    return router

