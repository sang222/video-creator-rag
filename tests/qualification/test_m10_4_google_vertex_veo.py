from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.contracts import AIHeroAssetPlanRequest, MediaProviderBudgetCheckRequest, MediaRenderRoutingDecisionRequest
from app.db.models import MediaProviderRoleProfile, ProviderCapabilityMatrixEntry
from app.services import AIHeroAssetPlanningService, MediaProviderBudgetService, MediaProviderRoleService, MediaRenderJobRouterService
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


def test_m10_4_veo_budget_uses_configured_cost_and_unknown_when_missing(db_session, monkeypatch) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    service = MediaProviderBudgetService(db_session)
    default_clip = service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="GOOGLE_VERTEX_VEO",
            estimated_usage_seconds=Decimal("10"),
        )
    )
    assert default_clip.decision == "PASS"
    snapshot = service.latest_snapshots()[0]
    assert snapshot.estimated_usage_usd == Decimal("1.000000")

    def no_price_config(self):
        return GoogleVertexVeoResolvedConfig(
            provider_key="GOOGLE_VERTEX_VEO",
            model="veo-3.1-fast",
            mode="video-only",
            resolution="1080p",
            audio_enabled=False,
            default_duration_seconds=Decimal("8"),
            max_duration_seconds=Decimal("10"),
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
            estimated_usage_seconds=Decimal("10"),
        )
    )
    assert missing_cost.decision == "REVIEW_REQUIRED"
    assert missing_cost.budget_state == "UNKNOWN"

    assert db_session.scalars(
        select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == "GOOGLE_VERTEX_VEO")
    ).one().monthly_budget_assumption["backup_provider"] is None
