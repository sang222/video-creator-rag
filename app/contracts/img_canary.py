from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.ai_image import ai_image_stable_hash
from app.contracts.img_canary_security import (
    IMG_CANARY_V2_AUTHORIZATION_REF,
    IMG_CANARY_V2_TASK_KEY,
    IMG_CANARY_V3_AUTHORIZATION_REF,
    IMG_CANARY_V3_TASK_KEY,
    IMGCanaryBudgetReservationEvidence,
    IMGCanaryCredentialRotationEvidence,
    IMGCanaryTaskAuthorizationLedger,
)


IMG_CANARY_PROVIDER = "google_gemini_image"
IMG_CANARY_MODEL = "gemini-3.1-flash-image"
IMG_CANARY_IMAGE_SIZE = "2K"
IMG_CANARY_ASPECT_RATIO = "16:9"
IMG_CANARY_HARD_CAP_USD = Decimal("0.15")


class IMGCanaryRunIdentity(BaseModel):
    run_id: str = Field(
        pattern=r"^img-canary(?:-v[23])?-\d{8}T\d{6}Z-[0-9a-f]{8}$"
    )
    run_type: Literal["IMG_CANARY"] = "IMG_CANARY"
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    canary_id: str = Field(min_length=1)
    channel_key: Literal["small-team-ai"] = "small-team-ai"
    niche_visual_source_profile: Literal["STOCK_ASSISTED"] = "STOCK_ASSISTED"
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    created_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_hash(self) -> "IMGCanaryRunIdentity":
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_RUN_IDENTITY_HASH_MISMATCH")
        return self


class IMGCanaryNativeHeadlineArtifact(BaseModel):
    artifact_ref: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    content_kind: Literal["HEADLINE"] = "HEADLINE"
    exact_text: Literal["Information is everywhere. Context is nowhere."] = (
        "Information is everywhere. Context is nowhere."
    )
    authority: Literal["NATIVE_OVERLAY"] = "NATIVE_OVERLAY"
    generated_pixel_authority: Literal[False] = False
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_hash(self) -> "IMGCanaryNativeHeadlineArtifact":
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_NATIVE_HEADLINE_HASH_MISMATCH")
        return self


class IMGCanaryScopedApproval(BaseModel):
    approval_ref: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    canary_id: str = Field(min_length=1)
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    model: Literal["gemini-3.1-flash-image"] = "gemini-3.1-flash-image"
    image_size: Literal["2K"] = "2K"
    aspect_ratio: Literal["16:9"] = "16:9"
    output_count: Literal[1] = 1
    request_hash: str = Field(min_length=64, max_length=64)
    prompt_hash: str = Field(min_length=64, max_length=64)
    visual_source_decision_hash: str = Field(min_length=64, max_length=64)
    base_decision_status: Literal["PLANNED"] = "PLANNED"
    base_provider_execution_allowed: Literal[False] = False
    scoped_provider_boundary_authorized: Literal[True] = True
    catalog_ref: str = Field(min_length=1)
    estimated_cost_usd: Decimal = Field(gt=0)
    hard_cap_usd: Decimal = Field(gt=0)
    attempt_limit: Literal[1] = 1
    reference_image_count: Literal[0] = 0
    grounding_enabled: Literal[False] = False
    search_grounding_enabled: Literal[False] = False
    authorized_at: datetime
    expires_at: datetime
    operator_authorization_source: Literal[
        "ATTACHED_MASTER_PROMPT", "CODEX_OPERATOR_MESSAGE"
    ] = "ATTACHED_MASTER_PROMPT"
    external_fallback_allowed: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_scope(self) -> "IMGCanaryScopedApproval":
        if self.estimated_cost_usd > self.hard_cap_usd or self.hard_cap_usd != IMG_CANARY_HARD_CAP_USD:
            raise ValueError("IMG_CANARY_APPROVAL_COST_CAP_EXCEEDED")
        if self.expires_at <= self.authorized_at:
            raise ValueError("IMG_CANARY_APPROVAL_EXPIRY_INVALID")
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_APPROVAL_HASH_MISMATCH")
        return self


class IMGCanarySerializedRequestEvidence(BaseModel):
    schema_version: Literal["img-canary-serialized-request/v2"] = (
        "img-canary-serialized-request/v2"
    )
    run_id: str = Field(pattern=r"^img-canary-v2-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport: Literal["OFFICIAL_SDK_HTTPX_MOCK_TRANSPORT"] = (
        "OFFICIAL_SDK_HTTPX_MOCK_TRANSPORT"
    )
    endpoint_path: Literal["/v1beta/interactions"] = "/v1beta/interactions"
    http_method: Literal["POST"] = "POST"
    redacted_request_body: dict[str, Any]
    serialized_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    redacted_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_format_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdk_retry_attempts: Literal[1] = 1
    sdk_retries_disabled: Literal[True] = True
    api_key_persisted: Literal[False] = False
    authorization_headers_persisted: Literal[False] = False
    captured_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_serialized_body(self) -> "IMGCanarySerializedRequestEvidence":
        if self.captured_at.tzinfo is None:
            raise ValueError("IMG_CANARY_SERIALIZED_REQUEST_TIMEZONE_REQUIRED")
        expected_keys = {
            "model",
            "input",
            "stream",
            "store",
            "background",
            "response_format",
        }
        if set(self.redacted_request_body) != expected_keys:
            raise ValueError("IMG_CANARY_SERIALIZED_REQUEST_KEYS_INVALID")
        response_format = self.redacted_request_body.get("response_format")
        if not isinstance(response_format, dict) or response_format != {
            "type": "image",
            "mime_type": "image/jpeg",
            "delivery": "inline",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        }:
            raise ValueError("IMG_CANARY_SERIALIZED_RESPONSE_FORMAT_INVALID")
        if (
            self.redacted_request_body.get("model") != IMG_CANARY_MODEL
            or self.redacted_request_body.get("input")
            != f"sha256://prompt/{self.prompt_hash}"
            or self.redacted_request_body.get("stream") is not False
            or self.redacted_request_body.get("store") is not False
            or self.redacted_request_body.get("background") is not False
            or "response_modalities" in self.redacted_request_body
        ):
            raise ValueError("IMG_CANARY_SERIALIZED_REQUEST_CONTRACT_INVALID")
        if self.redacted_body_hash != ai_image_stable_hash(
            self.redacted_request_body
        ):
            raise ValueError("IMG_CANARY_REDACTED_BODY_HASH_MISMATCH")
        if self.response_format_hash != ai_image_stable_hash(response_format):
            raise ValueError("IMG_CANARY_SERIALIZED_RESPONSE_FORMAT_HASH_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_SERIALIZED_REQUEST_EVIDENCE_HASH_MISMATCH")
        return self


class IMGCanaryV3SerializedRequestEvidence(BaseModel):
    """Offline SDK serialization captured for the corrected V3 request.

    V3 deliberately omits ``delivery``.  It is a separate contract instead of
    widening the V2 model so every already-written V2 artifact remains
    byte-for-byte and hash compatible.
    """

    schema_version: Literal["img-canary-serialized-request/v3"] = (
        "img-canary-serialized-request/v3"
    )
    run_id: str = Field(pattern=r"^img-canary-v3-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    transport: Literal["OFFICIAL_SDK_HTTPX_MOCK_TRANSPORT"] = (
        "OFFICIAL_SDK_HTTPX_MOCK_TRANSPORT"
    )
    endpoint_path: Literal["/v1beta/interactions"] = "/v1beta/interactions"
    http_method: Literal["POST"] = "POST"
    redacted_request_body: dict[str, Any]
    serialized_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    redacted_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_format_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sdk_retry_attempts: Literal[1] = 1
    sdk_retries_disabled: Literal[True] = True
    api_key_persisted: Literal[False] = False
    authorization_headers_persisted: Literal[False] = False
    captured_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_serialized_body(self) -> "IMGCanaryV3SerializedRequestEvidence":
        if self.captured_at.tzinfo is None:
            raise ValueError("IMG_CANARY_V3_SERIALIZED_REQUEST_TIMEZONE_REQUIRED")
        expected_keys = {
            "model",
            "input",
            "stream",
            "store",
            "background",
            "response_format",
        }
        if set(self.redacted_request_body) != expected_keys:
            raise ValueError("IMG_CANARY_V3_SERIALIZED_REQUEST_KEYS_INVALID")
        response_format = self.redacted_request_body.get("response_format")
        if not isinstance(response_format, dict) or response_format != {
            "type": "image",
            "mime_type": "image/jpeg",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        }:
            raise ValueError("IMG_CANARY_V3_SERIALIZED_RESPONSE_FORMAT_INVALID")
        if (
            self.redacted_request_body.get("model") != IMG_CANARY_MODEL
            or self.redacted_request_body.get("input")
            != f"sha256://prompt/{self.prompt_hash}"
            or self.redacted_request_body.get("stream") is not False
            or self.redacted_request_body.get("store") is not False
            or self.redacted_request_body.get("background") is not False
            or "response_modalities" in self.redacted_request_body
            or "delivery" in response_format
        ):
            raise ValueError("IMG_CANARY_V3_SERIALIZED_REQUEST_CONTRACT_INVALID")
        if self.redacted_body_hash != ai_image_stable_hash(
            self.redacted_request_body
        ):
            raise ValueError("IMG_CANARY_V3_REDACTED_BODY_HASH_MISMATCH")
        if self.response_format_hash != ai_image_stable_hash(response_format):
            raise ValueError("IMG_CANARY_V3_RESPONSE_FORMAT_HASH_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_V3_SERIALIZED_EVIDENCE_HASH_MISMATCH")
        return self


class IMGCanaryV2ApprovalBinding(BaseModel):
    schema_version: Literal["img-canary-v2-approval-binding/v1"] = (
        "img-canary-v2-approval-binding/v1"
    )
    approval_source_ref: Literal[
        "attachment://d6de1eab-f9bd-44fe-ab23-4bf7e05ce167"
    ] = "attachment://d6de1eab-f9bd-44fe-ab23-4bf7e05ce167"
    approval_source_sha256: Literal[
        "6261dfc83261e6470d6a1e0755e827880e57261c8791851b20812267b84e3319"
    ] = "6261dfc83261e6470d6a1e0755e827880e57261c8791851b20812267b84e3319"
    run_id: str = Field(pattern=r"^img-canary-v2-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    task_key: Literal[IMG_CANARY_V2_TASK_KEY] = IMG_CANARY_V2_TASK_KEY
    task_authorization_ref: Literal[IMG_CANARY_V2_AUTHORIZATION_REF] = (
        IMG_CANARY_V2_AUTHORIZATION_REF
    )
    base_approval_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    serialized_request_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    serialized_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_run_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    model: Literal["gemini-3.1-flash-image"] = "gemini-3.1-flash-image"
    image_size: Literal["2K"] = "2K"
    aspect_ratio: Literal["16:9"] = "16:9"
    output_count: Literal[1] = 1
    estimated_cost_usd: Decimal = Field(default=Decimal("0.101"), gt=0)
    hard_cap_usd: Decimal = Field(default=Decimal("0.15"), gt=0)
    attempt_limit: Literal[1] = 1
    external_fallback_allowed: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    authorized_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_binding(self) -> "IMGCanaryV2ApprovalBinding":
        if self.authorized_at.tzinfo is None:
            raise ValueError("IMG_CANARY_V2_APPROVAL_TIMEZONE_REQUIRED")
        if (
            self.estimated_cost_usd != Decimal("0.101")
            or self.hard_cap_usd != Decimal("0.15")
        ):
            raise ValueError("IMG_CANARY_V2_APPROVAL_COST_BINDING_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_V2_APPROVAL_BINDING_HASH_MISMATCH")
        return self


class IMGCanaryV3ApprovalBinding(BaseModel):
    schema_version: Literal["img-canary-v3-approval-binding/v1"] = (
        "img-canary-v3-approval-binding/v1"
    )
    approval_source_ref: Literal[
        "operator-message://codex-thread/2026-07-18/fix-and-rerun"
    ] = "operator-message://codex-thread/2026-07-18/fix-and-rerun"
    approval_source_sha256: Literal[
        "3c895af877e10f7faa7db9fd2ad92752cb43305c13ce7d078cb1adfa077e9ada"
    ] = "3c895af877e10f7faa7db9fd2ad92752cb43305c13ce7d078cb1adfa077e9ada"
    approval_id: Literal["operator-3c895af877e10f7f"] = (
        "operator-3c895af877e10f7f"
    )
    run_id: str = Field(pattern=r"^img-canary-v3-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    task_key: Literal[IMG_CANARY_V3_TASK_KEY] = IMG_CANARY_V3_TASK_KEY
    task_authorization_ref: Literal[IMG_CANARY_V3_AUTHORIZATION_REF] = (
        IMG_CANARY_V3_AUTHORIZATION_REF
    )
    base_approval_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    serialized_request_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    serialized_body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_runs_evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    model: Literal["gemini-3.1-flash-image"] = "gemini-3.1-flash-image"
    image_size: Literal["2K"] = "2K"
    aspect_ratio: Literal["16:9"] = "16:9"
    output_count: Literal[1] = 1
    estimated_cost_usd: Decimal = Field(default=Decimal("0.101"), gt=0)
    hard_cap_usd: Decimal = Field(default=Decimal("0.15"), gt=0)
    attempt_limit: Literal[1] = 1
    external_fallback_allowed: Literal[False] = False
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    authorized_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_binding(self) -> "IMGCanaryV3ApprovalBinding":
        if self.authorized_at.tzinfo is None:
            raise ValueError("IMG_CANARY_V3_APPROVAL_TIMEZONE_REQUIRED")
        if (
            self.estimated_cost_usd != Decimal("0.101")
            or self.hard_cap_usd != Decimal("0.15")
        ):
            raise ValueError("IMG_CANARY_V3_APPROVAL_COST_BINDING_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_V3_APPROVAL_BINDING_HASH_MISMATCH")
        return self


class IMGCanaryPreviousRunImmutabilityEvidence(BaseModel):
    schema_version: Literal["img-canary-v2-previous-run-evidence/v1"] = (
        "img-canary-v2-previous-run-evidence/v1"
    )
    previous_run_id: Literal["img-canary-20260718T075252Z-319bacb0"] = (
        "img-canary-20260718T075252Z-319bacb0"
    )
    file_count: Literal[24] = 24
    file_sha256_by_relative_path: dict[str, str]
    aggregate_sha256: Literal[
        "6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423"
    ] = "6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423"
    task_authority_file_sha256: Literal[
        "6c115ed2ead3a6a730a26edc775dd68aae91e82dc54ef67661482d9d85c9c440"
    ] = "6c115ed2ead3a6a730a26edc775dd68aae91e82dc54ef67661482d9d85c9c440"
    attempts_consumed: Literal[1] = 1
    task_authorization_status: Literal["CONSUMED"] = "CONSUMED"
    task_completion_status: Literal["PROVIDER_ATTEMPT_FAILED"] = (
        "PROVIDER_ATTEMPT_FAILED"
    )
    provider_status: Literal["NATIVE_SUBMIT_FAILED"] = "NATIVE_SUBMIT_FAILED"
    provider_error_code: Literal["GEMINI_IMAGE_PROVIDER_HTTP_400"] = (
        "GEMINI_IMAGE_PROVIDER_HTTP_400"
    )
    provider_output_count: Literal[0] = 0
    external_fallback_used: Literal[False] = False
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_evidence(self) -> "IMGCanaryPreviousRunImmutabilityEvidence":
        if self.captured_at.tzinfo is None:
            raise ValueError("IMG_CANARY_PREVIOUS_RUN_EVIDENCE_TIMEZONE_REQUIRED")
        if len(self.file_sha256_by_relative_path) != self.file_count or any(
            len(value) != 64 for value in self.file_sha256_by_relative_path.values()
        ):
            raise ValueError("IMG_CANARY_PREVIOUS_RUN_FILE_SET_INVALID")
        stable_payload = self.model_dump(
            mode="json",
            exclude={"content_hash", "captured_at"},
        )
        stable_payload.pop("evidence_hash", None)
        if self.evidence_hash != ai_image_stable_hash(stable_payload):
            raise ValueError("IMG_CANARY_PREVIOUS_RUN_EVIDENCE_HASH_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_PREVIOUS_RUN_CONTENT_HASH_MISMATCH")
        return self


class IMGCanaryTerminalRunImmutabilitySnapshot(BaseModel):
    """Stable terminal facts for one historical paid canary run."""

    run_id: str = Field(
        pattern=r"^img-canary(?:-v2)?-\d{8}T\d{6}Z-[0-9a-f]{8}$"
    )
    file_count: int = Field(gt=0)
    file_sha256_by_relative_path: dict[str, str]
    aggregate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_authority_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempts_consumed: Literal[1] = 1
    task_authorization_status: Literal["CONSUMED"] = "CONSUMED"
    task_completion_status: Literal[
        "PROVIDER_ATTEMPT_FAILED", "PROVIDER_ATTEMPT_SUBMITTED"
    ]
    provider_status: Literal["NATIVE_SUBMIT_FAILED"] = "NATIVE_SUBMIT_FAILED"
    provider_error_code: Literal["GEMINI_IMAGE_PROVIDER_HTTP_400"] = (
        "GEMINI_IMAGE_PROVIDER_HTTP_400"
    )
    provider_output_count: Literal[0] = 0
    external_fallback_used: Literal[False] = False
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_snapshot(self) -> "IMGCanaryTerminalRunImmutabilitySnapshot":
        if len(self.file_sha256_by_relative_path) != self.file_count or any(
            not isinstance(value, str) or len(value) != 64
            for value in self.file_sha256_by_relative_path.values()
        ):
            raise ValueError("IMG_CANARY_TERMINAL_RUN_FILE_SET_INVALID")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"snapshot_hash"})
        )
        if self.snapshot_hash != expected:
            raise ValueError("IMG_CANARY_TERMINAL_RUN_SNAPSHOT_HASH_MISMATCH")
        return self


class IMGCanaryPreviousRunsImmutabilityEvidence(BaseModel):
    """V3 binding over the complete, terminal V1 and V2 artifact trees."""

    schema_version: Literal["img-canary-v3-previous-runs-evidence/v1"] = (
        "img-canary-v3-previous-runs-evidence/v1"
    )
    v1_terminal_run: IMGCanaryTerminalRunImmutabilitySnapshot
    v2_terminal_run: IMGCanaryTerminalRunImmutabilitySnapshot
    evidence_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    captured_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    def evidence_hash_payload(self) -> dict[str, Any]:
        return self.model_dump(
            mode="json",
            exclude={"evidence_hash", "captured_at", "content_hash"},
        )

    @model_validator(mode="after")
    def validate_evidence(self) -> "IMGCanaryPreviousRunsImmutabilityEvidence":
        if self.captured_at.tzinfo is None:
            raise ValueError("IMG_CANARY_PREVIOUS_RUNS_TIMEZONE_REQUIRED")
        v1 = self.v1_terminal_run
        v2 = self.v2_terminal_run
        if (
            v1.run_id != "img-canary-20260718T075252Z-319bacb0"
            or v1.file_count != 24
            or v1.aggregate_sha256
            != "6ea77966c51b012e09430c88e9f3c91d630ea4de67cbc87a54aa1ec1ab13f423"
            or v1.task_authority_file_sha256
            != "6c115ed2ead3a6a730a26edc775dd68aae91e82dc54ef67661482d9d85c9c440"
            or v1.task_completion_status != "PROVIDER_ATTEMPT_FAILED"
            or v2.run_id != "img-canary-v2-20260718T091203Z-cce118a4"
            or v2.file_count != 28
            or v2.aggregate_sha256
            != "7528b4c0fcbcb523174d158e6e2e760ba14409d8d05a3df0a330daa990b22603"
            or v2.task_authority_file_sha256
            != "88bdf88d881b6cbe8d1ee0428344d871053255686b0ff999008ae937bb884b36"
            or v2.task_completion_status != "PROVIDER_ATTEMPT_SUBMITTED"
        ):
            raise ValueError("IMG_CANARY_PREVIOUS_RUNS_TERMINAL_STATE_MISMATCH")
        if self.evidence_hash != ai_image_stable_hash(self.evidence_hash_payload()):
            raise ValueError("IMG_CANARY_PREVIOUS_RUNS_EVIDENCE_HASH_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_PREVIOUS_RUNS_CONTENT_HASH_MISMATCH")
        return self


class IMGCanaryDriveReadinessEvidence(BaseModel):
    schema_version: Literal[
        "img-canary-v2-drive-readiness/v1",
        "img-canary-v3-drive-readiness/v1",
    ] = (
        "img-canary-v2-drive-readiness/v1"
    )
    run_id: str = Field(pattern=r"^img-canary-v[23]-\d{8}T\d{6}Z-[0-9a-f]{8}$")
    status: Literal["PASS"] = "PASS"
    root_folder_id: str = Field(min_length=1)
    root_folder_mime_type: Literal["application/vnd.google-apps.folder"] = (
        "application/vnd.google-apps.folder"
    )
    oauth_access_token_persisted: Literal[False] = False
    raw_drive_response_persisted: Literal[False] = False
    checked_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_evidence(self) -> "IMGCanaryDriveReadinessEvidence":
        if self.checked_at.tzinfo is None:
            raise ValueError("IMG_CANARY_DRIVE_READINESS_TIMEZONE_REQUIRED")
        expected_version = "v3" if self.run_id.startswith("img-canary-v3-") else "v2"
        if self.schema_version != f"img-canary-{expected_version}-drive-readiness/v1":
            raise ValueError("IMG_CANARY_DRIVE_READINESS_VERSION_MISMATCH")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_DRIVE_READINESS_HASH_MISMATCH")
        return self


class IMGCanaryAttemptLedger(BaseModel):
    run_id: str = Field(min_length=1)
    request_fingerprint: str = Field(min_length=64, max_length=64)
    idempotency_key_hash: str = Field(min_length=64, max_length=64)
    attempt_limit: Literal[1] = 1
    attempts_consumed: int = Field(ge=0, le=1)
    status: Literal[
        "PLANNED",
        "EXECUTING",
        "SUCCEEDED",
        "FAILED",
        "BLOCKED_REQUIRES_NEW_APPROVAL",
    ]
    provider_call_made: bool
    provider_request_id_ref: str | None = None
    provider_operation_id_ref: str | None = None
    failure_reason_code: str | None = None
    created_at: datetime
    updated_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_attempt_state(self) -> "IMGCanaryAttemptLedger":
        if self.provider_call_made != (self.attempts_consumed == 1):
            raise ValueError("IMG_CANARY_ATTEMPT_LEDGER_CALL_COUNT_MISMATCH")
        if self.status == "PLANNED" and (self.attempts_consumed or self.provider_call_made):
            raise ValueError("IMG_CANARY_PLANNED_LEDGER_ALREADY_CONSUMED")
        if self.status != "PLANNED" and self.attempts_consumed != 1:
            raise ValueError("IMG_CANARY_NON_PLANNED_LEDGER_MUST_CONSUME_ATTEMPT")
        if self.updated_at < self.created_at:
            raise ValueError("IMG_CANARY_ATTEMPT_LEDGER_TIME_INVALID")
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_ATTEMPT_LEDGER_HASH_MISMATCH")
        return self


class IMGCanaryMonthlyBudgetEvidence(BaseModel):
    """Durable, run-scoped reservation evidence for the single paid request."""

    run_id: str = Field(min_length=1)
    channel_key: Literal["small-team-ai"] = "small-team-ai"
    billing_period: str = Field(pattern=r"^\d{4}-\d{2}$")
    budget_ref: str = Field(min_length=1)
    monthly_cap_usd: Decimal = Field(ge=0)
    spent_usd: Decimal = Field(ge=0)
    prior_reservations_usd: Decimal = Field(ge=0)
    available_before_usd: Decimal = Field(ge=0)
    request_estimate_usd: Decimal = Field(gt=0, le=IMG_CANARY_HARD_CAP_USD)
    reservation_status: Literal["RESERVED", "INSUFFICIENT", "UNVERIFIED"]
    reservation_ref: str | None = None
    checked_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_budget_reservation(self) -> "IMGCanaryMonthlyBudgetEvidence":
        if self.checked_at.tzinfo is None:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_TIMEZONE_REQUIRED")
        calculated_available = max(
            Decimal("0"),
            self.monthly_cap_usd - self.spent_usd - self.prior_reservations_usd,
        )
        if self.available_before_usd != calculated_available:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_AVAILABLE_MISMATCH")
        sufficient = calculated_available >= self.request_estimate_usd
        if self.reservation_status == "RESERVED":
            if not sufficient or not self.reservation_ref:
                raise ValueError("IMG_CANARY_MONTHLY_BUDGET_RESERVATION_INVALID")
        elif self.reservation_ref is not None:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_UNRESERVED_HAS_REF")
        elif self.reservation_status == "INSUFFICIENT" and sufficient:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_INSUFFICIENT_STATUS_INVALID")
        elif self.reservation_status == "UNVERIFIED" and not sufficient:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_UNVERIFIED_STATUS_INVALID")
        expected = ai_image_stable_hash(
            self.model_dump(mode="json", exclude={"content_hash"})
        )
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_MONTHLY_BUDGET_HASH_MISMATCH")
        return self


class IMGCanaryPreflightEvidence(BaseModel):
    run_id: str = Field(min_length=1)
    status: Literal["PASS", "BLOCKED"]
    repository_identity_passed: bool
    worktree_reviewed: bool
    vqc1_final_passed: bool
    credential_configured: bool
    credential_safe_for_use: bool
    credential_rotation_evidence: IMGCanaryCredentialRotationEvidence
    route_registered: bool
    model_catalog_present: bool
    model_locked: bool
    image_size_locked: bool
    aspect_ratio_locked: bool
    output_count_locked: bool
    reference_images_empty: bool
    grounding_disabled: bool
    raster_decoder_ready: bool | None = None
    serialized_request_contract_passed: bool | None = None
    v2_approval_binding_passed: bool | None = None
    v3_approval_binding_passed: bool | None = None
    drive_readiness_passed: bool | None = None
    provider_boundary_passed: bool
    cost_estimate_passed: bool
    paid_authorization_passed: bool
    monthly_budget_passed: bool
    task_authorization_passed: bool
    attempt_limit_passed: bool
    idempotency_passed: bool
    global_kill_switch_scoped_open: bool
    provider_kill_switch_scoped_open: bool
    defaults_remain_disabled: bool
    monthly_budget_evidence: IMGCanaryBudgetReservationEvidence
    task_authorization_evidence: IMGCanaryTaskAuthorizationLedger
    production_database_mutation_required: Literal[False] = False
    blocker_reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: dict[str, str] = Field(default_factory=dict)
    checked_at: datetime
    approval_expires_at: datetime
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @staticmethod
    def raster_decoder_evidence_hash(*, ready: bool) -> str:
        return ai_image_stable_hash(
            {
                "required_output_mime_type": "image/jpeg",
                "mjpeg_decoder_ready": ready,
            }
        )

    def content_hash_payload(self) -> dict[str, Any]:
        """Return the strict hash variant used by this immutable artifact.

        Historical artifacts omitted optional fields that did not yet exist,
        while some local constructors hash a current Pydantic model whose dump
        includes those fields as explicit ``None`` values.  Enumerate only
        those representation-compatible variants and select the one already
        committed by ``content_hash``; semantic values are never changed.
        """

        optional_none_fields = [
            name
            for name in (
                "raster_decoder_ready",
                "serialized_request_contract_passed",
                "v2_approval_binding_passed",
                "v3_approval_binding_passed",
                "drive_readiness_passed",
            )
            if getattr(self, name) is None
        ]
        nested_optional_fields = (
            "approval_version",
            "approved_run_id",
            "approved_request_fingerprint",
            "approved_prompt_hash",
            "approved_serialized_body_hash",
            "approved_scoped_approval_hash",
        )
        variant_count = 1 << len(optional_none_fields)
        for mask in range(variant_count):
            for strip_nested_task_fields in (False, True):
                payload = self.model_dump(mode="json", exclude={"content_hash"})
                for index, name in enumerate(optional_none_fields):
                    if mask & (1 << index):
                        payload.pop(name, None)
                task_authorization = payload.get("task_authorization_evidence")
                if (
                    strip_nested_task_fields
                    and isinstance(task_authorization, dict)
                    and task_authorization.get("approval_version") is None
                ):
                    for name in nested_optional_fields:
                        task_authorization.pop(name, None)
                if self.content_hash == ai_image_stable_hash(payload):
                    return payload

        # Invalid hashes still compare against one deterministic canonical
        # variant in the validator below, preserving fail-closed behavior.
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        for name in optional_none_fields:
            payload.pop(name, None)
        task_authorization = payload.get("task_authorization_evidence")
        if (
            isinstance(task_authorization, dict)
            and task_authorization.get("approval_version") is None
        ):
            for name in nested_optional_fields:
                task_authorization.pop(name, None)
        return payload

    @model_validator(mode="after")
    def validate_preflight(self) -> "IMGCanaryPreflightEvidence":
        if self.checked_at.tzinfo is None or self.approval_expires_at.tzinfo is None:
            raise ValueError("IMG_CANARY_PREFLIGHT_TIMEZONE_REQUIRED")
        if self.approval_expires_at <= self.checked_at:
            raise ValueError("IMG_CANARY_PREFLIGHT_APPROVAL_EXPIRED_AT_CHECK")
        if self.monthly_budget_evidence.run_id != self.run_id:
            raise ValueError("IMG_CANARY_PREFLIGHT_MONTHLY_BUDGET_RUN_MISMATCH")
        expected_budget_pass = self.monthly_budget_evidence.status in {
            "AVAILABLE_UNRESERVED",
            "RESERVED",
            "ALREADY_RESERVED",
        }
        if self.monthly_budget_passed != expected_budget_pass:
            raise ValueError("IMG_CANARY_PREFLIGHT_MONTHLY_BUDGET_STATUS_MISMATCH")
        if self.credential_configured != self.credential_rotation_evidence.credential_configured:
            raise ValueError("IMG_CANARY_PREFLIGHT_CREDENTIAL_CONFIGURATION_MISMATCH")
        if self.credential_safe_for_use != (
            self.credential_rotation_evidence.status == "PASS"
        ):
            raise ValueError("IMG_CANARY_PREFLIGHT_CREDENTIAL_ROTATION_MISMATCH")
        task_authorization = self.task_authorization_evidence
        runtime_claim_bound = bool(
            task_authorization.status == "CLAIMED"
            and task_authorization.claimed_run_id == self.run_id
            and task_authorization.claimed_request_fingerprint
            == self.monthly_budget_evidence.request_fingerprint
            and self.monthly_budget_evidence.status
            in {"RESERVED", "ALREADY_RESERVED"}
            and self.monthly_budget_evidence.reservation_ref
        )
        expected_task_authorization_pass = bool(
            task_authorization.status == "AVAILABLE" or runtime_claim_bound
        )
        if self.task_authorization_passed != expected_task_authorization_pass:
            raise ValueError("IMG_CANARY_PREFLIGHT_TASK_AUTHORIZATION_MISMATCH")
        checks = [
            value
            for name, value in self.model_dump().items()
            if value is not None
            and (
                name.endswith("_passed")
                or name.endswith("_configured")
                or name.endswith("_reviewed")
                or name.endswith("_registered")
                or name.endswith("_present")
                or name.endswith("_locked")
                or name.endswith("_empty")
                or name.endswith("_disabled")
                or name.endswith("_open")
                or name == "credential_safe_for_use"
            )
        ]
        # Preserve validation of legacy preflight artifacts written before the
        # one-shot JPEG decoder gate existed, while requiring every newly
        # emitted artifact to include and satisfy the typed gate.
        if self.raster_decoder_ready is not None:
            checks.append(self.raster_decoder_ready)
        for value in (
            self.serialized_request_contract_passed,
            self.v2_approval_binding_passed,
            self.v3_approval_binding_passed,
            self.drive_readiness_passed,
        ):
            if value is not None:
                checks.append(value)
        expected_status = "PASS" if checks and all(checks) else "BLOCKED"
        if self.status != expected_status:
            raise ValueError("IMG_CANARY_PREFLIGHT_STATUS_MISMATCH")
        if self.status == "PASS" and self.blocker_reason_codes:
            raise ValueError("IMG_CANARY_PREFLIGHT_PASS_HAS_BLOCKERS")
        if self.status == "BLOCKED" and not self.blocker_reason_codes:
            raise ValueError("IMG_CANARY_PREFLIGHT_BLOCKER_REQUIRED")
        if self.raster_decoder_ready is not None and self.evidence_refs.get(
            "raster_decoder_readiness"
        ) != self.raster_decoder_evidence_hash(ready=self.raster_decoder_ready):
            raise ValueError("IMG_CANARY_PREFLIGHT_DECODER_EVIDENCE_MISMATCH")
        expected = ai_image_stable_hash(self.content_hash_payload())
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_PREFLIGHT_HASH_MISMATCH")
        return self


class IMGCanaryProviderResponseSummary(BaseModel):
    run_id: str = Field(min_length=1)
    provider: Literal["google_gemini_image"] = "google_gemini_image"
    model: Literal["gemini-3.1-flash-image"] = "gemini-3.1-flash-image"
    provider_status: str = Field(min_length=1)
    provider_request_id_ref: str | None = None
    provider_operation_id_ref: str | None = None
    submitted_at: datetime
    completed_at: datetime | None = None
    output_count: int = Field(ge=0, le=1)
    output_checksum: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    image_width: int | None = Field(default=None, gt=0)
    image_height: int | None = Field(default=None, gt=0)
    image_format: Literal["PNG", "JPEG"] | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    usage_metadata: dict[str, Any] = Field(default_factory=dict)
    estimated_cost_usd: Decimal = Field(ge=0, le=IMG_CANARY_HARD_CAP_USD)
    actual_cost_usd: Decimal | None = Field(default=None, ge=0, le=IMG_CANARY_HARD_CAP_USD)
    provider_attempts_consumed: Literal[1] = 1
    raw_response_persisted: Literal[False] = False
    raw_image_bytes_persisted_in_manifest: Literal[False] = False
    raw_url_persisted: Literal[False] = False
    api_key_persisted: Literal[False] = False
    external_fallback_used: Literal[False] = False
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_hash(self) -> "IMGCanaryProviderResponseSummary":
        if self.submitted_at.tzinfo is None or (
            self.completed_at is not None and self.completed_at.tzinfo is None
        ):
            raise ValueError("IMG_CANARY_PROVIDER_RESPONSE_TIMEZONE_REQUIRED")
        if self.completed_at is not None and self.completed_at < self.submitted_at:
            raise ValueError("IMG_CANARY_PROVIDER_RESPONSE_TIME_INVALID")
        output_fields = (
            self.output_checksum,
            self.image_width,
            self.image_height,
            self.image_format,
            self.size_bytes,
        )
        if self.output_count == 1:
            if (
                self.provider_status != "INTERACTION_COMPLETED"
                or self.completed_at is None
                or any(value is None for value in output_fields)
            ):
                raise ValueError("IMG_CANARY_PROVIDER_SUCCESS_OUTPUT_EVIDENCE_INCOMPLETE")
        elif any(value is not None for value in output_fields):
            raise ValueError("IMG_CANARY_PROVIDER_FAILURE_HAS_OUTPUT_EVIDENCE")
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_PROVIDER_RESPONSE_HASH_MISMATCH")
        return self


IMG_CANARY_REVIEW_CHECKLIST = (
    "fragmented_knowledge_metaphor_clear",
    "no_fake_text_numbers_logos_or_ui",
    "small_team_ai_visual_language_match",
    "native_headline_readable_and_positioned",
    "crop_and_motion_preserve_focal_composition",
    "authored_not_generic_ai_filler",
    "acceptable_production_visual_source_pattern",
)


class IMGCanaryHumanReviewPacket(BaseModel):
    run_id: str = Field(min_length=1)
    review_state: Literal["PENDING"] = "PENDING"
    original_image_path: str = Field(min_length=1)
    normalized_image_path: str = Field(min_length=1)
    review_mp4_path: str = Field(min_length=1)
    drive_archive_receipt_ref: str = Field(min_length=1)
    drive_archive_receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    drive_archive_manifest_ref: str = Field(min_length=1)
    archive_verified: Literal[True] = True
    drive_provider_call_made: Literal[True] = True
    provider_attempts_consumed: Literal[1] = 1
    estimated_cost_usd: Decimal = Field(ge=0, le=IMG_CANARY_HARD_CAP_USD)
    actual_cost_usd: Decimal | None = Field(default=None, ge=0, le=IMG_CANARY_HARD_CAP_USD)
    checklist: dict[str, Literal[False]]
    generated_artifact_ambiguities: list[str] = Field(default_factory=list)
    production_eligible: Literal[False] = False
    not_publishable: Literal[True] = True
    proceed_to_ch1_flex_v2: Literal[False] = False
    content_hash: str = Field(min_length=64, max_length=64)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_pending_boundary(self) -> "IMGCanaryHumanReviewPacket":
        if set(self.checklist) != set(IMG_CANARY_REVIEW_CHECKLIST) or any(self.checklist.values()):
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_CHECKLIST_INVALID")
        expected = ai_image_stable_hash(self.model_dump(mode="json", exclude={"content_hash"}))
        if self.content_hash != expected:
            raise ValueError("IMG_CANARY_HUMAN_REVIEW_PACKET_HASH_MISMATCH")
        return self


__all__ = [
    "IMG_CANARY_ASPECT_RATIO",
    "IMG_CANARY_HARD_CAP_USD",
    "IMG_CANARY_IMAGE_SIZE",
    "IMG_CANARY_MODEL",
    "IMG_CANARY_PROVIDER",
    "IMG_CANARY_REVIEW_CHECKLIST",
    "IMGCanaryAttemptLedger",
    "IMGCanaryHumanReviewPacket",
    "IMGCanaryMonthlyBudgetEvidence",
    "IMGCanaryNativeHeadlineArtifact",
    "IMGCanaryPreflightEvidence",
    "IMGCanaryPreviousRunImmutabilityEvidence",
    "IMGCanaryProviderResponseSummary",
    "IMGCanaryRunIdentity",
    "IMGCanaryScopedApproval",
    "IMGCanarySerializedRequestEvidence",
    "IMGCanaryV2ApprovalBinding",
    "IMGCanaryDriveReadinessEvidence",
]
