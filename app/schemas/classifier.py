"""Stable future Qwen boundary. This module intentionally contains no model implementation."""

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


Intent = Literal[
    "CONCEPT_EXPLANATION", "BRAINSTORMING", "REQUEST_FOR_HINT", "CODE_FEEDBACK",
    "ANSWER_REQUEST", "LANGUAGE_SUPPORT", "SUMMARY_REQUEST", "POLICY_EXTRACTION", "AMBIGUOUS",
]
RecommendedAction = Literal[
    "EXPLAIN_CONCEPT", "GUIDE_PLANNING", "GIVE_HINT", "REVIEW_CODE", "CHECK_POLICY",
    "CLARIFY_REQUEST", "TRANSLATE_OR_EXPLAIN", "SUMMARIZE", "EXTRACT_POLICY", "REQUEST_HUMAN_REVIEW",
]


class NormalizedClassifierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str = Field(min_length=1, max_length=100_000)
    policy_flag: Literal["RED", "YELLOW", "GREEN", "UNKNOWN"]
    context: str = Field(default="", max_length=50_000)


class IntentActionRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    recommended_action: RecommendedAction


class IntentActionClassifier(Protocol):
    async def classify(self, request: NormalizedClassifierInput) -> IntentActionRecommendation: ...
