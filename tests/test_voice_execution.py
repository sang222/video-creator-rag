from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import ValidationFailureError
from app.services.config_registry import content_hash
from app.services.voice_execution import (
    CombinedReplacementBudget,
    elevenlabs_capability,
    frozen_voice_authority_gate,
    narration_text_fidelity_gate,
    provider_text_projection,
    select_execution_strategy,
    seam_qc,
)


def _authority() -> dict[str, str]:
    return {
        "authority_mode": "FROZEN_PROJECT_VOICE_AUTHORITY",
        "approved_voice_pool_id": "pool",
        "approved_voice_pool_hash": "a" * 64,
        "voice_casting_decision_id": "casting",
        "voice_casting_decision_hash": "b" * 64,
        "narration_voice_snapshot_id": "snapshot",
        "narration_voice_snapshot_hash": "c" * 64,
        "narration_performance_plan_id": "plan",
        "narration_performance_plan_hash": "d" * 64,
        "tts_performance_projection_id": "projection",
        "tts_performance_projection_hash": "e" * 64,
        "qualified_script_hash": "f" * 64,
        "voice_id": "voice",
        "model_id": "eleven_multilingual_v2",
    }


def test_real_execution_requires_exact_frozen_voice_authority() -> None:
    authority = _authority()
    frozen_voice_authority_gate(
        authority=authority,
        script_hash="f" * 64,
        voice_id="voice",
        model_id="eleven_multilingual_v2",
    )
    authority["narration_voice_snapshot_hash"] = ""
    with pytest.raises(
        ValidationFailureError, match="REAL_PRODUCTION_VOICE_AUTHORITY_REQUIRED"
    ):
        frozen_voice_authority_gate(
            authority=authority,
            script_hash="f" * 64,
            voice_id="voice",
            model_id="eleven_multilingual_v2",
        )


def test_capability_routing_blocks_unconfirmed_v3_request_id_stitching() -> None:
    assert (
        select_execution_strategy(model_id="eleven_multilingual_v2", segment_count=2)
        == "CONTEXT_STITCHED_MULTI_REQUEST"
    )
    assert (
        select_execution_strategy(model_id="eleven_v3", segment_count=2)
        == "SEGMENTED_WITH_SEAM_QC"
    )
    with pytest.raises(ValidationFailureError, match="ELEVENLABS_CONTEXT_UNSUPPORTED"):
        provider_text_projection(
            canonical_text="One safe segment.",
            context={"previous_request_ids": ["request-1"]},
            capability=elevenlabs_capability("eleven_v3"),
        )


def test_fidelity_gate_rejects_reordered_or_deleted_narration() -> None:
    text = "Alpha. Beta."
    segments = [
        {
            "segment_id": "one",
            "source_text_start": 0,
            "source_text_end": 7,
            "text_hash": content_hash({"text": "Alpha. "}),
        },
        {
            "segment_id": "two",
            "source_text_start": 7,
            "source_text_end": len(text),
            "text_hash": content_hash({"text": "Beta."}),
        },
    ]
    narration_text_fidelity_gate(canonical_text=text, segments=segments)
    with pytest.raises(
        ValidationFailureError, match="NARRATION_TEXT_FIDELITY_GATE_FAILED"
    ):
        narration_text_fidelity_gate(canonical_text=text, segments=segments[:1])


def test_combined_budget_blocks_before_paid_execution() -> None:
    budget = CombinedReplacementBudget(
        Decimal("1.00"),
        Decimal("0.50"),
        Decimal("2.00"),
        Decimal("4.00"),
        Decimal("0"),
        Decimal("7.00"),
    )
    with pytest.raises(
        ValidationFailureError, match="COMBINED_REPLACEMENT_BUDGET_INSUFFICIENT"
    ):
        budget.require_authorized()
    assert budget.report()["shortfall_usd"] == "0.50"


def test_seam_qc_rejects_missing_or_reordered_audio() -> None:
    report = seam_qc(
        segments=[
            {
                "segment_index": 0,
                "duration_ms": 100,
                "canonical_start_ms": 0,
                "audio_checksum": "a" * 64,
            },
            {
                "segment_index": 1,
                "duration_ms": 100,
                "canonical_start_ms": 100,
                "audio_checksum": "b" * 64,
            },
        ]
    )
    assert report.state == "PASS"
    assert seam_qc(segments=[{"segment_index": 1, "duration_ms": 0}]).state == "FAIL"
