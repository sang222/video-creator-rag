"""Canonical v2 final-video decision and manual-publish workflow."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.events import EventEnvelope
from app.contracts.production_package import (
    ProductionPackageContentV2,
    ProductionReadinessReceiptContentV2,
)
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_publish import (
    FinalReviewCandidateCreateV2,
    FinalVideoDecisionCreate,
    FinalVideoDecisionResult,
    FinalVideoDecisionValue,
    HumanUploadTaskCancelV2,
    HumanUploadTaskStartV2,
    ManualPublishConfirmationCreateV2,
    ManualPublishConfirmationReadV2,
    ManualPublishCorrectionV2,
    ManualPublishVerificationResultV2,
    ManualPublishVerificationV2,
    PUBLISH_MATERIALITY_POLICY_V1,
    UploadedVideoReadV2,
)
from app.contracts.vcos_v2 import StrategicLineageV2
from app.core.actor import ActorContext, ActorType
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationFailureError,
)
from app.core.time import utc_now
from app.db.models.m10_1 import HumanUploadTask
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m5 import ProjectAdmissionDecision
from app.db.models.m6 import MediaQCReport
from app.db.models.m7 import ManualPublishConfirmation, UploadedVideo
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.production_publish import (
    FinalReviewCandidate,
    FinalVideoDecision,
    SeriesEpisodePublication,
)
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.vcos_v2 import SeriesRun
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.company_access import require_company_permission
from app.services.config_registry import content_hash
from app.services.domain_events import DomainEventBus
from app.services.long_form_analytics import LongFormAnalyticsScheduler
from app.services.production_package import (
    ProductionPackageService,
    strategic_lineage_from_record,
)


PUBLISH_SERVICE_VERSION = "vcos.production-publish.v2"
_EVENT_NAMESPACE = uuid.UUID("6b147c80-23f9-5d0c-9e1a-128d79e3455a")
_DURATION_TOLERANCE_SECONDS = Decimal("1.000000")
_V2_DRIVE_ARCHIVE_ADAPTER_KEYS = frozenset(
    {
        "v2-google-drive-archive",
        "v2-google-drive-remote",
    }
)
_V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE = "v2_drive_final_media_lineage_receipt"
_V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA = "vcos.v2-drive-final-media-lineage.v1"


@dataclass(frozen=True, slots=True)
class VerifiedCandidateMedia:
    path: Path
    file_name: str
    media_type: str
    checksum_sha256: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _final_review_candidate_hash_candidates(
    *,
    payload: dict[str, Any],
    target_market_lineage: dict[str, Any],
    allow_historical_native_payload: bool,
) -> tuple[str, ...]:
    """Return current and immutable pre-0079 candidate identities.

    The historical projection is deliberately unavailable to AI replacement
    candidates.  It exists only so an idempotent replay can find a native
    candidate whose hash predates the nullable AI lineage columns.
    """

    variants = [stable_hash(payload)]
    if target_market_lineage.get("destination_mode") is None:
        variants.append(
            stable_hash({**payload, "target_market_lineage": target_market_lineage})
        )
    if allow_historical_native_payload:
        historical = dict(payload)
        for key in (
            "ai_visual_production_run_id",
            "ai_visual_asset_manifest_hash",
            "ffmpeg_effect_plan_hash",
        ):
            historical.pop(key, None)
        variants.append(stable_hash(historical))
        if target_market_lineage.get("destination_mode") is None:
            variants.append(
                stable_hash(
                    {**historical, "target_market_lineage": target_market_lineage}
                )
            )
    return tuple(dict.fromkeys(variants))


def _destination_lineage_projection(value: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "destination_mode",
        "destination_status",
        "destination_handle",
        "destination_binding_ref",
        "destination_binding_hash",
        "destination_model_hash",
        "destination_authority_hash",
        "publish_execution_allowed",
        "automatic_publish",
    }
    if value.get("destination_mode") == "FINAL_REVIEW_ONLY":
        keys.update(
            {
                "controlled_recovery_authority_id",
                "controlled_recovery_authority_hash",
                "settlement_authority_id",
                "settlement_authority_hash",
                "settlement_qualification_run_id",
                "settlement_provenance_hash",
            }
        )
    return {key: value.get(key) for key in sorted(keys)}


def _strategic_lineage_event_payload(
    lineage: StrategicLineageV2,
) -> dict[str, Any]:
    """Serialize only the frozen package object for an uploaded-video event."""

    return lineage.model_dump(mode="json")


def _v2_production_root() -> Path:
    configured = os.getenv("VCOS_V2_PRODUCTION_ROOT")
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[2] / "var" / "v2-production").resolve()


def _resolve_verified_archive_file(
    *,
    root: Path,
    target: Path,
    expected_checksum: str,
) -> Path:
    if not _is_sha256(expected_checksum):
        raise ValidationFailureError("VERIFIED_LOCAL_ARCHIVE_CHECKSUM_INVALID")
    guarded_paths = (
        root,
        root / "archive",
        target.parent,
        target,
    )
    if any(path.is_symlink() for path in guarded_paths):
        raise ValidationFailureError("VERIFIED_LOCAL_ARCHIVE_SYMLINK_REJECTED")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_target = target.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("verified local archive file not found") from exc
    if (
        not resolved_target.is_file()
        or not resolved_target.is_relative_to(resolved_root / "archive")
        or resolved_target != resolved_root / target.relative_to(root)
    ):
        raise ValidationFailureError("VERIFIED_LOCAL_ARCHIVE_PATH_REJECTED")
    digest = hashlib.sha256()
    with resolved_target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_checksum:
        raise ValidationFailureError(
            "VERIFIED_LOCAL_ARCHIVE_READBACK_CHECKSUM_MISMATCH"
        )
    return resolved_target


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _has_v2_drive_render_source(
    *,
    cloud: CloudMediaRef,
    final_media: FinalMediaRef,
    expected_checksum: str,
) -> bool:
    required = {
        "type": "v2_render_output",
        "render_output_checksum": expected_checksum,
        "production_package_artifact_version_id": str(
            final_media.production_package_artifact_version_id
        ),
        "production_package_hash": final_media.production_package_hash,
    }
    return any(
        isinstance(item, dict)
        and all(item.get(key) == value for key, value in required.items())
        for item in (cloud.source_refs or [])
    )


def _v2_drive_duration_matches(
    *,
    cloud_appendix: dict[str, Any],
    final_media: FinalMediaRef,
) -> bool:
    value = cloud_appendix.get("measured_render_duration_ms")
    if isinstance(value, bool) or final_media.duration_seconds is None:
        return False
    try:
        duration_ms = int(value)
    except (TypeError, ValueError):
        return False
    return (
        duration_ms > 0
        and str(value) == str(duration_ms)
        and duration_ms
        == int(
            (Decimal(final_media.duration_seconds) * Decimal(1000)).to_integral_value()
        )
    )


def _v2_drive_web_view_matches_file_id(cloud: CloudMediaRef) -> bool:
    if not cloud.web_view_link or not cloud.drive_file_id:
        return False
    parsed = urlparse(cloud.web_view_link)
    if parsed.scheme != "https" or parsed.netloc not in {
        "drive.google.com",
        "docs.google.com",
    }:
        return False
    file_id = str(cloud.drive_file_id)
    if file_id in parse_qs(parsed.query).get("id", []):
        return True
    parts = [part for part in parsed.path.split("/") if part]
    return any(
        part == "d" and index + 1 < len(parts) and parts[index + 1] == file_id
        for index, part in enumerate(parts)
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, uuid.UUID, Decimal)):
        return str(value)
    raise TypeError(f"unsupported canonical JSON value: {type(value)!r}")


class ProductionPublishService:
    """Sole v2 creator for decisions, upload tasks, and verified videos."""

    def __init__(self, session: Session):
        self.session = session

    # ------------------------------------------------------------------
    # Final review candidate (trusted coordinator only; no public route).
    # ------------------------------------------------------------------
    def create_final_review_candidate(
        self,
        data: FinalReviewCandidateCreateV2,
    ) -> FinalReviewCandidate:
        run = self.session.scalar(
            select(ProductionWorkflowRun)
            .where(ProductionWorkflowRun.id == data.workflow_run_id)
            .with_for_update()
        )
        if run is None:
            raise NotFoundError(
                f"production workflow run not found: {data.workflow_run_id}"
            )
        if run.video_project_id is None:
            raise ValidationFailureError("FINAL_REVIEW_PROJECT_NOT_ADMITTED")

        project = self.session.get(VideoProject, run.video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {run.video_project_id}")
        self._validate_v2_project(project)
        if (
            run.company_id != project.company_id
            or run.channel_workspace_id != project.channel_workspace_id
            or run.production_lane != project.production_lane
            or run.production_lane != "LONG_FORM"
        ):
            raise ValidationFailureError("FINAL_REVIEW_WORKFLOW_SCOPE_MISMATCH")

        exact_run_bindings = {
            "production_package_artifact_version_id": (
                data.production_package_artifact_version_id
            ),
            "production_package_hash": data.production_package_hash,
            "production_readiness_receipt_artifact_version_id": (
                data.production_readiness_receipt_artifact_version_id
            ),
            "production_readiness_receipt_hash": (
                data.production_readiness_receipt_hash
            ),
            "canonical_media_timeline_ref": data.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": data.canonical_media_timeline_hash,
            "ai_visual_production_run_id": data.ai_visual_production_run_id,
            "ai_visual_asset_manifest_hash": data.ai_visual_asset_manifest_hash,
            "ffmpeg_effect_plan_hash": data.ffmpeg_effect_plan_hash,
            "native_render_plan_ref": data.native_render_plan_ref,
            "native_render_plan_hash": data.native_render_plan_hash,
            "render_output_ref": data.render_output_ref,
            "render_output_checksum": data.render_output_checksum,
            "technical_qc_receipt_ref": data.technical_qc_receipt_ref,
            "technical_qc_receipt_hash": data.technical_qc_receipt_hash,
            "creative_qc_receipt_ref": data.creative_qc_receipt_ref,
            "creative_qc_receipt_hash": data.creative_qc_receipt_hash,
            "archive_receipt_ref": data.archive_receipt_ref,
            "archive_receipt_hash": data.archive_receipt_hash,
            "archive_object_ref": data.archive_object_ref,
            "archive_verification_state": data.archive_verification_state,
            "final_media_ref_id": data.final_media_ref_id,
            "destination_binding_id": data.destination_binding_id,
            "destination_binding_fingerprint": (data.destination_binding_fingerprint),
        }
        for field, expected in exact_run_bindings.items():
            if getattr(run, field) != expected:
                raise ValidationFailureError(
                    f"FINAL_REVIEW_WORKFLOW_BINDING_MISMATCH:{field}"
                )
        if run.final_media_ref_hash != data.render_output_checksum:
            raise ValidationFailureError("FINAL_REVIEW_FINAL_MEDIA_HASH_MISMATCH")

        package_version, package_artifact = self._require_artifact_version(
            data.production_package_artifact_version_id,
            expected_type="production_package",
            expected_hash=data.production_package_hash,
            expected_project_id=project.id,
        )
        readiness_version, _ = self._require_artifact_version(
            data.production_readiness_receipt_artifact_version_id,
            expected_type="production_readiness_receipt",
            expected_hash=data.production_readiness_receipt_hash,
            expected_project_id=project.id,
        )
        readiness = ProductionReadinessReceiptContentV2.model_validate(
            readiness_version.content
        )
        package_content = ProductionPackageContentV2.model_validate(
            package_version.content
        )
        if (
            package_content.company_id != project.company_id
            or package_content.channel_workspace_id != project.channel_workspace_id
            or package_content.video_project_id != project.id
            or package_content.channel_profile_version_id
            != project.channel_profile_version_id
            or package_content.compiled_policy_snapshot_id != project.policy_snapshot_id
            or package_content.production_lane != project.production_lane
            or package_content.content_mode != project.content_mode
            or package_content.series_plan_id != project.series_plan_id
            or package_content.series_run_id != project.series_run_id
            or package_content.episode_number != project.episode_number
            or package_content.standalone_reason_code != project.standalone_reason_code
        ):
            raise ValidationFailureError("FINAL_REVIEW_PACKAGE_PROJECT_SPLICE_DETECTED")
        if (
            readiness.production_package_artifact_version_id != package_version.id
            or readiness.production_package_hash != package_version.content_hash
            or readiness.channel_profile_version_id
            != project.channel_profile_version_id
            or readiness.compiled_policy_snapshot_id != project.policy_snapshot_id
        ):
            raise ValidationFailureError("FINAL_REVIEW_READINESS_SPLICE_DETECTED")
        if package_artifact.current_version_id != package_version.id:
            raise ValidationFailureError("FINAL_REVIEW_PACKAGE_NOT_CURRENT")

        final_media = self.session.get(FinalMediaRef, data.final_media_ref_id)
        if final_media is None:
            raise NotFoundError(f"final media ref not found: {data.final_media_ref_id}")
        if (
            final_media.company_id != project.company_id
            or final_media.channel_workspace_id != project.channel_workspace_id
            or final_media.video_project_id != project.id
            or final_media.production_package_artifact_version_id != package_version.id
            or final_media.production_package_hash != package_version.content_hash
            or final_media.checksum_sha256 != data.render_output_checksum
        ):
            raise ValidationFailureError("FINAL_REVIEW_FINAL_MEDIA_SPLICE_DETECTED")
        self._require_final_media_authority(
            final_media=final_media,
            project=project,
            package_content=package_content,
            data=data,
        )
        destination_projection = self._require_destination_binding_authority(
            project=project,
            package_content=package_content,
            data=data,
        )
        target_market_lineage = (
            dict(data.target_market_lineage)
            if data.target_market_lineage.get("destination_mode") is None
            else {
                **data.target_market_lineage,
                **_destination_lineage_projection(destination_projection),
            }
        )

        materiality_policy_hash = stable_hash(data.materiality_policy_snapshot)
        if data.ai_visual_production_run_id is not None:
            from app.db.models.ai_visual import (
                AIVisualAssetManifest,
                AIVisualProductionRun,
                AIVisualRerenderAuthority,
            )

            visual_run = self.session.get(
                AIVisualProductionRun, data.ai_visual_production_run_id
            )
            manifest = (
                self.session.get(AIVisualAssetManifest, visual_run.asset_manifest_id)
                if visual_run is not None and visual_run.asset_manifest_id is not None
                else None
            )
            rerender = (
                self.session.get(
                    AIVisualRerenderAuthority, visual_run.rerender_authority_id
                )
                if visual_run is not None
                and visual_run.rerender_authority_id is not None
                else None
            )
            if (
                run.ai_visual_production_run_id != data.ai_visual_production_run_id
                or run.ai_visual_asset_manifest_hash
                != data.ai_visual_asset_manifest_hash
                or run.ffmpeg_effect_plan_hash != data.ffmpeg_effect_plan_hash
                or visual_run is None
                or visual_run.workflow_run_id != run.id
                or visual_run.video_project_id != project.id
                or visual_run.production_package_artifact_version_id
                != package_version.id
                or visual_run.production_package_hash != package_version.content_hash
                or (
                    visual_run.state,
                    visual_run.current_phase,
                )
                not in {
                    ("ARCHIVED", "ARCHIVE"),
                    ("FINAL_REVIEW_READY", "FINALIZE"),
                }
                or visual_run.final_media_ref_id != data.final_media_ref_id
                or visual_run.render_output_checksum != data.render_output_checksum
                or visual_run.archive_receipt_hash != data.archive_receipt_hash
                or (
                    visual_run.state == "FINAL_REVIEW_READY"
                    and visual_run.final_review_candidate_id
                    != run.final_review_candidate_id
                )
                or manifest is None
                or visual_run.asset_manifest_hash != manifest.content_hash
                or manifest.content_hash != data.ai_visual_asset_manifest_hash
                or manifest.effect_plan_hash != data.ffmpeg_effect_plan_hash
            ):
                raise ValidationFailureError(
                    "FINAL_REVIEW_AI_VISUAL_REPLACEMENT_BINDING_MISMATCH"
                )
            if visual_run.execution_kind == "NORMAL_PRODUCTION":
                if (
                    visual_run.rerender_authority_id is not None
                    or rerender is not None
                    or data.supersedes_final_review_candidate_id is not None
                ):
                    raise ValidationFailureError(
                        "FINAL_REVIEW_NORMAL_AI_VISUAL_SUPERSEDES_FORBIDDEN"
                    )
            elif visual_run.execution_kind == "GOVERNED_RERENDER":
                if (
                    rerender is None
                    or rerender.replacement_workflow_run_id != run.id
                    or rerender.source_workflow_run_id == run.id
                    or rerender.rejected_final_review_candidate_id
                    != data.supersedes_final_review_candidate_id
                ):
                    raise ValidationFailureError(
                        "FINAL_REVIEW_AI_VISUAL_REPLACEMENT_BINDING_MISMATCH"
                    )
            else:
                raise ValidationFailureError(
                    "FINAL_REVIEW_AI_VISUAL_EXECUTION_KIND_INVALID"
                )
        payload = {
            "schema_version": "vcos.final-review-candidate.v2",
            "workflow_run_id": run.id,
            "company_id": project.company_id,
            "channel_workspace_id": project.channel_workspace_id,
            "video_project_id": project.id,
            "channel_profile_version_id": project.channel_profile_version_id,
            "policy_snapshot_id": project.policy_snapshot_id,
            **exact_run_bindings,
            **(
                {
                    "supersedes_final_review_candidate_id": (
                        data.supersedes_final_review_candidate_id
                    )
                }
                if data.supersedes_final_review_candidate_id is not None
                else {}
            ),
            "final_media_hash": final_media.checksum_sha256,
            "destination_platform_channel_id": (data.destination_platform_channel_id),
            "destination_account_identity": data.destination_account_identity,
            "target_platform": data.target_platform,
            "target_surface": data.target_surface,
            "target_market_lineage": target_market_lineage,
            "production_lane": project.production_lane,
            "content_mode": project.content_mode,
            "series_plan_id": project.series_plan_id,
            "series_run_id": project.series_run_id,
            "episode_number": project.episode_number,
            "standalone_reason_code": project.standalone_reason_code,
            "publish_metadata_snapshot": data.publish_metadata_snapshot,
            "disclosure_snapshot": data.disclosure_snapshot,
            "materiality_policy_snapshot": data.materiality_policy_snapshot,
            "materiality_policy_hash": materiality_policy_hash,
        }
        candidate_hash = stable_hash(payload)
        candidate_hashes = _final_review_candidate_hash_candidates(
            payload=payload,
            target_market_lineage=data.target_market_lineage,
            allow_historical_native_payload=(
                data.ai_visual_production_run_id is None
                and data.ai_visual_asset_manifest_hash is None
                and data.ffmpeg_effect_plan_hash is None
                and data.supersedes_final_review_candidate_id is None
            ),
        )
        existing = self.session.scalar(
            select(FinalReviewCandidate).where(
                FinalReviewCandidate.candidate_hash.in_(tuple(candidate_hashes))
            )
        )
        if existing is not None:
            self._assert_candidate_scope(existing, project)
            return existing
        if run.final_review_candidate_id is not None:
            previous = self.session.get(
                FinalReviewCandidate, run.final_review_candidate_id
            )
            previous_decision = (
                self.session.scalar(
                    select(FinalVideoDecision).where(
                        FinalVideoDecision.final_review_candidate_id
                        == run.final_review_candidate_id
                    )
                )
                if previous is not None
                else None
            )
            if (
                previous is None
                or previous_decision is None
                or previous_decision.decision != "DO_NOT_UPLOAD"
                or (
                    previous.production_package_artifact_version_id
                    == package_version.id
                    and previous.production_package_hash == package_version.content_hash
                    and previous.final_media_ref_id == final_media.id
                    and previous.final_media_hash == final_media.checksum_sha256
                )
            ):
                raise ConflictError("FINAL_REVIEW_WORKFLOW_ALREADY_HAS_CANDIDATE")

        candidate = FinalReviewCandidate(
            workflow_run_id=run.id,
            company_id=project.company_id,
            channel_workspace_id=project.channel_workspace_id,
            video_project_id=project.id,
            channel_profile_version_id=project.channel_profile_version_id,
            policy_snapshot_id=project.policy_snapshot_id,
            production_package_artifact_version_id=package_version.id,
            production_package_hash=package_version.content_hash,
            production_readiness_receipt_artifact_version_id=readiness_version.id,
            production_readiness_receipt_hash=readiness_version.content_hash,
            canonical_media_timeline_ref=data.canonical_media_timeline_ref,
            canonical_media_timeline_hash=data.canonical_media_timeline_hash,
            ai_visual_production_run_id=data.ai_visual_production_run_id,
            ai_visual_asset_manifest_hash=data.ai_visual_asset_manifest_hash,
            ffmpeg_effect_plan_hash=data.ffmpeg_effect_plan_hash,
            supersedes_final_review_candidate_id=(
                data.supersedes_final_review_candidate_id
            ),
            native_render_plan_ref=data.native_render_plan_ref,
            native_render_plan_hash=data.native_render_plan_hash,
            render_output_ref=data.render_output_ref,
            render_output_checksum=data.render_output_checksum,
            technical_qc_receipt_ref=data.technical_qc_receipt_ref,
            technical_qc_receipt_hash=data.technical_qc_receipt_hash,
            creative_qc_receipt_ref=data.creative_qc_receipt_ref,
            creative_qc_receipt_hash=data.creative_qc_receipt_hash,
            archive_receipt_ref=data.archive_receipt_ref,
            archive_receipt_hash=data.archive_receipt_hash,
            archive_object_ref=data.archive_object_ref,
            archive_verification_state="VERIFIED",
            final_media_ref_id=final_media.id,
            final_media_hash=final_media.checksum_sha256,
            destination_binding_id=data.destination_binding_id,
            destination_binding_fingerprint=data.destination_binding_fingerprint,
            destination_platform_channel_id=data.destination_platform_channel_id,
            destination_account_identity=data.destination_account_identity,
            target_platform=data.target_platform,
            target_surface=data.target_surface,
            target_market_lineage=target_market_lineage,
            production_lane=project.production_lane,
            content_mode=project.content_mode,
            series_plan_id=project.series_plan_id,
            series_run_id=project.series_run_id,
            episode_number=project.episode_number,
            standalone_reason_code=project.standalone_reason_code,
            publish_metadata_snapshot=dict(data.publish_metadata_snapshot),
            disclosure_snapshot=dict(data.disclosure_snapshot),
            materiality_policy_snapshot=dict(data.materiality_policy_snapshot),
            materiality_policy_hash=materiality_policy_hash,
            candidate_hash=candidate_hash,
        )
        self.session.add(candidate)
        self.session.flush()
        run.final_review_candidate_id = candidate.id
        run.final_review_candidate_hash = candidate.candidate_hash
        run.last_progress_at = utc_now()
        self.session.flush()
        from app.services.youtube_delivery import TelegramDeliveryService

        TelegramDeliveryService(self.session).prepare(
            candidate_id=candidate.id,
            notification_kind="FINAL_REVIEW_READY",
        )
        self.session.flush()
        return candidate

    def require_candidate(
        self,
        candidate_id: uuid.UUID,
        *,
        actor: ActorContext,
    ) -> FinalReviewCandidate:
        candidate = self.session.get(FinalReviewCandidate, candidate_id)
        if candidate is None or candidate.production_lane != "LONG_FORM":
            raise NotFoundError(f"final review candidate not found: {candidate_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=candidate.company_id,
        )
        return candidate

    # ------------------------------------------------------------------
    # Sole human content decision.
    # ------------------------------------------------------------------
    def decide(
        self,
        *,
        candidate_id: uuid.UUID,
        data: FinalVideoDecisionCreate,
        actor: ActorContext,
    ) -> FinalVideoDecisionResult:
        self._require_human_permission(actor, "review.final_decide")
        candidate = self.session.scalar(
            select(FinalReviewCandidate)
            .where(FinalReviewCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            raise NotFoundError(f"final review candidate not found: {candidate_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="review.final_decide",
            company_id=candidate.company_id,
        )
        self._assert_candidate_current(candidate)
        if data.decision == FinalVideoDecisionValue.UPLOAD:
            self._require_candidate_publish_enabled(candidate)

        decision_payload = {
            "schema_version": "vcos.final-video-decision.v2",
            "candidate_id": candidate.id,
            "candidate_hash": candidate.candidate_hash,
            "decision": data.decision.value,
            "operator_user_id": actor.actor_id,
            "authenticated_actor_role": actor.actor_role,
            "final_media_ref_id": candidate.final_media_ref_id,
            "final_media_hash": candidate.final_media_hash,
            "production_package_artifact_version_id": (
                candidate.production_package_artifact_version_id
            ),
            "production_package_hash": candidate.production_package_hash,
            "destination_binding_id": candidate.destination_binding_id,
            "destination_binding_fingerprint": (
                candidate.destination_binding_fingerprint
            ),
            "command_id": data.command_id,
            "reason": data.reason,
            "warnings_acknowledged": sorted(set(data.warnings_acknowledged)),
        }
        decision_hash = stable_hash(decision_payload)
        command_match = self.session.scalar(
            select(FinalVideoDecision).where(
                FinalVideoDecision.command_id == data.command_id
            )
        )
        if command_match is not None:
            if (
                command_match.final_review_candidate_id != candidate.id
                or command_match.decision_hash != decision_hash
            ):
                raise ConflictError("FINAL_DECISION_COMMAND_REUSE_CONFLICT")
            return self._decision_result(command_match)

        existing = self.session.scalar(
            select(FinalVideoDecision).where(
                FinalVideoDecision.final_review_candidate_id == candidate.id
            )
        )
        if existing is not None:
            if existing.decision_hash != decision_hash:
                raise ConflictError("FINAL_VIDEO_DECISION_TERMINAL")
            return self._decision_result(existing)

        now = utc_now()
        decision = FinalVideoDecision(
            final_review_candidate_id=candidate.id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            decision=data.decision.value,
            operator_user_id=actor.actor_id,
            authenticated_actor_role=actor.actor_role,
            final_media_ref_id=candidate.final_media_ref_id,
            final_media_hash=candidate.final_media_hash,
            production_package_artifact_version_id=(
                candidate.production_package_artifact_version_id
            ),
            production_package_hash=candidate.production_package_hash,
            destination_binding_id=candidate.destination_binding_id,
            destination_binding_fingerprint=(candidate.destination_binding_fingerprint),
            command_id=data.command_id,
            decision_timestamp=now,
            reason=data.reason,
            warnings_acknowledged=sorted(set(data.warnings_acknowledged)),
            decision_hash=decision_hash,
        )
        self.session.add(decision)
        self.session.flush()

        if data.decision == FinalVideoDecisionValue.UPLOAD:
            self._create_upload_task_once(
                candidate=candidate,
                decision=decision,
                actor=actor,
            )
        self.session.flush()
        return self._decision_result(decision)

    def require_decision(
        self,
        decision_id: uuid.UUID,
        *,
        actor: ActorContext,
    ) -> FinalVideoDecision:
        decision = self.session.get(FinalVideoDecision, decision_id)
        if decision is None:
            raise NotFoundError(f"final video decision not found: {decision_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=decision.company_id,
        )
        return decision

    # ------------------------------------------------------------------
    # File-selection attestation and task lifecycle.
    # ------------------------------------------------------------------
    def start_upload_task(
        self,
        *,
        task_id: uuid.UUID,
        data: HumanUploadTaskStartV2,
        actor: ActorContext,
    ) -> HumanUploadTask:
        self._require_human_permission(actor, "publish.prepare")
        task = self._require_v2_task(task_id, for_update=True)
        require_company_permission(
            self.session,
            actor=actor,
            permission="publish.prepare",
            company_id=task.company_id,
        )
        self._require_task_publish_enabled(task)
        if task.task_state == "CANCELED":
            raise ConflictError("UPLOAD_TASK_CANCELED")
        if task.task_state == "VERIFIED":
            return task
        if self._candidate_requires_private_stage(candidate=None, task=task):
            raise ValidationFailureError(
                "YOUTUBE_PRIVATE_STAGE_FILE_ATTESTATION_NOT_APPLICABLE"
            )

        expected_refs = {task.final_media_file_ref, task.archive_object_ref}
        if data.selected_file_ref not in expected_refs:
            raise ValidationFailureError("SELECTED_FILE_REF_MISMATCH")
        if data.archive_object_ref != task.archive_object_ref:
            raise ValidationFailureError("ARCHIVE_OBJECT_REF_MISMATCH")
        if data.selected_file_checksum != task.reviewed_checksum:
            raise ValidationFailureError("SELECTED_FILE_CHECKSUM_MISMATCH")
        expected_name = _file_name(data.selected_file_ref)
        if expected_name and data.selected_file_name != expected_name:
            raise ValidationFailureError("SELECTED_FILE_NAME_MISMATCH")

        if task.task_state in {"IN_PROGRESS", "AWAITING_CONFIRMATION"}:
            if (
                task.selected_file_name != data.selected_file_name
                or task.selected_file_ref != data.selected_file_ref
                or task.selected_file_checksum != data.selected_file_checksum
                or task.attested_by_user_id != actor.actor_id
            ):
                raise ConflictError("UPLOAD_TASK_ATTESTATION_IMMUTABLE")
            return task
        if task.task_state != "READY_FOR_OPERATOR":
            raise ConflictError(f"UPLOAD_TASK_NOT_STARTABLE:{task.task_state}")

        now = utc_now()
        task.selected_file_name = data.selected_file_name
        task.selected_file_ref = data.selected_file_ref
        task.selected_file_checksum = data.selected_file_checksum
        task.attested_by_user_id = actor.actor_id
        task.attested_at = now
        task.started_by_user_id = actor.actor_id
        task.started_at = now
        task.task_state = "IN_PROGRESS"
        task.blocked_reason = None
        self.session.flush()
        return task

    def cancel_upload_task(
        self,
        *,
        task_id: uuid.UUID,
        data: HumanUploadTaskCancelV2,
        actor: ActorContext,
    ) -> HumanUploadTask:
        self._require_human_permission(actor, "publish.confirm")
        task = self._require_v2_task(task_id, for_update=True)
        require_company_permission(
            self.session,
            actor=actor,
            permission="publish.confirm",
            company_id=task.company_id,
        )
        if task.task_state == "VERIFIED":
            raise ConflictError("VERIFIED_UPLOAD_TASK_TERMINAL")
        if task.task_state == "CANCELED":
            if task.cancel_command_id != data.command_id:
                raise ConflictError("UPLOAD_TASK_ALREADY_CANCELED")
            return task
        command_match = self.session.scalar(
            select(HumanUploadTask).where(
                HumanUploadTask.cancel_command_id == data.command_id
            )
        )
        if command_match is not None and command_match.id != task.id:
            raise ConflictError("UPLOAD_TASK_CANCEL_COMMAND_REUSE_CONFLICT")

        now = utc_now()
        task.task_state = "CANCELED"
        task.cancel_command_id = data.command_id
        task.canceled_by_user_id = actor.actor_id
        task.canceled_at = now
        task.completed_at = now
        task.blocked_reason = data.reason
        confirmation = self.session.scalar(
            select(ManualPublishConfirmation).where(
                ManualPublishConfirmation.human_upload_task_id == task.id
            )
        )
        if confirmation is not None and confirmation.confirmation_state != "VERIFIED":
            confirmation.confirmation_state = "CANCELED"
            confirmation.canceled_by_user_id = actor.actor_id
            confirmation.canceled_at = now
            confirmation.reason_codes = ["UPLOAD_TASK_CANCELED"]
            confirmation.next_action = None
        self.session.flush()
        return task

    def require_upload_task(
        self,
        task_id: uuid.UUID,
        *,
        actor: ActorContext,
    ) -> HumanUploadTask:
        task = self._require_v2_task(task_id, for_update=False)
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=task.company_id,
        )
        return task

    # ------------------------------------------------------------------
    # Manual confirmation, deterministic mismatch states, correction.
    # ------------------------------------------------------------------
    def submit_confirmation(
        self,
        *,
        task_id: uuid.UUID,
        data: ManualPublishConfirmationCreateV2,
        actor: ActorContext,
    ) -> ManualPublishConfirmation:
        self._require_human_permission(actor, "publish.confirm")
        task = self._require_v2_task(task_id, for_update=True)
        require_company_permission(
            self.session,
            actor=actor,
            permission="publish.confirm",
            company_id=task.company_id,
        )
        self._require_task_publish_enabled(task)
        if task.task_state == "CANCELED":
            raise ConflictError("UPLOAD_TASK_CANCELED")
        private_stage_required = self._candidate_requires_private_stage(
            candidate=None,
            task=task,
        )
        if task.task_state == "READY_FOR_OPERATOR":
            raise ValidationFailureError("FILE_SELECTION_ATTESTATION_REQUIRED")
        if task.task_state == "VERIFIED":
            existing = self._confirmation_for_task(task.id)
            if existing is None:
                raise ConflictError("VERIFIED_TASK_CONFIRMATION_MISSING")
            return existing

        candidate, decision, final_media = self._task_lineage(task)
        if private_stage_required:
            from app.db.models.youtube_delivery import YouTubePrivateStage

            stage = self.session.scalar(
                select(YouTubePrivateStage).where(
                    YouTubePrivateStage.final_video_decision_id == decision.id
                )
            )
            if (
                stage is None
                or stage.state != "PRIVATE_VERIFIED"
                or stage.platform_video_id != data.platform_video_id
                or stage.public_release_expectation.get("platform_channel_id")
                != data.platform_channel_id
                or task.task_state != "AWAITING_CONFIRMATION"
            ):
                raise ValidationFailureError(
                    "YOUTUBE_PRIVATE_STAGE_PUBLIC_RELEASE_NOT_READY"
                )
        payload = self._confirmation_payload(task=task, data=data, actor=actor)
        confirmation_hash = stable_hash(payload)
        command_match = self.session.scalar(
            select(ManualPublishConfirmation).where(
                ManualPublishConfirmation.command_id == data.command_id
            )
        )
        if command_match is not None:
            if (
                command_match.human_upload_task_id != task.id
                or command_match.confirmation_hash != confirmation_hash
            ):
                raise ConflictError("PUBLISH_CONFIRMATION_COMMAND_REUSE_CONFLICT")
            return command_match
        existing = self._confirmation_for_task(task.id)
        if existing is not None:
            raise ConflictError("PUBLISH_CONFIRMATION_ALREADY_EXISTS")

        classification = self._classify_confirmation(
            candidate=candidate,
            final_media=final_media,
            data=data,
        )
        now = utc_now()
        confirmation = ManualPublishConfirmation(
            publish_handoff_package_id=task.publish_package_id,
            company_id=task.company_id,
            channel_workspace_id=task.channel_workspace_id,
            video_project_id=task.video_project_id,
            policy_snapshot_id=task.policy_snapshot_id,
            target_platform=candidate.target_platform,
            target_surface=candidate.target_surface,
            confirmed_by_user_id=actor.actor_id,
            confirmation_state=classification["state"],
            actual_video_id=data.platform_video_id,
            actual_video_url=data.video_url,
            actual_published_at=data.published_at,
            destination_binding_id=data.destination_binding_id,
            destination_binding_fingerprint=data.destination_binding_fingerprint,
            market_policy_hash=_market_hash(candidate.target_market_lineage),
            approved_package_hash=task.production_package_hash,
            actual_metadata=_actual_metadata(data),
            actual_disclosures=dict(data.disclosures),
            actual_files={
                "selected_file_name": task.selected_file_name,
                "selected_file_ref": task.selected_file_ref,
                "selected_file_checksum": task.selected_file_checksum,
                "archive_object_ref": task.archive_object_ref,
            },
            operator_notes=data.operator_notes,
            validation_summary=classification["validation_summary"],
            metadata_diff=classification["metadata_diff"],
            reason_codes=classification["reason_codes"],
            next_action=classification["next_action"],
            schema_version="v2",
            command_id=data.command_id,
            confirmation_hash=confirmation_hash,
            human_upload_task_id=task.id,
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            final_media_ref_id=final_media.id,
            reviewed_checksum=task.reviewed_checksum,
            production_package_artifact_version_id=(
                task.production_package_artifact_version_id
            ),
            production_package_hash=task.production_package_hash,
            channel_profile_version_id=task.channel_profile_version_id,
            platform_channel_id=data.platform_channel_id,
            destination_account_identity=data.destination_account_identity,
            actual_duration_seconds=data.duration_seconds,
            thumbnail_confirmed=data.thumbnail_confirmed,
            caption_confirmed=data.caption_confirmed,
            playlist_id=data.playlist_id,
            playlist_order=data.playlist_order,
            materiality_policy_hash=candidate.materiality_policy_hash,
            variance_attested_by_user_id=(
                actor.actor_id
                if classification["state"] == "VARIANCE_ACCEPTED"
                else None
            ),
            variance_attested_at=(
                now if classification["state"] == "VARIANCE_ACCEPTED" else None
            ),
        )
        self.session.add(confirmation)
        task.task_state = "AWAITING_CONFIRMATION"
        task.blocked_reason = (
            ",".join(classification["reason_codes"])
            if classification["state"]
            in {"BLOCKED_DESTINATION", "REJECTED_MISMATCH", "CORRECTION_REQUIRED"}
            else None
        )
        self.session.flush()
        return confirmation

    def apply_correction(
        self,
        *,
        confirmation_id: uuid.UUID,
        data: ManualPublishCorrectionV2,
        actor: ActorContext,
    ) -> ManualPublishConfirmation:
        self._require_human_permission(actor, "publish.confirm")
        confirmation = self._require_v2_confirmation(confirmation_id, for_update=True)
        require_company_permission(
            self.session,
            actor=actor,
            permission="publish.confirm",
            company_id=confirmation.company_id,
        )
        if confirmation.confirmation_state in {"VERIFIED", "CANCELED"}:
            raise ConflictError(
                f"PUBLISH_CONFIRMATION_TERMINAL:{confirmation.confirmation_state}"
            )
        history = list(confirmation.correction_history or [])
        for item in history:
            if item.get("command_id") == str(data.correction_command_id):
                return confirmation

        task = self._require_v2_task(confirmation.human_upload_task_id, for_update=True)
        candidate, _decision, final_media = self._task_lineage(task)
        self._require_candidate_publish_enabled(candidate)
        classification = self._classify_confirmation(
            candidate=candidate,
            final_media=final_media,
            data=data,
        )
        before_hash = confirmation.confirmation_hash
        corrected_payload = self._confirmation_payload(
            task=task,
            data=data,
            actor=actor,
            command_id=data.correction_command_id,
        )
        corrected_hash = stable_hash(corrected_payload)
        now = utc_now()
        history.append(
            {
                "command_id": str(data.correction_command_id),
                "actor_id": str(actor.actor_id),
                "actor_role": actor.actor_role,
                "corrected_at": now.isoformat(),
                "before_hash": before_hash,
                "after_hash": corrected_hash,
                "resulting_state": classification["state"],
            }
        )
        self._apply_confirmation_values(
            confirmation=confirmation,
            data=data,
            classification=classification,
        )
        confirmation.confirmation_hash = corrected_hash
        confirmation.corrected_by_user_id = actor.actor_id
        confirmation.corrected_at = now
        confirmation.correction_history = history
        if classification["state"] == "VARIANCE_ACCEPTED":
            confirmation.variance_attested_by_user_id = actor.actor_id
            confirmation.variance_attested_at = now
        else:
            confirmation.variance_attested_by_user_id = None
            confirmation.variance_attested_at = None
        task.blocked_reason = (
            ",".join(classification["reason_codes"])
            if classification["state"]
            in {"BLOCKED_DESTINATION", "REJECTED_MISMATCH", "CORRECTION_REQUIRED"}
            else None
        )
        self.session.flush()
        return confirmation

    def require_confirmation(
        self,
        confirmation_id: uuid.UUID,
        *,
        actor: ActorContext,
    ) -> ManualPublishConfirmation:
        confirmation = self._require_v2_confirmation(
            confirmation_id,
            for_update=False,
        )
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=confirmation.company_id,
        )
        return confirmation

    # ------------------------------------------------------------------
    # Deterministic observable verification and exactly-once effects.
    # ------------------------------------------------------------------
    def verify_confirmation(
        self,
        *,
        confirmation_id: uuid.UUID,
        data: ManualPublishVerificationV2,
        actor: ActorContext,
    ) -> ManualPublishVerificationResultV2:
        self._require_human_permission(actor, "publish.confirm")
        confirmation = self._require_v2_confirmation(confirmation_id, for_update=True)
        require_company_permission(
            self.session,
            actor=actor,
            permission="publish.confirm",
            company_id=confirmation.company_id,
        )
        existing_uploaded = self.session.scalar(
            select(UploadedVideo).where(
                UploadedVideo.manual_publish_confirmation_id == confirmation.id
            )
        )
        if existing_uploaded is not None:
            return self._verification_result(
                confirmation=confirmation,
                uploaded=existing_uploaded,
            )
        if confirmation.confirmation_state not in {
            "SUBMITTED",
            "VARIANCE_ACCEPTED",
        }:
            raise ConflictError(
                f"PUBLISH_CONFIRMATION_NOT_VERIFIABLE:{confirmation.confirmation_state}"
            )
        if (
            data.observed_privacy_status != "PUBLIC"
            or str((confirmation.actual_metadata or {}).get("privacy_status") or "").upper()
            != "PUBLIC"
        ):
            raise ValidationFailureError(
                "PUBLICATION_VERIFICATION_REQUIRES_PUBLIC_VISIBILITY"
            )

        task = self._require_v2_task(confirmation.human_upload_task_id, for_update=True)
        candidate, decision, final_media = self._task_lineage(task)
        self._require_candidate_publish_enabled(candidate)
        self._assert_confirmation_lineage(
            confirmation=confirmation,
            task=task,
            candidate=candidate,
            decision=decision,
            final_media=final_media,
        )
        strategic_lineage = self._require_uploaded_video_strategic_lineage(
            candidate=candidate,
        )
        verification_evidence_hash = stable_hash(
            _verification_evidence_payload(
                confirmation=confirmation,
                task=task,
                final_media=final_media,
                data=data,
            )
        )
        mismatch = self._classify_observation(confirmation=confirmation, data=data)
        if mismatch is not None:
            confirmation.confirmation_state = mismatch["state"]
            confirmation.reason_codes = mismatch["reason_codes"]
            confirmation.next_action = mismatch["next_action"]
            confirmation.validation_summary = {
                **dict(confirmation.validation_summary or {}),
                "verification_observation": mismatch,
            }
            task.blocked_reason = ",".join(mismatch["reason_codes"])
            self.session.flush()
            return ManualPublishVerificationResultV2(
                status=mismatch["state"],
                confirmation=ManualPublishConfirmationReadV2.model_validate(
                    confirmation
                ),
                uploaded_video=None,
            )

        verification_command_match = self.session.scalar(
            select(ManualPublishConfirmation).where(
                ManualPublishConfirmation.verification_command_id
                == data.verification_command_id
            )
        )
        if (
            verification_command_match is not None
            and verification_command_match.id != confirmation.id
        ):
            raise ConflictError("PUBLISH_VERIFICATION_COMMAND_REUSE_CONFLICT")

        now = utc_now()
        confirmation.confirmation_state = "VERIFIED"
        confirmation.verified_by_user_id = actor.actor_id
        confirmation.verified_at = now
        confirmation.verification_command_id = data.verification_command_id
        confirmation.verification_evidence_ref = data.verification_evidence_ref
        confirmation.verification_evidence_hash = verification_evidence_hash
        confirmation.reason_codes = []
        confirmation.next_action = None

        observed_public_metadata = {
            "title": data.observed_title,
            "description": data.observed_description,
            "privacy_status": data.observed_privacy_status,
            "duration_seconds": str(data.observed_duration_seconds),
            "platform": data.observed_platform,
            "platform_channel_id": data.observed_platform_channel_id,
            "platform_video_id": data.observed_platform_video_id,
            "tags": data.observed_tags,
            "category_id": data.observed_category_id,
            "default_language": data.observed_default_language,
            "made_for_kids": data.observed_made_for_kids,
            "contains_synthetic_media": data.observed_contains_synthetic_media,
            "thumbnail_confirmed": data.observed_thumbnail_confirmed,
            "caption_confirmed": data.observed_caption_confirmed,
        }
        from app.services.youtube_delivery import YouTubeDeliveryService

        public_receipt = YouTubeDeliveryService(self.session).create_publication_receipt(
            candidate=candidate,
            decision=decision,
            confirmation=confirmation,
            observed_metadata=observed_public_metadata,
            observed_platform_channel_id=data.observed_platform_channel_id,
            observed_platform_video_id=data.observed_platform_video_id,
            observed_video_url=data.observed_video_url,
            observed_published_at=data.observed_published_at,
            verification_evidence_ref=data.verification_evidence_ref,
            verification_evidence_hash=verification_evidence_hash,
        )

        uploaded_id = uuid.uuid4()
        verified_event_id = _event_id(uploaded_id, "UPLOADED_VIDEO_VERIFIED")
        analytics_event_id = _event_id(uploaded_id, "ANALYTICS_READY")
        archive_supplement = self._archive_supplement(
            uploaded_id=uploaded_id,
            candidate=candidate,
            decision=decision,
            task=task,
            confirmation=confirmation,
            verification=data,
            verification_evidence_hash=verification_evidence_hash,
        )
        archive_supplement_hash = stable_hash(archive_supplement)
        archive_supplement_ref = (
            f"archive-supplement://uploaded-video/{uploaded_id}/"
            f"{archive_supplement_hash}"
        )
        event_payload = {
            "uploaded_video_id": str(uploaded_id),
            "video_project_id": str(candidate.video_project_id),
            "final_media_ref_id": str(candidate.final_media_ref_id),
            "final_video_decision_id": str(decision.id),
            "human_upload_task_id": str(task.id),
            "manual_publish_confirmation_id": str(confirmation.id),
            "public_publication_receipt_id": str(public_receipt.id),
            "public_publication_receipt_hash": public_receipt.receipt_hash,
            "production_package_artifact_version_id": str(
                candidate.production_package_artifact_version_id
            ),
            "production_package_hash": candidate.production_package_hash,
            "destination_binding_id": str(candidate.destination_binding_id),
            "destination_binding_fingerprint": (
                candidate.destination_binding_fingerprint
            ),
            "production_lane": candidate.production_lane,
            "content_mode": candidate.content_mode,
            "series_plan_id": _optional_uuid(candidate.series_plan_id),
            "series_run_id": _optional_uuid(candidate.series_run_id),
            "episode_number": candidate.episode_number,
        }
        if strategic_lineage is not None:
            # This is intentionally projected from the sealed package only.
            # Neither the confirmation nor the verification command carries
            # caller-authored strategic intent.
            event_payload["strategic_lineage"] = strategic_lineage
        self._append_event_once(
            event_id=verified_event_id,
            event_type="UPLOADED_VIDEO_VERIFIED",
            aggregate_id=uploaded_id,
            company_id=candidate.company_id,
            correlation_id=str(data.verification_command_id),
            causation_id=confirmation.id,
            payload=event_payload,
        )
        self._append_event_once(
            event_id=analytics_event_id,
            event_type="ANALYTICS_READY",
            aggregate_id=uploaded_id,
            company_id=candidate.company_id,
            correlation_id=str(data.verification_command_id),
            causation_id=verified_event_id,
            payload=event_payload,
        )

        actual_metadata = dict(confirmation.actual_metadata or {})
        uploaded = UploadedVideo(
            id=uploaded_id,
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            policy_snapshot_id=candidate.policy_snapshot_id,
            publish_handoff_package_id=confirmation.publish_handoff_package_id,
            manual_publish_confirmation_id=confirmation.id,
            render_package_snapshot_id=None,
            first_scripted_video_package_id=None,
            human_upload_task_id=task.id,
            destination=candidate.target_platform,
            destination_binding_id=candidate.destination_binding_id,
            destination_binding_fingerprint=(candidate.destination_binding_fingerprint),
            market_policy_hash=_market_hash(candidate.target_market_lineage),
            approved_package_hash=candidate.production_package_hash,
            source_manifest_snapshot_id=None,
            rights_envelope_ref=_rights_ref(candidate.target_market_lineage),
            platform=confirmation.target_platform,
            platform_video_id=confirmation.actual_video_id,
            video_url=confirmation.actual_video_url,
            published_at=confirmation.actual_published_at,
            publish_status="CONFIRMED",
            actual_metadata=actual_metadata,
            actual_disclosures=dict(confirmation.actual_disclosures or {}),
            lineage_refs=event_payload,
            monitoring_state="NOT_STARTED",
            operator_summary={
                "status": "VERIFIED",
                "next_action": "ANALYTICS_READY",
                "verification_evidence_ref": data.verification_evidence_ref,
            },
            actual_title=actual_metadata.get("title"),
            actual_visibility=actual_metadata.get("privacy_status", "UNKNOWN"),
            actual_publish_time=confirmation.actual_published_at,
            actual_upload_time=confirmation.created_at,
            playlist_id=confirmation.playlist_id,
            thumbnail_uploaded=confirmation.thumbnail_confirmed,
            subtitles_uploaded=confirmation.caption_confirmed,
            description_modified_from_package=bool(
                (confirmation.metadata_diff or {}).get("description")
            ),
            package_metadata_diff=dict(confirmation.metadata_diff or {}),
            verification_status="VERIFIED",
            analytics_sync_status="READY",
            last_verified_at=now,
            operator_note=confirmation.operator_notes,
            schema_version="v3",
            public_publication_receipt_id=public_receipt.id,
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            final_media_ref_id=final_media.id,
            production_package_artifact_version_id=(
                candidate.production_package_artifact_version_id
            ),
            production_package_hash=candidate.production_package_hash,
            channel_profile_version_id=candidate.channel_profile_version_id,
            reviewed_checksum=candidate.final_media_hash,
            production_lane=candidate.production_lane,
            content_mode=candidate.content_mode,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            episode_number=candidate.episode_number,
            standalone_reason_code=candidate.standalone_reason_code,
            target_market_lineage=dict(candidate.target_market_lineage),
            archive_supplement=archive_supplement,
            archive_supplement_ref=archive_supplement_ref,
            archive_supplement_hash=archive_supplement_hash,
            verified_event_id=verified_event_id,
            analytics_ready_event_id=analytics_event_id,
            analytics_ready_at=now,
        )
        self.session.add(uploaded)
        self.session.flush()
        LongFormAnalyticsScheduler(self.session).schedule_uploaded_video(uploaded.id)
        task.actual_uploaded_video_id = uploaded.id
        task.task_state = "VERIFIED"
        task.completed_at = now
        task.blocked_reason = None
        if final_media.uploaded_video_id not in {None, uploaded.id}:
            raise ValidationFailureError("FINAL_MEDIA_ALREADY_BOUND_TO_OTHER_UPLOAD")
        final_media.uploaded_video_id = uploaded.id
        self._advance_series_after_verified(
            candidate=candidate,
            decision=decision,
            task=task,
            confirmation=confirmation,
            uploaded=uploaded,
        )
        self.session.flush()
        return self._verification_result(
            confirmation=confirmation,
            uploaded=uploaded,
        )

    def require_uploaded_video(
        self,
        uploaded_id: uuid.UUID,
        *,
        actor: ActorContext,
    ) -> UploadedVideo:
        uploaded = self.session.get(UploadedVideo, uploaded_id)
        if uploaded is None or uploaded.schema_version not in {"v2", "v3"}:
            raise NotFoundError(f"canonical uploaded video not found: {uploaded_id}")
        require_company_permission(
            self.session,
            actor=actor,
            permission="production.read",
            company_id=uploaded.company_id,
        )
        return uploaded

    def resolve_verified_candidate_media(
        self,
        *,
        candidate_id: uuid.UUID,
        actor: ActorContext,
        media_kind: str = "video",
    ) -> VerifiedCandidateMedia:
        """Resolve only a checksum-verified local archive object.

        The public API supplies a candidate ID, never a path. Every database
        authority and the bytes on disk must agree before a path is returned.
        """

        candidate = self.require_candidate(candidate_id, actor=actor)
        self._assert_candidate_current(candidate)
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        run = self.session.get(ProductionWorkflowRun, candidate.workflow_run_id)
        cloud = (
            self.session.get(CloudMediaRef, final_media.cloud_media_ref_id)
            if final_media is not None and final_media.cloud_media_ref_id is not None
            else None
        )
        checksum = candidate.render_output_checksum
        expected_ref = (
            f"vcos-local-archive://{candidate.video_project_id}/{checksum}/final.mp4"
        )
        appendix = dict(cloud.technical_appendix or {}) if cloud is not None else {}
        if (
            final_media is None
            or run is None
            or cloud is None
            or candidate.archive_verification_state != "VERIFIED"
            or candidate.archive_object_ref != expected_ref
            or final_media.company_id != candidate.company_id
            or final_media.channel_workspace_id != candidate.channel_workspace_id
            or final_media.video_project_id != candidate.video_project_id
            or final_media.file_ref != expected_ref
            or final_media.checksum_sha256 != checksum
            or cloud.company_id != candidate.company_id
            or cloud.channel_workspace_id != candidate.channel_workspace_id
            or cloud.video_project_id != candidate.video_project_id
            or cloud.storage_provider != "VCOS_LOCAL_ARCHIVE"
            or cloud.web_view_link != expected_ref
            or cloud.mime_type != "video/mp4"
            or cloud.checksum_sha256 != checksum
            or cloud.upload_status != "VERIFIED"
            or cloud.verification_status != "CHECKSUM_VERIFIED"
            or appendix.get("readback_checksum") != checksum
            or appendix.get("archive_receipt_hash") != candidate.archive_receipt_hash
            or run.company_id != candidate.company_id
            or run.channel_workspace_id != candidate.channel_workspace_id
            or run.video_project_id != candidate.video_project_id
            or run.archive_object_ref != expected_ref
            or run.archive_verification_state != "VERIFIED"
            or run.archive_receipt_hash != candidate.archive_receipt_hash
            or run.final_media_ref_id != final_media.id
            or run.final_media_ref_hash != checksum
        ):
            raise ValidationFailureError("VERIFIED_LOCAL_ARCHIVE_AUTHORITY_MISMATCH")

        root = _v2_production_root()
        archive_dir = root / "archive" / str(candidate.video_project_id)
        if media_kind == "video":
            target = archive_dir / f"{checksum}.mp4"
            expected_checksum = checksum
            media_type = "video/mp4"
            file_name = "final.mp4"
        elif media_kind == "thumbnail":
            relative_ref = f"archive/{candidate.video_project_id}/{checksum}.jpg"
            expected_checksum = appendix.get("thumbnail_checksum")
            if appendix.get("thumbnail_relative_ref") != relative_ref or not _is_sha256(
                expected_checksum
            ):
                raise NotFoundError(
                    f"verified candidate thumbnail not found: {candidate_id}"
                )
            expected_checksum = str(expected_checksum)
            target = archive_dir / f"{checksum}.jpg"
            media_type = "image/jpeg"
            file_name = "thumbnail.jpg"
        else:
            raise NotFoundError(f"candidate media not found: {candidate_id}")

        resolved = _resolve_verified_archive_file(
            root=root,
            target=target,
            expected_checksum=expected_checksum,
        )
        if media_kind == "video" and (
            cloud.size_bytes is not None and resolved.stat().st_size != cloud.size_bytes
        ):
            raise ValidationFailureError("VERIFIED_LOCAL_ARCHIVE_SIZE_MISMATCH")
        return VerifiedCandidateMedia(
            path=resolved,
            file_name=file_name,
            media_type=media_type,
            checksum_sha256=expected_checksum,
        )

    # ------------------------------------------------------------------
    # Internal validators and exactly-once helpers.
    # ------------------------------------------------------------------
    def _create_upload_task_once(
        self,
        *,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        actor: ActorContext,
    ) -> HumanUploadTask:
        if decision.decision != "UPLOAD":
            raise ValidationFailureError("UPLOAD_TASK_REQUIRES_UPLOAD_DECISION")
        self._require_candidate_publish_enabled(candidate)
        existing = self.session.scalar(
            select(HumanUploadTask).where(
                HumanUploadTask.final_video_decision_id == decision.id
            )
        )
        if existing is not None:
            return existing
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        if (
            final_media is None
            or final_media.file_ref != candidate.archive_object_ref
            or final_media.checksum_sha256 != candidate.final_media_hash
        ):
            raise ValidationFailureError("UPLOAD_TASK_FINAL_MEDIA_AUTHORITY_MISMATCH")
        private_stage_required = self._candidate_requires_private_stage(
            candidate=candidate,
            task=None,
        )
        now = utc_now()
        task = HumanUploadTask(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            first_scripted_video_package_id=None,
            publish_package_id=None,
            destination=candidate.target_platform,
            target_platform=candidate.target_platform,
            task_state="IN_PROGRESS" if private_stage_required else "READY_FOR_OPERATOR",
            publish_metadata_ref=(
                f"final_review_candidate:{candidate.id}:publish_metadata_snapshot"
            ),
            title_snapshot=str(candidate.publish_metadata_snapshot.get("title") or ""),
            description_snapshot=candidate.publish_metadata_snapshot.get("description"),
            thumbnail_ref=candidate.publish_metadata_snapshot.get("thumbnail_ref"),
            subtitle_refs=list(
                candidate.publish_metadata_snapshot.get("subtitle_refs") or []
            ),
            required_assets=[
                {
                    "type": "FINAL_MEDIA",
                    "file_ref": final_media.file_ref,
                    "checksum_sha256": candidate.final_media_hash,
                    "archive_object_ref": candidate.archive_object_ref,
                }
            ],
            checklist=(
                [
                    {
                        "key": "YOUTUBE_PRIVATE_STAGE",
                        "required": True,
                        "state": "PENDING",
                    },
                    {
                        "key": "HUMAN_PUBLIC_RELEASE",
                        "required": True,
                        "auto_publish": False,
                    },
                    {
                        "key": "PUBLICATION_CONFIRMATION",
                        "required": True,
                    },
                ]
                if private_stage_required
                else [
                    {
                        "key": "SELECT_EXACT_FINAL_MEDIA",
                        "required": True,
                        "expected_checksum": candidate.final_media_hash,
                    },
                    {
                        "key": "UPLOAD_MANUALLY",
                        "required": True,
                        "auto_publish": False,
                    },
                ]
            ),
            required_checklist=(
                [
                    {
                        "key": "PRIVATE_STAGE_VERIFIED",
                        "required": True,
                    },
                    {
                        "key": "HUMAN_PUBLIC_RELEASE_CONFIRMATION",
                        "required": True,
                    },
                ]
                if private_stage_required
                else [
                    {
                        "key": "FILE_SELECTION_ATTESTATION",
                        "required": True,
                    }
                ]
            ),
            schema_version="v2",
            final_review_candidate_id=candidate.id,
            final_video_decision_id=decision.id,
            final_media_ref_id=candidate.final_media_ref_id,
            final_media_file_ref=final_media.file_ref,
            reviewed_checksum=candidate.final_media_hash,
            production_package_artifact_version_id=(
                candidate.production_package_artifact_version_id
            ),
            production_package_hash=candidate.production_package_hash,
            destination_binding_id=candidate.destination_binding_id,
            destination_binding_fingerprint=(candidate.destination_binding_fingerprint),
            channel_profile_version_id=candidate.channel_profile_version_id,
            policy_snapshot_id=candidate.policy_snapshot_id,
            production_lane=candidate.production_lane,
            content_mode=candidate.content_mode,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            episode_number=candidate.episode_number,
            standalone_reason_code=candidate.standalone_reason_code,
            archive_object_ref=candidate.archive_object_ref,
            selected_file_name=(
                _file_name(final_media.file_ref) if private_stage_required else None
            ),
            selected_file_ref=(final_media.file_ref if private_stage_required else None),
            selected_file_checksum=(
                candidate.final_media_hash if private_stage_required else None
            ),
            attested_by_user_id=(actor.actor_id if private_stage_required else None),
            attested_at=(now if private_stage_required else None),
            started_by_user_id=(actor.actor_id if private_stage_required else None),
            started_at=(now if private_stage_required else None),
            operator_note=(
                "VCOS will upload the exact reviewed file to YouTube PRIVATE. "
                "The operator retains the sole public-release decision."
                if private_stage_required
                else None
            ),
        )
        self.session.add(task)
        self.session.flush()
        return task

    def _candidate_requires_private_stage(
        self,
        *,
        candidate: FinalReviewCandidate | None,
        task: HumanUploadTask | None,
    ) -> bool:
        if candidate is None and task is None:
            raise TypeError("candidate or task is required")
        policy_snapshot_id = (
            candidate.policy_snapshot_id
            if candidate is not None
            else task.policy_snapshot_id
        )
        snapshot = self.session.get(CompiledChannelPolicySnapshot, policy_snapshot_id)
        try:
            policy = ChannelScopedPolicy.model_validate(
                (snapshot.compiled_payload or {})["channel_scoped_policy"]
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("PUBLISH_POLICY_AUTHORITY_INVALID") from exc
        return bool(policy.publish_policy.youtube_private_stage_required)

    def _decision_result(
        self, decision: FinalVideoDecision
    ) -> FinalVideoDecisionResult:
        task_id = self.session.scalar(
            select(HumanUploadTask.id).where(
                HumanUploadTask.final_video_decision_id == decision.id
            )
        )
        return FinalVideoDecisionResult(
            decision=decision,
            human_upload_task_id=task_id,
        )

    def _require_final_media_authority(
        self,
        *,
        final_media: FinalMediaRef,
        project: VideoProject,
        package_content: ProductionPackageContentV2,
        data: FinalReviewCandidateCreateV2,
    ) -> None:
        duration_contract = package_content.duration_contract.model_dump(mode="json")
        if (
            final_media.duration_contract != duration_contract
            or final_media.duration_seconds is None
            or not final_media.aspect_ratio
            or not final_media.resolution
            or final_media.cloud_media_ref_id is None
            or final_media.lineage_artifact_version_id is None
            or final_media.file_ref != data.archive_object_ref
        ):
            raise ValidationFailureError(
                "FINAL_REVIEW_FINAL_MEDIA_AUTHORITY_INCOMPLETE"
            )
        duration_ms = int(
            (Decimal(final_media.duration_seconds) * Decimal(1000)).to_integral_value()
        )
        if not (
            package_content.duration_contract.minimum_duration_ms
            <= duration_ms
            <= package_content.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError(
                "FINAL_REVIEW_FINAL_MEDIA_DURATION_OUTSIDE_CONTRACT"
            )

        cloud = self.session.get(CloudMediaRef, final_media.cloud_media_ref_id)
        cloud_appendix = cloud.technical_appendix if cloud is not None else {}
        parsed_file_ref = urlparse(final_media.file_ref)
        v2_drive_archive = bool(
            final_media.provider_key in _V2_DRIVE_ARCHIVE_ADAPTER_KEYS
            and final_media.provider_type == "MEDIA_STORAGE"
        )
        drive_binding_valid = bool(
            cloud is not None
            and cloud.storage_provider == "GOOGLE_DRIVE"
            and parsed_file_ref.scheme == "drive"
            and parsed_file_ref.netloc == cloud.drive_file_id
        )
        local_binding_valid = bool(
            cloud is not None
            and cloud.storage_provider == "VCOS_LOCAL_ARCHIVE"
            and parsed_file_ref.scheme == "vcos-local-archive"
            and parsed_file_ref.netloc == str(project.id)
            and cloud.drive_file_id == f"local-{final_media.checksum_sha256}"
            and cloud.web_view_link == final_media.file_ref
            and isinstance(cloud_appendix, dict)
            and cloud_appendix.get("readback_checksum") == final_media.checksum_sha256
            and isinstance(cloud_appendix.get("archive_journal_hash"), str)
            and len(cloud_appendix["archive_journal_hash"]) == 64
        )
        if (
            cloud is None
            or cloud.company_id != project.company_id
            or cloud.channel_workspace_id != project.channel_workspace_id
            or cloud.video_project_id != project.id
            or cloud.upload_status != "VERIFIED"
            or cloud.verification_status != "CHECKSUM_VERIFIED"
            or cloud.checksum_sha256 != final_media.checksum_sha256
            or not cloud.drive_file_id
            or not (
                drive_binding_valid
                if v2_drive_archive
                else (drive_binding_valid or local_binding_valid)
            )
            or (
                v2_drive_archive
                and (
                    not isinstance(cloud_appendix, dict)
                    or cloud_appendix.get("drive_file_id_verified") is not True
                    or cloud_appendix.get("size_verified") is not True
                    or cloud_appendix.get("checksum_verified") is not True
                    or cloud.mime_type != "video/mp4"
                    or not _v2_drive_web_view_matches_file_id(cloud)
                    or not _v2_drive_duration_matches(
                        cloud_appendix=cloud_appendix,
                        final_media=final_media,
                    )
                    or not _has_v2_drive_render_source(
                        cloud=cloud,
                        final_media=final_media,
                        expected_checksum=data.render_output_checksum,
                    )
                )
            )
            or (
                not v2_drive_archive
                and (
                    not isinstance(cloud_appendix, dict)
                    or cloud_appendix.get("archive_receipt_hash")
                    != data.archive_receipt_hash
                )
            )
        ):
            raise ValidationFailureError(
                "FINAL_REVIEW_CLOUD_MEDIA_NOT_CHECKSUM_VERIFIED"
            )

        if final_media.media_qc_report_id is not None:
            qc_report = self.session.get(MediaQCReport, final_media.media_qc_report_id)
            if (
                qc_report is None
                or qc_report.video_project_id != project.id
                or qc_report.qc_state != "PASS"
            ):
                raise ValidationFailureError("FINAL_REVIEW_MEDIA_QC_AUTHORITY_INVALID")

        lineage_row = self.session.execute(
            select(ArtifactVersion, Artifact)
            .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
            .where(ArtifactVersion.id == final_media.lineage_artifact_version_id)
        ).one_or_none()
        if lineage_row is None:
            raise ValidationFailureError("FINAL_REVIEW_FINAL_MEDIA_LINEAGE_MISSING")
        lineage, lineage_artifact = lineage_row
        lineage_content = lineage.content or {}
        expected_lineage = {
            "schema_version": (
                _V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA
                if v2_drive_archive
                else "vcos.native-final-media-lineage.v2"
            ),
            "video_project_id": str(project.id),
            "production_package_artifact_version_id": str(
                data.production_package_artifact_version_id
            ),
            "production_package_hash": data.production_package_hash,
            "duration_contract": duration_contract,
            "canonical_media_timeline_hash": data.canonical_media_timeline_hash,
            "native_render_plan_hash": data.native_render_plan_hash,
            "render_output_checksum": data.render_output_checksum,
            "technical_qc_hash": data.technical_qc_receipt_hash,
            "creative_qc_hash": data.creative_qc_receipt_hash,
            "archive_receipt_hash": data.archive_receipt_hash,
            "archive_state": "VERIFIED",
            "cloud_media_ref_id": str(cloud.id),
            (
                "archive_object_ref" if v2_drive_archive else "file_ref"
            ): final_media.file_ref,
        }
        if v2_drive_archive:
            expected_lineage.update(
                {
                    "render_output_ref": data.render_output_ref,
                    "measured_render_duration_ms": duration_ms,
                    "storage_provider": "GOOGLE_DRIVE",
                    "invokes_mr1": False,
                    "automatic_publish": False,
                }
            )
        if (
            lineage_artifact.artifact_type
            != (
                _V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE
                if v2_drive_archive
                else "mr1_final_media_lineage_receipt"
            )
            or lineage_artifact.video_project_id != project.id
            or lineage_artifact.current_version_id != lineage.id
            or lineage_artifact.status != "approved"
            or lineage.status != "approved"
            or content_hash(lineage_content) != lineage.content_hash
            or any(
                lineage_content.get(field) != expected
                for field, expected in expected_lineage.items()
            )
        ):
            raise ValidationFailureError("FINAL_REVIEW_FINAL_MEDIA_LINEAGE_MISMATCH")

    def _require_destination_binding_authority(
        self,
        *,
        project: VideoProject,
        package_content: ProductionPackageContentV2,
        data: FinalReviewCandidateCreateV2,
    ) -> dict[str, Any]:
        projection = self._load_destination_projection(
            project=project,
            package_content=package_content,
            destination_binding_id=data.destination_binding_id,
            destination_binding_fingerprint=data.destination_binding_fingerprint,
        )
        exact_lineage = _destination_lineage_projection(data.target_market_lineage)
        legacy_verified = data.target_market_lineage.get("destination_mode") is None
        if (
            (
                not legacy_verified
                and _destination_lineage_projection(projection) != exact_lineage
            )
            or (
                legacy_verified
                and (
                    projection["destination_mode"] != "VERIFIED_PUBLISH_DESTINATION"
                    or (
                        isinstance(
                            data.target_market_lineage.get("destination_binding_hash"),
                            str,
                        )
                        and data.target_market_lineage["destination_binding_hash"]
                        != projection["destination_binding_hash"]
                    )
                )
            )
            or projection["platform"] != data.target_platform
            or projection["platform_channel_id"] != data.destination_platform_channel_id
            or projection["account_identity"] != data.destination_account_identity
        ):
            raise ValidationFailureError("FINAL_REVIEW_DESTINATION_BINDING_MISMATCH")
        return projection

    def _load_destination_projection(
        self,
        *,
        project: VideoProject,
        package_content: ProductionPackageContentV2,
        destination_binding_id: uuid.UUID,
        destination_binding_fingerprint: str,
    ) -> dict[str, Any]:
        binding_ref = package_content.destination_binding_ref
        if binding_ref.artifact_version_id is None:
            raise ValidationFailureError(
                "FINAL_REVIEW_DESTINATION_BINDING_VERSION_REQUIRED"
            )
        version, artifact = self._require_artifact_version(
            binding_ref.artifact_version_id,
            expected_type="destination_binding",
            expected_hash=binding_ref.content_hash,
            expected_project_id=project.id,
        )
        content = version.content or {}
        wrapped = content.get("destination_binding", content.get("destination"))
        binding = wrapped if isinstance(wrapped, dict) else content
        binding_channel_id = binding.get("channel_workspace_id") or binding.get(
            "channel_id"
        )
        if (
            artifact.current_version_id != version.id
            or artifact.status != "approved"
            or version.status != "approved"
            or content_hash(content) != version.content_hash
            or destination_binding_id != version.id
            or destination_binding_fingerprint != version.content_hash
            or (
                binding_channel_id is not None
                and str(binding_channel_id) != str(project.channel_workspace_id)
            )
        ):
            raise ValidationFailureError("FINAL_REVIEW_DESTINATION_BINDING_MISMATCH")
        try:
            from app.services.v2_provider_production import _normalized_destination

            return _normalized_destination(content)
        except ValidationFailureError as exc:
            raise ValidationFailureError(
                "FINAL_REVIEW_DESTINATION_BINDING_MISMATCH"
            ) from exc

    def _require_candidate_destination_projection(
        self, candidate: FinalReviewCandidate
    ) -> dict[str, Any]:
        project = self.session.get(VideoProject, candidate.video_project_id)
        if project is None:
            raise ValidationFailureError("FINAL_REVIEW_DESTINATION_PROJECT_MISSING")
        package_content = ProductionPackageService(self.session).validate_for_readiness(
            candidate.production_package_artifact_version_id
        )
        projection = self._load_destination_projection(
            project=project,
            package_content=package_content,
            destination_binding_id=candidate.destination_binding_id,
            destination_binding_fingerprint=candidate.destination_binding_fingerprint,
        )
        legacy_verified = (
            candidate.target_market_lineage.get("destination_mode") is None
        )
        if (
            (
                not legacy_verified
                and _destination_lineage_projection(projection)
                != _destination_lineage_projection(candidate.target_market_lineage)
            )
            or (
                legacy_verified
                and (
                    projection["destination_mode"] != "VERIFIED_PUBLISH_DESTINATION"
                    or (
                        isinstance(
                            candidate.target_market_lineage.get(
                                "destination_binding_hash"
                            ),
                            str,
                        )
                        and candidate.target_market_lineage["destination_binding_hash"]
                        != projection["destination_binding_hash"]
                    )
                )
            )
            or projection["platform"] != candidate.target_platform
            or projection["platform_channel_id"]
            != candidate.destination_platform_channel_id
            or projection["account_identity"] != candidate.destination_account_identity
        ):
            raise ValidationFailureError("FINAL_REVIEW_DESTINATION_BINDING_MISMATCH")
        return projection

    def _require_candidate_publish_enabled(
        self, candidate: FinalReviewCandidate
    ) -> None:
        projection = self._require_candidate_destination_projection(candidate)
        if (
            projection["destination_mode"] != "VERIFIED_PUBLISH_DESTINATION"
            or projection["destination_status"] != "VERIFIED"
            or projection["publish_execution_allowed"] is not True
            or projection["automatic_publish"] is not False
            or not candidate.destination_platform_channel_id
            or not candidate.destination_account_identity
        ):
            raise ValidationFailureError("FINAL_REVIEW_ONLY_UPLOAD_FORBIDDEN")

    def _require_task_publish_enabled(self, task: HumanUploadTask) -> None:
        candidate = self.session.get(
            FinalReviewCandidate, task.final_review_candidate_id
        )
        if candidate is None:
            raise ValidationFailureError("UPLOAD_TASK_LINEAGE_MISSING")
        self._require_candidate_publish_enabled(candidate)

    def _require_artifact_version(
        self,
        version_id: uuid.UUID,
        *,
        expected_type: str,
        expected_hash: str,
        expected_project_id: uuid.UUID,
    ) -> tuple[ArtifactVersion, Artifact]:
        row = self.session.execute(
            select(ArtifactVersion, Artifact)
            .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
            .where(ArtifactVersion.id == version_id)
        ).one_or_none()
        if row is None:
            raise NotFoundError(f"artifact version not found: {version_id}")
        version, artifact = row
        if (
            artifact.artifact_type != expected_type
            or artifact.video_project_id != expected_project_id
            or version.content_hash != expected_hash
        ):
            raise ValidationFailureError(
                f"FINAL_REVIEW_{expected_type.upper()}_SPLICE_DETECTED"
            )
        return version, artifact

    def _assert_candidate_current(self, candidate: FinalReviewCandidate) -> None:
        project = self.session.get(VideoProject, candidate.video_project_id)
        if project is None:
            raise NotFoundError(
                f"video project not found: {candidate.video_project_id}"
            )
        self._assert_candidate_scope(candidate, project)
        run = self.session.get(ProductionWorkflowRun, candidate.workflow_run_id)
        final_media = self.session.get(FinalMediaRef, candidate.final_media_ref_id)
        if run is None or final_media is None:
            raise ValidationFailureError("FINAL_REVIEW_AUTHORITY_MISSING")
        if (
            run.final_review_candidate_id != candidate.id
            or run.final_review_candidate_hash != candidate.candidate_hash
            or run.archive_verification_state != "VERIFIED"
            or run.archive_receipt_hash != candidate.archive_receipt_hash
            or final_media.video_project_id != candidate.video_project_id
            or final_media.production_package_artifact_version_id
            != candidate.production_package_artifact_version_id
            or final_media.production_package_hash != candidate.production_package_hash
            or final_media.checksum_sha256 != candidate.final_media_hash
        ):
            raise ValidationFailureError("FINAL_REVIEW_CANDIDATE_STALE_OR_SPLICED")
        self._require_candidate_destination_projection(candidate)

    @staticmethod
    def _assert_candidate_scope(
        candidate: FinalReviewCandidate, project: VideoProject
    ) -> None:
        if (
            candidate.company_id != project.company_id
            or candidate.channel_workspace_id != project.channel_workspace_id
            or candidate.video_project_id != project.id
            or candidate.channel_profile_version_id
            != project.channel_profile_version_id
            or candidate.policy_snapshot_id != project.policy_snapshot_id
            or candidate.production_lane != project.production_lane
            or candidate.production_lane != "LONG_FORM"
            or project.production_lane != "LONG_FORM"
            or candidate.content_mode != project.content_mode
            or candidate.series_plan_id != project.series_plan_id
            or candidate.series_run_id != project.series_run_id
            or candidate.episode_number != project.episode_number
            or candidate.standalone_reason_code != project.standalone_reason_code
        ):
            raise ValidationFailureError("FINAL_REVIEW_CANDIDATE_PROJECT_SPLICE")

    @staticmethod
    def _validate_v2_project(project: VideoProject) -> None:
        if (
            project.schema_version != "v2"
            or project.channel_profile_version_id is None
            or project.production_lane != "LONG_FORM"
            or project.planning_source_type != "LONG_FORM_PLAN"
            or project.content_mode not in {"SERIES_EPISODE", "STANDALONE"}
        ):
            raise ValidationFailureError("FINAL_REVIEW_V2_PROJECT_REQUIRED")

    def _require_v2_task(
        self, task_id: uuid.UUID, *, for_update: bool
    ) -> HumanUploadTask:
        statement = select(HumanUploadTask).where(HumanUploadTask.id == task_id)
        if for_update:
            statement = statement.with_for_update()
        task = self.session.scalar(statement)
        if (
            task is None
            or task.schema_version != "v2"
            or task.production_lane != "LONG_FORM"
        ):
            raise NotFoundError(f"v2 human upload task not found: {task_id}")
        return task

    def _require_v2_confirmation(
        self, confirmation_id: uuid.UUID, *, for_update: bool
    ) -> ManualPublishConfirmation:
        statement = select(ManualPublishConfirmation).where(
            ManualPublishConfirmation.id == confirmation_id
        )
        if for_update:
            statement = statement.with_for_update()
        confirmation = self.session.scalar(statement)
        if confirmation is None or confirmation.schema_version != "v2":
            raise NotFoundError(
                f"v2 manual publish confirmation not found: {confirmation_id}"
            )
        return confirmation

    def _confirmation_for_task(
        self, task_id: uuid.UUID
    ) -> ManualPublishConfirmation | None:
        return self.session.scalar(
            select(ManualPublishConfirmation).where(
                ManualPublishConfirmation.human_upload_task_id == task_id
            )
        )

    def _task_lineage(
        self, task: HumanUploadTask
    ) -> tuple[FinalReviewCandidate, FinalVideoDecision, FinalMediaRef]:
        candidate = self.session.get(
            FinalReviewCandidate, task.final_review_candidate_id
        )
        decision = self.session.get(FinalVideoDecision, task.final_video_decision_id)
        final_media = self.session.get(FinalMediaRef, task.final_media_ref_id)
        if candidate is None or decision is None or final_media is None:
            raise ValidationFailureError("UPLOAD_TASK_LINEAGE_MISSING")
        if decision.decision != "UPLOAD":
            raise ValidationFailureError("UPLOAD_TASK_DECISION_NOT_UPLOAD")
        expected = (
            task.company_id,
            task.channel_workspace_id,
            task.video_project_id,
            task.final_review_candidate_id,
            task.final_media_ref_id,
            task.reviewed_checksum,
            task.production_package_artifact_version_id,
            task.production_package_hash,
            task.destination_binding_id,
            task.destination_binding_fingerprint,
        )
        candidate_values = (
            candidate.company_id,
            candidate.channel_workspace_id,
            candidate.video_project_id,
            candidate.id,
            candidate.final_media_ref_id,
            candidate.final_media_hash,
            candidate.production_package_artifact_version_id,
            candidate.production_package_hash,
            candidate.destination_binding_id,
            candidate.destination_binding_fingerprint,
        )
        decision_values = (
            decision.company_id,
            decision.channel_workspace_id,
            decision.video_project_id,
            decision.final_review_candidate_id,
            decision.final_media_ref_id,
            decision.final_media_hash,
            decision.production_package_artifact_version_id,
            decision.production_package_hash,
            decision.destination_binding_id,
            decision.destination_binding_fingerprint,
        )
        if expected != candidate_values or expected != decision_values:
            raise ValidationFailureError("UPLOAD_TASK_LINEAGE_SPLICE_DETECTED")
        if (
            final_media.video_project_id != task.video_project_id
            or final_media.production_package_artifact_version_id
            != task.production_package_artifact_version_id
            or final_media.production_package_hash != task.production_package_hash
            or final_media.checksum_sha256 != task.reviewed_checksum
            or final_media.file_ref != task.final_media_file_ref
        ):
            raise ValidationFailureError("UPLOAD_TASK_FINAL_MEDIA_SPLICE_DETECTED")
        return candidate, decision, final_media

    def _require_uploaded_video_strategic_lineage(
        self,
        *,
        candidate: FinalReviewCandidate,
    ) -> dict[str, Any] | None:
        """Resolve the immutable strategy authority for an uploaded V2 video.

        A current V2 package must carry the same frozen audience, intent, and
        launch-policy lineage as its admitted project.  The all-absent case is
        retained solely for immutable pre-lineage V2 artifacts; it is not a
        compatibility escape hatch for a partial or newly-created authority.
        """

        package = ProductionPackageService(self.session).validate_for_readiness(
            candidate.production_package_artifact_version_id
        )
        package_version = self.session.get(
            ArtifactVersion,
            candidate.production_package_artifact_version_id,
        )
        if (
            package_version is None
            or package_version.content_hash != candidate.production_package_hash
            or package.company_id != candidate.company_id
            or package.channel_workspace_id != candidate.channel_workspace_id
            or package.video_project_id != candidate.video_project_id
            or package.production_lane.value != candidate.production_lane
        ):
            raise ValidationFailureError(
                "UPLOADED_VIDEO_STRATEGIC_LINEAGE_PACKAGE_SCOPE_MISMATCH"
            )
        if package.project_admission_decision_hash is None:
            raise ValidationFailureError(
                "UPLOADED_VIDEO_STRATEGIC_LINEAGE_ADMISSION_HASH_REQUIRED"
            )

        project = self.session.get(VideoProject, candidate.video_project_id)
        if (
            project is None
            or project.schema_version != "v2"
            or project.company_id != candidate.company_id
            or project.channel_workspace_id != candidate.channel_workspace_id
            or project.project_admission_decision_id
            != package.project_admission_decision_id
        ):
            raise ValidationFailureError(
                "UPLOADED_VIDEO_STRATEGIC_LINEAGE_PROJECT_AUTHORITY_MISMATCH"
            )
        admission = self.session.get(
            ProjectAdmissionDecision,
            package.project_admission_decision_id,
        )
        if (
            admission is None
            or admission.schema_version != "v2"
            or admission.company_id != candidate.company_id
            or admission.channel_workspace_id != candidate.channel_workspace_id
            or admission.decision != "ADMIT"
            or admission.admitted_video_project_id != project.id
            or admission.decision_hash != package.project_admission_decision_hash
        ):
            raise ValidationFailureError(
                "UPLOADED_VIDEO_STRATEGIC_LINEAGE_ADMISSION_AUTHORITY_MISMATCH"
            )

        project_lineage = strategic_lineage_from_record(
            project,
            invalid_reason_code="UPLOADED_VIDEO_PROJECT_STRATEGIC_LINEAGE_INVALID",
        )
        admission_lineage = strategic_lineage_from_record(
            admission,
            invalid_reason_code=("UPLOADED_VIDEO_ADMISSION_STRATEGIC_LINEAGE_INVALID"),
        )
        package_lineage = package.strategic_lineage
        if (
            package_lineage is None
            and project_lineage is None
            and admission_lineage is None
        ):
            return None
        if (
            package_lineage is None
            or project_lineage is None
            or admission_lineage is None
        ):
            raise ValidationFailureError("UPLOADED_VIDEO_STRATEGIC_LINEAGE_REQUIRED")
        if package_lineage != project_lineage or package_lineage != admission_lineage:
            raise ValidationFailureError("UPLOADED_VIDEO_STRATEGIC_LINEAGE_MISMATCH")
        return _strategic_lineage_event_payload(package_lineage)

    def _confirmation_payload(
        self,
        *,
        task: HumanUploadTask,
        data: ManualPublishConfirmationCreateV2 | ManualPublishCorrectionV2,
        actor: ActorContext,
        command_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "vcos.manual-publish-confirmation.v2",
            "command_id": command_id or data.command_id,
            "human_upload_task_id": task.id,
            "final_video_decision_id": task.final_video_decision_id,
            "final_media_ref_id": task.final_media_ref_id,
            "reviewed_checksum": task.reviewed_checksum,
            "production_package_artifact_version_id": (
                task.production_package_artifact_version_id
            ),
            "production_package_hash": task.production_package_hash,
            "destination_binding_id": data.destination_binding_id,
            "destination_binding_fingerprint": (data.destination_binding_fingerprint),
            "platform": data.platform,
            "platform_channel_id": data.platform_channel_id,
            "destination_account_identity": data.destination_account_identity,
            "platform_video_id": data.platform_video_id,
            "video_url": data.video_url,
            "title": data.title,
            "description": data.description,
            "privacy_status": data.privacy_status,
            "published_at": data.published_at,
            "duration_seconds": data.duration_seconds,
            "thumbnail_confirmed": data.thumbnail_confirmed,
            "caption_confirmed": data.caption_confirmed,
            "playlist_id": data.playlist_id,
            "playlist_order": data.playlist_order,
            "disclosures": data.disclosures,
            "accept_non_material_variance": (data.accept_non_material_variance),
            "actor_id": actor.actor_id,
            "actor_role": actor.actor_role,
        }

    def _classify_confirmation(
        self,
        *,
        candidate: FinalReviewCandidate,
        final_media: FinalMediaRef,
        data: ManualPublishConfirmationCreateV2 | ManualPublishCorrectionV2,
    ) -> dict[str, Any]:
        reason_codes: list[str] = []
        metadata_diff: dict[str, Any] = {}

        destination_mismatch = (
            data.platform != candidate.target_platform
            or data.destination_binding_id != candidate.destination_binding_id
            or data.destination_binding_fingerprint
            != candidate.destination_binding_fingerprint
            or data.platform_channel_id != candidate.destination_platform_channel_id
            or data.destination_account_identity
            != candidate.destination_account_identity
        )
        if destination_mismatch:
            return _classification(
                state="BLOCKED_DESTINATION",
                reason_codes=["PUBLISH_DESTINATION_MISMATCH"],
                next_action=("Correct the destination channel/account and resubmit."),
            )
        if not _video_url_matches(data.video_url, data.platform_video_id):
            return _classification(
                state="REJECTED_MISMATCH",
                reason_codes=["PLATFORM_VIDEO_ID_URL_MISMATCH"],
                next_action="Correct the platform video ID or URL and resubmit.",
            )
        if final_media.duration_seconds is not None and (
            abs(Decimal(data.duration_seconds) - Decimal(final_media.duration_seconds))
            > _DURATION_TOLERANCE_SECONDS
        ):
            return _classification(
                state="REJECTED_MISMATCH",
                reason_codes=["PLATFORM_VIDEO_DURATION_MISMATCH"],
                next_action="Select/upload the reviewed final media and resubmit.",
            )

        expected = candidate.publish_metadata_snapshot
        expected_privacy = str(
            expected.get("public_privacy_status")
            or expected.get("privacy_status")
            or ""
        ).upper()
        if expected_privacy and data.privacy_status != expected_privacy:
            reason_codes.append("PRIVACY_MISMATCH")
            metadata_diff["privacy_status"] = {
                "expected": expected_privacy,
                "actual": data.privacy_status,
                "materiality": "CRITICAL",
            }
        disclosure_mismatch = {
            key: {"expected": value, "actual": data.disclosures.get(key)}
            for key, value in candidate.disclosure_snapshot.items()
            if data.disclosures.get(key) != value
        }
        if disclosure_mismatch:
            reason_codes.append("DISCLOSURE_MISMATCH")
            metadata_diff["disclosures"] = disclosure_mismatch
        if bool(expected.get("thumbnail_required")) and not data.thumbnail_confirmed:
            reason_codes.append("THUMBNAIL_CONFIRMATION_MISSING")
        if bool(expected.get("caption_required")) and not data.caption_confirmed:
            reason_codes.append("CAPTION_CONFIRMATION_MISSING")

        expected_title = str(expected.get("title") or "")
        if _normalize_text(data.title) != _normalize_text(expected_title):
            reason_codes.append("MATERIAL_TITLE_VARIANCE")
            metadata_diff["title"] = {
                "expected": expected_title,
                "actual": data.title,
                "materiality": "MATERIAL",
            }
        expected_description = expected.get("description")
        description_varies = _normalize_text(data.description) != _normalize_text(
            expected_description
        )
        if description_varies:
            metadata_diff["description"] = {
                "expected": expected_description,
                "actual": data.description,
                "materiality": "NON_MATERIAL_WITH_ATTESTATION",
            }

        if reason_codes:
            return _classification(
                state="CORRECTION_REQUIRED",
                reason_codes=reason_codes,
                next_action=("Apply the exact privacy/disclosure/metadata correction."),
                metadata_diff=metadata_diff,
            )
        if description_varies:
            if data.accept_non_material_variance:
                return _classification(
                    state="VARIANCE_ACCEPTED",
                    reason_codes=["NON_MATERIAL_DESCRIPTION_VARIANCE_ATTESTED"],
                    next_action="Verify observable platform metadata.",
                    metadata_diff=metadata_diff,
                )
            return _classification(
                state="CORRECTION_REQUIRED",
                reason_codes=["NON_MATERIAL_VARIANCE_ATTESTATION_REQUIRED"],
                next_action=("Attest the allowed description variance or correct it."),
                metadata_diff=metadata_diff,
            )
        return _classification(
            state="SUBMITTED",
            reason_codes=[],
            next_action="Verify observable platform metadata.",
            metadata_diff=metadata_diff,
        )

    @staticmethod
    def _apply_confirmation_values(
        *,
        confirmation: ManualPublishConfirmation,
        data: ManualPublishCorrectionV2,
        classification: dict[str, Any],
    ) -> None:
        confirmation.target_platform = data.platform
        confirmation.confirmation_state = classification["state"]
        confirmation.actual_video_id = data.platform_video_id
        confirmation.actual_video_url = data.video_url
        confirmation.actual_published_at = data.published_at
        confirmation.destination_binding_id = data.destination_binding_id
        confirmation.destination_binding_fingerprint = (
            data.destination_binding_fingerprint
        )
        confirmation.platform_channel_id = data.platform_channel_id
        confirmation.destination_account_identity = data.destination_account_identity
        confirmation.actual_metadata = _actual_metadata(data)
        confirmation.actual_disclosures = dict(data.disclosures)
        confirmation.actual_duration_seconds = data.duration_seconds
        confirmation.thumbnail_confirmed = data.thumbnail_confirmed
        confirmation.caption_confirmed = data.caption_confirmed
        confirmation.playlist_id = data.playlist_id
        confirmation.playlist_order = data.playlist_order
        confirmation.operator_notes = data.operator_notes
        confirmation.validation_summary = classification["validation_summary"]
        confirmation.metadata_diff = classification["metadata_diff"]
        confirmation.reason_codes = classification["reason_codes"]
        confirmation.next_action = classification["next_action"]

    @staticmethod
    def _classify_observation(
        *,
        confirmation: ManualPublishConfirmation,
        data: ManualPublishVerificationV2,
    ) -> dict[str, Any] | None:
        if (
            data.observed_platform != confirmation.target_platform
            or data.observed_platform_channel_id != confirmation.platform_channel_id
            or data.observed_destination_account_identity
            != confirmation.destination_account_identity
        ):
            return {
                "state": "BLOCKED_DESTINATION",
                "reason_codes": ["OBSERVED_DESTINATION_MISMATCH"],
                "next_action": "Correct the destination/account before verification.",
            }
        if (
            data.observed_platform_video_id != confirmation.actual_video_id
            or data.observed_video_url != confirmation.actual_video_url
            or not _video_url_matches(
                data.observed_video_url, data.observed_platform_video_id
            )
        ):
            return {
                "state": "REJECTED_MISMATCH",
                "reason_codes": ["OBSERVED_PLATFORM_VIDEO_IDENTITY_MISMATCH"],
                "next_action": "Correct the platform video identity and resubmit.",
            }
        actual = confirmation.actual_metadata or {}
        if (
            _normalize_text(data.observed_title) != _normalize_text(actual.get("title"))
            or _normalize_text(data.observed_description)
            != _normalize_text(actual.get("description"))
            or data.observed_privacy_status != actual.get("privacy_status")
            or abs(
                Decimal(data.observed_duration_seconds)
                - Decimal(confirmation.actual_duration_seconds)
            )
            > _DURATION_TOLERANCE_SECONDS
            or abs(
                (
                    data.observed_published_at - confirmation.actual_published_at
                ).total_seconds()
            )
            > 1
        ):
            return {
                "state": "CORRECTION_REQUIRED",
                "reason_codes": ["OBSERVED_METADATA_MISMATCH"],
                "next_action": "Correct the confirmation to the observed values.",
            }
        return None

    @staticmethod
    def _assert_confirmation_lineage(
        *,
        confirmation: ManualPublishConfirmation,
        task: HumanUploadTask,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        final_media: FinalMediaRef,
    ) -> None:
        expected = (
            task.id,
            candidate.id,
            decision.id,
            final_media.id,
            task.reviewed_checksum,
            task.production_package_artifact_version_id,
            task.production_package_hash,
            task.destination_binding_id,
            task.destination_binding_fingerprint,
            task.channel_profile_version_id,
            task.policy_snapshot_id,
        )
        actual = (
            confirmation.human_upload_task_id,
            confirmation.final_review_candidate_id,
            confirmation.final_video_decision_id,
            confirmation.final_media_ref_id,
            confirmation.reviewed_checksum,
            confirmation.production_package_artifact_version_id,
            confirmation.production_package_hash,
            confirmation.destination_binding_id,
            confirmation.destination_binding_fingerprint,
            confirmation.channel_profile_version_id,
            confirmation.policy_snapshot_id,
        )
        if expected != actual:
            raise ValidationFailureError("PUBLISH_CONFIRMATION_LINEAGE_SPLICE_DETECTED")

    def _append_event_once(
        self,
        *,
        event_id: uuid.UUID,
        event_type: str,
        aggregate_id: uuid.UUID,
        company_id: uuid.UUID,
        correlation_id: str,
        causation_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> None:
        bus = DomainEventBus(self.session)
        existing = bus.get_by_id(event_id)
        if existing is not None:
            if (
                existing.event_type != event_type
                or existing.aggregate_id != aggregate_id
                or existing.payload != payload
            ):
                raise ConflictError("PUBLISH_EVENT_ID_COLLISION")
            return
        bus.append(
            EventEnvelope(
                event_id=event_id,
                event_type=event_type,
                event_version=1,
                aggregate_type="UploadedVideo",
                aggregate_id=aggregate_id,
                payload=payload,
                metadata={
                    "service_version": PUBLISH_SERVICE_VERSION,
                    "delivery_semantics": "EXACTLY_ONCE_EFFECT",
                },
                correlation_id=correlation_id,
                causation_id=causation_id,
            ),
            company_id=company_id,
        )

    def _advance_series_after_verified(
        self,
        *,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        task: HumanUploadTask,
        confirmation: ManualPublishConfirmation,
        uploaded: UploadedVideo,
    ) -> SeriesEpisodePublication | None:
        if candidate.content_mode == "STANDALONE":
            return None
        if (
            candidate.series_plan_id is None
            or candidate.series_run_id is None
            or candidate.episode_number is None
        ):
            raise ValidationFailureError("SERIES_PUBLICATION_LINEAGE_INCOMPLETE")
        run = self.session.scalar(
            select(SeriesRun)
            .where(SeriesRun.id == candidate.series_run_id)
            .with_for_update()
        )
        if run is None:
            raise NotFoundError(f"series run not found: {candidate.series_run_id}")
        if (
            run.series_plan_id != candidate.series_plan_id
            or run.company_id != candidate.company_id
            or run.channel_workspace_id != candidate.channel_workspace_id
        ):
            raise ValidationFailureError("SERIES_PUBLICATION_SCOPE_MISMATCH")
        existing = self.session.scalar(
            select(SeriesEpisodePublication).where(
                SeriesEpisodePublication.series_run_id == candidate.series_run_id,
                SeriesEpisodePublication.episode_number == candidate.episode_number,
            )
        )
        if existing is not None:
            if (
                existing.video_project_id != candidate.video_project_id
                or existing.uploaded_video_id != uploaded.id
            ):
                raise ConflictError("SERIES_EPISODE_ALREADY_PUBLISHED")
            return existing
        if run.published_episode_count >= run.reserved_episode_count:
            raise ValidationFailureError("SERIES_PUBLISHED_PROGRESS_EXCEEDS_RESERVED")
        receipt = SeriesEpisodePublication(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            series_plan_id=candidate.series_plan_id,
            series_run_id=candidate.series_run_id,
            episode_number=candidate.episode_number,
            uploaded_video_id=uploaded.id,
            final_video_decision_id=decision.id,
            human_upload_task_id=task.id,
            manual_publish_confirmation_id=confirmation.id,
            published_at=uploaded.published_at,
        )
        self.session.add(receipt)
        run.published_episode_count += 1
        self.session.flush()
        return receipt

    @staticmethod
    def _archive_supplement(
        *,
        uploaded_id: uuid.UUID,
        candidate: FinalReviewCandidate,
        decision: FinalVideoDecision,
        task: HumanUploadTask,
        confirmation: ManualPublishConfirmation,
        verification: ManualPublishVerificationV2,
        verification_evidence_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "vcos.publish-archive-supplement.v2",
            "prior_archive_receipt": {
                "ref": candidate.archive_receipt_ref,
                "hash": candidate.archive_receipt_hash,
                "state": candidate.archive_verification_state,
                "archive_object_ref": candidate.archive_object_ref,
            },
            "final_video_decision": {
                "id": str(decision.id),
                "decision": decision.decision,
                "decision_hash": decision.decision_hash,
                "operator_user_id": str(decision.operator_user_id),
                "authenticated_actor_role": decision.authenticated_actor_role,
                "decision_timestamp": decision.decision_timestamp.isoformat(),
            },
            "human_upload_task": {
                "id": str(task.id),
                "selected_file_name": task.selected_file_name,
                "selected_file_ref": task.selected_file_ref,
                "selected_file_checksum": task.selected_file_checksum,
                "attested_by_user_id": str(task.attested_by_user_id),
                "attested_at": task.attested_at.isoformat(),
            },
            "manual_publish_confirmation": {
                "id": str(confirmation.id),
                "confirmation_hash": confirmation.confirmation_hash,
                "confirmed_by_user_id": str(confirmation.confirmed_by_user_id),
                "actual_video_id": confirmation.actual_video_id,
                "actual_video_url": confirmation.actual_video_url,
                "actual_metadata": confirmation.actual_metadata,
                "actual_disclosures": confirmation.actual_disclosures,
                "variance_attested_by_user_id": _optional_uuid(
                    confirmation.variance_attested_by_user_id
                ),
                "variance_attested_at": (
                    confirmation.variance_attested_at.isoformat()
                    if confirmation.variance_attested_at
                    else None
                ),
            },
            "uploaded_video": {
                "id": str(uploaded_id),
                "platform": confirmation.target_platform,
                "platform_video_id": confirmation.actual_video_id,
                "video_url": confirmation.actual_video_url,
                "published_at": confirmation.actual_published_at.isoformat(),
            },
            "verification": {
                "command_id": str(verification.verification_command_id),
                "evidence_ref": verification.verification_evidence_ref,
                "evidence_hash": verification_evidence_hash,
                "evidence_hash_authority": "SERVER_CANONICAL_OBSERVATION",
            },
        }

    @staticmethod
    def _require_human_permission(actor: ActorContext, permission: str) -> None:
        if actor.actor_type != ActorType.HUMAN_USER:
            raise ForbiddenError("AUTHENTICATED_HUMAN_ACTOR_REQUIRED")
        if not actor.has_permission(permission):
            raise ForbiddenError(f"PERMISSION_REQUIRED:{permission}")

    @staticmethod
    def _verification_result(
        *,
        confirmation: ManualPublishConfirmation,
        uploaded: UploadedVideo,
    ) -> ManualPublishVerificationResultV2:
        return ManualPublishVerificationResultV2(
            status="VERIFIED",
            confirmation=ManualPublishConfirmationReadV2.model_validate(confirmation),
            uploaded_video=UploadedVideoReadV2.model_validate(uploaded),
        )


def _classification(
    *,
    state: str,
    reason_codes: list[str],
    next_action: str | None,
    metadata_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "reason_codes": reason_codes,
        "next_action": next_action,
        "metadata_diff": metadata_diff or {},
        "validation_summary": {
            "state": state,
            "reason_codes": reason_codes,
            "deterministic_policy": PUBLISH_MATERIALITY_POLICY_V1,
            "generic_review_queue_required": False,
        },
    }


def _actual_metadata(
    data: ManualPublishConfirmationCreateV2 | ManualPublishCorrectionV2,
) -> dict[str, Any]:
    return {
        "title": data.title,
        "description": data.description,
        "privacy_status": data.privacy_status,
        "duration_seconds": str(data.duration_seconds),
        "thumbnail_confirmed": data.thumbnail_confirmed,
        "caption_confirmed": data.caption_confirmed,
        "playlist_id": data.playlist_id,
        "playlist_order": data.playlist_order,
    }


def _verification_evidence_payload(
    *,
    confirmation: ManualPublishConfirmation,
    task: HumanUploadTask,
    final_media: FinalMediaRef,
    data: ManualPublishVerificationV2,
) -> dict[str, Any]:
    """Build server-owned observable evidence bound to the exact v2 lineage."""

    return {
        "schema_version": "vcos.manual-publish-verification-evidence.v1",
        "manual_publish_confirmation_id": confirmation.id,
        "manual_publish_confirmation_hash": confirmation.confirmation_hash,
        "human_upload_task_id": task.id,
        "video_project_id": task.video_project_id,
        "final_media_ref_id": final_media.id,
        "reviewed_checksum": task.reviewed_checksum,
        "production_package_artifact_version_id": (
            task.production_package_artifact_version_id
        ),
        "production_package_hash": task.production_package_hash,
        "destination_binding_id": task.destination_binding_id,
        "destination_binding_fingerprint": task.destination_binding_fingerprint,
        "verification_evidence_ref": data.verification_evidence_ref,
        "observation": {
            "platform": data.observed_platform,
            "platform_channel_id": data.observed_platform_channel_id,
            "destination_account_identity": (
                data.observed_destination_account_identity
            ),
            "platform_video_id": data.observed_platform_video_id,
            "video_url": data.observed_video_url,
            "title": data.observed_title,
            "description": data.observed_description,
            "privacy_status": data.observed_privacy_status,
            "published_at": data.observed_published_at,
            "duration_seconds": data.observed_duration_seconds,
            "tags": data.observed_tags,
            "category_id": data.observed_category_id,
            "default_language": data.observed_default_language,
            "made_for_kids": data.observed_made_for_kids,
            "contains_synthetic_media": data.observed_contains_synthetic_media,
            "thumbnail_confirmed": data.observed_thumbnail_confirmed,
            "caption_confirmed": data.observed_caption_confirmed,
        },
    }


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _file_name(file_ref: str) -> str:
    parsed = urlparse(file_ref)
    return PurePosixPath(parsed.path or file_ref).name


def _video_url_matches(video_url: str, platform_video_id: str) -> bool:
    parsed = urlparse(video_url)
    query = parse_qs(parsed.query)
    candidates = {
        value for key in ("v", "video_id", "id") for value in query.get(key, [])
    }
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        candidates.add(path_parts[-1])
    return platform_video_id in candidates or platform_video_id in video_url


def _event_id(uploaded_id: uuid.UUID, event_type: str) -> uuid.UUID:
    return uuid.uuid5(_EVENT_NAMESPACE, f"{uploaded_id}:{event_type}")


def _optional_uuid(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _market_hash(lineage: dict[str, Any]) -> str | None:
    for key in (
        "market_policy_hash",
        "target_market_profile_hash",
        "market_alignment_dossier_hash",
    ):
        value = lineage.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _rights_ref(lineage: dict[str, Any]) -> str | None:
    value = lineage.get("rights_envelope_ref")
    return str(value) if value else None
