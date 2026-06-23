import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.contracts import (
    ChannelMembershipCreate,
    ChannelMembershipRead,
    ChannelProfileCompileRequest,
    ChannelProfileCompileResult,
    ChannelProfileVersionCreate,
    ChannelProfileVersionRead,
    ChannelWorkspaceCreate,
    ChannelWorkspaceRead,
)
from app.contracts.policy_snapshot import CompiledChannelPolicySnapshot as SnapshotRead
from app.core.config import get_settings
from app.core.db import check_database
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    PolicySnapshotService,
)


class CompanyCreate(BaseModel):
    name: str
    status: str = "active"
    default_currency: str = "USD"

    model_config = ConfigDict(extra="forbid")


class CompanyRead(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    default_currency: str

    model_config = ConfigDict(extra="forbid")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.app_name)

    @application.get("/health")
    def health() -> dict[str, str]:
        try:
            check_database(settings.database_url)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="database unavailable",
            ) from exc
        return {"status": "ok", "app": settings.app_name, "database": "ok"}

    @application.post("/companies", response_model=CompanyRead)
    def create_company(data: CompanyCreate) -> CompanyRead:
        try:
            with session_scope() as session:
                company = CompanyService(session).create_company(
                    name=data.name,
                    status=data.status,
                    default_currency=data.default_currency,
                )
                return CompanyRead.model_validate(_company(company))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/companies/{company_id}", response_model=CompanyRead)
    def get_company(company_id: uuid.UUID) -> CompanyRead:
        with session_scope() as session:
            company = CompanyService(session).get_company(company_id)
            if company is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="company not found")
            return CompanyRead.model_validate(_company(company))

    @application.post("/companies/{company_id}/channels", response_model=ChannelWorkspaceRead)
    def create_channel(company_id: uuid.UUID, data: ChannelWorkspaceCreate) -> ChannelWorkspaceRead:
        try:
            with session_scope() as session:
                channel = ChannelWorkspaceService(session).create_channel(
                    company_id=company_id,
                    data=data,
                )
                return ChannelWorkspaceRead.model_validate(_channel(channel))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/companies/{company_id}/channels", response_model=list[ChannelWorkspaceRead])
    def list_channels(company_id: uuid.UUID) -> list[ChannelWorkspaceRead]:
        with session_scope() as session:
            channels = ChannelWorkspaceService(session).list_channels(company_id)
            return [ChannelWorkspaceRead.model_validate(_channel(channel)) for channel in channels]

    @application.get("/channels/{channel_id}", response_model=ChannelWorkspaceRead)
    def get_channel(channel_id: uuid.UUID) -> ChannelWorkspaceRead:
        with session_scope() as session:
            channel = ChannelWorkspaceService(session).get_channel(channel_id)
            if channel is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel not found")
            return ChannelWorkspaceRead.model_validate(_channel(channel))

    @application.post("/channels/{channel_id}/memberships", response_model=ChannelMembershipRead)
    def assign_membership(channel_id: uuid.UUID, data: ChannelMembershipCreate) -> ChannelMembershipRead:
        try:
            with session_scope() as session:
                membership = ChannelWorkspaceService(session).assign_member(
                    channel_id=channel_id,
                    data=data,
                )
                return ChannelMembershipRead.model_validate(_membership(membership))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channels/{channel_id}/profile-versions", response_model=ChannelProfileVersionRead)
    def create_profile_version(
        channel_id: uuid.UUID,
        data: ChannelProfileVersionCreate,
    ) -> ChannelProfileVersionRead:
        try:
            with session_scope() as session:
                profile = ChannelProfileService(session).create_profile_version(
                    channel_id=channel_id,
                    data=data,
                )
                return ChannelProfileVersionRead.model_validate(_profile(profile))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}/profile-versions", response_model=list[ChannelProfileVersionRead])
    def list_profile_versions(channel_id: uuid.UUID) -> list[ChannelProfileVersionRead]:
        with session_scope() as session:
            profiles = ChannelProfileService(session).list_profile_versions(channel_id)
            return [ChannelProfileVersionRead.model_validate(_profile(profile)) for profile in profiles]

    @application.post("/profile-versions/{profile_version_id}/compile", response_model=ChannelProfileCompileResult)
    def compile_profile_version(
        profile_version_id: uuid.UUID,
        data: ChannelProfileCompileRequest | None = None,
    ) -> ChannelProfileCompileResult:
        try:
            with session_scope() as session:
                request = data or ChannelProfileCompileRequest()
                return ChannelProfileCompiler(session).compile(
                    profile_version_id=profile_version_id,
                    correlation_id=request.correlation_id or f"api-compile-{profile_version_id}",
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/profile-versions/{profile_version_id}/approve", response_model=ChannelProfileVersionRead)
    def approve_profile_version(
        profile_version_id: uuid.UUID,
        approved_by: uuid.UUID | None = None,
    ) -> ChannelProfileVersionRead:
        try:
            with session_scope() as session:
                profile = ChannelProfileService(session).approve_profile_version(
                    profile_version_id=profile_version_id,
                    approved_by=approved_by,
                )
                return ChannelProfileVersionRead.model_validate(_profile(profile))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-snapshots/{snapshot_id}/activate", response_model=SnapshotRead)
    def activate_policy_snapshot(snapshot_id: uuid.UUID) -> SnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ChannelProfileService(session).activate_snapshot(snapshot_id=snapshot_id)
                return SnapshotRead.model_validate(_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}/active-policy-snapshot", response_model=SnapshotRead | None)
    def get_active_policy_snapshot(channel_id: uuid.UUID) -> SnapshotRead | None:
        with session_scope() as session:
            snapshot = PolicySnapshotService(session).get_active_snapshot_for_channel(channel_id)
            return SnapshotRead.model_validate(_snapshot(snapshot)) if snapshot is not None else None

    return application


app = create_app()


def _company(company: Any) -> dict[str, Any]:
    return {
        "id": company.id,
        "name": company.name,
        "status": company.status,
        "default_currency": company.default_currency,
    }


def _channel(channel: Any) -> dict[str, Any]:
    return {
        "id": channel.id,
        "company_id": channel.company_id,
        "key": channel.key,
        "name": channel.name,
        "status": channel.status,
        "primary_language": channel.primary_language,
        "target_market": channel.target_market,
        "default_timezone": channel.default_timezone,
        "active_policy_snapshot_id": channel.active_policy_snapshot_id,
        "metadata": channel.metadata_,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _membership(membership: Any) -> dict[str, Any]:
    return {
        "id": membership.id,
        "channel_workspace_id": membership.channel_workspace_id,
        "user_id": membership.user_id,
        "role_id": membership.role_id,
        "status": membership.status,
        "created_at": membership.created_at,
    }


def _profile(profile: Any) -> dict[str, Any]:
    return {
        "id": profile.id,
        "channel_workspace_id": profile.channel_workspace_id,
        "version": profile.version,
        "status": profile.status,
        "profile_input": profile.profile_input,
        "profile_input_hash": profile.profile_input_hash,
        "source_template_key": profile.source_template_key,
        "source_template_version": profile.source_template_version,
        "created_by": profile.created_by,
        "approved_by": profile.approved_by,
        "approved_at": profile.approved_at,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def _snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "channel_profile_version_id": snapshot.channel_profile_version_id,
        "compile_run_id": snapshot.compile_run_id,
        "snapshot_version": snapshot.snapshot_version,
        "status": snapshot.status,
        "compiler_version": snapshot.compiler_version,
        "capability_matrix_version": snapshot.capability_matrix_version,
        "compiled_payload": snapshot.compiled_payload,
        "content_hash": snapshot.content_hash,
        "profile_input_hash": snapshot.profile_input_hash,
        "activated_at": snapshot.activated_at,
        "created_at": snapshot.created_at,
    }


def _as_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (NotFoundError, KeyError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ValidationFailureError, ValueError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
