from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.geo_delivery import (
    ActualPublishDestination,
    AnalyticsConfidenceState,
    ComparableVideoGeoSignal,
    DeliveryVerdict,
    DestinationRuntimeContract,
    GEO_DELIVERY_ACCEPTANCE_GATES,
    GeoAlignmentState,
    GeoAnalyticsInput,
    GeoDeliveryAcceptanceVerdicts,
    GeoDeliveryVerificationGateResult,
    GeoDeliveryVerificationManifest,
    GeoDeliveryVerificationNodeOutcome,
    GeoDeliveryVerificationReceipt,
    GeoDeliveryVerificationReceiptRunEvidence,
    GeoDeliveryVerificationRun,
    GeoMarketDeliveryCloseoutEvidence,
    GeoWindow,
    MarketDeliveryEvidence,
    MetricDataState,
    PlatformRevenueType,
    SelfFundingWindow,
    StrictMarketLineageEnvelope,
)
from app.contracts.m7 import (
    ManualPublishConfirmationCreate,
    PublishHandoffCreate,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    VideoProjectCreate,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    AccessibilityQCReport,
    Artifact,
    ArtifactVersion,
    AssetManifestSnapshot,
    MediaQCReport,
    MediaRenderJob,
    RenderPackageSnapshot,
    RenderSpecSnapshot,
    SceneManifestSnapshot,
    SourceManifestSnapshot,
    User,
    VideoProject,
)
from app.services.geo_delivery import (
    AdsOnlyMonetizationPolicyService,
    GeoDeliveryCloseoutArtifactService,
    GeoDistributionTrackerService,
    GeoMaturityDiagnosticService,
    MarketDeliveryAlignmentGate,
    SelfFundingGate,
    StrictMarketLineageService,
)
from app.services.geo_delivery_verification import (
    GEO_DELIVERY_PYTEST_RUN_ID,
    GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS,
    GEO_DELIVERY_REQUIRED_RUN_IDS,
    GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS,
    GEO_DELIVERY_REQUIRED_TEST_NODES,
    GEO_DELIVERY_REQUIRED_TEST_TARGETS,
    geo_delivery_workspace_hash,
    validate_geo_delivery_verification_scope,
)
from app.services.workflow import ArtifactService
from app.services import (
    ApprovalService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    RBACService,
    VideoProjectService,
)
from app.services.m7 import (
    ManualPublishConfirmationService,
    PublishHandoffService,
)
from app.services.m9 import (
    DiagnosticContext,
    NoViewDiagnosticService,
    _geo_diagnostic_for_context,
)
from app.services.pkg1_market_revision import PKG1MarketRevisionService
from app.services.config_registry import content_hash as registry_content_hash
from tests.qualification.conftest import QualificationFactory
from scripts.closeout_geo_market_delivery import (
    ACTIVE_PACKAGE_COMPONENT_ARTIFACT_STATUSES,
    ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES,
    EXPECTED_BASE_MONETIZATION_POLICY,
    REQUIRED_TARGET_MARKET_CONSISTENCY_CHECKS,
    _artifact_alignment_actuals,
    _require_base_monetization_truth,
    _require_historical_source_project_lineage,
    _require_target_market_consistency_pass,
    _require_version,
)
import scripts.run_geo_delivery_verification as geo_verification_runner


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _verification_manifest(
    *,
    channel_workspace_id: uuid.UUID,
    policy_snapshot_id: uuid.UUID,
    policy_snapshot_hash: str,
    source_package_artifact_version_id: uuid.UUID,
    source_package_content_hash: str,
    passing: bool = True,
) -> GeoDeliveryVerificationManifest:
    outcome = "passed" if passing else "failed"
    node_ids = sorted(
        {
            node_id
            for required in GEO_DELIVERY_REQUIRED_TEST_NODES.values()
            for node_id in required
        }
    )
    node_outcomes = [
        GeoDeliveryVerificationNodeOutcome(
            node_id=node_id,
            outcome=outcome,
        )
        for node_id in node_ids
    ]
    run = GeoDeliveryVerificationRun(
        run_id=GEO_DELIVERY_PYTEST_RUN_ID,
        run_kind="PYTEST",
        command=[
            "python",
            "-m",
            "pytest",
            "-q",
            *GEO_DELIVERY_REQUIRED_TEST_TARGETS,
        ],
        exit_code=0 if passing else 1,
        passed=len(node_ids) if passing else 0,
        failed=0 if passing else len(node_ids),
        skipped=0,
        output_hash=HASH_D,
        verdict=DeliveryVerdict.PASS if passing else DeliveryVerdict.BLOCK,
        node_outcomes=node_outcomes,
    )
    static_runs = [
        GeoDeliveryVerificationRun(
            run_id=run_id,
            run_kind="STATIC_CHECK",
            command=command,
            exit_code=0 if passing else 1,
            passed=int(passing),
            failed=int(not passing),
            skipped=0,
            output_hash=HASH_C,
            verdict=(DeliveryVerdict.PASS if passing else DeliveryVerdict.BLOCK),
            node_outcomes=[],
        )
        for run_id, command in zip(
            GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS,
            (
                ["python", "-m", "compileall", "-q", "app", "scripts"],
                [".venv/bin/alembic", "heads"],
                ["git", "diff", "--check"],
            ),
            strict=True,
        )
    ]
    gate_results = [
        GeoDeliveryVerificationGateResult(
            gate=gate,
            verdict=DeliveryVerdict.PASS if passing else DeliveryVerdict.BLOCK,
            checks={
                "pytest_exit_zero": passing,
                "no_failed_nodes": passing,
                "exact_node_outcomes_recorded": True,
                "all_gate_required_nodes_passed": passing,
                "compileall_passed": passing,
                "alembic_single_head": passing,
                "git_diff_check_passed": passing,
                "all_run_output_hashes_present": True,
            },
            verification_run_ids=list(GEO_DELIVERY_REQUIRED_RUN_IDS),
            required_node_ids=list(GEO_DELIVERY_REQUIRED_TEST_NODES[gate]),
        )
        for gate in GEO_DELIVERY_ACCEPTANCE_GATES
    ]
    workspace_hash = geo_delivery_workspace_hash(ROOT)
    return GeoDeliveryVerificationManifest(
        producer="VCOS_MACHINE_VERIFICATION_RUNNER",
        generated_at=NOW,
        workspace_hash=workspace_hash,
        repository_revision=f"workspace-sha256:{workspace_hash}",
        channel_workspace_id=channel_workspace_id,
        policy_snapshot_id=policy_snapshot_id,
        policy_snapshot_hash=policy_snapshot_hash,
        source_package_artifact_version_id=(source_package_artifact_version_id),
        source_package_content_hash=source_package_content_hash,
        verification_runs=[run, *static_runs],
        gate_results=gate_results,
    )


def _destination(*, status: str = "VERIFIED") -> DestinationRuntimeContract:
    return DestinationRuntimeContract(
        destination_binding_id=uuid.UUID("10000000-0000-0000-0000-000000000001"),
        channel_workspace_id=uuid.UUID("10000000-0000-0000-0000-000000000002"),
        platform="YOUTUBE",
        platform_account_ref="youtube://@SmallTeamAI",
        platform_channel_id=("UC-small-team-ai" if status == "VERIFIED" else None),
        handle="@SmallTeamAI",
        account_country_region="US",
        default_language="en",
        status=status,
        verified_at=(NOW if status == "VERIFIED" else None),
        verification_method=("manual-owner-check" if status == "VERIFIED" else None),
        binding_fingerprint=HASH_A,
    )


def _market_evidence(**overrides: object) -> MarketDeliveryEvidence:
    values: dict[str, object] = {
        "policy_snapshot_id": uuid.UUID("20000000-0000-0000-0000-000000000001"),
        "market_policy_hash": HASH_B,
        "target_market_profile_ref": "artifact-version://target-market-profile/v3",
        "target_market_profile_hash": HASH_C,
        "market_alignment_dossier_ref": "artifact-version://market-dossier/v3",
        "market_alignment_dossier_hash": HASH_D,
        "creative_brief_ref": "artifact-version://creative-brief/1",
        "research_pack_ref": "artifact-version://research-pack/1",
        "script_ref": "artifact-version://script/1",
        "voice_manifest_ref": "artifact-version://voice/1",
        "visual_plan_ref": "artifact-version://visual-plan/1",
        "metadata_package_ref": "artifact-version://metadata/1",
        "caption_plan_ref": "artifact-version://publish-handoff/1#caption-plan",
        "caption_plan_state": "WAITING_FOR_FINAL_AUDIO_ALIGNMENT",
        "caption_artifact_ref": None,
        "thumbnail_brief_ref": "artifact-version://thumbnail/1",
        "publish_package_ref": "artifact-version://publish-package/1",
        "destination_binding_id": _destination().destination_binding_id,
        "destination_binding_fingerprint": HASH_A,
        "expected_market": "US",
        "expected_content_language": "en",
        "expected_locale": "en-US",
        "expected_currency": "USD",
        "expected_unit_system": "US_CUSTOMARY",
        "expected_date_format": "MM/DD/YYYY",
        "expected_timezone": "America/New_York",
        "preferred_source_jurisdictions": ["US", "CA"],
        "acceptable_visual_geos": ["US", "CA"],
        "script_locale": "en-US",
        "voice_locale": "en-US",
        "voice_content_language": "en",
        "metadata_locale": "en-US",
        "metadata_original_language": "en",
        "caption_locales": ["en-US"],
        "currency_contexts": ["USD"],
        "unit_system": "US_CUSTOMARY",
        "date_format": "MM/DD/YYYY",
        "source_jurisdictions": ["US"],
        "local_examples_present": True,
        "visual_geos": ["US"],
        "ui_locales": ["en-US", "en"],
        "destination_market": "US",
        "destination_status": "PENDING_PLATFORM_ID",
        "publish_timezone": "America/New_York",
        "approved_publish_window": {
            "timezone": "America/New_York",
            "days": ["TUE", "THU"],
            "local_time": "10:00",
        },
        "terminology_localized": True,
        "translated_sounding_copy": False,
    }
    values.update(overrides)
    return MarketDeliveryEvidence.model_validate(values)


def _strict_envelope() -> StrictMarketLineageEnvelope:
    evidence = _market_evidence()
    return StrictMarketLineageEnvelope(
        policy_snapshot_id=evidence.policy_snapshot_id,
        approved_market_policy_hash=evidence.market_policy_hash,
        target_market_profile_ref=evidence.target_market_profile_ref,
        target_market_profile_hash=evidence.target_market_profile_hash,
        market_alignment_dossier_ref=evidence.market_alignment_dossier_ref,
        market_alignment_dossier_hash=evidence.market_alignment_dossier_hash,
        destination_binding_id=evidence.destination_binding_id,
        approved_destination_fingerprint=HASH_A,
        approved_platform="YOUTUBE",
        approved_platform_channel_id="UC-small-team-ai",
        approved_handle="@SmallTeamAI",
        approved_package_hash=HASH_D,
        approved_publish_timezone="America/New_York",
        approved_publish_window=evidence.approved_publish_window,
        approval_decision_id=uuid.UUID("30000000-0000-0000-0000-000000000001"),
    )


def _approval(envelope: StrictMarketLineageEnvelope) -> SimpleNamespace:
    package_version_id = uuid.UUID("30000000-0000-0000-0000-000000000002")
    return SimpleNamespace(
        id=envelope.approval_decision_id,
        decision="approved",
        target_type="artifact_version",
        target_id=package_version_id,
        target_artifact_version_id=package_version_id,
        policy_snapshot_id=envelope.policy_snapshot_id,
        destination_binding_id=envelope.destination_binding_id,
        destination_binding_fingerprint=envelope.approved_destination_fingerprint,
        market_policy_hash=envelope.approved_market_policy_hash,
        approved_package_hash=envelope.approved_package_hash,
        target_market_profile_ref=envelope.target_market_profile_ref,
        target_market_profile_hash=envelope.target_market_profile_hash,
        market_alignment_dossier_ref=envelope.market_alignment_dossier_ref,
        market_alignment_dossier_hash=envelope.market_alignment_dossier_hash,
        approved_publish_window=envelope.approved_publish_window,
        metadata_={
            "approved_package_hash": envelope.approved_package_hash,
            "package_artifact_version_id": str(package_version_id),
            "effective_market_policy_hash": envelope.approved_market_policy_hash,
            "destination_binding_id": str(envelope.destination_binding_id),
            "market_alignment_dossier_ref": envelope.market_alignment_dossier_ref,
            "market_alignment_dossier_hash": envelope.market_alignment_dossier_hash,
            "approved_publish_timezone": envelope.approved_publish_timezone,
            "approved_publish_window": envelope.approved_publish_window,
        },
        policy_basis={
            "compiled_channel_policy_snapshot": {
                "id": str(envelope.policy_snapshot_id)
            },
            "target_market_profile": {
                "ref": envelope.target_market_profile_ref,
                "content_hash": envelope.target_market_profile_hash,
            },
            "destination_binding": {
                "id": str(envelope.destination_binding_id),
                "content_hash": envelope.approved_destination_fingerprint,
            },
            "market_alignment_dossier": {
                "ref": envelope.market_alignment_dossier_ref,
                "content_hash": envelope.market_alignment_dossier_hash,
            },
        },
    )


def _strict_m7_fixture(
    db_session: Session,
    tmp_path: Path,
    *,
    destination_status: str,
) -> SimpleNamespace:
    flow = QualificationFactory(db_session).m6_full_flow(output_dir=tmp_path)
    run = flow.production_run
    assert run.visual_plan_snapshot_id is not None
    scene_manifest = SceneManifestSnapshot(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        visual_plan_snapshot_id=run.visual_plan_snapshot_id,
        scene_manifest_blob={"scenes": [{"scene_id": "geo-scene-001"}]},
        scene_manifest_hash=HASH_A,
    )
    db_session.add(scene_manifest)
    db_session.flush()
    asset_manifest = AssetManifestSnapshot(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        scene_manifest_snapshot_id=scene_manifest.id,
        asset_manifest_blob={"candidates": [], "requirements": []},
        asset_manifest_hash=HASH_B,
    )
    db_session.add(asset_manifest)
    db_session.flush()
    source_manifest = SourceManifestSnapshot(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        asset_manifest_snapshot_id=asset_manifest.id,
        source_manifest_blob={"source_refs": [], "manifest_hash": HASH_C},
        source_manifest_hash=HASH_C,
    )
    db_session.add(source_manifest)
    db_session.flush()
    render_spec = RenderSpecSnapshot(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        voice_timeline_snapshot_id=run.voice_timeline_snapshot_id,
        visual_plan_snapshot_id=run.visual_plan_snapshot_id,
        caption_track_snapshot_id=run.caption_track_snapshot_id,
        asset_manifest_snapshot_id=asset_manifest.id,
        scene_manifest_snapshot_id=scene_manifest.id,
        render_spec_blob={
            "caption_track_ref": f"caption-track://{run.caption_track_snapshot_id}",
        },
        render_spec_hash=HASH_D,
        validation_state="PASS",
        reason_codes=[],
    )
    db_session.add(render_spec)
    db_session.flush()
    render_job = MediaRenderJob(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        render_spec_snapshot_id=render_spec.id,
        renderer_key="MOCK_RENDERER",
        status="COMPLETED",
        output_ref={"file_path": str(tmp_path / "geo-final.mp4")},
        reason_codes=["QUALIFICATION_FIXTURE"],
        metadata_={"no_provider_call": True},
    )
    db_session.add(render_job)
    db_session.flush()
    render_package = RenderPackageSnapshot(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        media_render_job_id=render_job.id,
        render_spec_snapshot_id=render_spec.id,
        final_video_ref={"file_path": str(tmp_path / "geo-final.mp4")},
        thumbnail_ref={"file_path": str(tmp_path / "geo-thumbnail.png")},
        caption_ref={"file_path": str(tmp_path / "geo-captions.srt")},
        manifest_ref={"ref": "qualification://geo-render-manifest"},
        file_manifest={"fixture": True},
        checksum_manifest={"geo-final.mp4": HASH_A},
        duration_seconds=Decimal("24"),
        variant_outputs=[],
        package_state="QC_PASSED",
    )
    db_session.add(render_package)
    db_session.flush()
    media_qc = MediaQCReport(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        render_package_snapshot_id=render_package.id,
        render_spec_snapshot_id=render_spec.id,
        qc_state="PASS",
        duration_check={"state": "PASS"},
        scene_coverage_check={"state": "PASS"},
        caption_alignment_check={"state": "PASS"},
        audio_presence_check={"state": "PASS"},
        file_integrity_check={"state": "PASS"},
        manifest_check={"state": "PASS"},
        variant_check={"state": "PASS"},
        reason_codes=[],
    )
    accessibility_qc = AccessibilityQCReport(
        production_artifact_run_id=run.id,
        video_project_id=flow.project.id,
        caption_track_snapshot_id=run.caption_track_snapshot_id,
        render_package_snapshot_id=render_package.id,
        qc_state="PASS",
        caption_presence_check={"state": "PASS"},
        caption_readability_check={"state": "PASS"},
        safe_area_check={"state": "PASS"},
        flashing_risk_check={"state": "PASS"},
        disclosure_placement_check={"state": "PASS"},
        pronunciation_check={"state": "PASS"},
        reason_codes=[],
    )
    db_session.add_all([media_qc, accessibility_qc])
    db_session.flush()
    run.scene_manifest_snapshot_id = scene_manifest.id
    run.asset_manifest_snapshot_id = asset_manifest.id
    run.source_manifest_snapshot_id = source_manifest.id
    run.render_spec_snapshot_id = render_spec.id
    run.render_package_snapshot_id = render_package.id
    run.media_qc_report_id = media_qc.id
    run.accessibility_qc_report_id = accessibility_qc.id
    db_session.flush()

    artifact_service = ArtifactService(db_session)
    package_artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=flow.project.id,
            artifact_type="package_manifest",
            status="approved",
            created_by_user_id=flow.operator.id,
        ),
        correlation_id="geo-m7-package-artifact",
    )
    package_version = artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=package_artifact.id,
            content={
                "schema_version": "qualification.geo-strict-package.v1",
                "render_package_snapshot_id": str(render_package.id),
            },
            status="approved",
            created_by_user_id=flow.operator.id,
        ),
        correlation_id="geo-m7-package-version",
    )
    destination_binding_id = uuid.uuid4()
    approved_publish_window = {
        "timezone": "America/New_York",
        "days": ["TUE", "THU"],
        "local_time": "10:00",
    }
    target_profile_ref = "artifact-version://target-market-profile/geo-m7"
    dossier_ref = "artifact-version://market-alignment-dossier/geo-m7"
    approval = ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=package_version.id,
            target_artifact_version_id=package_version.id,
            decision="approved",
            decided_by_user_id=flow.admin.id,
            metadata={
                "approved_package_hash": package_version.content_hash,
                "package_artifact_version_id": str(package_version.id),
                "effective_market_policy_hash": HASH_B,
                "destination_binding_id": str(destination_binding_id),
                "market_alignment_dossier_ref": dossier_ref,
                "market_alignment_dossier_hash": HASH_D,
                "approved_publish_timezone": "America/New_York",
                "approved_publish_window": approved_publish_window,
            },
            policy_basis={
                "compiled_channel_policy_snapshot": {
                    "id": str(flow.project.policy_snapshot_id),
                },
                "target_market_profile": {
                    "ref": target_profile_ref,
                    "content_hash": HASH_C,
                },
                "destination_binding": {
                    "id": str(destination_binding_id),
                    "content_hash": HASH_A,
                },
                "market_alignment_dossier": {
                    "ref": dossier_ref,
                    "content_hash": HASH_D,
                },
            },
            policy_snapshot_id=flow.project.policy_snapshot_id,
            destination_binding_id=destination_binding_id,
            destination_binding_fingerprint=HASH_A,
            market_policy_hash=HASH_B,
            approved_package_hash=package_version.content_hash,
            target_market_profile_ref=target_profile_ref,
            target_market_profile_hash=HASH_C,
            market_alignment_dossier_ref=dossier_ref,
            market_alignment_dossier_hash=HASH_D,
            approved_publish_window=approved_publish_window,
        ),
        correlation_id="geo-m7-strict-approval",
    )
    platform_channel_id = (
        "UC-small-team-ai" if destination_status == "VERIFIED" else None
    )
    envelope = StrictMarketLineageEnvelope(
        policy_snapshot_id=flow.project.policy_snapshot_id,
        approved_market_policy_hash=HASH_B,
        target_market_profile_ref=target_profile_ref,
        target_market_profile_hash=HASH_C,
        market_alignment_dossier_ref=dossier_ref,
        market_alignment_dossier_hash=HASH_D,
        destination_binding_id=destination_binding_id,
        approved_destination_fingerprint=HASH_A,
        approved_platform="YOUTUBE",
        approved_platform_channel_id=platform_channel_id,
        approved_handle="@SmallTeamAI",
        approved_package_hash=package_version.content_hash,
        approved_publish_timezone="America/New_York",
        approved_publish_window=approved_publish_window,
        approval_decision_id=approval.id,
    )
    destination = DestinationRuntimeContract(
        destination_binding_id=destination_binding_id,
        channel_workspace_id=flow.channel.id,
        platform="YOUTUBE",
        platform_account_ref="youtube://@SmallTeamAI",
        platform_channel_id=platform_channel_id,
        handle="@SmallTeamAI",
        account_country_region="US",
        default_language="en",
        status=destination_status,
        verified_at=NOW if destination_status == "VERIFIED" else None,
        verification_method=(
            "manual-owner-check" if destination_status == "VERIFIED" else None
        ),
        binding_fingerprint=HASH_A,
    )
    return SimpleNamespace(
        **flow.__dict__,
        render_package=render_package,
        package_version=package_version,
        approval=approval,
        envelope=envelope,
        destination=destination,
    )


def _m7_confirmation_data(
    fixture: SimpleNamespace,
    *,
    video_id: str,
    actual_destination: ActualPublishDestination,
) -> ManualPublishConfirmationCreate:
    return ManualPublishConfirmationCreate(
        publish_handoff_package_id=fixture.handoff.id,
        confirmed_by_user_id=fixture.operator.id,
        actual_video_id=video_id,
        actual_video_url=f"https://www.youtube.com/watch?v={video_id}",
        actual_published_at=NOW,
        actual_metadata={
            "actual_title": fixture.project.title,
            "actual_description": fixture.project.description,
            "actual_tags": [],
            "actual_hashtags": [],
            "actual_privacy_status": "PUBLIC",
            "actual_caption_uploaded": True,
            "actual_made_for_kids": False,
        },
        actual_disclosures={
            "ai_disclosure_confirmed": False,
            "ai_disclosure_label_used": None,
            "paid_promotion_disclosure_confirmed": False,
            "music_license_confirmed": True,
            "stock_license_confirmed": True,
            "rights_confirmed": True,
            "operator_confirmed_no_unlicensed_assets": True,
        },
        actual_files={"caption_uploaded": True},
        actual_destination=actual_destination,
    )


def _m7_actual_destination(
    fixture: SimpleNamespace,
    *,
    video_id: str,
    platform_channel_id: str,
) -> ActualPublishDestination:
    return ActualPublishDestination(
        destination_binding_id=fixture.envelope.destination_binding_id,
        destination_binding_fingerprint=(
            fixture.envelope.approved_destination_fingerprint
        ),
        destination_status="VERIFIED",
        platform="YOUTUBE",
        platform_channel_id=platform_channel_id,
        external_video_id=video_id,
        external_video_url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=NOW,
        published_market_policy_hash=(fixture.envelope.approved_market_policy_hash),
        published_package_hash=fixture.envelope.approved_package_hash,
    )


def _analytics(
    *,
    confidence: AnalyticsConfidenceState,
    views_by_geo: dict[str, float] | None,
    unavailable_metrics: list[str] | None = None,
) -> GeoAnalyticsInput:
    return GeoAnalyticsInput(
        analytics_snapshot_id=uuid.uuid4(),
        uploaded_video_id=uuid.uuid4(),
        channel_workspace_id=_destination().channel_workspace_id,
        policy_snapshot_id=_market_evidence().policy_snapshot_id,
        captured_at=NOW,
        published_at=NOW,
        observation_window=GeoWindow.D7,
        confidence_state=confidence,
        views_by_geo=views_by_geo,
        unavailable_metrics=unavailable_metrics or [],
    )


def _tracker(*, confidence: AnalyticsConfidenceState, views: dict[str, float] | None):
    evidence = _market_evidence()
    return GeoDistributionTrackerService().build(
        analytics=_analytics(confidence=confidence, views_by_geo=views),
        destination_binding_id=evidence.destination_binding_id,
        destination_binding_fingerprint=evidence.destination_binding_fingerprint,
        market_policy_hash=evidence.market_policy_hash,
        target_market_profile_ref=evidence.target_market_profile_ref,
        target_market_profile_hash=evidence.target_market_profile_hash,
        expected_primary_geos=["US"],
        acceptable_spillover_geos=["CA"],
    )


def test_market_delivery_gate_pass_warn_and_typed_block() -> None:
    gate = MarketDeliveryAlignmentGate()

    passing = gate.evaluate(_market_evidence())
    assert passing.verdict == DeliveryVerdict.PASS
    assert not passing.reason_codes

    warning = gate.evaluate(_market_evidence(translated_sounding_copy=True))
    assert warning.verdict == DeliveryVerdict.WARN
    assert [reason.value for reason in warning.reason_codes] == [
        "LOCALIZATION_FEELS_TRANSLATED"
    ]

    blocked = gate.evaluate(
        _market_evidence(script_locale="vi-VN", destination_market="VN")
    )
    assert blocked.verdict == DeliveryVerdict.BLOCK
    assert {reason.value for reason in blocked.reason_codes} >= {
        "MARKET_LANGUAGE_MISMATCH",
        "DESTINATION_MARKET_MISMATCH",
    }


def test_alignment_builder_uses_bound_artifact_actuals_and_blocks_mismatch() -> None:
    creative_content = {
        "target_market": "US",
        "primary_locale": "en-US",
        "narration_locale": "en-US",
    }
    script_content = {"language": "en-US"}
    voice_content = {
        "narration_locale": "en-US",
        "content_language": "en",
        "pronunciation_policy": {"locale": "en-US"},
    }
    visual_content = {
        "scenes": [
            {
                "target_market": "US",
                "currency": "USD",
                "units_policy": "US_CUSTOMARY",
                "date_format": "MM/DD/YYYY",
                "market_context": "US_SMALL_BUSINESS",
                "workplace_context": "US_SMALL_BUSINESS",
                "generated_evidence_authority": False,
            }
        ]
    }
    visual_direction_content = {
        "primary_locale": "en-US",
        "target_market": "US",
    }
    visual_decisions_content = {
        "decisions": [{"market_checks": {"foreign_ui_context": False}}]
    }
    research_content = {"source_jurisdiction": "US"}
    metadata_content = {
        "locale": "en-US",
        "original_language": "en",
        "checks": {
            "translated_sounding_copy": False,
            "us_spelling": True,
            "us_search_wording": True,
        },
    }
    thumbnail_content = {
        "target_market": "US",
        "text_locale": "en-US",
        "decision": "PASS",
        "rules": {
            "foreign_market_wording": False,
            "generated_exact_text_allowed": False,
            "unsupported_number_or_claim": False,
            "misleading_ui_or_product": False,
        },
    }
    publish_handoff_content = {
        "caption_plan": {
            "locale": "en-US",
            "artifact_ref": None,
            "state": "WAITING_FOR_FINAL_AUDIO_ALIGNMENT",
        },
        "approved_publish_timezone": "America/New_York",
        "primary_market": "US",
        "primary_locale": "en-US",
        "original_language": "en",
        "approved_publish_window": {
            "timezone": "America/New_York",
            "days": ["TUE"],
            "local_time": "10:00",
        },
    }

    def actuals() -> dict[str, object]:
        return _artifact_alignment_actuals(
            creative_content=creative_content,
            script_content=script_content,
            voice_content=voice_content,
            visual_content=visual_content,
            visual_direction_content=visual_direction_content,
            visual_decisions_content=visual_decisions_content,
            research_content=research_content,
            metadata_content=metadata_content,
            thumbnail_content=thumbnail_content,
            publish_handoff_content=publish_handoff_content,
            expected_market="US",
            expected_content_language="en",
            expected_locale="en-US",
            expected_audience_market_context="US_SMALL_BUSINESS",
            expected_workplace_context="US_SMALL_BUSINESS",
        )

    gate = MarketDeliveryAlignmentGate()
    assert gate.evaluate(_market_evidence(**actuals())).verdict == DeliveryVerdict.PASS

    voice_content["content_language"] = "vi"
    metadata_content["original_language"] = "vi"
    publish_handoff_content["original_language"] = "vi"
    mismatched = gate.evaluate(_market_evidence(**actuals()))
    assert mismatched.verdict == DeliveryVerdict.BLOCK
    assert {item.value for item in mismatched.reason_codes} >= {
        "MARKET_LANGUAGE_MISMATCH",
        "METADATA_LOCALE_MISMATCH",
    }

    voice_content["content_language"] = "en"
    metadata_content["original_language"] = "en"
    publish_handoff_content["original_language"] = "en"
    visual_content["scenes"][0]["market_context"] = "VN_SMALL_BUSINESS"
    context_mismatch = gate.evaluate(_market_evidence(**actuals()))
    assert context_mismatch.verdict != DeliveryVerdict.PASS
    assert "LOCALIZATION_FEELS_TRANSLATED" in {
        item.value for item in context_mismatch.reason_codes
    }

    visual_content["scenes"][0]["market_context"] = "US_SMALL_BUSINESS"
    thumbnail_content["target_market"] = "VN"
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_BOUND_ARTIFACT_MARKET_MISMATCH",
    ):
        actuals()
    thumbnail_content["target_market"] = "US"
    visual_content["scenes"][0]["generated_evidence_authority"] = True
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_VISUAL_GENERATED_EVIDENCE_AUTHORITY_INVALID",
    ):
        actuals()


def test_historical_source_lineage_allows_hash_bound_snapshot_upgrade() -> None:
    channel_id = uuid.uuid4()
    historical_snapshot_id = uuid.uuid4()
    revision_snapshot_id = uuid.uuid4()
    historical_project = SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=channel_id,
        policy_snapshot_id=historical_snapshot_id,
        channel_profile_version_id=uuid.uuid4(),
        title="Historical PKG1",
        project_type="PKG1",
        status="approved",
        created_at=NOW,
    )
    source_project = SimpleNamespace(
        id=uuid.uuid4(),
        channel_workspace_id=channel_id,
        policy_snapshot_id=revision_snapshot_id,
        channel_profile_version_id=uuid.uuid4(),
        title="PKG1 Market Revision",
        project_type="PKG1_MARKET_REVISION",
        status="approved",
        created_at=NOW,
    )
    snapshot = SimpleNamespace(
        id=revision_snapshot_id,
        channel_workspace_id=channel_id,
    )

    class FakeSession:
        def get(self, model: object, key: object) -> object | None:
            if model is VideoProject and key == historical_project.id:
                return historical_project
            if model is VideoProject and key == source_project.id:
                return source_project
            return None

    bindings = {
        "historical_video_project": {
            "ref": f"video-project://{historical_project.id}",
            "content_hash": PKG1MarketRevisionService._project_hash(historical_project),
        },
        "revision_video_project": {
            "ref": f"video-project://{source_project.id}",
            "content_hash": PKG1MarketRevisionService._project_hash(source_project),
        },
    }

    historical_id, resolved_historical, resolved_source = (
        _require_historical_source_project_lineage(
            FakeSession(),
            snapshot=snapshot,
            source_project_id=source_project.id,
            bindings=bindings,
        )
    )
    assert historical_id == historical_project.id
    assert resolved_historical is historical_project
    assert resolved_source is source_project
    assert historical_project.policy_snapshot_id != source_project.policy_snapshot_id

    bindings["historical_video_project"]["content_hash"] = HASH_A
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_HISTORICAL_SOURCE_PROJECT_LINEAGE_INVALID",
    ):
        _require_historical_source_project_lineage(
            FakeSession(),
            snapshot=snapshot,
            source_project_id=source_project.id,
            bindings=bindings,
        )


def test_package_component_authority_and_named_consistency_fail_closed() -> None:
    project_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    version_id = uuid.uuid4()
    content = {"authority": "exact-current-version"}
    version = SimpleNamespace(
        id=version_id,
        artifact_id=artifact_id,
        status="submitted",
        content=content,
        content_hash=registry_content_hash(content),
    )
    artifact = SimpleNamespace(
        id=artifact_id,
        artifact_type="creative_brief",
        video_project_id=project_id,
        current_version_id=version_id,
        status="in_review",
    )

    class FakeSession:
        def get(self, model: object, key: object) -> object | None:
            if model is ArtifactVersion and key == version_id:
                return version
            if model is Artifact and key == artifact_id:
                return artifact
            return None

    binding = {
        "artifact_id": str(artifact_id),
        "artifact_version_id": str(version_id),
        "artifact_version_ref": f"artifact-version://{version_id}",
        "content_hash": version.content_hash,
    }
    assert ACTIVE_PACKAGE_COMPONENT_ARTIFACT_STATUSES == {
        "in_review",
        "approved",
    }
    assert ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES == {
        "submitted",
        "approved",
    }
    assert (
        _require_version(
            FakeSession(),
            binding=binding,
            expected_artifact_type="creative_brief",
            expected_project_id=project_id,
        )
        is version
    )

    artifact.video_project_id = uuid.uuid4()
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_BOUND_VERSION_MISMATCH:creative_brief",
    ):
        _require_version(
            FakeSession(),
            binding=binding,
            expected_artifact_type="creative_brief",
            expected_project_id=project_id,
        )

    consistency = {
        "overall_decision": "PASS",
        "checks": {key: True for key in REQUIRED_TARGET_MARKET_CONSISTENCY_CHECKS},
    }
    _require_target_market_consistency_pass(consistency)
    consistency["checks"].pop("thumbnail_locale_match")
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_MARKET_CONSISTENCY_NOT_PASSING",
    ):
        _require_target_market_consistency_pass(consistency)


def test_strict_lineage_approval_is_complete_and_missing_binding_fails_closed() -> None:
    envelope = _strict_envelope()
    approval = _approval(envelope)
    service = StrictMarketLineageService()

    package_version = SimpleNamespace(
        id=approval.target_artifact_version_id,
        artifact_id=uuid.UUID("30000000-0000-0000-0000-000000000003"),
        content_hash=envelope.approved_package_hash,
    )
    project_id = uuid.UUID("30000000-0000-0000-0000-000000000004")
    package_artifact = SimpleNamespace(
        id=package_version.artifact_id,
        video_project_id=project_id,
    )
    service.validate_approval_record(
        envelope=envelope,
        approval=approval,
        approved_package_version=package_version,
        approved_package_artifact=package_artifact,
        expected_video_project_id=project_id,
    )
    approval.destination_binding_id = None
    with pytest.raises(
        ValidationFailureError,
        match="STRICT_MARKET_APPROVAL_DESTINATION_BINDING_ID_MISSING",
    ):
        service.validate_approval_record(
            envelope=envelope,
            approval=approval,
            approved_package_version=package_version,
            approved_package_artifact=package_artifact,
            expected_video_project_id=project_id,
        )


def test_strict_lineage_actual_destination_must_match_every_approved_hash() -> None:
    envelope = _strict_envelope()
    actual = ActualPublishDestination(
        destination_binding_id=envelope.destination_binding_id,
        destination_binding_fingerprint=envelope.approved_destination_fingerprint,
        destination_status="VERIFIED",
        platform="YOUTUBE",
        platform_channel_id="UC-small-team-ai",
        external_video_id="video-123",
        external_video_url="https://youtube.example/watch?v=video-123",
        published_at=NOW,
        published_market_policy_hash=envelope.approved_market_policy_hash,
        published_package_hash=envelope.approved_package_hash,
    )
    service = StrictMarketLineageService()
    assert (
        service.verify(envelope=envelope, actual=actual).verdict == DeliveryVerdict.PASS
    )

    mismatch = actual.model_copy(update={"published_market_policy_hash": HASH_C})
    result = service.verify(envelope=envelope, actual=mismatch)
    assert result.verdict == DeliveryVerdict.BLOCK
    assert "MARKET_POLICY_HASH_MISMATCH" in result.reason_codes


def test_unverified_destination_blocks_strict_handoff_but_not_closeout_contract() -> (
    None
):
    envelope = _strict_envelope().model_copy(
        update={"approved_platform_channel_id": None}
    )
    destination = _destination(status="PENDING_PLATFORM_ID")
    reasons = StrictMarketLineageService().validate_handoff_context(
        envelope=envelope,
        destination=destination,
        project_policy_snapshot_id=envelope.policy_snapshot_id,
        target_platform="YOUTUBE",
    )
    assert reasons == [
        "DESTINATION_NOT_VERIFIED",
        "DESTINATION_PLATFORM_CHANNEL_ID_MISSING",
    ]


def test_strict_handoff_contract_requires_exact_destination_pair() -> None:
    envelope = _strict_envelope()
    with pytest.raises(ValueError, match="STRICT_MARKET_CONTEXT_REQUIRES"):
        PublishHandoffCreate(
            render_package_snapshot_id=uuid.uuid4(),
            destination_binding_id=envelope.destination_binding_id,
            strict_market_lineage=envelope,
        )


def test_m7_strict_pending_destination_persists_lineage_and_blocks_confirmation(
    db_session: Session,
    tmp_path: Path,
) -> None:
    fixture = _strict_m7_fixture(
        db_session,
        tmp_path,
        destination_status="PENDING_PLATFORM_ID",
    )
    handoff = PublishHandoffService(db_session).create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=fixture.render_package.id,
            destination_binding_id=fixture.envelope.destination_binding_id,
            strict_market_lineage=fixture.envelope,
            destination_runtime=fixture.destination,
            created_by_user_id=fixture.operator.id,
        )
    )
    fixture.handoff = handoff

    assert handoff.package_state == "BLOCKED"
    assert {
        "DESTINATION_NOT_VERIFIED",
        "DESTINATION_PLATFORM_CHANNEL_ID_MISSING",
    } <= set(handoff.reason_codes)
    assert handoff.destination_binding_id == fixture.approval.destination_binding_id
    assert (
        handoff.destination_binding_fingerprint
        == fixture.approval.destination_binding_fingerprint
    )
    assert handoff.market_policy_hash == fixture.approval.market_policy_hash
    assert handoff.approved_package_hash == fixture.approval.approved_package_hash
    assert handoff.approval_decision_id == fixture.approval.id
    assert (
        handoff.target_market_profile_ref == fixture.approval.target_market_profile_ref
    )
    assert (
        handoff.target_market_profile_hash
        == fixture.approval.target_market_profile_hash
    )
    assert (
        handoff.market_alignment_dossier_ref
        == fixture.approval.market_alignment_dossier_ref
    )
    assert (
        handoff.market_alignment_dossier_hash
        == fixture.approval.market_alignment_dossier_hash
    )
    assert handoff.approved_publish_timezone == "America/New_York"
    assert handoff.approved_publish_window == fixture.approval.approved_publish_window
    assert handoff.risk_summary["strict_market_lineage"] == fixture.envelope.model_dump(
        mode="json"
    )
    assert handoff.risk_summary[
        "destination_runtime"
    ] == fixture.destination.model_dump(mode="json")
    with pytest.raises(
        ValidationFailureError,
        match="blocked handoff cannot be marked ready",
    ):
        PublishHandoffService(db_session).mark_ready(handoff_id=handoff.id)

    blocked_actual = _m7_actual_destination(
        fixture,
        video_id="geo-pending-001",
        platform_channel_id="UC-small-team-ai",
    )
    with pytest.raises(
        ValidationFailureError,
        match="strict market handoff is not ready",
    ):
        ManualPublishConfirmationService(db_session).create_confirmation(
            data=_m7_confirmation_data(
                fixture,
                video_id="geo-pending-001",
                actual_destination=blocked_actual,
            )
        )


def test_m7_verified_destination_mismatch_blocks_and_match_propagates(
    db_session: Session,
    tmp_path: Path,
) -> None:
    fixture = _strict_m7_fixture(
        db_session,
        tmp_path,
        destination_status="VERIFIED",
    )
    handoff_service = PublishHandoffService(db_session)
    confirmation_service = ManualPublishConfirmationService(db_session)

    mismatch_handoff = handoff_service.create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=fixture.render_package.id,
            destination_binding_id=fixture.envelope.destination_binding_id,
            strict_market_lineage=fixture.envelope,
            destination_runtime=fixture.destination,
            created_by_user_id=fixture.operator.id,
        )
    )
    fixture.handoff = handoff_service.mark_ready(handoff_id=mismatch_handoff.id)
    mismatch = confirmation_service.create_confirmation(
        data=_m7_confirmation_data(
            fixture,
            video_id="geo-mismatch-001",
            actual_destination=_m7_actual_destination(
                fixture,
                video_id="geo-mismatch-001",
                platform_channel_id="UC-wrong-destination",
            ),
        )
    )
    assert mismatch.confirmation_state == "REVIEW_REQUIRED"
    assert "DESTINATION_PLATFORM_CHANNEL_MISMATCH" in mismatch.reason_codes
    with pytest.raises(
        ValidationFailureError,
        match="only submitted confirmations can be accepted",
    ):
        confirmation_service.accept_confirmation(confirmation_id=mismatch.id)

    matching_handoff = handoff_service.create_from_render_package(
        data=PublishHandoffCreate(
            render_package_snapshot_id=fixture.render_package.id,
            destination_binding_id=fixture.envelope.destination_binding_id,
            strict_market_lineage=fixture.envelope,
            destination_runtime=fixture.destination,
            created_by_user_id=fixture.operator.id,
        )
    )
    fixture.handoff = handoff_service.mark_ready(handoff_id=matching_handoff.id)
    matching = confirmation_service.create_confirmation(
        data=_m7_confirmation_data(
            fixture,
            video_id="geo-match-001",
            actual_destination=_m7_actual_destination(
                fixture,
                video_id="geo-match-001",
                platform_channel_id="UC-small-team-ai",
            ),
        )
    )
    assert matching.confirmation_state == "SUBMITTED"
    uploaded = confirmation_service.accept_confirmation(confirmation_id=matching.id)
    assert uploaded.destination_binding_id == fixture.envelope.destination_binding_id
    assert (
        uploaded.destination_binding_fingerprint
        == fixture.envelope.approved_destination_fingerprint
    )
    assert uploaded.market_policy_hash == fixture.envelope.approved_market_policy_hash
    assert uploaded.approved_package_hash == fixture.envelope.approved_package_hash
    assert uploaded.lineage_refs[
        "strict_market_lineage"
    ] == fixture.envelope.model_dump(mode="json")
    assert uploaded.lineage_refs[
        "destination_runtime"
    ] == fixture.destination.model_dump(mode="json")


def test_geo_tracker_preserves_null_unavailable_and_zero_as_distinct_truth() -> None:
    analytics = _analytics(
        confidence=AnalyticsConfidenceState.STABLE,
        views_by_geo=None,
        unavailable_metrics=["views_by_geo"],
    )
    evidence = _market_evidence()
    tracker = GeoDistributionTrackerService().build(
        analytics=analytics,
        destination_binding_id=evidence.destination_binding_id,
        destination_binding_fingerprint=evidence.destination_binding_fingerprint,
        market_policy_hash=evidence.market_policy_hash,
        target_market_profile_ref=evidence.target_market_profile_ref,
        target_market_profile_hash=evidence.target_market_profile_hash,
        expected_primary_geos=["US"],
        acceptable_spillover_geos=["CA"],
    )
    assert tracker.views_by_geo is None
    assert tracker.metric_states["views_by_geo"] == MetricDataState.UNAVAILABLE
    assert tracker.target_geo_share is None
    assert tracker.latest_alignment_state == GeoAlignmentState.INSUFFICIENT_DATA

    actual_zero = _tracker(
        confidence=AnalyticsConfidenceState.STABLE,
        views={"US": 0.0, "CA": 0.0},
    )
    assert actual_zero.views_by_geo == {"US": 0.0, "CA": 0.0}
    assert actual_zero.metric_states["views_by_geo"] == MetricDataState.AVAILABLE
    assert actual_zero.target_geo_share is None


def test_maturity_rules_only_allow_profile_mismatch_after_three_comparable_videos() -> (
    None
):
    service = GeoMaturityDiagnosticService()
    stable = _tracker(
        confidence=AnalyticsConfidenceState.STABLE,
        views={"US": 20.0, "VN": 80.0},
    )
    stable_result = service.evaluate(tracker=stable)
    assert stable_result.video_reason_codes == ["TARGET_GEO_MISMATCH"]
    assert stable_result.channel_reason_codes == []
    assert stable_result.action_allowed is False

    ready = _tracker(
        confidence=AnalyticsConfidenceState.ACTION_READY,
        views={"US": 20.0, "VN": 80.0},
    )
    signals = [
        ComparableVideoGeoSignal(
            uploaded_video_id=uuid.uuid4(),
            profile_family_ref="profile-family-v3",
            policy_family_ref="policy-family-v3",
            alignment_state=GeoAlignmentState.ACTION_READY,
            confidence_state=AnalyticsConfidenceState.ACTION_READY,
            drift_signature="US_LT_50_PERCENT",
        )
        for _ in range(3)
    ]
    two = service.evaluate(
        tracker=ready,
        comparable_signals=signals[:2],
        profile_family_ref="profile-family-v3",
        policy_family_ref="policy-family-v3",
        drift_signature="US_LT_50_PERCENT",
    )
    assert two.channel_reason_codes == []

    three = service.evaluate(
        tracker=ready,
        comparable_signals=signals,
        profile_family_ref="profile-family-v3",
        policy_family_ref="policy-family-v3",
        drift_signature="US_LT_50_PERCENT",
    )
    assert three.channel_reason_codes == ["PROFILE_MARKET_MISMATCH"]
    assert three.action_allowed is True


@pytest.mark.parametrize(
    "confidence",
    [AnalyticsConfidenceState.TOO_EARLY, AnalyticsConfidenceState.WEAK_SIGNAL],
)
def test_early_geo_confidence_never_emits_target_mismatch(
    confidence: AnalyticsConfidenceState,
) -> None:
    tracker = _tracker(
        confidence=confidence,
        views={"US": 10.0, "VN": 90.0},
    )
    result = GeoMaturityDiagnosticService().evaluate(tracker=tracker)
    assert tracker.latest_alignment_state == GeoAlignmentState.INSUFFICIENT_DATA
    assert "TARGET_GEO_MISMATCH" not in result.video_reason_codes
    assert result.channel_reason_codes == []
    assert result.action_allowed is False


def test_directional_geo_confidence_emits_drift_only() -> None:
    tracker = _tracker(
        confidence=AnalyticsConfidenceState.DIRECTIONAL,
        views={"US": 10.0, "VN": 90.0},
    )
    result = GeoMaturityDiagnosticService().evaluate(tracker=tracker)
    assert tracker.latest_alignment_state == GeoAlignmentState.GEO_DRIFT_DIRECTIONAL
    assert result.video_reason_codes == ["GEO_DRIFT_DIRECTIONAL"]
    assert "TARGET_GEO_MISMATCH" not in result.video_reason_codes
    assert result.channel_reason_codes == []
    assert result.action_allowed is False


def test_m9_geo_tracker_lineage_and_maturity_reason_codes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_tracker = _tracker(
        confidence=AnalyticsConfidenceState.STABLE,
        views={"US": 20.0, "VN": 80.0},
    )
    uploaded_id = uuid.uuid4()
    analytics_snapshot_id = uuid.uuid4()
    tracker_payload = base_tracker.model_dump(
        mode="json",
        exclude={"content_hash"},
    )
    tracker_payload.update(
        {
            "uploaded_video_id": str(uploaded_id),
            "analytics_snapshot_id": str(analytics_snapshot_id),
        }
    )
    tracker = type(base_tracker).model_validate(tracker_payload)
    uploaded = SimpleNamespace(
        id=uploaded_id,
        channel_workspace_id=tracker.channel_workspace_id,
        policy_snapshot_id=tracker.policy_snapshot_id,
    )
    snapshot = SimpleNamespace(
        id=analytics_snapshot_id,
        uploaded_video_id=uploaded_id,
        channel_workspace_id=tracker.channel_workspace_id,
        policy_snapshot_id=tracker.policy_snapshot_id,
        source_metadata={
            "geo_distribution_tracker": tracker.model_dump(mode="json"),
        },
        metric_availability={
            "views": {"state": "AVAILABLE"},
            "impressions": {"state": "AVAILABLE"},
        },
        normalized_metrics_blob={
            "views": {"value": 0},
            "impressions": {"value": 200},
        },
        metrics_blob={},
    )
    context = DiagnosticContext(
        uploaded=uploaded,
        observation_window="T_PLUS_24H",
        window=None,
        analytics_snapshot=snapshot,
        metrics_summary=None,
        retention_snapshot=None,
        traffic_snapshot=None,
        engagement_snapshot=None,
    )
    geo_diagnostic = _geo_diagnostic_for_context(context)
    assert geo_diagnostic is not None
    assert geo_diagnostic.video_reason_codes == ["TARGET_GEO_MISMATCH"]

    class RecordingSession:
        def __init__(self) -> None:
            self.added: list[object] = []

        def add(self, value: object) -> None:
            self.added.append(value)

        def flush(self) -> None:
            return None

    monkeypatch.setattr(
        "app.services.m9._diagnostic_event",
        lambda *_args, **_kwargs: None,
    )
    session = RecordingSession()
    record = NoViewDiagnosticService(session).run(
        run=SimpleNamespace(
            id=uuid.uuid4(),
            uploaded_video_id=uploaded_id,
            observation_window="T_PLUS_24H",
            company_id=uuid.uuid4(),
        ),
        context=context,
        correlation_id="geo-m9-focused",
    )
    assert "TARGET_GEO_MISMATCH" in record.reason_codes
    assert record.evidence_blob["geo_diagnostic"]["video_reason_codes"] == [
        "TARGET_GEO_MISMATCH"
    ]
    assert session.added == [record]

    mismatched_payload = tracker.model_dump(
        mode="json",
        exclude={"content_hash"},
    )
    mismatched_payload["analytics_snapshot_id"] = str(uuid.uuid4())
    mismatched_tracker = type(tracker).model_validate(mismatched_payload)
    snapshot.source_metadata = {
        "geo_distribution_tracker": mismatched_tracker.model_dump(mode="json"),
    }
    with pytest.raises(
        ValidationFailureError,
        match="GEO_TRACKER_ANALYTICS_LINEAGE_MISMATCH",
    ):
        NoViewDiagnosticService(session).run(
            run=SimpleNamespace(
                id=uuid.uuid4(),
                uploaded_video_id=uploaded_id,
                observation_window="T_PLUS_24H",
                company_id=uuid.uuid4(),
            ),
            context=context,
            correlation_id="geo-m9-lineage-mismatch",
        )


def test_incident_blocks_action_and_profile_level_mismatch() -> None:
    tracker = _tracker(
        confidence=AnalyticsConfidenceState.ACTION_READY,
        views={"US": 10.0, "VN": 90.0},
    ).model_copy(
        update={
            "reason_codes": ["PROCESSING_OR_POLICY_INCIDENT"],
            "action_allowed": False,
        }
    )
    signals = [
        ComparableVideoGeoSignal(
            uploaded_video_id=uuid.uuid4(),
            profile_family_ref="profile-family-v3",
            policy_family_ref="policy-family-v3",
            alignment_state=GeoAlignmentState.ACTION_READY,
            confidence_state=AnalyticsConfidenceState.ACTION_READY,
            drift_signature="US_LT_50_PERCENT",
        )
        for _ in range(3)
    ]
    result = GeoMaturityDiagnosticService().evaluate(
        tracker=tracker,
        comparable_signals=signals,
        profile_family_ref="profile-family-v3",
        policy_family_ref="policy-family-v3",
        drift_signature="US_LT_50_PERCENT",
    )
    assert result.video_reason_codes == []
    assert result.channel_reason_codes == []
    assert result.action_allowed is False


def test_ads_only_overlay_preserves_base_snapshot_and_self_funding_uses_finalized_truth() -> (
    None
):
    snapshot_id = uuid.uuid4()
    policy, effective_hash = (
        AdsOnlyMonetizationPolicyService().compile_effective_policy(
            base_policy_snapshot_id=snapshot_id,
            base_policy_snapshot_hash=HASH_A,
            overlay_authority_ref="operator-prompt://geo-closeout/2026-07-21",
        )
    )
    assert policy.base_policy_snapshot_id == snapshot_id
    assert policy.base_policy_snapshot_hash == HASH_A
    assert len(effective_hash) == 64
    assert policy.affiliate_enabled is False
    assert policy.sponsorship_base_case_enabled is False
    assert set(policy.allowed_revenue_types) == set(PlatformRevenueType)

    windows = [
        SelfFundingWindow(
            window_key="2026-05",
            revenue_type=PlatformRevenueType.YOUTUBE_AD_FINALIZED,
            revenue_amount=100,
            revenue_state="FINALIZED",
            allocated_cost=80,
            raw_views=1_000_000,
            estimated_revenue=1_000,
        ),
        SelfFundingWindow(
            window_key="2026-06",
            revenue_type=PlatformRevenueType.YOUTUBE_PREMIUM_FINALIZED,
            revenue_amount=90,
            revenue_state="LOCKED",
            allocated_cost=90,
            projected_revenue=5_000,
        ),
    ]
    result = SelfFundingGate().evaluate(policy=policy, windows=windows)
    assert result.verdict == DeliveryVerdict.PASS
    assert result.consecutive_qualifying_windows == 2
    assert "raw_views" in result.excluded_estimate_fields
    assert "estimated_revenue" in result.excluded_estimate_fields
    assert "projected_revenue" in result.excluded_estimate_fields

    failed = SelfFundingGate().evaluate(
        policy=policy,
        windows=[
            windows[0],
            windows[1].model_copy(update={"revenue_amount": 0}),
        ],
    )
    assert failed.verdict == DeliveryVerdict.BLOCK


def test_closeout_acceptance_has_no_implicit_pass_defaults() -> None:
    with pytest.raises(ValidationError):
        GeoDeliveryAcceptanceVerdicts()


def test_closeout_pins_exact_immutable_base_monetization_truth() -> None:
    payload = {
        "monetization_policy": deepcopy(EXPECTED_BASE_MONETIZATION_POLICY),
        "compiled_policy_snapshot_json": {
            "legacy_policy_sections": {
                "monetization_policy": deepcopy(EXPECTED_BASE_MONETIZATION_POLICY)
            }
        },
    }
    assert _require_base_monetization_truth(payload) == {
        "primary": "mixed",
        "channels": ["adsense", "affiliate"],
    }

    for mutation in (
        {"primary": "mixed", "channels": ["adsense", "saas_affiliate"]},
        {"primary": "mixed", "channels": ["adsense"]},
        {"primary": "ads_only", "channels": ["adsense"]},
    ):
        changed_root = deepcopy(payload)
        changed_root["monetization_policy"] = mutation
        with pytest.raises(
            ValidationFailureError,
            match="GEO_CLOSEOUT_BASE_MONETIZATION_TRUTH_CHANGED",
        ):
            _require_base_monetization_truth(changed_root)

        changed_legacy = deepcopy(payload)
        changed_legacy["compiled_policy_snapshot_json"]["legacy_policy_sections"][
            "monetization_policy"
        ] = mutation
        with pytest.raises(
            ValidationFailureError,
            match="GEO_CLOSEOUT_BASE_MONETIZATION_TRUTH_CHANGED",
        ):
            _require_base_monetization_truth(changed_legacy)


def test_geo_workspace_hash_covers_direct_authority_dependencies(
    tmp_path: Path,
) -> None:
    direct_authority_dependencies = {
        "app/contracts/channel_policy.py",
        "app/contracts/geo_delivery.py",
        "app/contracts/geo_market.py",
        "app/contracts/workflow.py",
        "app/core/config.py",
        "app/core/db.py",
        "app/core/errors.py",
        "app/db/models/__init__.py",
        "app/db/models/channel.py",
        "app/db/models/workflow.py",
        "app/db/session.py",
        "app/services/config_registry.py",
        "app/services/geo_delivery.py",
        "app/services/geo_delivery_verification.py",
        "app/services/pkg1.py",
        "app/services/pkg1_sc04_revision.py",
        "app/services/workflow.py",
        "scripts/closeout_geo_market_delivery.py",
        "scripts/run_geo_delivery_verification.py",
        "tests/qualification/conftest.py",
        "tests/test_pkg1_sc04_visual_revision.py",
    }
    assert direct_authority_dependencies.issubset(GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS)

    for relative_path in GEO_DELIVERY_RELEVANT_WORKSPACE_PATHS:
        target = tmp_path / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative_path).read_bytes())
    baseline = geo_delivery_workspace_hash(tmp_path)

    for relative_path in sorted(direct_authority_dependencies):
        target = tmp_path / relative_path
        original = target.read_bytes()
        target.write_bytes(original + b"\n# geo-delivery-hash-scope-mutation\n")
        assert geo_delivery_workspace_hash(tmp_path) != baseline
        target.write_bytes(original)

    next_migration = tmp_path / "alembic/versions/0042_test_hash_scope.py"
    next_migration.write_text(
        'revision = "0042_test_hash_scope"\ndown_revision = "0041_geo_delivery"\n',
        encoding="utf-8",
    )
    assert geo_delivery_workspace_hash(tmp_path) != baseline


def test_verification_manifest_rejects_missing_required_passing_node() -> None:
    manifest = _verification_manifest(
        channel_workspace_id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        policy_snapshot_hash=HASH_A,
        source_package_artifact_version_id=uuid.uuid4(),
        source_package_content_hash=HASH_B,
    )
    payload = manifest.model_dump(mode="json")
    payload.pop("content_hash")
    payload["gate_results"][0].pop("content_hash")
    payload["gate_results"][0]["required_node_ids"].append(
        "tests/missing.py::test_deleted_gate_evidence"
    )
    with pytest.raises(
        ValidationError,
        match="GEO_VERIFICATION_REQUIRED_NODE_NOT_PASSING",
    ):
        GeoDeliveryVerificationManifest.model_validate(payload)


def test_verification_scope_requires_all_static_runs_per_gate() -> None:
    manifest = _verification_manifest(
        channel_workspace_id=uuid.uuid4(),
        policy_snapshot_id=uuid.uuid4(),
        policy_snapshot_hash=HASH_A,
        source_package_artifact_version_id=uuid.uuid4(),
        source_package_content_hash=HASH_B,
    )
    missing_run_id = GEO_DELIVERY_REQUIRED_STATIC_RUN_IDS[-1]
    incomplete = manifest.model_copy(
        update={
            "verification_runs": [
                item
                for item in manifest.verification_runs
                if item.run_id != missing_run_id
            ],
            "gate_results": [
                item.model_copy(
                    update={
                        "verification_run_ids": [
                            run_id
                            for run_id in item.verification_run_ids
                            if run_id != missing_run_id
                        ]
                    }
                )
                for item in manifest.gate_results
            ],
        }
    )
    with pytest.raises(
        ValueError,
        match="GEO_VERIFICATION_REQUIRED_RUN_SET_CHANGED",
    ):
        validate_geo_delivery_verification_scope(incomplete)


def test_verification_runner_restores_database_env_and_both_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_clears: list[str] = []
    monkeypatch.setattr(
        geo_verification_runner,
        "get_settings",
        SimpleNamespace(cache_clear=lambda: cache_clears.append("settings")),
    )
    monkeypatch.setattr(
        geo_verification_runner,
        "reset_db_caches",
        lambda: cache_clears.append("db"),
    )
    monkeypatch.setenv("VCOS_DATABASE_URL", "postgresql://temporary-test")

    geo_verification_runner._restore_database_runtime(
        original_database_url="postgresql://production-authority",
        database_url_was_set=True,
    )

    assert (
        geo_verification_runner.os.environ["VCOS_DATABASE_URL"]
        == "postgresql://production-authority"
    )
    assert cache_clears == ["settings", "db"]


def test_verification_runner_preserves_canonical_command_when_pytest_mutates_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_args: list[list[str]] = []

    def mutate_pytest_args(args: list[str], **_: object) -> int:
        received_args.append(args)
        args.insert(0, "-ra")
        return 0

    monkeypatch.setattr(geo_verification_runner.pytest, "main", mutate_pytest_args)
    monkeypatch.setattr(
        geo_verification_runner,
        "_restore_database_runtime",
        lambda **_: None,
    )

    run = geo_verification_runner._run_pytest()

    assert received_args[0][0] == "-ra"
    assert run.command[1:] == [
        "-m",
        "pytest",
        "-q",
        *GEO_DELIVERY_REQUIRED_TEST_TARGETS,
    ]


def test_verification_receipt_uses_valid_submitted_lifecycle() -> None:
    artifact = ArtifactCreate(
        video_project_id=uuid.uuid4(),
        artifact_type="geo_delivery_verification_receipt",
        status=geo_verification_runner.GEO_VERIFICATION_RECEIPT_ARTIFACT_STATUS,
        created_by_user_id=uuid.uuid4(),
    )
    version = ArtifactVersionCreate(
        artifact_id=uuid.uuid4(),
        status=geo_verification_runner.GEO_VERIFICATION_RECEIPT_VERSION_STATUS,
        created_by_user_id=uuid.uuid4(),
    )

    assert artifact.status == "in_review"
    assert version.status == "submitted"
    assert (
        GeoDeliveryCloseoutArtifactService.VERIFICATION_RECEIPT_ARTIFACT_STATUSES
        == {"in_review"}
    )
    assert GeoDeliveryCloseoutArtifactService.VERIFICATION_RECEIPT_VERSION_STATUSES == {
        "submitted"
    }
    assert (
        artifact.status
        in GeoDeliveryCloseoutArtifactService.VERIFICATION_RECEIPT_ARTIFACT_STATUSES
    )
    assert (
        version.status
        in GeoDeliveryCloseoutArtifactService.VERIFICATION_RECEIPT_VERSION_STATUSES
    )
    with pytest.raises(ValidationError):
        ArtifactCreate(
            video_project_id=uuid.uuid4(),
            artifact_type="geo_delivery_verification_receipt",
            status="submitted",
            created_by_user_id=uuid.uuid4(),
        )


def test_closeout_artifacts_are_submitted_immutable_and_idempotent(
    db_session: Session,
) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    company = CompanyService(db_session).create_company(name="Geo Closeout Co")
    operator = User(
        email="geo-closeout@example.com",
        display_name="Geo Closeout",
        status="active",
    )
    reviewer = User(
        email="geo-closeout-reviewer@example.com",
        display_name="Geo Closeout Reviewer",
        status="active",
    )
    db_session.add_all([operator, reviewer])
    db_session.flush()
    RBACService(db_session).assign_role(
        user_id=operator.id,
        role_key="operator",
        company_id=company.id,
    )
    RBACService(db_session).assign_role(
        user_id=reviewer.id,
        role_key="operator",
        company_id=company.id,
    )
    RBACService(db_session).assign_role(
        user_id=reviewer.id,
        role_key="company_admin",
        company_id=company.id,
    )
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key="geo-closeout",
            name="Geo Closeout",
            primary_language="en",
            primary_region="US",
            target_market="US",
            default_timezone="America/New_York",
        ),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(db_session).compile(
        profile_version_id=profile.id,
        correlation_id="geo-closeout-test-compile",
    )
    snapshot = ChannelProfileService(db_session).activate_snapshot(
        snapshot_id=compiled.snapshot_id
    )
    source_project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            channel_profile_version_id=profile.id,
            title="APPROVED_PKG1_SOURCE",
            status="approved",
            project_type="PKG1_MARKET_REVISION",
            created_by_user_id=operator.id,
        )
    )
    artifact_service = ArtifactService(db_session)
    component_versions: dict[str, ArtifactVersion] = {}
    for artifact_type in (
        "creative_brief",
        "research_pack",
        "script",
        "voice_policy",
        "visual_plan",
        "publishing_metadata_package",
        "thumbnail_brief",
        "market_alignment_dossier",
        "publish_handoff_package",
    ):
        component_artifact = artifact_service.create_artifact(
            data=ArtifactCreate(
                video_project_id=source_project.id,
                artifact_type=artifact_type,
                status="approved",
                created_by_user_id=operator.id,
            ),
            correlation_id=f"geo-closeout-component-{artifact_type}",
        )
        component_versions[artifact_type] = artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=component_artifact.id,
                content={"artifact_type": artifact_type},
                status="approved",
                created_by_user_id=operator.id,
            ),
            correlation_id=(f"geo-closeout-component-version-{artifact_type}"),
        )
    component_bindings = {
        key: {
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "artifact_version_ref": f"artifact-version://{version.id}",
            "content_hash": version.content_hash,
        }
        for key, version in component_versions.items()
    }
    source_package_artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=source_project.id,
            artifact_type="package_manifest",
            status="approved",
            created_by_user_id=operator.id,
        ),
        correlation_id="geo-closeout-source-package",
    )
    source_package = artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=source_package_artifact.id,
            content={
                "schema_version": "test.pkg1-market-revision.v1",
                "package_status": "APPROVED",
                "revised_artifacts": component_bindings,
                "reused_artifacts": {},
                "exact_bindings": {
                    "target_market_profile": {
                        "ref": "artifact-version://target-market-profile/v3",
                        "content_hash": HASH_C,
                    }
                },
            },
            status="approved",
            created_by_user_id=operator.id,
        ),
        correlation_id="geo-closeout-source-package-version",
    )
    approval = ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=source_package.id,
            target_artifact_version_id=source_package.id,
            decision="approved",
            decided_by_user_id=reviewer.id,
            metadata={
                "approval_scope": "PKG1_MARKET_REVISION_PACKAGE_PLANNING",
                "package_artifact_version_id": str(source_package.id),
                "package_content_hash": source_package.content_hash,
                "production_package_approved": True,
                "mr1_execution_authorized": False,
                "publish_execution_authorized": False,
            },
        ),
        correlation_id="geo-closeout-source-approval",
    )
    receipt_artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=source_project.id,
            artifact_type="pkg1_market_revision_human_review_receipt",
            status="approved",
            created_by_user_id=reviewer.id,
        ),
        correlation_id="geo-closeout-source-receipt",
    )
    artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=receipt_artifact.id,
            content={
                "approval_scope": "PKG1_MARKET_REVISION_PACKAGE_PLANNING",
                "approval_decision_id": str(approval.id),
                "receipt_content_authority": "ARTIFACT_VERSION_CONTENT_HASH",
                "decision": "PASS",
                "decision_source": "OPERATOR",
                "review_authority": "HUMAN",
                "revision": {"video_project_id": str(source_project.id)},
                "reviewed_package": {
                    "artifact_version_id": str(source_package.id),
                    "artifact_version_ref": (f"artifact-version://{source_package.id}"),
                    "content_hash": source_package.content_hash,
                },
            },
            status="approved",
            created_by_user_id=reviewer.id,
        ),
        correlation_id="geo-closeout-source-receipt-version",
    )
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            channel_profile_version_id=profile.id,
            title="GEO_MARKET_DELIVERY_CLOSEOUT",
            project_type="GEO_MARKET_DELIVERY_CLOSEOUT",
            created_by_user_id=operator.id,
        )
    )

    destination = DestinationRuntimeContract.model_validate(
        {
            **_destination(status="PENDING_PLATFORM_ID").model_dump(
                mode="json", exclude={"content_hash"}
            ),
            "channel_workspace_id": str(channel.id),
        }
    )
    _policy, effective_hash = (
        AdsOnlyMonetizationPolicyService().compile_effective_policy(
            base_policy_snapshot_id=snapshot.id,
            base_policy_snapshot_hash=snapshot.content_hash,
            overlay_authority_ref="operator-prompt://geo-closeout/2026-07-21",
        )
    )
    alignment_evidence = _market_evidence(
        policy_snapshot_id=snapshot.id,
        market_policy_hash=effective_hash,
        destination_binding_id=destination.destination_binding_id,
        destination_binding_fingerprint=destination.binding_fingerprint,
        creative_brief_ref=component_bindings["creative_brief"]["artifact_version_ref"],
        research_pack_ref=component_bindings["research_pack"]["artifact_version_ref"],
        script_ref=component_bindings["script"]["artifact_version_ref"],
        voice_manifest_ref=component_bindings["voice_policy"]["artifact_version_ref"],
        visual_plan_ref=component_bindings["visual_plan"]["artifact_version_ref"],
        metadata_package_ref=component_bindings["publishing_metadata_package"][
            "artifact_version_ref"
        ],
        thumbnail_brief_ref=component_bindings["thumbnail_brief"][
            "artifact_version_ref"
        ],
        market_alignment_dossier_ref=component_bindings["market_alignment_dossier"][
            "artifact_version_ref"
        ],
        market_alignment_dossier_hash=component_versions[
            "market_alignment_dossier"
        ].content_hash,
        caption_plan_ref=(
            component_bindings["publish_handoff_package"]["artifact_version_ref"]
            + "#caption-plan"
        ),
        publish_package_ref=f"artifact-version://{source_package.id}",
    )
    alignment = MarketDeliveryAlignmentGate().evaluate(alignment_evidence)
    service = GeoDeliveryCloseoutArtifactService(db_session)
    verification_manifest = _verification_manifest(
        channel_workspace_id=channel.id,
        policy_snapshot_id=snapshot.id,
        policy_snapshot_hash=snapshot.content_hash,
        source_package_artifact_version_id=source_package.id,
        source_package_content_hash=source_package.content_hash,
    )

    def persist_verification_receipt(
        manifest: GeoDeliveryVerificationManifest,
    ) -> ArtifactVersion:
        receipt_model = GeoDeliveryVerificationReceipt(
            producer="VCOS_MACHINE_VERIFICATION_RUNNER",
            manifest=manifest,
            run_evidence=[
                GeoDeliveryVerificationReceiptRunEvidence(
                    run_id=item.run_id,
                    run_kind=item.run_kind,
                    command=list(item.command),
                    exit_code=item.exit_code,
                    output_hash=item.output_hash,
                    verdict=item.verdict,
                )
                for item in manifest.verification_runs
            ],
        )
        receipt_artifact = artifact_service.create_artifact(
            data=ArtifactCreate(
                video_project_id=source_project.id,
                artifact_type="geo_delivery_verification_receipt",
                status="in_review",
                created_by_user_id=reviewer.id,
            ),
            correlation_id=f"geo-test-verification-{manifest.content_hash}",
        )
        return artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=receipt_artifact.id,
                content=receipt_model.model_dump(mode="json"),
                status="submitted",
                created_by_user_id=reviewer.id,
                evidence_refs=[
                    {
                        "type": "source_package_manifest",
                        "artifact_version_id": str(source_package.id),
                        "content_hash": source_package.content_hash,
                    }
                ],
                packaging_metadata={
                    "producer": "VCOS_MACHINE_VERIFICATION_RUNNER",
                    "manifest_content_hash": manifest.content_hash,
                    "workspace_hash": manifest.workspace_hash,
                },
            ),
            correlation_id=(f"geo-test-verification-version-{manifest.content_hash}"),
        )

    verification_receipt = persist_verification_receipt(verification_manifest)
    kwargs = {
        "video_project_id": project.id,
        "created_by_user_id": operator.id,
        "base_policy_snapshot_id": snapshot.id,
        "base_policy_snapshot_hash": snapshot.content_hash,
        "source_package_artifact_version_id": source_package.id,
        "source_package_content_hash": source_package.content_hash,
        "overlay_authority_ref": "operator-prompt://geo-closeout/2026-07-21",
        "destination_runtime": destination,
        "market_alignment_evidence": alignment_evidence,
        "market_alignment_result": alignment,
        "verification_receipt_artifact_version_id": verification_receipt.id,
        "verification_receipt_content_hash": verification_receipt.content_hash,
    }
    approval_metadata = dict(approval.metadata_ or {})
    approval.metadata_ = {
        **approval_metadata,
        "production_package_approved": False,
    }
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_SOURCE_APPROVAL_LINEAGE_INVALID",
    ):
        service.ensure_closeout_artifacts(**kwargs)
    approval.metadata_ = approval_metadata
    db_session.flush()

    stale_payload = verification_manifest.model_dump(mode="json")
    stale_payload.pop("content_hash")
    stale_payload["workspace_hash"] = HASH_A
    stale_payload["repository_revision"] = f"workspace-sha256:{HASH_A}"
    stale_manifest = GeoDeliveryVerificationManifest.model_validate(stale_payload)
    stale_receipt = persist_verification_receipt(stale_manifest)
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_VERIFICATION_WORKSPACE_STALE",
    ):
        service.ensure_closeout_artifacts(
            **{
                **kwargs,
                "verification_receipt_artifact_version_id": stale_receipt.id,
                "verification_receipt_content_hash": stale_receipt.content_hash,
            }
        )
    blocked_manifest = _verification_manifest(
        channel_workspace_id=channel.id,
        policy_snapshot_id=snapshot.id,
        policy_snapshot_hash=snapshot.content_hash,
        source_package_artifact_version_id=source_package.id,
        source_package_content_hash=source_package.content_hash,
        passing=False,
    )
    blocked_receipt = persist_verification_receipt(blocked_manifest)
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_VERIFICATION_NOT_PASSING",
    ):
        service.ensure_closeout_artifacts(
            **{
                **kwargs,
                "verification_receipt_artifact_version_id": blocked_receipt.id,
                "verification_receipt_content_hash": (blocked_receipt.content_hash),
            }
        )
    wrong_channel_destination = DestinationRuntimeContract.model_validate(
        {
            **destination.model_dump(mode="json", exclude={"content_hash"}),
            "channel_workspace_id": str(uuid.uuid4()),
        }
    )
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_DESTINATION_CHANNEL_MISMATCH",
    ):
        service.ensure_closeout_artifacts(
            **{**kwargs, "destination_runtime": wrong_channel_destination}
        )
    first = service.ensure_closeout_artifacts(**kwargs)
    second = service.ensure_closeout_artifacts(**kwargs)

    assert first["effective_ads_only_policy"] == second["effective_ads_only_policy"]
    assert first["geo_closeout_evidence"] == second["geo_closeout_evidence"]
    assert first["destination_status"] == "PENDING_PLATFORM_ID"
    assert first["upload_ready"] is False
    assert first["publish_execution_ready"] is False
    closeout_version = db_session.get(
        ArtifactVersion,
        first["geo_closeout_evidence"].artifact_version_id,
    )
    assert closeout_version is not None
    persisted_closeout = GeoMarketDeliveryCloseoutEvidence.model_validate(
        closeout_version.content
    )
    assert persisted_closeout.no_execution_proof.all_deltas_zero is True
    assert set(persisted_closeout.no_execution_proof.deltas.values()) == {0}
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == project.id)
        )
        == 2
    )
    versions = list(
        db_session.scalars(
            select(ArtifactVersion)
            .join(Artifact, Artifact.id == ArtifactVersion.artifact_id)
            .where(Artifact.video_project_id == project.id)
        ).all()
    )
    assert len(versions) == 2
    assert {version.status for version in versions} == {"submitted"}

    closeout_version = db_session.get(
        ArtifactVersion,
        first["geo_closeout_evidence"].artifact_version_id,
    )
    assert closeout_version is not None
    original_context_refs = deepcopy(closeout_version.context_refs)
    closeout_version.context_refs = []
    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_CURRENT_ARTIFACT_INVALID",
    ):
        service.ensure_closeout_artifacts(**kwargs)
    closeout_version.context_refs = original_context_refs
    original_content = deepcopy(closeout_version.content)
    with pytest.raises(
        ProgrammingError,
        match="artifact_versions are immutable after creation",
    ):
        with db_session.begin_nested():
            closeout_version.content = {
                **closeout_version.content,
                "destination_status": "TAMPERED",
            }
            db_session.flush()
    db_session.expire(closeout_version, ["content"])
    assert closeout_version.content == original_content
