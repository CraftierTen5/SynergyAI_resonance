"""FastAPI application entry point for the implemented preprocessing MVP."""

from fastapi import FastAPI

from app.api.preprocess import create_preprocess_router
from app.core.config import get_settings
from app.services.ai.gemini import GeminiFlashAdapter
from app.services.ai.preprocessing import GeminiPreprocessingService


settings = get_settings()
preprocessing = GeminiPreprocessingService(GeminiFlashAdapter(settings), settings=settings)

app = FastAPI(
    title="ResonanceAI Backend",
    description="Multilingual preprocessing and deterministic policy metadata. Tutoring inference is not enabled.",
    version="0.1.0",
)
app.include_router(create_preprocess_router(preprocessing))


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
        },
    }
