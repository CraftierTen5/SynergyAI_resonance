from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import AsyncIterator as AsyncIteratorType

import httpx

from app.core.config import Settings, get_settings
from app.schemas.ai import AITutorRequest, AITutorResponse, StreamChunk
from app.services.ai.errors import (
    AIProviderError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.ai.resilience import CircuitBreaker, with_retries


class BaseAIProvider(ABC):
    """Common provider interface with shared timeout, retry and breaker behavior."""

    provider_name: str

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._timeout = httpx.Timeout(
            connect=self.settings.ai_connect_timeout_seconds,
            read=self.settings.ai_read_timeout_seconds,
            write=self.settings.ai_read_timeout_seconds,
            pool=self.settings.ai_connect_timeout_seconds,
        )
        self._breaker = CircuitBreaker(
            self.settings.ai_circuit_failure_threshold,
            self.settings.ai_circuit_recovery_seconds,
        )

    @abstractmethod
    async def generate_completion(self, request: AITutorRequest) -> AITutorResponse:
        """Return a complete provider-neutral tutor response."""

    @abstractmethod
    def stream_completion(self, request: AITutorRequest) -> AsyncIterator[StreamChunk]:
        """Return an async iterator of provider-neutral chunks."""

    async def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        await self._breaker.before_call()

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response

        try:
            response = await with_retries(
                send,
                retries=self.settings.ai_max_retries,
                base_delay=self.settings.ai_retry_base_seconds,
            )
            await self._breaker.record_success()
            return response
        except Exception as exc:
            await self._breaker.record_failure()
            if isinstance(exc, AIProviderError):
                raise
            if isinstance(exc, httpx.TimeoutException):
                raise ProviderTimeoutError(f"{self.provider_name} request timed out") from exc
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status in (401, 403):
                    raise ProviderAuthenticationError(f"{self.provider_name} rejected credentials") from exc
                if status == 429:
                    raise ProviderRateLimitError(f"{self.provider_name} rate limit reached") from exc
            raise ProviderUnavailableError(f"{self.provider_name} request failed") from exc

    @asynccontextmanager
    async def _stream_lines(
        self, method: str, url: str, **kwargs: object
    ) -> AsyncIteratorType[AsyncIterator[str]]:
        """Open an SSE response with retries before yielding any provider output."""
        await self._breaker.before_call()
        client = httpx.AsyncClient(timeout=self._timeout)

        async def open_response() -> httpx.Response:
            request = client.build_request(method, url, **kwargs)
            response = await client.send(request, stream=True)
            try:
                response.raise_for_status()
            except Exception:
                await response.aclose()
                raise
            return response

        try:
            response = await with_retries(
                open_response,
                retries=self.settings.ai_max_retries,
                base_delay=self.settings.ai_retry_base_seconds,
            )
            await self._breaker.record_success()
            try:
                yield response.aiter_lines()
            finally:
                await response.aclose()
        except Exception as exc:
            await self._breaker.record_failure()
            if isinstance(exc, AIProviderError):
                raise
            raise ProviderUnavailableError(f"{self.provider_name} stream failed") from exc
        finally:
            await client.aclose()
