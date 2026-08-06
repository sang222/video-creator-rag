"""Fail-closed runtime guard for schema-dependent production work."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


REQUIRED_RUNTIME_DB_REVISION = "0063_background_script_qual"
RUNTIME_DB_REVISION_BLOCKED = "RUNTIME_DB_REVISION_BELOW_QUALIFICATION_RECOVERY_AUTHORITY"


@dataclass(frozen=True, slots=True)
class RuntimeMigrationStatus:
    current_revision: str | None
    required_revision: str
    ready: bool
    reason_code: str | None

    def as_dict(self) -> dict[str, str | bool | None]:
        return {
            "current_revision": self.current_revision,
            "required_revision": self.required_revision,
            "ready": self.ready,
            "reason_code": self.reason_code,
        }


class RuntimeMigrationGuard:
    """Read-only revision check; it never upgrades production data."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def inspect(self) -> RuntimeMigrationStatus:
        try:
            current = self.session.scalar(
                text("select version_num from alembic_version limit 1")
            )
        except SQLAlchemyError:
            current = None
        current_text = str(current) if current else None
        ready = current_text == REQUIRED_RUNTIME_DB_REVISION
        return RuntimeMigrationStatus(
            current_revision=current_text,
            required_revision=REQUIRED_RUNTIME_DB_REVISION,
            ready=ready,
            reason_code=None if ready else RUNTIME_DB_REVISION_BLOCKED,
        )
