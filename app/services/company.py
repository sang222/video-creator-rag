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
        status: str = "active",
        default_currency: str = "USD",
    ) -> Company:
        company = Company(name=name, status=status, default_currency=default_currency)
        self.session.add(company)
        self.session.flush()
        return company

    def get_company(self, company_id: uuid.UUID) -> Company | None:
        return self.session.get(Company, company_id)

    def list_companies(self, limit: int = 100) -> list[Company]:
        statement = select(Company).order_by(Company.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement).all())
