"""Pure, deterministic policy preflight rules."""

from app.policy.schemas import PreflightResult, PreflightStatus
from app.schemas.agent import AssistanceMode, PolicyLevel


class PolicyEngine:
    """Evaluate assignment policy without I/O or mutable state."""

    def preflight(
        self,
        *,
        policy_level: PolicyLevel,
        disclosure_acknowledged: bool,
    ) -> PreflightResult:
        if not isinstance(policy_level, PolicyLevel):
            raise TypeError("policy_level must be a PolicyLevel")
        if type(disclosure_acknowledged) is not bool:
            raise TypeError("disclosure_acknowledged must be a bool")

        if policy_level is PolicyLevel.RED:
            return PreflightResult(
                allowed=False,
                status=PreflightStatus.REFUSED,
                message=(
                    "AI assistance is not permitted for this assignment. "
                    "Please complete it using resources allowed by your instructor."
                ),
                assistance_mode=AssistanceMode.REFUSAL,
            )

        if policy_level is PolicyLevel.YELLOW:
            if not disclosure_acknowledged:
                return PreflightResult(
                    allowed=False,
                    status=PreflightStatus.DISCLOSURE_REQUIRED,
                    message=(
                        "Please acknowledge the AI-use disclosure before "
                        "requesting guided assistance."
                    ),
                    assistance_mode=AssistanceMode.SOCRATIC_PROMPT,
                )

            return PreflightResult(
                allowed=True,
                status=PreflightStatus.ALLOWED,
                message="Acknowledgment received. Guided assistance may proceed.",
                assistance_mode=AssistanceMode.SOCRATIC_PROMPT,
            )

        return PreflightResult(
            allowed=True,
            status=PreflightStatus.ALLOWED,
            message="Request may proceed with direct assistance.",
            assistance_mode=AssistanceMode.DIRECT_ANSWER,
        )
