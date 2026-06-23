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
from app.contracts import ChannelProfileVersionCreate, ChannelWorkspaceCreate
from app.services import (
    AuditService,
    ChannelProfileCompiler,
    ChannelProfileService,
    ChannelWorkspaceService,
    CompanyService,
    ConfigRegistryService,
    PolicySnapshotService,
)

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
company_app = typer.Typer(no_args_is_help=True)
channel_app = typer.Typer(no_args_is_help=True)
profile_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")
app.add_typer(audit_app, name="audit")
app.add_typer(company_app, name="company")
app.add_typer(channel_app, name="channel")
app.add_typer(profile_app, name="profile")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
