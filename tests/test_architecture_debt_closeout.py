from __future__ import annotations

import json
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models.architecture_closeout import (
    AffiliateLinkRegistry,
    AffiliateOfferSnapshot,
    SeriesEpisodeAttemptAuthority,
    SeriesPublicOrdinalAuthority,
)
from app.db.models.channel import ChannelWorkspace
from app.db.models.foundation import Company, User
from app.db.models.m10 import LearningCandidateGenerationRun
from app.db.models.m11 import LearningReviewDecision
from app.db.models.ops import CostEvent
from app.db.models.r3d5 import ChannelMemoryItem
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.youtube_delivery import (
    PublicPublicationReceipt,
    YouTubeSeriesEpisodeBinding,
)
from app.services.business_os import BusinessOperatingService
from app.services.config_registry import content_hash
from app.services.learning_authority_closeout import LearningAuthorityCloseoutService
from app.services.scale_closeout import OneEngineManyProfilesAudit
from app.services.series_authority_closeout import SeriesAuthorityCloseoutService


@pytest.fixture
def db_session():
    engine = create_engine(get_settings().database_url, future=True, pool_pre_ping=True)
    session = Session(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()


def _identity(session: Session, label: str) -> tuple[Company, ChannelWorkspace, User]:
    company = Company(name=f"Closeout {label}", slug=f"closeout-{label}-{uuid.uuid4().hex[:8]}")
    session.add(company)
    session.flush()
    channel = ChannelWorkspace(
        company_id=company.id,
        key=f"channel-{label}-{uuid.uuid4().hex[:8]}",
        name=f"Channel {label}",
        status="active",
        primary_language="en",
        primary_timezone="UTC",
        default_timezone="UTC",
        target_market="US",
    )
    user = User(
        email=f"{label}-{uuid.uuid4().hex[:8]}@example.test",
        display_name=f"Reviewer {label}",
    )
    session.add_all([channel, user])
    session.flush()
    return company, channel, user


def _series_plan_run(
    session: Session,
    *,
    company: Company,
    channel: ChannelWorkspace,
    user: User,
    label: str,
    capacity: int = 10,
) -> tuple[SeriesPlan, SeriesRun]:
    plan = SeriesPlan(
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        stable_series_key=f"series-{label}",
        display_name=f"Series {label}",
        editorial_promise="A bounded editorial promise",
        allowed_production_lanes=["LONG_FORM"],
        episode_role_policy={},
        state="DRAFT",
        version=1,
        created_by_user_id=user.id,
    )
    run = SeriesRun(
        series_plan_id=uuid.uuid4(),  # replaced after plan flush
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        run_key=f"run-{label}-{uuid.uuid4().hex[:6]}",
        run_number=1,
        capacity=capacity,
        first_episode_number=1,
        next_episode_number=1,
        reserved_episode_count=0,
        published_episode_count=0,
        state="ACTIVE",
        state_reason_codes=[],
        created_by_user_id=user.id,
        approved_by_user_id=user.id,
        approved_at=utc_now(),
        activated_at=utc_now(),
    )
    session.execute(text("SET session_replication_role = replica"))
    session.add(plan)
    session.flush()
    run.series_plan_id = plan.id
    session.add(run)
    session.flush()
    session.execute(text("SET session_replication_role = origin"))
    return plan, run


def test_fixed_series_arc_is_editorial_length_not_run_capacity(db_session: Session) -> None:
    company, channel, user = _identity(db_session, "series-fixed")
    plan, run = _series_plan_run(
        db_session, company=company, channel=channel, user=user, label="fixed", capacity=10
    )
    service = SeriesAuthorityCloseoutService(db_session)
    arc = service.create_arc(
        series_plan_id=plan.id,
        planning_mode="FIXED_COUNT",
        planned_episode_count=3,
        editorial_coverage={"promise": "three-part arc"},
    )
    for position in range(1, 4):
        service.add_blueprint(
            arc_version_id=arc.id,
            blueprint_key=f"ep-{position}",
            editorial_position=position,
            editorial_purpose=f"Purpose {position}",
            coverage_contract={"coverage": position},
        )
    service.approve_arc(
        arc_version_id=arc.id,
        actor_user_id=user.id,
        evidence_refs=[{"type": "human_approval", "ref": "fixture"}],
    )
    progress = service.progress(plan.id)
    assert run.capacity == 10
    assert progress.planned_episode_count == 3
    assert progress.public_episode_count == 0
    assert progress.remaining_episode_count == 3


def test_fixed_arc_rejects_incomplete_episode_blueprint_coverage(db_session: Session) -> None:
    company, channel, user = _identity(db_session, "series-coverage")
    plan, _run = _series_plan_run(
        db_session, company=company, channel=channel, user=user, label="coverage"
    )
    service = SeriesAuthorityCloseoutService(db_session)
    arc = service.create_arc(
        series_plan_id=plan.id,
        planning_mode="FIXED_COUNT",
        planned_episode_count=2,
        editorial_coverage={},
    )
    service.add_blueprint(
        arc_version_id=arc.id,
        blueprint_key="ep-1",
        editorial_position=1,
        editorial_purpose="Only one",
        coverage_contract={},
    )
    with pytest.raises(Exception, match="SERIES_ARC_EDITORIAL_COVERAGE_INCOMPLETE"):
        service.approve_arc(
            arc_version_id=arc.id,
            actor_user_id=user.id,
            evidence_refs=[{"ref": "approval"}],
        )


def test_public_ordinals_continue_across_runs_and_drive_playlist_position(db_session: Session) -> None:
    company, channel, user = _identity(db_session, "series-ordinal")
    plan, run1 = _series_plan_run(
        db_session, company=company, channel=channel, user=user, label="ordinal", capacity=10
    )
    service = SeriesAuthorityCloseoutService(db_session)
    arc = service.create_arc(
        series_plan_id=plan.id,
        planning_mode="FIXED_COUNT",
        planned_episode_count=3,
        editorial_coverage={},
    )
    for position in range(1, 4):
        service.add_blueprint(
            arc_version_id=arc.id,
            blueprint_key=f"ordinal-{position}",
            editorial_position=position,
            editorial_purpose=f"Position {position}",
            coverage_contract={},
        )
    service.approve_arc(
        arc_version_id=arc.id,
        actor_user_id=user.id,
        evidence_refs=[{"ref": "approval"}],
    )
    run2 = SeriesRun(
        series_plan_id=plan.id,
        company_id=company.id,
        channel_workspace_id=channel.id,
        channel_profile_version_id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        run_key=f"run-ordinal-2-{uuid.uuid4().hex[:6]}",
        run_number=2,
        capacity=10,
        first_episode_number=1,
        next_episode_number=1,
        reserved_episode_count=0,
        published_episode_count=0,
        state="ACTIVE",
        state_reason_codes=[],
        created_by_user_id=user.id,
        approved_by_user_id=user.id,
        approved_at=utc_now(),
        activated_at=utc_now(),
    )
    db_session.execute(text("SET session_replication_role = replica"))
    db_session.add(run2)
    db_session.flush()
    db_session.execute(text("SET session_replication_role = origin"))

    attempts: list[SeriesEpisodeAttemptAuthority] = []
    for run, technical in ((run1, 1), (run1, 2), (run2, 1)):
        attempts.append(
            service.register_attempt(
                series_run_id=run.id,
                technical_attempt_number=technical,
                reservation_ref=f"fixture://{run.id}/{technical}",
            )
        )
    receipt_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    project_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    db_session.execute(text("SET session_replication_role = replica"))
    for index, attempt in enumerate(attempts, start=1):
        db_session.add(
            SeriesPublicOrdinalAuthority(
                company_id=company.id,
                channel_workspace_id=channel.id,
                series_plan_id=plan.id,
                series_run_id=attempt.series_run_id,
                series_arc_version_id=arc.id,
                episode_attempt_authority_id=attempt.id,
                video_project_id=project_ids[index - 1],
                public_publication_receipt_id=receipt_ids[index - 1],
                public_episode_ordinal=index,
                authority_hash=content_hash({"ordinal": index, "plan": str(plan.id)}),
            )
        )
    db_session.flush()
    db_session.execute(text("SET session_replication_role = origin"))
    progress = service.progress(plan.id)
    assert progress.public_episode_count == 3
    assert progress.next_public_ordinal == 4
    service._auto_completion_pending(run_id=run2.id, arc=arc)
    assert run2.state == "COMPLETION_PENDING"

    public_receipt = PublicPublicationReceipt(
        id=receipt_ids[-1],
        company_id=company.id,
        channel_workspace_id=channel.id,
        video_project_id=project_ids[-1],
        final_review_candidate_id=uuid.uuid4(),
        final_video_decision_id=uuid.uuid4(),
        manual_publish_confirmation_id=uuid.uuid4(),
        youtube_private_stage_id=None,
        platform_channel_id="channel-test",
        platform_video_id="video-test",
        public_url="https://youtube.test/video-test",
        observed_privacy_status="PUBLIC",
        observed_published_at=utc_now(),
        observed_metadata={},
        observed_metadata_hash="a" * 64,
        verification_evidence_ref="fixture://public",
        verification_evidence_hash="b" * 64,
        receipt_hash="c" * 64,
    )
    binding = YouTubeSeriesEpisodeBinding(
        youtube_series_playlist_binding_id=uuid.uuid4(),
        series_plan_id=plan.id,
        series_run_id=run2.id,
        technical_episode_number=1,
        video_project_id=project_ids[-1],
        youtube_private_stage_id=uuid.uuid4(),
        youtube_video_id="video-test",
        state="PRIVATE_UPLOADED",
        binding_hash="d" * 64,
    )
    db_session.execute(text("SET session_replication_role = replica"))
    db_session.add_all([public_receipt, binding])
    db_session.flush()
    db_session.execute(text("SET session_replication_role = origin"))
    authority = db_session.scalar(
        select(SeriesPublicOrdinalAuthority).where(
            SeriesPublicOrdinalAuthority.public_episode_ordinal == 3,
            SeriesPublicOrdinalAuthority.series_plan_id == plan.id,
        )
    )
    service._project_to_youtube_binding(
        candidate=SimpleNamespace(video_project_id=project_ids[-1]),
        receipt=public_receipt,
        authority=authority,
    )
    assert binding.public_episode_ordinal == 3
    assert binding.expected_position == 2
    assert binding.public_ordinal_authority_ref.startswith("series-public-ordinal://")


def test_rolling_series_never_completes_from_capacity(db_session: Session) -> None:
    company, channel, user = _identity(db_session, "series-rolling")
    plan, run = _series_plan_run(
        db_session, company=company, channel=channel, user=user, label="rolling", capacity=1
    )
    service = SeriesAuthorityCloseoutService(db_session)
    arc = service.create_arc(
        series_plan_id=plan.id,
        planning_mode="ROLLING",
        planned_episode_count=None,
        editorial_coverage={"rolling": True},
    )
    service.approve_arc(
        arc_version_id=arc.id,
        actor_user_id=user.id,
        evidence_refs=[{"ref": "approval"}],
    )
    service._auto_completion_pending(run_id=run.id, arc=arc)
    assert run.state == "ACTIVE"
    assert service.progress(plan.id).remaining_episode_count is None


def _insert_learning_cohort(
    session: Session,
    *,
    company: Company,
    channel: ChannelWorkspace,
    count: int,
    learning: str,
) -> list[uuid.UUID]:
    fingerprint = content_hash(
        {
            "schema_version": "vcos.learning-equivalence.v1",
            "candidate_type": "PACKAGING_PATTERN",
            "recommended_scope": "CHANNEL",
            "normalized_learning": " ".join(learning.lower().split()),
        }
    )
    candidate_ids: list[uuid.UUID] = []
    session.execute(text("SET session_replication_role = replica"))
    for index in range(count):
        run_id = uuid.uuid4()
        candidate_id = uuid.uuid4()
        eligibility_id = uuid.uuid4()
        uploaded_id = uuid.uuid4()
        session.execute(
            text(
                """
                INSERT INTO learning_candidate_generation_runs
                (id, company_id, channel_workspace_id, uploaded_video_id, run_mode, run_state,
                 generated_candidate_count, reason_codes, metadata, created_at, updated_at)
                VALUES (:id,:company,:channel,:uploaded,'RULE_BASED','COMPLETED',1,'[]'::jsonb,
                        CAST(:metadata AS jsonb),now(),now())
                """
            ),
            {
                "id": run_id,
                "company": company.id,
                "channel": channel.id,
                "uploaded": uploaded_id,
                "metadata": json.dumps({"maturity": "MATURE", "automated_learning": True}),
            },
        )
        session.execute(
            text(
                """
                INSERT INTO learning_candidates
                (id,generation_run_id,company_id,channel_workspace_id,uploaded_video_id,candidate_type,
                 candidate_state,operator_summary,friendly_status,candidate_summary,suggested_learning,
                 recommended_scope,confidence_label,risk_level,source_refs,diagnostic_refs,recovery_refs,
                 metric_refs,policy_flags,rights_flags,limitations,counter_evidence,technical_appendix,
                 equivalence_fingerprint,created_at,updated_at)
                VALUES (:id,:run,:company,:channel,:uploaded,'PACKAGING_PATTERN','READY_FOR_HUMAN_REVIEW',
                        'summary','ready','candidate',:learning,'CHANNEL','HIGH','LOW','[]'::jsonb,'[]'::jsonb,
                        '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'{}'::jsonb,
                        :fingerprint,now(),now())
                """
            ),
            {
                "id": candidate_id,
                "run": run_id,
                "company": company.id,
                "channel": channel.id,
                "uploaded": uploaded_id,
                "learning": learning,
                "fingerprint": fingerprint,
            },
        )
        session.execute(
            text(
                """
                INSERT INTO learning_promotion_eligibility_runs
                (id,learning_candidate_id,result,min_evidence_met,metric_freshness_ok,policy_flags_ok,
                 rights_flags_ok,confidence_label,risk_level,blockers,warnings,reason_codes,
                 operator_summary,created_at)
                VALUES (:id,:candidate,'ELIGIBLE_FOR_REVIEW',true,true,true,true,'HIGH','LOW',
                        '[]'::jsonb,'[]'::jsonb,'[]'::jsonb,'eligible',now())
                """
            ),
            {"id": eligibility_id, "candidate": candidate_id},
        )
        session.execute(
            text("UPDATE learning_candidates SET eligibility_run_id=:eligibility WHERE id=:candidate"),
            {"eligibility": eligibility_id, "candidate": candidate_id},
        )
        candidate_ids.append(candidate_id)
    session.execute(text("SET session_replication_role = origin"))
    session.flush()
    return candidate_ids


def test_system_learning_requires_exact_equivalence_recurrence_and_eligibility(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "learning")
    candidates = _insert_learning_cohort(
        db_session,
        company=company,
        channel=channel,
        count=3,
        learning="Keep title promise aligned with first 30 seconds",
    )
    service = LearningAuthorityCloseoutService(db_session)
    preflight = service.system_promotion_preflight(
        candidate_id=candidates[-1],
        policy_version="learning-v1",
        policy_hash="a" * 64,
    )
    assert preflight.result == "PROMOTED"
    assert preflight.distinct_mature_source_count == 3
    receipt = service.record_system_promotion_preflight(
        candidate_id=candidates[-1],
        policy_version="learning-v1",
        policy_hash="a" * 64,
    )
    assert receipt.result == "PROMOTED"


def test_system_learning_is_frozen_by_platform_enforcement(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "learning-enforcement")
    candidates = _insert_learning_cohort(
        db_session,
        company=company,
        channel=channel,
        count=3,
        learning="Use evidence-led hook before feature list",
    )
    BusinessOperatingService(db_session).open_enforcement_incident(
        company_id=company.id,
        channel_workspace_id=channel.id,
        platform="YOUTUBE",
        incident_type="LIMITED_ADS",
        scope="CHANNEL",
        severity="HIGH",
        source_status="LIMITED",
        evidence_refs=[{"ref": "studio://fixture"}],
        freeze_learning=True,
    )
    preflight = LearningAuthorityCloseoutService(db_session).system_promotion_preflight(
        candidate_id=candidates[-1],
        policy_version="learning-v1",
        policy_hash="b" * 64,
    )
    assert preflight.result == "BLOCKED"
    assert "SYSTEM_PROMOTION_ENFORCEMENT_FREEZE_ACTIVE" in preflight.reason_codes


def test_learning_audit_semantics_removes_false_no_auto_promotion_marker(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "learning-audit")
    run = LearningCandidateGenerationRun(
        company_id=company.id,
        channel_workspace_id=channel.id,
        run_mode="RULE_BASED",
        run_state="COMPLETED",
        generated_candidate_count=0,
        reason_codes=["NO_AUTO_PROMOTION"],
        metadata_={"automated_learning": True, "maturity": "MATURE"},
    )
    db_session.add(run)
    db_session.flush()
    db_session.refresh(run)
    assert "NO_AUTO_PROMOTION" not in run.reason_codes
    assert "SYSTEM_GOVERNED_PROMOTION_PATH" in run.reason_codes


def test_m11_database_guard_blocks_second_terminal_decision(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "learning-exactly-once")
    candidate_id = _insert_learning_cohort(
        db_session,
        company=company,
        channel=channel,
        count=1,
        learning="One terminal review only",
    )[0]
    first = LearningReviewDecision(
        learning_candidate_id=candidate_id,
        company_id=company.id,
        channel_workspace_id=channel.id,
        action="REJECT",
        decision_state="RECORDED",
        actor_role="LEARNING_REVIEWER",
        reason_codes=[],
        evidence_refs=[],
        technical_appendix={},
    )
    db_session.add(first)
    db_session.flush()
    with pytest.raises(DBAPIError, match="LEARNING_REVIEW_DECISION_ALREADY_TERMINAL"):
        with db_session.begin_nested():
            db_session.add(
                LearningReviewDecision(
                    learning_candidate_id=candidate_id,
                    company_id=company.id,
                    channel_workspace_id=channel.id,
                    action="APPROVE",
                    decision_state="RECORDED",
                    actor_role="LEARNING_REVIEWER",
                    reason_codes=[],
                    evidence_refs=[],
                    technical_appendix={},
                )
            )
            db_session.flush()


def test_self_funding_uses_finalized_revenue_not_estimates(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "business")
    service = BusinessOperatingService(db_session)
    now = utc_now()
    service.record_payment_status(
        company_id=company.id,
        payee_ref="youtube-payee",
        tax_state="VERIFIED",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="NONE",
        source_type="OPERATOR_ATTESTATION",
        source_updated_at=now,
        evidence_ref="attestation://payment",
    )
    service.record_monetization_status(
        company_id=company.id,
        channel_workspace_id=channel.id,
        platform="YOUTUBE",
        destination_ref="youtube://channel",
        program_type="YPP",
        eligibility_state="ELIGIBLE",
        enrollment_state="ACTIVE",
        restriction_state="NONE",
        country_eligibility_state="ELIGIBLE",
        source_type="OPERATOR_ATTESTATION",
        source_updated_at=now,
        evidence_ref="attestation://ypp",
    )
    periods = [
        (now - timedelta(days=60), now - timedelta(days=31)),
        (now - timedelta(days=30), now - timedelta(seconds=1)),
    ]
    for index, (start, end) in enumerate(periods):
        service.record_revenue_snapshot(
            company_id=company.id,
            channel_workspace_id=channel.id,
            source=f"YOUTUBE-{index}",
            period_start=start,
            period_end=end,
            estimated_amount=Decimal("1000"),
            finalized_or_locked_amount=Decimal("25"),
            reversed_amount=Decimal("0"),
            cash_received_amount=Decimal("20"),
            cash_receivable_amount=Decimal("5"),
            source_updated_at=now,
        )
        db_session.add(
            CostEvent(
                provider_key="fixture",
                cost_scope_type="CHANNEL",
                cost_scope_id=channel.id,
                amount=Decimal("10"),
                currency="USD",
                cost_type="PRODUCTION",
                created_at=start + timedelta(days=1),
            )
        )
        db_session.flush()
        service.build_channel_pnl(
            company_id=company.id,
            channel_workspace_id=channel.id,
            period_start=start,
            period_end=end,
        )
    decision = service.self_funding_gate(
        company_id=company.id,
        channel_workspace_id=channel.id,
    )
    assert decision.result == "SELF_FUNDING"
    assert decision.trailing_finalized_revenue == Decimal("50")
    assert decision.trailing_cost == Decimal("20")


def test_estimated_revenue_alone_never_passes_self_funding(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "business-estimate")
    service = BusinessOperatingService(db_session)
    now = utc_now()
    service.record_payment_status(
        company_id=company.id,
        payee_ref="payee",
        tax_state="VERIFIED",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="NONE",
        source_type="OPERATOR_ATTESTATION",
        source_updated_at=now,
        evidence_ref="attestation://payment",
    )
    service.record_monetization_status(
        company_id=company.id,
        channel_workspace_id=channel.id,
        platform="YOUTUBE",
        destination_ref="youtube://channel",
        program_type="YPP",
        eligibility_state="ELIGIBLE",
        enrollment_state="ACTIVE",
        restriction_state="NONE",
        country_eligibility_state="ELIGIBLE",
        source_type="OPERATOR_ATTESTATION",
        source_updated_at=now,
        evidence_ref="attestation://ypp",
    )
    for index in range(2):
        end = now - timedelta(days=index * 31 + 1)
        start = end - timedelta(days=30)
        service.record_revenue_snapshot(
            company_id=company.id,
            channel_workspace_id=channel.id,
            source=f"ESTIMATE-{index}",
            period_start=start,
            period_end=end,
            estimated_amount=Decimal("5000"),
            finalized_or_locked_amount=Decimal("0"),
            reversed_amount=Decimal("0"),
            cash_received_amount=Decimal("0"),
            cash_receivable_amount=Decimal("0"),
            source_updated_at=now,
        )
        db_session.add(
            CostEvent(
                provider_key="fixture",
                cost_scope_type="CHANNEL",
                cost_scope_id=channel.id,
                amount=Decimal("10"),
                currency="USD",
                cost_type="PRODUCTION",
                created_at=start + timedelta(days=1),
            )
        )
        db_session.flush()
        service.build_channel_pnl(
            company_id=company.id,
            channel_workspace_id=channel.id,
            period_start=start,
            period_end=end,
        )
    decision = service.self_funding_gate(company_id=company.id, channel_workspace_id=channel.id)
    assert decision.result == "SUBSIDIZED_OR_RESTRICTED"
    assert "FINALIZED_REVENUE_COST_COVERAGE_INSUFFICIENT" in decision.reason_codes


def test_affiliate_disclosure_and_offer_expiry_are_fail_closed(db_session: Session) -> None:
    company, channel, _user = _identity(db_session, "affiliate")
    offer = AffiliateOfferSnapshot(
        company_id=company.id,
        merchant="Fixture Merchant",
        offer_ref="merchant://offer",
        product_ref="product://1",
        commission_model="10 percent",
        attribution_window_text="30 days",
        terms_hash="a" * 64,
        effective_at=utc_now() - timedelta(days=1),
        expires_at=utc_now() + timedelta(days=30),
        disclosure_required=True,
        state="ACTIVE",
        content_hash=content_hash({"offer": "fixture"}),
    )
    db_session.add(offer)
    db_session.flush()
    link = AffiliateLinkRegistry(
        affiliate_offer_snapshot_id=offer.id,
        channel_workspace_id=channel.id,
        destination_url="https://example.test/product",
        redirect_url=None,
        utm_template_version="v1",
        disclosure_required=True,
        active_state="ACTIVE",
        content_hash=content_hash({"link": "fixture"}),
    )
    db_session.add(link)
    db_session.flush()
    service = BusinessOperatingService(db_session)
    with pytest.raises(Exception, match="AFFILIATE_DISCLOSURE_REQUIRED"):
        service.validate_affiliate_use(
            channel_workspace_id=channel.id,
            link_id=link.id,
            disclosure_present=False,
        )
    assert service.validate_affiliate_use(
        channel_workspace_id=channel.id,
        link_id=link.id,
        disclosure_present=True,
    ).id == link.id


def test_scale_audit_rejects_niche_branches_and_current_repo_is_profile_driven(tmp_path: Path) -> None:
    bad_root = tmp_path / "bad"
    (bad_root / "app").mkdir(parents=True)
    (bad_root / "app" / "bad.py").write_text("if niche == 'x':\n    pass\n", encoding="utf-8")
    violations = OneEngineManyProfilesAudit().scan_active_source(bad_root)
    assert any(item.startswith("NICHE_RUNTIME_BRANCH") for item in violations)

    repository_root = Path(__file__).resolve().parents[1]
    assert OneEngineManyProfilesAudit().scan_active_source(repository_root) == ()
