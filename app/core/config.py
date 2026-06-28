from functools import lru_cache
from decimal import Decimal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


OLLAMA_LOCAL_BASE_URL = "http://localhost:11434"
VEO_GA_MODEL_ID = "veo-3.1-fast-generate-001"
VEO_FORBIDDEN_MODEL_IDS = frozenset(
    {
        "veo-3.1-fast",
        "veo-3.1-fast-generate-preview",
    }
)
VEO_VIDEO_ONLY_MODE = "video_only"
VEO_ALLOWED_DURATION_SECONDS = (4, 6, 8)
VEO_DEFAULT_DURATION_SECONDS = 8
VEO_MAX_DURATION_SECONDS = 8


class Settings(BaseSettings):
    app_name: str = "VCOS"
    environment: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://vcos:vcos@localhost:55432/vcos"
    )
    log_level: str = "INFO"
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("VCOS_CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGINS"),
    )
    ollama_base_url: str = Field(
        default=OLLAMA_LOCAL_BASE_URL,
        validation_alias=AliasChoices("OLLAMA_BASE_URL", "VCOS_OLLAMA_BASE_URL"),
    )
    llm_provider: str = Field(
        default="ollama",
        validation_alias=AliasChoices("VCOS_LLM_PROVIDER", "LLM_PROVIDER"),
    )
    llm_real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_LLM_REAL_EXECUTION_ENABLED", "LLM_REAL_EXECUTION_ENABLED"),
    )
    llm_router_real_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_LLM_ROUTER_REAL_SMOKE", "LLM_ROUTER_REAL_SMOKE"),
    )
    production_prompt_activation_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION", "ENABLE_PRODUCTION_PROMPT_ACTIVATION"),
    )
    real_llm_package_run_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_ENABLE_REAL_LLM_PACKAGE_RUN", "ENABLE_REAL_LLM_PACKAGE_RUN"),
    )
    media_provider_calls_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("VCOS_DISABLE_MEDIA_PROVIDER_CALLS", "DISABLE_MEDIA_PROVIDER_CALLS"),
    )
    upload_and_publish_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("VCOS_DISABLE_UPLOAD_AND_PUBLISH", "DISABLE_UPLOAD_AND_PUBLISH"),
    )
    old_provider_smoke_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("VCOS_DISABLE_OLD_PROVIDER_SMOKE", "DISABLE_OLD_PROVIDER_SMOKE"),
    )
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "VCOS_ELEVENLABS_API_KEY"),
    )
    voice_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VOICE_PROVIDER", "VOICE_PROVIDER"),
    )
    elevenlabs_plan: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_ELEVENLABS_PLAN", "ELEVENLABS_PLAN"),
    )
    elevenlabs_monthly_cap_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_ELEVENLABS_MONTHLY_CAP_USD", "ELEVENLABS_MONTHLY_CAP_USD"),
    )
    elevenlabs_monthly_credit_cap: int | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP", "ELEVENLABS_MONTHLY_CREDIT_CAP"),
    )
    elevenlabs_budget_basis: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_ELEVENLABS_BUDGET_BASIS", "ELEVENLABS_BUDGET_BASIS"),
    )
    elevenlabs_real_account_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_ELEVENLABS_REAL_ACCOUNT_SMOKE", "ELEVENLABS_REAL_ACCOUNT_SMOKE"),
    )
    creatomate_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_API_KEY", "VCOS_CREATOMATE_API_KEY"),
    )
    render_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_RENDER_PROVIDER", "RENDER_PROVIDER"),
    )
    cloud_final_renderer_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CLOUD_FINAL_RENDERER_PROVIDER", "VCOS_CLOUD_FINAL_RENDERER_PROVIDER"),
    )
    creatomate_plan: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_PLAN", "VCOS_CREATOMATE_PLAN"),
    )
    creatomate_monthly_credits: int | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_MONTHLY_CREDITS", "VCOS_CREATOMATE_MONTHLY_CREDITS"),
    )
    creatomate_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("CREATOMATE_MONTHLY_BUDGET_USD", "VCOS_CREATOMATE_MONTHLY_BUDGET_USD"),
    )
    creatomate_real_account_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_CREATOMATE_REAL_ACCOUNT_SMOKE", "CREATOMATE_REAL_ACCOUNT_SMOKE"),
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
    youtube_test_video_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_TEST_VIDEO_ID", "VCOS_YOUTUBE_TEST_VIDEO_ID"),
    )
    youtube_real_public_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_YOUTUBE_REAL_PUBLIC_SMOKE", "YOUTUBE_REAL_PUBLIC_SMOKE"),
    )
    youtube_real_owner_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_YOUTUBE_REAL_OWNER_SMOKE", "YOUTUBE_REAL_OWNER_SMOKE"),
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
    dashboard_auth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_DASHBOARD_AUTH_ENABLED", "DASHBOARD_AUTH_ENABLED"),
    )
    auth_mode: str = Field(
        default="local_password",
        validation_alias=AliasChoices("VCOS_AUTH_MODE", "AUTH_MODE"),
    )
    bootstrap_admin_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_EMAIL"),
    )
    bootstrap_admin_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_BOOTSTRAP_ADMIN_PASSWORD", "BOOTSTRAP_ADMIN_PASSWORD"),
    )
    bootstrap_admin_role: str = Field(
        default="OWNER_ADMIN",
        validation_alias=AliasChoices("VCOS_BOOTSTRAP_ADMIN_ROLE", "BOOTSTRAP_ADMIN_ROLE"),
    )
    auth_session_ttl_hours: int = Field(
        default=24,
        validation_alias=AliasChoices("VCOS_AUTH_SESSION_TTL_HOURS", "AUTH_SESSION_TTL_HOURS"),
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
    veo_model_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_VEO_MODEL_ID", "VEO_MODEL_ID", "VCOS_VEO_MODEL", "VEO_MODEL"),
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
    veo_cost_per_second_1080p_video_only: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_VEO_COST_PER_SECOND_1080P_VIDEO_ONLY",
            "VEO_COST_PER_SECOND_1080P_VIDEO_ONLY",
            "VCOS_VEO_COST_PER_SECOND_1080P",
            "VEO_COST_PER_SECOND_1080P",
        ),
    )
    veo_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_VEO_MONTHLY_CAP_USD",
            "VEO_MONTHLY_CAP_USD",
            "VCOS_VEO_MONTHLY_BUDGET_USD",
            "VEO_MONTHLY_BUDGET_USD",
        ),
    )
    veo_real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_VEO_REAL_EXECUTION_ENABLED", "VEO_REAL_EXECUTION_ENABLED"),
    )
    veo_real_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices("VCOS_VEO_REAL_SMOKE", "VEO_REAL_SMOKE"),
    )
    budget_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_BUDGET_MODE", "BUDGET_MODE"),
    )
    monthly_ai_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_MONTHLY_AI_BUDGET_USD", "MONTHLY_AI_BUDGET_USD"),
    )
    llm_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_LLM_MONTHLY_BUDGET_USD", "LLM_MONTHLY_BUDGET_USD"),
    )
    llm_budget_note: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_LLM_BUDGET_NOTE", "LLM_BUDGET_NOTE"),
    )
    stock_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_STOCK_MONTHLY_BUDGET_USD", "STOCK_MONTHLY_BUDGET_USD"),
    )
    music_sfx_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_MUSIC_SFX_MONTHLY_BUDGET_USD", "MUSIC_SFX_MONTHLY_BUDGET_USD"),
    )
    extra_ai_image_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD", "EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VCOS_",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_allowed_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

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
        "bootstrap_admin_password",
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
        "youtube_oauth_client_secrets_file",
        "youtube_oauth_client_id",
        "youtube_oauth_redirect_uri",
        "youtube_test_video_id",
        "auth_mode",
        "bootstrap_admin_email",
        "bootstrap_admin_role",
        "ollama_base_url",
        "llm_provider",
        "voice_provider",
        "elevenlabs_plan",
        "elevenlabs_budget_basis",
        "render_provider",
        "cloud_final_renderer_provider",
        "creatomate_plan",
        "budget_mode",
        "llm_budget_note",
        "veo_model_id",
        "veo_mode",
        "veo_resolution",
        mode="before",
    )
    @classmethod
    def empty_string_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator(
        "elevenlabs_monthly_cap_usd",
        "elevenlabs_monthly_credit_cap",
        "creatomate_monthly_credits",
        "creatomate_monthly_budget_usd",
        "veo_audio_enabled",
        "veo_default_duration_seconds",
        "veo_max_duration_seconds",
        "veo_cost_per_second_1080p_video_only",
        "veo_monthly_budget_usd",
        "monthly_ai_budget_usd",
        "llm_monthly_budget_usd",
        "stock_monthly_budget_usd",
        "music_sfx_monthly_budget_usd",
        "extra_ai_image_monthly_budget_usd",
        mode="before",
    )
    @classmethod
    def empty_optional_value_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("veo_model_id")
    @classmethod
    def veo_model_id_must_be_ga(cls, value: str | None) -> str | None:
        if value in VEO_FORBIDDEN_MODEL_IDS:
            raise ValueError("VCOS_VEO_MODEL_ID must use the GA model id veo-3.1-fast-generate-001")
        return value

    @property
    def veo_model(self) -> str | None:
        return self.veo_model_id

    @property
    def veo_cost_per_second_1080p(self) -> Decimal | None:
        return self.veo_cost_per_second_1080p_video_only


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
