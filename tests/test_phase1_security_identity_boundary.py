from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

from app.core.actor import ActorContext
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import (
    AuditEvent,
    ChannelLifecycleDecision,
    ChannelWorkspace,
    Company,
    OperatorAuthSession,
    OperatorUser,
    User,
)
from app.main import create_app
from app.services.m11_1 import AUTH_COOKIE_NAME, hash_session_token
from app.services.long_production import LongProductionOrchestrator
from app.services.config_registry import ConfigRegistryService
from app.services.rbac import RBACService
from app.services.security_boundary import (
    MutationSecurityMiddleware,
    permission_for_route,
    uncovered_protected_routes,
)
from app.services.workflow import DecisionRightsService


class _ActorEchoPayload(BaseModel):
    created_by_user_id: uuid.UUID | None = None
    decided_by_user_id: uuid.UUID | None = None
    actor_role: str | None = None
    actor_type: str | None = None
    actor_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class _NestedReviewDecision(BaseModel):
    reviewer_user_id: uuid.UUID | None = None


class _NestedReviewPayload(BaseModel):
    decisions: list[_NestedReviewDecision]


def _authenticated_client(db_session, *, role: str) -> tuple[TestClient, User, OperatorUser]:
    suffix = uuid.uuid4().hex
    canonical_user = User(
        email=f"phase1-{suffix}@example.com",
        display_name=f"Phase 1 {role}",
        status="active",
    )
    db_session.add(canonical_user)
    db_session.flush()
    operator_user = OperatorUser(
        canonical_user_id=canonical_user.id,
        email=canonical_user.email,
        password_hash="not-used-by-session-auth",
        display_name=canonical_user.display_name,
        role=role,
        status="ACTIVE",
    )
    db_session.add(operator_user)
    db_session.flush()
    token = secrets.token_urlsafe(48)
    db_session.add(
        OperatorAuthSession(
            user_id=operator_user.id,
            session_token_hash=hash_session_token(token),
            expires_at=utc_now() + timedelta(hours=1),
        )
    )
    db_session.commit()
    client = TestClient(create_app())
    client.cookies.set(AUTH_COOKIE_NAME, token)
    return client, canonical_user, operator_user


def _actor_echo_app() -> FastAPI:
    application = FastAPI()
    application.add_middleware(
        MutationSecurityMiddleware,
        settings=get_settings(),
    )

    @application.post("/artifacts")
    async def echo_actor_payload(
        payload: _ActorEchoPayload,
        request: Request,
    ) -> dict[str, Any]:
        return {
            "payload": payload.model_dump(mode="json"),
            "actor_id": str(request.state.actor.actor_id),
            "actor_role": request.state.actor.actor_role,
        }

    @application.post("/channel-init-drafts/{draft_id}/review")
    async def echo_nested_review_payload(
        draft_id: uuid.UUID,
        payload: _NestedReviewPayload,
    ) -> dict[str, Any]:
        return {
            "draft_id": str(draft_id),
            "payload": payload.model_dump(mode="json"),
        }

    return application


def test_anonymous_mutation_is_rejected_even_when_dashboard_auth_is_disabled() -> None:
    response = TestClient(create_app()).post(
        "/companies",
        json={"name": "Anonymous", "slug": f"anonymous-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication required"


def test_authenticated_read_only_caller_cannot_mutate_or_trigger_production(
    db_session,
) -> None:
    client, _, _ = _authenticated_client(db_session, role="READ_ONLY")

    company_response = client.post(
        "/companies",
        json={"name": "Denied", "slug": f"denied-{uuid.uuid4().hex[:8]}"},
    )
    production_response = client.post(
        f"/production-runs/{uuid.uuid4()}/execute",
    )

    assert company_response.status_code == 403
    assert production_response.status_code == 403


def test_allowed_role_succeeds_and_audit_uses_authenticated_identity(
    db_session,
) -> None:
    client, canonical_user, operator_user = _authenticated_client(
        db_session,
        role="OWNER_ADMIN",
    )

    response = client.post(
        "/companies",
        headers={"X-Request-ID": "phase1-owner-company-create"},
        json={
            "name": "Authenticated Company",
            "slug": f"authenticated-{uuid.uuid4().hex[:8]}",
        },
    )

    assert response.status_code == 200, response.text
    audit = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.event_type == "security.authenticated_mutation")
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.actor_id == canonical_user.id
    assert audit.actor_type == "HUMAN_USER"
    assert audit.correlation_id == "phase1-owner-company-create"
    assert audit.payload["authenticated_actor_role"] == "OWNER_ADMIN"
    assert audit.payload["operator_user_id"] == str(operator_user.id)
    assert audit.payload["permission"] == "channel.manage"
    assert "password" not in str(audit.payload).lower()
    assert "token" not in str(audit.payload).lower()


def test_body_actor_spoof_is_replaced_by_authenticated_actor(db_session) -> None:
    _, canonical_user, operator_user = _authenticated_client(
        db_session,
        role="OWNER_ADMIN",
    )
    auth_session = (
        db_session.query(OperatorAuthSession)
        .filter(OperatorAuthSession.user_id == operator_user.id)
        .one()
    )
    # The raw token is not stored. Create a fresh session specifically for this app.
    raw_token = secrets.token_urlsafe(48)
    auth_session.session_token_hash = hash_session_token(raw_token)
    db_session.commit()
    client = TestClient(_actor_echo_app())
    client.cookies.set(AUTH_COOKIE_NAME, raw_token)

    response = client.post(
        "/artifacts",
        json={
            "created_by_user_id": str(uuid.uuid4()),
            "decided_by_user_id": str(uuid.uuid4()),
            "actor_role": "READ_ONLY",
        },
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["actor_id"] == str(canonical_user.id)
    assert payload["actor_role"] == "OWNER_ADMIN"
    assert payload["payload"]["created_by_user_id"] == str(canonical_user.id)
    assert payload["payload"]["decided_by_user_id"] == str(canonical_user.id)
    assert payload["payload"]["actor_role"] == "OWNER_ADMIN"


def test_nested_review_actor_spoof_is_replaced(
    db_session,
) -> None:
    _, canonical_user, operator_user = _authenticated_client(
        db_session,
        role="OWNER_ADMIN",
    )
    auth_session = (
        db_session.query(OperatorAuthSession)
        .filter(OperatorAuthSession.user_id == operator_user.id)
        .one()
    )
    raw_token = secrets.token_urlsafe(48)
    auth_session.session_token_hash = hash_session_token(raw_token)
    db_session.commit()
    client = TestClient(_actor_echo_app())
    client.cookies.set(AUTH_COOKIE_NAME, raw_token)

    response = client.post(
        f"/channel-init-drafts/{uuid.uuid4()}/review",
        json={
            "decisions": [
                {"reviewer_user_id": str(uuid.uuid4())},
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert (
        response.json()["payload"]["decisions"][0]["reviewer_user_id"]
        == str(canonical_user.id)
    )


def test_failed_login_audit_survives_rejected_transaction(
    db_session,
) -> None:
    email = f"missing-{uuid.uuid4()}@example.com"
    response = TestClient(create_app()).post(
        "/auth/login",
        json={"email": email, "password": "incorrect-password"},
    )
    assert response.status_code == 401
    db_session.expire_all()
    audit = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.event_type == "auth.login_failed")
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.reason_code == "LOGIN_FAILED"
    assert audit.payload["email"] == email


def test_lifecycle_decision_actor_comes_from_session_not_body(db_session) -> None:
    client, canonical_user, _ = _authenticated_client(
        db_session,
        role="OWNER_ADMIN",
    )
    company = Company(
        name="Phase 1 Decision Company",
        slug=f"phase1-decision-{uuid.uuid4().hex[:8]}",
        description="",
        status="active",
        default_currency="USD",
    )
    db_session.add(company)
    db_session.flush()
    channel = ChannelWorkspace(
        company_id=company.id,
        key=f"phase1-{uuid.uuid4().hex[:8]}",
        name="Phase 1 Decision Channel",
        status="draft",
    )
    db_session.add(channel)
    db_session.commit()

    spoofed_id = uuid.uuid4()
    response = client.post(
        f"/channels/{channel.id}/lifecycle-decision",
        json={
            "action": "ADD_MANUAL_NOTE",
            "reason": "Session actor must be authoritative.",
            "decided_by_user_id": str(spoofed_id),
            "actor_role": "CHANNEL_MANAGER",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["decided_by_user_id"] == str(canonical_user.id)
    decision = (
        db_session.query(ChannelLifecycleDecision)
        .filter(ChannelLifecycleDecision.channel_workspace_id == channel.id)
        .one()
    )
    assert decision.decided_by_user_id == canonical_user.id
    assert decision.decided_by_user_id != spoofed_id


def test_system_worker_identity_cannot_be_forged_from_public_json(
    db_session,
) -> None:
    _, _, operator_user = _authenticated_client(db_session, role="OWNER_ADMIN")
    auth_session = (
        db_session.query(OperatorAuthSession)
        .filter(OperatorAuthSession.user_id == operator_user.id)
        .one()
    )
    raw_token = secrets.token_urlsafe(48)
    auth_session.session_token_hash = hash_session_token(raw_token)
    db_session.commit()
    client = TestClient(_actor_echo_app())
    client.cookies.set(AUTH_COOKIE_NAME, raw_token)

    response = client.post(
        "/artifacts",
        json={
            "actor_type": "SYSTEM_WORKER",
            "actor_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 403
    assert "cannot be supplied publicly" in response.json()["detail"]
    with pytest.raises(TypeError):
        ActorContext(
            actor_type="SYSTEM_WORKER",
            actor_id=uuid.uuid4(),
            actor_role="SYSTEM_WORKER",
            operator_user_id=None,
            permissions=frozenset({"*"}),
        )


def test_permission_registry_covers_every_protected_application_route() -> None:
    application = create_app()
    assert uncovered_protected_routes(application) == []
    assert permission_for_route("POST", "/auth/login") is None
    assert permission_for_route("POST", "/auth/logout") == "session.end"
    assert (
        permission_for_route("POST", "/production-runs/{run_id}/cancel")
        == "production.cancel"
    )
    assert permission_for_route("GET", "/auth/youtube/start") == "publish.prepare"
    assert permission_for_route("GET", "/auth/google-drive/callback") == "publish.prepare"
    assert permission_for_route("GET", "/media/local-retention-policy") == "ops.manage"
    assert (
        permission_for_route(
            "GET",
            "/credential-references/{credential_reference_id}",
        )
        == "provider.execute"
    )
    assert (
        permission_for_route(
            "POST",
            "/video-projects/{project_id}/long-production/run",
        )
        == "production.start"
    )


def test_credential_reference_read_requires_provider_permission(
    db_session,
) -> None:
    credential_reference_id = uuid.uuid4()
    anonymous = TestClient(create_app()).get(
        f"/credential-references/{credential_reference_id}"
    )
    observer, _, _ = _authenticated_client(
        db_session,
        role="READ_ONLY",
    )
    forbidden = observer.get(
        f"/credential-references/{credential_reference_id}"
    )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403


def test_long_production_persists_authenticated_actor_with_internal_fallback() -> None:
    project_id = uuid.uuid4()
    authenticated_actor_id = uuid.uuid4()
    fallback_actor_id = uuid.uuid4()
    authority = object()
    receipt = object()
    orchestrator = object.__new__(LongProductionOrchestrator)
    orchestrator.session = object()
    orchestrator._authority_from_db = Mock(
        return_value=(authority, fallback_actor_id)
    )
    orchestrator._run_authority = Mock(return_value=receipt)
    orchestrator._persist_receipt = Mock()

    assert (
        orchestrator.run(
            project_id=project_id,
            actor_user_id=authenticated_actor_id,
        )
        is receipt
    )
    orchestrator._persist_receipt.assert_called_once_with(
        project_id=project_id,
        actor_id=authenticated_actor_id,
        receipt=receipt,
    )

    orchestrator._persist_receipt.reset_mock()
    assert orchestrator.run(project_id=project_id) is receipt
    orchestrator._persist_receipt.assert_called_once_with(
        project_id=project_id,
        actor_id=fallback_actor_id,
        receipt=receipt,
    )


def test_coarse_roles_match_service_layer_decision_rights(
    db_session,
) -> None:
    ConfigRegistryService(db_session).seed(["config"])
    company = Company(
        name="Phase 1 Rights Company",
        slug=f"phase1-rights-{uuid.uuid4().hex[:8]}",
        description="",
        status="active",
        default_currency="USD",
    )
    db_session.add(company)
    db_session.flush()
    users: dict[str, User] = {}
    for role_key in ("channel_manager", "producer", "reviewer"):
        user = User(
            email=f"{role_key}-{uuid.uuid4()}@example.com",
            display_name=role_key,
            status="active",
        )
        db_session.add(user)
        db_session.flush()
        RBACService(db_session).assign_role(
            user_id=user.id,
            role_key=role_key,
            company_id=company.id,
        )
        users[role_key] = user

    rights = DecisionRightsService(db_session)
    for role_key in ("channel_manager", "producer"):
        assert rights.has_capability(
            user_id=users[role_key].id,
            company_id=company.id,
            action="video_project.create",
        )
        assert rights.has_capability(
            user_id=users[role_key].id,
            company_id=company.id,
            action="artifact_version.create",
        )
    assert rights.has_capability(
        user_id=users["reviewer"].id,
        company_id=company.id,
        action="approval_decision.create",
    )
