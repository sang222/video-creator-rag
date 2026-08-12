"""Package-authorized v2 post-readiness production gateway.

The durable coordinator does not select providers and this module does not
contain an MR1 runner, fixture renderer, or publishing client.  Instead, the
canonical provider plan selects a registered production adapter for each
effect.  The exact package budget authority independently authorizes that
operation before the adapter receives the workflow command id as its external
idempotency key.
"""

from __future__ import annotations

import uuid
import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.contracts.geo_market import DestinationBinding
from app.contracts.production_publish import FinalReviewCandidateCreateV2
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowFailureClassification,
    WorkflowStageResult,
)
from app.contracts.vcos_v2 import ProductionLane
from app.core.errors import ValidationFailureError
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.production_package import ProductionPackageService, semantic_hash
from app.services.production_workflow import (
    PostReadinessProductionGatewayDescriptor,
    WorkflowStageError,
    WorkflowStageContext,
)


V2_PROVIDER_GATEWAY_VERSION = "vcos.v2-provider-gateway.v1"
V2_PROVIDER_PLAN_SCHEMA = "vcos.post-readiness-provider-plan.v2"
V2_ADAPTER_OPERATION_SCHEMA = "vcos.provider-adapter-operation.v1"
V2_BUDGET_OPERATION_SCHEMA = "vcos.operation-budget-authority.v1"
V2_QUALIFICATION_MODE = "QUALIFICATION_LOCAL"
V2_REAL_PRODUCTION_MODE = "REAL_LONG_FORM_PRODUCTION"
_V2_EXECUTION_MODES = frozenset({V2_QUALIFICATION_MODE, V2_REAL_PRODUCTION_MODE})
_V2_REAL_ADAPTERS_BY_STAGE = {
    ProductionWorkflowStage.MEDIA: "v2-elevenlabs-narration",
    ProductionWorkflowStage.RENDER: "v2-local-native",
    ProductionWorkflowStage.QC: "v2-local-native",
    ProductionWorkflowStage.ARCHIVE: "v2-google-drive-remote",
}
V2_EFFECT_STAGES = frozenset(
    {
        ProductionWorkflowStage.MEDIA,
        ProductionWorkflowStage.RENDER,
        ProductionWorkflowStage.QC,
        ProductionWorkflowStage.ARCHIVE,
    }
)


@dataclass(frozen=True, slots=True)
class V2ProductionAdapterDescriptor:
    """Code-reviewed capability declaration for one effect adapter."""

    adapter_key: str
    supported_stages: frozenset[ProductionWorkflowStage]
    production_eligible: bool
    fixture_only: bool
    invokes_mr1: bool
    paid_provider_calls: bool
    automatic_publish: bool

    def __post_init__(self) -> None:
        if (
            not self.adapter_key
            or len(self.adapter_key) > 80
            or any(
                character
                not in (
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                )
                for character in self.adapter_key
            )
        ):
            raise ValueError("V2_PRODUCTION_ADAPTER_KEY_INVALID")
        if not self.supported_stages or not self.supported_stages.issubset(
            V2_EFFECT_STAGES
        ):
            raise ValueError("V2_PRODUCTION_ADAPTER_STAGE_INVALID")
        if not self.production_eligible:
            raise ValueError("V2_PRODUCTION_ADAPTER_NOT_PRODUCTION_ELIGIBLE")
        forbidden = {
            "fixture_only": self.fixture_only,
            "invokes_mr1": self.invokes_mr1,
            "automatic_publish": self.automatic_publish,
        }
        enabled = sorted(key for key, value in forbidden.items() if value)
        if enabled:
            raise ValueError(
                "V2_PRODUCTION_ADAPTER_FORBIDDEN_CAPABILITY:" + ",".join(enabled)
            )


@dataclass(frozen=True, slots=True)
class V2AuthorizedAdapterOperation:
    """One immutable, package-and-budget-authorized adapter invocation."""

    operation_id: str
    stage: ProductionWorkflowStage
    adapter_key: str
    paid_provider_call: bool
    max_cost_usd: Decimal
    parameters: Mapping[str, Any]
    execution_mode: str = V2_QUALIFICATION_MODE


@runtime_checkable
class V2ProductionOperationAdapter(Protocol):
    descriptor: V2ProductionAdapterDescriptor

    def execute(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> WorkflowStageResult:
        """Execute or reconcile exactly ``context.command_id``."""


@runtime_checkable
class V2MediaProviderGateway(Protocol):
    def produce_media(self, context: WorkflowStageContext) -> WorkflowStageResult: ...


@runtime_checkable
class V2RenderGateway(Protocol):
    def render_media(self, context: WorkflowStageContext) -> WorkflowStageResult: ...


@runtime_checkable
class V2QualityControlGateway(Protocol):
    def run_quality_control(
        self, context: WorkflowStageContext
    ) -> WorkflowStageResult: ...


@runtime_checkable
class V2ArchiveGateway(Protocol):
    def archive_media(self, context: WorkflowStageContext) -> WorkflowStageResult: ...


@runtime_checkable
class V2FinalReviewPresentationGateway(Protocol):
    def build_final_review_candidate(
        self, context: WorkflowStageContext
    ) -> FinalReviewCandidateCreateV2: ...


@dataclass(frozen=True, slots=True)
class V2ProviderProductionGateway:
    """Concrete composition root for all post-readiness v2 stages."""

    media: V2MediaProviderGateway
    renderer: V2RenderGateway
    quality_control: V2QualityControlGateway
    archive: V2ArchiveGateway
    presentation: V2FinalReviewPresentationGateway
    descriptor: PostReadinessProductionGatewayDescriptor = (
        PostReadinessProductionGatewayDescriptor(
            gateway_id="v2-provider",
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
        dependencies = (
            (self.media, V2MediaProviderGateway),
            (self.renderer, V2RenderGateway),
            (self.quality_control, V2QualityControlGateway),
            (self.archive, V2ArchiveGateway),
            (self.presentation, V2FinalReviewPresentationGateway),
        )
        if any(
            not isinstance(dependency, contract)
            for dependency, contract in dependencies
        ):
            raise TypeError("V2_PROVIDER_GATEWAY_DEPENDENCY_INVALID")

    def produce_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self.media.produce_media(context)

    def render_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self.renderer.render_media(context)

    def run_quality_control(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self.quality_control.run_quality_control(context)

    def archive_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self.archive.archive_media(context)

    def build_final_review_candidate(
        self, context: WorkflowStageContext
    ) -> FinalReviewCandidateCreateV2:
        return self.presentation.build_final_review_candidate(context)


class PackageBoundV2StageGateway:
    """Execute package-selected operations through code-registered adapters."""

    def __init__(
        self,
        adapters: Mapping[str, V2ProductionOperationAdapter] | None = None,
    ) -> None:
        self._adapters: dict[str, V2ProductionOperationAdapter] = {}
        for registry_key, adapter in (adapters or {}).items():
            if not isinstance(adapter, V2ProductionOperationAdapter):
                raise TypeError("V2_PRODUCTION_OPERATION_ADAPTER_INVALID")
            descriptor = adapter.descriptor
            if not isinstance(descriptor, V2ProductionAdapterDescriptor):
                raise TypeError("V2_PRODUCTION_ADAPTER_DESCRIPTOR_INVALID")
            # Reconstruct to prevent a mutable lookalike from bypassing the
            # descriptor validation performed at process configuration time.
            validated = V2ProductionAdapterDescriptor(
                adapter_key=descriptor.adapter_key,
                supported_stages=frozenset(descriptor.supported_stages),
                production_eligible=descriptor.production_eligible,
                fixture_only=descriptor.fixture_only,
                invokes_mr1=descriptor.invokes_mr1,
                paid_provider_calls=descriptor.paid_provider_calls,
                automatic_publish=descriptor.automatic_publish,
            )
            if registry_key != validated.adapter_key:
                raise ValueError("V2_PRODUCTION_ADAPTER_REGISTRY_KEY_MISMATCH")
            if registry_key in self._adapters:
                raise ValueError("V2_PRODUCTION_ADAPTER_DUPLICATE")
            self._adapters[registry_key] = adapter

    @property
    def paid_provider_calls(self) -> bool:
        return any(
            adapter.descriptor.paid_provider_calls
            for adapter in self._adapters.values()
        )

    @property
    def registered_adapter_keys(self) -> frozenset[str]:
        """Expose the concrete process registry for pre-start readiness only."""

        return frozenset(self._adapters)

    def produce_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self._execute_operation(context, ProductionWorkflowStage.MEDIA)

    def render_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self._execute_operation(context, ProductionWorkflowStage.RENDER)

    def run_quality_control(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self._execute_operation(context, ProductionWorkflowStage.QC)

    def archive_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        result = self._execute_operation(context, ProductionWorkflowStage.ARCHIVE)
        operation = _authorized_adapter_operation(
            context, ProductionWorkflowStage.ARCHIVE
        )
        refs = result.authority_refs
        if (
            refs.final_media_ref_id is None
            or refs.archive_receipt_hash is None
            or refs.archive_verification_state != "VERIFIED"
            or refs.final_media_ref_hash != context.run.render_output_checksum
        ):
            raise ValidationFailureError("V2_PROVIDER_ARCHIVE_RESULT_INCOMPLETE")
        if operation.execution_mode != V2_REAL_PRODUCTION_MODE:
            raise ValidationFailureError("V2_QUALIFICATION_FINAL_MEDIA_FORBIDDEN")
        if operation.adapter_key in {
            "v2-google-drive-archive",
            "v2-google-drive-remote",
        }:
            from app.services.v2_drive_archive import (
                require_v2_google_drive_final_media,
            )

            require_v2_google_drive_final_media(
                context.session,
                project_id=context.run.video_project_id,
                final_media_id=refs.final_media_ref_id,
                expected_checksum=context.run.render_output_checksum,
                expected_archive_hash=refs.archive_receipt_hash,
            )
        else:
            _require_verified_final_media(
                context.session,
                context.run.video_project_id,
                refs.final_media_ref_id,
                expected_checksum=context.run.render_output_checksum,
                expected_archive_hash=refs.archive_receipt_hash,
            )
        destination = _destination_authority(context)
        expected_destination = _normalized_destination(destination.content)
        if (
            refs.destination_binding_id != destination.id
            or refs.destination_binding_fingerprint != destination.content_hash
            or refs.destination_binding != expected_destination
        ):
            raise ValidationFailureError("V2_PROVIDER_ARCHIVE_DESTINATION_MISMATCH")
        return result

    def _execute_operation(
        self,
        context: WorkflowStageContext,
        stage: ProductionWorkflowStage,
    ) -> WorkflowStageResult:
        _require_long_form_context(context)
        operation = _authorized_adapter_operation(context, stage)
        if (
            operation.execution_mode == V2_QUALIFICATION_MODE
            and stage == ProductionWorkflowStage.ARCHIVE
        ):
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
                error_code="V2_QUALIFICATION_FINAL_MEDIA_FORBIDDEN",
                summary=(
                    "Qualification-local authority cannot create final media "
                    "or a final review candidate."
                ),
                incident_type="POLICY_BLOCK",
                retry_eligible=False,
            )
        if operation.execution_mode == V2_REAL_PRODUCTION_MODE:
            expected_adapter = _V2_REAL_ADAPTERS_BY_STAGE[stage]
            if operation.adapter_key != expected_adapter:
                raise ValidationFailureError("V2_REAL_PROVIDER_ADAPTER_ROUTE_MISMATCH")
            _require_real_provider_operation(operation)
        adapter = self._adapters.get(operation.adapter_key)
        if adapter is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code="V2_PRODUCTION_ADAPTER_NOT_CONFIGURED",
                summary=(
                    "The package-authorized production adapter is not "
                    f"configured: {operation.adapter_key}"
                ),
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
            )
        descriptor = adapter.descriptor
        if (
            stage not in descriptor.supported_stages
            or descriptor.paid_provider_calls != operation.paid_provider_call
        ):
            raise ValidationFailureError("V2_PRODUCTION_ADAPTER_CAPABILITY_MISMATCH")
        context.ensure_active()
        result = adapter.execute(context=context, operation=operation)
        context.ensure_active()
        if not isinstance(result, WorkflowStageResult):
            result = WorkflowStageResult.model_validate(result)
        payload = dict(result.result_payload)
        payload.update(
            {
                "adapter_key": operation.adapter_key,
                "provider_operation_id": operation.operation_id,
                "effect_idempotency_key": context.command_id,
            }
        )
        return result.model_copy(
            update={
                "result_payload": payload,
                "reason_codes": [
                    *result.reason_codes,
                    "V2_PACKAGE_AUTHORIZED_ADAPTER_OPERATION_EXECUTED",
                ],
            }
        )

    def build_final_review_candidate(
        self, context: WorkflowStageContext
    ) -> FinalReviewCandidateCreateV2:
        _require_long_form_context(context)
        run = context.run
        project = context.session.get(VideoProject, run.video_project_id)
        if (
            project is None
            or project.production_lane != ProductionLane.LONG_FORM.value
            or project.planning_source_type != "LONG_FORM_PLAN"
        ):
            raise ValidationFailureError("V2_PROVIDER_PROJECT_REQUIRED")
        archive_operation = _authorized_adapter_operation(
            context, ProductionWorkflowStage.ARCHIVE
        )
        if archive_operation.execution_mode != V2_REAL_PRODUCTION_MODE:
            raise ValidationFailureError("V2_QUALIFICATION_FINAL_REVIEW_FORBIDDEN")
        if archive_operation.adapter_key in {
            "v2-google-drive-archive",
            "v2-google-drive-remote",
        }:
            from app.services.v2_drive_archive import (
                require_v2_google_drive_final_media,
            )

            require_v2_google_drive_final_media(
                context.session,
                project_id=run.video_project_id,
                final_media_id=_required_run_uuid(
                    run.final_media_ref_id, "final_media_ref_id"
                ),
                expected_checksum=_required_run_hash(
                    run.render_output_checksum, "render_output_checksum"
                ),
                expected_archive_hash=_required_run_hash(
                    run.archive_receipt_hash, "archive_receipt_hash"
                ),
            )
        final_review = _provider_plan(context).get("final_review")
        if not isinstance(final_review, dict):
            raise ValidationFailureError("V2_PROVIDER_FINAL_REVIEW_AUTHORITY_REQUIRED")
        destination_version = _destination_authority(context)
        destination = _normalized_destination(destination_version.content)
        if final_review.get("target_surface", "LONG_FORM") != "LONG_FORM":
            raise ValidationFailureError("V2_PROVIDER_LONG_FORM_SURFACE_REQUIRED")
        target_surface = "LONG_FORM"
        target_market_lineage = _required_mapping(final_review, "target_market_lineage")
        target_market_lineage.update(
            {
                "destination_mode": destination["destination_mode"],
                "destination_status": destination["destination_status"],
                "destination_handle": destination["destination_handle"],
                "destination_binding_ref": destination["destination_binding_ref"],
                "destination_binding_hash": destination["destination_binding_hash"],
                "destination_model_hash": destination["destination_model_hash"],
                "destination_authority_hash": destination["destination_authority_hash"],
                "publish_execution_allowed": destination["publish_execution_allowed"],
                "automatic_publish": destination["automatic_publish"],
            }
        )
        if destination["destination_mode"] == "FINAL_REVIEW_ONLY":
            target_market_lineage.update(
                {
                    "controlled_recovery_authority_id": destination[
                        "controlled_recovery_authority_id"
                    ],
                    "controlled_recovery_authority_hash": destination[
                        "controlled_recovery_authority_hash"
                    ],
                    "settlement_authority_id": destination[
                        "settlement_authority_id"
                    ],
                    "settlement_authority_hash": destination[
                        "settlement_authority_hash"
                    ],
                    "settlement_qualification_run_id": destination[
                        "settlement_qualification_run_id"
                    ],
                    "settlement_provenance_hash": destination[
                        "settlement_provenance_hash"
                    ],
                }
            )
        return FinalReviewCandidateCreateV2(
            workflow_run_id=run.id,
            production_package_artifact_version_id=(
                _required_run_uuid(
                    run.production_package_artifact_version_id,
                    "production_package_artifact_version_id",
                )
            ),
            production_package_hash=_required_run_hash(
                run.production_package_hash, "production_package_hash"
            ),
            production_readiness_receipt_artifact_version_id=(
                _required_run_uuid(
                    run.production_readiness_receipt_artifact_version_id,
                    "production_readiness_receipt_artifact_version_id",
                )
            ),
            production_readiness_receipt_hash=_required_run_hash(
                run.production_readiness_receipt_hash,
                "production_readiness_receipt_hash",
            ),
            canonical_media_timeline_ref=_required_run_text(
                run.canonical_media_timeline_ref,
                "canonical_media_timeline_ref",
            ),
            canonical_media_timeline_hash=_required_run_hash(
                run.canonical_media_timeline_hash,
                "canonical_media_timeline_hash",
            ),
            native_render_plan_ref=_required_run_text(
                run.native_render_plan_ref, "native_render_plan_ref"
            ),
            native_render_plan_hash=_required_run_hash(
                run.native_render_plan_hash, "native_render_plan_hash"
            ),
            render_output_ref=_required_run_text(
                run.render_output_ref, "render_output_ref"
            ),
            render_output_checksum=_required_run_hash(
                run.render_output_checksum, "render_output_checksum"
            ),
            technical_qc_receipt_ref=_required_run_text(
                run.technical_qc_receipt_ref, "technical_qc_receipt_ref"
            ),
            technical_qc_receipt_hash=_required_run_hash(
                run.technical_qc_receipt_hash, "technical_qc_receipt_hash"
            ),
            technical_qc_state="PASS",
            creative_qc_receipt_ref=_required_run_text(
                run.creative_qc_receipt_ref, "creative_qc_receipt_ref"
            ),
            creative_qc_receipt_hash=_required_run_hash(
                run.creative_qc_receipt_hash, "creative_qc_receipt_hash"
            ),
            creative_qc_state="PASS",
            archive_receipt_ref=_required_run_text(
                run.archive_receipt_ref, "archive_receipt_ref"
            ),
            archive_receipt_hash=_required_run_hash(
                run.archive_receipt_hash, "archive_receipt_hash"
            ),
            archive_object_ref=_required_run_text(
                run.archive_object_ref, "archive_object_ref"
            ),
            archive_verification_state="VERIFIED",
            final_media_ref_id=_required_run_uuid(
                run.final_media_ref_id, "final_media_ref_id"
            ),
            destination_binding_id=_required_run_uuid(
                run.destination_binding_id, "destination_binding_id"
            ),
            destination_binding_fingerprint=_required_run_hash(
                run.destination_binding_fingerprint,
                "destination_binding_fingerprint",
            ),
            destination_platform_channel_id=(destination["platform_channel_id"]),
            destination_account_identity=destination["account_identity"],
            target_platform=destination["platform"],
            target_surface=target_surface,
            target_market_lineage=target_market_lineage,
            publish_metadata_snapshot=_required_mapping(
                final_review, "publish_metadata_snapshot"
            ),
            disclosure_snapshot=dict(final_review.get("disclosure_snapshot") or {}),
        )


def build_v2_provider_production_gateway(
    *,
    adapters: Mapping[str, V2ProductionOperationAdapter] | None = None,
) -> V2ProviderProductionGateway:
    """Build the V2 gateway with concrete real-provider effect adapters."""

    configured_adapters = adapters
    if configured_adapters is None:
        # Import lazily because the adapter implements the protocol declared in
        # this module and is also usable directly with injected test storage.
        from app.services.v2_drive_archive import (
            V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY,
            V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY,
            V2GoogleDriveArchiveAdapter,
            V2GoogleDriveRemoteArchiveAdapter,
        )
        from app.services.v2_elevenlabs_narration import (
            V2_ELEVENLABS_NARRATION_ADAPTER_KEY,
            V2ElevenLabsNarrationAdapter,
        )
        from app.services.v2_native_effects import (
            V2_LOCAL_ADAPTER_KEY,
            V2LocalNativeProductionAdapter,
        )

        configured_adapters = {
            V2_LOCAL_ADAPTER_KEY: V2LocalNativeProductionAdapter(),
            V2_GOOGLE_DRIVE_ARCHIVE_ADAPTER_KEY: V2GoogleDriveArchiveAdapter(),
            V2_ELEVENLABS_NARRATION_ADAPTER_KEY: V2ElevenLabsNarrationAdapter(),
            V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY: V2GoogleDriveRemoteArchiveAdapter(),
        }
    package_bound = PackageBoundV2StageGateway(configured_adapters)
    return V2ProviderProductionGateway(
        media=package_bound,
        renderer=package_bound,
        quality_control=package_bound,
        archive=package_bound,
        presentation=package_bound,
        descriptor=PostReadinessProductionGatewayDescriptor(
            gateway_id="v2-provider",
            version="1.4.0",
            supported_lanes=frozenset({ProductionLane.LONG_FORM}),
            production_eligible=True,
            fixture_only=False,
            invokes_mr1=False,
            paid_provider_calls=package_bound.paid_provider_calls,
            automatic_publish=False,
        ),
    )


def _provider_plan(context: WorkflowStageContext) -> dict[str, Any]:
    _require_long_form_context(context)
    run = context.run
    if run.production_package_artifact_version_id is None:
        raise ValidationFailureError("V2_PROVIDER_PACKAGE_REQUIRED")
    package = ProductionPackageService(context.session).validate_for_readiness(
        run.production_package_artifact_version_id
    )
    version_id = package.provider_execution_plan_ref.artifact_version_id
    version = (
        context.session.get(ArtifactVersion, version_id)
        if version_id is not None
        else None
    )
    artifact = (
        context.session.get(Artifact, version.artifact_id)
        if version is not None
        else None
    )
    if (
        version is None
        or artifact is None
        or artifact.artifact_type != "provider_execution_plan"
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != package.provider_execution_plan_ref.content_hash
        or not isinstance(version.content, dict)
    ):
        raise ValidationFailureError("V2_PROVIDER_EXECUTION_PLAN_AUTHORITY_MISMATCH")
    plan = dict(version.content)
    if (
        plan.get("schema_version") != V2_PROVIDER_PLAN_SCHEMA
        or plan.get("execution_authorized") is not True
        or plan.get("fixture_only") is not False
        or plan.get("invokes_mr1") is not False
        or plan.get("automatic_publish") is not False
        or not isinstance(plan.get("paid_provider_calls"), bool)
        or plan.get("execution_mode") not in _V2_EXECUTION_MODES
        or "stage_authorities" in plan
    ):
        raise ValidationFailureError("V2_PROVIDER_EXECUTION_PLAN_NOT_AUTHORIZED")
    if str(plan.get("production_lane")) != run.production_lane:
        raise ValidationFailureError("V2_PROVIDER_EXECUTION_PLAN_LANE_MISMATCH")
    return plan


def _require_long_form_context(context: WorkflowStageContext) -> None:
    if (
        context.run.production_lane != ProductionLane.LONG_FORM.value
        or context.run.planning_source_type != "LONG_FORM_PLAN"
    ):
        raise ValidationFailureError("V2_PROVIDER_LONG_FORM_ONLY")


def _require_real_provider_operation(operation: V2AuthorizedAdapterOperation) -> None:
    """Fail closed on the real-provider bindings before any adapter lookup.

    This deliberately validates only stable references here.  Credentials are
    never copied into an immutable package, and no local qualification adapter
    is treated as a substitute for either external authority.
    """

    details = operation.parameters.get("provider_execution")
    if not isinstance(details, dict):
        raise ValidationFailureError("V2_REAL_PROVIDER_EXECUTION_DETAILS_REQUIRED")
    attempt_limit = details.get("attempt_limit")
    idempotency_key = details.get("idempotency_key")
    reservation_ref = details.get("budget_reservation_ref")
    if (
        attempt_limit != 1
        or not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or not isinstance(reservation_ref, str)
        or not reservation_ref.startswith("mr1-budget://")
    ):
        raise ValidationFailureError("V2_REAL_PROVIDER_EXECUTION_BINDING_INVALID")
    if operation.stage == ProductionWorkflowStage.MEDIA:
        if (
            details.get("provider") != "elevenlabs"
            or details.get("credential_ref") != "env://ELEVENLABS_API_KEY"
            or not isinstance(details.get("voice_id"), str)
            or not isinstance(details.get("model_id"), str)
            or not isinstance(details.get("voice_settings"), dict)
            or details.get("estimated_cost_usd") != str(operation.max_cost_usd)
            or not operation.paid_provider_call
            or operation.max_cost_usd <= 0
        ):
            raise ValidationFailureError("V2_REAL_ELEVENLABS_OPERATION_INVALID")
        if not (
            str(os.getenv("ELEVENLABS_API_KEY") or "").strip()
            or str(os.getenv("VCOS_ELEVENLABS_API_KEY") or "").strip()
        ):
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_REAL_ELEVENLABS_BLOCKED_CREDENTIAL",
                summary=(
                    "The package selects ElevenLabs final narration, but its "
                    "credential is unavailable; no local narration fallback "
                    "was attempted."
                ),
                incident_type="CREDENTIAL_BLOCK",
                retry_eligible=False,
                operator_visible_blocker=(
                    "Cấu hình lại ElevenLabs credential cho worker rồi tạo "
                    "một natural cadence run mới; không thay bằng local TTS."
                ),
            )
    elif operation.stage == ProductionWorkflowStage.ARCHIVE:
        if (
            details.get("provider") != "google_drive"
            or details.get("credential_ref") != "oauth://google-drive/channel-connected"
            or details.get("remote_object_required") is not True
            or details.get("checksum_readback_required") is not True
            or operation.paid_provider_call
            or operation.max_cost_usd != 0
        ):
            raise ValidationFailureError("V2_REAL_GOOGLE_DRIVE_OPERATION_INVALID")
    elif operation.paid_provider_call or operation.max_cost_usd != 0:
        raise ValidationFailureError("V2_REAL_LOCAL_STAGE_COST_INVALID")


def _authorized_adapter_operation(
    context: WorkflowStageContext,
    stage: ProductionWorkflowStage,
) -> V2AuthorizedAdapterOperation:
    """Resolve one pre-effect operation authorized by two exact authorities."""

    plan = _provider_plan(context)
    raw_operations = plan.get("adapter_operations")
    raw_operation = (
        raw_operations.get(stage.value) if isinstance(raw_operations, dict) else None
    )
    if not isinstance(raw_operation, dict):
        raise ValidationFailureError(
            f"V2_PROVIDER_ADAPTER_OPERATION_REQUIRED:{stage.value}"
        )
    if (
        raw_operation.get("schema_version") != V2_ADAPTER_OPERATION_SCHEMA
        or raw_operation.get("execution_authorized") is not True
        or raw_operation.get("production_eligible") is not True
        or raw_operation.get("fixture_only") is not False
        or raw_operation.get("invokes_mr1") is not False
        or raw_operation.get("automatic_publish") is not False
        or str(raw_operation.get("stage")) != stage.value
        or str(raw_operation.get("production_lane")) != context.run.production_lane
        or raw_operation.get("execution_mode") != plan.get("execution_mode")
        or not isinstance(raw_operation.get("paid_provider_call"), bool)
    ):
        raise ValidationFailureError(
            f"V2_PROVIDER_ADAPTER_OPERATION_NOT_AUTHORIZED:{stage.value}"
        )
    operation_id = _required_text(raw_operation, "operation_id")
    adapter_key = _required_text(raw_operation, "adapter_key")
    if any(
        other_stage != stage.value
        and isinstance(other, dict)
        and other.get("operation_id") == operation_id
        for other_stage, other in raw_operations.items()
    ):
        raise ValidationFailureError("V2_PROVIDER_ADAPTER_OPERATION_ID_DUPLICATE")
    if any(
        character
        not in ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
        for character in operation_id
    ):
        raise ValidationFailureError("V2_PROVIDER_ADAPTER_OPERATION_ID_INVALID")
    if (
        raw_operation["paid_provider_call"]
        and plan.get("paid_provider_calls") is not True
    ):
        raise ValidationFailureError("V2_PROVIDER_PAID_OPERATION_NOT_AUTHORIZED")
    try:
        max_cost = Decimal(str(raw_operation["max_cost_usd"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationFailureError("V2_PROVIDER_OPERATION_COST_INVALID") from exc
    if max_cost < 0 or (not raw_operation["paid_provider_call"] and max_cost != 0):
        raise ValidationFailureError("V2_PROVIDER_OPERATION_COST_INVALID")
    parameters = raw_operation.get("parameters")
    if not isinstance(parameters, dict):
        raise ValidationFailureError("V2_PROVIDER_OPERATION_PARAMETERS_REQUIRED")

    budget = _budget_authority(context)
    raw_authorizations = budget.get("operation_authorizations")
    budget_authorization = (
        raw_authorizations.get(operation_id)
        if isinstance(raw_authorizations, dict)
        else None
    )
    if (
        budget.get("schema_version") != V2_BUDGET_OPERATION_SCHEMA
        or budget.get("budget_authorized") is not True
        or not isinstance(budget_authorization, dict)
        or budget_authorization.get("authorized") is not True
        or budget_authorization.get("operation_id") != operation_id
        or budget_authorization.get("adapter_key") != adapter_key
        or budget_authorization.get("stage") != stage.value
        or budget_authorization.get("paid_provider_call")
        is not raw_operation["paid_provider_call"]
        or budget_authorization.get("execution_mode") != plan.get("execution_mode")
    ):
        raise ValidationFailureError("V2_PROVIDER_OPERATION_BUDGET_NOT_AUTHORIZED")
    try:
        authorized_cost = Decimal(str(budget_authorization["max_cost_usd"]))
        remaining = Decimal(str(budget["remaining_budget_usd"]))
    except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationFailureError("V2_PROVIDER_OPERATION_BUDGET_INVALID") from exc
    if (
        authorized_cost < 0
        or remaining < 0
        or authorized_cost < max_cost
        or remaining < max_cost
    ):
        raise ValidationFailureError("V2_PROVIDER_OPERATION_BUDGET_EXHAUSTED")
    return V2AuthorizedAdapterOperation(
        operation_id=operation_id,
        stage=stage,
        adapter_key=adapter_key,
        paid_provider_call=raw_operation["paid_provider_call"],
        max_cost_usd=max_cost,
        parameters=dict(parameters),
        execution_mode=str(plan.get("execution_mode", V2_QUALIFICATION_MODE)),
    )


def _budget_authority(context: WorkflowStageContext) -> dict[str, Any]:
    package_id = context.run.production_package_artifact_version_id
    if package_id is None:
        raise ValidationFailureError("V2_PROVIDER_PACKAGE_REQUIRED")
    package = ProductionPackageService(context.session).validate_for_readiness(
        package_id
    )
    version_id = package.budget_scope_ref.artifact_version_id
    version = (
        context.session.get(ArtifactVersion, version_id)
        if version_id is not None
        else None
    )
    artifact = (
        context.session.get(Artifact, version.artifact_id)
        if version is not None
        else None
    )
    if (
        version is None
        or artifact is None
        or artifact.video_project_id != context.run.video_project_id
        or artifact.artifact_type != "cost_estimate_snapshot"
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != package.budget_scope_ref.content_hash
        or not isinstance(version.content, dict)
    ):
        raise ValidationFailureError("V2_PROVIDER_BUDGET_AUTHORITY_MISMATCH")
    return dict(version.content)


def _destination_authority(
    context: WorkflowStageContext,
) -> ArtifactVersion:
    package_id = context.run.production_package_artifact_version_id
    if package_id is None:
        raise ValidationFailureError("V2_PROVIDER_PACKAGE_REQUIRED")
    package = ProductionPackageService(context.session).validate_for_readiness(
        package_id
    )
    version_id = package.destination_binding_ref.artifact_version_id
    version = (
        context.session.get(ArtifactVersion, version_id)
        if version_id is not None
        else None
    )
    artifact = (
        context.session.get(Artifact, version.artifact_id)
        if version is not None
        else None
    )
    if (
        version is None
        or artifact is None
        or artifact.artifact_type != "destination_binding"
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != package.destination_binding_ref.content_hash
        or not isinstance(version.content, dict)
        or semantic_hash(version.content) != version.content_hash
    ):
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_AUTHORITY_MISMATCH")
    return version


def _normalized_destination(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_CONTENT_INVALID")
    nested = content.get("destination_binding", content.get("destination"))
    payload = nested if isinstance(nested, dict) else content
    status = str(payload.get("destination_status", payload.get("status", ""))).upper()
    authority_key = (
        "final_review_only_destination_authority"
        if status == "PENDING_PLATFORM_ID"
        else "verified_destination_authority"
    )
    authority = content.get(authority_key)
    authority_binding = (
        authority.get("binding") if isinstance(authority, dict) else None
    )
    if not isinstance(authority, dict) or not isinstance(authority_binding, dict):
        platform = str(payload.get("platform") or "").strip().upper()
        platform_channel_id = str(payload.get("platform_channel_id") or "").strip()
        account_identity = str(
            payload.get("platform_account_ref", payload.get("account_identity")) or ""
        ).strip()
        verification_state = str(payload.get("verification_state") or "").upper()
        verified_evidence = verification_state == "VERIFIED" or (
            bool(payload.get("verified_at")) and bool(payload.get("verification_method"))
        )
        if (
            status != "VERIFIED"
            or content.get("publish_execution_allowed") is not True
            or content.get("automatic_publish", False) is not False
            or not verified_evidence
            or not platform
            or not platform_channel_id
            or not account_identity
        ):
            raise ValidationFailureError("V2_PROVIDER_DESTINATION_AUTHORITY_REQUIRED")
        authority_hash = semantic_hash(content)
        return {
            "destination_mode": "VERIFIED_PUBLISH_DESTINATION",
            "platform": platform,
            "platform_channel_id": platform_channel_id,
            "account_identity": account_identity,
            "destination_status": "VERIFIED",
            "destination_handle": payload.get("channel_handle"),
            "destination_binding_ref": (
                f"destination-binding://legacy/{authority_hash}"
            ),
            "destination_binding_hash": authority_hash,
            "destination_model_hash": semantic_hash(payload),
            "destination_authority_hash": authority_hash,
            "publish_execution_allowed": True,
            "automatic_publish": False,
        }
    try:
        from app.services.v2_support_authority import (
            V2FinalReviewOnlyDestinationAuthority,
            V2VerifiedDestinationAuthority,
        )

        typed_authority = (
            V2FinalReviewOnlyDestinationAuthority.model_validate(authority)
            if status == "PENDING_PLATFORM_ID"
            else V2VerifiedDestinationAuthority.model_validate(authority)
        )
        typed_binding = DestinationBinding.model_validate(authority_binding)
    except ValidationError as exc:
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_BINDING_INVALID") from exc
    binding_payload = typed_binding.model_dump(mode="json")
    if typed_authority.binding != typed_binding or any(
        payload.get(key) != value for key, value in binding_payload.items()
    ):
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_BINDING_MISMATCH")
    active_binding_ref = authority.get("active_binding_ref")
    expected_ref = (
        f"destination-binding://{typed_binding.channel_key}/"
        f"v{typed_binding.binding_version}"
    )
    if (
        active_binding_ref != expected_ref
        or authority.get("destination_hash") != typed_binding.content_hash
        or authority.get("content_hash")
        != semantic_hash(
            {key: value for key, value in authority.items() if key != "content_hash"}
        )
    ):
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_AUTHORITY_MISMATCH")

    platform = typed_binding.platform.strip().upper()
    platform_channel_id = typed_binding.platform_channel_id
    account_identity = typed_binding.platform_account_ref
    handle = typed_binding.channel_handle
    automatic_publish = content.get("automatic_publish", False)
    mode = content.get("destination_mode")
    if status == "VERIFIED":
        mode = mode or "VERIFIED_PUBLISH_DESTINATION"
        if (
            mode != "VERIFIED_PUBLISH_DESTINATION"
            or content.get("publish_execution_allowed") is not True
            or typed_binding.verification_state != "VERIFIED"
            or not platform_channel_id
            or not account_identity
            or automatic_publish is not False
        ):
            raise ValidationFailureError("V2_PROVIDER_DESTINATION_NOT_VERIFIED")
    elif status == "PENDING_PLATFORM_ID":
        recovery_authority_id = str(typed_authority.controlled_recovery_authority_id)
        recovery_authority_hash = typed_authority.controlled_recovery_authority_hash
        settlement_authority_id = str(typed_authority.settlement_authority_id)
        settlement_authority_hash = typed_authority.settlement_authority_hash
        settlement_qualification_run_id = str(
            typed_authority.settlement_qualification_run_id
        )
        settlement_provenance_hash = typed_authority.settlement_provenance_hash
        exact_review_only_duplicates = {
            "destination_mode": "FINAL_REVIEW_ONLY",
            "destination_status": "PENDING_PLATFORM_ID",
            "destination_handle": handle,
            "destination_binding_hash": typed_authority.content_hash,
            "destination_model_hash": typed_binding.content_hash,
            "destination_authority_hash": typed_authority.content_hash,
            "publish_execution_allowed": False,
            "automatic_publish": False,
            "controlled_recovery_authority_id": recovery_authority_id,
            "controlled_recovery_authority_hash": recovery_authority_hash,
            "settlement_authority_id": settlement_authority_id,
            "settlement_authority_hash": settlement_authority_hash,
            "settlement_qualification_run_id": settlement_qualification_run_id,
            "settlement_provenance_hash": settlement_provenance_hash,
        }
        duplicate_mismatch = any(
            source.get(key) != expected
            for source in (content, payload)
            for key, expected in exact_review_only_duplicates.items()
            if key in source
        )
        if (
            mode != "FINAL_REVIEW_ONLY"
            or content.get("result") != "PASS_FOR_FINAL_REVIEW_ONLY"
            or content.get("publish_execution_allowed") is not False
            or automatic_publish is not False
            or authority.get("schema_version")
            != "vcos.final-review-only-destination-authority.v1"
            or authority.get("authority_mode") != "FINAL_REVIEW_ONLY"
            or authority.get("publish_policy") != "NO_PUBLISH"
            or authority.get("publish_execution_allowed") is not False
            or typed_binding.verification_state != "PENDING"
            or platform_channel_id is not None
            or account_identity is not None
            or typed_binding.credential_ref is not None
            or typed_binding.verification_timestamp is not None
            or not handle
            or duplicate_mismatch
            or content.get("controlled_recovery_authority_id", recovery_authority_id)
            != recovery_authority_id
            or content.get(
                "controlled_recovery_authority_hash", recovery_authority_hash
            )
            != recovery_authority_hash
        ):
            raise ValidationFailureError("V2_PROVIDER_FINAL_REVIEW_ONLY_INVALID")
    else:
        raise ValidationFailureError("V2_PROVIDER_DESTINATION_STATUS_INVALID")
    return {
        "destination_mode": mode,
        "platform": platform,
        "platform_channel_id": platform_channel_id,
        "account_identity": account_identity,
        "destination_status": status,
        "destination_handle": handle,
        "destination_binding_ref": active_binding_ref,
        "destination_binding_hash": authority["content_hash"],
        "destination_model_hash": typed_binding.content_hash,
        "destination_authority_hash": authority["content_hash"],
        "publish_execution_allowed": status == "VERIFIED",
        "automatic_publish": False,
        **(
            {
                "controlled_recovery_authority_id": content.get(
                    "controlled_recovery_authority_id",
                    payload.get(
                        "controlled_recovery_authority_id",
                        recovery_authority_id,
                    ),
                ),
                "controlled_recovery_authority_hash": content.get(
                    "controlled_recovery_authority_hash",
                    payload.get(
                        "controlled_recovery_authority_hash",
                        recovery_authority_hash,
                    ),
                ),
                "settlement_authority_id": settlement_authority_id,
                "settlement_authority_hash": settlement_authority_hash,
                "settlement_qualification_run_id": (
                    settlement_qualification_run_id
                ),
                "settlement_provenance_hash": settlement_provenance_hash,
            }
            if status == "PENDING_PLATFORM_ID"
            else {}
        ),
    }


def _require_verified_final_media(
    session: Session,
    project_id: uuid.UUID | None,
    final_media_id: uuid.UUID,
    *,
    expected_checksum: str | None,
    expected_archive_hash: str,
) -> None:
    media = session.get(FinalMediaRef, final_media_id)
    cloud = (
        session.get(CloudMediaRef, media.cloud_media_ref_id)
        if media is not None and media.cloud_media_ref_id is not None
        else None
    )
    lineage = (
        session.get(ArtifactVersion, media.lineage_artifact_version_id)
        if media is not None and media.lineage_artifact_version_id is not None
        else None
    )
    lineage_artifact = (
        session.get(Artifact, lineage.artifact_id) if lineage is not None else None
    )
    parsed_file_ref = urlparse(media.file_ref) if media is not None else None
    cloud_appendix = cloud.technical_appendix if cloud is not None else {}
    storage_binding_valid = bool(
        media is not None
        and cloud is not None
        and parsed_file_ref is not None
        and (
            (
                cloud.storage_provider == "GOOGLE_DRIVE"
                and parsed_file_ref.scheme == "drive"
                and parsed_file_ref.netloc == cloud.drive_file_id
            )
            or (
                cloud.storage_provider == "VCOS_LOCAL_ARCHIVE"
                and parsed_file_ref.scheme == "vcos-local-archive"
                and parsed_file_ref.netloc == str(project_id)
                and cloud.drive_file_id == f"local-{expected_checksum}"
                and cloud.web_view_link == media.file_ref
                and isinstance(cloud_appendix, dict)
                and cloud_appendix.get("readback_checksum") == expected_checksum
                and isinstance(cloud_appendix.get("archive_journal_hash"), str)
                and len(cloud_appendix["archive_journal_hash"]) == 64
            )
        )
    )
    if (
        media is None
        or project_id is None
        or media.video_project_id != project_id
        or media.checksum_sha256 != expected_checksum
        or cloud is None
        or cloud.video_project_id != project_id
        or cloud.checksum_sha256 != expected_checksum
        or cloud.upload_status != "VERIFIED"
        or cloud.verification_status != "CHECKSUM_VERIFIED"
        or not storage_binding_valid
        or cloud_appendix.get("archive_receipt_hash") != expected_archive_hash
        or lineage is None
        or lineage_artifact is None
        or lineage_artifact.artifact_type != "mr1_final_media_lineage_receipt"
        or lineage_artifact.current_version_id != lineage.id
        or lineage_artifact.status != "approved"
        or lineage.status != "approved"
        or not isinstance(lineage.content, dict)
        or lineage.content.get("schema_version") != "vcos.native-final-media-lineage.v2"
    ):
        raise ValidationFailureError("V2_PROVIDER_FINAL_MEDIA_AUTHORITY_MISMATCH")


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise ValidationFailureError(f"V2_PROVIDER_FIELD_REQUIRED:{key}")
    return result.strip()


def _required_hash(value: dict[str, Any], key: str) -> str:
    result = _required_text(value, key)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ValidationFailureError(f"V2_PROVIDER_HASH_INVALID:{key}")
    return result


def _required_uuid(value: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailureError(f"V2_PROVIDER_UUID_INVALID:{key}") from exc


def _required_mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict) or not result:
        raise ValidationFailureError(f"V2_PROVIDER_MAPPING_REQUIRED:{key}")
    return dict(result)


def _required_run_text(value: str | None, key: str) -> str:
    if value is None or not value.strip():
        raise ValidationFailureError(f"V2_PROVIDER_RUN_FIELD_REQUIRED:{key}")
    return value


def _required_run_hash(value: str | None, key: str) -> str:
    result = _required_run_text(value, key)
    if len(result) != 64:
        raise ValidationFailureError(f"V2_PROVIDER_RUN_HASH_INVALID:{key}")
    return result


def _required_run_uuid(value: uuid.UUID | None, key: str) -> uuid.UUID:
    if value is None:
        raise ValidationFailureError(f"V2_PROVIDER_RUN_UUID_REQUIRED:{key}")
    return value


__all__ = [
    "PackageBoundV2StageGateway",
    "V2AuthorizedAdapterOperation",
    "V2ArchiveGateway",
    "V2FinalReviewPresentationGateway",
    "V2MediaProviderGateway",
    "V2ProductionAdapterDescriptor",
    "V2ProductionOperationAdapter",
    "V2ProviderProductionGateway",
    "V2QualityControlGateway",
    "V2RenderGateway",
    "build_v2_provider_production_gateway",
]
