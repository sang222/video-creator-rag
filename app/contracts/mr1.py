from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts.vcos_v2 import DurationContractV2


class MR1ReapprovalCommand(BaseModel):
    """Explicit operator authority for one exact MR1 production run.

    Channel scope is derived from the frozen project/profile/snapshot lineage;
    the human-readable channel key remains input metadata and is never a
    product-specific runtime constant.
    """

    project_id: uuid.UUID
    pkg1_approval_decision_id: uuid.UUID
    pkg1_human_review_receipt_version_id: uuid.UUID
    channel_profile_version_id: uuid.UUID
    compiled_policy_snapshot_id: uuid.UUID
    approval_version: int = Field(default=1, gt=0)
    decision: Literal["APPROVED"] = "APPROVED"
    decision_source: Literal["OPERATOR"] = "OPERATOR"
    approval_purpose: Literal["MR1_REAL_PRODUCTION_EXECUTION"] = (
        "MR1_REAL_PRODUCTION_EXECUTION"
    )
    execution_mode: Literal["REAL_APPROVED_PRODUCTION"] = "REAL_APPROVED_PRODUCTION"
    run_type: Literal["MR1"] = "MR1"
    channel_key: str = Field(min_length=1, max_length=120)
    operator_decision_text: Literal["APPROVE_EXACT_MR1_EXECUTION"] = (
        "APPROVE_EXACT_MR1_EXECUTION"
    )

    model_config = ConfigDict(extra="forbid")


class MR1ApprovalIdentity(BaseModel):
    approval_id: uuid.UUID
    approval_version: int = Field(gt=0)
    approval_ref: str
    approval_content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid")

    @field_validator("approval_content_hash")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("approval_content_hash must be lowercase SHA-256 hex")
        return value


class MR1StartCommand(BaseModel):
    """Start only the exact approval produced by MR1 re-approval."""

    approval_id: uuid.UUID
    approval_content_hash: str = Field(min_length=64, max_length=64)
    project_id: uuid.UUID
    package_artifact_version_id: uuid.UUID
    execution_mode: Literal["REAL_APPROVED_PRODUCTION"] = "REAL_APPROVED_PRODUCTION"

    model_config = ConfigDict(extra="forbid")

    @field_validator("approval_content_hash")
    @classmethod
    def validate_start_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("approval_content_hash must be lowercase SHA-256 hex")
        return value


class MR1V2AutomatedAdmissionRequest(BaseModel):
    """Provider-free validation of the current automated Phase 3 authority."""

    schema_version: Literal["mr1.automated-admission.v2"] = (
        "mr1.automated-admission.v2"
    )
    project_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    execution_mode: Literal["VALIDATION_ONLY"] = "VALIDATION_ONLY"

    model_config = ConfigDict(extra="forbid")


class MR1V2AutomatedAdmissionRead(BaseModel):
    schema_version: Literal["mr1.automated-admission.v2"] = (
        "mr1.automated-admission.v2"
    )
    status: Literal["VALIDATED"] = "VALIDATED"
    project_id: uuid.UUID
    production_package_artifact_version_id: uuid.UUID
    production_package_version: int = Field(gt=0)
    production_package_hash: str = Field(
        min_length=64,
        max_length=64,
    )
    production_readiness_receipt_artifact_version_id: uuid.UUID
    production_readiness_receipt_hash: str = Field(
        min_length=64,
        max_length=64,
    )
    duration_contract: DurationContractV2
    provider_execution_plan_hash: str = Field(
        min_length=64,
        max_length=64,
    )
    budget_scope_hash: str = Field(min_length=64, max_length=64)
    legacy_approval_required: Literal[False] = False
    execution_authorized: Literal[False] = False
    reason_codes: list[
        Literal[
            "AUTOMATED_PRODUCTION_READINESS_ACCEPTED",
            "MR1_V2_REAL_EXECUTION_DISABLED",
        ]
    ]

    model_config = ConfigDict(extra="forbid", frozen=True)


MR1PexelsScene = Literal["SC-04", "SC-07", "SC-09"]
MR1PexelsOperationKey = Literal[
    "pexels:SC-04",
    "pexels:SC-07",
    "pexels:SC-09",
]
MR1ReviewedStockSearchIntent = Annotated[
    str,
    Field(min_length=12, max_length=500),
]


class MR1ProviderAttemptContinuationReviewCommand(BaseModel):
    """Provider-free proposal used to materialize an exact review manifest."""

    run_id: uuid.UUID
    operation_key: MR1PexelsOperationKey
    approved_stock_search_intent: MR1ReviewedStockSearchIntent
    approved_pending_scene_stock_search_intents: dict[
        MR1PexelsScene,
        MR1ReviewedStockSearchIntent,
    ] = Field(default_factory=dict)
    additional_attempts: Literal[1] = 1

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_exact_review_scope(self):
        scene_id = self.operation_key.removeprefix("pexels:")
        pending_scenes = sorted(
            self.approved_pending_scene_stock_search_intents
        )
        if scene_id in pending_scenes:
            raise ValueError("MR1_PROVIDER_CONTINUATION_SCENE_SCOPE_OVERLAP")
        if not self.approved_stock_search_intent.strip() or any(
            not value.strip()
            for value in (
                self.approved_pending_scene_stock_search_intents.values()
            )
        ):
            raise ValueError(
                "MR1_PROVIDER_CONTINUATION_STOCK_SEARCH_INTENT_INVALID"
            )
        return self


class MR1ProviderAttemptContinuationCommand(
    MR1ProviderAttemptContinuationReviewCommand
):
    """Explicit operator authority for one reviewed continuation manifest."""

    operator_review_manifest_artifact_version_id: uuid.UUID
    operator_review_manifest_content_hash: str = Field(
        min_length=64,
        max_length=64,
    )
    operator_review_task_id: uuid.UUID
    decided_by_user_id: uuid.UUID
    decision: Literal["APPROVED"] = "APPROVED"
    decision_source: Literal["OPERATOR"] = "OPERATOR"
    operator_decision_text: str = Field(min_length=1, max_length=220)

    model_config = ConfigDict(extra="forbid")

    @field_validator("operator_review_manifest_content_hash")
    @classmethod
    def validate_review_manifest_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError(
                "operator_review_manifest_content_hash must be lowercase SHA-256 hex"
            )
        return value

    @model_validator(mode="after")
    def validate_exact_operator_authority(self):
        scene_id = self.operation_key.removeprefix("pexels:")
        pending_scenes = sorted(
            self.approved_pending_scene_stock_search_intents
        )
        amendment_text = "".join(
            f" và đổi query Pexels {pending_scene} trước attempt đầu tiên"
            for pending_scene in pending_scenes
        )
        expected = (
            f"Phê duyệt thêm đúng 1 Pexels {scene_id} attempt"
            f"{amendment_text} cho run này; manifest sha256 "
            f"{self.operator_review_manifest_content_hash}"
        )
        if self.operator_decision_text != expected:
            raise ValueError("MR1_PROVIDER_CONTINUATION_OPERATOR_TEXT_INVALID")
        return self


class MR1FinalMediaCloseoutCommand(BaseModel):
    """Human authority for closing one exact MR1 review candidate."""

    run_id: uuid.UUID
    project_id: uuid.UUID
    review_media_candidate_artifact_version_id: uuid.UUID
    review_media_candidate_content_hash: str = Field(min_length=64, max_length=64)
    reviewed_output_sha256: str = Field(min_length=64, max_length=64)
    drive_archive_receipt_artifact_version_id: uuid.UUID
    drive_archive_receipt_content_hash: str = Field(min_length=64, max_length=64)
    archive_identity: str = Field(min_length=1)
    decided_by_user_id: uuid.UUID
    decision: Literal["PASS", "REJECT"]
    decision_source: Literal["OPERATOR"] = "OPERATOR"
    review_authority: Literal["HUMAN"] = "HUMAN"
    operator_decision_text: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator(
        "review_media_candidate_content_hash",
        "reviewed_output_sha256",
        "drive_archive_receipt_content_hash",
    )
    @classmethod
    def validate_closeout_hash(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("MR1 closeout hashes must be lowercase SHA-256 hex")
        return value

    @model_validator(mode="after")
    def validate_human_decision_text(self) -> "MR1FinalMediaCloseoutCommand":
        if self.decision == "PASS":
            if self.operator_decision_text != "PASS":
                raise ValueError(
                    "PASS closeout requires literal operator_decision_text=PASS"
                )
            return self
        reason = self.operator_decision_text.removeprefix("REJECT: ")
        if (
            not self.operator_decision_text.startswith("REJECT: ")
            or not reason
            or reason != reason.strip()
            or "\n" in reason
            or "\r" in reason
        ):
            raise ValueError(
                "REJECT closeout requires operator_decision_text='REJECT: <reason>'"
            )
        return self
