"""HTTP client for the separately hosted ResonanceAI GPU inference API."""

from __future__ import annotations

import logging

import httpx
from pydantic import ValidationError

from app.agent.contracts import ModelRecommendation, NormalizedInput
from app.core.config import Settings
from app.schemas.classifier import IntentActionRecommendation
from app.services.ai.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

logger = logging.getLogger(__name__)


class QwenInferenceModelClient:
    """Return validated intent/action labels; policy remains a backend decision."""

    model_version = "qwen3.5-4b-lora"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def classify(self, normalized: NormalizedInput) -> ModelRecommendation:
        endpoint = self.settings.model_inference_url
        api_key = self.settings.model_inference_api_key
        if endpoint is None or api_key is None or not api_key.get_secret_value():
            raise ProviderConfigurationError("model inference URL and API key are required")

        context = "\n\n".join(
            part for part in (normalized.assignment_context, normalized.extracted_document_text) if part
        )
        # These bounds mirror the separate inference server's PredictRequest.
        if len(normalized.normalized_message) > 8_000 or len(context) > 4_000:
            raise ProviderResponseError("normalized input exceeds the inference API limits")

        payload = {
            "user_message": normalized.normalized_message,
            "policy_flag": normalized.policy_flag,
            "context": context,
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.model_inference_timeout),
                transport=self.transport,
            ) as client:
                response = await client.post(
                    endpoint,
                    headers={"X-API-Key": api_key.get_secret_value()},
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("model inference request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("model inference connection failed (%s)", type(exc).__name__)
            raise ProviderUnavailableError("model inference service is unavailable") from exc

        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError("model inference service rejected its API key")
        if response.status_code == 429 or response.status_code >= 500:
            raise ProviderUnavailableError("model inference service is temporarily unavailable")
        if response.status_code >= 400:
            raise ProviderResponseError("model inference service rejected the request")

        try:
            prediction = IntentActionRecommendation.model_validate(response.json())
            recommendation = ModelRecommendation(
                intent=prediction.intent,
                recommended_action=prediction.recommended_action,
                model_version=self.model_version,
            )
        except (ValueError, TypeError, ValidationError) as exc:
            logger.warning("model inference returned an invalid prediction (%s)", type(exc).__name__)
            raise ProviderResponseError("model inference service returned an invalid prediction") from exc
        return recommendation
