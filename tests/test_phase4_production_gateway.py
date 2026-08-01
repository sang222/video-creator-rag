from __future__ import annotations

import runpy
import uuid
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.contracts.production_publish import FinalReviewCandidateCreateV2
from app.contracts.production_workflow import (
    ProductionWorkflowProjectStart,
    ProductionWorkflowStage,
    WorkflowAuthorityRefs,
    WorkflowStageResult,
)
from app.contracts.vcos_v2 import ProductionLane
from app.core.actor import authenticated_actor_context
from app.core.errors import ValidationFailureError
from app.db.models.production_workflow import (
    ProductionWorkflowRun,
    WorkflowCommandReceipt,
)
from app.services.production_workflow import (
    GatewayBackedPreReadinessStageHandler,
    GatewayBackedPostReadinessStageHandler,
    PreReadinessProductionGatewayDescriptor,
    PostReadinessProductionGatewayDescriptor,
    WorkflowStageError,
    _require_package_bound_retry_authority,
    _validate_gateway_stage_result,
    build_default_stage_handler_registry,
    handler_key_for,
)
from app.services.v2_provider_production import (
    PackageBoundV2StageGateway,
    V2AuthorizedAdapterOperation,
    V2ProductionAdapterDescriptor,
    V2ProviderProductionGateway,
    _authorized_adapter_operation,
    build_v2_provider_production_gateway,
)
from app.workers import production_workflow as worker_runtime
from app.workers.production_workflow import ProductionWorkflowWorker


ROOT = Path(__file__).resolve().parents[1]


class _ConfiguredGateway:
    descriptor = PostReadinessProductionGatewayDescriptor(
        gateway_id="native-drive",
        version="2026.07",
        supported_lanes=frozenset(ProductionLane),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def produce_media(self, _context: object) -> WorkflowStageResult:
        raise AssertionError("not executed by registry-wiring test")

    def render_media(self, _context: object) -> WorkflowStageResult:
        raise AssertionError("not executed by registry-wiring test")

    def run_quality_control(self, _context: object) -> WorkflowStageResult:
        raise AssertionError("not executed by registry-wiring test")

    def archive_media(self, _context: object) -> WorkflowStageResult:
        raise AssertionError("not executed by registry-wiring test")

    def build_final_review_candidate(
        self, _context: object
    ) -> FinalReviewCandidateCreateV2:
        raise AssertionError("not executed by registry-wiring test")


class _ConfiguredPreReadinessGateway:
    descriptor = PreReadinessProductionGatewayDescriptor(
        gateway_id="trusted-package",
        version="2026.07",
        supported_lanes=frozenset(ProductionLane),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.project_id = uuid.uuid4()
        self.admission_id = uuid.uuid4()
        self.package_id = uuid.uuid4()
        self.readiness_id = uuid.uuid4()

    def _refs(self, stage: str) -> WorkflowAuthorityRefs:
        values = {
            "video_project_id": self.project_id,
            "project_admission_decision_id": self.admission_id,
            "project_admission_decision_hash": "a" * 64,
        }
        if stage in {"PACKAGE", "READINESS"}:
            values.update(
                {
                    "production_package_artifact_version_id": (self.package_id),
                    "production_package_hash": "b" * 64,
                }
            )
        if stage == "READINESS":
            values.update(
                {
                    "production_readiness_receipt_artifact_version_id": (
                        self.readiness_id
                    ),
                    "production_readiness_receipt_hash": "c" * 64,
                }
            )
        return WorkflowAuthorityRefs(**values)

    def _result(self, stage: str, context: object) -> WorkflowStageResult:
        self.calls.append((stage, context.command_id))
        return WorkflowStageResult(
            result_type=f"trusted_{stage.lower()}",
            result_ref=f"authority://{stage.lower()}",
            result_hash={
                "RESEARCH": "1" * 64,
                "PACKAGE": "2" * 64,
                "READINESS": "3" * 64,
            }[stage],
            authority_refs=self._refs(stage),
        )

    def produce_support(self, context: object) -> WorkflowStageResult:
        return self._result("RESEARCH", context)

    def create_package(self, context: object) -> WorkflowStageResult:
        return self._result("PACKAGE", context)

    def evaluate_readiness(self, context: object) -> WorkflowStageResult:
        return self._result("READINESS", context)


class _MediaGateway(_ConfiguredGateway):
    descriptor = PostReadinessProductionGatewayDescriptor(
        gateway_id="native-drive",
        version="2026.07",
        supported_lanes=frozenset({ProductionLane.LONG_FORM}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def produce_media(self, _context: object) -> WorkflowStageResult:
        return WorkflowStageResult(
            result_type="canonical_media_timeline",
            result_ref="artifact://canonical-media-timeline/v2",
            result_hash="c" * 64,
            authority_refs=WorkflowAuthorityRefs(
                canonical_media_timeline_ref=("artifact://canonical-media-timeline/v2"),
                canonical_media_timeline_hash="c" * 64,
            ),
        )


class _DeterministicV2Pipeline:
    def __init__(self, lane: ProductionLane):
        self.lane = lane
        self.calls: list[tuple[str, str]] = []

    def _record(self, stage: str, context: object) -> None:
        self.calls.append((stage, context.command_id))
        assert context.run.production_lane == self.lane.value

    def produce_media(self, context: object) -> WorkflowStageResult:
        self._record("MEDIA", context)
        return WorkflowStageResult(
            result_type="canonical_media_timeline",
            result_ref="artifact-version://timeline",
            result_hash="1" * 64,
        )

    def render_media(self, context: object) -> WorkflowStageResult:
        self._record("RENDER", context)
        return WorkflowStageResult(
            result_type="native_render_output",
            result_ref="workspace://production/final.mp4",
            result_hash="2" * 64,
        )

    def run_quality_control(self, context: object) -> WorkflowStageResult:
        self._record("QC", context)
        return WorkflowStageResult(
            result_type="automated_media_qc",
            result_ref="artifact-version://creative-qc",
            result_hash="3" * 64,
        )

    def archive_media(self, context: object) -> WorkflowStageResult:
        self._record("ARCHIVE", context)
        return WorkflowStageResult(
            result_type="verified_drive_archive",
            result_ref="drive://verified/final.mp4",
            result_hash="4" * 64,
        )

    def build_final_review_candidate(
        self, context: object
    ) -> FinalReviewCandidateCreateV2:
        self._record("FINALIZE", context)
        return FinalReviewCandidateCreateV2(
            workflow_run_id=context.run.id,
            production_package_artifact_version_id=uuid.uuid4(),
            production_package_hash="5" * 64,
            production_readiness_receipt_artifact_version_id=uuid.uuid4(),
            production_readiness_receipt_hash="6" * 64,
            canonical_media_timeline_ref="artifact-version://timeline",
            canonical_media_timeline_hash="1" * 64,
            native_render_plan_ref="artifact-version://render-plan",
            native_render_plan_hash="7" * 64,
            render_output_ref="workspace://production/final.mp4",
            render_output_checksum="2" * 64,
            technical_qc_receipt_ref="artifact-version://technical-qc",
            technical_qc_receipt_hash="8" * 64,
            technical_qc_state="PASS",
            creative_qc_receipt_ref="artifact-version://creative-qc",
            creative_qc_receipt_hash="3" * 64,
            creative_qc_state="PASS",
            archive_receipt_ref="artifact-version://archive-receipt",
            archive_receipt_hash="4" * 64,
            archive_object_ref="drive://verified/final.mp4",
            archive_verification_state="VERIFIED",
            final_media_ref_id=uuid.uuid4(),
            destination_binding_id=uuid.uuid4(),
            destination_binding_fingerprint="9" * 64,
            destination_platform_channel_id="channel-v2",
            destination_account_identity="account-v2",
            target_platform="YOUTUBE",
            target_surface="LONG_FORM",
            target_market_lineage={"market": "approved"},
            publish_metadata_snapshot={
                "title": "Approved v2 output",
                "privacy_status": "PRIVATE",
            },
        )


class _RecordingOperationAdapter:
    descriptor = V2ProductionAdapterDescriptor(
        adapter_key="recording-native",
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

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    def execute(
        self,
        *,
        context: object,
        operation: V2AuthorizedAdapterOperation,
    ) -> WorkflowStageResult:
        self.calls.append(
            (
                operation.stage.value,
                operation.operation_id,
                context.command_id,
            )
        )
        return WorkflowStageResult(
            result_type=f"executed_{operation.stage.value.lower()}",
            result_ref=f"authority://{operation.operation_id}",
            result_hash={
                ProductionWorkflowStage.MEDIA: "1" * 64,
                ProductionWorkflowStage.RENDER: "2" * 64,
                ProductionWorkflowStage.QC: "3" * 64,
                ProductionWorkflowStage.ARCHIVE: "4" * 64,
            }[operation.stage],
        )


@pytest.mark.parametrize("lane", list(ProductionLane))
def test_configured_gateway_wires_all_post_readiness_stages(
    lane: ProductionLane,
) -> None:
    gateway = _ConfiguredGateway()
    registry = build_default_stage_handler_registry(post_readiness_gateway=gateway)

    for stage in (
        ProductionWorkflowStage.MEDIA,
        ProductionWorkflowStage.RENDER,
        ProductionWorkflowStage.QC,
        ProductionWorkflowStage.ARCHIVE,
        ProductionWorkflowStage.FINALIZE,
    ):
        handler = registry.require(handler_key_for(lane, stage))
        assert isinstance(handler, GatewayBackedPostReadinessStageHandler)
        assert handler.gateway is gateway
        assert handler.lane == lane
        assert handler.stage == stage
        assert handler.version.endswith("native-drive@2026.07")


def test_trusted_pre_readiness_boundary_invokes_all_three_producers() -> None:
    gateway = _ConfiguredPreReadinessGateway()
    registry = build_default_stage_handler_registry(pre_readiness_gateway=gateway)
    heartbeats: list[bool] = []
    context = SimpleNamespace(
        command_id="package-command",
        run=SimpleNamespace(
            production_lane=ProductionLane.LONG_FORM.value,
            video_project_id=gateway.project_id,
        ),
        ensure_active=lambda: None,
        heartbeat=lambda: heartbeats.append(True),
    )

    for stage in (
        ProductionWorkflowStage.RESEARCH,
        ProductionWorkflowStage.PACKAGE,
        ProductionWorkflowStage.READINESS,
    ):
        handler = registry.require(handler_key_for(ProductionLane.LONG_FORM, stage))
        assert isinstance(handler, GatewayBackedPreReadinessStageHandler)
        assert handler.execute(context).result_type == (
            f"trusted_{stage.value.lower()}"
        )

    assert gateway.calls == [
        ("RESEARCH", context.command_id),
        ("PACKAGE", context.command_id),
        ("READINESS", context.command_id),
    ]
    assert len(heartbeats) == 6


@pytest.mark.parametrize(
    "flag",
    [
        "fixture_only",
        "invokes_mr1",
        "automatic_publish",
    ],
)
def test_gateway_forbidden_capabilities_fail_configuration(
    flag: str,
) -> None:
    values = {
        "gateway_id": "unsafe",
        "version": "1",
        "supported_lanes": frozenset(ProductionLane),
        "production_eligible": True,
        "fixture_only": False,
        "invokes_mr1": False,
        "paid_provider_calls": False,
        "automatic_publish": False,
    }
    values[flag] = True
    with pytest.raises(ValueError, match="PRODUCTION_GATEWAY_FORBIDDEN_CAPABILITY"):
        PostReadinessProductionGatewayDescriptor(**values)


def test_gateway_may_declare_paid_capability_without_granting_an_effect() -> None:
    descriptor = PostReadinessProductionGatewayDescriptor(
        gateway_id="paid-capable",
        version="1",
        supported_lanes=frozenset(ProductionLane),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=True,
        automatic_publish=False,
    )

    assert descriptor.paid_provider_calls is True


def test_paid_operation_requires_matching_provider_and_budget_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(
        run=SimpleNamespace(production_lane=ProductionLane.LONG_FORM.value)
    )
    plan = {
        "paid_provider_calls": True,
        "adapter_operations": {
            "MEDIA": {
                "schema_version": "vcos.provider-adapter-operation.v1",
                "execution_authorized": True,
                "production_eligible": True,
                "fixture_only": False,
                "invokes_mr1": False,
                "automatic_publish": False,
                "stage": "MEDIA",
                "production_lane": "LONG_FORM",
                "paid_provider_call": True,
                "operation_id": "narration:001",
                "adapter_key": "elevenlabs-v2",
                "max_cost_usd": "1.50",
                "parameters": {"voice_profile_ref": "voice-profile://approved"},
            }
        },
    }
    budget = {
        "schema_version": "vcos.operation-budget-authority.v1",
        "budget_authorized": True,
        "remaining_budget_usd": "2.00",
        "operation_authorizations": {
            "narration:001": {
                "authorized": True,
                "operation_id": "narration:001",
                "adapter_key": "elevenlabs-v2",
                "stage": "MEDIA",
                "paid_provider_call": True,
                "max_cost_usd": "1.50",
            }
        },
    }
    monkeypatch.setattr(
        "app.services.v2_provider_production._provider_plan",
        lambda _context: plan,
    )
    monkeypatch.setattr(
        "app.services.v2_provider_production._budget_authority",
        lambda _context: budget,
    )

    operation = _authorized_adapter_operation(context, ProductionWorkflowStage.MEDIA)

    assert operation.operation_id == "narration:001"
    assert operation.max_cost_usd == Decimal("1.50")
    budget["operation_authorizations"]["narration:001"]["adapter_key"] = (
        "different-adapter"
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_PROVIDER_OPERATION_BUDGET_NOT_AUTHORIZED",
    ):
        _authorized_adapter_operation(context, ProductionWorkflowStage.MEDIA)


def test_worker_runtime_resolves_explicit_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _ConfiguredGateway()
    module = SimpleNamespace(build_gateway=lambda: gateway)
    monkeypatch.setattr(
        worker_runtime.importlib,
        "import_module",
        lambda module_name: (
            module
            if module_name == "approved.production"
            else pytest.fail("unexpected module")
        ),
    )

    loaded = worker_runtime.load_post_readiness_gateway_from_env(
        "approved.production:build_gateway"
    )

    assert loaded is gateway


def test_worker_runtime_missing_or_invalid_factory_fails_closed() -> None:
    assert worker_runtime.load_post_readiness_gateway_from_env("") is None
    with pytest.raises(
        RuntimeError,
        match="must be python.module:factory",
    ):
        worker_runtime.load_post_readiness_gateway_from_env("../../untrusted:factory")


def test_gateway_cannot_relabel_fixture_reference_as_production() -> None:
    run = SimpleNamespace(
        production_package_artifact_version_id=object(),
        production_package_hash="a" * 64,
        production_readiness_receipt_artifact_version_id=object(),
        production_readiness_receipt_hash="b" * 64,
    )
    result = WorkflowStageResult(
        result_type="canonical_media_timeline",
        result_ref="fixture://forbidden/timeline",
        result_hash="c" * 64,
        authority_refs=WorkflowAuthorityRefs(
            canonical_media_timeline_ref="fixture://forbidden/timeline",
            canonical_media_timeline_hash="c" * 64,
        ),
    )

    with pytest.raises(WorkflowStageError, match="fixture or qualification-only"):
        _validate_gateway_stage_result(
            session=None,  # type: ignore[arg-type]
            run=run,  # type: ignore[arg-type]
            stage=ProductionWorkflowStage.MEDIA,
            result=result,
        )


def test_worker_uses_configured_gateway_after_exact_readiness(
    db_session: Session,
    engine,
) -> None:
    phase3 = runpy.run_path(str(ROOT / "tests/test_phase3_production_package_v2.py"))
    scope = phase3["_scope"](db_session)
    package = phase3["_create_package"](db_session, scope)
    scope.pause_active_launch_run(db_session)
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
    gateway = _MediaGateway()
    started = worker_runtime.ProductionWorkflowCoordinator(
        db_session,
        handlers=build_default_stage_handler_registry(post_readiness_gateway=gateway),
    ).start_from_project(
        video_project_id=scope.project.id,
        company_id=scope.company.id,
        data=ProductionWorkflowProjectStart(
            idempotency_key="configured-gateway-one-action"
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
    worker = ProductionWorkflowWorker(
        post_readiness_gateway=gateway,
        session_factory=factory,
        worker_id="configured-gateway-worker",
    )

    for _ in range(7):
        assert worker.run_once().status == "DELIVERED"

    with factory() as check:
        run = check.get(ProductionWorkflowRun, started.id)
        assert run is not None
        assert run.current_stage == ProductionWorkflowStage.RENDER.value
        assert run.canonical_media_timeline_ref == (
            "artifact://canonical-media-timeline/v2"
        )
        assert run.production_package_artifact_version_id == (
            package.artifact_version_id
        )
        media_receipt = check.scalar(
            select(WorkflowCommandReceipt).where(
                WorkflowCommandReceipt.workflow_run_id == run.id,
                WorkflowCommandReceipt.stage == ProductionWorkflowStage.MEDIA.value,
            )
        )
        assert media_receipt is not None
        assert media_receipt.handler_version.endswith("native-drive@2026.07")


@pytest.mark.parametrize("lane", list(ProductionLane))
def test_concrete_v2_gateway_executes_injected_pipeline_for_every_lane(
    lane: ProductionLane,
) -> None:
    pipeline = _DeterministicV2Pipeline(lane)
    gateway = V2ProviderProductionGateway(
        media=pipeline,
        renderer=pipeline,
        quality_control=pipeline,
        archive=pipeline,
        presentation=pipeline,
    )
    context = SimpleNamespace(
        command_id=f"command-{lane.value.lower()}",
        run=SimpleNamespace(
            id=uuid.uuid4(),
            production_lane=lane.value,
        ),
    )

    assert gateway.produce_media(context).result_type == ("canonical_media_timeline")
    assert gateway.render_media(context).result_type == "native_render_output"
    assert gateway.run_quality_control(context).result_type == ("automated_media_qc")
    assert gateway.archive_media(context).result_type == ("verified_drive_archive")
    candidate = gateway.build_final_review_candidate(context)

    assert candidate.workflow_run_id == context.run.id
    assert [stage for stage, _command_id in pipeline.calls] == [
        "MEDIA",
        "RENDER",
        "QC",
        "ARCHIVE",
        "FINALIZE",
    ]
    assert {command_id for _stage, command_id in pipeline.calls} == {context.command_id}


@pytest.mark.parametrize("lane", list(ProductionLane))
def test_package_bound_gateway_executes_authorized_adapter_operations(
    monkeypatch: pytest.MonkeyPatch,
    lane: ProductionLane,
) -> None:
    adapter = _RecordingOperationAdapter()
    gateway = PackageBoundV2StageGateway({"recording-native": adapter})
    context = SimpleNamespace(
        command_id=f"command-{lane.value.lower()}",
        run=SimpleNamespace(
            production_lane=lane.value,
            planning_source_type="LONG_FORM_PLAN",
        ),
        ensure_active=lambda: None,
    )

    def operation(
        _context: object, stage: ProductionWorkflowStage
    ) -> V2AuthorizedAdapterOperation:
        return V2AuthorizedAdapterOperation(
            operation_id=f"{lane.value.lower()}:{stage.value.lower()}",
            stage=stage,
            adapter_key="recording-native",
            paid_provider_call=False,
            max_cost_usd=Decimal("0"),
            parameters={"lane": lane.value},
        )

    monkeypatch.setattr(
        "app.services.v2_provider_production._authorized_adapter_operation",
        operation,
    )
    for stage, execute in (
        (ProductionWorkflowStage.MEDIA, gateway.produce_media),
        (ProductionWorkflowStage.RENDER, gateway.render_media),
        (ProductionWorkflowStage.QC, gateway.run_quality_control),
    ):
        result = execute(context)
        assert result.result_type == f"executed_{stage.value.lower()}"
        assert result.result_payload["provider_operation_id"] == (
            f"{lane.value.lower()}:{stage.value.lower()}"
        )
        assert result.result_payload["effect_idempotency_key"] == (context.command_id)

    assert adapter.calls == [
        (
            stage.value,
            f"{lane.value.lower()}:{stage.value.lower()}",
            context.command_id,
        )
        for stage in (
            ProductionWorkflowStage.MEDIA,
            ProductionWorkflowStage.RENDER,
            ProductionWorkflowStage.QC,
        )
    ]


def test_normal_worker_factory_is_in_repo_concrete_gateway() -> None:
    gateway = build_v2_provider_production_gateway()
    assert isinstance(gateway, V2ProviderProductionGateway)
    worker = ProductionWorkflowWorker(
        session_factory=lambda: pytest.fail("not opened during construction"),
    )
    handler = worker.handlers.require(
        handler_key_for(
            ProductionLane.LONG_FORM,
            ProductionWorkflowStage.MEDIA,
        )
    )
    assert isinstance(handler, GatewayBackedPostReadinessStageHandler)
    assert isinstance(handler.gateway, V2ProviderProductionGateway)
    pre_handler = worker.handlers.require(
        handler_key_for(
            ProductionLane.LONG_FORM,
            ProductionWorkflowStage.RESEARCH,
        )
    )
    assert isinstance(pre_handler, GatewayBackedPreReadinessStageHandler)


def test_retry_requires_affirmative_package_policy_and_remaining_budget() -> None:
    provider = {
        "retry_authorized": True,
        "max_attempts": 3,
        "retry_cost_usd": "1.25",
    }
    budget = {
        "retry_authorized": True,
        "max_attempts": 2,
        "remaining_budget_usd": "2.00",
    }
    _require_package_bound_retry_authority(
        attempt_count=2,
        provider_content=provider,
        budget_content=budget,
    )

    with pytest.raises(
        WorkflowStageError,
        match="affirmatively authorize retry",
    ):
        _require_package_bound_retry_authority(
            attempt_count=2,
            provider_content={**provider, "retry_authorized": False},
            budget_content=budget,
        )
    with pytest.raises(
        WorkflowStageError,
        match="cannot fund another attempt",
    ):
        _require_package_bound_retry_authority(
            attempt_count=2,
            provider_content=provider,
            budget_content={
                **budget,
                "remaining_budget_usd": "1.00",
            },
        )
