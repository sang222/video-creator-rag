import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class UploadedVideoBackfillEvent(Base):
    __tablename__ = "uploaded_video_backfill_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    uploaded_video_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("uploaded_videos.id"))
    human_upload_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("human_upload_tasks.id"))
    channel_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("channel_workspaces.id"), nullable=False)
    input_url_or_video_id: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_video_id: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(String(40), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(80))
    new_status: Mapped[str | None] = mapped_column(String(80))
    operator_note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_uploaded_video_backfill_events_uploaded_video_id", "uploaded_video_id"),
        Index("ix_uploaded_video_backfill_events_task_id", "human_upload_task_id"),
        Index("ix_uploaded_video_backfill_events_channel_id", "channel_id"),
        Index("ix_uploaded_video_backfill_events_parse_status", "parse_status"),
        Index("ix_uploaded_video_backfill_events_created_at", "created_at"),
    )
