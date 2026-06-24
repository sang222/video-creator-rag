from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.errors import ConfigVersionConflictError
from app.db.models import ConfigCatalogVersion, Role
from app.services.config_registry import ConfigRegistryService

ROOT = Path(__file__).resolve().parents[1]


def test_config_yaml_validates(db_session) -> None:
    loaded = ConfigRegistryService(db_session).load_catalog_files([ROOT / "config"])
    assert {catalog.catalog_key for catalog in loaded} == {
        "artifact_type_registry",
        "capability_matrix",
        "decision_rights_policy",
        "event_types",
        "niche_profile_templates",
        "profile_compiler_policy",
        "reason_codes",
        "review_type_registry",
        "role_catalog",
    }


def test_config_seed_deterministic(db_session) -> None:
    service = ConfigRegistryService(db_session)
    first = {catalog.catalog_key: catalog.content_hash for catalog in service.load_catalog_files([ROOT / "config"])}
    second = {catalog.catalog_key: catalog.content_hash for catalog in service.load_catalog_files([ROOT / "config"])}
    assert first == second


def test_config_seed_idempotent(db_session) -> None:
    service = ConfigRegistryService(db_session)
    service.seed([ROOT / "config"])
    service.seed([ROOT / "config"])
    catalog_count = db_session.scalar(select(func.count()).select_from(ConfigCatalogVersion))
    role_count = db_session.scalar(select(func.count()).select_from(Role))
    assert catalog_count == 9
    assert role_count == 3


def test_config_version_conflict_is_blocked(db_session, tmp_path) -> None:
    service = ConfigRegistryService(db_session)
    service.seed([ROOT / "config" / "reason_codes.yaml"])
    conflict = tmp_path / "reason_codes.yaml"
    conflict.write_text(
        """
catalog_key: reason_codes
catalog_version: "1.1.0"
schema_version: catalog.v1
status: active
items:
  - code: SYSTEM_OK
    description: Changed description.
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigVersionConflictError):
        service.seed([conflict])
