import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

CloudMediaStorageProvider = Literal["GOOGLE_DRIVE"]
CloudMediaType = Literal[
    "LONG_FORM_FINAL",
    "SHORT_FINAL",
    "THUMBNAIL",
    "CAPTION",
    "AI_HERO",
    "CREATOMATE_ASSET",
    "CHARACTER_REFERENCE",
    "CHARACTER_FACE_REF",
    "CHARACTER_BRANCH",
    "VOICE_REFERENCE",
    "REFERENCE_PACK",
    "PUBLISH_PACKAGE",
    "QC_EXPORT",
    "OTHER",
]
CloudMediaUploadStatus = Literal["PENDING", "UPLOADING", "VERIFIED", "FAILED", "CANCELLED"]
CloudMediaVerificationStatus = Literal["NOT_STARTED", "SIZE_VERIFIED", "CHECKSUM_VERIFIED", "CHECKSUM_UNAVAILABLE", "FAILED"]
LocalCleanupStatus = Literal["NOT_ELIGIBLE", "PENDING", "CLEANED", "SKIPPED", "FAILED"]
MediaOffloadJobState = Literal["PENDING", "UPLOADING", "VERIFIED", "CLEANED_LOCAL", "FAILED", "CANCELLED", "SKIPPED"]
GoogleDriveConnectionState = Literal["NOT_CONFIGURED", "CONFIGURED", "CONNECTED", "NEEDS_REAUTH", "REVOKED", "ERROR"]
GoogleDriveOAuthSessionStatus = Literal["STARTED", "CALLBACK_RECEIVED", "TOKEN_EXCHANGED", "FAILED", "CANCELLED"]
LocalRetentionPolicyState = Literal["ACTIVE", "DISABLED"]


class _ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class GoogleDriveOAuthStartResult(BaseModel):
    oauth_session_id: uuid.UUID
    authorization_url: str

    model_config = ConfigDict(extra="forbid")


class GoogleDriveOAuthSessionRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    redirect_uri: str
    scopes: list[str]
    status: GoogleDriveOAuthSessionStatus
    credential_reference_id: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class GoogleDriveConnectionStatusRead(BaseModel):
    offload_enabled: bool
    config_state: GoogleDriveConnectionState
    connection_state: GoogleDriveConnectionState
    connected: bool
    credential_reference_id: uuid.UUID | None = None
    root_folder_id_configured: bool
    scopes: list[str] = Field(default_factory=list)
    upload_mode: str
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str | None = None

    model_config = ConfigDict(extra="forbid")


class GoogleDriveMediaCredentialRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    credential_reference_id: uuid.UUID
    connection_state: GoogleDriveConnectionState
    scopes: list[str]
    root_folder_id: str | None
    last_health_check_at: AwareDatetime | None
    error_code: str | None
    error_message: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CloudMediaRefRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    render_package_id: uuid.UUID | None
    media_type: CloudMediaType
    storage_provider: CloudMediaStorageProvider
    drive_file_id: str
    drive_folder_id: str | None
    web_view_link: str
    mime_type: str | None
    file_name: str | None
    size_bytes: int | None
    checksum_sha256: str | None
    local_source_path_hash: str | None
    upload_status: CloudMediaUploadStatus
    verification_status: CloudMediaVerificationStatus
    local_cleanup_status: LocalCleanupStatus
    uploaded_at: AwareDatetime | None
    cleaned_at: AwareDatetime | None
    retention_policy: dict[str, Any]
    source_refs: list[dict[str, Any]]
    technical_appendix: dict[str, Any]
    created_at: AwareDatetime
    updated_at: AwareDatetime


class CloudMediaReadPayload(BaseModel):
    cloud_media_ref_id: uuid.UUID
    media_type: CloudMediaType
    file_name: str | None
    storage_provider: Literal["GOOGLE_DRIVE"]
    web_view_link: str
    upload_status: CloudMediaUploadStatus
    verification_status: CloudMediaVerificationStatus
    local_cleanup_status: LocalCleanupStatus
    size_bytes: int | None
    mime_type: str | None
    uploaded_at: AwareDatetime | None
    cleaned_at: AwareDatetime | None
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class MediaOffloadJobCreate(BaseModel):
    company_id: uuid.UUID | None = None
    channel_workspace_id: uuid.UUID | None = None
    video_project_id: uuid.UUID | None = None
    uploaded_video_id: uuid.UUID | None = None
    source_media_ref_id: uuid.UUID | None = None
    render_package_id: uuid.UUID | None = None
    local_source_path: str | None = None
    target_media_type: CloudMediaType
    target_folder_policy: dict[str, Any] = Field(default_factory=dict)
    keep_local: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("local_source_path")
    @classmethod
    def empty_path_is_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


class MediaOffloadExecuteRequest(BaseModel):
    local_source_path: str | None = None
    keep_local: bool = False

    model_config = ConfigDict(extra="forbid")

    @field_validator("local_source_path")
    @classmethod
    def empty_path_is_none(cls, value: str | None) -> str | None:
        if value == "":
            return None
        return value


class MediaOffloadJobRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    video_project_id: uuid.UUID | None
    uploaded_video_id: uuid.UUID | None
    source_media_ref_id: uuid.UUID | None
    render_package_id: uuid.UUID | None
    local_source_path_hash: str | None
    target_provider: Literal["GOOGLE_DRIVE"]
    target_folder_policy: dict[str, Any]
    target_media_type: CloudMediaType
    job_state: MediaOffloadJobState
    cloud_media_ref_id: uuid.UUID | None
    retry_count: int
    error_code: str | None
    error_message: str | None
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LocalMediaRetentionPolicyRead(_ReadModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    channel_workspace_id: uuid.UUID | None
    keep_local_after_upload: bool
    cleanup_after_verified: bool
    max_local_age_hours: int | None
    max_local_storage_gb: int | None
    protected_paths: list[str]
    allowed_cleanup_roots: list[str]
    state: LocalRetentionPolicyState
    created_at: AwareDatetime
    updated_at: AwareDatetime


class LocalCleanupRunRequest(BaseModel):
    dry_run: bool = False

    model_config = ConfigDict(extra="forbid")


class LocalCleanupRunResult(BaseModel):
    scanned: int
    cleaned: int
    skipped: int
    failed: int
    reason_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
