"""Remove active Shorts authority and refactor Daily production into research.

Revision ID: 0047_vcos_remove_shorts
Revises: 0046_vcos_v2_effect_ledger
Create Date: 2026-07-30 00:00:00

The migration records a deterministic inventory in the existing audit ledger
before it removes active runtime authority.  Immutable artifact versions and
versioned catalog/prompt contents are not rewritten: their owning/current
authority is archived instead.

Downgrade restores schema only.  It is refused after a post-removal editorial
write exists because translating that authority back into the former Daily
production vocabulary would corrupt meaning.
"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import conv


revision: str = "0047_vcos_remove_shorts"
down_revision: str | None = "0046_vcos_v2_effect_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())
UUID = postgresql.UUID(as_uuid=True)

_REMOVED_TABLES = (
    "creatomate_render_assets",
    "short_render_packages",
    "upload_cards",
    "promote_short_to_long_candidates",
    "derivative_originality_checks",
    "short_candidate_scores",
    "short_render_plans",
    "short_candidates",
    "asset_reuse_index_entries",
    "reusable_artifacts",
    "derivative_release_plans",
    "cross_platform_funnel_packages",
    "usage_savings_ledger_entries",
    "originality_budgets",
    "content_derivative_graph_edges",
)

_SHORT_AGENT_KEYS = (
    "ShortCandidateExtractor",
    "ShortCandidateRanker",
    "DerivativeOriginalityReviewer",
    "UploadCardCopyAgent",
)

_SHORT_CATALOG_KEYS = (
    "cta_type_catalog",
    "daily_run_status_catalog",
    "derivative_type_catalog",
    "idea_decision_status_catalog",
    "music_policy_catalog",
    "originality_check_result_catalog",
    "release_plan_state_catalog",
    "reusable_artifact_state_catalog",
    "reusable_artifact_type_catalog",
    "short_candidate_state_catalog",
    "short_crop_strategy_catalog",
    "short_render_package_state_catalog",
    "short_visual_source_catalog",
    "upload_card_state_catalog",
)

_SHORT_ARTIFACT_TYPES = (
    "derivative_release_plan",
    "short_candidate",
    "short_render_package",
    "upload_card",
)

_PURGE_TRIGGER_GUARDS = (
    (
        "v2_production_effect_ledger",
        "trg_prevent_verified_v2_effect_change",
    ),
    (
        "workflow_command_receipts",
        "trg_prevent_vcos_workflow_receipt_change",
    ),
    (
        "final_review_candidates",
        "trg_prevent_final_review_candidates_change",
    ),
    (
        "final_video_decisions",
        "trg_prevent_final_video_decisions_change",
    ),
    (
        "series_episode_publications",
        "trg_prevent_series_episode_publications_change",
    ),
)


def upgrade() -> None:
    _prepare_short_purge_closure()
    _archive_removal_inventory()
    _verify_removal_inventory()
    _archive_versioned_runtime_authority()
    # Remove the dedicated Short tables before deleting their parent projects;
    # several 0046-only FKs are not represented in the post-0047 ORM metadata.
    _drop_short_only_tables()
    _suspend_purge_trigger_guards()
    _purge_short_runtime_rows()
    _restore_purge_trigger_guards()
    _verify_purge_trigger_guards()
    _refactor_editorial_research()
    _migrate_mutable_json_authority()
    _reconcile_model_metadata()
    _narrow_long_form_authorities()
    _remove_remaining_short_checks()


def downgrade() -> None:
    _fail_closed_if_post_removal_authority_exists()
    _restore_mutable_json_authority()
    _restore_daily_schema()
    _restore_removed_table_shells()
    _restore_historical_long_form_checks()
    _restore_preexisting_model_metadata()


def _prepare_short_purge_closure() -> None:
    """Freeze the exact row-ID closure used by both inventory and purge."""

    op.execute(
        """
        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_workflow_ids
        ON COMMIT DROP AS
        SELECT id
        FROM production_workflow_runs
        WHERE production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
           OR planning_source_type IN ('DAILY_IDEA','DERIVED_SHORT');

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_project_ids
        ON COMMIT DROP AS
        SELECT id
        FROM video_projects
        WHERE schema_version = 'v2'
          AND (
            production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
            OR planning_source_type IN ('DAILY_IDEA','DERIVED_SHORT')
          )
        UNION
        SELECT video_project_id
        FROM production_workflow_runs
        WHERE id IN (SELECT id FROM vcos_0047_short_workflow_ids)
          AND video_project_id IS NOT NULL;

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_admission_ids
        ON COMMIT DROP AS
        SELECT id
        FROM project_admission_decisions
        WHERE schema_version = 'v2'
          AND (
            production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
            OR planning_source_type IN ('DAILY_IDEA','DERIVED_SHORT')
            OR admitted_video_project_id IN (
                SELECT id FROM vcos_0047_short_project_ids
            )
          )
        UNION
        SELECT project_admission_decision_id
        FROM video_projects
        WHERE id IN (SELECT id FROM vcos_0047_short_project_ids)
          AND project_admission_decision_id IS NOT NULL;

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_slot_ids
        ON COMMIT DROP AS
        SELECT id
        FROM editorial_calendar_slots
        WHERE schema_version = 'v2'
          AND production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
        UNION
        SELECT editorial_calendar_slot_id
        FROM project_admission_decisions
        WHERE id IN (SELECT id FROM vcos_0047_short_admission_ids)
          AND editorial_calendar_slot_id IS NOT NULL;

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_uploaded_ids
        ON COMMIT DROP AS
        SELECT id
        FROM uploaded_videos
        WHERE platform = 'YOUTUBE_SHORTS'
           OR production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
           OR id IN (
                SELECT uploaded_video_id
                FROM production_workflow_runs
                WHERE id IN (SELECT id FROM vcos_0047_short_workflow_ids)
                  AND uploaded_video_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_final_review_ids
        ON COMMIT DROP AS
        SELECT id
        FROM final_review_candidates
        WHERE production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
           OR workflow_run_id IN (
                SELECT id FROM vcos_0047_short_workflow_ids
           )
           OR id IN (
                SELECT final_review_candidate_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND final_review_candidate_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_final_decision_ids
        ON COMMIT DROP AS
        SELECT id
        FROM final_video_decisions
        WHERE final_review_candidate_id IN (
                SELECT id FROM vcos_0047_short_final_review_ids
              )
           OR id IN (
                SELECT final_video_decision_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND final_video_decision_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_upload_task_ids
        ON COMMIT DROP AS
        SELECT id
        FROM human_upload_tasks
        WHERE target_platform IN
                ('YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS')
           OR production_lane IN ('DAILY_SHORT','LONG_DERIVED_SHORT')
           OR actual_uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
           )
           OR final_review_candidate_id IN (
                SELECT id FROM vcos_0047_short_final_review_ids
           )
           OR final_video_decision_id IN (
                SELECT id FROM vcos_0047_short_final_decision_ids
           )
           OR id IN (
                SELECT human_upload_task_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND human_upload_task_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_confirmation_ids
        ON COMMIT DROP AS
        SELECT id
        FROM manual_publish_confirmations
        WHERE target_platform = 'YOUTUBE_SHORTS'
           OR target_surface IN ('SHORT_FORM','REELS')
           OR human_upload_task_id IN (
                SELECT id FROM vcos_0047_short_upload_task_ids
           )
           OR final_review_candidate_id IN (
                SELECT id FROM vcos_0047_short_final_review_ids
           )
           OR final_video_decision_id IN (
                SELECT id FROM vcos_0047_short_final_decision_ids
           )
           OR id IN (
                SELECT manual_publish_confirmation_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND manual_publish_confirmation_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_handoff_ids
        ON COMMIT DROP AS
        SELECT id
        FROM publish_handoff_packages
        WHERE target_platform = 'YOUTUBE_SHORTS'
           OR target_surface IN ('SHORT_FORM','REELS')
           OR id IN (
                SELECT publish_handoff_package_id
                FROM manual_publish_confirmations
                WHERE id IN (SELECT id FROM vcos_0047_short_confirmation_ids)
                  AND publish_handoff_package_id IS NOT NULL
           )
           OR id IN (
                SELECT publish_handoff_package_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND publish_handoff_package_id IS NOT NULL
           )
           OR id IN (
                SELECT publish_package_id
                FROM human_upload_tasks
                WHERE id IN (SELECT id FROM vcos_0047_short_upload_task_ids)
                  AND publish_package_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_final_media_ids
        ON COMMIT DROP AS
        SELECT id
        FROM final_media_refs
        WHERE media_type = 'SHORT_FINAL'
           OR uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
           )
           OR id IN (
                SELECT final_media_ref_id
                FROM final_review_candidates
                WHERE id IN (SELECT id FROM vcos_0047_short_final_review_ids)
           )
           OR id IN (
                SELECT final_media_ref_id
                FROM final_video_decisions
                WHERE id IN (SELECT id FROM vcos_0047_short_final_decision_ids)
           )
           OR id IN (
                SELECT final_media_ref_id
                FROM uploaded_videos
                WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
                  AND final_media_ref_id IS NOT NULL
           )
           OR id IN (
                SELECT final_media_ref_id
                FROM production_workflow_runs
                WHERE id IN (SELECT id FROM vcos_0047_short_workflow_ids)
                  AND final_media_ref_id IS NOT NULL
           );

        CREATE TEMPORARY TABLE IF NOT EXISTS vcos_0047_short_cloud_media_ids
        ON COMMIT DROP AS
        SELECT id
        FROM cloud_media_refs
        WHERE media_type = 'SHORT_FINAL'
           OR uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
           )
           OR id IN (
                SELECT cloud_media_ref_id
                FROM final_media_refs
                WHERE id IN (SELECT id FROM vcos_0047_short_final_media_ids)
                  AND cloud_media_ref_id IS NOT NULL
           );
        """
    )


def _archive_removal_inventory() -> None:
    """Persist counts and an integrity hash before destructive cleanup."""

    op.execute(
        """
        CREATE OR REPLACE FUNCTION pg_temp.vcos_0047_table_count(
            qualified_table_name text
        )
        RETURNS bigint
        LANGUAGE plpgsql
        AS $$
        DECLARE
            row_count bigint;
        BEGIN
            IF to_regclass(qualified_table_name) IS NULL THEN
                RETURN 0;
            END IF;
            EXECUTE format(
                'SELECT count(*) FROM %s',
                to_regclass(qualified_table_name)
            )
            INTO row_count;
            RETURN row_count;
        END
        $$;
        """
    )
    table_count_pairs = ",\n".join(
        f"'{table_name}', pg_temp.vcos_0047_table_count('public.{table_name}')"
        for table_name in _REMOVED_TABLES
    )
    op.execute(
        sa.text(
            f"""
            WITH inventory AS (
                SELECT jsonb_build_object(
                    'revision', '0047_vcos_remove_shorts',
                    'classified_at', statement_timestamp(),
                    'short_only_tables', jsonb_build_object(
                        {table_count_pairs}
                    ),
                    'short_workflow_runs', (
                        SELECT count(*)
                        FROM vcos_0047_short_workflow_ids
                    ),
                    'short_outbox_events', (
                        SELECT count(*) FROM domain_events
                        WHERE workflow_run_id IN (
                                SELECT id
                                FROM vcos_0047_short_workflow_ids
                              )
                           OR lower(event_type) LIKE '%short%'
                           OR payload ->> 'production_lane' IN
                            ('DAILY_SHORT','LONG_DERIVED_SHORT')
                           OR payload ->> 'planning_source_type' IN
                            ('DAILY_IDEA','DERIVED_SHORT')
                    ),
                    'short_dead_letters', (
                        SELECT count(*) FROM dead_letter_jobs
                        WHERE workflow_run_id IN (
                                SELECT id
                                FROM vcos_0047_short_workflow_ids
                              )
                           OR lower(job_type) LIKE '%short%'
                           OR lower(coalesce(target_type, '')) LIKE '%short%'
                           OR lower(coalesce(payload_ref, '')) LIKE '%short%'
                    ),
                    'short_effect_rows', (
                        SELECT count(*)
                        FROM v2_production_effect_ledger
                        WHERE workflow_run_id IN (
                            SELECT id FROM vcos_0047_short_workflow_ids
                        )
                    ),
                    'purge_projection_counts', jsonb_build_object(
                        'workflow_receipts', (
                            SELECT count(*)
                            FROM workflow_command_receipts
                            WHERE workflow_run_id IN (
                                SELECT id
                                FROM vcos_0047_short_workflow_ids
                            )
                        ),
                        'ops_incidents', (
                            SELECT count(*)
                            FROM ops_incidents
                            WHERE workflow_run_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_workflow_ids
                                  )
                               OR lower(coalesce(stage, '')) LIKE '%short%'
                               OR lower(metadata::text) SIMILAR TO
                                    '%(daily.short|derived.short|short.form|youtube.shorts)%'
                        ),
                        'final_review_candidates', (
                            SELECT count(*)
                            FROM vcos_0047_short_final_review_ids
                        ),
                        'final_video_decisions', (
                            SELECT count(*)
                            FROM vcos_0047_short_final_decision_ids
                        ),
                        'human_upload_tasks', (
                            SELECT count(*)
                            FROM vcos_0047_short_upload_task_ids
                        ),
                        'manual_publish_confirmations', (
                            SELECT count(*)
                            FROM vcos_0047_short_confirmation_ids
                        ),
                        'publish_handoff_packages', (
                            SELECT count(*)
                            FROM vcos_0047_short_handoff_ids
                        ),
                        'final_media_refs', (
                            SELECT count(*)
                            FROM vcos_0047_short_final_media_ids
                        ),
                        'cloud_media_refs', (
                            SELECT count(*)
                            FROM vcos_0047_short_cloud_media_ids
                        ),
                        'series_episode_publications', (
                            SELECT count(*)
                            FROM series_episode_publications
                            WHERE video_project_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_project_ids
                                  )
                               OR uploaded_video_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_uploaded_ids
                                  )
                               OR final_video_decision_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_final_decision_ids
                                  )
                        ),
                        'routing_decisions', (
                            SELECT count(*)
                            FROM media_render_routing_decisions
                            WHERE lower(job_type) SIMILAR TO
                                '%(short|vertical.9.16|derived.short)%'
                        ),
                        'provider_capability_entries', (
                            SELECT count(*)
                            FROM provider_capability_matrix_entries
                            WHERE lower(
                                job_type || ' ' ||
                                supported_aspect_ratios::text || ' ' ||
                                supported_outputs::text || ' ' ||
                                capability_reason
                            ) SIMILAR TO '%(short|9:16|vertical|reels)%'
                        ),
                        'metric_definitions', (
                            SELECT count(*)
                            FROM metric_definition_versions
                            WHERE platform = 'YOUTUBE_SHORTS'
                        ),
                        'hero_assets', (
                            SELECT count(*)
                            FROM ai_hero_assets
                            WHERE intended_usage = 'SHORT_HOOK'
                        ),
                        'media_offload_jobs', (
                            SELECT count(*)
                            FROM media_offload_jobs
                            WHERE target_media_type = 'SHORT_FINAL'
                        )
                    ),
                    'verified_public_short_rows', (
                        SELECT jsonb_build_object(
                            'policy',
                                'AUDIT_ARCHIVE_THEN_PURGE_ACTIVE_AUTHORITY',
                            'affected_row_count', count(*),
                            'rows', coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'uploaded_video_id', id,
                                        'company_id', company_id,
                                        'channel_workspace_id',
                                            channel_workspace_id,
                                        'video_project_id', video_project_id,
                                        'platform', platform,
                                        'platform_video_id', platform_video_id,
                                        'video_url', video_url,
                                        'published_at', published_at,
                                        'publish_status', publish_status,
                                        'verification_status',
                                            verification_status,
                                        'production_lane', production_lane,
                                        'reviewed_checksum',
                                            reviewed_checksum,
                                        'final_media_ref_id',
                                            final_media_ref_id,
                                        'production_package_hash',
                                            production_package_hash,
                                        'lineage_refs', lineage_refs,
                                        'row_evidence_hash',
                                            encode(
                                                sha256(
                                                    convert_to(
                                                        jsonb_build_object(
                                                            'id', id,
                                                            'platform',
                                                                platform,
                                                            'platform_video_id',
                                                                platform_video_id,
                                                            'video_url',
                                                                video_url,
                                                            'published_at',
                                                                published_at,
                                                            'reviewed_checksum',
                                                                reviewed_checksum,
                                                            'lineage_refs',
                                                                lineage_refs
                                                        )::text,
                                                        'UTF8'
                                                    )
                                                ),
                                                'hex'
                                            )
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                        )
                        FROM uploaded_videos
                        WHERE id IN (
                            SELECT id FROM vcos_0047_short_uploaded_ids
                        )
                    ),
                    'short_planning_authority', jsonb_build_object(
                        'policy',
                            'SEALED_AUDIT_THEN_PURGE_OR_FAIL_CLOSED_ON_IMMUTABLE_GRAPH',
                        'editorial_slots', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'id', id,
                                        'company_id', company_id,
                                        'channel_workspace_id',
                                            channel_workspace_id,
                                        'policy_snapshot_id',
                                            policy_snapshot_id,
                                        'slot_type', slot_type,
                                        'status', status,
                                        'production_lane',
                                            production_lane,
                                        'assignment_mode',
                                            assignment_mode
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                            FROM editorial_calendar_slots
                            WHERE id IN (
                                SELECT id FROM vcos_0047_short_slot_ids
                            )
                        ),
                        'admission_decisions', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'id', id,
                                        'company_id', company_id,
                                        'channel_workspace_id',
                                            channel_workspace_id,
                                        'admitted_video_project_id',
                                            admitted_video_project_id,
                                        'planning_source_type',
                                            planning_source_type,
                                        'production_lane',
                                            production_lane,
                                        'decision', decision,
                                        'decision_hash', decision_hash
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                            FROM project_admission_decisions
                            WHERE id IN (
                                SELECT id FROM vcos_0047_short_admission_ids
                            )
                        ),
                        'video_projects', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'id', id,
                                        'company_id', company_id,
                                        'channel_workspace_id',
                                            channel_workspace_id,
                                        'policy_snapshot_id',
                                            policy_snapshot_id,
                                        'project_admission_decision_id',
                                            project_admission_decision_id,
                                        'planning_source_type',
                                            planning_source_type,
                                        'production_lane',
                                            production_lane,
                                        'status', status,
                                        'render_eligible',
                                            render_eligible
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                            FROM video_projects
                            WHERE id IN (
                                SELECT id FROM vcos_0047_short_project_ids
                            )
                        )
                    ),
                    'guarded_immutable_rows', jsonb_build_object(
                        'policy',
                            'SEALED_EXACT_ROW_EVIDENCE_BEFORE_PURGE',
                        'v2_effects', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(effect_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(effect_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY effect_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM v2_production_effect_ledger AS effect_row
                            WHERE effect_row.workflow_run_id IN (
                                SELECT id
                                FROM vcos_0047_short_workflow_ids
                            )
                        ),
                        'workflow_receipts', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(receipt_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(receipt_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY receipt_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM workflow_command_receipts AS receipt_row
                            WHERE receipt_row.workflow_run_id IN (
                                SELECT id
                                FROM vcos_0047_short_workflow_ids
                            )
                        ),
                        'final_review_candidates', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(review_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(review_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY review_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM final_review_candidates AS review_row
                            WHERE review_row.id IN (
                                SELECT id
                                FROM vcos_0047_short_final_review_ids
                            )
                        ),
                        'final_video_decisions', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(decision_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(decision_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY decision_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM final_video_decisions AS decision_row
                            WHERE decision_row.id IN (
                                SELECT id
                                FROM vcos_0047_short_final_decision_ids
                            )
                        ),
                        'series_episode_publications', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(publication_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(publication_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY publication_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM series_episode_publications AS publication_row
                            WHERE publication_row.video_project_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_project_ids
                               )
                               OR publication_row.uploaded_video_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_uploaded_ids
                               )
                               OR publication_row.final_video_decision_id IN (
                                    SELECT id
                                    FROM vcos_0047_short_final_decision_ids
                               )
                        ),
                        'project_admission_decisions', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'row', to_jsonb(admission_row),
                                        'row_hash', encode(
                                            sha256(
                                                convert_to(
                                                    to_jsonb(admission_row)::text,
                                                    'UTF8'
                                                )
                                            ),
                                            'hex'
                                        )
                                    )
                                    ORDER BY admission_row.id
                                ),
                                '[]'::jsonb
                            )
                            FROM project_admission_decisions AS admission_row
                            WHERE admission_row.id IN (
                                SELECT id
                                FROM vcos_0047_short_admission_ids
                            )
                        ),
                        'owned_artifact_versions', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'artifact_id', artifact.id,
                                        'artifact_version_id',
                                            artifact_version.id,
                                        'artifact_type',
                                            artifact.artifact_type,
                                        'content_hash',
                                            artifact_version.content_hash
                                    )
                                    ORDER BY artifact_version.id
                                ),
                                '[]'::jsonb
                            )
                            FROM artifacts AS artifact
                            JOIN artifact_versions AS artifact_version
                              ON artifact_version.artifact_id = artifact.id
                            WHERE artifact.video_project_id IN (
                                SELECT id FROM vcos_0047_short_project_ids
                            )
                        )
                    ),
                    'legacy_daily_runs', (
                        SELECT count(*) FROM channel_daily_runs
                    ),
                    'legacy_daily_candidates', (
                        SELECT count(*) FROM daily_idea_decisions
                    ),
                    'contaminated_active_catalog_versions', (
                        SELECT count(*) FROM config_catalog_versions
                        WHERE lower(status) = 'active'
                          AND (
                            catalog_key = ANY (
                                ARRAY[{_sql_string_array(_SHORT_CATALOG_KEYS)}]
                            )
                            OR {
                _explicit_short_text_predicate(
                    "concat_ws(' ', catalog_key, source_path, content::text)"
                )
            }
                          )
                    ),
                    'short_prompt_authorities', (
                        SELECT
                            (SELECT count(*) FROM agent_prompt_profiles
                             WHERE agent_key = ANY (
                                ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}]
                             ))
                          + (SELECT count(*) FROM prompt_template_records
                             WHERE agent_key = ANY (
                                ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}]
                             ))
                          + (SELECT count(*) FROM prompt_contract_versions
                             WHERE agent_key = ANY (
                                ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}]
                             ))
                    ),
                    'immutable_artifact_key_compatibility', (
                        SELECT jsonb_build_object(
                            'policy', 'PRESERVE_BYTES_ARCHIVE_CURRENT_AUTHORITY',
                            'affected_version_count', count(*),
                            'versions', coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'artifact_version_id', av.id,
                                        'artifact_id', av.artifact_id,
                                        'content_hash', av.content_hash,
                                        'is_current_authority',
                                            a.current_version_id = av.id
                                    )
                                    ORDER BY av.id
                                ),
                                '[]'::jsonb
                            )
                        )
                        FROM artifact_versions AS av
                        JOIN artifacts AS a ON a.id = av.artifact_id
                        WHERE concat_ws(
                            ' ',
                            av.content::text,
                            av.packaging_metadata::text,
                            av.source_manifest::text,
                            av.evidence_refs::text,
                            av.context_refs::text,
                            av.claim_refs::text
                        ) LIKE '%"daily_idea_decision_ref"%'
                           OR concat_ws(
                            ' ',
                            av.content::text,
                            av.packaging_metadata::text,
                            av.source_manifest::text,
                            av.evidence_refs::text,
                            av.context_refs::text,
                            av.claim_refs::text
                           ) LIKE '%"approved_daily_idea"%'
                    ),
                    'immutable_m5_snapshot_compatibility', jsonb_build_object(
                        'policy',
                            'PRESERVE_ROWS_ENFORCE_EDITORIAL_ON_NEW_WRITES',
                        'retrieval_plans', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'id', id,
                                        'purpose', purpose,
                                        'plan_hash', plan_hash
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                            FROM retrieval_plan_snapshots
                            WHERE purpose = 'DAILY_IDEA'
                        ),
                        'context_packs', (
                            SELECT coalesce(
                                jsonb_agg(
                                    jsonb_build_object(
                                        'id', id,
                                        'purpose', purpose,
                                        'pack_hash', pack_hash
                                    )
                                    ORDER BY id
                                ),
                                '[]'::jsonb
                            )
                            FROM context_pack_snapshots
                            WHERE purpose = 'DAILY_IDEA'
                        )
                    )
                ) AS payload
            ),
            sealed AS (
                SELECT payload || jsonb_build_object(
                    'archive_hash',
                    encode(
                        sha256(convert_to(payload::text, 'UTF8')),
                        'hex'
                    )
                ) AS payload
                FROM inventory
            )
            INSERT INTO audit_events (
                id,
                event_type,
                actor_type,
                actor_id,
                target_type,
                target_id,
                company_id,
                correlation_id,
                reason_code,
                payload,
                occurred_at,
                created_at
            )
            SELECT
                gen_random_uuid(),
                'VCOS_SHORTS_REMOVAL_INVENTORY_ARCHIVED',
                'SYSTEM_MIGRATION',
                NULL,
                'DATABASE_SCHEMA',
                NULL,
                NULL,
                'alembic:0047_vcos_remove_shorts',
                'ACTIVE_SHORTS_AUTHORITY_REMOVED',
                payload,
                statement_timestamp(),
                statement_timestamp()
            FROM sealed
            """
        )
    )


def _verify_removal_inventory() -> None:
    """Fail closed unless the immutable audit payload verifies byte-for-byte."""

    op.execute(
        """
        DO $$
        DECLARE
            archived_payload jsonb;
            expected_hash text;
        BEGIN
            SELECT payload
            INTO archived_payload
            FROM audit_events
            WHERE event_type = 'VCOS_SHORTS_REMOVAL_INVENTORY_ARCHIVED'
              AND correlation_id = 'alembic:0047_vcos_remove_shorts'
            ORDER BY created_at DESC, id DESC
            LIMIT 1;

            IF archived_payload IS NULL THEN
                RAISE EXCEPTION
                    '0047 immutable Shorts removal inventory is missing';
            END IF;

            expected_hash := encode(
                sha256(
                    convert_to(
                        (archived_payload - 'archive_hash')::text,
                        'UTF8'
                    )
                ),
                'hex'
            );
            IF archived_payload ->> 'archive_hash' IS DISTINCT FROM expected_hash
            THEN
                RAISE EXCEPTION
                    '0047 immutable Shorts removal inventory hash mismatch';
            END IF;
        END
        $$;
        """
    )


def _suspend_purge_trigger_guards() -> None:
    """Suspend only the immutable guards covered by the sealed purge archive.

    PostgreSQL takes an ACCESS EXCLUSIVE lock for each ALTER TABLE. Alembic
    executes the revision transactionally, so any later failure rolls these
    changes back together with the purge.
    """

    for table_name, trigger_name in _PURGE_TRIGGER_GUARDS:
        op.execute(f'ALTER TABLE "{table_name}" DISABLE TRIGGER "{trigger_name}"')


def _restore_purge_trigger_guards() -> None:
    for table_name, trigger_name in _PURGE_TRIGGER_GUARDS:
        op.execute(f'ALTER TABLE "{table_name}" ENABLE TRIGGER "{trigger_name}"')


def _verify_purge_trigger_guards() -> None:
    expected = ",\n".join(
        f"('{table_name}', '{trigger_name}')"
        for table_name, trigger_name in _PURGE_TRIGGER_GUARDS
    )
    op.execute(
        f"""
        DO $$
        DECLARE
            disabled_count integer;
        BEGIN
            SELECT count(*)
            INTO disabled_count
            FROM (VALUES {expected}) AS expected(table_name, trigger_name)
            LEFT JOIN pg_class AS relation
              ON relation.oid =
                    to_regclass('public.' || expected.table_name)
            LEFT JOIN pg_trigger AS trigger_row
              ON trigger_row.tgrelid = relation.oid
             AND trigger_row.tgname = expected.trigger_name
             AND NOT trigger_row.tgisinternal
            WHERE trigger_row.oid IS NULL
               OR trigger_row.tgenabled <> 'O';

            IF disabled_count <> 0 THEN
                RAISE EXCEPTION
                    '0047 immutable purge trigger restoration failed';
            END IF;
        END
        $$;
        """
    )


def _archive_versioned_runtime_authority() -> None:
    # Catalog versions are evidence.  Archive contaminated active versions;
    # never rewrite their content or content_hash.
    op.execute(
        f"""
        UPDATE config_catalog_versions
        SET status = 'archived'
        WHERE lower(status) = 'active'
          AND (
            catalog_key = ANY (
                ARRAY[{_sql_string_array(_SHORT_CATALOG_KEYS)}]
            )
            OR {
            _explicit_short_text_predicate(
                "concat_ws(' ', catalog_key, source_path, content::text)"
            )
        }
          )
        """
    )
    op.execute(
        f"""
        UPDATE prompt_template_records
        SET status = 'DEPRECATED', updated_at = statement_timestamp()
        WHERE agent_key = ANY (ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}])
          AND status <> 'DEPRECATED'
        """
    )
    op.execute(
        f"""
        UPDATE prompt_contract_versions
        SET status = 'DEPRECATED', updated_at = statement_timestamp()
        WHERE agent_key = ANY (ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}])
          AND status <> 'DEPRECATED'
        """
    )
    # Profiles are mutable current authority rather than versioned evidence.
    op.execute(
        f"""
        DELETE FROM agent_prompt_profiles
        WHERE agent_key = ANY (ARRAY[{_sql_string_array(_SHORT_AGENT_KEYS)}])
        """
    )
    # Preserve immutable ArtifactVersion rows/hashes while severing a
    # contaminated current pointer from active runtime authority.
    op.execute(
        f"""
        UPDATE artifacts AS a
        SET status = 'archived',
            current_version_id = NULL,
            updated_at = statement_timestamp()
        FROM artifact_versions AS av
        WHERE a.current_version_id = av.id
          AND (
            lower(a.artifact_type) = ANY (
                ARRAY[{_sql_string_array(_SHORT_ARTIFACT_TYPES)}]
            )
            OR {
            _explicit_short_text_predicate(
                "concat_ws(' ', a.artifact_type, av.content::text, "
                "av.packaging_metadata::text)"
            )
        }
            OR concat_ws(
                ' ',
                av.content::text,
                av.packaging_metadata::text,
                av.source_manifest::text,
                av.evidence_refs::text,
                av.context_refs::text,
                av.claim_refs::text
            ) LIKE '%"daily_idea_decision_ref"%'
            OR concat_ws(
                ' ',
                av.content::text,
                av.packaging_metadata::text,
                av.source_manifest::text,
                av.evidence_refs::text,
                av.context_refs::text,
                av.claim_refs::text
            ) LIKE '%"approved_daily_idea"%'
          )
        """
    )


def _purge_short_runtime_rows() -> None:
    # The verified current environment has no public Shorts production.  These
    # statements still remove stale tests/outbox/workflow projections in a
    # deterministic FK-safe order.  All deletes below consume the same frozen
    # ID closure that was archived and hash-verified before any mutation.
    op.execute(
        """
        DELETE FROM v2_production_effect_ledger
        WHERE workflow_run_id IN (
            SELECT id FROM vcos_0047_short_workflow_ids
        )
        """
    )
    op.execute(
        """
        DELETE FROM workflow_command_receipts
        WHERE workflow_run_id IN (
            SELECT id FROM vcos_0047_short_workflow_ids
        )
        """
    )
    op.execute(
        """
        DELETE FROM dead_letter_jobs
        WHERE workflow_run_id IN (
                SELECT id FROM vcos_0047_short_workflow_ids
              )
           OR lower(job_type) LIKE '%short%'
           OR lower(coalesce(target_type, '')) LIKE '%short%'
           OR lower(coalesce(payload_ref, '')) LIKE '%short%'
        """
    )
    op.execute(
        """
        DELETE FROM ops_incidents
        WHERE workflow_run_id IN (
                SELECT id FROM vcos_0047_short_workflow_ids
              )
           OR lower(coalesce(stage, '')) LIKE '%short%'
           OR lower(metadata::text) SIMILAR TO
                '%(daily.short|derived.short|short.form|youtube.shorts)%'
        """
    )
    op.execute(
        """
        DELETE FROM domain_events
        WHERE workflow_run_id IN (
                SELECT id FROM vcos_0047_short_workflow_ids
              )
           OR lower(event_type) LIKE '%short%'
           OR payload ->> 'production_lane' IN
                ('DAILY_SHORT','LONG_DERIVED_SHORT')
           OR payload ->> 'planning_source_type' IN
                ('DAILY_IDEA','DERIVED_SHORT')
        """
    )
    # Remove non-production qualification/provider rows whose only capability
    # is Short/vertical execution.
    op.execute(
        """
        DELETE FROM media_render_routing_decisions
        WHERE lower(job_type) SIMILAR TO
            '%(short|vertical.9.16|derived.short)%'
        """
    )
    op.execute(
        """
        UPDATE media_render_routing_decisions
        SET capability_entry_id = NULL
        WHERE capability_entry_id IN (
            SELECT id
            FROM provider_capability_matrix_entries
            WHERE lower(
                job_type || ' ' || supported_aspect_ratios::text || ' ' ||
                supported_outputs::text || ' ' || capability_reason
            ) SIMILAR TO '%(short|9:16|vertical|reels)%'
        )
        """
    )
    op.execute(
        """
        DELETE FROM provider_capability_matrix_entries
        WHERE lower(
            job_type || ' ' || supported_aspect_ratios::text || ' ' ||
            supported_outputs::text || ' ' || capability_reason
        ) SIMILAR TO '%(short|9:16|vertical|reels)%'
        """
    )

    # Detach preserved generic learning/memory evidence from removed public
    # Short authority.  The immutable audit manifest above retains exact
    # lineage; no surviving runtime row points back to a purged Short row.
    op.execute(
        """
        UPDATE channel_memory_items
        SET created_from_failure_trace_report_id = NULL
        WHERE created_from_failure_trace_report_id IN (
            SELECT id
            FROM failure_trace_reports
            WHERE uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
            )
        )
        """
    )
    op.execute(
        """
        UPDATE channel_memory_items
        SET created_from_recovery_proposal_id = NULL
        WHERE created_from_recovery_proposal_id IN (
            SELECT id
            FROM recovery_proposals
            WHERE uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
            )
        )
        """
    )
    for table_name, column_name in (
        ("learning_candidate_generation_runs", "uploaded_video_id"),
        ("learning_candidates", "uploaded_video_id"),
        ("learning_review_queue_items", "uploaded_video_id"),
        ("learning_to_memory_promotion_runs", "source_uploaded_video_id"),
        ("ops_incidents", "uploaded_video_id"),
        ("quality_delta_attributions", "target_uploaded_video_id"),
        ("uploaded_video_backfill_events", "uploaded_video_id"),
    ):
        op.execute(
            f"""
            UPDATE {table_name}
            SET {column_name} = NULL
            WHERE {column_name} IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
            )
            """
        )

    # Break the nullable cycles in the production/publication graph.  Marking
    # doomed v2 projections as legacy is transaction-local and allows their
    # nullable bindings to be cleared without falsifying a long-form v2 row.
    op.execute(
        """
        UPDATE uploaded_videos
        SET schema_version = 'v1',
            publish_handoff_package_id = NULL,
            manual_publish_confirmation_id = NULL,
            human_upload_task_id = NULL,
            final_review_candidate_id = NULL,
            final_video_decision_id = NULL,
            final_media_ref_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
        """
    )
    op.execute(
        """
        UPDATE human_upload_tasks
        SET schema_version = 'v1',
            actual_uploaded_video_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_upload_task_ids)
        """
    )
    op.execute(
        """
        UPDATE production_workflow_runs
        SET uploaded_video_id = NULL,
            final_review_candidate_id = NULL,
            final_media_ref_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_workflow_ids)
        """
    )
    op.execute(
        """
        UPDATE final_media_refs
        SET uploaded_video_id = NULL,
            cloud_media_ref_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_final_media_ids)
        """
    )
    op.execute(
        """
        UPDATE cloud_media_refs
        SET uploaded_video_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_cloud_media_ids)
        """
    )
    op.execute(
        """
        UPDATE character_reference_assets
        SET cloud_media_ref_id = NULL
        WHERE cloud_media_ref_id IN (
            SELECT id FROM vcos_0047_short_cloud_media_ids
        )
        """
    )
    op.execute(
        """
        UPDATE localized_subtitle_packages
        SET srt_cloud_media_ref_id = CASE
                WHEN srt_cloud_media_ref_id IN (
                    SELECT id FROM vcos_0047_short_cloud_media_ids
                ) THEN NULL
                ELSE srt_cloud_media_ref_id
            END,
            vtt_cloud_media_ref_id = CASE
                WHEN vtt_cloud_media_ref_id IN (
                    SELECT id FROM vcos_0047_short_cloud_media_ids
                ) THEN NULL
                ELSE vtt_cloud_media_ref_id
            END
        WHERE srt_cloud_media_ref_id IN (
                SELECT id FROM vcos_0047_short_cloud_media_ids
              )
           OR vtt_cloud_media_ref_id IN (
                SELECT id FROM vcos_0047_short_cloud_media_ids
              )
        """
    )
    op.execute(
        """
        UPDATE media_offload_jobs
        SET uploaded_video_id = NULL,
            source_media_ref_id = NULL,
            cloud_media_ref_id = NULL
        WHERE uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
              )
           OR source_media_ref_id IN (
                SELECT id FROM vcos_0047_short_final_media_ids
              )
           OR cloud_media_ref_id IN (
                SELECT id FROM vcos_0047_short_cloud_media_ids
              )
        """
    )

    # Required-FK projections are purged before their uploaded-video parent.
    for table_name in (
        "analytics_snapshots",
        "analytics_sync_runs",
        "engagement_diagnostic_runs",
        "engagement_snapshots",
        "failure_trace_reports",
        "metric_availability_snapshots",
        "no_view_diagnostic_runs",
        "packaging_diagnostic_runs",
        "policy_rights_diagnostic_runs",
        "post_publish_health_runs",
        "post_publish_observation_windows",
        "recovery_proposals",
        "retention_curve_snapshots",
        "retention_diagnostic_runs",
        "traffic_source_snapshots",
        "uploaded_video_metrics_summaries",
        "uploaded_video_publication_summaries",
        "uploaded_video_youtube_owner_analytics_snapshots",
        "uploaded_video_youtube_public_monitor_snapshots",
        "youtube_owner_analytics_sync_runs",
        "youtube_public_sync_runs",
    ):
        op.execute(
            f"""
            DELETE FROM {table_name}
            WHERE uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
            )
            """
        )
    op.execute(
        """
        DELETE FROM series_episode_publications
        WHERE uploaded_video_id IN (
                SELECT id FROM vcos_0047_short_uploaded_ids
              )
           OR human_upload_task_id IN (
                SELECT id FROM vcos_0047_short_upload_task_ids
              )
           OR manual_publish_confirmation_id IN (
                SELECT id FROM vcos_0047_short_confirmation_ids
              )
           OR final_video_decision_id IN (
                SELECT id FROM vcos_0047_short_final_decision_ids
              )
        """
    )
    op.execute(
        """
        DELETE FROM publish_timing_suggestions
        WHERE publish_handoff_package_id IN (
            SELECT id FROM vcos_0047_short_handoff_ids
        )
        """
    )
    op.execute(
        """
        DELETE FROM uploaded_video_backfill_events
        WHERE human_upload_task_id IN (
            SELECT id FROM vcos_0047_short_upload_task_ids
        )
        """
    )

    # Delete active Short production/publication authority leaf-first.
    op.execute(
        """
        DELETE FROM manual_publish_confirmations
        WHERE id IN (SELECT id FROM vcos_0047_short_confirmation_ids)
        """
    )
    op.execute(
        """
        DELETE FROM human_upload_tasks
        WHERE id IN (SELECT id FROM vcos_0047_short_upload_task_ids)
        """
    )
    op.execute(
        """
        DELETE FROM final_video_decisions
        WHERE id IN (SELECT id FROM vcos_0047_short_final_decision_ids)
        """
    )
    op.execute(
        """
        DELETE FROM final_review_candidates
        WHERE id IN (SELECT id FROM vcos_0047_short_final_review_ids)
        """
    )
    op.execute(
        """
        DELETE FROM final_media_refs
        WHERE id IN (SELECT id FROM vcos_0047_short_final_media_ids)
        """
    )
    op.execute(
        """
        DELETE FROM cloud_media_refs
        WHERE id IN (SELECT id FROM vcos_0047_short_cloud_media_ids)
        """
    )
    op.execute(
        """
        DELETE FROM publish_handoff_packages
        WHERE id IN (SELECT id FROM vcos_0047_short_handoff_ids)
        """
    )
    op.execute(
        """
        DELETE FROM production_workflow_runs
        WHERE id IN (SELECT id FROM vcos_0047_short_workflow_ids)
        """
    )
    op.execute(
        """
        DELETE FROM uploaded_videos
        WHERE id IN (SELECT id FROM vcos_0047_short_uploaded_ids)
        """
    )

    # Purge remaining independent enum-like rows before rebuilding checks.
    op.execute(
        """
        DELETE FROM metric_definition_versions
        WHERE platform = 'YOUTUBE_SHORTS'
        """
    )
    op.execute("DELETE FROM ai_hero_assets WHERE intended_usage = 'SHORT_HOOK'")
    op.execute("DELETE FROM media_offload_jobs WHERE target_media_type = 'SHORT_FINAL'")

    # Remove the exact planning closure archived at the start of the
    # transaction.  Nullable cycle edges on rows being removed may be cleared,
    # but any other surviving FK is evidence we cannot translate safely.
    op.execute(
        """
        SET CONSTRAINTS fk_video_projects_v2_admission DEFERRED;

        UPDATE video_projects
        SET schema_version = 'v1',
            project_admission_decision_id = NULL,
            parent_video_project_id = NULL
        WHERE id IN (SELECT id FROM vcos_0047_short_project_ids);

        UPDATE channel_daily_runs
        SET project_admission_decision_id = NULL
        WHERE project_admission_decision_id IN (
            SELECT id FROM vcos_0047_short_admission_ids
        )
        """
    )
    _fail_closed_if_fk_references_ids(
        "project_admission_decisions",
        "vcos_0047_short_admission_ids",
        "0047 refused to purge Short admissions with surviving evidence",
    )
    op.execute(
        """
        DELETE FROM project_admission_decisions
        WHERE id IN (SELECT id FROM vcos_0047_short_admission_ids);

        UPDATE channel_daily_runs
        SET editorial_calendar_slot_id = NULL
        WHERE editorial_calendar_slot_id IN (
            SELECT id FROM vcos_0047_short_slot_ids
        )
        """
    )
    _fail_closed_if_fk_references_ids(
        "editorial_calendar_slots",
        "vcos_0047_short_slot_ids",
        "0047 refused to purge Short slots with surviving evidence",
    )
    op.execute(
        """
        DELETE FROM editorial_calendar_slots
        WHERE id IN (SELECT id FROM vcos_0047_short_slot_ids)
        """
    )
    _fail_closed_if_fk_references_ids(
        "video_projects",
        "vcos_0047_short_project_ids",
        "0047 refused to purge Short projects with surviving evidence",
    )
    op.execute(
        """
        DELETE FROM video_projects
        WHERE id IN (SELECT id FROM vcos_0047_short_project_ids)
        """
    )
    op.execute(
        """
        UPDATE series_plans
        SET allowed_production_lanes = '["LONG_FORM"]'::jsonb,
            state = CASE
                WHEN allowed_production_lanes ? 'LONG_FORM' THEN state
                ELSE 'ARCHIVED'
            END,
            updated_at = statement_timestamp()
        WHERE allowed_production_lanes ? 'DAILY_SHORT'
        """
    )
    op.execute(
        """
        UPDATE channel_lifecycle_decisions
        SET action = 'CONTINUE_OBSERVING'
        WHERE action = 'PAUSE_DAILY_GENERATION'
        """
    )


def _drop_short_only_tables() -> None:
    # upload_cards is referenced by the retained manual upload task.
    op.execute("ALTER TABLE human_upload_tasks DROP COLUMN IF EXISTS upload_card_id")
    # This unversioned development drift was an empty Creatomate compatibility
    # surface and has no current long-form model consumer.
    op.execute(
        "ALTER TABLE long_form_render_packages "
        "DROP COLUMN IF EXISTS creatomate_asset_refs"
    )
    op.execute(
        "ALTER TABLE human_upload_tasks "
        "RENAME COLUMN upload_card_ref TO publish_metadata_ref"
    )
    for table_name in _REMOVED_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table_name}"')


def _refactor_editorial_research() -> None:
    # The old candidate update trigger protected a terminal decision.  The new
    # editorial candidate is an auditable state machine and must be mutable.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_prevent_daily_idea_decision_update "
        "ON daily_idea_decisions"
    )

    _drop_all_checks("channel_daily_runs")
    op.execute(
        """
        UPDATE channel_daily_runs
        SET status = 'ARCHIVED',
            trigger_type = 'MIGRATED',
            reason_codes = reason_codes ||
                '["LEGACY_DAILY_RUN_ARCHIVED_BY_0047"]'::jsonb
        """
    )
    for column_name in (
        "daily_idea_decision_id",
        "project_admission_decision_id",
        "run_mode",
    ):
        op.execute(
            f'ALTER TABLE channel_daily_runs DROP COLUMN IF EXISTS "{column_name}"'
        )
    op.add_column(
        "channel_daily_runs",
        sa.Column(
            "candidate_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.rename_table("channel_daily_runs", "editorial_research_runs")
    _drop_all_foreign_keys("editorial_research_runs")
    _rename_primary_key(
        "editorial_research_runs",
        "pk_channel_daily_runs",
        "pk_editorial_research_runs",
    )
    _drop_indexes_with_prefix("ix_channel_daily_runs_")
    _create_editorial_research_core_foreign_keys()
    _create_editorial_research_indexes()
    op.create_check_constraint(
        "ck_editorial_research_runs_status",
        "editorial_research_runs",
        "status in ('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED',"
        "'CANCELLED','ARCHIVED')",
    )
    op.create_check_constraint(
        "ck_editorial_research_runs_trigger",
        "editorial_research_runs",
        "trigger_type in ('MANUAL','SCHEDULED','TEST','MIGRATED')",
    )
    op.create_check_constraint(
        "ck_editorial_research_runs_candidate_count",
        "editorial_research_runs",
        "candidate_count >= 0",
    )

    _drop_all_checks("daily_idea_decisions")
    op.add_column(
        "daily_idea_decisions",
        sa.Column("stage", sa.String(length=40), nullable=True),
    )
    op.execute(
        """
        UPDATE daily_idea_decisions
        SET stage = 'EXPIRED',
            reason_codes = reason_codes ||
                '["LEGACY_DAILY_CANDIDATE_EXPIRED_BY_0047"]'::jsonb
        """
    )
    op.alter_column(
        "daily_idea_decisions",
        "stage",
        existing_type=sa.String(length=40),
        nullable=False,
    )
    op.alter_column(
        "daily_idea_decisions",
        "context_pack_snapshot_id",
        existing_type=UUID,
        nullable=True,
    )
    for column_name in (
        "schema_version",
        "production_lane",
        "proposed_content_mode",
        "assignment_input_ref",
        "decision_status",
        "proposed_series_key",
    ):
        op.execute(
            f'ALTER TABLE daily_idea_decisions DROP COLUMN IF EXISTS "{column_name}"'
        )
    _drop_all_foreign_keys("daily_idea_decisions")
    op.alter_column(
        "daily_idea_decisions",
        "channel_daily_run_id",
        new_column_name="editorial_research_run_id",
        existing_type=UUID,
        existing_nullable=False,
    )
    op.rename_table("daily_idea_decisions", "editorial_idea_candidates")
    _rename_primary_key(
        "editorial_idea_candidates",
        "pk_daily_idea_decisions",
        "pk_editorial_idea_candidates",
    )
    _drop_indexes_with_prefix("ix_daily_idea_decisions_")
    _create_editorial_candidate_core_foreign_keys()
    for index_name, columns in (
        (
            "ix_editorial_idea_candidates_research_run_id",
            ["editorial_research_run_id"],
        ),
        ("ix_editorial_idea_candidates_company_id", ["company_id"]),
        (
            "ix_editorial_idea_candidates_channel_workspace_id",
            ["channel_workspace_id"],
        ),
        (
            "ix_editorial_idea_candidates_policy_snapshot_id",
            ["policy_snapshot_id"],
        ),
        (
            "ix_editorial_idea_candidates_context_pack_id",
            ["context_pack_snapshot_id"],
        ),
        ("ix_editorial_idea_candidates_stage", ["stage"]),
        ("ix_editorial_idea_candidates_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "editorial_idea_candidates", columns)
    op.create_check_constraint(
        "ck_editorial_idea_candidates_stage",
        "editorial_idea_candidates",
        "stage in ('RESEARCHED','PREFLIGHT_PASS','PREFLIGHT_BLOCK','GREENLIT',"
        "'SELECTED_FOR_SLOT','IN_PRODUCTION','FINAL_REVIEW_READY','PUBLISHED',"
        "'REJECTED','EXPIRED')",
    )

    for table_name in (
        "channel_state_pack_snapshots",
        "search_intent_maps",
        "audience_target_packs",
        "idea_market_preflights",
        "project_admission_decisions",
    ):
        _rename_editorial_reference_columns(table_name)

    # The 0046 check admits DAILY but not RESEARCH. Replace it before touching
    # populated rows so the semantic rename cannot fail mid-migration.
    _replace_check(
        "editorial_calendar_slots",
        "ck_editorial_calendar_slots_slot_type",
        "slot_type in "
        "('PUBLISH','RESEARCH','CAMPAIGN','EVERGREEN','EXPERIMENT','MANUAL')",
    )
    op.execute(
        """
        UPDATE editorial_calendar_slots
        SET slot_type = 'RESEARCH'
        WHERE slot_type = 'DAILY'
        """
    )


def _rename_editorial_reference_columns(table_name: str) -> None:
    old_columns: list[str] = []
    if table_name in (
        "channel_state_pack_snapshots",
        "search_intent_maps",
        "audience_target_packs",
        "idea_market_preflights",
        "project_admission_decisions",
    ):
        old_columns.append("channel_daily_run_id")
    if table_name in (
        "search_intent_maps",
        "audience_target_packs",
        "idea_market_preflights",
        "project_admission_decisions",
    ):
        old_columns.append("daily_idea_decision_id")
    _drop_foreign_keys_for_columns(table_name, old_columns)
    _drop_daily_reference_indexes(table_name)
    if "channel_daily_run_id" in old_columns:
        op.alter_column(
            table_name,
            "channel_daily_run_id",
            new_column_name="editorial_research_run_id",
            existing_type=UUID,
        )
    if "daily_idea_decision_id" in old_columns:
        op.alter_column(
            table_name,
            "daily_idea_decision_id",
            new_column_name="editorial_idea_candidate_id",
            existing_type=UUID,
        )
    op.create_foreign_key(
        op.f(f"fk_{table_name}_editorial_research_run_id_editorial_research_runs"),
        table_name,
        "editorial_research_runs",
        ["editorial_research_run_id"],
        ["id"],
    )
    if "daily_idea_decision_id" in old_columns:
        op.create_foreign_key(
            op.f(
                f"fk_{table_name}_editorial_idea_candidate_id_editorial_idea_candidates"
            ),
            table_name,
            "editorial_idea_candidates",
            ["editorial_idea_candidate_id"],
            ["id"],
        )
    index_prefixes = {
        "channel_state_pack_snapshots": (
            "ix_channel_state_pack_snapshots_editorial_research_run_id",
            None,
        ),
        "search_intent_maps": (
            "ix_search_intent_maps_editorial_research_run_id",
            "ix_search_intent_maps_editorial_idea_candidate_id",
        ),
        "audience_target_packs": (
            "ix_audience_target_packs_editorial_research_run_id",
            "ix_audience_target_packs_editorial_idea_candidate_id",
        ),
        "idea_market_preflights": (
            "ix_idea_market_preflights_editorial_research_run_id",
            "ix_idea_market_preflights_editorial_idea_candidate_id",
        ),
        "project_admission_decisions": (
            "ix_project_admission_decisions_editorial_research_run_id",
            "ix_project_admission_decisions_editorial_idea_candidate_id",
        ),
    }
    run_index, candidate_index = index_prefixes[table_name]
    op.create_index(run_index, table_name, ["editorial_research_run_id"])
    if candidate_index is not None:
        op.create_index(
            candidate_index,
            table_name,
            ["editorial_idea_candidate_id"],
        )


def _migrate_mutable_json_authority() -> None:
    """Rename Daily keys in mutable current-state JSON documents.

    ArtifactVersion JSON is intentionally excluded because its bytes and hash
    are immutable.  Affected immutable versions were inventoried and any
    affected current Artifact pointer was archived above.
    """

    op.execute(
        """
        UPDATE video_projects
        SET audience_delivery_summary = jsonb_set(
            audience_delivery_summary,
            '{niche_governance}',
            (
                (audience_delivery_summary -> 'niche_governance')
                - 'daily_idea_decision_ref'
            ) || CASE
                WHEN (audience_delivery_summary -> 'niche_governance')
                    ? 'editorial_idea_candidate_ref'
                THEN '{}'::jsonb
                ELSE jsonb_build_object(
                    'editorial_idea_candidate_ref',
                    audience_delivery_summary
                        -> 'niche_governance'
                        -> 'daily_idea_decision_ref'
                )
            END,
            false
        )
        WHERE jsonb_typeof(
                audience_delivery_summary -> 'niche_governance'
              ) = 'object'
          AND (audience_delivery_summary -> 'niche_governance')
                ? 'daily_idea_decision_ref'
        """
    )
    # Normalize the nested candidate object before renaming its top-level key.
    for candidate_key in (
        "approved_daily_idea",
        "approved_editorial_candidate",
    ):
        op.execute(
            f"""
            UPDATE first_scripted_video_packages
            SET artifacts = jsonb_set(
                artifacts,
                '{{{candidate_key}}}',
                (
                    (artifacts -> '{candidate_key}')
                    - 'daily_idea_decision_ref'
                ) || CASE
                    WHEN (artifacts -> '{candidate_key}')
                        ? 'editorial_idea_candidate_ref'
                    THEN '{{}}'::jsonb
                    ELSE jsonb_build_object(
                        'editorial_idea_candidate_ref',
                        artifacts
                            -> '{candidate_key}'
                            -> 'daily_idea_decision_ref'
                    )
                END,
                false
            )
            WHERE jsonb_typeof(artifacts -> '{candidate_key}') = 'object'
              AND (artifacts -> '{candidate_key}')
                    ? 'daily_idea_decision_ref'
            """
        )
    op.execute(
        """
        UPDATE first_scripted_video_packages
        SET artifacts = (
            artifacts - 'approved_daily_idea'
        ) || CASE
            WHEN artifacts ? 'approved_editorial_candidate'
            THEN '{}'::jsonb
            ELSE jsonb_build_object(
                'approved_editorial_candidate',
                artifacts -> 'approved_daily_idea'
            )
        END
        WHERE artifacts ? 'approved_daily_idea'
        """
    )
    op.execute(
        """
        UPDATE first_scripted_video_packages
        SET artifacts = (
            artifacts - 'daily_idea_decision_ref'
        ) || CASE
            WHEN artifacts ? 'editorial_idea_candidate_ref'
            THEN '{}'::jsonb
            ELSE jsonb_build_object(
                'editorial_idea_candidate_ref',
                artifacts -> 'daily_idea_decision_ref'
            )
        END
        WHERE artifacts ? 'daily_idea_decision_ref'
        """
    )


def _reconcile_model_metadata() -> None:
    """Close pre-existing 0046 metadata drift while the freeze is open."""

    op.execute(
        """
        ALTER TABLE format_identity_contracts
        ALTER COLUMN approved_at
        TYPE TIMESTAMP WITHOUT TIME ZONE
        USING approved_at AT TIME ZONE 'UTC'
        """
    )
    for table_name, index_name in (
        (
            "manual_publish_confirmations",
            "ix_manual_publish_destination_binding_id",
        ),
        (
            "publish_handoff_packages",
            "ix_publish_handoff_destination_binding_id",
        ),
        (
            "uploaded_videos",
            "ix_uploaded_videos_destination_binding_id",
        ),
    ):
        op.drop_index(index_name, table_name=table_name, if_exists=True)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'media_provider_role_profiles'::regclass
                  AND conname = 'uq_media_provider_role_profiles_key'
            ) AND NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'media_provider_role_profiles'::regclass
                  AND conname =
                    'uq_media_provider_role_profiles_provider_key'
            ) THEN
                ALTER TABLE media_provider_role_profiles
                RENAME CONSTRAINT uq_media_provider_role_profiles_key
                TO uq_media_provider_role_profiles_provider_key;
            END IF;
        END
        $$;
        """
    )


def _narrow_long_form_authorities() -> None:
    # Remove derivative-only lineage fields.  PostgreSQL drops their dependent
    # FKs/indexes/checks with each column.
    for table_name, columns in (
        (
            "video_projects",
            (
                "parent_video_project_id",
                "parent_final_media_ref_id",
                "canonical_timeline_ref",
                "canonical_timeline_hash",
            ),
        ),
        (
            "project_admission_decisions",
            (
                "parent_video_project_id",
                "parent_final_media_ref_id",
                "canonical_timeline_ref",
                "canonical_timeline_hash",
            ),
        ),
        (
            "final_review_candidates",
            ("parent_video_project_id", "parent_final_media_ref_id"),
        ),
        (
            "human_upload_tasks",
            ("parent_video_project_id", "parent_final_media_ref_id"),
        ),
        (
            "uploaded_videos",
            ("parent_video_project_id", "parent_final_media_ref_id"),
        ),
        ("platform_native_package_plans", ("derivative_manifest_ref",)),
    ):
        for column_name in columns:
            op.execute(
                f'ALTER TABLE "{table_name}" DROP COLUMN IF EXISTS "{column_name}"'
            )

    _replace_check(
        "series_plans",
        "ck_series_plans_allowed_lanes",
        "jsonb_typeof(allowed_production_lanes) = 'array' "
        "and allowed_production_lanes = '[\"LONG_FORM\"]'::jsonb",
    )
    _replace_check(
        "editorial_calendar_slots",
        "ck_editorial_calendar_slots_v2_authority",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and production_lane = 'LONG_FORM' "
        "and assignment_mode in "
        "('SERIES_REQUIRED','SERIES_PREFERRED',"
        "'STANDALONE_REQUIRED','OPEN_MIX') "
        "and series_key is null "
        "and (preferred_series_run_id is null "
        "or preferred_series_plan_id is not null))",
    )
    _replace_check(
        "production_workflow_runs",
        "production_workflow_runs_lane",
        "production_lane = 'LONG_FORM'",
    )
    _replace_check(
        "production_workflow_runs",
        "production_workflow_runs_planning_source",
        "planning_source_type = 'LONG_FORM_PLAN'",
    )
    _replace_check(
        "final_review_candidates",
        "ck_final_review_candidates_production_lane",
        "production_lane = 'LONG_FORM'",
    )
    _replace_check(
        "human_upload_tasks",
        "ck_human_upload_tasks_target_platform",
        "target_platform in ('YOUTUBE_LONG','YOUTUBE')",
    )
    _replace_check(
        "human_upload_tasks",
        "ck_human_upload_tasks_v2_binding",
        "(schema_version = 'v1') or "
        "(first_scripted_video_package_id is null "
        "and publish_package_id is null "
        "and final_review_candidate_id is not null "
        "and final_video_decision_id is not null "
        "and final_media_ref_id is not null "
        "and final_media_file_ref is not null "
        "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
        "and production_package_artifact_version_id is not null "
        "and production_package_hash ~ '^[0-9a-f]{64}$' "
        "and destination_binding_id is not null "
        "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
        "and channel_profile_version_id is not null "
        "and policy_snapshot_id is not null "
        "and production_lane = 'LONG_FORM' "
        "and content_mode in ('SERIES_EPISODE','STANDALONE') "
        "and archive_object_ref is not null "
        "and task_state in "
        "('READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
        "'VERIFIED','CANCELED'))",
    )
    _replace_check(
        "uploaded_videos",
        "ck_uploaded_videos_v2_binding",
        "(schema_version = 'v1') or "
        "(video_project_id is not null "
        "and policy_snapshot_id is not null "
        "and manual_publish_confirmation_id is not null "
        "and human_upload_task_id is not null "
        "and final_review_candidate_id is not null "
        "and final_video_decision_id is not null "
        "and final_media_ref_id is not null "
        "and production_package_artifact_version_id is not null "
        "and production_package_hash ~ '^[0-9a-f]{64}$' "
        "and channel_profile_version_id is not null "
        "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
        "and destination_binding_id is not null "
        "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
        "and production_lane = 'LONG_FORM' "
        "and content_mode in ('SERIES_EPISODE','STANDALONE') "
        "and target_market_lineage is not null "
        "and archive_supplement is not null "
        "and archive_supplement_ref is not null "
        "and archive_supplement_hash ~ '^[0-9a-f]{64}$' "
        "and verification_status = 'VERIFIED' "
        "and analytics_sync_status = 'READY' "
        "and verified_event_id is not null "
        "and analytics_ready_event_id is not null "
        "and analytics_ready_at is not null)",
    )
    _replace_video_project_checks()
    _replace_project_admission_checks()
    _replace_immutable_snapshot_purpose_guards()
    _replace_check(
        "editorial_calendar_slots",
        "ck_editorial_calendar_slots_slot_type",
        "slot_type in "
        "('PUBLISH','RESEARCH','CAMPAIGN','EVERGREEN','EXPERIMENT','MANUAL')",
    )
    _replace_check(
        "channel_lifecycle_decisions",
        "ck_channel_lifecycle_action",
        "action in ('KEEP_ACTIVE','CONTINUE_OBSERVING','ADD_MANUAL_NOTE',"
        "'DEACTIVATE_CHANNEL','ARCHIVE_CHANNEL','REACTIVATE_CHANNEL')",
    )
    op.drop_index(
        "uq_project_admission_v2_daily_source",
        table_name="project_admission_decisions",
        if_exists=True,
    )
    op.create_index(
        "uq_project_admission_v2_editorial_candidate",
        "project_admission_decisions",
        ["editorial_idea_candidate_id"],
        unique=True,
        postgresql_where=sa.text(
            "schema_version = 'v2' and editorial_idea_candidate_id is not null"
        ),
    )


def _replace_video_project_checks() -> None:
    _replace_check(
        "video_projects",
        "ck_video_projects_v2_assignment",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and channel_profile_version_id is not null "
        "and project_admission_decision_id is not null "
        "and planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM' "
        "and content_mode in ('SERIES_EPISODE','STANDALONE') "
        "and assignment_mode in "
        "('SERIES_REQUIRED','SERIES_PREFERRED',"
        "'STANDALONE_REQUIRED','OPEN_MIX') "
        "and duration_contract is not null "
        "and ((content_mode = 'SERIES_EPISODE' "
        "and series_plan_id is not null "
        "and series_run_id is not null "
        "and episode_number > 0 "
        "and standalone_reason_code is null) "
        "or (content_mode = 'STANDALONE' "
        "and series_plan_id is null "
        "and series_run_id is null "
        "and episode_number is null "
        "and episode_role is null "
        "and standalone_reason_code is not null)))",
    )
    _replace_check(
        "video_projects",
        "ck_video_projects_v2_lane_source",
        "(schema_version = 'v1') or "
        "(planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM')",
    )


def _replace_project_admission_checks() -> None:
    _replace_check(
        "project_admission_decisions",
        "ck_project_admission_decisions_v2_authority",
        "(schema_version = 'v1') or "
        "(schema_version = 'v2' "
        "and company_id is not null "
        "and channel_workspace_id is not null "
        "and channel_profile_version_id is not null "
        "and policy_snapshot_id is not null "
        "and planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM' "
        "and assignment_mode in "
        "('SERIES_REQUIRED','SERIES_PREFERRED',"
        "'STANDALONE_REQUIRED','OPEN_MIX') "
        "and resolver_version is not null "
        "and resolver_input_hash ~ '^[0-9a-f]{64}$' "
        "and decision_hash ~ '^[0-9a-f]{64}$' "
        "and assignment_input_ref is not null "
        "and ((decision = 'BLOCK') or "
        "(decision = 'ADMIT' "
        "and admitted_video_project_id is not null "
        "and duration_contract is not null "
        "and ((content_mode = 'SERIES_EPISODE' "
        "and series_plan_id is not null "
        "and series_run_id is not null "
        "and episode_number > 0 "
        "and standalone_reason_code is null) "
        "or (content_mode = 'STANDALONE' "
        "and series_plan_id is null "
        "and series_run_id is null "
        "and episode_number is null "
        "and episode_role is null "
        "and standalone_reason_code is not null)))))",
    )
    _replace_check(
        "project_admission_decisions",
        "ck_project_admission_decisions_v2_lane_source",
        "(schema_version = 'v1') or (decision = 'BLOCK') or "
        "(planning_source_type = 'LONG_FORM_PLAN' "
        "and production_lane = 'LONG_FORM' "
        "and editorial_calendar_slot_id is not null)",
    )


def _remove_remaining_short_checks() -> None:
    # Rebuild each retained enum-like guard explicitly.  Never leave a column
    # unconstrained merely because one historical value was removed.
    platform_condition = (
        "platform in ('YOUTUBE','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')"
    )
    for table_name, constraint_name in (
        ("uploaded_videos", "ck_uploaded_videos_platform"),
        (
            "uploaded_video_publication_summaries",
            "ck_uploaded_video_publication_summaries_platform",
        ),
        ("analytics_sync_runs", "ck_analytics_sync_runs_platform"),
        (
            "metric_definition_versions",
            "ck_metric_definition_versions_platform",
        ),
        (
            "metric_availability_snapshots",
            "ck_metric_availability_snapshots_platform",
        ),
        ("analytics_snapshots", "ck_analytics_snapshots_platform"),
        (
            "traffic_source_snapshots",
            "ck_traffic_source_snapshots_platform",
        ),
        (
            "retention_curve_snapshots",
            "ck_retention_curve_snapshots_platform",
        ),
        ("engagement_snapshots", "ck_engagement_snapshots_platform"),
        (
            "uploaded_video_metrics_summaries",
            "ck_uploaded_video_metrics_summaries_platform",
        ),
        (
            "post_publish_observation_windows",
            "ck_post_publish_windows_platform",
        ),
        (
            "post_publish_health_runs",
            "ck_post_publish_health_runs_platform",
        ),
        ("failure_trace_reports", "ck_failure_trace_reports_platform"),
    ):
        _replace_check(
            table_name,
            constraint_name,
            platform_condition,
        )

    for table_name, constraint_name in (
        (
            "publish_handoff_packages",
            "ck_publish_handoff_packages_target_platform",
        ),
        (
            "manual_publish_confirmations",
            "ck_manual_publish_confirmations_target_platform",
        ),
    ):
        _replace_check(
            table_name,
            constraint_name,
            "target_platform in ('YOUTUBE','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')",
        )
    for table_name, constraint_name in (
        (
            "publish_handoff_packages",
            "ck_publish_handoff_packages_target_surface",
        ),
        (
            "manual_publish_confirmations",
            "ck_manual_publish_confirmations_target_surface",
        ),
    ):
        _replace_check(
            table_name,
            constraint_name,
            "target_surface in ('LONG_FORM','FEED','GENERIC')",
        )

    _replace_check(
        "ai_hero_assets",
        "ck_ai_hero_assets_usage",
        "intended_usage in ('OPENING_HOOK','KEY_METAPHOR','THUMBNAIL_STILL','OTHER')",
    )
    _replace_check(
        "final_media_refs",
        "ck_final_media_refs_type",
        "media_type in ('LONG_FORM_FINAL','THUMBNAIL','CARD','AI_HERO','PREVIEW')",
    )
    media_type_condition = (
        "media_type in "
        "('LONG_FORM_FINAL','THUMBNAIL','CAPTION','AI_HERO',"
        "'CHARACTER_REFERENCE','CHARACTER_FACE_REF','CHARACTER_BRANCH',"
        "'VOICE_REFERENCE','REFERENCE_PACK','PUBLISH_PACKAGE','QC_EXPORT',"
        "'OTHER')"
    )
    _replace_check(
        "cloud_media_refs",
        "ck_cloud_media_refs_media_type",
        media_type_condition,
    )
    _replace_check(
        "media_offload_jobs",
        "ck_media_offload_jobs_media_type",
        media_type_condition.replace("media_type", "target_media_type"),
    )

    _raise_if(
        "EXISTS ("
        "SELECT 1 FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace "
        "AND contype = 'c' "
        "AND ("
        "lower(pg_get_constraintdef(oid)) LIKE '%youtube_shorts%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%daily_short%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%daily_idea%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%derived_short%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%long_derived_short%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%short_form%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%short_final%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%short_hook%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%new_short%' "
        "OR lower(pg_get_constraintdef(oid)) "
        "LIKE '%pause_daily_generation%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%9:16%' "
        "OR lower(pg_get_constraintdef(oid)) LIKE '%reels%'"
        "))",
        "0047 retained check still advertises removed Short vocabulary",
    )


def _restore_mutable_json_authority() -> None:
    op.execute(
        """
        UPDATE video_projects
        SET audience_delivery_summary = jsonb_set(
            audience_delivery_summary,
            '{niche_governance}',
            (
                (audience_delivery_summary -> 'niche_governance')
                - 'editorial_idea_candidate_ref'
            ) || CASE
                WHEN (audience_delivery_summary -> 'niche_governance')
                    ? 'daily_idea_decision_ref'
                THEN '{}'::jsonb
                ELSE jsonb_build_object(
                    'daily_idea_decision_ref',
                    audience_delivery_summary
                        -> 'niche_governance'
                        -> 'editorial_idea_candidate_ref'
                )
            END,
            false
        )
        WHERE jsonb_typeof(
                audience_delivery_summary -> 'niche_governance'
              ) = 'object'
          AND (audience_delivery_summary -> 'niche_governance')
                ? 'editorial_idea_candidate_ref'
        """
    )
    for candidate_key in (
        "approved_editorial_candidate",
        "approved_daily_idea",
    ):
        op.execute(
            f"""
            UPDATE first_scripted_video_packages
            SET artifacts = jsonb_set(
                artifacts,
                '{{{candidate_key}}}',
                (
                    (artifacts -> '{candidate_key}')
                    - 'editorial_idea_candidate_ref'
                ) || CASE
                    WHEN (artifacts -> '{candidate_key}')
                        ? 'daily_idea_decision_ref'
                    THEN '{{}}'::jsonb
                    ELSE jsonb_build_object(
                        'daily_idea_decision_ref',
                        artifacts
                            -> '{candidate_key}'
                            -> 'editorial_idea_candidate_ref'
                    )
                END,
                false
            )
            WHERE jsonb_typeof(artifacts -> '{candidate_key}') = 'object'
              AND (artifacts -> '{candidate_key}')
                    ? 'editorial_idea_candidate_ref'
            """
        )
    op.execute(
        """
        UPDATE first_scripted_video_packages
        SET artifacts = (
            artifacts - 'approved_editorial_candidate'
        ) || CASE
            WHEN artifacts ? 'approved_daily_idea'
            THEN '{}'::jsonb
            ELSE jsonb_build_object(
                'approved_daily_idea',
                artifacts -> 'approved_editorial_candidate'
            )
        END
        WHERE artifacts ? 'approved_editorial_candidate'
        """
    )
    op.execute(
        """
        UPDATE first_scripted_video_packages
        SET artifacts = (
            artifacts - 'editorial_idea_candidate_ref'
        ) || CASE
            WHEN artifacts ? 'daily_idea_decision_ref'
            THEN '{}'::jsonb
            ELSE jsonb_build_object(
                'daily_idea_decision_ref',
                artifacts -> 'editorial_idea_candidate_ref'
            )
        END
        WHERE artifacts ? 'editorial_idea_candidate_ref'
        """
    )


def _restore_preexisting_model_metadata() -> None:
    op.execute(
        """
        ALTER TABLE format_identity_contracts
        ALTER COLUMN approved_at
        TYPE TIMESTAMP WITH TIME ZONE
        USING approved_at AT TIME ZONE 'UTC'
        """
    )
    for table_name, index_name in (
        (
            "manual_publish_confirmations",
            "ix_manual_publish_destination_binding_id",
        ),
        (
            "publish_handoff_packages",
            "ix_publish_handoff_destination_binding_id",
        ),
        (
            "uploaded_videos",
            "ix_uploaded_videos_destination_binding_id",
        ),
    ):
        op.create_index(
            index_name,
            table_name,
            ["destination_binding_id"],
            if_not_exists=True,
        )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'media_provider_role_profiles'::regclass
                  AND conname =
                    'uq_media_provider_role_profiles_provider_key'
            ) AND NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'media_provider_role_profiles'::regclass
                  AND conname = 'uq_media_provider_role_profiles_key'
            ) THEN
                ALTER TABLE media_provider_role_profiles
                RENAME CONSTRAINT
                    uq_media_provider_role_profiles_provider_key
                TO uq_media_provider_role_profiles_key;
            END IF;
        END
        $$;
        """
    )


def _fail_closed_if_post_removal_authority_exists() -> None:
    message = (
        "0047 downgrade refused: post-removal editorial/long-form-only authority exists"
    )
    predicate = (
        "EXISTS (SELECT 1 FROM editorial_research_runs "
        "WHERE trigger_type <> 'MIGRATED') "
        "OR EXISTS ("
        "SELECT 1 FROM editorial_idea_candidates c "
        "JOIN editorial_research_runs r "
        "ON r.id = c.editorial_research_run_id "
        "WHERE r.trigger_type <> 'MIGRATED'"
        ")"
    )
    _raise_if(predicate, message)


def _restore_historical_long_form_checks() -> None:
    # Restore only checks that 0047 replaced or removed.  Unaffected 0046
    # guards must remain intact; dropping all checks would leave a database
    # stamped 0046 materially less constrained than the historical revision.
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_guard_retrieval_plan_snapshot_purpose
        ON retrieval_plan_snapshots;
        DROP TRIGGER IF EXISTS trg_guard_context_pack_snapshot_purpose
        ON context_pack_snapshots;
        DROP FUNCTION IF EXISTS guard_editorial_snapshot_purpose();
        """
    )

    legacy_checks = (
        (
            "series_plans",
            "ck_series_plans_allowed_lanes",
            "jsonb_typeof(allowed_production_lanes) = 'array' "
            "and jsonb_array_length(allowed_production_lanes) > 0 "
            "and allowed_production_lanes "
            '<@ \'["DAILY_SHORT","LONG_FORM"]\'::jsonb',
        ),
        (
            "editorial_calendar_slots",
            "ck_editorial_calendar_slots_v2_authority",
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and series_key is null "
            "and (preferred_series_run_id is null "
            "or preferred_series_plan_id is not null))",
        ),
        (
            "editorial_calendar_slots",
            "ck_editorial_calendar_slots_slot_type",
            "slot_type in "
            "('DAILY','WEEKLY','CAMPAIGN','EVERGREEN','EXPERIMENT','MANUAL')",
        ),
        (
            "production_workflow_runs",
            "production_workflow_runs_lane",
            "production_lane in ('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT')",
        ),
        (
            "production_workflow_runs",
            "production_workflow_runs_planning_source",
            "planning_source_type in ('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT')",
        ),
        (
            "final_review_candidates",
            "ck_final_review_candidates_production_lane",
            "production_lane in ('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT')",
        ),
        (
            "final_review_candidates",
            "ck_final_review_candidates_parent_lineage",
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
        ),
        (
            "human_upload_tasks",
            "ck_human_upload_tasks_target_platform",
            "target_platform in "
            "('YOUTUBE_LONG','YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS',"
            "'YOUTUBE')",
        ),
        (
            "human_upload_tasks",
            "ck_human_upload_tasks_v2_binding",
            "(schema_version = 'v1') or "
            "(upload_card_id is null "
            "and first_scripted_video_package_id is null "
            "and publish_package_id is null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and final_media_file_ref is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and policy_snapshot_id is not null "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and archive_object_ref is not null "
            "and task_state in "
            "('READY_FOR_OPERATOR','IN_PROGRESS','AWAITING_CONFIRMATION',"
            "'VERIFIED','CANCELED'))",
        ),
        (
            "human_upload_tasks",
            "ck_human_upload_tasks_v2_parent_lineage",
            "(schema_version = 'v1') or "
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
        ),
        (
            "uploaded_videos",
            "ck_uploaded_videos_platform",
            "platform in "
            "('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM',"
            "'GENERIC')",
        ),
        (
            "uploaded_videos",
            "ck_uploaded_videos_v2_binding",
            "(schema_version = 'v1') or "
            "(video_project_id is not null "
            "and policy_snapshot_id is not null "
            "and manual_publish_confirmation_id is not null "
            "and human_upload_task_id is not null "
            "and final_review_candidate_id is not null "
            "and final_video_decision_id is not null "
            "and final_media_ref_id is not null "
            "and production_package_artifact_version_id is not null "
            "and production_package_hash ~ '^[0-9a-f]{64}$' "
            "and channel_profile_version_id is not null "
            "and reviewed_checksum ~ '^[0-9a-f]{64}$' "
            "and destination_binding_id is not null "
            "and destination_binding_fingerprint ~ '^[0-9a-f]{64}$' "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and target_market_lineage is not null "
            "and archive_supplement is not null "
            "and archive_supplement_ref is not null "
            "and archive_supplement_hash ~ '^[0-9a-f]{64}$' "
            "and verification_status = 'VERIFIED' "
            "and analytics_sync_status = 'READY' "
            "and verified_event_id is not null "
            "and analytics_ready_event_id is not null "
            "and analytics_ready_at is not null)",
        ),
        (
            "uploaded_videos",
            "ck_uploaded_videos_v2_parent_lineage",
            "(schema_version = 'v1') or "
            "(production_lane <> 'LONG_DERIVED_SHORT') or "
            "(parent_video_project_id is not null "
            "and parent_final_media_ref_id is not null)",
        ),
        (
            "video_projects",
            "ck_video_projects_v2_assignment",
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and channel_profile_version_id is not null "
            "and project_admission_decision_id is not null "
            "and planning_source_type in "
            "('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT') "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and content_mode in ('SERIES_EPISODE','STANDALONE') "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and duration_contract is not null "
            "and ((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and episode_role is null "
            "and standalone_reason_code is not null)))",
        ),
        (
            "video_projects",
            "ck_video_projects_v2_lane_source",
            "(schema_version = 'v1') or "
            "((planning_source_type = 'DAILY_IDEA' "
            "and production_lane = 'DAILY_SHORT') "
            "or (planning_source_type = 'LONG_FORM_PLAN' "
            "and production_lane = 'LONG_FORM') "
            "or (planning_source_type = 'DERIVED_SHORT' "
            "and production_lane = 'LONG_DERIVED_SHORT' "
            "and content_mode = 'STANDALONE' "
            "and assignment_mode = 'STANDALONE_REQUIRED' "
            "and parent_video_project_id is not null "
            "and canonical_timeline_ref is not null "
            "and canonical_timeline_hash ~ '^[0-9a-f]{64}$' "
            "and render_eligible = false))",
        ),
        (
            "project_admission_decisions",
            "ck_project_admission_decisions_v2_authority",
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and company_id is not null "
            "and channel_workspace_id is not null "
            "and channel_profile_version_id is not null "
            "and policy_snapshot_id is not null "
            "and planning_source_type in "
            "('DAILY_IDEA','LONG_FORM_PLAN','DERIVED_SHORT') "
            "and production_lane in "
            "('DAILY_SHORT','LONG_FORM','LONG_DERIVED_SHORT') "
            "and assignment_mode in "
            "('SERIES_REQUIRED','SERIES_PREFERRED',"
            "'STANDALONE_REQUIRED','OPEN_MIX') "
            "and resolver_version is not null "
            "and resolver_input_hash ~ '^[0-9a-f]{64}$' "
            "and decision_hash ~ '^[0-9a-f]{64}$' "
            "and assignment_input_ref is not null "
            "and ((decision = 'BLOCK') or "
            "(decision = 'ADMIT' "
            "and admitted_video_project_id is not null "
            "and duration_contract is not null "
            "and ((content_mode = 'SERIES_EPISODE' "
            "and series_plan_id is not null "
            "and series_run_id is not null "
            "and episode_number > 0 "
            "and standalone_reason_code is null) "
            "or (content_mode = 'STANDALONE' "
            "and series_plan_id is null "
            "and series_run_id is null "
            "and episode_number is null "
            "and episode_role is null "
            "and standalone_reason_code is not null)))))",
        ),
        (
            "project_admission_decisions",
            "ck_project_admission_decisions_v2_lane_source",
            "(schema_version = 'v1') or (decision = 'BLOCK') or "
            "((planning_source_type = 'DAILY_IDEA' "
            "and production_lane = 'DAILY_SHORT' "
            "and channel_daily_run_id is not null "
            "and daily_idea_decision_id is not null) "
            "or (planning_source_type = 'LONG_FORM_PLAN' "
            "and production_lane = 'LONG_FORM' "
            "and editorial_calendar_slot_id is not null "
            "and channel_daily_run_id is null "
            "and daily_idea_decision_id is null) "
            "or (planning_source_type = 'DERIVED_SHORT' "
            "and production_lane = 'LONG_DERIVED_SHORT' "
            "and content_mode = 'STANDALONE' "
            "and assignment_mode = 'STANDALONE_REQUIRED' "
            "and parent_video_project_id is not null "
            "and canonical_timeline_ref is not null "
            "and canonical_timeline_hash ~ '^[0-9a-f]{64}$'))",
        ),
        (
            "retrieval_plan_snapshots",
            "ck_retrieval_plan_snapshots_purpose",
            "purpose in ('DAILY_IDEA','PROJECT_ADMISSION',"
            "'AUTHORITY_REVIEW','SEARCH_DEMAND','TEST')",
        ),
        (
            "context_pack_snapshots",
            "ck_context_pack_snapshots_purpose",
            "purpose in ('DAILY_IDEA','PROJECT_ADMISSION',"
            "'AUTHORITY_REVIEW','SEARCH_DEMAND','TEST')",
        ),
        (
            "channel_lifecycle_decisions",
            "ck_channel_lifecycle_action",
            "action in "
            "('KEEP_ACTIVE','PAUSE_DAILY_GENERATION','CONTINUE_OBSERVING',"
            "'ADD_MANUAL_NOTE','DEACTIVATE_CHANNEL','ARCHIVE_CHANNEL',"
            "'REACTIVATE_CHANNEL')",
        ),
    )
    for table_name, constraint_name, condition in legacy_checks:
        _restore_check(table_name, constraint_name, condition)

    platform_condition = (
        "platform in "
        "('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM','GENERIC')"
    )
    for table_name, constraint_name in (
        (
            "uploaded_video_publication_summaries",
            "ck_uploaded_video_publication_summaries_platform",
        ),
        ("analytics_sync_runs", "ck_analytics_sync_runs_platform"),
        (
            "metric_definition_versions",
            "ck_metric_definition_versions_platform",
        ),
        (
            "metric_availability_snapshots",
            "ck_metric_availability_snapshots_platform",
        ),
        ("analytics_snapshots", "ck_analytics_snapshots_platform"),
        (
            "traffic_source_snapshots",
            "ck_traffic_source_snapshots_platform",
        ),
        (
            "retention_curve_snapshots",
            "ck_retention_curve_snapshots_platform",
        ),
        ("engagement_snapshots", "ck_engagement_snapshots_platform"),
        (
            "uploaded_video_metrics_summaries",
            "ck_uploaded_video_metrics_summaries_platform",
        ),
        (
            "post_publish_observation_windows",
            "ck_post_publish_windows_platform",
        ),
        (
            "post_publish_health_runs",
            "ck_post_publish_health_runs_platform",
        ),
        ("failure_trace_reports", "ck_failure_trace_reports_platform"),
    ):
        _restore_check(table_name, constraint_name, platform_condition)

    for table_name, constraint_name in (
        (
            "publish_handoff_packages",
            "ck_publish_handoff_packages_target_platform",
        ),
        (
            "manual_publish_confirmations",
            "ck_manual_publish_confirmations_target_platform",
        ),
    ):
        _restore_check(
            table_name,
            constraint_name,
            "target_platform in "
            "('YOUTUBE','YOUTUBE_SHORTS','TIKTOK','FACEBOOK','INSTAGRAM',"
            "'GENERIC')",
        )
    for table_name, constraint_name in (
        (
            "publish_handoff_packages",
            "ck_publish_handoff_packages_target_surface",
        ),
        (
            "manual_publish_confirmations",
            "ck_manual_publish_confirmations_target_surface",
        ),
    ):
        _restore_check(
            table_name,
            constraint_name,
            "target_surface in "
            "('LONG_FORM','SHORT_FORM','REELS','FEED','STORY','GENERIC')",
        )

    for table_name, constraint_name, condition in (
        (
            "ai_hero_assets",
            "ck_ai_hero_assets_usage",
            "intended_usage in "
            "('OPENING_HOOK','KEY_METAPHOR','SHORT_HOOK',"
            "'THUMBNAIL_STILL','OTHER')",
        ),
        (
            "final_media_refs",
            "ck_final_media_refs_type",
            "media_type in "
            "('LONG_FORM_FINAL','SHORT_FINAL','THUMBNAIL','CARD','AI_HERO',"
            "'PREVIEW')",
        ),
        (
            "cloud_media_refs",
            "ck_cloud_media_refs_media_type",
            "media_type in "
            "('LONG_FORM_FINAL','SHORT_FINAL','THUMBNAIL','CAPTION','AI_HERO',"
            "'PUBLISH_PACKAGE','QC_EXPORT','OTHER')",
        ),
        (
            "media_offload_jobs",
            "ck_media_offload_jobs_media_type",
            "target_media_type in "
            "('LONG_FORM_FINAL','SHORT_FINAL','THUMBNAIL','CAPTION','AI_HERO',"
            "'PUBLISH_PACKAGE','QC_EXPORT','OTHER')",
        ),
    ):
        _restore_check(table_name, constraint_name, condition)


def _restore_daily_schema() -> None:
    # Remove renamed FKs before the owning tables/columns move back.
    for table_name in (
        "channel_state_pack_snapshots",
        "search_intent_maps",
        "audience_target_packs",
        "idea_market_preflights",
        "project_admission_decisions",
    ):
        columns = ["editorial_research_run_id"]
        if table_name != "channel_state_pack_snapshots":
            columns.append("editorial_idea_candidate_id")
        _drop_foreign_keys_for_columns(table_name, columns)
        _drop_editorial_reference_indexes(table_name)
        op.alter_column(
            table_name,
            "editorial_research_run_id",
            new_column_name="channel_daily_run_id",
            existing_type=UUID,
        )
        if table_name != "channel_state_pack_snapshots":
            op.alter_column(
                table_name,
                "editorial_idea_candidate_id",
                new_column_name="daily_idea_decision_id",
                existing_type=UUID,
            )

    _drop_all_foreign_keys("editorial_idea_candidates")
    _drop_all_checks("editorial_idea_candidates")
    _drop_indexes_with_prefix("ix_editorial_idea_candidates_")
    op.rename_table("editorial_idea_candidates", "daily_idea_decisions")
    _rename_primary_key(
        "daily_idea_decisions",
        "pk_editorial_idea_candidates",
        "pk_daily_idea_decisions",
    )
    op.alter_column(
        "daily_idea_decisions",
        "editorial_research_run_id",
        new_column_name="channel_daily_run_id",
        existing_type=UUID,
        existing_nullable=False,
    )
    for column in (
        sa.Column(
            "decision_status",
            sa.String(length=40),
            server_default=sa.text("'SKIPPED'"),
            nullable=False,
        ),
        sa.Column("proposed_series_key", sa.Text(), nullable=True),
        sa.Column(
            "schema_version",
            sa.String(length=16),
            server_default=sa.text("'v1'"),
            nullable=False,
        ),
        sa.Column("production_lane", sa.String(length=40), nullable=True),
        sa.Column("proposed_content_mode", sa.String(length=40), nullable=True),
        sa.Column("assignment_input_ref", JSONB, nullable=True),
    ):
        op.add_column("daily_idea_decisions", column)
    op.drop_column("daily_idea_decisions", "stage")
    op.alter_column(
        "daily_idea_decisions",
        "context_pack_snapshot_id",
        existing_type=UUID,
        nullable=False,
    )

    _drop_all_foreign_keys("editorial_research_runs")
    _drop_all_checks("editorial_research_runs")
    _drop_indexes_with_prefix("ix_editorial_research_runs_")
    op.rename_table("editorial_research_runs", "channel_daily_runs")
    _rename_primary_key(
        "channel_daily_runs",
        "pk_editorial_research_runs",
        "pk_channel_daily_runs",
    )
    op.add_column(
        "channel_daily_runs",
        sa.Column(
            "run_mode",
            sa.String(length=40),
            server_default=sa.text("'REAL_DISABLED'"),
            nullable=False,
        ),
    )
    op.alter_column(
        "channel_daily_runs",
        "run_mode",
        existing_type=sa.String(length=40),
        server_default=None,
    )
    op.add_column(
        "channel_daily_runs",
        sa.Column("daily_idea_decision_id", UUID, nullable=True),
    )
    op.add_column(
        "channel_daily_runs",
        sa.Column("project_admission_decision_id", UUID, nullable=True),
    )
    op.drop_column("channel_daily_runs", "candidate_count")
    op.execute(
        """
        UPDATE channel_daily_runs
        SET status = CASE WHEN status = 'ARCHIVED' THEN 'COMPLETED' ELSE status END,
            trigger_type = CASE
                WHEN trigger_type = 'MIGRATED' THEN 'MANUAL'
                ELSE trigger_type
            END
        """
    )

    op.execute(
        """
        UPDATE retrieval_plan_snapshots
        SET purpose = 'DAILY_IDEA'
        WHERE purpose = 'EDITORIAL_RESEARCH'
        """
    )
    op.execute(
        """
        UPDATE context_pack_snapshots
        SET purpose = 'DAILY_IDEA'
        WHERE purpose = 'EDITORIAL_RESEARCH'
        """
    )
    op.execute(
        """
        UPDATE editorial_calendar_slots
        SET slot_type = 'DAILY'
        WHERE slot_type = 'RESEARCH'
        """
    )
    op.alter_column(
        "daily_idea_decisions",
        "decision_status",
        existing_type=sa.String(length=40),
        server_default=None,
    )
    _restore_legacy_daily_schema_guards_and_references()


def _restore_legacy_daily_schema_guards_and_references() -> None:
    for name, source_table, source_column, target_table in (
        (
            "fk_channel_daily_runs_company_id_companies",
            "channel_daily_runs",
            "company_id",
            "companies",
        ),
        (
            "fk_channel_daily_runs_channel_workspace_id_channel_workspaces",
            "channel_daily_runs",
            "channel_workspace_id",
            "channel_workspaces",
        ),
        (
            "fk_channel_daily_runs_policy_snapshot_id_"
            "compiled_channel_policy_snapshots",
            "channel_daily_runs",
            "policy_snapshot_id",
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_channel_daily_runs_editorial_calendar_slot_id_editorial_calendar_slots",
            "channel_daily_runs",
            "editorial_calendar_slot_id",
            "editorial_calendar_slots",
        ),
        (
            "fk_channel_daily_runs_context_pack_snapshot_id_context_pack_snapshots",
            "channel_daily_runs",
            "context_pack_snapshot_id",
            "context_pack_snapshots",
        ),
        (
            "fk_channel_daily_runs_channel_state_pack_snapshot_id",
            "channel_daily_runs",
            "channel_state_pack_snapshot_id",
            "channel_state_pack_snapshots",
        ),
        (
            "fk_channel_daily_runs_daily_idea_decision_id",
            "channel_daily_runs",
            "daily_idea_decision_id",
            "daily_idea_decisions",
        ),
        (
            "fk_channel_daily_runs_project_admission_decision_id",
            "channel_daily_runs",
            "project_admission_decision_id",
            "project_admission_decisions",
        ),
        (
            "fk_daily_idea_decisions_channel_daily_run_id_channel_daily_runs",
            "daily_idea_decisions",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_daily_idea_decisions_company_id_companies",
            "daily_idea_decisions",
            "company_id",
            "companies",
        ),
        (
            "fk_daily_idea_decisions_channel_workspace_id_channel_workspaces",
            "daily_idea_decisions",
            "channel_workspace_id",
            "channel_workspaces",
        ),
        (
            "fk_daily_idea_decisions_policy_snapshot_id_"
            "compiled_channel_policy_snapshots",
            "daily_idea_decisions",
            "policy_snapshot_id",
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_daily_idea_decisions_context_pack_snapshot_id_context_pack_snapshots",
            "daily_idea_decisions",
            "context_pack_snapshot_id",
            "context_pack_snapshots",
        ),
        (
            "fk_daily_idea_decisions_channel_state_pack_snapshot_id_"
            "channel_state_pack_snapshots",
            "daily_idea_decisions",
            "channel_state_pack_snapshot_id",
            "channel_state_pack_snapshots",
        ),
        (
            "fk_daily_idea_decisions_llm_run_snapshot_id_llm_run_snapshots",
            "daily_idea_decisions",
            "llm_run_snapshot_id",
            "llm_run_snapshots",
        ),
        (
            "fk_channel_state_pack_snapshots_channel_daily_run_id_channel_daily_runs",
            "channel_state_pack_snapshots",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_search_intent_maps_channel_daily_run_id_channel_daily_runs",
            "search_intent_maps",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_search_intent_maps_daily_idea_decision_id",
            "search_intent_maps",
            "daily_idea_decision_id",
            "daily_idea_decisions",
        ),
        (
            "fk_audience_target_packs_channel_daily_run_id_channel_daily_runs",
            "audience_target_packs",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_audience_target_packs_daily_idea_decision_id",
            "audience_target_packs",
            "daily_idea_decision_id",
            "daily_idea_decisions",
        ),
        (
            "fk_idea_market_preflights_channel_daily_run_id_channel_daily_runs",
            "idea_market_preflights",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_idea_market_preflights_daily_idea_decision_id",
            "idea_market_preflights",
            "daily_idea_decision_id",
            "daily_idea_decisions",
        ),
        (
            "fk_project_admission_decisions_channel_daily_run_id_channel_daily_runs",
            "project_admission_decisions",
            "channel_daily_run_id",
            "channel_daily_runs",
        ),
        (
            "fk_project_admission_decisions_daily_idea_decision_id_"
            "daily_idea_decisions",
            "project_admission_decisions",
            "daily_idea_decision_id",
            "daily_idea_decisions",
        ),
    ):
        op.create_foreign_key(
            op.f(name),
            source_table,
            target_table,
            [source_column],
            ["id"],
        )

    for index_name, table_name, columns in (
        ("ix_channel_daily_runs_company_id", "channel_daily_runs", ["company_id"]),
        (
            "ix_channel_daily_runs_channel_workspace_id",
            "channel_daily_runs",
            ["channel_workspace_id"],
        ),
        (
            "ix_channel_daily_runs_policy_snapshot_id",
            "channel_daily_runs",
            ["policy_snapshot_id"],
        ),
        (
            "ix_channel_daily_runs_slot_id",
            "channel_daily_runs",
            ["editorial_calendar_slot_id"],
        ),
        ("ix_channel_daily_runs_run_date", "channel_daily_runs", ["run_date"]),
        ("ix_channel_daily_runs_status", "channel_daily_runs", ["status"]),
        ("ix_channel_daily_runs_created_at", "channel_daily_runs", ["created_at"]),
        (
            "ix_daily_idea_decisions_daily_run_id",
            "daily_idea_decisions",
            ["channel_daily_run_id"],
        ),
        (
            "ix_daily_idea_decisions_company_id",
            "daily_idea_decisions",
            ["company_id"],
        ),
        (
            "ix_daily_idea_decisions_channel_workspace_id",
            "daily_idea_decisions",
            ["channel_workspace_id"],
        ),
        (
            "ix_daily_idea_decisions_policy_snapshot_id",
            "daily_idea_decisions",
            ["policy_snapshot_id"],
        ),
        (
            "ix_daily_idea_decisions_context_pack_id",
            "daily_idea_decisions",
            ["context_pack_snapshot_id"],
        ),
        (
            "ix_daily_idea_decisions_llm_run_id",
            "daily_idea_decisions",
            ["llm_run_snapshot_id"],
        ),
        (
            "ix_daily_idea_decisions_status",
            "daily_idea_decisions",
            ["decision_status"],
        ),
        (
            "ix_daily_idea_decisions_created_at",
            "daily_idea_decisions",
            ["created_at"],
        ),
        (
            "ix_daily_idea_decisions_production_lane",
            "daily_idea_decisions",
            ["production_lane"],
        ),
        (
            "ix_channel_state_pack_snapshots_daily_run_id",
            "channel_state_pack_snapshots",
            ["channel_daily_run_id"],
        ),
        (
            "ix_search_intent_maps_daily_run_id",
            "search_intent_maps",
            ["channel_daily_run_id"],
        ),
        (
            "ix_search_intent_maps_daily_idea_decision_id",
            "search_intent_maps",
            ["daily_idea_decision_id"],
        ),
        (
            "ix_audience_target_packs_daily_run_id",
            "audience_target_packs",
            ["channel_daily_run_id"],
        ),
        (
            "ix_audience_target_packs_daily_idea_decision_id",
            "audience_target_packs",
            ["daily_idea_decision_id"],
        ),
        (
            "ix_idea_market_preflights_daily_run_id",
            "idea_market_preflights",
            ["channel_daily_run_id"],
        ),
        (
            "ix_idea_market_preflights_daily_idea_decision_id",
            "idea_market_preflights",
            ["daily_idea_decision_id"],
        ),
        (
            "ix_project_admission_decisions_daily_run_id",
            "project_admission_decisions",
            ["channel_daily_run_id"],
        ),
        (
            "ix_project_admission_decisions_daily_idea_id",
            "project_admission_decisions",
            ["daily_idea_decision_id"],
        ),
    ):
        op.create_index(index_name, table_name, columns)

    op.create_index(
        "uq_project_admission_v2_daily_source",
        "project_admission_decisions",
        ["daily_idea_decision_id"],
        unique=True,
        postgresql_where=sa.text(
            "schema_version = 'v2' "
            "and planning_source_type = 'DAILY_IDEA' "
            "and daily_idea_decision_id is not null"
        ),
    )

    for table_name, constraint_name, condition in (
        (
            "channel_daily_runs",
            "ck_channel_daily_runs_status",
            "status in "
            "('PENDING','RUNNING','COMPLETED','BLOCKED','FAILED','CANCELLED')",
        ),
        (
            "channel_daily_runs",
            "ck_channel_daily_runs_run_mode",
            "run_mode in ('MOCK','REAL_DISABLED','REAL')",
        ),
        (
            "channel_daily_runs",
            "ck_channel_daily_runs_trigger_type",
            "trigger_type in ('MANUAL','SCHEDULED','TEST')",
        ),
        (
            "daily_idea_decisions",
            "ck_daily_idea_decisions_status",
            "decision_status in "
            "('PROPOSED','ADMITTED','REVIEW_REQUIRED','BLOCKED','REJECTED',"
            "'SKIPPED')",
        ),
        (
            "daily_idea_decisions",
            "ck_daily_idea_decisions_confidence_level",
            "confidence_level in ('HIGH','MEDIUM','LOW','UNKNOWN')",
        ),
        (
            "daily_idea_decisions",
            "ck_daily_idea_decisions_schema_version",
            "schema_version in ('v1','v2')",
        ),
        (
            "daily_idea_decisions",
            "ck_daily_idea_decisions_v2_daily_short",
            "(schema_version = 'v1') or "
            "(schema_version = 'v2' "
            "and production_lane = 'DAILY_SHORT' "
            "and (proposed_content_mode is null "
            "or proposed_content_mode in ('SERIES_EPISODE','STANDALONE')) "
            "and assignment_input_ref is not null)",
        ),
    ):
        op.create_check_constraint(constraint_name, table_name, condition)

    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_prevent_daily_idea_decision_update
        ON daily_idea_decisions;
        CREATE TRIGGER trg_prevent_daily_idea_decision_update
        BEFORE UPDATE ON daily_idea_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_m5_immutable_update();
        """
    )


def _restore_removed_table_shells() -> None:
    """Restore the empty, structurally faithful 0046 Short-era schema.

    The purged rows are intentionally irreversible.  A guarded downgrade may
    nevertheless restore the exact table/constraint/index surface that 0046
    declared, so the revision stamp never describes ID-only placeholder
    tables.  ``creatomate_render_assets`` is deliberately absent: no tracked
    migration created it, and 0047 only removes that unversioned drift with
    ``DROP TABLE IF EXISTS``.
    """

    _restore_m10_1_derivative_tables()
    _restore_m10_1_reuse_release_tables()
    _restore_m10_1_upload_and_m10_2_render_tables()
    _restore_removed_0046_lineage_columns()
    op.alter_column(
        "human_upload_tasks",
        "publish_metadata_ref",
        new_column_name="upload_card_ref",
        existing_type=sa.Text(),
    )


def _legacy_jsonb_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def _legacy_jsonb_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _legacy_created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _legacy_updated_at() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _restore_m10_1_derivative_tables() -> None:
    derivative_type_check = (
        "derivative_type in "
        "('SHORT','CLIP','FOLLOW_UP_LONG','COMPILATION','UPDATE',"
        "'TRANSLATION','OTHER')"
    )
    op.create_table(
        "content_derivative_graph_edges",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_uploaded_video_id", UUID, nullable=True),
        sa.Column("derivative_video_project_id", UUID, nullable=True),
        sa.Column("derivative_uploaded_video_id", UUID, nullable=True),
        sa.Column("derivative_type", sa.String(length=40), nullable=False),
        sa.Column("transformation_summary", sa.Text(), nullable=False),
        sa.Column("new_value_added", sa.Text(), nullable=True),
        sa.Column("originality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "publish_allowed",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("policy_risk_level", sa.String(length=40), nullable=True),
        sa.Column("rights_risk_level", sa.String(length=40), nullable=True),
        sa.Column(
            "source_refs",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "technical_appendix",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            derivative_type_check,
            name="ck_content_derivative_edges_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_refs) = 'array'",
            name="ck_content_derivative_edges_source_refs_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(technical_appendix) = 'object'",
            name="ck_content_derivative_edges_appendix_object",
        ),
        sa.CheckConstraint(
            "publish_allowed = false or derivative_type <> 'COMPILATION' "
            "or coalesce(new_value_added, '') <> ''",
            name="ck_content_derivative_edges_compilation_needs_value",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_uploaded_video_id"],
            ["uploaded_videos.id"],
        ),
        sa.ForeignKeyConstraint(
            ["derivative_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["derivative_uploaded_video_id"],
            ["uploaded_videos.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_derivative_edges_company_id", ["company_id"]),
        ("ix_derivative_edges_channel_id", ["channel_workspace_id"]),
        ("ix_derivative_edges_parent_project", ["parent_video_project_id"]),
        ("ix_derivative_edges_parent_uploaded", ["parent_uploaded_video_id"]),
        ("ix_derivative_edges_type", ["derivative_type"]),
    ):
        op.create_index(index_name, "content_derivative_graph_edges", columns)

    op.create_table(
        "short_candidates",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("parent_video_project_id", UUID, nullable=False),
        sa.Column("parent_voice_timeline_id", UUID, nullable=True),
        sa.Column("parent_caption_track_id", UUID, nullable=True),
        sa.Column("parent_visual_plan_id", UUID, nullable=True),
        sa.Column("start_time_ms", sa.Integer(), nullable=False),
        sa.Column("end_time_ms", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "caption_ids",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("core_idea", sa.Text(), nullable=False),
        sa.Column("hook_line", sa.Text(), nullable=False),
        sa.Column("standalone_summary", sa.Text(), nullable=False),
        sa.Column("suggested_title", sa.Text(), nullable=True),
        sa.Column("overlay_text", sa.Text(), nullable=True),
        sa.Column("crop_strategy", sa.String(length=40), nullable=False),
        sa.Column("visual_source", sa.String(length=40), nullable=False),
        sa.Column("candidate_state", sa.String(length=40), nullable=False),
        sa.Column("policy_risk_level", sa.String(length=40), nullable=True),
        sa.Column("rights_risk_level", sa.String(length=40), nullable=True),
        sa.Column(
            "production_cost_estimate",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "candidate_state in "
            "('GENERATED','SCORED','SELECTED_FOR_RENDER','REJECTED',"
            "'NEEDS_REWRITE','BLOCKED')",
            name="ck_short_candidates_state",
        ),
        sa.CheckConstraint(
            "crop_strategy in "
            "('VERTICAL_9_16','CENTER_CROP','SMART_CROP','TEMPLATE_CARD',"
            "'DIAGRAM_CARD')",
            name="ck_short_candidates_crop_strategy",
        ),
        sa.CheckConstraint(
            "visual_source in "
            "('PARENT_HERO_REUSE','PARENT_SCENE_REUSE','TEMPLATE_CARD',"
            "'DIAGRAM_CARD','SCREENSHOT','NEW_AI_HERO_REQUIRED','UNKNOWN')",
            name="ck_short_candidates_visual_source",
        ),
        sa.CheckConstraint(
            "duration_ms = end_time_ms - start_time_ms",
            name="ck_short_candidates_duration_matches",
        ),
        sa.CheckConstraint(
            "duration_ms > 0 and duration_ms < 59000",
            name="ck_short_candidates_duration_cap",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(caption_ids) = 'array'",
            name="ck_short_candidates_caption_ids_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(production_cost_estimate) = 'object'",
            name="ck_short_candidates_cost_object",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_voice_timeline_id"],
            ["voice_timeline_snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_caption_track_id"],
            ["caption_track_snapshots.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_visual_plan_id"],
            ["visual_plan_snapshots.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_short_candidates_company_id", ["company_id"]),
        ("ix_short_candidates_channel_id", ["channel_workspace_id"]),
        ("ix_short_candidates_parent_project", ["parent_video_project_id"]),
        ("ix_short_candidates_state", ["candidate_state"]),
    ):
        op.create_index(index_name, "short_candidates", columns)

    op.create_table(
        "short_candidate_scores",
        sa.Column("id", UUID, nullable=False),
        sa.Column("short_candidate_id", UUID, nullable=False),
        sa.Column("hook_strength", sa.Numeric(8, 4), nullable=False),
        sa.Column("standalone_clarity", sa.Numeric(8, 4), nullable=False),
        sa.Column("insight_density", sa.Numeric(8, 4), nullable=False),
        sa.Column("visual_punch", sa.Numeric(8, 4), nullable=False),
        sa.Column("audience_relevance", sa.Numeric(8, 4), nullable=False),
        sa.Column("bridge_value", sa.Numeric(8, 4), nullable=False),
        sa.Column("production_reuse_saving", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "context_dependency_penalty",
            sa.Numeric(8, 4),
            nullable=False,
        ),
        sa.Column("policy_risk_penalty", sa.Numeric(8, 4), nullable=False),
        sa.Column("generic_template_penalty", sa.Numeric(8, 4), nullable=False),
        sa.Column("total_score", sa.Numeric(8, 4), nullable=False),
        sa.Column("score_version", sa.String(length=40), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        _legacy_created_at(),
        sa.ForeignKeyConstraint(
            ["short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_short_candidate_scores_candidate_id",
        "short_candidate_scores",
        ["short_candidate_id"],
    )
    op.create_index(
        "ix_short_candidate_scores_total",
        "short_candidate_scores",
        ["total_score"],
    )

    op.create_table(
        "short_render_plans",
        sa.Column("id", UUID, nullable=False),
        sa.Column("short_candidate_id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("target_platform", sa.String(length=40), nullable=False),
        sa.Column(
            "target_aspect_ratio",
            sa.String(length=20),
            server_default=sa.text("'9:16'"),
            nullable=False,
        ),
        sa.Column("target_duration_ms", sa.Integer(), nullable=False),
        sa.Column("voice_source", sa.String(length=40), nullable=False),
        sa.Column("caption_style_ref", sa.Text(), nullable=True),
        sa.Column(
            "visual_plan",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column("render_state", sa.String(length=40), nullable=False),
        sa.Column("blocker_reason", sa.Text(), nullable=True),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "target_platform in ('YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS')",
            name="ck_short_render_plans_target_platform",
        ),
        sa.CheckConstraint(
            "voice_source in "
            "('REUSE_PARENT_AUDIO','NEW_SHORT_VOICE_REQUIRED','MOCK_ONLY')",
            name="ck_short_render_plans_voice_source",
        ),
        sa.CheckConstraint(
            "render_state in "
            "('PLANNED','BLOCKED','READY_FOR_M10_2_RENDER','CANCELLED')",
            name="ck_short_render_plans_render_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(visual_plan) = 'object'",
            name="ck_short_render_plans_visual_plan_object",
        ),
        sa.ForeignKeyConstraint(
            ["short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_short_render_plans_candidate_id", ["short_candidate_id"]),
        ("ix_short_render_plans_platform", ["target_platform"]),
        ("ix_short_render_plans_state", ["render_state"]),
    ):
        op.create_index(index_name, "short_render_plans", columns)

    op.create_table(
        "promote_short_to_long_candidates",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("source_short_uploaded_video_id", UUID, nullable=True),
        sa.Column("source_short_candidate_id", UUID, nullable=True),
        sa.Column("winning_hook", sa.Text(), nullable=False),
        sa.Column(
            "audience_signal",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column("suggested_long_topic", sa.Text(), nullable=False),
        sa.Column(
            "suggested_outline",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "expected_watch_hour_potential",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column("confidence_label", sa.String(length=40), nullable=True),
        sa.Column("risk_level", sa.String(length=40), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column(
            "evidence_refs",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "expected_watch_hour_potential in ('LOW','MEDIUM','HIGH','UNKNOWN')",
            name="ck_promote_short_to_long_watch_hours",
        ),
        sa.CheckConstraint(
            "state in "
            "('GENERATED','NEEDS_MORE_EVIDENCE','READY_FOR_HUMAN_REVIEW',"
            "'REJECTED','CANCELLED')",
            name="ck_promote_short_to_long_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(audience_signal) = 'object'",
            name="ck_promote_short_to_long_audience_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(suggested_outline) = 'object'",
            name="ck_promote_short_to_long_outline_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_refs) = 'array'",
            name="ck_promote_short_to_long_refs_array",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_short_uploaded_video_id"],
            ["uploaded_videos.id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_promote_short_to_long_company_id", ["company_id"]),
        ("ix_promote_short_to_long_channel_id", ["channel_workspace_id"]),
        ("ix_promote_short_to_long_state", ["state"]),
    ):
        op.create_index(index_name, "promote_short_to_long_candidates", columns)


def _restore_m10_1_reuse_release_tables() -> None:
    op.create_table(
        "reusable_artifacts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=True),
        sa.Column("artifact_type", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("source_provider", sa.String(length=160), nullable=True),
        sa.Column("license_status", sa.String(length=80), nullable=False),
        sa.Column("rights_envelope_id", UUID, nullable=True),
        sa.Column("reuse_scope", sa.String(length=40), nullable=False),
        sa.Column(
            "reuse_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "max_reuse_policy",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column("cooldown_days", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_used_video_ids",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("quality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("state", sa.String(length=40), nullable=False),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "artifact_type in "
            "('SCRIPT_BLOCK','RESEARCH_PACKET','DIAGRAM_TEMPLATE',"
            "'MOTION_TEMPLATE','STOCK_CLIP','AI_VIDEO_CLIP','MUSIC_BED',"
            "'SFX','VOICE_LINE','CAPTION_STYLE','PROMPT_PREFIX',"
            "'THUMBNAIL_TEMPLATE','OTHER')",
            name="ck_reusable_artifacts_type",
        ),
        sa.CheckConstraint(
            "reuse_scope in ('CHANNEL','SERIES','COMPANY','PROJECT_ONLY')",
            name="ck_reusable_artifacts_scope",
        ),
        sa.CheckConstraint(
            "state in ('ACTIVE','NEEDS_REVIEW','RETIRED','BLOCKED')",
            name="ck_reusable_artifacts_state",
        ),
        sa.CheckConstraint(
            "reuse_count >= 0",
            name="ck_reusable_artifacts_reuse_count_nonnegative",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(max_reuse_policy) = 'object'",
            name="ck_reusable_artifacts_policy_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(last_used_video_ids) = 'array'",
            name="ck_reusable_artifacts_last_used_array",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_reusable_artifacts_company_id", ["company_id"]),
        ("ix_reusable_artifacts_channel_id", ["channel_workspace_id"]),
        ("ix_reusable_artifacts_hash", ["content_hash"]),
        ("ix_reusable_artifacts_state", ["state"]),
    ):
        op.create_index(index_name, "reusable_artifacts", columns)

    op.create_table(
        "asset_reuse_index_entries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("reusable_artifact_id", UUID, nullable=False),
        sa.Column("scene_requirement_hash", sa.String(length=128), nullable=False),
        sa.Column("match_reason", sa.Text(), nullable=False),
        sa.Column("match_score", sa.Numeric(8, 4), nullable=False),
        sa.Column(
            "last_selected_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        _legacy_created_at(),
        sa.ForeignKeyConstraint(
            ["reusable_artifact_id"],
            ["reusable_artifacts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_asset_reuse_entries_artifact_id",
        "asset_reuse_index_entries",
        ["reusable_artifact_id"],
    )
    op.create_index(
        "ix_asset_reuse_entries_requirement_hash",
        "asset_reuse_index_entries",
        ["scene_requirement_hash"],
    )

    op.create_table(
        "derivative_originality_checks",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("content_derivative_edge_id", UUID, nullable=True),
        sa.Column("short_candidate_id", UUID, nullable=True),
        sa.Column("derivative_type", sa.String(length=40), nullable=False),
        sa.Column(
            "standalone_value_ok",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "new_value_added_ok",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "template_repetition_risk",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column("generic_stock_risk", sa.String(length=40), nullable=True),
        sa.Column(
            "commentary_or_context_added",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "policy_flags",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "rights_flags",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("result", sa.String(length=40), nullable=False),
        sa.Column("operator_summary", sa.Text(), nullable=False),
        sa.Column(
            "technical_appendix",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        _legacy_created_at(),
        sa.CheckConstraint(
            "derivative_type in "
            "('SHORT','CLIP','FOLLOW_UP_LONG','COMPILATION','UPDATE',"
            "'TRANSLATION','OTHER')",
            name="ck_derivative_originality_type",
        ),
        sa.CheckConstraint(
            "result in ('PASS','REVIEW_REQUIRED','BLOCK')",
            name="ck_derivative_originality_result",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_flags) = 'array'",
            name="ck_derivative_originality_policy_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rights_flags) = 'array'",
            name="ck_derivative_originality_rights_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(technical_appendix) = 'object'",
            name="ck_derivative_originality_appendix_object",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["content_derivative_edge_id"],
            ["content_derivative_graph_edges.id"],
        ),
        sa.ForeignKeyConstraint(
            ["short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_derivative_originality_company_id", ["company_id"]),
        ("ix_derivative_originality_short_id", ["short_candidate_id"]),
        ("ix_derivative_originality_result", ["result"]),
    ):
        op.create_index(index_name, "derivative_originality_checks", columns)

    op.create_table(
        "originality_budgets",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=True),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("uploaded_video_id", UUID, nullable=True),
        sa.Column("new_script_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "new_narrative_angle_score",
            sa.Numeric(8, 4),
            nullable=True,
        ),
        sa.Column(
            "new_diagram_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("reused_runtime_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "same_template_recent_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "same_stock_clip_recent_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "derivative_count_from_parent",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("originality_score", sa.Numeric(8, 4), nullable=True),
        sa.Column("result", sa.String(length=40), nullable=False),
        _legacy_created_at(),
        sa.CheckConstraint(
            "result in ('OK','REVIEW_REQUIRED','BLOCK')",
            name="ck_originality_budgets_result",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(["uploaded_video_id"], ["uploaded_videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_originality_budgets_company_id", ["company_id"]),
        ("ix_originality_budgets_project_id", ["video_project_id"]),
        ("ix_originality_budgets_result", ["result"]),
    ):
        op.create_index(index_name, "originality_budgets", columns)

    op.create_table(
        "derivative_release_plans",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_uploaded_video_id", UUID, nullable=True),
        sa.Column(
            "max_shorts_per_long",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
        ),
        sa.Column(
            "min_spacing_hours",
            sa.Integer(),
            server_default=sa.text("24"),
            nullable=False,
        ),
        sa.Column(
            "preferred_publish_order",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "platform_surface",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "bridge_strategy",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "avoid_same_day_spam",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("release_state", sa.String(length=40), nullable=False),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "release_state in ('DRAFT','READY_FOR_HUMAN_REVIEW','BLOCKED','CANCELLED')",
            name="ck_derivative_release_plans_state",
        ),
        sa.CheckConstraint(
            "max_shorts_per_long >= 0 and max_shorts_per_long <= 3",
            name="ck_derivative_release_plans_max_shorts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(preferred_publish_order) = 'array'",
            name="ck_derivative_release_plans_order_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(platform_surface) = 'array'",
            name="ck_derivative_release_plans_surface_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bridge_strategy) = 'object'",
            name="ck_derivative_release_plans_bridge_object",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_uploaded_video_id"],
            ["uploaded_videos.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_derivative_release_plans_company_id", ["company_id"]),
        (
            "ix_derivative_release_plans_parent_project",
            ["parent_video_project_id"],
        ),
        ("ix_derivative_release_plans_state", ["release_state"]),
    ):
        op.create_index(index_name, "derivative_release_plans", columns)

    op.create_table(
        "cross_platform_funnel_packages",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("parent_video_project_id", UUID, nullable=True),
        sa.Column("parent_uploaded_video_id", UUID, nullable=True),
        sa.Column("youtube_long_package_id", UUID, nullable=True),
        sa.Column(
            "selected_short_candidate_ids",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "youtube_shorts_package_status",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column(
            "tiktok_package_status",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column(
            "facebook_reels_package_status",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column(
            "bridge_strategy",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column("package_state", sa.String(length=40), nullable=False),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "package_state in "
            "('DRAFT','READY_FOR_HUMAN_REVIEW','READY_FOR_UPLOAD_TASKS',"
            "'BLOCKED','CANCELLED')",
            name="ck_cross_platform_funnel_packages_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_short_candidate_ids) = 'array'",
            name="ck_cross_platform_funnel_packages_selected_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(bridge_strategy) = 'object'",
            name="ck_cross_platform_funnel_packages_bridge_object",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_video_project_id"],
            ["video_projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["parent_uploaded_video_id"],
            ["uploaded_videos.id"],
        ),
        sa.ForeignKeyConstraint(
            ["youtube_long_package_id"],
            ["publish_handoff_packages.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_cross_platform_funnel_packages_company_id", ["company_id"]),
        (
            "ix_cross_platform_funnel_packages_parent_project",
            ["parent_video_project_id"],
        ),
        ("ix_cross_platform_funnel_packages_state", ["package_state"]),
    ):
        op.create_index(index_name, "cross_platform_funnel_packages", columns)

    op.create_table(
        "usage_savings_ledger_entries",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=True),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column(
            "estimated_cost_without_reuse",
            sa.Numeric(18, 6),
            nullable=True,
        ),
        sa.Column("actual_cost_with_reuse", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_tokens", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_ai_video_seconds", sa.Numeric(18, 6), nullable=True),
        sa.Column("saved_tts_characters", sa.Numeric(18, 6), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _legacy_created_at(),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_usage_savings_company_id", ["company_id"]),
        ("ix_usage_savings_project_id", ["video_project_id"]),
        ("ix_usage_savings_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "usage_savings_ledger_entries", columns)


def _restore_m10_1_upload_and_m10_2_render_tables() -> None:
    op.create_table(
        "upload_cards",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("platform", sa.String(length=40), nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("short_candidate_id", UUID, nullable=True),
        sa.Column("render_plan_id", UUID, nullable=True),
        sa.Column("file_ref", sa.Text(), nullable=True),
        sa.Column("title_internal", sa.Text(), nullable=False),
        sa.Column("hook_line", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "hashtags",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("cta_type", sa.String(length=40), nullable=False),
        sa.Column("cta_text", sa.Text(), nullable=True),
        sa.Column("pinned_comment", sa.Text(), nullable=True),
        sa.Column(
            "ai_disclosure_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "ai_disclosure_reason",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("music_policy", sa.String(length=40), nullable=False),
        sa.Column("cover_frame_suggestion", sa.Text(), nullable=True),
        sa.Column(
            "human_notes",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "paste_back_required_fields",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column("card_state", sa.String(length=40), nullable=False),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "platform in ('YOUTUBE_LONG','YOUTUBE_SHORTS','TIKTOK','FACEBOOK_REELS')",
            name="ck_upload_cards_platform",
        ),
        sa.CheckConstraint(
            "cta_type in "
            "('NONE','SEARCH_YOUTUBE','BRAND_CTA','LINK_IN_BIO',"
            "'PINNED_COMMENT')",
            name="ck_upload_cards_cta_type",
        ),
        sa.CheckConstraint(
            "music_policy in ('SAFE_MODE','PLATFORM_NATIVE_MODE','NO_MUSIC_MODE')",
            name="ck_upload_cards_music_policy",
        ),
        sa.CheckConstraint(
            "card_state in "
            "('DRAFT','READY','UPLOAD_INPUT_MISSING','AWAITING_FINAL_MEDIA',"
            "'BLOCKED','USED','CANCELLED')",
            name="ck_upload_cards_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(hashtags) = 'array'",
            name="ck_upload_cards_hashtags_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ai_disclosure_reason) = 'array'",
            name="ck_upload_cards_ai_reason_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(human_notes) = 'array'",
            name="ck_upload_cards_human_notes_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(paste_back_required_fields) = 'array'",
            name="ck_upload_cards_paste_back_array",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.ForeignKeyConstraint(["render_plan_id"], ["short_render_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_upload_cards_company_id", ["company_id"]),
        ("ix_upload_cards_channel_id", ["channel_workspace_id"]),
        ("ix_upload_cards_short_candidate_id", ["short_candidate_id"]),
        ("ix_upload_cards_platform", ["platform"]),
        ("ix_upload_cards_state", ["card_state"]),
    ):
        op.create_index(index_name, "upload_cards", columns)

    op.create_table(
        "short_render_packages",
        sa.Column("id", UUID, nullable=False),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("channel_workspace_id", UUID, nullable=False),
        sa.Column("video_project_id", UUID, nullable=True),
        sa.Column("short_candidate_id", UUID, nullable=True),
        sa.Column("short_render_plan_id", UUID, nullable=True),
        sa.Column("voice_ref", sa.Text(), nullable=True),
        sa.Column("caption_track_id", UUID, nullable=True),
        sa.Column("hero_reuse_ref", sa.Text(), nullable=True),
        sa.Column(
            "template_asset_refs",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        sa.Column(
            "render_manifest",
            JSONB,
            server_default=_legacy_jsonb_object(),
            nullable=False,
        ),
        sa.Column(
            "target_duration_seconds",
            sa.Numeric(18, 6),
            nullable=True,
        ),
        sa.Column(
            "target_aspect_ratio",
            sa.String(length=20),
            server_default=sa.text("'9:16'"),
            nullable=False,
        ),
        sa.Column(
            "hard_cap_seconds",
            sa.Integer(),
            server_default=sa.text("59"),
            nullable=False,
        ),
        sa.Column(
            "renderer_provider_key",
            sa.String(length=160),
            nullable=True,
        ),
        sa.Column("package_state", sa.String(length=80), nullable=False),
        sa.Column(
            "cloud_media_refs",
            JSONB,
            server_default=_legacy_jsonb_array(),
            nullable=False,
        ),
        _legacy_created_at(),
        _legacy_updated_at(),
        sa.CheckConstraint(
            "package_state in "
            "('DRAFT','READY_FOR_TEMPLATE_RENDER','RENDERED','QC_READY',"
            "'BLOCKED','CANCELLED')",
            name="ck_short_render_packages_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(template_asset_refs) = 'array'",
            name="ck_short_pkg_template_refs_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(render_manifest) = 'object'",
            name="ck_short_pkg_manifest_object",
        ),
        sa.CheckConstraint(
            "target_duration_seconds is null "
            "or target_duration_seconds < hard_cap_seconds",
            name="ck_short_pkg_duration_cap",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(cloud_media_refs) = 'array'",
            name="ck_short_pkg_cloud_refs_array",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(
            ["channel_workspace_id"],
            ["channel_workspaces.id"],
        ),
        sa.ForeignKeyConstraint(["video_project_id"], ["video_projects.id"]),
        sa.ForeignKeyConstraint(
            ["short_candidate_id"],
            ["short_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["short_render_plan_id"],
            ["short_render_plans.id"],
        ),
        sa.ForeignKeyConstraint(
            ["caption_track_id"],
            ["caption_track_snapshots.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_short_render_packages_company", ["company_id"]),
        ("ix_short_render_packages_candidate", ["short_candidate_id"]),
        ("ix_short_render_packages_state", ["package_state"]),
    ):
        op.create_index(index_name, "short_render_packages", columns)


def _restore_removed_0046_lineage_columns() -> None:
    # Columns dropped by 0047 are restored only after their referenced tables
    # exist, then their 0046 FK/index shape is rebuilt explicitly.
    for table_name, columns in (
        (
            "video_projects",
            (
                sa.Column("parent_video_project_id", UUID, nullable=True),
                sa.Column("parent_final_media_ref_id", UUID, nullable=True),
                sa.Column("canonical_timeline_ref", sa.Text(), nullable=True),
                sa.Column(
                    "canonical_timeline_hash",
                    sa.String(length=64),
                    nullable=True,
                ),
            ),
        ),
        (
            "project_admission_decisions",
            (
                sa.Column("parent_video_project_id", UUID, nullable=True),
                sa.Column("parent_final_media_ref_id", UUID, nullable=True),
                sa.Column("canonical_timeline_ref", sa.Text(), nullable=True),
                sa.Column(
                    "canonical_timeline_hash",
                    sa.String(length=64),
                    nullable=True,
                ),
            ),
        ),
        (
            "final_review_candidates",
            (
                sa.Column("parent_video_project_id", UUID, nullable=True),
                sa.Column("parent_final_media_ref_id", UUID, nullable=True),
            ),
        ),
        (
            "human_upload_tasks",
            (
                sa.Column("upload_card_id", UUID, nullable=True),
                sa.Column("parent_video_project_id", UUID, nullable=True),
                sa.Column("parent_final_media_ref_id", UUID, nullable=True),
            ),
        ),
        (
            "uploaded_videos",
            (
                sa.Column("parent_video_project_id", UUID, nullable=True),
                sa.Column("parent_final_media_ref_id", UUID, nullable=True),
            ),
        ),
        (
            "platform_native_package_plans",
            (sa.Column("derivative_manifest_ref", sa.Text(), nullable=True),),
        ),
    ):
        for column in columns:
            op.add_column(table_name, column)

    for name, table_name, source_column, target_table in (
        (
            "fk_video_projects_v2_parent_project",
            "video_projects",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_video_projects_v2_parent_media",
            "video_projects",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_admission_v2_parent_project",
            "project_admission_decisions",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_admission_v2_parent_media",
            "project_admission_decisions",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_final_review_candidates_parent_video_project_id_video_projects",
            "final_review_candidates",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_final_review_candidates_parent_final_media_ref_id_final_media_refs",
            "final_review_candidates",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_human_upload_tasks_upload_card_id_upload_cards",
            "human_upload_tasks",
            "upload_card_id",
            "upload_cards",
        ),
        (
            "fk_human_upload_tasks_parent_video_project_id_video_projects",
            "human_upload_tasks",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_hut_parent_final_media",
            "human_upload_tasks",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
        (
            "fk_uploaded_videos_parent_video_project_id_video_projects",
            "uploaded_videos",
            "parent_video_project_id",
            "video_projects",
        ),
        (
            "fk_uploaded_videos_parent_final_media_ref_id_final_media_refs",
            "uploaded_videos",
            "parent_final_media_ref_id",
            "final_media_refs",
        ),
    ):
        op.create_foreign_key(
            op.f(name),
            table_name,
            target_table,
            [source_column],
            ["id"],
        )
    op.create_index(
        "ix_video_projects_parent_video_project_id",
        "video_projects",
        ["parent_video_project_id"],
    )
    op.create_index(
        "ix_human_upload_tasks_card_id",
        "human_upload_tasks",
        ["upload_card_id"],
    )


def _create_editorial_research_core_foreign_keys() -> None:
    for constraint_name, local_columns, remote_table in (
        (
            "fk_editorial_research_runs_company_id_companies",
            ["company_id"],
            "companies",
        ),
        (
            "fk_editorial_research_runs_channel_workspace_id_channel_workspaces",
            ["channel_workspace_id"],
            "channel_workspaces",
        ),
        (
            "fk_editorial_research_runs_policy_snapshot_id_"
            "compiled_channel_policy_snapshots",
            ["policy_snapshot_id"],
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_editorial_research_runs_editorial_calendar_slot_id_"
            "editorial_calendar_slots",
            ["editorial_calendar_slot_id"],
            "editorial_calendar_slots",
        ),
        (
            "fk_editorial_research_runs_context_pack_snapshot_id_"
            "context_pack_snapshots",
            ["context_pack_snapshot_id"],
            "context_pack_snapshots",
        ),
        (
            "fk_editorial_research_runs_channel_state_pack_snapshot_id_"
            "channel_state_pack_snapshots",
            ["channel_state_pack_snapshot_id"],
            "channel_state_pack_snapshots",
        ),
    ):
        op.create_foreign_key(
            op.f(constraint_name),
            "editorial_research_runs",
            remote_table,
            local_columns,
            ["id"],
        )


def _create_editorial_research_indexes() -> None:
    for index_name, columns in (
        ("ix_editorial_research_runs_company_id", ["company_id"]),
        (
            "ix_editorial_research_runs_channel_workspace_id",
            ["channel_workspace_id"],
        ),
        (
            "ix_editorial_research_runs_policy_snapshot_id",
            ["policy_snapshot_id"],
        ),
        (
            "ix_editorial_research_runs_slot_id",
            ["editorial_calendar_slot_id"],
        ),
        ("ix_editorial_research_runs_run_date", ["run_date"]),
        ("ix_editorial_research_runs_status", ["status"]),
        ("ix_editorial_research_runs_created_at", ["created_at"]),
    ):
        op.create_index(index_name, "editorial_research_runs", columns)


def _create_editorial_candidate_core_foreign_keys() -> None:
    for constraint_name, local_columns, remote_table in (
        (
            "fk_editorial_idea_candidates_editorial_research_run_id_"
            "editorial_research_runs",
            ["editorial_research_run_id"],
            "editorial_research_runs",
        ),
        (
            "fk_editorial_idea_candidates_company_id_companies",
            ["company_id"],
            "companies",
        ),
        (
            "fk_editorial_idea_candidates_channel_workspace_id_channel_workspaces",
            ["channel_workspace_id"],
            "channel_workspaces",
        ),
        (
            "fk_editorial_idea_candidates_policy_snapshot_id_"
            "compiled_channel_policy_snapshots",
            ["policy_snapshot_id"],
            "compiled_channel_policy_snapshots",
        ),
        (
            "fk_editorial_idea_candidates_context_pack_snapshot_id_"
            "context_pack_snapshots",
            ["context_pack_snapshot_id"],
            "context_pack_snapshots",
        ),
        (
            "fk_editorial_idea_candidates_channel_state_pack_snapshot_id_"
            "channel_state_pack_snapshots",
            ["channel_state_pack_snapshot_id"],
            "channel_state_pack_snapshots",
        ),
        (
            "fk_editorial_idea_candidates_llm_run_snapshot_id_llm_run_snapshots",
            ["llm_run_snapshot_id"],
            "llm_run_snapshots",
        ),
    ):
        op.create_foreign_key(
            op.f(constraint_name),
            "editorial_idea_candidates",
            remote_table,
            local_columns,
            ["id"],
        )


def _replace_immutable_snapshot_purpose_guards() -> None:
    """Preserve legacy immutable rows while closing the old write domain."""

    for table_name, constraint_name in (
        (
            "retrieval_plan_snapshots",
            "ck_retrieval_plan_snapshots_purpose",
        ),
        (
            "context_pack_snapshots",
            "ck_context_pack_snapshots_purpose",
        ),
    ):
        _drop_check_constraints_matching(table_name, constraint_name)
        _drop_checks_with_removed_vocabulary(table_name)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_editorial_snapshot_purpose()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.purpose NOT IN (
                'EDITORIAL_RESEARCH',
                'PROJECT_ADMISSION',
                'AUTHORITY_REVIEW',
                'SEARCH_DEMAND',
                'TEST'
            ) THEN
                RAISE EXCEPTION
                    'new snapshot purpose must use editorial vocabulary';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_guard_retrieval_plan_snapshot_purpose
        BEFORE INSERT ON retrieval_plan_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION guard_editorial_snapshot_purpose();

        CREATE TRIGGER trg_guard_context_pack_snapshot_purpose
        BEFORE INSERT ON context_pack_snapshots
        FOR EACH ROW
        EXECUTE FUNCTION guard_editorial_snapshot_purpose();
        """
    )


def _replace_check(
    table_name: str,
    constraint_name: str,
    condition: str,
) -> None:
    _drop_check_constraints_matching(table_name, constraint_name)
    _drop_checks_with_removed_vocabulary(table_name)
    op.create_check_constraint(constraint_name, table_name, condition)


def _restore_check(
    table_name: str,
    constraint_name: str,
    condition: str,
) -> None:
    """Replace one 0047-narrowed guard without touching unrelated checks."""

    _drop_check_constraints_matching(table_name, constraint_name)
    op.create_check_constraint(constraint_name, table_name, condition)


def _drop_checks_with_removed_vocabulary(table_name: str) -> None:
    """Drop predecessor checks even when PostgreSQL truncated their names.

    Several logical check names are longer than PostgreSQL's 63-byte
    identifier limit.  SQLAlchemy's naming convention can therefore map an
    old and replacement logical name to the same truncated physical name.
    Match the removed vocabulary in the definition rather than relying only
    on the physical identifier.
    """

    escaped_table = table_name.replace("'", "''")
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{escaped_table}'::regclass
                  AND contype = 'c'
                  AND (
                    lower(pg_get_constraintdef(oid)) LIKE '%youtube_shorts%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%daily_short%'
                    OR lower(pg_get_constraintdef(oid))
                        LIKE '%long_derived_short%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%derived_short%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%daily_idea%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%short_form%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%short_final%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%short_hook%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%new_short%'
                    OR lower(pg_get_constraintdef(oid))
                        LIKE '%pause_daily_generation%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%9:16%'
                    OR lower(pg_get_constraintdef(oid)) LIKE '%reels%'
                    OR (
                        '{escaped_table}' = 'editorial_calendar_slots'
                        AND lower(pg_get_constraintdef(oid))
                            LIKE '%''daily''%'
                    )
                  )
            LOOP
                EXECUTE format(
                    'ALTER TABLE "{escaped_table}" DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )


def _drop_check_constraints_matching(
    table_name: str,
    logical_name: str,
) -> None:
    escaped_table = table_name.replace("'", "''")
    escaped_name = logical_name.replace("'", "''")
    # Alembic uses Base.metadata's naming convention.  An explicit logical
    # name such as ``ck_editorial_calendar_slots_slot_type`` is therefore
    # stored by PostgreSQL as ``ck_<table>_<logical-name>`` and, when that
    # exceeds 63 bytes, gets a deterministic four-character hash suffix.
    # The predecessor constraint for editorial_calendar_slots is one such
    # name.  Matching only the logical tail misses it and causes the
    # replacement CREATE below to collide on empty-DB upgrades.
    convention_name = f"ck_{table_name}_{logical_name}"
    physical_name = (
        postgresql.dialect()
        .identifier_preparer.truncate_and_render_constraint_name(
            conv(convention_name),
            _alembic_quote=False,
        )
        .replace("'", "''")
    )
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{escaped_table}'::regclass
                  AND contype = 'c'
                  AND (
                    conname = '{escaped_name}'
                    OR conname = '{physical_name}'
                    OR conname LIKE '%{escaped_name}'
                    OR conname LIKE '%{escaped_name[-24:]}'
                  )
            LOOP
                EXECUTE format(
                    'ALTER TABLE "{escaped_table}" DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )


def _drop_all_checks(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{table_name}'::regclass
                  AND contype = 'c'
            LOOP
                EXECUTE format(
                    'ALTER TABLE "{table_name}" DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )


def _drop_all_foreign_keys(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{table_name}'::regclass
                  AND contype = 'f'
            LOOP
                EXECUTE format(
                    'ALTER TABLE "{table_name}" DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )


def _drop_foreign_keys_for_columns(
    table_name: str,
    column_names: list[str],
) -> None:
    if not column_names:
        return
    sql_columns = _sql_string_array(tuple(column_names))
    op.execute(
        f"""
        DO $$
        DECLARE
            constraint_name text;
        BEGIN
            FOR constraint_name IN
                SELECT DISTINCT c.conname
                FROM pg_constraint AS c
                JOIN unnest(c.conkey) AS key(attnum) ON true
                JOIN pg_attribute AS a
                  ON a.attrelid = c.conrelid
                 AND a.attnum = key.attnum
                WHERE c.conrelid = '{table_name}'::regclass
                  AND c.contype = 'f'
                  AND a.attname = ANY (ARRAY[{sql_columns}])
            LOOP
                EXECUTE format(
                    'ALTER TABLE "{table_name}" DROP CONSTRAINT %I',
                    constraint_name
                );
            END LOOP;
        END
        $$;
        """
    )


def _rename_primary_key(
    table_name: str,
    old_name: str,
    new_name: str,
) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = '{table_name}'::regclass
                  AND conname = '{old_name}'
            ) THEN
                ALTER TABLE "{table_name}"
                RENAME CONSTRAINT "{old_name}" TO "{new_name}";
            END IF;
        END
        $$;
        """
    )


def _drop_indexes_with_prefix(prefix: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            index_name text;
        BEGIN
            FOR index_name IN
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND indexname LIKE '{prefix}%'
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', index_name);
            END LOOP;
        END
        $$;
        """
    )


def _drop_daily_reference_indexes(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            index_name text;
        BEGIN
            FOR index_name IN
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = '{table_name}'
                  AND (
                    indexdef LIKE '%channel_daily_run_id%'
                    OR indexdef LIKE '%daily_idea_decision_id%'
                  )
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', index_name);
            END LOOP;
        END
        $$;
        """
    )


def _drop_editorial_reference_indexes(table_name: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            index_name text;
        BEGIN
            FOR index_name IN
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = '{table_name}'
                  AND (
                    indexdef LIKE '%editorial_research_run_id%'
                    OR indexdef LIKE '%editorial_idea_candidate_id%'
                  )
            LOOP
                EXECUTE format('DROP INDEX IF EXISTS %I', index_name);
            END LOOP;
        END
        $$;
        """
    )


def _fail_closed_if_fk_references_ids(
    target_table: str,
    temp_id_table: str,
    message: str,
) -> None:
    """Reject a destructive purge when a retained row still owns an FK edge."""

    safe_target = target_table.replace("'", "''")
    safe_temp = temp_id_table.replace("'", "''")
    safe_message = message.replace("'", "''")
    op.execute(
        f"""
        DO $$
        DECLARE
            foreign_key record;
            has_reference boolean;
        BEGIN
            FOR foreign_key IN
                SELECT
                    constraint_row.conname,
                    constraint_row.conrelid::regclass AS child_table,
                    source_attribute.attname AS child_column,
                    target_attribute.attname AS target_column,
                    cardinality(constraint_row.conkey) AS source_arity,
                    cardinality(constraint_row.confkey) AS target_arity
                FROM pg_constraint AS constraint_row
                LEFT JOIN pg_attribute AS source_attribute
                  ON source_attribute.attrelid = constraint_row.conrelid
                 AND source_attribute.attnum = constraint_row.conkey[1]
                LEFT JOIN pg_attribute AS target_attribute
                  ON target_attribute.attrelid = constraint_row.confrelid
                 AND target_attribute.attnum = constraint_row.confkey[1]
                WHERE constraint_row.contype = 'f'
                  AND constraint_row.confrelid =
                        to_regclass('public.{safe_target}')
            LOOP
                IF foreign_key.source_arity <> 1
                   OR foreign_key.target_arity <> 1
                   OR foreign_key.target_column <> 'id'
                THEN
                    RAISE EXCEPTION
                        '{safe_message}: unsupported FK %',
                        foreign_key.conname;
                END IF;

                EXECUTE format(
                    'SELECT EXISTS ('
                    'SELECT 1 FROM %s AS child '
                    'WHERE child.%I IN (SELECT id FROM %s)'
                    ')',
                    foreign_key.child_table,
                    foreign_key.child_column,
                    to_regclass('pg_temp.{safe_temp}')
                )
                INTO has_reference;

                IF has_reference THEN
                    RAISE EXCEPTION
                        '{safe_message}: %.%',
                        foreign_key.child_table,
                        foreign_key.child_column;
                END IF;
            END LOOP;
        END
        $$;
        """
    )


def _raise_if(predicate: str, message: str) -> None:
    safe_message = message.replace("'", "''")
    if context.is_offline_mode():
        op.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF {predicate} THEN
                        RAISE EXCEPTION '{safe_message}';
                    END IF;
                END
                $$;
                """
            )
        )
        return
    connection = op.get_bind()
    if bool(connection.execute(sa.text(f"SELECT {predicate}")).scalar()):
        raise RuntimeError(message)


def _sql_string_array(values: tuple[str, ...]) -> str:
    return ",".join("'" + value.replace("'", "''") + "'" for value in values)


def _explicit_short_text_predicate(expression: str) -> str:
    """Match only removed machine vocabulary, never ordinary prose like "short"."""

    tokens = (
        "youtube_shorts",
        "daily_short",
        "long_derived_short",
        "derived_short",
        "short_form",
        "short_final",
        "short_hook",
        "short_candidate",
        "short_render",
        "upload_card",
        "facebook_reels",
        '"reels"',
        "9:16",
    )
    return (
        "("
        + " OR ".join(f"lower({expression}) LIKE '%{token}%'" for token in tokens)
        + ")"
    )
