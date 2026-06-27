from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.contracts import (
    AIHeroAssetPlanRequest,
    CreatomateRenderAssetPlanRequest,
    FinalMediaRefCreate,
    LicenseEvidenceGateCheckRequest,
    LongFormRenderPackageCreate,
    MediaProviderBudgetCheckRequest,
    MediaRenderRoutingDecisionRequest,
    ProviderCapabilityGateCheckRequest,
    ReusedContentRiskGateCheckRequest,
    ShortCandidateExtractRequest,
    ShortRenderPackageCreate,
    ThumbnailVariantPlanRequest,
    ThumbnailVariantInput,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    FinalMediaRef,
    MediaProviderRoleProfile,
    MediaRenderRoutingDecision,
    ProviderAttempt,
    ProviderCapabilityMatrixEntry,
)
from app.main import create_app
from app.services import (
    AIHeroAssetPlanningService,
    AIHeroGenerationService,
    CreatomateRenderAssetPlanningService,
    FinalMediaRefService,
    LicenseEvidenceGateService,
    LongFormRenderPackageService,
    MediaProviderBudgetService,
    MediaProviderRoleService,
    MediaRenderJobRouterService,
    ProviderCapabilityGateService,
    ReusedContentRiskGateService,
    ShortCandidateExtractionService,
    ShortRenderPackageService,
    ThumbnailVariantPlanningService,
)

from .helpers.git_checks import tag_exists


M10_2_TABLES = {
    "media_provider_role_profiles",
    "provider_capability_matrix_entries",
    "media_render_routing_decisions",
    "media_provider_budget_policies",
    "media_provider_budget_snapshots",
    "long_form_render_packages",
    "short_render_packages",
    "ai_hero_assets",
    "creatomate_render_assets",
    "thumbnail_variants",
    "final_media_refs",
    "license_evidence_records",
}

FORBIDDEN_M10_3_M11_TABLES = {
    "youtube_follow_sync_runs",
    "youtube_owner_analytics_follow_runs",
    "youtube_public_metric_snapshots",
    "dashboard_widgets",
    "operator_cockpit_views",
    "auto_publish_runs",
    "elevenlabs_provider_runs",
    "creatomate_provider_runs",
    "ai_hero_provider_runs",
    "cloud_final_renderer_runs",
}


def test_m10_2_preflight_migration_defaults_catalogs_and_scope(engine, db_session, qualification_factory) -> None:
    assert tag_exists("m10-1-router-derivative-funnel") is True
    tables = set(inspect(engine).get_table_names())
    assert M10_2_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_3_M11_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0016_m11_operator_dashboard"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in (
                    'media_provider_role_profiles',
                    'provider_capability_matrix_entries',
                    'media_render_routing_decisions',
                    'long_form_render_packages',
                    'short_render_packages',
                    'creatomate_render_assets'
                )
                  and column_name in (
                    'monthly_budget_assumption',
                    'supported_aspect_ratios',
                    'supported_outputs',
                    'technical_appendix',
                    'ai_hero_asset_refs',
                    'creatomate_asset_refs',
                    'template_asset_refs',
                    'render_manifest',
                    'input_payload'
                )
                """
            )
        ).all()
    default_map = {(row.table_name, row.column_name, row.column_default) for row in defaults}
    assert ("media_provider_role_profiles", "monthly_budget_assumption", "'{}'::jsonb") in default_map
    assert ("provider_capability_matrix_entries", "supported_aspect_ratios", "'[]'::jsonb") in default_map
    assert ("provider_capability_matrix_entries", "supported_outputs", "'[]'::jsonb") in default_map
    assert ("media_render_routing_decisions", "technical_appendix", "'{}'::jsonb") in default_map
    assert ("long_form_render_packages", "ai_hero_asset_refs", "'[]'::jsonb") in default_map
    assert ("short_render_packages", "template_asset_refs", "'[]'::jsonb") in default_map
    assert ("creatomate_render_assets", "input_payload", "'{}'::jsonb") in default_map

    qualification_factory.seed_all()
    routes = {route.path for route in create_app().routes}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
    route_text = " ".join(routes).lower()
    assert "publish-now" not in route_text
    assert "youtube-follow" not in route_text


def test_provider_role_matrix_seeded_from_quality_first_matrix(db_session) -> None:
    roles = MediaProviderRoleService(db_session).ensure_matrix()
    by_key = {role.provider_key: role for role in roles}
    assert by_key["vcos_backend"].provider_type == "WORKFLOW_ORCHESTRATOR"
    assert by_key["llm_router"].provider_type == "LLM_SCRIPT_ENGINE"
    assert by_key["elevenlabs_flash_turbo"].provider_type == "API_NATIVE_TTS"
    assert by_key["elevenlabs_flash_turbo"].monthly_budget_assumption["baseline_plan"] == "CREATOR"
    assert by_key["elevenlabs_flash_turbo"].monthly_budget_assumption["budget_basis"] == "credits_or_characters"
    assert by_key["vcos_caption_timeline"].provider_type == "CAPTION_TIMELINE_ENGINE"
    assert by_key["GOOGLE_VERTEX_VEO"].provider_type == "AI_VIDEO_HERO_PROVIDER"
    assert by_key["GOOGLE_VERTEX_VEO"].provider_name == "Google Vertex AI - Veo 3.1 Fast video-only 1080p"
    assert by_key["GOOGLE_VERTEX_VEO"].monthly_budget_assumption["model_id"] == "veo-3.1-fast-generate-001"
    assert by_key["GOOGLE_VERTEX_VEO"].monthly_budget_assumption["video_mode"] == "video_only"
    assert by_key["GOOGLE_VERTEX_VEO"].monthly_budget_assumption["allowed_duration_seconds"] == [4, 6, 8]
    assert by_key["GOOGLE_VERTEX_VEO"].monthly_budget_assumption["cost_per_second_1080p_video_only"] == "0.10"
    assert "cinematic_ai_hero" not in by_key
    assert by_key["creatomate_essential_2k"].provider_type == "CLOUD_TEMPLATE_RENDERER_LIGHT"
    assert by_key["cloud_final_assembly_renderer_tbd"].provider_type == "CLOUD_FINAL_ASSEMBLY_RENDERER"
    assert by_key["cloud_final_assembly_renderer_tbd"].recommendation == "REQUIRED_GAP"
    assert by_key["envato_manual_library"].provider_type == "DEFERRED_MANUAL_LIBRARY"
    assert by_key["envato_manual_library"].is_enabled is False
    assert by_key["mock_media_provider"].provider_type == "MOCK_PROVIDER"
    assert by_key["elevenlabs_flash_turbo"].supports_real_execution is False
    assert by_key["creatomate_essential_2k"].monthly_budget_assumption["plan"] == "ESSENTIAL_2K"

    caps = {
        (entry.provider_key, entry.job_type): entry
        for entry in db_session.scalars(select(ProviderCapabilityMatrixEntry)).all()
    }
    assert caps[("elevenlabs_flash_turbo", "VOICE_GENERATION")].capability == "SUPPORTED"
    assert caps[("GOOGLE_VERTEX_VEO", "AI_HERO_GENERATION")].capability == "SUPPORTED"
    assert caps[("GOOGLE_VERTEX_VEO", "AI_HERO_GENERATION")].max_duration_seconds == Decimal("8.000000")
    assert caps[("creatomate_essential_2k", "SHORT_RENDER")].capability == "SUPPORTED"
    assert caps[("creatomate_essential_2k", "LONG_FORM_FINAL_RENDER")].capability == "BLOCKED_BY_PLAN"


def test_render_routing_enforces_creatomate_essential_boundary_and_cloud_gap(db_session) -> None:
    router = MediaRenderJobRouterService(db_session)
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="THUMBNAIL_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="SHORT_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="TITLE_CARD_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="DIAGRAM_CARD_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="STAT_CARD_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="LOWER_THIRD_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="HERO_COMPOSITION_RENDER")).selected_provider_key == "creatomate_essential_2k"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_HERO_GENERATION")).selected_provider_key == "GOOGLE_VERTEX_VEO"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_METAPHOR_GENERATION")).selected_provider_key == "GOOGLE_VERTEX_VEO"
    assert router.decide(data=MediaRenderRoutingDecisionRequest(job_type="VOICE_GENERATION")).selected_provider_key == "elevenlabs_flash_turbo"
    too_long_hero = router.decide(data=MediaRenderRoutingDecisionRequest(job_type="AI_HERO_GENERATION", target_duration_seconds=Decimal("10")))
    assert too_long_hero.routing_result == "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"

    blocked = router.decide(data=MediaRenderRoutingDecisionRequest(job_type="LONG_FORM_FINAL_RENDER"))
    assert blocked.routing_result == "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"
    assert blocked.selected_provider_key is None
    assert "CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER" in blocked.technical_appendix["reason_codes"]

    unknown = router.decide(data=MediaRenderRoutingDecisionRequest(job_type="NOT_A_JOB"))
    assert unknown.routing_result == "BLOCKED_UNKNOWN_PROVIDER"

    cloud_role = MediaProviderRoleProfile(
        provider_key="cloud_final_renderer_test",
        provider_name="Cloud Final Renderer Test",
        provider_type="CLOUD_FINAL_ASSEMBLY_RENDERER",
        role_description="Configured test final renderer.",
        recommendation="CORE_QUALITY_LAYER",
        is_enabled=True,
        is_real_provider=True,
        supports_real_execution=True,
        monthly_budget_assumption={"mode": "TEST"},
    )
    db_session.add(cloud_role)
    db_session.flush()
    db_session.add(
        ProviderCapabilityMatrixEntry(
            provider_key=cloud_role.provider_key,
            provider_type=cloud_role.provider_type,
            job_type="LONG_FORM_FINAL_RENDER",
            capability="SUPPORTED",
            supported_aspect_ratios=["16:9"],
            supported_outputs=["mp4"],
            capability_reason="Configured test final renderer supports long-form final assembly.",
        )
    )
    db_session.flush()
    routed = router.decide(data=MediaRenderRoutingDecisionRequest(job_type="LONG_FORM_FINAL_RENDER"))
    assert routed.routing_result == "ROUTED"
    assert routed.selected_provider_key == "cloud_final_renderer_test"


def test_capability_budget_license_and_reuse_gates(db_session, qualification_factory) -> None:
    MediaProviderRoleService(db_session).ensure_matrix()
    capability_gate = ProviderCapabilityGateService(db_session)
    blocked = capability_gate.check(
        data=ProviderCapabilityGateCheckRequest(job_type="LONG_FORM_FINAL_RENDER", provider_key="creatomate_essential_2k")
    )
    assert blocked.decision == "BLOCK"
    assert "CREATOMATE_ESSENTIAL_NOT_FINAL_RENDERER" in blocked.reason_codes

    short_ok = capability_gate.check(
        data=ProviderCapabilityGateCheckRequest(
            job_type="SHORT_RENDER",
            provider_key="creatomate_essential_2k",
            target_duration_seconds=Decimal("45"),
            target_aspect_ratio="9:16",
        )
    )
    assert short_ok.decision == "PASS"
    bad_duration = capability_gate.check(
        data=ProviderCapabilityGateCheckRequest(
            job_type="SHORT_RENDER",
            provider_key="creatomate_essential_2k",
            target_duration_seconds=Decimal("65"),
            target_aspect_ratio="9:16",
        )
    )
    assert bad_duration.decision == "BLOCK"

    budget_service = MediaProviderBudgetService(db_session)
    assert budget_service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="CLOUD_TEMPLATE_RENDERER_LIGHT",
            provider_key="creatomate_essential_2k",
            estimated_usage_usd=Decimal("10"),
        )
    ).decision == "PASS"
    exceeded = budget_service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="CLOUD_FINAL_ASSEMBLY_RENDERER",
            estimated_render_count=11,
        )
    )
    assert exceeded.decision == "BLOCK"
    assert exceeded.budget_state == "EXCEEDED"
    unknown = budget_service.check(
        data=MediaProviderBudgetCheckRequest(provider_type="AI_VIDEO_HERO_PROVIDER", provider_key="GOOGLE_VERTEX_VEO")
    )
    assert unknown.decision == "REVIEW_REQUIRED"
    assert unknown.budget_state == "UNKNOWN"
    veo_default_clip = budget_service.check(
        data=MediaProviderBudgetCheckRequest(
            provider_type="AI_VIDEO_HERO_PROVIDER",
            provider_key="GOOGLE_VERTEX_VEO",
            estimated_usage_seconds=Decimal("8"),
        )
    )
    assert veo_default_clip.decision == "PASS"
    assert veo_default_clip.budget_state == "OK"

    scope = qualification_factory.channel_scope(name="M10.2 License")
    license_result = LicenseEvidenceGateService(db_session).check(
        data=LicenseEvidenceGateCheckRequest(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            asset_ref="pexels:asset-1",
            source_provider_type="FREE_FALLBACK_PROVIDER",
            license_status="UNKNOWN",
        )
    )
    assert license_result.decision == "BLOCK"
    assert "LICENSE_EVIDENCE_REQUIRED" in license_result.reason_codes

    reuse = ReusedContentRiskGateService(db_session).check(
        data=ReusedContentRiskGateCheckRequest(template_only=True, original_script_present=False)
    )
    assert reuse.decision == "REVIEW_REQUIRED"
    assert "REUSED_CONTENT_REVIEW_REQUIRED" in reuse.reason_codes


def test_packages_and_asset_planning_create_placeholders_without_provider_calls(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    before_provider_attempts = db_session.scalar(select(func.count()).select_from(ProviderAttempt))

    long_package = LongFormRenderPackageService(db_session).create(
        video_project_id=flow.project.id,
        data=LongFormRenderPackageCreate(
            voice_timeline_id=flow.production_run.voice_timeline_snapshot_id,
            caption_track_id=flow.production_run.caption_track_snapshot_id,
            visual_plan_id=flow.production_run.visual_plan_snapshot_id,
            ai_hero_asset_refs=[{"type": "AIHeroAsset", "id": "planned"}],
            creatomate_asset_refs=[{"type": "CreatomateRenderAsset", "id": "planned"}],
            thumbnail_variant_refs=[{"type": "ThumbnailVariant", "id": "planned"}],
            render_manifest={"mode": "quality_first_250"},
        ),
    )
    assert long_package.package_state == "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"
    assert long_package.final_renderer_provider_key is None

    candidate = ShortCandidateExtractionService(db_session).extract_for_project(
        video_project_id=flow.project.id,
        data=ShortCandidateExtractRequest(max_candidates=1),
    )[0]
    short_package = ShortRenderPackageService(db_session).create(
        short_candidate_id=candidate.id,
        data=ShortRenderPackageCreate(target_duration_seconds=Decimal("45"), target_aspect_ratio="9:16"),
    )
    assert short_package.package_state == "READY_FOR_TEMPLATE_RENDER"
    assert short_package.renderer_provider_key == "creatomate_essential_2k"
    with pytest.raises(ValidationFailureError):
        ShortRenderPackageService(db_session).create(
            short_candidate_id=candidate.id,
            data=ShortRenderPackageCreate(target_duration_seconds=Decimal("59"), target_aspect_ratio="9:16"),
        )

    hero = AIHeroAssetPlanningService(db_session).plan(
        video_project_id=flow.project.id,
        data=AIHeroAssetPlanRequest(prompt="Premium metaphor scene.", intended_usage="OPENING_HOOK", duration_seconds=Decimal("8")),
    )
    assert hero.generation_state == "READY_FOR_PROVIDER"
    assert hero.provider_key == "GOOGLE_VERTEX_VEO"
    assert hero.asset_ref is None
    generated = AIHeroGenerationService(db_session).execute(asset_id=hero.id)
    assert generated.generation_state == "READY_FOR_PROVIDER"
    assert generated.real_execution_attempted is False
    assert generated.estimated_cost_usd == Decimal("0.80")
    assert "VEO_REAL_EXECUTION_DISABLED" in generated.reason_codes

    creatomate = CreatomateRenderAssetPlanningService(db_session).plan(
        video_project_id=flow.project.id,
        data=CreatomateRenderAssetPlanRequest(job_type="TITLE_CARD_RENDER", template_key="title-card-v1"),
    )
    assert creatomate.render_state == "READY_FOR_PROVIDER"
    assert creatomate.output_ref is None

    thumbnails = ThumbnailVariantPlanningService(db_session).plan(
        video_project_id=flow.project.id,
        data=ThumbnailVariantPlanRequest(
            variants=[ThumbnailVariantInput(variant_label="A", title_text="Clear Promise", subtitle_text="Quality-first")]
        ),
    )
    assert thumbnails[0].state == "READY_FOR_PROVIDER"
    assert thumbnails[0].output_ref is None

    with pytest.raises(ValidationFailureError):
        FinalMediaRefService(db_session).create(
            data=FinalMediaRefCreate(
                company_id=flow.company.id,
                channel_workspace_id=flow.channel.id,
                video_project_id=flow.project.id,
                media_type="LONG_FORM_FINAL",
                file_ref="provider://fake/long.mp4",
            )
        )
    valid_ref = FinalMediaRefService(db_session).create(
        data=FinalMediaRefCreate(
            company_id=flow.company.id,
            channel_workspace_id=flow.channel.id,
            video_project_id=flow.project.id,
            media_type="PREVIEW",
            file_ref=str(tmp_path / "fixture-preview.mp4"),
        )
    )
    assert db_session.scalar(select(func.count()).select_from(FinalMediaRef)) == 1
    assert valid_ref.file_ref.endswith("fixture-preview.mp4")
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == before_provider_attempts


def test_m10_2_api_smoke_and_no_real_execution_routes(db_session, qualification_factory) -> None:
    client = TestClient(create_app())
    roles = client.get("/media-provider-roles")
    assert roles.status_code == 200
    assert any(item["provider_key"] == "creatomate_essential_2k" for item in roles.json())

    routed = client.post("/media-render-routing/decide", json={"job_type": "SHORT_RENDER"})
    assert routed.status_code == 200
    assert routed.json()["selected_provider_key"] == "creatomate_essential_2k"

    blocked = client.post("/media-render-routing/decide", json={"job_type": "LONG_FORM_FINAL_RENDER"})
    assert blocked.status_code == 200
    assert blocked.json()["routing_result"] == "BLOCKED_PROVIDER_CAPABILITY_REQUIRED"

    gate = client.post(
        "/media-provider-gates/capability/check",
        json={"job_type": "LONG_FORM_FINAL_RENDER", "provider_key": "creatomate_essential_2k"},
    )
    assert gate.status_code == 200
    assert gate.json()["decision"] == "BLOCK"

    assert db_session.scalar(select(func.count()).select_from(MediaRenderRoutingDecision)) >= 2
