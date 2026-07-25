from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    Company,
    CompiledChannelPolicySnapshot,
    CostEvent,
    MR1MonthlyBudgetReservation,
    User,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.mr1_monthly_budget import MR1MonthlyBudgetAuthority


def _channel(session, *, company: Company, key: str) -> SimpleNamespace:
    channel = ChannelWorkspace(
        company_id=company.id,
        key=key,
        name=key,
        status="active",
        primary_language="en",
        primary_timezone="UTC",
        default_timezone="UTC",
    )
    session.add(channel)
    session.flush()
    profile_payload = {"source": "mr1-budget-test", "channel": key}
    profile_hash = content_hash(profile_payload)
    profile = ChannelProfileVersion(
        channel_workspace_id=channel.id,
        version=1,
        status="active",
        profile_input=profile_payload,
        profile_input_hash=profile_hash,
    )
    session.add(profile)
    session.flush()
    compiled_payload = {"channel_scoped_policy": {"budget_policy": {}}}
    snapshot = CompiledChannelPolicySnapshot(
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile.id,
        snapshot_version=1,
        status="active",
        compiler_version="mr1-budget-test",
        capability_matrix_version="test",
        compiled_payload=compiled_payload,
        content_hash=content_hash(compiled_payload),
        profile_input_hash=profile_hash,
        activated_at=utc_now(),
    )
    session.add(snapshot)
    session.flush()
    channel.active_policy_snapshot_id = snapshot.id
    session.flush()
    return SimpleNamespace(channel=channel, profile=profile, snapshot=snapshot)


def _project(
    session,
    *,
    company: Company,
    operator: User,
    channel_scope: SimpleNamespace,
    title: str,
) -> VideoProject:
    project = VideoProject(
        company_id=company.id,
        channel_workspace_id=channel_scope.channel.id,
        policy_snapshot_id=channel_scope.snapshot.id,
        channel_profile_version_id=channel_scope.profile.id,
        title=title,
        status="active",
        created_by_user_id=operator.id,
    )
    session.add(project)
    session.flush()
    return project


def _scope(session) -> SimpleNamespace:
    suffix = uuid.uuid4().hex[:10]
    company = Company(name=f"MR1 Budget {suffix}", slug=f"mr1-budget-{suffix}")
    operator = User(
        email=f"mr1-budget-{suffix}@example.com", display_name="MR1 Budget Operator"
    )
    session.add_all([company, operator])
    session.flush()
    channel_a = _channel(session, company=company, key=f"budget-a-{suffix}")
    channel_b = _channel(session, company=company, key=f"budget-b-{suffix}")
    project = _project(
        session,
        company=company,
        operator=operator,
        channel_scope=channel_a,
        title="Primary",
    )
    sibling_channel_project = _project(
        session,
        company=company,
        operator=operator,
        channel_scope=channel_a,
        title="Same channel sibling",
    )
    sibling_company_project = _project(
        session,
        company=company,
        operator=operator,
        channel_scope=channel_b,
        title="Same company sibling",
    )
    return SimpleNamespace(
        company=company,
        operator=operator,
        channel_a=channel_a,
        channel_b=channel_b,
        project=project,
        sibling_channel_project=sibling_channel_project,
        sibling_company_project=sibling_company_project,
    )


def _reserve(
    service: MR1MonthlyBudgetAuthority,
    *,
    run_id: uuid.UUID,
    project_id: uuid.UUID,
    amount: str = "1",
    environment_cap: str = "10",
    company_cap: str = "5",
    channel_cap: str = "3",
    provider_cap: str = "10",
) -> dict:
    return service.reserve_run(
        run_id=run_id,
        project_id=project_id,
        reservation_amount_usd=Decimal(amount),
        environment_cap_usd=Decimal(environment_cap),
        company_cap_usd=Decimal(company_cap),
        channel_cap_usd=Decimal(channel_cap),
        provider_allocations_usd={"elevenlabs": Decimal(amount)},
        provider_caps_usd={"elevenlabs": Decimal(provider_cap)},
        provider_aliases={"elevenlabs": ["elevenlabs", "forced_alignment"]},
    )


def test_migration_and_exact_run_idempotency(engine, db_session) -> None:
    assert "mr1_monthly_budget_reservations" in set(inspect(engine).get_table_names())
    scope = _scope(db_session)
    service = MR1MonthlyBudgetAuthority(db_session)
    run_id = uuid.uuid4()

    first = _reserve(service, run_id=run_id, project_id=scope.project.id)
    repeated = _reserve(service, run_id=run_id, project_id=scope.project.id)

    assert first["reservation_id"] == repeated["reservation_id"]
    assert first["status"] == "RESERVED"
    assert first["reserved_amount_usd"] == "1.000000"
    assert first["provider_allocations_usd"] == {"elevenlabs": "1.000000"}
    assert first["capacity_evidence"]["checks"]["environment_cap"] == "PASS"
    assert (
        db_session.scalar(select(func.count()).select_from(MR1MonthlyBudgetReservation))
        == 1
    )

    with pytest.raises(
        ValidationFailureError,
        match="MR1_BUDGET_RESERVATION_BINDING_MISMATCH",
    ):
        _reserve(
            service,
            run_id=run_id,
            project_id=scope.project.id,
            environment_cap="11",
        )


def test_sibling_project_cost_events_count_for_channel_and_company(db_session) -> None:
    scope = _scope(db_session)
    db_session.add(
        CostEvent(
            provider_key="elevenlabs",
            cost_scope_type="PROJECT",
            cost_scope_id=scope.sibling_channel_project.id,
            amount=Decimal("0.75"),
            currency="USD",
            cost_type="ACTUAL",
        )
    )
    db_session.flush()
    service = MR1MonthlyBudgetAuthority(db_session)

    with pytest.raises(ValidationFailureError, match="CHANNEL"):
        _reserve(
            service,
            run_id=uuid.uuid4(),
            project_id=scope.project.id,
            amount="0.30",
            environment_cap="3",
            company_cap="3",
            channel_cap="1",
            provider_cap="3",
        )

    db_session.add(
        CostEvent(
            provider_key="other_provider",
            cost_scope_type="PROJECT",
            cost_scope_id=scope.sibling_company_project.id,
            amount=Decimal("0.50"),
            currency="USD",
            cost_type="ACTUAL",
        )
    )
    db_session.flush()
    with pytest.raises(ValidationFailureError, match="COMPANY"):
        _reserve(
            service,
            run_id=uuid.uuid4(),
            project_id=scope.project.id,
            amount="0.10",
            environment_cap="3",
            company_cap="1.30",
            channel_cap="2",
            provider_cap="3",
        )


def test_environment_and_provider_caps_include_global_cost_events(db_session) -> None:
    scope = _scope(db_session)
    service = MR1MonthlyBudgetAuthority(db_session)
    db_session.add(
        CostEvent(
            provider_key="unrelated_provider",
            cost_scope_type="GLOBAL",
            amount=Decimal("0.90"),
            currency="USD",
            cost_type="ACTUAL",
        )
    )
    db_session.flush()
    with pytest.raises(ValidationFailureError, match="ENVIRONMENT"):
        _reserve(
            service,
            run_id=uuid.uuid4(),
            project_id=scope.project.id,
            amount="0.20",
            environment_cap="1",
            company_cap="2",
            channel_cap="2",
            provider_cap="2",
        )

    db_session.add(
        CostEvent(
            provider_key="forced_alignment",
            cost_scope_type="GLOBAL",
            amount=Decimal("0.90"),
            currency="USD",
            cost_type="ACTUAL",
        )
    )
    db_session.flush()
    with pytest.raises(ValidationFailureError, match="PROVIDER:elevenlabs"):
        _reserve(
            service,
            run_id=uuid.uuid4(),
            project_id=scope.project.id,
            amount="0.20",
            environment_cap="2",
            company_cap="2",
            channel_cap="2",
            provider_cap="1",
        )


def test_release_actual_settlement_and_conservative_consumed_failure(
    db_session,
) -> None:
    scope = _scope(db_session)
    service = MR1MonthlyBudgetAuthority(db_session)

    released = _reserve(
        service,
        run_id=uuid.uuid4(),
        project_id=scope.project.id,
        amount="0.80",
        environment_cap="1",
        company_cap="1",
        channel_cap="1",
        provider_cap="1",
    )
    released = service.release_pre_submit(released["reservation_ref"])
    assert released["status"] == "RELEASED"
    assert released["occupied_amount_usd"] == "0.000000"

    successful = _reserve(
        service,
        run_id=uuid.uuid4(),
        project_id=scope.project.id,
        amount="0.80",
        environment_cap="1",
        company_cap="1",
        channel_cap="1",
        provider_cap="1",
    )
    service.mark_submitted(successful["reservation_ref"])
    with pytest.raises(
        ValidationFailureError,
        match="RELEASE_FORBIDDEN_AFTER_PROVIDER_SUBMIT",
    ):
        service.release_pre_submit(successful["reservation_ref"])
    successful = service.settle_success(
        successful["reservation_ref"],
        actual_amount_usd=Decimal("0.25"),
        provider_actuals_usd={"elevenlabs": Decimal("0.25")},
    )
    assert successful["status"] == "SETTLED_ACTUAL"
    assert successful["actual_amount_usd"] == "0.250000"
    assert successful["occupied_amount_usd"] == "0.250000"

    consumed = _reserve(
        service,
        run_id=uuid.uuid4(),
        project_id=scope.project.id,
        amount="0.75",
        environment_cap="1",
        company_cap="1",
        channel_cap="1",
        provider_cap="1",
    )
    service.mark_submitted(consumed["reservation_ref"])
    consumed = service.settle_consumed_failure(consumed["reservation_ref"])
    assert consumed["status"] == "SETTLED_CONSERVATIVE"
    assert consumed["actual_amount_usd"] == "0.750000"
    assert consumed["settlement_kind"] == "CONSERVATIVE_RESERVED_CEILING"

    with pytest.raises(ValidationFailureError, match="MONTHLY_BUDGET_EXCEEDED"):
        _reserve(
            service,
            run_id=uuid.uuid4(),
            project_id=scope.project.id,
            amount="0.01",
            environment_cap="1",
            company_cap="1",
            channel_cap="1",
            provider_cap="1",
        )


def test_concurrent_runs_cannot_reserve_the_same_last_capacity(
    engine, db_session
) -> None:
    scope = _scope(db_session)
    project_id = scope.project.id
    db_session.commit()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    barrier = Barrier(2)

    def attempt(run_id: uuid.UUID) -> str:
        session = factory()
        try:
            barrier.wait(timeout=5)
            _reserve(
                MR1MonthlyBudgetAuthority(session),
                run_id=run_id,
                project_id=project_id,
                amount="0.75",
                environment_cap="1",
                company_cap="1",
                channel_cap="1",
                provider_cap="1",
            )
            session.commit()
            return "RESERVED"
        except ValidationFailureError:
            session.rollback()
            return "BLOCKED"
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, [uuid.uuid4(), uuid.uuid4()]))

    assert sorted(results) == ["BLOCKED", "RESERVED"]
    db_session.expire_all()
    assert (
        db_session.scalar(select(func.count()).select_from(MR1MonthlyBudgetReservation))
        == 1
    )
