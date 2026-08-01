"""Authenticated Phase E analytics window and launch-observability API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.long_form_analytics import (
    AnalyticsWindowRetryRequest,
    LaunchAnalyticsDashboardRead,
    LongFormAnalyticsWindowRead,
)
from app.core.errors import NotFoundError
from app.db.models import LongFormAnalyticsWindow
from app.db.session import session_scope
from app.services.company_access import require_company_permission
from app.services.long_form_analytics import LongFormAnalyticsScheduler
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get(
        "/uploaded-videos/{uploaded_video_id}/analytics/windows",
        response_model=list[LongFormAnalyticsWindowRead],
    )
    def list_windows(
        uploaded_video_id: uuid.UUID, request: Request
    ) -> list[LongFormAnalyticsWindowRead]:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                windows = LongFormAnalyticsScheduler(session).list_windows(
                    uploaded_video_id
                )
                if not windows:
                    raise NotFoundError("long-form analytics windows not found")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=windows[0].company_id,
                )
                return [
                    LongFormAnalyticsWindowRead.model_validate(window)
                    for window in windows
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/analytics/windows/{window_id}/retry",
        response_model=LongFormAnalyticsWindowRead,
    )
    def retry_window(
        window_id: uuid.UUID, data: AnalyticsWindowRetryRequest, request: Request
    ) -> LongFormAnalyticsWindowRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                window = session.scalar(
                    select(LongFormAnalyticsWindow).where(
                        LongFormAnalyticsWindow.id == window_id
                    )
                )
                if window is None:
                    raise NotFoundError("long-form analytics window not found")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="ops.manage",
                    company_id=window.company_id,
                )
                return LongFormAnalyticsWindowRead.model_validate(
                    LongFormAnalyticsScheduler(session).request_retry(
                        window_id=window_id, reason=data.reason
                    )
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/channels/{channel_workspace_id}/launch-analytics-dashboard",
        response_model=LaunchAnalyticsDashboardRead,
    )
    def launch_dashboard(
        channel_workspace_id: uuid.UUID, request: Request
    ) -> LaunchAnalyticsDashboardRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                windows = list(
                    session.scalars(
                        select(LongFormAnalyticsWindow)
                        .where(
                            LongFormAnalyticsWindow.channel_workspace_id
                            == channel_workspace_id
                        )
                        .limit(1)
                    ).all()
                )
                if not windows:
                    raise NotFoundError("launch analytics dashboard not found")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=windows[0].company_id,
                )
                return LongFormAnalyticsScheduler(session).dashboard(
                    channel_workspace_id
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
