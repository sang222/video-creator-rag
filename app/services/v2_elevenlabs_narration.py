"""Real, one-shot ElevenLabs narration adapter for V2 long-form production."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select

from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowFailureClassification,
    WorkflowStageResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.contracts.temporal_authority import NarrationTimingSeed
from app.core.config import Settings, get_settings
from app.core.errors import ValidationFailureError
from app.services.config_registry import content_hash
from app.services.cqr1_real_provider import (
    ElevenLabsConvertWithTimestampsClient,
    ElevenLabsForcedAlignmentClient,
)
from app.services.mr1_provider_gateways import _temporal_normalized
from app.services.production_workflow import WorkflowStageContext, WorkflowStageError
from app.services.workflow import ArtifactService
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.v2_native_effects import (
    V2_ELEVENLABS_NARRATION_STRATEGY,
    V2_TIMELINE_SCHEMA,
    V2LocalNativeProductionAdapter,
    _build_timeline,
    _load_json,
    _persist_exact_json,
    _production_inputs,
    _result_from_ledger,
    _sha256_file,
    _write_json_atomic,
)
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
)
from app.services.voice_execution import (
    AudioStitchCompiler,
    CombinedReplacementBudget,
    NarrationSegmentExecutionService,
    frozen_voice_authority_gate,
    narration_text_fidelity_gate,
    provider_text_projection,
    seam_qc,
    elevenlabs_capability,
)


V2_ELEVENLABS_NARRATION_ADAPTER_KEY = "v2-elevenlabs-narration"
V2_TRANSCRIPT_ARTIFACT_TYPE = "v2_transcript"
V2_TIMED_WORDS_ARTIFACT_TYPE = "v2_timed_words"
V2_CAPTION_SRT_ARTIFACT_TYPE = "v2_caption_srt"
V2_SUBTITLE_QC_ARTIFACT_TYPE = "v2_subtitle_qc"
V2_SIDECAR_SCHEMA = "vcos.v2-sidecar-srt.v1"
V2_ELEVENLABS_PROVIDER_RESPONSE_JOURNAL_SCHEMA = (
    "vcos.v2-elevenlabs-provider-response.v1"
)


def _timing_seed_from_forced_alignment(
    *,
    normalized: Any,
    evidence: Any,
    audio_asset_ref: str,
    audio_duration_ms: int,
    voice_id: str,
    model_id: str,
) -> NarrationTimingSeed:
    """Make final forced alignment the only timing authority for stitched audio."""

    matches = list(re.finditer(r"\S+", normalized.spoken_text))
    if (
        evidence.verification_status != "PASS"
        or evidence.audio_asset_ref != audio_asset_ref
        or evidence.audio_duration_ms != audio_duration_ms
        or len(matches) != len(evidence.words)
    ):
        raise ValidationFailureError("FINAL_FORCED_ALIGNMENT_INVALID")
    caption_words = []
    previous_end = -1
    for index, (match, word, token) in enumerate(
        zip(matches, evidence.words, normalized.spoken_tokens, strict=True), start=1
    ):
        if (
            word.source_spoken_token_ids != [token.token_id]
            or word.start_ms < previous_end
            or word.end_ms <= word.start_ms
        ):
            raise ValidationFailureError("FINAL_FORCED_ALIGNMENT_INVALID")
        caption_words.append(
            {
                "index": index,
                "text": match.group(),
                "start_ms": word.start_ms,
                "end_ms": word.end_ms,
                "provider_word_id": word.word_id,
                "source_spoken_token_ids": list(word.source_spoken_token_ids),
            }
        )
        previous_end = word.end_ms
    payload = {
        "provider_key": "elevenlabs_forced_alignment_recovery",
        "provider_request_id": evidence.provider_request_id,
        "audio_asset_ref": audio_asset_ref,
        "audio_duration_ms": audio_duration_ms,
        "source_text_hash": normalized.source_text_hash,
        "spoken_text_hash": normalized.spoken_text_hash,
        "original_character_alignment": [
            item.model_dump(mode="json") for item in evidence.characters
        ],
        "normalized_character_alignment": [
            item.model_dump(mode="json") for item in evidence.characters
        ],
        "provider_model_id": model_id,
        "provider_voice_id": voice_id,
        "seed": None,
        "voice_settings": {},
        "pronunciation_dictionary_refs": [],
        "response_metadata": {
            "forced_alignment_evidence_hash": evidence.content_hash,
            "exact_character_coverage": True,
            "exact_token_coverage": True,
            "alignment_audit": {
                "exact_raw_character_sequence": True,
                "exact_word_token_coverage": True,
                "zero_duration_character_timing_synthesized": False,
                "caption_timing_source": "ELEVENLABS_FORCED_ALIGNMENT_WORD_BOUNDARIES",
                "provider_word_count": len(caption_words),
                "canonical_spoken_token_count": len(caption_words),
            },
            "caption_timed_words": caption_words,
            "interpolation_used": False,
            "estimation_used": False,
        },
        "timing_available": True,
        "timing_parse_warnings": list(evidence.warnings),
    }
    return NarrationTimingSeed(**payload, content_hash=content_hash(payload))


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
        client_factory: Any | None = None,
        forced_alignment_client_factory: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._settings = settings or get_settings()
        self._client = client or ElevenLabsConvertWithTimestampsClient()
        self._client_factory = client_factory or ElevenLabsConvertWithTimestampsClient
        self._forced_alignment_client_factory = (
            forced_alignment_client_factory or ElevenLabsForcedAlignmentClient
        )

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
        self._validate_frozen_voice_authority(context=context, details=details)

    @staticmethod
    def _validate_frozen_voice_authority(
        *, context: WorkflowStageContext, details: dict[str, Any]
    ) -> None:
        """Resolve exact persisted voice truth; environment is never authority."""

        # The script hash was sealed by package readiness.  Binding every
        # source object here closes the former gap where an operation carried
        # IDs but the real media adapter still trusted global voice config.
        try:
            frozen_voice_authority_gate(
                authority=details,
                script_hash=str(details["qualified_script_hash"]),
                voice_id=str(details["voice_id"]),
                model_id=str(details["model_id"]),
            )
            from app.db.models.voice_authority import (
                ApprovedVoicePool,
                NarrationPerformancePlan,
                NarrationVoiceSnapshot,
                TTSPerformanceProjection,
                VoiceCastingDecision,
            )

            pool = context.session.get(
                ApprovedVoicePool, uuid.UUID(str(details["approved_voice_pool_id"]))
            )
            casting = context.session.get(
                VoiceCastingDecision,
                uuid.UUID(str(details["voice_casting_decision_id"])),
            )
            snapshot = context.session.get(
                NarrationVoiceSnapshot,
                uuid.UUID(str(details["narration_voice_snapshot_id"])),
            )
            performance = context.session.get(
                NarrationPerformancePlan,
                uuid.UUID(str(details["narration_performance_plan_id"])),
            )
            projection = context.session.get(
                TTSPerformanceProjection,
                uuid.UUID(str(details["tts_performance_projection_id"])),
            )
        except (KeyError, ValueError) as exc:
            raise ValidationFailureError(
                "REAL_PRODUCTION_VOICE_AUTHORITY_REQUIRED"
            ) from exc
        if (
            pool is None
            or casting is None
            or snapshot is None
            or performance is None
            or projection is None
            or pool.content_hash != details["approved_voice_pool_hash"]
            or casting.content_hash != details["voice_casting_decision_hash"]
            or snapshot.content_hash != details["narration_voice_snapshot_hash"]
            or performance.content_hash != details["narration_performance_plan_hash"]
            or projection.content_hash != details["tts_performance_projection_hash"]
            or casting.video_project_id != context.run.video_project_id
            or snapshot.video_project_id != context.run.video_project_id
            or performance.video_project_id != context.run.video_project_id
            or projection.video_project_id != context.run.video_project_id
            or snapshot.voice_casting_decision_id != casting.id
            or snapshot.approved_voice_pool_id != pool.id
            or performance.narration_voice_snapshot_id != snapshot.id
            or projection.narration_performance_plan_id != performance.id
            or projection.narration_voice_snapshot_id != snapshot.id
            or snapshot.qualified_script_hash != details["qualified_script_hash"]
            or snapshot.voice_id != details["voice_id"]
            or snapshot.model_id != details["model_id"]
            or projection.model_id != details["model_id"]
            or projection.execution_strategy != details.get("tts_execution_strategy")
            or projection.capability_profile_version
            != details.get("capability_profile_version")
        ):
            raise ValidationFailureError("REAL_PRODUCTION_VOICE_AUTHORITY_MISMATCH")

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
            video_project_id=project.id,
        )
        with self._session_factory() as session:
            persisted_project = session.get(VideoProject, project.id)
            if persisted_project is None:
                raise ValidationFailureError("V2_CAPTION_SIDECAR_PROJECT_REQUIRED")
            sidecar = _persist_sidecar_artifacts(
                session=session,
                project=persisted_project,
                command_id=context.command_id,
                script_content=dict(script.content or {}),
                script_content_hash=script.content_hash,
                audio=audio,
                effect_dir=effect_dir,
            )
            session.commit()
        timeline_ref = f"v2-effect://{ledger_id}/canonical-media-timeline"
        timeline = _build_timeline(
            run=run,
            project=project,
            package=package,
            script=script,
            visual=visual,
            timeline_ref=timeline_ref,
            audio={**audio, **sidecar},
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
            **sidecar,
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
                    "caption_ref": sidecar["caption_ref"],
                    "caption_checksum": sidecar["caption_checksum"],
                    "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
                    "subtitle_qc_state": sidecar["subtitle_qc_state"],
                },
                authority_refs=WorkflowAuthorityRefs(
                    video_project_id=project.id,
                    canonical_media_timeline_ref=timeline_ref,
                    canonical_media_timeline_hash=timeline_hash,
                ),
            ),
            journal,
        )

    def reconcile_recovered_media(
        self,
        *,
        workflow_run_id: Any,
        ledger_id: Any,
        audio: dict[str, Any],
        recovery_authority_id: Any,
        recovery_authority_hash: str,
    ) -> WorkflowStageResult:
        """Finish MEDIA from already-sealed TTS bytes and recovered timing.

        This entrypoint deliberately has no TTS client path.  It may only turn
        the existing one-invocation uncertain MEDIA ledger into VERIFIED after
        the recovery service has supplied a strict timing seed.
        """

        with self._session_factory() as session:
            from app.db.models.v2_effect import V2ProductionEffectLedger

            ledger = session.scalar(
                select(V2ProductionEffectLedger)
                .where(V2ProductionEffectLedger.id == ledger_id)
                .with_for_update()
            )
            if (
                ledger is None
                or ledger.workflow_run_id != workflow_run_id
                or ledger.stage != ProductionWorkflowStage.MEDIA.value
                or ledger.adapter_key != V2_ELEVENLABS_NARRATION_ADAPTER_KEY
                or ledger.state not in {"FAILED_UNCERTAIN", "VERIFIED"}
                or ledger.effect_invocation_count != 1
                or (
                    ledger.state == "FAILED_UNCERTAIN"
                    and ledger.result_hash is not None
                )
            ):
                raise ValidationFailureError(
                    "V2_NARRATION_TIMING_RECOVERY_LEDGER_INVALID"
                )
            command_id = ledger.command_id

        with self._command_lock(command_id):
            with self._session_factory() as session:
                from app.db.models.v2_effect import V2ProductionEffectLedger

                ledger = session.scalar(
                    select(V2ProductionEffectLedger).where(
                        V2ProductionEffectLedger.id == ledger_id
                    )
                )
                recovery_journal = (
                    dict(ledger.effect_journal or {}) if ledger is not None else {}
                )
                if ledger is not None and ledger.state == "VERIFIED":
                    if (
                        ledger.workflow_run_id != workflow_run_id
                        or ledger.result_type
                        != "V2_ELEVENLABS_CANONICAL_MEDIA_TIMELINE"
                        or ledger.result_hash is None
                        or recovery_journal.get("timeline_hash") != ledger.result_hash
                        or (ledger.authority_refs or {}).get(
                            "canonical_media_timeline_hash"
                        )
                        != ledger.result_hash
                        or (ledger.result_payload or {}).get(
                            "timing_recovery_authority_id"
                        )
                        != str(recovery_authority_id)
                        or (ledger.result_payload or {}).get(
                            "timing_recovery_authority_hash"
                        )
                        != recovery_authority_hash
                        or recovery_journal.get("timing_recovery_authority_id")
                        != str(recovery_authority_id)
                        or recovery_journal.get("timing_recovery_authority_hash")
                        != recovery_authority_hash
                        or recovery_journal.get("provider_call_count") != 2
                        or recovery_journal.get("tts_provider_call_count") != 1
                        or recovery_journal.get("tts_retry_count") != 0
                        or recovery_journal.get("forced_alignment_provider_call_count")
                        != 1
                    ):
                        raise ValidationFailureError(
                            "V2_NARRATION_TIMING_RECOVERY_VERIFIED_LEDGER_MISMATCH"
                        )
                    return _result_from_ledger(ledger, reconciled=True)
                if (
                    ledger is None
                    or ledger.state != "FAILED_UNCERTAIN"
                    or ledger.effect_invocation_count != 1
                    or ledger.result_hash is not None
                ):
                    raise ValidationFailureError(
                        "V2_NARRATION_TIMING_RECOVERY_LEDGER_INVALID"
                    )
            with self._session_factory() as session:
                run, project, package, script, visual = _production_inputs(
                    session, workflow_run_id
                )
            effect_dir = self._effect_dir(command_id)
            with self._session_factory() as session:
                persisted_project = session.get(VideoProject, project.id)
                if persisted_project is None:
                    raise ValidationFailureError("V2_CAPTION_SIDECAR_PROJECT_REQUIRED")
                sidecar = _persist_sidecar_artifacts(
                    session=session,
                    project=persisted_project,
                    command_id=command_id,
                    script_content=dict(script.content or {}),
                    script_content_hash=script.content_hash,
                    audio=audio,
                    effect_dir=effect_dir,
                )
                session.commit()
            timeline_ref = f"v2-effect://{ledger_id}/canonical-media-timeline"
            timeline = _build_timeline(
                run=run,
                project=project,
                package=package,
                script=script,
                visual=visual,
                timeline_ref=timeline_ref,
                audio={**audio, **sidecar},
            )
            timeline_hash = content_hash(timeline)
            timeline_path = effect_dir / "canonical-media-timeline.json"
            _persist_exact_json(timeline_path, timeline)
            journal = {
                "schema_version": "vcos.production-effect-journal.v1",
                "command_id": command_id,
                "stage": "MEDIA",
                "state": "VERIFIED",
                "effect_invocation_count": 1,
                "provider_call_count": 2,
                "tts_provider_call_count": 1,
                "tts_retry_count": 0,
                "forced_alignment_provider_call_count": 1,
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
                "timing_recovery_authority_id": str(recovery_authority_id),
                "timing_recovery_authority_hash": recovery_authority_hash,
                **sidecar,
            }
            _persist_exact_json(
                effect_dir / "effect-journal.json",
                journal,
                allow_reconciled_update=True,
            )
            result = WorkflowStageResult(
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
                    "caption_ref": sidecar["caption_ref"],
                    "caption_checksum": sidecar["caption_checksum"],
                    "subtitle_qc_ref": sidecar["subtitle_qc_ref"],
                    "subtitle_qc_state": sidecar["subtitle_qc_state"],
                    "timing_recovery_authority_id": str(recovery_authority_id),
                    "timing_recovery_authority_hash": recovery_authority_hash,
                },
                authority_refs=WorkflowAuthorityRefs(
                    video_project_id=project.id,
                    canonical_media_timeline_ref=timeline_ref,
                    canonical_media_timeline_hash=timeline_hash,
                ),
                reason_codes=["V2_NARRATION_TIMING_RECOVERED"],
            )
            return self._verify_effect(
                ledger_id,
                result=result,
                journal=journal,
                reconciled=True,
            )

    def _prepare_real_audio(
        self,
        *,
        effect_dir: Path,
        command_id: str,
        package: Any,
        script: Any,
        operation: V2AuthorizedAdapterOperation,
        video_project_id: Any,
    ) -> dict[str, Any]:
        details = dict(operation.parameters["provider_execution"])
        script_text = str((script.content or {}).get("narration_text") or "").strip()
        if not script_text:
            raise ValidationFailureError("V2_ELEVENLABS_APPROVED_SCRIPT_REQUIRED")
        return self._prepare_projection_audio(
            effect_dir=effect_dir,
            command_id=command_id,
            package=package,
            script=script,
            details=details,
            operation=operation,
            video_project_id=video_project_id,
        )

    def _prepare_projection_audio(
        self,
        *,
        effect_dir: Path,
        command_id: str,
        package: Any,
        script: Any,
        details: dict[str, Any],
        operation: V2AuthorizedAdapterOperation,
        video_project_id: Any,
    ) -> dict[str, Any]:
        """Execute the exact frozen projection, one paid effect per segment."""

        script_text = str((script.content or {}).get("narration_text") or "").strip()
        with self._session_factory() as session:
            from app.db.models.voice_authority import TTSPerformanceProjection

            projection = session.get(
                TTSPerformanceProjection, details["tts_performance_projection_id"]
            )
            if (
                projection is None
                or projection.content_hash != details["tts_performance_projection_hash"]
                or projection.video_project_id != video_project_id
            ):
                raise ValidationFailureError("REAL_PRODUCTION_VOICE_AUTHORITY_MISMATCH")
            raw_segments = list(projection.segments)
        raw_segments.sort(key=lambda item: int(item["ordinal"]))
        materialized: list[dict[str, Any]] = []
        for index, segment in enumerate(raw_segments):
            start, end = (
                int(segment["source_text_start"]),
                int(segment["source_text_end"]),
            )
            text = script_text[start:end]
            materialized.append(
                {
                    **segment,
                    "segment_index": index,
                    "source_text_start": start,
                    "source_text_end": end,
                    "text_hash": content_hash({"text": text}),
                    "canonical_text": text,
                }
            )
        narration_text_fidelity_gate(canonical_text=script_text, segments=materialized)
        estimated_tts = Decimal(str(operation.max_cost_usd))
        budget = CombinedReplacementBudget(
            new_tts_projected_cost_usd=estimated_tts,
            forced_alignment_projected_cost_usd=Decimal(
                str(details.get("forced_alignment_projected_cost_usd", "0"))
            ),
            ai_image_projected_cost_usd=Decimal(
                str(details.get("ai_image_projected_cost_usd", "0"))
            ),
            ai_video_projected_cost_usd=Decimal(
                str(details.get("ai_video_projected_cost_usd", "0"))
            ),
            other_metered_effects_projected_cost_usd=Decimal(
                str(details.get("other_metered_effects_projected_cost_usd", "0"))
            ),
            approved_ceiling_usd=Decimal(
                str(
                    details.get(
                        "combined_replacement_ceiling_usd", operation.max_cost_usd
                    )
                )
            ),
        )
        budget.require_authorized()
        api_key = self._api_key_or_block()
        capability = elevenlabs_capability(str(details["model_id"]))
        effects = NarrationSegmentExecutionService(self._session_factory)
        executions: list[Any] = []
        for index, segment in enumerate(materialized):
            context: dict[str, Any] = {}
            if index:
                context["previous_text"] = materialized[index - 1]["canonical_text"]
            if index + 1 < len(materialized):
                context["next_text"] = materialized[index + 1]["canonical_text"]
            if (
                projection.execution_strategy == "CONTEXT_STITCHED_MULTI_REQUEST"
                and executions
            ):
                request_id = executions[-1].timing_seed.provider_request_id
                if request_id:
                    context["previous_request_ids"] = [request_id]
            provider_projection = provider_text_projection(
                canonical_text=segment["canonical_text"],
                context=context,
                capability=capability,
            )
            settings = (
                dict(segment["voice_settings"])
                if capability.supports_voice_settings
                else {}
            )
            output = effect_dir / f"narration-segment-{index:03d}.mp3"
            effect = effects.intend_and_submit(
                video_project_id=video_project_id,
                authority=details,
                segment=segment,
                canonical_text=segment["canonical_text"],
                provider_projection=provider_projection,
                voice_id=str(details["voice_id"]),
                model_id=str(details["model_id"]),
                settings=settings,
                context=context,
                estimated_cost_usd=estimated_tts / len(materialized),
            )
            if effect.state == "VERIFIED":
                raise ValidationFailureError(
                    "NARRATION_SEGMENT_RECONCILIATION_AUDIO_REQUIRED"
                )
            client = self._client if index == 0 else self._client_factory()
            normalized = _temporal_normalized(
                {"normalized_text": segment["canonical_text"]}
            )
            try:
                execution = client.execute_once(
                    api_key=api_key,
                    normalized=normalized,
                    voice_id=str(details["voice_id"]),
                    model_id=str(details["model_id"]),
                    voice_settings=settings,
                    provider_context={
                        key: value
                        for key, value in provider_projection.items()
                        if key not in {"text", "apply_text_normalization"}
                    },
                    destination=output,
                    audio_asset_ref=f"v2-elevenlabs://{effect.provider_effect_key}",
                )
            except Exception as exc:
                effects.mark_unknown(effect_id=effect.id)
                raise WorkflowStageError(
                    classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                    error_code="V2_ELEVENLABS_PROVIDER_FAILURE",
                    summary="A submitted narration segment has an uncertain provider outcome and was not retried.",
                    incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                    retry_eligible=False,
                ) from exc
            effects.verify(
                effect_id=effect.id,
                provider_request_hash=execution.request_hash,
                provider_request_id=execution.timing_seed.provider_request_id,
                audio_ref=execution.audio_asset_ref,
                audio_checksum=execution.audio_sha256,
                duration_ms=execution.audio_duration_ms,
            )
            executions.append(execution)
        return self._compile_canonical_projection_audio(
            effect_dir=effect_dir,
            package=package,
            script_text=script_text,
            details=details,
            executions=executions,
            materialized=materialized,
            api_key=api_key,
        )

    def _api_key_or_block(self) -> str:
        api_key = (
            self._settings.elevenlabs_api_key.get_secret_value()
            if self._settings.elevenlabs_api_key is not None
            else ""
        )
        if not api_key.strip():
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_REAL_ELEVENLABS_BLOCKED_CREDENTIAL",
                summary="ElevenLabs credential is unavailable; no narration request was attempted.",
                incident_type="CREDENTIAL_MISSING",
                retry_eligible=False,
            )
        return api_key

    def _compile_canonical_projection_audio(
        self,
        *,
        effect_dir: Path,
        package: Any,
        script_text: str,
        details: dict[str, Any],
        executions: Sequence[Any],
        materialized: Sequence[dict[str, Any]],
        api_key: str,
    ) -> dict[str, Any]:
        if len(executions) == 1:
            execution = executions[0]
            return self._receipt_from_execution(execution, package=package, identity={})
        output = effect_dir / "canonical-narration.mp3"
        stitch = AudioStitchCompiler().stitch(
            audio_paths=[execution.audio_path for execution in executions],
            destination=output,
        )
        from app.services.v2_native_effects import _probe_duration_ms

        duration_ms = _probe_duration_ms(self._builder.ffprobe, output)
        if not (
            package.duration_contract.minimum_duration_ms
            <= duration_ms
            <= package.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError("V2_ELEVENLABS_DURATION_OUTSIDE_CONTRACT")
        offsets: list[dict[str, Any]] = []
        offset = 0
        for index, execution in enumerate(executions):
            offsets.append(
                {
                    "segment_index": index,
                    "canonical_start_ms": offset,
                    "duration_ms": execution.audio_duration_ms,
                    "audio_checksum": execution.audio_sha256,
                }
            )
            offset += execution.audio_duration_ms
        qc = seam_qc(segments=offsets)
        if qc.state != "PASS":
            raise ValidationFailureError(
                "NARRATION_SEAM_QC_FAILED:" + ",".join(qc.reason_codes)
            )
        normalized = _temporal_normalized({"normalized_text": script_text})
        alignment_client = self._forced_alignment_client_factory()
        alignment = alignment_client.execute_once(
            api_key=api_key,
            normalized=normalized,
            audio_path=output,
            audio_asset_ref=f"v2-elevenlabs://canonical/{stitch['audio_checksum']}",
            audio_duration_ms=duration_ms,
        )
        timing_seed = _timing_seed_from_forced_alignment(
            normalized=normalized,
            evidence=alignment.evidence,
            audio_asset_ref=f"v2-elevenlabs://canonical/{stitch['audio_checksum']}",
            audio_duration_ms=duration_ms,
            voice_id=str(details["voice_id"]),
            model_id=str(details["model_id"]),
        )
        return {
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
            "audio_asset_ref": timing_seed.audio_asset_ref,
            "audio_checksum": stitch["audio_checksum"],
            "audio_relative_path": self._relative(output),
            "duration_ms": duration_ms,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_FORCED_ALIGNMENT_FINAL_CANONICAL_AUDIO",
            "provider_request_hash": content_hash(
                [item.request_hash for item in executions]
            ),
            "provider_request_id": alignment.evidence.provider_request_id,
            "timing_seed": timing_seed.model_dump(mode="json"),
            "timing_seed_hash": timing_seed.content_hash,
            "usage_metadata": {
                "segment_count": len(executions),
                "seam_qc_hash": qc.content_hash,
                "segment_offsets": offsets,
            },
            "actual_cost_usd": None,
            "estimated_cost_usd": "0",
            "secret_values_exposed": False,
        }

    def _reconcile_sealed_output(
        self,
        *,
        identity: dict[str, Any],
        output: Path,
        provider_response_path: Path,
        receipt_path: Path,
        package: Any,
    ) -> dict[str, Any]:
        """Recover only a locally interrupted post-provider receipt write.

        Audio bytes alone do not prove timing/alignment provenance.  The
        response journal is therefore mandatory: without it the provider
        outcome is retained as uncertain and never retried.
        """

        if not provider_response_path.exists():
            raise WorkflowStageError(
                classification=WorkflowFailureClassification.BLOCK_EXTERNAL_FAILURE,
                error_code="V2_ELEVENLABS_OUTCOME_UNCERTAIN",
                summary=(
                    "A sealed ElevenLabs audio file lacks a persisted timing "
                    "response proof; no duplicate request was attempted."
                ),
                incident_type="PROVIDER_OUTCOME_UNCERTAIN",
                retry_eligible=False,
            )
        provider_response = _load_json(provider_response_path)
        receipt = self._receipt_from_provider_response(
            identity=identity,
            output=output,
            provider_response=provider_response,
            package=package,
        )
        _write_json_atomic(receipt_path, receipt)
        return receipt

    @staticmethod
    def _receipt_from_execution(
        execution: Any, *, package: Any, identity: dict[str, Any]
    ) -> dict[str, Any]:
        if not (
            package.duration_contract.minimum_duration_ms
            <= execution.audio_duration_ms
            <= package.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError("V2_ELEVENLABS_DURATION_OUTSIDE_CONTRACT")
        return {
            **identity,
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
            "audio_asset_ref": execution.audio_asset_ref,
            "audio_checksum": execution.audio_sha256,
            "audio_relative_path": str(execution.audio_path),
            "duration_ms": execution.audio_duration_ms,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
            "provider_request_hash": execution.request_hash,
            "provider_request_id": execution.timing_seed.provider_request_id,
            "timing_seed": execution.timing_seed.model_dump(mode="json"),
            "timing_seed_hash": execution.timing_seed.content_hash,
            "usage_metadata": execution.usage_metadata,
            "actual_cost_usd": None,
            "estimated_cost_usd": "0",
            "secret_values_exposed": False,
        }

    @staticmethod
    def _receipt_from_provider_response(
        *,
        identity: dict[str, Any],
        output: Path,
        provider_response: dict[str, Any],
        package: Any,
    ) -> dict[str, Any]:
        if (
            provider_response.get("schema_version")
            != V2_ELEVENLABS_PROVIDER_RESPONSE_JOURNAL_SCHEMA
            or provider_response.get("request_identity_hash") != content_hash(identity)
            or provider_response.get("audio_relative_path")
            != identity["output_relative_path"]
            or provider_response.get("audio_checksum") != _sha256_file(output)
            or provider_response.get("audio_asset_ref") in {None, ""}
        ):
            raise ValidationFailureError("V2_ELEVENLABS_PROVIDER_RESPONSE_MISMATCH")
        try:
            duration_ms = int(provider_response["duration_ms"])
            timing_seed = NarrationTimingSeed.model_validate(
                provider_response["timing_seed"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "V2_ELEVENLABS_PROVIDER_RESPONSE_MISMATCH"
            ) from exc
        if (
            duration_ms <= 0
            or not (
                package.duration_contract.minimum_duration_ms
                <= duration_ms
                <= package.duration_contract.maximum_duration_ms
            )
            or timing_seed.content_hash != provider_response.get("timing_seed_hash")
            or timing_seed.audio_asset_ref != provider_response.get("audio_asset_ref")
            or timing_seed.audio_duration_ms != duration_ms
            or not timing_seed.timing_available
        ):
            raise ValidationFailureError("V2_ELEVENLABS_PROVIDER_RESPONSE_MISMATCH")
        return {
            **identity,
            "audio_strategy": V2_ELEVENLABS_NARRATION_STRATEGY,
            "audio_asset_ref": provider_response["audio_asset_ref"],
            "audio_checksum": provider_response["audio_checksum"],
            "audio_relative_path": provider_response["audio_relative_path"],
            "duration_ms": duration_ms,
            "narration_present": True,
            "alignment_method": "ELEVENLABS_TIMESTAMPS",
            "provider_request_hash": provider_response.get("provider_request_hash"),
            "provider_request_id": provider_response.get("provider_request_id"),
            "timing_seed": timing_seed.model_dump(mode="json"),
            "timing_seed_hash": timing_seed.content_hash,
            "usage_metadata": provider_response.get("usage_metadata") or {},
            "actual_cost_usd": provider_response.get("actual_cost_usd"),
            "secret_values_exposed": False,
        }


def _persist_sidecar_artifacts(
    *,
    session: Any,
    project: VideoProject,
    command_id: str,
    script_content: dict[str, Any],
    script_content_hash: str,
    audio: dict[str, Any],
    effect_dir: Path,
) -> dict[str, Any]:
    """Persist V2's canonical SRT lineage without exposing it to FFmpeg.

    The narrator already owns the exact provider timestamps.  Keeping this
    materialization in that stage prevents a later renderer from deriving a
    new, parallel caption timeline or silently replacing an invalid sidecar.
    """

    transcript = str(script_content.get("narration_text") or "").strip()
    locale = str(
        script_content.get("locale")
        or script_content.get("language")
        or audio.get("caption_locale")
        or ""
    ).strip()
    if not transcript:
        raise ValidationFailureError("CAPTION_SIDECAR_TRANSCRIPT_MISSING")
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", locale):
        raise ValidationFailureError("CAPTION_SIDECAR_LOCALE_INVALID")
    raw_seed = audio.get("timing_seed")
    try:
        timing_seed = NarrationTimingSeed.model_validate(raw_seed)
    except (TypeError, ValueError) as exc:
        raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_MISSING") from exc
    if (
        timing_seed.content_hash != audio.get("timing_seed_hash")
        or not timing_seed.timing_available
        or timing_seed.audio_asset_ref != audio.get("audio_asset_ref")
        or timing_seed.audio_duration_ms != int(audio.get("duration_ms") or 0)
    ):
        raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
    seed_payload = timing_seed.model_dump(mode="json", exclude={"content_hash"})
    if content_hash(seed_payload) != timing_seed.content_hash:
        raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")

    timed_words = _timed_words_from_seed(timing_seed, transcript)
    cues = _build_srt_cues(timed_words)
    srt_text = _render_srt(cues)
    try:
        srt_bytes = srt_text.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ValidationFailureError("CAPTION_SIDECAR_UTF8_INVALID") from exc
    caption_path = effect_dir / "canonical-captions.srt"
    _write_srt_once(caption_path, srt_bytes)
    caption_checksum = _sha256_file(caption_path)
    transcript_content = {
        "schema_version": V2_SIDECAR_SCHEMA,
        "kind": "TRANSCRIPT",
        "language": locale.split("-", 1)[0].lower(),
        "locale": locale,
        "text": transcript,
        "script_content_hash": script_content_hash,
        "audio_asset_ref": audio["audio_asset_ref"],
    }
    transcript_version = _ensure_sidecar_artifact_version(
        session=session,
        project=project,
        artifact_type=V2_TRANSCRIPT_ARTIFACT_TYPE,
        command_id=command_id,
        content=transcript_content,
        source_manifest={
            "items": [
                {"type": "approved_script", "content_hash": script_content_hash},
                {"type": "narration_audio", "ref": audio["audio_asset_ref"]},
            ]
        },
    )
    timed_words_content = {
        "schema_version": V2_SIDECAR_SCHEMA,
        "kind": "TIMED_WORDS",
        "language": locale.split("-", 1)[0].lower(),
        "locale": locale,
        "audio_asset_ref": audio["audio_asset_ref"],
        "audio_duration_ms": timing_seed.audio_duration_ms,
        "timing_seed_hash": timing_seed.content_hash,
        "transcript_ref": f"artifact-version://{transcript_version.id}",
        "transcript_hash": transcript_version.content_hash,
        "words": timed_words,
    }
    timed_words_version = _ensure_sidecar_artifact_version(
        session=session,
        project=project,
        artifact_type=V2_TIMED_WORDS_ARTIFACT_TYPE,
        command_id=command_id,
        content=timed_words_content,
        source_manifest={
            "items": [
                {
                    "type": "transcript",
                    "artifact_version_id": str(transcript_version.id),
                },
                {
                    "type": "elevenlabs_timing_seed",
                    "content_hash": timing_seed.content_hash,
                },
            ]
        },
    )
    caption_content = {
        "schema_version": V2_SIDECAR_SCHEMA,
        "kind": "SRT",
        "language": locale.split("-", 1)[0].lower(),
        "locale": locale,
        "file_name": "canonical-captions.srt",
        "mime_type": "application/x-subrip",
        "checksum_sha256": caption_checksum,
        "size_bytes": len(srt_bytes),
        "srt_text": srt_text,
        "transcript_ref": f"artifact-version://{transcript_version.id}",
        "transcript_hash": transcript_version.content_hash,
        "timed_words_ref": f"artifact-version://{timed_words_version.id}",
        "timed_words_hash": timed_words_version.content_hash,
        "audio_asset_ref": audio["audio_asset_ref"],
        "audio_duration_ms": timing_seed.audio_duration_ms,
        "cues": cues,
    }
    caption_version = _ensure_sidecar_artifact_version(
        session=session,
        project=project,
        artifact_type=V2_CAPTION_SRT_ARTIFACT_TYPE,
        command_id=command_id,
        content=caption_content,
        source_manifest={
            "items": [
                {
                    "type": "timed_words",
                    "artifact_version_id": str(timed_words_version.id),
                },
                {
                    "type": "transcript",
                    "artifact_version_id": str(transcript_version.id),
                },
            ]
        },
    )
    qc_content = _subtitle_qc_content(
        caption_content=caption_content,
        timed_words=timed_words,
        transcript_hash=transcript_version.content_hash,
        caption_artifact_hash=caption_version.content_hash,
        caption_ref=f"artifact-version://{caption_version.id}",
    )
    if qc_content["status"] != "PASS":
        raise ValidationFailureError(
            "SUBTITLE_QC_FAILED:" + ",".join(qc_content["reason_codes"])
        )
    qc_version = _ensure_sidecar_artifact_version(
        session=session,
        project=project,
        artifact_type=V2_SUBTITLE_QC_ARTIFACT_TYPE,
        command_id=command_id,
        content=qc_content,
        source_manifest={
            "items": [
                {"type": "caption_srt", "artifact_version_id": str(caption_version.id)},
                {
                    "type": "timed_words",
                    "artifact_version_id": str(timed_words_version.id),
                },
            ]
        },
    )
    return {
        "caption_relative_path": str(caption_path.relative_to(effect_dir.parents[1])),
        "caption_checksum": caption_checksum,
        "caption_ref": f"artifact-version://{caption_version.id}",
        "caption_artifact_version_id": str(caption_version.id),
        "caption_artifact_hash": caption_version.content_hash,
        "transcript_ref": f"artifact-version://{transcript_version.id}",
        "transcript_artifact_version_id": str(transcript_version.id),
        "transcript_hash": transcript_version.content_hash,
        "timed_words_ref": f"artifact-version://{timed_words_version.id}",
        "timed_words_artifact_version_id": str(timed_words_version.id),
        "timed_words_hash": timed_words_version.content_hash,
        # This exact sidecar-derived list is consumed once by the canonical
        # narration-unit binder.  It is not a second caption timeline.
        "timed_words": timed_words,
        "subtitle_qc_ref": f"artifact-version://{qc_version.id}",
        "subtitle_qc_artifact_version_id": str(qc_version.id),
        "subtitle_qc_hash": qc_version.content_hash,
        "subtitle_qc_state": "PASS",
        "caption_language": locale.split("-", 1)[0].lower(),
        "caption_locale": locale,
    }


def _timed_words_from_seed(
    seed: NarrationTimingSeed,
    transcript: str,
) -> list[dict[str, Any]]:
    if seed.provider_key == "elevenlabs_forced_alignment_recovery":
        metadata = dict(seed.response_metadata or {})
        audit = metadata.get("alignment_audit")
        raw_words = metadata.get("caption_timed_words")
        canonical_words = [match.group() for match in re.finditer(r"\S+", transcript)]
        if (
            not isinstance(audit, dict)
            or audit.get("exact_raw_character_sequence") is not True
            or audit.get("exact_word_token_coverage") is not True
            or audit.get("zero_duration_character_timing_synthesized") is not False
            or audit.get("caption_timing_source")
            != "ELEVENLABS_FORCED_ALIGNMENT_WORD_BOUNDARIES"
            or not isinstance(raw_words, list)
            or len(raw_words) != len(canonical_words)
            or audit.get("provider_word_count") != len(raw_words)
            or audit.get("canonical_spoken_token_count") != len(raw_words)
        ):
            raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
        words: list[dict[str, Any]] = []
        previous_end = -1
        for index, (raw, canonical_text) in enumerate(
            zip(raw_words, canonical_words, strict=True), start=1
        ):
            if not isinstance(raw, dict):
                raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
            try:
                raw_index = int(raw["index"])
                start_ms = int(raw["start_ms"])
                end_ms = int(raw["end_ms"])
            except (KeyError, TypeError, ValueError):
                raise ValidationFailureError(
                    "CAPTION_SIDECAR_TIMED_WORDS_INVALID"
                ) from None
            if (
                raw_index != index
                or raw.get("text") != canonical_text
                or not isinstance(raw.get("provider_word_id"), str)
                or not raw["provider_word_id"]
                or not isinstance(raw.get("source_spoken_token_ids"), list)
                or len(raw["source_spoken_token_ids"]) != 1
                or start_ms < previous_end
                or end_ms <= start_ms
                or end_ms > seed.audio_duration_ms
            ):
                raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
            words.append(
                {
                    "index": index,
                    "text": canonical_text,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                }
            )
            previous_end = end_ms
        if not words:
            raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_MISSING")
        return words

    characters = sorted(
        seed.normalized_character_alignment, key=lambda item: item.character_index
    )
    if not characters or [item.character_index for item in characters] != list(
        range(len(characters))
    ):
        raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
    reconstructed = "".join(item.character for item in characters)
    if reconstructed != transcript:
        raise ValidationFailureError("CAPTION_SIDECAR_TRANSCRIPT_PROVENANCE_INVALID")
    words: list[dict[str, Any]] = []
    previous_end = -1
    for index, match in enumerate(re.finditer(r"\S+", reconstructed), start=1):
        span = characters[match.start() : match.end()]
        start_ms, end_ms = span[0].start_ms, span[-1].end_ms
        if (
            start_ms < previous_end
            or end_ms <= start_ms
            or end_ms > seed.audio_duration_ms
        ):
            raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_INVALID")
        words.append(
            {
                "index": index,
                "text": match.group(),
                "start_ms": start_ms,
                "end_ms": end_ms,
            }
        )
        previous_end = end_ms
    if not words:
        raise ValidationFailureError("CAPTION_SIDECAR_TIMED_WORDS_MISSING")
    return words


def _build_srt_cues(words: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    cues: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal group
        if not group:
            return
        text = " ".join(str(word["text"]) for word in group)
        lines = _subtitle_lines(text)
        cues.append(
            {
                "index": len(cues) + 1,
                "start_ms": int(group[0]["start_ms"]),
                "end_ms": int(group[-1]["end_ms"]),
                "lines": lines,
                "word_start_index": int(group[0]["index"]),
                "word_end_index": int(group[-1]["index"]),
            }
        )
        group = []

    for word in words:
        candidate = [*group, word]
        candidate_text = " ".join(str(item["text"]) for item in candidate)
        duration_ms = int(candidate[-1]["end_ms"]) - int(candidate[0]["start_ms"])
        if group and (
            len(candidate_text) > 84
            or _subtitle_lines_if_valid(candidate_text) is None
            or duration_ms > 6_000
        ):
            flush()
            candidate = [word]
        group = candidate
        duration_ms = int(group[-1]["end_ms"]) - int(group[0]["start_ms"])
        if duration_ms >= 1_000 and re.search(r"[.!?;:]$", str(word["text"])):
            flush()
    flush()
    if len(cues) > 1 and (cues[-1]["end_ms"] - cues[-1]["start_ms"]) < 800:
        previous, final = cues[-2], cues[-1]
        merged_text = " ".join([*previous["lines"], *final["lines"]])
        merged_lines = _subtitle_lines_if_valid(merged_text)
        merged_duration_ms = int(final["end_ms"]) - int(previous["start_ms"])
        if (
            len(merged_text) <= 84
            and merged_lines is not None
            and merged_duration_ms <= 6_000
        ):
            previous.update(
                {
                    "end_ms": final["end_ms"],
                    "lines": merged_lines,
                    "word_end_index": final["word_end_index"],
                }
            )
            cues.pop()
    for index, cue in enumerate(cues, start=1):
        cue["index"] = index
    return _repair_srt_reading_speed(cues, words)


def _repair_srt_reading_speed(
    cues: list[dict[str, Any]], words: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Repartition only an over-fast cue and its predecessor.

    Boundaries move only across existing provider-timed words.  No timestamp is
    changed or synthesized.  If the local window has no policy-valid
    repartition, leave it unchanged so SubtitleQC remains blocking.
    """

    by_index = {int(word["index"]): word for word in words}
    position = 1
    while position < len(cues):
        current = cues[position]
        if _cue_cps(current) <= 20:
            position += 1
            continue
        previous = cues[position - 1]
        window = [
            by_index[index]
            for index in range(
                int(previous["word_start_index"]),
                int(current["word_end_index"]) + 1,
            )
        ]
        old_boundary = (
            int(previous["word_end_index"]) - int(previous["word_start_index"]) + 1
        )
        replacement: list[dict[str, Any]] | None = None
        # Prefer retaining two cues.  A third cue is allowed only when moving a
        # single old boundary cannot satisfy the unchanged QC policy.
        for part_count in (2, 3):
            candidates: list[
                tuple[tuple[int, tuple[int, ...]], list[dict[str, Any]]]
            ] = []
            for splits in combinations(range(1, len(window)), part_count - 1):
                boundaries = (0, *splits, len(window))
                groups = [
                    window[boundaries[index] : boundaries[index + 1]]
                    for index in range(part_count)
                ]
                if not all(_srt_group_qc_valid(group) for group in groups):
                    continue
                score = (
                    sum(abs(split - old_boundary) for split in splits),
                    splits,
                )
                candidates.append(
                    (score, [_cue_from_words(group, index=0) for group in groups])
                )
            if candidates:
                replacement = min(candidates, key=lambda item: item[0])[1]
                break
        if replacement is None:
            position += 1
            continue
        cues[position - 1 : position + 1] = replacement
        position = max(position - 1, 1)
    for index, cue in enumerate(cues, start=1):
        cue["index"] = index
    return cues


def _srt_group_qc_valid(group: Sequence[dict[str, Any]]) -> bool:
    if not group:
        return False
    text = " ".join(str(word["text"]) for word in group)
    duration_ms = int(group[-1]["end_ms"]) - int(group[0]["start_ms"])
    return bool(
        800 <= duration_ms <= 6_000
        and len(text) <= 84
        and _subtitle_lines_if_valid(text) is not None
        and len(text) / (duration_ms / 1_000) <= 20
    )


def _cue_from_words(group: Sequence[dict[str, Any]], *, index: int) -> dict[str, Any]:
    text = " ".join(str(word["text"]) for word in group)
    return {
        "index": index,
        "start_ms": int(group[0]["start_ms"]),
        "end_ms": int(group[-1]["end_ms"]),
        "lines": _subtitle_lines(text),
        "word_start_index": int(group[0]["index"]),
        "word_end_index": int(group[-1]["index"]),
    }


def _cue_cps(cue: dict[str, Any]) -> float:
    duration_ms = int(cue["end_ms"]) - int(cue["start_ms"])
    return len(" ".join(str(line) for line in cue["lines"])) / max(
        duration_ms / 1_000, 0.001
    )


def _subtitle_lines(text: str) -> list[str]:
    lines = _subtitle_lines_if_valid(text)
    if lines is None:
        raise ValidationFailureError("CAPTION_SIDECAR_LINE_LENGTH_INVALID")
    return lines


def _subtitle_lines_if_valid(text: str) -> list[str] | None:
    if len(text) <= 42:
        return [text]
    words = text.split()
    best: tuple[int, str, str] | None = None
    for split in range(1, len(words)):
        left, right = " ".join(words[:split]), " ".join(words[split:])
        if len(left) <= 46 and len(right) <= 46:
            score = abs(len(left) - len(right))
            if best is None or score < best[0]:
                best = (score, left, right)
    return [best[1], best[2]] if best is not None else None


def _render_srt(cues: Sequence[dict[str, Any]]) -> str:
    return (
        "\n\n".join(
            f"{cue['index']}\n{_srt_timestamp(cue['start_ms'])} --> {_srt_timestamp(cue['end_ms'])}\n"
            + "\n".join(cue["lines"])
            for cue in cues
        )
        + "\n"
    )


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(int(milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _subtitle_qc_content(
    *,
    caption_content: dict[str, Any],
    timed_words: Sequence[dict[str, Any]],
    transcript_hash: str,
    caption_artifact_hash: str,
    caption_ref: str,
) -> dict[str, Any]:
    cues = list(caption_content["cues"])
    reasons: list[str] = []
    previous_end = -1
    expected_word_index = 1
    cps_values: list[float] = []
    for expected_index, cue in enumerate(cues, start=1):
        start, end = int(cue["start_ms"]), int(cue["end_ms"])
        lines = list(cue["lines"])
        text = " ".join(lines)
        if cue.get("index") != expected_index or start < previous_end:
            reasons.append("CAPTION_SIDECAR_ORDER_INVALID")
        if end <= start:
            reasons.append("CAPTION_SIDECAR_DURATION_INVALID")
        if not lines or any(not str(line).strip() for line in lines):
            reasons.append("CAPTION_SIDECAR_EMPTY_CUE")
        if len(lines) > 2 or any(len(str(line)) > 46 for line in lines):
            reasons.append("CAPTION_SIDECAR_LINE_LENGTH_INVALID")
        duration_seconds = (end - start) / 1000
        if not 0.8 <= duration_seconds <= 7.0:
            reasons.append("CAPTION_SIDECAR_DURATION_INVALID")
        cps = len(text) / max(duration_seconds, 0.001)
        cps_values.append(cps)
        if cps > 20:
            reasons.append("CAPTION_SIDECAR_READING_SPEED_INVALID")
        if (
            int(cue["word_start_index"]) != expected_word_index
            or int(cue["word_end_index"]) < expected_word_index
        ):
            reasons.append("CAPTION_SIDECAR_ALIGNMENT_INVALID")
        expected_word_index = int(cue["word_end_index"]) + 1
        previous_end = end
    if expected_word_index != len(timed_words) + 1:
        reasons.append("CAPTION_SIDECAR_ALIGNMENT_INVALID")
    duration_ms = int(caption_content["audio_duration_ms"])
    if not cues or cues[0]["start_ms"] < 0 or cues[-1]["end_ms"] > duration_ms:
        reasons.append("CAPTION_SIDECAR_COVERAGE_INVALID")
    if cues and duration_ms - int(cues[-1]["end_ms"]) > 2_000:
        reasons.append("CAPTION_SIDECAR_COVERAGE_INVALID")
    if cps_values and (sum(cps_values) / len(cps_values)) > 17.5:
        reasons.append("CAPTION_SIDECAR_READING_SPEED_INVALID")
    return {
        "schema_version": V2_SIDECAR_SCHEMA,
        "kind": "SUBTITLE_QC",
        "status": "BLOCK" if reasons else "PASS",
        "reason_codes": sorted(set(reasons)),
        "language": caption_content["language"],
        "locale": caption_content["locale"],
        "caption_ref": caption_ref,
        "caption_artifact_hash": caption_artifact_hash,
        "caption_checksum": caption_content["checksum_sha256"],
        "transcript_hash": transcript_hash,
        "timed_words_hash": caption_content["timed_words_hash"],
        "cue_count": len(cues),
        "timed_word_count": len(timed_words),
        "encoding": "UTF-8",
        "punctuation_normalized": True,
        "provenance_valid": True,
    }


def _ensure_sidecar_artifact_version(
    *,
    session: Any,
    project: VideoProject,
    artifact_type: str,
    command_id: str,
    content: dict[str, Any],
    source_manifest: dict[str, Any],
) -> ArtifactVersion:
    expected_hash = content_hash(content)
    existing = session.execute(
        select(ArtifactVersion, Artifact)
        .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
        .where(
            Artifact.video_project_id == project.id,
            Artifact.artifact_type == artifact_type,
            ArtifactVersion.content_hash == expected_hash,
        )
    ).one_or_none()
    if existing is not None:
        version, artifact = existing
        if (
            artifact.current_version_id != version.id
            or artifact.status != "approved"
            or version.status != "approved"
            or version.content != content
        ):
            raise ValidationFailureError("CAPTION_SIDECAR_ARTIFACT_MISMATCH")
        return version
    service = ArtifactService(session)
    artifact = service.create_artifact(
        data=ArtifactCreate(
            video_project_id=project.id,
            artifact_type=artifact_type,
            status="approved",
            created_by_user_id=project.created_by_user_id,
        ),
        correlation_id=f"v2-sidecar-{command_id}",
        trusted_authority_write=True,
    )
    version = service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content=content,
            status="approved",
            created_by_user_id=project.created_by_user_id,
            external_entity_refs=[],
            packaging_metadata={
                "producer": V2_ELEVENLABS_NARRATION_ADAPTER_KEY,
                "command_id": command_id,
                "sidecar_only": True,
            },
            media_qc_metadata={},
            source_manifest=source_manifest,
            evidence_refs=[],
            context_refs=[],
            claim_refs=[],
        ),
        correlation_id=f"v2-sidecar-{command_id}",
        trusted_authority_write=True,
    )
    if version.content_hash != expected_hash:
        raise ValidationFailureError("CAPTION_SIDECAR_ARTIFACT_HASH_MISMATCH")
    artifact.status = "approved"
    session.flush()
    return version


def _write_srt_once(path: Path, payload: bytes) -> None:
    if path.exists():
        if not path.is_file() or path.is_symlink() or path.read_bytes() != payload:
            raise ValidationFailureError("CAPTION_SIDECAR_FILE_MISMATCH")
        return
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("wb") as stream:
            stream.write(payload)
            stream.flush()
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


__all__ = [
    "V2_CAPTION_SRT_ARTIFACT_TYPE",
    "V2_ELEVENLABS_NARRATION_ADAPTER_KEY",
    "V2_SUBTITLE_QC_ARTIFACT_TYPE",
    "V2_TIMED_WORDS_ARTIFACT_TYPE",
    "V2_TRANSCRIPT_ARTIFACT_TYPE",
    "V2ElevenLabsNarrationAdapter",
]
