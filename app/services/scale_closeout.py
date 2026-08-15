"""P3 one-engine/many-profiles and portfolio isolation audit.

The audit distinguishes active behavioral hard-codes from immutable historical
labels.  A legacy policy version string containing a channel slug is not by
itself runtime authority; a branch/default that selects behavior by that slug
is. Historical canary/recovery modules are quarantined explicitly rather than
rewritten, so their evidence remains reproducible.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class _BehavioralHardcodeVisitor(ast.NodeVisitor):
    LEGACY_FUNCTION_PREFIXES = (
        "approve_and_activate_ch1_",
        "build_ch1_",
        "compile_ch1_",
        "rehearse_ch1_",
    )
    CHANNEL_TARGET_NAMES = {
        "channel_key",
        "channel_name",
        "channel_id",
        "destination_channel_key",
        "expected_channel_key",
    }

    def __init__(self):
        self.violations: set[str] = set()
        self._skip_depth = 0

    @staticmethod
    def _is_first_channel_literal(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip().lower().replace("_", "-")
        return normalized in {"small-team-ai", "small team ai"}

    @staticmethod
    def _name(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = _BehavioralHardcodeVisitor._name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    @classmethod
    def _is_channel_target(cls, node: ast.AST) -> bool:
        name = (cls._name(node) or "").lower()
        leaf = name.rsplit(".", 1)[-1]
        return leaf in cls.CHANNEL_TARGET_NAMES or name.endswith("channelworkspace.key")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith(self.LEGACY_FUNCTION_PREFIXES):
            self._skip_depth += 1
            self._skip_depth -= 1
            return
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name.startswith(self.LEGACY_FUNCTION_PREFIXES):
            return
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if self._skip_depth:
            return
        if (
            isinstance(node.value, ast.Constant)
            and self._is_first_channel_literal(node.value.value)
            and any(self._is_channel_target(target) for target in node.targets)
        ):
            self.violations.add("SMALL_TEAM_AI_HARDCODE")
        for target in node.targets:
            name = (self._name(target) or "").upper()
            if (
                "SMALL_TEAM_AI" in name
                or name == "CHANNEL_SCOPED_STRATEGIES"
            ) and self._node_contains_first_channel_literal(node.value):
                self.violations.add("SMALL_TEAM_AI_HARDCODE")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._skip_depth:
            return
        if (
            node.value is not None
            and self._node_contains_first_channel_literal(node.value)
            and self._is_channel_target(node.target)
        ):
            self.violations.add("SMALL_TEAM_AI_HARDCODE")
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if self._skip_depth:
            return
        operands = [node.left, *node.comparators]
        has_channel_target = any(self._is_channel_target(item) for item in operands)
        has_first_channel_literal = any(
            isinstance(item, ast.Constant)
            and self._is_first_channel_literal(item.value)
            for item in operands
        )
        if has_channel_target and has_first_channel_literal:
            self.violations.add("SMALL_TEAM_AI_HARDCODE")
        if (
            isinstance(node.left, ast.Name)
            and node.left.id == "niche"
            and any(
                isinstance(item, ast.Constant) and isinstance(item.value, str)
                for item in node.comparators
            )
        ):
            self.violations.add("NICHE_RUNTIME_BRANCH")
        self.generic_visit(node)

    @classmethod
    def _node_contains_first_channel_literal(cls, node: ast.AST) -> bool:
        return any(
            isinstance(item, ast.Constant)
            and cls._is_first_channel_literal(item.value)
            for item in ast.walk(node)
        )


class OneEngineManyProfilesAudit:
    # These modules are immutable canary/recovery/superseded execution evidence,
    # not reachable authority for the current AI-only long-form runtime. They
    # remain in source for audit/replay and are intentionally quarantined.
    NON_RUNTIME_EVIDENCE_FILES = frozenset(
        {
            "as1_rehearsal.py",
            "asset_request_compiler.py",
            "google_gemini_image_rehearsal.py",
            "google_veo_rehearsal.py",
            "img_canary.py",
            "img_canary_runner.py",
            "img_canary_security.py",
            "long_production.py",
            "mr1_local_production.py",
            "mr1_provider_gateways.py",
            "mr1_real_production.py",
            "mr1_reapproval_sc07_sc09.py",
            "pkg1.py",
            "pkg1_market_revision.py",
            "pkg1_sc07_sc09_revision.py",
            "scale_closeout.py",
        }
    )
    # Channel-init research is allowed to branch on the human-selected niche;
    # runtime executors are not.
    CHANNEL_INIT_SEMANTIC_FILES = frozenset({"m12_2p3.py"})

    TEXT_PATTERNS = {
        "SHORTS_PRODUCT_RETURN": re.compile(
            r"(?:production_lane|content_mode|target_surface)\s*==?\s*['\"](?:SHORTS|SHORT_FORM)['\"]",
            re.IGNORECASE,
        ),
        "CREATOMATE_LEGACY": re.compile(r"creatomate", re.IGNORECASE),
        # Provider identity only; "luma" in image-QC code means luminance.
        "LUMA_LEGACY": re.compile(
            r"(?:provider|adapter)[^\n]{0,80}['\"]luma['\"]|\bLUMA_(?:PROVIDER|MODEL|ADAPTER)",
            re.IGNORECASE,
        ),
    }

    def __init__(self, session: Session | None = None):
        self.session = session

    def scan_active_source(self, root: Path) -> tuple[str, ...]:
        violations: list[str] = []
        target = root / "app"
        if not target.exists():
            return ()
        for path in target.rglob("*.py"):
            if path.name in self.NON_RUNTIME_EVIDENCE_FILES or any(
                part in {"__pycache__", "migrations"} for part in path.parts
            ):
                continue
            text = path.read_text(encoding="utf-8")
            for code, pattern in self.TEXT_PATTERNS.items():
                if pattern.search(text):
                    violations.append(f"{code}:{path.relative_to(root)}")
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                violations.append(f"PYTHON_PARSE_ERROR:{path.relative_to(root)}")
                continue
            visitor = _BehavioralHardcodeVisitor()
            visitor.visit(tree)
            for code in visitor.violations:
                if (
                    code == "NICHE_RUNTIME_BRANCH"
                    and path.name in self.CHANNEL_INIT_SEMANTIC_FILES
                ):
                    continue
                violations.append(f"{code}:{path.relative_to(root)}")
        return tuple(sorted(set(violations)))

    def database_isolation_violations(self) -> tuple[str, ...]:
        if self.session is None:
            return ()
        violations: list[str] = []
        orphan_profiles = int(
            self.session.scalar(
                select(func.count(ChannelProfileVersion.id))
                .outerjoin(
                    ChannelWorkspace,
                    ChannelWorkspace.id == ChannelProfileVersion.channel_workspace_id,
                )
                .where(ChannelWorkspace.id.is_(None))
            )
            or 0
        )
        if orphan_profiles:
            violations.append(f"ORPHAN_CHANNEL_PROFILES:{orphan_profiles}")
        orphan_memory = int(
            self.session.scalar(
                select(func.count(ChannelMemoryItem.id))
                .outerjoin(
                    ChannelWorkspace,
                    ChannelWorkspace.id == ChannelMemoryItem.channel_workspace_id,
                )
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
                        ChannelWorkspace.status.in_(
                            ["active", "ready", "ACTIVE", "READY"]
                        )
                    )
                )
                or 0
            )
            public_channels = int(
                self.session.scalar(
                    select(
                        func.count(func.distinct(UploadedVideo.channel_workspace_id))
                    ).where(
                        UploadedVideo.actual_visibility == "PUBLIC",
                        UploadedVideo.verification_status.in_(
                            ["VERIFIED", "VERIFIED_PUBLIC"]
                        ),
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
