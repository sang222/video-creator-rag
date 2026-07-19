from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.contracts import (
    ChannelProfileDraftUpdate,
    ChannelProfileInput,
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
)
from app.contracts.channel_policy import ChannelScopedPolicy, GeminiImageUsagePolicy
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.visual_routing import NicheVisualSourceProfile, VisualSourceRoute
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    AuditEvent,
    CompiledChannelPolicySnapshot,
    FinalMediaRef,
    HumanUploadTask,
    MediaRenderJob,
    PaidProviderCallLedger,
    ProviderJobSnapshot,
    UploadedVideo,
    User,
)
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    RBACService,
    VideoProjectService,
)
from app.services.config_registry import content_hash
from app.services.ofv0 import FormatIdentityContractService


ROOT = Path(__file__).resolve().parents[1]
APPROVAL_REF = (
    "operator-approval://ch1-flex-v2/small-team-ai/master-prompt-2026-07-19"
)


def _v1_policy() -> ChannelScopedPolicy:
    raw = ConfigRegistryService(None).validate_catalog(
        ROOT / "config" / "channel_scoped_policy_catalog.yaml"
    ).content["items"][0]
    return ChannelScopedPolicy.model_validate(raw)


def _compile_blocks(compiler: ChannelProfileCompiler, policy: ChannelScopedPolicy) -> dict:
    return compiler.compile_channel_policy_blocks(
        policy=policy,
        creative_quality_policies=compiler._creative_quality_policies(  # noqa: SLF001
            channel_key="small-team-ai"
        ),
        profile_input_hash="1" * 64,
        channel_policy_catalog_ref=(
            f"profile-input://small-team-ai/{policy.policy_version}"
        ),
        channel_policy_catalog_hash=content_hash(policy.model_dump(mode="json")),
        format_contract_evidence={
            "status": "APPROVED",
            "content_hash": policy.format_identity_contract.content_hash,
        },
    )


def _external_execution_counts(session) -> dict[str, int]:
    return {
        "provider_jobs": session.scalar(select(func.count()).select_from(ProviderJobSnapshot)),
        "paid_calls": session.scalar(select(func.count()).select_from(PaidProviderCallLedger)),
        "render_jobs": session.scalar(select(func.count()).select_from(MediaRenderJob)),
        "final_media": session.scalar(select(func.count()).select_from(FinalMediaRef)),
        "upload_tasks": session.scalar(select(func.count()).select_from(HumanUploadTask)),
        "uploaded_videos": session.scalar(select(func.count()).select_from(UploadedVideo)),
    }


def _scope(session):
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(
        name="CH1 FLEX v2",
        slug="ch1-flex-v2",
    )
    operator = User(
        email="ch1-flex-v2@example.com",
        display_name="CH1 v2 operator",
        status="active",
    )
    session.add(operator)
    session.flush()
    RBACService(session).assign_role(
        user_id=operator.id,
        role_key="operator",
        company_id=company.id,
    )
    channel = ChannelWorkspaceService(session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key="small-team-ai",
            name="Small Team AI",
            primary_language="en",
            primary_region="US",
            target_market="US",
        ),
    )
    service = ChannelProfileService(session)
    profile_v1 = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(
            template_key="saas_digital_leverage",
            created_by=operator.id,
        ),
    )
    contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile_v1.id,
            created_by="ChannelAuthorityAgent",
        )
    )
    FormatIdentityContractService(session).approve(
        contract.id,
        decided_by="human-operator",
    )
    compiled_v1 = ChannelProfileCompiler(session).compile(
        profile_version_id=profile_v1.id,
        correlation_id="ch1-flex-v1-compile",
    )
    snapshot_v1 = session.get(CompiledChannelPolicySnapshot, compiled_v1.snapshot_id)
    service.approve_profile_version(
        profile_version_id=profile_v1.id,
        approved_by=operator.id,
        approval_ref="operator-approval://ch1-flex/small-team-ai/profile-v1",
    )
    service.activate_snapshot(snapshot_id=snapshot_v1.id)
    return company, operator, channel, profile_v1, snapshot_v1


def test_v2_overlay_is_exact_deterministic_and_v1_serialization_is_unchanged() -> None:
    compiler = ChannelProfileCompiler(None)
    v1 = _v1_policy()
    v1_dump = v1.model_dump(mode="json")
    assert "visual_source_policy_binding" not in v1_dump
    assert "google_gemini_image" not in v1_dump["provider_usage_policy"]

    first = compiler.build_ch1_flex_v2_policy(
        active_policy=v1,
        approval_ref=APPROVAL_REF,
    )
    second = compiler.build_ch1_flex_v2_policy(
        active_policy=v1,
        approval_ref=APPROVAL_REF,
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.policy_version == "small-team-ai.channel-policy.v2"
    assert first.gate_policy.channel_fit_threshold is None

    visual = first.visual_source_policy_binding
    image = first.provider_usage_policy.google_gemini_image
    assert visual is not None
    assert image == GeminiImageUsagePolicy()
    assert visual.niche_visual_source_profile == NicheVisualSourceProfile.STOCK_ASSISTED
    assert set(visual.allowed_source_routes) == set(VisualSourceRoute)
    assert visual.one_source_decision_per_scene is True
    assert visual.auto_pexels_to_ai_failover is False
    assert visual.final_composition_authority == "NativeFFmpegRenderer"
    assert visual.exact_text_authority == "native_only"
    assert visual.exact_number_authority == "native_only"
    assert visual.generated_evidence_authority is False
    assert visual.minimum_effective_output_resolution == "1080p"
    assert visual.resolution_downgrade_below_1080p == "BLOCK"
    assert visual.human_final_visual_approval_required is True
    assert visual.archive_verification_required is True
    assert first.provider_usage_policy.pexels.role == "SUPPORTING_ONLY"
    assert first.provider_usage_policy.pexels.factual_evidence_allowed is False
    assert image.maximum_outputs_per_request == 1
    assert image.maximum_automated_attempts_per_scene == 1
    assert image.provider_fallback_allowed is False
    assert image.provider_execution_enabled_by_default is False
    assert image.native_overlay_required_when_exact_content_exists is True
    with pytest.raises(
        ValidationFailureError,
        match="exact scoped operator approval",
    ):
        compiler.build_ch1_flex_v2_policy(
            active_policy=v1,
            approval_ref=(
                "operator-approval://ch1-flex-v2/"
                "small-team-ai/self-invented-suffix"
            ),
        )

    first_blocks = _compile_blocks(compiler, first)
    second_blocks = _compile_blocks(compiler, second)
    assert content_hash(first_blocks) == content_hash(second_blocks)
    assert first_blocks["capability_evaluation"]["status"] == "PASS"
    assert first_blocks["launch_restrictions"]["provider_execution_enabled"] is False
    assert {
        "visual_source_routing_policy",
        "visual_source_routing_catalog",
        "gemini_image_provider_registry",
        "gemini_image_model_catalog",
        "image_visual_quality_control",
        "image_canary_v3_qualification",
        "drive_verified_canary_receipt",
    } <= set(first_blocks["snapshot_refs"])


def test_compiler_rejects_tampered_qualification_binding() -> None:
    compiler = ChannelProfileCompiler(None)
    policy = compiler.build_ch1_flex_v2_policy(
        active_policy=_v1_policy(),
        approval_ref=APPROVAL_REF,
    )
    raw = policy.model_dump(mode="json")
    raw["visual_source_policy_binding"]["image_canary_v3_qualification"][
        "content_hash"
    ] = "0" * 64
    tampered = ChannelScopedPolicy.model_validate(raw)
    with pytest.raises(
        ValidationFailureError,
        match="CH1_FLEX_V2_VISUAL_QUALIFICATION_BINDING_MISMATCH",
    ):
        _compile_blocks(compiler, tampered)


def test_reuses_mutable_v2_activates_future_work_and_preserves_v1(db_session) -> None:
    before = _external_execution_counts(db_session)
    company, operator, channel, profile_v1, snapshot_v1 = _scope(db_session)
    service = ChannelProfileService(db_session)
    v1_profile_payload = deepcopy(profile_v1.profile_input)
    v1_snapshot_payload = deepcopy(snapshot_v1.compiled_payload)
    v1_snapshot_hash = snapshot_v1.content_hash

    historical_project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot_v1.id,
            title="Frozen CH1 v1 project",
            created_by_user_id=operator.id,
        )
    )
    historical_refs = {
        "snapshot": historical_project.policy_snapshot_id,
        "profile": historical_project.channel_profile_version_id,
        "provider": historical_project.provider_usage_policy_hash,
        "budget": historical_project.budget_policy_hash,
    }

    unrelated_v2 = service.create_draft_from_active(
        channel_id=channel.id,
        created_by=operator.id,
    )
    unrelated_raw = deepcopy(unrelated_v2.profile_input)
    unrelated_raw["channel_policy"]["policy_version"] = (
        "small-team-ai.channel-policy.v2-draft"
    )
    unrelated_raw["channel_policy"]["channel_visual_strategy_profile"][
        "native_explanatory_target_range"
    ]["maximum"] = 0.65
    service.update_draft(
        profile_version_id=unrelated_v2.id,
        data=ChannelProfileDraftUpdate(
            profile_input=ChannelProfileInput.model_validate(unrelated_raw),
            expected_profile_input_hash=unrelated_v2.profile_input_hash,
        ),
    )

    result = service.approve_and_activate_ch1_flex_v2(
        channel_id=channel.id,
        approval_ref=APPROVAL_REF,
        approved_by=operator.id,
    )
    assert result["status"] == "PASS"
    assert result["provider_calls"] == 0
    assert result["channel_profile_version_id"] == str(unrelated_v2.id)
    assert result["channel_profile_version"] == 2
    assert result["rollback_pointer"]["channel_profile_version_id"] == str(
        profile_v1.id
    )
    assert result["rollback_pointer"]["compiled_policy_snapshot_id"] == str(
        snapshot_v1.id
    )
    assert all(result["receipts"].values())

    profile_v2 = service.get_profile_version(unrelated_v2.id)
    snapshot_v2 = db_session.get(
        CompiledChannelPolicySnapshot,
        uuid.UUID(result["compiled_policy_snapshot_id"]),
    )
    assert profile_v2.status == "active"
    assert snapshot_v2.status == "active"
    assert channel.active_policy_snapshot_id == snapshot_v2.id
    scoped = ChannelScopedPolicy.model_validate(
        snapshot_v2.compiled_payload["channel_scoped_policy"]
    )
    assert scoped.visual_source_policy_binding.niche_visual_source_profile == (
        NicheVisualSourceProfile.STOCK_ASSISTED
    )
    assert scoped.provider_usage_policy.google_gemini_image.provider_fallback_allowed is False
    compiled_gate_policy = snapshot_v2.compiled_payload["gate_policy"]
    assert compiled_gate_policy["channel_fit_threshold"] == (
        scoped.provider_usage_policy.pexels.semantic_fit_threshold
    )
    threshold_authority = compiled_gate_policy["channel_fit_threshold_authority"]
    provider_ref = snapshot_v2.compiled_payload["snapshot_refs"][
        "provider_usage_policy"
    ]
    assert threshold_authority == {
        "ref": provider_ref["ref"] + "#pexels.semantic_fit_threshold",
        "version": provider_ref["version"],
        "content_hash": provider_ref["content_hash"],
        "derivation": "REUSE_APPROVED_SEMANTIC_FIT_THRESHOLD",
    }
    assert snapshot_v2.compiled_payload["launch_restrictions"][
        "provider_execution_enabled"
    ] is False

    activation_receipt = db_session.get(
        AuditEvent,
        uuid.UUID(result["receipts"]["activation_audit_id"]),
    )
    diff_receipt = db_session.get(
        AuditEvent,
        uuid.UUID(result["receipts"]["profile_diff_audit_id"]),
    )
    assert diff_receipt.payload["status"] == "PASS"
    changed_paths = {
        item["path"] for item in diff_receipt.payload["changed_paths"]
    }
    assert changed_paths <= set(diff_receipt.payload["allowed_diff_paths"])
    assert {
        "$.channel_policy.policy_version",
        "$.channel_policy.approval_ref",
        "$.channel_policy.visual_source_policy_binding",
        "$.channel_policy.provider_usage_policy.google_gemini_image",
    } <= changed_paths
    assert activation_receipt.payload["rollback_snapshot_id"] == str(snapshot_v1.id)
    assert activation_receipt.payload["rollback_profile_version_id"] == str(
        profile_v1.id
    )

    deterministic = ChannelProfileCompiler(db_session).compile(
        profile_version_id=profile_v2.id,
        correlation_id="ch1-flex-v2-determinism",
    )
    assert deterministic.snapshot_id == snapshot_v2.id
    assert deterministic.content_hash == snapshot_v2.content_hash

    future_project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=channel.active_policy_snapshot_id,
            title="Future CH1 v2 project",
            created_by_user_id=operator.id,
        )
    )
    assert future_project.channel_profile_version_id == profile_v2.id
    assert future_project.policy_snapshot_id == snapshot_v2.id

    db_session.refresh(historical_project)
    assert {
        "snapshot": historical_project.policy_snapshot_id,
        "profile": historical_project.channel_profile_version_id,
        "provider": historical_project.provider_usage_policy_hash,
        "budget": historical_project.budget_policy_hash,
    } == historical_refs
    assert profile_v1.profile_input == v1_profile_payload
    assert snapshot_v1.compiled_payload == v1_snapshot_payload
    assert snapshot_v1.content_hash == v1_snapshot_hash
    assert _external_execution_counts(db_session) == before
