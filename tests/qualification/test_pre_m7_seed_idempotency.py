from __future__ import annotations

import pytest
from sqlalchemy import func, select

pytestmark = pytest.mark.skip(
    reason="Historical seed contract expected runtime mock providers; M12.1R real-only catalog coverage lives in tests/test_m12_1r_mock_runtime_purge.py."
)

from app.db.models import ConfigCatalogVersion, GateDefinitionVersion, ProviderRegistryEntry
from app.services import ConfigRegistryService, GateDefinitionService, ProviderRegistryService

from .helpers.qualification_asserts import ROOT


def _counts(session) -> dict[str, int]:
    return {
        "config": session.scalar(select(func.count()).select_from(ConfigCatalogVersion)) or 0,
        "providers": session.scalar(select(func.count()).select_from(ProviderRegistryEntry)) or 0,
        "gates": session.scalar(select(func.count()).select_from(GateDefinitionVersion)) or 0,
    }


def test_config_provider_gate_seed_idempotency_and_catalog_integrity(db_session) -> None:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    ProviderRegistryService(db_session).seed_mock_providers()
    GateDefinitionService(db_session).seed_definitions()
    first = _counts(db_session)
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    ProviderRegistryService(db_session).seed_mock_providers()
    GateDefinitionService(db_session).seed_definitions()
    second = _counts(db_session)
    assert second == first
    assert first["providers"] == 6
    assert first["gates"] >= 5

    catalog_keys = set(db_session.scalars(select(ConfigCatalogVersion.catalog_key)).all())
    assert {
        "reason_code_catalog",
        "m4_reason_code_catalog",
        "m5_reason_code_catalog",
        "m6_reason_code_catalog",
        "confidence_reason_code_catalog",
    } <= catalog_keys

    duplicate_catalogs = db_session.execute(
        select(ConfigCatalogVersion.catalog_key, ConfigCatalogVersion.catalog_version, func.count())
        .group_by(ConfigCatalogVersion.catalog_key, ConfigCatalogVersion.catalog_version)
        .having(func.count() > 1)
    ).all()
    duplicate_providers = db_session.execute(
        select(ProviderRegistryEntry.provider_key, func.count())
        .group_by(ProviderRegistryEntry.provider_key)
        .having(func.count() > 1)
    ).all()
    duplicate_active_gates = db_session.execute(
        select(GateDefinitionVersion.gate_key, func.count())
        .where(GateDefinitionVersion.status == "active")
        .group_by(GateDefinitionVersion.gate_key)
        .having(func.count() > 1)
    ).all()
    assert duplicate_catalogs == []
    assert duplicate_providers == []
    assert duplicate_active_gates == []
