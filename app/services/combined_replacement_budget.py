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
    V2_GEMINI_IMAGE_2K_OUTPUT_TOKENS,
    V2_GEMINI_IMAGE_INPUT_PRICE_PER_MILLION_TOKENS_USD,
    V2_GEMINI_IMAGE_MAX_OUTPUT_TOKENS,
    V2_GEMINI_IMAGE_MAX_PROVIDER_INPUT_BYTES,
    V2_GEMINI_IMAGE_TEXT_THINKING_PRICE_PER_MILLION_TOKENS_USD,
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


def _visual_preflight(
    *, sections: Sequence[Mapping[str, Any]], visual_policy_hash: str | None
) -> tuple[Decimal, Decimal, dict[str, Any]]:
    """Seal the current package visual owner plan before any MEDIA effect.

    At package readiness the only sealed visual owner plan is one AI-image
    owner per canonical section.  It authorizes no AI-video owner; a later
    change to that plan must create a new package/combined authority rather
    than silently spending against this one.
    """

    owners: list[dict[str, str]] = []
    for index, section in enumerate(sections, start=1):
        section_id = section.get("section_id")
        section_hash = section.get("section_hash") or content_hash(dict(section))
        if (
            not isinstance(section_id, str)
            or not section_id
            or not isinstance(section_hash, str)
        ):
            raise ValidationFailureError("COMBINED_REPLACEMENT_VISUAL_PLAN_INVALID")
        owners.append({"owner_id": section_id, "section_hash": section_hash})
    if not owners or not visual_policy_hash:
        raise ValidationFailureError(
            "COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED"
        )

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
        catalog_unit + semantic_upper_bound,
        code="COMBINED_REPLACEMENT_VISUAL_COST_AUTHORITY_REQUIRED",
        positive=True,
    )
    veo_catalog = GoogleVeoModelPriceCatalog()
    source = {
        "schema_version": "vcos.package-ai-visual-cost-preflight.v1",
        "state": "SEALED",
        "visual_policy_hash": visual_policy_hash,
        "image_owner_count": len(owners),
        "video_owner_count": 0,
        "image_owners": owners,
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
        "video_owner_cost_state": "EXACT_ZERO_NO_VIDEO_OWNER_IN_SEALED_PACKAGE_PLAN",
    }
    return (
        _money(
            image_unit * len(owners), code="COMBINED_REPLACEMENT_VISUAL_COST_INVALID"
        ),
        Decimal("0.000000"),
        {**source, "content_hash": content_hash(source)},
    )


class CombinedReplacementBudgetAuthorityService:
    """Freeze and replay the exact combined cost authority for one package."""

    def __init__(self, session: Session, *, settings: Settings | Any | None = None):
        self.session = session
        self.settings = settings or get_settings()

    def freeze(
        self,
        *,
        project_id: uuid.UUID,
        reservation_ref: str,
        support_envelope_hash: str,
        route_budget_authority_hash: str,
        projection: TTSPerformanceProjection,
        canonical_narration: str,
        sections: Sequence[Mapping[str, Any]],
        visual_policy_hash: str | None,
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
        if (
            _money(
                reservation.reserved_amount,
                code="COMBINED_REPLACEMENT_BUDGET_RESERVATION_REQUIRED",
            )
            != ceiling
        ):
            raise ValidationFailureError(
                "COMBINED_REPLACEMENT_BUDGET_RESERVATION_DRIFT"
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
        image_cost, video_cost, visual_source = _visual_preflight(
            sections=sections, visual_policy_hash=visual_policy_hash
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
            "approved_ceiling": {
                "reservation_id": str(reservation.id),
                "reservation_ref": reservation.reservation_ref,
                "reservation_request_hash": reservation.request_hash,
                "reserved_amount_usd": _money_text(ceiling),
            },
        }
        total = _money(
            tts_cost + alignment_cost + image_cost + video_cost + other_cost,
            code="COMBINED_REPLACEMENT_TOTAL_INVALID",
        )
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
