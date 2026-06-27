from functools import lru_cache
from decimal import Decimal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


OLLAMA_LOCAL_BASE_URL = "http://localhost:11434"


class Settings(BaseSettings):
    app_name: str = "VCOS"
    environment: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://vcos:vcos@localhost:55432/vcos"
    )
    log_level: str = "INFO"
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "VCOS_ELEVENLABS_API_KEY"),
    )
    creatomate_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_API_KEY", "VCOS_CREATOMATE_API_KEY"),
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
    youtube_public_monitor_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("YOUTUBE_PUBLIC_MONITOR_ENABLED", "VCOS_YOUTUBE_PUBLIC_MONITOR_ENABLED"),
    )
    youtube_data_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_DATA_API_KEY", "VCOS_YOUTUBE_DATA_API_KEY"),
    )
    youtube_owner_analytics_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("YOUTUBE_OWNER_ANALYTICS_ENABLED", "VCOS_YOUTUBE_OWNER_ANALYTICS_ENABLED"),
    )
    youtube_oauth_client_secrets_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_OAUTH_CLIENT_SECRETS_FILE", "VCOS_YOUTUBE_OAUTH_CLIENT_SECRETS_FILE"),
    )
    youtube_oauth_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_OAUTH_CLIENT_ID", "VCOS_YOUTUBE_OAUTH_CLIENT_ID"),
    )
    youtube_oauth_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_OAUTH_CLIENT_SECRET", "VCOS_YOUTUBE_OAUTH_CLIENT_SECRET"),
    )
    youtube_oauth_redirect_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_OAUTH_REDIRECT_URI", "VCOS_YOUTUBE_OAUTH_REDIRECT_URI"),
    )
    youtube_oauth_scopes: str = Field(
        default="https://www.googleapis.com/auth/youtube.readonly,https://www.googleapis.com/auth/yt-analytics.readonly",
        validation_alias=AliasChoices("YOUTUBE_OAUTH_SCOPES", "VCOS_YOUTUBE_OAUTH_SCOPES"),
    )
    google_drive_offload_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("GOOGLE_DRIVE_OFFLOAD_ENABLED", "VCOS_GOOGLE_DRIVE_OFFLOAD_ENABLED"),
    )
    google_drive_oauth_client_secrets_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE", "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE"),
    )
    google_drive_oauth_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_ID"),
    )
    google_drive_oauth_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_SECRET"),
    )
    google_drive_oauth_redirect_uri: str | None = Field(
        default="http://localhost:8000/auth/google-drive/callback",
        validation_alias=AliasChoices("GOOGLE_DRIVE_OAUTH_REDIRECT_URI", "VCOS_GOOGLE_DRIVE_OAUTH_REDIRECT_URI"),
    )
    google_drive_oauth_scopes: str = Field(
        default="https://www.googleapis.com/auth/drive.file",
        validation_alias=AliasChoices("GOOGLE_DRIVE_OAUTH_SCOPES", "VCOS_GOOGLE_DRIVE_OAUTH_SCOPES"),
    )
    google_drive_root_folder_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_DRIVE_ROOT_FOLDER_ID", "VCOS_GOOGLE_DRIVE_ROOT_FOLDER_ID"),
    )
    google_drive_upload_mode: str = Field(
        default="resumable",
        validation_alias=AliasChoices("GOOGLE_DRIVE_UPLOAD_MODE", "VCOS_GOOGLE_DRIVE_UPLOAD_MODE"),
    )
    delete_local_after_drive_upload: bool = Field(
        default=True,
        validation_alias=AliasChoices("VCOS_DELETE_LOCAL_AFTER_DRIVE_UPLOAD", "DELETE_LOCAL_AFTER_DRIVE_UPLOAD"),
    )
    local_media_max_age_hours: int = Field(
        default=24,
        validation_alias=AliasChoices("VCOS_LOCAL_MEDIA_MAX_AGE_HOURS", "LOCAL_MEDIA_MAX_AGE_HOURS"),
    )
    local_media_max_storage_gb: int = Field(
        default=20,
        validation_alias=AliasChoices("VCOS_LOCAL_MEDIA_MAX_STORAGE_GB", "LOCAL_MEDIA_MAX_STORAGE_GB"),
    )
    drive_real_upload_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_DRIVE_REAL_UPLOAD_SMOKE", "DRIVE_REAL_UPLOAD_SMOKE"),
    )
    ai_hero_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_AI_HERO_PROVIDER", "AI_HERO_PROVIDER"),
    )
    google_cloud_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_CLOUD_PROJECT_ID", "VCOS_GOOGLE_CLOUD_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"),
    )
    google_cloud_location: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_CLOUD_LOCATION", "VCOS_GOOGLE_CLOUD_LOCATION"),
    )
    google_application_credentials: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_APPLICATION_CREDENTIALS", "VCOS_GOOGLE_APPLICATION_CREDENTIALS"),
    )
    veo_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_MODEL", "VEO_MODEL"),
    )
    veo_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_MODE", "VEO_MODE"),
    )
    veo_resolution: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_RESOLUTION", "VEO_RESOLUTION"),
    )
    veo_audio_enabled: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_AUDIO_ENABLED", "VEO_AUDIO_ENABLED"),
    )
    veo_default_duration_seconds: int | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_DEFAULT_DURATION_SECONDS", "VEO_DEFAULT_DURATION_SECONDS"),
    )
    veo_max_duration_seconds: int | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_MAX_DURATION_SECONDS", "VEO_MAX_DURATION_SECONDS"),
    )
    veo_cost_per_second_1080p: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_COST_PER_SECOND_1080P", "VEO_COST_PER_SECOND_1080P"),
    )
    veo_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_MONTHLY_BUDGET_USD", "VEO_MONTHLY_BUDGET_USD"),
    )
    veo_real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_VEO_REAL_EXECUTION_ENABLED", "VEO_REAL_EXECUTION_ENABLED"),
    )
    veo_real_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_VEO_REAL_SMOKE", "VEO_REAL_SMOKE"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VCOS_",
        extra="ignore",
    )

    @property
    def ollama_base_url(self) -> str:
        return OLLAMA_LOCAL_BASE_URL

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("VCOS_DATABASE_URL must be a PostgreSQL URL")
        return value

    @field_validator(
        "elevenlabs_api_key",
        "creatomate_api_key",
        "cloud_final_renderer_api_key",
        "pexels_api_key",
        "pixabay_api_key",
        "youtube_data_api_key",
        "youtube_oauth_client_secret",
        "google_drive_oauth_client_secret",
        mode="before",
    )
    @classmethod
    def empty_secret_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "ai_hero_provider",
        "google_cloud_project_id",
        "google_cloud_location",
        "google_application_credentials",
        "google_drive_oauth_client_secrets_file",
        "google_drive_oauth_client_id",
        "google_drive_oauth_redirect_uri",
        "google_drive_root_folder_id",
        "google_drive_upload_mode",
        "veo_model",
        "veo_mode",
        "veo_resolution",
        mode="before",
    )
    @classmethod
    def empty_string_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
