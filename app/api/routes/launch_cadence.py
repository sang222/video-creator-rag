"""Authenticated launch-policy, runway, and long-form cadence surfaces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.launch_cadence import (
    CadenceEvaluationOutboxRead,
    CadenceEvaluationRead,
    CadenceEvaluationRequest,
    FirstChannelLaunchPolicyCreate,
    FirstChannelLaunchPolicyRead,
    LaunchDashboardRead,
    LaunchPolicyApproval,
    LaunchRunCreate,
    LaunchRunRead,
    LaunchRunTransition,
    LaunchRunwayProjection,
    LongFormPublishSlotRead,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.launch_cadence import (
    CadenceEvaluationReceipt,
    FirstChannelLaunchPolicyVersion,
    LaunchRun,
    LongFormPublishSlot,
)
from app.db.session import session_scope
from app.services.launch_cadence import (
    FirstChannelLaunchPolicyService,
    LaunchDashboardService,
    LaunchRunService,
    LaunchRunwayService,
    LongFormCadenceService,
)
from app.services.company_access import require_company_permission
from app.services.security_boundary import actor_from_request


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/channels/{channel_id}/launch-policies",
        response_model=FirstChannelLaunchPolicyRead,
    )
    def create_launch_policy(
        channel_id: uuid.UUID,
        data: FirstChannelLaunchPolicyCreate,
        request: Request,
    ) -> FirstChannelLaunchPolicyRead:
        try:
            if data.channel_workspace_id != channel_id:
                raise ValidationFailureError("LAUNCH_POLICY_PATH_SCOPE_MISMATCH")
            actor = actor_from_request(request)
            with session_scope() as session:
                record = FirstChannelLaunchPolicyService(session).create(
                    data=data,
                    actor=actor,
                )
                return FirstChannelLaunchPolicyRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/channels/{channel_id}/launch-policy",
        response_model=FirstChannelLaunchPolicyRead,
    )
    def read_active_launch_policy(
        channel_id: uuid.UUID,
        request: Request,
    ) -> FirstChannelLaunchPolicyRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = FirstChannelLaunchPolicyService(session).active_for_channel(
                    channel_id
                )
                if record is None:
                    raise NotFoundError(f"active launch policy not found: {channel_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=record.company_id,
                )
                return FirstChannelLaunchPolicyRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-policies/{policy_version_id}",
        response_model=FirstChannelLaunchPolicyRead,
    )
    def read_launch_policy(
        policy_version_id: uuid.UUID,
        request: Request,
    ) -> FirstChannelLaunchPolicyRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = session.get(FirstChannelLaunchPolicyVersion, policy_version_id)
                if record is None:
                    raise NotFoundError(f"launch policy not found: {policy_version_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=record.company_id,
                )
                return FirstChannelLaunchPolicyRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/launch-policies/{policy_version_id}/approve",
        response_model=FirstChannelLaunchPolicyRead,
    )
    def approve_launch_policy(
        policy_version_id: uuid.UUID,
        data: LaunchPolicyApproval,
        request: Request,
    ) -> FirstChannelLaunchPolicyRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = FirstChannelLaunchPolicyService(session).approve(
                    policy_version_id=policy_version_id,
                    data=data,
                    actor=actor,
                )
                return FirstChannelLaunchPolicyRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/channels/{channel_id}/launch-runs",
        response_model=LaunchRunRead,
    )
    def create_launch_run(
        channel_id: uuid.UUID,
        data: LaunchRunCreate,
        request: Request,
    ) -> LaunchRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                policy = session.get(
                    FirstChannelLaunchPolicyVersion,
                    data.launch_policy_version_id,
                )
                if policy is None or policy.channel_workspace_id != channel_id:
                    raise ValidationFailureError("LAUNCH_RUN_PATH_SCOPE_MISMATCH")
                record = LaunchRunService(session).create(
                    data=data,
                    actor=actor,
                )
                return LaunchRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/channels/{channel_id}/launch-run",
        response_model=LaunchRunRead,
    )
    def read_open_launch_run(
        channel_id: uuid.UUID,
        request: Request,
    ) -> LaunchRunRead:
        """Resolve the channel's single non-terminal launch run for the cockpit."""

        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = session.scalar(
                    select(LaunchRun)
                    .where(
                        LaunchRun.channel_workspace_id == channel_id,
                        LaunchRun.state.in_(
                            ["PREPARING", "READY_TO_LAUNCH", "ACTIVE", "PAUSED"]
                        ),
                    )
                    .order_by(LaunchRun.created_at.desc())
                )
                if record is None:
                    raise NotFoundError(
                        f"open launch run not found for channel: {channel_id}"
                    )
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=record.company_id,
                )
                return LaunchRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-runs/{launch_run_id}",
        response_model=LaunchRunRead,
    )
    def read_launch_run(
        launch_run_id: uuid.UUID,
        request: Request,
    ) -> LaunchRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = session.get(LaunchRun, launch_run_id)
                if record is None:
                    raise NotFoundError(f"launch run not found: {launch_run_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=record.company_id,
                )
                return LaunchRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/launch-runs/{launch_run_id}/transition",
        response_model=LaunchRunRead,
    )
    def transition_launch_run(
        launch_run_id: uuid.UUID,
        data: LaunchRunTransition,
        request: Request,
    ) -> LaunchRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = LaunchRunService(session).transition(
                    launch_run_id=launch_run_id,
                    data=data,
                    actor=actor,
                )
                if record.state == "ACTIVE":
                    LongFormCadenceService(session).ensure_slots(record.id)
                return LaunchRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-runs/{launch_run_id}/runway",
        response_model=LaunchRunwayProjection,
    )
    def read_launch_runway(
        launch_run_id: uuid.UUID,
        request: Request,
    ) -> LaunchRunwayProjection:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                launch_run = session.get(LaunchRun, launch_run_id)
                if launch_run is None:
                    raise NotFoundError(f"launch run not found: {launch_run_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=launch_run.company_id,
                )
                return LaunchRunwayService(session).project(launch_run_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-runs/{launch_run_id}/publish-slots",
        response_model=list[LongFormPublishSlotRead],
    )
    def read_publish_slots(
        launch_run_id: uuid.UUID,
        request: Request,
    ) -> list[LongFormPublishSlotRead]:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                launch_run = session.get(LaunchRun, launch_run_id)
                if launch_run is None:
                    raise NotFoundError(f"launch run not found: {launch_run_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=launch_run.company_id,
                )
                records = list(
                    session.scalars(
                        select(LongFormPublishSlot)
                        .where(
                            LongFormPublishSlot.launch_run_id == launch_run_id,
                            LongFormPublishSlot.intended_publish_at > utc_now(),
                            LongFormPublishSlot.state.in_(["OPEN", "QUALIFICATION_RESERVED", "RESERVED"]),
                        )
                        .order_by(LongFormPublishSlot.intended_publish_at)
                    ).all()
                )
                return [
                    LongFormPublishSlotRead.model_validate(item) for item in records
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/launch-runs/{launch_run_id}/cadence/request",
        response_model=CadenceEvaluationOutboxRead,
    )
    def request_cadence_evaluation(
        launch_run_id: uuid.UUID,
        data: CadenceEvaluationRequest,
        request: Request,
    ) -> CadenceEvaluationOutboxRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                event = LongFormCadenceService(session).request_evaluation(
                    launch_run_id=launch_run_id,
                    data=data,
                    actor=actor,
                )
                return CadenceEvaluationOutboxRead(
                    event_id=event.id,
                    launch_run_id=launch_run_id,
                    command_id=event.command_id or "",
                    evaluation_key=str(event.payload["evaluation_key"]),
                    status=(
                        "DEAD_LETTERED"
                        if event.dead_lettered_at
                        else "DELIVERED"
                        if event.delivered_at
                        else "QUEUED"
                    ),
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-runs/{launch_run_id}/cadence/latest",
        response_model=CadenceEvaluationRead,
    )
    def read_latest_cadence(
        launch_run_id: uuid.UUID,
        request: Request,
    ) -> CadenceEvaluationRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                launch_run = session.get(LaunchRun, launch_run_id)
                if launch_run is None:
                    raise NotFoundError(f"launch run not found: {launch_run_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=launch_run.company_id,
                )
                record = session.scalar(
                    select(CadenceEvaluationReceipt)
                    .where(CadenceEvaluationReceipt.launch_run_id == launch_run_id)
                    .order_by(CadenceEvaluationReceipt.created_at.desc())
                )
                if record is None:
                    raise NotFoundError(
                        f"cadence evaluation not found: {launch_run_id}"
                    )
                return CadenceEvaluationRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/launch-runs/{launch_run_id}/dashboard",
        response_model=LaunchDashboardRead,
    )
    def read_launch_dashboard(
        launch_run_id: uuid.UUID,
        request: Request,
    ) -> LaunchDashboardRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                launch_run = session.get(LaunchRun, launch_run_id)
                if launch_run is None:
                    raise NotFoundError(f"launch run not found: {launch_run_id}")
                require_company_permission(
                    session,
                    actor=actor,
                    permission="production.read",
                    company_id=launch_run.company_id,
                )
                return LaunchDashboardService(session).read(launch_run_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
