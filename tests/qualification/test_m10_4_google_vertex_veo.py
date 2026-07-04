from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select

from app.contracts import AIHeroAssetPlanRequest, MediaProviderBudgetCheckRequest, MediaRenderRoutingDecisionRequest, ProviderCapabilityGateCheckRequest
from app.db.models import MediaProviderRoleProfile, VideoProject
from app.services import AIHeroAssetPlanningService, MediaProviderBudgetService, MediaProviderRoleService, MediaRenderJobRouterService, ProviderCapabilityGateService
from app.services.m10_2 import LumaHeroVideoConfigService, LumaHeroVideoResolvedConfig


def test_dx2_binds_ai_hero_to_luma_without_veo_or_alternative_fallbacks(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="DX2LumaHero")
    project = VideoProject(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        title="DX2 Luma hero project",
        description="Provider routing fixture without media/provider execution.",
        created_by_user_id=scope.operator.id,
    )
    db_session.add(project)
    db_session.flush()
    MediaProviderRoleService(db_session).ensure_matrix()

    hero_route = MediaRenderJobRouterService(db_session).decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_HERO_GENERATION"))
    metaphor_route = MediaRenderJobRouterService(db_session).decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_METAPHOR_GENERATION"))
    assert hero_route.routing_result == "ROUTED"
    assert hero_route.selected_provider_key == "luma_api"
    assert metaphor_route.selected_provider_key == "luma_api"
    assert "GOOGLE_VERTEX_VEO" not in {hero_route.selected_provider_key, metaphor_route.selected_provider_key}

    planned = AIHeroAssetPlanningService(db_session).plan(
        video_project_id=project.id,
        data=AIHeroAssetPlanRequest(prompt="Opening hook visual.", intended_usage="OPENING_HOOK"),
    )
    assert planned.provider_key == "luma_api"
    assert planned.duration_seconds == Decimal("8")
    assert planned.generation_state == "READY_FOR_PROVIDER"


def test_dx2_luma_config_uses_exact_durations_and_no_ten_second_mode(db_session) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    config = LumaHeroVideoConfigService(db_session).resolve()
    role = db_session.scalars(select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_key == "luma_api")).one()
    defaults = role.monthly_budget_assumption

    assert defaults["provider_key_alias"] == "luma_api"
    assert tuple(int(value) for value in config.allowed_duration_seconds) == (4, 6, 8)
    assert defaults["allowed_duration_seconds"] == [4, 6, 8]
    assert config.default_duration_seconds == Decimal("8")
    assert config.max_duration_seconds == Decimal("8")
    assert config.estimate_cost(Decimal("8")) is None
    assert defaults["backup_provider"] is None

    ai_hero_roles = db_session.scalars(select(MediaProviderRoleProfile).where(MediaProviderRoleProfile.provider_type == "AI_VIDEO_HERO_PROVIDER")).all()
    assert {role.provider_key for role in ai_hero_roles if role.is_enabled} == {"luma_api"}

    gate = ProviderCapabilityGateService(db_session)
    for duration in (4, 6, 8):
        result = gate.check(
            data=ProviderCapabilityGateCheckRequest(
                job_type="AI_HERO_GENERATION",
                provider_key="luma_api",
                target_duration_seconds=Decimal(duration),
                target_aspect_ratio="16:9",
            )
        )
        assert result.decision == "PASS"
    blocked = gate.check(
        data=ProviderCapabilityGateCheckRequest(
            job_type="AI_HERO_GENERATION",
            provider_key="luma_api",
            target_duration_seconds=Decimal("10"),
            target_aspect_ratio="16:9",
        )
    )
    assert blocked.decision == "BLOCK"


def test_dx2_luma_budget_policy_accepts_default_eight_second_clip(db_session, monkeypatch) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    service = MediaProviderBudgetService(db_session)
    default_clip = service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="luma_api",
            estimated_usage_seconds=Decimal("8"),
        )
    )
    assert default_clip.decision == "PASS"
    assert default_clip.budget_state == "OK"

    def priced_config(self):
        return LumaHeroVideoResolvedConfig(
            provider_key="luma_api",
            model_id="ray-2",
            mode="video_only",
            resolution=None,
            audio_enabled=False,
            allowed_duration_seconds=(Decimal("4"), Decimal("6"), Decimal("8")),
            default_duration_seconds=Decimal("8"),
            max_duration_seconds=Decimal("8"),
            cost_per_second_1080p=Decimal("0.25"),
            monthly_budget_usd=Decimal("100"),
            project_id=None,
            location=None,
            service_account_path=None,
            real_execution_enabled=False,
            real_smoke_enabled=False,
        )

    monkeypatch.setattr(LumaHeroVideoConfigService, "resolve", priced_config)
    priced = service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="luma_api",
            estimated_usage_seconds=Decimal("8"),
        )
    )
    assert priced.decision in {"PASS", "REVIEW_REQUIRED"}
