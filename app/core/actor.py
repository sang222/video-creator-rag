from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class ActorType(StrEnum):
    HUMAN_USER = "HUMAN_USER"
    SYSTEM_WORKER = "SYSTEM_WORKER"


@dataclass(frozen=True, slots=True, init=False)
class ActorContext:
    """Trusted actor identity created after authentication, never from request data."""

    actor_type: ActorType
    actor_id: uuid.UUID
    actor_role: str
    operator_user_id: uuid.UUID | None
    permissions: frozenset[str]

    def has_permission(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def authenticated_actor_context(
    *,
    canonical_user_id: uuid.UUID,
    operator_user_id: uuid.UUID,
    actor_role: str,
    permissions: Iterable[str],
) -> ActorContext:
    return _new_actor_context(
        actor_type=ActorType.HUMAN_USER,
        actor_id=canonical_user_id,
        actor_role=actor_role,
        operator_user_id=operator_user_id,
        permissions=permissions,
    )


_SYSTEM_WORKER_IDENTITIES = {
    "vcos-durable-worker": uuid.UUID("95428dc2-b989-5a1c-8f49-8dd64e99f00e"),
    # A distinct durable identity keeps explicit operator-requested recovery
    # receipts truthful without fabricating a human User row.
    "vcos-controlled-recovery": uuid.UUID(
        "6d196d74-7938-5c85-bc10-f25466616258"
    ),
}


def _system_worker_actor(
    worker_key: str,
    *,
    permissions: Iterable[str],
) -> ActorContext:
    """Internal-only factory. Public request contracts must never call this."""

    try:
        actor_id = _SYSTEM_WORKER_IDENTITIES[worker_key]
    except KeyError as exc:
        raise ValueError("untrusted system worker identity") from exc
    return _new_actor_context(
        actor_type=ActorType.SYSTEM_WORKER,
        actor_id=actor_id,
        actor_role="SYSTEM_WORKER",
        operator_user_id=None,
        permissions=permissions,
    )


def _new_actor_context(
    *,
    actor_type: ActorType,
    actor_id: uuid.UUID,
    actor_role: str,
    operator_user_id: uuid.UUID | None,
    permissions: Iterable[str],
) -> ActorContext:
    context = object.__new__(ActorContext)
    object.__setattr__(context, "actor_type", actor_type)
    object.__setattr__(context, "actor_id", actor_id)
    object.__setattr__(context, "actor_role", actor_role)
    object.__setattr__(context, "operator_user_id", operator_user_id)
    object.__setattr__(context, "permissions", frozenset(permissions))
    return context
