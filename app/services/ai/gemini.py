import json
from collections.abc import AsyncIterator

from pydantic import SecretStr

from app.schemas.ai import AITutorRequest, AITutorResponse, StreamChunk
from app.services.ai.base import BaseAIProvider
from app.services.ai.errors import ProviderConfigurationError, ProviderResponseError


class GeminiFlashAdapter(BaseAIProvider):
    provider_name = "gemini"

    def _api_key(self) -> str:
        secret: SecretStr | None = self.settings.gemini_api_key
        if secret is None or not secret.get_secret_value():
            raise ProviderConfigurationError("GEMINI_API_KEY is not configured")
        return secret.get_secret_value()

    def _url(self, *, stream: bool) -> str:
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self.settings.gemini_base_url}/models/{self.settings.gemini_model}:{method}"

    def _payload(self, request: AITutorRequest) -> dict[str, object]:
        system = "\n\n".join(
            message.content for message in request.messages if message.role == "system"
        )
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        payload: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key(), "Content-Type": "application/json"}

    async def generate_structured(
        self, prompt: str, images: list[tuple[str, bytes]], schema: dict[str, object] | None = None
    ) -> str:
        """Call the configured Gemini model for validated JSON preprocessing output."""
        parts: list[dict[str, object]] = [{"text": prompt}]
        for mime_type, content in images:
            import base64

            parts.append({"inlineData": {"mimeType": mime_type, "data": base64.b64encode(content).decode("ascii")}})
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema or {
                    "type": "OBJECT",
                    "properties": {
                        "detected_language": {"type": "STRING"},
                        "processed_text": {"type": "STRING"},
                        "translated": {"type": "BOOLEAN"},
                        "extracted_text": {"type": "STRING"},
                        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["detected_language", "processed_text", "translated", "extracted_text"],
                },
                "temperature": 0,
            },
        }
        response = await self._request("POST", self._url(stream=False), headers=self._headers(), json=payload)
        try:
            return self._content(response.json())
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Gemini returned an invalid preprocessing payload") from exc

    @staticmethod
    def _content(data: dict[str, object]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0]["content"]
        return "".join(part.get("text", "") for part in content.get("parts", []))

    async def generate_completion(self, request: AITutorRequest) -> AITutorResponse:
        response = await self._request(
            "POST", self._url(stream=False), headers=self._headers(), json=self._payload(request)
        )
        try:
            data = response.json()
            usage = data.get("usageMetadata") or {}
            candidate = data["candidates"][0]
            return AITutorResponse(
                request_id=request.request_id,
                content=self._content(data),
                provider=self.provider_name,
                model=self.settings.gemini_model,
                finish_reason=candidate.get("finishReason"),
                input_tokens=usage.get("promptTokenCount"),
                output_tokens=usage.get("candidatesTokenCount"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderResponseError("Gemini returned an invalid completion payload") from exc

    async def stream_completion(self, request: AITutorRequest) -> AsyncIterator[StreamChunk]:
        async with self._stream_lines(
            "POST",
            self._url(stream=True),
            headers=self._headers(),
            json=self._payload(request),
        ) as lines:
            async for line in lines:
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                    text = self._content(data)
                    candidate = (data.get("candidates") or [{}])[0]
                    finish = candidate.get("finishReason")
                    usage = data.get("usageMetadata") or {}
                    input_tokens = usage.get("promptTokenCount")
                    output_tokens = usage.get("candidatesTokenCount")
                    if text or finish or input_tokens is not None or output_tokens is not None:
                        yield StreamChunk(
                            request_id=request.request_id,
                            delta=text,
                            provider=self.provider_name,
                            model=self.settings.gemini_model,
                            finish_reason=finish,
                            is_final=finish is not None or input_tokens is not None,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                    raise ProviderResponseError("Gemini returned an invalid stream event") from exc
