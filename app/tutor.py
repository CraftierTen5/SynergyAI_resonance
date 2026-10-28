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
    def __init__(self, api_key: str, model: str, base_url: str):
        self.api_key, self.model, self.base_url = api_key, model, base_url.rstrip("/")

    def generate(self, message: str, mode: str, language: str, context: str = "") -> str:
        if not self.api_key or not self.model:
            raise ProviderUnavailable("LLM provider configuration is incomplete")
        instruction = {
            "socratic": "Act as a Socratic tutor. Give hints, ask questions, and explain concepts; do not write a complete submission.",
            "full": "Help directly, explain your reasoning, and support learning.",
        }[mode]
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": f"{instruction} Reply in {language}. Assignment context: {context}"},
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


def configured_provider():
    if settings.llm_provider in ("openai", "openai_compatible") and settings.llm_api_key and settings.llm_model:
        return OpenAICompatibleProvider(settings.llm_api_key, settings.llm_model, settings.llm_base_url)
    return None
