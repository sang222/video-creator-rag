"""P3 one-engine/many-profiles and portfolio isolation audit.

This is intentionally a deterministic audit, not a new scale agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.channel import ChannelProfileVersion, ChannelWorkspace
from app.db.models.m7 import UploadedVideo
from app.db.models.r3d5 import ChannelMemoryItem


@dataclass(frozen=True, slots=True)
class ScaleAuditResult:
    result: str
    violations: tuple[str, ...]
    active_channel_count: int
    public_portfolio_channel_count: int
    multi_channel_live_proven: bool


class OneEngineManyProfilesAudit:
    BANNED_LITERAL_PATTERNS = {
        "SMALL_TEAM_AI_HARDCODE": re.compile(r"Small\s+Team\s+AI", re.IGNORECASE),
        "NICHE_RUNTIME_BRANCH": re.compile(r"\b(?:if|elif)\s+[^\n:]*\bniche\b[^\n:]*:"),
        "SHORTS_PRODUCT_RETURN": re.compile(r"\bSHORTS\b|\bSHORT_FORM\b"),
        "CREATOMATE_LEGACY": re.compile(r"creatomate", re.IGNORECASE),
        "LUMA_LEGACY": re.compile(r"\bluma\b", re.IGNORECASE),
    }

    def __init__(self, session: Session | None = None):
        self.session = session

    def scan_active_source(self, root: Path) -> tuple[str, ...]:
        violations: list[str] = []
        targets = [root / "app"]
        for target in targets:
            if not target.exists():
                continue
            for path in target.rglob("*.py"):
                if any(part in {"__pycache__", "migrations"} for part in path.parts):
                    continue
                text = path.read_text(encoding="utf-8")
                for code, pattern in self.BANNED_LITERAL_PATTERNS.items():
                    if pattern.search(text):
                        violations.append(f"{code}:{path.relative_to(root)}")
        return tuple(sorted(set(violations)))

    def database_isolation_violations(self) -> tuple[str, ...]:
        if self.session is None:
            return ()
        violations: list[str] = []
        orphan_profiles = int(
            self.session.scalar(
                select(func.count(ChannelProfileVersion.id))
                .outerjoin(ChannelWorkspace, ChannelWorkspace.id == ChannelProfileVersion.channel_workspace_id)
                .where(ChannelWorkspace.id.is_(None))
            )
            or 0
        )
        if orphan_profiles:
            violations.append(f"ORPHAN_CHANNEL_PROFILES:{orphan_profiles}")
        orphan_memory = int(
            self.session.scalar(
                select(func.count(ChannelMemoryItem.id))
                .outerjoin(ChannelWorkspace, ChannelWorkspace.id == ChannelMemoryItem.channel_workspace_id)
                .where(ChannelWorkspace.id.is_(None))
            )
            or 0
        )
        if orphan_memory:
            violations.append(f"ORPHAN_CHANNEL_MEMORY:{orphan_memory}")
        return tuple(violations)

    def run(self, root: Path) -> ScaleAuditResult:
        violations = list(self.scan_active_source(root))
        violations.extend(self.database_isolation_violations())
        active_channels = 0
        public_channels = 0
        if self.session is not None:
            active_channels = int(
                self.session.scalar(
                    select(func.count(ChannelWorkspace.id)).where(
                        ChannelWorkspace.status.in_(["active", "ready", "ACTIVE", "READY"])
                    )
                )
                or 0
            )
            public_channels = int(
                self.session.scalar(
                    select(func.count(func.distinct(UploadedVideo.channel_workspace_id))).where(
                        UploadedVideo.actual_visibility == "PUBLIC",
                        UploadedVideo.verification_status == "VERIFIED",
                    )
                )
                or 0
            )
        return ScaleAuditResult(
            result="PASS" if not violations else "FAIL",
            violations=tuple(sorted(set(violations))),
            active_channel_count=active_channels,
            public_portfolio_channel_count=public_channels,
            multi_channel_live_proven=public_channels >= 2,
        )
