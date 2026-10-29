"""Request-level workflow coordinator for policy-aware tutoring."""

import logging
from time import monotonic

from app.agent.protocols import (
    AgentRunRepository,
    AssignmentService,
    InputProcessingService,
    ModelClient,
    PolicyActionService,
)
from app.exceptions import (
    AssignmentNotFound,
    DocumentAccessDenied,
    ModelUnavailableError,
    PolicyChangedError,
    ProviderUnavailableError,
)
from app.schemas.agent import (
    AssignmentSnapshot,
    AssistanceMode,
    Principal,
    TutorChatRequest,
    TutorChatResponse,
)

logger = logging.getLogger(__name__)


class TutorOrchestrator:
    """Coordinate assignment authorization, input prep, inference, and execution."""

    def __init__(
        self,
        *,
        assignment_service: AssignmentService,
        input_processing_service: InputProcessingService,
        model_client: ModelClient,
        policy_action_service: PolicyActionService,
        agent_run_repository: AgentRunRepository,
    ) -> None:
        self.assignment_service = assignment_service
        self.input_processing_service = input_processing_service
        self.model_client = model_client
        self.policy_action_service = policy_action_service
        self.agent_run_repository = agent_run_repository

    async def handle(
        self,
        request: TutorChatRequest,
        principal: Principal,
        request_id: str,
    ) -> TutorChatResponse:
        started_at = monotonic()
        assignment: AssignmentSnapshot | None = None
        model_version: str | None = None

        try:
            assignment = await self.assignment_service.get_authorized_snapshot(
                request.assignment_id, principal
            )

            gate = self.policy_action_service.preflight(
                policy_level=assignment.policy_level,
                disclosure_acknowledged=request.disclosure_acknowledged,
            )
            if not gate.allowed:
                await self._record(
                    request_id=request_id,
                    assignment=assignment,
                    status=gate.status.value,
                    model_version=None,
                    action=None,
                    assistance_mode=gate.assistance_mode,
                    provider=None,
                    error_code=gate.status.value,
                    latency_ms=self._latency_ms(started_at),
                )
                return TutorChatResponse(
                    request_id=request_id,
                    status=gate.status.value.lower(),
                    assignment_id=assignment.id,
                    policy_level=assignment.policy_level,
                    assistance_mode=gate.assistance_mode,
                    response=gate.message,
                )

            prepared = await self.input_processing_service.prepare(
                request=request,
                assignment=assignment,
                principal=principal,
            )

            prediction = await self.model_client.classify(
                prepared=prepared,
                policy_level=assignment.policy_level,
            )
            model_version = prediction.model_version

            current = await self.assignment_service.get_authorized_snapshot(
                assignment.id, principal
            )
            if (
                current.policy_version != assignment.policy_version
                or current.policy_level != assignment.policy_level
            ):
                await self._record(
                    request_id=request_id,
                    assignment=current,
                    status="POLICY_CHANGED",
                    model_version=model_version,
                    action=None,
                    assistance_mode=None,
                    provider=None,
                    error_code="POLICY_CHANGED",
                    latency_ms=self._latency_ms(started_at),
                )
                return self._policy_changed_response(
                    request_id=request_id,
                    assignment=current,
                    model_version=model_version,
                )

            result = await self.policy_action_service.execute(
                assignment=current,
                prepared=prepared,
                prediction=prediction,
                disclosure_acknowledged=request.disclosure_acknowledged,
                principal=principal,
                request_id=request_id,
            )

            await self._record(
                request_id=request_id,
                assignment=current,
                status=result.status,
                model_version=model_version,
                action=result.action,
                assistance_mode=result.assistance_mode,
                provider=result.provider,
                error_code=None,
                latency_ms=self._latency_ms(started_at),
            )
            return TutorChatResponse(
                request_id=request_id,
                status=result.status.lower(),
                assignment_id=current.id,
                policy_level=current.policy_level,
                assistance_mode=result.assistance_mode,
                intent=prediction.intent,
                action=result.action,
                response=result.response,
                model_version=model_version,
                provider=result.provider,
            )

        except AssignmentNotFound as exc:
            logger.warning(
                "Assignment unavailable for tutoring request",
                extra={"request_id": request_id},
            )
            await self._record(
                request_id=request_id,
                assignment=assignment,
                assignment_id=request.assignment_id,
                status="NOT_FOUND",
                model_version=model_version,
                action=None,
                assistance_mode=None,
                provider=None,
                error_code=type(exc).__name__,
                latency_ms=self._latency_ms(started_at),
            )
            raise

        except DocumentAccessDenied as exc:
            logger.warning(
                "Document access denied during tutoring request",
                extra={"request_id": request_id},
            )
            await self._record(
                request_id=request_id,
                assignment=assignment,
                assignment_id=request.assignment_id,
                status="FORBIDDEN",
                model_version=model_version,
                action=None,
                assistance_mode=None,
                provider=None,
                error_code=type(exc).__name__,
                latency_ms=self._latency_ms(started_at),
            )
            raise

        except PolicyChangedError as exc:
            logger.warning(
                "Assignment policy changed during tutoring request",
                extra={"request_id": request_id},
            )
            if assignment is None:
                await self._record(
                    request_id=request_id,
                    assignment_id=request.assignment_id,
                    status="POLICY_CHANGED",
                    model_version=model_version,
                    error_code=type(exc).__name__,
                    latency_ms=self._latency_ms(started_at),
                )
                raise

            try:
                current = await self.assignment_service.get_authorized_snapshot(
                    assignment.id, principal
                )
            except Exception:
                logger.exception(
                    "Could not reload assignment after policy change",
                    extra={"request_id": request_id},
                )
                await self._record(
                    request_id=request_id,
                    assignment=assignment,
                    status="POLICY_CHANGED",
                    model_version=model_version,
                    action=None,
                    assistance_mode=None,
                    provider=None,
                    error_code=type(exc).__name__,
                    latency_ms=self._latency_ms(started_at),
                )
                raise

            await self._record(
                request_id=request_id,
                assignment=current,
                status="POLICY_CHANGED",
                model_version=model_version,
                action=None,
                assistance_mode=None,
                provider=None,
                error_code="POLICY_CHANGED",
                latency_ms=self._latency_ms(started_at),
            )
            return self._policy_changed_response(
                request_id=request_id,
                assignment=current,
                model_version=model_version,
            )

        except (ModelUnavailableError, ProviderUnavailableError) as exc:
            logger.exception(
                "AI dependency unavailable during tutoring request",
                extra={"request_id": request_id},
            )
            await self._record(
                request_id=request_id,
                assignment=assignment,
                assignment_id=request.assignment_id,
                status="UNAVAILABLE",
                model_version=model_version,
                action=None,
                assistance_mode=None,
                provider=None,
                error_code=type(exc).__name__,
                latency_ms=self._latency_ms(started_at),
            )
            if assignment is None:
                raise
            return self._unavailable_response(
                request_id=request_id,
                assignment=assignment,
                model_version=model_version,
            )

        except Exception as exc:
            logger.exception(
                "Unexpected tutoring orchestration failure",
                extra={"request_id": request_id},
            )
            await self._record(
                request_id=request_id,
                assignment=assignment,
                assignment_id=request.assignment_id,
                status="UNAVAILABLE",
                model_version=model_version,
                action=None,
                assistance_mode=None,
                provider=None,
                error_code=type(exc).__name__,
                latency_ms=self._latency_ms(started_at),
            )
            if assignment is None:
                raise
            return self._unavailable_response(
                request_id=request_id,
                assignment=assignment,
                model_version=model_version,
            )

    async def _record(
        self,
        *,
        request_id: str,
        status: str,
        assignment: AssignmentSnapshot | None = None,
        assignment_id: str | None = None,
        model_version: str | None = None,
        action: object | None = None,
        assistance_mode: AssistanceMode | None = None,
        provider: str | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        fields: dict[str, object] = {
            "request_id": request_id,
            "status": status,
            "model_version": model_version,
            "action": action,
            "assistance_mode": assistance_mode,
            "provider": provider,
            "error_code": error_code,
            "latency_ms": latency_ms,
        }
        if assignment is not None:
            fields["assignment"] = assignment
            fields["assignment_id"] = assignment.id
            fields["policy_version"] = assignment.policy_version
        elif assignment_id is not None:
            fields["assignment_id"] = assignment_id

        try:
            await self.agent_run_repository.record(**fields)
        except Exception:
            logger.exception(
                "Could not persist tutoring run metadata",
                extra={"request_id": request_id},
            )

    @staticmethod
    def _latency_ms(started_at: float) -> int:
        return max(0, round((monotonic() - started_at) * 1000))

    @staticmethod
    def _policy_changed_response(
        *,
        request_id: str,
        assignment: AssignmentSnapshot,
        model_version: str | None,
    ) -> TutorChatResponse:
        return TutorChatResponse(
            request_id=request_id,
            status="policy_changed",
            assignment_id=assignment.id,
            policy_level=assignment.policy_level,
            response=(
                "This assignment's AI-use policy changed while your request was "
                "processing. Please submit the request again."
            ),
            model_version=model_version,
        )

    @staticmethod
    def _unavailable_response(
        *,
        request_id: str,
        assignment: AssignmentSnapshot,
        model_version: str | None,
    ) -> TutorChatResponse:
        return TutorChatResponse(
            request_id=request_id,
            status="unavailable",
            assignment_id=assignment.id,
            policy_level=assignment.policy_level,
            response="The tutoring service is temporarily unavailable. Please try again.",
            model_version=model_version,
        )
