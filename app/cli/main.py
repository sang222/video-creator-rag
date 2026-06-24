import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.core.db import check_database
from app.db.session import session_scope
from app.contracts import (
    ApprovalDecisionCreate,
    ArtifactCreate,
    ArtifactVersionCreate,
    BudgetGateCheckRequest,
    BudgetPolicyCreate,
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
    ComponentHealthSnapshotCreate,
    CostEventCreate,
    CredentialHealthSnapshotCreate,
    CredentialReferenceCreate,
    DailyIdeaDecisionCreate,
    DailyRunExecuteRequest,
    DeadLetterJobCreate,
    EditorialCalendarSlotCreate,
    GateRunCreate,
    IdeaMarketPreflightCreate,
    ManualActionCreate,
    OpsIncidentCreate,
    PlatformPolicyCatalogCreate,
    PlatformPolicyVersionCreate,
    PolicyChangeRecordCreate,
    PolicyRevalidationBatchCreate,
    PolicySourceRefCreate,
    ProviderAttemptMockRequest,
    ProviderRegistryEntryCreate,
    ProjectAdmissionDecisionCreate,
    QuotaAccountCreate,
    QuotaEventRequest,
    RetrievalPlanSnapshotCreate,
    ReviewFindingCreate,
    ReviewTaskCreate,
    RevisionRequestCreate,
    RetryPolicyCreate,
    SearchDemandEvidenceCreate,
    VideoProjectCreate,
    ChannelDailyRunCreate,
    ChannelStatePackSnapshotCreate,
    ContextPackSnapshotCreate,
)
from app.services import (
    ApprovalService,
    ArtifactService,
    AuditService,
    BudgetGateService,
    ChannelAuthorityService,
    ChannelDailyRunService,
    ChannelStatePackService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ComponentHealthService,
    ConfigRegistryService,
    CostService,
    CredentialReferenceService,
    DeadLetterService,
    EditorialCalendarService,
    GateDefinitionService,
    GateRunnerService,
    IdeaMarketPreflightService,
    ManualActionService,
    OpsIncidentService,
    PolicySnapshotService,
    PolicyCatalogService,
    PolicyChangeService,
    PolicyRevalidationService,
    ProviderHealthService,
    ProviderRegistryService,
    ProjectAdmissionService,
    QuotaService,
    ResourceResolverService,
    RetryOpsService,
    ReviewService,
    SearchDemandEvidenceService,
    SystemHealthService,
    VideoProjectService,
    WorkflowReadinessService,
)

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
company_app = typer.Typer(no_args_is_help=True)
channel_app = typer.Typer(no_args_is_help=True)
profile_app = typer.Typer(no_args_is_help=True)
project_app = typer.Typer(no_args_is_help=True)
artifact_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
revision_app = typer.Typer(no_args_is_help=True)
approval_app = typer.Typer(no_args_is_help=True)
workflow_app = typer.Typer(no_args_is_help=True)
gate_app = typer.Typer(no_args_is_help=True)
policy_app = typer.Typer(no_args_is_help=True)
readiness_app = typer.Typer(no_args_is_help=True)
calendar_app = typer.Typer(no_args_is_help=True)
search_app = typer.Typer(no_args_is_help=True)
context_app = typer.Typer(no_args_is_help=True)
channel_state_app = typer.Typer(no_args_is_help=True)
daily_app = typer.Typer(no_args_is_help=True)
idea_app = typer.Typer(no_args_is_help=True)
provider_app = typer.Typer(no_args_is_help=True)
credential_app = typer.Typer(no_args_is_help=True)
quota_app = typer.Typer(no_args_is_help=True)
cost_app = typer.Typer(no_args_is_help=True)
budget_app = typer.Typer(no_args_is_help=True)
dead_letter_app = typer.Typer(no_args_is_help=True)
incident_app = typer.Typer(no_args_is_help=True)
manual_action_app = typer.Typer(no_args_is_help=True)
system_health_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")
app.add_typer(audit_app, name="audit")
app.add_typer(company_app, name="company")
app.add_typer(channel_app, name="channel")
app.add_typer(profile_app, name="profile")
app.add_typer(project_app, name="project")
app.add_typer(artifact_app, name="artifact")
app.add_typer(review_app, name="review")
app.add_typer(revision_app, name="revision")
app.add_typer(approval_app, name="approval")
app.add_typer(workflow_app, name="workflow")
app.add_typer(gate_app, name="gate")
app.add_typer(policy_app, name="policy")
app.add_typer(readiness_app, name="readiness")
app.add_typer(calendar_app, name="calendar")
app.add_typer(search_app, name="search")
app.add_typer(context_app, name="context")
app.add_typer(channel_state_app, name="channel-state")
app.add_typer(daily_app, name="daily")
app.add_typer(idea_app, name="idea")
app.add_typer(provider_app, name="provider")
app.add_typer(credential_app, name="credential")
app.add_typer(quota_app, name="quota")
app.add_typer(cost_app, name="cost")
app.add_typer(budget_app, name="budget")
app.add_typer(dead_letter_app, name="dead-letter")
app.add_typer(incident_app, name="incident")
app.add_typer(manual_action_app, name="manual-action")
app.add_typer(system_health_app, name="system-health")


def _fail(message: str) -> None:
    typer.secho(message, err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


@db_app.command("migrate")
def migrate() -> None:
    try:
        command.upgrade(Config("alembic.ini"), "head")
    except Exception as exc:
        _fail(f"migration failed: {exc}")
    typer.echo("migration ok")


@config_app.command("seed")
def seed(config_dir: Path = typer.Option(Path("config"), "--config-dir")) -> None:
    try:
        with session_scope() as session:
            records = ConfigRegistryService(session).seed([config_dir])
    except Exception as exc:
        _fail(f"config seed failed: {exc}")
    typer.echo(f"config seed ok: {len(records)} catalogs")


@app.command("audit-tail")
def audit_tail_alias(
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    company_id: uuid.UUID | None = typer.Option(None, "--company-id"),
) -> None:
    audit_tail(limit=limit, company_id=company_id)


@app.command("health")
def health() -> None:
    try:
        settings = get_settings()
        check_database(settings.database_url)
    except Exception as exc:
        _fail(f"health failed: {exc}")
    typer.echo("health ok")


@company_app.command("create")
def company_create(
    name: str = typer.Option(..., "--name"),
    default_currency: str = typer.Option("USD", "--default-currency"),
) -> None:
    try:
        with session_scope() as session:
            company = CompanyService(session).create_company(
                name=name,
                default_currency=default_currency,
            )
            typer.echo(json.dumps({"id": str(company.id), "name": company.name}))
    except Exception as exc:
        _fail(f"company create failed: {exc}")


@channel_app.command("create")
def channel_create(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    key: str = typer.Option(..., "--key"),
    name: str = typer.Option(..., "--name"),
    primary_language: str = typer.Option("en", "--primary-language"),
    default_timezone: str = typer.Option("UTC", "--default-timezone"),
) -> None:
    try:
        with session_scope() as session:
            channel = ChannelWorkspaceService(session).create_channel(
                company_id=company_id,
                data=ChannelWorkspaceCreate(
                    key=key,
                    name=name,
                    primary_language=primary_language,
                    default_timezone=default_timezone,
                ),
            )
            typer.echo(json.dumps({"id": str(channel.id), "key": channel.key}))
    except Exception as exc:
        _fail(f"channel create failed: {exc}")


@profile_app.command("create")
def profile_create(
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    template_key: str = typer.Option("saas_digital_leverage", "--template-key"),
) -> None:
    try:
        with session_scope() as session:
            profile = ChannelProfileService(session).create_profile_version(
                channel_id=channel_id,
                data=ChannelProfileVersionCreate(template_key=template_key),
            )
            typer.echo(
                json.dumps(
                    {
                        "id": str(profile.id),
                        "version": profile.version,
                        "profile_input_hash": profile.profile_input_hash,
                    }
                )
            )
    except Exception as exc:
        _fail(f"profile create failed: {exc}")


@profile_app.command("compile")
def profile_compile(
    profile_version_id: uuid.UUID = typer.Option(..., "--profile-version-id"),
    correlation_id: str | None = typer.Option(None, "--correlation-id"),
) -> None:
    try:
        with session_scope() as session:
            result = ChannelProfileCompiler(session).compile(
                profile_version_id=profile_version_id,
                correlation_id=correlation_id or f"cli-compile-{profile_version_id}",
            )
            typer.echo(json.dumps(result.model_dump(mode="json")))
    except Exception as exc:
        _fail(f"profile compile failed: {exc}")


@profile_app.command("activate")
def profile_activate(snapshot_id: uuid.UUID = typer.Option(..., "--snapshot-id")) -> None:
    try:
        with session_scope() as session:
            snapshot = ChannelProfileService(session).activate_snapshot(snapshot_id=snapshot_id)
            typer.echo(
                json.dumps(
                    {
                        "snapshot_id": str(snapshot.id),
                        "channel_id": str(snapshot.channel_workspace_id),
                        "status": snapshot.status,
                    }
                )
            )
    except Exception as exc:
        _fail(f"profile activate failed: {exc}")


@profile_app.command("active")
def profile_active(channel_id: uuid.UUID = typer.Option(..., "--channel-id")) -> None:
    try:
        with session_scope() as session:
            snapshot = PolicySnapshotService(session).get_active_snapshot_for_channel(channel_id)
            if snapshot is None:
                typer.echo("null")
            else:
                typer.echo(
                    json.dumps(
                        {
                            "snapshot_id": str(snapshot.id),
                            "content_hash": snapshot.content_hash,
                            "status": snapshot.status,
                        }
                    )
                )
    except Exception as exc:
        _fail(f"profile active failed: {exc}")


@project_app.command("create")
def project_create(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    policy_snapshot_id: uuid.UUID = typer.Option(..., "--policy-snapshot-id"),
    title: str = typer.Option(..., "--title"),
    created_by_user_id: uuid.UUID = typer.Option(..., "--created-by-user-id"),
    description: str | None = typer.Option(None, "--description"),
) -> None:
    try:
        with session_scope() as session:
            project = VideoProjectService(session).create_project(
                data=VideoProjectCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    policy_snapshot_id=policy_snapshot_id,
                    title=title,
                    description=description,
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(project.id), "policy_snapshot_id": str(project.policy_snapshot_id)}))
    except Exception as exc:
        _fail(f"project create failed: {exc}")

@artifact_app.command("create")
def artifact_create(
    project_id: uuid.UUID = typer.Option(..., "--project-id"),
    artifact_type: str = typer.Option(..., "--artifact-type"),
    created_by_user_id: uuid.UUID = typer.Option(..., "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            artifact = ArtifactService(session).create_artifact(
                data=ArtifactCreate(
                    video_project_id=project_id,
                    artifact_type=artifact_type,
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(artifact.id), "artifact_type": artifact.artifact_type}))
    except Exception as exc:
        _fail(f"artifact create failed: {exc}")

@artifact_app.command("version-create")
def artifact_version_create(
    artifact_id: uuid.UUID = typer.Option(..., "--artifact-id"),
    created_by_user_id: uuid.UUID = typer.Option(..., "--created-by-user-id"),
    content_json: str = typer.Option("{}", "--content-json"),
    parent_version_id: uuid.UUID | None = typer.Option(None, "--parent-version-id"),
) -> None:
    try:
        with session_scope() as session:
            version = ArtifactService(session).create_artifact_version(
                data=ArtifactVersionCreate(
                    artifact_id=artifact_id,
                    parent_version_id=parent_version_id,
                    content=_json_object(content_json),
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(version.id), "version_number": version.version_number, "content_hash": version.content_hash}))
    except Exception as exc:
        _fail(f"artifact version create failed: {exc}")

@review_app.command("create-task")
def review_create_task(
    project_id: uuid.UUID = typer.Option(..., "--project-id"),
    target_type: str = typer.Option(..., "--target-type"),
    target_id: uuid.UUID = typer.Option(..., "--target-id"),
    review_type: str = typer.Option(..., "--review-type"),
    requested_by_user_id: uuid.UUID = typer.Option(..., "--requested-by-user-id"),
    target_artifact_version_id: uuid.UUID | None = typer.Option(None, "--target-artifact-version-id"),
) -> None:
    try:
        with session_scope() as session:
            task = ReviewService(session).create_review_task(
                data=ReviewTaskCreate(
                    video_project_id=project_id,
                    target_type=target_type,
                    target_id=target_id,
                    target_artifact_version_id=target_artifact_version_id,
                    review_type=review_type,
                    requested_by_user_id=requested_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(task.id), "target_id": str(task.target_id)}))
    except Exception as exc:
        _fail(f"review task create failed: {exc}")

@review_app.command("add-finding")
def review_add_finding(
    review_task_id: uuid.UUID = typer.Option(..., "--review-task-id"),
    severity: str = typer.Option(..., "--severity"),
    reason_code: str = typer.Option(..., "--reason-code"),
    finding_text: str = typer.Option(..., "--finding-text"),
    created_by_user_id: uuid.UUID = typer.Option(..., "--created-by-user-id"),
    evidence_json: str = typer.Option("[]", "--evidence-json"),
) -> None:
    try:
        with session_scope() as session:
            finding = ReviewService(session).add_finding(
                data=ReviewFindingCreate(
                    review_task_id=review_task_id,
                    severity=severity,
                    reason_code=reason_code,
                    finding_text=finding_text,
                    created_by_user_id=created_by_user_id,
                    evidence_refs=_json_list(evidence_json),
                )
            )
            typer.echo(json.dumps({"id": str(finding.id), "severity": finding.severity}))
    except Exception as exc:
        _fail(f"review finding create failed: {exc}")

@revision_app.command("create")
def revision_create(
    review_task_id: uuid.UUID = typer.Option(..., "--review-task-id"),
    target_artifact_version_id: uuid.UUID = typer.Option(..., "--target-artifact-version-id"),
    requested_by_user_id: uuid.UUID = typer.Option(..., "--requested-by-user-id"),
    reason: str = typer.Option(..., "--reason"),
) -> None:
    try:
        with session_scope() as session:
            revision = ReviewService(session).create_revision_request(
                data=RevisionRequestCreate(
                    review_task_id=review_task_id,
                    target_artifact_version_id=target_artifact_version_id,
                    requested_by_user_id=requested_by_user_id,
                    reason=reason,
                )
            )
            typer.echo(json.dumps({"id": str(revision.id), "status": revision.status}))
    except Exception as exc:
        _fail(f"revision create failed: {exc}")

@revision_app.command("resolve")
def revision_resolve(
    revision_request_id: uuid.UUID = typer.Option(..., "--revision-request-id"),
    resolved_by_artifact_version_id: uuid.UUID = typer.Option(..., "--resolved-by-artifact-version-id"),
) -> None:
    try:
        with session_scope() as session:
            revision = ReviewService(session).resolve_revision_request(
                revision_request_id=revision_request_id,
                resolved_by_artifact_version_id=resolved_by_artifact_version_id,
            )
            typer.echo(json.dumps({"id": str(revision.id), "status": revision.status}))
    except Exception as exc:
        _fail(f"revision resolve failed: {exc}")

@approval_app.command("decide")
def approval_decide(
    target_type: str = typer.Option(..., "--target-type"),
    target_id: uuid.UUID = typer.Option(..., "--target-id"),
    decision: str = typer.Option(..., "--decision"),
    decided_by_user_id: uuid.UUID = typer.Option(..., "--decided-by-user-id"),
    target_artifact_version_id: uuid.UUID | None = typer.Option(None, "--target-artifact-version-id"),
    rationale: str | None = typer.Option(None, "--rationale"),
) -> None:
    try:
        with session_scope() as session:
            result = ApprovalService(session).create_approval_decision(
                data=ApprovalDecisionCreate(
                    target_type=target_type,
                    target_id=target_id,
                    target_artifact_version_id=target_artifact_version_id,
                    decision=decision,
                    decided_by_user_id=decided_by_user_id,
                    rationale=rationale,
                )
            )
            typer.echo(json.dumps({"id": str(result.id), "decision": result.decision}))
    except Exception as exc:
        _fail(f"approval decision failed: {exc}")

@workflow_app.command("inspect")
def workflow_inspect(project_id: uuid.UUID = typer.Option(..., "--project-id")) -> None:
    try:
        with session_scope() as session:
            state = VideoProjectService(session).inspect_workflow_state(project_id)
            typer.echo(json.dumps(state))
    except Exception as exc:
        _fail(f"workflow inspect failed: {exc}")

@gate_app.command("seed-definitions")
def gate_seed_definitions() -> None:
    try:
        with session_scope() as session:
            records = GateDefinitionService(session).seed_definitions()
            session.commit()
            typer.echo(json.dumps({"count": len(records)}))
    except Exception as exc:
        _fail(f"gate definition seed failed: {exc}")

@gate_app.command("run")
def gate_run(
    gate_key: str = typer.Option(..., "--gate-key"),
    target_type: str = typer.Option(..., "--target-type"),
    target_id: uuid.UUID = typer.Option(..., "--target-id"),
    gate_definition_version_id: uuid.UUID | None = typer.Option(None, "--gate-definition-version-id"),
    created_by_user_id: uuid.UUID | None = typer.Option(None, "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            result = GateRunnerService(session).run_gate(
                data=GateRunCreate(
                    gate_key=gate_key,
                    target_type=target_type,
                    target_id=target_id,
                    gate_definition_version_id=gate_definition_version_id,
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps(_gate_run_to_dict(result)))
    except Exception as exc:
        _fail(f"gate run failed: {exc}")

@gate_app.command("inspect")
def gate_inspect(gate_run_id: uuid.UUID = typer.Option(..., "--gate-run-id")) -> None:
    try:
        with session_scope() as session:
            result = GateRunnerService(session).get_gate_run(gate_run_id)
            if result is None:
                _fail(f"gate run not found: {gate_run_id}")
            typer.echo(json.dumps(_gate_run_to_dict(result)))
    except Exception as exc:
        _fail(f"gate inspect failed: {exc}")

@policy_app.command("catalog-create")
def policy_catalog_create(
    catalog_key: str = typer.Option(..., "--catalog-key"),
    platform: str = typer.Option(..., "--platform"),
    policy_domain: str = typer.Option(..., "--policy-domain"),
) -> None:
    try:
        with session_scope() as session:
            catalog = PolicyCatalogService(session).create_catalog(
                data=PlatformPolicyCatalogCreate(catalog_key=catalog_key, platform=platform, policy_domain=policy_domain)
            )
            typer.echo(json.dumps({"id": str(catalog.id), "catalog_key": catalog.catalog_key}))
    except Exception as exc:
        _fail(f"policy catalog create failed: {exc}")

@policy_app.command("version-create")
def policy_version_create(
    catalog_id: uuid.UUID = typer.Option(..., "--catalog-id"),
    version: str = typer.Option(..., "--version"),
    policy_json: str = typer.Option("{}", "--policy-json"),
    created_by_user_id: uuid.UUID | None = typer.Option(None, "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            record = PolicyCatalogService(session).create_version(
                data=PlatformPolicyVersionCreate(
                    catalog_id=catalog_id,
                    version=version,
                    policy_blob=_json_object(policy_json),
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(record.id), "version": record.version, "status": record.status}))
    except Exception as exc:
        _fail(f"policy version create failed: {exc}")

@policy_app.command("version-activate")
def policy_version_activate(policy_version_id: uuid.UUID = typer.Option(..., "--policy-version-id")) -> None:
    try:
        with session_scope() as session:
            record = PolicyCatalogService(session).activate_version(policy_version_id)
            typer.echo(json.dumps({"id": str(record.id), "status": record.status}))
    except Exception as exc:
        _fail(f"policy version activate failed: {exc}")

@policy_app.command("source-ref-create")
def policy_source_ref_create(
    source_type: str = typer.Option(..., "--source-type"),
    reliability: str = typer.Option("UNKNOWN", "--reliability"),
    policy_version_id: uuid.UUID | None = typer.Option(None, "--policy-version-id"),
    policy_change_record_id: uuid.UUID | None = typer.Option(None, "--policy-change-record-id"),
    source_title: str | None = typer.Option(None, "--source-title"),
    source_url: str | None = typer.Option(None, "--source-url"),
) -> None:
    try:
        with session_scope() as session:
            ref = PolicyCatalogService(session).attach_source_ref(
                data=PolicySourceRefCreate(
                    policy_version_id=policy_version_id,
                    policy_change_record_id=policy_change_record_id,
                    source_type=source_type,
                    reliability=reliability,
                    source_title=source_title,
                    source_url=source_url,
                )
            )
            typer.echo(json.dumps({"id": str(ref.id), "source_type": ref.source_type}))
    except Exception as exc:
        _fail(f"policy source ref create failed: {exc}")

@policy_app.command("change-create")
def policy_change_create(
    change_key: str = typer.Option(..., "--change-key"),
    platform: str = typer.Option(..., "--platform"),
    policy_domain: str = typer.Option(..., "--policy-domain"),
    summary: str = typer.Option(..., "--summary"),
    created_by_user_id: uuid.UUID | None = typer.Option(None, "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            record = PolicyChangeService(session).create_change_record(
                data=PolicyChangeRecordCreate(
                    change_key=change_key,
                    platform=platform,
                    policy_domain=policy_domain,
                    summary=summary,
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps({"id": str(record.id), "state": record.state}))
    except Exception as exc:
        _fail(f"policy change create failed: {exc}")

@policy_app.command("revalidate")
def policy_revalidate(
    scope_json: str = typer.Option(..., "--scope-json"),
    gate_definition_version_id: uuid.UUID | None = typer.Option(None, "--gate-definition-version-id"),
    policy_change_record_id: uuid.UUID | None = typer.Option(None, "--policy-change-record-id"),
    created_by_user_id: uuid.UUID | None = typer.Option(None, "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            service = PolicyRevalidationService(session)
            batch = service.create_batch(
                data=PolicyRevalidationBatchCreate(
                    policy_change_record_id=policy_change_record_id,
                    gate_definition_version_id=gate_definition_version_id,
                    scope=_json_object(scope_json),
                    created_by_user_id=created_by_user_id,
                )
            )
            batch = service.run_batch(batch.id)
            typer.echo(json.dumps({"id": str(batch.id), "status": batch.status, "counts": batch.counts}))
    except Exception as exc:
        _fail(f"policy revalidate failed: {exc}")

@readiness_app.command("inspect")
def readiness_inspect(project_id: uuid.UUID = typer.Option(..., "--project-id")) -> None:
    try:
        with session_scope() as session:
            state = WorkflowReadinessService(session).inspect_project(project_id)
            typer.echo(json.dumps(state))
    except Exception as exc:
        _fail(f"readiness inspect failed: {exc}")

@provider_app.command("seed-mocks")
def provider_seed_mocks() -> None:
    try:
        with session_scope() as session:
            records = ProviderRegistryService(session).seed_mock_providers()
            typer.echo(json.dumps({"count": len(records)}))
    except Exception as exc:
        _fail(f"provider seed failed: {exc}")

@provider_app.command("register")
def provider_register(
    provider_key: str = typer.Option(..., "--provider-key"),
    provider_name: str = typer.Option(..., "--provider-name"),
    provider_type: str = typer.Option(..., "--provider-type"),
    status: str = typer.Option("ACTIVE", "--status"),
    capability_json: str = typer.Option("{}", "--capability-json"),
) -> None:
    try:
        with session_scope() as session:
            entry = ProviderRegistryService(session).create_entry(
                data=ProviderRegistryEntryCreate(
                    provider_key=provider_key,
                    provider_name=provider_name,
                    provider_type=provider_type,
                    status=status,
                    capability_blob=_json_object(capability_json),
                )
            )
            typer.echo(json.dumps(_provider_to_dict(entry)))
    except Exception as exc:
        _fail(f"provider register failed: {exc}")

@provider_app.command("list")
def provider_list() -> None:
    try:
        with session_scope() as session:
            entries = ProviderRegistryService(session).list_entries()
            typer.echo(json.dumps([_provider_to_dict(entry) for entry in entries]))
    except Exception as exc:
        _fail(f"provider list failed: {exc}")

@provider_app.command("health-check")
def provider_health_check(
    provider_key: str = typer.Option(..., "--provider-key"),
    mode: str = typer.Option("success", "--mode"),
) -> None:
    try:
        with session_scope() as session:
            snapshot = ProviderHealthService(session).check_provider(provider_key=provider_key, mode=mode)
            typer.echo(json.dumps(_provider_health_to_dict(snapshot)))
    except Exception as exc:
        _fail(f"provider health-check failed: {exc}")

@provider_app.command("attempt-mock")
def provider_attempt_mock(
    provider_key: str = typer.Option(..., "--provider-key"),
    operation_key: str = typer.Option("contract_test", "--operation-key"),
    mode: str = typer.Option("success", "--mode"),
    attempt_number: int = typer.Option(1, "--attempt-number"),
) -> None:
    try:
        with session_scope() as session:
            attempt = RetryOpsService(session).record_mock_attempt(
                data=ProviderAttemptMockRequest(
                    provider_key=provider_key,
                    operation_key=operation_key,
                    mode=mode,
                    attempt_number=attempt_number,
                )
            )
            typer.echo(json.dumps(_provider_attempt_to_dict(attempt)))
    except Exception as exc:
        _fail(f"provider attempt-mock failed: {exc}")

@credential_app.command("ref-create")
def credential_ref_create(
    provider_key: str = typer.Option(..., "--provider-key"),
    credential_key: str = typer.Option(..., "--credential-key"),
    credential_type: str = typer.Option("API_KEY", "--credential-type"),
    secret_ref: str | None = typer.Option(None, "--secret-ref"),
    status: str = typer.Option("UNKNOWN", "--status"),
) -> None:
    try:
        with session_scope() as session:
            reference = CredentialReferenceService(session).create_reference(
                data=CredentialReferenceCreate(
                    provider_key=provider_key,
                    credential_key=credential_key,
                    credential_type=credential_type,
                    secret_ref=secret_ref,
                    status=status,
                )
            )
            typer.echo(json.dumps(_credential_to_dict(reference)))
    except Exception as exc:
        _fail(f"credential ref-create failed: {exc}")

@credential_app.command("health-check")
def credential_health_check(
    credential_reference_id: uuid.UUID = typer.Option(..., "--credential-reference-id"),
    health_state: str | None = typer.Option(None, "--health-state"),
    next_action: str | None = typer.Option(None, "--next-action"),
) -> None:
    try:
        with session_scope() as session:
            snapshot = CredentialReferenceService(session).check_health(
                data=CredentialHealthSnapshotCreate(
                    credential_reference_id=credential_reference_id,
                    health_state=health_state,
                    next_action=next_action,
                )
            )
            typer.echo(json.dumps(_credential_health_to_dict(snapshot)))
    except Exception as exc:
        _fail(f"credential health-check failed: {exc}")

@quota_app.command("account-create")
def quota_account_create(
    provider_key: str = typer.Option(..., "--provider-key"),
    quota_scope_type: str = typer.Option("GLOBAL", "--quota-scope-type"),
    quota_window: str = typer.Option("DAILY", "--quota-window"),
    quota_limit: str | None = typer.Option(None, "--quota-limit"),
    unit: str = typer.Option("REQUESTS", "--unit"),
) -> None:
    try:
        with session_scope() as session:
            account = QuotaService(session).create_account(
                data=QuotaAccountCreate(
                    provider_key=provider_key,
                    quota_scope_type=quota_scope_type,
                    quota_window=quota_window,
                    quota_limit=Decimal(quota_limit) if quota_limit is not None else None,
                    unit=unit,
                )
            )
            typer.echo(json.dumps(_quota_account_to_dict(account)))
    except Exception as exc:
        _fail(f"quota account-create failed: {exc}")

@quota_app.command("reserve")
def quota_reserve(
    quota_account_id: uuid.UUID = typer.Option(..., "--quota-account-id"),
    amount: str = typer.Option(..., "--amount"),
) -> None:
    _quota_event_command("reserve", quota_account_id, Decimal(amount))

@quota_app.command("consume")
def quota_consume(
    quota_account_id: uuid.UUID = typer.Option(..., "--quota-account-id"),
    amount: str = typer.Option(..., "--amount"),
) -> None:
    _quota_event_command("consume", quota_account_id, Decimal(amount))

@quota_app.command("release")
def quota_release(
    quota_account_id: uuid.UUID = typer.Option(..., "--quota-account-id"),
    amount: str = typer.Option(..., "--amount"),
) -> None:
    _quota_event_command("release", quota_account_id, Decimal(amount))

def _quota_event_command(kind: str, quota_account_id: uuid.UUID, amount: Decimal) -> None:
    try:
        with session_scope() as session:
            service = QuotaService(session)
            request = QuotaEventRequest(quota_account_id=quota_account_id, amount=amount)
            if kind == "reserve":
                event = service.reserve_quota(data=request)
            elif kind == "consume":
                event = service.consume_quota(data=request)
            else:
                event = service.release_quota(data=request)
            typer.echo(json.dumps(_quota_event_to_dict(event)))
    except Exception as exc:
        _fail(f"quota {kind} failed: {exc}")

@cost_app.command("record")
def cost_record(
    provider_key: str = typer.Option(..., "--provider-key"),
    cost_scope_type: str = typer.Option("GLOBAL", "--cost-scope-type"),
    amount: str = typer.Option(..., "--amount"),
    cost_type: str = typer.Option("ESTIMATED", "--cost-type"),
    currency: str = typer.Option("USD", "--currency"),
) -> None:
    try:
        with session_scope() as session:
            event = CostService(session).record_event(
                data=CostEventCreate(
                    provider_key=provider_key,
                    cost_scope_type=cost_scope_type,
                    amount=Decimal(amount),
                    cost_type=cost_type,
                    currency=currency,
                )
            )
            typer.echo(json.dumps(_cost_event_to_dict(event)))
    except Exception as exc:
        _fail(f"cost record failed: {exc}")

@budget_app.command("policy-create")
def budget_policy_create(
    policy_key: str = typer.Option(..., "--policy-key"),
    scope_type: str = typer.Option("GLOBAL", "--scope-type"),
    policy_json: str = typer.Option("{}", "--policy-json"),
    status: str = typer.Option("ACTIVE", "--status"),
) -> None:
    try:
        with session_scope() as session:
            policy = BudgetGateService(session).create_policy(
                data=BudgetPolicyCreate(
                    policy_key=policy_key,
                    scope_type=scope_type,
                    policy_blob=_json_object(policy_json),
                    status=status,
                )
            )
            typer.echo(json.dumps({"id": str(policy.id), "policy_key": policy.policy_key, "status": policy.status}))
    except Exception as exc:
        _fail(f"budget policy-create failed: {exc}")

@budget_app.command("check")
def budget_check(
    policy_key: str = typer.Option(..., "--policy-key"),
    estimated_cost: str | None = typer.Option(None, "--estimated-cost"),
    quota_account_id: uuid.UUID | None = typer.Option(None, "--quota-account-id"),
    quota_amount: str | None = typer.Option(None, "--quota-amount"),
) -> None:
    try:
        with session_scope() as session:
            decision = BudgetGateService(session).check(
                data=BudgetGateCheckRequest(
                    policy_key=policy_key,
                    estimated_cost=Decimal(estimated_cost) if estimated_cost is not None else None,
                    quota_account_id=quota_account_id,
                    quota_amount=Decimal(quota_amount) if quota_amount is not None else None,
                )
            )
            typer.echo(json.dumps(decision.model_dump(mode="json")))
    except Exception as exc:
        _fail(f"budget check failed: {exc}")

@dead_letter_app.command("create")
def dead_letter_create(
    queue_name: str = typer.Option(..., "--queue-name"),
    job_type: str = typer.Option(..., "--job-type"),
    payload_ref: str | None = typer.Option(None, "--payload-ref"),
    fail_count: int = typer.Option(1, "--fail-count"),
    reason_code: str | None = typer.Option("DEAD_LETTER_CREATED", "--reason-code"),
    next_action: str | None = typer.Option("Review and replay if safe.", "--next-action"),
) -> None:
    try:
        with session_scope() as session:
            job = DeadLetterService(session).create_job(
                data=DeadLetterJobCreate(
                    queue_name=queue_name,
                    job_type=job_type,
                    payload_ref=payload_ref,
                    fail_count=fail_count,
                    reason_code=reason_code,
                    next_action=next_action,
                )
            )
            typer.echo(json.dumps(_dead_letter_to_dict(job)))
    except Exception as exc:
        _fail(f"dead-letter create failed: {exc}")

@dead_letter_app.command("replay")
def dead_letter_replay(job_id: uuid.UUID = typer.Option(..., "--job-id")) -> None:
    try:
        with session_scope() as session:
            job = DeadLetterService(session).replay_job(job_id)
            typer.echo(json.dumps(_dead_letter_to_dict(job)))
    except Exception as exc:
        _fail(f"dead-letter replay failed: {exc}")

@incident_app.command("create")
def incident_create(
    incident_type: str = typer.Option(..., "--incident-type"),
    severity: str = typer.Option("WARNING", "--severity"),
    next_action: str = typer.Option(..., "--next-action"),
    reason_code: str | None = typer.Option(None, "--reason-code"),
) -> None:
    try:
        with session_scope() as session:
            incident = OpsIncidentService(session).create_incident(
                data=OpsIncidentCreate(
                    incident_type=incident_type,
                    severity=severity,
                    next_action=next_action,
                    reason_codes=[reason_code] if reason_code else [],
                )
            )
            typer.echo(json.dumps(_incident_to_dict(incident)))
    except Exception as exc:
        _fail(f"incident create failed: {exc}")

@incident_app.command("list")
def incident_list() -> None:
    try:
        with session_scope() as session:
            typer.echo(json.dumps([_incident_to_dict(item) for item in OpsIncidentService(session).list_incidents()]))
    except Exception as exc:
        _fail(f"incident list failed: {exc}")

@incident_app.command("ack")
def incident_ack(incident_id: uuid.UUID = typer.Option(..., "--incident-id")) -> None:
    _incident_transition_command(incident_id, "ACKNOWLEDGED")

@incident_app.command("resolve")
def incident_resolve(incident_id: uuid.UUID = typer.Option(..., "--incident-id")) -> None:
    _incident_transition_command(incident_id, "RESOLVED")

def _incident_transition_command(incident_id: uuid.UUID, state: str) -> None:
    try:
        with session_scope() as session:
            incident = OpsIncidentService(session).transition(incident_id, state)
            typer.echo(json.dumps(_incident_to_dict(incident)))
    except Exception as exc:
        _fail(f"incident transition failed: {exc}")

@manual_action_app.command("create")
def manual_action_create(
    action_type: str = typer.Option(..., "--action-type"),
    target_type: str = typer.Option(..., "--target-type"),
    next_action: str = typer.Option(..., "--next-action"),
    priority: str = typer.Option("MEDIUM", "--priority"),
    reason_code: str | None = typer.Option(None, "--reason-code"),
) -> None:
    try:
        with session_scope() as session:
            action = ManualActionService(session).create_action(
                data=ManualActionCreate(
                    action_type=action_type,
                    target_type=target_type,
                    priority=priority,
                    next_action=next_action,
                    reason_code=reason_code,
                )
            )
            typer.echo(json.dumps(_manual_action_to_dict(action)))
    except Exception as exc:
        _fail(f"manual-action create failed: {exc}")

@manual_action_app.command("list")
def manual_action_list() -> None:
    try:
        with session_scope() as session:
            typer.echo(json.dumps([_manual_action_to_dict(item) for item in ManualActionService(session).list_actions()]))
    except Exception as exc:
        _fail(f"manual-action list failed: {exc}")

@manual_action_app.command("complete")
def manual_action_complete(action_id: uuid.UUID = typer.Option(..., "--action-id")) -> None:
    try:
        with session_scope() as session:
            action = ManualActionService(session).complete_action(action_id)
            typer.echo(json.dumps(_manual_action_to_dict(action)))
    except Exception as exc:
        _fail(f"manual-action complete failed: {exc}")

@system_health_app.command("component")
def system_health_component(
    component_type: str = typer.Option(..., "--component-type"),
    component_key: str = typer.Option(..., "--component-key"),
    health_state: str = typer.Option(..., "--health-state"),
    next_action: str | None = typer.Option(None, "--next-action"),
) -> None:
    try:
        with session_scope() as session:
            snapshot = ComponentHealthService(session).create_snapshot(
                data=ComponentHealthSnapshotCreate(
                    component_type=component_type,
                    component_key=component_key,
                    health_state=health_state,
                    next_action=next_action,
                )
            )
            typer.echo(json.dumps({"id": str(snapshot.id), "health_state": snapshot.health_state}))
    except Exception as exc:
        _fail(f"system-health component failed: {exc}")

@system_health_app.command("snapshot")
def system_health_snapshot() -> None:
    try:
        with session_scope() as session:
            snapshot = SystemHealthService(session).create_snapshot()
            typer.echo(json.dumps(_system_health_to_dict(snapshot)))
    except Exception as exc:
        _fail(f"system-health snapshot failed: {exc}")

@system_health_app.command("latest")
def system_health_latest() -> None:
    try:
        with session_scope() as session:
            snapshot = SystemHealthService(session).latest()
            typer.echo("null" if snapshot is None else json.dumps(_system_health_to_dict(snapshot)))
    except Exception as exc:
        _fail(f"system-health latest failed: {exc}")

@calendar_app.command("slot-create")
def calendar_slot_create(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    policy_snapshot_id: uuid.UUID = typer.Option(..., "--policy-snapshot-id"),
    slot_date: str = typer.Option(..., "--slot-date"),
    production_goal: str | None = typer.Option(None, "--production-goal"),
    slot_type: str = typer.Option("DAILY", "--slot-type"),
    target_platforms_json: str = typer.Option("[]", "--target-platforms-json"),
    created_by_user_id: uuid.UUID | None = typer.Option(None, "--created-by-user-id"),
) -> None:
    try:
        with session_scope() as session:
            slot = EditorialCalendarService(session).create_slot(
                data=EditorialCalendarSlotCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    policy_snapshot_id=policy_snapshot_id,
                    slot_date=date.fromisoformat(slot_date),
                    slot_type=slot_type,
                    production_goal=production_goal,
                    target_platforms=_json_string_list(target_platforms_json),
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps(_editorial_slot_to_dict(slot)))
    except Exception as exc:
        _fail(f"calendar slot-create failed: {exc}")

@search_app.command("evidence-create")
def search_evidence_create(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    query: str = typer.Option(..., "--query"),
    source_type: str = typer.Option("MOCK", "--source-type"),
    platform: str = typer.Option("YOUTUBE", "--platform"),
    search_volume_30d: int | None = typer.Option(None, "--search-volume-30d"),
    relative_interest_index: str | None = typer.Option(None, "--relative-interest-index"),
    competition_index: str | None = typer.Option(None, "--competition-index"),
    confidence: str = typer.Option("UNKNOWN", "--confidence"),
) -> None:
    try:
        with session_scope() as session:
            evidence = SearchDemandEvidenceService(session).create_evidence(
                data=SearchDemandEvidenceCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    evidence_source_type=source_type,
                    query=query,
                    platform=platform,
                    search_volume_30d=search_volume_30d,
                    relative_interest_index=Decimal(relative_interest_index) if relative_interest_index is not None else None,
                    competition_index=Decimal(competition_index) if competition_index is not None else None,
                    evidence_confidence=confidence,
                )
            )
            typer.echo(json.dumps(_search_evidence_to_dict(evidence)))
    except Exception as exc:
        _fail(f"search evidence-create failed: {exc}")

@context_app.command("plan-create")
def context_plan_create(
    purpose: str = typer.Option("DAILY_IDEA", "--purpose"),
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID | None = typer.Option(None, "--channel-id"),
    policy_snapshot_id: uuid.UUID | None = typer.Option(None, "--policy-snapshot-id"),
    slot_id: uuid.UUID | None = typer.Option(None, "--slot-id"),
    allowed_sources_json: str = typer.Option("[]", "--allowed-sources-json"),
) -> None:
    try:
        with session_scope() as session:
            sources = _json_string_list(allowed_sources_json)
            plan = ResourceResolverService(session).create_retrieval_plan(
                data=RetrievalPlanSnapshotCreate(
                    purpose=purpose,
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    policy_snapshot_id=policy_snapshot_id,
                    editorial_calendar_slot_id=slot_id,
                    allowed_sources=sources,
                    source_order=sources,
                )
            )
            typer.echo(json.dumps(_retrieval_plan_to_dict(plan)))
    except Exception as exc:
        _fail(f"context plan-create failed: {exc}")

@context_app.command("pack-create")
def context_pack_create(
    retrieval_plan_snapshot_id: uuid.UUID = typer.Option(..., "--retrieval-plan-snapshot-id"),
) -> None:
    try:
        with session_scope() as session:
            pack = ResourceResolverService(session).build_context_pack(
                data=ContextPackSnapshotCreate(retrieval_plan_snapshot_id=retrieval_plan_snapshot_id)
            )
            typer.echo(json.dumps(_context_pack_to_dict(pack)))
    except Exception as exc:
        _fail(f"context pack-create failed: {exc}")

@channel_state_app.command("build")
def channel_state_build(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    policy_snapshot_id: uuid.UUID = typer.Option(..., "--policy-snapshot-id"),
    context_pack_snapshot_id: uuid.UUID | None = typer.Option(None, "--context-pack-snapshot-id"),
    daily_run_id: uuid.UUID | None = typer.Option(None, "--daily-run-id"),
) -> None:
    try:
        with session_scope() as session:
            snapshot = ChannelStatePackService(session).build_snapshot(
                data=ChannelStatePackSnapshotCreate(
                    channel_daily_run_id=daily_run_id,
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    policy_snapshot_id=policy_snapshot_id,
                    context_pack_snapshot_id=context_pack_snapshot_id,
                )
            )
            typer.echo(json.dumps(_channel_state_pack_to_dict(snapshot)))
    except Exception as exc:
        _fail(f"channel-state build failed: {exc}")

@daily_app.command("run-create")
def daily_run_create(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    policy_snapshot_id: uuid.UUID = typer.Option(..., "--policy-snapshot-id"),
    run_date: str = typer.Option(..., "--run-date"),
    slot_id: uuid.UUID | None = typer.Option(None, "--slot-id"),
    trigger_type: str = typer.Option("MANUAL", "--trigger-type"),
) -> None:
    try:
        with session_scope() as session:
            daily_run = ChannelDailyRunService(session).create_run(
                data=ChannelDailyRunCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    policy_snapshot_id=policy_snapshot_id,
                    editorial_calendar_slot_id=slot_id,
                    run_date=date.fromisoformat(run_date),
                    trigger_type=trigger_type,
                )
            )
            typer.echo(json.dumps(_daily_run_to_dict(daily_run)))
    except Exception as exc:
        _fail(f"daily run-create failed: {exc}")

@daily_app.command("execute")
def daily_execute(
    daily_run_id: uuid.UUID = typer.Option(..., "--daily-run-id"),
    mock_mode: str = typer.Option("success", "--mock-mode"),
    quota_account_id: uuid.UUID | None = typer.Option(None, "--quota-account-id"),
    budget_policy_key: str | None = typer.Option(None, "--budget-policy-key"),
    estimated_cost: str = typer.Option("0", "--estimated-cost"),
) -> None:
    try:
        with session_scope() as session:
            daily_run = ChannelDailyRunService(session).execute_run(
                daily_run_id=daily_run_id,
                data=DailyRunExecuteRequest(
                    mock_mode=mock_mode,
                    quota_account_id=quota_account_id,
                    budget_policy_key=budget_policy_key,
                    estimated_cost=Decimal(estimated_cost),
                ),
            )
            typer.echo(json.dumps(_daily_run_to_dict(daily_run)))
    except Exception as exc:
        _fail(f"daily execute failed: {exc}")

@daily_app.command("inspect")
def daily_inspect(daily_run_id: uuid.UUID = typer.Option(..., "--daily-run-id")) -> None:
    try:
        with session_scope() as session:
            daily_run = ChannelDailyRunService(session).get_run(daily_run_id)
            if daily_run is None:
                _fail(f"daily run not found: {daily_run_id}")
            typer.echo(json.dumps(_daily_run_to_dict(daily_run)))
    except Exception as exc:
        _fail(f"daily inspect failed: {exc}")

@idea_app.command("decide")
def idea_decide(
    daily_run_id: uuid.UUID = typer.Option(..., "--daily-run-id"),
    context_pack_snapshot_id: uuid.UUID = typer.Option(..., "--context-pack-snapshot-id"),
    channel_state_pack_snapshot_id: uuid.UUID | None = typer.Option(None, "--channel-state-pack-snapshot-id"),
    mock_mode: str = typer.Option("success", "--mock-mode"),
) -> None:
    try:
        with session_scope() as session:
            decision = ChannelAuthorityService(session).create_decision(
                data=DailyIdeaDecisionCreate(
                    channel_daily_run_id=daily_run_id,
                    context_pack_snapshot_id=context_pack_snapshot_id,
                    channel_state_pack_snapshot_id=channel_state_pack_snapshot_id,
                    mock_mode=mock_mode,
                )
            )
            typer.echo(json.dumps(_daily_idea_to_dict(decision)))
    except Exception as exc:
        _fail(f"idea decide failed: {exc}")

@idea_app.command("preflight")
def idea_preflight(
    company_id: uuid.UUID = typer.Option(..., "--company-id"),
    channel_id: uuid.UUID = typer.Option(..., "--channel-id"),
    daily_run_id: uuid.UUID | None = typer.Option(None, "--daily-run-id"),
    daily_idea_decision_id: uuid.UUID | None = typer.Option(None, "--daily-idea-decision-id"),
    evidence_json: str = typer.Option("{}", "--evidence-json"),
) -> None:
    try:
        with session_scope() as session:
            preflight = IdeaMarketPreflightService(session).create_preflight(
                data=IdeaMarketPreflightCreate(
                    company_id=company_id,
                    channel_workspace_id=channel_id,
                    channel_daily_run_id=daily_run_id,
                    daily_idea_decision_id=daily_idea_decision_id,
                    evidence_blob=_json_object(evidence_json),
                )
            )
            typer.echo(json.dumps(_idea_preflight_to_dict(preflight)))
    except Exception as exc:
        _fail(f"idea preflight failed: {exc}")

@project_app.command("admit")
def project_admit(
    daily_run_id: uuid.UUID = typer.Option(..., "--daily-run-id"),
    daily_idea_decision_id: uuid.UUID = typer.Option(..., "--daily-idea-decision-id"),
    created_by_user_id: uuid.UUID = typer.Option(..., "--created-by-user-id"),
    idea_market_preflight_id: uuid.UUID | None = typer.Option(None, "--idea-market-preflight-id"),
    budget_policy_key: str | None = typer.Option(None, "--budget-policy-key"),
    quota_account_id: uuid.UUID | None = typer.Option(None, "--quota-account-id"),
    estimated_cost: str = typer.Option("0", "--estimated-cost"),
) -> None:
    try:
        with session_scope() as session:
            decision = ProjectAdmissionService(session).create_decision(
                data=ProjectAdmissionDecisionCreate(
                    channel_daily_run_id=daily_run_id,
                    daily_idea_decision_id=daily_idea_decision_id,
                    idea_market_preflight_id=idea_market_preflight_id,
                    budget_policy_key=budget_policy_key,
                    quota_account_id=quota_account_id,
                    estimated_cost=Decimal(estimated_cost),
                    created_by_user_id=created_by_user_id,
                )
            )
            typer.echo(json.dumps(_project_admission_to_dict(decision)))
    except Exception as exc:
        _fail(f"project admit failed: {exc}")

@audit_app.command("tail")
def audit_tail(
    limit: int = typer.Option(50, "--limit", min=1, max=500),
    company_id: uuid.UUID | None = typer.Option(None, "--company-id"),
) -> None:
    try:
        with session_scope() as session:
            events = AuditService(session).tail(limit=limit, company_id=company_id)
            typer.echo(json.dumps([_audit_event_to_dict(event) for event in events], default=str))
    except Exception as exc:
        _fail(f"audit tail failed: {exc}")


def _audit_event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "event_type": event.event_type,
        "actor_type": event.actor_type,
        "actor_id": str(event.actor_id) if event.actor_id else None,
        "target_type": event.target_type,
        "target_id": str(event.target_id) if event.target_id else None,
        "company_id": str(event.company_id) if event.company_id else None,
        "correlation_id": event.correlation_id,
        "reason_code": event.reason_code,
        "payload": event.payload,
        "occurred_at": event.occurred_at.isoformat(),
        "created_at": event.created_at.isoformat(),
    }


def _gate_run_to_dict(gate_run: Any) -> dict[str, Any]:
    return {
        "id": str(gate_run.id),
        "gate_definition_version_id": str(gate_run.gate_definition_version_id),
        "gate_key": gate_run.gate_key,
        "target_type": gate_run.target_type,
        "target_id": str(gate_run.target_id),
        "video_project_id": str(gate_run.video_project_id) if gate_run.video_project_id else None,
        "artifact_version_id": str(gate_run.artifact_version_id) if gate_run.artifact_version_id else None,
        "review_task_id": str(gate_run.review_task_id) if gate_run.review_task_id else None,
        "policy_snapshot_id": str(gate_run.policy_snapshot_id) if gate_run.policy_snapshot_id else None,
        "input_snapshot_hash": gate_run.input_snapshot_hash,
        "result": gate_run.result,
        "reason_codes": gate_run.reason_codes,
        "evidence_refs": gate_run.evidence_refs,
        "metric_refs": gate_run.metric_refs,
        "freshness_state": gate_run.freshness_state,
        "confidence_level": gate_run.confidence_level,
        "confidence_reason_codes": gate_run.confidence_reason_codes,
        "decision_basis": gate_run.decision_basis,
        "created_review_task_id": str(gate_run.created_review_task_id) if gate_run.created_review_task_id else None,
        "created_at": gate_run.created_at.isoformat(),
    }

def _provider_to_dict(entry: Any) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "provider_key": entry.provider_key,
        "provider_name": entry.provider_name,
        "provider_type": entry.provider_type,
        "status": entry.status,
    }

def _provider_health_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "provider_key": snapshot.provider_key,
        "health_state": snapshot.health_state,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
        "checked_at": snapshot.checked_at.isoformat(),
    }

def _provider_attempt_to_dict(attempt: Any) -> dict[str, Any]:
    return {
        "id": str(attempt.id),
        "provider_key": attempt.provider_key,
        "operation_key": attempt.operation_key,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "error_code": attempt.error_code,
        "error_message_redacted": attempt.error_message_redacted,
    }

def _credential_to_dict(reference: Any) -> dict[str, Any]:
    return {
        "id": str(reference.id),
        "provider_key": reference.provider_key,
        "credential_key": reference.credential_key,
        "credential_type": reference.credential_type,
        "secret_ref": reference.secret_ref,
        "status": reference.status,
    }

def _credential_health_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "credential_reference_id": str(snapshot.credential_reference_id),
        "provider_key": snapshot.provider_key,
        "health_state": snapshot.health_state,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
    }

def _quota_account_to_dict(account: Any) -> dict[str, Any]:
    return {
        "id": str(account.id),
        "provider_key": account.provider_key,
        "quota_limit": str(account.quota_limit) if account.quota_limit is not None else None,
        "quota_used": str(account.quota_used),
        "quota_reserved": str(account.quota_reserved),
        "unit": account.unit,
        "status": account.status,
    }

def _quota_event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "quota_account_id": str(event.quota_account_id) if event.quota_account_id else None,
        "provider_key": event.provider_key,
        "event_type": event.event_type,
        "amount": str(event.amount),
        "unit": event.unit,
        "reason_code": event.reason_code,
    }

def _cost_event_to_dict(event: Any) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "provider_key": event.provider_key,
        "cost_scope_type": event.cost_scope_type,
        "amount": str(event.amount),
        "currency": event.currency,
        "cost_type": event.cost_type,
    }

def _dead_letter_to_dict(job: Any) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "queue_name": job.queue_name,
        "job_type": job.job_type,
        "fail_count": job.fail_count,
        "replay_state": job.replay_state,
        "reason_code": job.reason_code,
        "next_action": job.next_action,
    }

def _incident_to_dict(incident: Any) -> dict[str, Any]:
    return {
        "id": str(incident.id),
        "incident_type": incident.incident_type,
        "severity": incident.severity,
        "state": incident.state,
        "reason_codes": incident.reason_codes,
        "next_action": incident.next_action,
    }

def _manual_action_to_dict(action: Any) -> dict[str, Any]:
    return {
        "id": str(action.id),
        "action_type": action.action_type,
        "target_type": action.target_type,
        "priority": action.priority,
        "state": action.state,
        "reason_code": action.reason_code,
        "next_action": action.next_action,
    }

def _system_health_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "overall_state": snapshot.overall_state,
        "component_counts": snapshot.component_counts,
        "active_incident_count": snapshot.active_incident_count,
        "action_required": snapshot.action_required,
        "reason_codes": snapshot.reason_codes,
        "next_action": snapshot.next_action,
    }

def _editorial_slot_to_dict(slot: Any) -> dict[str, Any]:
    return {
        "id": str(slot.id),
        "company_id": str(slot.company_id),
        "channel_workspace_id": str(slot.channel_workspace_id),
        "policy_snapshot_id": str(slot.policy_snapshot_id),
        "slot_date": slot.slot_date.isoformat(),
        "slot_type": slot.slot_type,
        "status": slot.status,
    }

def _search_evidence_to_dict(evidence: Any) -> dict[str, Any]:
    return {
        "id": str(evidence.id),
        "query": evidence.query,
        "platform": evidence.platform,
        "evidence_source_type": evidence.evidence_source_type,
        "search_volume_30d": evidence.search_volume_30d,
        "evidence_confidence": evidence.evidence_confidence,
    }

def _retrieval_plan_to_dict(plan: Any) -> dict[str, Any]:
    return {
        "id": str(plan.id),
        "purpose": plan.purpose,
        "company_id": str(plan.company_id),
        "channel_workspace_id": str(plan.channel_workspace_id) if plan.channel_workspace_id else None,
        "policy_snapshot_id": str(plan.policy_snapshot_id) if plan.policy_snapshot_id else None,
        "allowed_sources": plan.allowed_sources,
        "plan_hash": plan.plan_hash,
    }

def _context_pack_to_dict(pack: Any) -> dict[str, Any]:
    return {
        "id": str(pack.id),
        "retrieval_plan_snapshot_id": str(pack.retrieval_plan_snapshot_id),
        "purpose": pack.purpose,
        "pack_hash": pack.pack_hash,
        "freshness_state": pack.freshness_state,
        "confidence_level": pack.confidence_level,
        "evidence_refs": pack.evidence_refs,
        "metric_refs": pack.metric_refs,
    }

def _channel_state_pack_to_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "id": str(snapshot.id),
        "channel_daily_run_id": str(snapshot.channel_daily_run_id) if snapshot.channel_daily_run_id else None,
        "state_hash": snapshot.state_hash,
        "freshness_state": snapshot.freshness_state,
        "confidence_level": snapshot.confidence_level,
        "evidence_summary": snapshot.evidence_summary,
    }

def _daily_run_to_dict(daily_run: Any) -> dict[str, Any]:
    return {
        "id": str(daily_run.id),
        "status": daily_run.status,
        "run_mode": daily_run.run_mode,
        "run_date": daily_run.run_date.isoformat(),
        "context_pack_snapshot_id": str(daily_run.context_pack_snapshot_id) if daily_run.context_pack_snapshot_id else None,
        "channel_state_pack_snapshot_id": str(daily_run.channel_state_pack_snapshot_id) if daily_run.channel_state_pack_snapshot_id else None,
        "daily_idea_decision_id": str(daily_run.daily_idea_decision_id) if daily_run.daily_idea_decision_id else None,
        "project_admission_decision_id": str(daily_run.project_admission_decision_id) if daily_run.project_admission_decision_id else None,
        "reason_codes": daily_run.reason_codes,
    }

def _daily_idea_to_dict(decision: Any) -> dict[str, Any]:
    return {
        "id": str(decision.id),
        "channel_daily_run_id": str(decision.channel_daily_run_id),
        "llm_run_snapshot_id": str(decision.llm_run_snapshot_id) if decision.llm_run_snapshot_id else None,
        "decision_status": decision.decision_status,
        "proposed_title": decision.proposed_title,
        "confidence_level": decision.confidence_level,
        "reason_codes": decision.reason_codes,
    }

def _idea_preflight_to_dict(preflight: Any) -> dict[str, Any]:
    return {
        "id": str(preflight.id),
        "decision": preflight.decision,
        "demand_score": str(preflight.demand_score) if preflight.demand_score is not None else None,
        "confidence_state": preflight.confidence_state,
        "reason_codes": preflight.reason_codes,
    }

def _project_admission_to_dict(decision: Any) -> dict[str, Any]:
    return {
        "id": str(decision.id),
        "decision": decision.decision,
        "reason_codes": decision.reason_codes,
        "admitted_video_project_id": str(decision.admitted_video_project_id) if decision.admitted_video_project_id else None,
        "created_artifact_refs": decision.created_artifact_refs,
    }

def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("expected JSON object")
    return parsed

def _json_list(value: str) -> list[dict[str, Any]]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("expected JSON list")
    return parsed

def _json_string_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("expected JSON string list")
    return parsed

def main() -> None:
    app()


if __name__ == "__main__":
    main()
