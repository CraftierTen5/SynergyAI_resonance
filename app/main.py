from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import AssignmentRecord, check_database, database_configured, get_db
from app.policy import evaluate_policy
from app.schemas import Assignment, AssignmentCreate, AssignmentUpdate, PolicyCheck, TutorRequest, VivaRequest
from app.tutor import ProviderFailure, ProviderUnavailable, configured_provider

app = FastAPI(title="SamarthAI / ResonanceAI", description="Equitable, multilingual AI-readiness backend", version="0.2.0")

# Volatile data is an explicitly labeled development convenience, never presented as persistent.
_dev_assignments: dict[str, dict] = {}
_dev_logs: list[dict] = []


@app.get("/")
def root():
    return {"message": "SamarthAI / ResonanceAI backend is running", "environment": settings.environment}


@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.get("/ready")
def readiness(response: Response):
    db_ready = check_database()
    response.status_code = 200 if (not database_configured or db_ready) else 503
    return {"status": "ready" if response.status_code == 200 else "not_ready",
            "database": "connected" if db_ready else ("not_configured" if not database_configured else "unavailable"),
            "assignment_storage": "postgresql" if db_ready else "volatile_development"}


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
    return _get_assignment(assignment_id, db)


@app.patch("/assignments/{assignment_id}/policy", response_model=Assignment)
def update_assignment_policy(assignment_id: str, payload: AssignmentUpdate, db: Session | None = Depends(get_db)):
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


def _get_provider():
    provider = configured_provider()
    if provider is None:
        raise HTTPException(503, "Tutor provider is not configured. Set LLM_PROVIDER, LLM_API_KEY, and LLM_MODEL.")
    return provider


@app.post("/tutor/chat")
def tutor_chat(payload: TutorRequest, db: Session | None = Depends(get_db)):
    assignment = _get_assignment(payload.assignment_id, db)
    decision = evaluate_policy(assignment["policy_level"])
    if not decision["ai_allowed"]:
        # RED refusal is generated locally and cannot reach the provider.
        return {"assignment_id": assignment["id"], "policy_level": "RED", "assistance_mode": "refuse",
                "response": "I can’t provide help with this assignment because its policy prohibits AI assistance. Please ask your instructor what support is permitted.",
                "disclosure_acknowledged": False, "process_log": "not_recorded"}
    if decision["disclosure_required"] and not payload.disclosure_acknowledged:
        raise HTTPException(400, "Please acknowledge the AI-use disclosure requirement before continuing")
    provider = _get_provider()
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
           "message": payload.message, "response": answer, "storage": "volatile_development"}
    if db is None:
        _dev_logs.append(log)
        log_status = "recorded_in_memory"
    else:
        # Process-log persistence is intentionally not claimed until its migration exists.
        log_status = "not_persisted"
    return {"assignment_id": assignment["id"], "policy_level": assignment["policy_level"],
            "assistance_mode": decision["assistance_mode"], "response": answer,
            "disclosure_acknowledged": payload.disclosure_acknowledged, "process_log": log_status}


@app.get("/process-logs")
def process_logs(assignment_id: str | None = None):
    logs = _dev_logs if assignment_id is None else [log for log in _dev_logs if log["assignment_id"] == assignment_id]
    return {"logs": logs, "storage": "volatile_development", "authentication": "not_implemented"}


@app.post("/viva/generate")
def generate_viva(payload: VivaRequest, db: Session | None = Depends(get_db)):
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
