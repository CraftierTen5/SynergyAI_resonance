"""Environment based settings. Secrets are never included in status responses."""
import os
from urllib.parse import urlsplit


class Settings:
    environment = os.getenv("APP_ENV", "development").strip().lower()
    database_url = os.getenv("DATABASE_URL", "").strip()
    llm_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    llm_api_key = os.getenv("LLM_API_KEY", "").strip()
    llm_model = os.getenv("LLM_MODEL", "").strip()
    llm_base_url = os.getenv("LLM_BASE_URL", "").strip() or "https://api.openai.com/v1"
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_base_url = os.getenv("GROQ_BASE_URL", "").strip() or "https://api.groq.com/openai/v1"
    groq_model = os.getenv("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile"
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_base_url = os.getenv("GEMINI_BASE_URL", "").strip() or "https://generativelanguage.googleapis.com/v1beta"
    gemini_model = os.getenv("GEMINI_MODEL", "").strip() or "gemini-2.0-flash"
    redis_url = os.getenv("REDIS_URL", "").strip()
    process_log_pseudonym_key = os.getenv("PROCESS_LOG_PSEUDONYM_KEY", "").strip()
    ai_institution_budgets_usd = os.getenv("AI_INSTITUTION_BUDGETS_USD", "{}").strip()
    ai_model_pricing = os.getenv("AI_MODEL_PRICING", "{}").strip()

    def __init__(self):
        for name in ("llm_base_url", "groq_base_url", "gemini_base_url"):
            value = getattr(self, name)
            parsed = urlsplit(value)
            local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or (parsed.scheme != "https" and not local_http) or parsed.query or parsed.fragment):
                raise ValueError(f"{name} must be an HTTPS base URL without embedded credentials")
        for name in ("llm_model", "groq_model", "gemini_model"):
            value = getattr(self, name)
            if value and any(character.isspace() for character in value):
                raise ValueError(f"{name} must not contain whitespace")


settings = Settings()
