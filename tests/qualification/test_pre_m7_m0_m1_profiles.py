from __future__ import annotations

import copy

import pytest
from sqlalchemy.exc import DBAPIError

from app.contracts import ChannelProfileInput, ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.db.models import CompiledChannelPolicySnapshot
from app.services import ChannelProfileCompiler, ChannelProfileService, ChannelWorkspaceService, VideoProjectService

from .helpers.qualification_asserts import ROOT


def test_m1_profile_compile_snapshot_determinism_and_immutability(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M1")
    compiler = ChannelProfileCompiler(db_session)
    same = compiler.compile(profile_version_id=scope.profile.id, correlation_id="pre-m7-m1-same")
    assert same.snapshot_id == scope.snapshot.id
    assert same.content_hash == scope.snapshot.content_hash

    before_payload = copy.deepcopy(scope.snapshot.compiled_payload)
    changed_input = ChannelProfileInput.model_validate(scope.profile.profile_input).model_copy(update={"display_name": "Changed Pre-M7"})
    new_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=scope.channel.id,
        data=ChannelProfileVersionCreate(profile_input=changed_input),
    )
    new_snapshot_id = compiler.compile(profile_version_id=new_profile.id, correlation_id="pre-m7-m1-new").snapshot_id
    new_snapshot = db_session.get(CompiledChannelPolicySnapshot, new_snapshot_id)
    assert new_snapshot.id != scope.snapshot.id
    assert new_snapshot.snapshot_version == scope.snapshot.snapshot_version + 1
    assert new_snapshot.content_hash != scope.snapshot.content_hash
    db_session.refresh(scope.snapshot)
    assert scope.snapshot.compiled_payload == before_payload

    scope.snapshot.compiled_payload = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_downstream_rejects_missing_wrong_inactive_snapshot_and_no_latest_lookup(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="M1 downstream")
    with pytest.raises(Exception):
        VideoProjectCreate.model_validate(
            {
                "company_id": scope.company.id,
                "channel_workspace_id": scope.channel.id,
                "title": "missing snapshot",
                "created_by_user_id": scope.operator.id,
            }
        )

    other = ChannelWorkspaceService(db_session).create_channel(
        company_id=scope.company.id,
        data=ChannelWorkspaceCreate(key=f"other-{scope.channel.key}", name="Other"),
    )
    other_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=other.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    other_snapshot_id = ChannelProfileCompiler(db_session).compile(
        profile_version_id=other_profile.id,
        correlation_id="pre-m7-other",
    ).snapshot_id
    with pytest.raises(ValidationFailureError):
        VideoProjectService(db_session).create_project(
            data=VideoProjectCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=other_snapshot_id,
                title="wrong channel",
                created_by_user_id=scope.operator.id,
            )
        )

    changed_input = ChannelProfileInput.model_validate(scope.profile.profile_input).model_copy(update={"display_name": "Inactive"})
    inactive_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=scope.channel.id,
        data=ChannelProfileVersionCreate(profile_input=changed_input),
    )
    inactive_snapshot_id = ChannelProfileCompiler(db_session).compile(
        profile_version_id=inactive_profile.id,
        correlation_id="pre-m7-inactive",
    ).snapshot_id
    with pytest.raises(ValidationFailureError):
        VideoProjectService(db_session).create_project(
            data=VideoProjectCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                policy_snapshot_id=inactive_snapshot_id,
                title="inactive snapshot",
                created_by_user_id=scope.operator.id,
            )
        )

    downstream_sources = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("app/services/workflow.py", "app/services/m5.py", "app/services/m6.py")
    )
    assert "get_active_snapshot_for_channel" not in downstream_sources
