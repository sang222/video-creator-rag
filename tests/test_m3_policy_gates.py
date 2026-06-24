import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.gates import (
    GateDefinitionVersionCreate,
    GateRunCreate,
    PlatformPolicyCatalogCreate,
    PlatformPolicyVersionCreate,
    PolicyChangeRecordCreate,
    PolicyRevalidationBatchCreate,
    PolicySourceRefCreate,
)
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate, VideoProjectCreate
from app.core.errors import ConflictError, ValidationFailureError
from app.db.models import ApprovalDecision, GateDefinitionVersion, GateRun, LLMRunSnapshot, ReviewTask, User
from app.main import create_app
from app.services import (
    ArtifactService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    GateDefinitionService,
    GateRunnerService,
    PolicyCatalogService,
    PolicyChangeService,
    PolicyRevalidationService,
    RBACService,
    VideoProjectService,
    WorkflowReadinessService,
)

ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()

M3_TABLES = {
    "gate_definition_versions",
    "gate_runs",
    "platform_policy_catalogs",
    "platform_policy_versions",
    "policy_source_refs",
    "policy_change_records",
    "policy_revalidation_batches",
}


def _user(db_session, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], status="active")
    db_session.add(user)
    db_session.flush()
    return user


def _base(db_session, *, project_overrides=None, version_overrides=None):
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    GateDefinitionService(db_session).seed_definitions()
    company = CompanyService(db_session).create_company(name="M3 Co")
    creator = _user(db_session, "m3-creator@example.com")
    reviewer = _user(db_session, "m3-reviewer@example.com")
    rbac = RBACService(db_session)
    rbac.assign_role(user_id=creator.id, role_key="operator", company_id=company.id)
    rbac.assign_role(user_id=reviewer.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="m3", name="M3 Channel"),
    )
    profile = ChannelProfileService(db_session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(db_session).compile(profile_version_id=profile.id, correlation_id="m3-compile")
    snapshot = ChannelProfileService(db_session).activate_snapshot(snapshot_id=compiled.snapshot_id)
    project_payload = {
        "company_id": company.id,
        "channel_workspace_id": channel.id,
        "policy_snapshot_id": snapshot.id,
        "title": "M3 Project",
        "created_by_user_id": creator.id,
    }
    project_payload.update(project_overrides or {})
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(**project_payload)
    )
    artifact = ArtifactService(db_session).create_artifact(
        data=ArtifactCreate(video_project_id=project.id, artifact_type="script", created_by_user_id=creator.id)
    )
    version_payload = {
        "artifact_id": artifact.id,
        "content": {"body": "draft"},
        "created_by_user_id": creator.id,
    }
    version_payload.update(version_overrides or {})
    version = ArtifactService(db_session).create_artifact_version(data=ArtifactVersionCreate(**version_payload))
    return company, channel, snapshot, creator, reviewer, project, artifact, version


def test_m3_tables_exist_and_json_defaults(engine, db_session) -> None:
    assert M3_TABLES <= set(inspect(engine).get_table_names())
    GateDefinitionService(db_session).seed_definitions()
    definition = GateDefinitionService(db_session).get_active_gate_version("ai_use_disclosure_gate")
    assert definition.definition
    assert isinstance(definition.reason_code_refs, list)
    assert definition.status == "active"


def test_gate_definition_lifecycle_uniqueness_and_active_immutability(db_session) -> None:
    service = GateDefinitionService(db_session)
    service.seed_definitions()
    active = service.get_active_gate_version("ai_use_disclosure_gate")
    with pytest.raises(ConflictError):
        service.create_definition(
            data=GateDefinitionVersionCreate(
                gate_key="ai_use_disclosure_gate",
                gate_name="Duplicate",
                gate_domain="ai_policy",
                version="1.0.0",
                input_schema_version="gate-input.m3.v1",
                output_schema_version="gate-output.m3.v1",
            )
        )
    v2 = service.create_definition(
        data=GateDefinitionVersionCreate(
            gate_key="ai_use_disclosure_gate",
            gate_name="AI Use Disclosure Gate",
            gate_domain="ai_policy",
            version="1.0.1",
            input_schema_version="gate-input.m3.v1",
            output_schema_version="gate-output.m3.v1",
            definition={"logic": "ai_use_disclosure_gate", "review_required": True},
            reason_code_refs=["AI_DISCLOSURE_REQUIRED"],
        )
    )
    activated = service.activate_definition(v2.id)
    db_session.refresh(active)
    assert activated.status == "active"
    assert active.status == "superseded"
    activated.definition = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_gate_run_exact_target_hash_fields_and_immutability(db_session) -> None:
    *_, version = _base(db_session)
    run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=version.id)
    )
    same_hash = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=version.id)
    ).input_snapshot_hash
    assert run.gate_definition_version_id
    assert run.target_type == "artifact_version"
    assert run.target_id == version.id
    assert run.artifact_version_id == version.id
    assert run.input_snapshot_hash == same_hash
    assert run.reason_codes
    assert run.evidence_refs == []
    assert run.metric_refs == []
    assert run.freshness_state in {"FRESH", "STALE", "UNKNOWN", "NOT_REQUIRED"}
    assert run.confidence_level in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
    assert db_session.scalar(select(LLMRunSnapshot).limit(1)) is None
    run.result = "PASS"
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_review_required_creates_review_task_and_pass_does_not(db_session) -> None:
    *_, version = _base(
        db_session,
        version_overrides={"media_qc_metadata": {"ai_used": True}},
    )
    ai_run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="ai_use_disclosure_gate", target_type="artifact_version", target_id=version.id)
    )
    assert ai_run.result == "REVIEW_REQUIRED"
    task = db_session.get(ReviewTask, ai_run.created_review_task_id)
    assert task is not None
    assert task.review_type == "ai_disclosure"
    assert task.target_type == "artifact_version"
    assert task.target_id == version.id
    assert task.target_artifact_version_id == version.id

    rights_version = ArtifactService(db_session).create_artifact_version(
        data=ArtifactVersionCreate(
            artifact_id=version.artifact_id,
            parent_version_id=version.id,
            content={"body": "rights ok"},
            created_by_user_id=version.created_by_user_id,
            source_manifest={"rights_basis": "licensed"},
            evidence_refs=[{"type": "rights_license_ref", "id": "lic-1"}],
        )
    )
    pass_run = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=rights_version.id)
    )
    assert pass_run.result == "PASS"
    assert pass_run.created_review_task_id is None
    assert db_session.scalar(select(ApprovalDecision).limit(1)) is None


def test_policy_catalog_version_source_and_active_immutability(db_session) -> None:
    service = PolicyCatalogService(db_session)
    catalog = service.create_catalog(data=PlatformPolicyCatalogCreate(catalog_key="generic_test_policy", platform="generic", policy_domain="privacy"))
    v1 = service.create_version(data=PlatformPolicyVersionCreate(catalog_id=catalog.id, version="1.0.0", policy_blob={"rules": [{"key": "a"}]}))
    service.attach_source_ref(data=PolicySourceRefCreate(policy_version_id=v1.id, source_type="OFFICIAL", reliability="OFFICIAL", source_url="https://example.test/policy"))
    service.activate_version(v1.id)
    db_session.refresh(catalog)
    assert catalog.current_version_id == v1.id
    v2 = service.create_version(data=PlatformPolicyVersionCreate(catalog_id=catalog.id, version="1.0.1", policy_blob={"rules": [{"key": "b"}]}))
    service.activate_version(v2.id)
    db_session.refresh(v1)
    db_session.refresh(catalog)
    assert v1.status == "superseded"
    assert catalog.current_version_id == v2.id
    v2.policy_blob = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()


def test_policy_change_and_revalidation_create_new_gate_runs(db_session) -> None:
    *_, version = _base(db_session)
    first = GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=version.id)
    )
    change_service = PolicyChangeService(db_session)
    change = change_service.create_change_record(
        data=PolicyChangeRecordCreate(change_key="m3-change-1", platform="generic", policy_domain="legal_compliance", summary="Manual policy update")
    )
    change_service.transition_state(change.id, "SOURCE_VERIFIED")
    change_service.transition_state(change.id, "DIFFED")
    with pytest.raises(ValidationFailureError):
        change_service.transition_state(change.id, "ACTIVE")
    batch = PolicyRevalidationService(db_session).create_batch(
        data=PolicyRevalidationBatchCreate(
            policy_change_record_id=change.id,
            scope={"targets": [{"target_type": "artifact_version", "target_id": str(version.id), "gate_key": "rights_copyright_gate"}]},
        )
    )
    completed = PolicyRevalidationService(db_session).run_batch(batch.id)
    runs = db_session.scalars(select(GateRun).where(GateRun.gate_key == "rights_copyright_gate")).all()
    db_session.refresh(first)
    assert completed.status == "COMPLETED"
    assert completed.counts["created"] == 1
    assert len(runs) == 2
    assert first.input_snapshot_hash == runs[0].input_snapshot_hash


def test_readiness_aggregates_blockers_reviews_and_passes(db_session) -> None:
    *_, project, _, version = _base(db_session)
    definition = GateDefinitionService(db_session).get_active_gate_version("publish_risk_gate")
    db_session.add(
        GateRun(
            gate_definition_version_id=definition.id,
            gate_key="manual_block_gate",
            target_type="artifact_version",
            target_id=version.id,
            video_project_id=project.id,
            artifact_version_id=version.id,
            policy_snapshot_id=project.policy_snapshot_id,
            input_snapshot={"target": {"target_type": "artifact_version", "target_id": str(version.id)}},
            input_snapshot_hash="manual-block",
            result="BLOCK",
            reason_codes=["MANUAL_REVIEW_REQUIRED"],
            freshness_state="UNKNOWN",
            confidence_level="HIGH",
            decision_basis={"manual_test": True},
        )
    )
    GateRunnerService(db_session).run_gate(data=GateRunCreate(gate_key="publish_risk_gate", target_type="artifact_version", target_id=version.id))
    state = WorkflowReadinessService(db_session).inspect_project(project.id)
    assert state["status"] == "BLOCKED"
    assert state["counts"]["BLOCK"] >= 1
    assert state["blockers"]


def test_builtin_gate_minimal_results(db_session) -> None:
    *_, project, artifact, ai_version = _base(
        db_session,
        project_overrides={"financial_summary": {"paid_promotion": True}, "audience_delivery_summary": {"search_led": True}},
        version_overrides={"media_qc_metadata": {"ai_used": True}, "external_entity_refs": [{"restricted_entity": True}]},
    )
    runner_service = GateRunnerService(db_session)
    assert runner_service.run_gate(data=GateRunCreate(gate_key="ai_use_disclosure_gate", target_type="artifact_version", target_id=ai_version.id)).result == "REVIEW_REQUIRED"
    rights = runner_service.run_gate(data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=ai_version.id))
    assert rights.result == "REVIEW_REQUIRED"
    assert "RIGHTS_BASIS_MISSING" in rights.reason_codes
    commercial = runner_service.run_gate(data=GateRunCreate(gate_key="commercial_disclosure_gate", target_type="artifact_version", target_id=ai_version.id))
    assert commercial.result == "REVIEW_REQUIRED"
    brand = runner_service.run_gate(data=GateRunCreate(gate_key="brand_conflict_gate", target_type="artifact_version", target_id=ai_version.id))
    assert brand.result == "REVIEW_REQUIRED"
    search = runner_service.run_gate(data=GateRunCreate(gate_key="search_demand_gate", target_type="video_project", target_id=project.id))
    assert search.result == "REVIEW_REQUIRED"
    definition = GateDefinitionService(db_session).get_active_gate_version("publish_risk_gate")
    db_session.add(
        GateRun(
            gate_definition_version_id=definition.id,
            gate_key="manual_block_gate",
            target_type="artifact_version",
            target_id=ai_version.id,
            video_project_id=project.id,
            artifact_version_id=ai_version.id,
            policy_snapshot_id=project.policy_snapshot_id,
            input_snapshot={"target": {"target_type": "artifact_version", "target_id": str(ai_version.id)}},
            input_snapshot_hash="manual-block-2",
            result="BLOCK",
            reason_codes=["MANUAL_REVIEW_REQUIRED"],
            freshness_state="UNKNOWN",
            confidence_level="HIGH",
            decision_basis={"manual_test": True},
        )
    )
    db_session.flush()
    publish = runner_service.run_gate(data=GateRunCreate(gate_key="publish_risk_gate", target_type="artifact_version", target_id=ai_version.id))
    assert publish.result == "BLOCK"


def test_m3_api_and_cli_smoke(db_session) -> None:
    *_, project, _, version = _base(db_session)
    db_session.commit()
    client = TestClient(create_app())
    response = client.post(
        "/gates/run",
        json={"gate_key": "rights_copyright_gate", "target_type": "artifact_version", "target_id": str(version.id)},
    )
    assert response.status_code == 200, response.text
    readiness = client.get(f"/video-projects/{project.id}/readiness")
    assert readiness.status_code == 200, readiness.text
    cli = runner.invoke(
        cli_app,
        ["gate", "run", "--gate-key", "rights_copyright_gate", "--target-type", "artifact_version", "--target-id", str(version.id)],
    )
    assert cli.exit_code == 0, cli.output
    cli_run_id = json.loads(cli.output)["id"]
    inspected = runner.invoke(cli_app, ["gate", "inspect", "--gate-run-id", cli_run_id])
    assert inspected.exit_code == 0, inspected.output
    ready_cli = runner.invoke(cli_app, ["readiness", "inspect", "--project-id", str(project.id)])
    assert ready_cli.exit_code == 0, ready_cli.output


def test_m3_scope_guards_and_docs() -> None:
    forbidden_tables = {
        "resource_resolvers",
        "context_packs",
        "retrieval_plans",
        "semantic_layers",
        "media_renders",
        "publish_uploads",
        "analytics_events",
        "dashboard_widgets",
        "algorithm_agents",
        "growth_agents",
        "view_agents",
    }
    assert not forbidden_tables & M3_TABLES
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    assert "ResourceResolver" not in app_text
    assert "ContextPack" not in app_text
    assert "RetrievalPlan" not in app_text
    assert "SemanticLayer" not in app_text
    assert "MediaRender" not in app_text
    assert "PublishUpload" not in app_text
    assert "Dashboard" not in app_text
    doc = (ROOT / "docs/architecture/m3-policy-gate-readiness.md").read_text(encoding="utf-8")
    assert "No LLM policy decisions" in doc
    assert "M5 will build ResourceResolver" in doc
    assert "M11 will build multi-channel operator cockpit" in doc
