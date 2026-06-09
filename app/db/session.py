from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings


def _engine_kwargs(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


settings = get_settings()
engine = create_engine(settings.database_url, future=True, **_engine_kwargs(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _table_columns(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column_name: str, ddl: str) -> None:
    if column_name not in _table_columns(table_name):
        with engine.begin() as connection:
            connection.execute(text(ddl))


def _ensure_sqlite_compatibility() -> None:
    _add_column_if_missing(
        "cost_events",
        "raw_usage_json",
        "ALTER TABLE cost_events ADD COLUMN raw_usage_json JSON NOT NULL DEFAULT '{}'",
    )
    _add_column_if_missing(
        "memory_items",
        "embedded_at",
        "ALTER TABLE memory_items ADD COLUMN embedded_at DATETIME",
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE workspace_operational_constitutions
                SET active = 0
                WHERE active = 1
                  AND id NOT IN (
                    SELECT id FROM (
                      SELECT id,
                             ROW_NUMBER() OVER (
                               PARTITION BY workspace_id
                               ORDER BY created_at DESC, id DESC
                             ) AS rn
                      FROM workspace_operational_constitutions
                      WHERE active = 1
                    )
                    WHERE rn = 1
                  )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_workspace_active_constitution
                ON workspace_operational_constitutions(workspace_id)
                WHERE active = 1
                """
            )
        )


def _ensure_postgres_compatibility() -> None:
    if settings.enable_pgvector:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    _add_column_if_missing(
        "memory_items",
        "embedded_at",
        "ALTER TABLE memory_items ADD COLUMN embedded_at TIMESTAMPTZ",
    )
    if settings.enable_pgvector and "embedding" not in _table_columns("memory_items"):
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE memory_items ADD COLUMN embedding vector({settings.embedding_dimension})"))

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE workspace_operational_constitutions
                SET active = false
                WHERE active = true
                  AND id NOT IN (
                    SELECT id FROM (
                      SELECT id,
                             ROW_NUMBER() OVER (
                               PARTITION BY workspace_id
                               ORDER BY created_at DESC, id DESC
                             ) AS rn
                      FROM workspace_operational_constitutions
                      WHERE active = true
                    ) ranked
                    WHERE rn = 1
                  )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS ux_workspace_active_constitution
                ON workspace_operational_constitutions(workspace_id)
                WHERE active = true
                """
            )
        )
    if settings.enable_pgvector:
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_hnsw
                        ON memory_items
                        USING hnsw (embedding vector_l2_ops)
                        WHERE embedding IS NOT NULL
                        """
                    )
                )
        except SQLAlchemyError:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_ivfflat
                        ON memory_items
                        USING ivfflat (embedding vector_l2_ops)
                        WITH (lists = 100)
                        WHERE embedding IS NOT NULL
                        """
                    )
                )


def init_db() -> None:
    from app.models import entities  # noqa: F401
    from app.db.base import Base

    backend = engine.url.get_backend_name()
    if backend == "postgresql" and settings.enable_pgvector:
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    Base.metadata.create_all(bind=engine)

    if backend == "sqlite":
        _ensure_sqlite_compatibility()
    elif backend == "postgresql":
        _ensure_postgres_compatibility()
