import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts import AuditEnvelope, ChannelMembershipCreate, ChannelWorkspaceCreate
from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.db.models import ChannelMembership, ChannelWorkspace, Company, Role, User
from app.services.audit import AuditService
from app.services.channel_contract import reject_legacy_provider_budget_fields


CHANNEL_STATUSES = {"draft", "ready", "active", "paused", "deactivated", "archived"}


class ChannelWorkspaceService:
    def __init__(self, session: Session):
        self.session = session

    def create_channel(
        self,
        *,
        company_id: uuid.UUID,
        data: ChannelWorkspaceCreate,
        correlation_id: str = "m1-channel-create",
    ) -> ChannelWorkspace:
        if self.session.get(Company, company_id) is None:
            raise NotFoundError(f"company not found: {company_id}")
        reject_legacy_provider_budget_fields(data.model_dump(mode="json"))
        duplicate = self.session.scalars(
            select(ChannelWorkspace).where(
                ChannelWorkspace.company_id == company_id,
                ChannelWorkspace.key == data.key,
            )
        ).one_or_none()
        if duplicate is not None:
            raise ConflictError(f"channel key already exists for company: {data.key}")
        channel = ChannelWorkspace(
            company_id=company_id,
            key=data.key,
            name=data.name,
            status=data.status,
            primary_language=data.primary_language,
            primary_region=data.primary_region,
            primary_timezone=data.primary_timezone or data.default_timezone,
            target_market=data.target_market,
            default_timezone=data.default_timezone,
            target_subtitle_languages=data.target_subtitle_languages,
            target_metadata_languages=data.target_metadata_languages,
            target_regions=data.target_regions,
            translation_mode=data.translation_mode,
            localization_required_for_publish=data.localization_required_for_publish,
            localized_metadata_required=data.localized_metadata_required,
            metadata_=data.metadata,
        )
        self.session.add(channel)
        self.session.flush()
        self._audit(
            action="channel.created",
            target_id=channel.id,
            company_id=company_id,
            correlation_id=correlation_id,
            payload={"key": channel.key, "status": channel.status},
        )
        return channel

    def get_channel(self, channel_id: uuid.UUID) -> ChannelWorkspace | None:
        return self.session.get(ChannelWorkspace, channel_id)

    def list_channels(self, company_id: uuid.UUID) -> list[ChannelWorkspace]:
        statement = (
            select(ChannelWorkspace)
            .where(ChannelWorkspace.company_id == company_id)
            .order_by(ChannelWorkspace.created_at.desc())
        )
        return list(self.session.scalars(statement).all())

    def update_status(
        self,
        *,
        channel_id: uuid.UUID,
        status: str,
        correlation_id: str = "m1-channel-status",
    ) -> ChannelWorkspace:
        if status not in CHANNEL_STATUSES:
            raise ValidationFailureError(f"unsupported channel status: {status}")
        channel = self.get_channel(channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        channel.status = status
        self.session.flush()
        self._audit(
            action="channel.status_changed",
            target_id=channel.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={"status": status},
        )
        return channel

    def assign_member(
        self,
        *,
        channel_id: uuid.UUID,
        data: ChannelMembershipCreate,
        correlation_id: str = "m1-channel-member-assign",
    ) -> ChannelMembership:
        channel = self.get_channel(channel_id)
        if channel is None:
            raise NotFoundError(f"channel not found: {channel_id}")
        if self.session.get(User, data.user_id) is None:
            raise NotFoundError(f"user not found: {data.user_id}")
        if self.session.get(Role, data.role_id) is None:
            raise NotFoundError(f"role not found: {data.role_id}")
        duplicate = self.session.scalars(
            select(ChannelMembership).where(
                ChannelMembership.channel_workspace_id == channel_id,
                ChannelMembership.user_id == data.user_id,
                ChannelMembership.role_id == data.role_id,
            )
        ).one_or_none()
        if duplicate is not None:
            raise ConflictError("channel membership already exists")
        membership = ChannelMembership(
            channel_workspace_id=channel_id,
            user_id=data.user_id,
            role_id=data.role_id,
            status=data.status,
        )
        self.session.add(membership)
        self.session.flush()
        self._audit(
            action="channel.member_assigned",
            target_id=membership.id,
            company_id=channel.company_id,
            correlation_id=correlation_id,
            payload={"channel_id": str(channel_id), "user_id": str(data.user_id)},
        )
        return membership

    def _audit(
        self,
        *,
        action: str,
        target_id: uuid.UUID,
        company_id: uuid.UUID,
        correlation_id: str,
        payload: dict,
    ) -> None:
        AuditService(self.session).append(
            AuditEnvelope(
                actor_type="system",
                action=action,
                target_type="channel_workspace",
                target_id=target_id,
                reason_code="AUDIT_EVENT_RECORDED",
                correlation_id=correlation_id,
                payload=payload,
            ),
            company_id=company_id,
        )
