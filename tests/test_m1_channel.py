import uuid

import pytest
from sqlalchemy import select

from app.contracts import ChannelMembershipCreate, ChannelWorkspaceCreate
from app.db.models import Role, User
from app.services import ChannelWorkspaceService, CompanyService, ConfigRegistryService


def test_create_company_works(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Acme")
    db_session.commit()
    assert CompanyService(db_session).get_company(company.id).name == "Acme"


def test_create_channel_works(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Acme")
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )
    db_session.commit()
    assert channel.key == "main"


def test_duplicate_channel_key_in_same_company_fails(db_session) -> None:
    company = CompanyService(db_session).create_company(name="Acme")
    service = ChannelWorkspaceService(db_session)
    service.create_channel(company_id=company.id, data=ChannelWorkspaceCreate(key="main", name="Main"))
    with pytest.raises(Exception):
        service.create_channel(company_id=company.id, data=ChannelWorkspaceCreate(key="main", name="Again"))


def test_same_channel_key_in_different_company_works(db_session) -> None:
    first = CompanyService(db_session).create_company(name="One")
    second = CompanyService(db_session).create_company(name="Two")
    service = ChannelWorkspaceService(db_session)
    a = service.create_channel(company_id=first.id, data=ChannelWorkspaceCreate(key="main", name="Main"))
    b = service.create_channel(company_id=second.id, data=ChannelWorkspaceCreate(key="main", name="Main"))
    assert a.id != b.id


def test_assign_channel_member_works(db_session) -> None:
    ConfigRegistryService(db_session).seed()
    company = CompanyService(db_session).create_company(name="Acme")
    user = User(email="m1@example.com", display_name="M1", status="active")
    db_session.add(user)
    db_session.flush()
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )
    role = db_session.scalars(select(Role).where(Role.key == "operator")).one()
    membership = ChannelWorkspaceService(db_session).assign_member(
        channel_id=channel.id,
        data=ChannelMembershipCreate(user_id=user.id, role_id=role.id),
    )
    assert membership.role_id == role.id


def test_assign_channel_member_and_invalid_assignment(db_session) -> None:
    ConfigRegistryService(db_session).seed()
    company = CompanyService(db_session).create_company(name="Acme")
    user = User(email="m2@example.com", display_name="M2", status="active")
    db_session.add(user)
    db_session.flush()
    role = db_session.query(Role).filter(Role.key == "operator").one()
    service = ChannelWorkspaceService(db_session)
    channel = service.create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )
    membership = service.assign_member(
        channel_id=channel.id,
        data=ChannelMembershipCreate(user_id=user.id, role_id=role.id),
    )
    assert membership.user_id == user.id
    with pytest.raises(Exception):
        service.assign_member(
            channel_id=uuid.uuid4(),
            data=ChannelMembershipCreate(user_id=user.id, role_id=role.id),
        )
    with pytest.raises(Exception):
        service.assign_member(
            channel_id=channel.id,
            data=ChannelMembershipCreate(user_id=uuid.uuid4(), role_id=role.id),
        )
    with pytest.raises(Exception):
        service.assign_member(
            channel_id=channel.id,
            data=ChannelMembershipCreate(user_id=user.id, role_id=uuid.uuid4()),
        )
