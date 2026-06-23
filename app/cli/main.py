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
from app.services.audit import AuditService
from app.services.config_registry import ConfigRegistryService

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(config_app, name="config")
app.add_typer(audit_app, name="audit")


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
