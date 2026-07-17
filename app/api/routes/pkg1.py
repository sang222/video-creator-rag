import uuid
from typing import Any

from fastapi import APIRouter

from app.api.routes.serializers_publish_learning import _as_http_error
from app.db.session import session_scope
from app.services.pkg1 import PKG1PackageService


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/video-projects/{project_id}/production-package-readiness")
    def production_package_readiness(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return PKG1PackageService(session).production_package_readiness(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/pkg1")
    def pkg1_package(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return PKG1PackageService(session).read_package(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/provider-execution-plan")
    def provider_execution_plan(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return PKG1PackageService(session).provider_execution_plan(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    return router
