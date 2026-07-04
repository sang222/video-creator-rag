import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.foundation import utc_created_at, uuid_pk


class PackageRuntimeDisposition(Base):
    __tablename__ = "package_runtime_dispositions"

    id: Mapped[uuid.UUID] = uuid_pk()
    package_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("first_scripted_video_packages.id"), nullable=False)
    disposition: Mapped[str] = mapped_column(String(80), nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    decided_by: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = utc_created_at()

    __table_args__ = (
        Index("ix_package_runtime_dispositions_package", "package_id"),
        Index("ix_package_runtime_dispositions_disposition", "disposition"),
        Index("ix_package_runtime_dispositions_created_at", "created_at"),
    )
