"""Deterministic structural and policy checks for generated responses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OutputCheck:
    allowed: bool
    reason_code: str
    replacement_message: str | None = None


class ResponseGuard:
    """Apply deterministic output checks; this is not semantic moderation."""

    def __init__(self, max_socratic_chars: int = 12_000) -> None:
        if max_socratic_chars < 1:
            raise ValueError("max_socratic_chars must be positive")
        self.max_socratic_chars = max_socratic_chars

    def validate(
        self,
        *,
        policy_level: str,
        mode: str,
        response_text: str,
    ) -> OutputCheck:
        if not isinstance(response_text, str) or not response_text.strip():
            return OutputCheck(False, "EMPTY_RESPONSE")

        canonical_level = policy_level.strip().upper() if isinstance(policy_level, str) else ""
        canonical_mode = mode.strip().upper() if isinstance(mode, str) else ""

        if canonical_level not in {"RED", "YELLOW", "GREEN"}:
            return OutputCheck(False, "POLICY_INVALID")

        if canonical_level == "RED" or canonical_mode == "NO_AI":
            return OutputCheck(False, "RED_OUTPUT_FORBIDDEN")

        if canonical_mode not in {"SOCRATIC", "FULL_ASSISTANCE"}:
            return OutputCheck(False, "MODE_INVALID")

        if canonical_level == "YELLOW" and canonical_mode != "SOCRATIC":
            return OutputCheck(False, "POLICY_MODE_MISMATCH")

        if canonical_mode == "SOCRATIC" and len(response_text) > self.max_socratic_chars:
            return OutputCheck(False, "YELLOW_OUTPUT_TOO_LONG")

        return OutputCheck(True, "OUTPUT_ACCEPTED")
