from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.contracts.asset_acquisition import ChannelVisualStrategyProfile, FormatIdentitySnapshot, ProviderUsagePolicy
from app.contracts.native_renderer import CanvasSpec, NativeRenderPlan, NativeRenderScene
from app.services.asset_request_compiler import AssetRequestCompiler, CompilationEvidence
from app.services.local_cleanup import LocalCleanupService
from app.services.local_project_workspace import LocalProjectWorkspaceService
from app.services.media_normalizer import MediaNormalizer
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.pexels_query_planner import PexelsQueryPlanner
from app.services.production_archive import (
    ArchiveSource,
    DriveArchiveFixtureVerifier,
    ProductionArchiveBuilder,
    ProductionArchivePathBuilder,
    ROLE_ARCHIVE_PATHS,
)
from app.services.provider_asset_manifests import (
    PexelsDownloadPlanBuilder,
    PexelsRenditionSelector,
    PexelsResponseParser,
    build_ai_hero_request,
    build_planned_ai_generation_manifest,
    build_stock_source_manifest,
)
from app.services.stock_candidate_ranker import StockCandidateRanker


class AS1LocalFixtureRehearsal:
    def run(self, *, workspace_root: Path, fixture_dir: Path) -> dict:
        project_id = "as1-small-team-ai-project"
        package_id = "as1-small-team-ai-package"
        workspace = LocalProjectWorkspaceService(workspace_root, minimum_free_bytes=1, max_file_size_bytes=5 * 1024 * 1024)
        summary = workspace.create(project_id)
        plan = _native_plan(project_id=project_id, package_id=package_id)
        format_identity = FormatIdentitySnapshot(
            contract_ref=plan.format_identity_contract_ref,
            contract_hash=plan.format_identity_contract_hash,
            status="APPROVED",
            channel_id="small-team-ai",
            character_policy_mode="NO_CHARACTER",
            allowed_asset_roles=["NATIVE_VISUAL", "SUPPORTING_STOCK", "AI_HERO"],
        )
        strategy = ChannelVisualStrategyProfile(
            profile_ref="nr2-small-team-ai-strategy-b",
            profile_hash=stable_hash({"channel": "small-team-ai", "strategy": "NR2_B_BALANCED"}),
            channel_id="small-team-ai",
            strategy_key="NR2_B_BALANCED",
        )
        provider_policy = ProviderUsagePolicy(policy_ref="as1-provider-policy-v1", policy_hash=stable_hash("AS1_PROVIDER_POLICY_V1"))
        compiled = AssetRequestCompiler().compile(
            plan,
            format_identity=format_identity,
            strategy_profile=strategy,
            provider_policy=provider_policy,
            evidence=CompilationEvidence(
                originality_manifest_ref=plan.episode_originality_manifest_ref,
                originality_manifest_hash=plan.episode_originality_manifest_hash,
                claim_ledger_refs=tuple(plan.claim_evidence_ledger_refs),
                disclosure_receipt_ref=plan.synthetic_media_disclosure_receipt_ref or "",
            ),
        )
        workspace.write_json(project_id, "manifests/native_render_plan.json", plan.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/compiled_asset_request_plan.json", compiled.model_dump(mode="json"))

        stock_request = next(item for item in compiled.requests if item.requested_role == "SUPPORTING_STOCK")
        query_plan = PexelsQueryPlanner().plan(stock_request, per_page=12)
        response = json.loads((fixture_dir / "pexels_response.json").read_text(encoding="utf-8"))
        candidates = PexelsResponseParser().parse(response)
        ranking = StockCandidateRanker().rank(stock_request, candidates, previous_asset_usage_refs=["asset-older-episode"])
        selected = next(item for item in candidates if item.candidate_id == ranking.selected_candidate_id)
        rendition = PexelsRenditionSelector().select(selected, stock_request)
        download_plan = PexelsDownloadPlanBuilder().build(selected, rendition, stock_request)
        download = workspace.fixture_download(
            project_id=project_id,
            request_id=stock_request.request_id,
            fixture_source=fixture_dir / "pexels_supporting_fixture.mp4",
            destination_relative=f"source/pexels/{selected.provider_asset_id}.mp4",
        )
        stock_manifest = build_stock_source_manifest(
            asset_id="as1-stock-fixture-001",
            request=stock_request,
            query_used=query_plan.queries[0],
            candidate=selected,
            plan=download_plan,
            download=download,
            retrieved_at=datetime(2026, 7, 11, tzinfo=UTC),
            rights_policy_ref="pexels-source-rights-policy-v1",
        )
        workspace.write_json(project_id, "manifests/pexels_query_plan.json", query_plan.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/stock_candidate_ranking.json", ranking.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/pexels_download_plan.json", download_plan.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/stock_source_manifest.json", stock_manifest.model_dump(mode="json"))

        hero_asset_request = next(item for item in compiled.requests if item.requested_role == "AI_HERO")
        hero_request = build_ai_hero_request(
            hero_asset_request,
            package_id=package_id,
            prompt_text="Abstract paper workflow transforming into a calm luminous operating system, no people, no logos, documentary lighting",
        )
        ai_manifest = build_planned_ai_generation_manifest(hero_request)
        workspace.write_json(project_id, "manifests/ai_hero_asset_request.json", hero_request.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/ai_generation_manifest.json", ai_manifest.model_dump(mode="json"))

        normalization = MediaNormalizer().compile_video_plan(
            input_asset_ref=stock_manifest.asset_id,
            input_asset_hash=stock_manifest.local_sha256,
            input_path=Path(stock_manifest.local_path),
            output_path=workspace.path(project_id, "normalized/stock/as1-stock-fixture-001.mp4"),
            width=1920,
            height=1080,
            audio_policy="REMOVE",
        )
        workspace.write_json(project_id, "manifests/media_normalization_manifest.json", normalization.model_dump(mode="json"))

        archive_sources = self._archive_sources(workspace, project_id, fixture_dir)
        archive_manifest = ProductionArchiveBuilder().build(
            manifest_id="as1-production-archive-manifest",
            project_id=project_id,
            package_id=package_id,
            sources=archive_sources,
        )
        workspace.write_json(project_id, "manifests/production_archive_manifest.json", archive_manifest.model_dump(mode="json"))
        archive_path = ProductionArchivePathBuilder().build(company_id="fixture-company", channel_workspace_id="small-team-ai", video_project_id=project_id)
        fake_drive_metadata = [
            {"archive_path": entry.expected_archive_path, "size_bytes": entry.size_bytes, "sha256": entry.sha256}
            for entry in archive_manifest.files
        ]
        archive_receipt = DriveArchiveFixtureVerifier().verify(
            manifest=archive_manifest,
            configured_root_folder_id_reference="settings://GOOGLE_DRIVE_ROOT_FOLDER_ID",
            root_relative_folder_path=archive_path,
            fixture_files=fake_drive_metadata,
        )
        workspace.write_json(project_id, "manifests/drive_archive_receipt.json", archive_receipt.model_dump(mode="json"))

        scratch = workspace.path(project_id, "render/scenes/as1-fixture-scratch.tmp")
        scratch.write_text("LOCAL_FIXTURE_ONLY scratch", encoding="utf-8")
        cleanup = LocalCleanupService().execute_fixture_only(
            project_id=project_id,
            workspace_path=Path(summary.workspace_path),
            archive_receipt=archive_receipt,
            candidate_files=[scratch, workspace.path(project_id, "manifests/native_render_plan.json")],
            fixture_only=True,
        )
        cleanup_repeat = LocalCleanupService().execute_fixture_only(
            project_id=project_id,
            workspace_path=Path(summary.workspace_path),
            archive_receipt=archive_receipt,
            candidate_files=[scratch],
            fixture_only=True,
        )
        workspace.write_json(project_id, "manifests/local_cleanup_receipt.json", cleanup.model_dump(mode="json"))
        workspace.write_json(project_id, "manifests/local_cleanup_idempotency_receipt.json", cleanup_repeat.model_dump(mode="json"))
        result = {
            "phase": "AS1",
            "channel_id": "small-team-ai",
            "strategy": "NR2_B_BALANCED",
            "project_id": project_id,
            "package_id": package_id,
            "request_counts": {"native": compiled.native_request_count, "pexels": compiled.pexels_request_count, "luma": compiled.luma_request_count},
            "provider_calls_made": False,
            "drive_calls_made": False,
            "transport": "LOCAL_FIXTURE_ONLY",
            "production_eligible": False,
            "archive_state": archive_receipt.archive_state,
            "local_purge_eligible": cleanup.eligibility_status == "ELIGIBLE",
            "cleanup_status": cleanup.cleanup_status,
            "cleanup_idempotency_status": cleanup_repeat.cleanup_status,
            "ai_generation_status": ai_manifest.provider_status,
            "provider_execution_allowed": False,
            "verdict": "PASS",
        }
        workspace.write_json(project_id, "manifests/as1_rehearsal_summary.json", result)
        return result

    @staticmethod
    def _archive_sources(workspace: LocalProjectWorkspaceService, project_id: str, fixture_dir: Path) -> list[ArchiveSource]:
        role_sources: list[ArchiveSource] = []
        existing = {
            "PACKAGE_MANIFEST": workspace.path(project_id, "manifests/compiled_asset_request_plan.json"),
            "STOCK_SOURCES": workspace.path(project_id, "manifests/stock_source_manifest.json"),
            "AI_GENERATION_MANIFEST": workspace.path(project_id, "manifests/ai_generation_manifest.json"),
            "NATIVE_RENDER_PLAN": workspace.path(project_id, "manifests/native_render_plan.json"),
        }
        for role, source_path in existing.items():
            role_sources.append(ArchiveSource(logical_role=role, source_path=source_path))
        for role in sorted(set(ROLE_ARCHIVE_PATHS) - set(existing)):
            suffix = Path(ROLE_ARCHIVE_PATHS[role]).suffix or ".txt"
            source = workspace.path(project_id, f"source/script/archive-fixture-{role.lower()}{suffix}")
            if role in {"SELECTED_STOCK_ORIGINAL", "SELECTED_AI_HERO_TAKE", "FINAL_MASTER", "REVIEW_PROXY"}:
                source.write_bytes((fixture_dir / "pexels_supporting_fixture.mp4").read_bytes())
            else:
                source.write_text(json.dumps({"role": role, "transport": "LOCAL_FIXTURE_ONLY", "production_eligible": False}), encoding="utf-8")
            role_sources.append(ArchiveSource(logical_role=role, source_path=source))
        rejected = workspace.path(project_id, "source/pexels/rejected/rejected-stock.mp4")
        rejected.parent.mkdir(parents=True, exist_ok=True)
        rejected.write_text("rejected fixture", encoding="utf-8")
        role_sources.append(ArchiveSource(logical_role="REJECTED_STOCK", source_path=rejected, required_for_archive=False, required_for_local_purge=False))
        normalized = workspace.path(project_id, "normalized/stock/temporary-normalized.mp4")
        normalized.write_text("normalized fixture", encoding="utf-8")
        role_sources.append(ArchiveSource(logical_role="NORMALIZED_TEMP", source_path=normalized, required_for_archive=False, required_for_local_purge=False))
        return role_sources


def _native_plan(*, project_id: str, package_id: str) -> NativeRenderPlan:
    scene_specs = [
        ("s1", "NATIVE_SLIDE", "HOOK", "An overloaded small team workflow represented as native cards", None),
        ("s2", "DIAGRAM", "EXPLANATION", "A native diagram explains the coordination bottleneck", None),
        ("s3", "UI_SIMULATION", "MECHANISM", "Native UI simulation shows one automation handoff", None),
        ("s4", "STOCK_VIDEO", "SUPPORT", "small team collaborating around laptop in a clean office", "PEXELS"),
        ("s5", "AI_HERO_VIDEO", "METAPHOR", "paper workflow transforms into a calm luminous operating system", "LUMA"),
    ]
    scenes = []
    for index, (scene_id, treatment, role, notes, provider) in enumerate(scene_specs):
        start = index * 6000
        scenes.append(
            NativeRenderScene(
                scene_id=scene_id,
                source_segment_ids=[f"seg-{index + 1}"],
                narration_start_ms=start,
                narration_end_ms=start + 6000,
                duration_ms=6000,
                visual_treatment=treatment,
                layout_type=treatment,
                originality_role=role,
                provider_intent=provider,
                scene_notes=notes,
            )
        )
    plan = NativeRenderPlan(
        plan_id="as1-small-team-ai-strategy-b-plan",
        plan_version=1,
        package_id=package_id,
        video_project_id=project_id,
        company_id="fixture-company",
        channel_id="small-team-ai",
        channel_profile_version_id="small-team-ai-frozen-profile-v1",
        effective_context_snapshot_id="small-team-ai-effective-context-v1",
        effective_context_hash=stable_hash("small-team-ai-effective-context-v1"),
        format_identity_contract_ref="f4ef71b1-6942-49c4-bb69-47244751265d",
        format_identity_contract_hash="8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc",
        format_identity_status="APPROVED",
        episode_originality_manifest_ref="d0bb74e3-eb8c-44ac-a1d8-b165892e176b",
        episode_originality_manifest_hash="d0bf32bf52e45c81ec0cab062f0b1c933a6cfdcdf63aabc961928764999d8624",
        final_originality_gate="PASS",
        claim_evidence_ledger_refs=["small-team-20-hours-scenario"],
        synthetic_media_disclosure_receipt_ref="as1-disclosure-receipt",
        script_ref="as1-fixture-script",
        script_hash=stable_hash("as1-fixture-script"),
        srt_ref="as1-fixture-captions",
        srt_hash=stable_hash("as1-fixture-captions"),
        visual_plan_ref="nr2-strategy-b",
        visual_plan_hash=stable_hash("nr2-strategy-b"),
        canvas_spec=CanvasSpec(width=1920, height=1080, fps=30),
        scenes=scenes,
        output_profiles=["YT_LONG_1080P30_SDR_H264_VT"],
        character_policy_mode="NO_CHARACTER",
        purpose="AS1_LOCAL_FIXTURE_REHEARSAL",
        production_eligible=False,
        status="APPROVED",
        created_at=datetime(2026, 7, 11, tzinfo=UTC),
        created_by="as1-local-fixture",
    )
    plan.content_hash = canonical_plan_hash(plan)
    return plan
