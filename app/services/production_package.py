"""Canonical ProductionPackage v2 creation and automated readiness.

This module is intentionally additive.  Historical PKG1 and
FirstScriptedVideoPackage rows keep their existing readers and workflows.
New v2 authority is stored only as immutable ArtifactVersion rows.
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from enum import Enum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.gates import GateRunCreate
from app.contracts.production_package import (
    DURATION_CONTRACT_VERSION_V2,
    PRODUCTION_PACKAGE_SCHEMA_V2,
    ExactContentRefV2,
    GateRunBindingV2,
    ProductionDurationContractV2,
    ProductionPackageContentV2,
    ProductionPackageCreateV2,
    ProductionPackageMateriality,
    ProductionPackageReadV2,
    ProductionPackageRevisionRequestV2,
    ProductionReadinessEvaluationV2,
    ProductionReadinessReceiptContentV2,
    ProductionReadinessReceiptReadV2,
    ProductionRevisionV2,
)
from app.contracts.vcos_v2 import DurationContractV2, ProductionLane
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models.channel import (
    ChannelProfileVersion,
    CompiledChannelPolicySnapshot,
)
from app.db.models.gates import GateRun
from app.db.models.m5 import ProjectAdmissionDecision
from app.db.models.r3d2 import EffectiveChannelRuntimeContextSnapshot
from app.db.models.workflow import Artifact, ArtifactVersion, VideoProject
from app.services.config_registry import content_hash
from app.services.gates import GateDefinitionService, GateRunnerService
from app.services.workflow import ArtifactService


PRODUCTION_PACKAGE_ARTIFACT_TYPE = "production_package"
PRODUCTION_READINESS_ARTIFACT_TYPE = "production_readiness_receipt"
READINESS_EVALUATOR_VERSION = "production-readiness-evaluator.v2.0.0"

REQUIRED_PRODUCTION_GATE_KEYS = (
    "production_research_evidence_gate",
    "production_assignment_integrity_gate",
    "production_duration_contract_gate",
    "production_editorial_depth_gate",
    "production_anti_padding_gate",
    "production_script_integrity_gate",
    "production_visual_metadata_gate",
    "production_rights_disclosure_gate",
    "production_provider_budget_gate",
    "production_materiality_gate",
    "production_package_integrity_gate",
)

MATERIAL_CHANGE_CLASSES = frozenset(
    {
        ProductionPackageMateriality.MATERIAL_EDITORIAL_CHANGE,
        ProductionPackageMateriality.MATERIAL_MARKET_OR_DESTINATION_CHANGE,
        ProductionPackageMateriality.MATERIAL_PROVIDER_OR_COST_CHANGE,
        ProductionPackageMateriality.MATERIAL_RIGHTS_OR_EVIDENCE_CHANGE,
    }
)


def semantic_hash(value: Any) -> str:
    return content_hash(_jsonable(value))


def duration_contract_semantic_payload(
    *,
    minimum_duration_ms: int,
    target_duration_ms: int,
    maximum_duration_ms: int,
    duration_contract_version: str,
    source_profile_version_id: uuid.UUID,
    source_policy_snapshot_id: uuid.UUID,
) -> dict[str, Any]:
    return {
        "minimum_duration_ms": minimum_duration_ms,
        "target_duration_ms": target_duration_ms,
        "maximum_duration_ms": maximum_duration_ms,
        "duration_contract_version": duration_contract_version,
        "source_profile_version_id": str(source_profile_version_id),
        "source_policy_snapshot_id": str(source_policy_snapshot_id),
    }


class ChannelDurationContractResolver:
    """Resolve exact approved channel values without a package/global fallback."""

    def __init__(self, session: Session):
        self.session = session

    def resolve(
        self,
        *,
        profile_version_id: uuid.UUID,
        policy_snapshot_id: uuid.UUID,
        production_lane: ProductionLane | str | None = None,
    ) -> ProductionDurationContractV2:
        profile = self.session.get(ChannelProfileVersion, profile_version_id)
        if profile is None:
            raise ValidationFailureError("DURATION_SOURCE_PROFILE_NOT_FOUND")
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, policy_snapshot_id
        )
        if snapshot is None:
            raise ValidationFailureError("DURATION_SOURCE_POLICY_NOT_FOUND")
        if profile.status not in {"approved", "active"}:
            raise ValidationFailureError("DURATION_SOURCE_PROFILE_NOT_APPROVED")
        if snapshot.status not in {"approved", "active"}:
            raise ValidationFailureError("DURATION_SOURCE_POLICY_NOT_APPROVED")
        if (
            snapshot.channel_profile_version_id != profile.id
            or snapshot.channel_workspace_id != profile.channel_workspace_id
            or snapshot.profile_input_hash != profile.profile_input_hash
        ):
            raise ValidationFailureError("DURATION_SOURCE_LINEAGE_MISMATCH")

        lane_key = (
            production_lane.value
            if isinstance(production_lane, ProductionLane)
            else production_lane
        )
        profile_values = _profile_duration_values(
            profile.profile_input or {}, production_lane=lane_key
        )
        snapshot_values = _snapshot_duration_values(
            snapshot.compiled_payload or {}, production_lane=lane_key
        )
        if profile_values is None or snapshot_values is None:
            raise ValidationFailureError("DURATION_CONTRACT_MISSING")
        if profile_values != snapshot_values:
            raise ValidationFailureError("DURATION_PROFILE_POLICY_MISMATCH")

        if (
            profile_values["duration_contract_version"]
            != DURATION_CONTRACT_VERSION_V2
        ):
            raise ValidationFailureError("DURATION_CONTRACT_VERSION_INVALID")
        duration_hash = DurationContractV2.calculate_hash(
            minimum_duration_ms=profile_values["minimum_duration_ms"],
            target_duration_ms=profile_values["target_duration_ms"],
            maximum_duration_ms=profile_values["maximum_duration_ms"],
            duration_contract_version=profile_values[
                "duration_contract_version"
            ],
            source_profile_version_id=profile.id,
            source_policy_snapshot_id=snapshot.id,
        )
        return ProductionDurationContractV2(
            **profile_values,
            duration_contract_hash=duration_hash,
            source_profile_version_id=profile.id,
            source_policy_snapshot_id=snapshot.id,
        )


class ProductionPackageService:
    def __init__(self, session: Session):
        self.session = session
        self.duration_resolver = ChannelDurationContractResolver(session)

    def create_package(
        self, request: ProductionPackageCreateV2
    ) -> ProductionPackageReadV2:
        canonical = _canonical_package_content(request.content)
        self._validate_live_authority(canonical)
        self._lock_package_scope(canonical.video_project_id)
        self._validate_exact_content_refs(canonical)
        artifact = self._package_artifact(canonical.video_project_id)
        current = self._current_version(artifact) if artifact is not None else None
        canonical_payload = canonical.model_dump(mode="json")
        expected_hash = semantic_hash(canonical_payload)
        if current is not None and current.content_hash == expected_hash:
            return self.read_package(current.id)
        if current is not None:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_REVISION_SERVICE_REQUIRED"
            )
        if canonical.revision is not None:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_INITIAL_REVISION_FORBIDDEN"
            )
        return self._persist_package_version(
            canonical=canonical,
            created_by_user_id=request.created_by_user_id,
            artifact=artifact,
            parent=None,
        )

    def _persist_package_version(
        self,
        *,
        canonical: ProductionPackageContentV2,
        created_by_user_id: uuid.UUID,
        artifact: Artifact | None,
        parent: ArtifactVersion | None,
    ) -> ProductionPackageReadV2:
        if artifact is None:
            if parent is not None:
                raise ValidationFailureError(
                    "PRODUCTION_PACKAGE_REVISION_ARTIFACT_MISSING"
                )
            artifact = ArtifactService(self.session).create_artifact(
                data=ArtifactCreate(
                    video_project_id=canonical.video_project_id,
                    artifact_type=PRODUCTION_PACKAGE_ARTIFACT_TYPE,
                    created_by_user_id=created_by_user_id,
                ),
                correlation_id="phase3-production-package-create",
                trusted_authority_write=True,
            )
        elif parent is not None and parent.artifact_id != artifact.id:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_REVISION_PARENT_MISMATCH"
            )
        elif parent is None and artifact.current_version_id is not None:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_INITIAL_ARTIFACT_NOT_EMPTY"
            )
        canonical_payload = canonical.model_dump(mode="json")
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=parent.id if parent is not None else None,
                content=canonical_payload,
                status="submitted",
                created_by_user_id=created_by_user_id,
                external_entity_refs=_external_refs(canonical),
                evidence_refs=[
                    *[
                        item.model_dump(mode="json")
                        for item in canonical.research_refs
                    ],
                    *[
                        item.model_dump(mode="json")
                        for item in canonical.rights_disclosure_refs
                    ],
                ],
                context_refs=[
                    canonical.effective_context_ref.model_dump(mode="json"),
                    {
                        "type": "channel_profile_version",
                        "id": str(canonical.channel_profile_version_id),
                        "content_hash": canonical.channel_profile_hash,
                    },
                    {
                        "type": "compiled_channel_policy_snapshot",
                        "id": str(canonical.compiled_policy_snapshot_id),
                        "content_hash": canonical.compiled_policy_snapshot_hash,
                    },
                ],
                packaging_metadata={
                    "schema_version": PRODUCTION_PACKAGE_SCHEMA_V2,
                    "authority_classification": "CANONICAL_V2_AUTHORITY",
                },
            ),
            correlation_id="phase3-production-package-version-create",
            trusted_authority_write=True,
        )
        return self.read_package(version.id)

    def revise_package(
        self, request: ProductionPackageRevisionRequestV2
    ) -> ProductionPackageReadV2:
        parent = self._require_package_version(
            request.package_artifact_version_id
        )
        base = ProductionPackageContentV2.model_validate(parent.content)
        self._lock_package_scope(base.video_project_id)
        artifact = self.session.get(Artifact, parent.artifact_id)
        current = self._current_version(artifact)
        if current is None or current.id != parent.id:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_REVISION_PARENT_NOT_CURRENT"
            )
        updates = deepcopy(request.content_updates)
        updates.pop("schema_version", None)
        updates.pop("authority_classification", None)
        merged = _deep_merge(base.model_dump(mode="python"), updates)
        readiness = dict(merged.get("readiness_evidence") or {})
        readiness["new_planning_cycle"] = request.new_planning_cycle
        merged["readiness_evidence"] = readiness
        merged["revision"] = ProductionRevisionV2(
            parent_package_artifact_version_id=parent.id,
            parent_package_hash=parent.content_hash,
            materiality=request.materiality,
            affected_gate_keys=sorted(set(request.affected_gate_keys)),
            policy_authorized_repair=request.policy_authorized_repair,
            revision_reason_codes=request.revision_reason_codes,
        ).model_dump(mode="python")
        candidate = _canonical_package_content(
            ProductionPackageContentV2.model_validate(merged)
        )
        classified_materiality = _classify_revision_materiality(base, candidate)
        if request.materiality != classified_materiality:
            raise ValidationFailureError(
                "PRODUCTION_REVISION_MATERIALITY_CLASSIFICATION_MISMATCH:"
                f"{classified_materiality.value}"
            )
        if (
            request.materiality
            == ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR
            and not request.policy_authorized_repair
        ):
            raise ValidationFailureError(
                "NON_MATERIAL_REPAIR_POLICY_AUTHORIZATION_REQUIRED"
            )
        if semantic_hash(candidate.model_dump(mode="json")) == parent.content_hash:
            raise ValidationFailureError("PRODUCTION_PACKAGE_REVISION_NO_CHANGE")
        self._validate_live_authority(candidate)
        self._validate_exact_content_refs(candidate)
        return self._persist_package_version(
            canonical=candidate,
            created_by_user_id=request.created_by_user_id,
            artifact=artifact,
            parent=parent,
        )

    def read_package(
        self, package_artifact_version_id: uuid.UUID
    ) -> ProductionPackageReadV2:
        version = self._require_package_version(package_artifact_version_id)
        content = ProductionPackageContentV2.model_validate(version.content)
        receipt = self._receipt_for_package(version.id, version.content_hash)
        return ProductionPackageReadV2(
            artifact_id=version.artifact_id,
            artifact_version_id=version.id,
            version_number=version.version_number,
            parent_version_id=version.parent_version_id,
            canonical_hash=version.content_hash,
            readiness_state=(
                "READY_FOR_PRODUCTION" if receipt is not None else "BUILT"
            ),
            content=content,
        )

    def validate_for_readiness(
        self, package_artifact_version_id: uuid.UUID
    ) -> ProductionPackageContentV2:
        version = self._require_package_version(package_artifact_version_id)
        content = ProductionPackageContentV2.model_validate(version.content)
        artifact = self.session.get(Artifact, version.artifact_id)
        if artifact is None or artifact.current_version_id != version.id:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_CURRENT_VERSION_REQUIRED"
            )
        if semantic_hash(content.model_dump(mode="json")) != version.content_hash:
            raise ValidationFailureError("PRODUCTION_PACKAGE_HASH_MISMATCH")
        self._validate_live_authority(content)
        self._validate_exact_content_refs(content)
        return content

    def require_ready_projection_authority(
        self,
        *,
        project_id: uuid.UUID,
        package_artifact_version_id: uuid.UUID | None = None,
        package_hash: str | None = None,
    ) -> tuple[ArtifactVersion, ProductionPackageContentV2]:
        """Resolve the one current ready v2 package for a downstream projection."""

        artifact = self._package_artifact(project_id)
        current = self._current_version(artifact)
        if current is None:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_V2_PROJECTION_AUTHORITY_REQUIRED"
            )
        if (
            package_artifact_version_id is not None
            and package_artifact_version_id != current.id
        ):
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_V2_PROJECTION_VERSION_MISMATCH"
            )
        if package_hash is not None and package_hash != current.content_hash:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_V2_PROJECTION_HASH_MISMATCH"
            )
        content = self.validate_for_readiness(current.id)
        if self._receipt_for_package(current.id, current.content_hash) is None:
            raise ValidationFailureError(
                "PRODUCTION_READINESS_RECEIPT_REQUIRED"
            )
        return current, content

    def _validate_live_authority(
        self, content: ProductionPackageContentV2
    ) -> None:
        project = self.session.get(VideoProject, content.video_project_id)
        if project is None:
            raise NotFoundError(
                f"video project not found: {content.video_project_id}"
            )
        if (
            project.company_id != content.company_id
            or project.channel_workspace_id != content.channel_workspace_id
            or project.channel_profile_version_id
            != content.channel_profile_version_id
            or project.policy_snapshot_id != content.compiled_policy_snapshot_id
            or getattr(project, "schema_version", "v1") != "v2"
            or getattr(project, "project_admission_decision_id", None)
            != content.project_admission_decision_id
            or getattr(project, "production_lane", None)
            != content.production_lane.value
            or getattr(project, "content_mode", None) != content.content_mode.value
            or getattr(project, "assignment_mode", None)
            != content.assignment_mode.value
            or getattr(project, "series_plan_id", None) != content.series_plan_id
            or getattr(project, "series_run_id", None) != content.series_run_id
            or getattr(project, "episode_number", None) != content.episode_number
            or getattr(project, "episode_role", None) != content.episode_role
            or getattr(project, "standalone_reason_code", None)
            != content.standalone_reason_code
        ):
            raise ValidationFailureError("PRODUCTION_PACKAGE_PROJECT_SCOPE_MISMATCH")
        profile = self.session.get(
            ChannelProfileVersion, content.channel_profile_version_id
        )
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot,
            content.compiled_policy_snapshot_id,
        )
        admission = self.session.get(
            ProjectAdmissionDecision,
            content.project_admission_decision_id,
        )
        if profile is None or snapshot is None or admission is None:
            raise ValidationFailureError("PRODUCTION_PACKAGE_AUTHORITY_MISSING")
        if profile.profile_input_hash != content.channel_profile_hash:
            raise ValidationFailureError("PRODUCTION_PACKAGE_PROFILE_HASH_MISMATCH")
        if snapshot.content_hash != content.compiled_policy_snapshot_hash:
            raise ValidationFailureError("PRODUCTION_PACKAGE_POLICY_HASH_MISMATCH")
        if (
            getattr(admission, "schema_version", "v1") != "v2"
            or getattr(admission, "company_id", None) != content.company_id
            or getattr(admission, "channel_workspace_id", None)
            != content.channel_workspace_id
            or getattr(admission, "channel_profile_version_id", None)
            != content.channel_profile_version_id
            or getattr(admission, "policy_snapshot_id", None)
            != content.compiled_policy_snapshot_id
            or getattr(admission, "production_lane", None)
            != content.production_lane.value
            or getattr(admission, "content_mode", None) != content.content_mode.value
            or getattr(admission, "assignment_mode", None)
            != content.assignment_mode.value
            or getattr(admission, "series_plan_id", None) != content.series_plan_id
            or getattr(admission, "series_run_id", None) != content.series_run_id
            or getattr(admission, "episode_number", None) != content.episode_number
            or getattr(admission, "episode_role", None) != content.episode_role
            or getattr(admission, "standalone_reason_code", None)
            != content.standalone_reason_code
            or admission.decision != "ADMIT"
            or admission.admitted_video_project_id != project.id
        ):
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_ADMISSION_NOT_ADMITTED"
            )
        if _admission_hash(admission) != content.project_admission_decision_hash:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_ADMISSION_HASH_MISMATCH"
            )
        exact_duration = self.duration_resolver.resolve(
            profile_version_id=content.channel_profile_version_id,
            policy_snapshot_id=content.compiled_policy_snapshot_id,
            production_lane=content.production_lane,
        )
        if exact_duration != content.duration_contract:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_DURATION_CONTRACT_MISMATCH"
            )
        try:
            admission_duration = ProductionDurationContractV2.model_validate(
                getattr(admission, "duration_contract", None)
            )
            project_duration = ProductionDurationContractV2.model_validate(
                getattr(project, "duration_contract", None)
            )
        except Exception as exc:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_ADMISSION_DURATION_MISSING"
            ) from exc
        if admission_duration != exact_duration or project_duration != exact_duration:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_ADMISSION_DURATION_MISMATCH"
            )
        if content.effective_context_ref.id is None:
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_EFFECTIVE_CONTEXT_ID_REQUIRED"
            )
        effective = self.session.get(
            EffectiveChannelRuntimeContextSnapshot,
            content.effective_context_ref.id,
        )
        if (
            effective is None
            or project.effective_context_snapshot_id != effective.id
            or effective.video_project_id != project.id
            or effective.company_id != content.company_id
            or effective.channel_workspace_id != content.channel_workspace_id
            or effective.context_hash != content.effective_context_ref.content_hash
            or effective.channel_profile_version_id != profile.id
            or effective.compiled_policy_snapshot_id != snapshot.id
            or effective.compile_status != "PASS"
        ):
            raise ValidationFailureError(
                "PRODUCTION_PACKAGE_EFFECTIVE_CONTEXT_MISMATCH"
            )

    def _validate_exact_content_refs(
        self, content: ProductionPackageContentV2
    ) -> None:
        resolved: dict[str, list[ArtifactVersion]] = {}
        for field_name, ref, allowed_types, same_project in _artifact_ref_bindings(
            content
        ):
            if ref.artifact_version_id is None:
                raise ValidationFailureError(
                    f"PRODUCTION_PACKAGE_ARTIFACT_VERSION_REQUIRED:{field_name}"
                )
            version = self.session.scalar(
                select(ArtifactVersion)
                .where(ArtifactVersion.id == ref.artifact_version_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            artifact = (
                self.session.scalar(
                    select(Artifact)
                    .where(Artifact.id == version.artifact_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if version is not None
                else None
            )
            ready_parent_package = bool(
                field_name == "parent_derivative_lineage"
                and version is not None
                and artifact is not None
                and artifact.artifact_type
                == PRODUCTION_PACKAGE_ARTIFACT_TYPE
                and self._receipt_for_package(
                    version.id,
                    version.content_hash,
                )
                is not None
            )
            if (
                version is None
                or artifact is None
                or version.content_hash != ref.content_hash
                or artifact.artifact_type not in allowed_types
                or artifact.current_version_id != version.id
                or (
                    same_project
                    and artifact.video_project_id != content.video_project_id
                )
            ):
                raise ValidationFailureError(
                    f"PRODUCTION_PACKAGE_ARTIFACT_REF_MISMATCH:{field_name}"
                )
            if (
                not ready_parent_package
                and (
                    artifact.status != "approved"
                    or version.status != "approved"
                )
            ):
                raise ValidationFailureError(
                    f"PRODUCTION_PACKAGE_ARTIFACT_NOT_APPROVED:{field_name}"
                )
            if (
                artifact.artifact_type
                in {
                    PRODUCTION_PACKAGE_ARTIFACT_TYPE,
                    PRODUCTION_READINESS_ARTIFACT_TYPE,
                }
                and not _has_trusted_domain_authority(
                    version,
                    artifact.artifact_type,
                )
            ):
                raise ValidationFailureError(
                    "PRODUCTION_PACKAGE_DOMAIN_AUTHORITY_REQUIRED:"
                    f"{field_name}"
                )
            if ref.version is not None and str(ref.version) != str(
                version.version_number
            ):
                raise ValidationFailureError(
                    f"PRODUCTION_PACKAGE_ARTIFACT_VERSION_MISMATCH:{field_name}"
                )
            resolved.setdefault(field_name, []).append(version)
        _validate_derived_readiness_evidence(content, resolved)

    def _package_artifact(self, project_id: uuid.UUID) -> Artifact | None:
        return self.session.scalars(
            select(Artifact)
            .where(Artifact.video_project_id == project_id)
            .where(Artifact.artifact_type == PRODUCTION_PACKAGE_ARTIFACT_TYPE)
            .order_by(Artifact.created_at.asc())
        ).first()

    def _lock_package_scope(self, project_id: uuid.UUID) -> VideoProject:
        project = self.session.scalar(
            select(VideoProject)
            .where(VideoProject.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise NotFoundError(f"video project not found: {project_id}")
        return project

    def _require_package_version(
        self, version_id: uuid.UUID
    ) -> ArtifactVersion:
        version = self.session.get(ArtifactVersion, version_id)
        if version is None:
            raise NotFoundError(f"production package version not found: {version_id}")
        artifact = self.session.get(Artifact, version.artifact_id)
        if (
            artifact is None
            or artifact.artifact_type != PRODUCTION_PACKAGE_ARTIFACT_TYPE
            or (version.content or {}).get("schema_version")
            != PRODUCTION_PACKAGE_SCHEMA_V2
            or not _has_trusted_domain_authority(
                version,
                PRODUCTION_PACKAGE_ARTIFACT_TYPE,
            )
        ):
            raise ValidationFailureError("PRODUCTION_PACKAGE_V2_REQUIRED")
        return version

    def _current_version(self, artifact: Artifact | None) -> ArtifactVersion | None:
        if artifact is None or artifact.current_version_id is None:
            return None
        return self.session.get(ArtifactVersion, artifact.current_version_id)

    def _receipt_for_package(
        self, package_version_id: uuid.UUID, package_hash: str
    ) -> ArtifactVersion | None:
        package = self.session.get(ArtifactVersion, package_version_id)
        if package is None:
            return None
        package_artifact = self.session.get(Artifact, package.artifact_id)
        if (
            package_artifact is None
            or package_artifact.artifact_type
            != PRODUCTION_PACKAGE_ARTIFACT_TYPE
            or package_artifact.current_version_id != package.id
            or package.content_hash != package_hash
            or not _has_trusted_domain_authority(
                package,
                PRODUCTION_PACKAGE_ARTIFACT_TYPE,
            )
        ):
            return None
        artifact = self.session.scalars(
            select(Artifact)
            .where(
                Artifact.video_project_id
                == _package_project_id(self.session, package)
            )
            .where(
                Artifact.artifact_type == PRODUCTION_READINESS_ARTIFACT_TYPE
            )
            .order_by(Artifact.created_at.asc())
        ).first()
        current = self._current_version(artifact)
        if (
            artifact is None
            or current is None
            or artifact.status != "approved"
            or current.status != "approved"
            or semantic_hash(current.content or {}) != current.content_hash
            or not _has_trusted_domain_authority(
                current,
                PRODUCTION_READINESS_ARTIFACT_TYPE,
            )
        ):
            return None
        try:
            payload = ProductionReadinessReceiptContentV2.model_validate(
                current.content
            )
            package_content = ProductionPackageContentV2.model_validate(
                package.content
            )
        except Exception:
            return None
        if (
            payload.production_package_artifact_version_id
            != package_version_id
            or payload.production_package_version != package.version_number
            or payload.production_package_hash != package_hash
            or payload.project_admission_decision_id
            != package_content.project_admission_decision_id
            or payload.project_admission_decision_hash
            != package_content.project_admission_decision_hash
            or payload.channel_profile_version_id
            != package_content.channel_profile_version_id
            or payload.channel_profile_hash
            != package_content.channel_profile_hash
            or payload.compiled_policy_snapshot_id
            != package_content.compiled_policy_snapshot_id
            or payload.compiled_policy_snapshot_hash
            != package_content.compiled_policy_snapshot_hash
            or payload.duration_contract_hash
            != package_content.duration_contract.duration_contract_hash
            or payload.provider_execution_plan_hash
            != package_content.provider_execution_plan_ref.content_hash
            or payload.budget_scope_hash
            != package_content.budget_scope_ref.content_hash
            or payload.readiness_evaluator_version
            != READINESS_EVALUATOR_VERSION
            or payload.research_evidence_refs
            != package_content.research_refs
            or payload.rights_evidence_refs
            != package_content.rights_disclosure_refs
        ):
            return None
        gate_keys = [item.gate_key for item in payload.required_gate_runs]
        if (
            len(gate_keys) != len(REQUIRED_PRODUCTION_GATE_KEYS)
            or set(gate_keys) != set(REQUIRED_PRODUCTION_GATE_KEYS)
        ):
            return None
        for binding in payload.required_gate_runs:
            run = self.session.get(GateRun, binding.gate_run_id)
            if (
                run is None
                or run.target_type != "artifact_version"
                or run.target_id != package.id
                or run.result != "PASS"
            ):
                return None
            try:
                actual = _gate_run_binding(run)
            except ValidationFailureError:
                return None
            if actual != binding:
                return None
        return current


class ProductionReadinessService:
    def __init__(self, session: Session):
        self.session = session
        self.packages = ProductionPackageService(session)

    def evaluate(
        self,
        *,
        package_artifact_version_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
    ) -> ProductionReadinessEvaluationV2:
        package_content = self.packages.validate_for_readiness(
            package_artifact_version_id
        )
        self.packages._lock_package_scope(
            package_content.video_project_id
        )
        package_content = self.packages.validate_for_readiness(
            package_artifact_version_id
        )
        package_version = self.session.get(
            ArtifactVersion, package_artifact_version_id
        )
        assert package_version is not None
        existing = self.packages._receipt_for_package(
            package_version.id, package_version.content_hash
        )
        if existing is not None:
            receipt = _read_receipt(existing)
            return ProductionReadinessEvaluationV2(
                status="READY_FOR_PRODUCTION",
                package=self.packages.read_package(package_version.id),
                gate_run_ids=[
                    item.gate_run_id for item in receipt.content.required_gate_runs
                ],
                receipt=receipt,
            )

        GateDefinitionService(self.session).seed_definitions()
        runner = GateRunnerService(self.session)
        runs: list[GateRun] = []
        for gate_key in REQUIRED_PRODUCTION_GATE_KEYS:
            runs.append(
                runner.run_gate(
                    data=GateRunCreate(
                        gate_key=gate_key,
                        target_type="artifact_version",
                        target_id=package_version.id,
                        created_by_user_id=created_by_user_id,
                    ),
                    correlation_id="phase3-production-readiness-gate",
                )
            )
        blocked = [run for run in runs if run.result != "PASS"]
        if blocked:
            package_read = self.packages.read_package(package_version.id).model_copy(
                update={"readiness_state": "BLOCKED"}
            )
            return ProductionReadinessEvaluationV2(
                status="BLOCKED",
                package=package_read,
                gate_run_ids=[run.id for run in runs],
                blocker_reason_codes=sorted(
                    {
                        reason
                        for run in blocked
                        for reason in (run.reason_codes or [])
                    }
                ),
            )

        gate_bindings = [_gate_run_binding(run) for run in runs]
        receipt_content = ProductionReadinessReceiptContentV2(
            production_package_artifact_version_id=package_version.id,
            production_package_version=package_version.version_number,
            production_package_hash=package_version.content_hash,
            project_admission_decision_id=package_content.project_admission_decision_id,
            project_admission_decision_hash=package_content.project_admission_decision_hash,
            channel_profile_version_id=package_content.channel_profile_version_id,
            channel_profile_hash=package_content.channel_profile_hash,
            compiled_policy_snapshot_id=package_content.compiled_policy_snapshot_id,
            compiled_policy_snapshot_hash=package_content.compiled_policy_snapshot_hash,
            duration_contract_hash=package_content.duration_contract.duration_contract_hash,
            required_gate_runs=gate_bindings,
            research_evidence_refs=package_content.research_refs,
            rights_evidence_refs=package_content.rights_disclosure_refs,
            provider_execution_plan_hash=package_content.provider_execution_plan_ref.content_hash,
            budget_scope_hash=package_content.budget_scope_ref.content_hash,
            readiness_evaluator_version=READINESS_EVALUATOR_VERSION,
        )
        receipt = self._persist_receipt(
            package_content=package_content,
            receipt_content=receipt_content,
            created_by_user_id=created_by_user_id,
        )
        return ProductionReadinessEvaluationV2(
            status="READY_FOR_PRODUCTION",
            package=self.packages.read_package(package_version.id),
            gate_run_ids=[
                item.gate_run_id
                for item in receipt.content.required_gate_runs
            ],
            receipt=receipt,
        )

    def _persist_receipt(
        self,
        *,
        package_content: ProductionPackageContentV2,
        receipt_content: ProductionReadinessReceiptContentV2,
        created_by_user_id: uuid.UUID,
    ) -> ProductionReadinessReceiptReadV2:
        self.packages._lock_package_scope(
            package_content.video_project_id
        )
        existing = self.packages._receipt_for_package(
            receipt_content.production_package_artifact_version_id,
            receipt_content.production_package_hash,
        )
        if existing is not None:
            return _read_receipt(existing)
        artifact = self.session.scalars(
            select(Artifact)
            .where(
                Artifact.video_project_id == package_content.video_project_id
            )
            .where(
                Artifact.artifact_type == PRODUCTION_READINESS_ARTIFACT_TYPE
            )
            .order_by(Artifact.created_at.asc())
        ).first()
        current = self.packages._current_version(artifact)
        payload = receipt_content.model_dump(mode="json")
        if current is not None and current.content_hash == semantic_hash(payload):
            return _read_receipt(current)
        if artifact is None:
            artifact = ArtifactService(self.session).create_artifact(
                data=ArtifactCreate(
                    video_project_id=package_content.video_project_id,
                    artifact_type=PRODUCTION_READINESS_ARTIFACT_TYPE,
                    created_by_user_id=created_by_user_id,
                ),
                correlation_id="phase3-production-readiness-receipt-create",
                trusted_authority_write=True,
            )
        version = ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=current.id if current is not None else None,
                content=payload,
                status="approved",
                created_by_user_id=created_by_user_id,
                evidence_refs=[
                    *[
                        item.model_dump(mode="json")
                        for item in receipt_content.research_evidence_refs
                    ],
                    *[
                        item.model_dump(mode="json")
                        for item in receipt_content.rights_evidence_refs
                    ],
                    *[
                        {
                            "type": "gate_run",
                            "id": str(item.gate_run_id),
                            "content_hash": item.gate_run_hash,
                        }
                        for item in receipt_content.required_gate_runs
                    ],
                ],
                context_refs=[
                    {
                        "type": "production_package",
                        "artifact_version_id": str(
                            receipt_content.production_package_artifact_version_id
                        ),
                        "content_hash": receipt_content.production_package_hash,
                    }
                ],
            ),
            correlation_id="phase3-production-readiness-receipt-version",
            trusted_authority_write=True,
        )
        artifact.status = "approved"
        self.session.flush()
        return _read_receipt(version)


def _gate_run_binding(run: GateRun) -> GateRunBindingV2:
    if run.result != "PASS":
        raise ValidationFailureError("READINESS_RECEIPT_REQUIRES_PASS_GATE_RUNS")
    stable = {
        "gate_definition_version_id": str(run.gate_definition_version_id),
        "gate_key": run.gate_key,
        "target_type": run.target_type,
        "target_id": str(run.target_id),
        "input_snapshot_hash": run.input_snapshot_hash,
        "result": run.result,
        "reason_codes": run.reason_codes,
        "evidence_refs": run.evidence_refs,
        "metric_refs": run.metric_refs,
        "freshness_state": run.freshness_state,
        "confidence_level": run.confidence_level,
        "confidence_reason_codes": run.confidence_reason_codes,
        "decision_basis": run.decision_basis,
    }
    return GateRunBindingV2(
        gate_run_id=run.id,
        gate_definition_version_id=run.gate_definition_version_id,
        gate_key=run.gate_key,
        result="PASS",
        input_snapshot_hash=run.input_snapshot_hash,
        gate_run_hash=semantic_hash(stable),
    )


def _read_receipt(version: ArtifactVersion) -> ProductionReadinessReceiptReadV2:
    return ProductionReadinessReceiptReadV2(
        artifact_id=version.artifact_id,
        artifact_version_id=version.id,
        version_number=version.version_number,
        receipt_hash=version.content_hash,
        content=ProductionReadinessReceiptContentV2.model_validate(version.content),
    )


def _canonical_package_content(
    content: ProductionPackageContentV2,
) -> ProductionPackageContentV2:
    payload = content.model_dump(mode="python")
    for key in (
        "research_refs",
        "source_refs",
        "niche_market_gate_refs",
        "thumbnail_refs",
        "rights_disclosure_refs",
    ):
        payload[key] = sorted(
            payload[key],
            key=lambda item: (
                str(item.get("type") or ""),
                str(item.get("content_hash") or ""),
                str(item.get("ref") or ""),
            ),
        )
    revision = payload.get("revision")
    if isinstance(revision, dict):
        revision["affected_gate_keys"] = sorted(
            set(revision.get("affected_gate_keys") or [])
        )
        revision["revision_reason_codes"] = sorted(
            set(revision.get("revision_reason_codes") or [])
        )
    evidence = payload.get("readiness_evidence")
    if isinstance(evidence, dict):
        evidence["unresolved_exception_types"] = sorted(
            set(evidence.get("unresolved_exception_types") or [])
        )
    return ProductionPackageContentV2.model_validate(payload)


def _profile_duration_values(
    payload: dict[str, Any],
    *,
    production_lane: str | None = None,
) -> dict[str, Any] | None:
    format_strategy = _dict(payload.get("format_strategy"))
    lane_contracts = _dict(format_strategy.get("duration_contracts"))
    candidates = (
        lane_contracts.get(production_lane) if production_lane else None,
        payload.get("duration_contract_v2"),
        payload.get("duration_contract"),
        format_strategy.get("duration_contract_v2"),
        format_strategy.get("duration_contract"),
        _dict(format_strategy.get("long_form")).get("duration_contract"),
    )
    return _normalize_duration_values(candidates)


def _snapshot_duration_values(
    payload: dict[str, Any],
    *,
    production_lane: str | None = None,
) -> dict[str, Any] | None:
    channel_contract = _dict(payload.get("channel_contract_json"))
    format_policy = _dict(channel_contract.get("format_policy"))
    legacy = _dict(payload.get("legacy_policy_sections"))
    default_playbook = _dict(legacy.get("default_playbook"))
    if not default_playbook:
        default_playbook = _dict(payload.get("default_playbook"))
    strategy = _dict(default_playbook.get("format_strategy"))
    format_lane_contracts = _dict(format_policy.get("duration_contracts"))
    strategy_lane_contracts = _dict(strategy.get("duration_contracts"))
    candidates = (
        (
            format_lane_contracts.get(production_lane)
            if production_lane
            else None
        ),
        (
            strategy_lane_contracts.get(production_lane)
            if production_lane
            else None
        ),
        payload.get("duration_contract_v2"),
        payload.get("duration_contract"),
        format_policy.get("duration_contract_v2"),
        format_policy.get("duration_contract"),
        _dict(format_policy.get("long_form")).get("duration_contract"),
        strategy.get("duration_contract_v2"),
        strategy.get("duration_contract"),
        _dict(strategy.get("long_form")).get("duration_contract"),
    )
    return _normalize_duration_values(candidates)


def _normalize_duration_values(
    candidates: tuple[Any, ...],
) -> dict[str, Any] | None:
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        try:
            minimum_duration_ms = int(candidate["minimum_duration_ms"])
            target_duration_ms = int(candidate["target_duration_ms"])
            maximum_duration_ms = int(candidate["maximum_duration_ms"])
            duration_contract_version = str(
                candidate["duration_contract_version"]
            )
        except (KeyError, TypeError, ValueError):
            continue
        if (
            minimum_duration_ms <= 0
            or not (
                minimum_duration_ms
                <= target_duration_ms
                <= maximum_duration_ms
            )
            or not duration_contract_version
        ):
            continue
        # Profile and compiled-policy authority deliberately stores only the
        # approved channel values.  The immutable row identities do not exist
        # until those rows are persisted, so their IDs and the final semantic
        # hash are frozen by ``resolve`` at the admission boundary.
        return {
            "minimum_duration_ms": minimum_duration_ms,
            "target_duration_ms": target_duration_ms,
            "maximum_duration_ms": maximum_duration_ms,
            "duration_contract_version": duration_contract_version,
        }
    return None


def _admission_hash(admission: ProjectAdmissionDecision) -> str:
    exact = getattr(admission, "decision_hash", None)
    if isinstance(exact, str) and len(exact) == 64:
        return exact
    stable = {
        "channel_daily_run_id": str(admission.channel_daily_run_id),
        "daily_idea_decision_id": str(admission.daily_idea_decision_id),
        "idea_market_preflight_id": (
            str(admission.idea_market_preflight_id)
            if admission.idea_market_preflight_id
            else None
        ),
        "budget_gate_result": admission.budget_gate_result,
        "readiness_gate_refs": admission.readiness_gate_refs,
        "decision": admission.decision,
        "reason_codes": admission.reason_codes,
        "evidence_refs": admission.evidence_refs,
        "admitted_video_project_id": (
            str(admission.admitted_video_project_id)
            if admission.admitted_video_project_id
            else None
        ),
        "created_artifact_refs": admission.created_artifact_refs,
    }
    return semantic_hash(stable)


def admission_decision_hash(admission: ProjectAdmissionDecision) -> str:
    """Public helper for exact package builders and tests."""

    return _admission_hash(admission)


def _has_trusted_domain_authority(
    version: ArtifactVersion,
    artifact_type: str,
) -> bool:
    metadata = version.packaging_metadata or {}
    authority = metadata.get("_vcos_domain_authority")
    return bool(
        isinstance(authority, dict)
        and authority.get("schema_version") == "vcos.domain-authority.v1"
        and authority.get("writer") == "server_domain_service"
        and authority.get("artifact_type") == artifact_type
        and authority.get("content_hash") == version.content_hash
    )


def _artifact_ref_bindings(
    content: ProductionPackageContentV2,
) -> list[tuple[str, ExactContentRefV2, frozenset[str], bool]]:
    bindings: list[
        tuple[str, ExactContentRefV2, frozenset[str], bool]
    ] = []

    def add(
        field_name: str,
        refs: list[ExactContentRefV2],
        artifact_types: set[str],
        *,
        same_project: bool = True,
    ) -> None:
        bindings.extend(
            (
                field_name,
                ref,
                frozenset(artifact_types),
                same_project,
            )
            for ref in refs
        )

    add("research_refs", content.research_refs, {"research_pack"})
    add("source_refs", content.source_refs, {"source_pack"})
    add(
        "niche_market_gate_refs",
        content.niche_market_gate_refs,
        {
            "niche_alignment_dossier",
            "market_alignment_dossier",
            "market_gate_results",
            "gate_results",
        },
    )
    add("script_ref", [content.script_ref], {"script"})
    add("visual_plan_ref", [content.visual_plan_ref], {"visual_plan"})
    add(
        "thumbnail_refs",
        content.thumbnail_refs,
        {"thumbnail_brief", "thumbnail_package"},
    )
    add(
        "metadata_ref",
        [content.metadata_ref],
        {"publishing_metadata_package", "publish_package_draft"},
    )
    add(
        "rights_disclosure_refs",
        content.rights_disclosure_refs,
        {
            "rights_disclosure_completeness_report",
            "publish_risk_dossier",
            "asset_provenance_plan",
            "claim_evidence_ledger",
        },
    )
    add(
        "provider_execution_plan_ref",
        [content.provider_execution_plan_ref],
        {"provider_execution_plan"},
    )
    add(
        "budget_scope_ref",
        [content.budget_scope_ref],
        {"cost_estimate_snapshot"},
    )
    add(
        "destination_binding_ref",
        [content.destination_binding_ref],
        {"destination_binding"},
    )
    if content.parent_derivative_lineage is not None:
        add(
            "parent_derivative_lineage",
            [content.parent_derivative_lineage],
            {"production_package", "mr1_final_media_lineage_receipt"},
            same_project=False,
        )
    return bindings


def _validate_derived_readiness_evidence(
    content: ProductionPackageContentV2,
    resolved: dict[str, list[ArtifactVersion]],
) -> None:
    evidence = content.readiness_evidence
    research_complete = all(
        _artifact_evidence_complete(version.content)
        for version in resolved["research_refs"]
    ) and all(
        _source_evidence_complete(version.content)
        for version in resolved["source_refs"]
    )
    niche_market_pass = all(
        _artifact_passes(version.content)
        for version in resolved["niche_market_gate_refs"]
    )
    script_content = resolved["script_ref"][0].content
    script_facts = _script_readiness_facts(script_content)
    visual_metadata_pass = all(
        _artifact_passes(version.content)
        for field_name in (
            "visual_plan_ref",
            "thumbnail_refs",
            "metadata_ref",
            "destination_binding_ref",
        )
        for version in resolved[field_name]
    )
    rights_pass = all(
        _artifact_passes(version.content)
        for version in resolved["rights_disclosure_refs"]
    )
    provider_pass = _artifact_passes(
        resolved["provider_execution_plan_ref"][0].content
    )
    budget_pass = _artifact_passes(
        resolved["budget_scope_ref"][0].content
    )
    checks = (
        (
            evidence.research_evidence_complete,
            research_complete,
            "RESEARCH_EVIDENCE_FACT_MISMATCH",
        ),
        (
            evidence.niche_market_gates_pass,
            niche_market_pass,
            "NICHE_MARKET_GATE_FACT_MISMATCH",
        ),
        (
            evidence.script_gates_pass,
            _artifact_passes(script_content),
            "SCRIPT_GATE_FACT_MISMATCH",
        ),
        (
            evidence.visual_thumbnail_metadata_gates_pass,
            visual_metadata_pass,
            "VISUAL_METADATA_GATE_FACT_MISMATCH",
        ),
        (
            evidence.rights_disclosure_gates_pass,
            rights_pass,
            "RIGHTS_DISCLOSURE_GATE_FACT_MISMATCH",
        ),
        (
            evidence.provider_plan_valid,
            provider_pass,
            "PROVIDER_PLAN_FACT_MISMATCH",
        ),
        (
            evidence.budget_scope_valid,
            budget_pass,
            "BUDGET_SCOPE_FACT_MISMATCH",
        ),
        (
            evidence.package_integrity_inputs_complete,
            all(
                (
                    research_complete,
                    niche_market_pass,
                    visual_metadata_pass,
                    rights_pass,
                    provider_pass,
                    budget_pass,
                )
            ),
            "PACKAGE_INTEGRITY_FACT_MISMATCH",
        ),
        (
            evidence.editorial_depth_sufficient,
            script_facts["editorial_depth_sufficient"],
            "EDITORIAL_DEPTH_FACT_MISMATCH",
        ),
        (
            evidence.anti_padding_pass,
            script_facts["anti_padding_pass"],
            "ANTI_PADDING_FACT_MISMATCH",
        ),
    )
    for declared, derived, reason in checks:
        if declared is not derived:
            raise ValidationFailureError(reason)
    exact_metrics = (
        (
            evidence.script_duration_ms,
            script_facts["script_duration_ms"],
            "SCRIPT_DURATION_FACT_MISMATCH",
        ),
        (
            evidence.supported_claim_count,
            script_facts["supported_claim_count"],
            "SUPPORTED_CLAIM_FACT_MISMATCH",
        ),
        (
            evidence.distinct_editorial_section_count,
            script_facts["distinct_editorial_section_count"],
            "EDITORIAL_SECTION_FACT_MISMATCH",
        ),
        (
            evidence.padding_phrase_hits,
            script_facts["padding_phrase_hits"],
            "PADDING_PHRASE_FACT_MISMATCH",
        ),
    )
    for declared, derived, reason in exact_metrics:
        if declared != derived:
            raise ValidationFailureError(reason)
    float_metrics = (
        (
            evidence.research_coverage_ratio,
            script_facts["research_coverage_ratio"],
            "RESEARCH_COVERAGE_FACT_MISMATCH",
        ),
        (
            evidence.repeated_sentence_ratio,
            script_facts["repeated_sentence_ratio"],
            "REPEATED_SENTENCE_FACT_MISMATCH",
        ),
    )
    for declared, derived, reason in float_metrics:
        if abs(float(declared) - float(derived)) > 0.000001:
            raise ValidationFailureError(reason)


def _artifact_passes(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if any(
        str(payload.get(key) or "").upper()
        in {"BLOCK", "FAIL", "FAILED", "REVIEW_REQUIRED"}
        for key in (
            "result",
            "status",
            "decision",
            "overall_verdict",
            "gate_status",
            "readiness_result",
        )
    ):
        return False
    return any(
        str(payload.get(key) or "").upper()
        in {"PASS", "PASSED", "VALID", "READY"}
        for key in (
            "result",
            "status",
            "decision",
            "overall_verdict",
            "gate_status",
            "readiness_result",
        )
    )


def _artifact_evidence_complete(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and (
            payload.get("evidence_complete") is True
            or (
                _artifact_passes(payload)
                and bool(payload.get("evidence_refs"))
            )
        )
    )


def _source_evidence_complete(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and _artifact_passes(payload)
        and (
            int(payload.get("source_count") or 0) > 0
            or bool(payload.get("sources"))
        )
    )


def _script_readiness_facts(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValidationFailureError("SCRIPT_READINESS_FACTS_MISSING")
    try:
        script_duration_ms = int(payload["estimated_duration_ms"])
        research_coverage_ratio = float(payload["research_coverage_ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationFailureError(
            "SCRIPT_READINESS_FACTS_MISSING"
        ) from exc
    supported_claims = payload.get("supported_claims")
    sections = payload.get("sections")
    if not isinstance(supported_claims, list) or not isinstance(sections, list):
        raise ValidationFailureError("SCRIPT_READINESS_FACTS_MISSING")
    texts = [
        str(item.get("text") or "").strip()
        for section in sections
        if isinstance(section, dict)
        for item in (
            section.get("sentences")
            if isinstance(section.get("sentences"), list)
            else [section]
        )
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    normalized = [
        re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", text.lower())).strip()
        for text in texts
    ]
    repeated_count = len(normalized) - len(set(normalized))
    repeated_ratio = (
        round(repeated_count / len(normalized), 6) if normalized else 0.0
    )
    canned_phrases = (
        "this keeps the claim tied to verified time savings before publication",
        "the workflow should reduce repeated handoffs while keeping human review in place",
        "operators still verify the numbers and exceptions before any public use",
        "the team monitors edge cases instead of repeating the full task manually",
        "this adds practical detail without changing the original hook promise",
    )
    joined = " ".join(text.lower() for text in texts)
    padding_phrase_hits = sum(joined.count(phrase) for phrase in canned_phrases)
    claim_count = len(supported_claims)
    section_count = len(sections)
    editorial_depth_sufficient = bool(
        claim_count >= 3
        and section_count >= 3
        and research_coverage_ratio >= 0.75
    )
    anti_padding_pass = padding_phrase_hits == 0 and repeated_ratio <= 0.20
    return {
        "script_duration_ms": script_duration_ms,
        "supported_claim_count": claim_count,
        "distinct_editorial_section_count": section_count,
        "research_coverage_ratio": research_coverage_ratio,
        "padding_phrase_hits": padding_phrase_hits,
        "repeated_sentence_ratio": repeated_ratio,
        "editorial_depth_sufficient": editorial_depth_sufficient,
        "anti_padding_pass": anti_padding_pass,
    }


def _all_bound_refs(
    content: ProductionPackageContentV2,
) -> list[ExactContentRefV2]:
    return [
        content.effective_context_ref,
        *content.research_refs,
        *content.source_refs,
        *content.niche_market_gate_refs,
        content.script_ref,
        content.visual_plan_ref,
        *content.thumbnail_refs,
        content.metadata_ref,
        *content.rights_disclosure_refs,
        content.provider_execution_plan_ref,
        content.budget_scope_ref,
        content.destination_binding_ref,
        *(
            [content.parent_derivative_lineage]
            if content.parent_derivative_lineage is not None
            else []
        ),
    ]


def _external_refs(content: ProductionPackageContentV2) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json") for item in _all_bound_refs(content)]


def _package_project_id(
    session: Session, package_version: ArtifactVersion
) -> uuid.UUID:
    artifact = session.get(Artifact, package_version.artifact_id)
    if artifact is None:
        raise ValidationFailureError("PRODUCTION_PACKAGE_ARTIFACT_MISSING")
    return artifact.video_project_id


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _classify_revision_materiality(
    base: ProductionPackageContentV2,
    candidate: ProductionPackageContentV2,
) -> ProductionPackageMateriality:
    before = base.model_dump(mode="json", exclude={"revision"})
    after = candidate.model_dump(mode="json", exclude={"revision"})
    before_evidence = dict(before.pop("readiness_evidence"))
    after_evidence = dict(after.pop("readiness_evidence"))
    before_evidence.pop("new_planning_cycle", None)
    after_evidence.pop("new_planning_cycle", None)
    changed = {
        key
        for key in sorted(set(before) | set(after))
        if before.get(key) != after.get(key)
    }
    if before_evidence != after_evidence:
        changed.add("readiness_evidence")

    technical_ref_fields = {
        "script_ref",
        "visual_plan_ref",
        "thumbnail_refs",
        "metadata_ref",
    }
    if changed and changed <= technical_ref_fields:
        all_hashes_unchanged = all(
            _ref_hashes(before.get(field)) == _ref_hashes(after.get(field))
            for field in changed
        )
        if all_hashes_unchanged:
            return ProductionPackageMateriality.NON_MATERIAL_TECHNICAL_REPAIR

    if changed & {
        "provider_execution_plan_ref",
        "budget_scope_ref",
    }:
        return ProductionPackageMateriality.MATERIAL_PROVIDER_OR_COST_CHANGE
    if changed & {
        "destination_binding_ref",
        "company_id",
        "channel_workspace_id",
        "channel_profile_version_id",
        "channel_profile_hash",
        "compiled_policy_snapshot_id",
        "compiled_policy_snapshot_hash",
        "effective_context_ref",
    }:
        return (
            ProductionPackageMateriality.MATERIAL_MARKET_OR_DESTINATION_CHANGE
        )
    if changed & {
        "research_refs",
        "source_refs",
        "niche_market_gate_refs",
        "rights_disclosure_refs",
    }:
        return (
            ProductionPackageMateriality.MATERIAL_RIGHTS_OR_EVIDENCE_CHANGE
        )
    if changed:
        return ProductionPackageMateriality.MATERIAL_EDITORIAL_CHANGE
    raise ValidationFailureError("PRODUCTION_PACKAGE_REVISION_NO_CHANGE")


def _ref_hashes(value: Any) -> list[str]:
    refs = value if isinstance(value, list) else [value]
    return sorted(
        str(ref.get("content_hash"))
        for ref in refs
        if isinstance(ref, dict)
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _jsonable(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    return value
