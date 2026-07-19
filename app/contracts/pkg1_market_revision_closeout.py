from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PKG1MarketRevisionApprovalCommand(BaseModel):
    project_id: uuid.UUID
    review_task_id: uuid.UUID
    reviewed_package_artifact_version_id: uuid.UUID
    reviewed_package_hash: str = Field(min_length=64, max_length=64)
    reviewed_revision_id: uuid.UUID
    reviewed_revision_version: int = Field(gt=0)
    reviewed_revision_hash: str = Field(min_length=64, max_length=64)
    decided_by_user_id: uuid.UUID
    decision: Literal["PASS"]
    decision_source: Literal["OPERATOR"]
    review_authority: Literal["HUMAN"]
    operator_decision_text: Literal["PASS"]
    approval_scope: Literal[
        "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
    ] = "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
    approval_ref: str
    review_notes: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("reviewed_package_hash", "reviewed_revision_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("reviewed hashes must be lowercase SHA-256 hex")
        return value

    @field_validator("approval_ref")
    @classmethod
    def validate_approval_ref(cls, value: str) -> str:
        if not value.startswith("operator-approval://pkg1-market-revision/"):
            raise ValueError(
                "approval_ref must identify the PKG1 market revision operator decision"
            )
        return value
