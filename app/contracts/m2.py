from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


ProviderReadinessState = Literal[
    "NOT_CONFIGURED",
    "DISABLED",
    "CREDENTIAL_PRESENT",
    "CREDENTIAL_MISSING",
    "CREDENTIAL_INVALID_FORMAT",
    "CREDENTIAL_VALIDATION_SKIPPED",
    "CAPABILITY_READY",
    "NEEDS_CREDENTIAL",
    "NEEDS_TEMPLATE",
    "NEEDS_VOICE",
    "NEEDS_MODEL",
    "NEEDS_MODEL_ACCESS",
    "NEEDS_WORKSPACE",
    "BLOCKED_PROVIDER_NOT_CONFIGURED",
    "BLOCKED_CREDENTIAL_INVALID",
    "READY_FOR_EXECUTION_AUTHORIZATION",
    "READY_FOR_FUTURE_EXECUTION",
]

ProviderCapability = Literal[
    "VOICE_GENERATION",
    "AI_HERO_VIDEO",
    "AI_IMAGE_GENERATION",
    "FINAL_ASSEMBLY_RENDER",
    "TEMPLATE_RENDER",
    "CARD_RENDER",
    "THUMBNAIL_COMPOSITION",
    "FREE_VISUAL_FALLBACK",
    "ARCHIVE_STORAGE",
    "READ_ONLY_VERIFICATION_ANALYTICS",
]

ProviderCostEstimateStatus = Literal[
    "ESTIMATE_NOT_AVAILABLE",
    "ESTIMATE_PENDING_PROVIDER_CONFIG",
    "ESTIMATE_REQUIRES_REAL_PROVIDER",
]


class ProviderCredentialStatusRead(BaseModel):
    provider_key: str
    state: ProviderReadinessState
    credential_present: bool = False
    missing_env_keys: list[str] = Field(default_factory=list)
    invalid_env_keys: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    validation_probe_enabled: bool = False
    validation_probe_state: ProviderReadinessState = "CREDENTIAL_VALIDATION_SKIPPED"

    model_config = ConfigDict(extra="forbid")


class ProviderCapabilityMatrixEntryRead(BaseModel):
    provider_key: str
    provider_name: str
    provider_type: str
    capabilities: list[ProviderCapability]
    requires: list[str] = Field(default_factory=list)
    future_execution: str
    no_call_in_m2: bool = True
    allowed_roles: list[str] = Field(default_factory=list)
    blocked_roles: list[str] = Field(default_factory=list)
    limits: dict[str, Any] = Field(default_factory=dict)
    attribution_required: bool | None = None

    model_config = ConfigDict(extra="forbid")


class ProviderCostEstimatePlaceholderRead(BaseModel):
    provider_key: str
    status: ProviderCostEstimateStatus
    amount: float | None = None
    currency: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    no_paid_zero_success: bool = True

    model_config = ConfigDict(extra="forbid")


class ProviderRequestValidationResultRead(BaseModel):
    provider_key: str
    provider_capability: ProviderCapability
    is_valid: bool
    reason_codes: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    invalid_fields: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str
    effective_context_snapshot_id: uuid.UUID | str | None = None
    video_project_id: uuid.UUID | str | None = None
    package_id: uuid.UUID | str | None = None
    cost_estimate: ProviderCostEstimatePlaceholderRead
    human_paid_approval_required: bool = True
    will_execute: bool = False
    no_network_call_made: bool = True

    model_config = ConfigDict(extra="forbid")


class ProviderBoundaryPreflightResultRead(BaseModel):
    provider_key: str
    provider_capability: ProviderCapability
    status: Literal["PASS", "BLOCK"]
    blocked: bool
    reason_codes: list[str] = Field(default_factory=list)
    next_action: str
    human_paid_approval_required: bool = True
    real_call_requested: bool = False
    no_network_call_made: bool = True
    technical_appendix: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderReadinessItemRead(BaseModel):
    provider_key: str
    provider_name: str
    provider_type: str
    configured_provider: str | None = None
    readiness_state: ProviderReadinessState
    credential_status: ProviderCredentialStatusRead
    capability_status: ProviderReadinessState
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    blocker_reason_codes: list[str] = Field(default_factory=list)
    missing_env_keys: list[str] = Field(default_factory=list)
    future_required_next_action: str
    real_network_probe_enabled: bool = False
    no_call_was_made: bool = True
    safe_config: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class IntegrationSettingsReadModel(BaseModel):
    configured_provider_by_role: dict[str, str | None] = Field(default_factory=dict)
    real_network_probe_enabled: bool = False
    provider_real_calls_enabled_by_default: bool = False
    no_provider_network_call_by_default: bool = True
    env_keys: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderReadinessSnapshotM2Read(BaseModel):
    generated_at: AwareDatetime
    snapshot_state: Literal["READY", "PARTIAL", "BLOCKED", "UNKNOWN"]
    providers: list[ProviderReadinessItemRead]
    capability_matrix: list[ProviderCapabilityMatrixEntryRead]
    blocking_items: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    integration_settings: IntegrationSettingsReadModel
    pexels_policy: dict[str, Any] = Field(default_factory=dict)
    real_network_probe_enabled: bool = False
    no_network_calls_made: bool = True

    model_config = ConfigDict(extra="forbid")


class AttributionBlockRead(BaseModel):
    provider: str
    creator_name: str
    creator_url: str
    source_url: str
    license_snapshot_ref: str

    model_config = ConfigDict(extra="forbid")


class ExternalAssetManifestRead(BaseModel):
    provider: str
    asset_id: str
    source_url: str
    creator_name: str
    creator_url: str
    downloaded_at: AwareDatetime | str
    license_snapshot_ref: str
    usage_role: str

    model_config = ConfigDict(extra="forbid")


class ProviderAssetCandidateRead(BaseModel):
    provider: str
    asset_id: str
    source_url: str
    creator_name: str | None = None
    creator_url: str | None = None
    usage_role: str
    attribution_required: bool = True
    manifest_requirements: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
