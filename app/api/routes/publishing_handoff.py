from fastapi import APIRouter

from app.api.routes.imports import (
    AnalyticsSnapshotRead,
    AnalyticsSyncRunCreate,
    AnalyticsSyncRunExecuteRequest,
    AnalyticsSyncRunRead,
    AnalyticsSyncService,
    LocalizedMetadataPackageRead,
    LocalizedMetadataPackageService,
    LocalizedSubtitlePackageRead,
    LocalizedSubtitlePackageService,
    M11DashboardService,
    ManualAnalyticsImportContract,
    ManualPublishConfirmationCreate,
    ManualPublishConfirmationRead,
    ManualPublishConfirmationService,
    NotFoundError,
    PublishHandoffCreate,
    PublishHandoffLedgerService,
    PublishHandoffRead,
    PublishHandoffService,
    PublishTimingSuggestionRead,
    PublishTimingSuggestionService,
    RedirectResponse,
    RetentionCurveSnapshotRead,
    TrafficSourceSnapshotRead,
    UploadedVideoDashboardRead,
    UploadedVideoListItem,
    UploadedVideoMetricsSummaryRead,
    UploadedVideoPublicationSummaryRead,
    UploadedVideoRead,
    UploadedVideoVerificationResult,
    YouTubeConnectionStatusRead,
    YouTubeCredentialHealthService,
    YouTubeOAuthSessionRead,
    YouTubeOAuthSessionService,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _analytics_snapshot,
    _analytics_sync_run,
    _as_http_error,
    _manual_publish_confirmation,
    _publish_handoff,
    _retention_curve_snapshot,
    _traffic_source_snapshot,
    _uploaded_video,
    _uploaded_video_metrics_summary,
    _uploaded_video_summary,
    _youtube_oauth_session,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/publish-handoffs", response_model=PublishHandoffRead)
    def create_publish_handoff(data: PublishHandoffCreate) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).create_from_render_package(data=data)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/publish-handoffs/{handoff_id}", response_model=PublishHandoffRead)
    def get_publish_handoff(handoff_id: uuid.UUID) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).require(handoff_id)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/publish-handoffs/{handoff_id}/publish-timing-suggestion", response_model=PublishTimingSuggestionRead)
    def create_publish_timing_suggestion(handoff_id: uuid.UUID) -> PublishTimingSuggestionRead:
        try:
            with session_scope() as session:
                return PublishTimingSuggestionService(session).create_for_handoff(handoff_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/publish-handoffs/{handoff_id}/mark-ready", response_model=PublishHandoffRead)
    def mark_publish_handoff_ready(handoff_id: uuid.UUID) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).mark_ready(handoff_id=handoff_id)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/localized-subtitle-packages/{package_id}", response_model=LocalizedSubtitlePackageRead)
    def get_localized_subtitle_package(package_id: uuid.UUID) -> LocalizedSubtitlePackageRead:
        try:
            with session_scope() as session:
                return LocalizedSubtitlePackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/localized-metadata-packages/{package_id}", response_model=LocalizedMetadataPackageRead)
    def get_localized_metadata_package(package_id: uuid.UUID) -> LocalizedMetadataPackageRead:
        try:
            with session_scope() as session:
                return LocalizedMetadataPackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/publish-timing-suggestions/{suggestion_id}", response_model=PublishTimingSuggestionRead)
    def get_publish_timing_suggestion(suggestion_id: uuid.UUID) -> PublishTimingSuggestionRead:
        try:
            with session_scope() as session:
                return PublishTimingSuggestionService(session).get(suggestion_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/manual-publish-confirmations", response_model=ManualPublishConfirmationRead)
    def create_manual_publish_confirmation(data: ManualPublishConfirmationCreate) -> ManualPublishConfirmationRead:
        try:
            with session_scope() as session:
                confirmation = ManualPublishConfirmationService(session).create_confirmation(data=data)
                return ManualPublishConfirmationRead.model_validate(_manual_publish_confirmation(confirmation))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/manual-publish-confirmations/{confirmation_id}", response_model=ManualPublishConfirmationRead)
    def get_manual_publish_confirmation(confirmation_id: uuid.UUID) -> ManualPublishConfirmationRead:
        try:
            with session_scope() as session:
                confirmation = ManualPublishConfirmationService(session).require_confirmation(confirmation_id)
                return ManualPublishConfirmationRead.model_validate(_manual_publish_confirmation(confirmation))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/manual-publish-confirmations/{confirmation_id}/accept", response_model=UploadedVideoRead)
    def accept_manual_publish_confirmation(confirmation_id: uuid.UUID) -> UploadedVideoRead:
        try:
            with session_scope() as session:
                uploaded = ManualPublishConfirmationService(session).accept_confirmation(confirmation_id=confirmation_id)
                return UploadedVideoRead.model_validate(_uploaded_video(uploaded))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos", response_model=list[UploadedVideoListItem])
    def list_uploaded_videos_dashboard(
        channel_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
    ) -> list[UploadedVideoListItem]:
        try:
            with session_scope() as session:
                return M11DashboardService(session).list_uploaded_videos(channel_id=channel_id, company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/dashboard", response_model=UploadedVideoDashboardRead)
    def get_uploaded_video_dashboard(uploaded_video_id: uuid.UUID) -> UploadedVideoDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).uploaded_video_dashboard(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/uploaded-videos/{uploaded_video_id}/verify", response_model=UploadedVideoVerificationResult)
    def verify_uploaded_video(uploaded_video_id: uuid.UUID) -> UploadedVideoVerificationResult:
        try:
            with session_scope() as session:
                return PublishHandoffLedgerService(session).verify_uploaded_video(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}", response_model=UploadedVideoRead)
    def get_uploaded_video(uploaded_video_id: uuid.UUID) -> UploadedVideoRead:
        try:
            with session_scope() as session:
                uploaded = ManualPublishConfirmationService(session).get_uploaded_video(uploaded_video_id)
                if uploaded is None:
                    raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
                return UploadedVideoRead.model_validate(_uploaded_video(uploaded))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/uploaded-videos", response_model=list[UploadedVideoRead])
    def list_project_uploaded_videos(project_id: uuid.UUID) -> list[UploadedVideoRead]:
        try:
            with session_scope() as session:
                return [
                    UploadedVideoRead.model_validate(_uploaded_video(uploaded))
                    for uploaded in ManualPublishConfirmationService(session).list_uploaded_videos_by_project(project_id)
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/publication-summary", response_model=UploadedVideoPublicationSummaryRead)
    def get_uploaded_video_publication_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoPublicationSummaryRead:
        try:
            with session_scope() as session:
                summary = ManualPublishConfirmationService(session).get_publication_summary(uploaded_video_id)
                if summary is None:
                    raise NotFoundError(f"uploaded video publication summary not found: {uploaded_video_id}")
                return UploadedVideoPublicationSummaryRead.model_validate(_uploaded_video_summary(summary))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/analytics-sync-runs", response_model=AnalyticsSyncRunRead)
    def create_analytics_sync_run(data: AnalyticsSyncRunCreate) -> AnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = AnalyticsSyncService(session).create_sync_run(data=data)
                return AnalyticsSyncRunRead.model_validate(_analytics_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/analytics-sync-runs/{sync_run_id}/execute", response_model=AnalyticsSyncRunRead)
    def execute_analytics_sync_run(
        sync_run_id: uuid.UUID,
        data: AnalyticsSyncRunExecuteRequest | None = None,
    ) -> AnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = AnalyticsSyncService(session).execute_sync_run(sync_run_id=sync_run_id, data=data)
                return AnalyticsSyncRunRead.model_validate(_analytics_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/analytics-sync-runs/{sync_run_id}", response_model=AnalyticsSyncRunRead)
    def get_analytics_sync_run(sync_run_id: uuid.UUID) -> AnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = AnalyticsSyncService(session).require_sync_run(sync_run_id)
                return AnalyticsSyncRunRead.model_validate(_analytics_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/analytics/import-manual", response_model=AnalyticsSnapshotRead)
    def import_manual_analytics(data: ManualAnalyticsImportContract) -> AnalyticsSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).import_manual(data=data)
                return AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/analytics-snapshots/{snapshot_id}", response_model=AnalyticsSnapshotRead)
    def get_analytics_snapshot(snapshot_id: uuid.UUID) -> AnalyticsSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).require_snapshot(snapshot_id)
                return AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/analytics-snapshots", response_model=list[AnalyticsSnapshotRead])
    def list_uploaded_video_analytics_snapshots(uploaded_video_id: uuid.UUID) -> list[AnalyticsSnapshotRead]:
        try:
            with session_scope() as session:
                snapshots = AnalyticsSyncService(session).list_snapshots_by_uploaded_video(uploaded_video_id)
                return [AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot)) for snapshot in snapshots]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/metrics-summary", response_model=UploadedVideoMetricsSummaryRead)
    def get_uploaded_video_metrics_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoMetricsSummaryRead:
        try:
            with session_scope() as session:
                summary = AnalyticsSyncService(session).get_metrics_summary(uploaded_video_id)
                return UploadedVideoMetricsSummaryRead.model_validate(_uploaded_video_metrics_summary(summary))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/retention", response_model=RetentionCurveSnapshotRead)
    def get_uploaded_video_retention(uploaded_video_id: uuid.UUID) -> RetentionCurveSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).latest_retention(uploaded_video_id)
                if snapshot is None:
                    raise NotFoundError(f"retention snapshot not found: {uploaded_video_id}")
                return RetentionCurveSnapshotRead.model_validate(_retention_curve_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/traffic-sources", response_model=TrafficSourceSnapshotRead)
    def get_uploaded_video_traffic_sources(uploaded_video_id: uuid.UUID) -> TrafficSourceSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).latest_traffic_sources(uploaded_video_id)
                if snapshot is None:
                    raise NotFoundError(f"traffic source snapshot not found: {uploaded_video_id}")
                return TrafficSourceSnapshotRead.model_validate(_traffic_source_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/auth/youtube/start")
    def start_youtube_auth(
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> RedirectResponse:
        try:
            with session_scope() as session:
                result = YouTubeOAuthSessionService(session).start(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                )
                return RedirectResponse(result.authorization_url)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/auth/youtube/callback", response_model=YouTubeOAuthSessionRead)
    def youtube_auth_callback(
        state: str,
        code: str | None = None,
        error: str | None = None,
    ) -> YouTubeOAuthSessionRead:
        try:
            with session_scope() as session:
                oauth_session = YouTubeOAuthSessionService(session).handle_callback(state=state, code=code, error=error)
                return YouTubeOAuthSessionRead.model_validate(_youtube_oauth_session(oauth_session))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/youtube/connection-status", response_model=YouTubeConnectionStatusRead)
    def get_youtube_connection_status() -> YouTubeConnectionStatusRead:
        try:
            with session_scope() as session:
                return YouTubeCredentialHealthService(session).connection_status()
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
