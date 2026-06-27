from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m11 import (
    ApprovedPlaybookEntryRead,
    ApprovalQueueItem,
    ApprovalQueueSummary,
    ChannelLifecycleDecisionCreate,
    ChannelLifecycleDecisionRead,
    ChannelLifecycleRead,
    ChannelWorkspaceDashboardRead,
    CommandCenterRead,
    DashboardActionCard,
    DashboardMetricCard,
    DashboardQueuesRead,
    DashboardWarning,
    LearningReviewDecisionCreate,
    LearningReviewDecisionRead,
    ProviderOpsDashboardRead,
    UploadedVideoDashboardRead,
    UploadedVideoListItem,
)
from app.core.errors import ForbiddenError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AnalyticsSnapshot,
    ApprovedPlaybookEntry,
    ChannelDailyRun,
    ChannelLifecycleDecision,
    ChannelProfileVersion,
    ChannelWorkspace,
    CloudMediaRef,
    CostEvent,
    CredentialHealthSnapshot,
    CredentialReference,
    FailureTraceReport,
    GoogleDriveMediaCredential,
    LearningCandidate,
    LearningEvidenceBundle,
    LearningReviewDecision,
    LearningReviewQueueItem,
    LocalizedMetadataPackage,
    LocalizedSubtitlePackage,
    ManualAction,
    MediaOffloadJob,
    OpsIncident,
    PlaybookCandidateDraft,
    ProviderCapabilityMatrixEntry,
    ProviderHealthSnapshot,
    ProviderRegistryEntry,
    PublishHandoffPackage,
    PublishTimingSuggestion,
    QuotaAccount,
    RecoveryProposal,
    UploadedVideo,
    UploadedVideoMetricsSummary,
    UploadedVideoYouTubeOwnerAnalyticsSnapshot,
    UploadedVideoYouTubePublicMonitorSnapshot,
    VideoProject,
    YouTubeMonitoringCredential,
)
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "OWNER_ADMIN": {"*"},
    "CHANNEL_MANAGER": {"CHANNEL_LIFECYCLE", "RECOVERY_DECISION"},
    "PRODUCER": {"PROJECT_READ"},
    "REVIEWER": {"ARTIFACT_REVIEW"},
    "PUBLISHER": {"PUBLISH_CONFIRM"},
    "ANALYST": {"ANALYTICS_READ", "RECOVERY_DECISION"},
    "PROCUREMENT_OPERATOR": {"ASSET_REVIEW"},
    "COMPLIANCE_REVIEWER": {"GATE_REVIEW", "RIGHTS_REVIEW"},
    "LEARNING_REVIEWER": {"LEARNING_REVIEW"},
    "READ_ONLY_OBSERVER": set(),
}
LEARNING_ACTION_TO_CANDIDATE_STATE = {
    "APPROVE": "READY_FOR_HUMAN_REVIEW",
    "REJECT": "INELIGIBLE_LOW_EVIDENCE",
    "REQUEST_MORE_EVIDENCE": "NEEDS_MORE_EVIDENCE",
    "SUPPRESS": "CANCELLED",
    "EXPIRE": "EXPIRED",
}
LEARNING_ACTION_TO_QUEUE_STATE = {
    "APPROVE": "CANCELLED",
    "REJECT": "CANCELLED",
    "REQUEST_MORE_EVIDENCE": "NEEDS_MORE_EVIDENCE",
    "SUPPRESS": "CANCELLED",
    "EXPIRE": "EXPIRED",
}


class M11DashboardService:
    def __init__(self, session: Session):
        self.session = session

    def command_center(self, *, company_id: uuid.UUID | None = None) -> CommandCenterRead:
        ready_to_publish = self._count(PublishHandoffPackage, PublishHandoffPackage.package_state == "READY_FOR_OPERATOR", company_id=company_id)
        learning_count = self._count(
            LearningReviewQueueItem,
            LearningReviewQueueItem.queue_state.in_(["READY_FOR_HUMAN_REVIEW", "NEEDS_MORE_EVIDENCE", "BLOCKED"]),
            company_id=company_id,
        )
        recovery_count = self._count(RecoveryProposal, RecoveryProposal.proposal_state == "PROPOSED")
        manual_action_count = self._count(ManualAction, ManualAction.state == "OPEN")
        incident_count = self._count(OpsIncident, OpsIncident.state.in_(["OPEN", "ACKNOWLEDGED"]))
        stale_metrics_count = self._count(
            UploadedVideoMetricsSummary,
            UploadedVideoMetricsSummary.freshness_state.in_(["STALE", "UNKNOWN"]),
            company_id=company_id,
        )
        channels_at_risk = self._count_channels_with_health(["LOW_VIEW", "NO_VIEW", "WATCHLIST", "NEEDS_HUMAN_REVIEW"])
        drive_auth_needed = self._drive_auth_needed()
        youtube_auth_needed = self._youtube_auth_needed()
        cards = [
            _action_card("critical_queue", "Việc cần xử lý", learning_count + recovery_count + manual_action_count + incident_count, "HIGH", "Mở hàng chờ duyệt, phục hồi và ops.", "/queues"),
            _action_card("due_today", "Việc ops đang mở", manual_action_count, "NORMAL", "Mở hàng chờ thao tác thủ công.", "/queues/ops"),
            _action_card("blocked_human", "Đang chờ người vận hành", ready_to_publish + learning_count, "HIGH", "Duyệt bài học hoặc hoàn tất gói publish.", "/queues"),
            _action_card("blocked_policy", "Bị chặn bởi policy/rights", self._count_blocked_gates(), "HIGH", "Mở hàng chờ kiểm tra bằng chứng và quyền.", "/queues"),
            _action_card("blocked_provider", "Nhà cung cấp/quota cần xem", incident_count, "HIGH", "Kiểm tra trạng thái nhà cung cấp và ops.", "/ops"),
            _action_card("needs_youtube_auth", "Cần kết nối YouTube", 1 if youtube_auth_needed else 0, "NORMAL", "Kết nối lại owner analytics khi cần.", "/ops"),
            _action_card("needs_drive_auth", "Cần kết nối Google Drive", 1 if drive_auth_needed else 0, "NORMAL", "Kết nối Google Drive media offload.", "/media"),
            _action_card("channels_at_risk", "Kênh cần theo dõi", channels_at_risk, "HIGH", "Mở lifecycle và diagnostic của kênh.", "/channels"),
            _action_card("learning_review", "Bài học chờ duyệt", learning_count, "NORMAL", "Xem bằng chứng trước khi đưa vào playbook.", "/learning"),
        ]
        required_actions = [
            {"type": "PUBLISH_HANDOFF", "count": ready_to_publish, "next_action": "Upload thủ công trên YouTube, rồi paste back video_id/url."},
            {"type": "LEARNING_REVIEW", "count": learning_count, "next_action": "Duyệt evidence bundle; không tự mutate profile/config."},
            {"type": "RECOVERY_REVIEW", "count": recovery_count, "next_action": "Duyệt/chờ/từ chối đề xuất phục hồi. Không re-upload spam."},
            {"type": "ANALYTICS_FRESHNESS", "count": stale_metrics_count, "next_action": "Sync/import analytics trước khi kết luận."},
        ]
        return CommandCenterRead(
            generated_at=utc_now(),
            company_id=company_id,
            cards=cards,
            metrics=[
                DashboardMetricCard(key="ready_to_publish", label="Gói publish sẵn sàng", value=ready_to_publish, state="ACTION_REQUIRED"),
                DashboardMetricCard(key="stale_metrics", label="Metric YouTube cũ/chưa có", value=stale_metrics_count, state="CHECK_FRESHNESS"),
                DashboardMetricCard(key="ops_incidents", label="Sự cố ops/nhà cung cấp", value=incident_count, state="WATCH"),
            ],
            required_actions=required_actions,
            safety_warnings=_safety_warnings(),
            technical_appendix={
                "source": "M11DashboardService",
                "no_raw_logs_by_default": True,
                "no_provider_calls": True,
            },
        )

    def queues(self, *, queue_type: str | None = None) -> DashboardQueuesRead:
        items: list[ApprovalQueueItem] = []
        if queue_type in {None, "learning"}:
            items.extend(self._learning_queue_items())
        if queue_type in {None, "publish"}:
            items.extend(self._publish_queue_items())
        if queue_type in {None, "recovery"}:
            items.extend(self._recovery_queue_items())
        if queue_type in {None, "ops"}:
            items.extend(self._ops_queue_items())
        summaries_by_type: dict[str, ApprovalQueueSummary] = {}
        for item in items:
            current = summaries_by_type.get(item.queue_type)
            if current is None:
                summaries_by_type[item.queue_type] = ApprovalQueueSummary(
                    queue_type=item.queue_type,
                    label=_queue_label(item.queue_type),
                    count=1,
                    priority=item.priority,
                    next_action=item.next_action,
                    allowed_actions=sorted(set(item.allowed_actions)),
                )
            else:
                current.count += 1
                current.allowed_actions = sorted(set([*current.allowed_actions, *item.allowed_actions]))
        return DashboardQueuesRead(generated_at=utc_now(), summaries=list(summaries_by_type.values()), items=items)

    def list_channels(self, *, company_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        statement = select(ChannelWorkspace).order_by(ChannelWorkspace.created_at.desc(), ChannelWorkspace.id.desc())
        if company_id is not None:
            statement = statement.where(ChannelWorkspace.company_id == company_id)
        return [_channel_summary(channel, self.lifecycle(channel.id)) for channel in self.session.scalars(statement).all()]

    def lifecycle(self, channel_id: uuid.UUID) -> ChannelLifecycleRead:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        latest = self._latest_lifecycle_decision(channel_id)
        lifecycle_state = _lifecycle_from_channel(channel)
        health_status = _metadata_health(channel)
        if latest is not None:
            health_status = latest.health_status
        daily_allowed = lifecycle_state == "ACTIVE"
        next_action = _channel_next_action(lifecycle_state, health_status)
        return ChannelLifecycleRead(
            channel_id=channel.id,
            lifecycle_state=lifecycle_state,
            health_status=health_status,
            daily_generation_allowed=daily_allowed,
            next_action=next_action,
            main_blocker=None if daily_allowed else next_action,
            allowed_actions=_allowed_lifecycle_actions(lifecycle_state),
            last_decision=_channel_lifecycle_decision_dict(latest) if latest is not None else None,
        )

    def workspace(self, channel_id: uuid.UUID) -> ChannelWorkspaceDashboardRead:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        lifecycle = self.lifecycle(channel_id)
        projects = [
            _project_card(project)
            for project in self.session.scalars(
                select(VideoProject)
                .where(VideoProject.channel_workspace_id == channel_id)
                .order_by(VideoProject.created_at.desc())
                .limit(25)
            ).all()
        ]
        daily_runs = [
            _daily_run_card(run)
            for run in self.session.scalars(
                select(ChannelDailyRun)
                .where(ChannelDailyRun.channel_workspace_id == channel_id)
                .order_by(ChannelDailyRun.created_at.desc())
                .limit(20)
            ).all()
        ]
        approvals = [item for item in self._learning_queue_items(channel_id=channel_id)[:10]]
        uploaded_videos = [item.model_dump(mode="json") for item in self.list_uploaded_videos(channel_id=channel_id)[:10]]
        media_count = self._count(CloudMediaRef, CloudMediaRef.channel_workspace_id == channel_id)
        failed_media_count = self._count(CloudMediaRef, CloudMediaRef.channel_workspace_id == channel_id, CloudMediaRef.upload_status == "FAILED")
        return ChannelWorkspaceDashboardRead(
            channel=_channel_dict(channel),
            health_summary={
                "channel_status": lifecycle.lifecycle_state,
                "health": lifecycle.health_status,
                "next_action": lifecycle.next_action,
                "main_blocker": lifecycle.main_blocker,
                "analytics_freshness": self._channel_analytics_freshness(channel_id),
                "production_state": _state_from_count(len(projects), "NO_PROJECTS", "PROJECTS_ACTIVE"),
                "publish_state": _state_from_count(self._count(PublishHandoffPackage, PublishHandoffPackage.channel_workspace_id == channel_id), "NO_HANDOFFS", "HANDOFFS_AVAILABLE"),
                "learning_state": _state_from_count(len(approvals), "NO_LEARNING_REVIEW", "LEARNING_REVIEW_READY"),
                "storage_state": "FAILED" if failed_media_count else _state_from_count(media_count, "NO_CLOUD_MEDIA", "GOOGLE_DRIVE_READY"),
            },
            lifecycle=lifecycle,
            projects=projects,
            daily_runs=daily_runs,
            approvals=approvals,
            uploaded_videos=uploaded_videos,
            media_storage={"provider": "Google Drive", "cloud_media_count": media_count, "failed_count": failed_media_count, "cta_only": True},
            provider_health=self.provider_ops().integrations,
            technical_appendix={"no_latest_profile_lookup_for_projects": True},
        )

    def list_uploaded_videos(
        self,
        *,
        channel_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
    ) -> list[UploadedVideoListItem]:
        statement = select(UploadedVideo).order_by(UploadedVideo.published_at.desc(), UploadedVideo.id.desc())
        if channel_id is not None:
            statement = statement.where(UploadedVideo.channel_workspace_id == channel_id)
        if company_id is not None:
            statement = statement.where(UploadedVideo.company_id == company_id)
        items = []
        for uploaded in self.session.scalars(statement.limit(100)).all():
            summary = self._latest_metrics_summary(uploaded.id)
            public = self._latest_public_snapshot(uploaded.id)
            owner = self._latest_owner_snapshot(uploaded.id)
            metrics = _metrics_from_summary(summary, public, owner)
            title = str(uploaded.actual_metadata.get("actual_title") or uploaded.operator_summary.get("title") or uploaded.platform_video_id)
            items.append(
                UploadedVideoListItem(
                    id=uploaded.id,
                    title=title,
                    channel_id=uploaded.channel_workspace_id,
                    platform=uploaded.platform,
                    platform_video_id=uploaded.platform_video_id,
                    video_url=uploaded.video_url,
                    published_at=uploaded.published_at,
                    metrics=metrics,
                    freshness=summary.freshness_state if summary is not None else "UNKNOWN",
                    owner_analytics_status="CONNECTED" if owner is not None else "UNKNOWN",
                    latest_diagnostic=self._latest_failure_status(uploaded.id),
                    next_action=summary.next_action if summary is not None else "Metric này chưa có dữ liệu, không phải bằng 0. Cần sync/import trước khi quyết định.",
                )
            )
        return items

    def uploaded_video_dashboard(self, uploaded_video_id: uuid.UUID) -> UploadedVideoDashboardRead:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        public = self._latest_public_snapshot(uploaded.id)
        owner = self._latest_owner_snapshot(uploaded.id)
        return UploadedVideoDashboardRead(
            uploaded_video=_uploaded_video_dict(uploaded),
            public_stats=_public_stats_card(public),
            owner_analytics=_owner_analytics_card(owner),
            publish_check={
                **_publish_check(public),
                **self._publish_timing_check(uploaded),
                "localization_packages": self._localization_packages(uploaded.video_project_id),
            },
            diagnostics=[_failure_report_card(report) for report in self._failure_reports(uploaded.id)],
            recovery_proposals=[_recovery_card(proposal) for proposal in self._recovery_proposals(uploaded.id)],
            learning_candidates=[_learning_candidate_card(candidate) for candidate in self._learning_candidates(uploaded.id)],
            media=[_cloud_media_card(ref) for ref in self._cloud_refs_for_uploaded(uploaded.id)],
            safety_warnings=_safety_warnings(),
            technical_appendix={"uploaded_video_id": str(uploaded.id), "youtube_public_authority": "WEAK", "youtube_owner_authority": "STRONG"},
        )

    def provider_ops(self) -> ProviderOpsDashboardRead:
        providers = [
            {
                "provider_key": provider.provider_key,
                "provider_name": provider.provider_name,
                "provider_type": provider.provider_type,
                "status": provider.status,
                "next_action": "Kiểm tra capability/budget của nhà cung cấp." if provider.status != "ACTIVE" else None,
            }
            for provider in self.session.scalars(select(ProviderRegistryEntry).order_by(ProviderRegistryEntry.provider_key.asc()).limit(100)).all()
        ]
        credentials = [
            {
                "provider_key": credential.provider_key,
                "credential_key": credential.credential_key,
                "status": credential.status,
                "secret_values_exposed": False,
            }
            for credential in self.session.scalars(select(CredentialReference).order_by(CredentialReference.created_at.desc()).limit(100)).all()
        ]
        quotas = [
            {
                "provider_key": quota.provider_key,
                "unit": quota.unit,
                "quota_limit": str(quota.quota_limit) if quota.quota_limit is not None else None,
                "quota_used": str(quota.quota_used),
                "quota_reserved": str(quota.quota_reserved),
                "status": quota.status,
            }
            for quota in self.session.scalars(select(QuotaAccount).order_by(QuotaAccount.created_at.desc()).limit(50)).all()
        ]
        costs = [
            {"provider_key": cost.provider_key, "amount": str(cost.amount), "currency": cost.currency, "cost_type": cost.cost_type, "created_at": cost.created_at}
            for cost in self.session.scalars(select(CostEvent).order_by(CostEvent.created_at.desc()).limit(50)).all()
        ]
        incidents = [
            {"id": incident.id, "incident_type": incident.incident_type, "severity": incident.severity, "state": incident.state, "next_action": incident.next_action}
            for incident in self.session.scalars(select(OpsIncident).order_by(OpsIncident.created_at.desc()).limit(50)).all()
        ]
        manual_actions = [
            {"id": action.id, "action_type": action.action_type, "priority": action.priority, "state": action.state, "next_action": action.next_action}
            for action in self.session.scalars(select(ManualAction).order_by(ManualAction.created_at.desc()).limit(50)).all()
        ]
        return ProviderOpsDashboardRead(
            generated_at=utc_now(),
            providers=providers,
            credentials=credentials,
            quotas=quotas,
            costs=costs,
            incidents=incidents,
            manual_actions=manual_actions,
            integrations={
                "ollama_router": {"state": _state_from_count(self._count_provider_key_like("ollama"), "DISABLED", "CONFIGURED")},
                "google_vertex_veo": {"state": _state_from_count(self._count_provider_key_like("GOOGLE_VERTEX_VEO"), "CONFIGURED_BY_CATALOG", "CONFIGURED")},
                "google_drive": {"state": "CONNECTED" if not self._drive_auth_needed() else "NEEDS_AUTH"},
                "youtube_analytics": {"state": "CONNECTED" if not self._youtube_auth_needed() else "NEEDS_AUTH"},
                "cloud_final_renderer": {"state": self._cloud_final_renderer_state()},
            },
            safety_warnings=_safety_warnings(),
        )

    def _count(self, model: Any, *conditions: Any, company_id: uuid.UUID | None = None) -> int:
        statement = select(func.count()).select_from(model)
        for condition in conditions:
            statement = statement.where(condition)
        if company_id is not None and hasattr(model, "company_id"):
            statement = statement.where(model.company_id == company_id)
        return int(self.session.scalar(statement) or 0)

    def _latest_lifecycle_decision(self, channel_id: uuid.UUID) -> ChannelLifecycleDecision | None:
        return self.session.scalars(
            select(ChannelLifecycleDecision)
            .where(ChannelLifecycleDecision.channel_workspace_id == channel_id)
            .order_by(ChannelLifecycleDecision.created_at.desc(), ChannelLifecycleDecision.id.desc())
            .limit(1)
        ).one_or_none()

    def _learning_queue_items(self, *, channel_id: uuid.UUID | None = None) -> list[ApprovalQueueItem]:
        statement = select(LearningReviewQueueItem).order_by(LearningReviewQueueItem.created_at.desc()).limit(100)
        if channel_id is not None:
            statement = statement.where(LearningReviewQueueItem.channel_workspace_id == channel_id)
        items = []
        for item in self.session.scalars(statement).all():
            channel = self.session.get(ChannelWorkspace, item.channel_workspace_id) if item.channel_workspace_id else None
            project = self.session.get(VideoProject, item.video_project_id) if item.video_project_id else None
            items.append(
                ApprovalQueueItem(
                    queue_item_id=item.id,
                    queue_type="learning",
                    entity_type="learning_candidate",
                    entity_id=item.learning_candidate_id,
                    channel=_channel_ref(channel),
                    project=_project_ref(project),
                    operator_summary=item.operator_summary,
                    friendly_status=item.friendly_status,
                    priority=item.priority,
                    risk_level=item.risk_level,
                    confidence_label=item.confidence_label,
                    freshness_label="SEE_EVIDENCE",
                    evidence_summary=item.evidence_summary,
                    next_action=item.next_action,
                    due_at=item.due_at,
                    allowed_actions=item.approval_actions_allowed,
                    source_refs=item.source_refs,
                    audit_refs=item.audit_refs,
                    technical_appendix=item.technical_appendix,
                )
            )
        return items

    def _publish_queue_items(self) -> list[ApprovalQueueItem]:
        statement = (
            select(PublishHandoffPackage)
            .where(PublishHandoffPackage.package_state == "READY_FOR_OPERATOR")
            .order_by(PublishHandoffPackage.created_at.desc())
            .limit(100)
        )
        items = []
        for handoff in self.session.scalars(statement).all():
            channel = self.session.get(ChannelWorkspace, handoff.channel_workspace_id)
            project = self.session.get(VideoProject, handoff.video_project_id)
            items.append(
                ApprovalQueueItem(
                    queue_item_id=handoff.id,
                    queue_type="publish_confirmation",
                    entity_type="publish_handoff_package",
                    entity_id=handoff.id,
                    channel=_channel_ref(channel),
                    project=_project_ref(project),
                    operator_summary="Gói publish đã sẵn sàng cho upload thủ công.",
                    friendly_status="Cần người vận hành upload lên YouTube và paste back video_id/url.",
                    priority="HIGH",
                    risk_level=str(handoff.risk_summary.get("risk_level", "UNKNOWN")),
                    confidence_label="HUMAN_REQUIRED",
                    freshness_label="CURRENT",
                    evidence_summary="Media phải mở qua Google Drive CTA; không dùng đường dẫn local.",
                    next_action=handoff.next_action or "Mở file trên Google Drive, upload thủ công, rồi nhập lại thông tin publish thực tế.",
                    allowed_actions=["OPEN_DRIVE", "CONFIRM_MANUAL_PUBLISH"],
                    source_refs=handoff.cloud_media_refs,
                    audit_refs=[{"type": "publish_handoff_package", "id": str(handoff.id)}],
                    technical_appendix={"no_youtube_upload_api": True, "no_backend_download_proxy": True},
                )
            )
        return items

    def _recovery_queue_items(self) -> list[ApprovalQueueItem]:
        statement = (
            select(RecoveryProposal)
            .where(RecoveryProposal.proposal_state == "PROPOSED")
            .order_by(RecoveryProposal.created_at.desc())
            .limit(100)
        )
        return [
            ApprovalQueueItem(
                queue_item_id=proposal.id,
                queue_type="recovery",
                entity_type="recovery_proposal",
                entity_id=proposal.id,
                operator_summary=proposal.operator_summary,
                friendly_status="Đề xuất phục hồi đang chờ người duyệt.",
                priority="NORMAL",
                risk_level=proposal.risk_level,
                confidence_label="EVIDENCE_BOUND",
                freshness_label="SEE_DIAGNOSTIC",
                evidence_summary=", ".join(proposal.recommended_actions) or "Chưa có mô tả thao tác.",
                next_action="Duyệt, từ chối, chờ thêm dữ liệu, yêu cầu review, hoặc đánh dấu không an toàn.",
                allowed_actions=["ACCEPT", "REJECT", "WAIT", "REQUEST_REVIEW", "MARK_UNSAFE"],
                source_refs=proposal.evidence_refs,
                audit_refs=[{"type": "recovery_proposal", "id": str(proposal.id)}],
                technical_appendix={"forbidden_recovery": ["fake traffic", "bot engagement", "platform evasion", "reupload spam"]},
            )
            for proposal in self.session.scalars(statement).all()
        ]

    def _ops_queue_items(self) -> list[ApprovalQueueItem]:
        statement = select(ManualAction).where(ManualAction.state == "OPEN").order_by(ManualAction.created_at.desc()).limit(100)
        return [
            ApprovalQueueItem(
                queue_item_id=action.id,
                queue_type="ops_manual_action",
                entity_type=action.target_type,
                entity_id=action.target_id,
                operator_summary=action.next_action,
                friendly_status=action.reason_code or "Cần thao tác thủ công.",
                priority=action.priority,
                risk_level="UNKNOWN",
                confidence_label="SYSTEM_RECORDED",
                freshness_label="CURRENT",
                evidence_summary=action.reason_code or "Thao tác ops thủ công.",
                next_action=action.next_action,
                allowed_actions=["COMPLETE", "ADD_NOTE"],
                audit_refs=[{"type": "manual_action", "id": str(action.id)}],
            )
            for action in self.session.scalars(statement).all()
        ]

    def _latest_metrics_summary(self, uploaded_video_id: uuid.UUID) -> UploadedVideoMetricsSummary | None:
        return self.session.scalars(select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded_video_id).limit(1)).one_or_none()

    def _latest_public_snapshot(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubePublicMonitorSnapshot | None:
        return self.session.scalars(
            select(UploadedVideoYouTubePublicMonitorSnapshot)
            .where(UploadedVideoYouTubePublicMonitorSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(UploadedVideoYouTubePublicMonitorSnapshot.last_synced_at.desc())
            .limit(1)
        ).one_or_none()

    def _latest_owner_snapshot(self, uploaded_video_id: uuid.UUID) -> UploadedVideoYouTubeOwnerAnalyticsSnapshot | None:
        return self.session.scalars(
            select(UploadedVideoYouTubeOwnerAnalyticsSnapshot)
            .where(UploadedVideoYouTubeOwnerAnalyticsSnapshot.uploaded_video_id == uploaded_video_id)
            .order_by(UploadedVideoYouTubeOwnerAnalyticsSnapshot.last_synced_at.desc())
            .limit(1)
        ).one_or_none()

    def _latest_failure_status(self, uploaded_video_id: uuid.UUID) -> str | None:
        report = self.session.scalars(
            select(FailureTraceReport)
            .where(FailureTraceReport.uploaded_video_id == uploaded_video_id)
            .order_by(FailureTraceReport.created_at.desc())
            .limit(1)
        ).one_or_none()
        return report.primary_status if report is not None else None

    def _failure_reports(self, uploaded_video_id: uuid.UUID) -> list[FailureTraceReport]:
        return list(
            self.session.scalars(
                select(FailureTraceReport)
                .where(FailureTraceReport.uploaded_video_id == uploaded_video_id)
                .order_by(FailureTraceReport.created_at.desc())
                .limit(10)
            ).all()
        )

    def _recovery_proposals(self, uploaded_video_id: uuid.UUID) -> list[RecoveryProposal]:
        return list(
            self.session.scalars(
                select(RecoveryProposal)
                .where(RecoveryProposal.uploaded_video_id == uploaded_video_id)
                .order_by(RecoveryProposal.created_at.desc())
                .limit(10)
            ).all()
        )

    def _learning_candidates(self, uploaded_video_id: uuid.UUID) -> list[LearningCandidate]:
        return list(
            self.session.scalars(
                select(LearningCandidate)
                .where(LearningCandidate.uploaded_video_id == uploaded_video_id)
                .order_by(LearningCandidate.created_at.desc())
                .limit(10)
            ).all()
        )

    def _cloud_refs_for_uploaded(self, uploaded_video_id: uuid.UUID) -> list[CloudMediaRef]:
        return list(
            self.session.scalars(
                select(CloudMediaRef)
                .where(CloudMediaRef.uploaded_video_id == uploaded_video_id)
                .order_by(CloudMediaRef.created_at.desc())
                .limit(25)
            ).all()
        )

    def _publish_timing_check(self, uploaded: UploadedVideo) -> dict[str, Any]:
        suggestion = self.session.scalars(
            select(PublishTimingSuggestion)
            .where(PublishTimingSuggestion.publish_handoff_package_id == uploaded.publish_handoff_package_id)
            .order_by(PublishTimingSuggestion.created_at.desc())
            .limit(1)
        ).one_or_none()
        if suggestion is None:
            return {
                "actual_published_at": uploaded.published_at,
                "configured_publish_window": None,
                "published_inside_configured_window": "UNKNOWN",
                "publish_timing_summary": "Chưa có khung giờ publish đã cấu hình cho video này.",
            }
        inside_window = abs((uploaded.published_at - suggestion.suggested_publish_at_utc).total_seconds()) <= 7200
        return {
            "actual_published_at": uploaded.published_at,
            "configured_publish_window": suggestion.suggested_publish_at_local,
            "channel_timezone": suggestion.target_timezone,
            "operator_local_time": suggestion.operator_local_time,
            "published_inside_configured_window": "INSIDE" if inside_window else "OUTSIDE",
            "publish_timing_summary": "Khung giờ publish đã cấu hình; human vẫn quyết định giờ publish thực tế.",
        }

    def _localization_packages(self, video_project_id: uuid.UUID) -> dict[str, Any]:
        subtitles = self.session.scalars(
            select(LocalizedSubtitlePackage).where(LocalizedSubtitlePackage.video_project_id == video_project_id)
        ).all()
        metadata = self.session.scalars(
            select(LocalizedMetadataPackage).where(LocalizedMetadataPackage.video_project_id == video_project_id)
        ).all()
        return {
            "subtitle_languages": [item.target_language for item in subtitles],
            "metadata_languages": [item.language for item in metadata],
            "subtitle_review_status": {item.target_language: item.human_review_status for item in subtitles},
            "metadata_review_status": {item.language: item.human_review_status for item in metadata},
        }

    def _channel_analytics_freshness(self, channel_id: uuid.UUID) -> str:
        states = set(
            self.session.scalars(
                select(UploadedVideoMetricsSummary.freshness_state)
                .where(UploadedVideoMetricsSummary.channel_workspace_id == channel_id)
                .limit(20)
            ).all()
        )
        if "STALE" in states:
            return "STALE"
        if "UNKNOWN" in states or not states:
            return "UNKNOWN"
        return "CURRENT"

    def _drive_auth_needed(self) -> bool:
        connected = self.session.scalar(
            select(func.count()).select_from(GoogleDriveMediaCredential).where(GoogleDriveMediaCredential.connection_state == "CONNECTED")
        )
        return int(connected or 0) == 0

    def _youtube_auth_needed(self) -> bool:
        owner_connected = self.session.scalar(
            select(func.count())
            .select_from(YouTubeMonitoringCredential)
            .where(YouTubeMonitoringCredential.provider_key == "YOUTUBE_ANALYTICS_API", YouTubeMonitoringCredential.connection_state == "CONNECTED")
        )
        return int(owner_connected or 0) == 0

    def _count_blocked_gates(self) -> int:
        return self._count(RecoveryProposal, RecoveryProposal.risk_level.in_(["HIGH", "BLOCKED"]))

    def _count_channels_with_health(self, states: list[str]) -> int:
        channels = self.session.scalars(select(ChannelWorkspace)).all()
        return sum(1 for channel in channels if _metadata_health(channel) in states)

    def _count_provider_key_like(self, fragment: str) -> int:
        return int(
            self.session.scalar(
                select(func.count()).select_from(ProviderRegistryEntry).where(ProviderRegistryEntry.provider_key.ilike(f"%{fragment}%"))
            )
            or 0
        )

    def _cloud_final_renderer_state(self) -> str:
        count = int(
            self.session.scalar(
                select(func.count())
                .select_from(ProviderCapabilityMatrixEntry)
                .where(ProviderCapabilityMatrixEntry.provider_type == "CLOUD_FINAL_ASSEMBLY_RENDERER")
            )
            or 0
        )
        return "CONFIGURED" if count else "MISSING_REQUIRED_GAP"


class M11ChannelLifecycleService:
    def __init__(self, session: Session):
        self.session = session

    def decide(
        self,
        *,
        channel_id: uuid.UUID,
        data: ChannelLifecycleDecisionCreate,
        correlation_id: str = "m11-channel-lifecycle",
    ) -> ChannelLifecycleDecision:
        _require_permission(data.actor_role, "CHANNEL_LIFECYCLE")
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        previous = _lifecycle_from_channel(channel)
        new_state = _state_for_lifecycle_action(data.action, previous)
        health = data.health_status or _metadata_health(channel)
        next_action = _channel_next_action(new_state, health)
        channel.status = new_state.lower()
        metadata = dict(channel.metadata_ or {})
        metadata["m11_lifecycle_state"] = new_state
        metadata["m11_health_status"] = health
        metadata["m11_lifecycle_note"] = data.reason
        channel.metadata_ = metadata
        decision = ChannelLifecycleDecision(
            channel_workspace_id=channel.id,
            company_id=channel.company_id,
            previous_lifecycle_state=previous,
            lifecycle_state=new_state,
            health_status=health,
            action=data.action,
            reason=data.reason,
            next_action=next_action,
            decided_by_user_id=data.decided_by_user_id,
            decision_metadata={
                **data.metadata,
                "daily_generation_allowed": new_state == "ACTIVE",
                "no_auto_deactivation": True,
            },
        )
        self.session.add(decision)
        self.session.flush()
        _audit(
            self.session,
            action="channel.lifecycle_decision_recorded",
            target_type="channel_workspace",
            target_id=channel.id,
            company_id=channel.company_id,
            actor_id=data.decided_by_user_id,
            correlation_id=correlation_id,
            reason_code="M11_HUMAN_LIFECYCLE_DECISION",
            payload={"action": data.action, "previous_lifecycle_state": previous, "lifecycle_state": new_state},
        )
        _event(
            self.session,
            event_type="channel.lifecycle_decision_recorded",
            aggregate_type="channel_workspace",
            aggregate_id=channel.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={"decision_id": str(decision.id), "action": data.action, "lifecycle_state": new_state},
        )
        return decision


class M11LearningReviewService:
    def __init__(self, session: Session):
        self.session = session

    def decide(
        self,
        *,
        candidate_id: uuid.UUID,
        data: LearningReviewDecisionCreate,
        correlation_id: str = "m11-learning-review",
    ) -> LearningReviewDecision:
        _require_permission(data.actor_role, "LEARNING_REVIEW")
        candidate = self.session.get(LearningCandidate, candidate_id)
        if candidate is None:
            raise NotFoundError(f"learning candidate not found: {candidate_id}")
        queue = self.session.scalars(
            select(LearningReviewQueueItem)
            .where(LearningReviewQueueItem.learning_candidate_id == candidate.id)
            .order_by(LearningReviewQueueItem.created_at.desc())
            .limit(1)
        ).one_or_none()
        if queue is not None and data.action not in queue.approval_actions_allowed:
            raise ValidationFailureError(f"action is not allowed for this queue item: {data.action}")
        if data.action == "APPROVE" and candidate.candidate_state != "READY_FOR_HUMAN_REVIEW":
            raise ValidationFailureError("learning candidate is not ready for approval")
        bundle = self.session.get(LearningEvidenceBundle, candidate.evidence_bundle_id) if candidate.evidence_bundle_id else None
        draft = self.session.scalars(
            select(PlaybookCandidateDraft)
            .where(PlaybookCandidateDraft.learning_candidate_id == candidate.id)
            .order_by(PlaybookCandidateDraft.created_at.desc())
            .limit(1)
        ).one_or_none()
        decision = LearningReviewDecision(
            learning_candidate_id=candidate.id,
            learning_review_queue_item_id=queue.id if queue else None,
            evidence_bundle_id=bundle.id if bundle else None,
            playbook_candidate_draft_id=draft.id if draft else None,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            action=data.action,
            decision_state="RECORDED",
            actor_role=data.actor_role,
            decided_by_user_id=data.decided_by_user_id,
            rationale=data.rationale,
            reason_codes=_learning_decision_reason_codes(data.action),
            evidence_refs=_learning_evidence_refs(candidate, bundle),
            technical_appendix={
                "no_channel_profile_mutation": True,
                "no_config_upgrade_suggestion": True,
                "no_daily_workflow_change": True,
            },
        )
        self.session.add(decision)
        self.session.flush()
        approved_entry = None
        if data.action == "APPROVE":
            approved_entry = self._create_approved_playbook_entry(
                candidate=candidate,
                decision=decision,
                bundle=bundle,
                draft=draft,
                approved_by_user_id=data.decided_by_user_id,
            )
            decision.approved_playbook_entry_id = approved_entry.id
            if draft is not None:
                draft.state = "READY_FOR_REVIEW"
        else:
            if draft is not None and data.action in {"REJECT", "SUPPRESS", "EXPIRE"}:
                draft.state = "EXPIRED"
        candidate.candidate_state = LEARNING_ACTION_TO_CANDIDATE_STATE[data.action]
        candidate.friendly_status = _learning_friendly_status(data.action)
        if queue is not None:
            queue.queue_state = LEARNING_ACTION_TO_QUEUE_STATE[data.action]
            queue.next_action = _learning_next_action(data.action)
            queue.approval_actions_allowed = []
        self.session.flush()
        _audit(
            self.session,
            action="learning.review_decision_recorded",
            target_type="learning_candidate",
            target_id=candidate.id,
            company_id=candidate.company_id,
            actor_id=data.decided_by_user_id,
            correlation_id=correlation_id,
            reason_code=f"M11_LEARNING_{data.action}",
            payload={
                "action": data.action,
                "decision_id": str(decision.id),
                "approved_playbook_entry_id": str(approved_entry.id) if approved_entry else None,
                "no_channel_profile_mutation": True,
            },
        )
        _event(
            self.session,
            event_type="learning.review_decision_recorded",
            aggregate_type="learning_candidate",
            aggregate_id=candidate.id,
            company_id=candidate.company_id,
            correlation_id=correlation_id,
            payload={"decision_id": str(decision.id), "action": data.action, "approved_playbook_entry_id": str(approved_entry.id) if approved_entry else None},
        )
        return decision

    def _create_approved_playbook_entry(
        self,
        *,
        candidate: LearningCandidate,
        decision: LearningReviewDecision,
        bundle: LearningEvidenceBundle | None,
        draft: PlaybookCandidateDraft | None,
        approved_by_user_id: uuid.UUID | None,
    ) -> ApprovedPlaybookEntry:
        if draft is None:
            raise ValidationFailureError("approved learning requires a playbook candidate draft")
        entry = ApprovedPlaybookEntry(
            learning_candidate_id=candidate.id,
            learning_review_decision_id=decision.id,
            playbook_candidate_draft_id=draft.id,
            evidence_bundle_id=bundle.id if bundle else None,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            scope=candidate.recommended_scope,
            category=draft.playbook_category,
            playbook_text=draft.draft_text,
            evidence_refs=draft.evidence_refs or _learning_evidence_refs(candidate, bundle),
            limitations=candidate.limitations,
            counter_evidence=candidate.counter_evidence,
            policy_rights_summary=bundle.policy_rights_summary if bundle else {},
            state="APPROVED",
            approved_by_user_id=approved_by_user_id,
        )
        self.session.add(entry)
        self.session.flush()
        _event(
            self.session,
            event_type="approved_playbook_entry.created",
            aggregate_type="approved_playbook_entry",
            aggregate_id=entry.id,
            company_id=entry.company_id,
            correlation_id="m11-learning-review",
            payload={
                "learning_candidate_id": str(candidate.id),
                "evidence_bundle_id": str(bundle.id) if bundle else None,
                "no_channel_profile_mutation": True,
            },
        )
        return entry


def _action_card(key: str, title: str, count: int, severity: str, next_action: str, route: str) -> DashboardActionCard:
    return DashboardActionCard(key=key, title=title, count=count, severity=severity, next_action=next_action, route=route)


def _safety_warnings() -> list[DashboardWarning]:
    return [
        DashboardWarning(key="no_auto_publish", label="Không tự publish", severity="HARD_RULE", text="Bảng điều hành không upload/publish/reupload tự động."),
        DashboardWarning(key="no_fake_traffic", label="Không fake traffic", severity="HARD_RULE", text="Không bot engagement, fake views, IP/VPS tricks, hoặc platform evasion."),
        DashboardWarning(key="drive_cta_only", label="Chỉ dùng CTA Google Drive", severity="HARD_RULE", text="Media chỉ mở qua nút Google Drive đã xác minh; không tạo link tải hoặc preview trung gian."),
    ]


def _queue_label(queue_type: str) -> str:
    return {
        "learning": "Bài học chờ duyệt",
        "publish_confirmation": "Gói publish",
        "recovery": "Đề xuất phục hồi",
        "ops_manual_action": "Thao tác ops",
    }.get(queue_type, queue_type)


def _channel_dict(channel: ChannelWorkspace) -> dict[str, Any]:
    return {
        "id": channel.id,
        "company_id": channel.company_id,
        "key": channel.key,
        "name": channel.name,
        "status": channel.status,
        "primary_language": channel.primary_language,
        "primary_region": channel.primary_region,
        "primary_timezone": channel.primary_timezone,
        "target_market": channel.target_market,
        "default_timezone": channel.default_timezone,
        "target_subtitle_languages": channel.target_subtitle_languages,
        "target_metadata_languages": channel.target_metadata_languages,
        "target_regions": channel.target_regions,
        "translation_mode": channel.translation_mode,
        "localization_required_for_publish": channel.localization_required_for_publish,
        "localized_metadata_required": channel.localized_metadata_required,
        "active_policy_snapshot_id": channel.active_policy_snapshot_id,
        "metadata": channel.metadata_,
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _channel_summary(channel: ChannelWorkspace, lifecycle: ChannelLifecycleRead) -> dict[str, Any]:
    return {
        **_channel_dict(channel),
        "lifecycle_state": lifecycle.lifecycle_state,
        "health_status": lifecycle.health_status,
        "next_action": lifecycle.next_action,
        "daily_generation_allowed": lifecycle.daily_generation_allowed,
    }


def _channel_ref(channel: ChannelWorkspace | None) -> dict[str, Any] | None:
    if channel is None:
        return None
    return {"id": str(channel.id), "key": channel.key, "name": channel.name, "status": channel.status}


def _project_ref(project: VideoProject | None) -> dict[str, Any] | None:
    if project is None:
        return None
    return {"id": str(project.id), "title": project.title, "status": project.status}


def _project_card(project: VideoProject) -> dict[str, Any]:
    return {
        "id": project.id,
        "title": project.title,
        "current_stage": project.status,
        "next_action": "Tiếp tục workflow trong hàng chờ duyệt/sản xuất.",
        "policy_snapshot_id": project.policy_snapshot_id,
        "due_at": None,
    }


def _daily_run_card(run: ChannelDailyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "run_date": run.run_date,
        "run_state": run.run_state,
        "admission_state": run.admission_state,
        "next_action": run.next_action,
        "policy_snapshot_id": run.policy_snapshot_id,
    }


def _state_from_count(count: int, empty_state: str, non_empty_state: str) -> str:
    return non_empty_state if count else empty_state


def _lifecycle_from_channel(channel: ChannelWorkspace) -> str:
    metadata_state = (channel.metadata_ or {}).get("m11_lifecycle_state")
    if metadata_state:
        return str(metadata_state)
    return {
        "draft": "DRAFT",
        "ready": "READY",
        "active": "ACTIVE",
        "paused": "PAUSED",
        "deactivated": "DEACTIVATED",
        "archived": "ARCHIVED",
    }.get(channel.status.lower(), "DRAFT")


def _metadata_health(channel: ChannelWorkspace) -> str:
    value = (channel.metadata_ or {}).get("m11_health_status") or (channel.metadata_ or {}).get("health_status")
    return str(value or "NEW")


def _channel_next_action(lifecycle_state: str, health_status: str) -> str:
    if lifecycle_state == "DRAFT":
        return "Cần review policy snapshot trước khi activate channel."
    if lifecycle_state == "READY":
        return "Channel này đã sẵn sàng sản xuất video."
    if lifecycle_state == "ACTIVE" and health_status in {"LOW_VIEW", "NO_VIEW", "WATCHLIST"}:
        return "Tiếp tục quan sát hoặc mở diagnostic; lifecycle không tự đổi."
    if lifecycle_state == "ACTIVE":
        return "Tiếp tục daily generation và theo dõi bằng chứng."
    if lifecycle_state == "PAUSED":
        return "Channel đang PAUSED nên daily job sẽ không tạo video mới."
    if lifecycle_state == "DEACTIVATED":
        return "Channel đã DEACTIVATED nên VCOS không generate idea/project mới."
    return "Channel đã archive là read-only trừ khi được kích hoạt lại."


def _allowed_lifecycle_actions(lifecycle_state: str) -> list[str]:
    if lifecycle_state == "ACTIVE":
        return ["KEEP_ACTIVE", "PAUSE_DAILY_GENERATION", "CONTINUE_OBSERVING", "ADD_MANUAL_NOTE", "DEACTIVATE_CHANNEL", "ARCHIVE_CHANNEL"]
    if lifecycle_state in {"PAUSED", "DEACTIVATED", "ARCHIVED"}:
        return ["REACTIVATE_CHANNEL", "ADD_MANUAL_NOTE", "ARCHIVE_CHANNEL"]
    return ["KEEP_ACTIVE", "ADD_MANUAL_NOTE", "REACTIVATE_CHANNEL"]


def _state_for_lifecycle_action(action: str, previous: str) -> str:
    if action in {"KEEP_ACTIVE", "CONTINUE_OBSERVING"}:
        return "ACTIVE" if previous in {"READY", "ACTIVE"} else previous
    if action == "PAUSE_DAILY_GENERATION":
        return "PAUSED"
    if action == "DEACTIVATE_CHANNEL":
        return "DEACTIVATED"
    if action == "ARCHIVE_CHANNEL":
        return "ARCHIVED"
    if action == "REACTIVATE_CHANNEL":
        return "ACTIVE"
    return previous


def _channel_lifecycle_decision_dict(decision: ChannelLifecycleDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "id": str(decision.id),
        "action": decision.action,
        "lifecycle_state": decision.lifecycle_state,
        "health_status": decision.health_status,
        "next_action": decision.next_action,
        "created_at": decision.created_at.isoformat(),
    }


def _metrics_from_summary(
    summary: UploadedVideoMetricsSummary | None,
    public: UploadedVideoYouTubePublicMonitorSnapshot | None,
    owner: UploadedVideoYouTubeOwnerAnalyticsSnapshot | None,
) -> dict[str, Any]:
    metrics = dict(summary.metrics_summary if summary is not None else {})
    if public is not None:
        metrics.update({"views": public.views, "likes": public.likes, "comments": public.comments})
    if owner is not None:
        metrics.update(
            {
                "impressions": owner.impressions,
                "ctr": owner.impression_click_through_rate,
                "average_view_duration_seconds": owner.average_view_duration_seconds,
                "average_view_percentage": owner.average_view_percentage,
                "watch_time_minutes": owner.estimated_minutes_watched,
                "subscribers_gained": owner.subscribers_gained,
                "subscribers_lost": owner.subscribers_lost,
            }
        )
    return metrics


def _uploaded_video_dict(uploaded: UploadedVideo) -> dict[str, Any]:
    return {
        "id": uploaded.id,
        "company_id": uploaded.company_id,
        "channel_workspace_id": uploaded.channel_workspace_id,
        "video_project_id": uploaded.video_project_id,
        "platform": uploaded.platform,
        "platform_video_id": uploaded.platform_video_id,
        "video_url": uploaded.video_url,
        "published_at": uploaded.published_at,
        "publish_status": uploaded.publish_status,
        "actual_metadata": uploaded.actual_metadata,
        "actual_disclosures": uploaded.actual_disclosures,
        "monitoring_state": uploaded.monitoring_state,
    }


def _public_stats_card(snapshot: UploadedVideoYouTubePublicMonitorSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "source": "YouTube Data API",
            "authority": "WEAK",
            "views": None,
            "likes": None,
            "comments": None,
            "freshness": "UNKNOWN",
            "next_action": "Public stats chưa có dữ liệu; sync/import trước khi quyết định.",
        }
    return {
        "source": "YouTube Data API",
        "authority": "WEAK",
        "views": snapshot.views,
        "likes": snapshot.likes,
        "comments": snapshot.comments,
        "last_public_sync": snapshot.last_synced_at,
        "freshness": snapshot.freshness_state,
        "unknown_metrics": snapshot.unknown_metrics,
        "unavailable_metrics": snapshot.unavailable_metrics,
    }


def _owner_analytics_card(snapshot: UploadedVideoYouTubeOwnerAnalyticsSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "connection": "UNKNOWN",
            "authority": "STRONG",
            "impressions": None,
            "ctr": None,
            "average_view_duration_seconds": None,
            "average_view_percentage": None,
            "estimated_minutes_watched": None,
            "subscribers_gained": None,
            "subscribers_lost": None,
            "next_action": "Owner analytics chưa kết nối hoặc chưa sync. Chưa có dữ liệu không phải bằng 0.",
        }
    return {
        "connection": "CONNECTED",
        "authority": "STRONG",
        "impressions": snapshot.impressions,
        "ctr": snapshot.impression_click_through_rate,
        "average_view_duration_seconds": snapshot.average_view_duration_seconds,
        "average_view_percentage": snapshot.average_view_percentage,
        "estimated_minutes_watched": snapshot.estimated_minutes_watched,
        "subscribers_gained": snapshot.subscribers_gained,
        "subscribers_lost": snapshot.subscribers_lost,
        "last_owner_analytics_sync": snapshot.last_synced_at,
        "metric_availability": snapshot.metric_availability,
    }


def _publish_check(snapshot: UploadedVideoYouTubePublicMonitorSnapshot | None) -> dict[str, str]:
    if snapshot is None:
        return {"title_match": "UNKNOWN", "duration_match": "UNKNOWN", "captions": "UNKNOWN", "visibility": "UNKNOWN"}
    return {
        "title_match": "OK" if snapshot.title_matches_confirmed_metadata else "CHANGED" if snapshot.title_matches_confirmed_metadata is False else "UNKNOWN",
        "duration_match": "OK" if snapshot.duration_matches_render_package else "REVIEW" if snapshot.duration_matches_render_package is False else "UNKNOWN",
        "captions": "AVAILABLE" if snapshot.caption_status == "true" else "UNKNOWN" if snapshot.caption_status is None else "NOT_AVAILABLE",
        "visibility": str(snapshot.privacy_status or "UNKNOWN").upper(),
    }


def _failure_report_card(report: FailureTraceReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "primary_status": report.primary_status,
        "primary_suspected_cause": report.primary_suspected_cause,
        "confidence_level": report.confidence_level,
        "severity": report.severity,
        "operator_summary": report.operator_summary,
        "next_action": report.next_action,
        "do_not_do": report.do_not_do,
    }


def _recovery_card(proposal: RecoveryProposal) -> dict[str, Any]:
    return {
        "id": proposal.id,
        "proposal_type": proposal.proposal_type,
        "proposal_state": proposal.proposal_state,
        "operator_summary": proposal.operator_summary,
        "recommended_actions": proposal.recommended_actions,
        "do_not_do": proposal.do_not_do,
        "risk_level": proposal.risk_level,
        "requires_human_approval": proposal.requires_human_approval,
    }


def _learning_candidate_card(candidate: LearningCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "candidate_state": candidate.candidate_state,
        "candidate_summary": candidate.candidate_summary,
        "confidence_label": candidate.confidence_label,
        "risk_level": candidate.risk_level,
        "recommended_scope": candidate.recommended_scope,
        "limitations": candidate.limitations,
        "counter_evidence": candidate.counter_evidence,
        "next_action": "Xem evidence bundle trước khi đưa vào playbook.",
    }


def _cloud_media_card(ref: CloudMediaRef) -> dict[str, Any]:
    return {
        "id": ref.id,
        "storage": "Google Drive",
        "media_type": ref.media_type,
        "status": ref.upload_status,
        "cta_label": "Mở trên Google Drive",
        "web_view_link": ref.web_view_link,
        "file_size": ref.size_bytes,
        "uploaded_at": ref.uploaded_at,
        "cleanup_status": ref.local_cleanup_status,
        "verification_status": ref.verification_status,
        "friendly_error": None if ref.upload_status == "VERIFIED" else "Không mở được file trên Google Drive. Cần kiểm tra quyền hoặc re-upload.",
        "technical_appendix": {"no_local_path": True, "no_backend_download": True, "no_preview_proxy": True},
    }


def _learning_decision_reason_codes(action: str) -> list[str]:
    codes = [f"M11_LEARNING_{action}", "NO_CHANNEL_PROFILE_MUTATION", "NO_CONFIG_UPGRADE_SUGGESTION"]
    if action == "APPROVE":
        codes.append("APPROVED_PLAYBOOK_ENTRY_CREATED")
    return codes


def _learning_evidence_refs(candidate: LearningCandidate, bundle: LearningEvidenceBundle | None) -> list[dict[str, Any]]:
    refs = list(candidate.source_refs or [])
    if bundle is not None:
        refs.append({"type": "learning_evidence_bundle", "id": str(bundle.id)})
        refs.extend(bundle.source_video_refs or [])
        refs.extend(bundle.analytics_snapshot_refs or [])
    return refs


def _learning_friendly_status(action: str) -> str:
    return {
        "APPROVE": "Bài học đã được approve thành playbook entry, chưa tự đổi channel config.",
        "REJECT": "Bài học đã bị reject.",
        "REQUEST_MORE_EVIDENCE": "Bài học này chưa đủ bằng chứng để đưa vào playbook.",
        "SUPPRESS": "Bài học đã được suppress khỏi queue.",
        "EXPIRE": "Bài học đã hết hạn.",
    }[action]


def _learning_next_action(action: str) -> str:
    return {
        "APPROVE": "Có thể dùng playbook entry khi human chỉnh profile/policy thủ công sau này.",
        "REJECT": "Không dùng bài học này.",
        "REQUEST_MORE_EVIDENCE": "Chờ thêm analytics/diagnostic evidence.",
        "SUPPRESS": "Không hiển thị lại trừ khi tạo candidate mới.",
        "EXPIRE": "Tạo candidate mới nếu evidence thay đổi.",
    }[action]


def _require_permission(actor_role: str, permission: str) -> None:
    permissions = ROLE_PERMISSIONS.get(actor_role, set())
    if "*" in permissions or permission in permissions:
        return
    raise ForbiddenError(f"role {actor_role} cannot perform {permission}")


def _audit(
    session: Session,
    *,
    action: str,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    actor_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
) -> None:
    AuditService(session).append(
        AuditEnvelope(
            actor_type="human" if actor_id else "system",
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            reason_code=reason_code,
            correlation_id=correlation_id,
            payload=payload,
        ),
        company_id=company_id,
    )


def _event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    payload: dict[str, Any],
) -> None:
    DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            correlation_id=correlation_id,
            payload=payload,
        ),
        company_id=company_id,
    )


def channel_lifecycle_decision_read(decision: ChannelLifecycleDecision) -> ChannelLifecycleDecisionRead:
    return ChannelLifecycleDecisionRead(
        id=decision.id,
        channel_workspace_id=decision.channel_workspace_id,
        company_id=decision.company_id,
        previous_lifecycle_state=decision.previous_lifecycle_state,
        lifecycle_state=decision.lifecycle_state,
        health_status=decision.health_status,
        action=decision.action,
        reason=decision.reason,
        next_action=decision.next_action,
        decided_by_user_id=decision.decided_by_user_id,
        decision_metadata=decision.decision_metadata,
        created_at=decision.created_at,
    )


def approved_playbook_entry_read(entry: ApprovedPlaybookEntry) -> ApprovedPlaybookEntryRead:
    return ApprovedPlaybookEntryRead(
        id=entry.id,
        learning_candidate_id=entry.learning_candidate_id,
        learning_review_decision_id=entry.learning_review_decision_id,
        playbook_candidate_draft_id=entry.playbook_candidate_draft_id,
        evidence_bundle_id=entry.evidence_bundle_id,
        company_id=entry.company_id,
        channel_workspace_id=entry.channel_workspace_id,
        scope=entry.scope,
        category=entry.category,
        playbook_text=entry.playbook_text,
        evidence_refs=entry.evidence_refs,
        limitations=entry.limitations,
        counter_evidence=entry.counter_evidence,
        policy_rights_summary=entry.policy_rights_summary,
        state=entry.state,
        approved_by_user_id=entry.approved_by_user_id,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def learning_review_decision_read(session: Session, decision: LearningReviewDecision) -> LearningReviewDecisionRead:
    entry = session.get(ApprovedPlaybookEntry, decision.approved_playbook_entry_id) if decision.approved_playbook_entry_id else None
    return LearningReviewDecisionRead(
        id=decision.id,
        learning_candidate_id=decision.learning_candidate_id,
        learning_review_queue_item_id=decision.learning_review_queue_item_id,
        evidence_bundle_id=decision.evidence_bundle_id,
        playbook_candidate_draft_id=decision.playbook_candidate_draft_id,
        approved_playbook_entry_id=decision.approved_playbook_entry_id,
        company_id=decision.company_id,
        channel_workspace_id=decision.channel_workspace_id,
        action=decision.action,
        decision_state=decision.decision_state,
        actor_role=decision.actor_role,
        decided_by_user_id=decision.decided_by_user_id,
        rationale=decision.rationale,
        reason_codes=decision.reason_codes,
        evidence_refs=decision.evidence_refs,
        technical_appendix=decision.technical_appendix,
        created_at=decision.created_at,
        approved_playbook_entry=approved_playbook_entry_read(entry) if entry is not None else None,
    )
