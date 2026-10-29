"""FastAPI dependencies populated by the host application's auth/composition layer."""

from uuid import uuid4

from fastapi import HTTPException, Request, status

from app.agent.orchestrator import TutorOrchestrator
from app.schemas.agent import Principal


def get_tutor_orchestrator(request: Request) -> TutorOrchestrator:
    orchestrator = getattr(request.app.state, "tutor_orchestrator", None)
    if not isinstance(orchestrator, TutorOrchestrator):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "request_id": request_id,
                "status": "unavailable",
                "response": "The tutoring service is not configured.",
            },
        )
    return orchestrator


def get_principal(request: Request) -> Principal:
    """Read only the principal established by trusted authentication middleware."""
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal
