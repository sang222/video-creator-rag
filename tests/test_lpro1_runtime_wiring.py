from __future__ import annotations

from datetime import timedelta
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.contracts.m5 import ChannelDailyRunCreate, DailyRunExecuteRequest, DailyRunMode
from app.core.errors import ConflictError
from app.db.models import ChannelDailyRun
from app.services.m5 import ChannelDailyRunService
from tests.test_d2p1_daily_to_package_bridge import _d2p_scope


def test_m5_schema_accepts_all_declared_modes_and_default_real(db_session, engine) -> None:
    scope = _d2p_scope(db_session)
    assert set(get_args(DailyRunMode)) == {"MOCK", "REAL_DISABLED", "REAL"}
    created = {}
    for offset, mode in enumerate(("MOCK", "REAL_DISABLED", "REAL"), start=1):
        run = ChannelDailyRunService(db_session).create_run(
            data=ChannelDailyRunCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=scope.snapshot.id,
                editorial_calendar_slot_id=scope.slot.id,
                run_date=scope.slot.slot_date + timedelta(days=offset),
                run_mode=mode,
                trigger_type="TEST",
            )
        )
        created[mode] = run
    default = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            editorial_calendar_slot_id=scope.slot.id,
            run_date=scope.slot.slot_date + timedelta(days=10),
            trigger_type="TEST",
        )
    )
    assert default.run_mode == "REAL"
    assert created["MOCK"].status == "PENDING"
    assert created["REAL"].status == "PENDING"
    assert created["REAL_DISABLED"].status == "BLOCKED"
    assert "REAL_LLM_EXECUTION_DISABLED" in created["REAL_DISABLED"].reason_codes
    with pytest.raises(ConflictError):
        ChannelDailyRunService(db_session).execute_run(
            daily_run_id=created["REAL_DISABLED"].id,
            data=DailyRunExecuteRequest(),
        )
    with pytest.raises(ValidationError):
        ChannelDailyRunCreate.model_validate(
            {
                "company_id": scope.company.id,
                "channel_workspace_id": scope.channel.id,
                "policy_snapshot_id": scope.snapshot.id,
                "run_date": scope.slot.slot_date,
                "run_mode": "UNKNOWN",
            }
        )
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(
                ChannelDailyRun(
                    company_id=scope.company.id,
                    channel_workspace_id=scope.channel.id,
                    policy_snapshot_id=scope.snapshot.id,
                    editorial_calendar_slot_id=scope.slot.id,
                    run_date=scope.slot.slot_date + timedelta(days=20),
                    run_mode="UNKNOWN",
                    status="PENDING",
                    trigger_type="TEST",
                    reason_codes=[],
                    metadata_={},
                )
            )
            db_session.flush()
    constraints = {item["name"] for item in inspect(engine).get_check_constraints("channel_daily_runs")}
    assert "ck_channel_daily_runs_ck_channel_daily_runs_run_mode" in constraints


def test_real_daily_without_llm_readiness_fails_closed_truthfully(db_session) -> None:
    scope = _d2p_scope(db_session)
    run = ChannelDailyRunService(db_session).create_run(
        data=ChannelDailyRunCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            editorial_calendar_slot_id=scope.slot.id,
            run_date=scope.slot.slot_date + timedelta(days=30),
            run_mode="REAL",
            trigger_type="TEST",
        )
    )
    executed = ChannelDailyRunService(db_session).execute_run(
        daily_run_id=run.id,
        data=DailyRunExecuteRequest(created_by_user_id=scope.operator.id),
    )
    assert executed.status == "BLOCKED"
    assert any(
        code in executed.reason_codes
        for code in (
            "OLLAMA_REAL_EXECUTION_DISABLED",
            "LLM_PROVIDER_NOT_CONFIGURED",
            "DAILY_PROMPT_NOT_READY",
        )
    )
