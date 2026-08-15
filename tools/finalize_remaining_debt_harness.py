from __future__ import annotations

from pathlib import Path
import re


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def restore_generic_db_harness() -> None:
    path = Path("tests/conftest.py")
    path.write_text(
        '''from __future__ import annotations

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
BASE_DATABASE_URL = os.getenv(
    "VCOS_TEST_ADMIN_DATABASE_URL"
) or os.getenv(
    "VCOS_DATABASE_URL",
    "postgresql+psycopg://vcos:vcos@localhost:55432/postgres",
)
BASE_URL = make_url(BASE_DATABASE_URL)
ADMIN_URL = BASE_URL.set(database="postgres")
TEST_DB_NAME = f"vcos_test_{os.getpid()}_{uuid.uuid4().hex[:8]}"
TEST_DATABASE_URL = ADMIN_URL.set(database=TEST_DB_NAME).render_as_string(
    hide_password=False
)
os.environ["VCOS_DATABASE_URL"] = TEST_DATABASE_URL


def _admin_conninfo() -> str:
    return ADMIN_URL.set(drivername="postgresql").render_as_string(
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
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DB_NAME))
        )


def _drop_database() -> None:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
        connection.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
            (TEST_DB_NAME,),
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(TEST_DB_NAME))
        )


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
    """Reset application tables between tests without channel-specific seed state."""

    with engine.begin() as connection:
        rows = (
            connection.execute(
                text(
                    """
                    select tablename
                    from pg_tables
                    where schemaname = 'public'
                      and tablename <> 'alembic_version'
                    order by tablename
                    """
                )
            )
            .scalars()
            .all()
        )
        if rows:
            quoted = ", ".join(
                '"' + name.replace('"', '""') + '"' for name in rows
            )
            connection.execute(
                text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
            )


@pytest.fixture
def db_session(engine: Engine) -> Session:
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
''',
        encoding="utf-8",
    )


def genericize_qualification_factory() -> None:
    path = Path("tests/qualification/conftest.py")
    replace_once(
        path,
        '''    def channel_scope(
        self,
        *,
        name: str = "Qualification",
        strict_long_form: bool = False,
    ) -> SimpleNamespace:
''',
        '''    def channel_scope(
        self,
        *,
        name: str = "Qualification",
        strict_long_form: bool = False,
        template_key: str | None = None,
    ) -> SimpleNamespace:
''',
        label="qualification factory signature",
    )
    replace_once(
        path,
        '''        profile = ChannelProfileService(self.session).create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(
                template_key="saas_digital_leverage",
                created_by=admin.id,
            ),
        )
''',
        '''        if template_key is None:
            compiler_policy = ConfigRegistryService(self.session).validate_catalog(
                ROOT / "config/profile_compiler_policy.yaml"
            )
            allowed = list(
                compiler_policy.content["items"][0]["allowed_template_keys"]
            )
            if not allowed:
                raise RuntimeError("profile compiler exposes no allowed test template")
            template_key = sorted(str(item) for item in allowed)[0]
        profile = ChannelProfileService(self.session).create_profile_version(
            channel_id=channel.id,
            data=ChannelProfileVersionCreate(
                template_key=template_key,
                created_by=admin.id,
            ),
        )
''',
        label="qualification factory template selection",
    )


def repair_series_extension_transition() -> None:
    path = Path("app/services/remaining_debt_closeout.py")
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'(def extend_fixed_series\(.*?new_arc = SeriesArcVersion\(.*?'
        r'planned_episode_count=new_planned_episode_count,\n\s*)state="ACTIVE",',
        re.S,
    )
    text, count = pattern.subn(r'\1state="DRAFT",', text, count=1)
    if count != 1:
        raise SystemExit(
            f"series extension draft transition: expected one match, found {count}"
        )
    old = '''        arc.state = "SUPERSEDED"
        self._decision(
'''
    new = '''        arc.state = "SUPERSEDED"
        self.session.flush()
        new_arc.state = "ACTIVE"
        self._decision(
'''
    if text.count(old) != 1:
        raise SystemExit(
            "series extension activation transition: expected exactly one match"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def repair_audience_delivery_state() -> None:
    path = Path("app/services/remaining_debt_closeout.py")
    replace_once(
        path,
        '''            state="ACTIVE",
            plan_hash=digest,
            activated_at=utc_now(),
''',
        '''            state="ACTIVATED",
            plan_hash=digest,
            activated_at=utc_now(),
''',
        label="audience delivery activated state",
    )


def update_superseded_migration_expectation() -> None:
    path = Path("tests/test_voice_authority.py")
    replace_once(
        path,
        '    assert heads == ["0084_youtube_private_delivery"]\n',
        '    assert heads == ["0087_business_os"]\n',
        label="voice migration head expectation",
    )


def add_one_engine_many_profiles_proof() -> None:
    path = Path("tests/test_one_engine_many_profiles.py")
    path.write_text(
        '''from __future__ import annotations

from tests.qualification.conftest import QualificationFactory


def test_same_engine_compiles_two_isolated_channel_profiles(db_session) -> None:
    factory = QualificationFactory(db_session)
    channel_a = factory.channel_scope(name="Channel A")
    channel_b = factory.channel_scope(name="Channel B")

    assert channel_a.channel.id != channel_b.channel.id
    assert channel_a.profile.id != channel_b.profile.id
    assert channel_a.snapshot.id != channel_b.snapshot.id
    assert channel_a.profile.channel_workspace_id == channel_a.channel.id
    assert channel_b.profile.channel_workspace_id == channel_b.channel.id
    assert channel_a.snapshot.channel_profile_version_id == channel_a.profile.id
    assert channel_b.snapshot.channel_profile_version_id == channel_b.profile.id
    assert channel_a.channel.active_policy_snapshot_id == channel_a.snapshot.id
    assert channel_b.channel.active_policy_snapshot_id == channel_b.snapshot.id
''',
        encoding="utf-8",
    )


def harden_permanent_regression_workflow() -> None:
    path = Path(".github/workflows/remaining-debt-closeout.yml")
    text = path.read_text(encoding="utf-8")
    anchor = '''          pytest -q "${files[@]}"
      - name: Architecture audit
'''
    replacement = '''          pytest -q "${files[@]}"
      - name: Full repository regression
        run: pytest -q
      - name: Architecture audit
'''
    if "Full repository regression" not in text:
        if anchor not in text:
            raise SystemExit("remaining debt full regression anchor not found")
        text = text.replace(anchor, replacement, 1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    restore_generic_db_harness()
    genericize_qualification_factory()
    repair_series_extension_transition()
    repair_audience_delivery_state()
    update_superseded_migration_expectation()
    add_one_engine_many_profiles_proof()
    harden_permanent_regression_workflow()


if __name__ == "__main__":
    main()
