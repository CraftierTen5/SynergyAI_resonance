"""Pure assignment policy evaluation; safe to use without a database."""
from enum import Enum


class PolicyLevel(str, Enum):
    """Layer 3A names and wire values used by the orchestration service."""

    RED = "NO_AI"
    YELLOW = "PROCESS_DISCLOSURE"
    GREEN = "FULL_INTEGRATION"


POLICY_LEVELS = ("RED", "YELLOW", "GREEN")


def evaluate_policy(policy_level: str) -> dict:
    if not isinstance(policy_level, str):
        raise ValueError("policy_level must be RED, YELLOW, or GREEN")
    level = policy_level.strip().upper()
    level = {
        PolicyLevel.RED.value: "RED",
        PolicyLevel.YELLOW.value: "YELLOW",
        PolicyLevel.GREEN.value: "GREEN",
    }.get(level, level)
    if level not in POLICY_LEVELS:
        raise ValueError("policy_level must be RED, YELLOW, or GREEN")
    rules = {
        "RED": (False, "refuse", "This assignment's policy prohibits AI assistance.", False, False,
                "", "blocked", PolicyLevel.RED.value),
        "YELLOW": (True, "socratic", "I can guide you with hints and questions, without writing a complete submission.", True, True,
                   "You are an academic mentor. Do not provide direct complete solutions. Guide the student using Socratic questions in their preferred language.", "guided", PolicyLevel.YELLOW.value),
        "GREEN": (True, "full", "AI assistance is allowed for this assignment.", False, False,
                  "AI assistance is allowed under this assignment policy.", "direct", PolicyLevel.GREEN.value),
    }
    allowed, mode, explanation, disclosure, logging, instruction, profile, tier = rules[level]
    wire_tier = {"RED": "red", "YELLOW": "yellow", "GREEN": "green"}[level]
    return {"policy_level": level, "ai_allowed": allowed, "assistance_mode": mode,
            "explanation": explanation, "disclosure_required": disclosure,
            "process_logging_required": logging, "log_required": True,
            "policy_audit_recorded": False, "process_log_recorded": False,
            "policy_tier": wire_tier, "policy_level_value": tier, "instruction_profile": profile,
            "system_instruction": instruction}
