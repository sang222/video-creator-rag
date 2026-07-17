from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.contracts import (
    ChannelProfileDraftUpdate,
    ChannelProfileInput,
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
)
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    CompiledChannelPolicySnapshot,
    FinalMediaRef,
    HumanUploadTask,
    PaidProviderCallLedger,
    ProviderJobSnapshot,
    UploadedVideo,
    User,
)
from app.main import create_app
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
APPROVAL_REF = "operator-approval://ch1-flex/small-team-ai/profile-v1"


def _external_execution_counts(session) -> dict[str, int]:
    return {
        "provider_jobs": session.scalar(select(func.count()).select_from(ProviderJobSnapshot)),
        "paid_calls": session.scalar(select(func.count()).select_from(PaidProviderCallLedger)),
        "final_media": session.scalar(select(func.count()).select_from(FinalMediaRef)),
        "upload_tasks": session.scalar(select(func.count()).select_from(HumanUploadTask)),
        "uploaded_videos": session.scalar(select(func.count()).select_from(UploadedVideo)),
    }


def _scope(session):
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(name="CH1 FLEX", slug="ch1-flex")
    operator = User(email="ch1-flex@example.com", display_name="CH1 operator", status="active")
    session.add(operator)
    session.flush()
    RBACService(session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
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
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage", created_by=operator.id),
    )
    contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile.id,
            created_by="ChannelAuthorityAgent",
        )
    )
    contract = FormatIdentityContractService(session).approve(contract.id, decided_by="human-operator")
    assert contract.content_hash == "8522fb38cdfe3ff6ae615d39b7d1c8ff2a6fb34a33363276bd3ebea98a320cbc"
    compiled = ChannelProfileCompiler(session).compile(
        profile_version_id=profile.id,
        correlation_id="ch1-flex-v1-compile",
    )
    snapshot = session.get(CompiledChannelPolicySnapshot, compiled.snapshot_id)
    return company, operator, channel, profile, contract, snapshot


def _approve_and_activate(session, profile, snapshot, operator):
    service = ChannelProfileService(session)
    service.approve_profile_version(
        profile_version_id=profile.id,
        approved_by=operator.id,
        approval_ref=APPROVAL_REF,
    )
    return service.activate_snapshot(snapshot_id=snapshot.id)


def test_v1_compiles_typed_channel_policy_and_activation_requires_approval(db_session) -> None:
    before = _external_execution_counts(db_session)
    _, operator, channel, profile, contract, snapshot = _scope(db_session)
    payload = snapshot.compiled_payload
    policy = ChannelScopedPolicy.model_validate(payload["channel_scoped_policy"])

    assert profile.version == 1
    assert policy.channel_key == "small-team-ai"
    assert policy.approval_ref == APPROVAL_REF
    assert policy.character_policy.mode == "NO_CHARACTER"
    assert policy.channel_visual_strategy_profile.native_explanatory_target_range.model_dump() == {
        "minimum": 0.5,
        "maximum": 0.7,
    }
    assert policy.channel_visual_strategy_profile.minimum_pexels_quota == 0
    assert policy.channel_visual_strategy_profile.minimum_veo_quota == 0
    assert policy.voice_policy.voice_id == "pNInz6obpgDQGcFmaJgB"
    assert policy.voice_policy.model_id == "eleven_multilingual_v2"
    assert policy.voice_policy.settings.speed == 0.9
    assert policy.provider_usage_policy.native_ffmpeg_final_render_authority is True
    assert policy.publish_policy.manual_upload_only is True
    assert policy.publish_policy.local_purge_after_archive_state == "ARCHIVE_VERIFIED"
    assert policy.format_identity_contract.content_hash == contract.content_hash
    assert payload["creative_quality_policy_snapshot"]["source_run_id"] == "pa1r-cqr1-20260716-paid-canary-009"
    assert payload["creative_quality_policy_snapshot"]["content_hash"] == payload["snapshot_refs"]["creative_quality_policy"]["content_hash"]
    assert payload["capability_evaluation"]["status"] == "PASS"
    assert payload["launch_restrictions"]["provider_execution_enabled"] is False

    with pytest.raises(ValidationFailureError, match="approved channel profile snapshot"):
        ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
    activated = _approve_and_activate(db_session, profile, snapshot, operator)
    assert activated.status == "active"
    assert channel.active_policy_snapshot_id == snapshot.id
    assert _external_execution_counts(db_session) == before


def test_draft_v2_isolated_deterministic_and_cannot_activate_without_approval(db_session) -> None:
    company, operator, channel, profile_v1, _, snapshot_v1 = _scope(db_session)
    _approve_and_activate(db_session, profile_v1, snapshot_v1, operator)
    v1_profile_payload = deepcopy(profile_v1.profile_input)
    v1_snapshot_payload = deepcopy(snapshot_v1.compiled_payload)
    project_v1 = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot_v1.id,
            title="Frozen v1 project",
            created_by_user_id=operator.id,
        )
    )
    frozen_refs = {
        "snapshot": project_v1.policy_snapshot_id,
        "profile": project_v1.channel_profile_version_id,
        "native": project_v1.native_render_policy_snapshot_hash,
        "creative": project_v1.creative_quality_policy_hash,
        "provider": project_v1.provider_usage_policy_hash,
    }

    service = ChannelProfileService(db_session)
    profile_v2 = service.create_draft_from_active(channel_id=channel.id, created_by=operator.id)
    draft_payload = deepcopy(profile_v2.profile_input)
    draft_payload["channel_policy"]["policy_version"] = "small-team-ai.channel-policy.v2-draft"
    draft_payload["channel_policy"]["channel_visual_strategy_profile"]["native_explanatory_target_range"]["maximum"] = 0.65
    updated = service.update_draft(
        profile_version_id=profile_v2.id,
        data=ChannelProfileDraftUpdate(
            profile_input=ChannelProfileInput.model_validate(draft_payload),
            expected_profile_input_hash=profile_v2.profile_input_hash,
        ),
    )
    preview_1 = service.preview_compile(updated.id)
    preview_2 = service.preview_compile(updated.id)
    assert preview_1["content_hash"] == preview_2["content_hash"]
    assert preview_1["persisted"] is False
    compiled_v2 = ChannelProfileCompiler(db_session).compile(
        profile_version_id=updated.id,
        correlation_id="ch1-flex-v2-draft",
    )
    snapshot_v2 = db_session.get(CompiledChannelPolicySnapshot, compiled_v2.snapshot_id)

    assert profile_v2.version == 2
    assert compiled_v2.content_hash != snapshot_v1.content_hash
    assert profile_v1.profile_input == v1_profile_payload
    assert snapshot_v1.compiled_payload == v1_snapshot_payload
    assert channel.active_policy_snapshot_id == snapshot_v1.id
    with pytest.raises(ValidationFailureError, match="approved channel profile snapshot"):
        service.activate_snapshot(snapshot_id=snapshot_v2.id)
    db_session.refresh(project_v1)
    assert {
        "snapshot": project_v1.policy_snapshot_id,
        "profile": project_v1.channel_profile_version_id,
        "native": project_v1.native_render_policy_snapshot_hash,
        "creative": project_v1.creative_quality_policy_hash,
        "provider": project_v1.provider_usage_policy_hash,
    } == frozen_refs
    diff = service.semantic_diff(profile_v2.id, profile_v1.id)
    assert diff["different"] is True
    assert any(item["path"].endswith("native_explanatory_target_range.maximum") for item in diff["changed_paths"])


def test_generic_second_channel_policy_uses_same_compiler_without_niche_branch(db_session) -> None:
    _, _, _, _, _, snapshot = _scope(db_session)
    compiler = ChannelProfileCompiler(db_session)
    first = ChannelScopedPolicy.model_validate(snapshot.compiled_payload["channel_scoped_policy"])
    raw = first.model_dump(mode="json")
    raw["channel_key"] = "synthetic-second-channel"
    raw["policy_version"] = "synthetic-second-channel.fixture.v1"
    raw["policy_status"] = "FIXTURE_ONLY"
    raw["approval_ref"] = "fixture://ch1-flex/generic-compiler-only"
    raw["channel_identity_policy"]["channel_key"] = "synthetic-second-channel"
    raw["channel_visual_strategy_profile"]["native_explanatory_target_range"] = {"minimum": 0.6, "maximum": 0.8}
    second = ChannelScopedPolicy.model_validate(raw)
    creative = deepcopy(snapshot.compiled_payload["creative_quality_policies"])
    result = compiler.compile_channel_policy_blocks(
        policy=second,
        creative_quality_policies=creative,
        profile_input_hash="1" * 64,
        channel_policy_catalog_ref="fixture://ch1-flex/generic-compiler-only",
        channel_policy_catalog_hash=content_hash(second.model_dump(mode="json")),
        format_contract_evidence={
            "status": "APPROVED",
            "content_hash": second.format_identity_contract.content_hash,
        },
    )
    assert result["channel_scoped_policy"]["channel_key"] == "synthetic-second-channel"
    assert result["snapshot_refs"]["native_render_policy"]["content_hash"] != snapshot.compiled_payload["snapshot_refs"]["native_render_policy"]["content_hash"] or content_hash(result) != snapshot.content_hash
    assert result["native_render_policy_snapshot"]["final_render_authority"] == "native_ffmpeg_renderer"
    source = (ROOT / "app/services/asset_request_compiler.py").read_text(encoding="utf-8")
    assert 'plan.channel_id != "small-team-ai"' not in source
    assert "if channel ==" not in (ROOT / "app/services/profile_compiler.py").read_text(encoding="utf-8")


def test_hard_policy_literals_block_weakening_and_operator_api_has_safe_workflow(db_session) -> None:
    _, _, channel, _, _, _ = _scope(db_session)
    raw = ConfigRegistryService(db_session).validate_catalog(ROOT / "config/channel_scoped_policy_catalog.yaml").content["items"][0]
    weakened = deepcopy(raw)
    weakened["gate_policy"]["hard_policy_cannot_be_weakened"] = False
    with pytest.raises(ValidationError):
        ChannelScopedPolicy.model_validate(weakened)

    paths = TestClient(create_app()).get("/openapi.json").json()["paths"]
    required = {
        f"/channels/{{channel_id}}/profile-management",
        f"/channels/{{channel_id}}/profile-versions/active",
        f"/channels/{{channel_id}}/profile-versions/draft-from-active",
        "/profile-versions/{profile_version_id}/draft",
        "/profile-versions/{profile_version_id}/validate",
        "/profile-versions/{profile_version_id}/preview-compile",
        "/profile-versions/{profile_version_id}/diff/{other_profile_version_id}",
        "/profile-versions/{profile_version_id}/submit-for-approval",
        "/profile-versions/{profile_version_id}/approve",
        "/profile-versions/{profile_version_id}/reject",
        "/policy-snapshots/{snapshot_id}/activate",
    }
    assert required <= set(paths)
    assert channel.key == "small-team-ai"
