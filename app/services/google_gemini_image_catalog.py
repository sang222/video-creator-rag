from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.google_gemini_image import GeminiImageCostEstimateSnapshot
from app.core.config import (
    GEMINI_IMAGE_APPROVED_MODEL_IDS,
    GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS,
    GEMINI_IMAGE_SUPPORTED_SIZES,
)


CATALOG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "google_gemini_image_model_price_catalog.yaml"
)


class GoogleGeminiImageModelPriceCatalog:
    def __init__(self, path: Path = CATALOG_PATH):
        self.path = path
        self.payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if self.payload.get("catalog_key") != "google_gemini_image_model_price_catalog":
            raise ValueError("GEMINI_IMAGE_PRICE_CATALOG_INVALID")
        self._validate_complete_matrix()

    @property
    def version(self) -> str:
        return str(self.payload["catalog_version"])

    @property
    def ref(self) -> str:
        return f"config://google_gemini_image_model_price_catalog/{self.version}"

    def row(self, *, model_id: str, image_size: str, aspect_ratio: str) -> dict[str, Any]:
        if model_id not in GEMINI_IMAGE_APPROVED_MODEL_IDS:
            raise ValueError("GEMINI_IMAGE_MODEL_NOT_IN_PRICE_CATALOG")
        if image_size not in GEMINI_IMAGE_SUPPORTED_SIZES:
            raise ValueError("GEMINI_IMAGE_SIZE_NOT_IN_PRICE_CATALOG")
        if aspect_ratio not in GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS:
            raise ValueError("GEMINI_IMAGE_ASPECT_RATIO_NOT_IN_PRICE_CATALOG")
        matches = [
            item
            for item in self.payload["items"]
            if item["model_id"] == model_id
            and item["size"] == image_size
            and item["aspect_ratio"] == aspect_ratio
        ]
        if len(matches) != 1:
            raise ValueError("GEMINI_IMAGE_PRICE_CATALOG_ROW_NOT_UNIQUE")
        return dict(matches[0])

    def estimate(
        self,
        *,
        model_id: str,
        image_size: str,
        aspect_ratio: str,
        output_count: int,
        attempt_count: int,
        hard_cap: Decimal,
        approval_amount: Decimal,
        four_k_approval_ref: str | None = None,
    ) -> GeminiImageCostEstimateSnapshot:
        row = self.row(
            model_id=model_id,
            image_size=image_size,
            aspect_ratio=aspect_ratio,
        )
        if row["policy_state"] == "BLOCK":
            raise ValueError("GEMINI_IMAGE_EFFECTIVE_RESOLUTION_BELOW_1080P")
        if row["policy_state"] == "REVIEW_REQUIRED" and not four_k_approval_ref:
            raise ValueError("GEMINI_IMAGE_4K_REVIEW_APPROVAL_REQUIRED")
        if output_count != 1 or attempt_count != 1:
            raise ValueError("GEMINI_IMAGE_SINGLE_OUTPUT_SINGLE_ATTEMPT_REQUIRED")
        unit_cost = Decimal(str(row["estimated_unit_cost_usd"]))
        estimated = unit_cost * output_count * attempt_count
        payload = {
            "price_catalog_version": self.version,
            "price_catalog_ref": self.ref,
            "provider_key": "google_gemini_image",
            "model_id": model_id,
            "image_size": image_size,
            "aspect_ratio": aspect_ratio,
            "output_count": output_count,
            "attempt_count": attempt_count,
            "currency": row["currency"],
            "estimated_unit_cost": unit_cost,
            "estimated_amount": estimated,
            "hard_cap": hard_cap,
            "approval_amount": approval_amount,
            "actual_amount": None,
            "effective_date": self._as_date(row["effective_date"]),
            "source_note": row["source_note"],
        }
        return GeminiImageCostEstimateSnapshot(
            **payload,
            snapshot_hash=ai_image_stable_hash(payload),
        )

    def _validate_complete_matrix(self) -> None:
        rows = self.payload.get("items")
        if not isinstance(rows, list):
            raise ValueError("GEMINI_IMAGE_PRICE_CATALOG_ITEMS_MISSING")
        expected = {
            (model, size, aspect)
            for model in GEMINI_IMAGE_APPROVED_MODEL_IDS
            for size in GEMINI_IMAGE_SUPPORTED_SIZES
            for aspect in GEMINI_IMAGE_SUPPORTED_ASPECT_RATIOS
        }
        actual = {
            (item.get("model_id"), item.get("size"), item.get("aspect_ratio"))
            for item in rows
        }
        if actual != expected or len(actual) != len(rows):
            raise ValueError("GEMINI_IMAGE_PRICE_CATALOG_MATRIX_INCOMPLETE")
        defaults = [item for item in rows if item.get("is_default_route") is True]
        if len(defaults) != 1 or (
            defaults[0]["model_id"],
            defaults[0]["size"],
            defaults[0]["aspect_ratio"],
        ) != ("gemini-3.1-flash-image", "2K", "16:9"):
            raise ValueError("GEMINI_IMAGE_DEFAULT_PRICE_ROUTE_INVALID")
        if any(item.get("actual_billed_amount") is not None for item in rows):
            raise ValueError("GEMINI_IMAGE_ACTUAL_BILLED_AMOUNT_MUST_BE_NULL")

    @staticmethod
    def _as_date(value: Any) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))


__all__ = ["GoogleGeminiImageModelPriceCatalog"]
