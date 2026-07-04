from __future__ import annotations

import importlib
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.main import create_app


SEMANTIC_SERVICE_MODULES = [
    "app.services.daily_operations",
    "app.services.context_resolver",
    "app.services.project_admission",
    "app.services.post_publish_diagnostics",
    "app.services.learning_candidates",
    "app.services.learning_review",
    "app.services.approved_playbook",
    "app.services.provider_readiness",
    "app.services.prompt_registry",
    "app.services.prompt_audit",
    "app.services.runtime_provider_boundary",
    "app.services.video_package_generation",
    "app.services.publish_handoff",
    "app.services.uploaded_video_backfill",
    "app.services.channel_contract_compiler",
    "app.services.channel_init_research",
    "app.services.agent_rehearsal",
    "app.services.package_generation_rehearsal",
    "app.services.channel_scope_authority",
    "app.services.channel_runtime_context",
    "app.services.agent_context_pack",
    "app.services.output_validation_gates",
    "app.services.packaging_handoff",
    "app.services.provider_wiring",
    "app.services.controlled_memory",
    "app.services.vector_retrieval",
    "app.services.learning_loop",
    "app.services.cost_firewall",
]

PHASE_CODED_SERVICE_MODULES = [
    "app.services.m1",
    "app.services.m2",
    "app.services.m5",
    "app.services.m6",
    "app.services.m7",
    "app.services.m8",
    "app.services.m9",
    "app.services.m10",
    "app.services.m10_1",
    "app.services.m10_2",
    "app.services.m10_3",
    "app.services.m10_5",
    "app.services.m11",
    "app.services.m11_1",
    "app.services.m12",
    "app.services.m12_1",
    "app.services.m12_1r",
    "app.services.m12_2",
    "app.services.m12_2p3",
    "app.services.m12_2r",
    "app.services.r3d1",
    "app.services.r3d2",
    "app.services.r3d3",
    "app.services.r3d4",
    "app.services.r3d5",
    "app.services.r3d6",
    "app.services.r3d7",
    "app.services.r3d8",
]

PHASE_CODED_CONTRACT_MODULES = [
    "app.contracts.m1",
    "app.contracts.m2",
    "app.contracts.m5",
    "app.contracts.m6",
    "app.contracts.m7",
    "app.contracts.m8",
    "app.contracts.m9",
    "app.contracts.m10",
    "app.contracts.m10_1",
    "app.contracts.m10_2",
    "app.contracts.m10_3",
    "app.contracts.m10_5",
    "app.contracts.m11",
    "app.contracts.m11_1",
    "app.contracts.m12",
    "app.contracts.m12_1",
    "app.contracts.m12_2",
    "app.contracts.m12_2p3",
    "app.contracts.m12_2r",
    "app.contracts.r3d1",
    "app.contracts.r3d2",
    "app.contracts.r3d3",
    "app.contracts.r3d4",
    "app.contracts.r3d5",
    "app.contracts.r3d6",
    "app.contracts.r3d7",
    "app.contracts.r3d8",
]

PHASE_CODED_MODEL_MODULES = [
    "app.db.models.m5",
    "app.db.models.m6",
    "app.db.models.m7",
    "app.db.models.m8",
    "app.db.models.m9",
    "app.db.models.m10",
    "app.db.models.m10_1",
    "app.db.models.m10_2",
    "app.db.models.m10_3",
    "app.db.models.m10_5",
    "app.db.models.m11",
    "app.db.models.m11_1",
    "app.db.models.m12",
    "app.db.models.m12_1",
    "app.db.models.m12_2",
    "app.db.models.m12_2r",
    "app.db.models.r3d1",
    "app.db.models.r3d2",
    "app.db.models.r3d3",
    "app.db.models.r3d4",
    "app.db.models.r3d5",
    "app.db.models.r3d6",
    "app.db.models.r3d7",
    "app.db.models.r3d8",
]

EXPORT_EXPECTATIONS = [
    ("app.services.daily_operations", "app.services.m5", "ChannelDailyRunService"),
    ("app.services.context_resolver", "app.services.m5", "ResourceResolverService"),
    ("app.services.project_admission", "app.services.m5", "ProjectAdmissionService"),
    ("app.services.post_publish_diagnostics", "app.services.m9", "NoViewDiagnosticService"),
    ("app.services.learning_candidates", "app.services.m10", "LearningCandidateGenerationService"),
    ("app.services.learning_review", "app.services.m11", "M11LearningReviewService"),
    ("app.services.approved_playbook", "app.services.m11", "M11LearningReviewService"),
    ("app.services.provider_readiness", "app.services.m12", "ProviderReadinessService"),
    ("app.services.prompt_registry", "app.services.m12_1", "PromptRegistryService"),
    ("app.services.prompt_audit", "app.services.m12_1", "prompt_context_hash"),
    ("app.services.runtime_provider_boundary", "app.services.m2", "ProviderBoundaryPreflight"),
    ("app.services.video_package_generation", "app.services.m12_2", "FirstScriptedVideoPackageService"),
    ("app.services.publish_handoff", "app.services.m12_2r", "PublishHandoffLedgerService"),
    ("app.services.uploaded_video_backfill", "app.services.m12_2r", "PublishHandoffLedgerService"),
    ("app.services.channel_contract_compiler", "app.services.m12_2p3", "ChannelContractCompiler"),
    ("app.services.channel_init_research", "app.services.m12_2p3", "ChannelSetupResearchAgentService"),
    ("app.services.agent_rehearsal", "app.services.m12_2", "FirstScriptedVideoPackageService"),
    ("app.services.package_generation_rehearsal", "app.services.m12_2", "FirstScriptedVideoPackageService"),
    ("app.services.channel_scope_authority", "app.services.r3d1", "ChannelRuntimeAuthorityService"),
    ("app.services.channel_runtime_context", "app.services.r3d2", "EffectiveChannelRuntimeContextCompiler"),
    ("app.services.agent_context_pack", "app.services.r3d3", "AgentContextPackBuilder"),
    ("app.services.output_validation_gates", "app.services.r3d4", "R3D4GateService"),
    ("app.services.packaging_handoff", "app.services.m1", "PackagingHandoffReadService"),
    ("app.services.provider_wiring", "app.services.m2", "ProviderReadinessM2Service"),
    ("app.services.controlled_memory", "app.services.r3d5", "ControlledMemoryService"),
    ("app.services.vector_retrieval", "app.services.r3d6", "VectorSafeRetrievalService"),
    ("app.services.learning_loop", "app.services.r3d7", "ClosedLearningLoopService"),
    ("app.services.cost_firewall", "app.services.r3d8", "PaidProviderBoundaryService"),
]


def test_dx1_semantic_service_modules_import() -> None:
    for module_name in SEMANTIC_SERVICE_MODULES:
        assert importlib.import_module(module_name)


def test_dx1_old_phase_coded_modules_still_import() -> None:
    for module_name in [*PHASE_CODED_SERVICE_MODULES, *PHASE_CODED_CONTRACT_MODULES, *PHASE_CODED_MODEL_MODULES]:
        assert importlib.import_module(module_name)


def test_dx1_semantic_facades_export_expected_objects() -> None:
    for semantic_name, phase_name, attr_name in EXPORT_EXPECTATIONS:
        semantic = importlib.import_module(semantic_name)
        phase = importlib.import_module(phase_name)
        assert getattr(semantic, attr_name) is getattr(phase, attr_name)


def test_dx1_public_api_routes_preserved() -> None:
    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    expected_paths = {
        "/render-revisions",
        "/provider-boundary/preflight",
        "/learning-loop/status",
        "/memory-influence-manifests",
        "/quality-delta-attributions/run",
        "/video-packages/first-scripted",
        "/video-packages/rehearse-full",
        "/video-packages/{package_id}/packaging-handoff",
        "/channels/{channel_id}/upload-tasks",
        "/uploaded-videos/{uploaded_video_id}/post-publish-health",
    }
    assert expected_paths.issubset(paths)


def test_dx1_db_tables_not_renamed(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    expected_tables = {
        "channel_workspaces",
        "compiled_channel_policy_snapshots",
        "effective_channel_runtime_context_snapshots",
        "agent_context_pack_snapshots",
        "r3d4_gate_batch_runs",
        "channel_memory_items",
        "memory_facets",
        "vector_retrieval_manifests",
        "memory_influence_manifests",
        "quality_delta_attributions",
        "render_revisions",
        "cost_estimate_snapshots",
        "paid_provider_call_ledger",
        "packaging_review_queue_items",
        "packaging_proposed_patches",
        "packaging_patch_approval_decisions",
        "packaging_patch_apply_runs",
        "packaging_gate_rerun_records",
        "package_runtime_dispositions",
        "first_scripted_video_packages",
        "publish_handoff_packages",
        "uploaded_videos",
    }
    assert expected_tables.issubset(tables)


def test_dx1_alembic_history_not_rewritten() -> None:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0033_p1_pre_lts_disposition"
    assert script.get_revision("0030_r3d7_closed_learning_loop") is not None
    r3d8 = script.get_revision("0031_r3d8_cost_firewall")
    assert r3d8 is not None
    assert r3d8.down_revision == "0030_r3d7_closed_learning_loop"
    r3d9_ux2 = script.get_revision("0032_r3d9_ux2_review_queue")
    assert r3d9_ux2 is not None
    assert r3d9_ux2.down_revision == "0031_r3d8_cost_firewall"
    p1_pre_lts = script.get_revision("0033_p1_pre_lts_disposition")
    assert p1_pre_lts is not None
    assert p1_pre_lts.down_revision == "0032_r3d9_ux2_review_queue"
    assert Path("alembic/versions/0030_r3d7_closed_learning_loop.py").exists()
    assert Path("alembic/versions/0031_r3d8_production_cost_firewall.py").exists()
    assert Path("alembic/versions/0032_r3d9_ux2_packaging_review_queue.py").exists()
    assert Path("alembic/versions/0033_p1_pre_lts_package_runtime_disposition.py").exists()
    assert Path("alembic/versions/0032_r3d9_ux2_packaging_review_queue.py").exists()
