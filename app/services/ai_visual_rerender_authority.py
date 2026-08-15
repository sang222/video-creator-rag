"""Derived execution authority for one governed AI-only visual replacement.

The historical production package is intentionally immutable and was sealed
before the AI-only visual policy existed.  This module therefore does not
rewrite or reinterpret that package.  It revalidates an append-only
``AIVisualRerenderAuthority`` and derives the narrow VISUAL/RENDER/QC/ARCHIVE
operation plan authorized by that row and its fresh monthly budget.

Both the workflow coordinator and the provider gateway use this resolver so a
stage cannot see a different provider or budget projection at the two effect
boundaries.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import (
    VEO_DEFAULT_DURATION_SECONDS,
    VEO_DEFAULT_MODEL_ID,
    VEO_DEFAULT_OUTPUT_COUNT,
    VEO_DEFAULT_RESOLUTION,
)
from app.core.errors import ValidationFailureError
from app.db.models.ai_visual import (
    AI_VISUAL_POLICY_VERSION,
    AIVisualProductionRun,
    AIVisualRerenderAuthority,
)
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.v2_effect import (
    V2NarrationTimingRecoveryAuthority,
    V2NarrationTimingRecoveryReceipt,
)
from app.db.models.voice_authority import CombinedReplacementBudgetAuthority
from app.db.models.workflow import Artifact, ArtifactVersion
from app.services.config_registry import ConfigRegistryService
from app.services.combined_replacement_budget import (
    CombinedReplacementBudgetAuthorityService,
)
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.production_package import ProductionPackageService, semantic_hash
from app.services.v2_ai_visual_provider import (
    V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD,
)


AI_VISUAL_POLICY_REF = (
    "config://production_visual_policy_catalog/2026-08-13/active-real-long-form-ai-only"
)
AI_VISUAL_RERENDER_AUTHORITY_SCHEMA = "vcos.ai-visual-rerender-authority.v1"
AI_VISUAL_RERENDER_PROVIDER_PLAN_SCHEMA = "vcos.post-readiness-provider-plan.v2"
AI_VISUAL_RERENDER_BUDGET_SCHEMA = "vcos.operation-budget-authority.v1"
AI_VISUAL_RERENDER_ADAPTER_OPERATION_SCHEMA = "vcos.provider-adapter-operation.v1"
AI_VISUAL_RERENDER_EXECUTION_MODE = "REAL_LONG_FORM_PRODUCTION"
AI_VISUAL_RERENDER_MAXIMUM_SCENES = 46
AI_VISUAL_RERENDER_MAXIMUM_IMAGES = 14
AI_VISUAL_RERENDER_MAXIMUM_VIDEOS = 0
AI_VISUAL_RERENDER_IMAGE_COST_USD = (
    V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD * AI_VISUAL_RERENDER_MAXIMUM_IMAGES
)
AI_VISUAL_RERENDER_VIDEO_COST_USD = (
    Decimal("0.800000") * AI_VISUAL_RERENDER_MAXIMUM_VIDEOS
)
AI_VISUAL_RERENDER_MAXIMUM_COST_USD = (
    AI_VISUAL_RERENDER_IMAGE_COST_USD + AI_VISUAL_RERENDER_VIDEO_COST_USD
)

_POLICY_PATH = Path("config/production_visual_policy_catalog.yaml")
_HASH_FIELD = "authority_hash"
_ALLOWED_BUDGET_STATES = frozenset({"RESERVED", "SUBMITTED", "SETTLED_CONSERVATIVE"})


def _durable_visual_allocations(
    authority: AIVisualRerenderAuthority,
) -> dict[str, Decimal]:
    """Price the authority's durable image/video caps, never module constants."""

    video_unit = GoogleVeoModelPriceCatalog().estimate(
        model_id=VEO_DEFAULT_MODEL_ID,
        resolution=VEO_DEFAULT_RESOLUTION,
        duration_seconds=VEO_DEFAULT_DURATION_SECONDS,
        output_count=VEO_DEFAULT_OUTPUT_COUNT,
        hard_cap=Decimal("1000000"),
        approval_amount=Decimal("1000000"),
    ).estimated_amount
    return {
        key: value
        for key, value in {
            "google_gemini_image": (
                V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD
                * authority.maximum_image_submissions
            ),
            "google_veo": video_unit * authority.maximum_video_submissions,
        }.items()
        if value > 0
    }


@dataclass(frozen=True, slots=True)
class GovernedAIVisualRerenderExecutionAuthority:
    authority: AIVisualRerenderAuthority
    visual_run: AIVisualProductionRun
    source_workflow: ProductionWorkflowRun
    replacement_workflow: ProductionWorkflowRun
    budget: MR1MonthlyBudgetReservation
    combined_budget_authority: CombinedReplacementBudgetAuthority
    visual_partition: dict[str, Any]
    provider_plan: dict[str, Any]
    budget_plan: dict[str, Any]


def active_ai_visual_policy_authority() -> dict[str, Any]:
    """Return the exact reviewed catalog identity without touching the DB."""

    loaded = ConfigRegistryService(None).validate_catalog(_POLICY_PATH)
    items = loaded.content.get("items") or []
    if len(items) != 1 or not isinstance(items[0], dict):
        raise ValidationFailureError("AI_VISUAL_RERENDER_POLICY_INVALID")
    item = items[0]
    ref = (
        "config://production_visual_policy_catalog/"
        f"{loaded.catalog_version}/{item.get('key')}"
    )
    if (
        ref != AI_VISUAL_POLICY_REF
        or item.get("policy_version") != AI_VISUAL_POLICY_VERSION
        or item.get("status") != "ACTIVE"
        or item.get("production_visual_origin") != "AI_GENERATED"
        or item.get("allowed_primary_routes") != ["AI_IMAGE", "AI_VIDEO"]
        or item.get("renderer_policy", {}).get("assembly_only") is not True
        or item.get("renderer_policy", {}).get("primary_visual_generation") is not False
        or item.get("fallback_policy", {}).get("native_fallback_allowed") is not False
        or item.get("fallback_policy", {}).get("stock_fallback_allowed") is not False
        or item.get("fallback_policy", {}).get("screenshot_fallback_allowed")
        is not False
    ):
        raise ValidationFailureError("AI_VISUAL_RERENDER_POLICY_INVALID")
    return {
        "version": AI_VISUAL_POLICY_VERSION,
        "ref": ref,
        "hash": loaded.content_hash,
        "routes": ["AI_IMAGE", "AI_VIDEO"],
        "content": copy.deepcopy(item),
    }


def ai_visual_rerender_authority_body(
    value: AIVisualRerenderAuthority | Mapping[str, Any],
) -> dict[str, Any]:
    """Canonical full-row hash body used before insert and on every replay."""

    fields = tuple(
        column.name
        for column in AIVisualRerenderAuthority.__table__.columns
        if column.name != _HASH_FIELD
    )
    body: dict[str, Any] = {
        "schema_version": AI_VISUAL_RERENDER_AUTHORITY_SCHEMA,
    }
    for field in fields:
        raw = value.get(field) if isinstance(value, Mapping) else getattr(value, field)
        body[field] = _canonical_value(raw)
    return body


def seal_ai_visual_rerender_authority_hash(
    value: AIVisualRerenderAuthority | Mapping[str, Any],
) -> str:
    return semantic_hash(ai_visual_rerender_authority_body(value))


def resolve_governed_ai_visual_rerender_execution_authority(
    session: Session,
    *,
    workflow_run_id: uuid.UUID,
    required: bool = False,
) -> GovernedAIVisualRerenderExecutionAuthority | None:
    """Resolve and revalidate the exact immutable replacement authority.

    ``None`` is returned only for a workflow that has no AI visual run, or for
    a normal (non-rerender) AI visual run.  A partial or drifted governed
    lineage always raises instead of falling back to the historical package.
    """

    replacement = session.get(ProductionWorkflowRun, workflow_run_id)
    if replacement is None:
        if required:
            raise ValidationFailureError("AI_VISUAL_RERENDER_WORKFLOW_REQUIRED")
        return None
    if replacement.ai_visual_production_run_id is None:
        if required:
            raise ValidationFailureError("AI_VISUAL_RERENDER_RUN_REQUIRED")
        return None
    visual_run = session.get(
        AIVisualProductionRun, replacement.ai_visual_production_run_id
    )
    if visual_run is None:
        raise ValidationFailureError("AI_VISUAL_RERENDER_RUN_MISSING")
    if visual_run.execution_kind != "GOVERNED_RERENDER":
        if required:
            raise ValidationFailureError("AI_VISUAL_GOVERNED_RERENDER_REQUIRED")
        return None
    if visual_run.rerender_authority_id is None:
        raise ValidationFailureError("AI_VISUAL_RERENDER_AUTHORITY_REQUIRED")
    authority = session.get(AIVisualRerenderAuthority, visual_run.rerender_authority_id)
    if authority is None:
        raise ValidationFailureError("AI_VISUAL_RERENDER_AUTHORITY_MISSING")
    source = session.get(ProductionWorkflowRun, authority.source_workflow_run_id)
    budget = session.get(MR1MonthlyBudgetReservation, authority.budget_reservation_id)
    if source is None or budget is None:
        raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_AUTHORITY_MISSING")
    combined = session.scalar(
        select(CombinedReplacementBudgetAuthority).where(
            CombinedReplacementBudgetAuthority.video_project_id
            == authority.video_project_id,
            CombinedReplacementBudgetAuthority.budget_reservation_id == budget.id,
            CombinedReplacementBudgetAuthority.budget_reservation_ref
            == budget.reservation_ref,
            CombinedReplacementBudgetAuthority.content_hash
            == authority.budget_authority_hash,
        )
    )
    if combined is None:
        raise ValidationFailureError(
            "AI_VISUAL_RERENDER_COMBINED_BUDGET_AUTHORITY_REQUIRED"
        )

    policy = active_ai_visual_policy_authority()
    try:
        visual_partition = (
            CombinedReplacementBudgetAuthorityService.governed_rerender_visual_partition(
                authority=combined,
                reservation=budget,
                production_visual_policy_ref=str(policy["ref"]),
                production_visual_policy_hash=str(policy["hash"]),
                image_owner_count=authority.maximum_image_submissions,
                video_owner_count=authority.maximum_video_submissions,
            )
        )
    except ValidationFailureError as exc:
        raise ValidationFailureError(
            "AI_VISUAL_RERENDER_COMBINED_VISUAL_PARTITION_REQUIRED"
        ) from exc
    _validate_exact_authority(
        session=session,
        authority=authority,
        visual_run=visual_run,
        source=source,
        replacement=replacement,
        budget=budget,
        combined=combined,
        visual_partition=visual_partition,
        policy=policy,
    )
    final_review = _source_final_review_projection(session, source=source)
    provider_plan, budget_plan = _derived_operation_plans(
        authority=authority,
        visual_run=visual_run,
        replacement=replacement,
        budget=budget,
        combined=combined,
        visual_partition=visual_partition,
        policy=policy,
        final_review=final_review,
    )
    return GovernedAIVisualRerenderExecutionAuthority(
        authority=authority,
        visual_run=visual_run,
        source_workflow=source,
        replacement_workflow=replacement,
        budget=budget,
        combined_budget_authority=combined,
        visual_partition=visual_partition,
        provider_plan=provider_plan,
        budget_plan=budget_plan,
    )


def _validate_exact_authority(
    *,
    session: Session,
    authority: AIVisualRerenderAuthority,
    visual_run: AIVisualProductionRun,
    source: ProductionWorkflowRun,
    replacement: ProductionWorkflowRun,
    budget: MR1MonthlyBudgetReservation,
    combined: CombinedReplacementBudgetAuthority,
    visual_partition: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> None:
    timing = session.get(
        V2NarrationTimingRecoveryAuthority,
        authority.narration_timing_recovery_authority_id,
    )
    timing_receipt = session.get(
        V2NarrationTimingRecoveryReceipt,
        authority.narration_timing_recovery_receipt_id,
    )
    old_candidate = session.get(
        FinalReviewCandidate, authority.rejected_final_review_candidate_id
    )
    allocations = {
        str(key): Decimal(str(value))
        for key, value in dict(budget.provider_allocations_json or {}).items()
    }
    durable_allocations = _durable_visual_allocations(authority)
    durable_total = sum(durable_allocations.values(), Decimal("0"))
    aggregate_allocations = {
        "elevenlabs": Decimal(combined.new_tts_projected_cost_usd)
        + Decimal(combined.forced_alignment_projected_cost_usd),
        **(
            {"google_gemini_image": Decimal(combined.ai_image_projected_cost_usd)}
            if Decimal(combined.ai_image_projected_cost_usd) > 0
            else {}
        ),
        **(
            {"google_veo": Decimal(combined.ai_video_projected_cost_usd)}
            if Decimal(combined.ai_video_projected_cost_usd) > 0
            else {}
        ),
    }
    if (
        authority.authority_hash != seal_ai_visual_rerender_authority_hash(authority)
        or authority.authorized_visual_production_run_id != visual_run.id
        or authority.source_workflow_run_id != source.id
        or authority.replacement_workflow_run_id != replacement.id
        or source.id == replacement.id
        or visual_run.workflow_run_id != replacement.id
        or visual_run.rerender_authority_id != authority.id
        or replacement.ai_visual_production_run_id != visual_run.id
        or source.video_project_id != authority.video_project_id
        or replacement.video_project_id != authority.video_project_id
        or visual_run.video_project_id != authority.video_project_id
        or replacement.company_id != source.company_id
        or replacement.channel_workspace_id != source.channel_workspace_id
        or source.production_package_artifact_version_id
        != authority.production_package_artifact_version_id
        or replacement.production_package_artifact_version_id
        != authority.production_package_artifact_version_id
        or visual_run.production_package_artifact_version_id
        != authority.production_package_artifact_version_id
        or source.production_package_hash != authority.production_package_hash
        or replacement.production_package_hash != authority.production_package_hash
        or visual_run.production_package_hash != authority.production_package_hash
        or source.production_readiness_receipt_artifact_version_id
        != authority.production_readiness_receipt_artifact_version_id
        or replacement.production_readiness_receipt_artifact_version_id
        != authority.production_readiness_receipt_artifact_version_id
        or source.production_readiness_receipt_hash
        != authority.production_readiness_receipt_hash
        or replacement.production_readiness_receipt_hash
        != authority.production_readiness_receipt_hash
        or authority.production_visual_policy_version != policy["version"]
        or authority.production_visual_policy_ref != policy["ref"]
        or authority.production_visual_policy_hash != policy["hash"]
        or visual_run.production_visual_policy_version != policy["version"]
        or visual_run.production_visual_policy_ref != policy["ref"]
        or visual_run.production_visual_policy_hash != policy["hash"]
        or authority.maximum_total_cost_usd != durable_total
        or authority.maximum_scene_count <= 0
        or authority.maximum_image_submissions < 0
        or authority.maximum_video_submissions < 0
        or authority.maximum_image_submissions + authority.maximum_video_submissions
        <= 0
        or authority.maximum_tts_submissions != 0
        or authority.maximum_forced_alignment_submissions != 0
        or authority.automatic_publish is not False
        or budget.id != visual_run.budget_reservation_id
        or budget.id != authority.budget_reservation_id
        or budget.run_id != source.id
        or budget.video_project_id != authority.video_project_id
        or budget.company_id != replacement.company_id
        or budget.channel_workspace_id != replacement.channel_workspace_id
        or budget.reservation_ref != authority.budget_reservation_ref
        or visual_run.budget_reservation_ref != authority.budget_reservation_ref
        or authority.budget_authority_hash != combined.content_hash
        or visual_run.budget_authority_hash != authority.budget_authority_hash
        or Decimal(budget.reserved_amount)
        != Decimal(combined.combined_replacement_projected_cost_usd)
        or allocations != aggregate_allocations
        or durable_allocations
        != {
            key: Decimal(value)
            for key, value in dict(
                visual_partition.get("visual_provider_allocations_usd") or {}
            ).items()
        }
        or Decimal(visual_partition.get("maximum_total_cost_usd", "-1"))
        != durable_total
        or budget.status not in _ALLOWED_BUDGET_STATES
        or timing is None
        or timing_receipt is None
        or timing.workflow_run_id != source.id
        or timing_receipt.workflow_run_id != source.id
        or timing_receipt.authority_id != timing.id
        or timing.authority_hash != authority.narration_timing_recovery_authority_hash
        or timing_receipt.receipt_hash
        != authority.narration_timing_recovery_receipt_hash
        or timing_receipt.recovery_state != "VERIFIED"
        or timing_receipt.tts_retry_count != 0
        or timing.max_tts_retries != 0
        or authority.audio_ref != timing.audio_relative_path
        or authority.audio_checksum != timing.audio_checksum_sha256
        or authority.audio_duration_ms != timing.audio_duration_ms
        or visual_run.audio_ref != authority.audio_ref
        or visual_run.audio_checksum != authority.audio_checksum
        or visual_run.audio_duration_ms != authority.audio_duration_ms
        or visual_run.source_timeline_hash
        != timing_receipt.canonical_media_timeline_hash
        or old_candidate is None
        or old_candidate.workflow_run_id != source.id
        or old_candidate.final_media_ref_id != authority.rejected_final_media_ref_id
        or old_candidate.candidate_hash
        != authority.rejected_final_review_candidate_hash
        or old_candidate.final_media_hash != authority.rejected_final_media_hash
    ):
        raise ValidationFailureError("AI_VISUAL_RERENDER_RUNTIME_AUTHORITY_MISMATCH")

    package = ProductionPackageService(session).validate_for_readiness(
        authority.production_package_artifact_version_id
    )
    receipt = ProductionPackageService(session)._receipt_for_package(
        authority.production_package_artifact_version_id,
        authority.production_package_hash,
    )
    if (
        package.video_project_id != authority.video_project_id
        or receipt is None
        or receipt.id != authority.production_readiness_receipt_artifact_version_id
        or receipt.content_hash != authority.production_readiness_receipt_hash
    ):
        raise ValidationFailureError("AI_VISUAL_RERENDER_PACKAGE_AUTHORITY_MISMATCH")


def _source_final_review_projection(
    session: Session,
    *,
    source: ProductionWorkflowRun,
) -> dict[str, Any]:
    package_id = source.production_package_artifact_version_id
    if package_id is None:
        raise ValidationFailureError("AI_VISUAL_RERENDER_SOURCE_PACKAGE_REQUIRED")
    package = ProductionPackageService(session).validate_for_readiness(package_id)
    version_id = package.provider_execution_plan_ref.artifact_version_id
    version = session.get(ArtifactVersion, version_id) if version_id else None
    artifact = session.get(Artifact, version.artifact_id) if version else None
    content = (
        dict(version.content) if version and isinstance(version.content, dict) else {}
    )
    final_review = content.get("final_review")
    if (
        version is None
        or artifact is None
        or artifact.artifact_type != "provider_execution_plan"
        or artifact.video_project_id != source.video_project_id
        or artifact.current_version_id != version.id
        or artifact.status != "approved"
        or version.status != "approved"
        or version.content_hash != package.provider_execution_plan_ref.content_hash
        or content.get("schema_version") != AI_VISUAL_RERENDER_PROVIDER_PLAN_SCHEMA
        or content.get("execution_authorized") is not True
        or content.get("fixture_only") is not False
        or content.get("automatic_publish") is not False
        or not isinstance(final_review, dict)
    ):
        raise ValidationFailureError(
            "AI_VISUAL_RERENDER_FINAL_REVIEW_AUTHORITY_INVALID"
        )
    return copy.deepcopy(final_review)


def _derived_operation_plans(
    *,
    authority: AIVisualRerenderAuthority,
    visual_run: AIVisualProductionRun,
    replacement: ProductionWorkflowRun,
    budget: MR1MonthlyBudgetReservation,
    combined: CombinedReplacementBudgetAuthority,
    visual_partition: Mapping[str, Any],
    policy: Mapping[str, Any],
    final_review: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    operation_specs: tuple[tuple[str, str, bool, str], ...] = (
        (
            "VISUAL",
            "v2-ai-visual-production",
            True,
            format(Decimal(authority.maximum_total_cost_usd), "f"),
        ),
        ("RENDER", "v2-local-native", False, "0"),
        ("QC", "v2-local-native", False, "0"),
        ("ARCHIVE", "v2-google-drive-remote", False, "0"),
    )
    operations: dict[str, Any] = {}
    authorizations: dict[str, Any] = {}
    for stage, adapter, paid, max_cost in operation_specs:
        operation_id = f"v2-ai-rerender:{replacement.id}:{stage.lower()}"
        parameters: dict[str, Any] = {
            "mode": {
                "VISUAL": "AI_ONLY_PRIMARY_VISUAL_GENERATION",
                "RENDER": "AI_ONLY_ASSEMBLY_RENDER",
                "QC": "AI_ONLY_AUTOMATED_QC",
                "ARCHIVE": "GOOGLE_DRIVE_REMOTE_ARCHIVE",
            }[stage],
            "audio_strategy": "ELEVENLABS_FINAL_NARRATION",
            "execution_mode": AI_VISUAL_RERENDER_EXECUTION_MODE,
            "governed_rerender_authority_id": str(authority.id),
            "governed_rerender_authority_hash": authority.authority_hash,
            "visual_production_run_id": str(visual_run.id),
        }
        if stage == "VISUAL":
            parameters["provider_execution"] = {
                "provider": "ai_visual_scene_effects",
                "credential_ref": "env://GEMINI_API_KEY",
                "routes": list(policy["routes"]),
                "active_primary_visual_routes": list(policy["routes"]),
                "image_provider": "google_gemini_image",
                "video_provider": "google_veo",
                "maximum_scene_count": authority.maximum_scene_count,
                "maximum_image_submissions": authority.maximum_image_submissions,
                "maximum_video_submissions": authority.maximum_video_submissions,
                "attempt_limit": 1,
                "attempt_limit_per_asset_slot": 1,
                "automatic_provider_retry": False,
                "fallback_allowed": False,
                "native_fallback_allowed": False,
                "stock_fallback_allowed": False,
                "screenshot_fallback_allowed": False,
                "production_visual_policy_ref": policy["ref"],
                "production_visual_policy_hash": policy["hash"],
                "idempotency_key": f"{operation_id}:ai-visual-asset-set",
                "estimated_cost_usd": max_cost,
                "budget_reservation_ref": budget.reservation_ref,
                "combined_replacement_budget_authority": (
                    visual_partition["combined_replacement_budget_authority"]
                ),
                "aggregate_budget_reservation_ref": budget.reservation_ref,
                "aggregate_budget_authority_hash": combined.content_hash,
                "visual_provider_allocations_usd": (
                    visual_partition["visual_provider_allocations_usd"]
                ),
                "aggregate_provider_allocations_usd": (
                    visual_partition["aggregate_provider_allocations_usd"]
                ),
                "mr1_occupancy": visual_partition["occupancy"],
                "rerender_authority_hash": authority.authority_hash,
            }
        elif stage == "ARCHIVE":
            parameters["provider_execution"] = {
                "provider": "google_drive",
                "credential_ref": "oauth://google-drive/channel-connected",
                "attempt_limit": 1,
                "idempotency_key": f"{operation_id}:google-drive-archive",
                "remote_object_required": True,
                "checksum_readback_required": True,
                "budget_reservation_ref": budget.reservation_ref,
                "rerender_authority_hash": authority.authority_hash,
            }
        operations[stage] = {
            "schema_version": AI_VISUAL_RERENDER_ADAPTER_OPERATION_SCHEMA,
            "execution_authorized": True,
            "production_eligible": True,
            "fixture_only": False,
            "invokes_mr1": False,
            "automatic_publish": False,
            "stage": stage,
            "production_lane": replacement.production_lane,
            "execution_mode": AI_VISUAL_RERENDER_EXECUTION_MODE,
            "paid_provider_call": paid,
            "operation_id": operation_id,
            "adapter_key": adapter,
            "max_cost_usd": max_cost,
            "parameters": parameters,
        }
        authorizations[operation_id] = {
            "authorized": True,
            "operation_id": operation_id,
            "adapter_key": adapter,
            "stage": stage,
            "paid_provider_call": paid,
            "execution_mode": AI_VISUAL_RERENDER_EXECUTION_MODE,
            "max_cost_usd": max_cost,
        }

    lineage = {
        "schema_version": "vcos.ai-visual-rerender-execution-lineage.v1",
        "rerender_authority_id": str(authority.id),
        "rerender_authority_hash": authority.authority_hash,
        "source_workflow_run_id": str(authority.source_workflow_run_id),
        "replacement_workflow_run_id": str(authority.replacement_workflow_run_id),
        "visual_production_run_id": str(visual_run.id),
        "production_package_artifact_version_id": str(
            authority.production_package_artifact_version_id
        ),
        "production_package_hash": authority.production_package_hash,
        "budget_reservation_id": str(budget.id),
        "budget_authority_hash": authority.budget_authority_hash,
        "automatic_publish": False,
    }
    provider_plan = {
        "schema_version": AI_VISUAL_RERENDER_PROVIDER_PLAN_SCHEMA,
        "result": "PASS",
        "execution_authorized": True,
        "retry_authorized": False,
        "visual_resume_authorized": True,
        "scene_effect_max_attempts": 1,
        "provider_retry_authorized": False,
        "max_attempts": 1,
        "retry_cost_usd": "0",
        "production_lane": replacement.production_lane,
        "execution_mode": AI_VISUAL_RERENDER_EXECUTION_MODE,
        "fixture_only": False,
        "invokes_mr1": False,
        "automatic_publish": False,
        "paid_provider_calls": True,
        "final_tts_provider": "REUSED_ELEVENLABS_NO_NEW_CALL",
        "archive_provider": "GOOGLE_DRIVE",
        "visual_provider_plan": ["google_gemini_image", "google_veo"],
        "production_visual_policy_ref": policy["ref"],
        "production_visual_policy_hash": policy["hash"],
        "active_primary_visual_routes": list(policy["routes"]),
        "adapter_operations": operations,
        "final_review": copy.deepcopy(dict(final_review)),
        "lineage": lineage,
    }
    budget_plan = {
        "schema_version": AI_VISUAL_RERENDER_BUDGET_SCHEMA,
        "result": "PASS",
        "budget_authorized": True,
        "retry_authorized": False,
        "visual_resume_authorized": True,
        "scene_effect_max_attempts": 1,
        "max_attempts": 1,
        "retry_cost_usd": "0",
        "remaining_budget_usd": (
            format(Decimal(authority.maximum_total_cost_usd), "f")
            if budget.status in {"RESERVED", "SUBMITTED"}
            else "0"
        ),
        # A settled VISUAL event may be replayed only to assemble already
        # VERIFIED slot effects.  The gateway recognizes this separate ceiling
        # while the stage/store forbid a new submission after settlement.
        "visual_reconciliation_ceiling_usd": format(
            Decimal(authority.maximum_total_cost_usd),
            "f",
        ),
        "execution_mode": AI_VISUAL_RERENDER_EXECUTION_MODE,
        "operation_authorizations": authorizations,
        "budget_reservation_id": str(budget.id),
        "budget_reservation_ref": budget.reservation_ref,
        "budget_authority_hash": authority.budget_authority_hash,
        "budget_status": budget.status,
        "mr1_occupancy": visual_partition["occupancy"],
        "visual_provider_allocations_usd": copy.deepcopy(
            dict(visual_partition["visual_provider_allocations_usd"])
        ),
        "aggregate_provider_allocations_usd": copy.deepcopy(
            dict(visual_partition["aggregate_provider_allocations_usd"])
        ),
        "provider_allocations_usd": copy.deepcopy(
            dict(budget.provider_allocations_json or {})
        ),
        "lineage": lineage,
    }
    return provider_plan, budget_plan


def _canonical_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    return value
