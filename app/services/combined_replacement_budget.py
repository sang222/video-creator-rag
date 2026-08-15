"""Durable, package-time combined replacement cost authority.

This is intentionally a server-side compiler.  Callers cannot supply a cost
map: it resolves the frozen narration projection, the current package visual
preflight, route receipts, reviewed runtime provider prices, and the already
reserved policy ceiling into one append-only row before MEDIA can execute.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ValidationFailureError
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.voice_authority import (
    CombinedReplacementBudgetAuthority,
    TTSPerformanceProjection,
)
from app.services.config_registry import content_hash
from app.services.google_gemini_image_catalog import GoogleGeminiImageModelPriceCatalog
from app.services.google_veo_catalog import GoogleVeoModelPriceCatalog
from app.services.v2_ai_visual_provider import (
    V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD,
    V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS,
    V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD,
    V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS,
    V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES,
    V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD,
)
from app.services.v2_ai_visual_stage import (
    V2_AI_VISUAL_PROVIDER_KEY,
    V2_AI_VISUAL_VIDEO_PROVIDER_KEY,
    compile_pre_tts_ai_visual_cost_preflight,
)

_SCHEMA = "vcos.combined-replacement-budget.v1"
_MONEY_QUANTUM = Decimal("0.000001")
_AUTHORITY_NAMESPACE = uuid.UUID("f859ea48-2bbe-504f-9b82-07df2d65a3f6")


def _money(value: Any, *, code: str, positive: bool = False) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationFailureError(code) from exc
    if not amount.is_finite() or amount < 0 or (positive and amount <= 0):
        raise ValidationFailureError(code)
    try:
        return amount.quantize(_MONEY_QUANTUM)
    except InvalidOperation as exc:
        raise ValidationFailureError(code) from exc


def _money_text(value: Decimal) -> str:
    return format(value.quantize(_MONEY_QUANTUM), "f")


def _segment_text_cost(
    *, projection: TTSPerformanceProjection, canonical_narration: str, rate: Decimal
) -> tuple[Decimal, dict[str, Any]]:
    total_characters = 0
    source_segments: list[dict[str, Any]] = []
    for expected, raw in enumerate(projection.segments, start=1):
        if not isinstance(raw, Mapping):
            raise ValidationFailureError("COMBINED_REPLACEMENT_TTS_PROJECTION_INVALID")
        try:
            ordinal = int(raw["ordinal"])
            start = int(raw["source_text_start"])
            end = int(raw["source_text_end"])
            segment_id = str(raw["segment_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_TTS_PROJECTION_INVALID"
            ) from exc
        text = canonical_narration[start:end]
        if (
            ordinal != expected
            or not segment_id
            or start < 0
            or end <= start
            or end > len(canonical_narration)
            or raw.get("text_hash") != content_hash({"text": text})
        ):
            raise ValidationFailureError("COMBINED_REPLACEMENT_TTS_PROJECTION_DRIFT")
        total_characters += len(text)
        source_segments.append(
            {
                "segment_id": segment_id,
                "ordinal": ordinal,
                "text_hash": str(raw["text_hash"]),
                "character_count": len(text),
            }
        )
    if not source_segments:
        raise ValidationFailureError("COMBINED_REPLACEMENT_TTS_SEGMENTS_REQUIRED")
    return _money(
        rate * total_characters, code="COMBINED_REPLACEMENT_TTS_COST_INVALID"
    ), {
        "provider": "elevenlabs",
        "pricing_basis": "CURRENT_REVIEWED_RUNTIME_RATE_PER_CANONICAL_CHARACTER",
        "rate_usd_per_character": _money_text(rate),
        "canonical_character_count": total_characters,
        "segments": source_segments,
    }


def _current_visual_prices() -> tuple[Decimal, Decimal, dict[str, Any]]:
    """Resolve the two current catalog prices; neither can silently be zero."""
    image_catalog = GoogleGeminiImageModelPriceCatalog()
    image_row = image_catalog.row(
        model_id="gemini-3.1-flash-image", image_size="2K", aspect_ratio="16:9"
    )
    catalog_unit = _money(
        image_row.get("estimated_unit_cost_usd"),
        code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        positive=True,
    )
    semantic_upper_bound = (
        Decimal(V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES)
        * V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD
        + Decimal(V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS - V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS)
        * V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD
    ) / Decimal(1000000)
    image_unit = _money(
        V2_GEMINI_IMAGE_CONSERVATIVE_UNIT_COST_USD,
        code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        positive=True,
    )
    veo_catalog = GoogleVeoModelPriceCatalog()
    video_unit = _money(
        veo_catalog.estimate(
            model_id="veo-3.1-fast-generate-preview",
            resolution="720p",
            duration_seconds=8,
            output_count=1,
            hard_cap=Decimal("1000000"),
            approval_amount=Decimal("1000000"),
        ).estimated_amount,
        code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        positive=True,
    )
    source = {
        "image_price_catalog_ref": image_catalog.ref,
        "image_price_catalog_hash": content_hash(image_catalog.payload),
        "image_model_id": "gemini-3.1-flash-image",
        "image_size": "2K",
        "image_aspect_ratio": "16:9",
        "image_catalog_unit_cost_usd": _money_text(catalog_unit),
        "image_semantic_input_upper_bound_usd": _money_text(semantic_upper_bound),
        "image_unit_projected_cost_usd": _money_text(image_unit),
        "video_price_catalog_ref": veo_catalog.ref,
        "video_price_catalog_hash": content_hash(veo_catalog.payload),
        "video_model_id": "veo-3.1-fast-generate-preview",
        "video_resolution": "720p",
        "video_duration_seconds": "8",
        "video_output_count": 1,
        "video_unit_projected_cost_usd": _money_text(video_unit),
    }
    return image_unit, video_unit, source


def _active_visual_policy_hash() -> str:
    from pathlib import Path

    from app.services.config_registry import ConfigRegistryService

    loaded = ConfigRegistryService(None).validate_catalog(
        Path(__file__).resolve().parents[2]
        / "config"
        / "production_visual_policy_catalog.yaml"
    )
    return loaded.content_hash


class CombinedReplacementBudgetAuthorityService:
    """Freeze and replay the exact combined cost authority for one package."""

    def __init__(self, session: Session, *, settings: Settings | Any | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def compile_normal_visual_preflight(
        self,
        *,
        project_id: uuid.UUID,
        projection: TTSPerformanceProjection,
        canonical_narration: str,
        estimated_duration_ms: int,
        production_visual_policy_ref: str,
        production_visual_policy_hash: str,
        maximum_image_submissions: int,
        maximum_video_submissions: int,
    ) -> dict[str, Any]:
        """Produce the one pre-TTS visual/cost projection for normal production.

        This is intentionally compiled through the production
        ``UnifiedAIVisualPlanner`` path.  It records unique owner slots, not
        script sections, and therefore carries both Gemini-image and Veo
        authority when the capability projection selects video.
        """

        if (
            projection.video_project_id != project_id
            or projection.state != "FROZEN"
            or not production_visual_policy_ref
            or len(production_visual_policy_hash) != 64
            or production_visual_policy_hash != _active_visual_policy_hash()
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_VISUAL_POLICY_AUTHORITY_REQUIRED"
            )
        tts_rate = _money(
            getattr(self.settings, "elevenlabs_tts_cost_per_character_usd", None),
            code="COMBINED_REPLACEMENT_TTS_COST_AUTHORITY_REQUIRED",
            positive=True,
        )
        alignment_cost = _money(
            getattr(self.settings, "elevenlabs_forced_alignment_cost_usd", None),
            code="COMBINED_REPLACEMENT_ALIGNMENT_COST_AUTHORITY_REQUIRED",
            positive=True,
        )
        tts_cost, tts_source = _segment_text_cost(
            projection=projection,
            canonical_narration=canonical_narration,
            rate=tts_rate,
        )
        artifacts, preflight_timeline = compile_pre_tts_ai_visual_cost_preflight(
            video_project_id=project_id,
            preflight_id=projection.id,
            canonical_narration=canonical_narration,
            estimated_duration_ms=estimated_duration_ms,
            maximum_image_submissions=maximum_image_submissions,
            maximum_video_submissions=maximum_video_submissions,
        )
        image_count = artifacts.scene_plan.unique_ai_image_asset_slot_count
        video_count = artifacts.scene_plan.unique_ai_video_asset_slot_count
        if image_count < 0 or video_count < 0 or image_count + video_count <= 0:
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PLAN_INVALID")
        image_unit, video_unit, prices = _current_visual_prices()
        image_cost = _money(
            image_unit * image_count,
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        video_cost = _money(
            video_unit * video_count,
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        provider_allocations = {
            "elevenlabs": _money_text(tts_cost + alignment_cost),
            **(
                {V2_AI_VISUAL_PROVIDER_KEY: _money_text(image_cost)}
                if image_cost > 0
                else {}
            ),
            **(
                {V2_AI_VISUAL_VIDEO_PROVIDER_KEY: _money_text(video_cost)}
                if video_cost > 0
                else {}
            ),
        }
        total = _money(
            tts_cost + alignment_cost + image_cost + video_cost,
            code="COMBINED_REPLACEMENT_TOTAL_INVALID",
        )
        body = {
            "schema_version": "vcos.combined-replacement-preflight.v2",
            "state": "FROZEN",
            "execution_kind": "NORMAL_PRODUCTION",
            "visual_authority_kind": "UNIFIED_AI_VISUAL_PLANNER_PRE_TTS",
            "tts_performance_projection_id": str(projection.id),
            "tts_performance_projection_hash": projection.content_hash,
            "tts_projection": tts_source,
            "forced_alignment": {
                "provider": "elevenlabs_forced_alignment",
                "pricing_basis": "CURRENT_REVIEWED_RUNTIME_FLAT_RATE",
                "projected_cost_usd": _money_text(alignment_cost),
            },
            "production_visual_policy_ref": production_visual_policy_ref,
            "production_visual_policy_hash": production_visual_policy_hash,
            "visual_plan_compilation": artifacts.scene_plan.model_dump(mode="json"),
            "visual_plan_compilation_hash": artifacts.scene_plan.content_hash,
            "pre_tts_timeline_hash": preflight_timeline["content_hash"],
            "unique_ai_image_asset_slot_count": image_count,
            "unique_ai_video_asset_slot_count": video_count,
            "maximum_image_submissions": maximum_image_submissions,
            "maximum_video_submissions": maximum_video_submissions,
            "ai_image_projected_cost_usd": _money_text(image_cost),
            "ai_video_projected_cost_usd": _money_text(video_cost),
            "new_tts_projected_cost_usd": _money_text(tts_cost),
            "forced_alignment_projected_cost_usd": _money_text(alignment_cost),
            "combined_replacement_projected_cost_usd": _money_text(total),
            "provider_allocations_usd": provider_allocations,
            "pricing_authorities": prices,
        }
        return {**body, "content_hash": content_hash(body)}

    @staticmethod
    def governed_rerender_visual_authority(*, authority: Any) -> dict[str, Any]:
        """Bind the existing governed-rerender visual authority without inference.

        A rerender is not allowed to derive "14 images / 0 video" from a
        script or a deployment constant.  Those values are read only from the
        durable ``AIVisualRerenderAuthority`` that authorized the replacement.
        """

        required = (
            "id",
            "authority_hash",
            "production_visual_policy_ref",
            "production_visual_policy_hash",
            "maximum_image_submissions",
            "maximum_video_submissions",
            "maximum_total_cost_usd",
            "budget_reservation_ref",
            "budget_authority_hash",
        )
        if any(getattr(authority, key, None) is None for key in required):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_GOVERNED_RERENDER_AUTHORITY_REQUIRED"
            )
        image_unit, video_unit, prices = _current_visual_prices()
        image_count = int(authority.maximum_image_submissions)
        video_count = int(authority.maximum_video_submissions)
        maximum = _money(
            authority.maximum_total_cost_usd,
            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_AUTHORITY_REQUIRED",
            positive=True,
        )
        projected = _money(
            image_unit * image_count + video_unit * video_count,
            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_AUTHORITY_REQUIRED",
        )
        if (
            image_count < 0
            or video_count < 0
            or image_count + video_count <= 0
            or projected > maximum
            or authority.production_visual_policy_hash != _active_visual_policy_hash()
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_GOVERNED_RERENDER_AUTHORITY_DRIFT"
            )
        body = {
            "schema_version": "vcos.governed-rerender-visual-authority.v1",
            "execution_kind": "GOVERNED_RERENDER",
            "visual_authority_kind": "AI_VISUAL_RERENDER_AUTHORITY",
            "ai_visual_rerender_authority_id": str(authority.id),
            "ai_visual_rerender_authority_hash": authority.authority_hash,
            "production_visual_policy_ref": authority.production_visual_policy_ref,
            "production_visual_policy_hash": authority.production_visual_policy_hash,
            "maximum_image_submissions": image_count,
            "maximum_video_submissions": video_count,
            "maximum_total_cost_usd": _money_text(maximum),
            "budget_reservation_ref": authority.budget_reservation_ref,
            "budget_authority_hash": authority.budget_authority_hash,
            "pricing_authorities": prices,
            "ai_image_projected_cost_usd": _money_text(image_unit * image_count),
            "ai_video_projected_cost_usd": _money_text(video_unit * video_count),
        }
        return {**body, "content_hash": content_hash(body)}

    @staticmethod
    def governed_rerender_visual_partition(
        *,
        authority: CombinedReplacementBudgetAuthority,
        reservation: MR1MonthlyBudgetReservation,
        production_visual_policy_ref: str,
        production_visual_policy_hash: str,
        image_owner_count: int,
        video_owner_count: int,
    ) -> dict[str, Any]:
        """Bind one governed rerender to already-occupied visual partitions.

        A controlled rerender is a replacement effect of the same project, not
        a second MR1 run.  Its durable owner counts are therefore charged only
        against the Gemini/Veo partitions that were frozen before narration.
        This function deliberately validates the complete aggregate envelope
        before returning a zero-additional-occupancy child binding.
        """

        if image_owner_count < 0 or video_owner_count < 0:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID"
            )
        binding = CombinedReplacementBudgetAuthorityService.provider_execution_binding(
            authority
        )
        preflight = dict((authority.source_refs or {}).get("ai_visual_preflight") or {})
        preflight_body = {
            key: value for key, value in preflight.items() if key != "content_hash"
        }
        image_unit, video_unit, current_prices = _current_visual_prices()
        aggregate_allocations = {
            "elevenlabs": _money_text(
                _money(
                    authority.new_tts_projected_cost_usd
                    + authority.forced_alignment_projected_cost_usd,
                    code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
                )
            ),
            **(
                {
                    V2_AI_VISUAL_PROVIDER_KEY: _money_text(
                        _money(
                            authority.ai_image_projected_cost_usd,
                            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
                        )
                    )
                }
                if Decimal(authority.ai_image_projected_cost_usd) > 0
                else {}
            ),
            **(
                {
                    V2_AI_VISUAL_VIDEO_PROVIDER_KEY: _money_text(
                        _money(
                            authority.ai_video_projected_cost_usd,
                            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
                        )
                    )
                }
                if Decimal(authority.ai_video_projected_cost_usd) > 0
                else {}
            ),
        }
        reservation_allocations = {
            str(key): _money_text(
                _money(
                    value,
                    code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
                )
            )
            for key, value in dict(reservation.provider_allocations_json or {}).items()
        }
        image_cost = _money(
            image_unit * image_owner_count,
            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
        )
        video_cost = _money(
            video_unit * video_owner_count,
            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
        )
        visual_allocations = {
            **(
                {V2_AI_VISUAL_PROVIDER_KEY: _money_text(image_cost)}
                if image_cost > 0
                else {}
            ),
            **(
                {V2_AI_VISUAL_VIDEO_PROVIDER_KEY: _money_text(video_cost)}
                if video_cost > 0
                else {}
            ),
        }
        expected_total = _money(
            authority.new_tts_projected_cost_usd
            + authority.forced_alignment_projected_cost_usd
            + authority.ai_image_projected_cost_usd
            + authority.ai_video_projected_cost_usd
            + authority.other_metered_effects_projected_cost_usd,
            code="COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_INVALID",
        )
        if (
            authority.state != "FROZEN"
            or authority.video_project_id != reservation.video_project_id
            or authority.budget_reservation_id != reservation.id
            or authority.budget_reservation_ref != reservation.reservation_ref
            or authority.combined_replacement_projected_cost_usd != expected_total
            or Decimal(reservation.reserved_amount) != expected_total
            or reservation_allocations != aggregate_allocations
            or sum(
                (Decimal(value) for value in reservation_allocations.values()),
                Decimal("0"),
            )
            != expected_total
            or preflight.get("content_hash") != content_hash(preflight_body)
            or preflight.get("state") != "FROZEN"
            or preflight.get("execution_kind") != "NORMAL_PRODUCTION"
            or preflight.get("visual_authority_kind")
            != "UNIFIED_AI_VISUAL_PLANNER_PRE_TTS"
            or preflight.get("production_visual_policy_ref")
            != production_visual_policy_ref
            or preflight.get("production_visual_policy_hash")
            != production_visual_policy_hash
            or production_visual_policy_hash != _active_visual_policy_hash()
            or preflight.get("pricing_authorities") != current_prices
            or int(preflight.get("unique_ai_image_asset_slot_count", -1))
            < image_owner_count
            or int(preflight.get("unique_ai_video_asset_slot_count", -1))
            < video_owner_count
            or image_cost > Decimal(authority.ai_image_projected_cost_usd)
            or video_cost > Decimal(authority.ai_video_projected_cost_usd)
            or Decimal(
                reservation_allocations.get(V2_AI_VISUAL_PROVIDER_KEY, "0")
            )
            < image_cost
            or Decimal(
                reservation_allocations.get(V2_AI_VISUAL_VIDEO_PROVIDER_KEY, "0")
            )
            < video_cost
            or reservation.status
            not in {"RESERVED", "SUBMITTED", "SETTLED_CONSERVATIVE"}
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_GOVERNED_RERENDER_PARTITION_REQUIRED"
            )
        body = {
            "schema_version": "vcos.governed-rerender-visual-partition.v1",
            "execution_kind": "GOVERNED_RERENDER",
            "occupancy": "ZERO_ADDITIONAL_MR1_OCCUPANCY",
            "combined_replacement_budget_authority": binding,
            "aggregate_reservation_id": str(reservation.id),
            "aggregate_reservation_ref": reservation.reservation_ref,
            "aggregate_provider_allocations_usd": aggregate_allocations,
            "visual_provider_allocations_usd": visual_allocations,
            "maximum_image_submissions": image_owner_count,
            "maximum_video_submissions": video_owner_count,
            "maximum_total_cost_usd": _money_text(image_cost + video_cost),
        }
        return {**body, "content_hash": content_hash(body)}

    @staticmethod
    def _validate_normal_preflight(
        *,
        preflight: Mapping[str, Any],
        projection: TTSPerformanceProjection,
        visual_policy_hash: str | None,
        tts_cost: Decimal,
        alignment_cost: Decimal,
    ) -> tuple[Decimal, Decimal, dict[str, Any]]:
        body = {key: value for key, value in preflight.items() if key != "content_hash"}
        if (
            preflight.get("content_hash") != content_hash(body)
            or preflight.get("schema_version")
            != "vcos.combined-replacement-preflight.v2"
            or preflight.get("state") != "FROZEN"
            or preflight.get("execution_kind") != "NORMAL_PRODUCTION"
            or preflight.get("visual_authority_kind")
            != "UNIFIED_AI_VISUAL_PLANNER_PRE_TTS"
            or preflight.get("tts_performance_projection_id") != str(projection.id)
            or preflight.get("tts_performance_projection_hash") != projection.content_hash
            or preflight.get("production_visual_policy_hash") != visual_policy_hash
            or visual_policy_hash != _active_visual_policy_hash()
        ):
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PREFLIGHT_DRIFT")
        image_count = preflight.get("unique_ai_image_asset_slot_count")
        video_count = preflight.get("unique_ai_video_asset_slot_count")
        if (
            not isinstance(image_count, int)
            or not isinstance(video_count, int)
            or image_count < 0
            or video_count < 0
            or image_count + video_count <= 0
        ):
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PLAN_INVALID")
        image_unit, video_unit, current_prices = _current_visual_prices()
        if preflight.get("pricing_authorities") != current_prices:
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_CATALOG_DRIFT")
        image_cost = _money(
            preflight.get("ai_image_projected_cost_usd"),
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        video_cost = _money(
            preflight.get("ai_video_projected_cost_usd"),
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        expected_image = _money(
            image_unit * image_count,
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        expected_video = _money(
            video_unit * video_count,
            code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        )
        if (
            image_cost != expected_image
            or video_cost != expected_video
            or _money(
                preflight.get("new_tts_projected_cost_usd"),
                code="COMBINED_REPLACEMENT_TTS_COST_AUTHORITY_REQUIRED",
            )
            != tts_cost
            or _money(
                preflight.get("forced_alignment_projected_cost_usd"),
                code="COMBINED_REPLACEMENT_ALIGNMENT_COST_AUTHORITY_REQUIRED",
            )
            != alignment_cost
        ):
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PREFLIGHT_DRIFT")
        return image_cost, video_cost, dict(preflight)

    def freeze(
        self,
        *,
        project_id: uuid.UUID,
        reservation_ref: str,
        support_envelope_hash: str,
        route_budget_authority_hash: str,
        projection: TTSPerformanceProjection,
        canonical_narration: str,
        visual_policy_hash: str | None,
        visual_preflight: Mapping[str, Any] | None,
        routes: Sequence[Any],
        approved_ceiling_usd: Any,
    ) -> CombinedReplacementBudgetAuthority:
        """Create or return the one exact immutable pre-provider authority."""

        if (
            projection.video_project_id != project_id
            or projection.state != "FROZEN"
            or not canonical_narration.strip()
            or len(support_envelope_hash) != 64
            or len(route_budget_authority_hash) != 64
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_AUTHORITY_LINEAGE_INVALID"
            )
        reservation = self.session.scalar(
            select(MR1MonthlyBudgetReservation).where(
                MR1MonthlyBudgetReservation.reservation_ref == reservation_ref,
                MR1MonthlyBudgetReservation.video_project_id == project_id,
            )
        )
        if reservation is None or reservation.status not in {"RESERVED", "SUBMITTED"}:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_BUDGET_RESERVATION_REQUIRED"
            )
        ceiling = _money(
            approved_ceiling_usd,
            code="COMBINED_REPLACEMENT_APPROVED_CEILING_INVALID",
            positive=True,
        )
        tts_rate = _money(
            getattr(self.settings, "elevenlabs_tts_cost_per_character_usd", None),
            code="COMBINED_REPLACEMENT_TTS_COST_AUTHORITY_REQUIRED",
            positive=True,
        )
        alignment_cost = _money(
            getattr(self.settings, "elevenlabs_forced_alignment_cost_usd", None),
            code="COMBINED_REPLACEMENT_ALIGNMENT_COST_AUTHORITY_REQUIRED",
            positive=True,
        )
        tts_cost, tts_source = _segment_text_cost(
            projection=projection,
            canonical_narration=canonical_narration,
            rate=tts_rate,
        )
        if visual_preflight is None:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED"
            )
        image_cost, video_cost, visual_source = self._validate_normal_preflight(
            preflight=visual_preflight,
            projection=projection,
            visual_policy_hash=visual_policy_hash,
            tts_cost=tts_cost,
            alignment_cost=alignment_cost,
        )
        paid_routes = {
            str(route.stage): str(route.route_hash)
            for route in routes
            if bool(getattr(route, "paid_provider_call", False))
        }
        if set(paid_routes) != {"MEDIA", "VISUAL"}:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_OTHER_METERED_AUTHORITY_REQUIRED"
            )
        nonpaid_routes = {
            str(route.stage): {
                "route_hash": str(route.route_hash),
                "cost_state": "EXACT_ZERO_ROUTE_DECLARED_NOT_METERED",
            }
            for route in routes
            if not bool(getattr(route, "paid_provider_call", False))
        }
        if not nonpaid_routes:
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_OTHER_METERED_AUTHORITY_REQUIRED"
            )
        other_cost = Decimal("0.000000")
        sources = {
            "tts_projection": {
                "id": str(projection.id),
                "content_hash": projection.content_hash,
                **tts_source,
                "provider_pricing_authority": {
                    "ref": "runtime-config://elevenlabs/tts-cost-per-character",
                    "content_hash": content_hash(
                        {
                            "provider": "elevenlabs",
                            "model_id": projection.model_id,
                            "pricing_basis": tts_source["pricing_basis"],
                            "rate_usd_per_character": tts_source[
                                "rate_usd_per_character"
                            ],
                        }
                    ),
                },
            },
            "forced_alignment": {
                "provider": "elevenlabs_forced_alignment",
                "pricing_basis": "CURRENT_REVIEWED_RUNTIME_FLAT_RATE",
                "projected_cost_usd": _money_text(alignment_cost),
                "provider_pricing_authority": {
                    "ref": "runtime-config://elevenlabs/forced-alignment-cost",
                    "content_hash": content_hash(
                        {
                            "provider": "elevenlabs_forced_alignment",
                            "pricing_basis": "CURRENT_REVIEWED_RUNTIME_FLAT_RATE",
                            "projected_cost_usd": _money_text(alignment_cost),
                        }
                    ),
                },
            },
            "ai_visual_preflight": visual_source,
            "other_metered_replacements": {
                "paid_route_hashes": paid_routes,
                "non_metered_routes": nonpaid_routes,
                "projected_cost_state": "EXACT_ZERO_ALL_OTHER_SEALED_ROUTES_NON_METERED",
            },
        }
        total = _money(
            tts_cost + alignment_cost + image_cost + video_cost + other_cost,
            code="COMBINED_REPLACEMENT_TOTAL_INVALID",
        )
        allocations = {
            "elevenlabs": _money_text(tts_cost + alignment_cost),
            **(
                {V2_AI_VISUAL_PROVIDER_KEY: _money_text(image_cost)}
                if image_cost > 0
                else {}
            ),
            **(
                {V2_AI_VISUAL_VIDEO_PROVIDER_KEY: _money_text(video_cost)}
                if video_cost > 0
                else {}
            ),
        }
        reservation_allocations = {
            str(key): _money_text(
                _money(value, code="COMBINED_REPLACEMENT_BUDGET_RESERVATION_REQUIRED")
            )
            for key, value in (reservation.provider_allocations_json or {}).items()
        }
        if (
            total > ceiling
            or _money(
                reservation.reserved_amount,
                code="COMBINED_REPLACEMENT_BUDGET_RESERVATION_REQUIRED",
            )
            != total
            or reservation_allocations != allocations
            or visual_source.get("provider_allocations_usd") != allocations
            or _money(
                visual_source.get("combined_replacement_projected_cost_usd"),
                code="COMBINED_REPLACEMENT_TOTAL_INVALID",
            )
            != total
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_PROVIDER_ALLOCATION_AUTHORITY_REQUIRED"
            )
        sources["approved_ceiling"] = {
            "reservation_id": str(reservation.id),
            "reservation_ref": reservation.reservation_ref,
            "reservation_request_hash": reservation.request_hash,
            "reserved_amount_usd": _money_text(total),
            "policy_ceiling_usd": _money_text(ceiling),
            "provider_allocations_usd": allocations,
        }
        shortfall = max(Decimal(0), total - ceiling)
        identity = {
            "project_id": str(project_id),
            "reservation_ref": reservation_ref,
            "route_budget_authority_hash": route_budget_authority_hash,
            "support_envelope_hash": support_envelope_hash,
            "tts_performance_projection_id": str(projection.id),
            "tts_performance_projection_hash": projection.content_hash,
            "source_refs": sources,
            "new_tts_projected_cost_usd": _money_text(tts_cost),
            "forced_alignment_projected_cost_usd": _money_text(alignment_cost),
            "ai_image_projected_cost_usd": _money_text(image_cost),
            "ai_video_projected_cost_usd": _money_text(video_cost),
            "other_metered_effects_projected_cost_usd": _money_text(other_cost),
            "combined_replacement_projected_cost_usd": _money_text(total),
            "approved_ceiling_usd": _money_text(ceiling),
            "shortfall_usd": _money_text(shortfall),
        }
        authority_id = uuid.uuid5(_AUTHORITY_NAMESPACE, content_hash(identity))
        authority_ref = f"combined-replacement-budget://{authority_id}"
        body = {
            "schema_version": _SCHEMA,
            "state": "FROZEN",
            "authority_id": str(authority_id),
            "authority_ref": authority_ref,
            **identity,
        }
        digest = content_hash(body)
        existing = self.session.scalar(
            select(CombinedReplacementBudgetAuthority).where(
                CombinedReplacementBudgetAuthority.video_project_id == project_id,
                CombinedReplacementBudgetAuthority.support_envelope_hash
                == support_envelope_hash,
                CombinedReplacementBudgetAuthority.tts_performance_projection_hash
                == projection.content_hash,
                CombinedReplacementBudgetAuthority.content_hash == digest,
            )
        )
        if existing is not None:
            return existing
        record = CombinedReplacementBudgetAuthority(
            id=authority_id,
            authority_ref=authority_ref,
            video_project_id=project_id,
            budget_reservation_id=reservation.id,
            budget_reservation_ref=reservation_ref,
            support_envelope_hash=support_envelope_hash,
            route_budget_authority_hash=route_budget_authority_hash,
            tts_performance_projection_id=projection.id,
            tts_performance_projection_hash=projection.content_hash,
            source_refs=sources,
            new_tts_projected_cost_usd=tts_cost,
            forced_alignment_projected_cost_usd=alignment_cost,
            ai_image_projected_cost_usd=image_cost,
            ai_video_projected_cost_usd=video_cost,
            other_metered_effects_projected_cost_usd=other_cost,
            combined_replacement_projected_cost_usd=total,
            approved_ceiling_usd=ceiling,
            shortfall_usd=shortfall,
            state="FROZEN",
            content_hash=digest,
        )
        self.session.add(record)
        self.session.flush()
        return record

    @staticmethod
    def provider_execution_binding(
        authority: CombinedReplacementBudgetAuthority,
    ) -> dict[str, Any]:
        body = {
            "schema_version": _SCHEMA,
            "state": authority.state,
            "authority_id": str(authority.id),
            "authority_ref": authority.authority_ref,
            "project_id": str(authority.video_project_id),
            "reservation_ref": authority.budget_reservation_ref,
            "route_budget_authority_hash": authority.route_budget_authority_hash,
            "support_envelope_hash": authority.support_envelope_hash,
            "tts_performance_projection_id": str(
                authority.tts_performance_projection_id
            ),
            "tts_performance_projection_hash": authority.tts_performance_projection_hash,
            "source_refs": authority.source_refs,
            "new_tts_projected_cost_usd": _money_text(
                authority.new_tts_projected_cost_usd
            ),
            "forced_alignment_projected_cost_usd": _money_text(
                authority.forced_alignment_projected_cost_usd
            ),
            "ai_image_projected_cost_usd": _money_text(
                authority.ai_image_projected_cost_usd
            ),
            "ai_video_projected_cost_usd": _money_text(
                authority.ai_video_projected_cost_usd
            ),
            "other_metered_effects_projected_cost_usd": _money_text(
                authority.other_metered_effects_projected_cost_usd
            ),
            "combined_replacement_projected_cost_usd": _money_text(
                authority.combined_replacement_projected_cost_usd
            ),
            "approved_ceiling_usd": _money_text(authority.approved_ceiling_usd),
            "shortfall_usd": _money_text(authority.shortfall_usd),
        }
        if authority.content_hash != content_hash(body):
            raise ValidationFailureError("COMBINED_REPLACEMENT_BUDGET_AUTHORITY_DRIFT")
        return {**body, "authority_hash": authority.content_hash}
