from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.contracts import (
    AIHeroAssetPlanRequest,
    MediaProviderBudgetCheckRequest,
    MediaRenderRoutingDecisionRequest,
    ProviderCapabilityGateCheckRequest,
)
from app.core.config import VEO_ALLOWED_DURATION_SECONDS, VEO_GA_MODEL_ID, VEO_VIDEO_ONLY_MODE
from app.db.models import MediaProviderRoleProfile, ProviderCapabilityMatrixEntry
from app.services import (
    AIHeroAssetPlanningService,
    MediaProviderBudgetService,
    MediaProviderRoleService,
    MediaRenderJobRouterService,
    ProviderCapabilityGateService,
)
from app.services.m10_2 import GoogleVertexVeoConfigService, GoogleVertexVeoResolvedConfig


def test_m10_4_binds_ai_hero_to_google_vertex_veo_without_alternative_fallbacks(db_session, qualification_factory) -> None:
    flow = qualification_factory.m6_full_flow()
    MediaProviderRoleService(db_session).ensure_matrix()

    for provider_key in ["runway", "luma", "cinematic_ai_hero"]:
        db_session.add(
            MediaProviderRoleProfile(
                provider_key=provider_key,
                provider_name=f"{provider_key} should not be used",
                provider_type="AI_VIDEO_HERO_PROVIDER",
                role_description="Injected fallback candidate.",
                recommendation="CORE_QUALITY_LAYER",
                is_enabled=True,
                is_real_provider=True,
                supports_real_execution=True,
                monthly_budget_assumption={"mode": "TEST"},
            )
        )
        db_session.add(
            ProviderCapabilityMatrixEntry(
                provider_key=provider_key,
                provider_type="AI_VIDEO_HERO_PROVIDER",
                job_type="AI_HERO_GENERATION",
                capability="SUPPORTED",
                supported_aspect_ratios=["16:9"],
                supported_outputs=["video_clip"],
                capability_reason="Injected fallback capability must not be selected.",
            )
        )
    db_session.flush()

    hero_route = MediaRenderJobRouterService(db_session).decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_HERO_GENERATION"))
    metaphor_route = MediaRenderJobRouterService(db_session).decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_METAPHOR_GENERATION"))
    assert hero_route.routing_result == "ROUTED"
    assert hero_route.selected_provider_key == "GOOGLE_VERTEX_VEO"
    assert metaphor_route.selected_provider_key == "GOOGLE_VERTEX_VEO"
    assert "runway" not in {hero_route.selected_provider_key, metaphor_route.selected_provider_key}
    assert "luma" not in {hero_route.selected_provider_key, metaphor_route.selected_provider_key}
    assert "cinematic_ai_hero" not in {hero_route.selected_provider_key, metaphor_route.selected_provider_key}

    planned = AIHeroAssetPlanningService(db_session).plan(
        video_project_id=flow.project.id,
        data=AIHeroAssetPlanRequest(prompt="Opening hook visual.", intended_usage="OPENING_HOOK"),
    )
    assert planned.provider_key == "GOOGLE_VERTEX_VEO"
    assert planned.duration_seconds == Decimal("8")
    assert planned.generation_state == "READY_FOR_PROVIDER"


def test_m10_4_veo_config_uses_ga_model_exact_durations_and_no_preview_or_backup(db_session) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    config = GoogleVertexVeoConfigService(db_session).resolve()
    role = db_session.scalars(
        select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == "GOOGLE_VERTEX_VEO")
    ).one()
    defaults = role.monthly_budget_assumption
    capability_entries = {
        entry.job_type: entry
        for entry in db_session.scalars(
            select(ProviderCapabilityMatrixEntry).where(ProviderCapabilityMatrixEntry.provider_key == "GOOGLE_VERTEX_VEO")
        )
    }

    assert config.model_id == VEO_GA_MODEL_ID
    assert defaults["model_id"] == VEO_GA_MODEL_ID
    assert "model" not in defaults
    assert config.model_id != "veo-3.1-fast-generate-preview"
    assert config.model_id != "veo-3.1-fast"
    assert config.mode == VEO_VIDEO_ONLY_MODE
    assert defaults["video_mode"] == VEO_VIDEO_ONLY_MODE
    assert tuple(int(value) for value in config.allowed_duration_seconds) == VEO_ALLOWED_DURATION_SECONDS
    assert defaults["allowed_duration_seconds"] == [4, 6, 8]
    assert config.default_duration_seconds == Decimal("8")
    assert config.max_duration_seconds == Decimal("8")
    assert capability_entries["AI_HERO_GENERATION"].max_duration_seconds == Decimal("8.000000")
    assert capability_entries["AI_METAPHOR_GENERATION"].max_duration_seconds == Decimal("8.000000")
    assert config.estimate_cost(Decimal("8")) == Decimal("0.80")
    assert defaults["cost_per_second_1080p_video_only"] == "0.10"
    assert defaults["default_8s_attempt_estimate_usd"] == "0.80"
    assert defaults["backup_provider"] is None

    ai_hero_roles = db_session.scalars(
        select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_type == "AI_VIDEO_HERO_PROVIDER")
    ).all()
    assert {role.provider_key for role in ai_hero_roles if role.is_enabled} == {"GOOGLE_VERTEX_VEO"}
    assert all("runway" not in role.provider_key.lower() for role in ai_hero_roles)
    assert all("luma" not in role.provider_key.lower() for role in ai_hero_roles)

    gate = ProviderCapabilityGateService(db_session)
    for duration in (4, 6, 8):
        result = gate.check(
            data=ProviderCapabilityGateCheckRequest(
                job_type="AI_HERO_GENERATION",
                provider_key="GOOGLE_VERTEX_VEO",
                target_duration_seconds=Decimal(duration),
                target_aspect_ratio="16:9",
            )
        )
        assert result.decision == "PASS"
    blocked = gate.check(
        data=ProviderCapabilityGateCheckRequest(
            job_type="AI_HERO_GENERATION",
            provider_key="GOOGLE_VERTEX_VEO",
            target_duration_seconds=Decimal("10"),
            target_aspect_ratio="16:9",
        )
    )
    assert blocked.decision == "BLOCK"


def test_m10_4_veo_budget_uses_configured_cost_and_unknown_when_missing(db_session, monkeypatch) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    service = MediaProviderBudgetService(db_session)
    default_clip = service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="GOOGLE_VERTEX_VEO",
            estimated_usage_seconds=Decimal("8"),
        )
    )
    assert default_clip.decision == "PASS"
    snapshot = service.latest_snapshots()[0]
    assert snapshot.estimated_usage_usd == Decimal("0.800000")

    def no_price_config(self):
        return GoogleVertexVeoResolvedConfig(
            provider_key="GOOGLE_VERTEX_VEO",
            model_id=VEO_GA_MODEL_ID,
            mode=VEO_VIDEO_ONLY_MODE,
            resolution="1080p",
            audio_enabled=False,
            allowed_duration_seconds=tuple(Decimal(str(value)) for value in VEO_ALLOWED_DURATION_SECONDS),
            default_duration_seconds=Decimal("8"),
            max_duration_seconds=Decimal("8"),
            cost_per_second_1080p=None,
            monthly_budget_usd=Decimal("175"),
            project_id=None,
            location=None,
            service_account_path=None,
            real_execution_enabled=False,
            real_smoke_enabled=False,
        )

    monkeypatch.setattr(GoogleVertexVeoConfigService, "resolve", no_price_config)
    missing_cost = service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="GOOGLE_VERTEX_VEO",
            estimated_usage_seconds=Decimal("8"),
        )
    )
    assert missing_cost.decision == "REVIEW_REQUIRED"
    assert missing_cost.budget_state == "UNKNOWN"

    assert db_session.scalars(
        select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == "GOOGLE_VERTEX_VEO")
    ).one().monthly_budget_assumption["backup_provider"] is None
