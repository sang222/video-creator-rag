"""PostgreSQL durable-outbox claim, lease, retry, and dead-letter mechanics."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.contracts.production_workflow import (
    DeadLetterRetryRead,
    DeadLetterRetryRequest,
    ProductionWorkflowState,
    WorkflowFailureClassification,
    WorkflowStageEventPayload,
)
from app.core.actor import ActorContext
from app.core.errors import ConflictError, NotFoundError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.ops import DeadLetterJob, OpsIncident
from app.db.models.script_qualification import ScriptQualificationRun
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.services.company_access import require_company_permission
from app.services.cadence_events import (
    CADENCE_AGGREGATE_TYPE,
    CADENCE_EVALUATION_EVENT_TYPE,
)
from app.services.script_qualification import (
    SCRIPT_QUALIFICATION_AGGREGATE_TYPE,
    SCRIPT_QUALIFICATION_EVENT_TYPE,
)
from app.services.script_qualification_background import (
    BACKGROUND_EVENT_TYPE,
    BACKGROUND_POLL_EVENT_TYPE,
)
from app.services.long_form_analytics import (
    ANALYTICS_WINDOW_AGGREGATE_TYPE,
    ANALYTICS_WINDOW_EVENT_TYPE,
)
from app.services.production_workflow import (
    WORKFLOW_EVENT_TYPE,
    WorkflowStageError,
    command_id_for,
    semantic_hash,
)
from app.services.stale_workflow_recovery import STALE_WORKFLOW_RECOVERY_EVENT_TYPE
from app.services.youtube_delivery import DELIVERY_EVENT_TYPES


DEFAULT_QUEUE_NAME = "production-workflow"
DEFAULT_LEASE_SECONDS = 60
DEFAULT_MAX_EXECUTION_SECONDS = 3600
DEFAULT_BACKOFF_BASE_SECONDS = 5
DEFAULT_BACKOFF_CAP_SECONDS = 900

RETRYABLE_CLASSIFICATIONS = frozenset(
    {
        WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
        WorkflowFailureClassification.POLICY_AUTHORIZED_LOCAL_REPAIR,
    }
)


class OutboxLeaseLostError(RuntimeError):
    pass


class OutboxExecutionDeadlineExceeded(OutboxLeaseLostError):
    pass


@dataclass(frozen=True, slots=True)
class ClaimedWorkflowEvent:
    event_id: uuid.UUID
    workflow_run_id: uuid.UUID | None
    command_id: str
    worker_id: str
    attempt_number: int
    lease_expires_at: datetime
    execution_deadline: datetime


@dataclass(frozen=True, slots=True)
class FailureDisposition:
    event_id: uuid.UUID
    classification: WorkflowFailureClassification
    retry_scheduled: bool
    next_attempt_at: datetime | None
    dead_letter_job_id: uuid.UUID | None
    incident_id: uuid.UUID | None
    workflow_canceled: bool = False


class DurableOutboxDispatcher:
    """Claim and settle workflow events without holding a DB lock during work."""

    def __init__(
        self,
        session: Session,
        *,
        queue_name: str = DEFAULT_QUEUE_NAME,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        max_execution_seconds: int = DEFAULT_MAX_EXECUTION_SECONDS,
        backoff_base_seconds: int = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_cap_seconds: int = DEFAULT_BACKOFF_CAP_SECONDS,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        if max_execution_seconds < lease_seconds:
            raise ValueError("max_execution_seconds must be at least lease_seconds")
        if backoff_base_seconds < 1:
            raise ValueError("backoff_base_seconds must be positive")
        if backoff_cap_seconds < backoff_base_seconds:
            raise ValueError(
                "backoff_cap_seconds must be at least backoff_base_seconds"
            )
        self.session = session
        self.queue_name = queue_name
        self.lease_seconds = lease_seconds
        self.max_execution_seconds = max_execution_seconds
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_cap_seconds = backoff_cap_seconds
        self.now = now

    def claim_next(self, *, worker_id: str) -> ClaimedWorkflowEvent | None:
        """Claim one due event using ``FOR UPDATE SKIP LOCKED``."""

        _validate_worker_id(worker_id)
        # A canceled or already-exhausted event is settled in this transaction
        # and the dispatcher continues to the next due row.
        for _ in range(100):
            now = self.now()
            event = self.session.scalar(
                select(DomainEvent)
                .where(
                    or_(
                        and_(
                            DomainEvent.event_type == WORKFLOW_EVENT_TYPE,
                            DomainEvent.workflow_run_id.is_not(None),
                            DomainEvent.workflow_run_id.in_(_long_form_run_ids()),
                        ),
                        and_(
                            DomainEvent.event_type == CADENCE_EVALUATION_EVENT_TYPE,
                            DomainEvent.aggregate_type == CADENCE_AGGREGATE_TYPE,
                            DomainEvent.workflow_run_id.is_(None),
                        ),
                        and_(
                            DomainEvent.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE,
                            DomainEvent.aggregate_type == SCRIPT_QUALIFICATION_AGGREGATE_TYPE,
                            DomainEvent.workflow_run_id.is_(None),
                        ),
                        and_(
                            DomainEvent.event_type.in_((BACKGROUND_EVENT_TYPE, BACKGROUND_POLL_EVENT_TYPE)),
                            DomainEvent.aggregate_type == SCRIPT_QUALIFICATION_AGGREGATE_TYPE,
                            DomainEvent.workflow_run_id.is_(None),
                        ),
                        and_(
                            DomainEvent.event_type == ANALYTICS_WINDOW_EVENT_TYPE,
                            DomainEvent.aggregate_type
                            == ANALYTICS_WINDOW_AGGREGATE_TYPE,
                            DomainEvent.workflow_run_id.is_(None),
                        ),
                        and_(
                            DomainEvent.event_type
                            == STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
                            DomainEvent.aggregate_type == "production_workflow_run",
                            DomainEvent.workflow_run_id.is_not(None),
                        ),
                        and_(
                            DomainEvent.event_type.in_(DELIVERY_EVENT_TYPES),
                            DomainEvent.workflow_run_id.is_(None),
                        ),
                    ),
                    DomainEvent.delivered_at.is_(None),
                    DomainEvent.published_at.is_(None),
                    DomainEvent.dead_lettered_at.is_(None),
                    or_(
                        DomainEvent.next_attempt_at.is_(None),
                        DomainEvent.next_attempt_at <= now,
                    ),
                    or_(
                        DomainEvent.lease_owner.is_(None),
                        DomainEvent.lease_expires_at <= now,
                    ),
                )
                .order_by(
                    DomainEvent.next_attempt_at.asc().nulls_first(),
                    DomainEvent.created_at.asc(),
                    DomainEvent.id.asc(),
                )
                .limit(1)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if event is None:
                return None
            cadence_event = event.event_type == CADENCE_EVALUATION_EVENT_TYPE
            qualification_event = event.event_type in {SCRIPT_QUALIFICATION_EVENT_TYPE, BACKGROUND_EVENT_TYPE, BACKGROUND_POLL_EVENT_TYPE}
            analytics_event = event.event_type == ANALYTICS_WINDOW_EVENT_TYPE
            recovery_event = event.event_type == STALE_WORKFLOW_RECOVERY_EVENT_TYPE
            delivery_event = event.event_type in DELIVERY_EVENT_TYPES
            if (
                cadence_event
                or qualification_event
                or analytics_event
                or recovery_event
                or delivery_event
            ):
                if event.command_id is None or not isinstance(event.payload, dict):
                    if qualification_event:
                        self._dead_letter_qualification_event(
                            event,
                            now=now,
                            error_code="SCRIPT_QUALIFICATION_EVENT_IDENTITY_INVALID",
                            summary="qualification command identity or payload is invalid",
                            retry_eligible=False,
                        )
                    elif delivery_event:
                        self._dead_letter_delivery_event(
                            event,
                            now=now,
                            error_code="DELIVERY_EVENT_IDENTITY_INVALID",
                            summary="delivery command identity or payload is invalid",
                        )
                    else:
                        self._dead_letter_cadence_event(
                            event,
                            now=now,
                            error_code=(
                                "CADENCE_EVENT_IDENTITY_INVALID"
                                if cadence_event
                                else (
                                    "ANALYTICS_EVENT_IDENTITY_INVALID"
                                    if analytics_event
                                    else "STALE_WORKFLOW_RECOVERY_EVENT_IDENTITY_INVALID"
                                )
                            ),
                            summary="scheduler command identity or payload is invalid",
                        )
                    continue
                if event.attempt_count >= event.max_attempts:
                    if qualification_event:
                        qualification = self.session.get(
                            ScriptQualificationRun, event.aggregate_id
                        )
                        self._dead_letter_qualification_event(
                            event,
                            now=now,
                            error_code=(
                                "SCRIPT_QUALIFICATION_FINALIZATION_RETRY_EXHAUSTED"
                                if qualification is not None
                                and qualification.state == "QUALIFIED"
                                else "SCRIPT_QUALIFICATION_EXECUTION_FAILED_NO_PROVIDER_RETRY"
                            ),
                            summary="qualification event reached its bounded attempt limit",
                            retry_eligible=(
                                qualification is not None
                                and qualification.state == "QUALIFIED"
                            ),
                        )
                    elif delivery_event:
                        self._dead_letter_delivery_event(
                            event,
                            now=now,
                            error_code="DELIVERY_RETRY_EXHAUSTED",
                            summary="delivery event reached its bounded attempt limit",
                        )
                    else:
                        self._dead_letter_cadence_event(
                            event,
                            now=now,
                            error_code=(
                                "CADENCE_RETRY_EXHAUSTED"
                                if cadence_event
                                else (
                                    "ANALYTICS_RETRY_EXHAUSTED"
                                    if analytics_event
                                    else "STALE_WORKFLOW_RECOVERY_RETRY_EXHAUSTED"
                                )
                            ),
                            summary="scheduler event reached its bounded attempt limit",
                        )
                    continue
                run = None
            else:
                run = self._lock_run(event.workflow_run_id)
                if run is None:
                    self._dead_letter_orphan(event, now=now)
                    continue
                if run.state == ProductionWorkflowState.CANCELED.value:
                    self._settle_canceled_event(event, now=now)
                    continue
                if event.attempt_count >= event.max_attempts:
                    failure = WorkflowStageError(
                        classification=(
                            WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY
                        ),
                        error_code="STAGE_RETRY_EXHAUSTED",
                        summary=(
                            "event reached its attempt limit before another claim"
                        ),
                        incident_type="STAGE_RETRY_EXHAUSTED",
                        retry_eligible=True,
                    )
                    self._dead_letter_locked(event, run, failure, now=now)
                    continue

            previous_owner = event.lease_owner
            previous_expiry = event.lease_expires_at
            if (
                run is not None
                and previous_owner is not None
                and previous_expiry is not None
                and previous_expiry <= now
            ):
                self._ensure_incident(
                    run=run,
                    event=event,
                    incident_type="WORKER_LEASE_EXPIRED",
                    severity="WARNING",
                    retry_eligible=True,
                    learning_excluded=True,
                    operator_visible_blocker=(
                        "A worker lease expired; the same deterministic command "
                        "will be reconciled before any effect is repeated."
                    ),
                    reason_codes=["WORKER_LEASE_EXPIRED"],
                    next_action=(
                        "Inspect the prior worker and immutable command receipt."
                    ),
                    metadata={
                        "previous_lease_owner": previous_owner,
                        "previous_lease_expires_at": previous_expiry.isoformat(),
                    },
                )

            max_execution_seconds = _bounded_execution_seconds(
                event.metadata_, self.max_execution_seconds
            )
            execution_deadline = now + timedelta(seconds=max_execution_seconds)
            lease_expires_at = min(
                now + timedelta(seconds=self.lease_seconds),
                execution_deadline,
            )
            metadata = dict(event.metadata_ or {})
            metadata.update(
                {
                    "lease_acquired_at": now.isoformat(),
                    "execution_deadline": execution_deadline.isoformat(),
                    "lease_generation": int(metadata.get("lease_generation", 0)) + 1,
                }
            )
            event.metadata_ = metadata
            event.attempt_count += 1
            event.lease_owner = worker_id
            event.lease_expires_at = lease_expires_at
            event.heartbeat_at = now
            event.last_error_code = None
            event.last_error_summary = None
            self.session.flush()
            assert event.command_id is not None
            return ClaimedWorkflowEvent(
                event_id=event.id,
                workflow_run_id=event.workflow_run_id,
                command_id=event.command_id,
                worker_id=worker_id,
                attempt_number=event.attempt_count,
                lease_expires_at=lease_expires_at,
                execution_deadline=execution_deadline,
            )
        raise RuntimeError("OUTBOX_CLAIM_SETTLEMENT_LIMIT_EXCEEDED")

    def claim_exact(
        self, *, event_id: uuid.UUID, worker_id: str
    ) -> ClaimedWorkflowEvent | None:
        """Claim one named event without inspecting any other outbox row.

        This is deliberately separate from ``claim_next`` for tightly scoped
        operator-authorized continuations.  It preserves the normal lease,
        attempt, and acknowledgement mechanics while never behaving as a
        queue sweep.
        """

        _validate_worker_id(worker_id)
        now = self.now()
        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == event_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if event is None:
            return None
        if (
            event.delivered_at is not None
            or event.published_at is not None
            or event.dead_lettered_at is not None
            or (
                event.next_attempt_at is not None
                and event.next_attempt_at > now
            )
            or (
                event.lease_owner is not None
                and event.lease_expires_at is not None
                and event.lease_expires_at > now
            )
        ):
            return None
        if event.command_id is None or not isinstance(event.payload, dict):
            raise ConflictError("SCOPED_EVENT_IDENTITY_INVALID")
        if event.attempt_count >= event.max_attempts:
            raise ConflictError("SCOPED_EVENT_RETRY_BUDGET_EXHAUSTED")

        run = (
            self._lock_run(event.workflow_run_id)
            if event.workflow_run_id is not None
            else None
        )
        if event.workflow_run_id is not None and run is None:
            raise ConflictError("SCOPED_EVENT_WORKFLOW_MISSING")
        if run is not None and run.state == ProductionWorkflowState.CANCELED.value:
            raise ConflictError("SCOPED_EVENT_WORKFLOW_CANCELED")

        max_execution_seconds = _bounded_execution_seconds(
            event.metadata_, self.max_execution_seconds
        )
        execution_deadline = now + timedelta(seconds=max_execution_seconds)
        lease_expires_at = min(
            now + timedelta(seconds=self.lease_seconds), execution_deadline
        )
        metadata = dict(event.metadata_ or {})
        metadata.update(
            {
                "lease_acquired_at": now.isoformat(),
                "execution_deadline": execution_deadline.isoformat(),
                "lease_generation": int(metadata.get("lease_generation", 0)) + 1,
            }
        )
        event.metadata_ = metadata
        event.attempt_count += 1
        event.lease_owner = worker_id
        event.lease_expires_at = lease_expires_at
        event.heartbeat_at = now
        event.last_error_code = None
        event.last_error_summary = None
        self.session.flush()
        return ClaimedWorkflowEvent(
            event_id=event.id,
            workflow_run_id=event.workflow_run_id,
            command_id=event.command_id,
            worker_id=worker_id,
            attempt_number=event.attempt_count,
            lease_expires_at=lease_expires_at,
            execution_deadline=execution_deadline,
        )

    def require_claimed_event(
        self, *, event_id: uuid.UUID, worker_id: str
    ) -> DomainEvent:
        event = self.session.get(DomainEvent, event_id)
        now = self.now()
        if (
            event is None
            or event.lease_owner != worker_id
            or event.lease_expires_at is None
            or event.lease_expires_at <= now
            or event.delivered_at is not None
            or event.published_at is not None
            or event.dead_lettered_at is not None
        ):
            raise OutboxLeaseLostError("OUTBOX_EVENT_LEASE_NOT_OWNED")
        return event

    def heartbeat(self, *, event_id: uuid.UUID, worker_id: str) -> datetime:
        """Extend a live lease without exceeding the fixed execution deadline."""

        now = self.now()
        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            event is None
            or event.lease_owner != worker_id
            or event.delivered_at is not None
            or event.published_at is not None
            or event.dead_lettered_at is not None
            or event.lease_expires_at is None
            or event.lease_expires_at <= now
        ):
            raise OutboxLeaseLostError("OUTBOX_HEARTBEAT_LEASE_LOST")
        execution_deadline = _execution_deadline(event)
        if execution_deadline is None:
            raise OutboxLeaseLostError("OUTBOX_EXECUTION_DEADLINE_MISSING")
        if now >= execution_deadline:
            raise OutboxExecutionDeadlineExceeded("OUTBOX_MAX_EXECUTION_EXCEEDED")
        event.heartbeat_at = now
        event.lease_expires_at = min(
            now + timedelta(seconds=self.lease_seconds),
            execution_deadline,
        )
        self.session.flush()
        return event.lease_expires_at

    def mark_delivered(self, *, event_id: uuid.UUID, worker_id: str) -> DomainEvent:
        now = self.now()
        event = self._lock_owned_event(
            event_id=event_id, worker_id=worker_id, allow_expired=False
        )
        event.delivered_at = now
        # Preserve the historical publication read surface while delivered_at
        # is the canonical Phase 4 acknowledgement.
        event.published_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = None
        event.last_error_summary = None
        self.session.flush()
        return event

    def record_failure(
        self,
        *,
        event_id: uuid.UUID,
        worker_id: str,
        error: WorkflowStageError | Exception,
    ) -> FailureDisposition:
        """Normalize a failure into retry, block, terminal, or dead-letter state."""

        now = self.now()
        event = self._lock_owned_event(
            event_id=event_id,
            worker_id=worker_id,
            # A handler can notice deadline expiry just after the lease elapsed.
            # The command owner may still record its failure if nobody reclaimed
            # it; row locking plus owner identity preserves that race boundary.
            allow_expired=True,
        )
        if event.event_type in DELIVERY_EVENT_TYPES:
            return self._record_delivery_failure(
                event=event,
                error=error,
                now=now,
            )
        if event.event_type in {
            CADENCE_EVALUATION_EVENT_TYPE,
            ANALYTICS_WINDOW_EVENT_TYPE,
            STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
        }:
            return self._record_cadence_failure(
                event=event,
                error=error,
                now=now,
            )
        if event.event_type in {SCRIPT_QUALIFICATION_EVENT_TYPE, BACKGROUND_EVENT_TYPE, BACKGROUND_POLL_EVENT_TYPE}:
            return self._record_script_qualification_failure(
                event=event,
                error=error,
                now=now,
            )
        run = self._lock_run(event.workflow_run_id)
        if run is None:
            self._dead_letter_orphan(event, now=now)
            return FailureDisposition(
                event_id=event.id,
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY),
                retry_scheduled=False,
                next_attempt_at=None,
                dead_letter_job_id=self._dead_letter_for_event_id(event.id),
                incident_id=None,
            )
        if run.state == ProductionWorkflowState.CANCELED.value:
            self._settle_canceled_event(event, now=now)
            return FailureDisposition(
                event_id=event.id,
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
                retry_scheduled=False,
                next_attempt_at=None,
                dead_letter_job_id=None,
                incident_id=None,
                workflow_canceled=True,
            )
        normalized = normalize_stage_error(error)
        invariant_error = self._retry_invariant_error(
            event=event,
            run=run,
            classification=normalized.classification,
        )
        if invariant_error is not None:
            normalized = invariant_error
        event.last_error_code = normalized.error_code
        event.last_error_summary = _redact_summary(normalized.summary)
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None

        retryable = (
            normalized.classification in RETRYABLE_CLASSIFICATIONS
            and normalized.retry_eligible
        )
        attempts_remain = event.attempt_count < event.max_attempts
        if retryable and attempts_remain and run.state != "CANCELED":
            delay = self.retry_delay_seconds(event.attempt_count)
            next_attempt_at = now + timedelta(seconds=delay)
            event.next_attempt_at = next_attempt_at
            run.state = ProductionWorkflowState.RETRY_SCHEDULED.value
            run.state_reason_codes = [
                normalized.error_code,
                normalized.classification.value,
            ]
            run.last_progress_at = now
            run.projection_version += 1
            incident = None
            if normalized.incident_type is not None:
                incident = self._ensure_incident(
                    run=run,
                    event=event,
                    incident_type=normalized.incident_type,
                    severity="WARNING",
                    retry_eligible=True,
                    learning_excluded=normalized.learning_excluded,
                    operator_visible_blocker=(normalized.operator_visible_blocker),
                    reason_codes=[
                        normalized.error_code,
                        normalized.classification.value,
                    ],
                    next_action=(
                        "Wait for the deterministic retry or inspect the "
                        "immutable command receipt."
                    ),
                )
            self.session.flush()
            return FailureDisposition(
                event_id=event.id,
                classification=normalized.classification,
                retry_scheduled=True,
                next_attempt_at=next_attempt_at,
                dead_letter_job_id=None,
                incident_id=incident.id if incident is not None else None,
            )

        job, incident = self._dead_letter_locked(event, run, normalized, now=now)
        self.session.flush()
        return FailureDisposition(
            event_id=event.id,
            classification=normalized.classification,
            retry_scheduled=False,
            next_attempt_at=None,
            dead_letter_job_id=job.id,
            incident_id=incident.id,
        )

    def _record_delivery_failure(
        self,
        *,
        event: DomainEvent,
        error: WorkflowStageError | Exception,
        now: datetime,
    ) -> FailureDisposition:
        """Retry only deterministic same-effect delivery reconciliation."""

        summary = _redact_summary(str(error) or type(error).__name__)
        code = (str(error).split(":", 1)[0] or type(error).__name__)[:160]
        retryable_codes = {
            "YOUTUBE_PROCESSING_PENDING",
            "YOUTUBE_UPLOAD_INCOMPLETE",
            "YOUTUBE_UPLOAD_RECONCILIATION_REQUIRED",
            "YOUTUBE_PUBLICATION_NOT_YET_PUBLIC",
            "YOUTUBE_PUBLICATION_OBSERVATION_RECONCILIATION_REQUIRED",
            "LOCAL_MEDIA_PURGE_RECONCILIATION_REQUIRED",
        }
        event.last_error_code = code
        event.last_error_summary = summary
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        if code in retryable_codes and event.attempt_count < event.max_attempts:
            event.next_attempt_at = now + timedelta(
                seconds=self.retry_delay_seconds(event.attempt_count)
            )
            self.session.flush()
            return FailureDisposition(
                event_id=event.id,
                classification=(
                    WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY
                ),
                retry_scheduled=True,
                next_attempt_at=event.next_attempt_at,
                dead_letter_job_id=None,
                incident_id=None,
            )
        job, incident = self._dead_letter_delivery_event(
            event,
            now=now,
            error_code=code,
            summary=summary,
        )
        return FailureDisposition(
            event_id=event.id,
            classification=(
                WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY
            ),
            retry_scheduled=False,
            next_attempt_at=None,
            dead_letter_job_id=job.id,
            incident_id=incident.id,
        )

    def _record_script_qualification_failure(
        self,
        *,
        event: DomainEvent,
        error: WorkflowStageError | Exception,
        now: datetime,
    ) -> FailureDisposition:
        """Retry only final admission after a sealed qualification PASS.

        Qualification owns paid writer/verifier calls, so every state before
        ``QUALIFIED`` fails closed.  The only safe automatic replay is the
        local final-admission transaction after its immutable receipt exists.
        """

        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == event.aggregate_id)
            .with_for_update()
        )
        summary = _redact_summary(str(error) or type(error).__name__)
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        if qualification is not None and qualification.state == "QUALIFIED":
            event.last_error_code = "SCRIPT_QUALIFICATION_FINALIZATION_FAILED"
            event.last_error_summary = summary
            if event.attempt_count < event.max_attempts:
                event.next_attempt_at = now + timedelta(
                    seconds=self.retry_delay_seconds(event.attempt_count)
                )
                metadata = dict(event.metadata_ or {})
                metadata["qualification_phase"] = "FINALIZATION_RETRY"
                event.metadata_ = metadata
                self.session.flush()
                return FailureDisposition(
                    event_id=event.id,
                    classification=WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
                    retry_scheduled=True,
                    next_attempt_at=event.next_attempt_at,
                    dead_letter_job_id=None,
                    incident_id=None,
                )
            job, incident = self._dead_letter_qualification_event(
                event,
                now=now,
                error_code="SCRIPT_QUALIFICATION_FINALIZATION_RETRY_EXHAUSTED",
                summary=summary,
                retry_eligible=True,
            )
            return FailureDisposition(
                event_id=event.id,
                classification=WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
                retry_scheduled=False,
                next_attempt_at=None,
                dead_letter_job_id=job.id,
                incident_id=incident.id,
            )

        event.last_error_code = "SCRIPT_QUALIFICATION_EXECUTION_FAILED_NO_PROVIDER_RETRY"
        event.last_error_summary = summary
        if qualification is not None and qualification.state not in {
            "QUALIFIED",
            "BLOCKED_NON_REPAIRABLE",
            "BLOCKED_REPAIR_BUDGET_EXHAUSTED",
            "COOLDOWN",
            "SUPERSEDED",
        }:
            qualification.state = "BLOCKED_NON_REPAIRABLE"
            qualification.failure_receipt = {
                "reason_codes": ["SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"],
                "detail": summary[:512],
                "logical_identity_hash": qualification.logical_identity_hash,
            }
            from app.services.script_qualification_recovery import (
                ScriptQualificationRecoveryService,
            )

            ScriptQualificationRecoveryService(
                self.session, now=self.now
            ).settle_unknown_provider_outcome(qualification)
        job, incident = self._dead_letter_qualification_event(
            event,
            now=now,
            error_code="SCRIPT_QUALIFICATION_EXECUTION_FAILED_NO_PROVIDER_RETRY",
            summary=summary,
            retry_eligible=False,
        )
        return FailureDisposition(
            event_id=event.id,
            classification=WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
            retry_scheduled=False,
            next_attempt_at=None,
            dead_letter_job_id=job.id,
            incident_id=incident.id,
        )

    def _record_cadence_failure(
        self,
        *,
        event: DomainEvent,
        error: WorkflowStageError | Exception,
        now: datetime,
    ) -> FailureDisposition:
        summary = _redact_summary(str(error) or type(error).__name__)
        event.last_error_code = "CADENCE_EVALUATION_FAILED"
        event.last_error_summary = summary
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        if event.attempt_count < event.max_attempts:
            next_attempt_at = now + timedelta(
                seconds=self.retry_delay_seconds(event.attempt_count)
            )
            event.next_attempt_at = next_attempt_at
            self.session.flush()
            return FailureDisposition(
                event_id=event.id,
                classification=(WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY),
                retry_scheduled=True,
                next_attempt_at=next_attempt_at,
                dead_letter_job_id=None,
                incident_id=None,
            )
        job, incident = self._dead_letter_cadence_event(
            event,
            now=now,
            error_code="CADENCE_RETRY_EXHAUSTED",
            summary=summary,
        )
        return FailureDisposition(
            event_id=event.id,
            classification=(WorkflowFailureClassification.FAIL_PERMANENT_POLICY),
            retry_scheduled=False,
            next_attempt_at=None,
            dead_letter_job_id=job.id,
            incident_id=incident.id,
        )

    def retry_delay_seconds(self, attempt_number: int) -> int:
        if attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        return min(
            self.backoff_cap_seconds,
            self.backoff_base_seconds * (2 ** (attempt_number - 1)),
        )

    def retry_dead_letter(
        self,
        *,
        dead_letter_job_id: uuid.UUID,
        company_id: uuid.UUID,
        data: DeadLetterRetryRequest,
        actor: ActorContext,
    ) -> DeadLetterRetryRead:
        require_company_permission(
            self.session,
            actor=actor,
            permission="ops.manage",
            company_id=company_id,
        )
        job = self.session.scalar(
            select(DeadLetterJob)
            .where(DeadLetterJob.id == dead_letter_job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise NotFoundError(f"dead-letter job not found: {dead_letter_job_id}")
        if job.domain_event_id is None:
            raise ConflictError("WORKFLOW_DEAD_LETTER_BINDING_REQUIRED")
        if job.workflow_run_id is None:
            return self._retry_qualification_dead_letter(
                job=job,
                company_id=company_id,
                data=data,
                actor=actor,
            )
        run = self._lock_run(job.workflow_run_id)
        if run is None or run.company_id != company_id:
            raise NotFoundError(f"dead-letter job not found: {dead_letter_job_id}")
        if (
            not job.retry_eligible
            or job.replay_state != "REPLAYABLE"
            or run.state
            in {
                ProductionWorkflowState.CANCELED.value,
                ProductionWorkflowState.FINAL_REVIEW_READY.value,
                ProductionWorkflowState.PUBLICATION_VERIFIED.value,
            }
        ):
            raise ConflictError("DEAD_LETTER_NOT_RETRYABLE")
        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == job.domain_event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if event is None or event.workflow_run_id != run.id:
            raise ConflictError("DEAD_LETTER_EVENT_BINDING_MISMATCH")
        now = self.now()
        event.dead_lettered_at = None
        event.delivered_at = None
        event.published_at = None
        event.next_attempt_at = now
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = None
        event.last_error_summary = None
        event.max_attempts = max(
            event.max_attempts,
            event.attempt_count + data.additional_attempts,
        )
        event_metadata = dict(event.metadata_ or {})
        retry_policy = dict(event_metadata.get("retry_policy") or {})
        retry_policy["max_attempts"] = event.max_attempts
        retry_policy["ops_replay_authorized"] = True
        retry_policy["ops_replay_reason_code"] = data.reason_code
        event_metadata["retry_policy"] = retry_policy
        event.metadata_ = event_metadata
        job.replay_state = "REPLAYED"
        metadata = dict(job.metadata_ or {})
        metadata.update(
            {
                "replayed_at": now.isoformat(),
                "replayed_by_user_id": str(actor.actor_id),
                "replay_reason_code": data.reason_code,
                "additional_attempts": data.additional_attempts,
            }
        )
        job.metadata_ = metadata
        run.state = ProductionWorkflowState.RETRY_SCHEDULED.value
        run.state_reason_codes = [data.reason_code]
        try:
            payload = WorkflowStageEventPayload.model_validate(event.payload)
            run.current_stage = payload.stage.value
        except Exception as exc:
            raise ConflictError("DEAD_LETTER_EVENT_PAYLOAD_INVALID") from exc
        run.last_progress_at = now
        run.projection_version += 1
        self.session.flush()
        assert event.command_id is not None
        return DeadLetterRetryRead(
            dead_letter_job_id=job.id,
            workflow_run_id=run.id,
            domain_event_id=event.id,
            command_id=event.command_id,
            replay_state=job.replay_state,
            next_attempt_at=now,
        )

    def _retry_qualification_dead_letter(
        self,
        *,
        job: DeadLetterJob,
        company_id: uuid.UUID,
        data: DeadLetterRetryRequest,
        actor: ActorContext,
    ) -> DeadLetterRetryRead:
        """Replay only a previously exhausted final-admission transaction."""

        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == job.domain_event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            event is None
            or event.event_type != SCRIPT_QUALIFICATION_EVENT_TYPE
            or event.company_id != company_id
            or not job.retry_eligible
            or job.replay_state != "REPLAYABLE"
        ):
            raise ConflictError("DEAD_LETTER_NOT_RETRYABLE")
        qualification = self.session.scalar(
            select(ScriptQualificationRun)
            .where(ScriptQualificationRun.id == event.aggregate_id)
            .with_for_update()
        )
        if qualification is None or qualification.state != "QUALIFIED":
            raise ConflictError("SCRIPT_QUALIFICATION_FINALIZATION_NOT_RETRYABLE")
        now = self.now()
        event.dead_lettered_at = None
        event.delivered_at = None
        event.published_at = None
        event.next_attempt_at = now
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = None
        event.last_error_summary = None
        event.max_attempts = max(
            event.max_attempts,
            event.attempt_count + data.additional_attempts,
        )
        metadata = dict(event.metadata_ or {})
        retry_policy = dict(metadata.get("retry_policy") or {})
        retry_policy["max_attempts"] = event.max_attempts
        retry_policy["ops_replay_authorized"] = True
        retry_policy["ops_replay_reason_code"] = data.reason_code
        metadata["retry_policy"] = retry_policy
        metadata["qualification_phase"] = "FINALIZATION_RETRY"
        event.metadata_ = metadata
        job.replay_state = "REPLAYED"
        job.metadata_ = {
            **dict(job.metadata_ or {}),
            "replayed_at": now.isoformat(),
            "replayed_by_user_id": str(actor.actor_id),
            "replay_reason_code": data.reason_code,
            "additional_attempts": data.additional_attempts,
        }
        self.session.flush()
        assert event.command_id is not None
        return DeadLetterRetryRead(
            dead_letter_job_id=job.id,
            workflow_run_id=None,
            domain_event_id=event.id,
            command_id=event.command_id,
            replay_state=job.replay_state,
            next_attempt_at=now,
        )

    def release_worker_leases(self, *, worker_id: str) -> int:
        """Recoverably release this worker's leases during graceful shutdown."""

        _validate_worker_id(worker_id)
        now = self.now()
        events = self.session.scalars(
            select(DomainEvent)
            .where(
                or_(
                    and_(
                        DomainEvent.event_type == WORKFLOW_EVENT_TYPE,
                        DomainEvent.workflow_run_id.in_(_long_form_run_ids()),
                    ),
                    DomainEvent.event_type == CADENCE_EVALUATION_EVENT_TYPE,
                    DomainEvent.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE,
                    DomainEvent.event_type.in_((BACKGROUND_EVENT_TYPE, BACKGROUND_POLL_EVENT_TYPE)),
                    DomainEvent.event_type == ANALYTICS_WINDOW_EVENT_TYPE,
                    DomainEvent.event_type == STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
                ),
                DomainEvent.lease_owner == worker_id,
                DomainEvent.delivered_at.is_(None),
                DomainEvent.published_at.is_(None),
                DomainEvent.dead_lettered_at.is_(None),
            )
            .with_for_update(skip_locked=True)
        ).all()
        for event in events:
            event.lease_owner = None
            event.lease_expires_at = None
            event.heartbeat_at = None
            event.next_attempt_at = now
            event.last_error_code = "WORKER_SHUTDOWN_RELEASE"
            event.last_error_summary = (
                "worker released the lease during graceful shutdown"
            )
        self.session.flush()
        return len(events)

    def reclaim_expired(self, *, limit: int = 100) -> int:
        """Make expired leases immediately due and record one visible incident."""

        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        now = self.now()
        events = self.session.scalars(
            select(DomainEvent)
            .where(
                or_(
                    and_(
                        DomainEvent.event_type == WORKFLOW_EVENT_TYPE,
                        DomainEvent.workflow_run_id.in_(_long_form_run_ids()),
                    ),
                    DomainEvent.event_type == CADENCE_EVALUATION_EVENT_TYPE,
                    DomainEvent.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE,
                    DomainEvent.event_type == ANALYTICS_WINDOW_EVENT_TYPE,
                    DomainEvent.event_type == STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
                ),
                DomainEvent.delivered_at.is_(None),
                DomainEvent.published_at.is_(None),
                DomainEvent.dead_lettered_at.is_(None),
                DomainEvent.lease_owner.is_not(None),
                DomainEvent.lease_expires_at <= now,
            )
            .order_by(DomainEvent.lease_expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        reclaimed = 0
        for event in events:
            if event.event_type in {
                CADENCE_EVALUATION_EVENT_TYPE,
                ANALYTICS_WINDOW_EVENT_TYPE,
                STALE_WORKFLOW_RECOVERY_EVENT_TYPE,
            }:
                event.lease_owner = None
                event.lease_expires_at = None
                event.heartbeat_at = None
                event.next_attempt_at = now
                event.last_error_code = "WORKER_LEASE_EXPIRED"
                event.last_error_summary = "expired scheduler or recovery lease was released for deterministic replay"
                reclaimed += 1
                continue
            if event.event_type == SCRIPT_QUALIFICATION_EVENT_TYPE:
                qualification = self.session.scalar(
                    select(ScriptQualificationRun)
                    .where(ScriptQualificationRun.id == event.aggregate_id)
                    # A skipped row is a temporarily owned authority, not a
                    # missing authority.  Waiting here is safe: this path is
                    # deterministic reconciliation only and never performs a
                    # provider retry.
                    .with_for_update()
                )
                if qualification is None:
                    self._dead_letter_qualification_event(
                        event,
                        now=now,
                        error_code="SCRIPT_QUALIFICATION_RUN_NOT_FOUND",
                        summary="qualification authority is missing",
                        retry_eligible=False,
                    )
                    reclaimed += 1
                    continue
                event.lease_owner = None
                event.lease_expires_at = None
                event.heartbeat_at = None
                event.next_attempt_at = now
                event.last_error_code = "WORKER_LEASE_EXPIRED"
                event.last_error_summary = "expired qualification lease was released for deterministic reconciliation"
                reclaimed += 1
                continue
            run = self._lock_run(event.workflow_run_id, skip_locked=True)
            if run is None:
                continue
            old_owner = event.lease_owner
            old_expiry = event.lease_expires_at
            event.lease_owner = None
            event.lease_expires_at = None
            event.heartbeat_at = None
            event.next_attempt_at = now
            event.last_error_code = "WORKER_LEASE_EXPIRED"
            event.last_error_summary = (
                "expired lease was released for deterministic reconciliation"
            )
            if run.state not in TERMINAL_STATES_FOR_RECLAIM:
                run.state = ProductionWorkflowState.RETRY_SCHEDULED.value
                run.state_reason_codes = ["WORKER_LEASE_EXPIRED"]
                run.last_progress_at = now
                run.projection_version += 1
            self._ensure_incident(
                run=run,
                event=event,
                incident_type="WORKER_LEASE_EXPIRED",
                severity="WARNING",
                retry_eligible=True,
                learning_excluded=True,
                operator_visible_blocker=(
                    "A stage lease expired and is ready for safe replay."
                ),
                reason_codes=["WORKER_LEASE_EXPIRED"],
                next_action=(
                    "Inspect worker health; replay will reuse command identity."
                ),
                metadata={
                    "previous_lease_owner": old_owner,
                    "previous_lease_expires_at": (
                        old_expiry.isoformat() if old_expiry else None
                    ),
                },
            )
            reclaimed += 1
        self.session.flush()
        return reclaimed

    def record_cancellation_uncertainty(
        self,
        *,
        run: ProductionWorkflowRun,
        events: Iterable[DomainEvent],
    ) -> list[OpsIncident]:
        incidents: list[OpsIncident] = []
        for event in events:
            receipt = self.session.scalar(
                select(WorkflowCommandReceipt.id).where(
                    WorkflowCommandReceipt.domain_event_id == event.id
                )
            )
            if receipt is not None:
                continue
            incidents.append(
                self._ensure_incident(
                    run=run,
                    event=event,
                    incident_type="CANCELED_WITH_IN_FLIGHT_EFFECT",
                    severity="ERROR",
                    retry_eligible=False,
                    learning_excluded=True,
                    operator_visible_blocker=(
                        "Cancellation occurred while an effect may have been "
                        "in flight. Provider and budget truth must be reconciled."
                    ),
                    reason_codes=["CANCELED_WITH_IN_FLIGHT_EFFECT"],
                    next_action=(
                        "Reconcile provider, budget, render, and archive receipts; "
                        "do not resubmit the command."
                    ),
                )
            )
        self.session.flush()
        return incidents

    def _dead_letter_locked(
        self,
        event: DomainEvent,
        run: ProductionWorkflowRun,
        error: WorkflowStageError,
        *,
        now: datetime,
    ) -> tuple[DeadLetterJob, OpsIncident]:
        event.dead_lettered_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = error.error_code
        event.last_error_summary = _redact_summary(error.summary)
        retry_eligible = (
            error.classification in RETRYABLE_CLASSIFICATIONS and error.retry_eligible
        )
        job = self.session.scalar(
            select(DeadLetterJob)
            .where(DeadLetterJob.domain_event_id == event.id)
            .with_for_update()
        )
        if job is None:
            job = DeadLetterJob(
                queue_name=self.queue_name,
                job_type=event.event_type,
                payload_ref=f"domain-event:{event.id}",
                target_type=event.aggregate_type,
                target_id=event.aggregate_id,
                domain_event_id=event.id,
                workflow_run_id=run.id,
                command_id=event.command_id,
                fail_count=event.attempt_count,
                first_failed_at=now,
                last_failed_at=now,
                replay_state=("REPLAYABLE" if retry_eligible else "NOT_REPLAYABLE"),
                retry_eligible=retry_eligible,
                reason_code=error.error_code,
                next_action=(
                    "Retry under ops.manage after evidence review."
                    if retry_eligible
                    else "Resolve the permanent authority or policy failure."
                ),
                metadata_={
                    "failure_classification": error.classification.value,
                    "learning_excluded": error.learning_excluded,
                },
            )
            self.session.add(job)
        else:
            job.fail_count = max(job.fail_count, event.attempt_count)
            job.last_failed_at = now
            job.replay_state = "REPLAYABLE" if retry_eligible else "NOT_REPLAYABLE"
            job.retry_eligible = retry_eligible
            job.reason_code = error.error_code
            metadata = dict(job.metadata_ or {})
            metadata.update(
                {
                    "failure_classification": error.classification.value,
                    "learning_excluded": error.learning_excluded,
                    "redelivered_after_replay": True,
                }
            )
            job.metadata_ = metadata

        self.session.flush()
        if error.classification in {
            WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
            WorkflowFailureClassification.FAIL_PERMANENT_POLICY,
        }:
            run.state = ProductionWorkflowState.FAILED_TERMINAL.value
        elif error.classification == (
            WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE
        ):
            run.state = ProductionWorkflowState.BLOCKED.value
        else:
            run.state = ProductionWorkflowState.DEAD_LETTERED.value
        run.state_reason_codes = [
            error.error_code,
            error.classification.value,
        ]
        run.last_progress_at = now
        run.projection_version += 1

        incident_type = error.incident_type
        if incident_type is None:
            incident_type = (
                "STAGE_RETRY_EXHAUSTED" if retry_eligible else "INTEGRITY_MISMATCH"
            )
        severity = (
            "CRITICAL"
            if error.classification
            == WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY
            else "ERROR"
        )
        incident = self._ensure_incident(
            run=run,
            event=event,
            incident_type=incident_type,
            severity=severity,
            retry_eligible=retry_eligible,
            learning_excluded=error.learning_excluded,
            operator_visible_blocker=error.operator_visible_blocker,
            reason_codes=[
                error.error_code,
                error.classification.value,
            ],
            next_action=job.next_action or "Inspect the dead-letter job.",
            metadata={"dead_letter_job_id": str(job.id)},
        )
        self.session.flush()
        return job, incident

    def _retry_invariant_error(
        self,
        *,
        event: DomainEvent,
        run: ProductionWorkflowRun,
        classification: WorkflowFailureClassification,
    ) -> WorkflowStageError | None:
        if classification not in RETRYABLE_CLASSIFICATIONS:
            return None
        try:
            payload = WorkflowStageEventPayload.model_validate(event.payload)
        except Exception:
            return _retry_integrity_error("RETRY_EVENT_PAYLOAD_INVALID")
        policy = (event.metadata_ or {}).get("retry_policy")
        if not isinstance(policy, dict):
            return _retry_integrity_error("RETRY_POLICY_BINDING_MISSING")
        allowed = policy.get("allowed_classifications")
        if (
            policy.get("policy_key") != "production-workflow-bounded-v1"
            or policy.get("automatic_retry_allowed") is not True
            or not isinstance(allowed, list)
            or classification.value not in allowed
            or bool(policy.get("provider_substitution_allowed"))
            or _safe_int(policy.get("max_attempts")) != event.max_attempts
            or event.max_attempts < 1
            or event.max_attempts > 20
            or event.payload_hash != semantic_hash(event.payload or {})
            or payload.workflow_run_id != run.id
            or payload.production_lane.value != run.production_lane
            or event.command_id != command_id_for(run.id, payload.stage)
        ):
            return _retry_integrity_error("RETRY_POLICY_IDENTITY_MISMATCH")
        if (
            classification
            == WorkflowFailureClassification.POLICY_AUTHORIZED_LOCAL_REPAIR
            and policy.get("policy_authorized_local_repair") is not True
        ):
            return _retry_integrity_error("LOCAL_REPAIR_NOT_POLICY_AUTHORIZED")
        return None

    def _dead_letter_orphan(self, event: DomainEvent, *, now: datetime) -> None:
        event.dead_lettered_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = "WORKFLOW_RUN_NOT_FOUND"
        event.last_error_summary = "workflow projection is missing"
        job = self.session.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        if job is None:
            self.session.add(
                DeadLetterJob(
                    queue_name=self.queue_name,
                    job_type=event.event_type,
                    payload_ref=f"domain-event:{event.id}",
                    target_type=event.aggregate_type,
                    target_id=event.aggregate_id,
                    domain_event_id=event.id,
                    workflow_run_id=event.workflow_run_id,
                    command_id=event.command_id,
                    fail_count=event.attempt_count,
                    first_failed_at=now,
                    last_failed_at=now,
                    replay_state="NOT_REPLAYABLE",
                    retry_eligible=False,
                    reason_code="WORKFLOW_RUN_NOT_FOUND",
                    next_action="Restore or reconcile workflow authority.",
                    metadata_={
                        "failure_classification": (
                            WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY.value
                        )
                    },
                )
            )
        self.session.flush()

    def _dead_letter_delivery_event(
        self,
        event: DomainEvent,
        *,
        now: datetime,
        error_code: str,
        summary: str,
    ) -> tuple[DeadLetterJob, OpsIncident]:
        event.dead_lettered_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = error_code
        event.last_error_summary = _redact_summary(summary)
        job = self.session.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        if job is None:
            job = DeadLetterJob(
                queue_name=self.queue_name,
                job_type=event.event_type,
                payload_ref=f"domain-event:{event.id}",
                target_type=event.aggregate_type,
                target_id=event.aggregate_id,
                domain_event_id=event.id,
                workflow_run_id=None,
                command_id=event.command_id,
                fail_count=event.attempt_count,
                first_failed_at=now,
                last_failed_at=now,
                replay_state="NOT_REPLAYABLE",
                retry_eligible=False,
                reason_code=error_code,
                next_action=(
                    "Inspect the exact delivery effect and reconcile provider "
                    "or filesystem evidence without creating a second effect."
                ),
                metadata_={"learning_excluded": True},
            )
            self.session.add(job)
            self.session.flush()
        incident = self.session.scalar(
            select(OpsIncident)
            .where(
                OpsIncident.domain_event_id == event.id,
                OpsIncident.incident_type == "DEAD_LETTER_JOB",
                OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
            )
            .order_by(OpsIncident.created_at)
        )
        if incident is None:
            incident = OpsIncident(
                incident_type="DEAD_LETTER_JOB",
                severity="ERROR",
                state="OPEN",
                workflow_run_id=None,
                stage="DELIVERY",
                domain_event_id=event.id,
                command_id=event.command_id,
                retry_eligible=False,
                learning_excluded=True,
                operator_visible_blocker=_redact_summary(summary),
                resolution_evidence={},
                impacted_refs=[
                    {"type": event.aggregate_type, "id": str(event.aggregate_id)},
                    {"type": "domain_event", "id": str(event.id)},
                ],
                reason_codes=[error_code],
                next_action=job.next_action or "Inspect delivery authority.",
                metadata_={
                    "channel_workspace_id": (
                        str(event.channel_workspace_id)
                        if event.channel_workspace_id
                        else None
                    )
                },
            )
            self.session.add(incident)
        self.session.flush()
        return job, incident

    def _dead_letter_cadence_event(
        self,
        event: DomainEvent,
        *,
        now: datetime,
        error_code: str,
        summary: str,
    ) -> tuple[DeadLetterJob, OpsIncident]:
        event.dead_lettered_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = error_code
        event.last_error_summary = _redact_summary(summary)
        job = self.session.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        if job is None:
            job = DeadLetterJob(
                queue_name=self.queue_name,
                job_type=event.event_type,
                payload_ref=f"domain-event:{event.id}",
                target_type=event.aggregate_type,
                target_id=event.aggregate_id,
                domain_event_id=event.id,
                workflow_run_id=None,
                command_id=event.command_id,
                fail_count=event.attempt_count,
                first_failed_at=now,
                last_failed_at=now,
                replay_state="NOT_REPLAYABLE",
                retry_eligible=False,
                reason_code=error_code,
                next_action=(
                    "Inspect launch policy, candidate authority, and cadence "
                    "evidence before requesting a new deterministic evaluation."
                ),
                metadata_={"learning_excluded": True},
            )
            self.session.add(job)
            self.session.flush()
        incident = self.session.scalar(
            select(OpsIncident)
            .where(
                OpsIncident.domain_event_id == event.id,
                OpsIncident.incident_type == "DEAD_LETTER_JOB",
                OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
            )
            .order_by(OpsIncident.created_at)
        )
        if incident is None:
            incident = OpsIncident(
                incident_type="DEAD_LETTER_JOB",
                severity="ERROR",
                state="OPEN",
                workflow_run_id=None,
                stage="CADENCE_EVALUATION",
                domain_event_id=event.id,
                command_id=event.command_id,
                retry_eligible=False,
                learning_excluded=True,
                operator_visible_blocker=_redact_summary(summary),
                resolution_evidence={},
                impacted_refs=[
                    {"type": "launch_run", "id": str(event.aggregate_id)},
                    {"type": "domain_event", "id": str(event.id)},
                ],
                reason_codes=[error_code],
                next_action=job.next_action or "Inspect cadence authority.",
                metadata_={
                    "channel_workspace_id": (
                        str(event.channel_workspace_id)
                        if event.channel_workspace_id
                        else None
                    )
                },
            )
            self.session.add(incident)
        self.session.flush()
        return job, incident

    def _dead_letter_qualification_event(
        self,
        event: DomainEvent,
        *,
        now: datetime,
        error_code: str,
        summary: str,
        retry_eligible: bool,
    ) -> tuple[DeadLetterJob, OpsIncident]:
        """Dead-letter qualification without pretending it is a workflow run."""

        event.dead_lettered_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = error_code
        event.last_error_summary = _redact_summary(summary)
        job = self.session.scalar(
            select(DeadLetterJob).where(DeadLetterJob.domain_event_id == event.id)
        )
        if job is None:
            job = DeadLetterJob(
                queue_name=self.queue_name,
                job_type=event.event_type,
                payload_ref=f"domain-event:{event.id}",
                target_type=event.aggregate_type,
                target_id=event.aggregate_id,
                domain_event_id=event.id,
                workflow_run_id=None,
                command_id=event.command_id,
                fail_count=event.attempt_count,
                first_failed_at=now,
                last_failed_at=now,
                replay_state="REPLAYABLE" if retry_eligible else "NOT_REPLAYABLE",
                retry_eligible=retry_eligible,
                reason_code=error_code,
                next_action=(
                    "Retry final admission from the immutable qualification receipt."
                    if retry_eligible
                    else "Do not retry provider execution; inspect the immutable qualification failure receipt."
                ),
                metadata_={"learning_excluded": True, "qualification_event": True},
            )
            self.session.add(job)
            self.session.flush()
        incident = self.session.scalar(
            select(OpsIncident)
            .where(
                OpsIncident.domain_event_id == event.id,
                OpsIncident.incident_type == "DEAD_LETTER_JOB",
                OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]),
            )
            .order_by(OpsIncident.created_at)
        )
        if incident is None:
            incident = OpsIncident(
                incident_type="DEAD_LETTER_JOB",
                severity="ERROR",
                state="OPEN",
                workflow_run_id=None,
                stage="SCRIPT_QUALIFICATION",
                domain_event_id=event.id,
                command_id=event.command_id,
                retry_eligible=retry_eligible,
                learning_excluded=True,
                operator_visible_blocker=_redact_summary(summary),
                resolution_evidence={},
                impacted_refs=[
                    {"type": "script_qualification_run", "id": str(event.aggregate_id)},
                    {"type": "domain_event", "id": str(event.id)},
                ],
                reason_codes=[error_code],
                next_action=job.next_action or "Inspect qualification authority.",
                metadata_={
                    "channel_workspace_id": (
                        str(event.channel_workspace_id)
                        if event.channel_workspace_id
                        else None
                    )
                },
            )
            self.session.add(incident)
        self.session.flush()
        return job, incident

    def _settle_canceled_event(self, event: DomainEvent, *, now: datetime) -> None:
        event.delivered_at = now
        event.published_at = now
        event.next_attempt_at = None
        event.lease_owner = None
        event.lease_expires_at = None
        event.heartbeat_at = None
        event.last_error_code = "WORKFLOW_CANCELED"
        event.last_error_summary = "event suppressed because the workflow was canceled"
        self.session.flush()

    def _ensure_incident(
        self,
        *,
        run: ProductionWorkflowRun,
        event: DomainEvent,
        incident_type: str,
        severity: str,
        retry_eligible: bool,
        learning_excluded: bool,
        operator_visible_blocker: str,
        reason_codes: list[str],
        next_action: str,
        metadata: dict[str, Any] | None = None,
    ) -> OpsIncident:
        incident = self.session.scalar(
            select(OpsIncident)
            .where(
                OpsIncident.workflow_run_id == run.id,
                OpsIncident.domain_event_id == event.id,
                OpsIncident.incident_type == incident_type,
                OpsIncident.state.in_(("OPEN", "ACKNOWLEDGED")),
            )
            .order_by(OpsIncident.created_at.asc())
            .limit(1)
        )
        if incident is not None:
            return incident
        stage = None
        try:
            stage = WorkflowStageEventPayload.model_validate(event.payload).stage.value
        except Exception:
            stage = (event.metadata_ or {}).get("stage")
        incident = OpsIncident(
            incident_type=incident_type,
            severity=severity,
            state="OPEN",
            project_id=run.video_project_id,
            uploaded_video_id=run.uploaded_video_id,
            workflow_run_id=run.id,
            stage=stage,
            domain_event_id=event.id,
            command_id=event.command_id,
            retry_eligible=retry_eligible,
            learning_excluded=learning_excluded,
            operator_visible_blocker=_redact_summary(operator_visible_blocker),
            resolution_evidence={},
            impacted_refs=[
                {"type": "production_workflow_run", "id": str(run.id)},
                {"type": "domain_event", "id": str(event.id)},
            ],
            reason_codes=reason_codes,
            next_action=next_action,
            metadata_=metadata or {},
        )
        self.session.add(incident)
        self.session.flush()
        return incident

    def _lock_owned_event(
        self,
        *,
        event_id: uuid.UUID,
        worker_id: str,
        allow_expired: bool,
    ) -> DomainEvent:
        event = self.session.scalar(
            select(DomainEvent)
            .where(DomainEvent.id == event_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = self.now()
        if (
            event is None
            or event.lease_owner != worker_id
            or event.delivered_at is not None
            or event.published_at is not None
            or event.dead_lettered_at is not None
            or (
                not allow_expired
                and (event.lease_expires_at is None or event.lease_expires_at <= now)
            )
        ):
            raise OutboxLeaseLostError("OUTBOX_EVENT_LEASE_LOST")
        return event

    def _lock_run(
        self,
        workflow_run_id: uuid.UUID | None,
        *,
        skip_locked: bool = False,
    ) -> ProductionWorkflowRun | None:
        if workflow_run_id is None:
            return None
        return self.session.scalar(
            select(ProductionWorkflowRun)
            .where(
                ProductionWorkflowRun.id == workflow_run_id,
                ProductionWorkflowRun.production_lane == "LONG_FORM",
                ProductionWorkflowRun.planning_source_type == "LONG_FORM_PLAN",
            )
            .with_for_update(skip_locked=skip_locked)
            .execution_options(populate_existing=True)
        )

    def _dead_letter_for_event_id(self, event_id: uuid.UUID) -> uuid.UUID | None:
        return self.session.scalar(
            select(DeadLetterJob.id).where(DeadLetterJob.domain_event_id == event_id)
        )


TERMINAL_STATES_FOR_RECLAIM = frozenset(
    {
        ProductionWorkflowState.CANCELED.value,
        ProductionWorkflowState.FINAL_REVIEW_READY.value,
        ProductionWorkflowState.PUBLICATION_VERIFIED.value,
        ProductionWorkflowState.FAILED_TERMINAL.value,
        ProductionWorkflowState.DEAD_LETTERED.value,
    }
)


def _long_form_run_ids() -> Any:
    return select(ProductionWorkflowRun.id).where(
        ProductionWorkflowRun.production_lane == "LONG_FORM",
        ProductionWorkflowRun.planning_source_type == "LONG_FORM_PLAN",
    )


def normalize_stage_error(
    error: WorkflowStageError | Exception,
) -> WorkflowStageError:
    if isinstance(error, WorkflowStageError):
        return error
    return WorkflowStageError(
        classification=WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY,
        error_code=f"UNEXPECTED_{type(error).__name__.upper()}",
        # Deliberately do not persist an arbitrary exception message; provider
        # SDK exceptions commonly contain request details or credential hints.
        summary=f"unexpected stage exception: {type(error).__name__}",
        retry_eligible=True,
        learning_excluded=True,
    )


def _retry_integrity_error(error_code: str) -> WorkflowStageError:
    return WorkflowStageError(
        classification=WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY,
        error_code=error_code,
        summary=(
            "automatic retry was refused because its immutable policy or "
            "command identity changed"
        ),
        incident_type="INTEGRITY_MISMATCH",
        retry_eligible=False,
        learning_excluded=True,
    )


def _bounded_execution_seconds(
    metadata: dict[str, Any] | None, configured_maximum: int
) -> int:
    raw = (metadata or {}).get("max_execution_seconds", configured_maximum)
    try:
        requested = int(raw)
    except (TypeError, ValueError):
        requested = configured_maximum
    return max(1, min(requested, configured_maximum))


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _execution_deadline(event: DomainEvent) -> datetime | None:
    raw = (event.metadata_ or {}).get("execution_deadline")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _validate_worker_id(worker_id: str) -> None:
    if (
        not worker_id
        or len(worker_id) > 160
        or re.fullmatch(r"[A-Za-z0-9._:-]+", worker_id) is None
    ):
        raise ValueError("worker_id is invalid")


def _redact_summary(value: str) -> str:
    redacted = value[:4000]
    redacted = re.sub(
        r"(?i)(password|secret|api[_-]?key|access[_-]?token|"
        r"refresh[_-]?token|authorization)\s*[:=]\s*[^\s,;]+",
        r"\1=[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED]",
        redacted,
    )
    return redacted
