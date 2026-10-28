from datetime import datetime, timezone
import hashlib
from uuid import uuid4

import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import AssignmentRecord, check_database, database_configured, get_db
from app.policy import evaluate_policy
from app.schemas import (AIOrchestrationRequest, Assignment, AssignmentCreate, AssignmentUpdate,
                         PolicyCheck, TutorRequest, TutorStreamRequest, VivaEvidenceRequest, VivaRequest)
from app.tutor import ProviderFailure, ProviderUnavailable, configured_provider

app = FastAPI(title="SamarthAI / ResonanceAI", description="Equitable, multilingual AI-readiness backend", version="0.2.0")
logger = logging.getLogger(__name__)

# Volatile data is an explicitly labeled development convenience, never presented as persistent.
_dev_assignments: dict[str, dict] = {}
_dev_logs: list[dict] = []
app.state.guidance_service = None
app.state.process_log_reader = None
app.state.viva_generator = None


def install_orchestration_services(*, guidance_service, process_log_reader=None, viva_generator=None):
    """Install orchestrate-compatible services from the backend composition layer."""
    if not callable(getattr(guidance_service, "generate_multilingual_guidance", None)):
        raise TypeError("guidance_service must implement generate_multilingual_guidance(request, provider)")
    if process_log_reader is not None and not callable(process_log_reader):
        raise TypeError("process_log_reader must be callable")
    if viva_generator is not None and not callable(getattr(viva_generator, "generate_viva_questions", None)):
        raise TypeError("viva_generator must implement generate_viva_questions(process_logs)")
    app.state.guidance_service = guidance_service
    app.state.process_log_reader = process_log_reader
    app.state.viva_generator = viva_generator


def _require_demo_mode():
    if settings.environment != "development":
        raise HTTPException(503, "Authentication and authorization are not implemented; demo routes are disabled outside development")


@app.get("/")
def root():
    return {"message": "SamarthAI / ResonanceAI backend is running", "environment": settings.environment}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/ready")
def readiness(response: Response):
    db_ready = check_database()
    storage_ready = db_ready or (not database_configured and settings.environment == "development")
    response.status_code = 200 if storage_ready else 503
    return {"status": "ready" if response.status_code == 200 else "not_ready",
            "database": "connected" if db_ready else ("not_configured" if not database_configured else "unavailable"),
            "assignment_storage": "postgresql" if db_ready else ("volatile_development" if settings.environment == "development" else "unavailable"),
            "ai_orchestration": "configured" if app.state.guidance_service is not None else "not_configured",
            "authentication": "not_implemented"}


@app.get("/policies")
def policies():
    return {"policies": [evaluate_policy(level) for level in ("RED", "YELLOW", "GREEN")]}


@app.post("/policies/check")
def policy_check(payload: PolicyCheck):
    try:
        return evaluate_policy(payload.policy_level)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _assignment_json(item: dict) -> dict:
    return {**item, "storage": "volatile_development"}


@app.post("/assignments", response_model=Assignment, status_code=201)
def create_assignment(payload: AssignmentCreate, db: Session | None = Depends(get_db)):
    _require_demo_mode()
    now = datetime.now(timezone.utc)
    item = payload.model_dump() | {"id": str(uuid4()), "created_at": now}
    if db is not None:
        row = AssignmentRecord(**item)
        db.add(row)
        db.commit()
        item["storage"] = "postgresql"
        return item
    if settings.environment != "development":
        raise HTTPException(503, "Assignment storage is unavailable; configure PostgreSQL")
    _dev_assignments[item["id"]] = item
    return _assignment_json(item)


@app.get("/assignments", response_model=list[Assignment])
def list_assignments(db: Session | None = Depends(get_db)):
    _require_demo_mode()
    if db is not None:
        return [{**row.__dict__, "storage": "postgresql"} for row in db.scalars(select(AssignmentRecord)).all()]
    return [_assignment_json(item) for item in _dev_assignments.values()]


def _get_assignment(assignment_id: str, db: Session | None):
    row = db.get(AssignmentRecord, assignment_id) if db is not None else _dev_assignments.get(assignment_id)
    if row is None:
        raise HTTPException(404, "Assignment not found")
    if db is not None:
        return {key: getattr(row, key) for key in ("id", "title", "description", "policy_level", "course", "institution_id", "creator_id", "due_date", "created_at")} | {"storage": "postgresql"}
    return _assignment_json(row)


@app.get("/assignments/{assignment_id}", response_model=Assignment)
def get_assignment(assignment_id: str, db: Session | None = Depends(get_db)):
    _require_demo_mode()
    return _get_assignment(assignment_id, db)


@app.patch("/assignments/{assignment_id}/policy", response_model=Assignment)
def update_assignment_policy(assignment_id: str, payload: AssignmentUpdate, db: Session | None = Depends(get_db)):
    _require_demo_mode()
    if db is None and settings.environment != "development":
        raise HTTPException(503, "Assignment storage is unavailable; configure PostgreSQL")
    item = _get_assignment(assignment_id, db)
    if db is not None:
        row = db.get(AssignmentRecord, assignment_id)
        row.policy_level = payload.policy_level
        db.commit()
        item["policy_level"] = payload.policy_level
    else:
        _dev_assignments[assignment_id]["policy_level"] = payload.policy_level
        item["policy_level"] = payload.policy_level
    return item


def _get_provider(provider_name: str | None = None):
    provider = configured_provider(provider_name) if provider_name else configured_provider()
    if provider is None:
        raise HTTPException(503, "Tutor provider is not configured. Set Groq or Gemini credentials and a model, or use the legacy OpenAI-compatible settings.")
    return provider


@app.post("/tutor/chat")
def tutor_chat(payload: TutorRequest, db: Session | None = Depends(get_db)):
    _require_demo_mode()
    assignment = _get_assignment(payload.assignment_id, db)
    decision = evaluate_policy(assignment["policy_level"])
    if not decision["ai_allowed"]:
        # RED refusal is generated locally and cannot reach the provider.
        return {"assignment_id": assignment["id"], "policy_level": "RED", "assistance_mode": "refuse",
                "response": "I can’t provide help with this assignment because its policy prohibits AI assistance. Please ask your instructor what support is permitted.",
                "disclosure_acknowledged": False, "process_log": "not_recorded"}
    if decision["disclosure_required"] and not payload.disclosure_acknowledged:
        raise HTTPException(400, "Please acknowledge the AI-use disclosure requirement before continuing")
    provider = _get_provider(payload.provider)
    try:
        answer = provider.generate(payload.message, decision["assistance_mode"], payload.language, assignment["title"])
    except ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ProviderFailure as exc:
        raise HTTPException(502, str(exc)) from exc
    log = {"id": str(uuid4()), "assignment_id": assignment["id"], "user_id": None,
           "institution_id": assignment.get("institution_id"), "session_id": payload.session_id,
           "created_at": datetime.now(timezone.utc), "policy_level": assignment["policy_level"],
           "assistance_mode": decision["assistance_mode"], "disclosure_acknowledged": payload.disclosure_acknowledged,
           "message_sha256": hashlib.sha256(payload.message.encode("utf-8")).hexdigest(),
           "response_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
           "storage": "volatile_development"}
    if not decision["process_logging_required"]:
        log_status = "not_required"
    elif db is None:
        _dev_logs.append(log)
        log_status = "recorded_in_memory"
    else:
        # Process-log persistence is intentionally not claimed until its migration exists.
        log_status = "not_persisted"
    return {"assignment_id": assignment["id"], "policy_level": assignment["policy_level"],
            "assistance_mode": decision["assistance_mode"], "response": answer,
            "disclosure_acknowledged": payload.disclosure_acknowledged, "process_log": log_status}


@app.post("/tutor/chat/stream")
async def tutor_chat_stream(payload: TutorStreamRequest, request: Request,
                            db: Session | None = Depends(get_db)):
    """Adapt assignment policy into the provider-neutral orchestrate contract.

    The orchestration service owns streaming, tenant context, cache, and usage
    metering. It is injected at app.state.guidance_service by the deployment
    composition layer; the API never accepts client-supplied policy claims.
    """
    _require_demo_mode()
    assignment = _get_assignment(payload.assignment_id, db)
    decision = evaluate_policy(assignment["policy_level"])
    if not decision["ai_allowed"]:
        raise HTTPException(403, detail={"code": "policy_denied", "policy_level": "RED",
                                         "reason": decision["explanation"]})
    if decision["disclosure_required"] and not payload.disclosure_acknowledged:
        raise HTTPException(400, "Please acknowledge the AI-use disclosure requirement before continuing")
    institution_id = assignment.get("institution_id")
    if not institution_id:
        raise HTTPException(503, "Assignment institution scope is required for AI orchestration")
    guidance = request.app.state.guidance_service
    if guidance is None:
        raise HTTPException(503, "Orchestrate-compatible AI streaming service is not configured")

    conversation_id = payload.session_id or str(uuid4())
    orchestration_request = AIOrchestrationRequest(
        request_id=str(uuid4()), institution_id=institution_id,
        conversation_id=conversation_id,
        messages=[{"role": "user", "content": payload.message}],
        language=payload.language,
        policy_tier=decision["policy_tier"],
        instruction_profile=decision["instruction_profile"],
        policy_version=assignment.get("policy_version", "assignment-policy-v1"),
        system_instruction=decision["system_instruction"],
        technical_terms=payload.technical_terms,
        max_output_tokens=payload.max_output_tokens, temperature=payload.temperature,
    )

    async def emit_events():
        try:
            async for chunk in guidance.generate_multilingual_guidance(orchestration_request, payload.provider):
                yield "data: " + chunk.model_dump_json() + "\n\n"
        except Exception:
            # Provider, Redis, and metering details stay in server logs only.
            logger.exception("AI orchestration request failed")
            yield "event: error\ndata: {\"detail\":\"AI request failed\"}\n\n"
        else:
            yield "data: [DONE]\n\n"
    return StreamingResponse(emit_events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/process-logs")
def process_logs(assignment_id: str | None = None):
    _require_demo_mode()
    logs = _dev_logs if assignment_id is None else [log for log in _dev_logs if log["assignment_id"] == assignment_id]
    return {"logs": logs, "storage": "volatile_development", "authentication": "not_implemented"}


@app.post("/viva/generate")
def generate_viva(payload: VivaRequest, db: Session | None = Depends(get_db)):
    _require_demo_mode()
    assignment = _get_assignment(payload.assignment_id, db)
    decision = evaluate_policy(assignment["policy_level"])
    if not decision["ai_allowed"]:
        raise HTTPException(403, "Viva generation is unavailable for RED assignments")
    provider = _get_provider()
    prompt = (f"Generate {payload.number_of_questions} oral exam questions on {payload.topic}. Difficulty: {payload.difficulty}. "
              "Mix conceptual, reasoning, application, and follow-up questions. Return a numbered list.")
    try:
        answer = provider.generate(prompt, "full", payload.language, assignment["title"])
    except (ProviderUnavailable, ProviderFailure) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"assignment_id": assignment["id"], "questions": answer, "generated_by": "configured_provider",
            "accuracy_note": "Review questions for course fit and accuracy."}


@app.post("/viva/generate/from-process-logs")
async def generate_evidence_viva(payload: VivaEvidenceRequest, request: Request,
                                 db: Session | None = Depends(get_db)):
    """Connect authorized server-side process-log retrieval to the orchestrate viva service."""
    _require_demo_mode()
    assignment = _get_assignment(payload.assignment_id, db)
    institution_id = assignment.get("institution_id")
    reader = request.app.state.process_log_reader
    generator = request.app.state.viva_generator
    if not institution_id or reader is None or generator is None:
        raise HTTPException(503, "Authorized process-log and viva services are not configured")
    try:
        # The reader must enforce faculty/admin identity and assignment scope.
        process_logs = await reader(request, assignment["id"])
    except PermissionError as exc:
        raise HTTPException(403, "Not authorized to view process logs for this assignment") from exc
    except LookupError as exc:
        raise HTTPException(404, "No process logs are available for this assignment") from exc
    except Exception as exc:
        logger.error("authorized process-log retrieval failed")
        raise HTTPException(503, "Process logs are temporarily unavailable") from exc
    if not isinstance(process_logs, list) or not process_logs:
        raise HTTPException(404, "No process logs are available for this assignment")
    if any(not isinstance(entry, dict) or entry.get("institution_id") != institution_id
           for entry in process_logs):
        raise HTTPException(403, "Process logs do not match the assignment institution")
    try:
        questions = await generator.generate_viva_questions(process_logs)
    except ValueError as exc:
        raise HTTPException(422, "Process logs could not be used to generate viva questions") from exc
    except Exception as exc:
        logger.error("evidence-based viva generation failed")
        raise HTTPException(502, "Viva question generation failed") from exc
    return {"assignment_id": assignment["id"],
            "questions": [question.model_dump() if hasattr(question, "model_dump") else question
                          for question in questions],
            "review_required": True}
