"""Deterministic recovery of stale, zero-effect pre-repair workflows.

The existing production worker owns this service.  It never replays a failed
command, mutates a package/provider/media authority, or converts a historical
workflow into a current one.  It only supersedes the narrowly proven class of
dead letters that predate effective-context sealing and have no effect or cost
evidence whatsoever.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.actor import ActorContext, ActorType
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m5 import EditorialIdeaCandidate, ProjectAdmissionDecision
from app.db.models.script_qualification import ScriptQualificationRun
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.ops import CostEvent, DeadLetterJob, OpsIncident, ProviderAttempt
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowRecoveryReceipt,
)
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash
from app.services.series_episode_reservation import EpisodeReservationAuthorityService


STALE_WORKFLOW_RECOVERY_EVENT_TYPE = "production.workflow.stale_recovery.requested"
STALE_WORKFLOW_RECOVERY_VERSION = "vcos.stale-workflow-recovery.v1"
STALE_WORKFLOW_RECOVERY_CLASSIFICATION = "STALE_PRE_REPAIR_ZERO_EFFECT_WORKFLOW"
STALE_WORKFLOW_RECOVERY_DECISION = "AUTO_SUPERSEDE_STALE_PRE_REPAIR_WORKFLOW"
STALE_WORKFLOW_ADMISSION_LINEAGE_CLOSED = "ZERO_EFFECT_ADMISSION_LINEAGE_CLOSED"
_RECOVERY_COMMAND_NAMESPACE = uuid.UUID("567475c1-7d3d-56f5-b747-c91221444582")


class StaleWorkflowRecoveryService:
    """Queue and apply the one allowed automated stale-workflow transition."""

    def __init__(self, session: Session, *, now: Any = utc_now) -> None:
        self.session = session
        self.now = now

    def enqueue_due(self) -> int:
        """Put deterministic recovery events on the existing worker outbox."""

        candidates = list(
            self.session.scalars(
                select(ProductionWorkflowRun)
                .where(
                    ProductionWorkflowRun.state == "DEAD_LETTERED",
                    ProductionWorkflowRun.current_stage == "RESEARCH",
                )
                .order_by(ProductionWorkflowRun.created_at)
                .with_for_update(skip_locked=True)
            ).all()
        )
        enqueued = 0
        for run in candidates:
            dead_letter = self._dead_letter_for(run.id)
            if dead_letter is None:
                continue
            proof = self._zero_effect_proof(run=run, dead_letter=dead_letter)
            if not self._is_stale_pre_repair_zero_effect(run, dead_letter, proof):
                continue
            payload = {
                "workflow_run_id": str(run.id),
                "dead_letter_job_id": str(dead_letter.id),
                "classification": STALE_WORKFLOW_RECOVERY_CLASSIFICATION,
                "recovery_version": STALE_WORKFLOW_RECOVERY_VERSION,
                "proof_hash": content_hash(proof),
            }
            identity = content_hash(payload)
            command_id = f"workflow-recovery:{identity}"
            existing = self.session.scalar(
                select(DomainEvent).where(DomainEvent.command_id == command_id)
            )
            if existing is not None:
                continue
            occurred_at = self.now()
            self.session.add(
                DomainEvent(
                    id=uuid.uuid5(_RECOVERY_COMMAND_NAMESPACE, command_id),
                    event_type=STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
                    event_version=1,
                    aggregate_type="production_workflow_run",
                    aggregate_id=run.id,
                    company_id=run.company_id,
                    channel_workspace_id=run.channel_workspace_id,
                    workflow_run_id=run.id,
                    correlation_id=f"workflow-recovery:{run.id}",
                    causation_id=dead_letter.domain_event_id,
                    command_id=command_id,
                    payload_hash=identity,
                    payload=payload,
                    metadata_={
                        "queue_name": "production-workflow",
                        "retry_policy": {
                            "policy_key": "stale-workflow-supersession-v1",
                            "automatic_retry_allowed": False,
                            "max_attempts": 1,
                            "provider_substitution_allowed": False,
                        },
                    },
                    attempt_count=0,
                    max_attempts=1,
                    next_attempt_at=occurred_at,
                    occurred_at=occurred_at,
                )
            )
            enqueued += 1
        self.session.flush()
        return enqueued

    def execute_event(
        self, *, event: DomainEvent, actor: ActorContext
    ) -> WorkflowRecoveryReceipt:
        """Supersede only a still-zero-effect workflow under the worker identity."""

        if (
            event.event_type != STALE_WORKFLOW_RECOVERY_EVENT_TYPE
            or event.workflow_run_id is None
            or event.aggregate_id != event.workflow_run_id
            or actor.actor_type != ActorType.SYSTEM_WORKER
            or actor.actor_role != "SYSTEM_WORKER"
            or not actor.has_permission("production.start")
        ):
            raise ValidationFailureError("STALE_WORKFLOW_RECOVERY_UNTRUSTED")
        existing = self.session.scalar(
            select(WorkflowRecoveryReceipt).where(
                WorkflowRecoveryReceipt.recovery_event_id == event.id
            )
        )
        if existing is not None:
            return existing
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == event.workflow_run_id)
            .with_for_update()
        )
        if run is None:
            raise ValidationFailureError("STALE_WORKFLOW_RECOVERY_RUN_MISSING")
        dead_letter_id = _required_uuid(event.payload, "dead_letter_job_id")
        dead_letter = self.session.scalar(
            select(DeadLetterJob)
            .where(DeadLetterJob.id == dead_letter_id)
            .with_for_update()
        )
        if dead_letter is None or dead_letter.workflow_run_id != run.id:
            raise ValidationFailureError("STALE_WORKFLOW_RECOVERY_DEAD_LETTER_MISMATCH")
        proof = self._zero_effect_proof(run=run, dead_letter=dead_letter)
        if (
            event.payload.get("classification")
            != STALE_WORKFLOW_RECOVERY_CLASSIFICATION
            or event.payload.get("recovery_version") != STALE_WORKFLOW_RECOVERY_VERSION
            or event.payload.get("proof_hash") != content_hash(proof)
            or not self._is_stale_pre_repair_zero_effect(run, dead_letter, proof)
        ):
            raise ValidationFailureError("STALE_WORKFLOW_RECOVERY_NOT_SAFE")
        incidents = list(
            self.session.scalars(
                select(OpsIncident)
                .where(
                    OpsIncident.workflow_run_id == run.id,
                    OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
                )
                .with_for_update()
            ).all()
        )
        incident = incidents[0] if len(incidents) == 1 else None
        input_payload = {
            "workflow_run_id": str(run.id),
            "workflow_key": run.workflow_key,
            "dead_letter_job_id": str(dead_letter.id),
            "dead_letter_event_id": str(dead_letter.domain_event_id),
            "failed_stage": run.current_stage,
            "failure_reason_code": dead_letter.reason_code,
            "recovery_event_id": str(event.id),
            "recovery_version": STALE_WORKFLOW_RECOVERY_VERSION,
            "proof": proof,
        }
        input_hash = content_hash(input_payload)
        receipt = self.session.scalar(
            select(WorkflowRecoveryReceipt).where(
                WorkflowRecoveryReceipt.input_hash == input_hash
            )
        )
        if receipt is not None:
            return receipt
        decision_payload = {
            "input_hash": input_hash,
            "classification": STALE_WORKFLOW_RECOVERY_CLASSIFICATION,
            "decision": STALE_WORKFLOW_RECOVERY_DECISION,
            "incident_id": str(incident.id) if incident is not None else None,
        }
        receipt = WorkflowRecoveryReceipt(
            workflow_run_id=run.id,
            dead_letter_job_id=dead_letter.id,
            incident_id=incident.id if incident is not None else None,
            recovery_event_id=event.id,
            recovery_version=STALE_WORKFLOW_RECOVERY_VERSION,
            classification=STALE_WORKFLOW_RECOVERY_CLASSIFICATION,
            decision=STALE_WORKFLOW_RECOVERY_DECISION,
            failed_stage=run.current_stage,
            failure_reason_code=dead_letter.reason_code or "UNKNOWN",
            proof=proof,
            input_hash=input_hash,
            decision_hash=content_hash(decision_payload),
            created_by="SYSTEM_WORKER",
        )
        self.session.add(receipt)
        self.session.flush()
        now = self.now()
        run.state = "SUPERSEDED"
        run.state_reason_codes = [
            STALE_WORKFLOW_RECOVERY_CLASSIFICATION,
            STALE_WORKFLOW_RECOVERY_DECISION,
        ]
        run.metadata_ = {
            **(run.metadata_ or {}),
            "stale_workflow_recovery": {
                "receipt_id": str(receipt.id),
                "decision_hash": receipt.decision_hash,
                "prior_state": "DEAD_LETTERED",
            },
        }
        run.completed_at = now
        run.last_progress_at = now
        run.projection_version += 1
        lineage_closure = self._close_zero_effect_admission_lineage(run=run)
        run.metadata_ = {
            **(run.metadata_ or {}),
            "stale_workflow_recovery": {
                **((run.metadata_ or {}).get("stale_workflow_recovery") or {}),
                "admission_lineage_closure": lineage_closure,
            },
        }
        dead_letter.replay_state = "DISCARDED"
        dead_letter.retry_eligible = False
        dead_letter.next_action = "Superseded by zero-effect stale-workflow recovery."
        dead_letter.metadata_ = {
            **(dead_letter.metadata_ or {}),
            "stale_workflow_recovery_receipt_id": str(receipt.id),
            "recovery_decision": STALE_WORKFLOW_RECOVERY_DECISION,
        }
        for item in incidents:
            item.state = "RESOLVED"
            item.resolved_at = now
            item.retry_eligible = False
            item.next_action = "Historical stale workflow superseded; do not replay."
            item.reason_codes = [
                *(item.reason_codes or []),
                STALE_WORKFLOW_RECOVERY_DECISION,
            ]
            item.resolution_evidence = {
                **(item.resolution_evidence or {}),
                "workflow_recovery_receipt_id": str(receipt.id),
                "decision_hash": receipt.decision_hash,
                "effect_proof_hash": content_hash(proof),
            }
        self.session.flush()
        return receipt

    def _close_zero_effect_admission_lineage(
        self, *, run: ProductionWorkflowRun
    ) -> dict[str, Any]:
        """Close the cadence reservation that cannot outlive a superseded run.

        This is deliberately narrower than general project cancellation: it only
        applies after this service has proved zero provider, budget, media, and
        package effects for a pre-repair workflow.  The admitted project and
        immutable admission receipt remain historical evidence; the candidate
        and publish-slot states stop claiming that production is still active.
        """

        if run.video_project_id is None or run.project_admission_decision_id is None:
            return {"state": "NOT_APPLICABLE", "reason": "ADMISSION_LINEAGE_MISSING"}
        admission = self.session.scalar(
            select(ProjectAdmissionDecision)
            .where(
                ProjectAdmissionDecision.id == run.project_admission_decision_id,
                ProjectAdmissionDecision.decision == "ADMIT",
                ProjectAdmissionDecision.admitted_video_project_id
                == run.video_project_id,
            )
            .with_for_update()
        )
        if admission is None or admission.editorial_idea_candidate_id is None:
            return {
                "state": "NOT_APPLICABLE",
                "reason": "CADENCE_CANDIDATE_LINEAGE_MISSING",
            }
        candidate = self.session.scalar(
            select(EditorialIdeaCandidate)
            .where(EditorialIdeaCandidate.id == admission.editorial_idea_candidate_id)
            .with_for_update()
        )
        if candidate is None:
            return {
                "state": "NOT_APPLICABLE",
                "reason": "CADENCE_CANDIDATE_MISSING",
            }

        candidate_closed = False
        if candidate.stage == "IN_PRODUCTION":
            candidate.stage = "REJECTED"
            candidate.reason_codes = [
                *(candidate.reason_codes or []),
                STALE_WORKFLOW_ADMISSION_LINEAGE_CLOSED,
            ]
            candidate_closed = True

        publish_slots = list(
            self.session.scalars(
                select(LongFormPublishSlot)
                .where(
                    LongFormPublishSlot.admitted_video_project_id
                    == run.video_project_id,
                    LongFormPublishSlot.reserved_candidate_id == candidate.id,
                    LongFormPublishSlot.state == "RESERVED",
                )
                .with_for_update()
            ).all()
        )
        for publish_slot in publish_slots:
            publish_slot.state = "CANCELED"

        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.production_workflow_run_id == run.id)
            .with_for_update()
        )
        abandoned_reservation = None
        if qualification is not None:
            abandoned_reservation = EpisodeReservationAuthorityService(
                self.session
            ).abandon_after_admission(
                qualification,
                reason_code="ZERO_EFFECT_WORKFLOW_ABANDONED_AFTER_ADMISSION",
            )

        return {
            "state": "CLOSED",
            "candidate_id": str(candidate.id),
            "candidate_closed": candidate_closed,
            "publish_slot_ids": [str(item.id) for item in publish_slots],
            "qualification_run_id": str(qualification.id) if qualification else None,
            "series_reservation_id": (
                str(abandoned_reservation.id) if abandoned_reservation else None
            ),
            "series_reservation_state": (
                abandoned_reservation.state if abandoned_reservation else None
            ),
        }

    def _dead_letter_for(self, workflow_run_id: uuid.UUID) -> DeadLetterJob | None:
        return self.session.scalar(
            select(DeadLetterJob)
            .where(DeadLetterJob.workflow_run_id == workflow_run_id)
            .order_by(DeadLetterJob.last_failed_at.desc())
            .limit(1)
        )

    def _zero_effect_proof(
        self, *, run: ProductionWorkflowRun, dead_letter: DeadLetterJob
    ) -> dict[str, Any]:
        project = self.session.get(VideoProject, run.video_project_id)
        project_id = run.video_project_id
        v2_effect_rows, invocation_count = self.session.execute(
            select(
                func.count(V2ProductionEffectLedger.id),
                func.coalesce(
                    func.sum(V2ProductionEffectLedger.effect_invocation_count), 0
                ),
            ).where(V2ProductionEffectLedger.workflow_run_id == run.id)
        ).one()
        provider_attempts = int(
            self.session.scalar(
                select(func.count(ProviderAttempt.id)).where(
                    or_(
                        ProviderAttempt.target_id == run.id,
                        ProviderAttempt.target_id == project_id,
                    )
                )
            )
            or 0
        )
        return {
            "schema_version": "vcos.stale-workflow-zero-effect-proof.v1",
            "workflow_state": run.state,
            "workflow_stage": run.current_stage,
            "dead_letter_reason_code": dead_letter.reason_code,
            "dead_letter_fail_count": dead_letter.fail_count,
            "effective_runtime_context_absent": bool(
                project is not None and project.effective_context_snapshot_id is None
            ),
            "package_absent": run.production_package_artifact_version_id is None,
            "readiness_absent": run.production_readiness_receipt_artifact_version_id
            is None,
            "render_absent": run.render_output_ref is None,
            "archive_absent": run.archive_object_ref is None,
            "final_media_absent": run.final_media_ref_id is None,
            "final_review_absent": run.final_review_candidate_id is None,
            "v2_effect_rows": int(v2_effect_rows or 0),
            "effect_invocation_count": int(invocation_count or 0),
            "provider_attempt_count": provider_attempts,
            "budget_reservation_count": self._count(
                MR1MonthlyBudgetReservation,
                MR1MonthlyBudgetReservation.video_project_id == project_id,
            ),
            "budget_settlement_count": self._count(
                CostEvent,
                or_(
                    CostEvent.cost_scope_id == project_id,
                    CostEvent.provider_run_ref == str(run.id),
                ),
            ),
            "cloud_media_count": self._count(
                CloudMediaRef, CloudMediaRef.video_project_id == project_id
            ),
            "final_media_count": self._count(
                FinalMediaRef, FinalMediaRef.video_project_id == project_id
            ),
        }

    def _count(self, model: Any, predicate: Any) -> int:
        return int(
            self.session.scalar(select(func.count(model.id)).where(predicate)) or 0
        )

    @staticmethod
    def _is_stale_pre_repair_zero_effect(
        run: ProductionWorkflowRun,
        dead_letter: DeadLetterJob,
        proof: dict[str, Any],
    ) -> bool:
        required = {
            "effective_runtime_context_absent": True,
            "package_absent": True,
            "readiness_absent": True,
            "render_absent": True,
            "archive_absent": True,
            "final_media_absent": True,
            "final_review_absent": True,
            "v2_effect_rows": 0,
            "effect_invocation_count": 0,
            "provider_attempt_count": 0,
            "budget_reservation_count": 0,
            "budget_settlement_count": 0,
            "cloud_media_count": 0,
            "final_media_count": 0,
        }
        return (
            run.state == "DEAD_LETTERED"
            and run.current_stage == "RESEARCH"
            and dead_letter.replay_state == "REPLAYABLE"
            and dead_letter.retry_eligible is True
            and dead_letter.reason_code == "STAGE_RETRY_EXHAUSTED"
            and all(proof.get(key) == value for key, value in required.items())
        )


def _required_uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailureError(
            f"STALE_WORKFLOW_RECOVERY_{key.upper()}_INVALID"
        ) from exc


__all__ = [
    "STALE_WORKFLOW_RECOVERY_EVENT_TYPE",
    "STALE_WORKFLOW_RECOVERY_VERSION",
    "StaleWorkflowRecoveryService",
]
