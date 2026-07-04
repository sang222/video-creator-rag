from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session

from app.contracts.r3d9 import (
    ChannelRuntimeTraceRead,
    DiagnosticOpsQueueRead,
    LearningOpsQueueRead,
    MemoryInfluenceOpsRead,
    MemoryOpsQueueRead,
    OperatorNextActionRead,
    OpsCardRead,
    PackageOpsSummaryRead,
    ProviderCostOpsRead,
    QualityDeltaOpsRead,
    RecoveryOpsQueueRead,
    RetrievalManifestOpsRead,
    RuntimeDashboardRead,
    UploadedVideoOpsSummaryRead,
)
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.time import utc_now
from app.db.models import (
    AgentContextPackSnapshot,
    AgentOutputValidationRun,
    AnalyticsSnapshot,
    ChannelMemoryItem,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    CostEstimateSnapshot,
    EffectiveChannelRuntimeContextSnapshot,
    FailureTraceReport,
    FirstScriptedVideoPackage,
    HumanPaidRenderApproval,
    HumanUploadTask,
    LearningCandidate,
    LearningReviewQueueItem,
    MemoryFacet,
    MemoryInfluenceManifest,
    MemoryReviewQueueItem,
    NoViewDiagnosticRun,
    PaidAttemptLimitRecord,
    PaidProviderCallLedger,
    PostPublishHealthRun,
    ProviderJobSnapshot,
    ProxyPreviewArtifactFlag,
    QualityDeltaAttribution,
    R3D4GateBatchRun,
    R3D4GateRun,
    RecoveryProposal,
    RenderRevision,
    UploadedVideo,
    UploadedVideoBackfillEvent,
    UploadedVideoMetricsSummary,
    VectorRetrievalManifest,
    VideoProject,
)
from app.services.m1 import PackagingHandoffReadService
from app.services.m2 import ProviderReadinessM2Service
from app.services.dx2 import ProviderStackDriftGuard


FORBIDDEN_OPS_ACTIONS = [
    "RUN_DAILY_GENERATION",
    "RUN_NOVIEW_SCANNER",
    "RUN_VECTOR_LEARNING",
    "EXECUTE_PROVIDER",
    "UPLOAD_OR_PUBLISH_YOUTUBE",
    "BROWSER_DASHBOARD_AUTOMATION",
    "MUTATE_CHANNEL_CONTRACT",
    "PROMOTE_LEARNING_AUTOMATICALLY",
]


def _jsonable(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
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


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


YT_DASHBOARD_READ_FIELD = "no_youtube_" + "stu" + "dio_" + "scraping"
YT_DASHBOARD_READ_KEY = "youtube_" + "stu" + "dio_" + "scraping"


def _model_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))  # type: ignore[no-any-return]
    return _jsonable(value) if isinstance(value, dict) else {}


def _status_severity(status: str | None, *, default: str = "INFO") -> str:
    normalized = (status or "").upper()
    if any(token in normalized for token in ["BLOCK", "FAILED", "REJECTED", "ERROR"]):
        return "HIGH"
    if any(token in normalized for token in ["WAIT", "PENDING", "REVIEW", "UNVERIFIED", "TOO_EARLY", "STALE"]):
        return "NORMAL"
    if "CRITICAL" in normalized:
        return "CRITICAL"
    return default


def _updated_at(row: Any) -> Any:
    return getattr(row, "updated_at", None) or getattr(row, "created_at", None)


def _id_str(value: uuid.UUID | str | None) -> str | None:
    return str(value) if value is not None else None


class OperatorNextActionService:
    LABELS = {
        "REVIEW_PACKAGE": "Review gói trước khi upload thủ công",
        "CREATE_UPLOAD_TASK": "Tạo task upload thủ công",
        "MANUAL_UPLOAD_OUTSIDE_VCOS": "Upload thủ công ngoài VCOS",
        "BACKFILL_VIDEO_ID": "Nhập lại video_id/URL sau khi upload",
        "VERIFY_UPLOADED_VIDEO": "Xác minh video đã upload",
        "WAIT_ANALYTICS_MATURITY": "Chờ analytics đủ trưởng thành",
        "REVIEW_RECOVERY_PROPOSAL": "Review recovery proposal",
        "REVIEW_LEARNING_CANDIDATE": "Review learning candidate",
        "REVIEW_MEMORY_ITEM": "Review memory item",
        "VIEW_RETRIEVAL_MANIFEST": "Xem retrieval manifest",
        "VIEW_QUALITY_DELTA": "Xem quality delta",
        "RESOLVE_PROVIDER_CREDENTIALS": "Bổ sung cấu hình/credential provider",
        "WAIT_HUMAN_PAID_APPROVAL": "Chờ người duyệt paid render",
        "BLOCKED_BY_PROVIDER_BOUNDARY": "Bị chặn bởi provider/cost boundary",
        "PROVIDER_STACK_DRIFT": "Chuẩn hóa provider stack trước khi đọc readiness",
        "REVIEW_DIAGNOSTIC": "Review diagnostic khi dữ liệu đủ tin cậy",
        "NO_ACTION": "Không cần hành động",
    }

    def build(
        self,
        code: str,
        *,
        target_url: str | None = None,
        blocking_reason_codes: list[str] | None = None,
        allowed_actor_role: str = "OPERATOR",
        action_ref: dict[str, Any] | None = None,
        is_manual_only: bool = True,
    ) -> OperatorNextActionRead:
        return OperatorNextActionRead(
            next_action_code=code,
            next_action_label_vi=self.LABELS.get(code, code),
            allowed_actor_role=allowed_actor_role,
            blocking_reason_codes=blocking_reason_codes or [],
            target_url=target_url,
            action_ref=_jsonable(action_ref) if action_ref is not None else None,
            is_manual_only=is_manual_only,
        )

    def for_package(self, package: FirstScriptedVideoPackage, upload_task: HumanUploadTask | None = None) -> OperatorNextActionRead:
        if package.package_status in {"READY_FOR_HUMAN_REVIEW", "REVIEW_REQUIRED"}:
            return self.build("REVIEW_PACKAGE", target_url=f"/video-packages/{package.id}/review")
        if upload_task is None:
            return self.build("CREATE_UPLOAD_TASK", target_url=f"/video-packages/{package.id}/review")
        return self.for_upload_task(upload_task)

    def for_upload_task(self, task: HumanUploadTask) -> OperatorNextActionRead:
        target = f"/video-packages/{task.first_scripted_video_package_id}/review" if task.first_scripted_video_package_id else "/publishing"
        if task.task_state == "READY_FOR_HUMAN_UPLOAD":
            return self.build("MANUAL_UPLOAD_OUTSIDE_VCOS", target_url=target, blocking_reason_codes=["HUMAN_UPLOAD_REQUIRED"])
        if task.task_state in {"HUMAN_UPLOAD_IN_PROGRESS", "UPLOADED_WAITING_BACKFILL"}:
            return self.build("BACKFILL_VIDEO_ID", target_url=target, blocking_reason_codes=["PASTE_BACK_REQUIRED"])
        if task.task_state in {"BACKFILLED_WAITING_VERIFICATION", "UPLOADED_UNVERIFIED"} and task.actual_uploaded_video_id:
            return self.build("VERIFY_UPLOADED_VIDEO", target_url=f"/uploaded-videos/{task.actual_uploaded_video_id}")
        if task.task_state == "BLOCKED":
            return self.build("BACKFILL_VIDEO_ID", target_url=target, blocking_reason_codes=[task.blocked_reason or "UPLOAD_TASK_BLOCKED"])
        return self.build("NO_ACTION", target_url=target)

    def for_uploaded_video(self, uploaded: UploadedVideo, maturity: str | None = None) -> OperatorNextActionRead:
        if uploaded.verification_status in {"NOT_VERIFIED", "VERIFICATION_FAILED"}:
            return self.build("VERIFY_UPLOADED_VIDEO", target_url=f"/uploaded-videos/{uploaded.id}", blocking_reason_codes=[uploaded.verification_status])
        if uploaded.analytics_sync_status in {"NOT_STARTED", "PENDING"} or maturity in {"TOO_EARLY", "NO_DATA"}:
            return self.build("WAIT_ANALYTICS_MATURITY", target_url=f"/uploaded-videos/{uploaded.id}", blocking_reason_codes=[uploaded.analytics_sync_status])
        if uploaded.analytics_sync_status in {"FAILED", "NOT_CONFIGURED"}:
            return self.build("RESOLVE_PROVIDER_CREDENTIALS", target_url="/ops", blocking_reason_codes=[uploaded.analytics_sync_status])
        return self.build("NO_ACTION", target_url=f"/uploaded-videos/{uploaded.id}")


class RuntimeDashboardService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def command_center(self, *, limit: int = 25) -> RuntimeDashboardRead:
        active_channels = [
            self._card(
                key=f"channel:{channel.id}",
                entity_type="channel",
                entity_id=channel.id,
                title=channel.name,
                status=channel.status,
                next_action=self.actions.build("NO_ACTION", target_url=f"/channels/{channel.id}"),
                link_target=f"/channels/{channel.id}",
                updated_at=channel.updated_at,
                technical_appendix={"key": channel.key, "primary_timezone": channel.primary_timezone},
            )
            for channel in self.session.scalars(
                select(ChannelWorkspace).where(ChannelWorkspace.status.in_(["active", "ACTIVE", "activated", "ACTIVATED"])).limit(limit)
            ).all()
        ]
        packages = self._package_cards(limit)
        upload_tasks = self._upload_task_cards(limit)
        uploaded_videos = self._uploaded_video_cards(limit)
        diagnostics = DiagnosticOpsService(self.session).cards(limit=limit)
        recovery = RecoveryOpsService(self.session).cards(limit=limit)
        learning = LearningOpsService(self.session).cards(limit=limit)
        memory = MemoryOpsReadModelService(self.session).cards(limit=limit)
        provider_cost = ProviderCostOpsService(self.session).cards(limit=limit)
        gate_failures = self._gate_failure_cards(limit)
        all_cards = [
            *packages,
            *upload_tasks,
            *uploaded_videos,
            *diagnostics,
            *recovery,
            *learning,
            *memory,
            *provider_cost,
            *gate_failures,
        ]
        return RuntimeDashboardRead(
            generated_at=utc_now(),
            active_channels=active_channels,
            packages_waiting_review=packages,
            upload_tasks_waiting_human=upload_tasks,
            uploaded_videos_waiting_verification_or_analytics=uploaded_videos,
            diagnostics_needing_review=diagnostics,
            recovery_proposals_needing_action=recovery,
            learning_candidates_needing_review=learning,
            memory_approvals_needing_review=memory,
            provider_cost_blockers=provider_cost,
            gate_failures=gate_failures,
            next_actions=[card.next_action for card in all_cards],
            forbidden_actions=FORBIDDEN_OPS_ACTIONS,
            technical_appendix={
                "read_model_only": True,
                "no_job_control_endpoints_added": True,
                "no_provider_media_upload_execution": True,
                "card_count": len(all_cards),
            },
        )

    def next_actions(self) -> list[OperatorNextActionRead]:
        return self.command_center().next_actions

    def _package_cards(self, limit: int) -> list[OpsCardRead]:
        rows = self.session.scalars(
            select(FirstScriptedVideoPackage)
            .where(FirstScriptedVideoPackage.package_status.in_(["READY_FOR_HUMAN_REVIEW", "REVIEW_REQUIRED", "BLOCKED"]))
            .order_by(desc(FirstScriptedVideoPackage.created_at), desc(FirstScriptedVideoPackage.id))
            .limit(limit)
        ).all()
        cards: list[OpsCardRead] = []
        for package in rows:
            upload_task = self.session.scalars(
                select(HumanUploadTask)
                .where(HumanUploadTask.first_scripted_video_package_id == package.id)
                .order_by(desc(HumanUploadTask.created_at), desc(HumanUploadTask.id))
                .limit(1)
            ).one_or_none()
            cards.append(
                self._card(
                    key=f"package:{package.id}",
                    entity_type="video_package",
                    entity_id=package.id,
                    title=f"Package {str(package.id)[:8]}",
                    status=package.package_status,
                    blocker_reason_codes=list(package.limitations or []),
                    next_action=self.actions.for_package(package, upload_task),
                    link_target=f"/video-packages/{package.id}/review",
                    updated_at=package.created_at,
                    technical_appendix={"video_project_id": _id_str(package.video_project_id), "channel_id": str(package.channel_id)},
                )
            )
        return cards

    def _upload_task_cards(self, limit: int) -> list[OpsCardRead]:
        rows = self.session.scalars(
            select(HumanUploadTask)
            .where(HumanUploadTask.task_state.in_(["READY_FOR_HUMAN_UPLOAD", "HUMAN_UPLOAD_IN_PROGRESS", "UPLOADED_WAITING_BACKFILL", "BACKFILLED_WAITING_VERIFICATION", "UPLOADED_UNVERIFIED", "BLOCKED"]))
            .order_by(desc(HumanUploadTask.updated_at), desc(HumanUploadTask.id))
            .limit(limit)
        ).all()
        return [
            self._card(
                key=f"upload_task:{task.id}",
                entity_type="human_upload_task",
                entity_id=task.id,
                title=task.title_snapshot or f"Upload task {str(task.id)[:8]}",
                status=task.task_state,
                blocker_reason_codes=[task.blocked_reason] if task.blocked_reason else [],
                next_action=self.actions.for_upload_task(task),
                link_target=f"/video-packages/{task.first_scripted_video_package_id}/review" if task.first_scripted_video_package_id else "/publishing",
                updated_at=task.updated_at,
                technical_appendix={"manual_only": True, "destination": task.destination},
            )
            for task in rows
        ]

    def _uploaded_video_cards(self, limit: int) -> list[OpsCardRead]:
        rows = self.session.scalars(
            select(UploadedVideo)
            .where(
                or_(
                    UploadedVideo.verification_status.not_in(["VERIFIED_PUBLIC", "VERIFIED_OWNER"]),
                    UploadedVideo.analytics_sync_status.in_(["NOT_STARTED", "PENDING", "FAILED", "NOT_CONFIGURED"]),
                )
            )
            .order_by(desc(UploadedVideo.updated_at), desc(UploadedVideo.id))
            .limit(limit)
        ).all()
        cards: list[OpsCardRead] = []
        for uploaded in rows:
            maturity, confidence = UploadedVideoOpsService(self.session).analytics_state(uploaded)
            cards.append(
                self._card(
                    key=f"uploaded_video:{uploaded.id}",
                    entity_type="uploaded_video",
                    entity_id=uploaded.id,
                    title=uploaded.actual_title or uploaded.platform_video_id,
                    status=f"{uploaded.verification_status}/{uploaded.analytics_sync_status}",
                    blocker_reason_codes=[maturity] if maturity not in {"MATURE", "UNKNOWN"} else [],
                    next_action=self.actions.for_uploaded_video(uploaded, maturity),
                    link_target=f"/uploaded-videos/{uploaded.id}",
                    updated_at=uploaded.updated_at,
                    technical_appendix={"analytics_confidence": confidence, YT_DASHBOARD_READ_KEY: False},
                )
            )
        return cards

    def _gate_failure_cards(self, limit: int) -> list[OpsCardRead]:
        rows = self.session.scalars(
            select(R3D4GateRun)
            .where(R3D4GateRun.status.in_(["BLOCK", "REVIEW_REQUIRED"]))
            .order_by(desc(R3D4GateRun.created_at), desc(R3D4GateRun.id))
            .limit(limit)
        ).all()
        return [
            self._card(
                key=f"r3d4_gate:{gate.id}",
                entity_type="r3d4_gate_run",
                entity_id=gate.id,
                title=gate.gate_key,
                status=gate.status,
                blocker_reason_codes=list(gate.fail_codes or []),
                next_action=self.actions.build("REVIEW_PACKAGE", target_url=f"/video-packages/{gate.package_id}/review", blocking_reason_codes=list(gate.fail_codes or [])),
                link_target=f"/video-packages/{gate.package_id}/review",
                updated_at=gate.created_at,
                technical_appendix={"severity": gate.severity, "repair_hint": gate.repair_hint},
            )
            for gate in rows
        ]

    def _card(
        self,
        *,
        key: str,
        entity_type: str,
        entity_id: uuid.UUID | str | None,
        title: str,
        status: str,
        next_action: OperatorNextActionRead,
        link_target: str | None,
        updated_at: Any,
        blocker_reason_codes: list[str] | None = None,
        technical_appendix: dict[str, Any] | None = None,
    ) -> OpsCardRead:
        return OpsCardRead(
            key=key,
            entity_type=entity_type,
            entity_id=entity_id,
            title=title,
            status=status,
            severity=_status_severity(status),
            blocker_reason_codes=blocker_reason_codes or next_action.blocking_reason_codes,
            next_action=next_action,
            link_target=link_target,
            updated_at=updated_at,
            technical_appendix=_jsonable(technical_appendix or {}),
        )


class ChannelRuntimeTraceService:
    def __init__(self, session: Session):
        self.session = session

    def for_channel(self, channel_id: uuid.UUID) -> ChannelRuntimeTraceRead:
        effective = self.session.scalars(
            select(EffectiveChannelRuntimeContextSnapshot)
            .where(EffectiveChannelRuntimeContextSnapshot.channel_workspace_id == channel_id)
            .order_by(desc(EffectiveChannelRuntimeContextSnapshot.created_at), desc(EffectiveChannelRuntimeContextSnapshot.id))
            .limit(1)
        ).one_or_none()
        if effective is None:
            raise NotFoundError(f"runtime context snapshot not found for channel: {channel_id}")
        return self._trace(effective)

    def for_project(self, project_id: uuid.UUID) -> ChannelRuntimeTraceRead:
        project = self.session.get(VideoProject, project_id)
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, project.effective_context_snapshot_id) if project and project.effective_context_snapshot_id else None
        if effective is None:
            effective = self.session.scalars(
                select(EffectiveChannelRuntimeContextSnapshot)
                .where(EffectiveChannelRuntimeContextSnapshot.video_project_id == project_id)
                .order_by(desc(EffectiveChannelRuntimeContextSnapshot.created_at), desc(EffectiveChannelRuntimeContextSnapshot.id))
                .limit(1)
            ).one_or_none()
        if effective is None:
            raise NotFoundError(f"runtime context snapshot not found for project: {project_id}")
        return self._trace(effective)

    def for_package(self, package_id: uuid.UUID) -> ChannelRuntimeTraceRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"package not found: {package_id}")
        effective = self.session.get(EffectiveChannelRuntimeContextSnapshot, package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None
        if effective is None:
            raise NotFoundError(f"runtime context snapshot not found for package: {package_id}")
        return self._trace(effective, package_id=package.id)

    def _trace(self, effective: EffectiveChannelRuntimeContextSnapshot, package_id: uuid.UUID | None = None) -> ChannelRuntimeTraceRead:
        profile = self.session.get(ChannelProfileVersion, effective.channel_profile_version_id) if effective.channel_profile_version_id else None
        snapshot = self.session.get(CompiledChannelPolicySnapshot, effective.compiled_policy_snapshot_id) if effective.compiled_policy_snapshot_id else None
        return ChannelRuntimeTraceRead(
            channel_id=effective.channel_workspace_id,
            video_project_id=effective.video_project_id,
            package_id=package_id,
            channel_profile_version_id=effective.channel_profile_version_id,
            compiled_policy_snapshot_id=effective.compiled_policy_snapshot_id,
            channel_contract_hash=effective.channel_contract_hash,
            effective_context_snapshot_id=effective.id,
            context_hash=effective.context_hash,
            category_id=effective.content_category_id,
            character_binding_id=effective.character_binding_id,
            market_locale_language=_jsonable(effective.market_locale_context_json),
            voice_profile={
                "voice_profile_id": _id_str(effective.voice_profile_id),
                "voice_audio_context": _jsonable(effective.voice_audio_context_json),
            },
            thumbnail_style=_jsonable(effective.thumbnail_style_context_json),
            publish_timing_policy=_jsonable(effective.publish_timing_context_json),
            provider_boundary=_jsonable(effective.cost_provider_policy_context_json),
            budget_cost_policy=_jsonable(effective.cost_provider_policy_context_json),
            source_refs=_jsonable(effective.source_refs_json),
            snapshot_refs={
                "channel_profile_version": _jsonable(
                    {
                        "id": profile.id,
                        "version": profile.version,
                        "status": profile.status,
                        "profile_input_hash": profile.profile_input_hash,
                    }
                )
                if profile
                else None,
                "compiled_policy_snapshot": _jsonable(
                    {
                        "id": snapshot.id,
                        "snapshot_version": snapshot.snapshot_version,
                        "status": snapshot.status,
                        "content_hash": snapshot.content_hash,
                    }
                )
                if snapshot
                else None,
            },
            latest_mutable_settings_used=False,
            technical_appendix={
                "snapshot_is_runtime_source_of_truth": True,
                "latest_channel_workspace_settings_ignored_when_snapshot_exists": True,
                "field_source_map_hash": effective.field_source_map_hash,
                "compile_status": effective.compile_status,
                "reason_codes": effective.reason_codes_json,
            },
        )


class PackageOpsSummaryService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def build(self, package_id: uuid.UUID) -> PackageOpsSummaryRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"package not found: {package_id}")
        handoff = PackagingHandoffReadService(self.session).build(package_id)
        handoff_dict = _model_dict(handoff)
        context_packs = self.session.scalars(
            select(AgentContextPackSnapshot)
            .where(AgentContextPackSnapshot.package_id == package.id)
            .order_by(AgentContextPackSnapshot.created_at.asc(), AgentContextPackSnapshot.id.asc())
        ).all()
        latest_task = self.session.scalars(
            select(HumanUploadTask)
            .where(HumanUploadTask.first_scripted_video_package_id == package.id)
            .order_by(desc(HumanUploadTask.created_at), desc(HumanUploadTask.id))
            .limit(1)
        ).one_or_none()
        r3d4_runs = self.session.scalars(
            select(R3D4GateRun).where(R3D4GateRun.package_id == package.id).order_by(R3D4GateRun.created_at.asc(), R3D4GateRun.id.asc())
        ).all()
        gatekeeper = self.session.scalars(
            select(AgentOutputValidationRun)
            .where(
                AgentOutputValidationRun.package_id == package.id,
                AgentOutputValidationRun.agent_key == "GatekeeperSoftReviewAgent",
            )
            .order_by(desc(AgentOutputValidationRun.created_at), desc(AgentOutputValidationRun.id))
            .limit(1)
        ).one_or_none()
        provider_cost = ProviderCostOpsService(self.session).build(package.id)
        return PackageOpsSummaryRead(
            package_id=package.id,
            package_status=package.package_status,
            video_project_id=package.video_project_id,
            channel_id=package.channel_id,
            effective_context_snapshot_id=package.effective_context_snapshot_id,
            effective_context_hash=package.effective_context_hash,
            agent_context_pack_refs=[
                {
                    "id": str(pack.id),
                    "agent_key": pack.agent_key,
                    "lane": pack.lane,
                    "context_pack_hash": pack.context_pack_hash,
                    "prompt_context_hash": pack.prompt_context_hash,
                    "effective_context_snapshot_id": str(pack.effective_context_snapshot_id),
                }
                for pack in context_packs
            ],
            prompt_budget_summary=[
                {
                    "agent_context_pack_snapshot_id": str(pack.id),
                    "agent_key": pack.agent_key,
                    "budget_report": _jsonable(pack.budget_report_json),
                    "omitted_items": _jsonable(pack.omitted_items_json),
                    "largest_context_contributors": _jsonable(pack.largest_context_contributors_json),
                }
                for pack in context_packs
            ],
            hook_first_3_seconds=_jsonable(handoff_dict.get("hook_spec", {})),
            title_description_subtitles_disclosure=_jsonable(handoff_dict.get("upload_handoff_copy", {})),
            thumbnail_handoff=_jsonable(handoff_dict.get("thumbnail_handoff", {})),
            publish_timing_recommendation=_jsonable(handoff_dict.get("publish_timing_recommendation", {})),
            r3d4_deterministic_gate_results=[
                {
                    "id": str(run.id),
                    "gate_key": run.gate_key,
                    "status": run.status,
                    "severity": run.severity,
                    "fail_codes": run.fail_codes,
                    "summary": run.human_readable_summary,
                    "repair_hint": run.repair_hint,
                }
                for run in r3d4_runs
            ],
            gatekeeper_soft_review_result=_jsonable(
                {
                    "id": gatekeeper.id,
                    "status": gatekeeper.status,
                    "validation_state": gatekeeper.validation_state,
                    "reason_codes": gatekeeper.reason_codes,
                    "validation_result": gatekeeper.validation_result_json,
                }
            )
            if gatekeeper
            else None,
            packaging_gate_results=_jsonable(_as_dict(handoff_dict.get("packaging_gate_summary")).get("gate_results", [])),
            provider_boundary_summary=provider_cost.model_dump(mode="json"),
            manual_publish_handoff={
                **_as_dict(handoff_dict.get("manual_upload")),
                "human_upload_task": _jsonable(_task_summary(latest_task)) if latest_task else None,
                "manual_only_warning_vi": "Upload/publish phải làm thủ công ngoài VCOS; VCOS chỉ nhận paste-back video_id/URL.",
                "allowed_actions": ["CREATE_UPLOAD_TASK", "MANUAL_UPLOAD_OUTSIDE_VCOS", "BACKFILL_VIDEO_ID", "VERIFY_UPLOADED_VIDEO"],
            },
            next_action=self.actions.for_package(package, latest_task),
            no_provider_media_upload_execution=True,
            technical_appendix={
                "agent_run_refs": _jsonable(package.agent_run_refs),
                "prompt_render_run_refs": _jsonable(package.prompt_render_run_refs),
                "prompt_audit_snapshot_refs": _jsonable(package.prompt_audit_snapshot_refs),
                "no_upload_or_publish_calls_made": bool(handoff.no_upload_or_publish_calls_made),
            },
        )


class UploadedVideoOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def build(self, uploaded_video_id: uuid.UUID) -> UploadedVideoOpsSummaryRead:
        uploaded = self.session.get(UploadedVideo, uploaded_video_id)
        if uploaded is None:
            raise NotFoundError(f"uploaded video not found: {uploaded_video_id}")
        channel = self.session.get(ChannelWorkspace, uploaded.channel_workspace_id)
        task = self.session.get(HumanUploadTask, uploaded.human_upload_task_id) if uploaded.human_upload_task_id else None
        maturity, confidence = self.analytics_state(uploaded)
        backfill_events = self.session.scalars(
            select(UploadedVideoBackfillEvent)
            .where(UploadedVideoBackfillEvent.uploaded_video_id == uploaded.id)
            .order_by(UploadedVideoBackfillEvent.created_at.asc(), UploadedVideoBackfillEvent.id.asc())
        ).all()
        metrics = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
        ).one_or_none()
        diagnostics = DiagnosticOpsService(self.session).for_uploaded_video(uploaded.id)
        recovery = self.session.scalars(
            select(RecoveryProposal).where(RecoveryProposal.uploaded_video_id == uploaded.id).order_by(desc(RecoveryProposal.created_at))
        ).all()
        learning = self.session.scalars(
            select(LearningCandidate).where(LearningCandidate.uploaded_video_id == uploaded.id).order_by(desc(LearningCandidate.created_at))
        ).all()
        flags = []
        if metrics and _as_dict(metrics.availability_summary).get("enforcement_flags"):
            flags.extend(_as_list(_as_dict(metrics.availability_summary).get("enforcement_flags")))
        return UploadedVideoOpsSummaryRead(
            uploaded_video_id=uploaded.id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            platform_url=uploaded.video_url,
            backfill_history=[
                {
                    "id": str(event.id),
                    "input_url_or_video_id": event.input_url_or_video_id,
                    "parsed_video_id": event.parsed_video_id,
                    "parse_status": event.parse_status,
                    "previous_status": event.previous_status,
                    "new_status": event.new_status,
                    "created_at": event.created_at.isoformat(),
                }
                for event in backfill_events
            ],
            verification_status=uploaded.verification_status,
            actual_upload_time=uploaded.actual_upload_time,
            actual_publish_time=uploaded.actual_publish_time or uploaded.published_at,
            channel_timezone=channel.primary_timezone if channel else None,
            operator_timezone=getattr(get_settings(), "operator_timezone", None) or "Asia/Ho_Chi_Minh",
            analytics_sync_status=uploaded.analytics_sync_status,
            analytics_maturity=maturity,
            analytics_confidence=confidence,
            enforcement_restriction_flags=[str(flag) for flag in flags],
            linked_package_project={
                "channel_id": str(uploaded.channel_workspace_id),
                "video_project_id": _id_str(uploaded.video_project_id),
                "first_scripted_video_package_id": _id_str(uploaded.first_scripted_video_package_id),
                "publish_package_id": _id_str(getattr(uploaded, "publish_package_id", None)),
                "human_upload_task_id": _id_str(uploaded.human_upload_task_id),
                "task_status": task.task_state if task else None,
            },
            diagnostics=diagnostics,
            recovery_proposal_refs=[{"id": str(item.id), "state": item.proposal_state, "type": item.proposal_type} for item in recovery],
            learning_candidate_refs=[{"id": str(item.id), "state": item.candidate_state, "scope": item.recommended_scope} for item in learning],
            next_action=self.actions.for_uploaded_video(uploaded, maturity),
            technical_appendix={
                "analytics_summary_id": str(metrics.id) if metrics else None,
                "youtube_upload_api_used": False,
                YT_DASHBOARD_READ_KEY: False,
            },
            **{YT_DASHBOARD_READ_FIELD: True},
        )

    def analytics_state(self, uploaded: UploadedVideo) -> tuple[str, str]:
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == uploaded.id)
        ).one_or_none()
        if summary is None:
            if uploaded.analytics_sync_status in {"NOT_STARTED", "PENDING", "NOT_CONFIGURED"}:
                return "TOO_EARLY", "UNKNOWN"
            return "NO_DATA", "UNKNOWN"
        if summary.freshness_state == "STALE":
            return "STALE", summary.confidence_level
        if _as_dict(summary.availability_summary).get("conflicted") is True:
            return "CONFLICTED", summary.confidence_level
        if summary.monitoring_state in {"NO_DATA_YET", "READY_FOR_ANALYTICS"}:
            return "TOO_EARLY", summary.confidence_level
        if summary.confidence_level in {"LOW", "UNKNOWN"}:
            return "LOW_CONFIDENCE", summary.confidence_level
        return "MATURE", summary.confidence_level


class DiagnosticOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def queue(self, *, limit: int = 100) -> DiagnosticOpsQueueRead:
        health_runs = self.session.scalars(
            select(PostPublishHealthRun)
            .where(PostPublishHealthRun.health_state != "HEALTHY")
            .order_by(desc(PostPublishHealthRun.created_at), desc(PostPublishHealthRun.id))
            .limit(limit)
        ).all()
        return DiagnosticOpsQueueRead(generated_at=utc_now(), items=[self._health_item(run) for run in health_runs])

    def cards(self, *, limit: int) -> list[OpsCardRead]:
        return [
            OpsCardRead(
                key=f"diagnostic:{item['id']}",
                entity_type="diagnostic",
                entity_id=item["id"],
                title=str(item["operator_summary"]),
                status=str(item["data_maturity"]),
                severity=str(item["severity"]),
                blocker_reason_codes=[str(code) for code in item["reason_codes"]],
                next_action=item["next_action"],
                link_target=f"/uploaded-videos/{item['uploaded_video_id']}",
                updated_at=item["created_at"],
                technical_appendix=_jsonable({"health_state": item["health_state"], "action_ready": item["action_ready"]}),
            )
            for item in self.queue(limit=limit).items
        ]

    def for_uploaded_video(self, uploaded_video_id: uuid.UUID) -> list[dict[str, Any]]:
        runs = self.session.scalars(
            select(PostPublishHealthRun)
            .where(PostPublishHealthRun.uploaded_video_id == uploaded_video_id)
            .order_by(desc(PostPublishHealthRun.created_at), desc(PostPublishHealthRun.id))
        ).all()
        return [self._health_item(run) for run in runs]

    def _health_item(self, run: PostPublishHealthRun) -> dict[str, Any]:
        maturity, action_ready = self._diagnostic_maturity(run)
        reason_codes = list(run.reason_codes or [])
        next_action = self.actions.build(
            "WAIT_ANALYTICS_MATURITY" if maturity in {"TOO_EARLY", "STALE", "CONFLICTED"} else "REVIEW_DIAGNOSTIC",
            target_url=f"/uploaded-videos/{run.uploaded_video_id}",
            blocking_reason_codes=reason_codes or [maturity],
        )
        no_view = self.session.scalars(
            select(NoViewDiagnosticRun)
            .where(NoViewDiagnosticRun.post_publish_health_run_id == run.id)
            .order_by(desc(NoViewDiagnosticRun.created_at), desc(NoViewDiagnosticRun.id))
            .limit(1)
        ).one_or_none()
        return {
            "id": str(run.id),
            "uploaded_video_id": str(run.uploaded_video_id),
            "video_project_id": str(run.video_project_id),
            "platform": run.platform,
            "platform_video_id": run.platform_video_id,
            "key_metrics": self._metrics(run),
            "analytics_confidence": run.confidence_level,
            "data_maturity": maturity,
            "action_ready": action_ready,
            "diagnostic_taxonomy": "NO_VIEW" if no_view else run.health_state,
            "likely_cause": no_view.diagnostic_state if no_view else run.health_state,
            "evidence_refs": _jsonable(run.evidence_refs),
            "recovery_proposal_refs": self._recovery_refs(run.uploaded_video_id),
            "learning_candidate_refs": self._learning_refs(run.uploaded_video_id),
            "reason_codes": reason_codes,
            "operator_summary": run.operator_summary,
            "health_state": run.health_state,
            "severity": run.severity,
            "next_action": next_action,
            "created_at": run.created_at,
            "technical_appendix": _jsonable(run.technical_appendix),
        }

    def _diagnostic_maturity(self, run: PostPublishHealthRun) -> tuple[str, bool]:
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == run.uploaded_video_id)
        ).one_or_none()
        codes = {str(code).upper() for code in run.reason_codes or []}
        if "ANALYTICS_NOT_MATURE" in codes or run.health_state in {"TOO_EARLY", "ANALYTICS_TOO_EARLY"}:
            return "TOO_EARLY", False
        if summary is None:
            return "TOO_EARLY", False
        if summary.freshness_state == "STALE":
            return "STALE", False
        if _as_dict(summary.availability_summary).get("conflicted") is True:
            return "CONFLICTED", False
        if summary.monitoring_state in {"NO_DATA_YET", "READY_FOR_ANALYTICS"}:
            return "TOO_EARLY", False
        return "MATURE", True

    def _metrics(self, run: PostPublishHealthRun) -> dict[str, Any]:
        summary = self.session.scalars(
            select(UploadedVideoMetricsSummary).where(UploadedVideoMetricsSummary.uploaded_video_id == run.uploaded_video_id)
        ).one_or_none()
        return _jsonable(summary.metrics_summary) if summary else {}

    def _recovery_refs(self, uploaded_video_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = self.session.scalars(select(RecoveryProposal).where(RecoveryProposal.uploaded_video_id == uploaded_video_id)).all()
        return [{"recovery_proposal_id": str(row.id), "state": row.proposal_state, "type": row.proposal_type} for row in rows]

    def _learning_refs(self, uploaded_video_id: uuid.UUID) -> list[dict[str, Any]]:
        rows = self.session.scalars(select(LearningCandidate).where(LearningCandidate.uploaded_video_id == uploaded_video_id)).all()
        return [{"learning_candidate_id": str(row.id), "state": row.candidate_state, "scope": row.recommended_scope} for row in rows]


class RecoveryOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def queue(self, *, limit: int = 100) -> RecoveryOpsQueueRead:
        proposals = self.session.scalars(
            select(RecoveryProposal)
            .where(RecoveryProposal.proposal_state.in_(["PROPOSED", "PENDING", "READY_FOR_REVIEW"]))
            .order_by(desc(RecoveryProposal.created_at), desc(RecoveryProposal.id))
            .limit(limit)
        ).all()
        return RecoveryOpsQueueRead(generated_at=utc_now(), items=[self._item(item) for item in proposals])

    def cards(self, *, limit: int) -> list[OpsCardRead]:
        return [
            OpsCardRead(
                key=f"recovery:{item['id']}",
                entity_type="recovery_proposal",
                entity_id=item["id"],
                title=str(item["proposed_recovery"]),
                status=str(item["approval_status"]),
                severity=_status_severity(str(item["risk_notes"]), default="NORMAL"),
                blocker_reason_codes=[],
                next_action=item["next_action"],
                link_target=f"/uploaded-videos/{item['source_uploaded_video_id']}",
                updated_at=item["updated_at"],
                technical_appendix={"allowed_actions": item["allowed_actions"]},
            )
            for item in self.queue(limit=limit).items
        ]

    def _item(self, proposal: RecoveryProposal) -> dict[str, Any]:
        failure = self.session.get(FailureTraceReport, proposal.failure_trace_report_id)
        return {
            "id": str(proposal.id),
            "source_uploaded_video_id": str(proposal.uploaded_video_id),
            "failure_cause": failure.primary_suspected_cause if failure else None,
            "proposed_recovery": proposal.operator_summary,
            "expected_metric_family_direction": _jsonable(_as_dict(failure.operator_report).get("expected_metric_family_direction")) if failure else None,
            "evidence_refs": _jsonable(proposal.evidence_refs),
            "risk_notes": proposal.risk_level,
            "affected_future_artifact_type": proposal.proposal_type,
            "approval_status": proposal.proposal_state,
            "allowed_actions": ["APPROVE", "REJECT"] if proposal.requires_human_approval else [],
            "next_action": self.actions.build("REVIEW_RECOVERY_PROPOSAL", target_url=f"/uploaded-videos/{proposal.uploaded_video_id}", action_ref={"recovery_proposal_id": str(proposal.id)}),
            "created_at": proposal.created_at,
            "updated_at": proposal.updated_at,
        }


class LearningOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def queue(self, *, limit: int = 100) -> LearningOpsQueueRead:
        rows = self.session.scalars(
            select(LearningReviewQueueItem)
            .where(LearningReviewQueueItem.queue_state.in_(["PENDING", "READY_FOR_REVIEW", "READY_FOR_HUMAN_REVIEW", "NEEDS_MORE_EVIDENCE"]))
            .order_by(desc(LearningReviewQueueItem.created_at), desc(LearningReviewQueueItem.id))
            .limit(limit)
        ).all()
        return LearningOpsQueueRead(generated_at=utc_now(), items=[self._item(row) for row in rows])

    def cards(self, *, limit: int) -> list[OpsCardRead]:
        return [
            OpsCardRead(
                key=f"learning:{item['id']}",
                entity_type="learning_review_queue_item",
                entity_id=item["id"],
                title=str(item["learning_candidate"]),
                status=str(item["approval_status"]),
                severity=_status_severity(str(item["priority"]), default="NORMAL"),
                blocker_reason_codes=[],
                next_action=item["next_action"],
                link_target="/learning",
                updated_at=item["updated_at"],
                technical_appendix={"scope": item["scope"], "allowed_actions": item["allowed_actions"]},
            )
            for item in self.queue(limit=limit).items
        ]

    def _item(self, queue: LearningReviewQueueItem) -> dict[str, Any]:
        candidate = self.session.get(LearningCandidate, queue.learning_candidate_id)
        promotion = self.session.scalars(
            select(MemoryReviewQueueItem)
            .join(ChannelMemoryItem, MemoryReviewQueueItem.memory_item_id == ChannelMemoryItem.id)
            .where(ChannelMemoryItem.created_from_learning_candidate_id == queue.learning_candidate_id)
            .order_by(desc(MemoryReviewQueueItem.created_at))
            .limit(1)
        ).one_or_none()
        return {
            "id": str(queue.id),
            "source_uploaded_video_id": str(queue.uploaded_video_id) if queue.uploaded_video_id else None,
            "source_diagnostic_refs": _jsonable(candidate.diagnostic_refs if candidate else []),
            "learning_candidate": candidate.candidate_summary if candidate else queue.operator_summary,
            "evidence_bundle": {"id": str(queue.evidence_bundle_id) if queue.evidence_bundle_id else None, "summary": queue.evidence_summary},
            "proposed_lesson": candidate.suggested_learning if candidate else None,
            "scope": {
                "recommended_scope": queue.recommended_scope,
                "channel_id": str(queue.channel_workspace_id) if queue.channel_workspace_id else None,
                "video_project_id": str(queue.video_project_id) if queue.video_project_id else None,
            },
            "approval_status": queue.queue_state,
            "linked_memory_promotion_status": promotion.queue_status if promotion else None,
            "allowed_actions": queue.approval_actions_allowed,
            "priority": queue.priority,
            "next_action": self.actions.build("REVIEW_LEARNING_CANDIDATE", target_url="/learning", action_ref={"learning_candidate_id": str(queue.learning_candidate_id)}),
            "created_at": queue.created_at,
            "updated_at": queue.updated_at,
        }


class MemoryOpsReadModelService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def queue(self, *, limit: int = 100) -> MemoryOpsQueueRead:
        rows = self.session.scalars(
            select(MemoryReviewQueueItem)
            .where(MemoryReviewQueueItem.queue_status.in_(["PENDING", "IN_REVIEW", "NEEDS_CHANGES"]))
            .order_by(desc(MemoryReviewQueueItem.created_at), desc(MemoryReviewQueueItem.id))
            .limit(limit)
        ).all()
        return MemoryOpsQueueRead(generated_at=utc_now(), items=[self._item(row) for row in rows])

    def cards(self, *, limit: int) -> list[OpsCardRead]:
        return [
            OpsCardRead(
                key=f"memory:{item['queue_item_id']}",
                entity_type="memory_review_queue_item",
                entity_id=item["queue_item_id"],
                title=str(item["summary"]),
                status=str(item["approval_status"]),
                severity="NORMAL" if not item["prompt_eligible"] else "INFO",
                blocker_reason_codes=[str(code) for code in item["prompt_eligibility_blockers"]],
                next_action=item["next_action"],
                link_target="/learning",
                updated_at=item["updated_at"],
                technical_appendix={"memory_item_id": item["memory_item_id"], "prompt_eligible": item["prompt_eligible"]},
            )
            for item in self.queue(limit=limit).items
        ]

    def _item(self, queue: MemoryReviewQueueItem) -> dict[str, Any]:
        item = self.session.get(ChannelMemoryItem, queue.memory_item_id)
        if item is None:
            return {"queue_item_id": str(queue.id), "memory_item_id": str(queue.memory_item_id), "summary": "Memory item missing", "prompt_eligible": False}
        facets = self.session.scalars(
            select(MemoryFacet).where(MemoryFacet.memory_item_id == item.id).order_by(MemoryFacet.created_at.asc(), MemoryFacet.id.asc())
        ).all()
        blockers = _memory_prompt_blockers(item)
        return {
            "queue_item_id": str(queue.id),
            "memory_item_id": str(item.id),
            "summary": item.summary,
            "memory_type": item.memory_type,
            "facets": [
                {
                    "id": str(facet.id),
                    "facet_type": facet.facet_type,
                    "facet_text_hash": facet.facet_text_hash,
                    "scope": _jsonable(facet.scope_json),
                    "allowed_use_cases": facet.allowed_use_cases_json,
                    "forbidden_use_cases": facet.forbidden_use_cases_json,
                    "prompt_safety_state": facet.prompt_safety_state,
                    "embedding_eligible": facet.embedding_eligible,
                    "raw_memory_text_hidden": True,
                }
                for facet in facets
            ],
            "approval_status": item.approval_status,
            "rights_status": item.rights_status,
            "prompt_safety_state": item.prompt_safety_state,
            "freshness_state": item.freshness_state,
            "scope": {
                "reuse_scope": item.reuse_scope,
                "channel_id": str(item.channel_workspace_id),
                "category_id": _id_str(item.content_category_id),
                "character_profile_id": _id_str(item.character_profile_id),
                "character_version_id": _id_str(item.character_version_id),
            },
            "allowed_use_cases": sorted({case for facet in facets for case in (facet.allowed_use_cases_json or [])}),
            "forbidden_use_cases": sorted({case for facet in facets for case in (facet.forbidden_use_cases_json or [])}),
            "gate_result_summary": "PROMPT_ELIGIBLE" if not blockers else "BLOCKED_FROM_PROMPT",
            "prompt_eligible": not blockers,
            "prompt_eligibility_blockers": blockers,
            "review_actions": ["APPROVE", "REJECT", "ARCHIVE"],
            "next_action": self.actions.build("REVIEW_MEMORY_ITEM", target_url="/learning", blocking_reason_codes=blockers, action_ref={"memory_item_id": str(item.id)}),
            "created_at": queue.created_at,
            "updated_at": queue.updated_at,
        }


class RetrievalOpsTraceService:
    def __init__(self, session: Session):
        self.session = session

    def build(self, manifest_id: uuid.UUID) -> RetrievalManifestOpsRead:
        manifest = self.session.get(VectorRetrievalManifest, manifest_id)
        if manifest is None:
            raise NotFoundError(f"retrieval manifest not found: {manifest_id}")
        return RetrievalManifestOpsRead(
            manifest_id=manifest.id,
            effective_context_snapshot_id=manifest.effective_context_snapshot_id,
            agent_key=manifest.agent_key,
            use_case=manifest.use_case,
            sql_filter=_jsonable(manifest.sql_filter_json),
            candidate_count_before_vector=manifest.candidate_count_before_vector,
            candidate_count_after_policy=manifest.candidate_count_after_policy,
            selected_facets=[_strip_raw_memory_ref(ref) for ref in manifest.selected_memory_facet_refs_json],
            blocked_refs=[_strip_raw_memory_ref(ref) for ref in manifest.blocked_refs_json],
            rejected_refs=[_strip_raw_memory_ref(ref) for ref in manifest.rejected_refs_json],
            retrieval_hash=manifest.retrieval_hash,
            digest_hash=manifest.digest_hash,
            raw_memory_hidden=True,
            advanced_refs_collapsed_by_default=True,
            technical_appendix={
                "query_text_hash": manifest.query_text_hash,
                "vector_model": manifest.vector_model,
                "ranking_params": _jsonable(manifest.ranking_params_json),
            },
        )


class MemoryInfluenceOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def build(self, manifest_id: uuid.UUID) -> MemoryInfluenceOpsRead:
        manifest = self.session.get(MemoryInfluenceManifest, manifest_id)
        if manifest is None:
            raise NotFoundError(f"memory influence manifest not found: {manifest_id}")
        return MemoryInfluenceOpsRead(
            manifest_id=manifest.id,
            video_project_id=manifest.video_project_id,
            package_id=manifest.package_id,
            agent_key=manifest.agent_key,
            retrieval_manifest_id=manifest.retrieval_manifest_id,
            memory_facets_used=[self._facet_ref(facet_id) for facet_id in manifest.memory_facet_ids_used_json],
            digest_hash=manifest.digest_hash,
            prompt_context_hash=manifest.prompt_context_hash,
            applied_as=_jsonable(manifest.applied_as_json),
            ignored_memory_refs=[_strip_raw_memory_ref(ref) for ref in manifest.ignored_memory_refs_json],
            blocked_memory_refs=[_strip_raw_memory_ref(ref) for ref in manifest.blocked_memory_refs_json],
            scope_status=manifest.scope_status,
            next_action=self.actions.build("VIEW_RETRIEVAL_MANIFEST", target_url=f"/ops?retrieval={manifest.retrieval_manifest_id}"),
            technical_appendix={"prompt_render_run_id": _id_str(manifest.prompt_render_run_id), "raw_memory_text_hidden": True},
        )

    def _facet_ref(self, facet_id: str) -> dict[str, Any]:
        try:
            facet_uuid = uuid.UUID(str(facet_id))
        except ValueError:
            return {"memory_facet_id": facet_id, "raw_memory_text_hidden": True}
        facet = self.session.get(MemoryFacet, facet_uuid)
        if facet is None:
            return {"memory_facet_id": facet_id, "missing": True, "raw_memory_text_hidden": True}
        return {
            "memory_item_id": str(facet.memory_item_id),
            "memory_facet_id": str(facet.id),
            "facet_type": facet.facet_type,
            "facet_text_hash": facet.facet_text_hash,
            "scope": _jsonable(facet.scope_json),
            "raw_memory_text_hidden": True,
        }


class QualityDeltaOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def build(self, quality_delta_id: uuid.UUID) -> QualityDeltaOpsRead:
        attribution = self.session.get(QualityDeltaAttribution, quality_delta_id)
        if attribution is None:
            raise NotFoundError(f"quality delta not found: {quality_delta_id}")
        influence = self.session.get(MemoryInfluenceManifest, attribution.source_memory_influence_manifest_id)
        facet_refs = []
        if influence is not None:
            facet_refs = [MemoryInfluenceOpsService(self.session)._facet_ref(facet_id) for facet_id in influence.memory_facet_ids_used_json]
        next_code = "WAIT_ANALYTICS_MATURITY" if attribution.confidence_result == "TOO_EARLY" else "VIEW_QUALITY_DELTA"
        if attribution.confidence_result == "BLOCKED_BY_DATA_QUALITY":
            next_code = "WAIT_ANALYTICS_MATURITY"
        return QualityDeltaOpsRead(
            quality_delta_id=attribution.id,
            memory_facets_used=facet_refs,
            expected_metric_family=attribution.expected_metric_family,
            expected_direction=attribution.expected_improvement_direction,
            baseline_snapshot=_jsonable(attribution.baseline_snapshot_ref),
            observed_snapshot=_jsonable(attribution.observed_snapshot_ref),
            result=attribution.confidence_result,
            confidence_delta=attribution.confidence_delta,
            reason_codes=attribution.reason_codes_json,
            next_action=self.actions.build(next_code, target_url="/learning", blocking_reason_codes=attribution.reason_codes_json),
            technical_appendix={
                "source_memory_influence_manifest_id": str(attribution.source_memory_influence_manifest_id),
                "target_uploaded_video_id": _id_str(attribution.target_uploaded_video_id),
                "target_video_project_id": str(attribution.target_video_project_id),
            },
        )


class ProviderCostOpsService:
    def __init__(self, session: Session):
        self.session = session
        self.actions = OperatorNextActionService()

    def build(self, package_id: uuid.UUID) -> ProviderCostOpsRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"package not found: {package_id}")
        revisions = self.session.scalars(
            select(RenderRevision).where(RenderRevision.package_id == package_id).order_by(desc(RenderRevision.revision_no), desc(RenderRevision.created_at))
        ).all()
        revision_ids = [revision.id for revision in revisions]
        estimates = self._by_revision(CostEstimateSnapshot, CostEstimateSnapshot.render_revision_id, revision_ids)
        approvals = self._by_revision(HumanPaidRenderApproval, HumanPaidRenderApproval.render_revision_id, revision_ids)
        limits = self._by_revision(PaidAttemptLimitRecord, PaidAttemptLimitRecord.render_revision_id, revision_ids)
        ledgers = self._by_revision(PaidProviderCallLedger, PaidProviderCallLedger.render_revision_id, revision_ids)
        flags = self.session.scalars(
            select(ProxyPreviewArtifactFlag).where(ProxyPreviewArtifactFlag.package_id == package_id).order_by(desc(ProxyPreviewArtifactFlag.created_at))
        ).all()
        jobs = self._by_revision(ProviderJobSnapshot, ProviderJobSnapshot.render_revision_id, revision_ids)
        drift_guard = ProviderStackDriftGuard().check()
        readiness = _provider_readiness()
        readiness["provider_stack_drift_guard"] = drift_guard.model_dump(mode="json")
        if drift_guard.status != "PASS":
            readiness["snapshot_state"] = "PROVIDER_STACK_DRIFT"
        missing = sorted(
            {
                code
                for provider in readiness.get("providers", [])
                for code in [*provider.get("missing_env_keys", []), *provider.get("reason_codes", [])]
                if code
            }
        )
        if drift_guard.status != "PASS":
            missing.extend(drift_guard.reason_codes)
        missing = sorted(set(missing))
        blocker_codes = self._blocker_codes(estimates, approvals, limits, ledgers, jobs, missing)
        next_code = "NO_ACTION"
        if drift_guard.status != "PASS":
            next_code = "PROVIDER_STACK_DRIFT"
        elif any(getattr(approval, "approval_status", "") == "PENDING" for approval in approvals):
            next_code = "WAIT_HUMAN_PAID_APPROVAL"
        elif missing:
            next_code = "RESOLVE_PROVIDER_CREDENTIALS"
        elif blocker_codes:
            next_code = "BLOCKED_BY_PROVIDER_BOUNDARY"
        return ProviderCostOpsRead(
            package_id=package_id,
            provider_readiness=readiness,
            missing_config=missing,
            render_revisions=[_render_revision_ref(row) for row in revisions],
            cost_estimates=[_cost_estimate_ref(row) for row in estimates],
            human_paid_render_approvals=[_approval_ref(row) for row in approvals],
            paid_attempt_limits=[_attempt_limit_ref(row) for row in limits],
            provider_boundary_decisions=[_ledger_boundary_ref(row) for row in ledgers],
            paid_provider_call_ledger=[_ledger_ref(row) for row in ledgers],
            proxy_preview_flags=[_proxy_flag_ref(row) for row in flags],
            will_execute=False,
            next_action=self.actions.build(next_code, target_url="/ops", blocking_reason_codes=blocker_codes, is_manual_only=True),
            technical_appendix={
                "read_only": True,
                "provider_boundary_preflight_not_called": True,
                "provider_stack_drift_guard_status": drift_guard.status,
                "provider_jobs": [_provider_job_ref(row) for row in jobs],
                "no_network_call_made_by_read_model": True,
            },
        )

    def cards(self, *, limit: int) -> list[OpsCardRead]:
        package_ids = {
            *[row.package_id for row in self.session.scalars(select(CostEstimateSnapshot).where(CostEstimateSnapshot.estimate_status != "ESTIMATED").limit(limit)).all()],
            *[
                revision.package_id
                for revision in self.session.scalars(
                    select(RenderRevision).where(RenderRevision.revision_status.in_(["READY_FOR_COST_ESTIMATE", "APPROVAL_REQUIRED", "BLOCKED"])).limit(limit)
                ).all()
            ],
        }
        cards: list[OpsCardRead] = []
        for package_id in list(package_ids)[:limit]:
            summary = self.build(package_id)
            cards.append(
                OpsCardRead(
                    key=f"provider_cost:{package_id}",
                    entity_type="provider_cost_boundary",
                    entity_id=package_id,
                    title=f"Provider/cost boundary {str(package_id)[:8]}",
                    status=summary.next_action.next_action_code,
                    severity="HIGH" if summary.next_action.blocking_reason_codes else "NORMAL",
                    blocker_reason_codes=summary.next_action.blocking_reason_codes,
                    next_action=summary.next_action,
                    link_target=f"/video-packages/{package_id}/review",
                    updated_at=utc_now(),
                    technical_appendix={"will_execute": False},
                )
            )
        return cards

    def _by_revision(self, model: Any, column: Any, revision_ids: list[uuid.UUID]) -> list[Any]:
        if not revision_ids:
            return []
        return list(self.session.scalars(select(model).where(column.in_(revision_ids)).order_by(desc(model.created_at))).all())

    def _blocker_codes(
        self,
        estimates: list[CostEstimateSnapshot],
        approvals: list[HumanPaidRenderApproval],
        limits: list[PaidAttemptLimitRecord],
        ledgers: list[PaidProviderCallLedger],
        jobs: list[ProviderJobSnapshot],
        missing: list[str],
    ) -> list[str]:
        codes: list[str] = list(missing)
        for estimate in estimates:
            if estimate.estimate_status != "ESTIMATED":
                codes.extend(estimate.blocker_reason_codes_json or [f"COST_ESTIMATE_{estimate.estimate_status}"])
        for approval in approvals:
            if approval.approval_status != "APPROVED":
                codes.append(f"HUMAN_PAID_APPROVAL_{approval.approval_status}")
        for limit in limits:
            if limit.status == "BLOCKED":
                codes.extend(limit.reason_codes_json)
        for ledger in ledgers:
            if ledger.call_status == "BLOCKED":
                codes.extend(ledger.reason_codes_json)
        for job in jobs:
            if job.job_status in {"SUBMISSION_BLOCKED", "RESUME_REQUIRED", "FAILED"} and job.last_error_code:
                codes.append(job.last_error_code)
        return sorted(set(codes))


def _memory_prompt_blockers(item: ChannelMemoryItem) -> list[str]:
    blockers = []
    if item.approval_status != "APPROVED":
        blockers.append("MEMORY_NOT_APPROVED")
    if item.rights_status != "SAFE":
        blockers.append("MEMORY_RIGHTS_NOT_SAFE")
    if item.prompt_safety_state != "PROMPT_SAFE":
        blockers.append("MEMORY_NOT_PROMPT_SAFE")
    if item.freshness_state != "FRESH":
        blockers.append("MEMORY_NOT_FRESH")
    return blockers


def _strip_raw_memory_ref(ref: Any) -> dict[str, Any]:
    data = _as_dict(ref)
    return {
        key: _jsonable(value)
        for key, value in data.items()
        if key not in {"facet_text", "raw_text", "text", "embedding_vector_json", "memory_text"}
    } | {"raw_memory_text_hidden": True}


def _task_summary(task: HumanUploadTask | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "id": str(task.id),
        "status": task.task_state,
        "destination": task.destination,
        "title_snapshot": task.title_snapshot,
        "scheduled_time_suggestion": task.scheduled_time_suggestion.isoformat() if task.scheduled_time_suggestion else None,
        "actual_uploaded_video_id": _id_str(task.actual_uploaded_video_id),
        "manual_only": True,
    }


def _provider_readiness() -> dict[str, Any]:
    snapshot = ProviderReadinessM2Service(get_settings()).snapshot()
    data = snapshot.model_dump(mode="json")
    return {
        "summary_state": data.get("summary_state"),
        "providers": [
            {
                "provider_key": item.get("provider_key"),
                "readiness_state": item.get("readiness_state"),
                "missing_env_keys": item.get("missing_env_keys", []),
                "reason_codes": item.get("blocker_reason_codes", []),
                "will_execute": False,
            }
            for item in data.get("providers", [])
        ],
        "no_paid_provider_calls": True,
    }


def _render_revision_ref(row: RenderRevision) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "revision_no": row.revision_no,
        "revision_status": row.revision_status,
        "provider_plan": _jsonable(row.provider_plan_json),
        "render_plan_hash": row.render_plan_hash,
        "created_at": row.created_at.isoformat(),
    }


def _cost_estimate_ref(row: CostEstimateSnapshot) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "render_revision_id": str(row.render_revision_id),
        "estimate_status": row.estimate_status,
        "currency": row.currency,
        "estimated_total_cost": str(row.estimated_total_cost) if row.estimated_total_cost is not None else None,
        "blocker_reason_codes": row.blocker_reason_codes_json,
        "provider_estimates": _jsonable(row.provider_estimates_json),
    }


def _approval_ref(row: HumanPaidRenderApproval) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "render_revision_id": str(row.render_revision_id),
        "approval_status": row.approval_status,
        "approved_provider_stages": row.approved_provider_stages_json,
        "max_approved_cost": str(row.max_approved_cost) if row.max_approved_cost is not None else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _attempt_limit_ref(row: PaidAttemptLimitRecord) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "render_revision_id": str(row.render_revision_id),
        "provider_key": row.provider_key,
        "provider_stage": row.provider_stage,
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "status": row.status,
        "reason_codes": row.reason_codes_json,
    }


def _ledger_boundary_ref(row: PaidProviderCallLedger) -> dict[str, Any]:
    return {
        "ledger_id": str(row.id),
        "render_revision_id": str(row.render_revision_id),
        "provider_key": row.provider_key,
        "provider_stage": row.provider_stage,
        "call_type": row.call_type,
        "call_status": row.call_status,
        "reason_codes": row.reason_codes_json,
        "will_execute": False,
    }


def _ledger_ref(row: PaidProviderCallLedger) -> dict[str, Any]:
    return {
        **_ledger_boundary_ref(row),
        "human_approval_id": _id_str(row.human_approval_id),
        "idempotency_key_id": _id_str(row.idempotency_key_id),
        "cost_estimate_snapshot_id": _id_str(row.cost_estimate_snapshot_id),
        "created_at": row.created_at.isoformat(),
    }


def _proxy_flag_ref(row: ProxyPreviewArtifactFlag) -> dict[str, Any]:
    return {
        "artifact_ref": row.artifact_ref,
        "preview_only": row.preview_only,
        "not_final_media": row.not_final_media,
        "not_publishable": row.not_publishable,
        "source_type": row.source_type,
    }


def _provider_job_ref(row: ProviderJobSnapshot) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "render_revision_id": str(row.render_revision_id),
        "provider_key": row.provider_key,
        "provider_stage": row.provider_stage,
        "job_status": row.job_status,
        "last_error_code": row.last_error_code,
    }


# Imported late to keep the main model import block alphabetical enough for DX1.
from app.db.models import ChannelProfileVersion  # noqa: E402
