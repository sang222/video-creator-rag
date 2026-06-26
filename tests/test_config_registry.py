from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.errors import ConfigVersionConflictError
from app.db.models import ConfigCatalogVersion, MetricDefinitionVersion, Role
from app.services.config_registry import ConfigRegistryService

ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_validates(db_session) -> None:
    loaded = ConfigRegistryService(db_session).load_catalog_files([ROOT / "config"])
    assert {catalog.catalog_key for catalog in loaded} == {
        "artifact_type_registry",
        "admission_decision_catalog",
        "analytics_observation_window_catalog",
        "analytics_sync_mode_catalog",
        "analytics_sync_state_catalog",
        "accessibility_qc_state_catalog",
        "asset_source_type_catalog",
        "capability_matrix",
        "context_pack_purpose_catalog",
        "daily_run_status_catalog",
        "decision_rights_policy",
        "disclosure_confirmation_catalog",
        "evidence_type_catalog",
        "event_types",
        "export_profile_catalog",
        "freshness_state_catalog",
        "confidence_reason_code_catalog",
        "component_type_catalog",
        "cost_event_type_catalog",
        "credential_status_catalog",
        "credential_type_catalog",
        "gate_definition_catalog",
        "health_state_catalog",
        "idea_decision_status_catalog",
        "license_state_catalog",
        "m4_reason_code_catalog",
        "m5_reason_code_catalog",
        "m6_reason_code_catalog",
        "m7_reason_code_catalog",
        "m8_reason_code_catalog",
        "manual_action_type_catalog",
        "manual_publish_confirmation_state_catalog",
        "metadata_diff_severity_catalog",
        "metric_confidence_level_catalog",
        "metric_definition_catalog",
        "metric_freshness_state_catalog",
        "metric_group_catalog",
        "metric_unit_catalog",
        "media_qc_state_catalog",
        "media_render_job_status_catalog",
        "niche_profile_templates",
        "ops_incident_type_catalog",
        "platform_policy_catalog",
        "platform_surface_catalog",
        "policy_domain_catalog",
        "publish_checklist_category_catalog",
        "publish_handoff_state_catalog",
        "publish_target_platform_catalog",
        "publish_target_surface_catalog",
        "provider_registry_catalog",
        "provider_source_class_catalog",
        "provider_status_catalog",
        "provider_type_catalog",
        "production_run_status_catalog",
        "profile_compiler_policy",
        "quota_event_type_catalog",
        "quota_unit_catalog",
        "reason_code_catalog",
        "reason_codes",
        "render_intent_catalog",
        "render_package_state_catalog",
        "render_source_type_catalog",
        "render_variant_status_catalog",
        "review_type_registry",
        "role_catalog",
        "retry_policy_catalog",
        "scene_factual_risk_catalog",
        "scene_importance_catalog",
        "scene_type_catalog",
        "search_demand_source_type_catalog",
        "slot_type_catalog",
        "source_decision_catalog",
        "uploaded_video_monitoring_state_catalog",
        "uploaded_video_publish_status_catalog",
    }


def test_config_seed_deterministic(db_session) -> None:
    service = ConfigRegistryService(db_session)
    first = {catalog.catalog_key: catalog.content_hash for catalog in service.load_catalog_files([ROOT / "config"])}
    second = {catalog.catalog_key: catalog.content_hash for catalog in service.load_catalog_files([ROOT / "config"])}
    assert first == second


def test_config_seed_idempotent(db_session) -> None:
    service = ConfigRegistryService(db_session)
    service.seed([ROOT / "config"])
    service.seed([ROOT / "config"])
    catalog_count = db_session.scalar(select(func.count()).select_from(ConfigCatalogVersion))
    role_count = db_session.scalar(select(func.count()).select_from(Role))
    metric_definition_count = db_session.scalar(select(func.count()).select_from(MetricDefinitionVersion))
    assert catalog_count == 74
    assert role_count == 3
    assert metric_definition_count == 16


def test_config_version_conflict_is_blocked(db_session, tmp_path) -> None:
    service = ConfigRegistryService(db_session)
    service.seed([ROOT / "config" / "reason_codes.yaml"])
    conflict = tmp_path / "reason_codes.yaml"
    conflict.write_text(
        """
catalog_key: reason_codes
catalog_version: "1.2.0"
schema_version: catalog.v1
status: active
items:
  - code: SYSTEM_OK
    description: Changed description.
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigVersionConflictError):
        service.seed([conflict])
