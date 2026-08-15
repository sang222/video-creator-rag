from decimal import Decimal
from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENAI_RESPONSES_BASE_URL = "https://api.openai.com/v1"
VEO_DEFAULT_MODEL_ID = "veo-3.1-fast-generate-preview"
VEO_APPROVED_MODEL_IDS = (
    "veo-3.1-generate-preview",
    "veo-3.1-fast-generate-preview",
    "veo-3.1-lite-generate-preview",
)
VEO_FORBIDDEN_MODEL_IDS = frozenset(
    {
        "veo-3.0-generate-001",
        "veo-3.0-fast-generate-001",
        "veo-2.0-generate-001",
    }
)
VEO_ALLOWED_DURATION_SECONDS = (8,)
VEO_DEFAULT_DURATION_SECONDS = 8
VEO_MAX_DURATION_SECONDS = 8
VEO_DEFAULT_RESOLUTION = "720p"
VEO_DEFAULT_ASPECT_RATIO = "16:9"
VEO_DEFAULT_OUTPUT_COUNT = 1

GEMINI_IMAGE_DEFAULT_MODEL_ID = "gemini-3.1-flash-image"
GEMINI_IMAGE_APPROVED_MODEL_IDS = (GEMINI_IMAGE_DEFAULT_MODEL_ID,)
GEMINI_IMAGE_SUPPORTED_SIZES = ("1K", "2K", "4K")
GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS = ("16:9", "1:1")
GEMINI_IMAGE_DEFAULT_SIZE = "2K"
GEMINI_IMAGE_DEFAULT_ASPECT_RATIO = "16:9"
GEMINI_IMAGE_MAX_OUTPUTS = 1
GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE = 1
GEMINI_IMAGE_MINIMUM_EFFECTIVE_RESOLUTION = "1080p"


class Settings(BaseSettings):
    app_name: str = "VCOS"
    environment: str = "local"
    database_url: str = Field(
        default="postgresql+psycopg://vcos:vcos@localhost:55432/vcos"
    )
    log_level: str = "INFO"
    cors_allowed_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices(
            "VCOS_CORS_ALLOWED_ORIGINS", "CORS_ALLOWED_ORIGINS"
        ),
    )
    openai_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_API_KEY", "VCOS_OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(
        default=OPENAI_RESPONSES_BASE_URL,
        validation_alias=AliasChoices("OPENAI_BASE_URL", "VCOS_OPENAI_BASE_URL"),
    )
    openai_timeout_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices(
            "VCOS_OPENAI_TIMEOUT_SECONDS", "OPENAI_TIMEOUT_SECONDS"
        ),
    )
    # Synchronous Responses calls still use this legacy timeout.  Long-running
    # script qualification never does: it submits a Background response and
    # polls the durable response id with bounded individual requests.
    openai_background_submit_timeout_seconds: int = Field(
        default=15,
        ge=1,
        validation_alias=AliasChoices(
            "VCOS_OPENAI_BACKGROUND_SUBMIT_TIMEOUT_SECONDS",
            "OPENAI_BACKGROUND_SUBMIT_TIMEOUT_SECONDS",
        ),
    )
    openai_background_poll_request_timeout_seconds: int = Field(
        default=10,
        ge=1,
        validation_alias=AliasChoices(
            "VCOS_OPENAI_BACKGROUND_POLL_REQUEST_TIMEOUT_SECONDS",
            "OPENAI_BACKGROUND_POLL_REQUEST_TIMEOUT_SECONDS",
        ),
    )
    script_qualification_background_poll_seconds: int = Field(
        default=15,
        ge=1,
        validation_alias="VCOS_SCRIPT_QUALIFICATION_BACKGROUND_POLL_SECONDS",
    )
    script_qualification_background_poll_cycles_per_stage: int = Field(
        default=60,
        ge=1,
        validation_alias=(
            "VCOS_SCRIPT_QUALIFICATION_BACKGROUND_POLL_CYCLES_PER_STAGE"
        ),
    )
    script_qualification_background_queue_latency_seconds_per_stage: int = Field(
        default=60,
        ge=0,
        validation_alias=(
            "VCOS_SCRIPT_QUALIFICATION_BACKGROUND_QUEUE_LATENCY_SECONDS_PER_STAGE"
        ),
    )
    script_qualification_background_safety_buffer_seconds: int = Field(
        default=300,
        ge=0,
        validation_alias=(
            "VCOS_SCRIPT_QUALIFICATION_BACKGROUND_SAFETY_BUFFER_SECONDS"
        ),
    )
    script_qualification_downstream_lead_seconds: int = Field(
        default=10800,
        ge=0,
        validation_alias="VCOS_SCRIPT_QUALIFICATION_DOWNSTREAM_LEAD_SECONDS",
    )
    llm_provider: str = Field(
        default="openai",
        validation_alias=AliasChoices("VCOS_LLM_PROVIDER", "LLM_PROVIDER"),
    )
    llm_real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_LLM_REAL_EXECUTION_ENABLED", "LLM_REAL_EXECUTION_ENABLED"
        ),
    )
    llm_router_real_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_LLM_ROUTER_REAL_SMOKE", "LLM_ROUTER_REAL_SMOKE"
        ),
    )
    production_prompt_activation_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION",
            "ENABLE_PRODUCTION_PROMPT_ACTIVATION",
        ),
    )
    real_llm_package_run_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_ENABLE_REAL_LLM_PACKAGE_RUN", "ENABLE_REAL_LLM_PACKAGE_RUN"
        ),
    )
    real_openai_agent_run_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_ENABLE_REAL_OPENAI_AGENT_RUN", "ENABLE_REAL_OPENAI_AGENT_RUN"
        ),
    )
    media_provider_calls_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "VCOS_DISABLE_MEDIA_PROVIDER_CALLS", "DISABLE_MEDIA_PROVIDER_CALLS"
        ),
    )
    upload_and_publish_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "VCOS_DISABLE_UPLOAD_AND_PUBLISH", "DISABLE_UPLOAD_AND_PUBLISH"
        ),
    )
    old_provider_smoke_disabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "VCOS_DISABLE_OLD_PROVIDER_SMOKE", "DISABLE_OLD_PROVIDER_SMOKE"
        ),
    )
    native_render_workspace_root: str = Field(
        default="var/tmp/native_renderer",
        validation_alias=AliasChoices(
            "VCOS_NATIVE_RENDER_WORKSPACE_ROOT", "NATIVE_RENDER_WORKSPACE_ROOT"
        ),
    )
    local_project_workspace_root: str = Field(
        default="var/tmp/vcos-project-workspaces",
        validation_alias=AliasChoices(
            "VCOS_LOCAL_PROJECT_WORKSPACE_ROOT", "LOCAL_PROJECT_WORKSPACE_ROOT"
        ),
    )
    native_ffmpeg_local_smoke_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED",
    )
    native_ffmpeg_production_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED",
    )
    controlled_memory_retrieval_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "CONTROLLED_MEMORY_RETRIEVAL_ENABLED",
            "VCOS_CONTROLLED_MEMORY_RETRIEVAL_ENABLED",
        ),
    )
    vector_retrieval_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VECTOR_RETRIEVAL_ENABLED", "VCOS_VECTOR_RETRIEVAL_ENABLED"
        ),
    )
    vector_provider: str | None = Field(
        default="json",
        validation_alias=AliasChoices("VECTOR_PROVIDER", "VCOS_VECTOR_PROVIDER"),
    )
    embedding_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "EMBEDDING_EXECUTION_ENABLED", "VCOS_EMBEDDING_EXECUTION_ENABLED"
        ),
    )
    elevenlabs_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ELEVENLABS_API_KEY", "VCOS_ELEVENLABS_API_KEY"),
    )
    elevenlabs_voice_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ELEVENLABS_VOICE_ID", "VCOS_ELEVENLABS_VOICE_ID"
        ),
    )
    elevenlabs_model_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ELEVENLABS_MODEL_ID", "VCOS_ELEVENLABS_MODEL_ID"
        ),
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
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_MONTHLY_CAP_USD", "ELEVENLABS_MONTHLY_CAP_USD"
        ),
    )
    elevenlabs_monthly_credit_cap: int | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_MONTHLY_CREDIT_CAP", "ELEVENLABS_MONTHLY_CREDIT_CAP"
        ),
    )
    elevenlabs_budget_basis: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_BUDGET_BASIS", "ELEVENLABS_BUDGET_BASIS"
        ),
    )
    # These are intentionally optional.  A real paid narration route cannot
    # infer a per-character or forced-alignment charge from a monthly cap; the
    # current reviewed values must be explicitly configured and then frozen in
    # CombinedReplacementBudgetAuthority before the first provider request.
    elevenlabs_tts_cost_per_character_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_TTS_COST_PER_CHARACTER_USD",
            "ELEVENLABS_TTS_COST_PER_CHARACTER_USD",
        ),
    )
    elevenlabs_forced_alignment_cost_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_FORCED_ALIGNMENT_COST_USD",
            "ELEVENLABS_FORCED_ALIGNMENT_COST_USD",
        ),
    )
    elevenlabs_real_account_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_ELEVENLABS_REAL_ACCOUNT_SMOKE", "ELEVENLABS_REAL_ACCOUNT_SMOKE"
        ),
    )
    pexels_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PEXELS_API_KEY", "VCOS_PEXELS_API_KEY"),
    )
    free_visual_fallback_provider: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "FREE_VISUAL_FALLBACK_PROVIDER", "VCOS_FREE_VISUAL_FALLBACK_PROVIDER"
        ),
    )
    pexels_attribution_required: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PEXELS_ATTRIBUTION_REQUIRED", "VCOS_PEXELS_ATTRIBUTION_REQUIRED"
        ),
    )
    pexels_max_clips_per_long: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "PEXELS_MAX_CLIPS_PER_LONG", "VCOS_PEXELS_MAX_CLIPS_PER_LONG"
        ),
    )
    pexels_max_runtime_pct_per_long: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "PEXELS_MAX_RUNTIME_PCT_PER_LONG", "VCOS_PEXELS_MAX_RUNTIME_PCT_PER_LONG"
        ),
    )
    pexels_max_same_asset_reuse_per_30_days: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "PEXELS_MAX_SAME_ASSET_REUSE_PER_30_DAYS",
            "VCOS_PEXELS_MAX_SAME_ASSET_REUSE_PER_30_DAYS",
        ),
    )
    pixabay_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("PIXABAY_API_KEY", "VCOS_PIXABAY_API_KEY"),
    )
    youtube_public_monitor_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "YOUTUBE_PUBLIC_MONITOR_ENABLED", "VCOS_YOUTUBE_PUBLIC_MONITOR_ENABLED"
        ),
    )
    youtube_data_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_DATA_API_KEY", "VCOS_YOUTUBE_DATA_API_KEY"
        ),
    )
    youtube_owner_analytics_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "YOUTUBE_OWNER_ANALYTICS_ENABLED", "VCOS_YOUTUBE_OWNER_ANALYTICS_ENABLED"
        ),
    )
    youtube_oauth_client_secrets_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_OAUTH_CLIENT_SECRETS_FILE",
            "VCOS_YOUTUBE_OAUTH_CLIENT_SECRETS_FILE",
        ),
    )
    youtube_oauth_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_OAUTH_CLIENT_ID", "VCOS_YOUTUBE_OAUTH_CLIENT_ID"
        ),
    )
    youtube_oauth_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_OAUTH_CLIENT_SECRET", "VCOS_YOUTUBE_OAUTH_CLIENT_SECRET"
        ),
    )
    youtube_oauth_redirect_uri: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_OAUTH_REDIRECT_URI", "VCOS_YOUTUBE_OAUTH_REDIRECT_URI"
        ),
    )
    youtube_oauth_scopes: str = Field(
        default="https://www.googleapis.com/auth/youtube.readonly,https://www.googleapis.com/auth/yt-analytics.readonly",
        validation_alias=AliasChoices(
            "YOUTUBE_OAUTH_SCOPES", "VCOS_YOUTUBE_OAUTH_SCOPES"
        ),
    )
    youtube_test_video_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "YOUTUBE_TEST_VIDEO_ID", "VCOS_YOUTUBE_TEST_VIDEO_ID"
        ),
    )
    youtube_real_public_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_YOUTUBE_REAL_PUBLIC_SMOKE", "YOUTUBE_REAL_PUBLIC_SMOKE"
        ),
    )
    youtube_real_owner_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_YOUTUBE_REAL_OWNER_SMOKE", "YOUTUBE_REAL_OWNER_SMOKE"
        ),
    )
    google_drive_offload_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OFFLOAD_ENABLED", "VCOS_GOOGLE_DRIVE_OFFLOAD_ENABLED"
        ),
    )
    google_drive_archive_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_ARCHIVE_ENABLED", "VCOS_GOOGLE_DRIVE_ARCHIVE_ENABLED"
        ),
    )
    google_drive_oauth_client_secrets_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE",
            "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_SECRETS_FILE",
        ),
    )
    google_drive_oauth_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OAUTH_CLIENT_ID", "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_ID"
        ),
    )
    google_drive_oauth_client_secret: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "VCOS_GOOGLE_DRIVE_OAUTH_CLIENT_SECRET"
        ),
    )
    google_drive_oauth_redirect_uri: str | None = Field(
        default="http://localhost:8000/auth/google-drive/callback",
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OAUTH_REDIRECT_URI", "VCOS_GOOGLE_DRIVE_OAUTH_REDIRECT_URI"
        ),
    )
    google_drive_oauth_scopes: str = Field(
        default="https://www.googleapis.com/auth/drive.file",
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_OAUTH_SCOPES", "VCOS_GOOGLE_DRIVE_OAUTH_SCOPES"
        ),
    )
    google_drive_root_folder_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_ROOT_FOLDER_ID", "VCOS_GOOGLE_DRIVE_ROOT_FOLDER_ID"
        ),
    )
    google_drive_upload_mode: str = Field(
        default="resumable",
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_UPLOAD_MODE", "VCOS_GOOGLE_DRIVE_UPLOAD_MODE"
        ),
    )
    delete_local_after_drive_upload: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "VCOS_DELETE_LOCAL_AFTER_DRIVE_UPLOAD", "DELETE_LOCAL_AFTER_DRIVE_UPLOAD"
        ),
    )
    local_media_max_age_hours: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "VCOS_LOCAL_MEDIA_MAX_AGE_HOURS", "LOCAL_MEDIA_MAX_AGE_HOURS"
        ),
    )
    local_media_max_storage_gb: int = Field(
        default=20,
        validation_alias=AliasChoices(
            "VCOS_LOCAL_MEDIA_MAX_STORAGE_GB", "LOCAL_MEDIA_MAX_STORAGE_GB"
        ),
    )
    drive_real_upload_smoke: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_DRIVE_REAL_UPLOAD_SMOKE", "DRIVE_REAL_UPLOAD_SMOKE"
        ),
    )
    dashboard_auth_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "VCOS_DASHBOARD_AUTH_ENABLED", "DASHBOARD_AUTH_ENABLED"
        ),
    )
    auth_mode: str = Field(
        default="local_password",
        validation_alias=AliasChoices("VCOS_AUTH_MODE", "AUTH_MODE"),
    )
    bootstrap_admin_email: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_BOOTSTRAP_ADMIN_EMAIL", "BOOTSTRAP_ADMIN_EMAIL"
        ),
    )
    bootstrap_admin_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_BOOTSTRAP_ADMIN_PASSWORD", "BOOTSTRAP_ADMIN_PASSWORD"
        ),
    )
    bootstrap_admin_role: str = Field(
        default="OWNER_ADMIN",
        validation_alias=AliasChoices(
            "VCOS_BOOTSTRAP_ADMIN_ROLE", "BOOTSTRAP_ADMIN_ROLE"
        ),
    )
    auth_session_ttl_hours: int = Field(
        default=24,
        validation_alias=AliasChoices(
            "VCOS_AUTH_SESSION_TTL_HOURS", "AUTH_SESSION_TTL_HOURS"
        ),
    )
    ai_video_hero_provider: str = Field(
        default="google_veo",
        validation_alias="VCOS_AI_VIDEO_HERO_PROVIDER",
    )
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="GEMINI_API_KEY",
    )
    gemini_image_real_generation_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_GEMINI_IMAGE_REAL_GENERATION_ENABLED",
    )
    img1_fixture_only: bool = Field(
        default=True,
        validation_alias="VCOS_IMG1_FIXTURE_ONLY",
    )
    gemini_image_provider_route_approved: bool = Field(
        default=True,
        validation_alias="VCOS_GEMINI_IMAGE_PROVIDER_ROUTE_APPROVED",
    )
    gemini_image_model_id: str = Field(
        default=GEMINI_IMAGE_DEFAULT_MODEL_ID,
        validation_alias="GEMINI_IMAGE_MODEL_ID",
    )
    gemini_image_default_size: str = Field(
        default=GEMINI_IMAGE_DEFAULT_SIZE,
        validation_alias="GEMINI_IMAGE_DEFAULT_SIZE",
    )
    gemini_image_default_aspect_ratio: str = Field(
        default=GEMINI_IMAGE_DEFAULT_ASPECT_RATIO,
        validation_alias="GEMINI_IMAGE_DEFAULT_ASPECT_RATIO",
    )
    gemini_image_max_outputs: int = Field(
        default=GEMINI_IMAGE_MAX_OUTPUTS,
        ge=1,
        le=GEMINI_IMAGE_MAX_OUTPUTS,
        validation_alias="GEMINI_IMAGE_MAX_OUTPUTS",
    )
    gemini_image_max_attempts_per_scene: int = Field(
        default=GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE,
        ge=1,
        le=GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE,
        validation_alias="GEMINI_IMAGE_MAX_ATTEMPTS_PER_SCENE",
    )
    veo_model_id: str = Field(
        default=VEO_DEFAULT_MODEL_ID,
        validation_alias="VEO_MODEL_ID",
    )
    veo_default_duration_seconds: int = Field(
        default=VEO_DEFAULT_DURATION_SECONDS,
        validation_alias="VEO_DEFAULT_DURATION_SECONDS",
    )
    veo_default_resolution: str = Field(
        default=VEO_DEFAULT_RESOLUTION,
        validation_alias="VEO_DEFAULT_RESOLUTION",
    )
    veo_default_aspect_ratio: str = Field(
        default=VEO_DEFAULT_ASPECT_RATIO,
        validation_alias="VEO_DEFAULT_ASPECT_RATIO",
    )
    veo_default_output_count: int = Field(
        default=VEO_DEFAULT_OUTPUT_COUNT,
        validation_alias="VEO_DEFAULT_OUTPUT_COUNT",
    )
    provider_real_execution_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PROVIDER_REAL_EXECUTION_ENABLED", "VCOS_PROVIDER_REAL_EXECUTION_ENABLED"
        ),
    )
    provider_production_execution_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_PROVIDER_PRODUCTION_EXECUTION_ENABLED",
    )
    pexels_real_execution_enabled: bool = Field(
        default=False,
        validation_alias="PEXELS_REAL_EXECUTION_ENABLED",
    )
    elevenlabs_real_execution_enabled: bool = Field(
        default=False,
        validation_alias="ELEVENLABS_REAL_EXECUTION_ENABLED",
    )
    elevenlabs_real_generation_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "ELEVENLABS_REAL_GENERATION_ENABLED",
            "VCOS_ELEVENLABS_REAL_GENERATION_ENABLED",
        ),
    )
    elevenlabs_forced_alignment_permission_confirmed: bool | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED",
            "VCOS_ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED",
        ),
    )
    veo_real_generation_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_VEO_REAL_GENERATION_ENABLED",
    )
    pa1r_veo_smoke_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_PA1R_VEO_SMOKE_ENABLED",
    )
    cqr1_paid_canary_enabled: bool = Field(
        default=False,
        validation_alias="VCOS_CQR1_PAID_CANARY_ENABLED",
    )
    pexels_real_search_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PEXELS_REAL_SEARCH_ENABLED", "VCOS_PEXELS_REAL_SEARCH_ENABLED"
        ),
    )
    google_drive_real_archive_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED",
            "VCOS_GOOGLE_DRIVE_REAL_ARCHIVE_ENABLED",
        ),
    )
    provider_real_readiness_probe_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PROVIDER_REAL_READINESS_PROBE_ENABLED",
            "VCOS_PROVIDER_REAL_READINESS_PROBE_ENABLED",
        ),
    )
    budget_mode: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_BUDGET_MODE", "BUDGET_MODE"),
    )
    monthly_ai_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_MONTHLY_AI_BUDGET_USD", "MONTHLY_AI_BUDGET_USD"
        ),
    )
    llm_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_LLM_MONTHLY_BUDGET_USD", "LLM_MONTHLY_BUDGET_USD"
        ),
    )
    llm_budget_note: str | None = Field(
        default=None,
        validation_alias=AliasChoices("VCOS_LLM_BUDGET_NOTE", "LLM_BUDGET_NOTE"),
    )
    stock_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_STOCK_MONTHLY_BUDGET_USD", "STOCK_MONTHLY_BUDGET_USD"
        ),
    )
    music_sfx_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_MUSIC_SFX_MONTHLY_BUDGET_USD", "MUSIC_SFX_MONTHLY_BUDGET_USD"
        ),
    )
    extra_ai_image_monthly_budget_usd: Decimal | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "VCOS_EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD",
            "EXTRA_AI_IMAGE_MONTHLY_BUDGET_USD",
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VCOS_",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_allowed_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_postgres(cls, value: str) -> str:
        if not value.startswith("postgresql"):
            raise ValueError("VCOS_DATABASE_URL must be a PostgreSQL URL")
        return value

    @field_validator(
        "elevenlabs_api_key",
        "openai_api_key",
        "gemini_api_key",
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
        "openai_base_url",
        "llm_provider",
        "vector_provider",
        "voice_provider",
        "elevenlabs_voice_id",
        "elevenlabs_model_id",
        "elevenlabs_plan",
        "elevenlabs_budget_basis",
        "free_visual_fallback_provider",
        "budget_mode",
        "llm_budget_note",
        "ai_video_hero_provider",
        "gemini_image_model_id",
        "gemini_image_default_size",
        "gemini_image_default_aspect_ratio",
        "veo_model_id",
        "veo_default_resolution",
        "veo_default_aspect_ratio",
        mode="before",
    )
    @classmethod
    def empty_string_must_be_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("llm_provider")
    @classmethod
    def llm_provider_must_be_openai(cls, value: str | None) -> str:
        if value is None or value.strip().lower() != "openai":
            raise ValueError("VCOS_LLM_PROVIDER must be openai")
        return "openai"

    @field_validator(
        "elevenlabs_monthly_cap_usd",
        "elevenlabs_monthly_credit_cap",
        "veo_default_duration_seconds",
        "veo_default_output_count",
        "pexels_max_clips_per_long",
        "pexels_max_runtime_pct_per_long",
        "pexels_max_same_asset_reuse_per_30_days",
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
    def veo_model_id_must_be_approved(cls, value: str) -> str:
        if value not in VEO_APPROVED_MODEL_IDS or value in VEO_FORBIDDEN_MODEL_IDS:
            raise ValueError(
                "VEO_MODEL_ID must be present in the approved Veo 3.1 model catalog"
            )
        return value

    @field_validator("gemini_image_model_id")
    @classmethod
    def gemini_image_model_id_must_be_approved(cls, value: str) -> str:
        if value not in GEMINI_IMAGE_APPROVED_MODEL_IDS:
            raise ValueError(
                "GEMINI_IMAGE_MODEL_ID must be present in the approved image model catalog"
            )
        return value

    @field_validator("gemini_image_default_size")
    @classmethod
    def gemini_image_size_must_be_supported(cls, value: str) -> str:
        if value not in GEMINI_IMAGE_SUPPORTED_SIZES:
            raise ValueError("GEMINI_IMAGE_DEFAULT_SIZE is unsupported")
        return value

    @field_validator("gemini_image_default_aspect_ratio")
    @classmethod
    def gemini_image_aspect_ratio_must_be_supported(cls, value: str) -> str:
        if value not in GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS:
            raise ValueError("GEMINI_IMAGE_DEFAULT_ASPECT_RATIO is unsupported")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
