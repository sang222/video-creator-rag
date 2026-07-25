from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.contracts.channel_policy import ChannelScopedPolicy
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    ReviewTask,
    VideoProject,
)
from app.services.channel_profile import ChannelProfileService
from app.services.config_registry import content_hash
from app.services.nich1 import nich1_stable_hash
from app.services.pkg1 import PKG1PackageService
from app.services.pkg1_market_revision import (
    DRIVE_IDEMPOTENCY_PHASES,
    PROJECT_TYPE,
    PKG1MarketRevisionService,
    REUSED_ARTIFACT_TYPES,
)
from tests.test_ch1_market_profile_v3 import (
    V2_APPROVAL,
    V3_APPROVAL,
    _market_bindings,
)
from tests.test_pkg1_first_production_package import _external_counts, _scope


def _reports(tmp_path: Path, *, channel, profile, snapshot) -> dict[str, Path]:
    payloads = {
        "lpro1": {"result": "PASS"},
        "geo1": {"verdicts": {"GEO1_FINAL": "PASS"}},
        "geo2": {"verdicts": {"GEO2_FINAL": "PASS"}},
        "ch1": {
            "verdicts": {"CH1_MARKET_V3_FINAL": "PASS"},
            "production_activation": {
                "channel_id": str(channel.id),
                "profile_v3_id": str(profile.id),
                "profile_v3_input_hash": profile.profile_input_hash,
                "snapshot_v3_id": str(snapshot.id),
                "snapshot_v3_hash": snapshot.content_hash,
            },
            "mr1_execution": "ON_HOLD",
            "proceed_to_mr1": False,
            "proceed_to_pkg1_revision": True,
        },
    }
    result = {}
    for key, payload in payloads.items():
        path = tmp_path / f"{key}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        result[key] = path
    return result


def _category(session, *, company, channel, snapshot) -> ContentCategory:
    contract = snapshot.compiled_payload["channel_contract_json"]
    content_pillar = contract["editorial_strategy"]["content_pillars"][0]
    category_payload = {
        "category_key": "workflow-automation",
        "name": "Workflow Automation Explainers",
        "sub_niche": "small team AI workflow automation",
        "audience_segment": "small team operators and founders",
        "content_pillar": content_pillar,
    }
    category = ContentCategory(
        company_id=company.id,
        channel_workspace_id=channel.id,
        **category_payload,
        default_format_policy_json={"format": "long-form documentary/explainer"},
        default_visual_style_json={"niche_visual_source_profile": "STOCK_ASSISTED"},
        default_voice_style_json={"locale": "en-US"},
        default_thumbnail_style_json={"locale": "en-US"},
        visual_mode="STOCK_ASSISTED",
        character_policy_mode="NO_CHARACTER",
        status="ACTIVE",
        source_refs_json=[{"ref": "operator://pkg1-market-revision/category"}],
        content_hash=nich1_stable_hash(category_payload),
    )
    session.add(category)
    session.flush()
    return category


def _revision_scope(session, tmp_path: Path):
    company, operator, channel, _profile_v1, snapshot_v1, ch1_v1_report = _scope(
        session, tmp_path
    )
    pkg1 = PKG1PackageService(session, ch1_report_path=ch1_v1_report)
    historical = pkg1.build_first_package(
        channel_id=channel.id, created_by_user_id=operator.id
    )
    pkg1.persist_human_approval_and_open_mr1(
        project_id=historical.video_project_id,
        decided_by_user_id=operator.id,
        approval_ref="operator-approval://pkg1/test/final-and-mr1",
    )
    explicit_contract = deepcopy(
        snapshot_v1.compiled_payload["channel_contract_json"]
    )
    explicit_contract["channel_identity"]["brand_promise"] = (
        "Practical, bounded AI workflows for small-team operators."
    )
    explicit_contract["target_audience"]["pain_points"] = [
        "Repeated manual handoffs with unclear ownership."
    ]
    explicit_contract["target_audience"]["desired_outcome"] = (
        "An auditable workflow with a visible human exception path."
    )
    channel.metadata_ = {
        **deepcopy(channel.metadata_ or {}),
        "channel_contract": explicit_contract,
    }
    session.flush()
    profile_service = ChannelProfileService(session)
    profile_service.approve_and_activate_ch1_flex_v2(
        channel_id=channel.id,
        approval_ref=V2_APPROVAL,
        approved_by=operator.id,
    )
    market_profile, market_digest, destination = _market_bindings(channel)
    activated = profile_service.approve_and_activate_ch1_market_v3(
        channel_id=channel.id,
        target_market_profile=market_profile,
        target_market_digest=market_digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
        approved_by=operator.id,
    )
    profile = profile_service.get_profile_version(activated["channel_profile_version_id"])
    snapshot = session.get(
        CompiledChannelPolicySnapshot,
        uuid.UUID(str(activated["compiled_policy_snapshot_id"])),
    )
    _category(session, company=company, channel=channel, snapshot=snapshot)
    reports = _reports(
        tmp_path, channel=channel, profile=profile, snapshot=snapshot
    )
    return company, operator, channel, historical, profile, snapshot, reports


def _historical_state(session, project_id) -> dict:
    project = session.get(VideoProject, project_id)
    artifacts = list(
        session.scalars(
            select(Artifact).where(Artifact.video_project_id == project.id)
        ).all()
    )
    versions = list(
        session.scalars(
            select(ArtifactVersion).where(
                ArtifactVersion.artifact_id.in_([item.id for item in artifacts])
            )
        ).all()
    )
    approvals = list(
        session.scalars(
            select(ApprovalDecision).where(
                ApprovalDecision.target_artifact_version_id.in_(
                    [item.id for item in versions]
                )
            )
        ).all()
    )
    return {
        "project": {
            "id": str(project.id),
            "status": project.status,
            "profile": str(project.channel_profile_version_id),
            "snapshot": str(project.policy_snapshot_id),
        },
        "artifacts": sorted(
            (str(item.id), item.artifact_type, str(item.current_version_id), item.status)
            for item in artifacts
        ),
        "versions": sorted(
            (
                str(item.id),
                str(item.artifact_id),
                item.version_number,
                str(item.parent_version_id),
                item.content_hash,
                deepcopy(item.content),
            )
            for item in versions
        ),
        "approvals": sorted(
            (str(item.id), item.decision, str(item.target_artifact_version_id))
            for item in approvals
        ),
    }


def test_market_revision_supersedes_v1_exactly_and_waits_for_human_review(
    db_session, tmp_path
) -> None:
    (
        _company,
        operator,
        channel,
        historical,
        profile,
        snapshot,
        reports,
    ) = _revision_scope(db_session, tmp_path)
    history_before = _historical_state(db_session, historical.video_project_id)
    external_before = _external_counts(db_session)
    approval_count_before = db_session.scalar(
        select(func.count()).select_from(ApprovalDecision)
    )

    service = PKG1MarketRevisionService(db_session, report_paths=reports)
    assert service.entry_status(channel.id)["status"] == "PASS"
    result = service.build_revision(
        channel_id=channel.id, created_by_user_id=operator.id
    )

    assert result["final_state"] == "WAITING_HUMAN_REVIEW"
    assert result["human_review_state"] == "PENDING"
    assert result["provider_calls"] == result["render_calls"] == 0
    assert result["drive_calls"] == result["youtube_calls"] == 0
    package = result["package"]
    assert package["historical_pkg1_state"] == "HISTORICAL_PASS"
    assert package["historical_pkg1_mutated"] is False
    assert package["supersedes"]["artifact_id"] == historical.package_id
    assert package["supersedes"]["content_hash"]
    assert set(REUSED_ARTIFACT_TYPES) == set(package["reused_artifacts"])
    assert package["old_mr1_approval"]["reuse_allowed"] is False
    assert package["PRODUCTION_PACKAGE_APPROVED"] is False
    assert package["FINAL_MARKET_PACKAGE_PENDING_MEDIA"] is True
    assert package["MARKET_PACKAGE_FROZEN"] is False
    assert package["UPLOAD_READY"] is False
    assert package["PUBLISH_EXECUTION_READY"] is False
    assert package["destination_status"] == "PENDING_PLATFORM_ID"
    assert package["publish_blocker"] == "PENDING_PLATFORM_ID"
    assert package["publish_blocker_reason_code"] == "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
    assert package["MR1_EXECUTION"] == "ON_HOLD"
    assert package["PROCEED_TO_MR1"] is False
    assert package["PROCEED_TO_MR1_REAPPROVAL"] is False

    bindings = package["exact_bindings"]
    active_policy = ChannelScopedPolicy.model_validate(
        snapshot.compiled_payload["channel_scoped_policy"]
    )
    assert bindings["channel_profile_version"]["id"] == str(profile.id)
    assert bindings["channel_profile_version"]["version"] == 3
    assert bindings["compiled_channel_policy_snapshot"]["id"] == str(snapshot.id)
    assert bindings["target_market_profile"]["content_hash"] == (
        active_policy.target_market_profile.content_hash
    )
    assert bindings["target_market_digest"]["content_hash"] == (
        active_policy.target_market_digest.content_hash
    )
    assert bindings["destination_binding"]["content_hash"] == (
        active_policy.destination_binding_policy.destination.content_hash
    )
    assert bindings["destination_binding"]["destination_status"] == "PENDING_PLATFORM_ID"
    assert bindings["content_category"]["pillar"] == (
        snapshot.compiled_payload["channel_contract_json"]["editorial_strategy"][
            "content_pillars"
        ][0]
    )
    assert bindings["content_category"]["series"] == profile.profile_input[
        "series_plan"
    ][0]["key"]
    assert bindings["lpro1_production_orchestrator_version"] == "lpro1.long-production-orchestrator/1.0.0"
    assert bindings["lpro1_production_contract_version"] == "lpro1.long-form-render-package.v1"
    assert bindings["niche_alignment_dossier"]["content_hash"]
    assert bindings["market_alignment_dossier"]["content_hash"]
    assert package["no_execution_proof"]["all_deltas_zero"] is True
    assert set(package["no_execution_proof"]["deltas"].values()) == {0}

    assert _historical_state(db_session, historical.video_project_id) == history_before
    assert _external_counts(db_session) == external_before
    assert db_session.scalar(select(func.count()).select_from(ApprovalDecision)) == approval_count_before
    assert db_session.scalar(
        select(func.count()).select_from(ReviewTask).where(
            ReviewTask.video_project_id == uuid.UUID(result["video_project_id"]),
            ReviewTask.status == "open",
            ReviewTask.target_artifact_version_id
            == uuid.UUID(result["package_artifact_version_id"]),
        )
    ) == 1


def test_market_revision_artifacts_preserve_market_visual_provider_and_cost_policy(
    db_session, tmp_path
) -> None:
    _, operator, channel, _, _, _, reports = _revision_scope(db_session, tmp_path)
    result = PKG1MarketRevisionService(
        db_session, report_paths=reports
    ).build_revision(channel_id=channel.id, created_by_user_id=operator.id)
    artifacts = result["artifacts"]

    gates = artifacts["market_gate_results"]["content"]
    assert gates["strict_order"] == [
        "idea_market_preflight",
        "topic_market_alignment_gate",
        "research_jurisdiction_gate",
        "script_market_alignment_gate",
        "voice_locale_alignment_gate",
        "visual_market_alignment_gate",
        "thumbnail_market_alignment_gate",
        "metadata_market_alignment_gate",
    ]
    assert gates["idea_market_preflight"]["decision"] == "PASS"
    assert gates["idea_market_preflight_criteria_source"]["mode"] == (
        "DETERMINISTIC_CONTRACT_PREFLIGHT"
    )
    assert all(gates["idea_market_preflight_criteria_source"]["criteria"].values())
    assert len(gates["component_results"]) == 7
    assert all(item["verdict"] == "PASS" for item in gates["component_results"])
    assert gates["all_mandatory_evidence_present"] is True
    assert all(
        value["evidence_artifact"]["content_hash"]
        for value in gates["subject_artifact_bindings"].values()
    )
    assert artifacts["market_alignment_dossier"]["content"]["overall_verdict"] == "PASS"
    assert artifacts["market_alignment_dossier"]["content"]["video_project_ref"] == (
        f"video-project://{result['video_project_id']}"
    )
    assert artifacts["niche_alignment_dossier"]["content"]["overall_verdict"] == "PASS"
    assert artifacts["target_market_consistency_check"]["content"]["overall_decision"] == "PASS"

    destination = artifacts["destination_binding"]["content"]["destination"]
    assert destination["platform"] == "YOUTUBE"
    assert destination["channel_handle"] == "@SmallTeamAI"
    assert destination["platform_channel_id"] is None
    assert destination["credential_ref"] is None
    assert destination["destination_status"] == "PENDING_PLATFORM_ID"

    voice = artifacts["voice_policy"]["content"]
    assert voice["content_language"] == "en"
    assert voice["narration_locale"] == "en-US"
    assert voice["voice_profile_locale"] == "en-US"
    assert voice["tts_called"] is False
    visual = artifacts["visual_direction_contract"]["content"]
    assert visual["niche_visual_source_profile"] == "STOCK_ASSISTED"
    assert visual["rules"]["gemini_image"] == {
        "use": "custom editorial still only",
        "model": "gemini-3.1-flash-image",
        "size": "2K",
        "outputs": 1,
        "maximum_automated_attempts": 1,
        "exact_text_requires_native_overlay": True,
    }
    decisions = artifacts["visual_source_decision_set"]["content"]
    scene_count = len(artifacts["scene_visual_intent"]["content"]["scenes"])
    assert len(decisions["decisions"]) == scene_count
    assert decisions["automatic_pexels_to_ai_fallback"] is False
    assert decisions["provider_outputs"] == []
    assert all(
        item["preferred_source_route"] in {"PEXELS_VIDEO", "NATIVE_DIAGRAM"}
        for item in decisions["decisions"]
    )

    thumbnail = artifacts["thumbnail_brief"]["content"]
    metadata = artifacts["publishing_metadata_package"]["content"]
    assert thumbnail["target_market"] == "US"
    assert thumbnail["text_locale"] == "en-US"
    assert artifacts["thumbnail_brief"]["version_number"] == 2
    assert thumbnail["market_alignment_dossier"]["content_hash"] == artifacts[
        "market_alignment_dossier"
    ]["content_hash"]
    assert thumbnail["market_alignment_evidence_subject"]["version_number"] == 1
    assert metadata["locale"] == "en-US"
    assert metadata["market"] == "US"
    assert metadata["original_language"] == "en"
    assert metadata["checks"]["metadata_drift_from_script"] is False

    provider = artifacts["provider_execution_plan"]["content"]
    assert provider["execution_enabled"] is False
    assert provider["automatic_pexels_to_ai_fallback"] is False
    assert provider["external_ai_video_fallback"] is False
    assert provider["provider_outputs"] == []
    assert len(provider["scene_routes"]) == scene_count
    drive_stage = next(
        item for item in provider["stages"] if item["provider"] == "google_drive"
    )
    assert drive_stage == {
        "order": 8,
        "provider": "google_drive",
        "operation": "canonical_review_archive_plus_finalization_supplement",
        "planned_requests": 2,
        "state": "WAITING_FOR_FINAL_MEDIA",
        "idempotency_phases": DRIVE_IDEMPOTENCY_PHASES,
    }
    cost = artifacts["cost_estimate_snapshot"]["content"]
    assert "config://media_provider_budget_policy_catalog/1.0.2" in cost["catalog_refs"]
    assert "config://google_gemini_image_model_price_catalog/2026-07-17" in cost["catalog_refs"]
    assert "config://google_veo_model_price_catalog/2026-07-12" in cost["catalog_refs"]
    assert all(item["content_hash"] for item in cost["catalog_bindings"])
    assert cost["actual_cost"] is None
    assert cost["estimated_cost"] <= cost["hard_cap"]
    assert cost["decision"] == "PASS"
    drive_cost = next(
        item for item in cost["line_items"] if item["provider"] == "google_drive"
    )
    assert drive_cost["planned_requests"] == 2
    assert drive_cost["idempotency_phases"] == DRIVE_IDEMPOTENCY_PHASES
    assert drive_cost["estimated_incremental_cost_usd"] == 0.0

    provenance = artifacts["asset_provenance_plan"]["content"]
    rights = artifacts["rights_disclosure_completeness_report"]["content"]
    assert provenance["provider_output_exists"] is False
    assert provenance["generated_evidence_authority"] is False
    assert rights["provider_outputs_claimed"] is False
    assert rights["generated_evidence_authority"] is False
    publish = artifacts["publish_handoff_package"]["content"]
    assert publish["media_output_placeholder"]["file_ref"] is None
    assert publish["UPLOAD_READY"] is False
    assert publish["PUBLISH_EXECUTION_READY"] is False
    assert publish["MARKET_PACKAGE_FROZEN"] is False
    assert publish["package_state"] == "DRAFT"
    assert publish["technical_media_qc"] == "REVIEW_REQUIRED"
    assert publish["creative_human_review"] == "PENDING"
    assert publish["publish_blocker"] == "PENDING_PLATFORM_ID"
    risk = artifacts["publish_risk_dossier"]["content"]
    assert risk["market_alignment"]["overall_decision"] == "REVIEW_REQUIRED"
    assert risk["market_alignment"]["publish_window_status"] == "REVIEW_REQUIRED"
    assert risk["package_integrity"]["final_package_integrity"] == (
        "PENDING_PACKAGE_HASH"
    )
    assert artifacts["upload_card"]["content"]["human_upload_task_created"] is False


def test_market_revision_is_idempotent_and_revision_hash_is_stable(
    db_session, tmp_path
) -> None:
    _, operator, channel, _, _, _, reports = _revision_scope(db_session, tmp_path)
    service = PKG1MarketRevisionService(db_session, report_paths=reports)
    first = service.build_revision(
        channel_id=channel.id, created_by_user_id=operator.id
    )
    project_count = db_session.scalar(
        select(func.count()).select_from(VideoProject).where(
            VideoProject.project_type == PROJECT_TYPE
        )
    )
    artifact_count = db_session.scalar(
        select(func.count()).select_from(Artifact).where(
            Artifact.video_project_id == uuid.UUID(first["video_project_id"])
        )
    )
    second = service.build_revision(
        channel_id=channel.id, created_by_user_id=operator.id
    )
    assert second["video_project_id"] == first["video_project_id"]
    assert second["package_artifact_version_id"] == first["package_artifact_version_id"]
    assert second["revision_id"] == first["revision_id"]
    assert second["revision_hash"] == first["revision_hash"]
    assert db_session.scalar(
        select(func.count()).select_from(VideoProject).where(
            VideoProject.project_type == PROJECT_TYPE
        )
    ) == project_count
    assert db_session.scalar(
        select(func.count()).select_from(Artifact).where(
            Artifact.video_project_id == uuid.UUID(first["video_project_id"])
        )
    ) == artifact_count
    assert content_hash({"b": 2, "a": 1}) == content_hash({"a": 1, "b": 2})
    assert content_hash({"scenes": ["a", "b"]}) != content_hash(
        {"scenes": ["b", "a"]}
    )


def test_market_revision_rerun_rejects_non_pending_human_review(
    db_session, tmp_path
) -> None:
    _, operator, channel, _, _, _, reports = _revision_scope(
        db_session, tmp_path
    )
    service = PKG1MarketRevisionService(db_session, report_paths=reports)
    first = service.build_revision(
        channel_id=channel.id, created_by_user_id=operator.id
    )
    review = db_session.scalar(
        select(ReviewTask).where(
            ReviewTask.id == uuid.UUID(first["human_review_task_ids"][0])
        )
    )
    assert review is not None
    review.status = "completed"
    db_session.flush()

    with pytest.raises(
        ValidationFailureError,
        match="EXISTING_REVISION_EXACT_OPEN_REVIEW_MISSING",
    ):
        service.build_revision(
            channel_id=channel.id, created_by_user_id=operator.id
        )


def test_repository_entry_reports_pin_canonical_production_ids_and_hashes() -> None:
    root = Path(__file__).resolve().parents[1]
    summary = json.loads(
        (root / "reports/ch1_market_profile_v3_summary.json").read_text(
            encoding="utf-8"
        )
    )
    activation = summary["production_activation"]
    assert activation["profile_v3_id"] == "d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711"
    assert activation["snapshot_v3_id"] == "e6c33d80-f5d8-4f72-9abc-87de3601b89e"
    assert activation["target_market_profile_hash"] == "d456033a947408f671b328f9c5f5589ae86ea4529caf60b18c3d913058d1bb9e"
    assert activation["target_market_digest_hash"] == "244989186381a71c4eda812743b3b095426397ae0cdfb791641b2875918014f0"
    assert activation["destination_binding_hash"] == "411aae66418315da8e6a0bf2cd23e896e89e7cd4827a5b54c36c0437ad63efab"
    assert activation["destination_status"] == "PENDING_PLATFORM_ID"
    assert activation["publish_execution_allowed"] is False
