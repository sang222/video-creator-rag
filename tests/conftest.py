import os
import sys
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ADMIN_URL = os.getenv(
    "VCOS_TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://vcos:vcos@localhost:55432/postgres",
)
TEST_DB_NAME = f"vcos_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
TEST_DATABASE_URL = make_url(ADMIN_URL).set(database=TEST_DB_NAME).render_as_string(
    hide_password=False
)
os.environ["VCOS_DATABASE_URL"] = TEST_DATABASE_URL


def _admin_conninfo() -> str:
    return make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _wait_for_postgres() -> None:
    deadline = time.time() + 30
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
                connection.execute("select 1")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL unavailable for tests: {last_error}")


def _create_database() -> None:
    _wait_for_postgres()
    with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME)))


def _drop_database() -> None:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
        connection.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
            (TEST_DB_NAME,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEST_DB_NAME)))


def _run_migrations() -> None:
    from app.core.config import get_settings
    from app.core.db import reset_db_caches

    get_settings.cache_clear()
    reset_db_caches()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, "head")


def pytest_sessionstart(session: pytest.Session) -> None:
    _create_database()
    _run_migrations()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _drop_database()


@pytest.fixture(scope="session")
def engine() -> Engine:
    engine = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                TRUNCATE TABLE
                    playbook_candidate_drafts,
                    learning_review_queue_items,
                    learning_promotion_eligibility_runs,
                    learning_evidence_bundles,
                    learning_candidates,
                    learning_candidate_generation_runs,
                    recovery_proposals,
                    failure_trace_reports,
                    policy_rights_diagnostic_runs,
                    engagement_diagnostic_runs,
                    retention_diagnostic_runs,
                    packaging_diagnostic_runs,
                    no_view_diagnostic_runs,
                    post_publish_health_runs,
                    diagnostic_taxonomy_versions,
                    post_publish_observation_windows,
                    uploaded_video_metrics_summaries,
                    engagement_snapshots,
                    retention_curve_snapshots,
                    traffic_source_snapshots,
                    analytics_snapshots,
                    metric_availability_snapshots,
                    metric_definition_versions,
                    analytics_sync_runs,
                    uploaded_video_publication_summaries,
                    uploaded_videos,
                    manual_publish_confirmations,
                    publish_handoff_packages,
                    accessibility_qc_reports,
                    media_qc_reports,
                    render_package_snapshots,
                    media_render_jobs,
                    render_spec_snapshots,
                    source_manifest_snapshots,
                    asset_manifest_snapshots,
                    scene_manifest_snapshots,
                    visual_plan_snapshots,
                    caption_track_snapshots,
                    voice_timeline_snapshots,
                    production_artifact_runs,
                    pronunciation_dictionary_entries,
                    project_admission_decisions,
                    daily_idea_decisions,
                    idea_market_preflights,
                    audience_target_packs,
                    search_intent_maps,
                    search_demand_evidence,
                    channel_state_pack_snapshots,
                    channel_daily_runs,
                    context_pack_snapshots,
                    retrieval_plan_snapshots,
                    editorial_calendar_slots,
                    manual_action_queue,
                    ops_incidents,
                    dead_letter_jobs,
                    provider_attempts,
                    retry_policies,
                    system_health_snapshots,
                    component_health_snapshots,
                    provider_health_snapshots,
                    budget_policies,
                    cost_events,
                    quota_events,
                    quota_accounts,
                    credential_health_snapshots,
                    credential_references,
                    provider_registry_entries,
                    policy_revalidation_batches,
                    policy_source_refs,
                    policy_change_records,
                    gate_runs,
                    platform_policy_catalogs,
                    platform_policy_versions,
                    gate_definition_versions,
                    approval_decisions,
                    revision_requests,
                    review_findings,
                    review_tasks,
                    artifact_versions,
                    artifacts,
                    video_projects,
                    compiled_channel_policy_snapshots,
                    channel_profile_compile_runs,
                    channel_profile_versions,
                    channel_memberships,
                    channel_workspaces,
                    audit_events,
                    domain_events,
                    llm_run_snapshots,
                    config_catalog_versions,
                    user_roles,
                    roles,
                    users,
                    companies
                RESTART IDENTITY CASCADE
                """
            )
        )


@pytest.fixture
def db_session(engine: Engine) -> Session:
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
