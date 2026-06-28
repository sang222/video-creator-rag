from __future__ import annotations

import json
from dataclasses import replace

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.models import CredentialReference, GoogleDriveMediaCredential, ProviderReadinessSnapshot, RealSmokeRun
from app.main import create_app
from app.services.m10_5 import GOOGLE_DRIVE_SCOPE
from app.services import m12 as m12_module
from app.services.m12 import ProviderReadinessService, RealSmokeOrchestratorService
from tests.qualification.helpers.network_sentinel import install_network_sentinel


def _settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "llm_provider": "ollama",
        "llm_real_execution_enabled": False,
        "llm_router_real_smoke": False,
        "youtube_public_monitor_enabled": True,
        "youtube_data_api_key": None,
        "youtube_owner_analytics_enabled": True,
        "youtube_oauth_client_secrets_file": None,
        "youtube_oauth_client_id": None,
        "youtube_oauth_client_secret": None,
        "google_drive_offload_enabled": True,
        "google_drive_oauth_client_secrets_file": None,
        "google_drive_oauth_client_id": None,
        "google_drive_oauth_client_secret": None,
        "google_drive_root_folder_id": None,
        "veo_real_execution_enabled": False,
        "veo_real_smoke": False,
        "elevenlabs_api_key": None,
        "creatomate_api_key": None,
        "cloud_final_renderer_api_key": None,
        "budget_mode": None,
        "monthly_ai_budget_usd": None,
    }
    base.update(overrides)
    return Settings(**base)


def test_m12_readiness_classifies_missing_config_and_cloud_final_needs_config(db_session) -> None:
    payload = ProviderReadinessService(db_session, _settings()).readiness()

    by_provider = {summary.provider_key: summary for summary in payload.provider_summaries}
    assert payload.snapshot_state == "BLOCKED"
    assert by_provider["ollama"].readiness_state == "WARNING"
    assert by_provider["youtube-public"].readiness_state == "BLOCKED"
    assert by_provider["youtube-owner"].readiness_state == "BLOCKED"
    assert by_provider["google-drive"].readiness_state == "BLOCKED"
    assert by_provider["google-vertex-veo"].safe_config["duration_rules"] == "4,6,8; max 8s"
    assert by_provider["elevenlabs"].readiness_state == "BLOCKED"
    assert by_provider["creatomate"].readiness_state == "BLOCKED"
    assert by_provider["cloud-final-renderer"].readiness_state == "BLOCKED"
    assert "CREATOMATE_GROWTH_10K_REQUIRED" in by_provider["cloud-final-renderer"].reason_codes
    assert "CREATOMATE_API_KEY_MISSING" in by_provider["cloud-final-renderer"].reason_codes
    assert by_provider["cloud-final-renderer"].safe_config["status"] == "NOT_CONFIGURED"
    assert any(item["provider_key"] == "cloud-final-renderer" for item in payload.blocking_items)


def test_m12_cloud_final_renderer_remains_required_gap_even_if_creatomate_env_present(db_session) -> None:
    payload = ProviderReadinessService(
        db_session,
        _settings(
            cloud_final_renderer_provider="creatomate",
            creatomate_plan="growth_10k",
            creatomate_api_key="creatomate-secret",
            cloud_final_renderer_api_key="cloud-final-secret",
        ),
    ).readiness()

    by_provider = {item.provider_key: item for item in payload.provider_summaries}
    cloud = by_provider["cloud-final-renderer"]
    creatomate = by_provider["creatomate"]
    assert cloud.readiness_state == "PASS"
    assert cloud.safe_config["configuration_state"] == "CONFIGURED"
    assert cloud.safe_config["status"] == "READY_FOR_CONFIGURED_PROVIDER"
    assert cloud.safe_config["provider"] == "creatomate"
    assert cloud.safe_config["long_form_final_render_blocked"] is False
    assert "CLOUD_FINAL_RENDERER_READY" in cloud.reason_codes
    assert not any(item["provider_key"] == "cloud-final-renderer" for item in payload.blocking_items)
    assert creatomate.safe_config["role"] == "Shorts/cards/thumbnails"
    assert creatomate.safe_config["not_final_long_form_renderer"] is True
    raw = payload.model_dump_json()
    assert "creatomate-secret" not in raw
    assert "cloud-final-secret" not in raw


def test_m12_budget_cards_are_hard_env_display_only(db_session) -> None:
    settings = _settings(
        budget_mode="hard_env",
        monthly_ai_budget_usd=250,
        llm_monthly_budget_usd=0,
        llm_budget_note="local ollama",
        elevenlabs_plan="creator",
        elevenlabs_monthly_cap_usd=22,
        elevenlabs_monthly_credit_cap=121000,
        elevenlabs_budget_basis="credits_characters",
        veo_monthly_budget_usd=75,
        veo_cost_per_second_1080p_video_only="0.10",
        veo_max_duration_seconds=8,
        creatomate_plan="growth_10k",
        creatomate_monthly_credits=10000,
        creatomate_monthly_budget_usd=149,
        stock_monthly_budget_usd=0,
        music_sfx_monthly_budget_usd=0,
        extra_ai_image_monthly_budget_usd=0,
    )
    payload = ProviderReadinessService(db_session, settings).readiness()
    cards = {card.key: card for card in payload.budget_cards}

    assert cards["total-ai"].configured_monthly_cap == "$250 USD"
    assert cards["google-vertex-veo"].configured_monthly_cap == "$75 USD"
    assert cards["elevenlabs"].budget_basis == "credits_characters"
    assert "remaining" not in json.dumps(payload.model_dump(mode="json")).lower()
    assert "chi phí thực tế" in cards["total-ai"].note


def test_m12_google_drive_readiness_ignores_manual_smoke_failure(db_session, monkeypatch) -> None:
    settings = _settings(
        google_drive_root_folder_id="drive-root",
        google_drive_oauth_client_id="client-id",
        google_drive_oauth_client_secret="client-secret",
    )
    reference = CredentialReference(
        provider_key="google_drive",
        credential_key="media_offload_default",
        credential_type="OAUTH_TOKEN",
        secret_ref="local_file://var/credentials/google-drive/oauth/test.json",
        scope_blob={"scopes": [GOOGLE_DRIVE_SCOPE]},
        status="CONFIGURED",
        metadata_={"storage": "LOCAL_DEV_FILE", "raw_values_in_db": False},
    )
    db_session.add(reference)
    db_session.flush()
    db_session.add(
        GoogleDriveMediaCredential(
            credential_reference_id=reference.id,
            connection_state="CONNECTED",
            scopes=[GOOGLE_DRIVE_SCOPE],
            root_folder_id="drive-root",
        )
    )
    db_session.flush()

    original_evaluate = m12_module.GoogleDriveReadinessCheck.evaluate

    def evaluate_with_failed_smoke(self):
        checks = original_evaluate(self)
        return [
            replace(check, check_state="FAILED", operator_summary="Drive real smoke failed in a guarded test folder.")
            if check.check_type == "REAL_SMOKE"
            else check
            for check in checks
        ]

    monkeypatch.setattr(m12_module.GoogleDriveReadinessCheck, "evaluate", evaluate_with_failed_smoke)

    payload = ProviderReadinessService(db_session, settings).provider_readiness("google-drive")
    drive = payload.provider_summaries[0]

    assert payload.snapshot_state == "READY"
    assert payload.blocking_items == []
    assert payload.warning_items == []
    assert drive.readiness_state == "PASS"
    assert drive.smoke_state == "FAILED"


def test_m12_veo_monthly_cap_env_alias(monkeypatch) -> None:
    monkeypatch.setenv("VCOS_VEO_MONTHLY_CAP_USD", "75")

    settings = Settings(_env_file=None)

    assert settings.veo_monthly_budget_usd == 75


def test_m12_readiness_run_records_snapshot_and_redacts_secrets(db_session) -> None:
    settings = _settings(elevenlabs_api_key="sk-test-secret", creatomate_api_key="creatomate-secret")
    snapshot = ProviderReadinessService(db_session, settings).run()

    assert snapshot.snapshot_state == "BLOCKED"
    assert db_session.query(ProviderReadinessSnapshot).count() == 1
    raw = json.dumps(snapshot.model_dump(mode="json"))
    assert "sk-test-secret" not in raw
    assert "creatomate-secret" not in raw


def test_m12_smoke_guards_skip_without_external_calls(db_session, monkeypatch) -> None:
    install_network_sentinel(monkeypatch)
    settings = _settings()

    ollama = RealSmokeOrchestratorService(db_session, settings).run_provider("ollama")
    drive = RealSmokeOrchestratorService(db_session, settings).run_provider("google-drive")
    veo = RealSmokeOrchestratorService(db_session, settings).run_provider("google-vertex-veo")

    assert ollama.run_state == "SKIPPED"
    assert drive.run_state == "SKIPPED"
    assert veo.run_state == "SKIPPED"
    assert db_session.query(RealSmokeRun).count() == 3
    assert all("secret" not in json.dumps(run.env_flags).lower() for run in db_session.query(RealSmokeRun).all())


def test_m12_cloud_final_renderer_smoke_skips_without_render(db_session, monkeypatch) -> None:
    install_network_sentinel(monkeypatch)
    settings = _settings(
        cloud_final_renderer_provider="creatomate",
        creatomate_plan="growth_10k",
        creatomate_api_key="creatomate-secret",
        cloud_final_renderer_api_key="cloud-final-secret",
    )

    run = RealSmokeOrchestratorService(db_session, settings).run_provider("cloud-final-renderer")

    assert run.run_state == "SKIPPED"
    assert run.technical_appendix["real_final_render_added"] is False
    assert run.error_code is None
    raw = run.model_dump_json()
    assert "creatomate-secret" not in raw
    assert "cloud-final-secret" not in raw


def test_m12_enabled_smoke_blocks_when_credentials_missing(db_session, monkeypatch) -> None:
    install_network_sentinel(monkeypatch)
    settings = _settings(youtube_real_public_smoke=True, youtube_test_video_id="dQw4w9WgXcQ")

    run = RealSmokeOrchestratorService(db_session, settings).run_provider("youtube-public")

    assert run.run_state == "BLOCKED"
    assert run.error_code == "YOUTUBE_PUBLIC_SMOKE_CONFIG_MISSING"


def test_m12_api_exposes_readiness_without_secrets(monkeypatch) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk-raw-secret")
    monkeypatch.setenv("CREATOMATE_API_KEY", "creatomate-raw-secret")
    monkeypatch.setenv("VCOS_LLM_REAL_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("VCOS_LLM_ROUTER_REAL_SMOKE", "false")
    get_settings.cache_clear()
    client = TestClient(create_app())

    response = client.get("/integrations/readiness")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["snapshot_state"] == "BLOCKED"
    assert "sk-raw-secret" not in response.text
    assert "creatomate-raw-secret" not in response.text
    assert any(summary["provider_key"] == "cloud-final-renderer" for summary in payload["provider_summaries"])


def test_m12_scope_guard_no_forbidden_routes() -> None:
    client = TestClient(create_app())
    forbidden = [
        "/youtube/upload",
        "/youtube/publish",
        "/youtube/reupload",
        "/browser/scrape-dashboard",
        "/traffic/fake-engagement",
        "/providers/cloud-final-renderer/select",
        "/tiktok/analytics-loop",
        "/facebook/analytics-loop",
    ]
    for path in forbidden:
        assert client.post(path).status_code in {404, 405}
