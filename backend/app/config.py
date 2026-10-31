"""Application settings, read from environment variables (and a local .env file).

Secrets such as GEMINI_API_KEY live only here on the backend, never in the frontend.
The key is a SecretStr, so it never appears in repr(), logs or error messages.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVIDERS = ("sarvam", "gemini", "mock")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "NafaPilot API"
    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+psycopg://nafapilot:nafapilot@localhost:5432/nafapilot"

    # LLM provider, or a comma-separated fallback chain tried in order, e.g. "sarvam,gemini".
    # "mock" works with no key; "gemini" needs GEMINI_API_KEY + GEMINI_MODEL; "sarvam" needs
    # SARVAM_API_KEY + SARVAM_MODEL.
    llm_provider: str = "mock"
    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None
    sarvam_api_key: SecretStr | None = None
    sarvam_model: str | None = "sarvam-105b"
    # Sarvam reasoning effort ("none", "low", "high", "max"); empty = the model's default.
    sarvam_reasoning_effort: str | None = "low"
    sarvam_base_url: str = "https://api.sarvam.ai"
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=2048, ge=64, le=8192)

    # Comma-separated list of origins allowed to call the API (the Next.js app).
    cors_origins: str = "http://localhost:3000"

    # Synthetic dataset folder. Empty = <project root>/data/synthetic.
    data_dir: str | None = None

    @property
    def resolved_data_dir(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir).expanduser().resolve()
        return Path(__file__).resolve().parents[2] / "data" / "synthetic"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("llm_provider")
    @classmethod
    def _valid_chain(cls, v: str) -> str:
        names = [n.strip().lower() for n in v.split(",") if n.strip()]
        bad = [n for n in names if n not in PROVIDERS]
        if not names or bad:
            raise ValueError(
                f"LLM_PROVIDER must be one or more of {', '.join(PROVIDERS)}, comma-separated"
            )
        if len(set(names)) != len(names):
            raise ValueError("LLM_PROVIDER lists a provider twice")
        return ",".join(names)

    @property
    def llm_provider_chain(self) -> list[str]:
        return self.llm_provider.split(",")

    @property
    def sarvam_key_value(self) -> str | None:
        """The raw key, for the Sarvam client only. Empty strings count as missing."""
        if self.sarvam_api_key is None:
            return None
        value = self.sarvam_api_key.get_secret_value().strip()
        return value or None

    @property
    def gemini_key_value(self) -> str | None:
        """The raw key, for the Gemini client only. Empty strings count as missing."""
        if self.gemini_api_key is None:
            return None
        value = self.gemini_api_key.get_secret_value().strip()
        return value or None

    @property
    def secret_values(self) -> list[str]:
        """Every secret the log redactor must scrub."""
        return [k for k in (self.gemini_key_value, self.sarvam_key_value) if k]


@lru_cache
def get_settings() -> Settings:
    return Settings()
