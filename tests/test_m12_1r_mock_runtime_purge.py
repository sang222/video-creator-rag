from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from app.cli.main import app as cli_app
from app.contracts.m5 import ChannelDailyRunCreate, DailyRunMode
from app.contracts.m6 import ProductionArtifactRunCreate, ProductionRunMode
from app.contracts.m8 import AnalyticsSyncMode, AnalyticsSyncRunCreate
from app.core.time import utc_now
from app.db.models import LLMRunSnapshot, ProviderAttempt, ProviderRegistryEntry
from app.main import create_app
from app.services.m12 import ProviderReadinessService
from app.services.m12_1r import MockRuntimePurgeService


ROOT = Path(__file__).resolve().parents[1]


def test_openapi_excludes_removed_mock_runtime_routes() -> None:
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]

    assert "/providers/seed-mocks" not in paths
    assert "/provider-attempts/mock" not in paths
    assert client.post("/providers/seed-mocks").status_code in {404, 405}
    assert client.post("/provider-attempts/mock").status_code in {404, 405}


def test_cli_help_excludes_mock_runtime_commands_and_flags() -> None:
    runner = CliRunner()

    provider_help = runner.invoke(cli_app, ["provider", "--help"]).output
    daily_help = runner.invoke(cli_app, ["daily", "execute", "--help"]).output
    production_help = runner.invoke(cli_app, ["production", "run-create", "--help"]).output
    analytics_help = runner.invoke(cli_app, ["analytics", "sync-create", "--help"]).output
    media_help = runner.invoke(cli_app, ["media", "--help"]).output

    assert "seed-mocks" not in provider_help
    assert "attempt-mock" not in provider_help
    assert "--mock-mode" not in daily_help
    assert "MOCK" not in production_help
    assert "MOCK" not in analytics_help
    assert "render-local-smoke" not in media_help


def test_runtime_catalogs_contain_no_mock_provider_entries() -> None:
    for path in (ROOT / "config").glob("*.yaml"):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = json.dumps(payload).lower()
        assert "mock" not in raw, path
        assert "local_fixture" not in raw, path


def test_runtime_defaults_are_not_mock() -> None:
    assert "MOCK" not in get_args(DailyRunMode)
    assert ChannelDailyRunCreate.model_fields["run_mode"].default == "REAL_DISABLED"
    assert "MOCK" not in get_args(ProductionRunMode)
    assert ProductionArtifactRunCreate.model_fields["run_mode"].default == "REAL_DISABLED"
    assert "MOCK" not in get_args(AnalyticsSyncMode)
    assert AnalyticsSyncRunCreate.model_fields["sync_mode"].default == "YOUTUBE_OWNER_ANALYTICS"


def test_readiness_excludes_mock_providers(db_session) -> None:
    payload = ProviderReadinessService(db_session).readiness()
    provider_keys = {summary.provider_key for summary in payload.provider_summaries}

    assert not any(key.startswith("mock_") for key in provider_keys)
    assert provider_keys == {
        "ollama",
        "youtube-public",
        "youtube-owner",
        "google-drive",
        "google-vertex-veo",
        "elevenlabs",
        "creatomate",
        "cloud-final-renderer",
    }
    assert payload.provider_summaries[-1].safe_config["provider"] == "creatomate"


def test_no_production_module_imports_runtime_mock_provider() -> None:
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.as_posix().endswith("app/providers/mock.py"):
            continue
        text = path.read_text(encoding="utf-8")
        if "app.providers.mock" in text or "MockLLMProvider" in text or "MockAnalyticsProvider" in text:
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == []


def test_purge_mock_runtime_dry_run_apply_and_idempotency(db_session) -> None:
    db_session.add(
        ProviderRegistryEntry(
            provider_key="mock_llm",
            provider_name="Mock LLM Provider",
            provider_type="LLM",
            status="ACTIVE",
        )
    )
    db_session.add(
        ProviderAttempt(
            provider_key="mock_llm",
            operation_key="contract_test",
            attempt_number=1,
            status="SUCCESS",
            started_at=utc_now(),
            finished_at=utc_now(),
            metadata_={"mock": True},
        )
    )
    db_session.add(
        LLMRunSnapshot(
            run_type="M5_CHANNEL_AUTHORITY_PROPOSAL",
            provider="mock",
            model_name="mock-llm",
            provider_key="mock_llm",
            model_key="mock-llm",
            run_mode="MOCK",
            prompt_template_key="legacy",
            prompt_template_version="1.0.0",
            input_payload={"legacy": True},
            input_hash="abc",
            output_payload={"ok": True},
            output_hash="def",
            status="COMPLETED",
            correlation_id="m12-1r-test",
        )
    )
    db_session.flush()

    service = MockRuntimePurgeService(db_session)
    preview = service.preview()
    assert preview["before"]["active_mock_providers"] == 1
    assert preview["before"]["mock_provider_attempts"] == 1
    assert preview["before"]["mock_llm_snapshots"] == 1

    applied = service.apply()
    assert applied["after"]["active_mock_providers"] == 0
    assert applied["after"]["mock_provider_attempts"] == 0
    assert applied["after"]["mock_llm_snapshots"] == 0

    second = service.apply()
    assert second["after"] == applied["after"]


def test_purge_cli_dry_run_outputs_counts() -> None:
    result = CliRunner().invoke(cli_app, ["data", "purge-mock-runtime", "--dry-run"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["apply"] is False
    assert "active_mock_providers" in payload["before"]
