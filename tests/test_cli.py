import json

from typer.testing import CliRunner

from app.cli.main import app
from app.contracts import AuditEnvelope
from app.services import AuditService

runner = CliRunner()


def test_cli_health_smoke() -> None:
    result = runner.invoke(app, ["health"])
    assert result.exit_code == 0, result.output
    assert "health ok" in result.output


def test_cli_config_seed_smoke() -> None:
    result = runner.invoke(app, ["config", "seed"])
    assert result.exit_code == 0, result.output
    assert "config seed ok" in result.output


def test_cli_audit_tail_smoke(db_session) -> None:
    AuditService(db_session).append(
        AuditEnvelope(
            actor_type="system",
            action="audit.cli_smoke",
            target_type="system",
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id="corr-cli",
        )
    )
    db_session.commit()
    result = runner.invoke(app, ["audit", "tail", "--limit", "5"])
    assert result.exit_code == 0, result.output
    events = json.loads(result.output)
    assert events[0]["event_type"] == "audit.cli_smoke"
