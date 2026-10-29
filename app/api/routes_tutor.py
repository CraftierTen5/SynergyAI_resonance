"""HTTP route for policy-aware tutoring requests."""

import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.agent.orchestrator import TutorOrchestrator
from app.api.dependencies import get_principal, get_tutor_orchestrator
from app.exceptions import (
    AssignmentNotFound,
    DocumentAccessDenied,
    ModelUnavailableError,
    PolicyChangedError,
    ProviderUnavailableError,
)
from app.schemas.agent import Principal, TutorChatRequest, TutorChatResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tutor", tags=["tutor"])


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    if supplied and len(supplied) <= 128:
        return supplied
    return str(uuid4())


@router.post("/chat", response_model=TutorChatResponse)
async def tutor_chat(
    body: TutorChatRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    orchestrator: TutorOrchestrator = Depends(get_tutor_orchestrator),
) -> TutorChatResponse:
    request_id = _request_id(request)
    try:
        return await orchestrator.handle(
            request=body,
            principal=principal,
            request_id=request_id,
        )
    except AssignmentNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "request_id": request_id,
                "status": "not_found",
                "response": "The assignment was not found.",
            },
        ) from exc
    except DocumentAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "request_id": request_id,
                "status": "forbidden",
                "response": "You cannot access one or more referenced documents.",
            },
        ) from exc
    except PolicyChangedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "request_id": request_id,
                "status": "policy_changed",
                "response": "The assignment policy changed. Please submit the request again.",
            },
        ) from exc
    except (ModelUnavailableError, ProviderUnavailableError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "request_id": request_id,
                "status": "unavailable",
                "response": "The tutoring service is temporarily unavailable. Please try again.",
            },
        ) from exc
    except Exception as exc:
        logger.exception(
            "Unhandled tutor route error",
            extra={"request_id": request_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "request_id": request_id,
                "status": "error",
                "response": "An unexpected error occurred. Please try again.",
            },
        ) from exc
