"""Evidence-grounded viva question generation for faculty review."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.ai import AITutorRequest, TutorMessage
from app.services.ai.base import BaseAIProvider
from app.services.assessment.anonymization import ProcessLogAnonymizer

logger = logging.getLogger(__name__)


class VivaQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=12, max_length=300)
    focus_area: Literal["design_choice", "edge_case", "conceptual_gap"]
    evidence_step_numbers: list[int] = Field(min_length=1, max_length=3)


class VivaGenerationError(RuntimeError):
    pass


class VivaQuestionGenerator:
    """Read anonymized workflow steps and generate three faculty-editable prompts."""

    def __init__(
        self,
        provider: BaseAIProvider,
        anonymizer: ProcessLogAnonymizer,
        *,
        max_steps: int = 40,
        max_context_characters: int = 24_000,
    ) -> None:
        if max_steps < 3 or max_context_characters < 1_000:
            raise ValueError("viva context limits are too small")
        self.provider = provider
        self.anonymizer = anonymizer
        self.max_steps = max_steps
        self.max_context_characters = max_context_characters

    async def generate_viva_questions(self, process_logs: list) -> list[VivaQuestion]:
        """Generate exactly three JSON-structured questions grounded in the log.

        Every log entry is anonymized again before it is sent to the provider.
        The model receives concise step summaries, not device metadata or raw
        response transcripts beyond the fields needed to understand the trail.
        """
        steps = self._parse_process_logs(process_logs)
        if not steps:
            raise VivaGenerationError("no usable process-log steps were provided")

        workflow = json.dumps(steps, ensure_ascii=False, separators=(",", ":"))
        workflow = workflow[: self.max_context_characters]
        system_instruction = (
            "You are helping faculty verify a student's understanding through an oral defense. "
            "Analyze only the supplied development trail. Identify one meaningful design choice, "
            "one edge case the student should reason through, and one conceptual gap or uncertainty "
            "visible in the trail. Ask concise, open-ended questions that invite explanation rather "
            "than accusation. Do not grade, infer misconduct, or invent facts. Return only a valid JSON "
            "array containing exactly three objects with keys: question, focus_area, "
            "evidence_step_numbers. Use each focus_area exactly once from design_choice, edge_case, "
            "conceptual_gap. Cite one or more existing step_number values in each object."
        )
        request = AITutorRequest(
            request_id=f"viva-{uuid4().hex}",
            messages=[
                TutorMessage(role="system", content=system_instruction),
                TutorMessage(
                    role="user",
                    content=(
                        "Generate the three viva questions from this anonymized, numbered workflow. "
                        "Keep each question understandable when read aloud.\n\n" + workflow
                    ),
                ),
            ],
            language="en",
            policy_tier="green",
            instruction_profile="direct",
            policy_version="viva-generation",
            system_instruction=system_instruction,
            max_output_tokens=900,
            temperature=0.2,
        )
        response = await self.provider.generate_completion(request)
        questions = self._parse_response(response.content)
        valid_steps = {step["step_number"] for step in steps}
        for question in questions:
            if not set(question.evidence_step_numbers).issubset(valid_steps):
                raise VivaGenerationError("model cited a workflow step that does not exist")
        if {item.focus_area for item in questions} != {
            "design_choice",
            "edge_case",
            "conceptual_gap",
        }:
            raise VivaGenerationError("model output must cover each required focus area once")
        return questions

    def _parse_process_logs(self, process_logs: list) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        for raw in process_logs:
            if len(steps) >= self.max_steps:
                break
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    continue
            if not isinstance(raw, Mapping):
                continue
            try:
                anonymized = self.anonymizer.anonymize(raw)
            except (TypeError, ValueError):
                # Logs without institution scope cannot be safely pseudonymized.
                logger.warning("skipping process log without valid institution scope")
                continue
            parsed = self._step_summary(anonymized, len(steps) + 1)
            if parsed:
                steps.append(parsed)

        # Keep a compact sequential representation for stable model citations.
        return steps

    @staticmethod
    def _step_summary(event: Mapping[str, Any], step_number: int) -> dict[str, Any] | None:
        prompt = VivaQuestionGenerator._first_text(event, "prompt", "student_prompt", "input")
        response = VivaQuestionGenerator._first_text(
            event, "response_summary", "assistant_summary", "summary", "response"
        )
        action = VivaQuestionGenerator._first_text(event, "action", "event_type", "type")
        if not (prompt or response or action):
            return None

        step: dict[str, Any] = {"step_number": step_number}
        if action:
            step["action"] = action[:160]
        if prompt:
            step["student_prompt"] = prompt[:1_200]
        if response:
            step["response_or_result_summary"] = response[:1_200]
        for output_key, aliases in {
            "hint_count": ("hint_count", "hints_used"),
            "revision_count": ("revision_count", "revisions"),
            "language": ("language", "preferred_language"),
            "self_correction": ("self_correction", "correction_summary"),
        }.items():
            value = next((event[key] for key in aliases if key in event), None)
            if isinstance(value, (int, float, bool)):
                step[output_key] = value
            elif isinstance(value, str) and value.strip():
                step[output_key] = value[:300]
        return step

    @staticmethod
    def _first_text(event: Mapping[str, Any], *keys: str) -> str:
        for key in keys:
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _parse_response(content: str) -> list[VivaQuestion]:
        text = content.strip()
        if text.startswith("```"):
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end < start:
            raise VivaGenerationError("model response did not contain a JSON list")
        try:
            payload = json.loads(text[start : end + 1])
            if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
                raise TypeError("expected a JSON array")
            questions = [VivaQuestion.model_validate(item) for item in payload]
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise VivaGenerationError("model response was not a valid viva-question list") from exc
        if len(questions) != 3:
            raise VivaGenerationError("model must return exactly three viva questions")
        return questions
