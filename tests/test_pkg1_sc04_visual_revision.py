from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
)
from app.contracts.geo_delivery import (
    GeoDeliveryVerificationManifest,
    GeoDeliveryVerificationReceipt,
    GeoDeliveryVerificationReceiptRunEvidence,
)
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    CompiledChannelPolicySnapshot,
    ReviewTask,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.geo_delivery import (
    AdsOnlyMonetizationPolicyService,
    GeoDeliveryCloseoutArtifactService,
    destination_runtime_contract,
    MarketDeliveryAlignmentGate,
)
from app.services.pkg1_market_revision_closeout import (
    PKG1MarketRevisionCloseoutService,
)
from app.services.pkg1_sc04_revision import (
    DIFF_ARTIFACT_TYPE,
    PROJECT_TYPE,
    REVIEW_PACKET_ARTIFACT_TYPE,
    PKG1SC04RevisionService,
)
from app.services.workflow import ApprovalService, ArtifactService
from tests.test_geo_market_delivery_closeout import (
    _market_evidence,
    _verification_manifest,
)
from tests.test_pkg1_market_revision_human_closeout import _pending_revision


def _create_artifact(
    session,
    *,
    project_id: uuid.UUID,
    artifact_type: str,
    actor_id: uuid.UUID,
    content: dict,
) -> ArtifactVersion:
    service = ArtifactService(session)
    artifact = service.create_artifact(
        data=ArtifactCreate(
            video_project_id=project_id,
            artifact_type=artifact_type,
            status="in_review",
            created_by_user_id=actor_id,
        ),
        correlation_id=f"test-sc04-{artifact_type}",
    )
    return service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content=deepcopy(content),
            status="submitted",
            created_by_user_id=actor_id,
        ),
        correlation_id=f"test-sc04-version-{artifact_type}",
    )


def _detached_version_with_content(
    version: ArtifactVersion,
    content: dict,
) -> SimpleNamespace:
    """Return the immutable identity fields with test-only detached content."""
    detached_content = deepcopy(content)
    return SimpleNamespace(
        id=version.id,
        artifact_id=version.artifact_id,
        version_number=version.version_number,
        content=detached_content,
        content_hash=content_hash(detached_content),
    )


def _geo_authority(session, *, project, actor_id):
    snapshot = session.get(CompiledChannelPolicySnapshot, project.policy_snapshot_id)
    assert snapshot is not None
    _policy, effective_hash = (
        AdsOnlyMonetizationPolicyService().compile_effective_policy(
            base_policy_snapshot_id=snapshot.id,
            base_policy_snapshot_hash=snapshot.content_hash,
            overlay_authority_ref="operator-policy://vcos/ads-only/phase-a",
        )
    )
    policy = ChannelScopedPolicy.model_validate(
        (snapshot.compiled_payload or {})["channel_scoped_policy"]
    )
    assert policy.destination_binding_policy is not None
    destination_ref = (snapshot.compiled_payload or {})["snapshot_refs"][
        "destination_binding"
    ]["ref"]
    destination = destination_runtime_contract(
        policy.destination_binding_policy.destination,
        canonical_ref=destination_ref,
    )
    package_artifact = session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == project.id,
            Artifact.artifact_type == "package_manifest",
        )
    )
    assert package_artifact is not None and package_artifact.current_version_id
    source_package = session.get(ArtifactVersion, package_artifact.current_version_id)
    assert source_package is not None
    package_content = source_package.content or {}
    artifact_bindings = {
        **(package_content.get("reused_artifacts") or {}),
        **(package_content.get("revised_artifacts") or {}),
    }

    def _binding(artifact_type: str) -> dict:
        binding = artifact_bindings.get(artifact_type)
        assert isinstance(binding, dict)
        assert binding.get("artifact_version_ref")
        assert binding.get("content_hash")
        return binding

    target_market = (package_content.get("exact_bindings") or {}).get(
        "target_market_profile"
    ) or {}
    market_alignment_evidence = _market_evidence(
        policy_snapshot_id=snapshot.id,
        market_policy_hash=effective_hash,
        target_market_profile_ref=target_market["ref"],
        target_market_profile_hash=target_market["content_hash"],
        market_alignment_dossier_ref=_binding("market_alignment_dossier")[
            "artifact_version_ref"
        ],
        market_alignment_dossier_hash=_binding("market_alignment_dossier")[
            "content_hash"
        ],
        creative_brief_ref=_binding("creative_brief")["artifact_version_ref"],
        research_pack_ref=_binding("research_pack")["artifact_version_ref"],
        script_ref=_binding("script")["artifact_version_ref"],
        voice_manifest_ref=_binding("voice_policy")["artifact_version_ref"],
        visual_plan_ref=_binding("visual_plan")["artifact_version_ref"],
        metadata_package_ref=_binding("publishing_metadata_package")[
            "artifact_version_ref"
        ],
        caption_plan_ref=(
            _binding("publish_handoff_package")["artifact_version_ref"]
            + "#caption-plan"
        ),
        thumbnail_brief_ref=_binding("thumbnail_brief")["artifact_version_ref"],
        publish_package_ref=f"artifact-version://{source_package.id}",
        destination_binding_id=destination.destination_binding_id,
        destination_binding_fingerprint=destination.binding_fingerprint,
        destination_status=destination.status,
    )
    market_alignment = MarketDeliveryAlignmentGate().evaluate(market_alignment_evidence)
    verification_manifest = _verification_manifest(
        channel_workspace_id=project.channel_workspace_id,
        policy_snapshot_id=snapshot.id,
        policy_snapshot_hash=snapshot.content_hash,
        source_package_artifact_version_id=source_package.id,
        source_package_content_hash=source_package.content_hash,
    )
    verification_receipt_model = GeoDeliveryVerificationReceipt(
        producer="VCOS_MACHINE_VERIFICATION_RUNNER",
        manifest=verification_manifest,
        run_evidence=[
            GeoDeliveryVerificationReceiptRunEvidence(
                run_id=item.run_id,
                run_kind=item.run_kind,
                command=list(item.command),
                exit_code=item.exit_code,
                output_hash=item.output_hash,
                verdict=item.verdict,
            )
            for item in verification_manifest.verification_runs
        ],
    )
    artifact_service = ArtifactService(session)
    verification_receipt_artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=project.id,
            artifact_type="geo_delivery_verification_receipt",
            status="in_review",
            created_by_user_id=actor_id,
        ),
        correlation_id="test-sc04-geo-verification-receipt",
    )
    verification_receipt = artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=verification_receipt_artifact.id,
            content=verification_receipt_model.model_dump(mode="json"),
            status="submitted",
            created_by_user_id=actor_id,
            evidence_refs=[
                {
                    "type": "source_package_manifest",
                    "artifact_version_id": str(source_package.id),
                    "content_hash": source_package.content_hash,
                }
            ],
            packaging_metadata={
                "producer": "VCOS_MACHINE_VERIFICATION_RUNNER",
                "manifest_content_hash": verification_manifest.content_hash,
                "workspace_hash": verification_manifest.workspace_hash,
            },
        ),
        correlation_id="test-sc04-geo-verification-receipt-version",
    )
    result = GeoDeliveryCloseoutArtifactService(session).ensure_closeout_artifacts(
        video_project_id=project.id,
        created_by_user_id=actor_id,
        base_policy_snapshot_id=snapshot.id,
        base_policy_snapshot_hash=snapshot.content_hash,
        source_package_artifact_version_id=source_package.id,
        source_package_content_hash=source_package.content_hash,
        overlay_authority_ref="operator-policy://vcos/ads-only/phase-a",
        destination_runtime=destination,
        market_alignment_evidence=market_alignment_evidence,
        market_alignment_result=market_alignment,
        verification_receipt_artifact_version_id=verification_receipt.id,
        verification_receipt_content_hash=verification_receipt.content_hash,
    )
    overlay = session.get(
        ArtifactVersion, result["effective_ads_only_policy"].artifact_version_id
    )
    closeout = session.get(
        ArtifactVersion, result["geo_closeout_evidence"].artifact_version_id
    )
    assert overlay is not None and closeout is not None
    return overlay, closeout, effective_hash


def _persist_workspace_revalidation(
    session,
    *,
    closeout: ArtifactVersion,
    actor_id: uuid.UUID,
    workspace_hash: str,
    manifest_updates: dict | None = None,
    passing: bool = True,
    tamper: str | None = None,
) -> ArtifactVersion:
    closeout_content = closeout.content or {}
    original_receipt = session.get(
        ArtifactVersion,
        uuid.UUID(closeout_content["verification_receipt_artifact_version_id"]),
    )
    assert original_receipt is not None
    original_receipt_artifact = session.get(Artifact, original_receipt.artifact_id)
    assert original_receipt_artifact is not None
    typed_original = GeoDeliveryVerificationReceipt.model_validate(
        original_receipt.content
    )
    source_manifest = typed_original.manifest
    if not passing:
        source_manifest = _verification_manifest(
            channel_workspace_id=source_manifest.channel_workspace_id,
            policy_snapshot_id=source_manifest.policy_snapshot_id,
            policy_snapshot_hash=source_manifest.policy_snapshot_hash,
            source_package_artifact_version_id=(
                source_manifest.source_package_artifact_version_id
            ),
            source_package_content_hash=source_manifest.source_package_content_hash,
            passing=False,
        )
    manifest_payload = source_manifest.model_dump(
        mode="json", exclude={"content_hash"}
    )
    manifest_payload.update(
        {
            "workspace_hash": workspace_hash,
            "repository_revision": f"workspace-sha256:{workspace_hash}",
            **(manifest_updates or {}),
        }
    )
    manifest = GeoDeliveryVerificationManifest.model_validate(manifest_payload)
    receipt = GeoDeliveryVerificationReceipt(
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
    receipt_content = receipt.model_dump(mode="json")
    packaging_metadata = {
        "producer": "VCOS_MACHINE_VERIFICATION_RUNNER",
        "manifest_content_hash": manifest.content_hash,
        "workspace_hash": manifest.workspace_hash,
    }
    if tamper == "content":
        receipt_content["run_evidence"][0]["output_hash"] = "0" * 64
    elif tamper == "producer":
        packaging_metadata["producer"] = "UNTRUSTED_TEST_PRODUCER"
    artifact_service = ArtifactService(session)
    artifact = artifact_service.create_artifact(
        data=ArtifactCreate(
            video_project_id=original_receipt_artifact.video_project_id,
            artifact_type="geo_delivery_verification_receipt",
            status="in_review",
            created_by_user_id=actor_id,
        ),
        correlation_id=f"test-sc04-workspace-revalidation-{uuid.uuid4()}",
    )
    return artifact_service.create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content=receipt_content,
            status="submitted",
            created_by_user_id=actor_id,
            evidence_refs=[
                {
                    "type": "source_package_manifest",
                    "artifact_version_id": str(
                        manifest.source_package_artifact_version_id
                    ),
                    "content_hash": manifest.source_package_content_hash,
                }
            ],
            packaging_metadata=packaging_metadata,
        ),
        correlation_id=f"test-sc04-workspace-revalidation-version-{uuid.uuid4()}",
    )


def _failed_sc04_runtime(session, *, project, actor_id):
    package_artifact = session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == project.id,
            Artifact.artifact_type == "package_manifest",
        )
    )
    assert package_artifact is not None
    package = session.get(ArtifactVersion, package_artifact.current_version_id)
    assert package is not None
    now = datetime.now(UTC)
    base_approval = ApprovalDecision(
        target_type="artifact_version",
        target_id=package.id,
        target_artifact_version_id=package.id,
        decision="approved",
        decided_by_user_id=actor_id,
        decided_at=now,
        metadata_={
            "approval_scope": "MR1_REAL_PRODUCTION_EXECUTION",
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
        },
        decision_basis={},
        evidence_basis={},
        policy_basis={},
        approved_package_hash=package.content_hash,
    )
    session.add(base_approval)
    session.flush()

    attempts = []
    first_content = {
        "schema_version": "mr1.provider-attempt-ledger.v1",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "operation_key": "pexels:SC-04",
        "provider": "pexels_api",
        "operation": "supporting_asset_acquisition",
        "scene_id": "SC-04",
        "provider_attempt_ordinal": 1,
        "attempt_cap": 1,
        "attempt_count": 1,
        "network_submit_started": True,
        "search_submit_count": 1,
        "download_submit_count": 0,
        "pre_submit_failures": 0,
        "state": "CONSUMED_FAILED",
        "submit_state": "FAILED_CONSUMED",
        "request_hash": "request-hash-1",
        "failure": "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE",
        "automatic_retry_allowed": False,
        "provider_substitution_allowed": False,
        "approval_id": str(base_approval.id),
    }
    first = _create_artifact(
        session,
        project_id=project.id,
        artifact_type="mr1_provider_attempt_ledger",
        actor_id=actor_id,
        content=first_content,
    )
    attempts.append(first)

    authorization_scope = {
        "schema_version": "mr1.provider-attempt-continuation-authority.v1",
        "decision": "approved",
        "decision_source": "OPERATOR",
        "operator_decision_text": "Approve exactly one bounded SC-04 continuation.",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "project_id": str(project.id),
        "operation_key": "pexels:SC-04",
        "scene_id": "SC-04",
        "provider": "pexels_api",
        "route": "PEXELS_VIDEO",
        "base_approval_id": str(base_approval.id),
        "base_approval_content_hash": "base-approval-content-hash",
        "package_artifact_version_id": str(package.id),
        "package_content_hash": package.content_hash,
        "prior_attempt_artifact_id": str(first.artifact_id),
        "prior_attempt_artifact_version_id": str(first.id),
        "prior_attempt_content_hash": first.content_hash,
        "prior_request_hash": "request-hash-1",
        "prior_failure": "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE",
        "prior_attempt_count": 1,
        "additional_attempts": 1,
        "maximum_total_attempts": 2,
        "request_invariants_hash": content_hash({"fixture": "exact"}),
        "semantic_fit_threshold": 0.78,
        "canonical_timeline_hash": "timeline-hash",
        "automatic_retry_allowed": False,
        "provider_substitution_allowed": False,
        "automatic_pexels_to_ai_fallback": False,
        "incremental_cost_cap_usd": 0.0,
        "youtube_upload_authorized": False,
        "publish_execution_authorized": False,
    }
    authorization_hash = content_hash(authorization_scope)
    continuation_approval = ApprovalDecision(
        target_type="artifact_version",
        target_id=package.id,
        target_artifact_version_id=package.id,
        decision="approved",
        decided_by_user_id=actor_id,
        decided_at=now,
        metadata_={
            "approval_scope": "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION",
            "authorization_content_hash": authorization_hash,
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
        },
        decision_basis=deepcopy(authorization_scope),
        evidence_basis={
            "prior_attempt_artifact_version_id": str(first.id),
            "prior_attempt_content_hash": first.content_hash,
        },
        policy_basis={"no_fallback": True},
        approved_package_hash=package.content_hash,
    )
    session.add(continuation_approval)
    session.flush()
    continuation_receipt = {
        **authorization_scope,
        "approval_decision_id": str(continuation_approval.id),
        "authorization_content_hash": authorization_hash,
        "decided_by_user_id": str(actor_id),
        "decided_at": now.isoformat(),
    }
    continuation_receipt["receipt_content_hash"] = content_hash(continuation_receipt)
    second_content = {
        "schema_version": "mr1.provider-attempt-ledger.v1",
        "run_id": "11111111-1111-4111-8111-111111111111",
        "operation_key": "pexels:SC-04:supplement:02",
        "provider": "pexels_api",
        "operation": "supporting_asset_acquisition",
        "scene_id": "SC-04",
        "provider_attempt_ordinal": 2,
        "attempt_cap": 1,
        "attempt_count": 1,
        "network_submit_started": True,
        "search_submit_count": 1,
        "download_submit_count": 0,
        "pre_submit_failures": 0,
        "state": "CONSUMED_FAILED",
        "submit_state": "FAILED_CONSUMED",
        "request_hash": "request-hash-2",
        "failure": "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE",
        "automatic_retry_allowed": False,
        "provider_substitution_allowed": False,
        "approval_id": str(base_approval.id),
        "provider_attempt_continuation": continuation_receipt,
        "prior_consumed_attempt": {
            "operation_key": "pexels:SC-04",
            "artifact_id": str(first.artifact_id),
            "artifact_version_id": str(first.id),
            "content_hash": first.content_hash,
            "state": "CONSUMED_FAILED",
            "request_hash": "request-hash-1",
            "failure": "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE",
        },
    }
    second = _create_artifact(
        session,
        project_id=project.id,
        artifact_type="mr1_provider_attempt_ledger",
        actor_id=actor_id,
        content=second_content,
    )
    attempts.append(second)
    attempt_rows = {
        "pexels:SC-04": {
            **deepcopy(first_content),
            "artifact_version_id": str(first.id),
        },
        "pexels:SC-04:supplement:02": {
            **deepcopy(second_content),
            "artifact_version_id": str(second.id),
        },
    }
    run = _create_artifact(
        session,
        project_id=project.id,
        artifact_type="mr1_execution_run",
        actor_id=actor_id,
        content={
            "schema_version": "mr1.execution-run.v1",
            "run_id": "11111111-1111-4111-8111-111111111111",
            "project_id": str(project.id),
            "approval_id": str(base_approval.id),
            "approval_content_hash": "base-approval-content-hash",
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "current_state": "BLOCKED_REQUIRES_NEW_MR1_APPROVAL",
            "blocker": "pexels:SC-04:supplement:02:POST_SUBMIT_FAILURE",
            "attempts": attempt_rows,
            "attempt_artifact_ids": {
                "pexels:SC-04": str(first.artifact_id),
                "pexels:SC-04:supplement:02": str(second.artifact_id),
            },
            "provider_attempt_continuation_approvals": [deepcopy(continuation_receipt)],
            "temporal_authority": {
                "timeline_hash": "timeline-hash",
                "scene_windows": [
                    {
                        "scene_id": "SC-04",
                        "start_ms": 146020,
                        "end_ms": 197120,
                        "duration_ms": 51100,
                    }
                ],
                "supporting_visual_subwindows": [
                    {
                        "scene_id": "SC-04",
                        "stock_context": {"duration_ms": 8000},
                        "native_explanation": {"duration_ms": 43100},
                        "native_mechanism": ("BRIEF_CONTEXT_THEN_BASELINE_CHECKLIST"),
                    }
                ],
            },
        },
    )
    return attempts, run


def _scope(session, tmp_path):
    _historical, pending, command, _revision_service = _pending_revision(
        session, tmp_path
    )
    PKG1MarketRevisionCloseoutService(session).closeout(command)
    project = session.get(VideoProject, uuid.UUID(pending["video_project_id"]))
    assert project is not None and project.status == "approved"
    overlay, closeout, effective_hash = _geo_authority(
        session, project=project, actor_id=command.decided_by_user_id
    )
    attempts, run = _failed_sc04_runtime(
        session, project=project, actor_id=command.decided_by_user_id
    )
    return (
        project,
        command.decided_by_user_id,
        overlay,
        closeout,
        effective_hash,
        attempts,
        run,
    )


def _exact_source_bindings(session, source):
    package_artifact = session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == source.id,
            Artifact.artifact_type == "package_manifest",
        )
    )
    receipt_artifact = session.scalar(
        select(Artifact).where(
            Artifact.video_project_id == source.id,
            Artifact.artifact_type == "pkg1_market_revision_human_review_receipt",
        )
    )
    assert package_artifact is not None and package_artifact.current_version_id
    assert receipt_artifact is not None and receipt_artifact.current_version_id
    package = session.get(ArtifactVersion, package_artifact.current_version_id)
    receipt = session.get(ArtifactVersion, receipt_artifact.current_version_id)
    assert package is not None and receipt is not None
    approvals = [
        item
        for item in session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id == package.id
            )
        ).all()
        if item.decision == "approved"
        and (item.metadata_ or {}).get("approval_scope")
        == "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
    ]
    assert len(approvals) == 1
    return {
        "source_project_id": source.id,
        "source_package_artifact_version_id": package.id,
        "source_package_content_hash": package.content_hash,
        "source_approval_decision_id": approvals[0].id,
        "source_human_receipt_artifact_version_id": receipt.id,
        "source_human_receipt_content_hash": receipt.content_hash,
    }


def _source_fingerprint(session, project_id):
    artifacts = list(
        session.scalars(
            select(Artifact).where(Artifact.video_project_id == project_id)
        ).all()
    )
    versions = list(
        session.scalars(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id.in_([item.id for item in artifacts])
            )
        ).all()
    )
    return {
        "project_status": session.get(VideoProject, project_id).status,
        "artifacts": sorted(
            (
                str(item.id),
                item.artifact_type,
                str(item.current_version_id),
                item.status,
            )
            for item in artifacts
        ),
        "versions": sorted(
            (
                str(item.id),
                str(item.artifact_id),
                item.version_number,
                item.content_hash,
                deepcopy(item.content),
            )
            for item in versions
        ),
    }


def test_sc04_revision_is_immutable_exact_and_pending_human_review(
    db_session, tmp_path
) -> None:
    (
        source,
        actor_id,
        overlay,
        closeout,
        effective_hash,
        attempts,
        _run,
    ) = _scope(db_session, tmp_path)
    source_before = _source_fingerprint(db_session, source.id)
    source_bindings = _exact_source_bindings(db_session, source)
    approval_count = db_session.scalar(
        select(func.count()).select_from(ApprovalDecision)
    )

    result = PKG1SC04RevisionService(db_session).build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=closeout.id,
        geo_closeout_content_hash=closeout.content_hash,
        **source_bindings,
    )

    assert result["project_type"] == PROJECT_TYPE
    assert result["human_review_state"] == "PENDING"
    assert result["final_state"] == "WAITING_HUMAN_REVIEW"
    assert result["provider_calls"] == result["render_calls"] == 0
    package = result["package"]
    assert package["root_cause"] == "INSUFFICIENT_SCENE_SPEC"
    assert package["repaired_route"] == "NATIVE_MOTION_GRAPHIC"
    assert package["provider_execution"] == "DISABLED"
    assert package["third_sc04_pexels_attempt_allowed"] is False
    assert package["PROCEED_TO_MR1"] is False
    assert package["MR1_EXECUTION"] == "BLOCKED_PENDING_PACKAGE_APPROVAL"
    assert package["effective_monetization_policy"]["artifact_version_id"] == str(
        overlay.id
    )
    assert package["geo_market_delivery_closeout_evidence"][
        "artifact_version_id"
    ] == str(closeout.id)
    assert package["effective_market_policy_hash"] == effective_hash
    assert package["source_human_authority"]["source_project_id"] == str(source.id)
    assert package["source_human_authority"]["approved_package"][
        "artifact_version_id"
    ] == str(source_bindings["source_package_artifact_version_id"])
    assert package["source_human_authority"]["approval"]["approval_decision_id"] == str(
        source_bindings["source_approval_decision_id"]
    )
    assert package["source_human_authority"]["human_review_receipt"][
        "artifact_version_id"
    ] == str(source_bindings["source_human_receipt_artifact_version_id"])
    assert package["no_execution_proof"]["all_deltas_zero"] is True
    assert len(package["attempt_evidence"]["attempts"]) == 2
    assert {
        item["artifact_version_id"] for item in package["attempt_evidence"]["attempts"]
    } == {str(item.id) for item in attempts}
    assert package["attempt_evidence"]["old_query_family"]["queries"] == [
        "use brief supporting team-work workplace b roll",
        "use brief supporting team-work close up action",
        "use brief supporting team-work clean composition",
    ]
    assert package["attempt_evidence"]["candidate_scores"] == {
        "state": "UNAVAILABLE_NOT_PERSISTED",
        "values": [],
        "fabricated": False,
        "reason": (
            "The Pexels failure path persisted request hashes and counters but "
            "did not serialize candidate rankings or semantic scores."
        ),
    }
    assert (
        db_session.scalar(select(func.count()).select_from(ApprovalDecision))
        == approval_count
    )
    assert _source_fingerprint(db_session, source.id) == source_before

    project_id = uuid.UUID(result["video_project_id"])
    review = db_session.scalar(
        select(ReviewTask).where(ReviewTask.video_project_id == project_id)
    )
    assert review is not None
    assert review.status == "open"
    assert review.review_type == "final_human"
    assert review.target_artifact_version_id == uuid.UUID(
        result["package_artifact_version_id"]
    )
    assert {
        DIFF_ARTIFACT_TYPE,
        REVIEW_PACKET_ARTIFACT_TYPE,
    } <= set(result["artifacts"])


def test_sc04_revision_changes_only_sc04_and_emits_all_named_gates(
    db_session, tmp_path
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    result = PKG1SC04RevisionService(db_session).build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=closeout.id,
        geo_closeout_content_hash=closeout.content_hash,
    )
    artifacts = result["artifacts"]
    diff = artifacts[DIFF_ARTIFACT_TYPE]["content"]
    assert diff["changed_scene_ids"] == ["SC-04"]
    assert diff["unchanged_scenes_exact"] is True
    assert diff["script_changed"] is False
    assert diff["spoken_text_changed"] is False
    assert diff["attempt_ledgers_changed"] is False

    scenes = artifacts["visual_plan"]["content"]["scenes"]
    sc04 = next(item for item in scenes if item["scene_id"] == "SC-04")
    assert sc04["preferred_source_route"] == "NATIVE_MOTION_GRAPHIC"
    assert sc04["provider"] == "native"
    assert sc04["source_role"] == "NATIVE_VISUAL"
    assert sc04["native_motion_blueprint"]["stock_layer_allowed"] is False
    decision = next(
        item
        for item in artifacts["visual_source_decision_set"]["content"]["decisions"]
        if item["scene_id"] == "SC-04"
    )
    assert decision["preferred_source_route"] == "NATIVE_MOTION_GRAPHIC"
    assert decision["provider_execution_required"] is False
    assert decision["planned_requests"] == 0
    provider = artifacts["provider_execution_plan"]["content"]
    route = next(
        item for item in provider["scene_routes"] if item["scene_id"] == "SC-04"
    )
    assert route == {
        **route,
        "route": "NATIVE_MOTION_GRAPHIC",
        "provider": "native",
        "attempt_cap": 0,
    }
    assert (
        next(item for item in provider["stages"] if item["provider"] == "pexels_api")[
            "planned_requests"
        ]
        == 2
    )
    expected_drive_phases = [
        {
            "phase": "CANONICAL_REVIEW_ARCHIVE",
            "operation_key": "google_drive:archive",
            "boundary": "PRE_HUMAN_PASS",
            "max_mutations": 1,
            "cost_usd": 0.0,
        },
        {
            "phase": "FINALIZATION_SUPPLEMENT",
            "operation_key": "google_drive:finalization-supplement",
            "boundary": "POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF",
            "max_mutations": 1,
            "cost_usd": 0.0,
        },
    ]
    drive_stage = next(
        item for item in provider["stages"] if item["provider"] == "google_drive"
    )
    assert drive_stage["planned_requests"] == 2
    assert drive_stage["idempotency_phases"] == expected_drive_phases
    drive_cost = next(
        item
        for item in artifacts["cost_estimate_snapshot"]["content"]["line_items"]
        if item["provider"] == "google_drive"
    )
    assert drive_cost["planned_requests"] == 2
    assert drive_cost["estimated_incremental_cost_usd"] == 0.0
    assert drive_cost["idempotency_phases"] == expected_drive_phases
    drive_rights = artifacts["rights_disclosure_completeness_report"]["content"][
        "drive_archive_scope"
    ]
    assert drive_rights["idempotency_phases"] == expected_drive_phases
    assert drive_rights["canonical_archive_mutated_by_supplement"] is False
    packet = artifacts[REVIEW_PACKET_ARTIFACT_TYPE]["content"]
    assert packet["provider_attempt_scope"]["google_drive_mutation_scope"] == {
        "before_planned_requests": 2,
        "after_planned_requests": 2,
        "idempotency_phases": expected_drive_phases,
        "canonical_archive_mutated_by_supplement": False,
        "execution_requires_fresh_mr1_approval": True,
    }
    assert packet["cost_difference"]["google_drive"] == {
        "planned_requests": 2,
        "idempotency_phases": expected_drive_phases,
        "incremental_cost_usd": 0.0,
    }

    gates = artifacts["gate_results"]["content"]
    for key in (
        "scene_spec_completeness",
        "pexels_eligibility",
        "evidence_truth",
        "diagram_suitability",
        "visual_niche_alignment",
        "visual_market_alignment",
        "semantic_match",
        "visual_continuity",
        "repetitive_production_risk",
        "rights_disclosure_completeness",
        "provider_cost_estimate",
    ):
        assert gates[key]["verdict"] == "PASS"
    assert gates["ai_image_eligibility"]["verdict"] == "NOT_APPLICABLE"
    assert gates["threshold_integrity"]["thresholds_modified"] is False
    assert gates["threshold_integrity"]["semantic_fit_threshold_lowered"] is False

    packet = artifacts[REVIEW_PACKET_ARTIFACT_TYPE]["content"]
    assert packet["candidate_score_disclosure"]["fabricated"] is False
    assert packet["old_scene_authority"]["preferred_source_route"] == ("PEXELS_VIDEO")
    assert packet["old_scene_authority"]["attempt_cap"] == 1
    assert packet["new_scene_meaning"]["semantic_intent"]
    assert packet["native_motion_blueprint"]["phases"] == [
        {
            "phase": "OBSERVE_WORKFLOW",
            "items": [
                "REQUEST_BEGINS",
                "FIELDS_COPIED",
                "MISSING_INFORMATION",
                "GAP_OWNER",
            ],
        },
        {
            "phase": "MEASURE_BASELINE",
            "items": ["COMPLETED_HANDOFFS", "REWORK", "JUDGMENT_STEPS"],
        },
        {
            "phase": "SPLIT_WORK",
            "branches": ["MOVE_INFORMATION", "MAKE_DECISION"],
        },
        {
            "phase": "PRESERVE_RESPONSIBILITY",
            "items": ["HUMAN_EXCEPTION_PATH", "VISIBLE_OWNER"],
        },
    ]
    assert packet["strict_scene_spec"]["scene_class"] == "mechanism"
    assert packet["new_decision"]["preferred_source_route"] == ("NATIVE_MOTION_GRAPHIC")
    assert packet["guardrails"]["automatic_approval"] == "PROHIBITED"
    bindings = result["package"]["exact_bindings"]
    for key in (
        "channel_profile_version",
        "compiled_channel_policy_snapshot",
        "target_market_profile",
        "destination_binding",
        "effective_ads_only_monetization_policy",
        "geo_market_delivery_closeout_evidence",
        "visual_source_decision_set",
        "provider_execution_plan",
        "cost_estimate_snapshot",
        "rights_disclosure_completeness_report",
    ):
        assert bindings[key]


def test_sc04_revision_rejects_cross_artifact_old_route_drift(
    db_session, tmp_path, monkeypatch
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    service = PKG1SC04RevisionService(db_session)
    original_resolver = service._resolve_source_package_artifacts

    def _drifted_artifacts(*args, **kwargs):
        artifacts = original_resolver(*args, **kwargs)
        provider_plan = artifacts["provider_execution_plan"]
        drifted_content = deepcopy(provider_plan.content)
        scene = next(
            item
            for item in drifted_content["scene_routes"]
            if item["scene_id"] == "SC-04"
        )
        scene["provider"] = "native"
        artifacts = dict(artifacts)
        artifacts["provider_execution_plan"] = _detached_version_with_content(
            provider_plan,
            drifted_content,
        )
        return artifacts

    monkeypatch.setattr(
        service,
        "_resolve_source_package_artifacts",
        _drifted_artifacts,
    )

    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC04_SOURCE_ROUTE_NOT_PEXELS_VIDEO",
    ):
        service.build_revision(
            channel_id=source.channel_workspace_id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=overlay.id,
            ads_only_overlay_content_hash=overlay.content_hash,
            geo_closeout_artifact_version_id=closeout.id,
            geo_closeout_content_hash=closeout.content_hash,
        )


def test_sc04_revision_rejects_script_meaning_mismatch(
    db_session, tmp_path, monkeypatch
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    service = PKG1SC04RevisionService(db_session)
    original_resolver = service._resolve_source_package_artifacts

    def _script_mismatch(*args, **kwargs):
        artifacts = original_resolver(*args, **kwargs)
        script = artifacts["script"]
        mismatched_content = deepcopy(script.content)
        segment = next(
            item
            for item in mismatched_content["segments"]
            if item["segment_id"] == "S04"
        )
        segment["text"] = segment["text"].replace(
            "without hiding responsibility",
            "while simplifying the ending",
        )
        artifacts = dict(artifacts)
        artifacts["script"] = _detached_version_with_content(
            script,
            mismatched_content,
        )
        return artifacts

    monkeypatch.setattr(
        service,
        "_resolve_source_package_artifacts",
        _script_mismatch,
    )
    with pytest.raises(
        ValidationFailureError,
        match=("SC04_SCRIPT_SEMANTIC_DERIVATION_INCOMPLETE:human_responsibility"),
    ):
        service.build_revision(
            channel_id=source.channel_workspace_id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=overlay.id,
            ads_only_overlay_content_hash=overlay.content_hash,
            geo_closeout_artifact_version_id=closeout.id,
            geo_closeout_content_hash=closeout.content_hash,
            **_exact_source_bindings(db_session, source),
        )


def test_sc04_read_revision_rejects_component_provenance_tamper(
    db_session, tmp_path
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    service = PKG1SC04RevisionService(db_session)
    result = service.build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=closeout.id,
        geo_closeout_content_hash=closeout.content_hash,
        **_exact_source_bindings(db_session, source),
    )
    visual_plan = db_session.get(
        ArtifactVersion,
        uuid.UUID(result["artifacts"]["visual_plan"]["artifact_version_id"]),
    )
    assert visual_plan is not None
    visual_plan.context_refs = [
        {"type": "pkg1_sc04_revision", "content_hash": "0" * 64}
    ]

    with pytest.raises(
        ValidationFailureError,
        match=("PKG1_SC04_REVISION_ARTIFACT_PROVENANCE_INVALID:visual_plan"),
    ):
        service.read_revision(uuid.UUID(result["video_project_id"]))


def test_sc04_revision_is_idempotent_and_final_reviewer_is_exact_package_only(
    db_session, tmp_path, monkeypatch
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    service = PKG1SC04RevisionService(db_session)
    kwargs = {
        "channel_id": source.channel_workspace_id,
        "created_by_user_id": actor_id,
        "ads_only_overlay_artifact_version_id": overlay.id,
        "ads_only_overlay_content_hash": overlay.content_hash,
        "geo_closeout_artifact_version_id": closeout.id,
        "geo_closeout_content_hash": closeout.content_hash,
        **_exact_source_bindings(db_session, source),
    }
    first = service.build_revision(**kwargs)
    project_id = uuid.UUID(first["video_project_id"])
    artifact_count = db_session.scalar(
        select(func.count())
        .select_from(Artifact)
        .where(Artifact.video_project_id == project_id)
    )
    later_global_counts = {
        **first["package"]["no_execution_proof"]["after_counts"],
        "unrelated_later_provider_activity": 1,
    }
    monkeypatch.setattr(
        service.source_service,
        "_no_execution_counts",
        lambda: later_global_counts,
    )
    second = service.build_revision(**kwargs)
    assert second["video_project_id"] == first["video_project_id"]
    assert second["package_artifact_version_id"] == first["package_artifact_version_id"]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.video_project_id == project_id)
        )
        == artifact_count
    )

    project = db_session.get(VideoProject, project_id)
    package = db_session.get(
        ArtifactVersion, uuid.UUID(first["package_artifact_version_id"])
    )
    review = db_session.get(ReviewTask, uuid.UUID(first["human_review_task_ids"][0]))
    assert project is not None and package is not None and review is not None
    approval_data = ApprovalDecisionCreate(
        target_type="artifact_version",
        target_id=package.id,
        target_artifact_version_id=package.id,
        decision="approved",
        decided_by_user_id=actor_id,
        metadata={
            "approval_ref": (
                f"operator-approval://pkg1-sc04-revision/{first['revision_id']}/"
                f"{first['revision_hash']}/{package.id}/{package.content_hash}"
            )
        },
    )
    authority = ApprovalService(db_session)._validate_assigned_final_review_authority(
        data=approval_data,
        project=project,
        version=package,
        review_task_id=review.id,
    )
    assert authority.id == review.id
    component = db_session.get(
        ArtifactVersion,
        uuid.UUID(first["artifacts"]["visual_plan"]["artifact_version_id"]),
    )
    assert component is not None
    component_data = approval_data.model_copy(
        update={
            "target_id": component.id,
            "target_artifact_version_id": component.id,
        }
    )
    with pytest.raises(
        ValidationFailureError,
        match="only the exact package manifest",
    ):
        ApprovalService(db_session)._validate_assigned_final_review_authority(
            data=component_data,
            project=project,
            version=component,
            review_task_id=review.id,
        )


def test_sc04_revision_rejects_tampered_ads_only_overlay(db_session, tmp_path) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    tampered_content = deepcopy(overlay.content)
    tampered_content["policy"]["affiliate_enabled"] = True
    tampered = _create_artifact(
        db_session,
        project_id=source.id,
        artifact_type="effective_ads_only_monetization_policy",
        actor_id=actor_id,
        content=tampered_content,
    )
    with pytest.raises(
        ValidationFailureError,
        match="EXACT_IMMUTABLE_ADS_ONLY_POLICY_OVERLAY_REQUIRED",
    ):
        PKG1SC04RevisionService(db_session).build_revision(
            channel_id=source.channel_workspace_id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=tampered.id,
            ads_only_overlay_content_hash=tampered.content_hash,
            geo_closeout_artifact_version_id=closeout.id,
            geo_closeout_content_hash=closeout.content_hash,
        )


def test_sc04_revision_requires_exact_geo_refs_and_hashes(db_session, tmp_path) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    with pytest.raises(
        ValidationFailureError,
        match="PKG1_SC04_EXACT_GEO_BINDINGS_REQUIRED",
    ):
        PKG1SC04RevisionService(db_session).build_revision(
            channel_id=source.channel_workspace_id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=overlay.id,
            ads_only_overlay_content_hash=overlay.content_hash,
            geo_closeout_artifact_version_id=closeout.id,
            geo_closeout_content_hash=None,
        )


def test_sc04_revision_revalidates_geo_verification_receipt(
    db_session, tmp_path
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    closeout_content = closeout.content or {}
    receipt = db_session.get(
        ArtifactVersion,
        uuid.UUID(closeout_content["verification_receipt_artifact_version_id"]),
    )
    assert receipt is not None
    receipt_artifact = db_session.get(Artifact, receipt.artifact_id)
    assert receipt_artifact is not None
    receipt_artifact.status = "superseded"

    with pytest.raises(
        ValidationFailureError,
        match="GEO_CLOSEOUT_VERIFICATION_RECEIPT_INVALID",
    ):
        PKG1SC04RevisionService(db_session).build_revision(
            channel_id=source.channel_workspace_id,
            created_by_user_id=actor_id,
            ads_only_overlay_artifact_version_id=overlay.id,
            ads_only_overlay_content_hash=overlay.content_hash,
            geo_closeout_artifact_version_id=closeout.id,
            geo_closeout_content_hash=closeout.content_hash,
        )


def test_sc04_revision_uses_exact_current_workspace_machine_revalidation_only(
    db_session, tmp_path, monkeypatch
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    source_bindings = _exact_source_bindings(db_session, source)
    closeout_before = deepcopy(closeout.content)
    closeout_hash_before = closeout.content_hash
    closeout_artifact = db_session.get(Artifact, closeout.artifact_id)
    assert closeout_artifact is not None
    closeout_current_before = closeout_artifact.current_version_id
    original_receipt = db_session.get(
        ArtifactVersion,
        uuid.UUID(
            closeout_before["verification_receipt_artifact_version_id"]
        ),
    )
    assert original_receipt is not None
    original_receipt_before = deepcopy(original_receipt.content)
    original_receipt_hash_before = original_receipt.content_hash
    original_receipt_artifact = db_session.get(
        Artifact, original_receipt.artifact_id
    )
    assert original_receipt_artifact is not None
    original_receipt_current_before = original_receipt_artifact.current_version_id
    source_package = db_session.get(
        ArtifactVersion, source_bindings["source_package_artifact_version_id"]
    )
    source_approval = db_session.get(
        ApprovalDecision, source_bindings["source_approval_decision_id"]
    )
    assert source_package is not None and source_approval is not None
    source_package_before = deepcopy(source_package.content)
    source_package_hash_before = source_package.content_hash
    source_approval_before = deepcopy(source_approval.metadata_)

    current_hash = "e" * 64
    monkeypatch.setattr(
        "app.services.pkg1_sc04_revision.geo_delivery_workspace_hash",
        lambda _root: current_hash,
    )
    revalidation = _persist_workspace_revalidation(
        db_session,
        closeout=closeout,
        actor_id=actor_id,
        workspace_hash=current_hash,
    )
    service = PKG1SC04RevisionService(db_session)
    resolved = service._resolve_ads_only_overlay(
        channel_id=source.channel_workspace_id,
        snapshot_id=source.policy_snapshot_id,
        requested_version_id=overlay.id,
        requested_hash=overlay.content_hash,
        requested_closeout_version_id=closeout.id,
        requested_closeout_hash=closeout.content_hash,
    )
    assert resolved["workspace_freshness"] == {
        "mode": "CURRENT_WORKSPACE_MACHINE_REVALIDATION",
        "artifact_version_id": str(revalidation.id),
        "content_hash": revalidation.content_hash,
        "manifest_content_hash": (
            (revalidation.packaging_metadata or {})["manifest_content_hash"]
        ),
        "workspace_hash": current_hash,
        "producer": "VCOS_MACHINE_VERIFICATION_RUNNER",
    }

    result = service.build_revision(
        channel_id=source.channel_workspace_id,
        created_by_user_id=actor_id,
        ads_only_overlay_artifact_version_id=overlay.id,
        ads_only_overlay_content_hash=overlay.content_hash,
        geo_closeout_artifact_version_id=closeout.id,
        geo_closeout_content_hash=closeout.content_hash,
        **source_bindings,
    )
    assert result["final_state"] == "WAITING_HUMAN_REVIEW"

    db_session.refresh(closeout)
    db_session.refresh(closeout_artifact)
    db_session.refresh(original_receipt)
    db_session.refresh(original_receipt_artifact)
    db_session.refresh(source_package)
    db_session.refresh(source_approval)
    assert closeout.content == closeout_before
    assert closeout.content_hash == closeout_hash_before
    assert closeout_artifact.current_version_id == closeout_current_before
    assert original_receipt.content == original_receipt_before
    assert original_receipt.content_hash == original_receipt_hash_before
    assert (
        original_receipt_artifact.current_version_id
        == original_receipt_current_before
    )
    assert closeout.content["verification_receipt_artifact_version_id"] == str(
        original_receipt.id
    )
    assert closeout.content["verification_receipt_content_hash"] == (
        original_receipt.content_hash
    )
    assert source_package.content == source_package_before
    assert source_package.content_hash == source_package_hash_before
    assert source_approval.metadata_ == source_approval_before


@pytest.mark.parametrize(
    ("case", "expected_error"),
    [
        ("absent", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_REQUIRED"),
        ("nonpassing", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_NOT_PASSING"),
        ("mismatched", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_SCOPE_INVALID"),
        ("ambiguous", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_AMBIGUOUS"),
        ("tampered_content", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_INVALID"),
        ("tampered_producer", "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_INVALID"),
    ],
)
def test_sc04_revision_current_workspace_revalidation_fails_closed(
    db_session,
    tmp_path,
    monkeypatch,
    case,
    expected_error,
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    current_hash = "f" * 64
    monkeypatch.setattr(
        "app.services.pkg1_sc04_revision.geo_delivery_workspace_hash",
        lambda _root: current_hash,
    )
    if case != "absent":
        updates = (
            {"channel_workspace_id": str(uuid.uuid4())}
            if case == "mismatched"
            else None
        )
        _persist_workspace_revalidation(
            db_session,
            closeout=closeout,
            actor_id=actor_id,
            workspace_hash=current_hash,
            manifest_updates=updates,
            passing=case != "nonpassing",
            tamper=(
                "content"
                if case == "tampered_content"
                else "producer"
                if case == "tampered_producer"
                else None
            ),
        )
        if case == "ambiguous":
            _persist_workspace_revalidation(
                db_session,
                closeout=closeout,
                actor_id=actor_id,
                workspace_hash=current_hash,
                manifest_updates={"generated_at": "2026-07-21T12:00:01Z"},
            )
        db_session.flush()

    with pytest.raises(ValidationFailureError, match=expected_error):
        PKG1SC04RevisionService(db_session)._resolve_ads_only_overlay(
            channel_id=source.channel_workspace_id,
            snapshot_id=source.policy_snapshot_id,
            requested_version_id=overlay.id,
            requested_hash=overlay.content_hash,
            requested_closeout_version_id=closeout.id,
            requested_closeout_hash=closeout.content_hash,
        )


def test_sc04_workspace_revalidation_ignores_failed_run_and_deduplicates_exact_pass(
    db_session, tmp_path, monkeypatch
) -> None:
    source, actor_id, overlay, closeout, _, _, _ = _scope(db_session, tmp_path)
    current_hash = "9" * 64
    monkeypatch.setattr(
        "app.services.pkg1_sc04_revision.geo_delivery_workspace_hash",
        lambda _root: current_hash,
    )
    _persist_workspace_revalidation(
        db_session,
        closeout=closeout,
        actor_id=actor_id,
        workspace_hash=current_hash,
        passing=False,
    )
    first_pass = _persist_workspace_revalidation(
        db_session,
        closeout=closeout,
        actor_id=actor_id,
        workspace_hash=current_hash,
    )
    duplicate_pass = _persist_workspace_revalidation(
        db_session,
        closeout=closeout,
        actor_id=actor_id,
        workspace_hash=current_hash,
    )

    resolved = PKG1SC04RevisionService(db_session)._resolve_ads_only_overlay(
        channel_id=source.channel_workspace_id,
        snapshot_id=source.policy_snapshot_id,
        requested_version_id=overlay.id,
        requested_hash=overlay.content_hash,
        requested_closeout_version_id=closeout.id,
        requested_closeout_hash=closeout.content_hash,
    )
    expected = min((first_pass, duplicate_pass), key=lambda item: str(item.id))
    assert resolved["workspace_freshness"][
        "mode"
    ] == "CURRENT_WORKSPACE_MACHINE_REVALIDATION"
    assert resolved["workspace_freshness"]["artifact_version_id"] == str(
        expected.id
    )
    assert first_pass.content_hash == duplicate_pass.content_hash
