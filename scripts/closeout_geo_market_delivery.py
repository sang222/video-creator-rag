from __future__ import annotations

# ruff: noqa: E402 -- repository root is inserted before application imports.

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.geo_delivery import (
    GeoMarketDeliveryCloseoutEvidence,
    MarketDeliveryEvidence,
)
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    VideoProject,
)
from app.db.session import session_scope
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.geo_delivery import (
    AdsOnlyMonetizationPolicyService,
    GeoDeliveryCloseoutArtifactService,
    MarketDeliveryAlignmentGate,
    destination_runtime_contract,
)
from app.services.pkg1_market_revision import PKG1MarketRevisionService
from app.services.workflow import VideoProjectService


REPORTS = ROOT / "reports"
DEFAULT_VERIFICATION_RECEIPT_PATH = (
    REPORTS / "geo_market_delivery_verification_receipt.json"
)

CHANNEL_KEY = "small-team-ai"
CHANNEL_ID = uuid.UUID("a77bc5dc-f7be-4ae0-8523-55fb846d64bd")
PROFILE_ID = uuid.UUID("d0d16fc5-0dc9-4022-bfd3-7f9a47c3a711")
SNAPSHOT_ID = uuid.UUID("e6c33d80-f5d8-4f72-9abc-87de3601b89e")
SNAPSHOT_HASH = "12b66551bd9bdfce1d59d1019ff50bc1c49756b6dc4ab505fde080630b4551bc"
SOURCE_PROJECT_ID = uuid.UUID("2522a8f1-1ea4-4d66-8ea5-411aaa8f152b")
PACKAGE_VERSION_ID = uuid.UUID("7de25ac8-46e4-46da-b112-f805f16ebaaa")
PACKAGE_HASH = "200b3be30b92ccff3b0efb26881d5654ab4b53162afe73d4e7f34bed3b0454bd"
APPROVAL_ID = uuid.UUID("ef766b1d-c1a5-43b8-be98-0751bd055653")
HUMAN_RECEIPT_VERSION_ID = uuid.UUID("a35c55b8-6887-4e60-a19c-22928205c572")
HUMAN_RECEIPT_HASH = "24a2d4c7b0dec7394a8b78ab646f66750fbca35282700d50dcde77bd304c2231"

PROJECT_TYPE = "GEO_MARKET_DELIVERY_CLOSEOUT"
PROJECT_TITLE = "Geo/Market Delivery Closeout — small-team-ai — snapshot v3"
OVERLAY_AUTHORITY_REF = (
    "operator-prompt://geo-market-delivery-closeout/2026-07-21/"
    "ads-only-effective-overlay"
)
BUILDER_VERSION = "geo-market-delivery-closeout-builder/1.0.0"
EXPECTED_BASE_MONETIZATION_POLICY = {
    "primary": "mixed",
    "channels": ["adsense", "affiliate"],
}
ACTIVE_PACKAGE_COMPONENT_ARTIFACT_STATUSES = frozenset({"in_review", "approved"})
ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES = frozenset({"submitted", "approved"})
REQUIRED_TARGET_MARKET_CONSISTENCY_CHECKS = frozenset(
    {
        "content_language_match",
        "narration_locale_match",
        "title_locale_match",
        "thumbnail_locale_match",
        "caption_language_match",
        "currency_units_match",
        "cultural_context_match",
        "source_jurisdiction_match",
        "topic_market_demand_match",
        "destination_market_match",
    }
)


def _require_base_monetization_truth(
    compiled_payload: dict[str, Any],
) -> dict[str, Any]:
    """Pin the exact immutable snapshot-v3 monetization truth and its duplicate."""

    base_monetization = compiled_payload.get("monetization_policy")
    legacy_monetization = (
        (compiled_payload.get("compiled_policy_snapshot_json") or {})
        .get("legacy_policy_sections", {})
        .get("monetization_policy")
    )
    if (
        base_monetization != EXPECTED_BASE_MONETIZATION_POLICY
        or legacy_monetization != EXPECTED_BASE_MONETIZATION_POLICY
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_BASE_MONETIZATION_TRUTH_CHANGED")
    return dict(base_monetization)


def _artifact_version_ref(version: ArtifactVersion) -> str:
    return f"artifact-version://{version.id}"


def _require_historical_source_project_lineage(
    session: Session,
    *,
    snapshot: CompiledChannelPolicySnapshot,
    source_project_id: uuid.UUID,
    bindings: dict[str, Any],
) -> tuple[uuid.UUID, VideoProject, VideoProject]:
    historical_binding = bindings.get("historical_video_project") or {}
    historical_ref = historical_binding.get("ref")
    if not isinstance(historical_ref, str) or not historical_ref.startswith(
        "video-project://"
    ):
        raise ValidationFailureError(
            "GEO_CLOSEOUT_HISTORICAL_SOURCE_PROJECT_BINDING_MISSING"
        )
    try:
        historical_project_id = uuid.UUID(
            historical_ref.removeprefix("video-project://")
        )
    except ValueError as exc:
        raise ValidationFailureError(
            "GEO_CLOSEOUT_HISTORICAL_SOURCE_PROJECT_BINDING_INVALID"
        ) from exc

    historical_project = session.get(VideoProject, historical_project_id)
    source_project = session.get(VideoProject, source_project_id)
    revision_binding = bindings.get("revision_video_project") or {}
    if (
        historical_project is None
        or source_project is None
        or historical_project.status != "approved"
        or source_project.status != "approved"
        or historical_project.channel_workspace_id
        != source_project.channel_workspace_id
        or source_project.channel_workspace_id != snapshot.channel_workspace_id
        or source_project.policy_snapshot_id != snapshot.id
        or historical_binding.get("content_hash")
        != PKG1MarketRevisionService._project_hash(historical_project)
        or revision_binding.get("ref") != f"video-project://{source_project.id}"
        or revision_binding.get("content_hash")
        != PKG1MarketRevisionService._project_hash(source_project)
    ):
        raise ValidationFailureError(
            "GEO_CLOSEOUT_HISTORICAL_SOURCE_PROJECT_LINEAGE_INVALID"
        )
    return historical_project_id, historical_project, source_project


def _require_version(
    session: Session,
    *,
    binding: dict[str, Any],
    expected_artifact_type: str,
    expected_project_id: uuid.UUID,
) -> ArtifactVersion:
    raw_id = binding.get("artifact_version_id")
    if not raw_id:
        raise ValidationFailureError(
            f"GEO_CLOSEOUT_BOUND_VERSION_ID_MISSING:{expected_artifact_type}"
        )
    version = session.get(ArtifactVersion, uuid.UUID(str(raw_id)))
    artifact = session.get(Artifact, version.artifact_id) if version else None
    if (
        version is None
        or artifact is None
        or artifact.artifact_type != expected_artifact_type
        or artifact.video_project_id != expected_project_id
        or artifact.current_version_id != version.id
        or artifact.status not in ACTIVE_PACKAGE_COMPONENT_ARTIFACT_STATUSES
        or version.status not in ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES
        or version.content_hash != binding.get("content_hash")
        or content_hash(version.content or {}) != version.content_hash
        or binding.get("artifact_id") not in {None, str(artifact.id)}
        or binding.get("artifact_version_ref")
        not in {None, _artifact_version_ref(version)}
    ):
        raise ValidationFailureError(
            f"GEO_CLOSEOUT_BOUND_VERSION_MISMATCH:{expected_artifact_type}"
        )
    return version


def _load_verification_receipt_locator(
    path: Path,
) -> tuple[uuid.UUID, str]:
    if not path.is_file():
        raise ValidationFailureError(
            f"GEO_CLOSEOUT_VERIFICATION_RECEIPT_LOCATOR_MISSING:{path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifact_version_id = uuid.UUID(str(payload["artifact_version_id"]))
        receipt_hash = str(payload["content_hash"])
    except Exception as exc:
        raise ValidationFailureError(
            "GEO_CLOSEOUT_VERIFICATION_RECEIPT_LOCATOR_INVALID"
        ) from exc
    if len(receipt_hash) != 64 or any(
        character not in "0123456789abcdef" for character in receipt_hash
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_VERIFICATION_RECEIPT_HASH_INVALID")
    return artifact_version_id, receipt_hash


def _read_persisted_closeout(
    session: Session,
    *,
    project: VideoProject,
    artifact_version_id: uuid.UUID,
    expected_hash: str,
) -> GeoMarketDeliveryCloseoutEvidence:
    version = session.get(ArtifactVersion, artifact_version_id)
    artifact = session.get(Artifact, version.artifact_id) if version else None
    if (
        version is None
        or artifact is None
        or artifact.video_project_id != project.id
        or artifact.artifact_type != "geo_market_delivery_closeout_evidence"
        or artifact.current_version_id != version.id
        or version.status != "submitted"
        or version.content_hash != expected_hash
        or content_hash(version.content or {}) != version.content_hash
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_PERSISTED_EVIDENCE_INVALID")
    try:
        return GeoMarketDeliveryCloseoutEvidence.model_validate(version.content)
    except Exception as exc:
        raise ValidationFailureError(
            "GEO_CLOSEOUT_PERSISTED_EVIDENCE_SCHEMA_INVALID"
        ) from exc


def _resolve_source(
    session: Session,
) -> tuple[
    ChannelWorkspace,
    ChannelProfileVersion,
    CompiledChannelPolicySnapshot,
    ChannelScopedPolicy,
    ArtifactVersion,
    ApprovalDecision,
    dict[str, Any],
]:
    channel = session.get(ChannelWorkspace, CHANNEL_ID)
    profile = session.get(ChannelProfileVersion, PROFILE_ID)
    snapshot = session.get(CompiledChannelPolicySnapshot, SNAPSHOT_ID)
    package = session.get(ArtifactVersion, PACKAGE_VERSION_ID)
    package_artifact = session.get(Artifact, package.artifact_id) if package else None
    approval = session.get(ApprovalDecision, APPROVAL_ID)
    receipt = session.get(ArtifactVersion, HUMAN_RECEIPT_VERSION_ID)
    receipt_artifact = (
        session.get(Artifact, receipt.artifact_id) if receipt is not None else None
    )
    source_project = session.get(VideoProject, SOURCE_PROJECT_ID)
    failures: list[str] = []
    if (
        channel is None
        or channel.key != CHANNEL_KEY
        or channel.active_policy_snapshot_id != SNAPSHOT_ID
    ):
        failures.append("ACTIVE_CHANNEL_MISMATCH")
    if (
        profile is None
        or profile.channel_workspace_id != CHANNEL_ID
        or profile.version != 3
        or profile.status != "active"
        or content_hash(profile.profile_input) != profile.profile_input_hash
    ):
        failures.append("ACTIVE_PROFILE_V3_MISMATCH")
    if (
        snapshot is None
        or snapshot.channel_workspace_id != CHANNEL_ID
        or snapshot.channel_profile_version_id != PROFILE_ID
        or snapshot.status != "active"
        or snapshot.content_hash != SNAPSHOT_HASH
        or content_hash(snapshot.compiled_payload) != snapshot.content_hash
    ):
        failures.append("ACTIVE_SNAPSHOT_V3_MISMATCH")
    if (
        source_project is None
        or source_project.policy_snapshot_id != SNAPSHOT_ID
        or source_project.channel_workspace_id != CHANNEL_ID
        or source_project.status != "approved"
    ):
        failures.append("APPROVED_SOURCE_PROJECT_MISMATCH")
    if (
        package is None
        or package_artifact is None
        or package_artifact.video_project_id != SOURCE_PROJECT_ID
        or package_artifact.artifact_type != "package_manifest"
        or package_artifact.current_version_id != package.id
        or package_artifact.status != "approved"
        or package.status not in ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES
        or package.content_hash != PACKAGE_HASH
        or content_hash(package.content) != package.content_hash
    ):
        failures.append("APPROVED_PACKAGE_MISMATCH")
    approval_metadata = approval.metadata_ if approval else {}
    if (
        approval is None
        or approval.decision != "approved"
        or approval.target_artifact_version_id != PACKAGE_VERSION_ID
        or approval_metadata.get("approval_scope")
        != "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
        or approval_metadata.get("package_artifact_version_id")
        != str(PACKAGE_VERSION_ID)
        or approval_metadata.get("package_content_hash") != PACKAGE_HASH
    ):
        failures.append("EXACT_HUMAN_APPROVAL_MISMATCH")
    if (
        receipt is None
        or receipt_artifact is None
        or receipt_artifact.video_project_id != SOURCE_PROJECT_ID
        or receipt_artifact.artifact_type != "pkg1_market_revision_human_review_receipt"
        or receipt_artifact.current_version_id != receipt.id
        or receipt_artifact.status != "approved"
        or receipt.content_hash != HUMAN_RECEIPT_HASH
        or content_hash(receipt.content) != receipt.content_hash
        or receipt.content.get("approval_decision_id") != str(APPROVAL_ID)
        or (receipt.content.get("reviewed_package") or {}).get("artifact_version_id")
        != str(PACKAGE_VERSION_ID)
        or (receipt.content.get("reviewed_package") or {}).get("content_hash")
        != PACKAGE_HASH
    ):
        failures.append("EXACT_HUMAN_RECEIPT_MISMATCH")
    if failures:
        raise ValidationFailureError(
            "GEO_CLOSEOUT_SOURCE_INVALID:" + ",".join(failures)
        )
    assert channel is not None
    assert profile is not None
    assert snapshot is not None
    assert package is not None
    assert approval is not None
    policy = ChannelScopedPolicy.model_validate(
        (snapshot.compiled_payload or {}).get("channel_scoped_policy")
    )
    if (
        policy.target_market_profile is None
        or policy.target_market_digest is None
        or policy.destination_binding_policy is None
        or policy.publish_timing_localization_policy is None
        or policy.geo_evaluation_policy is None
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_MARKET_POLICY_SLICE_MISSING")
    manifest = package.content or {}
    return channel, profile, snapshot, policy, package, approval, manifest


def _ensure_project(
    session: Session,
    *,
    channel: ChannelWorkspace,
    profile: ChannelProfileVersion,
    snapshot: CompiledChannelPolicySnapshot,
    approval: ApprovalDecision,
    manifest: dict[str, Any],
) -> VideoProject:
    rows = list(
        session.scalars(
            select(VideoProject).where(
                VideoProject.channel_workspace_id == channel.id,
                VideoProject.policy_snapshot_id == snapshot.id,
                VideoProject.project_type == PROJECT_TYPE,
            )
        ).all()
    )
    if len(rows) > 1:
        raise ValidationFailureError("GEO_CLOSEOUT_PROJECT_NOT_UNIQUE")
    category_id = uuid.UUID(
        str((manifest.get("exact_bindings") or {})["content_category"]["id"])
    )
    if rows:
        project = rows[0]
        if (
            project.status != "in_review"
            or project.channel_profile_version_id != profile.id
            or project.category_id != category_id
            or project.title != PROJECT_TITLE
            or project.company_id != channel.company_id
            or project.owner_user_id != approval.decided_by_user_id
            or project.created_by_user_id != approval.decided_by_user_id
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_PROJECT_LINEAGE_MISMATCH")
        return project
    return VideoProjectService(session).create_project(
        data=VideoProjectCreate(
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            channel_profile_version_id=profile.id,
            category_id=category_id,
            title=PROJECT_TITLE,
            description=(
                "Dedicated immutable implementation closeout for strict market "
                "delivery, geo diagnostics, and the ads-only effective overlay."
            ),
            status="in_review",
            project_type=PROJECT_TYPE,
            priority="normal",
            owner_user_id=approval.decided_by_user_id,
            created_by_user_id=approval.decided_by_user_id,
            financial_summary={
                "state": "POLICY_ONLY_NO_PROVIDER_EXECUTION",
                "provider_cost_usd": 0,
            },
            brand_safety_summary={"state": "NOT_CHANGED"},
            legal_compliance_summary={"state": "ADS_ONLY_OVERLAY_SUBMITTED"},
            audience_delivery_summary={
                "primary_market": "US",
                "destination_status": "PENDING_PLATFORM_ID",
                "upload_ready": False,
                "publish_execution_ready": False,
            },
        ),
        correlation_id="geo-market-delivery-closeout-project",
    )


def _required_text(
    value: Any,
    *,
    reason_code: str,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailureError(reason_code)
    return value.strip()


def _uniform_scene_value(
    scenes: list[dict[str, Any]],
    field_name: str,
) -> str:
    values = {
        _required_text(
            scene.get(field_name),
            reason_code=(f"GEO_CLOSEOUT_VISUAL_{field_name.upper()}_MISSING"),
        )
        for scene in scenes
    }
    if len(values) != 1:
        raise ValidationFailureError(
            f"GEO_CLOSEOUT_VISUAL_{field_name.upper()}_AMBIGUOUS"
        )
    return next(iter(values))


def _require_target_market_consistency_pass(
    content: dict[str, Any],
) -> None:
    checks = content.get("checks")
    if (
        content.get("overall_decision") != "PASS"
        or not isinstance(checks, dict)
        or set(checks) != REQUIRED_TARGET_MARKET_CONSISTENCY_CHECKS
        or any(value is not True for value in checks.values())
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_MARKET_CONSISTENCY_NOT_PASSING")


def _artifact_alignment_actuals(
    *,
    creative_content: dict[str, Any],
    script_content: dict[str, Any],
    voice_content: dict[str, Any],
    visual_content: dict[str, Any],
    visual_direction_content: dict[str, Any],
    visual_decisions_content: dict[str, Any],
    research_content: dict[str, Any],
    metadata_content: dict[str, Any],
    thumbnail_content: dict[str, Any],
    publish_handoff_content: dict[str, Any],
    expected_market: str,
    expected_content_language: str,
    expected_locale: str,
    expected_audience_market_context: str,
    expected_workplace_context: str,
) -> dict[str, Any]:
    """Extract delivery actuals from exact artifact bytes, never policy defaults."""

    _required_text(
        expected_content_language,
        reason_code="GEO_CLOSEOUT_EXPECTED_CONTENT_LANGUAGE_MISSING",
    )
    creative_market = _required_text(
        creative_content.get("target_market"),
        reason_code="GEO_CLOSEOUT_CREATIVE_MARKET_MISSING",
    )
    creative_locale = _required_text(
        creative_content.get("primary_locale"),
        reason_code="GEO_CLOSEOUT_CREATIVE_LOCALE_MISSING",
    )
    creative_narration_locale = _required_text(
        creative_content.get("narration_locale"),
        reason_code="GEO_CLOSEOUT_CREATIVE_NARRATION_LOCALE_MISSING",
    )
    thumbnail_market = _required_text(
        thumbnail_content.get("target_market"),
        reason_code="GEO_CLOSEOUT_THUMBNAIL_MARKET_MISSING",
    )
    thumbnail_locale = _required_text(
        thumbnail_content.get("text_locale"),
        reason_code="GEO_CLOSEOUT_THUMBNAIL_LOCALE_MISSING",
    )
    thumbnail_rules = thumbnail_content.get("rules")
    if (
        thumbnail_content.get("decision") != "PASS"
        or not isinstance(thumbnail_rules, dict)
        or thumbnail_rules.get("foreign_market_wording") is not False
        or thumbnail_rules.get("generated_exact_text_allowed") is not False
        or thumbnail_rules.get("unsupported_number_or_claim") is not False
        or thumbnail_rules.get("misleading_ui_or_product") is not False
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_THUMBNAIL_TRUTH_NOT_PASSING")
    scenes = visual_content.get("scenes")
    if (
        not isinstance(scenes, list)
        or not scenes
        or any(not isinstance(scene, dict) for scene in scenes)
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_VISUAL_SCENES_MISSING")
    visual_geos = sorted(
        {
            _required_text(
                scene.get("target_market"),
                reason_code="GEO_CLOSEOUT_VISUAL_TARGET_MARKET_MISSING",
            )
            for scene in scenes
        }
    )
    currency = _uniform_scene_value(scenes, "currency")
    market_contexts = {
        _required_text(
            scene.get("market_context"),
            reason_code="GEO_CLOSEOUT_VISUAL_MARKET_CONTEXT_MISSING",
        )
        for scene in scenes
    }
    workplace_contexts = {
        _required_text(
            scene.get("workplace_context"),
            reason_code="GEO_CLOSEOUT_VISUAL_WORKPLACE_CONTEXT_MISSING",
        )
        for scene in scenes
    }
    if any(scene.get("generated_evidence_authority") is not False for scene in scenes):
        raise ValidationFailureError(
            "GEO_CLOSEOUT_VISUAL_GENERATED_EVIDENCE_AUTHORITY_INVALID"
        )
    direction_locale = _required_text(
        visual_direction_content.get("primary_locale"),
        reason_code="GEO_CLOSEOUT_VISUAL_DIRECTION_LOCALE_MISSING",
    )
    direction_market = _required_text(
        visual_direction_content.get("target_market"),
        reason_code="GEO_CLOSEOUT_VISUAL_DIRECTION_MARKET_MISSING",
    )
    if set(visual_geos) != {direction_market}:
        raise ValidationFailureError(
            "GEO_CLOSEOUT_VISUAL_DIRECTION_SCENE_MARKET_MISMATCH"
        )
    decisions = visual_decisions_content.get("decisions")
    if (
        not isinstance(decisions, list)
        or not decisions
        or any(not isinstance(item, dict) for item in decisions)
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_VISUAL_SOURCE_DECISIONS_MISSING")
    if any(
        not isinstance(item.get("market_checks"), dict)
        or item["market_checks"].get("foreign_ui_context") is not False
        for item in decisions
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_VISUAL_FOREIGN_UI_CHECK_NOT_CLEAR")
    caption_plan = publish_handoff_content.get("caption_plan")
    if not isinstance(caption_plan, dict):
        raise ValidationFailureError("GEO_CLOSEOUT_CAPTION_PLAN_MISSING")
    caption_locale = _required_text(
        caption_plan.get("locale"),
        reason_code="GEO_CLOSEOUT_CAPTION_LOCALE_MISSING",
    )
    caption_plan_state = _required_text(
        caption_plan.get("state"),
        reason_code="GEO_CLOSEOUT_CAPTION_PLAN_STATE_MISSING",
    )
    caption_artifact_ref = caption_plan.get("artifact_ref")
    if caption_artifact_ref is not None:
        caption_artifact_ref = _required_text(
            caption_artifact_ref,
            reason_code="GEO_CLOSEOUT_CAPTION_ARTIFACT_REF_INVALID",
        )
    if (
        caption_plan_state == "WAITING_FOR_FINAL_AUDIO_ALIGNMENT"
        and caption_artifact_ref is not None
    ) or (caption_plan_state == "FINALIZED" and caption_artifact_ref is None):
        raise ValidationFailureError("GEO_CLOSEOUT_CAPTION_PLAN_AUTHORITY_MISMATCH")
    if caption_plan_state not in {
        "WAITING_FOR_FINAL_AUDIO_ALIGNMENT",
        "FINALIZED",
    }:
        raise ValidationFailureError("GEO_CLOSEOUT_CAPTION_PLAN_STATE_INVALID")
    metadata_checks = metadata_content.get("checks")
    if not isinstance(metadata_checks, dict):
        raise ValidationFailureError("GEO_CLOSEOUT_METADATA_CHECKS_MISSING")
    translated = metadata_checks.get("translated_sounding_copy")
    us_spelling = metadata_checks.get("us_spelling")
    us_search_wording = metadata_checks.get("us_search_wording")
    if not all(
        isinstance(value, bool)
        for value in (translated, us_spelling, us_search_wording)
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_METADATA_LOCALIZATION_TRUTH_MISSING")
    window = publish_handoff_content.get("approved_publish_window") or (
        publish_handoff_content.get("publish_window_hypothesis")
    )
    if not isinstance(window, dict) or not window:
        raise ValidationFailureError("GEO_CLOSEOUT_APPROVED_PUBLISH_WINDOW_MISSING")
    script_locale = _required_text(
        script_content.get("language"),
        reason_code="GEO_CLOSEOUT_SCRIPT_LOCALE_MISSING",
    )
    voice_locale = _required_text(
        voice_content.get("narration_locale"),
        reason_code="GEO_CLOSEOUT_VOICE_LOCALE_MISSING",
    )
    voice_language = _required_text(
        voice_content.get("content_language"),
        reason_code="GEO_CLOSEOUT_VOICE_LANGUAGE_MISSING",
    )
    pronunciation_policy = voice_content.get("pronunciation_policy")
    if not isinstance(pronunciation_policy, dict):
        raise ValidationFailureError("GEO_CLOSEOUT_VOICE_PRONUNCIATION_POLICY_MISSING")
    pronunciation_locale = _required_text(
        pronunciation_policy.get("locale"),
        reason_code="GEO_CLOSEOUT_VOICE_PRONUNCIATION_LOCALE_MISSING",
    )
    if pronunciation_locale != voice_locale:
        raise ValidationFailureError("GEO_CLOSEOUT_VOICE_PRONUNCIATION_LOCALE_MISMATCH")
    metadata_locale = _required_text(
        metadata_content.get("locale"),
        reason_code="GEO_CLOSEOUT_METADATA_LOCALE_MISSING",
    )
    metadata_language = _required_text(
        metadata_content.get("original_language"),
        reason_code="GEO_CLOSEOUT_METADATA_LANGUAGE_MISSING",
    )
    source_jurisdiction = _required_text(
        research_content.get("source_jurisdiction"),
        reason_code="GEO_CLOSEOUT_SOURCE_JURISDICTION_MISSING",
    )
    publish_timezone = _required_text(
        publish_handoff_content.get("approved_publish_timezone"),
        reason_code="GEO_CLOSEOUT_APPROVED_PUBLISH_TIMEZONE_MISSING",
    )
    if (
        _required_text(
            window.get("timezone"),
            reason_code="GEO_CLOSEOUT_PUBLISH_WINDOW_TIMEZONE_MISSING",
        )
        != publish_timezone
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_PUBLISH_WINDOW_TIMEZONE_MISMATCH")
    handoff_market = _required_text(
        publish_handoff_content.get("primary_market"),
        reason_code="GEO_CLOSEOUT_HANDOFF_MARKET_MISSING",
    )
    handoff_locale = _required_text(
        publish_handoff_content.get("primary_locale"),
        reason_code="GEO_CLOSEOUT_HANDOFF_LOCALE_MISSING",
    )
    handoff_language = _required_text(
        publish_handoff_content.get("original_language"),
        reason_code="GEO_CLOSEOUT_HANDOFF_LANGUAGE_MISSING",
    )
    if {creative_market, thumbnail_market, handoff_market} != {expected_market}:
        raise ValidationFailureError("GEO_CLOSEOUT_BOUND_ARTIFACT_MARKET_MISMATCH")
    if {
        creative_locale,
        thumbnail_locale,
        handoff_locale,
        caption_locale,
        direction_locale,
        metadata_locale,
    } != {expected_locale}:
        raise ValidationFailureError("GEO_CLOSEOUT_BOUND_ARTIFACT_LOCALE_MISMATCH")
    if creative_narration_locale != voice_locale:
        raise ValidationFailureError("GEO_CLOSEOUT_CREATIVE_VOICE_LOCALE_MISMATCH")
    if handoff_language not in {voice_language, metadata_language} or (
        voice_language != metadata_language
    ):
        # Raw mismatches remain in typed evidence when both artifacts agree on
        # a non-target language; cross-artifact disagreement fails even earlier.
        raise ValidationFailureError(
            "GEO_CLOSEOUT_BOUND_ARTIFACT_LANGUAGE_INCONSISTENT"
        )
    return {
        "script_locale": script_locale,
        "voice_locale": voice_locale,
        "voice_content_language": voice_language,
        "metadata_locale": metadata_locale,
        "metadata_original_language": metadata_language,
        "caption_locales": [caption_locale],
        "caption_plan_state": caption_plan_state,
        "caption_artifact_ref": caption_artifact_ref,
        "currency_contexts": [currency],
        "unit_system": _uniform_scene_value(scenes, "units_policy"),
        "date_format": _uniform_scene_value(scenes, "date_format"),
        "source_jurisdictions": [source_jurisdiction],
        "local_examples_present": (
            set(visual_geos) == {expected_market}
            and market_contexts == {expected_audience_market_context}
            and workplace_contexts == {expected_workplace_context}
        ),
        "visual_geos": visual_geos,
        "ui_locales": [direction_locale],
        "publish_timezone": publish_timezone,
        "approved_publish_window": window,
        "terminology_localized": us_spelling and us_search_wording,
        "translated_sounding_copy": translated,
    }


def _build_alignment_evidence(
    session: Session,
    *,
    snapshot: CompiledChannelPolicySnapshot,
    policy: ChannelScopedPolicy,
    package: ArtifactVersion,
    manifest: dict[str, Any],
    effective_market_policy_hash: str,
) -> tuple[MarketDeliveryEvidence, dict[str, str]]:
    market = policy.target_market_profile
    destination_policy = policy.destination_binding_policy
    assert market is not None
    assert destination_policy is not None
    destination = destination_policy.destination
    bindings = manifest.get("exact_bindings") or {}
    revised = manifest.get("revised_artifacts") or {}
    reused = manifest.get("reused_artifacts") or {}
    package_artifact = session.get(Artifact, package.artifact_id)
    if (
        package_artifact is None
        or package_artifact.artifact_type != "package_manifest"
        or package_artifact.current_version_id != package.id
        or package_artifact.status != "approved"
        or package.status not in ACTIVE_PACKAGE_COMPONENT_VERSION_STATUSES
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_SOURCE_PACKAGE_NOT_APPROVED")
    source_project_id = package_artifact.video_project_id
    historical_project_id, _, _ = _require_historical_source_project_lineage(
        session,
        snapshot=snapshot,
        source_project_id=source_project_id,
        bindings=bindings,
    )

    version_sources = {
        "creative_brief": (
            revised["creative_brief"],
            "creative_brief",
            source_project_id,
        ),
        "research_pack": (revised["research_pack"], "research_pack", source_project_id),
        "script": (reused["script"], "script", historical_project_id),
        "voice_policy": (revised["voice_policy"], "voice_policy", source_project_id),
        "visual_plan": (revised["visual_plan"], "visual_plan", source_project_id),
        "visual_direction_contract": (
            revised["visual_direction_contract"],
            "visual_direction_contract",
            source_project_id,
        ),
        "visual_source_decision_set": (
            revised["visual_source_decision_set"],
            "visual_source_decision_set",
            source_project_id,
        ),
        "publishing_metadata_package": (
            revised["publishing_metadata_package"],
            "publishing_metadata_package",
            source_project_id,
        ),
        "thumbnail_brief": (
            revised["thumbnail_brief"],
            "thumbnail_brief",
            source_project_id,
        ),
        "publish_handoff_package": (
            revised["publish_handoff_package"],
            "publish_handoff_package",
            source_project_id,
        ),
        "market_alignment_dossier": (
            revised["market_alignment_dossier"],
            "market_alignment_dossier",
            source_project_id,
        ),
        "target_market_consistency_check": (
            revised["target_market_consistency_check"],
            "target_market_consistency_check",
            source_project_id,
        ),
    }
    versions = {
        key: _require_version(
            session,
            binding=binding,
            expected_artifact_type=artifact_type,
            expected_project_id=expected_project_id,
        )
        for key, (
            binding,
            artifact_type,
            expected_project_id,
        ) in version_sources.items()
    }
    artifact_refs = {
        key: _artifact_version_ref(version) for key, version in versions.items()
    }
    consistency = versions["target_market_consistency_check"]
    publish_handoff = versions["publish_handoff_package"]
    consistency_content = consistency.content or {}
    _require_target_market_consistency_pass(consistency_content)
    profile_consistency_binding = consistency_content.get("target_market_profile")
    destination_consistency_binding = consistency_content.get("destination_binding")
    dossier_consistency_binding = consistency_content.get("market_alignment_dossier")
    expected_profile_binding = bindings.get("target_market_profile")
    expected_destination_binding = bindings.get("destination_binding")
    if (
        not isinstance(expected_profile_binding, dict)
        or not isinstance(expected_destination_binding, dict)
        or not isinstance(profile_consistency_binding, dict)
        or profile_consistency_binding.get("ref") != expected_profile_binding.get("ref")
        or profile_consistency_binding.get("content_hash") != market.content_hash
        or consistency_content.get("target_market_profile_hash") != market.content_hash
        or not isinstance(destination_consistency_binding, dict)
        or destination_consistency_binding.get("ref")
        != expected_destination_binding.get("ref")
        or destination_consistency_binding.get("content_hash")
        != destination.content_hash
        or consistency_content.get("destination_binding_hash")
        != destination.content_hash
        or not isinstance(dossier_consistency_binding, dict)
        or dossier_consistency_binding.get("artifact_version_ref")
        != artifact_refs["market_alignment_dossier"]
        or dossier_consistency_binding.get("content_hash")
        != versions["market_alignment_dossier"].content_hash
    ):
        raise ValidationFailureError("GEO_CLOSEOUT_MARKET_CONSISTENCY_BINDING_MISMATCH")
    actuals = _artifact_alignment_actuals(
        creative_content=versions["creative_brief"].content or {},
        script_content=versions["script"].content or {},
        voice_content=versions["voice_policy"].content or {},
        visual_content=versions["visual_plan"].content or {},
        visual_direction_content=(versions["visual_direction_contract"].content or {}),
        visual_decisions_content=(versions["visual_source_decision_set"].content or {}),
        research_content=versions["research_pack"].content or {},
        metadata_content=versions["publishing_metadata_package"].content or {},
        thumbnail_content=versions["thumbnail_brief"].content or {},
        publish_handoff_content=publish_handoff.content or {},
        expected_market=market.primary_market,
        expected_content_language=market.content_language,
        expected_locale=market.primary_locale,
        expected_audience_market_context=market.audience_market_context,
        expected_workplace_context=market.workplace_context,
    )
    destination_ref = (bindings.get("destination_binding") or {}).get("ref")
    if not destination_ref:
        raise ValidationFailureError("GEO_CLOSEOUT_DESTINATION_REF_MISSING")
    destination_runtime = destination_runtime_contract(
        destination, canonical_ref=destination_ref
    )
    dossier_binding = revised["market_alignment_dossier"]
    evidence = MarketDeliveryEvidence(
        policy_snapshot_id=snapshot.id,
        market_policy_hash=effective_market_policy_hash,
        target_market_profile_ref=bindings["target_market_profile"]["ref"],
        target_market_profile_hash=market.content_hash,
        market_alignment_dossier_ref=artifact_refs["market_alignment_dossier"],
        market_alignment_dossier_hash=dossier_binding["content_hash"],
        creative_brief_ref=artifact_refs["creative_brief"],
        research_pack_ref=artifact_refs["research_pack"],
        script_ref=artifact_refs["script"],
        voice_manifest_ref=artifact_refs["voice_policy"],
        visual_plan_ref=artifact_refs["visual_plan"],
        metadata_package_ref=artifact_refs["publishing_metadata_package"],
        caption_plan_ref=(artifact_refs["publish_handoff_package"] + "#caption-plan"),
        thumbnail_brief_ref=artifact_refs["thumbnail_brief"],
        publish_package_ref=_artifact_version_ref(package),
        destination_binding_id=destination_runtime.destination_binding_id,
        destination_binding_fingerprint=destination.content_hash,
        expected_market=market.primary_market,
        expected_content_language=market.content_language,
        expected_locale=market.primary_locale,
        expected_currency=market.currency,
        expected_unit_system=market.units_policy,
        expected_date_format=market.date_format,
        expected_timezone=market.primary_timezone,
        preferred_source_jurisdictions=market.preferred_source_jurisdictions,
        acceptable_visual_geos=[
            *market.primary_geo_cluster,
            *market.acceptable_secondary_geos,
        ],
        destination_market=destination.target_market,
        destination_status=destination.destination_status,
        **actuals,
    )
    return evidence, artifact_refs


def _write_reports(summary: dict[str, Any]) -> None:
    summary_path = REPORTS / "geo_market_delivery_closeout_summary.json"
    cycles_path = REPORTS / "geo_market_delivery_closeout_repair_cycles.json"
    report_path = REPORTS / "geo_market_delivery_closeout_report.md"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    cycles = {
        "schema_version": "geo-market-delivery-closeout-repair-cycles/v1",
        "final_state": summary["final_state"],
        "cycles": [
            {
                "cycle": 1,
                "failure_class": "SCHEMA_RUNTIME_GAP",
                "root_cause": (
                    "M7 stored destination/market lineage only in JSON and did not "
                    "enforce exact approval-to-actual destination invariants."
                ),
                "repair": (
                    "Added nullable first-class lineage columns, migration 0041, "
                    "strict fail-closed validation, and M7 propagation."
                ),
                "acceptance_results": {
                    gate: summary["acceptance"][gate]
                    for gate in (
                        "GEO_DELIVERY_CLOSEOUT_MARKET_LINEAGE",
                        "GEO_DELIVERY_CLOSEOUT_DESTINATION_ENFORCEMENT",
                    )
                },
            },
            {
                "cycle": 2,
                "failure_class": "DELIVERY_READ_MODEL_GAP",
                "root_cause": (
                    "No deterministic aggregate market delivery gate or maturity-aware "
                    "derived geo tracker existed."
                ),
                "repair": (
                    "Added MarketDeliveryAlignmentGate, GeoDistributionTracker, "
                    "maturity diagnostics, and M9 integration."
                ),
                "acceptance_results": {
                    gate: summary["acceptance"][gate]
                    for gate in (
                        "GEO_DISTRIBUTION_TRACKER",
                        "GEO_MATURITY_INTEGRATION",
                        "GEO_DIAGNOSTIC_RULES",
                    )
                },
            },
            {
                "cycle": 3,
                "failure_class": "POLICY_CONTRADICTION",
                "root_cause": (
                    "Immutable snapshot v3 still declares mixed/affiliate monetization."
                ),
                "repair": (
                    "Preserved snapshot v3 and persisted an exact base-hash-bound "
                    "PLATFORM_AD_REVENUE_ONLY overlay as submitted immutable evidence."
                ),
                "acceptance_results": {
                    "ADS_ONLY_MONETIZATION_POLICY": summary["acceptance"][
                        "ADS_ONLY_MONETIZATION_POLICY"
                    ]
                },
            },
            {
                "cycle": 4,
                "failure_class": "TEST_FIXTURE_HASH_LINEAGE",
                "root_cause": (
                    "A focused test changed channel_workspace_id via model_copy while "
                    "retaining the old content_hash."
                ),
                "repair": "Revalidated the fixture so its derived hash matched content.",
                "acceptance_results": {
                    "GEO_DELIVERY_CLOSEOUT_MARKET_ALIGNMENT": summary["acceptance"][
                        "GEO_DELIVERY_CLOSEOUT_MARKET_ALIGNMENT"
                    ]
                },
            },
        ],
        "verification": summary["verification"],
    }
    cycles_path.write_text(
        json.dumps(cycles, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    overlay = summary["artifacts"]["effective_ads_only_monetization_policy"]
    closeout = summary["artifacts"]["geo_market_delivery_closeout_evidence"]
    acceptance_rows = "\n".join(
        f"| `{gate}` | `{verdict}` |"
        for gate, verdict in sorted(summary["acceptance"].items())
    )
    verification_rows = "\n".join(
        "| `{run_id}` | `{command}` | `{exit_code}` | `{passed}` | "
        "`{failed}` | `{skipped}` | `{output_hash}` |".format(
            **{**item, "command": " ".join(item["command"])}
        )
        for item in summary["verification"]["runs"]
    )
    report = f"""# Geo/Market Delivery Closeout

Kết quả: **{summary["final_state"]}** ở mức implementation closeout. Destination production vẫn là
`PENDING_PLATFORM_ID`; vì vậy `UPLOAD_READY=false` và
`PUBLISH_EXECUTION_READY=false` được giữ nguyên.

| Binding | Giá trị |
|---|---|
| Dedicated project | `{summary["project"]["id"]}` / `{PROJECT_TYPE}` |
| Active profile v3 | `{PROFILE_ID}` |
| Base snapshot v3 | `{SNAPSHOT_ID}` / `{SNAPSHOT_HASH}` |
| Base monetization truth | `primary=mixed`, channels=`adsense, affiliate` (không sửa) |
| Effective ads-only overlay | `{overlay["artifact_version_id"]}` / `{overlay["content_hash"]}` |
| Effective market policy hash | `{summary["policy"]["effective_market_policy_hash"]}` |
| Closeout evidence | `{closeout["artifact_version_id"]}` / `{closeout["content_hash"]}` |
| Destination binding | `{summary["destination"]["destination_binding_id"]}` / `{summary["destination"]["binding_fingerprint"]}` |
| Destination status | `PENDING_PLATFORM_ID` |

Overlay chỉ tạo effective policy `PLATFORM_AD_REVENUE_ONLY` trên exact base hash;
không mutate `ChannelProfileVersion v3` hay `CompiledChannelPolicySnapshot v3`,
không giả lập channel ID/verification, và không cấp provider/render/publish authority.

## Acceptance

| Gate | Verdict |
|---|---|
{acceptance_rows}

## Machine verification manifest

- Manifest hash: `{summary["verification"]["manifest_content_hash"]}`
- Producer: `{summary["verification"]["producer"]}`
- Relevant-workspace hash: `{summary["verification"]["workspace_hash"]}`
- Repository revision authority: `{summary["verification"]["repository_revision"]}`

| Run | Command | Exit | Passed | Failed | Skipped | Output hash |
|---|---|---:|---:|---:|---:|---|
{verification_rows}
"""
    report_path.write_text(report, encoding="utf-8")


def main() -> int:
    ConfigRegistryService(None).validate_catalog(
        ROOT / "config" / "artifact_type_registry.yaml"
    )
    with session_scope() as session:
        ConfigRegistryService(session).seed(
            [ROOT / "config" / "artifact_type_registry.yaml"]
        )
        (
            channel,
            profile,
            snapshot,
            policy,
            package,
            approval,
            manifest,
        ) = _resolve_source(session)
        project = _ensure_project(
            session,
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            approval=approval,
            manifest=manifest,
        )
        base_monetization = _require_base_monetization_truth(
            snapshot.compiled_payload or {}
        )
        _overlay, effective_hash = (
            AdsOnlyMonetizationPolicyService().compile_effective_policy(
                base_policy_snapshot_id=snapshot.id,
                base_policy_snapshot_hash=snapshot.content_hash,
                overlay_authority_ref=OVERLAY_AUTHORITY_REF,
            )
        )
        evidence, artifact_refs = _build_alignment_evidence(
            session,
            snapshot=snapshot,
            policy=policy,
            package=package,
            manifest=manifest,
            effective_market_policy_hash=effective_hash,
        )
        alignment = MarketDeliveryAlignmentGate().evaluate(evidence)
        if alignment.verdict.value != "PASS":
            raise ValidationFailureError(
                "GEO_CLOSEOUT_MARKET_ALIGNMENT_BLOCKED:"
                + ",".join(reason.value for reason in alignment.reason_codes)
            )
        verification_receipt_path = Path(
            os.getenv(
                "VCOS_GEO_VERIFICATION_RECEIPT_PATH",
                str(DEFAULT_VERIFICATION_RECEIPT_PATH),
            )
        ).expanduser()
        (
            verification_receipt_artifact_version_id,
            verification_receipt_content_hash,
        ) = _load_verification_receipt_locator(
            verification_receipt_path,
        )
        destination_ref = (manifest["exact_bindings"]["destination_binding"])["ref"]
        destination = policy.destination_binding_policy.destination
        runtime = destination_runtime_contract(
            destination, canonical_ref=destination_ref
        )
        result = GeoDeliveryCloseoutArtifactService(session).ensure_closeout_artifacts(
            video_project_id=project.id,
            created_by_user_id=approval.decided_by_user_id,
            base_policy_snapshot_id=snapshot.id,
            base_policy_snapshot_hash=snapshot.content_hash,
            source_package_artifact_version_id=package.id,
            source_package_content_hash=package.content_hash,
            overlay_authority_ref=OVERLAY_AUTHORITY_REF,
            destination_runtime=runtime,
            market_alignment_evidence=evidence,
            market_alignment_result=alignment,
            verification_receipt_artifact_version_id=(
                verification_receipt_artifact_version_id
            ),
            verification_receipt_content_hash=(verification_receipt_content_hash),
        )
        policy_ref = result["effective_ads_only_policy"]
        closeout_ref = result["geo_closeout_evidence"]
        persisted_closeout = _read_persisted_closeout(
            session,
            project=project,
            artifact_version_id=closeout_ref.artifact_version_id,
            expected_hash=closeout_ref.content_hash,
        )
        persisted_manifest = persisted_closeout.verification_manifest
        persisted_acceptance = persisted_closeout.acceptance_verdicts.model_dump(
            mode="json"
        )
        final_state = (
            "PASS"
            if persisted_acceptance and set(persisted_acceptance.values()) == {"PASS"}
            else "BLOCK"
        )
        if final_state != "PASS":
            raise ValidationFailureError(
                "GEO_CLOSEOUT_PERSISTED_ACCEPTANCE_NOT_PASSING"
            )
        summary = {
            "schema_version": "geo-market-delivery-closeout-summary/v1",
            "date": persisted_manifest.generated_at.date().isoformat(),
            "channel_key": CHANNEL_KEY,
            "builder_version": BUILDER_VERSION,
            "final_state": final_state,
            "project": {"id": str(project.id), "project_type": project.project_type},
            "source": {
                "approved_package_artifact_version_id": str(PACKAGE_VERSION_ID),
                "approved_package_hash": PACKAGE_HASH,
                "approval_decision_id": str(APPROVAL_ID),
                "human_review_receipt_artifact_version_id": str(
                    HUMAN_RECEIPT_VERSION_ID
                ),
                "bound_artifact_refs": artifact_refs,
            },
            "base_snapshot": {
                "id": str(snapshot.id),
                "content_hash": snapshot.content_hash,
                "monetization_policy": base_monetization,
                "mutated": False,
            },
            "policy": {
                "overlay_authority_ref": OVERLAY_AUTHORITY_REF,
                "effective_market_policy_hash": result["effective_market_policy_hash"],
                "monetization_mode": "PLATFORM_AD_REVENUE_ONLY",
                "base_snapshot_monetization_contradiction": (
                    "REPAIRED_BY_IMMUTABLE_ADS_ONLY_OVERLAY"
                ),
            },
            "destination": {
                "destination_binding_id": str(runtime.destination_binding_id),
                "binding_fingerprint": runtime.binding_fingerprint,
                "platform": runtime.platform,
                "platform_channel_id": runtime.platform_channel_id,
                "status": runtime.status,
                "upload_ready": False,
                "publish_execution_ready": False,
            },
            "artifacts": {
                "effective_ads_only_monetization_policy": policy_ref.model_dump(
                    mode="json"
                ),
                "geo_market_delivery_closeout_evidence": closeout_ref.model_dump(
                    mode="json"
                ),
            },
            "acceptance": persisted_acceptance,
            "verification": {
                "receipt_locator_path": str(verification_receipt_path),
                "receipt_artifact_version_id": str(
                    verification_receipt_artifact_version_id
                ),
                "receipt_content_hash": verification_receipt_content_hash,
                "manifest_content_hash": persisted_manifest.content_hash,
                "producer": persisted_manifest.producer,
                "generated_at": persisted_manifest.generated_at.isoformat(),
                "workspace_hash": persisted_manifest.workspace_hash,
                "repository_revision": persisted_manifest.repository_revision,
                "runs": [
                    {
                        "run_id": item.run_id,
                        "command": item.command,
                        "exit_code": item.exit_code,
                        "passed": item.passed,
                        "failed": item.failed,
                        "skipped": item.skipped,
                        "output_hash": item.output_hash,
                        "verdict": item.verdict.value,
                        "content_hash": item.content_hash,
                    }
                    for item in persisted_manifest.verification_runs
                ],
            },
            "no_execution_proof": (
                persisted_closeout.no_execution_proof.model_dump(mode="json")
            ),
        }
        session.flush()
    _write_reports(summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
