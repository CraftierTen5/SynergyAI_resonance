"""Pure assignment policy evaluation; safe to use without a database."""


POLICY_LEVELS = ("RED", "YELLOW", "GREEN")


def evaluate_policy(policy_level: str) -> dict:
    if not isinstance(policy_level, str):
        raise ValueError("policy_level must be RED, YELLOW, or GREEN")
    level = policy_level.strip().upper()
    if level not in POLICY_LEVELS:
        raise ValueError("policy_level must be RED, YELLOW, or GREEN")
    rules = {
        "RED": (False, "refuse", "This assignment's policy prohibits AI assistance.", False, False),
        "YELLOW": (True, "socratic", "I can guide you with hints and questions, without writing a complete submission.", True, True),
        "GREEN": (True, "full", "AI assistance is allowed for this assignment.", False, True),
    }
    allowed, mode, explanation, disclosure, logging = rules[level]
    return {"policy_level": level, "ai_allowed": allowed, "assistance_mode": mode,
            "explanation": explanation, "disclosure_required": disclosure,
            "process_logging_required": logging}
