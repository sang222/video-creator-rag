from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.contracts.asset_acquisition import AssetRequest
from app.core.config import Settings, get_settings
from app.main import create_app
from app.services.as1_rehearsal import AS1LocalFixtureRehearsal, _native_plan
from app.services.asset_request_compiler import AssetRequestCompiler, CompilationEvidence
from app.services.local_cleanup import LocalCleanupService
from app.services.local_project_workspace import AssetDownloadStateMachine, LocalProjectWorkspaceService
from app.services.media_normalizer import MediaNormalizer
from app.services.native_render_plan import stable_hash
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.production_archive import (
    ArchivePurgeStateMachine,
    DriveArchiveFixtureVerifier,
    ProductionArchivePathBuilder,
)
from app.services.provider_asset_manifests import (
    PexelsDownloadPlanBuilder,
    PexelsRateLimitMetadataParser,
    PexelsRequestBuilder,
    PexelsRenditionSelector,
    PexelsResponseParser,
    build_ai_hero_request,
)
from app.services.stock_candidate_ranker import StockCandidateRanker
from app.contracts.asset_acquisition import ChannelVisualStrategyProfile, FormatIdentitySnapshot, ProviderUsagePolicy


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/as1"


def _asset_request(**changes) -> AssetRequest:
    payload = {
        "request_id": "asset-stock",
        "scene_id": "scene-stock",
        "source_segment_ids": ["seg-1"],
        "purpose": "SUPPORT",
        "requested_role": "SUPPORTING_STOCK",
        "semantic_visual_intent": "small team collaborating around laptop in clean office",
        "required_orientation": "landscape",
        "minimum_resolution": "1280x720",
        "preferred_resolution": "1920x1080",
        "minimum_duration_seconds": 4,
        "maximum_duration_seconds": 10,
        "crop_policy": "SAFE_CENTER_CROP_WITH_SEMANTIC_REVIEW",
        "person_policy": "NO_RECURRING_HOST",
        "logo_text_policy": "REJECT_VISIBLE_LOGO_OR_EMBEDDED_TEXT",
        "evidence_usage_policy": "NOT_FACTUAL_EVIDENCE",
        "fallback_order": ["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"],
        "projected_cost_class": "LOW",
        "human_review_required": True,
    }
    payload.update(changes)
    return AssetRequest(**payload, request_hash=stable_hash(payload))


def _compiler_inputs():
    plan = _native_plan(project_id="as1-small-team-ai-project", package_id="as1-small-team-ai-package")
    identity = FormatIdentitySnapshot(
        contract_ref=plan.format_identity_contract_ref,
        contract_hash=plan.format_identity_contract_hash,
        status="APPROVED",
        channel_id="small-team-ai",
        character_policy_mode="NO_CHARACTER",
        allowed_asset_roles=["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"],
    )
    strategy = ChannelVisualStrategyProfile(
        profile_ref="strategy-b",
        profile_hash=stable_hash("strategy-b"),
        channel_id="small-team-ai",
        strategy_key="NR2_B_BALANCED",
    )
    policy = ProviderUsagePolicy(policy_ref="policy", policy_hash=stable_hash("policy"))
    evidence = CompilationEvidence("originality", "originality-hash", ("claim",), "disclosure")
    return plan, identity, strategy, policy, evidence


def _parsed_candidates():
    return PexelsResponseParser().parse(json.loads((FIXTURES / "pexels_response.json").read_text(encoding="utf-8")))


def test_native_visual_is_backbone_and_first_fallback_priority():
    plan, identity, strategy, policy, evidence = _compiler_inputs()
    compiled = AssetRequestCompiler().compile(plan, format_identity=identity, strategy_profile=strategy, provider_policy=policy, evidence=evidence)
    assert compiled.native_request_count == 3
    assert compiled.supporting_stock_request_count == 1
    assert compiled.ai_hero_request_count == 1
    assert all(request.fallback_order[-1] == "NATIVE_VISUAL" for request in compiled.requests)
    assert compiled.provider_execution_allowed is False


def test_strategy_b_is_channel_scoped_and_character_conflict_blocks():
    plan, identity, strategy, policy, evidence = _compiler_inputs()
    plan.channel_id = "other-channel"
    identity.channel_id = "other-channel"
    strategy.channel_id = "other-channel"
    with pytest.raises(ValueError, match="STRATEGY_B_CHANNEL_SCOPE_VIOLATION"):
        AssetRequestCompiler().compile(plan, format_identity=identity, strategy_profile=strategy, provider_policy=policy, evidence=evidence)
    plan, identity, strategy, policy, evidence = _compiler_inputs()
    identity.character_policy_mode = "CHARACTER_ALLOWED"
    with pytest.raises(ValueError, match="CHARACTER_POLICY_CONFLICT"):
        AssetRequestCompiler().compile(plan, format_identity=identity, strategy_profile=strategy, provider_policy=policy, evidence=evidence)


def test_unsupported_provider_and_format_role_contradiction_block():
    plan, identity, strategy, policy, evidence = _compiler_inputs()
    policy.supported_providers = ["NATIVE", "PEXELS"]
    with pytest.raises(ValueError, match="UNSUPPORTED_PROVIDER"):
        AssetRequestCompiler().compile(plan, format_identity=identity, strategy_profile=strategy, provider_policy=policy, evidence=evidence)
    plan, identity, strategy, policy, evidence = _compiler_inputs()
    identity.allowed_asset_roles = ["NATIVE_VISUAL"]
    with pytest.raises(ValueError, match="ASSET_ROLE_CONTRADICTS_FORMAT_IDENTITY"):
        AssetRequestCompiler().compile(plan, format_identity=identity, strategy_profile=strategy, provider_policy=policy, evidence=evidence)


def test_stock_cannot_be_factual_evidence_or_recurring_host():
    with pytest.raises(ValueError, match="STOCK_FACTUAL_EVIDENCE_FORBIDDEN"):
        _asset_request(evidence_usage_policy="FACTUAL_EVIDENCE")
    with pytest.raises(ValueError, match="STOCK_RECURRING_HOST_FORBIDDEN"):
        _asset_request(person_policy="RECURRING_HOST")


def test_google_veo_hero_requires_allowed_reason_and_filler_blocks():
    with pytest.raises(ValueError, match="AI_HERO_FILLER_FORBIDDEN"):
        _asset_request(requested_role="AI_HERO", purpose="FILLER", projected_cost_class="MEDIUM")
    invalid = _asset_request(requested_role="AI_HERO", purpose="DECORATION", projected_cost_class="MEDIUM")
    with pytest.raises(ValueError, match="AI_HERO_REASON_NOT_ALLOWED"):
        build_ai_hero_request(
            invalid,
            package_id="pkg",
            project_id="project",
            channel_id="small-team-ai",
            prompt_text="abstract no-person metaphor",
            provider_resolution_policy_ref="policy://fixture",
        )


def test_pexels_query_plan_is_bounded_english_structured_and_secret_free():
    plan = PexelsQueryPlanner().plan(_asset_request(), per_page=40)
    assert 2 <= len(plan.queries) <= 4
    assert all(query.isascii() and len(query) <= 80 for query in plan.queries)
    assert plan.endpoint == "/v1/videos/search" and plan.per_page == 40
    serialized = plan.model_dump_json()
    assert "PEXELS_API_KEY" not in serialized and "api_key" not in serialized.lower()
    request = PexelsRequestBuilder().build(plan, plan.queries[0])
    assert request["endpoint"] == "/v1/videos/search"
    assert isinstance(request["query_params"], dict) and request["network_execution_allowed"] is False
    assert "api_key" not in request["query_params"]
    assert PexelsRateLimitMetadataParser().parse({"X-Ratelimit-Limit": "200"})["limit"] == 200


def test_pexels_query_unsupported_options_and_unsafe_concepts_block():
    with pytest.raises(ValueError, match="PEXELS_SIZE_UNSUPPORTED"):
        PexelsQueryPlanner().plan(_asset_request(), size_preference="original")
    with pytest.raises(ValueError, match="PEXELS_UNSAFE_QUERY_CONCEPT"):
        PexelsQueryPlanner().plan(_asset_request(semantic_visual_intent="fake testimonial proof"))


def test_candidate_ranking_is_deterministic_multidimensional_and_reuse_aware():
    candidates = _parsed_candidates()
    ranker = StockCandidateRanker()
    first = ranker.rank(_asset_request(), candidates, previous_asset_usage_refs=["old-asset"])
    second = ranker.rank(_asset_request(), candidates, previous_asset_usage_refs=["old-asset"])
    assert first == second
    assert first.selected_candidate_id == "pexels-1001"
    assert first.rejected_candidates[0].candidate_id == "pexels-1002"
    assert "SAME_ASSET_REUSE_RISK_REPRESENTED" in first.ranking_reason_codes
    assert len(first.candidate_scores[0].dimensions) == 12 and first.selection_requires_human_review


def test_rendition_selector_chooses_compatible_mp4_and_redacts_download_url():
    candidate = _parsed_candidates()[0]
    rendition = PexelsRenditionSelector().select(candidate, _asset_request())
    assert rendition["id"] == 5001 and rendition["file_type"] == "video/mp4"
    plan = PexelsDownloadPlanBuilder().build(candidate, rendition, _asset_request())
    assert plan.selected_download_url_reference.startswith("volatile://")
    assert "token=" not in plan.model_dump_json()


def test_download_requires_file_checksum_and_cleans_part_on_failure(tmp_path):
    service = LocalProjectWorkspaceService(tmp_path / "work", max_file_size_bytes=1000)
    service.create("project")
    receipt = service.fixture_download(project_id="project", request_id="asset", fixture_source=FIXTURES / "pexels_supporting_fixture.mp4", destination_relative="source/pexels/asset.mp4")
    assert Path(receipt.local_path).is_file() and receipt.sha256 and receipt.state == "ASSET_DOWNLOADED"
    with pytest.raises(ValueError, match="ASSET_SUCCESS_REQUIRES_FILE_AND_CHECKSUM"):
        AssetDownloadStateMachine().transition("ASSET_DOWNLOADING", "ASSET_DOWNLOADED")
    with pytest.raises(OSError, match="LOCAL_FIXTURE_INJECTED_DOWNLOAD_FAILURE"):
        service.fixture_download(project_id="project", request_id="fail", fixture_source=FIXTURES / "pexels_supporting_fixture.mp4", destination_relative="source/pexels/fail.mp4", fail_after_bytes=1)
    assert not service.path("project", "source/pexels/fail.mp4.part").exists()


def test_workspace_traversal_and_symlink_escape_blocked(tmp_path):
    service = LocalProjectWorkspaceService(tmp_path / "work")
    service.create("project")
    with pytest.raises(ValueError, match="WORKSPACE_PATH_TRAVERSAL"):
        service.path("project", "../escape")
    outside = tmp_path / "outside"
    outside.mkdir()
    (service.path("project", "source") / "escape-link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="WORKSPACE_PATH_ESCAPE|WORKSPACE_SYMLINK_ESCAPE"):
        service.path("project", "source/escape-link/asset.mp4")


def test_normalization_command_plan_is_sanitized_and_not_executable(tmp_path):
    manifest = MediaNormalizer().compile_video_plan(input_asset_ref="asset", input_asset_hash="hash", input_path=tmp_path / "in.mp4", output_path=tmp_path / "out.mp4", width=1920, height=1080)
    assert manifest.execution_allowed is False
    assert "bt709" in manifest.sanitized_ffmpeg_argv_plan
    with pytest.raises(ValueError, match="NORMALIZATION_ARGV_UNSAFE"):
        MediaNormalizer().compile_video_plan(input_asset_ref="asset", input_asset_hash="hash", input_path=Path("bad;name.mp4"), output_path=tmp_path / "out.mp4", width=1920, height=1080)


def test_archive_path_rejects_nested_root_and_unknown_segments():
    builder = ProductionArchivePathBuilder()
    assert builder.build(company_id="company", channel_workspace_id="channel", video_project_id="project").endswith("production-package-v1")
    with pytest.raises(ValueError, match="ARCHIVE_NESTED_CONFIGURED_ROOT_FORBIDDEN"):
        builder.validate("VCOS/company_company/channel_channel/project_project/production-package-v1")
    with pytest.raises(ValueError, match="ARCHIVE_UNKNOWN_OR_INVALID_SCOPE_SEGMENT"):
        builder.build(company_id="unknown", channel_workspace_id="channel", video_project_id="project")


def test_archive_purge_state_machine_blocks_unverified_purge():
    machine = ArchivePurgeStateMachine()
    with pytest.raises(ValueError, match="ARCHIVE_PURGE_TRANSITION_FORBIDDEN"):
        machine.transition("ARCHIVE_UPLOADING", "LOCAL_PURGED")
    with pytest.raises(ValueError, match="ARCHIVE_PURGE_TRANSITION_FORBIDDEN"):
        machine.transition("ARCHIVE_UPLOADED_UNVERIFIED", "LOCAL_PURGED")


def test_rehearsal_covers_archive_exclusions_verified_cleanup_and_idempotency(tmp_path):
    result = AS1LocalFixtureRehearsal().run(workspace_root=tmp_path / "work", fixture_dir=FIXTURES)
    assert result["request_counts"] == {"native": 3, "supporting_stock": 1, "ai_hero": 1}
    assert result["provider_calls_made"] is False and result["drive_calls_made"] is False
    assert result["transport"] == "LOCAL_FIXTURE_ONLY" and result["production_eligible"] is False
    assert result["archive_state"] == "VERIFIED"
    assert result["cleanup_status"] == "COMPLETED" and result["cleanup_idempotency_status"] == "NOOP_IDEMPOTENT"
    ai_manifest = json.loads((tmp_path / "work/as1-small-team-ai-project/manifests/ai_generation_manifest.json").read_text())
    assert ai_manifest["external_operation_id"] is None and ai_manifest["provider_status"] == "PLANNED"
    hero_request = json.loads((tmp_path / "work/as1-small-team-ai-project/manifests/ai_hero_asset_request.json").read_text())
    assert hero_request["required_duration_seconds"] == 8
    manifest = json.loads((tmp_path / "work/as1-small-team-ai-project/manifests/production_archive_manifest.json").read_text())
    from app.services.production_archive import ROLE_ARCHIVE_PATHS
    assert manifest["required_roles_complete"] and len(manifest["files"]) == len(ROLE_ARCHIVE_PATHS)
    assert len(manifest["excluded_paths"]) == 2
    assert all("rejected" not in entry["source_path"] and "/normalized/" not in entry["source_path"] for entry in manifest["files"])


def test_one_required_file_mismatch_blocks_all_cleanup(tmp_path):
    AS1LocalFixtureRehearsal().run(workspace_root=tmp_path / "work", fixture_dir=FIXTURES)
    manifests = tmp_path / "work/as1-small-team-ai-project/manifests"
    archive_data = json.loads((manifests / "production_archive_manifest.json").read_text())
    from app.contracts.asset_acquisition import ProductionArchiveManifest

    archive = ProductionArchiveManifest(**archive_data)
    fake = [{"archive_path": entry.expected_archive_path, "size_bytes": entry.size_bytes, "sha256": entry.sha256} for entry in archive.files]
    fake[0]["sha256"] = "mismatch"
    receipt = DriveArchiveFixtureVerifier().verify(manifest=archive, configured_root_folder_id_reference="settings://root", root_relative_folder_path="company_c/channel_ch/project_p/production-package-v1", fixture_files=fake)
    cleanup = LocalCleanupService().evaluate(project_id=archive.project_id, archive_receipt=receipt)
    assert receipt.archive_state == "FAILED" and cleanup.cleanup_status == "BLOCKED" and cleanup.deleted_files == []


def test_provider_secret_flags_off_and_no_secret_serialization(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-secret-value")
    monkeypatch.setenv("GEMINI_API_KEY", "google_veo-secret-value")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "eleven-secret-value")
    settings = Settings()
    assert not settings.pexels_real_execution_enabled
    assert not settings.veo_real_generation_enabled
    assert not settings.elevenlabs_real_execution_enabled
    assert not settings.provider_production_execution_enabled
    dumped = str(settings.model_dump())
    assert "pexels-secret-value" not in dumped and "google_veo-secret-value" not in dumped and "eleven-secret-value" not in dumped


def test_no_provider_or_drive_execution_and_no_forbidden_entities_in_as1_services():
    source_files = [
        ROOT / "app/services/asset_request_compiler.py",
        ROOT / "app/services/pexels_query_planner.py",
        ROOT / "app/services/provider_asset_manifests.py",
        ROOT / "app/services/local_project_workspace.py",
        ROOT / "app/services/production_archive.py",
        ROOT / "app/services/local_cleanup.py",
        ROOT / "app/services/as1_rehearsal.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    for forbidden in ("urlopen(", "requests.", "httpx.", "GoogleDriveUploadService", "ProviderJobSnapshot", "PaidProviderCallLedger", "HumanUploadTask", "FinalMediaRef", "CloudMediaRef"):
        assert forbidden not in source
    assert "sqlalchemy" not in source and "db_session" not in source


def test_read_only_api_exposes_plan_workspace_and_archive_without_actions(tmp_path, monkeypatch):
    AS1LocalFixtureRehearsal().run(workspace_root=tmp_path / "work", fixture_dir=FIXTURES)
    monkeypatch.setenv("VCOS_LOCAL_PROJECT_WORKSPACE_ROOT", str(tmp_path / "work"))
    get_settings.cache_clear()
    client = TestClient(create_app())
    plan = client.get("/video-packages/as1-small-team-ai-package/asset-acquisition-plan")
    workspace = client.get("/video-projects/as1-small-team-ai-project/local-workspace-summary")
    archive = client.get("/video-projects/as1-small-team-ai-project/archive-readiness")
    assert plan.status_code == workspace.status_code == archive.status_code == 200
    assert plan.json()["provider_execution_disabled"] is True
    assert archive.json()["purge_eligibility"] is True
    methods = {(route.path, method) for route in create_app().routes for method in getattr(route, "methods", set())}
    assert all(method == "GET" for path, method in methods if "asset-acquisition-plan" in path or "local-workspace-summary" in path or "archive-readiness" in path)
    get_settings.cache_clear()
