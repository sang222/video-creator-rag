from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m10 import LearningCandidateGenerationRunCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    DomainEvent,
    EngagementDiagnosticRun,
    FailureTraceReport,
    LearningCandidate,
    LearningCandidateGenerationRun,
    LearningEvidenceBundle,
    LearningPromotionEligibilityRun,
    LearningReviewQueueItem,
    NoViewDiagnosticRun,
    PackagingDiagnosticRun,
    PlaybookCandidateDraft,
    PolicyRightsDiagnosticRun,
    RecoveryProposal,
    RetentionDiagnosticRun,
    UploadedVideo,
    UploadedVideoMetricsSummary,
)
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus


REVIEW_ACTIONS_FOR_ELIGIBLE = ["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"]
REVIEW_ACTIONS_FOR_NEEDS_EVIDENCE = ["REQUEST_MORE_EVIDENCE", "SUPPRESS", "EXPIRE"]
REVIEW_ACTIONS_FOR_BLOCKED = ["REJECT", "SUPPRESS", "EXPIRE"]
METRIC_KEYS = [
    "views",
    "impressions",
    "click_through_rate",
    "average_view_duration_seconds",
    "average_view_percentage",
    "engagement_rate",
]


@dataclass(frozen=True)
class LearningEvidenceSet:
    uploaded: UploadedVideo
    analytics_snapshot: AnalyticsSnapshot | None
    metrics_summary: UploadedVideoMetricsSummary | None
    failure_trace_report: FailureTraceReport | None
    recovery_proposal: RecoveryProposal | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class GateDecision:
    result: str
    candidate_state: str
    min_evidence_met: bool
    metric_freshness_ok: bool
    policy_flags_ok: bool
    rights_flags_ok: bool
    confidence_label: str
    risk_level: str
    blockers: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    reason_codes: list[str]
    operator_summary: str
    next_action: str


class LearningCandidateGenerationService:
    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        *,
        data: LearningCandidateGenerationRunCreate,
        correlation_id: str = "m10-learning-generation-create",
    ) -> LearningCandidateGenerationRun:
        uploaded = _require_uploaded(self.session, data.uploaded_video_id)
        evidence = _load_evidence_set(
            self.session,
            uploaded,
            failure_trace_report_id=data.source_failure_trace_report_id,
            recovery_proposal_id=data.source_recovery_proposal_id,
            analytics_snapshot_id=data.source_analytics_snapshot_id,
            metrics_summary_id=data.source_uploaded_video_metrics_summary_id,
        )
        run_state = "PENDING"
        reason_codes = ["LEARNING_CANDIDATE_GENERATED"]
        next_action = "Execute the learning candidate generation run."
        if data.run_mode == "REAL_DISABLED":
            run_state = "BLOCKED"
            reason_codes = ["REAL_OLLAMA_ROUTER_DEFERRED_TO_M10_1", "NO_AUTO_PROMOTION"]
            next_action = "Use RULE_BASED mode in M10; real LLM routing is deferred."
        run = LearningCandidateGenerationRun(
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            uploaded_video_id=uploaded.id,
            source_failure_trace_report_id=evidence.failure_trace_report.id if evidence.failure_trace_report else None,
            source_recovery_proposal_id=evidence.recovery_proposal.id if evidence.recovery_proposal else None,
            source_analytics_snapshot_id=evidence.analytics_snapshot.id if evidence.analytics_snapshot else None,
            source_uploaded_video_metrics_summary_id=evidence.metrics_summary.id if evidence.metrics_summary else None,
            run_mode=data.run_mode,
            run_state=run_state,
            generated_candidate_count=0,
            reason_codes=reason_codes,
            next_action=next_action,
            metadata_={
                **data.metadata,
                "m10_rule_based_only": True,
                "no_real_provider_call": True,
                "no_config_recommendation": True,
                "no_auto_promotion": True,
            },
        )
        self.session.add(run)
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_candidate_generation_run.created",
            aggregate_type="learning_candidate_generation_run",
            aggregate_id=run.id,
            target_type="learning_candidate_generation_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code=reason_codes[0],
            payload={
                "uploaded_video_id": str(uploaded.id),
                "run_mode": run.run_mode,
                "run_state": run.run_state,
                "no_auto_promotion": True,
                "no_config_recommendation": True,
            },
        )
        if run.run_state == "BLOCKED":
            _record_m10_event(
                self.session,
                event_type="learning_candidate_generation_run.blocked",
                aggregate_type="learning_candidate_generation_run",
                aggregate_id=run.id,
                target_type="learning_candidate_generation_run",
                target_id=run.id,
                company_id=run.company_id,
                correlation_id=correlation_id,
                reason_code="REAL_OLLAMA_ROUTER_DEFERRED_TO_M10_1",
                payload={"run_mode": run.run_mode, "next_action": run.next_action},
            )
        return run

    def execute_run(
        self,
        *,
        run_id: uuid.UUID,
        correlation_id: str = "m10-learning-generation-execute",
    ) -> LearningCandidateGenerationRun:
        run = self.require_run(run_id)
        if run.run_state in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}:
            return run
        if run.uploaded_video_id is None:
            self._block_run(run, ["METRIC_SUPPORT_INSUFFICIENT"], "Run has no UploadedVideo source.", correlation_id)
            return run
        uploaded = _require_uploaded(self.session, run.uploaded_video_id)
        evidence = _load_evidence_set(
            self.session,
            uploaded,
            failure_trace_report_id=run.source_failure_trace_report_id,
            recovery_proposal_id=run.source_recovery_proposal_id,
            analytics_snapshot_id=run.source_analytics_snapshot_id,
            metrics_summary_id=run.source_uploaded_video_metrics_summary_id,
        )
        run.started_at = utc_now()
        run.run_state = "RUNNING"
        self.session.flush()
        missing = _missing_required_sources(evidence)
        if missing:
            self._block_run(
                run,
                ["LEARNING_NEEDS_MORE_EVIDENCE", "METRIC_SUPPORT_INSUFFICIENT", "M11_APPROVAL_REQUIRED"],
                "Add the missing M8/M9 evidence before creating a learning candidate.",
                correlation_id,
                missing_sources=missing,
            )
            return run

        candidate = self._create_candidate(run=run, evidence=evidence, correlation_id=correlation_id)
        bundle = EvidenceBundleService(self.session).create_for_candidate(
            candidate=candidate,
            evidence=evidence,
            correlation_id=correlation_id,
        )
        eligibility = PromotionEligibilityGateService(self.session).evaluate(
            candidate=candidate,
            evidence_bundle=bundle,
            correlation_id=correlation_id,
        )
        LearningReviewQueueService(self.session).create_for_candidate(
            candidate=candidate,
            evidence_bundle=bundle,
            eligibility_run=eligibility,
            correlation_id=correlation_id,
        )
        if eligibility.result == "ELIGIBLE_FOR_REVIEW":
            PlaybookCandidateDraftService(self.session).create_for_candidate(
                candidate=candidate,
                evidence_bundle=bundle,
                correlation_id=correlation_id,
            )
        run.source_failure_trace_report_id = evidence.failure_trace_report.id
        run.source_recovery_proposal_id = evidence.recovery_proposal.id
        run.source_analytics_snapshot_id = evidence.analytics_snapshot.id
        run.source_uploaded_video_metrics_summary_id = evidence.metrics_summary.id
        run.run_state = "COMPLETED"
        run.completed_at = utc_now()
        run.generated_candidate_count = 1
        run.reason_codes = _dedupe(
            [
                *run.reason_codes,
                "LEARNING_CANDIDATE_GENERATED",
                "LEARNING_EVIDENCE_BUNDLE_CREATED",
                "PROMOTION_ELIGIBILITY_RUN_CREATED",
                "DASHBOARD_QUEUE_ITEM_CREATED",
                "NO_AUTO_PROMOTION",
                "M11_APPROVAL_REQUIRED",
            ]
        )
        run.next_action = "Learning candidate is prepared for M11 human review workflow."
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_candidate_generation_run.completed",
            aggregate_type="learning_candidate_generation_run",
            aggregate_id=run.id,
            target_type="learning_candidate_generation_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="LEARNING_CANDIDATE_GENERATED",
            payload={
                "generated_candidate_count": run.generated_candidate_count,
                "learning_candidate_id": str(candidate.id),
                "evidence_bundle_id": str(bundle.id),
                "eligibility_run_id": str(eligibility.id),
                "no_auto_promotion": True,
            },
        )
        return run

    def _block_run(
        self,
        run: LearningCandidateGenerationRun,
        reason_codes: list[str],
        next_action: str,
        correlation_id: str,
        *,
        missing_sources: list[str] | None = None,
    ) -> None:
        run.run_state = "BLOCKED"
        run.completed_at = utc_now()
        run.generated_candidate_count = 0
        run.reason_codes = _dedupe([*run.reason_codes, *reason_codes])
        run.next_action = next_action
        run.metadata_ = {
            **(run.metadata_ or {}),
            "missing_sources": missing_sources or [],
            "no_candidate_without_source_evidence": True,
            "no_auto_promotion": True,
        }
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_candidate_generation_run.blocked",
            aggregate_type="learning_candidate_generation_run",
            aggregate_id=run.id,
            target_type="learning_candidate_generation_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code=reason_codes[0],
            payload={"reason_codes": reason_codes, "missing_sources": missing_sources or [], "next_action": next_action},
        )

    def _create_candidate(
        self,
        *,
        run: LearningCandidateGenerationRun,
        evidence: LearningEvidenceSet,
        correlation_id: str,
    ) -> LearningCandidate:
        assert evidence.analytics_snapshot is not None
        assert evidence.metrics_summary is not None
        assert evidence.failure_trace_report is not None
        assert evidence.recovery_proposal is not None
        candidate_type, category = _classify_candidate(evidence.failure_trace_report, evidence.recovery_proposal)
        source_refs = _source_refs(evidence)
        diagnostic_refs = _diagnostic_refs(evidence)
        recovery_refs = [{"type": "RecoveryProposal", "id": str(evidence.recovery_proposal.id), "proposal_type": evidence.recovery_proposal.proposal_type}]
        metric_support = _metric_support(evidence.analytics_snapshot, evidence.metrics_summary)
        metric_refs = [
            {"type": "metric_support", "metric_key": item["metric_key"], "source_snapshot_id": item["source_snapshot_id"]}
            for item in metric_support
            if item["availability"] == "AVAILABLE"
        ]
        policy_flags, rights_flags = _policy_rights_flags(evidence)
        counter_evidence = _counter_evidence(evidence)
        limitations = _limitations(evidence, metric_support)
        confidence = _candidate_confidence(evidence.failure_trace_report.confidence_level, counter_evidence, metric_support)
        risk_level = _candidate_risk(evidence.recovery_proposal.risk_level, policy_flags, rights_flags)
        candidate = LearningCandidate(
            generation_run_id=run.id,
            company_id=evidence.uploaded.company_id,
            channel_workspace_id=evidence.uploaded.channel_workspace_id,
            video_project_id=evidence.uploaded.video_project_id,
            uploaded_video_id=evidence.uploaded.id,
            candidate_type=candidate_type,
            candidate_state="GENERATED",
            operator_summary=_candidate_operator_summary(evidence.failure_trace_report, evidence.recovery_proposal),
            friendly_status="Có tín hiệu tốt, nhưng cần human review trước khi dùng lại.",
            candidate_summary=_candidate_summary(candidate_type, evidence.failure_trace_report),
            suggested_learning=_suggested_learning(candidate_type, evidence.failure_trace_report),
            suggested_playbook_text=_suggested_playbook_text(category, evidence.failure_trace_report),
            recommended_scope="CHANNEL" if not policy_flags and not rights_flags else "DO_NOT_PROMOTE",
            confidence_label=confidence,
            risk_level=risk_level,
            source_refs=source_refs,
            diagnostic_refs=diagnostic_refs,
            recovery_refs=recovery_refs,
            metric_refs=metric_refs,
            policy_flags=policy_flags,
            rights_flags=rights_flags,
            limitations=limitations,
            counter_evidence=counter_evidence,
            technical_appendix={
                "lineage_refs": _lineage_refs(evidence.uploaded),
                "metric_support": metric_support,
                "policy_snapshot_ref_lineage_only": str(evidence.uploaded.policy_snapshot_id),
                "single_case_review_rule": "A single M9 evidence set may enter human review with bounded confidence; it cannot auto-promote.",
                "category_hint": category,
                "m10_no_approval": True,
                "m10_no_config_recommendation": True,
                "m10_no_provider_routing": True,
            },
        )
        self.session.add(candidate)
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_candidate.generated",
            aggregate_type="learning_candidate",
            aggregate_id=candidate.id,
            target_type="learning_candidate",
            target_id=candidate.id,
            company_id=candidate.company_id,
            correlation_id=correlation_id,
            reason_code="LEARNING_CANDIDATE_GENERATED",
            payload={
                "candidate_type": candidate.candidate_type,
                "candidate_state": candidate.candidate_state,
                "generation_run_id": str(run.id),
                "no_auto_promotion": True,
                "m11_approval_required": True,
            },
        )
        return candidate

    def require_run(self, run_id: uuid.UUID) -> LearningCandidateGenerationRun:
        run = self.session.get(LearningCandidateGenerationRun, run_id)
        if run is None:
            raise NotFoundError(f"learning candidate generation run not found: {run_id}")
        return run


class EvidenceBundleService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_candidate(
        self,
        *,
        candidate: LearningCandidate,
        evidence: LearningEvidenceSet,
        correlation_id: str,
    ) -> LearningEvidenceBundle:
        assert evidence.analytics_snapshot is not None
        assert evidence.metrics_summary is not None
        metric_support = candidate.technical_appendix.get("metric_support") or _metric_support(
            evidence.analytics_snapshot,
            evidence.metrics_summary,
        )
        bundle = LearningEvidenceBundle(
            learning_candidate_id=candidate.id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            evidence_summary=_evidence_summary(candidate, evidence),
            source_video_refs=[{"type": "UploadedVideo", "id": str(evidence.uploaded.id), "platform_video_id": evidence.uploaded.platform_video_id}],
            source_project_refs=[
                {"type": "VideoProject", "id": str(evidence.uploaded.video_project_id)},
                {"type": "RenderPackage", "id": str(evidence.uploaded.render_package_snapshot_id)},
                {"type": "SourceManifest", "id": str(evidence.uploaded.source_manifest_snapshot_id) if evidence.uploaded.source_manifest_snapshot_id else None},
            ],
            analytics_snapshot_refs=[
                {
                    "type": "AnalyticsSnapshot",
                    "id": str(evidence.analytics_snapshot.id),
                    "freshness_state": evidence.analytics_snapshot.freshness_state,
                    "confidence_level": evidence.analytics_snapshot.confidence_level,
                },
                {"type": "UploadedVideoMetricsSummary", "id": str(evidence.metrics_summary.id)},
            ],
            diagnostic_refs=candidate.diagnostic_refs,
            recovery_refs=candidate.recovery_refs,
            metric_support=metric_support,
            counter_evidence=candidate.counter_evidence,
            limitations=candidate.limitations,
            freshness_summary={
                "analytics_snapshot_id": str(evidence.analytics_snapshot.id),
                "analytics_freshness_state": evidence.analytics_snapshot.freshness_state,
                "metrics_summary_freshness_state": evidence.metrics_summary.freshness_state,
                "freshness_required_for_review": True,
            },
            confidence_summary={
                "candidate_confidence_label": candidate.confidence_label,
                "diagnostic_confidence_level": evidence.failure_trace_report.confidence_level if evidence.failure_trace_report else "UNKNOWN",
                "single_case_review_only": True,
            },
            policy_rights_summary={
                "policy_flags": candidate.policy_flags,
                "rights_flags": candidate.rights_flags,
                "blocked_if_unresolved": bool(candidate.policy_flags or candidate.rights_flags),
            },
        )
        self.session.add(bundle)
        self.session.flush()
        candidate.evidence_bundle_id = bundle.id
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_evidence_bundle.created",
            aggregate_type="learning_evidence_bundle",
            aggregate_id=bundle.id,
            target_type="learning_evidence_bundle",
            target_id=bundle.id,
            company_id=bundle.company_id,
            correlation_id=correlation_id,
            reason_code="LEARNING_EVIDENCE_BUNDLE_CREATED",
            payload={
                "learning_candidate_id": str(candidate.id),
                "metric_support_count": len(metric_support),
                "limitations_count": len(bundle.limitations),
                "counter_evidence_count": len(bundle.counter_evidence),
            },
        )
        return bundle


class PromotionEligibilityGateService:
    def __init__(self, session: Session):
        self.session = session

    def evaluate(
        self,
        *,
        candidate: LearningCandidate,
        evidence_bundle: LearningEvidenceBundle,
        correlation_id: str,
    ) -> LearningPromotionEligibilityRun:
        decision = _evaluate_gate(candidate, evidence_bundle)
        eligibility = LearningPromotionEligibilityRun(
            learning_candidate_id=candidate.id,
            evidence_bundle_id=evidence_bundle.id,
            result=decision.result,
            min_evidence_met=decision.min_evidence_met,
            metric_freshness_ok=decision.metric_freshness_ok,
            policy_flags_ok=decision.policy_flags_ok,
            rights_flags_ok=decision.rights_flags_ok,
            confidence_label=decision.confidence_label,
            risk_level=decision.risk_level,
            blockers=decision.blockers,
            warnings=decision.warnings,
            reason_codes=decision.reason_codes,
            operator_summary=decision.operator_summary,
            next_action=decision.next_action,
        )
        self.session.add(eligibility)
        self.session.flush()
        candidate.eligibility_run_id = eligibility.id
        candidate.candidate_state = decision.candidate_state
        candidate.confidence_label = decision.confidence_label
        candidate.risk_level = decision.risk_level
        if decision.result == "ELIGIBLE_FOR_REVIEW":
            candidate.friendly_status = "Đủ điều kiện đưa vào dashboard review."
        elif decision.result == "BLOCKED":
            candidate.friendly_status = "Bị chặn vì còn rủi ro rights/disclosure."
        else:
            candidate.friendly_status = "Bài học này chưa đủ bằng chứng để đưa vào playbook."
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="learning_promotion_eligibility_run.created",
            aggregate_type="learning_promotion_eligibility_run",
            aggregate_id=eligibility.id,
            target_type="learning_promotion_eligibility_run",
            target_id=eligibility.id,
            company_id=candidate.company_id,
            correlation_id=correlation_id,
            reason_code="PROMOTION_ELIGIBILITY_RUN_CREATED",
            payload={
                "learning_candidate_id": str(candidate.id),
                "result": eligibility.result,
                "candidate_state": candidate.candidate_state,
                "no_auto_promotion": True,
            },
        )
        if decision.result == "BLOCKED":
            _record_m10_event(
                self.session,
                event_type="learning_candidate.blocked_policy_risk",
                aggregate_type="learning_candidate",
                aggregate_id=candidate.id,
                target_type="learning_candidate",
                target_id=candidate.id,
                company_id=candidate.company_id,
                correlation_id=correlation_id,
                reason_code=decision.reason_codes[0],
                payload={"candidate_state": candidate.candidate_state, "blockers": decision.blockers},
            )
        if decision.result == "NEEDS_MORE_EVIDENCE":
            _record_m10_event(
                self.session,
                event_type="learning_candidate.needs_more_evidence",
                aggregate_type="learning_candidate",
                aggregate_id=candidate.id,
                target_type="learning_candidate",
                target_id=candidate.id,
                company_id=candidate.company_id,
                correlation_id=correlation_id,
                reason_code="LEARNING_NEEDS_MORE_EVIDENCE",
                payload={"candidate_state": candidate.candidate_state, "warnings": decision.warnings},
            )
        return eligibility


class LearningReviewQueueService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_candidate(
        self,
        *,
        candidate: LearningCandidate,
        evidence_bundle: LearningEvidenceBundle,
        eligibility_run: LearningPromotionEligibilityRun,
        correlation_id: str,
    ) -> LearningReviewQueueItem:
        queue = LearningReviewQueueItem(
            learning_candidate_id=candidate.id,
            evidence_bundle_id=evidence_bundle.id,
            eligibility_run_id=eligibility_run.id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            uploaded_video_id=candidate.uploaded_video_id,
            queue_state=_queue_state(eligibility_run.result),
            priority=_queue_priority(eligibility_run),
            operator_summary=eligibility_run.operator_summary,
            friendly_status=candidate.friendly_status,
            evidence_summary=evidence_bundle.evidence_summary,
            recommended_scope=candidate.recommended_scope,
            confidence_label=eligibility_run.confidence_label,
            risk_level=eligibility_run.risk_level,
            next_action=eligibility_run.next_action or "Human review in M11 is required before any reuse.",
            approval_actions_allowed=_review_actions(eligibility_run.result),
            source_refs=candidate.source_refs,
            audit_refs=[],
            technical_appendix={
                "learning_candidate_id": str(candidate.id),
                "evidence_bundle_id": str(evidence_bundle.id),
                "eligibility_run_id": str(eligibility_run.id),
                "limitations": candidate.limitations,
                "counter_evidence": candidate.counter_evidence,
                "m10_prepares_m11_review_only": True,
                "no_approval_action_implemented": True,
                "no_auto_promotion": True,
                "no_config_recommendation": True,
            },
        )
        self.session.add(queue)
        self.session.flush()
        audit_refs = _record_m10_event(
            self.session,
            event_type="learning_review_queue_item.created",
            aggregate_type="learning_review_queue_item",
            aggregate_id=queue.id,
            target_type="learning_review_queue_item",
            target_id=queue.id,
            company_id=queue.company_id,
            correlation_id=correlation_id,
            reason_code="DASHBOARD_QUEUE_ITEM_CREATED",
            payload={
                "learning_candidate_id": str(candidate.id),
                "queue_state": queue.queue_state,
                "approval_actions_allowed": queue.approval_actions_allowed,
                "actions_are_future_m11_only": True,
            },
        )
        queue.audit_refs = audit_refs
        self.session.flush()
        return queue

    def list_queue(self, *, queue_state: str | None = None, company_id: uuid.UUID | None = None) -> list[LearningReviewQueueItem]:
        statement = select(LearningReviewQueueItem).order_by(
            LearningReviewQueueItem.created_at.desc(),
            LearningReviewQueueItem.id.desc(),
        )
        if queue_state is not None:
            statement = statement.where(LearningReviewQueueItem.queue_state == queue_state)
        if company_id is not None:
            statement = statement.where(LearningReviewQueueItem.company_id == company_id)
        return list(self.session.scalars(statement).all())


class PlaybookCandidateDraftService:
    def __init__(self, session: Session):
        self.session = session

    def create_for_candidate(
        self,
        *,
        candidate: LearningCandidate,
        evidence_bundle: LearningEvidenceBundle,
        correlation_id: str,
    ) -> PlaybookCandidateDraft:
        category = _category_from_candidate_type(candidate.candidate_type)
        scope = candidate.recommended_scope if candidate.recommended_scope != "DO_NOT_PROMOTE" else "UNKNOWN"
        draft = PlaybookCandidateDraft(
            learning_candidate_id=candidate.id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            candidate_scope=scope if scope in {"CHANNEL", "SERIES", "COMPANY_DEBRANDED"} else "UNKNOWN",
            playbook_category=category,
            draft_text=candidate.suggested_playbook_text or candidate.suggested_learning,
            rationale="Draft only. M11 approval is required before this can become reusable playbook guidance.",
            evidence_refs=evidence_bundle.analytics_snapshot_refs + evidence_bundle.diagnostic_refs + evidence_bundle.recovery_refs,
            risk_notes=[
                {"risk_level": candidate.risk_level, "note": "No automatic promotion. Human review required."},
                *candidate.policy_flags,
                *candidate.rights_flags,
            ],
            state="READY_FOR_REVIEW",
        )
        self.session.add(draft)
        self.session.flush()
        _record_m10_event(
            self.session,
            event_type="playbook_candidate_draft.created",
            aggregate_type="playbook_candidate_draft",
            aggregate_id=draft.id,
            target_type="playbook_candidate_draft",
            target_id=draft.id,
            company_id=draft.company_id,
            correlation_id=correlation_id,
            reason_code="PLAYBOOK_CANDIDATE_DRAFT_CREATED",
            payload={
                "learning_candidate_id": str(candidate.id),
                "state": draft.state,
                "not_approved": True,
                "m11_approval_required": True,
            },
        )
        return draft


class LearningReadService:
    def __init__(self, session: Session):
        self.session = session

    def require_candidate(self, candidate_id: uuid.UUID) -> LearningCandidate:
        candidate = self.session.get(LearningCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"learning candidate not found: {candidate_id}")
        return candidate

    def list_candidates(
        self,
        *,
        candidate_state: str | None = None,
        company_id: uuid.UUID | None = None,
        uploaded_video_id: uuid.UUID | None = None,
    ) -> list[LearningCandidate]:
        statement = select(LearningCandidate).order_by(LearningCandidate.created_at.desc(), LearningCandidate.id.desc())
        if candidate_state is not None:
            statement = statement.where(LearningCandidate.candidate_state == candidate_state)
        if company_id is not None:
            statement = statement.where(LearningCandidate.company_id == company_id)
        if uploaded_video_id is not None:
            statement = statement.where(LearningCandidate.uploaded_video_id == uploaded_video_id)
        return list(self.session.scalars(statement).all())

    def require_evidence_bundle_for_candidate(self, candidate_id: uuid.UUID) -> LearningEvidenceBundle:
        candidate = self.require_candidate(candidate_id)
        if candidate.evidence_bundle_id is None:
            raise NotFoundError(f"evidence bundle not found for learning candidate: {candidate_id}")
        bundle = self.session.get(LearningEvidenceBundle, candidate.evidence_bundle_id)
        if bundle is None:
            raise NotFoundError(f"evidence bundle not found: {candidate.evidence_bundle_id}")
        return bundle

    def require_queue_item(self, queue_item_id: uuid.UUID) -> LearningReviewQueueItem:
        item = self.session.get(LearningReviewQueueItem, queue_item_id)
        if item is None:
            raise NotFoundError(f"learning review queue item not found: {queue_item_id}")
        return item

    def require_playbook_candidate_draft(self, draft_id: uuid.UUID) -> PlaybookCandidateDraft:
        draft = self.session.get(PlaybookCandidateDraft, draft_id)
        if draft is None:
            raise NotFoundError(f"playbook candidate draft not found: {draft_id}")
        return draft


def _load_evidence_set(
    session: Session,
    uploaded: UploadedVideo,
    *,
    failure_trace_report_id: uuid.UUID | None = None,
    recovery_proposal_id: uuid.UUID | None = None,
    analytics_snapshot_id: uuid.UUID | None = None,
    metrics_summary_id: uuid.UUID | None = None,
) -> LearningEvidenceSet:
    summary = session.get(UploadedVideoMetricsSummary, metrics_summary_id) if metrics_summary_id else None
    if summary is None:
        summary = session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
        ).one_or_none()
    analytics = session.get(AnalyticsSnapshot, analytics_snapshot_id) if analytics_snapshot_id else None
    if analytics is None and summary and summary.latest_analytics_snapshot_id:
        analytics = session.get(AnalyticsSnapshot, summary.latest_analytics_snapshot_id)
    if analytics is None:
        analytics = session.scalars(
            select(AnalyticsSnapshot)
            .where(AnalyticsSnapshot.uploaded_video_id == uploaded.id)
            .order_by(AnalyticsSnapshot.captured_at.desc(), AnalyticsSnapshot.created_at.desc())
        ).first()
    report = session.get(FailureTraceReport, failure_trace_report_id) if failure_trace_report_id else None
    if report is None:
        report = session.scalars(
            select(FailureTraceReport)
            .where(FailureTraceReport.uploaded_video_id == uploaded.id)
            .order_by(FailureTraceReport.created_at.desc(), FailureTraceReport.id.desc())
        ).first()
    proposal = session.get(RecoveryProposal, recovery_proposal_id) if recovery_proposal_id else None
    if proposal is None and report is not None:
        proposal = session.scalars(
            select(RecoveryProposal)
            .where(RecoveryProposal.failure_trace_report_id == report.id)
            .order_by(RecoveryProposal.created_at.desc(), RecoveryProposal.id.desc())
        ).first()
    if proposal is None:
        proposal = session.scalars(
            select(RecoveryProposal)
            .where(RecoveryProposal.uploaded_video_id == uploaded.id)
            .order_by(RecoveryProposal.created_at.desc(), RecoveryProposal.id.desc())
        ).first()
    diagnostics: dict[str, Any] = {}
    if report is not None:
        health_run_id = report.post_publish_health_run_id
        diagnostics = {
            "no_view": session.scalars(select(NoViewDiagnosticRun).where(NoViewDiagnosticRun.post_publish_health_run_id == health_run_id)).first(),
            "packaging": session.scalars(
                select(PackagingDiagnosticRun).where(PackagingDiagnosticRun.post_publish_health_run_id == health_run_id)
            ).first(),
            "retention": session.scalars(
                select(RetentionDiagnosticRun).where(RetentionDiagnosticRun.post_publish_health_run_id == health_run_id)
            ).first(),
            "engagement": session.scalars(
                select(EngagementDiagnosticRun).where(EngagementDiagnosticRun.post_publish_health_run_id == health_run_id)
            ).first(),
            "policy_rights": session.scalars(
                select(PolicyRightsDiagnosticRun).where(PolicyRightsDiagnosticRun.post_publish_health_run_id == health_run_id)
            ).first(),
        }
    return LearningEvidenceSet(
        uploaded=uploaded,
        analytics_snapshot=analytics,
        metrics_summary=summary,
        failure_trace_report=report,
        recovery_proposal=proposal,
        diagnostics={key: value for key, value in diagnostics.items() if value is not None},
    )


def _missing_required_sources(evidence: LearningEvidenceSet) -> list[str]:
    missing = []
    if evidence.analytics_snapshot is None:
        missing.append("AnalyticsSnapshot")
    if evidence.metrics_summary is None:
        missing.append("UploadedVideoMetricsSummary")
    if evidence.failure_trace_report is None:
        missing.append("FailureTraceReport")
    if evidence.recovery_proposal is None:
        missing.append("RecoveryProposal")
    return missing


def _classify_candidate(report: FailureTraceReport, proposal: RecoveryProposal) -> tuple[str, str]:
    cause = report.primary_suspected_cause or report.primary_status
    proposal_type = proposal.proposal_type
    if cause == "PACKAGING_FAILURE" or proposal_type == "REVIEW_TITLE_THUMBNAIL":
        return "PACKAGING_PATTERN", "PACKAGING"
    if cause == "HOOK_FAILURE" or proposal_type == "REVIEW_HOOK":
        return "HOOK_PATTERN", "HOOK"
    if cause == "RETENTION_PACING_FAILURE" or proposal_type == "REVIEW_RETENTION_SECTION":
        return "RETENTION_PATTERN", "RETENTION"
    if cause == "POLICY_RIGHTS_REVIEW_REQUIRED" or proposal_type == "REVIEW_RIGHTS_DISCLOSURE":
        return "POLICY_RIGHTS_PATTERN", "POLICY"
    if cause == "LOW_ENGAGEMENT":
        return "CHANNEL_FIT_PATTERN", "OTHER"
    if report.primary_status == "NO_VIEW_RISK":
        return "TOPIC_DEMAND_PATTERN", "TOPIC"
    if proposal_type == "CREATE_FUTURE_VARIANT":
        return "RECOVERY_PATTERN", "RECOVERY"
    return "OTHER", "OTHER"


def _source_refs(evidence: LearningEvidenceSet) -> list[dict[str, Any]]:
    refs = [
        {"type": "UploadedVideo", "id": str(evidence.uploaded.id)},
        {"type": "VideoProject", "id": str(evidence.uploaded.video_project_id)},
        {"type": "RenderPackage", "id": str(evidence.uploaded.render_package_snapshot_id)},
        {"type": "PublishHandoff", "id": str(evidence.uploaded.publish_handoff_package_id)},
    ]
    if evidence.uploaded.source_manifest_snapshot_id:
        refs.append({"type": "SourceManifest", "id": str(evidence.uploaded.source_manifest_snapshot_id)})
    if evidence.uploaded.rights_envelope_ref:
        refs.append({"type": "RightsEnvelope", "id": evidence.uploaded.rights_envelope_ref})
    if evidence.analytics_snapshot:
        refs.append({"type": "AnalyticsSnapshot", "id": str(evidence.analytics_snapshot.id)})
    if evidence.metrics_summary:
        refs.append({"type": "UploadedVideoMetricsSummary", "id": str(evidence.metrics_summary.id)})
    if evidence.failure_trace_report:
        refs.append({"type": "FailureTraceReport", "id": str(evidence.failure_trace_report.id)})
    if evidence.recovery_proposal:
        refs.append({"type": "RecoveryProposal", "id": str(evidence.recovery_proposal.id)})
    return refs


def _diagnostic_refs(evidence: LearningEvidenceSet) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    if evidence.failure_trace_report:
        refs.append(
            {
                "type": "FailureTraceReport",
                "id": str(evidence.failure_trace_report.id),
                "primary_status": evidence.failure_trace_report.primary_status,
                "primary_suspected_cause": evidence.failure_trace_report.primary_suspected_cause,
            }
        )
    for name, diagnostic in evidence.diagnostics.items():
        refs.append(
            {
                "type": diagnostic.__class__.__name__,
                "id": str(diagnostic.id),
                "diagnostic_name": name,
                "diagnostic_state": diagnostic.diagnostic_state,
                "confidence_level": diagnostic.confidence_level,
            }
        )
    return refs


def _metric_support(analytics: AnalyticsSnapshot, summary: UploadedVideoMetricsSummary) -> list[dict[str, Any]]:
    availability = analytics.metric_availability or {}
    summary_availability = (summary.availability_summary or {}).get("availability", {})
    support: list[dict[str, Any]] = []
    for key in METRIC_KEYS:
        value = None
        source_snapshot_id = str(analytics.id)
        normalized = analytics.normalized_metrics_blob or {}
        raw = analytics.metrics_blob or {}
        summary_metrics = summary.metrics_summary or {}
        if key in normalized:
            item = normalized[key]
            value = item.get("value") if isinstance(item, dict) else item
        elif key in raw:
            value = raw[key]
        elif key in summary_metrics:
            item = summary_metrics[key]
            value = item.get("value") if isinstance(item, dict) else item
            source_snapshot_id = str(summary.id)
        availability_item = availability.get(key) or summary_availability.get(key) or {}
        availability_state = availability_item.get("state", "UNKNOWN") if isinstance(availability_item, dict) else "UNKNOWN"
        if value is not None and availability_state != "NOT_AVAILABLE":
            availability_state = "AVAILABLE"
        support.append(
            {
                "metric_key": key,
                "value": value,
                "availability": availability_state,
                "freshness_state": analytics.freshness_state,
                "confidence_level": analytics.confidence_level,
                "source_snapshot_id": source_snapshot_id,
                "interpretation_text": _metric_interpretation(key, value, availability_state),
            }
        )
    return support


def _metric_interpretation(key: str, value: Any, availability: str) -> str:
    if availability == "AVAILABLE":
        return f"{key} is available with value {value}."
    if availability == "NOT_AVAILABLE":
        return f"{key} is explicitly not available from the source snapshot."
    return f"{key} is unknown in the source snapshot."


def _policy_rights_flags(evidence: LearningEvidenceSet) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy_flags: list[dict[str, Any]] = []
    rights_flags: list[dict[str, Any]] = []
    diagnostic = evidence.diagnostics.get("policy_rights")
    if diagnostic is not None and diagnostic.diagnostic_state in {"REVIEW_REQUIRED", "BLOCKED"}:
        for code in diagnostic.reason_codes:
            flag = {"reason_code": code, "diagnostic_id": str(diagnostic.id), "blocking": True}
            if "RIGHTS" in code:
                rights_flags.append(flag)
            elif "POLICY" in code or "DISCLOSURE" in code:
                policy_flags.append(flag)
    disclosures = evidence.uploaded.actual_disclosures or {}
    if disclosures.get("rights_confirmed") is not True:
        rights_flags.append({"reason_code": "RIGHTS_FLAG_BLOCKING", "source": "UploadedVideo.actual_disclosures", "blocking": True})
    if disclosures.get("ai_disclosure_confirmed") is None:
        policy_flags.append({"reason_code": "POLICY_FLAG_BLOCKING", "source": "UploadedVideo.actual_disclosures", "blocking": True})
    return _unique_flags(policy_flags), _unique_flags(rights_flags)


def _counter_evidence(evidence: LearningEvidenceSet) -> list[dict[str, Any]]:
    report = evidence.failure_trace_report
    if report is None:
        return []
    counter: list[dict[str, Any]] = []
    for source in [report.operator_report or {}, report.technical_appendix or {}]:
        supplied = source.get("counter_evidence")
        if isinstance(supplied, list):
            counter.extend(item if isinstance(item, dict) else {"text": str(item)} for item in supplied)
    if report.primary_status == "HEALTHY":
        counter.append({"type": "healthy_status", "text": "M9 marked this uploaded video healthy."})
    return counter


def _limitations(evidence: LearningEvidenceSet, metric_support: list[dict[str, Any]]) -> list[dict[str, Any]]:
    limitations = [
        {"type": "single_case", "text": "Evidence comes from one uploaded video; M10 can queue review but cannot promote."},
        {"type": "human_review_required", "text": "Final approval and playbook promotion are M11 scope."},
    ]
    if evidence.analytics_snapshot and evidence.analytics_snapshot.freshness_state != "FRESH":
        limitations.append({"type": "analytics_freshness", "text": "Analytics freshness is not fresh.", "state": evidence.analytics_snapshot.freshness_state})
    for item in metric_support:
        if item["availability"] != "AVAILABLE":
            limitations.append({"type": "metric_availability", "metric_key": item["metric_key"], "availability": item["availability"]})
    return limitations


def _candidate_confidence(base: str, counter_evidence: list[dict[str, Any]], metric_support: list[dict[str, Any]]) -> str:
    confidence = base if base in {"HIGH", "MEDIUM", "LOW"} else "UNKNOWN"
    if confidence == "HIGH":
        confidence = "MEDIUM"
    if counter_evidence and confidence in {"HIGH", "MEDIUM"}:
        confidence = "LOW" if confidence == "MEDIUM" else "MEDIUM"
    available_count = sum(1 for item in metric_support if item["availability"] == "AVAILABLE")
    if available_count == 0:
        return "LOW"
    return confidence


def _candidate_risk(base: str, policy_flags: list[dict[str, Any]], rights_flags: list[dict[str, Any]]) -> str:
    if policy_flags or rights_flags:
        return "BLOCKED"
    if base in {"LOW", "MEDIUM", "HIGH"}:
        return base
    return "UNKNOWN"


def _candidate_operator_summary(report: FailureTraceReport, proposal: RecoveryProposal) -> str:
    return f"{report.operator_summary} M10 chỉ tạo learning candidate; không tự động promote."


def _candidate_summary(candidate_type: str, report: FailureTraceReport) -> str:
    return f"{candidate_type}: evidence from M9 report {report.primary_status} / {report.primary_suspected_cause or 'UNKNOWN'}."


def _suggested_learning(candidate_type: str, report: FailureTraceReport) -> str:
    return (
        f"Hypothesis: this {candidate_type.lower()} may be reusable only after human review, "
        f"because M9 observed {report.primary_status} with evidence refs."
    )


def _suggested_playbook_text(category: str, report: FailureTraceReport) -> str:
    return (
        f"Draft {category.lower()} learning: when future evidence shows {report.primary_suspected_cause or report.primary_status}, "
        "compare the M8/M9 evidence bundle before reuse. Do not apply automatically."
    )


def _evidence_summary(candidate: LearningCandidate, evidence: LearningEvidenceSet) -> str:
    report = evidence.failure_trace_report
    proposal = evidence.recovery_proposal
    status = report.primary_status if report else "UNKNOWN"
    proposal_type = proposal.proposal_type if proposal else "UNKNOWN"
    return f"M10 bundle uses UploadedVideo, M8 analytics, M9 failure trace ({status}), and recovery proposal ({proposal_type})."


def _evaluate_gate(candidate: LearningCandidate, evidence_bundle: LearningEvidenceBundle) -> GateDecision:
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    metric_support = evidence_bundle.metric_support or []
    source_types = {item.get("type") for item in candidate.source_refs}
    min_evidence_met = {"UploadedVideo", "AnalyticsSnapshot", "FailureTraceReport", "RecoveryProposal"} <= source_types
    metric_freshness_ok = evidence_bundle.freshness_summary.get("analytics_freshness_state") == "FRESH"
    policy_flags_ok = not candidate.policy_flags
    rights_flags_ok = not candidate.rights_flags
    metric_support_ok = _metric_support_ok(candidate.candidate_type, metric_support)
    reason_codes = ["PROMOTION_ELIGIBILITY_RUN_CREATED", "NO_AUTO_PROMOTION", "M11_APPROVAL_REQUIRED"]
    if not rights_flags_ok:
        blockers.extend(candidate.rights_flags)
        reason_codes.append("RIGHTS_FLAG_BLOCKING")
        return GateDecision(
            result="BLOCKED",
            candidate_state="BLOCKED_RIGHTS_RISK",
            min_evidence_met=min_evidence_met,
            metric_freshness_ok=metric_freshness_ok,
            policy_flags_ok=policy_flags_ok,
            rights_flags_ok=False,
            confidence_label="LOW",
            risk_level="BLOCKED",
            blockers=blockers,
            warnings=warnings,
            reason_codes=_dedupe(reason_codes + ["LEARNING_BLOCKED_RIGHTS_RISK"]),
            operator_summary="Bị chặn vì còn rủi ro rights/disclosure.",
            next_action="Resolve rights risk in M11 human review before any learning reuse.",
        )
    if not policy_flags_ok:
        blockers.extend(candidate.policy_flags)
        reason_codes.append("POLICY_FLAG_BLOCKING")
        return GateDecision(
            result="BLOCKED",
            candidate_state="BLOCKED_POLICY_RISK",
            min_evidence_met=min_evidence_met,
            metric_freshness_ok=metric_freshness_ok,
            policy_flags_ok=False,
            rights_flags_ok=rights_flags_ok,
            confidence_label="LOW",
            risk_level="BLOCKED",
            blockers=blockers,
            warnings=warnings,
            reason_codes=_dedupe(reason_codes + ["LEARNING_BLOCKED_POLICY_RISK"]),
            operator_summary="Bị chặn vì còn rủi ro rights/disclosure.",
            next_action="Resolve policy/disclosure risk in M11 human review before any learning reuse.",
        )
    if not min_evidence_met:
        warnings.append({"reason_code": "METRIC_SUPPORT_INSUFFICIENT", "text": "Required M8/M9 source refs are missing."})
    if not metric_freshness_ok:
        warnings.append({"reason_code": "ANALYTICS_FRESHNESS_INSUFFICIENT", "text": "Analytics freshness is stale or unknown."})
        reason_codes.append("ANALYTICS_FRESHNESS_INSUFFICIENT")
    if not metric_support_ok:
        warnings.append({"reason_code": "METRIC_SUPPORT_INSUFFICIENT", "text": "Metric support is not enough for this candidate type."})
        reason_codes.append("METRIC_SUPPORT_INSUFFICIENT")
    if candidate.counter_evidence:
        warnings.append({"reason_code": "COUNTER_EVIDENCE_PRESENT", "count": len(candidate.counter_evidence)})
        reason_codes.append("COUNTER_EVIDENCE_PRESENT")
    if not min_evidence_met or not metric_freshness_ok or not metric_support_ok:
        return GateDecision(
            result="NEEDS_MORE_EVIDENCE",
            candidate_state="NEEDS_MORE_EVIDENCE",
            min_evidence_met=min_evidence_met,
            metric_freshness_ok=metric_freshness_ok,
            policy_flags_ok=True,
            rights_flags_ok=True,
            confidence_label="LOW",
            risk_level="MEDIUM" if candidate.counter_evidence else "LOW",
            blockers=blockers,
            warnings=warnings,
            reason_codes=_dedupe(reason_codes + ["LEARNING_NEEDS_MORE_EVIDENCE"]),
            operator_summary="Bài học này chưa đủ bằng chứng để đưa vào playbook.",
            next_action="Collect fresher or more complete M8/M9 evidence before M11 approval.",
        )
    confidence = candidate.confidence_label
    risk = "MEDIUM" if candidate.counter_evidence else candidate.risk_level
    return GateDecision(
        result="ELIGIBLE_FOR_REVIEW",
        candidate_state="READY_FOR_HUMAN_REVIEW",
        min_evidence_met=True,
        metric_freshness_ok=True,
        policy_flags_ok=True,
        rights_flags_ok=True,
        confidence_label=confidence,
        risk_level=risk,
        blockers=blockers,
        warnings=warnings,
        reason_codes=_dedupe(reason_codes + ["LEARNING_READY_FOR_HUMAN_REVIEW"]),
        operator_summary="Đủ điều kiện đưa vào dashboard review. Không tự động promote.",
        next_action="Review in M11 before approval or rejection.",
    )


def _metric_support_ok(candidate_type: str, metric_support: list[dict[str, Any]]) -> bool:
    states = {item["metric_key"]: item for item in metric_support}

    def available(key: str) -> bool:
        return states.get(key, {}).get("availability") == "AVAILABLE"

    if candidate_type == "PACKAGING_PATTERN":
        return available("impressions") and available("click_through_rate")
    if candidate_type in {"HOOK_PATTERN", "RETENTION_PATTERN"}:
        return available("views") and (
            available("average_view_duration_seconds") or available("average_view_percentage")
        )
    if candidate_type == "CHANNEL_FIT_PATTERN":
        return available("views") and available("engagement_rate")
    return any(item["availability"] == "AVAILABLE" for item in metric_support)


def _queue_state(result: str) -> str:
    return {"ELIGIBLE_FOR_REVIEW": "READY_FOR_HUMAN_REVIEW", "NEEDS_MORE_EVIDENCE": "NEEDS_MORE_EVIDENCE", "BLOCKED": "BLOCKED"}.get(result, "NEEDS_MORE_EVIDENCE")


def _queue_priority(eligibility: LearningPromotionEligibilityRun) -> str:
    if eligibility.result == "BLOCKED":
        return "HIGH"
    if eligibility.confidence_label == "HIGH":
        return "HIGH"
    if eligibility.result == "NEEDS_MORE_EVIDENCE":
        return "LOW"
    return "NORMAL"


def _review_actions(result: str) -> list[str]:
    if result == "ELIGIBLE_FOR_REVIEW":
        return REVIEW_ACTIONS_FOR_ELIGIBLE
    if result == "BLOCKED":
        return REVIEW_ACTIONS_FOR_BLOCKED
    return REVIEW_ACTIONS_FOR_NEEDS_EVIDENCE


def _category_from_candidate_type(candidate_type: str) -> str:
    mapping = {
        "TOPIC_DEMAND_PATTERN": "TOPIC",
        "PACKAGING_PATTERN": "PACKAGING",
        "HOOK_PATTERN": "HOOK",
        "RETENTION_PATTERN": "RETENTION",
        "VISUAL_SOURCE_PATTERN": "VISUAL_SOURCE",
        "VOICE_NARRATION_PATTERN": "VOICE",
        "POLICY_RIGHTS_PATTERN": "POLICY",
        "COST_EFFICIENCY_PATTERN": "COST",
        "RECOVERY_PATTERN": "RECOVERY",
    }
    return mapping.get(candidate_type, "OTHER")


def _require_uploaded(session: Session, uploaded_video_id: uuid.UUID) -> UploadedVideo:
    uploaded = session.get(UploadedVideo, uploaded_video_id)
    if uploaded is None:
        raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
    return uploaded


def _lineage_refs(uploaded: UploadedVideo) -> dict[str, Any]:
    refs = dict(uploaded.lineage_refs or {})
    refs.update(
        {
            "uploaded_video_id": str(uploaded.id),
            "video_project_id": str(uploaded.video_project_id),
            "policy_snapshot_id": str(uploaded.policy_snapshot_id),
            "publish_handoff_package_id": str(uploaded.publish_handoff_package_id),
            "manual_publish_confirmation_id": str(uploaded.manual_publish_confirmation_id),
            "render_package_snapshot_id": str(uploaded.render_package_snapshot_id),
            "source_manifest_snapshot_id": str(uploaded.source_manifest_snapshot_id) if uploaded.source_manifest_snapshot_id else None,
            "rights_envelope_ref": uploaded.rights_envelope_ref,
        }
    )
    return refs


def _unique_flags(flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for flag in flags:
        key = (str(flag.get("reason_code")), str(flag.get("source") or flag.get("diagnostic_id")))
        if key not in seen:
            seen.add(key)
            result.append(flag)
    return result


def _record_m10_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
    actor_id: uuid.UUID | None = None,
) -> list[dict[str, str]]:
    safe_payload = _jsonable(payload)
    _ensure_no_secret_payload(safe_payload)
    domain_event: DomainEvent = DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=safe_payload,
            metadata={"milestone": "M10"},
        ),
        company_id=company_id,
    )
    audit_event = AuditService(session).append(
        AuditEnvelope(
            action=event_type,
            actor_type="system" if actor_id is None else "user",
            actor_id=actor_id,
            target_type=target_type,
            target_id=target_id,
            correlation_id=correlation_id,
            reason_code=reason_code,
            payload=safe_payload,
        ),
        company_id=company_id,
    )
    return [
        {"type": "domain_event", "id": str(domain_event.id), "event_type": event_type},
        {"type": "audit_event", "id": str(audit_event.id), "event_type": event_type},
    ]


SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")


def _ensure_no_secret_payload(value: Any) -> None:
    for key, item in _walk_items(value):
        normalized = key.lower().replace("-", "_")
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS) and normalized != "secret_ref":
            raise ValidationFailureError(f"secret-like payload key is not allowed: {key}")
        if isinstance(item, str) and any(marker in item for marker in RAW_SECRET_MARKERS):
            raise ValidationFailureError("raw secret-like value is not allowed")


def _walk_items(value: Any):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield str(child_key), child_value
            yield from _walk_items(child_value)
    elif isinstance(value, list):
        for child_value in value:
            yield "", child_value
            yield from _walk_items(child_value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
