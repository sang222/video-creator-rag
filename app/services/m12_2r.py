from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m12_2r import (
    BackfillUploadedVideoRequest,
    BackfillUploadedVideoResult,
    HumanUploadTaskLedgerRead,
    HumanUploadTaskListRead,
    PublishLedgerRead,
    UploadedVideoBackfillEventRead,
    UploadedVideoLedgerRead,
    UploadedVideoListRead,
    UploadedVideoVerificationResult,
)
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelWorkspace,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    PublishHandoffPackage,
    UploadedVideo,
    UploadedVideoBackfillEvent,
    YouTubeMonitoringCredential,
)
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus
from app.services.m10_3 import YouTubePublicStatsProvider


YOUTUBE_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
OPEN_UPLOAD_TASK_STATES = {
    "READY_FOR_HUMAN_UPLOAD",
    "HUMAN_UPLOAD_IN_PROGRESS",
    "UPLOADED_WAITING_BACKFILL",
    "BACKFILLED_WAITING_VERIFICATION",
    "UPLOADED_UNVERIFIED",
    "BLOCKED",
}
TERMINAL_UPLOAD_TASK_STATES = {"UPLOADED_VERIFIED", "CANCELLED"}
WAITING_BACKFILL_STATES = {"HUMAN_UPLOAD_IN_PROGRESS", "UPLOADED_WAITING_BACKFILL"}
VERIFIED_STATUSES = {"VERIFIED_PUBLIC", "VERIFIED_OWNER"}
WAITING_VERIFICATION_STATUSES = {"NOT_VERIFIED", "VERIFICATION_UNAVAILABLE", "VERIFICATION_FAILED"}
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"


def parse_youtube_video_id(value: str) -> str:
    raw = value.strip()
    if YOUTUBE_VIDEO_ID_RE.fullmatch(raw):
        return raw
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValidationFailureError("INVALID_YOUTUBE_VIDEO_ID")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    candidate: str | None = None
    if host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            values = parse_qs(parsed.query).get("v") or []
            candidate = values[0] if values else None
        elif parsed.path.startswith("/shorts/"):
            candidate = parsed.path.removeprefix("/shorts/").split("/")[0]
    elif host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    if candidate and YOUTUBE_VIDEO_ID_RE.fullmatch(candidate):
        return candidate
    raise ValidationFailureError("INVALID_YOUTUBE_VIDEO_ID")


class PublishHandoffLedgerService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        public_provider: YouTubePublicStatsProvider | None = None,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.public_provider = public_provider or YouTubePublicStatsProvider()

    def list_upload_tasks(
        self,
        *,
        channel_id: uuid.UUID,
        status: str | None = None,
        destination: str | None = None,
        video_project_id: uuid.UUID | None = None,
        package_id: uuid.UUID | None = None,
    ) -> HumanUploadTaskListRead:
        self._require_channel(channel_id)
        statement = select(HumanUploadTask).where(HumanUploadTask.channel_workspace_id == channel_id)
        if status:
            statement = statement.where(HumanUploadTask.task_state == status)
        if destination:
            statement = statement.where(HumanUploadTask.destination == destination)
        if video_project_id:
            statement = statement.where(HumanUploadTask.video_project_id == video_project_id)
        if package_id:
            statement = statement.where(
                (HumanUploadTask.first_scripted_video_package_id == package_id)
                | (HumanUploadTask.publish_package_id == package_id)
            )
        tasks = list(self.session.scalars(statement.order_by(desc(HumanUploadTask.created_at), desc(HumanUploadTask.id))).all())
        counts = self._ledger_counts(channel_id)
        return HumanUploadTaskListRead(
            channel_id=channel_id,
            tasks=[self._read_task(task) for task in tasks],
            **counts,
        )

    def create_upload_task_from_package(self, package_id: uuid.UUID) -> HumanUploadTaskLedgerRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        if package.package_status != "READY_FOR_HUMAN_REVIEW":
            raise ValidationFailureError("package must be READY_FOR_HUMAN_REVIEW before manual upload handoff")
        existing = self.session.scalars(
            select(HumanUploadTask)
            .where(HumanUploadTask.first_scripted_video_package_id == package.id)
            .where(HumanUploadTask.destination == "YOUTUBE")
            .where(HumanUploadTask.task_state.not_in(TERMINAL_UPLOAD_TASK_STATES))
            .order_by(desc(HumanUploadTask.created_at))
            .limit(1)
        ).one_or_none()
        if existing is not None:
            return self._read_task(existing)
        channel = self._require_channel(package.channel_id)
        snapshot = _package_task_snapshot(package)
        task = HumanUploadTask(
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            upload_card_id=None,
            video_project_id=package.video_project_id,
            first_scripted_video_package_id=package.id,
            publish_package_id=None,
            destination="YOUTUBE",
            target_platform="YOUTUBE_LONG",
            task_state="READY_FOR_HUMAN_UPLOAD",
            upload_card_ref=f"first_scripted_video_package:{package.id}:upload_card_copy",
            title_snapshot=snapshot["title"],
            description_snapshot=snapshot["description"],
            thumbnail_ref=snapshot["thumbnail_ref"],
            subtitle_refs=snapshot["subtitle_refs"],
            required_assets=snapshot["required_assets"],
            checklist=snapshot["checklist"],
            required_checklist=snapshot["checklist"],
            actual_uploaded_video_id=None,
        )
        self.session.add(task)
        self.session.flush()
        self._record_event(
            event_type="HUMAN_UPLOAD_TASK_CREATED",
            aggregate_type="human_upload_task",
            aggregate_id=task.id,
            target_type="human_upload_task",
            target_id=task.id,
            company_id=task.company_id,
            reason_code="NEEDS_HUMAN_UPLOAD",
            payload={
                "channel_id": str(task.channel_workspace_id),
                "first_scripted_video_package_id": str(package.id),
                "destination": task.destination,
                "status": task.task_state,
                "manual_only": True,
                "no_upload_api_by_policy": True,
            },
        )
        return self._read_task(task)

    def start_upload_task(self, task_id: uuid.UUID) -> HumanUploadTaskLedgerRead:
        task = self._require_task(task_id)
        if task.task_state != "READY_FOR_HUMAN_UPLOAD":
            raise ValidationFailureError("only READY_FOR_HUMAN_UPLOAD tasks can be started")
        task.task_state = "HUMAN_UPLOAD_IN_PROGRESS"
        self.session.flush()
        self._record_event(
            event_type="HUMAN_UPLOAD_STARTED",
            aggregate_type="human_upload_task",
            aggregate_id=task.id,
            target_type="human_upload_task",
            target_id=task.id,
            company_id=task.company_id,
            reason_code="HUMAN_UPLOAD_ONLY",
            payload={"status": task.task_state, "manual_only": True, "no_upload_api_by_policy": True},
        )
        return self._read_task(task)

    def backfill_uploaded_video(
        self,
        *,
        task_id: uuid.UUID,
        data: BackfillUploadedVideoRequest,
    ) -> BackfillUploadedVideoResult:
        task = self._require_task(task_id)
        previous_status = task.task_state
        try:
            parsed_video_id = parse_youtube_video_id(data.youtube_url_or_video_id)
        except ValidationFailureError:
            event = self._create_backfill_event(
                channel_id=task.channel_workspace_id,
                input_url_or_video_id=data.youtube_url_or_video_id,
                parse_status="INVALID",
                human_upload_task_id=task.id,
                previous_status=previous_status,
                new_status=task.task_state,
                operator_note=data.operator_note,
            )
            self._record_event(
                event_type="YOUTUBE_VIDEO_ID_PARSED",
                aggregate_type="human_upload_task",
                aggregate_id=task.id,
                target_type="human_upload_task",
                target_id=task.id,
                company_id=task.company_id,
                reason_code="INVALID_YOUTUBE_VIDEO_ID",
                payload={"parse_status": event.parse_status, "manual_only": True},
            )
            raise ValidationFailureError("INVALID_YOUTUBE_VIDEO_ID")
        duplicate = self._duplicate_uploaded_video(task.channel_workspace_id, parsed_video_id, exclude_task_id=task.id)
        if duplicate is not None:
            task.task_state = "BLOCKED"
            task.blocked_reason = "DUPLICATE_YOUTUBE_VIDEO_ID"
            self.session.flush()
            event = self._create_backfill_event(
                channel_id=task.channel_workspace_id,
                input_url_or_video_id=data.youtube_url_or_video_id,
                parse_status="DUPLICATE",
                parsed_video_id=parsed_video_id,
                human_upload_task_id=task.id,
                uploaded_video_id=duplicate.id,
                previous_status=previous_status,
                new_status=task.task_state,
                operator_note=data.operator_note,
            )
            self._record_event(
                event_type="UPLOADED_VIDEO_DUPLICATE_BLOCKED",
                aggregate_type="human_upload_task",
                aggregate_id=task.id,
                target_type="uploaded_video",
                target_id=duplicate.id,
                company_id=task.company_id,
                reason_code="DUPLICATE_YOUTUBE_VIDEO_ID",
                payload={"parsed_video_id": parsed_video_id, "existing_uploaded_video_id": str(duplicate.id)},
            )
            raise ConflictError(f"DUPLICATE_YOUTUBE_VIDEO_ID: {event.id}")

        uploaded = self._upsert_uploaded_video(task, parsed_video_id=parsed_video_id, data=data)
        verification_available = self._public_verification_configured()
        if verification_available:
            uploaded.verification_status = "NOT_VERIFIED"
            uploaded.analytics_sync_status = "PENDING" if self._owner_analytics_connected() else "NOT_CONFIGURED"
            task.task_state = "BACKFILLED_WAITING_VERIFICATION"
        else:
            uploaded.verification_status = "VERIFICATION_UNAVAILABLE"
            uploaded.analytics_sync_status = "NOT_CONFIGURED"
            task.task_state = "UPLOADED_UNVERIFIED"
        task.actual_uploaded_video_id = uploaded.id
        task.operator_note = data.operator_note
        if task.task_state in {"UPLOADED_UNVERIFIED", "UPLOADED_VERIFIED"}:
            task.completed_at = utc_now()
        self.session.flush()
        event = self._create_backfill_event(
            channel_id=task.channel_workspace_id,
            input_url_or_video_id=data.youtube_url_or_video_id,
            parse_status="PARSED",
            parsed_video_id=parsed_video_id,
            human_upload_task_id=task.id,
            uploaded_video_id=uploaded.id,
            previous_status=previous_status,
            new_status=task.task_state,
            operator_note=data.operator_note,
        )
        self._record_event(
            event_type="YOUTUBE_VIDEO_ID_PARSED",
            aggregate_type="human_upload_task",
            aggregate_id=task.id,
            target_type="human_upload_task",
            target_id=task.id,
            company_id=task.company_id,
            reason_code="UPLOADED_VIDEO_RECORDED",
            payload={"parsed_video_id": parsed_video_id, "parse_status": "PARSED"},
        )
        self._record_event(
            event_type="UPLOADED_VIDEO_BACKFILLED",
            aggregate_type="uploaded_video",
            aggregate_id=uploaded.id,
            target_type="uploaded_video",
            target_id=uploaded.id,
            company_id=uploaded.company_id,
            reason_code="UPLOADED_VIDEO_RECORDED",
            payload={
                "channel_id": str(uploaded.channel_workspace_id),
                "human_upload_task_id": str(task.id),
                "first_scripted_video_package_id": str(task.first_scripted_video_package_id)
                if task.first_scripted_video_package_id
                else None,
                "verification_status": uploaded.verification_status,
                "analytics_sync_status": uploaded.analytics_sync_status,
                "manual_only": True,
            },
        )
        if not verification_available:
            self._record_event(
                event_type="UPLOADED_VIDEO_VERIFICATION_SKIPPED",
                aggregate_type="uploaded_video",
                aggregate_id=uploaded.id,
                target_type="uploaded_video",
                target_id=uploaded.id,
                company_id=uploaded.company_id,
                reason_code="YOUTUBE_VERIFICATION_NOT_CONFIGURED",
                payload={"verification_status": uploaded.verification_status, "analytics_sync_status": uploaded.analytics_sync_status},
            )
        next_action = self._uploaded_next_action(uploaded)
        return BackfillUploadedVideoResult(
            task=self._read_task(task),
            uploaded_video=self._read_uploaded_video(uploaded),
            backfill_event=self._read_backfill_event(event),
            parsed_video_id=parsed_video_id,
            next_action=next_action,
        )

    def list_uploaded_videos(
        self,
        *,
        channel_id: uuid.UUID,
        verification_status: str | None = None,
        analytics_sync_status: str | None = None,
        actual_visibility: str | None = None,
    ) -> UploadedVideoListRead:
        self._require_channel(channel_id)
        statement = select(UploadedVideo).where(UploadedVideo.channel_workspace_id == channel_id)
        if verification_status:
            statement = statement.where(UploadedVideo.verification_status == verification_status)
        if analytics_sync_status:
            statement = statement.where(UploadedVideo.analytics_sync_status == analytics_sync_status)
        if actual_visibility:
            statement = statement.where(UploadedVideo.actual_visibility == actual_visibility)
        uploaded = list(self.session.scalars(statement.order_by(desc(UploadedVideo.created_at), desc(UploadedVideo.id))).all())
        return UploadedVideoListRead(channel_id=channel_id, uploaded_videos=[self._read_uploaded_video(item) for item in uploaded])

    def get_uploaded_video(self, uploaded_video_id: uuid.UUID) -> UploadedVideoLedgerRead:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        return self._read_uploaded_video(uploaded)

    def verify_uploaded_video(self, uploaded_video_id: uuid.UUID) -> UploadedVideoVerificationResult:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        task = self.session.get(HumanUploadTask, uploaded.human_upload_task_id) if uploaded.human_upload_task_id else None
        if not self._public_verification_configured():
            uploaded.verification_status = "VERIFICATION_UNAVAILABLE"
            uploaded.analytics_sync_status = "NOT_CONFIGURED"
            uploaded.last_verified_at = utc_now()
            if task is not None:
                task.task_state = "UPLOADED_UNVERIFIED"
                task.completed_at = task.completed_at or utc_now()
            self.session.flush()
            self._record_event(
                event_type="UPLOADED_VIDEO_VERIFICATION_SKIPPED",
                aggregate_type="uploaded_video",
                aggregate_id=uploaded.id,
                target_type="uploaded_video",
                target_id=uploaded.id,
                company_id=uploaded.company_id,
                reason_code="YOUTUBE_VERIFICATION_NOT_CONFIGURED",
                payload={"verification_status": uploaded.verification_status, "analytics_sync_status": uploaded.analytics_sync_status},
            )
            return UploadedVideoVerificationResult(
                uploaded_video=self._read_uploaded_video(uploaded),
                verification_status="VERIFICATION_UNAVAILABLE",
                analytics_sync_status="NOT_CONFIGURED",
                next_action=self._uploaded_next_action(uploaded),
                reason_codes=["YOUTUBE_VERIFICATION_NOT_CONFIGURED", "ANALYTICS_SYNC_NOT_CONFIGURED"],
                technical_appendix={"provider_calls_made": False, "no_metrics_invented": True},
            )
        api_key = self.settings.youtube_data_api_key.get_secret_value() if self.settings.youtube_data_api_key else ""
        result = self.public_provider.fetch(platform_video_id=uploaded.platform_video_id, api_key=api_key)
        uploaded.last_verified_at = utc_now()
        if not result.ok:
            uploaded.verification_status = "VERIFICATION_FAILED"
            if task is not None:
                task.task_state = "UPLOADED_UNVERIFIED"
            self.session.flush()
            self._record_event(
                event_type="UPLOADED_VIDEO_VERIFICATION_FAILED",
                aggregate_type="uploaded_video",
                aggregate_id=uploaded.id,
                target_type="uploaded_video",
                target_id=uploaded.id,
                company_id=uploaded.company_id,
                reason_code=result.error_code or "YOUTUBE_VERIFICATION_FAILED",
                payload={"error_code": result.error_code, "http_status": result.http_status, "no_metrics_invented": True},
            )
            return UploadedVideoVerificationResult(
                uploaded_video=self._read_uploaded_video(uploaded),
                verification_status="VERIFICATION_FAILED",
                analytics_sync_status=uploaded.analytics_sync_status,  # type: ignore[arg-type]
                next_action=self._uploaded_next_action(uploaded),
                reason_codes=[result.error_code or "YOUTUBE_VERIFICATION_FAILED"],
                technical_appendix={"provider_calls_made": True, "read_only": True, "no_metrics_invented": True},
            )
        output = result.output or {}
        uploaded.verification_status = "VERIFIED_PUBLIC"
        uploaded.actual_title = output.get("youtube_title") or uploaded.actual_title
        uploaded.actual_visibility = _visibility(output.get("privacy_status") or uploaded.actual_visibility)
        uploaded.actual_publish_time = _coerce_datetime(output.get("youtube_published_at")) or uploaded.actual_publish_time
        uploaded.analytics_sync_status = "PENDING" if self._owner_analytics_connected() else "NOT_CONFIGURED"
        if task is not None:
            task.task_state = "UPLOADED_VERIFIED"
            task.completed_at = utc_now()
        self.session.flush()
        self._record_event(
            event_type="UPLOADED_VIDEO_VERIFIED",
            aggregate_type="uploaded_video",
            aggregate_id=uploaded.id,
            target_type="uploaded_video",
            target_id=uploaded.id,
            company_id=uploaded.company_id,
            reason_code="UPLOADED_VIDEO_RECORDED",
            payload={"verification_status": uploaded.verification_status, "read_only": True, "no_metrics_invented": True},
        )
        return UploadedVideoVerificationResult(
            uploaded_video=self._read_uploaded_video(uploaded),
            verification_status="VERIFIED_PUBLIC",
            analytics_sync_status=uploaded.analytics_sync_status,  # type: ignore[arg-type]
            next_action=self._uploaded_next_action(uploaded),
            reason_codes=["UPLOADED_VIDEO_RECORDED"],
            technical_appendix={"provider_calls_made": True, "read_only": True, "no_metrics_invented": True},
        )

    def publish_ledger(self, channel_id: uuid.UUID) -> PublishLedgerRead:
        self._require_channel(channel_id)
        counts = self._ledger_counts(channel_id)
        latest_tasks = list(
            self.session.scalars(
                select(HumanUploadTask)
                .where(HumanUploadTask.channel_workspace_id == channel_id)
                .order_by(desc(HumanUploadTask.created_at), desc(HumanUploadTask.id))
                .limit(10)
            ).all()
        )
        latest_uploaded = list(
            self.session.scalars(
                select(UploadedVideo)
                .where(UploadedVideo.channel_workspace_id == channel_id)
                .order_by(desc(UploadedVideo.created_at), desc(UploadedVideo.id))
                .limit(10)
            ).all()
        )
        return PublishLedgerRead(
            channel_id=channel_id,
            latest_tasks=[self._read_task(task) for task in latest_tasks],
            latest_uploaded_videos=[self._read_uploaded_video(uploaded) for uploaded in latest_uploaded],
            operator_summary_vi="VCOS chỉ ghi nhận upload thủ công và xác minh YouTube khi đã kết nối read-only.",
            **counts,
        )

    def _upsert_uploaded_video(
        self,
        task: HumanUploadTask,
        *,
        parsed_video_id: str,
        data: BackfillUploadedVideoRequest,
    ) -> UploadedVideo:
        uploaded = self.session.get(UploadedVideo, task.actual_uploaded_video_id) if task.actual_uploaded_video_id else None
        package = self.session.get(FirstScriptedVideoPackage, task.first_scripted_video_package_id) if task.first_scripted_video_package_id else None
        publish_package = self.session.get(PublishHandoffPackage, task.publish_package_id) if task.publish_package_id else None
        external_url = data.youtube_url_or_video_id.strip()
        if not external_url.startswith(("http://", "https://")):
            external_url = YOUTUBE_WATCH_URL.format(video_id=parsed_video_id)
        actual_metadata = {
            "actual_title": data.actual_title,
            "actual_visibility": data.actual_visibility or "UNKNOWN",
            "input_url_or_video_id": data.youtube_url_or_video_id,
            "playlist_id": data.playlist_id,
        }
        package_diff = _package_metadata_diff(task, data)
        published_at = data.actual_publish_time or data.actual_upload_time or utc_now()
        if uploaded is None:
            uploaded = UploadedVideo(
                company_id=task.company_id,
                channel_workspace_id=task.channel_workspace_id,
                video_project_id=task.video_project_id,
                policy_snapshot_id=package.compiled_policy_snapshot_id if package else None,
                publish_handoff_package_id=task.publish_package_id,
                manual_publish_confirmation_id=None,
                render_package_snapshot_id=publish_package.render_package_snapshot_id if publish_package else None,
                first_scripted_video_package_id=task.first_scripted_video_package_id,
                human_upload_task_id=task.id,
                destination="YOUTUBE",
                source_manifest_snapshot_id=None,
                rights_envelope_ref=None,
                platform="YOUTUBE",
                platform_video_id=parsed_video_id,
                video_url=external_url,
                published_at=published_at,
                publish_status="UNKNOWN",
                actual_metadata=actual_metadata,
                actual_disclosures={},
                lineage_refs=_lineage_refs(task),
                monitoring_state="NOT_STARTED",
                operator_summary={
                    "operator_summary_vi": "Video đã được ghi nhận từ upload thủ công. VCOS không upload/publish video này.",
                    "title": data.actual_title or task.title_snapshot,
                    "next_action": "Chờ xác minh YouTube.",
                },
            )
            self.session.add(uploaded)
        uploaded.platform_video_id = parsed_video_id
        uploaded.video_url = external_url
        uploaded.actual_metadata = actual_metadata
        uploaded.actual_title = data.actual_title
        uploaded.actual_visibility = data.actual_visibility or "UNKNOWN"
        uploaded.actual_publish_time = data.actual_publish_time
        uploaded.actual_upload_time = data.actual_upload_time
        uploaded.playlist_id = data.playlist_id
        uploaded.thumbnail_uploaded = data.thumbnail_uploaded
        uploaded.subtitles_uploaded = data.subtitles_uploaded
        uploaded.description_modified_from_package = data.description_modified_from_package
        uploaded.package_metadata_diff = package_diff
        uploaded.operator_note = data.operator_note
        uploaded.human_upload_task_id = task.id
        uploaded.first_scripted_video_package_id = task.first_scripted_video_package_id
        uploaded.destination = "YOUTUBE"
        uploaded.lineage_refs = _lineage_refs(task)
        self.session.flush()
        return uploaded

    def _duplicate_uploaded_video(
        self,
        channel_id: uuid.UUID,
        parsed_video_id: str,
        *,
        exclude_task_id: uuid.UUID,
    ) -> UploadedVideo | None:
        return self.session.scalars(
            select(UploadedVideo)
            .where(UploadedVideo.channel_workspace_id == channel_id)
            .where(UploadedVideo.platform == "YOUTUBE")
            .where(UploadedVideo.platform_video_id == parsed_video_id)
            .where((UploadedVideo.human_upload_task_id.is_(None)) | (UploadedVideo.human_upload_task_id != exclude_task_id))
            .limit(1)
        ).one_or_none()

    def _create_backfill_event(
        self,
        *,
        channel_id: uuid.UUID,
        input_url_or_video_id: str,
        parse_status: str,
        parsed_video_id: str | None = None,
        uploaded_video_id: uuid.UUID | None = None,
        human_upload_task_id: uuid.UUID | None = None,
        previous_status: str | None = None,
        new_status: str | None = None,
        operator_note: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> UploadedVideoBackfillEvent:
        event = UploadedVideoBackfillEvent(
            uploaded_video_id=uploaded_video_id,
            human_upload_task_id=human_upload_task_id,
            channel_id=channel_id,
            input_url_or_video_id=input_url_or_video_id,
            parsed_video_id=parsed_video_id,
            parse_status=parse_status,
            previous_status=previous_status,
            new_status=new_status,
            operator_note=operator_note,
            created_by=created_by,
        )
        self.session.add(event)
        self.session.flush()
        return event

    def _ledger_counts(self, channel_id: uuid.UUID) -> dict[str, int]:
        tasks = list(self.session.scalars(select(HumanUploadTask).where(HumanUploadTask.channel_workspace_id == channel_id)).all())
        uploaded = list(self.session.scalars(select(UploadedVideo).where(UploadedVideo.channel_workspace_id == channel_id)).all())
        return {
            "need_upload_count": sum(1 for task in tasks if task.task_state == "READY_FOR_HUMAN_UPLOAD" and task.actual_uploaded_video_id is None),
            "waiting_backfill_count": sum(1 for task in tasks if task.task_state in WAITING_BACKFILL_STATES and task.actual_uploaded_video_id is None),
            "uploaded_count": sum(1 for item in uploaded if item.platform_video_id or item.video_url),
            "waiting_verification_count": sum(1 for item in uploaded if item.verification_status in WAITING_VERIFICATION_STATUSES),
            "verified_count": sum(1 for item in uploaded if item.verification_status in VERIFIED_STATUSES),
            "analytics_not_configured_count": sum(1 for item in uploaded if item.analytics_sync_status == "NOT_CONFIGURED"),
            "blocked_count": sum(1 for task in tasks if task.task_state == "BLOCKED"),
            "unverified_count": sum(1 for task in tasks if task.task_state == "UPLOADED_UNVERIFIED"),
        }

    def _public_verification_configured(self) -> bool:
        return bool(self.settings.youtube_public_monitor_enabled and self.settings.youtube_data_api_key)

    def _owner_analytics_connected(self) -> bool:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(YouTubeMonitoringCredential)
                .where(YouTubeMonitoringCredential.provider_key == "YOUTUBE_ANALYTICS_API")
                .where(YouTubeMonitoringCredential.connection_state == "CONNECTED")
            )
            or 0
        ) > 0

    def _require_channel(self, channel_id: uuid.UUID) -> ChannelWorkspace:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        return channel

    def _require_task(self, task_id: uuid.UUID) -> HumanUploadTask:
        task = self.session.get(HumanUploadTask, task_id)
        if task is None:
            raise NotFoundError(f"human upload task not found: {task_id}")
        return task

    def _read_task(self, task: HumanUploadTask) -> HumanUploadTaskLedgerRead:
        status = _task_status(task.task_state)
        return HumanUploadTaskLedgerRead(
            id=task.id,
            channel_id=task.channel_workspace_id,
            video_project_id=task.video_project_id,
            first_scripted_video_package_id=task.first_scripted_video_package_id,
            publish_package_id=task.publish_package_id,
            destination="YOUTUBE",
            status=status,  # type: ignore[arg-type]
            upload_card_ref=task.upload_card_ref,
            title_snapshot=task.title_snapshot or "Chưa có tiêu đề",
            description_snapshot=task.description_snapshot,
            thumbnail_ref=task.thumbnail_ref,
            subtitle_refs=task.subtitle_refs or [],
            required_assets=task.required_assets or [],
            checklist=task.checklist or task.required_checklist or [],
            actual_uploaded_video_id=task.actual_uploaded_video_id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            blocked_reason=task.blocked_reason,
            operator_note=task.operator_note,
            next_action=_task_next_action(status),
        )

    def _read_uploaded_video(self, uploaded: UploadedVideo) -> UploadedVideoLedgerRead:
        return UploadedVideoLedgerRead(
            id=uploaded.id,
            channel_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            first_scripted_video_package_id=uploaded.first_scripted_video_package_id,
            publish_package_id=uploaded.publish_handoff_package_id,
            human_upload_task_id=uploaded.human_upload_task_id,
            destination="YOUTUBE",
            external_video_id=uploaded.platform_video_id,
            external_url=uploaded.video_url,
            actual_title=uploaded.actual_title or uploaded.actual_metadata.get("actual_title"),
            actual_visibility=_visibility(uploaded.actual_visibility),
            actual_publish_time=uploaded.actual_publish_time,
            actual_upload_time=uploaded.actual_upload_time,
            playlist_id=uploaded.playlist_id,
            thumbnail_uploaded=uploaded.thumbnail_uploaded,
            subtitles_uploaded=uploaded.subtitles_uploaded,
            description_modified_from_package=uploaded.description_modified_from_package,
            package_metadata_diff=uploaded.package_metadata_diff,
            verification_status=uploaded.verification_status,  # type: ignore[arg-type]
            analytics_sync_status=uploaded.analytics_sync_status,  # type: ignore[arg-type]
            last_verified_at=uploaded.last_verified_at,
            last_analytics_sync_at=uploaded.last_analytics_sync_at,
            operator_note=uploaded.operator_note,
            next_action=self._uploaded_next_action(uploaded),
            created_at=uploaded.created_at,
            updated_at=uploaded.updated_at,
        )

    def _read_backfill_event(self, event: UploadedVideoBackfillEvent) -> UploadedVideoBackfillEventRead:
        return UploadedVideoBackfillEventRead(
            id=event.id,
            uploaded_video_id=event.uploaded_video_id,
            human_upload_task_id=event.human_upload_task_id,
            channel_id=event.channel_id,
            input_url_or_video_id=event.input_url_or_video_id,
            parsed_video_id=event.parsed_video_id,
            parse_status=event.parse_status,  # type: ignore[arg-type]
            previous_status=event.previous_status,
            new_status=event.new_status,
            operator_note=event.operator_note,
            created_by=event.created_by,
            created_at=event.created_at,
        )

    def _uploaded_next_action(self, uploaded: UploadedVideo) -> str:
        if uploaded.verification_status == "VERIFICATION_UNAVAILABLE":
            return "Kết nối YouTube để xác minh video."
        if uploaded.verification_status == "NOT_VERIFIED":
            return "Chờ xác minh YouTube."
        if uploaded.verification_status == "VERIFICATION_FAILED":
            return "Kiểm tra lại YouTube URL/video_id hoặc kết nối YouTube."
        if uploaded.analytics_sync_status == "NOT_CONFIGURED":
            return "Video đã xác minh; kết nối YouTube Owner Analytics để sync dữ liệu."
        return "Video đã được ghi nhận và sẵn sàng cho bước analytics read-only."

    def _record_event(
        self,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        target_type: str,
        target_id: uuid.UUID,
        company_id: uuid.UUID | None,
        reason_code: str,
        payload: dict[str, Any],
    ) -> None:
        safe_payload = _jsonable(payload)
        DomainEventBus(self.session).append(
            EventEnvelope(
                event_type=event_type,
                event_version=1,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                correlation_id=f"m12-2r-{event_type.lower()}",
                payload=safe_payload,
            ),
            company_id=company_id,
        )
        AuditService(self.session).append(
            AuditEnvelope(
                action=event_type,
                actor_type="system",
                target_type=target_type,
                target_id=target_id,
                correlation_id=f"m12-2r-{event_type.lower()}",
                reason_code=reason_code,
                payload=safe_payload,
            ),
            company_id=company_id,
        )


def _task_status(value: str) -> str:
    if value == "READY":
        return "READY_FOR_HUMAN_UPLOAD"
    return value if value in OPEN_UPLOAD_TASK_STATES or value in TERMINAL_UPLOAD_TASK_STATES else "BLOCKED"


def _task_next_action(status: str) -> str:
    return {
        "READY_FOR_HUMAN_UPLOAD": "Upload thủ công trên YouTube, rồi nhập URL/video_id vào VCOS.",
        "HUMAN_UPLOAD_IN_PROGRESS": "Sau khi upload thủ công xong, nhập URL hoặc video_id YouTube.",
        "UPLOADED_WAITING_BACKFILL": "Nhập URL hoặc video_id YouTube.",
        "BACKFILLED_WAITING_VERIFICATION": "Chờ xác minh YouTube read-only.",
        "UPLOADED_VERIFIED": "Đã xác minh; sẵn sàng cho analytics read-only khi cấu hình.",
        "UPLOADED_UNVERIFIED": "Kết nối YouTube để xác minh video.",
        "BLOCKED": "Xem lý do bị chặn trước khi tiếp tục.",
        "CANCELLED": "Task đã hủy.",
    }.get(status, "Kiểm tra task upload thủ công.")


def _package_task_snapshot(package: FirstScriptedVideoPackage) -> dict[str, Any]:
    artifacts = package.artifacts or {}
    upload_copy = artifacts.get("upload_card_copy") if isinstance(artifacts.get("upload_card_copy"), dict) else {}
    metadata = artifacts.get("metadata_package") if isinstance(artifacts.get("metadata_package"), dict) else {}
    visual = artifacts.get("visual_plan") if isinstance(artifacts.get("visual_plan"), dict) else {}
    title = str(upload_copy.get("title") or metadata.get("title") or f"First scripted package {package.id}")
    description = upload_copy.get("description") or metadata.get("description")
    checklist_blob = artifacts.get("human_review_checklist") if isinstance(artifacts.get("human_review_checklist"), dict) else {}
    checklist = [{"item": key, "state": value} for key, value in sorted(checklist_blob.items())]
    return {
        "title": title,
        "description": str(description) if description else None,
        "thumbnail_ref": metadata.get("thumbnail_ref") or metadata.get("planned_thumbnail_ref"),
        "subtitle_refs": _json_list(metadata.get("subtitle_refs") or metadata.get("caption_refs")),
        "required_assets": [
            {"type": "metadata_package", "ready": bool(metadata)},
            {"type": "visual_plan", "ready": bool(visual)},
            {"type": "upload_card_copy", "ready": bool(upload_copy)},
        ],
        "checklist": checklist
        or [
            {"item": "upload_manual_only", "state": "PENDING"},
            {"item": "paste_back_youtube_url_or_video_id", "state": "PENDING"},
        ],
    }


def _package_metadata_diff(task: HumanUploadTask, data: BackfillUploadedVideoRequest) -> dict[str, Any]:
    title_changed = bool(data.actual_title and task.title_snapshot and data.actual_title != task.title_snapshot)
    description_changed = bool(data.description_modified_from_package)
    changed = []
    if title_changed:
        changed.append("title")
    if description_changed:
        changed.append("description")
    return {
        "title_changed": title_changed,
        "description_changed": description_changed,
        "changed_fields": changed,
        "operator_summary_vi": "Metadata thực tế khác package gốc." if changed else "Metadata thực tế khớp hoặc chưa đủ dữ liệu để so sánh.",
    }


def _lineage_refs(task: HumanUploadTask) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "human_upload_task_id": str(task.id),
        "channel_id": str(task.channel_workspace_id),
        "manual_only": True,
        "no_upload_api_by_policy": True,
    }
    if task.first_scripted_video_package_id:
        refs["first_scripted_video_package_id"] = str(task.first_scripted_video_package_id)
    if task.publish_package_id:
        refs["publish_package_id"] = str(task.publish_package_id)
    if task.video_project_id:
        refs["video_project_id"] = str(task.video_project_id)
    return refs


def _visibility(value: Any) -> str:
    normalized = str(value or "UNKNOWN").upper()
    return normalized if normalized in {"PUBLIC", "UNLISTED", "PRIVATE", "SCHEDULED", "UNKNOWN"} else "UNKNOWN"


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _json_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item if isinstance(item, dict) else {"value": item} for item in value]


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value
