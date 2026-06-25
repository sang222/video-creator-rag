from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


CheckStatus = Literal["PASS", "FAIL", "BLOCKED", "WARN", "SKIP"]


@dataclass
class QualificationCheck:
    name: str
    status: CheckStatus
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class QualificationSummary:
    verdict: Literal["PASS", "FAIL", "BLOCKED"]
    safe_to_start_m7: bool
    checks: list[QualificationCheck] = field(default_factory=list)

    def failed_checks(self) -> list[QualificationCheck]:
        return [check for check in self.checks if check.status in {"FAIL", "BLOCKED"}]
