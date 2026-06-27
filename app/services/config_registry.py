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

class ProviderRegistryCatalogItem(BaseModel):
    provider_key: str
    provider_name: str
    provider_type: str
    status: str = "ACTIVE"
    capability_blob: dict[str, Any] = Field(default_factory=dict)
    policy_fit_blob: dict[str, Any] = Field(default_factory=dict)
    cost_model_blob: dict[str, Any] = Field(default_factory=dict)
    quota_model_blob: dict[str, Any] = Field(default_factory=dict)
    retry_policy_blob: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

class MetricDefinitionCatalogItem(BaseModel):
    metric_key: str
    metric_name: str
    metric_group: str
    platform: str
    unit: str
    description: str
    status: str = "ACTIVE"
    version: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

class DiagnosticTaxonomyCatalogItem(BaseModel):
    key: str
    description: str
    friendly_label: str | None = None
    status: str = "ACTIVE"
    version: str = "1.0.0"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

class LLMRouterLaneCatalogItem(BaseModel):
    lane_name: str
    lane_description: str
    allowed_task_types: list[str] = Field(default_factory=list)
    primary_model: str
    fallback_models: list[str] = Field(default_factory=list)
    premium_model: str | None = None
    emergency_model: str | None = None
    backup_model: str | None = None
    cost_tier: str
    latency_tier: str
    critical_path_allowed: bool = False
    requires_human_approval_for_premium: bool = True
    route_priority: int

    model_config = ConfigDict(extra="forbid")

class LLMModelProfileCatalogItem(BaseModel):
    provider_key: str
    model_id: str
    model_role: str
    lane_names: list[str] = Field(default_factory=list)
    role_bindings: list[dict[str, str]] = Field(default_factory=list)
    is_enabled: bool = True
    critical_path_allowed: bool = False
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")

class RetryPolicyCatalogItem(BaseModel):
    policy_key: str
    provider_key: str | None = None
    target_type: str | None = None
    policy_blob: dict[str, Any] = Field(default_factory=dict)
    status: str = "ACTIVE"

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
            if loaded.catalog_key == "metric_definition_catalog":
                self._seed_metric_definitions(loaded.content)
            if loaded.catalog_key == "diagnostic_taxonomy_catalog":
                self._seed_diagnostic_taxonomy(loaded.content)
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
            "provider_registry_catalog": ProviderRegistryCatalogItem,
            "provider_type_catalog": SimpleKeyCatalogItem,
            "provider_status_catalog": SimpleKeyCatalogItem,
            "credential_type_catalog": SimpleKeyCatalogItem,
            "credential_status_catalog": SimpleKeyCatalogItem,
            "quota_unit_catalog": SimpleKeyCatalogItem,
            "quota_event_type_catalog": SimpleKeyCatalogItem,
            "cost_event_type_catalog": SimpleKeyCatalogItem,
            "health_state_catalog": SimpleKeyCatalogItem,
            "component_type_catalog": SimpleKeyCatalogItem,
            "retry_policy_catalog": RetryPolicyCatalogItem,
            "ops_incident_type_catalog": SimpleKeyCatalogItem,
            "manual_action_type_catalog": SimpleKeyCatalogItem,
            "m4_reason_code_catalog": ReasonCodeItem,
            "daily_run_status_catalog": SimpleKeyCatalogItem,
            "slot_type_catalog": SimpleKeyCatalogItem,
            "context_pack_purpose_catalog": SimpleKeyCatalogItem,
            "search_demand_source_type_catalog": SimpleKeyCatalogItem,
            "platform_surface_catalog": SimpleKeyCatalogItem,
            "idea_decision_status_catalog": SimpleKeyCatalogItem,
            "admission_decision_catalog": SimpleKeyCatalogItem,
            "m5_reason_code_catalog": ReasonCodeItem,
            "production_run_status_catalog": SimpleKeyCatalogItem,
            "media_render_job_status_catalog": SimpleKeyCatalogItem,
            "render_package_state_catalog": SimpleKeyCatalogItem,
            "render_intent_catalog": SimpleKeyCatalogItem,
            "render_source_type_catalog": SimpleKeyCatalogItem,
            "provider_source_class_catalog": SimpleKeyCatalogItem,
            "scene_type_catalog": SimpleKeyCatalogItem,
            "scene_importance_catalog": SimpleKeyCatalogItem,
            "scene_factual_risk_catalog": SimpleKeyCatalogItem,
            "source_decision_catalog": SimpleKeyCatalogItem,
            "media_qc_state_catalog": SimpleKeyCatalogItem,
            "accessibility_qc_state_catalog": SimpleKeyCatalogItem,
            "asset_source_type_catalog": SimpleKeyCatalogItem,
            "license_state_catalog": SimpleKeyCatalogItem,
            "export_profile_catalog": SimpleKeyCatalogItem,
            "render_variant_status_catalog": SimpleKeyCatalogItem,
            "m6_reason_code_catalog": ReasonCodeItem,
            "publish_target_platform_catalog": SimpleKeyCatalogItem,
            "publish_target_surface_catalog": SimpleKeyCatalogItem,
            "publish_handoff_state_catalog": SimpleKeyCatalogItem,
            "manual_publish_confirmation_state_catalog": SimpleKeyCatalogItem,
            "uploaded_video_publish_status_catalog": SimpleKeyCatalogItem,
            "uploaded_video_monitoring_state_catalog": SimpleKeyCatalogItem,
            "publish_checklist_category_catalog": SimpleKeyCatalogItem,
            "disclosure_confirmation_catalog": SimpleKeyCatalogItem,
            "metadata_diff_severity_catalog": SimpleKeyCatalogItem,
            "m7_reason_code_catalog": ReasonCodeItem,
            "analytics_sync_mode_catalog": SimpleKeyCatalogItem,
            "analytics_sync_state_catalog": SimpleKeyCatalogItem,
            "analytics_observation_window_catalog": SimpleKeyCatalogItem,
            "metric_group_catalog": SimpleKeyCatalogItem,
            "metric_unit_catalog": SimpleKeyCatalogItem,
            "metric_freshness_state_catalog": SimpleKeyCatalogItem,
            "metric_confidence_level_catalog": SimpleKeyCatalogItem,
            "m8_reason_code_catalog": ReasonCodeItem,
            "metric_definition_catalog": MetricDefinitionCatalogItem,
            "post_publish_observation_window_catalog": SimpleKeyCatalogItem,
            "post_publish_health_state_catalog": SimpleKeyCatalogItem,
            "diagnostic_state_catalog": SimpleKeyCatalogItem,
            "diagnostic_taxonomy_catalog": DiagnosticTaxonomyCatalogItem,
            "recovery_proposal_type_catalog": SimpleKeyCatalogItem,
            "recovery_proposal_state_catalog": SimpleKeyCatalogItem,
            "diagnostic_confidence_catalog": SimpleKeyCatalogItem,
            "diagnostic_severity_catalog": SimpleKeyCatalogItem,
            "m9_reason_code_catalog": ReasonCodeItem,
            "learning_candidate_type_catalog": SimpleKeyCatalogItem,
            "learning_candidate_state_catalog": SimpleKeyCatalogItem,
            "learning_review_queue_state_catalog": SimpleKeyCatalogItem,
            "learning_promotion_eligibility_result_catalog": SimpleKeyCatalogItem,
            "learning_confidence_label_catalog": SimpleKeyCatalogItem,
            "learning_risk_level_catalog": SimpleKeyCatalogItem,
            "learning_recommended_scope_catalog": SimpleKeyCatalogItem,
            "playbook_candidate_category_catalog": SimpleKeyCatalogItem,
            "playbook_candidate_state_catalog": SimpleKeyCatalogItem,
            "m10_reason_code_catalog": ReasonCodeItem,
            "llm_router_lane_catalog": LLMRouterLaneCatalogItem,
            "llm_model_profile_catalog": LLMModelProfileCatalogItem,
            "llm_route_status_catalog": SimpleKeyCatalogItem,
            "derivative_type_catalog": SimpleKeyCatalogItem,
            "short_candidate_state_catalog": SimpleKeyCatalogItem,
            "short_crop_strategy_catalog": SimpleKeyCatalogItem,
            "short_visual_source_catalog": SimpleKeyCatalogItem,
            "originality_check_result_catalog": SimpleKeyCatalogItem,
            "reusable_artifact_type_catalog": SimpleKeyCatalogItem,
            "reusable_artifact_state_catalog": SimpleKeyCatalogItem,
            "release_plan_state_catalog": SimpleKeyCatalogItem,
            "upload_card_state_catalog": SimpleKeyCatalogItem,
            "human_upload_task_state_catalog": SimpleKeyCatalogItem,
            "music_policy_catalog": SimpleKeyCatalogItem,
            "cta_type_catalog": SimpleKeyCatalogItem,
            "m10_1_reason_code_catalog": ReasonCodeItem,
            "media_provider_type_catalog": SimpleKeyCatalogItem,
            "media_provider_recommendation_catalog": SimpleKeyCatalogItem,
            "media_job_type_catalog": SimpleKeyCatalogItem,
            "provider_capability_catalog": SimpleKeyCatalogItem,
            "media_routing_result_catalog": SimpleKeyCatalogItem,
            "media_budget_state_catalog": SimpleKeyCatalogItem,
            "media_budget_enforcement_catalog": SimpleKeyCatalogItem,
            "long_form_render_package_state_catalog": SimpleKeyCatalogItem,
            "short_render_package_state_catalog": SimpleKeyCatalogItem,
            "ai_hero_asset_state_catalog": SimpleKeyCatalogItem,
            "creatomate_render_asset_state_catalog": SimpleKeyCatalogItem,
            "thumbnail_variant_state_catalog": SimpleKeyCatalogItem,
            "final_media_type_catalog": SimpleKeyCatalogItem,
            "license_status_catalog": SimpleKeyCatalogItem,
            "m10_2_reason_code_catalog": ReasonCodeItem,
            "youtube_auth_mode_catalog": SimpleKeyCatalogItem,
            "youtube_connection_state_catalog": SimpleKeyCatalogItem,
            "youtube_sync_run_state_catalog": SimpleKeyCatalogItem,
            "youtube_metric_authority_catalog": SimpleKeyCatalogItem,
            "youtube_sync_source_catalog": SimpleKeyCatalogItem,
            "youtube_metric_availability_catalog": SimpleKeyCatalogItem,
            "youtube_follow_freshness_state_catalog": SimpleKeyCatalogItem,
            "youtube_follow_sync_status_catalog": SimpleKeyCatalogItem,
            "m10_3_reason_code_catalog": ReasonCodeItem,
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
                or getattr(parsed, "lane_name", None)
                or getattr(parsed, "model_id", None)
                or getattr(parsed, "provider_key", None)
                or getattr(parsed, "policy_key", None)
                or getattr(parsed, "metric_key", None)
                or getattr(parsed, "template_key", None)
                or getattr(parsed, "matrix_key", None)
                or getattr(parsed, "compiler_version", None)
            )
            if key in seen:
                raise ValidationFailureError(f"duplicate catalog item: {key}")
            seen.add(key)

    def _seed_metric_definitions(self, content: dict[str, Any]) -> None:
        from app.db.models import MetricDefinitionVersion

        for item in content["items"]:
            statement = select(MetricDefinitionVersion).where(
                MetricDefinitionVersion.metric_key == item["metric_key"],
                MetricDefinitionVersion.platform == item["platform"],
                MetricDefinitionVersion.version == item["version"],
            )
            existing = self.session.scalars(statement).one_or_none()
            if existing is None:
                existing = MetricDefinitionVersion(
                    metric_key=item["metric_key"],
                    metric_name=item["metric_name"],
                    metric_group=item["metric_group"],
                    platform=item["platform"],
                    unit=item["unit"],
                    description=item["description"],
                    status=item.get("status", "ACTIVE"),
                    version=item["version"],
                    metadata_=item.get("metadata", {}),
                )
                self.session.add(existing)
            else:
                existing.metric_name = item["metric_name"]
                existing.description = item["description"]
                existing.status = item.get("status", "ACTIVE")
                existing.metadata_ = item.get("metadata", {})
        self.session.flush()

    def _seed_diagnostic_taxonomy(self, content: dict[str, Any]) -> None:
        from app.db.models import DiagnosticTaxonomyVersion

        for item in content["items"]:
            version = item.get("version", content["catalog_version"])
            existing = self.session.scalars(
                select(DiagnosticTaxonomyVersion).where(
                    DiagnosticTaxonomyVersion.taxonomy_key == item["key"],
                    DiagnosticTaxonomyVersion.version == version,
                )
            ).one_or_none()
            blob = {
                "code": item["key"],
                "description": item["description"],
                "friendly_label": item.get("friendly_label") or item["description"],
                "metadata": item.get("metadata", {}),
            }
            if existing is None:
                existing = DiagnosticTaxonomyVersion(
                    taxonomy_key=item["key"],
                    version=version,
                    taxonomy_blob=blob,
                    status=item.get("status", "ACTIVE"),
                )
                self.session.add(existing)
            else:
                if existing.taxonomy_blob != blob or existing.status != item.get("status", "ACTIVE"):
                    raise ConfigVersionConflictError(f"diagnostic taxonomy version conflict: {item['key']} {version}")
        self.session.flush()

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
