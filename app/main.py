"""FastAPI application entry point for the implemented preprocessing MVP."""

from fastapi import FastAPI

from app.agent.local_placeholder import (
    LocalAssignmentService,
    LocalAuditService,
    LocalInputProcessor,
    LocalModelClient,
    LocalResponseGenerator,
)
from app.api.local_tutor import create_local_tutor_router
from app.api.preprocess import create_preprocess_router
from app.core.config import get_settings
from app.dependencies import build_agent_orchestrator
from app.services.ai.gemini import GeminiFlashAdapter
from app.services.ai.preprocessing import GeminiPreprocessingService
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


settings = get_settings()
preprocessing = GeminiPreprocessingService(GeminiFlashAdapter(settings), settings=settings)

app = FastAPI(
    title="ResonanceAI Backend",
    description=(
        "Multilingual preprocessing with an optional deterministic local tutoring pipeline."
    ),
    version="0.1.0",
)
app.include_router(create_preprocess_router(preprocessing))

if settings.local_placeholder_enabled:
    local_orchestrator = build_agent_orchestrator(
        assignments=LocalAssignmentService(),
        policy_actions=PolicyActionService(),
        input_processor=LocalInputProcessor(),
        model_client=LocalModelClient(),
        response_generator=LocalResponseGenerator(),
        response_guard=ResponseGuard(),
        audit_service=LocalAuditService(),
    )
    app.include_router(create_local_tutor_router(local_orchestrator))


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": "ResonanceAI Backend", "status": "ok"}


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, object]:
    # Gemini and PostgreSQL are optional for process startup; readiness reports their
    # configuration without claiming external connectivity or Qwen availability.
    key = settings.gemini_api_key
    return {
        "status": "ready",
        "dependencies": {
            "gemini_configured": bool(key and key.get_secret_value()),
            "assignment_store": "not_configured",
            "qwen": "not_implemented",
            "local_placeholder": (
                "enabled" if settings.local_placeholder_enabled else "disabled"
            ),
        },
    }
