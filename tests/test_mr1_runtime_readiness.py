from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.core.config import Settings
from app.services import mr1_runtime_readiness as readiness
from scripts import run_mr1_real_production as runner


def _executable(path: Path) -> str:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _explicit_closeout_args(**overrides):
    values = {
        "closeout": True,
        "authority_mode": "sc04",
        "resume_run_id": uuid.uuid4(),
        "human_decision": "PASS",
        "operator_decision_text": "PASS",
        "review_media_candidate_artifact_version_id": uuid.uuid4(),
        "review_media_candidate_content_hash": "1" * 64,
        "reviewed_output_sha256": "2" * 64,
        "drive_archive_receipt_artifact_version_id": uuid.uuid4(),
        "drive_archive_receipt_content_hash": "3" * 64,
        "archive_identity": "mr1-archive://small-team-ai/exact-run",
        "decided_by_user_id": uuid.uuid4(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_explicit_closeout_command_requires_literal_human_authority():
    args = _explicit_closeout_args()

    command = runner._explicit_closeout_command(args)

    assert command is not None
    assert command.run_id == args.resume_run_id
    assert command.decision == "PASS"
    assert command.operator_decision_text == "PASS"


def test_closeout_readiness_is_drive_only_and_skips_media_provider_flags(
    tmp_path, monkeypatch
):
    args = _explicit_closeout_args()
    command = runner._explicit_closeout_command(args)
    assert command is not None
    state = {
        "run_id": str(command.run_id),
        "project_id": str(command.project_id),
        "current_state": "AWAITING_HUMAN_FULL_WATCH",
        "archive_identity": command.archive_identity,
        "review_media_candidate": {
            "artifact_version_id": str(
                command.review_media_candidate_artifact_version_id
            ),
            "content_hash": command.review_media_candidate_content_hash,
            "output_sha256": command.reviewed_output_sha256,
        },
        "drive_archive": {
            "artifact_version_id": str(
                command.drive_archive_receipt_artifact_version_id
            ),
            "content_hash": command.drive_archive_receipt_content_hash,
        },
    }

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def _require_run(self, _run_id, *, lock):
            assert lock is False
            return object(), SimpleNamespace(content=state)

    class FakeConfig:
        def __init__(self, _settings):
            pass

        def root_folder_id(self):
            return "exact-root"

        def oauth_configured(self):
            return True

    class FakeCredentials:
        def __init__(self, _session, *, config_service):
            assert isinstance(config_service, FakeConfig)

        def get_connected_reference(self):
            return object()

        def get_valid_access_token(self, _reference):
            return "read-only-token"

    class FakeArchive:
        @classmethod
        def from_existing_configuration(cls, **_kwargs):
            return cls()

        def read_only_root_readiness(self, *, access_token):
            assert access_token == "read-only-token"
            return {"result": "PASS", "checks": {"mutation_free": "PASS"}}

    monkeypatch.setattr(runner, "MR1RealProductionService", FakeService)
    monkeypatch.setattr(runner, "GoogleDriveConfigService", FakeConfig)
    monkeypatch.setattr(
        runner,
        "GoogleDriveOAuthCredentialService",
        FakeCredentials,
    )
    monkeypatch.setattr(runner, "MR1DriveArchiveService", FakeArchive)
    monkeypatch.setattr(runner, "_git_root", lambda: runner.ROOT)
    settings = SimpleNamespace(
        google_drive_offload_enabled=True,
        google_drive_real_archive_enabled=True,
        google_drive_archive_enabled=True,
        upload_and_publish_disabled=True,
    )

    result = runner._closeout_runtime_readiness(
        settings=settings,
        session=object(),
        workspace_root=tmp_path,
        command=command,
    )

    assert result["result"] == "PASS"
    assert result["mode"] == "POST_WATCH_DRIVE_ONLY_NO_MEDIA_PROVIDER_PROBE"
    assert result["media_provider_preflights"] == 0
    assert result["provider_generation_calls"] == 0


def test_reject_closeout_readiness_constructs_no_provider_or_drive_gateway(
    tmp_path, monkeypatch
):
    args = _explicit_closeout_args(
        human_decision="REJECT",
        operator_decision_text="REJECT: caption readability",
    )
    command = runner._explicit_closeout_command(args)
    assert command is not None
    state = {
        "run_id": str(command.run_id),
        "project_id": str(command.project_id),
        "current_state": "AWAITING_HUMAN_FULL_WATCH",
        "archive_identity": command.archive_identity,
        "review_media_candidate": {
            "artifact_version_id": str(
                command.review_media_candidate_artifact_version_id
            ),
            "content_hash": command.review_media_candidate_content_hash,
            "output_sha256": command.reviewed_output_sha256,
        },
        "drive_archive": {
            "artifact_version_id": str(
                command.drive_archive_receipt_artifact_version_id
            ),
            "content_hash": command.drive_archive_receipt_content_hash,
        },
    }

    class FakeService:
        def __init__(self, *_args, **_kwargs):
            pass

        def _require_run(self, _run_id, *, lock):
            assert lock is False
            return object(), SimpleNamespace(content=state)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("reject readiness must not construct Drive")

    monkeypatch.setattr(runner, "MR1RealProductionService", FakeService)
    monkeypatch.setattr(runner, "GoogleDriveConfigService", forbidden)
    monkeypatch.setattr(
        runner,
        "GoogleDriveOAuthCredentialService",
        forbidden,
    )
    monkeypatch.setattr(runner, "MR1DriveArchiveService", forbidden)
    monkeypatch.setattr(runner, "_git_root", lambda: runner.ROOT)

    result = runner._closeout_runtime_readiness(
        settings=SimpleNamespace(upload_and_publish_disabled=True),
        session=object(),
        workspace_root=tmp_path,
        command=command,
    )

    assert result["result"] == "PASS"
    assert result["mode"] == "POST_WATCH_REJECT_AUTHORITY_ONLY_NO_GATEWAY"
    assert result["drive_mutation_calls"] == 0
    assert result["media_provider_preflights"] == 0


def _fake_toolchain_runner(calls: list[list[str]], *, blackdetect: bool = True):
    def run(argv, *, cwd, timeout):
        command = list(argv)
        calls.append(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=" V..... libx264 H.264\n A..... aac AAC\n",
                stderr="",
            )
        if "-filters" in command:
            names = ["ass", "drawtext"]
            if blackdetect:
                names.append("blackdetect")
            listing = "".join(f" ... {name} V->V\n" for name in names)
            return subprocess.CompletedProcess(command, 0, stdout=listing, stderr="")
        if "-filter_complex" in command:
            Path(command[-1]).write_bytes(b"fake-local-h264-aac-probe")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="",
                stderr=(
                    "[Parsed_ass] fontselect: (Arial, 400, 0) -> "
                    "/System/Library/Fonts/Arial.ttf\n"
                ),
            )
        if Path(command[0]).name == "ffprobe":
            payload = {
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 320,
                        "height": 180,
                    },
                    {
                        "codec_type": "audio",
                        "codec_name": "aac",
                        "sample_rate": "48000",
                        "channels": 2,
                    },
                ],
                "format": {"duration": "0.600000"},
            }
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(payload), stderr=""
            )
        if "-xerror" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    return run


def test_toolchain_probe_executes_all_local_capabilities_without_provider(
    tmp_path, monkeypatch
):
    allowed = tmp_path / "var" / "mr1"
    workspace = allowed / "runs"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ffmpeg = _executable(binaries / "ffmpeg")
    ffprobe = _executable(binaries / "ffprobe")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        readiness,
        "_run_probe_command",
        _fake_toolchain_runner(calls),
    )

    evidence = readiness.probe_mr1_production_toolchain(
        workspace_root=workspace,
        allowed_workspace_root=allowed,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        minimum_free_bytes=0,
    )

    assert evidence["result"] == "PASS"
    assert set(evidence["checks"].values()) == {"PASS"}
    assert evidence["local_probe_command_count"] == 5
    assert evidence["provider_calls"] == 0
    assert evidence["drive_calls"] == 0
    assert evidence["production_render_calls"] == 0
    assert evidence["temporary_probe_artifacts_retained"] is False
    assert [item.name for item in workspace.iterdir()] == []
    flattened = " ".join(" ".join(command) for command in calls)
    for required in ("libx264", "aac", "drawtext", "ass=", "blackdetect"):
        assert required in flattened
    assert any("-xerror" in command for command in calls)


def test_toolchain_probe_fails_closed_before_encode_when_filter_missing(
    tmp_path, monkeypatch
):
    allowed = tmp_path / "var" / "mr1"
    workspace = allowed / "runs"
    binaries = tmp_path / "bin"
    binaries.mkdir()
    ffmpeg = _executable(binaries / "ffmpeg")
    ffprobe = _executable(binaries / "ffprobe")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        readiness,
        "_run_probe_command",
        _fake_toolchain_runner(calls, blackdetect=False),
    )

    evidence = readiness.probe_mr1_production_toolchain(
        workspace_root=workspace,
        allowed_workspace_root=allowed,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        minimum_free_bytes=0,
    )

    assert evidence["result"] == "FAIL"
    assert evidence["checks"]["blackdetect_filter_available"] == "FAIL"
    assert evidence["checks"]["actual_local_encode_pass"] == "FAIL"
    assert evidence["reason_codes"] == ["MR1_TOOLCHAIN_REQUIRED_CAPABILITY_MISSING"]
    assert evidence["local_probe_command_count"] == 2


def test_runner_performs_drive_root_reads_only_after_exact_db_authority(
    tmp_path, monkeypatch
):
    events: list[str] = []
    summary = {
        "verdicts": {
            "MR1_REAPPROVAL_FINAL": "PASS",
            "PROCEED_TO_MR1": True,
            "MR1_EXECUTION": "NOT_STARTED",
        },
        "approval": {
            "approval_decision_id": str(runner.APPROVAL_ID),
            "approval_content_hash": runner.APPROVAL_CONTENT_HASH,
        },
        "exact_target": {
            "video_project_id": str(runner.PROJECT_ID),
            "package_artifact_version_id": str(runner.PACKAGE_ARTIFACT_VERSION_ID),
            "package_content_hash": runner.PACKAGE_CONTENT_HASH,
        },
        "exact_bindings": {
            "channel_profile_version_id": str(runner.PROFILE_ID),
            "compiled_policy_snapshot_id": str(runner.SNAPSHOT_ID),
        },
        "no_execution_proof": {
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        },
    }
    summary_path = tmp_path / "mr1-reapproval.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(runner, "REAPPROVAL_SUMMARY_PATH", summary_path)
    monkeypatch.setattr(runner, "_git_root", lambda: runner.ROOT)

    tool_checks = {
        "ffmpeg_executable": "PASS",
        "ffprobe_executable": "PASS",
        "libx264_encoder_available": "PASS",
        "aac_encoder_available": "PASS",
        "ass_libass_filter_available": "PASS",
        "drawtext_filter_available": "PASS",
        "blackdetect_filter_available": "PASS",
        "arial_font_available": "PASS",
        "actual_local_encode_pass": "PASS",
        "actual_local_decode_pass": "PASS",
        "workspace_contained": "PASS",
        "workspace_writable": "PASS",
        "render_disk_space_available": "PASS",
    }
    monkeypatch.setattr(
        runner,
        "probe_mr1_production_toolchain",
        lambda **_: {"result": "PASS", "checks": tool_checks},
    )

    class FakeProductionService:
        def __init__(self, *args, settings=None, **kwargs):
            assert settings is configured

        def _resolve_exact_authority(self, _command):
            events.append("exact-db-authority")
            return {"exact": True}

    class FakeDriveConfig:
        def __init__(self, _settings):
            pass

        def oauth_configured(self):
            return True

        def root_folder_id(self):
            return "configured-root"

        def upload_mode(self):
            return "resumable"

    class FakeCredentials:
        def __init__(self, _session, *, config_service):
            pass

        def get_connected_reference(self):
            return SimpleNamespace(id="credential-reference")

        def get_valid_access_token(self, _reference):
            events.append("oauth-token")
            return "secret-access-token"

    class FakeArchive:
        def read_only_root_readiness(self, *, access_token):
            assert access_token == "secret-access-token"
            events.append("drive-root-read-only")
            return {
                "result": "PASS",
                "checks": {
                    "root_folder_metadata_accessible": "PASS",
                    "root_folder_identity_exact": "PASS",
                    "root_folder_type_exact": "PASS",
                    "root_folder_listable": "PASS",
                    "mutation_free": "PASS",
                },
                "metadata_read_calls": 1,
                "folder_list_read_calls": 1,
                "drive_archive_calls": 0,
                "drive_mutation_calls": 0,
            }

    class FakeArchiveService:
        @classmethod
        def from_existing_configuration(cls, **_kwargs):
            return FakeArchive()

    monkeypatch.setattr(runner, "MR1RealProductionService", FakeProductionService)
    monkeypatch.setattr(runner, "GoogleDriveConfigService", FakeDriveConfig)
    monkeypatch.setattr(runner, "GoogleDriveOAuthCredentialService", FakeCredentials)
    monkeypatch.setattr(runner, "MR1DriveArchiveService", FakeArchiveService)

    configured = Settings(
        _env_file=None,
        provider_real_execution_enabled=True,
        provider_production_execution_enabled=True,
        media_provider_calls_disabled=False,
        elevenlabs_real_execution_enabled=True,
        elevenlabs_real_generation_enabled=True,
        elevenlabs_forced_alignment_permission_confirmed=True,
        elevenlabs_api_key="fake-elevenlabs",
        elevenlabs_voice_id=runner.EXACT_VOICE_ID,
        elevenlabs_model_id=runner.EXACT_MODEL_ID,
        pexels_real_execution_enabled=True,
        pexels_real_search_enabled=True,
        pexels_api_key="fake-pexels",
        pexels_max_clips_per_long=3,
        pexels_attribution_required=True,
        native_ffmpeg_production_enabled=True,
        google_drive_offload_enabled=True,
        google_drive_archive_enabled=True,
        google_drive_real_archive_enabled=True,
        upload_and_publish_disabled=True,
        old_provider_smoke_disabled=True,
        gemini_image_real_generation_enabled=False,
        veo_real_generation_enabled=False,
        provider_real_readiness_probe_enabled=False,
        monthly_ai_budget_usd=1,
        elevenlabs_monthly_cap_usd=1,
        stock_monthly_budget_usd=0,
        budget_mode="hard_env",
    )
    workspace = runner.ROOT / "var" / "mr1" / "runs"

    evidence = runner._runtime_readiness(
        settings=configured,
        session=object(),
        workspace_root=workspace,
    )

    assert evidence["result"] == "PASS", evidence
    assert events == [
        "exact-db-authority",
        "oauth-token",
        "drive-root-read-only",
    ]
    assert evidence["checks"]["drive_root_folder_listable"] == "PASS"
    assert evidence["checks"]["drive_mutation_free"] == "PASS"
    assert evidence["drive_root_readiness"]["drive_archive_calls"] == 0
    assert "secret-access-token" not in json.dumps(evidence)
