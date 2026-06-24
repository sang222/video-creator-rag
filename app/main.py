import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.contracts import (
    ApprovalDecisionCreate,
    ApprovalDecisionRead,
    ArtifactCreate,
    ArtifactRead,
    ArtifactVersionCreate,
    ArtifactVersionRead,
    ChannelMembershipCreate,
    ChannelMembershipRead,
    ChannelProfileCompileRequest,
    ChannelProfileCompileResult,
    ChannelProfileVersionCreate,
    ChannelProfileVersionRead,
    ChannelWorkspaceCreate,
    ChannelWorkspaceRead,
    GateRunCreate,
    GateRunRead,
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
    ReviewFindingCreate,
    ReviewFindingRead,
    ReviewTaskCreate,
    ReviewTaskRead,
    RevisionRequestCreate,
    RevisionRequestRead,
    RevisionResolveRequest,
    VideoProjectCreate,
    VideoProjectRead,
)
from app.contracts.policy_snapshot import CompiledChannelPolicySnapshot as SnapshotRead
from app.core.config import get_settings
from app.core.db import check_database
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailureError
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.services import (
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
    PolicyCatalogService,
    PolicyChangeService,
    PolicyRevalidationService,
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
