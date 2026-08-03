from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from app.contracts.vcos_v2 import DurationContractV2
from app.contracts.creative_quality_canary import (
    HUMAN_CRITICAL_REASON_CODES,
    HUMAN_WATCHABILITY_DIMENSIONS,
    CreativeGateEvidence,
    CreativePerceptualMediaQCReport,
    FinalDurationConsistencyResult,
    FinalDurationEvidence,
    HumanWatchabilityReviewPacket,
    PendingHumanDimension,
    TechnicalMediaQCReport,
)
from app.contracts.native_renderer import MediaQCReport as NativeMediaQCReport
from app.services.native_render_plan import stable_hash


REQUIRED_CREATIVE_MEDIA_QC_GATES = (
    "NarrationPacingGate",
    "CaptionCompilationGate",
    "SubtitleSidecarGate",
    "CaptionAudioSyncGate",
    "CaptionCoverageGate",
    "TimelineDriftGate",
    "SceneSemanticMatchGate",
    "VisualContinuityGate",
    "AssetAdjacencyGate",
    "FinalDurationConsistencyGate",
)

REQUIRED_TECHNICAL_MEDIA_QC_CHECKS = (
    "decode",
    "codec_container",
    "stream_integrity",
    "dimensions",
    "fps",
    "audio_format",
    "duration",
    "fast_start",
    "checksum",
)


def _reason_fragment(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _passed(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.upper() == "PASS"
    if isinstance(value, Mapping):
        state = value.get("state", value.get("result", value.get("status")))
        return isinstance(state, str) and state.upper() == "PASS"
    return False


class FinalDurationConsistencyGate:
    """Compares every final duration projection against the canonical timeline."""

    def __init__(self, policy: Mapping[str, Any]):
        self.pass_max_ms = int(policy["pass_max"])
        self.review_max_ms = int(policy["review_max"])
        block_above = int(policy["block_above"])
        if not 0 <= self.pass_max_ms <= self.review_max_ms <= block_above:
            raise ValueError("FINAL_DURATION_POLICY_INVALID")
        if block_above != self.review_max_ms:
            raise ValueError("FINAL_DURATION_POLICY_GAP_INVALID")

    def evaluate(self, evidence: FinalDurationEvidence) -> FinalDurationConsistencyResult:
        canonical = evidence.canonical_timeline_duration_ms
        measured = {
            "final_narration_duration_ms": evidence.final_narration_duration_ms,
            "final_mp4_duration_ms": evidence.final_mp4_duration_ms,
            "final_caption_end_ms": evidence.final_caption_end_ms,
            "final_scene_end_ms": evidence.final_scene_end_ms,
        }
        deltas = {name: abs(value - canonical) for name, value in measured.items()}
        max_delta = max(deltas.values())
        if max_delta <= self.pass_max_ms:
            result = "PASS"
            reasons: list[str] = []
        elif max_delta <= self.review_max_ms:
            result = "REVIEW_REQUIRED"
            reasons = ["FINAL_DURATION_DELTA_REVIEW"]
        else:
            result = "BLOCK"
            reasons = ["FINAL_DURATION_DELTA_BLOCK"]
        metrics = {
            **evidence.model_dump(),
            "deltas_from_canonical_ms": deltas,
            "max_abs_delta_ms": max_delta,
            "pass_max_ms": self.pass_max_ms,
            "review_max_ms": self.review_max_ms,
        }
        payload = {
            "gate_name": "FinalDurationConsistencyGate",
            "result": result,
            "reason_codes": reasons,
            "metrics": metrics,
            "evidence_refs": [],
        }
        return FinalDurationConsistencyResult(**payload, content_hash=stable_hash(payload))


class TechnicalMediaQC:
    """Technical-only aggregation. It deliberately makes no creative judgment."""

    def evaluate(
        self,
        *,
        run_id: str,
        checks: Mapping[str, Any],
        required_checks: Iterable[str] = REQUIRED_TECHNICAL_MEDIA_QC_CHECKS,
        duration_contract: DurationContractV2 | None = None,
        measured_duration_ms: int | None = None,
    ) -> TechnicalMediaQCReport:
        required = list(dict.fromkeys(required_checks))
        missing = [name for name in required if name not in checks]
        failures = [name for name in required if name in checks and not _passed(checks[name])]
        # Archive completeness belongs to the technical layer when supplied, but
        # is not required before the first archive attempt (avoids a circular gate).
        if "archive_completeness" in checks and not _passed(checks["archive_completeness"]):
            failures.append("archive_completeness")
        reasons = [f"TECHNICAL_CHECK_MISSING_{_reason_fragment(name)}" for name in missing]
        reasons.extend(f"TECHNICAL_CHECK_FAILED_{_reason_fragment(name)}" for name in failures)
        payload = {
            "run_id": run_id,
            "result": "FAIL" if reasons else "PASS",
            "checks": dict(checks),
            "required_checks": required,
            "reason_codes": sorted(set(reasons)),
            "duration_contract": (
                duration_contract.model_dump(mode="json")
                if duration_contract is not None
                else None
            ),
            "measured_duration_ms": measured_duration_ms,
            "production_eligible": False,
            "not_publishable": True,
        }
        canonical_payload = {
            key: value for key, value in payload.items() if value is not None
        }
        return TechnicalMediaQCReport(
            **canonical_payload,
            content_hash=stable_hash(canonical_payload),
        )

    def from_native_media_qc(
        self,
        *,
        run_id: str,
        native_report: NativeMediaQCReport,
    ) -> TechnicalMediaQCReport:
        """Adapt measured native probe/decode evidence to the CQR1 technical contract."""

        native = native_report.checks
        checksum = native.get("checksum_sha256")
        checksum_valid = bool(
            isinstance(checksum, str)
            and re.fullmatch(r"[0-9a-f]{64}", checksum)
        )
        checks: dict[str, Any] = {
            "decode": native.get("full_decode") is True,
            "codec_container": native.get("codec_container_matches_expected") is True,
            "stream_integrity": (
                native_report.result == "PASS"
                and native.get("stream_integrity") is True
                and native.get("av_drift_within_limit") is True
            ),
            "dimensions": native.get("dimensions_match_expected") is True,
            "fps": native.get("fps_matches_expected") is True,
            "audio_format": native.get("audio_format_matches_expected") is True,
            "duration": native.get("duration_matches_expected") is True,
            "fast_start": native.get("fast_start") is True,
            "checksum": checksum_valid,
            "native_media_qc_evidence": {
                "result": native_report.result,
                "reason_codes": sorted(set(native_report.reason_codes)),
                "duration_seconds": native.get("duration"),
                "av_drift_ms": native.get("av_drift_ms"),
                "max_av_drift_ms": native.get("max_av_drift_ms"),
                "checksum_sha256": checksum if checksum_valid else None,
                "timeline_coverage": None,
            },
        }
        return self.evaluate(run_id=run_id, checks=checks)


class CreativePerceptualMediaQC:
    """Aggregate creative gates without inheriting a technical PASS."""

    required_gates = REQUIRED_CREATIVE_MEDIA_QC_GATES

    def aggregate(
        self,
        *,
        run_id: str,
        gate_results: Iterable[CreativeGateEvidence | Mapping[str, Any]],
    ) -> CreativePerceptualMediaQCReport:
        normalized: list[CreativeGateEvidence] = []
        invalid_reasons: list[str] = []
        for raw in gate_results:
            if isinstance(raw, CreativeGateEvidence):
                normalized.append(raw)
                continue
            gate_name = str(raw.get("gate_name", raw.get("gate", "")))
            decision = str(raw.get("result", raw.get("verdict", raw.get("gate_status", "")))).upper()
            decision = {"FAIL": "BLOCK", "BLOCKED": "BLOCK", "WARN": "REVIEW_REQUIRED"}.get(decision, decision)
            if decision not in {"PASS", "REVIEW_REQUIRED", "BLOCK"}:
                decision = "BLOCK"
                invalid_reasons.append(f"CREATIVE_GATE_RESULT_INVALID_{_reason_fragment(gate_name or 'UNKNOWN')}")
            body = {
                "gate_name": gate_name or "UNKNOWN_GATE",
                "result": decision,
                "reason_codes": list(raw.get("reason_codes") or []),
                "metrics": dict(raw.get("metrics") or {}),
                "evidence_refs": list(raw.get("evidence_refs") or []),
            }
            normalized.append(CreativeGateEvidence(**body, content_hash=str(raw.get("content_hash") or stable_hash(body))))

        by_name: dict[str, CreativeGateEvidence] = {}
        duplicates: set[str] = set()
        for item in normalized:
            if item.gate_name in by_name:
                duplicates.add(item.gate_name)
            by_name[item.gate_name] = item
        missing = sorted(set(self.required_gates) - set(by_name))
        reasons = list(invalid_reasons)
        reasons.extend(f"CREATIVE_GATE_MISSING_{_reason_fragment(name)}" for name in missing)
        reasons.extend(f"CREATIVE_GATE_DUPLICATE_{_reason_fragment(name)}" for name in sorted(duplicates))
        for item in normalized:
            if item.result == "BLOCK":
                reasons.extend(item.reason_codes or [f"{_reason_fragment(item.gate_name)}_BLOCK"])
        if reasons or any(item.result == "BLOCK" for item in normalized):
            result = "BLOCK"
        elif any(item.result == "REVIEW_REQUIRED" for item in normalized):
            result = "REVIEW_REQUIRED"
            for item in normalized:
                if item.result == "REVIEW_REQUIRED":
                    reasons.extend(item.reason_codes or [f"{_reason_fragment(item.gate_name)}_REVIEW_REQUIRED"])
        else:
            result = "PASS"
        ordered = sorted(normalized, key=lambda item: item.gate_name)
        payload = {
            "run_id": run_id,
            "result": result,
            "gate_results": [item.model_dump(mode="json") for item in ordered],
            "required_gates": list(self.required_gates),
            "missing_gates": missing,
            "reason_codes": sorted(set(reasons)),
            "technical_media_qc_implies_creative_pass": False,
            "production_eligible": False,
            "not_publishable": True,
        }
        return CreativePerceptualMediaQCReport(**payload, content_hash=stable_hash(payload))


class HumanWatchabilityPacketBuilder:
    """Builds a blank operator packet; it cannot produce a human PASS."""

    def build(
        self,
        *,
        run_id: str,
        final_mp4_path: str,
        contact_sheet_path: str,
        before_after_packet_ref: str,
        policy: Mapping[str, Any],
        drive_archive_receipt_ref: str | None = None,
    ) -> HumanWatchabilityReviewPacket:
        payload = {
            "run_id": run_id,
            "review_state": "PENDING",
            "final_mp4_path": final_mp4_path,
            "contact_sheet_path": contact_sheet_path,
            "before_after_packet_ref": before_after_packet_ref,
            "drive_archive_receipt_ref": drive_archive_receipt_ref,
            "dimensions": [
                PendingHumanDimension(dimension=name).model_dump(mode="json")
                for name in HUMAN_WATCHABILITY_DIMENSIONS
            ],
            "timestamped_issues": [],
            "critical_reason_code_checklist": {name: False for name in HUMAN_CRITICAL_REASON_CODES},
            "uninterrupted_full_watch_1x_completed": False,
            "optional_flagged_spot_check_speed": float(policy["optional_flagged_spot_check_speed"]),
            "pass_total_minimum": int(policy["pass_total_min"]),
            "pass_dimension_minimum": int(policy["pass_dimension_min"]),
            "repair_total_range": tuple(policy["repair_total_range"]),
            "critical_issue_overrides_average": True,
            "no_publish_statement": (
                "VCOS CQR1 non-production canary; no YouTube write, FinalMediaRef, "
                "HumanUploadTask, UploadedVideo, production promotion, or auto-publish is authorized."
            ),
            "production_eligible": False,
            "not_publishable": True,
        }
        return HumanWatchabilityReviewPacket(**payload, content_hash=stable_hash(payload))
