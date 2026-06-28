import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Company


class CompanyService:
    def __init__(self, session: Session):
        self.session = session

    def create_company(
        self,
        *,
        name: str,
        slug: str | None = None,
        description: str = "",
        status: str = "active",
        default_currency: str = "USD",
    ) -> Company:
        slug = slug or _slug_from_name(name)
        existing = self.get_company_by_slug(slug)
        if existing is not None:
            return existing
        company = Company(
            name=name,
            slug=slug,
            description=description,
            status=status,
            default_currency=default_currency,
        )
        self.session.add(company)
        self.session.flush()
        return company

    def get_company(self, company_id: uuid.UUID) -> Company | None:
        return self.session.get(Company, company_id)

    def get_company_by_slug(self, slug: str) -> Company | None:
        statement = select(Company).where(Company.slug == slug)
        return self.session.scalars(statement).first()

    def list_companies(self, limit: int = 100) -> list[Company]:
        statement = select(Company).order_by(Company.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement).all())


def _slug_from_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "company"
