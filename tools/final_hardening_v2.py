from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app/services/remaining_debt_closeout.py"
MODEL = ROOT / "app/db/models/remaining_debt.py"
MIGRATION = ROOT / "alembic/versions/0087_business_os.py"
TESTS = ROOT / "tests/test_remaining_debt_closeout.py"


def replace_method(text: str, class_name: str, method_name: str, body: str) -> str:
    class_start = text.find(f"class {class_name}")
    if class_start < 0:
        raise RuntimeError(f"class not found: {class_name}")
    start = text.find(f"    def {method_name}(", class_start)
    if start < 0:
        raise RuntimeError(f"method not found: {class_name}.{method_name}")
    next_method = text.find("\n    def ", start + 5)
    next_class = text.find("\nclass ", start + 5)
    candidates = [value for value in (next_method, next_class) if value >= 0]
    end = min(candidates) if candidates else len(text)
    return text[:start] + body.rstrip() + "\n\n" + text[end + 1 :]


def insert_before_method(
    text: str, class_name: str, method_name: str, body: str, marker: str
) -> str:
    if marker in text:
        return text
    class_start = text.find(f"class {class_name}")
    start = text.find(f"    def {method_name}(", class_start)
    if start < 0:
        raise RuntimeError(f"insert anchor not found: {class_name}.{method_name}")
    return text[:start] + body.rstrip() + "\n\n" + text[start:]


text = SERVICE.read_text(encoding="utf-8")

text = replace_method(
    text,
    "SeriesAuthorityService",
    "activate_arc",
    '''    def activate_arc(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        existing_command = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing_command is not None:
            if existing_command.series_arc_version_id != arc.id:
                raise ConflictError("SERIES_COMMAND_REUSE_CONFLICT")
            return arc
        _require(arc.state == "DRAFT", "SERIES_ARC_NOT_ACTIVATABLE")
        blueprints = self._blueprints(arc.id)
        if arc.arc_mode == "FIXED_COUNT":
            expected = int(arc.planned_episode_count or 0)
            positions = sorted(
                item.planned_position
                for item in blueprints
                if item.planned_position is not None
            )
            _require(
                len(blueprints) == expected and positions == list(range(1, expected + 1)),
                "SERIES_FIXED_ARC_COVERAGE_INCOMPLETE",
            )
        prior = list(
            self.session.scalars(
                select(SeriesArcVersion)
                .where(
                    SeriesArcVersion.series_plan_id == arc.series_plan_id,
                    SeriesArcVersion.state.in_({"ACTIVE", "COMPLETION_PENDING"}),
                    SeriesArcVersion.id != arc.id,
                )
                .with_for_update()
            ).all()
        )
        for item in prior:
            item.state = "SUPERSEDED"
        # Clear the partial-unique current-arc slot before activating another row.
        self.session.flush()
        arc.state = "ACTIVE"
        arc.approved_by = actor_id
        arc.approved_at = utc_now()
        self._decision(
            arc=arc,
            decision_type="ACTIVATE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=None,
            resulting_count=arc.planned_episode_count,
        )
        self.session.flush()
        return arc''',
)

text = replace_method(
    text,
    "SeriesAuthorityService",
    "request_early_completion",
    '''    def request_early_completion(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        existing = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing is not None:
            if existing.series_arc_version_id != arc.id or existing.decision_type != "EARLY_COMPLETE":
                raise ConflictError("SERIES_COMMAND_REUSE_CONFLICT")
            return arc
        _require(arc.state == "ACTIVE", "SERIES_EARLY_COMPLETION_NOT_ALLOWED")
        progress = self.progress(series_plan_id=arc.series_plan_id)
        _require(progress.published_count > 0, "SERIES_EARLY_COMPLETION_NO_PUBLIC_EPISODE")
        self._decision(
            arc=arc,
            decision_type="EARLY_COMPLETE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=arc.planned_episode_count,
            resulting_count=progress.published_count,
        )
        arc.state = "COMPLETION_PENDING"
        self.session.flush()
        return arc''',
)

text = replace_method(
    text,
    "SeriesAuthorityService",
    "complete_series",
    '''    def complete_series(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        existing = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing is not None:
            if existing.series_arc_version_id != arc.id or existing.decision_type != "COMPLETE":
                raise ConflictError("SERIES_COMMAND_REUSE_CONFLICT")
            return arc
        _require(arc.state == "COMPLETION_PENDING", "SERIES_COMPLETION_NOT_PENDING")
        self._decision(
            arc=arc,
            decision_type="COMPLETE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=arc.planned_episode_count,
            resulting_count=arc.planned_episode_count,
        )
        arc.state = "COMPLETED"
        self.session.flush()
        return arc''',
)

text = replace_method(
    text,
    "LearningAuthorityService",
    "create_audience_delivery_plan",
    '''    def create_audience_delivery_plan(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        video_project_id: uuid.UUID,
        publication_receipt_id: uuid.UUID,
        target_markets: Sequence[str],
        target_languages: Sequence[str],
        packaging_refs: Sequence[str],
        playlist_refs: Sequence[str],
    ) -> AudienceDeliveryPlan:
        markets = sorted({str(item).strip().upper() for item in target_markets if str(item).strip()})
        languages = sorted({str(item).strip() for item in target_languages if str(item).strip()})
        _require(bool(markets) and "UNKNOWN" not in markets, "AUDIENCE_DELIVERY_TARGET_MARKET_REQUIRED")
        _require(bool(languages) and "UNKNOWN" not in {item.upper() for item in languages}, "AUDIENCE_DELIVERY_LANGUAGE_REQUIRED")
        payload = {
            "schema_version": "vcos.audience-delivery-plan.v1",
            "publication_receipt_id": publication_receipt_id,
            "target_markets": markets,
            "target_languages": languages,
            "packaging_refs": sorted({str(item) for item in packaging_refs}),
            "playlist_refs": sorted({str(item) for item in playlist_refs}),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(AudienceDeliveryPlan).where(
                AudienceDeliveryPlan.publication_receipt_id == publication_receipt_id
            )
        )
        if existing is not None:
            if existing.plan_hash != digest:
                raise ConflictError("AUDIENCE_DELIVERY_PLAN_IMMUTABLE_CONFLICT")
            return existing
        row = AudienceDeliveryPlan(
            id=_deterministic_id(_LEARNING_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            publication_receipt_id=publication_receipt_id,
            target_markets=markets,
            target_languages=languages,
            packaging_refs=payload["packaging_refs"],
            playlist_refs=payload["playlist_refs"],
            state="ELIGIBLE",
            plan_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row''',
)

text = replace_method(
    text,
    "BusinessMonitoringService",
    "build_channel_pnl",
    '''    def build_channel_pnl(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        currency: str,
        direct_cost: Decimal | str | float | int,
        allocated_ops_cost: Decimal | str | float | int,
        calculation_version: str = "vcos.channel-pnl.v2",
    ) -> ChannelPnlSnapshot:
        rows = list(
            self.session.scalars(
                select(RevenueSnapshot).where(
                    RevenueSnapshot.channel_workspace_id == channel_workspace_id,
                    RevenueSnapshot.period_start >= period_start,
                    RevenueSnapshot.period_end <= period_end,
                    RevenueSnapshot.currency == currency.upper(),
                )
            ).all()
        )
        # Revenue states are lifecycle snapshots. Count only the newest state of
        # each stable source_ref; otherwise ESTIMATED→FINALIZED→PAID is counted
        # three times.
        current: dict[tuple[str, str, datetime, datetime], RevenueSnapshot] = {}
        for row in rows:
            key = (row.source, row.source_ref, row.period_start, row.period_end)
            previous = current.get(key)
            if previous is None or (row.source_updated_at, row.created_at) > (
                previous.source_updated_at,
                previous.created_at,
            ):
                current[key] = row
        selected = list(current.values())
        buckets = {key: Decimal("0") for key in REVENUE_STATES}
        for row in selected:
            buckets[row.amount_state] += Decimal(row.amount)
        direct = _decimal(direct_cost)
        ops = _decimal(allocated_ops_cost)
        contribution = (
            buckets["LOCKED"]
            + buckets["FINALIZED"]
            + buckets["PAID"]
            - buckets["REVERSED"]
            - direct
            - ops
        )
        payload = {
            "schema_version": calculation_version,
            "channel_workspace_id": channel_workspace_id,
            "period_start": period_start,
            "period_end": period_end,
            "currency": currency.upper(),
            "buckets": buckets,
            "direct_cost": direct,
            "allocated_ops_cost": ops,
            "contribution_margin": contribution,
            "source_snapshot_refs": [str(row.id) for row in selected],
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(ChannelPnlSnapshot).where(
                ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
                ChannelPnlSnapshot.period_start == period_start,
                ChannelPnlSnapshot.period_end == period_end,
                ChannelPnlSnapshot.calculation_version == calculation_version,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("CHANNEL_PNL_IMMUTABLE_CONFLICT")
            return existing
        row = ChannelPnlSnapshot(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            period_start=period_start,
            period_end=period_end,
            currency=currency.upper(),
            estimated_revenue=buckets["ESTIMATED"],
            pending_revenue=buckets["PENDING"],
            locked_revenue=buckets["LOCKED"],
            finalized_revenue=buckets["FINALIZED"],
            cash_received=buckets["PAID"],
            reversed_revenue=buckets["REVERSED"],
            direct_cost=direct,
            allocated_ops_cost=ops,
            contribution_margin=contribution,
            calculation_version=calculation_version,
            source_snapshot_refs=[str(item.id) for item in selected],
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row''',
)

text = replace_method(
    text,
    "BusinessMonitoringService",
    "evaluate_self_funding",
    '''    def evaluate_self_funding(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        assessment_window_end: datetime,
        policy_version: str = "vcos.self-funding-gate.v2",
    ) -> SelfFundingAssessment:
        monetization = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id
            )
            .order_by(MonetizationAccountStatus.version_number.desc())
        )
        payment = self.session.scalar(
            select(PaymentProfileStatus)
            .where(PaymentProfileStatus.company_id == company_id)
            .order_by(PaymentProfileStatus.version_number.desc())
        )
        pnl = list(
            self.session.scalars(
                select(ChannelPnlSnapshot)
                .where(
                    ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
                    ChannelPnlSnapshot.period_end <= assessment_window_end,
                )
                .order_by(ChannelPnlSnapshot.period_end.desc())
                .limit(2)
            ).all()
        )
        open_critical = self.session.scalar(
            select(PlatformEnforcementIncident.id).where(
                PlatformEnforcementIncident.channel_workspace_id == channel_workspace_id,
                PlatformEnforcementIncident.state == "OPEN",
                PlatformEnforcementIncident.severity.in_({"HIGH", "CRITICAL"}),
            )
        )
        trusted_confidence = {"HIGH", "STABLE", "ACTION_READY"}
        reasons: list[str] = []
        if (
            monetization is None
            or monetization.enrollment_state != "ACTIVE"
            or monetization.restriction_state not in {"NONE", "CLEAR"}
        ):
            reasons.append("MONETIZATION_NOT_ACTIVE")
        elif (
            monetization.confidence_state not in trusted_confidence
            or (monetization.valid_until is not None and monetization.valid_until < assessment_window_end)
            or monetization.source_updated_at > assessment_window_end
        ):
            reasons.append("MONETIZATION_STATE_STALE_OR_UNTRUSTED")
        if (
            payment is None
            or payment.tax_state != "VERIFIED"
            or payment.address_verification_state != "VERIFIED"
            or payment.payment_method_state != "READY"
            or payment.payment_hold_state not in {"NONE", "CLEAR"}
        ):
            reasons.append("PAYMENT_PROFILE_NOT_READY")
        elif (
            payment.confidence_state not in trusted_confidence
            or (payment.valid_until is not None and payment.valid_until < assessment_window_end)
            or payment.source_updated_at > assessment_window_end
        ):
            reasons.append("PAYMENT_STATE_STALE_OR_UNTRUSTED")
        if len(pnl) < 2:
            reasons.append("TWO_REVIEW_CYCLES_REQUIRED")
        else:
            for snapshot in pnl:
                trusted = (
                    Decimal(snapshot.locked_revenue)
                    + Decimal(snapshot.finalized_revenue)
                    + Decimal(snapshot.cash_received)
                    - Decimal(snapshot.reversed_revenue)
                )
                cost = Decimal(snapshot.direct_cost) + Decimal(snapshot.allocated_ops_cost)
                if trusted < cost:
                    reasons.append("TRUSTED_REVENUE_BELOW_COST")
                    break
                refs = [uuid.UUID(str(item)) for item in snapshot.source_snapshot_refs]
                source_rows = list(
                    self.session.scalars(
                        select(RevenueSnapshot).where(RevenueSnapshot.id.in_(refs))
                    ).all()
                ) if refs else []
                if len(source_rows) != len(refs) or any(
                    item.confidence_state not in trusted_confidence
                    for item in source_rows
                    if item.amount_state in {"LOCKED", "FINALIZED", "PAID"}
                ):
                    reasons.append("REVENUE_SOURCE_STALE_OR_UNTRUSTED")
                    break
        if open_critical is not None:
            reasons.append("CRITICAL_ENFORCEMENT_OPEN")
        decision = "SELF_FUNDING" if not reasons else "FUNDED_EXPERIMENT"
        inputs = [str(item.id) for item in [monetization, payment, *pnl] if item is not None]
        payload = {
            "schema_version": policy_version,
            "channel_workspace_id": channel_workspace_id,
            "assessment_window_end": assessment_window_end,
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
            "input_refs": inputs,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(SelfFundingAssessment).where(
                SelfFundingAssessment.channel_workspace_id == channel_workspace_id,
                SelfFundingAssessment.assessment_window_end == assessment_window_end,
                SelfFundingAssessment.policy_version == policy_version,
            )
        )
        if existing is not None:
            if existing.assessment_hash != digest:
                raise ConflictError("SELF_FUNDING_ASSESSMENT_IMMUTABLE_CONFLICT")
            return existing
        row = SelfFundingAssessment(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            assessment_window_end=assessment_window_end,
            policy_version=policy_version,
            decision=decision,
            reason_codes=sorted(set(reasons)),
            input_refs=inputs,
            assessment_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row''',
)

text = replace_method(
    text,
    "BusinessMonitoringService",
    "create_appeal_pack",
    '''    def create_appeal_pack(
        self,
        *,
        incident_id: uuid.UUID,
        rights_basis: str,
        evidence_items: Sequence[Mapping[str, Any]],
        timeline: Sequence[Mapping[str, Any]],
    ) -> AppealEvidencePack:
        incident = self.session.get(PlatformEnforcementIncident, incident_id)
        if incident is None:
            raise NotFoundError(f"enforcement incident not found: {incident_id}")
        version = self.session.scalar(
            select(func.max(AppealEvidencePack.version_number)).where(
                AppealEvidencePack.platform_enforcement_incident_id == incident_id
            )
        )
        version_number = int(version or 0) + 1
        payload = {
            "schema_version": "vcos.appeal-evidence-pack.v1",
            "incident_id": incident_id,
            "incident_hash": incident.incident_hash,
            "version_number": version_number,
            "rights_basis": rights_basis.strip(),
            "evidence_items": list(evidence_items),
            "timeline": list(timeline),
        }
        _require(bool(payload["rights_basis"]), "APPEAL_RIGHTS_BASIS_REQUIRED")
        _require(bool(evidence_items), "APPEAL_EVIDENCE_REQUIRED")
        row = AppealEvidencePack(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=incident.company_id,
            channel_workspace_id=incident.channel_workspace_id,
            platform_enforcement_incident_id=incident.id,
            version_number=version_number,
            rights_basis=payload["rights_basis"],
            evidence_items=_jsonable(evidence_items),
            timeline=_jsonable(timeline),
            state="READY_FOR_HUMAN",
            approved_by=None,
            approved_at=None,
            pack_hash=_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row''',
)

approval_method = '''    def approve_appeal_pack(
        self, *, pack_id: uuid.UUID, actor_id: uuid.UUID
    ) -> AppealEvidencePack:
        row = self.session.scalar(
            select(AppealEvidencePack)
            .where(AppealEvidencePack.id == pack_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(f"appeal evidence pack not found: {pack_id}")
        if row.state == "HUMAN_APPROVED":
            if row.approved_by != actor_id:
                raise ConflictError("APPEAL_APPROVAL_IMMUTABLE")
            return row
        _require(row.state == "READY_FOR_HUMAN", "APPEAL_PACK_NOT_APPROVABLE")
        row.state = "HUMAN_APPROVED"
        row.approved_by = actor_id
        row.approved_at = utc_now()
        self.session.flush()
        return row'''
text = insert_before_method(
    text,
    "BusinessMonitoringService",
    "register_affiliate_offer",
    approval_method,
    "def approve_appeal_pack(",
)

text = replace_method(
    text,
    "BusinessMonitoringService",
    "assess_disclosures",
    '''    def assess_disclosures(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        video_project_id: uuid.UUID,
        publish_package_ref: str,
        policy_version: str,
        required_disclosures: Sequence[str],
        observed_disclosures: Sequence[str],
        link_registry_refs: Sequence[uuid.UUID],
    ) -> BusinessDisclosureAssessment:
        required = sorted({str(item).strip().upper() for item in required_disclosures if str(item).strip()})
        observed = sorted({str(item).strip().upper() for item in observed_disclosures if str(item).strip()})
        missing = sorted(set(required) - set(observed))
        unique_refs = sorted(set(link_registry_refs), key=str)
        link_rows = list(
            self.session.scalars(
                select(AffiliateLinkRegistry).where(AffiliateLinkRegistry.id.in_(unique_refs))
            ).all()
        ) if unique_refs else []
        reasons = [f"DISCLOSURE_MISSING:{item}" for item in missing]
        if len(link_rows) != len(unique_refs):
            reasons.append("AFFILIATE_LINK_AUTHORITY_MISSING")
        now = utc_now()
        affiliate_required = False
        for link in link_rows:
            if link.company_id != company_id or link.channel_workspace_id != channel_workspace_id:
                reasons.append("AFFILIATE_LINK_SCOPE_MISMATCH")
                continue
            offer = self.session.get(AffiliateOfferSnapshot, link.affiliate_offer_snapshot_id)
            affiliate_required = affiliate_required or link.disclosure_required
            if link.state != "ACTIVE" or link.last_health_state != "HEALTHY":
                reasons.append("AFFILIATE_LINK_NOT_HEALTHY")
            if offer is None or offer.state != "ACTIVE" or (
                offer.expires_at is not None and offer.expires_at <= now
            ):
                reasons.append("AFFILIATE_OFFER_NOT_ACTIVE")
        if affiliate_required and "AFFILIATE" not in required:
            reasons.append("AFFILIATE_DISCLOSURE_POLICY_MISSING")
        if affiliate_required and "AFFILIATE" not in observed:
            reasons.append("AFFILIATE_DISCLOSURE_MISSING")
        decision = "PASS" if not reasons else "BLOCK"
        payload = {
            "schema_version": "vcos.business-disclosure-assessment.v2",
            "publish_package_ref": publish_package_ref,
            "policy_version": policy_version,
            "required_disclosures": required,
            "observed_disclosures": observed,
            "link_registry_refs": [str(item) for item in unique_refs],
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(BusinessDisclosureAssessment).where(
                BusinessDisclosureAssessment.publish_package_ref == publish_package_ref,
                BusinessDisclosureAssessment.policy_version == policy_version,
            )
        )
        if existing is not None:
            if existing.assessment_hash != digest:
                raise ConflictError("BUSINESS_DISCLOSURE_ASSESSMENT_IMMUTABLE")
            return existing
        row = BusinessDisclosureAssessment(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            publish_package_ref=publish_package_ref,
            policy_version=policy_version,
            required_disclosures=required,
            observed_disclosures=observed,
            link_registry_refs=payload["link_registry_refs"],
            decision=decision,
            reason_codes=payload["reason_codes"],
            assessment_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row''',
)

monitor_method = '''    def refresh_action_queue(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        observed_at: datetime,
    ) -> tuple[BusinessActionItem, ...]:
        actions: list[BusinessActionItem] = []
        payment = self.session.scalar(
            select(PaymentProfileStatus)
            .where(PaymentProfileStatus.company_id == company_id)
            .order_by(PaymentProfileStatus.version_number.desc())
        )
        monetization = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(MonetizationAccountStatus.channel_workspace_id == channel_workspace_id)
            .order_by(MonetizationAccountStatus.version_number.desc())
        )
        if payment is None or (
            payment.valid_until is not None and payment.valid_until < observed_at
        ):
            actions.append(
                self._action(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    action_type="VERIFY_PAYMENT_PROFILE",
                    target_ref=f"company://{company_id}/payment-profile",
                    priority="HIGH",
                    reason_code="PAYMENT_PROFILE_MISSING_OR_STALE",
                    evidence_refs=[] if payment is None else [str(payment.id)],
                    due_at=observed_at,
                )
            )
        if monetization is None or (
            monetization.valid_until is not None and monetization.valid_until < observed_at
        ):
            actions.append(
                self._action(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    action_type="VERIFY_MONETIZATION_STATUS",
                    target_ref=f"channel://{channel_workspace_id}/monetization",
                    priority="HIGH",
                    reason_code="MONETIZATION_STATUS_MISSING_OR_STALE",
                    evidence_refs=[] if monetization is None else [str(monetization.id)],
                    due_at=observed_at,
                )
            )
        offers = list(
            self.session.scalars(
                select(AffiliateOfferSnapshot).where(
                    AffiliateOfferSnapshot.channel_workspace_id == channel_workspace_id,
                    AffiliateOfferSnapshot.state == "ACTIVE",
                )
            ).all()
        )
        for offer in offers:
            if offer.expires_at is not None and offer.expires_at <= observed_at:
                offer.state = "EXPIRED"
                reason = "AFFILIATE_OFFER_EXPIRED"
                priority = "HIGH"
            elif offer.expires_at is not None and offer.expires_at <= observed_at + timedelta(days=7):
                reason = "AFFILIATE_OFFER_EXPIRING"
                priority = "MEDIUM"
            else:
                continue
            actions.append(
                self._action(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    action_type="REVIEW_AFFILIATE_OFFER",
                    target_ref=f"affiliate-offer://{offer.id}",
                    priority=priority,
                    reason_code=reason,
                    evidence_refs=[str(offer.id)],
                    due_at=offer.expires_at,
                )
            )
        links = list(
            self.session.scalars(
                select(AffiliateLinkRegistry).where(
                    AffiliateLinkRegistry.channel_workspace_id == channel_workspace_id,
                    AffiliateLinkRegistry.state == "ACTIVE",
                )
            ).all()
        )
        for link in links:
            if link.last_health_state != "HEALTHY" or link.last_checked_at is None or (
                link.last_checked_at < observed_at - timedelta(hours=24)
            ):
                actions.append(
                    self._action(
                        company_id=company_id,
                        channel_workspace_id=channel_workspace_id,
                        action_type="VERIFY_AFFILIATE_LINK",
                        target_ref=f"affiliate-link://{link.id}",
                        priority="HIGH" if link.last_health_state == "BROKEN" else "MEDIUM",
                        reason_code="AFFILIATE_LINK_UNHEALTHY_OR_STALE",
                        evidence_refs=[str(link.id)],
                        due_at=observed_at,
                    )
                )
        deadlines = list(
            self.session.scalars(
                select(PlatformEnforcementIncident).where(
                    PlatformEnforcementIncident.channel_workspace_id == channel_workspace_id,
                    PlatformEnforcementIncident.state == "OPEN",
                    PlatformEnforcementIncident.deadline_at.is_not(None),
                    PlatformEnforcementIncident.deadline_at <= observed_at + timedelta(days=3),
                )
            ).all()
        )
        for incident in deadlines:
            actions.append(
                self._action(
                    company_id=company_id,
                    channel_workspace_id=channel_workspace_id,
                    action_type="REVIEW_ENFORCEMENT_DEADLINE",
                    target_ref=f"platform-enforcement://{incident.id}",
                    priority="CRITICAL",
                    reason_code="ENFORCEMENT_DEADLINE_APPROACHING",
                    evidence_refs=[incident.source_ref],
                    due_at=incident.deadline_at,
                )
            )
        self.session.flush()
        return tuple(actions)'''
text = insert_before_method(
    text,
    "BusinessMonitoringService",
    "dashboard",
    monitor_method,
    "def refresh_action_queue(",
)

text = replace_method(
    text,
    "BusinessMonitoringService",
    "_action",
    '''    def _action(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        action_type: str,
        target_ref: str,
        priority: str,
        reason_code: str,
        evidence_refs: Sequence[str],
        due_at: datetime | None,
    ) -> BusinessActionItem:
        payload = {
            "schema_version": "vcos.business-action-item.v2",
            "channel_workspace_id": channel_workspace_id,
            "action_type": action_type,
            "target_ref": target_ref,
            "priority": priority,
            "reason_code": reason_code,
            "evidence_refs": sorted(set(evidence_refs)),
            "due_at": due_at,
        }
        existing = self.session.scalar(
            select(BusinessActionItem).where(
                BusinessActionItem.channel_workspace_id == channel_workspace_id,
                BusinessActionItem.action_type == action_type,
                BusinessActionItem.target_ref == target_ref,
                BusinessActionItem.reason_code == reason_code,
            )
        )
        if existing is not None:
            existing.state = "OPEN"
            existing.priority = priority
            existing.due_at = due_at
            existing.evidence_refs = payload["evidence_refs"]
            existing.action_hash = _hash(payload)
            return existing
        row = BusinessActionItem(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            action_type=action_type,
            target_ref=target_ref,
            priority=priority,
            reason_code=reason_code,
            state="OPEN",
            due_at=due_at,
            evidence_refs=payload["evidence_refs"],
            action_hash=_hash(payload),
        )
        self.session.add(row)
        return row''',
)

text = replace_method(
    text,
    "RemainingDebtCloseoutCoordinator",
    "on_publication_verified",
    '''    def on_publication_verified(
        self,
        *,
        candidate: Any,
        public_receipt: Any,
        observed_at: datetime,
    ) -> dict[str, str | None]:
        learning = LearningAuthorityService(self.session)
        market = dict(getattr(candidate, "target_market_lineage", {}) or {})
        source_ref = f"public-publication-receipt://{public_receipt.id}"
        profile_hash = self._policy_snapshot_hash(candidate)
        target_market = str(
            market.get("primary_market") or market.get("target_market") or "UNKNOWN"
        ).strip()
        content_language = str(
            market.get("content_language") or market.get("locale") or "UNKNOWN"
        ).strip()
        fingerprint_id: str | None = None
        delivery_id: str | None = None
        if profile_hash is None:
            learning.open_operational_incident(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                video_project_id=candidate.video_project_id,
                incident_type="LIVE_PROOF",
                external_ref=f"policy-snapshot:{candidate.id}",
                severity="HIGH",
                evidence_payload={"reason": "EXACT_POLICY_SNAPSHOT_HASH_UNAVAILABLE"},
                blocks_learning=True,
            )
        else:
            fingerprint = learning.create_fingerprint(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                source_entity_ref=source_ref,
                content_product_type=str(
                    getattr(candidate, "content_product_type", None)
                    or getattr(candidate, "content_mode", "EDITORIAL_NARRATED_VIDEO")
                ),
                series_plan_id=getattr(candidate, "series_plan_id", None),
                profile_snapshot_hash=profile_hash,
                target_market=target_market,
                content_language=content_language,
                format_key=str(getattr(candidate, "content_mode", "STANDALONE")),
                normalized_features={
                    "production_lane": getattr(candidate, "production_lane", None),
                    "content_mode": getattr(candidate, "content_mode", None),
                    "series_plan_id": getattr(candidate, "series_plan_id", None),
                    "target_surface": getattr(candidate, "target_surface", None),
                },
            )
            fingerprint_id = str(fingerprint.id)
        if target_market.upper() == "UNKNOWN" or content_language.upper() == "UNKNOWN":
            learning.open_operational_incident(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                video_project_id=candidate.video_project_id,
                incident_type="LIVE_PROOF",
                external_ref=f"audience-delivery:{candidate.id}",
                severity="HIGH",
                evidence_payload={"reason": "TARGET_MARKET_OR_LANGUAGE_UNAVAILABLE"},
                blocks_learning=False,
            )
        else:
            delivery = learning.create_audience_delivery_plan(
                company_id=candidate.company_id,
                channel_workspace_id=candidate.channel_workspace_id,
                video_project_id=candidate.video_project_id,
                publication_receipt_id=public_receipt.id,
                target_markets=[target_market],
                target_languages=[content_language],
                packaging_refs=[
                    f"production-package://{getattr(candidate, 'production_package_hash', '')}"
                ],
                playlist_refs=[],
            )
            delivery_id = str(delivery.id)
        ordinal_id: str | None = None
        series_plan_id = getattr(candidate, "series_plan_id", None)
        if series_plan_id is not None:
            active_arc = self.session.scalar(
                select(SeriesArcVersion).where(
                    SeriesArcVersion.series_plan_id == series_plan_id,
                    SeriesArcVersion.state.in_({"ACTIVE", "COMPLETION_PENDING"}),
                )
            )
            blueprint = self.session.scalar(
                select(SeriesEpisodeBlueprint).where(
                    SeriesEpisodeBlueprint.series_plan_id == series_plan_id,
                    SeriesEpisodeBlueprint.video_project_id == candidate.video_project_id,
                )
            )
            if active_arc is not None and blueprint is not None:
                try:
                    ordinal = SeriesAuthorityService(self.session).record_publication(
                        series_plan_id=series_plan_id,
                        publication_receipt_id=public_receipt.id,
                        video_project_id=candidate.video_project_id,
                        published_at=observed_at,
                        technical_attempt_ref=str(
                            getattr(candidate, "workflow_run_id", "") or ""
                        ),
                        blueprint_id=blueprint.id,
                    )
                    ordinal_id = str(ordinal.id)
                except (ConflictError, ValidationFailureError) as exc:
                    learning.open_operational_incident(
                        company_id=candidate.company_id,
                        channel_workspace_id=candidate.channel_workspace_id,
                        video_project_id=candidate.video_project_id,
                        incident_type="LIVE_PROOF",
                        external_ref=f"series-publication:{public_receipt.id}",
                        severity="HIGH",
                        evidence_payload={"reason": str(exc)},
                        blocks_learning=False,
                    )
        self.session.flush()
        return {
            "learning_fingerprint_id": fingerprint_id,
            "audience_delivery_plan_id": delivery_id,
            "series_public_ordinal_id": ordinal_id,
        }''',
)

policy_helper = '''    def _policy_snapshot_hash(self, candidate: Any) -> str | None:
        direct = getattr(candidate, "policy_snapshot_hash", None)
        if isinstance(direct, str) and re.fullmatch(r"[0-9a-f]{64}", direct):
            return direct
        lineage = dict(getattr(candidate, "target_market_lineage", {}) or {})
        for key in ("policy_snapshot_hash", "compiled_policy_snapshot_hash"):
            value = lineage.get(key)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                return value
        policy_id = getattr(candidate, "policy_snapshot_id", None)
        if policy_id is None:
            return None
        try:
            from app.db.models.channel import CompiledChannelPolicySnapshot

            snapshot = self.session.get(CompiledChannelPolicySnapshot, policy_id)
        except Exception:
            return None
        if snapshot is None:
            return None
        for attribute in (
            "compiled_policy_hash",
            "policy_hash",
            "snapshot_hash",
            "content_hash",
            "compiled_hash",
        ):
            value = getattr(snapshot, attribute, None)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                return value
        return None'''
text = insert_before_method(
    text,
    "RemainingDebtCloseoutCoordinator",
    "on_publication_verified",
    policy_helper,
    "def _policy_snapshot_hash(",
)

# The monitor uses explicit time windows.
if "from datetime import datetime, timedelta" not in text:
    text = text.replace(
        "from datetime import datetime\n", "from datetime import datetime, timedelta\n", 1
    )
SERVICE.write_text(text, encoding="utf-8")

model = MODEL.read_text(encoding="utf-8")
model = model.replace(
    '            "amount_state",\n            "source_ref",\n            name="uq_revenue_snapshot_source",',
    '            "amount_state",\n            "source_ref",\n            "source_updated_at",\n            name="uq_revenue_snapshot_source",',
)
if "pending_revenue:" not in model:
    model = model.replace(
        '''    estimated_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    locked_revenue:''',
        '''    estimated_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    pending_revenue: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), nullable=False, default=Decimal("0")
    )
    locked_revenue:''',
    )
MODEL.write_text(model, encoding="utf-8")

migration = MIGRATION.read_text(encoding="utf-8")
migration = migration.replace(
    '''            "amount_state",
            "source_ref",
            name="uq_revenue_snapshot_source",''',
    '''            "amount_state",
            "source_ref",
            "source_updated_at",
            name="uq_revenue_snapshot_source",''',
)
if 'sa.Column("pending_revenue"' not in migration:
    migration = migration.replace(
        '        sa.Column("estimated_revenue", MONEY, nullable=False, server_default="0"),\n',
        '        sa.Column("estimated_revenue", MONEY, nullable=False, server_default="0"),\n'
        '        sa.Column("pending_revenue", MONEY, nullable=False, server_default="0"),\n',
    )
MIGRATION.write_text(migration, encoding="utf-8")

# Adjust existing fixtures for strict link-health and exact policy authority.
tests = TESTS.read_text(encoding="utf-8")
tests = tests.replace(
    '''    link = service.register_affiliate_link(
        offer_snapshot_id=offer.id,
        canonical_url="https://example.com/product?ref=vcos",
        short_url=None,
        utm_policy_version="utm-v1",
    )
    blocked = service.assess_disclosures(''',
    '''    link = service.register_affiliate_link(
        offer_snapshot_id=offer.id,
        canonical_url="https://example.com/product?ref=vcos",
        short_url=None,
        utm_policy_version="utm-v1",
    )
    link.last_health_state = "HEALTHY"
    link.last_checked_at = now
    db.flush()
    blocked = service.assess_disclosures(''',
)
tests = tests.replace(
    '''        production_package_hash="e" * 64,
        candidate_hash="f" * 64,''',
    '''        production_package_hash="e" * 64,
        policy_snapshot_hash="a" * 64,
        candidate_hash="f" * 64,''',
)
extra = r'''


def test_revenue_lifecycle_uses_latest_state_without_double_count(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    start = now - timedelta(days=30)
    service.record_revenue(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source="YOUTUBE",
        amount_state="ESTIMATED",
        amount="200",
        currency="USD",
        period_start=start,
        period_end=now,
        source_ref="youtube://revenue/stable-item",
        source_updated_at=now - timedelta(days=2),
        confidence_state="HIGH",
    )
    service.record_revenue(
        company_id=company_id,
        channel_workspace_id=channel_id,
        source="YOUTUBE",
        amount_state="FINALIZED",
        amount="180",
        currency="USD",
        period_start=start,
        period_end=now,
        source_ref="youtube://revenue/stable-item",
        source_updated_at=now - timedelta(days=1),
        confidence_state="HIGH",
    )
    pnl = service.build_channel_pnl(
        company_id=company_id,
        channel_workspace_id=channel_id,
        period_start=start,
        period_end=now,
        currency="USD",
        direct_cost="80",
        allocated_ops_cost="20",
    )
    assert pnl.estimated_revenue == Decimal("0.000000")
    assert pnl.finalized_revenue == Decimal("180.000000")
    assert pnl.contribution_margin == Decimal("80.000000")
    assert len(pnl.source_snapshot_refs) == 1


def test_self_funding_rejects_stale_payment_authority(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    now = utc_now()
    service.record_payment_status(
        company_id=company_id,
        payee_ref="payee://stale",
        tax_state="VERIFIED",
        address_verification_state="VERIFIED",
        payment_method_state="READY",
        payment_hold_state="NONE",
        source_type="OPERATOR_ATTESTATION",
        source_ref="evidence://stale-payment",
        confidence_state="HIGH",
        source_updated_at=now - timedelta(days=40),
        valid_until=now - timedelta(days=1),
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
    assessment = service.evaluate_self_funding(
        company_id=company_id,
        channel_workspace_id=channel_id,
        assessment_window_end=now,
    )
    assert assessment.decision == "FUNDED_EXPERIMENT"
    assert "PAYMENT_STATE_STALE_OR_UNTRUSTED" in assessment.reason_codes


def test_business_monitor_creates_action_first_queue(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    actions = service.refresh_action_queue(
        company_id=company_id,
        channel_workspace_id=channel_id,
        observed_at=utc_now(),
    )
    assert {item.reason_code for item in actions} == {
        "PAYMENT_PROFILE_MISSING_OR_STALE",
        "MONETIZATION_STATUS_MISSING_OR_STALE",
    }


def test_appeal_pack_cannot_self_approve(db: Session) -> None:
    company_id, channel_id, _ = _scope()
    service = BusinessMonitoringService(db)
    incident = service.open_enforcement_incident(
        company_id=company_id,
        channel_workspace_id=channel_id,
        platform="YOUTUBE",
        external_incident_ref="appeal-human-boundary",
        incident_type="CONTENT_ID",
        severity="MEDIUM",
        scope="VIDEO",
        source_ref="youtube://incident",
        evidence_payload={},
        detected_at=utc_now(),
    )
    pack = service.create_appeal_pack(
        incident_id=incident.id,
        rights_basis="Owned source",
        evidence_items=[{"ref": "rights://owned"}],
        timeline=[],
    )
    assert pack.state == "READY_FOR_HUMAN"
    assert pack.approved_by is None
    actor_id = uuid.uuid4()
    approved = service.approve_appeal_pack(pack_id=pack.id, actor_id=actor_id)
    assert approved.state == "HUMAN_APPROVED"
    assert approved.approved_by == actor_id
'''
if "test_revenue_lifecycle_uses_latest_state_without_double_count" not in tests:
    tests += extra
TESTS.write_text(tests, encoding="utf-8")

# Do not reformat the large pre-existing publish service; inserted code is
# already formatted and keeping the diff surgical matters.
subprocess.run(
    [
        "ruff",
        "format",
        "app/db/models/remaining_debt.py",
        "app/services/remaining_debt_closeout.py",
        "tests/test_remaining_debt_closeout.py",
        "alembic/versions/0085_series_authority.py",
        "alembic/versions/0086_learning_authority.py",
        "alembic/versions/0087_business_os.py",
    ],
    cwd=ROOT,
    check=True,
)

for relative in (
    ".github/workflows/debt-closeout-source.yml",
    ".github/workflows/apply-remaining-debt-closeout.yml",
    ".github/workflows/fix-remaining-debt-closeout.yml",
    ".github/workflows/complete-remaining-debt-closeout.yml",
    ".github/workflows/final-hardening-v2.yml",
    "tools/finalize_remaining_debt_closeout.py",
    "tools/harden_remaining_debt_closeout.py",
    "tools/complete_remaining_debt_closeout.py",
    "tools/final_hardening_v2.py",
):
    (ROOT / relative).unlink(missing_ok=True)
