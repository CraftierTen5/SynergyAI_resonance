"""Layer 3 request workflow coordination."""

from app.agent.contracts import AgentResult, AssignmentContext, TrustedUserContext, TutorCommand
from app.agent.ports import (
    AssignmentService,
    ConversationAuditService,
    InputProcessor,
    ModelClient,
    PolicyActionService,
    ResponseGenerator,
    ResponseGuardPort,
)


class AgentOrchestrator:
    """Coordinate injected services without owning persistence or providers."""

    def __init__(
        self,
        *,
        assignments: AssignmentService,
        policy_actions: PolicyActionService,
        input_processor: InputProcessor,
        model_client: ModelClient,
        response_generator: ResponseGenerator,
        response_guard: ResponseGuardPort,
        audit_service: ConversationAuditService,
    ) -> None:
        self.assignments = assignments
        self.policy_actions = policy_actions
        self.input_processor = input_processor
        self.model_client = model_client
        self.response_generator = response_generator
        self.response_guard = response_guard
        self.audit_service = audit_service

    async def handle(
        self,
        command: TutorCommand,
        user: TrustedUserContext,
    ) -> AgentResult:
        assignment = await self.assignments.get_authorized_assignment(
            command.assignment_id, user
        )

        # This gate must remain ahead of all optional AI/OCR/translation work.
        preflight = self.policy_actions.preflight(
            assignment, command.disclosure_acknowledged
        )
        if not preflight.allowed:
            result = AgentResult(
                request_id=command.request_id,
                status=(
                    "needs_clarification"
                    if preflight.reason_code == "DISCLOSURE_REQUIRED"
                    else "refused"
                ),
                message=(
                    preflight.user_message
                    or "This request cannot be processed under the assignment policy."
                ),
                response_language=command.language_preference,
                policy_level=assignment.policy_level,
            )
            await self._audit(
                user,
                assignment,
                result,
                {"stage": "preflight", "reason_code": preflight.reason_code},
            )
            return result

        normalized = await self.input_processor.normalize(
            message=command.message,
            language_preference=command.language_preference,
            document_ids=command.document_ids,
            user=user,
            assignment=assignment,
        )
        recommendation = await self.model_client.classify(normalized)
        decision = self.policy_actions.select_action(
            assignment,
            recommendation.intent,
            recommendation.recommended_action,
        )

        output_guard_reason: str | None = None
        if decision.needs_clarification:
            result = AgentResult(
                request_id=command.request_id,
                status="needs_clarification",
                message=(
                    decision.user_message
                    or "Could you clarify what kind of help you need?"
                ),
                response_language=normalized.response_language,
                intent=recommendation.intent,
                selected_action=decision.selected_action,
                policy_level=assignment.policy_level,
                model_version=recommendation.model_version,
            )
        elif not decision.allowed or decision.selected_action == "REFUSE":
            result = AgentResult(
                request_id=command.request_id,
                status="refused",
                message=(
                    decision.user_message
                    or "This request is not permitted under the assignment policy."
                ),
                response_language=normalized.response_language,
                intent=recommendation.intent,
                policy_level=assignment.policy_level,
                model_version=recommendation.model_version,
            )
        else:
            draft = await self.response_generator.generate(
                normalized=normalized,
                assignment=assignment,
                selected_action=decision.selected_action,
                mode=decision.mode,
            )
            output_check = self.response_guard.validate(
                policy_level=assignment.policy_level,
                mode=decision.mode,
                response_text=draft,
            )
            output_guard_reason = output_check.reason_code
            if output_check.allowed:
                result = AgentResult(
                    request_id=command.request_id,
                    status="completed",
                    message=draft,
                    response_language=normalized.response_language,
                    intent=recommendation.intent,
                    selected_action=decision.selected_action,
                    policy_level=assignment.policy_level,
                    model_version=recommendation.model_version,
                )
            else:
                result = AgentResult(
                    request_id=command.request_id,
                    status="failed",
                    message=(
                        output_check.replacement_message
                        or "The generated response did not pass policy validation. Please try again."
                    ),
                    response_language=normalized.response_language,
                    intent=recommendation.intent,
                    selected_action=decision.selected_action,
                    policy_level=assignment.policy_level,
                    model_version=recommendation.model_version,
                )

        metadata: dict[str, object] = {
            "stage": "completed",
            "normalization_method": normalized.normalization_method,
            "policy_reason_code": decision.reason_code,
        }
        if output_guard_reason is not None:
            metadata["output_guard_reason_code"] = output_guard_reason
        await self._audit(user, assignment, result, metadata)
        return result

    async def _audit(
        self,
        user: TrustedUserContext,
        assignment: AssignmentContext,
        result: AgentResult,
        metadata: dict[str, object],
    ) -> None:
        # Avoid raw student text and generated response content in audit metadata.
        await self.audit_service.record_run(
            user=user,
            assignment=assignment,
            result=result,
            metadata=metadata,
        )
