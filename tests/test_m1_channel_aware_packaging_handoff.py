from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.contracts.r3d1 import ContentCategoryCreate
from app.contracts.r3d1 import CharacterImageBranchCreate, CharacterProfileCreate, CharacterVersionCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.time import utc_now
from app.db.models import FirstScriptedVideoPackage, MediaRenderJob, ProviderAttempt, UploadedVideo, VideoProject
from app.main import create_app
from app.services import R3D1AdminService, VideoProjectService
from app.services.m1 import PackagingHandoffReadService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _project_with_context(db_session, qualification_factory) -> tuple[Any, VideoProject, Any]:
    scope = qualification_factory.channel_scope(name="M1")
    scope.channel.primary_language = "vi"
    scope.channel.primary_region = "VN"
    scope.channel.primary_timezone = "Asia/Ho_Chi_Minh"
    scope.channel.target_regions = ["VN"]
    category = R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"m1-{uuid.uuid4().hex[:8]}",
            name="M1 Packaging",
            default_format_policy_json={"target_duration_seconds": 480, "structure": ["hook", "payoff", "takeaway"]},
            default_visual_style_json={"style_note": "operator dashboard cards"},
            default_thumbnail_style_json={"style": "clear mobile text"},
            visual_mode="DIAGRAM_FIRST",
            character_policy_mode="NO_CHARACTER",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    project_read = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id,
            title="M1 packaging handoff project",
            description="Fixture for packaging handoff.",
            created_by_user_id=scope.operator.id,
        )
    )
    project = db_session.get(VideoProject, project_read.id)
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    effective.publish_timing_context_json = {
        "channel_timezone": "America/New_York",
        "audience_timezone": "America/New_York",
        "configured_publish_window": {"windows": [{"day": "MONDAY", "start": "09:00", "end": "11:00"}]},
        "manual_publish_only": True,
        "source_contract_paths": ["platform_strategy.publish_mode", "market_locale.timezone"],
    }
    db_session.flush()
    return scope, project, effective


def _artifacts(**overrides: Any) -> dict[str, Any]:
    artifacts: dict[str, Any] = {
        "hook_spec": {
            "hook_type": "DIRECT",
            "first_3_seconds_script": "VCOS can prepare a package without paid provider calls.",
            "first_3_seconds_visual": "Operator dashboard provider boundary card",
            "promise_made": "VCOS stops before paid provider calls",
            "payoff_location": "S2",
            "clickbait_risk": "LOW",
            "evidence_refs": [{"ref": "operator_note:m1"}],
            "contract_paths_used": ["platform_strategy.publish_mode"],
        },
        "narration_script": {
            "language": "vi",
            "sentences": [
                {"sentence_id": "S1", "text": "VCOS prepares packaging copy for a human operator.", "approx_seconds": 240},
                {"sentence_id": "S2", "text": "VCOS stops before paid provider calls and keeps manual upload only.", "approx_seconds": 240},
            ],
            "total_approx_seconds": 480,
        },
        "metadata_package": {
            "title": "VCOS packaging handoff without paid calls",
            "description": "Copy this package into YouTube manually after review. VCOS does not upload or publish.",
            "hashtags": ["VCOS", "AIWorkflow"],
            "subtitle_refs": [{"ref": "caption-track:draft", "lifecycle_state": "DRAFT_SCRIPT_TIMING"}],
            "disclosure_notes": ["AI-assisted draft; future generated media needs provider review."],
            "language": "vi",
            "locale": "vi-VN",
        },
        "visual_plan": {
            "scenes": [
                {"sentence_id": "S1", "description": "Operator dashboard package copy", "intended_visual_source": "DIAGRAM"},
                {"sentence_id": "S2", "description": "Provider boundary card showing no paid calls", "intended_visual_source": "CARD"},
            ]
        },
        "thumbnail_brief": {
            "concept": "Provider boundary dashboard",
            "text_overlay": "No paid calls",
            "main_subject": "VCOS dashboard",
            "composition": "Large text over operator panel",
            "mobile_readability_notes": "Three words, high contrast.",
            "variants": [{"concept": "Provider boundary dashboard", "text": "No paid calls"}],
            "rendered": False,
        },
        "rights_disclosure_review": {
            "result": "REVIEW_REQUIRED",
            "source_manifest_status": "OPERATOR_NOTES_ONLY",
            "ai_disclosure_needed": True,
            "rights_risk": "MEDIUM",
            "disclosure_notes": "Future generated media still needs source/provider manifest review.",
        },
        "upload_card_copy": {
            "title": "VCOS packaging handoff without paid calls",
            "description": "Copy this package into YouTube manually after review. VCOS does not upload or publish.",
            "not_uploaded": True,
            "checklist_items": ["Copy title", "Copy description", "Upload subtitles if final"],
        },
        "human_review_checklist": {"final_human_review": "PENDING", "upload_card_copy_ready": True},
    }
    artifacts.update(overrides)
    return artifacts


def _package(db_session, scope, project, effective, *, artifacts: dict[str, Any] | None = None) -> FirstScriptedVideoPackage:
    package = FirstScriptedVideoPackage(
        video_project_id=project.id,
        channel_id=scope.channel.id,
        channel_profile_version_id=scope.profile.id,
        compiled_policy_snapshot_id=scope.snapshot.id,
        effective_context_snapshot_id=effective.id,
        effective_context_hash=effective.context_hash,
        provider_readiness_snapshot_id=None,
        package_status="READY_FOR_HUMAN_REVIEW",
        agent_run_refs=[],
        prompt_render_run_refs=[],
        prompt_audit_snapshot_refs=[],
        artifacts=artifacts or _artifacts(),
        limitations=["Manual upload only."],
        risk_limitations_summary={"media_provider_calls_made": False, "upload_or_publish_calls_made": False},
        next_action="Human final approval required.",
    )
    db_session.add(package)
    db_session.flush()
    return package


def _handoff(db_session, qualification_factory, *, artifacts: dict[str, Any] | None = None):
    scope, project, effective = _project_with_context(db_session, qualification_factory)
    package = _package(db_session, scope, project, effective, artifacts=artifacts)
    return PackagingHandoffReadService(db_session).build(package.id), scope, package, effective


def _gate_status(handoff, gate_key: str) -> tuple[str, list[str]]:
    gate = next(item for item in handoff.packaging_gate_summary.gate_results if item.gate_key == gate_key)
    return gate.status, gate.reason_codes


def test_m1_packaging_handoff_includes_upload_copy_subtitles_and_disclosures(db_session, qualification_factory) -> None:
    handoff, _, package, _ = _handoff(db_session, qualification_factory)

    assert handoff.package_id == package.id
    assert handoff.upload_handoff_copy.title == "VCOS packaging handoff without paid calls"
    assert handoff.upload_handoff_copy.description
    assert handoff.upload_handoff_copy.subtitle_refs_json[0]["lifecycle_state"] == "DRAFT_SCRIPT_TIMING"
    assert handoff.upload_handoff_copy.disclosure_notes_json
    assert handoff.upload_handoff_copy.checklist_items_json
    assert handoff.manual_publish_only is True


def test_m1_hook_spec_extraction_works_from_package_artifacts(db_session, qualification_factory) -> None:
    handoff, _, _, _ = _handoff(db_session, qualification_factory)

    assert handoff.hook_spec.hook_type == "DIRECT"
    assert handoff.hook_spec.first_3_seconds_script.startswith("VCOS can prepare")
    assert handoff.hook_spec.promise_made == "VCOS stops before paid provider calls"
    assert handoff.hook_spec.payoff_location == "S2"
    assert handoff.hook_spec.content_hash


def test_m1_hook_truthfulness_and_payoff_gate_blocks_unsupported_promise(db_session, qualification_factory) -> None:
    artifacts = _artifacts(
        hook_spec={
            "hook_type": "OUTCOME",
            "first_3_seconds_script": "Get a free checklist instantly.",
            "first_3_seconds_visual": "Operator dashboard provider boundary card",
            "promise_made": "free checklist download",
            "payoff_location": "S2",
            "clickbait_risk": "HIGH",
        }
    )

    handoff, _, _, _ = _handoff(db_session, qualification_factory, artifacts=artifacts)

    status, codes = _gate_status(handoff, "HookTruthfulnessGate")
    assert status == "BLOCK"
    assert "HOOK_PROMISE_UNSUPPORTED_BY_SCRIPT" in codes


def test_m1_title_promise_gate_catches_title_over_promise(db_session, qualification_factory) -> None:
    artifacts = _artifacts(
        metadata_package={**_artifacts()["metadata_package"], "title": "Free checklist download for every workflow"},
        upload_card_copy={**_artifacts()["upload_card_copy"], "title": "Free checklist download for every workflow"},
    )

    handoff, _, _, _ = _handoff(db_session, qualification_factory, artifacts=artifacts)

    status, codes = _gate_status(handoff, "TitlePromiseGate")
    assert status == "BLOCK"
    assert "TITLE_OVER_PROMISE_UNSUPPORTED_OFFER" in codes


def test_m1_metadata_truthfulness_gate_catches_nonexistent_demo_claim(db_session, qualification_factory) -> None:
    base = _artifacts()
    base["upload_card_copy"] = {**base["upload_card_copy"], "description": "Watch the product demo and download checklist today."}

    handoff, _, _, _ = _handoff(db_session, qualification_factory, artifacts=base)

    status, codes = _gate_status(handoff, "MetadataTruthfulnessGate")
    assert status == "BLOCK"
    assert "METADATA_UNSUPPORTED_ASSET_OR_DEMO_CLAIM" in codes


def test_m1_thumbnail_truthfulness_and_mobile_legibility_gates(db_session, qualification_factory) -> None:
    base = _artifacts()
    base["thumbnail_brief"] = {
        **base["thumbnail_brief"],
        "concept": "Shocking proof of impossible ROI",
        "text_overlay": "This impossible workflow secret changes everything today",
    }

    handoff, _, _, _ = _handoff(db_session, qualification_factory, artifacts=base)

    thumb_status, thumb_codes = _gate_status(handoff, "ThumbnailTruthfulnessGate")
    mobile_status, mobile_codes = _gate_status(handoff, "MobileThumbnailLegibilityGate")
    assert thumb_status == "BLOCK"
    assert "THUMBNAIL_MISLEADING_PROMISE" in thumb_codes
    assert mobile_status == "BLOCK"
    assert "THUMBNAIL_TEXT_NOT_MOBILE_LEGIBLE" in mobile_codes


def test_m1_character_thumbnail_consistency_blocks_wrong_branch(db_session, qualification_factory) -> None:
    scope, project, effective = _project_with_context(db_session, qualification_factory)
    admin = R3D1AdminService(db_session)
    profile = admin.create_character_profile(
        CharacterProfileCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            character_key=f"host-{uuid.uuid4().hex[:8]}",
            display_name="Operator Host",
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )
    version = admin.create_character_version(
        CharacterVersionCreate(character_profile_id=profile.id, version=1, status="ACTIVE", human_approved_at=utc_now())
    )
    expected_branch = admin.create_character_image_branch(
        CharacterImageBranchCreate(character_version_id=version.id, branch_key="frozen", status="ACTIVE", human_approved_at=utc_now())
    )
    wrong_branch = admin.create_character_image_branch(
        CharacterImageBranchCreate(character_version_id=version.id, branch_key="wrong", status="ACTIVE", human_approved_at=utc_now())
    )
    effective.character_image_branch_id = expected_branch.id
    db_session.flush()
    base = _artifacts()
    base["thumbnail_brief"] = {**base["thumbnail_brief"], "character_image_branch_id": str(wrong_branch.id)}
    package = _package(db_session, scope, project, effective, artifacts=base)

    handoff = PackagingHandoffReadService(db_session).build(package.id)

    status, codes = _gate_status(handoff, "CharacterThumbnailConsistencyGate")
    assert status == "BLOCK"
    assert "THUMBNAIL_CHARACTER_BRANCH_MISMATCH" in codes


def test_m1_publish_timing_uses_effective_context_snapshot_not_latest_channel_settings(db_session, qualification_factory) -> None:
    handoff, scope, _, _ = _handoff(db_session, qualification_factory)
    scope.channel.primary_timezone = "Pacific/Honolulu"
    db_session.flush()
    refreshed = PackagingHandoffReadService(db_session).build(handoff.package_id)

    assert refreshed.publish_timing_recommendation.channel_timezone == "America/New_York"
    assert refreshed.publish_timing_recommendation.configured_publish_window_json["windows"][0]["start"] == "09:00"
    assert refreshed.publish_timing_recommendation.suggested_publish_time_channel_tz is not None


def test_m1_manual_publish_only_gate_blocks_automation_attempt(db_session, qualification_factory) -> None:
    base = _artifacts()
    base["upload_card_copy"] = {**base["upload_card_copy"], "auto_publish": True}

    handoff, _, _, _ = _handoff(db_session, qualification_factory, artifacts=base)

    status, codes = _gate_status(handoff, "ManualPublishOnlyGate")
    assert status == "BLOCK"
    assert "UPLOAD_OR_PUBLISH_AUTOMATION_ATTEMPT" in codes


def test_m1_api_review_exposes_packaging_handoff_and_alias(db_session, qualification_factory) -> None:
    _, _, package, _ = _handoff(db_session, qualification_factory)
    db_session.commit()
    client = TestClient(create_app())

    review = client.get(f"/video-packages/{package.id}/review")
    alias = client.get(f"/video-packages/{package.id}/packaging-handoff")

    assert review.status_code == 200
    assert alias.status_code == 200
    assert review.json()["packaging_handoff"]["hook_spec"]["hook_type"] == "DIRECT"
    assert alias.json()["upload_handoff_copy"]["title"] == "VCOS packaging handoff without paid calls"


def test_m1_does_not_call_providers_uploads_or_vector_retrieval(db_session, qualification_factory) -> None:
    _handoff(db_session, qualification_factory)

    assert db_session.query(MediaRenderJob).count() == 0
    assert db_session.query(ProviderAttempt).count() == 0
    assert db_session.query(UploadedVideo).count() == 0
    source = open("app/services/m1.py", encoding="utf-8").read().lower()
    forbidden = ["requests.", "httpx", "googledriveuploadservice", "youtubeuploadservice", "vector_store", "embedding_service", "rag_retrieval"]
    assert [token for token in forbidden if token in source] == []
