from __future__ import annotations

import hashlib
import json
import struct
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.contracts.creative_quality_canary import (
    CQR1_RUN_ID,
    CQR1CanaryApprovalScope,
    CQR1OfflineQualificationEvidence,
    CQR1ProviderReadinessEvidence,
    CreativeGateEvidence,
    FinalDurationEvidence,
)
from app.contracts.native_renderer import MediaQCReport as NativeMediaQCReport
from app.core.config import Settings
from app.services.cqr1_canary import (
    CQR1_CANARY_SCRIPT,
    CQR1_VISIBLE_LABEL,
    CQR1CanaryCallLedger,
    CQR1CanaryExecutionGuard,
    CQR1PaidCanaryEntryGate,
    current_static_provider_readiness,
    run_cqr1d_offline_rehearsal,
)
from app.services.creative_media_qc import (
    REQUIRED_CREATIVE_MEDIA_QC_GATES,
    REQUIRED_TECHNICAL_MEDIA_QC_CHECKS,
    CreativePerceptualMediaQC,
    FinalDurationConsistencyGate,
    HumanWatchabilityPacketBuilder,
    TechnicalMediaQC,
)
from app.services.native_render_plan import stable_hash
from app.services.native_media_qc import NativeMediaQC, _fast_start_atom_order
from app.services.production_archive import (
    CQR1A_REQUIRED_ARCHIVE_ROLES,
    CQR1ArchivePathBuilder,
    CQR1_REQUIRED_ARCHIVE_ROLES,
    LEGACY_REQUIRED_ARCHIVE_ROLES,
    ROLE_ARCHIVE_PATHS,
    ArchiveSource,
    ProductionArchiveBuilder,
)


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_PA1R_HASHES = {
    "reports/pa1r_guarded_provider_smoke_report.md": "ebe6b0eafa6d1dc3c96d4182d4278f37ad9fa031a88c896fa3f0b00687977c74",
    "reports/pa1r_provider_smoke_human_review.md": "20cc3a63798726119b2d74ebf4b2062ccc24b3526c4f433d55f1dc8cd2f0402c",
    "reports/pa1r_summary.json": "1e274e1e0a6ad8bafe93f39cec210e8a7431fa145edc7c2e97851d965e3bfeb9",
}
DURATION_POLICY = {"pass_max": 100, "review_max": 250, "block_above": 250}
HUMAN_POLICY = {
    "optional_flagged_spot_check_speed": 0.75,
    "pass_total_min": 32,
    "pass_dimension_min": 3,
    "repair_total_range": [24, 31],
}


def passing_offline(**changes: bool) -> CQR1OfflineQualificationEvidence:
    payload = {name: True for name in CQR1OfflineQualificationEvidence.model_fields}
    payload.update(changes)
    return CQR1OfflineQualificationEvidence(**payload)


def passing_readiness(**changes) -> CQR1ProviderReadinessEvidence:
    payload = {
        name: True
        for name in CQR1ProviderReadinessEvidence.model_fields
        if name not in {"secret_values_exposed", "provider_probe_count"}
    }
    payload |= {"secret_values_exposed": False, "provider_probe_count": 0}
    payload.update(changes)
    return CQR1ProviderReadinessEvidence(**payload)


def approval() -> CQR1CanaryApprovalScope:
    return CQR1CanaryApprovalScope(
        approval_ref=f"operator-prompt://{CQR1_RUN_ID}",
        total_hard_cost_cap_usd=Decimal("3.00"),
    )


def planned_gate(tmp_path: Path):
    scope = approval()
    ledger = CQR1CanaryCallLedger.create(tmp_path / "planned-provider-call-ledger.json", approval=scope)
    preflight = CQR1PaidCanaryEntryGate().evaluate(
        offline=passing_offline(),
        readiness=passing_readiness(),
        approval=scope,
        ledger=ledger,
        estimated_cost_usd=Decimal("2.75"),
    )
    assert preflight.status == "PASS"
    return ledger, preflight


@pytest.mark.parametrize(
    "delta,expected",
    [(0, "PASS"), (100, "PASS"), (101, "REVIEW_REQUIRED"), (250, "REVIEW_REQUIRED"), (251, "BLOCK")],
)
def test_final_duration_consistency_thresholds(delta: int, expected: str):
    result = FinalDurationConsistencyGate(DURATION_POLICY).evaluate(
        FinalDurationEvidence(
            canonical_timeline_duration_ms=30_000,
            final_narration_duration_ms=30_000,
            final_mp4_duration_ms=30_000 + delta,
            final_caption_end_ms=30_000,
            final_scene_end_ms=30_000,
        )
    )
    assert result.result == expected
    assert result.metrics["max_abs_delta_ms"] == delta


def test_technical_pass_never_implies_creative_pass():
    technical = TechnicalMediaQC().evaluate(
        run_id=CQR1_RUN_ID,
        checks={name: True for name in REQUIRED_TECHNICAL_MEDIA_QC_CHECKS},
    )
    creative = CreativePerceptualMediaQC().aggregate(run_id=CQR1_RUN_ID, gate_results=[])
    assert technical.result == "PASS"
    assert creative.result == "BLOCK"
    assert creative.technical_media_qc_implies_creative_pass is False
    assert set(creative.missing_gates) == set(REQUIRED_CREATIVE_MEDIA_QC_GATES)


def gate(name: str, result: str = "PASS") -> CreativeGateEvidence:
    payload = {
        "gate_name": name,
        "result": result,
        "reason_codes": [] if result == "PASS" else [f"{name.upper()}_{result}"],
        "metrics": {},
        "evidence_refs": [],
    }
    return CreativeGateEvidence(**payload, content_hash=stable_hash(payload))


def test_creative_qc_aggregates_pass_review_and_block():
    passing = [gate(name) for name in REQUIRED_CREATIVE_MEDIA_QC_GATES]
    assert CreativePerceptualMediaQC().aggregate(run_id=CQR1_RUN_ID, gate_results=passing).result == "PASS"
    review = [item if item.gate_name != "VisualContinuityGate" else gate(item.gate_name, "REVIEW_REQUIRED") for item in passing]
    assert CreativePerceptualMediaQC().aggregate(run_id=CQR1_RUN_ID, gate_results=review).result == "REVIEW_REQUIRED"
    blocked = [item if item.gate_name != "CaptionCoverageGate" else gate(item.gate_name, "BLOCK") for item in passing]
    assert CreativePerceptualMediaQC().aggregate(run_id=CQR1_RUN_ID, gate_results=blocked).result == "BLOCK"


def test_qc_and_pending_packet_hashes_are_deterministic():
    checks = {name: True for name in REQUIRED_TECHNICAL_MEDIA_QC_CHECKS}
    first = TechnicalMediaQC().evaluate(run_id=CQR1_RUN_ID, checks=checks)
    second = TechnicalMediaQC().evaluate(run_id=CQR1_RUN_ID, checks=checks)
    assert first.content_hash == second.content_hash
    builder = HumanWatchabilityPacketBuilder()
    packet_a = builder.build(
        run_id=CQR1_RUN_ID,
        final_mp4_path="render/final/cqr1-canary.mp4",
        contact_sheet_path="render/proxy/cqr1-contact-sheet.jpg",
        before_after_packet_ref="reports/cqr1-before-after.json",
        policy=HUMAN_POLICY,
    )
    packet_b = builder.build(
        run_id=CQR1_RUN_ID,
        final_mp4_path="render/final/cqr1-canary.mp4",
        contact_sheet_path="render/proxy/cqr1-contact-sheet.jpg",
        before_after_packet_ref="reports/cqr1-before-after.json",
        policy=HUMAN_POLICY,
    )
    assert packet_a.content_hash == packet_b.content_hash
    assert packet_a.review_state == "PENDING"
    assert all(item.score is None for item in packet_a.dimensions)
    assert packet_a.uninterrupted_full_watch_1x_completed is False
    assert packet_a.not_publishable and not packet_a.production_eligible


def test_technical_fast_start_check_reads_actual_mp4_atom_order(tmp_path: Path):
    def atom(name: bytes, payload: bytes = b"") -> bytes:
        return struct.pack(">I4s", len(payload) + 8, name) + payload

    fast = tmp_path / "fast.mp4"
    slow = tmp_path / "slow.mp4"
    fast.write_bytes(atom(b"ftyp", b"isom") + atom(b"moov") + atom(b"mdat", b"media"))
    slow.write_bytes(atom(b"ftyp", b"isom") + atom(b"mdat", b"media") + atom(b"moov"))
    assert _fast_start_atom_order(fast) is True
    assert _fast_start_atom_order(slow) is False


def test_native_media_qc_decodes_all_streams_and_blocks_large_av_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    output = tmp_path / "measured.mp4"
    atom = lambda name, payload=b"": struct.pack(">I4s", len(payload) + 8, name) + payload
    output.write_bytes(atom(b"ftyp", b"isom") + atom(b"moov") + atom(b"mdat", b"media"))
    probe_payload = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "30.000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "color_space": "bt709",
                "duration": "30.000",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "29.500",
            },
        ],
    }
    commands: list[list[str]] = []

    def fake_run(argv, **kwargs):
        commands.append(list(argv))
        if "-show_streams" in argv:
            return SimpleNamespace(returncode=0, stdout=json.dumps(probe_payload), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("app.services.native_media_qc.subprocess.run", fake_run)
    report = NativeMediaQC("fixture-ffprobe", ffmpeg="fixture-ffmpeg").inspect(
        output,
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
            "expected_duration_seconds": 30,
            "max_av_drift_ms": 250,
        },
        CQR1_RUN_ID,
    )
    decode = next(command for command in commands if command[0] == "fixture-ffmpeg")
    assert "-xerror" in decode
    assert decode[decode.index("-map") + 1] == "0"
    assert report.checks["full_decode"] is True
    assert report.checks["stream_integrity"] is True
    assert report.checks["av_drift_ms"] == 500
    assert report.checks["av_drift_within_limit"] is False
    assert report.result == "FAIL"
    assert "QC_AV_DRIFT_WITHIN_LIMIT" in report.reason_codes
    adapted = TechnicalMediaQC().from_native_media_qc(run_id=CQR1_RUN_ID, native_report=report)
    assert adapted.result == "FAIL"
    assert "TECHNICAL_CHECK_FAILED_STREAM_INTEGRITY" in adapted.reason_codes
    assert adapted.production_eligible is False and adapted.not_publishable is True


def test_native_technical_adapter_rejects_checksum_tamper_without_creative_or_human_pass():
    measured_checks = {
        "full_decode": True,
        "codec_container_matches_expected": True,
        "stream_integrity": True,
        "av_drift_within_limit": True,
        "dimensions_match_expected": True,
        "fps_matches_expected": True,
        "audio_format_matches_expected": True,
        "duration_matches_expected": True,
        "fast_start": True,
        "checksum_sha256": "a" * 64,
        "duration": 30.0,
        "av_drift_ms": 0,
        "max_av_drift_ms": 250,
    }
    native = NativeMediaQCReport(
        run_key=CQR1_RUN_ID,
        result="PASS",
        checks=measured_checks,
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
    )
    technical = TechnicalMediaQC().from_native_media_qc(run_id=CQR1_RUN_ID, native_report=native)
    assert technical.result == "PASS"
    tampered = native.model_copy(
        update={"checks": {**measured_checks, "checksum_sha256": "not-a-sha256"}}
    )
    rejected = TechnicalMediaQC().from_native_media_qc(run_id=CQR1_RUN_ID, native_report=tampered)
    assert rejected.result == "FAIL"
    assert "TECHNICAL_CHECK_FAILED_CHECKSUM" in rejected.reason_codes
    creative = CreativePerceptualMediaQC().aggregate(run_id=CQR1_RUN_ID, gate_results=[])
    human = HumanWatchabilityPacketBuilder().build(
        run_id=CQR1_RUN_ID,
        final_mp4_path="render/final/cqr1-canary.mp4",
        contact_sheet_path="render/proxy/cqr1-contact-sheet.jpg",
        before_after_packet_ref="reports/cqr1-before-after.json",
        policy=HUMAN_POLICY,
    )
    assert creative.result == "BLOCK"
    assert human.review_state == "PENDING"
    assert human.production_eligible is False and human.not_publishable is True


def test_current_preflight_blocks_unknown_forced_alignment_with_zero_calls(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        PEXELS_API_KEY="pexels-secret",
        ELEVENLABS_API_KEY="eleven-secret",
        GEMINI_API_KEY="gemini-secret",
        GOOGLE_DRIVE_ROOT_FOLDER_ID="configured-root",
    )
    readiness = current_static_provider_readiness(
        settings=settings,
        drive_oauth_connected=True,
        elevenlabs_tts_access_confirmed=True,
        elevenlabs_voices_read_confirmed=True,
        elevenlabs_models_read_confirmed=True,
        google_veo_model_accessible=True,
    )
    scope = approval()
    ledger = CQR1CanaryCallLedger.create(tmp_path / "ledger.json", approval=scope)
    calls: list[str] = []
    preflight = CQR1PaidCanaryEntryGate().evaluate(
        offline=passing_offline(), readiness=readiness, approval=scope, ledger=ledger
    )
    result = CQR1CanaryExecutionGuard(ledger).run_once(
        "elevenlabs_tts", preflight=preflight, operation=lambda: calls.append("called") or {}
    )
    assert preflight.status == "BLOCKED" and preflight.provider_call_count == 0
    assert "PROVIDER_READINESS_FAILED_ELEVENLABS_FORCED_ALIGNMENT_PERMISSION_CONFIRMED" in preflight.blocker_reason_codes
    assert result["provider_call_made"] is False and calls == []
    assert ledger.fresh
    assert "pexels-secret" not in preflight.model_dump_json()


def test_no_provider_callback_before_all_offline_gates_pass(tmp_path: Path):
    scope = approval()
    ledger = CQR1CanaryCallLedger.create(tmp_path / "ledger.json", approval=scope)
    preflight = CQR1PaidCanaryEntryGate().evaluate(
        offline=passing_offline(golden_media_tests_passed=False),
        readiness=passing_readiness(),
        approval=scope,
        ledger=ledger,
    )
    calls: list[int] = []
    result = CQR1CanaryExecutionGuard(ledger).run_once(
        "pexels_search", preflight=preflight, operation=lambda: calls.append(1) or {}
    )
    assert result["status"] == "BLOCKED" and result["provider_call_made"] is False
    assert calls == [] and ledger.provider_call_count == 0 and ledger.fresh


def test_offline_rehearsal_writes_atomic_pending_packet_without_any_operation(tmp_path: Path):
    settings = Settings(
        _env_file=None,
        PEXELS_API_KEY="configured",
        ELEVENLABS_API_KEY="configured",
        GEMINI_API_KEY="configured",
        GOOGLE_DRIVE_ROOT_FOLDER_ID="configured",
    )
    workspace = tmp_path / "cqr1d-offline"
    first = run_cqr1d_offline_rehearsal(
        workspace,
        offline_evidence=passing_offline(),
        settings=settings,
        readiness_overrides={"drive_oauth_connected": True},
        human_watchability_policy=HUMAN_POLICY,
    )
    second = run_cqr1d_offline_rehearsal(
        workspace,
        offline_evidence=passing_offline(),
        settings=settings,
        readiness_overrides={"drive_oauth_connected": True},
        human_watchability_policy=HUMAN_POLICY,
    )
    assert first == second
    assert first["preflight_status"] == "BLOCKED"
    assert first["provider_call_count"] == 0 and first["provider_operation_executed"] is False
    assert all(value == 0 for value in first["ledger_attempt_counts"].values())
    assert first["human_watchability_review"] == "PENDING"
    assert not list(workspace.rglob("*.part"))
    assert (workspace / "manifests/paid_canary_preflight.json").is_file()
    assert (workspace / "qc/human_watchability_review_packet.json").is_file()


def test_each_operation_is_one_shot_and_no_second_paid_attempt(tmp_path: Path):
    ledger, preflight = planned_gate(tmp_path)
    calls: list[int] = []
    guard = CQR1CanaryExecutionGuard(ledger)
    first = guard.run_once(
        "google_veo_submit",
        preflight=preflight,
        operation=lambda: calls.append(1) or {"operation_id_present": True, "output_count": 0},
    )
    second = guard.run_once(
        "google_veo_submit",
        preflight=preflight,
        operation=lambda: calls.append(2) or {},
    )
    assert first["status"] == "SUCCEEDED" and second["status"] == "BLOCKED"
    assert calls == [1]
    assert ledger.entries["google_veo_submit"].attempt_count == 1
    assert CQR1CanaryCallLedger.load(ledger.path).entries["google_veo_submit"].status == "SUCCEEDED"


def test_output_limit_failure_consumes_attempt_and_external_fallback_is_rejected(tmp_path: Path):
    ledger, preflight = planned_gate(tmp_path)
    guard = CQR1CanaryExecutionGuard(ledger)
    with pytest.raises(RuntimeError, match="OUTPUT_LIMIT"):
        guard.run_once(
            "google_veo_output", preflight=preflight, operation=lambda: {"output_count": 2}
        )
    assert ledger.entries["google_veo_output"].status == "FAILED"
    assert ledger.entries["google_veo_output"].attempt_count == 1
    calls: list[int] = []
    rejected = guard.run_once(
        "alternate_ai_video_provider", preflight=preflight, operation=lambda: calls.append(1) or {}
    )
    assert rejected["status"] == "BLOCKED" and calls == []


def test_ledger_rejects_secret_or_raw_download_evidence(tmp_path: Path):
    ledger, preflight = planned_gate(tmp_path)
    with pytest.raises(ValueError, match="UNSAFE_EVIDENCE"):
        CQR1CanaryExecutionGuard(ledger).run_once(
            "pexels_search",
            preflight=preflight,
            operation=lambda: {"api_key": "must-not-persist", "output_count": 0},
        )
    persisted = ledger.path.read_text(encoding="utf-8")
    assert "must-not-persist" not in persisted
    assert ledger.entries["pexels_search"].status == "FAILED"


def test_canary_scope_content_and_no_publish_limits_are_exact():
    scope = approval()
    assert scope.maximum_pexels_search_flows == scope.maximum_pexels_downloads == 1
    assert scope.maximum_elevenlabs_tts_generations == scope.maximum_elevenlabs_forced_alignment_calls == 1
    assert scope.maximum_google_veo_submits == scope.maximum_google_veo_outputs == 1
    assert scope.total_hard_cost_cap_usd == Decimal("3.00")
    assert scope.automatic_provider_retry is False and scope.external_provider_fallback is False
    assert scope.youtube_allowed is False and scope.not_publishable is True
    assert CQR1_VISIBLE_LABEL == "VCOS CQR1 NON-PRODUCTION CANARY"
    assert CQR1_CANARY_SCRIPT.endswith("This is a non-production canary.")


def test_cqr1_archive_role_set_does_not_expand_legacy_or_cqr1a(tmp_path: Path):
    assert CQR1A_REQUIRED_ARCHIVE_ROLES == frozenset(ROLE_ARCHIVE_PATHS)
    assert "CANONICAL_MEDIA_TIMELINE" not in LEGACY_REQUIRED_ARCHIVE_ROLES
    assert "TECHNICAL_MEDIA_QC" not in CQR1A_REQUIRED_ARCHIVE_ROLES
    assert {
        "CANONICAL_MEDIA_TIMELINE",
        "TECHNICAL_MEDIA_QC",
        "CREATIVE_PERCEPTUAL_MEDIA_QC",
        "HUMAN_REVIEW_PACKET",
        "NOT_PUBLISHABLE_MANIFEST",
    } <= CQR1_REQUIRED_ARCHIVE_ROLES

    sources = []
    for role in sorted(CQR1_REQUIRED_ARCHIVE_ROLES):
        source = tmp_path / f"{role.lower()}.fixture"
        source.write_text(role, encoding="utf-8")
        sources.append(ArchiveSource(logical_role=role, source_path=source))
    manifest = ProductionArchiveBuilder().build(
        manifest_id="cqr1-archive-fixture",
        project_id="cqr1-project",
        package_id="cqr1-package",
        sources=sources,
        required_roles=CQR1_REQUIRED_ARCHIVE_ROLES,
    )
    assert manifest.required_roles_complete
    assert {item.logical_role for item in manifest.files} == set(CQR1_REQUIRED_ARCHIVE_ROLES)


def test_cqr1_archive_path_is_exact_root_relative_policy():
    expected = f"smoke_tests/2026-07-14/cqr1/{CQR1_RUN_ID}"
    assert CQR1ArchivePathBuilder.build(run_id=CQR1_RUN_ID) == expected
    CQR1ArchivePathBuilder.validate(expected)
    for invalid in (
        f"/smoke_tests/2026-07-14/cqr1/{CQR1_RUN_ID}",
        f"smoke_tests/2026-07-14/pa1r/{CQR1_RUN_ID}",
        "smoke_tests/2026-07-14/cqr1/other-run",
        f"smoke_tests/2026-07-14/cqr1/../{CQR1_RUN_ID}",
    ):
        with pytest.raises(ValueError):
            CQR1ArchivePathBuilder.validate(invalid)


def test_original_pa1r_evidence_remains_byte_immutable():
    for relative_path, expected in HISTORICAL_PA1R_HASHES.items():
        assert hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() == expected


def test_cqr1_canary_boundary_does_not_reference_publish_or_domain_mutation_models():
    source = (ROOT / "app/services/cqr1_canary.py").read_text(encoding="utf-8")
    forbidden = (
        "FinalMediaRef",
        "HumanUploadTask",
        "UploadedVideo",
        "ChannelProfileVersion",
        "EffectiveChannelRuntimeContextSnapshot",
        "LearningToMemoryPromotionRun",
    )
    assert all(name not in source for name in forbidden)
