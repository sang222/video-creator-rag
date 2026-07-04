from fastapi import APIRouter

from app.api.routes.imports import (
    ChannelRuntimeTraceRead,
    ChannelRuntimeTraceService,
    CommandCenterRead,
    DashboardQueuesRead,
    DiagnosticOpsQueueRead,
    DiagnosticOpsService,
    LearningOpsQueueRead,
    LearningOpsService,
    M11DashboardService,
    MemoryInfluenceOpsRead,
    MemoryInfluenceOpsService,
    MemoryOpsQueueRead,
    MemoryOpsReadModelService,
    OperatorNextActionRead,
    PackageOpsSummaryRead,
    PackageOpsSummaryService,
    ProviderCostOpsRead,
    ProviderCostOpsService,
    ProviderOpsDashboardRead,
    QualityDeltaOpsRead,
    QualityDeltaOpsService,
    RecoveryOpsQueueRead,
    RecoveryOpsService,
    RetrievalManifestOpsRead,
    RetrievalOpsTraceService,
    RuntimeDashboardRead,
    RuntimeDashboardService,
    RuntimeLTSFreezeCheckRead,
    RuntimeLTSFreezeVerifier,
    UploadedVideoOpsService,
    UploadedVideoOpsSummaryRead,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router(application) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard/command-center", response_model=CommandCenterRead)
    def get_dashboard_command_center(company_id: uuid.UUID | None = None) -> CommandCenterRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).command_center(company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/dashboard/queues", response_model=DashboardQueuesRead)
    def get_dashboard_queues(queue_type: str | None = None) -> DashboardQueuesRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).queues(queue_type=queue_type)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/dashboard/queues/{queue_type}", response_model=DashboardQueuesRead)
    def get_dashboard_queue_by_type(queue_type: str) -> DashboardQueuesRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).queues(queue_type=queue_type)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ops/command-center", response_model=RuntimeDashboardRead)
    def get_runtime_ops_command_center(limit: int = 25) -> RuntimeDashboardRead:
        try:
            with session_scope() as session:
                return RuntimeDashboardService(session).command_center(limit=limit)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ops/next-actions", response_model=list[OperatorNextActionRead])
    def get_runtime_ops_next_actions() -> list[OperatorNextActionRead]:
        try:
            with session_scope() as session:
                return RuntimeDashboardService(session).next_actions()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ops/runtime-lts-freeze-check", response_model=RuntimeLTSFreezeCheckRead)
    def get_runtime_lts_freeze_check() -> RuntimeLTSFreezeCheckRead:
        try:
            with session_scope() as session:
                return RuntimeLTSFreezeVerifier(session, application=application).verify()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/runtime-trace", response_model=ChannelRuntimeTraceRead)
    def get_channel_runtime_trace(channel_id: uuid.UUID) -> ChannelRuntimeTraceRead:
        try:
            with session_scope() as session:
                return ChannelRuntimeTraceService(session).for_channel(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/runtime-trace", response_model=ChannelRuntimeTraceRead)
    def get_video_project_runtime_trace(project_id: uuid.UUID) -> ChannelRuntimeTraceRead:
        try:
            with session_scope() as session:
                return ChannelRuntimeTraceService(session).for_project(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-packages/{package_id}/ops-summary", response_model=PackageOpsSummaryRead)
    def get_video_package_ops_summary(package_id: uuid.UUID) -> PackageOpsSummaryRead:
        try:
            with session_scope() as session:
                return PackageOpsSummaryService(session).build(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/uploaded-videos/{uploaded_video_id}/ops-summary", response_model=UploadedVideoOpsSummaryRead)
    def get_uploaded_video_ops_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoOpsSummaryRead:
        try:
            with session_scope() as session:
                return UploadedVideoOpsService(session).build(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/diagnostics/queue", response_model=DiagnosticOpsQueueRead)
    def get_diagnostic_ops_queue(limit: int = 100) -> DiagnosticOpsQueueRead:
        try:
            with session_scope() as session:
                return DiagnosticOpsService(session).queue(limit=limit)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/recovery/queue", response_model=RecoveryOpsQueueRead)
    def get_recovery_ops_queue(limit: int = 100) -> RecoveryOpsQueueRead:
        try:
            with session_scope() as session:
                return RecoveryOpsService(session).queue(limit=limit)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/learning/queue", response_model=LearningOpsQueueRead)
    def get_learning_ops_queue(limit: int = 100) -> LearningOpsQueueRead:
        try:
            with session_scope() as session:
                return LearningOpsService(session).queue(limit=limit)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory/review-queue/ops", response_model=MemoryOpsQueueRead)
    def get_memory_ops_queue(limit: int = 100) -> MemoryOpsQueueRead:
        try:
            with session_scope() as session:
                return MemoryOpsReadModelService(session).queue(limit=limit)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/retrieval-manifests/{manifest_id}", response_model=RetrievalManifestOpsRead)
    def get_retrieval_manifest_ops(manifest_id: uuid.UUID) -> RetrievalManifestOpsRead:
        try:
            with session_scope() as session:
                return RetrievalOpsTraceService(session).build(manifest_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/memory-influence/{manifest_id}", response_model=MemoryInfluenceOpsRead)
    def get_memory_influence_ops(manifest_id: uuid.UUID) -> MemoryInfluenceOpsRead:
        try:
            with session_scope() as session:
                return MemoryInfluenceOpsService(session).build(manifest_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/quality-delta/{quality_delta_id}", response_model=QualityDeltaOpsRead)
    def get_quality_delta_ops(quality_delta_id: uuid.UUID) -> QualityDeltaOpsRead:
        try:
            with session_scope() as session:
                return QualityDeltaOpsService(session).build(quality_delta_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/provider-cost/{package_id}", response_model=ProviderCostOpsRead)
    def get_provider_cost_ops(package_id: uuid.UUID) -> ProviderCostOpsRead:
        try:
            with session_scope() as session:
                return ProviderCostOpsService(session).build(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/providers/status", response_model=ProviderOpsDashboardRead)
    def get_provider_status_dashboard() -> ProviderOpsDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).provider_ops()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/ops/health", response_model=ProviderOpsDashboardRead)
    def get_ops_health_dashboard() -> ProviderOpsDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).provider_ops()
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
