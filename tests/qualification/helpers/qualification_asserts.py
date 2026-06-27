from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]

REQUIRED_TAGS = {
    "m5-daily-run-context-admission",
    "m6-production-media-qc-foundation",
    "pre-m7-m0-m6-qualification-pass",
    "m7-manual-publish-handoff",
    "m8-analytics-sync-foundation",
    "m9-post-publish-diagnostics",
    "m10-learning-review-queue",
    "m10-1-router-derivative-funnel",
    "m10-2-media-provider-routing",
    "m10-3-youtube-follow",
    "m10-4-google-vertex-veo-binding",
    "m10-5-google-drive-media-offload",
}

EXPECTED_ALEMBIC_HEAD = "0017_m11_1_localization"

REQUIRED_SOURCE_OF_TRUTH_PATHS = {
    "README.md",
    "docs/architecture/source-of-truth.md",
    "docs/architecture/architecture-ledger.md",
    "docs/architecture/m0-scope.md",
    "docs/architecture/m1-scope.md",
    "docs/architecture/m2-artifact-workflow.md",
    "docs/architecture/m3-policy-gate-readiness.md",
    "docs/architecture/m4-ops-foundation.md",
    "docs/architecture/m5-daily-run-context-admission.md",
    "docs/architecture/m6-production-artifacts.md",
    "docs/architecture/m7-manual-publish-handoff.md",
    "docs/architecture/m8-analytics-sync.md",
    "docs/architecture/m9-post-publish-diagnostics.md",
    "docs/architecture/m10-learning-review-queue.md",
    "docs/architecture/m10-1-llm-router-derivative-funnel.md",
    "docs/architecture/m10-2-media-provider-role-matrix.md",
    "docs/architecture/m10-3-youtube-follow.md",
    "docs/architecture/m10-4-google-vertex-veo-binding.md",
    "docs/architecture/m10-5-google-drive-media-offload.md",
    "docs/architecture/m11-operator-dashboard.md",
    "docs/architecture/policy-snapshot-invariants.md",
    "docs/architecture/profile-compiler.md",
    "reports/m2-final-report.md",
    "reports/m3-final-report.md",
    "reports/m4-final-report.md",
    "reports/m5-final-report.md",
    "reports/m6-final-report.md",
    "reports/m7-final-report.md",
    "reports/m8-final-report.md",
    "reports/m9-final-report.md",
    "reports/m10-final-report.md",
    "reports/m10_1-final-report.md",
    "reports/m10_2-final-report.md",
    "reports/m10_3-final-report.md",
    "reports/m10_4-final-report.md",
    "reports/m10_5-final-report.md",
    "reports/m11-final-report.md",
    "reports/pre_m4_qualification_report.md",
    "scripts/pre_m4_qualification.py",
}

M0_M1_FINAL_REPORTS = {
    "reports/m0-final-report.md",
    "reports/m1-final-report.md",
}

M0_M1_WAIVER_DOCS = {
    "docs/architecture/m0-scope.md",
    "docs/architecture/m1-scope.md",
}

MEDIA_GITIGNORE_PATTERNS = {
    "var/generated/",
    "test-render-output/",
    "*.mp4",
    "*.mov",
    "*.wav",
}

EXPECTED_M0_M6_TABLES = {
    "companies",
    "users",
    "roles",
    "user_roles",
    "audit_events",
    "domain_events",
    "llm_run_snapshots",
    "config_catalog_versions",
    "channel_workspaces",
    "channel_memberships",
    "channel_profile_versions",
    "channel_profile_compile_runs",
    "compiled_channel_policy_snapshots",
    "video_projects",
    "artifacts",
    "artifact_versions",
    "review_tasks",
    "review_findings",
    "revision_requests",
    "approval_decisions",
    "gate_definition_versions",
    "gate_runs",
    "platform_policy_catalogs",
    "platform_policy_versions",
    "policy_source_refs",
    "policy_change_records",
    "policy_revalidation_batches",
    "provider_registry_entries",
    "credential_references",
    "credential_health_snapshots",
    "quota_accounts",
    "quota_events",
    "cost_events",
    "budget_policies",
    "provider_health_snapshots",
    "component_health_snapshots",
    "system_health_snapshots",
    "retry_policies",
    "provider_attempts",
    "dead_letter_jobs",
    "ops_incidents",
    "manual_action_queue",
    "editorial_calendar_slots",
    "channel_daily_runs",
    "retrieval_plan_snapshots",
    "context_pack_snapshots",
    "channel_state_pack_snapshots",
    "search_demand_evidence",
    "search_intent_maps",
    "audience_target_packs",
    "idea_market_preflights",
    "daily_idea_decisions",
    "project_admission_decisions",
    "production_artifact_runs",
    "voice_timeline_snapshots",
    "caption_track_snapshots",
    "visual_plan_snapshots",
    "scene_manifest_snapshots",
    "asset_manifest_snapshots",
    "source_manifest_snapshots",
    "render_spec_snapshots",
    "media_render_jobs",
    "render_package_snapshots",
    "media_qc_reports",
    "accessibility_qc_reports",
    "pronunciation_dictionary_entries",
    "publish_handoff_packages",
    "manual_publish_confirmations",
    "uploaded_videos",
    "uploaded_video_publication_summaries",
    "analytics_sync_runs",
    "metric_definition_versions",
    "metric_availability_snapshots",
    "analytics_snapshots",
    "traffic_source_snapshots",
    "retention_curve_snapshots",
    "engagement_snapshots",
    "uploaded_video_metrics_summaries",
}

FORBIDDEN_M8_PLUS_TABLES = {
    "publish_packages",
    "upload_jobs",
    "upload_attempts",
    "platform_processing_statuses",
    "analytics_events",
    "analytics_semantic_layers",
    "performance_summaries",
    "no_view_incidents",
    "recovery_actions",
    "learning_candidates",
    "memory_promotions",
}

RAW_SECRET_MARKERS = (
    "sk-",
    "pk_live_",
    "BEGIN PRIVATE KEY",
    "anthropic-",
    "xoxb-",
    "ghp_",
)

SECRET_KEY_FRAGMENTS = {
    "secret",
    "password",
    "token",
    "api_key",
    "apikey",
    "private_key",
    "credential_value",
}


def missing_paths(paths: set[str], *, root: Path = ROOT) -> list[str]:
    return sorted(path for path in paths if not (root / path).exists())


def m0_m1_evidence_status(*, root: Path = ROOT) -> dict[str, Any]:
    final_reports = sorted(path for path in M0_M1_FINAL_REPORTS if (root / path).exists())
    waiver_docs = sorted(path for path in M0_M1_WAIVER_DOCS if (root / path).exists())
    return {
        "final_reports_present": final_reports,
        "waiver_docs_present": waiver_docs,
        "waiver_applied": len(final_reports) < len(M0_M1_FINAL_REPORTS)
        and len(waiver_docs) == len(M0_M1_WAIVER_DOCS),
    }


def assert_no_secret_payload(payload: Any) -> None:
    text = json.dumps(payload, default=str, sort_keys=True)
    lower = text.lower()
    for marker in RAW_SECRET_MARKERS:
        assert marker.lower() not in lower
    for fragment in SECRET_KEY_FRAGMENTS:
        if fragment in lower:
            assert "secret_ref" in lower or "secret_ref_present" in lower


def assert_operator_signal(payload: dict[str, Any]) -> None:
    assert payload.get("reason_codes") or payload.get("reason_code") or payload.get("next_action")
    if payload.get("overall_state") in {"DEGRADED", "BLOCKED"}:
        assert payload.get("next_action")


def assert_file_ref_is_verified(file_ref: dict[str, Any]) -> None:
    path = Path(file_ref["file_path"])
    assert path.exists()
    assert path.stat().st_size == file_ref["size_bytes"]
    assert file_ref["checksum"]
    assert file_ref["license_state"] == "INTERNAL_TEST_ONLY"


def assert_artifact_types(actual: set[str], expected: set[str]) -> None:
    assert actual == expected, f"artifact types mismatch: expected {sorted(expected)}, got {sorted(actual)}"
