from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, insert, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models.ai_visual import (
    AIVisualAssetEffect,
    AIVisualAssetManifest,
    AIVisualProductionRun,
    AIVisualReplacementLineage,
    AIVisualRerenderAuthority,
    AIVisualScenePlanSnapshot,
    AIVisualStyleBible,
)
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.workflow import VideoProject
from app.services.runtime_migration_guard import (
    REQUIRED_RUNTIME_DB_REVISION,
    is_revision_at_or_after,
)


ROOT = Path(__file__).resolve().parents[1]
HEAD = "0091_youtube_publication_v2"
RUNTIME_MINIMUM = "0079_ai_visual"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


def _alembic_script() -> ScriptDirectory:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _replica_insert(db_session, statement) -> None:
    """Install a structural adversarial fixture without executing parent effects."""

    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        db_session.execute(statement)
    finally:
        db_session.execute(text("SET LOCAL session_replication_role = origin"))


def _prepared_effect_values() -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4(),
        "visual_production_run_id": uuid.uuid4(),
        "scene_plan_snapshot_id": uuid.uuid4(),
        "workflow_run_id": uuid.uuid4(),
        "video_project_id": uuid.uuid4(),
        "asset_slot_id": "asset-slot-1",
        "scene_id": "scene-1",
        "bound_scene_ids": ["scene-1"],
        "bound_scene_plan_hashes": [HASH_A],
        "bound_scene_count": 1,
        "primary_asset_owner_scene_id": "scene-1",
        "ordinal": 1,
        "route": "AI_IMAGE",
        "asset_acquisition_mode": "GENERATED",
        "provider_key": "google_gemini_image",
        "model_id": "gemini-3.1-flash-image",
        "provider_config_version": "vcos.gemini-image.production.v1",
        "provider_config_hash": HASH_A,
        "price_catalog_version": "2026-07-30",
        "price_catalog_ref": "catalog://gemini-image/2026-07-30",
        "price_catalog_hash": HASH_B,
        "production_visual_policy_version": (
            "vcos.production-visual-policy.ai-only.v1"
        ),
        "production_visual_policy_hash": HASH_C,
        "style_bible_ref": "ai-visual-style-bible://style-1",
        "style_bible_hash": HASH_D,
        "scene_plan_ref": "ai-visual-scene-plan://scene-1",
        "scene_plan_hash": HASH_A,
        "compiled_prompt_ref": "ai-visual-prompt://scene-1",
        "compiled_prompt_hash": HASH_E,
        "compiled_prompt_content_hash": HASH_E,
        "prompt_compiler_version": "vcos.ai-image-prompt.v1",
        "prompt_hash": HASH_F,
        "generation_policy": {"attempt_limit": 1},
        "generation_policy_hash": HASH_A,
        "effect_identity_hash": HASH_B,
        "request_hash": HASH_C,
        "idempotency_key": f"ai-image:{uuid.uuid4()}",
        "approval_ref": "approval://ai-visual/1",
        "approval_hash": HASH_D,
        "budget_reservation_id": uuid.uuid4(),
        "budget_authority_ref": "mr1-budget://reservation-1",
        "budget_authority_hash": HASH_E,
        "cost_estimate_ref": "cost-estimate://gemini/1",
        "cost_estimate_hash": HASH_F,
        "estimated_cost_usd": Decimal("0.101000"),
        "maximum_cost_usd": Decimal("0.101000"),
        "state": "PREPARED",
        "revision": 1,
        "maximum_attempts": 1,
        "provider_call_count": 0,
        "request_journal_ref": "journal://ai-image/request/1",
        "request_journal_hash": HASH_A,
        "qc_evidence": {},
        "retry_allowed": False,
        "fallback_allowed": False,
        "created_at": now,
        "updated_at": now,
    }


def _rerender_authority_values() -> dict:
    now = datetime.now(UTC)
    return {
        "id": uuid.uuid4(),
        "authorized_visual_production_run_id": uuid.uuid4(),
        "source_workflow_run_id": uuid.uuid4(),
        "replacement_workflow_run_id": uuid.uuid4(),
        "video_project_id": uuid.uuid4(),
        "production_package_artifact_version_id": uuid.uuid4(),
        "production_package_hash": HASH_A,
        "production_readiness_receipt_artifact_version_id": uuid.uuid4(),
        "production_readiness_receipt_hash": HASH_B,
        "script_artifact_version_id": uuid.uuid4(),
        "script_content_hash": HASH_C,
        "canonical_narration_hash": HASH_D,
        "audio_ref": "var/audio/source.mp3",
        "audio_checksum": HASH_E,
        "audio_duration_ms": 10_000,
        "timed_words_artifact_version_id": uuid.uuid4(),
        "timed_words_hash": HASH_F,
        "caption_artifact_version_id": uuid.uuid4(),
        "caption_hash": HASH_A,
        "caption_checksum": HASH_B,
        "subtitle_qc_artifact_version_id": uuid.uuid4(),
        "subtitle_qc_hash": HASH_C,
        "rejected_final_media_ref_id": uuid.uuid4(),
        "rejected_final_media_hash": HASH_D,
        "rejected_final_review_candidate_id": uuid.uuid4(),
        "rejected_final_review_candidate_hash": HASH_E,
        "rejected_visual_policy": "NATIVE_EXPLANATORY_DIAGRAM",
        "production_visual_policy_version": (
            "vcos.production-visual-policy.ai-only.v1"
        ),
        "production_visual_policy_ref": "catalog://ai-only/v1",
        "production_visual_policy_hash": HASH_F,
        "budget_reservation_id": uuid.uuid4(),
        "budget_reservation_ref": "mr1-budget://rerender",
        "budget_authority_hash": HASH_A,
        "maximum_total_cost_usd": Decimal("1.000000"),
        "maximum_scene_count": 2,
        "maximum_image_submissions": 1,
        "maximum_video_submissions": 1,
        "maximum_tts_submissions": 0,
        "maximum_forced_alignment_submissions": 0,
        "narration_timing_recovery_authority_id": uuid.uuid4(),
        "narration_timing_recovery_authority_hash": HASH_B,
        "narration_timing_recovery_receipt_id": uuid.uuid4(),
        "narration_timing_recovery_receipt_hash": HASH_C,
        "automatic_publish": False,
        "authorized_by_actor_type": "SYSTEM_WORKER",
        "authorized_by_actor_id": uuid.UUID("6d196d74-7938-5c85-bc10-f25466616258"),
        "authorized_by_actor_role": "SYSTEM_WORKER",
        "authority_hash": HASH_D,
        "created_at": now,
    }


def _insert_prepared_effect_fixture(db_session, values: dict) -> None:
    now = datetime.now(UTC)
    company_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    style_id = uuid.uuid4()
    statements = (
        insert(VideoProject).values(
            id=values["video_project_id"],
            company_id=company_id,
            channel_workspace_id=workspace_id,
            policy_snapshot_id=uuid.uuid4(),
            title="AI visual effect adversarial fixture",
            status="draft",
            schema_version="v1",
            created_by_user_id=uuid.uuid4(),
            financial_summary={},
            brand_safety_summary={},
            legal_compliance_summary={},
            audience_delivery_summary={},
            created_at=now,
            updated_at=now,
        ),
        insert(ProductionWorkflowRun).values(
            id=values["workflow_run_id"],
            company_id=company_id,
            channel_workspace_id=workspace_id,
            video_project_id=values["video_project_id"],
            production_lane="LONG_FORM",
            planning_source_type="LONG_FORM_PLAN",
            planning_source_id=uuid.uuid4(),
            planning_source_hash=HASH_A,
            workflow_key=HASH_B,
            start_input_hash=HASH_C,
            state="VISUAL_RUNNING",
            current_stage="VISUAL",
            state_reason_codes=[],
            projection_version=1,
            destination_binding={},
            started_at=now,
            last_progress_at=now,
            metadata_={},
            created_at=now,
            updated_at=now,
        ),
        insert(MR1MonthlyBudgetReservation).values(
            id=values["budget_reservation_id"],
            reservation_ref=values["budget_authority_ref"],
            run_id=values["visual_production_run_id"],
            video_project_id=values["video_project_id"],
            company_id=company_id,
            channel_workspace_id=workspace_id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=30),
            currency="USD",
            reserved_amount=Decimal("1.000000"),
            provider_allocations_json={},
            environment_cap=Decimal("250.000000"),
            company_cap=Decimal("250.000000"),
            channel_cap=Decimal("250.000000"),
            provider_caps_json={},
            status="RESERVED",
            provider_actuals_json={},
            request_hash=HASH_D,
            capacity_evidence_json={"content_hash": values["budget_authority_hash"]},
            created_at=now,
            updated_at=now,
        ),
        insert(AIVisualProductionRun).values(
            id=values["visual_production_run_id"],
            workflow_run_id=values["workflow_run_id"],
            video_project_id=values["video_project_id"],
            execution_kind="NORMAL_PRODUCTION",
            production_package_artifact_version_id=uuid.uuid4(),
            production_package_hash=HASH_A,
            production_visual_policy_version=(
                "vcos.production-visual-policy.ai-only.v1"
            ),
            production_visual_policy_ref="catalog://ai-only/v1",
            production_visual_policy_hash=values["production_visual_policy_hash"],
            source_timeline_ref="timeline://fixture",
            source_timeline_hash=HASH_B,
            audio_ref="audio://fixture",
            audio_checksum=HASH_C,
            audio_duration_ms=10_000,
            timed_words_ref="timed-words://fixture",
            timed_words_hash=HASH_D,
            caption_ref="caption://fixture",
            caption_hash=HASH_E,
            caption_checksum=HASH_F,
            subtitle_qc_ref="subtitle-qc://fixture",
            subtitle_qc_hash=HASH_A,
            budget_reservation_id=values["budget_reservation_id"],
            budget_reservation_ref=values["budget_authority_ref"],
            budget_authority_hash=values["budget_authority_hash"],
            state="AUTHORIZED",
            current_phase="AUTHORIZE",
            projection_version=1,
            started_at=now,
            created_at=now,
            updated_at=now,
        ),
        insert(AIVisualStyleBible).values(
            id=style_id,
            visual_production_run_id=values["visual_production_run_id"],
            schema_version="vcos.video-visual-style-bible.v1",
            content={},
            content_hash=values["style_bible_hash"],
            created_at=now,
        ),
        insert(AIVisualScenePlanSnapshot).values(
            id=values["scene_plan_snapshot_id"],
            visual_production_run_id=values["visual_production_run_id"],
            style_bible_id=style_id,
            style_bible_hash=values["style_bible_hash"],
            schema_version="vcos.ai-visual-scene-plan-set.v1",
            scene_count=1,
            ai_image_scene_count=1,
            ai_video_scene_count=0,
            unique_asset_slot_count=1,
            content={},
            content_hash=values["scene_plan_hash"],
            created_at=now,
        ),
        insert(AIVisualAssetEffect).values(**values),
    )
    db_session.execute(text("SET LOCAL session_replication_role = replica"))
    try:
        for statement in statements:
            db_session.execute(statement)
    finally:
        db_session.execute(text("SET LOCAL session_replication_role = origin"))


def test_runtime_migration_head_is_forward_only(engine) -> None:
    assert _alembic_script().get_heads() == [HEAD]
    with engine.connect() as connection:
        current = connection.scalar(text("select version_num from alembic_version"))
    assert current == HEAD
    assert REQUIRED_RUNTIME_DB_REVISION == RUNTIME_MINIMUM
    assert is_revision_at_or_after(current, minimum_revision=RUNTIME_MINIMUM)
    migration = (
        ROOT
        / "alembic/versions/0079_ai_visual_ai_only_visual_production_authority_and_.py"
    ).read_text(encoding="utf-8")
    assert "0079 AI-only visual production authority is forward-only" in migration


def test_0079_ai_visual_model_column_drift_is_zero(engine) -> None:
    inspector = inspect(engine)
    models = (
        AIVisualRerenderAuthority,
        AIVisualProductionRun,
        AIVisualStyleBible,
        AIVisualScenePlanSnapshot,
        AIVisualAssetEffect,
        AIVisualAssetManifest,
        AIVisualReplacementLineage,
    )
    assert {model.__table__.name for model in models}.issubset(
        set(inspector.get_table_names())
    )
    for model in models:
        database = {
            column["name"]: bool(column["nullable"])
            for column in inspector.get_columns(model.__table__.name)
        }
        metadata = {
            column.name: bool(column.nullable) for column in model.__table__.columns
        }
        assert database == metadata, model.__table__.name

    workflow_ai_columns = {
        "ai_visual_production_run_id",
        "ai_visual_policy_ref",
        "ai_visual_policy_hash",
        "ai_visual_style_bible_ref",
        "ai_visual_style_bible_hash",
        "ai_visual_scene_plan_ref",
        "ai_visual_scene_plan_hash",
        "ai_visual_asset_manifest_ref",
        "ai_visual_asset_manifest_hash",
        "video_motion_grammar_ref",
        "video_motion_grammar_hash",
        "ffmpeg_effect_plan_ref",
        "ffmpeg_effect_plan_hash",
    }
    assert workflow_ai_columns <= {
        column["name"] for column in inspector.get_columns("production_workflow_runs")
    }


def test_0079_create_table_definitions_have_no_duplicate_columns() -> None:
    migration_path = (
        ROOT
        / "alembic/versions/0079_ai_visual_ai_only_visual_production_authority_and_.py"
    )
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    definitions: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_table"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            continue
        table = str(node.args[0].value)
        columns = [
            str(argument.args[0].value)
            for argument in node.args[1:]
            if isinstance(argument, ast.Call)
            and isinstance(argument.func, ast.Attribute)
            and argument.func.attr == "Column"
            and argument.args
            and isinstance(argument.args[0], ast.Constant)
        ]
        definitions[table] = columns

    assert set(definitions) == {
        "ai_visual_rerender_authorities",
        "ai_visual_production_runs",
        "ai_visual_style_bibles",
        "ai_visual_scene_plan_snapshots",
        "ai_visual_asset_effects",
        "ai_visual_asset_manifests",
        "ai_visual_replacement_lineages",
    }
    for table, columns in definitions.items():
        assert len(columns) == len(set(columns)), table


def test_historical_rows_remain_nullable_and_visual_stage_is_authorized(engine) -> None:
    inspector = inspect(engine)
    historical_candidate_columns = {
        column["name"]: column
        for column in inspector.get_columns("final_review_candidates")
        if column["name"]
        in {
            "ai_visual_production_run_id",
            "ai_visual_asset_manifest_hash",
            "ffmpeg_effect_plan_hash",
            "supersedes_final_review_candidate_id",
        }
    }
    assert len(historical_candidate_columns) == 4
    assert all(column["nullable"] for column in historical_candidate_columns.values())

    with engine.connect() as connection:
        definitions = "\n".join(
            connection.scalars(
                text(
                    """
                    select pg_get_constraintdef(oid)
                    from pg_constraint
                    where conrelid::regclass::text in (
                      'production_workflow_runs',
                      'workflow_command_receipts',
                      'workflow_recovery_receipts',
                      'v2_production_effect_ledger'
                    ) and contype='c'
                    """
                )
            )
        )
    assert "VISUAL_PENDING" in definitions
    assert definitions.count("VISUAL") >= 4


def test_asset_effect_database_cas_one_call_and_verified_evidence(db_session) -> None:
    values = _prepared_effect_values()
    effect_id = values["id"]
    _insert_prepared_effect_fixture(db_session, values)

    with pytest.raises(DBAPIError, match="AI_VISUAL_EFFECT_CAS_OR_IDENTITY_VIOLATION"):
        with db_session.begin_nested():
            db_session.execute(
                update(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == effect_id)
                .values(
                    state="SUBMITTING",
                    provider_call_count=1,
                    submitted_at=datetime.now(UTC),
                )
            )

    submitted = datetime.now(UTC)
    db_session.execute(
        update(AIVisualAssetEffect)
        .where(AIVisualAssetEffect.id == effect_id)
        .values(
            state="SUBMITTING",
            revision=2,
            provider_call_count=1,
            submission_owner_token_hash=HASH_B,
            submission_lease_expires_at=submitted + timedelta(minutes=5),
            submitted_at=submitted,
            updated_at=submitted,
        )
    )

    with pytest.raises(DBAPIError, match="AI_VISUAL_EFFECT_CAS_OR_IDENTITY_VIOLATION"):
        with db_session.begin_nested():
            db_session.execute(
                update(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == effect_id)
                .values(model_id="mutated-model", revision=3)
            )

    captured = submitted + timedelta(seconds=1)
    db_session.execute(
        update(AIVisualAssetEffect)
        .where(AIVisualAssetEffect.id == effect_id)
        .values(
            state="RESPONSE_CAPTURED",
            revision=3,
            response_journal_ref="journal://ai-image/response/1",
            response_journal_hash=HASH_C,
            sanitized_response_hash=HASH_D,
            response_captured_at=captured,
            updated_at=captured,
        )
    )

    with pytest.raises(DBAPIError, match="AI_VISUAL_EFFECT_VERIFIED_EVIDENCE_INVALID"):
        with db_session.begin_nested():
            db_session.execute(
                update(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == effect_id)
                .values(state="VERIFIED", revision=4, completed_at=captured)
            )

    completed = captured + timedelta(seconds=1)
    db_session.execute(
        update(AIVisualAssetEffect)
        .where(AIVisualAssetEffect.id == effect_id)
        .values(
            state="VERIFIED",
            revision=4,
            output_ref="var/ai-visual/asset-1.jpg",
            output_checksum=HASH_E,
            output_size_bytes=1024,
            output_content_type="image/jpeg",
            output_width=2048,
            output_height=1152,
            qc_ref="qc://ai-image/asset-1",
            qc_hash=HASH_F,
            actual_cost_usd=None,
            cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_VERIFIED",
            completed_at=completed,
            updated_at=completed,
        )
    )

    with pytest.raises(DBAPIError, match="AI_VISUAL_EFFECT_STATE_TRANSITION_FORBIDDEN"):
        with db_session.begin_nested():
            db_session.execute(
                update(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == effect_id)
                .values(
                    state="FAILED_UNCERTAIN",
                    revision=5,
                    failure_reason_code="ILLEGAL_TERMINAL_REWRITE",
                )
            )

    row = db_session.get(AIVisualAssetEffect, effect_id)
    assert row is not None
    assert row.state == "VERIFIED"
    assert row.revision == 4
    assert row.provider_call_count == 1


def test_uncertain_effect_keeps_unknown_actual_cost_null(db_session) -> None:
    values = _prepared_effect_values()
    effect_id = values["id"]
    _insert_prepared_effect_fixture(db_session, values)
    submitted = datetime.now(UTC)
    db_session.execute(
        update(AIVisualAssetEffect)
        .where(AIVisualAssetEffect.id == effect_id)
        .values(
            state="SUBMITTING",
            revision=2,
            provider_call_count=1,
            submission_owner_token_hash=HASH_B,
            submission_lease_expires_at=submitted + timedelta(minutes=5),
            submitted_at=submitted,
            updated_at=submitted,
        )
    )
    completed = submitted + timedelta(seconds=1)
    db_session.execute(
        update(AIVisualAssetEffect)
        .where(AIVisualAssetEffect.id == effect_id)
        .values(
            state="FAILED_UNCERTAIN",
            revision=3,
            failure_reason_code="PROVIDER_OUTCOME_UNKNOWN",
            failure_evidence_hash=HASH_C,
            actual_cost_usd=None,
            cost_settlement_basis="CONSERVATIVE_CATALOG_ESTIMATE_UNCERTAIN",
            completed_at=completed,
            updated_at=completed,
        )
    )
    row = db_session.get(AIVisualAssetEffect, effect_id)
    assert row is not None
    assert row.state == "FAILED_UNCERTAIN"
    assert row.actual_cost_usd is None


def test_style_scene_manifest_and_replacement_are_database_immutable(
    db_session,
) -> None:
    now = datetime.now(UTC)
    visual_run_id = uuid.uuid4()
    style_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    manifest_id = uuid.uuid4()
    objects = (
        (
            AIVisualStyleBible,
            {
                "id": style_id,
                "visual_production_run_id": visual_run_id,
                "schema_version": "vcos.video-visual-style-bible.v1",
                "content": {},
                "content_hash": HASH_A,
                "created_at": now,
            },
        ),
        (
            AIVisualScenePlanSnapshot,
            {
                "id": scene_id,
                "visual_production_run_id": visual_run_id,
                "style_bible_id": style_id,
                "style_bible_hash": HASH_A,
                "schema_version": "vcos.ai-visual-scene-plan-set.v1",
                "scene_count": 1,
                "ai_image_scene_count": 1,
                "ai_video_scene_count": 0,
                "unique_asset_slot_count": 1,
                "content": {},
                "content_hash": HASH_B,
                "created_at": now,
            },
        ),
        (
            AIVisualAssetManifest,
            {
                "id": manifest_id,
                "visual_production_run_id": visual_run_id,
                "scene_plan_snapshot_id": scene_id,
                "scene_plan_hash": HASH_B,
                "style_bible_hash": HASH_A,
                "motion_grammar_hash": HASH_C,
                "effect_plan_hash": HASH_D,
                "schema_version": "vcos.ai-visual-asset-manifest.v1",
                "scene_count": 1,
                "ai_image_scene_count": 1,
                "ai_video_scene_count": 0,
                "asset_count": 1,
                "ai_image_asset_count": 1,
                "ai_video_asset_count": 0,
                "total_provider_call_count": 1,
                "total_estimated_cost_usd": Decimal("0.101000"),
                "total_actual_or_conservative_cost_usd": Decimal("0.101000"),
                "production_eligible": True,
                "renderer_primary_visual_generation": False,
                "content": {},
                "content_hash": HASH_E,
                "created_at": now,
            },
        ),
        (
            AIVisualReplacementLineage,
            {
                "id": uuid.uuid4(),
                "rerender_authority_id": uuid.uuid4(),
                "visual_production_run_id": visual_run_id,
                "asset_manifest_id": manifest_id,
                "asset_manifest_hash": HASH_E,
                "rejected_final_media_ref_id": uuid.uuid4(),
                "replacement_final_media_ref_id": uuid.uuid4(),
                "rejected_final_review_candidate_id": uuid.uuid4(),
                "replacement_final_review_candidate_id": uuid.uuid4(),
                "replacement_render_checksum": HASH_F,
                "replacement_archive_receipt_hash": HASH_A,
                "automatic_publish": False,
                "lineage_hash": HASH_B,
                "created_at": now,
            },
        ),
    )
    for model, values in objects:
        _replica_insert(db_session, insert(model).values(**values))

    for model, values in objects:
        with pytest.raises(DBAPIError, match="AI_VISUAL_IMMUTABLE_AUTHORITY"):
            with db_session.begin_nested():
                db_session.execute(
                    update(model)
                    .where(model.id == values["id"])
                    .values(created_at=now + timedelta(seconds=1))
                )


def test_gov_rerender_cycle_is_preallocated_authority_then_run() -> None:
    """The cycle is intentional and executable without a deferred missing FK.

    The application preallocates one run UUID, reserves budget against that UUID,
    inserts the immutable authority, then inserts the run which FK-binds it.
    """

    authority_fk_targets = {
        fk.target_fullname
        for fk in AIVisualRerenderAuthority.__table__.c[
            "authorized_visual_production_run_id"
        ].foreign_keys
    }
    run_fk_targets = {
        fk.target_fullname
        for fk in AIVisualProductionRun.__table__.c[
            "rerender_authority_id"
        ].foreign_keys
    }
    assert authority_fk_targets == set()
    assert run_fk_targets == {"ai_visual_rerender_authorities.id"}
    authority_columns = AIVisualRerenderAuthority.__table__.c
    assert "workflow_run_id" not in authority_columns
    assert authority_columns.source_workflow_run_id.nullable is False
    assert authority_columns.replacement_workflow_run_id.nullable is False

    migration = (
        ROOT
        / "alembic/versions/0079_ai_visual_ai_only_visual_production_authority_and_.py"
    ).read_text(encoding="utf-8")
    authority_position = migration.index("def _create_rerender_authorities")
    run_position = migration.index("def _create_visual_production_runs")
    assert authority_position < run_position
    assert (
        "budget.run_id IS DISTINCT FROM NEW.authorized_visual_production_run_id"
        in migration
    )
    assert (
        "authority.authorized_visual_production_run_id IS DISTINCT FROM NEW.id"
        in migration
    )
    assert (
        "authority.replacement_workflow_run_id IS DISTINCT FROM NEW.workflow_run_id"
        in migration
    )
    assert (
        "timing.workflow_run_id IS DISTINCT FROM NEW.source_workflow_run_id"
        in migration
    )
    assert (
        "old_candidate.workflow_run_id IS DISTINCT FROM NEW.source_workflow_run_id"
        in migration
    )


def test_rerender_source_and_replacement_workflow_are_distinct_and_sealed() -> None:
    migration = (
        ROOT
        / "alembic/versions/0079_ai_visual_ai_only_visual_production_authority_and_.py"
    ).read_text(encoding="utf-8")
    required = (
        "source_workflow_run_id <> replacement_workflow_run_id",
        "replacement_workflow.state IS DISTINCT FROM 'VISUAL_PENDING'",
        "replacement_workflow.current_stage IS DISTINCT FROM 'VISUAL'",
        "replacement_workflow.ai_visual_production_run_id IS NOT NULL",
        "replacement_workflow.final_media_ref_id IS NOT NULL",
        "replacement_workflow.final_review_candidate_id IS NOT NULL",
        "replacement_workflow.completed_at IS NOT NULL",
        "authority.source_workflow_run_id IS DISTINCT FROM old_candidate.workflow_run_id",
        "authority.replacement_workflow_run_id IS DISTINCT FROM NEW.workflow_run_id",
        "old_candidate.workflow_run_id IS NOT DISTINCT FROM NEW.workflow_run_id",
    )
    for seal in required:
        assert seal in migration


def test_rerender_authority_rejects_source_workflow_reuse(db_session) -> None:
    values = _rerender_authority_values()
    values["replacement_workflow_run_id"] = values["source_workflow_run_id"]

    with pytest.raises(DBAPIError) as exc_info:
        with db_session.begin_nested():
            db_session.execute(text("SET LOCAL session_replication_role = replica"))
            db_session.execute(insert(AIVisualRerenderAuthority).values(**values))

    assert "ck_ai_visual_rerender" in exc_info.value.orig.diag.constraint_name
    assert db_session.scalar(text("SHOW session_replication_role")) == "origin"


def test_rerender_authority_scene_bound_covers_long_form_plan_without_unbounded_growth(
    db_session,
) -> None:
    values = _rerender_authority_values()
    values.update(
        maximum_total_cost_usd=Decimal("14.021000"),
        maximum_scene_count=51,
        maximum_image_submissions=11,
        maximum_video_submissions=16,
        authority_hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    _replica_insert(db_session, insert(AIVisualRerenderAuthority).values(**values))
    assert (
        db_session.get(AIVisualRerenderAuthority, values["id"]).maximum_scene_count
        == 51
    )

    excessive = _rerender_authority_values()
    excessive.update(
        maximum_total_cost_usd=Decimal("99.000000"),
        maximum_scene_count=257,
        maximum_image_submissions=1,
        maximum_video_submissions=256,
        authority_hash=uuid.uuid4().hex + uuid.uuid4().hex,
    )
    with pytest.raises(DBAPIError) as exc_info:
        with db_session.begin_nested():
            db_session.execute(text("SET LOCAL session_replication_role = replica"))
            db_session.execute(insert(AIVisualRerenderAuthority).values(**excessive))

    assert "ck_ai_visual_rerender" in exc_info.value.orig.diag.constraint_name
    assert db_session.scalar(text("SHOW session_replication_role")) == "origin"


def test_rerender_does_not_fabricate_human_do_not_upload_decision() -> None:
    migration = (
        ROOT
        / "alembic/versions/0079_ai_visual_ai_only_visual_production_authority_and_.py"
    ).read_text(encoding="utf-8")
    assert "final_video_decisions" not in migration
    assert "decision='DO_NOT_UPLOAD'" not in migration
    assert "rejected_visual_policy = 'NATIVE_EXPLANATORY_DIAGRAM'" in migration
    assert "NEW.authorized_by_actor_type IS DISTINCT FROM 'SYSTEM_WORKER'" in migration
    assert "NEW.authorized_by_actor_role IS DISTINCT FROM 'SYSTEM_WORKER'" in migration
    assert (
        "NEW.authorized_by_actor_id IS DISTINCT FROM "
        "'6d196d74-7938-5c85-bc10-f25466616258'::uuid"
    ) in migration
    assert (
        "old_candidate.candidate_hash IS DISTINCT FROM "
        "NEW.rejected_final_review_candidate_hash"
    ) in migration
