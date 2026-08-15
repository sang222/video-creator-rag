"""P2 media-business state, monitoring and deterministic decision gates."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.architecture_closeout import (
    AffiliateLinkRegistry,
    AffiliateOfferSnapshot,
    AppealEvidencePack,
    ChannelPnlSnapshot,
    MonetizationAccountStatus,
    PaymentProfileStatus,
    PlatformEnforcementIncident,
    RevenueSnapshot,
)
from app.db.models.ops import CostEvent, ManualAction
from app.db.models.workflow import VideoProject
from app.services.config_registry import content_hash


@dataclass(frozen=True, slots=True)
class SelfFundingDecision:
    result: str
    reason_codes: tuple[str, ...]
    trailing_finalized_revenue: Decimal
    trailing_cost: Decimal
    cash_received: Decimal
    open_critical_enforcement: int


class BusinessOperatingService:
    """Business truth is snapshot/incident based; no LLM controls transitions."""

    def __init__(self, session: Session):
        self.session = session

    def record_payment_status(
        self,
        *,
        company_id: uuid.UUID,
        payee_ref: str,
        tax_state: str,
        address_verification_state: str,
        payment_method_state: str,
        payment_hold_state: str,
        source_type: str,
        source_updated_at: datetime,
        evidence_ref: str,
    ) -> PaymentProfileStatus:
        payload = {
            "schema_version": "vcos.payment-profile-status.v1",
            "company_id": str(company_id),
            "payee_ref": payee_ref,
            "tax_state": tax_state,
            "address_verification_state": address_verification_state,
            "payment_method_state": payment_method_state,
            "payment_hold_state": payment_hold_state,
            "source_type": source_type,
            "source_updated_at": source_updated_at.isoformat(),
            "evidence_ref": evidence_ref,
        }
        row = PaymentProfileStatus(
            company_id=company_id,
            payee_ref=payee_ref,
            tax_state=tax_state,
            address_verification_state=address_verification_state,
            payment_method_state=payment_method_state,
            payment_hold_state=payment_hold_state,
            source_type=source_type,
            source_updated_at=source_updated_at,
            evidence_ref=evidence_ref,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def record_monetization_status(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        platform: str,
        destination_ref: str,
        program_type: str,
        eligibility_state: str,
        enrollment_state: str,
        restriction_state: str,
        country_eligibility_state: str,
        source_type: str,
        source_updated_at: datetime,
        evidence_ref: str,
    ) -> MonetizationAccountStatus:
        payload = {
            "schema_version": "vcos.monetization-account-status.v1",
            "company_id": str(company_id),
            "channel_workspace_id": str(channel_workspace_id),
            "platform": platform,
            "destination_ref": destination_ref,
            "program_type": program_type,
            "eligibility_state": eligibility_state,
            "enrollment_state": enrollment_state,
            "restriction_state": restriction_state,
            "country_eligibility_state": country_eligibility_state,
            "source_type": source_type,
            "source_updated_at": source_updated_at.isoformat(),
            "evidence_ref": evidence_ref,
        }
        row = MonetizationAccountStatus(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            platform=platform,
            destination_ref=destination_ref,
            program_type=program_type,
            eligibility_state=eligibility_state,
            enrollment_state=enrollment_state,
            restriction_state=restriction_state,
            country_eligibility_state=country_eligibility_state,
            source_type=source_type,
            source_updated_at=source_updated_at,
            evidence_ref=evidence_ref,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def record_revenue_snapshot(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        source: str,
        period_start: datetime,
        period_end: datetime,
        estimated_amount: Decimal,
        finalized_or_locked_amount: Decimal,
        reversed_amount: Decimal,
        cash_received_amount: Decimal,
        cash_receivable_amount: Decimal,
        source_updated_at: datetime,
        currency: str = "USD",
    ) -> RevenueSnapshot:
        values = (
            estimated_amount,
            finalized_or_locked_amount,
            reversed_amount,
            cash_received_amount,
            cash_receivable_amount,
        )
        if period_end <= period_start or any(value < 0 for value in values):
            raise ValidationFailureError("REVENUE_SNAPSHOT_VALUES_INVALID")
        payload = {
            "schema_version": "vcos.revenue-snapshot.v1",
            "company_id": str(company_id),
            "channel_workspace_id": str(channel_workspace_id),
            "source": source,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "currency": currency,
            "estimated_amount": str(estimated_amount),
            "finalized_or_locked_amount": str(finalized_or_locked_amount),
            "reversed_amount": str(reversed_amount),
            "cash_received_amount": str(cash_received_amount),
            "cash_receivable_amount": str(cash_receivable_amount),
            "source_updated_at": source_updated_at.isoformat(),
        }
        row = RevenueSnapshot(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            source=source,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            estimated_amount=estimated_amount,
            finalized_or_locked_amount=finalized_or_locked_amount,
            reversed_amount=reversed_amount,
            cash_received_amount=cash_received_amount,
            cash_receivable_amount=cash_receivable_amount,
            source_updated_at=source_updated_at,
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def build_channel_pnl(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        shared_cost_allocated: Decimal = Decimal("0"),
        currency: str = "USD",
    ) -> ChannelPnlSnapshot:
        if period_end <= period_start or shared_cost_allocated < 0:
            raise ValidationFailureError("CHANNEL_PNL_PERIOD_INVALID")
        project_ids = select(VideoProject.id).where(
            VideoProject.channel_workspace_id == channel_workspace_id
        )
        # Self-funding truth uses incurred/settled provider cost, never forecast
        # or reservation occupancy. Refund/adjustment accounting can be added as
        # separate signed business events; the legacy CostEvent amount is
        # non-negative, so only ACTUAL is safe to aggregate here.
        direct_cost = Decimal(
            str(
                self.session.scalar(
                    select(func.coalesce(func.sum(CostEvent.amount), 0)).where(
                        CostEvent.currency == currency,
                        CostEvent.cost_type == "ACTUAL",
                        CostEvent.created_at >= period_start,
                        CostEvent.created_at < period_end,
                        or_(
                            (CostEvent.cost_scope_type == "CHANNEL")
                            & (CostEvent.cost_scope_id == channel_workspace_id),
                            (CostEvent.cost_scope_type == "PROJECT")
                            & CostEvent.cost_scope_id.in_(project_ids),
                        ),
                    )
                )
                or 0
            )
        )
        snapshots = list(
            self.session.scalars(
                select(RevenueSnapshot).where(
                    RevenueSnapshot.channel_workspace_id == channel_workspace_id,
                    RevenueSnapshot.currency == currency,
                    RevenueSnapshot.period_start >= period_start,
                    RevenueSnapshot.period_end <= period_end,
                )
            ).all()
        )
        estimated = sum(
            (item.estimated_amount for item in snapshots),
            Decimal("0"),
        )
        finalized = sum(
            (
                max(
                    Decimal("0"),
                    item.finalized_or_locked_amount - item.reversed_amount,
                )
                for item in snapshots
            ),
            Decimal("0"),
        )
        cash = sum(
            (item.cash_received_amount for item in snapshots),
            Decimal("0"),
        )
        contribution = finalized - direct_cost - shared_cost_allocated
        payload = {
            "schema_version": "vcos.channel-pnl-snapshot.v1",
            "company_id": str(company_id),
            "channel_workspace_id": str(channel_workspace_id),
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "currency": currency,
            "direct_cost": str(direct_cost),
            "shared_cost_allocated": str(shared_cost_allocated),
            "estimated_revenue": str(estimated),
            "finalized_revenue": str(finalized),
            "cash_received": str(cash),
            "contribution_margin": str(contribution),
            "burn_rate": str(direct_cost + shared_cost_allocated),
            "revenue_snapshot_ids": [str(item.id) for item in snapshots],
        }
        row = ChannelPnlSnapshot(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            direct_cost=direct_cost,
            shared_cost_allocated=shared_cost_allocated,
            estimated_revenue=estimated,
            finalized_revenue=finalized,
            cash_received=cash,
            contribution_margin=contribution,
            burn_rate=direct_cost + shared_cost_allocated,
            source_refs=[
                {"type": "revenue_snapshot", "id": str(item.id)} for item in snapshots
            ],
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def self_funding_gate(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        freshness_days: int = 7,
    ) -> SelfFundingDecision:
        reasons: list[str] = []
        cutoff = utc_now() - timedelta(days=freshness_days)
        payment = self.session.scalar(
            select(PaymentProfileStatus)
            .where(PaymentProfileStatus.company_id == company_id)
            .order_by(PaymentProfileStatus.source_updated_at.desc())
            .limit(1)
        )
        monetization = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id
            )
            .order_by(MonetizationAccountStatus.source_updated_at.desc())
            .limit(1)
        )
        if payment is None or payment.source_updated_at < cutoff:
            reasons.append("PAYMENT_PROFILE_MISSING_OR_STALE")
        elif (
            payment.tax_state != "VERIFIED"
            or payment.address_verification_state != "VERIFIED"
            or payment.payment_method_state != "READY"
            or payment.payment_hold_state not in {"NONE", "CLEAR"}
        ):
            reasons.append("PAYMENT_PROFILE_NOT_READY")
        if monetization is None or monetization.source_updated_at < cutoff:
            reasons.append("MONETIZATION_STATUS_MISSING_OR_STALE")
        elif (
            monetization.enrollment_state != "ACTIVE"
            or monetization.restriction_state not in {"NONE", "CLEAR"}
            or monetization.country_eligibility_state not in {"ELIGIBLE", "READY"}
        ):
            reasons.append("MONETIZATION_NOT_READY")
        pnl = list(
            self.session.scalars(
                select(ChannelPnlSnapshot)
                .where(ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id)
                .order_by(ChannelPnlSnapshot.period_end.desc())
                .limit(2)
            ).all()
        )
        if len(pnl) < 2:
            reasons.append("SELF_FUNDING_TWO_REVIEW_CYCLES_REQUIRED")
        elif any(
            item.finalized_revenue < item.direct_cost + item.shared_cost_allocated
            for item in pnl
        ):
            reasons.append("FINALIZED_REVENUE_COST_COVERAGE_INSUFFICIENT")
        critical = int(
            self.session.scalar(
                select(func.count(PlatformEnforcementIncident.id)).where(
                    PlatformEnforcementIncident.channel_workspace_id
                    == channel_workspace_id,
                    PlatformEnforcementIncident.severity.in_(["HIGH", "CRITICAL"]),
                    PlatformEnforcementIncident.state.in_(
                        ["OPEN", "UNDER_REVIEW", "APPEAL_READY", "SUBMITTED"]
                    ),
                )
            )
            or 0
        )
        if critical:
            reasons.append("CRITICAL_ENFORCEMENT_OPEN")
        trailing_finalized = sum(
            (item.finalized_revenue for item in pnl),
            Decimal("0"),
        )
        trailing_cost = sum(
            (item.direct_cost + item.shared_cost_allocated for item in pnl),
            Decimal("0"),
        )
        cash = sum((item.cash_received for item in pnl), Decimal("0"))
        return SelfFundingDecision(
            result="SELF_FUNDING" if not reasons else "SUBSIDIZED_OR_RESTRICTED",
            reason_codes=tuple(reasons),
            trailing_finalized_revenue=trailing_finalized,
            trailing_cost=trailing_cost,
            cash_received=cash,
            open_critical_enforcement=critical,
        )

    def open_enforcement_incident(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        platform: str,
        incident_type: str,
        scope: str,
        severity: str,
        source_status: str,
        evidence_refs: list[dict[str, Any]],
        uploaded_video_id: uuid.UUID | None = None,
        deadline_at: datetime | None = None,
        freeze_learning: bool = True,
    ) -> PlatformEnforcementIncident:
        detected_at = utc_now()
        payload = {
            "schema_version": "vcos.platform-enforcement-incident.v1",
            "company_id": str(company_id),
            "channel_workspace_id": str(channel_workspace_id),
            "uploaded_video_id": (
                str(uploaded_video_id) if uploaded_video_id else None
            ),
            "platform": platform,
            "incident_type": incident_type,
            "scope": scope,
            "severity": severity,
            "source_status": source_status,
            "freeze_learning": freeze_learning,
            "detected_at": detected_at.isoformat(),
            "deadline_at": deadline_at.isoformat() if deadline_at else None,
            "evidence_refs": evidence_refs,
        }
        row = PlatformEnforcementIncident(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            uploaded_video_id=uploaded_video_id,
            platform=platform,
            incident_type=incident_type,
            scope=scope,
            severity=severity,
            state="OPEN",
            source_status=source_status,
            freeze_learning=freeze_learning,
            detected_at=detected_at,
            deadline_at=deadline_at,
            evidence_refs=list(evidence_refs),
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        self._queue_enforcement_action(row)
        return row

    def prepare_appeal_evidence(
        self,
        *,
        incident_id: uuid.UUID,
        rights_basis: str,
        evidence_items: list[dict[str, Any]],
        timeline: list[dict[str, Any]],
        internal_reviewer_ref: str | None = None,
    ) -> AppealEvidencePack:
        incident = self.session.get(PlatformEnforcementIncident, incident_id)
        if incident is None:
            raise NotFoundError("platform enforcement incident not found")
        if incident.state in {"RESOLVED", "DISMISSED"}:
            raise ValidationFailureError("APPEAL_EVIDENCE_INCIDENT_TERMINAL")
        payload = {
            "schema_version": "vcos.appeal-evidence-pack.v1",
            "incident_id": str(incident.id),
            "rights_basis": rights_basis,
            "evidence_items": evidence_items,
            "timeline": timeline,
            "internal_reviewer_ref": internal_reviewer_ref,
        }
        row = AppealEvidencePack(
            platform_enforcement_incident_id=incident.id,
            rights_basis=rights_basis,
            evidence_items=list(evidence_items),
            timeline=list(timeline),
            internal_reviewer_ref=internal_reviewer_ref,
            state="READY_FOR_HUMAN" if internal_reviewer_ref else "DRAFT",
            content_hash=content_hash(payload),
        )
        self.session.add(row)
        if row.state == "READY_FOR_HUMAN":
            incident.state = "APPEAL_READY"
        self.session.flush()
        return row

    def validate_affiliate_use(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        link_id: uuid.UUID,
        disclosure_present: bool,
    ) -> AffiliateLinkRegistry:
        link = self.session.get(AffiliateLinkRegistry, link_id)
        if link is None or link.channel_workspace_id != channel_workspace_id:
            raise NotFoundError("affiliate link not found")
        offer = self.session.get(
            AffiliateOfferSnapshot,
            link.affiliate_offer_snapshot_id,
        )
        if offer is None:
            raise ValidationFailureError("AFFILIATE_OFFER_SNAPSHOT_REQUIRED")
        now = utc_now()
        if (
            link.active_state != "ACTIVE"
            or offer.state != "ACTIVE"
            or (offer.expires_at is not None and offer.expires_at <= now)
        ):
            raise ValidationFailureError("AFFILIATE_OFFER_OR_LINK_NOT_ACTIVE")
        if (
            link.disclosure_required or offer.disclosure_required
        ) and not disclosure_present:
            raise ValidationFailureError("AFFILIATE_DISCLOSURE_REQUIRED")
        return link

    def _queue_enforcement_action(
        self,
        incident: PlatformEnforcementIncident,
    ) -> None:
        self.session.add(
            ManualAction(
                action_type="PLATFORM_ENFORCEMENT_REVIEW",
                target_type="platform_enforcement_incident",
                target_id=incident.id,
                priority=("CRITICAL" if incident.severity == "CRITICAL" else "HIGH"),
                state="OPEN",
                reason_code=incident.incident_type,
                next_action=(
                    "Review platform evidence and prepare a human appeal/dispute "
                    "only when legally justified."
                ),
                due_at=incident.deadline_at,
            )
        )

    def action_first_snapshot(
        self,
        channel_workspace_id: uuid.UUID,
    ) -> dict[str, Any]:
        gate = self.self_funding_gate(
            company_id=self._company_id(channel_workspace_id),
            channel_workspace_id=channel_workspace_id,
        )
        latest_pnl = self.session.scalar(
            select(ChannelPnlSnapshot)
            .where(ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id)
            .order_by(ChannelPnlSnapshot.period_end.desc())
            .limit(1)
        )
        open_incidents = int(
            self.session.scalar(
                select(func.count(PlatformEnforcementIncident.id)).where(
                    PlatformEnforcementIncident.channel_workspace_id
                    == channel_workspace_id,
                    PlatformEnforcementIncident.state.in_(
                        ["OPEN", "UNDER_REVIEW", "APPEAL_READY", "SUBMITTED"]
                    ),
                )
            )
            or 0
        )
        latest_payload = None
        if latest_pnl is not None:
            latest_payload = {
                "period_end": latest_pnl.period_end,
                "finalized_revenue": str(latest_pnl.finalized_revenue),
                "cash_received": str(latest_pnl.cash_received),
                "direct_cost": str(latest_pnl.direct_cost),
                "shared_cost_allocated": str(latest_pnl.shared_cost_allocated),
                "contribution_margin": str(latest_pnl.contribution_margin),
            }
        return {
            "channel_workspace_id": str(channel_workspace_id),
            "self_funding": gate.result,
            "self_funding_reasons": list(gate.reason_codes),
            "open_enforcement_incidents": open_incidents,
            "latest_pnl": latest_payload,
            "next_action": (
                "NO_ACTION_REQUIRED"
                if gate.result == "SELF_FUNDING" and open_incidents == 0
                else "OPEN_BUSINESS_ACTION_QUEUE"
            ),
        }

    def _company_id(self, channel_workspace_id: uuid.UUID) -> uuid.UUID:
        value = self.session.scalar(
            select(VideoProject.company_id)
            .where(VideoProject.channel_workspace_id == channel_workspace_id)
            .limit(1)
        )
        if value is None:
            from app.db.models.channel import ChannelWorkspace

            value = self.session.scalar(
                select(ChannelWorkspace.company_id).where(
                    ChannelWorkspace.id == channel_workspace_id
                )
            )
        if value is None:
            raise NotFoundError("channel workspace not found")
        return value
