"""Transaction-local NORMAL_PRODUCTION AI-visual authorization after MEDIA.

The ordinary V2 workflow cannot enter VISUAL with only a package route.  This
module turns one already-verified, exact real MEDIA result into the mutable
``AIVisualProductionRun`` and its dedicated Gemini reservation.  It performs
no provider call and deliberately does not commit: the caller seals the run,
budget, MEDIA command receipt, and VISUAL scheduling in one transaction.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.production_workflow import WorkflowStageResult
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.ai_visual import AI_VISUAL_POLICY_VERSION, AIVisualProductionRun
from app.db.models.channel import CompiledChannelPolicySnapshot
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.voice_authority import CombinedReplacementBudgetAuthority
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.ai_visual_rerender_authority import AI_VISUAL_POLICY_REF
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.production_package import ProductionPackageService
from app.services.v2_ai_visual_provider import (
    V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD,
)
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.core.config import (
    VEO_DEFAULT_DURATION_SECONDS,
    VEO_DEFAULT_MODEL_ID,
    VEO_DEFAULT_OUTPUT_COUNT,
    VEO_DEFAULT_RESOLUTION,
)
from app.services.v2_support_authority import V2FrozenSupportEnvelope


NORMAL_AI_VISUAL_MAXIMUM_IMAGES = 512
NORMAL_AI_VISUAL_MAXIMUM_VIDEOS = 512
NORMAL_AI_VISUAL_MAXIMUM_COST_USD = Decimal("1000000.000000")
NORMAL_AI_VISUAL_PROVIDER_KEY = "google_gemini_image"
NORMAL_AI_VISUAL_VIDEO_PROVIDER_KEY = "google_veo"
_NORMAL_RUN_NAMESPACE = uuid.UUID("b3516fef-f3c3-5b15-a5aa-37aec9c076f4")
_REAL_MEDIA_ADAPTER = "v2-elevenlabs-narration"
_REAL_MEDIA_RESULT = "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE"


@dataclass(frozen=True, slots=True)
class NormalAIVisualAuthorization:
    visual_production_run_id: uuid.UUID
    budget_reservation_id: uuid.UUID
    budget_reservation_ref: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class _NormalVisualRequirement:
    image_owner_count: int
    video_owner_count: int
    scene_count: int
    image_cost_usd: Decimal
    video_cost_usd: Decimal
    total_cost_usd: Decimal
    video_unit_cost_usd: Decimal


def _required_text(value: Mapping[str, Any], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValidationFailureError(
            f"V2_NORMAL_AI_VISUAL_MEDIA_{key.upper()}_REQUIRED"
        )
    return candidate.strip()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_file(root: Path, value: str) -> Path:
    raw = Path(value)
    if raw.is_absolute() or not raw.parts or ".." in raw.parts or "~" in raw.parts:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_ROOT_RELATIVE_REF_REQUIRED")
    unresolved = root / raw
    resolved = unresolved.resolve(strict=True)
    try:
        relative = unresolved.absolute().relative_to(root)
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SOURCE_PATH_ESCAPE") from exc
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SOURCE_SYMLINK_FORBIDDEN")
    if not resolved.is_file() or resolved.is_symlink():
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SOURCE_FILE_INVALID")
    return resolved


def _sidecar_version(
    session: Session,
    *,
    project_id: uuid.UUID,
    journal: Mapping[str, Any],
    id_key: str,
    hash_key: str,
    ref_key: str,
    artifact_type: str,
) -> ArtifactVersion:
    try:
        identifier = uuid.UUID(_required_text(journal, id_key))
    except ValueError as exc:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SIDECAR_ID_INVALID") from exc
    expected_hash = _required_text(journal, hash_key)
    expected_ref = _required_text(journal, ref_key)
    version = session.get(ArtifactVersion, identifier)
    artifact = (
        session.get(Artifact, version.artifact_id) if version is not None else None
    )
    if (
        version is None
        or artifact is None
        or artifact.video_project_id != project_id
        or artifact.artifact_type != artifact_type
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != expected_hash
        or expected_ref != f"artifact-version://{version.id}"
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SIDECAR_AUTHORITY_DRIFT")
    return version


def _active_policy() -> dict[str, str]:
    path = (
        Path(__file__).resolve().parents[2]
        / "config"
        / "production_visual_policy_catalog.yaml"
    )
    loaded = ConfigRegistryService(None).validate_catalog(path)
    items = loaded.content.get("items") or []
    item = items[0] if len(items) == 1 and isinstance(items[0], dict) else None
    ref = (
        f"config://production_visual_policy_catalog/{loaded.catalog_version}/"
        f"{item.get('key')}"
        if item is not None
        else ""
    )
    if (
        item is None
        or ref != AI_VISUAL_POLICY_REF
        or item.get("policy_version") != AI_VISUAL_POLICY_VERSION
        or item.get("status") != "ACTIVE"
        or item.get("allowed_primary_routes") != ["AI_IMAGE", "AI_VIDEO"]
        or item.get("renderer_policy", {}).get("assembly_only") is not True
        or item.get("renderer_policy", {}).get("primary_visual_generation") is not False
        or item.get("fallback_policy", {}).get("native_fallback_allowed") is not False
        or item.get("fallback_policy", {}).get("stock_fallback_allowed") is not False
        or item.get("fallback_policy", {}).get("screenshot_fallback_allowed")
        is not False
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_POLICY_INVALID")
    return {
        "version": AI_VISUAL_POLICY_VERSION,
        "ref": ref,
        "hash": loaded.content_hash,
    }


def _positive_money(settings: Any, field: str, blocker: str) -> Decimal:
    try:
        amount = Decimal(str(getattr(settings, field)))
    except (AttributeError, TypeError, ValueError):
        raise ValidationFailureError(blocker) from None
    if not amount.is_finite() or amount <= 0:
        raise ValidationFailureError(blocker)
    return amount


def _money_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


def _planned_visual_cost(
    *,
    visual_run_id: uuid.UUID,
    workflow: ProductionWorkflowRun,
    timeline: Mapping[str, Any],
    source_timeline_hash: str,
    channel_policy: ChannelScopedPolicy,
) -> _NormalVisualRequirement:
    """Compile the exact owner-slot count before creating paid authority."""

    # Imported only at the post-MEDIA runtime boundary.  The stage imports the
    # workflow coordinator for its context type, so importing it at module load
    # here would create a coordinator/authorization cycle.
    from app.services.v2_ai_visual_stage import compile_ai_visual_stage_planning

    try:
        planning = compile_ai_visual_stage_planning(
            visual_run=SimpleNamespace(
                id=visual_run_id,
                video_project_id=workflow.video_project_id,
                production_package_artifact_version_id=(
                    workflow.production_package_artifact_version_id
                ),
                source_timeline_hash=source_timeline_hash,
            ),
            timeline=timeline,
            provider_readiness_ref=(
                f"ai-visual-readiness/{visual_run_id}/google-gemini-image+google-veo"
            ),
            # Only the owner projection is consumed here.  The durable stage
            # recompiles against the exact reservation ref after authorization.
            budget_authority_ref=f"pending-ai-visual-budget/{visual_run_id}",
            maximum_image_submissions=NORMAL_AI_VISUAL_MAXIMUM_IMAGES,
            maximum_video_submissions=min(
                NORMAL_AI_VISUAL_MAXIMUM_VIDEOS,
                int(channel_policy.budget_policy.max_veo_clips_per_video),
            ),
        )
    except ValidationFailureError as exc:
        if str(exc) == "V2_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT":
            raise ValidationFailureError(
                "V2_NORMAL_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT"
            ) from exc
        raise
    image_count = planning.scene_plan.unique_ai_image_asset_slot_count
    video_count = planning.scene_plan.unique_ai_video_asset_slot_count
    if image_count < 0 or video_count < 0 or image_count + video_count <= 0:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_OWNER_COUNT_INVALID")
    video_catalog = GoogleVeoModelPriceCatalog()
    video_unit_cost = video_catalog.estimate(
        model_id=VEO_DEFAULT_MODEL_ID,
        resolution=VEO_DEFAULT_RESOLUTION,
        duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
        output_count=VEO_DEFAULT_OUTPUT_COUNT,
        hard_cap=Decimal("1000000"),
        approval_amount=Decimal("1000000"),
    ).estimated_amount
    video_cost = video_unit_cost * video_count
    image_cost = V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD * image_count
    budget = channel_policy.budget_policy
    if (
        video_count > int(budget.max_veo_clips_per_video)
        or video_count * VEO_DEFAULT_DURATION_SECONDS
        > Decimal(str(budget.max_veo_seconds_per_video))
        or video_cost > Decimal(str(budget.max_veo_cost_per_video))
    ):
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_VIDEO_DURATION_AUTHORITY_INSUFFICIENT"
        )
    return _NormalVisualRequirement(
        image_owner_count=image_count,
        video_owner_count=video_count,
        scene_count=len(planning.scene_plan.scenes),
        image_cost_usd=image_cost,
        video_cost_usd=video_cost,
        total_cost_usd=image_cost + video_cost,
        video_unit_cost_usd=video_unit_cost,
    )


def _veo_monthly_provider_cap(*, unit_cost_usd: Decimal) -> Decimal:
    loaded = ConfigRegistryService(None).validate_catalog(
        Path(__file__).resolve().parents[2]
        / "config"
        / "media_provider_budget_policy_catalog.yaml"
    )
    matches = [
        item
        for item in loaded.content.get("items") or []
        if isinstance(item, Mapping) and item.get("provider_key") == "google_veo"
    ]
    renders = matches[0].get("monthly_cap_renders") if len(matches) == 1 else None
    if not isinstance(renders, int) or renders <= 0:
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_VEO_PROVIDER_CAP_AUTHORITY_REQUIRED"
        )
    return unit_cost_usd * renders


def _require_normal_combined_budget(
    *,
    workflow: ProductionWorkflowRun,
    project: VideoProject,
    media_budget: MR1MonthlyBudgetReservation | None,
    envelope: V2FrozenSupportEnvelope,
    channel_policy: ChannelScopedPolicy,
    authority: CombinedReplacementBudgetAuthority | None,
    requirement: _NormalVisualRequirement,
) -> CombinedReplacementBudgetAuthority:
    """Consume the pre-TTS aggregate reservation; never reserve VISUAL twice."""

    frozen = envelope.zero_cost_budget
    evidence = frozen.reservation_evidence or {}
    capacity_evidence = evidence.get("capacity_evidence") or {}
    policy_cap = Decimal(str(channel_policy.budget_policy.max_estimated_cost_per_video))
    frozen_aggregate = Decimal(frozen.authorized_cost_usd)
    allocations = {
        str(key): Decimal(str(value))
        for key, value in ((media_budget.provider_allocations_json or {}) if media_budget else {}).items()
    }
    visual_source = (
        dict((authority.source_refs or {}).get("ai_visual_preflight") or {})
        if authority is not None
        else {}
    )
    expected_visual_allocations = {
        key: value
        for key, value in {
            NORMAL_AI_VISUAL_PROVIDER_KEY: Decimal(authority.ai_image_projected_cost_usd)
            if authority is not None
            else Decimal("0"),
            NORMAL_AI_VISUAL_VIDEO_PROVIDER_KEY: Decimal(authority.ai_video_projected_cost_usd)
            if authority is not None
            else Decimal("0"),
        }.items()
        if value > 0
    }
    if (
        envelope.execution_mode != "REAL_LONG_FORM_PRODUCTION"
        or media_budget is None
        or media_budget.run_id != workflow.id
        or media_budget.video_project_id != project.id
        or frozen.reservation_ref != media_budget.reservation_ref
        or evidence.get("reservation_id") != str(media_budget.id)
        or evidence.get("run_id") != str(workflow.id)
        or evidence.get("project_id") != str(project.id)
        or evidence.get("request_hash") != media_budget.request_hash
        or capacity_evidence.get("content_hash")
        != (media_budget.capacity_evidence_json or {}).get("content_hash")
        or frozen_aggregate <= 0
        or frozen_aggregate > policy_cap
        or Decimal(media_budget.reserved_amount) != frozen_aggregate
        or authority is None
        or authority.state != "FROZEN"
        or authority.video_project_id != project.id
        or authority.budget_reservation_id != media_budget.id
        or authority.budget_reservation_ref != media_budget.reservation_ref
        or authority.route_budget_authority_hash != frozen.content_hash
        or authority.support_envelope_hash != envelope.content_hash
        or Decimal(authority.combined_replacement_projected_cost_usd)
        != frozen_aggregate
        or allocations.get(NORMAL_AI_VISUAL_PROVIDER_KEY, Decimal("0"))
        != expected_visual_allocations.get(NORMAL_AI_VISUAL_PROVIDER_KEY, Decimal("0"))
        or allocations.get(NORMAL_AI_VISUAL_VIDEO_PROVIDER_KEY, Decimal("0"))
        != expected_visual_allocations.get(NORMAL_AI_VISUAL_VIDEO_PROVIDER_KEY, Decimal("0"))
        or sum(allocations.values(), Decimal("0")) != frozen_aggregate
        or visual_source.get("production_visual_policy_hash")
        != envelope.production_visual_policy_hash
        or requirement.image_owner_count
        > int(visual_source.get("unique_ai_image_asset_slot_count", -1))
        or requirement.video_owner_count
        > int(visual_source.get("unique_ai_video_asset_slot_count", -1))
        or requirement.image_cost_usd > Decimal(authority.ai_image_projected_cost_usd)
        or requirement.video_cost_usd > Decimal(authority.ai_video_projected_cost_usd)
        or media_budget.status
        not in {
            "RESERVED",
            "SUBMITTED",
            "SETTLED_ACTUAL",
            "SETTLED_CONSERVATIVE",
        }
    ):
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_COMBINED_BUDGET_AUTHORITY_DRIFT"
        )
    return authority


def _validate_existing(
    *,
    workflow: ProductionWorkflowRun,
    visual_run: AIVisualProductionRun,
    budget: MR1MonthlyBudgetReservation | None,
    media_result: WorkflowStageResult,
    journal: Mapping[str, Any],
    policy: Mapping[str, str],
    visual_run_id: uuid.UUID,
    audio_duration_ms: int,
    requirement: _NormalVisualRequirement,
    authority: CombinedReplacementBudgetAuthority,
) -> NormalAIVisualAuthorization:
    expected = (
        visual_run.id == visual_run_id
        and workflow.ai_visual_production_run_id == visual_run.id
        and workflow.ai_visual_policy_ref == policy["ref"]
        and workflow.ai_visual_policy_hash == policy["hash"]
        and visual_run.workflow_run_id == workflow.id
        and visual_run.video_project_id == workflow.video_project_id
        and visual_run.rerender_authority_id is None
        and visual_run.execution_kind == "NORMAL_PRODUCTION"
        and visual_run.production_package_artifact_version_id
        == workflow.production_package_artifact_version_id
        and visual_run.production_package_hash == workflow.production_package_hash
        and visual_run.production_visual_policy_version == policy["version"]
        and visual_run.production_visual_policy_ref == policy["ref"]
        and visual_run.production_visual_policy_hash == policy["hash"]
        and visual_run.source_timeline_ref == media_result.result_ref
        and visual_run.source_timeline_hash == media_result.result_hash
        and visual_run.audio_ref == journal.get("audio_relative_path")
        and visual_run.audio_checksum == journal.get("audio_checksum")
        and visual_run.audio_duration_ms == audio_duration_ms
        and visual_run.timed_words_ref == journal.get("timed_words_ref")
        and visual_run.timed_words_hash == journal.get("timed_words_hash")
        and visual_run.caption_ref == journal.get("caption_relative_path")
        and visual_run.caption_hash == journal.get("caption_artifact_hash")
        and visual_run.caption_checksum == journal.get("caption_checksum")
        and visual_run.subtitle_qc_ref == journal.get("subtitle_qc_ref")
        and visual_run.subtitle_qc_hash == journal.get("subtitle_qc_hash")
        and budget is not None
        and budget.id == visual_run.budget_reservation_id
        and budget.run_id == workflow.id
        and budget.video_project_id == visual_run.video_project_id
        and budget.reservation_ref == visual_run.budget_reservation_ref
        and authority.content_hash
        == visual_run.budget_authority_hash
        and Decimal(budget.reserved_amount)
        == Decimal(authority.combined_replacement_projected_cost_usd)
        and requirement.image_cost_usd <= Decimal(authority.ai_image_projected_cost_usd)
        and requirement.video_cost_usd <= Decimal(authority.ai_video_projected_cost_usd)
        and budget.status in {"RESERVED", "SUBMITTED", "SETTLED_CONSERVATIVE"}
    )
    if not expected:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_AUTHORIZATION_DRIFT")
    return NormalAIVisualAuthorization(
        visual_production_run_id=visual_run.id,
        budget_reservation_id=budget.id,
        budget_reservation_ref=budget.reservation_ref,
        replayed=True,
    )


def authorize_normal_ai_visual_after_verified_media(
    *,
    session: Session,
    workflow_run_id: uuid.UUID,
    media_result: WorkflowStageResult,
    workspace_root: Path | None = None,
    settings: Any | None = None,
    clock: Callable[[], Any] = utc_now,
) -> NormalAIVisualAuthorization | None:
    """Seal or exactly replay ordinary VISUAL authority without committing."""

    if media_result.result_type != _REAL_MEDIA_RESULT:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_REAL_MEDIA_RESULT_REQUIRED")
    refs = media_result.authority_refs
    if (
        media_result.result_ref is None
        or media_result.result_hash is None
        or refs.canonical_media_timeline_ref != media_result.result_ref
        or refs.canonical_media_timeline_hash != media_result.result_hash
    ):
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_MEDIA_RESULT_IDENTITY_INVALID"
        )
    session.expire_all()
    workflow = session.execute(
        select(ProductionWorkflowRun)
        .where(ProductionWorkflowRun.id == workflow_run_id)
        .with_for_update()
    ).scalar_one_or_none()
    if (
        workflow is None
        or workflow.production_lane != "LONG_FORM"
        or workflow.planning_source_type != "LONG_FORM_PLAN"
        or workflow.video_project_id is None
        or workflow.production_package_artifact_version_id is None
        or workflow.production_package_hash is None
        or workflow.production_readiness_receipt_artifact_version_id is None
        or workflow.production_readiness_receipt_hash is None
        or workflow.state not in {"MEDIA_RUNNING", "BLOCKED"}
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_WORKFLOW_AUTHORITY_INVALID")
    # A pre-cutover coordinator graph cannot schedule or execute VISUAL even
    # if a package is replayed after the policy catalog was upgraded.  Do not
    # create provider authority that the active stage graph cannot consume.
    from app.contracts.production_workflow import ProductionWorkflowStage
    from app.services.production_workflow import STAGE_SEQUENCE

    if ProductionWorkflowStage.VISUAL not in STAGE_SEQUENCE:
        return None
    package_version = session.get(
        ArtifactVersion, workflow.production_package_artifact_version_id
    )
    if (
        package_version is None
        or package_version.content_hash != workflow.production_package_hash
        or not isinstance(package_version.content, dict)
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_PACKAGE_HASH_DRIFT")
    raw_package = package_version.content
    raw_package_policy = (
        raw_package.get("production_visual_policy_version"),
        raw_package.get("production_visual_policy_ref"),
        raw_package.get("production_visual_policy_hash"),
    )
    raw_active_routes = raw_package.get("active_primary_visual_routes")
    if raw_package_policy == (None, None, None) and not raw_active_routes:
        # Make the admission boundary independent of the current visual-policy
        # catalog: an immutable historical package cannot opt itself into paid
        # visual production merely because the deployment was upgraded.
        return None

    policy = _active_policy()
    package = ProductionPackageService(session).validate_for_readiness(
        workflow.production_package_artifact_version_id
    )
    package_policy = (
        package.production_visual_policy_version,
        package.production_visual_policy_ref,
        package.production_visual_policy_hash,
    )
    if (
        package_policy == (None, None, None)
        and not package.active_primary_visual_routes
    ):
        # Historical/pre-cutover packages have no AI-primary visual authority.
        # They must not acquire a new provider budget merely because MEDIA was
        # recovered under current code.
        return None
    if (
        package.production_visual_policy_version != policy["version"]
        or package.production_visual_policy_ref != policy["ref"]
        or package.production_visual_policy_hash != policy["hash"]
        or package.active_primary_visual_routes != ["AI_IMAGE", "AI_VIDEO"]
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_PACKAGE_POLICY_DRIFT")

    ledger = session.scalar(
        select(V2ProductionEffectLedger).where(
            V2ProductionEffectLedger.workflow_run_id == workflow.id,
            V2ProductionEffectLedger.stage == "MEDIA",
        )
    )
    journal = dict(ledger.effect_journal or {}) if ledger is not None else {}
    if (
        ledger is None
        or ledger.state != "VERIFIED"
        or ledger.adapter_key != _REAL_MEDIA_ADAPTER
        or ledger.result_type != _REAL_MEDIA_RESULT
        or ledger.result_ref != media_result.result_ref
        or ledger.result_hash != media_result.result_hash
        or journal.get("state") != "VERIFIED"
        or journal.get("timeline_hash") != media_result.result_hash
        or journal.get("subtitle_qc_state") != "PASS"
        or ledger.video_project_id != workflow.video_project_id
        or ledger.production_package_artifact_version_id
        != workflow.production_package_artifact_version_id
        or ledger.production_package_hash != workflow.production_package_hash
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_MEDIA_LEDGER_DRIFT")

    project = session.get(VideoProject, workflow.video_project_id)
    policy_snapshot = (
        session.get(CompiledChannelPolicySnapshot, project.policy_snapshot_id)
        if project is not None
        else None
    )
    try:
        channel_policy = ChannelScopedPolicy.model_validate(
            (policy_snapshot.compiled_payload or {}).get("channel_scoped_policy")
            if policy_snapshot is not None
            else None
        )
    except ValidationError as exc:
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_CHANNEL_BUDGET_POLICY_INVALID"
        ) from exc
    if (
        project is None
        or project.channel_workspace_id != workflow.channel_workspace_id
        or policy_snapshot is None
        or policy_snapshot.id != project.policy_snapshot_id
        or policy_snapshot.channel_workspace_id != project.channel_workspace_id
        or policy_snapshot.status not in {"active", "approved"}
        or channel_policy.policy_status != "APPROVED"
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_CHANNEL_POLICY_DRIFT")

    configured_root = os.getenv("VCOS_V2_PRODUCTION_ROOT")
    root = (
        workspace_root
        if workspace_root is not None
        else Path(configured_root)
        if configured_root
        else Path(__file__).resolve().parents[2] / "var" / "v2-production"
    )
    root = root.resolve(strict=True)
    timeline_path = _root_file(root, _required_text(journal, "timeline_relative_path"))
    audio_path = _root_file(root, _required_text(journal, "audio_relative_path"))
    caption_path = _root_file(root, _required_text(journal, "caption_relative_path"))
    try:
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_TIMELINE_FILE_INVALID"
        ) from exc
    timed_words = _sidecar_version(
        session,
        project_id=project.id,
        journal=journal,
        id_key="timed_words_artifact_version_id",
        hash_key="timed_words_hash",
        ref_key="timed_words_ref",
        artifact_type="v2_timed_words",
    )
    caption = _sidecar_version(
        session,
        project_id=project.id,
        journal=journal,
        id_key="caption_artifact_version_id",
        hash_key="caption_artifact_hash",
        ref_key="caption_ref",
        artifact_type="v2_caption_srt",
    )
    subtitle_qc = _sidecar_version(
        session,
        project_id=project.id,
        journal=journal,
        id_key="subtitle_qc_artifact_version_id",
        hash_key="subtitle_qc_hash",
        ref_key="subtitle_qc_ref",
        artifact_type="v2_subtitle_qc",
    )
    duration_ms = (
        int(timeline.get("duration_ms") or 0) if isinstance(timeline, dict) else 0
    )
    if (
        not isinstance(timeline, dict)
        or content_hash(timeline) != media_result.result_hash
        or _sha256_file(timeline_path) != journal.get("timeline_file_checksum")
        or _sha256_file(audio_path) != journal.get("audio_checksum")
        or _sha256_file(caption_path) != journal.get("caption_checksum")
        or timeline.get("timeline_ref") != media_result.result_ref
        or str(timeline.get("workflow_run_id")) != str(workflow.id)
        or str(timeline.get("video_project_id")) != str(project.id)
        or str(timeline.get("production_package_artifact_version_id"))
        != str(workflow.production_package_artifact_version_id)
        or timeline.get("production_package_hash") != workflow.production_package_hash
        or timeline.get("audio_asset_ref") != journal.get("audio_asset_ref")
        or timeline.get("audio_checksum") != journal.get("audio_checksum")
        or duration_ms <= 0
        or timeline.get("timed_words_ref") != journal.get("timed_words_ref")
        or timeline.get("caption_ref") != journal.get("caption_ref")
        or timeline.get("caption_artifact_hash") != caption.content_hash
        or timeline.get("caption_checksum") != journal.get("caption_checksum")
        or timeline.get("subtitle_qc_ref") != journal.get("subtitle_qc_ref")
        or timeline.get("subtitle_qc_hash") != subtitle_qc.content_hash
        or timeline.get("subtitle_qc_state") != "PASS"
        or timed_words.content_hash != journal.get("timed_words_hash")
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SOURCE_MEDIA_DRIFT")

    visual_run_id = uuid.uuid5(_NORMAL_RUN_NAMESPACE, str(workflow.id))
    requirement = _planned_visual_cost(
        visual_run_id=visual_run_id,
        workflow=workflow,
        timeline=timeline,
        source_timeline_hash=str(media_result.result_hash),
        channel_policy=channel_policy,
    )
    support_ref = package.support_envelope_ref
    support_version = session.get(
        ArtifactVersion,
        support_ref.artifact_version_id if support_ref is not None else None,
    )
    try:
        envelope = V2FrozenSupportEnvelope.model_validate(
            support_version.content if support_version is not None else None
        )
    except (ValidationError, ValueError) as exc:
        raise ValidationFailureError(
            "V2_NORMAL_AI_VISUAL_SUPPORT_AUTHORITY_INVALID"
        ) from exc
    if (
        support_ref is None
        or support_version is None
        or support_version.content_hash != support_ref.content_hash
        or envelope.production_visual_policy_ref != policy["ref"]
        or envelope.production_visual_policy_hash != policy["hash"]
        or envelope.active_primary_visual_routes != ["AI_IMAGE", "AI_VIDEO"]
    ):
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_SUPPORT_AUTHORITY_DRIFT")
    media_budget = session.scalar(
        select(MR1MonthlyBudgetReservation).where(
            MR1MonthlyBudgetReservation.run_id == workflow.id
        )
    )
    combined_authority = session.scalar(
        select(CombinedReplacementBudgetAuthority).where(
            CombinedReplacementBudgetAuthority.video_project_id == project.id,
            CombinedReplacementBudgetAuthority.support_envelope_hash
            == support_version.content_hash,
            CombinedReplacementBudgetAuthority.budget_reservation_ref
            == envelope.zero_cost_budget.reservation_ref,
        )
    )
    combined_authority = _require_normal_combined_budget(
        workflow=workflow,
        project=project,
        media_budget=media_budget,
        envelope=envelope,
        channel_policy=channel_policy,
        authority=combined_authority,
        requirement=requirement,
    )
    existing = session.scalar(
        select(AIVisualProductionRun).where(
            AIVisualProductionRun.workflow_run_id == workflow.id,
            AIVisualProductionRun.execution_kind == "NORMAL_PRODUCTION",
        )
    )
    if existing is not None:
        budget = session.get(
            MR1MonthlyBudgetReservation, existing.budget_reservation_id
        )
        return _validate_existing(
            workflow=workflow,
            visual_run=existing,
            budget=budget,
            media_result=media_result,
            journal=journal,
            policy=policy,
            visual_run_id=visual_run_id,
            audio_duration_ms=duration_ms,
            requirement=requirement,
            authority=combined_authority,
        )
    if workflow.ai_visual_production_run_id is not None:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_WORKFLOW_ALREADY_BOUND")

    # The aggregate reservation was created before MEDIA.  Binding the normal
    # visual run to it is consumption of its Gemini/Veo partitions, not a
    # second reservation or a second monthly-capacity charge.
    budget = media_budget
    if budget is None:
        raise ValidationFailureError("V2_NORMAL_AI_VISUAL_BUDGET_RESERVATION_INVALID")
    now = clock()
    visual_run = AIVisualProductionRun(
        id=visual_run_id,
        workflow_run_id=workflow.id,
        video_project_id=project.id,
        rerender_authority_id=None,
        execution_kind="NORMAL_PRODUCTION",
        production_package_artifact_version_id=workflow.production_package_artifact_version_id,
        production_package_hash=workflow.production_package_hash,
        production_visual_policy_version=policy["version"],
        production_visual_policy_ref=policy["ref"],
        production_visual_policy_hash=policy["hash"],
        source_timeline_ref=str(media_result.result_ref),
        source_timeline_hash=str(media_result.result_hash),
        audio_ref=_required_text(journal, "audio_relative_path"),
        audio_checksum=_required_text(journal, "audio_checksum"),
        audio_duration_ms=duration_ms,
        timed_words_ref=_required_text(journal, "timed_words_ref"),
        timed_words_hash=timed_words.content_hash,
        caption_ref=_required_text(journal, "caption_relative_path"),
        caption_hash=caption.content_hash,
        caption_checksum=_required_text(journal, "caption_checksum"),
        subtitle_qc_ref=_required_text(journal, "subtitle_qc_ref"),
        subtitle_qc_hash=subtitle_qc.content_hash,
        budget_reservation_id=budget.id,
        budget_reservation_ref=budget.reservation_ref,
        budget_authority_hash=combined_authority.content_hash,
        state="AUTHORIZED",
        current_phase="AUTHORIZE",
        projection_version=1,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(visual_run)
    session.flush()
    workflow.ai_visual_production_run_id = visual_run.id
    workflow.ai_visual_policy_ref = policy["ref"]
    workflow.ai_visual_policy_hash = policy["hash"]
    workflow.projection_version += 1
    workflow.last_progress_at = now
    session.flush()
    return NormalAIVisualAuthorization(
        visual_production_run_id=visual_run.id,
        budget_reservation_id=budget.id,
        budget_reservation_ref=budget.reservation_ref,
        replayed=False,
    )


__all__ = [
    "NORMAL_AI_VISUAL_MAXIMUM_COST_USD",
    "NORMAL_AI_VISUAL_MAXIMUM_IMAGES",
    "NORMAL_AI_VISUAL_MAXIMUM_VIDEOS",
    "NormalAIVisualAuthorization",
    "authorize_normal_ai_visual_after_verified_media",
]
