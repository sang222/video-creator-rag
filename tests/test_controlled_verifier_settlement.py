from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import sessionmaker

from app.contracts.m5 import (
    ContextPackSnapshotCreate,
    IdeaMarketPreflightCreate,
    RetrievalPlanSnapshotCreate,
)
from app.contracts.production_package import (
    ProductionDurationContractV2,
    ProductionPackageContentV2,
)
from app.contracts.production_publish import (
    FinalVideoDecisionCreate,
    FinalVideoDecisionValue,
    HumanUploadTaskStartV2,
    ManualPublishConfirmationCreateV2,
    ManualPublishVerificationV2,
)
from app.contracts.production_workflow import (
    WorkflowAuthorityRefs,
    WorkflowStageResult,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.contracts.vcos_v2 import ProductionLane
from app.contracts.script_qualification import (
    QualifiedScriptOutputV2,
    SemanticVerificationOutput,
)
from app.core.errors import ValidationFailureError
from app.core.actor import _system_worker_actor
from app.db.models.foundation import DomainEvent
from app.db.models.launch_cadence import LongFormPublishSlot
from app.db.models.m5 import (
    ContextPackSnapshot,
    EditorialIdeaCandidate,
    EditorialResearchRun,
    IdeaMarketPreflight,
)
from app.db.models.ops import ProviderAttempt
from app.db.models.m10_1 import HumanUploadTask
from app.db.models.m10_2 import FinalMediaRef
from app.db.models.m10_5 import CloudMediaRef
from app.db.models.m7 import ManualPublishConfirmation, UploadedVideo
from app.db.models.production_publish import FinalReviewCandidate, FinalVideoDecision
from app.db.models.production_workflow import ProductionWorkflowRun
from app.db.models.script_qualification import (
    ControlledVerifierSettlementAuthority,
    ScriptQualificationBackgroundAttempt,
    ScriptQualificationProviderResponseSnapshot,
    ScriptQualificationReceipt,
)
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.config_registry import content_hash
from app.services.launch_cadence import LongFormCadenceService
from app.services.m5 import IdeaMarketPreflightService, ResourceResolverService
from app.services.operator_cockpit import OperatorCockpitService
from app.services.production_publish import ProductionPublishService
from app.services.production_workflow import PostReadinessProductionGatewayDescriptor
from app.services.production_package import ProductionPackageService
from app.services.script_contract_replacement import (
    CONTROLLED_VERIFIER_SETTLEMENT_POLICY,
    CONTROLLED_VERIFIER_SETTLEMENT_REASON,
    CONTROLLED_VERIFIER_SETTLEMENT_SCHEMA,
    controlled_verifier_settlement_authority_body,
    resolve_replacement_qualification_leaf,
)
from app.services.script_qualification import ScriptQualificationService
from app.services.script_qualification_background import (
    ScriptQualificationBackgroundService,
)
from app.services.script_qualification_recovery import (
    ScriptQualificationRecoveryService,
)
from app.services.script_verifier_settlement import (
    ScriptVerifierSettlementRecoveryService,
    derive_v3_semantic_receipts,
)
from app.services.v2_support_authority import (
    V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
    V2FrozenSupportEnvelope,
    V2SupportAuthorityService,
    V2SupportProductionContext,
)
from app.services.v2_provider_production import (
    PackageBoundV2StageGateway,
    _normalized_destination,
)
from app.services.v2_drive_archive import (
    V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
    V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
    V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY,
)
from app.services.workflow import ArtifactService
import app.services.v2_support_authority as v2_support_authority
from app.workers.production_workflow import ProductionWorkflowWorker
from tests.qualification.conftest import QualificationFactory
from tests.test_controlled_production_continuation import (
    _build_continuation_lineage,
    _completed_attempt_with_snapshot,
    _fresh_attempt,
)
import tests.test_controlled_production_continuation as continuation_test
from tests.test_long_form_launch_cadence import (
    _actor,
    _configure_verified_destination,
    _test_support_authority_preparer,
)


_PARAPHRASE_CLAIM_2 = "Function calling connects a model to external tools and APIs."
_EXACT_CLAIM_2 = "Function calling lets you connect models to external tools and APIs."
_PARAPHRASE_CLAIM_3 = (
    "The documented examples include actions such as scheduling an appointment, "
    "creating an invoice, or sending an email."
)
_EXACT_CLAIM_3 = (
    "The documented action examples also include scheduling appointments, "
    "creating invoices, and sending emails."
)
_QUESTION_ALTERNATE = (
    "It is whether the workflow can turn unstructured text into predictable data, "
    "and then connect that data to an action without treating generated prose as "
    "an executable instruction."
)
_SHARED_FULFILLMENT_SPAN = (
    "Taken together, the workflow answers the central question without requiring "
    "a previous episode."
)


@pytest.fixture
def qualification_factory(db_session):
    return QualificationFactory(db_session)


def _install_research_context_source(monkeypatch) -> None:
    """Give the source the real immutable RESEARCH context present in production."""

    original = continuation_test._expired_zero_effect_source

    def source_with_context(session, qualification_factory, **kwargs):
        source = original(session, qualification_factory, **kwargs)
        research = session.get(
            EditorialResearchRun,
            source.candidate.editorial_research_run_id,
        )
        assert research is not None and research.editorial_calendar_slot_id is not None
        resolver = ResourceResolverService(session)
        plan = resolver.create_retrieval_plan(
            data=RetrievalPlanSnapshotCreate(
                purpose="EDITORIAL_RESEARCH",
                company_id=source.candidate.company_id,
                channel_workspace_id=source.candidate.channel_workspace_id,
                channel_profile_version_id=research.channel_profile_version_id,
                policy_snapshot_id=source.candidate.policy_snapshot_id,
                editorial_calendar_slot_id=research.editorial_calendar_slot_id,
                allowed_sources=[
                    "channel_profile",
                    "policy_snapshot",
                    "editorial_slot",
                    "niche_contract_digest",
                ],
                source_order=[
                    "channel_profile",
                    "policy_snapshot",
                    "editorial_slot",
                    "niche_contract_digest",
                ],
            ),
            correlation_id=f"settlement-test-research-context:{source.candidate.id}:plan",
        )
        context = resolver.build_context_pack(
            data=ContextPackSnapshotCreate(
                retrieval_plan_snapshot_id=plan.id,
                freshness_state="FRESH",
                confidence_level="HIGH",
            ),
            correlation_id=f"settlement-test-research-context:{source.candidate.id}:pack",
        )
        research.context_pack_snapshot_id = context.id
        source.candidate.context_pack_snapshot_id = context.id
        source.research_context = context
        session.flush()
        return source

    monkeypatch.setattr(
        continuation_test,
        "_expired_zero_effect_source",
        source_with_context,
    )


def _install_ready_finalization_authorities(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.script_verifier_settlement.resolve_provider_authority",
        lambda *_args, **_kwargs: continuation_test._ready_snapshot(),
    )
    ready_budget = {
        "state": "READY",
        "decision": "PASS",
        "max_estimated_cost_per_video": "10.00",
        "authority": "focused-settlement-finalization-test",
    }
    for target in (
        "app.services.script_verifier_settlement.resolve_budget_authority",
        "app.services.launch_cadence.resolve_budget_authority",
        "app.services.vcos_v2.resolve_budget_authority",
    ):
        monkeypatch.setattr(
            target,
            lambda *_args, **_kwargs: ready_budget,
        )


class _LocalReviewOnlyPostReadinessGateway:
    """Offline producer with a realistic remote-Drive final authority."""

    descriptor = PostReadinessProductionGatewayDescriptor(
        gateway_id="review-only-boundary",
        version="1.0.0",
        supported_lanes=frozenset({ProductionLane.LONG_FORM}),
        production_eligible=True,
        fixture_only=False,
        invokes_mr1=False,
        paid_provider_calls=False,
        automatic_publish=False,
    )

    def __init__(self) -> None:
        self._presentation = PackageBoundV2StageGateway()

    @staticmethod
    def _hash(context, label: str) -> str:
        return content_hash(
            {
                "workflow_run_id": str(context.run.id),
                "authority": label,
            }
        )

    def produce_media(self, context) -> WorkflowStageResult:
        timeline_hash = self._hash(context, "canonical-media-timeline")
        timeline_ref = f"local-authority://timeline/{timeline_hash}"
        return WorkflowStageResult(
            result_type="local_canonical_media_timeline",
            result_ref=timeline_ref,
            result_hash=timeline_hash,
            authority_refs=WorkflowAuthorityRefs(
                canonical_media_timeline_ref=timeline_ref,
                canonical_media_timeline_hash=timeline_hash,
            ),
        )

    def render_media(self, context) -> WorkflowStageResult:
        plan_hash = self._hash(context, "native-render-plan")
        output_hash = self._hash(context, "render-output")
        output_ref = f"local-authority://render/{output_hash}"
        return WorkflowStageResult(
            result_type="local_render_output",
            result_ref=output_ref,
            result_hash=output_hash,
            authority_refs=WorkflowAuthorityRefs(
                native_render_plan_ref=f"local-authority://render-plan/{plan_hash}",
                native_render_plan_hash=plan_hash,
                render_output_ref=output_ref,
                render_output_checksum=output_hash,
            ),
        )

    def run_quality_control(self, context) -> WorkflowStageResult:
        technical_hash = self._hash(context, "technical-qc")
        creative_hash = self._hash(context, "creative-qc")
        creative_ref = f"local-authority://creative-qc/{creative_hash}"
        return WorkflowStageResult(
            result_type="local_quality_control",
            result_ref=creative_ref,
            result_hash=creative_hash,
            authority_refs=WorkflowAuthorityRefs(
                technical_qc_receipt_ref=(
                    f"local-authority://technical-qc/{technical_hash}"
                ),
                technical_qc_receipt_hash=technical_hash,
                creative_qc_receipt_ref=creative_ref,
                creative_qc_receipt_hash=creative_hash,
            ),
        )

    def archive_media(self, context) -> WorkflowStageResult:
        run = context.run
        assert run.video_project_id is not None
        assert run.production_package_artifact_version_id is not None
        assert run.production_package_hash is not None
        assert run.render_output_checksum is not None
        package = ProductionPackageService(context.session).validate_for_readiness(
            run.production_package_artifact_version_id
        )
        assert package.destination_binding_ref.artifact_version_id is not None
        destination_version = context.session.get(
            ArtifactVersion,
            package.destination_binding_ref.artifact_version_id,
        )
        assert destination_version is not None
        destination = _normalized_destination(destination_version.content)
        archive_hash = self._hash(context, "archive-receipt")
        duration_ms = package.duration_contract.target_duration_ms
        drive_file_id = f"review-only-{run.render_output_checksum}"
        object_ref = f"drive://{drive_file_id}/final.mp4"
        cloud = CloudMediaRef(
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            video_project_id=run.video_project_id,
            media_type="LONG_FORM_FINAL",
            storage_provider="GOOGLE_DRIVE",
            drive_file_id=drive_file_id,
            web_view_link=(f"https://drive.google.com/file/d/{drive_file_id}/view"),
            mime_type="video/mp4",
            file_name="final.mp4",
            size_bytes=1024,
            checksum_sha256=run.render_output_checksum,
            local_source_path_hash=run.render_output_checksum,
            upload_status="VERIFIED",
            verification_status="CHECKSUM_VERIFIED",
            source_refs=[
                {
                    "type": "v2_render_output",
                    "workflow_run_id": str(run.id),
                    "render_output_ref": run.render_output_ref,
                    "render_output_checksum": run.render_output_checksum,
                    "production_package_artifact_version_id": str(
                        run.production_package_artifact_version_id
                    ),
                    "production_package_hash": run.production_package_hash,
                }
            ],
            technical_appendix={
                "drive_file_id_verified": True,
                "size_verified": True,
                "checksum_verified": True,
                "measured_render_duration_ms": duration_ms,
                "v2_remote_archive": True,
            },
        )
        context.session.add(cloud)
        context.session.flush()
        caption_checksum = self._hash(context, "canonical-caption-sidecar")
        caption_ref = f"artifact-version://caption/{caption_checksum}"
        caption_artifact_hash = self._hash(context, "caption-artifact")
        subtitle_qc_ref = f"artifact-version://subtitle-qc/{caption_checksum}"
        subtitle_qc_hash = self._hash(context, "subtitle-qc")
        caption_file_id = f"review-only-caption-{caption_checksum}"
        caption_object_ref = f"drive://{caption_file_id}/canonical-captions.srt"
        caption_cloud = CloudMediaRef(
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            video_project_id=run.video_project_id,
            media_type="CAPTION",
            storage_provider="GOOGLE_DRIVE",
            drive_file_id=caption_file_id,
            web_view_link=(f"https://drive.google.com/file/d/{caption_file_id}/view"),
            mime_type="application/x-subrip",
            file_name="canonical-captions.srt",
            size_bytes=512,
            checksum_sha256=caption_checksum,
            local_source_path_hash=caption_checksum,
            upload_status="VERIFIED",
            verification_status="CHECKSUM_VERIFIED",
            source_refs=[
                {
                    "type": "v2_caption_sidecar",
                    "workflow_run_id": str(run.id),
                    "caption_ref": caption_ref,
                    "caption_checksum": caption_checksum,
                    "caption_artifact_hash": caption_artifact_hash,
                    "subtitle_qc_ref": subtitle_qc_ref,
                    "subtitle_qc_hash": subtitle_qc_hash,
                }
            ],
            technical_appendix={
                "drive_file_id_verified": True,
                "size_verified": True,
                "checksum_verified": True,
                "v2_caption_sidecar": True,
                "caption_ref": caption_ref,
                "caption_artifact_hash": caption_artifact_hash,
                "subtitle_qc_ref": subtitle_qc_ref,
                "subtitle_qc_hash": subtitle_qc_hash,
            },
        )
        context.session.add(caption_cloud)
        context.session.flush()
        lineage_content = {
            "schema_version": V2_DRIVE_ARCHIVE_LINEAGE_SCHEMA,
            "workflow_run_id": str(run.id),
            "archive_command_id": context.command_id,
            "provider_operation_id": f"v2:{run.video_project_id}:archive",
            "video_project_id": str(run.video_project_id),
            "production_package_artifact_version_id": str(
                run.production_package_artifact_version_id
            ),
            "production_package_hash": run.production_package_hash,
            "duration_contract": package.duration_contract.model_dump(mode="json"),
            "canonical_media_timeline_hash": run.canonical_media_timeline_hash,
            "native_render_plan_hash": run.native_render_plan_hash,
            "render_output_ref": run.render_output_ref,
            "render_output_checksum": run.render_output_checksum,
            "measured_render_duration_ms": duration_ms,
            "technical_qc_hash": run.technical_qc_receipt_hash,
            "creative_qc_hash": run.creative_qc_receipt_hash,
            "archive_receipt_hash": archive_hash,
            "archive_state": "VERIFIED",
            "cloud_media_ref_id": str(cloud.id),
            "archive_object_ref": object_ref,
            "storage_provider": "GOOGLE_DRIVE",
            "caption_ref": caption_ref,
            "caption_checksum": caption_checksum,
            "caption_artifact_hash": caption_artifact_hash,
            "subtitle_qc_ref": subtitle_qc_ref,
            "subtitle_qc_hash": subtitle_qc_hash,
            "caption_cloud_media_ref_id": str(caption_cloud.id),
            "caption_archive_object_ref": caption_object_ref,
            "invokes_mr1": False,
            "automatic_publish": False,
            "external_effect_performed": True,
        }
        lineage_artifact = ArtifactService(context.session).create_artifact(
            data=ArtifactCreate(
                video_project_id=run.video_project_id,
                artifact_type=V2_DRIVE_ARCHIVE_LINEAGE_ARTIFACT_TYPE,
                status="approved",
                created_by_user_id=destination_version.created_by_user_id,
            ),
            correlation_id=f"review-only-drive-lineage-{context.command_id}",
            trusted_authority_write=True,
        )
        lineage = ArtifactService(context.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=lineage_artifact.id,
                content=lineage_content,
                status="approved",
                created_by_user_id=destination_version.created_by_user_id,
            ),
            correlation_id=f"review-only-drive-version-{context.command_id}",
            trusted_authority_write=True,
        )
        lineage_artifact.status = "approved"
        context.session.flush()
        media = FinalMediaRef(
            company_id=run.company_id,
            channel_workspace_id=run.channel_workspace_id,
            video_project_id=run.video_project_id,
            production_package_artifact_version_id=(
                run.production_package_artifact_version_id
            ),
            production_package_hash=run.production_package_hash,
            duration_contract=package.duration_contract.model_dump(mode="json"),
            media_type="LONG_FORM_FINAL",
            file_ref=object_ref,
            duration_seconds=Decimal(duration_ms) / Decimal(1000),
            aspect_ratio="16:9",
            resolution="1920x1080",
            provider_key=V2_GOOGLE_DRIVE_REMOTE_ADAPTER_KEY,
            provider_type="MEDIA_STORAGE",
            checksum_sha256=run.render_output_checksum,
            cloud_media_ref_id=cloud.id,
            lineage_artifact_version_id=lineage.id,
        )
        context.session.add(media)
        context.session.flush()
        return WorkflowStageResult(
            result_type="offline_remote_drive_verified_archive",
            result_id=media.id,
            result_ref=object_ref,
            result_hash=archive_hash,
            authority_refs=WorkflowAuthorityRefs(
                archive_receipt_ref=f"local-authority://archive-receipt/{archive_hash}",
                archive_receipt_hash=archive_hash,
                archive_object_ref=object_ref,
                archive_verification_state="VERIFIED",
                final_media_ref_id=media.id,
                final_media_ref_hash=run.render_output_checksum,
                destination_binding_id=destination_version.id,
                destination_binding_fingerprint=destination_version.content_hash,
                destination_binding=destination,
            ),
        )

    def build_final_review_candidate(self, context):
        return self._presentation.build_final_review_candidate(context)


def _run_local_post_readiness_to_final_review(engine, workflow_id: uuid.UUID) -> None:
    class _FailFastWorker(ProductionWorkflowWorker):
        def _record_failure(self, _claim, error):
            raise error

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    worker = _FailFastWorker(
        post_readiness_gateway=_LocalReviewOnlyPostReadinessGateway(),
        session_factory=factory,
        worker_id=f"review-only-finalization-{workflow_id}",
    )
    for expected_stage in ("MEDIA", "RENDER", "QC", "ARCHIVE", "FINALIZE"):
        with factory() as session:
            workflow = session.get(ProductionWorkflowRun, workflow_id)
            assert workflow is not None and workflow.current_stage == expected_stage
            event_id = session.scalar(
                select(DomainEvent.id).where(
                    DomainEvent.workflow_run_id == workflow_id,
                    DomainEvent.delivered_at.is_(None),
                    DomainEvent.published_at.is_(None),
                )
            )
            assert event_id is not None
        result = worker.run_exact_event(event_id=event_id)
        if result.status != "DELIVERED":
            with factory() as session:
                failed_event = session.get(DomainEvent, event_id)
                detail = (
                    failed_event.last_error_code,
                    failed_event.last_error_summary,
                )
            pytest.fail(f"{expected_stage}: {result!r}: {detail!r}")


def _run_exact_worker_to_readiness(engine, workflow_id: uuid.UUID) -> None:
    """Execute only this workflow's normal pre-readiness commands."""

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    worker = ProductionWorkflowWorker(
        session_factory=factory,
        worker_id=f"review-only-readiness-{workflow_id}",
    )
    for expected_stage in (
        "PLANNING",
        "PREFLIGHT",
        "ADMISSION",
        "RESEARCH",
        "PACKAGE",
        "READINESS",
    ):
        with factory() as session:
            workflow = session.get(ProductionWorkflowRun, workflow_id)
            assert workflow is not None
            assert workflow.current_stage == expected_stage
            event_id = session.scalar(
                select(DomainEvent.id).where(
                    DomainEvent.workflow_run_id == workflow_id,
                    DomainEvent.delivered_at.is_(None),
                    DomainEvent.published_at.is_(None),
                )
            )
            assert event_id is not None
        result = worker.run_exact_event(event_id=event_id)
        assert result.status == "DELIVERED", (expected_stage, result)

    with factory() as session:
        workflow = session.get(ProductionWorkflowRun, workflow_id)
        assert workflow is not None
        assert workflow.state == "READY_FOR_PRODUCTION"
        assert workflow.current_stage == "MEDIA"


def _live_shaped_v2_payload(run, *, repaired: bool = False) -> dict:
    """Create the audited 67-sentence shape under the fixture's frozen plan."""

    plan = (run.script_assignment or {})["section_coverage_plan"]
    assert len(plan["sections"]) == 3
    evidence_span_id = run.factual_evidence_pack["spans"][0]["evidence_span_id"]
    special = {
        4: _QUESTION_ALTERNATE,
        10: _PARAPHRASE_CLAIM_2,
        12: _PARAPHRASE_CLAIM_3,
        55: _EXACT_CLAIM_2,
        60: _EXACT_CLAIM_3,
        63: _SHARED_FULFILLMENT_SPAN,
    }
    # Preserve the audited global sentence ordinals while fitting the real
    # three-section continuation assignment.  Question is owned by section 1;
    # self-containment (and the shared sentence 63) is owned by section 3.
    counts = [23, 22, 22]
    output_sections: list[dict] = []
    global_ordinal = 0
    claim_1_text = ""
    for section_index, (coverage, sentence_count) in enumerate(
        zip(plan["sections"], counts, strict=True), start=1
    ):
        sentences: list[str] = []
        for local_ordinal in range(1, sentence_count + 1):
            global_ordinal += 1
            if global_ordinal in special:
                sentence = special[global_ordinal]
            else:
                variant = "repaired" if repaired and section_index == 1 else "original"
                sentence = (
                    " ".join(
                        [
                            f"{variant}section{section_index}",
                            f"sentence{global_ordinal}",
                            *[
                                f"s{section_index}n{global_ordinal}token{token}"
                                for token in range(1, 19)
                            ],
                        ]
                    )
                    + "."
                )
            if global_ordinal == 1:
                claim_1_text = sentence
            sentences.append(sentence)
        expected_claim_refs: list[str] = []
        if section_index == 1:
            # Mirror the audited provider output: the paraphrase observations
            # and their later exact anchors are both explicitly declared.
            expected_claim_refs = ["claim-001", "claim-002", "claim-003"]
        elif section_index == 3:
            expected_claim_refs = ["claim-002", "claim-003"]
        output_sections.append(
            {
                "section_id": coverage["section_id"],
                "ordinal": coverage["ordinal"],
                "purpose": coverage["section_delta"],
                "narration": " ".join(sentences),
                "required_assignment_unit_refs": coverage["primary_requirement_ids"],
                "expected_claim_refs": expected_claim_refs,
            }
        )
    assert global_ordinal == 67
    return QualifiedScriptOutputV2(
        language=(run.runtime_contract or {}).get("expected_language") or "en",
        sections=output_sections,
        claims=[
            {
                "claim_id": "claim-001",
                "claim_text": claim_1_text,
                "evidence_span_ids": [evidence_span_id],
            },
            {
                "claim_id": "claim-002",
                "claim_text": _EXACT_CLAIM_2,
                "evidence_span_ids": [evidence_span_id],
            },
            {
                "claim_id": "claim-003",
                "claim_text": _EXACT_CLAIM_3,
                "evidence_span_ids": [evidence_span_id],
            },
        ],
    ).model_dump(mode="json")


def _live_shaped_verifier(run) -> SemanticVerificationOutput:
    # The canonical inventory is deliberately derived from the persisted child,
    # not duplicated from the expected sentence numbering in this test.
    qualification = ScriptQualificationService(run._sa_instance_state.session)
    materialized = qualification.draft_from_run(run)
    inventory = qualification._canonical_sentence_inventory(materialized)["sentences"]
    assert len(inventory) == 67
    by_id = {item["sentence_id"]: item for item in inventory}
    evidence_span_id = run.factual_evidence_pack["spans"][0]["evidence_span_id"]
    material_claims = {
        "sentence-0001": ("claim-001", ["subject"]),
        "sentence-0010": ("claim-002", ["subject", "scope-inclusion:3"]),
        "sentence-0012": ("claim-003", ["subject", "scope-inclusion:4"]),
        "sentence-0055": ("claim-002", ["scope-inclusion:3"]),
        "sentence-0060": ("claim-003", ["subject", "scope-inclusion:4"]),
    }
    claim_inventory = []
    for sentence in inventory:
        claim = material_claims.get(sentence["sentence_id"])
        claim_inventory.append(
            {
                "observed_claim_id": sentence["sentence_id"],
                "span": {
                    "text": sentence["text"],
                    "section_id": sentence["section_id"],
                },
                "claim_type": (
                    "FACTUAL_ASSERTION" if claim else "STRUCTURAL_TRANSITION"
                ),
                "materiality_state": "MATERIAL" if claim else "NON_MATERIAL",
                "writer_declared_claim_id": claim[0] if claim else None,
                "factual_evidence_span_ids": [evidence_span_id] if claim else [],
                "semantic_relation": "ENTAILED" if claim else "NOT_APPLICABLE",
                "assignment_requirement_ids": claim[1] if claim else [],
                "reason_codes": ["DIRECT_OFFICIAL_EVIDENCE"] if claim else [],
            }
        )

    coverage_sections = (run.script_assignment or {})["section_coverage_plan"][
        "sections"
    ]
    requirement_spans: dict[str, list[str]] = {}
    starts = [1, 24, 46]
    for section, start in zip(coverage_sections, starts, strict=True):
        for offset, requirement_id in enumerate(section["primary_requirement_ids"]):
            requirement_spans[requirement_id] = [f"sentence-{start + offset:04d}"]
    requirement_spans["question"] = ["sentence-0004", "sentence-0063"]
    requirement_spans["self-containment"] = ["sentence-0063"]
    fulfillment = []
    for requirement in (run.script_assignment or {})["required_requirement_units"]:
        requirement_id = requirement["requirement_id"]
        fulfillment.append(
            {
                "requirement_id": requirement_id,
                "status": "SUFFICIENT",
                "spans": [
                    {
                        "text": by_id[sentence_id]["text"],
                        "section_id": by_id[sentence_id]["section_id"],
                    }
                    for sentence_id in requirement_spans[requirement_id]
                ],
                "evidence_span_ids": [],
                "missing_reasoning_step": None,
                "reason_codes": [f"{requirement_id.upper()}_PRESENT"],
            }
        )
    section_purpose = [
        {
            "section_id": section["section_id"],
            "observed_primary_role": f"ROLE_{section['ordinal']}",
            "fulfilled_requirement_ids": section["primary_requirement_ids"],
            "editorial_delta": f"Distinct frozen delta {section['ordinal']}",
            "genericity_state": "SPECIFIC",
            "role_reuse_justification": None,
        }
        for section in coverage_sections
    ]
    forbidden = [
        {
            "forbidden_scope_id": item["forbidden_scope_id"],
            "state": "ABSENT",
            "script_spans": [],
            "observed_relation": None,
            "reason_codes": [],
        }
        for item in (run.script_assignment or {}).get("forbidden_scope_units", [])
    ]
    return SemanticVerificationOutput.model_validate(
        {
            "material_claim_inventory": claim_inventory,
            "assignment_fulfillment_observations": fulfillment,
            "section_purpose_observations": section_purpose,
            "forbidden_scope_observations": forbidden,
            "memory_application_observations": [],
        }
    )


def _verifier_copy(
    verifier: SemanticVerificationOutput, mutate
) -> SemanticVerificationOutput:
    payload = verifier.model_dump(mode="json")
    mutate(payload)
    return SemanticVerificationOutput.model_validate(payload)


def _blocked_live_shaped_source(
    session,
    qualification_factory,
    monkeypatch,
) -> SimpleNamespace:
    monkeypatch.setattr(
        continuation_test,
        "_v2_payload",
        _live_shaped_v2_payload,
    )
    lineage = _build_continuation_lineage(session, qualification_factory, monkeypatch)
    source = lineage.child
    verifier = _live_shaped_verifier(source)
    attempt, snapshot = _completed_attempt_with_snapshot(
        session,
        run=source,
        phase="VERIFIER",
        payload=verifier.model_dump(mode="json"),
        provider_outcome="COMPLETED",
        prompt_version=source.verifier_prompt_version,
        identity="live-shaped-settlement-verifier",
    )
    source.verifier_receipt = ScriptQualificationBackgroundService._receipt(
        attempt, {"usage": None}
    )
    qualification = ScriptQualificationService(session)
    draft = qualification.draft_from_run(source)
    structural = qualification._structural_receipt(source, draft)
    receipts = qualification._semantic_receipts(source, draft, verifier, structural)
    assert receipts["structural"]["status"] == "PASS"
    assert receipts["inventory"]["reason_codes"] == [
        "SCRIPT_WRITER_CLAIM_SPAN_MISMATCH"
    ]
    assert receipts["fulfillment"]["reason_codes"] == [
        "SCRIPT_ASSIGNMENT_COVERAGE_SPAN_REUSED"
    ]
    assert receipts["memory"]["status"] == "PASS_EMPTY"
    source.state = "BLOCKED_NON_REPAIRABLE"
    source.result_receipts = receipts
    source.failure_receipt = {
        "reason_codes": [
            reason
            for receipt in receipts.values()
            for reason in receipt["reason_codes"]
        ]
    }
    qualification._create_receipt(source, draft, "BLOCK", receipts)
    # The audited source is terminal and its continuation deadline is expired.
    # Settlement must derive a fresh viable slot/deadline without reopening it.
    settlement_now = source.logical_deadline_at + timedelta(seconds=1)
    ScriptQualificationRecoveryService(
        session, now=lambda: settlement_now
    ).settle_deterministic_block(source, reason_code="SCRIPT_QUALIFICATION_BLOCKED")
    source_receipt = session.scalar(
        select(ScriptQualificationReceipt).where(
            ScriptQualificationReceipt.script_qualification_run_id == source.id
        )
    )
    assert source_receipt is not None and source_receipt.result == "BLOCK"
    session.flush()
    return SimpleNamespace(
        **vars(lineage),
        verifier=verifier,
        verifier_attempt=attempt,
        verifier_snapshot=snapshot,
        draft=draft,
        source_receipt=source_receipt,
        settlement_now=settlement_now,
    )


def test_policy_v3_applies_only_exact_anchor_and_frozen_ownership_projection(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )

    receipts, projection = derive_v3_semantic_receipts(
        service=ScriptQualificationService(db_session),
        run=lineage.child,
        draft=lineage.draft,
        verifier=lineage.verifier,
        source_verifier_output_hash=content_hash(
            lineage.verifier.model_dump(mode="json")
        ),
    )

    assert all(
        receipt["status"] in {"PASS", "PASS_EMPTY"} for receipt in receipts.values()
    )
    assert projection["schema_version"]
    assert projection["policy_version"] == CONTROLLED_VERIFIER_SETTLEMENT_POLICY
    assert projection["source_verifier_output_hash"] == content_hash(
        lineage.verifier.model_dump(mode="json")
    )
    assert projection["claim_anchor_decisions"] == [
        {
            "observed_claim_id": "sentence-0010",
            "writer_declared_claim_id": "claim-002",
            "anchor_observed_claim_id": "sentence-0055",
            "evidence_span_ids": [
                lineage.child.factual_evidence_pack["spans"][0]["evidence_span_id"]
            ],
            "semantic_relation": "ENTAILED",
        },
        {
            "observed_claim_id": "sentence-0012",
            "writer_declared_claim_id": "claim-003",
            "anchor_observed_claim_id": "sentence-0060",
            "evidence_span_ids": [
                lineage.child.factual_evidence_pack["spans"][0]["evidence_span_id"]
            ],
            "semantic_relation": "ENTAILED",
        },
    ]
    assert projection["removed_fulfillment_spans"] == [
        {
            "requirement_id": "question",
            "retained_owner_requirement_id": "self-containment",
            "section_id": "section-003",
            "text": _SHARED_FULFILLMENT_SPAN,
        }
    ]
    assert projection["resulting_receipts_hash"] == content_hash(receipts)
    assert projection["content_hash"] == content_hash(
        {key: value for key, value in projection.items() if key != "content_hash"}
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["material_claim_inventory"][9].update(
            semantic_relation="PARTIALLY_SUPPORTED"
        ),
        lambda payload: payload["material_claim_inventory"][9].update(
            factual_evidence_span_ids=["unknown-evidence-span"]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][2].update(
            spans=[payload["assignment_fulfillment_observations"][2]["spans"][1]]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][2].update(
            spans=[
                {
                    **payload["material_claim_inventory"][23]["span"],
                },
                payload["assignment_fulfillment_observations"][2]["spans"][1],
            ]
        ),
        lambda payload: payload["assignment_fulfillment_observations"][0].update(
            status="PARTIAL"
        ),
    ],
    ids=[
        "paraphrase-not-entailed",
        "paraphrase-evidence-mismatch",
        "duplicate-owner-has-no-alternate",
        "alternate-outside-frozen-ownership",
        "unrelated-gate-still-blocked",
    ],
)
def test_policy_v3_rejects_unsafe_semantic_projection(
    db_session,
    qualification_factory,
    monkeypatch,
    mutation,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    unsafe = _verifier_copy(lineage.verifier, mutation)

    with pytest.raises(ValidationFailureError):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=lineage.draft,
            verifier=unsafe,
            source_verifier_output_hash=content_hash(unsafe.model_dump(mode="json")),
        )


def test_policy_v3_rejects_paraphrase_without_exact_canonical_anchor(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    claims = list(lineage.draft.claims)
    claims[1] = claims[1].model_copy(
        update={"claim_text": "This claim text occurs nowhere in the narration."}
    )
    unsafe_draft = lineage.draft.model_copy(update={"claims": claims})

    with pytest.raises(ValidationFailureError):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=unsafe_draft,
            verifier=lineage.verifier,
            source_verifier_output_hash=content_hash(
                lineage.verifier.model_dump(mode="json")
            ),
        )


@pytest.mark.parametrize(
    "section_index",
    [0, 2],
    ids=["paraphrase-section-undeclared", "anchor-section-undeclared"],
)
def test_policy_v3_requires_claim_declared_in_both_observation_sections(
    db_session,
    qualification_factory,
    monkeypatch,
    section_index,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    unsafe_payload = deepcopy(lineage.child.script_payload)
    section = unsafe_payload["sections"][section_index]
    section["expected_claim_refs"] = [
        claim_id
        for claim_id in section["expected_claim_refs"]
        if claim_id != "claim-002"
    ]
    lineage.child.script_payload = unsafe_payload

    with pytest.raises(
        ValidationFailureError,
        match="VERIFIER_SETTLEMENT_EXACT_ANCHOR_INVALID",
    ):
        derive_v3_semantic_receipts(
            service=ScriptQualificationService(db_session),
            run=lineage.child,
            draft=lineage.draft,
            verifier=lineage.verifier,
            source_verifier_output_hash=content_hash(
                lineage.verifier.model_dump(mode="json")
            ),
        )


def _source_snapshot(lineage) -> dict:
    source = lineage.child
    slot = lineage.continuation_slot
    return {
        "state": source.state,
        "failure_receipt": deepcopy(source.failure_receipt),
        "result_receipts": deepcopy(source.result_receipts),
        "terminal_settlement_receipt": deepcopy(source.terminal_settlement_receipt),
        "script_payload": deepcopy(source.script_payload),
        "canonical_script_artifact_id": source.canonical_script_artifact_id,
        "derived_canonical_script_hash": source.derived_canonical_script_hash,
        "writer_receipt": deepcopy(source.writer_receipt),
        "verifier_receipt": deepcopy(source.verifier_receipt),
        "slot": {
            "id": slot.id,
            "state": slot.state,
            "reserved_candidate_id": slot.reserved_candidate_id,
            "admitted_video_project_id": slot.admitted_video_project_id,
            "target_start_window_open_at": slot.target_start_window_open_at,
            "target_start_window_close_at": slot.target_start_window_close_at,
            "intended_publish_at": slot.intended_publish_at,
            "replacement_lineage_key": slot.replacement_lineage_key,
        },
    }


def test_zero_provider_settlement_is_append_only_idempotent_and_consumable(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    before = _source_snapshot(lineage)
    attempt_count = db_session.scalar(
        select(func.count(ScriptQualificationBackgroundAttempt.id))
    )
    snapshot_count = db_session.scalar(
        select(func.count(ScriptQualificationProviderResponseSnapshot.id))
    )
    monkeypatch.setattr(
        "app.services.script_verifier_settlement.resolve_provider_authority",
        lambda *_args, **_kwargs: continuation_test._ready_snapshot(),
    )
    monkeypatch.setattr(
        "app.services.script_verifier_settlement.resolve_budget_authority",
        lambda *_args, **_kwargs: continuation_test._ready_snapshot(),
    )
    service = ScriptVerifierSettlementRecoveryService(
        db_session, now=lambda: lineage.settlement_now
    )

    child = service.create(source_qualification_run_id=lineage.child.id)
    repeated = service.create(source_qualification_run_id=lineage.child.id)

    assert repeated.id == child.id
    assert _source_snapshot(lineage) == before
    assert child.id != lineage.child.id
    assert child.supersedes_qualification_run_id == lineage.child.id
    assert child.state == "QUALIFIED"
    assert child.script_payload == lineage.child.script_payload
    assert (
        child.canonical_script_artifact_id != lineage.child.canonical_script_artifact_id
    )
    assert (
        child.derived_canonical_script_hash
        == lineage.child.derived_canonical_script_hash
    )
    # Settlement explicitly reclassifies the trusted local producer while
    # preserving every original provider-bound field and hash.
    assert {
        key: child.writer_receipt[key]
        for key in lineage.child.writer_receipt
        if key not in {"producer", "producer_type"}
    } == {
        key: value
        for key, value in lineage.child.writer_receipt.items()
        if key not in {"producer", "producer_type"}
    }
    assert child.writer_receipt["producer"] == (
        "DERIVED_FROM_COMPLETED_VERIFIER_SETTLEMENT"
    )
    assert child.writer_receipt["producer_type"] == (
        "OPENAI_BACKGROUND_VERIFIER_SETTLEMENT"
    )
    assert {
        key: child.verifier_receipt[key] for key in lineage.child.verifier_receipt
    } == lineage.child.verifier_receipt
    assert child.script_assignment_hash == lineage.child.script_assignment_hash
    assert child.factual_evidence_pack_hash == lineage.child.factual_evidence_pack_hash
    assert child.runtime_contract_hash == lineage.child.runtime_contract_hash
    assert child.assignment_resolution_hash == lineage.child.assignment_resolution_hash
    assert (
        db_session.scalar(select(func.count(ScriptQualificationBackgroundAttempt.id)))
        == attempt_count
    )
    assert (
        db_session.scalar(
            select(func.count(ScriptQualificationProviderResponseSnapshot.id))
        )
        == snapshot_count
    )
    assert (
        db_session.scalar(
            select(func.count(ScriptQualificationBackgroundAttempt.id)).where(
                ScriptQualificationBackgroundAttempt.script_qualification_run_id
                == child.id
            )
        )
        == 0
    )

    authority = db_session.scalar(
        select(ControlledVerifierSettlementAuthority).where(
            ControlledVerifierSettlementAuthority.settlement_qualification_run_id
            == child.id
        )
    )
    assert authority is not None
    # The settlement authority always derives its own fresh slot/deadline.  It
    # does not copy or extend the immutable source deadline.
    assert child.logical_deadline_at != lineage.child.logical_deadline_at
    assert child.logical_deadline_at == authority.qualification_deadline
    assert child.logical_deadline_at > lineage.settlement_now
    assert authority.production_window_end > child.logical_deadline_at
    assert child.writer_receipt["settlement_source_qualification_run_id"] == str(
        lineage.child.id
    )
    assert child.writer_receipt["settlement_source_verifier_attempt_id"] == str(
        lineage.verifier_attempt.id
    )
    assert child.writer_receipt["settlement_source_verifier_snapshot_id"] == str(
        lineage.verifier_snapshot.id
    )
    assert child.writer_receipt["settlement_authority_id"] == str(authority.id)
    assert child.writer_receipt["settlement_authority_hash"] == authority.authority_hash
    assert child.writer_receipt["settlement_projection_hash"] == (
        authority.derived_projection_hash
    )
    assert child.writer_receipt["provider_submission_count_for_settlement"] == 0
    assert child.verifier_receipt["settlement_authority_id"] == str(authority.id)
    assert (
        child.verifier_receipt["settlement_authority_hash"] == authority.authority_hash
    )
    assert child.verifier_receipt["settlement_source_qualification_run_id"] == str(
        lineage.child.id
    )
    assert child.verifier_receipt["settlement_source_verifier_snapshot_id"] == str(
        lineage.verifier_snapshot.id
    )
    assert child.verifier_receipt["derived_projection_hash"] == (
        authority.derived_projection_hash
    )
    assert child.verifier_receipt["provider_submission_count_for_settlement"] == 0
    slot = db_session.get(LongFormPublishSlot, child.publish_slot_id)
    assert slot is not None and slot.id != lineage.continuation_slot.id
    assert slot.target_start_window_open_at == lineage.settlement_now
    assert slot.target_start_window_close_at == authority.production_window_end
    assert slot.replaces_slot_id == lineage.continuation_slot.id
    assert slot.state == "QUALIFICATION_RESERVED"
    assert lineage.continuation_slot.state == "CANCELED"
    assert authority.schema_version == CONTROLLED_VERIFIER_SETTLEMENT_SCHEMA
    assert authority.settlement_reason == CONTROLLED_VERIFIER_SETTLEMENT_REASON
    assert authority.settlement_policy_version == CONTROLLED_VERIFIER_SETTLEMENT_POLICY
    assert authority.source_verifier_attempt_id == lineage.verifier_attempt.id
    assert authority.source_verifier_snapshot_id == lineage.verifier_snapshot.id
    assert authority.max_provider_submissions == 0
    assert authority.authority_hash == content_hash(
        controlled_verifier_settlement_authority_body(authority)
    )
    assert (
        authority.derived_projection_hash
        == authority.derived_projection["content_hash"]
    )
    assert authority.derived_projection_hash == content_hash(
        {
            key: value
            for key, value in authority.derived_projection.items()
            if key != "content_hash"
        }
    )
    assert (
        db_session.scalar(select(func.count(ControlledVerifierSettlementAuthority.id)))
        == 1
    )

    resolved = resolve_replacement_qualification_leaf(
        db_session, authority=lineage.root_lineage.authority
    )
    assert resolved.id == child.id
    pass_receipt = ScriptQualificationService(db_session).require_pass(
        child.id, candidate_id=child.editorial_idea_candidate_id
    )
    materialized, evidence, memory, _provenance = (
        ScriptQualificationService.qualification_output(pass_receipt)
    )
    assert materialized["canonical_script"] == lineage.draft.canonical_script
    assert evidence == child.factual_evidence_pack
    assert memory == child.memory_digest

    duration = ProductionDurationContractV2.model_validate(
        child.runtime_contract["duration_contract"]
    )
    support_context = V2SupportProductionContext(
        video_project_id=uuid.uuid4(),
        production_lane="LONG_FORM",
        title="Controlled verifier settlement",
        expected_language=child.runtime_contract["expected_language"],
        duration_contract=duration,
        frozen_sources=V2SupportAuthorityService._qualification_frozen_sources(
            pass_receipt
        ),
        memory_guidance_digest=child.memory_digest,
    )
    supported = V2SupportAuthorityService(db_session)._qualified_validated(
        qualification_receipt=pass_receipt,
        context=support_context,
    )
    trusted_script = supported["script"]
    assert trusted_script.approved_script_text == lineage.draft.canonical_script
    producer = trusted_script.producer_receipt
    assert producer.producer_type == "OPENAI_BACKGROUND_VERIFIER_SETTLEMENT"
    assert producer.settlement_source_qualification_run_id == lineage.child.id
    assert producer.settlement_source_verifier_attempt_id == lineage.verifier_attempt.id
    assert producer.settlement_source_verifier_snapshot_id == (
        lineage.verifier_snapshot.id
    )
    assert producer.settlement_authority_id == authority.id
    assert producer.settlement_authority_hash == authority.authority_hash
    assert producer.settlement_projection_hash == authority.derived_projection_hash

    for statement in (
        update(ControlledVerifierSettlementAuthority)
        .where(ControlledVerifierSettlementAuthority.id == authority.id)
        .values(max_provider_submissions=1),
        delete(ControlledVerifierSettlementAuthority).where(
            ControlledVerifierSettlementAuthority.id == authority.id
        ),
    ):
        with pytest.raises(
            ProgrammingError,
            match="controlled verifier settlement authorities are immutable",
        ):
            with db_session.begin_nested():
                db_session.execute(statement)
                db_session.flush()
        db_session.expire_all()

    with pytest.raises(
        ProgrammingError,
        match="controlled verifier settlement forbids provider submissions",
    ):
        with db_session.begin_nested():
            db_session.add(
                _fresh_attempt(
                    child,
                    phase="VERIFIER",
                    identity="forbidden-settlement-provider",
                )
            )
            db_session.flush()


def test_settlement_finalization_uses_distinct_publish_context_without_rebinding_candidate(
    db_session,
    qualification_factory,
    monkeypatch,
) -> None:
    _install_research_context_source(monkeypatch)
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    candidate = db_session.get(
        EditorialIdeaCandidate,
        lineage.child.editorial_idea_candidate_id,
    )
    assert candidate is not None and candidate.context_pack_snapshot_id is not None
    original_context_id = candidate.context_pack_snapshot_id
    original_context = db_session.get(ContextPackSnapshot, original_context_id)
    assert original_context is not None
    original_pack_hash = original_context.pack_hash
    original_pack_content = deepcopy(original_context.pack_content)
    assert original_context.purpose == "EDITORIAL_RESEARCH"
    assert original_context.id == lineage.historical.research_context.id

    _install_ready_finalization_authorities(monkeypatch)
    child = ScriptVerifierSettlementRecoveryService(
        db_session,
        now=lambda: lineage.settlement_now,
    ).create(source_qualification_run_id=lineage.child.id)

    admission, workflow = LongFormCadenceService(
        db_session,
        now=lambda: lineage.settlement_now,
        support_authority_preparer=_test_support_authority_preparer,
    ).finalize_qualified_script_run(
        script_qualification_run_id=child.id,
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert admission.decision == "ADMIT"
    assert admission.admitted_video_project_id is not None
    assert workflow.video_project_id == admission.admitted_video_project_id
    assert child.admitted_video_project_id == admission.admitted_video_project_id
    assert child.production_workflow_run_id == workflow.id
    assert candidate.stage == "IN_PRODUCTION"
    assert candidate.context_pack_snapshot_id == original_context_id
    assert original_context.pack_hash == original_pack_hash
    assert original_context.pack_content == original_pack_content

    publish_preflight = db_session.get(
        IdeaMarketPreflight,
        admission.idea_market_preflight_id,
    )
    assert publish_preflight is not None
    publish_blob = publish_preflight.evidence_blob
    publish_context_id = uuid.UUID(publish_blob["evaluation_context_pack_snapshot_id"])
    publish_context = db_session.get(ContextPackSnapshot, publish_context_id)
    assert publish_context is not None
    assert publish_context.id != original_context_id
    assert publish_context.purpose == "AUTHORITY_REVIEW"
    assert publish_context.editorial_calendar_slot_id == (
        admission.editorial_calendar_slot_id
    )
    assert publish_context.company_id == candidate.company_id
    assert publish_context.channel_workspace_id == candidate.channel_workspace_id
    assert publish_context.policy_snapshot_id == candidate.policy_snapshot_id
    assert publish_blob["evaluation_context_pack_hash"] == publish_context.pack_hash
    assert publish_blob["evaluation_context_pack_purpose"] == "AUTHORITY_REVIEW"
    assert publish_blob["source_context_pack_snapshot_id"] == str(original_context_id)
    assert publish_blob["source_context_pack_hash"] == original_pack_hash
    assert publish_context.pack_content["niche_contract_digest"][
        "editorial_slot_id"
    ] == str(admission.editorial_calendar_slot_id)
    assert (
        publish_blob["niche_contract_digest_hash"]
        == (publish_context.pack_content["niche_contract_digest"]["content_hash"])
    )
    assert original_context.editorial_calendar_slot_id != (
        publish_context.editorial_calendar_slot_id
    )

    replay_data = IdeaMarketPreflightCreate(
        company_id=candidate.company_id,
        channel_workspace_id=candidate.channel_workspace_id,
        editorial_calendar_slot_id=admission.editorial_calendar_slot_id,
        editorial_research_run_id=candidate.editorial_research_run_id,
        editorial_idea_candidate_id=candidate.id,
        search_intent_map_id=publish_preflight.search_intent_map_id,
        audience_target_pack_id=publish_preflight.audience_target_pack_id,
        claim_evidence_refs=[
            {"id": item["id"]} for item in publish_blob["claim_evidence_refs"]
        ],
        market_demand_evidence_refs=[
            {"id": item["id"]} for item in publish_blob["market_demand_evidence_refs"]
        ],
    )
    # An exact PUBLISH context must not mask corruption in the independently
    # frozen source context.
    original_context.pack_hash = "f" * 64
    try:
        with pytest.raises(
            ValidationFailureError,
            match="NICH1_EDITORIAL_CONTEXT_PACK_HASH_MISMATCH",
        ):
            IdeaMarketPreflightService(db_session).create_preflight(
                data=replay_data,
                evaluation_context_pack_snapshot_id=publish_context.id,
                correlation_id=f"settlement-tampered-source-context:{candidate.id}",
            )
    finally:
        original_context.pack_hash = original_pack_hash

    # An explicit PUBLISH evaluation pack must not mask substitution of the
    # source pointer frozen by the candidate's exact research run.
    research = db_session.get(
        EditorialResearchRun,
        candidate.editorial_research_run_id,
    )
    assert research is not None
    assert research.context_pack_snapshot_id == original_context_id
    research.context_pack_snapshot_id = publish_context.id
    try:
        with db_session.no_autoflush:
            with pytest.raises(
                ValidationFailureError,
                match="V2_LONG_FORM_PREFLIGHT_SOURCE_CONTEXT_SCOPE_MISMATCH",
            ):
                IdeaMarketPreflightService(db_session).create_preflight(
                    data=replay_data,
                    evaluation_context_pack_snapshot_id=publish_context.id,
                    correlation_id=(
                        f"settlement-substituted-source-context:{candidate.id}"
                    ),
                )
    finally:
        research.context_pack_snapshot_id = original_context_id

    # Omitting the explicit PUBLISH evaluation context falls back to the
    # candidate's immutable discovery pack and must fail as stale.
    with pytest.raises(
        ValidationFailureError,
        match="V2_LONG_FORM_PREFLIGHT_CONTEXT_DIGEST_STALE",
    ):
        IdeaMarketPreflightService(db_session).create_preflight(
            data=replay_data,
            correlation_id=f"settlement-stale-research-context:{candidate.id}",
        )


def test_settlement_real_support_seals_exact_pending_destination_for_review_only(
    db_session,
    engine,
    qualification_factory,
    monkeypatch,
) -> None:
    """A truthful pending destination permits review, never upload or publish."""

    _install_research_context_source(monkeypatch)
    lineage = _blocked_live_shaped_source(
        db_session, qualification_factory, monkeypatch
    )
    _install_ready_finalization_authorities(monkeypatch)
    candidate = db_session.get(
        EditorialIdeaCandidate,
        lineage.child.editorial_idea_candidate_id,
    )
    assert candidate is not None
    channel = lineage.historical.scope.channel
    original_governance = deepcopy((channel.metadata_ or {})["destination_governance"])
    original_active_ref = original_governance["active_binding_ref"]
    original_binding = next(
        item
        for item in original_governance["bindings"]
        if original_active_ref
        == f"destination-binding://{channel.key}/v{item['binding_version']}"
    )
    assert original_binding["destination_status"] == "PENDING_PLATFORM_ID"
    assert original_binding["verification_state"] == "PENDING"
    assert original_binding["platform_account_ref"] is None
    assert original_binding["platform_channel_id"] is None
    assert original_binding["credential_ref"] is None
    assert original_binding["verification_timestamp"] is None
    provider_attempt_count = db_session.scalar(
        select(func.count()).select_from(ProviderAttempt)
    )

    child = ScriptVerifierSettlementRecoveryService(
        db_session,
        now=lambda: lineage.settlement_now,
    ).create(source_qualification_run_id=lineage.child.id)
    admission, workflow = LongFormCadenceService(
        db_session,
        now=lambda: lineage.settlement_now,
    ).finalize_qualified_script_run(
        script_qualification_run_id=child.id,
        actor=_system_worker_actor(
            "vcos-durable-worker",
            permissions={"production.start"},
        ),
    )

    assert admission.decision == "ADMIT"
    assert admission.admitted_video_project_id == workflow.video_project_id
    assert child.admitted_video_project_id == workflow.video_project_id
    assert child.production_workflow_run_id == workflow.id
    assert candidate.stage == "IN_PRODUCTION"
    support_artifact = db_session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == workflow.video_project_id,
            Artifact.artifact_type == V2_FROZEN_SUPPORT_ENVELOPE_ARTIFACT_TYPE,
        )
    )
    assert support_artifact is not None
    support_version = db_session.get(
        ArtifactVersion,
        support_artifact.current_version_id,
    )
    assert support_version is not None
    envelope = V2FrozenSupportEnvelope.model_validate(support_version.content)
    destination = envelope.verified_destination.model_dump(mode="json")
    assert destination["schema_version"] == (
        "vcos.final-review-only-destination-authority.v1"
    )
    assert destination["authority_mode"] == "FINAL_REVIEW_ONLY"
    assert destination["publish_policy"] == "NO_PUBLISH"
    assert destination["publish_execution_allowed"] is False
    assert destination["active_binding_ref"] == original_active_ref
    assert destination["binding"] == original_binding
    assert destination["destination_hash"] == original_binding["content_hash"]
    assert destination["destination_status"] == "PENDING_PLATFORM_ID"
    assert destination["verification_state"] == "PENDING"
    assert destination["channel_handle"] == original_binding["channel_handle"]
    assert destination["platform"] == original_binding["platform"]
    assert destination["platform_account_ref"] is None
    assert destination["platform_channel_id"] is None
    assert destination["credential_ref"] is None
    assert destination["verification_timestamp"] is None
    assert destination["settlement_qualification_run_id"] == str(child.id)
    assert destination["controlled_recovery_authority_id"] == str(
        lineage.root_lineage.authority.id
    )
    assert destination["controlled_recovery_authority_hash"] == (
        lineage.root_lineage.authority.authority_hash
    )
    settlement = db_session.scalar(
        select(ControlledVerifierSettlementAuthority).where(
            ControlledVerifierSettlementAuthority.settlement_qualification_run_id
            == child.id
        )
    )
    assert settlement is not None
    assert destination["settlement_authority_id"] == str(settlement.id)
    assert destination["settlement_authority_hash"] == settlement.authority_hash
    destination_model_hash = original_binding["content_hash"]
    destination_authority_hash = destination["content_hash"]
    assert destination["destination_hash"] == destination_model_hash
    assert destination_authority_hash != destination_model_hash
    destination_projection = {
        **original_binding,
        "active_binding_ref": destination["active_binding_ref"],
        "destination_authority_hash": destination_authority_hash,
        "publish_execution_allowed": False,
    }
    assert destination_projection["content_hash"] == destination_model_hash
    assert (
        destination_projection["destination_authority_hash"]
        == destination_authority_hash
    )
    assert (channel.metadata_ or {})["destination_governance"] == original_governance
    assert (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt))
        == provider_attempt_count
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ProviderAttempt)
            .where(
                ProviderAttempt.target_id.in_([workflow.id, workflow.video_project_id])
            )
        )
        == 0
    )

    # Exercise the normal package/readiness worker.  This is the exact path
    # that projects the frozen support destination into immutable artifacts.
    db_session.commit()
    _run_exact_worker_to_readiness(engine, workflow.id)
    db_session.expire_all()
    workflow = db_session.get(ProductionWorkflowRun, workflow.id)
    assert workflow is not None
    assert workflow.state == "READY_FOR_PRODUCTION"
    assert workflow.production_package_artifact_version_id is not None
    package = ProductionPackageService(db_session).validate_for_readiness(
        workflow.production_package_artifact_version_id
    )
    assert isinstance(package, ProductionPackageContentV2)
    assert package.destination_binding_ref.artifact_version_id is not None
    destination_version = db_session.get(
        ArtifactVersion,
        package.destination_binding_ref.artifact_version_id,
    )
    assert destination_version is not None
    destination_artifact = db_session.get(Artifact, destination_version.artifact_id)
    assert destination_artifact is not None
    destination_content = destination_version.content
    assert destination_content["result"] == "PASS_FOR_FINAL_REVIEW_ONLY"
    assert destination_content["destination_mode"] == "FINAL_REVIEW_ONLY"
    assert destination_content["publish_execution_allowed"] is False
    assert destination_content["automatic_publish"] is False
    raw_binding = destination_content["destination_binding"]
    wrapped_authority = destination_content["final_review_only_destination_authority"]
    normalized_destination = _normalized_destination(destination_content)
    assert raw_binding["content_hash"] == original_binding["content_hash"]
    assert destination_content["destination_model_hash"] == raw_binding["content_hash"]
    assert (
        destination_content["destination_binding_hash"]
        == (wrapped_authority["content_hash"])
    )
    assert (
        destination_content["destination_authority_hash"]
        == (wrapped_authority["content_hash"])
    )
    assert (
        normalized_destination["destination_model_hash"]
        == (raw_binding["content_hash"])
    )
    assert (
        normalized_destination["destination_binding_hash"]
        == (wrapped_authority["content_hash"])
    )
    assert (
        normalized_destination["destination_authority_hash"]
        == (wrapped_authority["content_hash"])
    )
    for key in (
        "settlement_authority_id",
        "settlement_authority_hash",
        "settlement_qualification_run_id",
        "settlement_provenance_hash",
    ):
        assert destination_content[key] == wrapped_authority[key]
        assert normalized_destination[key] == wrapped_authority[key]
        tampered_destination = deepcopy(destination_content)
        tampered_destination[key] = (
            str(uuid.uuid4()) if key.endswith("_id") else "f" * 64
        )
        with pytest.raises(
            ValidationFailureError,
            match="V2_PROVIDER_FINAL_REVIEW_ONLY_INVALID",
        ):
            _normalized_destination(tampered_destination)
    assert (
        package.destination_binding_ref.content_hash == destination_version.content_hash
    )
    assert (channel.metadata_ or {})["destination_governance"] == original_governance

    # The post-readiness fixture creates checksum-verified Drive-shaped
    # authorities entirely offline. Destination/package/final-media validation
    # and candidate construction remain the real production services.
    # Hand off a clean transaction boundary before the worker opens separate
    # stage sessions against the same immutable artifact lineage.
    db_session.commit()
    _run_local_post_readiness_to_final_review(engine, workflow.id)
    db_session.expire_all()
    workflow = db_session.get(ProductionWorkflowRun, workflow.id)
    assert workflow is not None
    assert workflow.state == "FINAL_REVIEW_READY"
    assert workflow.current_stage == "FINALIZE"
    assert workflow.final_review_candidate_id is not None
    final_candidate = db_session.get(
        FinalReviewCandidate,
        workflow.final_review_candidate_id,
    )
    assert final_candidate is not None
    assert final_candidate.destination_platform_channel_id is None
    assert final_candidate.destination_account_identity is None
    assert final_candidate.destination_binding_id == destination_version.id
    assert (
        final_candidate.destination_binding_fingerprint
        == destination_version.content_hash
    )
    final_lineage = final_candidate.target_market_lineage
    assert final_lineage["destination_mode"] == "FINAL_REVIEW_ONLY"
    assert final_lineage["destination_status"] == "PENDING_PLATFORM_ID"
    assert final_lineage["destination_handle"] == original_binding["channel_handle"]
    assert final_lineage["publish_execution_allowed"] is False
    assert final_lineage["automatic_publish"] is False
    assert final_lineage["destination_model_hash"] == raw_binding["content_hash"]
    assert (
        final_lineage["destination_binding_hash"] == (wrapped_authority["content_hash"])
    )
    assert (
        final_lineage["destination_authority_hash"]
        == (wrapped_authority["content_hash"])
    )
    assert final_lineage["controlled_recovery_authority_id"] == str(
        lineage.root_lineage.authority.id
    )
    assert final_lineage["controlled_recovery_authority_hash"] == (
        lineage.root_lineage.authority.authority_hash
    )
    assert final_lineage["settlement_authority_id"] == str(settlement.id)
    assert final_lineage["settlement_authority_hash"] == settlement.authority_hash
    assert final_lineage["settlement_qualification_run_id"] == str(child.id)
    assert (
        final_lineage["settlement_provenance_hash"]
        == destination["settlement_provenance_hash"]
    )
    caption_metadata = final_candidate.publish_metadata_snapshot["caption_sidecar"]
    provider_plan_version = db_session.get(
        ArtifactVersion,
        package.provider_execution_plan_ref.artifact_version_id,
    )
    assert provider_plan_version is not None
    assert (
        "caption_sidecar"
        not in (
            provider_plan_version.content["final_review"]["publish_metadata_snapshot"]
        )
    )
    assert caption_metadata["schema_version"] == "vcos.v2-drive-caption-review.v1"
    assert caption_metadata["delivery_mode"] == "SIDECAR_ONLY"
    assert caption_metadata["archive_verification_state"] == "VERIFIED"
    assert caption_metadata["storage_provider"] == "GOOGLE_DRIVE"
    assert caption_metadata["file_name"] == "canonical-captions.srt"
    assert caption_metadata["caption_archive_object_ref"].endswith(
        "/canonical-captions.srt"
    )
    assert caption_metadata["caption_drive_web_view_url"].startswith(
        "https://drive.google.com/file/d/"
    )
    for field in (
        "caption_checksum_sha256",
        "caption_artifact_hash",
        "subtitle_qc_hash",
    ):
        assert len(caption_metadata[field]) == 64
    assert caption_metadata["caption_ref"].startswith("artifact-version://caption/")
    assert caption_metadata["subtitle_qc_ref"].startswith(
        "artifact-version://subtitle-qc/"
    )
    final_project = db_session.get(VideoProject, workflow.video_project_id)
    assert final_project is not None
    review = OperatorCockpitService(db_session)._final_review(
        project=final_project,
        run=workflow,
        channel=channel,
        candidate=final_candidate,
        decision=None,
        series_plan=None,
        series_run=None,
    )
    assert review.media.caption_sidecar is not None
    assert review.media.caption_sidecar.delivery_mode == "SIDECAR_ONLY"
    assert review.media.caption_sidecar.verification_state == "VERIFIED"
    assert (
        review.media.caption_sidecar.drive_web_view_url
        == caption_metadata["caption_drive_web_view_url"]
    )
    assert (
        review.media.caption_sidecar.checksum_sha256
        == caption_metadata["caption_checksum_sha256"]
    )
    assert review.media.captions_label == caption_metadata["label"]
    sealed_publish_metadata = deepcopy(final_candidate.publish_metadata_snapshot)
    final_candidate_id = final_candidate.id
    db_session.expunge(final_candidate)
    final_candidate.publish_metadata_snapshot = {
        **sealed_publish_metadata,
        "caption_sidecar": {
            **caption_metadata,
            "caption_checksum_sha256": "0" * 64,
        },
    }
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_CAPTION_SIDECAR_AUTHORITY_MISMATCH",
    ):
        OperatorCockpitService(db_session)._final_review(
            project=final_project,
            run=workflow,
            channel=channel,
            candidate=final_candidate,
            decision=None,
            series_plan=None,
            series_run=None,
        )
    final_candidate = db_session.get(FinalReviewCandidate, final_candidate_id)
    assert final_candidate is not None
    assert (channel.metadata_ or {})["destination_governance"] == original_governance
    assert (
        db_session.scalar(select(func.count()).select_from(ProviderAttempt))
        == provider_attempt_count
    )

    operator = _actor(db_session, lineage.historical.scope, admin=True)
    publish = ProductionPublishService(db_session)
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_ONLY_UPLOAD_FORBIDDEN",
    ):
        publish.decide(
            candidate_id=final_candidate.id,
            data=FinalVideoDecisionCreate(
                command_id=uuid.uuid4(),
                decision=FinalVideoDecisionValue.UPLOAD,
                reason="Review-only authority must never upload.",
            ),
            actor=operator,
        )

    # Defensive service checks remain active even if a caller somehow presents
    # an upload-task or confirmation-shaped object without a lawful UPLOAD
    # decision.  No row or provider effect is created by these probes.
    fake_task = SimpleNamespace(
        id=uuid.uuid4(),
        company_id=final_candidate.company_id,
        final_review_candidate_id=final_candidate.id,
    )
    monkeypatch.setattr(
        publish,
        "_require_v2_task",
        lambda *_args, **_kwargs: fake_task,
    )
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_ONLY_UPLOAD_FORBIDDEN",
    ):
        publish.start_upload_task(
            task_id=fake_task.id,
            data=HumanUploadTaskStartV2(
                selected_file_name="review-only.mp4",
                selected_file_ref=final_candidate.archive_object_ref,
                selected_file_checksum=final_candidate.final_media_hash,
                archive_object_ref=final_candidate.archive_object_ref,
            ),
            actor=operator,
        )
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_ONLY_UPLOAD_FORBIDDEN",
    ):
        publish.submit_confirmation(
            task_id=fake_task.id,
            data=ManualPublishConfirmationCreateV2(
                command_id=uuid.uuid4(),
                platform="YOUTUBE",
                platform_channel_id="must-not-be-used",
                destination_binding_id=final_candidate.destination_binding_id,
                destination_binding_fingerprint=(
                    final_candidate.destination_binding_fingerprint
                ),
                destination_account_identity="must-not-be-used",
                platform_video_id="must-not-be-used",
                video_url="https://example.invalid/must-not-publish",
                title="Must not publish",
                privacy_status="PRIVATE",
                published_at=lineage.settlement_now,
                duration_seconds=Decimal("360"),
                thumbnail_confirmed=True,
                caption_confirmed=True,
            ),
            actor=operator,
        )

    fake_confirmation = SimpleNamespace(
        id=uuid.uuid4(),
        company_id=final_candidate.company_id,
        confirmation_state="SUBMITTED",
        human_upload_task_id=fake_task.id,
    )
    final_media = db_session.get(FinalMediaRef, final_candidate.final_media_ref_id)
    assert final_media is not None
    monkeypatch.setattr(
        publish,
        "_require_v2_confirmation",
        lambda *_args, **_kwargs: fake_confirmation,
    )
    monkeypatch.setattr(
        publish,
        "_task_lineage",
        lambda *_args, **_kwargs: (
            final_candidate,
            SimpleNamespace(id=uuid.uuid4()),
            final_media,
        ),
    )
    with pytest.raises(
        ValidationFailureError,
        match="FINAL_REVIEW_ONLY_UPLOAD_FORBIDDEN",
    ):
        publish.verify_confirmation(
            confirmation_id=fake_confirmation.id,
            data=ManualPublishVerificationV2(
                verification_command_id=uuid.uuid4(),
                verification_evidence_ref="local-authority://must-not-publish",
                observed_platform="YOUTUBE",
                observed_platform_channel_id="must-not-be-used",
                observed_destination_account_identity="must-not-be-used",
                observed_platform_video_id="must-not-be-used",
                observed_video_url="https://example.invalid/must-not-publish",
                observed_title="Must not publish",
                observed_privacy_status="PRIVATE",
                observed_published_at=lineage.settlement_now,
                observed_duration_seconds=Decimal("360"),
            ),
            actor=operator,
        )

    with pytest.raises(
        ProgrammingError,
        match="final-review-only destination cannot authorize upload",
    ):
        with db_session.begin_nested():
            db_session.execute(
                insert(FinalVideoDecision).values(
                    final_review_candidate_id=final_candidate.id,
                    decision="UPLOAD",
                )
            )
    for model in (HumanUploadTask, ManualPublishConfirmation, UploadedVideo):
        with pytest.raises(
            ProgrammingError,
            match="final-review-only destination cannot enter publish surface",
        ):
            with db_session.begin_nested():
                db_session.execute(
                    insert(model).values(
                        final_review_candidate_id=final_candidate.id,
                    )
                )
        # A legacy/alternate writer cannot hide the candidate FK while
        # carrying the exact review-only project lineage.
        with pytest.raises(
            ProgrammingError,
            match="final-review-only destination cannot enter publish surface",
        ):
            with db_session.begin_nested():
                db_session.execute(
                    insert(model).values(
                        final_review_candidate_id=None,
                        video_project_id=final_candidate.video_project_id,
                    )
                )


def test_destination_authority_preserves_verified_mode_and_rejects_tamper(
    db_session,
    qualification_factory,
) -> None:
    scope = qualification_factory.channel_scope(
        name="Settlement destination authority",
        strict_long_form=True,
    )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_DESTINATION_NOT_VERIFIED",
    ):
        v2_support_authority._destination_authority(
            db_session,
            channel=scope.channel,
            execution_mode="REAL_LONG_FORM_PRODUCTION",
            script_qualification_run_id=None,
        )
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_DESTINATION_NOT_VERIFIED",
    ):
        v2_support_authority._destination_authority(
            db_session,
            channel=scope.channel,
            execution_mode="REAL_LONG_FORM_PRODUCTION",
            script_qualification_run_id=uuid.uuid4(),
        )

    original_metadata = deepcopy(scope.channel.metadata_)
    tampered_metadata = deepcopy(original_metadata)
    tampered_metadata["destination_governance"]["bindings"][0]["channel_handle"] = (
        "@tampered-without-rehash"
    )
    scope.channel.metadata_ = tampered_metadata
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_DESTINATION_NOT_VERIFIED",
    ):
        v2_support_authority._verified_destination(scope.channel)

    mismatched_metadata = deepcopy(original_metadata)
    mismatched_metadata["destination_governance"]["bindings"][0][
        "verification_state"
    ] = "VERIFIED"
    scope.channel.metadata_ = mismatched_metadata
    with pytest.raises(
        ValidationFailureError,
        match="V2_SUPPORT_DESTINATION_NOT_VERIFIED",
    ):
        v2_support_authority._verified_destination(scope.channel)

    scope.channel.metadata_ = original_metadata
    _configure_verified_destination(db_session, scope)
    verified = v2_support_authority._destination_authority(
        db_session,
        channel=scope.channel,
        execution_mode="REAL_LONG_FORM_PRODUCTION",
        script_qualification_run_id=None,
    ).model_dump(
        mode="json",
    )
    assert verified["binding"]["destination_status"] == "VERIFIED"
    assert verified["binding"]["verification_state"] == "VERIFIED"
    assert "authority_mode" not in verified
    assert "publish_execution_allowed" not in verified
