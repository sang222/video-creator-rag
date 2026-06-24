from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


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
}


def test_alembic_migration_applies_on_empty_postgres(engine: Engine) -> None:
    with engine.connect() as connection:
        revision = connection.execute(text("select version_num from alembic_version")).scalar_one()
    assert revision == "0003_m2_workflow"


def test_core_tables_exist_after_migration(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert REQUIRED_TABLES.issubset(tables)
