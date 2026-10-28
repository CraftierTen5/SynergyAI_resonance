"""Environment based settings. Secrets are never included in status responses."""
import os


class Settings:
    environment = os.getenv("APP_ENV", "development").strip().lower()
    database_url = os.getenv("DATABASE_URL", "").strip()
    llm_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    llm_api_key = os.getenv("LLM_API_KEY", "").strip()
    llm_model = os.getenv("LLM_MODEL", "").strip()
    llm_base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()


settings = Settings()
