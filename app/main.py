import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, status
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
    ProviderAttemptMockRequest,
    ProviderAttemptRead,
    ProviderHealthCheckRequest,
    ProviderHealthSnapshotRead,
    ProviderRegistryEntryCreate,
    ProviderRegistryEntryRead,
    ProjectAdmissionDecisionCreate,
    ProjectAdmissionDecisionRead,
    FailureTraceReportRead,
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
    RenderLocalSmokeRequest,
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
    VideoProjectCreate,
    VideoProjectRead,
    RecoveryProposalRead,
    ChannelDailyRunCreate,
    ChannelDailyRunRead,
    ChannelStatePackSnapshotCreate,
    ChannelStatePackSnapshotRead,
    ContextPackSnapshotCreate,
    ContextPackSnapshotRead,
)
from app.contracts.policy_snapshot import CompiledChannelPolicySnapshot as SnapshotRead
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
    MediaQCService,
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
    SystemHealthService,
    WorkflowReadinessService,
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

    @application.post("/providers/seed-mocks")
    def seed_mock_providers() -> dict[str, int]:
        try:
            with session_scope() as session:
                records = ProviderRegistryService(session).seed_mock_providers()
                return {"count": len(records)}
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

    @application.post("/provider-attempts/mock", response_model=ProviderAttemptRead)
    def create_provider_attempt_mock(data: ProviderAttemptMockRequest) -> ProviderAttemptRead:
        try:
            with session_scope() as session:
                attempt = RetryOpsService(session).record_mock_attempt(data=data)
                return ProviderAttemptRead.model_validate(_provider_attempt(attempt))
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
                run = ProductionArtifactRunService(session).execute_local_mock_flow(run_id=run_id)
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

    @application.post("/render-jobs/local-smoke")
    def create_local_smoke_render(data: RenderLocalSmokeRequest) -> dict[str, Any]:
        try:
            with session_scope() as session:
                result = LocalFixtureRendererService(session).render_local_smoke(
                    render_spec_snapshot_id=data.render_spec_snapshot_id,
                )
                return {
                    "job": _render_job(result.job),
                    "render_package": _render_package(result.package) if result.package else None,
                }
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

    @application.post("/publish-handoffs/{handoff_id}/mark-ready", response_model=PublishHandoffRead)
    def mark_publish_handoff_ready(handoff_id: uuid.UUID) -> PublishHandoffRead:
        try:
            with session_scope() as session:
                handoff = PublishHandoffService(session).mark_ready(handoff_id=handoff_id)
                return PublishHandoffRead.model_validate(_publish_handoff(handoff))
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
