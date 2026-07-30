from __future__ import annotations

import runpy
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.production_publish import FinalReviewCandidateCreateV2
from app.contracts.production_package import ProductionPackageContentV2
from app.contracts.production_workflow import (
    DeadLetterRetryRequest,
    ProductionWorkflowCancel,
    ProductionWorkflowProjectStart,
    ProductionWorkflowResume,
    ProductionWorkflowStart,
    WorkflowAuthorityRefs,
    WorkflowEffectState,
    WorkflowFailureClassification,
    WorkflowStageEventPayload,
    WorkflowStageResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.contracts.vcos_v2 import PlanningSourceType, ProductionLane
from app.core.actor import (
    _system_worker_actor,
    authenticated_actor_context,
)
from app.core.errors import ConflictError, ForbiddenError
from app.core.time import utc_now
from app.db.models.channel import ChannelWorkspace
from app.db.models.foundation import Company, DomainEvent, User
from app.db.models.workflow import ArtifactVersion
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.ops import DeadLetterJob, OpsIncident
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.services.outbox_dispatcher import DurableOutboxDispatcher
from app.services.production_publish import ProductionPublishService
from app.services.config_registry import ConfigRegistryService
from app.services.rbac import RBACService
from app.services.workflow import ArtifactService
from app.services.production_workflow import (
    CallableProductionStageHandler,
    ProductionStageHandlerRegistry,
    ProductionWorkflowCoordinator,
    ProductionWorkflowStage,
    WorkflowStageError,
    build_default_stage_handler_registry,
    handler_key_for,
)
from app.workers.production_workflow import ProductionWorkflowWorker


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _Clock:
    current: object

    def __call__(self):
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def _scope(session: Session):
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = Company(
        name=f"Phase 4 {uuid.uuid4().hex[:8]}",
        slug=f"phase4-{uuid.uuid4().hex[:12]}",
    )
    operator = User(
        email=f"phase4-{uuid.uuid4()}@example.com",
        display_name="Phase 4 Operator",
        status="active",
    )
    session.add_all([company, operator])
    session.flush()
    channel = ChannelWorkspace(
        company_id=company.id,
        key=f"phase4-{uuid.uuid4().hex[:8]}",
        name="Phase 4 Channel",
        status="active",
    )
    session.add(channel)
    session.flush()
    RBACService(session).assign_role(
        user_id=operator.id,
        role_key="owner_admin",
        company_id=company.id,
    )
    actor = authenticated_actor_context(
        canonical_user_id=operator.id,
        operator_user_id=operator.id,
        actor_role="OWNER_ADMIN",
        permissions={
            "production.start",
            "production.cancel",
            "production.read",
            "ops.manage",
        },
    )
    return company, channel, operator, actor


def _start_data(
    company: Company,
    channel: ChannelWorkspace,
    *,
    lane: ProductionLane = ProductionLane.LONG_FORM,
    source_id: uuid.UUID | None = None,
    max_attempts: int = 5,
) -> ProductionWorkflowStart:
    source_type = {
        ProductionLane.DAILY_SHORT: PlanningSourceType.DAILY_IDEA,
        ProductionLane.LONG_FORM: PlanningSourceType.LONG_FORM_PLAN,
        ProductionLane.LONG_DERIVED_SHORT: PlanningSourceType.DERIVED_SHORT,
    }[lane]
    values = {
        "company_id": company.id,
        "channel_workspace_id": channel.id,
        "production_lane": lane,
        "planning_source_type": source_type,
        "planning_source_id": source_id or uuid.uuid4(),
        "planning_source_hash": "a" * 64,
        "max_attempts": max_attempts,
    }
    if lane == ProductionLane.LONG_DERIVED_SHORT:
        values.update(
            {
                "parent_video_project_id": uuid.uuid4(),
                "canonical_media_timeline_ref": "artifact://timeline/1",
                "canonical_media_timeline_hash": "b" * 64,
            }
        )
    return ProductionWorkflowStart(**values)


def _start(
    session: Session,
    *,
    company: Company,
    channel: ChannelWorkspace,
    actor,
    data: ProductionWorkflowStart | None = None,
    now=utc_now,
):
    return ProductionWorkflowCoordinator(session, now=now).start(
        data=data or _start_data(company, channel),
        actor=actor,
    )


def _session_factory(engine):
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _worker_actor():
    return _system_worker_actor(
        "vcos-durable-worker",
        permissions={"production.workflow.execute"},
    )


def _success_handler(*, lane=ProductionLane.LONG_FORM, calls=None):
    key = handler_key_for(lane, ProductionWorkflowStage.PLANNING)

    def execute(context):
        if calls is not None:
            calls.append(context.command_id)
        return WorkflowStageResult(
            result_type="test_effect",
            result_ref=f"effect://{context.command_id}",
            result_hash="c" * 64,
            effect_state=WorkflowEffectState.COMPLETED,
        )

    return CallableProductionStageHandler(
        key=key,
        version="test.handler.v1",
        function=execute,
    )


def test_duplicate_start_reuses_same_workflow(db_session: Session) -> None:
    company, channel, _, actor = _scope(db_session)
    data = _start_data(company, channel)

    first = _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=data,
    )
    second = _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=data.model_copy(update={"idempotency_key": "retry-2"}),
    )

    assert second.id == first.id
    assert db_session.scalar(select(func.count(ProductionWorkflowRun.id))) == 1
    assert db_session.scalar(select(func.count(DomainEvent.id))) == 1


def test_duplicate_source_with_changed_semantics_fails_closed(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    source_id = uuid.uuid4()
    original = _start_data(company, channel, source_id=source_id)
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=original,
    )

    with pytest.raises(ConflictError):
        _start(
            db_session,
            company=company,
            channel=channel,
            actor=actor,
            data=original.model_copy(update={"planning_source_hash": "f" * 64}),
        )


def test_daily_short_event_never_resolves_long_form_handler(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=_start_data(company, channel, lane=ProductionLane.DAILY_SHORT),
    )
    event = db_session.scalar(
        select(DomainEvent).where(DomainEvent.workflow_run_id == run.id)
    )
    assert event is not None
    payload = WorkflowStageEventPayload.model_validate(event.payload)
    assert payload.handler_key.startswith("production.daily_short.")
    assert "long_form" not in payload.handler_key


def test_derived_short_contract_requires_exact_parent_timeline() -> None:
    with pytest.raises(ValidationError):
        ProductionWorkflowStart(
            company_id=uuid.uuid4(),
            channel_workspace_id=uuid.uuid4(),
            production_lane=ProductionLane.LONG_DERIVED_SHORT,
            planning_source_type=PlanningSourceType.DERIVED_SHORT,
            planning_source_id=uuid.uuid4(),
            planning_source_hash="a" * 64,
        )


def test_default_registry_is_lane_qualified_and_covers_every_stage() -> None:
    registry = build_default_stage_handler_registry()
    expected = {
        handler_key_for(lane, stage)
        for lane in ProductionLane
        for stage in ProductionWorkflowStage
    }
    assert registry.keys() == expected


def test_two_workers_cannot_claim_same_event(db_session: Session, engine) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(db_session, company=company, channel=channel, actor=actor)
    db_session.commit()
    factory = _session_factory(engine)
    first = factory()
    second = factory()
    try:
        claim = DurableOutboxDispatcher(first).claim_next(worker_id="worker-a")
        assert claim is not None
        assert DurableOutboxDispatcher(second).claim_next(worker_id="worker-b") is None
    finally:
        first.rollback()
        second.rollback()
        first.close()
        second.close()


def test_lease_expiry_allows_safe_reclaim_and_incident(
    db_session: Session, engine
) -> None:
    company, channel, _, actor = _scope(db_session)
    clock = _Clock(utc_now())
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        now=clock,
    )
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as first:
        original = DurableOutboxDispatcher(
            first, lease_seconds=10, max_execution_seconds=60, now=clock
        ).claim_next(worker_id="worker-a")
        first.commit()
    assert original is not None
    clock.advance(seconds=11)
    with factory() as second:
        reclaimed = DurableOutboxDispatcher(
            second, lease_seconds=10, max_execution_seconds=60, now=clock
        ).claim_next(worker_id="worker-b")
        second.commit()
    assert reclaimed is not None
    assert reclaimed.event_id == original.event_id
    with factory() as check:
        assert (
            check.scalar(
                select(func.count(OpsIncident.id)).where(
                    OpsIncident.incident_type == "WORKER_LEASE_EXPIRED"
                )
            )
            == 1
        )


def test_heartbeat_extends_lease_without_moving_execution_deadline(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    clock = _Clock(utc_now())
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        now=clock,
    )
    dispatcher = DurableOutboxDispatcher(
        db_session,
        lease_seconds=10,
        max_execution_seconds=60,
        now=clock,
    )
    claim = dispatcher.claim_next(worker_id="worker-a")
    assert claim is not None
    clock.advance(seconds=5)
    extended = dispatcher.heartbeat(event_id=claim.event_id, worker_id="worker-a")
    assert extended == clock.current + timedelta(seconds=10)
    event = db_session.get(DomainEvent, claim.event_id)
    assert event.metadata_["execution_deadline"] == (
        claim.execution_deadline.isoformat()
    )


def test_retryable_error_schedules_deterministic_backoff(
    db_session: Session, engine
) -> None:
    company, channel, _, actor = _scope(db_session)
    clock = _Clock(utc_now())
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        now=clock,
    )
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session, now=clock).claim_next(
            worker_id="worker-a"
        )
        claim_session.commit()
    assert claim is not None
    with factory() as failure_session:
        disposition = DurableOutboxDispatcher(
            failure_session, now=clock
        ).record_failure(
            event_id=claim.event_id,
            worker_id="worker-a",
            error=WorkflowStageError(
                classification=(WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY),
                error_code="TRANSIENT_FIXTURE",
                summary="temporary fixture failure",
            ),
        )
        failure_session.commit()
    assert disposition.retry_scheduled is True
    assert disposition.next_attempt_at == clock.current + timedelta(seconds=5)


def test_permanent_integrity_error_fails_closed(db_session: Session, engine) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(db_session, company=company, channel=channel, actor=actor)
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session).claim_next(worker_id="worker-a")
        claim_session.commit()
    assert claim is not None
    with factory() as failure_session:
        disposition = DurableOutboxDispatcher(failure_session).record_failure(
            event_id=claim.event_id,
            worker_id="worker-a",
            error=WorkflowStageError(
                classification=(WorkflowFailureClassification.FAIL_PERMANENT_INTEGRITY),
                error_code="FIXTURE_HASH_MISMATCH",
                summary="fixture hash mismatch",
                incident_type="INTEGRITY_MISMATCH",
            ),
        )
        failure_session.commit()
    assert disposition.retry_scheduled is False
    with factory() as check:
        persisted = check.get(ProductionWorkflowRun, run.id)
        assert persisted is not None
        assert persisted.state == "FAILED_TERMINAL"


def test_retry_exhaustion_creates_one_dead_letter_and_incident(
    db_session: Session, engine
) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=_start_data(company, channel, max_attempts=1),
    )
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session).claim_next(worker_id="worker-a")
        claim_session.commit()
    assert claim is not None
    error = WorkflowStageError(
        classification=(WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY),
        error_code="TRANSIENT_EXHAUSTED",
        summary="transient retry budget exhausted",
        retry_eligible=True,
    )
    with factory() as failure_session:
        first = DurableOutboxDispatcher(failure_session).record_failure(
            event_id=claim.event_id,
            worker_id="worker-a",
            error=error,
        )
        failure_session.commit()
    assert first.dead_letter_job_id is not None
    with factory() as check:
        assert check.scalar(select(func.count(DeadLetterJob.id))) == 1
        assert check.scalar(select(func.count(OpsIncident.id))) == 1


def test_explicit_retryable_dead_letter_reuses_same_command(
    db_session: Session, engine
) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        data=_start_data(company, channel, max_attempts=1),
    )
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session).claim_next(worker_id="worker-a")
        claim_session.commit()
    assert claim is not None
    with factory() as failure_session:
        disposition = DurableOutboxDispatcher(failure_session).record_failure(
            event_id=claim.event_id,
            worker_id="worker-a",
            error=WorkflowStageError(
                classification=(WorkflowFailureClassification.AUTO_RETRY_WITHIN_POLICY),
                error_code="TRANSIENT_EXHAUSTED",
                summary="try once under operator authority",
                retry_eligible=True,
            ),
        )
        failure_session.commit()
    assert disposition.dead_letter_job_id is not None
    with factory() as replay_session:
        replay = DurableOutboxDispatcher(replay_session).retry_dead_letter(
            dead_letter_job_id=disposition.dead_letter_job_id,
            company_id=company.id,
            data=DeadLetterRetryRequest(),
            actor=actor,
        )
        replay_session.commit()
    assert replay.command_id == claim.command_id
    assert replay.domain_event_id == claim.event_id


def test_cancel_suppresses_unleased_future_events(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(db_session, company=company, channel=channel, actor=actor)
    result, uncertain = ProductionWorkflowCoordinator(db_session).cancel(
        workflow_run_id=run.id,
        company_id=company.id,
        data=ProductionWorkflowCancel(reason="operator stopped fixture"),
        actor=actor,
    )
    event = db_session.scalar(
        select(DomainEvent).where(DomainEvent.workflow_run_id == run.id)
    )
    assert result.state.value == "CANCELED"
    assert uncertain == []
    assert event is not None and event.delivered_at is not None


def test_cancel_with_in_flight_effect_creates_incident(
    db_session: Session, engine
) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(db_session, company=company, channel=channel, actor=actor)
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session).claim_next(worker_id="worker-a")
        claim_session.commit()
    assert claim is not None
    with factory() as cancel_session:
        result, uncertain = ProductionWorkflowCoordinator(cancel_session).cancel(
            workflow_run_id=run.id,
            company_id=company.id,
            data=ProductionWorkflowCancel(reason="stop in-flight fixture"),
            actor=actor,
        )
        persisted = cancel_session.get(ProductionWorkflowRun, run.id)
        assert persisted is not None
        incidents = DurableOutboxDispatcher(
            cancel_session
        ).record_cancellation_uncertainty(
            run=persisted,
            events=uncertain,
        )
        cancel_session.commit()
    assert result.state.value == "CANCELED"
    assert len(incidents) == 1
    assert incidents[0].incident_type == "CANCELED_WITH_IN_FLIGHT_EFFECT"


def test_cancel_after_handler_intent_commit_cannot_advance_stale_projection(
    db_session: Session,
    engine,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run_read = _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
    )
    db_session.commit()
    factory = _session_factory(engine)
    cancellation_observation: dict[str, object] = {}

    def effect_with_concurrent_cancel(context):
        # Model the native adapter's durable effect-intent crash boundary.
        context.session.commit()
        with factory() as cancel_session:
            canceled, uncertain = ProductionWorkflowCoordinator(cancel_session).cancel(
                workflow_run_id=context.run.id,
                company_id=context.run.company_id,
                data=ProductionWorkflowCancel(
                    reason="cancel after durable effect intent"
                ),
                actor=actor,
            )
            cancellation_observation["state"] = canceled.state.value
            cancellation_observation["uncertain_event_ids"] = [
                item.id for item in uncertain
            ]
            cancel_session.commit()
        return WorkflowStageResult(
            result_type="effect_completed_after_cancel",
            result_ref=f"effect://{context.command_id}",
            result_hash="c" * 64,
            effect_state=WorkflowEffectState.COMPLETED,
        )

    handler = CallableProductionStageHandler(
        key=handler_key_for(
            ProductionLane.LONG_FORM,
            ProductionWorkflowStage.PLANNING,
        ),
        version="test.cancel-after-intent.v1",
        function=effect_with_concurrent_cancel,
    )
    worker = ProductionWorkflowWorker(
        handlers=ProductionStageHandlerRegistry([handler]),
        session_factory=factory,
        worker_id="worker-cancel-after-intent",
        lease_seconds=30,
        max_execution_seconds=120,
        heartbeat_interval_seconds=5,
    )

    result = worker.run_once()

    assert result.status == "CANCELED"
    assert cancellation_observation == {
        "state": "CANCELED",
        "uncertain_event_ids": [result.event_id],
    }
    with factory() as check:
        persisted_run = check.get(ProductionWorkflowRun, run_read.id)
        events = list(
            check.scalars(
                select(DomainEvent)
                .where(DomainEvent.workflow_run_id == run_read.id)
                .order_by(DomainEvent.created_at, DomainEvent.id)
            ).all()
        )
        assert persisted_run is not None
        assert persisted_run.state == "CANCELED"
        assert persisted_run.current_stage == "PLANNING"
        assert len(events) == 1
        assert events[0].id == result.event_id
        assert events[0].last_error_code == "WORKFLOW_CANCELED"
        assert events[0].delivered_at is not None
        assert (
            check.scalar(
                select(func.count(WorkflowCommandReceipt.id)).where(
                    WorkflowCommandReceipt.workflow_run_id == run_read.id
                )
            )
            == 0
        )
        assert (
            check.scalar(
                select(func.count(DeadLetterJob.id)).where(
                    DeadLetterJob.workflow_run_id == run_read.id
                )
            )
            == 0
        )


def test_human_cannot_execute_trusted_worker_stage(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(db_session, company=company, channel=channel, actor=actor)
    dispatcher = DurableOutboxDispatcher(db_session)
    claim = dispatcher.claim_next(worker_id="worker-a")
    assert claim is not None
    event = dispatcher.require_claimed_event(
        event_id=claim.event_id, worker_id="worker-a"
    )
    registry = ProductionStageHandlerRegistry([_success_handler()])
    with pytest.raises(ForbiddenError):
        ProductionWorkflowCoordinator(db_session, handlers=registry).execute_event(
            event=event,
            actor=actor,
            heartbeat=lambda: None,
            max_execution_seconds=60,
        )
    assert run.state.value == "PLANNING_PENDING"


@pytest.mark.parametrize(
    "effect_type",
    [
        "provider_submission",
        "budget_reservation",
        "budget_settlement",
        "render_output",
        "archive_object",
    ],
)
def test_crash_after_effect_before_ack_does_not_duplicate_effect(
    db_session: Session,
    engine,
    effect_type: str,
) -> None:
    company, channel, _, actor = _scope(db_session)
    clock = _Clock(utc_now())
    _start(
        db_session,
        company=company,
        channel=channel,
        actor=actor,
        now=clock,
    )
    db_session.commit()
    effects: dict[str, str] = {}
    handler_calls: list[str] = []

    def idempotent_effect(context):
        handler_calls.append(context.command_id)
        effects.setdefault(
            context.command_id,
            f"{effect_type}://{uuid.uuid4()}",
        )
        return WorkflowStageResult(
            result_type=effect_type,
            result_ref=effects[context.command_id],
            result_hash="d" * 64,
        )

    handler = CallableProductionStageHandler(
        key=handler_key_for(ProductionLane.LONG_FORM, ProductionWorkflowStage.PLANNING),
        version="test.idempotent.v1",
        function=idempotent_effect,
    )
    registry = ProductionStageHandlerRegistry([handler])
    crash_once = {"value": True}

    def crash_after_effect(_claim):
        if crash_once["value"]:
            crash_once["value"] = False
            raise RuntimeError("simulated crash after external effect")

    worker = ProductionWorkflowWorker(
        handlers=registry,
        session_factory=_session_factory(engine),
        worker_id="worker-crash-test",
        lease_seconds=30,
        max_execution_seconds=120,
        heartbeat_interval_seconds=5,
        after_stage_before_ack=crash_after_effect,
        now=clock,
    )
    first = worker.run_once()
    assert first.status == "RETRY_SCHEDULED"
    clock.advance(seconds=5)
    second = worker.run_once()
    assert second.status == "DELIVERED"
    assert len(effects) == 1
    assert len(handler_calls) == 2
    assert handler_calls[0] == handler_calls[1]


def test_command_receipt_is_immutable(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(db_session, company=company, channel=channel, actor=actor)
    dispatcher = DurableOutboxDispatcher(db_session)
    claim = dispatcher.claim_next(worker_id="worker-a")
    assert claim is not None
    event = dispatcher.require_claimed_event(
        event_id=claim.event_id, worker_id="worker-a"
    )
    coordinator = ProductionWorkflowCoordinator(
        db_session,
        handlers=ProductionStageHandlerRegistry([_success_handler()]),
    )
    receipt = coordinator.execute_event(
        event=event,
        actor=_worker_actor(),
        heartbeat=lambda: None,
        max_execution_seconds=60,
    )
    dispatcher.mark_delivered(event_id=event.id, worker_id="worker-a")
    db_session.flush()
    receipt.result_type = "mutated"
    with pytest.raises(RuntimeError, match="WORKFLOW_COMMAND_RECEIPT_IMMUTABLE"):
        db_session.flush()
    db_session.rollback()


def test_workflow_projection_reconciles_from_immutable_receipt(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run_read = _start(db_session, company=company, channel=channel, actor=actor)
    dispatcher = DurableOutboxDispatcher(db_session)
    claim = dispatcher.claim_next(worker_id="worker-reconcile")
    assert claim is not None
    event = dispatcher.require_claimed_event(
        event_id=claim.event_id,
        worker_id="worker-reconcile",
    )
    coordinator = ProductionWorkflowCoordinator(
        db_session,
        handlers=ProductionStageHandlerRegistry([_success_handler()]),
    )
    coordinator.execute_event(
        event=event,
        actor=_worker_actor(),
        heartbeat=lambda: None,
        max_execution_seconds=60,
    )
    dispatcher.mark_delivered(
        event_id=event.id,
        worker_id="worker-reconcile",
    )
    run = db_session.get(ProductionWorkflowRun, run_read.id)
    assert run is not None
    run.state = "QC_RUNNING"
    run.current_stage = "QC"
    run.state_reason_codes = ["CORRUPTED_FIXTURE_PROJECTION"]
    db_session.flush()

    reconciled = coordinator.reconcile(
        workflow_run_id=run.id,
        company_id=company.id,
        actor=_worker_actor(),
    )

    assert reconciled.state.value == "PLANNING_PENDING"
    assert reconciled.current_stage.value == "PREFLIGHT"
    assert (
        db_session.scalar(
            select(func.count(WorkflowCommandReceipt.id)).where(
                WorkflowCommandReceipt.workflow_run_id == run.id
            )
        )
        == 1
    )
    assert (
        db_session.scalar(
            select(func.count(DomainEvent.id)).where(
                DomainEvent.workflow_run_id == run.id
            )
        )
        == 2
    )


def test_one_action_and_worker_restart_complete_trusted_fixture_flow(
    db_session: Session,
    engine,
) -> None:
    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    scope = phase3["_scope"](db_session)
    package = phase3["_create_package"](db_session, scope)
    package_version = db_session.get(ArtifactVersion, package.artifact_version_id)
    assert package_version is not None
    package_content = ProductionPackageContentV2.model_validate(package_version.content)
    destination_version = db_session.get(
        ArtifactVersion,
        package_content.destination_binding_ref.artifact_version_id,
    )
    assert destination_version is not None
    destination = destination_version.content["destination"]
    actor = authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="OWNER_ADMIN",
        permissions={
            "production.start",
            "production.cancel",
            "production.read",
            "ops.manage",
        },
    )
    destination_id = destination_version.id
    destination_fingerprint = destination_version.content_hash
    render_checksum = "3" * 64
    calls: dict[str, list[str]] = {
        stage.value: []
        for stage in (
            ProductionWorkflowStage.MEDIA,
            ProductionWorkflowStage.RENDER,
            ProductionWorkflowStage.QC,
            ProductionWorkflowStage.ARCHIVE,
            ProductionWorkflowStage.FINALIZE,
        )
    }

    def media(context):
        calls["MEDIA"].append(context.command_id)
        return WorkflowStageResult(
            result_type="canonical_media_timeline",
            result_ref="fixture://phase4/canonical-media-timeline",
            result_hash="1" * 64,
            authority_refs=WorkflowAuthorityRefs(
                canonical_media_timeline_ref=(
                    "fixture://phase4/canonical-media-timeline"
                ),
                canonical_media_timeline_hash="1" * 64,
            ),
        )

    def render(context):
        calls["RENDER"].append(context.command_id)
        return WorkflowStageResult(
            result_type="native_render_output",
            result_ref="fixture://phase4/final.mp4",
            result_hash=render_checksum,
            authority_refs=WorkflowAuthorityRefs(
                native_render_plan_ref="fixture://phase4/native-render-plan",
                native_render_plan_hash="2" * 64,
                render_output_ref="fixture://phase4/final.mp4",
                render_output_checksum=render_checksum,
            ),
        )

    def qc(context):
        calls["QC"].append(context.command_id)
        return WorkflowStageResult(
            result_type="automated_media_qc",
            result_ref="fixture://phase4/creative-qc",
            result_hash="5" * 64,
            authority_refs=WorkflowAuthorityRefs(
                technical_qc_receipt_ref="fixture://phase4/technical-qc",
                technical_qc_receipt_hash="4" * 64,
                creative_qc_receipt_ref="fixture://phase4/creative-qc",
                creative_qc_receipt_hash="5" * 64,
            ),
        )

    def archive(context):
        calls["ARCHIVE"].append(context.command_id)
        project = context.session.get(type(scope.project), context.run.video_project_id)
        assert project is not None
        archive_receipt_hash = "6" * 64
        cloud = CloudMediaRef(
            company_id=context.run.company_id,
            channel_workspace_id=context.run.channel_workspace_id,
            video_project_id=project.id,
            media_type="LONG_FORM_FINAL",
            storage_provider="GOOGLE_DRIVE",
            drive_file_id=f"fixture-{context.command_id}",
            drive_folder_id="fixture-phase4",
            web_view_link="https://drive.invalid/phase4-final",
            mime_type="video/mp4",
            file_name="final.mp4",
            size_bytes=1024,
            checksum_sha256=render_checksum,
            local_source_path_hash=render_checksum,
            upload_status="VERIFIED",
            verification_status="CHECKSUM_VERIFIED",
            retention_policy={"fixture": True},
            source_refs=[
                {
                    "type": "archive_receipt",
                    "ref": (f"fixture://phase4/archive-receipt#{archive_receipt_hash}"),
                }
            ],
            technical_appendix={
                "transport": "DETERMINISTIC_TEST_CLIENT",
                "archive_receipt_hash": archive_receipt_hash,
            },
        )
        context.session.add(cloud)
        context.session.flush()
        duration_ms = int((project.duration_contract or {})["target_duration_ms"])
        archive_object_ref = f"drive://{cloud.drive_file_id}/final.mp4"
        lineage_artifact = ArtifactService(context.session).create_artifact(
            data=ArtifactCreate(
                video_project_id=project.id,
                artifact_type="mr1_final_media_lineage_receipt",
                created_by_user_id=project.created_by_user_id,
            ),
            correlation_id=f"phase4-lineage-{context.command_id}",
            trusted_authority_write=True,
        )
        lineage_version = ArtifactService(context.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=lineage_artifact.id,
                content={
                    "schema_version": "vcos.native-final-media-lineage.v2",
                    "video_project_id": str(project.id),
                    "production_package_artifact_version_id": str(
                        context.run.production_package_artifact_version_id
                    ),
                    "production_package_hash": (context.run.production_package_hash),
                    "duration_contract": project.duration_contract,
                    "canonical_media_timeline_hash": "1" * 64,
                    "native_render_plan_hash": "2" * 64,
                    "render_output_checksum": render_checksum,
                    "technical_qc_hash": "4" * 64,
                    "creative_qc_hash": "5" * 64,
                    "archive_receipt_hash": archive_receipt_hash,
                    "archive_state": "VERIFIED",
                    "cloud_media_ref_id": str(cloud.id),
                    "file_ref": archive_object_ref,
                },
                status="approved",
                created_by_user_id=project.created_by_user_id,
            ),
            correlation_id=f"phase4-lineage-version-{context.command_id}",
            trusted_authority_write=True,
        )
        lineage_artifact.status = "approved"
        context.session.flush()
        final_media = FinalMediaRef(
            company_id=context.run.company_id,
            channel_workspace_id=context.run.channel_workspace_id,
            video_project_id=project.id,
            production_package_artifact_version_id=(
                context.run.production_package_artifact_version_id
            ),
            production_package_hash=context.run.production_package_hash,
            duration_contract=project.duration_contract,
            media_type="LONG_FORM_FINAL",
            file_ref=archive_object_ref,
            duration_seconds=Decimal(duration_ms) / Decimal(1000),
            aspect_ratio="16:9",
            resolution="1920x1080",
            provider_key="native-ffmpeg",
            provider_type="LOCAL_RENDERER_CAPABILITY",
            checksum_sha256=render_checksum,
            cloud_media_ref_id=cloud.id,
            lineage_artifact_version_id=lineage_version.id,
        )
        context.session.add(final_media)
        context.session.flush()
        return WorkflowStageResult(
            result_type="verified_archive",
            result_id=final_media.id,
            result_ref=archive_object_ref,
            result_hash=archive_receipt_hash,
            authority_refs=WorkflowAuthorityRefs(
                archive_receipt_ref="fixture://phase4/archive-receipt",
                archive_receipt_hash=archive_receipt_hash,
                archive_object_ref=archive_object_ref,
                archive_verification_state="VERIFIED",
                final_media_ref_id=final_media.id,
                final_media_ref_hash=render_checksum,
                destination_binding_id=destination_id,
                destination_binding_fingerprint=destination_fingerprint,
                destination_binding={
                    "platform": destination["platform"],
                    "platform_channel_id": destination["platform_channel_id"],
                    "account_identity": destination["platform_account_ref"],
                },
            ),
        )

    def finalize(context):
        calls["FINALIZE"].append(context.command_id)
        run = context.run
        project = context.session.get(type(scope.project), run.video_project_id)
        assert project is not None
        candidate = ProductionPublishService(
            context.session
        ).create_final_review_candidate(
            FinalReviewCandidateCreateV2(
                workflow_run_id=run.id,
                production_package_artifact_version_id=(
                    run.production_package_artifact_version_id
                ),
                production_package_hash=run.production_package_hash,
                production_readiness_receipt_artifact_version_id=(
                    run.production_readiness_receipt_artifact_version_id
                ),
                production_readiness_receipt_hash=(
                    run.production_readiness_receipt_hash
                ),
                canonical_media_timeline_ref=run.canonical_media_timeline_ref,
                canonical_media_timeline_hash=run.canonical_media_timeline_hash,
                native_render_plan_ref=run.native_render_plan_ref,
                native_render_plan_hash=run.native_render_plan_hash,
                render_output_ref=run.render_output_ref,
                render_output_checksum=run.render_output_checksum,
                technical_qc_receipt_ref=run.technical_qc_receipt_ref,
                technical_qc_receipt_hash=run.technical_qc_receipt_hash,
                technical_qc_state="PASS",
                creative_qc_receipt_ref=run.creative_qc_receipt_ref,
                creative_qc_receipt_hash=run.creative_qc_receipt_hash,
                creative_qc_state="PASS",
                archive_receipt_ref=run.archive_receipt_ref,
                archive_receipt_hash=run.archive_receipt_hash,
                archive_object_ref=run.archive_object_ref,
                archive_verification_state="VERIFIED",
                final_media_ref_id=run.final_media_ref_id,
                destination_binding_id=run.destination_binding_id,
                destination_binding_fingerprint=(run.destination_binding_fingerprint),
                destination_platform_channel_id=(destination["platform_channel_id"]),
                destination_account_identity=(destination["platform_account_ref"]),
                target_platform="YOUTUBE",
                target_surface="LONG_FORM",
                target_market_lineage={
                    "profile_hash": scope.profile.profile_input_hash,
                    "policy_hash": scope.policy.content_hash,
                },
                publish_metadata_snapshot={
                    "title": project.title,
                    "description": project.description,
                    "privacy_status": "PRIVATE",
                    "thumbnail_required": True,
                    "caption_required": True,
                },
                disclosure_snapshot={
                    "ai_disclosure_confirmed": True,
                    "rights_confirmed": True,
                },
            )
        )
        return WorkflowStageResult(
            result_type="final_review_candidate",
            result_id=candidate.id,
            result_ref=f"final-review-candidate://{candidate.id}",
            result_hash=candidate.candidate_hash,
            authority_refs=WorkflowAuthorityRefs(
                final_review_candidate_id=candidate.id,
                final_review_candidate_hash=candidate.candidate_hash,
            ),
        )

    registry = build_default_stage_handler_registry()
    for stage, function in (
        (ProductionWorkflowStage.MEDIA, media),
        (ProductionWorkflowStage.RENDER, render),
        (ProductionWorkflowStage.QC, qc),
        (ProductionWorkflowStage.ARCHIVE, archive),
        (ProductionWorkflowStage.FINALIZE, finalize),
    ):
        registry.replace(
            CallableProductionStageHandler(
                key=handler_key_for(ProductionLane.LONG_FORM, stage),
                version=f"test.phase4.{stage.value.lower()}.v1",
                function=function,
            )
        )

    started = ProductionWorkflowCoordinator(
        db_session,
        handlers=registry,
    ).start_from_project(
        video_project_id=scope.project.id,
        company_id=scope.company.id,
        data=ProductionWorkflowProjectStart(idempotency_key="one-operator-action"),
        actor=actor,
    )
    db_session.commit()
    factory = _session_factory(engine)
    first_worker = ProductionWorkflowWorker(
        handlers=registry,
        session_factory=factory,
        worker_id="phase4-fixture-worker-before-restart",
    )
    for _ in range(7):
        assert first_worker.run_once().status == "DELIVERED"

    second_worker = ProductionWorkflowWorker(
        handlers=registry,
        session_factory=factory,
        worker_id="phase4-fixture-worker-after-restart",
    )
    for _ in range(4):
        result = second_worker.run_once()
        if result.status != "DELIVERED":
            with factory() as failure_check:
                failed_event = failure_check.get(DomainEvent, result.event_id)
                pytest.fail(
                    "fixture stage did not complete after restart: "
                    f"{result.status}:"
                    f"{failed_event.last_error_code if failed_event else None}:"
                    f"{failed_event.last_error_summary if failed_event else None}:"
                    f"calls={calls}"
                )
    assert second_worker.run_once().status == "IDLE"

    with factory() as check:
        run = check.get(ProductionWorkflowRun, started.id)
        assert run is not None
        assert run.state == "FINAL_REVIEW_READY"
        assert run.current_stage == "FINALIZE"
        assert run.production_package_artifact_version_id == (
            package.artifact_version_id
        )
        candidate = check.get(FinalReviewCandidate, run.final_review_candidate_id)
        assert candidate is not None
        assert candidate.production_lane == scope.project.production_lane
        assert candidate.content_mode == scope.project.content_mode
        assert candidate.series_plan_id == scope.project.series_plan_id
        assert candidate.series_run_id == scope.project.series_run_id
        assert candidate.episode_number == scope.project.episode_number
        assert candidate.standalone_reason_code == (
            scope.project.standalone_reason_code
        )
        assert (
            check.scalar(
                select(func.count(WorkflowCommandReceipt.id)).where(
                    WorkflowCommandReceipt.workflow_run_id == run.id
                )
            )
            == 11
        )
        assert (
            check.scalar(
                select(func.count(DomainEvent.id)).where(
                    DomainEvent.workflow_run_id == run.id,
                    DomainEvent.delivered_at.is_not(None),
                )
            )
            == 11
        )
    assert all(len(command_ids) == 1 for command_ids in calls.values())


def test_resume_reuses_existing_event_and_command(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    run = _start(db_session, company=company, channel=channel, actor=actor)
    event = db_session.scalar(
        select(DomainEvent).where(DomainEvent.workflow_run_id == run.id)
    )
    assert event is not None
    original_command = event.command_id
    event.delivered_at = utc_now()
    event.published_at = event.delivered_at
    persisted = db_session.get(ProductionWorkflowRun, run.id)
    assert persisted is not None
    persisted.state = "BLOCKED"
    db_session.flush()

    resumed = ProductionWorkflowCoordinator(db_session).resume(
        workflow_run_id=run.id,
        company_id=company.id,
        data=ProductionWorkflowResume(reason_code="FIXTURE_RESUME"),
        actor=actor,
    )
    assert resumed.state.value == "PLANNING_PENDING"
    assert event.command_id == original_command
    assert event.delivered_at is None
    assert event.published_at is None


def test_worker_shutdown_releases_owned_lease(db_session: Session, engine) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(db_session, company=company, channel=channel, actor=actor)
    db_session.commit()
    factory = _session_factory(engine)
    with factory() as claim_session:
        claim = DurableOutboxDispatcher(claim_session).claim_next(
            worker_id="worker-shutdown"
        )
        claim_session.commit()
    assert claim is not None
    worker = ProductionWorkflowWorker(
        handlers=ProductionStageHandlerRegistry(),
        session_factory=factory,
        worker_id="worker-shutdown",
    )
    assert worker.release_leases() == 1
    with factory() as check:
        event = check.get(DomainEvent, claim.event_id)
        assert event is not None
        assert event.lease_owner is None
        assert event.next_attempt_at is not None


def test_company_scoped_list_does_not_leak_other_company(
    db_session: Session,
) -> None:
    first_company, first_channel, _, first_actor = _scope(db_session)
    second_company, second_channel, _, second_actor = _scope(db_session)
    first = _start(
        db_session,
        company=first_company,
        channel=first_channel,
        actor=first_actor,
    )
    _start(
        db_session,
        company=second_company,
        channel=second_channel,
        actor=second_actor,
    )
    result = ProductionWorkflowCoordinator(db_session).list(
        company_id=first_company.id,
        actor=first_actor,
        view="all",
    )
    assert [item.id for item in result.items] == [first.id]


def test_sensitive_handler_payload_is_rejected_without_receipt(
    db_session: Session,
) -> None:
    company, channel, _, actor = _scope(db_session)
    _start(db_session, company=company, channel=channel, actor=actor)
    dispatcher = DurableOutboxDispatcher(db_session)
    claim = dispatcher.claim_next(worker_id="worker-a")
    assert claim is not None
    event = dispatcher.require_claimed_event(
        event_id=claim.event_id, worker_id="worker-a"
    )

    def unsafe(_context):
        return WorkflowStageResult(
            result_type="unsafe",
            result_payload={"api_key": "must-not-persist"},
        )

    registry = ProductionStageHandlerRegistry(
        [
            CallableProductionStageHandler(
                key=handler_key_for(
                    ProductionLane.LONG_FORM,
                    ProductionWorkflowStage.PLANNING,
                ),
                version="test.unsafe.v1",
                function=unsafe,
            )
        ]
    )
    with pytest.raises(WorkflowStageError) as captured:
        ProductionWorkflowCoordinator(db_session, handlers=registry).execute_event(
            event=event,
            actor=_worker_actor(),
            heartbeat=lambda: None,
            max_execution_seconds=60,
        )
    assert captured.value.error_code == "WORKFLOW_RECEIPT_SENSITIVE_DATA_FORBIDDEN"
    assert db_session.scalar(select(func.count(WorkflowCommandReceipt.id))) == 0
