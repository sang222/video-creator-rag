from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select

import app.services.m6 as m6_service
from app.contracts import CredentialReferenceCreate
from app.contracts.m6 import ProductionArtifactRunCreate
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate, ReviewTaskCreate
from app.core.errors import ValidationFailureError
from app.db.models import AuditEvent, DomainEvent, MediaRenderJob, RenderPackageSnapshot, VideoProject
from app.services import ArtifactService, CredentialReferenceService, ProductionArtifactRunService, ReviewService

from .helpers.network_sentinel import assert_network_sentinel_blocks
from .helpers.qualification_asserts import assert_no_secret_payload
from .helpers.repo_scanners import all_scope_violations


def test_negative_malformed_json_missing_ids_wrong_channel_and_cross_project_refs(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    with pytest.raises(ValidationError):
        ArtifactVersionCreate(artifact_id=flow.artifact.id, content=["not-object"], created_by_user_id=flow.operator.id)
    with pytest.raises(ValidationError):
        ArtifactCreate(artifact_type="script", created_by_user_id=flow.operator.id)

    other = qualification_factory.m2_project()
    with pytest.raises(ValidationFailureError):
        ReviewService(db_session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=other.project.id,
                target_type="artifact_version",
                target_id=flow.version.id,
                target_artifact_version_id=flow.version.id,
                review_type="editorial",
                requested_by_user_id=other.operator.id,
            )
        )


def test_negative_secret_network_scope_and_binary_stage_sentinels(db_session, qualification_factory, monkeypatch, engine) -> None:
    qualification_factory.seed_all()
    with pytest.raises(ValidationFailureError):
        CredentialReferenceService(db_session).create_reference(
            data=CredentialReferenceCreate(provider_key="mock_llm", credential_key="raw", credential_type="API_KEY", secret_ref="sk-this-is-raw")
        )
    event_payload = json.dumps([event.payload for event in db_session.scalars(select(AuditEvent)).all()] + [event.payload for event in db_session.scalars(select(DomainEvent)).all()])
    assert "sk-this-is-raw" not in event_payload
    assert_no_secret_payload(event_payload)
    assert_network_sentinel_blocks(monkeypatch)
    assert all_scope_violations(engine) == []


def test_negative_bad_ffmpeg_path_blocks_without_fake_pass(db_session, qualification_factory, monkeypatch) -> None:
    monkeypatch.setattr(m6_service.shutil, "which", lambda name: None)
    flow = qualification_factory.m5_admitted_project()
    run = ProductionArtifactRunService(db_session).create_run(data=ProductionArtifactRunCreate(video_project_id=flow.project.id))
    executed = ProductionArtifactRunService(db_session).execute_local_mock_flow(run_id=run.id)
    assert executed.status == "BLOCKED"
    assert "FFMPEG_UNAVAILABLE" in executed.reason_codes
    assert db_session.scalars(select(MediaRenderJob)).one().status == "BLOCKED"
    assert db_session.scalars(select(RenderPackageSnapshot)).all() == []


def test_negative_bad_artifact_contract_rollback_leaves_no_partial_project(db_session, qualification_factory) -> None:
    flow = qualification_factory.m2_project()
    before_projects = db_session.query(VideoProject).count()
    with pytest.raises(Exception):
        ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=flow.artifact.id,
                parent_version_id=flow.version.id,
                content={"title": "bad"},
                source_manifest={"prompt": "raw prompt should be blocked"},
                created_by_user_id=flow.operator.id,
            )
        )
    db_session.rollback()
    assert db_session.query(VideoProject).count() <= before_projects
