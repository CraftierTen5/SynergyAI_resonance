import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import TypeVar

import httpx

from app.services.ai.errors import CircuitOpenError

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Small async breaker: one probe is admitted after the recovery interval."""

    def __init__(self, failure_threshold: int, recovery_seconds: float) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._probe_in_flight = False
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._opened_at is None:
                return
            if time.monotonic() - self._opened_at < self.recovery_seconds:
                raise CircuitOpenError("provider circuit is open")
            if self._probe_in_flight:
                raise CircuitOpenError("provider circuit is awaiting its recovery probe")
            self._probe_in_flight = True

    async def record_success(self) -> None:
        async with self._lock:
            self._failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    async def record_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()


async def with_retries(
    operation: Callable[[], Awaitable[T]], *, retries: int, base_delay: float
) -> T:
    """Retry transport and transient HTTP failures with exponential backoff."""
    for attempt in range(retries + 1):
        try:
            return await operation()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            if attempt == retries:
                raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (408, 429, 500, 502, 503, 504) or attempt == retries:
                raise
            retry_after = exc.response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else base_delay * (2**attempt)
            await asyncio.sleep(min(delay, 30.0))
            continue
        await asyncio.sleep(min(base_delay * (2**attempt), 10.0))
    raise RuntimeError("unreachable retry state")
