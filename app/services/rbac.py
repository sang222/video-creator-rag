import uuid
from pathlib import Path

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.actor import ActorContext
from app.db.models import Role, UserRole
from app.services.config_registry import ConfigRegistryService


OPERATOR_ROLE_TO_ROLE_KEY = {
    "OWNER_ADMIN": "owner_admin",
    "CHANNEL_MANAGER": "channel_manager",
    "PRODUCER": "producer",
    "REVIEWER": "reviewer",
    "PUBLISHER": "publisher",
    "ANALYST": "analyst",
    "PROCUREMENT_OPERATOR": "procurement_operator",
    "COMPLIANCE_REVIEWER": "compliance_reviewer",
    "LEARNING_REVIEWER": "learning_reviewer",
    "READ_ONLY": "read_only_observer",
}


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
        permissions = self.permissions_for_user(user_id=user_id, company_id=company_id)
        return "*" in permissions or permission in permissions

    def permissions_for_user(
        self,
        *,
        user_id: uuid.UUID,
        company_id: uuid.UUID | None = None,
    ) -> set[str]:
        statement = (
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        if company_id is None:
            statement = statement.where(UserRole.company_id.is_(None))
        else:
            statement = statement.where(
                or_(UserRole.company_id == company_id, UserRole.company_id.is_(None))
            )
        assigned_role_keys = set(self.session.scalars(statement).all())
        mapping = self.role_catalog_mapping()
        permissions: set[str] = set()
        for role_key in assigned_role_keys:
            permissions.update(mapping.get(role_key, set()))
        return permissions


def require_permission(permission: str):
    def dependency(request: Request) -> ActorContext:
        actor = getattr(request.state, "actor", None)
        if not isinstance(actor, ActorContext):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            )
        if not actor.has_permission(permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")
        return actor

    return dependency
