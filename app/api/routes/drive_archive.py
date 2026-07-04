from fastapi import APIRouter

from app.api.routes.imports import (
    CloudMediaReadPayload,
    GoogleDriveConnectionStatusRead,
    GoogleDriveCredentialHealthService,
    GoogleDriveOAuthSessionRead,
    GoogleDriveOAuthSessionService,
    LocalCleanupRunRequest,
    LocalCleanupRunResult,
    LocalMediaCleanupService,
    LocalMediaRetentionPolicyRead,
    LocalMediaRetentionPolicyService,
    MediaCloudReadService,
    MediaOffloadExecuteRequest,
    MediaOffloadJobCreate,
    MediaOffloadJobRead,
    MediaOffloadJobService,
    RedirectResponse,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
    _google_drive_oauth_session,
    _local_media_retention_policy,
    _media_offload_job,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/auth/google-drive/start")
    def start_google_drive_auth(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> RedirectResponse:
        try:
            with session_scope() as session:
                result = GoogleDriveOAuthSessionService(session).start(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
                return RedirectResponse(result.authorization_url)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/auth/google-drive/callback", response_model=GoogleDriveOAuthSessionRead)
    def google_drive_auth_callback(
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> GoogleDriveOAuthSessionRead:
        try:
            with session_scope() as session:
                oauth_session = GoogleDriveOAuthSessionService(session).handle_callback(state=state, code=code, error=error)
                return GoogleDriveOAuthSessionRead.model_validate(_google_drive_oauth_session(oauth_session))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/google-drive/connection-status", response_model=GoogleDriveConnectionStatusRead)
    def get_google_drive_connection_status() -> GoogleDriveConnectionStatusRead:
        try:
            with session_scope() as session:
                return GoogleDriveCredentialHealthService(session).connection_status()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/media/offload-jobs", response_model=MediaOffloadJobRead)
    def create_media_offload_job(data: MediaOffloadJobCreate) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).create_job(data=data)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/media/offload-jobs/{job_id}/execute", response_model=MediaOffloadJobRead)
    def execute_media_offload_job(job_id: uuid.UUID, data: MediaOffloadExecuteRequest | None = None) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).execute_job(job_id=job_id, data=data)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/media/offload-jobs/{job_id}", response_model=MediaOffloadJobRead)
    def get_media_offload_job(job_id: uuid.UUID) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).require(job_id)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/media/cloud-refs/{cloud_media_ref_id}", response_model=CloudMediaReadPayload)
    def get_cloud_media_ref(cloud_media_ref_id: uuid.UUID) -> CloudMediaReadPayload:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).dashboard_payload(cloud_media_ref_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{video_project_id}/media", response_model=list[CloudMediaReadPayload])
    def list_video_project_media(video_project_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_video_project(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/render-packages/{render_package_id}/media", response_model=list[CloudMediaReadPayload])
    def list_render_package_media(render_package_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_render_package(render_package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/media", response_model=list[CloudMediaReadPayload])
    def list_uploaded_video_media(uploaded_video_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_uploaded_video(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/media/local-cleanup/run", response_model=LocalCleanupRunResult)
    def run_local_media_cleanup(data: LocalCleanupRunRequest | None = None) -> LocalCleanupRunResult:
        try:
            with session_scope() as session:
                request = data or LocalCleanupRunRequest()
                return LocalMediaCleanupService(session).run_pending_cleanup(dry_run=request.dry_run)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/media/local-retention-policy", response_model=LocalMediaRetentionPolicyRead)
    def get_local_media_retention_policy() -> LocalMediaRetentionPolicyRead:
        try:
            with session_scope() as session:
                policy = LocalMediaRetentionPolicyService(session).get_or_create_default()
                return LocalMediaRetentionPolicyRead.model_validate(_local_media_retention_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
