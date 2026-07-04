from fastapi import APIRouter

from app.api.routes.imports import (
    CostEstimateCreateRequest,
    CostEstimateService,
    CostEstimateSnapshotRead,
    HumanPaidRenderApprovalCreateRequest,
    HumanPaidRenderApprovalDecisionRequest,
    HumanPaidRenderApprovalRead,
    HumanPaidRenderApprovalService,
    NotFoundError,
    PaidAttemptLimitRecordRead,
    PaidProviderBoundaryService,
    PaidProviderCallLedgerRead,
    ProviderBoundaryDecisionRead,
    ProviderBoundaryPreflightRequest,
    ProviderIdempotencyKeyCreateRequest,
    ProviderIdempotencyKeyRead,
    ProviderIdempotencyService,
    ProviderJobCreateRequest,
    ProviderJobService,
    ProviderJobSnapshotRead,
    ProxyPreviewArtifactFlagCreateRequest,
    ProxyPreviewArtifactFlagRead,
    ProxyPreviewGate,
    RenderRevisionCreateRequest,
    RenderRevisionRead,
    RenderRevisionService,
    session_scope,
    uuid,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/render-revisions", response_model=RenderRevisionRead)
    def create_render_revision(data: RenderRevisionCreateRequest) -> RenderRevisionRead:
        try:
            with session_scope() as session:
                revision = RenderRevisionService(session).create(data)
                return RenderRevisionRead.model_validate(revision)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/render-revisions/{revision_id}", response_model=RenderRevisionRead)
    def read_render_revision(revision_id: uuid.UUID) -> RenderRevisionRead:
        try:
            with session_scope() as session:
                return RenderRevisionRead.model_validate(RenderRevisionService(session).get(revision_id))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/cost-estimates", response_model=CostEstimateSnapshotRead)
    def create_cost_estimate(data: CostEstimateCreateRequest) -> CostEstimateSnapshotRead:
        try:
            with session_scope() as session:
                estimate = CostEstimateService(session).create(data)
                return CostEstimateSnapshotRead.model_validate(estimate)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/paid-render-approvals", response_model=HumanPaidRenderApprovalRead)
    def create_paid_render_approval(data: HumanPaidRenderApprovalCreateRequest) -> HumanPaidRenderApprovalRead:
        try:
            with session_scope() as session:
                approval = HumanPaidRenderApprovalService(session).create_pending(data)
                return HumanPaidRenderApprovalRead.model_validate(approval)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/paid-render-approvals/{approval_id}/approve", response_model=HumanPaidRenderApprovalRead)
    def approve_paid_render(approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest | None = None) -> HumanPaidRenderApprovalRead:
        try:
            with session_scope() as session:
                approval = HumanPaidRenderApprovalService(session).approve(approval_id, data or HumanPaidRenderApprovalDecisionRequest())
                return HumanPaidRenderApprovalRead.model_validate(approval)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/paid-render-approvals/{approval_id}/reject", response_model=HumanPaidRenderApprovalRead)
    def reject_paid_render(approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest | None = None) -> HumanPaidRenderApprovalRead:
        try:
            with session_scope() as session:
                approval = HumanPaidRenderApprovalService(session).reject(approval_id, data)
                return HumanPaidRenderApprovalRead.model_validate(approval)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/paid-render-approvals/{approval_id}/revoke", response_model=HumanPaidRenderApprovalRead)
    def revoke_paid_render(approval_id: uuid.UUID, data: HumanPaidRenderApprovalDecisionRequest | None = None) -> HumanPaidRenderApprovalRead:
        try:
            with session_scope() as session:
                approval = HumanPaidRenderApprovalService(session).revoke(approval_id, data)
                return HumanPaidRenderApprovalRead.model_validate(approval)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/provider-idempotency-keys", response_model=ProviderIdempotencyKeyRead)
    def create_provider_idempotency_key(data: ProviderIdempotencyKeyCreateRequest) -> ProviderIdempotencyKeyRead:
        try:
            with session_scope() as session:
                record = ProviderIdempotencyService(session).get_or_create(data)
                return ProviderIdempotencyKeyRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/provider-boundary/preflight", response_model=ProviderBoundaryDecisionRead)
    def provider_boundary_preflight(data: ProviderBoundaryPreflightRequest) -> ProviderBoundaryDecisionRead:
        try:
            with session_scope() as session:
                return PaidProviderBoundaryService(session).preflight(data)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/provider-jobs", response_model=ProviderJobSnapshotRead)
    def create_provider_job_snapshot(data: ProviderJobCreateRequest) -> ProviderJobSnapshotRead:
        try:
            with session_scope() as session:
                job = ProviderJobService(session).create_not_submitted(data)
                return ProviderJobSnapshotRead.model_validate(job)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/provider-jobs/{job_id}/timeout", response_model=ProviderJobSnapshotRead)
    def timeout_provider_job(job_id: uuid.UUID) -> ProviderJobSnapshotRead:
        try:
            with session_scope() as session:
                job = ProviderJobService(session).mark_timeout_resume_required(job_id)
                return ProviderJobSnapshotRead.model_validate(job)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/proxy-preview-artifact-flags", response_model=ProxyPreviewArtifactFlagRead)
    def create_proxy_preview_flag(data: ProxyPreviewArtifactFlagCreateRequest) -> ProxyPreviewArtifactFlagRead:
        try:
            with session_scope() as session:
                flag = ProxyPreviewGate(session).flag(data)
                return ProxyPreviewArtifactFlagRead.model_validate(flag)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/paid-provider-call-ledger/{ledger_id}", response_model=PaidProviderCallLedgerRead)
    def read_paid_provider_call_ledger(ledger_id: uuid.UUID) -> PaidProviderCallLedgerRead:
        try:
            with session_scope() as session:
                from app.db.models import PaidProviderCallLedger

                ledger = session.get(PaidProviderCallLedger, ledger_id)
                if ledger is None:
                    raise NotFoundError(f"paid provider call ledger not found: {ledger_id}")
                return PaidProviderCallLedgerRead.model_validate(ledger)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/paid-attempt-limit-records/{record_id}", response_model=PaidAttemptLimitRecordRead)
    def read_paid_attempt_limit_record(record_id: uuid.UUID) -> PaidAttemptLimitRecordRead:
        try:
            with session_scope() as session:
                from app.db.models import PaidAttemptLimitRecord

                record = session.get(PaidAttemptLimitRecord, record_id)
                if record is None:
                    raise NotFoundError(f"paid attempt limit record not found: {record_id}")
                return PaidAttemptLimitRecordRead.model_validate(record)
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
