from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.asset_acquisition import ProductionArchiveManifest


IMG_CANARY_V3_CLOSEOUT_RUN_ID = "img-canary-v3-20260718T162027Z-a90959ed"
IMG_CANARY_V3_HUMAN_REVIEW_ROLE = "IMG_CANARY_V3_HUMAN_REVIEW_RECEIPT"
IMG_CANARY_V3_HUMAN_REVIEW_ARCHIVE_PATH = "06-qc/human-review-receipt.json"
IMG_CANARY_V3_ORIGINAL_MANIFEST_ROLE = "IMG_CANARY_V3_ORIGINAL_ARCHIVE_MANIFEST"
IMG_CANARY_V3_ORIGINAL_MANIFEST_ARCHIVE_PATH = (
    "00-manifests/original-production-archive-manifest.json"
)
IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE = "IMG_CANARY_V3_DRIVE_EXPORT_CLOSEOUT_MANIFEST"
IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH = (
    "00-manifests/drive-export-closeout-manifest.json"
)


class IMGCanaryV3HumanReviewReceipt(BaseModel):
    schema_version: Literal["img-canary-v3-human-review-receipt/v1.0.0"] = (
        "img-canary-v3-human-review-receipt/v1.0.0"
    )
    run_id: Literal[IMG_CANARY_V3_CLOSEOUT_RUN_ID]
    decision: Literal["PASS"]
    decision_source: Literal["OPERATOR"]
    human_review_authority: Literal["OPERATOR"]
    decision_source_ref: str
    reviewed_original_image_path: str
    reviewed_original_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_normalized_image_path: str
    reviewed_normalized_image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_mp4_path: str
    reviewed_mp4_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_archive_manifest_ref: str
    original_archive_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_archive_manifest_declared_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_hash_discrepancy_reason_codes: list[str]
    decision_timestamp: datetime
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_operator_authority(self):
        if self.decision_source_ref != "operator-prompt:img-canary-v3-drive-closeout":
            raise ValueError("IMG_CANARY_V3_HUMAN_REVIEW_SOURCE_INVALID")
        if self.decision_timestamp.tzinfo is None:
            raise ValueError("IMG_CANARY_V3_HUMAN_REVIEW_TIMESTAMP_NAIVE")
        return self


class IMGCanaryV3DriveExportItem(BaseModel):
    logical_role: str
    source_path: str
    expected_archive_path: str
    size_bytes: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    self_reference_without_checksum: bool = False
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_checksum_policy(self):
        if self.self_reference_without_checksum:
            if (
                self.logical_role != IMG_CANARY_V3_CLOSEOUT_MANIFEST_ROLE
                or self.expected_archive_path != IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH
                or self.size_bytes is not None
                or self.sha256 is not None
            ):
                raise ValueError("IMG_CANARY_V3_CLOSEOUT_SELF_REFERENCE_INVALID")
        elif self.size_bytes is None or self.sha256 is None:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_CHECKSUM_REQUIRED")
        return self


class IMGCanaryV3DriveExportCloseoutManifest(ProductionArchiveManifest):
    schema_version: Literal["img-canary-v3-drive-export-closeout/v1.0.0"] = (
        "img-canary-v3-drive-export-closeout/v1.0.0"
    )
    run_id: Literal[IMG_CANARY_V3_CLOSEOUT_RUN_ID]
    original_manifest_ref: str
    original_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    original_manifest_declared_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_review_receipt_ref: str
    human_review_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_review_receipt_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    export_item_count: int = Field(ge=1)
    export_items: list[IMGCanaryV3DriveExportItem]
    drive_destination_folder: str
    archive_identity: str
    upload_idempotency_key: str
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    correction_reason_codes: list[str]
    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_export_shape(self):
        if self.provider_execution_allowed:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_PROVIDER_EXECUTION_FORBIDDEN")
        if not self.required_roles_complete:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_REQUIRED_ROLES_INCOMPLETE")
        if self.export_item_count != len(self.export_items):
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_COUNT_MISMATCH")
        roles = [item.logical_role for item in self.export_items]
        paths = [item.expected_archive_path for item in self.export_items]
        if len(roles) != len(set(roles)) or len(paths) != len(set(paths)):
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_DUPLICATE")
        self_items = [item for item in self.export_items if item.self_reference_without_checksum]
        if len(self_items) != 1:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_SELF_REFERENCE_REQUIRED")
        if any(
            item.expected_archive_path == IMG_CANARY_V3_CLOSEOUT_MANIFEST_ARCHIVE_PATH
            for item in self.files
        ):
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_MANIFEST_IN_FILE_SET")
        file_paths = {item.expected_archive_path for item in self.files}
        export_nonself_paths = {
            item.expected_archive_path
            for item in self.export_items
            if not item.self_reference_without_checksum
        }
        if file_paths != export_nonself_paths:
            raise ValueError("IMG_CANARY_V3_CLOSEOUT_EXPORT_FILE_SET_MISMATCH")
        return self
