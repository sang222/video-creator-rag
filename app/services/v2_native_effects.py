"""Crash-safe, package-native V2 media/render/QC/local-archive effects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contracts.m10_2 import FinalMediaRefCreate
from app.contracts.native_renderer import (
    CompiledNativeRenderManifest,
    MediaQCReport,
    V2ProductionRenderExecutionEnvelope,
)
from app.contracts.production_package import ProductionPackageContentV2
from app.contracts.production_workflow import (
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowEffectState,
    WorkflowStageResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.db import get_session_factory
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.config_registry import content_hash
from app.services.m10_2 import FinalMediaRefService
from app.services.native_ffmpeg_renderer import (
    FFmpegCommandBuilder,
    NativeFFmpegRenderer,
)
from app.services.native_render_plan import stable_hash
from app.services.production_package import ProductionPackageService
from app.services.production_workflow import WorkflowStageContext
from app.services.v2_provider_production import (
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
)
from app.services.workflow import ArtifactService


V2_LOCAL_ADAPTER_KEY = "v2-local-native"
V2_SILENT_AUDIO_STRATEGY = "SILENT_STEREO_TEXT_LED"
V2_LOCAL_NARRATION_STRATEGY = "LOCAL_OS_TTS_SCRIPT_BOUND"
V2_AUDIO_STRATEGY = V2_SILENT_AUDIO_STRATEGY
V2_AUDIO_STRATEGIES = frozenset({V2_SILENT_AUDIO_STRATEGY, V2_LOCAL_NARRATION_STRATEGY})
V2_TIMELINE_SCHEMA = "vcos.canonical-media-timeline.v2"
V2_TECHNICAL_QC_SCHEMA = "vcos.technical-media-qc.v2"
V2_CREATIVE_QC_SCHEMA = "vcos.creative-media-qc.v2"
V2_ARCHIVE_SCHEMA = "vcos.local-archive-receipt.v2"
_STAGE_MODES = {
    ProductionWorkflowStage.MEDIA: "PACKAGE_NATIVE_TIMELINE",
    ProductionWorkflowStage.RENDER: "NATIVE_FFMPEG_LOCAL",
    ProductionWorkflowStage.QC: "AUTOMATED_NATIVE_QC",
    ProductionWorkflowStage.ARCHIVE: "LOCAL_VERIFIED_ARCHIVE",
}
_CREATIVE_GATES = (
    "PackageScriptCoverageGate",
    "VisualPlanBindingGate",
    "TimelineCoverageGate",
    "NonBlackOutputGate",
    "FinalDurationConsistencyGate",
    "AudioStrategyTruthfulnessGate",
    "StreamIntegrityGate",
)


@dataclass(frozen=True, slots=True)
class V2LocalNarrationRuntime:
    """One deterministic local CLI narration backend."""

    backend: str
    binary: str
    voice: str
    rate_wpm: int = 150

    def __post_init__(self) -> None:
        if (
            self.backend not in {"MACOS_SAY", "ESPEAK_NG"}
            or not self.binary
            or not self.voice
            or not 80 <= self.rate_wpm <= 450
        ):
            raise ValueError("V2_LOCAL_NARRATION_RUNTIME_INVALID")

    @property
    def output_suffix(self) -> str:
        return ".aiff" if self.backend == "MACOS_SAY" else ".wav"

    @property
    def output_format(self) -> str:
        return "AIFF" if self.backend == "MACOS_SAY" else "WAV"

    def output_path(self, effect_dir: Path, *, partial: bool = False) -> Path:
        marker = ".part" if partial else ""
        return effect_dir / f"canonical-narration{marker}{self.output_suffix}"

    def build_command(self, *, output: Path, script_text: str) -> list[str]:
        if output.suffix.casefold() != self.output_suffix:
            raise ValueError("V2_LOCAL_NARRATION_OUTPUT_FORMAT_MISMATCH")
        if not script_text.strip():
            raise ValueError("V2_LOCAL_NARRATION_SCRIPT_REQUIRED")
        if self.backend == "MACOS_SAY":
            return [
                self.binary,
                "-v",
                self.voice,
                "-r",
                str(self.rate_wpm),
                "-o",
                str(output),
                script_text,
            ]
        return [
            self.binary,
            "-v",
            self.voice,
            "-s",
            str(self.rate_wpm),
            "-w",
            str(output),
            script_text,
        ]

    def journal_identity(self, *, command: list[str]) -> dict[str, Any]:
        return {
            "tts_backend": self.backend,
            "tts_binary": self.binary,
            "voice": self.voice,
            "rate_wpm": self.rate_wpm,
            "output_format": self.output_format,
            "command_argv_hash": content_hash(command),
        }


def _resolve_local_narration_runtime() -> V2LocalNarrationRuntime | None:
    configured_binary = str(os.getenv("VCOS_V2_LOCAL_TTS_PATH") or "").strip()
    configured_backend = str(os.getenv("VCOS_V2_LOCAL_TTS_BACKEND") or "").strip()
    backend_aliases = {
        "say": "MACOS_SAY",
        "macos_say": "MACOS_SAY",
        "macos-say": "MACOS_SAY",
        "espeak": "ESPEAK_NG",
        "espeak_ng": "ESPEAK_NG",
        "espeak-ng": "ESPEAK_NG",
    }
    explicit_backend = (
        backend_aliases.get(configured_backend.casefold())
        if configured_backend
        else None
    )
    if configured_backend and explicit_backend is None:
        raise ValueError("V2_LOCAL_NARRATION_BACKEND_INVALID")

    if configured_binary:
        binary = configured_binary
    elif explicit_backend == "MACOS_SAY":
        binary = shutil.which("say") or ""
    elif explicit_backend == "ESPEAK_NG":
        binary = shutil.which("espeak-ng") or shutil.which("espeak") or ""
    else:
        binary = (
            shutil.which("say")
            or shutil.which("espeak-ng")
            or shutil.which("espeak")
            or ""
        )
    if not binary:
        return None

    executable = Path(binary).name.casefold()
    detected_backend = (
        "MACOS_SAY"
        if executable == "say"
        else ("ESPEAK_NG" if executable in {"espeak-ng", "espeak"} else None)
    )
    if explicit_backend is not None and (
        detected_backend is not None and detected_backend != explicit_backend
    ):
        raise ValueError("V2_LOCAL_NARRATION_BACKEND_BINARY_MISMATCH")
    backend = explicit_backend or detected_backend
    if backend is None:
        raise ValueError("V2_LOCAL_NARRATION_BACKEND_REQUIRED")
    if backend == "MACOS_SAY":
        voice = (
            str(os.getenv("VCOS_V2_MACOS_SAY_VOICE") or "").strip()
            or str(os.getenv("VCOS_V2_LOCAL_TTS_VOICE") or "").strip()
            or "Samantha"
        )
        raw_rate = str(os.getenv("VCOS_V2_MACOS_SAY_RATE_WPM") or "150").strip()
    else:
        # Never reuse the historical macOS voice variable here: a compose
        # environment carrying "Samantha" must not poison the Linux backend.
        voice = str(os.getenv("VCOS_V2_ESPEAK_NG_VOICE") or "").strip() or "en-us"
        raw_rate = str(os.getenv("VCOS_V2_ESPEAK_NG_RATE_WPM") or "150").strip()
    try:
        rate_wpm = int(raw_rate)
    except ValueError as exc:
        raise ValueError("V2_LOCAL_NARRATION_RATE_INVALID") from exc
    return V2LocalNarrationRuntime(
        backend=backend,
        binary=binary,
        voice=voice,
        rate_wpm=rate_wpm,
    )


class V2LocalNativeProductionAdapter:
    """A real, no-paid-call adapter with two independent durable journals."""

    descriptor = V2ProductionAdapterDescriptor(
        adapter_key=V2_LOCAL_ADAPTER_KEY,
        supported_stages=frozenset(
            {
                ProductionWorkflowStage.MEDIA,
                ProductionWorkflowStage.RENDER,
                ProductionWorkflowStage.QC,
                ProductionWorkflowStage.ARCHIVE,
            }
        ),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def __init__(
        self,
        *,
        workspace_root: Path | None = None,
        session_factory: Callable[[], Session] | None = None,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        after_effect_before_ledger_commit: (
            Callable[[ProductionWorkflowStage, Path], None] | None
        ) = None,
    ) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        configured = os.getenv("VCOS_V2_PRODUCTION_ROOT")
        self.root = (
            workspace_root
            or (
                Path(configured)
                if configured
                else repository_root / "var" / "v2-production"
            )
        ).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._session_factory = session_factory or get_session_factory()
        macos_ffmpeg_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
        macos_ffprobe_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
        selected_ffmpeg = (
            ffmpeg
            or os.getenv("VCOS_V2_FFMPEG_PATH")
            or (str(macos_ffmpeg_full) if macos_ffmpeg_full.is_file() else None)
            or shutil.which("ffmpeg")
            or str(macos_ffmpeg_full)
        )
        selected_ffprobe = (
            ffprobe
            or os.getenv("VCOS_V2_FFPROBE_PATH")
            or (str(macos_ffprobe_full) if macos_ffprobe_full.is_file() else None)
            or shutil.which("ffprobe")
            or str(macos_ffprobe_full)
        )
        self._builder = FFmpegCommandBuilder(
            self.root,
            ffmpeg=selected_ffmpeg,
            ffprobe=selected_ffprobe,
        )
        self._renderer = NativeFFmpegRenderer(
            self.root,
            smoke_enabled=False,
            production_enabled=True,
        )
        self._after_effect = after_effect_before_ledger_commit
        self._narration_runtime = _resolve_local_narration_runtime()
        # Retain the legacy private alias for existing local qualification
        # probes while the runtime itself is backend-neutral.
        self._say_binary = (
            self._narration_runtime.binary
            if self._narration_runtime is not None
            else None
        )

    def execute(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> WorkflowStageResult:
        self._validate_operation(context, operation)
        with self._command_lock(context.command_id):
            return self._execute_locked(context=context, operation=operation)

    def _execute_locked(
        self,
        *,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> WorkflowStageResult:
        ledger, reconciled = self._prepare_effect(context, operation)
        if ledger.state == "VERIFIED":
            return _result_from_ledger(ledger, reconciled=True)
        try:
            result, journal = self._execute_stage(
                ledger_id=ledger.id,
                context=context,
                operation=operation,
            )
            if self._after_effect is not None:
                self._after_effect(operation.stage, self._effect_dir(ledger.command_id))
            return self._verify_effect(
                ledger.id,
                result=result,
                journal=journal,
                reconciled=reconciled,
            )
        except Exception as exc:
            self._mark_uncertain(ledger.id, exc)
            raise

    @contextmanager
    def _command_lock(self, command_id: str):
        """Hold a crash-released PostgreSQL session lock across the effect."""

        lock_key = _advisory_lock_key(command_id)
        session = self._session_factory()
        acquired = False
        try:
            acquired = bool(
                session.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": lock_key},
                ).scalar_one()
            )
            if not acquired:
                raise RuntimeError("V2_EFFECT_COMMAND_LOCK_BUSY")
            yield
        finally:
            if acquired:
                session.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
            session.close()

    def _validate_operation(
        self,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> None:
        if (
            context.run.production_lane != "LONG_FORM"
            or context.run.planning_source_type != "LONG_FORM_PLAN"
        ):
            raise ValidationFailureError("V2_LOCAL_NATIVE_LONG_FORM_ONLY")
        if (
            operation.adapter_key != V2_LOCAL_ADAPTER_KEY
            or operation.paid_provider_call
            or operation.max_cost_usd != 0
            or operation.parameters.get("mode") != _STAGE_MODES[operation.stage]
        ):
            raise ValidationFailureError("V2_LOCAL_NATIVE_OPERATION_NOT_AUTHORIZED")
        audio_strategy = operation.parameters.get("audio_strategy")
        if operation.stage in {
            ProductionWorkflowStage.MEDIA,
            ProductionWorkflowStage.RENDER,
        }:
            if audio_strategy not in V2_AUDIO_STRATEGIES:
                raise ValidationFailureError("V2_LOCAL_NATIVE_AUDIO_STRATEGY_REQUIRED")
            if (
                context.run.production_lane == "LONG_FORM"
                and audio_strategy != V2_LOCAL_NARRATION_STRATEGY
            ):
                raise ValidationFailureError(
                    "V2_LONG_FORM_NARRATION_CAPABILITY_REQUIRED"
                )
            if (
                audio_strategy == V2_LOCAL_NARRATION_STRATEGY
                and self._narration_runtime is None
            ):
                raise ValidationFailureError(
                    "V2_LOCAL_NARRATION_CAPABILITY_UNAVAILABLE"
                )
        elif audio_strategy is not None and audio_strategy not in V2_AUDIO_STRATEGIES:
            raise ValidationFailureError("V2_LOCAL_NATIVE_AUDIO_STRATEGY_INVALID")
        flattened = json.dumps(
            operation.parameters, sort_keys=True, ensure_ascii=False
        ).casefold()
        if audio_strategy == V2_SILENT_AUDIO_STRATEGY and any(
            forbidden in flattened
            for forbidden in (
                '"narration": true',
                '"tts": true',
                "text_to_speech",
                "synthetic_voice",
                "voiceover",
            )
        ):
            raise ValidationFailureError("V2_LOCAL_NATIVE_NARRATION_CLAIM_FORBIDDEN")

    def _prepare_effect(
        self,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[V2ProductionEffectLedger, bool]:
        run = context.run
        if (
            run.video_project_id is None
            or run.production_package_artifact_version_id is None
            or run.production_package_hash is None
        ):
            raise ValidationFailureError("V2_EFFECT_PACKAGE_BINDING_REQUIRED")
        # Commit the intent through the caller's trusted worker session.  The
        # coordinator has already locked and projected this run in that same
        # transaction; inserting the FK-bound ledger from a second session
        # would self-deadlock on PostgreSQL's transaction-id lock.  This early
        # commit is an intentional crash boundary: the event remains
        # unacknowledged, while replay can observe EFFECT_STARTED durably.
        session = context.session
        row = session.scalar(
            select(V2ProductionEffectLedger)
            .where(V2ProductionEffectLedger.command_id == context.command_id)
            .with_for_update()
        )
        reconciled = row is not None
        if row is None:
            row = V2ProductionEffectLedger(
                workflow_run_id=run.id,
                video_project_id=run.video_project_id,
                production_package_artifact_version_id=(
                    run.production_package_artifact_version_id
                ),
                production_package_hash=run.production_package_hash,
                command_id=context.command_id,
                stage=operation.stage.value,
                operation_id=operation.operation_id,
                adapter_key=operation.adapter_key,
                input_hash=context.input_hash,
                state="PREPARED",
                effect_invocation_count=0,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                row = session.scalar(
                    select(V2ProductionEffectLedger)
                    .where(
                        V2ProductionEffectLedger.workflow_run_id == run.id,
                        V2ProductionEffectLedger.stage == operation.stage.value,
                    )
                    .with_for_update()
                )
                reconciled = True
            if row is None:
                raise RuntimeError("V2_EFFECT_LEDGER_PREPARE_FAILED")
        self._assert_ledger_identity(
            row=row,
            context=context,
            operation=operation,
        )
        if row.state == "VERIFIED":
            session.expunge(row)
            session.commit()
            return row, True
        if row.state == "PREPARED":
            row.state = "EFFECT_STARTED"
            row.effect_invocation_count = 1
            row.started_at = utc_now()
            row.effect_journal = {
                "schema_version": "vcos.production-effect-journal.v1",
                "command_id": row.command_id,
                "stage": row.stage,
                "state": "EFFECT_STARTED",
            }
            session.commit()
        elif row.state not in {"EFFECT_STARTED", "FAILED_UNCERTAIN"}:
            raise RuntimeError("V2_EFFECT_LEDGER_STATE_INVALID")
        session.refresh(row)
        session.expunge(row)
        session.commit()
        return row, reconciled

    @staticmethod
    def _assert_ledger_identity(
        *,
        row: V2ProductionEffectLedger,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> None:
        run = context.run
        exact = (
            row.workflow_run_id == run.id
            and row.video_project_id == run.video_project_id
            and row.production_package_artifact_version_id
            == run.production_package_artifact_version_id
            and row.production_package_hash == run.production_package_hash
            and row.command_id == context.command_id
            and row.stage == operation.stage.value
            and row.operation_id == operation.operation_id
            and row.adapter_key == operation.adapter_key
            and row.input_hash == context.input_hash
            and row.effect_invocation_count in {0, 1}
        )
        if not exact:
            raise ValidationFailureError("V2_EFFECT_LEDGER_IDENTITY_MISMATCH")

    def _verify_effect(
        self,
        ledger_id: uuid.UUID,
        *,
        result: WorkflowStageResult,
        journal: dict[str, Any],
        reconciled: bool,
    ) -> WorkflowStageResult:
        if result.result_hash is None:
            raise ValidationFailureError("V2_EFFECT_RESULT_HASH_REQUIRED")
        with self._session_factory() as session:
            row = session.scalar(
                select(V2ProductionEffectLedger)
                .where(V2ProductionEffectLedger.id == ledger_id)
                .with_for_update()
            )
            if row is None:
                raise RuntimeError("V2_EFFECT_LEDGER_MISSING")
            if row.state == "VERIFIED":
                return _result_from_ledger(row, reconciled=True)
            if row.state not in {"EFFECT_STARTED", "FAILED_UNCERTAIN"}:
                raise RuntimeError("V2_EFFECT_LEDGER_STATE_INVALID")
            row.result_type = result.result_type
            row.result_id = result.result_id
            row.result_ref = result.result_ref
            row.result_hash = result.result_hash
            row.result_payload = result.result_payload
            row.authority_refs = result.authority_refs.model_dump(mode="json")
            row.effect_journal = journal
            row.state = "VERIFIED"
            row.completed_at = utc_now()
            session.commit()
            session.refresh(row)
            return _result_from_ledger(
                row,
                reconciled=reconciled,
            )

    def _mark_uncertain(self, ledger_id: uuid.UUID, exc: Exception) -> None:
        with self._session_factory() as session:
            row = session.scalar(
                select(V2ProductionEffectLedger)
                .where(V2ProductionEffectLedger.id == ledger_id)
                .with_for_update()
            )
            if row is None or row.state == "VERIFIED":
                return
            row.state = "FAILED_UNCERTAIN"
            row.effect_journal = {
                **(row.effect_journal or {}),
                "state": "FAILED_UNCERTAIN",
                "last_error_type": type(exc).__name__,
            }
            session.commit()

    def _execute_stage(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        handlers = {
            ProductionWorkflowStage.MEDIA: self._produce_media,
            ProductionWorkflowStage.RENDER: self._render,
            ProductionWorkflowStage.QC: self._quality_control,
            ProductionWorkflowStage.ARCHIVE: self._archive,
        }
        return handlers[operation.stage](
            ledger_id=ledger_id,
            context=context,
            operation=operation,
        )

    def _produce_media(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, package, script, visual = _production_inputs(
                session, context.run.id
            )
        audio_strategy = str(operation.parameters["audio_strategy"])
        effect_dir = self._effect_dir(context.command_id)
        audio = self._prepare_audio_authority(
            effect_dir=effect_dir,
            command_id=context.command_id,
            package=package,
            script=script,
            audio_strategy=audio_strategy,
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
        journal_path = effect_dir / "effect-journal.json"
        reconciled = _persist_exact_json(timeline_path, timeline)
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "MEDIA",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "file_write_count": 1,
            "reconciled_from_existing_bytes": reconciled,
            "timeline_relative_path": self._relative(timeline_path),
            "timeline_file_checksum": _sha256_file(timeline_path),
            "timeline_hash": timeline_hash,
            "audio_strategy": audio_strategy,
            "audio_asset_ref": audio["audio_asset_ref"],
            "audio_checksum": audio.get("audio_checksum"),
            "audio_relative_path": audio.get("audio_relative_path"),
            "audio_effect_invocation_count": audio["effect_invocation_count"],
            "narration_present": audio["narration_present"],
            "alignment_method": audio["alignment_method"],
        }
        _persist_exact_json(journal_path, journal, allow_reconciled_update=True)
        result = WorkflowStageResult(
            result_type="V2_CANONICAL_MEDIA_TIMELINE",
            result_ref=timeline_ref,
            result_hash=timeline_hash,
            result_payload={
                "schema_version": V2_TIMELINE_SCHEMA,
                "scene_count": len(timeline["scenes"]),
                "duration_ms": timeline["duration_ms"],
                "audio_strategy": audio_strategy,
                "audio_asset_ref": audio["audio_asset_ref"],
                "audio_checksum": audio.get("audio_checksum"),
                "narration_present": audio["narration_present"],
                "alignment_method": audio["alignment_method"],
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=project.id,
                canonical_media_timeline_ref=timeline_ref,
                canonical_media_timeline_hash=timeline_hash,
            ),
        )
        return result, journal

    def _prepare_audio_authority(
        self,
        *,
        effect_dir: Path,
        command_id: str,
        package: ProductionPackageContentV2,
        script: ArtifactVersion,
        audio_strategy: str,
    ) -> dict[str, Any]:
        if audio_strategy == V2_SILENT_AUDIO_STRATEGY:
            silence_hash = content_hash(
                {
                    "strategy": audio_strategy,
                    "script_content_hash": script.content_hash,
                    "duration_ms": package.duration_contract.target_duration_ms,
                }
            )
            return {
                "audio_strategy": audio_strategy,
                "audio_asset_ref": f"v2-native-audio://silence/{silence_hash}",
                "audio_checksum": None,
                "audio_relative_path": None,
                "duration_ms": package.duration_contract.target_duration_ms,
                "narration_present": False,
                "alignment_method": "TEXT_LED_SCENE_TIMING",
                "effect_invocation_count": 0,
            }
        runtime = self._narration_runtime
        if audio_strategy != V2_LOCAL_NARRATION_STRATEGY or runtime is None:
            raise ValidationFailureError("V2_LOCAL_NARRATION_CAPABILITY_UNAVAILABLE")
        script_text = str((script.content or {}).get("narration_text") or "").strip()
        if not script_text:
            raise ValidationFailureError("V2_LOCAL_NARRATION_APPROVED_SCRIPT_REQUIRED")
        output = runtime.output_path(effect_dir)
        part = runtime.output_path(effect_dir, partial=True)
        receipt_path = effect_dir / "narration-command-receipt.json"
        intent_path = effect_dir / "narration-command-journal.json"
        approved_script_hash = hashlib.sha256(script_text.encode("utf-8")).hexdigest()
        narration_command = runtime.build_command(
            output=part,
            script_text=script_text,
        )
        intent_identity = {
            "schema_version": "vcos.local-narration-command-journal.v1",
            "command_id": command_id,
            "audio_strategy": audio_strategy,
            "script_content_hash": script.content_hash,
            "approved_script_hash": approved_script_hash,
            "output_relative_path": self._relative(output),
            "effect_invocation_count": 1,
            **runtime.journal_identity(command=narration_command),
        }
        intent: dict[str, Any] | None = None
        if intent_path.exists():
            intent = _load_json(intent_path)
            if any(
                intent.get(key) != value for key, value in intent_identity.items()
            ) or intent.get("state") not in {"EFFECT_STARTED", "VERIFIED"}:
                raise ValidationFailureError(
                    "V2_LOCAL_NARRATION_COMMAND_JOURNAL_MISMATCH"
                )
        if receipt_path.exists():
            receipt = _load_json(receipt_path)
            if (
                intent is None
                or receipt.get("command_id") != command_id
                or receipt.get("script_content_hash") != script.content_hash
                or receipt.get("approved_script_hash") != approved_script_hash
                or receipt.get("audio_strategy") != audio_strategy
                or any(
                    receipt.get(key) != value
                    for key, value in runtime.journal_identity(
                        command=narration_command
                    ).items()
                )
                or not output.is_file()
                or output.is_symlink()
                or receipt.get("audio_checksum") != _sha256_file(output)
                or receipt.get("duration_ms")
                != _probe_duration_ms(self._builder.ffprobe, output)
            ):
                raise ValidationFailureError("V2_LOCAL_NARRATION_RECEIPT_MISMATCH")
            sealed_fields = {
                "audio_checksum": receipt["audio_checksum"],
                "measured_duration_ms": receipt["duration_ms"],
                "receipt_hash": content_hash(receipt),
            }
            if intent.get("state") == "VERIFIED" and any(
                intent.get(key) != value for key, value in sealed_fields.items()
            ):
                raise ValidationFailureError(
                    "V2_LOCAL_NARRATION_COMMAND_JOURNAL_MISMATCH"
                )
            if intent.get("state") != "VERIFIED":
                _write_json_atomic(
                    intent_path,
                    {
                        **intent_identity,
                        "state": "VERIFIED",
                        **sealed_fields,
                    },
                )
            return dict(receipt)
        if output.exists():
            if intent is None or not output.is_file() or output.is_symlink():
                raise ValidationFailureError("V2_LOCAL_NARRATION_UNJOURNALED_OUTPUT")
            if intent.get("state") == "VERIFIED":
                raise ValidationFailureError(
                    "V2_LOCAL_NARRATION_VERIFIED_RECEIPT_MISSING"
                )
            return self._seal_narration_output(
                output=output,
                receipt_path=receipt_path,
                intent_path=intent_path,
                intent_identity=intent_identity,
                package=package,
            )
        if intent is not None:
            raise RuntimeError("V2_LOCAL_NARRATION_EFFECT_UNCERTAIN_OUTPUT_MISSING")
        part.unlink(missing_ok=True)
        _write_json_atomic(
            intent_path,
            {
                **intent_identity,
                "state": "EFFECT_STARTED",
            },
        )
        try:
            completed = subprocess.run(
                narration_command,
                capture_output=True,
                text=True,
                shell=False,
            )
            if completed.returncode != 0 or not part.is_file():
                raise RuntimeError(f"V2_LOCAL_NARRATION_FAILED:{completed.returncode}")
            with part.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(part, output)
            _fsync_directory(output.parent)
        finally:
            part.unlink(missing_ok=True)
        return self._seal_narration_output(
            output=output,
            receipt_path=receipt_path,
            intent_path=intent_path,
            intent_identity=intent_identity,
            package=package,
        )

    def _seal_narration_output(
        self,
        *,
        output: Path,
        receipt_path: Path,
        intent_path: Path,
        intent_identity: dict[str, Any],
        package: ProductionPackageContentV2,
    ) -> dict[str, Any]:
        """Probe and seal already-created narration without invoking TTS."""

        if not output.is_file() or output.is_symlink():
            raise ValidationFailureError("V2_LOCAL_NARRATION_OUTPUT_INVALID")
        duration_ms = _probe_duration_ms(self._builder.ffprobe, output)
        duration = package.duration_contract
        if not (
            duration.minimum_duration_ms <= duration_ms <= duration.maximum_duration_ms
        ):
            raise ValidationFailureError("V2_LOCAL_NARRATION_DURATION_OUTSIDE_CONTRACT")
        checksum = _sha256_file(output)
        receipt = {
            "schema_version": "vcos.local-narration-receipt.v1",
            "command_id": intent_identity["command_id"],
            "audio_strategy": intent_identity["audio_strategy"],
            "audio_asset_ref": (f"v2-native-audio://local-os-tts/{checksum}"),
            "audio_checksum": checksum,
            "audio_relative_path": self._relative(output),
            "duration_ms": duration_ms,
            "script_content_hash": intent_identity["script_content_hash"],
            "approved_script_hash": intent_identity["approved_script_hash"],
            "tts_backend": intent_identity["tts_backend"],
            "tts_binary": intent_identity["tts_binary"],
            "voice": intent_identity["voice"],
            "rate_wpm": intent_identity["rate_wpm"],
            "output_format": intent_identity["output_format"],
            "command_argv_hash": intent_identity["command_argv_hash"],
            "narration_present": True,
            "alignment_method": (
                "MEASURED_AUDIO_ENDPOINT_PROPORTIONAL_SCRIPT_ALIGNMENT"
            ),
            "effect_invocation_count": 1,
        }
        _write_json_atomic(receipt_path, receipt)
        _write_json_atomic(
            intent_path,
            {
                **intent_identity,
                "state": "VERIFIED",
                "audio_checksum": checksum,
                "measured_duration_ms": duration_ms,
                "receipt_hash": content_hash(receipt),
            },
        )
        return receipt

    def _render(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, package, script, visual = _production_inputs(
                session, context.run.id
            )
            provider = session.get(
                ArtifactVersion,
                package.provider_execution_plan_ref.artifact_version_id,
            )
            budget = session.get(
                ArtifactVersion,
                package.budget_scope_ref.artifact_version_id,
            )
            media_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == run.id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            if provider is None or budget is None or media_ledger is None:
                raise ValidationFailureError("V2_NATIVE_RENDER_AUTHORITIES_REQUIRED")
            media_journal = dict(media_ledger.effect_journal)
            media_created_at = media_ledger.created_at
        timeline_path = self._from_relative(
            _required_text(media_journal, "timeline_relative_path")
        )
        timeline = _load_json(timeline_path)
        timeline_hash = content_hash(timeline)
        audio_strategy = str(operation.parameters["audio_strategy"])
        if (
            timeline_hash != run.canonical_media_timeline_hash
            or timeline.get("timeline_ref") != run.canonical_media_timeline_ref
            or timeline.get("audio_strategy") != audio_strategy
            or media_journal.get("audio_strategy") != audio_strategy
        ):
            raise ValidationFailureError("V2_NATIVE_RENDER_TIMELINE_MISMATCH")
        audio_path = (
            self._from_relative(_required_text(media_journal, "audio_relative_path"))
            if audio_strategy == V2_LOCAL_NARRATION_STRATEGY
            else None
        )
        if audio_strategy == V2_LOCAL_NARRATION_STRATEGY and (
            timeline.get("narration_present") is not True
            or media_journal.get("audio_checksum") != _sha256_file(audio_path)
        ):
            raise ValidationFailureError(
                "V2_NATIVE_RENDER_NARRATION_AUTHORITY_MISMATCH"
            )
        if (
            audio_strategy == V2_SILENT_AUDIO_STRATEGY
            and timeline.get("narration_present") is not False
        ):
            raise ValidationFailureError("V2_NATIVE_RENDER_SILENT_AUTHORITY_MISMATCH")
        effect_dir = self._effect_dir(context.command_id)
        plan_ref = f"v2-effect://{ledger_id}/native-render-plan"
        plan = _build_render_plan(
            project=project,
            package=package,
            script=script,
            visual=visual,
            timeline=timeline,
            timeline_hash=timeline_hash,
            plan_ref=plan_ref,
        )
        plan_hash = content_hash(plan)
        manifest = _build_manifest(
            ledger_id=ledger_id,
            package_id=run.production_package_artifact_version_id,
            plan_ref=plan_ref,
            plan_hash=plan_hash,
            timeline=timeline,
            timeline_hash=timeline_hash,
            created_at=media_created_at,
        )
        plan_path = effect_dir / "native-render-plan.json"
        manifest_path = effect_dir / "compiled-native-manifest.json"
        _persist_exact_json(plan_path, plan)
        _persist_exact_json(manifest_path, manifest.model_dump(mode="json"))
        render_run_key = f"v2-{context.command_id}"
        command = self._builder.build_v2_local_native(
            manifest,
            run_key=render_run_key,
            audio_path=audio_path,
        )
        envelope_body = {
            "envelope_version": "vcos.v2-native-render-envelope.v1",
            "workflow_run_id": str(run.id),
            "command_id": context.command_id,
            "render_run_key": render_run_key,
            "production_package_artifact_version_id": str(
                run.production_package_artifact_version_id
            ),
            "production_package_hash": run.production_package_hash,
            "provider_execution_plan_ref": package.provider_execution_plan_ref.ref,
            "provider_execution_plan_hash": provider.content_hash,
            "budget_scope_ref": package.budget_scope_ref.ref,
            "budget_scope_hash": budget.content_hash,
            "operation_id": operation.operation_id,
            "adapter_key": V2_LOCAL_ADAPTER_KEY,
            "plan_ref": plan_ref,
            "plan_hash": plan_hash,
            "production_eligible": True,
            "paid_provider_call": False,
        }
        envelope = V2ProductionRenderExecutionEnvelope(
            **envelope_body,
            authorization_hash=stable_hash(envelope_body),
        )
        receipt, native_qc = self._renderer.execute(
            manifest,
            command,
            purpose="VCOS_V2_NATIVE_PRODUCTION",
            execution_envelope=envelope,
        )
        if not isinstance(native_qc, MediaQCReport) or native_qc.result != "PASS":
            raise RuntimeError("V2_NATIVE_RENDER_QC_FAILED")
        measured_render_duration_ms = round(
            float(native_qc.checks.get("duration") or 0) * 1000
        )
        if measured_render_duration_ms <= 0:
            raise RuntimeError("V2_NATIVE_RENDER_DURATION_MISSING")
        output_path = Path(receipt.output_path).resolve()
        output_checksum = _sha256_file(output_path)
        if output_checksum != receipt.output_checksum:
            raise ValidationFailureError("V2_NATIVE_RENDER_OUTPUT_CHECKSUM_MISMATCH")
        output_ref = f"v2-native-render://{ledger_id}/{output_checksum}"
        journal_path = effect_dir / "effect-journal.json"
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "RENDER",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "ffmpeg_invocation_count": 1,
            "renderer_completion_replay_supported": True,
            "plan_relative_path": self._relative(plan_path),
            "manifest_relative_path": self._relative(manifest_path),
            "renderer_working_directory": self._relative(
                Path(command.working_directory)
            ),
            "output_relative_path": self._relative(output_path),
            "render_output_ref": output_ref,
            "output_checksum": output_checksum,
            "native_qc_result": native_qc.result,
            "native_qc_checksum": native_qc.checks.get("checksum_sha256"),
            "audio_strategy": audio_strategy,
            "audio_asset_ref": timeline["audio_asset_ref"],
            "audio_checksum": timeline.get("audio_checksum"),
            "narration_present": timeline["narration_present"],
            "alignment_method": timeline["alignment_method"],
            "duration_ms": timeline["duration_ms"],
            "measured_render_duration_ms": measured_render_duration_ms,
        }
        _persist_exact_json(journal_path, journal, allow_reconciled_update=True)
        result = WorkflowStageResult(
            result_type="V2_NATIVE_RENDER_OUTPUT",
            result_ref=output_ref,
            result_hash=output_checksum,
            result_payload={
                "production_eligible": True,
                "renderer": "native_ffmpeg",
                "provider_calls_made": False,
                "audio_strategy": audio_strategy,
                "audio_asset_ref": timeline["audio_asset_ref"],
                "audio_checksum": timeline.get("audio_checksum"),
                "narration_present": timeline["narration_present"],
                "alignment_method": timeline["alignment_method"],
                "duration_ms": timeline["duration_ms"],
                "measured_render_duration_ms": measured_render_duration_ms,
                "width": plan["canvas"]["width"],
                "height": plan["canvas"]["height"],
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=project.id,
                native_render_plan_ref=plan_ref,
                native_render_plan_hash=plan_hash,
                render_output_ref=output_ref,
                render_output_checksum=output_checksum,
            ),
        )
        return result, journal

    def _quality_control(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, package, script, visual = _production_inputs(
                session, context.run.id
            )
            render_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == run.id,
                    V2ProductionEffectLedger.stage == "RENDER",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            media_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == run.id,
                    V2ProductionEffectLedger.stage == "MEDIA",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            if render_ledger is None or media_ledger is None:
                raise ValidationFailureError("V2_NATIVE_QC_RENDER_REQUIRED")
            render_journal = dict(render_ledger.effect_journal)
            media_journal = dict(media_ledger.effect_journal)
        timeline = _load_json(
            self._from_relative(_required_text(media_journal, "timeline_relative_path"))
        )
        work = self._directory_from_relative(
            _required_text(render_journal, "renderer_working_directory")
        )
        native_qc = MediaQCReport.model_validate_json(
            (work / "media_qc.json").read_text(encoding="utf-8")
        )
        audio_strategy = str(timeline.get("audio_strategy") or "")
        narration_present = timeline.get("narration_present")
        if native_qc.result != "PASS" or (
            native_qc.checks.get("checksum_sha256") != run.render_output_checksum
        ):
            raise ValidationFailureError("V2_NATIVE_QC_EVIDENCE_MISMATCH")
        approved_fragments = [
            value[:520]
            for value in (
                [
                    item.strip()
                    for item in re.split(
                        r"(?<=[.!?])\s+|\n+",
                        str((script.content or {}).get("narration_text") or ""),
                    )
                    if item.strip()
                ]
                or _script_fragments(script.content)
            )
        ]
        scenes = list(timeline.get("scenes") or [])
        checks = {
            "PackageScriptCoverageGate": bool(scenes)
            and all(
                scene.get("script_artifact_version_id") == str(script.id)
                and scene.get("script_content_hash") == script.content_hash
                and scene.get("body") in approved_fragments
                for scene in scenes
            ),
            "VisualPlanBindingGate": bool(scenes)
            and all(
                scene.get("visual_plan_artifact_version_id") == str(visual.id)
                and scene.get("visual_plan_content_hash") == visual.content_hash
                for scene in scenes
            ),
            "TimelineCoverageGate": (native_qc.checks.get("timeline_coverage") is True),
            "NonBlackOutputGate": (native_qc.checks.get("black_output_absent") is True),
            "FinalDurationConsistencyGate": abs(
                round(float(native_qc.checks.get("duration") or 0) * 1000)
                - int(timeline["duration_ms"])
            )
            <= 250,
            "AudioStrategyTruthfulnessGate": (
                render_journal.get("audio_strategy") == audio_strategy
                and render_journal.get("narration_present") is narration_present
                and (
                    (
                        audio_strategy == V2_LOCAL_NARRATION_STRATEGY
                        and narration_present is True
                        and isinstance(timeline.get("audio_checksum"), str)
                        and timeline.get("audio_checksum")
                        == render_journal.get("audio_checksum")
                    )
                    or (
                        audio_strategy == V2_SILENT_AUDIO_STRATEGY
                        and narration_present is False
                        and timeline.get("audio_checksum") is None
                    )
                )
            ),
            "StreamIntegrityGate": (
                native_qc.checks.get("stream_integrity") is True
                and native_qc.checks.get("audio_format_matches_expected") is True
            ),
        }
        failed_checks = sorted(name for name, passed in checks.items() if not passed)
        if failed_checks:
            raise ValidationFailureError(
                "V2_NATIVE_QC_GATE_FAILED:" + ",".join(failed_checks)
            )
        technical_ref = f"v2-effect://{ledger_id}/technical-qc"
        creative_ref = f"v2-effect://{ledger_id}/creative-qc"
        technical = {
            "schema_version": V2_TECHNICAL_QC_SCHEMA,
            "workflow_run_id": str(run.id),
            "video_project_id": str(project.id),
            "render_output_checksum": run.render_output_checksum,
            "result": "PASS",
            "native_media_qc": native_qc.model_dump(mode="json"),
            "audio_strategy": audio_strategy,
            "audio_asset_ref": timeline["audio_asset_ref"],
            "audio_checksum": timeline.get("audio_checksum"),
            "narration_present": narration_present,
            "alignment_method": timeline["alignment_method"],
            "production_eligible": True,
            "human_final_review_required": True,
        }
        technical_hash = content_hash(technical)
        evidence_ref = f"{technical_ref}#{technical_hash}"
        creative = {
            "schema_version": V2_CREATIVE_QC_SCHEMA,
            "workflow_run_id": str(run.id),
            "video_project_id": str(project.id),
            "render_output_checksum": run.render_output_checksum,
            "result": "PASS",
            "gate_results": [
                {
                    "gate_name": gate,
                    "result": "PASS" if checks[gate] else "BLOCK",
                    "reason_codes": [],
                    "evidence_refs": [
                        evidence_ref,
                        timeline["timeline_ref"],
                        package.script_ref.ref,
                        package.visual_plan_ref.ref,
                    ],
                    "measured": checks[gate],
                }
                for gate in _CREATIVE_GATES
            ],
            "automated_scope": list(checks),
            "audio_strategy": audio_strategy,
            "audio_asset_ref": timeline["audio_asset_ref"],
            "audio_checksum": timeline.get("audio_checksum"),
            "narration_present": narration_present,
            "alignment_method": timeline["alignment_method"],
            "technical_pass_does_not_imply_human_watchability": True,
            "human_final_review_required": True,
            "production_eligible": True,
        }
        creative_hash = content_hash(creative)
        effect_dir = self._effect_dir(context.command_id)
        technical_path = effect_dir / "technical-qc.json"
        creative_path = effect_dir / "creative-qc.json"
        _persist_exact_json(technical_path, technical)
        _persist_exact_json(creative_path, creative)
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "QC",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "qc_invocation_count": 1,
            "technical_qc_relative_path": self._relative(technical_path),
            "technical_qc_hash": technical_hash,
            "creative_qc_relative_path": self._relative(creative_path),
            "creative_qc_hash": creative_hash,
            "audio_strategy": audio_strategy,
            "audio_asset_ref": timeline["audio_asset_ref"],
            "audio_checksum": timeline.get("audio_checksum"),
            "narration_present": narration_present,
            "alignment_method": timeline["alignment_method"],
        }
        _persist_exact_json(
            effect_dir / "effect-journal.json",
            journal,
            allow_reconciled_update=True,
        )
        result = WorkflowStageResult(
            result_type="V2_AUTOMATED_NATIVE_QC",
            result_ref=creative_ref,
            result_hash=creative_hash,
            result_payload={
                "technical_result": "PASS",
                "creative_result": "PASS",
                "human_final_review_required": True,
                "audio_strategy": audio_strategy,
                "audio_asset_ref": timeline["audio_asset_ref"],
                "audio_checksum": timeline.get("audio_checksum"),
                "narration_present": narration_present,
                "alignment_method": timeline["alignment_method"],
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=project.id,
                technical_qc_receipt_ref=technical_ref,
                technical_qc_receipt_hash=technical_hash,
                creative_qc_receipt_ref=creative_ref,
                creative_qc_receipt_hash=creative_hash,
            ),
        )
        return result, journal

    def _archive(
        self,
        *,
        ledger_id: uuid.UUID,
        context: WorkflowStageContext,
        operation: V2AuthorizedAdapterOperation,
    ) -> tuple[WorkflowStageResult, dict[str, Any]]:
        with self._session_factory() as session:
            run, project, package, _, _ = _production_inputs(session, context.run.id)
            render_ledger = session.scalar(
                select(V2ProductionEffectLedger).where(
                    V2ProductionEffectLedger.workflow_run_id == run.id,
                    V2ProductionEffectLedger.stage == "RENDER",
                    V2ProductionEffectLedger.state == "VERIFIED",
                )
            )
            if render_ledger is None:
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_RENDER_REQUIRED")
            render_journal = dict(render_ledger.effect_journal)
            destination = session.get(
                ArtifactVersion,
                package.destination_binding_ref.artifact_version_id,
            )
            if destination is None:
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_DESTINATION_REQUIRED")
        source = self._from_relative(
            _required_text(render_journal, "output_relative_path")
        )
        checksum = _sha256_file(source)
        if checksum != run.render_output_checksum:
            raise ValidationFailureError("V2_LOCAL_ARCHIVE_SOURCE_CHECKSUM_MISMATCH")
        archive_dir = self.root / "archive" / str(project.id)
        archive_dir.mkdir(parents=True, exist_ok=True)
        destination_path = archive_dir / f"{checksum}.mp4"
        effect_dir = self._effect_dir(context.command_id)
        journal_path = effect_dir / "archive-command-journal.json"
        audio_strategy = str(render_journal["audio_strategy"])
        narration_present = bool(render_journal["narration_present"])
        if operation.parameters.get("audio_strategy", audio_strategy) != audio_strategy:
            raise ValidationFailureError("V2_LOCAL_ARCHIVE_AUDIO_AUTHORITY_DRIFT")
        archive_journal = self._copy_archive_once(
            command_id=context.command_id,
            source=source,
            destination=destination_path,
            checksum=checksum,
            journal_path=journal_path,
            audio_strategy=audio_strategy,
        )
        thumbnail_path = archive_dir / f"{checksum}.jpg"
        thumbnail_journal = self._create_archive_thumbnail_once(
            command_id=context.command_id,
            source=source,
            source_checksum=checksum,
            destination=thumbnail_path,
            journal_path=effect_dir / "archive-thumbnail-command-journal.json",
            seek_ms=max(
                0,
                int(render_journal["measured_render_duration_ms"]) // 2,
            ),
        )
        thumbnail_checksum = _sha256_file(thumbnail_path)
        thumbnail_relative_ref = self._relative(thumbnail_path)
        archive_journal_hash = content_hash(
            {
                "copy_journal": archive_journal,
                "thumbnail_journal": thumbnail_journal,
            }
        )
        object_ref = f"vcos-local-archive://{project.id}/{checksum}/final.mp4"
        receipt = {
            "schema_version": V2_ARCHIVE_SCHEMA,
            "workflow_run_id": str(run.id),
            "command_id": context.command_id,
            "video_project_id": str(project.id),
            "production_package_artifact_version_id": str(
                run.production_package_artifact_version_id
            ),
            "production_package_hash": run.production_package_hash,
            "render_output_checksum": checksum,
            "archive_object_ref": object_ref,
            "archive_state": "VERIFIED",
            "readback_checksum": archive_journal["readback_checksum"],
            "size_bytes": destination_path.stat().st_size,
            "thumbnail_relative_ref": thumbnail_relative_ref,
            "thumbnail_checksum": thumbnail_checksum,
            "measured_render_duration_ms": int(
                render_journal["measured_render_duration_ms"]
            ),
            "audio_strategy": audio_strategy,
            "audio_asset_ref": render_journal["audio_asset_ref"],
            "audio_checksum": render_journal.get("audio_checksum"),
            "narration_present": narration_present,
            "alignment_method": render_journal["alignment_method"],
            "automatic_publish": False,
        }
        receipt_hash = content_hash(receipt)
        receipt_ref = f"v2-effect://{ledger_id}/archive-receipt"
        receipt_path = effect_dir / "archive-receipt.json"
        _persist_exact_json(receipt_path, receipt)
        final_media = self._persist_archive_authorities(
            run_id=run.id,
            archive_command_id=context.command_id,
            package=package,
            object_ref=object_ref,
            destination_path=destination_path,
            receipt_hash=receipt_hash,
            archive_journal_hash=archive_journal_hash,
            thumbnail_relative_ref=thumbnail_relative_ref,
            thumbnail_checksum=thumbnail_checksum,
            audio_strategy=audio_strategy,
            audio_asset_ref=str(render_journal["audio_asset_ref"]),
            audio_checksum=render_journal.get("audio_checksum"),
            narration_present=narration_present,
            alignment_method=str(render_journal["alignment_method"]),
            duration_ms=int(render_journal["measured_render_duration_ms"]),
        )
        normalized_destination = _normalized_destination(destination.content)
        journal = {
            "schema_version": "vcos.production-effect-journal.v1",
            "command_id": context.command_id,
            "stage": "ARCHIVE",
            "state": "VERIFIED",
            "effect_invocation_count": 1,
            "archive_copy_invocation_count": 1,
            "archive_command_journal_relative_path": self._relative(journal_path),
            "archive_receipt_relative_path": self._relative(receipt_path),
            "archive_receipt_hash": receipt_hash,
            "archive_object_ref": object_ref,
            "archive_readback_checksum": checksum,
            "thumbnail_relative_ref": thumbnail_relative_ref,
            "thumbnail_checksum": thumbnail_checksum,
            "thumbnail_invocation_count": 1,
            "final_media_ref_id": str(final_media.id),
            "audio_strategy": audio_strategy,
            "audio_asset_ref": render_journal["audio_asset_ref"],
            "audio_checksum": render_journal.get("audio_checksum"),
            "narration_present": narration_present,
            "alignment_method": render_journal["alignment_method"],
        }
        _persist_exact_json(
            effect_dir / "effect-journal.json",
            journal,
            allow_reconciled_update=True,
        )
        result = WorkflowStageResult(
            result_type="V2_VERIFIED_LOCAL_ARCHIVE",
            result_id=final_media.id,
            result_ref=object_ref,
            result_hash=receipt_hash,
            result_payload={
                "archive_state": "VERIFIED",
                "storage_provider": "VCOS_LOCAL_ARCHIVE",
                "readback_checksum": checksum,
                "automatic_publish": False,
                "thumbnail_relative_ref": thumbnail_relative_ref,
                "thumbnail_checksum": thumbnail_checksum,
                "audio_strategy": audio_strategy,
                "audio_asset_ref": render_journal["audio_asset_ref"],
                "audio_checksum": render_journal.get("audio_checksum"),
                "narration_present": narration_present,
                "alignment_method": render_journal["alignment_method"],
                "measured_render_duration_ms": int(
                    render_journal["measured_render_duration_ms"]
                ),
            },
            authority_refs=WorkflowAuthorityRefs(
                video_project_id=project.id,
                archive_receipt_ref=receipt_ref,
                archive_receipt_hash=receipt_hash,
                archive_object_ref=object_ref,
                archive_verification_state="VERIFIED",
                final_media_ref_id=final_media.id,
                final_media_ref_hash=checksum,
                destination_binding_id=destination.id,
                destination_binding_fingerprint=destination.content_hash,
                destination_binding=normalized_destination,
            ),
        )
        return result, journal

    def _copy_archive_once(
        self,
        *,
        command_id: str,
        source: Path,
        destination: Path,
        checksum: str,
        journal_path: Path,
        audio_strategy: str,
    ) -> dict[str, Any]:
        if journal_path.exists():
            journal = _load_json(journal_path)
            if (
                journal.get("command_id") != command_id
                or journal.get("source_checksum") != checksum
                or journal.get("destination_relative_path")
                != self._relative(destination)
                or journal.get("copy_invocation_count") != 1
                or journal.get("audio_strategy") != audio_strategy
            ):
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_JOURNAL_MISMATCH")
            if destination.exists() and _sha256_file(destination) == checksum:
                if journal.get("state") != "VERIFIED":
                    journal = {
                        **journal,
                        "state": "VERIFIED",
                        "readback_checksum": checksum,
                    }
                    _write_json_atomic(journal_path, journal)
                return journal
            if journal.get("state") == "VERIFIED":
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_VERIFIED_BYTES_MISSING")
        else:
            if destination.exists():
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_UNJOURNALED_DESTINATION")
            journal = {
                "schema_version": "vcos.archive-command-journal.v1",
                "command_id": command_id,
                "state": "EFFECT_STARTED",
                "copy_invocation_count": 1,
                "source_checksum": checksum,
                "source_size_bytes": source.stat().st_size,
                "destination_relative_path": self._relative(destination),
                "audio_strategy": audio_strategy,
            }
            _write_json_atomic(journal_path, journal)
        part = destination.with_suffix(destination.suffix + ".part")
        part.unlink(missing_ok=True)
        try:
            with (
                source.open("rb") as source_stream,
                part.open("xb") as destination_stream,
            ):
                shutil.copyfileobj(
                    source_stream,
                    destination_stream,
                    length=1024 * 1024,
                )
                destination_stream.flush()
                os.fsync(destination_stream.fileno())
            if _sha256_file(part) != checksum:
                raise RuntimeError("V2_LOCAL_ARCHIVE_PART_CHECKSUM_MISMATCH")
            os.replace(part, destination)
            _fsync_directory(destination.parent)
        finally:
            part.unlink(missing_ok=True)
        if _sha256_file(destination) != checksum:
            raise RuntimeError("V2_LOCAL_ARCHIVE_READBACK_CHECKSUM_MISMATCH")
        verified = {
            **journal,
            "state": "VERIFIED",
            "readback_checksum": checksum,
        }
        _write_json_atomic(journal_path, verified)
        return verified

    def _create_archive_thumbnail_once(
        self,
        *,
        command_id: str,
        source: Path,
        source_checksum: str,
        destination: Path,
        journal_path: Path,
        seek_ms: int,
    ) -> dict[str, Any]:
        """Extract one deterministic review JPEG under the ARCHIVE command."""

        identity = {
            "schema_version": "vcos.archive-thumbnail-command-journal.v1",
            "command_id": command_id,
            "source_checksum": source_checksum,
            "source_relative_path": self._relative(source),
            "destination_relative_path": self._relative(destination),
            "seek_ms": seek_ms,
            "effect_invocation_count": 1,
        }
        journal: dict[str, Any] | None = None
        if journal_path.exists():
            journal = _load_json(journal_path)
            if any(
                journal.get(key) != value for key, value in identity.items()
            ) or journal.get("state") not in {"EFFECT_STARTED", "VERIFIED"}:
                raise ValidationFailureError(
                    "V2_LOCAL_ARCHIVE_THUMBNAIL_JOURNAL_MISMATCH"
                )
            if destination.exists():
                if not _probe_jpeg(self._builder.ffprobe, destination):
                    raise ValidationFailureError("V2_LOCAL_ARCHIVE_THUMBNAIL_INVALID")
                thumbnail_checksum = _sha256_file(destination)
                if (
                    journal.get("state") == "VERIFIED"
                    and journal.get("thumbnail_checksum") != thumbnail_checksum
                ):
                    raise ValidationFailureError(
                        "V2_LOCAL_ARCHIVE_THUMBNAIL_CHECKSUM_MISMATCH"
                    )
                if journal.get("state") != "VERIFIED":
                    journal = {
                        **identity,
                        "state": "VERIFIED",
                        "thumbnail_checksum": thumbnail_checksum,
                    }
                    _write_json_atomic(journal_path, journal)
                return journal
            if journal.get("state") == "VERIFIED":
                raise ValidationFailureError(
                    "V2_LOCAL_ARCHIVE_VERIFIED_THUMBNAIL_MISSING"
                )
            raise RuntimeError(
                "V2_LOCAL_ARCHIVE_THUMBNAIL_EFFECT_UNCERTAIN_OUTPUT_MISSING"
            )
        if destination.exists():
            raise ValidationFailureError("V2_LOCAL_ARCHIVE_UNJOURNALED_THUMBNAIL")

        journal = {
            **identity,
            "state": "EFFECT_STARTED",
        }
        _write_json_atomic(journal_path, journal)
        part = destination.with_name(destination.stem + ".part.jpg")
        part.unlink(missing_ok=True)
        try:
            completed = subprocess.run(
                [
                    self._builder.ffmpeg,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-ss",
                    f"{seek_ms / 1000:.3f}",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-frames:v",
                    "1",
                    "-threads",
                    "1",
                    "-q:v",
                    "2",
                    "-map_metadata",
                    "-1",
                    "-f",
                    "image2",
                    "-update",
                    "1",
                    str(part),
                ],
                capture_output=True,
                text=True,
                shell=False,
            )
            if completed.returncode != 0 or not part.is_file():
                raise RuntimeError(
                    f"V2_LOCAL_ARCHIVE_THUMBNAIL_FAILED:{completed.returncode}"
                )
            if not _probe_jpeg(self._builder.ffprobe, part):
                raise RuntimeError("V2_LOCAL_ARCHIVE_THUMBNAIL_PART_INVALID")
            with part.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(part, destination)
            _fsync_directory(destination.parent)
        finally:
            part.unlink(missing_ok=True)
        if not _probe_jpeg(self._builder.ffprobe, destination):
            raise RuntimeError("V2_LOCAL_ARCHIVE_THUMBNAIL_READBACK_INVALID")
        verified = {
            **identity,
            "state": "VERIFIED",
            "thumbnail_checksum": _sha256_file(destination),
        }
        _write_json_atomic(journal_path, verified)
        return verified

    def _persist_archive_authorities(
        self,
        *,
        run_id: uuid.UUID,
        archive_command_id: str,
        package: ProductionPackageContentV2,
        object_ref: str,
        destination_path: Path,
        receipt_hash: str,
        archive_journal_hash: str,
        thumbnail_relative_ref: str,
        thumbnail_checksum: str,
        audio_strategy: str,
        audio_asset_ref: str,
        audio_checksum: str | None,
        narration_present: bool,
        alignment_method: str,
        duration_ms: int,
    ) -> FinalMediaRef:
        checksum = _sha256_file(destination_path)
        if not (
            package.duration_contract.minimum_duration_ms
            <= duration_ms
            <= package.duration_contract.maximum_duration_ms
        ):
            raise ValidationFailureError(
                "V2_LOCAL_ARCHIVE_RENDER_DURATION_OUTSIDE_CONTRACT"
            )
        with self._session_factory() as session:
            run = session.scalar(
                select(ProductionWorkflowRun)
                .where(ProductionWorkflowRun.id == run_id)
                .with_for_update()
            )
            project = (
                session.scalar(
                    select(VideoProject)
                    .where(VideoProject.id == run.video_project_id)
                    .with_for_update()
                )
                if run is not None and run.video_project_id is not None
                else None
            )
            if run is None or project is None:
                raise ValidationFailureError("V2_LOCAL_ARCHIVE_PROJECT_REQUIRED")
            cloud = session.scalar(
                select(CloudMediaRef).where(
                    CloudMediaRef.video_project_id == project.id,
                    CloudMediaRef.storage_provider == "VCOS_LOCAL_ARCHIVE",
                    CloudMediaRef.checksum_sha256 == checksum,
                )
            )
            appendix = {
                "archive_receipt_hash": receipt_hash,
                "archive_journal_hash": archive_journal_hash,
                "readback_checksum": checksum,
                "thumbnail_relative_ref": thumbnail_relative_ref,
                "thumbnail_checksum": thumbnail_checksum,
                "audio_strategy": audio_strategy,
                "audio_asset_ref": audio_asset_ref,
                "audio_checksum": audio_checksum,
                "narration_present": narration_present,
                "alignment_method": alignment_method,
                "measured_duration_ms": duration_ms,
                "automatic_publish": False,
            }
            if cloud is None:
                cloud = CloudMediaRef(
                    company_id=project.company_id,
                    channel_workspace_id=project.channel_workspace_id,
                    video_project_id=project.id,
                    media_type="LONG_FORM_FINAL",
                    storage_provider="VCOS_LOCAL_ARCHIVE",
                    drive_file_id=f"local-{checksum}",
                    drive_folder_id=str(project.id),
                    web_view_link=object_ref,
                    mime_type="video/mp4",
                    file_name=destination_path.name,
                    size_bytes=destination_path.stat().st_size,
                    checksum_sha256=checksum,
                    local_source_path_hash=hashlib.sha256(
                        str(destination_path).encode("utf-8")
                    ).hexdigest(),
                    upload_status="VERIFIED",
                    verification_status="CHECKSUM_VERIFIED",
                    local_cleanup_status="NOT_ELIGIBLE",
                    uploaded_at=utc_now(),
                    retention_policy={
                        "keep_local": True,
                        "authority": "V2_LOCAL_ARCHIVE",
                    },
                    source_refs=[
                        {
                            "type": "render_output",
                            "ref": run.render_output_ref,
                            "checksum": checksum,
                        }
                    ],
                    technical_appendix=appendix,
                )
                session.add(cloud)
                session.flush()
            elif (
                cloud.company_id != project.company_id
                or cloud.channel_workspace_id != project.channel_workspace_id
                or cloud.web_view_link != object_ref
                or cloud.upload_status != "VERIFIED"
                or cloud.verification_status != "CHECKSUM_VERIFIED"
                or cloud.technical_appendix != appendix
            ):
                raise ValidationFailureError(
                    "V2_LOCAL_ARCHIVE_CLOUD_AUTHORITY_MISMATCH"
                )
            lineage_content = {
                "schema_version": "vcos.native-final-media-lineage.v2",
                "video_project_id": str(project.id),
                "production_package_artifact_version_id": str(
                    run.production_package_artifact_version_id
                ),
                "production_package_hash": run.production_package_hash,
                "duration_contract": package.duration_contract.model_dump(mode="json"),
                "canonical_media_timeline_hash": (run.canonical_media_timeline_hash),
                "native_render_plan_hash": run.native_render_plan_hash,
                "render_output_checksum": checksum,
                "technical_qc_hash": run.technical_qc_receipt_hash,
                "creative_qc_hash": run.creative_qc_receipt_hash,
                "archive_receipt_hash": receipt_hash,
                "archive_state": "VERIFIED",
                "cloud_media_ref_id": str(cloud.id),
                "file_ref": object_ref,
                "audio_strategy": audio_strategy,
                "audio_asset_ref": audio_asset_ref,
                "audio_checksum": audio_checksum,
                "narration_present": narration_present,
                "alignment_method": alignment_method,
                "measured_duration_ms": duration_ms,
                "automatic_publish": False,
            }
            lineage_hash = content_hash(lineage_content)
            lineage_row = session.execute(
                select(ArtifactVersion, Artifact)
                .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
                .where(
                    Artifact.video_project_id == project.id,
                    Artifact.artifact_type == "mr1_final_media_lineage_receipt",
                    ArtifactVersion.content_hash == lineage_hash,
                )
            ).one_or_none()
            if lineage_row is None:
                artifact_service = ArtifactService(session)
                artifact = artifact_service.create_artifact(
                    data=ArtifactCreate(
                        video_project_id=project.id,
                        artifact_type="mr1_final_media_lineage_receipt",
                        status="approved",
                        created_by_user_id=project.created_by_user_id,
                    ),
                    correlation_id=f"v2-archive-{archive_command_id}",
                    trusted_authority_write=True,
                )
                lineage = artifact_service.create_artifact_version(
                    data=ArtifactVersionCreate(
                        artifact_id=artifact.id,
                        content=lineage_content,
                        status="approved",
                        created_by_user_id=project.created_by_user_id,
                        external_entity_refs=[],
                        packaging_metadata={
                            "producer": "v2-local-native",
                            "effect_command_id": archive_command_id,
                        },
                        media_qc_metadata={
                            "technical_qc_hash": run.technical_qc_receipt_hash,
                            "creative_qc_hash": run.creative_qc_receipt_hash,
                        },
                        source_manifest={
                            "items": [
                                {
                                    "type": "production_package",
                                    "artifact_version_id": str(
                                        run.production_package_artifact_version_id
                                    ),
                                    "content_hash": run.production_package_hash,
                                }
                            ]
                        },
                        evidence_refs=[],
                        context_refs=[],
                        claim_refs=[],
                    ),
                    correlation_id=f"v2-archive-{archive_command_id}",
                    trusted_authority_write=True,
                )
                if lineage.content_hash != lineage_hash:
                    raise ValidationFailureError(
                        "V2_LOCAL_ARCHIVE_LINEAGE_HASH_MISMATCH"
                    )
            else:
                lineage, artifact = lineage_row
                domain_authority = (lineage.packaging_metadata or {}).get(
                    "_vcos_domain_authority"
                )
                if (
                    artifact.current_version_id != lineage.id
                    or artifact.status != "approved"
                    or lineage.status != "approved"
                    or lineage.content != lineage_content
                    or not isinstance(domain_authority, dict)
                    or domain_authority.get("writer") != "server_domain_service"
                    or (lineage.packaging_metadata or {}).get("effect_command_id")
                    != archive_command_id
                ):
                    raise ValidationFailureError("V2_LOCAL_ARCHIVE_LINEAGE_MISMATCH")
            existing = session.scalar(
                select(FinalMediaRef).where(
                    FinalMediaRef.video_project_id == project.id,
                    FinalMediaRef.production_package_artifact_version_id
                    == run.production_package_artifact_version_id,
                    FinalMediaRef.production_package_hash
                    == run.production_package_hash,
                    FinalMediaRef.checksum_sha256 == checksum,
                )
            )
            if existing is not None:
                if (
                    existing.file_ref != object_ref
                    or existing.cloud_media_ref_id != cloud.id
                    or existing.lineage_artifact_version_id != lineage.id
                    or existing.duration_seconds != Decimal(duration_ms) / Decimal(1000)
                ):
                    raise ValidationFailureError(
                        "V2_LOCAL_ARCHIVE_FINAL_MEDIA_MISMATCH"
                    )
                session.commit()
                return existing
            final_media = FinalMediaRefService(session).create(
                data=FinalMediaRefCreate(
                    company_id=project.company_id,
                    channel_workspace_id=project.channel_workspace_id,
                    video_project_id=project.id,
                    production_package_artifact_version_id=(
                        run.production_package_artifact_version_id
                    ),
                    production_package_hash=run.production_package_hash,
                    duration_contract=package.duration_contract,
                    media_type="LONG_FORM_FINAL",
                    file_ref=object_ref,
                    duration_seconds=Decimal(duration_ms) / Decimal(1000),
                    aspect_ratio="16:9",
                    resolution="1920x1080",
                    provider_key=V2_LOCAL_ADAPTER_KEY,
                    provider_type="LOCAL_RENDERER_CAPABILITY",
                    checksum_sha256=checksum,
                    cloud_media_ref_id=cloud.id,
                    lineage_artifact_version_id=lineage.id,
                )
            )
            session.commit()
            return final_media

    def _effect_dir(self, command_id: str) -> Path:
        key = hashlib.sha256(command_id.encode("utf-8")).hexdigest()
        path = (self.root / "effects" / key).resolve()
        if self.root not in path.parents:
            raise ValueError("V2_EFFECT_PATH_OUTSIDE_ROOT")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("V2_EFFECT_PATH_OUTSIDE_ROOT")
        return str(resolved.relative_to(self.root))

    def _from_relative(self, value: str) -> Path:
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError("V2_EFFECT_RELATIVE_PATH_INVALID")
        resolved = (self.root / raw).resolve()
        if self.root not in resolved.parents:
            raise ValueError("V2_EFFECT_PATH_OUTSIDE_ROOT")
        if not resolved.is_file() or resolved.is_symlink():
            raise ValueError("V2_EFFECT_FILE_AUTHORITY_MISSING")
        return resolved

    def _directory_from_relative(self, value: str) -> Path:
        raw = Path(value)
        if raw.is_absolute() or ".." in raw.parts:
            raise ValueError("V2_EFFECT_RELATIVE_PATH_INVALID")
        resolved = (self.root / raw).resolve()
        if self.root not in resolved.parents:
            raise ValueError("V2_EFFECT_PATH_OUTSIDE_ROOT")
        if not resolved.is_dir() or resolved.is_symlink():
            raise ValueError("V2_EFFECT_DIRECTORY_AUTHORITY_MISSING")
        return resolved


def _production_inputs(
    session: Session,
    run_id: uuid.UUID,
) -> tuple[
    ProductionWorkflowRun,
    VideoProject,
    ProductionPackageContentV2,
    ArtifactVersion,
    ArtifactVersion,
]:
    run = session.get(ProductionWorkflowRun, run_id)
    project = (
        session.get(VideoProject, run.video_project_id)
        if run is not None and run.video_project_id is not None
        else None
    )
    if (
        run is None
        or project is None
        or run.production_package_artifact_version_id is None
        or run.production_package_hash is None
        or project.schema_version != "v2"
        or run.production_lane != "LONG_FORM"
        or run.planning_source_type != "LONG_FORM_PLAN"
        or project.production_lane != "LONG_FORM"
        or project.planning_source_type != "LONG_FORM_PLAN"
    ):
        raise ValidationFailureError("V2_NATIVE_PACKAGE_INPUT_REQUIRED")
    package = ProductionPackageService(session).validate_for_readiness(
        run.production_package_artifact_version_id
    )
    package_version = session.get(
        ArtifactVersion, run.production_package_artifact_version_id
    )
    script = session.get(ArtifactVersion, package.script_ref.artifact_version_id)
    visual = session.get(ArtifactVersion, package.visual_plan_ref.artifact_version_id)
    if (
        package.video_project_id != project.id
        or package.production_lane.value != run.production_lane
        or package_version is None
        or run.production_package_hash != package_version.content_hash
        or script is None
        or visual is None
        or script.content_hash != package.script_ref.content_hash
        or visual.content_hash != package.visual_plan_ref.content_hash
    ):
        raise ValidationFailureError("V2_NATIVE_PACKAGE_INPUT_MISMATCH")
    return run, project, package, script, visual


def _build_timeline(
    *,
    run: ProductionWorkflowRun,
    project: VideoProject,
    package: ProductionPackageContentV2,
    script: ArtifactVersion,
    visual: ArtifactVersion,
    timeline_ref: str,
    audio: dict[str, Any],
) -> dict[str, Any]:
    narration_text = str((script.content or {}).get("narration_text") or "").strip()
    fragments = [
        value.strip()
        for value in re.split(r"(?<=[.!?])\s+|\n+", narration_text)
        if value.strip()
    ] or _script_fragments(script.content)
    if not fragments:
        raise ValidationFailureError("V2_NATIVE_SCRIPT_TEXT_REQUIRED")
    while len(fragments) < 2:
        words = fragments[0].split()
        split_at = max(1, len(words) // 2)
        fragments = [
            " ".join(words[:split_at]),
            " ".join(words[split_at:]) or fragments[0],
        ]
    fragments = fragments[:6]
    duration_ms = int(audio["duration_ms"])
    if not (
        package.duration_contract.minimum_duration_ms
        <= duration_ms
        <= package.duration_contract.maximum_duration_ms
    ):
        raise ValidationFailureError("V2_NATIVE_TIMELINE_DURATION_INVALID")
    weights = [max(1, len(fragment.split())) for fragment in fragments]
    total_weight = sum(weights)
    scenes: list[dict[str, Any]] = []
    cursor = 0
    cumulative_weight = 0
    for index, fragment in enumerate(fragments):
        cumulative_weight += weights[index]
        end = (
            duration_ms
            if index == len(fragments) - 1
            else max(
                cursor + 1,
                round(duration_ms * cumulative_weight / total_weight),
            )
        )
        scene_duration = end - cursor
        scenes.append(
            {
                "scene_id": f"scene-{index + 1:03d}",
                "start_ms": cursor,
                "end_ms": end,
                "duration_ms": scene_duration,
                "headline": (
                    f"{project.title.strip()[:72]} · {index + 1}/{len(fragments)}"
                ),
                "body": fragment[:520],
                "visual_treatment": "NATIVE_TEXT_CARD",
                "script_artifact_version_id": str(script.id),
                "script_content_hash": script.content_hash,
                "visual_plan_artifact_version_id": str(visual.id),
                "visual_plan_content_hash": visual.content_hash,
                "audio_start_ms": cursor,
                "audio_end_ms": end,
                "alignment_method": audio["alignment_method"],
            }
        )
        cursor = end
    return {
        "schema_version": V2_TIMELINE_SCHEMA,
        "timeline_ref": timeline_ref,
        "workflow_run_id": str(run.id),
        "video_project_id": str(project.id),
        "production_package_artifact_version_id": str(
            run.production_package_artifact_version_id
        ),
        "production_package_hash": run.production_package_hash,
        "production_lane": run.production_lane,
        "duration_ms": duration_ms,
        "duration_contract": package.duration_contract.model_dump(mode="json"),
        "script_ref": package.script_ref.model_dump(mode="json"),
        "visual_plan_ref": package.visual_plan_ref.model_dump(mode="json"),
        "audio_strategy": audio["audio_strategy"],
        "audio_asset_ref": audio["audio_asset_ref"],
        "audio_checksum": audio.get("audio_checksum"),
        "narration_present": audio["narration_present"],
        "alignment_method": audio["alignment_method"],
        "audio_description": (
            "Approved script rendered by the local operating-system speech "
            "engine; measured audio endpoint and proportional script alignment."
            if audio["narration_present"]
            else (
                "Authorized silent stereo bed; approved script is represented "
                "as native on-screen text. No narration or TTS is claimed."
            )
        ),
        "scenes": scenes,
    }


def _build_render_plan(
    *,
    project: VideoProject,
    package: ProductionPackageContentV2,
    script: ArtifactVersion,
    visual: ArtifactVersion,
    timeline: dict[str, Any],
    timeline_hash: str,
    plan_ref: str,
) -> dict[str, Any]:
    return {
        "schema_version": "vcos.native-render-plan.v2",
        "plan_ref": plan_ref,
        "video_project_id": str(project.id),
        "production_package_artifact_version_id": (
            timeline["production_package_artifact_version_id"]
        ),
        "production_package_hash": timeline["production_package_hash"],
        "canonical_media_timeline_ref": timeline["timeline_ref"],
        "canonical_media_timeline_hash": timeline_hash,
        "script_artifact_version_id": str(script.id),
        "script_content_hash": script.content_hash,
        "visual_plan_artifact_version_id": str(visual.id),
        "visual_plan_content_hash": visual.content_hash,
        "canvas": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "audio_strategy": timeline["audio_strategy"],
        "audio_asset_ref": timeline["audio_asset_ref"],
        "audio_checksum": timeline.get("audio_checksum"),
        "narration_present": timeline["narration_present"],
        "alignment_method": timeline["alignment_method"],
        "renderer": "native_ffmpeg",
        "production_eligible": True,
        "paid_provider_calls": False,
        "scenes": timeline["scenes"],
    }


def _build_manifest(
    *,
    ledger_id: uuid.UUID,
    package_id: uuid.UUID,
    plan_ref: str,
    plan_hash: str,
    timeline: dict[str, Any],
    timeline_hash: str,
    created_at: Any,
) -> CompiledNativeRenderManifest:
    body = {
        "source_plan_ref": plan_ref,
        "source_plan_hash": plan_hash,
        "compiler_version": "v2-local-native-compiler/1.0.0",
        "motion_pack_version": "v2-package-native-cards/1.0.0",
        "renderer_profile_refs": ["v2-native-h264-aac"],
        "ffmpeg_capability_digest": content_hash(
            {
                "required": [
                    "libx264",
                    "aac",
                    "drawtext",
                    "blackdetect",
                ]
            }
        ),
        "normalized_canvas": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
        },
        "normalized_audio": {
            "strategy": timeline["audio_strategy"],
            "sample_rate": 48000,
            "channels": 2,
            "audio_asset_ref": timeline["audio_asset_ref"],
            "audio_checksum": timeline.get("audio_checksum"),
            "narration_present": timeline["narration_present"],
            "alignment_method": timeline["alignment_method"],
        },
        "normalized_caption": {
            "mode": "TEXT_LED_NATIVE_SCENES",
            "separate_caption_track": False,
        },
        "compiled_scenes": timeline["scenes"],
        "transition_schedule": [],
        "overlay_schedule": [],
        "audio_mix_schedule": {
            "strategy": timeline["audio_strategy"],
            "audio_asset_ref": timeline["audio_asset_ref"],
            "narration_present": timeline["narration_present"],
            "alignment_method": timeline["alignment_method"],
        },
        "caption_schedule": {"cues": []},
        "output_specs": [
            {
                "width": 1920,
                "height": 1080,
                "fps": 30,
                "pix_fmt": "yuv420p",
                "color": "bt709",
                "audio_codec": "aac",
                "sample_rate": 48000,
                "channels": 2,
                "faststart": True,
            }
        ],
        "expected_input_refs": [
            f"artifact-version://{package_id}",
            timeline["script_ref"]["ref"],
            timeline["visual_plan_ref"]["ref"],
            timeline["timeline_ref"],
        ],
        "unresolved_inputs": [],
        "compilation_warnings": [],
        "compilation_reason_codes": [
            "V2_PACKAGE_NATIVE_TEXT_LED_RENDER",
            (
                "V2_LOCAL_NARRATION_SCRIPT_BOUND"
                if timeline["narration_present"]
                else "V2_SILENT_STEREO_EXPLICITLY_AUTHORIZED"
            ),
        ],
        "production_eligible": True,
        "temporal_authority_mode": "CANONICAL_STRICT",
        "canonical_media_timeline_ref": timeline["timeline_ref"],
        "canonical_media_timeline_hash": timeline_hash,
        "canonical_audio_asset_ref": timeline["audio_asset_ref"],
        "canonical_duration_ms": timeline["duration_ms"],
        "canonical_caption_compilation_ref": None,
        "canonical_caption_compilation_hash": None,
        "canonical_caption_render_payload_hash": None,
        "visual_direction_contract_ref": timeline["visual_plan_ref"]["ref"],
        "visual_direction_contract_hash": timeline["visual_plan_ref"]["content_hash"],
        "creative_gate_results": {
            "package_script_bound": "PASS",
            "visual_plan_bound": "PASS",
            "audio_strategy_truthfulness": "PASS",
        },
        "render_purpose": "VCOS_V2_NATIVE_PRODUCTION",
    }
    return CompiledNativeRenderManifest(
        compiled_manifest_id=f"v2-native-manifest:{ledger_id}",
        ffmpeg_binary_requirement="ffmpeg-with-libx264-aac-drawtext",
        manifest_hash=stable_hash(body),
        created_at=created_at,
        **body,
    )


def _script_fragments(content: Any) -> list[str]:
    priority_keys = {
        "narration_text",
        "text",
        "sentence",
        "body",
        "headline",
        "title",
        "description",
    }
    ignored_fragments = (
        "hash",
        "ref",
        "_id",
        "status",
        "result",
        "schema",
        "reason",
    )
    values: list[str] = []

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, str):
            if key in priority_keys:
                normalized = " ".join(value.split())
                if len(normalized) >= 8:
                    values.append(normalized)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, key)
            return
        if isinstance(value, dict):
            for child_key in priority_keys:
                if child_key in value:
                    visit(value[child_key], child_key)
            for child_key, child in value.items():
                lowered = str(child_key).casefold()
                if child_key in priority_keys or any(
                    marker in lowered for marker in ignored_fragments
                ):
                    continue
                visit(child, lowered)

    visit(content)
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            deduplicated.append(value)
    return deduplicated


def _result_from_ledger(
    row: V2ProductionEffectLedger,
    *,
    reconciled: bool,
) -> WorkflowStageResult:
    if row.state != "VERIFIED" or row.result_type is None:
        raise RuntimeError("V2_EFFECT_LEDGER_RESULT_NOT_VERIFIED")
    return WorkflowStageResult(
        result_type=row.result_type,
        result_id=row.result_id,
        result_ref=row.result_ref,
        result_hash=row.result_hash,
        result_payload=dict(row.result_payload or {}),
        authority_refs=WorkflowAuthorityRefs.model_validate(row.authority_refs or {}),
        reason_codes=["V2_DURABLE_EFFECT_RECONCILED"] if reconciled else [],
        effect_state=(
            WorkflowEffectState.RECONCILED
            if reconciled
            else WorkflowEffectState.COMPLETED
        ),
    )


def _normalized_destination(content: Any) -> dict[str, str]:
    if not isinstance(content, dict):
        raise ValidationFailureError("V2_LOCAL_DESTINATION_INVALID")
    nested = content.get("destination_binding", content.get("destination"))
    payload = nested if isinstance(nested, dict) else content
    status = str(payload.get("destination_status", payload.get("status", ""))).upper()
    result = {
        "platform": str(payload.get("platform") or "").strip().upper(),
        "platform_channel_id": str(payload.get("platform_channel_id") or "").strip(),
        "account_identity": str(
            payload.get("platform_account_ref", payload.get("account_identity")) or ""
        ).strip(),
    }
    if status != "VERIFIED" or not all(result.values()):
        raise ValidationFailureError("V2_LOCAL_DESTINATION_NOT_VERIFIED")
    return result


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise ValidationFailureError(f"V2_EFFECT_JOURNAL_FIELD_REQUIRED:{key}")
    return result


def _persist_exact_json(
    path: Path,
    payload: dict[str, Any],
    *,
    allow_reconciled_update: bool = False,
) -> bool:
    if path.exists():
        existing = _load_json(path)
        if existing == payload:
            return True
        if (
            allow_reconciled_update
            and existing.get("command_id") == payload.get("command_id")
            and existing.get("stage") == payload.get("stage")
            and existing.get("effect_invocation_count")
            == payload.get("effect_invocation_count")
        ):
            _write_json_atomic(path, payload)
            return True
        raise ValidationFailureError("V2_EFFECT_FILE_IDENTITY_CONFLICT")
    _write_json_atomic(path, payload)
    return False


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValidationFailureError("V2_EFFECT_FILE_INVALID")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationFailureError("V2_EFFECT_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ValidationFailureError("V2_EFFECT_JSON_OBJECT_REQUIRED")
    return value


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        encoded = (
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        with part.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(part, path)
        _fsync_directory(path.parent)
    finally:
        part.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_duration_ms(ffprobe: str, path: Path) -> int:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    try:
        duration_ms = round(float(completed.stdout.strip()) * 1000)
    except ValueError as exc:
        raise RuntimeError("V2_AUDIO_DURATION_PROBE_INVALID") from exc
    if completed.returncode != 0 or duration_ms <= 0:
        raise RuntimeError("V2_AUDIO_DURATION_PROBE_FAILED")
    return duration_ms


def _probe_jpeg(ffprobe: str, path: Path) -> bool:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 4:
        return False
    with path.open("rb") as stream:
        header = stream.read(2)
        stream.seek(-2, os.SEEK_END)
        trailer = stream.read(2)
    if header != b"\xff\xd8" or trailer != b"\xff\xd9":
        return False
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return False
    return bool(
        completed.returncode == 0
        and stream.get("codec_name") == "mjpeg"
        and int(stream.get("width") or 0) > 0
        and int(stream.get("height") or 0) > 0
    )


def _advisory_lock_key(command_id: str) -> int:
    """Map a command identity into PostgreSQL's signed BIGINT lock space."""

    return int.from_bytes(
        hashlib.sha256(command_id.encode("utf-8")).digest()[:8],
        byteorder="big",
        signed=False,
    ) & ((1 << 63) - 1)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "V2_AUDIO_STRATEGY",
    "V2_LOCAL_ADAPTER_KEY",
    "V2LocalNativeProductionAdapter",
]
