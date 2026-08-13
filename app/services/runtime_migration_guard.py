"""Fail-closed runtime guard for schema-dependent production work."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session


REQUIRED_RUNTIME_DB_REVISION = "0078_v2_drive_recovery_clock"
RUNTIME_DB_REVISION_BLOCKED = "RUNTIME_DB_REVISION_BELOW_CONTROLLED_RECOVERY_AUTHORITY"


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
        ready = self._is_at_or_after_required_revision(current_text)
        return RuntimeMigrationStatus(
            current_revision=current_text,
            required_revision=REQUIRED_RUNTIME_DB_REVISION,
            ready=ready,
            reason_code=None if ready else RUNTIME_DB_REVISION_BLOCKED,
        )

    @staticmethod
    def _is_at_or_after_required_revision(current: str | None) -> bool:
        return is_revision_at_or_after(
            current, minimum_revision=REQUIRED_RUNTIME_DB_REVISION
        )


def is_revision_at_or_after(current: str | None, *, minimum_revision: str) -> bool:
    """Return whether ``current`` is a known Alembic descendant of a minimum.

    This is intentionally graph based: Alembic revision labels are opaque
    identifiers, so neither lexical nor numeric comparison is valid.
    """

    if not current or not minimum_revision:
        return False
    try:
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "alembic"))
        scripts = ScriptDirectory.from_config(config)
        if scripts.get_revision(current) is None:
            return False
        pending = [current]
        seen: set[str] = set()
        while pending:
            revision_id = pending.pop()
            if revision_id == minimum_revision:
                return True
            if revision_id in seen:
                continue
            seen.add(revision_id)
            revision = scripts.get_revision(revision_id)
            if revision is None:
                return False
            parents = revision.down_revision
            if parents is None:
                continue
            if isinstance(parents, str):
                pending.append(parents)
            else:
                pending.extend(str(parent) for parent in parents)
    except Exception:
        # The worker must not assume a database is safe if its migration graph
        # cannot be read from the deployed code bundle.
        return False
    return False
