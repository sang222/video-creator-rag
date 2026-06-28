"""Tests for company create, list, idempotency, and bootstrap CLI commands."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company, ChannelWorkspace
from app.services.company import CompanyService


class TestCompanyCreate:
    """create_company returns a UUID and persists the company."""

    def test_create_returns_uuid(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        company = svc.create_company(name="Test Co", slug="test-co")
        assert isinstance(company.id, uuid.UUID)
        assert company.name == "Test Co"
        assert company.slug == "test-co"
        db_session.commit()

    def test_create_with_description(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        company = svc.create_company(
            name="Desc Co", slug="desc-co", description="A description"
        )
        assert company.description == "A description"
        db_session.commit()

    def test_create_default_values(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        company = svc.create_company(name="Defaults Co", slug="defaults-co")
        assert company.status == "active"
        assert company.default_currency == "USD"
        assert company.description == ""
        db_session.commit()


class TestCompanyIdempotent:
    """Duplicate slug returns existing company, not error."""

    def test_idempotent_by_slug(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        first = svc.create_company(name="Idem Co", slug="idem-co")
        db_session.commit()
        second = svc.create_company(name="Idem Co Renamed", slug="idem-co")
        assert first.id == second.id
        assert second.name == "Idem Co"  # returns existing, does not update


class TestCompanyList:
    """list_companies includes created companies."""

    def test_list_includes_created(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        svc.create_company(name="List Co 1", slug="list-co-1")
        svc.create_company(name="List Co 2", slug="list-co-2")
        db_session.commit()
        result = svc.list_companies()
        slugs = [c.slug for c in result]
        assert "list-co-1" in slugs
        assert "list-co-2" in slugs

    def test_list_default_limit(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        result = svc.list_companies()
        assert isinstance(result, list)


class TestCompanyGetBySlug:
    """get_company_by_slug returns correct company or None."""

    def test_get_by_slug_found(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        created = svc.create_company(name="Slug Co", slug="slug-co")
        db_session.commit()
        found = svc.get_company_by_slug("slug-co")
        assert found is not None
        assert found.id == created.id

    def test_get_by_slug_not_found(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        found = svc.get_company_by_slug("nonexistent-slug")
        assert found is None


class TestDuplicateSlugReturnsExisting:
    """Slug unique constraint: duplicate slug returns existing row."""

    def test_duplicate_slug_no_error(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        first = svc.create_company(name="First", slug="dup-slug")
        db_session.commit()
        second = svc.create_company(name="Second", slug="dup-slug")
        assert first.id == second.id


class TestNoChannelCreated:
    """Creating a company does not create any channel rows."""

    def test_no_channel_row(self, db_session: Session) -> None:
        svc = CompanyService(db_session)
        svc.create_company(name="No Channel Co", slug="no-channel-co")
        db_session.commit()
        channels = db_session.scalars(select(ChannelWorkspace)).all()
        assert len(channels) == 0
