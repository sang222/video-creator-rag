from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.contracts.geo_market import MinimalMarketChannelInit
from app.core.time import utc_now
from app.db.models import (
    AuditEvent,
    LocalizedMetadataPackage,
    LocalizedSubtitlePackage,
    OperatorAuthSession,
    OperatorUser,
    User,
)
from app.main import create_app
from app.services import CompanyService, MarketChannelGovernanceService
from app.services.m11_1 import AUTH_COOKIE_NAME, hash_session_token
from app.services.security_boundary import permission_for_route
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _authenticated_client(
    db_session,
    *,
    role: str,
) -> tuple[TestClient, User, OperatorUser]:
    suffix = uuid.uuid4().hex
    canonical_user = User(
        email=f"phase1-review-{suffix}@example.com",
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


def test_target_market_approval_requires_final_review_and_binds_session_actor(
    db_session,
) -> None:
    company = CompanyService(db_session).create_company(
        name="Phase 1 Market Review",
        slug=f"phase1-market-{uuid.uuid4().hex[:8]}",
    )
    service = MarketChannelGovernanceService(db_session)
    channel = service.create_minimal_channel(
        MinimalMarketChannelInit(
            company_id=company.id,
            channel_name="Phase 1 Market Channel",
            channel_key=f"phase1-market-{uuid.uuid4().hex[:8]}",
            channel_purpose="Verify authenticated target-market review provenance.",
            primary_market="US",
            primary_language="en",
            primary_locale="en-US",
            target_audience_summary="US small business operators",
            channel_market_type="MARKET_NATIVE",
        )
    )
    draft = service.run_market_research_draft(channel.id)
    db_session.commit()

    client, canonical_user, _ = _authenticated_client(db_session, role="REVIEWER")
    spoofed_reviewer = f"spoofed-{uuid.uuid4()}"
    response = client.post(
        f"/channels/{channel.id}/target-market-draft/approve",
        json={
            "expected_draft_id": str(draft.draft_id),
            "expected_draft_version": draft.draft_version,
            "expected_draft_hash": draft.content_hash,
            "reviewer": spoofed_reviewer,
            "approval_ref": f"human-review://{uuid.uuid4()}",
            "decision": "APPROVE",
        },
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    refreshed = service.get_market_draft(channel.id)
    assert refreshed.status == "APPROVED"
    persisted_channel = service._channel(channel.id)
    approval = persisted_channel.metadata_["target_market_governance"]["approvals"][-1]
    assert approval["reviewer"] == str(canonical_user.id)
    assert approval["reviewer"] != spoofed_reviewer
    assert (
        permission_for_route(
            "POST",
            "/channels/{channel_id}/target-market-draft/approve",
        )
        == "review.final_decide"
    )


def test_editorial_roles_cannot_create_readiness_final_localization(
    db_session,
    qualification_factory,
) -> None:
    scope = qualification_factory.m2_project()
    db_session.commit()

    producer, _, _ = _authenticated_client(db_session, role="PRODUCER")
    approved_subtitle = producer.post(
        f"/video-projects/{scope.project.id}/localized-subtitles",
        json={
            "source_language": "en",
            "target_language": "es",
            "translation_status": "APPROVED",
            "human_review_status": "APPROVED",
            "reviewer_id": str(uuid.uuid4()),
        },
    )
    not_required_subtitle = producer.post(
        f"/video-projects/{scope.project.id}/localized-subtitles",
        json={
            "source_language": "en",
            "target_language": "fr",
            "translation_status": "MACHINE_DRAFT",
            "human_review_status": "NOT_REQUIRED",
            "reviewer_id": str(uuid.uuid4()),
        },
    )

    channel_manager, _, _ = _authenticated_client(
        db_session,
        role="CHANNEL_MANAGER",
    )
    approved_metadata = channel_manager.post(
        f"/video-projects/{scope.project.id}/localized-metadata",
        json={
            "language": "de",
            "localized_title": "Human review required",
            "localized_description": "Editorial permission is not final review.",
            "localized_tags": ["workflow"],
            "human_review_status": "APPROVED",
            "reviewer_id": str(uuid.uuid4()),
        },
    )

    assert approved_subtitle.status_code == 403
    assert not_required_subtitle.status_code == 403
    assert approved_metadata.status_code == 403
    assert (
        db_session.query(LocalizedSubtitlePackage)
        .filter(LocalizedSubtitlePackage.video_project_id == scope.project.id)
        .count()
        == 0
    )
    assert (
        db_session.query(LocalizedMetadataPackage)
        .filter(LocalizedMetadataPackage.video_project_id == scope.project.id)
        .count()
        == 0
    )


def test_localization_reviewer_spoof_is_replaced_for_final_packages(
    db_session,
    qualification_factory,
) -> None:
    scope = qualification_factory.m2_project()
    db_session.commit()
    client, _, operator_user = _authenticated_client(db_session, role="REVIEWER")
    spoofed_reviewer = uuid.uuid4()

    subtitle = client.post(
        f"/video-projects/{scope.project.id}/localized-subtitles",
        headers={"X-Request-ID": "phase1-localization-final-review"},
        json={
            "source_language": "en",
            "target_language": "es",
            "translation_status": "APPROVED",
            "human_review_status": "APPROVED",
            "reviewer_id": str(spoofed_reviewer),
        },
    )
    metadata = client.post(
        f"/video-projects/{scope.project.id}/localized-metadata",
        json={
            "language": "de",
            "localized_title": "Verified localized title",
            "localized_description": "Verified localized description.",
            "localized_tags": ["workflow"],
            "human_review_status": "APPROVED",
            "reviewer_id": str(spoofed_reviewer),
        },
    )

    assert subtitle.status_code == 200, subtitle.text
    assert metadata.status_code == 200, metadata.text
    assert subtitle.json()["reviewer_id"] == str(operator_user.id)
    assert metadata.json()["reviewer_id"] == str(operator_user.id)
    assert subtitle.json()["reviewer_id"] != str(spoofed_reviewer)
    assert metadata.json()["reviewer_id"] != str(spoofed_reviewer)
    db_session.expire_all()
    persisted_subtitle = db_session.get(
        LocalizedSubtitlePackage,
        uuid.UUID(subtitle.json()["id"]),
    )
    persisted_metadata = db_session.get(
        LocalizedMetadataPackage,
        uuid.UUID(metadata.json()["id"]),
    )
    assert persisted_subtitle is not None
    assert persisted_subtitle.reviewer_id == operator_user.id
    assert persisted_metadata is not None
    assert persisted_metadata.reviewer_id == operator_user.id
    audit = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.event_type == "security.authenticated_mutation",
            AuditEvent.correlation_id == "phase1-localization-final-review",
        )
        .one()
    )
    assert audit.payload["permission"] == "review.final_decide"


def test_non_final_localization_binds_editorial_session_actor(
    db_session,
    qualification_factory,
) -> None:
    scope = qualification_factory.m2_project()
    db_session.commit()
    client, _, operator_user = _authenticated_client(db_session, role="PRODUCER")
    spoofed_reviewer = uuid.uuid4()

    response = client.post(
        f"/video-projects/{scope.project.id}/localized-metadata",
        json={
            "language": "fr",
            "localized_title": "Draft localized title",
            "localized_description": "Draft localized description.",
            "localized_tags": ["workflow"],
            "human_review_status": "NEEDS_HUMAN_REVIEW",
            "reviewer_id": str(spoofed_reviewer),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["reviewer_id"] == str(operator_user.id)
    assert response.json()["reviewer_id"] != str(spoofed_reviewer)
