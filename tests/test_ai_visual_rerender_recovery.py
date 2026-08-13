from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.orm import sessionmaker

from app.contracts.production_workflow import ProductionWorkflowStage
from app.core.actor import _system_worker_actor
from app.core.errors import ValidationFailureError
from app.db.models.ai_visual import (
    AIVisualAssetEffect,
    AIVisualProductionRun,
    AIVisualRerenderAuthority,
)
from app.db.models.foundation import DomainEvent
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.mr1_budget import MR1MonthlyBudgetReservation
from app.db.models.production_publish import FinalReviewCandidate
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.db.models.v2_effect import V2NarrationTimingRecoveryAuthority
from app.services.ai_visual_rerender_authority import (
    seal_ai_visual_rerender_authority_hash,
)
from app.services.ai_visual_rerender_recovery import (
    AIVisualRerenderRecoveryService,
)
from app.services.config_registry import (
    ConfigRegistryService,
    LoadedCatalog,
    content_hash,
)
from app.services.production_workflow import (
    ProductionWorkflowCoordinator,
    semantic_hash,
)
from app.services.v2_narration_timing_recovery import (
    V2NarrationTimingRecoveryService,
)
from app.workers.production_workflow import WorkerRunResult
from tests.qualification.conftest import QualificationFactory
from tests.test_v2_narration_timing_recovery import (
    _ExactForcedAlignmentClient,
    _build_live_shaped_failed_media,
)


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64


def _controlled_actor():
    return _system_worker_actor(
        "vcos-controlled-recovery",
        permissions={"production.start", "production.workflow.execute"},
    )


class _RecordingExactWorker:
    def __init__(self) -> None:
        self.event_ids: list[uuid.UUID] = []

    def run_exact_event(self, *, event_id: uuid.UUID) -> WorkerRunResult:
        self.event_ids.append(event_id)
        return WorkerRunResult(status="PROCESSED", event_id=event_id)


class _ProgressingExactWorker:
    """Offline exact-event handler used to exercise scoped continuation only."""

    _NEXT_STAGE = {
        "VISUAL": "RENDER",
        "RENDER": "QC",
        "QC": "ARCHIVE",
        "ARCHIVE": "FINALIZE",
        "FINALIZE": None,
    }
    _PENDING_STATE = {
        "RENDER": "RENDER_PENDING",
        "QC": "QC_PENDING",
        "ARCHIVE": "ARCHIVE_PENDING",
        "FINALIZE": "ARCHIVE_PENDING",
    }

    def __init__(self, factory, *, now: datetime) -> None:
        self.factory = factory
        self.now = now
        self.stages: list[str] = []
        self.event_ids: list[uuid.UUID] = []

    def run_exact_event(self, *, event_id: uuid.UUID) -> WorkerRunResult:
        with self.factory() as session:
            event = session.get(DomainEvent, event_id)
            assert event is not None and event.workflow_run_id is not None
            run = session.get(ProductionWorkflowRun, event.workflow_run_id)
            assert run is not None
            stage = str((event.payload or {}).get("stage") or "")
            assert stage in self._NEXT_STAGE
            self.stages.append(stage)
            self.event_ids.append(event.id)
            receipt = WorkflowCommandReceipt(
                workflow_run_id=run.id,
                domain_event_id=event.id,
                command_id=event.command_id,
                stage=stage,
                handler_key=str((event.payload or {})["handler_key"]),
                handler_version="ai-visual-rerender-test.v1",
                input_hash=str((event.payload or {})["input_hash"]),
                effect_state="COMPLETED",
                result_type="AI_VISUAL_RERENDER_OFFLINE_TEST",
                result_payload={"stage": stage},
                authority_refs={},
                started_at=self.now,
                completed_at=self.now,
            )
            session.add(receipt)
            event.delivered_at = self.now
            event.published_at = self.now
            event.lease_owner = None
            event.lease_expires_at = None
            event.heartbeat_at = None
            next_stage = self._NEXT_STAGE[stage]
            run.last_progress_at = self.now
            run.projection_version += 1
            if next_stage is None:
                run.state = "FINAL_REVIEW_READY"
                run.current_stage = "FINALIZE"
                run.completed_at = self.now
            else:
                run.state = self._PENDING_STATE[next_stage]
                run.current_stage = next_stage
                ProductionWorkflowCoordinator(
                    session, now=lambda: self.now
                )._schedule_stage(
                    run,
                    ProductionWorkflowStage(next_stage),
                    max_attempts=5,
                    causation_id=event.id,
                )
            session.commit()
            return WorkerRunResult(
                status="PROCESSED",
                event_id=event.id,
                workflow_run_id=run.id,
                command_id=event.command_id,
            )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        monthly_ai_budget_usd=Decimal("250.000000"),
        extra_ai_image_monthly_budget_usd=Decimal("20.000000"),
    )


def _seal_source_candidate(
    session,
    *,
    workflow_id: uuid.UUID,
    now: datetime,
) -> FinalReviewCandidate:
    run = session.get(ProductionWorkflowRun, workflow_id)
    assert run is not None and run.video_project_id is not None
    project = run.video_project_id
    from app.db.models.workflow import VideoProject

    project_row = session.get(VideoProject, project)
    assert project_row is not None
    checksum = semantic_hash({"fixture": "rejected-native-render", "run": str(run.id)})
    media = FinalMediaRef(
        company_id=run.company_id,
        channel_workspace_id=run.channel_workspace_id,
        video_project_id=run.video_project_id,
        production_package_artifact_version_id=(
            run.production_package_artifact_version_id
        ),
        production_package_hash=run.production_package_hash,
        duration_contract=dict(project_row.duration_contract or {}),
        media_type="LONG_FORM_FINAL",
        file_ref=f"fixture://rejected-native/{checksum}",
        duration_seconds=Decimal("420.000000"),
        aspect_ratio="16:9",
        resolution="1920x1080",
        provider_key="native_ffmpeg",
        provider_type="LOCAL_RENDERER_CAPABILITY",
        checksum_sha256=checksum,
        created_at=now,
    )
    session.add(media)
    session.flush()
    destination_id = run.destination_binding_id or uuid.uuid4()
    destination_fingerprint = run.destination_binding_fingerprint or _HASH_A
    candidate_hash = semantic_hash(
        {
            "schema_version": "fixture.rejected-native-candidate.v1",
            "workflow_run_id": str(run.id),
            "final_media_ref_id": str(media.id),
            "final_media_hash": checksum,
        }
    )
    candidate = FinalReviewCandidate(
        workflow_run_id=run.id,
        company_id=run.company_id,
        channel_workspace_id=run.channel_workspace_id,
        video_project_id=project_row.id,
        channel_profile_version_id=project_row.channel_profile_version_id,
        policy_snapshot_id=project_row.policy_snapshot_id,
        production_package_artifact_version_id=(
            run.production_package_artifact_version_id
        ),
        production_package_hash=run.production_package_hash,
        production_readiness_receipt_artifact_version_id=(
            run.production_readiness_receipt_artifact_version_id
        ),
        production_readiness_receipt_hash=run.production_readiness_receipt_hash,
        canonical_media_timeline_ref=run.canonical_media_timeline_ref,
        canonical_media_timeline_hash=run.canonical_media_timeline_hash,
        native_render_plan_ref="fixture://rejected-native/plan",
        native_render_plan_hash=_HASH_B,
        render_output_ref=media.file_ref,
        render_output_checksum=checksum,
        technical_qc_receipt_ref="fixture://rejected-native/technical-qc",
        technical_qc_receipt_hash=_HASH_C,
        creative_qc_receipt_ref="fixture://rejected-native/creative-qc",
        creative_qc_receipt_hash=_HASH_D,
        archive_receipt_ref="fixture://rejected-native/archive",
        archive_receipt_hash=_HASH_A,
        archive_object_ref="fixture://rejected-native/archive-object",
        archive_verification_state="VERIFIED",
        final_media_ref_id=media.id,
        final_media_hash=checksum,
        destination_binding_id=destination_id,
        destination_binding_fingerprint=destination_fingerprint,
        destination_platform_channel_id="fixture-channel",
        destination_account_identity="fixture-account",
        target_platform="YOUTUBE",
        target_surface="LONG_FORM_VIDEO",
        target_market_lineage={},
        production_lane="LONG_FORM",
        content_mode=project_row.content_mode,
        series_plan_id=project_row.series_plan_id,
        series_run_id=project_row.series_run_id,
        episode_number=project_row.episode_number,
        standalone_reason_code=project_row.standalone_reason_code,
        publish_metadata_snapshot={},
        disclosure_snapshot={},
        materiality_policy_snapshot={"fixture": True},
        materiality_policy_hash=_HASH_B,
        candidate_hash=candidate_hash,
        created_at=now,
    )
    session.add(candidate)
    session.flush()
    run.destination_binding_id = destination_id
    run.destination_binding_fingerprint = destination_fingerprint
    run.final_media_ref_id = media.id
    run.final_media_ref_hash = checksum
    run.final_review_candidate_id = candidate.id
    run.final_review_candidate_hash = candidate.candidate_hash
    run.native_render_plan_ref = candidate.native_render_plan_ref
    run.native_render_plan_hash = candidate.native_render_plan_hash
    run.render_output_ref = candidate.render_output_ref
    run.render_output_checksum = candidate.render_output_checksum
    run.technical_qc_receipt_ref = candidate.technical_qc_receipt_ref
    run.technical_qc_receipt_hash = candidate.technical_qc_receipt_hash
    run.creative_qc_receipt_ref = candidate.creative_qc_receipt_ref
    run.creative_qc_receipt_hash = candidate.creative_qc_receipt_hash
    run.archive_receipt_ref = candidate.archive_receipt_ref
    run.archive_receipt_hash = candidate.archive_receipt_hash
    run.archive_object_ref = candidate.archive_object_ref
    run.archive_verification_state = "VERIFIED"
    run.state = "FINAL_REVIEW_READY"
    run.current_stage = "FINALIZE"
    run.completed_at = now
    run.last_progress_at = now
    run.projection_version += 1
    session.commit()
    return candidate


def _source_candidate_fixture(
    *,
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
    expanded_visual_authority: bool = False,
) -> uuid.UUID:
    # The active provider registry now truthfully enables configured AI image
    # production.  The legacy source-lineage fixture predates that cutover and
    # intentionally asserts the old CH1-FLEX qualification boundary.  Supply
    # only that historical projection while constructing the historical row;
    # the rerender service itself reads the current AI-only policy unchanged.
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    with monkeypatch.context() as historical:
        original_validate = ConfigRegistryService.validate_catalog

        def historical_validate(self, path):
            loaded = original_validate(self, path)
            filename = Path(path).name
            if filename != "provider_registry_catalog.yaml":
                return loaded
            projected = deepcopy(loaded.content)
            rows = [
                row
                for row in projected.get("items", [])
                if row.get("provider_key") == "google_gemini_image"
            ]
            assert len(rows) == 1
            rows[0]["policy_fit_blob"]["production_enabled_when_configured"] = False
            return LoadedCatalog(
                path=loaded.path,
                content=projected,
                content_hash=content_hash(projected),
            )

        historical.setattr(
            ConfigRegistryService, "validate_catalog", historical_validate
        )
        # The rejected source predates the additive VISUAL stage. Recreate
        # that historical stage graph only while producing its immutable
        # source receipts; replacement authorization uses the active graph.
        import app.services.production_workflow as production_workflow

        historical.setattr(
            production_workflow,
            "STAGE_SEQUENCE",
            tuple(
                stage
                for stage in production_workflow.STAGE_SEQUENCE
                if stage.value != "VISUAL"
            ),
        )
        workflow_id, _tts, recovery_settings = _build_live_shaped_failed_media(
            db_session=db_session,
            engine=engine,
            qualification_factory=QualificationFactory(db_session),
            monkeypatch=historical,
            workspace_root=workspace_root,
        )
        with factory() as recovery_session:
            V2NarrationTimingRecoveryService(
                recovery_session,
                settings=recovery_settings,
                session_factory=factory,
                audio_probe=lambda _path: 420_000,
                client=_ExactForcedAlignmentClient(),
                workspace_root=workspace_root,
            ).recover(workflow_id, _controlled_actor())
    with factory() as finalize:
        candidate = _seal_source_candidate(
            finalize,
            workflow_id=workflow_id,
            now=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        )
        if expanded_visual_authority:
            original_resolve = AIVisualRerenderRecoveryService._resolve_source_scope

            def resolve_with_explicit_test_authority(service, candidate_id):
                scope = original_resolve(service, candidate_id)
                policy = scope.channel_policy
                budget = policy.budget_policy.model_copy(
                    update={
                        "max_estimated_cost_per_video": 20.0,
                        "max_actual_cost_per_video": 20.0,
                        "max_veo_clips_per_video": 14,
                        "max_veo_seconds_per_video": 112.0,
                        "max_veo_cost_per_video": 11.2,
                    }
                )
                veo = policy.provider_usage_policy.google_veo.model_copy(
                    update={
                        "max_hero_clips_per_video": 14,
                        "max_hero_seconds_per_video": 112.0,
                        "max_hero_cost_usd_per_video": 11.2,
                    }
                )
                usage = policy.provider_usage_policy.model_copy(
                    update={"google_veo": veo}
                )
                expanded = policy.model_copy(
                    update={
                        "budget_policy": budget,
                        "provider_usage_policy": usage,
                    }
                )
                return replace(scope, channel_policy=expanded)

            monkeypatch.setattr(
                AIVisualRerenderRecoveryService,
                "_resolve_source_scope",
                resolve_with_explicit_test_authority,
            )
        return candidate.id


def test_frozen_first_channel_cap_blocks_before_any_visual_write(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_id = _source_candidate_fixture(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    with factory() as session:
        workflow_count = session.scalar(select(func.count(ProductionWorkflowRun.id)))
        budget_count = session.scalar(
            select(func.count(MR1MonthlyBudgetReservation.id))
        )
        service = AIVisualRerenderRecoveryService(
            session,
            settings=_settings(),  # type: ignore[arg-type]
            workspace_root=tmp_path,
        )
        with pytest.raises(
            ValidationFailureError,
            match="AI_VISUAL_RERENDER_APPROVED_BUDGET_INSUFFICIENT",
        ):
            service.authorize(candidate_id, _controlled_actor())
        session.rollback()
        assert session.scalar(select(func.count(AIVisualRerenderAuthority.id))) == 0
        assert session.scalar(select(func.count(AIVisualProductionRun.id))) == 0
        assert session.scalar(select(func.count(AIVisualAssetEffect.id))) == 0
        assert (
            session.scalar(select(func.count(ProductionWorkflowRun.id)))
            == workflow_count
        )
        assert (
            session.scalar(select(func.count(MR1MonthlyBudgetReservation.id)))
            == budget_count
        )


def test_authorize_is_append_only_idempotent_and_schedules_visual_only(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_id = _source_candidate_fixture(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
        expanded_visual_authority=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    with factory() as session:
        source_candidate = session.get(FinalReviewCandidate, candidate_id)
        source = session.get(ProductionWorkflowRun, source_candidate.workflow_run_id)
        source_column_keys = [
            attribute.key for attribute in inspect(ProductionWorkflowRun).column_attrs
        ]
        source_snapshot = {
            key: deepcopy(getattr(source, key)) for key in source_column_keys
        }
        service = AIVisualRerenderRecoveryService(
            session,
            settings=_settings(),  # type: ignore[arg-type]
            now=lambda: now,
            workspace_root=tmp_path,
        )
        created = service.authorize(candidate_id, _controlled_actor())
        replay = service.authorize(candidate_id, _controlled_actor())

        assert created.replayed is False
        assert replay.replayed is True
        assert replay.authority_id == created.authority_id
        assert replay.replacement_workflow_run_id == created.replacement_workflow_run_id
        assert replay.visual_production_run_id == created.visual_production_run_id
        assert replay.budget_reservation_id == created.budget_reservation_id
        assert created.source_workflow_run_id != created.replacement_workflow_run_id

        authority = session.get(AIVisualRerenderAuthority, created.authority_id)
        visual_run = session.get(
            AIVisualProductionRun, created.visual_production_run_id
        )
        replacement = session.get(
            ProductionWorkflowRun, created.replacement_workflow_run_id
        )
        source_after = session.get(
            ProductionWorkflowRun, created.source_workflow_run_id
        )
        budget = session.get(MR1MonthlyBudgetReservation, created.budget_reservation_id)
        events = list(
            session.scalars(
                select(DomainEvent).where(DomainEvent.workflow_run_id == replacement.id)
            ).all()
        )
        assert authority.authority_hash == seal_ai_visual_rerender_authority_hash(
            authority
        )
        assert authority.maximum_total_cost_usd == Decimal("1.554000")
        assert authority.maximum_image_submissions == 14
        assert authority.maximum_video_submissions == 0
        assert authority.maximum_tts_submissions == 0
        assert authority.maximum_forced_alignment_submissions == 0
        assert authority.automatic_publish is False
        assert visual_run.execution_kind == "GOVERNED_RERENDER"
        assert visual_run.state == "AUTHORIZED"
        assert visual_run.audio_ref == authority.audio_ref
        assert visual_run.caption_ref.startswith("effects/")
        assert visual_run.timed_words_ref.startswith("artifact-version://")
        assert visual_run.subtitle_qc_ref.startswith("artifact-version://")
        assert replacement.state == "VISUAL_PENDING"
        assert replacement.current_stage == "VISUAL"
        assert replacement.final_media_ref_id is None
        assert replacement.final_review_candidate_id is None
        assert replacement.completed_at is None
        assert budget.run_id == visual_run.id
        assert budget.reserved_amount == Decimal("1.554000")
        assert budget.provider_allocations_json == {
            "google_gemini_image": "1.554000",
        }
        assert [(event.payload or {}).get("stage") for event in events] == ["VISUAL"]
        assert events[0].max_attempts == 5
        assert (events[0].metadata_ or {})["retry_policy"]["max_attempts"] == 5
        assert all((event.payload or {}).get("stage") != "MEDIA" for event in events)
        assert {
            key: getattr(source_after, key) for key in source_column_keys
        } == source_snapshot
        assert session.get(FinalReviewCandidate, candidate_id).candidate_hash == (
            source_candidate.candidate_hash
        )
        assert session.scalar(select(func.count(AIVisualRerenderAuthority.id))) == 1
        assert session.scalar(select(func.count(AIVisualProductionRun.id))) == 1


def test_run_once_uses_only_the_exact_replacement_event_and_wrong_actor_blocks(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_id = _source_candidate_fixture(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
        expanded_visual_authority=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    recorder = _RecordingExactWorker()
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    with factory() as session:
        service = AIVisualRerenderRecoveryService(
            session,
            settings=_settings(),  # type: ignore[arg-type]
            now=lambda: now,
            workspace_root=tmp_path,
            worker_factory=lambda: recorder,
        )
        authorization = service.authorize(candidate_id, _controlled_actor())
        retry_event = session.get(DomainEvent, authorization.visual_event_id)
        assert retry_event is not None and retry_event.max_attempts == 5
        retry_event.attempt_count = 1
        retry_event.lease_owner = "crashed-rerender-worker"
        retry_event.lease_expires_at = now - timedelta(seconds=1)
        retry_event.heartbeat_at = now - timedelta(seconds=2)
        retry_event.next_attempt_at = now - timedelta(seconds=1)
        session.commit()
        wrong = _system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        )
        with pytest.raises(
            ValidationFailureError,
            match="AI_VISUAL_RERENDER_CONTROLLED_SYSTEM_WORKER_REQUIRED",
        ):
            service.run_once(authorization.authority_id, wrong)
        result = service.run_once(authorization.authority_id, _controlled_actor())

        assert result.event_id == authorization.visual_event_id
        assert result.event_stage == "VISUAL"
        assert recorder.event_ids == [authorization.visual_event_id]
        assert result.worker_result is not None
        assert result.worker_result.status == "PROCESSED"
        replacement_events = list(
            session.scalars(
                select(DomainEvent).where(
                    DomainEvent.workflow_run_id
                    == authorization.replacement_workflow_run_id
                )
            ).all()
        )
        assert len(replacement_events) == 1
        assert (replacement_events[0].payload or {}).get("stage") == "VISUAL"


def test_authorization_blocks_wrong_actor_lineage_timing_and_budget(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_id = _source_candidate_fixture(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
        expanded_visual_authority=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    with factory() as session:
        service = AIVisualRerenderRecoveryService(
            session,
            settings=_settings(),  # type: ignore[arg-type]
            now=lambda: now,
            workspace_root=tmp_path,
        )
        wrong = _system_worker_actor(
            "vcos-durable-worker", permissions={"production.start"}
        )
        with pytest.raises(
            ValidationFailureError,
            match="AI_VISUAL_RERENDER_CONTROLLED_SYSTEM_WORKER_REQUIRED",
        ):
            service.authorize(candidate_id, wrong)

        candidate = session.get(FinalReviewCandidate, candidate_id)
        source = session.get(ProductionWorkflowRun, candidate.workflow_run_id)
        assert source is not None
        lineage_savepoint = session.begin_nested()
        source.final_review_candidate_hash = _HASH_D
        session.flush()
        with pytest.raises(
            ValidationFailureError,
            match="AI_VISUAL_RERENDER_SOURCE_LINEAGE_INVALID",
        ):
            service.authorize(candidate_id, _controlled_actor())
        lineage_savepoint.rollback()
        session.expire_all()

        timing = session.scalar(
            select(V2NarrationTimingRecoveryAuthority).where(
                V2NarrationTimingRecoveryAuthority.workflow_run_id
                == candidate.workflow_run_id
            )
        )
        assert timing is not None
        audio_path = tmp_path / timing.audio_relative_path
        original_audio = audio_path.read_bytes()
        audio_path.write_bytes(original_audio + b"timing-drift")
        try:
            with pytest.raises(
                ValidationFailureError,
                match="AI_VISUAL_RERENDER_SOURCE_MEDIA_DRIFT",
            ):
                service.authorize(candidate_id, _controlled_actor())
        finally:
            audio_path.write_bytes(original_audio)

        budget_savepoint = session.begin_nested()
        low_budget = AIVisualRerenderRecoveryService(
            session,
            settings=SimpleNamespace(
                monthly_ai_budget_usd=Decimal("250.000000"),
                extra_ai_image_monthly_budget_usd=Decimal("0.908999"),
            ),  # type: ignore[arg-type]
            now=lambda: now,
            workspace_root=tmp_path,
        )
        with pytest.raises(
            ValidationFailureError,
            match="AI_VISUAL_RERENDER_BUDGET_CAP_INSUFFICIENT",
        ):
            low_budget.authorize(candidate_id, _controlled_actor())
        budget_savepoint.rollback()
        assert session.scalar(select(func.count(AIVisualRerenderAuthority.id))) == 0


def test_scoped_replay_walks_every_remaining_stage_without_media_or_source_mutation(
    db_session,
    engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    candidate_id = _source_candidate_fixture(
        db_session=db_session,
        engine=engine,
        monkeypatch=monkeypatch,
        workspace_root=tmp_path,
        expanded_visual_authority=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    now = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    progressing = _ProgressingExactWorker(factory, now=now)
    with factory() as session:
        source_candidate = session.get(FinalReviewCandidate, candidate_id)
        source = session.get(ProductionWorkflowRun, source_candidate.workflow_run_id)
        source_keys = [
            attribute.key for attribute in inspect(ProductionWorkflowRun).column_attrs
        ]
        source_snapshot = {key: deepcopy(getattr(source, key)) for key in source_keys}
        candidate_snapshot = {
            attribute.key: deepcopy(getattr(source_candidate, attribute.key))
            for attribute in inspect(FinalReviewCandidate).column_attrs
        }
        service = AIVisualRerenderRecoveryService(
            session,
            settings=_settings(),  # type: ignore[arg-type]
            now=lambda: now,
            workspace_root=tmp_path,
            worker_factory=lambda: progressing,
        )
        authorization = service.authorize(candidate_id, _controlled_actor())
        stages = []
        for _ in range(5):
            step = service.run_once(authorization.authority_id, _controlled_actor())
            stages.append(step.event_stage)
            assert step.worker_result is not None
        finished = service.run_once(authorization.authority_id, _controlled_actor())
        replay = service.authorize(candidate_id, _controlled_actor())

        assert stages == ["VISUAL", "RENDER", "QC", "ARCHIVE", "FINALIZE"]
        assert progressing.stages == stages
        assert len(set(progressing.event_ids)) == 5
        assert finished.event_id is None
        assert finished.workflow_state == "FINAL_REVIEW_READY"
        assert replay.replayed is True
        assert replay.workflow_state == "FINAL_REVIEW_READY"
        events = list(
            session.scalars(
                select(DomainEvent).where(
                    DomainEvent.workflow_run_id
                    == authorization.replacement_workflow_run_id
                )
            ).all()
        )
        assert {str((event.payload or {}).get("stage")) for event in events} == {
            "VISUAL",
            "RENDER",
            "QC",
            "ARCHIVE",
            "FINALIZE",
        }
        assert all(event.max_attempts == 5 for event in events)
        session.expire_all()
        source_after = session.get(ProductionWorkflowRun, source.id)
        candidate_after = session.get(FinalReviewCandidate, candidate_id)
        assert {
            key: getattr(source_after, key) for key in source_keys
        } == source_snapshot
        assert {
            key: getattr(candidate_after, key) for key in candidate_snapshot
        } == candidate_snapshot


def test_final_review_projection_supersedes_the_rejected_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.v2_provider_production as provider_module

    replacement_workflow_id = uuid.uuid4()
    project_id = uuid.uuid4()
    visual_run_id = uuid.uuid4()
    rerender_authority_id = uuid.uuid4()
    rejected_candidate_id = uuid.uuid4()
    destination_id = uuid.uuid4()
    destination_hash = semantic_hash({"destination": "fixture"})
    run = SimpleNamespace(
        id=replacement_workflow_id,
        video_project_id=project_id,
        ai_visual_production_run_id=visual_run_id,
        production_package_artifact_version_id=uuid.uuid4(),
        production_package_hash=_HASH_A,
        production_readiness_receipt_artifact_version_id=uuid.uuid4(),
        production_readiness_receipt_hash=_HASH_B,
        canonical_media_timeline_ref="v2-effect://source/timeline",
        canonical_media_timeline_hash=_HASH_C,
        ai_visual_asset_manifest_hash=_HASH_D,
        ffmpeg_effect_plan_hash=_HASH_A,
        native_render_plan_ref="v2-effect://replacement/assembly-plan",
        native_render_plan_hash=_HASH_B,
        render_output_ref="v2-effect://replacement/render-output",
        render_output_checksum=_HASH_C,
        technical_qc_receipt_ref="v2-effect://replacement/technical-qc",
        technical_qc_receipt_hash=_HASH_D,
        creative_qc_receipt_ref="v2-effect://replacement/creative-qc",
        creative_qc_receipt_hash=_HASH_A,
        archive_receipt_ref="v2-effect://replacement/archive",
        archive_receipt_hash=_HASH_B,
        archive_object_ref="drive://replacement/final.mp4",
        final_media_ref_id=uuid.uuid4(),
        destination_binding_id=destination_id,
        destination_binding_fingerprint=destination_hash,
    )
    project = SimpleNamespace(
        id=project_id,
        production_lane="LONG_FORM",
        planning_source_type="LONG_FORM_PLAN",
    )
    visual_run = SimpleNamespace(
        id=visual_run_id,
        workflow_run_id=replacement_workflow_id,
        video_project_id=project_id,
        rerender_authority_id=rerender_authority_id,
        execution_kind="GOVERNED_RERENDER",
        state="ARCHIVED",
        current_phase="ARCHIVE",
    )
    rerender = SimpleNamespace(
        id=rerender_authority_id,
        replacement_workflow_run_id=replacement_workflow_id,
        source_workflow_run_id=uuid.uuid4(),
        rejected_final_review_candidate_id=rejected_candidate_id,
    )

    class _ProjectionSession:
        def get(self, model, identifier):
            if model.__name__ == "VideoProject":
                assert identifier == project_id
                return project
            if model.__name__ == "AIVisualProductionRun":
                assert identifier == visual_run_id
                return visual_run
            if model.__name__ == "AIVisualRerenderAuthority":
                assert identifier == rerender_authority_id
                return rerender
            raise AssertionError(model)

    destination = {
        "destination_mode": None,
        "destination_status": "VERIFIED",
        "destination_handle": "@fixture",
        "destination_binding_ref": f"destination-binding://{destination_id}",
        "destination_binding_hash": destination_hash,
        "destination_model_hash": destination_hash,
        "destination_authority_hash": destination_hash,
        "publish_execution_allowed": True,
        "automatic_publish": False,
        "platform_channel_id": "fixture-channel",
        "account_identity": "fixture-account",
        "platform": "YOUTUBE",
    }
    monkeypatch.setattr(provider_module, "_require_long_form_context", lambda _: None)
    monkeypatch.setattr(
        provider_module,
        "_authorized_adapter_operation",
        lambda *_args, **_kwargs: SimpleNamespace(
            execution_mode=provider_module.V2_REAL_PRODUCTION_MODE,
            adapter_key="offline-verified-archive",
        ),
    )
    monkeypatch.setattr(
        provider_module,
        "_provider_plan",
        lambda _context: {
            "final_review": {
                "target_surface": "LONG_FORM",
                "publish_metadata_snapshot": {
                    "title": "Governed AI visual replacement",
                    "privacy_status": "PRIVATE",
                },
                "target_market_lineage": {"market": "US"},
                "disclosure_snapshot": {},
            }
        },
    )
    monkeypatch.setattr(
        provider_module,
        "_destination_authority",
        lambda _context: SimpleNamespace(
            id=destination_id,
            content_hash=destination_hash,
            content={},
        ),
    )
    monkeypatch.setattr(
        provider_module,
        "_normalized_destination",
        lambda _content: dict(destination),
    )
    projected = provider_module.PackageBoundV2StageGateway(
        {}
    ).build_final_review_candidate(
        SimpleNamespace(run=run, session=_ProjectionSession())
    )

    assert projected.workflow_run_id == replacement_workflow_id
    assert projected.ai_visual_production_run_id == visual_run_id
    assert projected.ai_visual_asset_manifest_hash == run.ai_visual_asset_manifest_hash
    assert projected.ffmpeg_effect_plan_hash == run.ffmpeg_effect_plan_hash
    assert projected.supersedes_final_review_candidate_id == rejected_candidate_id


def test_authority_hash_binds_identity_created_at_and_actor() -> None:
    created = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    base = {
        column.name: None
        for column in AIVisualRerenderAuthority.__table__.columns
        if column.name != "authority_hash"
    }
    base.update(
        {
            "id": uuid.uuid4(),
            "created_at": created,
            "authorized_by_actor_id": uuid.uuid4(),
            "maximum_total_cost_usd": Decimal("0.999000"),
        }
    )
    sealed = seal_ai_visual_rerender_authority_hash(base)
    assert sealed != seal_ai_visual_rerender_authority_hash(
        {**base, "id": uuid.uuid4()}
    )
    assert sealed != seal_ai_visual_rerender_authority_hash(
        {**base, "created_at": datetime(2026, 8, 14, 9, 0, 1, tzinfo=UTC)}
    )
    assert sealed != seal_ai_visual_rerender_authority_hash(
        {**base, "authorized_by_actor_id": uuid.uuid4()}
    )
