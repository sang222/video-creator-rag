from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.contracts.m5 import (
    EditorialIdeaCandidateCreate,
    EditorialResearchRunCreate,
)
from app.core.actor import authenticated_actor_context
from app.db.models import IdeaMarketPreflight
from app.main import create_app
from app.services.editorial_research import EditorialResearchService
from app.services.m11_1 import AuthService
from app.services.rbac import RBACService
from tests.qualification.conftest import QualificationFactory


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


def _research_authorities(session, qualification_factory):
    scope = qualification_factory.channel_scope(name="Planning Tenant Owner")
    actor = _actor_for_scope(session, scope)
    service = EditorialResearchService(session)
    run = service.create_run(
        data=EditorialResearchRunCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            channel_profile_version_id=scope.profile.id,
            policy_snapshot_id=scope.snapshot.id,
            run_date=date(2026, 7, 30),
            trigger_type="TEST",
        ),
        actor=actor,
    )
    candidate = service.add_candidate(
        data=EditorialIdeaCandidateCreate(
            editorial_research_run_id=run.id,
            proposed_title="Tenant-bound long-form research candidate",
            evidence_refs=[
                {
                    "type": "qualification",
                    "ref": "qualification://tenant-bound-candidate",
                }
            ],
        ),
        actor=actor,
    )
    return scope, run, candidate


def _client_for_actor(monkeypatch, actor) -> TestClient:
    monkeypatch.setattr(
        AuthService,
        "actor_context",
        lambda _service, _token: actor,
    )
    return TestClient(create_app())


@pytest.mark.parametrize("resource_name", ["run", "candidate"])
def test_foreign_company_cannot_read_editorial_authority(
    db_session,
    qualification_factory,
    monkeypatch,
    resource_name,
) -> None:
    _, run, candidate = _research_authorities(db_session, qualification_factory)
    foreign_scope = qualification_factory.channel_scope(name="Planning Tenant Foreign")
    foreign_actor = _actor_for_scope(db_session, foreign_scope)
    db_session.commit()
    client = _client_for_actor(monkeypatch, foreign_actor)
    path = (
        f"/editorial-research-runs/{run.id}"
        if resource_name == "run"
        else f"/editorial-idea-candidates/{candidate.id}"
    )

    response = client.get(path)

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_REQUIRED:production.read"


def test_foreign_company_cannot_create_idea_market_preflight(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    owner_scope, _, _ = _research_authorities(db_session, qualification_factory)
    foreign_scope = qualification_factory.channel_scope(name="Preflight Tenant Foreign")
    foreign_actor = _actor_for_scope(db_session, foreign_scope)
    before = db_session.scalar(select(func.count()).select_from(IdeaMarketPreflight))
    db_session.commit()
    client = _client_for_actor(monkeypatch, foreign_actor)

    response = client.post(
        "/idea-market-preflights",
        json={
            "company_id": str(owner_scope.company.id),
            "channel_workspace_id": str(owner_scope.channel.id),
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "PERMISSION_REQUIRED:editorial.manage"
    assert (
        db_session.scalar(select(func.count()).select_from(IdeaMarketPreflight))
        == before
    )
