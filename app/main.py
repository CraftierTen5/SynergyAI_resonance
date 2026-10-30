"""FastAPI application entry point for the implemented preprocessing MVP."""

from __future__ import annotations

import hashlib
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from uuid import UUID

import asyncpg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

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
from app.db.access import TenantScopedStore
from app.db.postgres import PostgresStore
from app.dependencies import build_agent_orchestrator
from app.services.ai.gemini import GeminiFlashAdapter
from app.services.ai.preprocessing import GeminiPreprocessingService
from app.services.auth import hash_password, issue_token, read_token, verify_password
from app.services.policy.output_guard import ResponseGuard
from app.services.policy.service import PolicyActionService


settings = get_settings()
preprocessing = GeminiPreprocessingService(GeminiFlashAdapter(settings), settings=settings)

frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
frontend_index = frontend_dir / "index.html"


@asynccontextmanager
async def lifespan(application: FastAPI):
    store = PostgresStore(settings)
    if settings.postgres_dsn is not None:
        await store.connect()
    application.state.postgres = store
    try:
        yield
    finally:
        await store.close()


app = FastAPI(
    title="ResonanceAI Backend",
    description=(
        "Multilingual preprocessing with an optional deterministic local tutoring pipeline."
    ),
    version="0.1.0",
    lifespan=lifespan,
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
        policy_lookup=None,
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
            "assignment_store": "postgresql" if settings.postgres_dsn else "not_configured",
            "authentication": "configured" if settings.auth_signing_key else "not_configured",
            "qwen": "not_implemented",
            "local_placeholder": (
                "enabled" if settings.local_placeholder_enabled else "disabled"
            ),
        },
    }


class RegisterRequest(BaseModel):
    institution_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    institution_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    department: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class LoginRequest(BaseModel):
    institution_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class AssignmentCreateRequest(BaseModel):
    course_code: str = Field(min_length=1, max_length=64)
    course_title: str = Field(min_length=1, max_length=200)
    academic_term: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=300)
    policy_level: str = Field(pattern=r"^(RED|YELLOW|GREEN)$")
    due_at: datetime | None = None


def _database(request: Request) -> PostgresStore:
    store = getattr(request.app.state, "postgres", None)
    if store is None or settings.postgres_dsn is None:
        raise HTTPException(status_code=503, detail="PostgreSQL is not configured")
    return store


def _auth_key() -> str:
    if settings.auth_signing_key is None:
        raise HTTPException(status_code=503, detail="AUTH_SIGNING_KEY is not configured")
    return settings.auth_signing_key.get_secret_value()


def _session(authorization: str | None) -> dict[str, str]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in is required")
    try:
        return read_token(authorization[7:], _auth_key())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Session is invalid or expired") from exc


def _store_for(request: Request, session: dict[str, str]) -> TenantScopedStore:
    return TenantScopedStore(_database(request), session["institution_id"])


@app.post("/api/auth/register", tags=["authentication"], status_code=201)
async def register(req: RegisterRequest, request: Request) -> dict[str, object]:
    signing_key = _auth_key()
    store = TenantScopedStore(_database(request), req.institution_id)
    async with store.scoped_connection() as connection:
        await connection.execute(
            "INSERT INTO core.institutions (institution_id, name, data_region) VALUES ($1, $2, $3) "
            "ON CONFLICT (institution_id) DO NOTHING",
            req.institution_id, req.institution_name.strip(), settings.data_region,
        )
        institution_name = await connection.fetchval(
            "SELECT name FROM core.institutions WHERE institution_id = $1", req.institution_id
        )
        if institution_name != req.institution_name.strip():
            raise HTTPException(status_code=409, detail="Institution ID is already registered to another name")
        try:
            user_id = await connection.fetchval(
                "INSERT INTO core.users (institution_id, email, full_name, department, password_hash) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING user_id",
                req.institution_id, req.email, req.full_name.strip(), req.department.strip(), hash_password(req.password),
            )
        except asyncpg.UniqueViolationError as exc:
            raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    token = issue_token(user_id=str(user_id), institution_id=req.institution_id,
                        signing_key=signing_key, ttl_seconds=settings.session_ttl_seconds)
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.session_ttl_seconds,
            "user": {"user_id": str(user_id), "email": req.email, "full_name": req.full_name,
                     "department": req.department, "institution_id": req.institution_id,
                     "institution_name": req.institution_name.strip()}}


@app.post("/api/auth/login", tags=["authentication"])
async def login(req: LoginRequest, request: Request) -> dict[str, object]:
    signing_key = _auth_key()
    store = TenantScopedStore(_database(request), req.institution_id)
    row = await store.fetchrow(
        "SELECT user_id, email, full_name, department, password_hash FROM core.users WHERE email = $1",
        req.email,
    )
    if row is None or not verify_password(req.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Institution ID, email, or password is incorrect")
    token = issue_token(user_id=str(row["user_id"]), institution_id=req.institution_id,
                        signing_key=signing_key, ttl_seconds=settings.session_ttl_seconds)
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.session_ttl_seconds,
            "user": {"user_id": str(row["user_id"]), "email": row["email"],
                     "full_name": row["full_name"], "department": row["department"],
                     "institution_id": req.institution_id}}


@app.get("/api/auth/me", tags=["authentication"])
async def current_user(request: Request, authorization: str | None = Header(default=None)) -> dict[str, object]:
    session = _session(authorization)
    row = await _store_for(request, session).fetchrow(
        "SELECT user_id, email, full_name, department FROM core.users WHERE user_id = $1",
        UUID(session["user_id"]),
    )
    if row is None:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    institution = await _store_for(request, session).fetchrow(
        "SELECT institution_id, name FROM core.institutions WHERE institution_id = $1",
        session["institution_id"],
    )
    return {"user_id": str(row["user_id"]), "email": row["email"], "full_name": row["full_name"],
            "department": row["department"], "institution_id": institution["institution_id"],
            "institution_name": institution["name"]}


@app.get("/api/assignments", tags=["assignments"])
async def list_assignments(request: Request, authorization: str | None = Header(default=None)) -> list[dict[str, object]]:
    session = _session(authorization)
    rows = await _store_for(request, session).fetch(
        """SELECT a.assignment_id, c.code AS course_code, c.title AS course_title,
                  a.title, a.policy_tier AS policy_level, a.due_at
           FROM core.assignments a JOIN core.courses c
             ON c.institution_id = a.institution_id AND c.course_id = a.course_id
           WHERE a.archived_at IS NULL ORDER BY a.created_at DESC"""
    )
    return [{"assignment_id": str(row["assignment_id"]), "course_code": row["course_code"],
             "course_title": row["course_title"], "title": row["title"],
             "policy_level": row["policy_level"], "policy_name": row["policy_level"],
             "description": "", "due_at": row["due_at"].isoformat() if row["due_at"] else None}
            for row in rows]


@app.post("/api/assignments", tags=["assignments"], status_code=201)
async def create_assignment(req: AssignmentCreateRequest, request: Request,
                            authorization: str | None = Header(default=None)) -> dict[str, object]:
    session = _session(authorization)
    tenant = _store_for(request, session)
    async with tenant.scoped_connection() as connection:
        course_id = await connection.fetchval(
            "INSERT INTO core.courses (institution_id, code, title, academic_term) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (institution_id, code, academic_term) DO UPDATE SET title = EXCLUDED.title RETURNING course_id",
            tenant.institution_id, req.course_code.strip(), req.course_title.strip(), req.academic_term.strip(),
        )
        assignment_id = await connection.fetchval(
            "INSERT INTO core.assignments (institution_id, course_id, title, policy_tier, due_at) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING assignment_id",
            tenant.institution_id, course_id, req.title.strip(), req.policy_level, req.due_at,
        )
        await connection.execute(
            "INSERT INTO core.policy_versions (institution_id, assignment_id, version_label, policy_tier, instruction_profile) "
            "VALUES ($1, $2, 'v1', $3, 'guided')",
            tenant.institution_id, assignment_id, req.policy_level,
        )
    return {"assignment_id": str(assignment_id), "course_code": req.course_code,
            "course_title": req.course_title, "title": req.title, "policy_level": req.policy_level,
            "policy_name": req.policy_level, "description": ""}


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
        "status": "development_placeholder",
        "receipt_id": f"rcpt_{digest[:12]}",
        "verification_hash": digest,
        "short_hash": f"{digest[:6]}...{digest[-6:]}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "institution": "Not configured",
        "department": "Not configured",
        "assignment_id": req.assignment_id,
        "audit_status": "DEVELOPMENT_PLACEHOLDER",
        "guarantee": "Development response only; no durable audit record was written.",
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
