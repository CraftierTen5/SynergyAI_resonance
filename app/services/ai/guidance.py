"""Multilingual tutor orchestration over provider adapters and Redis context."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping

from app.schemas.ai import AITutorRequest, StreamChunk, TutorMessage
from app.services.ai.base import BaseAIProvider
from app.services.ai.usage import RedisUsageMeter

logger = logging.getLogger(__name__)


class MultilingualGuidanceService:
    """Build policy-first multilingual prompts, stream guidance, and meter usage."""

    def __init__(
        self,
        providers: Mapping[str, BaseAIProvider],
        redis_client: object,
        usage_meter: RedisUsageMeter,
        *,
        max_history_messages: int = 24,
        context_ttl_seconds: int = 2 * 60 * 60,
        first_token_metric: Callable[[float], None] | None = None,
    ) -> None:
        if max_history_messages < 1 or context_ttl_seconds < 60:
            raise ValueError("history limit and context TTL are outside supported bounds")
        self.providers = dict(providers)
        self.redis = redis_client
        self.usage_meter = usage_meter
        self.max_history_messages = max_history_messages
        self.context_ttl_seconds = context_ttl_seconds
        self.first_token_metric = first_token_metric

    async def generate_multilingual_guidance(
        self, request: AITutorRequest, provider_name: str
    ) -> AsyncIterator[StreamChunk]:
        """Yield tutor chunks after policy prompt assembly and budget reservation.

        ``request.system_instruction`` must come from the Layer 3A policy
        decision. Tenant and conversation identifiers are mandatory to prevent
        cross-institution context or unmetered provider calls.
        """
        institution_id = request.institution_id
        conversation_id = request.conversation_id
        if not institution_id or not conversation_id:
            raise ValueError("institution_id and conversation_id are required")
        try:
            provider = self.providers[provider_name]
        except KeyError as exc:
            raise ValueError(f"AI provider is not configured: {provider_name}") from exc

        model = self._model_name(provider)
        current_index = next(
            (index for index in range(len(request.messages) - 1, -1, -1) if request.messages[index].role == "user"),
            None,
        )
        if current_index is None:
            raise ValueError("request must contain a user message")
        current_question = request.messages[current_index].content
        system_prompt = self._system_prompt(request)

        cache_key = self._context_key(institution_id, conversation_id)
        history = await self._load_history(cache_key)
        if not history:
            history = [
                message
                for message in request.messages[:current_index]
                if message.role in ("user", "assistant")
            ][-self.max_history_messages :]

        messages = [TutorMessage(role="system", content=system_prompt)]
        messages.extend(history)
        messages.append(TutorMessage(role="user", content=current_question))
        assembled = request.model_copy(update={"messages": messages})

        # Reserve the worst case before calling the provider. Character count
        # deliberately overestimates token count for multilingual prompts.
        input_upper_bound = max(1, sum(len(message.content) for message in messages))
        reservation = await self.usage_meter.reserve(
            institution_id,
            provider.provider_name,
            model,
            input_upper_bound,
            request.max_output_tokens,
        )

        generated = ""
        input_tokens: int | None = None
        output_tokens: int | None = None
        settled = False
        generation_started = False
        started_at = time.monotonic()
        first_token_recorded = False
        try:
            await self._append_context(cache_key, {"role": "user", "content": current_question})
            generation_started = True
            async for chunk in provider.stream_completion(assembled):
                if chunk.delta:
                    generated += chunk.delta
                    if not first_token_recorded:
                        first_token_recorded = True
                        if self.first_token_metric is not None:
                            try:
                                self.first_token_metric((time.monotonic() - started_at) * 1000)
                            except Exception:
                                logger.exception("first-token metric callback failed")
                if chunk.input_tokens is not None:
                    input_tokens = chunk.input_tokens
                if chunk.output_tokens is not None:
                    output_tokens = chunk.output_tokens
                yield chunk

            actual_input = input_tokens if input_tokens is not None else self._estimate_tokens(
                "\n".join(message.content for message in messages)
            )
            actual_output = output_tokens if output_tokens is not None else min(
                request.max_output_tokens, self._estimate_tokens(generated)
            )
            await self.usage_meter.settle(reservation, actual_input, actual_output)
            settled = True
            if generated:
                await self._append_context(cache_key, {"role": "assistant", "content": generated})
        finally:
            if not settled:
                try:
                    if generation_started:
                        actual_input = input_tokens if input_tokens is not None else self._estimate_tokens(
                            "\n".join(message.content for message in messages)
                        )
                        actual_output = output_tokens if output_tokens is not None else min(
                            request.max_output_tokens, self._estimate_tokens(generated)
                        )
                        await self.usage_meter.settle(reservation, actual_input, actual_output)
                    else:
                        await self.usage_meter.release(reservation)
                except Exception:
                    logger.exception("failed to settle or release AI usage reservation")

    def _system_prompt(self, request: AITutorRequest) -> str:
        policy = request.system_instruction.strip()
        if not policy:
            raise ValueError("a policy system_instruction from Layer 3A is required")

        if request.instruction_profile == "guided":
            tutor_style = (
                "Act as a patient academic mentor. Help the student reason step by step; "
                "ask useful questions and avoid doing assessed work for them."
            )
        else:
            tutor_style = "Give clear, direct academic guidance while explaining the reasoning."

        language_rules = (
            f"Respond in the student's preferred language or regional phrasing: {request.language}. "
            "Keep technical programming and engineering terms, API names, code identifiers, "
            "library names, units, and standard terminology in English. Explain their meaning "
            "and surrounding context naturally in the student's preferred language; do not "
            "translate or alter code identifiers."
        )
        if request.technical_terms:
            terms = ", ".join(
                dict.fromkeys(
                    term.strip()
                    for term in request.technical_terms
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_+#./:-]{0,63}", term.strip())
                )
            )
            if terms:
                language_rules += f" Preserve these terms verbatim in English: {terms}."
        return "\n\n".join((policy, language_rules, tutor_style))

    async def _load_history(self, key: str) -> list[TutorMessage]:
        raw_entries = await self.redis.lrange(key, -self.max_history_messages, -1)
        history: list[TutorMessage] = []
        for raw in raw_entries:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                item = json.loads(raw)
                if item.get("role") in ("user", "assistant") and isinstance(item.get("content"), str):
                    history.append(TutorMessage(role=item["role"], content=item["content"]))
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.warning("discarding malformed conversation context entry")
        return history[-self.max_history_messages :]

    async def _append_context(self, key: str, message: dict[str, str]) -> None:
        await self.redis.rpush(key, json.dumps(message, ensure_ascii=False, separators=(",", ":")))
        await self.redis.ltrim(key, -self.max_history_messages, -1)
        await self.redis.expire(key, self.context_ttl_seconds)

    @staticmethod
    def _context_key(institution_id: str, conversation_id: str) -> str:
        tenant_hash = hashlib.sha256(institution_id.encode("utf-8")).hexdigest()
        conversation_hash = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        return f"resonanceai:context:v1:{tenant_hash}:{conversation_hash}"

    @staticmethod
    def _model_name(provider: BaseAIProvider) -> str:
        provider_attr = {"groq": "groq_model", "gemini": "gemini_model"}.get(
            provider.provider_name
        )
        if provider_attr is None:
            raise ValueError(f"No model identifier configured for provider {provider.provider_name}")
        return str(getattr(provider.settings, provider_attr))

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, (len(text) + 3) // 4)
