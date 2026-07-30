"""Authenticated operator endpoints for safe v2 planning selection and launch."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.operator_planning import (
    DailyShortPlanningLaunchRequest,
    LongFormPlanningLaunchRequest,
    OperatorPlanningCatalogRead,
    OperatorPlanningLaunchRead,
    OperatorPlanningPrepareRead,
    OperatorPlanningPrepareRequest,
    OperatorPlanningStartRequest,
)
from app.core.errors import ValidationFailureError
from app.db.session import session_scope
from app.services.operator_planning import OperatorPlanningService
from app.services.security_boundary import actor_from_request


_SUPPORT_EXTERNAL_FAILURES = {
    "V2_SUPPORT_LLM_PRODUCER_DISABLED": {
        "retry_eligible": False,
        "next_action": "CONFIGURE_LLM_ROUTER",
    },
    "V2_SUPPORT_LLM_PRODUCER_FAILED": {
        "retry_eligible": True,
        "next_action": "RETRY_WHEN_LLM_ROUTER_HEALTHY",
    },
}


def _as_operator_planning_http_error(exc: Exception) -> HTTPException:
    error_code = str(exc)
    resolution = _SUPPORT_EXTERNAL_FAILURES.get(error_code)
    if isinstance(exc, ValidationFailureError) and resolution is not None:
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error_code": error_code,
                "classification": "BLOCK_EXTERNAL_FAILURE",
                "retry_eligible": resolution["retry_eligible"],
                "next_action": resolution["next_action"],
                "workflow_started": False,
                "fallback_used": False,
            },
        )
    return _as_http_error(exc)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/operator-planning/catalog",
        response_model=OperatorPlanningCatalogRead,
    )
    def get_operator_planning_catalog(
        request: Request,
    ) -> OperatorPlanningCatalogRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorPlanningService(session).catalog(actor=actor)
        except Exception as exc:
            raise _as_operator_planning_http_error(exc) from exc

    @router.post(
        "/operator-planning/prepare",
        response_model=OperatorPlanningPrepareRead,
        status_code=status.HTTP_201_CREATED,
    )
    def prepare_operator_planning_source(
        data: OperatorPlanningPrepareRequest,
        request: Request,
    ) -> OperatorPlanningPrepareRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorPlanningService(session).prepare_source(
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_operator_planning_http_error(exc) from exc

    @router.post(
        "/operator-planning/launch",
        response_model=OperatorPlanningLaunchRead,
        status_code=status.HTTP_201_CREATED,
    )
    def prepare_and_launch_operator_planning_source(
        data: OperatorPlanningStartRequest,
        request: Request,
    ) -> OperatorPlanningLaunchRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorPlanningService(session).prepare_and_launch(
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_operator_planning_http_error(exc) from exc

    @router.post(
        "/operator-planning/daily-short/launch",
        response_model=OperatorPlanningLaunchRead,
        status_code=status.HTTP_201_CREATED,
    )
    def launch_daily_short(
        data: DailyShortPlanningLaunchRequest,
        request: Request,
    ) -> OperatorPlanningLaunchRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorPlanningService(session).launch_daily_short(
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_operator_planning_http_error(exc) from exc

    @router.post(
        "/operator-planning/long-form/launch",
        response_model=OperatorPlanningLaunchRead,
        status_code=status.HTTP_201_CREATED,
    )
    def launch_long_form(
        data: LongFormPlanningLaunchRequest,
        request: Request,
    ) -> OperatorPlanningLaunchRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return OperatorPlanningService(session).launch_long_form(
                    data=data,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_operator_planning_http_error(exc) from exc

    return router
