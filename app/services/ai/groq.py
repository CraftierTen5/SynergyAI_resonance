import json
from collections.abc import AsyncIterator

from pydantic import SecretStr

from app.schemas.ai import AITutorRequest, AITutorResponse, StreamChunk
from app.services.ai.base import BaseAIProvider
from app.services.ai.errors import ProviderConfigurationError, ProviderResponseError


class GroqAdapter(BaseAIProvider):
    provider_name = "groq"

    def _api_key(self) -> str:
        secret: SecretStr | None = self.settings.groq_api_key
        if secret is None or not secret.get_secret_value():
            raise ProviderConfigurationError("GROQ_API_KEY is not configured")
        return secret.get_secret_value()

    def _url(self) -> str:
        return f"{self.settings.groq_base_url}/chat/completions"

    def _payload(self, request: AITutorRequest, *, stream: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.settings.groq_model,
            "messages": [message.model_dump() for message in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "stream": stream,
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}

    async def generate_completion(self, request: AITutorRequest) -> AITutorResponse:
        response = await self._request(
            "POST", self._url(), headers=self._headers(), json=self._payload(request, stream=False)
        )
        try:
            data = response.json()
            choice = data["choices"][0]
            usage = data.get("usage") or {}
            return AITutorResponse(
                request_id=request.request_id,
                content=choice["message"]["content"],
                provider=self.provider_name,
                model=data.get("model", self.settings.groq_model),
                finish_reason=choice.get("finish_reason"),
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Groq returned an invalid completion payload") from exc

    async def stream_completion(self, request: AITutorRequest) -> AsyncIterator[StreamChunk]:
        async with self._stream_lines(
            "POST", self._url(), headers=self._headers(), json=self._payload(request, stream=True)
        ) as lines:
            async for line in lines:
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    yield StreamChunk(
                        request_id=request.request_id,
                        provider=self.provider_name,
                        model=self.settings.groq_model,
                        is_final=True,
                    )
                    return
                try:
                    data = json.loads(raw)
                    choice = (data.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    finish = choice.get("finish_reason")
                    if text or finish:
                        yield StreamChunk(
                            request_id=request.request_id,
                            delta=text,
                            provider=self.provider_name,
                            model=data.get("model", self.settings.groq_model),
                            finish_reason=finish,
                            is_final=finish is not None,
                        )
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise ProviderResponseError("Groq returned an invalid stream event") from exc
