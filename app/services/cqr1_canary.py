from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.creative_quality_canary import (
    CQR1_PURPOSE,
    CQR1_RUN_ID,
    CQR1CanaryApprovalScope,
    CQR1OfflineQualificationEvidence,
    CQR1PaidCanaryPreflightResult,
    CQR1ProviderCallLedgerEntry,
    CQR1ProviderReadinessEvidence,
)
from app.core.config import Settings, get_settings
from app.services.native_render_plan import stable_hash


CQR1_CANARY_SCRIPT = (
    "An approved script begins as the single source of meaning. VCOS prepares one complete narration, "
    "then verifies every spoken word against the final audio.\n\n"
    "Those verified word timings build the media timeline. Captions inherit the same tokens and timing, "
    "while each scene follows the narration instead of a guessed duration.\n\n"
    "Native graphics explain the workflow. A grounded stock shot adds context, and one restrained hero "
    "scene marks the transition.\n\n"
    "Finally, the renderer assembles every layer, checks synchronization and continuity, and archives the "
    "review package. This is a non-production canary."
)
CQR1_VISIBLE_LABEL = "VCOS CQR1 NON-PRODUCTION CANARY"

CQR1_OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "pexels_search": {"provider": "pexels_api", "operation": "bounded_search", "paid": False},
    "pexels_download": {"provider": "pexels_api", "operation": "selected_mp4_download", "paid": False},
    "elevenlabs_tts": {"provider": "elevenlabs", "operation": "convert_with_timestamps", "paid": True},
    "elevenlabs_forced_alignment": {"provider": "elevenlabs", "operation": "forced_alignment", "paid": True},
    "google_veo_submit": {"provider": "google_veo", "operation": "hero_generation_submit", "paid": True},
    "google_veo_output": {"provider": "google_veo", "operation": "single_generation_output", "paid": False},
    "drive_archive": {"provider": "google_drive", "operation": "verified_archive", "paid": False},
}


def _secret_configured(value: Any) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    raw = getter() if callable(getter) else value
    return bool(str(raw).strip())


def current_static_provider_readiness(
    *,
    settings: Settings | None = None,
    drive_oauth_connected: bool = False,
    elevenlabs_tts_access_confirmed: bool = False,
    elevenlabs_voices_read_confirmed: bool = False,
    elevenlabs_models_read_confirmed: bool = False,
    google_veo_model_accessible: bool = False,
    provider_probe_count: int = 0,
) -> CQR1ProviderReadinessEvidence:
    """Return booleans/safe metadata only. This function has no transport."""

    settings = settings or get_settings()
    permission = settings.elevenlabs_forced_alignment_permission_confirmed
    return CQR1ProviderReadinessEvidence(
        pexels_api_key_configured=_secret_configured(settings.pexels_api_key),
        elevenlabs_api_key_configured=_secret_configured(settings.elevenlabs_api_key),
        elevenlabs_voice_id_configured=bool(settings.elevenlabs_voice_id),
        elevenlabs_model_id_configured=bool(settings.elevenlabs_model_id),
        elevenlabs_tts_access_confirmed=elevenlabs_tts_access_confirmed,
        elevenlabs_voices_read_confirmed=elevenlabs_voices_read_confirmed,
        elevenlabs_models_read_confirmed=elevenlabs_models_read_confirmed,
        elevenlabs_forced_alignment_permission_confirmed=permission if permission is not None else "unknown",
        google_veo_api_key_configured=_secret_configured(settings.gemini_api_key),
        google_veo_model_accessible=google_veo_model_accessible,
        drive_oauth_connected=drive_oauth_connected,
        drive_archive_root_configured=bool(settings.google_drive_root_folder_id),
        secret_values_exposed=False,
        provider_probe_count=provider_probe_count,
    )


class CQR1CanaryCallLedger:
    """Atomic, one-shot ledger. It contains hashes and safe evidence only."""

    def __init__(self, path: Path, entries: Mapping[str, CQR1ProviderCallLedgerEntry] | None = None):
        self.path = path
        self.entries = dict(entries or {})

    @classmethod
    def create(cls, path: Path, *, approval: CQR1CanaryApprovalScope) -> "CQR1CanaryCallLedger":
        if path.is_file():
            # Never reset an attempt ledger by re-running preparation.
            return cls.load(path)
        ledger = cls(path)
        for key, spec in CQR1_OPERATION_SPECS.items():
            idempotency_material = {
                "run_id": approval.run_id,
                "purpose": approval.purpose,
                "approval_ref": approval.approval_ref,
                "provider": spec["provider"],
                "operation": spec["operation"],
            }
            ledger.entries[key] = CQR1ProviderCallLedgerEntry(
                operation_key=key,
                provider=spec["provider"],
                operation=spec["operation"],
                paid=spec["paid"],
                idempotency_key_hash=hashlib.sha256(stable_hash(idempotency_material).encode()).hexdigest(),
            )
        ledger.persist()
        return ledger

    @classmethod
    def load(cls, path: Path) -> "CQR1CanaryCallLedger":
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = {
            key: CQR1ProviderCallLedgerEntry.model_validate(value)
            for key, value in dict(payload.get("entries") or {}).items()
        }
        expected_hash = stable_hash({key: value.model_dump(mode="json") for key, value in sorted(entries.items())})
        if payload.get("ledger_hash") != expected_hash:
            raise ValueError("CQR1_LEDGER_HASH_MISMATCH")
        return cls(path, entries)

    @property
    def provider_call_count(self) -> int:
        return sum(1 for entry in self.entries.values() if entry.provider_call_made)

    @property
    def fresh(self) -> bool:
        return set(self.entries) == set(CQR1_OPERATION_SPECS) and all(
            entry.status == "PLANNED" and entry.attempt_count == 0 and not entry.provider_call_made
            for entry in self.entries.values()
        )

    def begin_once(self, operation_key: str) -> None:
        entry = self._entry(operation_key)
        if entry.attempt_count >= entry.max_attempts or entry.status != "PLANNED":
            raise RuntimeError("CQR1_PROVIDER_ATTEMPT_LIMIT_EXCEEDED")
        entry.status = "EXECUTING"
        entry.attempt_count = 1
        self.persist()

    def finish(
        self,
        operation_key: str,
        *,
        status: str,
        provider_call_made: bool,
        output_count: int = 0,
        safe_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        entry = self._entry(operation_key)
        entry.status = status  # type: ignore[assignment]
        entry.provider_call_made = provider_call_made
        entry.output_count = output_count
        entry.safe_evidence = dict(safe_evidence or {})
        self.persist()

    def persist(self) -> None:
        serialized = {key: value.model_dump(mode="json") for key, value in sorted(self.entries.items())}
        payload = {"run_id": CQR1_RUN_ID, "purpose": CQR1_PURPOSE, "entries": serialized, "ledger_hash": stable_hash(serialized)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_name(self.path.name + ".part")
        try:
            part.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(part, self.path)
        finally:
            part.unlink(missing_ok=True)

    def _entry(self, operation_key: str) -> CQR1ProviderCallLedgerEntry:
        if operation_key not in CQR1_OPERATION_SPECS or operation_key not in self.entries:
            raise ValueError("CQR1_EXTERNAL_PROVIDER_OR_OPERATION_FORBIDDEN")
        return self.entries[operation_key]


class CQR1PaidCanaryEntryGate:
    def evaluate(
        self,
        *,
        offline: CQR1OfflineQualificationEvidence,
        readiness: CQR1ProviderReadinessEvidence,
        approval: CQR1CanaryApprovalScope,
        ledger: CQR1CanaryCallLedger,
        estimated_cost_usd: Decimal = Decimal("0"),
    ) -> CQR1PaidCanaryPreflightResult:
        blockers: list[str] = []
        offline_payload = offline.model_dump()
        blockers.extend(
            f"OFFLINE_GATE_FAILED_{name.upper()}"
            for name, passed in offline_payload.items()
            if not passed
        )
        readiness_payload = readiness.model_dump(exclude={"secret_values_exposed", "provider_probe_count"})
        blockers.extend(
            f"PROVIDER_READINESS_FAILED_{name.upper()}"
            for name, passed in readiness_payload.items()
            if passed is not True
        )
        if readiness.elevenlabs_forced_alignment_permission_confirmed is not True and readiness.provider_probe_count:
            blockers.append("PROVIDER_PROBE_BEFORE_FORCED_ALIGNMENT_CONFIRMATION")
        if not ledger.fresh:
            blockers.append("CQR1_LEDGER_NOT_FRESH")
        if approval.run_id not in approval.approval_ref:
            blockers.append("CQR1_APPROVAL_NOT_BOUND_TO_RUN")
        if estimated_cost_usd < 0 or estimated_cost_usd > approval.total_hard_cost_cap_usd:
            blockers.append("CQR1_COST_CAP_EXCEEDED")
        if approval.automatic_provider_retry or approval.external_provider_fallback or approval.youtube_allowed:
            blockers.append("CQR1_APPROVAL_SCOPE_UNSAFE")
        blockers = sorted(set(blockers))
        passed = not blockers
        if readiness.elevenlabs_forced_alignment_permission_confirmed is not True:
            next_action = (
                "Grant ElevenLabs Text to Speech Access, Voices Read, Models Read, and Forced Alignment Access; "
                "configure ELEVENLABS_VOICE_ID/ELEVENLABS_MODEL_ID; then set "
                "ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED=true before any provider probe."
            )
        elif not offline.all_passed:
            next_action = "Complete all CQR1-B/C offline, golden, negative, regression, and repository gates."
        elif not readiness.all_passed:
            next_action = "Complete bounded provider readiness verification and retain only safe booleans/metadata."
        elif not ledger.fresh:
            next_action = "Use a fresh run ID and a new all-PLANNED zero-attempt ledger; no retry is authorized."
        else:
            next_action = "Execute only the approved one-shot CQR1 provider operations through the guarded ledger."
        payload = {
            "run_id": CQR1_RUN_ID,
            "status": "PASS" if passed else "BLOCKED",
            "blocker_reason_codes": blockers,
            "exact_next_action": next_action,
            "offline_gate_passed": offline.all_passed,
            "provider_readiness_passed": readiness.all_passed,
            "ledger_fresh": ledger.fresh,
            "provider_call_count": ledger.provider_call_count,
            "provider_execution_allowed": passed,
            "production_eligible": False,
            "not_publishable": True,
        }
        return CQR1PaidCanaryPreflightResult(**payload, content_hash=stable_hash(payload))


class CQR1CanaryExecutionGuard:
    def __init__(self, ledger: CQR1CanaryCallLedger):
        self.ledger = ledger

    def run_once(
        self,
        operation_key: str,
        *,
        preflight: CQR1PaidCanaryPreflightResult,
        operation: Callable[[], Mapping[str, Any]],
    ) -> dict[str, Any]:
        if preflight.status != "PASS" or not preflight.provider_execution_allowed:
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["CQR1_PAID_CANARY_PREFLIGHT_BLOCKED"],
            }
        if operation_key not in CQR1_OPERATION_SPECS:
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["CQR1_EXTERNAL_PROVIDER_OR_OPERATION_FORBIDDEN"],
            }
        entry = self.ledger.entries[operation_key]
        if entry.attempt_count >= entry.max_attempts or entry.status != "PLANNED":
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["CQR1_PROVIDER_ATTEMPT_LIMIT_EXCEEDED"],
            }
        self.ledger.begin_once(operation_key)
        try:
            safe_evidence = _safe_evidence(dict(operation()))
            output_count = int(safe_evidence.get("output_count", 0) or 0)
            if output_count > 1:
                raise RuntimeError("CQR1_PROVIDER_OUTPUT_LIMIT_EXCEEDED")
        except Exception as exc:
            self.ledger.finish(
                operation_key,
                status="FAILED",
                provider_call_made=True,
                safe_evidence={"error_type": type(exc).__name__, "error_message_redacted": True},
            )
            raise
        self.ledger.finish(
            operation_key,
            status="SUCCEEDED",
            provider_call_made=True,
            output_count=output_count,
            safe_evidence=safe_evidence,
        )
        return {"status": "SUCCEEDED", "provider_call_made": True, "safe_evidence": safe_evidence}


def run_cqr1d_offline_rehearsal(
    workspace_root: Path,
    *,
    offline_evidence: CQR1OfflineQualificationEvidence,
    settings: Settings | None = None,
    readiness_overrides: Mapping[str, Any] | None = None,
    human_watchability_policy: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist an offline-only CQR1-D packet without accepting an operation callback."""

    root = workspace_root.resolve()
    manifests = root / "manifests"
    qc = root / "qc"
    manifests.mkdir(parents=True, exist_ok=True)
    qc.mkdir(parents=True, exist_ok=True)
    approval = CQR1CanaryApprovalScope(
        approval_ref=f"operator-prompt://{CQR1_RUN_ID}",
        total_hard_cost_cap_usd=Decimal("3.00"),
    )
    ledger = CQR1CanaryCallLedger.create(
        manifests / "planned_provider_call_ledger.json",
        approval=approval,
    )
    readiness_payload = current_static_provider_readiness(settings=settings).model_dump()
    readiness_payload.update(dict(readiness_overrides or {}))
    readiness = CQR1ProviderReadinessEvidence.model_validate(readiness_payload)
    preflight = CQR1PaidCanaryEntryGate().evaluate(
        offline=offline_evidence,
        readiness=readiness,
        approval=approval,
        ledger=ledger,
        estimated_cost_usd=Decimal("0"),
    )
    from app.services.creative_media_qc import HumanWatchabilityPacketBuilder

    human_packet = HumanWatchabilityPacketBuilder().build(
        run_id=CQR1_RUN_ID,
        final_mp4_path=str(root / "render/final/cqr1-non-production-canary.mp4"),
        contact_sheet_path=str(root / "render/proxy/cqr1-contact-sheet.jpg"),
        before_after_packet_ref=str(manifests / "before_after_comparison.json"),
        policy=human_watchability_policy,
        drive_archive_receipt_ref=None,
    )
    _write_json_atomic(manifests / "approval_scope.json", approval.model_dump(mode="json"))
    _write_json_atomic(manifests / "offline_qualification_evidence.json", offline_evidence.model_dump(mode="json"))
    _write_json_atomic(manifests / "provider_readiness_safe.json", readiness.model_dump(mode="json"))
    _write_json_atomic(manifests / "paid_canary_preflight.json", preflight.model_dump(mode="json"))
    _write_json_atomic(qc / "human_watchability_review_packet.json", human_packet.model_dump(mode="json"))
    summary_payload = {
        "run_id": CQR1_RUN_ID,
        "rehearsal": "CQR1D_OFFLINE_GUARDRAIL_ONLY",
        "preflight_status": preflight.status,
        "provider_execution_allowed": preflight.provider_execution_allowed,
        "provider_call_count": ledger.provider_call_count,
        "provider_operation_executed": False,
        "ledger_attempt_counts": {
            key: entry.attempt_count for key, entry in sorted(ledger.entries.items())
        },
        "human_watchability_review": human_packet.review_state,
        "production_eligible": False,
        "not_publishable": True,
    }
    summary = {**summary_payload, "content_hash": stable_hash(summary_payload)}
    _write_json_atomic(root / "cqr1d_offline_summary.json", summary)
    return summary


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        part.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
        os.replace(part, path)
    finally:
        part.unlink(missing_ok=True)


def _safe_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    forbidden_fragments = (
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "raw_url",
        "download_url",
        "idempotency_key",
    )

    def visit(value: Any) -> Any:
        if isinstance(value, Mapping):
            safe: dict[str, Any] = {}
            for raw_key, nested in value.items():
                key = str(raw_key)
                if any(fragment in key.casefold() for fragment in forbidden_fragments):
                    raise ValueError("CQR1_UNSAFE_EVIDENCE_REJECTED")
                safe[key] = visit(nested)
            return safe
        if isinstance(value, list):
            return [visit(item) for item in value]
        if isinstance(value, tuple):
            return [visit(item) for item in value]
        return value

    return visit(payload)
