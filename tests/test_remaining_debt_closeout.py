from __future__ import annotations

import inspect
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
    AnalyticsEvidenceWindow,
    AudienceDeliveryPlan,
    BusinessActionItem,
    ChannelPnlSnapshot,
    ContinuationCapitalReview,
    LearningEquivalenceFingerprint,
    PlatformEnforcementIncident,
    SelfFundingAssessment,
    SeriesArcVersion,
    SeriesEpisodeBlueprint,
    SeriesPublicOrdinal,
)
from app.db.models.ops import CostEvent
from app.services.remaining_debt_closeout import (
    ArchitectureDebtAuditService,
    BusinessMonitoringService,
    LearningAuthorityService,
    RemainingDebtCloseoutCoordinator,
    SeriesAuthorityService,
)
from app.services.production_publish import ProductionPublishService


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


def _learning_window(
    db: Session,
    *,
    company_id: uuid.UUID,
    channel_workspace_id: uuid.UUID,
    uploaded_video_id: uuid.UUID,
    window_key: str,
    confidence_state: str,
) -> AnalyticsEvidenceWindow:
    row = AnalyticsEvidenceWindow(
        id=uuid.uuid4(),
        company_id=company_id,
        channel_workspace_id=channel_workspace_id,
        uploaded_video_id=uploaded_video_id,
        window_key=window_key,
        source_version=f"canonical-test:{uuid.uuid4()}",
        maturity_state="MATURE",
        confidence_state=confidence_state,
        sample_size=1200,
        impressions=8000,
        views=1200,
        source_snapshot_refs=["test://canonical-analytics-authority"],
        evidence_payload={"test_fixture": True},
        evidence_hash="1" * 64,
        matured_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


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
    with pytest.raises(
        ValidationFailureError, match="SERIES_FIXED_ARC_COVERAGE_INCOMPLETE"
    ):
        service.activate_arc(
            arc_id=arc.id,
            actor_id=uuid.uuid4(),
            command_id=uuid.uuid4(),
            reason="Incomplete plan must not activate",
        )


def test_series_extension_is_new_version_and_preserves_public_truth(
    db: Session,
) -> None:
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
    assert (
        db.scalar(
            select(SeriesPublicOrdinal).where(
                SeriesPublicOrdinal.series_plan_id == series_plan_id,
                SeriesPublicOrdinal.public_ordinal == 1,
            )
        )
        is not None
    )


def test_rolling_series_allocates_public_ordinal_independent_of_attempt(
    db: Session,
) -> None:
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
        normalized_features={
            "visual": ["image", "video"],
            "voice": {"pace": "mid"},
        },
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
    service.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source_entity_ref="publication://three",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="a" * 64,
        target_market="US",
        content_language="en-US",
        format_key="STANDALONE",
        normalized_features={"visual": ["image", "video"], "voice": {"pace": "mid"}},
    )
    window = _learning_window(
        db,
        company_id=company_id,
        channel_workspace_id=channel_id,
        uploaded_video_id=uploaded_video_id,
        window_key="M11",
        confidence_state="ACTION_READY",
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
    window = _learning_window(
        db,
        company_id=company_id,
        channel_workspace_id=channel_id,
        uploaded_video_id=uuid.uuid4(),
        window_key="D30",
        confidence_state="STABLE",
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


def test_business_self_funding_requires_two_trusted_profitable_cycles(
    db: Session,
) -> None:
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
        db.add(
            CostEvent(
                provider_key="test-provider",
                cost_scope_type="CHANNEL",
                cost_scope_id=channel_id,
                amount=Decimal("80"),
                currency="USD",
                cost_type="ACTUAL",
                created_at=start + timedelta(days=1),
            )
        )
        db.flush()
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
    assert (
        db.scalar(
            select(PlatformEnforcementIncident).where(
                PlatformEnforcementIncident.id == incident.id
            )
        ).state
        == "OPEN"
    )


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


def test_business_statuses_create_idempotent_actions_and_truthful_dashboard(
    db: Session,
) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    service.record_payment_status(
        company_id=company_id,
        payee_ref="payee://owner",
        tax_state="PENDING",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="ON_HOLD",
        source_type="OPERATOR_ATTESTATION",
        source_ref="evidence://payment/pending",
        confidence_state="LOW",
        source_updated_at=now,
        valid_until=now - timedelta(days=1),
    )
    service.record_monetization_status(
        company_id=company_id,
        channel_workspace_id=channel_id,
        platform="YOUTUBE",
        program_type="YPP",
        eligibility_state="ELIGIBLE",
        enrollment_state="ACTIVE",
        restriction_state="RESTRICTED",
        source_type="API",
        source_ref="youtube://monetization/restricted",
        confidence_state="HIGH",
        source_updated_at=now,
        valid_until=now + timedelta(days=7),
    )
    first_actions = list(
        db.scalars(select(BusinessActionItem).where(BusinessActionItem.state == "OPEN"))
    )
    assert {item.reason_code for item in first_actions} >= {
        "PAYMENT_TAX_VERIFICATION_REQUIRED",
        "PAYMENT_HOLD_OPEN",
        "PAYMENT_STATUS_STALE_OR_UNTRUSTED",
        "MONETIZATION_RESTRICTED",
    }
    dashboard = service.dashboard(
        company_id=company_id, channel_workspace_id=channel_id
    )
    assert dashboard.payment_state.startswith("ACTION_REQUIRED:")
    assert dashboard.monetization_state.startswith("ACTION_REQUIRED:")
    service.record_payment_status(
        company_id=company_id,
        payee_ref="payee://owner",
        tax_state="VERIFIED",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="NONE",
        source_type="OPERATOR_ATTESTATION",
        source_ref="evidence://payment/ready",
        confidence_state="HIGH",
        source_updated_at=now,
        valid_until=now + timedelta(days=30),
    )
    resolved = list(
        db.scalars(
            select(BusinessActionItem).where(
                BusinessActionItem.action_type == "RESOLVE_PAYMENT_PROFILE"
            )
        )
    )
    assert resolved and all(item.state == "RESOLVED" for item in resolved)


def test_continuation_recommendation_is_durable_and_human_gated(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    for offset in (60, 30):
        db.add(
            ChannelPnlSnapshot(
                id=uuid.uuid4(),
                company_id=company_id,
                channel_workspace_id=channel_id,
                period_start=now - timedelta(days=offset),
                period_end=now - timedelta(days=offset - 30),
                currency="USD",
                estimated_revenue=Decimal("0"),
                locked_revenue=Decimal("0"),
                finalized_revenue=Decimal("0"),
                cash_received=Decimal("0"),
                reversed_revenue=Decimal("0"),
                direct_cost=Decimal("10"),
                allocated_ops_cost=Decimal("0"),
                contribution_margin=Decimal("-10"),
                calculation_version=f"test-{offset}",
                source_snapshot_refs=[],
                content_hash=f"{offset}".zfill(64),
            )
        )
    db.add(
        SelfFundingAssessment(
            id=uuid.uuid4(),
            company_id=company_id,
            channel_workspace_id=channel_id,
            assessment_window_end=now,
            policy_version="test",
            decision="FUNDED_EXPERIMENT",
            reason_codes=[],
            input_refs=[],
            assessment_hash="a" * 64,
        )
    )
    db.flush()
    frozen = service.freeze_continuation_recommendation(
        company_id=company_id,
        channel_workspace_id=channel_id,
        evaluated_at=now,
    )
    replay = service.freeze_continuation_recommendation(
        company_id=company_id,
        channel_workspace_id=channel_id,
        evaluated_at=now + timedelta(minutes=1),
    )
    assert frozen.id == replay.id
    assert frozen.recommendation == "KILL_REVIEW"
    assert frozen.human_decision_required is True
    assert db.get(ContinuationCapitalReview, frozen.id) is not None
    assert (
        db.scalar(
            select(BusinessActionItem).where(
                BusinessActionItem.action_type == "HUMAN_CAPITAL_REVIEW",
                BusinessActionItem.reason_code == "KILL_REVIEW",
            )
        )
        is not None
    )


def test_publication_coordinator_seeds_learning_and_audience_delivery(
    db: Session,
) -> None:
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
        compiled_policy_snapshot_hash="c" * 64,
    )
    assert result["learning_fingerprint_id"]
    assert result["audience_delivery_plan_id"]
    fingerprint = db.get(
        LearningEquivalenceFingerprint,
        uuid.UUID(result["learning_fingerprint_id"]),
    )
    assert fingerprint is not None
    assert fingerprint.profile_snapshot_hash == "c" * 64
    assert (
        db.scalar(
            select(AudienceDeliveryPlan).where(
                AudienceDeliveryPlan.publication_receipt_id == receipt.id
            )
        )
        is not None
    )


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

    clean_root = tmp_path / "clean"
    clean_app = clean_root / "app"
    clean_app.mkdir(parents=True)
    (clean_app / "service.py").write_text(
        "def execute(profile_snapshot):\n    return profile_snapshot\n",
        encoding="utf-8",
    )
    clean_audit = ArchitectureDebtAuditService().audit(clean_root)
    assert clean_audit.one_engine_many_profiles is True

    a, b = uuid.uuid4(), uuid.uuid4()
    not_proven = ArchitectureDebtAuditService.portfolio_proof(
        verified_publications_by_channel={a: 1},
        compiled_profile_hash_by_channel={a: "p1"},
        code_audit=audit,
    )
    assert not_proven["state"] == "NOT_PROVEN"
    proven = ArchitectureDebtAuditService.portfolio_proof(
        verified_publications_by_channel={a: 1, b: 2},
        compiled_profile_hash_by_channel={a: "p1", b: "p2"},
        code_audit=clean_audit,
    )
    assert proven["state"] == "PROVEN"


def test_publication_closeout_requires_the_compiled_policy_hash(db: Session) -> None:
    candidate = SimpleNamespace(
        company_id=uuid.uuid4(),
        channel_workspace_id=uuid.uuid4(),
        video_project_id=uuid.uuid4(),
        target_market_lineage={},
        content_mode="STANDALONE",
        series_plan_id=None,
    )
    receipt = SimpleNamespace(id=uuid.uuid4())
    coordinator = RemainingDebtCloseoutCoordinator(db)
    assert (
        "compiled_policy_snapshot_hash"
        in inspect.signature(coordinator.on_publication_verified).parameters
    )
    assert "compiled_policy_snapshot_hash" in inspect.getsource(
        ProductionPublishService.verify_confirmation
    )
    with pytest.raises(
        ValidationFailureError, match="COMPILED_POLICY_SNAPSHOT_HASH_INVALID"
    ):
        coordinator.on_publication_verified(
            candidate=candidate,
            public_receipt=receipt,
            observed_at=utc_now(),
            compiled_policy_snapshot_hash="not-a-sha256",
        )


def test_analytics_authority_rejects_stale_incomplete_and_scope_mismatch() -> None:
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        analytics_sync_run_id=uuid.uuid4(),
        uploaded_video_id=uuid.uuid4(),
        platform="YOUTUBE",
        freshness_state="FRESH",
        confidence_level="HIGH",
        normalized_metrics_blob={
            "views": {"value": 1200},
            "impressions": {"value": 8000},
            "average_view_duration_seconds": {"value": 180},
            "average_view_percentage": {"value": 52},
        },
        captured_at=utc_now(),
    )
    availability = SimpleNamespace(
        id=uuid.uuid4(),
        uploaded_video_id=snapshot.uploaded_video_id,
        analytics_sync_run_id=snapshot.analytics_sync_run_id,
        platform="YOUTUBE",
        freshness_state="FRESH",
        confidence_level="HIGH",
        availability_blob={
            metric: {"state": "AVAILABLE"}
            for metric in (
                "views",
                "impressions",
                "average_view_duration_seconds",
                "average_view_percentage",
            )
        },
    )
    source_window = SimpleNamespace(
        id=uuid.uuid4(),
        window_type="D30",
        uploaded_video_id=snapshot.uploaded_video_id,
        analytics_snapshot_id=snapshot.id,
        state="DIAGNOSTICS_COMPLETE",
        observed_to=utc_now(),
    )
    decision = LearningAuthorityService.analytics_data_authority_decision(
        snapshot=snapshot,
        availability=availability,
        source_window=source_window,
        requested_window_key="M11",
    )
    assert decision["maturity_state"] == "MATURE"
    assert decision["confidence_state"] == "ACTION_READY"
    snapshot.freshness_state = "STALE"
    with pytest.raises(ValidationFailureError, match="ANALYTICS_SOURCE_STALE"):
        LearningAuthorityService.analytics_data_authority_decision(
            snapshot=snapshot,
            availability=availability,
            source_window=source_window,
            requested_window_key="M11",
        )
    snapshot.freshness_state = "FRESH"
    availability.availability_blob["impressions"] = {"state": "UNKNOWN"}
    with pytest.raises(
        ValidationFailureError, match="ANALYTICS_REQUIRED_METRICS_INCOMPLETE"
    ):
        LearningAuthorityService.analytics_data_authority_decision(
            snapshot=snapshot,
            availability=availability,
            source_window=source_window,
            requested_window_key="M11",
        )
    availability.availability_blob["impressions"] = {"state": "AVAILABLE"}
    availability.uploaded_video_id = uuid.uuid4()
    with pytest.raises(
        ValidationFailureError, match="ANALYTICS_AVAILABILITY_SCOPE_MISMATCH"
    ):
        LearningAuthorityService.analytics_data_authority_decision(
            snapshot=snapshot,
            availability=availability,
            source_window=source_window,
            requested_window_key="M11",
        )


def test_business_pnl_uses_cost_events_and_review_actions_are_human_gated(
    db: Session,
) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    end = utc_now()
    start = end - timedelta(days=30)
    for state, amount, source_ref in (
        ("PENDING", "100", "pending"),
        ("FINALIZED", "200", "finalized"),
        ("REVERSED", "50", "reversed"),
    ):
        service.record_revenue(
            company_id=company_id,
            channel_workspace_id=channel_id,
            source="YOUTUBE",
            amount_state=state,
            amount=amount,
            currency="USD",
            period_start=start,
            period_end=end,
            source_ref=f"youtube://{source_ref}",
            source_updated_at=end,
            confidence_state="HIGH",
        )
    db.add(
        CostEvent(
            provider_key="test-provider",
            cost_scope_type="CHANNEL",
            cost_scope_id=channel_id,
            amount=Decimal("25"),
            currency="USD",
            cost_type="ACTUAL",
            created_at=start + timedelta(days=1),
        )
    )
    db.flush()
    pnl = service.build_channel_pnl(
        company_id=company_id,
        channel_workspace_id=channel_id,
        period_start=start,
        period_end=end,
        currency="USD",
        direct_cost=Decimal("25"),
    )
    assert pnl.locked_revenue == Decimal("0.000000")
    assert pnl.contribution_margin == Decimal("125.000000")
    with pytest.raises(ValidationFailureError, match="ACTUAL_COST_AUTHORITY_MISMATCH"):
        service.build_channel_pnl(
            company_id=company_id,
            channel_workspace_id=channel_id,
            period_start=start,
            period_end=end,
            currency="USD",
            direct_cost=Decimal("26"),
            calculation_version="test-cost-mismatch",
        )
    for offset in (60, 30):
        db.add(
            ChannelPnlSnapshot(
                id=uuid.uuid4(),
                company_id=company_id,
                channel_workspace_id=channel_id,
                period_start=end - timedelta(days=offset),
                period_end=end - timedelta(days=offset - 30),
                currency="USD",
                estimated_revenue=0,
                locked_revenue=0,
                finalized_revenue=0,
                cash_received=0,
                reversed_revenue=0,
                direct_cost=Decimal("10"),
                allocated_ops_cost=0,
                contribution_margin=Decimal("-10"),
                calculation_version=f"negative-{offset}",
                source_snapshot_refs=[],
                content_hash=uuid.uuid4().hex * 2,
            )
        )
    db.add(
        SelfFundingAssessment(
            id=uuid.uuid4(),
            company_id=company_id,
            channel_workspace_id=channel_id,
            assessment_window_end=end,
            policy_version="test-self-funding",
            decision="FUNDED_EXPERIMENT",
            reason_codes=[],
            input_refs=[],
            assessment_hash="f" * 64,
        )
    )
    db.flush()
    projection = service.continuation_recommendation(channel_workspace_id=channel_id)
    assert projection["action"] == "PIVOT"
    assert projection["human_decision_required"] is True


def test_closeout_records_are_strictly_channel_isolated(db: Session) -> None:
    company_id = uuid.uuid4()
    channel_a, channel_b = uuid.uuid4(), uuid.uuid4()
    learning = LearningAuthorityService(db)
    fingerprint_a = learning.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_a,
        source_entity_ref="publication://shared-a",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="a" * 64,
        target_market="US",
        content_language="en-US",
        format_key="STANDALONE",
        normalized_features={"profile": "A"},
    )
    fingerprint_b = learning.create_fingerprint(
        company_id=company_id,
        channel_workspace_id=channel_b,
        source_entity_ref="publication://shared-b",
        content_product_type="EDITORIAL_NARRATED_VIDEO",
        series_plan_id=None,
        profile_snapshot_hash="b" * 64,
        target_market="VN",
        content_language="vi-VN",
        format_key="STANDALONE",
        normalized_features={"profile": "B"},
    )
    assert fingerprint_a.profile_snapshot_hash != fingerprint_b.profile_snapshot_hash
    assert list(
        db.scalars(
            select(LearningEquivalenceFingerprint).where(
                LearningEquivalenceFingerprint.channel_workspace_id == channel_b
            )
        )
    ) == [fingerprint_b]
    learning.create_audience_delivery_plan(
        company_id=company_id,
        channel_workspace_id=channel_a,
        video_project_id=uuid.uuid4(),
        publication_receipt_id=uuid.uuid4(),
        target_markets=["US"],
        target_languages=["en-US"],
        packaging_refs=["package://a"],
        playlist_refs=[],
    )
    delivery_b = learning.create_audience_delivery_plan(
        company_id=company_id,
        channel_workspace_id=channel_b,
        video_project_id=uuid.uuid4(),
        publication_receipt_id=uuid.uuid4(),
        target_markets=["VN"],
        target_languages=["vi-VN"],
        packaging_refs=["package://b"],
        playlist_refs=[],
    )
    assert delivery_b.channel_workspace_id == channel_b
    audit = ArchitectureDebtAuditService().code_isolation_proof(
        ArchitectureDebtAuditService().audit(Path(__file__).parents[1])
    )
    assert audit["live_portfolio_state"] == "LIVE_PORTFOLIO_PROOF_NOT_PROVEN"
