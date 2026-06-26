from __future__ import annotations

from .helpers.ffmpeg_checks import assert_ffmpeg_available
from .helpers.git_checks import collect_git_status
from .helpers.qualification_asserts import (
    REQUIRED_SOURCE_OF_TRUTH_PATHS,
    m0_m1_evidence_status,
    missing_paths,
)


ALLOWED_QUALIFICATION_DIRTY_PREFIXES = (
    ".env.example",
    "Makefile",
    "README.md",
    "docker-compose.yml",
    "app/cli/main.py",
    "app/contracts/__init__.py",
    "app/contracts/ops.py",
    "app/contracts/m7.py",
    "app/contracts/m8.py",
    "app/contracts/m9.py",
    "app/contracts/m10.py",
    "app/contracts/m10_1.py",
    "app/db/models/__init__.py",
    "app/db/models/m7.py",
    "app/db/models/m8.py",
    "app/db/models/m9.py",
    "app/db/models/m10.py",
    "app/db/models/m10_1.py",
    "app/main.py",
    "app/services/__init__.py",
    "app/services/config_registry.py",
    "app/services/m7.py",
    "app/services/m8.py",
    "app/services/m9.py",
    "app/services/m10.py",
    "app/services/m10_1.py",
    "app/providers/mock.py",
    "app/providers/ollama.py",
    "alembic/versions/0008_m7_publish_handoff.py",
    "alembic/versions/0009_m8_analytics_sync.py",
    "alembic/versions/0010_m9_post_publish_diagnostics.py",
    "alembic/versions/0011_m10_learning_review_queue.py",
    "alembic/versions/0012_m10_1_llm_router_derivatives.py",
    "config/analytics_observation_window_catalog.yaml",
    "config/analytics_sync_mode_catalog.yaml",
    "config/analytics_sync_state_catalog.yaml",
    "config/disclosure_confirmation_catalog.yaml",
    "config/diagnostic_confidence_catalog.yaml",
    "config/diagnostic_severity_catalog.yaml",
    "config/diagnostic_state_catalog.yaml",
    "config/diagnostic_taxonomy_catalog.yaml",
    "config/event_types.yaml",
    "config/m7_reason_code_catalog.yaml",
    "config/m8_reason_code_catalog.yaml",
    "config/m9_reason_code_catalog.yaml",
    "config/m10_reason_code_catalog.yaml",
    "config/m10_1_reason_code_catalog.yaml",
    "config/cta_type_catalog.yaml",
    "config/derivative_type_catalog.yaml",
    "config/human_upload_task_state_catalog.yaml",
    "config/llm_model_profile_catalog.yaml",
    "config/llm_route_status_catalog.yaml",
    "config/llm_router_lane_catalog.yaml",
    "config/music_policy_catalog.yaml",
    "config/originality_check_result_catalog.yaml",
    "config/release_plan_state_catalog.yaml",
    "config/reusable_artifact_state_catalog.yaml",
    "config/reusable_artifact_type_catalog.yaml",
    "config/short_candidate_state_catalog.yaml",
    "config/short_crop_strategy_catalog.yaml",
    "config/short_visual_source_catalog.yaml",
    "config/upload_card_state_catalog.yaml",
    "config/learning_candidate_state_catalog.yaml",
    "config/learning_candidate_type_catalog.yaml",
    "config/learning_confidence_label_catalog.yaml",
    "config/learning_promotion_eligibility_result_catalog.yaml",
    "config/learning_recommended_scope_catalog.yaml",
    "config/learning_review_queue_state_catalog.yaml",
    "config/learning_risk_level_catalog.yaml",
    "config/manual_action_type_catalog.yaml",
    "config/manual_publish_confirmation_state_catalog.yaml",
    "config/metadata_diff_severity_catalog.yaml",
    "config/metric_confidence_level_catalog.yaml",
    "config/metric_definition_catalog.yaml",
    "config/metric_freshness_state_catalog.yaml",
    "config/metric_group_catalog.yaml",
    "config/metric_unit_catalog.yaml",
    "config/publish_checklist_category_catalog.yaml",
    "config/publish_handoff_state_catalog.yaml",
    "config/publish_target_platform_catalog.yaml",
    "config/publish_target_surface_catalog.yaml",
    "config/playbook_candidate_category_catalog.yaml",
    "config/playbook_candidate_state_catalog.yaml",
    "config/post_publish_health_state_catalog.yaml",
    "config/post_publish_observation_window_catalog.yaml",
    "config/recovery_proposal_state_catalog.yaml",
    "config/recovery_proposal_type_catalog.yaml",
    "config/uploaded_video_monitoring_state_catalog.yaml",
    "config/uploaded_video_publish_status_catalog.yaml",
    "docs/architecture/architecture-ledger.md",
    "docs/architecture/m7-manual-publish-handoff.md",
    "docs/architecture/m8-analytics-sync.md",
    "docs/architecture/m9-post-publish-diagnostics.md",
    "docs/architecture/m10-learning-review-queue.md",
    "docs/architecture/m10-1-llm-router-derivative-funnel.md",
    "docs/architecture/source-of-truth.md",
    "reports/m7-final-report.md",
    "reports/m8-final-report.md",
    "reports/m9-final-report.md",
    "reports/m10-final-report.md",
    "reports/m10_1-final-report.md",
    "tests/conftest.py",
    "tests/test_config_registry.py",
    "tests/test_m4_ops_foundation.py",
    "tests/test_m5_daily_run_context_admission.py",
    "tests/test_m6_production.py",
    "tests/test_migration.py",
    "tests/test_pre_m4_regression_gauntlet.py",
    "tests/qualification/helpers/qualification_asserts.py",
    "tests/qualification/helpers/repo_scanners.py",
    "tests/qualification/test_m8_analytics_sync.py",
    "tests/qualification/test_m10_1_llm_router_derivatives.py",
    "tests/qualification/",
    "scripts/pre_m7_qualification.py",
    "reports/pre_m7_qualification_",
    "alembic/versions/0002_m1_channel_profile_backbone.py",
)


def _dirty_path(line: str) -> str:
    return line[3:] if len(line) > 3 else line


def test_required_tags_source_docs_gitignore_and_media_preflight() -> None:
    status = collect_git_status()
    assert all(status.required_tags.values())
    assert all(status.media_gitignore.values())
    assert status.staged_binary_media == []
    assert missing_paths(REQUIRED_SOURCE_OF_TRUTH_PATHS) == []
    assert_ffmpeg_available()


def test_worktree_has_no_unrelated_dirty_product_changes() -> None:
    status = collect_git_status()
    unrelated_dirty = [
        line
        for line in status.porcelain
        if not _dirty_path(line).startswith(ALLOWED_QUALIFICATION_DIRTY_PREFIXES)
    ]
    assert unrelated_dirty == []


def test_m0_m1_final_report_waiver_is_explicit() -> None:
    evidence = m0_m1_evidence_status()
    assert evidence["waiver_docs_present"] == ["docs/architecture/m0-scope.md", "docs/architecture/m1-scope.md"]
    assert evidence["waiver_applied"] is True
