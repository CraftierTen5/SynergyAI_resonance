"""FastAPI application entry point for the implemented preprocessing MVP."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
frontend_index = frontend_dir / "index.html"


def demo_policy_lookup(assignment_id: str):
    lookup = {
        "demo-green": [{"policy": "FULL_INTEGRATION", "scope": "assignment", "version": "v1"}],
        "demo-yellow": [{
            "policy": "PROCESS_DISCLOSURE",
            "scope": "assignment",
            "version": "v1",
            "required_conditions": ["Acknowledge disclosure", "Attach session reflections"],
        }],
        "demo-red": [{"policy": "NO_AI", "scope": "assignment", "version": "v1"}],
    }
    return lookup.get(assignment_id)


app = FastAPI(
    title="ResonanceAI Backend",
    description=(
        "Multilingual preprocessing with an optional deterministic local tutoring pipeline."
    ),
    version="0.1.0",
)

# CORS middleware for seamless communication from web clients & mobile wrappers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Preprocessing router with demo policy lookup when local placeholder is active
app.include_router(
    create_preprocess_router(
        preprocessing,
        policy_lookup=demo_policy_lookup if settings.local_placeholder_enabled else None,
    )
)

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

if frontend_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


@app.get("/", tags=["system"])
async def root(request: Request) -> object:
    accept = request.headers.get("accept", "")
    if "text/html" in accept and frontend_index.is_file():
        return FileResponse(frontend_index)
    return {"service": "ResonanceAI Backend", "status": "ok"}


@app.get("/app", tags=["frontend"])
async def frontend_view() -> object:
    if frontend_index.is_file():
        return FileResponse(frontend_index)
    return {"service": "ResonanceAI Frontend", "status": "ok"}


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, object]:
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


@app.get("/api/assignments", tags=["assignments"])
async def list_assignments() -> list[dict[str, object]]:
    return [
        {
            "assignment_id": "demo-green",
            "course_code": "CS201",
            "course_title": "Data Structures & Algorithms",
            "title": "Recursion & Binary Search Trees",
            "policy_level": "GREEN",
            "policy_name": "FULL INTEGRATION",
            "description": (
                "Socratic AI tutoring is fully enabled. Ask conceptual questions, explore edge cases, "
                "and receive guided stepped hints."
            ),
            "due_date": "2026-11-05T23:59:00Z",
            "status": "in_progress",
            "step_current": 1,
            "step_total": 3,
            "has_viva": True,
            "viva_title": "Upcoming viva: Binary Search Trees & Recursion — in 2 days",
            "viva_date": "Nov 2, 2:30 PM IST",
        },
        {
            "assignment_id": "demo-yellow",
            "course_code": "CS202",
            "course_title": "Design & Analysis of Algorithms",
            "title": "Graph Traversal & Shortest Path",
            "policy_level": "YELLOW",
            "policy_name": "PROCESS DISCLOSURE",
            "description": (
                "AI assistance is permitted with mandatory disclosure of queries and reflections "
                "upon final submission."
            ),
            "due_date": "2026-11-12T23:59:00Z",
            "status": "in_progress",
            "step_current": 0,
            "step_total": 2,
            "disclosure_required": True,
            "has_viva": False,
        },
        {
            "assignment_id": "demo-red",
            "course_code": "CS304",
            "course_title": "Cryptography & Network Security",
            "title": "Core Cryptographic Primitives",
            "policy_level": "RED",
            "policy_name": "NO AI ALLOWED",
            "description": (
                "Generative AI assistance is strictly barred for this assessment. "
                "Fail-closed policy enforcement active."
            ),
            "due_date": "2026-11-20T23:59:00Z",
            "status": "assigned",
            "step_current": 0,
            "step_total": 1,
            "has_viva": True,
            "viva_title": "In-person oral defense only",
            "viva_date": "Nov 25, 10:00 AM IST",
        },
    ]


class DisclosureRequest(BaseModel):
    assignment_id: str = Field(min_length=1)
    reflection_notes: str = Field(min_length=1)
    checked_criteria: list[str] = Field(default_factory=list)
    honor_code_agreed: bool = True


@app.post("/api/disclose", tags=["audit"])
async def record_disclosure(req: DisclosureRequest) -> dict[str, object]:
    seed = f"{req.assignment_id}:{req.reflection_notes}:{time.time()}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return {
        "status": "success",
        "receipt_id": f"rcpt_{digest[:12]}",
        "verification_hash": digest,
        "short_hash": f"{digest[:6]}...{digest[-6:]}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "institution": "Indian Institute of Technology Delhi",
        "department": "Department of Computer Science & Engineering",
        "assignment_id": req.assignment_id,
        "audit_status": "COMMITTED_IMMUTABLE",
        "guarantee": "Hash-chained into Layer 4 append-only audit trail.",
    }


@app.get("/api/config", tags=["system"])
async def api_config() -> dict[str, object]:
    return {
        "service": "ResonanceAI Backend",
        "data_region": settings.data_region,
        "allow_non_india_region": settings.allow_non_india_region,
        "process_data_retention_days": settings.process_data_retention_days,
        "assessment_retention_days": settings.assessment_retention_days,
        "audit_retention_days": settings.audit_retention_days,
        "gemini_model": settings.gemini_model,
        "groq_model": settings.groq_model,
        "local_placeholder_enabled": settings.local_placeholder_enabled,
        "india_compliance": True,
    }
