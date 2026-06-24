import json
import uuid
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
    ChannelProfileVersionCreate,
    ChannelWorkspaceCreate,
    ReviewFindingCreate,
    ReviewTaskCreate,
    RevisionRequestCreate,
    VideoProjectCreate,
)
from app.services import (
    ApprovalService,
    ArtifactService,
    AuditService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    PolicySnapshotService,
    ReviewService,
    VideoProjectService,
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

def main() -> None:
    app()


if __name__ == "__main__":
    main()
