from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.contracts.geo_delivery import (
    GeoDeliveryAcceptanceGate,
    GeoDeliveryVerificationManifest,
)


GEO_DELIVERY_PYTEST_RUN_ID = "geo-delivery-focused-and-regression"
GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS: tuple[str, ...] = (
    "geo-delivery-compileall",
    "geo-delivery-alembic-head",
    "geo-delivery-git-diff-check",
)
GEO_DELIVERY_REQUIRED_RUN_IDS: tuple[str, ...] = (
    GEO_DELIVERY_PYTEST_RUN_ID,
    *GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS,
)
GEO_DELIVERY_REQUIRED_TEST_TARGETS: tuple[str, ...] = (
    "tests/test_geo_market_delivery_closeout.py",
    "tests/qualification/test_m7_publish_handoff.py",
    "tests/qualification/test_m9_post_publish_diagnostics.py",
    "tests/qualification/test_m12_2r_publish_handoff_ledger.py",
)


GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS: tuple[str, ...] = (
    "alembic/versions/0041_geo_market_delivery_closeout.py",
    "app/contracts/channel_policy.py",
    "app/contracts/geo_delivery.py",
    "app/contracts/geo_market.py",
    "app/contracts/m7.py",
    "app/contracts/__init__.py",
    "app/contracts/workflow.py",
    "app/core/config.py",
    "app/core/db.py",
    "app/core/errors.py",
    "app/db/models/__init__.py",
    "app/db/models/channel.py",
    "app/db/models/m7.py",
    "app/db/models/workflow.py",
    "app/db/session.py",
    "app/api/routes/serializers_publish_learning.py",
    "app/services/__init__.py",
    "app/services/config_registry.py",
    "app/services/geo_delivery.py",
    "app/services/geo_delivery_verification.py",
    "app/services/m7.py",
    "app/services/m9.py",
    "app/services/pkg1.py",
    "app/services/pkg1_sc04_revision.py",
    "app/services/workflow.py",
    "config/artifact_type_registry.yaml",
    "scripts/closeout_geo_market_delivery.py",
    "scripts/run_geo_delivery_verification.py",
    "tests/conftest.py",
    "tests/qualification/conftest.py",
    "tests/test_geo_market_delivery_closeout.py",
    "tests/test_pkg1_sc04_visual_revision.py",
    "tests/qualification/test_m12_2r_publish_handoff_ledger.py",
    "tests/qualification/test_m7_publish_handoff.py",
    "tests/qualification/test_m9_post_publish_diagnostics.py",
)
GEO_DELIVERY_RELEVANT_WORKSPACE_GLOBS: tuple[str, ...] = ("alembic/versions/*.py",)


GEO_DELIVERY_REQUIRED_TEST_NODES: dict[GeoDeliveryAcceptanceGate, tuple[str, ...]] = {
    GeoDeliveryAcceptanceGate.MARKET_LINEAGE: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_geo_workspace_hash_covers_direct_authority_dependencies",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_verification_scope_requires_all_static_runs_per_gate",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_verification_runner_restores_database_env_and_both_caches",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_strict_lineage_approval_is_complete_and_missing_binding_fails_closed",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_strict_lineage_actual_destination_must_match_every_approved_hash",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m7_strict_pending_destination_persists_lineage_and_blocks_confirmation",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m7_verified_destination_mismatch_blocks_and_match_propagates",
    ),
    GeoDeliveryAcceptanceGate.DESTINATION_ENFORCEMENT: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_unverified_destination_blocks_strict_handoff_but_not_closeout_contract",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_strict_handoff_contract_requires_exact_destination_pair",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m7_strict_pending_destination_persists_lineage_and_blocks_confirmation",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m7_verified_destination_mismatch_blocks_and_match_propagates",
        "tests/qualification/test_m12_2r_publish_handoff_ledger.py::"
        "test_m12_2r_api_routes_have_no_upload_publish_api_and_no_local_paths",
    ),
    GeoDeliveryAcceptanceGate.MARKET_ALIGNMENT: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_market_delivery_gate_pass_warn_and_typed_block",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_alignment_builder_uses_bound_artifact_actuals_and_blocks_mismatch",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_package_component_authority_and_named_consistency_fail_closed",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_closeout_artifacts_are_submitted_immutable_and_idempotent",
    ),
    GeoDeliveryAcceptanceGate.DISTRIBUTION_TRACKER: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_geo_tracker_preserves_null_unavailable_and_zero_as_distinct_truth",
    ),
    GeoDeliveryAcceptanceGate.MATURITY_INTEGRATION: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_maturity_rules_only_allow_profile_mismatch_after_three_comparable_videos",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_directional_geo_confidence_emits_drift_only",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m9_geo_tracker_lineage_and_maturity_reason_codes_fail_closed",
    ),
    GeoDeliveryAcceptanceGate.DIAGNOSTIC_RULES: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_incident_blocks_action_and_profile_level_mismatch",
        "tests/test_geo_market_delivery_closeout.py::"
        "test_m9_geo_tracker_lineage_and_maturity_reason_codes_fail_closed",
    ),
    GeoDeliveryAcceptanceGate.ADS_ONLY_MONETIZATION_POLICY: (
        "tests/test_geo_market_delivery_closeout.py::"
        "test_ads_only_overlay_preserves_base_snapshot_and_self_funding_uses_finalized_truth",
        "tests/qualification/test_m12_2r_publish_handoff_ledger.py::"
        "test_m12_2r_cannot_create_upload_task_for_not_ready_package",
    ),
}


def validate_geo_delivery_verification_scope(
    manifest: GeoDeliveryVerificationManifest,
) -> None:
    """Require the exact pytest/static run graph used by closeout v1."""

    required_nodes_by_gate = {
        item.gate: tuple(item.required_node_ids) for item in manifest.gate_results
    }
    if required_nodes_by_gate != GEO_DELIVERY_REQUIRED_TEST_NODES:
        raise ValueError("GEO_VERIFICATION_REQUIRED_NODE_SET_CHANGED")

    runs_by_id = {item.run_id: item for item in manifest.verification_runs}
    if set(runs_by_id) != set(GEO_DELIVERY_REQUIRED_RUN_IDS):
        raise ValueError("GEO_VERIFICATION_REQUIRED_RUN_SET_CHANGED")
    if runs_by_id[GEO_DELIVERY_PYTEST_RUN_ID].run_kind != "PYTEST":
        raise ValueError("GEO_VERIFICATION_PYTEST_RUN_KIND_INVALID")
    if runs_by_id[GEO_DELIVERY_PYTEST_RUN_ID].command[1:] != [
        "-m",
        "pytest",
        "-q",
        *GEO_DELIVERY_REQUIRED_TEST_TARGETS,
    ]:
        raise ValueError("GEO_VERIFICATION_PYTEST_COMMAND_INVALID")
    if any(
        runs_by_id[run_id].run_kind != "STATIC_CHECK"
        for run_id in GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS
    ):
        raise ValueError("GEO_VERIFICATION_STATIC_RUN_KIND_INVALID")
    compileall_run_id, alembic_run_id, diff_run_id = (
        GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS
    )
    if runs_by_id[compileall_run_id].command[1:] != [
        "-m",
        "compileall",
        "-q",
        "app",
        "scripts",
    ]:
        raise ValueError("GEO_VERIFICATION_COMPILEALL_COMMAND_INVALID")
    alembic_command = runs_by_id[alembic_run_id].command
    if Path(alembic_command[0]).name != "alembic" or alembic_command[1:] != ["heads"]:
        raise ValueError("GEO_VERIFICATION_ALEMBIC_COMMAND_INVALID")
    if runs_by_id[diff_run_id].command != ["git", "diff", "--check"]:
        raise ValueError("GEO_VERIFICATION_GIT_DIFF_COMMAND_INVALID")

    required_run_set = set(GEO_DELIVERY_REQUIRED_RUN_IDS)
    if any(
        set(item.verification_run_ids) != required_run_set
        for item in manifest.gate_results
    ):
        raise ValueError("GEO_VERIFICATION_GATE_REQUIRED_RUN_SET_CHANGED")


def geo_delivery_workspace_hash(root: Path) -> str:
    """Hash the exact source, migration, registry, and verification test bytes."""

    relative_paths = set(GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS)
    for pattern in GEO_DELIVERY_RELEVANT_WORKSPACE_GLOBS:
        relative_paths.update(
            path.relative_to(root).as_posix()
            for path in root.glob(pattern)
            if path.is_file()
        )
    entries: list[dict[str, str]] = []
    for relative_path in sorted(relative_paths):
        path = root / relative_path
        if not path.is_file():
            raise FileNotFoundError(
                f"GEO_DELIVERY_RELEVANT_WORKSPACE_FILE_MISSING:{relative_path}"
            )
        entries.append(
            {
                "path": relative_path,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    encoded = json.dumps(
        entries,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "GEO_DELIVERY_PYTEST_RUN_ID",
    "GEO_DELIVERY_REQUIRED_RUN_IDS",
    "GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS",
    "GEO_DELIVERY_REQUIRED_TEST_NODES",
    "GEO_DELIVERY_REQUIRED_TEST_TARGETS",
    "GEO_DELIVERY_RELEVANT_WORKSPACE_GLOBS",
    "GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS",
    "geo_delivery_workspace_hash",
    "validate_geo_delivery_verification_scope",
]
