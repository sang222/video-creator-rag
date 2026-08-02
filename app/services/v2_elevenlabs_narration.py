"""Real, one-shot ElevenLabs narration adapter for V2 long-form production."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowFailureClassification,
    WorkflowStageResult,
)
from app.core.config import Settings, get_settings
from app.core.errors import ValidationFailureError
from app.services.config_registry import content_hash
from app.services.cqr1_real_provider import ElevenLabsConvertWithTimestampsClient
from app.services.mr1_provider_gateways import _temporal_normalized
from app.services.production_workflow import WorkflowStageContext, WorkflowStageError
from app.services.v2_native_effects import (
    V2_ELEVENLABS_NARRATION_STRATEGY,
    V2_TIMELINE_SCHEMA,
    V2LocalNativeProductionAdapter,
    _build_timeline,
    _load_json,
    _persist_exact_json,
    _production_inputs,
    _sha256_file,
    _write_json_atomic,
)
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
)


V2_ELEVENLABS_NARRATION_ADAPTER_KEY = "v2-elevenlabs-narration"


class V2ElevenLabsNarrationAdapter(V2LocalNativeProductionAdapter):
    """Use the existing no-retry ElevenLabs timestamp client exactly once.

    The inherited effect ledger commits ``EFFECT_STARTED`` before this adapter
    writes the local request journal or reaches the network.  A missing output
    after a submitted journal is intentionally uncertain and is never retried
    automatically under the same command id.
    """

    descriptor = V2ProductionAdapterDescriptor(
        adapter_key=V2_ELEVENLABS_NARRATION_ADAPTER_KEY,
        supported_stages=frozenset({ProductionWorkflowStage.MEDIA}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=True,
        automatic_publish=False,
    )

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: ElevenLabsConvertWithTimestampsClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or get_settings()
        self._client = client or ElevenLabsConvertWithTimestampsClient()

    def _validate_operation(
        self,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> None:
        details = operation.parameters.get("provider_execution")
        if (
            context.run.production_lane != "LONG_FORM"
            or context.run.planning_source_type != "LONG_FORM_PLAN"
            or operation.stage != ProductionWorkflowStage.MEDIA
            or operation.adapter_key != V2_ELEVENLABS_NARRATION_ADAPTER_KEY
            or operation.execution_mode != "REAL_LONG_FORM_PRODUCTION"
            or operation.paid_provider_call is not True
            or operation.max_cost_usd <= Decimal("0")
            or operation.parameters.get("mode") != "ELEVENLABS_FINAL_NARRATION"
            or operation.parameters.get("audio_strategy")
            != V2_ELEVENLABS_NARRATION_STRATEGY
            or not isinstance(details, dict)
            or details.get("provider") != "elevenlabs"
            or details.get("credential_ref") != "env://ELEVENLABS_API_KEY"
            or details.get("attempt_limit") != 1
            or not isinstance(details.get("idempotency_key"), str)
            or not details["idempotency_key"].strip()
            or not isinstance(details.get("voice_id"), str)
            or not isinstance(details.get("model_id"), str)
            or not isinstance(details.get("voice_settings"), dict)
        ):
            raise ValidationFailureError("V2_ELEVENLABS_NARRATION_OPERATION_INVALID")

    def _produce_media(
        self,
        *,
        ledger_id: Any,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, package, script, visual = _production_inputs(
                session, context.run.id
            )
        effect_dir = self._effect_dir(context.command_id)
        audio = self._prepare_real_audio(
            effect_dir=effect_dir,
            command_id=context.command_id,
            package=package,
            script=script,
            operation=operation,
        )
        timeline_ref = f"v2-effect://{ledger_id}/canonical-media-timeline"
        timeline = _build_timeline(
            run=run,
            project=project,
            package=package,
            script=script,
            visual=visual,
            timeline_ref=timeline_ref,
            audio=audio,
        )
        timeline_hash = content_hash(timeline)
        timeline_path = effect_dir / "canonical-media-timeline.json"
        _persist_exact_json(timeline_path, timeline)
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "MEDIA",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "provider_call_count": 1,
            "timeline_relative_path": self._relative(timeline_path),
            "timeline_file_checksum": _sha256_file(timeline_path),
            "timeline_hash": timeline_hash,
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
            "audio_asset_ref": audio["audio_asset_ref"],
            "audio_checksum": audio["audio_checksum"],
            "audio_relative_path": audio["audio_relative_path"],
            "audio_effect_invocation_count": 1,
            "narration_present": True,
            "alignment_method": audio["alignment_method"],
            "provider_request_hash": audio["provider_request_hash"],
            "provider_request_id": audio.get("provider_request_id"),
            "estimated_cost_usd": audio["estimated_cost_usd"],
            "actual_cost_usd": audio["actual_cost_usd"],
        }
        _persist_exact_json(
            effect_dir / "effect-journal.json", journal, allow_reconciled_update=True
        )
        return (
            WorkflowStageResult(
                result_type="V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE",
                result_ref=timeline_ref,
                result_hash=timeline_hash,
                result_payload={
                    "schema_version": V2_TIMELINE_SCHEMA,
                    "scene_count": len(timeline["scenes"]),
                    "duration_ms": timeline["duration_ms"],
                    "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
                    "audio_asset_ref": audio["audio_asset_ref"],
                    "audio_checksum": audio["audio_checksum"],
                    "narration_present": True,
                    "alignment_method": audio["alignment_method"],
                    "provider_request_id": audio.get("provider_request_id"),
                    "actual_cost_usd": audio["actual_cost_usd"],
                },
                authority_refs=WorkflowAuthorityRefs(
                    video_project_id=project.id,
                    canonical_media_timeline_ref=timeline_ref,
                    canonical_media_timeline_hash=timeline_hash,
                ),
            ),
            journal,
        )

    def _prepare_real_audio(
        self,
        *,
        effect_dir: Path,
        command_id: str,
        package: Any,
        script: Any,
        operation: V2AuthorizedAdapterOperation,
    ) -> dict[str, Any]:
        details = dict(operation.parameters["provider_execution"])
        script_text = str((script.content or {}).get("narration_text") or "").strip()
        if not script_text:
            raise ValidationFailureError("V2_ELEVENLABS_APPROVED_SCRIPT_REQUIRED")
        output = effect_dir / "elevenlabs-final-narration.mp3"
        request_path = effect_dir / "elevenlabs-request-journal.json"
        receipt_path = effect_dir / "elevenlabs-narration-receipt.json"
        identity = {
            "schema_version": "vcos.v2-elevenlabs-request.v1",
            "command_id": command_id,
            "idempotency_key": details["idempotency_key"],
            "script_content_hash": script.content_hash,
            "approved_script_hash": hashlib.sha256(script_text.encode()).hexdigest(),
            "voice_id": details["voice_id"],
            "model_id": details["model_id"],
            "voice_settings": details["voice_settings"],
            "estimated_cost_usd": str(operation.max_cost_usd),
            "output_relative_path": self._relative(output),
            "attempt_limit": 1,
        }
        if receipt_path.exists():
            receipt = _load_json(receipt_path)
            if (
                any(receipt.get(key) != value for key, value in identity.items())
                or not output.is_file()
                or output.is_symlink()
                or receipt.get("audio_checksum") != _sha256_file(output)
            ):
                raise ValidationFailureError("V2_ELEVENLABS_NARRATION_RECEIPT_MISMATCH")
            return dict(receipt)
        if request_path.exists():
            prior = _load_json(request_path)
            if any(prior.get(key) != value for key, value in identity.items()):
                raise ValidationFailureError("V2_ELEVENLABS_REQUEST_JOURNAL_MISMATCH")
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_ELEVENLABS_OUTCOME_UNCERTAIN",
                summary="An ElevenLabs request was submitted without a sealed output; no duplicate request was attempted.",
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            )
        api_key = (
            self._settings.elevenlabs_api_key.get_secret_value()
            if self._settings.elevenlabs_api_key is not None
            else ""
        )
        if not api_key.strip():
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_REAL_ELEVENLABS_BLOCKED_CREDENTIAL",
                summary="ElevenLabs credential is unavailable; no local narration fallback was attempted.",
                incident_type="CREDENTIAL_MISSING",
                retry_eligible=False,
            )
        _write_json_atomic(request_path, {**identity, "state": "SUBMITTED"})
        normalized = _temporal_normalized({"normalized_text": script_text})
        try:
            execution = self._client.execute_once(
                api_key=api_key,
                normalized=normalized,
                voice_id=details["voice_id"],
                model_id=details["model_id"],
                voice_settings=details["voice_settings"],
                destination=output,
                audio_asset_ref=f"v2-elevenlabs://{details['idempotency_key']}",
            )
        except Exception as exc:
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_ELEVENLABS_PROVIDER_FAILURE",
                summary="ElevenLabs narration did not yield a sealed response; no retry or local fallback was attempted.",
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            ) from exc
        if not (
            package.duration_contract.minimum_duration_ms
            <= execution.audio_duration_ms
            <= package.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError("V2_ELEVENLABS_DURATION_OUTSIDE_CONTRACT")
        receipt = {
            **identity,
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
            "audio_asset_ref": execution.audio_asset_ref,
            "audio_checksum": execution.audio_sha256,
            "audio_relative_path": self._relative(output),
            "duration_ms": execution.audio_duration_ms,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
            "provider_request_hash": execution.request_hash,
            "provider_request_id": execution.timing_seed.provider_request_id,
            "usage_metadata": execution.usage_metadata,
            "actual_cost_usd": None,
            "secret_values_exposed": False,
        }
        _write_json_atomic(receipt_path, receipt)
        return receipt


__all__ = ["V2_ELEVENLABS_NARRATION_ADAPTER_KEY", "V2ElevenLabsNarrationAdapter"]
