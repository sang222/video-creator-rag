"""Shared company-scope authorization for authenticated operator services."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actor import ActorContext, ActorType
from app.core.errors import ForbiddenError
from app.db.models.foundation import UserRole
from app.services.rbac import RBACService


def require_company_permission(
    session: Session,
    *,
    actor: ActorContext,
    permission: str,
    company_id: uuid.UUID,
) -> None:
    """Require both the authenticated claim and its persisted company scope."""

    if (
        actor.actor_type != ActorType.HUMAN_USER
        or actor.operator_user_id is None
        or not actor.has_permission(permission)
        or not RBACService(session).user_has_permission(
            user_id=actor.actor_id,
            permission=permission,
            company_id=company_id,
        )
    ):
        raise ForbiddenError(f"PERMISSION_REQUIRED:{permission}")


def accessible_company_ids(
    session: Session,
    *,
    actor: ActorContext,
    permission: str,
) -> set[uuid.UUID] | None:
    """Return ``None`` for a persisted global role, else exact company IDs."""

    if (
        actor.actor_type != ActorType.HUMAN_USER
        or actor.operator_user_id is None
        or not actor.has_permission(permission)
    ):
        raise ForbiddenError(f"PERMISSION_REQUIRED:{permission}")
    rbac = RBACService(session)
    global_permissions = rbac.permissions_for_user(
        user_id=actor.actor_id,
        company_id=None,
    )
    if "*" in global_permissions or permission in global_permissions:
        return None
    assigned_company_ids = set(
        session.scalars(
            select(UserRole.company_id).where(
                UserRole.user_id == actor.actor_id,
                UserRole.company_id.is_not(None),
            )
        ).all()
    )
    return {
        company_id
        for company_id in assigned_company_ids
        if company_id is not None
        and rbac.user_has_permission(
            user_id=actor.actor_id,
            permission=permission,
            company_id=company_id,
        )
    }
