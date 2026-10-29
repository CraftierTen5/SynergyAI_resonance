from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Data residency: production data stays in an India region unless an institution
# has explicitly approved another region (NFR-PRV-05).
INDIA_DATA_REGIONS = frozenset({"ap-south-1", "ap-south-2", "in-central-1", "in-west-1"})


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

    # Enables unauthenticated, server-owned demo fixtures for local pipeline checks.
    local_placeholder_enabled: bool = Field(
        default=False, validation_alias="LOCAL_PLACEHOLDER_ENABLED"
    )

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

    # Data layer: durable relational store, cache/stream store and analytics store.
    postgres_dsn: SecretStr | None = Field(default=None, validation_alias="POSTGRES_DSN")
    postgres_pool_min_size: int = Field(default=1, ge=1, le=100, validation_alias="POSTGRES_POOL_MIN_SIZE")
    postgres_pool_max_size: int = Field(default=10, ge=1, le=500, validation_alias="POSTGRES_POOL_MAX_SIZE")
    postgres_command_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, validation_alias="POSTGRES_COMMAND_TIMEOUT_SECONDS"
    )
    postgres_statement_cache_size: int = Field(
        default=100, ge=0, le=10_000, validation_alias="POSTGRES_STATEMENT_CACHE_SIZE"
    )
    redis_dsn: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_DSN")
    redis_socket_timeout_seconds: float = Field(
        default=5.0, gt=0, le=120, validation_alias="REDIS_SOCKET_TIMEOUT_SECONDS"
    )
    data_region: str = Field(default="ap-south-1", validation_alias="DATA_REGION")
    allow_non_india_region: bool = Field(default=False, validation_alias="ALLOW_NON_INDIA_REGION")

    # Raw process data is buffered briefly in Redis and never persisted verbatim.
    process_log_buffer_ttl_seconds: int = Field(
        default=900, ge=30, le=86_400, validation_alias="PROCESS_LOG_BUFFER_TTL_SECONDS"
    )
    session_ttl_seconds: int = Field(default=12 * 60 * 60, ge=60, le=30 * 24 * 60 * 60,
                                     validation_alias="SESSION_TTL_SECONDS")
    policy_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400,
                                          validation_alias="POLICY_CACHE_TTL_SECONDS")

    # Retention defaults applied per institution unless overridden (BRL-10).
    process_data_retention_days: int = Field(default=365, ge=1, le=3_650,
                                             validation_alias="PROCESS_DATA_RETENTION_DAYS")
    assessment_retention_days: int = Field(default=1_825, ge=1, le=7_300,
                                           validation_alias="ASSESSMENT_RETENTION_DAYS")
    audit_retention_days: int = Field(default=2_555, ge=365, le=18_250,
                                      validation_alias="AUDIT_RETENTION_DAYS")

    # Aggregates below this cohort size are never released (DL-9).
    analytics_min_cohort_size: int = Field(default=5, ge=2, le=1_000,
                                           validation_alias="ANALYTICS_MIN_COHORT_SIZE")

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

    @field_validator("redis_dsn")
    @classmethod
    def validate_redis_dsn(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("REDIS_DSN must be a redis:// or rediss:// URL")
        if parsed.scheme != "rediss" and not local:
            raise ValueError("REDIS_DSN must use rediss:// outside localhost")
        return value

    @model_validator(mode="after")
    def validate_data_layer(self) -> "Settings":
        if self.postgres_pool_min_size > self.postgres_pool_max_size:
            raise ValueError("POSTGRES_POOL_MIN_SIZE must not exceed POSTGRES_POOL_MAX_SIZE")
        if self.data_region not in INDIA_DATA_REGIONS and not self.allow_non_india_region:
            raise ValueError(
                "DATA_REGION must be an India region unless ALLOW_NON_INDIA_REGION is set"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
