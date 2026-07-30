"""Authenticated operator API for one-action durable production."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query, Request, status

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.production_workflow import (
    DeadLetterRetryRead,
    DeadLetterRetryRequest,
    ProductionWorkflowCancel,
    ProductionWorkflowList,
    ProductionWorkflowProjectStart,
    ProductionWorkflowRead,
    ProductionWorkflowResume,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.session import session_scope
from app.services.outbox_dispatcher import DurableOutboxDispatcher
from app.services.production_workflow import ProductionWorkflowCoordinator
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/video-projects/{project_id}/production-workflow/start",
        response_model=ProductionWorkflowRead,
        status_code=status.HTTP_201_CREATED,
    )
    def start_project_production(
        project_id: uuid.UUID,
        request: Request,
        company_id: uuid.UUID = Query(...),
        data: ProductionWorkflowProjectStart | None = None,
    ) -> ProductionWorkflowRead:
        """Start from server-verified v2 project/admission authority."""

        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionWorkflowCoordinator(session).start_from_project(
                    video_project_id=project_id,
                    company_id=company_id,
                    data=data or ProductionWorkflowProjectStart(),
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/production-workflows",
        response_model=ProductionWorkflowList,
    )
    def list_production_workflows(
        request: Request,
        company_id: uuid.UUID = Query(...),
        view: Literal["active", "stuck", "blocked", "all"] = "active",
        limit: int = Query(default=100, ge=1, le=500),
        stale_before: datetime | None = None,
    ) -> ProductionWorkflowList:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionWorkflowCoordinator(session).list(
                    company_id=company_id,
                    actor=actor,
                    view=view,
                    limit=limit,
                    stale_before=stale_before,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/production-workflows/{workflow_run_id}",
        response_model=ProductionWorkflowRead,
    )
    def get_production_workflow(
        workflow_run_id: uuid.UUID,
        request: Request,
        company_id: uuid.UUID = Query(...),
    ) -> ProductionWorkflowRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionWorkflowCoordinator(session).get(
                    workflow_run_id=workflow_run_id,
                    company_id=company_id,
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-workflows/{workflow_run_id}/resume",
        response_model=ProductionWorkflowRead,
    )
    def resume_production_workflow(
        workflow_run_id: uuid.UUID,
        request: Request,
        company_id: uuid.UUID = Query(...),
        data: ProductionWorkflowResume | None = None,
    ) -> ProductionWorkflowRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionWorkflowCoordinator(session).resume(
                    workflow_run_id=workflow_run_id,
                    company_id=company_id,
                    data=data or ProductionWorkflowResume(),
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-workflows/{workflow_run_id}/cancel",
        response_model=ProductionWorkflowRead,
    )
    def cancel_production_workflow(
        workflow_run_id: uuid.UUID,
        data: ProductionWorkflowCancel,
        request: Request,
        company_id: uuid.UUID = Query(...),
    ) -> ProductionWorkflowRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                result, uncertain_events = ProductionWorkflowCoordinator(
                    session
                ).cancel(
                    workflow_run_id=workflow_run_id,
                    company_id=company_id,
                    data=data,
                    actor=actor,
                )
                if uncertain_events:
                    run = session.get(ProductionWorkflowRun, workflow_run_id)
                    assert run is not None
                    DurableOutboxDispatcher(session).record_cancellation_uncertainty(
                        run=run,
                        events=uncertain_events,
                    )
                return result
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-workflows/dead-letters/{dead_letter_job_id}/retry",
        response_model=DeadLetterRetryRead,
    )
    def retry_production_dead_letter(
        dead_letter_job_id: uuid.UUID,
        request: Request,
        company_id: uuid.UUID = Query(...),
        data: DeadLetterRetryRequest | None = None,
    ) -> DeadLetterRetryRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return DurableOutboxDispatcher(session).retry_dead_letter(
                    dead_letter_job_id=dead_letter_job_id,
                    company_id=company_id,
                    data=data or DeadLetterRetryRequest(),
                    actor=actor,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
