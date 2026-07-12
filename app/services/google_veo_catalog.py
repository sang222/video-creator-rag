from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from app.contracts.google_veo import AIHeroProviderPolicySnapshot, GoogleVeoCostEstimateSnapshot
from app.services.native_render_plan import stable_hash


CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "google_veo_model_price_catalog.yaml"


class GoogleVeoModelPriceCatalog:
    def __init__(self, path: Path = CATALOG_PATH):
        self.path = path
        self.payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if self.payload.get("catalog_key") != "google_veo_model_price_catalog":
            raise ValueError("VEO_PRICE_CATALOG_INVALID")

    @property
    def version(self) -> str:
        return str(self.payload["catalog_version"])

    @property
    def ref(self) -> str:
        return f"config://google_veo_model_price_catalog/{self.version}"

    def model(self, model_id: str) -> dict:
        row = next((item for item in self.payload["items"] if item["model_id"] == model_id and item.get("approved")), None)
        if row is None:
            raise ValueError("VEO_MODEL_NOT_IN_PRICE_CATALOG")
        return row

    def estimate(
        self,
        *,
        model_id: str,
        resolution: str,
        duration_seconds: int,
        output_count: int,
        hard_cap: Decimal,
        approval_amount: Decimal,
    ) -> GoogleVeoCostEstimateSnapshot:
        row = self.model(model_id)
        if duration_seconds not in row["duration_seconds"]:
            raise ValueError("VEO_DURATION_NOT_IN_PRICE_CATALOG")
        resolution_row = row["resolutions"].get(resolution)
        if resolution_row is None:
            raise ValueError("VEO_RESOLUTION_NOT_IN_PRICE_CATALOG")
        price = Decimal(str(resolution_row["price_per_second_usd"]))
        amount = price * Decimal(duration_seconds) * Decimal(output_count)
        if amount > hard_cap or amount > approval_amount:
            raise ValueError("VEO_COST_CAP_EXCEEDED")
        payload = {
            "price_catalog_version": self.version,
            "price_catalog_ref": self.ref,
            "provider_key": "google_veo",
            "model_id": model_id,
            "resolution": resolution,
            "duration_seconds": duration_seconds,
            "output_count": output_count,
            "currency": "USD",
            "price_per_second": price,
            "estimated_amount": amount,
            "hard_cap": hard_cap,
            "approval_amount": approval_amount,
            "actual_amount": None,
            "variance_reason": None,
        }
        return GoogleVeoCostEstimateSnapshot(**payload, snapshot_hash=stable_hash(payload))


class GoogleVeoProjectPolicyCompiler:
    """Compile policy for a new project snapshot; this service never mutates an existing snapshot."""

    def compile(
        self,
        *,
        channel_id: str,
        max_clips: int,
        max_seconds: int,
        max_cost_usd: Decimal,
        unavailable_behavior: str,
    ) -> AIHeroProviderPolicySnapshot:
        payload = {
            "channel_id": channel_id,
            "ai_video_hero_enabled": True,
            "ai_video_provider": "google_veo",
            "allowed_model_ids": [item["model_id"] for item in GoogleVeoModelPriceCatalog().payload["items"] if item.get("approved")],
            "default_model_id": "veo-3.1-fast-generate-preview",
            "allowed_resolutions": ["720p", "1080p", "4k"],
            "max_ai_hero_clips_per_video": max_clips,
            "max_ai_hero_seconds_per_video": max_seconds,
            "max_ai_hero_cost_per_video": max_cost_usd,
            "allowed_hero_reasons": ["HOOK", "METAPHOR", "EMOTIONAL_PAYOFF", "VISUAL_SIGNATURE", "NATIVE_MOTION_INSUFFICIENT"],
            "provider_audio_policy": "DISCARD",
            "unavailable_behavior": unavailable_behavior,
            "frozen_at_project_creation": True,
        }
        if max_clips * 8 > max_seconds:
            raise ValueError("VEO_PROJECT_POLICY_DURATION_CAP_INCONSISTENT")
        return AIHeroProviderPolicySnapshot(**payload, snapshot_hash=stable_hash(payload))
