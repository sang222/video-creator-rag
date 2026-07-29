"""Authenticated API entry points for VCOS typed planning and package v2."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request

from app.api.routes.serializers_publish_learning import _as_http_error
from app.contracts.production_package import (
    ProductionPackageCreateV2,
    ProductionPackageReadV2,
    ProductionPackageRevisionRequestV2,
    ProductionReadinessEvaluationV2,
)
from app.contracts.vcos_v2 import (
    LongFormPlanningRequest,
    ProjectAdmissionV2Read,
    ProjectAdmissionV2Request,
    SeriesPlanCreate,
    SeriesPlanRead,
    SeriesPlanTransitionRequest,
    SeriesRunCreate,
    SeriesRunRead,
    SeriesRunTransitionRequest,
)
from app.db.session import session_scope
from app.services.production_package import (
    ProductionPackageService,
    ProductionReadinessService,
)
from app.services.security_boundary import actor_from_request
from app.services.vcos_v2 import (
    LongFormPlanningService,
    ProjectAdmissionV2Service,
    SeriesPlanService,
    SeriesRunService,
)


def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/series-plans", response_model=SeriesPlanRead)
    def create_series_plan(
        data: SeriesPlanCreate,
        request: Request,
    ) -> SeriesPlanRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = SeriesPlanService(session).create(
                    data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
                return SeriesPlanRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/series-plans/{series_plan_id}/transition",
        response_model=SeriesPlanRead,
    )
    def transition_series_plan(
        series_plan_id: uuid.UUID,
        data: SeriesPlanTransitionRequest,
        request: Request,
    ) -> SeriesPlanRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = SeriesPlanService(session).transition(
                    series_plan_id,
                    data.model_copy(update={"actor_user_id": actor.actor_id}),
                )
                return SeriesPlanRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/series-runs", response_model=SeriesRunRead)
    def create_series_run(
        data: SeriesRunCreate,
        request: Request,
    ) -> SeriesRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = SeriesRunService(session).create(
                    data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
                return SeriesRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/series-runs/{series_run_id}/transition",
        response_model=SeriesRunRead,
    )
    def transition_series_run(
        series_run_id: uuid.UUID,
        data: SeriesRunTransitionRequest,
        request: Request,
    ) -> SeriesRunRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = SeriesRunService(session).transition(
                    series_run_id,
                    data.model_copy(update={"actor_user_id": actor.actor_id}),
                )
                return SeriesRunRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/project-admission-decisions/v2",
        response_model=ProjectAdmissionV2Read,
    )
    def create_v2_admission(
        data: ProjectAdmissionV2Request,
        request: Request,
    ) -> ProjectAdmissionV2Read:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = ProjectAdmissionV2Service(session).create_decision(
                    data=data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
                return ProjectAdmissionV2Read.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/project-admission-decisions/v2/long-form",
        response_model=ProjectAdmissionV2Read,
    )
    def create_v2_long_form_admission(
        data: LongFormPlanningRequest,
        request: Request,
    ) -> ProjectAdmissionV2Read:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                record = LongFormPlanningService(session).admit(
                    data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
                return ProjectAdmissionV2Read.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-packages",
        response_model=ProductionPackageReadV2,
    )
    def create_production_package(
        data: ProductionPackageCreateV2,
        request: Request,
    ) -> ProductionPackageReadV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionPackageService(session).create_package(
                    data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get(
        "/production-packages/{package_artifact_version_id}",
        response_model=ProductionPackageReadV2,
    )
    def read_production_package(
        package_artifact_version_id: uuid.UUID,
    ) -> ProductionPackageReadV2:
        try:
            with session_scope() as session:
                return ProductionPackageService(session).read_package(
                    package_artifact_version_id
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-packages/{package_artifact_version_id}/revisions",
        response_model=ProductionPackageReadV2,
    )
    def revise_production_package(
        package_artifact_version_id: uuid.UUID,
        data: ProductionPackageRevisionRequestV2,
        request: Request,
    ) -> ProductionPackageReadV2:
        try:
            actor = actor_from_request(request)
            if data.package_artifact_version_id != package_artifact_version_id:
                raise ValueError(
                    "package path id must match revision request target"
                )
            with session_scope() as session:
                return ProductionPackageService(session).revise_package(
                    data.model_copy(
                        update={"created_by_user_id": actor.actor_id}
                    )
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post(
        "/production-packages/{package_artifact_version_id}/readiness",
        response_model=ProductionReadinessEvaluationV2,
    )
    def evaluate_production_readiness(
        package_artifact_version_id: uuid.UUID,
        request: Request,
    ) -> ProductionReadinessEvaluationV2:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                return ProductionReadinessService(session).evaluate(
                    package_artifact_version_id=package_artifact_version_id,
                    created_by_user_id=actor.actor_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
