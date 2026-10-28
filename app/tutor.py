"""Mockable LLM adapter and deterministic policy-first tutor orchestration."""
import json
import urllib.error
import urllib.request

from app.config import settings


class ProviderUnavailable(Exception):
    pass


class ProviderFailure(Exception):
    pass


class OpenAICompatibleProvider:
    def __init__(self, api_key: str, model: str, base_url: str, provider_name: str = "openai"):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")
        self.provider_name = provider_name

    def generate(self, message: str, mode: str, language: str, context: str = "") -> str:
        if not self.api_key or not self.model:
            raise ProviderUnavailable("LLM provider configuration is incomplete")
        instruction = {
            "socratic": "Act as a Socratic tutor. Give hints, ask questions, and explain concepts; do not write a complete submission.",
            "full": "Help directly, explain your reasoning, and support learning.",
        }[mode]
        system = f"{instruction} Reply in {language}. Assignment context: {context}"
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": message}], "temperature": 0.5}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                data = json.loads(response.read())
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            # Avoid forwarding provider errors, which can contain sensitive request details.
            raise ProviderFailure("LLM provider request failed") from exc


class GeminiProvider:
    """Gemini generateContent adapter, separate from OpenAI-compatible providers."""

    provider_name = "gemini"

    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")

    def generate(self, message: str, mode: str, language: str, context: str = "") -> str:
        if not self.api_key or not self.model:
            raise ProviderUnavailable("Gemini provider configuration is incomplete")
        instruction = {
            "socratic": "Act as a Socratic tutor. Give hints and questions; do not produce a complete submission.",
            "full": "Help directly, explain reasoning, and support learning.",
        }[mode]
        payload = {
            "systemInstruction": {"parts": [{"text": f"{instruction} Reply in {language}. Assignment: {context}"}]},
            "contents": [{"role": "user", "parts": [{"text": message}]}],
            "generationConfig": {"temperature": 0.5},
        }
        request = urllib.request.Request(
            f"{self.base_url}/models/{self.model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                data = json.loads(response.read())
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except Exception as exc:
            raise ProviderFailure("Gemini provider request failed") from exc


def configured_provider(provider_name: str | None = None):
    selected = (provider_name or settings.llm_provider).strip().lower()
    if not selected:
        selected = "groq" if settings.groq_api_key else "gemini" if settings.gemini_api_key else ""
    if selected == "groq" and settings.groq_api_key and settings.groq_model:
        return OpenAICompatibleProvider(settings.groq_api_key, settings.groq_model,
                                        settings.groq_base_url, "groq")
    if selected == "gemini" and settings.gemini_api_key and settings.gemini_model:
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model, settings.gemini_base_url)
    if selected in ("openai", "openai_compatible") and settings.llm_api_key and settings.llm_model:
        return OpenAICompatibleProvider(settings.llm_api_key, settings.llm_model, settings.llm_base_url)
    return None
