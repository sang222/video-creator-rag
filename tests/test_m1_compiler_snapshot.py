import copy

import pytest
from sqlalchemy import func, select

from app.contracts import ChannelProfileInput, ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    ChannelProfileCompileRun,
    CompiledChannelPolicySnapshot,
    LLMRunSnapshot,
)
from app.services import (
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
)


REQUIRED_SECTIONS = {
    "channel_constitution",
    "operating_blueprint",
    "content_pillars",
    "series_plan",
    "editorial_calendar_defaults",
    "initial_content_runway",
    "default_playbook",
    "render_policy",
    "gate_policy",
    "voice_policy",
    "evidence_policy",
    "monetization_policy",
    "kpi_profile",
    "editorial_promise",
    "distinctiveness_profile",
    "format_bible",
    "capability_status",
}


def _channel(db_session):
    company = CompanyService(db_session).create_company(name="Acme")
    return ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="main", name="Main"),
    )


def _profile(db_session):
    channel = _channel(db_session)
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    return channel, profile


def test_create_profile_version_from_template_works(db_session) -> None:
    _, profile = _profile(db_session)
    assert profile.source_template_key == "saas_digital_leverage"
    assert profile.profile_input_hash


def test_compile_creates_run_and_snapshot(db_session) -> None:
    _, profile = _profile(db_session)
    result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=profile.id,
        correlation_id="corr-compile",
    )
    run = db_session.get(ChannelProfileCompileRun, result.compile_run_id)
    snapshot = db_session.get(CompiledChannelPolicySnapshot, result.snapshot_id)
    assert run.status == "succeeded"
    assert snapshot.content_hash == result.content_hash


def test_same_profile_version_compile_is_snapshot_idempotent(db_session) -> None:
    _, profile = _profile(db_session)
    compiler = ChannelProfileCompiler(db_session)
    first = compiler.compile(
        profile_version_id=profile.id,
        correlation_id="corr-idempotent-1",
    )
    second = compiler.compile(
        profile_version_id=profile.id,
        correlation_id="corr-idempotent-2",
    )
    count = db_session.scalar(
        select(func.count())
        .select_from(CompiledChannelPolicySnapshot)
        .where(CompiledChannelPolicySnapshot.channel_profile_version_id == profile.id)
    )
    assert second.snapshot_id == first.snapshot_id
    assert second.content_hash == first.content_hash
    assert count == 1


def test_two_channels_same_template_get_distinct_snapshots_with_same_hash(db_session) -> None:
    first_channel, first_profile = _profile(db_session)
    second_channel, second_profile = _profile(db_session)
    compiler = ChannelProfileCompiler(db_session)
    first = compiler.compile(
        profile_version_id=first_profile.id,
        correlation_id="corr-channel-a",
    )
    second = compiler.compile(
        profile_version_id=second_profile.id,
        correlation_id="corr-channel-b",
    )
    first_snapshot = db_session.get(CompiledChannelPolicySnapshot, first.snapshot_id)
    second_snapshot = db_session.get(CompiledChannelPolicySnapshot, second.snapshot_id)
    assert first_channel.id != second_channel.id
    assert first.snapshot_id != second.snapshot_id
    assert first.content_hash == second.content_hash
    assert first_snapshot.channel_workspace_id == first_channel.id
    assert second_snapshot.channel_workspace_id == second_channel.id


def test_same_channel_two_identical_profile_versions_get_distinct_snapshots(db_session) -> None:
    channel = _channel(db_session)
    service = ChannelProfileService(db_session)
    first_profile = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    second_profile = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiler = ChannelProfileCompiler(db_session)
    first = compiler.compile(
        profile_version_id=first_profile.id,
        correlation_id="corr-same-channel-a",
    )
    second = compiler.compile(
        profile_version_id=second_profile.id,
        correlation_id="corr-same-channel-b",
    )
    first_snapshot = db_session.get(CompiledChannelPolicySnapshot, first.snapshot_id)
    second_snapshot = db_session.get(CompiledChannelPolicySnapshot, second.snapshot_id)
    assert first.snapshot_id != second.snapshot_id
    assert first.content_hash == second.content_hash
    assert first_snapshot.channel_profile_version_id == first_profile.id
    assert second_snapshot.channel_profile_version_id == second_profile.id


def test_global_content_hash_collision_across_channels_is_allowed(db_session) -> None:
    _, first_profile = _profile(db_session)
    _, second_profile = _profile(db_session)
    compiler = ChannelProfileCompiler(db_session)
    first = compiler.compile(profile_version_id=first_profile.id, correlation_id="corr-hash-a")
    second = compiler.compile(profile_version_id=second_profile.id, correlation_id="corr-hash-b")
    matching_hash_rows = db_session.scalar(
        select(func.count())
        .select_from(CompiledChannelPolicySnapshot)
        .where(CompiledChannelPolicySnapshot.content_hash == first.content_hash)
    )
    assert first.content_hash == second.content_hash
    assert matching_hash_rows == 2


def test_compile_is_deterministic_same_input_catalog_same_hash(db_session) -> None:
    compiler = ChannelProfileCompiler(db_session)
    profile_input, catalogs = compiler.profile_input_from_template("saas_digital_leverage")
    first_payload, first_hash = compiler.compile_from_input(
        profile_input=profile_input,
        template=catalogs.template,
        capability_matrix=catalogs.capability_matrix,
        compiler_policy=catalogs.compiler_policy,
    )
    second_payload, second_hash = compiler.compile_from_input(
        profile_input=profile_input,
        template=catalogs.template,
        capability_matrix=catalogs.capability_matrix,
        compiler_policy=catalogs.compiler_policy,
    )
    assert first_hash == second_hash
    assert first_payload == second_payload


def test_profile_input_hash_is_stable(db_session) -> None:
    channel = _channel(db_session)
    service = ChannelProfileService(db_session)
    first = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    second = service.create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    assert first.profile_input_hash == second.profile_input_hash


def test_compiled_payload_has_required_sections_and_capability_gaps(db_session) -> None:
    _, profile = _profile(db_session)
    result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=profile.id,
        correlation_id="corr-sections",
    )
    snapshot = db_session.get(CompiledChannelPolicySnapshot, result.snapshot_id)
    assert REQUIRED_SECTIONS <= set(snapshot.compiled_payload)
    capability_status = snapshot.compiled_payload["capability_status"]
    assert capability_status["media_pipeline"] == "restricted_until_milestone"
    assert capability_status["publish_pipeline"] == "restricted_until_milestone"


def test_unsupported_template_fails(db_session) -> None:
    with pytest.raises(ValidationFailureError):
        ChannelProfileCompiler(db_session).profile_input_from_template("unsupported_template")


def test_compiler_creates_no_llm_run_snapshot(db_session) -> None:
    _, profile = _profile(db_session)
    ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="corr-no-llm")
    count = db_session.scalar(select(func.count()).select_from(LLMRunSnapshot))
    assert count == 0


def test_approving_profile_requires_compiled_snapshot(db_session) -> None:
    _, profile = _profile(db_session)
    with pytest.raises(ValidationFailureError):
        ChannelProfileService(db_session).approve_profile_version(profile_version_id=profile.id)


def test_activating_snapshot_sets_channel_active_policy_snapshot_id(db_session) -> None:
    channel, profile = _profile(db_session)
    result = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="corr-active")
    snapshot = ChannelProfileService(db_session).activate_snapshot(snapshot_id=result.snapshot_id)
    db_session.refresh(channel)
    assert channel.active_policy_snapshot_id == snapshot.id


def test_activation_does_not_mutate_compiled_payload(db_session) -> None:
    _, profile = _profile(db_session)
    result = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="corr-immutable")
    snapshot = db_session.get(CompiledChannelPolicySnapshot, result.snapshot_id)
    before_payload = copy.deepcopy(snapshot.compiled_payload)
    before_hash = snapshot.content_hash
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=snapshot.id)
    db_session.refresh(snapshot)
    assert snapshot.compiled_payload == before_payload
    assert snapshot.content_hash == before_hash
    assert not hasattr(ChannelProfileService(db_session), "update_snapshot_payload")


def test_changing_profile_after_compile_creates_new_version_and_snapshot(db_session) -> None:
    channel, first_profile = _profile(db_session)
    first_result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=first_profile.id,
        correlation_id="corr-first",
    )
    first_snapshot = db_session.get(CompiledChannelPolicySnapshot, first_result.snapshot_id)
    first_payload = copy.deepcopy(first_snapshot.compiled_payload)
    changed_input = ChannelProfileInput.model_validate(first_profile.profile_input).model_copy(
        update={"display_name": "Changed Profile"}
    )
    second_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=changed_input),
    )
    second_result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=second_profile.id,
        correlation_id="corr-second",
    )
    second_snapshot = db_session.get(CompiledChannelPolicySnapshot, second_result.snapshot_id)
    assert second_profile.version == first_profile.version + 1
    assert second_snapshot.snapshot_version == first_snapshot.snapshot_version + 1
    assert second_snapshot.content_hash != first_snapshot.content_hash
    assert first_snapshot.compiled_payload == first_payload


def test_activating_new_snapshot_does_not_mutate_old_snapshot(db_session) -> None:
    channel, first_profile = _profile(db_session)
    first_result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=first_profile.id,
        correlation_id="corr-old",
    )
    first_snapshot = db_session.get(CompiledChannelPolicySnapshot, first_result.snapshot_id)
    first_payload = copy.deepcopy(first_snapshot.compiled_payload)
    changed_input = ChannelProfileInput.model_validate(first_profile.profile_input).model_copy(
        update={"display_name": "New Snapshot Profile"}
    )
    second_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=changed_input),
    )
    second_result = ChannelProfileCompiler(db_session).compile(
        profile_version_id=second_profile.id,
        correlation_id="corr-new",
    )
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=first_snapshot.id)
    ChannelProfileService(db_session).activate_snapshot(snapshot_id=second_result.snapshot_id)
    db_session.refresh(first_snapshot)
    assert first_snapshot.compiled_payload == first_payload
    db_session.refresh(channel)
    assert channel.active_policy_snapshot_id == second_result.snapshot_id
