import runpy
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


ROOT = Path(__file__).resolve().parents[1]


REQUIRED_TABLES = {
    "companies",
    "users",
    "roles",
    "user_roles",
    "audit_events",
    "domain_events",
    "llm_run_snapshots",
    "config_catalog_versions",
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
    "series_plans",
    "series_runs",
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
    "post_publish_observation_windows",
    "post_publish_health_runs",
    "diagnostic_taxonomy_versions",
    "no_view_diagnostic_runs",
    "packaging_diagnostic_runs",
    "retention_diagnostic_runs",
    "engagement_diagnostic_runs",
    "policy_rights_diagnostic_runs",
    "failure_trace_reports",
    "recovery_proposals",
    "learning_candidate_generation_runs",
    "learning_candidates",
    "learning_evidence_bundles",
    "learning_promotion_eligibility_runs",
    "learning_review_queue_items",
    "playbook_candidate_drafts",
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
    "media_provider_role_profiles",
    "provider_capability_matrix_entries",
    "media_render_routing_decisions",
    "media_provider_budget_policies",
    "media_provider_budget_snapshots",
    "long_form_render_packages",
    "short_render_packages",
    "ai_hero_assets",
    "thumbnail_variants",
    "final_media_refs",
    "license_evidence_records",
    "youtube_monitoring_credentials",
    "youtube_oauth_sessions",
    "youtube_public_sync_runs",
    "youtube_owner_analytics_sync_runs",
    "uploaded_video_youtube_public_monitor_snapshots",
    "uploaded_video_youtube_owner_analytics_snapshots",
    "cloud_media_refs",
    "media_offload_jobs",
    "local_media_retention_policies",
    "google_drive_media_credentials",
    "google_drive_oauth_sessions",
    "channel_lifecycle_decisions",
    "learning_review_decisions",
    "approved_playbook_entries",
    "operator_users",
    "operator_auth_sessions",
    "localized_subtitle_packages",
    "localized_metadata_packages",
    "channel_publish_timing_policies",
    "publish_timing_suggestions",
    "provider_readiness_checks",
    "provider_readiness_snapshots",
    "real_smoke_runs",
    "prompt_template_records",
    "agent_prompt_profiles",
    "prompt_contract_versions",
    "structured_output_schemas",
    "prompt_render_runs",
    "prompt_audit_snapshots",
    "prompt_evaluation_cases",
    "prompt_evaluation_runs",
    "first_scripted_video_packages",
    "video_generation_boundaries",
    "uploaded_video_backfill_events",
    "content_categories",
    "category_creative_digests",
    "character_profiles",
    "character_versions",
    "character_image_branches",
    "character_reference_asset_packs",
    "character_reference_assets",
    "voice_profiles",
    "character_bindings",
    "effective_channel_runtime_context_snapshots",
    "agent_context_pack_snapshots",
    "agent_output_validation_runs",
    "schema_violation_ledger",
    "r3d4_gate_batch_runs",
    "r3d4_gate_runs",
    "channel_memory_items",
    "memory_facets",
    "memory_review_queue_items",
    "memory_approval_decisions",
    "memory_usage_manifests",
    "memory_source_links",
    "embedding_facets",
    "embedding_jobs",
    "vector_retrieval_manifests",
    "memory_influence_manifests",
    "quality_delta_attributions",
    "learning_to_memory_promotion_runs",
    "agent_memory_application_records",
    "memory_confidence_update_ledger",
    "render_revisions",
    "cost_estimate_snapshots",
    "human_paid_render_approvals",
    "provider_idempotency_keys",
    "provider_job_snapshots",
    "paid_provider_call_ledger",
    "paid_attempt_limit_records",
    "proxy_preview_artifact_flags",
    "packaging_review_queue_items",
    "packaging_proposed_patches",
    "packaging_patch_approval_decisions",
    "packaging_patch_apply_runs",
    "packaging_gate_rerun_records",
    "package_runtime_dispositions",
    "production_workflow_runs",
    "workflow_command_receipts",
    "final_review_candidates",
    "final_video_decisions",
    "series_episode_publications",
    "v2_production_effect_ledger",
}


def test_alembic_migration_applies_on_empty_postgres(engine: Engine) -> None:
    with engine.connect() as connection:
        revision = connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()
        assert revision == "0046_vcos_v2_effect_ledger"


def test_core_tables_exist_after_migration(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert REQUIRED_TABLES.issubset(tables)


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def test_0043_through_0046_downgrade_round_trip_is_safe_without_authoritative_rows(
    engine: Engine,
) -> None:
    config = _alembic_config()
    try:
        command.downgrade(config, "0042_mr1_final_lineage")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0042_mr1_final_lineage"
            )
            assert "series_plans" not in inspect(connection).get_table_names()
    finally:
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == "0046_vcos_v2_effect_ledger"
        )


def test_0046_downgrade_fails_closed_then_immediately_reupgrades(
    engine: Engine,
    db_session,
) -> None:
    from app.db.models.production_workflow import ProductionWorkflowRun
    from app.db.models.v2_effect import V2ProductionEffectLedger
    from app.core.time import utc_now

    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    scope = phase3["_scope"](db_session)
    package = phase3["_create_package"](db_session, scope)
    run = ProductionWorkflowRun(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        video_project_id=scope.project.id,
        production_lane=scope.project.production_lane,
        planning_source_type=scope.project.planning_source_type,
        planning_source_id=scope.project.id,
        planning_source_hash="1" * 64,
        workflow_key=uuid.uuid4().hex.ljust(64, "0"),
        start_input_hash="2" * 64,
        state="MEDIA_RUNNING",
        current_stage="MEDIA",
        production_package_artifact_version_id=package.artifact_version_id,
        production_package_hash=package.canonical_hash,
    )
    db_session.add(run)
    db_session.flush()
    now = utc_now()
    db_session.add(
        V2ProductionEffectLedger(
            workflow_run_id=run.id,
            video_project_id=scope.project.id,
            production_package_artifact_version_id=package.artifact_version_id,
            production_package_hash=package.canonical_hash,
            command_id=str(uuid.uuid4()),
            stage="MEDIA",
            operation_id=f"v2:{scope.project.id}:media",
            adapter_key="v2-local-native",
            input_hash="3" * 64,
            state="VERIFIED",
            effect_invocation_count=1,
            result_type="V2_CANONICAL_MEDIA_TIMELINE",
            result_ref="v2-effect://migration/timeline",
            result_hash="4" * 64,
            result_payload={"migration_guard": True},
            authority_refs={},
            effect_journal={"state": "VERIFIED"},
            started_at=now,
            completed_at=now,
        )
    )
    db_session.commit()

    config = _alembic_config()
    try:
        with pytest.raises(
            RuntimeError,
            match="authoritative V2 production effect rows exist",
        ):
            command.downgrade(config, "0045_vcos_final_publish")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0046_vcos_v2_effect_ledger"
            )
            assert (
                "v2_production_effect_ledger" in inspect(connection).get_table_names()
            )

        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE v2_production_effect_ledger"))
        command.downgrade(config, "0045_vcos_final_publish")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0045_vcos_final_publish"
            )
            assert (
                "v2_production_effect_ledger"
                not in inspect(connection).get_table_names()
            )
    finally:
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == "0046_vcos_v2_effect_ledger"
        )
        assert "v2_production_effect_ledger" in inspect(connection).get_table_names()


def test_0046_widens_local_renderer_and_frozen_support_authority_with_guarded_downgrade(
    engine: Engine,
) -> None:
    provider_id = uuid.uuid4()
    capability_id = uuid.uuid4()
    provider_key = f"v2-native-{uuid.uuid4().hex}"
    with engine.begin() as connection:
        role_constraint = connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid)
                FROM pg_constraint AS constraint_row
                WHERE constraint_row.conrelid =
                    'media_provider_role_profiles'::regclass
                  AND constraint_row.contype = 'c'
                  AND pg_get_constraintdef(constraint_row.oid)
                    ILIKE '%provider_type%'
                """
            )
        ).scalar_one()
        capability_constraint = connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid)
                FROM pg_constraint AS constraint_row
                WHERE constraint_row.conrelid =
                    'provider_capability_matrix_entries'::regclass
                  AND constraint_row.contype = 'c'
                  AND pg_get_constraintdef(constraint_row.oid)
                    ILIKE '%provider_type%'
                """
            )
        ).scalar_one()
        storage_constraint = connection.execute(
            text(
                """
                SELECT pg_get_constraintdef(constraint_row.oid)
                FROM pg_constraint AS constraint_row
                WHERE constraint_row.conrelid = 'cloud_media_refs'::regclass
                  AND constraint_row.contype = 'c'
                  AND pg_get_constraintdef(constraint_row.oid)
                    ILIKE '%storage_provider%'
                """
            )
        ).scalar_one()
        authority_index = connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE indexname = 'uq_artifacts_v2_authority_per_project'
                """
            )
        ).scalar_one()
        assert "LOCAL_RENDERER_CAPABILITY" in role_constraint
        assert "LOCAL_RENDERER_CAPABILITY" in capability_constraint
        assert "VCOS_LOCAL_ARCHIVE" in storage_constraint
        assert "v2_frozen_support_envelope" in authority_index
        connection.execute(
            text(
                """
                INSERT INTO media_provider_role_profiles (
                    id, provider_key, provider_name, provider_type,
                    role_description, recommendation
                ) VALUES (
                    :id, :provider_key, 'V2 native renderer',
                    'LOCAL_RENDERER_CAPABILITY',
                    'Package-authorized local FFmpeg production renderer',
                    'CORE'
                )
                """
            ),
            {"id": provider_id, "provider_key": provider_key},
        )
        connection.execute(
            text(
                """
                INSERT INTO provider_capability_matrix_entries (
                    id, provider_key, provider_type, job_type,
                    capability, capability_reason
                ) VALUES (
                    :id, :provider_key, 'LOCAL_RENDERER_CAPABILITY',
                    'FINAL_RENDER', 'SUPPORTED',
                    'Native FFmpeg capability is present'
                )
                """
            ),
            {"id": capability_id, "provider_key": provider_key},
        )

    config = _alembic_config()
    try:
        with pytest.raises(
            RuntimeError,
            match="LOCAL_RENDERER_CAPABILITY or frozen V2 support authority",
        ):
            command.downgrade(config, "0045_vcos_final_publish")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0046_vcos_v2_effect_ledger"
            )
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM provider_capability_matrix_entries WHERE id = :id"),
                {"id": capability_id},
            )
            connection.execute(
                text("DELETE FROM media_provider_role_profiles WHERE id = :id"),
                {"id": provider_id},
            )


def test_0046_downgrade_fails_closed_for_local_archive_authority(
    engine: Engine,
) -> None:
    cloud_media_ref_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO cloud_media_refs (
                    id, media_type, storage_provider, drive_file_id,
                    web_view_link
                ) VALUES (
                    :id, 'LONG_FORM_FINAL', 'VCOS_LOCAL_ARCHIVE',
                    :drive_file_id, :web_view_link
                )
                """
            ),
            {
                "id": cloud_media_ref_id,
                "drive_file_id": f"vcos-local:{cloud_media_ref_id}",
                "web_view_link": f"vcos-local://archive/{cloud_media_ref_id}",
            },
        )

    config = _alembic_config()
    try:
        with pytest.raises(
            RuntimeError,
            match="VCOS_LOCAL_ARCHIVE cloud archive",
        ):
            command.downgrade(config, "0045_vcos_final_publish")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0046_vcos_v2_effect_ledger"
            )
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM cloud_media_refs WHERE id = :id"),
                {"id": cloud_media_ref_id},
            )


def test_0045_downgrade_fails_closed_for_final_publish_authority(
    engine: Engine,
    db_session,
) -> None:
    phase5 = runpy.run_path(str(ROOT / "tests/test_phase5_final_publish.py"))
    ready = phase5["_ready_final"](db_session)
    assert ready.candidate is not None
    db_session.commit()

    config = _alembic_config()
    try:
        with pytest.raises(
            RuntimeError,
            match="authoritative final-review/publish v2 rows exist",
        ):
            command.downgrade(config, "0044_vcos_orchestration")

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0046_vcos_v2_effect_ledger"
            )

        # Immutable authority rows are intentionally undeletable in production.
        # TRUNCATE is scoped to this disposable test database so the migration's
        # no-authority downgrade and immediate recovery path can also be proven.
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE final_review_candidates CASCADE"))

        command.downgrade(config, "0044_vcos_orchestration")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0044_vcos_orchestration"
            )
    finally:
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == "0046_vcos_v2_effect_ledger"
        )


def test_0044_downgrade_fails_closed_for_durable_workflow_authority(
    engine: Engine,
) -> None:
    config = _alembic_config()
    company_id = uuid.uuid4()
    channel_id = uuid.uuid4()
    workflow_id = uuid.uuid4()
    command.downgrade(config, "0044_vcos_orchestration")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO companies (
                        id, name, slug, description, status, default_currency,
                        created_at, updated_at
                    )
                    VALUES (
                        :id, '0044 Guard', :slug, '', 'active', 'USD',
                        now(), now()
                    )
                    """
                ),
                {
                    "id": company_id,
                    "slug": f"migration-0044-{company_id.hex[:12]}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO channel_workspaces (
                        id,
                        company_id,
                        key,
                        name,
                        status,
                        primary_language,
                        default_timezone
                    )
                    VALUES (
                        :id,
                        :company_id,
                        :key,
                        '0044 Guard Channel',
                        'active',
                        'en',
                        'UTC'
                    )
                    """
                ),
                {
                    "id": channel_id,
                    "company_id": company_id,
                    "key": f"migration-0044-{channel_id.hex[:12]}",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO production_workflow_runs (
                        id,
                        company_id,
                        channel_workspace_id,
                        production_lane,
                        planning_source_type,
                        planning_source_id,
                        planning_source_hash,
                        workflow_key,
                        start_input_hash,
                        state,
                        current_stage
                    )
                    VALUES (
                        :id,
                        :company_id,
                        :channel_id,
                        'LONG_FORM',
                        'LONG_FORM_PLAN',
                        :source_id,
                        :planning_hash,
                        :workflow_key,
                        :start_hash,
                        'PLANNING_PENDING',
                        'PLANNING'
                    )
                    """
                ),
                {
                    "id": workflow_id,
                    "company_id": company_id,
                    "channel_id": channel_id,
                    "source_id": uuid.uuid4(),
                    "planning_hash": "a" * 64,
                    "workflow_key": "b" * 64,
                    "start_hash": "c" * 64,
                },
            )

        with pytest.raises(
            RuntimeError,
            match="authoritative durable orchestration rows exist",
        ):
            command.downgrade(config, "0043_vcos_phase123")

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0044_vcos_orchestration"
            )

        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM production_workflow_runs WHERE id = :id"),
                {"id": workflow_id},
            )
            connection.execute(
                text("DELETE FROM channel_workspaces WHERE id = :id"),
                {"id": channel_id},
            )
            connection.execute(
                text("DELETE FROM companies WHERE id = :id"),
                {"id": company_id},
            )

        command.downgrade(config, "0043_vcos_phase123")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0043_vcos_phase123"
            )
            assert (
                "production_workflow_runs" not in inspect(connection).get_table_names()
            )

        command.upgrade(config, "0044_vcos_orchestration")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0044_vcos_orchestration"
            )
            assert "production_workflow_runs" in inspect(connection).get_table_names()
    finally:
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert (
            connection.execute(
                text("select version_num from alembic_version")
            ).scalar_one()
            == "0046_vcos_v2_effect_ledger"
        )


def test_0043_downgrade_fails_closed_for_canonical_identity(
    engine: Engine,
) -> None:
    canonical_user_id = uuid.uuid4()
    operator_user_id = uuid.uuid4()
    email = f"downgrade-guard-{uuid.uuid4()}@example.com"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO users (id, email, display_name, status)
                VALUES (:id, :email, 'Downgrade Guard', 'active')
                """
            ),
            {"id": canonical_user_id, "email": email},
        )
        connection.execute(
            text(
                """
                INSERT INTO operator_users (
                    id,
                    canonical_user_id,
                    email,
                    password_hash,
                    display_name,
                    role,
                    status
                )
                VALUES (
                    :id,
                    :canonical_user_id,
                    :email,
                    'not-used',
                    'Downgrade Guard',
                    'READ_ONLY',
                    'ACTIVE'
                )
                """
            ),
            {
                "id": operator_user_id,
                "canonical_user_id": canonical_user_id,
                "email": email,
            },
        )

    try:
        with pytest.raises(
            RuntimeError,
            match="authoritative Phase 1/v2 rows exist",
        ):
            command.downgrade(_alembic_config(), "0042_mr1_final_lineage")

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("select version_num from alembic_version")
                ).scalar_one()
                == "0046_vcos_v2_effect_ledger"
            )
    finally:
        command.upgrade(_alembic_config(), "head")
