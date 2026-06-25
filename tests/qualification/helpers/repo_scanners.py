from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from sqlalchemy import inspect

from app.main import create_app

from .qualification_asserts import ROOT


EXECUTABLE_SCAN_ROOTS = ("app", "alembic")

FORBIDDEN_ROUTE_FRAGMENTS = {
    "publish",
    "publish-handoff",
    "upload",
    "manual-publish",
    "analytics",
    "no-view",
    "dashboard",
    "operator-cockpit",
    "memory",
    "rag",
    "vector",
    "envato",
    "scrape",
}

FORBIDDEN_TABLE_FRAGMENTS = {
    "publish_packages",
    "publish_handoff",
    "publish_upload",
    "manual_publish",
    "uploaded_video",
    "analytics_event",
    "analytics_semantic",
    "no_view",
    "memory_promotion",
    "dashboard",
    "operator_cockpit",
    "vector",
    "embedding",
    "rag",
    "envato",
    "scrap",
    "algorithm_agent",
    "growth_agent",
    "view_agent",
    "fake_traffic",
    "bot_engagement",
    "auto_reupload",
}

FORBIDDEN_SYMBOL_PATTERNS = {
    r"\bPublisher\b",
    r"\bPublishService\b",
    r"\bPublishUpload\b",
    r"\bPublishPackage\b",
    r"\bPublishHandoff\b",
    r"\bPublishHandoffPackage\b",
    r"\bPublishHandoffService\b",
    r"\bPublisherService\b",
    r"\bManualPublishHandoff\b",
    r"\bManualPublishHandoffService\b",
    r"\bManualPublish\b",
    r"\bManualPublishConfirmation\b",
    r"\bUploadService\b",
    r"\bUploadedVideo\b",
    r"\bUploadedVideoPublicationSummary\b",
    r"\bPlatformPublishInstruction\b",
    r"\bPlatformPublishAdapter\b",
    r"\bAnalyticsSemanticLayer\b",
    r"\bAnalyticsEventService\b",
    r"\bNoViewRecovery\b",
    r"\bMemoryPromotion\b",
    r"\bOperatorCockpit\b",
    r"\bDashboardWidget\b",
    r"\bDashboardService\b",
    r"\bVectorStore\b",
    r"\bVectorIndex\b",
    r"\bEmbeddingStore\b",
    r"\bRAGEngine\b",
    r"\bRetrievalAugmentedGeneration\b",
    r"\bEnvatoClient\b",
    r"\bEnvatoDownloader\b",
    r"\bSourceScraper\b",
    r"\bMarketplaceScraper\b",
    r"\bSourceParser\b",
    r"\bAlgorithmAgent\b",
    r"\bGrowthAgent\b",
    r"\bViewAgent\b",
    r"\bAutoReupload\b",
    r"\bFakeTraffic\b",
    r"\bBotEngagement\b",
    r"\bOPAPolicyEngine\b",
    r"\bCedarPolicyEngine\b",
    r"\b0008_m7_manual_publish\b",
    r"\bm7_manual_publish\b",
}

FORBIDDEN_IMPORT_PATTERNS = {
    r"^\s*import\s+openai\b",
    r"^\s*from\s+openai\b",
    r"^\s*import\s+anthropic\b",
    r"^\s*from\s+anthropic\b",
    r"^\s*import\s+ollama\b",
    r"^\s*from\s+ollama\b",
    r"^\s*import\s+requests\b",
    r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",
    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",
    r"^\s*from\s+aiohttp\b",
    r"^\s*import\s+boto3\b",
    r"^\s*from\s+boto3\b",
}

FORBIDDEN_BEHAVIOR_PATTERNS = {
    r"envato.*(api|download|generate)",
    r"(api|download|generate).*envato",
    r"(fake_traffic|traffic_bot|view_bot|engagement_bot)",
    r"(platform_evasion|evasion_strategy|avoid_detection)",
    r"(auto_reupload|auto_re_upload)",
    r"(scrape_marketplace|marketplace_scraper|source_scraper)",
}

ALLOWED_CODE_SUBSTRINGS = {
    "publish_risk_gate",
    "published_at",
    "MockAnalyticsProvider",
    "mock_analytics",
    "MANUAL_ENVATO_PLACEHOLDER",
    "envato_api_calls",
    "APPROVED_ASSET_POOL_LOOKUP_PLACEHOLDER",
}


@dataclass(frozen=True)
class ScopeViolation:
    path: str
    pattern: str
    line: int | None
    text: str


def executable_python_files(*, root: Path = ROOT) -> Iterable[Path]:
    for folder in EXECUTABLE_SCAN_ROOTS:
        yield from (root / folder).rglob("*.py")


def route_scope_violations() -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    for route in create_app().routes:
        path = getattr(route, "path", "")
        lowered = path.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_ROUTE_FRAGMENTS):
            violations.append(ScopeViolation("app.routes", "forbidden route", None, path))
    return violations


def table_scope_violations(engine) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    for table in inspect(engine).get_table_names():
        lowered = table.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_TABLE_FRAGMENTS):
            violations.append(ScopeViolation("database", "forbidden table", None, table))
    return violations


def source_scope_violations(*, root: Path = ROOT) -> list[ScopeViolation]:
    violations: list[ScopeViolation] = []
    patterns = [
        *FORBIDDEN_SYMBOL_PATTERNS,
        *FORBIDDEN_IMPORT_PATTERNS,
        *FORBIDDEN_BEHAVIOR_PATTERNS,
    ]
    compiled = [(pattern, re.compile(pattern, re.IGNORECASE | re.MULTILINE)) for pattern in patterns]
    for path in executable_python_files(root=root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for pattern, regex in compiled:
            for match in regex.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                line = text.splitlines()[line_no - 1].strip()
                if any(allowed in line for allowed in ALLOWED_CODE_SUBSTRINGS):
                    continue
                violations.append(ScopeViolation(rel, pattern, line_no, line))
    return violations


def cli_scope_violations(*, root: Path = ROOT) -> list[ScopeViolation]:
    source = (root / "app/cli/main.py").read_text(encoding="utf-8")
    violations: list[ScopeViolation] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip().lower()
        if "add_typer" in stripped or ".command(" in stripped:
            if any(fragment in stripped for fragment in FORBIDDEN_ROUTE_FRAGMENTS):
                violations.append(ScopeViolation("app/cli/main.py", "forbidden CLI command", line_no, line.strip()))
    return violations


def all_scope_violations(engine=None, *, root: Path = ROOT) -> list[ScopeViolation]:
    violations = [*source_scope_violations(root=root), *route_scope_violations(), *cli_scope_violations(root=root)]
    if engine is not None:
        violations.extend(table_scope_violations(engine))
    return violations
