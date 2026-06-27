from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.contracts import (
    BuildUploadCardsRequest,
    ContentDerivativeGraphEdgeCreate,
    CrossPlatformFunnelPackageCreate,
    DerivativeOriginalityCheckCreate,
    PromoteShortToLongCandidateCreate,
    ReusableArtifactCreate,
    ShortCandidateExtractRequest,
    ShortCandidateRankRequest,
)
from app.db.models import (
    ContentDerivativeGraphEdge,
    HumanUploadTask,
    LLMRouteAttempt,
    LLMRunSnapshot,
    ProviderAttempt,
    ShortCandidate,
    UploadCard,
    VideoProject,
)
from app.main import create_app
from app.providers.base import ProviderResponse
from app.providers.ollama import OllamaChatRequest, OllamaLLMProvider
from app.services import (
    CrossPlatformFunnelPackageService,
    DerivativeGraphService,
    DerivativeOriginalityService,
    LLMRouterConfigLoader,
    LLMRouterService,
    PromoteShortToLongCandidateService,
    ReusableArtifactService,
    ShortCandidateExtractionService,
    ShortCandidateRankingService,
)
from app.services.m10_1 import FINAL_LANES, configured_router_models

from .helpers.git_checks import tag_exists
from .helpers.repo_scanners import all_scope_violations


M10_1_TABLES = {
    "llm_router_profiles",
    "llm_router_lanes",
    "llm_model_profiles",
    "llm_route_attempts",
    "content_derivative_graph_edges",
    "short_candidates",
    "short_candidate_scores",
    "short_render_plans",
    "promote_short_to_long_candidates",
    "reusable_artifacts",
    "asset_reuse_index_entries",
    "derivative_originality_checks",
    "originality_budgets",
    "derivative_release_plans",
    "cross_platform_funnel_packages",
    "upload_cards",
    "human_upload_tasks",
    "usage_savings_ledger_entries",
}

FORBIDDEN_M10_2_M11_TABLES = {
    "media_provider_routers",
    "provider_capability_matrices",
    "provider_capability_gates",
    "elevenlabs_provider_runs",
    "creatomate_provider_runs",
    "ai_hero_provider_runs",
    "cloud_final_renderer_runs",
    "dashboard_widgets",
    "operator_cockpit_views",
    "external_post_records",
}

EXPECTED_LANES = [
    "cheap_structured",
    "default_multimodal",
    "visual_creative_review",
    "long_context_text",
    "engineering_architect",
    "gatekeeper_soft_review",
]


class SequenceProvider:
    provider_key = "OLLAMA"

    def __init__(self, responses: list[ProviderResponse]):
        self.responses = responses
        self.calls: list[OllamaChatRequest] = []

    def chat(self, *, request: OllamaChatRequest) -> ProviderResponse:
        self.calls.append(request)
        return self.responses.pop(0)


def test_m10_1_preflight_migration_catalog_defaults_and_scope(engine, db_session, qualification_factory) -> None:
    assert tag_exists("m10-learning-review-queue") is True
    tables = set(inspect(engine).get_table_names())
    assert M10_1_TABLES <= tables
    assert tables.isdisjoint(FORBIDDEN_M10_2_M11_TABLES)
    with engine.connect() as connection:
        assert connection.execute(text("select version_num from alembic_version")).scalar_one() == "0019_m12_1_prompt_registry"
        defaults = connection.execute(
            text(
                """
                select table_name, column_name, column_default
                from information_schema.columns
                where table_name in ('llm_router_lanes','short_candidates','upload_cards','cross_platform_funnel_packages')
                  and column_name in ('fallback_models','allowed_task_types','caption_ids','production_cost_estimate','hashtags','bridge_strategy')
                """
            )
        ).all()
    default_map = {(row.table_name, row.column_name, row.column_default) for row in defaults}
    assert ("llm_router_lanes", "fallback_models", "'[]'::jsonb") in default_map
    assert ("llm_router_lanes", "allowed_task_types", "'[]'::jsonb") in default_map
    assert ("short_candidates", "caption_ids", "'[]'::jsonb") in default_map
    assert ("short_candidates", "production_cost_estimate", "'{}'::jsonb") in default_map
    assert ("upload_cards", "hashtags", "'[]'::jsonb") in default_map
    assert ("cross_platform_funnel_packages", "bridge_strategy", "'{}'::jsonb") in default_map

    qualification_factory.seed_all()
    assert all_scope_violations(engine) == []
    routes = {route.path for route in create_app().routes}
    assert {route for route in routes if "dashboard" in route} <= {
        "/dashboard/command-center",
        "/dashboard/queues",
        "/dashboard/queues/{queue_type}",
        "/uploaded-videos/{uploaded_video_id}/dashboard",
    }
    route_text = " ".join(routes).lower()
    assert "auto-upload" not in route_text
    assert "publish-now" not in route_text


def test_llm_router_lanes_disabled_guard_no_provider_call_and_smoke_skip(db_session, monkeypatch) -> None:
    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("VCOS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("VCOS_LLM_ROUTER_REAL_SMOKE", "false")
    loader = LLMRouterConfigLoader(db_session)
    lanes = loader.list_lanes()
    assert [lane.lane_name for lane in lanes] == EXPECTED_LANES
    assert all("glm" not in lane.primary_model.lower() for lane in lanes)
    lane_by_name = {lane.lane_name: lane for lane in lanes}
    assert lane_by_name["long_context_text"].primary_model == "deepseek-v4-flash:cloud"
    assert lane_by_name["long_context_text"].fallback_models == ["nemotron-3-super:cloud"]
    assert lane_by_name["long_context_text"].premium_model == "deepseek-v4-flash:cloud"
    assert lane_by_name["gatekeeper_soft_review"].primary_model == "nemotron-3-super:cloud"
    assert lane_by_name["gatekeeper_soft_review"].fallback_models == ["deepseek-v4-flash:cloud"]
    assert lane_by_name["gatekeeper_soft_review"].premium_model == "deepseek-v4-flash:cloud"
    assert "nemotron-3-ultra:cloud" not in configured_router_models()
    assert "nemotron-3-ultra:cloud" not in str(FINAL_LANES)
    assert all(not lane.real_execution_enabled for lane in lanes)

    provider = SequenceProvider([ProviderResponse(ok=True, output={"content": "should not be called"})])
    result = LLMRouterService(db_session, provider=provider).route(
        lane_name="cheap_structured",
        prompt='{"task":"metadata"}',
        requested_task_type="metadata_generation",
        response_format="json",
    )
    assert result.status == "SKIPPED"
    assert provider.calls == []
    assert db_session.scalar(select(func.count()).select_from(LLMRouteAttempt)) == 1
    assert db_session.scalar(select(func.count()).select_from(LLMRunSnapshot)) == 1
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 0

    smoke = LLMRouterService(db_session, provider=provider).run_smoke_test()
    assert smoke["status"] == "SKIPPED"
    assert "OLLAMA_REAL_EXECUTION_DISABLED" in smoke["reason_codes"]


def test_ollama_payload_and_router_fallback_logging(db_session, monkeypatch) -> None:
    payload = OllamaLLMProvider().build_chat_payload(
        request=OllamaChatRequest(model="gpt-oss:20b-cloud", prompt="Return JSON.", response_format="json")
    )
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["messages"] == [{"role": "user", "content": "Return JSON."}]

    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("VCOS_LLM_PROVIDER", "ollama")
    provider = SequenceProvider(
        [
            ProviderResponse(ok=False, error_code="MODEL_NOT_FOUND", error_message="missing", retryable=False, latency_ms=2),
            ProviderResponse(
                ok=True,
                output={
                    "content": '{"ok":true}',
                    "json": {"ok": True},
                    "usage": {"prompt_eval_count": 3, "eval_count": 4, "total_duration_ms": 11},
                },
                latency_ms=3,
            ),
        ]
    )
    result = LLMRouterService(db_session, provider=provider).route(
        lane_name="cheap_structured",
        prompt='Return {"ok": true}',
        requested_task_type="json_schema_output",
        response_format="json",
    )
    assert result.status == "SUCCESS"
    assert result.selected_model == "qwen3.5:cloud"
    assert result.fallback_level == "FALLBACK"
    assert result.structured_output == {"ok": True}
    assert [call.model for call in provider.calls] == ["gpt-oss:20b-cloud", "qwen3.5:cloud"]
    assert db_session.scalar(select(func.count()).select_from(ProviderAttempt)) == 2
    assert db_session.scalar(select(func.count()).select_from(LLMRunSnapshot)) == 2
    attempts = list(db_session.scalars(select(LLMRouteAttempt).order_by(LLMRouteAttempt.created_at)).all())
    assert [attempt.status for attempt in attempts] == ["FAILED", "SUCCESS"]
    assert attempts[-1].prompt_eval_count == 3
    assert attempts[-1].eval_count == 4
    assert attempts[-1].total_duration_ms == 11
    assert db_session.scalars(select(LLMRunSnapshot).where(LLMRunSnapshot.estimated_cost.is_not(None))).all() == []


def test_short_candidates_originality_graph_reuse_funnel_and_promotion(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    extractor = ShortCandidateExtractionService(db_session)
    candidates = extractor.extract_for_project(
        video_project_id=flow.project.id,
        data=ShortCandidateExtractRequest(max_candidates=3),
    )
    assert 1 <= len(candidates) <= 3
    candidate = candidates[0]
    assert 0 < candidate.duration_ms < 59000
    assert candidate.caption_ids
    assert candidate.hook_line
    assert candidate.core_idea
    assert candidate.standalone_summary

    score = ShortCandidateRankingService(db_session).rank(
        short_candidate_id=candidate.id,
        data=ShortCandidateRankRequest(select_threshold=Decimal("50")),
    )
    db_session.refresh(candidate)
    assert score.total_score >= Decimal("50")
    assert candidate.candidate_state == "SELECTED_FOR_RENDER"

    edge = DerivativeGraphService(db_session).create_edge(
        data=ContentDerivativeGraphEdgeCreate(
            parent_video_project_id=flow.project.id,
            derivative_type="SHORT",
            transformation_summary="Selected standalone short from a parent long-form video.",
            new_value_added="Standalone hook, caption context, and platform-native bridge copy.",
            originality_score=Decimal("75"),
            reused_runtime_pct=Decimal("85"),
            source_refs=[{"type": "ShortCandidate", "id": str(candidate.id)}],
        )
    )
    assert edge.publish_allowed is True
    assert db_session.scalar(select(func.count()).select_from(ContentDerivativeGraphEdge)) == 1

    originality = DerivativeOriginalityService(db_session).create_check(
        data=DerivativeOriginalityCheckCreate(
            content_derivative_edge_id=edge.id,
            short_candidate_id=candidate.id,
            derivative_type="SHORT",
            standalone_value_ok=True,
            new_value_added_ok=True,
            reused_runtime_pct=Decimal("85"),
        )
    )
    assert originality.result == "PASS"
    blocked = DerivativeOriginalityService(db_session).create_check(
        data=DerivativeOriginalityCheckCreate(
            content_derivative_edge_id=edge.id,
            derivative_type="COMPILATION",
            standalone_value_ok=True,
            new_value_added_ok=False,
        )
    )
    assert blocked.result == "BLOCK"

    reusable = ReusableArtifactService(db_session).create(
        data=ReusableArtifactCreate(
            company_id=flow.company.id,
            channel_workspace_id=flow.channel.id,
            artifact_type="CAPTION_STYLE",
            content_hash="caption-style-hash",
            source_provider="manual_stock",
            license_status="LICENSED",
            reuse_scope="CHANNEL",
            max_reuse_policy={"max_uses": 5},
        )
    )
    assert reusable.license_status == "LICENSED"
    assert reusable.reuse_count == 0
    with pytest.raises(Exception):
        ReusableArtifactService(db_session).create(
            data=ReusableArtifactCreate(
                company_id=flow.company.id,
                artifact_type="STOCK_CLIP",
                content_hash="blocked-envato-api",
                source_provider="envato_api",
                license_status="UNKNOWN",
            )
        )

    package = CrossPlatformFunnelPackageService(db_session).create(
        data=CrossPlatformFunnelPackageCreate(
            parent_video_project_id=flow.project.id,
            selected_short_candidate_ids=[candidate.id],
        )
    )
    assert package.package_state == "READY_FOR_HUMAN_REVIEW"
    assert package.tiktok_package_status == "EXPORT_ONLY"
    cards = CrossPlatformFunnelPackageService(db_session).build_upload_cards(
        package_id=package.id,
        data=BuildUploadCardsRequest(platforms=["YOUTUBE_SHORTS", "TIKTOK", "FACEBOOK_REELS"]),
    )
    assert len(cards) == 3
    assert db_session.scalar(select(func.count()).select_from(UploadCard)) == 3
    assert db_session.scalar(select(func.count()).select_from(HumanUploadTask)) == 3
    assert set(inspect(db_session.bind).get_table_names()).isdisjoint({"external_post_records"})
    assert all(card.card_state == "READY" for card in cards)
    assert all("actual_video_id" in card.paste_back_required_fields for card in cards)

    project_count_before = db_session.scalar(select(func.count()).select_from(VideoProject))
    opportunity = PromoteShortToLongCandidateService(db_session).create(
        data=PromoteShortToLongCandidateCreate(
            source_short_candidate_id=candidate.id,
            winning_hook=candidate.hook_line,
            audience_signal={"youtube": {"views": 1000, "retention": 70}},
            suggested_long_topic="Expand the short into a new long-form narrative arc.",
            suggested_outline={"sections": ["new angle", "new examples", "operator takeaway"]},
            expected_watch_hour_potential="MEDIUM",
            evidence_refs=[{"type": "ShortCandidate", "id": str(candidate.id)}],
        )
    )
    assert opportunity.state == "READY_FOR_HUMAN_REVIEW"
    assert db_session.scalar(select(func.count()).select_from(VideoProject)) == project_count_before
    assert opportunity.audience_signal["youtube_analytics_only_authority"] is True
    assert opportunity.audience_signal["tiktok_facebook_analytics_loop_deferred"] is True


def test_api_routes_are_read_generation_only(db_session, qualification_factory, tmp_path) -> None:
    flow = qualification_factory.m6_full_flow(output_dir=tmp_path)
    db_session.commit()
    client = TestClient(create_app())
    lanes = client.get("/llm-router/lanes")
    assert lanes.status_code == 200
    assert [lane["lane_name"] for lane in lanes.json()] == EXPECTED_LANES
    response = client.post(f"/video-projects/{flow.project.id}/short-candidates/extract", json={"max_candidates": 1})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    route_text = " ".join(route.path for route in create_app().routes).lower()
    assert "upload-jobs" not in route_text
    assert "upload-attempts" not in route_text
    assert "auto-upload" not in route_text
    assert "auto-publish" not in route_text
    assert "publish-now" not in route_text
    assert "tiktok-analytics" not in route_text
    assert "facebook-analytics" not in route_text
