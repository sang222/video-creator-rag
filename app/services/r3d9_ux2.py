from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts.r3d9 import (
    PackagingGateRerunRecordRead,
    PackagingPatchApplyRunRead,
    PackagingPatchApprovalDecisionRead,
    PackagingProposedPatchRead,
    PackagingReviewQueueItemRead,
    PackagingReviewQueueRead,
)
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    Artifact,
    ArtifactVersion,
    FirstScriptedVideoPackage,
    PackagingGateRerunRecord,
    PackagingPatchApplyRun,
    PackagingPatchApprovalDecision,
    PackagingProposedPatch,
    PackagingReviewQueueItem,
    R3D4GateRun,
    VideoProject,
)
from app.services.m1 import PackagingHandoffReadService
from app.services.r3d3 import stable_hash


UNRESOLVED_QUEUE_STATUSES = {
    "PENDING_PATCH",
    "PATCH_PROPOSED",
    "PENDING_HUMAN_REVIEW",
    "APPROVED",
    "REJECTED",
    "NEEDS_CHANGES",
    "APPLIED",
    "GATE_RERUN_REQUIRED",
}

READY_PATCH_STATUSES = {"READY_FOR_REVIEW", "APPROVED", "APPLIED"}

ISSUE_ACTION_COPY: dict[str, dict[str, str]] = {
    "HOOK_PROMISE_MISSING": {
        "title": "Hook thiếu promise rõ ràng",
        "why": "Người xem chưa biết video hứa trả lời điều gì.",
        "fix": "Duyệt patch bổ sung promise và payoff location cho hook.",
        "section": "Hook Review",
    },
    "SUBTITLE_REFS_MISSING": {
        "title": "Chưa có subtitle refs",
        "why": "Operator chưa biết subtitle là draft hay final.",
        "fix": "Duyệt patch subtitle handoff hoặc mark subtitle_not_ready có lý do.",
        "section": "Upload Copy / Subtitle",
    },
    "THUMBNAIL_BRIEF_MISSING": {
        "title": "Thiếu thumbnail brief",
        "why": "Chưa có concept/overlay/subject để human tạo thumbnail.",
        "fix": "Duyệt patch thumbnail brief.",
        "section": "Thumbnail Handoff",
    },
    "PUBLISH_WINDOW_MISSING": {
        "title": "Thiếu publish window",
        "why": "VCOS chưa có khung giờ publish khuyến nghị theo frozen context.",
        "fix": "Duyệt package-level ManualPublishTimingOverride. Không mutate Channel Contract.",
        "section": "Publish Timing",
    },
    "TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM": {
        "title": "Title đang hứa quá mức",
        "why": "Title claim chưa được script/evidence trả đủ.",
        "fix": "Duyệt title rewrite patch hoặc reject và request changes.",
        "section": "Upload Copy",
    },
    "DISCLOSURE_CONFLICT": {
        "title": "Disclosure đang mâu thuẫn",
        "why": "Metadata/upload copy không khớp rights/disclosure review.",
        "fix": "Duyệt patch copy/disclosure wording.",
        "section": "Disclosure / Upload Copy",
    },
    "UNSUPPORTED_CTA": {
        "title": "CTA chưa có bằng chứng hỗ trợ",
        "why": "Copy đang nhắc asset/demo/checklist chưa tồn tại.",
        "fix": "Duyệt patch xoá hoặc hạ claim CTA.",
        "section": "Upload Copy",
    },
    "FAKE_CHECKLIST": {
        "title": "Checklist chưa tồn tại",
        "why": "Copy đang hứa checklist chưa có artifact thật.",
        "fix": "Duyệt patch xoá hoặc hạ claim checklist.",
        "section": "Upload Copy",
    },
    "FAKE_DEMO": {
        "title": "Demo chưa tồn tại",
        "why": "Copy đang hứa demo chưa có artifact thật.",
        "fix": "Duyệt patch xoá hoặc hạ claim demo.",
        "section": "Upload Copy",
    },
}


PATCH_TYPE_RERUN_GATES: dict[str, list[str]] = {
    "HOOK_SPEC": ["HookTruthfulnessGate", "HookPayoffGate", "VisualHookRelevanceGate"],
    "METADATA": ["TitlePromiseGate", "MetadataTruthfulnessGate", "DescriptionCompletenessGate"],
    "THUMBNAIL_BRIEF": ["ThumbnailTruthfulnessGate", "MobileThumbnailLegibilityGate", "CharacterThumbnailConsistencyGate"],
    "SUBTITLE_HANDOFF": ["CaptionCoverageGate"],
    "PUBLISH_TIMING_OVERRIDE": ["PublishTimingComplianceGate"],
    "DISCLOSURE_COPY": ["DisclosureConsistencyGate", "UploadCopyTruthfulnessGate", "ManualPublishOnlyGate"],
    "UPLOAD_COPY": ["DisclosureConsistencyGate", "UploadCopyTruthfulnessGate", "ManualPublishOnlyGate"],
}


@dataclass(frozen=True)
class PackagingPatchRoute:
    proposal_source: str
    patch_type: str
    routed_agent_key: str | None
    route_key: str
    deterministic: bool = False


class PackagingPatchRouter:
    ROUTES: dict[str, PackagingPatchRoute] = {
        "HOOK_PROMISE_MISSING": PackagingPatchRoute("EXISTING_AGENT", "HOOK_SPEC", "ScriptRewriteAgent", "script_rewrite_agent.patch_proposal"),
        "TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM": PackagingPatchRoute("EXISTING_AGENT", "METADATA", "PublishingMetadataAgent", "publishing_metadata_agent.patch_proposal"),
        "THUMBNAIL_BRIEF_MISSING": PackagingPatchRoute("EXISTING_AGENT", "THUMBNAIL_BRIEF", "ThumbnailBriefAgent", "thumbnail_brief_agent.patch_proposal"),
        "SUBTITLE_REFS_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "SUBTITLE_HANDOFF", None, "subtitle_handoff_service.patch_proposal", True),
        "PUBLISH_WINDOW_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "PUBLISH_TIMING_OVERRIDE", None, "publish_timing_service.patch_proposal", True),
        "DISCLOSURE_CONFLICT": PackagingPatchRoute("EXISTING_AGENT", "DISCLOSURE_COPY", "RightsDisclosureReviewer+UploadCardCopyAgent", "rights_disclosure_upload_copy.patch_proposal"),
        "UNSUPPORTED_CTA": PackagingPatchRoute("EXISTING_AGENT", "UPLOAD_COPY", "UploadCardCopyAgent", "upload_card_copy_agent.patch_proposal"),
        "FAKE_CHECKLIST": PackagingPatchRoute("EXISTING_AGENT", "UPLOAD_COPY", "UploadCardCopyAgent", "upload_card_copy_agent.patch_proposal"),
        "FAKE_DEMO": PackagingPatchRoute("EXISTING_AGENT", "UPLOAD_COPY", "UploadCardCopyAgent", "upload_card_copy_agent.patch_proposal"),
    }

    def route(self, *, issue_code: str, gate_key: str | None = None) -> PackagingPatchRoute | None:
        if issue_code in self.ROUTES:
            return self.ROUTES[issue_code]
        if gate_key in {"HookTruthfulnessGate", "HookPayoffGate", "VisualHookRelevanceGate"}:
            return PackagingPatchRoute("EXISTING_AGENT", "HOOK_SPEC", "ScriptPlanningAgent", "script_planning_agent.patch_proposal")
        return None


class PackagingReviewQueueService:
    def __init__(self, session: Session):
        self.session = session

    def build_from_gates(self, package_id: uuid.UUID) -> PackagingReviewQueueRead:
        package = self._require_package(package_id)
        handoff = PackagingHandoffReadService(self.session).build(package.id)
        active_keys: set[tuple[str, str, str | None]] = set()

        latest_r3d4_runs: dict[str, R3D4GateRun] = {}
        for run in self.session.scalars(
            select(R3D4GateRun)
            .where(R3D4GateRun.package_id == package.id)
            .order_by(desc(R3D4GateRun.created_at), desc(R3D4GateRun.id))
        ).all():
            latest_r3d4_runs.setdefault(run.gate_key, run)
        for run in latest_r3d4_runs.values():
            if run.status not in {"BLOCK", "REVIEW_REQUIRED"}:
                continue
            for issue_code in run.fail_codes or []:
                item = self._upsert_item(
                    package=package,
                    gate_key=run.gate_key,
                    issue_code=issue_code,
                    gate_status=run.status,
                    checked_artifact_refs=run.checked_artifact_refs,
                    source_gate_run_id=run.id,
                    source_gate_batch_id=run.gate_batch_run_id,
                )
                active_keys.add((item.gate_key, item.issue_code, item.target_artifact_ref))
                PackagingPatchProposalService(self.session).ensure_proposal(item.id)

        for gate in handoff.packaging_gate_summary.gate_results:
            if gate.status not in {"BLOCK", "REVIEW_REQUIRED"}:
                continue
            for issue_code in gate.reason_codes:
                item = self._upsert_item(
                    package=package,
                    gate_key=gate.gate_key,
                    issue_code=issue_code,
                    gate_status=gate.status,
                    checked_artifact_refs=gate.checked_artifact_refs,
                    source_gate_run_id=None,
                    source_gate_batch_id=None,
                )
                active_keys.add((item.gate_key, item.issue_code, item.target_artifact_ref))
                PackagingPatchProposalService(self.session).ensure_proposal(item.id)

        pass_gates = {gate.gate_key for gate in handoff.packaging_gate_summary.gate_results if gate.status == "PASS"}
        latest_r3d4_by_gate: dict[str, str] = {}
        for gate_key, status in self.session.execute(
            select(R3D4GateRun.gate_key, R3D4GateRun.status)
            .where(R3D4GateRun.package_id == package.id)
            .order_by(desc(R3D4GateRun.created_at), desc(R3D4GateRun.id))
        ).all():
            latest_r3d4_by_gate.setdefault(gate_key, status)
        pass_gates.update(gate_key for gate_key, status in latest_r3d4_by_gate.items() if status == "PASS")
        self._close_passed_gates(package.id, pass_gates, active_keys)
        self.session.flush()
        return self.read(package.id)

    def read(self, package_id: uuid.UUID) -> PackagingReviewQueueRead:
        package = self._require_package(package_id)
        items = self.session.scalars(
            select(PackagingReviewQueueItem)
            .where(PackagingReviewQueueItem.package_id == package.id)
            .order_by(PackagingReviewQueueItem.created_at.asc(), PackagingReviewQueueItem.id.asc())
        ).all()
        handoff = PackagingHandoffReadService(self.session).build(package.id)
        unresolved = [item for item in items if item.status in UNRESOLVED_QUEUE_STATUSES]
        gate_status = self._combined_gate_status(package.id, handoff.packaging_gate_summary.overall_status)
        verdict = self._review_verdict(package, unresolved, gate_status)
        upload_allowed = verdict == "READY_FOR_MANUAL_UPLOAD"
        return PackagingReviewQueueRead(
            package_id=package.id,
            review_verdict=verdict,
            plain_language_status=_verdict_label(verdict),
            must_fix_count=sum(1 for item in unresolved if item.severity in {"BLOCK", "REVIEW_REQUIRED"}),
            next_safe_action=_next_safe_action(verdict, unresolved),
            upload_task_creation_allowed=upload_allowed,
            items=[self._read_item(item) for item in items],
            technical_appendix={
                "source": "PackagingReviewQueueService",
                "packaging_gate_overall_status": gate_status,
                "unresolved_item_count": len(unresolved),
                "no_provider_media_upload_execution": True,
                "does_not_mutate_channel_contract": True,
                "does_not_mutate_effective_context_snapshot": True,
            },
        )

    def has_unresolved_blockers(self, package_id: uuid.UUID) -> bool:
        return (
            self.session.scalar(
                select(func.count())
                .select_from(PackagingReviewQueueItem)
                .where(
                    PackagingReviewQueueItem.package_id == package_id,
                    PackagingReviewQueueItem.status.in_(UNRESOLVED_QUEUE_STATUSES),
                )
            )
            or 0
        ) > 0

    def assert_upload_task_allowed(self, package_id: uuid.UUID) -> None:
        package = self._require_package(package_id)
        queue = self.read(package.id)
        if not queue.upload_task_creation_allowed:
            raise ValidationFailureError(f"PACKAGING_REVIEW_UNRESOLVED: {queue.review_verdict}")

    def close_item(self, item_id: uuid.UUID, *, reason_code: str = "GATE_PASS_AFTER_RERUN") -> PackagingReviewQueueItem:
        item = self.session.get(PackagingReviewQueueItem, item_id)
        if item is None:
            raise NotFoundError(f"packaging review queue item not found: {item_id}")
        item.status = "CLOSED"
        item.next_action_code = reason_code
        self.session.flush()
        return item

    def _upsert_item(
        self,
        *,
        package: FirstScriptedVideoPackage,
        gate_key: str,
        issue_code: str,
        gate_status: str,
        checked_artifact_refs: list[dict[str, Any]],
        source_gate_run_id: uuid.UUID | None,
        source_gate_batch_id: uuid.UUID | None,
    ) -> PackagingReviewQueueItem:
        target_type, target_ref = _target_from_refs(checked_artifact_refs)
        copy = _issue_copy(issue_code)
        existing = self.session.scalars(
            select(PackagingReviewQueueItem)
            .where(
                PackagingReviewQueueItem.package_id == package.id,
                PackagingReviewQueueItem.gate_key == gate_key,
                PackagingReviewQueueItem.issue_code == issue_code,
                PackagingReviewQueueItem.target_artifact_ref == target_ref,
            )
            .order_by(desc(PackagingReviewQueueItem.created_at), desc(PackagingReviewQueueItem.id))
            .limit(1)
        ).one_or_none()
        severity = "BLOCK" if gate_status == "BLOCK" else "REVIEW_REQUIRED" if gate_status == "REVIEW_REQUIRED" else "WARNING"
        if existing is None:
            existing = PackagingReviewQueueItem(
                package_id=package.id,
                video_project_id=package.video_project_id,
                effective_context_snapshot_id=package.effective_context_snapshot_id,
                gate_key=gate_key,
                issue_code=issue_code,
                severity=severity,
                target_artifact_type=target_type,
                target_artifact_ref=target_ref,
                source_gate_run_id=source_gate_run_id,
                source_gate_batch_id=source_gate_batch_id,
                status="PENDING_PATCH",
                next_action_code="NEEDS_PROPOSED_PATCH",
                human_readable_title=copy["title"],
                human_readable_why=copy["why"],
                human_readable_fix=copy["fix"],
            )
            self.session.add(existing)
        else:
            existing.severity = severity
            existing.target_artifact_type = target_type
            existing.source_gate_run_id = source_gate_run_id or existing.source_gate_run_id
            existing.source_gate_batch_id = source_gate_batch_id or existing.source_gate_batch_id
            existing.human_readable_title = copy["title"]
            existing.human_readable_why = copy["why"]
            existing.human_readable_fix = copy["fix"]
            if existing.status == "CLOSED":
                existing.status = "PENDING_PATCH"
                existing.next_action_code = "NEEDS_PROPOSED_PATCH"
        self.session.flush()
        return existing

    def _close_passed_gates(
        self,
        package_id: uuid.UUID,
        pass_gates: set[str],
        active_keys: set[tuple[str, str, str | None]],
    ) -> None:
        if not pass_gates:
            return
        rows = self.session.scalars(
            select(PackagingReviewQueueItem).where(
                PackagingReviewQueueItem.package_id == package_id,
                PackagingReviewQueueItem.gate_key.in_(pass_gates),
                PackagingReviewQueueItem.status.in_(UNRESOLVED_QUEUE_STATUSES),
            )
        ).all()
        for item in rows:
            if (item.gate_key, item.issue_code, item.target_artifact_ref) not in active_keys:
                item.status = "CLOSED"
                item.next_action_code = "GATE_PASS_AFTER_RERUN"

    def _read_item(self, item: PackagingReviewQueueItem) -> PackagingReviewQueueItemRead:
        patch = PackagingPatchProposalService(self.session).latest_patch_for_item(item.id)
        return PackagingReviewQueueItemRead(
            id=item.id,
            package_id=item.package_id,
            video_project_id=item.video_project_id,
            effective_context_snapshot_id=item.effective_context_snapshot_id,
            gate_key=item.gate_key,
            issue_code=item.issue_code,
            severity=item.severity,
            target_artifact_type=item.target_artifact_type,
            target_artifact_ref=item.target_artifact_ref,
            source_gate_run_id=item.source_gate_run_id,
            source_gate_batch_id=item.source_gate_batch_id,
            status=item.status,
            next_action_code=item.next_action_code,
            human_readable_title=item.human_readable_title,
            human_readable_why=item.human_readable_why,
            human_readable_fix=item.human_readable_fix,
            section=_issue_copy(item.issue_code)["section"],
            proposed_patch=PackagingProposedPatchRead.model_validate(patch) if patch else None,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _review_verdict(
        self,
        package: FirstScriptedVideoPackage,
        unresolved: list[PackagingReviewQueueItem],
        gate_status: str,
    ) -> str:
        if package.package_status == "WAITING_PROVIDER_CONFIG":
            return "WAITING_PROVIDER_CONFIG"
        if any(item.severity == "BLOCK" for item in unresolved) or gate_status == "BLOCK":
            return "BLOCKED"
        if unresolved or gate_status == "REVIEW_REQUIRED":
            return "REVIEW_REQUIRED"
        return "READY_FOR_MANUAL_UPLOAD"

    def _combined_gate_status(self, package_id: uuid.UUID, m1_gate_status: str) -> str:
        statuses = [m1_gate_status]
        latest_by_gate: dict[str, str] = {}
        for gate_key, status in self.session.execute(
            select(R3D4GateRun.gate_key, R3D4GateRun.status)
            .where(R3D4GateRun.package_id == package_id)
            .order_by(desc(R3D4GateRun.created_at), desc(R3D4GateRun.id))
        ).all():
            latest_by_gate.setdefault(gate_key, status)
        statuses.extend(latest_by_gate.values())
        if "BLOCK" in statuses:
            return "BLOCK"
        if "REVIEW_REQUIRED" in statuses:
            return "REVIEW_REQUIRED"
        return "PASS"

    def _require_package(self, package_id: uuid.UUID) -> FirstScriptedVideoPackage:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        return package


class PackagingPatchProposalService:
    def __init__(self, session: Session, *, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.router = PackagingPatchRouter()

    def ensure_proposal(self, queue_item_id: uuid.UUID) -> PackagingProposedPatch | None:
        item = self._require_item(queue_item_id)
        existing = self.latest_patch_for_item(item.id)
        if existing and existing.status not in {"REJECTED", "SUPERSEDED"}:
            return existing
        route = self.router.route(issue_code=item.issue_code, gate_key=item.gate_key)
        if route is None:
            item.next_action_code = "NEEDS_PROPOSED_PATCH"
            return None
        if route.deterministic:
            return self._create_deterministic_patch(item, route)
        item.next_action_code = "NEEDS_PROPOSED_PATCH"
        if not self._llm_patch_proposal_enabled():
            return None
        item.next_action_code = "NEEDS_PROPOSED_PATCH"
        return None

    def latest_patch_for_item(self, queue_item_id: uuid.UUID) -> PackagingProposedPatch | None:
        return self.session.scalars(
            select(PackagingProposedPatch)
            .where(PackagingProposedPatch.queue_item_id == queue_item_id)
            .order_by(desc(PackagingProposedPatch.created_at), desc(PackagingProposedPatch.id))
            .limit(1)
        ).one_or_none()

    def _create_deterministic_patch(self, item: PackagingReviewQueueItem, route: PackagingPatchRoute) -> PackagingProposedPatch:
        package = self.session.get(FirstScriptedVideoPackage, item.package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {item.package_id}")
        payload = self._deterministic_payload(item, route, package)
        patch_hash = stable_hash(payload)
        patch = PackagingProposedPatch(
            queue_item_id=item.id,
            package_id=item.package_id,
            proposal_source=route.proposal_source,
            routed_agent_key=route.routed_agent_key,
            patch_type=route.patch_type,
            before_snapshot_ref=f"first_scripted_video_package:{package.id}:artifacts:{stable_hash(package.artifacts)}",
            proposed_patch_json=payload["proposed_patch_json"],
            after_preview_json=payload["after_preview_json"],
            affected_artifact_refs_json=payload["affected_artifact_refs_json"],
            risk_level=payload["risk_level"],
            requires_human_approval=True,
            patch_hash=patch_hash,
            status="READY_FOR_REVIEW",
        )
        self.session.add(patch)
        item.status = "PENDING_HUMAN_REVIEW"
        item.next_action_code = "REVIEW_PROPOSED_PATCH"
        self.session.flush()
        return patch

    def _deterministic_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        if route.patch_type == "SUBTITLE_HANDOFF":
            proposed = {
                "operation": "create_subtitle_handoff",
                "subtitle_state": "SUBTITLE_NOT_READY",
                "reason_code": item.issue_code,
                "operator_note_vi": "Subtitle chưa sẵn sàng; không được ghi là final khi chưa có refs.",
                "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
            }
            return {
                "proposed_patch_json": proposed,
                "after_preview_json": {"subtitle_handoff": proposed, "upload_copy_warning": "subtitle_not_ready"},
                "affected_artifact_refs_json": [{"artifact_key": "subtitle_package"}, {"artifact_key": "upload_card_copy"}],
                "risk_level": "LOW",
            }
        if route.patch_type == "PUBLISH_TIMING_OVERRIDE":
            proposed = {
                "operation": "create_manual_publish_timing_override",
                "manual_publish_only": True,
                "publish_window_state": "NEEDS_HUMAN_SELECTION",
                "reason_code": item.issue_code,
                "package_id": str(package.id),
                "effective_context_snapshot_id": str(package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None,
                "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
            }
            return {
                "proposed_patch_json": proposed,
                "after_preview_json": {
                    "manual_publish_timing_override": proposed,
                    "next_operator_action_vi": "Chọn khung giờ publish thủ công cho package này; không sửa policy/snapshot.",
                },
                "affected_artifact_refs_json": [{"artifact_key": "publish_timing"}, {"artifact_key": "manual_publish_handoff"}],
                "risk_level": "LOW",
            }
        return {
            "proposed_patch_json": {"operation": "route_to_existing_agent", "route_key": route.route_key, "reason_code": item.issue_code},
            "after_preview_json": {},
            "affected_artifact_refs_json": [{"artifact_key": item.target_artifact_ref or item.target_artifact_type}],
            "risk_level": "MEDIUM",
        }

    def _llm_patch_proposal_enabled(self) -> bool:
        return bool(self.settings.llm_real_execution_enabled and self.settings.real_llm_package_run_enabled)

    def _require_item(self, queue_item_id: uuid.UUID) -> PackagingReviewQueueItem:
        item = self.session.get(PackagingReviewQueueItem, queue_item_id)
        if item is None:
            raise NotFoundError(f"packaging review queue item not found: {queue_item_id}")
        return item


class PackagingPatchApprovalService:
    def __init__(self, session: Session):
        self.session = session

    def approve(self, patch_id: uuid.UUID, *, decided_by: str = "operator", rationale: str | None = None) -> PackagingPatchApprovalDecisionRead:
        return self._decide(patch_id, decision="APPROVE", decided_by=decided_by, rationale=rationale)

    def reject(self, patch_id: uuid.UUID, *, decided_by: str = "operator", rationale: str | None = None) -> PackagingPatchApprovalDecisionRead:
        return self._decide(patch_id, decision="REJECT", decided_by=decided_by, rationale=rationale)

    def request_changes(self, patch_id: uuid.UUID, *, decided_by: str = "operator", rationale: str | None = None) -> PackagingPatchApprovalDecisionRead:
        return self._decide(patch_id, decision="REQUEST_CHANGES", decided_by=decided_by, rationale=rationale)

    def _decide(
        self,
        patch_id: uuid.UUID,
        *,
        decision: str,
        decided_by: str,
        rationale: str | None,
    ) -> PackagingPatchApprovalDecisionRead:
        patch = self._require_patch(patch_id)
        item = self.session.get(PackagingReviewQueueItem, patch.queue_item_id)
        record = PackagingPatchApprovalDecision(
            proposed_patch_id=patch.id,
            decision=decision,
            decided_by=decided_by,
            rationale=rationale,
        )
        self.session.add(record)
        if decision == "APPROVE":
            patch.status = "APPROVED"
            if item is not None:
                item.status = "APPROVED"
                item.next_action_code = "APPLY_APPROVED_PATCH"
        elif decision == "REJECT":
            patch.status = "REJECTED"
            if item is not None:
                item.status = "REJECTED"
                item.next_action_code = "PATCH_REJECTED_NEEDS_NEW_PROPOSAL"
        else:
            patch.status = "DRAFT"
            if item is not None:
                item.status = "NEEDS_CHANGES"
                item.next_action_code = "REQUEST_PATCH_CHANGES"
        self.session.flush()
        return PackagingPatchApprovalDecisionRead.model_validate(record)

    def _require_patch(self, patch_id: uuid.UUID) -> PackagingProposedPatch:
        patch = self.session.get(PackagingProposedPatch, patch_id)
        if patch is None:
            raise NotFoundError(f"packaging proposed patch not found: {patch_id}")
        return patch


class PackagingPatchApplyService:
    def __init__(self, session: Session):
        self.session = session

    def apply(self, patch_id: uuid.UUID) -> PackagingPatchApplyRunRead:
        patch = self.session.get(PackagingProposedPatch, patch_id)
        if patch is None:
            raise NotFoundError(f"packaging proposed patch not found: {patch_id}")
        if patch.status != "APPROVED":
            return PackagingPatchApplyRunRead.model_validate(self._blocked_run(patch, ["PATCH_NOT_APPROVED"]))
        package = self.session.get(FirstScriptedVideoPackage, patch.package_id)
        if package is None or package.video_project_id is None:
            return PackagingPatchApplyRunRead.model_validate(self._blocked_run(patch, ["PACKAGE_OR_PROJECT_MISSING"]))
        project = self.session.get(VideoProject, package.video_project_id)
        if project is None:
            return PackagingPatchApplyRunRead.model_validate(self._blocked_run(patch, ["PROJECT_MISSING"]))

        artifact_type = _artifact_type_for_patch(patch.patch_type)
        artifact = self.session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project.id, Artifact.artifact_type == artifact_type)
            .order_by(desc(Artifact.created_at), desc(Artifact.id))
            .limit(1)
        ).one_or_none()
        if artifact is None:
            artifact = Artifact(
                video_project_id=project.id,
                artifact_type=artifact_type,
                current_version_id=None,
                status="approved",
                created_by_user_id=project.created_by_user_id,
            )
            self.session.add(artifact)
            self.session.flush()
        parent_version_id = artifact.current_version_id
        current_max = self.session.scalar(
            select(func.max(ArtifactVersion.version_number)).where(ArtifactVersion.artifact_id == artifact.id)
        ) or 0
        content = {
            "patch_id": str(patch.id),
            "queue_item_id": str(patch.queue_item_id),
            "package_id": str(patch.package_id),
            "patch_type": patch.patch_type,
            "proposed_patch": patch.proposed_patch_json,
            "after_preview": patch.after_preview_json,
            "manual_approval_required": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        version_hash = stable_hash(content)
        version = ArtifactVersion(
            artifact_id=artifact.id,
            version_number=current_max + 1,
            parent_version_id=parent_version_id,
            content=content,
            content_hash=version_hash,
            status="approved",
            created_by_user_id=project.created_by_user_id,
            external_entity_refs=[{"package_id": str(package.id), "proposed_patch_id": str(patch.id)}],
            packaging_metadata={"patch_type": patch.patch_type, "requires_gate_rerun": True},
            evidence_refs=patch.affected_artifact_refs_json,
            context_refs=[{"effective_context_snapshot_id": str(package.effective_context_snapshot_id)}]
            if package.effective_context_snapshot_id
            else [],
        )
        self.session.add(version)
        self.session.flush()
        artifact.current_version_id = version.id
        item = self.session.get(PackagingReviewQueueItem, patch.queue_item_id)
        if item is not None:
            item.status = "GATE_RERUN_REQUIRED"
            item.next_action_code = "RERUN_PACKAGING_GATES"
        patch.status = "APPLIED"
        handoff_ref = f"artifact_version:{version.id}" if patch.patch_type in {"PUBLISH_TIMING_OVERRIDE", "SUBTITLE_HANDOFF"} else None
        run = PackagingPatchApplyRun(
            proposed_patch_id=patch.id,
            package_id=patch.package_id,
            apply_status="APPLIED",
            created_artifact_ref=f"artifact_version:{version.id}",
            created_handoff_override_ref=handoff_ref,
            created_version_hash=version_hash,
            reason_codes_json=["VERSIONED_PATCH_CREATED", "NO_IN_PLACE_PACKAGE_MUTATION"],
        )
        self.session.add(run)
        self.session.flush()
        PackagingGateRerunService(self.session).rerun_for_patch(patch.id)
        return PackagingPatchApplyRunRead.model_validate(run)

    def _blocked_run(self, patch: PackagingProposedPatch, reason_codes: list[str]) -> PackagingPatchApplyRun:
        run = PackagingPatchApplyRun(
            proposed_patch_id=patch.id,
            package_id=patch.package_id,
            apply_status="BLOCKED",
            created_artifact_ref=None,
            created_handoff_override_ref=None,
            created_version_hash=None,
            reason_codes_json=reason_codes,
        )
        self.session.add(run)
        self.session.flush()
        return run


class PackagingGateRerunService:
    def __init__(self, session: Session):
        self.session = session

    def rerun_for_patch(self, patch_id: uuid.UUID) -> PackagingGateRerunRecordRead:
        patch = self.session.get(PackagingProposedPatch, patch_id)
        if patch is None:
            raise NotFoundError(f"packaging proposed patch not found: {patch_id}")
        gate_keys = PATCH_TYPE_RERUN_GATES.get(patch.patch_type, [])
        record = PackagingGateRerunRecord(
            package_id=patch.package_id,
            proposed_patch_id=patch.id,
            gate_keys_json=gate_keys,
            rerun_status="REVIEW_REQUIRED",
            gate_batch_run_id=None,
            reason_codes_json=["PATCH_VERSION_CREATED_RELEVANT_GATE_RERUN_REQUIRED"],
        )
        self.session.add(record)
        self.session.flush()
        return PackagingGateRerunRecordRead.model_validate(record)

    def rerun_for_package(self, package_id: uuid.UUID) -> PackagingGateRerunRecordRead:
        PackagingReviewQueueService(self.session)._require_package(package_id)
        queue = PackagingReviewQueueService(self.session).read(package_id)
        gate_keys = sorted({item.gate_key for item in queue.items if item.status in UNRESOLVED_QUEUE_STATUSES})
        record = PackagingGateRerunRecord(
            package_id=package_id,
            proposed_patch_id=None,
            gate_keys_json=gate_keys,
            rerun_status="REVIEW_REQUIRED" if gate_keys else "PASS",
            gate_batch_run_id=None,
            reason_codes_json=["MANUAL_RERUN_REQUEST_RECORDED"] if gate_keys else ["NO_UNRESOLVED_QUEUE_ITEMS"],
        )
        self.session.add(record)
        self.session.flush()
        return PackagingGateRerunRecordRead.model_validate(record)


def _issue_copy(issue_code: str) -> dict[str, str]:
    return ISSUE_ACTION_COPY.get(
        issue_code,
        {
            "title": "Gate packaging cần review",
            "why": "Gate báo issue cần người vận hành xem trước upload.",
            "fix": "Tạo proposed patch qua route domain phù hợp hoặc request changes.",
            "section": "Packaging Review",
        },
    )


def _target_from_refs(refs: list[dict[str, Any]]) -> tuple[str, str | None]:
    first = refs[0] if refs else {}
    target = first.get("artifact_key") or first.get("artifact_type") or first.get("ref")
    if target:
        return str(target), str(target)
    return "package_artifact", None


def _verdict_label(verdict: str) -> str:
    return {
        "READY_FOR_MANUAL_UPLOAD": "Sẵn sàng tạo task upload thủ công.",
        "REVIEW_REQUIRED": "Còn mục cần review trước upload.",
        "BLOCKED": "Đang bị block trước upload.",
        "WAITING_PROVIDER_CONFIG": "Đang chờ cấu hình provider; không tạo task upload.",
    }.get(verdict, "Chưa rõ trạng thái review.")


def _next_safe_action(verdict: str, unresolved: list[PackagingReviewQueueItem]) -> str:
    if verdict == "READY_FOR_MANUAL_UPLOAD":
        return "Có thể tạo task upload thủ công; VCOS vẫn không upload/publish."
    if verdict == "WAITING_PROVIDER_CONFIG":
        return "Kiểm tra provider/cost boundary; không chạy provider từ dashboard."
    if unresolved:
        needs_patch = sum(1 for item in unresolved if item.next_action_code == "NEEDS_PROPOSED_PATCH")
        if needs_patch:
            return "Tạo hoặc chờ proposed patch cho các gate đang fail."
        return "Duyệt, reject hoặc request changes trên proposed patch."
    return "Review gate kỹ thuật trước khi upload."


def _artifact_type_for_patch(patch_type: str) -> str:
    return {
        "HOOK_SPEC": "packaging_hook_patch",
        "METADATA": "packaging_metadata_patch",
        "THUMBNAIL_BRIEF": "packaging_thumbnail_patch",
        "SUBTITLE_HANDOFF": "packaging_subtitle_handoff_patch",
        "PUBLISH_TIMING_OVERRIDE": "manual_publish_timing_override",
        "DISCLOSURE_COPY": "packaging_disclosure_copy_patch",
        "UPLOAD_COPY": "packaging_upload_copy_patch",
    }.get(patch_type, "packaging_patch")
