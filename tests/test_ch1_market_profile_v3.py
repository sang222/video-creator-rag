from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.contracts import (
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
    DestinationBinding,
    TargetMarketProfile,
)
from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.ofv0 import FormatIdentityContractDraftRequest
from app.contracts.workflow import VideoProjectCreate
from app.db.models import (
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
    TargetMarketDigestCompiler,
    VideoProjectService,
)
from app.services.ofv0 import FormatIdentityContractService
from app.services.runtime_bootstrap import _automated_v4_channel_policy


ROOT = Path(__file__).resolve().parents[1]
V2_APPROVAL = "operator-approval://ch1-flex-v2/small-team-ai/master-prompt-2026-07-19"
V3_APPROVAL = "operator-approval://ch1-market-v3/small-team-ai/master-prompt-2026-07-19"


def _active_v2_scope(session):
    ConfigRegistryService(session).seed([ROOT / "config"])
    company = CompanyService(session).create_company(name="CH1 market v3", slug="ch1-market-v3")
    operator = User(email="ch1-market-v3@example.com", display_name="CH1 v3 operator", status="active")
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
    service = ChannelProfileService(session)
    profile_v1 = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage", created_by=operator.id),
    )
    contract = FormatIdentityContractService(session).draft(
        FormatIdentityContractDraftRequest(
            channel_id=channel.id,
            channel_profile_version_id=profile_v1.id,
            created_by="ChannelAuthorityAgent",
        )
    )
    FormatIdentityContractService(session).approve(contract.id, decided_by="human-operator")
    compiled_v1 = ChannelProfileCompiler(session).compile(
        profile_version_id=profile_v1.id,
        correlation_id="ch1-market-v1-compile",
    )
    service.approve_profile_version(
        profile_version_id=profile_v1.id,
        approved_by=operator.id,
        approval_ref="operator-approval://ch1-flex/small-team-ai/profile-v1",
    )
    service.activate_snapshot(snapshot_id=compiled_v1.snapshot_id)
    activated_v2 = service.approve_and_activate_ch1_flex_v2(
        channel_id=channel.id,
        approval_ref=V2_APPROVAL,
        approved_by=operator.id,
    )
    profile_v2 = service.get_profile_version(uuid.UUID(activated_v2["channel_profile_version_id"]))
    snapshot_v2 = session.get(CompiledChannelPolicySnapshot, uuid.UUID(activated_v2["compiled_policy_snapshot_id"]))
    return company, operator, channel, service, profile_v2, snapshot_v2


def _market_bindings(channel):
    profile = TargetMarketProfile(
        profile_version=1,
        channel_id=channel.id,
        channel_key=channel.key,
        primary_market="US",
        primary_geo_cluster=["US"],
        acceptable_secondary_geos=["CA", "GB", "AU"],
        primary_locale="en-US",
        content_language="en",
        narration_locale="en-US",
        primary_timezone="America/New_York",
        spelling_system="US",
        currency="USD",
        units_policy="US_WITH_METRIC_WHEN_RELEVANT",
        date_format="MMM D, YYYY",
        title_locale="en-US",
        thumbnail_text_locale="en-US",
        caption_locales=["en-US"],
        audience_market_context="US_SMALL_BUSINESS",
        workplace_context="US_SMALL_BUSINESS",
        source_jurisdiction_policy="TARGET_MARKET_FIRST_CONTEXTUAL_FOREIGN_ALLOWED",
        preferred_source_jurisdictions=["US"],
        foreign_source_context_required=True,
        allowed_market_contexts=["US", "CA", "GB", "AU"],
        prohibited_market_mismatches=[
            "TRANSLATED_SOUNDING_ENGLISH",
            "NON_US_CURRENCY_WITHOUT_USD_EQUIVALENT",
            "FOREIGN_LEGAL_ASSUMPTION_WITHOUT_CONTEXT",
            "WRONG_VOICE_LOCALE",
            "WRONG_METADATA_LOCALE",
            "WRONG_THUMBNAIL_LOCALE",
        ],
        initial_publish_window_hypotheses=[
            {"timezone": "America/New_York", "days": ["TUE", "THU"], "local_time": "10:00", "status": "HYPOTHESIS_ONLY"}
        ],
        minimum_comparable_videos=3,
        video_geo_evaluation_window_days=7,
        channel_geo_review_window_days=30,
        account_country=None,
        target_market="US",
        actual_viewer_geography_state="UNMEASURED",
        approval_ref=V3_APPROVAL,
    )
    digest = TargetMarketDigestCompiler().compile(profile)
    destination = DestinationBinding(
        binding_version=1,
        channel_id=channel.id,
        channel_key=channel.key,
        platform="YOUTUBE",
        channel_handle="@SmallTeamAI",
        account_country=None,
        target_market_profile_ref=digest.profile_ref,
        target_market_profile_hash=str(profile.content_hash),
        target_market="US",
        primary_market="US",
        primary_locale="en-US",
        original_language="en",
        default_visibility="PRIVATE",
        manual_publish_required=True,
        destination_status="PENDING_PLATFORM_ID",
        verification_state="PENDING",
        approval_ref=V3_APPROVAL,
    )
    return profile, digest, destination


def _external_counts(session):
    return {
        "provider_jobs": session.scalar(select(func.count()).select_from(ProviderJobSnapshot)),
        "paid_calls": session.scalar(select(func.count()).select_from(PaidProviderCallLedger)),
        "render_jobs": session.scalar(select(func.count()).select_from(MediaRenderJob)),
        "final_media": session.scalar(select(func.count()).select_from(FinalMediaRef)),
        "upload_tasks": session.scalar(select(func.count()).select_from(HumanUploadTask)),
        "uploaded_videos": session.scalar(select(func.count()).select_from(UploadedVideo)),
    }


def test_v3_policy_is_deterministic_and_preserves_every_v2_visual_rule(db_session) -> None:
    _company, _operator, channel, _service, profile_v2, snapshot_v2 = _active_v2_scope(db_session)
    profile, digest, destination = _market_bindings(channel)
    compiler = ChannelProfileCompiler(db_session)
    v2 = ChannelScopedPolicy.model_validate(snapshot_v2.compiled_payload["channel_scoped_policy"])
    first = compiler.build_ch1_market_v3_policy(
        active_policy=v2,
        target_market_profile=profile,
        target_market_digest=digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
    )
    second = compiler.build_ch1_market_v3_policy(
        active_policy=v2,
        target_market_profile=profile,
        target_market_digest=digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.policy_version == "small-team-ai.channel-policy.v3"
    assert first.visual_source_policy_binding == v2.visual_source_policy_binding
    assert first.provider_usage_policy == v2.provider_usage_policy
    assert first.channel_visual_strategy_profile == v2.channel_visual_strategy_profile
    assert first.media_production_profile == v2.media_production_profile
    assert first.target_market_profile.primary_market == "US"
    assert first.destination_binding_policy.destination.destination_status == "PENDING_PLATFORM_ID"
    assert first.destination_binding_policy.destination.account_country is None
    assert first.market_package_freeze_policy.frozen_state == "MARKET_PACKAGE_FROZEN"
    assert first.geo_evaluation_policy.minimum_comparable_videos == 3


def test_v3_activation_freezes_new_projects_preserves_old_projects_and_rolls_back_to_v2(db_session) -> None:
    before = _external_counts(db_session)
    company, operator, channel, service, profile_v2, snapshot_v2 = _active_v2_scope(db_session)
    v2_profile_payload = deepcopy(profile_v2.profile_input)
    v2_snapshot_payload = deepcopy(snapshot_v2.compiled_payload)
    old_project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot_v2.id,
            title="Historical v2 project",
            created_by_user_id=operator.id,
        )
    )
    old_lineage = (old_project.policy_snapshot_id, old_project.channel_profile_version_id, deepcopy(old_project.audience_delivery_summary))
    market_profile, digest, destination = _market_bindings(channel)
    result = service.approve_and_activate_ch1_market_v3(
        channel_id=channel.id,
        target_market_profile=market_profile,
        target_market_digest=digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
        approved_by=operator.id,
    )
    assert result["status"] == "PASS"
    assert result["channel_profile_version"] == 3
    assert result["destination_status"] == "PENDING_PLATFORM_ID"
    assert result["publish_execution_allowed"] is False
    assert result["diff"]["classification"]["removed"] == []
    assert result["rollback_pointer"]["channel_profile_version_id"] == str(profile_v2.id)
    assert all(result["receipts"].values())
    assert result["provider_calls"] == result["drive_calls"] == result["youtube_calls"] == 0

    snapshot_v3 = db_session.get(CompiledChannelPolicySnapshot, uuid.UUID(result["compiled_policy_snapshot_id"]))
    scoped = ChannelScopedPolicy.model_validate(snapshot_v3.compiled_payload["channel_scoped_policy"])
    assert scoped.market_alignment_policy.all_mandatory_market_gates_must_pass is True
    assert scoped.publish_policy.manual_upload_only is True
    assert scoped.publish_timing_localization_policy.localization_mode == "EN_US_MASTER_ONLY"
    assert scoped.geo_evaluation_policy.single_video_strategy_mutation_prohibited is True
    assert {"target_market_profile", "target_market_digest", "destination_binding", "market_alignment_policy", "market_package_freeze_policy", "geo_evaluation_policy"} <= set(snapshot_v3.compiled_payload["snapshot_refs"])

    new_project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot_v3.id,
            title="New v3 project",
            created_by_user_id=operator.id,
        )
    )
    frozen = new_project.audience_delivery_summary["target_market_freeze"]
    assert new_project.channel_profile_version_id == snapshot_v3.channel_profile_version_id
    assert frozen["target_market_profile_hash"] == market_profile.content_hash
    assert frozen["primary_market"] == "US"
    assert frozen["primary_locale"] == "en-US"

    assert profile_v2.profile_input == v2_profile_payload
    assert snapshot_v2.compiled_payload == v2_snapshot_payload
    assert (old_project.policy_snapshot_id, old_project.channel_profile_version_id, old_project.audience_delivery_summary) == old_lineage
    service.activate_snapshot(snapshot_id=snapshot_v2.id, correlation_id="ch1-market-v3-rollback-test")
    assert channel.active_policy_snapshot_id == snapshot_v2.id
    assert service.get_active_profile_version(channel.id).id == profile_v2.id
    assert _external_counts(db_session) == before


def test_v3_contract_blocks_missing_market_binding_wrong_locale_and_auto_publish(db_session) -> None:
    _company, _operator, channel, _service, _profile_v2, snapshot_v2 = _active_v2_scope(db_session)
    profile, digest, destination = _market_bindings(channel)
    policy = ChannelProfileCompiler(db_session).build_ch1_market_v3_policy(
        active_policy=snapshot_v2.compiled_payload["channel_scoped_policy"],
        target_market_profile=profile,
        target_market_digest=digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
    )
    missing = policy.model_dump(mode="json")
    missing.pop("target_market_profile")
    with pytest.raises(ValueError, match="POLICY_BINDING_INCOMPLETE"):
        ChannelScopedPolicy.model_validate(missing)

    wrong_locale = profile.model_copy(update={"primary_locale": "en-GB", "content_hash": None})
    with pytest.raises(Exception, match="PROFILE_VALUE_MISMATCH:primary_locale"):
        ChannelProfileCompiler(db_session).build_ch1_market_v3_policy(
            active_policy=snapshot_v2.compiled_payload["channel_scoped_policy"],
            target_market_profile=wrong_locale,
            target_market_digest=digest,
            destination_binding=destination,
            approval_ref=V3_APPROVAL,
        )

    auto_publish = policy.model_dump(mode="json")
    auto_publish["publish_policy"]["manual_upload_only"] = False
    with pytest.raises(ValueError):
        ChannelScopedPolicy.model_validate(auto_publish)


def test_runtime_successor_v4_removes_only_pre_render_human_gates(db_session) -> None:
    _company, _operator, channel, _service, _profile_v2, snapshot_v2 = _active_v2_scope(
        db_session
    )
    profile, digest, destination = _market_bindings(channel)
    v3 = ChannelProfileCompiler(db_session).build_ch1_market_v3_policy(
        active_policy=snapshot_v2.compiled_payload["channel_scoped_policy"],
        target_market_profile=profile,
        target_market_digest=digest,
        destination_binding=destination,
        approval_ref=V3_APPROVAL,
    )

    raw_v4 = _automated_v4_channel_policy(v3.model_dump(mode="json"))
    assert raw_v4 is not None
    v4 = ChannelScopedPolicy.model_validate(raw_v4)

    assert v4.policy_version == "small-team-ai.channel-policy.v4"
    assert v4.publish_policy.human_final_approval_required is True
    assert v4.voice_policy.retry_requires_new_approval is False
    assert v4.voice_policy.unavailable_behavior == "BLOCK_EXTERNAL_FAILURE"
    assert v4.provider_usage_policy.elevenlabs.controlled_retry_requires_new_approval is False
    assert v4.budget_policy.cost_overrun_review_required is False
    assert v4.market_package_freeze_policy.exact_package_human_approval_required is False
    assert v4.market_package_freeze_policy.post_approval_integrity_required is False
    assert all(
        "human" not in item.lower() and "approval" not in item.lower()
        for item in v4.market_package_freeze_policy.required_preconditions
    )
