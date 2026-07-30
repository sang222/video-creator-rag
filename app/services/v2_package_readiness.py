"""Trusted in-process boundary for v2 support, package, and readiness stages."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.contracts.geo_market import DestinationBinding
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
from app.contracts.vcos_v2 import PlanningSourceType, ProductionLane
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models.channel import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
)
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m5 import (
    AudienceTargetPack,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    ProjectAdmissionDecision,
    SearchIntentMap,
)
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.production_package import (
    ProductionPackageService,
    ProductionReadinessService,
    semantic_hash,
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
    V2FrozenSupportEnvelope,
    _audience_source,
    _daily_idea_source,
    _editorial_slot_source,
    _effective_context_hash,
    _preflight_source,
    _project_authority_hash,
    _search_intent_source,
    _verified_destination as _verified_support_destination,
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

    Daily and Long-form projects must already carry the exact immutable
    envelope produced by ``V2SupportAuthorityService``.  This compiler never
    calls an LLM and never treats a caller-authored planning description or
    preflight blob as approved script authority.  Derived Shorts retain their
    deterministic, ready-parent lineage path.
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
        admission = authority.admission
        parent_lineage = _parent_derivative_lineage(context, authority)
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
                parent_derivative_lineage=parent_lineage,
                duration_contract=authority.duration,
                support_envelope_ref=(
                    _exact_ref(
                        "frozen_support_envelope",
                        authority.support_envelope_version,
                    )
                    if authority.support_envelope_version is not None
                    else None
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
                    editorial_depth_sufficient=True,
                    supported_claim_count=len(claims),
                    distinct_editorial_section_count=len(sections),
                    research_coverage_ratio=float(script["research_coverage_ratio"]),
                    shorter_format_permitted=(
                        authority.project.production_lane
                        != ProductionLane.LONG_FORM.value
                    ),
                    script_duration_ms=int(script["estimated_duration_ms"]),
                    anti_padding_pass=True,
                    padding_phrase_hits=0,
                    repeated_sentence_ratio=float(script["repeated_sentence_ratio"]),
                    script_gates_pass=True,
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
            supported_lanes=frozenset(ProductionLane),
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

    support_envelope_artifact: Artifact | None = None
    support_envelope_version: ArtifactVersion | None = None
    support_envelope: V2FrozenSupportEnvelope | None = None
    if project.production_lane == ProductionLane.LONG_DERIVED_SHORT.value:
        source_payload, source_refs = _planning_source(
            context,
            project,
            admission,
        )
        approved_script = _approved_script_text(
            source_payload=source_payload,
            duration=duration,
        )
        destination = _verified_destination(channel)
    else:
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
        destination = {
            **support_envelope.verified_destination.binding.model_dump(mode="json"),
            "active_binding_ref": (
                support_envelope.verified_destination.active_binding_ref
            ),
            "destination_authority_hash": (
                support_envelope.verified_destination.content_hash
            ),
            "publish_execution_allowed": True,
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

    expected_destination = _verified_support_destination(channel)
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
    if admission.daily_idea_decision_id is not None:
        required_ids["daily_idea_decision"] = admission.daily_idea_decision_id
    source_by_type = {source.type: source for source in envelope.frozen_sources}
    if len(source_by_type) != len(envelope.frozen_sources) or any(
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

    builders: dict[str, tuple[type[Any], Any]] = {
        "daily_idea_decision": (DailyIdeaDecision, _daily_idea_source),
        "editorial_calendar_slot": (
            EditorialCalendarSlot,
            _editorial_slot_source,
        ),
        "idea_market_preflight": (IdeaMarketPreflight, _preflight_source),
        "search_intent_map": (SearchIntentMap, _search_intent_source),
        "audience_target_pack": (AudienceTargetPack, _audience_source),
    }
    for source in envelope.frozen_sources:
        binding = builders.get(source.type)
        if binding is None:
            raise _support_envelope_integrity_error(
                "V2_FROZEN_SUPPORT_SOURCE_TYPE_INVALID"
            )
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
    actual: dict[str, str] = {}
    for receipt in envelope.gate_receipts:
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") != "PASS"
            or not isinstance(receipt.get("gate_key"), str)
            or not isinstance(receipt.get("receipt_hash"), str)
            or receipt["gate_key"] in actual
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


def _planning_source(
    context: WorkflowStageContext,
    project: VideoProject,
    admission: ProjectAdmissionDecision,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_payload: dict[str, Any]
    refs: list[dict[str, Any]]
    if admission.daily_idea_decision_id is not None:
        idea = context.session.get(DailyIdeaDecision, admission.daily_idea_decision_id)
        preflight = (
            context.session.get(
                IdeaMarketPreflight,
                admission.idea_market_preflight_id,
            )
            if admission.idea_market_preflight_id is not None
            else None
        )
        if (
            idea is None
            or preflight is None
            or idea.schema_version != "v2"
            or idea.company_id != project.company_id
            or idea.channel_workspace_id != project.channel_workspace_id
            or idea.policy_snapshot_id != project.policy_snapshot_id
            or idea.production_lane != ProductionLane.DAILY_SHORT.value
            or idea.decision_status != "ADMITTED"
            or preflight.company_id != project.company_id
            or preflight.channel_workspace_id != project.channel_workspace_id
            or preflight.channel_daily_run_id != admission.channel_daily_run_id
            or preflight.daily_idea_decision_id != idea.id
            or preflight.editorial_calendar_slot_id
            != admission.editorial_calendar_slot_id
            or preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
        ):
            raise ValidationFailureError("V2_SUPPORT_DAILY_SOURCE_MISMATCH")
        source_payload = {
            "source_type": "DAILY_IDEA",
            "source_id": str(idea.id),
            "title": idea.proposed_title,
            "angle": idea.proposed_angle,
            "format": idea.proposed_format,
            "pillar": idea.proposed_pillar,
            "rationale": idea.rationale,
            "evidence_refs": idea.evidence_refs,
            "reason_codes": idea.reason_codes,
            "preflight_id": str(preflight.id),
            "preflight_evidence": preflight.evidence_blob,
            "preflight_reasons": preflight.reason_codes,
        }
        refs = [
            {
                "type": "daily_idea_decision",
                "id": str(idea.id),
                "content_hash": semantic_hash(source_payload),
            },
            {
                "type": "idea_market_preflight",
                "id": str(preflight.id),
                "content_hash": semantic_hash(
                    {
                        "decision": preflight.decision,
                        "policy_fit_state": preflight.policy_fit_state,
                        "confidence_state": preflight.confidence_state,
                        "evidence_blob": preflight.evidence_blob,
                        "reason_codes": preflight.reason_codes,
                    }
                ),
            },
        ]
    elif (
        str(admission.planning_source_type) == PlanningSourceType.LONG_FORM_PLAN.value
        and admission.production_lane == ProductionLane.LONG_FORM.value
    ):
        slot = (
            context.session.get(
                EditorialCalendarSlot,
                admission.editorial_calendar_slot_id,
            )
            if admission.editorial_calendar_slot_id is not None
            else None
        )
        preflight = (
            context.session.get(
                IdeaMarketPreflight,
                admission.idea_market_preflight_id,
            )
            if admission.idea_market_preflight_id is not None
            else None
        )
        if (
            slot is None
            or preflight is None
            or slot.schema_version != "v2"
            or slot.company_id != project.company_id
            or slot.channel_workspace_id != project.channel_workspace_id
            or slot.policy_snapshot_id != project.policy_snapshot_id
            or str(slot.production_lane) != project.production_lane
            or str(slot.assignment_mode) != project.assignment_mode
            or preflight.company_id != project.company_id
            or preflight.channel_workspace_id != project.channel_workspace_id
            or preflight.editorial_calendar_slot_id != slot.id
            or preflight.decision != "PASS"
            or preflight.policy_fit_state != "PASS"
        ):
            raise ValidationFailureError("V2_SUPPORT_LONG_SOURCE_MISMATCH")
        source_payload = {
            "source_type": str(admission.planning_source_type),
            "slot_id": str(slot.id),
            "preflight_id": str(preflight.id),
            "title": project.title,
            "description": project.description,
            "production_goal": slot.production_goal,
            "content_pillar": slot.content_pillar,
            "target_platforms": slot.target_platforms,
            "preflight_evidence": preflight.evidence_blob,
            "preflight_reasons": preflight.reason_codes,
        }
        refs = [
            {
                "type": "editorial_calendar_slot",
                "id": str(slot.id),
                "content_hash": semantic_hash(
                    {
                        "schema_version": slot.schema_version,
                        "production_lane": str(slot.production_lane),
                        "assignment_mode": str(slot.assignment_mode),
                        "category_id": str(slot.category_id),
                        "production_goal": slot.production_goal,
                        "target_platforms": slot.target_platforms,
                    }
                ),
            },
            {
                "type": "idea_market_preflight",
                "id": str(preflight.id),
                "content_hash": semantic_hash(
                    {
                        "decision": preflight.decision,
                        "policy_fit_state": preflight.policy_fit_state,
                        "confidence_state": preflight.confidence_state,
                        "evidence_blob": preflight.evidence_blob,
                        "reason_codes": preflight.reason_codes,
                    }
                ),
            },
        ]
    elif (
        str(admission.planning_source_type) == PlanningSourceType.DERIVED_SHORT.value
        and admission.production_lane == ProductionLane.LONG_DERIVED_SHORT.value
    ):
        parent_script, parent_script_version, parent_lineage_version = (
            _derived_parent_authorities(context, project)
        )
        derived_script = _deterministic_derived_short_script(
            parent_script,
            ProductionDurationContractV2.model_validate(project.duration_contract),
        )
        source_payload = {
            "source_type": PlanningSourceType.DERIVED_SHORT.value,
            "parent_video_project_id": str(project.parent_video_project_id),
            "parent_final_media_ref_id": str(project.parent_final_media_ref_id),
            "canonical_timeline_ref": project.canonical_timeline_ref,
            "canonical_timeline_hash": project.canonical_timeline_hash,
            "approved_script": derived_script,
            "derivative_method": (
                "DETERMINISTIC_PARENT_APPROVED_SCRIPT_SENTENCE_PREFIX"
            ),
        }
        refs = [
            {
                "type": "parent_approved_script",
                "id": str(parent_script_version.id),
                "content_hash": parent_script_version.content_hash,
            },
            {
                "type": "parent_final_media_lineage",
                "id": str(parent_lineage_version.id),
                "content_hash": parent_lineage_version.content_hash,
            },
        ]
    else:
        raise ValidationFailureError("V2_SUPPORT_PLANNING_SOURCE_INVALID")
    refs.append(
        {
            "type": "project_admission_decision",
            "id": str(admission.id),
            "content_hash": str(admission.decision_hash),
        }
    )
    return source_payload, refs


def _derived_parent_authorities(
    context: WorkflowStageContext,
    project: VideoProject,
) -> tuple[str, ArtifactVersion, ArtifactVersion]:
    parent_id = project.parent_video_project_id
    final_media_id = project.parent_final_media_ref_id
    timeline_hash = project.canonical_timeline_hash
    if (
        parent_id is None
        or final_media_id is None
        or not project.canonical_timeline_ref
        or not timeline_hash
    ):
        raise ValidationFailureError("V2_SUPPORT_DERIVATIVE_PARENT_AUTHORITY_REQUIRED")
    parent = context.session.get(VideoProject, parent_id)
    final_media = context.session.get(FinalMediaRef, final_media_id)
    current_final_media = context.session.scalars(
        select(FinalMediaRef)
        .where(FinalMediaRef.video_project_id == parent_id)
        .order_by(
            FinalMediaRef.created_at.desc(),
            FinalMediaRef.id.desc(),
        )
    ).first()
    if (
        parent is None
        or parent.company_id != project.company_id
        or parent.channel_workspace_id != project.channel_workspace_id
        or parent.production_lane != ProductionLane.LONG_FORM.value
        or parent.canonical_timeline_ref != project.canonical_timeline_ref
        or parent.canonical_timeline_hash != timeline_hash
        or final_media is None
        or final_media.video_project_id != parent.id
        or current_final_media is None
        or current_final_media.id != final_media.id
        or final_media.checksum_sha256 is None
    ):
        raise ValidationFailureError("V2_SUPPORT_DERIVATIVE_PARENT_AUTHORITY_MISMATCH")

    script_artifact = context.session.scalars(
        select(Artifact)
        .where(
            Artifact.video_project_id == parent.id,
            Artifact.artifact_type == "script",
        )
        .order_by(Artifact.created_at.desc())
    ).first()
    script_version = (
        context.session.get(
            ArtifactVersion,
            script_artifact.current_version_id,
        )
        if script_artifact is not None
        and script_artifact.current_version_id is not None
        else None
    )
    lineage_artifact = context.session.scalars(
        select(Artifact)
        .where(
            Artifact.video_project_id == parent.id,
            Artifact.artifact_type == "mr1_final_media_lineage_receipt",
        )
        .order_by(Artifact.created_at.desc())
    ).first()
    lineage_version = (
        context.session.get(
            ArtifactVersion,
            lineage_artifact.current_version_id,
        )
        if lineage_artifact is not None
        and lineage_artifact.current_version_id is not None
        else None
    )
    script_content = script_version.content if script_version is not None else None
    script_lineage = (
        script_content.get("lineage") if isinstance(script_content, dict) else None
    )
    script_domain = (
        (script_version.packaging_metadata or {}).get("_vcos_domain_authority")
        if script_version is not None
        else None
    )
    lineage_content = lineage_version.content if lineage_version is not None else None
    lineage_domain = (
        (lineage_version.packaging_metadata or {}).get("_vcos_domain_authority")
        if lineage_version is not None
        else None
    )
    parent_script = (
        script_content.get("narration_text")
        if isinstance(script_content, dict)
        else None
    )
    if (
        script_artifact is None
        or script_version is None
        or script_artifact.status != "approved"
        or script_version.status != "approved"
        or not isinstance(script_content, dict)
        or script_content.get("schema_version") != "vcos.approved-script.v2"
        or semantic_hash(script_content) != script_version.content_hash
        or not isinstance(script_lineage, dict)
        or str(script_lineage.get("video_project_id")) != str(parent.id)
        or not isinstance(script_domain, dict)
        or script_domain.get("writer") != "server_domain_service"
        or script_domain.get("artifact_type") != "script"
        or script_domain.get("content_hash") != script_version.content_hash
        or not isinstance(parent_script, str)
        or not parent_script.strip()
        or lineage_artifact is None
        or lineage_version is None
        or lineage_artifact.status != "approved"
        or lineage_version.status != "approved"
        or final_media.lineage_artifact_version_id != lineage_version.id
        or not isinstance(lineage_content, dict)
        or semantic_hash(lineage_content) != lineage_version.content_hash
        or lineage_content.get("schema_version") != "vcos.native-final-media-lineage.v2"
        or str(lineage_content.get("video_project_id")) != str(parent.id)
        or lineage_content.get("render_output_checksum") != final_media.checksum_sha256
        or lineage_content.get("canonical_media_timeline_hash") != timeline_hash
        or not isinstance(lineage_domain, dict)
        or lineage_domain.get("writer") != "server_domain_service"
        or lineage_domain.get("artifact_type") != "mr1_final_media_lineage_receipt"
        or lineage_domain.get("content_hash") != lineage_version.content_hash
    ):
        raise ValidationFailureError("V2_SUPPORT_DERIVATIVE_PARENT_LINEAGE_MISMATCH")
    return parent_script.strip(), script_version, lineage_version


def _deterministic_derived_short_script(
    parent_script: str,
    duration: ProductionDurationContractV2,
) -> str:
    minimum_words = (duration.minimum_duration_ms * 150 + 59_999) // 60_000
    maximum_words = duration.maximum_duration_ms * 150 // 60_000
    target_words = min(
        maximum_words,
        max(
            minimum_words,
            round(duration.target_duration_ms * 150 / 60_000),
        ),
    )
    selected: list[str] = []
    selected_words = 0
    for sentence in _sentences(parent_script):
        sentence_words = len(re.findall(r"\b[\w'-]+\b", sentence, flags=re.UNICODE))
        if selected_words + sentence_words > maximum_words:
            break
        selected.append(sentence)
        selected_words += sentence_words
        if selected_words >= target_words and len(selected) >= 3:
            break
    if (
        len(selected) < 3
        or selected_words < minimum_words
        or selected_words > maximum_words
    ):
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="V2_SUPPORT_DERIVED_SCRIPT_DURATION_INVALID",
            summary=(
                "The approved parent script cannot yield a complete "
                "sentence-bound Short inside the frozen duration contract."
            ),
            incident_type="INTEGRITY_MISMATCH",
            retry_eligible=False,
        )
    return " ".join(selected)


def _approved_script_text(
    *,
    source_payload: dict[str, Any],
    duration: ProductionDurationContractV2,
) -> str:
    candidates = [
        *_values_for_keys(
            source_payload,
            {
                "approved_script",
                "approved_script_text",
                "narration_text",
                "script_text",
            },
        ),
    ]
    text = max(
        (value.strip() for value in candidates if value.strip()),
        key=len,
        default="",
    )
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    # Use the frozen 150-wpm production timing model.  Refuse to invent or
    # repeat prose simply to reach the channel duration.
    estimated_duration_ms = round(len(words) / 150 * 60_000)
    if (
        len(words) < 24
        or estimated_duration_ms < duration.minimum_duration_ms
        or estimated_duration_ms > duration.maximum_duration_ms
    ):
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="V2_SUPPORT_APPROVED_SCRIPT_DURATION_INVALID",
            summary=(
                "The frozen planning source does not carry an approved "
                "non-padded script inside the channel duration contract."
            ),
            incident_type="INTEGRITY_MISMATCH",
            retry_eligible=False,
            operator_visible_blocker=(
                "Tạo planning source mới có approved_script đủ bằng chứng "
                "và đúng duration contract; VCOS không tự chèn filler."
            ),
        )
    return text


def _values_for_keys(
    value: Any,
    keys: set[str],
) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in keys and isinstance(child, str):
                yield child
            yield from _values_for_keys(child, keys)
    elif isinstance(value, list):
        for child in value:
            yield from _values_for_keys(child, keys)


def _verified_destination(channel: ChannelWorkspace) -> dict[str, Any]:
    governance = (channel.metadata_ or {}).get("destination_governance")
    bindings = governance.get("bindings") if isinstance(governance, dict) else None
    active_ref = (
        str(governance.get("active_binding_ref") or "")
        if isinstance(governance, dict)
        else ""
    )
    candidates = bindings if isinstance(bindings, list) else []
    active = next(
        (
            item
            for item in candidates
            if isinstance(item, dict)
            and active_ref
            == (f"destination-binding://{channel.key}/v{item.get('binding_version')}")
        ),
        None,
    )
    try:
        validated = (
            DestinationBinding.model_validate(active)
            if isinstance(active, dict)
            else None
        )
    except Exception as exc:
        raise ValidationFailureError("V2_SUPPORT_DESTINATION_BINDING_INVALID") from exc
    if (
        validated is None
        or validated.channel_id != channel.id
        or validated.destination_status != "VERIFIED"
        or validated.verification_state != "VERIFIED"
        or not validated.platform_channel_id
        or not validated.platform_account_ref
    ):
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="V2_SUPPORT_VERIFIED_DESTINATION_REQUIRED",
            summary=(
                "The active channel has no exact verified manual-publish "
                "destination binding."
            ),
            incident_type="INTEGRITY_MISMATCH",
            retry_eligible=False,
            operator_visible_blocker=(
                "Hoàn tất destination verification trong Channel Profile."
            ),
        )
    return {
        **validated.model_dump(mode="json"),
        "active_binding_ref": active_ref,
        "publish_execution_allowed": True,
    }


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
    modes = {
        "MEDIA": "PACKAGE_NATIVE_TIMELINE",
        "RENDER": "NATIVE_FFMPEG_LOCAL",
        "QC": "AUTOMATED_NATIVE_QC",
        "ARCHIVE": "LOCAL_VERIFIED_ARCHIVE",
    }
    audio_strategy = (
        "LOCAL_OS_TTS_SCRIPT_BOUND"
        if authority.project.production_lane == ProductionLane.LONG_FORM.value
        else "SILENT_STEREO_TEXT_LED"
    )
    route_by_stage = (
        {route.stage: route for route in authority.support_envelope.native_routes}
        if authority.support_envelope is not None
        else {}
    )
    for stage, mode in modes.items():
        route = route_by_stage.get(stage)
        operation_id = (
            route.operation_id
            if route is not None
            else f"v2:{authority.project.id}:{stage.lower()}"
        )
        adapter_operations[stage] = {
            "schema_version": "vcos.provider-adapter-operation.v1",
            "execution_authorized": True,
            "production_eligible": True,
            "fixture_only": False,
            "invokes_mr1": False,
            "automatic_publish": False,
            "stage": stage,
            "production_lane": authority.project.production_lane,
            "paid_provider_call": False,
            "operation_id": operation_id,
            "adapter_key": (
                route.adapter_key if route is not None else "v2-local-native"
            ),
            "max_cost_usd": "0",
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
            "parameters": {
                "mode": mode,
                "audio_strategy": audio_strategy,
            },
        }
        operation_authorizations[operation_id] = {
            "authorized": True,
            "operation_id": operation_id,
            "adapter_key": (
                route.adapter_key if route is not None else "v2-local-native"
            ),
            "stage": stage,
            "paid_provider_call": False,
            "max_cost_usd": "0",
            **({"route_hash": route.route_hash} if route is not None else {}),
        }
    target_surface = (
        "LONG_FORM"
        if authority.project.production_lane == ProductionLane.LONG_FORM.value
        else "SHORTS"
    )
    destination = authority.destination
    envelope = authority.support_envelope
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
                        )
                    },
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
                    "source_policy": (
                        "LOCAL_GENERATED_CARDS_ONLY"
                        if envelope is not None
                        else "LOCAL_OR_RIGHTS_VERIFIED_ONLY"
                    ),
                }
                for index, section in enumerate(sections)
            ],
            "aspect_ratio": ("16:9" if target_surface == "LONG_FORM" else "9:16"),
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
            "asset_policy": (
                "LOCAL_GENERATED_CARDS_ONLY"
                if envelope is not None
                else "LOCAL_OR_RIGHTS_VERIFIED_ONLY"
            ),
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
            "retry_authorized": True,
            "max_attempts": 5,
            "retry_cost_usd": "0",
            "production_lane": authority.project.production_lane,
            "fixture_only": False,
            "invokes_mr1": False,
            "automatic_publish": False,
            "paid_provider_calls": False,
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
                    "destination_binding_hash": (
                        envelope.verified_destination.content_hash
                        if envelope is not None
                        else semantic_hash(destination)
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
            "retry_authorized": True,
            "max_attempts": 5,
            "retry_cost_usd": "0",
            "remaining_budget_usd": "0",
            "operation_authorizations": operation_authorizations,
            **(
                {
                    "zero_cost_budget_authority": (
                        envelope.zero_cost_budget.model_dump(mode="json")
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
            "result": "PASS",
            "publish_execution_allowed": True,
            "destination_binding": destination,
            **(
                {
                    "verified_destination_authority": (
                        envelope.verified_destination.model_dump(mode="json")
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
    return {
        "schema_version": "vcos.approved-script.v2",
        "readiness_result": "PASS",
        "title": authority.project.title,
        "narration_text": authority.approved_script,
        "estimated_duration_ms": estimated_duration_ms,
        "speech_rate_wpm": 150,
        "supported_claims": claims,
        "sections": sections,
        "research_coverage_ratio": 1.0,
        "repeated_sentence_ratio": repeated_ratio,
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


def _parent_derivative_lineage(
    context: WorkflowStageContext,
    authority: _SupportAuthority,
) -> ExactContentRefV2 | None:
    if authority.project.production_lane != ProductionLane.LONG_DERIVED_SHORT.value:
        return None
    _, _, lineage_version = _derived_parent_authorities(
        context,
        authority.project,
    )
    return _exact_ref("parent_derivative_lineage", lineage_version)


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
