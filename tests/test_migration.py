from __future__ import annotations

import io
import runpy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.services.launch_cadence import LongFormCadenceService
from tests.qualification.conftest import QualificationFactory


ROOT = Path(__file__).resolve().parents[1]
HEAD = "0049_vcos_long_form_cadence"

PHASE_A_D_TABLES = {
    "editorial_research_runs",
    "editorial_idea_candidates",
    "first_channel_launch_policy_versions",
    "launch_runs",
    "long_form_publish_slots",
    "cadence_evaluation_receipts",
}

REMOVED_TABLES = {
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
}

REMOVED_COLUMN_NAMES = {
    "shorts_enabled",
    "shorts_per_week",
    "shorts_per_long_form",
    "shorts_publish_slots",
    "shorts_related_video_required",
    "shorts_monetization_target",
    "shorts_view_target",
    "shorts_revenue_module",
    "shorts_activation_policy",
}

REMOVED_SCHEMA_VALUES = {
    "DAILY_SHORT",
    "LONG_DERIVED_SHORT",
    "YOUTUBE_SHORTS",
    "SHORT_FORM",
    "SHORTS_DISCOVERY",
    "SHORTS_TOFU",
    "DERIVATIVE_BRIDGE_SHORT",
}


def _alembic_config(*, output_buffer: io.StringIO | None = None) -> Config:
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def _current_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text("select version_num from alembic_version")
        ).scalar_one()


def _launch_helpers() -> dict:
    return runpy.run_path(str(ROOT / "tests/test_long_form_launch_cadence.py"))


def test_alembic_has_one_linear_0049_head(engine: Engine) -> None:
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == [HEAD]
    assert _current_revision(engine) == HEAD


def test_active_schema_contains_new_authorities_and_no_removed_tables(
    engine: Engine,
) -> None:
    tables = set(inspect(engine).get_table_names())
    assert PHASE_A_D_TABLES.issubset(tables)
    assert tables.isdisjoint(REMOVED_TABLES)


def test_active_schema_has_no_removed_columns_or_constraint_values(
    engine: Engine,
) -> None:
    with engine.connect() as connection:
        columns = {
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                    """
                )
            )
        }
        definitions = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid)
                    FROM pg_constraint
                    WHERE connamespace = 'public'::regnamespace
                    """
                )
            )
        )
    assert columns.isdisjoint(REMOVED_COLUMN_NAMES)
    assert all(f"'{value}'" not in definitions for value in REMOVED_SCHEMA_VALUES)


def test_0049_to_0046_safe_downgrade_and_immediate_reupgrade(
    engine: Engine,
) -> None:
    config = _alembic_config()
    try:
        command.downgrade(config, "0046_vcos_v2_effect_ledger")
        assert _current_revision(engine) == "0046_vcos_v2_effect_ledger"
        tables = set(inspect(engine).get_table_names())
        assert tables.isdisjoint(PHASE_A_D_TABLES)
    finally:
        command.upgrade(config, "head")

    assert _current_revision(engine) == HEAD
    assert PHASE_A_D_TABLES.issubset(set(inspect(engine).get_table_names()))


def test_0048_guard_refuses_launch_or_post_removal_authority(
    engine: Engine,
    db_session,
) -> None:
    helpers = _launch_helpers()
    scope = QualificationFactory(db_session).channel_scope(
        name="Migration launch guard"
    )
    helpers["_approved_launch_policy"](db_session, scope)
    db_session.commit()

    config = _alembic_config()
    try:
        with pytest.raises(
            RuntimeError,
            match="0048 downgrade refused: launch or post-removal editorial authority exists",
        ):
            command.downgrade(config, "0047_vcos_remove_shorts")
        assert _current_revision(engine) == HEAD
    finally:
        command.upgrade(config, "head")

    assert _current_revision(engine) == HEAD


def test_0049_guard_refuses_cadence_authority(
    engine: Engine,
    db_session,
) -> None:
    helpers = _launch_helpers()
    scope = QualificationFactory(db_session).channel_scope(
        name="Migration cadence guard"
    )
    policy, actor, _ = helpers["_approved_launch_policy"](
        db_session,
        scope,
        timezone_name="UTC",
        weekdays=["TUESDAY", "SATURDAY"],
    )
    run = helpers["_active_launch_run"](
        db_session,
        policy,
        actor,
        started_on=date(2026, 7, 20),
    )
    LongFormCadenceService(
        db_session,
        now=lambda: datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc),
    ).ensure_slots(run.id)
    db_session.commit()

    try:
        with pytest.raises(
            RuntimeError,
            match="0049 downgrade refused: long-form cadence slots or immutable",
        ):
            command.downgrade(_alembic_config(), "0048_vcos_first_channel_launch")
        assert _current_revision(engine) == HEAD
    finally:
        command.upgrade(_alembic_config(), "head")


def test_approved_launch_policy_is_database_immutable(
    engine: Engine,
    db_session,
) -> None:
    helpers = _launch_helpers()
    scope = QualificationFactory(db_session).channel_scope(
        name="Migration immutability guard"
    )
    policy, _, _ = helpers["_approved_launch_policy"](db_session, scope)
    db_session.commit()

    with pytest.raises(DBAPIError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    UPDATE first_channel_launch_policy_versions
                    SET launch_mode = 'MUTATED'
                    WHERE id = :policy_id
                    """
                ),
                {"policy_id": policy.id},
            )


def test_offline_sql_generation_reaches_0049() -> None:
    output = io.StringIO()
    command.upgrade(_alembic_config(output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    assert "0049_vcos_long_form_cadence" in sql
    assert "CREATE TABLE first_channel_launch_policy_versions" in sql
    assert "CREATE TABLE long_form_publish_slots" in sql
    assert "CREATE TABLE cadence_evaluation_receipts" in sql
    assert "source ~ '^LP:[0-9a-f]" in sql


def test_offline_0049_downgrade_restores_legacy_timing_source_check() -> None:
    output = io.StringIO()
    command.downgrade(
        _alembic_config(output_buffer=output),
        "0049_vcos_long_form_cadence:0048_vcos_first_channel_launch",
        sql=True,
    )
    sql = output.getvalue()

    assert "WHERE source LIKE 'LP:%'" in sql
    assert (
        "CHECK (source in "
        "('CHANNEL_CONFIG','HUMAN_OVERRIDE','ANALYTICS_OBSERVED_LATER'))"
    ) in sql
