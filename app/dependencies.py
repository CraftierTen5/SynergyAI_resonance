"""Application composition for the policy-aware agent workflow.

Concrete database, OCR, model-harness, and provider adapters are supplied by
the application startup layer through these ports.
"""

from app.agent.orchestrator import AgentOrchestrator
from app.agent.ports import (
    AssignmentService,
    ConversationAuditService,
    InputProcessor,
    ModelClient,
    PolicyActionService,
    ResponseGenerator,
    ResponseGuardPort,
)


def build_agent_orchestrator(
    *,
    assignments: AssignmentService,
    policy_actions: PolicyActionService,
    input_processor: InputProcessor,
    model_client: ModelClient,
    response_generator: ResponseGenerator,
    response_guard: ResponseGuardPort,
    audit_service: ConversationAuditService,
) -> AgentOrchestrator:
    """Compose one orchestrator from application-provided service ports."""
    return AgentOrchestrator(
        assignments=assignments,
        policy_actions=policy_actions,
        input_processor=input_processor,
        model_client=model_client,
        response_generator=response_generator,
        response_guard=response_guard,
        audit_service=audit_service,
    )
