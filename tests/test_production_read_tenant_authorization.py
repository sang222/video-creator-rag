from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.contracts.m6 import ProductionArtifactRunCreate
from app.core.actor import authenticated_actor_context
from app.main import create_app
from app.services.m11_1 import AuthService
from app.services.m6 import ProductionArtifactRunService
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


@pytest.fixture
def production_read_authorities(db_session, qualification_factory):
    owner = qualification_factory.m5_admitted_project()
    production_run = ProductionArtifactRunService(db_session).create_run(
        data=ProductionArtifactRunCreate(
            video_project_id=owner.project.id,
            source_project_admission_decision_id=owner.admission.id,
        )
    )
    foreign_scope = qualification_factory.channel_scope(
        name="Production Read Tenant Foreign"
    )
    foreign_actor = _actor_for_scope(db_session, foreign_scope)
    db_session.commit()
    return owner, production_run, foreign_actor


@pytest.mark.parametrize(
    "surface",
    [
        "admission",
        "long_production",
        "production_run",
    ],
)
@pytest.mark.parametrize("access_mode", ["anonymous", "foreign_company"])
def test_production_read_rejects_unauthorized_actor(
    production_read_authorities,
    monkeypatch,
    surface,
    access_mode,
) -> None:
    owner, production_run, foreign_actor = production_read_authorities
    if access_mode == "foreign_company":
        monkeypatch.setattr(
            AuthService,
            "actor_context",
            lambda _service, _token: foreign_actor,
        )
    paths = {
        "admission": (f"/project-admission-decisions/{owner.admission.id}"),
        "long_production": (f"/video-projects/{owner.project.id}/long-production"),
        "production_run": f"/production-runs/{production_run.id}",
    }

    response = TestClient(create_app()).get(paths[surface])

    if access_mode == "anonymous":
        assert response.status_code == 401
        assert response.json()["detail"] == "authentication required"
    else:
        assert response.status_code == 403
        assert response.json()["detail"] == "PERMISSION_REQUIRED:production.read"
