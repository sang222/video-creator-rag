import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.main import create_app


runner = CliRunner()


def test_api_health_still_works() -> None:
    response = TestClient(create_app()).get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "ok"


def test_company_channel_profile_compile_api_smoke() -> None:
    client = TestClient(create_app())
    company = client.post("/companies", json={"name": "API Co", "slug": "api-co"})
    assert company.status_code == 200, company.text
    company_id = company.json()["id"]
    channel = client.post(
        f"/companies/{company_id}/channels",
        json={"key": "api", "name": "API Channel"},
    )
    assert channel.status_code == 200, channel.text
    channel_id = channel.json()["id"]
    profile = client.post(
        f"/channels/{channel_id}/profile-versions",
        json={"template_key": "saas_digital_leverage"},
    )
    assert profile.status_code == 200, profile.text
    compile_response = client.post(
        f"/profile-versions/{profile.json()['id']}/compile",
        json={},
    )
    assert compile_response.status_code == 200, compile_response.text
    assert compile_response.json()["content_hash"]


def test_cli_company_channel_profile_compile_smoke() -> None:
    company = runner.invoke(cli_app, ["company", "create", "--name", "CLI Co", "--slug", "cli-co"])
    assert company.exit_code == 0, company.output
    company_id = json.loads(company.output)["id"]
    channel = runner.invoke(
        cli_app,
        [
            "channel",
            "create",
            "--company-id",
            company_id,
            "--key",
            "cli",
            "--name",
            "CLI Channel",
        ],
    )
    assert channel.exit_code == 0, channel.output
    channel_id = json.loads(channel.output)["id"]
    profile = runner.invoke(
        cli_app,
        [
            "profile",
            "create",
            "--channel-id",
            channel_id,
            "--template-key",
            "saas_digital_leverage",
        ],
    )
    assert profile.exit_code == 0, profile.output
    profile_id = json.loads(profile.output)["id"]
    compiled = runner.invoke(
        cli_app,
        ["profile", "compile", "--profile-version-id", profile_id],
    )
    assert compiled.exit_code == 0, compiled.output
    snapshot_id = json.loads(compiled.output)["snapshot_id"]
    activated = runner.invoke(cli_app, ["profile", "activate", "--snapshot-id", snapshot_id])
    assert activated.exit_code == 0, activated.output
    active = runner.invoke(cli_app, ["profile", "active", "--channel-id", channel_id])
    assert active.exit_code == 0, active.output
    assert json.loads(active.output)["snapshot_id"] == snapshot_id
