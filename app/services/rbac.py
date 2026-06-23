import uuid
from pathlib import Path

from fastapi import Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Role, UserRole
from app.db.session import get_db
from app.services.config_registry import ConfigRegistryService


class RBACService:
    def __init__(self, session: Session, role_catalog_path: str | Path = "config/role_catalog.yaml"):
        self.session = session
        self.role_catalog_path = Path(role_catalog_path)

    def role_catalog_mapping(self) -> dict[str, set[str]]:
        return ConfigRegistryService(self.session).role_catalog_mapping(self.role_catalog_path)

    def assign_role(
        self,
        *,
        user_id: uuid.UUID,
        role_key: str,
        company_id: uuid.UUID | None = None,
    ) -> UserRole:
        role = self.session.scalars(select(Role).where(Role.key == role_key)).one_or_none()
        if role is None:
            raise KeyError(f"role not found: {role_key}")
        statement = select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role.id,
            UserRole.company_id.is_(None) if company_id is None else UserRole.company_id == company_id,
        )
        assignment = self.session.scalars(statement).one_or_none()
        if assignment is None:
            assignment = UserRole(user_id=user_id, role_id=role.id, company_id=company_id)
            self.session.add(assignment)
            self.session.flush()
        return assignment

    def user_has_role(
        self,
        *,
        user_id: uuid.UUID,
        role_key: str,
        company_id: uuid.UUID | None = None,
    ) -> bool:
        statement = select(UserRole).join(Role, UserRole.role_id == Role.id).where(
            UserRole.user_id == user_id,
            Role.key == role_key,
        )
        if company_id is None:
            statement = statement.where(UserRole.company_id.is_(None))
        else:
            statement = statement.where(
                or_(UserRole.company_id == company_id, UserRole.company_id.is_(None))
            )
        return self.session.scalars(statement.limit(1)).first() is not None

    def user_has_permission(
        self,
        *,
        user_id: uuid.UUID,
        permission: str,
        company_id: uuid.UUID | None = None,
    ) -> bool:
        mapping = self.role_catalog_mapping()
        for role_key, permissions in mapping.items():
            if permission in permissions and self.user_has_role(
                user_id=user_id, role_key=role_key, company_id=company_id
            ):
                return True
        return False


def require_permission(permission: str):
    def dependency(
        user_id: uuid.UUID | None = None,
        db: Session = Depends(get_db),
    ) -> None:
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="missing user_id for M0 RBAC dependency",
            )
        if not RBACService(db).user_has_permission(user_id=user_id, permission=permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")

    return dependency
