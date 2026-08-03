from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from app.contracts.creative_quality_canary import CreativeGateEvidence
from app.contracts.long_production import (
    MediaNormalizationItem,
    MediaNormalizationManifest,
    ProductionRenderExecutionEnvelope,
    ResolvedMediaAsset,
    ReviewMediaCandidate,
    VisualSourceBinding,
)
from app.contracts.native_renderer import (
    AssetRequirement,
    CanvasSpec,
    CompiledNativeRenderManifest,
    FFmpegCommandManifest,
    MediaQCReport,
    NativeRenderExecutionReceipt,
    NativeOverlayPlan,
    NativeRenderPlan,
    NativeRenderScene,
    ResolvedAssetRef,
    TextSafeRegion,
)
from app.contracts.temporal_authority import (
    AlignedWord,
    CanonicalMediaTimeline,
    EditorialSegmentInput,
    FinalNarrationAudio,
    ForcedAlignmentEvidence,
    NarrationTimingSeed,
    SourceToSpokenSpan,
    SpokenTextNormalized,
    SpokenToken,
    TextSpan,
)
from app.contracts.visual_routing import (
    ExactTextNativeOverlayContract,
    SourceFallbackClass,
    VisualSourceRoute,
)
from app.services.caption_voice_quality import ReadableCaptionCompiler
from app.services.creative_media_qc import CreativePerceptualMediaQC, TechnicalMediaQC
from app.services.native_ffmpeg_renderer import (
    FFMPEG_FULL_DEFAULT,
    FFPROBE_FULL_DEFAULT,
    NativeFFmpegRenderer,
)
from app.services.native_media_qc import NativeMediaQC
from app.services.native_motion_compiler import NativeMotionCompiler
from app.services.native_render_plan import canonical_plan_hash, stable_hash
from app.services.mr1_route_authority import (
    ALL_MR1_SCENES,
    MR1VisualRouteAuthority,
    resolve_mr1_visual_route_authority,
)
from app.services.temporal_authority import (
    CanonicalMediaTimelineCompiler,
    NarrationAlignmentReconciler,
    TemporalAuthorityGate,
)


MR1_LOCAL_CONTINUATION_VERSION = "mr1.local-production-continuation/1.0.0"
MR1_RENDER_PURPOSE = "REAL_APPROVED_PRODUCTION"
ALL_SCENES = ALL_MR1_SCENES
OUTPUT_PROFILE = "YT_LONG_1080P30_SDR_H264_VT"
HUMAN_DETERMINISTIC_REPAIR_CLASSES = frozenset(
    {
        "crop",
        "overlay",
        "motion",
        "transition",
        "readability",
        "render_parameters",
        "archive_package",
    }
)


# This is the exact visual-mechanism projection of the frozen PKG1 visual plan.
# It contains no new editorial claims: every label/value is present in the
# approved script or visual intent.  Keeping it here lets the local renderer
# fail closed if the approved package is ever replaced by a different scene
# plan without a corresponding renderer revision.
MR1_SCENE_VISUAL_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "SC-01": {
        "semantic_intent": "Open with a five-column workload grid and reveal the arithmetic as a labeled scenario.",
        "mechanism": "FIVE_COLUMN_WORKLOAD_GRID_ARITHMETIC",
        "headline": "ILLUSTRATIVE SCENARIO",
        "subhead": "Transparent assumptions — not a benchmark, result, or guarantee",
        "cards": [
            "PERSON 1\n1 HOUR",
            "PERSON 2\n1 HOUR",
            "PERSON 3\n1 HOUR",
            "PERSON 4\n1 HOUR",
            "PERSON 5\n1 HOUR",
        ],
        "formula": "5 PEOPLE  ×  1 HOUR  ×  4 DAYS  =  20 HOURS",
        "initial_cards": ["PERSON 1", "PERSON 2", "PERSON 3", "PERSON 4", "PERSON 5"],
        "initial_formula": "",
        "state_semantics": ["WORKLOAD_GRID_ONLY", "ARITHMETIC_REVEALED"],
        "footer": "VARIABLE MODEL  •  REPLACE WITH YOUR TEAM'S OBSERVATIONS",
        "exact_number_required": True,
        "visual_treatment": "DATA_CARD",
        "animation_type": "COUNT_UP",
        "transition_in": "REVEAL_UP",
        "transition_out": "FADE_SOFT",
    },
    "SC-02": {
        "semantic_intent": "Use native counters that change the three inputs and recompute the illustrative total.",
        "mechanism": "THREE_INPUT_COUNTER_RECOMPUTE",
        "headline": "ASSUMPTION COUNTERS",
        "subhead": "Change any input and the illustrative total recomputes",
        "cards": ["PEOPLE\n5", "HOURS / PERSON / DAY\n1", "WORKING DAYS\n4"],
        "formula": "ILLUSTRATIVE TOTAL  =  20 HOURS",
        "initial_cards": ["PEOPLE\n2", "MINUTES / PERSON / DAY\n20", "WORKING DAYS\n4"],
        "initial_formula": "ILLUSTRATIVE TOTAL  =  160 MINUTES  =  2 H 40 M",
        "state_semantics": ["2 × 20 MIN × 4 = 160 MIN", "5 × 1 H × 4 = 20 H"],
        "footer": "TRY 20 MINUTES  •  TRY 2 PEOPLE  •  AUDIT WITH OBSERVED INPUTS",
        "exact_number_required": True,
        "visual_treatment": "DATA_CARD",
        "animation_type": "COUNT_UP",
        "transition_in": "DISSOLVE",
        "transition_out": "FADE_SOFT",
    },
    "SC-03": {
        "semantic_intent": "Map trigger, inputs, checks, output, and exception as a native workflow diagram.",
        "mechanism": "BOUNDED_WORKFLOW_MAP",
        "headline": "ONE BOUNDED WORKFLOW",
        "subhead": "Automate one stable path; keep exceptions visible",
        "cards": ["TRIGGER", "INPUTS", "CHECKS", "OUTPUT"],
        "formula": "EXCEPTION  →  HUMAN",
        "initial_cards": ["TRIGGER", "INPUTS", "", ""],
        "initial_formula": "",
        "state_semantics": ["TRIGGER_AND_INPUTS", "FULL_FLOW_WITH_HUMAN_EXCEPTION"],
        "footer": "DEFINE THE PROCESS BEFORE AUTOMATING IT",
        "exact_number_required": False,
        "visual_treatment": "DIAGRAM",
        "animation_type": "REVEAL_UP",
        "transition_in": "SLIDE_LEFT",
        "transition_out": "FADE_SOFT",
    },
    "SC-04": {
        "semantic_intent": "Use brief supporting team-work context, then return to a native baseline checklist.",
        "mechanism": "BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST",
        "headline": "BASELINE CHECKLIST",
        "subhead": "Supporting footage is context only — team records are the evidence",
        "cards": [
            "REQUEST BEGINS",
            "FIELDS COPIED",
            "MISSING INFORMATION",
            "NAMED RESOLVER",
            "COMPLETED HANDOFFS",
            "REWORK",
            "JUDGMENT STEPS",
        ],
        "formula": "MOVE INFORMATION  ≠  MAKE A DECISION",
        "footer": "OBSERVE FIRST  •  KEEP RESPONSIBILITY VISIBLE",
        "exact_number_required": False,
        "visual_treatment": "STOCK_VIDEO",
        "animation_type": "SLIDE_IN_LEFT",
        "transition_in": "DISSOLVE",
        "transition_out": "FADE_SOFT",
    },
    "SC-05": {
        "semantic_intent": "Build a native five-card control panel and highlight the exception path.",
        "mechanism": "FIVE_CARD_CONTROL_PANEL",
        "headline": "CONTROLLED PATH",
        "subhead": "Five labels keep a shortcut from becoming an invisible system",
        "cards": ["TRIGGER", "INPUTS", "OWNER", "SUCCESS CONDITION", "EXCEPTION PATH"],
        "formula": "EXCEPTION PATH  →  ASK FOR HELP",
        "initial_cards": [
            "TRIGGER",
            "INPUTS",
            "OWNER",
            "SUCCESS CONDITION",
            "EXCEPTION PATH",
        ],
        "initial_formula": "CONTROLLED NORMAL PATH",
        "state_semantics": ["EXCEPTION_PATH_NEUTRAL", "EXCEPTION_PATH_HIGHLIGHTED"],
        "footer": "ACTIVITY RECORD  •  REVERSIBLE  •  MANUAL FALLBACK",
        "exact_number_required": False,
        "visual_treatment": "UI_SIMULATION",
        "animation_type": "HIGHLIGHT",
        "transition_in": "REVEAL_UP",
        "transition_out": "FADE_SOFT",
    },
    "SC-06": {
        "semantic_intent": "Show native baseline-versus-pilot cards with a prominent HYPOTHESIS label.",
        "mechanism": "BASELINE_VERSUS_PILOT_HYPOTHESIS",
        "headline": "HYPOTHESIS",
        "subhead": "The twenty-hour scenario is not a result",
        "cards": [
            "BASELINE\nCOMPLETED HANDOFFS\nREWORK\nMANUAL WORK",
            "PILOT\nCLEAN HANDOFFS\nCORRECTIONS\nMANUAL WORK REMOVED",
        ],
        "formula": "DO NOT COUNT MOVED TIME  •  DO NOT HIDE LATER REWORK",
        "initial_cards": ["BASELINE\nCOMPLETED HANDOFFS\nREWORK\nMANUAL WORK", ""],
        "initial_formula": "RECORD THE BASELINE BEFORE THE PILOT",
        "state_semantics": ["BASELINE_RECORDED", "HYPOTHESIS_BASELINE_VERSUS_PILOT"],
        "footer": "LET THE TEAM'S OWN RECORDS REPLACE THE SCENARIO",
        "exact_number_required": False,
        "visual_treatment": "COMPARISON_SLIDE",
        "animation_type": "HIGHLIGHT",
        "transition_in": "DISSOLVE",
        "transition_out": "FADE_SOFT",
    },
    "SC-07": {
        "semantic_intent": "Use supporting review context followed by a native exception queue and reason codes.",
        "mechanism": "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE",
        "headline": "EXCEPTION QUEUE",
        "subhead": "Route every exception to a named owner and preserve the original input",
        "cards": [
            "MISSING DATA",
            "DUPLICATE REQUEST",
            "UNUSUAL APPROVAL",
            "SYSTEM OUTAGE",
        ],
        "formula": "REASON CODE  →  NAMED OWNER  →  MANUAL FALLBACK",
        "footer": "NO SILENT RETRIES  •  PAUSE WHEN THE THRESHOLD IS CROSSED",
        "exact_number_required": False,
        "visual_treatment": "STOCK_VIDEO",
        "animation_type": "SLIDE_IN_RIGHT",
        "transition_in": "DISSOLVE",
        "transition_out": "FADE_SOFT",
    },
    "SC-08": {
        "semantic_intent": "Animate a native calculation sheet that replaces scenario inputs with observed values.",
        "mechanism": "OBSERVED_VALUE_CALCULATION_SHEET",
        "headline": "OBSERVED-VALUE CALCULATION",
        "subhead": "Replace scenario assumptions with the team's own records",
        "cards": [
            "OBSERVED PEOPLE",
            "OBSERVED MANUAL TIME",
            "REAL FREQUENCY",
            "− SETUP",
            "− REVIEW",
            "− EXCEPTION HANDLING",
        ],
        "formula": "DECISION SIGNAL  =  POSITIVE  /  NEUTRAL  /  NEGATIVE",
        "initial_cards": [
            "SCENARIO PEOPLE",
            "SCENARIO MANUAL TIME",
            "SCENARIO FREQUENCY",
            "− SETUP",
            "− REVIEW",
            "− EXCEPTION HANDLING",
        ],
        "initial_formula": "SCENARIO INPUTS  —  NOT A RESULT",
        "state_semantics": ["SCENARIO_INPUTS", "OBSERVED_INPUTS_AND_DECISION_SIGNAL"],
        "footer": "CONTINUE ONLY WHEN THE WORKFLOW IS CLEARER AND COST FITS",
        "exact_number_required": False,
        "visual_treatment": "DATA_CARD",
        "animation_type": "COUNT_UP",
        "transition_in": "REVEAL_UP",
        "transition_out": "FADE_SOFT",
    },
    "SC-09": {
        "semantic_intent": "Close with grounded planning context and a native five-item audit checklist.",
        "mechanism": "BRIEF_CONTEXT_THEN_FIVE_ITEM_AUDIT",
        "headline": "ONE-WORKFLOW AUDIT",
        "subhead": "Map one repeated handoff before building anything",
        "cards": ["TRIGGER", "INPUTS", "OWNER", "SUCCESS CONDITION", "EXCEPTION PATH"],
        "formula": "MEASURE BASELINE  →  RUN BOUNDED PILOT",
        "footer": "KEEP FALLBACK VISIBLE  •  LET OBSERVED RESULTS DECIDE",
        "exact_number_required": False,
        "visual_treatment": "STOCK_VIDEO",
        "animation_type": "REVEAL_UP",
        "transition_in": "DISSOLVE",
        "transition_out": "FADE_BLACK",
    },
}

# PKG1_SC04_REVISION changes only SC-04's exact approved visual authority.  Keep
# the prior blueprint available so already-approved historical runs remain
# reproducible, while selecting this blueprint only for the revised native route.
MR1_SC04_NATIVE_MOTION_BLUEPRINT: dict[str, Any] = {
    "semantic_intent": (
        "Animate a labeled baseline checklist, then split information-moving "
        "steps from judgment-heavy decisions while keeping human responsibility "
        "visible."
    ),
    "mechanism": "BASELINE_CHECKLIST_THEN_INFORMATION_VS_JUDGMENT_SPLIT",
    "headline": "BASELINE CHECKLIST",
    "subhead": "Observe the workflow, measure the baseline, then split the work",
    "cards": [
        "REQUEST BEGINS",
        "FIELDS COPIED",
        "MISSING INFORMATION",
        "GAP OWNER",
        "COMPLETED HANDOFFS",
        "REWORK",
        "JUDGMENT STEPS",
    ],
    "formula": "MOVE INFORMATION  ≠  MAKE A DECISION",
    "initial_cards": [
        "REQUEST BEGINS",
        "FIELDS COPIED",
        "MISSING INFORMATION",
        "GAP OWNER",
        "",
        "",
        "",
    ],
    "initial_formula": "OBSERVE WORKFLOW  →  MEASURE BASELINE",
    "state_semantics": [
        "OBSERVE_WORKFLOW_AND_MEASURE_BASELINE",
        "INFORMATION_VS_JUDGMENT_WITH_HUMAN_EXCEPTION_PATH",
    ],
    "footer": "HUMAN EXCEPTION PATH  •  RESPONSIBILITY STAYS VISIBLE",
    "exact_number_required": False,
    "visual_treatment": "MOTION_GRAPHIC",
    "animation_type": "SLIDE_IN_LEFT",
    "transition_in": "DISSOLVE",
    "transition_out": "FADE_SOFT",
}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (Path, uuid.UUID, datetime)):
        return str(value)
    return value


def _write_text_atomic(path: Path, value: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("x", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        if executable:
            part.chmod(0o700)
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _write_json_atomic(path: Path, value: Any) -> None:
    _write_text_atomic(
        path,
        json.dumps(_jsonable(value), indent=2, sort_keys=True, default=str) + "\n",
    )


def _digest_file(path: Path) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def _sha256_file(path: Path) -> str:
    return _digest_file(path)[0]


def _inside(root: Path, value: str | Path, *, must_exist: bool = False) -> Path:
    root = root.resolve()
    raw = Path(value)
    if ".." in raw.parts:
        raise ValueError("MR1_PATH_TRAVERSAL_REJECTED")
    candidate = raw if raw.is_absolute() else root / raw
    resolved = candidate.resolve(strict=must_exist)
    if resolved != root and root not in resolved.parents:
        raise ValueError("MR1_PATH_OUTSIDE_RUN_WORKSPACE")
    if must_exist:
        if candidate.is_symlink() or not resolved.is_file():
            raise ValueError("MR1_SYMLINK_OR_NON_FILE_INPUT_REJECTED")
        if resolved.stat().st_size <= 0:
            raise ValueError("MR1_EMPTY_MEDIA_INPUT_REJECTED")
    return resolved


def _run(command: list[str], reason: str) -> None:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode:
        stderr = completed.stderr[-4000:].replace("\n", " ").strip()
        raise RuntimeError(f"{reason}:{completed.returncode}:{stderr}")


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        shell=False,
    )
    if completed.returncode:
        raise RuntimeError(f"MR1_FFPROBE_FAILED:{path.name}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"MR1_FFPROBE_JSON_INVALID:{path.name}") from exc
    if not isinstance(result, dict) or not result.get("streams"):
        raise RuntimeError(f"MR1_FFPROBE_STREAMS_MISSING:{path.name}")
    return result


def _video_stream(probe: Mapping[str, Any]) -> dict[str, Any]:
    stream = next(
        (
            item
            for item in list(probe.get("streams") or [])
            if item.get("codec_type") == "video"
        ),
        None,
    )
    if not isinstance(stream, dict):
        raise RuntimeError("MR1_VIDEO_STREAM_MISSING")
    return stream


def _audio_stream(probe: Mapping[str, Any]) -> dict[str, Any]:
    stream = next(
        (
            item
            for item in list(probe.get("streams") or [])
            if item.get("codec_type") == "audio"
        ),
        None,
    )
    if not isinstance(stream, dict):
        raise RuntimeError("MR1_AUDIO_STREAM_MISSING")
    return stream


def _duration_ms(probe: Mapping[str, Any]) -> int:
    raw = (probe.get("format") or {}).get("duration")
    try:
        value = round(float(raw) * 1000)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MR1_MEDIA_DURATION_MISSING") from exc
    if value <= 0:
        raise RuntimeError("MR1_MEDIA_DURATION_INVALID")
    return value


def _frame_fingerprint(path: Path, *, seconds: float, ffmpeg: str) -> dict[str, Any]:
    completed = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-ss",
            f"{max(0.0, seconds):.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=160:90,format=gray",
            "-f",
            "rawvideo",
            "-",
        ],
        capture_output=True,
        shell=False,
    )
    frame = completed.stdout
    if completed.returncode or len(frame) != 160 * 90:
        raise RuntimeError(f"MR1_FRAME_STATE_PROBE_FAILED:{path.name}")
    return {
        "probe_seconds": round(seconds, 6),
        "decoded_frame_sha256": hashlib.sha256(frame).hexdigest(),
        "minimum_luma": min(frame),
        "maximum_luma": max(frame),
        "mean_luma": round(sum(frame) / len(frame), 3),
        "decoded_actual_bytes": True,
    }


def _hash_model(model: Any, field: str) -> str:
    return stable_hash(model.model_dump(mode="json", exclude={field}))


def _artifact_ref(authority: Mapping[str, Any], key: str) -> tuple[str, str]:
    resolved = (authority.get("resolved") or {}).get(key) or {}
    version_id = str(resolved.get("artifact_version_id") or "")
    digest = str(resolved.get("content_hash") or "")
    if not version_id or len(digest) != 64:
        raise ValueError(f"MR1_AUTHORITY_ARTIFACT_BINDING_MISSING:{key}")
    return f"artifact-version://{version_id}", digest


def _package_binding(authority: Mapping[str, Any], key: str) -> tuple[str, str] | None:
    package = authority.get("package") or {}
    refs = {
        **dict(package.get("reused_artifacts") or {}),
        **dict(package.get("revised_artifacts") or {}),
    }
    raw = refs.get(key)
    if not isinstance(raw, Mapping):
        return None
    version_id = str(raw.get("artifact_version_id") or "")
    digest = str(raw.get("content_hash") or "")
    if not version_id or len(digest) != 64:
        return None
    return f"artifact-version://{version_id}", digest


def _binding_ref(raw: Any, fallback: str) -> str:
    if isinstance(raw, Mapping):
        for key in ("ref", "artifact_ref", "version_ref", "id"):
            if raw.get(key):
                return str(raw[key])
    if raw:
        return str(raw)
    return fallback


def _binding_hash(raw: Any, fallback: str) -> str:
    if isinstance(raw, Mapping):
        for key in ("content_hash", "hash"):
            if raw.get(key):
                return str(raw[key])
    return fallback


class MR1LocalProductionContinuation:
    """Continue an approved MR1 run using only already-acquired local bytes.

    The class has deliberately no HTTP, provider, Drive, or YouTube gateway.  A
    successful call consumes narration, forced-alignment, and Pexels outputs
    already made durable by :class:`MR1RealProductionService`; every remaining
    operation is deterministic local compilation, FFmpeg execution, or probing.
    """

    def __init__(
        self,
        settings: Any | None = None,
        repository_root: Path | str | None = None,
        workspace_root: Path | str | None = None,
        *,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
    ) -> None:
        self.settings = settings
        self.repository_root = (
            Path(repository_root).resolve() if repository_root is not None else None
        )
        self.workspace_root = (
            Path(workspace_root).resolve() if workspace_root is not None else None
        )
        self.ffmpeg = str(
            ffmpeg
            or (
                FFMPEG_FULL_DEFAULT
                if Path(FFMPEG_FULL_DEFAULT).is_file()
                else shutil.which("ffmpeg") or ""
            )
        )
        self.ffprobe = str(
            ffprobe
            or (
                FFPROBE_FULL_DEFAULT
                if Path(FFPROBE_FULL_DEFAULT).is_file()
                else shutil.which("ffprobe") or ""
            )
        )
        if not self.ffmpeg or not Path(self.ffmpeg).is_file():
            raise RuntimeError("MR1_FFMPEG_RUNTIME_UNAVAILABLE")
        if not self.ffprobe or not Path(self.ffprobe).is_file():
            raise RuntimeError("MR1_FFPROBE_RUNTIME_UNAVAILABLE")

    @staticmethod
    def _visual_route_authority(
        authority: Mapping[str, Any],
    ) -> MR1VisualRouteAuthority:
        return resolve_mr1_visual_route_authority(authority)

    @staticmethod
    def _human_repair_context(root: Path, run_id: uuid.UUID) -> dict[str, Any]:
        directive_path = root / "human_repair_directive.json"
        if not directive_path.exists():
            return {
                "active": False,
                "review_round": 1,
                "directive_hash": None,
                "repair_classes": [],
                "rejected_output_sha256": None,
                "repair_profile": {
                    "mechanism_transition_fraction": 0.42,
                    "overlay_fade_ms": 350,
                    "crop_mode": "COVER",
                },
            }
        if directive_path.is_symlink() or not directive_path.is_file():
            raise ValueError("MR1_HUMAN_REPAIR_DIRECTIVE_PATH_INVALID")
        raw = json.loads(directive_path.read_text(encoding="utf-8"))
        payload = {key: value for key, value in raw.items() if key != "content_hash"}
        directive_hash = stable_hash(payload)
        if raw.get("content_hash") not in {None, directive_hash}:
            raise ValueError("MR1_HUMAN_REPAIR_DIRECTIVE_HASH_MISMATCH")
        if (
            raw.get("schema_version") != "mr1.human-repair-directive.v1"
            or raw.get("decision") != "REJECT"
            or str(raw.get("run_id") or "") != str(run_id)
        ):
            raise ValueError("MR1_HUMAN_REPAIR_DIRECTIVE_IDENTITY_INVALID")
        try:
            review_round = int(raw.get("review_round"))
        except (TypeError, ValueError) as exc:
            raise ValueError("MR1_HUMAN_REPAIR_REVIEW_ROUND_INVALID") from exc
        classes = [
            str(item).strip().lower() for item in raw.get("repair_classes") or []
        ]
        if (
            review_round < 2
            or not classes
            or len(classes) != len(set(classes))
            or not set(classes).issubset(HUMAN_DETERMINISTIC_REPAIR_CLASSES)
        ):
            raise ValueError("MR1_HUMAN_REPAIR_SCOPE_INVALID")
        rejected_hash = str(raw.get("rejected_output_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", rejected_hash):
            raise ValueError("MR1_HUMAN_REPAIR_REJECTED_HASH_INVALID")
        profile = {
            "mechanism_transition_fraction": (
                0.52
                if {"motion", "transition"}.intersection(classes)
                else 0.47
                if "render_parameters" in classes
                else 0.42
            ),
            "overlay_fade_ms": 600 if "overlay" in classes else 350,
            "crop_mode": "SAFE_CONTAIN" if "crop" in classes else "COVER",
        }
        return {
            "active": True,
            "review_round": review_round,
            "directive_hash": directive_hash,
            "directive_path": str(directive_path),
            "repair_classes": classes,
            "rejected_output_sha256": rejected_hash,
            "operator_reason": str(raw.get("operator_reason") or ""),
            "repair_profile": profile,
        }

    @staticmethod
    def _prepare_human_repair_workspace(
        *,
        root: Path,
        run_id: uuid.UUID,
        prior: Mapping[str, Any],
        repair: Mapping[str, Any],
    ) -> dict[str, Any]:
        marker_path = root / "human_repair_state.json"
        if marker_path.is_file() and not marker_path.is_symlink():
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                marker.get("run_id") != str(run_id)
                or marker.get("directive_hash") != repair["directive_hash"]
                or marker.get("review_round") != repair["review_round"]
            ):
                raise FileExistsError("MR1_HUMAN_REPAIR_STATE_CONFLICT")
            return marker
        candidate = prior.get("review_media_candidate") or {}
        rejected_hash = str(candidate.get("output_sha256") or "")
        if rejected_hash != repair["rejected_output_sha256"]:
            raise ValueError("MR1_HUMAN_REPAIR_REJECTED_OUTPUT_BINDING_MISMATCH")
        prior_output_ref = Path(str(candidate.get("output_file_ref") or ""))
        if prior_output_ref.parent.resolve() != (root / "render").resolve():
            raise ValueError("MR1_HUMAN_REPAIR_PRIOR_OUTPUT_PATH_INVALID")
        history = (
            root
            / "repair_history"
            / (f"review-round-{int(repair['review_round']) - 1:02d}")
        )
        if history.exists():
            raise FileExistsError("MR1_HUMAN_REPAIR_HISTORY_CONFLICT")
        history.mkdir(parents=True, exist_ok=False)
        targets = (
            Path("local_production_result.json"),
            Path("local_failure.json"),
            Path("render"),
            Path("qc"),
            Path("review"),
            Path("archive_package"),
            Path("reports"),
            Path("assets/normalized"),
            Path("assets/media-normalization-manifest.json"),
            Path("assets/asset-provenance-manifest.json"),
            Path("assets/scene-visual-execution-manifest.json"),
        )
        preserved: list[str] = []
        for relative in targets:
            source = root / relative
            if not source.exists():
                continue
            destination = history / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            preserved.append(relative.as_posix())
        prior_output = history / "render" / prior_output_ref.name
        if not prior_output.is_file() or _sha256_file(prior_output) != rejected_hash:
            raise RuntimeError("MR1_HUMAN_REPAIR_PRIOR_OUTPUT_PRESERVATION_FAILED")
        marker_payload = {
            "schema_version": "mr1.human-repair-state.v1",
            "run_id": str(run_id),
            "review_round": repair["review_round"],
            "directive_hash": repair["directive_hash"],
            "rejected_output_sha256": rejected_hash,
            "preserved_revision_root": str(history),
            "preserved_output_path": str(prior_output),
            "preserved_paths": preserved,
            "prior_render_attempts": int(prior.get("render_attempts") or 0),
            "provider_outputs_reused": True,
            "provider_calls_repeated": False,
        }
        marker = {**marker_payload, "content_hash": stable_hash(marker_payload)}
        _write_json_atomic(history / "preserved-revision.json", marker)
        _write_json_atomic(marker_path, marker)
        return marker

    def continue_once(
        self,
        *,
        run_id: uuid.UUID | str,
        workspace: Path | str,
        authority: dict[str, Any],
        provider_outputs: dict[str, Any],
        resume_from: str | None,
    ) -> dict[str, Any]:
        resolved_run_id = uuid.UUID(str(run_id))
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if self.workspace_root is not None:
            allowed = self.workspace_root.resolve()
            if root != allowed and allowed not in root.parents:
                raise ValueError("MR1_RUN_WORKSPACE_OUTSIDE_CONFIGURED_ROOT")
        repair = self._human_repair_context(root, resolved_run_id)
        repair_state: dict[str, Any] | None = None
        completion_path = root / "local_production_result.json"
        if completion_path.is_file() and not completion_path.is_symlink():
            prior = json.loads(completion_path.read_text(encoding="utf-8"))
            self._validate_completed_result(root, prior, resolved_run_id)
            if not repair["active"]:
                return prior
            if (
                prior.get("human_repair_directive_hash") == repair["directive_hash"]
                and prior.get("review_round") == repair["review_round"]
            ):
                return prior
            repair_state = self._prepare_human_repair_workspace(
                root=root,
                run_id=resolved_run_id,
                prior=prior,
                repair=repair,
            )
        elif repair["active"]:
            marker_path = root / "human_repair_state.json"
            if not marker_path.is_file() or marker_path.is_symlink():
                raise ValueError("MR1_HUMAN_REPAIR_REQUIRES_PRIOR_COMPLETION")
            repair_state = json.loads(marker_path.read_text(encoding="utf-8"))
            if (
                repair_state.get("directive_hash") != repair["directive_hash"]
                or repair_state.get("review_round") != repair["review_round"]
            ):
                raise ValueError("MR1_HUMAN_REPAIR_STATE_CONFLICT")

        stage = "PROVIDER_OUTPUT_VALIDATION"
        prior_stage = resume_from or "PROVIDER_EXECUTION_COMPLETE"
        try:
            (
                normalized,
                timing_seed,
                forced,
                audio_path,
                audio_probe,
                alignment,
                timeline,
                caption_track,
                temporal_gate,
            ) = self._prepare_temporal_models(
                run_id=resolved_run_id,
                root=root,
                authority=authority,
                provider_outputs=provider_outputs,
            )
            audio_sha256 = _sha256_file(audio_path)
            stage = "VERIFIED_NARRATION_ALIGNMENT"
            if (
                alignment.verification_status != "PASS"
                or alignment.token_coverage != 1.0
            ):
                raise RuntimeError(
                    "MR1_VERIFIED_ALIGNMENT_BLOCKED:"
                    + ",".join(alignment.reconciliation_reason_codes)
                )

            stage = "CANONICAL_MEDIA_TIMELINE"
            temporal_dir = root / "temporal"
            captions_path = temporal_dir / "canonical-captions.srt"

            stage = "MEDIA_NORMALIZATION"
            decisions = self._visual_decisions(authority)
            assets, normalization, provenance = self._materialize_and_normalize_assets(
                root=root,
                authority=authority,
                timeline=timeline,
                decisions=decisions,
                provider_outputs=provider_outputs,
                repair=repair,
            )
            _write_json_atomic(
                root / "assets" / "media-normalization-manifest.json",
                normalization.model_dump(mode="json"),
            )
            _write_json_atomic(
                root / "assets" / "asset-provenance-manifest.json",
                provenance,
            )

            stage = "NATIVE_RENDER_PLAN"
            plan = self._build_plan(
                run_id=resolved_run_id,
                authority=authority,
                timeline=timeline,
                audio_path=audio_path,
                audio_hash=audio_sha256,
                assets=assets,
                decisions=decisions,
                repair=repair,
            )
            render_dir = root / "render"
            _write_json_atomic(
                render_dir / "native-render-plan.json", plan.model_dump(mode="json")
            )

            stage = "NATIVE_MOTION_COMPILER"
            manifest = NativeMotionCompiler().compile(
                plan,
                allow_resolved_provider_assets=True,
                canonical_timeline=timeline,
            )
            _write_json_atomic(
                render_dir / "compiled-native-render-manifest.json",
                manifest.model_dump(mode="json"),
            )
            envelope = self._production_envelope(
                run_id=resolved_run_id,
                authority=authority,
                plan=plan,
            )
            _write_json_atomic(
                render_dir / "production-render-envelope.json",
                envelope.model_dump(mode="json"),
            )

            stage = "NATIVE_FFMPEG_RENDER"
            command = self._build_command(
                root=root,
                run_id=resolved_run_id,
                manifest=manifest,
                audio_path=audio_path,
                repair=repair,
            )
            render_attempts = self._render_attempt_count(render_dir)
            completed = self._load_completed_render(
                root=root,
                manifest=manifest,
                command=command,
            )
            if completed is None:
                render_attempts += 1
                _write_json_atomic(
                    render_dir / "render-attempt.json",
                    {
                        "run_id": str(resolved_run_id),
                        "render_identity": f"mr1-render://small-team-ai/{resolved_run_id}/v1",
                        "attempt_count": render_attempts,
                        "provider_calls_repeated": False,
                        "last_started_at": datetime.now(UTC).isoformat(),
                    },
                )
                receipt, native_qc = NativeFFmpegRenderer(
                    root,
                    smoke_enabled=False,
                    production_enabled=True,
                ).execute(
                    manifest,
                    command,
                    purpose=MR1_RENDER_PURPOSE,
                    execution_envelope=envelope,
                )
            else:
                receipt, native_qc = completed
            if receipt.production_eligible is not True or native_qc.result != "PASS":
                raise RuntimeError("MR1_NATIVE_RENDER_OR_QC_NOT_PRODUCTION_PASS")
            total_render_attempts = render_attempts + int(
                (repair_state or {}).get("prior_render_attempts") or 0
            )

            stage = "TECHNICAL_MEDIA_QC"
            technical = self._technical_qc(
                run_id=resolved_run_id,
                native_qc=native_qc,
                receipt=receipt,
                normalization=normalization,
            )
            if technical["result"] != "PASS":
                raise RuntimeError(
                    "MR1_TECHNICAL_MEDIA_QC_FAILED:"
                    + ",".join(technical.get("reason_codes") or [])
                )
            qc_dir = root / "qc"
            _write_json_atomic(
                qc_dir / "native-media-qc.json", native_qc.model_dump(mode="json")
            )
            _write_json_atomic(qc_dir / "technical-media-qc.json", technical)

            stage = "CREATIVE_PERCEPTUAL_MEDIA_QC"
            creative = self._creative_qc(
                run_id=resolved_run_id,
                authority=authority,
                timeline=timeline,
                assets=assets,
                output_path=Path(receipt.output_path),
            )
            if creative["result"] not in {"PASS", "REVIEW_REQUIRED"}:
                raise RuntimeError("MR1_CREATIVE_MEDIA_QC_BLOCKED")
            _write_json_atomic(qc_dir / "creative-perceptual-media-qc.json", creative)

            stage = "REVIEW_MEDIA_CANDIDATE"
            thumbnail_path = self._thumbnail(
                root=root,
                output=Path(receipt.output_path),
                duration_ms=timeline.audio_duration_ms,
            )
            candidate = self._candidate(
                run_id=resolved_run_id,
                authority=authority,
                plan=plan,
                receipt=receipt,
                native_qc=native_qc,
                technical=technical,
                creative=creative,
                provenance=provenance,
                captions_path=captions_path,
                thumbnail_path=thumbnail_path,
                timeline=timeline,
                repair=repair,
            )
            candidate_path = root / "review" / "review-media-candidate.json"
            _write_json_atomic(candidate_path, candidate)

            stage = "LOCAL_ARCHIVE_PACKAGE"
            strict_package = self._strict_package(
                run_id=resolved_run_id,
                authority=authority,
                normalized=normalized,
                alignment=alignment.model_dump(mode="json"),
                timeline=timeline,
                normalization=normalization,
                plan=plan,
                manifest=manifest,
                receipt=receipt,
                technical=technical,
                creative=creative,
                candidate=candidate,
                provenance=provenance,
            )
            strict_path = (
                root / "archive_package" / "strict-production-render-package.json"
            )
            _write_json_atomic(strict_path, strict_package)
            self._write_archive_scoped_reports(
                root=root,
                run_id=resolved_run_id,
                authority=authority,
                provider_outputs=provider_outputs,
                timeline=timeline,
                normalization=normalization,
                plan=plan,
                receipt=receipt,
                technical=technical,
                creative=creative,
                candidate=candidate,
                render_attempts=total_render_attempts,
            )
            archive_sources, archive_path = self._archive_sources(root, resolved_run_id)

            result = {
                "schema_version": "mr1.local-production-result.v1",
                "continuation_version": MR1_LOCAL_CONTINUATION_VERSION,
                "state": "READY_FOR_ARCHIVE",
                "resume_from": "REVIEW_MEDIA_CANDIDATE_CREATED",
                "run_id": str(resolved_run_id),
                "provider_outputs_durable": True,
                "provider_calls_repeated": False,
                "local_provider_calls": 0,
                "youtube_calls": 0,
                "canonical_timeline": {
                    "result": "PASS",
                    "timing_authority": "CANONICAL_MEDIA_TIMELINE",
                    "estimated_timing_fallback_used": False,
                    "content_hash": timeline.timeline_hash,
                    "path": str(temporal_dir / "canonical-media-timeline.json"),
                },
                "verified_alignment": {
                    "result": "PASS",
                    "token_coverage": alignment.token_coverage,
                    "content_hash": alignment.content_hash,
                },
                "media_normalization": {
                    "result": normalization.result,
                    "actual_bytes_probed": True,
                    "minimum_effective_resolution": "1080p",
                    "content_hash": normalization.content_hash,
                    "path": str(root / "assets" / "media-normalization-manifest.json"),
                },
                "native_render_plan": {
                    "result": "PASS",
                    "deterministic": True,
                    "production_eligible": True,
                    "content_hash": plan.content_hash,
                    "path": str(render_dir / "native-render-plan.json"),
                },
                "native_motion_compiler": {
                    "result": "PASS",
                    "manifest_hash": manifest.manifest_hash,
                    "path": str(render_dir / "compiled-native-render-manifest.json"),
                },
                "native_ffmpeg_render": {
                    "result": "PASS",
                    "render_attempts": total_render_attempts,
                    "exit_status": receipt.exit_code,
                    "output_file_ref": receipt.output_path,
                    "output_sha256": receipt.output_checksum,
                    "output_size_bytes": Path(receipt.output_path).stat().st_size,
                    "duration_seconds": native_qc.checks.get("duration"),
                    "command_manifest_path": str(render_dir / "command_manifest.json"),
                    "execution_receipt_path": str(
                        render_dir / "execution_receipt.json"
                    ),
                },
                "render_attempts": total_render_attempts,
                "review_round": int(repair.get("review_round") or 1),
                "human_repair_directive_hash": repair.get("directive_hash"),
                "human_repair_classes": list(repair.get("repair_classes") or []),
                "repaired_from_output_sha256": repair.get("rejected_output_sha256"),
                "provider_outputs_reused_for_human_repair": bool(repair.get("active")),
                "technical_media_qc": technical,
                "creative_media_qc": creative,
                "review_media_candidate": candidate,
                "thumbnail_path": str(thumbnail_path),
                "captions_path": str(captions_path),
                "archive_path": str(archive_path),
                "archive_sources": archive_sources,
                "archive_item_count": len(archive_sources),
                "final_media_ref": None,
                "human_review_status": "PENDING",
                "not_publishable": True,
            }
            _write_json_atomic(completion_path, result)
            self._validate_completed_result(root, result, resolved_run_id)
            return result
        except Exception as exc:
            failure = self._local_failure(
                root=root,
                run_id=resolved_run_id,
                stage=stage,
                prior_stage=prior_stage,
                exc=exc,
            )
            return failure

    def prepare_temporal_authority_once(
        self,
        *,
        run_id: uuid.UUID | str,
        workspace: Path | str,
        authority: dict[str, Any],
        provider_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist the canonical timeline before any visual provider is called."""

        resolved_run_id = uuid.UUID(str(run_id))
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if self.workspace_root is not None:
            allowed = self.workspace_root.resolve()
            if root != allowed and allowed not in root.parents:
                raise ValueError("MR1_RUN_WORKSPACE_OUTSIDE_CONFIGURED_ROOT")
        (
            _normalized,
            _timing_seed,
            _forced,
            audio_path,
            _audio_probe,
            alignment,
            timeline,
            _caption_track,
            temporal_gate,
        ) = self._prepare_temporal_models(
            run_id=resolved_run_id,
            root=root,
            authority=authority,
            provider_outputs=provider_outputs,
        )
        supporting_manifest = self._supporting_visual_subwindows_manifest(
            timeline, authority=authority
        )
        if (
            temporal_gate.supporting_visual_subwindows_hash
            != supporting_manifest["content_hash"]
        ):
            raise RuntimeError("MR1_SUPPORTING_VISUAL_SUBWINDOW_GATE_MISMATCH")
        return {
            "schema_version": "mr1.temporal-authority-preparation.v1",
            "state": "CANONICAL_TIMELINE_READY",
            "result": "PASS",
            "run_id": str(resolved_run_id),
            "timing_authority": "CANONICAL_MEDIA_TIMELINE",
            "timeline_ref": str(root / "temporal" / "canonical-media-timeline.json"),
            "timeline_hash": timeline.timeline_hash,
            "verified_alignment_ref": str(
                root / "temporal" / "verified-narration-alignment.json"
            ),
            "verified_alignment_hash": alignment.content_hash,
            "token_coverage": alignment.token_coverage,
            "audio_path": str(audio_path),
            "audio_asset_ref": timeline.audio_asset_id,
            "audio_duration_ms": timeline.audio_duration_ms,
            "captions_path": str(root / "temporal" / "canonical-captions.srt"),
            "scene_windows": [
                {
                    "scene_id": item.segment_id,
                    "start_ms": item.scene_start_ms,
                    "end_ms": item.scene_end_ms,
                    "duration_ms": item.target_scene_duration_ms,
                }
                for item in timeline.segments
            ],
            "supporting_visual_subwindows": supporting_manifest[
                "supporting_visual_subwindows"
            ],
            "supporting_visual_subwindows_hash": supporting_manifest["content_hash"],
            "estimated_timing_fallback_used": False,
            "automatic_visual_fallback_used": False,
            "provider_calls_made_by_continuation": 0,
            "temporal_gate_hash": temporal_gate.content_hash,
        }

    def _prepare_temporal_models(
        self,
        *,
        run_id: uuid.UUID,
        root: Path,
        authority: Mapping[str, Any],
        provider_outputs: Mapping[str, Any],
    ) -> tuple[Any, ...]:
        normalized, timing_seed, forced, audio_path, audio_probe = (
            self._validate_provider_outputs(
                root=root,
                authority=authority,
                provider_outputs=provider_outputs,
            )
        )
        temporal_dir = root / "temporal"
        prepared_path = temporal_dir / "prepared-temporal-authority.json"
        paths = {
            "normalized": temporal_dir / "spoken-text-normalized.json",
            "timing": temporal_dir / "narration-timing-seed.json",
            "forced": temporal_dir / "forced-alignment-evidence.json",
            "alignment": temporal_dir / "verified-narration-alignment.json",
            "timeline": temporal_dir / "canonical-media-timeline.json",
            "captions": temporal_dir / "compiled-caption-track.json",
            "gate": temporal_dir / "temporal-authority-gate.json",
            "supporting_subwindows": temporal_dir / "supporting-visual-subwindows.json",
            "srt": temporal_dir / "canonical-captions.srt",
        }
        if prepared_path.is_file() and not prepared_path.is_symlink():
            from app.contracts.caption_voice_quality import CompiledCaptionTrack
            from app.contracts.temporal_authority import (
                TemporalAuthorityGateResult,
                VerifiedNarrationAlignment,
            )

            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            if (
                prepared.get("run_id") != str(run_id)
                or prepared.get("result") != "PASS"
            ):
                raise ValueError("MR1_PREPARED_TEMPORAL_AUTHORITY_IDENTITY_CONFLICT")
            if not all(
                path.is_file() and not path.is_symlink() for path in paths.values()
            ):
                raise ValueError("MR1_PREPARED_TEMPORAL_AUTHORITY_SET_INCOMPLETE")
            persisted_normalized = SpokenTextNormalized.model_validate_json(
                paths["normalized"].read_text(encoding="utf-8")
            )
            persisted_timing = NarrationTimingSeed.model_validate_json(
                paths["timing"].read_text(encoding="utf-8")
            )
            persisted_forced = ForcedAlignmentEvidence.model_validate_json(
                paths["forced"].read_text(encoding="utf-8")
            )
            alignment = VerifiedNarrationAlignment.model_validate_json(
                paths["alignment"].read_text(encoding="utf-8")
            )
            timeline = CanonicalMediaTimeline.model_validate_json(
                paths["timeline"].read_text(encoding="utf-8")
            )
            caption_track = CompiledCaptionTrack.model_validate_json(
                paths["captions"].read_text(encoding="utf-8")
            )
            temporal_gate = TemporalAuthorityGateResult.model_validate_json(
                paths["gate"].read_text(encoding="utf-8")
            )
            supporting_manifest = json.loads(
                paths["supporting_subwindows"].read_text(encoding="utf-8")
            )
            expected_supporting = self._supporting_visual_subwindows_manifest(
                timeline, authority=authority
            )
            if (
                persisted_normalized != normalized
                or persisted_timing != timing_seed
                or persisted_forced != forced
                or timeline.timeline_hash != prepared.get("timeline_hash")
                or alignment.content_hash != prepared.get("verified_alignment_hash")
                or caption_track.srt_text != paths["srt"].read_text(encoding="utf-8")
                or supporting_manifest != expected_supporting
                or temporal_gate.supporting_visual_subwindows_hash
                != expected_supporting["content_hash"]
                or prepared.get("supporting_visual_subwindows_hash")
                != expected_supporting["content_hash"]
            ):
                raise ValueError("MR1_PREPARED_TEMPORAL_AUTHORITY_BINDING_MISMATCH")
            return (
                persisted_normalized,
                persisted_timing,
                persisted_forced,
                audio_path,
                audio_probe,
                alignment,
                timeline,
                caption_track,
                temporal_gate,
            )

        alignment = NarrationAlignmentReconciler().reconcile(
            normalized=normalized,
            timing_seed=timing_seed,
            forced_alignment=forced,
            audio_asset_ref=timing_seed.audio_asset_ref,
            audio_duration_ms=_duration_ms(audio_probe),
        )
        if alignment.verification_status != "PASS" or alignment.token_coverage != 1.0:
            raise RuntimeError(
                "MR1_VERIFIED_ALIGNMENT_BLOCKED:"
                + ",".join(alignment.reconciliation_reason_codes)
            )
        timeline, caption_track, temporal_gate = self._compile_temporal_authority(
            run_id=run_id,
            root=root,
            authority=authority,
            normalized=normalized,
            alignment=alignment,
            audio_path=audio_path,
            audio_duration_ms=_duration_ms(audio_probe),
        )
        _write_json_atomic(paths["normalized"], normalized.model_dump(mode="json"))
        _write_json_atomic(paths["timing"], timing_seed.model_dump(mode="json"))
        _write_json_atomic(paths["forced"], forced.model_dump(mode="json"))
        _write_json_atomic(paths["alignment"], alignment.model_dump(mode="json"))
        _write_json_atomic(paths["timeline"], timeline.model_dump(mode="json"))
        _write_json_atomic(paths["captions"], caption_track.model_dump(mode="json"))
        _write_json_atomic(paths["gate"], temporal_gate.model_dump(mode="json"))
        supporting_manifest = self._supporting_visual_subwindows_manifest(
            timeline, authority=authority
        )
        if (
            temporal_gate.supporting_visual_subwindows_hash
            != supporting_manifest["content_hash"]
        ):
            raise RuntimeError("MR1_SUPPORTING_VISUAL_SUBWINDOW_GATE_MISMATCH")
        _write_json_atomic(paths["supporting_subwindows"], supporting_manifest)
        _write_text_atomic(paths["srt"], caption_track.srt_text)
        _write_json_atomic(
            root / "provider_evidence" / "narration-output.json",
            provider_outputs.get("narration") or {},
        )
        _write_json_atomic(
            root / "provider_evidence" / "alignment-output.json",
            provider_outputs.get("alignment") or {},
        )
        _write_json_atomic(
            prepared_path,
            {
                "schema_version": "mr1.prepared-temporal-authority.v1",
                "run_id": str(run_id),
                "result": "PASS",
                "timeline_hash": timeline.timeline_hash,
                "verified_alignment_hash": alignment.content_hash,
                "caption_track_hash": caption_track.content_hash,
                "supporting_visual_subwindows_hash": supporting_manifest[
                    "content_hash"
                ],
                "audio_sha256": _sha256_file(audio_path),
                "estimated_timing_fallback_used": False,
                "provider_calls_made_by_continuation": 0,
            },
        )
        return (
            normalized,
            timing_seed,
            forced,
            audio_path,
            audio_probe,
            alignment,
            timeline,
            caption_track,
            temporal_gate,
        )

    def _validate_provider_outputs(
        self,
        *,
        root: Path,
        authority: Mapping[str, Any],
        provider_outputs: Mapping[str, Any],
    ) -> tuple[
        SpokenTextNormalized,
        NarrationTimingSeed,
        ForcedAlignmentEvidence,
        Path,
        dict[str, Any],
    ]:
        narration = provider_outputs.get("narration")
        alignment_output = provider_outputs.get("alignment")
        if not isinstance(narration, Mapping) or not isinstance(
            alignment_output, Mapping
        ):
            raise ValueError("MR1_NARRATION_AND_ALIGNMENT_OUTPUTS_REQUIRED")
        if narration.get("provider_call_made") is False:
            raise ValueError("MR1_REAL_NARRATION_PROVIDER_OUTPUT_REQUIRED")
        if alignment_output.get("provider_call_made") is False:
            raise ValueError("MR1_REAL_FORCED_ALIGNMENT_PROVIDER_OUTPUT_REQUIRED")

        normalized_raw = (
            narration.get("temporal_spoken_text_normalized")
            or alignment_output.get("temporal_spoken_text_normalized")
            or narration.get("spoken_text_normalized")
        )
        if not isinstance(normalized_raw, Mapping):
            normalized = self._normalized_from_authority(authority)
        else:
            normalized = SpokenTextNormalized.model_validate(normalized_raw)
        if normalized.content_hash != _hash_model(normalized, "content_hash"):
            raise ValueError("MR1_TEMPORAL_SPOKEN_TEXT_HASH_MISMATCH")
        spoken_authority = (authority.get("resolved") or {}).get(
            "spoken_text_normalized", {}
        )
        spoken_content = spoken_authority.get("content") or {}
        exact_spoken_text = str(spoken_content.get("normalized_text") or "")
        if not exact_spoken_text or normalized.spoken_text != exact_spoken_text:
            raise ValueError("MR1_APPROVED_SPOKEN_TEXT_BINDING_MISMATCH")
        expected_normalized_hash = str(spoken_content.get("normalized_text_hash") or "")
        if expected_normalized_hash and expected_normalized_hash != stable_hash(
            {"normalized_text": exact_spoken_text}
        ):
            raise ValueError("MR1_APPROVED_NORMALIZED_TEXT_HASH_INVALID")

        timing_raw = narration.get("timing_seed") or narration.get(
            "narration_timing_seed"
        )
        if not isinstance(timing_raw, Mapping):
            raise ValueError("MR1_REAL_PROVIDER_TIMING_SEED_REQUIRED")
        timing_seed = NarrationTimingSeed.model_validate(timing_raw)
        if timing_seed.content_hash != _hash_model(timing_seed, "content_hash"):
            raise ValueError("MR1_PROVIDER_TIMING_SEED_HASH_MISMATCH")
        if (
            not timing_seed.timing_available
            or timing_seed.spoken_text_hash != normalized.spoken_text_hash
            or not timing_seed.normalized_character_alignment
        ):
            raise ValueError("MR1_PROVIDER_TIMING_SEED_NOT_USABLE")

        audio_raw = (
            narration.get("audio_path")
            or narration.get("output_path")
            or narration.get("path")
        )
        if not audio_raw:
            raise ValueError("MR1_NARRATION_AUDIO_PATH_REQUIRED")
        audio_path = _inside(root, str(audio_raw), must_exist=True)
        audio_sha256 = _sha256_file(audio_path)
        expected_audio_hash = str(
            narration.get("audio_sha256") or narration.get("sha256") or ""
        )
        if expected_audio_hash != audio_sha256:
            raise ValueError("MR1_NARRATION_AUDIO_CHECKSUM_MISMATCH")
        audio_probe = _probe(audio_path, self.ffprobe)
        audio_stream = _audio_stream(audio_probe)
        measured_duration_ms = _duration_ms(audio_probe)
        claimed_duration_ms = int(narration.get("audio_duration_ms") or 0)
        if (
            claimed_duration_ms <= 0
            or abs(measured_duration_ms - claimed_duration_ms) > 40
            or timing_seed.audio_duration_ms != claimed_duration_ms
        ):
            raise ValueError("MR1_NARRATION_AUDIO_DURATION_MISMATCH")
        if (
            int(audio_stream.get("sample_rate") or 0) <= 0
            or int(audio_stream.get("channels") or 0) <= 0
        ):
            raise ValueError("MR1_NARRATION_AUDIO_STREAM_INVALID")
        if timing_seed.audio_asset_ref not in {
            str(narration.get("audio_asset_ref") or ""),
            f"file-sha256:{audio_sha256}",
            str(audio_path),
        }:
            raise ValueError("MR1_NARRATION_AUDIO_AUTHORITY_REF_MISMATCH")

        forced_raw = (
            alignment_output.get("forced_alignment_evidence")
            or alignment_output.get("evidence")
            or alignment_output.get("forced_alignment")
        )
        if isinstance(forced_raw, Mapping):
            forced = ForcedAlignmentEvidence.model_validate(forced_raw)
        else:
            forced = self._forced_from_flat_alignment(
                alignment_output=alignment_output,
                normalized=normalized,
                timing_seed=timing_seed,
            )
        if forced.content_hash != _hash_model(forced, "content_hash"):
            raise ValueError("MR1_FORCED_ALIGNMENT_EVIDENCE_HASH_MISMATCH")
        if (
            forced.verification_status != "PASS"
            or forced.missing_tokens
            or forced.extra_words
            or forced.spoken_text_hash != normalized.spoken_text_hash
            or forced.audio_asset_ref != timing_seed.audio_asset_ref
            or forced.audio_duration_ms != measured_duration_ms
        ):
            raise ValueError("MR1_FORCED_ALIGNMENT_EVIDENCE_NOT_STRICT_PASS")
        if str(alignment_output.get("audio_sha256") or audio_sha256) != audio_sha256:
            raise ValueError("MR1_ALIGNMENT_AUDIO_CHECKSUM_MISMATCH")
        if str(alignment_output.get("verification_status") or "PASS") != "PASS":
            raise ValueError("MR1_ALIGNMENT_GATE_NOT_PASS")
        if float(alignment_output.get("token_coverage") or 1.0) != 1.0:
            raise ValueError("MR1_ALIGNMENT_TOKEN_COVERAGE_INCOMPLETE")

        return normalized, timing_seed, forced, audio_path, audio_probe

    @staticmethod
    def _pexels_output(
        provider_outputs: Mapping[str, Any], scene_id: str
    ) -> Mapping[str, Any] | None:
        direct = provider_outputs.get(f"pexels:{scene_id}")
        if isinstance(direct, Mapping):
            return direct
        collection = provider_outputs.get("pexels") or provider_outputs.get(
            "pexels_assets"
        )
        if isinstance(collection, Mapping) and isinstance(
            collection.get(scene_id), Mapping
        ):
            return collection[scene_id]
        return None

    @staticmethod
    def _forced_from_flat_alignment(
        *,
        alignment_output: Mapping[str, Any],
        normalized: SpokenTextNormalized,
        timing_seed: NarrationTimingSeed,
    ) -> ForcedAlignmentEvidence:
        raw_words = alignment_output.get("verified_words") or alignment_output.get(
            "words"
        )
        if not isinstance(raw_words, list) or not raw_words:
            raise ValueError("MR1_FORCED_ALIGNMENT_WORD_EVIDENCE_REQUIRED")
        words: list[AlignedWord] = []
        for index, raw in enumerate(raw_words, start=1):
            if not isinstance(raw, Mapping):
                raise ValueError("MR1_FORCED_ALIGNMENT_WORD_INVALID")
            words.append(
                AlignedWord(
                    word_id=str(raw.get("word_id") or f"forced-{index:04d}"),
                    text=str(raw.get("text") or raw.get("word") or ""),
                    start_ms=int(raw["start_ms"]),
                    end_ms=int(raw["end_ms"]),
                    loss=(float(raw["loss"]) if raw.get("loss") is not None else None),
                    source_spoken_token_ids=list(
                        raw.get("source_spoken_token_ids") or []
                    ),
                )
            )
        provider_request_id = (
            str(alignment_output.get("provider_request_id") or "").strip() or None
        )
        payload = {
            "provider_key": "elevenlabs_forced_alignment",
            "provider_request_id": provider_request_id,
            "provider_request_id_availability": (
                "PRESENT" if provider_request_id else "NOT_EXPOSED_BY_ENDPOINT"
            ),
            "audio_asset_ref": timing_seed.audio_asset_ref,
            "audio_duration_ms": timing_seed.audio_duration_ms,
            "spoken_text_hash": normalized.spoken_text_hash,
            "words": [item.model_dump(mode="json") for item in words],
            "characters": [],
            "alignment_loss": alignment_output.get("alignment_loss"),
            "transcript_loss": alignment_output.get("transcript_loss"),
            "missing_tokens": list(alignment_output.get("missing_tokens") or []),
            "extra_words": list(
                alignment_output.get("extra_words")
                or alignment_output.get("extra_tokens")
                or []
            ),
            "warnings": list(alignment_output.get("warnings") or []),
            "verification_status": str(
                alignment_output.get("verification_status") or "BLOCK"
            ),
        }
        return ForcedAlignmentEvidence(**payload, content_hash=stable_hash(payload))

    @staticmethod
    def _normalized_from_authority(
        authority: Mapping[str, Any],
    ) -> SpokenTextNormalized:
        """Compatibility path for durable gateways created before the typed model.

        Production gateways now persist the full model.  This identity mapping is
        intentionally narrow and never performs a second text normalization.
        """

        spoken = (authority.get("resolved") or {}).get("spoken_text_normalized", {})
        content = spoken.get("content") or {}
        text = str(content.get("normalized_text") or "")
        if not text:
            raise ValueError("MR1_APPROVED_NORMALIZED_TEXT_REQUIRED")
        raw_tokens = [
            item
            for item in list(content.get("spoken_tokens") or [])
            if re.search(r"[A-Za-z0-9]", str(item.get("text") or ""))
        ]
        tokens: list[SpokenToken] = []
        cursor = 0
        for ordinal, raw in enumerate(raw_tokens, start=1):
            value = str(raw.get("text") or "")
            start = text.find(value, cursor)
            if start < 0:
                raise ValueError("MR1_APPROVED_SPOKEN_TOKEN_MAPPING_FAILED")
            end = start + len(value)
            token_id = str(
                raw.get("token_id") or f"token-{int(raw.get('index', ordinal - 1)):06d}"
            )
            tokens.append(
                SpokenToken(
                    token_id=token_id,
                    text=value,
                    spoken_span=TextSpan(start=start, end=end),
                    source_spans=[TextSpan(start=start, end=end)],
                    normalization_operation_ids=[],
                    comparison_key=re.sub(r"[^a-z0-9]", "", value.casefold()),
                )
            )
            cursor = end
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        payload = {
            "normalization_version": "pkg1-approved-spoken-text/identity-v1",
            "script_revision_id": str(
                spoken.get("artifact_version_id") or "approved-script"
            ),
            "source_text_hash": text_hash,
            "source_character_count": len(text),
            "spoken_text": text,
            "spoken_text_hash": text_hash,
            "spoken_character_count": len(text),
            "normalization_operations": [],
            "source_to_spoken_spans": [
                SourceToSpokenSpan(
                    source_span=TextSpan(start=0, end=len(text)),
                    spoken_span=TextSpan(start=0, end=len(text)),
                    operation_ids=[],
                ).model_dump(mode="json")
            ],
            "spoken_tokens": [item.model_dump(mode="json") for item in tokens],
            "pronunciation_dictionary_refs": list(
                content.get("pronunciation_dictionary_refs") or []
            ),
            "normalization_warnings": [],
        }
        return SpokenTextNormalized(**payload, content_hash=stable_hash(payload))

    def _compile_temporal_authority(
        self,
        *,
        run_id: uuid.UUID,
        root: Path,
        authority: Mapping[str, Any],
        normalized: SpokenTextNormalized,
        alignment: Any,
        audio_path: Path,
        audio_duration_ms: int,
    ) -> tuple[CanonicalMediaTimeline, Any, Any]:
        visual_ref, _ = _artifact_ref(authority, "visual_plan")
        script_ref, _ = _artifact_ref(authority, "script")
        scene_inputs = self._editorial_segments(
            authority=authority,
            normalized=normalized,
            script_ref=script_ref,
            visual_ref=visual_ref,
        )
        exact_target = authority.get("exact_target") or {}
        visual_content = (
            (authority.get("resolved") or {}).get("visual_plan", {}).get("content", {})
        )
        channel_id = str(
            visual_content.get("channel_id")
            or (authority.get("destination") or {}).get("channel_handle")
            or "small-team-ai"
        )
        timeline = CanonicalMediaTimelineCompiler().compile(
            project_id=str(
                authority.get("project_id") or exact_target.get("project_id")
            ),
            package_id=str(
                authority.get("package_artifact_version_id")
                or exact_target.get("package_artifact_version_id")
            ),
            channel_id=channel_id,
            script_revision_id=normalized.script_revision_id,
            spoken_text_revision_id=normalized.content_hash,
            tts_request_id=f"mr1-narration:{run_id}",
            normalized=normalized,
            alignment=alignment,
            segments=scene_inputs,
        )
        timeline = self._assign_alignment_silence_boundaries(timeline)
        captioned = ReadableCaptionCompiler().compile(
            normalized=normalized,
            alignment=alignment,
            timeline=timeline,
            policy=self._caption_policy(authority),
            aspect_ratio="16:9",
        )
        timeline = captioned.timeline
        final_audio_payload = {
            "audio_asset_ref": alignment.audio_asset_ref,
            "duration_ms": audio_duration_ms,
            "is_final": True,
        }
        final_audio = FinalNarrationAudio(
            **final_audio_payload,
            content_hash=stable_hash(final_audio_payload),
        )
        temporal_gate = TemporalAuthorityGate().evaluate(
            normalized=normalized,
            final_audio=final_audio,
            alignment=alignment,
            timeline=timeline,
        )
        if temporal_gate.gate_status != "PASS":
            raise RuntimeError(
                "MR1_TEMPORAL_AUTHORITY_GATE_BLOCKED:"
                + ",".join(temporal_gate.block_reasons)
            )
        if timeline.audio_asset_id != alignment.audio_asset_ref:
            raise RuntimeError("MR1_PARALLEL_AUDIO_AUTHORITY_DETECTED")
        supporting_manifest = self._supporting_visual_subwindows_manifest(
            timeline, authority=authority
        )
        gate_payload = temporal_gate.model_dump(
            mode="json", exclude={"content_hash"}, exclude_none=True
        )
        gate_payload["supporting_visual_subwindows_hash"] = supporting_manifest[
            "content_hash"
        ]
        temporal_gate = type(temporal_gate)(
            **gate_payload, content_hash=stable_hash(gate_payload)
        )
        return timeline, captioned.track, temporal_gate

    @staticmethod
    def _supporting_visual_subwindows_manifest(
        timeline: CanonicalMediaTimeline,
        *,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        policy_ref = (
            "mr1-temporal-policy://supporting-stock-subwindow/"
            "min-8000ms-or-floor-20pct/v1"
        )
        by_scene = {item.segment_id: item for item in timeline.segments}
        if set(by_scene) != set(ALL_SCENES):
            raise ValueError("MR1_SUPPORTING_VISUAL_TIMELINE_SCENE_SET_INVALID")
        visual_routes = resolve_mr1_visual_route_authority(authority)
        windows: list[dict[str, Any]] = []
        for scene_id in visual_routes.pexels_scenes:
            scene = by_scene[scene_id]
            scene_duration = scene.target_scene_duration_ms
            stock_duration = min(8_000, (scene_duration * 20) // 100)
            native_duration = scene_duration - stock_duration
            if stock_duration <= 0 or native_duration <= 0:
                raise ValueError(f"MR1_PEXELS_STOCK_SUBWINDOW_INVALID:{scene_id}")
            stock_end = scene.scene_start_ms + stock_duration
            if stock_end >= scene.scene_end_ms:
                raise ValueError(f"MR1_PEXELS_STOCK_SUBWINDOW_INVALID:{scene_id}")
            windows.append(
                {
                    "scene_id": scene_id,
                    "stock_context": {
                        "start_ms": scene.scene_start_ms,
                        "end_ms": stock_end,
                        "duration_ms": stock_duration,
                    },
                    "native_explanation": {
                        "start_ms": stock_end,
                        "end_ms": scene.scene_end_ms,
                        "duration_ms": native_duration,
                    },
                    "native_mechanism": MR1_SCENE_VISUAL_BLUEPRINTS[scene_id][
                        "mechanism"
                    ],
                    "policy_ref": policy_ref,
                }
            )
        payload = {
            "schema_version": "mr1.supporting-visual-subwindows.v1",
            "timeline_hash": timeline.timeline_hash,
            "policy_ref": policy_ref,
            "supporting_visual_subwindows": windows,
        }
        return {**payload, "content_hash": stable_hash(payload)}

    @staticmethod
    def _editorial_segments(
        *,
        authority: Mapping[str, Any],
        normalized: SpokenTextNormalized,
        script_ref: str,
        visual_ref: str,
    ) -> list[EditorialSegmentInput]:
        spoken_content = (
            (authority.get("resolved") or {})
            .get("spoken_text_normalized", {})
            .get("content", {})
        )
        raw_artifact_tokens = [
            item
            for item in list(spoken_content.get("spoken_tokens") or [])
            if re.search(r"[A-Za-z0-9]", str(item.get("text") or ""))
        ]
        if len(raw_artifact_tokens) != len(normalized.spoken_tokens):
            raise ValueError("MR1_TEMPORAL_APPROVED_TOKEN_COUNT_MISMATCH")
        token_segment = {
            temporal.token_id: str(raw.get("segment_id") or "")
            for temporal, raw in zip(
                normalized.spoken_tokens, raw_artifact_tokens, strict=True
            )
        }
        visual = (
            (authority.get("resolved") or {}).get("visual_plan", {}).get("content", {})
        )
        raw_scenes = list(visual.get("scenes") or [])
        if [item.get("scene_id") for item in raw_scenes] != list(ALL_SCENES):
            raise ValueError("MR1_EXACT_NINE_SCENE_VISUAL_PLAN_REQUIRED")
        visual_routes = resolve_mr1_visual_route_authority(authority)
        result: list[EditorialSegmentInput] = []
        seen: set[str] = set()
        for scene in raw_scenes:
            segment_refs = {str(item) for item in list(scene.get("segment_refs") or [])}
            selected = [
                token
                for token in normalized.spoken_tokens
                if token_segment[token.token_id] in segment_refs
            ]
            if not selected:
                raise ValueError(
                    f"MR1_SCENE_APPROVED_TOKEN_BINDING_MISSING:{scene['scene_id']}"
                )
            ids = [item.token_id for item in selected]
            if seen.intersection(ids):
                raise ValueError("MR1_SCENE_TOKEN_OVERLAP")
            seen.update(ids)
            source_start = min(
                span.start for item in selected for span in item.source_spans
            )
            source_end = max(
                span.end for item in selected for span in item.source_spans
            )
            result.append(
                EditorialSegmentInput(
                    segment_id=str(scene["scene_id"]),
                    editorial_span=TextSpan(start=source_start, end=source_end),
                    spoken_token_ids=ids,
                    motion_intent=(
                        "PEXELS_SUPPORTING_MOTION"
                        if visual_routes.routes[str(scene["scene_id"])]
                        == "PEXELS_VIDEO"
                        else visual_routes.routes[str(scene["scene_id"])]
                    ),
                    source_provenance=[
                        {"type": "approved_script", "ref": script_ref},
                        {"type": "approved_visual_plan", "ref": visual_ref},
                    ],
                )
            )
        if seen != {item.token_id for item in normalized.spoken_tokens}:
            raise ValueError("MR1_SCENE_TOKEN_COVERAGE_GAP")
        return result

    @staticmethod
    def _assign_alignment_silence_boundaries(
        timeline: CanonicalMediaTimeline,
    ) -> CanonicalMediaTimeline:
        """Assign verified inter-word silence without inventing timing estimates."""

        segments = []
        source = list(timeline.segments)
        for index, segment in enumerate(source):
            scene_start = 0 if index == 0 else segment.scene_start_ms
            scene_end = (
                source[index + 1].scene_start_ms
                if index + 1 < len(source)
                else timeline.audio_duration_ms
            )
            if scene_end <= scene_start:
                raise ValueError("MR1_CANONICAL_SILENCE_BOUNDARY_INVALID")
            provenance = [
                *segment.source_provenance,
                {
                    "type": "silence_boundary_policy",
                    "value": "ASSIGN_VERIFIED_INTER_WORD_SILENCE_TO_PRECEDING_SCENE",
                },
            ]
            segments.append(
                segment.model_copy(
                    update={
                        "scene_start_ms": scene_start,
                        "scene_end_ms": scene_end,
                        "target_scene_duration_ms": scene_end - scene_start,
                        "source_provenance": provenance,
                    }
                )
            )
        payload = timeline.model_dump(mode="json", exclude={"timeline_hash"})
        payload["segments"] = [item.model_dump(mode="json") for item in segments]
        payload["qc_metrics"] = {
            **dict(payload.get("qc_metrics") or {}),
            "scene_silence_boundary_policy": (
                "ASSIGN_VERIFIED_INTER_WORD_SILENCE_TO_PRECEDING_SCENE"
            ),
            "scene_timeline_contiguous": True,
            "estimated_timing_used": False,
        }
        return CanonicalMediaTimeline(**payload, timeline_hash=stable_hash(payload))

    @staticmethod
    def _caption_policy(authority: Mapping[str, Any]) -> dict[str, Any]:
        snapshot = (authority.get("exact_bindings") or {}).get(
            "compiled_channel_policy_snapshot"
        ) or {}
        return {
            "policy_ref": _binding_ref(
                snapshot, "compiled-policy-snapshot://mr1/subtitle-sidecar"
            )
            + "/subtitle-sidecar",
            "policy_version": "mr1-production-subtitle-sidecar-v1",
            "longform_16_9": {
                "max_chars_per_line_pass": 42,
                "max_chars_per_line_review": 46,
                "max_chars_per_line_block": 46,
            },
            "global": {
                "max_lines_per_cue": 2,
                "cue_duration_seconds": {
                    "pass": [1.0, 6.0],
                    "review": [0.8, 7.0],
                    "block_outside": [0.8, 7.0],
                },
                "reading_speed_cps": {
                    "pass_average_max": 15,
                    "review_average_max": 17.5,
                    "block_average_above": 17.5,
                    "pass_p95_max": 17,
                    "review_p95_max": 20,
                    "block_any_above": 20,
                },
            },
        }

    @staticmethod
    def _visual_decisions(authority: Mapping[str, Any]) -> list[VisualSourceBinding]:
        visual_routes = resolve_mr1_visual_route_authority(authority)
        resolved = (authority.get("resolved") or {}).get(
            "visual_source_decision_set", {}
        )
        content = resolved.get("content") or {}
        raw_decisions = list(content.get("decisions") or [])
        by_scene = {str(item.get("scene_id")): item for item in raw_decisions}
        if set(by_scene) != set(ALL_SCENES) or len(raw_decisions) != 9:
            raise ValueError("MR1_EXACT_VISUAL_SOURCE_DECISION_SET_REQUIRED")
        if content.get("automatic_pexels_to_ai_fallback") is not False:
            raise ValueError("MR1_AUTOMATIC_VISUAL_FALLBACK_PROHIBITED")
        version_id = str(resolved.get("artifact_version_id") or "")
        values: list[VisualSourceBinding] = []
        for scene_id in ALL_SCENES:
            raw = by_scene[scene_id]
            expected_route = VisualSourceRoute(visual_routes.routes[scene_id])
            decision_hash = stable_hash(raw)
            reason_codes = raw.get("routing_reason_codes")
            if not isinstance(reason_codes, list) or not reason_codes:
                reason_codes = [str(raw.get("eligibility") or expected_route.value)]
            values.append(
                VisualSourceBinding(
                    scene_id=scene_id,
                    decision_ref=f"artifact-version://{version_id}#{scene_id}",
                    decision_hash=decision_hash,
                    preferred_route=expected_route,
                    fallback_class=(
                        SourceFallbackClass.PEXELS_ONLY
                        if expected_route == VisualSourceRoute.PEXELS_VIDEO
                        else SourceFallbackClass.NATIVE_ONLY
                    ),
                    routing_reason_codes=[str(item) for item in reason_codes],
                    eligibility_gate_refs=[
                        f"artifact-version://{version_id}#{scene_id}/eligibility/PASS"
                    ],
                )
            )
        return values

    @staticmethod
    def _scene_visual_blueprints(
        authority: Mapping[str, Any],
    ) -> dict[str, dict[str, Any]]:
        visual = (
            (authority.get("resolved") or {}).get("visual_plan", {}).get("content", {})
        )
        raw_scenes = list(visual.get("scenes") or [])
        by_scene = {str(item.get("scene_id") or ""): item for item in raw_scenes}
        if len(raw_scenes) != 9 or set(by_scene) != set(ALL_SCENES):
            raise ValueError("MR1_EXACT_VISUAL_PLAN_SCENE_SET_REQUIRED")
        visual_routes = resolve_mr1_visual_route_authority(authority)
        result: dict[str, dict[str, Any]] = {}
        for scene_id in ALL_SCENES:
            approved_intent = str(by_scene[scene_id].get("semantic_intent") or "")
            if (
                scene_id == "SC-04"
                and visual_routes.routes[scene_id] == "NATIVE_MOTION_GRAPHIC"
            ):
                blueprint = deepcopy(MR1_SC04_NATIVE_MOTION_BLUEPRINT)
            else:
                blueprint = deepcopy(MR1_SCENE_VISUAL_BLUEPRINTS[scene_id])
            if approved_intent != blueprint["semantic_intent"]:
                raise ValueError(f"MR1_VISUAL_MECHANISM_AUTHORITY_MISMATCH:{scene_id}")
            approved_mechanism = by_scene[scene_id].get("native_mechanism")
            if (
                approved_mechanism is not None
                and approved_mechanism != blueprint["mechanism"]
            ):
                raise ValueError(f"MR1_VISUAL_MECHANISM_AUTHORITY_MISMATCH:{scene_id}")
            result[scene_id] = blueprint
        return result

    def _materialize_and_normalize_assets(
        self,
        *,
        root: Path,
        authority: Mapping[str, Any],
        timeline: CanonicalMediaTimeline,
        decisions: list[VisualSourceBinding],
        provider_outputs: Mapping[str, Any],
        repair: Mapping[str, Any],
    ) -> tuple[list[ResolvedMediaAsset], MediaNormalizationManifest, dict[str, Any]]:
        by_decision = {item.scene_id: item for item in decisions}
        if set(by_decision) != set(ALL_SCENES):
            raise ValueError("MR1_VISUAL_DECISION_COVERAGE_INCOMPLETE")
        visual_content = provider_outputs
        scene_blueprints = self._scene_visual_blueprints(authority)
        # Semantic text remains authority-owned; provider output never authors it.
        source_dir = root / "assets" / "source"
        normalized_dir = root / "assets" / "normalized"
        source_dir.mkdir(parents=True, exist_ok=True)
        normalized_dir.mkdir(parents=True, exist_ok=True)
        results: list[ResolvedMediaAsset] = []
        items: list[MediaNormalizationItem] = []
        provenance_items: list[dict[str, Any]] = []
        execution_items: list[dict[str, Any]] = []
        selected_pexels_ids: set[str] = set()
        selected_source_paths: set[Path] = set()

        for timeline_scene in timeline.segments:
            scene_id = timeline_scene.segment_id
            decision = by_decision[scene_id]
            is_stock = decision.preferred_route == VisualSourceRoute.PEXELS_VIDEO
            blueprint = scene_blueprints[scene_id]
            provider_output: Mapping[str, Any] | None = None
            native_overlay_source: Path | None = None
            mechanism_initial_source: Path | None = None
            if not is_stock:
                source = self._materialize_native_diagram(
                    root=root,
                    scene_id=scene_id,
                    blueprint=blueprint,
                )
                mechanism_initial_source = self._materialize_native_diagram(
                    root=root,
                    scene_id=scene_id,
                    blueprint=blueprint,
                    render_state="INITIAL",
                )
                route = decision.preferred_route
                rights_status = "NOT_REQUIRED"
                provenance_refs = [
                    (
                        f"native-renderer://mr1/{scene_id}/"
                        f"{route.value.casefold().replace('_', '-')}-v1"
                    ),
                    decision.decision_ref,
                    str(source),
                ]
                rights_evidence = {
                    "rights_status": "NOT_REQUIRED",
                    "reason": "LOCALLY_RENDERED_NATIVE_DIAGRAM",
                    "provider_asset_id": None,
                }
            else:
                provider_output = self._pexels_output(visual_content, scene_id)
                if not isinstance(provider_output, Mapping):
                    raise ValueError(f"MR1_PEXELS_OUTPUT_MISSING:{scene_id}")
                if provider_output.get("provider_call_made") is False:
                    raise ValueError(f"MR1_REAL_PEXELS_OUTPUT_REQUIRED:{scene_id}")
                if provider_output.get("route") not in {None, "PEXELS_VIDEO"}:
                    raise ValueError(f"MR1_PEXELS_ROUTE_CHANGED:{scene_id}")
                output_scene = str(provider_output.get("scene_id") or scene_id)
                if output_scene != scene_id:
                    raise ValueError(f"MR1_PEXELS_SCENE_BINDING_MISMATCH:{scene_id}")
                source_raw = provider_output.get("local_path") or provider_output.get(
                    "path"
                )
                if not source_raw:
                    raise ValueError(f"MR1_PEXELS_LOCAL_PATH_REQUIRED:{scene_id}")
                source = _inside(root, str(source_raw), must_exist=True)
                source_hash = _sha256_file(source)
                if str(provider_output.get("sha256") or "") != source_hash:
                    raise ValueError(f"MR1_PEXELS_CHECKSUM_MISMATCH:{scene_id}")
                provider_asset_id = str(
                    provider_output.get("provider_asset_id")
                    or (provider_output.get("selected_candidate") or {}).get(
                        "provider_asset_id"
                    )
                    or (provider_output.get("selected_candidate") or {}).get("id")
                    or ""
                )
                if not provider_asset_id:
                    raise ValueError(
                        f"MR1_PEXELS_PROVIDER_ASSET_ID_REQUIRED:{scene_id}"
                    )
                if (
                    provider_asset_id in selected_pexels_ids
                    or source in selected_source_paths
                ):
                    raise ValueError("MR1_PEXELS_DUPLICATE_SELECTION_PROHIBITED")
                selected_pexels_ids.add(provider_asset_id)
                selected_source_paths.add(source)
                license_ref = str(
                    provider_output.get("license_ref")
                    or provider_output.get("license_url")
                    or ""
                )
                creator_ref = str(
                    provider_output.get("creator_ref")
                    or provider_output.get("creator_url")
                    or ""
                )
                source_page = str(provider_output.get("source_page_url") or "")
                if license_ref != "https://www.pexels.com/license/":
                    raise ValueError(f"MR1_PEXELS_LICENSE_BINDING_INVALID:{scene_id}")
                if not creator_ref or not source_page:
                    raise ValueError(f"MR1_PEXELS_PROVENANCE_INCOMPLETE:{scene_id}")
                route = VisualSourceRoute.PEXELS_VIDEO
                rights_status = "CONFIRMED"
                provenance_refs = [
                    f"pexels-video://{provider_asset_id}",
                    creator_ref,
                    license_ref,
                    source_page,
                    decision.decision_ref,
                    str(source),
                ]
                rights_evidence = {
                    "rights_status": "CONFIRMED",
                    "provider_asset_id": provider_asset_id,
                    "provider_file_id": provider_output.get("provider_file_id"),
                    "creator_ref": creator_ref,
                    "creator_name": provider_output.get("creator_name"),
                    "source_page_url": source_page,
                    "license_ref": license_ref,
                    "rights_policy_ref": provider_output.get("rights_policy_ref"),
                    "attribution_copy": provider_output.get("attribution_copy"),
                }
                _write_json_atomic(
                    root / "provider_evidence" / f"pexels-{scene_id}.json",
                    provider_output,
                )
                native_overlay_source = self._materialize_native_diagram(
                    root=root,
                    scene_id=scene_id,
                    blueprint=blueprint,
                    overlay_only=True,
                )

            source_probe = _probe(source, self.ffprobe)
            source_video = _video_stream(source_probe)
            source_width = int(source_video.get("width") or 0)
            source_height = int(source_video.get("height") or 0)
            if min(source_width, source_height) < 1080:
                raise ValueError(f"MR1_SOURCE_BELOW_MINIMUM_EFFECTIVE_1080P:{scene_id}")
            if is_stock:
                required_stock_ms = min(
                    8_000,
                    (timeline_scene.target_scene_duration_ms * 20) // 100,
                )
                if _duration_ms(source_probe) + 50 < required_stock_ms:
                    raise ValueError(
                        f"MR1_PEXELS_ASSET_SHORTER_THAN_STOCK_SUBWINDOW:{scene_id}"
                    )
            source_hash = _sha256_file(source)
            normalized_path = normalized_dir / f"{scene_id}-normalized.mp4"
            normalized_probe = self._normalize_asset(
                root=root,
                scene_id=scene_id,
                source=source,
                source_hash=source_hash,
                destination=normalized_path,
                duration_ms=timeline_scene.target_scene_duration_ms,
                source_route=decision.preferred_route,
                still_image=not is_stock,
                blueprint=blueprint,
                overlay_source=native_overlay_source,
                mechanism_initial_source=mechanism_initial_source,
                repair=repair,
            )
            normalized_video = _video_stream(normalized_probe)
            normalized_hash = _sha256_file(normalized_path)
            asset_id = f"mr1-asset:{scene_id}:{normalized_hash[:16]}"
            result = ResolvedMediaAsset(
                asset_id=asset_id,
                scene_id=scene_id,
                source_decision_ref=decision.decision_ref,
                source_decision_hash=decision.decision_hash,
                actual_route=route,
                local_file_ref=str(normalized_path),
                checksum_sha256=normalized_hash,
                width=int(normalized_video["width"]),
                height=int(normalized_video["height"]),
                duration_ms=timeline_scene.target_scene_duration_ms,
                rights_status=rights_status,
                provenance_refs=provenance_refs,
                normalization_state="NORMALIZED",
                scene_usage_ref=(
                    f"canonical-timeline:{timeline.timeline_hash}#{scene_id}"
                ),
            )
            results.append(result)
            items.append(
                MediaNormalizationItem(
                    asset_id=asset_id,
                    source_ref=str(source),
                    source_checksum=source_hash,
                    normalized_ref=str(normalized_path),
                    normalized_checksum=normalized_hash,
                    byte_probe={
                        "source": source_probe,
                        "normalized": normalized_probe,
                        "source_effective_resolution": f"{source_width}x{source_height}",
                        "minimum_effective_resolution": "1080p",
                        "normalized_codec": normalized_video.get("codec_name"),
                        "normalized_pixel_format": normalized_video.get("pix_fmt"),
                        "normalized_width": normalized_video.get("width"),
                        "normalized_height": normalized_video.get("height"),
                        "normalized_fps": normalized_video.get("avg_frame_rate"),
                        "actual_bytes_probed": True,
                    },
                    state="PASS",
                )
            )
            provenance_items.append(
                {
                    "scene_id": scene_id,
                    "route": route.value,
                    "source_path": str(source),
                    "source_sha256": source_hash,
                    "normalized_path": str(normalized_path),
                    "normalized_sha256": normalized_hash,
                    "source_decision_ref": decision.decision_ref,
                    "source_decision_hash": decision.decision_hash,
                    "provenance_refs": provenance_refs,
                    "rights": rights_evidence,
                    "fallback_used": False,
                    "approved_mechanism": blueprint["mechanism"],
                    "native_overlay_required": True,
                    "native_overlay_source": (
                        str(native_overlay_source)
                        if native_overlay_source
                        else str(source)
                    ),
                    "mechanism_initial_source": (
                        str(mechanism_initial_source)
                        if mechanism_initial_source
                        else None
                    ),
                }
            )
            normalized_sidecar = normalized_path.with_suffix(".normalization.json")
            normalized_receipt = json.loads(
                normalized_sidecar.read_text(encoding="utf-8")
            )
            execution_items.append(
                {
                    "scene_id": scene_id,
                    "source_route": route.value,
                    "semantic_intent": blueprint["semantic_intent"],
                    "approved_mechanism": blueprint["mechanism"],
                    "exact_text_required": True,
                    "exact_number_required": blueprint["exact_number_required"],
                    "authoritative_labels": [
                        blueprint["headline"],
                        *blueprint["cards"],
                        blueprint["formula"],
                        blueprint["footer"],
                    ],
                    "animation_type": blueprint["animation_type"],
                    "transition_in": blueprint["transition_in"],
                    "transition_out": blueprint["transition_out"],
                    "native_overlay_required": True,
                    "pexels_context_duration_ms": normalized_receipt.get(
                        "pexels_context_duration_ms", 0
                    ),
                    "native_explanation_after_context": (is_stock),
                    "source_path": str(source),
                    "native_overlay_source": (
                        str(native_overlay_source)
                        if native_overlay_source
                        else str(source)
                    ),
                    "mechanism_initial_source": (
                        str(mechanism_initial_source)
                        if mechanism_initial_source
                        else None
                    ),
                    "normalized_path": str(normalized_path),
                    "normalized_sha256": normalized_hash,
                    "actual_bytes_rendered": True,
                    "frame_state_evidence": normalized_receipt["frame_state_evidence"],
                }
            )

        if [item.scene_id for item in results] != list(ALL_SCENES):
            raise ValueError("MR1_NORMALIZED_ASSET_SCENE_ORDER_INVALID")
        manifest_payload = {
            "manifest_id": f"mr1-media-normalization:{timeline.timeline_hash}",
            "items": [item.model_dump(mode="json") for item in items],
            "target_video": {
                "codec": "h264",
                "container": "mp4",
                "fps": 30,
                "pixel_format": "yuv420p",
                "resolution": "1920x1080",
                "aspect_ratio": "16:9",
                "color": "bt709",
                "minimum_effective_resolution": "1080p",
            },
            "target_audio": {"sample_rate": 48000, "channels": 2},
            "actual_byte_probe_required": True,
            "result": "PASS",
        }
        manifest = MediaNormalizationManifest(
            **manifest_payload, content_hash=stable_hash(manifest_payload)
        )
        provenance_payload = {
            "schema_version": "mr1.asset-provenance-manifest.v1",
            "timeline_hash": timeline.timeline_hash,
            "items": provenance_items,
            "scene_count": 9,
            "native_scene_count": len(
                [
                    item
                    for item in decisions
                    if item.preferred_route != VisualSourceRoute.PEXELS_VIDEO
                ]
            ),
            "pexels_scene_count": len(
                [
                    item
                    for item in decisions
                    if item.preferred_route == VisualSourceRoute.PEXELS_VIDEO
                ]
            ),
            "provider_substitution_used": False,
            "automatic_fallback_used": False,
            "rights_complete": True,
        }
        provenance = {
            **provenance_payload,
            "content_hash": stable_hash(provenance_payload),
        }
        visual_plan_ref, visual_plan_hash = _artifact_ref(authority, "visual_plan")
        execution_payload = {
            "schema_version": "mr1.scene-visual-execution-manifest.v1",
            "visual_plan_ref": visual_plan_ref,
            "visual_plan_hash": visual_plan_hash,
            "scene_count": len(execution_items),
            "items": execution_items,
            "all_scene_mechanisms_exact": len(execution_items) == 9,
            "all_exact_content_native_owned": True,
            "pexels_supporting_context_only": True,
            "pexels_native_explanation_scene_ids": [
                item.scene_id
                for item in decisions
                if item.preferred_route == VisualSourceRoute.PEXELS_VIDEO
            ],
            "provider_route_changes": 0,
            "provider_calls_made": 0,
        }
        execution_manifest = {
            **execution_payload,
            "content_hash": stable_hash(execution_payload),
        }
        _write_json_atomic(
            root / "assets" / "scene-visual-execution-manifest.json",
            execution_manifest,
        )
        return results, manifest, provenance

    def _materialize_native_diagram(
        self,
        *,
        root: Path,
        scene_id: str,
        blueprint: Mapping[str, Any],
        overlay_only: bool = False,
        render_state: str = "FINAL",
    ) -> Path:
        if render_state not in {"INITIAL", "FINAL"}:
            raise ValueError("MR1_NATIVE_VISUAL_RENDER_STATE_INVALID")
        if (
            scene_id not in ALL_SCENES
            or (overlay_only and render_state != "FINAL")
            or (render_state == "INITIAL" and "initial_cards" not in blueprint)
        ):
            raise ValueError("MR1_NATIVE_VISUAL_ROUTE_BINDING_INVALID")
        directory = (
            root
            / "assets"
            / "source"
            / ("native-overlays" if overlay_only else "native")
        )
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "native-overlay" if overlay_only else "native-diagram"
        if render_state == "INITIAL":
            suffix += "-state-initial"
        destination = _inside(root, directory / f"{scene_id}-{suffix}.png")
        sidecar = destination.with_suffix(".visual.json")
        identity = {
            "schema_version": "mr1.native-scene-visual.v1",
            "scene_id": scene_id,
            "route_role": (
                "PEXELS_NATIVE_EXPLANATION_OVERLAY"
                if overlay_only
                else "NATIVE_DIAGRAM"
            ),
            "render_state": render_state,
            "blueprint": _jsonable(blueprint),
            "renderer": "FFMPEG_DRAWBOX_DRAWTEXT_1920X1080",
            "caption_safe_lower_band_reserved": True,
        }
        identity_hash = stable_hash(identity)
        if destination.is_file() and sidecar.is_file():
            prior = json.loads(sidecar.read_text(encoding="utf-8"))
            if prior.get("identity_hash") != identity_hash or prior.get(
                "output_sha256"
            ) != _sha256_file(destination):
                raise FileExistsError(f"MR1_NATIVE_VISUAL_IDENTITY_CONFLICT:{scene_id}")
            probe = _probe(destination, self.ffprobe)
            video = _video_stream(probe)
            if (int(video.get("width") or 0), int(video.get("height") or 0)) != (
                1920,
                1080,
            ):
                raise FileExistsError(f"MR1_NATIVE_SOURCE_IDENTITY_CONFLICT:{scene_id}")
            return destination
        if destination.exists() or sidecar.exists():
            raise FileExistsError(
                f"MR1_NATIVE_VISUAL_COMPLETION_SET_INCOMPLETE:{scene_id}"
            )

        label_dir = directory / f"{scene_id}-{render_state.lower()}-labels"
        label_dir.mkdir(parents=True, exist_ok=True)
        font = self._font_path()
        escaped_font = str(font).replace("\\", "\\\\").replace("'", "\\'")
        palette = {
            "SC-01": ("071827", "2563eb", "14b8a6"),
            "SC-02": ("10233b", "0ea5e9", "f59e0b"),
            "SC-03": ("172554", "4f46e5", "22c55e"),
            "SC-04": ("111827", "0f766e", "38bdf8"),
            "SC-05": ("1f2937", "7c3aed", "06b6d4"),
            "SC-06": ("172033", "ea580c", "2563eb"),
            "SC-07": ("172554", "0369a1", "f97316"),
            "SC-08": ("0f2742", "0891b2", "65a30d"),
            "SC-09": ("102a1c", "15803d", "eab308"),
        }
        background, primary, secondary = palette[scene_id]
        filters = [
            "drawgrid=width=96:height=96:thickness=1:color=white@0.045",
            "drawbox=x=0:y=0:w=1920:h=220:color=black@0.22:t=fill",
            f"drawbox=x=70:y=62:w=14:h=118:color=0x{secondary}@1:t=fill",
        ]
        label_index = 0

        def add_text(
            value: str,
            *,
            x: int,
            y: int,
            size: int,
            color: str = "white",
            line_spacing: int = 10,
        ) -> None:
            nonlocal label_index
            if not str(value).strip():
                return
            label_path = _inside(root, label_dir / f"{scene_id}-{label_index:02d}.txt")
            label_index += 1
            label_value = str(value).strip() + "\n"
            if label_path.exists():
                if (
                    label_path.is_symlink()
                    or label_path.read_text(encoding="utf-8") != label_value
                ):
                    raise FileExistsError(
                        f"MR1_NATIVE_LABEL_IDENTITY_CONFLICT:{scene_id}"
                    )
            else:
                _write_text_atomic(label_path, label_value)
            escaped_label = (
                str(label_path)
                .replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\\'")
            )
            filters.append(
                f"drawtext=fontfile='{escaped_font}':textfile='{escaped_label}':"
                f"fontcolor={color}:fontsize={size}:line_spacing={line_spacing}:x={x}:y={y}"
            )

        add_text(str(blueprint["headline"]), x=110, y=62, size=58)
        add_text(str(blueprint["subhead"]), x=112, y=145, size=28, color="0xdbeafe")
        cards = [
            str(value)
            for value in (
                blueprint["initial_cards"]
                if render_state == "INITIAL"
                else blueprint["cards"]
            )
        ]
        mechanism = str(blueprint["mechanism"])
        if mechanism == "FIVE_COLUMN_WORKLOAD_GRID_ARITHMETIC":
            for index, card in enumerate(cards):
                x = 70 + index * 365
                filters.append(
                    f"drawbox=x={x}:y=260:w=325:h=310:color=0x{primary}@0.94:t=fill"
                )
                add_text(card, x=x + 34, y=337, size=35, line_spacing=18)
        elif mechanism == "THREE_INPUT_COUNTER_RECOMPUTE":
            for index, card in enumerate(cards):
                x = 125 + index * 585
                filters.append(
                    f"drawbox=x={x}:y=270:w=500:h=285:color=0x{primary if index != 1 else secondary}@0.94:t=fill"
                )
                add_text(card, x=x + 42, y=333, size=39, line_spacing=22)
        elif mechanism == "BOUNDED_WORKFLOW_MAP":
            for index, card in enumerate(cards):
                x = 70 + index * 460
                filters.append(
                    f"drawbox=x={x}:y=320:w=360:h=200:color=0x{primary if index % 2 == 0 else secondary}@0.94:t=fill"
                )
                if index:
                    filters.append(
                        f"drawbox=x={x - 100}:y=407:w=100:h=18:color=white@0.82:t=fill"
                    )
                add_text(card, x=x + 52, y=382, size=40)
        elif mechanism in {
            "BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST",
            "OBSERVED_VALUE_CALCULATION_SHEET",
        }:
            for index, card in enumerate(cards):
                column = index % 2
                row = index // 2
                x = 105 + column * 895
                y = 245 + row * 150
                filters.append(
                    f"drawbox=x={x}:y={y}:w=815:h=118:color=0x{primary if column == 0 else secondary}@0.88:t=fill"
                )
                filters.append(
                    f"drawbox=x={x + 22}:y={y + 35}:w=40:h=40:color=white@0.20:t=3"
                )
                add_text(card, x=x + 88, y=y + 35, size=31)
        elif mechanism == "FIVE_CARD_CONTROL_PANEL":
            for index, card in enumerate(cards):
                x = 65 + index * 365
                color = (
                    secondary
                    if render_state == "FINAL" and index == len(cards) - 1
                    else primary
                )
                filters.append(
                    f"drawbox=x={x}:y=285:w=330:h=310:color=0x{color}@0.94:t=fill"
                )
                add_text(card, x=x + 28, y=380, size=33, line_spacing=16)
        elif mechanism == "BASELINE_VERSUS_PILOT_HYPOTHESIS":
            for index, card in enumerate(cards):
                x = 130 + index * 895
                filters.append(
                    f"drawbox=x={x}:y=270:w=765:h=410:color=0x{primary if index == 0 else secondary}@0.90:t=fill"
                )
                add_text(card, x=x + 55, y=335, size=35, line_spacing=24)
        elif mechanism == "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE":
            add_text("REASON CODE", x=155, y=235, size=27, color="0xbae6fd")
            add_text("NAMED OWNER", x=940, y=235, size=27, color="0xbae6fd")
            for index, card in enumerate(cards):
                y = 285 + index * 112
                filters.append(
                    f"drawbox=x=115:y={y}:w=1690:h=88:color=0x{primary if index % 2 == 0 else secondary}@0.82:t=fill"
                )
                add_text(card, x=165, y=y + 22, size=30)
                add_text("OWNER  →  FALLBACK", x=945, y=y + 22, size=29)
        elif mechanism == "BRIEF_CONTEXT_THEN_FIVE_ITEM_AUDIT":
            for index, card in enumerate(cards):
                y = 235 + index * 105
                filters.append(
                    f"drawbox=x=205:y={y}:w=1510:h=78:color=0x{primary if index < 4 else secondary}@0.86:t=fill"
                )
                add_text(f"{index + 1}.  {card}", x=250, y=y + 17, size=31)
        else:
            raise ValueError(f"MR1_NATIVE_MECHANISM_UNSUPPORTED:{scene_id}")

        formula_y = (
            690
            if mechanism
            not in {
                "BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST",
                "OBSERVED_VALUE_CALCULATION_SHEET",
                "BRIEF_CONTEXT_THEN_EXCEPTION_QUEUE",
                "BRIEF_CONTEXT_THEN_FIVE_ITEM_AUDIT",
            }
            else 715
        )
        filters.append(
            f"drawbox=x=105:y={formula_y}:w=1710:h=86:color=0x07111f@0.96:t=fill"
        )
        formula_value = str(
            blueprint.get("initial_formula", "")
            if render_state == "INITIAL"
            else blueprint["formula"]
        )
        if formula_value:
            add_text(formula_value, x=145, y=formula_y + 22, size=32)
        footer_y = min(835, formula_y + 112)
        add_text(str(blueprint["footer"]), x=145, y=footer_y, size=25, color="0xcbd5e1")
        graph = ",".join(filters)
        part = destination.with_name(destination.stem + ".part.png")
        part.unlink(missing_ok=True)
        _run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x{background}:s=1920x1080:r=30",
                "-vf",
                graph,
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-update",
                "1",
                str(part),
            ],
            f"MR1_NATIVE_DIAGRAM_RENDER_FAILED:{scene_id}",
        )
        os.replace(part, destination)
        probe = _probe(destination, self.ffprobe)
        video = _video_stream(probe)
        if (int(video.get("width") or 0), int(video.get("height") or 0)) != (
            1920,
            1080,
        ):
            raise RuntimeError(f"MR1_NATIVE_DIAGRAM_RESOLUTION_INVALID:{scene_id}")
        _write_json_atomic(
            sidecar,
            {
                **identity,
                "identity_hash": identity_hash,
                "output_sha256": _sha256_file(destination),
                "state_semantic": (
                    (blueprint.get("state_semantics") or ["INITIAL", "FINAL"])[
                        0 if render_state == "INITIAL" else 1
                    ]
                ),
                "rendered_authoritative_labels": [
                    str(blueprint["headline"]),
                    str(blueprint["subhead"]),
                    *[card for card in cards if card],
                    *([formula_value] if formula_value else []),
                    str(blueprint["footer"]),
                ],
                "actual_bytes_probed": True,
                "width": 1920,
                "height": 1080,
            },
        )
        return destination

    def _normalize_asset(
        self,
        *,
        root: Path,
        scene_id: str,
        source: Path,
        source_hash: str,
        destination: Path,
        duration_ms: int,
        source_route: VisualSourceRoute,
        still_image: bool,
        blueprint: Mapping[str, Any],
        overlay_source: Path | None,
        mechanism_initial_source: Path | None,
        repair: Mapping[str, Any],
    ) -> dict[str, Any]:
        destination = _inside(root, destination)
        sidecar = destination.with_suffix(".normalization.json")
        is_stock = source_route == VisualSourceRoute.PEXELS_VIDEO
        if still_image is is_stock:
            raise ValueError(f"MR1_NORMALIZATION_ROUTE_MODE_MISMATCH:{scene_id}")
        pexels_context_duration_ms = (
            min(8_000, (duration_ms * 20) // 100) if is_stock else 0
        )
        if is_stock:
            if overlay_source is None:
                raise ValueError(f"MR1_PEXELS_NATIVE_OVERLAY_REQUIRED:{scene_id}")
            overlay_source = _inside(root, overlay_source, must_exist=True)
            if not 0 < pexels_context_duration_ms < duration_ms:
                raise ValueError(f"MR1_PEXELS_STOCK_SUBWINDOW_INVALID:{scene_id}")
        elif overlay_source is not None:
            raise ValueError(
                f"MR1_NATIVE_SCENE_UNEXPECTED_SECONDARY_OVERLAY:{scene_id}"
            )
        if not is_stock:
            if mechanism_initial_source is None:
                raise ValueError(f"MR1_NATIVE_INITIAL_STATE_REQUIRED:{scene_id}")
            mechanism_initial_source = _inside(
                root, mechanism_initial_source, must_exist=True
            )
        elif mechanism_initial_source is not None:
            raise ValueError(f"MR1_PEXELS_UNEXPECTED_NATIVE_INITIAL_STATE:{scene_id}")
        repair_profile = dict(repair.get("repair_profile") or {})
        mechanism_transition_ms = (
            max(
                1,
                round(
                    duration_ms
                    * float(repair_profile.get("mechanism_transition_fraction", 0.42))
                ),
            )
            if not is_stock
            else pexels_context_duration_ms
        )
        identity = {
            "scene_id": scene_id,
            "source_path": str(source),
            "source_sha256": source_hash,
            "native_overlay_path": str(overlay_source) if overlay_source else None,
            "native_overlay_sha256": (
                _sha256_file(overlay_source) if overlay_source else None
            ),
            "mechanism_initial_path": (
                str(mechanism_initial_source) if mechanism_initial_source else None
            ),
            "mechanism_initial_sha256": (
                _sha256_file(mechanism_initial_source)
                if mechanism_initial_source
                else None
            ),
            "duration_ms": duration_ms,
            "pexels_context_duration_ms": pexels_context_duration_ms,
            "native_explanation_duration_ms": duration_ms - pexels_context_duration_ms,
            "mechanism_transition_ms": mechanism_transition_ms,
            "approved_mechanism": blueprint["mechanism"],
            "animation_type": blueprint["animation_type"],
            "human_repair_directive_hash": repair.get("directive_hash"),
            "review_round": repair.get("review_round", 1),
            "repair_profile": repair_profile,
            "target": "1920x1080/30fps/h264/yuv420p/bt709",
        }
        if destination.is_file() and sidecar.is_file():
            prior = json.loads(sidecar.read_text(encoding="utf-8"))
            if prior.get("identity") != identity:
                raise FileExistsError(f"MR1_NORMALIZATION_IDENTITY_CONFLICT:{scene_id}")
            if prior.get("normalized_sha256") != _sha256_file(destination):
                raise FileExistsError(f"MR1_NORMALIZATION_CHECKSUM_CONFLICT:{scene_id}")
            frame_states = prior.get("frame_state_evidence") or {}
            expected_state_key = (
                "stock_to_native_state_change" if is_stock else "motion_state_change"
            )
            if frame_states.get(expected_state_key) is not True:
                raise FileExistsError(
                    f"MR1_NORMALIZATION_FRAME_EVIDENCE_INCOMPLETE:{scene_id}"
                )
            return self._validate_normalized_probe(
                scene_id=scene_id,
                probe=_probe(destination, self.ffprobe),
                duration_ms=duration_ms,
            )
        if destination.exists() or sidecar.exists():
            raise FileExistsError(
                f"MR1_NORMALIZATION_COMPLETION_SET_INCOMPLETE:{scene_id}"
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_name(destination.stem + ".part.mp4")
        part.unlink(missing_ok=True)
        if still_image:
            if mechanism_initial_source is None:
                raise ValueError(f"MR1_NATIVE_INITIAL_STATE_REQUIRED:{scene_id}")
            transition_seconds = mechanism_transition_ms / 1000.0
            fade_seconds = min(
                0.80,
                max(
                    0.08,
                    float(repair_profile.get("overlay_fade_ms", 350)) / 1000.0,
                ),
            )
            filtergraph = (
                "[0:v]scale=1920:1080,setsar=1,fps=30,format=yuv420p[initial];"
                "[1:v]scale=1920:1080,format=rgba,"
                f"fade=t=in:st=0:d={fade_seconds:.6f}:alpha=1,"
                f"setpts=PTS-STARTPTS+{transition_seconds:.6f}/TB[final];"
                "[initial][final]overlay=eof_action=repeat:shortest=0:format=auto,"
                "format=yuv420p[v]"
            )
            input_and_filter_args = [
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(mechanism_initial_source),
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(source),
                "-filter_complex",
                filtergraph,
                "-map",
                "[v]",
            ]
        else:
            if overlay_source is None:
                raise ValueError(f"MR1_PEXELS_NATIVE_OVERLAY_REQUIRED:{scene_id}")
            stock_seconds = pexels_context_duration_ms / 1000.0
            fade_seconds = min(
                0.80,
                max(
                    0.08,
                    float(repair_profile.get("overlay_fade_ms", 350)) / 1000.0,
                ),
            )
            stock_geometry = (
                "scale=1920:1080:force_original_aspect_ratio=decrease,"
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black"
                if repair_profile.get("crop_mode") == "SAFE_CONTAIN"
                else "scale=1920:1080:force_original_aspect_ratio=increase,"
                "crop=1920:1080"
            )
            filtergraph = (
                f"[0:v]{stock_geometry},setsar=1,fps=30,format=yuv420p[stock];"
                "[1:v]scale=1920:1080,format=rgba,"
                f"fade=t=in:st=0:d={fade_seconds:.6f}:alpha=1,"
                f"setpts=PTS-STARTPTS+{stock_seconds:.6f}/TB[native];"
                "[stock][native]overlay=eof_action=repeat:shortest=0:format=auto,"
                "format=yuv420p[v]"
            )
            input_and_filter_args = [
                "-stream_loop",
                "-1",
                "-i",
                str(source),
                "-loop",
                "1",
                "-framerate",
                "30",
                "-i",
                str(overlay_source),
                "-filter_complex",
                filtergraph,
                "-map",
                "[v]",
            ]
        _run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                *input_and_filter_args,
                "-an",
                "-t",
                f"{duration_ms / 1000.0:.6f}",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-movflags",
                "+faststart",
                str(part),
            ],
            f"MR1_MEDIA_NORMALIZATION_FAILED:{scene_id}",
        )
        os.replace(part, destination)
        probe = self._validate_normalized_probe(
            scene_id=scene_id,
            probe=_probe(destination, self.ffprobe),
            duration_ms=duration_ms,
        )
        if is_stock:
            stock_frame = _frame_fingerprint(
                destination,
                seconds=pexels_context_duration_ms / 2000.0,
                ffmpeg=self.ffmpeg,
            )
            native_frame = _frame_fingerprint(
                destination,
                seconds=(
                    pexels_context_duration_ms
                    + (duration_ms - pexels_context_duration_ms) / 2.0
                )
                / 1000.0,
                ffmpeg=self.ffmpeg,
            )
            frame_state_evidence = {
                "stock_context": stock_frame,
                "native_explanation": native_frame,
                "stock_source_sha256": source_hash,
                "native_overlay_source_sha256": _sha256_file(overlay_source),
                "state_semantics": [
                    "PEXELS_SUPPORTING_CONTEXT_ONLY",
                    str(blueprint["mechanism"]),
                ],
                "stock_to_native_state_change": (
                    stock_frame["decoded_frame_sha256"]
                    != native_frame["decoded_frame_sha256"]
                ),
            }
            if not frame_state_evidence["stock_to_native_state_change"]:
                raise RuntimeError(
                    f"MR1_PEXELS_NATIVE_STATE_CHANGE_NOT_RENDERED:{scene_id}"
                )
        else:
            early_frame = _frame_fingerprint(
                destination,
                seconds=max(0.001, duration_ms * 0.20 / 1000.0),
                ffmpeg=self.ffmpeg,
            )
            late_frame = _frame_fingerprint(
                destination,
                seconds=max(0.002, duration_ms * 0.78 / 1000.0),
                ffmpeg=self.ffmpeg,
            )
            frame_state_evidence = {
                "mechanism_early": early_frame,
                "mechanism_late": late_frame,
                "initial_state_source_sha256": _sha256_file(mechanism_initial_source),
                "final_state_source_sha256": source_hash,
                "state_semantics": list(blueprint["state_semantics"]),
                "motion_state_change": (
                    early_frame["decoded_frame_sha256"]
                    != late_frame["decoded_frame_sha256"]
                ),
            }
            if not frame_state_evidence["motion_state_change"]:
                raise RuntimeError(f"MR1_NATIVE_MOTION_NOT_RENDERED:{scene_id}")
        _write_json_atomic(
            sidecar,
            {
                "schema_version": "mr1.asset-normalization-receipt.v1",
                "identity": identity,
                "normalized_sha256": _sha256_file(destination),
                "actual_byte_probe": probe,
                "pexels_context_duration_ms": pexels_context_duration_ms,
                "native_explanation_duration_ms": duration_ms
                - pexels_context_duration_ms,
                "mechanism_transition_ms": mechanism_transition_ms,
                "state_semantics": blueprint.get("state_semantics") or [],
                "motion_semantic": blueprint["animation_type"],
                "native_overlay_burned_into_actual_bytes": True,
                "frame_state_evidence": frame_state_evidence,
                "state": "PASS",
            },
        )
        return probe

    @staticmethod
    def _validate_normalized_probe(
        *, scene_id: str, probe: dict[str, Any], duration_ms: int
    ) -> dict[str, Any]:
        video = _video_stream(probe)
        try:
            numerator, denominator = str(video.get("avg_frame_rate") or "0/1").split(
                "/", 1
            )
            fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
        measured = _duration_ms(probe)
        if (
            video.get("codec_name") != "h264"
            or int(video.get("width") or 0) != 1920
            or int(video.get("height") or 0) != 1080
            or abs(fps - 30.0) > 0.001
            or video.get("pix_fmt") != "yuv420p"
            or abs(measured - duration_ms) > 250
        ):
            raise RuntimeError(f"MR1_NORMALIZED_MEDIA_PROFILE_INVALID:{scene_id}")
        return probe

    @staticmethod
    def _font_path() -> Path:
        candidates = (
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        )
        font = next((item for item in candidates if item.is_file()), None)
        if font is None:
            raise FileNotFoundError("MR1_NATIVE_FONT_NOT_FOUND")
        return font

    def _build_plan(
        self,
        *,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        timeline: CanonicalMediaTimeline,
        audio_path: Path,
        audio_hash: str,
        assets: list[ResolvedMediaAsset],
        decisions: list[VisualSourceBinding],
        repair: Mapping[str, Any],
    ) -> NativeRenderPlan:
        by_asset = {item.scene_id: item for item in assets}
        by_decision = {item.scene_id: item for item in decisions}
        if set(by_asset) != set(ALL_SCENES) or set(by_decision) != set(ALL_SCENES):
            raise ValueError("MR1_NATIVE_PLAN_SCENE_BINDINGS_INCOMPLETE")
        blueprints = self._scene_visual_blueprints(authority)
        script_ref, script_hash = _artifact_ref(authority, "script")
        visual_plan_ref, visual_plan_hash = _artifact_ref(authority, "visual_plan")
        scenes: list[NativeRenderScene] = []
        for segment in timeline.segments:
            asset = by_asset[segment.segment_id]
            decision = by_decision[segment.segment_id]
            is_stock = decision.preferred_route == VisualSourceRoute.PEXELS_VIDEO
            blueprint = blueprints[segment.segment_id]
            content_refs = [
                f"{script_ref}#{segment.segment_id.replace('SC-', 'S')}",
                f"{visual_plan_ref}#{segment.segment_id}",
            ]
            exact_kinds = ["HEADLINE", "WORKFLOW_LABEL"]
            if blueprint["exact_number_required"]:
                exact_kinds.append("NUMBER")
            exact_payload = {
                "scene_id": segment.segment_id,
                "source_decision_ref": decision.decision_ref,
                "source_decision_hash": decision.decision_hash,
                "preferred_source_route": decision.preferred_route,
                "exact_text_required": True,
                "exact_number_required": blueprint["exact_number_required"],
                "forbidden_generated_text": True,
                "forbidden_generated_logo": True,
                "forbidden_generated_fake_ui": True,
                "native_overlay_required": True,
                "authoritative_content_kinds": exact_kinds,
                "authoritative_content_refs": content_refs,
            }
            exact_contract = ExactTextNativeOverlayContract(
                **exact_payload, content_hash=stable_hash(exact_payload)
            )
            safe_regions = [
                TextSafeRegion(
                    id=f"{segment.segment_id}-native-content",
                    x=0.035,
                    y=0.04,
                    width=0.93,
                    height=0.74,
                    purpose="APPROVED_EXACT_MECHANISM_AND_LABELS",
                    minimum_contrast_requirement=4.5,
                    alignment="CENTER",
                )
            ]
            reserved_regions: list[TextSafeRegion] = []
            overlay_payload = {
                "plan_id": (
                    f"mr1-native-overlay:{segment.segment_id}:"
                    f"{decision.decision_hash[:16]}"
                ),
                "scene_id": segment.segment_id,
                "source_decision_ref": decision.decision_ref,
                "source_decision_hash": decision.decision_hash,
                "preferred_source_route": decision.preferred_route,
                "exact_text_contract": exact_contract,
                "text_safe_regions": safe_regions,
                "reserved_overlay_regions": reserved_regions,
                "overlay_content_refs": content_refs,
                "native_overlay_required": True,
            }
            overlay_plan = NativeOverlayPlan(
                **overlay_payload,
                content_hash=stable_hash(_jsonable(overlay_payload)),
            )
            scenes.append(
                NativeRenderScene(
                    scene_id=segment.segment_id,
                    source_segment_ids=[segment.segment_id],
                    narration_start_ms=segment.scene_start_ms,
                    narration_end_ms=segment.scene_end_ms,
                    duration_ms=segment.target_scene_duration_ms,
                    visual_treatment=str(blueprint["visual_treatment"]),
                    layout_type=(
                        f"{blueprint['mechanism']}_WITH_NATIVE_OVERLAY_SAFE_AREA"
                    ),
                    asset_requirements=[AssetRequirement(key=asset.asset_id)],
                    resolved_asset_refs=[
                        ResolvedAssetRef(
                            key=asset.asset_id,
                            path=asset.local_file_ref,
                            checksum=asset.checksum_sha256,
                        )
                    ],
                    animation_type=str(blueprint["animation_type"]),
                    transition_in=str(blueprint["transition_in"]),
                    transition_out=str(blueprint["transition_out"]),
                    emphasis_targets=[
                        str(blueprint["headline"]),
                        str(blueprint["formula"]),
                    ],
                    originality_role=(
                        "OBSERVABLE_REALITY_SUPPORT"
                        if is_stock
                        else "MECHANISM_EXPLANATION"
                    ),
                    provider_intent=(
                        "APPROVED_PEXELS_SUPPORTING_FOOTAGE_ACTUAL_BYTES"
                        if is_stock
                        else "DETERMINISTIC_NATIVE_DIAGRAM_ACTUAL_BYTES"
                    ),
                    scene_notes=(
                        "Pexels footage is brief illustrative context only; the native overlay owns the approved mechanism and exact labels."
                        if is_stock
                        else "Authority-owned exact native mechanism; no generated evidence."
                    ),
                    visual_routing_mode="VSR1_STRICT",
                    source_decision_ref=decision.decision_ref,
                    source_decision_hash=decision.decision_hash,
                    preferred_source_route=decision.preferred_route,
                    exact_text_required=True,
                    exact_number_required=bool(blueprint["exact_number_required"]),
                    forbidden_generated_text=True,
                    forbidden_generated_logo=True,
                    forbidden_generated_fake_ui=True,
                    text_safe_regions=safe_regions,
                    reserved_overlay_regions=reserved_regions,
                    eligibility_gate_refs=decision.eligibility_gate_refs,
                    native_overlay_required=True,
                    native_overlay_plan=overlay_plan,
                )
            )

        visual_direction = _package_binding(authority, "visual_direction_contract") or (
            visual_plan_ref,
            visual_plan_hash,
        )
        originality = _package_binding(authority, "episode_originality_manifest") or (
            f"package-originality://{authority.get('package_artifact_version_id')}",
            str(authority.get("package_content_hash") or visual_plan_hash),
        )
        claim = _package_binding(authority, "claim_evidence_ledger")
        exact_bindings = authority.get("exact_bindings") or {}
        profile = exact_bindings.get("channel_profile_version") or {}
        effective = (
            exact_bindings.get("effective_context_snapshot")
            or exact_bindings.get("effective_context")
            or {}
        )
        caption_metrics = timeline.qc_metrics
        creative = {
            name: {
                "result": "REVIEW_REQUIRED",
                "reason_codes": ["MR1_HUMAN_FULL_WATCH_REQUIRED"],
            }
            for name in CreativePerceptualMediaQC.required_gates
        }
        project_id = str(
            authority.get("project_id")
            or (authority.get("exact_target") or {}).get("project_id")
        )
        package_id = str(
            authority.get("package_artifact_version_id")
            or (authority.get("exact_target") or {}).get("package_artifact_version_id")
        )
        review_round = int(repair.get("review_round") or 1)
        body = {
            "plan_id": f"mr1-native-render-plan:{run_id}:review-round-{review_round}",
            "plan_version": review_round,
            "package_id": package_id,
            "video_project_id": project_id,
            "company_id": f"company-bound-to-project:{project_id}",
            "channel_id": str(
                (authority.get("destination") or {}).get("channel_handle")
                or "@SmallTeamAI"
            ),
            "channel_profile_version_id": _binding_ref(
                profile, f"channel-profile-version://{profile.get('id', 'missing')}"
            ),
            "effective_context_snapshot_id": _binding_ref(
                effective, f"effective-context://package/{package_id}"
            ),
            "effective_context_hash": _binding_hash(
                effective, str(authority.get("package_content_hash") or "")
            ),
            "format_identity_contract_ref": visual_direction[0],
            "format_identity_contract_hash": visual_direction[1],
            "format_identity_status": "APPROVED",
            "episode_originality_manifest_ref": originality[0],
            "episode_originality_manifest_hash": originality[1],
            "final_originality_gate": "PASS",
            "claim_evidence_ledger_refs": [claim[0]] if claim else [script_ref],
            "script_ref": script_ref,
            "script_hash": script_hash,
            "srt_ref": caption_metrics["caption_compilation_ref"],
            "srt_hash": caption_metrics["caption_compilation_hash"],
            "audio_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
            "temporal_authority_mode": "CANONICAL_STRICT",
            "canonical_media_timeline_ref": f"canonical-timeline:{timeline.timeline_hash}",
            "canonical_media_timeline_hash": timeline.timeline_hash,
            "canonical_audio_asset_ref": timeline.audio_asset_id,
            "canonical_caption_compilation_ref": caption_metrics[
                "caption_compilation_ref"
            ],
            "canonical_caption_compilation_hash": caption_metrics[
                "caption_compilation_hash"
            ],
            "scene_timing_source": "CANONICAL_MEDIA_TIMELINE",
            "caption_timing_source": "CANONICAL_MEDIA_TIMELINE",
            "parallel_timing_inputs": [],
            "visual_plan_ref": visual_plan_ref,
            "visual_plan_hash": visual_plan_hash,
            "visual_direction_contract_ref": visual_direction[0],
            "visual_direction_contract_hash": visual_direction[1],
            "creative_gate_results": creative,
            "canvas_spec": CanvasSpec(width=1920, height=1080, fps=30),
            "scenes": scenes,
            "global_motion_policy": {
                "motion_pack": "NativeMotionPack_v1",
                "raw_filter_injection_allowed": False,
                "review_round": review_round,
                "human_repair_directive_hash": repair.get("directive_hash"),
                "repair_classes": list(repair.get("repair_classes") or []),
                "repair_profile": deepcopy(repair.get("repair_profile") or {}),
            },
            "caption_policy": {
                "authority": "CANONICAL_MEDIA_TIMELINE",
                "compilation_hash": caption_metrics["caption_compilation_hash"],
                "human_repair_directive_hash": repair.get("directive_hash"),
                "repair_profile": deepcopy(repair.get("repair_profile") or {}),
            },
            "audio_policy": {
                "narration_asset_ref": str(audio_path),
                "canonical_audio_asset_ref": timeline.audio_asset_id,
                "narration_asset_hash": audio_hash,
                "output_sample_rate": 48000,
                "output_channels": 2,
            },
            "output_profiles": [OUTPUT_PROFILE],
            "character_policy_mode": "NO_CHARACTER",
            "purpose": MR1_RENDER_PURPOSE,
            "production_eligible": True,
            "status": "APPROVED",
            "created_at": datetime(2026, 7, 19, tzinfo=UTC),
            "created_by": (
                f"MR1_EXACT_APPROVAL:{authority.get('approval_id')}:"
                f"REVIEW_ROUND:{review_round}"
            ),
        }
        if not body["effective_context_hash"]:
            raise ValueError("MR1_EFFECTIVE_CONTEXT_HASH_REQUIRED")
        plan = NativeRenderPlan(**body)
        plan.content_hash = canonical_plan_hash(plan)
        return plan

    @staticmethod
    def _production_envelope(
        *,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        plan: NativeRenderPlan,
    ) -> ProductionRenderExecutionEnvelope:
        provider_ref, _ = _artifact_ref(authority, "provider_execution_plan")
        cost_ref, _ = _artifact_ref(authority, "cost_estimate_snapshot")
        approval_ref = str(authority.get("approval_ref") or "")
        if not approval_ref.startswith("mr1-approval://"):
            raise ValueError("MR1_SCOPED_APPROVAL_REF_REQUIRED")
        payload = {
            "envelope_version": "lpro1.production-render-envelope.v1",
            "execution_mode": "REAL_APPROVED_PRODUCTION",
            "project_ref": f"video-project://{authority.get('project_id')}",
            "package_ref": (
                f"artifact-version://{authority.get('package_artifact_version_id')}"
            ),
            "plan_ref": plan.plan_id,
            "plan_hash": plan.content_hash,
            "production_eligible": True,
            "operator_approval_ref": (
                f"operator-approval://mr1/{authority.get('approval_id')}"
            ),
            "provider_execution_plan_ref": provider_ref,
            "cost_snapshot_ref": cost_ref,
            "human_review_policy_ref": "mr1-policy://human-full-watch/required-v1",
            "archive_policy_ref": "mr1-policy://google-drive-complete-archive/required-v1",
            "mr1_scoped_approval_ref": approval_ref,
            "idempotency_key": f"mr1:{run_id}:native-ffmpeg-render:v1",
        }
        return ProductionRenderExecutionEnvelope(
            **payload, authorization_hash=stable_hash(payload)
        )

    def _build_command(
        self,
        *,
        root: Path,
        run_id: uuid.UUID,
        manifest: CompiledNativeRenderManifest,
        audio_path: Path,
        repair: Mapping[str, Any],
    ) -> FFmpegCommandManifest:
        if (
            manifest.production_eligible is not True
            or manifest.render_purpose != MR1_RENDER_PURPOSE
            or manifest.temporal_authority_mode != "CANONICAL_STRICT"
            or not manifest.canonical_duration_ms
            or len(manifest.compiled_scenes) != 9
        ):
            raise ValueError("MR1_PRODUCTION_COMPILED_MANIFEST_REQUIRED")
        render_dir = _inside(root, root / "render")
        render_dir.mkdir(parents=True, exist_ok=True)
        review_round = int(repair.get("review_round") or 1)
        output_name = (
            "mr1-review-candidate.mp4"
            if review_round == 1
            else f"mr1-review-candidate-r{review_round}.mp4"
        )
        output = _inside(root, render_dir / output_name)
        filtergraph = _inside(root, render_dir / "filtergraph.txt")
        repair_profile = dict(repair.get("repair_profile") or {})

        asset_paths: list[Path] = []
        graph_parts: list[str] = []
        scene_probe_seconds: list[float] = []
        for index, scene in enumerate(manifest.compiled_scenes):
            refs = list(scene.get("asset_refs") or [])
            if len(refs) != 1:
                raise ValueError("MR1_ONE_NORMALIZED_ASSET_PER_SCENE_REQUIRED")
            asset = _inside(root, str(refs[0]["path"]), must_exist=True)
            if str(refs[0].get("checksum") or "") != _sha256_file(asset):
                raise ValueError("MR1_COMPILED_ASSET_CHECKSUM_MISMATCH")
            asset_paths.append(asset)
            duration = float(scene["duration_ms"]) / 1000.0
            graph_parts.append(
                f"[{index}:v]trim=start=0:duration={duration:.6f},"
                "setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=increase,"
                "crop=1920:1080,setsar=1,fps=30,format=yuv420p"
                f"[scene{index}]"
            )
            scene_probe_seconds.append(
                (float(scene["start_ms"]) + float(scene["end_ms"])) / 2000.0
            )
        concat_inputs = "".join(f"[scene{index}]" for index in range(len(asset_paths)))
        graph_parts.append(
            f"{concat_inputs}concat=n={len(asset_paths)}:v=1:a=0[scenevideo]"
        )
        graph_parts.append("[scenevideo]null[v]")
        _write_text_atomic(filtergraph, ";\n".join(graph_parts) + "\n")

        duration_seconds = manifest.canonical_duration_ms / 1000.0
        argv = [self.ffmpeg, "-hide_banner", "-nostdin", "-y"]
        for asset in asset_paths:
            argv.extend(["-i", str(asset)])
        argv.extend(
            [
                "-i",
                str(_inside(root, audio_path, must_exist=True)),
                "-filter_complex_script",
                str(filtergraph),
                "-map",
                "[v]",
                "-map",
                f"{len(asset_paths)}:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-b:v",
                "6M",
                "-maxrate",
                "8M",
                "-bufsize",
                "12M",
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-movflags",
                "+faststart",
                "-metadata",
                f"vcos_review_round={review_round}",
                "-metadata",
                "vcos_repair_directive_hash="
                + str(repair.get("directive_hash") or "NONE"),
                "-t",
                f"{duration_seconds:.6f}",
                "-shortest",
                str(output) + ".part.mp4",
            ]
        )
        version = subprocess.run(
            [self.ffmpeg, "-version"],
            capture_output=True,
            text=True,
            check=True,
            shell=False,
        ).stdout.splitlines()[0]
        expected_qc = {
            **manifest.output_specs[0],
            "expected_duration_seconds": duration_seconds,
            "max_av_drift_ms": 250,
            "black_output_check_required": True,
            "subtitle_stream_count": 0,
            "scene_coverage_required": True,
            "scene_probe_seconds": scene_probe_seconds,
            "review_round": review_round,
            "human_repair_directive_hash": repair.get("directive_hash"),
            "repair_classes": list(repair.get("repair_classes") or []),
            "repair_profile": repair_profile,
        }
        inputs = [*asset_paths, _inside(root, audio_path, must_exist=True)]
        checksums = {
            str(filtergraph): _sha256_file(filtergraph),
            **{str(path): _sha256_file(path) for path in inputs},
        }
        core = {
            "run_key": str(run_id),
            "compiled_manifest_ref": manifest.compiled_manifest_id,
            "compiled_manifest_hash": manifest.manifest_hash,
            "ffmpeg_binary_path": self.ffmpeg,
            "ffprobe_binary_path": self.ffprobe,
            "ffmpeg_version": version,
            "command_builder_version": "mr1-production-command-builder/1.0.0",
            "input_files": [str(path) for path in inputs],
            "generated_filtergraph_path": str(filtergraph),
            "generated_text_files": [],
            "generated_file_checksums": checksums,
            "output_file": str(output),
            "output_profile": OUTPUT_PROFILE,
            "sanitized_argv": argv,
            "working_directory": str(render_dir),
            "expected_qc": expected_qc,
            "temporal_authority_mode": manifest.temporal_authority_mode,
            "canonical_media_timeline_ref": manifest.canonical_media_timeline_ref,
            "canonical_media_timeline_hash": manifest.canonical_media_timeline_hash,
            "canonical_audio_asset_ref": manifest.canonical_audio_asset_ref,
            "canonical_duration_ms": manifest.canonical_duration_ms,
            "canonical_caption_compilation_ref": manifest.canonical_caption_compilation_ref,
            "canonical_caption_compilation_hash": manifest.canonical_caption_compilation_hash,
        }
        candidate = FFmpegCommandManifest(
            **core, command_hash=stable_hash(core), created_at=datetime.now(UTC)
        )
        manifest_path = render_dir / "command_manifest.json"
        command = candidate
        if manifest_path.exists():
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise FileExistsError("MR1_COMMAND_MANIFEST_PATH_CONFLICT")
            prior = FFmpegCommandManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            prior_payload = prior.model_dump(
                mode="json", exclude={"command_hash", "created_at"}
            )
            if (
                prior.command_hash != candidate.command_hash
                or prior_payload != core
                or stable_hash(prior_payload) != prior.command_hash
            ):
                raise FileExistsError("MR1_COMMAND_MANIFEST_IDENTITY_CONFLICT")
            command = prior
        else:
            _write_json_atomic(manifest_path, candidate.model_dump(mode="json"))
        script_path = render_dir / "command.sh"
        script = "#!/bin/sh\n" + shlex.join(command.sanitized_argv) + "\n"
        if script_path.exists():
            if (
                script_path.is_symlink()
                or script_path.read_text(encoding="utf-8") != script
            ):
                raise FileExistsError("MR1_COMMAND_SCRIPT_IDENTITY_CONFLICT")
        else:
            _write_text_atomic(script_path, script, executable=True)
        return command

    @staticmethod
    def _render_attempt_count(render_dir: Path) -> int:
        path = render_dir / "render-attempt.json"
        if not path.is_file() or path.is_symlink():
            return 0
        try:
            return int(
                json.loads(path.read_text(encoding="utf-8")).get("attempt_count") or 0
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise FileExistsError("MR1_RENDER_ATTEMPT_JOURNAL_INVALID") from exc

    def _load_completed_render(
        self,
        *,
        root: Path,
        manifest: CompiledNativeRenderManifest,
        command: FFmpegCommandManifest,
    ) -> tuple[NativeRenderExecutionReceipt, MediaQCReport] | None:
        output = _inside(root, command.output_file)
        render_dir = _inside(root, command.working_directory)
        receipt_path = render_dir / "execution_receipt.json"
        qc_path = render_dir / "media_qc.json"
        present = (output.exists(), receipt_path.exists(), qc_path.exists())
        if not any(present):
            return None
        if not receipt_path.exists():
            # Recoverable local crash before the typed completion receipt.
            return None
        if present != (True, True, True):
            raise FileExistsError("MR1_RENDER_COMPLETION_SET_INCOMPLETE")
        if any(
            path.is_symlink() or not path.is_file()
            for path in (output, receipt_path, qc_path)
        ):
            raise FileExistsError("MR1_RENDER_COMPLETION_SET_INVALID")
        if Path(str(output) + ".part.mp4").exists():
            raise FileExistsError("MR1_RENDER_PARTIAL_OUTPUT_REMAINS")
        receipt = NativeRenderExecutionReceipt.model_validate_json(
            receipt_path.read_text(encoding="utf-8")
        )
        qc = MediaQCReport.model_validate_json(qc_path.read_text(encoding="utf-8"))
        checksum = _sha256_file(output)
        if (
            receipt.run_key != command.run_key
            or receipt.command_hash != command.command_hash
            or receipt.manifest_refs
            != {
                "compiled_manifest": manifest.compiled_manifest_id,
                "compiled_manifest_hash": manifest.manifest_hash,
            }
            or Path(receipt.output_path).resolve() != output
            or receipt.output_checksum != checksum
            or receipt.exit_code != 0
            or receipt.local_only is not False
            or receipt.production_eligible is not True
            or receipt.no_provider_calls_confirmed is not True
            or qc.run_key != command.run_key
            or qc.result != "PASS"
            or qc.checks.get("checksum_sha256") != checksum
        ):
            raise FileExistsError("MR1_RENDER_COMPLETION_BINDING_MISMATCH")
        fresh_qc = NativeMediaQC(self.ffprobe, self.ffmpeg).inspect(
            output, command.expected_qc, command.run_key
        )
        if (
            fresh_qc.result != "PASS"
            or fresh_qc.checks.get("checksum_sha256") != checksum
        ):
            raise FileExistsError("MR1_RENDER_COMPLETION_ACTUAL_BYTE_QC_FAILED")
        return receipt, qc

    @staticmethod
    def _technical_qc(
        *,
        run_id: uuid.UUID,
        native_qc: MediaQCReport,
        receipt: NativeRenderExecutionReceipt,
        normalization: MediaNormalizationManifest,
    ) -> dict[str, Any]:
        base = TechnicalMediaQC().from_native_media_qc(
            run_id=str(run_id), native_report=native_qc
        )
        normalized_items_pass = bool(normalization.items) and all(
            item.state == "PASS"
            and item.byte_probe.get("actual_bytes_probed") is not False
            and item.normalized_checksum
            for item in normalization.items
        )
        reason_codes = list(base.reason_codes)
        if not normalized_items_pass:
            reason_codes.append("TECHNICAL_ASSET_DECODE_INTEGRITY_FAILED")
        if native_qc.checks.get("black_output_absent") is not True:
            reason_codes.append("TECHNICAL_BLACK_EMPTY_FRAME_RISK")
        if native_qc.checks.get("subtitle_stream_count") != 0:
            reason_codes.append("TECHNICAL_SUBTITLE_STREAM_PRESENT")
        if native_qc.checks.get("timeline_coverage") is not True:
            reason_codes.append("TECHNICAL_SCENE_COVERAGE_FAILED")
        if native_qc.checks.get("checksum_sha256") != receipt.output_checksum:
            reason_codes.append("TECHNICAL_OUTPUT_CHECKSUM_MISMATCH")
        payload = {
            "schema_version": "mr1.technical-media-qc.v1",
            "run_id": str(run_id),
            "result": "FAIL" if reason_codes else "PASS",
            "actual_mp4_bytes_probed": True,
            "full_decode_performed": native_qc.checks.get("full_decode") is True,
            "checks": {
                **deepcopy(base.checks),
                "black_empty_frame_risk": native_qc.checks.get("black_output_absent")
                is True,
                "no_subtitle_stream": native_qc.checks.get("subtitle_stream_count")
                == 0,
                "scene_coverage": native_qc.checks.get("timeline_coverage") is True,
                "asset_decode_integrity": normalized_items_pass,
                "pixel_format": native_qc.checks.get("pixel_format"),
                "aspect_ratio": "16:9"
                if (
                    native_qc.checks.get("width"),
                    native_qc.checks.get("height"),
                )
                == (1920, 1080)
                else "INVALID",
            },
            "native_media_qc": native_qc.model_dump(mode="json"),
            "normalization_manifest_hash": normalization.content_hash,
            "output_sha256": receipt.output_checksum,
            "reason_codes": sorted(set(reason_codes)),
            "production_eligible": True,
            "not_publishable": True,
            "human_full_watch_still_required": True,
        }
        return {**payload, "content_hash": stable_hash(payload)}

    def _creative_qc(
        self,
        *,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        timeline: CanonicalMediaTimeline,
        assets: list[ResolvedMediaAsset],
        output_path: Path,
    ) -> dict[str, Any]:
        routes = {item.scene_id: item.actual_route.value for item in assets}
        output_sha256 = _sha256_file(output_path)
        output_probe = _probe(output_path, self.ffprobe)
        output_video = _video_stream(output_probe)
        output_audio = _audio_stream(output_probe)
        scene_frames = {
            item.segment_id: _frame_fingerprint(
                output_path,
                seconds=(item.scene_start_ms + item.scene_end_ms) / 2000.0,
                ffmpeg=self.ffmpeg,
            )
            for item in timeline.segments
        }
        visual_manifest_path = (
            output_path.parents[1] / "assets" / "scene-visual-execution-manifest.json"
        )
        if not visual_manifest_path.is_file() or visual_manifest_path.is_symlink():
            raise RuntimeError("MR1_SCENE_VISUAL_EXECUTION_EVIDENCE_MISSING")
        visual_manifest = json.loads(visual_manifest_path.read_text(encoding="utf-8"))
        measured_metrics = {
            "actual_review_mp4_ref": str(output_path),
            "actual_review_mp4_sha256": output_sha256,
            "actual_review_mp4_size_bytes": output_path.stat().st_size,
            "actual_review_mp4_duration_ms": _duration_ms(output_probe),
            "decoded_video": {
                "codec": output_video.get("codec_name"),
                "width": output_video.get("width"),
                "height": output_video.get("height"),
                "fps": output_video.get("avg_frame_rate"),
                "pixel_format": output_video.get("pix_fmt"),
            },
            "decoded_audio": {
                "codec": output_audio.get("codec_name"),
                "sample_rate": output_audio.get("sample_rate"),
                "channels": output_audio.get("channels"),
            },
            "decoded_scene_frame_evidence": scene_frames,
            "decoded_scene_frame_count": len(scene_frames),
            "scene_visual_execution_manifest_ref": str(visual_manifest_path),
            "scene_visual_execution_manifest_hash": visual_manifest.get("content_hash"),
            "canonical_timeline_hash": timeline.timeline_hash,
            "scene_routes": routes,
            "market": "US",
            "locale": "en-US",
            "niche": "Small Team AI",
            "technical_measurement_complete": True,
        }
        gates: list[CreativeGateEvidence] = []
        for name in CreativePerceptualMediaQC.required_gates:
            payload = {
                "gate_name": name,
                "result": "REVIEW_REQUIRED",
                "reason_codes": ["MR1_UNINTERRUPTED_HUMAN_FULL_WATCH_REQUIRED"],
                "metrics": {
                    **deepcopy(measured_metrics),
                    "stock_appropriateness": (
                        "MEASURED_SUBWINDOWS_BOUND_HUMAN_JUDGMENT_PENDING"
                        if name in {"SceneSemanticMatchGate", "AssetAdjacencyGate"}
                        else "HUMAN_JUDGMENT_PENDING"
                    ),
                    "generated_image_quality": "NOT_REQUIRED_ZERO_CALLS",
                    "veo_motion_value": "NOT_REQUIRED_ZERO_CALLS",
                    "native_diagram_clarity": "ACTUAL_FRAMES_MEASURED_HUMAN_JUDGMENT_PENDING",
                    "caption_readability": "ACTUAL_FRAME_PRESENCE_MEASURED_HUMAN_JUDGMENT_PENDING",
                    "voice_pacing": "TIMING_BOUND_HUMAN_WATCH_PENDING",
                    "voice_caption_sync": "CANONICAL_TIMELINE_BOUND",
                    "transitions": "MULTI_STATE_NATIVE_TRANSITIONS_RENDERED_HUMAN_JUDGMENT_PENDING",
                    "overall_watchability": "HUMAN_FULL_WATCH_PENDING",
                },
                "evidence_refs": [
                    str(output_path),
                    f"canonical-timeline:{timeline.timeline_hash}",
                    str(visual_manifest_path),
                    str(authority.get("approval_ref") or ""),
                ],
            }
            gates.append(
                CreativeGateEvidence(**payload, content_hash=stable_hash(payload))
            )
        base = CreativePerceptualMediaQC().aggregate(
            run_id=str(run_id), gate_results=gates
        )
        if base.result != "REVIEW_REQUIRED":
            raise RuntimeError("MR1_CREATIVE_HUMAN_REVIEW_BOUNDARY_INVALID")
        payload = {
            "schema_version": "mr1.creative-perceptual-media-qc.v1",
            "run_id": str(run_id),
            "result": "REVIEW_REQUIRED",
            "gate_results": [item.model_dump(mode="json") for item in gates],
            "required_gates": list(CreativePerceptualMediaQC.required_gates),
            "missing_gates": [],
            "reason_codes": list(base.reason_codes),
            "technical_media_qc_implies_creative_pass": False,
            "automated_result_is_final_creative_authority": False,
            "human_full_watch_required": True,
            "production_eligible": True,
            "not_publishable": True,
        }
        return {**payload, "content_hash": stable_hash(payload)}

    def _thumbnail(
        self,
        *,
        root: Path,
        output: Path,
        duration_ms: int,
    ) -> Path:
        output = _inside(root, output, must_exist=True)
        thumbnail = _inside(root, root / "review" / "mr1-review-thumbnail.png")
        if thumbnail.is_file() and not thumbnail.is_symlink():
            video = _video_stream(_probe(thumbnail, self.ffprobe))
            if (int(video.get("width") or 0), int(video.get("height") or 0)) != (
                1920,
                1080,
            ):
                raise FileExistsError("MR1_REVIEW_THUMBNAIL_IDENTITY_CONFLICT")
            return thumbnail
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        part = thumbnail.with_name(thumbnail.stem + ".part.png")
        part.unlink(missing_ok=True)
        seconds = min(max(duration_ms / 2000.0, 0.5), 10.0)
        _run(
            [
                self.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-ss",
                f"{seconds:.6f}",
                "-i",
                str(output),
                "-frames:v",
                "1",
                "-threads",
                "1",
                "-update",
                "1",
                str(part),
            ],
            "MR1_REVIEW_THUMBNAIL_FAILED",
        )
        os.replace(part, thumbnail)
        video = _video_stream(_probe(thumbnail, self.ffprobe))
        if (int(video.get("width") or 0), int(video.get("height") or 0)) != (
            1920,
            1080,
        ):
            raise RuntimeError("MR1_REVIEW_THUMBNAIL_RESOLUTION_INVALID")
        return thumbnail

    @staticmethod
    def _candidate(
        *,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        plan: NativeRenderPlan,
        receipt: NativeRenderExecutionReceipt,
        native_qc: MediaQCReport,
        technical: Mapping[str, Any],
        creative: Mapping[str, Any],
        provenance: Mapping[str, Any],
        captions_path: Path,
        thumbnail_path: Path,
        timeline: CanonicalMediaTimeline,
        repair: Mapping[str, Any],
    ) -> dict[str, Any]:
        project_id = str(
            authority.get("project_id")
            or (authority.get("exact_target") or {}).get("project_id")
        )
        package_id = str(
            authority.get("package_artifact_version_id")
            or (authority.get("exact_target") or {}).get("package_artifact_version_id")
        )
        review_round = int(repair.get("review_round") or 1)
        candidate_authority = deepcopy(
            authority.get("candidate_authority_bindings") or {}
        )
        authority_hash = candidate_authority.pop("content_hash", None)
        if candidate_authority.get(
            "schema_version"
        ) != "mr1.candidate-authority-bindings.v1" or authority_hash != stable_hash(
            candidate_authority
        ):
            raise RuntimeError("MR1_CANDIDATE_AUTHORITY_BINDINGS_INVALID")
        candidate_authority["content_hash"] = authority_hash
        provenance_core = {
            key: deepcopy(value)
            for key, value in provenance.items()
            if key != "content_hash"
        }
        provenance_hash = provenance.get("content_hash")
        provenance_path = (
            Path(receipt.output_path).parents[1]
            / "assets"
            / "asset-provenance-manifest.json"
        )
        provenance_file_sha256 = (
            _sha256_file(provenance_path)
            if provenance_path.is_file() and not provenance_path.is_symlink()
            else None
        )
        resolved = authority.get("resolved") or {}
        rights = (resolved.get("rights_disclosure_completeness_report") or {}).get(
            "content"
        ) or {}
        disclosure = (
            resolved.get("synthetic_media_disclosure_receipt_draft") or {}
        ).get("content") or {}
        provenance_plan = (resolved.get("asset_provenance_plan") or {}).get(
            "content"
        ) or {}
        lineage_checks = {
            "package_version_exact": bool(
                package_id
                == str(
                    (candidate_authority.get("package") or {}).get(
                        "artifact_version_id"
                    )
                )
                and str(authority.get("package_content_hash") or "")
                == (candidate_authority.get("package") or {}).get("content_hash")
            ),
            "approval_exact": bool(
                str(authority.get("approval_id") or "")
                == (candidate_authority.get("approval") or {}).get(
                    "approval_decision_id"
                )
                and str(authority.get("approval_content_hash") or "")
                == (candidate_authority.get("approval") or {}).get(
                    "approval_content_hash"
                )
            ),
            "profile_snapshot_exact": bool(
                candidate_authority.get("channel_profile_version")
                == (authority.get("exact_bindings") or {}).get(
                    "channel_profile_version"
                )
                and candidate_authority.get("compiled_channel_policy_snapshot")
                == (authority.get("exact_bindings") or {}).get(
                    "compiled_channel_policy_snapshot"
                )
            ),
            "rights_planning_authority_exact": bool(
                (
                    candidate_authority.get("rights_disclosure_completeness_report")
                    or {}
                ).get("content_hash")
                == (resolved.get("rights_disclosure_completeness_report") or {}).get(
                    "content_hash"
                )
                and rights.get("planning_state") == "PASS"
                and rights.get("decision") == "PASS"
                and rights.get("provider_outputs_claimed") is False
                and rights.get("generated_evidence_authority") is False
            ),
            "synthetic_disclosure_authority_exact": bool(
                (
                    candidate_authority.get("synthetic_media_disclosure_receipt_draft")
                    or {}
                ).get("content_hash")
                == (resolved.get("synthetic_media_disclosure_receipt_draft") or {}).get(
                    "content_hash"
                )
                and disclosure.get("receipt_status") == "PRE_RENDER_PLANNED"
                and disclosure.get("provider_outputs_exist") is False
                and disclosure.get("synthetic_voice_planned") is True
                and disclosure.get("synthetic_image_planned") is False
                and disclosure.get("synthetic_video_planned") is False
            ),
            "provenance_plan_authority_exact": bool(
                (candidate_authority.get("asset_provenance_plan") or {}).get(
                    "content_hash"
                )
                == (resolved.get("asset_provenance_plan") or {}).get("content_hash")
                and provenance_plan.get("provider_output_exists") is False
                and provenance_plan.get("generated_evidence_authority") is False
            ),
            "actual_provenance_manifest_exact": bool(
                provenance_hash == stable_hash(provenance_core)
                and provenance.get("timeline_hash") == timeline.timeline_hash
                and provenance.get("scene_count") == len(ALL_SCENES)
                and len(provenance.get("items") or []) == len(ALL_SCENES)
                and provenance.get("rights_complete") is True
                and provenance.get("provider_substitution_used") is False
                and provenance.get("automatic_fallback_used") is False
                and provenance_path.is_file()
                and not provenance_path.is_symlink()
            ),
        }
        package_lineage_valid = all(
            lineage_checks[key]
            for key in (
                "package_version_exact",
                "approval_exact",
                "profile_snapshot_exact",
            )
        )
        provenance_complete = bool(
            lineage_checks["provenance_plan_authority_exact"]
            and lineage_checks["actual_provenance_manifest_exact"]
        )
        rights_disclosure_resolved = bool(
            lineage_checks["rights_planning_authority_exact"]
            and lineage_checks["synthetic_disclosure_authority_exact"]
            and provenance_complete
        )
        if not (
            package_lineage_valid and provenance_complete and rights_disclosure_resolved
        ):
            failed = sorted(key for key, value in lineage_checks.items() if not value)
            raise RuntimeError(
                "MR1_CANDIDATE_LINEAGE_DERIVATION_FAILED:" + ",".join(failed)
            )
        typed_payload = {
            "candidate_id": f"mr1-review-candidate:{run_id}:review-round-{review_round}",
            "project_ref": f"video-project://{project_id}",
            "package_ref": f"artifact-version://{package_id}",
            "plan_ref": plan.plan_id,
            "output_file_ref": receipt.output_path,
            "output_sha256": receipt.output_checksum,
            "technical_media_qc_ref": str(
                Path(receipt.output_path).parents[1] / "qc" / "technical-media-qc.json"
            ),
            "technical_media_qc_hash": str(technical["content_hash"]),
            "creative_media_qc_ref": str(
                Path(receipt.output_path).parents[1]
                / "qc"
                / "creative-perceptual-media-qc.json"
            ),
            "creative_media_qc_hash": str(creative["content_hash"]),
            "production_eligible": True,
            "not_publishable": True,
            "human_review_status": "PENDING",
        }
        typed = ReviewMediaCandidate(
            **typed_payload, content_hash=stable_hash(typed_payload)
        )
        payload = {
            **typed.model_dump(mode="json", exclude={"content_hash"}),
            "schema_version": "mr1.review-media-candidate.v1",
            "run_id": str(run_id),
            "project_id": project_id,
            "package_artifact_version_id": package_id,
            "package_content_hash": str(authority.get("package_content_hash") or ""),
            "approval_id": str(authority.get("approval_id") or ""),
            "approval_content_hash": str(authority.get("approval_content_hash") or ""),
            "approval_ref": str(authority.get("approval_ref") or ""),
            "plan_hash": plan.content_hash,
            "canonical_timeline_hash": timeline.timeline_hash,
            "duration_seconds": float(native_qc.checks.get("duration") or 0),
            "thumbnail_path": str(thumbnail_path),
            "captions_path": str(captions_path),
            "technical_qc_result": "PASS",
            "technical_media_qc": "PASS",
            "creative_review_result": str(creative["result"]),
            "creative_media_qc": str(creative["result"]),
            "candidate_authority_bindings": candidate_authority,
            "candidate_authority_bindings_hash": authority_hash,
            "asset_provenance_manifest_ref": str(provenance_path),
            "asset_provenance_manifest_hash": provenance_hash,
            "asset_provenance_manifest_file_sha256": provenance_file_sha256,
            "lineage_derivation_checks": lineage_checks,
            "package_lineage_valid": package_lineage_valid,
            "legacy_incomplete_package": not package_lineage_valid,
            "provenance_complete": provenance_complete,
            "rights_disclosure_resolved": rights_disclosure_resolved,
            "archive_required": True,
            "archive_status": "PENDING",
            "final_media_ref_created": False,
            "youtube_upload_authorized": False,
            "upload_ready": False,
            "publish_execution_ready": False,
            "review_round": review_round,
            "human_repair_directive_hash": repair.get("directive_hash"),
            "human_repair_classes": list(repair.get("repair_classes") or []),
            "repaired_from_output_sha256": repair.get("rejected_output_sha256"),
            "provider_outputs_reused": bool(repair.get("active")),
            "provider_calls_repeated_for_repair": False,
        }
        return {**payload, "content_hash": stable_hash(payload)}

    @staticmethod
    def _strict_package(
        *,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        normalized: SpokenTextNormalized,
        alignment: Mapping[str, Any],
        timeline: CanonicalMediaTimeline,
        normalization: MediaNormalizationManifest,
        plan: NativeRenderPlan,
        manifest: CompiledNativeRenderManifest,
        receipt: NativeRenderExecutionReceipt,
        technical: Mapping[str, Any],
        creative: Mapping[str, Any],
        candidate: Mapping[str, Any],
        provenance: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "mr1.strict-production-render-package.v1",
            "run_id": str(run_id),
            "execution_mode": MR1_RENDER_PURPOSE,
            "approval": {
                "id": authority.get("approval_id"),
                "content_hash": authority.get("approval_content_hash"),
                "ref": authority.get("approval_ref"),
            },
            "exact_target": deepcopy(authority.get("exact_target") or {}),
            "package_artifact_version_id": authority.get("package_artifact_version_id"),
            "package_content_hash": authority.get("package_content_hash"),
            "exact_bindings": deepcopy(authority.get("exact_bindings") or {}),
            "market_and_destination": deepcopy(authority.get("destination") or {}),
            "provider_execution_plan": deepcopy(
                (authority.get("resolved") or {}).get("provider_execution_plan") or {}
            ),
            "provider_attempt_scope": deepcopy(
                authority.get("provider_attempt_scope") or {}
            ),
            "cost_scope": deepcopy(authority.get("cost_scope") or {}),
            "spoken_text_hash": normalized.spoken_text_hash,
            "verified_alignment_hash": alignment.get("content_hash"),
            "verified_alignment_status": alignment.get("verification_status"),
            "canonical_timeline_hash": timeline.timeline_hash,
            "normalization_manifest_hash": normalization.content_hash,
            "native_render_plan_hash": plan.content_hash,
            "compiled_motion_manifest_hash": manifest.manifest_hash,
            "ffmpeg_receipt_hash": receipt.receipt_hash,
            "output_sha256": receipt.output_checksum,
            "technical_media_qc_hash": technical.get("content_hash"),
            "creative_media_qc_hash": creative.get("content_hash"),
            "review_media_candidate_hash": candidate.get("content_hash"),
            "asset_provenance_hash": provenance.get("content_hash"),
            "production_eligible": True,
            "not_publishable": True,
            "human_review_status": "PENDING",
            "final_media_ref": None,
            "youtube_calls": 0,
        }
        return {**payload, "content_hash": stable_hash(payload)}

    @staticmethod
    def _write_archive_scoped_reports(
        *,
        root: Path,
        run_id: uuid.UUID,
        authority: Mapping[str, Any],
        provider_outputs: Mapping[str, Any],
        timeline: CanonicalMediaTimeline,
        normalization: MediaNormalizationManifest,
        plan: NativeRenderPlan,
        receipt: NativeRenderExecutionReceipt,
        technical: Mapping[str, Any],
        creative: Mapping[str, Any],
        candidate: Mapping[str, Any],
        render_attempts: int,
    ) -> None:
        report_dir = root / "reports"
        visual_routes = resolve_mr1_visual_route_authority(authority)
        repair_journal = root / "local_repair_journal.json"
        repairs: list[dict[str, Any]] = []
        if repair_journal.is_file() and not repair_journal.is_symlink():
            raw_repairs = json.loads(repair_journal.read_text(encoding="utf-8"))
            if isinstance(raw_repairs, list):
                repairs = raw_repairs
        repair_payload = {
            "schema_version": "mr1.repair-cycles.v1",
            "run_id": str(run_id),
            "repair_cycles": repairs,
            "repair_cycle_count": len(repairs),
            "provider_calls_repeated": False,
            "render_attempts": render_attempts,
        }
        _write_json_atomic(report_dir / "mr1_repair_cycles.json", repair_payload)

        pexels_outputs = [
            MR1LocalProductionContinuation._pexels_output(provider_outputs, scene_id)
            for scene_id in visual_routes.pexels_scenes
        ]
        summary_payload = {
            "schema_version": "mr1.archive-scoped-summary.v1",
            "run_id": str(run_id),
            "state": "READY_FOR_ARCHIVE",
            "approval_id": authority.get("approval_id"),
            "approval_content_hash": authority.get("approval_content_hash"),
            "approval_ref": authority.get("approval_ref"),
            "exact_target": deepcopy(authority.get("exact_target") or {}),
            "canonical_timeline": {
                "result": "PASS",
                "content_hash": timeline.timeline_hash,
                "duration_ms": timeline.audio_duration_ms,
                "scene_count": len(timeline.segments),
                "estimated_timing_fallback_used": False,
            },
            "provider_call_counts": {
                "elevenlabs_narration": 1,
                "forced_alignment": 1,
                "pexels_scene_flows": len([item for item in pexels_outputs if item]),
                "google_gemini_image": 0,
                "google_veo": 0,
                "youtube": 0,
            },
            "routes": dict(visual_routes.routes),
            "media_normalization": {
                "result": normalization.result,
                "content_hash": normalization.content_hash,
                "item_count": len(normalization.items),
                "actual_bytes_probed": True,
                "minimum_effective_resolution": "1080p",
            },
            "native_render_plan": {
                "content_hash": plan.content_hash,
                "production_eligible": plan.production_eligible,
                "profile": OUTPUT_PROFILE,
            },
            "render": {
                "attempt_count": render_attempts,
                "exit_status": receipt.exit_code,
                "output_file_ref": receipt.output_path,
                "output_sha256": receipt.output_checksum,
                "output_size_bytes": Path(receipt.output_path).stat().st_size,
            },
            "technical_media_qc": {
                "result": technical.get("result"),
                "content_hash": technical.get("content_hash"),
            },
            "creative_media_qc": {
                "result": creative.get("result"),
                "content_hash": creative.get("content_hash"),
            },
            "review_media_candidate": {
                "candidate_id": candidate.get("candidate_id"),
                "content_hash": candidate.get("content_hash"),
                "human_review_status": "PENDING",
                "production_eligible": True,
                "not_publishable": True,
            },
            "drive_archive": "PENDING_UPLOAD_AND_REMOTE_VERIFICATION",
            "final_media_ref": "NOT_CREATED",
            "destination_status": "PENDING_PLATFORM_ID",
            "upload_ready": False,
            "publish_execution_ready": False,
            "proceed_to_pub1": False,
        }
        summary = {**summary_payload, "content_hash": stable_hash(summary_payload)}
        _write_json_atomic(report_dir / "mr1_summary.json", summary)

        report = f"""# MR1 real production — archive-scoped report

- Run ID: `{run_id}`
- Exact MR1 approval: `{authority.get("approval_ref")}`
- CanonicalMediaTimeline: **PASS** (`{timeline.timeline_hash}`)
- Exact scene routes: 6 NATIVE_DIAGRAM + 3 PEXELS_VIDEO
- Gemini image calls: **0**
- Google Veo calls: **0**
- Normalized actual-byte assets: **{len(normalization.items)}/9 PASS** at 1920x1080/30fps
- NativeRenderPlan: **PASS**, production eligible (`{plan.content_hash}`)
- NativeMotionCompiler / NativeFFmpegRenderer: **PASS**
- Render attempts: **{render_attempts}**; no provider call was repeated
- Review MP4: `{receipt.output_path}`
- Review MP4 SHA-256: `{receipt.output_checksum}`
- TechnicalMediaQC: **{technical.get("result")}**
- CreativePerceptualMediaQC: **{creative.get("result")}**
- ReviewMediaCandidate: **created**, `production_eligible=true`, `not_publishable=true`
- Google Drive archive: **PENDING** (this report is materialized before the first Drive mutation)
- Human uninterrupted full watch at 1x: **PENDING**
- FinalMediaRef: **NOT CREATED**
- YouTube calls: **0**

The next authorized operation is the idempotent complete Drive archive upload and
remote verification. Publication and YouTube upload remain prohibited.
"""
        _write_text_atomic(report_dir / "mr1_real_production_report.md", report)

    @staticmethod
    def _archive_sources(
        root: Path, run_id: uuid.UUID
    ) -> tuple[list[dict[str, Any]], Path]:
        archive_dir = root / "archive_package"
        archive_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = archive_dir / "archive-manifest.json"
        excluded_exact = {
            root / "run_state.json",  # mutable until the service reaches Drive
            root / "local_production_result.json",
            root / "local_failure.json",
            manifest_path,
        }
        files: list[Path] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file() or path.is_symlink():
                continue
            if path in excluded_exact or ".part" in path.name:
                continue
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] == "drive":
                continue
            files.append(path)
        if not files:
            raise RuntimeError("MR1_LOCAL_ARCHIVE_PACKAGE_EMPTY")

        special_roles = {
            "reports/mr1_real_production_report.md": "MR1_REAL_PRODUCTION_REPORT",
            "reports/mr1_summary.json": "MR1_SUMMARY",
            "reports/mr1_repair_cycles.json": "MR1_REPAIR_CYCLES",
            "review/review-media-candidate.json": "MR1_REVIEW_MEDIA_CANDIDATE",
            "render/mr1-review-candidate.mp4": "MR1_FINAL_REVIEW_MP4",
            "review/mr1-review-thumbnail.png": "MR1_REVIEW_THUMBNAIL",
            "temporal/canonical-captions.srt": "MR1_CANONICAL_CAPTIONS",
            "temporal/canonical-media-timeline.json": "MR1_CANONICAL_TIMELINE",
            "temporal/verified-narration-alignment.json": "MR1_VERIFIED_ALIGNMENT",
            "assets/media-normalization-manifest.json": "MR1_MEDIA_NORMALIZATION_MANIFEST",
            "assets/asset-provenance-manifest.json": "MR1_ASSET_PROVENANCE_MANIFEST",
            "render/native-render-plan.json": "MR1_NATIVE_RENDER_PLAN",
            "render/compiled-native-render-manifest.json": "MR1_NATIVE_MOTION_MANIFEST",
            "render/command_manifest.json": "MR1_FFMPEG_COMMAND_MANIFEST",
            "render/execution_receipt.json": "MR1_FFMPEG_EXECUTION_RECEIPT",
            "qc/technical-media-qc.json": "MR1_TECHNICAL_MEDIA_QC",
            "qc/creative-perceptual-media-qc.json": "MR1_CREATIVE_MEDIA_QC",
            "archive_package/strict-production-render-package.json": "MR1_STRICT_PRODUCTION_PACKAGE",
        }
        items: list[dict[str, Any]] = []
        roles: set[str] = set()
        names: set[str] = set()
        archive_paths: set[str] = set()
        for ordinal, path in enumerate(files, start=1):
            relative = path.relative_to(root).as_posix()
            fragment = re.sub(r"[^A-Za-z0-9]+", "_", relative).strip("_").upper()
            role = special_roles.get(relative)
            if role is None and re.fullmatch(
                r"render/mr1-review-candidate-r\d+\.mp4", relative
            ):
                role = "MR1_FINAL_REVIEW_MP4"
            role = role or f"MR1_FILE_{ordinal:03d}_{fragment}"
            if role.casefold() in roles:
                raise RuntimeError("MR1_LOCAL_ARCHIVE_DUPLICATE_ROLE")
            flattened = re.sub(r"[^A-Za-z0-9._-]+", "_", relative.replace("/", "__"))
            name = f"{ordinal:03d}-{flattened}"
            archive_path = f"items/{ordinal:03d}-{role.lower()}/{name}"
            sha256, md5 = _digest_file(path)
            item = {
                "logical_role": role,
                "name": name,
                "source_path": str(path.resolve()),
                "archive_path": archive_path,
                "size_bytes": path.stat().st_size,
                "sha256": sha256,
                "md5": md5,
            }
            if (
                name.casefold() in names
                or archive_path.casefold() in archive_paths
                or not item["size_bytes"]
            ):
                raise RuntimeError("MR1_LOCAL_ARCHIVE_ITEM_IDENTITY_CONFLICT")
            roles.add(role.casefold())
            names.add(name.casefold())
            archive_paths.add(archive_path.casefold())
            items.append(item)

        manifest_payload = {
            "schema_version": "mr1.local-archive-manifest.v1",
            "run_id": str(run_id),
            "archive_identity": f"mr1-archive://small-team-ai/{run_id}",
            "item_count_excluding_this_manifest": len(items),
            "items": deepcopy(items),
            "exact_item_set": True,
            "unique_logical_roles": True,
            "unique_names": True,
            "unique_archive_paths": True,
            "all_sources_inside_run_workspace": True,
            "drive_status": "PENDING_FIRST_MUTATION",
        }
        manifest = {**manifest_payload, "content_hash": stable_hash(manifest_payload)}
        _write_json_atomic(manifest_path, manifest)
        ordinal = len(items) + 1
        manifest_name = f"{ordinal:03d}-archive_package__archive-manifest.json"
        manifest_role = "MR1_LOCAL_ARCHIVE_MANIFEST"
        manifest_archive_path = (
            f"items/{ordinal:03d}-{manifest_role.lower()}/{manifest_name}"
        )
        sha256, md5 = _digest_file(manifest_path)
        items.append(
            {
                "logical_role": manifest_role,
                "name": manifest_name,
                "source_path": str(manifest_path.resolve()),
                "archive_path": manifest_archive_path,
                "size_bytes": manifest_path.stat().st_size,
                "sha256": sha256,
                "md5": md5,
            }
        )
        return items, manifest_path

    @staticmethod
    def _local_failure(
        *,
        root: Path,
        run_id: uuid.UUID,
        stage: str,
        prior_stage: str,
        exc: Exception,
    ) -> dict[str, Any]:
        reason = f"{type(exc).__name__}:{exc}"[:8000]
        resume_by_stage = {
            "PROVIDER_OUTPUT_VALIDATION": "PROVIDER_EXECUTION_COMPLETE",
            "VERIFIED_NARRATION_ALIGNMENT": "NARRATION_READY",
            "CANONICAL_MEDIA_TIMELINE": "ALIGNMENT_READY",
            "MEDIA_NORMALIZATION": "CANONICAL_TIMELINE_READY",
            "NATIVE_RENDER_PLAN": "ASSETS_READY",
            "NATIVE_MOTION_COMPILER": "NATIVE_RENDER_PLAN_READY",
            "NATIVE_FFMPEG_RENDER": "NATIVE_RENDER_PLAN_READY",
            "TECHNICAL_MEDIA_QC": "RENDERED_AWAITING_TECHNICAL_QC",
            "CREATIVE_PERCEPTUAL_MEDIA_QC": "TECHNICAL_QC_PASSED",
            "REVIEW_MEDIA_CANDIDATE": "CREATIVE_REVIEW_REQUIRED",
            "LOCAL_ARCHIVE_PACKAGE": "REVIEW_MEDIA_CANDIDATE_CREATED",
        }
        repair_path = root / "local_repair_journal.json"
        repairs: list[dict[str, Any]] = []
        if repair_path.is_file() and not repair_path.is_symlink():
            raw = json.loads(repair_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                repairs = raw
        repairs.append(
            {
                "cycle": len(repairs) + 1,
                "stage": stage,
                "classification": "DETERMINISTIC_LOCAL_OR_INPUT_INTEGRITY",
                "reason": reason,
                "provider_calls_repeated": False,
                "resume_from": resume_by_stage.get(stage, prior_stage),
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        )
        _write_json_atomic(repair_path, repairs)
        failure = {
            "schema_version": "mr1.local-production-failure.v1",
            "state": "REPAIRABLE_LOCAL_FAILURE",
            "run_id": str(run_id),
            "stage": stage,
            "failed_stage": stage,
            "classification": "DETERMINISTIC_LOCAL_OR_INPUT_INTEGRITY",
            "reason": reason,
            "reason_codes": [str(exc).split(":", 1)[0] or type(exc).__name__],
            "resume_from": resume_by_stage.get(stage, prior_stage),
            "provider_outputs_durable": True,
            "provider_calls_repeated": False,
            "provider_recall_authorized": False,
            "youtube_calls": 0,
            "repair_cycle_count": len(repairs),
        }
        _write_json_atomic(root / "local_failure.json", failure)
        return failure

    @staticmethod
    def _validate_completed_result(
        root: Path, result: Mapping[str, Any], run_id: uuid.UUID
    ) -> None:
        if (
            result.get("state") != "READY_FOR_ARCHIVE"
            or result.get("run_id") != str(run_id)
            or result.get("provider_calls_repeated") is not False
            or result.get("local_provider_calls") != 0
            or result.get("youtube_calls") != 0
            or result.get("final_media_ref") is not None
        ):
            raise ValueError("MR1_LOCAL_COMPLETION_IDENTITY_INVALID")
        candidate = result.get("review_media_candidate") or {}
        output = _inside(
            root, str(candidate.get("output_file_ref") or ""), must_exist=True
        )
        if (
            candidate.get("production_eligible") is not True
            or candidate.get("not_publishable") is not True
            or candidate.get("human_review_status") != "PENDING"
            or candidate.get("technical_qc_result") != "PASS"
            or candidate.get("creative_review_result")
            not in {"PASS", "REVIEW_REQUIRED"}
            or candidate.get("output_sha256") != _sha256_file(output)
            or candidate.get("content_hash")
            != stable_hash(
                {
                    key: value
                    for key, value in candidate.items()
                    if key != "content_hash"
                }
            )
        ):
            raise ValueError("MR1_LOCAL_REVIEW_CANDIDATE_INVALID")
        sources = result.get("archive_sources")
        if not isinstance(sources, list) or not sources:
            raise ValueError("MR1_LOCAL_ARCHIVE_SOURCES_REQUIRED")
        roles: set[str] = set()
        names: set[str] = set()
        paths: set[str] = set()
        required_fields = {
            "logical_role",
            "name",
            "source_path",
            "archive_path",
            "size_bytes",
            "sha256",
            "md5",
        }
        for item in sources:
            if not isinstance(item, Mapping) or set(item) != required_fields:
                raise ValueError("MR1_LOCAL_ARCHIVE_SOURCE_FIELDS_INVALID")
            path = _inside(root, str(item["source_path"]), must_exist=True)
            sha256, md5 = _digest_file(path)
            if (
                path.stat().st_size != int(item["size_bytes"])
                or sha256 != item["sha256"]
                or md5 != item["md5"]
            ):
                raise ValueError("MR1_LOCAL_ARCHIVE_SOURCE_EVIDENCE_CHANGED")
            role = str(item["logical_role"]).casefold()
            name = str(item["name"]).casefold()
            archive_path = str(item["archive_path"]).casefold()
            if role in roles or name in names or archive_path in paths:
                raise ValueError("MR1_LOCAL_ARCHIVE_DUPLICATE_IDENTITY")
            if Path(str(item["archive_path"])).name != item["name"]:
                raise ValueError("MR1_LOCAL_ARCHIVE_NAME_PATH_MISMATCH")
            roles.add(role)
            names.add(name)
            paths.add(archive_path)
        for required_role in (
            "mr1_real_production_report",
            "mr1_summary",
            "mr1_repair_cycles",
            "mr1_final_review_mp4",
            "mr1_technical_media_qc",
            "mr1_creative_media_qc",
            "mr1_asset_provenance_manifest",
            "mr1_local_archive_manifest",
        ):
            if required_role not in roles:
                raise ValueError(
                    f"MR1_LOCAL_ARCHIVE_REQUIRED_ROLE_MISSING:{required_role}"
                )
