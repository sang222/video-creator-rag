from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    core_auth,
    dashboard_ops,
    integration_readiness,
    channel_workspace,
    project_foundation,
    artifact_policy_gates,
    provider_ops,
    production_planning,
    publishing_handoff,
    drive_archive,
    youtube_follow,
    learning_memory,
    provider_execution_safety,
    llm_prompt_ops,
    package_review,
    derivative_media,
    media_provider_workflow,
    originality_review,
    native_renderer,
    asset_acquisition,
)
from app.core.config import get_settings
from app.core.logging import configure_logging


def _include_router_flat(application: FastAPI, router: APIRouter) -> None:
    application.router.routes.extend(router.routes)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.app_name)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    _include_router_flat(application, core_auth.create_router(settings))
    _include_router_flat(application, dashboard_ops.create_router(application))
    _include_router_flat(application, integration_readiness.create_router())
    _include_router_flat(application, channel_workspace.create_router())
    _include_router_flat(application, project_foundation.create_router())
    _include_router_flat(application, artifact_policy_gates.create_router())
    _include_router_flat(application, provider_ops.create_router())
    _include_router_flat(application, production_planning.create_router())
    _include_router_flat(application, publishing_handoff.create_router())
    _include_router_flat(application, drive_archive.create_router())
    _include_router_flat(application, youtube_follow.create_router())
    _include_router_flat(application, learning_memory.create_router())
    _include_router_flat(application, provider_execution_safety.create_router())
    _include_router_flat(application, llm_prompt_ops.create_router())
    _include_router_flat(application, package_review.create_router())
    _include_router_flat(application, derivative_media.create_router())
    _include_router_flat(application, media_provider_workflow.create_router())
    _include_router_flat(application, originality_review.create_router())
    _include_router_flat(application, native_renderer.create_router())
    _include_router_flat(application, asset_acquisition.create_router())
    return application


app = create_app()
