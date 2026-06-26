from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m9 import PostPublishHealthRunCreate
from app.contracts.ops import ManualActionCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    DiagnosticTaxonomyVersion,
    DomainEvent,
    EngagementDiagnosticRun,
    EngagementSnapshot,
    FailureTraceReport,
    ManualAction,
    NoViewDiagnosticRun,
    PackagingDiagnosticRun,
    PolicyRightsDiagnosticRun,
    PostPublishHealthRun,
    PostPublishObservationWindow,
    RecoveryProposal,
    RetentionCurveSnapshot,
    RetentionDiagnosticRun,
    TrafficSourceSnapshot,
    UploadedVideo,
    UploadedVideoMetricsSummary,
)
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus
from app.services.ops import ManualActionService


WINDOW_DELTAS = {
    "T_PLUS_1H": timedelta(hours=1),
    "T_PLUS_6H": timedelta(hours=6),
    "T_PLUS_24H": timedelta(hours=24),
    "T_PLUS_48H": timedelta(hours=48),
    "T_PLUS_7D": timedelta(days=7),
}
WINDOW_MIN_IMPRESSIONS = {
    "T_PLUS_1H": 20,
    "T_PLUS_6H": 50,
    "T_PLUS_24H": 100,
    "T_PLUS_48H": 150,
    "T_PLUS_7D": 300,
    "CUSTOM": 100,
}
MIN_IMPRESSIONS_FOR_CTR = 100
LOW_CTR_PERCENT = 2.0
MIN_VIEWS_FOR_RETENTION = 30
MIN_VIEWS_FOR_ENGAGEMENT = 30
LOW_ENGAGEMENT_RATE = 0.02
DO_NOT_DO_DEFAULT = [
    "Do not reupload this video automatically.",
    "Do not use fake engagement, bought views, bot comments, or bot likes.",
    "Do not use platform evasion, IP tricks, or policy bypass.",
    "Do not auto-edit title, thumbnail, or platform metadata.",
]
FORBIDDEN_ACTION_TERMS = {
    "auto reupload",
    "delete video automatically",
    "fake engagement",
    "buy views",
    "bot comments",
    "bot likes",
    "platform evasion",
    "auto-edit",
    "auto publish",
}
TAXONOMY_VERSION = "1.0.0"
DIAGNOSTIC_TAXONOMY = {
    "HEALTHY": "Video đang ổn trong dữ liệu hiện có",
    "INSUFFICIENT_DATA": "Chưa đủ dữ liệu để kết luận",
    "DATA_UNAVAILABLE": "Dữ liệu cần thiết chưa có hoặc không khả dụng",
    "NO_VIEW_RISK": "Video có rủi ro rất ít lượt xem",
    "LOW_IMPRESSIONS": "Video đang có ít impressions",
    "LOW_CTR": "Tiêu đề/thumbnail có thể chưa kéo click",
    "EARLY_RETENTION_DROP": "Người xem rời sớm ở đoạn mở đầu",
    "MID_VIDEO_RETENTION_DROP": "Người xem rời ở phần giữa video",
    "LOW_ENGAGEMENT": "Tương tác thấp trên lượng xem đủ mẫu",
    "PACKAGING_FAILURE": "Tiêu đề/thumbnail có thể chưa kéo click",
    "HOOK_FAILURE": "Người xem rời sớm ở đoạn mở đầu",
    "RETENTION_PACING_FAILURE": "Nhịp video có thể bị chậm hoặc lặp",
    "VISUAL_RELEVANCE_RISK": "Hình ảnh có thể chưa đủ liên quan",
    "TOPIC_DEMAND_UNCERTAIN": "Chủ đề có thể chưa đủ nhu cầu hoặc chưa được phân phối",
    "DISTRIBUTION_UNCERTAIN": "Phân phối ban đầu còn chưa rõ",
    "POLICY_RIGHTS_REVIEW_REQUIRED": "Cần kiểm tra quyền, nhạc, disclosure hoặc policy",
    "DISCLOSURE_REVIEW_REQUIRED": "Cần kiểm tra disclosure đã xác nhận",
    "SOURCE_QUALITY_REVIEW_REQUIRED": "Cần kiểm tra chất lượng nguồn",
    "COST_EFFICIENCY_REVIEW_REQUIRED": "Cần kiểm tra hiệu quả chi phí",
}


@dataclass(frozen=True)
class MetricRead:
    value: float | None
    availability_state: str
    source_ref: str | None


@dataclass(frozen=True)
class DiagnosticContext:
    uploaded: UploadedVideo
    observation_window: str
    window: PostPublishObservationWindow | None
    analytics_snapshot: AnalyticsSnapshot | None
    metrics_summary: UploadedVideoMetricsSummary | None
    retention_snapshot: RetentionCurveSnapshot | None
    traffic_snapshot: TrafficSourceSnapshot | None
    engagement_snapshot: EngagementSnapshot | None


@dataclass(frozen=True)
class AggregateResult:
    run_state: str
    health_state: str
    severity: str
    confidence_level: str
    primary_status: str
    primary_suspected_cause: str | None
    secondary_suspected_causes: list[str]
    operator_summary: str
    next_action: str | None
    reason_codes: list[str]
    evidence_plain_text: list[str]
    proposal_type: str
    recommended_actions: list[str]
    risk_level: str


class ObservationWindowService:
    def __init__(self, session: Session):
        self.session = session

    def create_windows_for_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[PostPublishObservationWindow]:
        uploaded = _require_uploaded(self.session, uploaded_video_id)
        if uploaded.published_at is None:
            return []
        now = utc_now()
        windows: list[PostPublishObservationWindow] = []
        existing = {
            item.observation_window: item
            for item in self.session.scalars(
                select(PostPublishObservationWindow).where(PostPublishObservationWindow.uploaded_video_id == uploaded.id)
            ).all()
        }
        for name, delta in WINDOW_DELTAS.items():
            window = existing.get(name)
            window_end = uploaded.published_at + delta
            state = "READY" if now >= window_end else "PENDING"
            reason_codes = ["POST_PUBLISH_OBSERVATION_WINDOW_CREATED"]
            if state == "PENDING":
                reason_codes.append("OBSERVATION_WINDOW_NOT_READY")
            if window is None:
                window = PostPublishObservationWindow(
                    uploaded_video_id=uploaded.id,
                    platform=uploaded.platform,
                    platform_video_id=uploaded.platform_video_id,
                    published_at=uploaded.published_at,
                    observation_window=name,
                    window_start_at=uploaded.published_at,
                    window_end_at=window_end,
                    expected_check_at=window_end,
                    state=state,
                    reason_codes=reason_codes,
                )
                self.session.add(window)
                self.session.flush()
            elif window.state not in {"COMPLETED", "SKIPPED", "BLOCKED"}:
                window.state = state
                window.reason_codes = _dedupe([*window.reason_codes, *reason_codes])
            windows.append(window)
        self.session.flush()
        return sorted(windows, key=lambda item: item.expected_check_at)

    def get_window(self, uploaded_video_id: uuid.UUID, observation_window: str) -> PostPublishObservationWindow | None:
        return self.session.scalars(
            select(PostPublishObservationWindow).where(
                PostPublishObservationWindow.uploaded_video_id == uploaded_video_id,
                PostPublishObservationWindow.observation_window == observation_window,
            )
        ).one_or_none()

    def require_window(self, uploaded_video_id: uuid.UUID, observation_window: str) -> PostPublishObservationWindow:
        self.create_windows_for_uploaded_video(uploaded_video_id)
        window = self.get_window(uploaded_video_id, observation_window)
        if window is None:
            raise ValidationFailureError(f"observation window is not deterministic for M9: {observation_window}")
        return window

    def is_ready(self, window: PostPublishObservationWindow) -> bool:
        if window.state == "COMPLETED":
            return True
        if window.state in {"BLOCKED", "SKIPPED"}:
            return False
        ready = utc_now() >= window.expected_check_at
        window.state = "READY" if ready else "PENDING"
        if not ready:
            window.reason_codes = _dedupe([*window.reason_codes, "OBSERVATION_WINDOW_NOT_READY"])
        self.session.flush()
        return ready


class PostPublishHealthMonitorService:
    def __init__(self, session: Session):
        self.session = session

    def seed_taxonomy_versions(self) -> list[DiagnosticTaxonomyVersion]:
        records: list[DiagnosticTaxonomyVersion] = []
        for code, friendly_label in DIAGNOSTIC_TAXONOMY.items():
            existing = self.session.scalars(
                select(DiagnosticTaxonomyVersion).where(
                    DiagnosticTaxonomyVersion.taxonomy_key == code,
                    DiagnosticTaxonomyVersion.version == TAXONOMY_VERSION,
                )
            ).one_or_none()
            if existing is None:
                existing = DiagnosticTaxonomyVersion(
                    taxonomy_key=code,
                    version=TAXONOMY_VERSION,
                    taxonomy_blob={
                        "code": code,
                        "friendly_label": friendly_label,
                        "operator_default": True,
                        "technical_code_available": True,
                    },
                    status="ACTIVE",
                )
                self.session.add(existing)
                self.session.flush()
            records.append(existing)
        return records

    def create_health_run(
        self,
        *,
        data: PostPublishHealthRunCreate,
        correlation_id: str = "m9-post-publish-health-create",
    ) -> PostPublishHealthRun:
        self.seed_taxonomy_versions()
        context = _load_context(self.session, data.uploaded_video_id, data.observation_window)
        window = ObservationWindowService(self.session).require_window(context.uploaded.id, data.observation_window)
        evidence_refs = _evidence_refs(context)
        run = PostPublishHealthRun(
            uploaded_video_id=context.uploaded.id,
            company_id=context.uploaded.company_id,
            channel_workspace_id=context.uploaded.channel_workspace_id,
            video_project_id=context.uploaded.video_project_id,
            policy_snapshot_id=context.uploaded.policy_snapshot_id,
            platform=context.uploaded.platform,
            platform_video_id=context.uploaded.platform_video_id,
            observation_window=data.observation_window,
            analytics_snapshot_id=context.analytics_snapshot.id if context.analytics_snapshot else None,
            uploaded_video_metrics_summary_id=context.metrics_summary.id if context.metrics_summary else None,
            retention_curve_snapshot_id=context.retention_snapshot.id if context.retention_snapshot else None,
            traffic_source_snapshot_id=context.traffic_snapshot.id if context.traffic_snapshot else None,
            engagement_snapshot_id=context.engagement_snapshot.id if context.engagement_snapshot else None,
            run_state="PENDING",
            health_state="UNKNOWN",
            severity="INFO",
            confidence_level="UNKNOWN",
            evidence_refs=evidence_refs,
            reason_codes=["POST_PUBLISH_HEALTH_RUN_CREATED"],
            operator_summary="Đã tạo lượt chẩn đoán sau publish. Chưa có hành động tự động nào.",
            next_action="Execute this health run when the observation window is ready.",
            do_not_do=DO_NOT_DO_DEFAULT,
            technical_appendix={
                "observation_window_id": str(window.id),
                "m9_diagnostic_only": True,
                "no_analytics_sync": True,
                "no_platform_api_call": True,
            },
        )
        self.session.add(run)
        self.session.flush()
        _record_m9_event(
            self.session,
            event_type="post_publish_health_run.created",
            aggregate_type="post_publish_health_run",
            aggregate_id=run.id,
            target_type="post_publish_health_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="POST_PUBLISH_HEALTH_RUN_CREATED",
            payload={
                "uploaded_video_id": str(run.uploaded_video_id),
                "observation_window": run.observation_window,
                "analytics_snapshot_id": str(run.analytics_snapshot_id) if run.analytics_snapshot_id else None,
                "m9_diagnostic_only": True,
                "no_analytics_sync": True,
                "no_platform_api_call": True,
            },
        )
        return run

    def execute_health_run(
        self,
        *,
        run_id: uuid.UUID,
        correlation_id: str = "m9-post-publish-health-execute",
    ) -> PostPublishHealthRun:
        run = self.require_health_run(run_id)
        if run.run_state in {"COMPLETED", "BLOCKED", "INSUFFICIENT_DATA", "FAILED"}:
            return run
        context = _load_context(self.session, run.uploaded_video_id, run.observation_window)
        window = ObservationWindowService(self.session).require_window(context.uploaded.id, run.observation_window)
        if not ObservationWindowService(self.session).is_ready(window):
            aggregate = AggregateResult(
                run_state="INSUFFICIENT_DATA",
                health_state="INSUFFICIENT_DATA",
                severity="INFO",
                confidence_level="LOW",
                primary_status="INSUFFICIENT_DATA",
                primary_suspected_cause="INSUFFICIENT_DATA",
                secondary_suspected_causes=[],
                operator_summary="Chưa đến mốc quan sát nên chưa đủ dữ liệu để kết luận.",
                next_action=f"Chờ đến {window.expected_check_at.isoformat()} rồi chạy lại chẩn đoán.",
                reason_codes=["OBSERVATION_WINDOW_NOT_READY", "INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"],
                evidence_plain_text=[
                    f"Observation window {run.observation_window} chưa sẵn sàng.",
                    "M9 không sync analytics và không tự gọi platform.",
                ],
                proposal_type="WAIT_AND_MONITOR",
                recommended_actions=["wait until next observation window"],
                risk_level="LOW",
            )
            self._finish_run_from_aggregate(run=run, context=context, aggregate=aggregate, correlation_id=correlation_id)
            window.state = "PENDING"
            self.session.flush()
            return run

        diagnostics = {
            "no_view": NoViewDiagnosticService(self.session).run(run=run, context=context, correlation_id=correlation_id),
            "packaging": PackagingDiagnosticService(self.session).run(run=run, context=context, correlation_id=correlation_id),
            "retention": RetentionDiagnosticService(self.session).run(run=run, context=context, correlation_id=correlation_id),
            "engagement": EngagementDiagnosticService(self.session).run(run=run, context=context, correlation_id=correlation_id),
            "policy_rights": PolicyRightsDiagnosticService(self.session).run(run=run, context=context, correlation_id=correlation_id),
        }
        aggregate = _aggregate_diagnostics(context=context, diagnostics=diagnostics)
        self._finish_run_from_aggregate(run=run, context=context, aggregate=aggregate, correlation_id=correlation_id)
        window.state = "COMPLETED"
        window.reason_codes = _dedupe([*window.reason_codes, "POST_PUBLISH_HEALTH_COMPLETED"])
        self.session.flush()
        return run

    def _finish_run_from_aggregate(
        self,
        *,
        run: PostPublishHealthRun,
        context: DiagnosticContext,
        aggregate: AggregateResult,
        correlation_id: str,
    ) -> None:
        run.run_state = aggregate.run_state
        run.health_state = aggregate.health_state
        run.severity = aggregate.severity
        run.confidence_level = aggregate.confidence_level
        run.evidence_refs = _evidence_refs(context)
        run.reason_codes = _dedupe([*run.reason_codes, *aggregate.reason_codes])
        if aggregate.run_state == "COMPLETED":
            run.reason_codes = _dedupe([*run.reason_codes, "POST_PUBLISH_HEALTH_COMPLETED"])
        elif aggregate.run_state == "INSUFFICIENT_DATA":
            run.reason_codes = _dedupe([*run.reason_codes, "INSUFFICIENT_ANALYTICS_DATA"])
        run.operator_summary = aggregate.operator_summary
        run.next_action = aggregate.next_action
        run.do_not_do = DO_NOT_DO_DEFAULT
        run.technical_appendix = {
            "primary_suspected_cause": aggregate.primary_suspected_cause,
            "secondary_suspected_causes": aggregate.secondary_suspected_causes,
            "reason_codes": run.reason_codes,
            "metric_values": _metric_values_for_appendix(context),
            "lineage_refs": _lineage_refs(context.uploaded),
            "evidence_refs": run.evidence_refs,
            "m9_diagnostic_only": True,
            "no_analytics_sync": True,
            "no_platform_api_call": True,
            "confidence_is_not_severity": True,
        }
        self.session.flush()
        report = self._create_failure_trace_report(run=run, context=context, aggregate=aggregate, correlation_id=correlation_id)
        proposal = self._create_recovery_proposal(
            report=report,
            context=context,
            aggregate=aggregate,
            correlation_id=correlation_id,
        )
        self._maybe_create_manual_action(
            proposal=proposal,
            report=report,
            aggregate=aggregate,
            correlation_id=correlation_id,
        )
        event_type = "post_publish_health_run.completed" if run.run_state == "COMPLETED" else "post_publish_health_run.insufficient_data"
        _record_m9_event(
            self.session,
            event_type=event_type,
            aggregate_type="post_publish_health_run",
            aggregate_id=run.id,
            target_type="post_publish_health_run",
            target_id=run.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="POST_PUBLISH_HEALTH_COMPLETED" if run.run_state == "COMPLETED" else "INSUFFICIENT_ANALYTICS_DATA",
            payload={
                "uploaded_video_id": str(run.uploaded_video_id),
                "health_state": run.health_state,
                "run_state": run.run_state,
                "failure_trace_report_id": str(report.id),
                "recovery_proposal_id": str(proposal.id),
            },
        )

    def _create_failure_trace_report(
        self,
        *,
        run: PostPublishHealthRun,
        context: DiagnosticContext,
        aggregate: AggregateResult,
        correlation_id: str,
    ) -> FailureTraceReport:
        report = FailureTraceReport(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            video_project_id=run.video_project_id,
            platform=run.platform,
            platform_video_id=run.platform_video_id,
            observation_window=run.observation_window,
            primary_status=aggregate.primary_status,
            primary_suspected_cause=aggregate.primary_suspected_cause,
            secondary_suspected_causes=aggregate.secondary_suspected_causes,
            confidence_level=aggregate.confidence_level,
            severity=aggregate.severity,
            evidence_plain_text=aggregate.evidence_plain_text,
            operator_summary=aggregate.operator_summary,
            operator_report=_operator_report(run=run, aggregate=aggregate),
            next_action=aggregate.next_action,
            do_not_do=DO_NOT_DO_DEFAULT,
            technical_appendix={
                "internal_reason_codes": run.reason_codes,
                "metric_values": _metric_values_for_appendix(context),
                "snapshot_refs": _snapshot_refs(context),
                "lineage_refs": _lineage_refs(context.uploaded),
                "diagnostic_taxonomy_version": TAXONOMY_VERSION,
                "m9_diagnostic_only": True,
            },
        )
        self.session.add(report)
        self.session.flush()
        _record_m9_event(
            self.session,
            event_type="failure_trace_report.created",
            aggregate_type="failure_trace_report",
            aggregate_id=report.id,
            target_type="failure_trace_report",
            target_id=report.id,
            company_id=run.company_id,
            correlation_id=correlation_id,
            reason_code="FAILURE_TRACE_REPORT_CREATED",
            payload={
                "post_publish_health_run_id": str(run.id),
                "uploaded_video_id": str(run.uploaded_video_id),
                "primary_status": report.primary_status,
                "primary_suspected_cause": report.primary_suspected_cause,
            },
        )
        return report

    def _create_recovery_proposal(
        self,
        *,
        report: FailureTraceReport,
        context: DiagnosticContext,
        aggregate: AggregateResult,
        correlation_id: str,
    ) -> RecoveryProposal:
        recommended_actions = _sanitize_recommended_actions(aggregate.recommended_actions)
        proposal = RecoveryProposal(
            failure_trace_report_id=report.id,
            uploaded_video_id=report.uploaded_video_id,
            video_project_id=report.video_project_id,
            proposal_type=aggregate.proposal_type,
            proposal_state="PROPOSED",
            operator_summary=_proposal_summary(aggregate.proposal_type),
            recommended_actions=recommended_actions,
            do_not_do=DO_NOT_DO_DEFAULT,
            evidence_refs=_evidence_refs(context),
            risk_level=aggregate.risk_level,
            requires_human_approval=True,
        )
        self.session.add(proposal)
        self.session.flush()
        _record_m9_event(
            self.session,
            event_type="recovery_proposal.created",
            aggregate_type="recovery_proposal",
            aggregate_id=proposal.id,
            target_type="recovery_proposal",
            target_id=proposal.id,
            company_id=context.uploaded.company_id,
            correlation_id=correlation_id,
            reason_code="RECOVERY_PROPOSAL_CREATED",
            payload={
                "failure_trace_report_id": str(report.id),
                "uploaded_video_id": str(proposal.uploaded_video_id),
                "proposal_type": proposal.proposal_type,
                "requires_human_approval": True,
            },
        )
        return proposal

    def _maybe_create_manual_action(
        self,
        *,
        proposal: RecoveryProposal,
        report: FailureTraceReport,
        aggregate: AggregateResult,
        correlation_id: str,
    ) -> ManualAction | None:
        action_type_by_proposal = {
            "WAIT_AND_MONITOR": "WAIT_FOR_NEXT_WINDOW",
            "REVIEW_TITLE_THUMBNAIL": "REVIEW_TITLE_THUMBNAIL_VARIANT",
            "REVIEW_HOOK": "REVIEW_RETENTION_DROP_SECTION",
            "REVIEW_RETENTION_SECTION": "REVIEW_RETENTION_DROP_SECTION",
            "REVIEW_RIGHTS_DISCLOSURE": "REVIEW_DISCLOSURE",
            "REVIEW_SOURCE_QUALITY": "REVIEW_RIGHTS_EVIDENCE",
            "CREATE_FUTURE_VARIANT": "OTHER",
        }
        action_type = action_type_by_proposal.get(proposal.proposal_type)
        if action_type is None or proposal.proposal_type == "NO_ACTION":
            return None
        action = ManualActionService(self.session).create_action(
            data=ManualActionCreate(
                action_type=action_type,  # type: ignore[arg-type]
                target_type="failure_trace_report",
                target_id=report.id,
                priority=_priority_from_severity(aggregate.severity),
                reason_code=aggregate.reason_codes[0] if aggregate.reason_codes else "RECOVERY_PROPOSAL_CREATED",
                next_action=aggregate.next_action or "Review the post-publish diagnostic report.",
                due_at=None,
            ),
            correlation_id="m9-manual-action-from-diagnostic",
        )
        _record_m9_event(
            self.session,
            event_type="manual_action.created_from_post_publish_diagnostic",
            aggregate_type="manual_action",
            aggregate_id=action.id,
            target_type="manual_action",
            target_id=action.id,
            company_id=None,
            correlation_id=correlation_id,
            reason_code="RECOVERY_PROPOSAL_CREATED",
            payload={
                "failure_trace_report_id": str(report.id),
                "recovery_proposal_id": str(proposal.id),
                "action_type": action.action_type,
                "no_automatic_external_action": True,
            },
        )
        return action

    def get_health_run(self, run_id: uuid.UUID) -> PostPublishHealthRun | None:
        return self.session.get(PostPublishHealthRun, run_id)

    def require_health_run(self, run_id: uuid.UUID) -> PostPublishHealthRun:
        run = self.get_health_run(run_id)
        if run is None:
            raise NotFoundError(f"post-publish health run not found: {run_id}")
        return run

    def list_health_runs_by_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[PostPublishHealthRun]:
        _require_uploaded(self.session, uploaded_video_id)
        return list(
            self.session.scalars(
                select(PostPublishHealthRun)
                .where(PostPublishHealthRun.uploaded_video_id == uploaded_video_id)
                .order_by(PostPublishHealthRun.created_at.desc())
            ).all()
        )

    def list_failure_trace_reports_by_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[FailureTraceReport]:
        _require_uploaded(self.session, uploaded_video_id)
        return list(
            self.session.scalars(
                select(FailureTraceReport)
                .where(FailureTraceReport.uploaded_video_id == uploaded_video_id)
                .order_by(FailureTraceReport.created_at.desc())
            ).all()
        )

    def require_failure_trace_report(self, report_id: uuid.UUID) -> FailureTraceReport:
        report = self.session.get(FailureTraceReport, report_id)
        if report is None:
            raise NotFoundError(f"failure trace report not found: {report_id}")
        return report

    def list_recovery_proposals_by_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[RecoveryProposal]:
        _require_uploaded(self.session, uploaded_video_id)
        return list(
            self.session.scalars(
                select(RecoveryProposal)
                .where(RecoveryProposal.uploaded_video_id == uploaded_video_id)
                .order_by(RecoveryProposal.created_at.desc())
            ).all()
        )

    def require_recovery_proposal(self, proposal_id: uuid.UUID) -> RecoveryProposal:
        proposal = self.session.get(RecoveryProposal, proposal_id)
        if proposal is None:
            raise NotFoundError(f"recovery proposal not found: {proposal_id}")
        return proposal

    def accept_recovery_proposal(
        self,
        *,
        proposal_id: uuid.UUID,
        correlation_id: str = "m9-recovery-proposal-accept",
    ) -> RecoveryProposal:
        proposal = self.require_recovery_proposal(proposal_id)
        if proposal.proposal_state == "PROPOSED":
            proposal.proposal_state = "ACCEPTED"
            self.session.flush()
            _record_m9_event(
                self.session,
                event_type="recovery_proposal.accepted",
                aggregate_type="recovery_proposal",
                aggregate_id=proposal.id,
                target_type="recovery_proposal",
                target_id=proposal.id,
                company_id=None,
                correlation_id=correlation_id,
                reason_code="RECOVERY_PROPOSAL_CREATED",
                payload={"proposal_state": proposal.proposal_state, "no_automatic_action": True},
            )
        return proposal

    def reject_recovery_proposal(
        self,
        *,
        proposal_id: uuid.UUID,
        correlation_id: str = "m9-recovery-proposal-reject",
    ) -> RecoveryProposal:
        proposal = self.require_recovery_proposal(proposal_id)
        if proposal.proposal_state == "PROPOSED":
            proposal.proposal_state = "REJECTED"
            self.session.flush()
            _record_m9_event(
                self.session,
                event_type="recovery_proposal.rejected",
                aggregate_type="recovery_proposal",
                aggregate_id=proposal.id,
                target_type="recovery_proposal",
                target_id=proposal.id,
                company_id=None,
                correlation_id=correlation_id,
                reason_code="RECOVERY_PROPOSAL_CREATED",
                payload={"proposal_state": proposal.proposal_state, "no_automatic_action": True},
            )
        return proposal


class NoViewDiagnosticService:
    def __init__(self, session: Session):
        self.session = session

    def run(
        self,
        *,
        run: PostPublishHealthRun,
        context: DiagnosticContext,
        correlation_id: str,
    ) -> NoViewDiagnosticRun:
        views = _metric(context, "views")
        impressions = _metric(context, "impressions")
        min_impressions = WINDOW_MIN_IMPRESSIONS.get(run.observation_window, WINDOW_MIN_IMPRESSIONS["CUSTOM"])
        if context.analytics_snapshot is None:
            state = "DATA_UNAVAILABLE"
            reasons = ["ANALYTICS_DATA_UNAVAILABLE", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Chưa có analytics snapshot nên chưa thể kết luận no-view."
            next_action = "Import analytics từ M8 trước, rồi chạy lại M9."
            confidence = "LOW"
        elif views.availability_state != "AVAILABLE":
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Views chưa khả dụng nên chưa đủ dữ liệu để kết luận."
            next_action = "Chờ hoặc import snapshot có metric views."
            confidence = "LOW"
        elif impressions.availability_state != "AVAILABLE":
            state = "DATA_UNAVAILABLE" if views.value is not None and views.value <= 0 else "INSUFFICIENT_DATA"
            reasons = ["ANALYTICS_DATA_UNAVAILABLE", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Views thấp nhưng impressions chưa khả dụng, nên không đổ lỗi cho nội dung."
            next_action = "Chờ mốc quan sát tiếp theo hoặc import impressions nếu platform cung cấp."
            confidence = "LOW"
        elif (impressions.value or 0) < min_impressions:
            state = "LOW_IMPRESSIONS"
            reasons = ["LOW_IMPRESSIONS_DETECTED", "WAIT_AND_MONITOR_RECOMMENDED"]
            summary = "Video đang có ít impressions; nguyên nhân có thể là phân phối ban đầu hoặc nhu cầu chủ đề chưa rõ."
            next_action = "Chờ mốc quan sát tiếp theo; không re-upload."
            confidence = "MEDIUM"
        elif (views.value or 0) <= 0:
            state = "NO_VIEW_RISK"
            reasons = ["NO_VIEW_RISK_DETECTED", "REVIEW_TITLE_THUMBNAIL_RECOMMENDED"]
            summary = "Impressions đủ mẫu nhưng views bằng 0; cần xem xét packaging bằng human review."
            next_action = "Chuẩn bị biến thể title/thumbnail để human review nếu CTR vẫn thấp."
            confidence = "MEDIUM"
        else:
            state = "HEALTHY"
            reasons = ["POST_PUBLISH_HEALTH_COMPLETED"]
            summary = "Views và impressions đủ để không xếp video vào nhóm no-view."
            next_action = "Tiếp tục theo dõi ở mốc quan sát kế tiếp."
            confidence = "HIGH"
        record = NoViewDiagnosticRun(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            analytics_snapshot_id=context.analytics_snapshot.id if context.analytics_snapshot else None,
            uploaded_video_metrics_summary_id=context.metrics_summary.id if context.metrics_summary else None,
            observation_window=run.observation_window,
            diagnostic_state=state,
            views=views.value,
            impressions=impressions.value,
            metric_availability={
                "views": views.availability_state,
                "impressions": impressions.availability_state,
                "zero_is_available": True,
                "missing_is_not_zero": True,
            },
            evidence_blob={"min_impressions": min_impressions, "views_ref": views.source_ref, "impressions_ref": impressions.source_ref},
            confidence_level=confidence,
            reason_codes=reasons,
            operator_summary=summary,
            next_action=next_action,
        )
        self.session.add(record)
        self.session.flush()
        _diagnostic_event(self.session, "no_view_diagnostic.created", record.id, run, correlation_id, reasons[0])
        return record


class PackagingDiagnosticService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, run: PostPublishHealthRun, context: DiagnosticContext, correlation_id: str) -> PackagingDiagnosticRun:
        impressions = _metric(context, "impressions")
        ctr = _metric(context, "click_through_rate")
        views = _metric(context, "views")
        if context.analytics_snapshot is None:
            state = "INSUFFICIENT_DATA"
            reasons = ["ANALYTICS_DATA_UNAVAILABLE", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Chưa có analytics snapshot nên chưa thể đánh giá CTR."
            next_action = "Import analytics từ M8 trước."
            confidence = "LOW"
        elif impressions.availability_state != "AVAILABLE" or (impressions.value or 0) < MIN_IMPRESSIONS_FOR_CTR:
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Impressions chưa đủ mẫu để kết luận title/thumbnail."
            next_action = "Chờ thêm dữ liệu trước khi review packaging."
            confidence = "LOW"
        elif ctr.availability_state != "AVAILABLE":
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "CTR chưa khả dụng dù impressions đủ mẫu."
            next_action = "Import snapshot có CTR nếu platform cung cấp."
            confidence = "LOW"
        elif (ctr.value or 0) < LOW_CTR_PERCENT:
            state = "LOW_CTR"
            reasons = ["LOW_CTR_DETECTED", "REVIEW_TITLE_THUMBNAIL_RECOMMENDED"]
            summary = "Tiêu đề/thumbnail có thể chưa kéo click, vì impressions đủ nhưng CTR thấp."
            next_action = "Human review title/thumbnail; không dùng clickbait và không tự đổi trên platform."
            confidence = "MEDIUM"
        else:
            state = "HEALTHY"
            reasons = ["POST_PUBLISH_HEALTH_COMPLETED"]
            summary = "CTR đủ ổn trong mốc quan sát hiện tại."
            next_action = "Tiếp tục theo dõi."
            confidence = "HIGH"
        record = PackagingDiagnosticRun(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            analytics_snapshot_id=context.analytics_snapshot.id if context.analytics_snapshot else None,
            observation_window=run.observation_window,
            diagnostic_state=state,
            impressions=impressions.value,
            click_through_rate=ctr.value,
            views=views.value,
            evidence_blob={"min_impressions_for_ctr": MIN_IMPRESSIONS_FOR_CTR, "low_ctr_percent": LOW_CTR_PERCENT},
            confidence_level=confidence,
            reason_codes=reasons,
            operator_summary=summary,
            next_action=next_action,
        )
        self.session.add(record)
        self.session.flush()
        _diagnostic_event(self.session, "packaging_diagnostic.created", record.id, run, correlation_id, reasons[0])
        return record


class RetentionDiagnosticService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, run: PostPublishHealthRun, context: DiagnosticContext, correlation_id: str) -> RetentionDiagnosticRun:
        views = _metric(context, "views")
        avd = _metric(context, "average_view_duration_seconds")
        avp = _metric(context, "average_view_percentage")
        curve = context.retention_snapshot.curve_points if context.retention_snapshot else []
        drop = _detect_retention_drop(context.retention_snapshot) if context.retention_snapshot else None
        if context.analytics_snapshot is None:
            state = "INSUFFICIENT_DATA"
            reasons = ["ANALYTICS_DATA_UNAVAILABLE", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Chưa có analytics snapshot nên chưa thể đánh giá retention."
            next_action = "Import analytics từ M8 trước."
            confidence = "LOW"
            scene_alignment: list[dict[str, Any]] = []
        elif views.availability_state == "AVAILABLE" and (views.value or 0) < MIN_VIEWS_FOR_RETENTION:
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Mẫu views còn nhỏ, chưa nên kết luận retention."
            next_action = "Chờ thêm views trước khi ghi nhận hook hoặc pacing issue."
            confidence = "LOW"
            scene_alignment = []
        elif drop is not None and drop["state"] == "EARLY_DROP":
            state = "EARLY_DROP"
            reasons = ["EARLY_RETENTION_DROP_DETECTED", "REVIEW_HOOK_RECOMMENDED"]
            summary = "Người xem rời sớm ở đoạn mở đầu."
            next_action = "Review hook section và ghi edit note cho phiên bản tương lai."
            confidence = "MEDIUM"
            scene_alignment = _align_drop_to_scene(context.retention_snapshot, drop["time_seconds"])
        elif drop is not None and drop["state"] == "MID_VIDEO_DROP":
            state = "MID_VIDEO_DROP"
            reasons = ["MID_VIDEO_RETENTION_DROP_DETECTED", "REVIEW_RETENTION_SECTION_RECOMMENDED"]
            summary = "Retention tụt rõ ở phần giữa video."
            next_action = f"Review đoạn quanh {int(drop['time_seconds'])}s và ghi edit note cho phiên bản tương lai."
            confidence = "MEDIUM"
            scene_alignment = _align_drop_to_scene(context.retention_snapshot, drop["time_seconds"])
        elif curve or avd.availability_state == "AVAILABLE" or avp.availability_state == "AVAILABLE":
            state = "HEALTHY"
            reasons = ["POST_PUBLISH_HEALTH_COMPLETED"]
            summary = "Retention chưa có dấu hiệu tụt mạnh trong dữ liệu hiện có."
            next_action = "Tiếp tục theo dõi."
            confidence = "MEDIUM" if not curve else "HIGH"
            scene_alignment = []
        else:
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Chưa có retention curve hoặc AVD để đánh giá."
            next_action = "Import retention curve hoặc AVD khi có."
            confidence = "LOW"
            scene_alignment = []
        record = RetentionDiagnosticRun(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            analytics_snapshot_id=context.analytics_snapshot.id if context.analytics_snapshot else None,
            retention_curve_snapshot_id=context.retention_snapshot.id if context.retention_snapshot else None,
            observation_window=run.observation_window,
            diagnostic_state=state,
            average_view_duration_seconds=avd.value,
            average_view_percentage=avp.value,
            evidence_blob={"curve_point_count": len(curve), "min_views_for_retention": MIN_VIEWS_FOR_RETENTION, "drop": drop},
            scene_alignment=scene_alignment,
            confidence_level=confidence,
            reason_codes=reasons,
            operator_summary=summary,
            next_action=next_action,
        )
        self.session.add(record)
        self.session.flush()
        _diagnostic_event(self.session, "retention_diagnostic.created", record.id, run, correlation_id, reasons[0])
        return record


class EngagementDiagnosticService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, run: PostPublishHealthRun, context: DiagnosticContext, correlation_id: str) -> EngagementDiagnosticRun:
        views = _metric(context, "views")
        rate = _metric(context, "engagement_rate")
        metrics = _engagement_metrics(context)
        if context.analytics_snapshot is None:
            state = "INSUFFICIENT_DATA"
            reasons = ["ANALYTICS_DATA_UNAVAILABLE", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Chưa có analytics snapshot nên chưa thể đánh giá engagement."
            next_action = "Import analytics từ M8 trước."
            confidence = "LOW"
        elif views.availability_state != "AVAILABLE" or (views.value or 0) < MIN_VIEWS_FOR_ENGAGEMENT:
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Mẫu views còn nhỏ, chưa nên kết luận audience fit hay engagement."
            next_action = "Chờ thêm dữ liệu."
            confidence = "LOW"
        elif rate.availability_state != "AVAILABLE":
            state = "INSUFFICIENT_DATA"
            reasons = ["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA"]
            summary = "Engagement rate chưa khả dụng."
            next_action = "Import engagement metrics nếu platform cung cấp."
            confidence = "LOW"
        elif (rate.value or 0) < LOW_ENGAGEMENT_RATE:
            state = "LOW_ENGAGEMENT"
            reasons = ["LOW_ENGAGEMENT_DETECTED"]
            summary = "Engagement thấp trên lượng views đủ mẫu."
            next_action = "Human review câu hỏi/kêu gọi hành động cho phiên bản tương lai; không dùng engagement manipulation."
            confidence = "MEDIUM"
        else:
            state = "HEALTHY"
            reasons = ["POST_PUBLISH_HEALTH_COMPLETED"]
            summary = "Engagement đủ ổn trong dữ liệu hiện có."
            next_action = "Tiếp tục theo dõi."
            confidence = "HIGH"
        record = EngagementDiagnosticRun(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            analytics_snapshot_id=context.analytics_snapshot.id if context.analytics_snapshot else None,
            engagement_snapshot_id=context.engagement_snapshot.id if context.engagement_snapshot else None,
            observation_window=run.observation_window,
            diagnostic_state=state,
            engagement_metrics=metrics,
            evidence_blob={"min_views_for_engagement": MIN_VIEWS_FOR_ENGAGEMENT, "low_engagement_rate": LOW_ENGAGEMENT_RATE},
            confidence_level=confidence,
            reason_codes=reasons,
            operator_summary=summary,
            next_action=next_action,
        )
        self.session.add(record)
        self.session.flush()
        _diagnostic_event(self.session, "engagement_diagnostic.created", record.id, run, correlation_id, reasons[0])
        return record


class PolicyRightsDiagnosticService:
    def __init__(self, session: Session):
        self.session = session

    def run(self, *, run: PostPublishHealthRun, context: DiagnosticContext, correlation_id: str) -> PolicyRightsDiagnosticRun:
        disclosures = dict(context.uploaded.actual_disclosures or {})
        reasons: list[str] = []
        if "ai_disclosure_confirmed" not in disclosures or disclosures.get("ai_disclosure_confirmed") is None:
            reasons.append("DISCLOSURE_REVIEW_REQUIRED")
        if disclosures.get("rights_confirmed") is not True:
            reasons.append("RIGHTS_REVIEW_REQUIRED")
        if disclosures.get("music_license_confirmed") is False or disclosures.get("stock_license_confirmed") is False:
            reasons.append("POLICY_REVIEW_REQUIRED")
        if reasons:
            state = "REVIEW_REQUIRED"
            summary = "Cần kiểm tra quyền, nhạc, disclosure hoặc policy dựa trên dữ liệu M7."
            next_action = "Human review disclosure/license confirmation; không tự sửa hoặc takedown trên platform."
            confidence = "HIGH"
            reasons = _dedupe([*reasons, "POLICY_RIGHTS_REVIEW_REQUIRED"])
        else:
            state = "PASS"
            summary = "Disclosure và rights confirmation trong M7 đã có đủ cho M9."
            next_action = None
            confidence = "HIGH"
            reasons = ["POST_PUBLISH_HEALTH_COMPLETED"]
        record = PolicyRightsDiagnosticRun(
            post_publish_health_run_id=run.id,
            uploaded_video_id=run.uploaded_video_id,
            observation_window=run.observation_window,
            diagnostic_state=state,
            source_manifest_snapshot_id=context.uploaded.source_manifest_snapshot_id,
            rights_envelope_ref=context.uploaded.rights_envelope_ref,
            actual_disclosures=disclosures,
            evidence_blob={
                "manual_publish_confirmation_id": str(context.uploaded.manual_publish_confirmation_id),
                "publish_handoff_package_id": str(context.uploaded.publish_handoff_package_id),
                "no_platform_scraping": True,
            },
            confidence_level=confidence,
            reason_codes=reasons,
            operator_summary=summary,
            next_action=next_action,
        )
        self.session.add(record)
        self.session.flush()
        _diagnostic_event(self.session, "policy_rights_diagnostic.created", record.id, run, correlation_id, reasons[0])
        return record


def _load_context(session: Session, uploaded_video_id: uuid.UUID, observation_window: str) -> DiagnosticContext:
    uploaded = _require_uploaded(session, uploaded_video_id)
    summary = session.scalars(
        select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
    ).one_or_none()
    analytics = session.get(AnalyticsSnapshot, summary.latest_analytics_snapshot_id) if summary and summary.latest_analytics_snapshot_id else None
    if analytics is None:
        analytics = session.scalars(
            select(AnalyticsSnapshot)
            .where(AnalyticsSnapshot.uploaded_video_id == uploaded.id)
            .order_by(AnalyticsSnapshot.captured_at.desc(), AnalyticsSnapshot.created_at.desc())
        ).first()
    retention = session.get(RetentionCurveSnapshot, summary.latest_retention_curve_snapshot_id) if summary and summary.latest_retention_curve_snapshot_id else None
    traffic = session.get(TrafficSourceSnapshot, summary.latest_traffic_source_snapshot_id) if summary and summary.latest_traffic_source_snapshot_id else None
    engagement = session.get(EngagementSnapshot, summary.latest_engagement_snapshot_id) if summary and summary.latest_engagement_snapshot_id else None
    if analytics is not None:
        if retention is None:
            retention = session.scalars(
                select(RetentionCurveSnapshot)
                .where(RetentionCurveSnapshot.analytics_snapshot_id == analytics.id)
                .order_by(RetentionCurveSnapshot.created_at.desc())
            ).first()
        if traffic is None:
            traffic = session.scalars(
                select(TrafficSourceSnapshot)
                .where(TrafficSourceSnapshot.analytics_snapshot_id == analytics.id)
                .order_by(TrafficSourceSnapshot.created_at.desc())
            ).first()
        if engagement is None:
            engagement = session.scalars(
                select(EngagementSnapshot)
                .where(EngagementSnapshot.analytics_snapshot_id == analytics.id)
                .order_by(EngagementSnapshot.created_at.desc())
            ).first()
    window = ObservationWindowService(session).get_window(uploaded.id, observation_window)
    return DiagnosticContext(
        uploaded=uploaded,
        observation_window=observation_window,
        window=window,
        analytics_snapshot=analytics,
        metrics_summary=summary,
        retention_snapshot=retention,
        traffic_snapshot=traffic,
        engagement_snapshot=engagement,
    )


def _aggregate_diagnostics(context: DiagnosticContext, diagnostics: dict[str, Any]) -> AggregateResult:
    policy = diagnostics["policy_rights"]
    no_view = diagnostics["no_view"]
    packaging = diagnostics["packaging"]
    retention = diagnostics["retention"]
    engagement = diagnostics["engagement"]
    evidence = _evidence_plain_text(context, diagnostics)
    secondary: list[str] = []
    if policy.diagnostic_state == "REVIEW_REQUIRED":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="POLICY_REVIEW_REQUIRED",
            severity="HIGH",
            confidence_level="HIGH",
            primary_status="POLICY_REVIEW_REQUIRED",
            primary_suspected_cause="POLICY_RIGHTS_REVIEW_REQUIRED",
            secondary_suspected_causes=[],
            operator_summary=policy.operator_summary,
            next_action=policy.next_action,
            reason_codes=_dedupe([*policy.reason_codes, "RECOVERY_PROPOSAL_CREATED"]),
            evidence_plain_text=evidence,
            proposal_type="REVIEW_RIGHTS_DISCLOSURE",
            recommended_actions=["check disclosure/license confirmation"],
            risk_level="HIGH",
        )
    if context.analytics_snapshot is None or any(
        item.diagnostic_state in {"DATA_UNAVAILABLE", "INSUFFICIENT_DATA"}
        for item in [no_view, packaging, retention, engagement]
    ) and no_view.diagnostic_state in {"DATA_UNAVAILABLE", "INSUFFICIENT_DATA"}:
        return AggregateResult(
            run_state="INSUFFICIENT_DATA",
            health_state="INSUFFICIENT_DATA",
            severity="INFO",
            confidence_level="LOW",
            primary_status="INSUFFICIENT_DATA",
            primary_suspected_cause="INSUFFICIENT_DATA",
            secondary_suspected_causes=[],
            operator_summary="Video đang có ít dữ liệu hơn cần thiết; chưa đủ để kết luận.",
            next_action="Chờ mốc quan sát tiếp theo hoặc import thêm analytics từ M8.",
            reason_codes=["INSUFFICIENT_ANALYTICS_DATA", "NO_DIAGNOSIS_WITHOUT_DATA", "WAIT_AND_MONITOR_RECOMMENDED"],
            evidence_plain_text=evidence,
            proposal_type="WAIT_AND_MONITOR",
            recommended_actions=["wait until next observation window"],
            risk_level="LOW",
        )
    if no_view.diagnostic_state == "LOW_IMPRESSIONS":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="NO_VIEW_RISK",
            severity="MEDIUM",
            confidence_level=no_view.confidence_level,
            primary_status="NO_VIEW_RISK",
            primary_suspected_cause="DISTRIBUTION_UNCERTAIN",
            secondary_suspected_causes=["TOPIC_DEMAND_UNCERTAIN"],
            operator_summary=no_view.operator_summary,
            next_action=no_view.next_action,
            reason_codes=_dedupe([*no_view.reason_codes, "NO_REUPLOAD_RECOMMENDED"]),
            evidence_plain_text=evidence,
            proposal_type="WAIT_AND_MONITOR",
            recommended_actions=["wait until next observation window"],
            risk_level="LOW",
        )
    if no_view.diagnostic_state == "NO_VIEW_RISK":
        secondary.append("PACKAGING_FAILURE")
        return AggregateResult(
            run_state="COMPLETED",
            health_state="NO_VIEW_RISK",
            severity="MEDIUM",
            confidence_level=no_view.confidence_level,
            primary_status="NO_VIEW_RISK",
            primary_suspected_cause="NO_VIEW_RISK",
            secondary_suspected_causes=secondary,
            operator_summary=no_view.operator_summary,
            next_action=no_view.next_action,
            reason_codes=_dedupe([*no_view.reason_codes, "NO_REUPLOAD_RECOMMENDED"]),
            evidence_plain_text=evidence,
            proposal_type="REVIEW_TITLE_THUMBNAIL",
            recommended_actions=["review title/thumbnail manually", "prepare packaging variant draft"],
            risk_level="MEDIUM",
        )
    if packaging.diagnostic_state == "LOW_CTR":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="UNDERPERFORMING",
            severity="MEDIUM",
            confidence_level=packaging.confidence_level,
            primary_status="UNDERPERFORMING",
            primary_suspected_cause="PACKAGING_FAILURE",
            secondary_suspected_causes=[],
            operator_summary=packaging.operator_summary,
            next_action=packaging.next_action,
            reason_codes=_dedupe([*packaging.reason_codes, "NO_REUPLOAD_RECOMMENDED"]),
            evidence_plain_text=evidence,
            proposal_type="REVIEW_TITLE_THUMBNAIL",
            recommended_actions=["review title/thumbnail manually", "prepare packaging variant draft"],
            risk_level="MEDIUM",
        )
    if retention.diagnostic_state == "EARLY_DROP":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="UNDERPERFORMING",
            severity="MEDIUM",
            confidence_level=retention.confidence_level,
            primary_status="UNDERPERFORMING",
            primary_suspected_cause="HOOK_FAILURE",
            secondary_suspected_causes=[],
            operator_summary=retention.operator_summary,
            next_action=retention.next_action,
            reason_codes=_dedupe([*retention.reason_codes, "NO_REUPLOAD_RECOMMENDED"]),
            evidence_plain_text=evidence,
            proposal_type="REVIEW_HOOK",
            recommended_actions=["review hook section"],
            risk_level="MEDIUM",
        )
    if retention.diagnostic_state == "MID_VIDEO_DROP":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="UNDERPERFORMING",
            severity="MEDIUM",
            confidence_level=retention.confidence_level,
            primary_status="UNDERPERFORMING",
            primary_suspected_cause="RETENTION_PACING_FAILURE",
            secondary_suspected_causes=[],
            operator_summary=retention.operator_summary,
            next_action=retention.next_action,
            reason_codes=_dedupe([*retention.reason_codes, "NO_REUPLOAD_RECOMMENDED"]),
            evidence_plain_text=evidence,
            proposal_type="REVIEW_RETENTION_SECTION",
            recommended_actions=["review specific scene/time range"],
            risk_level="MEDIUM",
        )
    if engagement.diagnostic_state == "LOW_ENGAGEMENT":
        return AggregateResult(
            run_state="COMPLETED",
            health_state="WATCH",
            severity="LOW",
            confidence_level=engagement.confidence_level,
            primary_status="WATCH",
            primary_suspected_cause="LOW_ENGAGEMENT",
            secondary_suspected_causes=[],
            operator_summary=engagement.operator_summary,
            next_action=engagement.next_action,
            reason_codes=_dedupe([*engagement.reason_codes, "NO_FAKE_ENGAGEMENT"]),
            evidence_plain_text=evidence,
            proposal_type="CREATE_FUTURE_VARIANT",
            recommended_actions=["create future variant proposal"],
            risk_level="LOW",
        )
    return AggregateResult(
        run_state="COMPLETED",
        health_state="HEALTHY",
        severity="INFO",
        confidence_level="HIGH",
        primary_status="HEALTHY",
        primary_suspected_cause="HEALTHY",
        secondary_suspected_causes=[],
        operator_summary="Video đang ổn trong dữ liệu hiện có.",
        next_action="Không cần hành động lúc này.",
        reason_codes=["POST_PUBLISH_HEALTH_COMPLETED"],
        evidence_plain_text=evidence,
        proposal_type="NO_ACTION",
        recommended_actions=["no action"],
        risk_level="LOW",
    )


def _metric(context: DiagnosticContext, key: str) -> MetricRead:
    availability = {}
    if context.analytics_snapshot is not None:
        availability = context.analytics_snapshot.metric_availability or {}
    elif context.metrics_summary is not None:
        availability = (context.metrics_summary.availability_summary or {}).get("availability", {})
    state = (availability.get(key) or {}).get("state", "UNKNOWN") if isinstance(availability, dict) else "UNKNOWN"
    if context.analytics_snapshot is not None:
        normalized = context.analytics_snapshot.normalized_metrics_blob or {}
        if key in normalized:
            return MetricRead(value=_as_float(normalized[key].get("value")), availability_state="AVAILABLE", source_ref=f"analytics_snapshots.{context.analytics_snapshot.id}.{key}")
        raw = context.analytics_snapshot.metrics_blob or {}
        if key in raw:
            return MetricRead(value=_as_float(raw[key]), availability_state="AVAILABLE", source_ref=f"analytics_snapshots.{context.analytics_snapshot.id}.metrics_blob.{key}")
    if context.metrics_summary is not None:
        metrics = context.metrics_summary.metrics_summary or {}
        if key in metrics:
            return MetricRead(value=_as_float(metrics[key].get("value")), availability_state="AVAILABLE", source_ref=f"uploaded_video_metrics_summaries.{context.metrics_summary.id}.{key}")
    return MetricRead(value=None, availability_state=state, source_ref=None)


def _engagement_metrics(context: DiagnosticContext) -> dict[str, Any]:
    if context.engagement_snapshot is not None:
        return context.engagement_snapshot.engagement_blob
    keys = ("likes", "comments", "shares", "saves", "bookmarks", "engagement_rate")
    return {key: {"value": _metric(context, key).value, "state": _metric(context, key).availability_state} for key in keys}


def _detect_retention_drop(retention: RetentionCurveSnapshot | None) -> dict[str, Any] | None:
    if retention is None or not retention.curve_points:
        return None
    points = sorted(retention.curve_points, key=lambda item: float(item.get("time_seconds", 0)))
    baseline = next((point for point in points if point.get("retention_percent") is not None), None)
    if baseline is None:
        return None
    duration = float(retention.duration_seconds or points[-1].get("time_seconds") or 0)
    early_limit = max(10.0, duration * 0.15) if duration else 10.0
    previous_percent = float(baseline.get("retention_percent") or 0)
    for point in points[1:]:
        if point.get("retention_percent") is None:
            continue
        time_seconds = float(point.get("time_seconds") or 0)
        percent = float(point.get("retention_percent") or 0)
        drop_from_baseline = previous_percent - percent
        if time_seconds <= early_limit and (percent < 55 or drop_from_baseline >= 35):
            return {"state": "EARLY_DROP", "time_seconds": time_seconds, "retention_percent": percent}
        if time_seconds > early_limit and (percent < 40 or drop_from_baseline >= 35):
            return {"state": "MID_VIDEO_DROP", "time_seconds": time_seconds, "retention_percent": percent}
        previous_percent = percent
    return None


def _align_drop_to_scene(retention: RetentionCurveSnapshot | None, time_seconds: float) -> list[dict[str, Any]]:
    if retention is None:
        return []
    refs = retention.timeline_alignment.get("scene_refs", []) if isinstance(retention.timeline_alignment, dict) else []
    if not isinstance(refs, list):
        return []
    candidates = [item for item in refs if isinstance(item, dict) and item.get("time_seconds") is not None]
    candidates.sort(key=lambda item: float(item["time_seconds"]))
    selected = None
    for item in candidates:
        if float(item["time_seconds"]) <= time_seconds:
            selected = item
        else:
            break
    if selected is None and candidates:
        selected = candidates[0]
    if selected is None:
        return []
    return [
        {
            "drop_time_seconds": time_seconds,
            "scene_id": selected.get("scene_id"),
            "narration_segment_id": selected.get("narration_segment_id"),
            "scene_time_seconds": selected.get("time_seconds"),
        }
    ]


def _evidence_refs(context: DiagnosticContext) -> list[dict[str, Any]]:
    uploaded = context.uploaded
    lineage = _lineage_refs(uploaded)
    refs = [
        {"type": "UploadedVideo", "id": str(uploaded.id)},
        {"type": "RenderPackage", "id": str(uploaded.render_package_snapshot_id)},
        {"type": "PublishHandoff", "id": str(uploaded.publish_handoff_package_id)},
    ]
    if context.analytics_snapshot:
        refs.append({"type": "AnalyticsSnapshot", "id": str(context.analytics_snapshot.id)})
    if context.metrics_summary:
        refs.append({"type": "UploadedVideoMetricsSummary", "id": str(context.metrics_summary.id)})
    if context.retention_snapshot:
        refs.append({"type": "RetentionCurveSnapshot", "id": str(context.retention_snapshot.id)})
    if context.traffic_snapshot:
        refs.append({"type": "TrafficSourceSnapshot", "id": str(context.traffic_snapshot.id)})
    if context.engagement_snapshot:
        refs.append({"type": "EngagementSnapshot", "id": str(context.engagement_snapshot.id)})
    if uploaded.source_manifest_snapshot_id:
        refs.append({"type": "SourceManifest", "id": str(uploaded.source_manifest_snapshot_id)})
    if lineage.get("media_qc_report_id"):
        refs.append({"type": "MediaQC", "id": lineage["media_qc_report_id"]})
    if lineage.get("accessibility_qc_report_id"):
        refs.append({"type": "AccessibilityQC", "id": lineage["accessibility_qc_report_id"]})
    if uploaded.rights_envelope_ref:
        refs.append({"type": "RightsEnvelope", "id": uploaded.rights_envelope_ref})
    return refs


def _evidence_plain_text(context: DiagnosticContext, diagnostics: dict[str, Any]) -> list[str]:
    views = _metric(context, "views")
    impressions = _metric(context, "impressions")
    ctr = _metric(context, "click_through_rate")
    lines = [
        f"UploadedVideo: {context.uploaded.platform}/{context.uploaded.platform_video_id}.",
        f"Observation window: {context.observation_window}.",
        f"Views: {views.value if views.value is not None else views.availability_state}.",
        f"Impressions: {impressions.value if impressions.value is not None else impressions.availability_state}.",
        f"CTR: {ctr.value if ctr.value is not None else ctr.availability_state}.",
    ]
    if context.analytics_snapshot:
        lines.append(f"AnalyticsSnapshot used: {context.analytics_snapshot.id}.")
    else:
        lines.append("No AnalyticsSnapshot available.")
    for name, diagnostic in diagnostics.items():
        lines.append(f"{name}: {diagnostic.diagnostic_state} - {diagnostic.operator_summary}")
    return lines


def _operator_report(run: PostPublishHealthRun, aggregate: AggregateResult) -> dict[str, Any]:
    return {
        "operator_summary": aggregate.operator_summary,
        "friendly_status": _friendly_status(aggregate.primary_status),
        "severity_label": _friendly_severity(aggregate.severity),
        "confidence_label": _friendly_confidence(aggregate.confidence_level),
        "likely_cause_label": DIAGNOSTIC_TAXONOMY.get(aggregate.primary_suspected_cause or "", aggregate.primary_suspected_cause),
        "evidence_plain_text": aggregate.evidence_plain_text,
        "next_action": aggregate.next_action,
        "do_not_do": DO_NOT_DO_DEFAULT,
        "owner_role": _owner_role(aggregate.proposal_type),
        "due_at": None,
        "checklist": _checklist_for_proposal(aggregate.proposal_type),
        "technical_appendix": {
            "health_run_id": str(run.id),
            "reason_codes": aggregate.reason_codes,
            "confidence_is_not_severity": True,
        },
    }


def _friendly_status(status: str) -> str:
    mapping = {
        "HEALTHY": "Đang ổn",
        "WATCH": "Theo dõi thêm",
        "NO_VIEW_RISK": "Rủi ro ít lượt xem",
        "UNDERPERFORMING": "Đang dưới kỳ vọng",
        "POLICY_REVIEW_REQUIRED": "Cần review policy/quyền",
        "INSUFFICIENT_DATA": "Chưa đủ dữ liệu",
        "UNKNOWN": "Chưa rõ",
    }
    return mapping.get(status, status)


def _friendly_severity(severity: str) -> str:
    return {"INFO": "Thông tin", "LOW": "Thấp", "MEDIUM": "Trung bình", "HIGH": "Cao", "CRITICAL": "Rất cao"}.get(severity, severity)


def _friendly_confidence(confidence: str) -> str:
    return {"HIGH": "Cao", "MEDIUM": "Trung bình", "LOW": "Thấp", "UNKNOWN": "Chưa rõ"}.get(confidence, confidence)


def _owner_role(proposal_type: str) -> str | None:
    if proposal_type in {"REVIEW_TITLE_THUMBNAIL", "REVIEW_HOOK", "REVIEW_RETENTION_SECTION", "CREATE_FUTURE_VARIANT"}:
        return "operator"
    if proposal_type in {"REVIEW_RIGHTS_DISCLOSURE", "REVIEW_SOURCE_QUALITY"}:
        return "company_admin"
    return None


def _checklist_for_proposal(proposal_type: str) -> list[str]:
    mapping = {
        "WAIT_AND_MONITOR": ["Chờ mốc quan sát tiếp theo", "Import analytics từ M8 nếu có", "Không re-upload"],
        "REVIEW_TITLE_THUMBNAIL": ["Review title", "Review thumbnail", "Draft variant để human approve"],
        "REVIEW_HOOK": ["Review 0-10s", "Ghi edit note cho phiên bản tương lai"],
        "REVIEW_RETENTION_SECTION": ["Review scene/time range", "Ghi pacing note"],
        "REVIEW_RIGHTS_DISCLOSURE": ["Kiểm tra disclosure", "Kiểm tra rights/license evidence"],
        "NO_ACTION": ["Không cần hành động"],
    }
    return mapping.get(proposal_type, ["Human review"])


def _proposal_summary(proposal_type: str) -> str:
    mapping = {
        "WAIT_AND_MONITOR": "Chờ thêm dữ liệu theo observation window; không tự recovery.",
        "REVIEW_TITLE_THUMBNAIL": "Human review title/thumbnail và có thể chuẩn bị draft variant.",
        "REVIEW_HOOK": "Human review phần hook đầu video.",
        "REVIEW_RETENTION_SECTION": "Human review đoạn retention drop.",
        "REVIEW_RIGHTS_DISCLOSURE": "Human review disclosure, rights hoặc license evidence.",
        "REVIEW_SOURCE_QUALITY": "Human review source quality.",
        "CREATE_FUTURE_VARIANT": "Tạo proposal cho future variant, không tự publish.",
        "NO_ACTION": "Không cần hành động.",
    }
    return mapping[proposal_type]


def _sanitize_recommended_actions(actions: list[str]) -> list[str]:
    cleaned: list[str] = []
    for action in actions:
        lowered = action.lower()
        if any(term in lowered for term in FORBIDDEN_ACTION_TERMS):
            raise ValidationFailureError(f"forbidden recovery action: {action}")
        cleaned.append(action)
    return cleaned


def _priority_from_severity(severity: str) -> str:
    return {"CRITICAL": "URGENT", "HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW", "INFO": "LOW"}.get(severity, "MEDIUM")


def _metric_values_for_appendix(context: DiagnosticContext) -> dict[str, Any]:
    keys = [
        "views",
        "impressions",
        "click_through_rate",
        "average_view_duration_seconds",
        "average_view_percentage",
        "engagement_rate",
    ]
    return {key: {"value": _metric(context, key).value, "availability": _metric(context, key).availability_state} for key in keys}


def _snapshot_refs(context: DiagnosticContext) -> dict[str, str | None]:
    return {
        "analytics_snapshot_id": str(context.analytics_snapshot.id) if context.analytics_snapshot else None,
        "uploaded_video_metrics_summary_id": str(context.metrics_summary.id) if context.metrics_summary else None,
        "retention_curve_snapshot_id": str(context.retention_snapshot.id) if context.retention_snapshot else None,
        "traffic_source_snapshot_id": str(context.traffic_snapshot.id) if context.traffic_snapshot else None,
        "engagement_snapshot_id": str(context.engagement_snapshot.id) if context.engagement_snapshot else None,
    }


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


def _require_uploaded(session: Session, uploaded_video_id: uuid.UUID) -> UploadedVideo:
    uploaded = session.get(UploadedVideo, uploaded_video_id)
    if uploaded is None:
        raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
    return uploaded


def _diagnostic_event(
    session: Session,
    event_type: str,
    aggregate_id: uuid.UUID,
    run: PostPublishHealthRun,
    correlation_id: str,
    reason_code: str,
) -> None:
    _record_m9_event(
        session,
        event_type=event_type,
        aggregate_type=event_type.split(".")[0],
        aggregate_id=aggregate_id,
        target_type=event_type.split(".")[0],
        target_id=aggregate_id,
        company_id=run.company_id,
        correlation_id=correlation_id,
        reason_code=reason_code,
        payload={"post_publish_health_run_id": str(run.id), "uploaded_video_id": str(run.uploaded_video_id)},
    )


def _record_m9_event(
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
) -> None:
    safe_payload = _jsonable(payload)
    _ensure_no_secret_payload(safe_payload)
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=safe_payload,
            metadata={"milestone": "M9"},
        ),
        company_id=company_id,
    )
    AuditService(session).append(
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


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


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
