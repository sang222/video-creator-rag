import uuid
from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import (
    AuditEnvelope,
    ChannelProfileDraftUpdate,
    ChannelProfileInput,
    ChannelProfileVersionCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    AuditEvent,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    User,
)
from app.services.audit import AuditService
from app.services.config_registry import content_hash
from app.services.channel_contract import (
    CONTRACT_COMPLETE,
    contract_status_from_snapshot_payload,
    reject_legacy_provider_budget_fields,
)
from app.services.profile_compiler import ChannelProfileCompiler
from app.services.domain_events import DomainEventBus
from app.contracts import EventEnvelope


class ChannelProfileService:
    def __init__(self, session: Session):
        self.session = session

    def create_profile_version(
        self,
        *,
        channel_id: uuid.UUID,
        data: ChannelProfileVersionCreate,
        correlation_id: str = "m1-profile-create",
    ) -> ChannelProfileVersion:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        if data.created_by is not None and self.session.get(User, data.created_by) is None:
            raise NotFoundError(f"user not found: {data.created_by}")
        reject_legacy_provider_budget_fields(data.model_dump(mode="json"))
        if data.profile_input is not None:
            profile_input = data.profile_input
            source_template_key = profile_input.template_key
            source_template_version = profile_input.template_version
        elif data.template_key is not None:
            profile_input, catalogs = ChannelProfileCompiler(self.session).profile_input_from_template(
                data.template_key
            )
            source_template_key = data.template_key
            source_template_version = catalogs.template_catalog.catalog_version
        else:
            raise ValidationFailureError("template_key or profile_input is required")
        next_version = (
            self.session.scalar(
                select(func.max(ChannelProfileVersion.version)).where(
                    ChannelProfileVersion.channel_workspace_id == channel_id
                )
            )
            or 0
        ) + 1
        payload = profile_input.model_dump(mode="json")
        profile_version = ChannelProfileVersion(
            channel_workspace_id=channel_id,
            version=next_version,
            status="draft",
            profile_input=payload,
            profile_input_hash=content_hash(payload),
            source_template_key=source_template_key,
            source_template_version=source_template_version,
            created_by=data.created_by,
        )
        self.session.add(profile_version)
        self.session.flush()
        self._audit(
            action="channel_profile.created",
            target_id=profile_version.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={"channel_id": str(channel_id), "version": next_version},
        )
        return profile_version

    def get_profile_version(self, profile_version_id: uuid.UUID) -> ChannelProfileVersion | None:
        return self.session.get(ChannelProfileVersion, profile_version_id)

    def list_profile_versions(self, channel_id: uuid.UUID) -> list[ChannelProfileVersion]:
        statement = (
            select(ChannelProfileVersion)
            .where(ChannelProfileVersion.channel_workspace_id == channel_id)
            .order_by(ChannelProfileVersion.version.desc())
        )
        return list(self.session.scalars(statement).all())

    def get_active_profile_version(self, channel_id: uuid.UUID) -> ChannelProfileVersion | None:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None or channel.active_policy_snapshot_id is None:
            return None
        snapshot = self.session.get(CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id)
        return self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id) if snapshot else None

    def management_read_model(self, channel_id: uuid.UUID) -> dict:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        versions = []
        for profile in self.list_profile_versions(channel_id):
            snapshot = self.session.scalars(
                select(CompiledChannelPolicySnapshot)
                .where(CompiledChannelPolicySnapshot.channel_profile_version_id == profile.id)
                .order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
            ).first()
            capability = (snapshot.compiled_payload or {}).get("capability_evaluation") if snapshot else None
            versions.append(
                {
                    "id": str(profile.id),
                    "version": profile.version,
                    "status": profile.status,
                    "profile_input_hash": profile.profile_input_hash,
                    "profile_input": self._effective_profile_input(profile),
                    "latest_snapshot_id": str(snapshot.id) if snapshot else None,
                    "latest_snapshot_hash": snapshot.content_hash if snapshot else None,
                    "snapshot_status": snapshot.status if snapshot else None,
                    "is_active": channel.active_policy_snapshot_id == (snapshot.id if snapshot else None),
                    "capability_status": capability.get("status") if capability else "NOT_COMPILED",
                    "capability_blockers": capability.get("blockers", []) if capability else [],
                }
            )
        return {
            "channel_id": str(channel.id),
            "active_policy_snapshot_id": str(channel.active_policy_snapshot_id) if channel.active_policy_snapshot_id else None,
            "versions": versions,
            "provider_execution_available": False,
            "exact_next_action": "Tạo draft mới để thay đổi; compile, duyệt rồi mới kích hoạt cho dự án tương lai.",
        }

    def create_draft_from_active(
        self,
        *,
        channel_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
        correlation_id: str = "ch1-flex-draft-from-active",
    ) -> ChannelProfileVersion:
        channel = self.session.get(ChannelWorkspace, channel_id)
        active = self.get_active_profile_version(channel_id)
        if channel is None or active is None or channel.active_policy_snapshot_id is None:
            raise ValidationFailureError("active profile and snapshot are required")
        profile_input = ChannelProfileInput.model_validate(active.profile_input)
        if profile_input.channel_policy is None:
            snapshot = self.session.get(CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id)
            policy = (snapshot.compiled_payload or {}).get("channel_scoped_policy") if snapshot else None
            if not isinstance(policy, dict):
                raise ValidationFailureError("active channel-scoped policy is required")
            profile_payload = profile_input.model_dump(mode="json")
            profile_payload["channel_policy"] = policy
            profile_input = ChannelProfileInput.model_validate(profile_payload)
        return self.create_profile_version(
            channel_id=channel_id,
            data=ChannelProfileVersionCreate(profile_input=profile_input, created_by=created_by),
            correlation_id=correlation_id,
        )



    def update_draft(
        self,
        *,
        profile_version_id: uuid.UUID,
        data: ChannelProfileDraftUpdate,
        correlation_id: str = "ch1-flex-draft-update",
    ) -> ChannelProfileVersion:
        profile = self.get_profile_version(profile_version_id)
        if profile is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        if profile.status != "draft":
            raise ValidationFailureError("only a draft profile version can be edited")
        if data.expected_profile_input_hash and data.expected_profile_input_hash != profile.profile_input_hash:
            raise ValidationFailureError("draft profile hash conflict")
        if data.profile_input.channel_policy and data.profile_input.channel_policy.channel_key != self.session.get(
            ChannelWorkspace, profile.channel_workspace_id
        ).key:
            raise ValidationFailureError("draft channel policy scope mismatch")
        payload = data.profile_input.model_dump(mode="json")
        profile.profile_input = payload
        profile.profile_input_hash = content_hash(payload)
        self.session.flush()
        self._audit(
            action="channel_profile.draft_updated",
            target_id=profile.id,
            company_id=self.session.get(ChannelWorkspace, profile.channel_workspace_id).company_id,
            correlation_id=correlation_id,
            payload={"profile_input_hash": profile.profile_input_hash},
        )
        return profile

    def validate_draft(self, profile_version_id: uuid.UUID) -> dict:
        profile = self.get_profile_version(profile_version_id)
        if profile is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        parsed = ChannelProfileInput.model_validate(profile.profile_input)
        blockers = []
        if parsed.channel_policy is None:
            blockers.append("CHANNEL_SCOPED_POLICY_MISSING")
        elif parsed.channel_policy.policy_status != "APPROVED":
            blockers.append("CHANNEL_POLICY_NOT_APPROVED")
        return {
            "profile_version_id": str(profile.id),
            "status": "PASS" if not blockers else "BLOCKED",
            "profile_input_hash": profile.profile_input_hash,
            "blockers": blockers,
        }

    def preview_compile(self, profile_version_id: uuid.UUID) -> dict:
        profile = self.get_profile_version(profile_version_id)
        if profile is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        compiler = ChannelProfileCompiler(self.session)
        profile_input = ChannelProfileInput.model_validate(profile.profile_input)
        catalogs = compiler.load_catalogs(profile_input.template_key)
        payload, output_hash = compiler.compile_from_input(
            profile_input=profile_input,
            template=catalogs.template,
            capability_matrix=catalogs.capability_matrix,
            compiler_policy=catalogs.compiler_policy,
            channel=self.session.get(ChannelWorkspace, profile.channel_workspace_id),
            profile_input_hash_override=profile.profile_input_hash,
        )
        return {
            "profile_version_id": str(profile.id),
            "content_hash": output_hash,
            "capability_evaluation": payload.get("capability_evaluation"),
            "launch_restrictions": payload.get("launch_restrictions"),
            "snapshot_refs": payload.get("snapshot_refs"),
            "compiler_decision_log": payload.get("compiler_decision_log", []),
            "persisted": False,
        }

    def semantic_diff(self, profile_version_id: uuid.UUID, other_profile_version_id: uuid.UUID) -> dict:
        profile = self.get_profile_version(profile_version_id)
        other = self.get_profile_version(other_profile_version_id)
        if profile is None or other is None:
            raise NotFoundError("profile version not found")
        if profile.channel_workspace_id != other.channel_workspace_id:
            raise ValidationFailureError("profile versions must belong to the same channel")
        changes = _semantic_changes(
            self._effective_profile_input(profile),
            self._effective_profile_input(other),
        )
        return {
            "from_profile_version_id": str(other.id),
            "to_profile_version_id": str(profile.id),
            "changed_paths": changes,
            "different": bool(changes),
        }

    def _effective_profile_input(self, profile: ChannelProfileVersion) -> dict:
        payload = deepcopy(profile.profile_input)
        if payload.get("channel_policy") is not None:
            return payload
        snapshot = self.session.scalars(
            select(CompiledChannelPolicySnapshot)
            .where(CompiledChannelPolicySnapshot.channel_profile_version_id == profile.id)
            .order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
        ).first()
        scoped = (snapshot.compiled_payload or {}).get("channel_scoped_policy") if snapshot else None
        if isinstance(scoped, dict):
            payload["channel_policy"] = deepcopy(scoped)
        return payload

    def _latest_snapshot_for_profile(
        self,
        profile_version_id: uuid.UUID,
    ) -> CompiledChannelPolicySnapshot | None:
        return self.session.scalars(
            select(CompiledChannelPolicySnapshot)
            .where(
                CompiledChannelPolicySnapshot.channel_profile_version_id
                == profile_version_id
            )
            .order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
        ).first()

    def _find_audit_receipt(
        self,
        *,
        event_type: str,
        target_id: uuid.UUID,
    ) -> AuditEvent | None:
        return self.session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type == event_type,
                AuditEvent.target_id == target_id,
            )
            .order_by(AuditEvent.created_at.desc())
        ).first()

    def submit_for_approval(self, profile_version_id: uuid.UUID) -> ChannelProfileVersion:
        profile = self.get_profile_version(profile_version_id)
        if profile is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        if profile.status not in {"draft", "compiled"}:
            raise ValidationFailureError("only draft or compiled profile can be submitted")
        if profile.status == "draft":
            raise ValidationFailureError("compile is required before approval submission")
        profile.status = "pending_approval"
        self.session.flush()
        return profile

    def reject_profile_version(
        self,
        *,
        profile_version_id: uuid.UUID,
        reason: str,
        correlation_id: str = "ch1-flex-profile-reject",
    ) -> ChannelProfileVersion:
        profile = self.get_profile_version(profile_version_id)
        if profile is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        if profile.status not in {"compiled", "pending_approval"}:
            raise ValidationFailureError("profile is not awaiting approval")
        profile.status = "rejected"
        self.session.flush()
        channel = self.session.get(ChannelWorkspace, profile.channel_workspace_id)
        self._audit(
            action="channel_profile.rejected",
            target_id=profile.id,
            company_id=channel.company_id if channel else None,
            correlation_id=correlation_id,
            payload={"reason": reason},
        )
        return profile

    def approve_profile_version(
        self,
        *,
        profile_version_id: uuid.UUID,
        approved_by: uuid.UUID | None = None,
        approval_ref: str | None = None,
        correlation_id: str = "m1-profile-approve",
    ) -> ChannelProfileVersion:
        profile_version = self.get_profile_version(profile_version_id)
        if profile_version is None:
            raise NotFoundError(f"profile version not found: {profile_version_id}")
        if approved_by is not None and self.session.get(User, approved_by) is None:
            raise NotFoundError(f"user not found: {approved_by}")
        snapshot = self.session.scalars(
            select(CompiledChannelPolicySnapshot).where(
                CompiledChannelPolicySnapshot.channel_profile_version_id == profile_version_id
            ).order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
        ).first()
        if snapshot is None:
            raise ValidationFailureError("compiled snapshot is required before approval")
        profile_version.status = "approved"
        profile_version.approved_by = approved_by
        profile_version.approved_at = utc_now()
        snapshot.status = "approved"
        self.session.flush()
        channel = self.session.get(ChannelWorkspace, profile_version.channel_workspace_id)
        self._audit(
            action="channel_profile.approved",
            target_id=profile_version.id,
            company_id=channel.company_id if channel else None,
            correlation_id=correlation_id,
            payload={"snapshot_id": str(snapshot.id), "approval_ref": approval_ref},
        )
        return profile_version

    def activate_snapshot(
        self,
        *,
        snapshot_id: uuid.UUID,
        correlation_id: str = "m1-profile-activate",
    ) -> CompiledChannelPolicySnapshot:
        snapshot = self.session.get(CompiledChannelPolicySnapshot, snapshot_id)
        if snapshot is None:
            raise NotFoundError(f"snapshot not found: {snapshot_id}")
        contract_status, missing_fields, contradiction_reasons = contract_status_from_snapshot_payload(snapshot.compiled_payload)
        if contract_status != CONTRACT_COMPLETE:
            channel = self.session.get(ChannelWorkspace, snapshot.channel_workspace_id)
            blocked_payload = {
                "snapshot_id": str(snapshot.id),
                "reason_code": "CHANNEL_ACTIVATION_BLOCKED",
                "contract_status": contract_status,
                "missing_fields": missing_fields,
                "contradiction_reasons": contradiction_reasons,
            }
            AuditService(self.session).append(
                AuditEnvelope(
                    actor_type="system",
                    action="channel.activation_blocked",
                    target_type="channel_workspace",
                    target_id=snapshot.channel_workspace_id,
                    reason_code="CHANNEL_ACTIVATION_BLOCKED",
                    correlation_id=correlation_id,
                    payload=blocked_payload,
                ),
                company_id=channel.company_id if channel else None,
            )
            DomainEventBus(self.session).append(
                EventEnvelope(
                    event_type="channel.activation_blocked",
                    event_version=1,
                    aggregate_type="channel_workspace",
                    aggregate_id=snapshot.channel_workspace_id,
                    correlation_id=correlation_id,
                    payload=blocked_payload,
                ),
                company_id=channel.company_id if channel else None,
            )
            raise ValidationFailureError(
                f"channel contract is not COMPLETE (got {contract_status}); activation blocked. "
                f"missing_fields={missing_fields}, contradiction_reasons={contradiction_reasons}"
            )
        scoped_policy = (snapshot.compiled_payload or {}).get("channel_scoped_policy")
        if scoped_policy is not None:
            capability = (snapshot.compiled_payload or {}).get("capability_evaluation") or {}
            if snapshot.status not in {"approved", "active"}:
                raise ValidationFailureError("approved channel profile snapshot is required before activation")
            if capability.get("status") != "PASS":
                raise ValidationFailureError(f"channel capability blockers prevent activation: {capability.get('blockers', [])}")
        channel = self.session.get(ChannelWorkspace, snapshot.channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {snapshot.channel_workspace_id}")
        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        previous_status = channel.status
        previous_snapshot = (
            self.session.get(CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id)
            if channel.active_policy_snapshot_id and channel.active_policy_snapshot_id != snapshot.id
            else None
        )
        previous_profile = None
        if previous_snapshot is not None and previous_snapshot.status == "active":
            previous_snapshot.status = "approved"
            previous_profile = self.session.get(ChannelProfileVersion, previous_snapshot.channel_profile_version_id)
            if previous_profile is not None and previous_profile.id != snapshot.channel_profile_version_id:
                previous_profile.status = "approved"
        channel.active_policy_snapshot_id = snapshot.id
        channel.status = "active"
        snapshot.status = "active"
        snapshot.activated_at = utc_now()
        if profile_version is not None:
            profile_version.status = "active"
        metadata = dict(channel.metadata_ or {})
        metadata["m11_lifecycle_state"] = "ACTIVE"
        metadata["m11_health_status"] = metadata.get("m11_health_status", "NEW")
        channel.metadata_ = metadata
        self.session.flush()
        self._audit(
            action="policy_snapshot.activated",
            target_id=snapshot.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={
                "channel_id": str(channel.id),
                "profile_version_id": str(snapshot.channel_profile_version_id),
                "previous_status": previous_status,
                "new_status": "active",
                "rollback_snapshot_id": (
                    str(previous_snapshot.id) if previous_snapshot else None
                ),
                "rollback_profile_version_id": (
                    str(previous_profile.id) if previous_profile else None
                ),
            },
        )
        AuditService(self.session).append(
            AuditEnvelope(
                actor_type="system",
                action="channel.activated",
                target_type="channel_workspace",
                target_id=channel.id,
                reason_code="CHANNEL_ACTIVATED",
                correlation_id=correlation_id,
                payload={
                    "snapshot_id": str(snapshot.id),
                    "previous_status": previous_status,
                    "rollback_snapshot_id": (
                        str(previous_snapshot.id) if previous_snapshot else None
                    ),
                    "rollback_profile_version_id": (
                        str(previous_profile.id) if previous_profile else None
                    ),
                },
            ),
            company_id=channel.company_id,
        )
        DomainEventBus(self.session).append(
            EventEnvelope(
                event_type="channel.activated",
                event_version=1,
                aggregate_type="channel_workspace",
                aggregate_id=channel.id,
                correlation_id=correlation_id,
                payload={
                    "snapshot_id": str(snapshot.id),
                    "reason_code": "CHANNEL_ACTIVATED",
                    "previous_status": previous_status,
                    "new_status": "active",
                    "rollback_snapshot_id": (
                        str(previous_snapshot.id) if previous_snapshot else None
                    ),
                    "rollback_profile_version_id": (
                        str(previous_profile.id) if previous_profile else None
                    ),
                },
            ),
            company_id=channel.company_id,
        )
        return snapshot

    def _audit(
        self,
        *,
        action: str,
        target_id: uuid.UUID,
        company_id: uuid.UUID | None,
        correlation_id: str,
        payload: dict,
    ) -> AuditEvent:
        return AuditService(self.session).append(
            AuditEnvelope(
                actor_type="system",
                action=action,
                target_type="channel_profile",
                target_id=target_id,
                reason_code="AUDIT_EVENT_RECORDED",
                correlation_id=correlation_id,
                payload=payload,
            ),
            company_id=company_id,
        )


def _semantic_changes(current: object, previous: object, path: str = "$") -> list[dict[str, object]]:
    if isinstance(current, dict) and isinstance(previous, dict):
        changes: list[dict[str, object]] = []
        for key in sorted(set(current) | set(previous)):
            child_path = f"{path}.{key}"
            if key not in current:
                changes.append({"path": child_path, "change": "removed", "before": previous[key], "after": None})
            elif key not in previous:
                changes.append({"path": child_path, "change": "added", "before": None, "after": current[key]})
            else:
                changes.extend(_semantic_changes(current[key], previous[key], child_path))
        return changes
    if isinstance(current, list) and isinstance(previous, list):
        return [] if current == previous else [{"path": path, "change": "changed", "before": previous, "after": current}]
    return [] if current == previous else [{"path": path, "change": "changed", "before": previous, "after": current}]
