"""Permit truthful final-review-only destination identity.

Revision ID: 0075_review_only_destination
Revises: 0074_verifier_settlement
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "0075_review_only_destination"
down_revision: str | None = "0074_verifier_settlement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Keep publish identity nullable only for an exact review-only candidate."""

    op.alter_column(
        "final_review_candidates",
        "destination_platform_channel_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "final_review_candidates",
        "destination_account_identity",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.create_check_constraint(
        "ck_final_review_candidates_destination_mode",
        "final_review_candidates",
        "((target_market_lineage->>'destination_mode' is null "
        "and destination_platform_channel_id is not null "
        "and destination_account_identity is not null) or ("
        "coalesce(target_market_lineage->>'destination_binding_ref', '') "
        "like 'destination-binding://%' "
        "and coalesce(target_market_lineage->>'destination_model_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and coalesce(target_market_lineage->>'destination_authority_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and coalesce(target_market_lineage->>'destination_binding_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and target_market_lineage->>'destination_binding_hash' "
        "is not distinct from "
        "target_market_lineage->>'destination_authority_hash' "
        "and ((target_market_lineage->>'destination_mode' "
        "is not distinct from "
        "'FINAL_REVIEW_ONLY' "
        "and target_market_lineage->>'destination_status' is not distinct from "
        "'PENDING_PLATFORM_ID' "
        "and target_market_lineage->>'publish_execution_allowed' "
        "is not distinct from 'false' "
        "and target_market_lineage->>'automatic_publish' "
        "is not distinct from 'false' "
        "and coalesce(target_market_lineage->>'destination_handle', '') <> '' "
        "and coalesce("
        "target_market_lineage->>'controlled_recovery_authority_id', '') "
        "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
        "[0-9a-f]{12}$' "
        "and coalesce("
        "target_market_lineage->>'controlled_recovery_authority_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and coalesce("
        "target_market_lineage->>'settlement_authority_id', '') "
        "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
        "[0-9a-f]{12}$' "
        "and coalesce("
        "target_market_lineage->>'settlement_authority_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and coalesce("
        "target_market_lineage->>'settlement_qualification_run_id', '') "
        "~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
        "[0-9a-f]{12}$' "
        "and coalesce("
        "target_market_lineage->>'settlement_provenance_hash', '') "
        "~ '^[0-9a-f]{64}$' "
        "and destination_platform_channel_id is null "
        "and destination_account_identity is null) "
        "or (target_market_lineage->>'destination_mode' is not distinct from "
        "'VERIFIED_PUBLISH_DESTINATION' "
        "and target_market_lineage->>'destination_status' "
        "is not distinct from 'VERIFIED' "
        "and target_market_lineage->>'publish_execution_allowed' "
        "is not distinct from 'true' "
        "and target_market_lineage->>'automatic_publish' "
        "is not distinct from 'false' "
        "and destination_platform_channel_id is not null "
        "and destination_account_identity is not null))))",
    )
    op.execute(
        """
        CREATE FUNCTION block_review_only_upload_decision()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.decision = 'UPLOAD'
               AND (
                    EXISTS (
                        SELECT 1
                        FROM final_review_candidates candidate
                        WHERE candidate.id = NEW.final_review_candidate_id
                          AND candidate.target_market_lineage
                                  ->>'destination_mode'
                                  IS NOT DISTINCT FROM 'FINAL_REVIEW_ONLY'
                    )
                    OR (
                        -- A caller cannot evade the review-only boundary by
                        -- inserting a legacy-shaped candidate on the same
                        -- exact project/media/package/destination lineage.
                        NOT EXISTS (
                            SELECT 1
                            FROM final_review_candidates candidate
                            WHERE candidate.id = NEW.final_review_candidate_id
                              AND candidate.target_market_lineage
                                      ->>'destination_mode'
                                      IS NOT DISTINCT FROM
                                      'VERIFIED_PUBLISH_DESTINATION'
                              AND candidate.target_market_lineage
                                      ->>'publish_execution_allowed'
                                      IS NOT DISTINCT FROM 'true'
                              AND candidate.video_project_id
                                  = NEW.video_project_id
                              AND candidate.final_media_ref_id
                                  = NEW.final_media_ref_id
                              AND candidate.final_media_hash
                                  = NEW.final_media_hash
                              AND candidate.production_package_artifact_version_id
                                  = NEW.production_package_artifact_version_id
                              AND candidate.production_package_hash
                                  = NEW.production_package_hash
                              AND candidate.destination_binding_id
                                  = NEW.destination_binding_id
                              AND candidate.destination_binding_fingerprint
                                  = NEW.destination_binding_fingerprint
                        )
                        AND EXISTS (
                            SELECT 1
                            FROM final_review_candidates candidate
                            LEFT JOIN final_review_candidates supplied
                              ON supplied.id = NEW.final_review_candidate_id
                            WHERE candidate.target_market_lineage
                                      ->>'destination_mode'
                                      IS NOT DISTINCT FROM 'FINAL_REVIEW_ONLY'
                              AND (
                                  candidate.video_project_id
                                      = NEW.video_project_id
                                  OR candidate.final_media_ref_id
                                      = NEW.final_media_ref_id
                                  OR candidate.production_package_artifact_version_id
                                      = NEW.production_package_artifact_version_id
                                  OR candidate.destination_binding_id
                                      = NEW.destination_binding_id
                                  OR (
                                      candidate.video_project_id
                                          = NEW.video_project_id
                                      AND candidate.final_media_hash
                                          = NEW.final_media_hash
                                  )
                                  OR candidate.video_project_id
                                      = supplied.video_project_id
                                  OR candidate.final_media_ref_id
                                      = supplied.final_media_ref_id
                                  OR candidate.production_package_artifact_version_id
                                      = supplied.production_package_artifact_version_id
                                  OR candidate.destination_binding_id
                                      = supplied.destination_binding_id
                                  OR (
                                      candidate.video_project_id
                                          = supplied.video_project_id
                                      AND candidate.final_media_hash
                                          = supplied.final_media_hash
                                  )
                              )
                        )
                    )
               ) THEN
                RAISE EXCEPTION
                    'final-review-only destination cannot authorize upload';
            END IF;
            IF TG_OP = 'UPDATE' AND EXISTS (
                SELECT 1
                FROM final_review_candidates candidate
                WHERE candidate.id = OLD.final_review_candidate_id
                  AND candidate.target_market_lineage->>'destination_mode'
                      IS NOT DISTINCT FROM 'FINAL_REVIEW_ONLY'
            ) THEN
                RAISE EXCEPTION
                    'final-review-only decision lineage is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_block_review_only_upload_decision
        BEFORE INSERT OR UPDATE ON final_video_decisions
        FOR EACH ROW EXECUTE FUNCTION block_review_only_upload_decision();
        """
    )
    op.execute(
        """
        CREATE FUNCTION review_only_publish_lineage(
            p_candidate_id uuid,
            p_decision_id uuid,
            p_task_id uuid,
            p_confirmation_id uuid,
            p_final_media_ref_id uuid,
            p_package_version_id uuid,
            p_destination_binding_id uuid,
            p_video_project_id uuid,
            p_reviewed_checksum text
        )
        RETURNS boolean AS $$
        DECLARE
            exact_verified_candidate boolean;
        BEGIN
            -- A later publish-capable candidate is usable only when the row
            -- carries every exact canonical identity from that VERIFIED
            -- candidate.  Evaluate this before historical-lineage fallbacks.
            SELECT EXISTS (
                SELECT 1
                FROM final_review_candidates candidate
                WHERE candidate.id = p_candidate_id
                  AND candidate.target_market_lineage->>'destination_mode'
                          IS NOT DISTINCT FROM 'VERIFIED_PUBLISH_DESTINATION'
                  AND candidate.target_market_lineage
                          ->>'publish_execution_allowed'
                          IS NOT DISTINCT FROM 'true'
                  AND p_video_project_id IS NOT NULL
                  AND candidate.video_project_id = p_video_project_id
                  AND p_final_media_ref_id IS NOT NULL
                  AND candidate.final_media_ref_id = p_final_media_ref_id
                  AND p_package_version_id IS NOT NULL
                  AND candidate.production_package_artifact_version_id
                      = p_package_version_id
                  AND p_destination_binding_id IS NOT NULL
                  AND candidate.destination_binding_id
                      = p_destination_binding_id
                  AND p_reviewed_checksum IS NOT NULL
                  AND candidate.final_media_hash = p_reviewed_checksum
                  AND (
                      p_decision_id IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM final_video_decisions decision
                          WHERE decision.id = p_decision_id
                            AND decision.final_review_candidate_id = candidate.id
                      )
                  )
                  AND (
                      p_task_id IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM human_upload_tasks task
                          LEFT JOIN final_video_decisions decision
                            ON decision.id = task.final_video_decision_id
                          WHERE task.id = p_task_id
                            AND task.final_review_candidate_id = candidate.id
                            AND (
                                task.final_video_decision_id IS NULL
                                OR decision.final_review_candidate_id = candidate.id
                            )
                      )
                  )
                  AND (
                      p_confirmation_id IS NULL
                      OR EXISTS (
                          SELECT 1
                          FROM manual_publish_confirmations confirmation
                          LEFT JOIN final_video_decisions decision
                            ON decision.id = confirmation.final_video_decision_id
                          LEFT JOIN human_upload_tasks task
                            ON task.id = confirmation.human_upload_task_id
                          WHERE confirmation.id = p_confirmation_id
                            AND confirmation.final_review_candidate_id = candidate.id
                            AND (
                                confirmation.final_video_decision_id IS NULL
                                OR decision.final_review_candidate_id = candidate.id
                            )
                            AND (
                                confirmation.human_upload_task_id IS NULL
                                OR task.final_review_candidate_id = candidate.id
                            )
                      )
                  )
            ) INTO exact_verified_candidate;

            IF exact_verified_candidate THEN
                RETURN false;
            END IF;

            -- Foreign-key lineage is authoritative even when an alternate
            -- writer omits final_review_candidate_id on a legacy-shaped row.
            IF EXISTS (
                SELECT 1
                FROM final_review_candidates candidate
                WHERE candidate.target_market_lineage->>'destination_mode'
                          IS NOT DISTINCT FROM 'FINAL_REVIEW_ONLY'
                  AND (
                      candidate.id = p_candidate_id
                      OR candidate.final_media_ref_id = p_final_media_ref_id
                      OR EXISTS (
                          SELECT 1
                          FROM final_video_decisions decision
                          WHERE decision.id = p_decision_id
                            AND decision.final_review_candidate_id = candidate.id
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM human_upload_tasks task
                          LEFT JOIN final_video_decisions decision
                            ON decision.id = task.final_video_decision_id
                          WHERE task.id = p_task_id
                            AND (
                                task.final_review_candidate_id = candidate.id
                                OR decision.final_review_candidate_id = candidate.id
                            )
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM manual_publish_confirmations confirmation
                          LEFT JOIN final_video_decisions decision
                            ON decision.id = confirmation.final_video_decision_id
                          LEFT JOIN human_upload_tasks task
                            ON task.id = confirmation.human_upload_task_id
                          LEFT JOIN final_video_decisions task_decision
                            ON task_decision.id = task.final_video_decision_id
                          WHERE confirmation.id = p_confirmation_id
                            AND (
                                confirmation.final_review_candidate_id = candidate.id
                                OR decision.final_review_candidate_id = candidate.id
                                OR task.final_review_candidate_id = candidate.id
                                OR task_decision.final_review_candidate_id = candidate.id
                            )
                      )
                      OR EXISTS (
                          SELECT 1
                          FROM final_review_candidates linked
                          WHERE (
                              linked.id = p_candidate_id
                              OR EXISTS (
                                  SELECT 1
                                  FROM final_video_decisions decision
                                  WHERE decision.id = p_decision_id
                                    AND decision.final_review_candidate_id
                                        = linked.id
                              )
                              OR EXISTS (
                                  SELECT 1
                                  FROM human_upload_tasks task
                                  LEFT JOIN final_video_decisions decision
                                    ON decision.id = task.final_video_decision_id
                                  WHERE task.id = p_task_id
                                    AND (
                                        task.final_review_candidate_id = linked.id
                                        OR decision.final_review_candidate_id
                                            = linked.id
                                    )
                              )
                              OR EXISTS (
                                  SELECT 1
                                  FROM manual_publish_confirmations confirmation
                                  LEFT JOIN final_video_decisions decision
                                    ON decision.id
                                       = confirmation.final_video_decision_id
                                  LEFT JOIN human_upload_tasks task
                                    ON task.id
                                       = confirmation.human_upload_task_id
                                  LEFT JOIN final_video_decisions task_decision
                                    ON task_decision.id
                                       = task.final_video_decision_id
                                  WHERE confirmation.id = p_confirmation_id
                                    AND (
                                        confirmation.final_review_candidate_id
                                            = linked.id
                                        OR decision.final_review_candidate_id
                                            = linked.id
                                        OR task.final_review_candidate_id
                                            = linked.id
                                        OR task_decision.final_review_candidate_id
                                            = linked.id
                                    )
                              )
                          )
                            AND (
                                candidate.video_project_id
                                    = linked.video_project_id
                                OR candidate.final_media_ref_id
                                    = linked.final_media_ref_id
                                OR candidate.production_package_artifact_version_id
                                    = linked.production_package_artifact_version_id
                                OR candidate.destination_binding_id
                                    = linked.destination_binding_id
                                OR (
                                    candidate.video_project_id
                                        = linked.video_project_id
                                    AND candidate.final_media_hash
                                        = linked.final_media_hash
                                )
                            )
                      )
                  )
            ) THEN
                RETURN true;
            END IF;

            -- Project/package/destination/checksum bindings close the legacy
            -- NULL-candidate bypass even when no upstream lineage ID is set.
            RETURN EXISTS (
                SELECT 1
                FROM final_review_candidates candidate
                WHERE candidate.target_market_lineage->>'destination_mode'
                          IS NOT DISTINCT FROM 'FINAL_REVIEW_ONLY'
                  AND (
                      candidate.production_package_artifact_version_id
                          = p_package_version_id
                      OR candidate.destination_binding_id
                          = p_destination_binding_id
                      OR candidate.video_project_id = p_video_project_id
                      OR (
                          p_video_project_id IS NOT NULL
                          AND candidate.video_project_id = p_video_project_id
                          AND candidate.final_media_hash = p_reviewed_checksum
                      )
                  )
            );
        END;
        $$ LANGUAGE plpgsql VOLATILE;

        CREATE FUNCTION block_review_only_publish_surface()
        RETURNS trigger AS $$
        BEGIN
            IF review_only_publish_lineage(
                (to_jsonb(NEW)->>'final_review_candidate_id')::uuid,
                (to_jsonb(NEW)->>'final_video_decision_id')::uuid,
                (to_jsonb(NEW)->>'human_upload_task_id')::uuid,
                (to_jsonb(NEW)->>'manual_publish_confirmation_id')::uuid,
                (to_jsonb(NEW)->>'final_media_ref_id')::uuid,
                (to_jsonb(NEW)
                    ->>'production_package_artifact_version_id')::uuid,
                (to_jsonb(NEW)->>'destination_binding_id')::uuid,
                (to_jsonb(NEW)->>'video_project_id')::uuid,
                to_jsonb(NEW)->>'reviewed_checksum'
            ) THEN
                RAISE EXCEPTION
                    'final-review-only destination cannot enter publish surface';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF review_only_publish_lineage(
                    (to_jsonb(OLD)->>'final_review_candidate_id')::uuid,
                    (to_jsonb(OLD)->>'final_video_decision_id')::uuid,
                    (to_jsonb(OLD)->>'human_upload_task_id')::uuid,
                    (to_jsonb(OLD)->>'manual_publish_confirmation_id')::uuid,
                    (to_jsonb(OLD)->>'final_media_ref_id')::uuid,
                    (to_jsonb(OLD)
                        ->>'production_package_artifact_version_id')::uuid,
                    (to_jsonb(OLD)->>'destination_binding_id')::uuid,
                    (to_jsonb(OLD)->>'video_project_id')::uuid,
                    to_jsonb(OLD)->>'reviewed_checksum'
                ) THEN
                    RAISE EXCEPTION
                        'final-review-only publish lineage is immutable';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_block_review_only_human_upload_task
        BEFORE INSERT OR UPDATE ON human_upload_tasks
        FOR EACH ROW EXECUTE FUNCTION block_review_only_publish_surface();

        CREATE TRIGGER trg_block_review_only_manual_confirmation
        BEFORE INSERT OR UPDATE ON manual_publish_confirmations
        FOR EACH ROW EXECUTE FUNCTION block_review_only_publish_surface();

        CREATE TRIGGER trg_block_review_only_uploaded_video
        BEFORE INSERT OR UPDATE ON uploaded_videos
        FOR EACH ROW EXECUTE FUNCTION block_review_only_publish_surface();
        """
    )


def downgrade() -> None:
    raise RuntimeError("0075 is intentionally forward-only in production")
