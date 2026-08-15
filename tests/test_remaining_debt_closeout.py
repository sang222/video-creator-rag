from __future__ import annotations

import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.remaining_debt import (
    AudienceDeliveryPlan,
    BusinessActionItem,
    LearningReview,
    PlatformEnforcementIncident,
    SeriesArcVersion,
    SeriesEpisodeBlueprint,
    SeriesPublicOrdinal,
)
from app.services.remaining_debt_closeout import (
    ArchitectureDebtAuditService,
    BusinessMonitoringService,
    LearningAuthorityService,
    RemainingDebtCloseoutCoordinator,
    SeriesAuthorityService,
)


@pytest.fixture()
def db() -> Session:
    url = os.environ.get(
        "VCOS_DATABASE_URL",
        "postgresql+psycopg://vcos:vcos@localhost:5432/vcos",
    )
    engine = create_engine(url, future=True)
    connection = engine.connect()
    transaction = connection.begin()
    maker = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


def _scope() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_fixed_series_arc_public_ordinal_and_completion_pending(db: Session) -> None:
    company_id, channel_id, series_plan_id = _scope()
    service = SeriesAuthorityService(db)
    arc = service.create_arc(
        company_id=company_id,
        channel_workspace_id=channel_id,
        series_plan_id=series_plan_id,
        arc_mode="FIXED_COUNT",
        planned_episode_count=3,
        premise="Three bounded episodes",
        coverage_policy={"coverage": ["setup", "mechanism", "decision"]},
    )
    blueprints = [
        service.add_blueprint(
            arc_id=arc.id,
            blueprint_key=f"EP-{position:03d}",
            planned_position=position,
            title=f"Episode {position}",
            editorial_contract={"position": position},
            coverage_tags=[f"tag-{position}"],
        )
        for position in range(1, 4)
    ]
    service.activate_arc(
        arc_id=arc.id,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        reason="Approve fixed launch arc",
    )

    ordinals = []
    for index, blueprint in enumerate(blueprints, start=1):
        project_id = uuid.uuid4()
        service.bind_technical_attempt(
            blueprint_id=blueprint.id,
            video_project_id=project_id,
            technical_attempt_ref=f"workflow-attempt-{100 + index}",
        )
        ordinals.append(
            service.record_publication(
                series_plan_id=series_plan_id,
                publication_receipt_id=uuid.uuid4(),
                video_project_id=project_id,
                blueprint_id=blueprint.id,
                technical_attempt_ref=f"workflow-attempt-{100 + index}",
                published_at=utc_now(),
            )
        )

    assert [row.public_ordinal for row in ordinals] == [1, 2, 3]
    assert [row.playlist_position for row in ordinals] == [0, 1, 2]
    assert len({row.technical_attempt_ref for row in ordinals}) == 3
    progress = service.progress(series_plan_id=series_plan_id)
    assert progress.display_label == "EP03/03"
    assert progress.published_count == 3
    assert progress.remaining_count == 0
    assert db.get(SeriesArcVersion, arc.id).state == "COMPLETION_PENDING"


def test_fixed_series_requires_full_blueprint_coverage_before_activation(
    db: Session,
) -> None:
    company_id, channel_id, series_plan_id = _scope()
    service = SeriesAuthorityService(db)
    arc = service.create_arc(
        company_id=company_id,
        channel_workspace_id=channel_id,
        series_plan_id=series_plan_id,
        arc_mode="FIXED_COUNT",
        planned_episode_count=2,
        premise="Coverage must be explicit",
        coverage_policy={},
    )
    service.add_blueprint(
        arc_id=arc.id,
        blueprint_key="EP-001",
        planned_position=1,
        title="One",
        editorial_contract={},
        coverage_tags=[],
    )
    with pytest.raises(ValidationFailureError, match="SERIES_FIXED_ARC_COVERAGE_INCOMPLETE"):
        service.activate_arc(
            arc_id=arc.id,
            actor_id=uuid.uuid4(),
            command_id=uuid.uuid4(),
            reason="Incomplete plan must not activate",
        )


def test_series_extension_is_new_version_and_preserves_public_truth(db: Session) -> None:
    company_id, channel_id, series_plan_id = _scope()
    service = SeriesAuthorityService(db)
    arc = service.create_arc(
        company_id=company_id,
        channel_workspace_id=channel_id,
        series_plan_id=series_plan_id,
        arc_mode="FIXED_COUNT",
        planned_episode_count=1,
        premise="Expandable arc",
        coverage_policy={},
    )
    blueprint = service.add_blueprint(
        arc_id=arc.id,
        blueprint_key="EP-001",
        planned_position=1,
        title="One",
        editorial_contract={},
        coverage_tags=[],
    )
    service.activate_arc(
        arc_id=arc.id,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        reason="Start",
    )
    project_id = uuid.uuid4()
    service.bind_technical_attempt(
        blueprint_id=blueprint.id,
        video_project_id=project_id,
        technical_attempt_ref="attempt-not-public-ordinal",
    )
    service.record_publication(
        series_plan_id=series_plan_id,
        publication_receipt_id=uuid.uuid4(),
        video_project_id=project_id,
        blueprint_id=blueprint.id,
        technical_attempt_ref="attempt-not-public-ordinal",
        published_at=utc_now(),
    )
    extended = service.extend_fixed_series(
        arc_id=arc.id,
        new_planned_episode_count=3,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        reason="Human-approved extension",
    )
    assert extended.version_number == 2
    assert extended.previous_version_id == arc.id
    assert extended.planned_episode_count == 3
    assert db.get(SeriesArcVersion, arc.id).state == "SUPERSEDED"
    copied = list(
        db.scalars(
            select(SeriesEpisodeBlueprint).where(
                SeriesEpisodeBlueprint.series_arc_version_id == extended.id
            )
        ).all()
    )
    assert len(copied) == 3
    assert sum(item.state == "PUBLISHED" for item in copied) == 1
    assert db.scalar(
        select(SeriesPublicOrdinal).where(
            SeriesPublicOrdinal.series_plan_id == series_plan_id,
            SeriesPublicOrdinal.public_ordinal == 1,
        )
    ) is not None


def test_rolling_series_allocates_public_ordinal_independent_of_attempt(db: Session) -> None:
    company_id, channel_id, series_plan_id = _scope()
    service = SeriesAuthorityService(db)
    arc = service.create_arc(
        company_id=company_id,
        channel_workspace_id=channel_id,
        series_plan_id=series_plan_id,
        arc_mode="ROLLING",
        planned_episode_count=None,
        premise="Rolling show",
        coverage_policy={},
    )
    service.activate_arc(
        arc_id=arc.id,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        reason="Rolling launch",
    )
    first = service.record_publication(
        series_plan_id=series_plan_id,
        publication_receipt_id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        technical_attempt_ref="attempt-98271",
        published_at=utc_now(),
    )
    second = service.record_publication(
        series_plan_id=series_plan_id,
        publication_receipt_id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        technical_attempt_ref="attempt-2",
        published_at=utc_now(),
    )
    assert (first.public_ordinal, second.public_ordinal) == (1, 2)
    assert second.playlist_position == 1


def test_learning_fingerprint_is_order_stable_and_m11_exactly_once(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    uploaded_video_id = uuid.uuid4()
    service = LearningAuthorityService(db)
    first = service.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source_entity_ref="publication://one",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="a" * 64,
        target_market="US",
        content_language="en-US",
        format_key="STANDALONE",
        normalized_features={"visual": ["image", "video"], "voice": {"pace": "mid"}},
    )
    same_semantics = service.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source_entity_ref="publication://two",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="a" * 64,
        target_market="US",
        content_language="en-US",
        format_key="STANDALONE",
        normalized_features={"voice": {"pace": "mid"}, "visual": ["image", "video"]},
    )
    assert first.fingerprint == same_semantics.fingerprint
    window = service.record_analytics_window(
        company_id=company_id,
        channel_workspace_id=channel_id,
        uploaded_video_id=uploaded_video_id,
        window_key="M11",
        source_version="youtube-v1",
        maturity_state="MATURE",
        confidence_state="ACTION_READY",
        sample_size=1200,
        impressions=8000,
        views=1200,
        source_snapshot_refs=["analytics://m11"],
        evidence_payload={"retention": 0.52},
        matured_at=utc_now(),
    )
    command_id = uuid.uuid4()
    review = service.review(
        fingerprint_id=first.id,
        analytics_evidence_window_id=window.id,
        current_policy_hash="a" * 64,
        comparable_count=3,
        command_id=command_id,
    )
    replay = service.review(
        fingerprint_id=first.id,
        analytics_evidence_window_id=window.id,
        current_policy_hash="a" * 64,
        comparable_count=3,
        command_id=command_id,
    )
    assert review.id == replay.id
    assert review.decision == "ELIGIBLE"
    promoted = service.promote(
        review_id=review.id,
        actor_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
    )
    assert promoted.decision == "PROMOTED"


def test_learning_is_blocked_by_policy_drift_and_enforcement(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    learning = LearningAuthorityService(db)
    business = BusinessMonitoringService(db)
    fingerprint = learning.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source_entity_ref="publication://blocked",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="b" * 64,
        target_market="US",
        content_language="en-US",
        format_key="STANDALONE",
        normalized_features={},
    )
    window = learning.record_analytics_window(
        company_id=company_id,
        channel_workspace_id=channel_id,
        uploaded_video_id=uuid.uuid4(),
        window_key="D30",
        source_version="youtube-v1",
        maturity_state="MATURE",
        confidence_state="STABLE",
        sample_size=500,
        impressions=3000,
        views=500,
        source_snapshot_refs=["analytics://d30"],
        evidence_payload={},
        matured_at=utc_now(),
    )
    learning.open_operational_incident(
        company_id=company_id,
        channel_workspace_id=channel_id,
        incident_type="POLICY_DRIFT",
        external_ref="policy-drift-1",
        severity="HIGH",
        evidence_payload={"old": "b", "new": "c"},
    )
    business.open_enforcement_incident(
        company_id=company_id,
        channel_workspace_id=channel_id,
        platform="YOUTUBE",
        external_incident_ref="claim-1",
        incident_type="CONTENT_ID",
        severity="HIGH",
        scope="VIDEO",
        source_ref="youtube-studio://claim-1",
        evidence_payload={},
        detected_at=utc_now(),
    )
    review = learning.review(
        fingerprint_id=fingerprint.id,
        analytics_evidence_window_id=window.id,
        current_policy_hash="c" * 64,
        comparable_count=3,
        command_id=uuid.uuid4(),
    )
    assert review.decision == "BLOCKED"
    assert "SYSTEM_PROMOTION_POLICY_RECHECK_FAILED" in review.reason_codes
    assert "PLATFORM_ENFORCEMENT_FREEZE" in review.reason_codes
    assert "LEARNING_OPERATIONAL_INCIDENT_OPEN" in review.reason_codes


def test_business_self_funding_requires_two_trusted_profitable_cycles(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    service.record_payment_status(
        company_id=company_id,
        payee_ref="payee://owner",
        tax_state="VERIFIED",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="NONE",
        source_type="OPERATOR_ATTESTATION",
        source_ref="evidence://payment",
        confidence_state="HIGH",
        source_updated_at=now,
        valid_until=now + timedelta(days=30),
    )
    service.record_monetization_status(
        company_id=company_id,
        channel_workspace_id=channel_id,
        platform="YOUTUBE",
        program_type="YPP",
        eligibility_state="ELIGIBLE",
        enrollment_state="ACTIVE",
        restriction_state="NONE",
        source_type="API",
        source_ref="youtube://monetization",
        confidence_state="HIGH",
        source_updated_at=now,
        valid_until=now + timedelta(days=7),
    )
    for offset in (60, 30):
        start = now - timedelta(days=offset)
        end = start + timedelta(days=30)
        service.record_revenue(
            company_id=company_id,
            channel_workspace_id=channel_id,
            source="YOUTUBE",
            amount_state="FINALIZED",
            amount=Decimal("180"),
            currency="USD",
            period_start=start,
            period_end=end,
            source_ref=f"youtube://revenue/{offset}",
            source_updated_at=now,
            confidence_state="HIGH",
        )
        service.build_channel_pnl(
            company_id=company_id,
            channel_workspace_id=channel_id,
            period_start=start,
            period_end=end,
            currency="USD",
            direct_cost=Decimal("80"),
            allocated_ops_cost=Decimal("20"),
        )
    assessment = service.evaluate_self_funding(
        company_id=company_id,
        channel_workspace_id=channel_id,
        assessment_window_end=now,
    )
    assert assessment.decision == "SELF_FUNDING"
    dashboard = service.dashboard(
        company_id=company_id,
        channel_workspace_id=channel_id,
    )
    assert dashboard.self_funding_decision == "SELF_FUNDING"
    assert dashboard.contribution_margin == Decimal("80.000000")


def test_high_enforcement_blocks_self_funding_and_creates_action(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    incident = service.open_enforcement_incident(
        company_id=company_id,
        channel_workspace_id=channel_id,
        platform="YOUTUBE",
        external_incident_ref="strike-1",
        incident_type="COPYRIGHT_STRIKE",
        severity="CRITICAL",
        scope="CHANNEL",
        source_ref="youtube-studio://strike-1",
        evidence_payload={"deadline": "7d"},
        detected_at=now,
        deadline_at=now + timedelta(days=7),
    )
    assert incident.freeze_learning is True
    action = db.scalar(
        select(BusinessActionItem).where(
            BusinessActionItem.channel_workspace_id == channel_id
        )
    )
    assert action is not None
    pack = service.create_appeal_pack(
        incident_id=incident.id,
        rights_basis="Owned and licensed production source",
        evidence_items=[{"ref": "license://1"}],
        timeline=[{"at": now.isoformat(), "event": "detected"}],
    )
    assert pack.state == "READY_FOR_HUMAN"
    assert db.scalar(
        select(PlatformEnforcementIncident).where(
            PlatformEnforcementIncident.id == incident.id
        )
    ).state == "OPEN"


def test_affiliate_and_disclosure_gate_fails_closed(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    offer = service.register_affiliate_offer(
        company_id=company_id,
        channel_workspace_id=channel_id,
        merchant="Example",
        offer_ref="offer-1",
        product_ref="product-1",
        commission_model={"type": "percentage", "value": 20},
        attribution_window_text="30 days",
        terms_hash="d" * 64,
        disclosure_required=True,
        effective_at=now,
        expires_at=now + timedelta(days=30),
    )
    link = service.register_affiliate_link(
        offer_snapshot_id=offer.id,
        canonical_url="https://example.com/product?ref=vcos",
        short_url=None,
        utm_policy_version="utm-v1",
    )
    blocked = service.assess_disclosures(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=uuid.uuid4(),
        publish_package_ref="package://blocked",
        policy_version="disclosure-v1",
        required_disclosures=["AFFILIATE"],
        observed_disclosures=[],
        link_registry_refs=[link.id],
    )
    assert blocked.decision == "BLOCK"
    passed = service.assess_disclosures(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=uuid.uuid4(),
        publish_package_ref="package://passed",
        policy_version="disclosure-v1",
        required_disclosures=["AFFILIATE"],
        observed_disclosures=["AFFILIATE"],
        link_registry_refs=[link.id],
    )
    assert passed.decision == "PASS"


def test_publication_coordinator_seeds_learning_and_audience_delivery(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    candidate = SimpleNamespace(
        company_id=company_id,
        channel_workspace_id=channel_id,
        video_project_id=uuid.uuid4(),
        production_package_hash="e" * 64,
        candidate_hash="f" * 64,
        target_market_lineage={"primary_market": "US", "content_language": "en-US"},
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        content_mode="STANDALONE",
        production_lane="LONG_FORM",
        target_surface="YOUTUBE",
        series_plan_id=None,
    )
    receipt = SimpleNamespace(id=uuid.uuid4())
    result = RemainingDebtCloseoutCoordinator(db).on_publication_verified(
        candidate=candidate,
        public_receipt=receipt,
        observed_at=utc_now(),
    )
    assert result["learning_fingerprint_id"]
    assert result["audience_delivery_plan_id"]
    assert db.scalar(
        select(AudienceDeliveryPlan).where(
            AudienceDeliveryPlan.publication_receipt_id == receipt.id
        )
    ) is not None


def test_architecture_audit_and_portfolio_proof(tmp_path: Path) -> None:
    safe = tmp_path / "app"
    safe.mkdir()
    (safe / "service.py").write_text(
        "def execute(profile_snapshot):\n    return profile_snapshot\n",
        encoding="utf-8",
    )
    audit = ArchitectureDebtAuditService().audit(tmp_path)
    assert audit.one_engine_many_profiles is True

    (safe / "bad.py").write_text(
        "def bad(niche):\n    if niche == 'x':\n        return 'Small Team AI'\n",
        encoding="utf-8",
    )
    audit = ArchitectureDebtAuditService().audit(tmp_path)
    assert audit.one_engine_many_profiles is False
    assert audit.hardcoded_channel_findings == ("app/bad.py",)
    assert audit.niche_branch_findings == ("app/bad.py",)

    a, b = uuid.uuid4(), uuid.uuid4()
    not_proven = ArchitectureDebtAuditService.portfolio_proof(
        verified_publications_by_channel={a: 1},
        compiled_profile_hash_by_channel={a: "p1"},
    )
    assert not_proven["state"] == "NOT_PROVEN"
    proven = ArchitectureDebtAuditService.portfolio_proof(
        verified_publications_by_channel={a: 1, b: 2},
        compiled_profile_hash_by_channel={a: "p1", b: "p2"},
    )
    assert proven["state"] == "PROVEN"
