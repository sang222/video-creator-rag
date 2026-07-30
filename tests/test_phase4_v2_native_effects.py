from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.geo_market import DestinationBinding
from app.contracts.production_workflow import (
    ProductionWorkflowProjectStart,
    ProductionWorkflowStage,
)
from app.core.actor import authenticated_actor_context
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.v2_effect import V2ProductionEffectLedger
from app.db.models.workflow import ArtifactVersion
from app.services.production_workflow import (
    ProductionWorkflowCoordinator,
    command_id_for,
)
from app.services.production_package import semantic_hash
from app.services.v2_native_effects import (
    V2_LOCAL_ADAPTER_KEY,
    V2_LOCAL_NARRATION_STRATEGY,
    V2LocalNativeProductionAdapter,
    _advisory_lock_key,
)
from app.services.v2_provider_production import (
    _require_verified_final_media,
    build_v2_provider_production_gateway,
)
from app.services.v2_support_authority import (
    V2GeneratedCitation,
    V2GeneratedClaim,
    V2GeneratedSection,
    V2ProducerReceipt,
    V2SupportAuthorityPrepareCommand,
    V2SupportAuthorityService,
    V2SupportProductionContext,
    V2TrustedSupportDraft,
)
from app.workers.production_workflow import ProductionWorkflowWorker


ROOT = Path(__file__).resolve().parents[1]
QUALIFICATION_SCRIPT = (
    "Verified evidence guides decisions with exact source facts. "
    "Package timing keeps approved statements aligned to policy. "
    "Operators review private media before every manual publication."
)


class _QualificationTrustedProducer:
    """Deterministic no-provider producer for the real local media qualification."""

    def produce(
        self,
        context: V2SupportProductionContext,
    ) -> V2TrustedSupportDraft:
        sentences = [
            value.strip() for value in QUALIFICATION_SCRIPT.split(".") if value.strip()
        ]
        sections = [
            V2GeneratedSection(
                section_id=f"section-{index:03d}",
                heading=f"Verified section {index}",
                narration=sentence + ".",
            )
            for index, sentence in enumerate(sentences, start=1)
        ]
        source = context.frozen_sources[0]
        claims = [
            V2GeneratedClaim(
                claim_id=f"claim-{index:03d}",
                claim_text=section.narration,
                citations=[
                    V2GeneratedCitation(
                        source_ref_id=source.id,
                        source_excerpt=source.fact_statements[0],
                    )
                ],
            )
            for index, section in enumerate(sections, start=1)
        ]
        output = {
            "approved_script_text": QUALIFICATION_SCRIPT,
            "language": context.expected_language,
            "sections": [item.model_dump(mode="json") for item in sections],
            "claims": [item.model_dump(mode="json") for item in claims],
        }
        return V2TrustedSupportDraft(
            approved_script_text=QUALIFICATION_SCRIPT,
            language=context.expected_language,
            sections=sections,
            claims=claims,
            producer_receipt=V2ProducerReceipt(
                producer_type="LLM_ROUTER",
                producer_version="v2-native-qualification-producer.v1",
                lane_name="long_context_text",
                selected_model="injected-no-provider",
                fallback_level="PRIMARY",
                route_attempt_id=uuid.uuid4(),
                producer_input_hash=semantic_hash(context.model_dump(mode="json")),
                producer_output_hash=semantic_hash(output),
            ),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_event_due(
    factory: sessionmaker[Session],
    event_id: Any,
) -> None:
    with factory() as session:
        event = session.get(DomainEvent, event_id)
        assert event is not None
        event.next_attempt_at = utc_now()
        session.commit()


def _event_failure(factory: sessionmaker[Session], event_id: Any) -> str:
    with factory() as session:
        event = session.get(DomainEvent, event_id)
        assert event is not None
        return f"{event.last_error_code}: {event.last_error_summary}"


def _configure_verified_destination(scope: Any) -> None:
    binding = DestinationBinding(
        binding_version=1,
        channel_id=scope.channel.id,
        channel_key=scope.channel.key,
        platform="YOUTUBE",
        platform_account_ref="youtube-account://phase4-v2-native-local",
        platform_channel_id="UC_PHASE4_V2_NATIVE_LOCAL",
        channel_handle="@phase4-v2-native-local",
        target_market_profile_ref="target-market-profile://phase4-v2-native/v1",
        target_market_profile_hash="d" * 64,
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="VERIFIED",
        credential_ref="credential://phase4-v2-native/local",
        verification_state="VERIFIED",
        verification_timestamp="2026-07-29T00:00:00+00:00",
        approval_ref="operator-approval://phase4-v2-native/destination",
    ).model_dump(mode="json")
    scope.channel.metadata_ = {
        **(scope.channel.metadata_ or {}),
        "destination_governance": {
            "active_binding_ref": (f"destination-binding://{scope.channel.key}/v1"),
            "bindings": [binding],
        },
    }


def test_v2_native_real_say_h264_aac_and_archive_effects_reconcile_exactly_once(
    db_session: Session,
    engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Path("/usr/bin/say").is_file()
    assert shutil.which("ffmpeg")
    assert shutil.which("ffprobe")
    monkeypatch.setenv("VCOS_V2_LOCAL_TTS_PATH", "/usr/bin/say")
    monkeypatch.setenv("VCOS_V2_LOCAL_TTS_VOICE", "Samantha")

    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    support = runpy.run_path(str(ROOT / "tests/test_phase4_support_compiler.py"))
    support_scope = support["_new_long_scope_with_approved_script"]
    support_scope.__globals__["_approved_script"] = lambda: QUALIFICATION_SCRIPT
    base = phase3["_scope"](
        db_session,
        minimum_ms=6_000,
        target_ms=12_000,
        maximum_ms=15_000,
    )
    scope = support_scope(db_session, base)
    _configure_verified_destination(scope)
    assert scope.admission.editorial_calendar_slot_id is not None
    V2SupportAuthorityService(
        db_session,
        producer=_QualificationTrustedProducer(),
    ).prepare(
        V2SupportAuthorityPrepareCommand(
            video_project_id=scope.project.id,
            source_type="LONG_FORM_PLAN",
            source_id=scope.admission.editorial_calendar_slot_id,
            actor_user_id=scope.operator.id,
            idempotency_key="phase4-v2-native-support-envelope",
            max_budget_usd="0",
        )
    )
    actor = authenticated_actor_context(
        canonical_user_id=scope.operator.id,
        operator_user_id=scope.operator.id,
        actor_role="OWNER_ADMIN",
        permissions={
            "production.start",
            "production.cancel",
            "production.read",
            "ops.manage",
        },
    )
    started = ProductionWorkflowCoordinator(db_session).start_from_project(
        video_project_id=scope.project.id,
        company_id=scope.company.id,
        data=ProductionWorkflowProjectStart(
            idempotency_key="phase4-v2-native-real-qualification"
        ),
        actor=actor,
    )
    db_session.commit()
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    remaining_crashes = {
        ProductionWorkflowStage.MEDIA: 1,
        ProductionWorkflowStage.RENDER: 1,
        ProductionWorkflowStage.ARCHIVE: 1,
    }

    def crash_after_effect(stage: ProductionWorkflowStage, _effect_dir: Path) -> None:
        if remaining_crashes.get(stage, 0):
            remaining_crashes[stage] -= 1
            raise RuntimeError(f"TEST_CRASH_AFTER_{stage.value}_EFFECT")

    adapter = V2LocalNativeProductionAdapter(
        workspace_root=tmp_path / "v2-production",
        session_factory=factory,
        after_effect_before_ledger_commit=crash_after_effect,
    )
    assert adapter._say_binary == "/usr/bin/say"

    calls = {
        "say": 0,
        "render_ffmpeg": 0,
        "thumbnail_ffmpeg": 0,
        "archive_copy": 0,
    }
    forbid_render_reinvoke = False
    forbid_thumbnail_reinvoke = False
    real_run = subprocess.run
    real_copyfileobj = shutil.copyfileobj

    def tracked_run(argv, *args, **kwargs):
        nonlocal forbid_render_reinvoke, forbid_thumbnail_reinvoke
        values = [str(value) for value in argv]
        executable = Path(values[0]).name if values else ""
        if values and values[0] == "/usr/bin/say":
            calls["say"] += 1
        if executable == "ffmpeg" and "-filter_complex_script" in values:
            if forbid_render_reinvoke:
                raise AssertionError("FFmpeg render effect was invoked twice")
            calls["render_ffmpeg"] += 1
        if (
            executable == "ffmpeg"
            and "-frames:v" in values
            and "image2" in values
            and any(value.endswith(".part.jpg") for value in values)
        ):
            if forbid_thumbnail_reinvoke:
                raise AssertionError("FFmpeg thumbnail effect was invoked twice")
            calls["thumbnail_ffmpeg"] += 1
        return real_run(argv, *args, **kwargs)

    def tracked_copyfileobj(*args, **kwargs):
        calls["archive_copy"] += 1
        if calls["archive_copy"] > 1:
            raise AssertionError("archive copy effect was invoked twice")
        return real_copyfileobj(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", tracked_run)
    monkeypatch.setattr(shutil, "copyfileobj", tracked_copyfileobj)
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id="phase4-v2-native-effects-worker",
        post_readiness_gateway=build_v2_provider_production_gateway(
            adapters={V2_LOCAL_ADAPTER_KEY: adapter}
        ),
    )

    for bootstrap_index in range(6):
        bootstrap = worker.run_once()
        assert bootstrap.status == "DELIVERED", (
            f"bootstrap stage {bootstrap_index}: "
            f"{_event_failure(factory, bootstrap.event_id)}"
        )

    media_failed = worker.run_once()
    assert media_failed.status == "RETRY_SCHEDULED"
    media_effect_dir = adapter._effect_dir(media_failed.command_id)
    audio_path = media_effect_dir / "canonical-narration.aiff"
    narration_receipt_path = media_effect_dir / "narration-command-receipt.json"
    narration_intent_path = media_effect_dir / "narration-command-journal.json"
    assert audio_path.stat().st_size > 4_096
    audio_identity = (
        audio_path.stat().st_ino,
        audio_path.stat().st_mtime_ns,
        _sha256(audio_path),
    )
    narration_intent = json.loads(narration_intent_path.read_text())
    narration_intent_path.write_text(
        json.dumps(
            {
                key: value
                for key, value in narration_intent.items()
                if key
                not in {
                    "audio_checksum",
                    "measured_duration_ms",
                    "receipt_hash",
                }
            }
            | {"state": "EFFECT_STARTED"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    narration_receipt_path.unlink()
    assert narration_intent_path.is_file()
    _make_event_due(factory, media_failed.event_id)
    media_replayed = worker.run_once()
    assert media_replayed.status == "DELIVERED", _event_failure(
        factory, media_replayed.event_id
    )
    assert calls["say"] == 1
    assert audio_identity == (
        audio_path.stat().st_ino,
        audio_path.stat().st_mtime_ns,
        _sha256(audio_path),
    )
    narration_receipt = json.loads(narration_receipt_path.read_text())
    narration_intent = json.loads(narration_intent_path.read_text())
    assert narration_receipt["effect_invocation_count"] == 1
    assert (
        narration_receipt["approved_script_hash"]
        == hashlib.sha256(QUALIFICATION_SCRIPT.encode()).hexdigest()
    )
    assert narration_receipt["duration_ms"] > 0
    assert 6_000 <= narration_receipt["duration_ms"] <= 15_000
    assert narration_intent["state"] == "VERIFIED"

    render_command_id = command_id_for(
        started.id,
        ProductionWorkflowStage.RENDER,
    )
    lock_session = factory()
    try:
        assert lock_session.execute(
            text("SELECT pg_try_advisory_lock(:key)"),
            {"key": _advisory_lock_key(render_command_id)},
        ).scalar_one()
        render_locked = worker.run_once()
        assert render_locked.status == "RETRY_SCHEDULED"
        assert render_locked.command_id == render_command_id
        assert calls["render_ffmpeg"] == 0
        with factory() as check:
            assert (
                check.scalar(
                    select(V2ProductionEffectLedger).where(
                        V2ProductionEffectLedger.command_id == render_command_id
                    )
                )
                is None
            )
    finally:
        lock_session.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _advisory_lock_key(render_command_id)},
        )
        lock_session.close()

    _make_event_due(factory, render_locked.event_id)
    render_failed = worker.run_once()
    assert render_failed.status == "RETRY_SCHEDULED", _event_failure(
        factory, render_failed.event_id
    )
    render_work = adapter.root / "runs" / f"v2-{render_command_id}"
    render_output = render_work / "v2-native-production.mp4"
    assert render_output.is_file(), _event_failure(factory, render_failed.event_id)
    render_identity = (
        render_output.stat().st_ino,
        render_output.stat().st_mtime_ns,
        _sha256(render_output),
    )
    assert calls["render_ffmpeg"] == 1
    render_execution_journal_path = render_work / "v2-render-execution-journal.json"
    render_execution_journal = json.loads(render_execution_journal_path.read_text())
    render_execution_journal_path.write_text(
        json.dumps(
            {
                key: value
                for key, value in render_execution_journal.items()
                if key
                not in {
                    "output_checksum",
                    "execution_receipt_hash",
                    "completed_at",
                    "recovered_after_effect",
                }
            }
            | {"state": "EFFECT_STARTED"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    (render_work / "execution_receipt.json").unlink()
    assert (render_work / "media_qc.json").is_file()
    forbid_render_reinvoke = True
    _make_event_due(factory, render_failed.event_id)
    render_replayed = worker.run_once()
    assert render_replayed.status == "DELIVERED"
    assert calls["render_ffmpeg"] == 1
    assert render_identity == (
        render_output.stat().st_ino,
        render_output.stat().st_mtime_ns,
        _sha256(render_output),
    )
    render_execution_journal = json.loads(render_execution_journal_path.read_text())
    native_qc = json.loads((render_work / "media_qc.json").read_text())
    assert render_execution_journal["effect_invocation_count"] == 1
    assert render_execution_journal["state"] == "VERIFIED"
    assert render_execution_journal["recovered_after_effect"] is True
    assert native_qc["result"] == "PASS"
    assert native_qc["checks"]["video_codec_h264"] is True
    assert native_qc["checks"]["audio_codec"] == "aac"
    assert native_qc["checks"]["sample_rate"] == 48_000
    assert native_qc["checks"]["channels"] == 2
    assert native_qc["checks"]["stream_integrity"] is True
    assert native_qc["checks"]["audio_format_matches_expected"] is True

    qc_completed = worker.run_once()
    assert qc_completed.status == "DELIVERED", _event_failure(
        factory, qc_completed.event_id
    )
    archive_failed = worker.run_once()
    assert archive_failed.status == "RETRY_SCHEDULED"
    render_checksum = _sha256(render_output)
    archive_dir = adapter.root / "archive" / str(scope.project.id)
    archive_output = archive_dir / f"{render_checksum}.mp4"
    thumbnail_output = archive_dir / f"{render_checksum}.jpg"
    archive_identity = (
        archive_output.stat().st_ino,
        archive_output.stat().st_mtime_ns,
        _sha256(archive_output),
    )
    thumbnail_identity = (
        thumbnail_output.stat().st_ino,
        thumbnail_output.stat().st_mtime_ns,
        _sha256(thumbnail_output),
    )
    assert calls["archive_copy"] == 1
    assert calls["thumbnail_ffmpeg"] == 1
    archive_effect_dir = adapter._effect_dir(archive_failed.command_id)
    thumbnail_journal_path = (
        archive_effect_dir / "archive-thumbnail-command-journal.json"
    )
    thumbnail_journal = json.loads(thumbnail_journal_path.read_text())
    thumbnail_journal_path.write_text(
        json.dumps(
            {
                key: value
                for key, value in thumbnail_journal.items()
                if key != "thumbnail_checksum"
            }
            | {"state": "EFFECT_STARTED"},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    forbid_thumbnail_reinvoke = True
    _make_event_due(factory, archive_failed.event_id)
    archive_replayed = worker.run_once()
    assert archive_replayed.status == "DELIVERED", _event_failure(
        factory, archive_replayed.event_id
    )
    assert calls["archive_copy"] == 1
    assert calls["thumbnail_ffmpeg"] == 1
    assert archive_identity == (
        archive_output.stat().st_ino,
        archive_output.stat().st_mtime_ns,
        _sha256(archive_output),
    )
    assert thumbnail_identity == (
        thumbnail_output.stat().st_ino,
        thumbnail_output.stat().st_mtime_ns,
        _sha256(thumbnail_output),
    )
    finalized = worker.run_once()
    assert finalized.status == "DELIVERED", _event_failure(factory, finalized.event_id)

    with factory() as check:
        run = check.get(ProductionWorkflowRun, started.id)
        assert run is not None
        assert run.state == "FINAL_REVIEW_READY"
        assert run.final_review_candidate_id is not None
        ledgers = list(
            check.scalars(
                select(V2ProductionEffectLedger)
                .where(V2ProductionEffectLedger.workflow_run_id == started.id)
                .order_by(V2ProductionEffectLedger.stage)
            )
        )
        assert {row.stage for row in ledgers} == {
            "MEDIA",
            "RENDER",
            "QC",
            "ARCHIVE",
        }
        assert all(
            row.state == "VERIFIED" and row.effect_invocation_count == 1
            for row in ledgers
        )
        ledger_by_stage = {row.stage: row for row in ledgers}
        assert ledger_by_stage["RENDER"].effect_journal["ffmpeg_invocation_count"] == 1
        assert (
            ledger_by_stage["ARCHIVE"].effect_journal["archive_copy_invocation_count"]
            == 1
        )
        assert (
            ledger_by_stage["ARCHIVE"].effect_journal["thumbnail_invocation_count"] == 1
        )

        receipts = list(
            check.scalars(
                select(WorkflowCommandReceipt).where(
                    WorkflowCommandReceipt.workflow_run_id == started.id
                )
            )
        )
        receipt_by_stage = {row.stage: row for row in receipts}
        assert receipt_by_stage["MEDIA"].effect_state == "RECONCILED"
        assert receipt_by_stage["RENDER"].effect_state == "RECONCILED"
        assert receipt_by_stage["ARCHIVE"].effect_state == "RECONCILED"

        final_media = check.get(FinalMediaRef, run.final_media_ref_id)
        assert final_media is not None
        cloud = check.get(CloudMediaRef, final_media.cloud_media_ref_id)
        lineage = check.get(
            ArtifactVersion,
            final_media.lineage_artifact_version_id,
        )
        assert cloud is not None
        assert lineage is not None
        assert cloud.storage_provider == "VCOS_LOCAL_ARCHIVE"
        assert cloud.web_view_link.startswith("vcos-local-archive://")
        assert cloud.technical_appendix["readback_checksum"] == render_checksum
        assert cloud.technical_appendix["thumbnail_relative_ref"] == (
            f"archive/{scope.project.id}/{render_checksum}.jpg"
        )
        assert cloud.technical_appendix["thumbnail_checksum"] == _sha256(
            thumbnail_output
        )
        assert lineage.packaging_metadata["_vcos_domain_authority"]["writer"] == (
            "server_domain_service"
        )
        assert lineage.packaging_metadata["effect_command_id"] == (
            archive_failed.command_id
        )

        timeline = json.loads(
            (
                adapter.root
                / ledger_by_stage["MEDIA"].effect_journal["timeline_relative_path"]
            ).read_text()
        )
        assert timeline["audio_strategy"] == V2_LOCAL_NARRATION_STRATEGY
        assert timeline["narration_present"] is True
        assert timeline["audio_checksum"] == audio_identity[2]
        assert timeline["duration_ms"] == narration_receipt["duration_ms"]
        assert (
            ledger_by_stage["RENDER"].effect_journal["audio_checksum"]
            == (audio_identity[2])
        )
        assert (
            ledger_by_stage["QC"].effect_journal["audio_checksum"]
            == (audio_identity[2])
        )
        assert cloud.technical_appendix["audio_checksum"] == audio_identity[2]
        assert lineage.content["audio_checksum"] == audio_identity[2]
        assert (
            final_media.duration_seconds * 1000
            == (ledger_by_stage["RENDER"].effect_journal["measured_render_duration_ms"])
        )
        assert (
            abs(
                int(final_media.duration_seconds * 1000)
                - narration_receipt["duration_ms"]
            )
            <= 250
        )

        authority_text = json.dumps(
            {
                "ledgers": [row.effect_journal for row in ledgers],
                "cloud": cloud.technical_appendix,
                "lineage": lineage.content,
                "final_media_ref": final_media.file_ref,
            },
            default=str,
        ).casefold()
        assert "fixture://" not in authority_text
        assert "qualification://" not in authority_text
        assert "mr1://" not in authority_text

        assert run.archive_receipt_hash is not None
        _require_verified_final_media(
            check,
            scope.project.id,
            final_media.id,
            expected_checksum=render_checksum,
            expected_archive_hash=run.archive_receipt_hash,
        )
        cloud.storage_provider = "GOOGLE_DRIVE"
        check.flush()
        with pytest.raises(
            ValidationFailureError,
            match="V2_PROVIDER_FINAL_MEDIA_AUTHORITY_MISMATCH",
        ):
            _require_verified_final_media(
                check,
                scope.project.id,
                final_media.id,
                expected_checksum=render_checksum,
                expected_archive_hash=run.archive_receipt_hash,
            )
        check.rollback()
