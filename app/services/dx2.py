from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app.contracts.dx2 import ProviderStackDriftGuardRead
from app.core.config import Settings, get_settings
from app.core.time import utc_now
from app.services.m2 import ProviderReadinessM2Service
from app.services.provider_stack import (
    CANONICAL_PROVIDER_KEYS,
    STALE_PROVIDER_KEYS,
    is_canonical_provider_key,
    is_stale_provider_key,
    normalize_provider_key,
    provider_key_slug,
)


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DX2_CATALOGS = (
    "media_provider_routing_policy_catalog",
    "media_provider_role_profile_catalog",
    "media_provider_capability_matrix_catalog",
    "media_provider_budget_policy_catalog",
    "pexels_policy_catalog",
    "provider_registry_catalog",
)


class ProviderStackDriftGuard:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        catalog_dir: Path | None = None,
        catalog_overrides: dict[str, list[dict[str, Any]]] | None = None,
    ):
        self.settings = settings or get_settings()
        self.catalog_dir = catalog_dir or CONFIG_DIR
        self.catalog_overrides = catalog_overrides or {}

    def check(self) -> ProviderStackDriftGuardRead:
        found: set[str] = set()
        stale: set[str] = set()
        affected: dict[str, list[dict[str, Any]]] = {}

        for catalog_key in DX2_CATALOGS:
            for item in self._catalog_items(catalog_key):
                if not self._active_item(catalog_key, item):
                    continue
                provider_key = item.get("provider_key")
                if provider_key is None:
                    continue
                normalized = normalize_provider_key(provider_key)
                if normalized and normalized in CANONICAL_PROVIDER_KEYS:
                    found.add(normalized)
                if is_stale_provider_key(provider_key):
                    stale.add(str(provider_key))
                    affected.setdefault(catalog_key, []).append(
                        {
                            "key": item.get("key") or item.get("job_type") or item.get("provider_type"),
                            "provider_key": str(provider_key),
                            "provider_key_slug": provider_key_slug(provider_key),
                            "reason_code": "STALE_PROVIDER_KEY_ACTIVE",
                        }
                    )

        m2_snapshot = ProviderReadinessM2Service(self.settings).snapshot()
        for provider in m2_snapshot.providers:
            if provider.provider_key in CANONICAL_PROVIDER_KEYS:
                found.add(provider.provider_key)
            if is_stale_provider_key(provider.provider_key):
                stale.add(provider.provider_key)
                affected.setdefault("m2_provider_readiness", []).append(
                    {"provider_key": provider.provider_key, "reason_code": "STALE_PROVIDER_KEY_ACTIVE"}
                )

        missing = sorted(set(CANONICAL_PROVIDER_KEYS) - found)
        reason_codes: list[str] = []
        if stale:
            reason_codes.append("STALE_PROVIDER_KEY_ACTIVE")
        if missing:
            reason_codes.append("CANONICAL_PROVIDER_KEY_MISSING")
            affected.setdefault("canonical_provider_stack", []).append({"missing_provider_keys": missing})
        status = "PROVIDER_STACK_DRIFT" if reason_codes else "PASS"
        return ProviderStackDriftGuardRead(
            generated_at=utc_now(),
            status=status,
            expected_provider_keys=list(CANONICAL_PROVIDER_KEYS),
            found_active_provider_keys=sorted(found),
            stale_provider_keys=sorted(stale),
            affected_catalogs=affected,
            reason_codes=sorted(set(reason_codes)),
            next_action=(
                "Chuẩn hóa catalog/readiness về elevenlabs, luma_api, creatomate_growth_10k, pexels_api trước khi coi Provider/Cost là READY."
                if status == "PROVIDER_STACK_DRIFT"
                else "Provider stack canonical; R3D9 Provider/Cost panel có thể đọc readiness/cost firewall."
            ),
            no_provider_call_made=True,
        )

    def _catalog_items(self, catalog_key: str) -> list[dict[str, Any]]:
        if catalog_key in self.catalog_overrides:
            return self.catalog_overrides[catalog_key]
        path = self.catalog_dir / f"{catalog_key}.yaml"
        with path.open("r", encoding="utf-8") as handle:
            content = yaml.safe_load(handle) or {}
        items = content.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    def _active_item(self, catalog_key: str, item: dict[str, Any]) -> bool:
        if catalog_key == "media_provider_role_profile_catalog":
            return bool(item.get("is_enabled", True)) and str(item.get("recommendation") or "").upper() not in {
                "DEFERRED",
                "INACTIVE",
                "COMPATIBILITY_ONLY",
            }
        if catalog_key == "media_provider_capability_matrix_catalog":
            return str(item.get("capability") or "").upper() == "SUPPORTED"
        if catalog_key == "media_provider_budget_policy_catalog":
            return item.get("provider_key") is not None
        if catalog_key == "provider_registry_catalog":
            return str(item.get("status") or "").upper() == "ACTIVE"
        return True


def reject_stale_provider_key_reason(provider_key: Any) -> list[str]:
    if is_stale_provider_key(provider_key):
        return ["STALE_PROVIDER_KEY_NOT_ACTIVE", "PROVIDER_STACK_DRIFT"]
    if provider_key is not None and normalize_provider_key(provider_key) not in set(CANONICAL_PROVIDER_KEYS) | {
        "google_drive_archive",
        "youtube_readonly",
    }:
        return ["UNKNOWN_PROVIDER_KEY"]
    return []
