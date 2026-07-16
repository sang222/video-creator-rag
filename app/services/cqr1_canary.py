from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.creative_quality_canary import (
    CQR1_ALIGNMENT_REUSE_RUN_IDS,
    CQR1_PAID_CANARY_002_RUN_ID,
    CQR1_PAID_CANARY_004_RUN_ID,
    CQR1_PAID_CANARY_007_RUN_ID,
    CQR1_PURPOSE,
    CQR1_RUN_ID,
    CQR1_TTS_REUSE_RUN_IDS,
    CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS,
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

CQR1_RUN007_VISUAL_REUSE_PINS: dict[str, Any] = {
    "source_ledger_hash": "bbb8bd46c7a977bd60ff1aaf3dff8e25560d0262884a3bcf50bfc4c40b4889b2",
    "query_plan_hash": "2b1d2fd02e3318c1ff86e2a8fe77bba3891406a0d52b02b2405404a3ccc1327c",
    "search_provenance_hashes": frozenset(
        {"0f1b052f17729e3700e1365e3a030105f182aea1aaf1a89080c2d5950741ff67"}
    ),
    "visual_direction_hash": "48b29bf5f6769d4bad3306b24f1c462a1cdd5959f7d65f58f01e2fd2d8246315",
    "selected_provider_asset_id": "12991847",
    "pexels_asset_sha256": "9a12085cdb448a4a6238fae40d6fee450ceaada242bddaf5187517f7da8c8d08",
    "pexels_download_receipt_hashes": frozenset(
        {
            "254382b91362c8a0867b65efdbcf1e893451f6737e0af07e28ab5906ec78794c",
            "4bf8e6d1af7f3c7111104763eb11f3e01218502ba4d406f9d399868cc88fbcad",
        }
    ),
    "pexels_representative_still_sha256": (
        "f28b2f98d3d1e642146681ee4f7e43bf72d3edf855d31d42680e9a44a95a444a"
    ),
    "visual_review_content_hash": "6a426352546a43fd598a79adc9801f47a6bb2532f1ce321c612d0ca6d88471e9",
    "veo_operation_id": "models/veo-3.1-fast-generate-preview/operations/f1h6lf0kcws1",
    "veo_request_hash": "e668313e8a3139a5c28ad45ca940f0b813284eb00ff36894e05f62b8fd5705d1",
    "veo_prompt_hash": "3a2b6892b5cb4626b3e8f3b0746f8d4e84f2ad8fc7c866c2d109c05c9c9b6fee",
    "veo_operation_receipt_hashes": frozenset(
        {
            "31bb4a6292775966689291a036295375d028009dd4b0335736241cc5f8026a19",
            "71d178aeb121f367c827560a3a4b85be0524ff63a2178098ae2fa4d6b4d6ca51",
        }
    ),
    "veo_output_sha256": "5821d6cd1799b34fe5ce097d51ebef172fdd12519e96d84bde65343d9e54d027",
    "veo_output_provenance_hashes": frozenset(
        {
            "08c558f66d6e742f723035d45fbd028aa1c578ecb806a2039ae92897acb4b083",
            "a9bf11639deb110dc0d81ccc0d76889c561127b4549b0e29ef25869dde1878b3",
        }
    ),
    "veo_representative_still_sha256": (
        "05c367bf6012e35d00c8b50f1d79a8c8a0fe443c6195122ab799df89768735b4"
    ),
}

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

    def __init__(
        self,
        path: Path,
        entries: Mapping[str, CQR1ProviderCallLedgerEntry] | None = None,
        *,
        run_id: str = CQR1_RUN_ID,
        purpose: str = CQR1_PURPOSE,
        approval_ref: str = "",
    ):
        self.path = path
        self.entries = dict(entries or {})
        self.run_id = run_id
        self.purpose = purpose
        self.approval_ref = approval_ref

    @classmethod
    def create(cls, path: Path, *, approval: CQR1CanaryApprovalScope) -> "CQR1CanaryCallLedger":
        if path.is_file():
            # Never reset an attempt ledger by re-running preparation.
            existing = cls.load(path)
            if (
                existing.run_id != approval.run_id
                or existing.purpose != approval.purpose
                or existing.approval_ref != approval.approval_ref
            ):
                raise ValueError("CQR1_LEDGER_SCOPE_MISMATCH")
            return existing
        ledger = cls(
            path,
            run_id=approval.run_id,
            purpose=approval.purpose,
            approval_ref=approval.approval_ref,
        )
        maximum_attempts = {
            "pexels_search": approval.maximum_pexels_search_flows,
            "pexels_download": approval.maximum_pexels_downloads,
            "elevenlabs_tts": approval.maximum_elevenlabs_tts_generations,
            "elevenlabs_forced_alignment": approval.maximum_elevenlabs_forced_alignment_calls,
            "google_veo_submit": approval.maximum_google_veo_submits,
            "google_veo_output": approval.maximum_google_veo_outputs,
            "drive_archive": approval.maximum_drive_archive_attempts,
        }
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
                max_attempts=maximum_attempts[key],
                idempotency_key_hash=hashlib.sha256(stable_hash(idempotency_material).encode()).hexdigest(),
            )
        ledger.persist()
        return ledger

    @classmethod
    def load(cls, path: Path) -> "CQR1CanaryCallLedger":
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_id = str(payload.get("run_id") or "")
        purpose = str(payload.get("purpose") or "")
        approval_ref = str(payload.get("approval_ref") or "")
        if not run_id or purpose != CQR1_PURPOSE:
            raise ValueError("CQR1_LEDGER_SCOPE_INVALID")
        entries = {
            key: CQR1ProviderCallLedgerEntry.model_validate(value)
            for key, value in dict(payload.get("entries") or {}).items()
        }
        serialized = {
            key: value.model_dump(mode="json")
            for key, value in sorted(entries.items())
        }
        if payload.get("ledger_hash_version") == "cqr1-ledger-v2":
            if not approval_ref:
                raise ValueError("CQR1_LEDGER_APPROVAL_REF_MISSING")
            expected_hash = stable_hash(
                {
                    "run_id": run_id,
                    "purpose": purpose,
                    "approval_ref": approval_ref,
                    "entries": serialized,
                }
            )
        else:
            expected_hash = stable_hash(serialized)
        if payload.get("ledger_hash") != expected_hash:
            raise ValueError("CQR1_LEDGER_HASH_MISMATCH")
        return cls(
            path,
            entries,
            run_id=run_id,
            purpose=purpose,
            approval_ref=approval_ref,
        )

    @property
    def provider_call_count(self) -> int:
        return sum(1 for entry in self.entries.values() if entry.provider_call_made)

    @property
    def fresh(self) -> bool:
        return set(self.entries) == set(CQR1_OPERATION_SPECS) and all(
            entry.status == "PLANNED"
            and entry.max_attempts == 1
            and entry.attempt_count == 0
            and not entry.provider_call_made
            for entry in self.entries.values()
        )

    def preflight_ready(self, approval: CQR1CanaryApprovalScope) -> bool:
        """True only for a fresh ledger or the run-specific immutable imports."""

        if (
            self.run_id != approval.run_id
            or self.purpose != approval.purpose
            or self.approval_ref != approval.approval_ref
            or set(self.entries) != set(CQR1_OPERATION_SPECS)
            or self.provider_call_count != 0
        ):
            return False
        if approval.run_id not in CQR1_TTS_REUSE_RUN_IDS:
            return self.fresh
        tts = self.entries["elevenlabs_tts"]
        if not self._imported_tts_ready(tts):
            return False
        reused_keys = {"elevenlabs_tts"}
        if approval.run_id in CQR1_ALIGNMENT_REUSE_RUN_IDS:
            alignment = self.entries["elevenlabs_forced_alignment"]
            if (
                not self._imported_alignment_ready(alignment)
                or alignment.safe_evidence.get("audio_sha256")
                != tts.safe_evidence.get("audio_sha256")
                or int(alignment.safe_evidence.get("audio_duration_ms") or 0)
                != int(tts.safe_evidence.get("audio_duration_ms") or 0)
            ):
                return False
            reused_keys.add("elevenlabs_forced_alignment")
        if approval.run_id in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS:
            pexels_search = self.entries["pexels_search"]
            pexels_download = self.entries["pexels_download"]
            veo_submit = self.entries["google_veo_submit"]
            veo_output = self.entries["google_veo_output"]
            if (
                not self._imported_pexels_search_ready(pexels_search)
                or not self._imported_pexels_download_ready(pexels_download)
                or not self._imported_veo_submit_ready(veo_submit)
                or not self._imported_veo_output_ready(veo_output)
                or pexels_download.safe_evidence.get("selected_provider_asset_id")
                != pexels_search.safe_evidence.get("selected_provider_asset_id")
                or pexels_download.safe_evidence.get("query_plan_hash")
                != pexels_search.safe_evidence.get("query_plan_hash")
                or veo_output.safe_evidence.get("provider_operation_id")
                != veo_submit.safe_evidence.get("provider_operation_id")
                or veo_output.safe_evidence.get("request_hash")
                != veo_submit.safe_evidence.get("request_hash")
                or veo_output.safe_evidence.get("prompt_hash")
                != veo_submit.safe_evidence.get("prompt_hash")
                or pexels_search.safe_evidence.get("visual_direction_hash")
                != veo_submit.safe_evidence.get("visual_direction_hash")
                or pexels_download.safe_evidence.get("visual_review_content_hash")
                != veo_output.safe_evidence.get("visual_review_content_hash")
                or len(
                    {
                        pexels_search.safe_evidence.get("source_ledger_hash"),
                        pexels_download.safe_evidence.get("source_ledger_hash"),
                        veo_submit.safe_evidence.get("source_ledger_hash"),
                        veo_output.safe_evidence.get("source_ledger_hash"),
                    }
                )
                != 1
            ):
                return False
            reused_keys.update(
                {
                    "pexels_search",
                    "pexels_download",
                    "google_veo_submit",
                    "google_veo_output",
                }
            )
        downstream = [
            entry for key, entry in self.entries.items() if key not in reused_keys
        ]
        return (
            approval.maximum_elevenlabs_tts_generations == 0
            and approval.maximum_elevenlabs_forced_alignment_calls
            == (0 if approval.run_id in CQR1_ALIGNMENT_REUSE_RUN_IDS else 1)
            and approval.maximum_pexels_search_flows
            == (0 if approval.run_id in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS else 1)
            and approval.maximum_pexels_downloads
            == (0 if approval.run_id in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS else 1)
            and approval.maximum_google_veo_submits
            == (0 if approval.run_id in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS else 1)
            and approval.maximum_google_veo_outputs
            == (0 if approval.run_id in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS else 1)
            and approval.maximum_drive_archive_attempts == 1
            and all(
                entry.status == "PLANNED"
                and entry.max_attempts == 1
                and entry.attempt_count == 0
                and not entry.provider_call_made
                and entry.output_count == 0
                for entry in downstream
            )
        )

    def bind_imported_tts(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind an immutable prior-run TTS artifact without consuming a provider attempt."""

        if self.run_id not in CQR1_TTS_REUSE_RUN_IDS:
            raise ValueError("CQR1_IMPORTED_TTS_RUN_FORBIDDEN")
        entry = self._entry("elevenlabs_tts")
        if (
            entry.status != "PLANNED"
            or entry.max_attempts != 0
            or entry.attempt_count != 0
            or entry.provider_call_made
            or entry.output_count != 0
        ):
            raise RuntimeError("CQR1_IMPORTED_TTS_LEDGER_NOT_BINDABLE")
        evidence = _safe_evidence(dict(safe_evidence))
        if (
            evidence.get("evidence_mode") != "IMMUTABLE_IMPORTED_TTS"
            or evidence.get("source_run_id")
            != CQR1_PAID_CANARY_002_RUN_ID
            or not evidence.get("audio_sha256")
            or int(evidence.get("audio_duration_ms") or 0) <= 0
            or not evidence.get("import_evidence_hash")
        ):
            raise ValueError("CQR1_IMPORTED_TTS_EVIDENCE_INVALID")
        entry.status = "REUSED"
        entry.safe_evidence = evidence
        self.persist()

    def bind_imported_alignment(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind immutable run-004 alignment without consuming a provider attempt."""

        if self.run_id not in CQR1_ALIGNMENT_REUSE_RUN_IDS:
            raise ValueError("CQR1_IMPORTED_ALIGNMENT_RUN_FORBIDDEN")
        if not self._imported_tts_ready(self._entry("elevenlabs_tts")):
            raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_TTS_LINEAGE_NOT_BOUND")
        entry = self._entry("elevenlabs_forced_alignment")
        if (
            entry.status != "PLANNED"
            or entry.max_attempts != 0
            or entry.attempt_count != 0
            or entry.provider_call_made
            or entry.output_count != 0
        ):
            raise RuntimeError("CQR1_IMPORTED_ALIGNMENT_LEDGER_NOT_BINDABLE")
        evidence = _safe_evidence(dict(safe_evidence))
        probe = entry.model_copy(update={"status": "REUSED", "safe_evidence": evidence})
        tts_evidence = self._entry("elevenlabs_tts").safe_evidence
        if (
            not self._imported_alignment_ready(probe)
            or evidence.get("audio_sha256") != tts_evidence.get("audio_sha256")
            or int(evidence.get("audio_duration_ms") or 0)
            != int(tts_evidence.get("audio_duration_ms") or 0)
        ):
            raise ValueError("CQR1_IMPORTED_ALIGNMENT_EVIDENCE_INVALID")
        entry.status = "REUSED"
        entry.safe_evidence = evidence
        self.persist()

    def bind_imported_pexels_search(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind run-007 Pexels search/ranking evidence without a new search."""

        self._bind_imported_visual_entry(
            "pexels_search",
            safe_evidence=safe_evidence,
            validator=self._imported_pexels_search_ready,
            error_code="CQR1_IMPORTED_PEXELS_SEARCH_EVIDENCE_INVALID",
        )

    def bind_imported_pexels_download(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind run-007 Pexels bytes/provenance without a new download."""

        search = self._entry("pexels_search")
        if not self._imported_pexels_search_ready(search):
            raise RuntimeError("CQR1_IMPORTED_PEXELS_SEARCH_LINEAGE_NOT_BOUND")
        evidence = self._bind_imported_visual_entry(
            "pexels_download",
            safe_evidence=safe_evidence,
            validator=self._imported_pexels_download_ready,
            error_code="CQR1_IMPORTED_PEXELS_ASSET_EVIDENCE_INVALID",
            persist=False,
        )
        if (
            evidence.get("selected_provider_asset_id")
            != search.safe_evidence.get("selected_provider_asset_id")
            or evidence.get("query_plan_hash")
            != search.safe_evidence.get("query_plan_hash")
            or evidence.get("source_ledger_hash")
            != search.safe_evidence.get("source_ledger_hash")
        ):
            self._reset_unpersisted_import("pexels_download")
            raise ValueError("CQR1_IMPORTED_PEXELS_ASSET_LINEAGE_INVALID")
        self.persist()

    def bind_imported_veo_submit(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind run-007 Veo request/operation evidence without a new submit."""

        self._bind_imported_visual_entry(
            "google_veo_submit",
            safe_evidence=safe_evidence,
            validator=self._imported_veo_submit_ready,
            error_code="CQR1_IMPORTED_VEO_OPERATION_EVIDENCE_INVALID",
        )

    def bind_imported_veo_output(self, *, safe_evidence: Mapping[str, Any]) -> None:
        """Bind run-007 Veo output bytes/provenance without a new poll/download."""

        submit = self._entry("google_veo_submit")
        if not self._imported_veo_submit_ready(submit):
            raise RuntimeError("CQR1_IMPORTED_VEO_OPERATION_LINEAGE_NOT_BOUND")
        evidence = self._bind_imported_visual_entry(
            "google_veo_output",
            safe_evidence=safe_evidence,
            validator=self._imported_veo_output_ready,
            error_code="CQR1_IMPORTED_VEO_OUTPUT_EVIDENCE_INVALID",
            persist=False,
        )
        if (
            evidence.get("provider_operation_id")
            != submit.safe_evidence.get("provider_operation_id")
            or evidence.get("request_hash") != submit.safe_evidence.get("request_hash")
            or evidence.get("prompt_hash") != submit.safe_evidence.get("prompt_hash")
            or evidence.get("source_ledger_hash")
            != submit.safe_evidence.get("source_ledger_hash")
        ):
            self._reset_unpersisted_import("google_veo_output")
            raise ValueError("CQR1_IMPORTED_VEO_OUTPUT_LINEAGE_INVALID")
        self.persist()

    def _bind_imported_visual_entry(
        self,
        operation_key: str,
        *,
        safe_evidence: Mapping[str, Any],
        validator: Callable[[CQR1ProviderCallLedgerEntry], bool],
        error_code: str,
        persist: bool = True,
    ) -> dict[str, Any]:
        if self.run_id not in CQR1_VISUAL_PROVIDER_REUSE_RUN_IDS:
            raise ValueError("CQR1_IMPORTED_VISUAL_PROVIDER_RUN_FORBIDDEN")
        entry = self._entry(operation_key)
        if (
            entry.status != "PLANNED"
            or entry.max_attempts != 0
            or entry.attempt_count != 0
            or entry.provider_call_made
            or entry.output_count != 0
        ):
            raise RuntimeError("CQR1_IMPORTED_VISUAL_PROVIDER_LEDGER_NOT_BINDABLE")
        evidence = _safe_evidence(dict(safe_evidence))
        probe = entry.model_copy(update={"status": "REUSED", "safe_evidence": evidence})
        if not validator(probe):
            raise ValueError(error_code)
        entry.status = "REUSED"
        entry.safe_evidence = evidence
        if persist:
            self.persist()
        return evidence

    def _reset_unpersisted_import(self, operation_key: str) -> None:
        entry = self._entry(operation_key)
        entry.status = "PLANNED"
        entry.safe_evidence = {}

    @staticmethod
    def _imported_tts_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        return (
            entry.status == "REUSED"
            and entry.max_attempts == 0
            and entry.attempt_count == 0
            and not entry.provider_call_made
            and entry.output_count == 0
            and evidence.get("evidence_mode") == "IMMUTABLE_IMPORTED_TTS"
            and evidence.get("source_run_id") == CQR1_PAID_CANARY_002_RUN_ID
            and bool(evidence.get("audio_sha256"))
            and int(evidence.get("audio_duration_ms") or 0) > 0
            and bool(evidence.get("import_evidence_hash"))
        )

    @staticmethod
    def _imported_alignment_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        required_hashes = (
            "audio_sha256",
            "spoken_text_hash",
            "forced_alignment_content_hash",
            "verified_alignment_content_hash",
            "safe_provider_response_capture_hash",
            "import_evidence_hash",
        )
        try:
            coverage = float(evidence.get("spoken_coverage"))
            audio_duration_ms = int(evidence.get("audio_duration_ms") or 0)
            missing = int(evidence.get("missing_non_whitelisted_count", -1))
            extra = int(evidence.get("extra_non_whitelisted_count", -1))
        except (TypeError, ValueError):
            return False
        return (
            entry.status == "REUSED"
            and entry.max_attempts == 0
            and entry.attempt_count == 0
            and not entry.provider_call_made
            and entry.output_count == 0
            and evidence.get("evidence_mode") == "IMMUTABLE_IMPORTED_ALIGNMENT"
            and evidence.get("source_run_id") == CQR1_PAID_CANARY_004_RUN_ID
            and evidence.get("source_tts_run_id") == CQR1_PAID_CANARY_002_RUN_ID
            and audio_duration_ms > 0
            and coverage == 1.0
            and missing == 0
            and extra == 0
            and evidence.get("verification_status") == "PASS"
            and evidence.get("request_response_binding_valid") is True
            and all(_is_sha256_hex(evidence.get(name)) for name in required_hashes)
        )

    @staticmethod
    def _imported_pexels_search_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        required_hashes = (
            "query_plan_hash",
            "search_provenance_hash",
            "visual_direction_hash",
            "source_ledger_hash",
            "import_evidence_hash",
        )
        try:
            semantic_score = float(evidence.get("semantic_score"))
        except (TypeError, ValueError):
            return False
        return (
            CQR1CanaryCallLedger._base_visual_import_ready(
                entry, evidence_mode="IMMUTABLE_IMPORTED_PEXELS_SEARCH"
            )
            and all(_is_sha256_hex(evidence.get(name)) for name in required_hashes)
            and evidence.get("query_plan_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["query_plan_hash"]
            and evidence.get("search_provenance_hash")
            in CQR1_RUN007_VISUAL_REUSE_PINS["search_provenance_hashes"]
            and evidence.get("visual_direction_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["visual_direction_hash"]
            and evidence.get("selected_provider_asset_id")
            == CQR1_RUN007_VISUAL_REUSE_PINS["selected_provider_asset_id"]
            and evidence.get("selection_verdict") == "REVIEW_REQUIRED"
            and semantic_score == 0.744
        )

    @staticmethod
    def _imported_pexels_download_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        required_hashes = (
            "query_plan_hash",
            "asset_sha256",
            "download_receipt_hash",
            "representative_still_sha256",
            "visual_review_content_hash",
            "source_ledger_hash",
            "import_evidence_hash",
        )
        try:
            size_bytes = int(evidence.get("asset_size_bytes") or 0)
        except (TypeError, ValueError):
            return False
        return (
            CQR1CanaryCallLedger._base_visual_import_ready(
                entry, evidence_mode="IMMUTABLE_IMPORTED_PEXELS_ASSET"
            )
            and all(_is_sha256_hex(evidence.get(name)) for name in required_hashes)
            and evidence.get("query_plan_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["query_plan_hash"]
            and evidence.get("selected_provider_asset_id")
            == CQR1_RUN007_VISUAL_REUSE_PINS["selected_provider_asset_id"]
            and evidence.get("asset_sha256")
            == CQR1_RUN007_VISUAL_REUSE_PINS["pexels_asset_sha256"]
            and evidence.get("download_receipt_hash")
            in CQR1_RUN007_VISUAL_REUSE_PINS["pexels_download_receipt_hashes"]
            and evidence.get("representative_still_sha256")
            == CQR1_RUN007_VISUAL_REUSE_PINS[
                "pexels_representative_still_sha256"
            ]
            and evidence.get("visual_review_content_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["visual_review_content_hash"]
            and size_bytes > 0
        )

    @staticmethod
    def _imported_veo_submit_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        required_hashes = (
            "request_hash",
            "prompt_hash",
            "operation_receipt_hash",
            "visual_direction_hash",
            "source_ledger_hash",
            "import_evidence_hash",
        )
        operation_id = str(evidence.get("provider_operation_id") or "")
        return (
            CQR1CanaryCallLedger._base_visual_import_ready(
                entry, evidence_mode="IMMUTABLE_IMPORTED_VEO_OPERATION"
            )
            and all(_is_sha256_hex(evidence.get(name)) for name in required_hashes)
            and operation_id == CQR1_RUN007_VISUAL_REUSE_PINS["veo_operation_id"]
            and evidence.get("model_id") == "veo-3.1-fast-generate-preview"
            and evidence.get("request_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_request_hash"]
            and evidence.get("prompt_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_prompt_hash"]
            and evidence.get("operation_receipt_hash")
            in CQR1_RUN007_VISUAL_REUSE_PINS["veo_operation_receipt_hashes"]
            and evidence.get("visual_direction_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["visual_direction_hash"]
        )

    @staticmethod
    def _imported_veo_output_ready(entry: CQR1ProviderCallLedgerEntry) -> bool:
        evidence = entry.safe_evidence
        required_hashes = (
            "request_hash",
            "prompt_hash",
            "output_sha256",
            "output_provenance_hash",
            "representative_still_sha256",
            "visual_review_content_hash",
            "source_ledger_hash",
            "import_evidence_hash",
        )
        try:
            size_bytes = int(evidence.get("output_size_bytes") or 0)
        except (TypeError, ValueError):
            return False
        return (
            CQR1CanaryCallLedger._base_visual_import_ready(
                entry, evidence_mode="IMMUTABLE_IMPORTED_VEO_OUTPUT"
            )
            and all(_is_sha256_hex(evidence.get(name)) for name in required_hashes)
            and evidence.get("provider_operation_id")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_operation_id"]
            and evidence.get("request_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_request_hash"]
            and evidence.get("prompt_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_prompt_hash"]
            and evidence.get("output_sha256")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_output_sha256"]
            and evidence.get("output_provenance_hash")
            in CQR1_RUN007_VISUAL_REUSE_PINS["veo_output_provenance_hashes"]
            and evidence.get("representative_still_sha256")
            == CQR1_RUN007_VISUAL_REUSE_PINS["veo_representative_still_sha256"]
            and evidence.get("visual_review_content_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["visual_review_content_hash"]
            and evidence.get("provider_audio_policy") == "DISCARD"
            and size_bytes > 0
        )

    @staticmethod
    def _base_visual_import_ready(
        entry: CQR1ProviderCallLedgerEntry, *, evidence_mode: str
    ) -> bool:
        evidence = entry.safe_evidence
        return (
            entry.status == "REUSED"
            and entry.max_attempts == 0
            and entry.attempt_count == 0
            and not entry.provider_call_made
            and entry.output_count == 0
            and evidence.get("evidence_mode") == evidence_mode
            and evidence.get("source_run_id") == CQR1_PAID_CANARY_007_RUN_ID
            and evidence.get("source_ledger_hash")
            == CQR1_RUN007_VISUAL_REUSE_PINS["source_ledger_hash"]
            and evidence.get("provider_call_made_by_current_run") is False
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
        scope = {
            "run_id": self.run_id,
            "purpose": self.purpose,
            "approval_ref": self.approval_ref,
            "entries": serialized,
        }
        payload = {
            **scope,
            "ledger_hash_version": "cqr1-ledger-v2",
            "ledger_hash": stable_hash(scope),
        }
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
        ledger_ready = ledger.preflight_ready(approval)
        if not ledger_ready:
            blockers.append("CQR1_LEDGER_NOT_FRESH")
        if (
            ledger.run_id != approval.run_id
            or ledger.purpose != approval.purpose
            or ledger.approval_ref != approval.approval_ref
        ):
            blockers.append("CQR1_LEDGER_SCOPE_MISMATCH")
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
        elif not ledger_ready:
            next_action = "Use a fresh run ID and an authorized zero-attempt ledger; no retry is authorized."
        else:
            next_action = "Execute only the approved one-shot CQR1 provider operations through the guarded ledger."
        payload = {
            "run_id": approval.run_id,
            "status": "PASS" if passed else "BLOCKED",
            "blocker_reason_codes": blockers,
            "exact_next_action": next_action,
            "offline_gate_passed": offline.all_passed,
            "provider_readiness_passed": readiness.all_passed,
            "ledger_fresh": ledger_ready,
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
        if preflight.run_id != self.ledger.run_id:
            return {
                "status": "BLOCKED",
                "provider_call_made": False,
                "reason_codes": ["CQR1_PREFLIGHT_LEDGER_RUN_MISMATCH"],
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
            failure_evidence: dict[str, Any] = {
                "error_type": type(exc).__name__,
                "error_message_redacted": True,
            }
            provider_http_error = getattr(exc, "safe_evidence", None)
            if isinstance(provider_http_error, Mapping):
                try:
                    failure_evidence["provider_http_error"] = _safe_evidence(
                        dict(provider_http_error)
                    )
                except ValueError:
                    # The operation-specific error object can carry transient
                    # URL hashes for local diagnostics.  Ledger evidence must
                    # remain stricter and still complete the FAILED transition.
                    failure_evidence["provider_http_error"] = {
                        "provider": str(provider_http_error.get("provider") or "provider"),
                        "reason_code": str(
                            provider_http_error.get("reason_code")
                            or provider_http_error.get("reason")
                            or type(exc).__name__
                        )[:160],
                        "details_redacted": True,
                    }
            self.ledger.finish(
                operation_key,
                status="FAILED",
                provider_call_made=True,
                safe_evidence=failure_evidence,
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


def _is_sha256_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value.casefold())


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
                if key == "secret_values_exposed" and nested is False:
                    safe[key] = False
                    continue
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
