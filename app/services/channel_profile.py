import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, ChannelProfileInput, ChannelProfileVersionCreate
from app.core.errors import NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    User,
)
from app.services.audit import AuditService
from app.services.config_registry import content_hash
from app.services.profile_compiler import ChannelProfileCompiler


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

    def approve_profile_version(
        self,
        *,
        profile_version_id: uuid.UUID,
        approved_by: uuid.UUID | None = None,
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
            )
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
            payload={"snapshot_id": str(snapshot.id)},
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
        channel = self.session.get(ChannelWorkspace, snapshot.channel_workspace_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {snapshot.channel_workspace_id}")
        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        channel.active_policy_snapshot_id = snapshot.id
        snapshot.status = "active"
        snapshot.activated_at = utc_now()
        if profile_version is not None:
            profile_version.status = "active"
        self.session.flush()
        self._audit(
            action="policy_snapshot.activated",
            target_id=snapshot.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={"channel_id": str(channel.id), "profile_version_id": str(snapshot.channel_profile_version_id)},
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
    ) -> None:
        AuditService(self.session).append(
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
