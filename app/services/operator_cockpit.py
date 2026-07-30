"""Operator-facing projections over the Phase 4/5 production authorities."""

from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.contracts.operator_cockpit import (
    FinalReviewMediaRead,
    FinalReviewRead,
    ManualPublishRead,
    NextVideoRead,
    ProductionCockpitRead,
    ProductionProgressRead,
    WorkflowStageProgressRead,
)
from app.core.actor import ActorContext
from app.core.errors import NotFoundError
from app.core.time import utc_now
from app.db.models.channel import ChannelWorkspace
from app.db.models.foundation import DomainEvent
from app.db.models.m10_1 import HumanUploadTask
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m7 import ManualPublishConfirmation, UploadedVideo
from app.db.models.ops import CostEvent, OpsIncident, ProviderAttempt
from app.db.models.production_publish import FinalReviewCandidate, FinalVideoDecision
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.vcos_v2 import SeriesPlan, SeriesRun
from app.db.models.workflow import VideoProject
from app.services.company_access import (
    accessible_company_ids,
    require_company_permission,
)


TERMINAL_WORKFLOW_STATES = {
    "CANCELED",
    "FAILED_TERMINAL",
    "DEAD_LETTERED",
}
RESOLVED_INCIDENT_STATES = {"RESOLVED", "CLOSED", "CANCELED"}


class OperatorCockpitService:
    """Build one safe, deterministic operator projection.

    This service is read-only. It never advances a workflow, changes a final
    decision, creates an upload task, or invokes a provider.
    """

    def __init__(self, session: Session):
        self.session = session

    def build(
        self,
        *,
        actor: ActorContext,
        project_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
        channel_workspace_id: uuid.UUID | None = None,
    ) -> ProductionCockpitRead:
        permitted_company_ids = accessible_company_ids(
            self.session,
            actor=actor,
            permission="production.read",
        )
        if company_id is not None:
            require_company_permission(
                self.session,
                actor=actor,
                permission="production.read",
                company_id=company_id,
            )
        project, run = self._select_project_and_run(
            project_id=project_id,
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            permitted_company_ids=permitted_company_ids,
        )
        if project_id is not None and project is None:
            raise NotFoundError("video project not found")
        if project is None:
            return ProductionCockpitRead(
                generated_at=utc_now(),
                technical_appendix={
                    "scope": {
                        "company_id": company_id,
                        "channel_workspace_id": channel_workspace_id,
                    }
                },
            )

        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        series_plan = (
            self.session.get(SeriesPlan, project.series_plan_id)
            if project.series_plan_id
            else None
        )
        series_run = (
            self.session.get(SeriesRun, project.series_run_id)
            if project.series_run_id
            else None
        )
        incident = self._blocking_incident(project=project, run=run)
        candidate = self._candidate(project=project, run=run)
        decision = self._decision(candidate)
        task = self._upload_task(project=project, candidate=candidate)
        confirmation = self._confirmation(task)
        uploaded_video = self._uploaded_video(task=task, confirmation=confirmation)
        costs = self._cost_summary(project=project, run=run)
        provider_attempt = self._latest_provider_attempt(project=project, run=run)

        next_video = self._next_video(
            project=project,
            run=run,
            channel=channel,
            series_plan=series_plan,
            series_run=series_run,
            incident=incident,
            decision=decision,
            task=task,
            costs=costs,
            provider_attempt=provider_attempt,
        )
        progress = (
            self._progress(
                project=project,
                run=run,
                incident=incident,
                costs=costs,
                provider_attempt=provider_attempt,
            )
            if run is not None
            else None
        )
        final_review = (
            self._final_review(
                project=project,
                run=run,
                channel=channel,
                candidate=candidate,
                decision=decision,
                series_plan=series_plan,
                series_run=series_run,
            )
            if run is not None and candidate is not None
            else None
        )
        manual_publish = (
            self._manual_publish(
                project=project,
                channel=channel,
                candidate=candidate,
                task=task,
                confirmation=confirmation,
                uploaded_video=uploaded_video,
            )
            if candidate is not None and task is not None
            else None
        )
        return ProductionCockpitRead(
            generated_at=utc_now(),
            next_video=next_video,
            progress=progress,
            final_review=final_review,
            manual_publish=manual_publish,
            technical_appendix={
                "company_id": project.company_id,
                "channel_workspace_id": project.channel_workspace_id,
                "projection_source": "PHASE4_PHASE5_AUTHORITIES",
            },
        )

    def _select_project_and_run(
        self,
        *,
        project_id: uuid.UUID | None,
        company_id: uuid.UUID | None,
        channel_workspace_id: uuid.UUID | None,
        permitted_company_ids: set[uuid.UUID] | None,
    ) -> tuple[VideoProject | None, ProductionWorkflowRun | None]:
        run_statement = select(ProductionWorkflowRun)
        if permitted_company_ids is not None:
            if not permitted_company_ids:
                return None, None
            run_statement = run_statement.where(
                ProductionWorkflowRun.company_id.in_(permitted_company_ids)
            )
        if project_id is not None:
            run_statement = run_statement.where(
                ProductionWorkflowRun.video_project_id == project_id
            )
        if company_id is not None:
            run_statement = run_statement.where(
                ProductionWorkflowRun.company_id == company_id
            )
        if channel_workspace_id is not None:
            run_statement = run_statement.where(
                ProductionWorkflowRun.channel_workspace_id == channel_workspace_id
            )
        run_statement = run_statement.order_by(
            case(
                (
                    ProductionWorkflowRun.state.in_(TERMINAL_WORKFLOW_STATES),
                    1,
                ),
                else_=0,
            ),
            ProductionWorkflowRun.last_progress_at.desc(),
            ProductionWorkflowRun.created_at.desc(),
        )
        run = self.session.scalars(run_statement).first()
        if run is not None and run.video_project_id is not None:
            return self.session.get(VideoProject, run.video_project_id), run

        project_statement = select(VideoProject).where(
            VideoProject.schema_version == "v2"
        )
        if permitted_company_ids is not None:
            project_statement = project_statement.where(
                VideoProject.company_id.in_(permitted_company_ids)
            )
        if project_id is not None:
            project_statement = project_statement.where(VideoProject.id == project_id)
        if company_id is not None:
            project_statement = project_statement.where(
                VideoProject.company_id == company_id
            )
        if channel_workspace_id is not None:
            project_statement = project_statement.where(
                VideoProject.channel_workspace_id == channel_workspace_id
            )
        project_statement = project_statement.order_by(
            case(
                (VideoProject.priority == "CRITICAL", 0),
                (VideoProject.priority == "HIGH", 1),
                else_=2,
            ),
            VideoProject.created_at.desc(),
        )
        return self.session.scalars(project_statement).first(), None

    def _next_video(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun | None,
        channel: ChannelWorkspace | None,
        series_plan: SeriesPlan | None,
        series_run: SeriesRun | None,
        incident: OpsIncident | None,
        decision: FinalVideoDecision | None,
        task: HumanUploadTask | None,
        costs: dict[str, Any],
        provider_attempt: ProviderAttempt | None,
    ) -> NextVideoRead:
        metadata = run.metadata_ if run is not None else {}
        state = run.state if run is not None else project.status
        archive_state = (
            run.archive_verification_state or "NOT_STARTED"
            if run is not None
            else "NOT_STARTED"
        )
        operator_action = _operator_action(
            run=run,
            incident=incident,
            decision=decision,
            task=task,
        )
        return NextVideoRead(
            project_id=project.id,
            workflow_run_id=run.id if run is not None else None,
            lane=project.production_lane or (run.production_lane if run else "UNKNOWN"),
            content_mode=project.content_mode or "UNKNOWN",
            assignment_mode=project.assignment_mode or "UNKNOWN",
            title=project.title,
            topic=_first_text(
                metadata,
                "topic",
                "topic_summary",
                default=project.description,
            ),
            series_title=series_plan.display_name if series_plan is not None else None,
            run_label=(
                f"Đợt {series_run.run_number}" if series_run is not None else None
            ),
            episode_label=(
                f"Tập {project.episode_number}" if project.episode_number else None
            ),
            standalone_reason=(
                _friendly_standalone_reason(project.standalone_reason_code)
                if project.content_mode == "STANDALONE"
                else None
            ),
            why_selected=_why_selected(project, metadata),
            production_state=state,
            current_stage=run.current_stage if run is not None else None,
            blocker=_friendly_blocker(incident, run),
            next_action=_next_action_text(
                action=operator_action,
                run=run,
                incident=incident,
            ),
            destination_label=channel.name
            if channel is not None
            else "Kênh đã cấu hình",
            destination_handle=_destination_handle(run),
            estimated_cost=costs["estimated"],
            actual_cost_so_far=costs["actual"],
            currency=costs["currency"],
            provider_status=(
                provider_attempt.status
                if provider_attempt is not None
                else _provider_status(run)
            ),
            render_status=_render_status(run),
            archive_status=archive_state,
            incident_status=incident.state if incident is not None else "NO_INCIDENT",
            operator_action=operator_action,
            technical_appendix={
                "project_id": project.id,
                "workflow_run_id": run.id if run is not None else None,
                "project_admission_decision_id": project.project_admission_decision_id,
                "policy_snapshot_id": project.policy_snapshot_id,
                "channel_profile_version_id": project.channel_profile_version_id,
                "duration_contract": project.duration_contract,
                "state_reason_codes": run.state_reason_codes if run else [],
                "incident_id": incident.id if incident else None,
            },
        )

    def _progress(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun,
        incident: OpsIncident | None,
        costs: dict[str, Any],
        provider_attempt: ProviderAttempt | None,
    ) -> ProductionProgressRead:
        receipts = list(
            self.session.scalars(
                select(WorkflowCommandReceipt)
                .where(WorkflowCommandReceipt.workflow_run_id == run.id)
                .order_by(
                    WorkflowCommandReceipt.started_at,
                    WorkflowCommandReceipt.created_at,
                )
            )
        )
        events = list(
            self.session.scalars(
                select(DomainEvent)
                .where(DomainEvent.workflow_run_id == run.id)
                .order_by(DomainEvent.created_at)
            )
        )
        stages = _stage_projection(receipts=receipts, events=events, run=run)
        retry_count = max((event.attempt_count for event in events), default=0)
        next_retry = min(
            (
                event.next_attempt_at
                for event in events
                if event.next_attempt_at is not None
                and event.delivered_at is None
                and event.dead_lettered_at is None
            ),
            default=None,
        )
        metadata = run.metadata_ or {}
        return ProductionProgressRead(
            workflow_run_id=run.id,
            project_id=project.id,
            state=run.state,
            active_stage=(
                None
                if run.state in TERMINAL_WORKFLOW_STATES
                or run.state == "FINAL_REVIEW_READY"
                else run.current_stage
            ),
            started_at=run.started_at,
            finished_at=run.completed_at or run.canceled_at,
            retry_count=retry_count,
            next_retry_at=next_retry,
            lease_health=_lease_health(events),
            provider_status=(
                provider_attempt.status
                if provider_attempt is not None
                else _provider_status(run)
            ),
            budget_status=_budget_status(metadata, costs),
            estimated_cost=costs["estimated"],
            reserved_cost=_number_from(
                metadata,
                "budget_reserved",
                "reserved_cost",
                "budget_reservation_amount",
            ),
            settled_cost=_number_from(
                metadata,
                "budget_settled",
                "settled_cost",
                default=costs["actual"],
            ),
            currency=costs["currency"],
            render_status=_render_status(run),
            render_progress_percent=_render_progress(run),
            qc_status=_qc_status(run),
            archive_status=run.archive_verification_state or "NOT_STARTED",
            blocking_incident=(
                incident.operator_visible_blocker
                or _friendly_reason_codes(incident.reason_codes)
                if incident is not None
                else None
            ),
            next_action=_next_action_text(
                action=_operator_action(
                    run=run,
                    incident=incident,
                    decision=None,
                    task=None,
                ),
                run=run,
                incident=incident,
            ),
            operator_action=_operator_action(
                run=run,
                incident=incident,
                decision=None,
                task=None,
            ),
            stages=stages,
            technical_appendix={
                "workflow_key": run.workflow_key,
                "projection_version": run.projection_version,
                "package_artifact_version_id": (
                    run.production_package_artifact_version_id
                ),
                "package_hash": run.production_package_hash,
                "readiness_receipt_artifact_version_id": (
                    run.production_readiness_receipt_artifact_version_id
                ),
                "readiness_receipt_hash": run.production_readiness_receipt_hash,
                "timeline_hash": run.canonical_media_timeline_hash,
                "render_plan_hash": run.native_render_plan_hash,
                "render_output_checksum": run.render_output_checksum,
                "technical_qc_receipt_hash": run.technical_qc_receipt_hash,
                "creative_qc_receipt_hash": run.creative_qc_receipt_hash,
                "archive_receipt_hash": run.archive_receipt_hash,
                "final_media_ref_id": run.final_media_ref_id,
                "final_media_ref_hash": run.final_media_ref_hash,
                "command_receipt_ids": [receipt.id for receipt in receipts],
                "domain_event_ids": [event.id for event in events],
                "incident_id": incident.id if incident else None,
                "learning_excluded": (
                    incident.learning_excluded if incident is not None else False
                ),
            },
        )

    def _final_review(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun,
        channel: ChannelWorkspace | None,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision | None,
        series_plan: SeriesPlan | None,
        series_run: SeriesRun | None,
    ) -> FinalReviewRead:
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        cloud_media = (
            self.session.get(CloudMediaRef, final_media.cloud_media_ref_id)
            if final_media is not None and final_media.cloud_media_ref_id
            else None
        )
        metadata = candidate.publish_metadata_snapshot or {}
        disclosures = candidate.disclosure_snapshot or {}
        materiality = candidate.materiality_policy_snapshot or {}
        title = _first_text(metadata, "title", "title_text", default=project.title)
        description = _first_text(
            metadata,
            "description",
            "description_text",
            default=project.description or "Chưa có mô tả.",
        )
        checksum = candidate.render_output_checksum
        duration_seconds = (
            float(final_media.duration_seconds)
            if final_media is not None and final_media.duration_seconds is not None
            else _duration_from_contract(project.duration_contract)
        )
        local_player_url, local_thumbnail_url = _local_review_urls(
            candidate=candidate,
            final_media=final_media,
            cloud_media=cloud_media,
        )
        thumbnail_url = local_thumbnail_url or _https_url(
            _first_text(
                metadata,
                "thumbnail_url",
                "thumbnail_web_view_url",
            )
        )
        player_url = local_player_url or _https_url(
            _first_text(
                cloud_media.technical_appendix if cloud_media else {},
                "player_url",
                "stream_url",
            )
        )
        return FinalReviewRead(
            candidate_id=candidate.id,
            project_id=project.id,
            workflow_run_id=run.id,
            state="DECIDED" if decision is not None else "READY_FOR_FINAL_REVIEW",
            title=title,
            description=description,
            lane=candidate.production_lane,
            content_mode=candidate.content_mode,
            series_title=series_plan.display_name if series_plan is not None else None,
            run_label=(
                f"Đợt {series_run.run_number}" if series_run is not None else None
            ),
            episode_label=(
                f"Tập {candidate.episode_number}"
                if candidate.episode_number is not None
                else None
            ),
            standalone_reason=(
                _friendly_standalone_reason(candidate.standalone_reason_code)
                if candidate.content_mode == "STANDALONE"
                else None
            ),
            destination_label=channel.name if channel else "Kênh đã cấu hình",
            destination_handle=_friendly_destination_identity(
                candidate.destination_account_identity
            ),
            media=FinalReviewMediaRead(
                file_name=_safe_file_name(
                    cloud_media.file_name if cloud_media else None,
                    final_media.file_ref if final_media else None,
                ),
                player_url=player_url,
                drive_web_view_url=(
                    _https_url(cloud_media.web_view_link) if cloud_media else None
                ),
                thumbnail_url=thumbnail_url,
                captions_label=_captions_label(metadata),
                checksum_sha256=checksum,
                duration_seconds=duration_seconds,
            ),
            warnings=_friendly_warnings(metadata, disclosures),
            rights_disclosure_summary=_rights_summary(disclosures),
            auto_repair_summary=_repair_summary(metadata, materiality),
            archive_status=candidate.archive_verification_state,
            decision=decision.decision if decision is not None else None,
            decision_recorded_at=(
                decision.decision_timestamp if decision is not None else None
            ),
            technical_appendix={
                "final_media_ref_id": candidate.final_media_ref_id,
                "production_package_artifact_version_id": (
                    candidate.production_package_artifact_version_id
                ),
                "production_package_hash": candidate.production_package_hash,
                "readiness_receipt_artifact_version_id": (
                    candidate.production_readiness_receipt_artifact_version_id
                ),
                "readiness_receipt_hash": (candidate.production_readiness_receipt_hash),
                "canonical_media_timeline_hash": (
                    candidate.canonical_media_timeline_hash
                ),
                "native_render_plan_hash": candidate.native_render_plan_hash,
                "technical_qc_receipt_hash": candidate.technical_qc_receipt_hash,
                "creative_qc_receipt_hash": candidate.creative_qc_receipt_hash,
                "archive_receipt_hash": candidate.archive_receipt_hash,
                "archive_object_ref": _safe_external_ref(candidate.archive_object_ref),
                "destination_binding_id": candidate.destination_binding_id,
                "destination_binding_fingerprint": (
                    candidate.destination_binding_fingerprint
                ),
                "destination_platform_channel_id": (
                    candidate.destination_platform_channel_id
                ),
                "candidate_hash": candidate.candidate_hash,
                "warning_codes": _technical_warning_codes(metadata, disclosures),
                "decision_id": decision.id if decision else None,
                "decision_hash": decision.decision_hash if decision else None,
            },
        )

    def _manual_publish(
        self,
        *,
        project: VideoProject,
        channel: ChannelWorkspace | None,
        candidate: FinalReviewCandidate,
        task: HumanUploadTask,
        confirmation: ManualPublishConfirmation | None,
        uploaded_video: UploadedVideo | None,
    ) -> ManualPublishRead:
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        cloud_media = (
            self.session.get(CloudMediaRef, final_media.cloud_media_ref_id)
            if final_media is not None and final_media.cloud_media_ref_id
            else None
        )
        actual_metadata = confirmation.actual_metadata if confirmation else {}
        mismatch_state, correction_state = _confirmation_states(confirmation)
        return ManualPublishRead(
            task_id=task.id,
            project_id=project.id,
            final_review_candidate_id=candidate.id,
            state=task.task_state,
            exact_file_name=(
                task.selected_file_name
                or _safe_file_name(
                    cloud_media.file_name if cloud_media else None,
                    task.final_media_file_ref,
                )
            ),
            drive_web_view_url=(
                _https_url(cloud_media.web_view_link) if cloud_media else None
            ),
            verified_file_download_url=(
                f"/final-review-candidates/{candidate.id}/media?download=1"
                if _is_verified_local_archive(
                    candidate=candidate,
                    final_media=final_media,
                    cloud_media=cloud_media,
                )
                else None
            ),
            reviewed_checksum_sha256=(
                task.reviewed_checksum or candidate.render_output_checksum
            ),
            target_platform=task.target_platform,
            destination_label=channel.name if channel else "Kênh đã cấu hình",
            destination_channel_id=candidate.destination_platform_channel_id,
            destination_handle=_friendly_destination_identity(
                candidate.destination_account_identity
            ),
            platform_video_id=(
                uploaded_video.platform_video_id
                if uploaded_video is not None
                else confirmation.actual_video_id
                if confirmation is not None
                else None
            ),
            platform_video_url=(
                _https_url(uploaded_video.video_url)
                if uploaded_video is not None
                else _https_url(confirmation.actual_video_url)
                if confirmation is not None
                else None
            ),
            actual_title=(
                uploaded_video.actual_title
                if uploaded_video is not None
                else _first_text(actual_metadata, "title", "actual_title")
            ),
            actual_description=_first_text(
                actual_metadata,
                "description",
                "actual_description",
            ),
            actual_visibility=(
                uploaded_video.actual_visibility
                if uploaded_video is not None
                else _first_text(actual_metadata, "visibility", "actual_visibility")
            ),
            actual_published_at=(
                uploaded_video.published_at
                if uploaded_video is not None
                else confirmation.actual_published_at
                if confirmation is not None
                else None
            ),
            actual_duration_seconds=(
                float(confirmation.actual_duration_seconds)
                if confirmation is not None
                and confirmation.actual_duration_seconds is not None
                else None
            ),
            mismatch_state=mismatch_state,
            correction_state=correction_state,
            uploaded_video_id=uploaded_video.id if uploaded_video is not None else None,
            uploaded_video_status=(
                uploaded_video.verification_status
                if uploaded_video is not None
                else "NOT_RECORDED"
            ),
            analytics_ready=bool(
                uploaded_video is not None
                and uploaded_video.analytics_sync_status == "READY"
                and uploaded_video.analytics_ready_at is not None
            ),
            next_action=_publish_next_action(
                task=task,
                confirmation=confirmation,
                uploaded_video=uploaded_video,
            ),
            technical_appendix={
                "final_video_decision_id": task.final_video_decision_id,
                "final_media_ref_id": task.final_media_ref_id,
                "production_package_artifact_version_id": (
                    task.production_package_artifact_version_id
                ),
                "production_package_hash": task.production_package_hash,
                "destination_binding_id": task.destination_binding_id,
                "destination_binding_fingerprint": (
                    task.destination_binding_fingerprint
                ),
                "destination_account_identity": (
                    candidate.destination_account_identity
                ),
                "archive_object_ref": _safe_external_ref(task.archive_object_ref),
                "channel_profile_version_id": task.channel_profile_version_id,
                "policy_snapshot_id": task.policy_snapshot_id,
                "confirmation_id": confirmation.id if confirmation else None,
                "confirmation_hash": (
                    confirmation.confirmation_hash if confirmation else None
                ),
                "confirmation_state": (
                    confirmation.confirmation_state if confirmation else None
                ),
                "uploaded_video_id": (
                    uploaded_video.id if uploaded_video is not None else None
                ),
                "analytics_ready_event_id": (
                    uploaded_video.analytics_ready_event_id
                    if uploaded_video is not None
                    else None
                ),
            },
        )

    def _candidate(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun | None,
    ) -> FinalReviewCandidate | None:
        if run is not None and run.final_review_candidate_id is not None:
            candidate = self.session.get(
                FinalReviewCandidate,
                run.final_review_candidate_id,
            )
            if candidate is not None:
                return candidate
        return self.session.scalars(
            select(FinalReviewCandidate)
            .where(FinalReviewCandidate.video_project_id == project.id)
            .order_by(FinalReviewCandidate.created_at.desc())
        ).first()

    def _decision(
        self,
        candidate: FinalReviewCandidate | None,
    ) -> FinalVideoDecision | None:
        if candidate is None:
            return None
        return self.session.scalars(
            select(FinalVideoDecision)
            .where(FinalVideoDecision.final_review_candidate_id == candidate.id)
            .order_by(FinalVideoDecision.created_at.desc())
        ).first()

    def _upload_task(
        self,
        *,
        project: VideoProject,
        candidate: FinalReviewCandidate | None,
    ) -> HumanUploadTask | None:
        statement = (
            select(HumanUploadTask)
            .where(
                HumanUploadTask.schema_version == "v2",
                HumanUploadTask.video_project_id == project.id,
            )
            .order_by(HumanUploadTask.created_at.desc())
        )
        if candidate is not None:
            statement = statement.where(
                HumanUploadTask.final_review_candidate_id == candidate.id
            )
        return self.session.scalars(statement).first()

    def _confirmation(
        self,
        task: HumanUploadTask | None,
    ) -> ManualPublishConfirmation | None:
        if task is None:
            return None
        return self.session.scalars(
            select(ManualPublishConfirmation)
            .where(
                ManualPublishConfirmation.schema_version == "v2",
                ManualPublishConfirmation.human_upload_task_id == task.id,
            )
            .order_by(ManualPublishConfirmation.created_at.desc())
        ).first()

    def _uploaded_video(
        self,
        *,
        task: HumanUploadTask | None,
        confirmation: ManualPublishConfirmation | None,
    ) -> UploadedVideo | None:
        if task is None:
            return None
        if task.actual_uploaded_video_id is not None:
            uploaded = self.session.get(UploadedVideo, task.actual_uploaded_video_id)
            if uploaded is not None:
                return uploaded
        statement = select(UploadedVideo).where(
            UploadedVideo.schema_version == "v2",
            UploadedVideo.human_upload_task_id == task.id,
        )
        if confirmation is not None:
            statement = statement.where(
                UploadedVideo.manual_publish_confirmation_id == confirmation.id
            )
        return self.session.scalars(
            statement.order_by(UploadedVideo.created_at.desc())
        ).first()

    def _blocking_incident(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun | None,
    ) -> OpsIncident | None:
        conditions = [OpsIncident.project_id == project.id]
        if run is not None:
            conditions.append(OpsIncident.workflow_run_id == run.id)
        return self.session.scalars(
            select(OpsIncident)
            .where(
                or_(*conditions),
                OpsIncident.state.not_in(RESOLVED_INCIDENT_STATES),
            )
            .order_by(
                case(
                    (OpsIncident.severity == "CRITICAL", 0),
                    (OpsIncident.severity == "HIGH", 1),
                    else_=2,
                ),
                OpsIncident.created_at.desc(),
            )
        ).first()

    def _latest_provider_attempt(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun | None,
    ) -> ProviderAttempt | None:
        target_ids = [project.id]
        if run is not None:
            target_ids.append(run.id)
        return self.session.scalars(
            select(ProviderAttempt)
            .where(ProviderAttempt.target_id.in_(target_ids))
            .order_by(ProviderAttempt.started_at.desc())
        ).first()

    def _cost_summary(
        self,
        *,
        project: VideoProject,
        run: ProductionWorkflowRun | None,
    ) -> dict[str, Any]:
        scope_ids = [project.id]
        if run is not None:
            scope_ids.append(run.id)
        rows = list(
            self.session.execute(
                select(
                    CostEvent.currency,
                    func.coalesce(func.sum(CostEvent.amount), 0),
                )
                .where(CostEvent.cost_scope_id.in_(scope_ids))
                .group_by(CostEvent.currency)
                .order_by(CostEvent.currency)
            )
        )
        currency = (
            rows[0][0]
            if rows
            else _first_text(
                project.financial_summary,
                "currency",
                default="USD",
            )
        )
        actual = float(rows[0][1]) if rows else 0.0
        estimated = _number_from(
            project.financial_summary,
            "estimated_cost",
            "estimated_total",
            "cost_estimate",
        )
        return {
            "currency": currency or "USD",
            "actual": actual,
            "estimated": estimated,
        }


def _operator_action(
    *,
    run: ProductionWorkflowRun | None,
    incident: OpsIncident | None,
    decision: FinalVideoDecision | None,
    task: HumanUploadTask | None,
) -> str:
    if incident is not None:
        return "RESOLVE_INCIDENT"
    if run is None:
        return "START_PRODUCTION"
    if run.state in {"BLOCKED", "RETRY_SCHEDULED", "FAILED_TERMINAL"}:
        return "RESUME_PRODUCTION"
    if decision is None and (
        run.state == "FINAL_REVIEW_READY" or run.final_review_candidate_id is not None
    ):
        return "FINAL_REVIEW"
    if decision is not None and decision.decision == "UPLOAD" and task is not None:
        if task.task_state == "READY_FOR_OPERATOR":
            return "START_MANUAL_UPLOAD"
        if task.task_state in {"IN_PROGRESS", "AWAITING_CONFIRMATION"}:
            return "CONFIRM_MANUAL_UPLOAD"
    return "NONE"


def _next_action_text(
    *,
    action: str,
    run: ProductionWorkflowRun | None,
    incident: OpsIncident | None,
) -> str:
    if action == "RESOLVE_INCIDENT":
        return (
            incident.next_action
            if incident is not None
            else "Mở sự cố và xử lý theo hướng dẫn."
        )
    if action == "START_PRODUCTION":
        return "Bắt đầu luồng sản xuất đã được lên kế hoạch."
    if action == "RESUME_PRODUCTION":
        return "Kiểm tra trở ngại rồi tiếp tục luồng theo checkpoint bền vững."
    if action == "FINAL_REVIEW":
        return "Xem MP4 cuối và chọn UPLOAD hoặc DO_NOT_UPLOAD."
    if action == "START_MANUAL_UPLOAD":
        return "Tải đúng file đã xác minh và bắt đầu upload thủ công."
    if action == "CONFIRM_MANUAL_UPLOAD":
        return "Nhập chính xác kết quả upload thực tế để VCOS đối chiếu."
    if run is not None and run.state == "FINAL_REVIEW_READY":
        return "Chờ bước publish thủ công tiếp theo."
    return "Không cần thao tác. VCOS đang tiếp tục luồng kỹ thuật đã được phép."


def _stage_projection(
    *,
    receipts: list[WorkflowCommandReceipt],
    events: list[DomainEvent],
    run: ProductionWorkflowRun,
) -> list[WorkflowStageProgressRead]:
    grouped: dict[str, list[WorkflowCommandReceipt]] = defaultdict(list)
    for receipt in receipts:
        grouped[receipt.stage].append(receipt)
    event_by_stage: dict[str, list[DomainEvent]] = defaultdict(list)
    for event in events:
        stage = str((event.metadata_ or {}).get("stage") or "")
        if stage:
            event_by_stage[stage].append(event)

    ordered_stages: list[str] = []
    for receipt in receipts:
        if receipt.stage not in ordered_stages:
            ordered_stages.append(receipt.stage)
    if run.current_stage not in ordered_stages:
        ordered_stages.append(run.current_stage)

    result: list[WorkflowStageProgressRead] = []
    for stage in ordered_stages:
        stage_receipts = grouped.get(stage, [])
        stage_events = event_by_stage.get(stage, [])
        started_at = min(
            (receipt.started_at for receipt in stage_receipts),
            default=None,
        )
        finished_at = max(
            (receipt.completed_at for receipt in stage_receipts),
            default=None,
        )
        retry_count = max(
            (event.attempt_count for event in stage_events),
            default=max(len(stage_receipts) - 1, 0),
        )
        next_retry = min(
            (
                event.next_attempt_at
                for event in stage_events
                if event.next_attempt_at is not None and event.delivered_at is None
            ),
            default=None,
        )
        if stage_receipts:
            state = "COMPLETED"
        elif run.state in TERMINAL_WORKFLOW_STATES:
            state = run.state
        elif stage == run.current_stage:
            state = "IN_PROGRESS"
        else:
            state = "PENDING"
        result.append(
            WorkflowStageProgressRead(
                stage=stage,
                state=state,
                started_at=started_at,
                finished_at=finished_at,
                retry_count=retry_count,
                next_retry_at=next_retry,
                summary=(
                    f"{len(stage_receipts)} command đã hoàn tất"
                    if stage_receipts
                    else None
                ),
            )
        )
    return result


def _lease_health(events: list[DomainEvent]) -> str:
    undelivered = [
        event
        for event in events
        if event.delivered_at is None and event.dead_lettered_at is None
    ]
    if not undelivered:
        return "INACTIVE"
    latest = undelivered[-1]
    now = utc_now()
    if latest.lease_expires_at is not None:
        return "HEALTHY" if latest.lease_expires_at > now else "STALE"
    return "WAITING"


def _provider_status(run: ProductionWorkflowRun | None) -> str:
    if run is None:
        return "NOT_STARTED"
    if run.current_stage == "MEDIA":
        return "IN_PROGRESS"
    if run.current_stage in {"RENDER", "QC", "ARCHIVE", "FINALIZE"}:
        return "COMPLETED"
    return "NOT_STARTED"


def _render_status(run: ProductionWorkflowRun | None) -> str:
    if run is None:
        return "NOT_STARTED"
    if run.render_output_checksum:
        return "COMPLETED"
    if run.current_stage == "RENDER":
        return "IN_PROGRESS"
    if run.current_stage in {"QC", "ARCHIVE", "FINALIZE"}:
        return "COMPLETED"
    return "NOT_STARTED"


def _render_progress(run: ProductionWorkflowRun) -> int | None:
    value = _number_from(run.metadata_ or {}, "render_progress_percent")
    if value is not None:
        return max(0, min(100, round(value)))
    if run.render_output_checksum:
        return 100
    if run.current_stage == "RENDER":
        return 0
    return None


def _qc_status(run: ProductionWorkflowRun) -> str:
    if run.technical_qc_receipt_hash and run.creative_qc_receipt_hash:
        return "PASS"
    if run.current_stage == "QC":
        return "IN_PROGRESS"
    if run.current_stage in {"ARCHIVE", "FINALIZE"}:
        return "COMPLETED"
    return "NOT_STARTED"


def _budget_status(metadata: dict[str, Any], costs: dict[str, Any]) -> str:
    explicit = _first_text(
        metadata,
        "budget_status",
        "budget_reservation_state",
        "budget_settlement_state",
    )
    if explicit:
        return explicit
    if costs["actual"]:
        return "SETTLED"
    if _number_from(metadata, "budget_reserved", "reserved_cost") is not None:
        return "RESERVED"
    return "NOT_STARTED"


def _why_selected(project: VideoProject, metadata: dict[str, Any]) -> str:
    explicit = _first_text(
        metadata,
        "why_selected",
        "selection_reason",
        "assignment_summary",
    )
    if explicit and not _looks_like_code(explicit):
        return explicit
    if project.content_mode == "SERIES_EPISODE":
        return (
            "Đây là tập tiếp theo đã được giữ chỗ nguyên tử trong chuỗi đang hoạt động."
        )
    if project.production_lane == "LONG_DERIVED_SHORT":
        return "Được tạo từ đúng video cha và timeline đã chốt để mở rộng phân phối."
    return "Được chọn theo ưu tiên lịch nội dung và admission v2 đã chốt."


def _friendly_standalone_reason(reason: str | None) -> str:
    if not reason:
        return "Phù hợp làm video độc lập theo kế hoạch nội dung."
    labels = {
        "SERIES_NOT_REQUIRED": "Chủ đề này không cần ràng buộc vào một chuỗi.",
        "NO_ELIGIBLE_SERIES": "Chưa có chuỗi phù hợp với chủ đề và luồng sản xuất.",
        "STANDALONE_REQUIRED": "Chính sách nội dung yêu cầu video độc lập.",
        "DERIVED_SHORT": "Video ngắn được dẫn xuất trực tiếp từ video dài đã chốt.",
    }
    return labels.get(
        reason,
        "Admission v2 đã ghi nhận lý do làm video độc lập; mã chi tiết nằm trong Phụ lục kỹ thuật.",
    )


def _friendly_blocker(
    incident: OpsIncident | None,
    run: ProductionWorkflowRun | None,
) -> str | None:
    if incident is not None:
        return (
            incident.operator_visible_blocker
            or _friendly_reason_codes(incident.reason_codes)
            or "Có sự cố cần người vận hành xử lý."
        )
    if run is not None and run.state in {
        "BLOCKED",
        "FAILED_TERMINAL",
        "DEAD_LETTERED",
    }:
        return (
            _friendly_reason_codes(run.state_reason_codes)
            or "Luồng đang bị chặn; xem Phụ lục kỹ thuật và hướng xử lý."
        )
    return None


def _friendly_reason_codes(codes: Iterable[str]) -> str | None:
    values = list(codes)
    if not values:
        return None
    return f"Có {len(values)} lý do kỹ thuật cần kiểm tra; mã chi tiết nằm trong Phụ lục kỹ thuật."


def _friendly_warnings(
    metadata: dict[str, Any],
    disclosures: dict[str, Any],
) -> list[str]:
    values: list[Any] = []
    for source in (metadata, disclosures):
        candidate = source.get("warnings")
        if isinstance(candidate, list):
            values.extend(candidate)
    friendly = [
        str(value)
        for value in values
        if isinstance(value, str) and not _looks_like_code(value)
    ]
    coded_count = len(values) - len(friendly)
    if coded_count:
        friendly.append(
            f"Có {coded_count} cảnh báo kỹ thuật; xem mã trong Phụ lục kỹ thuật."
        )
    return friendly


def _technical_warning_codes(
    metadata: dict[str, Any],
    disclosures: dict[str, Any],
) -> list[str]:
    values: list[str] = []
    for source in (metadata, disclosures):
        warnings = source.get("warnings")
        if isinstance(warnings, list):
            values.extend(
                value
                for value in warnings
                if isinstance(value, str) and _looks_like_code(value)
            )
        codes = source.get("warning_codes")
        if isinstance(codes, list):
            values.extend(value for value in codes if isinstance(value, str))
    return list(dict.fromkeys(values))


def _rights_summary(disclosures: dict[str, Any]) -> str:
    explicit = _first_text(
        disclosures,
        "operator_summary",
        "summary",
        "rights_disclosure_summary",
    )
    if explicit and not _looks_like_code(explicit):
        return explicit
    if disclosures:
        return (
            "Bằng chứng quyền sử dụng và disclosure đã được đóng gói cùng video cuối."
        )
    return (
        "Chưa có tóm tắt thân thiện; xem receipt quyền sử dụng trong Phụ lục kỹ thuật."
    )


def _repair_summary(
    metadata: dict[str, Any],
    materiality: dict[str, Any],
) -> str:
    explicit = _first_text(
        metadata,
        "auto_repair_summary",
        "repair_summary",
        default=_first_text(
            materiality,
            "auto_repair_summary",
            "repair_summary",
        ),
    )
    if explicit and not _looks_like_code(explicit):
        return explicit
    return "Không có sửa đổi tự động mang tính trọng yếu sau khi video cuối được chốt."


def _captions_label(metadata: dict[str, Any]) -> str:
    explicit = _first_text(
        metadata,
        "captions_label",
        "subtitle_summary",
        "captions_summary",
    )
    if explicit:
        return explicit
    captions = metadata.get("captions") or metadata.get("subtitles")
    if isinstance(captions, list):
        return f"{len(captions)} track phụ đề"
    return (
        "Không có track phụ đề rời; nội dung chữ được hiển thị trực tiếp "
        "trong khung hình."
    )


def _publish_next_action(
    *,
    task: HumanUploadTask,
    confirmation: ManualPublishConfirmation | None,
    uploaded_video: UploadedVideo | None,
) -> str:
    if uploaded_video is not None:
        if uploaded_video.analytics_sync_status == "READY":
            return "Đã xác minh UploadedVideo; sẵn sàng cho analytics read-only."
        return "Chờ lineage analytics-ready được ghi nhận."
    if confirmation is not None:
        if confirmation.confirmation_state == "CORRECTION_REQUIRED":
            return "Sửa xác nhận theo dữ liệu thực tế; VCOS sẽ giữ đầy đủ lịch sử."
        if confirmation.confirmation_state in {
            "REJECTED_MISMATCH",
            "BLOCKED_DESTINATION",
        }:
            return "Giải quyết mismatch hoặc sai đích trước khi xác minh."
        if confirmation.confirmation_state in {"SUBMITTED", "VARIANCE_ACCEPTED"}:
            return "Xác minh confirmation để tạo UploadedVideo có lineage đầy đủ."
    if task.task_state == "READY_FOR_OPERATOR":
        return "Tải đúng file đã xác minh rồi bắt đầu upload thủ công ngoài VCOS."
    if task.task_state in {"IN_PROGRESS", "AWAITING_CONFIRMATION"}:
        return "Nhập platform video ID/URL và metadata thực tế sau khi upload."
    if task.task_state == "CANCELED":
        return "Task đã dừng; không upload hoặc tạo UploadedVideo."
    return "Không cần thao tác thêm."


def _confirmation_states(
    confirmation: ManualPublishConfirmation | None,
) -> tuple[str, str]:
    if confirmation is None:
        return "NOT_CHECKED", "NOT_REQUIRED"
    if confirmation.confirmation_state in {
        "REJECTED_MISMATCH",
        "BLOCKED_DESTINATION",
        "CORRECTION_REQUIRED",
    }:
        return "MISMATCH", "CORRECTION_REQUIRED"
    if confirmation.confirmation_state in {"VERIFIED", "VARIANCE_ACCEPTED"}:
        return "MATCHED", (
            "CORRECTED" if confirmation.corrected_at is not None else "NOT_REQUIRED"
        )
    return "NOT_CHECKED", "NOT_REQUIRED"


def _duration_from_contract(contract: dict[str, Any] | None) -> float:
    if not contract:
        return 0.0
    for key in (
        "configured_duration_seconds",
        "target_duration_seconds",
        "exact_duration_seconds",
        "duration_seconds",
    ):
        value = contract.get(key)
        if isinstance(value, (int, float, Decimal)):
            return float(value)
    return 0.0


def _destination_handle(run: ProductionWorkflowRun | None) -> str | None:
    if run is None:
        return None
    binding = run.destination_binding or {}
    return _friendly_destination_identity(
        _first_text(
            binding,
            "channel_handle",
            "handle",
            "destination_account_identity",
        )
    )


def _friendly_destination_identity(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("@"):
        return value
    if _looks_like_code(value):
        return None
    return value


def _safe_file_name(primary: str | None, fallback_ref: str | None) -> str:
    if primary:
        return PurePosixPath(primary.replace("\\", "/")).name
    if fallback_ref:
        parsed = urlparse(fallback_ref)
        name = PurePosixPath(parsed.path.replace("\\", "/")).name
        if name:
            return name
    return "video-final.mp4"


def _safe_external_ref(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in {"https", "drive", "gdrive"}:
        return value
    if _is_local_archive_ref(value):
        return value
    return None


def _https_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    return value if parsed.scheme == "https" and parsed.netloc else None


def _local_review_urls(
    *,
    candidate: FinalReviewCandidate,
    final_media: FinalMediaRef | None,
    cloud_media: CloudMediaRef | None,
) -> tuple[str | None, str | None]:
    if not _is_verified_local_archive(
        candidate=candidate,
        final_media=final_media,
        cloud_media=cloud_media,
    ):
        return None, None
    base = f"/final-review-candidates/{candidate.id}"
    appendix = dict(cloud_media.technical_appendix or {})
    expected_thumbnail_ref = (
        f"archive/{candidate.video_project_id}/{candidate.render_output_checksum}.jpg"
    )
    thumbnail_available = appendix.get(
        "thumbnail_relative_ref"
    ) == expected_thumbnail_ref and _is_sha256_text(appendix.get("thumbnail_checksum"))
    return (
        f"{base}/media",
        f"{base}/thumbnail" if thumbnail_available else None,
    )


def _is_verified_local_archive(
    *,
    candidate: FinalReviewCandidate,
    final_media: FinalMediaRef | None,
    cloud_media: CloudMediaRef | None,
) -> bool:
    expected_ref = (
        f"vcos-local-archive://{candidate.video_project_id}/"
        f"{candidate.render_output_checksum}/final.mp4"
    )
    appendix = dict(cloud_media.technical_appendix or {}) if cloud_media else {}
    return bool(
        final_media is not None
        and cloud_media is not None
        and candidate.archive_verification_state == "VERIFIED"
        and candidate.archive_object_ref == expected_ref
        and final_media.company_id == candidate.company_id
        and final_media.channel_workspace_id == candidate.channel_workspace_id
        and final_media.video_project_id == candidate.video_project_id
        and final_media.file_ref == expected_ref
        and final_media.checksum_sha256 == candidate.render_output_checksum
        and cloud_media.company_id == candidate.company_id
        and cloud_media.channel_workspace_id == candidate.channel_workspace_id
        and cloud_media.video_project_id == candidate.video_project_id
        and cloud_media.storage_provider == "VCOS_LOCAL_ARCHIVE"
        and cloud_media.web_view_link == expected_ref
        and cloud_media.mime_type == "video/mp4"
        and cloud_media.checksum_sha256 == candidate.render_output_checksum
        and cloud_media.upload_status == "VERIFIED"
        and cloud_media.verification_status == "CHECKSUM_VERIFIED"
        and appendix.get("readback_checksum") == candidate.render_output_checksum
        and appendix.get("archive_receipt_hash") == candidate.archive_receipt_hash
    )


def _is_local_archive_ref(value: str) -> bool:
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        uuid.UUID(parsed.netloc)
    except (ValueError, AttributeError):
        return False
    return bool(
        parsed.scheme == "vcos-local-archive"
        and not parsed.query
        and not parsed.fragment
        and len(parts) == 2
        and _is_sha256_text(parts[0])
        and parts[1] == "final.mp4"
    )


def _is_sha256_text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _first_text(
    source: dict[str, Any] | None,
    *keys: str,
    default: str | None = None,
) -> str | None:
    source = source or {}
    for key in keys:
        value = source.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _number_from(
    source: dict[str, Any] | None,
    *keys: str,
    default: float | None = None,
) -> float | None:
    source = source or {}
    for key in keys:
        value = source.get(key)
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, dict):
            nested = value.get("amount") or value.get("value")
            if isinstance(nested, (int, float, Decimal)):
                return float(nested)
    return default


def _looks_like_code(value: str) -> bool:
    return (
        bool(value)
        and value.upper() == value
        and ("_" in value or ":" in value or "-" in value)
    )
