from __future__ import annotations

import hashlib
import subprocess
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.errors import ValidationFailureError
from app.services.config_registry import content_hash
from app.services.v2_elevenlabs_narration import V2ElevenLabsNarrationAdapter
from app.services.v2_package_readiness import _combined_replacement_budget_binding
from app.services.voice_execution import (
    CombinedReplacementBudget,
    combined_replacement_budget_authority,
    elevenlabs_capability,
    frozen_voice_authority_gate,
    narration_text_fidelity_gate,
    provider_text_projection,
    seam_qc,
    select_execution_strategy,
)


def _combined_budget_authority(**overrides: str) -> dict[str, str]:
    payload = {
        "schema_version": "vcos.combined-replacement-budget.v1",
        "state": "FROZEN",
        "authority_ref": "budget://replacement/project-1",
        "new_tts_projected_cost_usd": "3.00",
        "forced_alignment_projected_cost_usd": "0.50",
        "ai_image_projected_cost_usd": "1.00",
        "ai_video_projected_cost_usd": "2.00",
        "other_metered_effects_projected_cost_usd": "0.25",
        "approved_ceiling_usd": "7.00",
        **overrides,
    }
    return {**payload, "authority_hash": content_hash(payload)}


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
        Decimal(0),
        Decimal("7.00"),
    )
    with pytest.raises(
        ValidationFailureError, match="COMBINED_REPLACEMENT_BUDGET_INSUFFICIENT"
    ):
        budget.require_authorized()
    assert budget.report()["shortfall_usd"] == "0.50"


def test_combined_budget_authority_requires_every_current_component() -> None:
    authority = _combined_budget_authority()
    budget, ref, budget_hash = combined_replacement_budget_authority(authority)
    assert ref == "budget://replacement/project-1"
    assert budget_hash == authority["authority_hash"]
    assert budget.projected_incremental_cost_usd == Decimal("6.75")
    budget.require_authorized()

    missing = _combined_budget_authority()
    del missing["ai_video_projected_cost_usd"]
    missing["authority_hash"] = content_hash(
        {key: value for key, value in missing.items() if key != "authority_hash"}
    )
    with pytest.raises(
        ValidationFailureError,
        match="COMBINED_REPLACEMENT_BUDGET_COMPONENT_REQUIRED:ai_video",
    ):
        combined_replacement_budget_authority(missing)

    over_budget = _combined_budget_authority(approved_ceiling_usd="6.74")
    with pytest.raises(
        ValidationFailureError, match="COMBINED_REPLACEMENT_BUDGET_INSUFFICIENT"
    ):
        combined_replacement_budget_authority(over_budget)[0].require_authorized()


def test_package_binds_explicit_unavailable_or_frozen_combined_cost_authority() -> None:
    budget = SimpleNamespace(
        reservation_evidence={},
        reservation_ref="reservation://project-1",
        content_hash="a" * 64,
    )
    unavailable = _combined_replacement_budget_binding(
        envelope=SimpleNamespace(zero_cost_budget=budget),
        support_envelope_hash="b" * 64,
    )
    assert unavailable["state"] == "UNAVAILABLE"
    assert unavailable["reason_code"] == "COMBINED_REPLACEMENT_COST_COMPONENTS_REQUIRED"

    budget.reservation_evidence = {
        "combined_replacement_costs": {
            "new_tts_projected_cost_usd": "3.00",
            "forced_alignment_projected_cost_usd": "0.50",
            "ai_image_projected_cost_usd": "1.00",
            "ai_video_projected_cost_usd": "2.00",
            "other_metered_effects_projected_cost_usd": "0.25",
            "approved_ceiling_usd": "7.00",
        }
    }
    frozen = _combined_replacement_budget_binding(
        envelope=SimpleNamespace(zero_cost_budget=budget),
        support_envelope_hash="b" * 64,
    )
    assert frozen["state"] == "FROZEN"
    assert combined_replacement_budget_authority(frozen)[0].projected_incremental_cost_usd == Decimal(
        "6.75"
    )


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
    assert (
        seam_qc(
            segments=[
                {
                    "segment_index": 0,
                    "duration_ms": 100,
                    "canonical_start_ms": 0,
                    "audio_checksum": "a" * 64,
                }
            ],
            stitched_duration_ms=500,
        ).state
        == "FAIL"
    )


def test_real_adapter_executes_and_reconciles_three_sealed_segments(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Exercise the production adapter path with native stitch and fake transport."""

    narration = "One. Two. Three."
    boundaries = (("one", 0, 5), ("two", 5, 10), ("three", 10, len(narration)))
    projection = SimpleNamespace(
        content_hash="p" * 64,
        video_project_id=uuid.uuid4(),
        execution_strategy="CONTEXT_STITCHED_MULTI_REQUEST",
        segments=[
            {
                "ordinal": index,
                "segment_id": segment_id,
                "source_text_start": start,
                "source_text_end": end,
                "voice_settings": {"speed": 1.0 + index / 100},
            }
            for index, (segment_id, start, end) in enumerate(boundaries, start=1)
        ],
    )
    calls: list[dict[str, object]] = []
    forced_calls: list[Path] = []

    def seed(*, text: str, ref: str, duration_ms: int, request_id: str):
        payload = {
            "provider_key": "fake-elevenlabs",
            "provider_request_id": request_id,
            "audio_asset_ref": ref,
            "audio_duration_ms": duration_ms,
            "source_text_hash": content_hash({"text": text}),
            "spoken_text_hash": content_hash({"text": text}),
            "original_character_alignment": [],
            "normalized_character_alignment": [],
            "provider_model_id": "eleven_multilingual_v2",
            "provider_voice_id": "voice-1",
            "seed": None,
            "voice_settings": {},
            "pronunciation_dictionary_refs": [],
            "response_metadata": {},
            "timing_available": True,
            "timing_parse_warnings": [],
        }
        return SimpleNamespace(
            provider_request_id=request_id,
            audio_asset_ref=ref,
            audio_duration_ms=duration_ms,
            content_hash=content_hash(payload),
            model_dump=lambda **_kwargs: {
                **payload,
                "content_hash": content_hash(payload),
            },
        )

    class FakeTTS:
        def execute_once(self, **kwargs):
            destination = kwargs["destination"]
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=0.12",
                    "-q:a",
                    "9",
                    str(destination),
                ],
                check=True,
            )
            from app.services.v2_native_effects import _probe_duration_ms

            duration_ms = _probe_duration_ms(adapter._builder.ffprobe, destination)
            request_id = f"request-{len(calls) + 1}"
            calls.append(
                {
                    "voice_settings": kwargs["voice_settings"],
                    "provider_context": kwargs["provider_context"],
                    "text": kwargs["normalized"].spoken_text,
                }
            )
            audio_ref = kwargs["audio_asset_ref"]
            timing_seed = seed(
                text=kwargs["normalized"].spoken_text,
                ref=audio_ref,
                duration_ms=duration_ms,
                request_id=request_id,
            )
            return SimpleNamespace(
                request_hash=content_hash({"request_id": request_id}),
                audio_path=destination,
                audio_asset_ref=audio_ref,
                audio_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                audio_duration_ms=duration_ms,
                timing_seed=timing_seed,
                usage_metadata={"fake": True},
            )

    class FakeForcedAlignment:
        def execute_once(self, **kwargs):
            forced_calls.append(kwargs["audio_path"])
            return SimpleNamespace(evidence=SimpleNamespace(provider_request_id="alignment-1"))

    class FakeEffects:
        records: ClassVar[dict[str, SimpleNamespace]] = {}

        def __init__(self, _session_factory):
            pass

        def intend_and_submit(self, **kwargs):
            key = kwargs["segment"]["segment_id"]
            existing = self.records.get(key)
            if existing is not None:
                if existing.state in {"SUBMITTED", "PROVIDER_OUTCOME_UNKNOWN"}:
                    raise ValidationFailureError("PROVIDER_OUTCOME_UNKNOWN")
                return existing
            effect = SimpleNamespace(
                id=key,
                provider_effect_key=f"effect-{key}",
                state="SUBMITTED",
                estimated_cost_usd=str(kwargs["estimated_cost_usd"]),
            )
            self.records[key] = effect
            return effect

        def verify(self, *, effect_id, **kwargs):
            effect = self.records[effect_id]
            effect.state = "VERIFIED"
            effect.provider_request_hash = kwargs["provider_request_hash"]
            effect.provider_request_id = kwargs["provider_request_id"]
            effect.audio_ref = kwargs["audio_ref"]
            effect.audio_checksum = kwargs["audio_checksum"]
            effect.duration_ms = kwargs["duration_ms"]
            effect.timing_seed = kwargs["timing_seed"]
            effect.actual_cost_usd = None

        def mark_unknown(self, *, effect_id):
            self.records[effect_id].state = "PROVIDER_OUTCOME_UNKNOWN"

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, model, _identifier):
            return projection if model.__name__ == "TTSPerformanceProjection" else None

    budget = _combined_budget_authority()
    details = {
        "voice_id": "voice-1",
        "model_id": "eleven_multilingual_v2",
        "tts_performance_projection_id": "projection-1",
        "tts_performance_projection_hash": "p" * 64,
        "combined_replacement_budget_authority": budget,
    }
    adapter = V2ElevenLabsNarrationAdapter(
        settings=SimpleNamespace(
            elevenlabs_api_key=SimpleNamespace(get_secret_value=lambda: "test-key")
        ),
        workspace_root=tmp_path,
        session_factory=FakeSession,
        client=FakeTTS(),
        client_factory=FakeTTS,
        forced_alignment_client_factory=FakeForcedAlignment,
    )
    monkeypatch.setattr(
        "app.services.v2_elevenlabs_narration.NarrationSegmentExecutionService",
        FakeEffects,
    )
    monkeypatch.setattr(
        "app.services.v2_elevenlabs_narration._timing_seed_from_forced_alignment",
        lambda **kwargs: seed(
            text=narration,
            ref=kwargs["audio_asset_ref"],
            duration_ms=kwargs["audio_duration_ms"],
            request_id="alignment-1",
        ),
    )
    effect_dir = adapter._effect_dir("adapter-integration")
    package = SimpleNamespace(
        duration_contract=SimpleNamespace(
            minimum_duration_ms=100, maximum_duration_ms=2_000
        )
    )
    script = SimpleNamespace(content={"narration_text": narration})
    operation = SimpleNamespace(max_cost_usd=Decimal("7.00"))

    missing_visual_cost = _combined_budget_authority()
    del missing_visual_cost["ai_image_projected_cost_usd"]
    missing_visual_cost["authority_hash"] = content_hash(
        {
            key: value
            for key, value in missing_visual_cost.items()
            if key != "authority_hash"
        }
    )
    with pytest.raises(
        ValidationFailureError,
        match="COMBINED_REPLACEMENT_BUDGET_COMPONENT_REQUIRED:ai_image",
    ):
        adapter._prepare_projection_audio(
            effect_dir=adapter._effect_dir("missing-visual-cost"),
            command_id="missing-visual-cost",
            package=package,
            script=script,
            details={
                **details,
                "combined_replacement_budget_authority": missing_visual_cost,
            },
            operation=operation,
            video_project_id=projection.video_project_id,
        )
    assert not calls

    with pytest.raises(
        ValidationFailureError, match="COMBINED_REPLACEMENT_BUDGET_INSUFFICIENT"
    ):
        adapter._prepare_projection_audio(
            effect_dir=adapter._effect_dir("over-combined-budget"),
            command_id="over-combined-budget",
            package=package,
            script=script,
            details={
                **details,
                "combined_replacement_budget_authority": _combined_budget_authority(
                    approved_ceiling_usd="6.74"
                ),
            },
            operation=operation,
            video_project_id=projection.video_project_id,
    )
    assert not calls

    with pytest.raises(
        ValidationFailureError,
        match="ELEVEN_V3_GOVERNED_EXPRESSIVE_EXECUTION_UNSUPPORTED",
    ):
        adapter._prepare_projection_audio(
            effect_dir=adapter._effect_dir("v3-performance-unsupported"),
            command_id="v3-performance-unsupported",
            package=package,
            script=script,
            details={**details, "model_id": "eleven_v3"},
            operation=operation,
            video_project_id=projection.video_project_id,
        )
    assert not calls

    receipt = adapter._prepare_projection_audio(
        effect_dir=effect_dir,
        command_id="adapter-integration",
        package=package,
        script=script,
        details=details,
        operation=operation,
        video_project_id=projection.video_project_id,
    )
    assert len(calls) == 3
    assert [call["voice_settings"] for call in calls] == [
        {"speed": 1.01},
        {"speed": 1.02},
        {"speed": 1.03},
    ]
    assert calls[1]["provider_context"]["previous_text"] == "One. "
    assert calls[1]["provider_context"]["next_text"] == "Three."
    assert len(forced_calls) == 1
    assert receipt["tts_provider_call_count"] == 3
    assert receipt["forced_alignment_provider_call_count"] == 1
    assert receipt["total_provider_call_count"] == 4
    assert receipt["estimated_cost_usd"] == "3.50"
    assert receipt["usage_metadata"]["seam_qc_hash"]
    assert all(effect.state == "VERIFIED" for effect in FakeEffects.records.values())

    replay = adapter._prepare_projection_audio(
        effect_dir=effect_dir,
        command_id="adapter-integration",
        package=package,
        script=script,
        details=details,
        operation=operation,
        video_project_id=projection.video_project_id,
    )
    assert len(calls) == 3
    assert len(forced_calls) == 1
    assert replay == receipt

    FakeEffects.records["two"].state = "PROVIDER_OUTCOME_UNKNOWN"
    with pytest.raises(ValidationFailureError, match="PROVIDER_OUTCOME_UNKNOWN"):
        adapter._prepare_projection_audio(
            effect_dir=effect_dir,
            command_id="adapter-integration",
            package=package,
            script=script,
            details=details,
            operation=operation,
            video_project_id=projection.video_project_id,
        )
    assert len(calls) == 3
