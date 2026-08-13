"""Exact-lineage runner that never invokes cadence or editorial discovery."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.script_qualification import (
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.services.launch_cadence import LongFormCadenceService
from app.services.script_content_repair import ScriptContentRepairService
from app.services.script_contract_replacement import (
    OPERATOR_RECOVERY_REASON,
    OPERATOR_RECOVERY_SCHEMA,
    resolve_replacement_qualification_leaf,
)
from app.services.script_qualification import SCRIPT_QUALIFICATION_EVENT_TYPE
from app.services.script_qualification_background import (
    BACKGROUND_EVENT_TYPE,
    ScriptQualificationBackgroundService,
)
from app.services.script_writer_output_recovery import (
    V2_OWNERSHIP_FAILURE,
    ScriptWriterOutputRecoveryService,
)
from app.services.script_verifier_settlement import (
    ScriptVerifierSettlementRecoveryService,
)
from app.services.v2_narration_timing_recovery import (
    ORIGINAL_FAILURE as V2_TIMING_RECOVERY_FAILURE,
    V2NarrationTimingRecoveryService,
)
from app.services.v2_drive_archive_property_limit_recovery import (
    ORIGINAL_FAILURE as V2_DRIVE_RECOVERY_FAILURE,
    V2DriveArchivePropertyLimitRecoveryService,
)
from app.db.models.v2_effect import (
    V2DriveArchivePropertyLimitRecoveryAuthority,
    V2NarrationTimingRecoveryAuthority,
    V2ProductionEffectLedger,
)
from app.workers.production_workflow import ProductionWorkflowWorker, WorkerRunResult


@dataclass(frozen=True, slots=True)
class ScopedReplacementRunResult:
    authority_id: uuid.UUID
    qualification_id: uuid.UUID
    qualification_state: str
    workflow_id: uuid.UUID | None
    workflow_state: str | None
    worker_result: WorkerRunResult | None


class ScopedReplacementContinuationRunner:
    """Continue exactly one replacement authority without any global scan.

    Qualification polling calls its durable service by the exact run ID. Once
    workflow commands exist, the normal worker claims their named event only;
    it never schedules or reads any other queue work.
    """

    def __init__(
        self,
        session: Session,
        *,
        now,
        post_render_hold_requested: bool = True,
    ) -> None:
        self.session = session
        self.now = now
        self.post_render_hold_requested = post_render_hold_requested
        self.actor = _system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        )
        self.recovery_actor = _system_worker_actor(
            "vcos-controlled-recovery", permissions={"production.start"}
        )

    def run_once(self, *, authority_id: uuid.UUID) -> ScopedReplacementRunResult:
        authority = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(ScriptContractReplacementAuthority.id == authority_id)
            .with_for_update()
        )
        if authority is None or authority.replacement_qualification_run_id is None:
            raise ValidationFailureError("SCOPED_REPLACEMENT_AUTHORITY_MISSING")
        if not self.post_render_hold_requested and (
            authority.replacement_reason != OPERATOR_RECOVERY_REASON
            or authority.operator_recovery_schema_version != OPERATOR_RECOVERY_SCHEMA
        ):
            raise ValidationFailureError(
                "SCOPED_REPLACEMENT_FINAL_BOUNDARY_NOT_AUTHORIZED"
            )
        qualification = resolve_replacement_qualification_leaf(
            self.session, authority=authority, lock=True
        )
        if (
            qualification.state == "BLOCKED_NON_REPAIRABLE"
            and (qualification.failure_receipt or {}).get("detail")
            == V2_OWNERSHIP_FAILURE
        ):
            qualification = ScriptWriterOutputRecoveryService(
                self.session, now=self.now
            ).create_v2_ownership_normalized_recovery(
                source_qualification_run_id=qualification.id
            )
        elif qualification.state == "BLOCKED_NON_REPAIRABLE" and "HTTP 401" in str(
            (qualification.failure_receipt or {}).get("detail") or ""
        ):
            qualification = ScriptWriterOutputRecoveryService(
                self.session, now=self.now
            ).continue_after_confirmed_verifier_auth_rejection(
                source_qualification_run_id=qualification.id
            )
        elif (
            qualification.state == "BLOCKED_NON_REPAIRABLE"
            and qualification.repair_attempts == 1
            and (qualification.failure_receipt or {}).get("detail")
            == "SCRIPT_CONTENT_REPAIR_SECTION_IDENTITY_CHANGED"
        ):
            qualification = ScriptWriterOutputRecoveryService(
                self.session, now=self.now
            ).continue_after_content_repair_scope_reclassification(
                source_qualification_run_id=qualification.id
            )
        elif (
            qualification.state == "BLOCKED_NON_REPAIRABLE"
            and qualification.script_contract_version == "V2_SINGLE_SOURCE"
            and qualification.gate_policy_version == "script-qualification-policy.v2"
            and set((qualification.failure_receipt or {}).get("reason_codes") or [])
            == {
                "SCRIPT_STRUCTURAL_INTEGRITY_PASS",
                "SCRIPT_WRITER_CLAIM_SPAN_MISMATCH",
                "SCRIPT_CLAIM_GROUNDING_PASS",
                "SCRIPT_ASSIGNMENT_COVERAGE_SPAN_REUSED",
                "SCRIPT_MEMORY_GUIDANCE_PASS_EMPTY",
            }
        ):
            qualification = ScriptVerifierSettlementRecoveryService(
                self.session, now=self.now
            ).create(source_qualification_run_id=qualification.id)
        elif (
            qualification.state == "BLOCKED_NON_REPAIRABLE"
            and qualification.repair_attempts == 0
            and isinstance(qualification.result_receipts, dict)
            and (qualification.result_receipts.get("structural") or {}).get("status")
            == "PASS"
        ):
            qualification = ScriptContentRepairService(
                self.session, now=self.now
            ).authorize(source_qualification_run_id=qualification.id)

        initial_event = self.session.scalar(
            select(DomainEvent)
            .where(
                DomainEvent.aggregate_id == qualification.id,
                DomainEvent.event_type.in_(
                    {SCRIPT_QUALIFICATION_EVENT_TYPE, BACKGROUND_EVENT_TYPE}
                ),
                DomainEvent.delivered_at.is_(None),
                DomainEvent.published_at.is_(None),
                DomainEvent.dead_lettered_at.is_(None),
            )
            .order_by(DomainEvent.created_at.asc(), DomainEvent.id.asc())
            .limit(1)
        )
        qualification_event_result: WorkerRunResult | None = None
        if initial_event is not None:
            self.session.commit()
            qualification_event_result = self._run_exact_event(initial_event.id)
            self.session.expire_all()
            qualification = self._locked_qualification(authority.id)
        else:
            # Background polling after the initial command is scoped by the
            # durable qualification ID and therefore never needs an outbox
            # scan or a scheduler enqueue.
            qualification = ScriptQualificationBackgroundService(
                self.session, now=self.now
            ).execute(qualification.id)
        workflow: ProductionWorkflowRun | None = None
        if qualification.state == "QUALIFIED":
            admission, workflow = LongFormCadenceService(
                self.session, now=self.now
            ).finalize_qualified_script_run(
                script_qualification_run_id=qualification.id,
                actor=self.actor,
            )
            if admission.admitted_video_project_id != workflow.video_project_id:
                raise ValidationFailureError(
                    "SCOPED_REPLACEMENT_ADMISSION_WORKFLOW_DRIFT"
                )
            metadata = dict(workflow.metadata_ or {})
            metadata["replacement_authority_id"] = str(authority.id)
            if self.post_render_hold_requested:
                metadata.update(
                    {
                        "post_render_hold_requested": True,
                        "post_render_hold_reason": "FIRST_VIDEO_OPERATOR_RENDER_INSPECTION",
                    }
                )
            else:
                metadata.update(
                    {
                        "post_render_hold_requested": False,
                        "post_render_hold_reason": None,
                        "controlled_recovery_final_boundary": "FINAL_REVIEW_READY",
                    }
                )
            workflow.metadata_ = metadata
        elif qualification.production_workflow_run_id is not None:
            workflow = self.session.get(
                ProductionWorkflowRun, qualification.production_workflow_run_id
            )
        self.session.flush()
        self.session.commit()

        worker_result: WorkerRunResult | None = None
        timing_recovered = False
        if workflow is not None and self._is_timing_recovery_candidate(workflow.id):
            V2NarrationTimingRecoveryService(self.session).recover(
                workflow.id, self.recovery_actor
            )
            timing_recovered = True
            self.session.expire_all()
            refreshed = self.session.get(ProductionWorkflowRun, workflow.id)
            workflow = refreshed or workflow
        archive_recovered = False
        if workflow is not None and self._is_drive_archive_recovery_candidate(
            workflow.id
        ):
            V2DriveArchivePropertyLimitRecoveryService(self.session).recover(
                workflow.id, self.recovery_actor
            )
            archive_recovered = True
            self.session.expire_all()
            refreshed = self.session.get(ProductionWorkflowRun, workflow.id)
            workflow = refreshed or workflow
        if (
            workflow is not None
            and workflow.state
            not in {
                "PAUSED_AFTER_NATIVE_RENDER",
                "FINAL_REVIEW_READY",
                "BLOCKED",
                "FAILED_TERMINAL",
                "DEAD_LETTERED",
                "CANCELED",
                "SUPERSEDED",
            }
            and not timing_recovered
            and not archive_recovered
        ):
            worker_result = self._run_one_scoped_event(workflow_id=workflow.id)
            self.session.expire_all()
            refreshed = self.session.get(ProductionWorkflowRun, workflow.id)
            workflow = refreshed or workflow
        if worker_result is None:
            worker_result = qualification_event_result
        return ScopedReplacementRunResult(
            authority_id=authority.id,
            qualification_id=qualification.id,
            qualification_state=qualification.state,
            workflow_id=workflow.id if workflow is not None else None,
            workflow_state=workflow.state if workflow is not None else None,
            worker_result=worker_result,
        )

    def _is_timing_recovery_candidate(self, workflow_id: uuid.UUID) -> bool:
        workflow = self.session.get(ProductionWorkflowRun, workflow_id)
        if (
            workflow is None
            or workflow.state != "BLOCKED"
            or workflow.current_stage != "MEDIA"
        ):
            return False
        ledger = self.session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_id,
                V2ProductionEffectLedger.stage == "MEDIA",
                V2ProductionEffectLedger.state.in_({"FAILED_UNCERTAIN", "VERIFIED"}),
                V2ProductionEffectLedger.effect_invocation_count == 1,
                V2ProductionEffectLedger.adapter_key == "v2-elevenlabs-narration",
            )
        )
        if ledger is None:
            return False
        event_id = self.session.scalar(
            select(DomainEvent.id).where(
                DomainEvent.workflow_run_id == workflow_id,
                DomainEvent.command_id == ledger.command_id,
                DomainEvent.dead_lettered_at.is_not(None),
                DomainEvent.last_error_code == V2_TIMING_RECOVERY_FAILURE,
            )
        )
        if event_id is None:
            return False
        if ledger.state == "FAILED_UNCERTAIN":
            return True

        # A crash may occur after the adapter commits VERIFIED but before the
        # recovery/workflow receipts commit.  Resume only when the verified
        # ledger itself proves this exact 0076 recovery; never adopt an
        # unrelated MEDIA result.
        authority = self.session.scalar(
            select(V2NarrationTimingRecoveryAuthority).where(
                V2NarrationTimingRecoveryAuthority.workflow_run_id == workflow_id,
                V2NarrationTimingRecoveryAuthority.media_effect_ledger_id == ledger.id,
            )
        )
        existing_receipt = self.session.scalar(
            select(WorkflowCommandReceipt.id).where(
                WorkflowCommandReceipt.domain_event_id == event_id
            )
        )
        journal = dict(ledger.effect_journal or {})
        return bool(
            authority is not None
            and existing_receipt is None
            and ledger.result_type == "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE"
            and ledger.result_hash is not None
            and ledger.completed_at is not None
            and journal.get("timeline_hash") == ledger.result_hash
            and journal.get("timing_recovery_authority_id") == str(authority.id)
            and journal.get("timing_recovery_authority_hash")
            == authority.authority_hash
            and journal.get("provider_call_count") == 2
            and journal.get("tts_provider_call_count") == 1
            and journal.get("tts_retry_count") == 0
            and journal.get("forced_alignment_provider_call_count") == 1
        )

    def _is_drive_archive_recovery_candidate(self, workflow_id: uuid.UUID) -> bool:
        workflow = self.session.get(ProductionWorkflowRun, workflow_id)
        if (
            workflow is None
            or workflow.state != "BLOCKED"
            or workflow.current_stage != "ARCHIVE"
        ):
            return False
        ledger = self.session.scalar(
            select(V2ProductionEffectLedger).where(
                V2ProductionEffectLedger.workflow_run_id == workflow_id,
                V2ProductionEffectLedger.stage == "ARCHIVE",
                V2ProductionEffectLedger.state.in_({"FAILED_UNCERTAIN", "VERIFIED"}),
                V2ProductionEffectLedger.effect_invocation_count == 1,
                V2ProductionEffectLedger.adapter_key == "v2-google-drive-remote",
            )
        )
        if ledger is None:
            return False
        event = self.session.scalar(
            select(DomainEvent).where(
                DomainEvent.workflow_run_id == workflow_id,
                DomainEvent.command_id == ledger.command_id,
                DomainEvent.dead_lettered_at.is_not(None),
                DomainEvent.last_error_code == V2_DRIVE_RECOVERY_FAILURE,
            )
        )
        if event is None:
            return False
        if ledger.state == "FAILED_UNCERTAIN":
            return True

        # A crash may leave the exact archive ledger VERIFIED after the
        # recovery effect transaction but before its workflow receipt settles.
        authority = self.session.scalar(
            select(V2DriveArchivePropertyLimitRecoveryAuthority).where(
                V2DriveArchivePropertyLimitRecoveryAuthority.workflow_run_id
                == workflow_id,
                V2DriveArchivePropertyLimitRecoveryAuthority.archive_effect_ledger_id
                == ledger.id,
                V2DriveArchivePropertyLimitRecoveryAuthority.archive_domain_event_id
                == event.id,
            )
        )
        existing_receipt = self.session.scalar(
            select(WorkflowCommandReceipt.id).where(
                WorkflowCommandReceipt.domain_event_id == event.id
            )
        )
        return bool(
            authority is not None
            and existing_receipt is None
            and ledger.result_type == "V2_VERIFIED_GOOGLE_DRIVE_REMOTE_ARCHIVE"
            and ledger.result_hash is not None
            and ledger.completed_at is not None
        )

    def _locked_qualification(self, authority_id: uuid.UUID) -> ScriptQualificationRun:
        authority = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(ScriptContractReplacementAuthority.id == authority_id)
            .with_for_update()
        )
        if authority is None:
            raise ValidationFailureError("SCOPED_REPLACEMENT_QUALIFICATION_DRIFT")
        return resolve_replacement_qualification_leaf(
            self.session, authority=authority, lock=True
        )

    def _run_one_scoped_event(self, *, workflow_id: uuid.UUID) -> WorkerRunResult:
        event = self.session.scalar(
            select(DomainEvent)
            .where(
                DomainEvent.workflow_run_id == workflow_id,
                DomainEvent.delivered_at.is_(None),
                DomainEvent.published_at.is_(None),
                DomainEvent.dead_lettered_at.is_(None),
            )
            .order_by(DomainEvent.created_at.asc(), DomainEvent.id.asc())
            .limit(1)
        )
        if event is None:
            return WorkerRunResult(status="IDLE", workflow_run_id=workflow_id)
        self.session.commit()
        return self._run_exact_event(event.id)

    def _run_exact_event(self, event_id: uuid.UUID) -> WorkerRunResult:
        worker = ProductionWorkflowWorker(now=self.now)
        return worker.run_exact_event(event_id=event_id)
