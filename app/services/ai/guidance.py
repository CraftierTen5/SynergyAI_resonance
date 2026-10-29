"""Multilingual tutor orchestration over provider adapters and Redis context."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol

from app.core.config import get_settings
from app.schemas.ai import AITutorRequest, StreamChunk, TutorMessage
from app.services.ai.base import BaseAIProvider
from app.services.ai.usage import RedisUsageMeter
from app.services.assessment.persistence import (
    AssessmentPersistenceFactory,
    AssessmentPersistenceService,
)

logger = logging.getLogger(__name__)


class _RedisClient(Protocol):
    """Redis commands used here; protocol keeps test and production clients typed."""

    async def lrange(self, key: str, start: int, end: int) -> list[Any]: ...

    async def rpush(self, key: str, value: str) -> Any: ...

    async def ltrim(self, key: str, start: int, end: int) -> Any: ...

    async def expire(self, key: str, seconds: int) -> Any: ...

    async def get(self, key: str) -> Any: ...

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def xadd(
        self,
        stream: str,
        fields: dict[str, str],
        *,
        maxlen: int | None = None,
        approximate: bool = False,
    ) -> Any: ...


class MultilingualGuidanceService:
    """Build policy-first multilingual prompts, stream guidance, and meter usage."""

    def __init__(
        self,
        providers: Mapping[str, BaseAIProvider],
        redis_client: _RedisClient,
        usage_meter: RedisUsageMeter,
        *,
        max_history_messages: int = 24,
        context_ttl_seconds: int = 2 * 60 * 60,
        first_token_metric: Callable[[float], None] | None = None,
        cache_ttl_seconds: int | None = None,
        log_stream_maxlen: int | None = None,
        persistence: AssessmentPersistenceService | None = None,
        persistence_factory: AssessmentPersistenceFactory | None = None,
    ) -> None:
        if max_history_messages < 1 or context_ttl_seconds < 60:
            raise ValueError("history limit and context TTL are outside supported bounds")
        if persistence is not None and persistence_factory is not None:
            raise ValueError("configure persistence or persistence_factory, not both")
        self.providers = dict(providers)
        self.redis = redis_client
        self.usage_meter = usage_meter
        self.persistence = persistence
        self.persistence_factory = persistence_factory
        self.max_history_messages = max_history_messages
        self.context_ttl_seconds = context_ttl_seconds
        self.first_token_metric = first_token_metric
        settings = get_settings()
        self.cache_ttl_seconds = (
            settings.ai_response_cache_ttl_seconds if cache_ttl_seconds is None else cache_ttl_seconds
        )
        self.log_stream_maxlen = settings.ai_log_stream_maxlen if log_stream_maxlen is None else log_stream_maxlen
        self._log_tasks: set[asyncio.Task[None]] = set()

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
        persistence = self.persistence
        if self.persistence_factory is not None:
            persistence = self.persistence_factory.for_institution(institution_id)
        if persistence is not None:
            if persistence.store.institution_id != institution_id:
                raise ValueError("persistence tenant must match the request institution")
            await persistence.ensure_conversation(
                conversation_id, policy_version=request.policy_version,
                policy_tier=request.policy_tier,
                actor_pseudonymous_id=request.actor_pseudonymous_id,
                assignment_id=request.assignment_id,
            )
            await persistence.append_message(
                conversation_id, role="user", content=current_question,
                idempotency_key=f"{request.request_id}:user",
            )
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

        response_cache_key = self._response_key(assembled, provider_name, model)
        cached = await self._cache_get(response_cache_key)
        if cached is not None:
            await self._append_context(cache_key, {"role": "user", "content": current_question})
            await self._append_context(cache_key, {"role": "assistant", "content": cached})
            if persistence is not None:
                await persistence.append_message(
                    conversation_id, role="assistant", content=cached,
                    idempotency_key=f"{request.request_id}:assistant",
                )
            self._enqueue_log({"event": "cache_hit", "request_id": request.request_id,
                               "tenant": hashlib.sha256(institution_id.encode()).hexdigest(),
                               "provider": provider_name})
            yield StreamChunk(request_id=request.request_id, delta=cached, provider=provider_name,
                              model=model, is_final=True)
            return

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
        run_id = None
        model_version_id = None
        if persistence is not None:
            try:
                model_version_id = await persistence.find_model_version_id(
                    provider.provider_name, model
                )
                run_id = await persistence.start_run(
                    conversation_id, request_id=request.request_id,
                    policy_version=request.policy_version, policy_tier=request.policy_tier,
                    provider=provider.provider_name, model_name=model,
                    model_version_id=model_version_id,
                )
            except Exception:
                await self.usage_meter.release(reservation)
                raise

        generated = ""
        input_tokens: int | None = None
        output_tokens: int | None = None
        settled = False
        run_finished = False
        generation_started = False
        started_at = time.monotonic()
        first_token_recorded = False
        try:
            await self._append_context(cache_key, {"role": "user", "content": current_question})
            generation_started = True
            active_provider = provider
            try:
                async for chunk in active_provider.stream_completion(assembled):
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
            except Exception:
                fallback = self.providers.get("gemini") if provider_name == "groq" else None
                if generated or fallback is None:
                    raise
                await self.usage_meter.release(reservation)
                active_provider = fallback
                model = self._model_name(active_provider)
                reservation = await self.usage_meter.reserve(
                    institution_id, active_provider.provider_name, model,
                    input_upper_bound, request.max_output_tokens,
                )
                input_tokens = output_tokens = None
                self._enqueue_log({"event": "provider_fallback", "request_id": request.request_id,
                                   "tenant": hashlib.sha256(institution_id.encode()).hexdigest(),
                                   "provider": "gemini"})
                async for chunk in active_provider.stream_completion(assembled):
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
                await self._cache_set(response_cache_key, generated)
                if persistence is not None:
                    await persistence.append_message(
                        conversation_id, role="assistant", content=generated,
                        idempotency_key=f"{request.request_id}:assistant",
                    )
            if persistence is not None and run_id is not None:
                actual_model_version_id = await persistence.find_model_version_id(
                    active_provider.provider_name, model
                )
                await persistence.finish_run(
                    run_id, input_tokens=actual_input, output_tokens=actual_output,
                    result_sha256=hashlib.sha256(generated.encode("utf-8")).hexdigest(),
                    provider=active_provider.provider_name, model_name=model,
                    model_version_id=actual_model_version_id,
                    update_model_version=True,
                )
                run_finished = True
            self._enqueue_log({"event": "completion", "request_id": request.request_id,
                               "tenant": hashlib.sha256(institution_id.encode()).hexdigest(),
                               "provider": active_provider.provider_name})
        finally:
            if persistence is not None and run_id is not None and not run_finished:
                try:
                    await persistence.finish_run(
                        run_id,
                        error_category="persistence_error" if settled else "provider_error",
                    )
                    run_finished = True
                except Exception:
                    logger.exception("failed to close persisted AI run")
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

    async def _cache_get(self, key: str) -> str | None:
        if self.cache_ttl_seconds == 0:
            return None
        try:
            value = await self.redis.get(key)
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            return value if isinstance(value, str) and value else None
        except Exception:
            logger.warning("AI response cache read failed", exc_info=True)
            return None

    async def _cache_set(self, key: str, value: str) -> None:
        if self.cache_ttl_seconds == 0:
            return
        try:
            await self.redis.set(key, value, ex=self.cache_ttl_seconds)
        except Exception:
            logger.warning("AI response cache write failed", exc_info=True)

    def _enqueue_log(self, event: dict[str, str]) -> None:
        """Schedule Redis stream logging without putting XADD on the response path."""
        task = asyncio.create_task(self._write_log(event))
        self._log_tasks.add(task)
        task.add_done_callback(self._log_tasks.discard)

    async def _write_log(self, event: dict[str, str]) -> None:
        try:
            await self.redis.xadd("resonanceai:logs:v1", event,
                                  maxlen=self.log_stream_maxlen, approximate=True)
        except Exception:
            logger.warning("asynchronous AI stream log write failed", exc_info=True)

    @staticmethod
    def _response_key(request: AITutorRequest, provider: str, model: str) -> str:
        payload = request.model_dump(mode="json", exclude={"request_id", "conversation_id"})
        encoded = json.dumps([payload, provider, model], sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        tenant_hash = hashlib.sha256((request.institution_id or "").encode("utf-8")).hexdigest()
        return f"resonanceai:response:v1:{tenant_hash}:{digest}"

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
