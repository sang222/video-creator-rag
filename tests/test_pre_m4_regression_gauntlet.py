import json
import os
import socket
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.contracts.gates import (
    GateRunCreate,
    GateRunRead,
    PlatformPolicyCatalogCreate,
    PlatformPolicyVersionCreate,
    PolicyChangeRecordCreate,
    PolicyRevalidationBatchCreate,
    PolicySourceRefCreate,
)
from app.contracts.workflow import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewFindingCreate,
    ReviewTaskCreate,
    RevisionRequestCreate,
    VideoProjectCreate,
)
from app.core.config import get_settings
from app.core.db import reset_db_caches
from app.core.errors import ForbiddenError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    AuditEvent,
    ConfigCatalogVersion,
    DomainEvent,
    GateDefinitionVersion,
    GateRun,
    PlatformPolicyCatalog,
    PlatformPolicyVersion,
    PolicyRevalidationBatch,
    ReviewTask,
    User,
)
from app.main import create_app
from app.services import (
    ApprovalService,
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
    ReviewService,
    VideoProjectService,
    WorkflowReadinessService,
)

ROOT = Path(__file__).resolve().parents[1]
ADMIN_URL = os.getenv(
    "VCOS_TEST_ADMIN_DATABASE_URL",
    "postgresql+psycopg://vcos:vcos@localhost:55432/postgres",
)
runner = CliRunner()

EXPECTED_M0_M3_TABLES = {
    "companies",
    "users",
    "roles",
    "user_roles",
    "audit_events",
    "domain_events",
    "llm_run_snapshots",
    "config_catalog_versions",
    "channel_workspaces",
    "channel_memberships",
    "channel_profile_versions",
    "channel_profile_compile_runs",
    "compiled_channel_policy_snapshots",
    "video_projects",
    "artifacts",
    "artifact_versions",
    "review_tasks",
    "review_findings",
    "revision_requests",
    "approval_decisions",
    "gate_definition_versions",
    "gate_runs",
    "platform_policy_catalogs",
    "platform_policy_versions",
    "policy_source_refs",
    "policy_change_records",
    "policy_revalidation_batches",
}

EXPECTED_M4_TABLES = {
    "provider_registry_entries",
    "credential_references",
    "credential_health_snapshots",
    "quota_accounts",
    "quota_events",
    "cost_events",
    "budget_policies",
    "provider_health_snapshots",
    "component_health_snapshots",
    "system_health_snapshots",
    "retry_policies",
    "provider_attempts",
    "dead_letter_jobs",
    "ops_incidents",
    "manual_action_queue",
}

FORBIDDEN_FUTURE_TERMS = {
    "ResourceResolver",
    "ContextPackSnapshot",
    "RetrievalPlanSnapshot",
    "VectorStore",
    "MediaRender",
    "PublishPackage",
    "PublishUpload",
    "SemanticLayer",
    "AnalyticsEvent",
    "MemoryPromotion",
    "OperatorCockpit",
    "DashboardWidget",
    "SourceScraper",
    "SourceParser",
    "OPA",
    "Cedar",
    "AlgorithmAgent",
    "GrowthAgent",
    "ViewAgent",
}

FORBIDDEN_TABLE_FRAGMENTS = {
    "resource_resolver",
    "context_pack",
    "retrieval_plan",
    "vector",
    "embedding",
    "media_render",
    "publish",
    "upload",
    "analytics",
    "semantic",
    "memory_promotion",
    "dashboard",
    "source_scrap",
    "source_parse",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
}


def _admin_conninfo() -> str:
    return make_url(ADMIN_URL).set(drivername="postgresql").render_as_string(hide_password=False)


def _alembic_config() -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return config


def _create_temp_database(name: str) -> str:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return make_url(ADMIN_URL).set(database=name).render_as_string(hide_password=False)


def _drop_temp_database(name: str) -> None:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as connection:
        connection.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
            (name,),
        )
        connection.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.fixture
def migrated_temp_database(monkeypatch):
    db_name = f"vcos_chain_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    database_url = _create_temp_database(db_name)
    old_url = os.environ.get("VCOS_DATABASE_URL")
    monkeypatch.setenv("VCOS_DATABASE_URL", database_url)
    get_settings.cache_clear()
    reset_db_caches()
    try:
        yield database_url
    finally:
        if old_url is None:
            monkeypatch.delenv("VCOS_DATABASE_URL", raising=False)
        else:
            monkeypatch.setenv("VCOS_DATABASE_URL", old_url)
        get_settings.cache_clear()
        reset_db_caches()
        _drop_temp_database(db_name)


def _user(session: Session, email: str) -> User:
    user = User(email=email, display_name=email.split("@")[0], status="active")
    session.add(user)
    session.flush()
    return user


def _base(session: Session):
    ConfigRegistryService(session).seed([ROOT / "config"])
    GateDefinitionService(session).seed_definitions()
    company = CompanyService(session).create_company(name="Pre M4 Co")
    creator = _user(session, f"creator-{uuid.uuid4().hex[:6]}@example.com")
    reviewer = _user(session, f"reviewer-{uuid.uuid4().hex[:6]}@example.com")
    approver = _user(session, f"approver-{uuid.uuid4().hex[:6]}@example.com")
    rbac = RBACService(session)
    rbac.assign_role(user_id=creator.id, role_key="operator", company_id=company.id)
    rbac.assign_role(user_id=reviewer.id, role_key="operator", company_id=company.id)
    rbac.assign_role(user_id=approver.id, role_key="company_admin", company_id=company.id)
    channel = ChannelWorkspaceService(session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key=f"prem4-{uuid.uuid4().hex[:6]}", name="Pre M4"),
    )
    profile = ChannelProfileService(session).create_profile_version(
        channel_id=channel.id,
        data=ChannelProfileVersionCreate(template_key="saas_digital_leverage"),
    )
    compiled = ChannelProfileCompiler(session).compile(profile_version_id=profile.id, correlation_id="prem4-compile")
    snapshot = ChannelProfileService(session).activate_snapshot(snapshot_id=compiled.snapshot_id)
    project = VideoProjectService(session).create_project(
        data=VideoProjectCreate(
            company_id=company.id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            title="Pre M4 Project",
            created_by_user_id=creator.id,
        )
    )
    artifact = ArtifactService(session).create_artifact(
        data=ArtifactCreate(video_project_id=project.id, artifact_type="script", created_by_user_id=creator.id)
    )
    v1 = ArtifactService(session).create_artifact_version(
        data=ArtifactVersionCreate(artifact_id=artifact.id, content={"body": "v1"}, created_by_user_id=creator.id)
    )
    return company, channel, snapshot, creator, reviewer, approver, project, artifact, v1


def test_migration_chain_idempotency_and_downgrade_reupgrade(migrated_temp_database) -> None:
    config = _alembic_config()
    revisions = [
        "0001_m0_foundation",
        "0002_m1_channel_profile_backbone",
        "0003_m2_workflow",
        "0004_m3_policy_gate_readiness",
        "0005_m4_ops_foundation",
    ]
    expected_by_revision = [
        {"companies", "config_catalog_versions"},
        {"channel_workspaces", "compiled_channel_policy_snapshots"},
        {"video_projects", "artifact_versions", "approval_decisions"},
        {"gate_runs", "platform_policy_versions", "policy_revalidation_batches"},
        {"provider_registry_entries", "quota_events", "system_health_snapshots"},
    ]
    engine = create_engine(migrated_temp_database, future=True)
    try:
        for revision, expected_tables in zip(revisions, expected_by_revision, strict=True):
            command.upgrade(config, revision)
            tables = set(inspect(engine).get_table_names())
            assert expected_tables <= tables
            with engine.connect() as connection:
                assert connection.execute(text("select version_num from alembic_version")).scalar_one() == revision
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        assert EXPECTED_M0_M3_TABLES <= set(inspect(engine).get_table_names())
        assert EXPECTED_M4_TABLES <= set(inspect(engine).get_table_names())
        command.downgrade(config, "0003_m2_workflow")
        assert "gate_runs" not in set(inspect(engine).get_table_names())
        assert "provider_registry_entries" not in set(inspect(engine).get_table_names())
        command.upgrade(config, "head")
        assert EXPECTED_M0_M3_TABLES <= set(inspect(engine).get_table_names())
        assert EXPECTED_M4_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_config_and_gate_seeds_are_idempotent_with_expected_counts(db_session) -> None:
    config_service = ConfigRegistryService(db_session)
    gate_service = GateDefinitionService(db_session)
    config_service.seed([ROOT / "config"])
    gate_service.seed_definitions()
    first = {
        "config": db_session.query(ConfigCatalogVersion).count(),
        "gates": db_session.query(GateDefinitionVersion).filter_by(status="active").count(),
        "audit": db_session.query(AuditEvent).count(),
        "domain": db_session.query(DomainEvent).count(),
    }
    config_service.seed([ROOT / "config"])
    gate_service.seed_definitions()
    second = {
        "config": db_session.query(ConfigCatalogVersion).count(),
        "gates": db_session.query(GateDefinitionVersion).filter_by(status="active").count(),
        "audit": db_session.query(AuditEvent).count(),
        "domain": db_session.query(DomainEvent).count(),
    }
    assert first == second
    assert second["config"] == 30
    assert second["gates"] == 15


def test_db_constraints_and_rollback_no_partial_events(db_session) -> None:
    *_, artifact, v1 = _base(db_session)
    db_session.commit()
    with pytest.raises(IntegrityError):
        db_session.add(
            type(v1)(
                artifact_id=artifact.id,
                version_number=v1.version_number,
                content={"dup": True},
                content_hash="dup",
                created_by_user_id=v1.created_by_user_id,
            )
        )
        db_session.commit()
    db_session.rollback()
    before = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    with pytest.raises(ValidationFailureError):
        ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content={"invalid": "missing parent after v1"},
                created_by_user_id=v1.created_by_user_id,
            )
        )
    db_session.rollback()
    after = (db_session.query(AuditEvent).count(), db_session.query(DomainEvent).count())
    assert after == before


def test_full_m2_e2e_exact_versions_and_events(db_session) -> None:
    _, _, _, creator, reviewer, approver, project, artifact, v1 = _base(db_session)
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
            severity="medium",
            reason_code="VALIDATION_FAILED",
            finding_text="revise exact v1",
            evidence_refs=[{"type": "note", "id": "f1"}],
            created_by_user_id=reviewer.id,
        )
    )
    revision = ReviewService(db_session).create_revision_request(
        data=RevisionRequestCreate(
            review_task_id=review.id,
            target_artifact_version_id=v1.id,
            requested_by_user_id=reviewer.id,
            reason="address finding",
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
            content={"body": "v2"},
            created_by_user_id=creator.id,
        )
    )
    ReviewService(db_session).resolve_revision_request(
        revision_request_id=revision.id,
        resolved_by_artifact_version_id=v2.id,
    )
    approval = ApprovalService(db_session).create_approval_decision(
        data=ApprovalDecisionCreate(
            target_type="artifact_version",
            target_id=v2.id,
            target_artifact_version_id=v2.id,
            decision="approved",
            decided_by_user_id=approver.id,
        )
    )
    with pytest.raises(ForbiddenError):
        ApprovalService(db_session).create_approval_decision(
            data=ApprovalDecisionCreate(
                target_type="artifact_version",
                target_id=v2.id,
                target_artifact_version_id=v2.id,
                decision="approved",
                decided_by_user_id=creator.id,
            )
        )
    state = VideoProjectService(db_session).inspect_workflow_state(project.id)
    assert approval.target_artifact_version_id == v2.id
    assert any(item["id"] == str(v2.id) and item["version_number"] == 2 for item in state["artifact_versions"])
    assert any(item["status"] == "resolved" for item in state["revision_requests"])
    assert db_session.scalar(select(ApprovalDecision).where(ApprovalDecision.target_artifact_version_id == v1.id)) is None


def test_gate_result_matrix_contracts_and_revalidation_history(db_session) -> None:
    *_, project, artifact, v1 = _base(db_session)
    runner_service = GateRunnerService(db_session)
    matrix_versions = {
        "PASS": ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=v1.id,
                content={"body": "licensed"},
                created_by_user_id=v1.created_by_user_id,
                source_manifest={"rights_basis": "licensed"},
                evidence_refs=[{"type": "rights_license_ref", "id": "lic-1"}],
            )
        ),
        "REVIEW_REQUIRED": v1,
        "NOT_APPLICABLE": ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=v1.id,
                content={"body": "plain"},
                created_by_user_id=v1.created_by_user_id,
            )
        ),
        "SKIPPED": ArtifactService(db_session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=v1.id,
                content={"body": "plain 2"},
                created_by_user_id=v1.created_by_user_id,
            )
        ),
    }
    pass_run = runner_service.run_gate(data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=matrix_versions["PASS"].id))
    review_run = runner_service.run_gate(data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=matrix_versions["REVIEW_REQUIRED"].id))
    na_run = runner_service.run_gate(data=GateRunCreate(gate_key="ai_use_disclosure_gate", target_type="artifact_version", target_id=matrix_versions["NOT_APPLICABLE"].id))
    skipped_run = runner_service.run_gate(data=GateRunCreate(gate_key="platform_originality_gate", target_type="artifact_version", target_id=matrix_versions["SKIPPED"].id))
    definition = GateDefinitionService(db_session).get_active_gate_version("publish_risk_gate")
    db_session.add(
        GateRun(
            gate_definition_version_id=definition.id,
            gate_key="manual_block_gate",
            target_type="artifact_version",
            target_id=v1.id,
            video_project_id=project.id,
            artifact_version_id=v1.id,
            policy_snapshot_id=project.policy_snapshot_id,
            input_snapshot={"target": {"target_type": "artifact_version", "target_id": str(v1.id)}},
            input_snapshot_hash="prem4-manual-block",
            result="BLOCK",
            reason_codes=["MANUAL_REVIEW_REQUIRED"],
            freshness_state="UNKNOWN",
            confidence_level="HIGH",
            decision_basis={"manual_test": True},
        )
    )
    db_session.flush()
    block_run = runner_service.run_gate(data=GateRunCreate(gate_key="publish_risk_gate", target_type="artifact_version", target_id=v1.id))
    assert {pass_run.result, review_run.result, na_run.result, skipped_run.result, block_run.result} == {
        "PASS",
        "REVIEW_REQUIRED",
        "NOT_APPLICABLE",
        "SKIPPED",
        "BLOCK",
    }
    for run in [pass_run, review_run, na_run, skipped_run, block_run]:
        assert run.gate_definition_version_id
        assert run.target_id
        assert run.input_snapshot_hash
        assert run.reason_codes
        assert isinstance(run.evidence_refs, list)
        assert isinstance(run.metric_refs, list)
        assert run.freshness_state
        assert run.confidence_level
        assert run.decision_basis["llm_used"] is False
    before = {
        "id": str(review_run.id),
        "result": review_run.result,
        "reason_codes": list(review_run.reason_codes),
        "input_snapshot_hash": review_run.input_snapshot_hash,
    }
    change = PolicyChangeService(db_session).create_change_record(
        data=PolicyChangeRecordCreate(
            change_key=f"prem4-change-{uuid.uuid4().hex[:6]}",
            platform="generic",
            policy_domain="legal_compliance",
            summary="manual policy drift",
        )
    )
    batch = PolicyRevalidationService(db_session).create_batch(
        data=PolicyRevalidationBatchCreate(
            policy_change_record_id=change.id,
            scope={"targets": [{"target_type": "artifact_version", "target_id": str(v1.id), "gate_key": "rights_copyright_gate"}]},
        )
    )
    PolicyRevalidationService(db_session).run_batch(batch.id)
    db_session.refresh(review_run)
    after = {
        "id": str(review_run.id),
        "result": review_run.result,
        "reason_codes": list(review_run.reason_codes),
        "input_snapshot_hash": review_run.input_snapshot_hash,
    }
    assert after == before
    assert db_session.query(GateRun).filter_by(gate_key="rights_copyright_gate", target_id=v1.id).count() == 2


def test_policy_lifecycle_source_refs_and_batch_failure(db_session) -> None:
    service = PolicyCatalogService(db_session)
    catalog = service.create_catalog(
        data=PlatformPolicyCatalogCreate(
            catalog_key=f"prem4_policy_{uuid.uuid4().hex[:6]}",
            platform="generic",
            policy_domain="privacy",
        )
    )
    v1 = service.create_version(data=PlatformPolicyVersionCreate(catalog_id=catalog.id, version="1.0.0", policy_blob={"rules": ["a"]}))
    source = service.attach_source_ref(
        data=PolicySourceRefCreate(policy_version_id=v1.id, source_type="OFFICIAL", reliability="OFFICIAL", source_url="https://example.test/policy")
    )
    service.activate_version(v1.id)
    v2 = service.create_version(data=PlatformPolicyVersionCreate(catalog_id=catalog.id, version="1.0.1", policy_blob={"rules": ["b"]}))
    service.activate_version(v2.id)
    db_session.refresh(v1)
    db_session.refresh(catalog)
    assert source.policy_version_id == v1.id
    assert v1.status == "superseded"
    assert catalog.current_version_id == v2.id
    v2.policy_blob = {"mutated": True}
    with pytest.raises(DBAPIError):
        db_session.commit()
    db_session.rollback()
    batch = PolicyRevalidationService(db_session).create_batch(data=PolicyRevalidationBatchCreate(scope={"targets": []}))
    with pytest.raises(ValidationFailureError):
        PolicyRevalidationService(db_session).run_batch(batch.id)
    db_session.refresh(batch)
    assert batch.status == "PENDING"


def test_gate_read_contract_rejects_malformed_result() -> None:
    valid = {
        "id": str(uuid.uuid4()),
        "gate_definition_version_id": str(uuid.uuid4()),
        "gate_key": "rights_copyright_gate",
        "target_type": "artifact_version",
        "target_id": str(uuid.uuid4()),
        "video_project_id": str(uuid.uuid4()),
        "artifact_version_id": str(uuid.uuid4()),
        "review_task_id": None,
        "policy_snapshot_id": str(uuid.uuid4()),
        "input_snapshot": {"target": "x"},
        "input_snapshot_hash": "hash",
        "result": "PASS",
        "reason_codes": ["SYSTEM_OK"],
        "evidence_refs": [],
        "metric_refs": [],
        "freshness_state": "FRESH",
        "confidence_level": "HIGH",
        "confidence_reason_codes": [],
        "decision_basis": {"deterministic": True},
        "created_review_task_id": None,
        "created_by_user_id": None,
        "created_at": "2026-06-24T00:00:00Z",
    }
    GateRunRead.model_validate(valid)
    for field, bad in {
        "reason_codes": [],
        "freshness_state": None,
        "confidence_level": "MAYBE",
        "evidence_refs": None,
    }.items():
        malformed = dict(valid)
        malformed[field] = bad
        with pytest.raises(ValidationError):
            GateRunRead.model_validate(malformed)


def test_api_cli_both_interface_orders_share_state(db_session) -> None:
    *_, project, _, version = _base(db_session)
    db_session.commit()
    client = TestClient(create_app())

    cli_run = runner.invoke(
        cli_app,
        ["gate", "run", "--gate-key", "rights_copyright_gate", "--target-type", "artifact_version", "--target-id", str(version.id)],
    )
    assert cli_run.exit_code == 0, cli_run.output
    readiness_after_cli = client.get(f"/video-projects/{project.id}/readiness")
    assert readiness_after_cli.status_code == 200, readiness_after_cli.text
    assert readiness_after_cli.json()["counts"]["REVIEW_REQUIRED"] >= 1

    api_run = client.post(
        "/gates/run",
        json={"gate_key": "privacy_retention_gate", "target_type": "artifact_version", "target_id": str(version.id)},
    )
    assert api_run.status_code == 200, api_run.text
    inspected = runner.invoke(cli_app, ["gate", "inspect", "--gate-run-id", api_run.json()["id"]])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["id"] == api_run.json()["id"]


def test_no_network_provider_llm_possible_on_compile_and_gate(monkeypatch, db_session) -> None:
    *_, version = _base(db_session)

    def fail_network(*args, **kwargs):  # pragma: no cover - only called on regression
        raise AssertionError("network/provider call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    monkeypatch.setattr(socket.socket, "connect", fail_network)
    GateRunnerService(db_session).run_gate(
        data=GateRunCreate(gate_key="rights_copyright_gate", target_type="artifact_version", target_id=version.id)
    )
    assert True


def test_scope_guard_scans_schema_routes_cli_services_and_imports(engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_M0_M3_TABLES <= tables
    assert EXPECTED_M4_TABLES <= tables
    assert not {table for table in tables for fragment in FORBIDDEN_TABLE_FRAGMENTS if fragment in table}
    app_text = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app").rglob("*.py"))
    for term in FORBIDDEN_FUTURE_TERMS:
        assert term not in app_text
    forbidden_imports = {"openai", "anthropic", "requests", "httpx", "urllib.request", "boto3"}
    for forbidden in forbidden_imports:
        assert f"import {forbidden}" not in app_text
        assert f"from {forbidden}" not in app_text
    routes = {route.path for route in create_app().routes}
    assert not {route for route in routes if any(fragment in route for fragment in ["rag", "vector", "render", "publish", "analytics", "dashboard"])}
