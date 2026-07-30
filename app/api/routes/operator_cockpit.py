"""Authenticated read endpoints for the Phase 6 production cockpit."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.operator_cockpit import ProductionCockpitRead
from app.db.session import session_scope
from app.services.operator_cockpit import OperatorCockpitService
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/operator-cockpit", response_model=ProductionCockpitRead)
    def get_operator_cockpit(
        request: Request,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> ProductionCockpitRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorCockpitService(session).build(
                    actor=actor,
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/video-projects/{project_id}/operator-cockpit",
        response_model=ProductionCockpitRead,
    )
    def get_project_operator_cockpit(
        project_id: uuid.UUID,
        request: Request,
    ) -> ProductionCockpitRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorCockpitService(session).build(
                    actor=actor,
                    project_id=project_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/channels/{channel_workspace_id}/operator-cockpit",
        response_model=ProductionCockpitRead,
    )
    def get_channel_operator_cockpit(
        channel_workspace_id: uuid.UUID,
        request: Request,
    ) -> ProductionCockpitRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorCockpitService(session).build(
                    actor=actor, channel_workspace_id=channel_workspace_id
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
