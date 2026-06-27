from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session
import yaml

from app.contracts import (
    AIHeroAssetPlanRequest,
    AIHeroGenerationExecuteRequest,
    AIHeroGenerationJobRead,
    CreatomateRenderAssetPlanRequest,
    FinalMediaRefCreate,
    LicenseEvidenceGateCheckRequest,
    LicenseEvidenceGateRead,
    LongFormRenderPackageCreate,
    MediaProviderBudgetCheckRequest,
    MediaProviderBudgetGateRead,
    MediaQCGateCheckRequest,
    MediaQCGateRead,
    MediaRenderRoutingDecisionRequest,
    ProviderCapabilityGateCheckRequest,
    ProviderCapabilityGateRead,
    ReusedContentRiskGateCheckRequest,
    ReusedContentRiskGateRead,
    ShortRenderPackageCreate,
    ThumbnailVariantPlanRequest,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import (
    AIHeroAsset,
    CreatomateRenderAsset,
    FinalMediaRef,
    LicenseEvidenceRecord,
    LongFormRenderPackage,
    MediaProviderBudgetPolicy,
    MediaProviderBudgetSnapshot,
    MediaProviderRoleProfile,
    MediaQCReport,
    MediaRenderRoutingDecision,
    ProviderCapabilityMatrixEntry,
    ShortCandidate,
    ShortRenderPackage,
    ThumbnailVariant,
    VideoProject,
)
from app.providers.google_vertex_veo import (
    GoogleVertexVeoExecutionConfig,
    GoogleVertexVeoProvider,
    GoogleVertexVeoRequest,
)


WORKFLOW_ORCHESTRATOR = "WORKFLOW_ORCHESTRATOR"
LLM_SCRIPT_ENGINE = "LLM_SCRIPT_ENGINE"
API_NATIVE_TTS = "API_NATIVE_TTS"
CAPTION_TIMELINE_ENGINE = "CAPTION_TIMELINE_ENGINE"
AI_VIDEO_HERO_PROVIDER = "AI_VIDEO_HERO_PROVIDER"
CLOUD_TEMPLATE_RENDERER_LIGHT = "CLOUD_TEMPLATE_RENDERER_LIGHT"
CLOUD_FINAL_ASSEMBLY_RENDERER = "CLOUD_FINAL_ASSEMBLY_RENDERER"
MEDIA_STORAGE = "MEDIA_STORAGE"
MEDIA_QC_ENGINE = "MEDIA_QC_ENGINE"
PUBLISH_PACKAGE_BUILDER = "PUBLISH_PACKAGE_BUILDER"
API_NATIVE_STOCK_PROVIDER = "API_NATIVE_STOCK_PROVIDER"
FREE_FALLBACK_PROVIDER = "FREE_FALLBACK_PROVIDER"
MOCK_PROVIDER = "MOCK_PROVIDER"
DEFERRED_MANUAL_LIBRARY = "DEFERRED_MANUAL_LIBRARY"

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
GOOGLE_VERTEX_VEO_PROVIDER_KEY = "GOOGLE_VERTEX_VEO"

LONG_FORM_FINAL_RENDER = "LONG_FORM_FINAL_RENDER"
VOICE_JOBS = {"VOICE_GENERATION", "LONG_VOICE_GENERATION", "SHORT_VOICE_GENERATION"}
CREATOMATE_LIGHT_JOBS = {
    "THUMBNAIL_RENDER",
    "SHORT_RENDER",
    "TITLE_CARD_RENDER",
    "DIAGRAM_CARD_RENDER",
    "STAT_CARD_RENDER",
    "LOWER_THIRD_RENDER",
    "HERO_COMPOSITION_RENDER",
    "PREVIEW_CLIP_RENDER",
}
AI_HERO_JOBS = {"AI_HERO_GENERATION", "AI_METAPHOR_GENERATION"}
MEDIA_JOB_TYPES = {
    "TOPIC_DECISION",
    "LONG_SCRIPT_GENERATION",
    "LONG_VOICE_GENERATION",
    "LONG_CAPTION_TIMELINE",
    "LONG_VISUAL_PLAN",
    "AI_HERO_GENERATION",
    "AI_METAPHOR_GENERATION",
    "TITLE_CARD_RENDER",
    "DIAGRAM_CARD_RENDER",
    "STAT_CARD_RENDER",
    "LOWER_THIRD_RENDER",
    "HERO_COMPOSITION_RENDER",
    "THUMBNAIL_RENDER",
    "LONG_FORM_FINAL_RENDER",
    "LONG_MEDIA_QC",
    "LONG_PUBLISH_PACKAGE",
    "SHORT_CANDIDATE_EXTRACTION",
    "SHORT_SCRIPT_GENERATION",
    "SHORT_VOICE_GENERATION",
    "SHORT_CAPTION_TIMELINE",
    "SHORT_HERO_REUSE",
    "SHORT_RENDER",
    "SHORT_MEDIA_QC",
    "SHORT_PUBLISH_PACKAGE",
    "PREVIEW_CLIP_RENDER",
    "LICENSE_EVIDENCE_CHECK",
    "BUDGET_CHECK",
    "PROVIDER_CAPABILITY_CHECK",
    "VOICE_GENERATION",
}


def _read_catalog(catalog_key: str) -> list[dict[str, Any]]:
    path = CONFIG_DIR / f"{catalog_key}.yaml"
    with path.open("r", encoding="utf-8") as handle:
        content = yaml.safe_load(handle) or {}
    if content.get("catalog_key") != catalog_key:
        raise ValidationFailureError(f"invalid catalog_key in {path}")
    items = content.get("items")
    if not isinstance(items, list):
        raise ValidationFailureError(f"catalog items must be a list: {path}")
    return items


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _provider_role_seeds() -> list[dict[str, Any]]:
    return [
        {
            "provider_key": item["provider_key"],
            "provider_name": item["provider_name"],
            "provider_type": item["provider_type"],
            "role_description": item["role_description"],
            "recommendation": item["recommendation"],
            "is_enabled": bool(item.get("is_enabled", True)),
            "is_real_provider": bool(item.get("is_real_provider", False)),
            "supports_real_execution": bool(item.get("supports_real_execution", False)),
            "monthly_budget_assumption": item.get("monthly_budget_assumption") or {},
            "notes": item.get("notes"),
        }
        for item in _read_catalog("media_provider_role_profile_catalog")
    ]


def _provider_capability_seeds() -> list[dict[str, Any]]:
    return [
        {
            "provider_key": item["provider_key"],
            "provider_type": item["provider_type"],
            "job_type": str(item["job_type"]).upper(),
            "capability": item["capability"],
            "max_duration_seconds": _decimal_or_none(item.get("max_duration_seconds")),
            "supported_aspect_ratios": item.get("supported_aspect_ratios") or [],
            "supported_outputs": item.get("supported_outputs") or [],
            "plan_requirement": item.get("plan_requirement"),
            "capability_reason": item["capability_reason"],
        }
        for item in _read_catalog("media_provider_capability_matrix_catalog")
    ]


def _default_budget_policy_seeds() -> list[dict[str, Any]]:
    return [
        {
            "provider_type": item["provider_type"],
            "provider_key": item.get("provider_key"),
            "monthly_cap_units": _decimal_or_none(item.get("monthly_cap_units")),
            "monthly_cap_usd": _decimal_or_none(item.get("monthly_cap_usd")),
            "monthly_cap_seconds": _decimal_or_none(item.get("monthly_cap_seconds")),
            "monthly_cap_renders": item.get("monthly_cap_renders"),
            "current_mode": item["current_mode"],
            "enforcement": item["enforcement"],
        }
        for item in _read_catalog("media_provider_budget_policy_catalog")
    ]


def _routing_policy() -> dict[str, dict[str, str]]:
    return {
        str(item["job_type"]).upper(): {"provider_key": item["provider_key"], "reason_code": item["reason_code"]}
        for item in _read_catalog("media_provider_routing_policy_catalog")
    }


@dataclass(frozen=True)
class GoogleVertexVeoResolvedConfig:
    provider_key: str
    model: str | None
    mode: str | None
    resolution: str | None
    audio_enabled: bool | None
    default_duration_seconds: Decimal | None
    max_duration_seconds: Decimal | None
    cost_per_second_1080p: Decimal | None
    monthly_budget_usd: Decimal | None
    project_id: str | None
    location: str | None
    service_account_path: str | None
    real_execution_enabled: bool
    real_smoke_enabled: bool

    def estimate_cost(self, duration_seconds: Decimal | None) -> Decimal | None:
        if duration_seconds is None or self.cost_per_second_1080p is None:
            return None
        return duration_seconds * self.cost_per_second_1080p


class GoogleVertexVeoConfigService:
    def __init__(self, session: Session):
        self.session = session

    def resolve(self) -> GoogleVertexVeoResolvedConfig:
        settings = get_settings()
        role = self.session.scalars(
            select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == GOOGLE_VERTEX_VEO_PROVIDER_KEY)
        ).one_or_none()
        seed = next((item for item in _provider_role_seeds() if item["provider_key"] == GOOGLE_VERTEX_VEO_PROVIDER_KEY), None)
        defaults = (role.monthly_budget_assumption if role else seed.get("monthly_budget_assumption") if seed else {}) or {}
        return GoogleVertexVeoResolvedConfig(
            provider_key=role.provider_key if role else GOOGLE_VERTEX_VEO_PROVIDER_KEY,
            model=settings.veo_model or defaults.get("model"),
            mode=settings.veo_mode or defaults.get("video_mode"),
            resolution=settings.veo_resolution or defaults.get("resolution"),
            audio_enabled=settings.veo_audio_enabled if settings.veo_audio_enabled is not None else defaults.get("audio_enabled"),
            default_duration_seconds=_decimal_or_none(
                settings.veo_default_duration_seconds
                if settings.veo_default_duration_seconds is not None
                else defaults.get("default_duration_seconds")
            ),
            max_duration_seconds=_decimal_or_none(
                settings.veo_max_duration_seconds if settings.veo_max_duration_seconds is not None else defaults.get("max_duration_seconds")
            ),
            cost_per_second_1080p=_decimal_or_none(
                settings.veo_cost_per_second_1080p
                if settings.veo_cost_per_second_1080p is not None
                else defaults.get("cost_per_second_1080p")
            ),
            monthly_budget_usd=_decimal_or_none(
                settings.veo_monthly_budget_usd if settings.veo_monthly_budget_usd is not None else defaults.get("monthly_budget_usd")
            ),
            project_id=settings.google_cloud_project_id,
            location=settings.google_cloud_location,
            service_account_path=settings.google_application_credentials,
            real_execution_enabled=settings.veo_real_execution_enabled,
            real_smoke_enabled=settings.veo_real_smoke,
        )

    def readiness_reason_codes(self, config: GoogleVertexVeoResolvedConfig | None = None) -> list[str]:
        resolved = config or self.resolve()
        reasons: list[str] = []
        if _normalized_ai_hero_provider(get_settings().ai_hero_provider) not in {None, GOOGLE_VERTEX_VEO_PROVIDER_KEY}:
            reasons.append("UNSUPPORTED_AI_HERO_PROVIDER")
        if not resolved.model or not resolved.mode or not resolved.resolution:
            reasons.append("VEO_CONFIG_MISSING")
        if resolved.cost_per_second_1080p is None:
            reasons.append("VEO_COST_CONFIG_MISSING")
        if resolved.real_execution_enabled:
            if not resolved.project_id:
                reasons.append("GOOGLE_CLOUD_PROJECT_ID_MISSING")
            if not resolved.location:
                reasons.append("GOOGLE_CLOUD_LOCATION_MISSING")
            if not resolved.service_account_path:
                reasons.append("GOOGLE_APPLICATION_CREDENTIALS_MISSING")
        return reasons or ["VEO_PROVIDER_CONFIG_READY"]


class MediaProviderRoleService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_matrix(self) -> list[MediaProviderRoleProfile]:
        records: list[MediaProviderRoleProfile] = []
        provider_role_seeds = _provider_role_seeds()
        configured_provider_keys = {seed["provider_key"] for seed in provider_role_seeds}
        for seed in provider_role_seeds:
            profile = self.session.scalars(
                select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == seed["provider_key"])
            ).one_or_none()
            if profile is None:
                profile = MediaProviderRoleProfile(**seed)
                self.session.add(profile)
            else:
                for key, value in seed.items():
                    setattr(profile, key, value)
            records.append(profile)
        self.session.flush()
        self._disable_removed_ai_hero_profiles(configured_provider_keys)
        ProviderCapabilityMatrixService(self.session).ensure_matrix()
        MediaProviderBudgetService(self.session).ensure_default_policies()
        return self.list_roles()

    def _disable_removed_ai_hero_profiles(self, configured_provider_keys: set[str]) -> None:
        removed = self.session.scalars(
            select(MediaProviderRoleProfile)
            .where(MediaProviderRoleProfile.provider_type == AI_VIDEO_HERO_PROVIDER)
            .where(MediaProviderRoleProfile.provider_key.not_in(configured_provider_keys))
        ).all()
        for profile in removed:
            profile.is_enabled = False
            profile.supports_real_execution = False
            profile.notes = "Disabled by M10.4 single AI hero provider binding; no fallback provider is allowed."
        self.session.flush()

    def list_roles(self) -> list[MediaProviderRoleProfile]:
        if not self.session.scalars(select(MediaProviderRoleProfile).limit(1)).first():
            self.ensure_matrix()
        return list(self.session.scalars(select(MediaProviderRoleProfile).order_by(MediaProviderRoleProfile.provider_key)).all())

    def get_role(self, provider_key: str) -> MediaProviderRoleProfile | None:
        if not self.session.scalars(select(MediaProviderRoleProfile).limit(1)).first():
            self.ensure_matrix()
        return self.session.scalars(
            select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == provider_key)
        ).one_or_none()

    def require_role(self, provider_key: str) -> MediaProviderRoleProfile:
        role = self.get_role(provider_key)
        if role is None:
            raise NotFoundError(f"media provider role not found: {provider_key}")
        return role


class ProviderCapabilityMatrixService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_matrix(self) -> list[ProviderCapabilityMatrixEntry]:
        for seed in _provider_capability_seeds():
            entry = self.session.scalars(
                select(ProviderCapabilityMatrixEntry)
                .where(ProviderCapabilityMatrixEntry.provider_key == seed["provider_key"])
                .where(ProviderCapabilityMatrixEntry.job_type == seed["job_type"])
            ).one_or_none()
            if entry is None:
                self.session.add(ProviderCapabilityMatrixEntry(**seed))
            else:
                for key, value in seed.items():
                    setattr(entry, key, value)
        self.session.flush()
        return self.list_entries()

    def list_entries(self, *, provider_key: str | None = None) -> list[ProviderCapabilityMatrixEntry]:
        if not self.session.scalars(select(ProviderCapabilityMatrixEntry).limit(1)).first():
            self.ensure_matrix()
        statement = select(ProviderCapabilityMatrixEntry)
        if provider_key is not None:
            statement = statement.where(ProviderCapabilityMatrixEntry.provider_key == provider_key)
        return list(self.session.scalars(statement.order_by(ProviderCapabilityMatrixEntry.provider_key, ProviderCapabilityMatrixEntry.job_type)).all())

    def find_entry(self, *, provider_key: str, job_type: str) -> ProviderCapabilityMatrixEntry | None:
        if not self.session.scalars(select(ProviderCapabilityMatrixEntry).limit(1)).first():
            self.ensure_matrix()
        return self.session.scalars(
            select(ProviderCapabilityMatrixEntry)
            .where(ProviderCapabilityMatrixEntry.provider_key == provider_key)
            .where(ProviderCapabilityMatrixEntry.job_type == job_type)
        ).one_or_none()

    def find_supported_by_type(self, *, provider_type: str, job_type: str) -> tuple[MediaProviderRoleProfile, ProviderCapabilityMatrixEntry] | None:
        MediaProviderRoleService(self.session).ensure_matrix()
        rows = self.session.execute(
            select(MediaProviderRoleProfile, ProviderCapabilityMatrixEntry)
            .join(ProviderCapabilityMatrixEntry, ProviderCapabilityMatrixEntry.provider_key == MediaProviderRoleProfile.provider_key)
            .where(MediaProviderRoleProfile.provider_type == provider_type)
            .where(MediaProviderRoleProfile.is_enabled.is_(True))
            .where(ProviderCapabilityMatrixEntry.job_type == job_type)
            .where(ProviderCapabilityMatrixEntry.capability == "SUPPORTED")
            .order_by(MediaProviderRoleProfile.provider_key)
        ).all()
        return rows[0] if rows else None


class MediaRenderJobRouterService:
    def __init__(self, session: Session):
        self.session = session

    def decide(self, *, data: MediaRenderRoutingDecisionRequest) -> MediaRenderRoutingDecision:
        MediaProviderRoleService(self.session).ensure_matrix()
        job_type = data.job_type.upper()
        if job_type not in MEDIA_JOB_TYPES:
            return self._record_decision(
                data=data,
                job_type=job_type,
                routing_result="BLOCKED_UNKNOWN_PROVIDER",
                blocker_reason=f"Unknown media job type: {data.job_type}",
                technical_appendix={"reason_code": "BLOCKED_UNKNOWN_PROVIDER", **data.technical_appendix},
            )
        if job_type == LONG_FORM_FINAL_RENDER:
            return self._decide_long_form_final(data=data, job_type=job_type)
        provider_key = _provider_key_for_job(job_type)
        if provider_key is None:
            return self._record_decision(
                data=data,
                job_type=job_type,
                routing_result="BLOCKED_UNKNOWN_PROVIDER",
                blocker_reason=f"No provider route is configured for {job_type}.",
                technical_appendix={"reason_code": "BLOCKED_UNKNOWN_PROVIDER", **data.technical_appendix},
            )
        role = MediaProviderRoleService(self.session).require_role(provider_key)
        entry = ProviderCapabilityMatrixService(self.session).find_entry(provider_key=provider_key, job_type=job_type)
        if entry is None or entry.capability not in {"SUPPORTED", "MOCK_ONLY"}:
            return self._record_decision(
                data=data,
                job_type=job_type,
                selected_provider_type=role.provider_type,
                selected_provider_key=role.provider_key,
                routing_result="BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
                blocker_reason=f"{role.provider_key} does not support {job_type}.",
                capability_entry_id=entry.id if entry else None,
                technical_appendix={"reason_code": "BLOCKED_PROVIDER_CAPABILITY_REQUIRED", **data.technical_appendix},
            )
        duration_block = _duration_block(entry, data.target_duration_seconds)
        aspect_block = _aspect_block(entry, data.target_aspect_ratio)
        if duration_block or aspect_block:
            return self._record_decision(
                data=data,
                job_type=job_type,
                selected_provider_type=role.provider_type,
                selected_provider_key=role.provider_key,
                routing_result="BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
                blocker_reason=duration_block or aspect_block,
                capability_entry_id=entry.id,
                technical_appendix={"reason_code": "BLOCKED_PROVIDER_CAPABILITY_REQUIRED", **data.technical_appendix},
            )
        estimated_usage_seconds = data.estimated_usage_seconds or (data.target_duration_seconds if role.provider_type == AI_VIDEO_HERO_PROVIDER else None)
        if data.estimated_usage_usd is not None or estimated_usage_seconds is not None:
            budget = MediaProviderBudgetService(self.session).check(
                data=MediaProviderBudgetCheckRequest(
                    company_id=data.company_id,
                    provider_type=role.provider_type,
                    provider_key=role.provider_key,
                    estimated_usage_usd=data.estimated_usage_usd,
                    estimated_usage_seconds=estimated_usage_seconds,
                )
            )
            if budget.decision == "BLOCK":
                return self._record_decision(
                    data=data,
                    job_type=job_type,
                    selected_provider_type=role.provider_type,
                    selected_provider_key=role.provider_key,
                    routing_result="BLOCKED_BUDGET",
                    blocker_reason=budget.operator_summary,
                    capability_entry_id=entry.id,
                    budget_snapshot_id=budget.snapshot_id,
                    technical_appendix={"reason_codes": budget.reason_codes, **data.technical_appendix},
                )
        return self._record_decision(
            data=data,
            job_type=job_type,
            selected_provider_type=role.provider_type,
            selected_provider_key=role.provider_key,
            routing_result="ROUTED",
            capability_entry_id=entry.id,
            budget_snapshot_id=budget.snapshot_id if "budget" in locals() else None,
            technical_appendix={
                "reason_code": _route_reason_code(role.provider_type),
                "budget_gate_decision": budget.decision if "budget" in locals() else None,
                "real_provider_execution": False,
                **data.technical_appendix,
            },
        )

    def get_decision(self, decision_id: uuid.UUID) -> MediaRenderRoutingDecision:
        decision = self.session.get(MediaRenderRoutingDecision, decision_id)
        if decision is None:
            raise NotFoundError(f"media render routing decision not found: {decision_id}")
        return decision

    def _decide_long_form_final(self, *, data: MediaRenderRoutingDecisionRequest, job_type: str) -> MediaRenderRoutingDecision:
        cloud = _configured_final_renderer(self.session)
        if cloud is not None:
            role, entry = cloud
            return self._record_decision(
                data=data,
                job_type=job_type,
                selected_provider_type=role.provider_type,
                selected_provider_key=role.provider_key,
                routing_result="ROUTED",
                capability_entry_id=entry.id,
                technical_appendix={"reason_code": "CLOUD_FINAL_RENDERER_REQUIRED", "real_provider_execution": False, **data.technical_appendix},
            )
        creatomate_growth = _configured_creatomate_growth_final_renderer(self.session)
        if creatomate_growth is not None:
            role, entry = creatomate_growth
            return self._record_decision(
                data=data,
                job_type=job_type,
                selected_provider_type=role.provider_type,
                selected_provider_key=role.provider_key,
                routing_result="ROUTED",
                capability_entry_id=entry.id,
                technical_appendix={
                    "reason_code": "CREATOMATE_AS_FINAL_RENDERER",
                    "plan": role.monthly_budget_assumption.get("plan"),
                    "real_provider_execution": False,
                    **data.technical_appendix,
                },
            )
        essential_entry = _blocked_light_renderer_entry(self.session, job_type=LONG_FORM_FINAL_RENDER)
        return self._record_decision(
            data=data,
            job_type=job_type,
            routing_result="BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
            blocker_reason="LONG_FORM_FINAL_RENDER requires a configured CLOUD_FINAL_ASSEMBLY_RENDERER. Creatomate Essential 2K is blocked from acting as the full long-form final render backbone.",
            capability_entry_id=essential_entry.id if essential_entry else None,
            technical_appendix={
                "reason_codes": [
                    "LONG_FORM_FINAL_RENDER_BLOCKED_PROVIDER_REQUIRED",
                    "CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER",
                    "CLOUD_FINAL_RENDERER_REQUIRED",
                ],
                "real_provider_execution": False,
                **data.technical_appendix,
            },
        )

    def _record_decision(
        self,
        *,
        data: MediaRenderRoutingDecisionRequest,
        job_type: str,
        routing_result: str,
        selected_provider_type: str | None = None,
        selected_provider_key: str | None = None,
        blocker_reason: str | None = None,
        capability_entry_id: uuid.UUID | None = None,
        budget_snapshot_id: uuid.UUID | None = None,
        technical_appendix: dict[str, Any] | None = None,
    ) -> MediaRenderRoutingDecision:
        decision = MediaRenderRoutingDecision(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            video_project_id=data.video_project_id,
            job_type=job_type,
            requested_provider_type=data.requested_provider_type,
            selected_provider_type=selected_provider_type,
            selected_provider_key=selected_provider_key,
            routing_result=routing_result,
            blocker_reason=blocker_reason,
            capability_entry_id=capability_entry_id,
            budget_snapshot_id=budget_snapshot_id,
            technical_appendix=technical_appendix or {},
        )
        self.session.add(decision)
        self.session.flush()
        return decision


class ProviderCapabilityGateService:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, data: ProviderCapabilityGateCheckRequest) -> ProviderCapabilityGateRead:
        job_type = data.job_type.upper()
        if data.provider_key is None and data.provider_type is None:
            decision = MediaRenderJobRouterService(self.session).decide(
                data=MediaRenderRoutingDecisionRequest(
                    job_type=job_type,
                    target_duration_seconds=data.target_duration_seconds,
                    target_aspect_ratio=data.target_aspect_ratio,
                )
            )
            if decision.routing_result == "ROUTED":
                return ProviderCapabilityGateRead(
                    decision="PASS",
                    routing_result=decision.routing_result,
                    provider_key=decision.selected_provider_key,
                    provider_type=decision.selected_provider_type,
                    reason_codes=["PROVIDER_CAPABILITY_ENTRY_CREATED"],
                    operator_summary=f"{job_type} can route to {decision.selected_provider_key}.",
                    capability_entry_id=decision.capability_entry_id,
                )
            return ProviderCapabilityGateRead(
                decision="BLOCK",
                routing_result=decision.routing_result,
                provider_key=decision.selected_provider_key,
                provider_type=decision.selected_provider_type,
                reason_codes=decision.technical_appendix.get("reason_codes", ["LONG_FORM_FINAL_RENDER_BLOCKED_PROVIDER_REQUIRED"]),
                blocker_reason=decision.blocker_reason,
                operator_summary=decision.blocker_reason or f"{job_type} is blocked.",
                capability_entry_id=decision.capability_entry_id,
            )
        role = self._resolve_role(data)
        entry = ProviderCapabilityMatrixService(self.session).find_entry(provider_key=role.provider_key, job_type=job_type)
        if entry is None:
            return ProviderCapabilityGateRead(
                decision="BLOCK",
                provider_key=role.provider_key,
                provider_type=role.provider_type,
                reason_codes=["BLOCKED_UNKNOWN_PROVIDER"],
                blocker_reason=f"No capability entry for {role.provider_key}/{job_type}.",
                operator_summary=f"{role.provider_key} has no declared support for {job_type}.",
            )
        duration_block = _duration_block(entry, data.target_duration_seconds)
        aspect_block = _aspect_block(entry, data.target_aspect_ratio)
        if duration_block or aspect_block or entry.capability != "SUPPORTED":
            reason = (
                "CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER"
                if role.provider_type == CLOUD_TEMPLATE_RENDERER_LIGHT and job_type == LONG_FORM_FINAL_RENDER
                else "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"
            )
            return ProviderCapabilityGateRead(
                decision="BLOCK",
                provider_key=role.provider_key,
                provider_type=role.provider_type,
                capability=entry.capability,
                reason_codes=[reason],
                blocker_reason=duration_block or aspect_block or entry.capability_reason,
                operator_summary=duration_block or aspect_block or entry.capability_reason,
                capability_entry_id=entry.id,
            )
        return ProviderCapabilityGateRead(
            decision="PASS",
            provider_key=role.provider_key,
            provider_type=role.provider_type,
            capability=entry.capability,
            reason_codes=["PROVIDER_CAPABILITY_ENTRY_CREATED"],
            operator_summary=f"{role.provider_key} supports {job_type}.",
            capability_entry_id=entry.id,
        )

    def _resolve_role(self, data: ProviderCapabilityGateCheckRequest) -> MediaProviderRoleProfile:
        MediaProviderRoleService(self.session).ensure_matrix()
        if data.provider_key is not None:
            return MediaProviderRoleService(self.session).require_role(data.provider_key)
        role = self.session.scalars(
            select(MediaProviderRoleProfile)
            .where(MediaProviderRoleProfile.provider_type == data.provider_type)
            .where(MediaProviderRoleProfile.is_enabled.is_(True))
            .order_by(MediaProviderRoleProfile.provider_key)
        ).first()
        if role is None:
            raise NotFoundError(f"media provider type not found: {data.provider_type}")
        return role


class MediaProviderBudgetService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_default_policies(self) -> list[MediaProviderBudgetPolicy]:
        records: list[MediaProviderBudgetPolicy] = []
        for seed in _default_budget_policy_seeds():
            if seed["provider_key"] == GOOGLE_VERTEX_VEO_PROVIDER_KEY:
                config = GoogleVertexVeoConfigService(self.session).resolve()
                if config.monthly_budget_usd is not None:
                    seed = {**seed, "monthly_cap_usd": config.monthly_budget_usd}
            policy = self.session.scalars(
                select(MediaProviderBudgetPolicy)
                .where(MediaProviderBudgetPolicy.company_id.is_(None))
                .where(MediaProviderBudgetPolicy.provider_type == seed["provider_type"])
                .where(MediaProviderBudgetPolicy.provider_key == seed.get("provider_key"))
            ).one_or_none()
            if policy is None:
                policy = MediaProviderBudgetPolicy(**seed)
                self.session.add(policy)
            else:
                for key, value in seed.items():
                    setattr(policy, key, value)
            records.append(policy)
        self.session.flush()
        return records

    def list_policies(self) -> list[MediaProviderBudgetPolicy]:
        self.ensure_default_policies()
        return list(self.session.scalars(select(MediaProviderBudgetPolicy).order_by(MediaProviderBudgetPolicy.provider_type)).all())

    def latest_snapshots(self) -> list[MediaProviderBudgetSnapshot]:
        self.ensure_default_policies()
        return list(
            self.session.scalars(
                select(MediaProviderBudgetSnapshot).order_by(MediaProviderBudgetSnapshot.provider_type, desc(MediaProviderBudgetSnapshot.created_at))
            ).all()
        )

    def check(self, *, data: MediaProviderBudgetCheckRequest) -> MediaProviderBudgetGateRead:
        self.ensure_default_policies()
        data = self._with_configured_cost_estimate(data)
        policy = self._find_policy(data)
        if policy is None:
            snapshot = self._create_snapshot(data=data, budget_state="UNKNOWN")
            return MediaProviderBudgetGateRead(
                decision="REVIEW_REQUIRED",
                budget_state="UNKNOWN",
                reason_codes=["BUDGET_GATE_WARNING"],
                operator_summary="No configured budget policy exists for this provider.",
                policy_id=None,
                snapshot_id=snapshot.id,
            )
        state = _budget_state(policy, data)
        snapshot = self._create_snapshot(data=data, budget_state=state)
        if state == "EXCEEDED":
            decision = "BLOCK" if policy.enforcement == "HARD_BLOCK" else "REVIEW_REQUIRED"
            return MediaProviderBudgetGateRead(
                decision=decision,
                budget_state=state,
                reason_codes=["BUDGET_GATE_BLOCKED" if decision == "BLOCK" else "BUDGET_GATE_WARNING"],
                operator_summary="Configured media provider budget cap is exceeded.",
                policy_id=policy.id,
                snapshot_id=snapshot.id,
            )
        if state == "UNKNOWN":
            return MediaProviderBudgetGateRead(
                decision="REVIEW_REQUIRED",
                budget_state=state,
                reason_codes=["BUDGET_GATE_WARNING"],
                operator_summary="Budget usage is unknown because no usage estimate was provided.",
                policy_id=policy.id,
                snapshot_id=snapshot.id,
            )
        return MediaProviderBudgetGateRead(
            decision="PASS",
            budget_state=state,
            reason_codes=["BUDGET_GATE_PASSED" if state == "OK" else "BUDGET_GATE_WARNING"],
            operator_summary="Configured media provider budget check passed." if state == "OK" else "Configured media provider budget is near a cap.",
            policy_id=policy.id,
            snapshot_id=snapshot.id,
        )

    def _find_policy(self, data: MediaProviderBudgetCheckRequest) -> MediaProviderBudgetPolicy | None:
        statements = [
            select(MediaProviderBudgetPolicy)
            .where(MediaProviderBudgetPolicy.company_id == data.company_id)
            .where(MediaProviderBudgetPolicy.provider_type == data.provider_type)
            .where(MediaProviderBudgetPolicy.provider_key == data.provider_key),
            select(MediaProviderBudgetPolicy)
            .where(MediaProviderBudgetPolicy.company_id.is_(None))
            .where(MediaProviderBudgetPolicy.provider_type == data.provider_type)
            .where(MediaProviderBudgetPolicy.provider_key == data.provider_key),
            select(MediaProviderBudgetPolicy)
            .where(MediaProviderBudgetPolicy.company_id.is_(None))
            .where(MediaProviderBudgetPolicy.provider_type == data.provider_type)
            .where(MediaProviderBudgetPolicy.provider_key.is_(None)),
        ]
        for statement in statements:
            policy = self.session.scalars(statement).one_or_none()
            if policy is not None:
                return policy
        return None

    def _with_configured_cost_estimate(self, data: MediaProviderBudgetCheckRequest) -> MediaProviderBudgetCheckRequest:
        if (
            data.provider_type == AI_VIDEO_HERO_PROVIDER
            and data.provider_key == GOOGLE_VERTEX_VEO_PROVIDER_KEY
            and data.estimated_usage_usd is None
            and data.estimated_usage_seconds is not None
        ):
            estimated_cost = GoogleVertexVeoConfigService(self.session).resolve().estimate_cost(data.estimated_usage_seconds)
            if estimated_cost is not None:
                return data.model_copy(update={"estimated_usage_usd": estimated_cost})
        return data

    def _create_snapshot(self, *, data: MediaProviderBudgetCheckRequest, budget_state: str) -> MediaProviderBudgetSnapshot:
        now = utc_now()
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        period_end = (period_start + timedelta(days=32)).replace(day=1)
        snapshot = MediaProviderBudgetSnapshot(
            company_id=data.company_id,
            provider_type=data.provider_type,
            provider_key=data.provider_key,
            period_start=period_start,
            period_end=period_end,
            estimated_usage_units=data.estimated_usage_units,
            estimated_usage_usd=data.estimated_usage_usd,
            estimated_usage_seconds=data.estimated_usage_seconds,
            estimated_render_count=data.estimated_render_count,
            budget_state=budget_state,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot


class LicenseEvidenceGateService:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, data: LicenseEvidenceGateCheckRequest) -> LicenseEvidenceGateRead:
        gated_provider = data.source_provider_type in {API_NATIVE_STOCK_PROVIDER, FREE_FALLBACK_PROVIDER, DEFERRED_MANUAL_LIBRARY}
        record = None
        if data.company_id is not None:
            record = LicenseEvidenceRecord(
                company_id=data.company_id,
                channel_workspace_id=data.channel_workspace_id,
                video_project_id=data.video_project_id,
                asset_ref=data.asset_ref,
                source_provider_type=data.source_provider_type,
                license_status=data.license_status,
                rights_envelope_id=data.rights_envelope_id,
                evidence_text=data.evidence_text,
                evidence_ref=data.evidence_ref,
            )
            self.session.add(record)
            self.session.flush()
        if data.license_status in {"CONFIRMED", "NOT_REQUIRED"}:
            return LicenseEvidenceGateRead(
                decision="PASS",
                license_status=data.license_status,
                reason_codes=["SYSTEM_OK"],
                operator_summary="License evidence is sufficient.",
                license_evidence_record_id=record.id if record else None,
            )
        if gated_provider or data.license_status == "BLOCKED":
            return LicenseEvidenceGateRead(
                decision="BLOCK",
                license_status=data.license_status,
                reason_codes=["LICENSE_EVIDENCE_REQUIRED"],
                operator_summary="Stock/free/manual assets require confirmed license evidence before use.",
                license_evidence_record_id=record.id if record else None,
            )
        return LicenseEvidenceGateRead(
            decision="REVIEW_REQUIRED",
            license_status=data.license_status,
            reason_codes=["LICENSE_EVIDENCE_REQUIRED"],
            operator_summary="License evidence needs review before use.",
            license_evidence_record_id=record.id if record else None,
        )


class ReusedContentRiskGateService:
    def __init__(self, session: Session | None = None):
        self.session = session

    def check(self, *, data: ReusedContentRiskGateCheckRequest) -> ReusedContentRiskGateRead:
        reasons: list[str] = []
        if data.template_only:
            reasons.append("REUSED_CONTENT_REVIEW_REQUIRED")
        if not data.original_script_present or not data.topic_specific_examples_present:
            reasons.append("REUSED_CONTENT_REVIEW_REQUIRED")
        if data.reused_runtime_pct is not None and data.reused_runtime_pct >= Decimal("90") and not data.human_approval_path_present:
            return ReusedContentRiskGateRead(
                decision="BLOCK",
                reason_codes=["REUSED_CONTENT_REVIEW_REQUIRED"],
                operator_summary="High-reuse output requires an original narrative and human approval path.",
            )
        if reasons:
            return ReusedContentRiskGateRead(
                decision="REVIEW_REQUIRED",
                reason_codes=sorted(set(reasons)),
                operator_summary="Template-only or weakly original output requires review.",
            )
        return ReusedContentRiskGateRead(
            decision="PASS",
            reason_codes=["SYSTEM_OK"],
            operator_summary="Reused content risk gate passed.",
        )


class MediaQCGateService:
    def __init__(self, session: Session):
        self.session = session

    def check(self, *, data: MediaQCGateCheckRequest) -> MediaQCGateRead:
        if data.media_qc_report_id is not None:
            report = self.session.get(MediaQCReport, data.media_qc_report_id)
            if report is None:
                raise NotFoundError(f"media QC report not found: {data.media_qc_report_id}")
            if report.qc_state == "PASS":
                return MediaQCGateRead(decision="PASS", reason_codes=["SYSTEM_OK"], operator_summary="Existing M6 MediaQC report passed.")
            return MediaQCGateRead(
                decision="BLOCK",
                reason_codes=report.reason_codes or ["MEDIA_QC_BLOCKED"],
                operator_summary="Existing M6 MediaQC report is not passing.",
            )
        failures: list[str] = []
        if not data.file_ref:
            failures.append("MEDIA_FILE_MISSING")
        if not data.duration_ok:
            failures.append("MEDIA_DURATION_INVALID")
        if not data.aspect_ratio_ok:
            failures.append("MEDIA_ASPECT_RATIO_INVALID")
        if not data.audio_present:
            failures.append("MEDIA_AUDIO_MISSING")
        if not data.captions_readable:
            failures.append("MEDIA_CAPTIONS_UNREADABLE")
        if data.black_frames_detected:
            failures.append("MEDIA_BLACK_FRAMES_DETECTED")
        if failures:
            return MediaQCGateRead(decision="BLOCK", reason_codes=failures, operator_summary="Media QC gate blocked the output.")
        return MediaQCGateRead(decision="PASS", reason_codes=["SYSTEM_OK"], operator_summary="Media QC gate passed.")


class HumanApprovalGateService:
    def check(self, *, approved: bool = False) -> dict[str, Any]:
        if approved:
            return {"decision": "PASS", "reason_codes": ["SYSTEM_OK"], "operator_summary": "Human approval is present."}
        return {
            "decision": "REVIEW_REQUIRED",
            "reason_codes": ["HUMAN_APPROVAL_REQUIRED"],
            "operator_summary": "Human approval is required before publishing long-form and Shorts.",
        }


class YouTubeOnlyAnalyticsGateService:
    def check(self) -> dict[str, Any]:
        return {
            "decision": "PASS",
            "reason_codes": ["YOUTUBE_ONLY_ANALYTICS_AUTHORITY"],
            "operator_summary": "Only YouTube analytics is learning authority in Quality-First $250 mode.",
        }


class LongFormRenderPackageService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, video_project_id: uuid.UUID, data: LongFormRenderPackageCreate) -> LongFormRenderPackage:
        project = _require_project(self.session, video_project_id)
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                job_type=LONG_FORM_FINAL_RENDER,
            )
        )
        package = LongFormRenderPackage(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            voice_timeline_id=data.voice_timeline_id,
            caption_track_id=data.caption_track_id,
            visual_plan_id=data.visual_plan_id,
            ai_hero_asset_refs=data.ai_hero_asset_refs,
            creatomate_asset_refs=data.creatomate_asset_refs,
            approved_asset_refs=data.approved_asset_refs,
            thumbnail_variant_refs=data.thumbnail_variant_refs,
            music_sfx_refs=data.music_sfx_refs,
            render_manifest={
                **data.render_manifest,
                "routing_decision_id": str(decision.id),
                "real_render_executed": False,
            },
            final_renderer_required=True,
            final_renderer_provider_key=decision.selected_provider_key,
            package_state="READY_FOR_FINAL_RENDER" if decision.routing_result == "ROUTED" else "BLOCKED_PROVIDER_CAPABILITY_REQUIRED",
        )
        self.session.add(package)
        self.session.flush()
        return package

    def require(self, package_id: uuid.UUID) -> LongFormRenderPackage:
        package = self.session.get(LongFormRenderPackage, package_id)
        if package is None:
            raise NotFoundError(f"long-form render package not found: {package_id}")
        return package


class ShortRenderPackageService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, short_candidate_id: uuid.UUID, data: ShortRenderPackageCreate) -> ShortRenderPackage:
        MediaProviderRoleService(self.session).ensure_matrix()
        candidate = self.session.get(ShortCandidate, short_candidate_id)
        if candidate is None:
            raise NotFoundError(f"short candidate not found: {short_candidate_id}")
        duration = data.target_duration_seconds or Decimal(candidate.duration_ms) / Decimal("1000")
        if data.target_aspect_ratio != "9:16":
            raise ValidationFailureError("ShortRenderPackage target_aspect_ratio must be 9:16.")
        if duration >= Decimal("59"):
            raise ValidationFailureError("ShortRenderPackage target_duration_seconds must be under 59 seconds.")
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                video_project_id=candidate.parent_video_project_id,
                job_type="SHORT_RENDER",
                target_duration_seconds=duration,
                target_aspect_ratio=data.target_aspect_ratio,
            )
        )
        package = ShortRenderPackage(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.parent_video_project_id,
            short_candidate_id=candidate.id,
            short_render_plan_id=data.short_render_plan_id,
            voice_ref=data.voice_ref,
            caption_track_id=data.caption_track_id,
            hero_reuse_ref=data.hero_reuse_ref,
            template_asset_refs=data.template_asset_refs,
            render_manifest={**data.render_manifest, "routing_decision_id": str(decision.id), "real_render_executed": False},
            target_duration_seconds=duration,
            target_aspect_ratio=data.target_aspect_ratio,
            hard_cap_seconds=59,
            renderer_provider_key=decision.selected_provider_key,
            package_state="READY_FOR_TEMPLATE_RENDER" if decision.routing_result == "ROUTED" else "BLOCKED",
        )
        self.session.add(package)
        self.session.flush()
        return package

    def require(self, package_id: uuid.UUID) -> ShortRenderPackage:
        package = self.session.get(ShortRenderPackage, package_id)
        if package is None:
            raise NotFoundError(f"short render package not found: {package_id}")
        return package


class AIHeroAssetPlanningService:
    def __init__(self, session: Session):
        self.session = session

    def plan(self, *, video_project_id: uuid.UUID, data: AIHeroAssetPlanRequest) -> AIHeroAsset:
        project = _require_project(self.session, video_project_id)
        config = GoogleVertexVeoConfigService(self.session).resolve()
        duration_seconds = data.duration_seconds or config.default_duration_seconds
        estimated_cost = config.estimate_cost(duration_seconds)
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                job_type="AI_HERO_GENERATION",
                target_duration_seconds=duration_seconds,
                target_aspect_ratio="16:9",
                estimated_usage_seconds=duration_seconds,
                estimated_usage_usd=estimated_cost,
            )
        )
        asset = AIHeroAsset(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            prompt=data.prompt,
            intended_usage=data.intended_usage,
            provider_type=AI_VIDEO_HERO_PROVIDER,
            provider_key=decision.selected_provider_key,
            duration_seconds=duration_seconds,
            asset_ref=None,
            still_frame_ref=None,
            rights_evidence_ref=None,
            generation_state="READY_FOR_PROVIDER" if decision.routing_result == "ROUTED" else "BLOCKED",
        )
        self.session.add(asset)
        self.session.flush()
        return asset

    def require(self, asset_id: uuid.UUID) -> AIHeroAsset:
        asset = self.session.get(AIHeroAsset, asset_id)
        if asset is None:
            raise NotFoundError(f"AI hero asset not found: {asset_id}")
        return asset


class AIHeroGenerationService:
    def __init__(self, session: Session):
        self.session = session

    def execute(self, *, asset_id: uuid.UUID, data: AIHeroGenerationExecuteRequest | None = None) -> AIHeroGenerationJobRead:
        request_data = data or AIHeroGenerationExecuteRequest()
        asset = AIHeroAssetPlanningService(self.session).require(asset_id)
        config_service = GoogleVertexVeoConfigService(self.session)
        config = config_service.resolve()
        estimated_cost = config.estimate_cost(asset.duration_seconds)
        budget_gate = MediaProviderBudgetService(self.session).check(
            data=MediaProviderBudgetCheckRequest(
                company_id=asset.company_id,
                provider_type=asset.provider_type,
                provider_key=asset.provider_key,
                estimated_usage_seconds=asset.duration_seconds,
                estimated_usage_usd=estimated_cost,
            )
        )
        readiness = config_service.readiness_reason_codes(config)
        if asset.provider_key != GOOGLE_VERTEX_VEO_PROVIDER_KEY:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=["UNSUPPORTED_AI_HERO_PROVIDER"],
                operator_summary="AI hero asset is not bound to Google Vertex Veo.",
            )
        if asset.generation_state not in {"READY_FOR_PROVIDER", "GENERATED"}:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=["AI_HERO_ASSET_NOT_PROVIDER_READY"],
                operator_summary="AI hero asset is not provider-ready.",
            )
        blocking_readiness = [reason for reason in readiness if reason not in {"VEO_PROVIDER_CONFIG_READY"}]
        if blocking_readiness and config.real_execution_enabled:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=blocking_readiness,
                operator_summary="Veo real execution config is incomplete.",
            )
        if budget_gate.decision != "PASS":
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=budget_gate.reason_codes,
                operator_summary=budget_gate.operator_summary,
            )
        if not config.real_execution_enabled or not config.real_smoke_enabled:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=["VEO_REAL_EXECUTION_DISABLED"],
                operator_summary="Veo provider binding is ready; real execution is disabled by env guard.",
            )
        if not config.model or not config.mode or not config.resolution or asset.duration_seconds is None:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=["VEO_CONFIG_MISSING"],
                operator_summary="Veo generation config is incomplete.",
            )
        response = GoogleVertexVeoProvider().generate_video(
            request=GoogleVertexVeoRequest(
                prompt=asset.prompt,
                model=config.model,
                mode=config.mode,
                resolution=config.resolution,
                duration_seconds=asset.duration_seconds,
                audio_enabled=bool(config.audio_enabled),
                output_gcs_uri=request_data.output_gcs_uri,
            ),
            config=GoogleVertexVeoExecutionConfig(
                project_id=config.project_id,
                location=config.location,
                service_account_path=config.service_account_path,
                real_execution_enabled=config.real_execution_enabled,
                real_smoke_enabled=config.real_smoke_enabled,
            ),
        )
        if not response.ok:
            return self._result(
                asset=asset,
                config=config,
                estimated_cost=estimated_cost,
                budget_gate=budget_gate,
                reason_codes=[response.error_code or "VEO_PROVIDER_ERROR"],
                operator_summary=response.error_message or "Veo provider call failed.",
                real_execution_attempted=True,
            )
        asset.asset_ref = response.output.get("asset_ref")
        asset.still_frame_ref = response.output.get("still_frame_ref")
        asset.rights_evidence_ref = f"provider://{GOOGLE_VERTEX_VEO_PROVIDER_KEY}/rights/provider-generated"
        asset.generation_state = "GENERATED"
        self.session.flush()
        return self._result(
            asset=asset,
            config=config,
            estimated_cost=estimated_cost,
            budget_gate=budget_gate,
            reason_codes=["VEO_REAL_SMOKE_COMPLETED"],
            operator_summary="Veo real smoke request completed.",
            real_execution_attempted=True,
            provider_operation_ref=response.output.get("operation_ref"),
        )

    def _result(
        self,
        *,
        asset: AIHeroAsset,
        config: GoogleVertexVeoResolvedConfig,
        estimated_cost: Decimal | None,
        budget_gate: MediaProviderBudgetGateRead | None,
        reason_codes: list[str],
        operator_summary: str,
        real_execution_attempted: bool = False,
        provider_operation_ref: str | None = None,
    ) -> AIHeroGenerationJobRead:
        return AIHeroGenerationJobRead(
            ai_hero_asset_id=asset.id,
            provider_key=asset.provider_key,
            provider_type=asset.provider_type,
            generation_state=asset.generation_state,
            model=config.model,
            mode=config.mode,
            resolution=config.resolution,
            audio_enabled=config.audio_enabled,
            requested_duration_seconds=asset.duration_seconds,
            estimated_cost_usd=estimated_cost,
            budget_gate=budget_gate,
            real_execution_attempted=real_execution_attempted,
            asset_ref=asset.asset_ref,
            still_frame_ref=asset.still_frame_ref,
            provider_operation_ref=provider_operation_ref,
            reason_codes=reason_codes,
            operator_summary=operator_summary,
        )


class CreatomateRenderAssetPlanningService:
    def __init__(self, session: Session):
        self.session = session

    def plan(self, *, video_project_id: uuid.UUID, data: CreatomateRenderAssetPlanRequest) -> CreatomateRenderAsset:
        project = _require_project(self.session, video_project_id)
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                job_type=data.job_type,
            )
        )
        if decision.routing_result != "ROUTED":
            raise ValidationFailureError(decision.blocker_reason or f"job cannot route to Creatomate: {data.job_type}")
        asset = CreatomateRenderAsset(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            short_candidate_id=data.short_candidate_id,
            job_type=data.job_type.upper(),
            template_key=data.template_key,
            input_payload=data.input_payload,
            output_ref=None,
            provider_type=CLOUD_TEMPLATE_RENDERER_LIGHT,
            provider_key=decision.selected_provider_key,
            render_state="READY_FOR_PROVIDER",
        )
        self.session.add(asset)
        self.session.flush()
        return asset

    def require(self, asset_id: uuid.UUID) -> CreatomateRenderAsset:
        asset = self.session.get(CreatomateRenderAsset, asset_id)
        if asset is None:
            raise NotFoundError(f"Creatomate render asset not found: {asset_id}")
        return asset


class ThumbnailVariantPlanningService:
    def __init__(self, session: Session):
        self.session = session

    def plan(self, *, video_project_id: uuid.UUID, data: ThumbnailVariantPlanRequest) -> list[ThumbnailVariant]:
        project = _require_project(self.session, video_project_id)
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                job_type="THUMBNAIL_RENDER",
            )
        )
        variants: list[ThumbnailVariant] = []
        for item in data.variants:
            variant = ThumbnailVariant(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                variant_label=item.variant_label,
                title_text=item.title_text,
                subtitle_text=item.subtitle_text,
                hero_still_ref=item.hero_still_ref,
                output_ref=None,
                provider_type=CLOUD_TEMPLATE_RENDERER_LIGHT,
                provider_key=decision.selected_provider_key,
                state="READY_FOR_PROVIDER" if decision.routing_result == "ROUTED" else "DRAFT",
            )
            self.session.add(variant)
            variants.append(variant)
        self.session.flush()
        return variants

    def require(self, variant_id: uuid.UUID) -> ThumbnailVariant:
        variant = self.session.get(ThumbnailVariant, variant_id)
        if variant is None:
            raise NotFoundError(f"thumbnail variant not found: {variant_id}")
        return variant


class FinalMediaRefService:
    def __init__(self, session: Session):
        self.session = session

    def create(self, *, data: FinalMediaRefCreate) -> FinalMediaRef:
        if not data.file_ref or data.file_ref.startswith("provider://fake") or data.file_ref.startswith("mock://provider-output"):
            raise ValidationFailureError("FinalMediaRef requires an actual known file ref or explicit test fixture ref.")
        ref = FinalMediaRef(**data.model_dump())
        self.session.add(ref)
        self.session.flush()
        return ref


class MediaProviderReadService:
    def __init__(self, session: Session):
        self.session = session

    def role(self, provider_key: str) -> MediaProviderRoleProfile:
        return MediaProviderRoleService(self.session).require_role(provider_key)

    def capabilities(self, provider_key: str | None = None) -> list[ProviderCapabilityMatrixEntry]:
        return ProviderCapabilityMatrixService(self.session).list_entries(provider_key=provider_key)


def _provider_key_for_job(job_type: str) -> str | None:
    route = _routing_policy().get(job_type)
    return route["provider_key"] if route else None


def _route_reason_code(provider_type: str) -> str:
    return {
        CLOUD_TEMPLATE_RENDERER_LIGHT: "CREATOMATE_LIGHT_RENDER_ROUTED",
        API_NATIVE_TTS: "ELEVENLABS_VOICE_ROUTED",
        AI_VIDEO_HERO_PROVIDER: "AI_HERO_PROVIDER_ROUTED",
    }.get(provider_type, "ROUTING_DECISION_CREATED")


def _configured_final_renderer(session: Session) -> tuple[MediaProviderRoleProfile, ProviderCapabilityMatrixEntry] | None:
    rows = session.execute(
        select(MediaProviderRoleProfile, ProviderCapabilityMatrixEntry)
        .join(ProviderCapabilityMatrixEntry, ProviderCapabilityMatrixEntry.provider_key == MediaProviderRoleProfile.provider_key)
        .where(MediaProviderRoleProfile.provider_type == CLOUD_FINAL_ASSEMBLY_RENDERER)
        .where(MediaProviderRoleProfile.is_enabled.is_(True))
        .where(MediaProviderRoleProfile.supports_real_execution.is_(True))
        .where(ProviderCapabilityMatrixEntry.job_type == LONG_FORM_FINAL_RENDER)
        .where(ProviderCapabilityMatrixEntry.capability == "SUPPORTED")
    ).all()
    return rows[0] if rows else None


def _blocked_light_renderer_entry(session: Session, *, job_type: str) -> ProviderCapabilityMatrixEntry | None:
    return session.scalars(
        select(ProviderCapabilityMatrixEntry)
        .where(ProviderCapabilityMatrixEntry.provider_type == CLOUD_TEMPLATE_RENDERER_LIGHT)
        .where(ProviderCapabilityMatrixEntry.job_type == job_type)
        .where(ProviderCapabilityMatrixEntry.capability == "BLOCKED_BY_PLAN")
    ).first()


def _configured_creatomate_growth_final_renderer(session: Session) -> tuple[MediaProviderRoleProfile, ProviderCapabilityMatrixEntry] | None:
    rows = session.execute(
        select(MediaProviderRoleProfile, ProviderCapabilityMatrixEntry)
        .join(ProviderCapabilityMatrixEntry, ProviderCapabilityMatrixEntry.provider_key == MediaProviderRoleProfile.provider_key)
        .where(MediaProviderRoleProfile.provider_type == CLOUD_TEMPLATE_RENDERER_LIGHT)
        .where(MediaProviderRoleProfile.is_enabled.is_(True))
        .where(ProviderCapabilityMatrixEntry.job_type == LONG_FORM_FINAL_RENDER)
        .where(ProviderCapabilityMatrixEntry.capability == "SUPPORTED")
    ).all()
    for role, entry in rows:
        budget = role.monthly_budget_assumption or {}
        if budget.get("plan") == "GROWTH_10K" and budget.get("allow_long_form_final_renderer") is True:
            return role, entry
    return None


def _duration_block(entry: ProviderCapabilityMatrixEntry, target_duration_seconds: Decimal | None) -> str | None:
    if target_duration_seconds is None or entry.max_duration_seconds is None:
        return None
    if target_duration_seconds > entry.max_duration_seconds:
        return f"{entry.provider_key} supports {entry.job_type} only up to {entry.max_duration_seconds} seconds."
    return None


def _aspect_block(entry: ProviderCapabilityMatrixEntry, target_aspect_ratio: str | None) -> str | None:
    if not target_aspect_ratio or not entry.supported_aspect_ratios:
        return None
    if target_aspect_ratio not in entry.supported_aspect_ratios:
        return f"{entry.provider_key} does not support aspect ratio {target_aspect_ratio} for {entry.job_type}."
    return None


def _budget_state(policy: MediaProviderBudgetPolicy, data: MediaProviderBudgetCheckRequest) -> str:
    estimates_present = any(
        value is not None
        for value in [
            data.estimated_usage_units,
            data.estimated_usage_usd,
            data.estimated_usage_seconds,
            data.estimated_render_count,
        ]
    )
    if not estimates_present:
        return "UNKNOWN"
    if (
        policy.provider_type == AI_VIDEO_HERO_PROVIDER
        and policy.provider_key == GOOGLE_VERTEX_VEO_PROVIDER_KEY
        and policy.monthly_cap_usd is not None
        and data.estimated_usage_seconds is not None
        and data.estimated_usage_usd is None
    ):
        return "UNKNOWN"
    exceeded = False
    warning = False
    if policy.monthly_cap_usd is not None and data.estimated_usage_usd is not None:
        exceeded = exceeded or data.estimated_usage_usd > policy.monthly_cap_usd
        warning = warning or data.estimated_usage_usd >= policy.monthly_cap_usd * Decimal("0.8")
    if policy.monthly_cap_units is not None and data.estimated_usage_units is not None:
        exceeded = exceeded or data.estimated_usage_units > policy.monthly_cap_units
        warning = warning or data.estimated_usage_units >= policy.monthly_cap_units * Decimal("0.8")
    if policy.monthly_cap_seconds is not None and data.estimated_usage_seconds is not None:
        exceeded = exceeded or data.estimated_usage_seconds > policy.monthly_cap_seconds
        warning = warning or data.estimated_usage_seconds >= policy.monthly_cap_seconds * Decimal("0.8")
    if policy.monthly_cap_renders is not None and data.estimated_render_count is not None:
        exceeded = exceeded or data.estimated_render_count > policy.monthly_cap_renders
        warning = warning or data.estimated_render_count >= int(policy.monthly_cap_renders * 0.8)
    if exceeded:
        return "EXCEEDED"
    if warning:
        return "WARNING"
    return "OK"


def _normalized_ai_hero_provider(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if normalized == "google_vertex_veo":
        return GOOGLE_VERTEX_VEO_PROVIDER_KEY
    return value


def _require_project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"video project not found: {project_id}")
    return project
