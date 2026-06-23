import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ChannelWorkspace, CompiledChannelPolicySnapshot


class PolicySnapshotService:
    def __init__(self, session: Session):
        self.session = session

    def get_snapshot(self, snapshot_id: uuid.UUID) -> CompiledChannelPolicySnapshot | None:
        return self.session.get(CompiledChannelPolicySnapshot, snapshot_id)

    def get_active_snapshot_for_channel(
        self, channel_id: uuid.UUID
    ) -> CompiledChannelPolicySnapshot | None:
        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None or channel.active_policy_snapshot_id is None:
            return None
        return self.get_snapshot(channel.active_policy_snapshot_id)

    def list_snapshots(self, channel_id: uuid.UUID) -> list[CompiledChannelPolicySnapshot]:
        statement = (
            select(CompiledChannelPolicySnapshot)
            .where(CompiledChannelPolicySnapshot.channel_workspace_id == channel_id)
            .order_by(CompiledChannelPolicySnapshot.snapshot_version.desc())
        )
        return list(self.session.scalars(statement).all())
