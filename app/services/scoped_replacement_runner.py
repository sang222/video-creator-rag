"""Exact-lineage runner that never invokes cadence or editorial discovery."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.db.models.foundation import DomainEvent
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.script_qualification import (
    ScriptContractReplacementAuthority,
    ScriptQualificationRun,
)
from app.services.launch_cadence import LongFormCadenceService
from app.services.script_qualification import SCRIPT_QUALIFICATION_EVENT_TYPE
from app.services.script_qualification_background import ScriptQualificationBackgroundService
from app.workers.production_workflow import ProductionWorkflowWorker, WorkerRunResult


@dataclass(frozen=True, slots=True)
class ScopedReplacementRunResult:
    authority_id: uuid.UUID
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

    def __init__(self, session: Session, *, now) -> None:
        self.session = session
        self.now = now
        self.actor = _system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        )

    def run_once(
        self, *, authority_id: uuid.UUID
    ) -> ScopedReplacementRunResult:
        authority = self.session.scalar(
            select(ScriptContractReplacementAuthority)
            .where(ScriptContractReplacementAuthority.id == authority_id)
            .with_for_update()
        )
        if authority is None or authority.replacement_qualification_run_id is None:
            raise ValidationFailureError("SCOPED_REPLACEMENT_AUTHORITY_MISSING")
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(
                ScriptQualificationRun.id == authority.replacement_qualification_run_id
            )
            .with_for_update()
        )
        if qualification is None or qualification.replacement_authority_id != authority.id:
            raise ValidationFailureError("SCOPED_REPLACEMENT_QUALIFICATION_DRIFT")

        initial_event = self.session.scalar(
            select(DomainEvent)
            .where(
                DomainEvent.aggregate_id == qualification.id,
                DomainEvent.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE,
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
            admission, workflow = LongFormCadenceService(self.session, now=self.now).finalize_qualified_script_run(
                script_qualification_run_id=qualification.id,
                actor=self.actor,
            )
            if admission.admitted_video_project_id != workflow.video_project_id:
                raise ValidationFailureError("SCOPED_REPLACEMENT_ADMISSION_WORKFLOW_DRIFT")
            metadata = dict(workflow.metadata_ or {})
            metadata.update(
                {
                    "post_render_hold_requested": True,
                    "post_render_hold_reason": "FIRST_VIDEO_OPERATOR_RENDER_INSPECTION",
                    "replacement_authority_id": str(authority.id),
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
        if workflow is not None and workflow.state != "PAUSED_AFTER_NATIVE_RENDER":
            worker_result = self._run_one_scoped_event(workflow_id=workflow.id)
            self.session.expire_all()
            refreshed = self.session.get(ProductionWorkflowRun, workflow.id)
            workflow = refreshed or workflow
        if worker_result is None:
            worker_result = qualification_event_result
        return ScopedReplacementRunResult(
            authority_id=authority.id,
            qualification_state=qualification.state,
            workflow_id=workflow.id if workflow is not None else None,
            workflow_state=workflow.state if workflow is not None else None,
            worker_result=worker_result,
        )

    def _locked_qualification(
        self, authority_id: uuid.UUID
    ) -> ScriptQualificationRun:
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.replacement_authority_id == authority_id)
            .with_for_update()
        )
        if qualification is None:
            raise ValidationFailureError("SCOPED_REPLACEMENT_QUALIFICATION_DRIFT")
        return qualification

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
