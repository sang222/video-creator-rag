import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts import ChannelWorkspaceCreate
from app.main import create_app
from app.services import ChannelWorkspaceService
from app.services.company import CompanyService
from app.services.m12_2 import FirstScriptedVideoPackageService


runner = CliRunner()


def test_company_create_api_returns_uuid() -> None:
    response = TestClient(create_app()).post(
        "/companies",
        json={"name": "VCOS Company", "slug": "vcos-company"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert uuid.UUID(payload["id"])
    assert payload["name"] == "VCOS Company"
    assert payload["slug"] == "vcos-company"


def test_companies_create_cli_is_idempotent_by_slug() -> None:
    first = runner.invoke(
        cli_app,
        ["companies", "create", "--name", "VCOS Company", "--slug", "vcos-company"],
    )
    second = runner.invoke(
        cli_app,
        ["companies", "create", "--name", "Renamed VCOS Company", "--slug", "vcos-company"],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert json.loads(first.output)["id"] == json.loads(second.output)["id"]


def test_bootstrap_company_command_returns_existing_uuid_by_slug() -> None:
    first = runner.invoke(
        cli_app,
        ["bootstrap", "company", "--name", "VCOS Company", "--slug", "vcos-company"],
    )
    second = runner.invoke(
        cli_app,
        ["bootstrap", "company", "--name", "Renamed VCOS Company", "--slug", "vcos-company"],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_id = next(line for line in first.output.splitlines() if line.startswith("COMPANY_ID=")).split("=", 1)[1]
    second_id = next(line for line in second.output.splitlines() if line.startswith("COMPANY_ID=")).split("=", 1)[1]
    assert uuid.UUID(first_id)
    assert first_id == second_id


def test_channel_create_rejects_invalid_company_id() -> None:
    response = TestClient(create_app()).post(
        "/channels",
        json={"company_id": str(uuid.uuid4()), "key": "missing-company", "name": "Missing Company"},
    )

    assert response.status_code == 404
    assert "company not found" in response.text


def test_m12_2s_preflight_returns_blocked_needs_company_when_no_company(db_session) -> None:
    preflight = FirstScriptedVideoPackageService(db_session).preflight_full_rehearsal()

    assert preflight.status == "BLOCKED_NEEDS_COMPANY"
    assert preflight.next_action == "Tạo company trước, sau đó tạo channel."


def test_m12_2s_preflight_returns_blocked_needs_channel_when_company_exists(db_session) -> None:
    company = CompanyService(db_session).create_company(name="VCOS Company", slug="vcos-company")
    db_session.flush()

    preflight = FirstScriptedVideoPackageService(db_session).preflight_full_rehearsal()

    assert preflight.status == "BLOCKED_NEEDS_CHANNEL"
    assert preflight.company_id == company.id
    assert preflight.next_action == "Tạo channel bằng Channel Init và compile snapshot."


def test_m12_2s_preflight_returns_blocked_needs_contract_when_channel_has_no_snapshot(db_session) -> None:
    company = CompanyService(db_session).create_company(name="VCOS Company", slug="vcos-company")
    channel = ChannelWorkspaceService(db_session).create_channel(
        company_id=company.id,
        data=ChannelWorkspaceCreate(key="vcos-channel", name="VCOS Channel"),
    )
    db_session.flush()

    preflight = FirstScriptedVideoPackageService(db_session).preflight_full_rehearsal(channel_id=channel.id)

    assert preflight.status == "BLOCKED_NEEDS_CHANNEL_CONTRACT"
    assert preflight.next_action == "Bổ sung field còn thiếu và compile lại ChannelProfileVersion."
    assert preflight.reason_codes == ["ACTIVE_COMPILED_POLICY_SNAPSHOT_MISSING"]


def test_m12_2s_preflight_does_not_call_provider_readiness_before_bootstrap(db_session, monkeypatch) -> None:
    def fail_provider_readiness(*args, **kwargs):
        raise AssertionError("provider readiness must not run before company/channel/contract preflight")

    monkeypatch.setattr("app.services.m12_2.ProviderReadinessService.run", fail_provider_readiness)

    preflight = FirstScriptedVideoPackageService(db_session).preflight_full_rehearsal()

    assert preflight.status == "BLOCKED_NEEDS_COMPANY"


def test_m12_2s0_source_has_no_old_provider_smoke_path() -> None:
    source = Path("app/services/m12_2.py").read_text(encoding="utf-8")

    assert "RealSmokeOrchestratorService" not in source
    assert "run_provider" not in source
