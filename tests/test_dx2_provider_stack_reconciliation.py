from __future__ import annotations

import yaml

from app.contracts.dx2 import ProviderStackDriftGuardRead
from app.contracts.r3d8 import ProviderBoundaryPreflightRequest
from app.core.config import Settings
from app.db.models import PaidAttemptLimitRecord, PaidProviderCallLedger, ProviderAttempt, RealSmokeRun
from app.services.dx2 import ProviderStackDriftGuard
from app.services.m2 import ProviderBoundaryPreflight, ProviderCapabilityMatrix, ProviderReadinessM2Service
from app.services.r3d8 import PaidProviderBoundaryService
from app.services.r3d9 import ProviderCostOpsService
from tests.test_r3d8_production_cost_firewall_provider_boundary import (
    _approve,
    _configured_settings,
    _effective,
    _estimate,
    _revision,
    _scope,
    _stage,
)


def _catalog(name: str) -> dict:
    with open(f"config/{name}.yaml", "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_dx2_catalogs_are_canonical_and_no_active_stale_provider_keys() -> None:
    routing = {item["job_type"]: item["provider_key"] for item in _catalog("media_provider_routing_policy_catalog")["items"]}
    assert routing["AI_HERO_GENERATION"] == "luma_api"
    assert routing["AI_METAPHOR_GENERATION"] == "luma_api"
    assert "GOOGLE_VERTEX_VEO" not in routing.values()
    assert "pexels_pixabay_free_fallback" not in routing.values()

    capability_items = _catalog("media_provider_capability_matrix_catalog")["items"]
    active_caps = {
        (item["provider_key"], item["job_type"]): item["capability"]
        for item in capability_items
        if item["capability"] == "SUPPORTED"
    }
    assert active_caps[("creatomate_growth_10k", "LONG_FORM_FINAL_RENDER")] == "SUPPORTED"
    assert active_caps[("creatomate_growth_10k", "TEMPLATE_RENDER")] == "SUPPORTED"
    assert active_caps[("creatomate_growth_10k", "CARD_RENDER")] == "SUPPORTED"
    assert active_caps[("creatomate_growth_10k", "THUMBNAIL_COMPOSITION")] == "SUPPORTED"
    assert active_caps[("creatomate_growth_10k", "SHORT_RENDER")] == "SUPPORTED"
    stale = {"GOOGLE_VERTEX_VEO", "google-vertex-veo", "creatomate_essential_2k", "cloud_final_assembly_renderer_tbd", "pexels_pixabay_free_fallback"}
    assert stale.isdisjoint({item["provider_key"] for item in capability_items if item["capability"] == "SUPPORTED"})


def test_dx2_m2_readiness_and_m12_labels_are_canonical(db_session) -> None:
    settings = _configured_settings()
    readiness = ProviderReadinessM2Service(settings).snapshot()
    provider_keys = {item.provider_key for item in readiness.providers}
    assert {"elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"} <= provider_keys
    assert "google-vertex-veo" not in provider_keys

    matrix = {entry.provider_key: entry for entry in ProviderCapabilityMatrix(settings).entries()}
    assert "SHORT_RENDER" in matrix["creatomate_growth_10k"].capabilities

    from app.services.m12 import ProviderReadinessService

    m12 = ProviderReadinessService(db_session, settings).readiness()
    labels = {item.provider_key: item.provider_name for item in m12.provider_summaries}
    assert labels["luma_api"] == "Luma API"
    assert labels["creatomate_growth_10k"] == "Creatomate Growth 10K"
    assert "google-vertex-veo" not in labels
    assert "cloud-final-renderer" not in labels


def test_dx2_provider_stack_drift_guard_pass_and_detects_stale_fixtures() -> None:
    passed = ProviderStackDriftGuard().check()
    assert passed.status == "PASS"
    assert passed.expected_provider_keys == ["elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"]
    assert set(passed.found_active_provider_keys) >= set(passed.expected_provider_keys)

    stale_veo = ProviderStackDriftGuard(
        catalog_overrides={"media_provider_routing_policy_catalog": [{"job_type": "AI_HERO_GENERATION", "provider_key": "GOOGLE_VERTEX_VEO"}]}
    ).check()
    assert stale_veo.status == "PROVIDER_STACK_DRIFT"
    assert "GOOGLE_VERTEX_VEO" in stale_veo.stale_provider_keys

    stale_renderers = ProviderStackDriftGuard(
        catalog_overrides={
            "media_provider_role_profile_catalog": [
                {"provider_key": "creatomate_essential_2k", "is_enabled": True, "recommendation": "CORE"},
                {"provider_key": "cloud_final_assembly_renderer_tbd", "is_enabled": True, "recommendation": "CORE"},
            ]
        }
    ).check()
    assert stale_renderers.status == "PROVIDER_STACK_DRIFT"
    assert {"creatomate_essential_2k", "cloud_final_assembly_renderer_tbd"} <= set(stale_renderers.stale_provider_keys)


def test_dx2_m2_boundary_accepts_canonical_and_rejects_stale_keys() -> None:
    configured = _configured_settings()
    ok = ProviderBoundaryPreflight(configured).check(
        provider_key="luma_api",
        provider_capability="AI_HERO_VIDEO",
        payload={"duration_seconds": 8},
        human_paid_approval=True,
        real_call_requested=False,
        render_revision_ref="render-rev",
        cost_estimate_ref="estimate",
        paid_attempt_limit_ref="attempt-limit",
    )
    assert ok.status == "PASS"
    assert ok.no_network_call_made is True

    stale = ProviderBoundaryPreflight(configured).check(
        provider_key="google-vertex-veo",
        provider_capability="AI_HERO_VIDEO",
        payload={"duration_seconds": 8},
        human_paid_approval=True,
        real_call_requested=False,
        render_revision_ref="render-rev",
        cost_estimate_ref="estimate",
        paid_attempt_limit_ref="attempt-limit",
    )
    assert stale.status == "BLOCK"
    assert "STALE_PROVIDER_KEY_NOT_ACTIVE" in stale.reason_codes


def test_dx2_cost_estimate_accepts_canonical_luma_and_creatomate(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(
        db_session,
        scope,
        effective,
        provider_plan={
            "provider_stages": [
                _stage("luma_api", "AI_HERO_VIDEO", "2.00"),
                _stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00"),
            ]
        },
    )
    estimate = _estimate(db_session, revision, _configured_settings())
    assert estimate.estimate_status == "ESTIMATED"
    assert "luma_api:ai_hero_video" in estimate.provider_estimates_json
    assert "creatomate_growth_10k:final_assembly_render" in estimate.provider_estimates_json


def test_dx2_provider_cost_read_model_refuses_ready_on_drift(db_session, monkeypatch) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(db_session, scope, effective, provider_plan={"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]})

    class FakeGuard:
        def check(self):
            return ProviderStackDriftGuardRead(
                generated_at=effective.created_at,
                status="PROVIDER_STACK_DRIFT",
                expected_provider_keys=["elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"],
                found_active_provider_keys=["elevenlabs"],
                stale_provider_keys=["GOOGLE_VERTEX_VEO"],
                affected_catalogs={"test": [{"provider_key": "GOOGLE_VERTEX_VEO"}]},
                reason_codes=["STALE_PROVIDER_KEY_ACTIVE"],
                next_action="fix provider stack",
            )

    import app.services.r3d9 as r3d9_module

    monkeypatch.setattr(r3d9_module, "ProviderStackDriftGuard", FakeGuard)
    summary = ProviderCostOpsService(db_session).build(revision.package_id)
    assert summary.provider_readiness["snapshot_state"] == "PROVIDER_STACK_DRIFT"
    assert summary.next_action.next_action_code == "PROVIDER_STACK_DRIFT"
    assert summary.will_execute is False


def test_dx2_allowed_not_executed_does_not_increment_paid_attempt(db_session) -> None:
    scope = _scope(db_session)
    effective, _, _ = _effective(db_session, scope)
    revision = _revision(db_session, scope, effective, provider_plan={"provider_stages": [_stage("creatomate_growth_10k", "FINAL_ASSEMBLY_RENDER", "3.00")]})
    estimate = _estimate(db_session, revision, _configured_settings())
    approval = _approve(db_session, revision, stages=["FINAL_ASSEMBLY_RENDER"])

    decision = PaidProviderBoundaryService(db_session, _configured_settings()).preflight(
        ProviderBoundaryPreflightRequest(
            render_revision_id=revision.id,
            provider_key="creatomate_growth_10k",
            provider_stage="FINAL_ASSEMBLY_RENDER",
            call_type="SUBMIT",
            request_payload_json={"template_id": "tpl-test"},
            cost_estimate_snapshot_id=estimate.id,
            human_approval_id=approval.id,
            real_call_requested=True,
            consume_attempt=True,
        )
    )

    attempt = db_session.get(PaidAttemptLimitRecord, decision.attempt_limit_record_id)
    assert decision.status == "ALLOWED_NOT_EXECUTED"
    assert decision.will_execute is False
    assert attempt is not None
    assert attempt.attempt_count == 0
    assert db_session.query(PaidProviderCallLedger).count() == 1
    assert db_session.query(ProviderAttempt).count() == 0
    assert db_session.query(RealSmokeRun).count() == 0
