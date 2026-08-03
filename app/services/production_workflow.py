"""Thin, durable, lane-aware production workflow coordinator.

The coordinator sequences trusted handlers and projects exact immutable
authorities.  It intentionally contains no research, admission, packaging,
rendering, QC, archive, or publishing business rules.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Select, select, text
from sqlalchemy.orm import Session

from app.contracts.production_workflow import (
    ProductionWorkflowCancel,
    ProductionWorkflowList,
    ProductionWorkflowProjectStart,
    ProductionWorkflowRead,
    ProductionWorkflowResume,
    ProductionWorkflowStage,
    ProductionWorkflowStart,
    ProductionWorkflowState,
    WorkflowAuthorityRefs,
    WorkflowCommandReceiptRead,
    WorkflowEffectState,
    WorkflowFailureClassification,
    WorkflowStageEventPayload,
    WorkflowStageResult,
)
from app.contracts.production_publish import FinalReviewCandidateCreateV2
from app.contracts.vcos_v2 import PlanningSourceType, ProductionLane
from app.core.actor import ActorContext, ActorType
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailureError,
)
from app.core.time import utc_now
from app.db.models.channel import ChannelWorkspace
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m5 import ProjectAdmissionDecision
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.company_access import require_company_permission
from app.services.production_package import (
    PRODUCTION_PACKAGE_ARTIFACT_TYPE,
    PRODUCTION_READINESS_ARTIFACT_TYPE,
    ProductionPackageService,
    ProductionReadinessService,
)
from app.services.production_publish import ProductionPublishService


WORKFLOW_EVENT_TYPE = "production.workflow.stage.requested"
WORKFLOW_EVENT_VERSION = 1
WORKFLOW_AGGREGATE_TYPE = "production_workflow_run"
_DURABLE_WORKER_ACTOR_ID = uuid.UUID("95428dc2-b989-5a1c-8f49-8dd64e99f00e")
WORKFLOW_COMMAND_NAMESPACE = uuid.UUID("405e3478-32c3-5c88-b231-226757d0fd70")
WORKFLOW_CORRELATION_PREFIX = "production-workflow"
WORKFLOW_HANDLER_VERSION = "production-workflow.v1"

TERMINAL_WORKFLOW_STATES = frozenset(
    {
        ProductionWorkflowState.FINAL_REVIEW_READY.value,
        ProductionWorkflowState.CANCELED.value,
        ProductionWorkflowState.FAILED_TERMINAL.value,
        ProductionWorkflowState.DEAD_LETTERED.value,
        ProductionWorkflowState.SUPERSEDED.value,
    }
)

RESUMABLE_WORKFLOW_STATES = frozenset(
    {
        ProductionWorkflowState.BLOCKED.value,
        ProductionWorkflowState.RETRY_SCHEDULED.value,
        ProductionWorkflowState.PLANNING_PENDING.value,
        ProductionWorkflowState.ASSIGNMENT_READY.value,
        ProductionWorkflowState.RESEARCH_PENDING.value,
        ProductionWorkflowState.PACKAGE_PENDING.value,
        ProductionWorkflowState.READY_FOR_PRODUCTION.value,
        ProductionWorkflowState.MEDIA_PENDING.value,
        ProductionWorkflowState.RENDER_PENDING.value,
        ProductionWorkflowState.QC_PENDING.value,
        ProductionWorkflowState.ARCHIVE_PENDING.value,
    }
)

STAGE_SEQUENCE: tuple[ProductionWorkflowStage, ...] = (
    ProductionWorkflowStage.PLANNING,
    ProductionWorkflowStage.PREFLIGHT,
    ProductionWorkflowStage.ADMISSION,
    ProductionWorkflowStage.RESEARCH,
    ProductionWorkflowStage.PACKAGE,
    ProductionWorkflowStage.READINESS,
    ProductionWorkflowStage.MEDIA,
    ProductionWorkflowStage.RENDER,
    ProductionWorkflowStage.QC,
    ProductionWorkflowStage.ARCHIVE,
    ProductionWorkflowStage.FINALIZE,
)

RUNNING_STATE_BY_STAGE: Mapping[ProductionWorkflowStage, ProductionWorkflowState] = {
    ProductionWorkflowStage.PLANNING: ProductionWorkflowState.PLANNING_RUNNING,
    ProductionWorkflowStage.PREFLIGHT: ProductionWorkflowState.PLANNING_RUNNING,
    ProductionWorkflowStage.ADMISSION: ProductionWorkflowState.PLANNING_RUNNING,
    ProductionWorkflowStage.RESEARCH: ProductionWorkflowState.RESEARCH_RUNNING,
    ProductionWorkflowStage.PACKAGE: ProductionWorkflowState.PACKAGE_RUNNING,
    ProductionWorkflowStage.READINESS: ProductionWorkflowState.PACKAGE_RUNNING,
    ProductionWorkflowStage.MEDIA: ProductionWorkflowState.MEDIA_RUNNING,
    ProductionWorkflowStage.RENDER: ProductionWorkflowState.RENDER_RUNNING,
    ProductionWorkflowStage.QC: ProductionWorkflowState.QC_RUNNING,
    ProductionWorkflowStage.ARCHIVE: ProductionWorkflowState.ARCHIVE_RUNNING,
    ProductionWorkflowStage.FINALIZE: ProductionWorkflowState.ARCHIVE_RUNNING,
}

PENDING_STATE_BY_NEXT_STAGE: Mapping[
    ProductionWorkflowStage, ProductionWorkflowState
] = {
    ProductionWorkflowStage.PREFLIGHT: ProductionWorkflowState.PLANNING_PENDING,
    ProductionWorkflowStage.ADMISSION: ProductionWorkflowState.PLANNING_PENDING,
    ProductionWorkflowStage.RESEARCH: ProductionWorkflowState.RESEARCH_PENDING,
    ProductionWorkflowStage.PACKAGE: ProductionWorkflowState.PACKAGE_PENDING,
    ProductionWorkflowStage.READINESS: ProductionWorkflowState.PACKAGE_PENDING,
    ProductionWorkflowStage.MEDIA: ProductionWorkflowState.MEDIA_PENDING,
    ProductionWorkflowStage.RENDER: ProductionWorkflowState.RENDER_PENDING,
    ProductionWorkflowStage.QC: ProductionWorkflowState.QC_PENDING,
    ProductionWorkflowStage.ARCHIVE: ProductionWorkflowState.ARCHIVE_PENDING,
    ProductionWorkflowStage.FINALIZE: ProductionWorkflowState.ARCHIVE_PENDING,
}

AUTHORITY_FIELD_NAMES = tuple(WorkflowAuthorityRefs.model_fields)
_STAGE_OUTPUT_AUTHORITY_FIELDS: Mapping[
    ProductionWorkflowStage,
    frozenset[str],
] = {
    ProductionWorkflowStage.PLANNING: frozenset(
        {
            "video_project_id",
            "project_admission_decision_id",
            "project_admission_decision_hash",
        }
    ),
    ProductionWorkflowStage.PREFLIGHT: frozenset(
        {
            "video_project_id",
            "project_admission_decision_id",
            "project_admission_decision_hash",
        }
    ),
    ProductionWorkflowStage.ADMISSION: frozenset(
        {
            "video_project_id",
            "project_admission_decision_id",
            "project_admission_decision_hash",
        }
    ),
    ProductionWorkflowStage.RESEARCH: frozenset(),
    ProductionWorkflowStage.PACKAGE: frozenset(
        {
            "production_package_artifact_version_id",
            "production_package_hash",
        }
    ),
    ProductionWorkflowStage.READINESS: frozenset(
        {
            "production_readiness_receipt_artifact_version_id",
            "production_readiness_receipt_hash",
        }
    ),
    ProductionWorkflowStage.MEDIA: frozenset(
        {
            "canonical_media_timeline_ref",
            "canonical_media_timeline_hash",
        }
    ),
    ProductionWorkflowStage.RENDER: frozenset(
        {
            "native_render_plan_ref",
            "native_render_plan_hash",
            "render_output_ref",
            "render_output_checksum",
        }
    ),
    ProductionWorkflowStage.QC: frozenset(
        {
            "technical_qc_receipt_ref",
            "technical_qc_receipt_hash",
            "creative_qc_receipt_ref",
            "creative_qc_receipt_hash",
        }
    ),
    ProductionWorkflowStage.ARCHIVE: frozenset(
        {
            "archive_receipt_ref",
            "archive_receipt_hash",
            "archive_object_ref",
            "archive_verification_state",
            "final_media_ref_id",
            "final_media_ref_hash",
            "destination_binding_id",
            "destination_binding_fingerprint",
            "destination_binding",
        }
    ),
    ProductionWorkflowStage.FINALIZE: frozenset(
        {
            "final_review_candidate_id",
            "final_review_candidate_artifact_version_id",
            "final_review_candidate_hash",
        }
    ),
}


class WorkflowStageError(Exception):
    """Normalized failure emitted by a trusted stage handler."""

    def __init__(
        self,
        *,
        classification: WorkflowFailureClassification,
        error_code: str,
        summary: str,
        incident_type: str | None = None,
        retry_eligible: bool | None = None,
        learning_excluded: bool = True,
        operator_visible_blocker: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.classification = classification
        self.error_code = error_code[:160]
        self.summary = summary[:4000]
        self.incident_type = incident_type
        self.retry_eligible = (
            classification
            in {
                WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
                WorkflowFailureClassification.POLICY_AUTHORIZED_LOCAL_REPAIR,
            }
            if retry_eligible is None
            else retry_eligible
        )
        self.learning_excluded = learning_excluded
        self.operator_visible_blocker = (operator_visible_blocker or self.summary)[
            :4000
        ]


class WorkflowExecutionExpired(WorkflowStageError):
    def __init__(self) -> None:
        super().__init__(
            classification=WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
            error_code="WORKFLOW_MAX_EXECUTION_EXCEEDED",
            summary="stage exceeded its bounded execution window",
            incident_type="WORKER_LEASE_EXPIRED",
        )


@dataclass(frozen=True, slots=True)
class WorkflowStageContext:
    """Trusted execution context; never constructed from an HTTP body."""

    session: Session
    actor: ActorContext
    run: ProductionWorkflowRun
    event: DomainEvent
    command_id: str
    input_hash: str
    execution_started_at: datetime
    execution_deadline: datetime
    heartbeat: Callable[[], None]

    def ensure_active(self) -> None:
        if self.run.state == ProductionWorkflowState.CANCELED.value:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
                error_code="WORKFLOW_CANCELED",
                summary="workflow was canceled before the stage completed",
                retry_eligible=False,
            )
        if utc_now() >= self.execution_deadline:
            raise WorkflowExecutionExpired()


@runtime_checkable
class ProductionStageHandler(Protocol):
    key: str
    version: str

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Execute or reconcile one deterministic command."""


@dataclass(frozen=True, slots=True)
class CallableProductionStageHandler:
    key: str
    version: str
    function: Callable[[WorkflowStageContext], WorkflowStageResult]

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        return self.function(context)


class ProductionStageHandlerRegistry:
    """Explicit registry of trusted, lane-qualified application handlers."""

    def __init__(
        self, handlers: Iterable[ProductionStageHandler] | None = None
    ) -> None:
        self._handlers: dict[str, ProductionStageHandler] = {}
        for handler in handlers or ():
            self.register(handler)

    def register(self, handler: ProductionStageHandler) -> None:
        if not isinstance(handler, ProductionStageHandler):
            raise TypeError("handler does not implement ProductionStageHandler")
        if not handler.key or not handler.version:
            raise ValueError("handler key and version are required")
        if handler.key in self._handlers:
            raise ValueError(f"duplicate workflow handler: {handler.key}")
        self._handlers[handler.key] = handler

    def replace(self, handler: ProductionStageHandler) -> None:
        """Replace one known recovery adapter with a trusted domain handler."""

        if not isinstance(handler, ProductionStageHandler):
            raise TypeError("handler does not implement ProductionStageHandler")
        if not handler.key or not handler.version:
            raise ValueError("handler key and version are required")
        if handler.key not in self._handlers:
            raise ValueError(f"workflow handler is not registered: {handler.key}")
        self._handlers[handler.key] = handler

    def require(self, handler_key: str) -> ProductionStageHandler:
        try:
            return self._handlers[handler_key]
        except KeyError as exc:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
                error_code="WORKFLOW_HANDLER_NOT_CONFIGURED",
                summary=f"trusted workflow handler is not configured: {handler_key}",
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
            ) from exc

    def keys(self) -> frozenset[str]:
        return frozenset(self._handlers)


@dataclass(frozen=True, slots=True)
class PostReadinessProductionGatewayDescriptor:
    """Auditable declaration for an approved post-readiness producer.

    The repository deliberately does not infer production eligibility from a
    callable.  A configured gateway must declare the safe execution boundary
    explicitly before it can replace the recovery-only handlers.
    """

    gateway_id: str
    version: str
    supported_lanes: frozenset[ProductionLane]
    production_eligible: bool
    fixture_only: bool
    invokes_mr1: bool
    paid_provider_calls: bool
    automatic_publish: bool

    def __post_init__(self) -> None:
        safe_identifier_characters = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        )
        if (
            not self.gateway_id
            or len(self.gateway_id) > 24
            or any(
                character not in safe_identifier_characters
                for character in self.gateway_id
            )
        ):
            raise ValueError("PRODUCTION_GATEWAY_ID_INVALID")
        if (
            not self.version
            or len(self.version) > 24
            or any(
                character not in safe_identifier_characters
                for character in self.version
            )
        ):
            raise ValueError("PRODUCTION_GATEWAY_VERSION_INVALID")
        if self.supported_lanes != frozenset({ProductionLane.LONG_FORM}):
            raise ValueError("PRODUCTION_GATEWAY_LANE_INVALID")
        if not self.production_eligible:
            raise ValueError("PRODUCTION_GATEWAY_ELIGIBILITY_REQUIRED")
        # ``paid_provider_calls`` declares capability, not blanket authority.
        # A paid-capable adapter is allowed here because every concrete effect
        # is re-authorized against the immutable package provider plan and
        # budget scope before invocation.  Fixtures, MR1, and publishing remain
        # structurally forbidden capabilities for a Phase 4 gateway.
        forbidden_flags = {
            "fixture_only": self.fixture_only,
            "invokes_mr1": self.invokes_mr1,
            "automatic_publish": self.automatic_publish,
        }
        enabled = sorted(name for name, value in forbidden_flags.items() if value)
        if enabled:
            raise ValueError(
                "PRODUCTION_GATEWAY_FORBIDDEN_CAPABILITY:" + ",".join(enabled)
            )


@runtime_checkable
class PostReadinessProductionGateway(Protocol):
    """Trusted application boundary for real media through verified archive.

    Implementations use ``context.command_id`` as their external idempotency
    key.  The coordinator transaction persists their exact authority receipt;
    implementations must reconcile the same command after a crash instead of
    repeating an external effect.
    """

    descriptor: PostReadinessProductionGatewayDescriptor

    def produce_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Produce the canonical media timeline authority."""

    def render_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Produce the approved native plan and exact render output."""

    def run_quality_control(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Produce exact passing technical and creative QC authorities."""

    def archive_media(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Verify Drive archive and persist the canonical FinalMediaRef."""

    def build_final_review_candidate(
        self, context: WorkflowStageContext
    ) -> FinalReviewCandidateCreateV2:
        """Build the exact non-public candidate input from frozen authorities."""


@dataclass(frozen=True, slots=True)
class PreReadinessProductionGatewayDescriptor:
    """Auditable declaration for trusted support/package/readiness producers."""

    gateway_id: str
    version: str
    supported_lanes: frozenset[ProductionLane]
    production_eligible: bool
    fixture_only: bool
    invokes_mr1: bool
    paid_provider_calls: bool
    automatic_publish: bool

    def __post_init__(self) -> None:
        safe = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
        )
        if (
            not self.gateway_id
            or len(self.gateway_id) > 24
            or any(character not in safe for character in self.gateway_id)
        ):
            raise ValueError("PRE_READINESS_GATEWAY_ID_INVALID")
        if (
            not self.version
            or len(self.version) > 24
            or any(character not in safe for character in self.version)
        ):
            raise ValueError("PRE_READINESS_GATEWAY_VERSION_INVALID")
        if self.supported_lanes != frozenset({ProductionLane.LONG_FORM}):
            raise ValueError("PRE_READINESS_GATEWAY_LANE_INVALID")
        if not self.production_eligible:
            raise ValueError("PRE_READINESS_GATEWAY_ELIGIBILITY_REQUIRED")
        forbidden = {
            "fixture_only": self.fixture_only,
            "invokes_mr1": self.invokes_mr1,
            "automatic_publish": self.automatic_publish,
        }
        enabled = sorted(key for key, value in forbidden.items() if value)
        if enabled:
            raise ValueError(
                "PRE_READINESS_GATEWAY_FORBIDDEN_CAPABILITY:" + ",".join(enabled)
            )


@runtime_checkable
class PreReadinessProductionGateway(Protocol):
    """Trusted internal producer boundary; never exposed as a public writer."""

    descriptor: PreReadinessProductionGatewayDescriptor

    def produce_support(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Persist or reconcile exact support authorities."""

    def create_package(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Create or reconcile the canonical ProductionPackage v2."""

    def evaluate_readiness(self, context: WorkflowStageContext) -> WorkflowStageResult:
        """Run the canonical automated readiness evaluator."""


@dataclass(frozen=True, slots=True)
class GatewayBackedPreReadinessStageHandler:
    """Adapter from trusted package producers to durable workflow receipts."""

    key: str
    version: str
    stage: ProductionWorkflowStage
    lane: ProductionLane
    gateway: PreReadinessProductionGateway

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        context.ensure_active()
        if context.run.production_lane != self.lane.value:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_PRE_READINESS_GATEWAY_LANE_MISMATCH"
            )
        descriptor = _require_pre_readiness_gateway(self.gateway)
        if self.lane not in descriptor.supported_lanes:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code="WORKFLOW_PRE_READINESS_GATEWAY_LANE_UNSUPPORTED",
                summary=(
                    f"The trusted package producer does not support {self.lane.value}."
                ),
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
            )
        context.heartbeat()
        if self.stage == ProductionWorkflowStage.RESEARCH:
            result = self.gateway.produce_support(context)
        elif self.stage == ProductionWorkflowStage.PACKAGE:
            result = self.gateway.create_package(context)
        elif self.stage == ProductionWorkflowStage.READINESS:
            result = self.gateway.evaluate_readiness(context)
        else:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_PRE_READINESS_GATEWAY_STAGE_INVALID"
            )
        context.heartbeat()
        if not isinstance(result, WorkflowStageResult):
            result = WorkflowStageResult.model_validate(result)
        _validate_pre_readiness_gateway_result(
            run=context.run,
            stage=self.stage,
            result=result,
        )
        return result


@dataclass(frozen=True, slots=True)
class GatewayBackedPostReadinessStageHandler:
    """Adapter from an approved production gateway to durable stage receipts."""

    key: str
    version: str
    stage: ProductionWorkflowStage
    lane: ProductionLane
    gateway: PostReadinessProductionGateway

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        context.ensure_active()
        if context.run.production_lane != self.lane.value:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_PRODUCTION_GATEWAY_LANE_MISMATCH"
            )
        descriptor = _require_production_gateway(self.gateway)
        if self.lane not in descriptor.supported_lanes:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code="WORKFLOW_PRODUCTION_GATEWAY_LANE_UNSUPPORTED",
                summary=(
                    "The configured post-readiness production gateway does "
                    f"not support {self.lane.value}."
                ),
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
            )

        _require_gateway_execution_authority(context)
        context.heartbeat()
        if self.stage == ProductionWorkflowStage.MEDIA:
            result = self.gateway.produce_media(context)
        elif self.stage == ProductionWorkflowStage.RENDER:
            result = self.gateway.render_media(context)
        elif self.stage == ProductionWorkflowStage.QC:
            result = self.gateway.run_quality_control(context)
        elif self.stage == ProductionWorkflowStage.ARCHIVE:
            result = self.gateway.archive_media(context)
        elif self.stage == ProductionWorkflowStage.FINALIZE:
            result = self._finalize(context)
        else:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_PRODUCTION_GATEWAY_STAGE_INVALID"
            )
        context.heartbeat()
        if not isinstance(result, WorkflowStageResult):
            result = WorkflowStageResult.model_validate(result)
        _validate_gateway_stage_result(
            session=context.session,
            run=context.run,
            stage=self.stage,
            result=result,
        )
        return result

    def _finalize(self, context: WorkflowStageContext) -> WorkflowStageResult:
        data = self.gateway.build_final_review_candidate(context)
        if not isinstance(data, FinalReviewCandidateCreateV2):
            data = FinalReviewCandidateCreateV2.model_validate(data)
        run = context.run
        if data.workflow_run_id != run.id:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_FINAL_REVIEW_GATEWAY_RUN_MISMATCH"
            )
        destination = run.destination_binding or {}
        canonical_destination = _canonical_destination_binding(context.session, run)
        exact_destination = {
            "platform": data.target_platform,
            "platform_channel_id": data.destination_platform_channel_id,
            "account_identity": data.destination_account_identity,
        }
        if (
            destination != exact_destination
            or canonical_destination != exact_destination
        ):
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_FINAL_REVIEW_GATEWAY_DESTINATION_MISMATCH"
            )
        candidate = ProductionPublishService(
            context.session
        ).create_final_review_candidate(data)
        return WorkflowStageResult(
            result_type="final_review_candidate",
            result_id=candidate.id,
            result_ref=f"final-review-candidate://{candidate.id}",
            result_hash=candidate.candidate_hash,
            authority_refs=WorkflowAuthorityRefs(
                final_review_candidate_id=candidate.id,
                final_review_candidate_hash=candidate.candidate_hash,
            ),
            reason_codes=["FINAL_REVIEW_CANDIDATE_CREATED"],
        )


@dataclass(frozen=True, slots=True)
class ExistingV2AuthorityStageHandler:
    """Trusted adapter for already-produced v2 planning/package authorities.

    This adapter never invents support artifacts.  It lets the durable
    coordinator adopt an admitted v2 project and its exact canonical package,
    and invokes the existing deterministic readiness evaluator when needed.
    Media/render/QC/archive handlers remain separately pluggable.
    """

    key: str
    version: str
    stage: ProductionWorkflowStage

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        context.ensure_active()
        run = context.run
        if run.video_project_id is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code="WORKFLOW_TRUSTED_PLANNING_PRODUCER_REQUIRED",
                summary=(
                    "No admitted v2 project exists; a trusted lane planning "
                    "producer must be registered."
                ),
                retry_eligible=False,
            )
        project = context.session.get(VideoProject, run.video_project_id)
        admission = (
            context.session.get(
                ProjectAdmissionDecision,
                project.project_admission_decision_id,
            )
            if project is not None and project.project_admission_decision_id is not None
            else None
        )
        if (
            project is None
            or admission is None
            or getattr(project, "schema_version", "v1") != "v2"
            or project.company_id != run.company_id
            or project.channel_workspace_id != run.channel_workspace_id
            or project.production_lane != run.production_lane
            or project.planning_source_type != run.planning_source_type
            or admission.decision != "ADMIT"
            or admission.admitted_video_project_id != project.id
            or not admission.decision_hash
            or not _project_admission_lineage_matches(project, admission)
        ):
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY),
                error_code="WORKFLOW_V2_PROJECT_ADMISSION_MISMATCH",
                summary="v2 project and admission authority do not match",
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
            )
        base_refs = WorkflowAuthorityRefs(
            video_project_id=project.id,
            project_admission_decision_id=admission.id,
            project_admission_decision_hash=admission.decision_hash,
        )
        if self.stage in {
            ProductionWorkflowStage.PLANNING,
            ProductionWorkflowStage.PREFLIGHT,
            ProductionWorkflowStage.ADMISSION,
        }:
            return WorkflowStageResult(
                result_type=f"v2_{self.stage.value.lower()}_authority",
                result_id=(
                    admission.id
                    if self.stage == ProductionWorkflowStage.ADMISSION
                    else project.id
                ),
                result_hash=(
                    admission.decision_hash
                    if self.stage == ProductionWorkflowStage.ADMISSION
                    else run.planning_source_hash
                ),
                result_payload={"project_lineage": _project_lineage_payload(project)},
                authority_refs=base_refs,
                reason_codes=[f"{self.stage.value}_AUTHORITY_RECONCILED"],
                effect_state=WorkflowEffectState.RECONCILED,
            )

        package_artifact = context.session.scalars(
            select(Artifact)
            .where(
                Artifact.video_project_id == project.id,
                Artifact.artifact_type == PRODUCTION_PACKAGE_ARTIFACT_TYPE,
            )
            .order_by(Artifact.created_at.asc())
        ).first()
        package_version = (
            context.session.get(ArtifactVersion, package_artifact.current_version_id)
            if package_artifact is not None
            and package_artifact.current_version_id is not None
            else None
        )
        if package_version is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code="WORKFLOW_TRUSTED_SUPPORT_PRODUCER_REQUIRED",
                summary=(
                    "Canonical support artifacts and ProductionPackage v2 "
                    "must be produced by a trusted lane handler."
                ),
                retry_eligible=False,
            )
        package_content = ProductionPackageService(
            context.session
        ).validate_for_readiness(package_version.id)
        package_refs = base_refs.model_copy(
            update={
                "production_package_artifact_version_id": package_version.id,
                "production_package_hash": package_version.content_hash,
            }
        )
        if self.stage in {
            ProductionWorkflowStage.RESEARCH,
            ProductionWorkflowStage.PACKAGE,
        }:
            return WorkflowStageResult(
                result_type=(
                    "production_support_authorities"
                    if self.stage == ProductionWorkflowStage.RESEARCH
                    else "production_package"
                ),
                result_id=package_version.id,
                result_hash=package_version.content_hash,
                result_payload={
                    "support_ref_count": len(
                        [
                            *package_content.research_refs,
                            *package_content.source_refs,
                            *package_content.niche_market_gate_refs,
                            package_content.script_ref,
                            package_content.visual_plan_ref,
                            *package_content.thumbnail_refs,
                            package_content.metadata_ref,
                            *package_content.rights_disclosure_refs,
                            package_content.provider_execution_plan_ref,
                            package_content.budget_scope_ref,
                            package_content.destination_binding_ref,
                        ]
                    ),
                    "project_lineage": _project_lineage_payload(project),
                },
                authority_refs=package_refs,
                reason_codes=[f"{self.stage.value}_AUTHORITY_RECONCILED"],
                effect_state=WorkflowEffectState.RECONCILED,
            )
        if self.stage != ProductionWorkflowStage.READINESS:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="WORKFLOW_AUTHORITY_HANDLER_STAGE_INVALID",
                summary=f"unsupported authority adapter stage: {self.stage.value}",
                retry_eligible=False,
            )
        context.heartbeat()
        readiness = ProductionReadinessService(context.session).evaluate(
            package_artifact_version_id=package_version.id,
            created_by_user_id=project.created_by_user_id,
        )
        context.heartbeat()
        if readiness.status != "READY_FOR_PRODUCTION" or readiness.receipt is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="PRODUCTION_READINESS_BLOCKED",
                summary="canonical ProductionPackage v2 did not pass readiness",
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
                operator_visible_blocker=";".join(readiness.blocker_reason_codes)
                or "Production readiness is blocked.",
            )
        ready_refs = package_refs.model_copy(
            update={
                "production_readiness_receipt_artifact_version_id": (
                    readiness.receipt.artifact_version_id
                ),
                "production_readiness_receipt_hash": (readiness.receipt.receipt_hash),
            }
        )
        return WorkflowStageResult(
            result_type="production_readiness_receipt",
            result_id=readiness.receipt.artifact_version_id,
            result_hash=readiness.receipt.receipt_hash,
            result_payload={"project_lineage": _project_lineage_payload(project)},
            authority_refs=ready_refs,
            reason_codes=["PRODUCTION_READINESS_VERIFIED"],
        )


@dataclass(frozen=True, slots=True)
class ExistingFinalReviewAuthorityStageHandler:
    """Reconcile post-readiness stages from a canonical Phase 5 candidate.

    This is intentionally a recovery adapter, not a render or archive engine.
    It permits a durable workflow projection to catch up when the immutable
    final-review authority already exists.  When it does not exist, execution
    blocks instead of invoking MR1, running the fixture-only LPRO1 path, or
    manufacturing media/QC/archive evidence.
    """

    key: str
    version: str
    stage: ProductionWorkflowStage

    def execute(self, context: WorkflowStageContext) -> WorkflowStageResult:
        context.ensure_active()
        run = context.run
        candidate = self._candidate(context.session, run)
        if candidate is None:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE),
                error_code=(f"WORKFLOW_TRUSTED_{self.stage.value}_PRODUCER_REQUIRED"),
                summary=(
                    "No immutable final-review authority can be reconciled. "
                    "A trusted production media/render/QC/archive producer is "
                    "required; fixture-only LPRO1 and MR1 are not executed."
                ),
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
                operator_visible_blocker=(
                    "The trusted post-readiness production handler is not "
                    "available for this project."
                ),
            )
        refs = self._refs_for_stage(candidate, run)
        result_ref, result_hash = self._result_identity(candidate)
        return WorkflowStageResult(
            result_type=f"final_review_{self.stage.value.lower()}_authority",
            result_id=(
                candidate.id if self.stage == ProductionWorkflowStage.FINALIZE else None
            ),
            result_ref=result_ref,
            result_hash=result_hash,
            authority_refs=refs,
            reason_codes=[f"{self.stage.value}_AUTHORITY_RECONCILED"],
            effect_state=WorkflowEffectState.RECONCILED,
        )

    @staticmethod
    def _candidate(
        session: Session,
        run: ProductionWorkflowRun,
    ) -> FinalReviewCandidate | None:
        statement = select(FinalReviewCandidate).where(
            FinalReviewCandidate.workflow_run_id == run.id
        )
        if run.final_review_candidate_id is not None:
            statement = statement.where(
                FinalReviewCandidate.id == run.final_review_candidate_id
            )
        return session.scalar(statement)

    def _refs_for_stage(
        self,
        candidate: FinalReviewCandidate,
        run: ProductionWorkflowRun,
    ) -> WorkflowAuthorityRefs:
        if (
            candidate.company_id != run.company_id
            or candidate.channel_workspace_id != run.channel_workspace_id
            or candidate.video_project_id != run.video_project_id
            or candidate.production_lane != run.production_lane
        ):
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_FINAL_REVIEW_SCOPE_MISMATCH"
            )
        if self.stage == ProductionWorkflowStage.MEDIA:
            return WorkflowAuthorityRefs(
                canonical_media_timeline_ref=(candidate.canonical_media_timeline_ref),
                canonical_media_timeline_hash=(candidate.canonical_media_timeline_hash),
            )
        if self.stage == ProductionWorkflowStage.RENDER:
            return WorkflowAuthorityRefs(
                native_render_plan_ref=candidate.native_render_plan_ref,
                native_render_plan_hash=candidate.native_render_plan_hash,
                render_output_ref=candidate.render_output_ref,
                render_output_checksum=candidate.render_output_checksum,
            )
        if self.stage == ProductionWorkflowStage.QC:
            return WorkflowAuthorityRefs(
                technical_qc_receipt_ref=(candidate.technical_qc_receipt_ref),
                technical_qc_receipt_hash=(candidate.technical_qc_receipt_hash),
                creative_qc_receipt_ref=candidate.creative_qc_receipt_ref,
                creative_qc_receipt_hash=candidate.creative_qc_receipt_hash,
            )
        if self.stage == ProductionWorkflowStage.ARCHIVE:
            return WorkflowAuthorityRefs(
                archive_receipt_ref=candidate.archive_receipt_ref,
                archive_receipt_hash=candidate.archive_receipt_hash,
                archive_object_ref=candidate.archive_object_ref,
                archive_verification_state=(candidate.archive_verification_state),
                final_media_ref_id=candidate.final_media_ref_id,
                final_media_ref_hash=candidate.final_media_hash,
                destination_binding_id=candidate.destination_binding_id,
                destination_binding_fingerprint=(
                    candidate.destination_binding_fingerprint
                ),
                destination_binding={
                    "platform": candidate.target_platform,
                    "platform_channel_id": (candidate.destination_platform_channel_id),
                    "account_identity": (candidate.destination_account_identity),
                },
            )
        if self.stage == ProductionWorkflowStage.FINALIZE:
            return WorkflowAuthorityRefs(
                final_review_candidate_id=candidate.id,
                final_review_candidate_hash=candidate.candidate_hash,
            )
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_FINAL_AUTHORITY_HANDLER_STAGE_INVALID"
        )

    def _result_identity(self, candidate: FinalReviewCandidate) -> tuple[str, str]:
        identities = {
            ProductionWorkflowStage.MEDIA: (
                candidate.canonical_media_timeline_ref,
                candidate.canonical_media_timeline_hash,
            ),
            ProductionWorkflowStage.RENDER: (
                candidate.render_output_ref,
                candidate.render_output_checksum,
            ),
            ProductionWorkflowStage.QC: (
                candidate.creative_qc_receipt_ref,
                candidate.creative_qc_receipt_hash,
            ),
            ProductionWorkflowStage.ARCHIVE: (
                candidate.archive_object_ref,
                candidate.archive_receipt_hash,
            ),
            ProductionWorkflowStage.FINALIZE: (
                f"final-review-candidate://{candidate.id}",
                candidate.candidate_hash,
            ),
        }
        return identities[self.stage]


def _require_pre_readiness_gateway(
    gateway: PreReadinessProductionGateway,
) -> PreReadinessProductionGatewayDescriptor:
    if not isinstance(gateway, PreReadinessProductionGateway):
        raise TypeError(
            "configured gateway does not implement PreReadinessProductionGateway"
        )
    descriptor = gateway.descriptor
    if not isinstance(descriptor, PreReadinessProductionGatewayDescriptor):
        raise TypeError(
            "configured gateway descriptor must be "
            "PreReadinessProductionGatewayDescriptor"
        )
    return PreReadinessProductionGatewayDescriptor(
        gateway_id=descriptor.gateway_id,
        version=descriptor.version,
        supported_lanes=frozenset(descriptor.supported_lanes),
        production_eligible=descriptor.production_eligible,
        fixture_only=descriptor.fixture_only,
        invokes_mr1=descriptor.invokes_mr1,
        paid_provider_calls=descriptor.paid_provider_calls,
        automatic_publish=descriptor.automatic_publish,
    )


def _validate_pre_readiness_gateway_result(
    *,
    run: ProductionWorkflowRun,
    stage: ProductionWorkflowStage,
    result: WorkflowStageResult,
) -> None:
    refs = result.authority_refs
    _assert_gateway_authority_refs_are_production(refs)
    for value in (result.result_ref,):
        if isinstance(value, str) and _is_forbidden_production_ref(value):
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="WORKFLOW_FIXTURE_AUTHORITY_FORBIDDEN",
                summary=(
                    "A fixture or qualification-only result cannot be "
                    "accepted from a trusted package producer."
                ),
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
            )
    required = {
        "video_project_id",
        "project_admission_decision_id",
        "project_admission_decision_hash",
    }
    if stage in {
        ProductionWorkflowStage.PACKAGE,
        ProductionWorkflowStage.READINESS,
    }:
        required.update(
            {
                "production_package_artifact_version_id",
                "production_package_hash",
            }
        )
    if stage == ProductionWorkflowStage.READINESS:
        required.update(
            {
                "production_readiness_receipt_artifact_version_id",
                "production_readiness_receipt_hash",
            }
        )
    missing = sorted(name for name in required if getattr(refs, name) is None)
    if missing:
        raise ProductionWorkflowCoordinator._integrity_error(
            f"WORKFLOW_{stage.value}_PRODUCER_OUTPUT_INCOMPLETE:" + ",".join(missing)
        )
    if refs.video_project_id != run.video_project_id or result.result_hash is None:
        raise ProductionWorkflowCoordinator._integrity_error(
            f"WORKFLOW_{stage.value}_PRODUCER_RESULT_MISMATCH"
        )


def _require_production_gateway(
    gateway: PostReadinessProductionGateway,
) -> PostReadinessProductionGatewayDescriptor:
    if not isinstance(gateway, PostReadinessProductionGateway):
        raise TypeError(
            "configured gateway does not implement PostReadinessProductionGateway"
        )
    descriptor = gateway.descriptor
    if not isinstance(descriptor, PostReadinessProductionGatewayDescriptor):
        raise TypeError(
            "configured gateway descriptor must be "
            "PostReadinessProductionGatewayDescriptor"
        )
    # Reconstructing is intentional: it prevents an implementation from
    # supplying an unvalidated subclass or a mutated lookalike.
    return PostReadinessProductionGatewayDescriptor(
        gateway_id=descriptor.gateway_id,
        version=descriptor.version,
        supported_lanes=frozenset(descriptor.supported_lanes),
        production_eligible=descriptor.production_eligible,
        fixture_only=descriptor.fixture_only,
        invokes_mr1=descriptor.invokes_mr1,
        paid_provider_calls=descriptor.paid_provider_calls,
        automatic_publish=descriptor.automatic_publish,
    )


def _require_gateway_execution_authority(
    context: WorkflowStageContext,
) -> None:
    """Revalidate the exact provider/budget authorization before each effect."""

    run = context.run
    package_id = run.production_package_artifact_version_id
    package_hash = run.production_package_hash
    readiness_id = run.production_readiness_receipt_artifact_version_id
    readiness_hash = run.production_readiness_receipt_hash
    if any(
        value is None
        for value in (
            package_id,
            package_hash,
            readiness_id,
            readiness_hash,
        )
    ):
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_PRODUCTION_GATEWAY_READINESS_REQUIRED"
        )
    assert package_id is not None
    assert package_hash is not None
    content = ProductionPackageService(context.session).validate_for_readiness(
        package_id
    )
    readiness = ProductionPackageService(context.session)._receipt_for_package(
        package_id, package_hash
    )
    if (
        readiness is None
        or readiness.id != readiness_id
        or readiness.content_hash != readiness_hash
        or not content.readiness_evidence.provider_plan_valid
        or not content.readiness_evidence.budget_scope_valid
    ):
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_PRODUCTION_GATEWAY_AUTHORIZATION_MISMATCH"
        )

    provider_content = _require_package_support_authority(
        context.session,
        project_id=run.video_project_id,
        artifact_version_id=(content.provider_execution_plan_ref.artifact_version_id),
        expected_hash=content.provider_execution_plan_ref.content_hash,
        expected_type="provider_execution_plan",
        label="PROVIDER_PLAN",
    )
    budget_content = _require_package_support_authority(
        context.session,
        project_id=run.video_project_id,
        artifact_version_id=content.budget_scope_ref.artifact_version_id,
        expected_hash=content.budget_scope_ref.content_hash,
        expected_type="cost_estimate_snapshot",
        label="BUDGET_SCOPE",
    )
    for label, value in (
        ("PROVIDER_PLAN", provider_content),
        ("BUDGET_SCOPE", budget_content),
    ):
        if not _support_authority_is_positive(value):
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code=f"WORKFLOW_{label}_NOT_AUTHORIZED",
                summary=(
                    f"The exact package {label.lower()} does not authorize "
                    "post-readiness production."
                ),
                incident_type="CONFIG_ERROR",
                retry_eligible=False,
            )
        if _contains_forbidden_fixture_marker(value):
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code=f"WORKFLOW_{label}_FIXTURE_AUTHORITY_FORBIDDEN",
                summary=(
                    "Fixture-only execution authority cannot enter the "
                    "production gateway."
                ),
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
            )
    if context.event.attempt_count > 1:
        _require_package_bound_retry_authority(
            attempt_count=context.event.attempt_count,
            provider_content=provider_content,
            budget_content=budget_content,
        )


def _require_package_bound_retry_authority(
    *,
    attempt_count: int,
    provider_content: Mapping[str, Any],
    budget_content: Mapping[str, Any],
) -> None:
    """Require affirmative package policy and remaining budget for a retry."""

    if (
        provider_content.get("retry_authorized") is not True
        or budget_content.get("retry_authorized") is not True
    ):
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="WORKFLOW_RETRY_NOT_AUTHORIZED_BY_PACKAGE",
            summary=(
                "Both the exact provider plan and budget authority must "
                "affirmatively authorize retry."
            ),
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
        )
    try:
        provider_max_attempts = int(provider_content["max_attempts"])
        budget_max_attempts = int(budget_content["max_attempts"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="WORKFLOW_RETRY_ATTEMPT_POLICY_MISSING",
            summary=(
                "Exact provider and budget authorities must bound retry attempts."
            ),
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
        ) from exc
    if (
        provider_max_attempts < 1
        or budget_max_attempts < 1
        or attempt_count > min(provider_max_attempts, budget_max_attempts)
    ):
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="WORKFLOW_RETRY_ATTEMPT_LIMIT_EXCEEDED",
            summary="The package-authorized retry attempt limit was exceeded.",
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
        )
    try:
        remaining = Decimal(str(budget_content["remaining_budget_usd"]))
        retry_cost_raw = (
            budget_content["retry_cost_usd"]
            if "retry_cost_usd" in budget_content
            else provider_content["retry_cost_usd"]
        )
        retry_cost = Decimal(str(retry_cost_raw))
    except (
        KeyError,
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="WORKFLOW_RETRY_BUDGET_AUTHORITY_MISSING",
            summary=(
                "Exact remaining budget and retry cost are required before retry."
            ),
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
        ) from exc
    if remaining < 0 or retry_cost < 0 or remaining < retry_cost:
        raise WorkflowStageError(
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            error_code="WORKFLOW_RETRY_BUDGET_EXHAUSTED",
            summary="The exact budget authority cannot fund another attempt.",
            incident_type="CONFIG_ERROR",
            retry_eligible=False,
        )


def _require_package_support_authority(
    session: Session,
    *,
    project_id: uuid.UUID | None,
    artifact_version_id: uuid.UUID | None,
    expected_hash: str,
    expected_type: str,
    label: str,
) -> dict[str, Any]:
    version = (
        session.get(ArtifactVersion, artifact_version_id)
        if artifact_version_id is not None
        else None
    )
    artifact = (
        session.get(Artifact, version.artifact_id) if version is not None else None
    )
    if (
        version is None
        or artifact is None
        or project_id is None
        or artifact.video_project_id != project_id
        or artifact.artifact_type != expected_type
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != expected_hash
        or not isinstance(version.content, dict)
    ):
        raise ProductionWorkflowCoordinator._integrity_error(
            f"WORKFLOW_{label}_AUTHORITY_MISMATCH"
        )
    return dict(version.content)


def _support_authority_is_positive(value: Mapping[str, Any]) -> bool:
    explicit_booleans = (
        value.get("execution_authorized"),
        value.get("budget_authorized"),
        value.get("publish_execution_allowed"),
        value.get("approved"),
    )
    if any(item is False for item in explicit_booleans):
        return False
    if any(item is True for item in explicit_booleans):
        return True
    states = {
        str(value.get(key, "")).strip().upper()
        for key in (
            "result",
            "status",
            "state",
            "authorization_state",
            "budget_state",
        )
    }
    return bool(
        states
        & {
            "PASS",
            "APPROVED",
            "AUTHORIZED",
            "VERIFIED",
            "READY",
        }
    )


def _contains_forbidden_fixture_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key == "fixture_only" and item is True:
                return True
            if normalized_key in {"execution_mode", "purpose"} and (
                "FIXTURE" in str(item).upper() or "LPRO1_OFFLINE" in str(item).upper()
            ):
                return True
            if _contains_forbidden_fixture_marker(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_fixture_marker(item) for item in value)
    return False


def _canonical_destination_binding(
    session: Session,
    run: ProductionWorkflowRun,
    refs: WorkflowAuthorityRefs | None = None,
) -> dict[str, str]:
    if run.production_package_artifact_version_id is None:
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_DESTINATION_PACKAGE_REQUIRED"
        )
    package = ProductionPackageService(session).validate_for_readiness(
        run.production_package_artifact_version_id
    )
    version_id = package.destination_binding_ref.artifact_version_id
    content = _require_package_support_authority(
        session,
        project_id=run.video_project_id,
        artifact_version_id=version_id,
        expected_hash=package.destination_binding_ref.content_hash,
        expected_type="destination_binding",
        label="DESTINATION_BINDING",
    )
    wrapped = content.get("destination_binding", content.get("destination"))
    payload = wrapped if isinstance(wrapped, dict) else content
    platform = payload.get("platform")
    platform_channel_id = payload.get("platform_channel_id")
    account_identity = payload.get(
        "platform_account_ref", payload.get("account_identity")
    )
    status = str(payload.get("destination_status", payload.get("status", ""))).upper()
    verification_state = payload.get("verification_state")
    destination_binding_id = (
        refs.destination_binding_id
        if refs is not None and refs.destination_binding_id is not None
        else run.destination_binding_id
    )
    destination_binding_fingerprint = (
        refs.destination_binding_fingerprint
        if refs is not None and refs.destination_binding_fingerprint is not None
        else run.destination_binding_fingerprint
    )
    if (
        version_id is None
        or destination_binding_id != version_id
        or destination_binding_fingerprint
        != package.destination_binding_ref.content_hash
        or status != "VERIFIED"
        or (
            verification_state is not None
            and str(verification_state).upper() != "VERIFIED"
        )
        or content.get("publish_execution_allowed") is False
        or payload.get("publish_execution_allowed") is False
        or not all(
            isinstance(item, str) and item.strip()
            for item in (
                platform,
                platform_channel_id,
                account_identity,
            )
        )
    ):
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_DESTINATION_BINDING_AUTHORITY_MISMATCH"
        )
    return {
        "platform": str(platform).strip().upper(),
        "platform_channel_id": str(platform_channel_id).strip(),
        "account_identity": str(account_identity).strip(),
    }


def _validate_gateway_stage_result(
    *,
    session: Session,
    run: ProductionWorkflowRun,
    stage: ProductionWorkflowStage,
    result: WorkflowStageResult,
) -> None:
    """Reject incomplete or cross-stage production gateway assertions."""

    refs = result.authority_refs
    _assert_gateway_authority_refs_are_production(refs)

    def require_run_fields(*field_names: str) -> None:
        missing = sorted(
            field_name for field_name in field_names if getattr(run, field_name) is None
        )
        if missing:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_PRODUCTION_GATEWAY_INPUT_INCOMPLETE:" + ",".join(missing)
            )

    def require_ref_fields(*field_names: str) -> None:
        missing = sorted(
            field_name
            for field_name in field_names
            if getattr(refs, field_name) is None
        )
        if missing:
            raise ProductionWorkflowCoordinator._integrity_error(
                f"WORKFLOW_{stage.value}_GATEWAY_OUTPUT_INCOMPLETE:" + ",".join(missing)
            )

    if stage == ProductionWorkflowStage.MEDIA:
        require_run_fields(
            "production_package_artifact_version_id",
            "production_package_hash",
            "production_readiness_receipt_artifact_version_id",
            "production_readiness_receipt_hash",
        )
        require_ref_fields(
            "canonical_media_timeline_ref",
            "canonical_media_timeline_hash",
        )
        expected_identity = (
            refs.canonical_media_timeline_ref,
            refs.canonical_media_timeline_hash,
        )
    elif stage == ProductionWorkflowStage.RENDER:
        require_run_fields(
            "canonical_media_timeline_ref",
            "canonical_media_timeline_hash",
        )
        require_ref_fields(
            "native_render_plan_ref",
            "native_render_plan_hash",
            "render_output_ref",
            "render_output_checksum",
        )
        expected_identity = (
            refs.render_output_ref,
            refs.render_output_checksum,
        )
    elif stage == ProductionWorkflowStage.QC:
        require_run_fields(
            "native_render_plan_ref",
            "native_render_plan_hash",
            "render_output_ref",
            "render_output_checksum",
        )
        require_ref_fields(
            "technical_qc_receipt_ref",
            "technical_qc_receipt_hash",
            "creative_qc_receipt_ref",
            "creative_qc_receipt_hash",
        )
        expected_identity = (
            refs.creative_qc_receipt_ref,
            refs.creative_qc_receipt_hash,
        )
    elif stage == ProductionWorkflowStage.ARCHIVE:
        require_run_fields(
            "technical_qc_receipt_ref",
            "technical_qc_receipt_hash",
            "creative_qc_receipt_ref",
            "creative_qc_receipt_hash",
        )
        require_ref_fields(
            "archive_receipt_ref",
            "archive_receipt_hash",
            "archive_object_ref",
            "archive_verification_state",
            "final_media_ref_id",
            "final_media_ref_hash",
            "destination_binding_id",
            "destination_binding_fingerprint",
            "destination_binding",
        )
        if refs.archive_verification_state != "VERIFIED":
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_ARCHIVE_GATEWAY_NOT_VERIFIED"
            )
        destination = refs.destination_binding or {}
        required_destination_fields = {
            "platform",
            "platform_channel_id",
            "account_identity",
        }
        if (
            set(destination) != required_destination_fields
            or any(
                not isinstance(destination.get(field_name), str)
                or not destination[field_name].strip()
                for field_name in required_destination_fields
            )
            or {
                **destination,
                "platform": destination["platform"].strip().upper(),
                "platform_channel_id": (destination["platform_channel_id"].strip()),
                "account_identity": destination["account_identity"].strip(),
            }
            != _canonical_destination_binding(session, run, refs)
        ):
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_ARCHIVE_DESTINATION_BINDING_INVALID"
            )
        if result.result_id != refs.final_media_ref_id:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_ARCHIVE_FINAL_MEDIA_IDENTITY_MISMATCH"
            )
        expected_identity = (
            refs.archive_object_ref,
            refs.archive_receipt_hash,
        )
    elif stage == ProductionWorkflowStage.FINALIZE:
        require_run_fields(
            "archive_receipt_ref",
            "archive_receipt_hash",
            "archive_object_ref",
            "archive_verification_state",
            "final_media_ref_id",
            "final_media_ref_hash",
            "destination_binding_id",
            "destination_binding_fingerprint",
        )
        require_ref_fields(
            "final_review_candidate_id",
            "final_review_candidate_hash",
        )
        if result.result_id != refs.final_review_candidate_id:
            raise ProductionWorkflowCoordinator._integrity_error(
                "WORKFLOW_FINAL_REVIEW_CANDIDATE_IDENTITY_MISMATCH"
            )
        expected_identity = (
            f"final-review-candidate://{refs.final_review_candidate_id}",
            refs.final_review_candidate_hash,
        )
    else:
        raise ProductionWorkflowCoordinator._integrity_error(
            "WORKFLOW_PRODUCTION_GATEWAY_STAGE_INVALID"
        )

    if (result.result_ref, result.result_hash) != expected_identity:
        raise ProductionWorkflowCoordinator._integrity_error(
            f"WORKFLOW_{stage.value}_GATEWAY_RESULT_IDENTITY_MISMATCH"
        )


def _assert_gateway_authority_refs_are_production(
    refs: WorkflowAuthorityRefs,
) -> None:
    for field_name, value in refs.model_dump(mode="python").items():
        if not isinstance(value, str):
            continue
        if _is_forbidden_production_ref(value):
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="WORKFLOW_FIXTURE_AUTHORITY_FORBIDDEN",
                summary=(
                    "A fixture or qualification-only reference cannot be "
                    f"accepted as production authority: {field_name}."
                ),
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
            )


def _is_forbidden_production_ref(value: str) -> bool:
    forbidden_markers = (
        "fixture://",
        "provider://fake",
        "qualification://",
        "lpro1_offline_fixture",
    )
    lowered = value.strip().lower()
    return any(marker in lowered for marker in forbidden_markers)


def build_default_stage_handler_registry(
    *,
    pre_readiness_gateway: PreReadinessProductionGateway | None = None,
    post_readiness_gateway: PostReadinessProductionGateway | None = None,
) -> ProductionStageHandlerRegistry:
    """Build fail-closed v2 authority/recovery handlers for every stage.

    Planning/admission adopt existing canonical v2 authorities.  Trusted
    support/package/readiness and post-readiness gateways are invoked when
    configured; either section otherwise remains a recovery-only adopter.
    Neither path executes MR1, fixture-only LPRO1, or automatic publishing.
    """

    pre_descriptor = (
        _require_pre_readiness_gateway(pre_readiness_gateway)
        if pre_readiness_gateway is not None
        else None
    )
    descriptor = (
        _require_production_gateway(post_readiness_gateway)
        if post_readiness_gateway is not None
        else None
    )
    handlers: list[ProductionStageHandler] = []
    lane = ProductionLane.LONG_FORM
    for stage in (
        ProductionWorkflowStage.PLANNING,
        ProductionWorkflowStage.PREFLIGHT,
        ProductionWorkflowStage.ADMISSION,
    ):
        handlers.append(
            ExistingV2AuthorityStageHandler(
                key=handler_key_for(lane, stage),
                version=WORKFLOW_HANDLER_VERSION,
                stage=stage,
            )
        )
    for stage in (
        ProductionWorkflowStage.RESEARCH,
        ProductionWorkflowStage.PACKAGE,
        ProductionWorkflowStage.READINESS,
    ):
        if (
            pre_readiness_gateway is not None
            and pre_descriptor is not None
            and lane in pre_descriptor.supported_lanes
        ):
            handlers.append(
                GatewayBackedPreReadinessStageHandler(
                    key=handler_key_for(lane, stage),
                    version=(
                        f"{WORKFLOW_HANDLER_VERSION}+"
                        f"{pre_descriptor.gateway_id}@"
                        f"{pre_descriptor.version}"
                    ),
                    stage=stage,
                    lane=lane,
                    gateway=pre_readiness_gateway,
                )
            )
        else:
            handlers.append(
                ExistingV2AuthorityStageHandler(
                    key=handler_key_for(lane, stage),
                    version=WORKFLOW_HANDLER_VERSION,
                    stage=stage,
                )
            )
    for stage in (
        ProductionWorkflowStage.MEDIA,
        ProductionWorkflowStage.RENDER,
        ProductionWorkflowStage.QC,
        ProductionWorkflowStage.ARCHIVE,
        ProductionWorkflowStage.FINALIZE,
    ):
        if (
            post_readiness_gateway is not None
            and descriptor is not None
            and lane in descriptor.supported_lanes
        ):
            handlers.append(
                GatewayBackedPostReadinessStageHandler(
                    key=handler_key_for(lane, stage),
                    version=(
                        f"{WORKFLOW_HANDLER_VERSION}+"
                        f"{descriptor.gateway_id}@{descriptor.version}"
                    ),
                    stage=stage,
                    lane=lane,
                    gateway=post_readiness_gateway,
                )
            )
        else:
            handlers.append(
                ExistingFinalReviewAuthorityStageHandler(
                    key=handler_key_for(lane, stage),
                    version=WORKFLOW_HANDLER_VERSION,
                    stage=stage,
                )
            )
    return ProductionStageHandlerRegistry(handlers)


class ProductionWorkflowCoordinator:
    """Sequence trusted domain handlers with deterministic outbox commands."""

    def __init__(
        self,
        session: Session,
        *,
        handlers: ProductionStageHandlerRegistry | None = None,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session = session
        self.handlers = handlers or ProductionStageHandlerRegistry()
        self.now = now

    def start(
        self,
        *,
        data: ProductionWorkflowStart,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.start",
            company_id=data.company_id,
        )
        return self._start_authorized(data=data, actor=actor)

    def _start_authorized(
        self,
        *,
        data: ProductionWorkflowStart,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        self._validate_scope(data)
        semantic_input = _start_semantic_payload(data)
        start_input_hash = semantic_hash(semantic_input)
        workflow_key = semantic_hash(
            {
                "company_id": str(data.company_id),
                "channel_workspace_id": str(data.channel_workspace_id),
                "planning_source_type": data.planning_source_type.value,
                "planning_source_id": str(data.planning_source_id),
                "production_lane": data.production_lane.value,
            }
        )
        self._advisory_lock(workflow_key)
        existing = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.workflow_key == workflow_key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if existing is not None:
            if existing.start_input_hash != start_input_hash:
                raise ConflictError("PRODUCTION_WORKFLOW_START_IDENTITY_CONFLICT")
            return self._read(existing)

        now = self.now()
        run = ProductionWorkflowRun(
            company_id=data.company_id,
            channel_workspace_id=data.channel_workspace_id,
            video_project_id=data.video_project_id,
            production_lane=data.production_lane.value,
            planning_source_type=data.planning_source_type.value,
            planning_source_id=data.planning_source_id,
            planning_source_hash=data.planning_source_hash,
            workflow_key=workflow_key,
            start_input_hash=start_input_hash,
            state=ProductionWorkflowState.PLANNING_PENDING.value,
            current_stage=ProductionWorkflowStage.PLANNING.value,
            state_reason_codes=["WORKFLOW_STARTED"],
            started_at=now,
            last_progress_at=now,
            metadata_={
                "schema_version": "production-workflow.v1",
                "requested_by_actor_id": str(actor.actor_id),
                "requested_by_actor_type": actor.actor_type.value,
                "idempotency_key": data.idempotency_key,
                "max_attempts": data.max_attempts,
            },
        )
        self.session.add(run)
        self.session.flush()
        self._schedule_stage(
            run,
            ProductionWorkflowStage.PLANNING,
            max_attempts=data.max_attempts,
        )
        self.session.flush()
        return self._read(run)

    def start_from_project(
        self,
        *,
        video_project_id: uuid.UUID,
        company_id: uuid.UUID,
        data: ProductionWorkflowProjectStart,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        """Derive the complete workflow start identity from v2 DB authority."""

        require_company_permission(
            self.session,
            actor=actor,
            permission="production.start",
            company_id=company_id,
        )
        return self._start_from_project_authorized(
            video_project_id=video_project_id,
            company_id=company_id,
            data=data,
            actor=actor,
        )

    def start_from_project_system(
        self,
        *,
        video_project_id: uuid.UUID,
        company_id: uuid.UUID,
        data: ProductionWorkflowProjectStart,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        """Internal cadence/worker start using the allowlisted durable identity."""

        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_id != _DURABLE_WORKER_ACTOR_ID
            or actor.actor_role != "SYSTEM_WORKER"
            or actor.operator_user_id is not None
            or not actor.has_permission("production.start")
        ):
            raise ForbiddenError("TRUSTED_DURABLE_WORKER_REQUIRED")
        return self._start_from_project_authorized(
            video_project_id=video_project_id,
            company_id=company_id,
            data=data,
            actor=actor,
        )

    def _start_from_project_authorized(
        self,
        *,
        video_project_id: uuid.UUID,
        company_id: uuid.UUID,
        data: ProductionWorkflowProjectStart,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        """Derive a long-form workflow identity from exact admitted authority."""

        project = self.session.get(VideoProject, video_project_id)
        if (
            project is None
            or project.company_id != company_id
            or getattr(project, "schema_version", "v1") != "v2"
            or project.project_admission_decision_id is None
            or project.production_lane != ProductionLane.LONG_FORM.value
            or project.planning_source_type != PlanningSourceType.LONG_FORM_PLAN.value
        ):
            raise NotFoundError(f"v2 video project not found: {video_project_id}")
        admission = self.session.get(
            ProjectAdmissionDecision, project.project_admission_decision_id
        )
        if (
            admission is None
            or admission.schema_version != "v2"
            or admission.decision != "ADMIT"
            or admission.admitted_video_project_id != project.id
            or not admission.decision_hash
            or admission.company_id != project.company_id
            or admission.channel_workspace_id != project.channel_workspace_id
            or admission.production_lane != project.production_lane
            or admission.planning_source_type != project.planning_source_type
            or admission.production_lane != ProductionLane.LONG_FORM.value
            or admission.planning_source_type != PlanningSourceType.LONG_FORM_PLAN.value
        ):
            raise ValidationFailureError(
                "WORKFLOW_PROJECT_ADMISSION_AUTHORITY_MISMATCH"
            )
        effective = (
            self.session.get(
                EffectiveChannelRuntimeContextSnapshot,
                project.effective_context_snapshot_id,
            )
            if project.effective_context_snapshot_id is not None
            else None
        )
        if (
            effective is None
            or effective.video_project_id != project.id
            or effective.company_id != project.company_id
            or effective.channel_workspace_id != project.channel_workspace_id
            or effective.channel_profile_version_id
            != project.channel_profile_version_id
            or effective.compiled_policy_snapshot_id != project.policy_snapshot_id
            or effective.compile_status != "PASS"
            or not effective.context_hash
        ):
            # Every entry point must fail before its first PLANNING command is
            # scheduled.  The cadence path compiles this snapshot immediately
            # after admission; this coordinator guard closes direct/API starts
            # as well, so a RESEARCH command can never recover a missing or
            # mismatched runtime authority later.
            raise ValidationFailureError("WORKFLOW_EFFECTIVE_CONTEXT_NOT_PASS")
        source_type = PlanningSourceType.LONG_FORM_PLAN
        source_id = admission.editorial_calendar_slot_id
        if source_id is None:
            raise ValidationFailureError("WORKFLOW_PROJECT_PLANNING_SOURCE_MISSING")
        source_hash = semantic_hash(
            {
                "planning_source_type": source_type.value,
                "planning_source_id": str(source_id),
                "project_admission_decision_id": str(admission.id),
                "project_admission_decision_hash": admission.decision_hash,
            }
        )
        return self._start_authorized(
            data=ProductionWorkflowStart(
                company_id=project.company_id,
                channel_workspace_id=project.channel_workspace_id,
                production_lane=ProductionLane.LONG_FORM,
                planning_source_type=source_type,
                planning_source_id=source_id,
                planning_source_hash=source_hash,
                video_project_id=project.id,
                max_attempts=data.max_attempts,
                idempotency_key=data.idempotency_key,
            ),
            actor=actor,
        )

    def get(
        self,
        *,
        workflow_run_id: uuid.UUID,
        company_id: uuid.UUID,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=company_id,
        )
        return self._read(self._require_run(workflow_run_id, company_id=company_id))

    def list(
        self,
        *,
        company_id: uuid.UUID,
        actor: ActorContext,
        view: str = "active",
        limit: int = 100,
        stale_before: datetime | None = None,
    ) -> ProductionWorkflowList:
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=company_id,
        )
        if limit < 1 or limit > 500:
            raise ValidationFailureError("WORKFLOW_LIST_LIMIT_INVALID")
        statement: Select[tuple[ProductionWorkflowRun]] = select(
            ProductionWorkflowRun
        ).where(
            ProductionWorkflowRun.company_id == company_id,
            ProductionWorkflowRun.production_lane == ProductionLane.LONG_FORM.value,
            ProductionWorkflowRun.planning_source_type
            == PlanningSourceType.LONG_FORM_PLAN.value,
        )
        if view == "active":
            statement = statement.where(
                ProductionWorkflowRun.state.not_in(TERMINAL_WORKFLOW_STATES)
            )
        elif view == "blocked":
            statement = statement.where(
                ProductionWorkflowRun.state.in_(
                    {
                        ProductionWorkflowState.BLOCKED.value,
                        ProductionWorkflowState.DEAD_LETTERED.value,
                        ProductionWorkflowState.FAILED_TERMINAL.value,
                    }
                )
            )
        elif view == "stuck":
            if stale_before is None:
                stale_before = self.now() - timedelta(minutes=15)
            statement = statement.where(
                ProductionWorkflowRun.state.not_in(TERMINAL_WORKFLOW_STATES),
                ProductionWorkflowRun.last_progress_at < stale_before,
            )
        elif view != "all":
            raise ValidationFailureError("WORKFLOW_LIST_VIEW_INVALID")
        rows = self.session.scalars(
            statement.order_by(
                ProductionWorkflowRun.last_progress_at.asc(),
                ProductionWorkflowRun.created_at.asc(),
            ).limit(limit)
        ).all()
        items = [self._read(row) for row in rows]
        return ProductionWorkflowList(items=items, count=len(items))

    def resume(
        self,
        *,
        workflow_run_id: uuid.UUID,
        company_id: uuid.UUID,
        data: ProductionWorkflowResume,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.start",
            company_id=company_id,
        )
        run = self._lock_run(workflow_run_id, company_id=company_id)
        if run.state == ProductionWorkflowState.CANCELED.value:
            raise ConflictError("CANCELED_WORKFLOW_CANNOT_RESUME")
        if run.state in {
            ProductionWorkflowState.FINAL_REVIEW_READY.value,
            ProductionWorkflowState.FAILED_TERMINAL.value,
            ProductionWorkflowState.DEAD_LETTERED.value,
        }:
            raise ConflictError("WORKFLOW_NOT_RESUMABLE")
        self._reconcile_locked(run)
        if run.state == ProductionWorkflowState.FINAL_REVIEW_READY.value:
            return self._read(run)
        if run.state not in RESUMABLE_WORKFLOW_STATES:
            raise ConflictError("WORKFLOW_STAGE_ALREADY_RUNNING")
        stage = ProductionWorkflowStage(run.current_stage)
        event = self._event_for_stage(run.id, stage)
        now = self.now()
        if event is None:
            self._schedule_stage(
                run,
                stage,
                max_attempts=int((run.metadata_ or {}).get("max_attempts", 5)),
            )
        elif event.dead_lettered_at is not None:
            raise ConflictError("DEAD_LETTER_RETRY_ENDPOINT_REQUIRED")
        elif event.delivered_at is None and event.published_at is None:
            if event.lease_owner is not None and (
                event.lease_expires_at is None or event.lease_expires_at > now
            ):
                raise ConflictError("WORKFLOW_STAGE_ALREADY_LEASED")
            event.next_attempt_at = now
            event.lease_owner = None
            event.lease_expires_at = None
            event.heartbeat_at = None
            event.last_error_code = None
            event.last_error_summary = None
        else:
            receipt = self._receipt_for_event(event.id)
            if receipt is None:
                event.delivered_at = None
                event.published_at = None
                event.next_attempt_at = now
                event.lease_owner = None
                event.lease_expires_at = None
                event.heartbeat_at = None
            else:
                self._advance_after_receipt(run, receipt)
        if run.state not in TERMINAL_WORKFLOW_STATES:
            run.state = _pending_state_for_stage(stage).value
            run.state_reason_codes = [data.reason_code]
            run.last_progress_at = now
            run.projection_version += 1
        self.session.flush()
        return self._read(run)

    def cancel(
        self,
        *,
        workflow_run_id: uuid.UUID,
        company_id: uuid.UUID,
        data: ProductionWorkflowCancel,
        actor: ActorContext,
    ) -> tuple[ProductionWorkflowRead, list[DomainEvent]]:
        """Cancel future work and return leased events whose effects are uncertain."""

        require_company_permission(
            self.session,
            actor=actor,
            permission="production.cancel",
            company_id=company_id,
        )
        run = self._lock_run(workflow_run_id, company_id=company_id)
        if run.state == ProductionWorkflowState.FINAL_REVIEW_READY.value:
            raise ConflictError("FINAL_REVIEW_READY_WORKFLOW_CANNOT_CANCEL")
        if run.state == ProductionWorkflowState.CANCELED.value:
            return self._read(run), []
        now = self.now()
        run.cancellation_requested_at = now
        run.cancellation_requested_by_user_id = actor.actor_id
        run.cancellation_reason = data.reason
        run.canceled_at = now
        run.state = ProductionWorkflowState.CANCELED.value
        run.state_reason_codes = ["OPERATOR_CANCELED"]
        run.completed_at = now
        run.last_progress_at = now
        run.projection_version += 1

        uncertain: list[DomainEvent] = []
        events = self.session.scalars(
            select(DomainEvent)
            .where(DomainEvent.workflow_run_id == run.id)
            .where(DomainEvent.delivered_at.is_(None))
            .where(DomainEvent.published_at.is_(None))
            .where(DomainEvent.dead_lettered_at.is_(None))
            .with_for_update()
        ).all()
        for event in events:
            metadata = dict(event.metadata_ or {})
            metadata["cancellation_requested_at"] = now.isoformat()
            metadata["cancellation_reason"] = data.reason
            event.metadata_ = metadata
            if event.lease_owner is not None and (
                event.lease_expires_at is None or event.lease_expires_at > now
            ):
                uncertain.append(event)
                continue
            event.delivered_at = now
            event.published_at = now
            event.next_attempt_at = None
            event.lease_owner = None
            event.lease_expires_at = None
            event.heartbeat_at = None
            event.last_error_code = "WORKFLOW_CANCELED"
            event.last_error_summary = (
                "event suppressed because the workflow was canceled"
            )
        self.session.flush()
        return self._read(run), uncertain

    def execute_event(
        self,
        *,
        event: DomainEvent,
        actor: ActorContext,
        heartbeat: Callable[[], None],
        max_execution_seconds: int,
    ) -> WorkflowCommandReceipt:
        """Run one claimed command or reuse its immutable receipt."""

        self._require_system_worker(actor)
        if max_execution_seconds < 1:
            raise ValueError("max_execution_seconds must be positive")
        payload = self._validate_stage_event(event)
        run = self._lock_run(
            payload.workflow_run_id,
            company_id=event.company_id,
        )
        if run.state == ProductionWorkflowState.CANCELED.value:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
                error_code="WORKFLOW_CANCELED",
                summary="canceled workflow cannot execute another stage",
                retry_eligible=False,
            )
        if run.production_lane != payload.production_lane.value:
            raise self._integrity_error("WORKFLOW_EVENT_LANE_MISMATCH")
        if run.current_stage != payload.stage.value:
            existing = self._receipt_for_event(event.id)
            if existing is not None:
                self._validate_existing_receipt(existing, event, payload)
                return existing
            raise self._integrity_error("WORKFLOW_EVENT_STAGE_MISMATCH")

        expected_handler_key = handler_key_for(payload.production_lane, payload.stage)
        if payload.handler_key != expected_handler_key:
            raise self._integrity_error("WORKFLOW_HANDLER_LANE_MISMATCH")
        existing = self._receipt_for_event(event.id)
        if existing is not None:
            self._validate_existing_receipt(existing, event, payload)
            self._apply_authority_refs(
                run,
                WorkflowAuthorityRefs.model_validate(existing.authority_refs),
            )
            self._advance_after_receipt(run, existing)
            return existing

        expected_input_hash = self._stage_input_hash(run, payload.stage)
        if payload.input_hash != expected_input_hash:
            raise self._integrity_error("WORKFLOW_STAGE_INPUT_HASH_MISMATCH")
        command_id = event.command_id
        if not command_id:
            raise self._integrity_error("WORKFLOW_COMMAND_ID_REQUIRED")
        expected_command_id = command_id_for(run.id, payload.stage)
        if command_id != expected_command_id:
            raise self._integrity_error("WORKFLOW_COMMAND_ID_MISMATCH")
        lease_owner = event.lease_owner
        if not lease_owner:
            raise self._integrity_error("WORKFLOW_EVENT_LEASE_OWNER_REQUIRED")
        event_attempt_count = event.attempt_count
        event_lease_generation = int((event.metadata_ or {}).get("lease_generation", 0))

        handler = self.handlers.require(payload.handler_key)
        now = self.now()
        run.state = RUNNING_STATE_BY_STAGE[payload.stage].value
        run.state_reason_codes = [f"{payload.stage.value}_STARTED"]
        run.last_progress_at = now
        run.projection_version += 1
        execution_projection_version = run.projection_version
        input_authority_snapshot = self._authority_refs(run)
        self.session.flush()

        context = WorkflowStageContext(
            session=self.session,
            actor=actor,
            run=run,
            event=event,
            command_id=command_id,
            input_hash=payload.input_hash,
            execution_started_at=now,
            execution_deadline=now + timedelta(seconds=max_execution_seconds),
            heartbeat=heartbeat,
        )
        context.ensure_active()
        result = handler.execute(context)
        run, event = self._revalidate_stage_execution_boundary(
            event_id=event.id,
            payload=payload,
            command_id=command_id,
            lease_owner=lease_owner,
            event_attempt_count=event_attempt_count,
            event_lease_generation=event_lease_generation,
            execution_projection_version=execution_projection_version,
            input_authority_snapshot=input_authority_snapshot,
        )
        refreshed_context = WorkflowStageContext(
            session=self.session,
            actor=actor,
            run=run,
            event=event,
            command_id=command_id,
            input_hash=payload.input_hash,
            execution_started_at=now,
            execution_deadline=context.execution_deadline,
            heartbeat=heartbeat,
        )
        refreshed_context.ensure_active()
        if not isinstance(result, WorkflowStageResult):
            result = WorkflowStageResult.model_validate(result)
        _assert_no_sensitive_payload(result.result_payload)
        completed_at = self.now()
        receipt = WorkflowCommandReceipt(
            workflow_run_id=run.id,
            domain_event_id=event.id,
            command_id=command_id,
            stage=payload.stage.value,
            handler_key=handler.key,
            handler_version=handler.version,
            input_hash=payload.input_hash,
            effect_state=result.effect_state.value,
            result_type=result.result_type,
            result_id=result.result_id,
            result_ref=result.result_ref,
            result_hash=result.result_hash,
            result_payload=_jsonable(result.result_payload),
            authority_refs=result.authority_refs.model_dump(
                mode="json", exclude_none=True
            ),
            started_at=now,
            completed_at=completed_at,
        )
        self.session.add(receipt)
        self._apply_authority_refs(run, result.authority_refs)
        self.session.flush()
        self._advance_after_receipt(run, receipt, reason_codes=result.reason_codes)
        self.session.flush()
        return receipt

    def reconcile(
        self,
        *,
        workflow_run_id: uuid.UUID,
        company_id: uuid.UUID,
        actor: ActorContext,
    ) -> ProductionWorkflowRead:
        self._require_system_worker(actor)
        run = self._lock_run(workflow_run_id, company_id=company_id)
        self._reconcile_locked(run)
        self.session.flush()
        return self._read(run)

    def receipt_read(
        self, receipt: WorkflowCommandReceipt
    ) -> WorkflowCommandReceiptRead:
        return WorkflowCommandReceiptRead(
            id=receipt.id,
            workflow_run_id=receipt.workflow_run_id,
            domain_event_id=receipt.domain_event_id,
            command_id=receipt.command_id,
            stage=receipt.stage,
            handler_key=receipt.handler_key,
            handler_version=receipt.handler_version,
            input_hash=receipt.input_hash,
            effect_state=receipt.effect_state,
            result_type=receipt.result_type,
            result_id=receipt.result_id,
            result_ref=receipt.result_ref,
            result_hash=receipt.result_hash,
            result_payload=receipt.result_payload or {},
            authority_refs=WorkflowAuthorityRefs.model_validate(
                receipt.authority_refs or {}
            ),
            started_at=receipt.started_at,
            completed_at=receipt.completed_at,
            created_at=receipt.created_at,
        )

    def _reconcile_locked(self, run: ProductionWorkflowRun) -> None:
        receipts = self.session.scalars(
            select(WorkflowCommandReceipt)
            .where(WorkflowCommandReceipt.workflow_run_id == run.id)
            .order_by(
                WorkflowCommandReceipt.completed_at.asc(),
                WorkflowCommandReceipt.created_at.asc(),
            )
        ).all()
        merged = WorkflowAuthorityRefs()
        for receipt in receipts:
            refs = WorkflowAuthorityRefs.model_validate(receipt.authority_refs or {})
            merged = _merge_authority_contracts(merged, refs)
        if run.video_project_id is not None:
            discovered = self._discover_ready_package(run)
            merged = _merge_authority_contracts(merged, discovered)
        self._replace_projection_from_exact_refs(run, merged)
        if receipts:
            latest = receipts[-1]
            latest_stage = ProductionWorkflowStage(latest.stage)
            if latest_stage == ProductionWorkflowStage.FINALIZE:
                self._require_final_review_authorities(run)
                run.state = ProductionWorkflowState.FINAL_REVIEW_READY.value
                run.completed_at = latest.completed_at
            elif run.state not in TERMINAL_WORKFLOW_STATES:
                next_stage = _next_stage(latest_stage)
                if next_stage is not None:
                    run.current_stage = next_stage.value
                    run.state = _pending_state_for_stage(next_stage).value
        run.last_progress_at = self.now()
        run.projection_version += 1

    def _discover_ready_package(
        self, run: ProductionWorkflowRun
    ) -> WorkflowAuthorityRefs:
        try:
            package, content = ProductionPackageService(
                self.session
            ).require_ready_projection_authority(project_id=run.video_project_id)
        except (NotFoundError, ValidationFailureError):
            return WorkflowAuthorityRefs()
        receipt = ProductionPackageService(self.session)._receipt_for_package(
            package.id, package.content_hash
        )
        if receipt is None:
            return WorkflowAuthorityRefs()
        return WorkflowAuthorityRefs(
            video_project_id=run.video_project_id,
            project_admission_decision_id=content.project_admission_decision_id,
            project_admission_decision_hash=content.project_admission_decision_hash,
            production_package_artifact_version_id=package.id,
            production_package_hash=package.content_hash,
            production_readiness_receipt_artifact_version_id=receipt.id,
            production_readiness_receipt_hash=receipt.content_hash,
        )

    def _replace_projection_from_exact_refs(
        self, run: ProductionWorkflowRun, refs: WorkflowAuthorityRefs
    ) -> None:
        self._validate_authority_refs(run, refs)
        for field_name in AUTHORITY_FIELD_NAMES:
            value = getattr(refs, field_name)
            if value is not None:
                setattr(run, field_name, value)

    def _apply_authority_refs(
        self, run: ProductionWorkflowRun, refs: WorkflowAuthorityRefs
    ) -> None:
        self._validate_authority_refs(run, refs)
        for field_name in AUTHORITY_FIELD_NAMES:
            value = getattr(refs, field_name)
            if value is None:
                continue
            current = getattr(run, field_name)
            if field_name == "destination_binding":
                current = current or None
            if current is not None and current != value:
                raise self._integrity_error(f"WORKFLOW_AUTHORITY_DRIFT:{field_name}")
            setattr(run, field_name, value)

    def _validate_authority_refs(
        self, run: ProductionWorkflowRun, refs: WorkflowAuthorityRefs
    ) -> None:
        project_id = refs.video_project_id or run.video_project_id
        if refs.video_project_id is not None:
            project = self.session.get(VideoProject, refs.video_project_id)
            if (
                project is None
                or project.company_id != run.company_id
                or project.channel_workspace_id != run.channel_workspace_id
                or getattr(project, "schema_version", "v1") != "v2"
                or project.production_lane != run.production_lane
                or project.planning_source_type != run.planning_source_type
            ):
                raise self._integrity_error("WORKFLOW_PROJECT_AUTHORITY_MISMATCH")
        if refs.project_admission_decision_id is not None:
            admission = self.session.get(
                ProjectAdmissionDecision, refs.project_admission_decision_id
            )
            if (
                admission is None
                or admission.schema_version != "v2"
                or admission.decision != "ADMIT"
                or admission.decision_hash != refs.project_admission_decision_hash
                or admission.company_id != run.company_id
                or admission.channel_workspace_id != run.channel_workspace_id
                or admission.production_lane != run.production_lane
                or admission.planning_source_type != run.planning_source_type
                or (
                    project_id is not None
                    and admission.admitted_video_project_id != project_id
                )
            ):
                raise self._integrity_error("WORKFLOW_ADMISSION_AUTHORITY_MISMATCH")
        if refs.production_package_artifact_version_id is not None:
            package = self._require_artifact_version(
                refs.production_package_artifact_version_id,
                refs.production_package_hash,
                artifact_type=PRODUCTION_PACKAGE_ARTIFACT_TYPE,
                project_id=project_id,
            )
            if project_id is None:
                raise self._integrity_error("WORKFLOW_PACKAGE_PROJECT_REQUIRED")
            content = ProductionPackageService(self.session).validate_for_readiness(
                package.id
            )
            if (
                content.video_project_id != project_id
                or content.production_lane.value != run.production_lane
            ):
                raise self._integrity_error("WORKFLOW_PACKAGE_AUTHORITY_MISMATCH")
        if refs.production_readiness_receipt_artifact_version_id is not None:
            readiness = self._require_artifact_version(
                refs.production_readiness_receipt_artifact_version_id,
                refs.production_readiness_receipt_hash,
                artifact_type=PRODUCTION_READINESS_ARTIFACT_TYPE,
                project_id=project_id,
            )
            package_id = (
                refs.production_package_artifact_version_id
                or run.production_package_artifact_version_id
            )
            package_hash = refs.production_package_hash or run.production_package_hash
            if package_id is None or package_hash is None:
                raise self._integrity_error("WORKFLOW_READINESS_PACKAGE_REQUIRED")
            canonical = ProductionPackageService(self.session)._receipt_for_package(
                package_id, package_hash
            )
            if canonical is None or canonical.id != readiness.id:
                raise self._integrity_error("WORKFLOW_READINESS_AUTHORITY_MISMATCH")
        self._validate_final_media_ref(run, refs, project_id)
        self._validate_final_review_candidate(run, refs)

    def _validate_final_media_ref(
        self,
        run: ProductionWorkflowRun,
        refs: WorkflowAuthorityRefs,
        project_id: uuid.UUID | None,
    ) -> None:
        if refs.final_media_ref_id is None:
            return
        media = self.session.get(FinalMediaRef, refs.final_media_ref_id)
        package_id = (
            refs.production_package_artifact_version_id
            or run.production_package_artifact_version_id
        )
        package_hash = refs.production_package_hash or run.production_package_hash
        if (
            media is None
            or media.company_id != run.company_id
            or media.channel_workspace_id != run.channel_workspace_id
            or (project_id is not None and media.video_project_id != project_id)
            or media.production_package_artifact_version_id != package_id
            or media.production_package_hash != package_hash
            or not media.checksum_sha256
            or media.cloud_media_ref_id is None
        ):
            raise self._integrity_error("WORKFLOW_FINAL_MEDIA_AUTHORITY_MISMATCH")
        expected_hash = final_media_ref_semantic_hash(media)
        if expected_hash != refs.final_media_ref_hash:
            raise self._integrity_error("WORKFLOW_FINAL_MEDIA_HASH_MISMATCH")

    def _validate_final_review_candidate(
        self, run: ProductionWorkflowRun, refs: WorkflowAuthorityRefs
    ) -> None:
        if refs.final_review_candidate_id is None:
            return
        candidate = self.session.get(
            FinalReviewCandidate, refs.final_review_candidate_id
        )
        exact = {
            "production_package_artifact_version_id": (
                refs.production_package_artifact_version_id
                or run.production_package_artifact_version_id
            ),
            "production_package_hash": (
                refs.production_package_hash or run.production_package_hash
            ),
            "production_readiness_receipt_artifact_version_id": (
                refs.production_readiness_receipt_artifact_version_id
                or run.production_readiness_receipt_artifact_version_id
            ),
            "production_readiness_receipt_hash": (
                refs.production_readiness_receipt_hash
                or run.production_readiness_receipt_hash
            ),
            "canonical_media_timeline_ref": (
                refs.canonical_media_timeline_ref or run.canonical_media_timeline_ref
            ),
            "canonical_media_timeline_hash": (
                refs.canonical_media_timeline_hash or run.canonical_media_timeline_hash
            ),
            "native_render_plan_ref": (
                refs.native_render_plan_ref or run.native_render_plan_ref
            ),
            "native_render_plan_hash": (
                refs.native_render_plan_hash or run.native_render_plan_hash
            ),
            "render_output_ref": (refs.render_output_ref or run.render_output_ref),
            "render_output_checksum": (
                refs.render_output_checksum or run.render_output_checksum
            ),
            "technical_qc_receipt_ref": (
                refs.technical_qc_receipt_ref or run.technical_qc_receipt_ref
            ),
            "technical_qc_receipt_hash": (
                refs.technical_qc_receipt_hash or run.technical_qc_receipt_hash
            ),
            "creative_qc_receipt_ref": (
                refs.creative_qc_receipt_ref or run.creative_qc_receipt_ref
            ),
            "creative_qc_receipt_hash": (
                refs.creative_qc_receipt_hash or run.creative_qc_receipt_hash
            ),
            "archive_receipt_ref": (
                refs.archive_receipt_ref or run.archive_receipt_ref
            ),
            "archive_receipt_hash": (
                refs.archive_receipt_hash or run.archive_receipt_hash
            ),
            "archive_object_ref": (refs.archive_object_ref or run.archive_object_ref),
            "final_media_ref_id": (refs.final_media_ref_id or run.final_media_ref_id),
            "final_media_hash": (refs.final_media_ref_hash or run.final_media_ref_hash),
            "destination_binding_id": (
                refs.destination_binding_id or run.destination_binding_id
            ),
            "destination_binding_fingerprint": (
                refs.destination_binding_fingerprint
                or run.destination_binding_fingerprint
            ),
        }
        if (
            candidate is None
            or candidate.workflow_run_id != run.id
            or candidate.company_id != run.company_id
            or candidate.channel_workspace_id != run.channel_workspace_id
            or (
                run.video_project_id is not None
                and candidate.video_project_id != run.video_project_id
            )
            or candidate.archive_verification_state != "VERIFIED"
            or any(
                getattr(candidate, field_name) != expected
                for field_name, expected in exact.items()
            )
            or (
                refs.final_review_candidate_hash is not None
                and candidate.candidate_hash != refs.final_review_candidate_hash
            )
        ):
            raise self._integrity_error("WORKFLOW_FINAL_REVIEW_CANDIDATE_MISMATCH")

    def _require_artifact_version(
        self,
        version_id: uuid.UUID,
        expected_hash: str | None,
        *,
        artifact_type: str,
        project_id: uuid.UUID | None,
    ) -> ArtifactVersion:
        version = self.session.get(ArtifactVersion, version_id)
        artifact = (
            self.session.get(Artifact, version.artifact_id)
            if version is not None
            else None
        )
        if (
            version is None
            or artifact is None
            or version.content_hash != expected_hash
            or artifact.artifact_type != artifact_type
            or artifact.current_version_id != version.id
            or (project_id is not None and artifact.video_project_id != project_id)
        ):
            raise self._integrity_error(
                f"WORKFLOW_ARTIFACT_AUTHORITY_MISMATCH:{artifact_type}"
            )
        return version

    def _advance_after_receipt(
        self,
        run: ProductionWorkflowRun,
        receipt: WorkflowCommandReceipt,
        *,
        reason_codes: list[str] | None = None,
    ) -> None:
        stage = ProductionWorkflowStage(receipt.stage)
        next_stage = _next_stage(stage)
        now = self.now()
        if next_stage is None:
            self._require_final_review_authorities(run)
            run.state = ProductionWorkflowState.FINAL_REVIEW_READY.value
            run.state_reason_codes = reason_codes or ["FINAL_REVIEW_READY"]
            run.completed_at = now
            run.last_progress_at = now
            run.projection_version += 1
            return
        run.current_stage = next_stage.value
        if stage == ProductionWorkflowStage.ADMISSION:
            run.state = ProductionWorkflowState.ASSIGNMENT_READY.value
        elif stage == ProductionWorkflowStage.READINESS:
            run.state = ProductionWorkflowState.READY_FOR_PRODUCTION.value
        else:
            run.state = _pending_state_for_stage(next_stage).value
        run.state_reason_codes = reason_codes or [f"{stage.value}_COMPLETED"]
        run.last_progress_at = now
        run.projection_version += 1
        self._schedule_stage(
            run,
            next_stage,
            max_attempts=int((run.metadata_ or {}).get("max_attempts", 5)),
            causation_id=receipt.domain_event_id,
        )

    def _require_final_review_authorities(self, run: ProductionWorkflowRun) -> None:
        required = {
            "video_project_id": run.video_project_id,
            "project_admission_decision_id": run.project_admission_decision_id,
            "project_admission_decision_hash": run.project_admission_decision_hash,
            "production_package_artifact_version_id": (
                run.production_package_artifact_version_id
            ),
            "production_package_hash": run.production_package_hash,
            "production_readiness_receipt_artifact_version_id": (
                run.production_readiness_receipt_artifact_version_id
            ),
            "production_readiness_receipt_hash": (
                run.production_readiness_receipt_hash
            ),
            "canonical_media_timeline_ref": run.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": run.canonical_media_timeline_hash,
            "native_render_plan_ref": run.native_render_plan_ref,
            "native_render_plan_hash": run.native_render_plan_hash,
            "render_output_ref": run.render_output_ref,
            "render_output_checksum": run.render_output_checksum,
            "technical_qc_receipt_ref": run.technical_qc_receipt_ref,
            "technical_qc_receipt_hash": run.technical_qc_receipt_hash,
            "creative_qc_receipt_ref": run.creative_qc_receipt_ref,
            "creative_qc_receipt_hash": run.creative_qc_receipt_hash,
            "archive_receipt_ref": run.archive_receipt_ref,
            "archive_receipt_hash": run.archive_receipt_hash,
            "archive_object_ref": run.archive_object_ref,
            "final_media_ref_id": run.final_media_ref_id,
            "final_media_ref_hash": run.final_media_ref_hash,
            "final_review_candidate_id": run.final_review_candidate_id,
            "final_review_candidate_hash": run.final_review_candidate_hash,
            "destination_binding_id": run.destination_binding_id,
            "destination_binding_fingerprint": (run.destination_binding_fingerprint),
        }
        missing = sorted(key for key, value in required.items() if value is None)
        if run.archive_verification_state != "VERIFIED":
            missing.append("archive_verification_state=VERIFIED")
        if missing:
            raise self._integrity_error(
                "FINAL_REVIEW_AUTHORITIES_INCOMPLETE:" + ",".join(missing)
            )
        refs = self._authority_refs(run)
        self._validate_authority_refs(run, refs)

    def _schedule_stage(
        self,
        run: ProductionWorkflowRun,
        stage: ProductionWorkflowStage,
        *,
        max_attempts: int,
        causation_id: uuid.UUID | None = None,
    ) -> DomainEvent:
        existing = self._event_for_stage(run.id, stage)
        if existing is not None:
            payload = WorkflowStageEventPayload.model_validate(existing.payload)
            if payload.input_hash != self._stage_input_hash(run, stage):
                raise self._integrity_error("WORKFLOW_EXISTING_EVENT_INPUT_DRIFT")
            return existing
        command_id = command_id_for(run.id, stage)
        payload = WorkflowStageEventPayload(
            workflow_run_id=run.id,
            production_lane=ProductionLane(run.production_lane),
            stage=stage,
            handler_key=handler_key_for(ProductionLane(run.production_lane), stage),
            input_hash=self._stage_input_hash(run, stage),
        )
        payload_dict = payload.model_dump(mode="json")
        now = self.now()
        event = DomainEvent(
            event_type=WORKFLOW_EVENT_TYPE,
            event_version=WORKFLOW_EVENT_VERSION,
            aggregate_type=WORKFLOW_AGGREGATE_TYPE,
            aggregate_id=run.id,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            workflow_run_id=run.id,
            correlation_id=f"{WORKFLOW_CORRELATION_PREFIX}:{run.id}",
            causation_id=causation_id,
            command_id=command_id,
            payload_hash=semantic_hash(payload_dict),
            payload=payload_dict,
            metadata_={
                "schema_version": "production-workflow-stage-event.v1",
                "stage": stage.value,
                "production_lane": run.production_lane,
                "max_execution_seconds": 3600,
                "retry_policy": {
                    "policy_key": "production-workflow-bounded-v1",
                    "automatic_retry_allowed": True,
                    "policy_authorized_local_repair": True,
                    "allowed_classifications": [
                        WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY.value,
                        WorkflowFailureClassification.POLICY_AUTHORIZED_LOCAL_REPAIR.value,
                    ],
                    "max_attempts": max_attempts,
                    "provider_substitution_allowed": False,
                },
            },
            attempt_count=0,
            max_attempts=max_attempts,
            next_attempt_at=now,
            occurred_at=now,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _validate_stage_event(self, event: DomainEvent) -> WorkflowStageEventPayload:
        if (
            event.event_type != WORKFLOW_EVENT_TYPE
            or event.event_version != WORKFLOW_EVENT_VERSION
            or event.aggregate_type != WORKFLOW_AGGREGATE_TYPE
            or event.workflow_run_id is None
            or event.aggregate_id != event.workflow_run_id
        ):
            raise self._integrity_error("WORKFLOW_EVENT_ENVELOPE_INVALID")
        if event.payload_hash != semantic_hash(event.payload or {}):
            raise self._integrity_error("WORKFLOW_EVENT_PAYLOAD_HASH_MISMATCH")
        try:
            payload = WorkflowStageEventPayload.model_validate(event.payload)
        except Exception as exc:
            raise self._integrity_error("WORKFLOW_EVENT_PAYLOAD_INVALID") from exc
        if payload.workflow_run_id != event.workflow_run_id:
            raise self._integrity_error("WORKFLOW_EVENT_RUN_MISMATCH")
        return payload

    def _revalidate_stage_execution_boundary(
        self,
        *,
        event_id: uuid.UUID,
        payload: WorkflowStageEventPayload,
        command_id: str,
        lease_owner: str,
        event_attempt_count: int,
        event_lease_generation: int,
        execution_projection_version: int,
        input_authority_snapshot: WorkflowAuthorityRefs,
    ) -> tuple[ProductionWorkflowRun, DomainEvent]:
        """Re-lock mutable workflow truth after a handler-side crash boundary.

        A trusted effect handler may commit its durable intent before invoking
        an external effect.  That commit necessarily releases the
        coordinator's original row lock, so cancellation or lease drift can
        occur while the effect is in flight.  No command receipt or projection
        advance is authorized until the exact run and event are refreshed and
        locked again.
        """

        self.session.flush()
        run = self._lock_run(
            payload.workflow_run_id,
            company_id=None,
        )
        if run.state == ProductionWorkflowState.CANCELED.value:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                error_code="WORKFLOW_CANCELED",
                summary="workflow was canceled before the stage completed",
                retry_eligible=False,
            )
        if (
            run.company_id is None
            or run.production_lane != payload.production_lane.value
            or run.current_stage != payload.stage.value
            or run.state != RUNNING_STATE_BY_STAGE[payload.stage].value
            or run.projection_version != execution_projection_version
        ):
            raise self._integrity_error("WORKFLOW_STAGE_PROJECTION_DRIFT")
        current_authorities = self._authority_refs(run)
        allowed_outputs = _STAGE_OUTPUT_AUTHORITY_FIELDS[payload.stage]
        if any(
            (
                current_value != input_value
                if input_value is not None
                else current_value is not None and field_name not in allowed_outputs
            )
            for field_name in AUTHORITY_FIELD_NAMES
            for input_value, current_value in (
                (
                    getattr(input_authority_snapshot, field_name),
                    getattr(current_authorities, field_name),
                ),
            )
        ):
            raise self._integrity_error("WORKFLOW_STAGE_AUTHORITY_DRIFT")

        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self.now()
        if event is None:
            raise self._integrity_error("WORKFLOW_EVENT_MISSING_AFTER_HANDLER")
        refreshed_payload = self._validate_stage_event(event)
        if (
            refreshed_payload != payload
            or event.company_id != run.company_id
            or event.workflow_run_id != run.id
            or event.command_id != command_id
            or event.attempt_count != event_attempt_count
            or int((event.metadata_ or {}).get("lease_generation", 0))
            != event_lease_generation
        ):
            raise self._integrity_error("WORKFLOW_EVENT_IDENTITY_DRIFT")
        if (
            event.lease_owner != lease_owner
            or event.lease_expires_at is None
            or event.lease_expires_at <= now
            or event.delivered_at is not None
            or event.published_at is not None
            or event.dead_lettered_at is not None
        ):
            raise self._integrity_error("WORKFLOW_EVENT_LEASE_DRIFT")
        return run, event

    def _validate_existing_receipt(
        self,
        receipt: WorkflowCommandReceipt,
        event: DomainEvent,
        payload: WorkflowStageEventPayload,
    ) -> None:
        if (
            receipt.domain_event_id != event.id
            or receipt.command_id != event.command_id
            or receipt.workflow_run_id != payload.workflow_run_id
            or receipt.stage != payload.stage.value
            or receipt.handler_key != payload.handler_key
            or receipt.input_hash != payload.input_hash
        ):
            raise self._integrity_error("WORKFLOW_COMMAND_RECEIPT_MISMATCH")

    def _stage_input_hash(
        self, run: ProductionWorkflowRun, stage: ProductionWorkflowStage
    ) -> str:
        refs = self._authority_refs(run).model_dump(mode="json", exclude_none=True)
        return semantic_hash(
            {
                "workflow_run_id": str(run.id),
                "workflow_key": run.workflow_key,
                "start_input_hash": run.start_input_hash,
                "production_lane": run.production_lane,
                "stage": stage.value,
                "authority_refs": refs,
            }
        )

    def _authority_refs(self, run: ProductionWorkflowRun) -> WorkflowAuthorityRefs:
        return WorkflowAuthorityRefs(
            **{
                field_name: getattr(run, field_name)
                for field_name in AUTHORITY_FIELD_NAMES
            }
        )

    def _event_for_stage(
        self, workflow_run_id: uuid.UUID, stage: ProductionWorkflowStage
    ) -> DomainEvent | None:
        return self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_run_id,
                DomainEvent.command_id == command_id_for(workflow_run_id, stage),
            )
        )

    def _receipt_for_event(self, event_id: uuid.UUID) -> WorkflowCommandReceipt | None:
        return self.session.scalar(
            select(WorkflowCommandReceipt).where(
                WorkflowCommandReceipt.domain_event_id == event_id
            )
        )

    def _validate_scope(self, data: ProductionWorkflowStart) -> None:
        if (
            data.production_lane != ProductionLane.LONG_FORM
            or data.planning_source_type != PlanningSourceType.LONG_FORM_PLAN
        ):
            raise ValidationFailureError("WORKFLOW_LONG_FORM_SOURCE_REQUIRED")
        channel = self.session.get(ChannelWorkspace, data.channel_workspace_id)
        if channel is None or channel.company_id != data.company_id:
            raise ValidationFailureError("WORKFLOW_CHANNEL_SCOPE_MISMATCH")
        if data.video_project_id is not None:
            project = self.session.get(VideoProject, data.video_project_id)
            if (
                project is None
                or project.company_id != data.company_id
                or project.channel_workspace_id != data.channel_workspace_id
                or getattr(project, "schema_version", "v1") != "v2"
                or project.production_lane != data.production_lane.value
                or project.planning_source_type != data.planning_source_type.value
            ):
                raise ValidationFailureError("WORKFLOW_INITIAL_PROJECT_SCOPE_MISMATCH")

    def _advisory_lock(self, workflow_key: str) -> None:
        lock_key = int.from_bytes(bytes.fromhex(workflow_key[:16]), "big", signed=True)
        self.session.execute(
            text("select pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )

    def _lock_run(
        self,
        workflow_run_id: uuid.UUID,
        *,
        company_id: uuid.UUID | None,
    ) -> ProductionWorkflowRun:
        statement = (
            select(ProductionWorkflowRun)
            .where(
                ProductionWorkflowRun.id == workflow_run_id,
                ProductionWorkflowRun.production_lane == ProductionLane.LONG_FORM.value,
                ProductionWorkflowRun.planning_source_type
                == PlanningSourceType.LONG_FORM_PLAN.value,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if company_id is not None:
            statement = statement.where(ProductionWorkflowRun.company_id == company_id)
        run = self.session.scalar(statement)
        if run is None:
            raise NotFoundError(f"production workflow not found: {workflow_run_id}")
        return run

    def _require_run(
        self, workflow_run_id: uuid.UUID, *, company_id: uuid.UUID
    ) -> ProductionWorkflowRun:
        run = self.session.scalar(
            select(ProductionWorkflowRun).where(
                ProductionWorkflowRun.id == workflow_run_id,
                ProductionWorkflowRun.company_id == company_id,
                ProductionWorkflowRun.production_lane == ProductionLane.LONG_FORM.value,
                ProductionWorkflowRun.planning_source_type
                == PlanningSourceType.LONG_FORM_PLAN.value,
            )
        )
        if run is None:
            raise NotFoundError(f"production workflow not found: {workflow_run_id}")
        return run

    def _read(self, run: ProductionWorkflowRun) -> ProductionWorkflowRead:
        return ProductionWorkflowRead(
            id=run.id,
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            video_project_id=run.video_project_id,
            uploaded_video_id=run.uploaded_video_id,
            production_lane=run.production_lane,
            planning_source_type=run.planning_source_type,
            planning_source_id=run.planning_source_id,
            planning_source_hash=run.planning_source_hash,
            workflow_key=run.workflow_key,
            start_input_hash=run.start_input_hash,
            state=run.state,
            current_stage=run.current_stage,
            state_reason_codes=run.state_reason_codes or [],
            projection_version=run.projection_version,
            authority_refs=self._authority_refs(run),
            cancellation_requested_at=run.cancellation_requested_at,
            cancellation_requested_by_user_id=(run.cancellation_requested_by_user_id),
            cancellation_reason=run.cancellation_reason,
            canceled_at=run.canceled_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            last_progress_at=run.last_progress_at,
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    @staticmethod
    def _require_human_permission(actor: ActorContext, permission: str) -> None:
        if actor.actor_type != ActorType.HUMAN_USER:
            raise ForbiddenError("HUMAN_OPERATOR_REQUIRED")
        if not actor.has_permission(permission):
            raise ForbiddenError(f"PERMISSION_REQUIRED:{permission}")

    @staticmethod
    def _require_system_worker(actor: ActorContext) -> None:
        if (
            actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or not actor.has_permission("production.workflow.execute")
        ):
            raise ForbiddenError("TRUSTED_SYSTEM_WORKER_REQUIRED")

    @staticmethod
    def _integrity_error(code: str) -> WorkflowStageError:
        return WorkflowStageError(
            classification=WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
            error_code=code,
            summary=code,
            incident_type="INTEGRITY_MISMATCH",
            retry_eligible=False,
            learning_excluded=True,
        )


def handler_key_for(lane: ProductionLane, stage: ProductionWorkflowStage) -> str:
    if lane != ProductionLane.LONG_FORM:
        raise ValueError("WORKFLOW_LANE_UNSUPPORTED")
    return f"production.{lane.value.lower()}.{stage.value.lower()}"


def command_id_for(workflow_run_id: uuid.UUID, stage: ProductionWorkflowStage) -> str:
    return str(
        uuid.uuid5(
            WORKFLOW_COMMAND_NAMESPACE,
            f"{workflow_run_id}:{stage.value}",
        )
    )


def semantic_hash(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def final_media_ref_semantic_hash(media: FinalMediaRef) -> str:
    """Return the canonical final-media content identity used by Phase 5."""

    if media.checksum_sha256 is None:
        raise ValidationFailureError("FINAL_MEDIA_CHECKSUM_REQUIRED")
    return media.checksum_sha256


def _start_semantic_payload(data: ProductionWorkflowStart) -> dict[str, Any]:
    payload = data.model_dump(mode="json")
    payload.pop("idempotency_key", None)
    return payload


def _project_admission_lineage_matches(
    project: VideoProject,
    admission: ProjectAdmissionDecision,
) -> bool:
    fields = (
        "channel_profile_version_id",
        "policy_snapshot_id",
        "planning_source_type",
        "production_lane",
        "content_mode",
        "assignment_mode",
        "series_plan_id",
        "series_run_id",
        "episode_number",
        "episode_role",
        "standalone_reason_code",
        "duration_contract",
    )
    return all(
        getattr(project, field_name) == getattr(admission, field_name)
        for field_name in fields
    )


def _project_lineage_payload(project: VideoProject) -> dict[str, Any]:
    return {
        "video_project_id": str(project.id),
        "channel_profile_version_id": str(project.channel_profile_version_id),
        "policy_snapshot_id": str(project.policy_snapshot_id),
        "planning_source_type": project.planning_source_type,
        "production_lane": project.production_lane,
        "content_mode": project.content_mode,
        "assignment_mode": project.assignment_mode,
        "series_plan_id": (
            str(project.series_plan_id) if project.series_plan_id is not None else None
        ),
        "series_run_id": (
            str(project.series_run_id) if project.series_run_id is not None else None
        ),
        "episode_number": project.episode_number,
        "episode_role": project.episode_role,
        "standalone_reason_code": project.standalone_reason_code,
    }


def _next_stage(
    stage: ProductionWorkflowStage,
) -> ProductionWorkflowStage | None:
    index = STAGE_SEQUENCE.index(stage)
    if index + 1 == len(STAGE_SEQUENCE):
        return None
    return STAGE_SEQUENCE[index + 1]


def _pending_state_for_stage(
    stage: ProductionWorkflowStage,
) -> ProductionWorkflowState:
    if stage == ProductionWorkflowStage.PLANNING:
        return ProductionWorkflowState.PLANNING_PENDING
    return PENDING_STATE_BY_NEXT_STAGE[stage]


def _merge_authority_contracts(
    left: WorkflowAuthorityRefs, right: WorkflowAuthorityRefs
) -> WorkflowAuthorityRefs:
    merged = left.model_dump(mode="python")
    for field_name in AUTHORITY_FIELD_NAMES:
        candidate = getattr(right, field_name)
        if candidate is None:
            continue
        current = merged.get(field_name)
        if field_name == "destination_binding" and not current:
            current = None
        if current is not None and current != candidate:
            raise WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY),
                error_code=f"WORKFLOW_RECEIPT_AUTHORITY_DRIFT:{field_name}",
                summary=f"immutable receipts disagree on {field_name}",
                incident_type="INTEGRITY_MISMATCH",
                retry_eligible=False,
                learning_excluded=True,
            )
        merged[field_name] = candidate
    return WorkflowAuthorityRefs.model_validate(merged)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    return value


def _assert_no_sensitive_payload(value: Any, *, path: str = "result") -> None:
    forbidden_tokens = {
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "credential",
        "private_key",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(token in normalized for token in forbidden_tokens):
                raise WorkflowStageError(
                    classification=(
                        WorkflowFailureClassification.FAIL_PERMANENT_POLICY
                    ),
                    error_code="WORKFLOW_RECEIPT_SENSITIVE_DATA_FORBIDDEN",
                    summary=f"sensitive field is forbidden at {path}.{key}",
                    incident_type="INTEGRITY_MISMATCH",
                    retry_eligible=False,
                    learning_excluded=True,
                )
            _assert_no_sensitive_payload(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_no_sensitive_payload(item, path=f"{path}[{index}]")
