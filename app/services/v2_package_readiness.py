"""Trusted in-process boundary for v2 support, package, and readiness stages."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_package import (
    ExactContentRefV2,
    ProductionDurationContractV2,
    ProductionPackageContentV2,
    ProductionPackageCreateV2,
    ProductionReadinessEvidenceV2,
)
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowEffectState,
    WorkflowFailureClassification,
    WorkflowStageResult,
)
from app.contracts.vcos_v2 import ProductionLane
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m5 import (
    AudienceTargetPack,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchIntentMap,
)
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.r3d7 import (
    AgentMemoryApplicationRecord,
    MemoryInfluenceManifest,
)
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.production_package import (
    ProductionPackageService,
    ProductionReadinessService,
    semantic_hash,
    strategic_lineage_from_record,
)
from app.services.production_workflow import (
    PreReadinessProductionGatewayDescriptor,
    WorkflowStageContext,
    WorkflowStageError,
    command_id_for,
)
from app.services.v2_support_authority import (
    V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
    V2ClaimSourceBinding,
    V2FinalReviewOnlyDestinationAuthority,
    V2FrozenSupportEnvelope,
    _audience_source,
    _editorial_slot_source,
    _effective_context_hash,
    _preflight_source,
    _project_authority_hash,
    _search_intent_source,
    _destination_authority as _support_destination_authority,
)
from app.services.workflow import ArtifactService


V2_SUPPORT_COMPILER_VERSION = "vcos.v2-support-compiler.v2"
_SUPPORT_TYPES = (
    "research_pack",
    "source_pack",
    "niche_alignment_dossier",
    "market_alignment_dossier",
    "script",
    "visual_plan",
    "thumbnail_brief",
    "publishing_metadata_package",
    "rights_disclosure_completeness_report",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "destination_binding",
)


@runtime_checkable
class V2TrustedSupportProducer(Protocol):
    """Persist package support artifacts through trusted domain services."""

    def produce_support(self, context: WorkflowStageContext) -> WorkflowStageResult: ...


@runtime_checkable
class V2ProductionPackageInputBuilder(Protocol):
    """Resolve exact support refs into a canonical package create request."""

    def build_package(
        self, context: WorkflowStageContext
    ) -> ProductionPackageCreateV2: ...


@dataclass(frozen=True, slots=True)
class _SupportAuthority:
    project: VideoProject
    admission: ProjectAdmissionDecision
    channel: ChannelWorkspace
    profile: ChannelProfileVersion
    policy: CompiledChannelPolicySnapshot
    effective: EffectiveChannelRuntimeContextSnapshot
    duration: ProductionDurationContractV2
    source_payload: dict[str, Any]
    source_refs: list[dict[str, Any]]
    approved_script: str
    destination: dict[str, Any]
    support_envelope_artifact: Artifact | None = None
    support_envelope_version: ArtifactVersion | None = None
    support_envelope: V2FrozenSupportEnvelope | None = None


@dataclass(frozen=True, slots=True)
class CanonicalV2SupportCompiler:
    """Project an approved frozen-support envelope into package support.

    Long-form projects must already carry the exact immutable envelope
    produced by ``V2SupportAuthorityService``. This compiler never calls an
    LLM and never treats caller-authored planning prose as script authority.
    """

    def produce_support(self, context: WorkflowStageContext) -> WorkflowStageResult:
        authority = _support_authority(context)
        versions = _ensure_support_versions(context, authority)
        manifest = _support_manifest(authority, versions)
        return WorkflowStageResult(
            result_type="production_support_authorities",
            result_id=versions["research_pack"].id,
            result_ref=(
                f"v2-support-manifest://{authority.project.id}/{context.command_id}"
            ),
            result_hash=semantic_hash(manifest),
            result_payload=manifest,
            authority_refs=_base_refs(context),
            reason_codes=["V2_SUPPORT_AUTHORITIES_COMPILED"],
            effect_state=WorkflowEffectState.COMPLETED,
        )

    def build_package(self, context: WorkflowStageContext) -> ProductionPackageCreateV2:
        authority = _support_authority(context)
        versions = _ensure_support_versions(context, authority)
        qualification = _qualification_projection(context, authority)
        if not qualification["script_gates_pass"]:
            # A trusted support envelope may describe a local/offline
            # qualification fixture, but it cannot become a production
            # package.  The package boundary requires the sealed current
            # script-qualification receipt that `_qualification_projection`
            # verifies for real long-form execution.
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
                error_code="V2_SCRIPT_QUALIFICATION_REQUIRED",
                summary=(
                    "ProductionPackage v2 requires a sealed current script "
                    "qualification authority."
                ),
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
            )
        admission = authority.admission
        project_lineage = strategic_lineage_from_record(
            authority.project,
            invalid_reason_code="VIDEO_PROJECT_STRATEGIC_LINEAGE_INVALID",
        )
        admission_lineage = strategic_lineage_from_record(
            admission,
            invalid_reason_code="PROJECT_ADMISSION_STRATEGIC_LINEAGE_INVALID",
        )
        if project_lineage is None or admission_lineage is None:
            raise ValidationFailureError("V2_PACKAGE_STRATEGIC_LINEAGE_REQUIRED")
        if project_lineage != admission_lineage:
            raise ValidationFailureError("V2_PACKAGE_STRATEGIC_LINEAGE_MISMATCH")
        script = versions["script"].content
        claims = list(script.get("supported_claims") or [])
        sections = list(script.get("sections") or [])
        return ProductionPackageCreateV2(
            content=ProductionPackageContentV2(
                company_id=authority.project.company_id,
                channel_workspace_id=authority.project.channel_workspace_id,
                video_project_id=authority.project.id,
                project_admission_decision_id=admission.id,
                project_admission_decision_hash=str(admission.decision_hash),
                channel_profile_version_id=authority.profile.id,
                channel_profile_hash=authority.profile.profile_input_hash,
                compiled_policy_snapshot_id=authority.policy.id,
                compiled_policy_snapshot_hash=authority.policy.content_hash,
                strategic_lineage=project_lineage,
                effective_context_ref=ExactContentRefV2(
                    type="effective_context",
                    ref=f"effective-context://{authority.effective.id}",
                    id=authority.effective.id,
                    version=1,
                    content_hash=authority.effective.context_hash,
                ),
                production_lane=admission.production_lane,
                assignment_mode=admission.assignment_mode,
                content_mode=admission.content_mode,
                series_plan_id=admission.series_plan_id,
                series_run_id=admission.series_run_id,
                episode_number=admission.episode_number,
                episode_role=admission.episode_role,
                standalone_reason_code=admission.standalone_reason_code,
                duration_contract=authority.duration,
                support_envelope_ref=(
                    _exact_ref(
                        "frozen_support_envelope",
                        authority.support_envelope_version,
                    )
                    if authority.support_envelope_version is not None
                    else None
                ),
                production_visual_policy_version=(
                    "vcos.production-visual-policy.ai-only.v1"
                    if authority.support_envelope is not None
                    and authority.support_envelope.production_visual_policy_ref
                    is not None
                    else None
                ),
                production_visual_policy_ref=(
                    authority.support_envelope.production_visual_policy_ref
                    if authority.support_envelope is not None
                    else None
                ),
                production_visual_policy_hash=(
                    authority.support_envelope.production_visual_policy_hash
                    if authority.support_envelope is not None
                    else None
                ),
                active_primary_visual_routes=(
                    authority.support_envelope.active_primary_visual_routes
                    if authority.support_envelope is not None
                    else []
                ),
                research_refs=[_exact_ref("research", versions["research_pack"])],
                source_refs=[_exact_ref("source", versions["source_pack"])],
                niche_market_gate_refs=[
                    _exact_ref(
                        "niche_gate",
                        versions["niche_alignment_dossier"],
                    ),
                    _exact_ref(
                        "market_gate",
                        versions["market_alignment_dossier"],
                    ),
                ],
                script_ref=_exact_ref("script", versions["script"]),
                visual_plan_ref=_exact_ref("visual_plan", versions["visual_plan"]),
                thumbnail_refs=[_exact_ref("thumbnail", versions["thumbnail_brief"])],
                metadata_ref=_exact_ref(
                    "metadata",
                    versions["publishing_metadata_package"],
                ),
                rights_disclosure_refs=[
                    _exact_ref(
                        "rights",
                        versions["rights_disclosure_completeness_report"],
                    )
                ],
                provider_execution_plan_ref=_exact_ref(
                    "provider_plan",
                    versions["provider_execution_plan"],
                ),
                budget_scope_ref=_exact_ref(
                    "budget_scope",
                    versions["cost_estimate_snapshot"],
                ),
                destination_binding_ref=_exact_ref(
                    "destination",
                    versions["destination_binding"],
                ),
                readiness_evidence=ProductionReadinessEvidenceV2(
                    research_evidence_complete=True,
                    niche_market_gates_pass=True,
                    assignment_integrity_pass=True,
                    # These values are projections from the immutable current
                    # qualification receipt, never caller/package assertions.
                    editorial_depth_sufficient=qualification[
                        "editorial_depth_sufficient"
                    ],
                    supported_claim_count=len(claims),
                    distinct_editorial_section_count=len(sections),
                    research_coverage_ratio=qualification["research_coverage_ratio"],
                    script_duration_ms=int(script["estimated_duration_ms"]),
                    anti_padding_pass=qualification["anti_padding_pass"],
                    padding_phrase_hits=0,
                    repeated_sentence_ratio=float(script["repeated_sentence_ratio"]),
                    script_gates_pass=qualification["script_gates_pass"],
                    visual_thumbnail_metadata_gates_pass=True,
                    rights_disclosure_gates_pass=True,
                    provider_plan_valid=True,
                    budget_scope_valid=True,
                    package_integrity_inputs_complete=True,
                    unresolved_exception_types=[],
                    new_planning_cycle=False,
                ),
            ),
            created_by_user_id=authority.project.created_by_user_id,
        )


@dataclass(frozen=True, slots=True)
class V2PackageReadinessGateway:
    """Effectful package service composition used by the durable worker.

    Missing support dependencies block explicitly.  Existing immutable support
    and package authorities are reconciled, while readiness is always invoked
    through the canonical evaluator and therefore needs no manual API/CLI step.
    """

    support_producer: V2TrustedSupportProducer | None = None
    package_builder: V2ProductionPackageInputBuilder | None = None
    descriptor: PreReadinessProductionGatewayDescriptor = (
        PreReadinessProductionGatewayDescriptor(
            gateway_id="v2-package",
            version="1.0.0",
            supported_lanes=frozenset({ProductionLane.LONG_FORM}),
            production_eligible=True,
            fixture_only=False,
            invokes_mr1=False,
            paid_provider_calls=False,
            automatic_publish=False,
        )
    )

    def __post_init__(self) -> None:
        if self.support_producer is not None and not isinstance(
            self.support_producer, V2TrustedSupportProducer
        ):
            raise TypeError("V2_TRUSTED_SUPPORT_PRODUCER_INVALID")
        if self.package_builder is not None and not isinstance(
            self.package_builder, V2ProductionPackageInputBuilder
        ):
            raise TypeError("V2_PACKAGE_INPUT_BUILDER_INVALID")

    def produce_support(self, context: WorkflowStageContext) -> WorkflowStageResult:
        existing = _current_package(context)
        if existing is not None:
            return WorkflowStageResult(
                result_type="production_support_authorities",
                result_id=existing.id,
                result_ref=f"artifact-version://{existing.id}",
                result_hash=existing.content_hash,
                authority_refs=_base_refs(context),
                reason_codes=["PRODUCTION_SUPPORT_AUTHORITY_RECONCILED"],
                effect_state=WorkflowEffectState.RECONCILED,
            )
        if self.support_producer is None:
            raise _producer_missing("V2_TRUSTED_SUPPORT_PRODUCER_REQUIRED")
        result = self.support_producer.produce_support(context)
        if not isinstance(result, WorkflowStageResult):
            result = WorkflowStageResult.model_validate(result)
        return result.model_copy(
            update={
                "authority_refs": _merge_refs(
                    _base_refs(context), result.authority_refs
                )
            }
        )

    def create_package(self, context: WorkflowStageContext) -> WorkflowStageResult:
        existing = _current_package(context)
        if existing is None:
            if self.package_builder is None:
                raise _producer_missing("V2_PACKAGE_INPUT_BUILDER_REQUIRED")
            request = self.package_builder.build_package(context)
            if not isinstance(request, ProductionPackageCreateV2):
                request = ProductionPackageCreateV2.model_validate(request)
            if (
                request.content.video_project_id != context.run.video_project_id
                or request.content.production_lane.value != context.run.production_lane
            ):
                raise ValidationFailureError("V2_PACKAGE_BUILDER_SCOPE_MISMATCH")
            created = ProductionPackageService(context.session).create_package(request)
            existing = context.session.get(ArtifactVersion, created.artifact_version_id)
            if existing is None:
                raise ValidationFailureError("V2_PACKAGE_CREATE_RESULT_MISSING")
            effect_state = WorkflowEffectState.COMPLETED
            reason = "PRODUCTION_PACKAGE_CREATED"
        else:
            effect_state = WorkflowEffectState.RECONCILED
            reason = "PRODUCTION_PACKAGE_RECONCILED"
        refs = _base_refs(context).model_copy(
            update={
                "production_package_artifact_version_id": existing.id,
                "production_package_hash": existing.content_hash,
            }
        )
        return WorkflowStageResult(
            result_type="production_package",
            result_id=existing.id,
            result_ref=f"artifact-version://{existing.id}",
            result_hash=existing.content_hash,
            authority_refs=refs,
            reason_codes=[reason],
            effect_state=effect_state,
        )

    def evaluate_readiness(self, context: WorkflowStageContext) -> WorkflowStageResult:
        package = _current_package(context)
        if package is None:
            raise _producer_missing("V2_PRODUCTION_PACKAGE_REQUIRED")
        project = _project(context)
        evaluation = ProductionReadinessService(context.session).evaluate(
            package_artifact_version_id=package.id,
            created_by_user_id=project.created_by_user_id,
        )
        if evaluation.status != "READY_FOR_PRODUCTION" or evaluation.receipt is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="PRODUCTION_READINESS_BLOCKED",
                summary="Canonical ProductionPackage v2 failed readiness.",
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
                operator_visible_blocker=";".join(evaluation.blocker_reason_codes)
                or "Production readiness is blocked.",
            )
        refs = _base_refs(context).model_copy(
            update={
                "production_package_artifact_version_id": package.id,
                "production_package_hash": package.content_hash,
                "production_readiness_receipt_artifact_version_id": (
                    evaluation.receipt.artifact_version_id
                ),
                "production_readiness_receipt_hash": (evaluation.receipt.receipt_hash),
            }
        )
        return WorkflowStageResult(
            result_type="production_readiness_receipt",
            result_id=evaluation.receipt.artifact_version_id,
            result_ref=(f"artifact-version://{evaluation.receipt.artifact_version_id}"),
            result_hash=evaluation.receipt.receipt_hash,
            authority_refs=refs,
            reason_codes=["PRODUCTION_READINESS_VERIFIED"],
        )


def build_v2_package_readiness_gateway() -> V2PackageReadinessGateway:
    """Build the normal in-repo support/package/readiness composition."""

    compiler = CanonicalV2SupportCompiler()
    return V2PackageReadinessGateway(
        support_producer=compiler,
        package_builder=compiler,
    )


def _support_authority(
    context: WorkflowStageContext,
) -> _SupportAuthority:
    project = _project(context)
    admission = context.session.get(
        ProjectAdmissionDecision,
        project.project_admission_decision_id,
    )
    channel = context.session.get(ChannelWorkspace, project.channel_workspace_id)
    profile = context.session.get(
        ChannelProfileVersion, project.channel_profile_version_id
    )
    policy = context.session.get(
        CompiledChannelPolicySnapshot, project.policy_snapshot_id
    )
    effective = (
        context.session.get(
            EffectiveChannelRuntimeContextSnapshot,
            project.effective_context_snapshot_id,
        )
        if project.effective_context_snapshot_id is not None
        else None
    )
    if (
        admission is None
        or channel is None
        or profile is None
        or policy is None
        or effective is None
        or admission.decision != "ADMIT"
        or admission.admitted_video_project_id != project.id
        or admission.decision_hash is None
        or len(admission.decision_hash) != 64
        or admission.company_id != project.company_id
        or admission.channel_workspace_id != project.channel_workspace_id
        or admission.channel_profile_version_id != profile.id
        or admission.policy_snapshot_id != policy.id
        or profile.channel_workspace_id != channel.id
        or policy.channel_workspace_id != channel.id
        or policy.channel_profile_version_id != profile.id
        or effective.video_project_id != project.id
        or effective.context_hash is None
        or effective.compile_status != "PASS"
    ):
        raise ValidationFailureError("V2_SUPPORT_FROZEN_AUTHORITY_MISMATCH")
    try:
        duration = ProductionDurationContractV2.model_validate(
            project.duration_contract
        )
    except Exception as exc:
        raise ValidationFailureError("V2_SUPPORT_DURATION_AUTHORITY_INVALID") from exc
    if (
        duration.source_profile_version_id != profile.id
        or duration.source_policy_snapshot_id != policy.id
        or admission.duration_contract != project.duration_contract
    ):
        raise ValidationFailureError("V2_SUPPORT_DURATION_AUTHORITY_MISMATCH")

    (
        support_envelope_artifact,
        support_envelope_version,
        support_envelope,
    ) = _require_frozen_support_envelope(
        context=context,
        project=project,
        admission=admission,
        channel=channel,
        profile=profile,
        policy=policy,
        effective=effective,
        duration=duration,
    )
    source_refs = [
        source.model_dump(mode="json") for source in support_envelope.frozen_sources
    ]
    source_payload = {
        "schema_version": "vcos.frozen-support-source-projection.v1",
        "support_envelope_ref": {
            "type": V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
            "id": str(support_envelope_artifact.id),
            "artifact_version_id": str(support_envelope_version.id),
            "version": support_envelope_version.version_number,
            "content_hash": support_envelope_version.content_hash,
        },
        "frozen_sources": source_refs,
        "claim_source_bindings": [
            binding.model_dump(mode="json")
            for binding in support_envelope.claim_source_bindings
        ],
    }
    approved_script = support_envelope.approved_script.approved_script_text
    destination_authority = support_envelope.verified_destination
    review_only = isinstance(
        destination_authority, V2FinalReviewOnlyDestinationAuthority
    )
    destination = {
        **destination_authority.binding.model_dump(mode="json"),
        "active_binding_ref": destination_authority.active_binding_ref,
        "destination_binding_hash": destination_authority.content_hash,
        "destination_model_hash": destination_authority.destination_hash,
        "destination_authority_hash": destination_authority.content_hash,
        "destination_mode": (
            "FINAL_REVIEW_ONLY" if review_only else "VERIFIED_PUBLISH_DESTINATION"
        ),
        "destination_handle": destination_authority.binding.channel_handle,
        "publish_execution_allowed": not review_only,
        "automatic_publish": False,
        **(
            {
                "publish_policy": "NO_PUBLISH",
                "controlled_recovery_authority_id": str(
                    destination_authority.controlled_recovery_authority_id
                ),
                "controlled_recovery_authority_hash": (
                    destination_authority.controlled_recovery_authority_hash
                ),
                "settlement_authority_id": str(
                    destination_authority.settlement_authority_id
                ),
                "settlement_authority_hash": (
                    destination_authority.settlement_authority_hash
                ),
                "settlement_qualification_run_id": str(
                    destination_authority.settlement_qualification_run_id
                ),
                "settlement_provenance_hash": (
                    destination_authority.settlement_provenance_hash
                ),
            }
            if review_only
            else {}
        ),
    }
    return _SupportAuthority(
        project=project,
        admission=admission,
        channel=channel,
        profile=profile,
        policy=policy,
        effective=effective,
        duration=duration,
        source_payload=source_payload,
        source_refs=source_refs,
        approved_script=approved_script,
        destination=destination,
        support_envelope_artifact=support_envelope_artifact,
        support_envelope_version=support_envelope_version,
        support_envelope=support_envelope,
    )


def _require_frozen_support_envelope(
    *,
    context: WorkflowStageContext,
    project: VideoProject,
    admission: ProjectAdmissionDecision,
    channel: ChannelWorkspace,
    profile: ChannelProfileVersion,
    policy: CompiledChannelPolicySnapshot,
    effective: EffectiveChannelRuntimeContextSnapshot,
    duration: ProductionDurationContractV2,
) -> tuple[Artifact, ArtifactVersion, V2FrozenSupportEnvelope]:
    """Resolve the one approved support envelope and recheck exact bindings."""

    artifacts = list(
        context.session.scalars(
            select(Artifact)
            .where(
                Artifact.video_project_id == project.id,
                Artifact.artifact_type == V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
            )
            .order_by(Artifact.created_at, Artifact.id)
        ).all()
    )
    if not artifacts:
        raise WorkflowStageError(
            classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
            error_code="V2_FROZEN_SUPPORT_ENVELOPE_REQUIRED",
            summary=(
                "The approved LLM-produced support envelope has not been "
                "prepared for this project."
            ),
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
            operator_visible_blocker=(
                "Chuẩn bị lại dự án qua launcher sau khi LLMRouter và "
                "destination đã sẵn sàng; workflow không tự tạo script."
            ),
        )
    if len(artifacts) != 1:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_ENVELOPE_CARDINALITY_MISMATCH"
        )
    artifact = artifacts[0]
    version = (
        context.session.get(ArtifactVersion, artifact.current_version_id)
        if artifact.current_version_id is not None
        else None
    )
    domain = (
        (version.packaging_metadata or {}).get("_vcos_domain_authority")
        if version is not None
        else None
    )
    try:
        envelope = (
            V2FrozenSupportEnvelope.model_validate(version.content)
            if version is not None
            else None
        )
    except Exception as exc:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_ENVELOPE_INVALID"
        ) from exc
    if (
        version is None
        or envelope is None
        or artifact.status != "approved"
        or version.status != "approved"
        or version.artifact_id != artifact.id
        or semantic_hash(version.content) != version.content_hash
        or not isinstance(domain, dict)
        or domain.get("schema_version") != "vcos.domain-authority.v1"
        or domain.get("writer") != "server_domain_service"
        or domain.get("artifact_type") != V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE
        or domain.get("content_hash") != version.content_hash
        or (version.packaging_metadata or {}).get("input_fingerprint")
        != envelope.input_fingerprint
        or (version.packaging_metadata or {}).get("idempotency_hash")
        != envelope.idempotency_hash
    ):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_ENVELOPE_INTEGRITY_MISMATCH"
        )

    qualification_gate = next(
        (
            item
            for item in envelope.gate_receipts
            if isinstance(item, dict) and item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    try:
        qualification_run_id = (
            uuid.UUID(str(qualification_gate["script_qualification_run_id"]))
            if isinstance(qualification_gate, dict)
            else None
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_QUALIFICATION_LINEAGE_INVALID"
        ) from exc
    try:
        expected_destination = _support_destination_authority(
            context.session,
            channel=channel,
            execution_mode=envelope.execution_mode,
            script_qualification_run_id=qualification_run_id,
        )
    except ValidationFailureError as exc:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_DESTINATION_AUTHORITY_DRIFT"
        ) from exc
    if (
        envelope.project_ref.id != project.id
        or envelope.project_ref.type != "video_project"
        or envelope.project_ref.content_hash != _project_authority_hash(project)
        or envelope.admission_ref.id != admission.id
        or envelope.admission_ref.type != "project_admission_decision"
        or envelope.admission_ref.content_hash != admission.decision_hash
        or envelope.profile_ref.id != profile.id
        or envelope.profile_ref.type != "channel_profile_version"
        or envelope.profile_ref.content_hash != profile.profile_input_hash
        or envelope.compiled_policy_ref.id != policy.id
        or envelope.compiled_policy_ref.type != "compiled_channel_policy_snapshot"
        or envelope.compiled_policy_ref.content_hash != policy.content_hash
        or envelope.effective_context_ref.id != effective.id
        or envelope.effective_context_ref.type != "effective_channel_runtime_context"
        or envelope.effective_context_ref.content_hash
        != _effective_context_hash(effective)
        or envelope.effective_context_ref.content_hash != effective.context_hash
        or envelope.production_lane != project.production_lane
        or envelope.duration_contract != duration
        or envelope.verified_destination != expected_destination
    ):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_ENVELOPE_AUTHORITY_DRIFT"
        )
    _validate_frozen_source_refs(
        context=context,
        admission=admission,
        envelope=envelope,
    )
    _validate_frozen_script_receipt(envelope)
    _validate_memory_guidance_authority(
        context=context,
        project=project,
        effective=effective,
        envelope=envelope,
    )
    _validate_frozen_gate_receipts(envelope)
    return artifact, version, envelope


def _validate_frozen_source_refs(
    *,
    context: WorkflowStageContext,
    admission: ProjectAdmissionDecision,
    envelope: V2FrozenSupportEnvelope,
) -> None:
    preflight = (
        context.session.get(
            IdeaMarketPreflight,
            admission.idea_market_preflight_id,
        )
        if admission.idea_market_preflight_id is not None
        else None
    )
    required_ids = {
        "editorial_calendar_slot": admission.editorial_calendar_slot_id,
        "idea_market_preflight": admission.idea_market_preflight_id,
    }
    builders: dict[str, tuple[type[Any], Any]] = {
        "editorial_calendar_slot": (
            EditorialCalendarSlot,
            _editorial_slot_source,
        ),
        "idea_market_preflight": (IdeaMarketPreflight, _preflight_source),
        "search_intent_map": (SearchIntentMap, _search_intent_source),
        "audience_target_pack": (AudienceTargetPack, _audience_source),
    }
    planning_sources = [
        source for source in envelope.frozen_sources if source.type in builders
    ]
    source_by_type = {source.type: source for source in planning_sources}
    if len(source_by_type) != len(planning_sources) or any(
        source_by_type.get(source_type) is None
        or source_by_type[source_type].id != source_id
        for source_type, source_id in required_ids.items()
    ):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_SOURCE_BINDING_MISMATCH"
        )
    if preflight is None:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_SOURCE_BINDING_MISMATCH"
        )
    if (
        preflight.search_intent_map_id is not None
        and (
            source_by_type.get("search_intent_map") is None
            or source_by_type["search_intent_map"].id != preflight.search_intent_map_id
        )
    ) or (
        preflight.audience_target_pack_id is not None
        and (
            source_by_type.get("audience_target_pack") is None
            or source_by_type["audience_target_pack"].id
            != preflight.audience_target_pack_id
        )
    ):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_TYPED_SOURCE_MISMATCH"
        )

    for source in planning_sources:
        binding = builders[source.type]
        model, builder = binding
        row = context.session.get(model, source.id)
        try:
            current = builder(row) if row is not None else None
        except Exception as exc:
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_SOURCE_DRIFT"
            ) from exc
        if current is None or current != source:
            raise _support_envelope_integrity_error("V2_FROZEN_SUPPORT_SOURCE_DRIFT")

    factual_sources = [
        source
        for source in envelope.frozen_sources
        if source.type == "search_demand_evidence"
    ]
    if len(planning_sources) + len(factual_sources) != len(envelope.frozen_sources):
        raise _support_envelope_integrity_error("V2_FROZEN_SUPPORT_SOURCE_TYPE_INVALID")
    qualification_gate = next(
        (
            item
            for item in envelope.gate_receipts
            if isinstance(item, dict) and item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    if factual_sources and not isinstance(qualification_gate, dict):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_QUALIFICATION_EVIDENCE_UNBOUND"
        )
    if qualification_gate is not None:
        from app.services.script_qualification import ScriptQualificationService
        from app.services.v2_support_authority import V2SupportAuthorityService

        try:
            qualification = ScriptQualificationService(context.session).require_pass(
                uuid.UUID(str(qualification_gate["script_qualification_run_id"]))
            )
            expected = V2SupportAuthorityService._qualification_frozen_sources(
                qualification
            )
        except Exception as exc:
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_QUALIFICATION_EVIDENCE_DRIFT"
            ) from exc
        if factual_sources != expected:
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_QUALIFICATION_EVIDENCE_DRIFT"
            )


def _validate_frozen_script_receipt(
    envelope: V2FrozenSupportEnvelope,
) -> None:
    script = envelope.approved_script
    text = script.approved_script_text.strip()
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", text)
        if sentence.strip()
    ]
    normalized = [
        re.sub(r"\s+", " ", sentence).strip().casefold() for sentence in sentences
    ]
    repeated_ratio = (
        round((len(normalized) - len(set(normalized))) / len(normalized), 6)
        if normalized
        else 1.0
    )
    gate_payload = {
        "schema_version": "vcos.script-authority-gate.v1",
        "status": "PASS",
        "script_hash": semantic_hash({"approved_script_text": text}),
        "word_count": len(words),
        "estimated_duration_ms": round(len(words) / 150 * 60_000),
        "repeated_sentence_ratio": repeated_ratio,
        "sections": [section.model_dump(mode="json") for section in script.sections],
        "claim_binding_hashes": [
            binding.binding_hash for binding in envelope.claim_source_bindings
        ],
        "duration_contract_hash": (envelope.duration_contract.duration_contract_hash),
    }
    if (
        script.script_hash != gate_payload["script_hash"]
        or script.word_count != gate_payload["word_count"]
        or script.estimated_duration_ms != gate_payload["estimated_duration_ms"]
        or script.repeated_sentence_ratio != gate_payload["repeated_sentence_ratio"]
        or script.gate_receipt_hash != semantic_hash(gate_payload)
    ):
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_SCRIPT_RECEIPT_MISMATCH"
        )


def _validate_memory_guidance_authority(
    *,
    context: WorkflowStageContext,
    project: VideoProject,
    effective: EffectiveChannelRuntimeContextSnapshot,
    envelope: V2FrozenSupportEnvelope,
) -> None:
    authority = envelope.memory_guidance_authority
    if authority is None:
        return
    manifest = context.session.get(
        MemoryInfluenceManifest, authority.memory_influence_manifest_id
    )
    application = context.session.get(
        AgentMemoryApplicationRecord,
        authority.agent_memory_application_record_id,
    )
    if (
        manifest is None
        or application is None
        or manifest.video_project_id != project.id
        or manifest.effective_context_snapshot_id != effective.id
        or manifest.retrieval_manifest_id != authority.retrieval_manifest_id
        or manifest.digest_hash != authority.digest_hash
        or manifest.scope_status != authority.scope_status
        or application.video_project_id != project.id
        or application.memory_influence_manifest_id != manifest.id
        or application.memory_digest_hash != authority.digest_hash
        or application.agent_key != "ScriptWriterAgent"
    ):
        raise _support_envelope_integrity_error("V2_MEMORY_GUIDANCE_AUTHORITY_DRIFT")


def _validate_frozen_gate_receipts(
    envelope: V2FrozenSupportEnvelope,
) -> None:
    expected = {
        "exact_authority_lineage": semantic_hash(
            {
                "project": envelope.project_ref.model_dump(mode="json"),
                "admission": envelope.admission_ref.model_dump(mode="json"),
                "profile": envelope.profile_ref.model_dump(mode="json"),
                "policy": envelope.compiled_policy_ref.model_dump(mode="json"),
                "effective": envelope.effective_context_ref.model_dump(mode="json"),
            }
        ),
        "frozen_source_preflight": semantic_hash(
            [source.model_dump(mode="json") for source in envelope.frozen_sources]
        ),
        "approved_script_integrity": (envelope.approved_script.gate_receipt_hash),
        "claim_source_bindings": semantic_hash(
            [binding.binding_hash for binding in envelope.claim_source_bindings]
        ),
        "local_generated_card_rights": (
            envelope.local_generated_card_rights.content_hash
        ),
        "native_provider_capability": semantic_hash(
            [route.route_hash for route in envelope.native_routes]
        ),
        "zero_cost_budget": envelope.zero_cost_budget.content_hash,
        "verified_destination": envelope.verified_destination.content_hash,
    }
    if envelope.memory_guidance_authority is not None:
        expected["memory_guidance_digest"] = (
            envelope.memory_guidance_authority.content_hash
        )
    qualification_gate = next(
        (
            item
            for item in envelope.gate_receipts
            if isinstance(item, dict) and item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    if isinstance(qualification_gate, dict):
        qualification_receipt_hash = qualification_gate.get("receipt_hash")
        memory_hash = qualification_gate.get("memory_digest_hash")
        if (
            not isinstance(qualification_receipt_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", qualification_receipt_hash)
            or not isinstance(memory_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", memory_hash)
        ):
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_QUALIFICATION_MEMORY_INVALID"
            )
        expected["script_qualification"] = qualification_receipt_hash
        expected["qualification_memory_digest"] = memory_hash
    actual: dict[str, str] = {}
    for receipt in envelope.gate_receipts:
        if (
            not isinstance(receipt, dict)
            or not isinstance(receipt.get("gate_key"), str)
            or not isinstance(receipt.get("receipt_hash"), str)
            or receipt["gate_key"] in actual
            or (
                receipt.get("status") != "PASS"
                and not (
                    receipt.get("gate_key") == "qualification_memory_digest"
                    and receipt.get("status") == "PASS_EMPTY"
                )
            )
        ):
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_GATE_RECEIPT_INVALID"
            )
        actual[receipt["gate_key"]] = receipt["receipt_hash"]
    if actual != expected:
        raise _support_envelope_integrity_error(
            "V2_FROZEN_SUPPORT_GATE_RECEIPT_MISMATCH"
        )


def _support_envelope_integrity_error(code: str) -> WorkflowStageError:
    return WorkflowStageError(
        classification=WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
        error_code=code,
        summary=(
            "The immutable v2 frozen-support envelope no longer matches its "
            "exact project authorities."
        ),
        incident_type="INTEGRITY_MISMATCH",
        retry_eligible=False,
        operator_visible_blocker=(
            "Support envelope không còn khớp authority đã đóng băng; "
            "không được tự fallback hoặc tự chứng nhận lại."
        ),
    )


def _decimal_text(value: Any) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationFailureError("V2_PROVIDER_COST_DECIMAL_INVALID") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValidationFailureError("V2_PROVIDER_COST_DECIMAL_INVALID")
    return format(parsed, "f")


def _real_provider_policy(authority: _SupportAuthority) -> ChannelScopedPolicy:
    try:
        scoped = ChannelScopedPolicy.model_validate(
            (authority.policy.compiled_payload or {}).get("channel_scoped_policy")
        )
    except Exception as exc:
        raise ValidationFailureError("V2_REAL_PROVIDER_POLICY_INVALID") from exc
    if (
        scoped.media_production_profile.final_narration_authority != "elevenlabs"
        or scoped.voice_policy.provider != "elevenlabs"
        or not scoped.provider_usage_policy.elevenlabs.enabled
        or not scoped.provider_usage_policy.elevenlabs.final_narration_authority
        or scoped.provider_usage_policy.elevenlabs.initial_tts_attempts != 1
        or not scoped.provider_usage_policy.native_ffmpeg_final_render_authority
        or not scoped.provider_usage_policy.drive_archive_required_before_cleanup
        or not scoped.publish_policy.drive_archive_required
        or scoped.budget_policy.max_estimated_cost_per_video <= 0
    ):
        raise ValidationFailureError("V2_REAL_PROVIDER_POLICY_NOT_AUTHORIZED")
    return scoped


def _ensure_support_versions(
    context: WorkflowStageContext,
    authority: _SupportAuthority,
) -> dict[str, ArtifactVersion]:
    payloads = _support_payloads(context, authority)
    versions: dict[str, ArtifactVersion] = {}
    service = ArtifactService(context.session)
    for artifact_type in _SUPPORT_TYPES:
        expected = payloads[artifact_type]
        artifact = context.session.scalars(
            select(Artifact)
            .where(
                Artifact.video_project_id == authority.project.id,
                Artifact.artifact_type == artifact_type,
            )
            .order_by(Artifact.created_at.asc())
        ).first()
        current = (
            context.session.get(ArtifactVersion, artifact.current_version_id)
            if artifact is not None and artifact.current_version_id is not None
            else None
        )
        if current is not None:
            _validate_support_version(
                authority=authority,
                artifact=artifact,
                version=current,
                artifact_type=artifact_type,
                expected=expected,
            )
            versions[artifact_type] = current
            continue
        if artifact is None:
            artifact = service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=authority.project.id,
                    artifact_type=artifact_type,
                    created_by_user_id=authority.project.created_by_user_id,
                ),
                correlation_id=(f"v2-support-{context.command_id}-{artifact_type}"),
                trusted_authority_write=True,
            )
        version = service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=expected,
                status="approved",
                created_by_user_id=authority.project.created_by_user_id,
                evidence_refs=_support_evidence_refs(authority),
                context_refs=[_lineage(authority, context)],
                packaging_metadata={
                    "producer_version": V2_SUPPORT_COMPILER_VERSION,
                    "command_id": context.command_id,
                    **(
                        {
                            "support_envelope_artifact_version_id": str(
                                authority.support_envelope_version.id
                            ),
                            "support_envelope_hash": (
                                authority.support_envelope_version.content_hash
                            ),
                        }
                        if authority.support_envelope_version is not None
                        else {}
                    ),
                },
            ),
            correlation_id=(f"v2-support-{context.command_id}-{artifact_type}-version"),
            trusted_authority_write=True,
        )
        artifact.status = "approved"
        context.session.flush()
        versions[artifact_type] = version
    return versions


def _validate_support_version(
    *,
    authority: _SupportAuthority,
    artifact: Artifact | None,
    version: ArtifactVersion,
    artifact_type: str,
    expected: dict[str, Any],
) -> None:
    content = version.content
    lineage = content.get("lineage") if isinstance(content, dict) else None
    metadata = version.packaging_metadata or {}
    domain = metadata.get("_vcos_domain_authority")
    if (
        artifact is None
        or artifact.video_project_id != authority.project.id
        or artifact.artifact_type != artifact_type
        or artifact.status != "approved"
        or version.status != "approved"
        or semantic_hash(content) != version.content_hash
        or semantic_hash(expected) != version.content_hash
        or not isinstance(lineage, dict)
        or lineage.get("schema_version") != "vcos.support-lineage.v2"
        or lineage.get("producer_version") != V2_SUPPORT_COMPILER_VERSION
        or str(lineage.get("video_project_id")) != str(authority.project.id)
        or str(lineage.get("project_admission_decision_id"))
        != str(authority.admission.id)
        or lineage.get("project_admission_decision_hash")
        != authority.admission.decision_hash
        or lineage.get("channel_profile_hash") != authority.profile.profile_input_hash
        or lineage.get("compiled_policy_snapshot_hash") != authority.policy.content_hash
        or lineage.get("duration_contract_hash")
        != authority.duration.duration_contract_hash
        or (
            authority.support_envelope_version is not None
            and (
                str(lineage.get("frozen_support_envelope_artifact_version_id"))
                != str(authority.support_envelope_version.id)
                or lineage.get("frozen_support_envelope_hash")
                != authority.support_envelope_version.content_hash
                or lineage.get("frozen_support_envelope_input_fingerprint")
                != authority.support_envelope.input_fingerprint
                or lineage.get("execution_mode")
                != authority.support_envelope.execution_mode
            )
        )
        or not isinstance(domain, dict)
        or domain.get("writer") != "server_domain_service"
        or domain.get("artifact_type") != artifact_type
        or domain.get("content_hash") != version.content_hash
    ):
        raise ValidationFailureError(f"V2_SUPPORT_AUTHORITY_DRIFT:{artifact_type}")


def _support_payloads(
    context: WorkflowStageContext,
    authority: _SupportAuthority,
) -> dict[str, dict[str, Any]]:
    lineage = _lineage(authority, context)
    script = _script_payload(authority, lineage)
    sections = script["sections"]
    adapter_operations: dict[str, dict[str, Any]] = {}
    operation_authorizations: dict[str, dict[str, Any]] = {}
    envelope = authority.support_envelope
    execution_mode = (
        envelope.execution_mode if envelope is not None else "QUALIFICATION_LOCAL"
    )
    real_production = execution_mode == "REAL_LONG_FORM_PRODUCTION"
    scoped_policy = _real_provider_policy(authority) if real_production else None
    voice_bundle = None
    if real_production:
        from app.services.voice_authority import (
            VoiceAuthorityService,
            voice_authority_required,
        )

        if voice_authority_required(authority.policy):
            script_ref = (
                f"v2-approved-script:{authority.support_envelope.approved_script.script_hash}"
                if authority.support_envelope is not None
                else f"v2-approved-script:{semantic_hash({'text': authority.approved_script})}"
            )
            script_hash = (
                authority.support_envelope.approved_script.script_hash
                if authority.support_envelope is not None
                else semantic_hash({"approved_script_text": authority.approved_script.strip()})
            )
            voice_bundle = VoiceAuthorityService(context.session).ensure_project_bundle(
                video_project_id=authority.project.id,
                qualified_script_ref=script_ref,
                qualified_script_hash=script_hash,
                canonical_narration=script["narration_text"],
                script_sections=script["sections"],
                created_by_user_id=authority.project.created_by_user_id,
            )
            script["voice_authority"] = {
                "approved_voice_pool_id": str(voice_bundle.pool.id),
                "approved_voice_pool_hash": voice_bundle.pool.content_hash,
                "voice_casting_decision_id": str(voice_bundle.casting.id),
                "voice_casting_decision_hash": voice_bundle.casting.content_hash,
                "narration_voice_snapshot_id": str(voice_bundle.snapshot.id),
                "narration_voice_snapshot_hash": voice_bundle.snapshot.content_hash,
                "narration_performance_plan_id": str(voice_bundle.performance.id),
                "narration_performance_plan_hash": voice_bundle.performance.content_hash,
                "tts_performance_projection_id": str(voice_bundle.projection.id),
                "tts_performance_projection_hash": voice_bundle.projection.content_hash,
            }
    support_envelope_hash = (
        authority.support_envelope_version.content_hash
        if authority.support_envelope_version is not None
        else ""
    )
    if real_production and not support_envelope_hash:
        raise ValidationFailureError("V2_REAL_PROVIDER_SUPPORT_ENVELOPE_REQUIRED")
    stage_modes = (
        {
            "MEDIA": "ELEVENLABS_FINAL_NARRATION",
            "VISUAL": "AI_ONLY_PRIMARY_VISUAL_GENERATION",
            "RENDER": "NATIVE_FFMPEG_LOCAL",
            "QC": "AUTOMATED_NATIVE_QC",
        }
        if real_production
        else {
            "MEDIA": "PACKAGE_NATIVE_TIMELINE",
            "RENDER": "NATIVE_FFMPEG_LOCAL",
            "QC": "AUTOMATED_NATIVE_QC",
        }
    )
    audio_strategy = (
        "ELEVENLABS_FINAL_NARRATION" if real_production else "LOCAL_OS_TTS_SCRIPT_BOUND"
    )
    route_by_stage = (
        {route.stage: route for route in authority.support_envelope.native_routes}
        if authority.support_envelope is not None
        else {}
    )
    visual_rights = (
        authority.support_envelope.local_generated_card_rights
        if authority.support_envelope is not None
        else None
    )
    visual_source_policy = (
        "AI_ONLY_GENERATED_ASSETS_REQUIRED"
        if visual_rights is not None
        and visual_rights.visual_source_mode == "AI_ONLY_GENERATED_ASSETS"
        else "POLICY_SELECTED_ASSET_REQUEST_REQUIRED"
        if visual_rights is not None
        and visual_rights.visual_source_mode == "POLICY_SELECTED_ASSET_REQUESTS"
        else "LOCAL_GENERATED_CARDS_ONLY"
        if authority.support_envelope is not None
        else "LOCAL_OR_RIGHTS_VERIFIED_ONLY"
    )
    if (
        visual_rights is not None
        and visual_rights.visual_source_mode == "NATIVE_BACKBONE_POLICY_ONLY"
    ):
        visual_source_policy = "NATIVE_BACKBONE_POLICY_ONLY"
    visual_provider_plan = (
        list(visual_rights.allowed_provider_keys)
        if visual_rights is not None
        and visual_rights.visual_source_mode == "AI_ONLY_GENERATED_ASSETS"
        else list(visual_rights.allowed_provider_keys)
        if visual_rights is not None
        and visual_rights.visual_source_mode == "POLICY_SELECTED_ASSET_REQUESTS"
        else ["NATIVE_FFMPEG"]
    )
    for stage in (*stage_modes, "ARCHIVE"):
        route = route_by_stage.get(stage)
        adapter_key = (
            route.adapter_key
            if route is not None
            else (
                "v2-google-drive-archive" if stage == "ARCHIVE" else "v2-local-native"
            )
        )
        # Normal production authority seals the Drive resolver.  A locally
        # sealed route remains useful only for the native-effects qualification
        # harness, where it is explicitly supplied in the frozen envelope; do
        # not silently turn an unconfigured Drive archive into a local copy.
        mode = (
            "GOOGLE_DRIVE_REMOTE_ARCHIVE"
            if real_production and stage == "ARCHIVE"
            else (
                "GOOGLE_DRIVE_VERIFIED_ARCHIVE"
                if stage == "ARCHIVE" and adapter_key == "v2-google-drive-archive"
                else (
                    "LOCAL_VERIFIED_ARCHIVE"
                    if stage == "ARCHIVE"
                    else stage_modes[stage]
                )
            )
        )
        parameters: dict[str, Any] = {
            "mode": mode,
            "audio_strategy": audio_strategy,
            "execution_mode": execution_mode,
        }
        if adapter_key == "v2-google-drive-archive":
            parameters["archive_resolution"] = "PERSISTED_VERIFIED_CLOUD_MEDIA"
        operation_id = (
            route.operation_id
            if route is not None
            else f"v2:{authority.project.id}:{stage.lower()}"
        )
        paid_provider_call = bool(route.paid_provider_call) if route else False
        max_cost_usd = _decimal_text(route.max_cost_usd) if route is not None else "0"
        if real_production and stage == "MEDIA":
            assert scoped_policy is not None and envelope is not None
            if voice_bundle is not None:
                voice_id = voice_bundle.snapshot.voice_id
                model_id = voice_bundle.snapshot.model_id
                voice_settings = dict(voice_bundle.snapshot.baseline_voice_settings)
                voice_authority = {
                    "authority_mode": "FROZEN_PROJECT_VOICE_AUTHORITY",
                    "approved_voice_pool_id": str(voice_bundle.pool.id),
                    "approved_voice_pool_hash": voice_bundle.pool.content_hash,
                    "voice_casting_decision_id": str(voice_bundle.casting.id),
                    "voice_casting_decision_hash": voice_bundle.casting.content_hash,
                    "narration_voice_snapshot_id": str(voice_bundle.snapshot.id),
                    "narration_voice_snapshot_hash": voice_bundle.snapshot.content_hash,
                    "narration_performance_plan_id": str(voice_bundle.performance.id),
                    "narration_performance_plan_hash": voice_bundle.performance.content_hash,
                    "tts_performance_projection_id": str(voice_bundle.projection.id),
                    "tts_performance_projection_hash": voice_bundle.projection.content_hash,
                    "qualified_script_hash": script_hash,
                    "voice_id": voice_bundle.snapshot.voice_id,
                    "model_id": voice_bundle.snapshot.model_id,
                    "tts_execution_strategy": voice_bundle.projection.execution_strategy,
                    "tts_segment_count": len(voice_bundle.projection.segments),
                    "capability_profile_version": voice_bundle.projection.capability_profile_version,
                }
            else:
                voice_id = scoped_policy.voice_policy.voice_id
                model_id = scoped_policy.voice_policy.model_id
                voice_settings = scoped_policy.voice_policy.settings.model_dump(
                    mode="json"
                )
                voice_authority = {
                    "authority_mode": "LEGACY_CHANNEL_POLICY_COMPATIBILITY",
                }
            parameters["provider_execution"] = {
                "provider": "elevenlabs",
                "voice_id": voice_id,
                "model_id": model_id,
                "voice_settings": voice_settings,
                **voice_authority,
                "credential_ref": "env://ELEVENLABS_API_KEY",
                "attempt_limit": scoped_policy.provider_usage_policy.elevenlabs.initial_tts_attempts,
                "attempt_limit_per_segment": 1,
                "idempotency_key": f"{operation_id}:elevenlabs-final-narration",
                "estimated_cost_usd": max_cost_usd,
                "budget_reservation_ref": envelope.zero_cost_budget.reservation_ref,
                "package_support_envelope_hash": support_envelope_hash,
            }
        if real_production and stage == "VISUAL":
            assert envelope is not None
            parameters["provider_execution"] = {
                "provider": "ai_visual_scene_effects",
                "credential_ref": "env://GEMINI_API_KEY",
                "routes": ["AI_IMAGE", "AI_VIDEO"],
                "active_primary_visual_routes": ["AI_IMAGE", "AI_VIDEO"],
                "image_provider": "google_gemini_image",
                "video_provider": "google_veo",
                "attempt_limit": 1,
                "attempt_limit_per_asset_slot": 1,
                "automatic_provider_retry": False,
                "fallback_allowed": False,
                "native_fallback_allowed": False,
                "stock_fallback_allowed": False,
                "screenshot_fallback_allowed": False,
                "production_visual_policy_ref": (envelope.production_visual_policy_ref),
                "production_visual_policy_hash": (
                    envelope.production_visual_policy_hash
                ),
                "idempotency_key": f"{operation_id}:ai-visual-asset-set",
                "estimated_cost_usd": max_cost_usd,
                "budget_reservation_ref": envelope.zero_cost_budget.reservation_ref,
                "package_support_envelope_hash": support_envelope_hash,
            }
        if real_production and stage == "ARCHIVE":
            assert envelope is not None
            parameters["provider_execution"] = {
                "provider": "google_drive",
                "credential_ref": "oauth://google-drive/channel-connected",
                "attempt_limit": 1,
                "idempotency_key": f"{operation_id}:google-drive-archive",
                "remote_object_required": True,
                "checksum_readback_required": True,
                "budget_reservation_ref": envelope.zero_cost_budget.reservation_ref,
                "package_support_envelope_hash": support_envelope_hash,
            }
        adapter_operations[stage] = {
            "schema_version": "vcos.provider-adapter-operation.v1",
            "execution_authorized": True,
            "production_eligible": True,
            "fixture_only": False,
            "invokes_mr1": False,
            "automatic_publish": False,
            "stage": stage,
            "production_lane": authority.project.production_lane,
            "execution_mode": execution_mode,
            "paid_provider_call": paid_provider_call,
            "operation_id": operation_id,
            "adapter_key": adapter_key,
            "max_cost_usd": max_cost_usd,
            **(
                {
                    "provider_role_id": str(route.provider_role_id),
                    "provider_key": route.provider_key,
                    "provider_type": route.provider_type,
                    "routing_policy_ref": route.routing_policy_ref,
                    "routing_policy_hash": route.routing_policy_hash,
                    "capability_entry_id": (
                        str(route.capability_entry_id)
                        if route.capability_entry_id is not None
                        else None
                    ),
                    "job_type": route.job_type,
                    "route_hash": route.route_hash,
                }
                if route is not None
                else {}
            ),
            "parameters": parameters,
        }
        operation_authorizations[operation_id] = {
            "authorized": True,
            "operation_id": operation_id,
            "adapter_key": adapter_key,
            "stage": stage,
            "paid_provider_call": paid_provider_call,
            "max_cost_usd": max_cost_usd,
            "execution_mode": execution_mode,
            **({"route_hash": route.route_hash} if route is not None else {}),
        }
    target_surface = "LONG_FORM"
    destination = authority.destination
    review_only_destination = bool(
        envelope is not None
        and isinstance(
            envelope.verified_destination,
            V2FinalReviewOnlyDestinationAuthority,
        )
    )
    gate_receipts = {
        str(receipt["gate_key"]): receipt
        for receipt in (envelope.gate_receipts if envelope is not None else [])
    }
    return {
        "research_pack": {
            "schema_version": "vcos.research-pack.v2",
            "result": "PASS",
            "evidence_complete": True,
            "evidence_refs": authority.source_refs,
            "planning_source": authority.source_payload,
            **(
                {
                    "claim_source_bindings": [
                        binding.model_dump(mode="json")
                        for binding in envelope.claim_source_bindings
                    ],
                    "gate_receipts": {
                        key: gate_receipts[key]
                        for key in (
                            "frozen_source_preflight",
                            "claim_source_bindings",
                            *(
                                ("memory_guidance_digest",)
                                if envelope.memory_guidance_authority is not None
                                else ()
                            ),
                        )
                    },
                    **(
                        {
                            "memory_guidance_authority": (
                                envelope.memory_guidance_authority.model_dump(
                                    mode="json"
                                )
                            )
                        }
                        if envelope.memory_guidance_authority is not None
                        else {}
                    ),
                }
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
        "source_pack": {
            "schema_version": "vcos.source-pack.v2",
            "result": "PASS",
            "source_count": len(authority.source_refs),
            "sources": authority.source_refs,
            "lineage": lineage,
        },
        "niche_alignment_dossier": {
            "schema_version": "vcos.niche-alignment.v2",
            "result": "PASS",
            "basis": (
                "APPROVED_FROZEN_SUPPORT_ENVELOPE"
                if envelope is not None
                else "FROZEN_PROFILE_POLICY_AND_STRICT_PREFLIGHT"
            ),
            **(
                {"gate_receipt": gate_receipts["frozen_source_preflight"]}
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
        "market_alignment_dossier": {
            "schema_version": "vcos.market-alignment.v2",
            "result": "PASS",
            "target_market": destination.get("target_market"),
            "primary_locale": destination.get("primary_locale"),
            "basis": (
                "APPROVED_FROZEN_SUPPORT_ENVELOPE"
                if envelope is not None
                else "VERIFIED_DESTINATION_AND_STRICT_PREFLIGHT"
            ),
            **(
                {
                    "gate_receipts": {
                        "source": gate_receipts["frozen_source_preflight"],
                        "destination": gate_receipts["verified_destination"],
                    }
                }
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
        "script": script,
        "visual_plan": {
            "schema_version": "vcos.visual-plan.v2",
            "result": "PASS",
            "scenes": [
                {
                    "scene_id": f"scene-{index + 1:03d}",
                    "section_id": section["section_id"],
                    "visual_intent": ("Illustrate only the approved section evidence."),
                    "source_policy": visual_source_policy,
                    "asset_request_state": (
                        "PENDING_POST_READINESS"
                        if visual_rights is not None
                        and visual_rights.visual_source_mode
                        == "POLICY_SELECTED_ASSET_REQUESTS"
                        else "NATIVE_COMPOSITION_AUTHORIZED"
                    ),
                    "provider_fallback_allowed": False,
                }
                for index, section in enumerate(sections)
            ],
            "aspect_ratio": "16:9",
            **(
                {
                    "memory_guidance_ref": {
                        "memory_influence_manifest_id": str(
                            envelope.memory_guidance_authority.memory_influence_manifest_id
                        ),
                        "digest_hash": (envelope.memory_guidance_authority.digest_hash),
                        "non_factual_guidance_only": True,
                        "no_raw_analytics": True,
                        "no_raw_memory": True,
                    }
                }
                if envelope is not None
                and envelope.memory_guidance_authority is not None
                else {}
            ),
            **(
                {
                    "asset_request_policy": {
                        "authority_hash": visual_rights.content_hash,
                        "allowed_provider_keys": (visual_rights.allowed_provider_keys),
                        "asset_request_compiler_required": True,
                        "execution_phase": "POST_READINESS_ONLY",
                        "provider_fallback_allowed": False,
                        "one_source_decision_per_scene": True,
                    }
                }
                if visual_rights is not None
                and visual_rights.visual_source_mode == "POLICY_SELECTED_ASSET_REQUESTS"
                else {}
            ),
            "lineage": lineage,
        },
        "thumbnail_brief": {
            "schema_version": "vcos.thumbnail-brief.v2",
            "result": "PASS",
            "headline": authority.project.title,
            "visual_claim_policy": "NO_UNSUPPORTED_CLAIMS",
            "lineage": lineage,
        },
        "publishing_metadata_package": {
            "schema_version": "vcos.publish-metadata.v2",
            "result": "PASS",
            "title": authority.project.title,
            "description": authority.project.description or "",
            "privacy": str(destination.get("default_visibility") or "PRIVATE").upper(),
            "manual_publish_required": True,
            "lineage": lineage,
        },
        "rights_disclosure_completeness_report": {
            "schema_version": "vcos.rights-disclosure.v2",
            "result": "PASS",
            "text_source": (
                "APPROVED_FROZEN_SUPPORT_ENVELOPE"
                if envelope is not None
                else "APPROVED_PLANNING_AUTHORITY"
            ),
            "asset_policy": (visual_source_policy),
            "audio_strategy": audio_strategy,
            "unresolved_exceptions": [],
            "disclosures": [],
            **(
                {
                    "rights_authority": (
                        envelope.local_generated_card_rights.model_dump(mode="json")
                    ),
                    "gate_receipt": gate_receipts["local_generated_card_rights"],
                }
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
        "provider_execution_plan": {
            "schema_version": "vcos.post-readiness-provider-plan.v2",
            "result": "PASS",
            "execution_authorized": True,
            "retry_authorized": not real_production,
            "visual_resume_authorized": real_production,
            "scene_effect_max_attempts": 1,
            "provider_retry_authorized": False,
            "max_attempts": 1 if real_production else 5,
            "retry_cost_usd": "0",
            "production_lane": authority.project.production_lane,
            "execution_mode": execution_mode,
            "fixture_only": False,
            "invokes_mr1": False,
            "automatic_publish": False,
            "paid_provider_calls": real_production,
            "final_tts_provider": (
                "ELEVENLABS" if real_production else "QUALIFICATION_LOCAL_OS_TTS"
            ),
            "archive_provider": (
                "GOOGLE_DRIVE" if real_production else "QUALIFICATION_ONLY"
            ),
            "visual_provider_plan": visual_provider_plan,
            "production_visual_policy_ref": (
                envelope.production_visual_policy_ref if envelope is not None else None
            ),
            "production_visual_policy_hash": (
                envelope.production_visual_policy_hash if envelope is not None else None
            ),
            "active_primary_visual_routes": (
                envelope.active_primary_visual_routes if envelope is not None else []
            ),
            "visual_asset_acquisition": (
                {
                    "authority": visual_rights.model_dump(mode="json"),
                    "request_compiler": "AssetRequestCompiler",
                    "execution_phase": "POST_READINESS_ONLY",
                    "provider_fallback_allowed": False,
                }
                if visual_rights is not None
                and visual_rights.visual_source_mode == "POLICY_SELECTED_ASSET_REQUESTS"
                else None
            ),
            "adapter_operations": adapter_operations,
            **(
                {
                    "native_route_receipts": [
                        route.model_dump(mode="json")
                        for route in envelope.native_routes
                    ],
                    "gate_receipt": gate_receipts["native_provider_capability"],
                }
                if envelope is not None
                else {}
            ),
            "final_review": {
                "target_surface": target_surface,
                "target_market_lineage": {
                    "target_market": destination.get("target_market"),
                    "primary_market": destination.get("primary_market"),
                    "primary_locale": destination.get("primary_locale"),
                    "destination_mode": destination.get("destination_mode"),
                    "destination_status": destination.get("destination_status"),
                    "destination_handle": destination.get("destination_handle"),
                    "destination_binding_ref": destination.get("active_binding_ref"),
                    "destination_binding_hash": (
                        envelope.verified_destination.content_hash
                        if envelope is not None
                        else semantic_hash(destination)
                    ),
                    "destination_model_hash": (
                        envelope.verified_destination.destination_hash
                        if envelope is not None
                        else destination.get("content_hash")
                    ),
                    "destination_authority_hash": (
                        envelope.verified_destination.content_hash
                        if envelope is not None
                        else semantic_hash(destination)
                    ),
                    "publish_execution_allowed": (
                        destination.get("publish_execution_allowed") is True
                    ),
                    "automatic_publish": False,
                    **(
                        {
                            "controlled_recovery_authority_id": destination.get(
                                "controlled_recovery_authority_id"
                            ),
                            "controlled_recovery_authority_hash": destination.get(
                                "controlled_recovery_authority_hash"
                            ),
                            "settlement_authority_id": destination.get(
                                "settlement_authority_id"
                            ),
                            "settlement_authority_hash": destination.get(
                                "settlement_authority_hash"
                            ),
                            "settlement_qualification_run_id": destination.get(
                                "settlement_qualification_run_id"
                            ),
                            "settlement_provenance_hash": destination.get(
                                "settlement_provenance_hash"
                            ),
                        }
                        if review_only_destination
                        else {}
                    ),
                },
                "publish_metadata_snapshot": {
                    "title": authority.project.title,
                    "description": authority.project.description or "",
                    "privacy_status": str(
                        destination.get("default_visibility") or "PRIVATE"
                    ).upper(),
                },
                "disclosure_snapshot": {
                    "manual_publish_required": True,
                    "audio_strategy": audio_strategy,
                    "disclosures": [],
                },
            },
            "lineage": lineage,
        },
        "cost_estimate_snapshot": {
            "schema_version": "vcos.operation-budget-authority.v1",
            "result": "PASS",
            "budget_authorized": True,
            "retry_authorized": not real_production,
            "visual_resume_authorized": real_production,
            "scene_effect_max_attempts": 1,
            "max_attempts": 1 if real_production else 5,
            "retry_cost_usd": "0",
            "remaining_budget_usd": (
                _decimal_text(envelope.zero_cost_budget.authorized_cost_usd)
                if envelope is not None
                else "0"
            ),
            "execution_mode": execution_mode,
            "operation_authorizations": operation_authorizations,
            **(
                {
                    "zero_cost_budget_authority": (
                        envelope.zero_cost_budget.model_dump(mode="json")
                    ),
                    **(
                        {
                            "real_provider_budget_authority": (
                                envelope.zero_cost_budget.model_dump(mode="json")
                            )
                        }
                        if real_production
                        else {}
                    ),
                    "gate_receipt": gate_receipts["zero_cost_budget"],
                }
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
        "destination_binding": {
            "schema_version": "vcos.destination-binding-artifact.v2",
            "result": (
                "PASS_FOR_FINAL_REVIEW_ONLY" if review_only_destination else "PASS"
            ),
            "destination_mode": destination.get("destination_mode"),
            "destination_status": destination.get("destination_status"),
            "destination_handle": destination.get("destination_handle"),
            "destination_binding_hash": destination.get("destination_binding_hash"),
            "destination_model_hash": destination.get("destination_model_hash"),
            "destination_authority_hash": destination.get("destination_authority_hash"),
            "publish_execution_allowed": (
                destination.get("publish_execution_allowed") is True
            ),
            "automatic_publish": False,
            **(
                {
                    "controlled_recovery_authority_id": destination.get(
                        "controlled_recovery_authority_id"
                    ),
                    "controlled_recovery_authority_hash": destination.get(
                        "controlled_recovery_authority_hash"
                    ),
                    "settlement_authority_id": destination.get(
                        "settlement_authority_id"
                    ),
                    "settlement_authority_hash": destination.get(
                        "settlement_authority_hash"
                    ),
                    "settlement_qualification_run_id": destination.get(
                        "settlement_qualification_run_id"
                    ),
                    "settlement_provenance_hash": destination.get(
                        "settlement_provenance_hash"
                    ),
                }
                if review_only_destination
                else {}
            ),
            "destination_binding": destination,
            **(
                {
                    **(
                        {
                            "final_review_only_destination_authority": (
                                envelope.verified_destination.model_dump(mode="json")
                            )
                        }
                        if review_only_destination
                        else {
                            "verified_destination_authority": (
                                envelope.verified_destination.model_dump(mode="json")
                            )
                        }
                    ),
                    "gate_receipt": gate_receipts["verified_destination"],
                }
                if envelope is not None
                else {}
            ),
            "lineage": lineage,
        },
    }


def _script_payload(
    authority: _SupportAuthority,
    lineage: dict[str, Any],
) -> dict[str, Any]:
    sentences = _sentences(authority.approved_script)
    if len(sentences) < 3:
        raise ValidationFailureError("V2_SUPPORT_APPROVED_SCRIPT_SECTIONS_REQUIRED")
    if authority.support_envelope is not None:
        sections = [
            {
                "section_id": section.section_id,
                "heading": section.heading,
                "sentences": [
                    {"text": sentence} for sentence in _sentences(section.narration)
                ],
            }
            for section in authority.support_envelope.approved_script.sections
        ]
    else:
        section_count = min(6, max(3, len(sentences)))
        groups = [sentences[index::section_count] for index in range(section_count)]
        sections = [
            {
                "section_id": f"section-{index + 1:03d}",
                "heading": f"Approved section {index + 1}",
                "sentences": [{"text": sentence} for sentence in group],
            }
            for index, group in enumerate(groups)
            if group
        ]
    normalized = [
        re.sub(r"\s+", " ", re.sub(r"[^\w ]", "", sentence.lower())).strip()
        for sentence in sentences
    ]
    repeated = len(normalized) - len(set(normalized))
    repeated_ratio = (
        authority.support_envelope.approved_script.repeated_sentence_ratio
        if authority.support_envelope is not None
        else round(repeated / len(normalized), 6)
    )
    if repeated_ratio > 0.2:
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="V2_SUPPORT_APPROVED_SCRIPT_PADDING_DETECTED",
            summary="The approved script repeats too much content.",
            incident_type="INTEGRITY_MISMATCH",
            retry_eligible=False,
        )
    word_count = len(
        re.findall(
            r"\b[\w'-]+\b",
            authority.approved_script,
            flags=re.UNICODE,
        )
    )
    estimated_duration_ms = (
        authority.support_envelope.approved_script.estimated_duration_ms
        if authority.support_envelope is not None
        else round(word_count / 150 * 60_000)
    )
    claims = (
        [
            _claim_binding_payload(binding)
            for binding in authority.support_envelope.claim_source_bindings
        ]
        if authority.support_envelope is not None
        else [
            {
                "claim_id": f"claim-{index + 1:03d}",
                "text": sentence,
                "evidence_ref": authority.source_refs[
                    index % len(authority.source_refs)
                ],
            }
            for index, sentence in enumerate(sentences[: max(3, len(sections))])
        ]
    )
    cross_modal = (
        authority.support_envelope.cross_modal_script_lineage
        if authority.support_envelope is not None
        else None
    )
    return {
        "schema_version": "vcos.approved-script.v2",
        "readiness_result": "PASS",
        "title": authority.project.title,
        "narration_text": authority.approved_script,
        "estimated_duration_ms": estimated_duration_ms,
        "speech_rate_wpm": 150,
        "supported_claims": claims,
        "sections": sections,
        "research_coverage_ratio": _qualification_projection_value(
            authority, "research_coverage_ratio", default=0.0
        ),
        "repeated_sentence_ratio": repeated_ratio,
        **(
            {
                "cross_modal_script_lineage": cross_modal.model_dump(mode="json"),
                "section_coverage_plan": cross_modal.section_coverage_plan.model_dump(
                    mode="json"
                ),
                "capability_projection_receipts": cross_modal.capability_projection_receipts,
                "single_source_sections": cross_modal.writer_sections,
            }
            if cross_modal is not None
            else {}
        ),
        **(
            {
                "approved_script_provenance": (
                    authority.support_envelope.approved_script.model_dump(mode="json")
                ),
                "claim_binding_hashes": [
                    binding.binding_hash
                    for binding in (authority.support_envelope.claim_source_bindings)
                ],
            }
            if authority.support_envelope is not None
            else {}
        ),
        "lineage": lineage,
    }


def _qualification_projection(
    context: WorkflowStageContext,
    authority: _SupportAuthority,
) -> dict[str, Any]:
    """Recompute readiness projections from a current immutable receipt."""

    envelope = authority.support_envelope
    if envelope is None or envelope.execution_mode != "REAL_LONG_FORM_PRODUCTION":
        return {
            "editorial_depth_sufficient": False,
            "anti_padding_pass": False,
            "script_gates_pass": False,
            "research_coverage_ratio": 0.0,
        }
    gate = next(
        (
            item
            for item in envelope.gate_receipts
            if item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    if (
        not isinstance(gate, dict)
        or gate.get("status") != "PASS"
        or not gate.get("script_qualification_run_id")
    ):
        raise ValidationFailureError("V2_REAL_PROVIDER_SCRIPT_QUALIFICATION_REQUIRED")
    from app.services.script_qualification import ScriptQualificationService

    receipt = ScriptQualificationService(context.session).require_pass(
        uuid.UUID(str(gate["script_qualification_run_id"]))
    )
    if receipt.content_hash != gate.get("receipt_hash"):
        raise ValidationFailureError("V2_SCRIPT_QUALIFICATION_RECEIPT_HASH_MISMATCH")
    content = receipt.content or {}
    gates = content.get("receipts") if isinstance(content, dict) else None
    if not isinstance(gates, dict):
        raise ValidationFailureError("V2_SCRIPT_QUALIFICATION_RECEIPT_INVALID")
    structural = gates.get("structural") or {}
    inventory = gates.get("inventory") or {}
    grounding = gates.get("grounding") or {}
    fulfillment = gates.get("fulfillment") or {}
    memory = gates.get("memory") or {}
    try:
        _script, _evidence, qualification_memory, _provenance = (
            ScriptQualificationService.qualification_output(receipt)
        )
    except ValidationFailureError as exc:
        raise ValidationFailureError("V2_SCRIPT_QUALIFICATION_RECEIPT_INVALID") from exc
    if not (
        structural.get("status") == "PASS"
        and inventory.get("status") == "PASS"
        and grounding.get("status") == "PASS"
        and fulfillment.get("status") == "PASS"
        and memory.get("status") in {"PASS", "PASS_EMPTY"}
        and structural.get("script_hash") == receipt.script_hash
        and grounding.get("assignment_hash") == receipt.script_assignment_hash
        and grounding.get("evidence_pack_hash") == receipt.factual_evidence_pack_hash
        and gate.get("memory_digest_hash") == qualification_memory.get("digest_hash")
    ):
        raise ValidationFailureError("V2_SCRIPT_QUALIFICATION_RECEIPT_NOT_CURRENT")
    return {
        "editorial_depth_sufficient": True,
        "anti_padding_pass": True,
        "script_gates_pass": True,
        "research_coverage_ratio": float(
            fulfillment.get("research_coverage_ratio", 0.0)
        ),
    }


def _qualification_projection_value(
    authority: _SupportAuthority,
    key: str,
    *,
    default: float,
) -> float:
    envelope = authority.support_envelope
    if envelope is None:
        return default
    gate = next(
        (
            item
            for item in envelope.gate_receipts
            if item.get("gate_key") == "script_qualification"
        ),
        None,
    )
    # The authoritative check occurs above when a package is created.  This
    # helper only prevents the support artifact itself from hardcoding 1.0.
    if not isinstance(gate, dict):
        return default
    try:
        value = float(gate.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if 0.0 <= value <= 1.0 else default


def _claim_binding_payload(
    binding: V2ClaimSourceBinding,
) -> dict[str, Any]:
    refs = [source.model_dump(mode="json") for source in binding.source_refs]
    return {
        "claim_id": binding.claim_id,
        "text": binding.claim_text,
        "evidence_ref": refs[0],
        "evidence_refs": refs,
        "source_excerpts": binding.source_excerpts,
        "evidence_span_refs": [
            span.model_dump(mode="json") for span in binding.evidence_span_refs
        ],
        "binding_hash": binding.binding_hash,
    }


def _sentences(text: str) -> list[str]:
    values = [
        value.strip() for value in re.split(r"(?<=[.!?])\s+|\n+", text) if value.strip()
    ]
    return values


def _lineage(
    authority: _SupportAuthority,
    context: WorkflowStageContext,
) -> dict[str, Any]:
    lineage = {
        "schema_version": "vcos.support-lineage.v2",
        "producer_version": V2_SUPPORT_COMPILER_VERSION,
        "command_id": command_id_for(
            context.run.id,
            ProductionWorkflowStage.RESEARCH,
        ),
        "video_project_id": str(authority.project.id),
        "project_admission_decision_id": str(authority.admission.id),
        "project_admission_decision_hash": str(authority.admission.decision_hash),
        "channel_profile_version_id": str(authority.profile.id),
        "channel_profile_hash": authority.profile.profile_input_hash,
        "compiled_policy_snapshot_id": str(authority.policy.id),
        "compiled_policy_snapshot_hash": authority.policy.content_hash,
        "duration_contract_hash": (authority.duration.duration_contract_hash),
        "production_lane": authority.project.production_lane,
        "content_mode": authority.project.content_mode,
        "planning_source_refs": authority.source_refs,
    }
    if (
        authority.support_envelope_artifact is not None
        and authority.support_envelope_version is not None
        and authority.support_envelope is not None
    ):
        lineage.update(
            {
                "frozen_support_envelope_artifact_id": str(
                    authority.support_envelope_artifact.id
                ),
                "frozen_support_envelope_artifact_version_id": str(
                    authority.support_envelope_version.id
                ),
                "frozen_support_envelope_hash": (
                    authority.support_envelope_version.content_hash
                ),
                "frozen_support_envelope_input_fingerprint": (
                    authority.support_envelope.input_fingerprint
                ),
                "execution_mode": authority.support_envelope.execution_mode,
            }
        )
    return lineage


def _support_manifest(
    authority: _SupportAuthority,
    versions: dict[str, ArtifactVersion],
) -> dict[str, Any]:
    manifest = {
        "schema_version": "vcos.support-manifest.v2",
        "video_project_id": str(authority.project.id),
        "producer_version": V2_SUPPORT_COMPILER_VERSION,
        "artifacts": {
            key: {
                "artifact_version_id": str(version.id),
                "content_hash": version.content_hash,
            }
            for key, version in sorted(versions.items())
        },
    }
    if authority.support_envelope_version is not None:
        manifest["frozen_support_envelope"] = {
            "artifact_version_id": str(authority.support_envelope_version.id),
            "content_hash": authority.support_envelope_version.content_hash,
        }
    return manifest


def _support_evidence_refs(
    authority: _SupportAuthority,
) -> list[dict[str, Any]]:
    refs = list(authority.source_refs)
    if (
        authority.support_envelope_artifact is not None
        and authority.support_envelope_version is not None
    ):
        refs.append(
            {
                "type": V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
                "id": str(authority.support_envelope_artifact.id),
                "artifact_version_id": str(authority.support_envelope_version.id),
                "version": authority.support_envelope_version.version_number,
                "content_hash": authority.support_envelope_version.content_hash,
            }
        )
    return refs


def _exact_ref(
    ref_type: str,
    version: ArtifactVersion,
) -> ExactContentRefV2:
    return ExactContentRefV2(
        type=ref_type,
        ref=f"artifact-version://{version.id}",
        artifact_version_id=version.id,
        version=version.version_number,
        content_hash=version.content_hash,
    )


def _project(context: WorkflowStageContext) -> VideoProject:
    project = (
        context.session.get(VideoProject, context.run.video_project_id)
        if context.run.video_project_id is not None
        else None
    )
    if (
        project is None
        or getattr(project, "schema_version", "v1") != "v2"
        or project.production_lane != context.run.production_lane
        or project.production_lane != ProductionLane.LONG_FORM.value
        or project.planning_source_type != "LONG_FORM_PLAN"
    ):
        raise ValidationFailureError("V2_PACKAGE_PROJECT_AUTHORITY_MISMATCH")
    return project


def _base_refs(context: WorkflowStageContext) -> WorkflowAuthorityRefs:
    project = _project(context)
    admission = (
        context.session.get(
            ProjectAdmissionDecision,
            project.project_admission_decision_id,
        )
        if project.project_admission_decision_id is not None
        else None
    )
    if (
        admission is None
        or admission.decision != "ADMIT"
        or admission.admitted_video_project_id != project.id
        or not admission.decision_hash
    ):
        raise ValidationFailureError("V2_PACKAGE_ADMISSION_AUTHORITY_MISMATCH")
    return WorkflowAuthorityRefs(
        video_project_id=project.id,
        project_admission_decision_id=admission.id,
        project_admission_decision_hash=admission.decision_hash,
    )


def _current_package(
    context: WorkflowStageContext,
) -> ArtifactVersion | None:
    project = _project(context)
    service = ProductionPackageService(context.session)
    artifact = service._package_artifact(project.id)
    version = service._current_version(artifact)
    if version is None:
        return None
    service.validate_for_readiness(version.id)
    return version


def _merge_refs(
    base: WorkflowAuthorityRefs,
    produced: WorkflowAuthorityRefs,
) -> WorkflowAuthorityRefs:
    values = base.model_dump(mode="python")
    for name, value in produced.model_dump(mode="python", exclude_none=True).items():
        existing = values.get(name)
        if existing is not None and existing != value:
            raise ValidationFailureError(f"V2_SUPPORT_PRODUCER_AUTHORITY_DRIFT:{name}")
        values[name] = value
    return WorkflowAuthorityRefs.model_validate(values)


def _producer_missing(code: str) -> WorkflowStageError:
    return WorkflowStageError(
        classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
        error_code=code,
        summary=(
            "The trusted in-repo v2 support/package producer dependency is "
            "not configured for this project."
        ),
        incident_type="CONFIG_ERROR",
        retry_eligible=False,
    )


__all__ = [
    "V2PackageReadinessGateway",
    "V2ProductionPackageInputBuilder",
    "V2TrustedSupportProducer",
    "build_v2_package_readiness_gateway",
]
