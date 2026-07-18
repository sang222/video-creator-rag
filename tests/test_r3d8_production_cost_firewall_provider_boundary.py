from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.contracts.r3d8 import CostEstimateCreateRequest
from app.core.config import Settings
from app.services.m2 import ProviderReadinessM2Service
from app.services.provider_stack import provider_key_rejection_reasons
from app.services.r3d8 import (
    CostEstimateService,
    EXECUTION_FLAG_BY_PROVIDER,
    GOOGLE_GEMINI_IMAGE_PROVIDER_STAGES,
    MAX_PAID_ATTEMPTS_BY_PROVIDER,
    PAID_PROVIDER_KEYS,
    PAID_PROVIDER_STAGES,
    derive_google_gemini_image_catalog_cost,
)


def test_paid_provider_set_contains_only_current_paid_external_providers():
    assert PAID_PROVIDER_KEYS == {"elevenlabs", "google_veo", "google_gemini_image"}
    assert GOOGLE_GEMINI_IMAGE_PROVIDER_STAGES == {"AI_IMAGE_GENERATION"}
    assert "AI_IMAGE_GENERATION" in PAID_PROVIDER_STAGES
    assert MAX_PAID_ATTEMPTS_BY_PROVIDER == {"google_gemini_image": 1}


def test_execution_flags_preserve_current_provider_safety():
    assert EXECUTION_FLAG_BY_PROVIDER == {
        "elevenlabs": "elevenlabs_real_generation_enabled",
        "google_veo": "veo_real_generation_enabled",
        "google_gemini_image": "gemini_image_real_generation_enabled",
        "pexels_api": "pexels_real_search_enabled",
        "google_drive_archive": "google_drive_real_archive_enabled",
    }


def test_all_provider_execution_defaults_are_off():
    settings = Settings()
    assert not settings.provider_real_execution_enabled
    assert not settings.elevenlabs_real_generation_enabled
    assert not settings.veo_real_generation_enabled
    assert not settings.gemini_image_real_generation_enabled
    assert not settings.pexels_real_search_enabled
    assert not settings.google_drive_real_archive_enabled


def test_unknown_provider_has_no_compatibility_path():
    assert provider_key_rejection_reasons("retired-cloud-renderer") == ["UNKNOWN_PROVIDER_KEY"]


def _gemini_image_cost_item(**overrides):
    item = {
        "provider_key": "google_gemini_image",
        "provider_stage": "AI_IMAGE_GENERATION",
        "price_catalog_version": "2026-07-17",
        "price_catalog_ref": "config://google_gemini_image_model_price_catalog/2026-07-17",
        "model_id": "gemini-3.1-flash-image",
        "image_size": "2K",
        "aspect_ratio": "16:9",
        "output_count": 1,
        "attempt_count": 1,
        "hard_cap": "1.00",
        "approval_amount": "1.00",
        "actual_amount": None,
    }
    item.update(overrides)
    return item


def test_fixture_only_execution_does_not_hide_configured_route_from_cost_firewall():
    settings = Settings(
        _env_file=None,
        gemini_api_key="fixture-only-placeholder",
        gemini_image_provider_route_approved=True,
        gemini_image_real_generation_enabled=False,
        img1_fixture_only=True,
    )
    provider = ProviderReadinessM2Service(settings).provider_map()["google_gemini_image"]

    assert provider.readiness_state == "READY_FOR_HUMAN_PAID_APPROVAL"
    assert "GEMINI_IMAGE_EXECUTION_DISABLED" in provider.blocker_reason_codes
    assert provider.safe_config["execution_enabled"] is False
    assert provider.safe_config["fixture_only"] is True
    assert provider.no_call_was_made is True


def test_gemini_image_cost_is_derived_from_versioned_catalog_with_actual_null():
    result = derive_google_gemini_image_catalog_cost(
        _gemini_image_cost_item(),
        currency="USD",
    )

    assert result.passed is True
    assert result.reason_codes == []
    assert result.details["cost_authority"] == "VERSIONED_MODEL_PRICE_CATALOG"
    assert result.details["price_catalog_version"] == "2026-07-17"
    assert result.details["estimated_cost"] == "0.101"
    assert result.details["estimated_unit_cost"] == "0.101"
    assert result.details["hard_cap"] == "1.00"
    assert result.details["approval_amount"] == "1.00"
    assert result.details["actual_amount"] is None
    assert result.details["output_count"] == 1
    assert result.details["attempt_count"] == 1


def test_r3d8_snapshot_persists_catalog_derived_image_cost_without_new_column():
    revision_id = uuid.uuid4()
    revision = SimpleNamespace(
        id=revision_id,
        video_project_id=uuid.uuid4(),
        package_id=uuid.uuid4(),
        provider_plan_json={"provider_stages": [_gemini_image_cost_item()]},
        revision_status="READY_FOR_COST_ESTIMATE",
    )

    class _Session:
        def __init__(self):
            self.added = []

        def get(self, _model, object_id):
            return revision if object_id == revision_id else None

        def add(self, value):
            self.added.append(value)

        def flush(self):
            return None

    settings = Settings(
        _env_file=None,
        gemini_api_key=None,
        gemini_image_provider_route_approved=True,
        gemini_image_real_generation_enabled=False,
        img1_fixture_only=True,
    )
    snapshot = CostEstimateService(_Session(), settings).create(
        CostEstimateCreateRequest(render_revision_id=revision_id, currency="USD")
    )

    evidence = snapshot.provider_estimates_json[
        "google_gemini_image:ai_image_generation"
    ]
    assert snapshot.estimate_status == "ESTIMATED"
    assert str(snapshot.estimated_total_cost) == "0.101"
    assert evidence["estimated_cost"] == "0.101"
    assert evidence["actual_amount"] is None
    assert evidence["price_catalog_version"] == "2026-07-17"
    assert evidence["configured"] is True
    assert evidence["execution_configured"] is False
    assert revision.revision_status == "COST_ESTIMATED"


@pytest.mark.parametrize(
    ("changes", "reason_code"),
    [
        ({"estimated_cost": "999.00"}, "GEMINI_IMAGE_FREEFORM_ESTIMATE_MISMATCH_CATALOG"),
        ({"actual_amount": "0.101"}, "GEMINI_IMAGE_ACTUAL_COST_MUST_BE_NULL"),
        ({"price_catalog_version": "stale"}, "GEMINI_IMAGE_PRICE_CATALOG_VERSION_MISMATCH"),
        ({"output_count": 2}, "GEMINI_IMAGE_SINGLE_OUTPUT_SINGLE_ATTEMPT_REQUIRED"),
        ({"attempt_count": 2}, "GEMINI_IMAGE_SINGLE_OUTPUT_SINGLE_ATTEMPT_REQUIRED"),
        ({"hard_cap": "0.10"}, "GEMINI_IMAGE_COST_CAP_EXCEEDED"),
        ({"approval_amount": "0.10"}, "GEMINI_IMAGE_COST_CAP_EXCEEDED"),
    ],
)
def test_gemini_image_catalog_cost_fails_closed_on_untrusted_or_unsafe_evidence(
    changes,
    reason_code,
):
    result = derive_google_gemini_image_catalog_cost(
        _gemini_image_cost_item(**changes),
        currency="USD",
    )

    assert result.passed is False
    assert reason_code in result.reason_codes
    assert result.details["actual_amount"] is None
