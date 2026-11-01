"""Opt-in local route for exercising the complete deterministic workflow."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agent.contracts import AgentResult, TrustedUserContext, TutorCommand
from app.agent.orchestrator import AgentOrchestrator
from app.services.ai.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


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
        except ProviderConfigurationError as exc:
            raise HTTPException(status_code=503, detail="Model inference is not configured") from exc
        except ProviderTimeoutError as exc:
            raise HTTPException(status_code=504, detail="Model inference timed out") from exc
        except ProviderUnavailableError as exc:
            raise HTTPException(status_code=503, detail="Model inference is unavailable") from exc
        except ProviderAuthenticationError as exc:
            raise HTTPException(status_code=502, detail="Model inference authentication failed") from exc
        except ProviderResponseError as exc:
            raise HTTPException(status_code=502, detail="Model inference returned an invalid response") from exc

    return router

