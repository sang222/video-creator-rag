from fastapi import APIRouter, Request

from app.api.routes.imports import (
    Any,
    ApprovalDecisionCreate,
    ApprovalDecisionRead,
    ApprovalService,
    ArtifactCreate,
    ArtifactRead,
    ArtifactService,
    ArtifactVersionCreate,
    ArtifactVersionRead,
    GateDefinitionService,
    GateRunCreate,
    GateRunRead,
    GateRunnerService,
    NotFoundError,
    PlatformPolicyCatalogCreate,
    PlatformPolicyCatalogRead,
    PlatformPolicyVersionCreate,
    PlatformPolicyVersionRead,
    PolicyCatalogService,
    PolicyChangeRecordCreate,
    PolicyChangeRecordRead,
    PolicyChangeService,
    PolicyChangeStateRequest,
    PolicyRevalidationBatchCreate,
    PolicyRevalidationBatchRead,
    PolicyRevalidationService,
    PolicySourceRefCreate,
    PolicySourceRefRead,
    ReviewFindingCreate,
    ReviewFindingRead,
    ReviewService,
    ReviewTaskCreate,
    ReviewTaskRead,
    RevisionRequestCreate,
    RevisionRequestRead,
    RevisionResolveRequest,
    WorkflowReadinessService,
    session_scope,
    uuid,
)

from app.api.routes.serializers_core import (
    _approval_decision,
    _artifact,
    _artifact_version,
    _gate_run,
    _policy_catalog,
    _policy_change_record,
    _policy_revalidation_batch,
    _policy_source_ref,
    _policy_version,
    _review_finding,
    _review_task,
    _revision_request,
)

from app.api.routes.serializers_publish_learning import (
    _as_http_error,
)
from app.services.security_boundary import actor_from_request



def create_router() -> APIRouter:
    router = APIRouter()

    @router.post("/artifacts", response_model=ArtifactRead)
    def create_artifact(data: ArtifactCreate) -> ArtifactRead:
        try:
            with session_scope() as session:
                artifact = ArtifactService(session).create_artifact(
                    data=data,
                    public_write=True,
                )
                return ArtifactRead.model_validate(_artifact(artifact))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/artifact-versions", response_model=ArtifactVersionRead)
    def create_artifact_version(data: ArtifactVersionCreate) -> ArtifactVersionRead:
        try:
            with session_scope() as session:
                version = ArtifactService(session).create_artifact_version(
                    data=data,
                    public_write=True,
                )
                return ArtifactVersionRead.model_validate(_artifact_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/review-tasks", response_model=ReviewTaskRead)
    def create_review_task(data: ReviewTaskCreate) -> ReviewTaskRead:
        try:
            with session_scope() as session:
                review_task = ReviewService(session).create_review_task(data=data)
                return ReviewTaskRead.model_validate(_review_task(review_task))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/review-findings", response_model=ReviewFindingRead)
    def add_review_finding(data: ReviewFindingCreate) -> ReviewFindingRead:
        try:
            with session_scope() as session:
                finding = ReviewService(session).add_finding(data=data)
                return ReviewFindingRead.model_validate(_review_finding(finding))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/revision-requests", response_model=RevisionRequestRead)
    def create_revision_request(data: RevisionRequestCreate) -> RevisionRequestRead:
        try:
            with session_scope() as session:
                revision = ReviewService(session).create_revision_request(data=data)
                return RevisionRequestRead.model_validate(_revision_request(revision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/revision-requests/{revision_request_id}/resolve", response_model=RevisionRequestRead)
    def resolve_revision_request(
        revision_request_id: uuid.UUID,
        data: RevisionResolveRequest,
        request: Request,
    ) -> RevisionRequestRead:
        try:
            actor = actor_from_request(request)
            with session_scope() as session:
                revision = ReviewService(session).resolve_revision_request(
                    revision_request_id=revision_request_id,
                    resolved_by_artifact_version_id=data.resolved_by_artifact_version_id,
                    actor_user_id=actor.actor_id,
                )
                return RevisionRequestRead.model_validate(_revision_request(revision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/approval-decisions", response_model=ApprovalDecisionRead)
    def create_approval_decision(
        data: ApprovalDecisionCreate,
        request: Request,
    ) -> ApprovalDecisionRead:
        try:
            actor = actor_from_request(request)
            data = data.model_copy(update={"decided_by_user_id": actor.actor_id})
            with session_scope() as session:
                decision = ApprovalService(session).create_approval_decision(data=data)
                return ApprovalDecisionRead.model_validate(_approval_decision(decision))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/gates/seed-definitions")
    def seed_gate_definitions() -> dict[str, int]:
        try:
            with session_scope() as session:
                records = GateDefinitionService(session).seed_definitions()
                return {"count": len(records)}
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/gates/run", response_model=GateRunRead)
    def run_gate(data: GateRunCreate) -> GateRunRead:
        try:
            with session_scope() as session:
                gate_run = GateRunnerService(session).run_gate(data=data)
                return GateRunRead.model_validate(_gate_run(gate_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/gate-runs/{gate_run_id}", response_model=GateRunRead)
    def get_gate_run(gate_run_id: uuid.UUID) -> GateRunRead:
        try:
            with session_scope() as session:
                gate_run = GateRunnerService(session).get_gate_run(gate_run_id)
                if gate_run is None:
                    raise NotFoundError(f"gate run not found: {gate_run_id}")
                return GateRunRead.model_validate(_gate_run(gate_run))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/gate-runs", response_model=list[GateRunRead])
    def list_project_gate_runs(project_id: uuid.UUID) -> list[GateRunRead]:
        try:
            with session_scope() as session:
                return [GateRunRead.model_validate(_gate_run(run)) for run in GateRunnerService(session).list_project_gate_runs(project_id)]
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.get("/video-projects/{project_id}/readiness")
    def inspect_project_readiness(project_id: uuid.UUID) -> dict[str, Any]:
        try:
            with session_scope() as session:
                return WorkflowReadinessService(session).inspect_project(project_id)
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-catalogs", response_model=PlatformPolicyCatalogRead)
    def create_policy_catalog(data: PlatformPolicyCatalogCreate) -> PlatformPolicyCatalogRead:
        try:
            with session_scope() as session:
                catalog = PolicyCatalogService(session).create_catalog(data=data)
                return PlatformPolicyCatalogRead.model_validate(_policy_catalog(catalog))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-versions", response_model=PlatformPolicyVersionRead)
    def create_policy_version(data: PlatformPolicyVersionCreate) -> PlatformPolicyVersionRead:
        try:
            with session_scope() as session:
                version = PolicyCatalogService(session).create_version(data=data)
                return PlatformPolicyVersionRead.model_validate(_policy_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-versions/{policy_version_id}/activate", response_model=PlatformPolicyVersionRead)
    def activate_policy_version(policy_version_id: uuid.UUID) -> PlatformPolicyVersionRead:
        try:
            with session_scope() as session:
                version = PolicyCatalogService(session).activate_version(policy_version_id)
                return PlatformPolicyVersionRead.model_validate(_policy_version(version))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-source-refs", response_model=PolicySourceRefRead)
    def create_policy_source_ref(data: PolicySourceRefCreate) -> PolicySourceRefRead:
        try:
            with session_scope() as session:
                ref = PolicyCatalogService(session).attach_source_ref(data=data)
                return PolicySourceRefRead.model_validate(_policy_source_ref(ref))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-change-records", response_model=PolicyChangeRecordRead)
    def create_policy_change_record(data: PolicyChangeRecordCreate) -> PolicyChangeRecordRead:
        try:
            with session_scope() as session:
                record = PolicyChangeService(session).create_change_record(data=data)
                return PolicyChangeRecordRead.model_validate(_policy_change_record(record))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-change-records/{policy_change_record_id}/state", response_model=PolicyChangeRecordRead)
    def transition_policy_change(policy_change_record_id: uuid.UUID, data: PolicyChangeStateRequest) -> PolicyChangeRecordRead:
        try:
            with session_scope() as session:
                record = PolicyChangeService(session).transition_state(policy_change_record_id, data.state)
                return PolicyChangeRecordRead.model_validate(_policy_change_record(record))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-revalidation-batches", response_model=PolicyRevalidationBatchRead)
    def create_policy_revalidation_batch(data: PolicyRevalidationBatchCreate) -> PolicyRevalidationBatchRead:
        try:
            with session_scope() as session:
                batch = PolicyRevalidationService(session).create_batch(data=data)
                return PolicyRevalidationBatchRead.model_validate(_policy_revalidation_batch(batch))
        except Exception as exc:
            raise _as_http_error(exc) from exc

    @router.post("/policy-revalidation-batches/{batch_id}/run", response_model=PolicyRevalidationBatchRead)
    def run_policy_revalidation_batch(batch_id: uuid.UUID) -> PolicyRevalidationBatchRead:
        try:
            with session_scope() as session:
                batch = PolicyRevalidationService(session).run_batch(batch_id)
                return PolicyRevalidationBatchRead.model_validate(_policy_revalidation_batch(batch))
        except Exception as exc:
            raise _as_http_error(exc) from exc


    return router
