from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.m7 import (
    ActualDisclosureConfirmationContract,
    ActualPublishMetadataContract,
    ManualPublishConfirmationCreate,
    PlannedPublishMetadataContract,
    PlatformPublishInstructionContract,
    PublishChecklistContract,
    PublishChecklistItem,
    PublishHandoffCreate,
    PublishMetadataDiffContract,
)
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.db.models import (
    AccessibilityQCReport,
    AssetManifestSnapshot,
    ManualPublishConfirmation,
    MediaQCReport,
    ProductionArtifactRun,
    PublishHandoffPackage,
    RenderPackageSnapshot,
    RenderSpecSnapshot,
    SourceManifestSnapshot,
    UploadedVideo,
    UploadedVideoPublicationSummary,
    VideoProject,
)
from app.services.audit import AuditService
from app.services.domain_events import DomainEventBus


SECRET_KEY_FRAGMENTS = {"secret", "password", "token", "api_key", "apikey", "private_key", "credential_value"}
RAW_SECRET_MARKERS = ("sk-", "pk_live_", "BEGIN PRIVATE KEY", "anthropic-", "xoxb-", "ghp_")


class PublishHandoffService:
    def __init__(self, session: Session):
        self.session = session

    def create_from_render_package(
        self,
        *,
        data: PublishHandoffCreate,
        correlation_id: str = "m7-publish-handoff-create",
    ) -> PublishHandoffPackage:
        package = self.session.get(RenderPackageSnapshot, data.render_package_snapshot_id)
        if package is None:
            raise NotFoundError(f"render package not found: {data.render_package_snapshot_id}")
        project = _require_project(self.session, package.video_project_id)
        run = self.session.get(ProductionArtifactRun, package.production_artifact_run_id) if package.production_artifact_run_id else None
        render_spec = self.session.get(RenderSpecSnapshot, package.render_spec_snapshot_id)
        media_qc = _latest_media_qc(self.session, package)
        accessibility_qc = _latest_accessibility_qc(self.session, package, run)
        source_manifest = _source_manifest_for_package(self.session, package, render_spec, run)
        asset_manifest = _asset_manifest_for_package(self.session, render_spec, run)

        planned_metadata = self._compile_planned_metadata(
            project=project,
            package=package,
            render_spec=render_spec,
            source_manifest=source_manifest,
            asset_manifest=asset_manifest,
            overrides=data.planned_metadata_overrides,
        )
        planned_files = {
            "final_video_ref": package.final_video_ref,
            "thumbnail_ref": package.thumbnail_ref,
            "caption_ref": package.caption_ref,
            "manifest_ref": package.manifest_ref,
            "checksum_manifest": package.checksum_manifest,
        }
        planned_disclosures = _planned_disclosures(planned_metadata)
        checklist = self._compile_checklist(
            target_platform=data.target_platform,
            target_surface=data.target_surface,
            planned_metadata=planned_metadata,
            package=package,
            media_qc=media_qc,
            accessibility_qc=accessibility_qc,
        )
        instructions = self._compile_operator_instructions(
            target_platform=data.target_platform,
            target_surface=data.target_surface,
            planned_metadata=planned_metadata,
            planned_files=planned_files,
            planned_disclosures=planned_disclosures,
        )
        reason_codes = _dedupe(
            [
                "PUBLISH_HANDOFF_CREATED",
                "NO_PLATFORM_API_CALL",
                "AUTO_PUBLISH_NOT_SUPPORTED",
                *checklist.blocking_reason_codes,
            ]
        )
        package_state = "BLOCKED" if checklist.blocking_reason_codes else "DRAFT"
        next_action = (
            "Fix render package or QC blockers before handing this package to an operator."
            if package_state == "BLOCKED"
            else "Review the checklist, then run handoff-ready before manual upload."
        )
        handoff = PublishHandoffPackage(
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            policy_snapshot_id=project.policy_snapshot_id,
            production_artifact_run_id=package.production_artifact_run_id,
            render_package_snapshot_id=package.id,
            render_spec_snapshot_id=package.render_spec_snapshot_id,
            media_qc_report_id=media_qc.id if media_qc else None,
            accessibility_qc_report_id=accessibility_qc.id if accessibility_qc else None,
            source_manifest_snapshot_id=source_manifest.id if source_manifest else None,
            asset_manifest_snapshot_id=asset_manifest.id if asset_manifest else None,
            target_platform=data.target_platform,
            target_surface=data.target_surface,
            destination_binding_id=data.destination_binding_id,
            render_variant_id=data.render_variant_id,
            package_state=package_state,
            planned_metadata=planned_metadata.model_dump(mode="json"),
            planned_disclosures=planned_disclosures,
            planned_files=_jsonable(planned_files),
            checklist_snapshot=checklist.model_dump(mode="json"),
            operator_instructions=instructions.model_dump(mode="json"),
            risk_summary={
                "media_qc_state": media_qc.qc_state if media_qc else "MISSING",
                "accessibility_qc_state": accessibility_qc.qc_state if accessibility_qc else "MISSING",
                "ai_disclosure_required": planned_metadata.planned_ai_disclosure_required,
                "rights_confirmation_required": True,
                "no_platform_api_call": True,
            },
            reason_codes=reason_codes,
            next_action=next_action,
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(handoff)
        self.session.flush()
        _record_m7_event(
            self.session,
            event_type="publish_handoff_package.created",
            aggregate_type="publish_handoff_package",
            aggregate_id=handoff.id,
            actor_id=data.created_by_user_id,
            target_type="publish_handoff_package",
            target_id=handoff.id,
            company_id=handoff.company_id,
            correlation_id=correlation_id,
            reason_code="PUBLISH_HANDOFF_CREATED",
            payload={
                "video_project_id": str(handoff.video_project_id),
                "render_package_snapshot_id": str(handoff.render_package_snapshot_id),
                "policy_snapshot_id": str(handoff.policy_snapshot_id),
                "package_state": handoff.package_state,
                "target_platform": handoff.target_platform,
                "no_platform_api_call": True,
            },
        )
        if package_state == "BLOCKED":
            _record_m7_event(
                self.session,
                event_type="publish_handoff_package.blocked",
                aggregate_type="publish_handoff_package",
                aggregate_id=handoff.id,
                actor_id=data.created_by_user_id,
                target_type="publish_handoff_package",
                target_id=handoff.id,
                company_id=handoff.company_id,
                correlation_id=correlation_id,
                reason_code=checklist.blocking_reason_codes[0],
                payload={"reason_codes": checklist.blocking_reason_codes, "next_action": handoff.next_action},
            )
        return handoff

    def get(self, handoff_id: uuid.UUID) -> PublishHandoffPackage | None:
        return self.session.get(PublishHandoffPackage, handoff_id)

    def require(self, handoff_id: uuid.UUID) -> PublishHandoffPackage:
        handoff = self.get(handoff_id)
        if handoff is None:
            raise NotFoundError(f"publish handoff package not found: {handoff_id}")
        return handoff

    def mark_ready(
        self,
        *,
        handoff_id: uuid.UUID,
        correlation_id: str = "m7-publish-handoff-ready",
    ) -> PublishHandoffPackage:
        handoff = self.require(handoff_id)
        if handoff.package_state == "BLOCKED":
            raise ValidationFailureError("blocked handoff cannot be marked ready")
        package = self.session.get(RenderPackageSnapshot, handoff.render_package_snapshot_id)
        if package is None:
            raise NotFoundError(f"render package not found: {handoff.render_package_snapshot_id}")
        media_qc = _latest_media_qc(self.session, package)
        blockers = _handoff_blockers(package, media_qc)
        if blockers:
            handoff.package_state = "BLOCKED"
            handoff.reason_codes = _dedupe([*handoff.reason_codes, "PUBLISH_HANDOFF_BLOCKED", *blockers])
            handoff.next_action = "Fix render package or QC blockers before manual upload."
            self.session.flush()
            _record_m7_event(
                self.session,
                event_type="publish_handoff_package.blocked",
                aggregate_type="publish_handoff_package",
                aggregate_id=handoff.id,
                actor_id=handoff.created_by_user_id,
                target_type="publish_handoff_package",
                target_id=handoff.id,
                company_id=handoff.company_id,
                correlation_id=correlation_id,
                reason_code=blockers[0],
                payload={"reason_codes": blockers, "next_action": handoff.next_action},
            )
            return handoff
        handoff.package_state = "READY_FOR_OPERATOR"
        handoff.reason_codes = _dedupe([*handoff.reason_codes, "PUBLISH_HANDOFF_READY"])
        handoff.next_action = "Human operator must upload outside VCOS, then copy actual publish data back into VCOS."
        self.session.flush()
        _record_m7_event(
            self.session,
            event_type="publish_handoff_package.ready",
            aggregate_type="publish_handoff_package",
            aggregate_id=handoff.id,
            actor_id=handoff.created_by_user_id,
            target_type="publish_handoff_package",
            target_id=handoff.id,
            company_id=handoff.company_id,
            correlation_id=correlation_id,
            reason_code="PUBLISH_HANDOFF_READY",
            payload={
                "video_project_id": str(handoff.video_project_id),
                "render_package_snapshot_id": str(handoff.render_package_snapshot_id),
                "target_platform": handoff.target_platform,
            },
        )
        return handoff

    def _compile_planned_metadata(
        self,
        *,
        project: VideoProject,
        package: RenderPackageSnapshot,
        render_spec: RenderSpecSnapshot | None,
        source_manifest: SourceManifestSnapshot | None,
        asset_manifest: AssetManifestSnapshot | None,
        overrides: dict[str, Any],
    ) -> PlannedPublishMetadataContract:
        source_blob = source_manifest.source_manifest_blob if source_manifest else {}
        asset_blob = asset_manifest.asset_manifest_blob if asset_manifest else {}
        ai_required = _contains_ai_disclosure_signal(source_blob) or _contains_ai_disclosure_signal(asset_blob)
        rights_summary = _rights_summary(source_blob=source_blob, asset_blob=asset_blob)
        source_summary = {
            "source_manifest_snapshot_id": str(source_manifest.id) if source_manifest else None,
            "asset_manifest_snapshot_id": str(asset_manifest.id) if asset_manifest else None,
            "source_ref_count": len(source_blob.get("source_refs", [])) if isinstance(source_blob.get("source_refs"), list) else 0,
        }
        render_spec_blob = render_spec.render_spec_blob if render_spec else {}
        language = _caption_language(render_spec_blob)
        payload = {
            "planned_title": project.title,
            "planned_description": project.description or project.title,
            "planned_tags": [],
            "planned_hashtags": [],
            "planned_category": None,
            "planned_language": language,
            "planned_thumbnail_ref": package.thumbnail_ref,
            "planned_caption_ref": package.caption_ref,
            "planned_privacy_status": "UNKNOWN",
            "planned_made_for_kids": None,
            "planned_ai_disclosure_required": ai_required,
            "planned_ai_disclosure_reason": "Source manifest indicates AI placeholder or realistic generated scene." if ai_required else None,
            "planned_paid_promotion_disclosure_required": False,
            "planned_music_license_summary": None,
            "planned_rights_summary": rights_summary,
            "planned_source_summary": source_summary,
        }
        payload.update(overrides)
        return PlannedPublishMetadataContract.model_validate(payload)

    def _compile_checklist(
        self,
        *,
        target_platform: str,
        target_surface: str,
        planned_metadata: PlannedPublishMetadataContract,
        package: RenderPackageSnapshot,
        media_qc: MediaQCReport | None,
        accessibility_qc: AccessibilityQCReport | None,
    ) -> PublishChecklistContract:
        blockers = _handoff_blockers(package, media_qc)
        items = [
            PublishChecklistItem(
                item_id="file_ready",
                category="FILE_READY",
                label="Final video file is ready",
                description="Upload the final MP4 file listed in planned files.",
                required=True,
                state="CONFIRMED" if package.final_video_ref else "BLOCKED",
                reason_code=None if package.final_video_ref else "RENDER_PACKAGE_NOT_READY",
                evidence_ref=_file_evidence(package.final_video_ref),
                operator_help_text="Use the final video file path, not a database id.",
            ),
            PublishChecklistItem(
                item_id="metadata_ready",
                category="METADATA_READY",
                label="Planned title and description are ready",
                description="Paste the planned title and description unless the operator intentionally changes them.",
                required=True,
                state="CONFIRMED" if planned_metadata.planned_title else "BLOCKED",
                reason_code=None if planned_metadata.planned_title else "ACTUAL_METADATA_CAPTURED",
                operator_help_text="If you change metadata during upload, copy the actual values back into VCOS.",
            ),
            PublishChecklistItem(
                item_id="thumbnail_ready",
                category="THUMBNAIL_READY",
                label="Thumbnail reference reviewed",
                description="Use the planned thumbnail when present, otherwise use the platform default or a human-approved thumbnail.",
                required=False,
                state="CONFIRMED" if planned_metadata.planned_thumbnail_ref else "NOT_REQUIRED",
                reason_code=None if planned_metadata.planned_thumbnail_ref else "THUMBNAIL_REF_MISSING",
                operator_help_text="Record the actual thumbnail ref or hash after upload when it differs.",
            ),
            PublishChecklistItem(
                item_id="captions_ready",
                category="CAPTIONS_READY",
                label="Caption file is ready",
                description="Upload captions when the platform flow supports captions.",
                required=True,
                state="CONFIRMED" if package.caption_ref else "BLOCKED",
                reason_code=None if package.caption_ref else "CAPTION_FILE_MISSING",
                evidence_ref=_file_evidence(package.caption_ref),
                operator_help_text="Use the SRT sidecar file from the render package.",
            ),
            PublishChecklistItem(
                item_id="ai_disclosure",
                category="AI_DISCLOSURE",
                label="AI disclosure decision",
                description="Check the platform AI disclosure box when the planned package says it is required.",
                required=planned_metadata.planned_ai_disclosure_required,
                state="PENDING" if planned_metadata.planned_ai_disclosure_required else "NOT_REQUIRED",
                reason_code="AI_DISCLOSURE_REQUIRED" if planned_metadata.planned_ai_disclosure_required else None,
                operator_help_text="Copy the exact disclosure choice back into VCOS after publishing.",
            ),
            PublishChecklistItem(
                item_id="paid_promotion",
                category="PAID_PROMOTION_DISCLOSURE",
                label="Paid promotion disclosure",
                description="Confirm whether paid promotion disclosure applies.",
                required=planned_metadata.planned_paid_promotion_disclosure_required,
                state="PENDING" if planned_metadata.planned_paid_promotion_disclosure_required else "NOT_REQUIRED",
                reason_code="PAID_PROMOTION_DISCLOSURE_REQUIRED"
                if planned_metadata.planned_paid_promotion_disclosure_required
                else None,
                operator_help_text="Do not infer sponsorship; use project/business truth.",
            ),
            PublishChecklistItem(
                item_id="music_license",
                category="MUSIC_LICENSE",
                label="Music license confirmation",
                description="Confirm music license status before pressing publish.",
                required=False,
                state="PENDING",
                reason_code="MUSIC_LICENSE_CONFIRMATION_REQUIRED",
                operator_help_text="Mark not applicable in actual disclosures if no music asset is used.",
            ),
            PublishChecklistItem(
                item_id="stock_license",
                category="STOCK_LICENSE",
                label="Stock asset license confirmation",
                description="Confirm stock or externally sourced asset license status.",
                required=True,
                state="PENDING",
                reason_code="STOCK_LICENSE_CONFIRMATION_REQUIRED",
                operator_help_text="Confirm no unlicensed assets were used before final acceptance.",
            ),
            PublishChecklistItem(
                item_id="rights_envelope",
                category="RIGHTS_ENVELOPE",
                label="Rights envelope confirmation",
                description="Confirm the rights envelope and license evidence before accepting the publish confirmation.",
                required=True,
                state="PENDING",
                reason_code="RIGHTS_CONFIRMATION_REQUIRED",
                operator_help_text="VCOS will require rights_confirmed=true before an accepted UploadedVideo is created.",
            ),
            PublishChecklistItem(
                item_id="privacy_status",
                category="PRIVACY_STATUS",
                label="Privacy status selected",
                description="Select the intended privacy state on the platform.",
                required=True,
                state="PENDING" if planned_metadata.planned_privacy_status == "UNKNOWN" else "CONFIRMED",
                operator_help_text="Copy actual privacy status back into VCOS.",
            ),
            PublishChecklistItem(
                item_id="platform_surface",
                category="PLATFORM_SURFACE",
                label="Platform and surface match",
                description=f"Upload to {target_platform} as {target_surface}.",
                required=True,
                state="CONFIRMED",
                operator_help_text="Do not use any VCOS platform API; upload manually outside VCOS.",
            ),
            PublishChecklistItem(
                item_id="final_human_review",
                category="FINAL_HUMAN_REVIEW",
                label="Final human review",
                description="Human operator reviews the platform preview before pressing publish.",
                required=True,
                state="PENDING",
                operator_help_text="After publishing, copy video id, URL, published time, actual metadata, and disclosures back into VCOS.",
            ),
        ]
        if accessibility_qc and accessibility_qc.qc_state != "PASS":
            blockers.append("ACCESSIBILITY_QC_REVIEW_REQUIRED")
        return PublishChecklistContract(
            target_platform=target_platform,
            target_surface=target_surface,
            items=items,
            blocking_reason_codes=_dedupe(blockers),
            operator_summary="Manual upload checklist generated. Human must upload outside VCOS and confirm actual publish data afterward.",
        )

    def _compile_operator_instructions(
        self,
        *,
        target_platform: str,
        target_surface: str,
        planned_metadata: PlannedPublishMetadataContract,
        planned_files: dict[str, Any],
        planned_disclosures: dict[str, Any],
    ) -> PlatformPublishInstructionContract:
        final_path = (planned_files.get("final_video_ref") or {}).get("file_path") or "the final video file from planned_files"
        caption_path = (planned_files.get("caption_ref") or {}).get("file_path")
        thumbnail_path = (planned_files.get("thumbnail_ref") or {}).get("file_path")
        return PlatformPublishInstructionContract(
            target_platform=target_platform,
            target_surface=target_surface,
            upload_file_instruction=f"Manually upload this video file outside VCOS: {final_path}.",
            title_instruction=f"Use this planned title unless a human changes it: {planned_metadata.planned_title}",
            description_instruction=f"Paste this planned description: {planned_metadata.planned_description or ''}",
            thumbnail_instruction=(
                f"Use this thumbnail file: {thumbnail_path}."
                if thumbnail_path
                else "No thumbnail file is planned; use platform default or a human-approved thumbnail and record the actual value."
            ),
            caption_instruction=(
                f"Upload this caption file when supported: {caption_path}."
                if caption_path
                else "No caption file is present; do not claim captions were uploaded."
            ),
            ai_disclosure_instruction=(
                "Check the platform AI disclosure or synthetic media label and record the exact label used."
                if planned_disclosures["ai_disclosure_required"]
                else "AI disclosure is not planned as required; still record the actual platform choice."
            ),
            paid_promotion_instruction=(
                "Check paid promotion disclosure if the upload is sponsored or promoted."
                if planned_disclosures["paid_promotion_disclosure_required"]
                else "Paid promotion is not planned; record the actual disclosure state."
            ),
            pre_publish_verification=[
                "Verify video preview, title, description, captions, thumbnail, visibility, and disclosure boxes before pressing publish.",
                "Do not use a VCOS upload or platform API; this handoff is manual only.",
            ],
            copy_back_fields=[
                "actual_video_id",
                "actual_video_url",
                "actual_published_at",
                "actual_title",
                "actual_description",
                "actual_tags",
                "actual_privacy_status",
                "actual_thumbnail_ref or actual_thumbnail_hash",
                "actual disclosure/license confirmations",
            ],
        )


class ManualPublishConfirmationService:
    def __init__(self, session: Session):
        self.session = session

    def create_confirmation(
        self,
        *,
        data: ManualPublishConfirmationCreate,
        correlation_id: str = "m7-manual-publish-confirm",
    ) -> ManualPublishConfirmation:
        handoff = PublishHandoffService(self.session).require(data.publish_handoff_package_id)
        if handoff.package_state not in {"READY_FOR_OPERATOR", "DRAFT"}:
            raise ValidationFailureError("handoff is not ready for manual confirmation")
        _validate_video_url(data.actual_video_url or "")
        self._reject_duplicate(
            channel_workspace_id=handoff.channel_workspace_id,
            platform=handoff.target_platform,
            platform_video_id=data.actual_video_id or "",
        )
        actual_metadata = ActualPublishMetadataContract.model_validate(data.actual_metadata)
        actual_disclosures = ActualDisclosureConfirmationContract.model_validate(data.actual_disclosures)
        diff = compute_metadata_diff(
            planned_metadata=handoff.planned_metadata,
            planned_disclosures=handoff.planned_disclosures,
            actual_metadata=actual_metadata.model_dump(mode="json"),
            actual_disclosures=actual_disclosures.model_dump(mode="json"),
        )
        validation_summary, reason_codes, next_action = self._validate_disclosures(
            handoff=handoff,
            actual_disclosures=actual_disclosures.model_dump(mode="json"),
            metadata_diff=diff,
        )
        state = "REVIEW_REQUIRED" if validation_summary["requires_review"] else "SUBMITTED"
        confirmation = ManualPublishConfirmation(
            publish_handoff_package_id=handoff.id,
            company_id=handoff.company_id,
            channel_workspace_id=handoff.channel_workspace_id,
            video_project_id=handoff.video_project_id,
            policy_snapshot_id=handoff.policy_snapshot_id,
            target_platform=handoff.target_platform,
            target_surface=handoff.target_surface,
            confirmed_by_user_id=data.confirmed_by_user_id,
            confirmation_state=state,
            actual_video_id=data.actual_video_id,
            actual_video_url=data.actual_video_url,
            actual_published_at=data.actual_published_at,
            actual_metadata=actual_metadata.model_dump(mode="json"),
            actual_disclosures=actual_disclosures.model_dump(mode="json"),
            actual_files=_jsonable(data.actual_files),
            operator_notes=data.operator_notes,
            validation_summary=validation_summary,
            metadata_diff=diff.model_dump(mode="json"),
            reason_codes=_dedupe(["ACTUAL_METADATA_CAPTURED", *reason_codes]),
            next_action=next_action,
        )
        self.session.add(confirmation)
        self.session.flush()
        _record_m7_event(
            self.session,
            event_type="manual_publish_confirmation.created",
            aggregate_type="manual_publish_confirmation",
            aggregate_id=confirmation.id,
            actor_id=data.confirmed_by_user_id,
            target_type="manual_publish_confirmation",
            target_id=confirmation.id,
            company_id=confirmation.company_id,
            correlation_id=correlation_id,
            reason_code="ACTUAL_METADATA_CAPTURED",
            payload={
                "publish_handoff_package_id": str(handoff.id),
                "video_project_id": str(handoff.video_project_id),
                "target_platform": handoff.target_platform,
                "confirmation_state": confirmation.confirmation_state,
            },
        )
        if diff.changed_fields:
            _record_m7_event(
                self.session,
                event_type="metadata_diff.detected",
                aggregate_type="manual_publish_confirmation",
                aggregate_id=confirmation.id,
                actor_id=data.confirmed_by_user_id,
                target_type="manual_publish_confirmation",
                target_id=confirmation.id,
                company_id=confirmation.company_id,
                correlation_id=correlation_id,
                reason_code="METADATA_DIFF_DETECTED",
                payload={
                    "changed_fields": diff.changed_fields,
                    "severity": diff.severity,
                    "requires_review": diff.requires_review,
                },
            )
        if confirmation.confirmation_state == "REVIEW_REQUIRED":
            _record_m7_event(
                self.session,
                event_type="manual_publish_confirmation.review_required",
                aggregate_type="manual_publish_confirmation",
                aggregate_id=confirmation.id,
                actor_id=data.confirmed_by_user_id,
                target_type="manual_publish_confirmation",
                target_id=confirmation.id,
                company_id=confirmation.company_id,
                correlation_id=correlation_id,
                reason_code=confirmation.reason_codes[-1],
                payload={"reason_codes": confirmation.reason_codes, "next_action": confirmation.next_action},
            )
            if "AI_DISCLOSURE_NOT_CONFIRMED" in confirmation.reason_codes or "RIGHTS_CONFIRMATION_REQUIRED" in confirmation.reason_codes:
                _record_m7_event(
                    self.session,
                    event_type="disclosure_confirmation.missing",
                    aggregate_type="manual_publish_confirmation",
                    aggregate_id=confirmation.id,
                    actor_id=data.confirmed_by_user_id,
                    target_type="manual_publish_confirmation",
                    target_id=confirmation.id,
                    company_id=confirmation.company_id,
                    correlation_id=correlation_id,
                    reason_code=confirmation.reason_codes[-1],
                    payload={"reason_codes": confirmation.reason_codes},
                )
        return confirmation

    def get_confirmation(self, confirmation_id: uuid.UUID) -> ManualPublishConfirmation | None:
        return self.session.get(ManualPublishConfirmation, confirmation_id)

    def require_confirmation(self, confirmation_id: uuid.UUID) -> ManualPublishConfirmation:
        confirmation = self.get_confirmation(confirmation_id)
        if confirmation is None:
            raise NotFoundError(f"manual publish confirmation not found: {confirmation_id}")
        return confirmation

    def accept_confirmation(
        self,
        *,
        confirmation_id: uuid.UUID,
        correlation_id: str = "m7-manual-publish-accept",
    ) -> UploadedVideo:
        confirmation = self.require_confirmation(confirmation_id)
        if confirmation.confirmation_state == "ACCEPTED":
            existing = self.session.scalars(
                select(UploadedVideo).where(UploadedVideo.manual_publish_confirmation_id == confirmation.id)
            ).one_or_none()
            if existing is None:
                raise ValidationFailureError("accepted confirmation is missing uploaded video record")
            return existing
        if confirmation.confirmation_state != "SUBMITTED":
            raise ValidationFailureError("only submitted confirmations can be accepted")
        handoff = self.session.get(PublishHandoffPackage, confirmation.publish_handoff_package_id)
        if handoff is None:
            raise NotFoundError(f"publish handoff package not found: {confirmation.publish_handoff_package_id}")
        self._reject_duplicate(
            channel_workspace_id=confirmation.channel_workspace_id,
            platform=confirmation.target_platform,
            platform_video_id=confirmation.actual_video_id or "",
            exclude_confirmation_id=confirmation.id,
        )
        lineage_refs = _lineage_refs(handoff)
        uploaded = UploadedVideo(
            company_id=confirmation.company_id,
            channel_workspace_id=confirmation.channel_workspace_id,
            video_project_id=confirmation.video_project_id,
            policy_snapshot_id=confirmation.policy_snapshot_id,
            publish_handoff_package_id=handoff.id,
            manual_publish_confirmation_id=confirmation.id,
            render_package_snapshot_id=handoff.render_package_snapshot_id,
            source_manifest_snapshot_id=handoff.source_manifest_snapshot_id,
            rights_envelope_ref=_rights_envelope_ref(handoff),
            platform=confirmation.target_platform,
            platform_video_id=confirmation.actual_video_id or "",
            video_url=confirmation.actual_video_url or "",
            published_at=confirmation.actual_published_at,
            publish_status="CONFIRMED",
            actual_metadata=confirmation.actual_metadata,
            actual_disclosures=confirmation.actual_disclosures,
            lineage_refs=lineage_refs,
            monitoring_state="READY_FOR_ANALYTICS",
            operator_summary=_uploaded_operator_summary(confirmation),
        )
        self.session.add(uploaded)
        self.session.flush()
        summary = _create_publication_summary(self.session, uploaded)
        confirmation.confirmation_state = "ACCEPTED"
        confirmation.reason_codes = _dedupe([*confirmation.reason_codes, "MANUAL_PUBLISH_CONFIRMED", "UPLOADED_VIDEO_CREATED"])
        confirmation.next_action = "Uploaded video is ready for a future analytics sync milestone."
        handoff.package_state = "CONFIRMED_PUBLISHED"
        handoff.reason_codes = _dedupe([*handoff.reason_codes, "MANUAL_PUBLISH_CONFIRMED", "UPLOADED_VIDEO_CREATED"])
        handoff.next_action = "Future M8 analytics may sync this uploaded video."
        self.session.flush()
        _record_m7_event(
            self.session,
            event_type="manual_publish_confirmation.accepted",
            aggregate_type="manual_publish_confirmation",
            aggregate_id=confirmation.id,
            actor_id=confirmation.confirmed_by_user_id,
            target_type="manual_publish_confirmation",
            target_id=confirmation.id,
            company_id=confirmation.company_id,
            correlation_id=correlation_id,
            reason_code="MANUAL_PUBLISH_CONFIRMED",
            payload={
                "uploaded_video_id": str(uploaded.id),
                "platform": uploaded.platform,
                "platform_video_id": uploaded.platform_video_id,
            },
        )
        _record_m7_event(
            self.session,
            event_type="uploaded_video.created",
            aggregate_type="uploaded_video",
            aggregate_id=uploaded.id,
            actor_id=confirmation.confirmed_by_user_id,
            target_type="uploaded_video",
            target_id=uploaded.id,
            company_id=uploaded.company_id,
            correlation_id=correlation_id,
            reason_code="UPLOADED_VIDEO_CREATED",
            payload={
                "video_project_id": str(uploaded.video_project_id),
                "render_package_snapshot_id": str(uploaded.render_package_snapshot_id),
                "policy_snapshot_id": str(uploaded.policy_snapshot_id),
                "platform": uploaded.platform,
                "platform_video_id": uploaded.platform_video_id,
            },
        )
        _record_m7_event(
            self.session,
            event_type="uploaded_video.ready_for_analytics",
            aggregate_type="uploaded_video",
            aggregate_id=uploaded.id,
            actor_id=confirmation.confirmed_by_user_id,
            target_type="uploaded_video",
            target_id=uploaded.id,
            company_id=uploaded.company_id,
            correlation_id=correlation_id,
            reason_code="READY_FOR_ANALYTICS",
            payload={"monitoring_state": uploaded.monitoring_state, "summary_id": str(summary.id)},
        )
        return uploaded

    def get_uploaded_video(self, uploaded_video_id: uuid.UUID) -> UploadedVideo | None:
        return self.session.get(UploadedVideo, uploaded_video_id)

    def list_uploaded_videos_by_project(self, project_id: uuid.UUID) -> list[UploadedVideo]:
        return list(
            self.session.scalars(
                select(UploadedVideo).where(UploadedVideo.video_project_id == project_id).order_by(UploadedVideo.published_at.desc())
            ).all()
        )

    def get_publication_summary(self, uploaded_video_id: uuid.UUID) -> UploadedVideoPublicationSummary | None:
        return self.session.scalars(
            select(UploadedVideoPublicationSummary).where(UploadedVideoPublicationSummary.uploaded_video_id == uploaded_video_id)
        ).one_or_none()

    def _reject_duplicate(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        platform: str,
        platform_video_id: str,
        exclude_confirmation_id: uuid.UUID | None = None,
    ) -> None:
        if not platform_video_id:
            raise ValidationFailureError("actual_video_id is required")
        duplicate_uploaded = self.session.scalars(
            select(UploadedVideo).where(
                UploadedVideo.channel_workspace_id == channel_workspace_id,
                UploadedVideo.platform == platform,
                UploadedVideo.platform_video_id == platform_video_id,
            )
        ).first()
        if duplicate_uploaded is not None:
            raise ConflictError("duplicate platform video id for channel/platform")
        duplicate_statement = select(ManualPublishConfirmation).where(
            ManualPublishConfirmation.channel_workspace_id == channel_workspace_id,
            ManualPublishConfirmation.target_platform == platform,
            ManualPublishConfirmation.actual_video_id == platform_video_id,
            ManualPublishConfirmation.confirmation_state.in_(["SUBMITTED", "ACCEPTED"]),
        )
        if exclude_confirmation_id is not None:
            duplicate_statement = duplicate_statement.where(ManualPublishConfirmation.id != exclude_confirmation_id)
        duplicate_confirmation = self.session.scalars(duplicate_statement).first()
        if duplicate_confirmation is not None:
            raise ConflictError("duplicate platform video id for channel/platform")

    def _validate_disclosures(
        self,
        *,
        handoff: PublishHandoffPackage,
        actual_disclosures: dict[str, Any],
        metadata_diff: PublishMetadataDiffContract,
    ) -> tuple[dict[str, Any], list[str], str | None]:
        reason_codes: list[str] = []
        if handoff.planned_disclosures.get("ai_disclosure_required") and not actual_disclosures.get("ai_disclosure_confirmed"):
            reason_codes.append("AI_DISCLOSURE_NOT_CONFIRMED")
        if not actual_disclosures.get("rights_confirmed"):
            reason_codes.append("RIGHTS_CONFIRMATION_REQUIRED")
        if actual_disclosures.get("music_license_confirmed") is False:
            reason_codes.append("MUSIC_LICENSE_CONFIRMATION_REQUIRED")
        if actual_disclosures.get("stock_license_confirmed") is False:
            reason_codes.append("STOCK_LICENSE_CONFIRMATION_REQUIRED")
        if metadata_diff.changed_fields:
            reason_codes.append("METADATA_DIFF_DETECTED")
        requires_review = bool(reason_codes and any(code != "METADATA_DIFF_DETECTED" for code in reason_codes)) or metadata_diff.requires_review
        return (
            {
                "valid_video_id": True,
                "valid_video_url": True,
                "actual_published_at_present": True,
                "requires_review": requires_review,
                "no_platform_api_call": True,
            },
            reason_codes,
            "Resolve disclosure/license review before accepting publication." if requires_review else None,
        )


def compute_metadata_diff(
    *,
    planned_metadata: dict[str, Any],
    planned_disclosures: dict[str, Any],
    actual_metadata: dict[str, Any],
    actual_disclosures: dict[str, Any],
) -> PublishMetadataDiffContract:
    changed_fields: list[str] = []
    title_changed = (planned_metadata.get("planned_title") or "") != (actual_metadata.get("actual_title") or "")
    description_changed = (planned_metadata.get("planned_description") or "") != (actual_metadata.get("actual_description") or "")
    tags_changed = sorted(planned_metadata.get("planned_tags") or []) != sorted(actual_metadata.get("actual_tags") or [])
    thumbnail_changed = bool(actual_metadata.get("actual_thumbnail_hash")) or bool(actual_metadata.get("actual_thumbnail_ref")) and (
        planned_metadata.get("planned_thumbnail_ref") != actual_metadata.get("actual_thumbnail_ref")
    )
    planned_privacy = planned_metadata.get("planned_privacy_status") or "UNKNOWN"
    privacy_status_changed = planned_privacy != "UNKNOWN" and planned_privacy != (actual_metadata.get("actual_privacy_status") or "UNKNOWN")
    disclosure_changed = bool(planned_disclosures.get("ai_disclosure_required")) and not bool(
        actual_disclosures.get("ai_disclosure_confirmed")
    )
    for field_name, changed in [
        ("title", title_changed),
        ("description", description_changed),
        ("tags", tags_changed),
        ("thumbnail", thumbnail_changed),
        ("privacy_status", privacy_status_changed),
        ("disclosure", disclosure_changed),
    ]:
        if changed:
            changed_fields.append(field_name)
    severity = "NONE"
    requires_review = False
    if disclosure_changed or actual_disclosures.get("rights_confirmed") is False:
        severity = "HIGH"
        requires_review = True
    elif thumbnail_changed or privacy_status_changed:
        severity = "MEDIUM"
        requires_review = True
    elif title_changed or description_changed or tags_changed:
        severity = "LOW"
    summary = (
        "No planned-vs-actual metadata changes."
        if not changed_fields
        else f"Human upload changed: {', '.join(changed_fields)}."
    )
    return PublishMetadataDiffContract(
        title_changed=title_changed,
        description_changed=description_changed,
        tags_changed=tags_changed,
        thumbnail_changed=thumbnail_changed,
        privacy_status_changed=privacy_status_changed,
        disclosure_changed=disclosure_changed,
        changed_fields=changed_fields,
        severity=severity,
        requires_review=requires_review,
        operator_summary=summary,
    )


def _require_project(session: Session, project_id: uuid.UUID) -> VideoProject:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise NotFoundError(f"project not found: {project_id}")
    return project


def _latest_media_qc(session: Session, package: RenderPackageSnapshot) -> MediaQCReport | None:
    return session.scalars(
        select(MediaQCReport)
        .where(MediaQCReport.render_package_snapshot_id == package.id)
        .order_by(MediaQCReport.created_at.desc())
    ).first()


def _latest_accessibility_qc(
    session: Session,
    package: RenderPackageSnapshot,
    run: ProductionArtifactRun | None,
) -> AccessibilityQCReport | None:
    statement = select(AccessibilityQCReport).where(AccessibilityQCReport.render_package_snapshot_id == package.id)
    report = session.scalars(statement.order_by(AccessibilityQCReport.created_at.desc())).first()
    if report is not None:
        return report
    if run and run.accessibility_qc_report_id:
        return session.get(AccessibilityQCReport, run.accessibility_qc_report_id)
    return None


def _source_manifest_for_package(
    session: Session,
    package: RenderPackageSnapshot,
    render_spec: RenderSpecSnapshot | None,
    run: ProductionArtifactRun | None,
) -> SourceManifestSnapshot | None:
    if run and run.source_manifest_snapshot_id:
        return session.get(SourceManifestSnapshot, run.source_manifest_snapshot_id)
    if render_spec is None:
        return None
    return session.scalars(
        select(SourceManifestSnapshot)
        .where(SourceManifestSnapshot.asset_manifest_snapshot_id == render_spec.asset_manifest_snapshot_id)
        .order_by(SourceManifestSnapshot.created_at.desc())
    ).first()


def _asset_manifest_for_package(
    session: Session,
    render_spec: RenderSpecSnapshot | None,
    run: ProductionArtifactRun | None,
) -> AssetManifestSnapshot | None:
    if run and run.asset_manifest_snapshot_id:
        return session.get(AssetManifestSnapshot, run.asset_manifest_snapshot_id)
    if render_spec is None:
        return None
    return session.get(AssetManifestSnapshot, render_spec.asset_manifest_snapshot_id)


def _handoff_blockers(package: RenderPackageSnapshot, media_qc: MediaQCReport | None) -> list[str]:
    blockers: list[str] = []
    if package.package_state not in {"QC_PASSED", "QC_REVIEW_REQUIRED"}:
        blockers.append("RENDER_PACKAGE_NOT_READY")
    if not package.final_video_ref:
        blockers.append("RENDER_PACKAGE_NOT_READY")
    if not package.caption_ref:
        blockers.append("CAPTION_FILE_MISSING")
    if media_qc is None or media_qc.qc_state != "PASS":
        blockers.append("MEDIA_QC_NOT_PASSING")
    return _dedupe(blockers)


def _contains_ai_disclosure_signal(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key in {"requires_ai_disclosure_check", "ai_generated", "synthetic_media"} and item is True:
                return True
            if _contains_ai_disclosure_signal(item):
                return True
    elif isinstance(value, list):
        return any(_contains_ai_disclosure_signal(item) for item in value)
    elif isinstance(value, str):
        normalized = value.upper()
        return "AI_PLACEHOLDER" in normalized or "AI-GENERATED REALISTIC" in normalized
    return False


def _rights_summary(*, source_blob: dict[str, Any], asset_blob: dict[str, Any]) -> dict[str, Any]:
    candidates = asset_blob.get("candidates", []) if isinstance(asset_blob, dict) else []
    requirements = asset_blob.get("requirements", []) if isinstance(asset_blob, dict) else []
    license_required = 0
    internal_test_only = 0
    unknown = 0
    for candidate in candidates if isinstance(candidates, list) else []:
        rights = candidate.get("rights_envelope", {}) if isinstance(candidate, dict) else {}
        state = rights.get("license_state")
        if state == "LICENSE_REQUIRED":
            license_required += 1
        elif state == "INTERNAL_TEST_ONLY":
            internal_test_only += 1
        elif state in {None, "UNKNOWN"}:
            unknown += 1
    for requirement in requirements if isinstance(requirements, list) else []:
        state = requirement.get("license_requirement") if isinstance(requirement, dict) else None
        if state == "LICENSE_REQUIRED":
            license_required += 1
        elif state == "UNKNOWN":
            unknown += 1
    return {
        "license_required_count": license_required,
        "internal_test_only_asset_count": internal_test_only,
        "unknown_license_count": unknown,
        "rights_confirmation_required": True,
        "source_manifest_hash": source_blob.get("manifest_hash") if isinstance(source_blob, dict) else None,
    }


def _planned_disclosures(planned_metadata: PlannedPublishMetadataContract) -> dict[str, Any]:
    rights = planned_metadata.planned_rights_summary
    return {
        "ai_disclosure_required": planned_metadata.planned_ai_disclosure_required,
        "ai_disclosure_reason": planned_metadata.planned_ai_disclosure_reason,
        "paid_promotion_disclosure_required": planned_metadata.planned_paid_promotion_disclosure_required,
        "music_license_confirmation_required": False,
        "stock_license_confirmation_required": True,
        "rights_confirmation_required": True,
        "license_required_count": rights.get("license_required_count", 0),
        "unknown_license_count": rights.get("unknown_license_count", 0),
    }


def _caption_language(render_spec_blob: dict[str, Any]) -> str | None:
    ref = render_spec_blob.get("caption_track_ref") if isinstance(render_spec_blob, dict) else None
    return "en" if ref else None


def _file_evidence(file_ref: dict[str, Any] | None) -> str | None:
    if not file_ref:
        return None
    return str(file_ref.get("file_path") or file_ref.get("checksum") or "")


def _validate_video_url(url: str) -> None:
    if not url:
        raise ValidationFailureError("actual_video_url is required")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValidationFailureError("invalid video url")


def _lineage_refs(handoff: PublishHandoffPackage) -> dict[str, Any]:
    return {
        "video_project_id": str(handoff.video_project_id),
        "policy_snapshot_id": str(handoff.policy_snapshot_id),
        "production_artifact_run_id": str(handoff.production_artifact_run_id) if handoff.production_artifact_run_id else None,
        "render_package_snapshot_id": str(handoff.render_package_snapshot_id),
        "render_spec_snapshot_id": str(handoff.render_spec_snapshot_id) if handoff.render_spec_snapshot_id else None,
        "media_qc_report_id": str(handoff.media_qc_report_id) if handoff.media_qc_report_id else None,
        "accessibility_qc_report_id": str(handoff.accessibility_qc_report_id) if handoff.accessibility_qc_report_id else None,
        "source_manifest_snapshot_id": str(handoff.source_manifest_snapshot_id) if handoff.source_manifest_snapshot_id else None,
        "asset_manifest_snapshot_id": str(handoff.asset_manifest_snapshot_id) if handoff.asset_manifest_snapshot_id else None,
    }


def _rights_envelope_ref(handoff: PublishHandoffPackage) -> str | None:
    if handoff.source_manifest_snapshot_id:
        return f"source_manifest_snapshot:{handoff.source_manifest_snapshot_id}"
    if handoff.asset_manifest_snapshot_id:
        return f"asset_manifest_snapshot:{handoff.asset_manifest_snapshot_id}"
    return None


def _uploaded_operator_summary(confirmation: ManualPublishConfirmation) -> dict[str, Any]:
    title = confirmation.actual_metadata.get("actual_title", "Untitled")
    return {
        "summary": f"Manual publish confirmed for {confirmation.target_platform}: {title}",
        "platform_video_id": confirmation.actual_video_id,
        "video_url": confirmation.actual_video_url,
        "next_action": "Ready for future M8 analytics sync.",
    }


def _create_publication_summary(session: Session, uploaded: UploadedVideo) -> UploadedVideoPublicationSummary:
    existing = session.scalars(
        select(UploadedVideoPublicationSummary).where(UploadedVideoPublicationSummary.uploaded_video_id == uploaded.id)
    ).one_or_none()
    title = str(uploaded.actual_metadata.get("actual_title") or "Untitled")
    if existing is None:
        existing = UploadedVideoPublicationSummary(
            uploaded_video_id=uploaded.id,
            company_id=uploaded.company_id,
            channel_workspace_id=uploaded.channel_workspace_id,
            video_project_id=uploaded.video_project_id,
            platform=uploaded.platform,
            platform_video_id=uploaded.platform_video_id,
            video_url=uploaded.video_url,
            published_at=uploaded.published_at,
            title=title,
            publish_status=uploaded.publish_status,
            monitoring_state=uploaded.monitoring_state,
            operator_status="READY_FOR_ANALYTICS",
            operator_summary=f"{uploaded.platform} video {uploaded.platform_video_id} is manually confirmed and ready for future analytics.",
            next_action="M8 may sync analytics later. No metrics exist in M7.",
            freshness_state="NOT_STARTED",
        )
        session.add(existing)
    else:
        existing.title = title
        existing.publish_status = uploaded.publish_status
        existing.monitoring_state = uploaded.monitoring_state
        existing.operator_status = "READY_FOR_ANALYTICS"
        existing.operator_summary = f"{uploaded.platform} video {uploaded.platform_video_id} is manually confirmed and ready for future analytics."
        existing.next_action = "M8 may sync analytics later. No metrics exist in M7."
    session.flush()
    return existing


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


def _record_m7_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    reason_code: str,
    payload: dict[str, Any],
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
