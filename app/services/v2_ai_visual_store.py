"""Transactional stores for per-asset AI visual provider effects.

The provider coordinators in :mod:`v2_ai_visual_provider` and
:mod:`v2_veo_visual_provider` deliberately know nothing about SQLAlchemy.
This module is the production bridge to ``AIVisualAssetEffect``.  Every
transition is committed before it is returned, is compare-and-swap guarded by
the immutable record hash/revision, and retains a typed record projection for
crash reconciliation.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.ai_visual import AIVisualAssetEffect
from app.services.production_package import semantic_hash
from app.services.v2_ai_visual_provider import (
    V2AIImageAssetReceipt,
    V2AIImageFailureReceipt,
    V2AIImageRecordTransitions,
    V2AIImageSafeResponseCapture,
    V2AIImageSceneEffectIdentity,
    V2AIImageSceneEffectRecord,
)
from app.services.v2_veo_visual_provider import (
    V2_VEO_STORE_DURABILITY,
    V2VeoEffectRecord,
    V2VeoGenerationAuthority,
    stable_hash as veo_stable_hash,
)
from app.contracts.ai_visual_cross_modal import (
    VeoTechnicalMotionInspectionEvidence,
)


_IMAGE_RECORD_SCHEMA = "vcos.ai-visual-image-db-record.v1"
_VEO_RECORD_SCHEMA = "vcos.ai-visual-veo-db-record.v1"


def _uuid(value: str, *, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"V2_AI_VISUAL_{label}_UUID_INVALID") from exc


def _record_payload(record: V2AIImageSceneEffectRecord) -> dict[str, Any]:
    return {
        "schema_version": _IMAGE_RECORD_SCHEMA,
        "record": record.model_dump(mode="json"),
    }


class SQLAlchemyAIImageSceneEffectStore:
    """PostgreSQL-backed exactly-once store for Gemini image scene effects."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @property
    def ready(self) -> bool:
        return True

    def load(self, *, effect_id: str) -> V2AIImageSceneEffectRecord | None:
        identifier = _uuid(effect_id, label="EFFECT_ID")
        with self._session_factory() as session:
            row = session.get(AIVisualAssetEffect, identifier)
            return self._record_from_row(row) if row is not None else None

    def prepare(
        self,
        *,
        identity: V2AIImageSceneEffectIdentity,
        prepared_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        identifier = _uuid(identity.effect_id, label="EFFECT_ID")
        record = V2AIImageRecordTransitions.prepared(identity, now=prepared_at)
        with self._session_factory() as session:
            self._lock_identity(session, identity.effect_id)
            existing = session.get(AIVisualAssetEffect, identifier)
            if existing is not None:
                current = self._record_from_row(existing)
                self._require_identity(current, identity)
                session.commit()
                return current
            projection = dict(identity.db_identity_projection)
            for name in (
                "visual_production_run_id",
                "scene_plan_snapshot_id",
                "workflow_run_id",
                "video_project_id",
                "budget_reservation_id",
            ):
                projection[name] = _uuid(str(projection[name]), label=name.upper())
            row = AIVisualAssetEffect(
                id=identifier,
                **projection,
                **record.db_evidence_projection,
            )
            session.add(row)
            session.commit()
            return record

    def claim_submitting(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        submitted_at: datetime,
        lease_expires_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        return self._transition(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            builder=lambda current: V2AIImageRecordTransitions.submitting(
                current,
                owner_token_hash=submission_owner_token_hash,
                submitted_at=submitted_at,
                lease_expires_at=lease_expires_at,
            ),
        )

    def record_response_captured(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        submission_owner_token_hash: str,
        capture: V2AIImageSafeResponseCapture,
        response_journal_hash: str,
    ) -> V2AIImageSceneEffectRecord:
        def build(current: V2AIImageSceneEffectRecord) -> V2AIImageSceneEffectRecord:
            if current.submission_owner_token_hash != submission_owner_token_hash:
                raise ValueError("V2_AI_IMAGE_SUBMISSION_OWNER_MISMATCH")
            return V2AIImageRecordTransitions.response_captured(
                current,
                capture=capture,
                response_journal_hash=response_journal_hash,
            )

        return self._transition(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            builder=build,
        )

    def mark_verified(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        receipt: V2AIImageAssetReceipt,
        completed_at: datetime,
    ) -> V2AIImageSceneEffectRecord:
        return self._transition(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            builder=lambda current: V2AIImageRecordTransitions.verified(
                current,
                receipt=receipt,
                completed_at=completed_at,
            ),
        )

    def mark_failed_definitive(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord:
        if failure.classification != "DEFINITIVE":
            raise ValueError("V2_AI_IMAGE_FAILURE_CLASSIFICATION_MISMATCH")
        return self._mark_failed(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            failure=failure,
        )

    def mark_failed_uncertain(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord:
        if failure.classification != "UNCERTAIN":
            raise ValueError("V2_AI_IMAGE_FAILURE_CLASSIFICATION_MISMATCH")
        return self._mark_failed(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            failure=failure,
        )

    def _mark_failed(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        failure: V2AIImageFailureReceipt,
    ) -> V2AIImageSceneEffectRecord:
        return self._transition(
            effect_id=effect_id,
            expected_revision=expected_revision,
            expected_record_hash=expected_record_hash,
            builder=lambda current: V2AIImageRecordTransitions.failed(
                current, failure=failure
            ),
        )

    def _transition(
        self,
        *,
        effect_id: str,
        expected_revision: int,
        expected_record_hash: str,
        builder: Callable[[V2AIImageSceneEffectRecord], V2AIImageSceneEffectRecord],
    ) -> V2AIImageSceneEffectRecord:
        identifier = _uuid(effect_id, label="EFFECT_ID")
        with self._session_factory() as session:
            row = session.execute(
                select(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == identifier)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("V2_AI_IMAGE_EFFECT_NOT_FOUND")
            current = self._record_from_row(row)
            if (
                current.revision != expected_revision
                or current.record_hash != expected_record_hash
            ):
                raise ValueError("V2_AI_IMAGE_EFFECT_COMPARE_AND_SWAP_MISMATCH")
            updated = builder(current)
            self._apply_record(row, updated)
            session.commit()
            return updated

    @staticmethod
    def _apply_record(
        row: AIVisualAssetEffect,
        record: V2AIImageSceneEffectRecord,
    ) -> None:
        for name, value in record.db_evidence_projection.items():
            setattr(row, name, value)

    @staticmethod
    def _record_from_row(row: AIVisualAssetEffect) -> V2AIImageSceneEffectRecord:
        evidence = dict(row.qc_evidence or {})
        if evidence.get("schema_version") != _IMAGE_RECORD_SCHEMA or not isinstance(
            evidence.get("record"), dict
        ):
            raise ValueError("V2_AI_IMAGE_EFFECT_RECORD_EVIDENCE_INVALID")
        record = V2AIImageSceneEffectRecord.model_validate(evidence["record"])
        if (
            record.identity.effect_id != str(row.id)
            or record.identity.effect_identity_hash != row.effect_identity_hash
            or record.state.value != row.state
            or record.revision != row.revision
            or record.provider_call_count != row.provider_call_count
            or record.identity.request_hash != row.request_hash
            or record.identity.request_journal_hash != row.request_journal_hash
            or semantic_hash(record.identity.generation_policy)
            != semantic_hash(row.generation_policy)
        ):
            raise ValueError("V2_AI_IMAGE_EFFECT_ROW_PROJECTION_MISMATCH")
        return record

    @staticmethod
    def _require_identity(
        record: V2AIImageSceneEffectRecord,
        identity: V2AIImageSceneEffectIdentity,
    ) -> None:
        if record.identity != identity:
            raise ValueError("V2_AI_IMAGE_EFFECT_IDENTITY_CONFLICT")

    @staticmethod
    def _lock_identity(session: Session, value: str) -> None:
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 7901))"),
            {"value": value},
        )


class SQLAlchemyVeoEffectStore:
    """Transactional ``V2VeoEffectStore`` over the same per-slot table."""

    durability = V2_VEO_STORE_DURABILITY
    ready = True

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        workspace_root: Path | str = Path("var/v2-production"),
    ) -> None:
        self._session_factory = session_factory
        raw_workspace = Path(workspace_root)
        if raw_workspace.exists() and raw_workspace.is_symlink():
            raise ValueError("V2_VEO_WORKSPACE_ROOT_INVALID")
        raw_workspace.mkdir(parents=True, exist_ok=True)
        self._workspace_root = raw_workspace.resolve()

    def load_or_prepare(
        self,
        *,
        asset_effect_id: str,
        identity_hash: str,
        request_hash: str,
        authority: V2VeoGenerationAuthority,
        request_journal: dict[str, Any],
    ) -> V2VeoEffectRecord:
        identifier = _uuid(asset_effect_id, label="EFFECT_ID")
        if (
            authority.asset_effect_id != asset_effect_id
            or authority.identity_hash != identity_hash
            or authority.request_hash != request_hash
            or not self._hashed_journal_valid(request_journal, "journal_hash")
            or request_journal.get("identity_hash") != identity_hash
            or request_journal.get("request_hash") != request_hash
        ):
            raise ValueError("V2_VEO_EFFECT_IDENTITY_CONFLICT")
        with self._session_factory() as session:
            SQLAlchemyAIImageSceneEffectStore._lock_identity(session, asset_effect_id)
            existing = session.get(AIVisualAssetEffect, identifier)
            if existing is not None:
                record = self._record_from_row(existing)
                if (
                    record.identity_hash != identity_hash
                    or record.request_hash != request_hash
                    or dict(record.authority) != authority.identity_payload
                ):
                    raise ValueError("V2_VEO_EFFECT_IDENTITY_CONFLICT")
                session.commit()
                return record
            projection = dict(authority.db_identity_projection)
            for name in (
                "visual_production_run_id",
                "scene_plan_snapshot_id",
                "workflow_run_id",
                "video_project_id",
                "budget_reservation_id",
            ):
                projection[name] = _uuid(str(projection[name]), label=name.upper())
            request_journal_hash = str(request_journal["journal_hash"])
            request_journal_ref = self._persist_journal(
                asset_effect_id=asset_effect_id,
                kind="request",
                payload=request_journal,
                hash_key="journal_hash",
            )
            prepared_at = self._parse_datetime(request_journal.get("prepared_at"))
            record = V2VeoEffectRecord(
                asset_effect_id=asset_effect_id,
                identity_hash=identity_hash,
                request_hash=request_hash,
                authority=authority.identity_payload,
                request_journal=dict(request_journal),
                prepared_at=prepared_at,
            )
            record_hash = self._record_hash(record)
            row = AIVisualAssetEffect(
                id=identifier,
                **projection,
                state=record.state,
                revision=record.version,
                provider_call_count=record.generation_attempt_count,
                request_journal_ref=request_journal_ref,
                request_journal_hash=request_journal_hash,
                qc_evidence={
                    **self._record_payload(record),
                    "record_hash": record_hash,
                    "technical_qc": None,
                },
            )
            session.add(row)
            session.commit()
            return record

    def get(self, asset_effect_id: str) -> V2VeoEffectRecord | None:
        identifier = _uuid(asset_effect_id, label="EFFECT_ID")
        with self._session_factory() as session:
            row = session.get(AIVisualAssetEffect, identifier)
            return self._record_from_row(row) if row is not None else None

    def compare_and_set(
        self,
        *,
        asset_effect_id: str,
        expected_version: int,
        expected_states: frozenset[str],
        new_state: str,
        patch: dict[str, Any],
    ) -> V2VeoEffectRecord:
        identifier = _uuid(asset_effect_id, label="EFFECT_ID")
        with self._session_factory() as session:
            row = session.execute(
                select(AIVisualAssetEffect)
                .where(AIVisualAssetEffect.id == identifier)
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                raise ValueError("V2_VEO_EFFECT_NOT_FOUND")
            current = self._record_from_row(row)
            if (
                current.version != expected_version
                or current.state not in expected_states
            ):
                raise ValueError("V2_VEO_EFFECT_COMPARE_AND_SWAP_MISMATCH")
            values = {
                name: getattr(current, name)
                for name in V2VeoEffectRecord.__dataclass_fields__
            }
            unknown = set(patch) - set(values)
            if unknown:
                raise ValueError("V2_VEO_EFFECT_PATCH_FIELD_INVALID")
            immutable = {
                "asset_effect_id",
                "identity_hash",
                "request_hash",
                "authority",
                "request_journal",
                "prepared_at",
            }
            if set(patch).intersection(immutable):
                raise ValueError("V2_VEO_EFFECT_IMMUTABLE_PATCH_FORBIDDEN")
            values.update(patch)
            values["state"] = new_state
            values["version"] = current.version + 1
            updated = V2VeoEffectRecord(**values)
            self._validate_transition(current, updated)
            self._apply_record(row, updated)
            session.commit()
            return updated

    @staticmethod
    def _record_payload(record: V2VeoEffectRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            name: getattr(record, name)
            for name in V2VeoEffectRecord.__dataclass_fields__
        }
        return {
            "schema_version": _VEO_RECORD_SCHEMA,
            "record": SQLAlchemyVeoEffectStore._json_value(payload),
        }

    @classmethod
    def _record_hash(cls, record: V2VeoEffectRecord) -> str:
        return semantic_hash(cls._record_payload(record))

    @classmethod
    def _record_from_row(cls, row: AIVisualAssetEffect) -> V2VeoEffectRecord:
        evidence = dict(row.qc_evidence or {})
        raw = evidence.get("record")
        if evidence.get("schema_version") != _VEO_RECORD_SCHEMA or not isinstance(
            raw, dict
        ):
            raise ValueError("V2_VEO_EFFECT_RECORD_EVIDENCE_INVALID")
        values = dict(raw)
        for field_name in (
            "prepared_at",
            "submitted_at",
            "response_captured_at",
            "completed_at",
        ):
            values[field_name] = cls._parse_datetime(values.get(field_name))
        for field_name in (
            "actual_cost_usd",
            "conservative_settlement_cost_usd",
            "output_fps",
        ):
            values[field_name] = (
                Decimal(str(values[field_name]))
                if values.get(field_name) is not None
                else None
            )
        values["response_journals"] = tuple(values.get("response_journals") or ())
        record = V2VeoEffectRecord(**values)
        if (
            record.asset_effect_id != str(row.id)
            or record.identity_hash != row.effect_identity_hash
            or record.request_hash != row.request_hash
            or record.state != row.state
            or record.version != row.revision
            or record.generation_attempt_count != row.provider_call_count
            or cls._record_hash(record) != evidence.get("record_hash")
        ):
            raise ValueError("V2_VEO_EFFECT_ROW_PROJECTION_MISMATCH")
        return record

    def _apply_record(
        self, row: AIVisualAssetEffect, record: V2VeoEffectRecord
    ) -> None:
        for name, value in record.db_state_projection.items():
            if name != "qc_evidence":
                setattr(row, name, value)
        row.qc_evidence = {
            **self._record_payload(record),
            "record_hash": self._record_hash(record),
            "technical_qc": dict(record.qc_receipt or {}) or None,
        }
        if record.response_journals:
            latest = dict(record.response_journals[-1])
            row.response_journal_ref = self._persist_journal_static(
                workspace_root=self._workspace_root,
                asset_effect_id=record.asset_effect_id,
                kind=f"response-{len(record.response_journals)}",
                payload=latest,
                hash_key="journal_hash",
            )
            row.response_journal_hash = str(latest["journal_hash"])
            row.sanitized_response_hash = row.response_journal_hash
            if latest.get("provider_response_id"):
                row.provider_response_id = latest.get("provider_response_id")
        if record.normalization_receipt:
            normalization = dict(record.normalization_receipt)
            row.normalization_ref = self._persist_journal_static(
                workspace_root=self._workspace_root,
                asset_effect_id=record.asset_effect_id,
                kind="normalization",
                payload=normalization,
                hash_key="normalization_hash",
            )
            row.normalization_hash = str(normalization["normalization_hash"])
        if record.qc_receipt:
            qc = dict(record.qc_receipt)
            row.qc_ref = self._persist_journal_static(
                workspace_root=self._workspace_root,
                asset_effect_id=record.asset_effect_id,
                kind="video-qc",
                payload=qc,
                hash_key="qc_hash",
            )
            row.qc_hash = str(qc["qc_hash"])
            if record.state == "VERIFIED":
                authority = dict(record.authority)
                sampled = list(
                    (qc.get("checks") or {}).get("sampled_frame_sha256") or []
                )
                technical = VeoTechnicalMotionInspectionEvidence.build(
                    asset_effect_id=record.asset_effect_id,
                    asset_effect_identity_hash=record.identity_hash,
                    provider_request_hash=record.request_hash,
                    asset_slot_id=str(authority.get("asset_slot_id") or ""),
                    primary_asset_owner_scene_id=str(
                        authority.get("primary_asset_owner_scene_id") or ""
                    ),
                    bound_scene_ids=list(authority.get("bound_scene_ids") or []),
                    bound_scene_plan_hashes=list(
                        authority.get("bound_scene_plan_hashes") or []
                    ),
                    scene_plan_hash=str(authority.get("scene_plan_hash") or ""),
                    compiled_prompt_hash=str(
                        authority.get("compiled_prompt_hash") or ""
                    ),
                    prompt_hash=str(authority.get("prompt_hash") or ""),
                    required_semantic_anchors=list(
                        authority.get("required_semantic_anchors") or []
                    ),
                    model_id=str(authority.get("model_id") or ""),
                    provider_operation_id=str(record.provider_operation_id or ""),
                    output_ref=str(record.normalized_output_ref or ""),
                    output_checksum=str(record.normalized_output_sha256 or ""),
                    qc_ref=str(row.qc_ref or ""),
                    qc_hash=str(row.qc_hash or ""),
                    sampled_frame_sha256=sampled,
                )
                row.qc_evidence = {
                    **row.qc_evidence,
                    "technical_motion_evidence": technical.model_dump(mode="json"),
                }
        row.failure_evidence_hash = (
            row.response_journal_hash
            if record.last_error_code and row.response_journal_hash
            else None
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, dict):
            return {
                str(key): SQLAlchemyVeoEffectStore._json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [SQLAlchemyVeoEffectStore._json_value(item) for item in value]
        return value

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("V2_VEO_EFFECT_TIMESTAMP_INVALID") from exc

    @staticmethod
    def _hashed_journal_valid(payload: dict[str, Any], hash_key: str) -> bool:
        expected = payload.get(hash_key)
        body = dict(payload)
        body.pop(hash_key, None)
        return (
            isinstance(expected, str)
            and len(expected) == 64
            and veo_stable_hash(body) == expected
        )

    @staticmethod
    def _validate_transition(
        current: V2VeoEffectRecord,
        updated: V2VeoEffectRecord,
    ) -> None:
        allowed: dict[str, frozenset[str]] = {
            "PREPARED": frozenset({"SUBMITTING"}),
            "SUBMITTING": frozenset(
                {"OPERATION_RECORDED", "FAILED_DEFINITIVE", "FAILED_UNCERTAIN"}
            ),
            "OPERATION_RECORDED": frozenset(
                {
                    "OPERATION_RECORDED",
                    "POLLING",
                    "RESPONSE_CAPTURED",
                    "FAILED_DEFINITIVE",
                }
            ),
            "POLLING": frozenset({"POLLING", "RESPONSE_CAPTURED", "FAILED_DEFINITIVE"}),
            "RESPONSE_CAPTURED": frozenset({"RESPONSE_CAPTURED", "DOWNLOADED"}),
            "DOWNLOADED": frozenset({"NORMALIZED", "BLOCKED"}),
            "NORMALIZED": frozenset({"VERIFIED", "BLOCKED"}),
            "VERIFIED": frozenset(),
            "FAILED_DEFINITIVE": frozenset(),
            "FAILED_UNCERTAIN": frozenset({"OPERATION_RECORDED"}),
            "BLOCKED": frozenset(),
        }
        if updated.state not in allowed.get(current.state, frozenset()):
            raise ValueError("V2_VEO_EFFECT_STATE_TRANSITION_INVALID")
        if updated.generation_attempt_count < current.generation_attempt_count or (
            updated.generation_attempt_count - current.generation_attempt_count > 1
        ):
            raise ValueError("V2_VEO_EFFECT_ATTEMPT_COUNT_INVALID")
        if (
            updated.generation_attempt_count == 1
            and current.generation_attempt_count == 0
            and not (current.state == "PREPARED" and updated.state == "SUBMITTING")
        ):
            raise ValueError("V2_VEO_EFFECT_ATTEMPT_BOUNDARY_INVALID")
        if (
            current.provider_operation_id is not None
            and updated.provider_operation_id != current.provider_operation_id
        ):
            raise ValueError("V2_VEO_OPERATION_ID_IMMUTABLE")
        current_journals = tuple(dict(value) for value in current.response_journals)
        updated_journals = tuple(dict(value) for value in updated.response_journals)
        if updated_journals[: len(current_journals)] != current_journals:
            raise ValueError("V2_VEO_RESPONSE_JOURNAL_APPEND_ONLY_REQUIRED")
        if len(updated_journals) - len(current_journals) not in {0, 1}:
            raise ValueError("V2_VEO_RESPONSE_JOURNAL_APPEND_BOUND_INVALID")
        if any(
            not SQLAlchemyVeoEffectStore._hashed_journal_valid(value, "journal_hash")
            for value in updated_journals
        ):
            raise ValueError("V2_VEO_RESPONSE_JOURNAL_HASH_INVALID")

    def _persist_journal(
        self,
        *,
        asset_effect_id: str,
        kind: str,
        payload: dict[str, Any],
        hash_key: str,
    ) -> str:
        return self._persist_journal_static(
            workspace_root=self._workspace_root,
            asset_effect_id=asset_effect_id,
            kind=kind,
            payload=payload,
            hash_key=hash_key,
        )

    @staticmethod
    def _persist_journal_static(
        *,
        workspace_root: Path,
        asset_effect_id: str,
        kind: str,
        payload: dict[str, Any],
        hash_key: str,
    ) -> str:
        if not SQLAlchemyVeoEffectStore._hashed_journal_valid(payload, hash_key):
            raise ValueError("V2_VEO_JOURNAL_HASH_INVALID")
        workspace_root = workspace_root.resolve()
        if not workspace_root.is_dir() or workspace_root.is_symlink():
            raise ValueError("V2_VEO_WORKSPACE_ROOT_INVALID")
        journal_hash = str(payload[hash_key])
        effect_digest = veo_stable_hash({"asset_effect_id": asset_effect_id})
        directory = (
            workspace_root / "ai-visual-assets" / "veo" / effect_digest / "journals"
        )
        cursor = workspace_root
        for part_name in ("ai-visual-assets", "veo", effect_digest, "journals"):
            cursor = cursor / part_name
            if cursor.exists() and (cursor.is_symlink() or not cursor.is_dir()):
                raise ValueError("V2_VEO_JOURNAL_DIRECTORY_INVALID")
            cursor.mkdir(exist_ok=True)
        resolved_directory = directory.resolve()
        if workspace_root not in resolved_directory.parents:
            raise ValueError("V2_VEO_JOURNAL_PATH_OUTSIDE_WORKSPACE")
        destination = directory / f"{kind}-{journal_hash}.json"
        encoded = json.dumps(
            SQLAlchemyVeoEffectStore._json_value(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if destination.exists():
            if not destination.is_file() or destination.read_bytes() != encoded:
                raise ValueError("V2_VEO_JOURNAL_IMMUTABILITY_CONFLICT")
        else:
            part = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
            try:
                with part.open("xb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(part, destination)
                directory_fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                part.unlink(missing_ok=True)
        return destination.relative_to(workspace_root).as_posix()
