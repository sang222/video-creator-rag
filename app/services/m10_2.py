from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.contracts import (
    AIHeroAssetPlanRequest,
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

CREATOMATE_PROVIDER_KEY = "creatomate_essential_2k"
ELEVENLABS_PROVIDER_KEY = "elevenlabs_flash_turbo"
CINEMATIC_AI_PROVIDER_KEY = "cinematic_ai_hero"
CLOUD_FINAL_RENDERER_TBD_KEY = "cloud_final_assembly_renderer_tbd"

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
VCOS_JOB_PROVIDER_KEYS = {
    "TOPIC_DECISION": "vcos_backend",
    "SHORT_CANDIDATE_EXTRACTION": "vcos_backend",
    "SHORT_HERO_REUSE": "vcos_backend",
    "BUDGET_CHECK": "vcos_backend",
    "PROVIDER_CAPABILITY_CHECK": "vcos_backend",
    "LONG_SCRIPT_GENERATION": "llm_router",
    "SHORT_SCRIPT_GENERATION": "llm_router",
    "LONG_VISUAL_PLAN": "llm_router",
    "LONG_CAPTION_TIMELINE": "vcos_caption_timeline",
    "SHORT_CAPTION_TIMELINE": "vcos_caption_timeline",
    "LONG_MEDIA_QC": "vcos_media_qc",
    "SHORT_MEDIA_QC": "vcos_media_qc",
    "LONG_PUBLISH_PACKAGE": "vcos_publish_handoff",
    "SHORT_PUBLISH_PACKAGE": "vcos_publish_handoff",
    "LICENSE_EVIDENCE_CHECK": "vcos_backend",
}

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

PROVIDER_ROLE_SEEDS: list[dict[str, Any]] = [
    {
        "provider_key": "vcos_backend",
        "provider_name": "VCOS Backend",
        "provider_type": WORKFLOW_ORCHESTRATOR,
        "role_description": "Orchestration, state, manifest, budget, QC, approval workflow, and publish handoff package.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": True,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "cost_usd": 0},
        "notes": "VCOS does not fake external provider outputs, bypass approval, or auto publish/upload/reupload.",
    },
    {
        "provider_key": "llm_router",
        "provider_name": "Existing LLM source / LLMRouter",
        "provider_type": LLM_SCRIPT_ENGINE,
        "role_description": "Script and planning language tasks through the guarded M10.1 router.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "provider_cost_unknown": True},
        "notes": "Business services route by lane; M10.2 does not add new real LLM execution.",
    },
    {
        "provider_key": ELEVENLABS_PROVIDER_KEY,
        "provider_name": "ElevenLabs Flash/Turbo",
        "provider_type": API_NATIVE_TTS,
        "role_description": "Voice generation only: long narration, short narration, segments, and usage metadata.",
        "recommendation": "CORE_QUALITY_LAYER",
        "is_enabled": True,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "assumption_range_usd": [10, 15]},
        "notes": "No script writing, video render, captions, stock licensing, or publish package behavior.",
    },
    {
        "provider_key": "vcos_caption_timeline",
        "provider_name": "VCOS caption timeline service",
        "provider_type": CAPTION_TIMELINE_ENGINE,
        "role_description": "Caption timing and caption track planning derived from voice timelines.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": True,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "cost_usd": 0},
        "notes": "Caption planning only; not a real media provider call.",
    },
    {
        "provider_key": CINEMATIC_AI_PROVIDER_KEY,
        "provider_name": "Cinematic AI Hero Provider",
        "provider_type": AI_VIDEO_HERO_PROVIDER,
        "role_description": "Premium AI hero/metaphor visual generation only.",
        "recommendation": "CORE_QUALITY_LAYER",
        "is_enabled": True,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "base_assumption_usd": 129, "extra_budget_range_usd": [45, 50]},
        "notes": "No full edited video render, accurate diagram generation, caption management, or final publish package.",
    },
    {
        "provider_key": CREATOMATE_PROVIDER_KEY,
        "provider_name": "Creatomate Essential 2K",
        "provider_type": CLOUD_TEMPLATE_RENDERER_LIGHT,
        "role_description": "Light template renderer for Shorts, cards, thumbnails, lower thirds, and hero composition.",
        "recommendation": "CORE_LIGHT_RENDER",
        "is_enabled": True,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {
            "mode": "QUALITY_FIRST_250",
            "assumption_usd": 59,
            "plan": "ESSENTIAL_2K",
            "allow_long_form_final_renderer": False,
        },
        "notes": "Critical invariant: not the full long-form render backbone on Essential 2K.",
    },
    {
        "provider_key": CLOUD_FINAL_RENDERER_TBD_KEY,
        "provider_name": "TBD Cloud Final Assembly Renderer",
        "provider_type": CLOUD_FINAL_ASSEMBLY_RENDERER,
        "role_description": "Required gap for full long-form final MP4 assembly.",
        "recommendation": "REQUIRED_GAP",
        "is_enabled": False,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "provider_required_gap": True},
        "notes": "Must be configured before LONG_FORM_FINAL_RENDER can route.",
    },
    {
        "provider_key": "vcos_storage",
        "provider_name": "VCOS storage/object storage",
        "provider_type": MEDIA_STORAGE,
        "role_description": "Object refs and durable media storage references.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": True,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "cost_usd": 0},
        "notes": "Does not turn Creatomate into permanent storage/archive.",
    },
    {
        "provider_key": "vcos_media_qc",
        "provider_name": "VCOS MediaQC",
        "provider_type": MEDIA_QC_ENGINE,
        "role_description": "Media correctness checks and M6 MediaQC integration point.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": True,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "cost_usd": 0},
        "notes": "Delegates to existing M6 MediaQC where a report exists.",
    },
    {
        "provider_key": "vcos_publish_handoff",
        "provider_name": "VCOS publish handoff",
        "provider_type": PUBLISH_PACKAGE_BUILDER,
        "role_description": "Manual publish handoff package builder.",
        "recommendation": "CORE",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": True,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "cost_usd": 0},
        "notes": "No auto upload, publish, reupload, or dashboard approval UI.",
    },
    {
        "provider_key": "paid_stock_provider_deferred",
        "provider_name": "Paid stock providers",
        "provider_type": API_NATIVE_STOCK_PROVIDER,
        "role_description": "Paid stock API providers deferred from daily backbone.",
        "recommendation": "DEFERRED",
        "is_enabled": False,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "core_budget_usd": 0},
        "notes": "Deferred; stock remains $0 core in current mode.",
    },
    {
        "provider_key": "pexels_pixabay_free_fallback",
        "provider_name": "Pexels/Pixabay/free fallback",
        "provider_type": FREE_FALLBACK_PROVIDER,
        "role_description": "Free fallback assets only, gated by license evidence.",
        "recommendation": "FALLBACK",
        "is_enabled": True,
        "is_real_provider": True,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "core_budget_usd": 0},
        "notes": "Fallback only; license evidence is required.",
    },
    {
        "provider_key": "envato_manual_library",
        "provider_name": "Envato/manual stock library",
        "provider_type": DEFERRED_MANUAL_LIBRARY,
        "role_description": "Manual library, not daily automated production backbone.",
        "recommendation": "DEFERRED",
        "is_enabled": False,
        "is_real_provider": False,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "QUALITY_FIRST_250", "daily_backbone": False},
        "notes": "Manual library only; no automated Envato integration in M10.2.",
    },
    {
        "provider_key": "mock_media_provider",
        "provider_name": "Mock provider",
        "provider_type": MOCK_PROVIDER,
        "role_description": "Tests/dev only mock provider.",
        "recommendation": "MOCK",
        "is_enabled": True,
        "is_real_provider": False,
        "supports_real_execution": False,
        "monthly_budget_assumption": {"mode": "TEST", "mock_cost_usd": 0},
        "notes": "Mock-only and never production media execution.",
    },
]


def _capability(
    provider_key: str,
    provider_type: str,
    job_type: str,
    capability: str,
    reason: str,
    *,
    max_duration_seconds: int | None = None,
    ratios: list[str] | None = None,
    outputs: list[str] | None = None,
    plan_requirement: str | None = None,
) -> dict[str, Any]:
    return {
        "provider_key": provider_key,
        "provider_type": provider_type,
        "job_type": job_type,
        "capability": capability,
        "max_duration_seconds": Decimal(str(max_duration_seconds)) if max_duration_seconds is not None else None,
        "supported_aspect_ratios": ratios or [],
        "supported_outputs": outputs or [],
        "plan_requirement": plan_requirement,
        "capability_reason": reason,
    }


PROVIDER_CAPABILITY_SEEDS: list[dict[str, Any]] = [
    *[
        _capability(
            CREATOMATE_PROVIDER_KEY,
            CLOUD_TEMPLATE_RENDERER_LIGHT,
            job,
            "SUPPORTED",
            "Creatomate Essential 2K is allowed for light template rendering in Quality-First $250 mode.",
            max_duration_seconds=59 if job == "SHORT_RENDER" else 30 if job == "PREVIEW_CLIP_RENDER" else None,
            ratios=["9:16", "16:9", "1:1"],
            outputs=["mp4", "png", "jpg"],
            plan_requirement="ESSENTIAL_2K",
        )
        for job in sorted(CREATOMATE_LIGHT_JOBS)
    ],
    _capability(
        CREATOMATE_PROVIDER_KEY,
        CLOUD_TEMPLATE_RENDERER_LIGHT,
        LONG_FORM_FINAL_RENDER,
        "BLOCKED_BY_PLAN",
        "Creatomate Essential 2K is not the full long-form final render backbone.",
        plan_requirement="GROWTH_10K_EXPLICIT_FINAL_RENDERER_OR_CLOUD_FINAL_ASSEMBLY_RENDERER",
    ),
    *[
        _capability(
            ELEVENLABS_PROVIDER_KEY,
            API_NATIVE_TTS,
            job,
            "SUPPORTED",
            "ElevenLabs is voice generation only.",
            outputs=["audio", "voice_usage_metadata"],
        )
        for job in sorted(VOICE_JOBS)
    ],
    *[
        _capability(
            CINEMATIC_AI_PROVIDER_KEY,
            AI_VIDEO_HERO_PROVIDER,
            job,
            "SUPPORTED",
            "Cinematic AI provider is premium hero/metaphor visual generation only.",
            outputs=["video_clip", "still_frame"],
        )
        for job in sorted(AI_HERO_JOBS)
    ],
    _capability(
        CLOUD_FINAL_RENDERER_TBD_KEY,
        CLOUD_FINAL_ASSEMBLY_RENDERER,
        LONG_FORM_FINAL_RENDER,
        "REQUIRES_EXTERNAL_PROVIDER",
        "Full long-form final MP4 assembly requires a configured cloud final assembly renderer.",
    ),
    _capability("llm_router", LLM_SCRIPT_ENGINE, "LONG_SCRIPT_GENERATION", "SUPPORTED", "M10.1 LLMRouter handles script tasks."),
    _capability("llm_router", LLM_SCRIPT_ENGINE, "SHORT_SCRIPT_GENERATION", "SUPPORTED", "M10.1 LLMRouter handles short script tasks."),
    _capability("llm_router", LLM_SCRIPT_ENGINE, "LONG_VISUAL_PLAN", "SUPPORTED", "LLMRouter may plan visuals, not render media."),
    _capability("vcos_caption_timeline", CAPTION_TIMELINE_ENGINE, "LONG_CAPTION_TIMELINE", "SUPPORTED", "VCOS builds caption timeline contracts."),
    _capability("vcos_caption_timeline", CAPTION_TIMELINE_ENGINE, "SHORT_CAPTION_TIMELINE", "SUPPORTED", "VCOS builds short caption timeline contracts."),
    _capability("vcos_media_qc", MEDIA_QC_ENGINE, "LONG_MEDIA_QC", "SUPPORTED", "M6 MediaQC remains the QC foundation."),
    _capability("vcos_media_qc", MEDIA_QC_ENGINE, "SHORT_MEDIA_QC", "SUPPORTED", "M6 MediaQC remains the QC foundation."),
    _capability("vcos_publish_handoff", PUBLISH_PACKAGE_BUILDER, "LONG_PUBLISH_PACKAGE", "SUPPORTED", "M7 publish handoff remains manual."),
    _capability("vcos_publish_handoff", PUBLISH_PACKAGE_BUILDER, "SHORT_PUBLISH_PACKAGE", "SUPPORTED", "M10.1 upload cards remain manual."),
    *[
        _capability("vcos_backend", WORKFLOW_ORCHESTRATOR, job, "SUPPORTED", "VCOS orchestrates state, gates, and package planning.")
        for job in ["TOPIC_DECISION", "SHORT_CANDIDATE_EXTRACTION", "SHORT_HERO_REUSE", "BUDGET_CHECK", "PROVIDER_CAPABILITY_CHECK", "LICENSE_EVIDENCE_CHECK"]
    ],
    _capability("mock_media_provider", MOCK_PROVIDER, "SHORT_RENDER", "MOCK_ONLY", "Mock provider is test/dev only."),
]


DEFAULT_BUDGET_POLICIES: list[dict[str, Any]] = [
    {
        "provider_type": API_NATIVE_TTS,
        "provider_key": ELEVENLABS_PROVIDER_KEY,
        "monthly_cap_usd": Decimal("15"),
        "current_mode": "QUALITY_FIRST_250",
        "enforcement": "REVIEW_REQUIRED",
    },
    {
        "provider_type": CLOUD_TEMPLATE_RENDERER_LIGHT,
        "provider_key": CREATOMATE_PROVIDER_KEY,
        "monthly_cap_usd": Decimal("59"),
        "monthly_cap_renders": 160,
        "current_mode": "QUALITY_FIRST_250",
        "enforcement": "REVIEW_REQUIRED",
    },
    {
        "provider_type": AI_VIDEO_HERO_PROVIDER,
        "provider_key": CINEMATIC_AI_PROVIDER_KEY,
        "monthly_cap_usd": Decimal("179"),
        "monthly_cap_renders": 20,
        "current_mode": "QUALITY_FIRST_250",
        "enforcement": "REVIEW_REQUIRED",
    },
    {
        "provider_type": CLOUD_FINAL_ASSEMBLY_RENDERER,
        "provider_key": None,
        "monthly_cap_renders": 10,
        "current_mode": "QUALITY_FIRST_250",
        "enforcement": "HARD_BLOCK",
    },
]


class MediaProviderRoleService:
    def __init__(self, session: Session):
        self.session = session

    def ensure_matrix(self) -> list[MediaProviderRoleProfile]:
        records: list[MediaProviderRoleProfile] = []
        for seed in PROVIDER_ROLE_SEEDS:
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
        ProviderCapabilityMatrixService(self.session).ensure_matrix()
        MediaProviderBudgetService(self.session).ensure_default_policies()
        return self.list_roles()

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
        for seed in PROVIDER_CAPABILITY_SEEDS:
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
        if data.estimated_usage_usd is not None:
            budget = MediaProviderBudgetService(self.session).check(
                data=MediaProviderBudgetCheckRequest(
                    company_id=data.company_id,
                    provider_type=role.provider_type,
                    provider_key=role.provider_key,
                    estimated_usage_usd=data.estimated_usage_usd,
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
            technical_appendix={
                "reason_code": _route_reason_code(role.provider_type),
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
        essential_entry = ProviderCapabilityMatrixService(self.session).find_entry(
            provider_key=CREATOMATE_PROVIDER_KEY,
            job_type=LONG_FORM_FINAL_RENDER,
        )
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
            reason = "CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER" if role.provider_key == CREATOMATE_PROVIDER_KEY and job_type == LONG_FORM_FINAL_RENDER else "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"
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
        for seed in DEFAULT_BUDGET_POLICIES:
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
        decision = MediaRenderJobRouterService(self.session).decide(
            data=MediaRenderRoutingDecisionRequest(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                video_project_id=project.id,
                job_type="AI_HERO_GENERATION",
                target_duration_seconds=data.duration_seconds,
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
            duration_seconds=data.duration_seconds,
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
    if job_type in CREATOMATE_LIGHT_JOBS:
        return CREATOMATE_PROVIDER_KEY
    if job_type in AI_HERO_JOBS:
        return CINEMATIC_AI_PROVIDER_KEY
    if job_type in VOICE_JOBS:
        return ELEVENLABS_PROVIDER_KEY
    return VCOS_JOB_PROVIDER_KEYS.get(job_type)


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


def _require_project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"video project not found: {project_id}")
    return project
