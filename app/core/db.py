from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings


@lru_cache(maxsize=8)
def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    return create_engine(url, future=True, pool_pre_ping=True)


@lru_cache(maxsize=8)
def get_session_factory(database_url: str | None = None) -> sessionmaker:
    return sessionmaker(
        bind=get_engine(database_url),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def check_database(database_url: str | None = None) -> bool:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        connection.execute(text("select 1"))
    return True


def reset_db_caches() -> None:
    get_engine.cache_clear()
    get_session_factory.cache_clear()
