import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from app.contracts import (
    ApprovalDecisionCreate,
    ApprovalDecisionRead,
    AnalyticsSnapshotRead,
    AnalyticsSyncRunCreate,
    AnalyticsSyncRunExecuteRequest,
    AnalyticsSyncRunRead,
    ArtifactCreate,
    ArtifactRead,
    ArtifactVersionCreate,
    ArtifactVersionRead,
    BudgetGateCheckRequest,
    BudgetGateDecisionRead,
    BudgetPolicyCreate,
    BudgetPolicyRead,
    ChannelMembershipCreate,
    ChannelMembershipRead,
    ChannelProfileCompileRequest,
    ChannelProfileCompileResult,
    ChannelProfileVersionCreate,
    ChannelProfileVersionRead,
    ChannelWorkspaceCreate,
    ChannelWorkspaceRead,
    ComponentHealthSnapshotCreate,
    ComponentHealthSnapshotRead,
    CostEventCreate,
    CostEventRead,
    CredentialHealthSnapshotCreate,
    CredentialHealthSnapshotRead,
    CredentialReferenceCreate,
    CredentialReferenceRead,
    DailyIdeaDecisionCreate,
    DailyIdeaDecisionRead,
    DailyRunExecuteRequest,
    DeadLetterJobCreate,
    DeadLetterJobRead,
    EditorialCalendarSlotCreate,
    EditorialCalendarSlotRead,
    GateRunCreate,
    GateRunRead,
    IdeaMarketPreflightCreate,
    IdeaMarketPreflightRead,
    ManualActionCreate,
    ManualActionRead,
    ManualAnalyticsImportContract,
    ManualPublishConfirmationCreate,
    ManualPublishConfirmationRead,
    OpsIncidentCreate,
    OpsIncidentRead,
    PlatformPolicyCatalogCreate,
    PlatformPolicyCatalogRead,
    PlatformPolicyVersionCreate,
    PlatformPolicyVersionRead,
    PolicyChangeRecordCreate,
    PolicyChangeRecordRead,
    PolicyChangeStateRequest,
    PolicyRevalidationBatchCreate,
    PolicyRevalidationBatchRead,
    PolicySourceRefCreate,
    PolicySourceRefRead,
    ProviderAttemptRead,
    ProviderHealthCheckRequest,
    ProviderHealthSnapshotRead,
    ProviderRegistryEntryCreate,
    ProviderRegistryEntryRead,
    ProjectAdmissionDecisionCreate,
    ProjectAdmissionDecisionRead,
    FailureTraceReportRead,
    LearningCandidateGenerationRunCreate,
    LearningCandidateGenerationRunExecuteRequest,
    LearningCandidateGenerationRunRead,
    LearningCandidateRead,
    LearningEvidenceBundleRead,
    LearningReviewQueueItemRead,
    AssetReuseIndexEntryRead,
    AssetReuseSearchRequest,
    BuildUploadCardsRequest,
    ContentDerivativeGraphEdgeCreate,
    ContentDerivativeGraphEdgeRead,
    CrossPlatformFunnelPackageCreate,
    CrossPlatformFunnelPackageRead,
    DerivativeOriginalityCheckCreate,
    DerivativeOriginalityCheckRead,
    HumanUploadTaskRead,
    AIHeroAssetPlanRequest,
    AIHeroAssetRead,
    AIHeroGenerationExecuteRequest,
    AIHeroGenerationJobRead,
    CreatomateRenderAssetPlanRequest,
    CreatomateRenderAssetRead,
    LicenseEvidenceGateCheckRequest,
    LicenseEvidenceGateRead,
    LongFormRenderPackageCreate,
    LongFormRenderPackageRead,
    MediaProviderBudgetCheckRequest,
    MediaProviderBudgetGateRead,
    MediaProviderBudgetPolicyRead,
    MediaProviderBudgetSnapshotRead,
    MediaProviderRoleProfileRead,
    MediaQCGateCheckRequest,
    MediaQCGateRead,
    MediaRenderRoutingDecisionRead,
    MediaRenderRoutingDecisionRequest,
    ProviderCapabilityGateCheckRequest,
    ProviderCapabilityGateRead,
    ProviderCapabilityMatrixEntryRead,
    ReusedContentRiskGateCheckRequest,
    ReusedContentRiskGateRead,
    ShortRenderPackageCreate,
    ShortRenderPackageRead,
    ThumbnailVariantPlanRequest,
    ThumbnailVariantRead,
    LLMModelProfileRead,
    LLMRouteRequest,
    LLMRouteResponse,
    LLMRouterLaneRead,
    LLMRouterProfileRead,
    LLMRouterSmokeTestRead,
    LLMRouterSmokeTestRequest,
    PromoteShortToLongCandidateCreate,
    PromoteShortToLongCandidateRead,
    ReusableArtifactCreate,
    ReusableArtifactRead,
    ShortCandidateExtractRequest,
    ShortCandidateRankRequest,
    ShortCandidateRead,
    ShortCandidateScoreRead,
    UploadCardRead,
    ProductionArtifactRunCreate,
    ProductionArtifactRunRead,
    PostPublishHealthRunCreate,
    PostPublishHealthRunRead,
    PublishHandoffCreate,
    PublishHandoffRead,
    QCRunRequest,
    QuotaAccountCreate,
    QuotaAccountRead,
    QuotaEventRead,
    QuotaEventRequest,
    RetentionCurveSnapshotRead,
    RetrievalPlanSnapshotCreate,
    RetrievalPlanSnapshotRead,
    ReviewFindingCreate,
    ReviewFindingRead,
    ReviewTaskCreate,
    ReviewTaskRead,
    RevisionRequestCreate,
    RevisionRequestRead,
    RevisionResolveRequest,
    RetryPolicyCreate,
    RetryPolicyRead,
    SearchDemandEvidenceCreate,
    SearchDemandEvidenceRead,
    SystemHealthSnapshotRead,
    TrafficSourceSnapshotRead,
    UploadedVideoPublicationSummaryRead,
    UploadedVideoMetricsSummaryRead,
    UploadedVideoRead,
    UploadedVideoYouTubeFollowSummaryRead,
    CloudMediaReadPayload,
    GoogleDriveConnectionStatusRead,
    GoogleDriveOAuthSessionRead,
    LocalCleanupRunRequest,
    LocalCleanupRunResult,
    LocalMediaRetentionPolicyRead,
    MediaOffloadExecuteRequest,
    MediaOffloadJobCreate,
    MediaOffloadJobRead,
    VideoProjectCreate,
    VideoProjectRead,
    YouTubeConnectionStatusRead,
    YouTubeOAuthSessionRead,
    YouTubeOwnerAnalyticsSnapshotRead,
    YouTubeOwnerAnalyticsSyncRequest,
    YouTubeOwnerAnalyticsSyncRunRead,
    YouTubePublicMonitorSnapshotRead,
    YouTubePublicSyncRunRead,
    RecoveryProposalRead,
    PlaybookCandidateDraftRead,
    ChannelDailyRunCreate,
    ChannelDailyRunRead,
    ChannelStatePackSnapshotCreate,
    ChannelStatePackSnapshotRead,
    ContextPackSnapshotCreate,
    ContextPackSnapshotRead,
    AuthLoginRequest,
    AuthSessionRead,
    ChannelLocalizationConfig,
    ChannelLocalizationConfigUpdate,
    ChannelPublishTimingPolicyCreate,
    ChannelPublishTimingPolicyRead,
    LocalizationReadinessGateRead,
    LocalizedMetadataPackageCreate,
    LocalizedMetadataPackageRead,
    LocalizedSubtitlePackageCreate,
    LocalizedSubtitlePackageRead,
    PublishTimingSuggestionRead,
    VideoProjectLocalizationRead,
    IntegrationReadinessRead,
    ProviderReadinessSnapshotRead,
    ProviderSmokeRequest,
    PromptEvaluationRunRead,
    PromptOutputValidationRequest,
    PromptOutputValidationResult,
    PromptRegistrySyncSummary,
    PromptRenderRequest,
    PromptRenderResult,
    ReadinessRunRequest,
    RealSmokeRunRead,
    FirstScriptedVideoPackageRead,
    FirstScriptedVideoPackageRequest,
    FirstScriptedVideoPackageReviewRead,
)
from app.contracts.policy_snapshot import CompiledChannelPolicySnapshot as SnapshotRead
from app.contracts.m11 import (
    ChannelLifecycleDecisionCreate,
    ChannelLifecycleDecisionRead,
    ChannelLifecycleRead,
    ChannelWorkspaceDashboardRead,
    CommandCenterRead,
    DashboardQueuesRead,
    LearningReviewDecisionCreate,
    LearningReviewDecisionRead,
    ProviderOpsDashboardRead,
    UploadedVideoDashboardRead,
    UploadedVideoListItem,
)
from app.core.config import get_settings
from app.core.db import check_database
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailureError
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.services import (
    AccessibilityQCService,
    AnalyticsSyncService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    PolicySnapshotService,
    ApprovalService,
    ArtifactService,
    ReviewService,
    VideoProjectService,
    GateDefinitionService,
    GateRunnerService,
    BudgetGateService,
    ChannelAuthorityService,
    ChannelDailyRunService,
    ChannelStatePackService,
    ComponentHealthService,
    CostService,
    CredentialReferenceService,
    DeadLetterService,
    EditorialCalendarService,
    ManualActionService,
    ManualPublishConfirmationService,
    OpsIncidentService,
    PolicyCatalogService,
    PolicyChangeService,
    PolicyRevalidationService,
    IdeaMarketPreflightService,
    LocalFixtureRendererService,
    LearningCandidateGenerationService,
    LearningReadService,
    LearningReviewQueueService,
    AssetReuseIndexService,
    CrossPlatformFunnelPackageService,
    DerivativeGraphService,
    DerivativeOriginalityService,
    HumanUploadTaskService,
    AIHeroAssetPlanningService,
    AIHeroGenerationService,
    CreatomateRenderAssetPlanningService,
    LicenseEvidenceGateService,
    LongFormRenderPackageService,
    MediaProviderBudgetService,
    MediaProviderRoleService,
    MediaQCGateService,
    MediaRenderJobRouterService,
    ProviderCapabilityGateService,
    ProviderCapabilityMatrixService,
    ReusedContentRiskGateService,
    ShortRenderPackageService,
    ThumbnailVariantPlanningService,
    LLMRouterConfigLoader,
    LLMRouterService,
    MediaQCService,
    PromoteShortToLongCandidateService,
    ProjectAdmissionService,
    ProviderHealthService,
    ProviderRegistryService,
    ProductionArtifactRunService,
    PostPublishHealthMonitorService,
    PublishHandoffService,
    QuotaService,
    ResourceResolverService,
    RetryOpsService,
    SearchDemandEvidenceService,
    ReusableArtifactService,
    ShortCandidateExtractionService,
    ShortCandidateRankingService,
    SystemHealthService,
    UploadCardService,
    WorkflowReadinessService,
    UploadedVideoYouTubeFollowReadService,
    GoogleDriveCredentialHealthService,
    GoogleDriveOAuthSessionService,
    LocalMediaCleanupService,
    LocalMediaRetentionPolicyService,
    MediaCloudReadService,
    MediaOffloadJobService,
    YouTubeCredentialHealthService,
    YouTubeOAuthSessionService,
    YouTubeOwnerAnalyticsSyncService,
    YouTubePublicStatsSyncService,
    AuthService,
    LocalizationConfigService,
    LocalizationReadinessGateService,
    LocalizedMetadataPackageService,
    LocalizedSubtitlePackageService,
    PublishTimingPolicyService,
    PublishTimingSuggestionService,
    ProviderReadinessService,
    PromptRegistryService,
    RealSmokeOrchestratorService,
    FirstScriptedVideoPackageService,
)
from app.services.m11 import (
    M11ChannelLifecycleService,
    M11DashboardService,
    M11LearningReviewService,
    channel_lifecycle_decision_read,
    learning_review_decision_read,
)
from app.services.m11_1 import AUTH_COOKIE_NAME


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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

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

    @application.post("/auth/login", response_model=AuthSessionRead)
    def auth_login(data: AuthLoginRequest, response: Response) -> AuthSessionRead:
        try:
            with session_scope() as session:
                auth_payload, token, expires_at = AuthService(session, settings).login(email=data.email, password=data.password)
                response.set_cookie(
                    AUTH_COOKIE_NAME,
                    token,
                    httponly=True,
                    secure=False,
                    samesite="lax",
                    expires=expires_at,
                    max_age=settings.auth_session_ttl_hours * 60 * 60,
                    path="/",
                )
                return auth_payload
        except ForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email hoặc mật khẩu không đúng.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/auth/logout")
    def auth_logout(request: Request, response: Response) -> dict[str, str]:
        try:
            with session_scope() as session:
                AuthService(session, settings).logout(request.cookies.get(AUTH_COOKIE_NAME))
                response.delete_cookie(AUTH_COOKIE_NAME, path="/")
                return {"status": "ok", "message": "Đăng xuất thành công."}
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/auth/me", response_model=AuthSessionRead)
    def auth_me(request: Request) -> AuthSessionRead:
        try:
            with session_scope() as session:
                return AuthService(session, settings).current_user(request.cookies.get(AUTH_COOKIE_NAME))
        except ForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Phiên đăng nhập đã hết hạn.") from exc
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/dashboard/command-center", response_model=CommandCenterRead)
    def get_dashboard_command_center(company_id: uuid.UUID | None = None) -> CommandCenterRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).command_center(company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/dashboard/queues", response_model=DashboardQueuesRead)
    def get_dashboard_queues(queue_type: str | None = None) -> DashboardQueuesRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).queues(queue_type=queue_type)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/dashboard/queues/{queue_type}", response_model=DashboardQueuesRead)
    def get_dashboard_queue_by_type(queue_type: str) -> DashboardQueuesRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).queues(queue_type=queue_type)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/providers/status", response_model=ProviderOpsDashboardRead)
    def get_provider_status_dashboard() -> ProviderOpsDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).provider_ops()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/ops/health", response_model=ProviderOpsDashboardRead)
    def get_ops_health_dashboard() -> ProviderOpsDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).provider_ops()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/integrations/readiness", response_model=IntegrationReadinessRead)
    def get_integrations_readiness() -> IntegrationReadinessRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).readiness()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/integrations/readiness/run", response_model=ProviderReadinessSnapshotRead)
    def run_integrations_readiness(data: ReadinessRunRequest | None = None) -> ProviderReadinessSnapshotRead:
        _ = data or ReadinessRunRequest()
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).run()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/integrations/readiness/snapshots/{snapshot_id}", response_model=ProviderReadinessSnapshotRead)
    def get_integrations_readiness_snapshot(snapshot_id: uuid.UUID) -> ProviderReadinessSnapshotRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).get_snapshot(snapshot_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/integrations/providers/{provider_key}/readiness", response_model=IntegrationReadinessRead)
    def get_provider_readiness(provider_key: str) -> IntegrationReadinessRead:
        try:
            with session_scope() as session:
                return ProviderReadinessService(session).provider_readiness(provider_key)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/integrations/providers/{provider_key}/smoke", response_model=RealSmokeRunRead)
    def run_provider_smoke(provider_key: str, data: ProviderSmokeRequest | None = None) -> RealSmokeRunRead:
        try:
            with session_scope() as session:
                return RealSmokeOrchestratorService(session).run_provider(provider_key, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/integrations/smoke-runs/{run_id}", response_model=RealSmokeRunRead)
    def get_integration_smoke_run(run_id: uuid.UUID) -> RealSmokeRunRead:
        try:
            with session_scope() as session:
                return RealSmokeOrchestratorService(session).get_run(run_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

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

    @application.get("/channels")
    def list_dashboard_channels(company_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        try:
            with session_scope() as session:
                return M11DashboardService(session).list_channels(company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}", response_model=ChannelWorkspaceRead)
    def get_channel(channel_id: uuid.UUID) -> ChannelWorkspaceRead:
        with session_scope() as session:
            channel = ChannelWorkspaceService(session).get_channel(channel_id)
            if channel is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel not found")
            return ChannelWorkspaceRead.model_validate(_channel(channel))

    @application.get("/channels/{channel_id}/workspace", response_model=ChannelWorkspaceDashboardRead)
    def get_channel_workspace_dashboard(channel_id: uuid.UUID) -> ChannelWorkspaceDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).workspace(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}/lifecycle", response_model=ChannelLifecycleRead)
    def get_channel_lifecycle(channel_id: uuid.UUID) -> ChannelLifecycleRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).lifecycle(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channels/{channel_id}/lifecycle-decision", response_model=ChannelLifecycleDecisionRead)
    def create_channel_lifecycle_decision(
        channel_id: uuid.UUID,
        data: ChannelLifecycleDecisionCreate,
    ) -> ChannelLifecycleDecisionRead:
        try:
            with session_scope() as session:
                decision = M11ChannelLifecycleService(session).decide(channel_id=channel_id, data=data)
                return channel_lifecycle_decision_read(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}/localization-config", response_model=ChannelLocalizationConfig)
    def get_channel_localization_config(channel_id: uuid.UUID) -> ChannelLocalizationConfig:
        try:
            with session_scope() as session:
                return LocalizationConfigService(session).get(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channels/{channel_id}/localization-config", response_model=ChannelLocalizationConfig)
    def update_channel_localization_config(
        channel_id: uuid.UUID,
        data: ChannelLocalizationConfigUpdate,
    ) -> ChannelLocalizationConfig:
        try:
            with session_scope() as session:
                return LocalizationConfigService(session).update(channel_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channels/{channel_id}/publish-timing-policy", response_model=ChannelPublishTimingPolicyRead)
    def get_channel_publish_timing_policy(channel_id: uuid.UUID) -> ChannelPublishTimingPolicyRead:
        try:
            with session_scope() as session:
                return PublishTimingPolicyService(session).get(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channels/{channel_id}/publish-timing-policy", response_model=ChannelPublishTimingPolicyRead)
    def update_channel_publish_timing_policy(
        channel_id: uuid.UUID,
        data: ChannelPublishTimingPolicyCreate,
    ) -> ChannelPublishTimingPolicyRead:
        try:
            with session_scope() as session:
                return PublishTimingPolicyService(session).update(channel_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

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

    @application.post("/video-projects", response_model=VideoProjectRead)
    def create_video_project(data: VideoProjectCreate) -> VideoProjectRead:
        try:
            with session_scope() as session:
                project = VideoProjectService(session).create_project(data=data)
                return VideoProjectRead.model_validate(_video_project(project))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{project_id}/workflow-state")
    def inspect_video_project_workflow(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return VideoProjectService(session).inspect_workflow_state(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{video_project_id}/localization", response_model=VideoProjectLocalizationRead)
    def get_video_project_localization(video_project_id: uuid.UUID) -> VideoProjectLocalizationRead:
        try:
            with session_scope() as session:
                return LocalizationReadinessGateService(session).video_localization(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/localized-subtitles", response_model=LocalizedSubtitlePackageRead)
    def create_localized_subtitle_package(
        video_project_id: uuid.UUID,
        data: LocalizedSubtitlePackageCreate,
    ) -> LocalizedSubtitlePackageRead:
        try:
            with session_scope() as session:
                return LocalizedSubtitlePackageService(session).create(video_project_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/localized-metadata", response_model=LocalizedMetadataPackageRead)
    def create_localized_metadata_package(
        video_project_id: uuid.UUID,
        data: LocalizedMetadataPackageCreate,
    ) -> LocalizedMetadataPackageRead:
        try:
            with session_scope() as session:
                return LocalizedMetadataPackageService(session).create(video_project_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/localization-readiness/check", response_model=LocalizationReadinessGateRead)
    def check_video_project_localization_readiness(video_project_id: uuid.UUID) -> LocalizationReadinessGateRead:
        try:
            with session_scope() as session:
                return LocalizationReadinessGateService(session).check(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/artifacts", response_model=ArtifactRead)
    def create_artifact(data: ArtifactCreate) -> ArtifactRead:
        try:
            with session_scope() as session:
                artifact = ArtifactService(session).create_artifact(data=data)
                return ArtifactRead.model_validate(_artifact(artifact))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/artifact-versions", response_model=ArtifactVersionRead)
    def create_artifact_version(data: ArtifactVersionCreate) -> ArtifactVersionRead:
        try:
            with session_scope() as session:
                version = ArtifactService(session).create_artifact_version(data=data)
                return ArtifactVersionRead.model_validate(_artifact_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/review-tasks", response_model=ReviewTaskRead)
    def create_review_task(data: ReviewTaskCreate) -> ReviewTaskRead:
        try:
            with session_scope() as session:
                review_task = ReviewService(session).create_review_task(data=data)
                return ReviewTaskRead.model_validate(_review_task(review_task))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/review-findings", response_model=ReviewFindingRead)
    def add_review_finding(data: ReviewFindingCreate) -> ReviewFindingRead:
        try:
            with session_scope() as session:
                finding = ReviewService(session).add_finding(data=data)
                return ReviewFindingRead.model_validate(_review_finding(finding))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/revision-requests", response_model=RevisionRequestRead)
    def create_revision_request(data: RevisionRequestCreate) -> RevisionRequestRead:
        try:
            with session_scope() as session:
                revision = ReviewService(session).create_revision_request(data=data)
                return RevisionRequestRead.model_validate(_revision_request(revision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/revision-requests/{revision_request_id}/resolve", response_model=RevisionRequestRead)
    def resolve_revision_request(
        revision_request_id: uuid.UUID,
        data: RevisionResolveRequest,
    ) -> RevisionRequestRead:
        try:
            with session_scope() as session:
                revision = ReviewService(session).resolve_revision_request(
                    revision_request_id=revision_request_id,
                    resolved_by_artifact_version_id=data.resolved_by_artifact_version_id,
                )
                return RevisionRequestRead.model_validate(_revision_request(revision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/approval-decisions", response_model=ApprovalDecisionRead)
    def create_approval_decision(data: ApprovalDecisionCreate) -> ApprovalDecisionRead:
        try:
            with session_scope() as session:
                decision = ApprovalService(session).create_approval_decision(data=data)
                return ApprovalDecisionRead.model_validate(_approval_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/gates/seed-definitions")
    def seed_gate_definitions() -> dict[str, int]:
        try:
            with session_scope() as session:
                records = GateDefinitionService(session).seed_definitions()
                return {"count": len(records)}
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/gates/run", response_model=GateRunRead)
    def run_gate(data: GateRunCreate) -> GateRunRead:
        try:
            with session_scope() as session:
                gate_run = GateRunnerService(session).run_gate(data=data)
                return GateRunRead.model_validate(_gate_run(gate_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/gate-runs/{gate_run_id}", response_model=GateRunRead)
    def get_gate_run(gate_run_id: uuid.UUID) -> GateRunRead:
        try:
            with session_scope() as session:
                gate_run = GateRunnerService(session).get_gate_run(gate_run_id)
                if gate_run is None:
                    raise NotFoundError(f"gate run not found: {gate_run_id}")
                return GateRunRead.model_validate(_gate_run(gate_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{project_id}/gate-runs", response_model=list[GateRunRead])
    def list_project_gate_runs(project_id: uuid.UUID) -> list[GateRunRead]:
        try:
            with session_scope() as session:
                return [GateRunRead.model_validate(_gate_run(run)) for run in GateRunnerService(session).list_project_gate_runs(project_id)]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{project_id}/readiness")
    def inspect_project_readiness(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return WorkflowReadinessService(session).inspect_project(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-catalogs", response_model=PlatformPolicyCatalogRead)
    def create_policy_catalog(data: PlatformPolicyCatalogCreate) -> PlatformPolicyCatalogRead:
        try:
            with session_scope() as session:
                catalog = PolicyCatalogService(session).create_catalog(data=data)
                return PlatformPolicyCatalogRead.model_validate(_policy_catalog(catalog))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-versions", response_model=PlatformPolicyVersionRead)
    def create_policy_version(data: PlatformPolicyVersionCreate) -> PlatformPolicyVersionRead:
        try:
            with session_scope() as session:
                version = PolicyCatalogService(session).create_version(data=data)
                return PlatformPolicyVersionRead.model_validate(_policy_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-versions/{policy_version_id}/activate", response_model=PlatformPolicyVersionRead)
    def activate_policy_version(policy_version_id: uuid.UUID) -> PlatformPolicyVersionRead:
        try:
            with session_scope() as session:
                version = PolicyCatalogService(session).activate_version(policy_version_id)
                return PlatformPolicyVersionRead.model_validate(_policy_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-source-refs", response_model=PolicySourceRefRead)
    def create_policy_source_ref(data: PolicySourceRefCreate) -> PolicySourceRefRead:
        try:
            with session_scope() as session:
                ref = PolicyCatalogService(session).attach_source_ref(data=data)
                return PolicySourceRefRead.model_validate(_policy_source_ref(ref))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-change-records", response_model=PolicyChangeRecordRead)
    def create_policy_change_record(data: PolicyChangeRecordCreate) -> PolicyChangeRecordRead:
        try:
            with session_scope() as session:
                record = PolicyChangeService(session).create_change_record(data=data)
                return PolicyChangeRecordRead.model_validate(_policy_change_record(record))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-change-records/{policy_change_record_id}/state", response_model=PolicyChangeRecordRead)
    def transition_policy_change(policy_change_record_id: uuid.UUID, data: PolicyChangeStateRequest) -> PolicyChangeRecordRead:
        try:
            with session_scope() as session:
                record = PolicyChangeService(session).transition_state(policy_change_record_id, data.state)
                return PolicyChangeRecordRead.model_validate(_policy_change_record(record))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-revalidation-batches", response_model=PolicyRevalidationBatchRead)
    def create_policy_revalidation_batch(data: PolicyRevalidationBatchCreate) -> PolicyRevalidationBatchRead:
        try:
            with session_scope() as session:
                batch = PolicyRevalidationService(session).create_batch(data=data)
                return PolicyRevalidationBatchRead.model_validate(_policy_revalidation_batch(batch))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/policy-revalidation-batches/{batch_id}/run", response_model=PolicyRevalidationBatchRead)
    def run_policy_revalidation_batch(batch_id: uuid.UUID) -> PolicyRevalidationBatchRead:
        try:
            with session_scope() as session:
                batch = PolicyRevalidationService(session).run_batch(batch_id)
                return PolicyRevalidationBatchRead.model_validate(_policy_revalidation_batch(batch))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/providers", response_model=ProviderRegistryEntryRead)
    def create_provider(data: ProviderRegistryEntryCreate) -> ProviderRegistryEntryRead:
        try:
            with session_scope() as session:
                entry = ProviderRegistryService(session).create_entry(data=data)
                return ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/providers", response_model=list[ProviderRegistryEntryRead])
    def list_providers() -> list[ProviderRegistryEntryRead]:
        try:
            with session_scope() as session:
                return [
                    ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
                    for entry in ProviderRegistryService(session).list_entries()
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/providers/{provider_key}", response_model=ProviderRegistryEntryRead)
    def get_provider(provider_key: str) -> ProviderRegistryEntryRead:
        try:
            with session_scope() as session:
                entry = ProviderRegistryService(session).require_entry(provider_key)
                return ProviderRegistryEntryRead.model_validate(_provider_registry_entry(entry))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/providers/{provider_key}/health-check", response_model=ProviderHealthSnapshotRead)
    def provider_health_check(provider_key: str, data: ProviderHealthCheckRequest) -> ProviderHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ProviderHealthService(session).check_provider(
                    provider_key=provider_key,
                    mode=data.mode,
                    next_action=data.next_action,
                    metadata=data.metadata,
                )
                return ProviderHealthSnapshotRead.model_validate(_provider_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/providers/{provider_key}/health", response_model=list[ProviderHealthSnapshotRead])
    def list_provider_health(provider_key: str) -> list[ProviderHealthSnapshotRead]:
        try:
            with session_scope() as session:
                return [ProviderHealthSnapshotRead.model_validate(_provider_health(item)) for item in ProviderHealthService(session).list_health(provider_key)]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/credential-references", response_model=CredentialReferenceRead)
    def create_credential_reference(data: CredentialReferenceCreate) -> CredentialReferenceRead:
        try:
            with session_scope() as session:
                reference = CredentialReferenceService(session).create_reference(data=data)
                return CredentialReferenceRead.model_validate(_credential_reference(reference))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/credential-references/{credential_reference_id}", response_model=CredentialReferenceRead)
    def get_credential_reference(credential_reference_id: uuid.UUID) -> CredentialReferenceRead:
        try:
            with session_scope() as session:
                reference = CredentialReferenceService(session).require_reference(credential_reference_id)
                return CredentialReferenceRead.model_validate(_credential_reference(reference))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/credential-references/{credential_reference_id}/health-check", response_model=CredentialHealthSnapshotRead)
    def credential_health_check(credential_reference_id: uuid.UUID, data: CredentialHealthSnapshotCreate | None = None) -> CredentialHealthSnapshotRead:
        try:
            with session_scope() as session:
                request = data or CredentialHealthSnapshotCreate(credential_reference_id=credential_reference_id)
                request = request.model_copy(update={"credential_reference_id": credential_reference_id})
                snapshot = CredentialReferenceService(session).check_health(data=request)
                return CredentialHealthSnapshotRead.model_validate(_credential_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/quota-accounts", response_model=QuotaAccountRead)
    def create_quota_account(data: QuotaAccountCreate) -> QuotaAccountRead:
        try:
            with session_scope() as session:
                account = QuotaService(session).create_account(data=data)
                return QuotaAccountRead.model_validate(_quota_account(account))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/quota-accounts/{quota_account_id}", response_model=QuotaAccountRead)
    def get_quota_account(quota_account_id: uuid.UUID) -> QuotaAccountRead:
        try:
            with session_scope() as session:
                account = QuotaService(session).require_account(quota_account_id)
                return QuotaAccountRead.model_validate(_quota_account(account))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/quota-events/reserve", response_model=QuotaEventRead)
    def reserve_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).reserve_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/quota-events/consume", response_model=QuotaEventRead)
    def consume_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).consume_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/quota-events/release", response_model=QuotaEventRead)
    def release_quota(data: QuotaEventRequest) -> QuotaEventRead:
        try:
            with session_scope() as session:
                event = QuotaService(session).release_quota(data=data)
                return QuotaEventRead.model_validate(_quota_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/cost-events", response_model=CostEventRead)
    def create_cost_event(data: CostEventCreate) -> CostEventRead:
        try:
            with session_scope() as session:
                event = CostService(session).record_event(data=data)
                return CostEventRead.model_validate(_cost_event(event))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/cost-events", response_model=list[CostEventRead])
    def list_cost_events(
        provider_key: str | None = None,
        cost_scope_type: str | None = None,
        cost_scope_id: uuid.UUID | None = None,
    ) -> list[CostEventRead]:
        try:
            with session_scope() as session:
                return [
                    CostEventRead.model_validate(_cost_event(event))
                    for event in CostService(session).list_events(
                        provider_key=provider_key,
                        cost_scope_type=cost_scope_type,
                        cost_scope_id=cost_scope_id,
                    )
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/budget-policies", response_model=BudgetPolicyRead)
    def create_budget_policy(data: BudgetPolicyCreate) -> BudgetPolicyRead:
        try:
            with session_scope() as session:
                policy = BudgetGateService(session).create_policy(data=data)
                return BudgetPolicyRead.model_validate(_budget_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/budget-gates/check", response_model=BudgetGateDecisionRead)
    def check_budget_gate(data: BudgetGateCheckRequest) -> BudgetGateDecisionRead:
        try:
            with session_scope() as session:
                return BudgetGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/component-health/snapshot", response_model=ComponentHealthSnapshotRead)
    def create_component_health(data: ComponentHealthSnapshotCreate) -> ComponentHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ComponentHealthService(session).create_snapshot(data=data)
                return ComponentHealthSnapshotRead.model_validate(_component_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/system-health/snapshot", response_model=SystemHealthSnapshotRead)
    def create_system_health_snapshot() -> SystemHealthSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = SystemHealthService(session).create_snapshot()
                return SystemHealthSnapshotRead.model_validate(_system_health(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/system-health/latest", response_model=SystemHealthSnapshotRead | None)
    def get_latest_system_health() -> SystemHealthSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = SystemHealthService(session).latest()
                return SystemHealthSnapshotRead.model_validate(_system_health(snapshot)) if snapshot is not None else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/retry-policies", response_model=RetryPolicyRead)
    def create_retry_policy(data: RetryPolicyCreate) -> RetryPolicyRead:
        try:
            with session_scope() as session:
                policy = RetryOpsService(session).create_policy(data=data)
                return RetryPolicyRead.model_validate(_retry_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/provider-attempts/{attempt_id}", response_model=ProviderAttemptRead)
    def get_provider_attempt(attempt_id: uuid.UUID) -> ProviderAttemptRead:
        try:
            with session_scope() as session:
                attempt = RetryOpsService(session).get_attempt(attempt_id)
                if attempt is None:
                    raise NotFoundError(f"provider attempt not found: {attempt_id}")
                return ProviderAttemptRead.model_validate(_provider_attempt(attempt))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/dead-letter-jobs", response_model=DeadLetterJobRead)
    def create_dead_letter_job(data: DeadLetterJobCreate) -> DeadLetterJobRead:
        try:
            with session_scope() as session:
                job = DeadLetterService(session).create_job(data=data)
                return DeadLetterJobRead.model_validate(_dead_letter_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/dead-letter-jobs/{job_id}/replay", response_model=DeadLetterJobRead)
    def replay_dead_letter_job(job_id: uuid.UUID) -> DeadLetterJobRead:
        try:
            with session_scope() as session:
                job = DeadLetterService(session).replay_job(job_id)
                return DeadLetterJobRead.model_validate(_dead_letter_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/ops-incidents", response_model=OpsIncidentRead)
    def create_ops_incident(data: OpsIncidentCreate) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).create_incident(data=data)
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/ops-incidents", response_model=list[OpsIncidentRead])
    def list_ops_incidents() -> list[OpsIncidentRead]:
        try:
            with session_scope() as session:
                return [OpsIncidentRead.model_validate(_ops_incident(item)) for item in OpsIncidentService(session).list_incidents()]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/ops-incidents/{incident_id}/acknowledge", response_model=OpsIncidentRead)
    def acknowledge_ops_incident(incident_id: uuid.UUID) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).transition(incident_id, "ACKNOWLEDGED")
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/ops-incidents/{incident_id}/resolve", response_model=OpsIncidentRead)
    def resolve_ops_incident(incident_id: uuid.UUID) -> OpsIncidentRead:
        try:
            with session_scope() as session:
                incident = OpsIncidentService(session).transition(incident_id, "RESOLVED")
                return OpsIncidentRead.model_validate(_ops_incident(incident))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/manual-actions", response_model=ManualActionRead)
    def create_manual_action(data: ManualActionCreate) -> ManualActionRead:
        try:
            with session_scope() as session:
                action = ManualActionService(session).create_action(data=data)
                return ManualActionRead.model_validate(_manual_action(action))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/manual-actions", response_model=list[ManualActionRead])
    def list_manual_actions() -> list[ManualActionRead]:
        try:
            with session_scope() as session:
                return [ManualActionRead.model_validate(_manual_action(item)) for item in ManualActionService(session).list_actions()]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/manual-actions/{action_id}/complete", response_model=ManualActionRead)
    def complete_manual_action(action_id: uuid.UUID) -> ManualActionRead:
        try:
            with session_scope() as session:
                action = ManualActionService(session).complete_action(action_id)
                return ManualActionRead.model_validate(_manual_action(action))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/editorial-calendar-slots", response_model=EditorialCalendarSlotRead)
    def create_editorial_calendar_slot(data: EditorialCalendarSlotCreate) -> EditorialCalendarSlotRead:
        try:
            with session_scope() as session:
                slot = EditorialCalendarService(session).create_slot(data=data)
                return EditorialCalendarSlotRead.model_validate(_editorial_slot(slot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/editorial-calendar-slots/{slot_id}", response_model=EditorialCalendarSlotRead)
    def get_editorial_calendar_slot(slot_id: uuid.UUID) -> EditorialCalendarSlotRead:
        try:
            with session_scope() as session:
                slot = EditorialCalendarService(session).get_slot(slot_id)
                if slot is None:
                    raise NotFoundError(f"editorial slot not found: {slot_id}")
                return EditorialCalendarSlotRead.model_validate(_editorial_slot(slot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/search-demand-evidence", response_model=SearchDemandEvidenceRead)
    def create_search_demand_evidence(data: SearchDemandEvidenceCreate) -> SearchDemandEvidenceRead:
        try:
            with session_scope() as session:
                evidence = SearchDemandEvidenceService(session).create_evidence(data=data)
                return SearchDemandEvidenceRead.model_validate(_search_demand_evidence(evidence))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/context/retrieval-plans", response_model=RetrievalPlanSnapshotRead)
    def create_retrieval_plan(data: RetrievalPlanSnapshotCreate) -> RetrievalPlanSnapshotRead:
        try:
            with session_scope() as session:
                plan = ResourceResolverService(session).create_retrieval_plan(data=data)
                return RetrievalPlanSnapshotRead.model_validate(_retrieval_plan(plan))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/context/context-packs", response_model=ContextPackSnapshotRead)
    def create_context_pack(data: ContextPackSnapshotCreate) -> ContextPackSnapshotRead:
        try:
            with session_scope() as session:
                pack = ResourceResolverService(session).build_context_pack(data=data)
                return ContextPackSnapshotRead.model_validate(_context_pack(pack))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/context/context-packs/{context_pack_id}", response_model=ContextPackSnapshotRead)
    def get_context_pack(context_pack_id: uuid.UUID) -> ContextPackSnapshotRead:
        try:
            with session_scope() as session:
                pack = ResourceResolverService(session).get_context_pack(context_pack_id)
                if pack is None:
                    raise NotFoundError(f"context pack not found: {context_pack_id}")
                return ContextPackSnapshotRead.model_validate(_context_pack(pack))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channel-state-packs", response_model=ChannelStatePackSnapshotRead)
    def create_channel_state_pack(data: ChannelStatePackSnapshotCreate) -> ChannelStatePackSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ChannelStatePackService(session).build_snapshot(data=data)
                return ChannelStatePackSnapshotRead.model_validate(_channel_state_pack(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channel-daily-runs", response_model=ChannelDailyRunRead)
    def create_channel_daily_run(data: ChannelDailyRunCreate) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).create_run(data=data)
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/channel-daily-runs/{daily_run_id}/execute", response_model=ChannelDailyRunRead)
    def execute_channel_daily_run(daily_run_id: uuid.UUID, data: DailyRunExecuteRequest | None = None) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).execute_run(
                    daily_run_id=daily_run_id,
                    data=data or DailyRunExecuteRequest(),
                )
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/channel-daily-runs/{daily_run_id}", response_model=ChannelDailyRunRead)
    def get_channel_daily_run(daily_run_id: uuid.UUID) -> ChannelDailyRunRead:
        try:
            with session_scope() as session:
                daily_run = ChannelDailyRunService(session).get_run(daily_run_id)
                if daily_run is None:
                    raise NotFoundError(f"daily run not found: {daily_run_id}")
                return ChannelDailyRunRead.model_validate(_channel_daily_run(daily_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/daily-idea-decisions", response_model=DailyIdeaDecisionRead)
    def create_daily_idea_decision(data: DailyIdeaDecisionCreate) -> DailyIdeaDecisionRead:
        try:
            with session_scope() as session:
                decision = ChannelAuthorityService(session).create_decision(data=data)
                return DailyIdeaDecisionRead.model_validate(_daily_idea_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/daily-idea-decisions/{decision_id}", response_model=DailyIdeaDecisionRead)
    def get_daily_idea_decision(decision_id: uuid.UUID) -> DailyIdeaDecisionRead:
        try:
            with session_scope() as session:
                from app.db.models import DailyIdeaDecision

                decision = session.get(DailyIdeaDecision, decision_id)
                if decision is None:
                    raise NotFoundError(f"daily idea decision not found: {decision_id}")
                return DailyIdeaDecisionRead.model_validate(_daily_idea_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/idea-market-preflights", response_model=IdeaMarketPreflightRead)
    def create_idea_market_preflight(data: IdeaMarketPreflightCreate) -> IdeaMarketPreflightRead:
        try:
            with session_scope() as session:
                preflight = IdeaMarketPreflightService(session).create_preflight(data=data)
                return IdeaMarketPreflightRead.model_validate(_idea_market_preflight(preflight))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/project-admission-decisions", response_model=ProjectAdmissionDecisionRead)
    def create_project_admission_decision(data: ProjectAdmissionDecisionCreate) -> ProjectAdmissionDecisionRead:
        try:
            with session_scope() as session:
                decision = ProjectAdmissionService(session).create_decision(data=data)
                return ProjectAdmissionDecisionRead.model_validate(_project_admission_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/project-admission-decisions/{decision_id}", response_model=ProjectAdmissionDecisionRead)
    def get_project_admission_decision(decision_id: uuid.UUID) -> ProjectAdmissionDecisionRead:
        try:
            with session_scope() as session:
                decision = ProjectAdmissionService(session).get_decision(decision_id)
                if decision is None:
                    raise NotFoundError(f"project admission decision not found: {decision_id}")
                return ProjectAdmissionDecisionRead.model_validate(_project_admission_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/production-runs", response_model=ProductionArtifactRunRead)
    def create_production_run(data: ProductionArtifactRunCreate) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).create_run(data=data)
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/production-runs/{run_id}/execute", response_model=ProductionArtifactRunRead)
    def execute_production_run(run_id: uuid.UUID) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).execute_real_provider_flow(run_id=run_id)
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/production-runs/{run_id}", response_model=ProductionArtifactRunRead)
    def get_production_run(run_id: uuid.UUID) -> ProductionArtifactRunRead:
        try:
            with session_scope() as session:
                run = ProductionArtifactRunService(session).get_run(run_id)
                if run is None:
                    raise NotFoundError(f"production artifact run not found: {run_id}")
                return ProductionArtifactRunRead.model_validate(_production_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/render-jobs/{render_job_id}")
    def get_render_job(render_job_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                job = LocalFixtureRendererService(session).get_job(render_job_id)
                if job is None:
                    raise NotFoundError(f"render job not found: {render_job_id}")
                return _render_job(job)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/render-packages/{render_package_id}")
    def get_render_package(render_package_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                package = LocalFixtureRendererService(session).get_package(render_package_id)
                if package is None:
                    raise NotFoundError(f"render package not found: {render_package_id}")
                return _render_package(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-qc/run")
    def run_media_qc(data: QCRunRequest) -> dict[str, Any]:
        try:
            with session_scope() as session:
                if data.render_package_snapshot_id is None:
                    raise ValidationFailureError("render_package_snapshot_id is required for media QC API")
                package = LocalFixtureRendererService(session).get_package(data.render_package_snapshot_id)
                if package is None:
                    raise NotFoundError(f"render package not found: {data.render_package_snapshot_id}")
                report = MediaQCService(session).run_qc(render_package_snapshot=package)
                return _media_qc_report(report)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/accessibility-qc/run")
    def run_accessibility_qc(data: QCRunRequest) -> dict[str, Any]:
        try:
            with session_scope() as session:
                from app.db.models import CaptionTrackSnapshot, RenderPackageSnapshot

                if data.caption_track_snapshot_id is None:
                    raise ValidationFailureError("caption_track_snapshot_id is required for accessibility QC API")
                caption = session.get(CaptionTrackSnapshot, data.caption_track_snapshot_id)
                if caption is None:
                    raise NotFoundError(f"caption track snapshot not found: {data.caption_track_snapshot_id}")
                package = session.get(RenderPackageSnapshot, data.render_package_snapshot_id) if data.render_package_snapshot_id else None
                report = AccessibilityQCService(session).run_qc(
                    caption_track_snapshot=caption,
                    render_package_snapshot=package,
                )
                return _accessibility_qc_report(report)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/publish-handoffs", response_model=PublishHandoffRead)
    def create_publish_handoff(data: PublishHandoffCreate) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).create_from_render_package(data=data)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/publish-handoffs/{handoff_id}", response_model=PublishHandoffRead)
    def get_publish_handoff(handoff_id: uuid.UUID) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).require(handoff_id)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/publish-handoffs/{handoff_id}/publish-timing-suggestion", response_model=PublishTimingSuggestionRead)
    def create_publish_timing_suggestion(handoff_id: uuid.UUID) -> PublishTimingSuggestionRead:
        try:
            with session_scope() as session:
                return PublishTimingSuggestionService(session).create_for_handoff(handoff_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/publish-handoffs/{handoff_id}/mark-ready", response_model=PublishHandoffRead)
    def mark_publish_handoff_ready(handoff_id: uuid.UUID) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).mark_ready(handoff_id=handoff_id)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/localized-subtitle-packages/{package_id}", response_model=LocalizedSubtitlePackageRead)
    def get_localized_subtitle_package(package_id: uuid.UUID) -> LocalizedSubtitlePackageRead:
        try:
            with session_scope() as session:
                return LocalizedSubtitlePackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/localized-metadata-packages/{package_id}", response_model=LocalizedMetadataPackageRead)
    def get_localized_metadata_package(package_id: uuid.UUID) -> LocalizedMetadataPackageRead:
        try:
            with session_scope() as session:
                return LocalizedMetadataPackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/publish-timing-suggestions/{suggestion_id}", response_model=PublishTimingSuggestionRead)
    def get_publish_timing_suggestion(suggestion_id: uuid.UUID) -> PublishTimingSuggestionRead:
        try:
            with session_scope() as session:
                return PublishTimingSuggestionService(session).get(suggestion_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/manual-publish-confirmations", response_model=ManualPublishConfirmationRead)
    def create_manual_publish_confirmation(data: ManualPublishConfirmationCreate) -> ManualPublishConfirmationRead:
        try:
            with session_scope() as session:
                confirmation = ManualPublishConfirmationService(session).create_confirmation(data=data)
                return ManualPublishConfirmationRead.model_validate(_manual_publish_confirmation(confirmation))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/manual-publish-confirmations/{confirmation_id}", response_model=ManualPublishConfirmationRead)
    def get_manual_publish_confirmation(confirmation_id: uuid.UUID) -> ManualPublishConfirmationRead:
        try:
            with session_scope() as session:
                confirmation = ManualPublishConfirmationService(session).require_confirmation(confirmation_id)
                return ManualPublishConfirmationRead.model_validate(_manual_publish_confirmation(confirmation))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/manual-publish-confirmations/{confirmation_id}/accept", response_model=UploadedVideoRead)
    def accept_manual_publish_confirmation(confirmation_id: uuid.UUID) -> UploadedVideoRead:
        try:
            with session_scope() as session:
                uploaded = ManualPublishConfirmationService(session).accept_confirmation(confirmation_id=confirmation_id)
                return UploadedVideoRead.model_validate(_uploaded_video(uploaded))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos", response_model=list[UploadedVideoListItem])
    def list_uploaded_videos_dashboard(
        channel_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
    ) -> list[UploadedVideoListItem]:
        try:
            with session_scope() as session:
                return M11DashboardService(session).list_uploaded_videos(channel_id=channel_id, company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/dashboard", response_model=UploadedVideoDashboardRead)
    def get_uploaded_video_dashboard(uploaded_video_id: uuid.UUID) -> UploadedVideoDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).uploaded_video_dashboard(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}", response_model=UploadedVideoRead)
    def get_uploaded_video(uploaded_video_id: uuid.UUID) -> UploadedVideoRead:
        try:
            with session_scope() as session:
                uploaded = ManualPublishConfirmationService(session).get_uploaded_video(uploaded_video_id)
                if uploaded is None:
                    raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
                return UploadedVideoRead.model_validate(_uploaded_video(uploaded))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{project_id}/uploaded-videos", response_model=list[UploadedVideoRead])
    def list_project_uploaded_videos(project_id: uuid.UUID) -> list[UploadedVideoRead]:
        try:
            with session_scope() as session:
                return [
                    UploadedVideoRead.model_validate(_uploaded_video(uploaded))
                    for uploaded in ManualPublishConfirmationService(session).list_uploaded_videos_by_project(project_id)
                ]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/publication-summary", response_model=UploadedVideoPublicationSummaryRead)
    def get_uploaded_video_publication_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoPublicationSummaryRead:
        try:
            with session_scope() as session:
                summary = ManualPublishConfirmationService(session).get_publication_summary(uploaded_video_id)
                if summary is None:
                    raise NotFoundError(f"uploaded video publication summary not found: {uploaded_video_id}")
                return UploadedVideoPublicationSummaryRead.model_validate(_uploaded_video_summary(summary))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/analytics-sync-runs", response_model=AnalyticsSyncRunRead)
    def create_analytics_sync_run(data: AnalyticsSyncRunCreate) -> AnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = AnalyticsSyncService(session).create_sync_run(data=data)
                return AnalyticsSyncRunRead.model_validate(_analytics_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/analytics-sync-runs/{sync_run_id}/execute", response_model=AnalyticsSyncRunRead)
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

    @application.get("/analytics-sync-runs/{sync_run_id}", response_model=AnalyticsSyncRunRead)
    def get_analytics_sync_run(sync_run_id: uuid.UUID) -> AnalyticsSyncRunRead:
        try:
            with session_scope() as session:
                run = AnalyticsSyncService(session).require_sync_run(sync_run_id)
                return AnalyticsSyncRunRead.model_validate(_analytics_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/analytics/import-manual", response_model=AnalyticsSnapshotRead)
    def import_manual_analytics(data: ManualAnalyticsImportContract) -> AnalyticsSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).import_manual(data=data)
                return AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/analytics-snapshots/{snapshot_id}", response_model=AnalyticsSnapshotRead)
    def get_analytics_snapshot(snapshot_id: uuid.UUID) -> AnalyticsSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).require_snapshot(snapshot_id)
                return AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/analytics-snapshots", response_model=list[AnalyticsSnapshotRead])
    def list_uploaded_video_analytics_snapshots(uploaded_video_id: uuid.UUID) -> list[AnalyticsSnapshotRead]:
        try:
            with session_scope() as session:
                snapshots = AnalyticsSyncService(session).list_snapshots_by_uploaded_video(uploaded_video_id)
                return [AnalyticsSnapshotRead.model_validate(_analytics_snapshot(snapshot)) for snapshot in snapshots]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/metrics-summary", response_model=UploadedVideoMetricsSummaryRead)
    def get_uploaded_video_metrics_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoMetricsSummaryRead:
        try:
            with session_scope() as session:
                summary = AnalyticsSyncService(session).get_metrics_summary(uploaded_video_id)
                return UploadedVideoMetricsSummaryRead.model_validate(_uploaded_video_metrics_summary(summary))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/retention", response_model=RetentionCurveSnapshotRead)
    def get_uploaded_video_retention(uploaded_video_id: uuid.UUID) -> RetentionCurveSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).latest_retention(uploaded_video_id)
                if snapshot is None:
                    raise NotFoundError(f"retention snapshot not found: {uploaded_video_id}")
                return RetentionCurveSnapshotRead.model_validate(_retention_curve_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/traffic-sources", response_model=TrafficSourceSnapshotRead)
    def get_uploaded_video_traffic_sources(uploaded_video_id: uuid.UUID) -> TrafficSourceSnapshotRead:
        try:
            with session_scope() as session:
                snapshot = AnalyticsSyncService(session).latest_traffic_sources(uploaded_video_id)
                if snapshot is None:
                    raise NotFoundError(f"traffic source snapshot not found: {uploaded_video_id}")
                return TrafficSourceSnapshotRead.model_validate(_traffic_source_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/auth/youtube/start")
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

    @application.get("/auth/youtube/callback", response_model=YouTubeOAuthSessionRead)
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

    @application.get("/youtube/connection-status", response_model=YouTubeConnectionStatusRead)
    def get_youtube_connection_status() -> YouTubeConnectionStatusRead:
        try:
            with session_scope() as session:
                return YouTubeCredentialHealthService(session).connection_status()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/auth/google-drive/start")
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

    @application.get("/auth/google-drive/callback", response_model=GoogleDriveOAuthSessionRead)
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

    @application.get("/google-drive/connection-status", response_model=GoogleDriveConnectionStatusRead)
    def get_google_drive_connection_status() -> GoogleDriveConnectionStatusRead:
        try:
            with session_scope() as session:
                return GoogleDriveCredentialHealthService(session).connection_status()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media/offload-jobs", response_model=MediaOffloadJobRead)
    def create_media_offload_job(data: MediaOffloadJobCreate) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).create_job(data=data)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media/offload-jobs/{job_id}/execute", response_model=MediaOffloadJobRead)
    def execute_media_offload_job(job_id: uuid.UUID, data: MediaOffloadExecuteRequest | None = None) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).execute_job(job_id=job_id, data=data)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media/offload-jobs/{job_id}", response_model=MediaOffloadJobRead)
    def get_media_offload_job(job_id: uuid.UUID) -> MediaOffloadJobRead:
        try:
            with session_scope() as session:
                job = MediaOffloadJobService(session).require(job_id)
                return MediaOffloadJobRead.model_validate(_media_offload_job(job))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media/cloud-refs/{cloud_media_ref_id}", response_model=CloudMediaReadPayload)
    def get_cloud_media_ref(cloud_media_ref_id: uuid.UUID) -> CloudMediaReadPayload:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).dashboard_payload(cloud_media_ref_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{video_project_id}/media", response_model=list[CloudMediaReadPayload])
    def list_video_project_media(video_project_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_video_project(video_project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/render-packages/{render_package_id}/media", response_model=list[CloudMediaReadPayload])
    def list_render_package_media(render_package_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_render_package(render_package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/media", response_model=list[CloudMediaReadPayload])
    def list_uploaded_video_media(uploaded_video_id: uuid.UUID) -> list[CloudMediaReadPayload]:
        try:
            with session_scope() as session:
                return MediaCloudReadService(session).list_by_uploaded_video(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media/local-cleanup/run", response_model=LocalCleanupRunResult)
    def run_local_media_cleanup(data: LocalCleanupRunRequest | None = None) -> LocalCleanupRunResult:
        try:
            with session_scope() as session:
                request = data or LocalCleanupRunRequest()
                return LocalMediaCleanupService(session).run_pending_cleanup(dry_run=request.dry_run)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media/local-retention-policy", response_model=LocalMediaRetentionPolicyRead)
    def get_local_media_retention_policy() -> LocalMediaRetentionPolicyRead:
        try:
            with session_scope() as session:
                policy = LocalMediaRetentionPolicyService(session).get_or_create_default()
                return LocalMediaRetentionPolicyRead.model_validate(_local_media_retention_policy(policy))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/uploaded-videos/{uploaded_video_id}/youtube/public-sync", response_model=YouTubePublicSyncRunRead)
    def sync_uploaded_video_youtube_public(uploaded_video_id: uuid.UUID) -> YouTubePublicSyncRunRead:
        try:
            with session_scope() as session:
                run = YouTubePublicStatsSyncService(session).sync_uploaded_video(uploaded_video_id=uploaded_video_id)
                return YouTubePublicSyncRunRead.model_validate(_youtube_public_sync_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/youtube/public-monitor", response_model=YouTubePublicMonitorSnapshotRead | None)
    def get_uploaded_video_youtube_public_monitor(uploaded_video_id: uuid.UUID) -> YouTubePublicMonitorSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = YouTubePublicStatsSyncService(session).latest_snapshot(uploaded_video_id)
                return YouTubePublicMonitorSnapshotRead.model_validate(_youtube_public_snapshot(snapshot)) if snapshot else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/uploaded-videos/{uploaded_video_id}/youtube/owner-analytics-sync", response_model=YouTubeOwnerAnalyticsSyncRunRead)
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

    @application.get("/uploaded-videos/{uploaded_video_id}/youtube/owner-analytics", response_model=YouTubeOwnerAnalyticsSnapshotRead | None)
    def get_uploaded_video_youtube_owner_analytics(uploaded_video_id: uuid.UUID) -> YouTubeOwnerAnalyticsSnapshotRead | None:
        try:
            with session_scope() as session:
                snapshot = YouTubeOwnerAnalyticsSyncService(session).latest_snapshot(uploaded_video_id)
                return YouTubeOwnerAnalyticsSnapshotRead.model_validate(_youtube_owner_snapshot(snapshot)) if snapshot else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/youtube/follow-summary", response_model=list[UploadedVideoYouTubeFollowSummaryRead])
    def list_uploaded_video_youtube_follow_summaries() -> list[UploadedVideoYouTubeFollowSummaryRead]:
        try:
            with session_scope() as session:
                return UploadedVideoYouTubeFollowReadService(session).list_summaries()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/youtube/follow-summary", response_model=UploadedVideoYouTubeFollowSummaryRead)
    def get_uploaded_video_youtube_follow_summary(uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeFollowSummaryRead:
        try:
            with session_scope() as session:
                return UploadedVideoYouTubeFollowReadService(session).get_summary(uploaded_video_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/post-publish-health-runs", response_model=PostPublishHealthRunRead)
    def create_post_publish_health_run(data: PostPublishHealthRunCreate) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).create_health_run(data=data)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/post-publish-health-runs/{run_id}/execute", response_model=PostPublishHealthRunRead)
    def execute_post_publish_health_run(run_id: uuid.UUID) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).execute_health_run(run_id=run_id)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/post-publish-health-runs/{run_id}", response_model=PostPublishHealthRunRead)
    def get_post_publish_health_run(run_id: uuid.UUID) -> PostPublishHealthRunRead:
        try:
            with session_scope() as session:
                run = PostPublishHealthMonitorService(session).require_health_run(run_id)
                return PostPublishHealthRunRead.model_validate(_post_publish_health_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/post-publish-health", response_model=list[PostPublishHealthRunRead])
    def list_uploaded_video_post_publish_health(uploaded_video_id: uuid.UUID) -> list[PostPublishHealthRunRead]:
        try:
            with session_scope() as session:
                runs = PostPublishHealthMonitorService(session).list_health_runs_by_uploaded_video(uploaded_video_id)
                return [PostPublishHealthRunRead.model_validate(_post_publish_health_run(run)) for run in runs]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/failure-trace-reports", response_model=list[FailureTraceReportRead])
    def list_uploaded_video_failure_trace_reports(uploaded_video_id: uuid.UUID) -> list[FailureTraceReportRead]:
        try:
            with session_scope() as session:
                reports = PostPublishHealthMonitorService(session).list_failure_trace_reports_by_uploaded_video(uploaded_video_id)
                return [FailureTraceReportRead.model_validate(_failure_trace_report(report)) for report in reports]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/failure-trace-reports/{report_id}", response_model=FailureTraceReportRead)
    def get_failure_trace_report(report_id: uuid.UUID) -> FailureTraceReportRead:
        try:
            with session_scope() as session:
                report = PostPublishHealthMonitorService(session).require_failure_trace_report(report_id)
                return FailureTraceReportRead.model_validate(_failure_trace_report(report))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/uploaded-videos/{uploaded_video_id}/recovery-proposals", response_model=list[RecoveryProposalRead])
    def list_uploaded_video_recovery_proposals(uploaded_video_id: uuid.UUID) -> list[RecoveryProposalRead]:
        try:
            with session_scope() as session:
                proposals = PostPublishHealthMonitorService(session).list_recovery_proposals_by_uploaded_video(uploaded_video_id)
                return [RecoveryProposalRead.model_validate(_recovery_proposal(proposal)) for proposal in proposals]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/recovery-proposals/{proposal_id}/accept", response_model=RecoveryProposalRead)
    def accept_recovery_proposal(proposal_id: uuid.UUID) -> RecoveryProposalRead:
        try:
            with session_scope() as session:
                proposal = PostPublishHealthMonitorService(session).accept_recovery_proposal(proposal_id=proposal_id)
                return RecoveryProposalRead.model_validate(_recovery_proposal(proposal))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/recovery-proposals/{proposal_id}/reject", response_model=RecoveryProposalRead)
    def reject_recovery_proposal(proposal_id: uuid.UUID) -> RecoveryProposalRead:
        try:
            with session_scope() as session:
                proposal = PostPublishHealthMonitorService(session).reject_recovery_proposal(proposal_id=proposal_id)
                return RecoveryProposalRead.model_validate(_recovery_proposal(proposal))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/learning-candidate-generation-runs", response_model=LearningCandidateGenerationRunRead)
    def create_learning_candidate_generation_run(
        data: LearningCandidateGenerationRunCreate,
    ) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                run = LearningCandidateGenerationService(session).create_run(data=data)
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/learning-candidate-generation-runs/{run_id}/execute", response_model=LearningCandidateGenerationRunRead)
    def execute_learning_candidate_generation_run(
        run_id: uuid.UUID,
        data: LearningCandidateGenerationRunExecuteRequest | None = None,
    ) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                request = data or LearningCandidateGenerationRunExecuteRequest()
                run = LearningCandidateGenerationService(session).execute_run(
                    run_id=run_id,
                    correlation_id=request.correlation_id or "api-m10-learning-generation-execute",
                )
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/learning-candidate-generation-runs/{run_id}", response_model=LearningCandidateGenerationRunRead)
    def get_learning_candidate_generation_run(run_id: uuid.UUID) -> LearningCandidateGenerationRunRead:
        try:
            with session_scope() as session:
                run = LearningCandidateGenerationService(session).require_run(run_id)
                return LearningCandidateGenerationRunRead.model_validate(_learning_generation_run(run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/learning-candidates", response_model=list[LearningCandidateRead])
    def list_learning_candidates(
        candidate_state: str | None = None,
        company_id: uuid.UUID | None = None,
        uploaded_video_id: uuid.UUID | None = None,
    ) -> list[LearningCandidateRead]:
        try:
            with session_scope() as session:
                candidates = LearningReadService(session).list_candidates(
                    candidate_state=candidate_state,
                    company_id=company_id,
                    uploaded_video_id=uploaded_video_id,
                )
                return [LearningCandidateRead.model_validate(_learning_candidate(candidate)) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/learning-candidates/{candidate_id}", response_model=LearningCandidateRead)
    def get_learning_candidate(candidate_id: uuid.UUID) -> LearningCandidateRead:
        try:
            with session_scope() as session:
                candidate = LearningReadService(session).require_candidate(candidate_id)
                return LearningCandidateRead.model_validate(_learning_candidate(candidate))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/learning-candidates/{candidate_id}/evidence-bundle", response_model=LearningEvidenceBundleRead)
    def get_learning_candidate_evidence_bundle(candidate_id: uuid.UUID) -> LearningEvidenceBundleRead:
        try:
            with session_scope() as session:
                bundle = LearningReadService(session).require_evidence_bundle_for_candidate(candidate_id)
                return LearningEvidenceBundleRead.model_validate(_learning_evidence_bundle(bundle))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/learning-candidates/{candidate_id}/approve", response_model=LearningReviewDecisionRead)
    def approve_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "APPROVE", data)

    @application.post("/learning-candidates/{candidate_id}/reject", response_model=LearningReviewDecisionRead)
    def reject_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "REJECT", data)

    @application.post("/learning-candidates/{candidate_id}/request-more-evidence", response_model=LearningReviewDecisionRead)
    def request_more_learning_evidence(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "REQUEST_MORE_EVIDENCE", data)

    @application.post("/learning-candidates/{candidate_id}/suppress", response_model=LearningReviewDecisionRead)
    def suppress_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "SUPPRESS", data)

    @application.post("/learning-candidates/{candidate_id}/expire", response_model=LearningReviewDecisionRead)
    def expire_learning_candidate(
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate | None = None,
    ) -> LearningReviewDecisionRead:
        return _learning_review_action(candidate_id, "EXPIRE", data)

    @application.get("/learning-review-queue", response_model=list[LearningReviewQueueItemRead])
    def list_learning_review_queue(
        queue_state: str | None = None,
        company_id: uuid.UUID | None = None,
    ) -> list[LearningReviewQueueItemRead]:
        try:
            with session_scope() as session:
                items = LearningReviewQueueService(session).list_queue(queue_state=queue_state, company_id=company_id)
                return [LearningReviewQueueItemRead.model_validate(_learning_review_queue_item(item)) for item in items]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/learning-review-queue/{queue_item_id}", response_model=LearningReviewQueueItemRead)
    def get_learning_review_queue_item(queue_item_id: uuid.UUID) -> LearningReviewQueueItemRead:
        try:
            with session_scope() as session:
                item = LearningReadService(session).require_queue_item(queue_item_id)
                return LearningReviewQueueItemRead.model_validate(_learning_review_queue_item(item))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/playbook-candidate-drafts/{draft_id}", response_model=PlaybookCandidateDraftRead)
    def get_playbook_candidate_draft(draft_id: uuid.UUID) -> PlaybookCandidateDraftRead:
        try:
            with session_scope() as session:
                draft = LearningReadService(session).require_playbook_candidate_draft(draft_id)
                return PlaybookCandidateDraftRead.model_validate(_playbook_candidate_draft(draft))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/llm-router/profiles", response_model=list[LLMRouterProfileRead])
    def list_llm_router_profiles() -> list[LLMRouterProfileRead]:
        try:
            with session_scope() as session:
                profiles = LLMRouterConfigLoader(session).list_profiles()
                return [LLMRouterProfileRead.model_validate(profile) for profile in profiles]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/llm-router/profiles/{profile_key}", response_model=LLMRouterProfileRead)
    def get_llm_router_profile(profile_key: str) -> LLMRouterProfileRead:
        try:
            with session_scope() as session:
                return LLMRouterProfileRead.model_validate(LLMRouterConfigLoader(session).get_profile(profile_key))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/llm-router/lanes", response_model=list[LLMRouterLaneRead])
    def list_llm_router_lanes(profile_key: str = "default") -> list[LLMRouterLaneRead]:
        try:
            with session_scope() as session:
                lanes = LLMRouterConfigLoader(session).list_lanes(profile_key=profile_key)
                return [LLMRouterLaneRead.model_validate(lane) for lane in lanes]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/llm-router/route", response_model=LLMRouteResponse)
    def route_llm_request(data: LLMRouteRequest) -> LLMRouteResponse:
        try:
            with session_scope() as session:
                return LLMRouterService(session).route(
                    lane_name=data.lane_name,
                    prompt=data.prompt,
                    messages=data.messages,
                    requested_task_type=data.requested_task_type,
                    response_format=data.response_format,
                    profile_key=data.profile_key,
                    correlation_id="api-m10-1-llm-route",
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/llm-router/smoke-test", response_model=LLMRouterSmokeTestRead)
    def run_llm_router_smoke_test(data: LLMRouterSmokeTestRequest | None = None) -> LLMRouterSmokeTestRead:
        try:
            with session_scope() as session:
                request = data or LLMRouterSmokeTestRequest()
                return LLMRouterSmokeTestRead.model_validate(LLMRouterService(session).run_smoke_test(profile_key=request.profile_key))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/prompt-registry/sync", response_model=PromptRegistrySyncSummary)
    def sync_prompt_registry() -> PromptRegistrySyncSummary:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).sync_repo_registry()
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/prompt-registry/render", response_model=PromptRenderResult)
    def render_prompt(data: PromptRenderRequest) -> PromptRenderResult:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).render_prompt(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/prompt-registry/validate-output", response_model=PromptOutputValidationResult)
    def validate_prompt_output(data: PromptOutputValidationRequest) -> PromptOutputValidationResult:
        try:
            with session_scope() as session:
                return PromptRegistryService(session).validate_output(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/prompt-registry/evaluations/run", response_model=list[PromptEvaluationRunRead])
    def run_prompt_evaluations() -> list[PromptEvaluationRunRead]:
        try:
            with session_scope() as session:
                runs = PromptRegistryService(session).run_evaluation_cases()
                return [PromptEvaluationRunRead.model_validate(run) for run in runs]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-packages/first-scripted", response_model=FirstScriptedVideoPackageRead)
    def create_first_scripted_video_package(data: FirstScriptedVideoPackageRequest) -> FirstScriptedVideoPackageRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).create(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-packages/{package_id}", response_model=FirstScriptedVideoPackageRead)
    def get_first_scripted_video_package(package_id: uuid.UUID) -> FirstScriptedVideoPackageRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).get(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-packages/{package_id}/review", response_model=FirstScriptedVideoPackageReviewRead)
    def get_first_scripted_video_package_review(package_id: uuid.UUID) -> FirstScriptedVideoPackageReviewRead:
        try:
            with session_scope() as session:
                return FirstScriptedVideoPackageService(session).review(package_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/short-candidates/extract", response_model=list[ShortCandidateRead])
    def extract_short_candidates(
        video_project_id: uuid.UUID,
        data: ShortCandidateExtractRequest | None = None,
    ) -> list[ShortCandidateRead]:
        try:
            with session_scope() as session:
                request = data or ShortCandidateExtractRequest()
                candidates = ShortCandidateExtractionService(session).extract_for_project(video_project_id=video_project_id, data=request)
                return [ShortCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{video_project_id}/short-candidates", response_model=list[ShortCandidateRead])
    def list_short_candidates(video_project_id: uuid.UUID) -> list[ShortCandidateRead]:
        try:
            with session_scope() as session:
                candidates = ShortCandidateExtractionService(session).list_for_project(video_project_id)
                return [ShortCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/short-candidates/{short_candidate_id}", response_model=ShortCandidateRead)
    def get_short_candidate(short_candidate_id: uuid.UUID) -> ShortCandidateRead:
        try:
            with session_scope() as session:
                from app.db.models import ShortCandidate

                candidate = session.get(ShortCandidate, short_candidate_id)
                if candidate is None:
                    raise NotFoundError("short candidate not found")
                return ShortCandidateRead.model_validate(candidate)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/short-candidates/{short_candidate_id}/rank", response_model=ShortCandidateScoreRead)
    def rank_short_candidate(
        short_candidate_id: uuid.UUID,
        data: ShortCandidateRankRequest | None = None,
    ) -> ShortCandidateScoreRead:
        try:
            with session_scope() as session:
                score = ShortCandidateRankingService(session).rank(short_candidate_id=short_candidate_id, data=data or ShortCandidateRankRequest())
                return ShortCandidateScoreRead.model_validate(score)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/video-projects/{video_project_id}/derivative-graph", response_model=list[ContentDerivativeGraphEdgeRead])
    def get_derivative_graph(video_project_id: uuid.UUID) -> list[ContentDerivativeGraphEdgeRead]:
        try:
            with session_scope() as session:
                edges = DerivativeGraphService(session).graph_for_project(video_project_id)
                return [ContentDerivativeGraphEdgeRead.model_validate(edge) for edge in edges]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/derivative-graph/edges/{edge_id}", response_model=ContentDerivativeGraphEdgeRead)
    def get_derivative_graph_edge(edge_id: uuid.UUID) -> ContentDerivativeGraphEdgeRead:
        try:
            with session_scope() as session:
                return ContentDerivativeGraphEdgeRead.model_validate(DerivativeGraphService(session).require_edge(edge_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/derivative-graph/edges", response_model=ContentDerivativeGraphEdgeRead)
    def create_derivative_graph_edge(data: ContentDerivativeGraphEdgeCreate) -> ContentDerivativeGraphEdgeRead:
        try:
            with session_scope() as session:
                return ContentDerivativeGraphEdgeRead.model_validate(DerivativeGraphService(session).create_edge(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/derivative-originality-checks", response_model=DerivativeOriginalityCheckRead)
    def create_derivative_originality_check(data: DerivativeOriginalityCheckCreate) -> DerivativeOriginalityCheckRead:
        try:
            with session_scope() as session:
                return DerivativeOriginalityCheckRead.model_validate(DerivativeOriginalityService(session).create_check(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/derivative-originality-checks/{check_id}", response_model=DerivativeOriginalityCheckRead)
    def get_derivative_originality_check(check_id: uuid.UUID) -> DerivativeOriginalityCheckRead:
        try:
            with session_scope() as session:
                return DerivativeOriginalityCheckRead.model_validate(DerivativeOriginalityService(session).require_check(check_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/reusable-artifacts", response_model=list[ReusableArtifactRead])
    def list_reusable_artifacts(company_id: uuid.UUID | None = None) -> list[ReusableArtifactRead]:
        try:
            with session_scope() as session:
                artifacts = ReusableArtifactService(session).list(company_id=company_id)
                return [ReusableArtifactRead.model_validate(artifact) for artifact in artifacts]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/reusable-artifacts", response_model=ReusableArtifactRead)
    def create_reusable_artifact(data: ReusableArtifactCreate) -> ReusableArtifactRead:
        try:
            with session_scope() as session:
                return ReusableArtifactRead.model_validate(ReusableArtifactService(session).create(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/reusable-artifacts/{artifact_id}", response_model=ReusableArtifactRead)
    def get_reusable_artifact(artifact_id: uuid.UUID) -> ReusableArtifactRead:
        try:
            with session_scope() as session:
                return ReusableArtifactRead.model_validate(ReusableArtifactService(session).require(artifact_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/asset-reuse-index/search", response_model=list[AssetReuseIndexEntryRead])
    def search_asset_reuse_index(data: AssetReuseSearchRequest) -> list[AssetReuseIndexEntryRead]:
        try:
            with session_scope() as session:
                entries = AssetReuseIndexService(session).search(data=data)
                return [AssetReuseIndexEntryRead.model_validate(entry) for entry in entries]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/cross-platform-funnel-packages", response_model=CrossPlatformFunnelPackageRead)
    def create_cross_platform_funnel_package(data: CrossPlatformFunnelPackageCreate) -> CrossPlatformFunnelPackageRead:
        try:
            with session_scope() as session:
                package = CrossPlatformFunnelPackageService(session).create(data=data)
                return CrossPlatformFunnelPackageRead.model_validate(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/cross-platform-funnel-packages/{package_id}", response_model=CrossPlatformFunnelPackageRead)
    def get_cross_platform_funnel_package(package_id: uuid.UUID) -> CrossPlatformFunnelPackageRead:
        try:
            with session_scope() as session:
                return CrossPlatformFunnelPackageRead.model_validate(CrossPlatformFunnelPackageService(session).require(package_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/cross-platform-funnel-packages/{package_id}/build-upload-cards", response_model=list[UploadCardRead])
    def build_cross_platform_upload_cards(
        package_id: uuid.UUID,
        data: BuildUploadCardsRequest | None = None,
    ) -> list[UploadCardRead]:
        try:
            with session_scope() as session:
                cards = CrossPlatformFunnelPackageService(session).build_upload_cards(
                    package_id=package_id,
                    data=data or BuildUploadCardsRequest(),
                )
                return [UploadCardRead.model_validate(card) for card in cards]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/upload-cards/{upload_card_id}", response_model=UploadCardRead)
    def get_upload_card(upload_card_id: uuid.UUID) -> UploadCardRead:
        try:
            with session_scope() as session:
                return UploadCardRead.model_validate(UploadCardService(session).require(upload_card_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/human-upload-tasks", response_model=list[HumanUploadTaskRead])
    def list_human_upload_tasks(task_state: str | None = None) -> list[HumanUploadTaskRead]:
        try:
            with session_scope() as session:
                tasks = HumanUploadTaskService(session).list(task_state=task_state)
                return [HumanUploadTaskRead.model_validate(task) for task in tasks]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/human-upload-tasks/{task_id}", response_model=HumanUploadTaskRead)
    def get_human_upload_task(task_id: uuid.UUID) -> HumanUploadTaskRead:
        try:
            with session_scope() as session:
                return HumanUploadTaskRead.model_validate(HumanUploadTaskService(session).require(task_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/promote-short-to-long-candidates", response_model=PromoteShortToLongCandidateRead)
    def create_promote_short_to_long_candidate(data: PromoteShortToLongCandidateCreate) -> PromoteShortToLongCandidateRead:
        try:
            with session_scope() as session:
                return PromoteShortToLongCandidateRead.model_validate(PromoteShortToLongCandidateService(session).create(data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/promote-short-to-long-candidates", response_model=list[PromoteShortToLongCandidateRead])
    def list_promote_short_to_long_candidates() -> list[PromoteShortToLongCandidateRead]:
        try:
            with session_scope() as session:
                candidates = PromoteShortToLongCandidateService(session).list()
                return [PromoteShortToLongCandidateRead.model_validate(candidate) for candidate in candidates]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/promote-short-to-long-candidates/{candidate_id}", response_model=PromoteShortToLongCandidateRead)
    def get_promote_short_to_long_candidate(candidate_id: uuid.UUID) -> PromoteShortToLongCandidateRead:
        try:
            with session_scope() as session:
                return PromoteShortToLongCandidateRead.model_validate(PromoteShortToLongCandidateService(session).require(candidate_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-roles", response_model=list[MediaProviderRoleProfileRead])
    def list_media_provider_roles() -> list[MediaProviderRoleProfileRead]:
        try:
            with session_scope() as session:
                roles = MediaProviderRoleService(session).list_roles()
                return [MediaProviderRoleProfileRead.model_validate(role) for role in roles]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-roles/{provider_key}", response_model=MediaProviderRoleProfileRead)
    def get_media_provider_role(provider_key: str) -> MediaProviderRoleProfileRead:
        try:
            with session_scope() as session:
                return MediaProviderRoleProfileRead.model_validate(MediaProviderRoleService(session).require_role(provider_key))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-capabilities", response_model=list[ProviderCapabilityMatrixEntryRead])
    def list_media_provider_capabilities(provider_key: str | None = None) -> list[ProviderCapabilityMatrixEntryRead]:
        try:
            with session_scope() as session:
                entries = ProviderCapabilityMatrixService(session).list_entries(provider_key=provider_key)
                return [ProviderCapabilityMatrixEntryRead.model_validate(entry) for entry in entries]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-capabilities/{provider_key}", response_model=list[ProviderCapabilityMatrixEntryRead])
    def get_media_provider_capabilities(provider_key: str) -> list[ProviderCapabilityMatrixEntryRead]:
        try:
            with session_scope() as session:
                entries = ProviderCapabilityMatrixService(session).list_entries(provider_key=provider_key)
                return [ProviderCapabilityMatrixEntryRead.model_validate(entry) for entry in entries]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-render-routing/decide", response_model=MediaRenderRoutingDecisionRead)
    def decide_media_render_route(data: MediaRenderRoutingDecisionRequest) -> MediaRenderRoutingDecisionRead:
        try:
            with session_scope() as session:
                decision = MediaRenderJobRouterService(session).decide(data=data)
                return MediaRenderRoutingDecisionRead.model_validate(decision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-render-routing/decisions/{decision_id}", response_model=MediaRenderRoutingDecisionRead)
    def get_media_render_routing_decision(decision_id: uuid.UUID) -> MediaRenderRoutingDecisionRead:
        try:
            with session_scope() as session:
                return MediaRenderRoutingDecisionRead.model_validate(MediaRenderJobRouterService(session).get_decision(decision_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/long-form-render-package", response_model=LongFormRenderPackageRead)
    def create_long_form_render_package(
        video_project_id: uuid.UUID,
        data: LongFormRenderPackageCreate | None = None,
    ) -> LongFormRenderPackageRead:
        try:
            with session_scope() as session:
                package = LongFormRenderPackageService(session).create(
                    video_project_id=video_project_id,
                    data=data or LongFormRenderPackageCreate(),
                )
                return LongFormRenderPackageRead.model_validate(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/long-form-render-packages/{package_id}", response_model=LongFormRenderPackageRead)
    def get_long_form_render_package(package_id: uuid.UUID) -> LongFormRenderPackageRead:
        try:
            with session_scope() as session:
                return LongFormRenderPackageRead.model_validate(LongFormRenderPackageService(session).require(package_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/short-candidates/{short_candidate_id}/short-render-package", response_model=ShortRenderPackageRead)
    def create_short_render_package(
        short_candidate_id: uuid.UUID,
        data: ShortRenderPackageCreate | None = None,
    ) -> ShortRenderPackageRead:
        try:
            with session_scope() as session:
                package = ShortRenderPackageService(session).create(
                    short_candidate_id=short_candidate_id,
                    data=data or ShortRenderPackageCreate(),
                )
                return ShortRenderPackageRead.model_validate(package)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/short-render-packages/{package_id}", response_model=ShortRenderPackageRead)
    def get_short_render_package(package_id: uuid.UUID) -> ShortRenderPackageRead:
        try:
            with session_scope() as session:
                return ShortRenderPackageRead.model_validate(ShortRenderPackageService(session).require(package_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/ai-hero-assets/plan", response_model=AIHeroAssetRead)
    def plan_ai_hero_asset(video_project_id: uuid.UUID, data: AIHeroAssetPlanRequest) -> AIHeroAssetRead:
        try:
            with session_scope() as session:
                return AIHeroAssetRead.model_validate(AIHeroAssetPlanningService(session).plan(video_project_id=video_project_id, data=data))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/ai-hero-assets/{asset_id}", response_model=AIHeroAssetRead)
    def get_ai_hero_asset(asset_id: uuid.UUID) -> AIHeroAssetRead:
        try:
            with session_scope() as session:
                return AIHeroAssetRead.model_validate(AIHeroAssetPlanningService(session).require(asset_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/ai-hero-assets/{asset_id}/generate", response_model=AIHeroGenerationJobRead)
    def generate_ai_hero_asset(
        asset_id: uuid.UUID,
        data: AIHeroGenerationExecuteRequest | None = None,
    ) -> AIHeroGenerationJobRead:
        try:
            with session_scope() as session:
                return AIHeroGenerationService(session).execute(asset_id=asset_id, data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/creatomate-assets/plan", response_model=CreatomateRenderAssetRead)
    def plan_creatomate_render_asset(video_project_id: uuid.UUID, data: CreatomateRenderAssetPlanRequest) -> CreatomateRenderAssetRead:
        try:
            with session_scope() as session:
                asset = CreatomateRenderAssetPlanningService(session).plan(video_project_id=video_project_id, data=data)
                return CreatomateRenderAssetRead.model_validate(asset)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/creatomate-render-assets/{asset_id}", response_model=CreatomateRenderAssetRead)
    def get_creatomate_render_asset(asset_id: uuid.UUID) -> CreatomateRenderAssetRead:
        try:
            with session_scope() as session:
                return CreatomateRenderAssetRead.model_validate(CreatomateRenderAssetPlanningService(session).require(asset_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/video-projects/{video_project_id}/thumbnail-variants/plan", response_model=list[ThumbnailVariantRead])
    def plan_thumbnail_variants(video_project_id: uuid.UUID, data: ThumbnailVariantPlanRequest) -> list[ThumbnailVariantRead]:
        try:
            with session_scope() as session:
                variants = ThumbnailVariantPlanningService(session).plan(video_project_id=video_project_id, data=data)
                return [ThumbnailVariantRead.model_validate(variant) for variant in variants]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/thumbnail-variants/{variant_id}", response_model=ThumbnailVariantRead)
    def get_thumbnail_variant(variant_id: uuid.UUID) -> ThumbnailVariantRead:
        try:
            with session_scope() as session:
                return ThumbnailVariantRead.model_validate(ThumbnailVariantPlanningService(session).require(variant_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-budgets", response_model=list[MediaProviderBudgetPolicyRead])
    def list_media_provider_budget_policies() -> list[MediaProviderBudgetPolicyRead]:
        try:
            with session_scope() as session:
                policies = MediaProviderBudgetService(session).list_policies()
                return [MediaProviderBudgetPolicyRead.model_validate(policy) for policy in policies]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.get("/media-provider-budgets/snapshot", response_model=list[MediaProviderBudgetSnapshotRead])
    def list_media_provider_budget_snapshots() -> list[MediaProviderBudgetSnapshotRead]:
        try:
            with session_scope() as session:
                snapshots = MediaProviderBudgetService(session).latest_snapshots()
                return [MediaProviderBudgetSnapshotRead.model_validate(snapshot) for snapshot in snapshots]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-provider-gates/capability/check", response_model=ProviderCapabilityGateRead)
    def check_media_provider_capability_gate(data: ProviderCapabilityGateCheckRequest) -> ProviderCapabilityGateRead:
        try:
            with session_scope() as session:
                return ProviderCapabilityGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-provider-gates/license/check", response_model=LicenseEvidenceGateRead)
    def check_media_provider_license_gate(data: LicenseEvidenceGateCheckRequest) -> LicenseEvidenceGateRead:
        try:
            with session_scope() as session:
                return LicenseEvidenceGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-provider-gates/budget/check", response_model=MediaProviderBudgetGateRead)
    def check_media_provider_budget_gate(data: MediaProviderBudgetCheckRequest) -> MediaProviderBudgetGateRead:
        try:
            with session_scope() as session:
                return MediaProviderBudgetService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-provider-gates/reused-content/check", response_model=ReusedContentRiskGateRead)
    def check_reused_content_risk_gate(data: ReusedContentRiskGateCheckRequest) -> ReusedContentRiskGateRead:
        try:
            with session_scope() as session:
                return ReusedContentRiskGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @application.post("/media-provider-gates/media-qc/check", response_model=MediaQCGateRead)
    def check_media_qc_gate(data: MediaQCGateCheckRequest) -> MediaQCGateRead:
        try:
            with session_scope() as session:
                return MediaQCGateService(session).check(data=data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

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


def _video_project(project: Any) -> dict[str, Any]:
    return {
        "id": project.id,
        "company_id": project.company_id,
        "channel_workspace_id": project.channel_workspace_id,
        "policy_snapshot_id": project.policy_snapshot_id,
        "title": project.title,
        "description": project.description,
        "status": project.status,
        "project_type": project.project_type,
        "priority": project.priority,
        "owner_user_id": project.owner_user_id,
        "created_by_user_id": project.created_by_user_id,
        "financial_summary": project.financial_summary,
        "brand_safety_summary": project.brand_safety_summary,
        "legal_compliance_summary": project.legal_compliance_summary,
        "audience_delivery_summary": project.audience_delivery_summary,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }

def _artifact(artifact: Any) -> dict[str, Any]:
    return {
        "id": artifact.id,
        "video_project_id": artifact.video_project_id,
        "artifact_type": artifact.artifact_type,
        "current_version_id": artifact.current_version_id,
        "status": artifact.status,
        "created_by_user_id": artifact.created_by_user_id,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
    }

def _artifact_version(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "artifact_id": version.artifact_id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "content": version.content,
        "content_hash": version.content_hash,
        "status": version.status,
        "created_by_user_id": version.created_by_user_id,
        "external_entity_refs": version.external_entity_refs,
        "packaging_metadata": version.packaging_metadata,
        "media_qc_metadata": version.media_qc_metadata,
        "source_manifest": version.source_manifest,
        "evidence_refs": version.evidence_refs,
        "context_refs": version.context_refs,
        "claim_refs": version.claim_refs,
        "retrieval_plan_ref": version.retrieval_plan_ref,
        "created_at": version.created_at,
    }

def _review_task(review_task: Any) -> dict[str, Any]:
    return {
        "id": review_task.id,
        "video_project_id": review_task.video_project_id,
        "target_type": review_task.target_type,
        "target_id": review_task.target_id,
        "target_artifact_version_id": review_task.target_artifact_version_id,
        "review_type": review_task.review_type,
        "status": review_task.status,
        "assigned_to_user_id": review_task.assigned_to_user_id,
        "requested_by_user_id": review_task.requested_by_user_id,
        "due_at": review_task.due_at,
        "review_reason_codes": review_task.review_reason_codes,
        "evidence_required": review_task.evidence_required,
        "evidence_refs": review_task.evidence_refs,
        "review_scope": review_task.review_scope,
        "context_pack_ref": review_task.context_pack_ref,
        "created_at": review_task.created_at,
        "updated_at": review_task.updated_at,
    }

def _review_finding(finding: Any) -> dict[str, Any]:
    return {
        "id": finding.id,
        "review_task_id": finding.review_task_id,
        "severity": finding.severity,
        "reason_code": finding.reason_code,
        "finding_text": finding.finding_text,
        "evidence_refs": finding.evidence_refs,
        "created_by_user_id": finding.created_by_user_id,
        "created_at": finding.created_at,
    }

def _revision_request(revision: Any) -> dict[str, Any]:
    return {
        "id": revision.id,
        "review_task_id": revision.review_task_id,
        "target_artifact_version_id": revision.target_artifact_version_id,
        "requested_by_user_id": revision.requested_by_user_id,
        "reason": revision.reason,
        "status": revision.status,
        "resolved_by_artifact_version_id": revision.resolved_by_artifact_version_id,
        "created_at": revision.created_at,
        "resolved_at": revision.resolved_at,
    }

def _approval_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "target_type": decision.target_type,
        "target_id": decision.target_id,
        "target_artifact_version_id": decision.target_artifact_version_id,
        "decision": decision.decision,
        "decided_by_user_id": decision.decided_by_user_id,
        "decided_at": decision.decided_at,
        "rationale": decision.rationale,
        "metadata": decision.metadata_,
        "decision_basis": decision.decision_basis,
        "evidence_basis": decision.evidence_basis,
        "policy_basis": decision.policy_basis,
        "context_pack_ref": decision.context_pack_ref,
        "human_decision_note": decision.human_decision_note,
        "created_at": decision.created_at,
    }

def _gate_run(gate_run: Any) -> dict[str, Any]:
    return {
        "id": gate_run.id,
        "gate_definition_version_id": gate_run.gate_definition_version_id,
        "gate_key": gate_run.gate_key,
        "target_type": gate_run.target_type,
        "target_id": gate_run.target_id,
        "video_project_id": gate_run.video_project_id,
        "artifact_version_id": gate_run.artifact_version_id,
        "review_task_id": gate_run.review_task_id,
        "policy_snapshot_id": gate_run.policy_snapshot_id,
        "input_snapshot": gate_run.input_snapshot,
        "input_snapshot_hash": gate_run.input_snapshot_hash,
        "result": gate_run.result,
        "reason_codes": gate_run.reason_codes,
        "evidence_refs": gate_run.evidence_refs,
        "metric_refs": gate_run.metric_refs,
        "freshness_state": gate_run.freshness_state,
        "confidence_level": gate_run.confidence_level,
        "confidence_reason_codes": gate_run.confidence_reason_codes,
        "decision_basis": gate_run.decision_basis,
        "created_review_task_id": gate_run.created_review_task_id,
        "created_by_user_id": gate_run.created_by_user_id,
        "created_at": gate_run.created_at,
    }

def _policy_catalog(catalog: Any) -> dict[str, Any]:
    return {
        "id": catalog.id,
        "catalog_key": catalog.catalog_key,
        "platform": catalog.platform,
        "policy_domain": catalog.policy_domain,
        "current_version_id": catalog.current_version_id,
        "status": catalog.status,
        "created_at": catalog.created_at,
        "updated_at": catalog.updated_at,
    }

def _policy_version(version: Any) -> dict[str, Any]:
    return {
        "id": version.id,
        "catalog_id": version.catalog_id,
        "version": version.version,
        "status": version.status,
        "effective_at": version.effective_at,
        "observed_at": version.observed_at,
        "policy_blob": version.policy_blob,
        "interpretation_notes": version.interpretation_notes,
        "created_by_user_id": version.created_by_user_id,
        "created_at": version.created_at,
        "activated_at": version.activated_at,
        "superseded_at": version.superseded_at,
    }

def _policy_source_ref(ref: Any) -> dict[str, Any]:
    return {
        "id": ref.id,
        "policy_version_id": ref.policy_version_id,
        "policy_change_record_id": ref.policy_change_record_id,
        "source_type": ref.source_type,
        "source_title": ref.source_title,
        "source_url": ref.source_url,
        "captured_at": ref.captured_at,
        "reliability": ref.reliability,
        "notes": ref.notes,
        "created_at": ref.created_at,
    }

def _policy_change_record(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "change_key": record.change_key,
        "platform": record.platform,
        "policy_domain": record.policy_domain,
        "state": record.state,
        "summary": record.summary,
        "old_policy_version_id": record.old_policy_version_id,
        "new_policy_version_id": record.new_policy_version_id,
        "impact_classification": record.impact_classification,
        "diff_summary": record.diff_summary,
        "affected_gate_keys": record.affected_gate_keys,
        "affected_domains": record.affected_domains,
        "requires_revalidation": record.requires_revalidation,
        "rollback_available": record.rollback_available,
        "created_by_user_id": record.created_by_user_id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }

def _policy_revalidation_batch(batch: Any) -> dict[str, Any]:
    return {
        "id": batch.id,
        "policy_change_record_id": batch.policy_change_record_id,
        "gate_definition_version_id": batch.gate_definition_version_id,
        "scope": batch.scope,
        "status": batch.status,
        "counts": batch.counts,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
        "created_by_user_id": batch.created_by_user_id,
        "created_at": batch.created_at,
    }

def _provider_registry_entry(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "provider_key": entry.provider_key,
        "provider_name": entry.provider_name,
        "provider_type": entry.provider_type,
        "status": entry.status,
        "capability_blob": entry.capability_blob,
        "policy_fit_blob": entry.policy_fit_blob,
        "cost_model_blob": entry.cost_model_blob,
        "quota_model_blob": entry.quota_model_blob,
        "retry_policy_blob": entry.retry_policy_blob,
        "metadata": entry.metadata_,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }

def _credential_reference(reference: Any) -> dict[str, Any]:
    return {
        "id": reference.id,
        "provider_key": reference.provider_key,
        "credential_key": reference.credential_key,
        "credential_type": reference.credential_type,
        "secret_ref": reference.secret_ref,
        "scope_blob": reference.scope_blob,
        "status": reference.status,
        "expires_at": reference.expires_at,
        "last_checked_at": reference.last_checked_at,
        "metadata": reference.metadata_,
        "created_at": reference.created_at,
        "updated_at": reference.updated_at,
    }

def _credential_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "credential_reference_id": snapshot.credential_reference_id,
        "provider_key": snapshot.provider_key,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _quota_account(account: Any) -> dict[str, Any]:
    return {
        "id": account.id,
        "provider_key": account.provider_key,
        "quota_scope_type": account.quota_scope_type,
        "quota_scope_id": account.quota_scope_id,
        "quota_window": account.quota_window,
        "quota_limit": account.quota_limit,
        "quota_used": account.quota_used,
        "quota_reserved": account.quota_reserved,
        "unit": account.unit,
        "reset_at": account.reset_at,
        "status": account.status,
        "metadata": account.metadata_,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }

def _quota_event(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "quota_account_id": event.quota_account_id,
        "provider_key": event.provider_key,
        "event_type": event.event_type,
        "amount": event.amount,
        "unit": event.unit,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "reason_code": event.reason_code,
        "metadata": event.metadata_,
        "created_at": event.created_at,
    }

def _cost_event(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "provider_key": event.provider_key,
        "cost_scope_type": event.cost_scope_type,
        "cost_scope_id": event.cost_scope_id,
        "amount": event.amount,
        "currency": event.currency,
        "cost_type": event.cost_type,
        "unit_count": event.unit_count,
        "unit_type": event.unit_type,
        "provider_run_ref": event.provider_run_ref,
        "metadata": event.metadata_,
        "created_at": event.created_at,
    }

def _budget_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "policy_key": policy.policy_key,
        "scope_type": policy.scope_type,
        "scope_id": policy.scope_id,
        "policy_blob": policy.policy_blob,
        "status": policy.status,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }

def _provider_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "provider_key": snapshot.provider_key,
        "provider_type": snapshot.provider_type,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "latency_ms": snapshot.latency_ms,
        "error_rate": snapshot.error_rate,
        "quota_state": snapshot.quota_state,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _component_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "component_type": snapshot.component_type,
        "component_key": snapshot.component_key,
        "health_state": snapshot.health_state,
        "checked_at": snapshot.checked_at,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _system_health(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "captured_at": snapshot.captured_at,
        "overall_state": snapshot.overall_state,
        "component_counts": snapshot.component_counts,
        "active_incident_count": snapshot.active_incident_count,
        "action_required": snapshot.action_required,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "metadata": snapshot.metadata_,
        "created_at": snapshot.created_at,
    }

def _retry_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "policy_key": policy.policy_key,
        "provider_key": policy.provider_key,
        "target_type": policy.target_type,
        "policy_blob": policy.policy_blob,
        "status": policy.status,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }

def _provider_attempt(attempt: Any) -> dict[str, Any]:
    return {
        "id": attempt.id,
        "provider_key": attempt.provider_key,
        "operation_key": attempt.operation_key,
        "target_type": attempt.target_type,
        "target_id": attempt.target_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "error_code": attempt.error_code,
        "error_message_redacted": attempt.error_message_redacted,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "latency_ms": attempt.latency_ms,
        "cost_event_id": attempt.cost_event_id,
        "quota_event_id": attempt.quota_event_id,
        "metadata": attempt.metadata_,
    }

def _dead_letter_job(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "queue_name": job.queue_name,
        "job_type": job.job_type,
        "payload_ref": job.payload_ref,
        "target_type": job.target_type,
        "target_id": job.target_id,
        "fail_count": job.fail_count,
        "first_failed_at": job.first_failed_at,
        "last_failed_at": job.last_failed_at,
        "replay_state": job.replay_state,
        "reason_code": job.reason_code,
        "next_action": job.next_action,
        "metadata": job.metadata_,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

def _ops_incident(incident: Any) -> dict[str, Any]:
    return {
        "id": incident.id,
        "incident_type": incident.incident_type,
        "severity": incident.severity,
        "state": incident.state,
        "impacted_refs": incident.impacted_refs,
        "reason_codes": incident.reason_codes,
        "next_action": incident.next_action,
        "owner_user_id": incident.owner_user_id,
        "opened_at": incident.opened_at,
        "acknowledged_at": incident.acknowledged_at,
        "resolved_at": incident.resolved_at,
        "metadata": incident.metadata_,
        "created_at": incident.created_at,
        "updated_at": incident.updated_at,
    }

def _manual_action(action: Any) -> dict[str, Any]:
    return {
        "id": action.id,
        "action_type": action.action_type,
        "target_type": action.target_type,
        "target_id": action.target_id,
        "priority": action.priority,
        "state": action.state,
        "reason_code": action.reason_code,
        "next_action": action.next_action,
        "assignee_user_id": action.assignee_user_id,
        "due_at": action.due_at,
        "created_at": action.created_at,
        "updated_at": action.updated_at,
    }

def _editorial_slot(slot: Any) -> dict[str, Any]:
    return {
        "id": slot.id,
        "company_id": slot.company_id,
        "channel_workspace_id": slot.channel_workspace_id,
        "policy_snapshot_id": slot.policy_snapshot_id,
        "slot_date": slot.slot_date,
        "slot_type": slot.slot_type,
        "status": slot.status,
        "production_goal": slot.production_goal,
        "target_platforms": slot.target_platforms,
        "content_pillar": slot.content_pillar,
        "series_key": slot.series_key,
        "format_hint": slot.format_hint,
        "risk_level": slot.risk_level,
        "operational_envelope": slot.operational_envelope,
        "created_by_user_id": slot.created_by_user_id,
        "created_at": slot.created_at,
        "updated_at": slot.updated_at,
    }

def _channel_daily_run(daily_run: Any) -> dict[str, Any]:
    return {
        "id": daily_run.id,
        "company_id": daily_run.company_id,
        "channel_workspace_id": daily_run.channel_workspace_id,
        "policy_snapshot_id": daily_run.policy_snapshot_id,
        "editorial_calendar_slot_id": daily_run.editorial_calendar_slot_id,
        "run_date": daily_run.run_date,
        "status": daily_run.status,
        "run_mode": daily_run.run_mode,
        "trigger_type": daily_run.trigger_type,
        "started_at": daily_run.started_at,
        "completed_at": daily_run.completed_at,
        "context_pack_snapshot_id": daily_run.context_pack_snapshot_id,
        "channel_state_pack_snapshot_id": daily_run.channel_state_pack_snapshot_id,
        "daily_idea_decision_id": daily_run.daily_idea_decision_id,
        "project_admission_decision_id": daily_run.project_admission_decision_id,
        "reason_codes": daily_run.reason_codes,
        "metadata": daily_run.metadata_,
        "created_at": daily_run.created_at,
        "updated_at": daily_run.updated_at,
    }

def _retrieval_plan(plan: Any) -> dict[str, Any]:
    return {
        "id": plan.id,
        "purpose": plan.purpose,
        "company_id": plan.company_id,
        "channel_workspace_id": plan.channel_workspace_id,
        "channel_profile_version_id": plan.channel_profile_version_id,
        "policy_snapshot_id": plan.policy_snapshot_id,
        "video_project_id": plan.video_project_id,
        "editorial_calendar_slot_id": plan.editorial_calendar_slot_id,
        "allowed_sources": plan.allowed_sources,
        "excluded_sources": plan.excluded_sources,
        "redaction_rules": plan.redaction_rules,
        "token_budget": plan.token_budget,
        "source_order": plan.source_order,
        "plan_hash": plan.plan_hash,
        "created_by_user_id": plan.created_by_user_id,
        "created_at": plan.created_at,
    }

def _context_pack(pack: Any) -> dict[str, Any]:
    return {
        "id": pack.id,
        "retrieval_plan_snapshot_id": pack.retrieval_plan_snapshot_id,
        "purpose": pack.purpose,
        "company_id": pack.company_id,
        "channel_workspace_id": pack.channel_workspace_id,
        "channel_profile_version_id": pack.channel_profile_version_id,
        "policy_snapshot_id": pack.policy_snapshot_id,
        "video_project_id": pack.video_project_id,
        "editorial_calendar_slot_id": pack.editorial_calendar_slot_id,
        "input_refs": pack.input_refs,
        "policy_refs": pack.policy_refs,
        "evidence_refs": pack.evidence_refs,
        "metric_refs": pack.metric_refs,
        "memory_refs": pack.memory_refs,
        "pack_content": pack.pack_content,
        "freshness_state": pack.freshness_state,
        "confidence_level": pack.confidence_level,
        "pack_hash": pack.pack_hash,
        "created_by_user_id": pack.created_by_user_id,
        "created_at": pack.created_at,
    }

def _channel_state_pack(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "channel_daily_run_id": snapshot.channel_daily_run_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "policy_snapshot_id": snapshot.policy_snapshot_id,
        "context_pack_snapshot_id": snapshot.context_pack_snapshot_id,
        "state_blob": snapshot.state_blob,
        "active_project_refs": snapshot.active_project_refs,
        "pending_review_refs": snapshot.pending_review_refs,
        "readiness_summary": snapshot.readiness_summary,
        "provider_health_summary": snapshot.provider_health_summary,
        "quota_summary": snapshot.quota_summary,
        "evidence_summary": snapshot.evidence_summary,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "state_hash": snapshot.state_hash,
        "created_at": snapshot.created_at,
    }

def _search_demand_evidence(evidence: Any) -> dict[str, Any]:
    return {
        "id": evidence.id,
        "company_id": evidence.company_id,
        "channel_workspace_id": evidence.channel_workspace_id,
        "evidence_source_type": evidence.evidence_source_type,
        "source_ref": evidence.source_ref,
        "query": evidence.query,
        "platform": evidence.platform,
        "geo": evidence.geo,
        "language": evidence.language,
        "lookback_window_days": evidence.lookback_window_days,
        "search_volume_30d": evidence.search_volume_30d,
        "relative_interest_index": evidence.relative_interest_index,
        "competition_index": evidence.competition_index,
        "trending_velocity": evidence.trending_velocity,
        "evidence_confidence": evidence.evidence_confidence,
        "captured_at": evidence.captured_at,
        "metadata": evidence.metadata_,
        "created_at": evidence.created_at,
    }

def _daily_idea_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "channel_daily_run_id": decision.channel_daily_run_id,
        "company_id": decision.company_id,
        "channel_workspace_id": decision.channel_workspace_id,
        "policy_snapshot_id": decision.policy_snapshot_id,
        "context_pack_snapshot_id": decision.context_pack_snapshot_id,
        "channel_state_pack_snapshot_id": decision.channel_state_pack_snapshot_id,
        "llm_run_snapshot_id": decision.llm_run_snapshot_id,
        "decision_status": decision.decision_status,
        "proposed_title": decision.proposed_title,
        "proposed_angle": decision.proposed_angle,
        "proposed_format": decision.proposed_format,
        "proposed_pillar": decision.proposed_pillar,
        "proposed_series_key": decision.proposed_series_key,
        "rationale": decision.rationale,
        "evidence_refs": decision.evidence_refs,
        "reason_codes": decision.reason_codes,
        "confidence_level": decision.confidence_level,
        "created_at": decision.created_at,
    }

def _idea_market_preflight(preflight: Any) -> dict[str, Any]:
    return {
        "id": preflight.id,
        "company_id": preflight.company_id,
        "channel_workspace_id": preflight.channel_workspace_id,
        "channel_daily_run_id": preflight.channel_daily_run_id,
        "daily_idea_decision_id": preflight.daily_idea_decision_id,
        "search_intent_map_id": preflight.search_intent_map_id,
        "audience_target_pack_id": preflight.audience_target_pack_id,
        "demand_score": preflight.demand_score,
        "channel_fit_score": preflight.channel_fit_score,
        "policy_fit_state": preflight.policy_fit_state,
        "confidence_state": preflight.confidence_state,
        "evidence_blob": preflight.evidence_blob,
        "reason_codes": preflight.reason_codes,
        "decision": preflight.decision,
        "created_at": preflight.created_at,
    }

def _project_admission_decision(decision: Any) -> dict[str, Any]:
    return {
        "id": decision.id,
        "channel_daily_run_id": decision.channel_daily_run_id,
        "daily_idea_decision_id": decision.daily_idea_decision_id,
        "idea_market_preflight_id": decision.idea_market_preflight_id,
        "budget_policy_key": decision.budget_gate_result.get("policy_key"),
        "quota_account_id": None,
        "estimated_cost": 0,
        "created_by_user_id": decision.created_by_user_id,
        "budget_gate_result": decision.budget_gate_result,
        "readiness_gate_refs": decision.readiness_gate_refs,
        "decision": decision.decision,
        "reason_codes": decision.reason_codes,
        "evidence_refs": decision.evidence_refs,
        "admitted_video_project_id": decision.admitted_video_project_id,
        "created_artifact_refs": decision.created_artifact_refs,
        "created_at": decision.created_at,
    }

def _production_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "source_project_admission_decision_id": run.source_project_admission_decision_id,
        "run_mode": run.run_mode,
        "status": run.status,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "script_artifact_version_id": run.script_artifact_version_id,
        "voice_timeline_snapshot_id": run.voice_timeline_snapshot_id,
        "caption_track_snapshot_id": run.caption_track_snapshot_id,
        "visual_plan_snapshot_id": run.visual_plan_snapshot_id,
        "scene_manifest_snapshot_id": run.scene_manifest_snapshot_id,
        "render_spec_snapshot_id": run.render_spec_snapshot_id,
        "asset_manifest_snapshot_id": run.asset_manifest_snapshot_id,
        "source_manifest_snapshot_id": run.source_manifest_snapshot_id,
        "render_package_snapshot_id": run.render_package_snapshot_id,
        "media_qc_report_id": run.media_qc_report_id,
        "accessibility_qc_report_id": run.accessibility_qc_report_id,
        "reason_codes": run.reason_codes,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _render_job(job: Any) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "production_artifact_run_id": str(job.production_artifact_run_id) if job.production_artifact_run_id else None,
        "video_project_id": str(job.video_project_id),
        "render_spec_snapshot_id": str(job.render_spec_snapshot_id),
        "render_variant_id": job.render_variant_id,
        "renderer_key": job.renderer_key,
        "status": job.status,
        "output_ref": job.output_ref,
        "error_code": job.error_code,
        "reason_codes": job.reason_codes,
    }

def _render_package(package: Any) -> dict[str, Any]:
    return {
        "id": str(package.id),
        "production_artifact_run_id": str(package.production_artifact_run_id) if package.production_artifact_run_id else None,
        "video_project_id": str(package.video_project_id),
        "media_render_job_id": str(package.media_render_job_id),
        "render_spec_snapshot_id": str(package.render_spec_snapshot_id),
        "final_video_ref": package.final_video_ref,
        "caption_ref": package.caption_ref,
        "manifest_ref": package.manifest_ref,
        "file_manifest": package.file_manifest,
        "checksum_manifest": package.checksum_manifest,
        "duration_seconds": str(package.duration_seconds) if package.duration_seconds is not None else None,
        "package_state": package.package_state,
    }

def _media_qc_report(report: Any) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "production_artifact_run_id": str(report.production_artifact_run_id) if report.production_artifact_run_id else None,
        "video_project_id": str(report.video_project_id),
        "render_package_snapshot_id": str(report.render_package_snapshot_id) if report.render_package_snapshot_id else None,
        "render_spec_snapshot_id": str(report.render_spec_snapshot_id),
        "qc_state": report.qc_state,
        "reason_codes": report.reason_codes,
        "duration_check": report.duration_check,
        "file_integrity_check": report.file_integrity_check,
        "manifest_check": report.manifest_check,
    }

def _accessibility_qc_report(report: Any) -> dict[str, Any]:
    return {
        "id": str(report.id),
        "production_artifact_run_id": str(report.production_artifact_run_id) if report.production_artifact_run_id else None,
        "video_project_id": str(report.video_project_id),
        "caption_track_snapshot_id": str(report.caption_track_snapshot_id) if report.caption_track_snapshot_id else None,
        "render_package_snapshot_id": str(report.render_package_snapshot_id) if report.render_package_snapshot_id else None,
        "qc_state": report.qc_state,
        "reason_codes": report.reason_codes,
        "caption_presence_check": report.caption_presence_check,
        "caption_readability_check": report.caption_readability_check,
    }

def _publish_handoff(handoff: Any) -> dict[str, Any]:
    return {
        "id": handoff.id,
        "company_id": handoff.company_id,
        "channel_workspace_id": handoff.channel_workspace_id,
        "video_project_id": handoff.video_project_id,
        "policy_snapshot_id": handoff.policy_snapshot_id,
        "production_artifact_run_id": handoff.production_artifact_run_id,
        "render_package_snapshot_id": handoff.render_package_snapshot_id,
        "render_spec_snapshot_id": handoff.render_spec_snapshot_id,
        "media_qc_report_id": handoff.media_qc_report_id,
        "accessibility_qc_report_id": handoff.accessibility_qc_report_id,
        "source_manifest_snapshot_id": handoff.source_manifest_snapshot_id,
        "asset_manifest_snapshot_id": handoff.asset_manifest_snapshot_id,
        "target_platform": handoff.target_platform,
        "target_surface": handoff.target_surface,
        "destination_binding_id": handoff.destination_binding_id,
        "render_variant_id": handoff.render_variant_id,
        "package_state": handoff.package_state,
        "planned_metadata": handoff.planned_metadata,
        "planned_disclosures": handoff.planned_disclosures,
        "planned_files": handoff.planned_files,
        "cloud_media_refs": handoff.cloud_media_refs,
        "checklist_snapshot": handoff.checklist_snapshot,
        "operator_instructions": handoff.operator_instructions,
        "risk_summary": handoff.risk_summary,
        "reason_codes": handoff.reason_codes,
        "next_action": handoff.next_action,
        "created_by_user_id": handoff.created_by_user_id,
        "created_at": handoff.created_at,
        "updated_at": handoff.updated_at,
    }

def _manual_publish_confirmation(confirmation: Any) -> dict[str, Any]:
    return {
        "id": confirmation.id,
        "publish_handoff_package_id": confirmation.publish_handoff_package_id,
        "company_id": confirmation.company_id,
        "channel_workspace_id": confirmation.channel_workspace_id,
        "video_project_id": confirmation.video_project_id,
        "policy_snapshot_id": confirmation.policy_snapshot_id,
        "target_platform": confirmation.target_platform,
        "target_surface": confirmation.target_surface,
        "confirmed_by_user_id": confirmation.confirmed_by_user_id,
        "confirmation_state": confirmation.confirmation_state,
        "actual_video_id": confirmation.actual_video_id,
        "actual_video_url": confirmation.actual_video_url,
        "actual_published_at": confirmation.actual_published_at,
        "actual_metadata": confirmation.actual_metadata,
        "actual_disclosures": confirmation.actual_disclosures,
        "actual_files": confirmation.actual_files,
        "operator_notes": confirmation.operator_notes,
        "validation_summary": confirmation.validation_summary,
        "metadata_diff": confirmation.metadata_diff,
        "reason_codes": confirmation.reason_codes,
        "next_action": confirmation.next_action,
        "created_at": confirmation.created_at,
        "updated_at": confirmation.updated_at,
    }

def _uploaded_video(uploaded: Any) -> dict[str, Any]:
    return {
        "id": uploaded.id,
        "company_id": uploaded.company_id,
        "channel_workspace_id": uploaded.channel_workspace_id,
        "video_project_id": uploaded.video_project_id,
        "policy_snapshot_id": uploaded.policy_snapshot_id,
        "publish_handoff_package_id": uploaded.publish_handoff_package_id,
        "manual_publish_confirmation_id": uploaded.manual_publish_confirmation_id,
        "render_package_snapshot_id": uploaded.render_package_snapshot_id,
        "source_manifest_snapshot_id": uploaded.source_manifest_snapshot_id,
        "rights_envelope_ref": uploaded.rights_envelope_ref,
        "platform": uploaded.platform,
        "platform_video_id": uploaded.platform_video_id,
        "video_url": uploaded.video_url,
        "published_at": uploaded.published_at,
        "publish_status": uploaded.publish_status,
        "actual_metadata": uploaded.actual_metadata,
        "actual_disclosures": uploaded.actual_disclosures,
        "lineage_refs": uploaded.lineage_refs,
        "monitoring_state": uploaded.monitoring_state,
        "operator_summary": uploaded.operator_summary,
        "created_at": uploaded.created_at,
        "updated_at": uploaded.updated_at,
    }

def _uploaded_video_summary(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "uploaded_video_id": summary.uploaded_video_id,
        "company_id": summary.company_id,
        "channel_workspace_id": summary.channel_workspace_id,
        "video_project_id": summary.video_project_id,
        "platform": summary.platform,
        "platform_video_id": summary.platform_video_id,
        "video_url": summary.video_url,
        "published_at": summary.published_at,
        "title": summary.title,
        "publish_status": summary.publish_status,
        "monitoring_state": summary.monitoring_state,
        "operator_status": summary.operator_status,
        "operator_summary": summary.operator_summary,
        "next_action": summary.next_action,
        "freshness_state": summary.freshness_state,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }

def _analytics_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "uploaded_video_id": run.uploaded_video_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "platform": run.platform,
        "platform_video_id": run.platform_video_id,
        "sync_mode": run.sync_mode,
        "sync_state": run.sync_state,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "observed_from": run.observed_from,
        "observed_to": run.observed_to,
        "provider_key": run.provider_key,
        "provider_attempt_id": run.provider_attempt_id,
        "analytics_snapshot_id": run.analytics_snapshot_id,
        "reason_codes": run.reason_codes,
        "next_action": run.next_action,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _analytics_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_sync_run_id": snapshot.analytics_sync_run_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "video_project_id": snapshot.video_project_id,
        "policy_snapshot_id": snapshot.policy_snapshot_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "observed_from": snapshot.observed_from,
        "observed_to": snapshot.observed_to,
        "observation_window": snapshot.observation_window,
        "metrics_blob": snapshot.metrics_blob,
        "normalized_metrics_blob": snapshot.normalized_metrics_blob,
        "metric_availability": snapshot.metric_availability,
        "source_metadata": snapshot.source_metadata,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "reason_codes": snapshot.reason_codes,
        "created_at": snapshot.created_at,
    }

def _traffic_source_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_snapshot_id": snapshot.analytics_snapshot_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "traffic_sources": snapshot.traffic_sources,
        "source_summary": snapshot.source_summary,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "created_at": snapshot.created_at,
    }

def _retention_curve_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "analytics_snapshot_id": snapshot.analytics_snapshot_id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "video_project_id": snapshot.video_project_id,
        "render_package_snapshot_id": snapshot.render_package_snapshot_id,
        "platform": snapshot.platform,
        "platform_video_id": snapshot.platform_video_id,
        "captured_at": snapshot.captured_at,
        "curve_points": snapshot.curve_points,
        "curve_summary": snapshot.curve_summary,
        "duration_seconds": snapshot.duration_seconds,
        "timeline_alignment": snapshot.timeline_alignment,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "created_at": snapshot.created_at,
    }

def _uploaded_video_metrics_summary(summary: Any) -> dict[str, Any]:
    return {
        "id": summary.id,
        "uploaded_video_id": summary.uploaded_video_id,
        "company_id": summary.company_id,
        "channel_workspace_id": summary.channel_workspace_id,
        "video_project_id": summary.video_project_id,
        "platform": summary.platform,
        "platform_video_id": summary.platform_video_id,
        "latest_analytics_snapshot_id": summary.latest_analytics_snapshot_id,
        "latest_retention_curve_snapshot_id": summary.latest_retention_curve_snapshot_id,
        "latest_traffic_source_snapshot_id": summary.latest_traffic_source_snapshot_id,
        "latest_engagement_snapshot_id": summary.latest_engagement_snapshot_id,
        "latest_captured_at": summary.latest_captured_at,
        "metrics_summary": summary.metrics_summary,
        "availability_summary": summary.availability_summary,
        "freshness_state": summary.freshness_state,
        "confidence_level": summary.confidence_level,
        "monitoring_state": summary.monitoring_state,
        "operator_summary": summary.operator_summary,
        "next_action": summary.next_action,
        "created_at": summary.created_at,
        "updated_at": summary.updated_at,
    }

def _youtube_oauth_session(session: Any) -> dict[str, Any]:
    return {
        "id": session.id,
        "company_id": session.company_id,
        "channel_workspace_id": session.channel_workspace_id,
        "redirect_uri": session.redirect_uri,
        "scopes": session.scopes,
        "status": session.status,
        "credential_reference_id": session.credential_reference_id,
        "error_code": session.error_code,
        "error_message": session.error_message,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }

def _google_drive_oauth_session(session: Any) -> dict[str, Any]:
    return {
        "id": session.id,
        "company_id": session.company_id,
        "channel_workspace_id": session.channel_workspace_id,
        "redirect_uri": session.redirect_uri,
        "scopes": session.scopes,
        "status": session.status,
        "credential_reference_id": session.credential_reference_id,
        "error_code": session.error_code,
        "error_message": session.error_message,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }

def _media_offload_job(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "company_id": job.company_id,
        "channel_workspace_id": job.channel_workspace_id,
        "video_project_id": job.video_project_id,
        "uploaded_video_id": job.uploaded_video_id,
        "source_media_ref_id": job.source_media_ref_id,
        "render_package_id": job.render_package_id,
        "local_source_path_hash": job.local_source_path_hash,
        "target_provider": job.target_provider,
        "target_folder_policy": job.target_folder_policy,
        "target_media_type": job.target_media_type,
        "job_state": job.job_state,
        "cloud_media_ref_id": job.cloud_media_ref_id,
        "retry_count": job.retry_count,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

def _local_media_retention_policy(policy: Any) -> dict[str, Any]:
    return {
        "id": policy.id,
        "company_id": policy.company_id,
        "channel_workspace_id": policy.channel_workspace_id,
        "keep_local_after_upload": policy.keep_local_after_upload,
        "cleanup_after_verified": policy.cleanup_after_verified,
        "max_local_age_hours": policy.max_local_age_hours,
        "max_local_storage_gb": policy.max_local_storage_gb,
        "protected_paths": policy.protected_paths,
        "allowed_cleanup_roots": policy.allowed_cleanup_roots,
        "state": policy.state,
        "created_at": policy.created_at,
        "updated_at": policy.updated_at,
    }

def _youtube_public_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "platform_video_id": run.platform_video_id,
        "run_state": run.run_state,
        "source": run.source,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "http_status": run.http_status,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metrics_found": run.metrics_found,
        "created_snapshot_id": run.created_snapshot_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _youtube_owner_sync_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "platform_video_id": run.platform_video_id,
        "credential_reference_id": run.credential_reference_id,
        "run_state": run.run_state,
        "source": run.source,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "http_status": run.http_status,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "metrics_found": run.metrics_found,
        "created_snapshot_id": run.created_snapshot_id,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _youtube_public_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "platform_video_id": snapshot.platform_video_id,
        "video_url": snapshot.video_url,
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "youtube_title": snapshot.youtube_title,
        "youtube_published_at": snapshot.youtube_published_at,
        "youtube_channel_id": snapshot.youtube_channel_id,
        "youtube_channel_title": snapshot.youtube_channel_title,
        "thumbnail_url": snapshot.thumbnail_url,
        "duration_seconds": snapshot.duration_seconds,
        "definition": snapshot.definition,
        "caption_status": snapshot.caption_status,
        "privacy_status": snapshot.privacy_status,
        "public_stats_viewable": snapshot.public_stats_viewable,
        "title_matches_confirmed_metadata": snapshot.title_matches_confirmed_metadata,
        "duration_matches_render_package": snapshot.duration_matches_render_package,
        "views_availability": snapshot.views_availability,
        "likes_availability": snapshot.likes_availability,
        "comments_availability": snapshot.comments_availability,
        "freshness_state": snapshot.freshness_state,
        "sync_status": snapshot.sync_status,
        "sync_error_code": snapshot.sync_error_code,
        "learning_authority": snapshot.learning_authority,
        "last_synced_at": snapshot.last_synced_at,
        "unknown_metrics": snapshot.unknown_metrics,
        "unavailable_metrics": snapshot.unavailable_metrics,
        "technical_appendix": snapshot.technical_appendix,
        "created_at": snapshot.created_at,
    }

def _youtube_owner_snapshot(snapshot: Any) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "uploaded_video_id": snapshot.uploaded_video_id,
        "company_id": snapshot.company_id,
        "channel_workspace_id": snapshot.channel_workspace_id,
        "platform_video_id": snapshot.platform_video_id,
        "analytics_start_date": snapshot.analytics_start_date,
        "analytics_end_date": snapshot.analytics_end_date,
        "learning_authority": snapshot.learning_authority,
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "impressions": snapshot.impressions,
        "impression_click_through_rate": snapshot.impression_click_through_rate,
        "average_view_duration_seconds": snapshot.average_view_duration_seconds,
        "average_view_percentage": snapshot.average_view_percentage,
        "estimated_minutes_watched": snapshot.estimated_minutes_watched,
        "subscribers_gained": snapshot.subscribers_gained,
        "subscribers_lost": snapshot.subscribers_lost,
        "metric_availability": snapshot.metric_availability,
        "freshness_state": snapshot.freshness_state,
        "sync_status": snapshot.sync_status,
        "sync_error_code": snapshot.sync_error_code,
        "last_synced_at": snapshot.last_synced_at,
        "technical_appendix": snapshot.technical_appendix,
        "created_at": snapshot.created_at,
    }

def _post_publish_health_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "uploaded_video_id": run.uploaded_video_id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "policy_snapshot_id": run.policy_snapshot_id,
        "platform": run.platform,
        "platform_video_id": run.platform_video_id,
        "observation_window": run.observation_window,
        "analytics_snapshot_id": run.analytics_snapshot_id,
        "uploaded_video_metrics_summary_id": run.uploaded_video_metrics_summary_id,
        "retention_curve_snapshot_id": run.retention_curve_snapshot_id,
        "traffic_source_snapshot_id": run.traffic_source_snapshot_id,
        "engagement_snapshot_id": run.engagement_snapshot_id,
        "run_state": run.run_state,
        "health_state": run.health_state,
        "severity": run.severity,
        "confidence_level": run.confidence_level,
        "evidence_refs": run.evidence_refs,
        "reason_codes": run.reason_codes,
        "operator_summary": run.operator_summary,
        "next_action": run.next_action,
        "do_not_do": run.do_not_do,
        "technical_appendix": run.technical_appendix,
        "created_at": run.created_at,
    }

def _failure_trace_report(report: Any) -> dict[str, Any]:
    return {
        "id": report.id,
        "post_publish_health_run_id": report.post_publish_health_run_id,
        "uploaded_video_id": report.uploaded_video_id,
        "video_project_id": report.video_project_id,
        "platform": report.platform,
        "platform_video_id": report.platform_video_id,
        "observation_window": report.observation_window,
        "primary_status": report.primary_status,
        "primary_suspected_cause": report.primary_suspected_cause,
        "secondary_suspected_causes": report.secondary_suspected_causes,
        "confidence_level": report.confidence_level,
        "severity": report.severity,
        "evidence_plain_text": report.evidence_plain_text,
        "operator_summary": report.operator_summary,
        "operator_report": report.operator_report,
        "next_action": report.next_action,
        "do_not_do": report.do_not_do,
        "technical_appendix": report.technical_appendix,
        "created_at": report.created_at,
    }

def _recovery_proposal(proposal: Any) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "failure_trace_report_id": proposal.failure_trace_report_id,
        "uploaded_video_id": proposal.uploaded_video_id,
        "video_project_id": proposal.video_project_id,
        "proposal_type": proposal.proposal_type,
        "proposal_state": proposal.proposal_state,
        "operator_summary": proposal.operator_summary,
        "recommended_actions": proposal.recommended_actions,
        "do_not_do": proposal.do_not_do,
        "evidence_refs": proposal.evidence_refs,
        "risk_level": proposal.risk_level,
        "requires_human_approval": proposal.requires_human_approval,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
    }

def _learning_generation_run(run: Any) -> dict[str, Any]:
    return {
        "id": run.id,
        "company_id": run.company_id,
        "channel_workspace_id": run.channel_workspace_id,
        "video_project_id": run.video_project_id,
        "uploaded_video_id": run.uploaded_video_id,
        "source_failure_trace_report_id": run.source_failure_trace_report_id,
        "source_recovery_proposal_id": run.source_recovery_proposal_id,
        "source_analytics_snapshot_id": run.source_analytics_snapshot_id,
        "source_uploaded_video_metrics_summary_id": run.source_uploaded_video_metrics_summary_id,
        "run_mode": run.run_mode,
        "run_state": run.run_state,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
        "generated_candidate_count": run.generated_candidate_count,
        "reason_codes": run.reason_codes,
        "next_action": run.next_action,
        "metadata": run.metadata_,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }

def _learning_candidate(candidate: Any) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "generation_run_id": candidate.generation_run_id,
        "company_id": candidate.company_id,
        "channel_workspace_id": candidate.channel_workspace_id,
        "video_project_id": candidate.video_project_id,
        "uploaded_video_id": candidate.uploaded_video_id,
        "candidate_type": candidate.candidate_type,
        "candidate_state": candidate.candidate_state,
        "operator_summary": candidate.operator_summary,
        "friendly_status": candidate.friendly_status,
        "candidate_summary": candidate.candidate_summary,
        "suggested_learning": candidate.suggested_learning,
        "suggested_playbook_text": candidate.suggested_playbook_text,
        "recommended_scope": candidate.recommended_scope,
        "confidence_label": candidate.confidence_label,
        "risk_level": candidate.risk_level,
        "evidence_bundle_id": candidate.evidence_bundle_id,
        "eligibility_run_id": candidate.eligibility_run_id,
        "source_refs": candidate.source_refs,
        "diagnostic_refs": candidate.diagnostic_refs,
        "recovery_refs": candidate.recovery_refs,
        "metric_refs": candidate.metric_refs,
        "policy_flags": candidate.policy_flags,
        "rights_flags": candidate.rights_flags,
        "limitations": candidate.limitations,
        "counter_evidence": candidate.counter_evidence,
        "technical_appendix": candidate.technical_appendix,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }

def _learning_evidence_bundle(bundle: Any) -> dict[str, Any]:
    return {
        "id": bundle.id,
        "learning_candidate_id": bundle.learning_candidate_id,
        "company_id": bundle.company_id,
        "channel_workspace_id": bundle.channel_workspace_id,
        "evidence_summary": bundle.evidence_summary,
        "source_video_refs": bundle.source_video_refs,
        "source_project_refs": bundle.source_project_refs,
        "analytics_snapshot_refs": bundle.analytics_snapshot_refs,
        "diagnostic_refs": bundle.diagnostic_refs,
        "recovery_refs": bundle.recovery_refs,
        "metric_support": bundle.metric_support,
        "counter_evidence": bundle.counter_evidence,
        "limitations": bundle.limitations,
        "freshness_summary": bundle.freshness_summary,
        "confidence_summary": bundle.confidence_summary,
        "policy_rights_summary": bundle.policy_rights_summary,
        "created_at": bundle.created_at,
    }

def _learning_review_queue_item(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "learning_candidate_id": item.learning_candidate_id,
        "evidence_bundle_id": item.evidence_bundle_id,
        "eligibility_run_id": item.eligibility_run_id,
        "company_id": item.company_id,
        "channel_workspace_id": item.channel_workspace_id,
        "video_project_id": item.video_project_id,
        "uploaded_video_id": item.uploaded_video_id,
        "queue_state": item.queue_state,
        "priority": item.priority,
        "operator_summary": item.operator_summary,
        "friendly_status": item.friendly_status,
        "evidence_summary": item.evidence_summary,
        "recommended_scope": item.recommended_scope,
        "confidence_label": item.confidence_label,
        "risk_level": item.risk_level,
        "next_action": item.next_action,
        "approval_actions_allowed": item.approval_actions_allowed,
        "source_refs": item.source_refs,
        "audit_refs": item.audit_refs,
        "technical_appendix": item.technical_appendix,
        "due_at": item.due_at,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }

def _playbook_candidate_draft(draft: Any) -> dict[str, Any]:
    return {
        "id": draft.id,
        "learning_candidate_id": draft.learning_candidate_id,
        "company_id": draft.company_id,
        "channel_workspace_id": draft.channel_workspace_id,
        "candidate_scope": draft.candidate_scope,
        "playbook_category": draft.playbook_category,
        "draft_text": draft.draft_text,
        "rationale": draft.rationale,
        "evidence_refs": draft.evidence_refs,
        "risk_notes": draft.risk_notes,
        "state": draft.state,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
    }


def _learning_review_action(
    candidate_id: uuid.UUID,
    action: str,
    data: LearningReviewDecisionCreate | None,
) -> LearningReviewDecisionRead:
    try:
        request = data.model_copy(update={"action": action}) if data is not None else LearningReviewDecisionCreate(action=action)
        with session_scope() as session:
            decision = M11LearningReviewService(session).decide(candidate_id=candidate_id, data=request)
            return learning_review_decision_read(session, decision)
    except Exception as exc:
        raise _as_http_error(exc) from exc


def _as_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (NotFoundError, KeyError)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, (ValidationFailureError, ValueError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
