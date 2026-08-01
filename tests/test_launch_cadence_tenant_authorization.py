from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.core.actor import authenticated_actor_context
from app.main import create_app
from app.services.m11_1 import AuthService
from app.services.rbac import RBACService
from tests.qualification.conftest import QualificationFactory
from tests.test_long_form_launch_cadence import (
    _active_launch_run,
    _approved_launch_policy,
)


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _actor_for_scope(session, scope):
    return authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="PRODUCER",
        permissions=RBACService(session).permissions_for_user(
            user_id=scope.operator.id,
            company_id=scope.company.id,
        ),
    )


@pytest.fixture
def launch_authorities(db_session, qualification_factory):
    owner_scope = qualification_factory.channel_scope(name="Launch Read Tenant Owner")
    policy, owner_actor, _ = _approved_launch_policy(
        db_session,
        owner_scope,
        timezone_name="UTC",
    )
    launch_run = _active_launch_run(
        db_session,
        policy,
        owner_actor,
        started_on=date(2026, 7, 20),
    )
    foreign_scope = qualification_factory.channel_scope(
        name="Launch Read Tenant Foreign"
    )
    foreign_actor = _actor_for_scope(db_session, foreign_scope)
    db_session.commit()
    return owner_scope, policy, launch_run, foreign_actor


@pytest.mark.parametrize(
    "surface",
    [
        "active_policy",
        "policy_by_id",
        "launch_run",
        "runway",
        "publish_slots",
        "latest_cadence",
        "dashboard",
    ],
)
def test_foreign_company_cannot_read_launch_cadence_surface(
    db_session,
    launch_authorities,
    monkeypatch,
    surface,
) -> None:
    owner_scope, policy, launch_run, foreign_actor = launch_authorities
    monkeypatch.setattr(
        AuthService,
        "actor_context",
        lambda _service, _token: foreign_actor,
    )
    paths = {
        "active_policy": f"/channels/{owner_scope.channel.id}/launch-policy",
        "policy_by_id": f"/launch-policies/{policy.id}",
        "launch_run": f"/launch-runs/{launch_run.id}",
        "runway": f"/launch-runs/{launch_run.id}/runway",
        "publish_slots": f"/launch-runs/{launch_run.id}/publish-slots",
        "latest_cadence": f"/launch-runs/{launch_run.id}/cadence/latest",
        "dashboard": f"/launch-runs/{launch_run.id}/dashboard",
    }

    response = TestClient(create_app()).get(paths[surface])

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_REQUIRED:production.read"
