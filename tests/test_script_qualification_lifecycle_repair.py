from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.contracts.launch_cadence import CadenceDecision, CadenceEvaluationCommand
from app.core.actor import _system_worker_actor
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import EditorialIdeaCandidate
from app.db.models.script_qualification import (
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationRun,
)
from app.services.launch_cadence import LongFormCadenceService
from app.services.script_qualification_background import (
    ScriptQualificationBackgroundService,
    build_script_qualification_deadline_policy,
    derive_script_qualification_deadline,
    minimum_script_qualification_window_close_at,
    script_qualification_slot_is_viable,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_long_form_launch_cadence import (
    _active_launch_run,
    _actor,
    _approved_launch_policy,
    _greenlit_candidate,
    _ready_provider_snapshot,
    _test_support_authority_preparer,
)


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


class _NoProviderCalls:
    def submit_background(self, **_kwargs):
        raise AssertionError("deadline settlement must not submit to a provider")

    def retrieve_background(self, **_kwargs):
        raise AssertionError("deadline settlement must not poll a provider")


def _cadence(
    db_session,
    qualification_factory,
    *,
    now: datetime,
    name: str,
):
    scope = qualification_factory.channel_scope(name=name, strict_long_form=True)
    policy, admin_actor, _ = _approved_launch_policy(
        db_session,
        scope,
        timezone_name="UTC",
        weekdays=["TUESDAY"],
    )
    launch = _active_launch_run(
        db_session,
        policy,
        admin_actor,
        started_on=date(2026, 7, 20),
    )
    _, candidate, _ = _greenlit_candidate(
        db_session,
        scope,
        _actor(db_session, scope),
    )
    service = LongFormCadenceService(
        db_session,
        now=lambda: now,
        provider_readiness_snapshot=_ready_provider_snapshot,
        support_authority_preparer=_test_support_authority_preparer,
    )
    return service, launch, candidate


def _reserve_viable_qualification(
    db_session,
    qualification_factory,
    *,
    evaluation_key: str,
):
    now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
    cadence, launch, candidate = _cadence(
        db_session,
        qualification_factory,
        now=now,
        name=evaluation_key,
    )
    receipt = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key=evaluation_key),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )
    assert receipt.decision == CadenceDecision.START_SCRIPT_QUALIFICATION
    assert receipt.script_qualification_run_id is not None
    run = db_session.get(
        ScriptQualificationRun,
        receipt.script_qualification_run_id,
    )
    slot = db_session.get(LongFormPublishSlot, receipt.publish_slot_id)
    assert run is not None
    assert slot is not None
    assert run.logical_deadline_at == derive_script_qualification_deadline(
        slot.target_start_window_close_at
    )
    return run, slot, candidate


def test_deadline_policy_and_cadence_reject_the_last_window(
    db_session,
    qualification_factory,
) -> None:
    configured = SimpleNamespace(
        openai_background_submit_timeout_seconds=15,
        openai_background_poll_request_timeout_seconds=10,
        script_qualification_background_poll_seconds=15,
    )
    deadline_policy = build_script_qualification_deadline_policy(configured)
    assert deadline_policy.provider_stage_count == 2
    assert deadline_policy.poll_cycles_per_stage == 60
    assert deadline_policy.provider_stage_budget_seconds == 1_575
    assert deadline_policy.total_qualification_budget_seconds == 3_450
    assert deadline_policy.receipt()["poll_cycles_per_stage"] == 60

    now = datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    exclusive_close_boundary = minimum_script_qualification_window_close_at(
        now,
        deadline_policy,
    )
    assert not script_qualification_slot_is_viable(
        now,
        exclusive_close_boundary,
        deadline_policy,
    )
    assert script_qualification_slot_is_viable(
        now,
        exclusive_close_boundary + timedelta(microseconds=1),
        deadline_policy,
    )

    cadence, launch, candidate = _cadence(
        db_session,
        qualification_factory,
        now=now,
        name="Qualification last window",
    )
    receipt = cadence.evaluate(
        launch_run_id=launch.id,
        data=CadenceEvaluationCommand(evaluation_key="last-window-rejected"),
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert receipt.decision == CadenceDecision.WAIT_OUTSIDE_PRODUCTION_HORIZON
    assert receipt.script_qualification_run_id is None
    assert receipt.selected_candidate_id is None
    assert candidate.stage == "GREENLIT"
    assert db_session.scalar(select(func.count(ScriptQualificationRun.id))) == 0
    assert (
        db_session.scalar(
            select(func.count(LongFormPublishSlot.id)).where(
                LongFormPublishSlot.state == "QUALIFICATION_RESERVED"
            )
        )
        == 0
    )


def test_pre_provider_deadline_settles_candidate_and_slot_without_orphan(
    db_session,
    qualification_factory,
) -> None:
    run, slot, candidate = _reserve_viable_qualification(
        db_session,
        qualification_factory,
        evaluation_key="deterministic-deadline-settlement",
    )
    assert run.logical_deadline_at is not None

    result = ScriptQualificationBackgroundService(
        db_session,
        now=lambda: run.logical_deadline_at,
        provider=_NoProviderCalls(),
    ).execute(run.id)

    assert result.state == "BLOCKED_NON_REPAIRABLE"
    assert slot.state == "CANCELED"
    assert candidate.stage == "REJECTED"
    assert result.terminal_settlement_receipt is not None
    assert result.terminal_settlement_receipt["kind"] == "DETERMINISTIC_BLOCK"
    assert result.terminal_settlement_receipt["capacity_released"] is True
    assert (
        db_session.scalar(
            select(func.count(LongFormPublishSlot.id)).where(
                LongFormPublishSlot.state == "QUALIFICATION_RESERVED"
            )
        )
        == 0
    )
    assert (
        db_session.scalar(
            select(func.count(EditorialIdeaCandidate.id)).where(
                EditorialIdeaCandidate.stage == "GREENLIT",
                EditorialIdeaCandidate.id == candidate.id,
            )
        )
        == 0
    )


def test_in_flight_deadline_preserves_unknown_outcome_and_closes_orphan(
    db_session,
    qualification_factory,
) -> None:
    run, slot, candidate = _reserve_viable_qualification(
        db_session,
        qualification_factory,
        evaluation_key="unknown-deadline-settlement",
    )
    assert run.logical_deadline_at is not None
    attempt = ScriptQualificationBackgroundAttempt(
        script_qualification_run_id=run.id,
        phase="WRITER",
        provider="OPENAI",
        model=run.model,
        lane="long_context_text",
        task="long_form_script",
        input_fingerprint="a" * 64,
        immutable_input_hashes={},
        client_correlation_id=f"{run.writer_attempt_key}:accepted-unknown",
        provider_response_id="resp-accepted-outcome-unknown",
        background_status="IN_PROGRESS",
        logical_deadline_at=run.logical_deadline_at,
        poll_count=1,
        submission_attempt_count=1,
    )
    db_session.add(attempt)
    db_session.flush()

    result = ScriptQualificationBackgroundService(
        db_session,
        now=lambda: run.logical_deadline_at,
        provider=_NoProviderCalls(),
    ).execute(run.id)

    assert result.state == "BLOCKED_NON_REPAIRABLE"
    assert attempt.background_status == "DEADLINE_EXCEEDED"
    assert slot.state == "QUALIFICATION_RECONCILIATION_REQUIRED"
    assert candidate.stage == "REJECTED"
    assert (
        "SCRIPT_PROVIDER_OUTCOME_UNKNOWN_NO_RETRY"
        in result.failure_receipt["reason_codes"]
    )
    assert result.terminal_settlement_receipt is not None
    assert result.terminal_settlement_receipt["kind"] == "UNKNOWN_PROVIDER_OUTCOME"
    assert result.terminal_settlement_receipt["capacity_released"] is False
    assert (
        db_session.scalar(
            select(func.count(LongFormPublishSlot.id)).where(
                LongFormPublishSlot.state == "QUALIFICATION_RESERVED"
            )
        )
        == 0
    )
