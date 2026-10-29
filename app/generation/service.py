"""Configured provider dispatch with bounded timeout and failover."""

import asyncio
from collections.abc import Mapping

from app.exceptions import ProviderUnavailableError
from app.generation.provider_interface import (
    GenerationProvider,
    GenerationRequest,
    GenerationResult,
)


class ConfiguredGenerationService:
    """Dispatch only to explicitly configured providers in a fixed order."""

    def __init__(
        self,
        *,
        providers: Mapping[str, GenerationProvider],
        primary_provider: str,
        fallback_provider: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if primary_provider not in providers:
            raise ValueError(f"Primary generation provider is not configured: {primary_provider}")
        if fallback_provider is not None and fallback_provider not in providers:
            raise ValueError(f"Fallback generation provider is not configured: {fallback_provider}")
        if fallback_provider == primary_provider:
            raise ValueError("fallback_provider must differ from primary_provider")
        for provider_name, provider in providers.items():
            if provider.provider_name != provider_name:
                raise ValueError(
                    f"Provider mapping key {provider_name!r} does not match "
                    f"provider name {provider.provider_name!r}"
                )

        self.providers = dict(providers)
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.timeout_seconds = timeout_seconds

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.policy_level.value == "RED":
            raise ProviderUnavailableError("Generation is forbidden for RED policy")

        candidates = [self.primary_provider]
        if self.fallback_provider is not None:
            candidates.append(self.fallback_provider)

        last_error: Exception | None = None
        for provider_name in candidates:
            provider = self.providers[provider_name]
            try:
                result = await asyncio.wait_for(
                    provider.generate(request), timeout=self.timeout_seconds
                )
                if not isinstance(result, GenerationResult):
                    result = GenerationResult.model_validate(result)
                if result.provider != provider_name:
                    raise ValueError("Generation result provider does not match dispatched provider")
                return result
            except Exception as exc:
                last_error = exc

        raise ProviderUnavailableError(
            "All configured generation providers failed or timed out"
        ) from last_error
