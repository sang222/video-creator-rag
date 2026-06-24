import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import ChannelProfileInput, ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewFindingCreate,
    ReviewTaskCreate,
    RevisionRequestCreate,
    VideoProjectCreate,
)
from app.core.errors import ForbiddenError, ValidationFailureError
from app.db.models import AuditEvent, DomainEvent, LLMRunSnapshot, User
from app.main import create_app
from app.services import (
    ApprovalService,
    ArtifactService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    DecisionRightsService,
    RBACService,
    ReviewService,
    VideoProjectService,
)
from app.services.workflow import deterministic_artifact_content_hash

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

M2_TABLES = {
    "video_projects",
    "artifacts",
    "artifact_versions",
    "review_tasks",
    "review_findings",
    "revision_requests",
    "approval_decisions",
}


def _user(db_session, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], status="active")
    db_session.add(user)
    db_session.flush()
    return user


def _base(db_session):
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    company = CompanyService(db_session).create_company(name="M2 Co")
    creator = _user(db_session, "creator@example.com")
    reviewer = _user(db_session, "reviewer@example.com")
    approver = _user(db_session, "approver@example.com")
    rbac = RBACService(db_session)
    rbac.assign_role(user_id=creator.id, role_key="operator", company_id=company.id)
    rbac.assign_role(user_id=reviewer.id, role_key="operator", company_id=company.id)
    rbac.assign_role(user_id=approver.id, role_key="company_admin", company_id=company.id)
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="m2", name="M2 Channel"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="m2-compile")
    snapshot = ChannelProfileService(db_session).activate_snapshot(snapshot_id=compiled.snapshot_id)
    return company, channel, snapshot, creator, reviewer, approver, profile


def _project(db_session):
    company, channel, snapshot, creator, reviewer, approver, _ = _base(db_session)
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            title="M2 Project",
            created_by_user_id=creator.id,
        )
    )
    return company, channel, snapshot, creator, reviewer, approver, project


def _artifact_version(db_session):
    company, channel, snapshot, creator, reviewer, approver, project = _project(db_session)
    artifact = ArtifactService(db_session).create_artifact(
        data=ArtifactCreate(
            video_project_id=project.id,
            artifact_type="script",
            created_by_user_id=creator.id,
        )
    )
    version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            content={"title": "v1", "lines": ["hello"]},
            created_by_user_id=creator.id,
            external_entity_refs=[{"type": "brand", "id": "b1"}],
            packaging_metadata={"stub": True},
            media_qc_metadata={"stub": True},
            source_manifest={"sources": []},
            evidence_refs=[{"id": "ev1"}],
            context_refs=[{"id": "ctx1"}],
            claim_refs=[{"id": "cl1"}],
        )
    )
    return company, channel, snapshot, creator, reviewer, approver, project, artifact, version


def test_m2_tables_exist_and_defaults_are_non_null(engine, db_session) -> None:
    assert M2_TABLES <= set(inspect(engine).get_table_names())
    _, _, _, _, reviewer, _, project, _, version = _artifact_version(db_session)
    task = ReviewService(db_session).create_review_task(
        data=ReviewTaskCreate(
            video_project_id=project.id,
            target_type="artifact_version",
            target_id=version.id,
            target_artifact_version_id=version.id,
            review_type="editorial",
            requested_by_user_id=reviewer.id,
        )
    )
    assert version.external_entity_refs == [{"type": "brand", "id": "b1"}]
    assert version.packaging_metadata == {"stub": True}
    assert version.evidence_refs == [{"id": "ev1"}]
    assert version.retrieval_plan_ref is None
    assert task.review_reason_codes == []
    assert task.evidence_refs == []
    assert task.context_pack_ref is None


def test_project_creation_policy_snapshot_invariants(db_session) -> None:
    company, channel, snapshot, creator, _, _, profile = _base(db_session)
    with pytest.raises(ValidationError):
        VideoProjectCreate.model_validate(
            {
                "company_id": company.id,
                "channel_workspace_id": channel.id,
                "title": "Missing snapshot",
                "created_by_user_id": creator.id,
            }
        )
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            title="Pinned snapshot",
            created_by_user_id=creator.id,
        )
    )
    assert project.policy_snapshot_id == snapshot.id
    changed_input = ChannelProfileInput.model_validate(profile.profile_input).model_copy(update={"display_name": "Newer"})
    newer_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(profile_input=changed_input),
    )
    newer = ChannelProfileCompiler(db_session).compile(profile_version_id=newer_profile.id, correlation_id="m2-newer")
    second = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            title="Still pinned",
            created_by_user_id=creator.id,
        )
    )
    assert second.policy_snapshot_id == snapshot.id
    assert newer.snapshot_id != snapshot.id

    other_channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="other", name="Other"),
    )
    other_profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=other_channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    other_snapshot_id = ChannelProfileCompiler(db_session).compile(profile_version_id=other_profile.id, correlation_id="m2-other").snapshot_id
    with pytest.raises(ValidationFailureError):
        VideoProjectService(db_session).create_project(
            data=VideoProjectCreate(
                company_id=company.id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=other_snapshot_id,
                title="Wrong channel",
                created_by_user_id=creator.id,
            )
        )
    with pytest.raises(ValidationFailureError):
        VideoProjectService(db_session).create_project(
            data=VideoProjectCreate(
                company_id=company.id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=newer.snapshot_id,
                title="Inactive snapshot",
                created_by_user_id=creator.id,
            )
        )


def test_artifact_versions_are_immutable_and_hash_deterministic(db_session) -> None:
    _, _, _, creator, _, _, _, artifact, v1 = _artifact_version(db_session)
    v2 = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=v1.id,
            content={"lines": ["hello", "again"], "title": "v2"},
            created_by_user_id=creator.id,
        )
    )
    assert v2.version_number == 2
    assert v2.parent_version_id == v1.id
    assert v1.content_hash == deterministic_artifact_content_hash({"title": "v1", "lines": ["hello"]})
    assert deterministic_artifact_content_hash({"b": 2, "a": 1}) == deterministic_artifact_content_hash({"a": 1, "b": 2})
    v1.content = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_review_finding_revision_resolution_requires_new_version(db_session) -> None:
    _, _, _, creator, reviewer, _, project, artifact, v1 = _artifact_version(db_session)
    review = ReviewService(db_session).create_review_task(
        data=ReviewTaskCreate(
            video_project_id=project.id,
            target_type="artifact_version",
            target_id=v1.id,
            target_artifact_version_id=v1.id,
            review_type="editorial",
            requested_by_user_id=reviewer.id,
        )
    )
    with pytest.raises(ValidationFailureError):
        ReviewService(db_session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=project.id,
                target_type="artifact_version",
                target_id=artifact.id,
                target_artifact_version_id=v1.id,
                review_type="editorial",
                requested_by_user_id=reviewer.id,
            )
        )
    finding = ReviewService(db_session).add_finding(
        data=ReviewFindingCreate(
            review_task_id=review.id,
            severity="medium",
            reason_code="VALIDATION_FAILED",
            finding_text="Needs revision",
            created_by_user_id=reviewer.id,
        )
    )
    revision = ReviewService(db_session).create_revision_request(
        data=RevisionRequestCreate(
            review_task_id=review.id,
            target_artifact_version_id=v1.id,
            requested_by_user_id=reviewer.id,
            reason="Fix finding",
        )
    )
    with pytest.raises(ValidationFailureError):
        ReviewService(db_session).resolve_revision_request(
            revision_request_id=revision.id,
            resolved_by_artifact_version_id=v1.id,
        )
    v2 = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=v1.id,
            content={"title": "v2"},
            created_by_user_id=creator.id,
        )
    )
    resolved = ReviewService(db_session).resolve_revision_request(
        revision_request_id=revision.id,
        resolved_by_artifact_version_id=v2.id,
    )
    assert finding.review_task_id == review.id
    assert resolved.status == "resolved"
    assert resolved.resolved_by_artifact_version_id == v2.id


def test_approval_exact_version_self_approval_and_stale_state(db_session) -> None:
    _, _, _, creator, _, approver, project, artifact, v1 = _artifact_version(db_session)
    approval = ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=v1.id,
            target_artifact_version_id=v1.id,
            decision="approved",
            decided_by_user_id=approver.id,
        )
    )
    assert approval.target_artifact_version_id == v1.id
    with pytest.raises(ForbiddenError):
        ApprovalService(db_session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=v1.id,
                target_artifact_version_id=v1.id,
                decision="approved",
                decided_by_user_id=creator.id,
            )
        )
    v2 = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=v1.id,
            content={"title": "v2"},
            created_by_user_id=creator.id,
        )
    )
    db_session.refresh(artifact)
    assert artifact.current_version_id == v2.id
    assert ApprovalService(db_session).is_decision_stale_for_current_version(approval.id)
    state = VideoProjectService(db_session).inspect_workflow_state(project.id)
    stale = [item for item in state["approval_decisions"] if item["id"] == str(approval.id)][0]
    assert stale["stale_for_current_artifact_version"] is True


def test_decision_rights_separate_creator_reviewer_approver(db_session) -> None:
    company, _, _, creator, reviewer, approver, _, _, v1 = _artifact_version(db_session)
    rights = DecisionRightsService(db_session)
    assert rights.has_capability(user_id=reviewer.id, company_id=company.id, action="review_finding.create")
    assert not rights.has_capability(user_id=creator.id, company_id=company.id, action="approval_decision.create")
    assert rights.has_capability(user_id=approver.id, company_id=company.id, action="approval_decision.create")
    other = _user(db_session, "other@example.com")
    with pytest.raises(ForbiddenError):
        ApprovalService(db_session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=v1.id,
                target_artifact_version_id=v1.id,
                decision="approved",
                decided_by_user_id=other.id,
            )
        )


def test_workflow_actions_write_audit_and_domain_events(db_session) -> None:
    _, _, _, creator, reviewer, approver, project, artifact, v1 = _artifact_version(db_session)
    review = ReviewService(db_session).create_review_task(
        data=ReviewTaskCreate(
            video_project_id=project.id,
            target_type="artifact_version",
            target_id=v1.id,
            target_artifact_version_id=v1.id,
            review_type="editorial",
            requested_by_user_id=reviewer.id,
        )
    )
    ReviewService(db_session).add_finding(
        data=ReviewFindingCreate(
            review_task_id=review.id,
            severity="low",
            reason_code="VALIDATION_FAILED",
            finding_text="Tighten copy",
            created_by_user_id=reviewer.id,
        )
    )
    revision = ReviewService(db_session).create_revision_request(
        data=RevisionRequestCreate(
            review_task_id=review.id,
            target_artifact_version_id=v1.id,
            requested_by_user_id=reviewer.id,
            reason="Tighten copy",
        )
    )
    v2 = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=artifact.id,
            parent_version_id=v1.id,
            content={"title": "v2"},
            created_by_user_id=creator.id,
        )
    )
    ReviewService(db_session).resolve_revision_request(
        revision_request_id=revision.id,
        resolved_by_artifact_version_id=v2.id,
    )
    ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=v2.id,
            target_artifact_version_id=v2.id,
            decision="approved",
            decided_by_user_id=approver.id,
        )
    )
    expected = {
        "video_project.created",
        "artifact.created",
        "artifact_version.created",
        "review_task.created",
        "review_finding.created",
        "revision_request.created",
        "revision_request.resolved",
        "approval_decision.created",
    }
    audit_types = set(db_session.scalars(select(AuditEvent.event_type)).all())
    domain_types = set(db_session.scalars(select(DomainEvent.event_type)).all())
    assert expected <= audit_types
    assert expected <= domain_types
    assert db_session.scalar(select(LLMRunSnapshot).limit(1)) is None


def test_m2_scope_guard_tables_and_code() -> None:
    forbidden_tables = {
        "resource_resolvers",
        "context_packs",
        "retrieval_plans",
        "semantic_layers",
        "memory_promotions",
        "media_renders",
        "publish_uploads",
        "analytics_events",
        "dashboard_widgets",
    }
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    assert not forbidden_tables & M2_TABLES
    assert "ResourceResolver" not in app_text
    assert "ContextPack" not in app_text
    assert "RetrievalPlan" not in app_text
    assert "SemanticLayer" not in app_text
    assert "MemoryPromotion" not in app_text
    assert "AIAnswerJudge" not in app_text


def test_m2_docs_cover_allowances_contracts_and_future_ownership() -> None:
    doc = (ROOT / "docs/architecture/m2-artifact-workflow.md").read_text(encoding="utf-8")
    assert "Allowance fields exist" in doc
    assert "Numeric Truth Contract" in doc
    assert "Retrieval Boundary Contract" in doc
    assert "Answer Contract" in doc
    assert "Memory Promotion Contract" in doc
    assert "M5 owns ResourceResolver" in doc
    assert "M11 owns multi-channel operator cockpit" in doc
    assert "Raw vendor payloads" in doc


def test_m2_api_and_cli_smoke(db_session) -> None:
    company, channel, snapshot, creator, reviewer, _, _ = _base(db_session)
    db_session.commit()
    client = TestClient(create_app())
    project = client.post(
        "/video-projects",
        json={
            "company_id": str(company.id),
            "channel_workspace_id": str(channel.id),
            "policy_snapshot_id": str(snapshot.id),
            "title": "API M2",
            "created_by_user_id": str(creator.id),
        },
    )
    assert project.status_code == 200, project.text
    artifact = client.post(
        "/artifacts",
        json={
            "video_project_id": project.json()["id"],
            "artifact_type": "script",
            "created_by_user_id": str(creator.id),
        },
    )
    assert artifact.status_code == 200, artifact.text
    version = client.post(
        "/artifact-versions",
        json={
            "artifact_id": artifact.json()["id"],
            "content": {"body": "v1"},
            "created_by_user_id": str(creator.id),
        },
    )
    assert version.status_code == 200, version.text
    review = client.post(
        "/review-tasks",
        json={
            "video_project_id": project.json()["id"],
            "target_type": "artifact_version",
            "target_id": version.json()["id"],
            "target_artifact_version_id": version.json()["id"],
            "review_type": "editorial",
            "requested_by_user_id": str(reviewer.id),
        },
    )
    assert review.status_code == 200, review.text
    cli_project = runner.invoke(
        cli_app,
        [
            "project",
            "create",
            "--company-id",
            str(company.id),
            "--channel-id",
            str(channel.id),
            "--policy-snapshot-id",
            str(snapshot.id),
            "--title",
            "CLI M2",
            "--created-by-user-id",
            str(creator.id),
        ],
    )
    assert cli_project.exit_code == 0, cli_project.output
    cli_project_id = json.loads(cli_project.output)["id"]
    inspected = runner.invoke(cli_app, ["workflow", "inspect", "--project-id", cli_project_id])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["project_id"] == cli_project_id
