from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "VCOS"
    environment: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://vcos:vcos@localhost:55432/vcos"
    )
    log_level: str = "INFO"
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "VCOS_OLLAMA_BASE_URL"),
    )
    ollama_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OLLAMA_API_KEY", "VCOS_OLLAMA_API_KEY"),
    )
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "VCOS_ELEVENLABS_API_KEY"),
    )
    creatomate_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_API_KEY", "VCOS_CREATOMATE_API_KEY"),
    )
    cinematic_ai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CINEMATIC_AI_API_KEY", "VCOS_CINEMATIC_AI_API_KEY"),
    )
    cloud_final_renderer_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUD_FINAL_RENDERER_API_KEY", "VCOS_CLOUD_FINAL_RENDERER_API_KEY"),
    )
    pexels_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PEXELS_API_KEY", "VCOS_PEXELS_API_KEY"),
    )
    pixabay_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PIXABAY_API_KEY", "VCOS_PIXABAY_API_KEY"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VCOS_",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("VCOS_DATABASE_URL must be a PostgreSQL URL")
        return value

    @field_validator(
        "ollama_api_key",
        "elevenlabs_api_key",
        "creatomate_api_key",
        "cinematic_ai_api_key",
        "cloud_final_renderer_api_key",
        "pexels_api_key",
        "pixabay_api_key",
        mode="before",
    )
    @classmethod
    def empty_secret_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
