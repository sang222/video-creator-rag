from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.contracts.google_gemini_image import GeminiImageReadiness
from app.core.config import Settings, get_settings
from app.services.provider_stack import CANONICAL_PROVIDER_KEYS


PROVIDER_KEY = "google_gemini_image"
MODEL_PRICE_CATALOG_FILENAME = "google_gemini_image_model_price_catalog.yaml"


class GoogleGeminiImageReadinessService:
    """Build a configuration-only IMG1 snapshot without probing a provider."""

    def __init__(self, settings: Settings, *, config_root: Path | None = None):
        self.settings = settings
        self.config_root = config_root or Path(__file__).resolve().parents[3] / "config"

    def snapshot(self) -> GeminiImageReadiness:
        route_registered = PROVIDER_KEY in CANONICAL_PROVIDER_KEYS
        catalog_present = (self.config_root / MODEL_PRICE_CATALOG_FILENAME).is_file()
        credential_configured = bool(
            self.settings.gemini_api_key
            and self.settings.gemini_api_key.get_secret_value().strip()
        )
        return GeminiImageReadiness(
            provider_route_registered=route_registered,
            credential_configured=credential_configured,
            model_configured=bool(self.settings.gemini_image_model_id.strip()),
            model_catalog_present=catalog_present,
            route_approval_state=self.settings.gemini_image_provider_route_approved,
            execution_enabled=self.settings.gemini_image_real_generation_enabled,
            fixture_only=self.settings.img1_fixture_only,
            cost_catalog_state="PRESENT" if catalog_present else "MISSING",
            global_kill_switch_open=(
                self.settings.provider_real_execution_enabled
                and self.settings.provider_production_execution_enabled
                and not self.settings.media_provider_calls_disabled
            ),
            provider_kill_switch_open=(
                self.settings.gemini_image_real_generation_enabled
                and not self.settings.img1_fixture_only
            ),
            exact_next_action=self._next_action(
                route_registered=route_registered,
                catalog_present=catalog_present,
                credential_configured=credential_configured,
            ),
        )

    def _next_action(
        self,
        *,
        route_registered: bool,
        catalog_present: bool,
        credential_configured: bool,
    ) -> str:
        if not route_registered:
            return "REGISTER_GOOGLE_GEMINI_IMAGE_PROVIDER_ROUTE"
        if not catalog_present:
            return "ADD_VERSIONED_GEMINI_IMAGE_MODEL_PRICE_CATALOG"
        if not self.settings.gemini_image_provider_route_approved:
            return "APPROVE_GOOGLE_GEMINI_IMAGE_PROVIDER_ROUTE"
        if not credential_configured:
            return "CONFIGURE_GEMINI_API_KEY_WITHOUT_ENABLING_REAL_GENERATION"
        return "RUN_IMG1_LOCAL_FIXTURE_REHEARSAL_WITH_REAL_GENERATION_DISABLED"


def create_router() -> APIRouter:
    router = APIRouter(tags=["google-gemini-image-readiness"])

    @router.get(
        "/providers/google-gemini-image/readiness",
        response_model=GeminiImageReadiness,
    )
    def get_google_gemini_image_readiness() -> GeminiImageReadiness:
        return GoogleGeminiImageReadinessService(get_settings()).snapshot()

    return router


__all__ = [
    "GoogleGeminiImageReadinessService",
    "MODEL_PRICE_CATALOG_FILENAME",
    "PROVIDER_KEY",
    "create_router",
]
