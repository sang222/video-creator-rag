from fastapi import APIRouter

from app.api.routes.imports import (
    UploadedVideoYouTubeFollowReadService,
    UploadedVideoYouTubeFollowSummaryRead,
    YouTubeOwnerAnalyticsSnapshotRead,
    YouTubeOwnerAnalyticsSyncRequest,
    YouTubeOwnerAnalyticsSyncRunRead,
    YouTubeOwnerAnalyticsSyncService,
    YouTubePublicMonitorSnapshotRead,
    YouTubePublicStatsSyncService,
    YouTubePublicSyncRunRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
    _youtube_owner_snapshot,
    _youtube_owner_sync_run,
    _youtube_public_snapshot,
    _youtube_public_sync_run,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/uploaded-videos/{uploaded_video_id}/youtube/public-sync", response_model=YouTubePublicSyncRunRead)
    def sync_uploaded_video_youtube_public(uploaded_video_id: uuid.UUID) -> YouTubePublicSyncRunRead:
        try:
            with session_scope() as session:
                run = YouTubePublicStatsSyncService(session).sync_uploaded_video(uploaded_video_id=uploaded_video_id)
                return YouTubePublicSyncRunRead.model_validate(_youtube_public_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/youtube/public-monitor", response_model=YouTubePublicMonitorSnapshotRead | None)
    def get_uploaded_video_youtube_public_monitor(uploaded_video_id: uuid.UUID) -> YouTubePublicMonitorSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = YouTubePublicStatsSyncService(session).latest_snapshot(uploaded_video_id)
                return YouTubePublicMonitorSnapshotRead.model_validate(_youtube_public_snapshot(snapshot)) if snapshot else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/uploaded-videos/{uploaded_video_id}/youtube/owner-analytics-sync", response_model=YouTubeOwnerAnalyticsSyncRunRead)
    def sync_uploaded_video_youtube_owner_analytics(
        uploaded_video_id: uuid.UUID,
        data: YouTubeOwnerAnalyticsSyncRequest | None = None,
    ) -> YouTubeOwnerAnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = YouTubeOwnerAnalyticsSyncService(session).sync_uploaded_video(
                    uploaded_video_id=uploaded_video_id,
                    request=data or YouTubeOwnerAnalyticsSyncRequest(),
                )
                return YouTubeOwnerAnalyticsSyncRunRead.model_validate(_youtube_owner_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/youtube/owner-analytics", response_model=YouTubeOwnerAnalyticsSnapshotRead | None)
    def get_uploaded_video_youtube_owner_analytics(uploaded_video_id: uuid.UUID) -> YouTubeOwnerAnalyticsSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = YouTubeOwnerAnalyticsSyncService(session).latest_snapshot(uploaded_video_id)
                return YouTubeOwnerAnalyticsSnapshotRead.model_validate(_youtube_owner_snapshot(snapshot)) if snapshot else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/youtube/follow-summary", response_model=list[UploadedVideoYouTubeFollowSummaryRead])
    def list_uploaded_video_youtube_follow_summaries() -> list[UploadedVideoYouTubeFollowSummaryRead]:
        try:
            with session_scope() as session:
                return UploadedVideoYouTubeFollowReadService(session).list_summaries()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/youtube/follow-summary", response_model=UploadedVideoYouTubeFollowSummaryRead)
    def get_uploaded_video_youtube_follow_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeFollowSummaryRead:
        try:
            with session_scope() as session:
                return UploadedVideoYouTubeFollowReadService(session).get_summary(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
