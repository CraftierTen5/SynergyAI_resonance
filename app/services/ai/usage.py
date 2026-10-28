"""Tenant-scoped atomic AI budget reservations and token cost metering."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Mapping


class BudgetCapExceededError(RuntimeError):
    pass


class UsageConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelPricing:
    """USD per one million input/output tokens for a configured model."""

    input_usd_per_million: Decimal
    output_usd_per_million: Decimal


@dataclass(frozen=True)
class UsageReservation:
    institution_id: str
    provider: str
    model: str
    period: str
    reserved_microusd: int


class RedisUsageMeter:
    """Meter against Redis using micro-USD and atomic Lua budget checks.

    Budgets are supplied by institution in USD and apply per UTC calendar month.
    Keep pricing and budgets in centrally managed configuration, not client input.
    """

    _RESERVE_SCRIPT = """
    local spent = tonumber(redis.call('GET', KEYS[1]) or '0')
    local reserved = tonumber(redis.call('GET', KEYS[2]) or '0')
    local amount = tonumber(ARGV[1])
    local cap = tonumber(ARGV[2])
    if spent + reserved + amount > cap then return 0 end
    redis.call('INCRBY', KEYS[2], amount)
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    redis.call('EXPIRE', KEYS[2], ARGV[3])
    return 1
    """
    _SETTLE_SCRIPT = """
    local reserved = tonumber(redis.call('GET', KEYS[1]) or '0')
    local amount = tonumber(ARGV[1])
    local actual = tonumber(ARGV[2])
    local ttl = tonumber(ARGV[3])
    redis.call('SET', KEYS[1], math.max(0, reserved - amount), 'EX', ttl)
    redis.call('INCRBY', KEYS[2], actual)
    redis.call('INCRBY', KEYS[3], tonumber(ARGV[4]))
    redis.call('INCRBY', KEYS[4], tonumber(ARGV[5]))
    redis.call('EXPIRE', KEYS[2], ttl)
    redis.call('EXPIRE', KEYS[3], ttl)
    redis.call('EXPIRE', KEYS[4], ttl)
    return 1
    """

    def __init__(
        self,
        redis_client: object,
        *,
        budgets_usd: Mapping[str, Decimal | str | int],
        pricing: Mapping[tuple[str, str], ModelPricing],
        key_prefix: str = "resonanceai:usage",
        retention_seconds: int = 400 * 24 * 60 * 60,
    ) -> None:
        if retention_seconds < 60:
            raise ValueError("usage retention must be at least 60 seconds")
        self.redis = redis_client
        self.budgets_usd = {key: Decimal(value) for key, value in budgets_usd.items()}
        self.pricing = dict(pricing)
        self.key_prefix = key_prefix
        self.retention_seconds = retention_seconds

    def price_microusd(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> int:
        rate = self.pricing.get((provider, model))
        if rate is None:
            raise UsageConfigurationError(f"No usage pricing configured for {provider}/{model}")
        # At USD per million tokens, rate * token count is micro-USD.
        amount = rate.input_usd_per_million * input_tokens + rate.output_usd_per_million * output_tokens
        return int(amount.to_integral_value(rounding=ROUND_CEILING))

    async def reserve(
        self,
        institution_id: str,
        provider: str,
        model: str,
        input_token_upper_bound: int,
        max_output_tokens: int,
    ) -> UsageReservation:
        budget = self.budgets_usd.get(institution_id)
        if budget is None or budget < 0:
            raise UsageConfigurationError("No valid AI budget configured for institution")
        period = datetime.now(UTC).strftime("%Y-%m")
        amount = self.price_microusd(provider, model, input_token_upper_bound, max_output_tokens)
        if amount <= 0:
            amount = 1
        digest = hashlib.sha256(institution_id.encode("utf-8")).hexdigest()
        base = f"{self.key_prefix}:{{{digest}:{period}}}"
        cap_microusd = int((budget * Decimal(1_000_000)).to_integral_value(rounding=ROUND_CEILING))
        allowed = await self.redis.eval(
            self._RESERVE_SCRIPT,
            2,
            f"{base}:spent",
            f"{base}:reserved",
            amount,
            cap_microusd,
            self.retention_seconds,
        )
        if int(allowed) != 1:
            raise BudgetCapExceededError("Institution AI usage budget is exhausted")
        return UsageReservation(institution_id, provider, model, period, amount)

    async def settle(
        self, reservation: UsageReservation, input_tokens: int, output_tokens: int
    ) -> int:
        actual = self.price_microusd(
            reservation.provider,
            reservation.model,
            max(0, input_tokens),
            max(0, output_tokens),
        )
        if actual <= 0:
            actual = 1
        spent_key, reserved_key = self._keys(reservation)
        await self.redis.eval(
            self._SETTLE_SCRIPT,
            4,
            reserved_key,
            spent_key,
            f"{spent_key}:input_tokens",
            f"{spent_key}:output_tokens",
            reservation.reserved_microusd,
            actual,
            self.retention_seconds,
            max(0, input_tokens),
            max(0, output_tokens),
        )
        return actual

    async def release(self, reservation: UsageReservation) -> None:
        spent_key, reserved_key = self._keys(reservation)
        await self.redis.eval(
            self._SETTLE_SCRIPT,
            4,
            reserved_key,
            spent_key,
            f"{spent_key}:input_tokens",
            f"{spent_key}:output_tokens",
            reservation.reserved_microusd,
            0,
            self.retention_seconds,
            0,
            0,
        )

    async def get_usage(self, institution_id: str, period: str | None = None) -> dict[str, int]:
        """Read the institution's accumulated micro-USD and token counters."""
        period = period or datetime.now(UTC).strftime("%Y-%m")
        digest = hashlib.sha256(institution_id.encode("utf-8")).hexdigest()
        spent_key = f"{self.key_prefix}:{{{digest}:{period}}}:spent"
        values = await self.redis.mget(
            spent_key,
            f"{spent_key}:input_tokens",
            f"{spent_key}:output_tokens",
        )
        return {
            "spent_microusd": int(values[0] or 0),
            "input_tokens": int(values[1] or 0),
            "output_tokens": int(values[2] or 0),
        }

    def _keys(self, reservation: UsageReservation) -> tuple[str, str]:
        digest = hashlib.sha256(reservation.institution_id.encode("utf-8")).hexdigest()
        base = f"{self.key_prefix}:{{{digest}:{reservation.period}}}"
        return f"{base}:spent", f"{base}:reserved"
