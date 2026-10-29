from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime settings. Secrets are never exposed in reprs/logs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    groq_api_key: SecretStr | None = Field(default=None, validation_alias="GROQ_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    process_log_pseudonym_key: SecretStr | None = Field(
        default=None, validation_alias="PROCESS_LOG_PSEUDONYM_KEY"
    )

    groq_base_url: str = Field(
        default="https://api.groq.com/openai/v1", validation_alias="GROQ_BASE_URL"
    )
    groq_model: str = Field(default="llama-3.3-70b-versatile", validation_alias="GROQ_MODEL")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        validation_alias="GEMINI_BASE_URL",
    )
    gemini_model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")

    ai_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    ai_read_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    ai_max_retries: int = Field(default=3, ge=0, le=10)
    ai_retry_base_seconds: float = Field(default=0.25, gt=0, le=10)
    ai_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    ai_circuit_recovery_seconds: float = Field(default=30.0, gt=0, le=3600)
    ai_response_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    ai_log_stream_maxlen: int = Field(default=100_000, ge=1, le=10_000_000)

    preprocess_max_text_length: int = Field(default=100_000, ge=1, le=500_000, validation_alias="PREPROCESS_MAX_TEXT_LENGTH")
    preprocess_max_image_bytes: int = Field(default=5_000_000, ge=1, le=20_000_000, validation_alias="PREPROCESS_MAX_IMAGE_BYTES")
    preprocess_max_images: int = Field(default=5, ge=1, le=10, validation_alias="PREPROCESS_MAX_IMAGES")

    default_rate_limit_requests: int = Field(default=60, ge=1, le=100_000)
    default_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)

    @field_validator("groq_base_url", "gemini_base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        value = value.rstrip("/")
        parsed = urlsplit(value)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or (parsed.scheme != "https" and not local_http)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("provider endpoints must use HTTPS (HTTP is allowed for localhost)")
        return value

    @field_validator("groq_model", "gemini_model")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ch.isspace() for ch in value):
            raise ValueError("model name must be a non-empty token without whitespace")
        return value

    @field_validator("process_log_pseudonym_key")
    @classmethod
    def validate_pseudonym_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("PROCESS_LOG_PSEUDONYM_KEY must contain at least 32 bytes")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
