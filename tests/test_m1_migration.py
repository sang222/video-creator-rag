import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.db.models import ChannelProfileVersion
from app.services import ChannelProfileService, ChannelWorkspaceService, CompanyService


M1_TABLES = {
    "channel_workspaces",
    "channel_memberships",
    "channel_profile_versions",
    "channel_profile_compile_runs",
    "compiled_channel_policy_snapshots",
}


def test_migration_0002_applies_after_0001(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M1_TABLES.issubset(tables)


def test_m1_tables_exist(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert M1_TABLES <= tables


def test_unique_company_channel_key_works(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Acme")
    service = ChannelWorkspaceService(db_session)
    service.create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )
    with pytest.raises(Exception):
        service.create_channel(
            company_id=company.id,
            data=ChannelWorkspaceCreate(key="main", name="Duplicate"),
        )


def test_unique_channel_profile_version_works(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Acme")
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    duplicate = ChannelProfileVersion(
        channel_workspace_id=channel.id,
        version=profile.version,
        status="draft",
        profile_input=profile.profile_input,
        profile_input_hash=profile.profile_input_hash,
        source_template_key=profile.source_template_key,
        source_template_version=profile.source_template_version,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
