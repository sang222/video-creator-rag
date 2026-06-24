import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.config_catalog import CatalogDocument
from app.contracts.profile import CapabilityMatrix, NicheProfileTemplate, ProfileCompilerPolicy
from app.core.errors import ConfigVersionConflictError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import ConfigCatalogVersion, Role


class ReasonCodeItem(BaseModel):
    code: str
    description: str

    model_config = ConfigDict(extra="forbid")


class EventTypeItem(BaseModel):
    event_type: str
    event_version: int = Field(gt=0)
    description: str

    model_config = ConfigDict(extra="forbid")


class RoleCatalogItem(BaseModel):
    key: str
    name: str
    description: str
    permissions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

class ArtifactTypeRegistryItem(BaseModel):
    key: str
    description: str

    model_config = ConfigDict(extra="forbid")

class ReviewTypeRegistryItem(BaseModel):
    key: str
    description: str

    model_config = ConfigDict(extra="forbid")

class SimpleKeyCatalogItem(BaseModel):
    key: str
    description: str

    model_config = ConfigDict(extra="forbid")

class ConfidenceReasonCodeItem(BaseModel):
    code: str
    description: str

    model_config = ConfigDict(extra="forbid")

class GateDefinitionCatalogItem(BaseModel):
    gate_key: str
    gate_name: str
    gate_domain: str
    version: str
    status: str = "active"
    input_schema_version: str
    output_schema_version: str
    definition: dict[str, Any] = Field(default_factory=dict)
    reason_code_refs: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

class PlatformPolicyCatalogItem(BaseModel):
    catalog_key: str
    platform: str
    policy_domain: str
    status: str = "active"
    description: str | None = None

    model_config = ConfigDict(extra="forbid")

class DecisionRightsPolicyItem(BaseModel):
    key: str
    description: str
    required_permission: str
    allowed_role_keys: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class LoadedCatalog:
    path: Path
    content: dict[str, Any]
    content_hash: str

    @property
    def catalog_key(self) -> str:
        return str(self.content["catalog_key"])

    @property
    def catalog_version(self) -> str:
        return str(self.content["catalog_version"])


def canonical_json(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


class ConfigRegistryService:
    def __init__(self, session: Session):
        self.session = session

    def load_catalog_files(self, paths: Iterable[str | Path] | None = None) -> list[LoadedCatalog]:
        discovered = self._discover(paths or [Path("config")])
        return [self.validate_catalog(path) for path in discovered]

    def validate_catalog(self, path: str | Path) -> LoadedCatalog:
        catalog_path = Path(path)
        raw = self._read_catalog(catalog_path)
        try:
            document = CatalogDocument.model_validate(raw)
            self._validate_items(document)
        except ValidationError as exc:
            raise ValidationFailureError(f"invalid catalog {catalog_path}: {exc}") from exc
        content = document.model_dump(mode="json")
        return LoadedCatalog(catalog_path, content, content_hash(content))

    def seed(self, paths: Iterable[str | Path] | None = None) -> list[ConfigCatalogVersion]:
        records: list[ConfigCatalogVersion] = []
        for loaded in self.load_catalog_files(paths):
            record = self.get_version(loaded.catalog_key, loaded.catalog_version)
            if record is not None:
                if record.content_hash != loaded.content_hash:
                    raise ConfigVersionConflictError(
                        f"catalog version conflict: {loaded.catalog_key} {loaded.catalog_version}"
                    )
            else:
                record = ConfigCatalogVersion(
                    catalog_key=loaded.content["catalog_key"],
                    catalog_version=loaded.content["catalog_version"],
                    schema_version=loaded.content["schema_version"],
                    content=loaded.content,
                    content_hash=loaded.content_hash,
                    source_path=str(loaded.path),
                    status=loaded.content["status"],
                    activated_at=utc_now() if loaded.content["status"] == "active" else None,
                )
                self.session.add(record)
                self.session.flush()
            if loaded.catalog_key == "role_catalog":
                self._seed_roles(loaded.content)
            records.append(record)
        self.session.commit()
        return records

    def get_version(
        self, catalog_key: str, catalog_version: str
    ) -> ConfigCatalogVersion | None:
        statement = select(ConfigCatalogVersion).where(
            ConfigCatalogVersion.catalog_key == catalog_key,
            ConfigCatalogVersion.catalog_version == catalog_version,
        )
        return self.session.scalars(statement).one_or_none()

    def role_catalog_mapping(self, path: str | Path = "config/role_catalog.yaml") -> dict[str, set[str]]:
        loaded = self.validate_catalog(path)
        if loaded.catalog_key != "role_catalog":
            raise ValidationFailureError("catalog is not role_catalog")
        return {
            item["key"]: set(item.get("permissions", []))
            for item in loaded.content["items"]
        }

    def _seed_roles(self, content: dict[str, Any]) -> None:
        for item in content["items"]:
            statement = select(Role).where(Role.key == item["key"])
            role = self.session.scalars(statement).one_or_none()
            if role is None:
                role = Role(
                    key=item["key"],
                    name=item["name"],
                    description=item["description"],
                )
                self.session.add(role)
            else:
                role.name = item["name"]
                role.description = item["description"]
        self.session.flush()

    def _validate_items(self, document: CatalogDocument) -> None:
        validators = {
            "reason_codes": ReasonCodeItem,
            "event_types": EventTypeItem,
            "role_catalog": RoleCatalogItem,
            "artifact_type_registry": ArtifactTypeRegistryItem,
            "review_type_registry": ReviewTypeRegistryItem,
            "decision_rights_policy": DecisionRightsPolicyItem,
            "reason_code_catalog": ReasonCodeItem,
            "evidence_type_catalog": SimpleKeyCatalogItem,
            "freshness_state_catalog": SimpleKeyCatalogItem,
            "confidence_reason_code_catalog": ConfidenceReasonCodeItem,
            "policy_domain_catalog": SimpleKeyCatalogItem,
            "gate_definition_catalog": GateDefinitionCatalogItem,
            "platform_policy_catalog": PlatformPolicyCatalogItem,
            "niche_profile_templates": NicheProfileTemplate,
            "capability_matrix": CapabilityMatrix,
            "profile_compiler_policy": ProfileCompilerPolicy,
        }
        item_model = validators.get(document.catalog_key)
        if item_model is None:
            raise ValidationFailureError(f"unknown catalog_key: {document.catalog_key}")
        seen: set[str] = set()
        for item in document.items:
            parsed = item_model.model_validate(item)
            key = (
                getattr(parsed, "code", None)
                or getattr(parsed, "event_type", None)
                or getattr(parsed, "key", None)
                or getattr(parsed, "gate_key", None)
                or getattr(parsed, "catalog_key", None)
                or getattr(parsed, "template_key", None)
                or getattr(parsed, "matrix_key", None)
                or getattr(parsed, "compiler_version", None)
            )
            if key in seen:
                raise ValidationFailureError(f"duplicate catalog item: {key}")
            seen.add(key)

    def _discover(self, paths: Iterable[str | Path]) -> list[Path]:
        files: list[Path] = []
        for path_like in paths:
            path = Path(path_like)
            if path.is_dir():
                files.extend(sorted(path.glob("*.yaml")))
                files.extend(sorted(path.glob("*.yml")))
                files.extend(sorted(path.glob("*.json")))
            else:
                files.append(path)
        return sorted(files)

    def _read_catalog(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValidationFailureError(f"catalog root must be a mapping: {path}")
        return data
