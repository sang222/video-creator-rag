from __future__ import annotations

import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.profile import ChannelProfileInput
from app.contracts.vcos_qualification import NativeQualificationRenderRequest
from app.contracts.vcos_v2 import DurationContractV2, ProductionLane
from app.db.models import User
from app.services.channel_profile import ChannelProfileService
from app.services.channel_workspace import ChannelWorkspaceService
from app.services.company import CompanyService
from app.services.config_registry import ConfigRegistryService
from app.services.production_package import ChannelDurationContractResolver
from app.services.profile_compiler import ChannelProfileCompiler
from app.services.vcos_qualification import NativeQualificationService


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("lane", "width", "height"),
    [
        (ProductionLane.LONG_FORM, 1920, 1080),
        (ProductionLane.DAILY_SHORT, 1080, 1920),
    ],
)
def test_real_native_ffmpeg_qualification_has_h264_aac_streams(
    tmp_path,
    lane: ProductionLane,
    width: int,
    height: int,
) -> None:
    profile_id = uuid.uuid4()
    policy_id = uuid.uuid4()
    duration = DurationContractV2(
        minimum_duration_ms=1_500,
        target_duration_ms=2_000,
        maximum_duration_ms=3_500,
        duration_contract_version="channel-duration-contract.v2",
        duration_contract_hash=DurationContractV2.calculate_hash(
            minimum_duration_ms=1_500,
            target_duration_ms=2_000,
            maximum_duration_ms=3_500,
            duration_contract_version="channel-duration-contract.v2",
            source_profile_version_id=profile_id,
            source_policy_snapshot_id=policy_id,
        ),
        source_profile_version_id=profile_id,
        source_policy_snapshot_id=policy_id,
    )
    result = NativeQualificationService().render(
        NativeQualificationRenderRequest(
            run_key=f"phase6-{lane.value.lower().replace('_', '-')}-{uuid.uuid4().hex[:8]}",
            workspace_root=tmp_path / lane.value.lower(),
            company_id=uuid.uuid4(),
            channel_workspace_id=uuid.uuid4(),
            video_project_id=uuid.uuid4(),
            production_package_artifact_version_id=uuid.uuid4(),
            production_package_hash="a" * 64,
            channel_profile_version_id=profile_id,
            effective_context_snapshot_id=uuid.uuid4(),
            effective_context_hash="b" * 64,
            duration_contract=duration,
            production_lane=lane,
        )
    )

    assert result.output_path.is_file()
    assert result.output_path.stat().st_size > 0
    assert result.width == width
    assert result.height == height
    assert result.has_video_stream is True
    assert result.has_audio_stream is True
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert result.media_qc.result == "PASS"
    assert abs(result.duration_seconds - 2.0) <= 0.30
    assert result.execution_receipt.output_checksum == result.output_checksum
    assert result.execution_receipt.no_provider_calls_confirmed is True
    assert result.paid_provider_calls == 0


def test_real_long_render_resolves_exact_frozen_channel_duration(
    db_session,
    tmp_path,
) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    company = CompanyService(db_session).create_company(
        name=f"Phase 6 Qualification {uuid.uuid4().hex[:8]}"
    )
    operator = User(
        email=f"phase6-native-{uuid.uuid4()}@example.com",
        display_name="Phase 6 Native Qualification",
        status="active",
    )
    db_session.add(operator)
    db_session.flush()
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(
            key=f"phase6-native-{uuid.uuid4().hex[:8]}",
            name="Phase 6 Native Qualification",
        ),
    )
    compiler = ChannelProfileCompiler(db_session)
    template_input, _catalogs = compiler.profile_input_from_template(
        "saas_digital_leverage"
    )
    payload = deepcopy(template_input.model_dump(mode="json"))
    qualification_duration = {
        "minimum_duration_ms": 1_500,
        "target_duration_ms": 2_000,
        "maximum_duration_ms": 3_500,
        "duration_contract_version": "channel-duration-contract.v2",
    }
    payload["format_strategy"]["duration_contract"] = qualification_duration
    payload["format_strategy"]["duration_contracts"][ProductionLane.LONG_FORM.value] = (
        qualification_duration
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(
            profile_input=ChannelProfileInput.model_validate(payload),
            created_by=operator.id,
        ),
    )
    compiled = compiler.compile(
        profile_version_id=profile.id,
        correlation_id=f"phase6-native-{uuid.uuid4().hex[:8]}",
    )
    snapshot = ChannelProfileService(db_session).activate_snapshot(
        snapshot_id=compiled.snapshot_id
    )
    duration = ChannelDurationContractResolver(db_session).resolve(
        profile_version_id=profile.id,
        policy_snapshot_id=snapshot.id,
        production_lane=ProductionLane.LONG_FORM,
    )
    request = NativeQualificationRenderRequest(
        run_key=f"phase6-authority-{uuid.uuid4().hex[:8]}",
        workspace_root=tmp_path / "authority",
        company_id=company.id,
        channel_workspace_id=channel.id,
        video_project_id=uuid.uuid4(),
        production_package_artifact_version_id=uuid.uuid4(),
        production_package_hash="c" * 64,
        channel_profile_version_id=profile.id,
        effective_context_snapshot_id=uuid.uuid4(),
        effective_context_hash="d" * 64,
        duration_contract=DurationContractV2.model_validate(
            duration.model_dump(mode="json")
        ),
        production_lane=ProductionLane.LONG_FORM,
    )

    result = NativeQualificationService().render_from_frozen_channel(
        db_session,
        request,
    )

    assert result.output_path.is_file()
    assert result.width == 1920
    assert result.height == 1080
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"
    assert abs(result.duration_seconds - 2.0) <= 0.30
    assert (
        result.plan.duration_contract.duration_contract_hash
        == duration.duration_contract_hash
    )
    assert result.plan.channel_profile_version_id == str(profile.id)
    assert result.paid_provider_calls == 0
