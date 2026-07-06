from __future__ import annotations

from dataclasses import dataclass
import re
import uuid
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.contracts.r3d9 import (
    PackagingApprovedPatchApplyAndRecheckResultRead,
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
    EffectiveChannelRuntimeContextSnapshot,
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

PACKAGE_APPLY_RECHECK_STATUSES = {
    "APPLIED_AND_RECHECKED",
    "BLOCKED_WAITING_HUMAN_APPROVAL",
    "BLOCKED_PENDING_HUMAN_DECISIONS",
    "APPLY_FAILED",
    "NOOP_ALREADY_APPLIED",
}

ISSUE_ACTION_COPY: dict[str, dict[str, str]] = {
    "HOOK_PROMISE_MISSING": {
        "title": "Hook thiếu promise rõ ràng",
        "why": "Người xem chưa biết video hứa trả lời điều gì.",
        "fix": "Duyệt patch bổ sung promise và payoff location cho hook.",
        "section": "Hook Review",
    },
    "HOOK_VISUAL_MISSING": {
        "title": "Hook thiếu visual 3 giây đầu",
        "why": "Operator chưa có ý tưởng visual mở đầu khớp với script hook.",
        "fix": "Duyệt patch visual hook không chạy render/provider.",
        "section": "Hook Review",
    },
    "SCRIPT_FORBIDDEN_STYLE_USED": {
        "title": "Script dùng style bị cấm",
        "why": "Narration script chứa wording/style nằm trong frozen channel/runtime contract.",
        "fix": "Duyệt patch rewrite đúng câu vi phạm, giữ topic/claim/audience/evidence.",
        "section": "Script Review",
    },
    "TITLE_MISSING": {
        "title": "Thiếu title upload",
        "why": "Package chưa có title paste-ready cho YouTube.",
        "fix": "Duyệt patch metadata có 3 title candidates và title khuyến nghị.",
        "section": "Upload Copy",
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
    "DESCRIPTION_MISSING": {
        "title": "Thiếu description upload",
        "why": "Package chưa có description ngắn gọn để paste sang YouTube.",
        "fix": "Duyệt patch description không thêm CTA/resource giả.",
        "section": "Upload Copy",
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
    "VISUAL_HOOK": ["VisualHookRelevanceGate"],
    "SCRIPT_STYLE_PATCH": ["script_style_compliance_gate"],
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


@dataclass(frozen=True)
class PackagingPatchReviewState:
    item: PackagingReviewQueueItem
    patch: PackagingProposedPatch
    latest_decision: str | None
    applied_run: PackagingPatchApplyRun | None


@dataclass(frozen=True)
class PackagingPatchInventory:
    states: list[PackagingPatchReviewState]
    approved_count: int
    ready_for_review_count: int
    rejected_count: int
    request_changes_count: int
    applied_count: int


class PackagingPatchRouter:
    ROUTES: dict[str, PackagingPatchRoute] = {
        "SCRIPT_FORBIDDEN_STYLE_USED": PackagingPatchRoute("DETERMINISTIC_SERVICE", "SCRIPT_STYLE_PATCH", "ScriptRewriteAgent", "script_rewrite_agent.patch_proposal", True),
        "HOOK_PROMISE_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "HOOK_SPEC", "ScriptRewriteAgent", "script_rewrite_agent.patch_proposal", True),
        "HOOK_VISUAL_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "VISUAL_HOOK", "VisualPlanningAgent", "visual_planning_agent.patch_proposal", True),
        "TITLE_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "METADATA", "PublishingMetadataAgent", "publishing_metadata_agent.patch_proposal", True),
        "TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM": PackagingPatchRoute("EXISTING_AGENT", "METADATA", "PublishingMetadataAgent", "publishing_metadata_agent.patch_proposal"),
        "DESCRIPTION_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "METADATA", "PublishingMetadataAgent", "publishing_metadata_agent.patch_proposal", True),
        "THUMBNAIL_BRIEF_MISSING": PackagingPatchRoute("DETERMINISTIC_SERVICE", "THUMBNAIL_BRIEF", "ThumbnailBriefAgent", "thumbnail_brief_agent.patch_proposal", True),
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


def _patch_inventory_for_package(session: Session, package_id: uuid.UUID) -> PackagingPatchInventory:
    items = session.scalars(
        select(PackagingReviewQueueItem)
        .where(
            PackagingReviewQueueItem.package_id == package_id,
            PackagingReviewQueueItem.status.in_(UNRESOLVED_QUEUE_STATUSES),
        )
        .order_by(PackagingReviewQueueItem.created_at.asc(), PackagingReviewQueueItem.id.asc())
    ).all()
    patches: list[PackagingProposedPatch] = []
    states_by_patch_id: dict[uuid.UUID, PackagingReviewQueueItem] = {}
    for item in items:
        patch = _latest_patch_for_item(session, item.id)
        if patch is None:
            continue
        patches.append(patch)
        states_by_patch_id[patch.id] = item

    latest_decisions = _latest_decisions_for_patches(session, [patch.id for patch in patches])
    applied_runs = _latest_applied_runs_for_patches(session, [patch.id for patch in patches])
    states = [
        PackagingPatchReviewState(
            item=states_by_patch_id[patch.id],
            patch=patch,
            latest_decision=latest_decisions.get(patch.id),
            applied_run=applied_runs.get(patch.id),
        )
        for patch in patches
    ]
    return PackagingPatchInventory(
        states=states,
        approved_count=sum(1 for state in states if _state_is_human_approved(state)),
        ready_for_review_count=sum(1 for state in states if state.patch.status == "READY_FOR_REVIEW"),
        rejected_count=sum(1 for state in states if _state_is_rejected(state)),
        request_changes_count=sum(1 for state in states if _state_is_request_changes(state)),
        applied_count=sum(1 for state in states if _state_is_applied(state)),
    )


def _latest_patch_for_item(session: Session, queue_item_id: uuid.UUID) -> PackagingProposedPatch | None:
    return session.scalars(
        select(PackagingProposedPatch)
        .where(PackagingProposedPatch.queue_item_id == queue_item_id)
        .order_by(desc(PackagingProposedPatch.created_at), desc(PackagingProposedPatch.id))
        .limit(1)
    ).one_or_none()


def _latest_decisions_for_patches(session: Session, patch_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not patch_ids:
        return {}
    decisions: dict[uuid.UUID, str] = {}
    for decision in session.scalars(
        select(PackagingPatchApprovalDecision)
        .where(PackagingPatchApprovalDecision.proposed_patch_id.in_(patch_ids))
        .order_by(desc(PackagingPatchApprovalDecision.created_at), desc(PackagingPatchApprovalDecision.id))
    ).all():
        decisions.setdefault(decision.proposed_patch_id, decision.decision)
    return decisions


def _latest_applied_runs_for_patches(session: Session, patch_ids: list[uuid.UUID]) -> dict[uuid.UUID, PackagingPatchApplyRun]:
    if not patch_ids:
        return {}
    runs: dict[uuid.UUID, PackagingPatchApplyRun] = {}
    for run in session.scalars(
        select(PackagingPatchApplyRun)
        .where(
            PackagingPatchApplyRun.proposed_patch_id.in_(patch_ids),
            PackagingPatchApplyRun.apply_status == "APPLIED",
        )
        .order_by(desc(PackagingPatchApplyRun.created_at), desc(PackagingPatchApplyRun.id))
    ).all():
        runs.setdefault(run.proposed_patch_id, run)
    return runs


def _state_is_human_approved(state: PackagingPatchReviewState) -> bool:
    return state.patch.status == "APPROVED" and state.latest_decision == "APPROVE"


def _state_is_rejected(state: PackagingPatchReviewState) -> bool:
    return state.patch.status == "REJECTED" or state.latest_decision == "REJECT"


def _state_is_request_changes(state: PackagingPatchReviewState) -> bool:
    return state.patch.status == "REQUEST_CHANGES" or state.latest_decision == "REQUEST_CHANGES" or state.item.status == "NEEDS_CHANGES"


def _state_is_applied(state: PackagingPatchReviewState) -> bool:
    return state.patch.status == "APPLIED" or state.applied_run is not None


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
        inventory = _patch_inventory_for_package(self.session, package.id)
        apply_state = _apply_approved_changes_state(inventory, unresolved)
        return PackagingReviewQueueRead(
            package_id=package.id,
            review_verdict=verdict,
            plain_language_status=_verdict_label(verdict),
            must_fix_count=sum(1 for item in unresolved if item.severity in {"BLOCK", "REVIEW_REQUIRED"}),
            next_safe_action=_next_safe_action(verdict, unresolved),
            upload_task_creation_allowed=upload_allowed,
            approved_patch_count=inventory.approved_count,
            ready_for_review_patch_count=inventory.ready_for_review_count,
            rejected_patch_count=inventory.rejected_count,
            request_changes_patch_count=inventory.request_changes_count,
            applied_patch_count=inventory.applied_count,
            can_apply_approved_changes=apply_state["can_apply"],
            apply_approved_changes_label=apply_state["label"],
            apply_approved_changes_disabled_reason=apply_state["disabled_reason"],
            last_apply_recheck_result=_last_apply_recheck_result(self.session, package.id),
            items=[self._read_item(item) for item in items],
            technical_appendix={
                "source": "PackagingReviewQueueService",
                "packaging_gate_overall_status": gate_status,
                "unresolved_item_count": len(unresolved),
                "approved_patch_count": inventory.approved_count,
                "ready_for_review_patch_count": inventory.ready_for_review_count,
                "rejected_patch_count": inventory.rejected_count,
                "request_changes_patch_count": inventory.request_changes_count,
                "applied_patch_count": inventory.applied_count,
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
            item.next_action_code = "ROUTE_NOT_AVAILABLE"
            return None
        if route.deterministic:
            return self._create_deterministic_patch(item, route)
        if not self._llm_patch_proposal_enabled():
            item.next_action_code = "LLM_PROPOSAL_DISABLED"
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
        if route.patch_type == "SCRIPT_STYLE_PATCH":
            return self._script_style_patch_payload(item, route, package)
        if route.patch_type == "HOOK_SPEC":
            return self._hook_spec_patch_payload(item, route, package)
        if route.patch_type == "VISUAL_HOOK":
            return self._visual_hook_patch_payload(item, route, package)
        if route.patch_type == "METADATA" and item.issue_code == "TITLE_MISSING":
            return self._title_patch_payload(item, route, package)
        if route.patch_type == "METADATA" and item.issue_code == "DESCRIPTION_MISSING":
            return self._description_patch_payload(item, route, package)
        if route.patch_type == "THUMBNAIL_BRIEF":
            return self._thumbnail_brief_patch_payload(item, route, package)
        return {
            "proposed_patch_json": {"operation": "route_to_existing_agent", "route_key": route.route_key, "reason_code": item.issue_code},
            "after_preview_json": {},
            "affected_artifact_refs_json": [{"artifact_key": item.target_artifact_ref or item.target_artifact_type}],
            "risk_level": "MEDIUM",
        }

    def _script_style_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        artifacts = package.artifacts or {}
        script = _dict(artifacts.get("narration_script"))
        effective_context = self._effective_context(package)
        forbidden_terms = _strings(_dict(effective_context.brand_voice_persona_context_json if effective_context else {}).get("forbidden_style"))
        sentence_patches: list[dict[str, str | None]] = []
        for sentence in _list(script.get("sentences")):
            if not isinstance(sentence, dict):
                continue
            before = str(sentence.get("text") or "")
            after = _remove_forbidden_terms(before, forbidden_terms)
            if after != before:
                sentence_patches.append(
                    {
                        "sentence_id": str(sentence.get("sentence_id") or len(sentence_patches) + 1),
                        "before_text": before,
                        "after_text": after,
                    }
                )
        proposed = {
            "operation": "rewrite_script_style_only",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "narration_script",
            "forbidden_style_terms": forbidden_terms,
            "sentence_patches": sentence_patches,
            "preserve": ["topic", "claim", "duration_target", "audience", "evidence_refs", "sentence_order"],
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {
                "before_after_preview": sentence_patches,
                "style_contract_note": "Chỉ xoá/rewrite wording vi phạm trong narration_script; không sửa frozen contract.",
            },
            "affected_artifact_refs_json": [{"artifact_key": "narration_script"}],
            "risk_level": "MEDIUM",
        }

    def _hook_spec_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        artifacts = package.artifacts or {}
        hook = _existing_hook_payload(artifacts)
        topic = _topic_from_package(self.session, package)
        first_script = _first_sentence_text(artifacts) or _clean_text(hook.get("first_3_seconds_script")) or _shorten(topic, 96)
        payoff_location = _payoff_location(artifacts)
        promise = _safe_promise(topic)
        visual = _clean_text(hook.get("first_3_seconds_visual")) or _first_visual_scene(artifacts)
        proposed_hook = {
            "promise_made": promise,
            "payoff_location": payoff_location,
            "first_3_seconds_script": first_script,
            "first_3_seconds_visual": visual,
            "no_overpromise_note": "Promise dùng framing thận trọng từ topic/script hiện có; không thêm claim mới.",
        }
        proposed = {
            "operation": "create_hook_spec_patch",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "hook_spec",
            "hook_spec_patch": proposed_hook,
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {"hook_spec": {**hook, **proposed_hook}},
            "affected_artifact_refs_json": [{"artifact_key": "hook_spec"}, {"artifact_key": "narration_script"}],
            "risk_level": "LOW",
        }

    def _visual_hook_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        artifacts = package.artifacts or {}
        effective_context = self._effective_context(package)
        hook = _existing_hook_payload(artifacts)
        first_script = _clean_text(hook.get("first_3_seconds_script")) or _first_sentence_text(artifacts)
        topic = _topic_from_package(self.session, package)
        character_policy = (
            effective_context.character_policy_mode
            or _dict(effective_context.character_identity_context_json).get("character_policy_mode")
            if effective_context
            else None
        )
        visual = _visual_hook_idea(first_script=first_script, topic=topic, character_policy=character_policy)
        proposed = {
            "operation": "create_visual_hook_patch",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "hook_spec",
            "first_3_seconds_visual": visual,
            "alignment_note": "Visual hook bám first_3_seconds_script/promise hiện có.",
            "character_policy_note": _character_policy_note(character_policy),
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {"hook_spec": {**hook, "first_3_seconds_visual": visual}},
            "affected_artifact_refs_json": [{"artifact_key": "hook_spec"}, {"artifact_key": "visual_plan"}],
            "risk_level": "LOW",
        }

    def _title_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        topic = _topic_from_package(self.session, package)
        candidates = _title_candidates(topic)
        recommended = candidates[0]
        proposed = {
            "operation": "create_metadata_title_patch",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "metadata_package",
            "title_candidates": candidates,
            "recommended_title": recommended,
            "promise_payoff_alignment_note": "Title giữ đúng topic/script hiện có và không hứa asset, demo, kết quả bảo đảm.",
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {"metadata_package": {"title": recommended, "title_candidates": candidates}},
            "affected_artifact_refs_json": [{"artifact_key": "metadata_package"}, {"artifact_key": "upload_card_copy"}],
            "risk_level": "LOW",
        }

    def _description_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        artifacts = package.artifacts or {}
        topic = _topic_from_package(self.session, package)
        summary = _script_summary(artifacts, topic)
        description = (
            f"{summary}\n\n"
            "Human review note: verify claims, assets, subtitles, and thumbnail before manual upload. "
            "VCOS does not upload, publish, schedule, or generate media from this patch."
        )
        proposed = {
            "operation": "create_upload_description_patch",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "metadata_package",
            "summary": summary,
            "description": description,
            "unsupported_cta_guard": "Không thêm fake resource, demo, checklist, limited-time CTA, hoặc asset chưa có manifest.",
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {"metadata_package": {"description": description}, "upload_card_copy": {"description": description}},
            "affected_artifact_refs_json": [{"artifact_key": "metadata_package"}, {"artifact_key": "upload_card_copy"}],
            "risk_level": "LOW",
        }

    def _thumbnail_brief_patch_payload(
        self,
        item: PackagingReviewQueueItem,
        route: PackagingPatchRoute,
        package: FirstScriptedVideoPackage,
    ) -> dict[str, Any]:
        topic = _topic_from_package(self.session, package)
        hook = _existing_hook_payload(package.artifacts or {})
        effective_context = self._effective_context(package)
        character_policy = (
            effective_context.character_policy_mode
            or _dict(effective_context.character_identity_context_json).get("character_policy_mode")
            if effective_context
            else None
        )
        overlay = _thumbnail_overlay(topic)
        brief = {
            "concept": f"Concrete operator workflow around: {_shorten(topic, 72)}",
            "text_overlay": overlay,
            "main_subject": "Workflow board, automation trigger, and time-saved marker; no stock face.",
            "composition": "Large subject left, short overlay right, high contrast, plenty of negative space.",
            "mobile_readability_notes": "Overlay is 2-4 words, high contrast, readable at small size.",
            "truthfulness_note": "Thumbnail repeats only topic/hook framing already present; no new proof/result claim.",
            "character_policy_note": _character_policy_note(character_policy),
            "aligned_hook": _clean_text(hook.get("promise_made") or hook.get("first_3_seconds_script")),
            "rendered": False,
        }
        proposed = {
            "operation": "create_thumbnail_brief_patch",
            "reason_code": item.issue_code,
            "route_key": route.route_key,
            "routed_agent_key": route.routed_agent_key,
            "target_artifact_key": "thumbnail_brief",
            "thumbnail_brief": brief,
            "requires_human_approval": True,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        return {
            "proposed_patch_json": proposed,
            "after_preview_json": {"thumbnail_brief": brief},
            "affected_artifact_refs_json": [{"artifact_key": "thumbnail_brief"}],
            "risk_level": "LOW",
        }

    def _effective_context(self, package: FirstScriptedVideoPackage) -> EffectiveChannelRuntimeContextSnapshot | None:
        if not package.effective_context_snapshot_id:
            return None
        return self.session.get(EffectiveChannelRuntimeContextSnapshot, package.effective_context_snapshot_id)

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
            patch.status = "REQUEST_CHANGES"
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

    def apply(self, patch_id: uuid.UUID, *, rerun_gates: bool = True) -> PackagingPatchApplyRunRead:
        patch = self.session.get(PackagingProposedPatch, patch_id)
        if patch is None:
            raise NotFoundError(f"packaging proposed patch not found: {patch_id}")
        existing_run = self._existing_applied_run(patch.id)
        if existing_run is not None:
            patch.status = "APPLIED"
            item = self.session.get(PackagingReviewQueueItem, patch.queue_item_id)
            if item is not None and item.status != "CLOSED":
                item.status = "GATE_RERUN_REQUIRED"
                item.next_action_code = "RERUN_PACKAGING_GATES"
            self.session.flush()
            return _apply_run_read(existing_run, apply_status="ALREADY_APPLIED")
        if patch.status == "APPLIED":
            return PackagingPatchApplyRunRead.model_validate(self._already_applied_run(patch, ["PATCH_STATUS_ALREADY_APPLIED"]))
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
        if rerun_gates:
            PackagingGateRerunService(self.session).rerun_for_patch(patch.id)
        return PackagingPatchApplyRunRead.model_validate(run)

    def _existing_applied_run(self, patch_id: uuid.UUID) -> PackagingPatchApplyRun | None:
        return self.session.scalars(
            select(PackagingPatchApplyRun)
            .where(
                PackagingPatchApplyRun.proposed_patch_id == patch_id,
                PackagingPatchApplyRun.apply_status == "APPLIED",
            )
            .order_by(desc(PackagingPatchApplyRun.created_at), desc(PackagingPatchApplyRun.id))
            .limit(1)
        ).one_or_none()

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

    def _already_applied_run(self, patch: PackagingProposedPatch, reason_codes: list[str]) -> PackagingPatchApplyRun:
        run = PackagingPatchApplyRun(
            proposed_patch_id=patch.id,
            package_id=patch.package_id,
            apply_status="ALREADY_APPLIED",
            created_artifact_ref=None,
            created_handoff_override_ref=None,
            created_version_hash=None,
            reason_codes_json=reason_codes,
        )
        self.session.add(run)
        self.session.flush()
        return run


class PackagingApprovedPatchApplyAndRecheckService:
    def __init__(self, session: Session):
        self.session = session

    def apply_and_recheck(self, package_id: uuid.UUID) -> PackagingApprovedPatchApplyAndRecheckResultRead:
        package = PackagingReviewQueueService(self.session)._require_package(package_id)
        queue_before = PackagingReviewQueueService(self.session).read(package.id)
        inventory = _patch_inventory_for_package(self.session, package.id)
        skipped_patch_ids = [state.patch.id for state in inventory.states if not _state_is_human_approved(state)]

        if inventory.approved_count == 0:
            status = (
                "NOOP_ALREADY_APPLIED"
                if inventory.applied_count > 0 and inventory.ready_for_review_count == 0
                else "BLOCKED_WAITING_HUMAN_APPROVAL"
            )
            return self._result(
                status=status,
                package_id=package.id,
                applied_patch_ids=[],
                skipped_patch_ids=skipped_patch_ids,
                gate_rerun_record_ids=[],
                queue=queue_before,
            )
        if inventory.ready_for_review_count > 0:
            return self._result(
                status="BLOCKED_PENDING_HUMAN_DECISIONS",
                package_id=package.id,
                applied_patch_ids=[],
                skipped_patch_ids=skipped_patch_ids,
                gate_rerun_record_ids=[],
                queue=queue_before,
            )

        approved_states = [state for state in inventory.states if _state_is_human_approved(state)]
        validation_errors = self._validate_approved_patch_eligibility(approved_states)
        if validation_errors:
            return self._result(
                status="APPLY_FAILED",
                package_id=package.id,
                applied_patch_ids=[],
                skipped_patch_ids=[state.patch.id for state in inventory.states],
                gate_rerun_record_ids=[],
                queue=queue_before,
                extra_proof={"apply_validation_errors": validation_errors},
            )

        applied_patch_ids: list[uuid.UUID] = []
        already_applied_patch_ids: list[uuid.UUID] = []
        failed_patch_ids: list[uuid.UUID] = []
        for state in approved_states:
            run = PackagingPatchApplyService(self.session).apply(state.patch.id, rerun_gates=False)
            if run.apply_status == "APPLIED":
                applied_patch_ids.append(state.patch.id)
            elif run.apply_status == "ALREADY_APPLIED":
                already_applied_patch_ids.append(state.patch.id)
            else:
                failed_patch_ids.append(state.patch.id)

        if failed_patch_ids:
            return self._result(
                status="APPLY_FAILED",
                package_id=package.id,
                applied_patch_ids=applied_patch_ids,
                skipped_patch_ids=skipped_patch_ids + already_applied_patch_ids + failed_patch_ids,
                gate_rerun_record_ids=[],
                queue=queue_before,
                extra_proof={"failed_patch_ids": [str(patch_id) for patch_id in failed_patch_ids]},
            )
        if not applied_patch_ids:
            return self._result(
                status="NOOP_ALREADY_APPLIED",
                package_id=package.id,
                applied_patch_ids=[],
                skipped_patch_ids=skipped_patch_ids + already_applied_patch_ids,
                gate_rerun_record_ids=[],
                queue=queue_before,
            )

        gate_rerun_record_ids: list[uuid.UUID] = []
        for patch_id in applied_patch_ids:
            rerun = PackagingGateRerunService(self.session).rerun_for_patch(patch_id)
            gate_rerun_record_ids.append(rerun.id)
        queue_after = PackagingReviewQueueService(self.session).build_from_gates(package.id)
        return self._result(
            status="APPLIED_AND_RECHECKED",
            package_id=package.id,
            applied_patch_ids=applied_patch_ids,
            skipped_patch_ids=skipped_patch_ids + already_applied_patch_ids,
            gate_rerun_record_ids=gate_rerun_record_ids,
            queue=queue_after,
        )

    def _validate_approved_patch_eligibility(self, approved_states: list[PackagingPatchReviewState]) -> list[str]:
        reason_codes: list[str] = []
        for state in approved_states:
            if state.patch.status != "APPROVED" or state.latest_decision != "APPROVE":
                reason_codes.append(f"{state.patch.id}:PATCH_NOT_HUMAN_APPROVED")
            if not state.patch.requires_human_approval:
                reason_codes.append(f"{state.patch.id}:PATCH_APPROVAL_BOUNDARY_INVALID")
            package = self.session.get(FirstScriptedVideoPackage, state.patch.package_id)
            if package is None or package.video_project_id is None:
                reason_codes.append(f"{state.patch.id}:PACKAGE_OR_PROJECT_MISSING")
                continue
            if self.session.get(VideoProject, package.video_project_id) is None:
                reason_codes.append(f"{state.patch.id}:PROJECT_MISSING")
        return reason_codes

    def _result(
        self,
        *,
        status: str,
        package_id: uuid.UUID,
        applied_patch_ids: list[uuid.UUID],
        skipped_patch_ids: list[uuid.UUID],
        gate_rerun_record_ids: list[uuid.UUID],
        queue: PackagingReviewQueueRead,
        extra_proof: dict[str, Any] | None = None,
    ) -> PackagingApprovedPatchApplyAndRecheckResultRead:
        if status not in PACKAGE_APPLY_RECHECK_STATUSES:
            raise ValidationFailureError(f"unknown apply/recheck status: {status}")
        package = PackagingReviewQueueService(self.session)._require_package(package_id)
        return PackagingApprovedPatchApplyAndRecheckResultRead(
            status=status,
            package_id=package.id,
            applied_patch_ids=applied_patch_ids,
            skipped_patch_ids=_dedupe_uuid(skipped_patch_ids),
            gate_rerun_record_ids=_dedupe_uuid(gate_rerun_record_ids),
            package_status=package.package_status,
            final_package_status=package.package_status,
            review_verdict=queue.review_verdict,
            must_fix_count=queue.must_fix_count,
            upload_task_creation_allowed=queue.upload_task_creation_allowed,
            remaining_blockers=_remaining_blockers(queue),
            next_safe_action=queue.next_safe_action,
            no_provider_media_upload_execution=True,
            no_execution_proof=_no_execution_proof(status=status, extra=extra_proof),
        )


class PackagingGateRerunService:
    def __init__(self, session: Session):
        self.session = session

    def rerun_for_patch(self, patch_id: uuid.UUID) -> PackagingGateRerunRecordRead:
        patch = self.session.get(PackagingProposedPatch, patch_id)
        if patch is None:
            raise NotFoundError(f"packaging proposed patch not found: {patch_id}")
        existing = self.session.scalars(
            select(PackagingGateRerunRecord)
            .where(PackagingGateRerunRecord.proposed_patch_id == patch.id)
            .order_by(desc(PackagingGateRerunRecord.created_at), desc(PackagingGateRerunRecord.id))
            .limit(1)
        ).one_or_none()
        if existing is not None:
            return PackagingGateRerunRecordRead.model_validate(existing)
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
        needs_patch = sum(
            1
            for item in unresolved
            if item.next_action_code in {"NEEDS_PROPOSED_PATCH", "ROUTE_NOT_AVAILABLE", "LLM_PROPOSAL_DISABLED"}
        )
        if needs_patch:
            return "Tạo hoặc chờ proposed patch cho các gate đang fail."
        return "Duyệt, reject hoặc request changes trên proposed patch."
    return "Review gate kỹ thuật trước khi upload."


def _apply_approved_changes_state(
    inventory: PackagingPatchInventory,
    unresolved: list[PackagingReviewQueueItem],
) -> dict[str, str | bool | None]:
    if not unresolved:
        return {
            "can_apply": False,
            "label": "Không có thay đổi cần apply",
            "disabled_reason": "Không có thay đổi cần apply",
        }
    if inventory.approved_count == 0:
        return {
            "can_apply": False,
            "label": "Chưa có patch được duyệt",
            "disabled_reason": "Chưa có patch được duyệt",
        }
    if inventory.ready_for_review_count > 0:
        return {
            "can_apply": False,
            "label": "Còn patch chưa quyết định",
            "disabled_reason": "Còn patch chưa quyết định",
        }
    return {
        "can_apply": True,
        "label": "Apply approved changes & recheck package",
        "disabled_reason": None,
    }


def _last_apply_recheck_result(session: Session, package_id: uuid.UUID) -> dict[str, Any] | None:
    latest_apply = session.scalars(
        select(PackagingPatchApplyRun)
        .where(PackagingPatchApplyRun.package_id == package_id)
        .order_by(desc(PackagingPatchApplyRun.created_at), desc(PackagingPatchApplyRun.id))
        .limit(1)
    ).one_or_none()
    latest_rerun = session.scalars(
        select(PackagingGateRerunRecord)
        .where(PackagingGateRerunRecord.package_id == package_id)
        .order_by(desc(PackagingGateRerunRecord.created_at), desc(PackagingGateRerunRecord.id))
        .limit(1)
    ).one_or_none()
    if latest_apply is None and latest_rerun is None:
        return None
    return {
        "latest_apply_run_id": str(latest_apply.id) if latest_apply else None,
        "latest_apply_status": latest_apply.apply_status if latest_apply else None,
        "latest_gate_rerun_record_id": str(latest_rerun.id) if latest_rerun else None,
        "latest_gate_rerun_status": latest_rerun.rerun_status if latest_rerun else None,
        "no_provider_media_upload_execution": True,
    }


def _apply_run_read(run: PackagingPatchApplyRun, *, apply_status: str | None = None) -> PackagingPatchApplyRunRead:
    return PackagingPatchApplyRunRead(
        id=run.id,
        proposed_patch_id=run.proposed_patch_id,
        package_id=run.package_id,
        apply_status=apply_status or run.apply_status,
        created_artifact_ref=run.created_artifact_ref,
        created_handoff_override_ref=run.created_handoff_override_ref,
        created_version_hash=run.created_version_hash,
        reason_codes_json=run.reason_codes_json,
        created_at=run.created_at,
    )


def _remaining_blockers(queue: PackagingReviewQueueRead) -> list[dict[str, Any]]:
    return [
        {
            "queue_item_id": str(item.id),
            "gate_key": item.gate_key,
            "issue_code": item.issue_code,
            "severity": item.severity,
            "status": item.status,
            "next_action_code": item.next_action_code,
        }
        for item in queue.items
        if item.status in UNRESOLVED_QUEUE_STATUSES
    ]


def _no_execution_proof(*, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    proof: dict[str, Any] = {
        "status": status,
        "no_provider_media_upload_execution": True,
        "no_upload_task_created": True,
        "no_youtube_upload_publish_reupload": True,
        "no_real_video_or_media_generation": True,
        "no_provider_render_job": True,
        "no_paid_provider_execution": True,
        "providers_not_called": ["ElevenLabs", "Luma", "Creatomate", "Pexels", "Google Drive upload", "YouTube"],
        "does_not_mutate": [
            "Channel Contract",
            "ChannelProfileVersion",
            "EffectiveChannelRuntimeContextSnapshot",
        ],
        "learning_auto_promotion": False,
        "prompt_self_mutation": False,
    }
    if extra:
        proof.update(extra)
    return proof


def _dedupe_uuid(values: list[uuid.UUID]) -> list[uuid.UUID]:
    seen: set[uuid.UUID] = set()
    result: list[uuid.UUID] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _artifact_type_for_patch(patch_type: str) -> str:
    return {
        "HOOK_SPEC": "packaging_hook_patch",
        "VISUAL_HOOK": "packaging_visual_hook_patch",
        "SCRIPT_STYLE_PATCH": "packaging_script_style_patch",
        "METADATA": "packaging_metadata_patch",
        "THUMBNAIL_BRIEF": "packaging_thumbnail_patch",
        "SUBTITLE_HANDOFF": "packaging_subtitle_handoff_patch",
        "PUBLISH_TIMING_OVERRIDE": "manual_publish_timing_override",
        "DISCLOSURE_COPY": "packaging_disclosure_copy_patch",
        "UPLOAD_COPY": "packaging_upload_copy_patch",
    }.get(patch_type, "packaging_patch")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _strings(value: Any) -> list[str]:
    return [str(item).strip() for item in _list(value) if str(item).strip()]


def _clean_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _shorten(value: Any, limit: int) -> str:
    text = _clean_text(value) or "Package topic cần human review"
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,:;") + "..."


def _remove_forbidden_terms(text: str, forbidden_terms: list[str]) -> str:
    rewritten = text
    for term in sorted(forbidden_terms, key=len, reverse=True):
        if not term:
            continue
        rewritten = re.sub(re.escape(term), _neutral_style_replacement(term), rewritten, flags=re.IGNORECASE)
    rewritten = re.sub(r"\s+", " ", rewritten).strip()
    return rewritten or text


def _neutral_style_replacement(term: str) -> str:
    normalized = term.lower().strip()
    if normalized == "hype":
        return "overstatement"
    if "hype" in normalized:
        return "measured framing"
    if "fear" in normalized:
        return "calm framing"
    if "urgency" in normalized:
        return "specific timing"
    return "neutral wording"


def _existing_hook_payload(artifacts: dict[str, Any]) -> dict[str, Any]:
    script = _dict(artifacts.get("narration_script"))
    outline = _dict(artifacts.get("script_outline"))
    return {
        **_dict(script.get("hook_spec")),
        **_dict(outline.get("hook_spec")),
        **_dict(artifacts.get("hook_spec")),
    }


def _first_sentence_text(artifacts: dict[str, Any]) -> str | None:
    script = _dict(artifacts.get("narration_script"))
    for sentence in _list(script.get("sentences")):
        if isinstance(sentence, dict):
            text = _clean_text(sentence.get("text"))
            if text:
                return text
    return _clean_text(script.get("text") or script.get("script"))


def _first_visual_scene(artifacts: dict[str, Any]) -> str | None:
    visual = _dict(artifacts.get("visual_plan"))
    for scene in _list(visual.get("scenes")):
        if isinstance(scene, dict):
            text = _clean_text(scene.get("description") or scene.get("visual") or scene.get("kind"))
            if text:
                return text
    return None


def _topic_from_package(session: Session, package: FirstScriptedVideoPackage) -> str:
    project = session.get(VideoProject, package.video_project_id) if package.video_project_id else None
    artifacts = package.artifacts or {}
    outline = _dict(artifacts.get("script_outline"))
    metadata = _dict(artifacts.get("metadata_package"))
    topic = (
        getattr(project, "title", None)
        or metadata.get("title")
        or outline.get("title")
        or outline.get("topic")
        or _dict(artifacts.get("admission_decision")).get("topic")
        or _first_sentence_text(artifacts)
    )
    return _shorten(topic, 120)


def _payoff_location(artifacts: dict[str, Any]) -> str:
    script = _dict(artifacts.get("narration_script"))
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    if len(sentences) >= 2:
        return str(sentences[min(1, len(sentences) - 1)].get("sentence_id") or "S2")
    if sentences:
        return str(sentences[0].get("sentence_id") or "S1")
    return "SCRIPT_REVIEW_REQUIRED"


def _safe_promise(topic: str) -> str:
    return f"Explain {_shorten(topic, 90)} with evidence-aware steps and human-reviewable limits."


def _visual_hook_idea(*, first_script: str | None, topic: str, character_policy: Any) -> str:
    base = _shorten(first_script or topic, 96)
    character_note = "without stock faces or recurring characters" if str(character_policy or "").upper() != "REQUIRED_CHARACTER" else "using only the frozen character branch if approved"
    return f"First 3-5 seconds: fast operator-dashboard/workflow close-up for '{base}', {character_note}; no media/provider render."


def _character_policy_note(character_policy: Any) -> str:
    if str(character_policy or "").upper() == "REQUIRED_CHARACTER":
        return "Use only the frozen character branch if the human approves this brief."
    return "No stock face, host, or recurring character is introduced."


def _title_candidates(topic: str) -> list[str]:
    base = _shorten(topic, 82).rstrip(".")
    candidates = [
        base,
        f"Practical Workflow: {base}" if len(base) <= 58 else f"Practical Workflow: {_shorten(base, 58)}",
        f"What Operators Should Check: {_shorten(base, 56)}",
    ]
    deduped: list[str] = []
    for candidate in candidates:
        clean = _clean_text(candidate)
        if clean and clean not in deduped:
            deduped.append(clean)
    while len(deduped) < 3:
        deduped.append(f"{base} - review candidate {len(deduped) + 1}")
    return deduped[:3]


def _script_summary(artifacts: dict[str, Any], topic: str) -> str:
    sentences: list[str] = []
    script = _dict(artifacts.get("narration_script"))
    for sentence in _list(script.get("sentences")):
        if isinstance(sentence, dict):
            text = _clean_text(sentence.get("text"))
            if text:
                sentences.append(text)
        if len(sentences) >= 2:
            break
    if sentences:
        return _shorten(" ".join(sentences), 240)
    return f"This video walks through {_shorten(topic, 120)} for a human-reviewed manual publish workflow."


def _thumbnail_overlay(topic: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", topic)
    if "20" in words or "hours" in [word.lower() for word in words]:
        return "20 Hours Back?"
    if len(words) >= 2:
        return " ".join(words[:3]).title()
    return "Workflow Check"
