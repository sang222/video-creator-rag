import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, EventEnvelope
from app.contracts.gates import (
    GateDefinitionVersionCreate,
    GateRunCreate,
    PlatformPolicyCatalogCreate,
    PlatformPolicyVersionCreate,
    PolicyChangeRecordCreate,
    PolicyRevalidationBatchCreate,
    PolicySourceRefCreate,
)
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    Artifact,
    ArtifactVersion,
    AuditEvent,
    DomainEvent,
    GateDefinitionVersion,
    GateRun,
    PlatformPolicyCatalog,
    PlatformPolicyVersion,
    PolicyChangeRecord,
    PolicyRevalidationBatch,
    PolicySourceRef,
    ReviewTask,
    User,
    VideoProject,
)
from app.services.audit import AuditService
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.domain_events import DomainEventBus

GATE_DEFINITION_CATALOG = Path("config/gate_definition_catalog.yaml")

BUILTIN_GATE_KEYS = [
    "ai_use_disclosure_gate",
    "ai_provenance_gate",
    "rights_copyright_gate",
    "affiliate_disclosure_gate",
    "commercial_disclosure_gate",
    "platform_originality_gate",
    "repetitive_template_risk_gate",
    "brand_conflict_gate",
    "commercial_conflict_gate",
    "disclosure_placement_gate",
    "search_demand_gate",
    "distribution_readiness_gate",
    "packaging_expectation_gate",
    "privacy_retention_gate",
    "publish_risk_gate",
]

REVIEW_TYPE_BY_GATE = {
    "ai_use_disclosure_gate": "ai_disclosure",
    "ai_provenance_gate": "ai_disclosure",
    "rights_copyright_gate": "rights",
    "affiliate_disclosure_gate": "commercial_disclosure",
    "commercial_disclosure_gate": "commercial_disclosure",
    "brand_conflict_gate": "brand_conflict_review",
    "commercial_conflict_gate": "brand_conflict_review",
    "search_demand_gate": "search_demand_review",
    "distribution_readiness_gate": "distribution_readiness",
    "packaging_expectation_gate": "packaging_review",
    "privacy_retention_gate": "policy_review",
}

VALID_POLICY_TRANSITIONS = {
    "DRAFT": {"SOURCE_VERIFIED", "REJECTED", "MONITOR_ONLY"},
    "SOURCE_VERIFIED": {"DIFFED", "OPERATOR_REVIEW_REQUIRED", "REJECTED"},
    "DIFFED": {"IMPACT_CLASSIFIED", "OPERATOR_REVIEW_REQUIRED", "REJECTED"},
    "IMPACT_CLASSIFIED": {"CATALOG_PATCHED", "MONITOR_ONLY", "REJECTED"},
    "CATALOG_PATCHED": {"GATES_UPDATED", "READY_TO_ACTIVATE", "REJECTED"},
    "GATES_UPDATED": {"REVALIDATION_RUNNING", "READY_TO_ACTIVATE", "REJECTED"},
    "REVALIDATION_RUNNING": {"READY_TO_ACTIVATE", "OPERATOR_REVIEW_REQUIRED", "REJECTED"},
    "OPERATOR_REVIEW_REQUIRED": {"READY_TO_ACTIVATE", "REJECTED", "MONITOR_ONLY"},
    "READY_TO_ACTIVATE": {"ACTIVE", "REJECTED"},
    "ACTIVE": {"SUPERSEDED", "ROLLED_BACK"},
    "SUPERSEDED": set(),
    "ROLLED_BACK": set(),
    "REJECTED": set(),
    "MONITOR_ONLY": set(),
}


@dataclass(frozen=True)
class GateEvaluation:
    result: str
    reason_codes: list[str]
    evidence_refs: list[dict[str, Any]]
    metric_refs: list[dict[str, Any]]
    freshness_state: str
    confidence_level: str
    confidence_reason_codes: list[str]
    decision_basis: dict[str, Any]


@dataclass(frozen=True)
class GateTargetContext:
    project: VideoProject
    artifact_version: ArtifactVersion | None
    review_task: ReviewTask | None
    input_snapshot: dict[str, Any]


@dataclass(frozen=True)
class ReviewLinkResult:
    review_task: ReviewTask | None
    created: bool


class GateDefinitionService:
    def __init__(self, session: Session):
        self.session = session

    def create_definition(self, *, data: GateDefinitionVersionCreate) -> GateDefinitionVersion:
        existing = self.get_definition(data.gate_key, data.version)
        if existing is not None:
            raise ConflictError(f"gate definition exists: {data.gate_key} {data.version}")
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        definition = GateDefinitionVersion(**data.model_dump())
        self.session.add(definition)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="gate_definition_version.created",
            aggregate_type="gate_definition_version",
            aggregate_id=definition.id,
            actor_id=data.created_by_user_id,
            target_type="gate_definition_version",
            target_id=definition.id,
            company_id=None,
            correlation_id="m3-gate-definition-created",
            payload={"gate_key": definition.gate_key, "version": definition.version},
        )
        return definition

    def seed_definitions(self) -> list[GateDefinitionVersion]:
        definitions = self._load_catalog_definitions()
        records: list[GateDefinitionVersion] = []
        for data in definitions:
            existing = self.get_definition(data.gate_key, data.version)
            if existing is None:
                record = GateDefinitionVersion(**data.model_dump())
                self.session.add(record)
                self.session.flush()
                _record_m3_event(
                    self.session,
                    event_type="gate_definition_version.created",
                    aggregate_type="gate_definition_version",
                    aggregate_id=record.id,
                    actor_id=data.created_by_user_id,
                    target_type="gate_definition_version",
                    target_id=record.id,
                    company_id=None,
                    correlation_id="m3-gate-definition-seed",
                    payload={"gate_key": record.gate_key, "version": record.version},
                )
                if record.status == "active" and record.activated_at is None:
                    record.activated_at = utc_now()
            else:
                record = existing
            records.append(record)
        self.session.flush()
        return records

    def activate_definition(self, gate_definition_version_id: uuid.UUID) -> GateDefinitionVersion:
        definition = self._require_definition_id(gate_definition_version_id)
        if definition.status == "active":
            return definition
        active = self.get_active_gate_version(definition.gate_key, required=False)
        if active is not None and active.id != definition.id:
            active.status = "superseded"
            active.superseded_at = utc_now()
        definition.status = "active"
        definition.activated_at = utc_now()
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="gate_definition_version.activated",
            aggregate_type="gate_definition_version",
            aggregate_id=definition.id,
            actor_id=definition.created_by_user_id,
            target_type="gate_definition_version",
            target_id=definition.id,
            company_id=None,
            correlation_id="m3-gate-definition-activated",
            payload={"gate_key": definition.gate_key, "version": definition.version},
        )
        return definition

    def deprecate_definition(self, gate_definition_version_id: uuid.UUID) -> GateDefinitionVersion:
        definition = self._require_definition_id(gate_definition_version_id)
        if definition.status == "active":
            raise ValidationFailureError("active gate definition must be superseded by activating a new version")
        definition.status = "deprecated"
        self.session.flush()
        return definition

    def get_definition(self, gate_key: str, version: str) -> GateDefinitionVersion | None:
        return self.session.scalars(
            select(GateDefinitionVersion).where(
                GateDefinitionVersion.gate_key == gate_key,
                GateDefinitionVersion.version == version,
            )
        ).one_or_none()

    def get_active_gate_version(self, gate_key: str, *, required: bool = True) -> GateDefinitionVersion | None:
        record = self.session.scalars(
            select(GateDefinitionVersion).where(
                GateDefinitionVersion.gate_key == gate_key,
                GateDefinitionVersion.status == "active",
            )
        ).one_or_none()
        if record is None and required:
            raise NotFoundError(f"active gate definition not found: {gate_key}")
        return record

    def _require_definition_id(self, gate_definition_version_id: uuid.UUID) -> GateDefinitionVersion:
        definition = self.session.get(GateDefinitionVersion, gate_definition_version_id)
        if definition is None:
            raise NotFoundError(f"gate definition not found: {gate_definition_version_id}")
        return definition

    def _load_catalog_definitions(self) -> list[GateDefinitionVersionCreate]:
        try:
            loaded = ConfigRegistryService(self.session).validate_catalog(GATE_DEFINITION_CATALOG)
        except FileNotFoundError:
            return _builtin_definition_contracts()
        return [GateDefinitionVersionCreate.model_validate(item) for item in loaded.content["items"]]


class GateRunnerService:
    def __init__(self, session: Session):
        self.session = session

    def run_gate(self, *, data: GateRunCreate, correlation_id: str = "m3-gate-run") -> GateRun:
        definition = self._resolve_definition(data)
        context = self._build_target_context(data.target_type, data.target_id)
        input_hash = content_hash(context.input_snapshot)
        evaluation = _evaluate_gate(definition.gate_key, context.input_snapshot, self.session)
        gate_run_id = uuid.uuid4()
        review_link = GateReviewIntegrationService(self.session).create_or_link_review_task(
            gate_run_id=gate_run_id,
            definition=definition,
            context=context,
            target_type=data.target_type,
            target_id=data.target_id,
            result=evaluation.result,
            reason_codes=evaluation.reason_codes,
            evidence_refs=evaluation.evidence_refs,
            actor_user_id=data.created_by_user_id,
        )
        gate_run = GateRun(
            id=gate_run_id,
            gate_definition_version_id=definition.id,
            gate_key=definition.gate_key,
            target_type=data.target_type,
            target_id=data.target_id,
            video_project_id=context.project.id,
            artifact_version_id=context.artifact_version.id if context.artifact_version else None,
            review_task_id=context.review_task.id if context.review_task else None,
            policy_snapshot_id=context.project.policy_snapshot_id,
            input_snapshot=context.input_snapshot,
            input_snapshot_hash=input_hash,
            result=evaluation.result,
            reason_codes=evaluation.reason_codes,
            evidence_refs=evaluation.evidence_refs,
            metric_refs=evaluation.metric_refs,
            freshness_state=evaluation.freshness_state,
            confidence_level=evaluation.confidence_level,
            confidence_reason_codes=evaluation.confidence_reason_codes,
            decision_basis=evaluation.decision_basis,
            created_review_task_id=review_link.review_task.id if review_link.review_task else None,
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(gate_run)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="gate_run.created",
            aggregate_type="gate_run",
            aggregate_id=gate_run.id,
            actor_id=data.created_by_user_id,
            target_type="gate_run",
            target_id=gate_run.id,
            company_id=context.project.company_id,
            correlation_id=correlation_id,
            payload={
                "gate_key": gate_run.gate_key,
                "gate_definition_version_id": str(gate_run.gate_definition_version_id),
                "target_type": gate_run.target_type,
                "target_id": str(gate_run.target_id),
                "result": gate_run.result,
                "reason_codes": gate_run.reason_codes,
                "input_snapshot_hash": gate_run.input_snapshot_hash,
            },
        )
        if review_link.review_task is not None and review_link.created:
            _record_m3_event(
                self.session,
                event_type="gate_run.review_task_created",
                aggregate_type="gate_run",
                aggregate_id=gate_run.id,
                actor_id=data.created_by_user_id,
                target_type="review_task",
                target_id=review_link.review_task.id,
                company_id=context.project.company_id,
                correlation_id=correlation_id,
                payload={
                    "gate_run_id": str(gate_run.id),
                    "review_task_id": str(review_link.review_task.id),
                    "gate_key": gate_run.gate_key,
                },
            )
        return gate_run

    def get_gate_run(self, gate_run_id: uuid.UUID) -> GateRun | None:
        return self.session.get(GateRun, gate_run_id)

    def list_project_gate_runs(self, project_id: uuid.UUID) -> list[GateRun]:
        return list(
            self.session.scalars(
                select(GateRun).where(GateRun.video_project_id == project_id).order_by(GateRun.created_at.asc())
            ).all()
        )

    def _resolve_definition(self, data: GateRunCreate) -> GateDefinitionVersion:
        if data.gate_definition_version_id is not None:
            definition = self.session.get(GateDefinitionVersion, data.gate_definition_version_id)
            if definition is None:
                raise NotFoundError(f"gate definition not found: {data.gate_definition_version_id}")
            if definition.gate_key != data.gate_key:
                raise ValidationFailureError("gate key does not match gate definition version")
            return definition
        return GateDefinitionService(self.session).get_active_gate_version(data.gate_key)

    def _build_target_context(self, target_type: str, target_id: uuid.UUID) -> GateTargetContext:
        if target_type == "video_project":
            project = self.session.get(VideoProject, target_id)
            if project is None:
                raise NotFoundError(f"project not found: {target_id}")
            return GateTargetContext(project=project, artifact_version=None, review_task=None, input_snapshot=_snapshot_for_project(project))
        if target_type == "artifact_version":
            version = self.session.get(ArtifactVersion, target_id)
            if version is None:
                raise NotFoundError(f"artifact version not found: {target_id}")
            artifact = self.session.get(Artifact, version.artifact_id)
            if artifact is None:
                raise NotFoundError(f"artifact not found: {version.artifact_id}")
            project = self.session.get(VideoProject, artifact.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {artifact.video_project_id}")
            return GateTargetContext(
                project=project,
                artifact_version=version,
                review_task=None,
                input_snapshot=_snapshot_for_artifact_version(project, artifact, version),
            )
        if target_type == "review_task":
            review = self.session.get(ReviewTask, target_id)
            if review is None:
                raise NotFoundError(f"review task not found: {target_id}")
            project = self.session.get(VideoProject, review.video_project_id)
            if project is None:
                raise NotFoundError(f"project not found: {review.video_project_id}")
            version = self.session.get(ArtifactVersion, review.target_artifact_version_id) if review.target_artifact_version_id else None
            return GateTargetContext(
                project=project,
                artifact_version=version,
                review_task=review,
                input_snapshot=_snapshot_for_review_task(project, review, version),
            )
        raise ValidationFailureError(f"unsupported gate target_type: {target_type}")


class GateReviewIntegrationService:
    def __init__(self, session: Session):
        self.session = session

    def create_or_link_review_task(
        self,
        *,
        gate_run_id: uuid.UUID,
        definition: GateDefinitionVersion,
        context: GateTargetContext,
        target_type: str,
        target_id: uuid.UUID,
        result: str,
        reason_codes: list[str],
        evidence_refs: list[dict[str, Any]],
        actor_user_id: uuid.UUID | None,
    ) -> ReviewLinkResult:
        gate_config = definition.definition or {}
        if result != "REVIEW_REQUIRED" or not gate_config.get("review_required", True):
            return ReviewLinkResult(review_task=None, created=False)
        review_type = gate_config.get("review_type") or REVIEW_TYPE_BY_GATE.get(definition.gate_key, "policy_review")
        review_target_type, review_target_id, target_version_id = _review_target(target_type, target_id, context)
        existing = self.session.scalars(
            select(ReviewTask).where(
                ReviewTask.video_project_id == context.project.id,
                ReviewTask.target_type == review_target_type,
                ReviewTask.target_id == review_target_id,
                ReviewTask.review_type == review_type,
                ReviewTask.status.in_(["open", "in_progress"]),
            )
        ).first()
        if existing is not None:
            return ReviewLinkResult(review_task=existing, created=False)
        requested_by = actor_user_id or context.project.created_by_user_id
        _require_user(self.session, requested_by, "requested_by_user_id")
        task = ReviewTask(
            video_project_id=context.project.id,
            target_type=review_target_type,
            target_id=review_target_id,
            target_artifact_version_id=target_version_id,
            review_type=review_type,
            status="open",
            requested_by_user_id=requested_by,
            review_reason_codes=reason_codes,
            evidence_required=True,
            evidence_refs=evidence_refs,
            review_scope=f"gate:{definition.gate_key}:{gate_run_id}",
        )
        self.session.add(task)
        self.session.flush()
        return ReviewLinkResult(review_task=task, created=True)


class WorkflowReadinessService:
    def __init__(self, session: Session):
        self.session = session

    def inspect_project(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None:
            raise NotFoundError(f"project not found: {project_id}")
        runs = list(
            self.session.scalars(
                select(GateRun).where(GateRun.video_project_id == project_id).order_by(GateRun.created_at.desc())
            ).all()
        )
        latest: dict[tuple[str, str, uuid.UUID], GateRun] = {}
        for run in runs:
            key = (run.gate_key, run.target_type, run.target_id)
            if key not in latest:
                latest[key] = run
        latest_runs = list(latest.values())
        blockers = [_readiness_item(run) for run in latest_runs if run.result == "BLOCK"]
        review_required = [_readiness_item(run) for run in latest_runs if run.result == "REVIEW_REQUIRED"]
        return {
            "project_id": str(project.id),
            "policy_snapshot_id": str(project.policy_snapshot_id),
            "status": "BLOCKED" if blockers else "REVIEW_REQUIRED" if review_required else "READY" if latest_runs else "UNKNOWN",
            "counts": {
                "PASS": sum(1 for run in latest_runs if run.result == "PASS"),
                "REVIEW_REQUIRED": len(review_required),
                "BLOCK": len(blockers),
                "SKIPPED": sum(1 for run in latest_runs if run.result == "SKIPPED"),
                "NOT_APPLICABLE": sum(1 for run in latest_runs if run.result == "NOT_APPLICABLE"),
            },
            "blockers": blockers,
            "review_required": review_required,
            "next_actions": [
                {"type": "resolve_blocker", "gate_run_id": item["gate_run_id"], "reason_codes": item["reason_codes"]}
                for item in blockers
            ] + [
                {"type": "complete_review", "gate_run_id": item["gate_run_id"], "review_task_id": item["review_task_id"]}
                for item in review_required
            ],
        }


class PolicyCatalogService:
    def __init__(self, session: Session):
        self.session = session

    def create_catalog(self, *, data: PlatformPolicyCatalogCreate) -> PlatformPolicyCatalog:
        existing = self.session.scalars(
            select(PlatformPolicyCatalog).where(PlatformPolicyCatalog.catalog_key == data.catalog_key)
        ).one_or_none()
        if existing is not None:
            raise ConflictError(f"policy catalog exists: {data.catalog_key}")
        catalog = PlatformPolicyCatalog(**data.model_dump())
        self.session.add(catalog)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="platform_policy_catalog.created",
            aggregate_type="platform_policy_catalog",
            aggregate_id=catalog.id,
            actor_id=None,
            target_type="platform_policy_catalog",
            target_id=catalog.id,
            company_id=None,
            correlation_id="m3-policy-catalog-created",
            payload={"catalog_key": catalog.catalog_key, "platform": catalog.platform, "policy_domain": catalog.policy_domain},
        )
        return catalog

    def create_version(self, *, data: PlatformPolicyVersionCreate) -> PlatformPolicyVersion:
        catalog = self.session.get(PlatformPolicyCatalog, data.catalog_id)
        if catalog is None:
            raise NotFoundError(f"policy catalog not found: {data.catalog_id}")
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        existing = self.session.scalars(
            select(PlatformPolicyVersion).where(
                PlatformPolicyVersion.catalog_id == data.catalog_id,
                PlatformPolicyVersion.version == data.version,
            )
        ).one_or_none()
        if existing is not None:
            raise ConflictError(f"policy version exists: {catalog.catalog_key} {data.version}")
        version = PlatformPolicyVersion(**data.model_dump())
        self.session.add(version)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="platform_policy_version.created",
            aggregate_type="platform_policy_version",
            aggregate_id=version.id,
            actor_id=data.created_by_user_id,
            target_type="platform_policy_version",
            target_id=version.id,
            company_id=None,
            correlation_id="m3-policy-version-created",
            payload={"catalog_id": str(catalog.id), "version": version.version},
        )
        return version

    def activate_version(self, policy_version_id: uuid.UUID) -> PlatformPolicyVersion:
        version = self.session.get(PlatformPolicyVersion, policy_version_id)
        if version is None:
            raise NotFoundError(f"policy version not found: {policy_version_id}")
        catalog = self.session.get(PlatformPolicyCatalog, version.catalog_id)
        if catalog is None:
            raise NotFoundError(f"policy catalog not found: {version.catalog_id}")
        if version.status == "active" and catalog.current_version_id == version.id:
            return version
        if catalog.current_version_id and catalog.current_version_id != version.id:
            old = self.session.get(PlatformPolicyVersion, catalog.current_version_id)
            if old is not None and old.status == "active":
                old.status = "superseded"
                old.superseded_at = utc_now()
        version.status = "active"
        version.activated_at = utc_now()
        catalog.current_version_id = version.id
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="platform_policy_version.activated",
            aggregate_type="platform_policy_version",
            aggregate_id=version.id,
            actor_id=version.created_by_user_id,
            target_type="platform_policy_version",
            target_id=version.id,
            company_id=None,
            correlation_id="m3-policy-version-activated",
            payload={"catalog_id": str(catalog.id), "version": version.version},
        )
        return version

    def attach_source_ref(self, *, data: PolicySourceRefCreate) -> PolicySourceRef:
        return _create_policy_source_ref(self.session, data=data)

    def get_active_policy_version(self, *, catalog_key: str | None = None, platform: str | None = None, policy_domain: str | None = None) -> PlatformPolicyVersion | None:
        statement: Select[tuple[PlatformPolicyCatalog]] = select(PlatformPolicyCatalog)
        if catalog_key is not None:
            statement = statement.where(PlatformPolicyCatalog.catalog_key == catalog_key)
        if platform is not None:
            statement = statement.where(PlatformPolicyCatalog.platform == platform)
        if policy_domain is not None:
            statement = statement.where(PlatformPolicyCatalog.policy_domain == policy_domain)
        catalog = self.session.scalars(statement).first()
        if catalog is None or catalog.current_version_id is None:
            return None
        return self.session.get(PlatformPolicyVersion, catalog.current_version_id)


class PolicyChangeService:
    def __init__(self, session: Session):
        self.session = session

    def create_change_record(self, *, data: PolicyChangeRecordCreate) -> PolicyChangeRecord:
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        existing = self.session.scalars(
            select(PolicyChangeRecord).where(PolicyChangeRecord.change_key == data.change_key)
        ).one_or_none()
        if existing is not None:
            raise ConflictError(f"policy change exists: {data.change_key}")
        record = PolicyChangeRecord(**data.model_dump())
        self.session.add(record)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="policy_change_record.created",
            aggregate_type="policy_change_record",
            aggregate_id=record.id,
            actor_id=data.created_by_user_id,
            target_type="policy_change_record",
            target_id=record.id,
            company_id=None,
            correlation_id="m3-policy-change-created",
            payload={"change_key": record.change_key, "state": record.state},
        )
        return record

    def add_source_ref(self, *, data: PolicySourceRefCreate) -> PolicySourceRef:
        return _create_policy_source_ref(self.session, data=data)

    def record_diff_summary(self, policy_change_record_id: uuid.UUID, diff_summary: dict[str, Any]) -> PolicyChangeRecord:
        record = self._require_record(policy_change_record_id)
        record.diff_summary = diff_summary
        self.session.flush()
        return record

    def record_impact_classification(
        self,
        policy_change_record_id: uuid.UUID,
        impact_classification: dict[str, Any],
        *,
        affected_gate_keys: list[str] | None = None,
        affected_domains: list[str] | None = None,
        requires_revalidation: bool | None = None,
    ) -> PolicyChangeRecord:
        record = self._require_record(policy_change_record_id)
        record.impact_classification = impact_classification
        if affected_gate_keys is not None:
            record.affected_gate_keys = affected_gate_keys
        if affected_domains is not None:
            record.affected_domains = affected_domains
        if requires_revalidation is not None:
            record.requires_revalidation = requires_revalidation
        self.session.flush()
        return record

    def transition_state(self, policy_change_record_id: uuid.UUID, new_state: str) -> PolicyChangeRecord:
        record = self._require_record(policy_change_record_id)
        if new_state == record.state:
            return record
        allowed = VALID_POLICY_TRANSITIONS.get(record.state, set())
        if new_state not in allowed:
            raise ValidationFailureError(f"invalid policy change transition: {record.state} -> {new_state}")
        old_state = record.state
        record.state = new_state
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="policy_change_record.state_changed",
            aggregate_type="policy_change_record",
            aggregate_id=record.id,
            actor_id=record.created_by_user_id,
            target_type="policy_change_record",
            target_id=record.id,
            company_id=None,
            correlation_id="m3-policy-change-state",
            payload={"old_state": old_state, "new_state": new_state},
        )
        return record

    def _require_record(self, policy_change_record_id: uuid.UUID) -> PolicyChangeRecord:
        record = self.session.get(PolicyChangeRecord, policy_change_record_id)
        if record is None:
            raise NotFoundError(f"policy change record not found: {policy_change_record_id}")
        return record


class PolicyRevalidationService:
    def __init__(self, session: Session):
        self.session = session

    def create_batch(self, *, data: PolicyRevalidationBatchCreate) -> PolicyRevalidationBatch:
        if data.created_by_user_id is not None:
            _require_user(self.session, data.created_by_user_id, "created_by_user_id")
        batch = PolicyRevalidationBatch(
            policy_change_record_id=data.policy_change_record_id,
            gate_definition_version_id=data.gate_definition_version_id,
            scope=data.scope,
            status="PENDING",
            counts={},
            created_by_user_id=data.created_by_user_id,
        )
        self.session.add(batch)
        self.session.flush()
        _record_m3_event(
            self.session,
            event_type="policy_revalidation_batch.created",
            aggregate_type="policy_revalidation_batch",
            aggregate_id=batch.id,
            actor_id=data.created_by_user_id,
            target_type="policy_revalidation_batch",
            target_id=batch.id,
            company_id=None,
            correlation_id="m3-policy-revalidation-created",
            payload={"scope": batch.scope},
        )
        return batch

    def run_batch(self, policy_revalidation_batch_id: uuid.UUID) -> PolicyRevalidationBatch:
        batch = self.session.get(PolicyRevalidationBatch, policy_revalidation_batch_id)
        if batch is None:
            raise NotFoundError(f"policy revalidation batch not found: {policy_revalidation_batch_id}")
        if batch.status not in {"PENDING", "FAILED"}:
            raise ValidationFailureError(f"cannot run batch with status: {batch.status}")
        targets = batch.scope.get("targets", [])
        if not isinstance(targets, list) or not targets:
            raise ValidationFailureError("revalidation scope requires targets list")
        batch.status = "RUNNING"
        batch.started_at = utc_now()
        self.session.flush()
        counts = {"total": 0, "created": 0, "PASS": 0, "REVIEW_REQUIRED": 0, "BLOCK": 0, "SKIPPED": 0, "NOT_APPLICABLE": 0, "failed": 0}
        try:
            for target in targets:
                if not isinstance(target, dict):
                    raise ValidationFailureError("each revalidation target must be an object")
                gate_keys = _target_gate_keys(target, batch, self.session)
                for gate_key in gate_keys:
                    counts["total"] += 1
                    gate_run = GateRunnerService(self.session).run_gate(
                        data=GateRunCreate(
                            gate_key=gate_key,
                            gate_definition_version_id=batch.gate_definition_version_id,
                            target_type=target["target_type"],
                            target_id=uuid.UUID(str(target["target_id"])),
                            created_by_user_id=batch.created_by_user_id,
                        ),
                        correlation_id=f"m3-revalidation-{batch.id}",
                    )
                    counts["created"] += 1
                    counts[gate_run.result] += 1
            batch.status = "COMPLETED"
            batch.completed_at = utc_now()
            batch.counts = counts
            self.session.flush()
            _record_m3_event(
                self.session,
                event_type="policy_revalidation_batch.completed",
                aggregate_type="policy_revalidation_batch",
                aggregate_id=batch.id,
                actor_id=batch.created_by_user_id,
                target_type="policy_revalidation_batch",
                target_id=batch.id,
                company_id=None,
                correlation_id="m3-policy-revalidation-completed",
                payload={"counts": counts},
            )
        except Exception:
            counts["failed"] += 1
            batch.status = "FAILED"
            batch.counts = counts
            batch.completed_at = utc_now()
            self.session.flush()
            raise
        return batch


def _builtin_definition_contracts() -> list[GateDefinitionVersionCreate]:
    return [
        GateDefinitionVersionCreate(
            gate_key=gate_key,
            gate_name=gate_key.replace("_", " ").title(),
            gate_domain=_gate_domain(gate_key),
            version="1.0.0",
            status="active",
            input_schema_version="gate-input.m3.v1",
            output_schema_version="gate-output.m3.v1",
            definition={"logic": gate_key, "review_required": gate_key != "publish_risk_gate", "review_type": REVIEW_TYPE_BY_GATE.get(gate_key)},
            reason_code_refs=_reason_refs_for_gate(gate_key),
        )
        for gate_key in BUILTIN_GATE_KEYS
    ]


def _evaluate_gate(gate_key: str, snapshot: dict[str, Any], session: Session) -> GateEvaluation:
    mapping = {
        "ai_use_disclosure_gate": _ai_use_disclosure_gate,
        "ai_provenance_gate": _ai_provenance_gate,
        "rights_copyright_gate": _rights_copyright_gate,
        "affiliate_disclosure_gate": _affiliate_disclosure_gate,
        "commercial_disclosure_gate": _commercial_disclosure_gate,
        "platform_originality_gate": _platform_originality_gate,
        "repetitive_template_risk_gate": _repetitive_template_risk_gate,
        "brand_conflict_gate": _brand_conflict_gate,
        "commercial_conflict_gate": _commercial_conflict_gate,
        "disclosure_placement_gate": _disclosure_placement_gate,
        "search_demand_gate": _search_demand_gate,
        "distribution_readiness_gate": _distribution_readiness_gate,
        "packaging_expectation_gate": _packaging_expectation_gate,
        "privacy_retention_gate": _privacy_retention_gate,
        "publish_risk_gate": lambda item: _publish_risk_gate(item, session),
    }
    handler = mapping.get(gate_key)
    if handler is None:
        return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, basis={"reason": "unknown_gate"})
    return handler(snapshot)


def _ai_use_disclosure_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if not _has_ai_flag(snapshot):
        return _gate_result("NOT_APPLICABLE", ["AI_DISCLOSURE_NOT_REQUIRED"], snapshot, freshness="NOT_REQUIRED", confidence="HIGH")
    evidence = _evidence_refs(snapshot, keywords=["ai_disclosure", "disclosure", "aigc"])
    if _has_disclosure(snapshot, "ai") or evidence:
        return _gate_result("PASS", ["AI_DISCLOSURE_REQUIRED"], snapshot, evidence=evidence, freshness="FRESH", confidence="HIGH")
    return _gate_result(
        "REVIEW_REQUIRED",
        ["AI_DISCLOSURE_REQUIRED", "AI_DISCLOSURE_MISSING"],
        snapshot,
        confidence="MEDIUM",
        confidence_reasons=["EVIDENCE_REQUIRED"],
    )


def _ai_provenance_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["content_credentials", "c2pa_present", "c2pa_metadata_present"]):
        return _gate_result("PASS", ["CONTENT_CREDENTIALS_DETECTED", "C2PA_METADATA_PRESENT"], snapshot, freshness="FRESH", confidence="HIGH")
    if _has_ai_flag(snapshot):
        return _gate_result("REVIEW_REQUIRED", ["PROVENANCE_MISSING", "C2PA_METADATA_ABSENT"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])
    return _gate_result("NOT_APPLICABLE", ["AI_DISCLOSURE_NOT_REQUIRED"], snapshot, freshness="NOT_REQUIRED", confidence="HIGH")


def _rights_copyright_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    evidence = _evidence_refs(snapshot, keywords=["rights", "license", "permission", "source"])
    if _text_contains(snapshot, ["rights_basis", "license_id", "permission_ref", "public_domain"]) or evidence:
        return _gate_result("PASS", ["EVIDENCE_REQUIRED"], snapshot, evidence=evidence, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["RIGHTS_BASIS_MISSING"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _affiliate_disclosure_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if not _text_contains(snapshot, ["affiliate", "affiliate_link", "commission"]):
        return _gate_result("NOT_APPLICABLE", ["GATE_INPUT_INSUFFICIENT"], snapshot, freshness="NOT_REQUIRED", confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])
    if _has_disclosure(snapshot, "affiliate"):
        return _gate_result("PASS", ["AFFILIATE_DISCLOSURE_REQUIRED"], snapshot, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["AFFILIATE_DISCLOSURE_REQUIRED", "AFFILIATE_DISCLOSURE_MISSING"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _commercial_disclosure_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if not _text_contains(snapshot, ["sponsor", "paid_promotion", "commercial", "brand_deal"]):
        return _gate_result("NOT_APPLICABLE", ["GATE_INPUT_INSUFFICIENT"], snapshot, freshness="NOT_REQUIRED", confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])
    if _has_disclosure(snapshot, "commercial") or _has_disclosure(snapshot, "paid"):
        return _gate_result("PASS", ["COMMERCIAL_DISCLOSURE_REQUIRED"], snapshot, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["COMMERCIAL_DISCLOSURE_REQUIRED", "COMMERCIAL_DISCLOSURE_MISSING"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _platform_originality_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["reused_content", "transformation_insufficient"]):
        return _gate_result("REVIEW_REQUIRED", ["REUSED_CONTENT_TRANSFORMATION_INSUFFICIENT"], snapshot, confidence="MEDIUM")
    if _text_contains(snapshot, ["human_value_weak", "low_originality"]):
        return _gate_result("REVIEW_REQUIRED", ["HUMAN_VALUE_CONTRIBUTION_WEAK"], snapshot, confidence="MEDIUM")
    return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _repetitive_template_risk_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["repetitive_template", "template_saturation", "stock_visual_overuse"]):
        return _gate_result("REVIEW_REQUIRED", ["REPETITIVE_TEMPLATE_RISK", "TEMPLATE_SATURATION_RISK"], snapshot, confidence="MEDIUM")
    return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _brand_conflict_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["restricted_entity", "entity_restricted", "brand_conflict"]):
        return _gate_result("REVIEW_REQUIRED", ["ENTITY_RESTRICTED"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])
    return _gate_result("NOT_APPLICABLE", ["GATE_INPUT_INSUFFICIENT"], snapshot, freshness="NOT_REQUIRED", confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _commercial_conflict_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["affiliate_conflict", "sponsor_conflict", "commercial_conflict"]):
        return _gate_result("REVIEW_REQUIRED", ["SPONSOR_CONFLICT_WITH_CONTENT"], snapshot, confidence="MEDIUM")
    return _gate_result("NOT_APPLICABLE", ["GATE_INPUT_INSUFFICIENT"], snapshot, freshness="NOT_REQUIRED", confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _disclosure_placement_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if _text_contains(snapshot, ["disclosure_required"]) and not _text_contains(snapshot, ["clear_and_conspicuous", "placement_ok", "above_fold"]):
        return _gate_result("REVIEW_REQUIRED", ["DISCLOSURE_PLACEMENT_INSUFFICIENT", "DISCLOSURE_NOT_CLEAR_AND_CONSPICUOUS"], snapshot, confidence="MEDIUM")
    return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _search_demand_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if not _text_contains(snapshot, ["search_led", "keyword", "search_demand"]):
        return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])
    metrics = _metric_refs(snapshot, keywords=["search_volume", "demand", "keyword"])
    if metrics or _text_contains(snapshot, ["search_volume", "demand_evidence"]):
        return _gate_result("PASS", ["EVIDENCE_REQUIRED"], snapshot, metrics=metrics, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["SEARCH_DEMAND_EVIDENCE_MISSING", "SEARCH_VOLUME_UNKNOWN"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _distribution_readiness_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    packaging = snapshot.get("artifact_version", {}).get("packaging_metadata", {})
    audience = snapshot.get("project", {}).get("audience_delivery_summary", {})
    if _text_contains(packaging, ["metadata_ready", "distribution_ready"]) or (
        isinstance(packaging, dict) and bool(packaging.get("title")) and bool(packaging.get("description"))
    ) or _text_contains(audience, ["metadata_ready", "distribution_ready"]):
        return _gate_result("PASS", ["EVIDENCE_REQUIRED"], snapshot, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["DISTRIBUTION_READINESS_MISSING", "METADATA_READINESS_MISSING"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _packaging_expectation_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    packaging = snapshot.get("artifact_version", {}).get("packaging_metadata", {})
    if _text_contains(snapshot, ["promise_mismatch", "packaging_mismatch"]):
        return _gate_result("REVIEW_REQUIRED", ["PACKAGING_PROMISE_MISMATCH"], snapshot, confidence="MEDIUM")
    if isinstance(packaging, dict) and (packaging.get("title") or packaging.get("hook") or packaging.get("description")):
        return _gate_result("PASS", ["EVIDENCE_REQUIRED"], snapshot, freshness="FRESH", confidence="HIGH")
    return _gate_result("SKIPPED", ["GATE_INPUT_INSUFFICIENT"], snapshot, confidence="LOW", confidence_reasons=["GATE_INPUT_INSUFFICIENT"])


def _privacy_retention_gate(snapshot: dict[str, Any]) -> GateEvaluation:
    if not _text_contains(snapshot, ["raw_comment", "pii", "community_data", "memory_promotion"]):
        return _gate_result("NOT_APPLICABLE", ["PRIVACY_PII_REMOVED"], snapshot, freshness="NOT_REQUIRED", confidence="HIGH")
    if _text_contains(snapshot, ["pii_removed", "scrubbed", "retention_policy", "raw_comment_storage_blocked"]):
        return _gate_result("PASS", ["PRIVACY_PII_REMOVED"], snapshot, freshness="FRESH", confidence="HIGH")
    return _gate_result("REVIEW_REQUIRED", ["RAW_COMMENT_STORAGE_BLOCKED", "CONTEXT_SCOPE_MISSING"], snapshot, confidence="MEDIUM", confidence_reasons=["EVIDENCE_REQUIRED"])


def _publish_risk_gate(snapshot: dict[str, Any], session: Session) -> GateEvaluation:
    target = snapshot["target"]
    runs = list(
        session.scalars(
            select(GateRun).where(
                GateRun.target_type == target["target_type"],
                GateRun.target_id == uuid.UUID(target["target_id"]),
                GateRun.gate_key != "publish_risk_gate",
            )
        ).all()
    )
    if any(run.result == "BLOCK" for run in runs):
        return _gate_result("BLOCK", ["MANUAL_REVIEW_REQUIRED"], snapshot, confidence="HIGH", basis={"source_gate_run_count": len(runs)})
    if any(run.result == "REVIEW_REQUIRED" for run in runs):
        return _gate_result("REVIEW_REQUIRED", ["MANUAL_REVIEW_REQUIRED"], snapshot, confidence="HIGH", basis={"source_gate_run_count": len(runs)})
    return _gate_result("PASS", ["SYSTEM_OK"], snapshot, freshness="FRESH", confidence="HIGH", basis={"source_gate_run_count": len(runs)})


def _gate_result(
    result: str,
    reason_codes: list[str],
    snapshot: dict[str, Any],
    *,
    evidence: list[dict[str, Any]] | None = None,
    metrics: list[dict[str, Any]] | None = None,
    freshness: str = "UNKNOWN",
    confidence: str = "UNKNOWN",
    confidence_reasons: list[str] | None = None,
    basis: dict[str, Any] | None = None,
) -> GateEvaluation:
    return GateEvaluation(
        result=result,
        reason_codes=reason_codes,
        evidence_refs=evidence if evidence is not None else _evidence_refs(snapshot),
        metric_refs=metrics if metrics is not None else _metric_refs(snapshot),
        freshness_state=freshness,
        confidence_level=confidence,
        confidence_reason_codes=confidence_reasons or [],
        decision_basis={"deterministic": True, "llm_used": False, "basis": basis or {}},
    )


def _snapshot_for_project(project: VideoProject) -> dict[str, Any]:
    return {
        "target": {"target_type": "video_project", "target_id": str(project.id)},
        "project": _project_payload(project),
    }


def _snapshot_for_artifact_version(project: VideoProject, artifact: Artifact, version: ArtifactVersion) -> dict[str, Any]:
    return {
        "target": {"target_type": "artifact_version", "target_id": str(version.id)},
        "project": _project_payload(project),
        "artifact": {"id": str(artifact.id), "artifact_type": artifact.artifact_type, "current_version_id": str(artifact.current_version_id) if artifact.current_version_id else None},
        "artifact_version": {
            "id": str(version.id),
            "artifact_id": str(version.artifact_id),
            "version_number": version.version_number,
            "content_hash": version.content_hash,
            "status": version.status,
            "content": version.content,
            "external_entity_refs": version.external_entity_refs,
            "packaging_metadata": version.packaging_metadata,
            "media_qc_metadata": version.media_qc_metadata,
            "source_manifest": version.source_manifest,
            "evidence_refs": version.evidence_refs,
            "context_refs": version.context_refs,
            "claim_refs": version.claim_refs,
            "retrieval_plan_ref": version.retrieval_plan_ref,
        },
    }


def _snapshot_for_review_task(project: VideoProject, review: ReviewTask, version: ArtifactVersion | None) -> dict[str, Any]:
    snapshot = _snapshot_for_project(project)
    snapshot["target"] = {"target_type": "review_task", "target_id": str(review.id)}
    snapshot["review_task"] = {
        "id": str(review.id),
        "target_type": review.target_type,
        "target_id": str(review.target_id),
        "target_artifact_version_id": str(review.target_artifact_version_id) if review.target_artifact_version_id else None,
        "review_type": review.review_type,
        "status": review.status,
        "review_reason_codes": review.review_reason_codes,
        "evidence_required": review.evidence_required,
        "evidence_refs": review.evidence_refs,
        "review_scope": review.review_scope,
    }
    if version is not None:
        snapshot["artifact_version"] = {
            "id": str(version.id),
            "artifact_id": str(version.artifact_id),
            "version_number": version.version_number,
            "content_hash": version.content_hash,
            "content": version.content,
            "external_entity_refs": version.external_entity_refs,
            "packaging_metadata": version.packaging_metadata,
            "media_qc_metadata": version.media_qc_metadata,
            "source_manifest": version.source_manifest,
            "evidence_refs": version.evidence_refs,
            "context_refs": version.context_refs,
            "claim_refs": version.claim_refs,
        }
    return snapshot


def _project_payload(project: VideoProject) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "company_id": str(project.company_id),
        "channel_workspace_id": str(project.channel_workspace_id),
        "policy_snapshot_id": str(project.policy_snapshot_id),
        "title": project.title,
        "status": project.status,
        "project_type": project.project_type,
        "financial_summary": project.financial_summary,
        "brand_safety_summary": project.brand_safety_summary,
        "legal_compliance_summary": project.legal_compliance_summary,
        "audience_delivery_summary": project.audience_delivery_summary,
    }


def _review_target(target_type: str, target_id: uuid.UUID, context: GateTargetContext) -> tuple[str, uuid.UUID, uuid.UUID | None]:
    if target_type == "artifact_version":
        return "artifact_version", target_id, target_id
    if target_type == "review_task":
        return "review_task", target_id, context.review_task.target_artifact_version_id if context.review_task else None
    return "video_project", context.project.id, None


def _readiness_item(run: GateRun) -> dict[str, Any]:
    return {
        "gate_run_id": str(run.id),
        "gate_key": run.gate_key,
        "target_type": run.target_type,
        "target_id": str(run.target_id),
        "result": run.result,
        "reason_codes": run.reason_codes,
        "review_task_id": str(run.created_review_task_id) if run.created_review_task_id else None,
        "created_at": run.created_at.isoformat(),
    }


def _target_gate_keys(target: dict[str, Any], batch: PolicyRevalidationBatch, session: Session) -> list[str]:
    if "gate_key" in target:
        return [str(target["gate_key"])]
    if "gate_keys" in target:
        return [str(item) for item in target["gate_keys"]]
    if batch.scope.get("gate_keys"):
        return [str(item) for item in batch.scope["gate_keys"]]
    if batch.gate_definition_version_id is not None:
        definition = session.get(GateDefinitionVersion, batch.gate_definition_version_id)
        if definition is None:
            raise NotFoundError(f"gate definition not found: {batch.gate_definition_version_id}")
        return [definition.gate_key]
    raise ValidationFailureError("revalidation target requires gate_key, gate_keys, or batch gate_definition_version_id")


def _create_policy_source_ref(session: Session, *, data: PolicySourceRefCreate) -> PolicySourceRef:
    if data.policy_version_id is None and data.policy_change_record_id is None:
        raise ValidationFailureError("policy source ref requires policy_version_id or policy_change_record_id")
    if data.policy_version_id is not None and session.get(PlatformPolicyVersion, data.policy_version_id) is None:
        raise NotFoundError(f"policy version not found: {data.policy_version_id}")
    if data.policy_change_record_id is not None and session.get(PolicyChangeRecord, data.policy_change_record_id) is None:
        raise NotFoundError(f"policy change record not found: {data.policy_change_record_id}")
    source_ref = PolicySourceRef(**data.model_dump())
    session.add(source_ref)
    session.flush()
    _record_m3_event(
        session,
        event_type="policy_source_ref.created",
        aggregate_type="policy_source_ref",
        aggregate_id=source_ref.id,
        actor_id=None,
        target_type="policy_source_ref",
        target_id=source_ref.id,
        company_id=None,
        correlation_id="m3-policy-source-ref-created",
        payload={"source_type": source_ref.source_type, "reliability": source_ref.reliability},
    )
    return source_ref


def _has_ai_flag(value: Any) -> bool:
    return _text_contains(value, ["ai_used", "synthetic_voice", "voice_clone", "realistic_ai", "aigc", "generated_by_ai", "synthetic_media"])


def _has_disclosure(value: Any, keyword: str) -> bool:
    return _text_contains(value, [f"{keyword}_disclosure", f"{keyword}_disclosed", "disclosure_present", "disclosure_evidence"])


def _text_contains(value: Any, needles: list[str]) -> bool:
    normalized_needles = [needle.lower() for needle in needles]
    for key, item in _walk_items(value):
        key_text = str(key).lower()
        value_text = str(item).lower() if isinstance(item, (str, bool, int, float)) else ""
        if any(needle in key_text or needle in value_text for needle in normalized_needles):
            if item is False or item == "false":
                continue
            return True
    return False


def _evidence_refs(snapshot: dict[str, Any], keywords: list[str] | None = None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key, item in _walk_items(snapshot):
        if key == "evidence_refs" and isinstance(item, list):
            refs.extend(ref for ref in item if isinstance(ref, dict))
    if keywords:
        lowered = [keyword.lower() for keyword in keywords]
        return [ref for ref in refs if any(keyword in str(ref).lower() for keyword in lowered)]
    return refs


def _metric_refs(snapshot: dict[str, Any], keywords: list[str] | None = None) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for key, item in _walk_items(snapshot):
        if key in {"metric_refs", "metrics"} and isinstance(item, list):
            refs.extend(ref for ref in item if isinstance(ref, dict))
    if keywords:
        lowered = [keyword.lower() for keyword in keywords]
        return [ref for ref in refs if any(keyword in str(ref).lower() for keyword in lowered)]
    return refs


def _walk_items(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        items: list[tuple[str, Any]] = []
        for key, child in value.items():
            items.append((str(key), child))
            items.extend(_walk_items(child))
        return items
    if isinstance(value, list):
        items = []
        for child in value:
            items.extend(_walk_items(child))
        return items
    return []


def _reason_refs_for_gate(gate_key: str) -> list[str]:
    refs = {
        "ai_use_disclosure_gate": ["AI_DISCLOSURE_REQUIRED", "AI_DISCLOSURE_MISSING", "AI_DISCLOSURE_NOT_REQUIRED"],
        "ai_provenance_gate": ["PROVENANCE_MISSING", "CONTENT_CREDENTIALS_DETECTED", "C2PA_METADATA_ABSENT"],
        "rights_copyright_gate": ["RIGHTS_BASIS_MISSING", "RIGHTS_EVIDENCE_WEAK"],
        "affiliate_disclosure_gate": ["AFFILIATE_DISCLOSURE_REQUIRED", "AFFILIATE_DISCLOSURE_MISSING"],
        "commercial_disclosure_gate": ["COMMERCIAL_DISCLOSURE_REQUIRED", "COMMERCIAL_DISCLOSURE_MISSING"],
        "platform_originality_gate": ["REUSED_CONTENT_TRANSFORMATION_INSUFFICIENT", "HUMAN_VALUE_CONTRIBUTION_WEAK"],
        "repetitive_template_risk_gate": ["REPETITIVE_TEMPLATE_RISK", "TEMPLATE_SATURATION_RISK"],
        "brand_conflict_gate": ["ENTITY_RESTRICTED"],
        "commercial_conflict_gate": ["AFFILIATE_CONFLICT_WITH_CLAIM", "SPONSOR_CONFLICT_WITH_CONTENT"],
        "disclosure_placement_gate": ["DISCLOSURE_PLACEMENT_INSUFFICIENT", "DISCLOSURE_NOT_CLEAR_AND_CONSPICUOUS"],
        "search_demand_gate": ["SEARCH_DEMAND_EVIDENCE_MISSING", "SEARCH_VOLUME_UNKNOWN"],
        "distribution_readiness_gate": ["DISTRIBUTION_READINESS_MISSING", "METADATA_READINESS_MISSING"],
        "packaging_expectation_gate": ["PACKAGING_PROMISE_MISMATCH"],
        "privacy_retention_gate": ["RAW_COMMENT_STORAGE_BLOCKED", "CONTEXT_SCOPE_MISSING"],
        "publish_risk_gate": ["MANUAL_REVIEW_REQUIRED", "SYSTEM_OK"],
    }
    return refs.get(gate_key, ["GATE_INPUT_INSUFFICIENT"])


def _gate_domain(gate_key: str) -> str:
    if gate_key.startswith("ai_"):
        return "ai_policy"
    if "rights" in gate_key or "disclosure" in gate_key:
        return "legal_compliance"
    if "brand" in gate_key or "commercial_conflict" in gate_key:
        return "brand_safety"
    if "search" in gate_key or "distribution" in gate_key or "packaging" in gate_key:
        return "audience_delivery"
    if "privacy" in gate_key:
        return "privacy"
    return "readiness"


def _require_user(session: Session, user_id: uuid.UUID, field_name: str) -> None:
    if session.get(User, user_id) is None:
        raise NotFoundError(f"{field_name} user not found: {user_id}")


def _record_m3_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID | None,
    correlation_id: str,
    payload: dict[str, Any],
) -> tuple[AuditEvent, DomainEvent]:
    audit = AuditService(session).append(
        AuditEnvelope(
            actor_type="user" if actor_id else "system",
            actor_id=actor_id,
            action=event_type,
            target_type=target_type,
            target_id=target_id,
            reason_code="AUDIT_EVENT_RECORDED",
            correlation_id=correlation_id,
            payload=payload,
        ),
        company_id=company_id,
    )
    domain = DomainEventBus(session).append(
        EventEnvelope(
            event_type=event_type,
            event_version=1,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            metadata={"actor_id": str(actor_id) if actor_id else None},
            correlation_id=correlation_id,
            causation_id=audit.id,
        ),
        company_id=company_id,
    )
    return audit, domain
