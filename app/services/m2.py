from __future__ import annotations

# Compatibility note: semantic facade `provider_wiring` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
from typing import Any

from app.contracts.m2 import (
    IntegrationSettingsReadModel,
    ProviderBoundaryPreflightResultRead,
    ProviderCapability,
    ProviderCapabilityMatrixEntryRead,
    ProviderCostEstimatePlaceholderRead,
    ProviderCredentialStatusRead,
    ProviderReadinessItemRead,
    ProviderReadinessSnapshotM2Read,
    ProviderRequestValidationResultRead,
)
from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.services.r3d3 import stable_hash
from app.services.provider_stack import normalize_provider_key, provider_key_rejection_reasons


LOCKED_PROVIDERS = {
    "VOICE_PROVIDER": "elevenlabs",
    "AI_VIDEO_HERO_PROVIDER": "google_veo",
    "FINAL_ASSEMBLY_RENDERER": "native_ffmpeg_renderer",
    "TEMPLATE_RENDERER": "native_ffmpeg_renderer",
    "FREE_VISUAL_FALLBACK_PROVIDER": "pexels_api",
}

PAID_CAPABILITIES = {
    "VOICE_GENERATION",
    "AI_HERO_VIDEO",
    "FINAL_ASSEMBLY_RENDER",
    "TEMPLATE_RENDER",
    "CARD_RENDER",
    "THUMBNAIL_COMPOSITION",
    "SHORT_RENDER",
}

PEXELS_ALLOWED_ROLES = ["background_visual", "short_broll", "thumbnail_background", "mood_support"]
PEXELS_BLOCKED_ROLES = [
    "factual_evidence",
    "fake_testimonial",
    "implied_endorsement",
    "core_visual_backbone",
    "recurring_host_identity",
    "every_scene_default_stock",
]
PEXELS_REQUIRED_MANIFEST = [
    "provider",
    "asset_id",
    "source_url",
    "creator_name",
    "creator_url",
    "downloaded_at",
    "license_snapshot_ref",
    "usage_role",
]


class ProviderConfigRegistry:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def configured_provider_by_role(self) -> dict[str, str | None]:
        return {
            "VOICE_PROVIDER": _norm(self.settings.voice_provider) or LOCKED_PROVIDERS["VOICE_PROVIDER"],
            "AI_VIDEO_HERO_PROVIDER": _norm(self.settings.ai_video_hero_provider) or LOCKED_PROVIDERS["AI_VIDEO_HERO_PROVIDER"],
            "FINAL_ASSEMBLY_RENDERER": LOCKED_PROVIDERS["FINAL_ASSEMBLY_RENDERER"],
            "TEMPLATE_RENDERER": LOCKED_PROVIDERS["TEMPLATE_RENDERER"],
            "FREE_VISUAL_FALLBACK_PROVIDER": _norm(self.settings.free_visual_fallback_provider)
            or LOCKED_PROVIDERS["FREE_VISUAL_FALLBACK_PROVIDER"],
            "GOOGLE_DRIVE_ARCHIVE": "google_drive" if self.settings.google_drive_archive_enabled else None,
            "YOUTUBE": "read_only_verification_analytics",
        }

    def integration_settings(self) -> IntegrationSettingsReadModel:
        return IntegrationSettingsReadModel(
            configured_provider_by_role=self.configured_provider_by_role(),
            real_network_probe_enabled=self.settings.provider_real_readiness_probe_enabled,
            provider_real_calls_enabled_by_default=False,
            no_provider_network_call_by_default=True,
            env_keys={
                "elevenlabs": ["VOICE_PROVIDER", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "ELEVENLABS_MODEL_ID"],
                "google_veo": ["VCOS_AI_VIDEO_HERO_PROVIDER", "GEMINI_API_KEY", "VEO_MODEL_ID", "VEO_DEFAULT_DURATION_SECONDS", "VEO_DEFAULT_RESOLUTION", "VEO_DEFAULT_ASPECT_RATIO", "VEO_DEFAULT_OUTPUT_COUNT", "VCOS_VEO_REAL_GENERATION_ENABLED", "VCOS_PA1R_VEO_SMOKE_ENABLED"],
                "native_ffmpeg_renderer": ["VCOS_NATIVE_RENDER_WORKSPACE_ROOT", "VCOS_NATIVE_FFMPEG_LOCAL_SMOKE_ENABLED", "VCOS_NATIVE_FFMPEG_PRODUCTION_ENABLED"],
                "pexels_api": ["FREE_VISUAL_FALLBACK_PROVIDER", "PEXELS_API_KEY", "PEXELS_ATTRIBUTION_REQUIRED", "PEXELS_MAX_CLIPS_PER_LONG", "PEXELS_MAX_RUNTIME_PCT_PER_LONG", "PEXELS_MAX_SAME_ASSET_REUSE_PER_30_DAYS"],
                "google_drive_archive": ["GOOGLE_DRIVE_ARCHIVE_ENABLED", "GOOGLE_DRIVE_ROOT_FOLDER_ID"],
                "youtube_readonly": ["YOUTUBE_PUBLIC_MONITOR_ENABLED", "YOUTUBE_OWNER_ANALYTICS_ENABLED"],
            },
        )


class ProviderCapabilityMatrix:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def entries(self) -> list[ProviderCapabilityMatrixEntryRead]:
        pexels_limits = _pexels_limits(self.settings)
        return [
            ProviderCapabilityMatrixEntryRead(
                provider_key="elevenlabs",
                provider_name="ElevenLabs",
                provider_type="VOICE_PROVIDER",
                capabilities=["VOICE_GENERATION"],
                requires=["API key", "voice id", "model id"],
                future_execution="R3D8 only",
                no_call_in_m2=True,
            ),
            ProviderCapabilityMatrixEntryRead(
                provider_key="google_veo",
                provider_name="Google Veo API",
                provider_type="AI_VIDEO_HERO_PROVIDER",
                capabilities=["AI_HERO_VIDEO"],
                requires=["Gemini API key", "approved Veo 3.1 model", "8 seconds", "one output"],
                future_execution="R3D8 only",
                no_call_in_m2=True,
                limits={"allowed_durations_seconds": [8], "resolutions": ["720p", "1080p", "4k"], "aspect_ratios": ["16:9", "9:16"], "output_count": 1},
            ),
            ProviderCapabilityMatrixEntryRead(
                provider_key="native_ffmpeg_renderer",
                provider_name="NativeFFmpegRenderer",
                provider_type="LOCAL_RENDERER_CAPABILITY",
                capabilities=["FINAL_ASSEMBLY_RENDER", "TEMPLATE_RENDER", "CARD_RENDER", "SHORT_RENDER"],
                requires=["ffmpeg-full", "approved render plan", "local execution boundary"],
                future_execution="local renderer; production disabled by default",
                no_call_in_m2=True,
            ),
            ProviderCapabilityMatrixEntryRead(
                provider_key="pexels_api",
                provider_name="Pexels API",
                provider_type="FREE_VISUAL_FALLBACK_PROVIDER",
                capabilities=["FREE_VISUAL_FALLBACK"],
                requires=["API key", "asset manifest", "attribution block when required"],
                future_execution="R3D8 only",
                no_call_in_m2=True,
                allowed_roles=list(PEXELS_ALLOWED_ROLES),
                blocked_roles=list(PEXELS_BLOCKED_ROLES),
                limits=pexels_limits,
                attribution_required=self.settings.pexels_attribution_required,
            ),
            ProviderCapabilityMatrixEntryRead(
                provider_key="google_drive_archive",
                provider_name="Google Drive Archive",
                provider_type="ARCHIVE_STORAGE",
                capabilities=["ARCHIVE_STORAGE"],
                requires=["archive flag", "root folder id", "connected Drive auth in later phase"],
                future_execution="storage/archive only; no source of truth",
                no_call_in_m2=True,
            ),
            ProviderCapabilityMatrixEntryRead(
                provider_key="youtube_readonly",
                provider_name="YouTube read-only verification/analytics",
                provider_type="READ_ONLY_VERIFICATION_ANALYTICS",
                capabilities=["READ_ONLY_VERIFICATION_ANALYTICS"],
                requires=["read-only API/OAuth config when used"],
                future_execution="read-only verification/analytics only; no upload",
                no_call_in_m2=True,
            ),
        ]

    def by_provider(self) -> dict[str, ProviderCapabilityMatrixEntryRead]:
        return {entry.provider_key: entry for entry in self.entries()}


class ProviderCostEstimatePlaceholder:
    def for_provider(self, provider_key: str, *, provider_configured: bool) -> ProviderCostEstimatePlaceholderRead:
        if not provider_configured:
            return ProviderCostEstimatePlaceholderRead(
                provider_key=provider_key,
                status="ESTIMATE_PENDING_PROVIDER_CONFIG",
                reason_codes=["PROVIDER_NOT_CONFIGURED_FOR_COST_ESTIMATE"],
            )
        return ProviderCostEstimatePlaceholderRead(
            provider_key=provider_key,
            status="ESTIMATE_REQUIRES_REAL_PROVIDER",
            reason_codes=["REAL_PROVIDER_PRICING_NOT_AVAILABLE_IN_M2"],
        )


class ProviderEnvValidator:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.registry = ProviderConfigRegistry(self.settings)
        self.matrix = ProviderCapabilityMatrix(self.settings).by_provider()

    def validate(self) -> list[ProviderReadinessItemRead]:
        role = self.registry.configured_provider_by_role()
        return [
            self._elevenlabs(role),
            self._google_veo(role),
            self._pexels(role),
            self._drive_archive(role),
            self._youtube_readonly(),
        ]

    def _elevenlabs(self, role: dict[str, str | None]) -> ProviderReadinessItemRead:
        selected = role["VOICE_PROVIDER"]
        provider_mismatch = selected != "elevenlabs"
        missing = _missing(
            ("ELEVENLABS_API_KEY", _secret_present(self.settings.elevenlabs_api_key)),
            ("ELEVENLABS_VOICE_ID", _configured(self.settings.elevenlabs_voice_id)),
            ("ELEVENLABS_MODEL_ID", _configured(self.settings.elevenlabs_model_id)),
        )
        reason_codes = _missing_codes(missing, "ELEVENLABS")
        if provider_mismatch:
            reason_codes.append("VOICE_PROVIDER_NOT_ELEVENLABS")
        return self._item(
            provider_key="elevenlabs",
            configured_provider=selected,
            selected_ok=not provider_mismatch,
            credential_present=_secret_present(self.settings.elevenlabs_api_key),
            missing_env_keys=missing,
            reason_codes=reason_codes,
            readiness_state=_readiness_from_missing(
                missing,
                all_missing_state="NOT_CONFIGURED",
                priority=[("ELEVENLABS_API_KEY", "CREDENTIAL_MISSING"), ("ELEVENLABS_VOICE_ID", "NEEDS_VOICE"), ("ELEVENLABS_MODEL_ID", "NEEDS_MODEL")],
            ),
            next_action="Cấu hình ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID và ELEVENLABS_MODEL_ID; M2 vẫn không sinh voice.",
            safe_config={
                "api_key_configured": _secret_present(self.settings.elevenlabs_api_key),
                "voice_id_configured": bool(self.settings.elevenlabs_voice_id),
                "model_id_configured": bool(self.settings.elevenlabs_model_id),
            },
        )

    def _google_veo(self, role: dict[str, str | None]) -> ProviderReadinessItemRead:
        selected = role["AI_VIDEO_HERO_PROVIDER"]
        provider_mismatch = selected != "google_veo"
        missing = _missing(
            ("GEMINI_API_KEY", _secret_present(self.settings.gemini_api_key)),
            ("VEO_MODEL_ID", _configured(self.settings.veo_model_id)),
        )
        reason_codes = _missing_codes(missing, "VEO")
        if provider_mismatch:
            reason_codes.append("AI_VIDEO_HERO_PROVIDER_NOT_GOOGLE_VEO")
        readiness = _readiness_from_missing(
            missing,
            all_missing_state="NOT_CONFIGURED",
            priority=[("GEMINI_API_KEY", "CREDENTIAL_MISSING"), ("VEO_MODEL_ID", "NEEDS_MODEL")],
        )
        return self._item(
            provider_key="google_veo",
            configured_provider=selected,
            selected_ok=not provider_mismatch,
            credential_present=_secret_present(self.settings.gemini_api_key),
            missing_env_keys=missing,
            reason_codes=reason_codes,
            readiness_state=readiness,
            next_action="Cấu hình GEMINI_API_KEY; giữ execution/smoke disabled cho tới PA1R được phê duyệt.",
            safe_config={
                "credential_configured": _secret_present(self.settings.gemini_api_key),
                "credential_value_redacted": True,
                "model_configured": bool(self.settings.veo_model_id),
                "model_catalog_status": "APPROVED_VERSIONED_CATALOG",
                "allowed_durations_seconds": [8],
                "default_duration_seconds": self.settings.veo_default_duration_seconds,
                "default_resolution": self.settings.veo_default_resolution,
                "default_aspect_ratio": self.settings.veo_default_aspect_ratio,
                "default_output_count": self.settings.veo_default_output_count,
                "execution_enabled": self.settings.veo_real_generation_enabled,
                "smoke_enabled": self.settings.pa1r_veo_smoke_enabled,
            },
        )

    def _pexels(self, role: dict[str, str | None]) -> ProviderReadinessItemRead:
        selected = role["FREE_VISUAL_FALLBACK_PROVIDER"]
        provider_mismatch = selected != "pexels_api"
        missing = _missing(("PEXELS_API_KEY", _secret_present(self.settings.pexels_api_key)))
        reason_codes = _missing_codes(missing, "PEXELS")
        if provider_mismatch:
            reason_codes.append("FREE_VISUAL_FALLBACK_PROVIDER_NOT_PEXELS_API")
        return self._item(
            provider_key="pexels_api",
            configured_provider=selected,
            selected_ok=not provider_mismatch,
            credential_present=_secret_present(self.settings.pexels_api_key),
            missing_env_keys=missing,
            reason_codes=reason_codes,
            readiness_state=_readiness_from_missing(
                missing,
                all_missing_state="NOT_CONFIGURED",
                priority=[("PEXELS_API_KEY", "CREDENTIAL_MISSING")],
            ),
            next_action="Cấu hình PEXELS_API_KEY nếu muốn dùng fallback ảnh/video miễn phí; M2 không search/download.",
            safe_config={
                "api_key_configured": _secret_present(self.settings.pexels_api_key),
                "attribution_required": self.settings.pexels_attribution_required,
                "limits": _pexels_limits(self.settings),
            },
        )

    def _drive_archive(self, role: dict[str, str | None]) -> ProviderReadinessItemRead:
        if not self.settings.google_drive_archive_enabled:
            return self._item(
                provider_key="google_drive_archive",
                configured_provider=None,
                selected_ok=True,
                credential_present=False,
                missing_env_keys=[],
                reason_codes=["GOOGLE_DRIVE_ARCHIVE_DISABLED"],
                readiness_state="DISABLED",
                next_action="Bật GOOGLE_DRIVE_ARCHIVE_ENABLED và cấu hình root folder ở phase archive sau; M2 không upload Drive.",
                safe_config={"archive_enabled": False, "root_folder_configured": bool(self.settings.google_drive_root_folder_id)},
            )
        missing = _missing(("GOOGLE_DRIVE_ROOT_FOLDER_ID", _configured(self.settings.google_drive_root_folder_id)))
        return self._item(
            provider_key="google_drive_archive",
            configured_provider=role["GOOGLE_DRIVE_ARCHIVE"],
            selected_ok=True,
            credential_present=bool(self.settings.google_drive_root_folder_id),
            missing_env_keys=missing,
            reason_codes=["GOOGLE_DRIVE_ROOT_FOLDER_ID_MISSING"] if missing else [],
            readiness_state="NOT_CONFIGURED" if missing else "READY_FOR_FUTURE_EXECUTION",
            next_action="Google Drive chỉ archive/storage; M2 không upload mặc định.",
            safe_config={"archive_enabled": True, "root_folder_configured": bool(self.settings.google_drive_root_folder_id)},
        )

    def _youtube_readonly(self) -> ProviderReadinessItemRead:
        enabled = self.settings.youtube_public_monitor_enabled or self.settings.youtube_owner_analytics_enabled
        return self._item(
            provider_key="youtube_readonly",
            configured_provider="read_only_verification_analytics" if enabled else None,
            selected_ok=True,
            credential_present=enabled,
            missing_env_keys=[],
            reason_codes=[] if enabled else ["YOUTUBE_READONLY_DISABLED"],
            readiness_state="READY_FOR_FUTURE_EXECUTION" if enabled else "DISABLED",
            next_action="YouTube chỉ read-only verification/analytics; upload API vẫn bị cấm.",
            safe_config={
                "public_monitor_enabled": self.settings.youtube_public_monitor_enabled,
                "owner_analytics_enabled": self.settings.youtube_owner_analytics_enabled,
                "youtube_upload_allowed": False,
            },
        )

    def _item(
        self,
        *,
        provider_key: str,
        configured_provider: str | None,
        selected_ok: bool,
        credential_present: bool,
        missing_env_keys: list[str],
        reason_codes: list[str],
        readiness_state: str,
        next_action: str,
        safe_config: dict[str, Any],
    ) -> ProviderReadinessItemRead:
        matrix = self.matrix[provider_key]
        if not selected_ok and readiness_state not in {"DISABLED"}:
            readiness_state = "BLOCKED_PROVIDER_NOT_CONFIGURED"
        configured = readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION"}
        if not missing_env_keys and selected_ok and readiness_state not in {"DISABLED", "BLOCKED_PROVIDER_NOT_CONFIGURED"}:
            readiness_state = "READY_FOR_HUMAN_PAID_APPROVAL" if any(cap in PAID_CAPABILITIES for cap in matrix.capabilities) else "READY_FOR_FUTURE_EXECUTION"
        credential_state = _credential_state(readiness_state, credential_present, missing_env_keys)
        generic_blocker = "NEEDS_CREDENTIAL" if missing_env_keys and not credential_present else readiness_state
        blockers = sorted(set(reason_codes + ([] if configured or readiness_state == "DISABLED" else [generic_blocker])))
        return ProviderReadinessItemRead(
            provider_key=provider_key,
            provider_name=matrix.provider_name,
            provider_type=matrix.provider_type,
            configured_provider=configured_provider,
            readiness_state=readiness_state,  # type: ignore[arg-type]
            credential_status=ProviderCredentialStatusRead(
                provider_key=provider_key,
                state=credential_state,  # type: ignore[arg-type]
                credential_present=credential_present,
                missing_env_keys=missing_env_keys,
                reason_codes=blockers,
                validation_probe_enabled=self.settings.provider_real_readiness_probe_enabled,
                validation_probe_state="CREDENTIAL_VALIDATION_SKIPPED",
            ),
            capability_status="CAPABILITY_READY" if readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION", "DISABLED"} else readiness_state,  # type: ignore[arg-type]
            capabilities=matrix.capabilities,
            blocker_reason_codes=blockers,
            missing_env_keys=missing_env_keys,
            future_required_next_action=next_action,
            real_network_probe_enabled=self.settings.provider_real_readiness_probe_enabled,
            no_call_was_made=True,
            safe_config=safe_config,
        )


class ProviderReadinessM2Service:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.registry = ProviderConfigRegistry(self.settings)
        self.validator = ProviderEnvValidator(self.settings)
        self.matrix = ProviderCapabilityMatrix(self.settings)

    def snapshot(self) -> ProviderReadinessSnapshotM2Read:
        providers = self.validator.validate()
        blocking = [
            {
                "provider_key": item.provider_key,
                "readiness_state": item.readiness_state,
                "reason_codes": item.blocker_reason_codes,
                "next_action": item.future_required_next_action,
            }
            for item in providers
            if item.readiness_state not in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION", "DISABLED"}
        ]
        return ProviderReadinessSnapshotM2Read(
            generated_at=utc_now(),
            snapshot_state="BLOCKED" if blocking else "READY",
            providers=providers,
            capability_matrix=self.matrix.entries(),
            blocking_items=blocking,
            next_actions=[{"provider_key": item.provider_key, "next_action": item.future_required_next_action} for item in providers],
            integration_settings=self.registry.integration_settings(),
            pexels_policy=pexels_policy(self.settings),
            real_network_probe_enabled=self.settings.provider_real_readiness_probe_enabled,
            no_network_calls_made=True,
        )

    def provider_map(self) -> dict[str, ProviderReadinessItemRead]:
        return {item.provider_key: item for item in self.snapshot().providers}


class ProviderBoundaryPreflight:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.readiness = ProviderReadinessM2Service(self.settings)

    def check(
        self,
        *,
        provider_key: str,
        provider_capability: ProviderCapability,
        payload: dict[str, Any] | None = None,
        human_paid_approval: bool = False,
        real_call_requested: bool = True,
        render_revision_ref: str | None = None,
        cost_estimate_ref: str | None = None,
        paid_attempt_limit_ref: str | None = None,
        usage_metrics: dict[str, Any] | None = None,
    ) -> ProviderBoundaryPreflightResultRead:
        provider_key = _norm(provider_key) or str(provider_key)
        payload = payload or {}
        usage_metrics = usage_metrics or {}
        providers = self.readiness.provider_map()
        provider = providers.get(provider_key)
        reason_codes: list[str] = provider_key_rejection_reasons(provider_key)
        if not reason_codes and (provider is None or provider.readiness_state not in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION"}):
            reason_codes.append("BLOCKED_PROVIDER_NOT_CONFIGURED")
            if provider is not None:
                reason_codes.extend(provider.blocker_reason_codes)
        if real_call_requested:
            reason_codes.append("PROVIDER_REAL_CALL_BLOCKED_IN_M2")
        if provider_capability in PAID_CAPABILITIES:
            if not human_paid_approval:
                reason_codes.append("HUMAN_PAID_APPROVAL_MISSING")
            if not render_revision_ref:
                reason_codes.append("RENDER_REVISION_REQUIRED_R3D8")
            if not cost_estimate_ref:
                reason_codes.append("COST_ESTIMATE_REQUIRED_R3D8")
            if not paid_attempt_limit_ref:
                reason_codes.append("PAID_ATTEMPT_LIMIT_REQUIRED_R3D8")
        reason_codes.extend(self._provider_specific_blocks(provider_key, payload, usage_metrics))
        reason_codes = sorted(set(reason_codes))
        return ProviderBoundaryPreflightResultRead(
            provider_key=provider_key,
            provider_capability=provider_capability,
            status="BLOCK" if reason_codes else "PASS",
            blocked=bool(reason_codes),
            reason_codes=reason_codes,
            next_action="Dừng trước provider boundary; cần cấu hình provider, approval người thật và R3D8 execution ledger." if reason_codes else "Validation-only payload sẵn sàng cho future execution.",
            human_paid_approval_required=provider_capability in PAID_CAPABILITIES,
            real_call_requested=real_call_requested,
            no_network_call_made=True,
            technical_appendix={
                "m2_wiring_only": True,
                "provider_readiness_state": provider.readiness_state if provider else "UNKNOWN",
            },
        )

    def _provider_specific_blocks(self, provider_key: str, payload: dict[str, Any], usage_metrics: dict[str, Any]) -> list[str]:
        codes: list[str] = []
        if provider_key == "pexels_api":
            codes.extend(validate_pexels_policy(payload.get("usage_role"), self.settings, usage_metrics))
        if provider_key == "google_veo" and _int(payload.get("duration_seconds")) != 8:
            codes.append("VEO_DURATION_NOT_ALLOWED")
        if provider_key == "elevenlabs":
            if not payload.get("voice_id"):
                codes.append("ELEVENLABS_VOICE_ID_MISSING")
            if not payload.get("model_id"):
                codes.append("ELEVENLABS_MODEL_ID_MISSING")
        return codes


class _BaseRequestBuilder:
    provider_key: str
    capability: ProviderCapability

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.costs = ProviderCostEstimatePlaceholder()

    def _result(
        self,
        *,
        payload: dict[str, Any],
        required_fields: list[str],
        reason_codes: list[str] | None = None,
        invalid_fields: list[str] | None = None,
        human_paid_approval_required: bool = True,
    ) -> ProviderRequestValidationResultRead:
        missing_fields = [field for field in required_fields if not _present(payload.get(field))]
        invalid_fields = invalid_fields or []
        reason_codes = list(reason_codes or [])
        reason_codes.extend(_request_missing_code(self.provider_key, field) for field in missing_fields)
        provider = ProviderReadinessM2Service(self.settings).provider_map().get(self.provider_key)
        provider_configured = bool(provider and provider.readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION"})
        return ProviderRequestValidationResultRead(
            provider_key=self.provider_key,
            provider_capability=self.capability,
            is_valid=not missing_fields and not invalid_fields and not reason_codes,
            reason_codes=sorted(set(reason_codes)),
            missing_fields=missing_fields,
            invalid_fields=invalid_fields,
            payload=payload,
            idempotency_key=f"idempotency:{stable_hash({'provider': self.provider_key, 'capability': self.capability, 'payload': payload})}",
            effective_context_snapshot_id=payload.get("effective_context_snapshot_id"),
            video_project_id=payload.get("video_project_id"),
            package_id=payload.get("package_id"),
            cost_estimate=self.costs.for_provider(self.provider_key, provider_configured=provider_configured),
            human_paid_approval_required=human_paid_approval_required,
            will_execute=False,
            no_network_call_made=True,
        )


class ElevenLabsVoiceRequestBuilder(_BaseRequestBuilder):
    provider_key = "elevenlabs"
    capability: ProviderCapability = "VOICE_GENERATION"

    def build(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        payload = {
            "provider": self.provider_key,
            "text": data.get("text"),
            "voice_id": data.get("voice_id") or self.settings.elevenlabs_voice_id,
            "model_id": data.get("model_id") or self.settings.elevenlabs_model_id,
            "endpoint_semantics": "CONVERT_WITH_TIMESTAMPS",
            "source_text_hash": data.get("source_text_hash"),
            "spoken_text_hash": data.get("spoken_text_hash"),
            "voice_settings": data.get("voice_settings") or {},
            "seed": data.get("seed"),
            "pronunciation_dictionary_refs": data.get("pronunciation_dictionary_refs") or [],
            "effective_context_snapshot_id": data.get("effective_context_snapshot_id"),
            "video_project_id": data.get("video_project_id"),
            "package_id": data.get("package_id"),
            "provider_capability": self.capability,
        }
        return self._result(payload=payload, required_fields=["text", "voice_id", "model_id"])


class GoogleVeoRequestBuilder(_BaseRequestBuilder):
    provider_key = "google_veo"
    capability: ProviderCapability = "AI_HERO_VIDEO"

    def build(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        duration = _int(data.get("duration_seconds") or self.settings.veo_default_duration_seconds)
        invalid = []
        reason_codes = []
        if duration != 8:
            invalid.append("duration_seconds")
            reason_codes.append("VEO_DURATION_NOT_ALLOWED")
        payload = {
            "provider": self.provider_key,
            "prompt": data.get("prompt"),
            "model": data.get("model") or self.settings.veo_model_id,
            "duration_seconds": duration,
            "video_only": True,
            "effective_context_snapshot_id": data.get("effective_context_snapshot_id"),
            "video_project_id": data.get("video_project_id"),
            "package_id": data.get("package_id"),
            "provider_capability": self.capability,
        }
        return self._result(payload=payload, required_fields=["prompt", "model"], invalid_fields=invalid, reason_codes=reason_codes)


class PexelsSearchRequestBuilder(_BaseRequestBuilder):
    provider_key = "pexels_api"
    capability: ProviderCapability = "FREE_VISUAL_FALLBACK"

    def build(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        payload = {
            "provider": self.provider_key,
            "query": data.get("query"),
            "usage_role": data.get("usage_role"),
            "orientation": data.get("orientation"),
            "effective_context_snapshot_id": data.get("effective_context_snapshot_id"),
            "video_project_id": data.get("video_project_id"),
            "package_id": data.get("package_id"),
            "provider_capability": self.capability,
            "required_manifest": list(PEXELS_REQUIRED_MANIFEST),
        }
        reason_codes = validate_pexels_policy(payload.get("usage_role"), self.settings, data.get("usage_metrics") or {})
        return self._result(payload=payload, required_fields=["query", "usage_role"], reason_codes=reason_codes, human_paid_approval_required=False)


class DriveArchiveRequestBuilder(_BaseRequestBuilder):
    provider_key = "google_drive_archive"
    capability: ProviderCapability = "ARCHIVE_STORAGE"

    def build(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        payload = {
            "provider": self.provider_key,
            "source_ref": data.get("source_ref"),
            "root_folder_id": data.get("root_folder_id") or self.settings.google_drive_root_folder_id,
            "effective_context_snapshot_id": data.get("effective_context_snapshot_id"),
            "video_project_id": data.get("video_project_id"),
            "package_id": data.get("package_id"),
            "provider_capability": self.capability,
        }
        return self._result(payload=payload, required_fields=["source_ref", "root_folder_id"], human_paid_approval_required=False)


class ElevenLabsVoiceAdapter:
    def __init__(self, settings: Settings | None = None):
        self.builder = ElevenLabsVoiceRequestBuilder(settings)
        # Imported lazily to keep the legacy M2 provider boundary dependency-light.
        from app.services.temporal_authority import ElevenLabsTimingResponseParser

        self.timing_parser = ElevenLabsTimingResponseParser()

    def prepare(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        return self.builder.build(data)

    def parse_timing_response(self, **kwargs: Any):
        return self.timing_parser.parse(**kwargs)


class GoogleVeoHeroVideoAdapter:
    def __init__(self, settings: Settings | None = None):
        self.builder = GoogleVeoRequestBuilder(settings)

    def prepare(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        return self.builder.build(data)


class PexelsVisualFallbackAdapter:
    def __init__(self, settings: Settings | None = None):
        self.builder = PexelsSearchRequestBuilder(settings)

    def prepare(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        return self.builder.build(data)


class GoogleDriveArchiveAdapter:
    def __init__(self, settings: Settings | None = None):
        self.builder = DriveArchiveRequestBuilder(settings)

    def prepare(self, data: dict[str, Any]) -> ProviderRequestValidationResultRead:
        return self.builder.build(data)


def validate_pexels_policy(usage_role: Any, settings: Settings | None = None, usage_metrics: dict[str, Any] | None = None) -> list[str]:
    settings = settings or get_settings()
    usage_metrics = usage_metrics or {}
    role = str(usage_role or "")
    codes: list[str] = []
    if role in PEXELS_BLOCKED_ROLES:
        codes.append("PEXELS_USAGE_ROLE_BLOCKED")
    elif role and role not in PEXELS_ALLOWED_ROLES:
        codes.append("PEXELS_USAGE_ROLE_NOT_ALLOWED")
    if _int(usage_metrics.get("clips_per_long")) > settings.pexels_max_clips_per_long:
        codes.append("PEXELS_MAX_CLIPS_EXCEEDED")
    if _int(usage_metrics.get("runtime_pct_per_long")) > settings.pexels_max_runtime_pct_per_long:
        codes.append("PEXELS_RUNTIME_PCT_EXCEEDED")
    if _int(usage_metrics.get("same_asset_reuse_per_30_days")) > settings.pexels_max_same_asset_reuse_per_30_days:
        codes.append("PEXELS_REUSE_LIMIT_EXCEEDED")
    return codes


def pexels_policy(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    return {
        "provider_type": "FREE_VISUAL_FALLBACK_PROVIDER",
        "allowed": list(PEXELS_ALLOWED_ROLES),
        "blocked": list(PEXELS_BLOCKED_ROLES),
        "limits": _pexels_limits(settings),
        "required_manifest": list(PEXELS_REQUIRED_MANIFEST),
    }


def _pexels_limits(settings: Settings) -> dict[str, int]:
    return {
        "max_pexels_runtime_pct_per_long": settings.pexels_max_runtime_pct_per_long,
        "max_pexels_clips_per_long": settings.pexels_max_clips_per_long,
        "max_same_asset_reuse_per_30_days": settings.pexels_max_same_asset_reuse_per_30_days,
    }


def _credential_state(readiness_state: str, credential_present: bool, missing_env_keys: list[str]) -> str:
    if readiness_state == "DISABLED":
        return "DISABLED"
    if not credential_present and missing_env_keys:
        return "NEEDS_CREDENTIAL"
    if not credential_present:
        return "CREDENTIAL_MISSING"
    return "CREDENTIAL_PRESENT"


def _readiness_from_missing(missing: list[str], *, all_missing_state: str, priority: list[tuple[str, str]]) -> str:
    if not missing:
        return "READY_FOR_HUMAN_PAID_APPROVAL"
    if len(missing) == len(priority):
        return all_missing_state
    missing_set = set(missing)
    for field, state in priority:
        if field in missing_set:
            return state
    return "NOT_CONFIGURED"


def _missing(*pairs: tuple[str, bool]) -> list[str]:
    return [key for key, present in pairs if not present]


def _missing_codes(missing_env_keys: list[str], provider_prefix: str) -> list[str]:
    return [f"{key}_MISSING" if key.startswith(provider_prefix) else f"{provider_prefix}_{key}_MISSING" for key in missing_env_keys]


def _request_missing_code(provider_key: str, field: str) -> str:
    prefixes = {
        "google_veo": "VEO",
        "pexels_api": "PEXELS",
        "google_drive_archive": "GOOGLE_DRIVE_ARCHIVE",
        "elevenlabs": "ELEVENLABS",
    }
    return f"{prefixes.get(provider_key, provider_key.upper())}_{field.upper()}_MISSING"


def _secret_present(value: Any) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return bool(str(raw).strip())


def _configured(value: Any) -> bool:
    return bool(str(value or "").strip())


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _norm(value: Any) -> str | None:
    return normalize_provider_key(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
