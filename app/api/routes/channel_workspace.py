from fastapi import APIRouter

from app.api.routes.imports import (
    Any,
    ChannelActivateRequest,
    ChannelContractCompiler,
    ChannelContractDraftRead,
    ChannelContractPreviewRead,
    ChannelContractReviewRequest,
    ChannelContractReviewService,
    ChannelCreateRequest,
    ChannelInitCompileRequest,
    ChannelInitCompileResult,
    ChannelInitDraftCreate,
    ChannelInitDraftRead,
    ChannelInitDraftService,
    ChannelInitResearchRequest,
    ChannelLifecycleDecisionCreate,
    ChannelLifecycleDecisionRead,
    ChannelLifecycleRead,
    ChannelLocalizationConfig,
    ChannelLocalizationConfigUpdate,
    ChannelMembershipCreate,
    ChannelMembershipRead,
    ChannelProfileCompileRequest,
    ChannelProfileCompileResult,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelProfileVersionCreate,
    ChannelProfileVersionRead,
    ChannelPublishTimingPolicyCreate,
    ChannelPublishTimingPolicyRead,
    ChannelSetupResearchAgentService,
    ChannelWorkspaceCreate,
    ChannelWorkspaceDashboardRead,
    ChannelWorkspaceRead,
    ChannelWorkspaceService,
    CompanyCreate,
    CompanyRead,
    CompanyService,
    HTTPException,
    HumanUploadTaskListRead,
    LocalizationConfigService,
    M11ChannelLifecycleService,
    M11DashboardService,
    NotFoundError,
    PolicySnapshotService,
    PublishHandoffLedgerService,
    PublishLedgerRead,
    PublishTimingPolicyService,
    SnapshotRead,
    UploadedVideoListRead,
    ValidationFailureError,
    channel_lifecycle_decision_read,
    evaluate_contract,
    leaf_values,
    session_scope,
    uuid,
)

from app.api.routes.serializers_core import (
    _channel,
    _channel_contract_draft,
    _channel_init_draft,
    _company,
    _membership,
    _profile,
    _snapshot,
    _snapshot_with_contract_state,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/companies", response_model=CompanyRead)
    def create_company(data: CompanyCreate) -> CompanyRead:
        try:
            with session_scope() as session:
                company = CompanyService(session).create_company(
                    name=data.name,
                    slug=data.slug,
                    description=data.description,
                    status=data.status,
                    default_currency=data.default_currency,
                )
                return CompanyRead.model_validate(_company(company))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/companies", response_model=list[CompanyRead])
    def list_companies(limit: int = 100) -> list[CompanyRead]:
        with session_scope() as session:
            companies = CompanyService(session).list_companies(limit=limit)
            return [CompanyRead.model_validate(_company(c)) for c in companies]

    @router.get("/companies/{company_id}", response_model=CompanyRead)
    def get_company(company_id: uuid.UUID) -> CompanyRead:
        with session_scope() as session:
            company = CompanyService(session).get_company(company_id)
            if company is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="company not found")
            return CompanyRead.model_validate(_company(company))

    @router.post("/channel-init-drafts", response_model=ChannelInitDraftRead)
    def create_channel_init_draft(data: ChannelInitDraftCreate) -> ChannelInitDraftRead:
        try:
            with session_scope() as session:
                draft = ChannelInitDraftService(session).create(data)
                return ChannelInitDraftRead.model_validate(_channel_init_draft(draft, None))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channel-init-drafts/{draft_id}", response_model=ChannelInitDraftRead)
    def get_channel_init_draft(draft_id: uuid.UUID) -> ChannelInitDraftRead:
        try:
            with session_scope() as session:
                service = ChannelInitDraftService(session)
                draft = service.get(draft_id)
                latest = service.latest_contract_draft(draft_id)
                return ChannelInitDraftRead.model_validate(_channel_init_draft(draft, latest))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-init-drafts/{draft_id}/research", response_model=ChannelContractDraftRead)
    def research_channel_init_draft(
        draft_id: uuid.UUID,
        data: ChannelInitResearchRequest | None = None,
    ) -> ChannelContractDraftRead:
        try:
            with session_scope() as session:
                request = data or ChannelInitResearchRequest()
                contract_draft = ChannelSetupResearchAgentService(session).run(
                    draft_id,
                    enable_optional_web_snippets=request.enable_optional_web_snippets,
                )
                return ChannelContractDraftRead.model_validate(_channel_contract_draft(contract_draft))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-init-drafts/{draft_id}/review", response_model=ChannelContractDraftRead)
    def review_channel_init_draft(
        draft_id: uuid.UUID,
        data: ChannelContractReviewRequest,
    ) -> ChannelContractDraftRead:
        try:
            with session_scope() as session:
                contract_draft = ChannelContractReviewService(session).apply_review(draft_id, data)
                return ChannelContractDraftRead.model_validate(_channel_contract_draft(contract_draft))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channel-init-drafts/{draft_id}/compile", response_model=ChannelInitCompileResult)
    def compile_channel_init_draft(
        draft_id: uuid.UUID,
        data: ChannelInitCompileRequest | None = None,
    ) -> ChannelInitCompileResult:
        try:
            with session_scope() as session:
                request = data or ChannelInitCompileRequest()
                result = ChannelContractCompiler(session).compile(draft_id, correlation_id=request.correlation_id)
                return ChannelInitCompileResult.model_validate(result)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channel-init-drafts/{draft_id}/contract-preview", response_model=ChannelContractPreviewRead)
    def preview_channel_init_contract(draft_id: uuid.UUID) -> ChannelContractPreviewRead:
        try:
            with session_scope() as session:
                service = ChannelInitDraftService(session)
                draft = service.get(draft_id)
                contract_draft = service.latest_contract_draft(draft_id)
                if contract_draft is None:
                    raise NotFoundError(f"channel contract draft not found for init draft: {draft_id}")
                contract = contract_draft.suggested_channel_contract
                field_map = contract_draft.field_source_map_json
                status, missing_fields, contradiction_reasons = evaluate_contract(contract, field_map)
                leaf_paths = set(leaf_values(contract))
                coverage = {
                    "leaf_count": len(leaf_paths),
                    "covered_count": len(leaf_paths & set(field_map)),
                    "missing_paths": sorted(leaf_paths - set(field_map)),
                }
                return ChannelContractPreviewRead.model_validate(
                    {
                        "init_draft_id": draft.id,
                        "contract_status": status,
                        "workflow_status": draft.workflow_status,
                        "channel_contract_json": contract,
                        "field_source_map_json": field_map,
                        "missing_fields": missing_fields,
                        "contradiction_reasons": contradiction_reasons,
                        "field_source_coverage": coverage,
                    }
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/companies/{company_id}/channels", response_model=ChannelWorkspaceRead)
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

    @router.post("/channels", response_model=ChannelWorkspaceRead)
    def create_channel_direct(data: ChannelCreateRequest) -> ChannelWorkspaceRead:
        try:
            with session_scope() as session:
                payload = data.model_dump()
                company_id = payload.pop("company_id")
                channel = ChannelWorkspaceService(session).create_channel(
                    company_id=company_id,
                    data=ChannelWorkspaceCreate.model_validate(payload),
                )
                return ChannelWorkspaceRead.model_validate(_channel(channel))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/companies/{company_id}/channels", response_model=list[ChannelWorkspaceRead])
    def list_channels(company_id: uuid.UUID) -> list[ChannelWorkspaceRead]:
        with session_scope() as session:
            channels = ChannelWorkspaceService(session).list_channels(company_id)
            return [ChannelWorkspaceRead.model_validate(_channel(channel)) for channel in channels]

    @router.get("/channels")
    def list_dashboard_channels(company_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        try:
            with session_scope() as session:
                return M11DashboardService(session).list_channels(company_id=company_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}", response_model=ChannelWorkspaceRead)
    def get_channel(channel_id: uuid.UUID) -> ChannelWorkspaceRead:
        with session_scope() as session:
            channel = ChannelWorkspaceService(session).get_channel(channel_id)
            if channel is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="channel not found")
            return ChannelWorkspaceRead.model_validate(_channel(channel))

    @router.get("/channels/{channel_id}/workspace", response_model=ChannelWorkspaceDashboardRead)
    def get_channel_workspace_dashboard(channel_id: uuid.UUID) -> ChannelWorkspaceDashboardRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).workspace(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/upload-tasks", response_model=HumanUploadTaskListRead)
    def list_channel_upload_tasks(
        channel_id: uuid.UUID,
        status: str | None = None,
        destination: str | None = None,
        video_project_id: uuid.UUID | None = None,
        package_id: uuid.UUID | None = None,
    ) -> HumanUploadTaskListRead:
        try:
            with session_scope() as session:
                return PublishHandoffLedgerService(session).list_upload_tasks(
                    channel_id=channel_id,
                    status=status,
                    destination=destination,
                    video_project_id=video_project_id,
                    package_id=package_id,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/uploaded-videos", response_model=UploadedVideoListRead)
    def list_channel_uploaded_videos(
        channel_id: uuid.UUID,
        verification_status: str | None = None,
        analytics_sync_status: str | None = None,
        actual_visibility: str | None = None,
    ) -> UploadedVideoListRead:
        try:
            with session_scope() as session:
                return PublishHandoffLedgerService(session).list_uploaded_videos(
                    channel_id=channel_id,
                    verification_status=verification_status,
                    analytics_sync_status=analytics_sync_status,
                    actual_visibility=actual_visibility,
                )
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/publish-ledger", response_model=PublishLedgerRead)
    def get_channel_publish_ledger(channel_id: uuid.UUID) -> PublishLedgerRead:
        try:
            with session_scope() as session:
                return PublishHandoffLedgerService(session).publish_ledger(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/lifecycle", response_model=ChannelLifecycleRead)
    def get_channel_lifecycle(channel_id: uuid.UUID) -> ChannelLifecycleRead:
        try:
            with session_scope() as session:
                return M11DashboardService(session).lifecycle(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channels/{channel_id}/lifecycle-decision", response_model=ChannelLifecycleDecisionRead)
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

    @router.get("/channels/{channel_id}/localization-config", response_model=ChannelLocalizationConfig)
    def get_channel_localization_config(channel_id: uuid.UUID) -> ChannelLocalizationConfig:
        try:
            with session_scope() as session:
                return LocalizationConfigService(session).get(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channels/{channel_id}/localization-config", response_model=ChannelLocalizationConfig)
    def update_channel_localization_config(
        channel_id: uuid.UUID,
        data: ChannelLocalizationConfigUpdate,
    ) -> ChannelLocalizationConfig:
        try:
            with session_scope() as session:
                return LocalizationConfigService(session).update(channel_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/publish-timing-policy", response_model=ChannelPublishTimingPolicyRead)
    def get_channel_publish_timing_policy(channel_id: uuid.UUID) -> ChannelPublishTimingPolicyRead:
        try:
            with session_scope() as session:
                return PublishTimingPolicyService(session).get(channel_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channels/{channel_id}/publish-timing-policy", response_model=ChannelPublishTimingPolicyRead)
    def update_channel_publish_timing_policy(
        channel_id: uuid.UUID,
        data: ChannelPublishTimingPolicyCreate,
    ) -> ChannelPublishTimingPolicyRead:
        try:
            with session_scope() as session:
                return PublishTimingPolicyService(session).update(channel_id, data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channels/{channel_id}/memberships", response_model=ChannelMembershipRead)
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

    @router.post("/channels/{channel_id}/profile-versions", response_model=ChannelProfileVersionRead)
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

    @router.get("/channels/{channel_id}/profile-versions", response_model=list[ChannelProfileVersionRead])
    def list_profile_versions(channel_id: uuid.UUID) -> list[ChannelProfileVersionRead]:
        with session_scope() as session:
            profiles = ChannelProfileService(session).list_profile_versions(channel_id)
            return [ChannelProfileVersionRead.model_validate(_profile(profile)) for profile in profiles]

    @router.post("/profile-versions/{profile_version_id}/compile", response_model=ChannelProfileCompileResult)
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

    @router.post("/channels/{channel_id}/compile-policy-snapshot")
    def compile_channel_policy_snapshot(
        channel_id: uuid.UUID,
        data: ChannelProfileCompileRequest | None = None,
    ) -> dict[str, Any]:
        try:
            with session_scope() as session:
                profiles = ChannelProfileService(session).list_profile_versions(channel_id)
                if not profiles:
                    raise NotFoundError(f"profile version not found for channel: {channel_id}")
                request = data or ChannelProfileCompileRequest()
                compiled = ChannelProfileCompiler(session).compile(
                    profile_version_id=profiles[0].id,
                    correlation_id=request.correlation_id or f"api-channel-compile-{channel_id}",
                )
                snapshot = PolicySnapshotService(session).get_snapshot(compiled.snapshot_id)
                return _snapshot_with_contract_state(snapshot)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/profile-versions/{profile_version_id}/approve", response_model=ChannelProfileVersionRead)
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

    @router.post("/policy-snapshots/{snapshot_id}/activate", response_model=SnapshotRead)
    def activate_policy_snapshot(snapshot_id: uuid.UUID) -> SnapshotRead:
        try:
            with session_scope() as session:
                snapshot = ChannelProfileService(session).activate_snapshot(snapshot_id=snapshot_id)
                return SnapshotRead.model_validate(_snapshot(snapshot))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/policy-snapshot")
    def get_channel_policy_snapshot(channel_id: uuid.UUID) -> dict[str, Any] | None:
        try:
            with session_scope() as session:
                snapshots = PolicySnapshotService(session).list_snapshots(channel_id)
                snapshot = PolicySnapshotService(session).get_active_snapshot_for_channel(channel_id) or (snapshots[0] if snapshots else None)
                return _snapshot_with_contract_state(snapshot) if snapshot is not None else None
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/channels/{channel_id}/activate")
    def activate_channel(channel_id: uuid.UUID, data: ChannelActivateRequest | None = None) -> dict[str, Any]:
        try:
            with session_scope() as session:
                request = data or ChannelActivateRequest()
                snapshot = None
                if request.snapshot_id is not None:
                    snapshot = PolicySnapshotService(session).get_snapshot(request.snapshot_id)
                    if snapshot is not None and snapshot.channel_workspace_id != channel_id:
                        raise ValidationFailureError("policy snapshot does not belong to selected channel")
                else:
                    snapshots = PolicySnapshotService(session).list_snapshots(channel_id)
                    snapshot = snapshots[0] if snapshots else None
                if snapshot is None:
                    raise NotFoundError(f"policy snapshot not found for channel: {channel_id}")
                activated = ChannelProfileService(session).activate_snapshot(snapshot_id=snapshot.id)
                from sqlalchemy import select
                from app.db.models import ChannelInitDraft

                init_draft = session.scalars(
                    select(ChannelInitDraft).where(
                        ChannelInitDraft.channel_id == channel_id,
                        ChannelInitDraft.compiled_policy_snapshot_id == activated.id,
                    )
                ).first()
                if init_draft is not None:
                    init_draft.workflow_status = "ACTIVATED"
                    session.flush()
                return _snapshot_with_contract_state(activated)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/channels/{channel_id}/active-policy-snapshot", response_model=SnapshotRead | None)
    def get_active_policy_snapshot(channel_id: uuid.UUID) -> SnapshotRead | None:
        with session_scope() as session:
            snapshot = PolicySnapshotService(session).get_active_snapshot_for_channel(channel_id)
            return SnapshotRead.model_validate(_snapshot(snapshot)) if snapshot is not None else None


    return router
