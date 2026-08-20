"""Code-only closeout for D15, P1, P2, and P3 VCOS debt.

The services are deterministic, append-only where authority matters, and make a
strict distinction between software readiness and live proof.  No provider,
platform, payment, analytics, or legal action is executed from this module.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationFailureError
from app.core.time import utc_now
from app.db.models.long_form_analytics import LongFormAnalyticsWindow
from app.db.models.m8 import AnalyticsSnapshot, MetricAvailabilitySnapshot
from app.db.models.ops import CostEvent
from app.db.models.workflow import VideoProject
from app.db.models.remaining_debt import (
    AffiliateLinkRegistry,
    AffiliateOfferSnapshot,
    AnalyticsEvidenceWindow,
    AppealEvidencePack,
    AudienceDeliveryPlan,
    BusinessActionItem,
    BusinessDisclosureAssessment,
    ChannelPnlSnapshot,
    ContinuationCapitalReview,
    LearningEquivalenceFingerprint,
    LearningOperationalIncident,
    LearningReview,
    MonetizationAccountStatus,
    PaymentProfileStatus,
    PlatformEnforcementIncident,
    RevenueSnapshot,
    SelfFundingAssessment,
    SeriesArcVersion,
    SeriesEpisodeBlueprint,
    SeriesLifecycleDecision,
    SeriesPublicOrdinal,
)


_SERIES_NAMESPACE = uuid.UUID("4309262e-e6d1-5929-83aa-8938e9e7f0e4")
_LEARNING_NAMESPACE = uuid.UUID("c2b93570-e354-5ccf-a99f-35dcc8178a2a")
_BUSINESS_NAMESPACE = uuid.UUID("55345f60-b903-5a64-9258-56f6c7d36802")

SERIES_MODES = frozenset({"FIXED_COUNT", "ROLLING"})
SERIES_STATES = frozenset(
    {"DRAFT", "ACTIVE", "SUPERSEDED", "COMPLETION_PENDING", "COMPLETED"}
)
BLUEPRINT_STATES = frozenset({"PLANNED", "ASSIGNED", "PUBLISHED", "SKIPPED"})
ANALYTICS_WINDOWS = frozenset({"H24", "H72", "D7", "D30", "M11"})
CONFIDENCE_STATES = frozenset(
    {"TOO_EARLY", "WEAK_SIGNAL", "DIRECTIONAL", "STABLE", "ACTION_READY"}
)
REVENUE_STATES = frozenset(
    {"ESTIMATED", "PENDING", "LOCKED", "FINALIZED", "REVERSED", "PAID"}
)
SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
TRUSTED_SOURCE_CONFIDENCE = frozenset({"HIGH", "VERIFIED", "ACTION_READY"})
ANALYTICS_DATA_AUTHORITY_POLICY = {
    "version": "vcos.analytics-data-authority.v1",
    "platform": "YOUTUBE",
    "freshness": "FRESH",
    "accepted_source_confidence": ("MEDIUM", "HIGH"),
    "required_metrics": {
        "H24": ("views", "impressions"),
        "H72": ("views", "impressions"),
        "D7": (
            "views",
            "impressions",
            "average_view_duration_seconds",
            "average_view_percentage",
        ),
        "D30": (
            "views",
            "impressions",
            "average_view_duration_seconds",
            "average_view_percentage",
        ),
        "M11": (
            "views",
            "impressions",
            "average_view_duration_seconds",
            "average_view_percentage",
        ),
    },
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple, set, frozenset)):
        normalized = [_jsonable(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
        return normalized
    if isinstance(value, (uuid.UUID, datetime, Decimal, Path)):
        return str(value)
    return value


def _hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _deterministic_id(namespace: uuid.UUID, payload: Mapping[str, Any]) -> uuid.UUID:
    return uuid.uuid5(namespace, _hash(payload))


def _require(value: bool, code: str) -> None:
    if not value:
        raise ValidationFailureError(code)


def _enum(value: str, allowed: frozenset[str], code: str) -> str:
    normalized = str(value).strip().upper()
    if normalized not in allowed:
        raise ValidationFailureError(code)
    return normalized


def _decimal(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class SeriesProgressProjection:
    series_plan_id: uuid.UUID
    series_arc_version_id: uuid.UUID
    arc_mode: str
    state: str
    planned_episode_count: int | None
    blueprint_count: int
    assigned_count: int
    published_count: int
    skipped_count: int
    remaining_count: int | None
    next_public_ordinal: int
    display_label: str


class SeriesAuthorityService:
    """Append-only editorial series authority with public ordinal continuity."""

    def __init__(self, session: Session):
        self.session = session

    def create_arc(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        series_plan_id: uuid.UUID,
        arc_mode: str,
        planned_episode_count: int | None,
        premise: str,
        coverage_policy: Mapping[str, Any],
    ) -> SeriesArcVersion:
        mode = _enum(arc_mode, SERIES_MODES, "SERIES_ARC_MODE_INVALID")
        if mode == "FIXED_COUNT":
            _require(
                isinstance(planned_episode_count, int) and planned_episode_count > 0,
                "SERIES_PLANNED_EPISODE_COUNT_REQUIRED",
            )
        else:
            _require(
                planned_episode_count is None,
                "ROLLING_SERIES_PLANNED_COUNT_FORBIDDEN",
            )
        premise = premise.strip()
        _require(bool(premise), "SERIES_PREMISE_REQUIRED")
        latest = self.session.scalar(
            select(func.max(SeriesArcVersion.version_number)).where(
                SeriesArcVersion.series_plan_id == series_plan_id
            )
        )
        version_number = int(latest or 0) + 1
        payload = {
            "schema_version": "vcos.series-arc-version.v1",
            "company_id": company_id,
            "channel_workspace_id": channel_workspace_id,
            "series_plan_id": series_plan_id,
            "version_number": version_number,
            "arc_mode": mode,
            "planned_episode_count": planned_episode_count,
            "premise": premise,
            "coverage_policy": dict(coverage_policy),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(SeriesArcVersion).where(SeriesArcVersion.content_hash == digest)
        )
        if existing is not None:
            return existing
        row = SeriesArcVersion(
            id=_deterministic_id(_SERIES_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            series_plan_id=series_plan_id,
            version_number=version_number,
            arc_mode=mode,
            planned_episode_count=planned_episode_count,
            state="DRAFT",
            premise=premise,
            coverage_policy=dict(coverage_policy),
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def add_blueprint(
        self,
        *,
        arc_id: uuid.UUID,
        blueprint_key: str,
        title: str,
        editorial_contract: Mapping[str, Any],
        coverage_tags: Sequence[str],
        planned_position: int | None = None,
    ) -> SeriesEpisodeBlueprint:
        arc = self._arc(arc_id, lock=True)
        _require(arc.state in {"DRAFT", "ACTIVE"}, "SERIES_ARC_NOT_EDITABLE")
        key = blueprint_key.strip()
        title = title.strip()
        _require(bool(key) and bool(title), "SERIES_BLUEPRINT_IDENTITY_REQUIRED")
        if arc.arc_mode == "FIXED_COUNT":
            _require(
                isinstance(planned_position, int)
                and planned_position > 0
                and planned_position <= int(arc.planned_episode_count or 0),
                "SERIES_BLUEPRINT_POSITION_INVALID",
            )
        elif planned_position is not None:
            _require(planned_position > 0, "SERIES_BLUEPRINT_POSITION_INVALID")
        payload = {
            "schema_version": "vcos.series-episode-blueprint.v1",
            "arc_id": arc.id,
            "arc_hash": arc.content_hash,
            "blueprint_key": key,
            "planned_position": planned_position,
            "title": title,
            "editorial_contract": dict(editorial_contract),
            "coverage_tags": sorted({str(item) for item in coverage_tags}),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(SeriesEpisodeBlueprint).where(
                SeriesEpisodeBlueprint.series_arc_version_id == arc.id,
                SeriesEpisodeBlueprint.blueprint_key == key,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("SERIES_BLUEPRINT_IMMUTABLE_CONFLICT")
            return existing
        row = SeriesEpisodeBlueprint(
            id=_deterministic_id(_SERIES_NAMESPACE, payload),
            company_id=arc.company_id,
            channel_workspace_id=arc.channel_workspace_id,
            series_plan_id=arc.series_plan_id,
            series_arc_version_id=arc.id,
            blueprint_key=key,
            planned_position=planned_position,
            title=title,
            editorial_contract=dict(editorial_contract),
            coverage_tags=sorted({str(item) for item in coverage_tags}),
            state="PLANNED",
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def activate_arc(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        reason = reason.strip()
        _require(bool(reason), "SERIES_LIFECYCLE_REASON_REQUIRED")
        existing_command = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing_command is not None:
            if existing_command.series_arc_version_id != arc.id:
                raise ConflictError("SERIES_COMMAND_REUSE_CONFLICT")
            return arc
        _require(arc.state == "DRAFT", "SERIES_ARC_NOT_ACTIVATABLE")
        blueprints = self._blueprints(arc.id)
        if arc.arc_mode == "FIXED_COUNT":
            expected = int(arc.planned_episode_count or 0)
            positions = sorted(
                item.planned_position
                for item in blueprints
                if item.planned_position is not None
            )
            _require(
                len(blueprints) == expected
                and positions == list(range(1, expected + 1)),
                "SERIES_FIXED_ARC_COVERAGE_INCOMPLETE",
            )
        prior = list(
            self.session.scalars(
                select(SeriesArcVersion)
                .where(
                    SeriesArcVersion.series_plan_id == arc.series_plan_id,
                    SeriesArcVersion.state.in_({"ACTIVE", "COMPLETION_PENDING"}),
                    SeriesArcVersion.id != arc.id,
                )
                .with_for_update()
            ).all()
        )
        for item in prior:
            item.state = "SUPERSEDED"
        arc.state = "ACTIVE"
        arc.approved_by = actor_id
        arc.approved_at = utc_now()
        self._decision(
            arc=arc,
            decision_type="ACTIVATE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=None,
            resulting_count=arc.planned_episode_count,
        )
        self.session.flush()
        return arc

    def bind_technical_attempt(
        self,
        *,
        blueprint_id: uuid.UUID,
        video_project_id: uuid.UUID,
        technical_attempt_ref: str,
    ) -> SeriesEpisodeBlueprint:
        row = self.session.scalar(
            select(SeriesEpisodeBlueprint)
            .where(SeriesEpisodeBlueprint.id == blueprint_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(f"series blueprint not found: {blueprint_id}")
        _require(
            row.state in {"PLANNED", "ASSIGNED"}, "SERIES_BLUEPRINT_NOT_ASSIGNABLE"
        )
        attempt = technical_attempt_ref.strip()
        _require(bool(attempt), "SERIES_TECHNICAL_ATTEMPT_REQUIRED")
        if row.state == "ASSIGNED":
            if (
                row.video_project_id != video_project_id
                or row.technical_attempt_ref != attempt
            ):
                raise ConflictError("SERIES_TECHNICAL_ATTEMPT_IMMUTABLE")
            return row
        row.video_project_id = video_project_id
        row.technical_attempt_ref = attempt
        row.state = "ASSIGNED"
        self.session.flush()
        return row

    def record_publication(
        self,
        *,
        series_plan_id: uuid.UUID,
        publication_receipt_id: uuid.UUID,
        video_project_id: uuid.UUID,
        published_at: datetime,
        technical_attempt_ref: str | None = None,
        blueprint_id: uuid.UUID | None = None,
    ) -> SeriesPublicOrdinal:
        existing = self.session.scalar(
            select(SeriesPublicOrdinal).where(
                SeriesPublicOrdinal.series_plan_id == series_plan_id,
                SeriesPublicOrdinal.publication_receipt_id == publication_receipt_id,
            )
        )
        if existing is not None:
            if (
                existing.video_project_id != video_project_id
                or (
                    technical_attempt_ref is not None
                    and existing.technical_attempt_ref != technical_attempt_ref
                )
            ):
                raise ConflictError("SERIES_PUBLICATION_RECEIPT_REUSE_CONFLICT")
            return existing
        arc = self.session.scalar(
            select(SeriesArcVersion)
            .where(
                SeriesArcVersion.series_plan_id == series_plan_id,
                SeriesArcVersion.state.in_({"ACTIVE", "COMPLETION_PENDING"}),
            )
            .order_by(SeriesArcVersion.version_number.desc())
            .with_for_update()
        )
        if arc is None:
            raise ValidationFailureError("SERIES_ACTIVE_ARC_REQUIRED")
        blueprint = None
        if blueprint_id is not None:
            blueprint = self.session.get(SeriesEpisodeBlueprint, blueprint_id)
        if blueprint is None:
            blueprint = self.session.scalar(
                select(SeriesEpisodeBlueprint)
                .where(
                    SeriesEpisodeBlueprint.series_arc_version_id == arc.id,
                    SeriesEpisodeBlueprint.video_project_id == video_project_id,
                )
                .with_for_update()
            )
        if blueprint is None and arc.arc_mode == "ROLLING":
            blueprint = self.add_blueprint(
                arc_id=arc.id,
                blueprint_key=f"rolling:{video_project_id}",
                title=f"Rolling episode {video_project_id}",
                editorial_contract={"generated_at_assignment": True},
                coverage_tags=[],
            )
            self.bind_technical_attempt(
                blueprint_id=blueprint.id,
                video_project_id=video_project_id,
                technical_attempt_ref=technical_attempt_ref or str(video_project_id),
            )
        if (
            blueprint is None
            or blueprint.series_arc_version_id != arc.id
            or blueprint.series_plan_id != series_plan_id
        ):
            raise ValidationFailureError("SERIES_PUBLICATION_BLUEPRINT_REQUIRED")
        if blueprint.state == "PUBLISHED":
            ordinal = self.session.scalar(
                select(SeriesPublicOrdinal).where(
                    SeriesPublicOrdinal.episode_blueprint_id == blueprint.id
                )
            )
            if (
                ordinal is None
                or ordinal.publication_receipt_id != publication_receipt_id
            ):
                raise ConflictError("SERIES_BLUEPRINT_ALREADY_PUBLISHED")
            return ordinal
        _require(
            blueprint.state == "ASSIGNED"
            and blueprint.video_project_id == video_project_id,
            "SERIES_PUBLICATION_TECHNICAL_ATTEMPT_REQUIRED",
        )
        attempt_ref = technical_attempt_ref or blueprint.technical_attempt_ref
        _require(bool(attempt_ref), "SERIES_PUBLICATION_TECHNICAL_ATTEMPT_REQUIRED")
        _require(
            blueprint.technical_attempt_ref == attempt_ref,
            "SERIES_PUBLICATION_TECHNICAL_ATTEMPT_MISMATCH",
        )
        maximum = self.session.scalar(
            select(func.max(SeriesPublicOrdinal.public_ordinal)).where(
                SeriesPublicOrdinal.series_plan_id == series_plan_id
            )
        )
        public_ordinal = int(maximum or 0) + 1
        if arc.arc_mode == "FIXED_COUNT":
            _require(
                public_ordinal <= int(arc.planned_episode_count or 0),
                "SERIES_PUBLIC_ORDINAL_EXCEEDS_PLAN",
            )
        payload = {
            "schema_version": "vcos.series-public-ordinal.v1",
            "series_plan_id": series_plan_id,
            "arc_id": arc.id,
            "blueprint_id": blueprint.id,
            "video_project_id": video_project_id,
            "publication_receipt_id": publication_receipt_id,
            "public_ordinal": public_ordinal,
            "playlist_position": public_ordinal - 1,
            "technical_attempt_ref": attempt_ref,
            "published_at": published_at,
        }
        row = SeriesPublicOrdinal(
            id=_deterministic_id(_SERIES_NAMESPACE, payload),
            company_id=arc.company_id,
            channel_workspace_id=arc.channel_workspace_id,
            series_plan_id=series_plan_id,
            series_arc_version_id=arc.id,
            episode_blueprint_id=blueprint.id,
            video_project_id=video_project_id,
            publication_receipt_id=publication_receipt_id,
            public_ordinal=public_ordinal,
            playlist_position=public_ordinal - 1,
            technical_attempt_ref=attempt_ref,
            identity_hash=_hash(payload),
            published_at=published_at,
        )
        self.session.add(row)
        blueprint.state = "PUBLISHED"
        blueprint.publication_receipt_id = publication_receipt_id
        blueprint.public_ordinal = public_ordinal
        blueprint.video_project_id = video_project_id
        blueprint.technical_attempt_ref = attempt_ref
        self.session.flush()
        progress = self.progress(series_plan_id=series_plan_id)
        if (
            arc.arc_mode == "FIXED_COUNT"
            and progress.planned_episode_count is not None
            and progress.published_count >= progress.planned_episode_count
        ):
            arc.state = "COMPLETION_PENDING"
        self.session.flush()
        return row

    def request_early_completion(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        reason = reason.strip()
        _require(bool(reason), "SERIES_LIFECYCLE_REASON_REQUIRED")
        _require(arc.state == "ACTIVE", "SERIES_EARLY_COMPLETION_NOT_ALLOWED")
        progress = self.progress(series_plan_id=arc.series_plan_id)
        _require(
            progress.published_count > 0, "SERIES_EARLY_COMPLETION_NO_PUBLIC_EPISODE"
        )
        self._decision(
            arc=arc,
            decision_type="EARLY_COMPLETE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=arc.planned_episode_count,
            resulting_count=progress.published_count,
        )
        arc.state = "COMPLETION_PENDING"
        self.session.flush()
        return arc

    def extend_fixed_series(
        self,
        *,
        arc_id: uuid.UUID,
        new_planned_episode_count: int,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        reason = reason.strip()
        _require(bool(reason), "SERIES_LIFECYCLE_REASON_REQUIRED")
        _require(arc.arc_mode == "FIXED_COUNT", "SERIES_EXTENSION_REQUIRES_FIXED_COUNT")
        _require(
            arc.state in {"ACTIVE", "COMPLETION_PENDING"},
            "SERIES_EXTENSION_NOT_ALLOWED",
        )
        old_count = int(arc.planned_episode_count or 0)
        _require(
            new_planned_episode_count > old_count, "SERIES_EXTENSION_COUNT_INVALID"
        )
        existing_command = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing_command is not None:
            result = self.session.scalar(
                select(SeriesArcVersion).where(
                    SeriesArcVersion.previous_version_id == arc.id,
                    SeriesArcVersion.planned_episode_count == new_planned_episode_count,
                )
            )
            if result is None:
                raise ConflictError("SERIES_EXTENSION_COMMAND_REUSE_CONFLICT")
            return result
        # The partial unique index permits only one ACTIVE or COMPLETION_PENDING
        # arc per plan.  Retire the predecessor before inserting its successor.
        arc.state = "SUPERSEDED"
        self.session.flush()
        payload = {
            "schema_version": "vcos.series-arc-version.v1",
            "company_id": arc.company_id,
            "channel_workspace_id": arc.channel_workspace_id,
            "series_plan_id": arc.series_plan_id,
            "version_number": arc.version_number + 1,
            "previous_version_id": arc.id,
            "arc_mode": "FIXED_COUNT",
            "planned_episode_count": new_planned_episode_count,
            "premise": arc.premise,
            "coverage_policy": arc.coverage_policy,
        }
        new_arc = SeriesArcVersion(
            id=_deterministic_id(_SERIES_NAMESPACE, payload),
            company_id=arc.company_id,
            channel_workspace_id=arc.channel_workspace_id,
            series_plan_id=arc.series_plan_id,
            version_number=arc.version_number + 1,
            previous_version_id=arc.id,
            arc_mode="FIXED_COUNT",
            planned_episode_count=new_planned_episode_count,
            state="ACTIVE",
            premise=arc.premise,
            coverage_policy=dict(arc.coverage_policy or {}),
            approved_by=actor_id,
            approved_at=utc_now(),
            content_hash=_hash(payload),
        )
        self.session.add(new_arc)
        self.session.flush()
        for old in self._blueprints(arc.id):
            copied_payload = {
                "schema_version": "vcos.series-episode-blueprint.v1",
                "arc_id": new_arc.id,
                "arc_hash": new_arc.content_hash,
                "blueprint_key": old.blueprint_key,
                "planned_position": old.planned_position,
                "title": old.title,
                "editorial_contract": old.editorial_contract,
                "coverage_tags": old.coverage_tags,
            }
            self.session.add(
                SeriesEpisodeBlueprint(
                    id=_deterministic_id(_SERIES_NAMESPACE, copied_payload),
                    company_id=old.company_id,
                    channel_workspace_id=old.channel_workspace_id,
                    series_plan_id=old.series_plan_id,
                    series_arc_version_id=new_arc.id,
                    blueprint_key=old.blueprint_key,
                    planned_position=old.planned_position,
                    title=old.title,
                    editorial_contract=dict(old.editorial_contract or {}),
                    coverage_tags=list(old.coverage_tags or []),
                    state=old.state,
                    video_project_id=old.video_project_id,
                    technical_attempt_ref=old.technical_attempt_ref,
                    publication_receipt_id=old.publication_receipt_id,
                    public_ordinal=old.public_ordinal,
                    content_hash=_hash(copied_payload),
                )
            )
        for position in range(old_count + 1, new_planned_episode_count + 1):
            self.add_blueprint(
                arc_id=new_arc.id,
                blueprint_key=f"EP-{position:03d}",
                planned_position=position,
                title=f"Episode {position}",
                editorial_contract={"extension_placeholder": True},
                coverage_tags=[],
            )
        self._decision(
            arc=arc,
            decision_type="EXTEND",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=old_count,
            resulting_count=new_planned_episode_count,
        )
        self.session.flush()
        return new_arc

    def complete_series(
        self,
        *,
        arc_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
        reason: str,
    ) -> SeriesArcVersion:
        arc = self._arc(arc_id, lock=True)
        reason = reason.strip()
        _require(bool(reason), "SERIES_LIFECYCLE_REASON_REQUIRED")
        _require(arc.state == "COMPLETION_PENDING", "SERIES_COMPLETION_NOT_PENDING")
        self._decision(
            arc=arc,
            decision_type="COMPLETE",
            command_id=command_id,
            actor_id=actor_id,
            reason=reason,
            previous_count=arc.planned_episode_count,
            resulting_count=arc.planned_episode_count,
        )
        arc.state = "COMPLETED"
        self.session.flush()
        return arc

    def progress(self, *, series_plan_id: uuid.UUID) -> SeriesProgressProjection:
        arc = self.session.scalar(
            select(SeriesArcVersion)
            .where(
                SeriesArcVersion.series_plan_id == series_plan_id,
                SeriesArcVersion.state.in_(
                    {"ACTIVE", "COMPLETION_PENDING", "COMPLETED"}
                ),
            )
            .order_by(SeriesArcVersion.version_number.desc())
        )
        if arc is None:
            raise NotFoundError(f"active series arc not found: {series_plan_id}")
        blueprints = self._blueprints(arc.id)
        published = sum(item.state == "PUBLISHED" for item in blueprints)
        assigned = sum(item.state == "ASSIGNED" for item in blueprints)
        skipped = sum(item.state == "SKIPPED" for item in blueprints)
        maximum = self.session.scalar(
            select(func.max(SeriesPublicOrdinal.public_ordinal)).where(
                SeriesPublicOrdinal.series_plan_id == series_plan_id
            )
        )
        remaining = (
            max(int(arc.planned_episode_count or 0) - published - skipped, 0)
            if arc.arc_mode == "FIXED_COUNT"
            else None
        )
        label = (
            f"EP{published:02d}/{int(arc.planned_episode_count or 0):02d}"
            if arc.arc_mode == "FIXED_COUNT"
            else f"EP{published:02d}/ROLLING"
        )
        return SeriesProgressProjection(
            series_plan_id=series_plan_id,
            series_arc_version_id=arc.id,
            arc_mode=arc.arc_mode,
            state=arc.state,
            planned_episode_count=arc.planned_episode_count,
            blueprint_count=len(blueprints),
            assigned_count=assigned,
            published_count=published,
            skipped_count=skipped,
            remaining_count=remaining,
            next_public_ordinal=int(maximum or 0) + 1,
            display_label=label,
        )

    def _arc(self, arc_id: uuid.UUID, *, lock: bool) -> SeriesArcVersion:
        statement = select(SeriesArcVersion).where(SeriesArcVersion.id == arc_id)
        if lock:
            statement = statement.with_for_update()
        row = self.session.scalar(statement)
        if row is None:
            raise NotFoundError(f"series arc not found: {arc_id}")
        return row

    def _blueprints(self, arc_id: uuid.UUID) -> list[SeriesEpisodeBlueprint]:
        return list(
            self.session.scalars(
                select(SeriesEpisodeBlueprint)
                .where(SeriesEpisodeBlueprint.series_arc_version_id == arc_id)
                .order_by(
                    SeriesEpisodeBlueprint.planned_position.asc().nullslast(),
                    SeriesEpisodeBlueprint.created_at,
                )
            ).all()
        )

    def _decision(
        self,
        *,
        arc: SeriesArcVersion,
        decision_type: str,
        command_id: uuid.UUID,
        actor_id: uuid.UUID,
        reason: str,
        previous_count: int | None,
        resulting_count: int | None,
    ) -> SeriesLifecycleDecision:
        reason = reason.strip()
        _require(bool(reason), "SERIES_LIFECYCLE_REASON_REQUIRED")
        payload = {
            "schema_version": "vcos.series-lifecycle-decision.v1",
            "arc_id": arc.id,
            "arc_hash": arc.content_hash,
            "decision_type": decision_type,
            "command_id": command_id,
            "actor_id": actor_id,
            "previous_count": previous_count,
            "resulting_count": resulting_count,
            "reason": reason,
        }
        existing = self.session.scalar(
            select(SeriesLifecycleDecision).where(
                SeriesLifecycleDecision.command_id == command_id
            )
        )
        if existing is not None:
            if existing.decision_hash != _hash(payload):
                raise ConflictError("SERIES_COMMAND_REUSE_CONFLICT")
            return existing
        row = SeriesLifecycleDecision(
            id=_deterministic_id(_SERIES_NAMESPACE, payload),
            company_id=arc.company_id,
            channel_workspace_id=arc.channel_workspace_id,
            series_plan_id=arc.series_plan_id,
            series_arc_version_id=arc.id,
            decision_type=decision_type,
            command_id=command_id,
            actor_id=actor_id,
            previous_count=previous_count,
            resulting_count=resulting_count,
            reason=reason,
            state="APPROVED",
            evidence_refs=[],
            decision_hash=_hash(payload),
        )
        self.session.add(row)
        return row


class LearningAuthorityService:
    """Confidence-aware, policy-current and exactly-once learning review."""

    def __init__(self, session: Session):
        self.session = session

    def create_fingerprint(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        source_entity_ref: str,
        content_product_type: str,
        series_plan_id: uuid.UUID | None,
        profile_snapshot_hash: str,
        target_market: str,
        content_language: str,
        format_key: str,
        normalized_features: Mapping[str, Any],
    ) -> LearningEquivalenceFingerprint:
        _require(
            re.fullmatch(r"[0-9a-f]{64}", profile_snapshot_hash) is not None,
            "LEARNING_PROFILE_SNAPSHOT_HASH_INVALID",
        )
        source_entity_ref = source_entity_ref.strip()
        _require(bool(source_entity_ref), "LEARNING_SOURCE_ENTITY_REF_REQUIRED")
        payload = {
            "schema_version": "vcos.learning-equivalence-fingerprint.v1",
            "company_id": company_id,
            "channel_workspace_id": channel_workspace_id,
            "content_product_type": content_product_type,
            "series_plan_id": series_plan_id,
            "profile_snapshot_hash": profile_snapshot_hash,
            "target_market": target_market,
            "content_language": content_language,
            "format_key": format_key,
            "normalized_features": dict(normalized_features),
        }
        fingerprint = _hash(payload)
        existing = self.session.scalar(
            select(LearningEquivalenceFingerprint).where(
                LearningEquivalenceFingerprint.channel_workspace_id
                == channel_workspace_id,
                LearningEquivalenceFingerprint.source_entity_ref == source_entity_ref,
            )
        )
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise ConflictError("LEARNING_SOURCE_FINGERPRINT_IMMUTABLE")
            return existing
        row = LearningEquivalenceFingerprint(
            id=_deterministic_id(
                _LEARNING_NAMESPACE,
                {"source_entity_ref": source_entity_ref, "fingerprint": fingerprint},
            ),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            source_entity_ref=source_entity_ref,
            content_product_type=content_product_type,
            series_plan_id=series_plan_id,
            profile_snapshot_hash=profile_snapshot_hash,
            target_market=target_market,
            content_language=content_language,
            format_key=format_key,
            normalized_features=_jsonable(normalized_features),
            fingerprint=fingerprint,
        )
        self.session.add(row)
        self.session.flush()
        return row

    @staticmethod
    def analytics_data_authority_decision(
        *,
        snapshot: AnalyticsSnapshot,
        availability: MetricAvailabilitySnapshot | None,
        source_window: LongFormAnalyticsWindow | None,
        requested_window_key: str,
    ) -> dict[str, Any]:
        """Derive immutable learning evidence exclusively from M8/M9 authority."""

        window_key = _enum(
            requested_window_key, ANALYTICS_WINDOWS, "ANALYTICS_WINDOW_INVALID"
        )
        _require(
            str(snapshot.platform).upper()
            == ANALYTICS_DATA_AUTHORITY_POLICY["platform"],
            "ANALYTICS_PLATFORM_AUTHORITY_INVALID",
        )
        _require(
            str(snapshot.freshness_state).upper()
            == ANALYTICS_DATA_AUTHORITY_POLICY["freshness"],
            "ANALYTICS_SOURCE_STALE",
        )
        _require(availability is not None, "ANALYTICS_AVAILABILITY_AUTHORITY_MISSING")
        _require(
            availability.uploaded_video_id == snapshot.uploaded_video_id,
            "ANALYTICS_AVAILABILITY_SCOPE_MISMATCH",
        )
        _require(
            availability.analytics_sync_run_id == snapshot.analytics_sync_run_id,
            "ANALYTICS_AVAILABILITY_RUN_MISMATCH",
        )
        _require(
            str(availability.platform).upper() == str(snapshot.platform).upper(),
            "ANALYTICS_AVAILABILITY_PLATFORM_MISMATCH",
        )
        _require(
            str(availability.freshness_state).upper()
            == ANALYTICS_DATA_AUTHORITY_POLICY["freshness"],
            "ANALYTICS_AVAILABILITY_STALE",
        )
        accepted_confidence = set(
            ANALYTICS_DATA_AUTHORITY_POLICY["accepted_source_confidence"]
        )
        source_confidence = str(snapshot.confidence_level).upper()
        availability_confidence = str(availability.confidence_level).upper()
        _require(
            source_confidence in accepted_confidence,
            "ANALYTICS_SOURCE_CONFIDENCE_INSUFFICIENT",
        )
        _require(
            availability_confidence in accepted_confidence,
            "ANALYTICS_AVAILABILITY_CONFIDENCE_INSUFFICIENT",
        )
        _require(
            source_window is not None, "ANALYTICS_LONG_FORM_WINDOW_AUTHORITY_MISSING"
        )
        expected_window = "D30" if window_key == "M11" else window_key
        _require(
            source_window.window_type == expected_window,
            "ANALYTICS_WINDOW_AUTHORITY_MISMATCH",
        )
        _require(
            source_window.uploaded_video_id == snapshot.uploaded_video_id,
            "ANALYTICS_WINDOW_VIDEO_SCOPE_MISMATCH",
        )
        _require(
            source_window.analytics_snapshot_id == snapshot.id,
            "ANALYTICS_WINDOW_SNAPSHOT_MISMATCH",
        )
        _require(
            source_window.state == "DIAGNOSTICS_COMPLETE",
            "ANALYTICS_DIAGNOSTICS_NOT_COMPLETE",
        )
        availability_blob = dict(availability.availability_blob or {})
        missing_metrics = [
            metric
            for metric in ANALYTICS_DATA_AUTHORITY_POLICY["required_metrics"][
                window_key
            ]
            if str((availability_blob.get(metric) or {}).get("state", "")).upper()
            != "AVAILABLE"
        ]
        _require(
            not missing_metrics,
            "ANALYTICS_REQUIRED_METRICS_INCOMPLETE:"
            + ",".join(sorted(missing_metrics)),
        )
        normalized = dict(snapshot.normalized_metrics_blob or {})

        def metric_value(key: str) -> int | None:
            value = (normalized.get(key) or {}).get("value")
            return None if value is None else int(value)

        views = metric_value("views")
        impressions = metric_value("impressions")
        confidence_state = (
            "ACTION_READY"
            if source_confidence == "HIGH" and window_key in {"D30", "M11"}
            else "STABLE"
            if source_confidence == "HIGH"
            else "DIRECTIONAL"
        )
        return {
            "window_key": window_key,
            "source_version": (
                f"{ANALYTICS_DATA_AUTHORITY_POLICY['version']}:{snapshot.id}"
            ),
            "maturity_state": "TOO_EARLY" if window_key in {"H24", "H72"} else "MATURE",
            "confidence_state": confidence_state,
            "sample_size": max(int(views or 0), 0),
            "impressions": impressions,
            "views": views,
            "source_snapshot_refs": [
                f"analytics-snapshot://{snapshot.id}",
                f"metric-availability://{availability.id}",
                f"long-form-analytics-window://{source_window.id}",
            ],
            "evidence_payload": {
                "authority_policy": ANALYTICS_DATA_AUTHORITY_POLICY,
                "authority_policy_hash": _hash(ANALYTICS_DATA_AUTHORITY_POLICY),
                "analytics_snapshot_id": str(snapshot.id),
                "metric_availability_snapshot_id": str(availability.id),
                "long_form_analytics_window_id": str(source_window.id),
                "source_freshness": str(snapshot.freshness_state).upper(),
                "source_confidence": source_confidence,
                "availability_freshness": str(availability.freshness_state).upper(),
                "availability_confidence": availability_confidence,
            },
            "matured_at": source_window.observed_to or snapshot.captured_at,
        }

    def record_analytics_window_from_snapshot(
        self, *, analytics_snapshot_id: uuid.UUID, requested_window_key: str
    ) -> AnalyticsEvidenceWindow:
        snapshot = self.session.get(AnalyticsSnapshot, analytics_snapshot_id)
        if snapshot is None:
            raise NotFoundError(
                f"analytics snapshot not found: {analytics_snapshot_id}"
            )
        availability = self.session.scalar(
            select(MetricAvailabilitySnapshot)
            .where(
                MetricAvailabilitySnapshot.uploaded_video_id
                == snapshot.uploaded_video_id,
                MetricAvailabilitySnapshot.analytics_sync_run_id
                == snapshot.analytics_sync_run_id,
                MetricAvailabilitySnapshot.platform == snapshot.platform,
            )
            .order_by(MetricAvailabilitySnapshot.captured_at.desc())
        )
        source_window = (
            self.session.get(
                LongFormAnalyticsWindow, snapshot.long_form_analytics_window_id
            )
            if snapshot.long_form_analytics_window_id is not None
            else None
        )
        decision = self.analytics_data_authority_decision(
            snapshot=snapshot,
            availability=availability,
            source_window=source_window,
            requested_window_key=requested_window_key,
        )
        return self._record_analytics_window(
            company_id=snapshot.company_id,
            channel_workspace_id=snapshot.channel_workspace_id,
            uploaded_video_id=snapshot.uploaded_video_id,
            **decision,
        )

    def _record_analytics_window(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        uploaded_video_id: uuid.UUID,
        window_key: str,
        source_version: str,
        maturity_state: str,
        confidence_state: str,
        sample_size: int,
        impressions: int | None,
        views: int | None,
        source_snapshot_refs: Sequence[str],
        evidence_payload: Mapping[str, Any],
        matured_at: datetime | None,
    ) -> AnalyticsEvidenceWindow:
        window = _enum(window_key, ANALYTICS_WINDOWS, "ANALYTICS_WINDOW_INVALID")
        confidence = _enum(
            confidence_state, CONFIDENCE_STATES, "ANALYTICS_CONFIDENCE_INVALID"
        )
        maturity = maturity_state.strip().upper()
        _require(
            maturity in {"TOO_EARLY", "MATURE", "STALE", "INCOMPLETE"},
            "ANALYTICS_MATURITY_INVALID",
        )
        _require(sample_size >= 0, "ANALYTICS_SAMPLE_SIZE_INVALID")
        payload = {
            "schema_version": "vcos.analytics-evidence-window.v1",
            "uploaded_video_id": uploaded_video_id,
            "window_key": window,
            "source_version": source_version,
            "maturity_state": maturity,
            "confidence_state": confidence,
            "sample_size": sample_size,
            "impressions": impressions,
            "views": views,
            "source_snapshot_refs": list(source_snapshot_refs),
            "evidence_payload": dict(evidence_payload),
            "matured_at": matured_at,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(AnalyticsEvidenceWindow).where(
                AnalyticsEvidenceWindow.uploaded_video_id == uploaded_video_id,
                AnalyticsEvidenceWindow.window_key == window,
                AnalyticsEvidenceWindow.source_version == source_version,
            )
        )
        if existing is not None:
            if existing.evidence_hash != digest:
                raise ConflictError("ANALYTICS_WINDOW_IMMUTABLE_CONFLICT")
            return existing
        row = AnalyticsEvidenceWindow(
            id=_deterministic_id(_LEARNING_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            uploaded_video_id=uploaded_video_id,
            window_key=window,
            source_version=source_version,
            maturity_state=maturity,
            confidence_state=confidence,
            sample_size=sample_size,
            impressions=impressions,
            views=views,
            source_snapshot_refs=list(source_snapshot_refs),
            evidence_payload=_jsonable(evidence_payload),
            evidence_hash=digest,
            matured_at=matured_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def open_operational_incident(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        incident_type: str,
        external_ref: str,
        severity: str,
        evidence_payload: Mapping[str, Any],
        video_project_id: uuid.UUID | None = None,
        blocks_learning: bool = True,
    ) -> LearningOperationalIncident:
        severity = _enum(severity, SEVERITIES, "LEARNING_INCIDENT_SEVERITY_INVALID")
        incident_type = incident_type.strip().upper()
        _require(
            incident_type
            in {"NO_VIEW_CANARY", "POLICY_DRIFT", "ANALYTICS_DRIFT", "LIVE_PROOF"},
            "LEARNING_INCIDENT_TYPE_INVALID",
        )
        payload = {
            "schema_version": "vcos.learning-operational-incident.v1",
            "channel_workspace_id": channel_workspace_id,
            "video_project_id": video_project_id,
            "incident_type": incident_type,
            "external_ref": external_ref,
            "severity": severity,
            "blocks_learning": blocks_learning,
            "evidence_payload": dict(evidence_payload),
        }
        existing = self.session.scalar(
            select(LearningOperationalIncident).where(
                LearningOperationalIncident.channel_workspace_id
                == channel_workspace_id,
                LearningOperationalIncident.incident_type == incident_type,
                LearningOperationalIncident.external_ref == external_ref,
            )
        )
        if existing is not None:
            if existing.content_hash != _hash(payload):
                raise ConflictError("LEARNING_INCIDENT_IMMUTABLE_CONFLICT")
            return existing
        row = LearningOperationalIncident(
            id=_deterministic_id(_LEARNING_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            incident_type=incident_type,
            external_ref=external_ref,
            severity=severity,
            state="OPEN",
            blocks_learning=blocks_learning,
            evidence_payload=_jsonable(evidence_payload),
            content_hash=_hash(payload),
            detected_at=utc_now(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def resolve_operational_incident(
        self, *, incident_id: uuid.UUID
    ) -> LearningOperationalIncident:
        row = self.session.scalar(
            select(LearningOperationalIncident)
            .where(LearningOperationalIncident.id == incident_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(f"learning incident not found: {incident_id}")
        if row.state == "RESOLVED":
            return row
        row.state = "RESOLVED"
        row.resolved_at = utc_now()
        self.session.flush()
        return row

    def review(
        self,
        *,
        fingerprint_id: uuid.UUID,
        analytics_evidence_window_id: uuid.UUID,
        current_policy_hash: str,
        comparable_count: int,
        command_id: uuid.UUID,
    ) -> LearningReview:
        fingerprint = self.session.get(LearningEquivalenceFingerprint, fingerprint_id)
        window = self.session.get(AnalyticsEvidenceWindow, analytics_evidence_window_id)
        if fingerprint is None or window is None:
            raise NotFoundError("learning fingerprint or analytics window not found")
        _require(
            re.fullmatch(r"[0-9a-f]{64}", current_policy_hash) is not None,
            "LEARNING_CURRENT_POLICY_HASH_INVALID",
        )
        if (
            fingerprint.company_id != window.company_id
            or fingerprint.channel_workspace_id != window.channel_workspace_id
        ):
            raise ValidationFailureError("LEARNING_SCOPE_MISMATCH")
        derived_comparable_count = int(
            self.session.scalar(
                select(func.count(LearningEquivalenceFingerprint.id)).where(
                    LearningEquivalenceFingerprint.channel_workspace_id
                    == fingerprint.channel_workspace_id,
                    LearningEquivalenceFingerprint.fingerprint
                    == fingerprint.fingerprint,
                )
            )
            or 0
        )
        supplied_comparable_count = comparable_count
        comparable_count = derived_comparable_count
        reasons: list[str] = []
        if supplied_comparable_count != derived_comparable_count:
            reasons.append("COMPARABLE_COUNT_AUTHORITY_MISMATCH")
        if window.maturity_state != "MATURE":
            reasons.append("ANALYTICS_NOT_MATURE")
        if window.confidence_state not in {"DIRECTIONAL", "STABLE", "ACTION_READY"}:
            reasons.append("ANALYTICS_CONFIDENCE_INSUFFICIENT")
        if current_policy_hash != fingerprint.profile_snapshot_hash:
            reasons.append("SYSTEM_PROMOTION_POLICY_RECHECK_FAILED")
        if comparable_count < 1:
            reasons.append("COMPARABLE_EVIDENCE_MISSING")
        if window.window_key in {"D30", "M11"} and comparable_count < 3:
            reasons.append("PROMOTION_COMPARABLE_COUNT_INSUFFICIENT")
        open_learning_incident = self.session.scalar(
            select(LearningOperationalIncident.id).where(
                LearningOperationalIncident.channel_workspace_id
                == fingerprint.channel_workspace_id,
                LearningOperationalIncident.state == "OPEN",
                LearningOperationalIncident.blocks_learning.is_(True),
            )
        )
        open_enforcement = self.session.scalar(
            select(PlatformEnforcementIncident.id).where(
                PlatformEnforcementIncident.channel_workspace_id
                == fingerprint.channel_workspace_id,
                PlatformEnforcementIncident.company_id == fingerprint.company_id,
                PlatformEnforcementIncident.state == "OPEN",
                PlatformEnforcementIncident.freeze_learning.is_(True),
            )
        )
        if open_learning_incident is not None:
            reasons.append("LEARNING_OPERATIONAL_INCIDENT_OPEN")
        if open_enforcement is not None:
            reasons.append("PLATFORM_ENFORCEMENT_FREEZE")
        decision = "ELIGIBLE" if not reasons else "BLOCKED"
        payload = {
            "schema_version": "vcos.learning-review.v1",
            "fingerprint_id": fingerprint.id,
            "fingerprint": fingerprint.fingerprint,
            "window_id": window.id,
            "window_key": window.window_key,
            "evidence_hash": window.evidence_hash,
            "current_policy_hash": current_policy_hash,
            "comparable_count": comparable_count,
            "decision": decision,
            "reason_codes": sorted(reasons),
            "command_id": command_id,
        }
        command_match = self.session.scalar(
            select(LearningReview).where(LearningReview.command_id == command_id)
        )
        if command_match is not None:
            if command_match.decision_hash != _hash(payload):
                raise ConflictError("LEARNING_REVIEW_COMMAND_REUSE_CONFLICT")
            return command_match
        row = LearningReview(
            id=_deterministic_id(_LEARNING_NAMESPACE, payload),
            company_id=fingerprint.company_id,
            channel_workspace_id=fingerprint.channel_workspace_id,
            fingerprint_id=fingerprint.id,
            analytics_evidence_window_id=window.id,
            window_key=window.window_key,
            command_id=command_id,
            current_policy_hash=current_policy_hash,
            comparable_count=comparable_count,
            decision=decision,
            reason_codes=sorted(reasons),
            audit_trail=[
                {
                    "at": utc_now().isoformat(),
                    "from": None,
                    "to": decision,
                    "reason_codes": sorted(reasons),
                }
            ],
            evidence_hash=window.evidence_hash,
            decision_hash=_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        self.cleanup_superseded_reviews(
            fingerprint_id=fingerprint.id,
            keep_review_id=row.id,
        )
        return row

    def promote(
        self,
        *,
        review_id: uuid.UUID,
        actor_id: uuid.UUID,
        command_id: uuid.UUID,
    ) -> LearningReview:
        row = self.session.scalar(
            select(LearningReview)
            .where(LearningReview.id == review_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(f"learning review not found: {review_id}")
        if row.decision == "PROMOTED":
            return row
        _require(row.decision == "ELIGIBLE", "LEARNING_REVIEW_NOT_PROMOTABLE")
        _require(row.window_key in {"D30", "M11"}, "LEARNING_PROMOTION_WINDOW_INVALID")
        trail = list(row.audit_trail or [])
        if any(item.get("command_id") == str(command_id) for item in trail):
            return row
        trail.append(
            {
                "at": utc_now().isoformat(),
                "from": row.decision,
                "to": "PROMOTED",
                "actor_id": str(actor_id),
                "command_id": str(command_id),
            }
        )
        row.decision = "PROMOTED"
        row.reviewed_by = actor_id
        row.reviewed_at = utc_now()
        row.audit_trail = trail
        self.session.flush()
        return row

    def cleanup_superseded_reviews(
        self, *, fingerprint_id: uuid.UUID, keep_review_id: uuid.UUID
    ) -> int:
        rows = list(
            self.session.scalars(
                select(LearningReview)
                .where(
                    LearningReview.fingerprint_id == fingerprint_id,
                    LearningReview.id != keep_review_id,
                    LearningReview.decision.in_({"BLOCKED", "ELIGIBLE"}),
                )
                .with_for_update()
            ).all()
        )
        for row in rows:
            trail = list(row.audit_trail or [])
            trail.append(
                {
                    "at": utc_now().isoformat(),
                    "from": row.decision,
                    "to": "SUPERSEDED",
                    "superseded_by": str(keep_review_id),
                }
            )
            row.decision = "SUPERSEDED"
            row.audit_trail = trail
        self.session.flush()
        return len(rows)

    def create_audience_delivery_plan(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        video_project_id: uuid.UUID,
        publication_receipt_id: uuid.UUID,
        target_markets: Sequence[str],
        target_languages: Sequence[str],
        packaging_refs: Sequence[str],
        playlist_refs: Sequence[str],
    ) -> AudienceDeliveryPlan:
        _require(bool(target_markets), "AUDIENCE_DELIVERY_TARGET_MARKET_REQUIRED")
        _require(bool(target_languages), "AUDIENCE_DELIVERY_LANGUAGE_REQUIRED")
        payload = {
            "schema_version": "vcos.audience-delivery-plan.v1",
            "publication_receipt_id": publication_receipt_id,
            "target_markets": sorted({str(item) for item in target_markets}),
            "target_languages": sorted({str(item) for item in target_languages}),
            "packaging_refs": sorted({str(item) for item in packaging_refs}),
            "playlist_refs": sorted({str(item) for item in playlist_refs}),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(AudienceDeliveryPlan).where(
                AudienceDeliveryPlan.publication_receipt_id == publication_receipt_id
            )
        )
        if existing is not None:
            if existing.plan_hash != digest:
                raise ConflictError("AUDIENCE_DELIVERY_PLAN_IMMUTABLE_CONFLICT")
            return existing
        row = AudienceDeliveryPlan(
            id=_deterministic_id(_LEARNING_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            publication_receipt_id=publication_receipt_id,
            target_markets=payload["target_markets"],
            target_languages=payload["target_languages"],
            packaging_refs=payload["packaging_refs"],
            playlist_refs=payload["playlist_refs"],
            state="ELIGIBLE",
            plan_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row


@dataclass(frozen=True, slots=True)
class BusinessDashboardProjection:
    channel_workspace_id: uuid.UUID
    monetization_state: str
    payment_state: str
    open_enforcement_count: int
    disclosure_health: str
    trailing_finalized_revenue: Decimal
    trailing_cash_received: Decimal
    trailing_cost: Decimal
    contribution_margin: Decimal
    self_funding_decision: str
    next_actions: tuple[str, ...]


class BusinessMonitoringService:
    """Persistent monitor state for monetization, economics and enforcement."""

    def __init__(self, session: Session):
        self.session = session

    def record_payment_status(
        self,
        *,
        company_id: uuid.UUID,
        payee_ref: str,
        tax_state: str,
        address_verification_state: str,
        payment_method_state: str,
        payment_hold_state: str,
        source_type: str,
        source_ref: str,
        confidence_state: str,
        source_updated_at: datetime,
        valid_until: datetime | None,
    ) -> PaymentProfileStatus:
        _require(
            source_updated_at <= utc_now(), "PAYMENT_STATUS_SOURCE_FUTURE"
        )
        version = self.session.scalar(
            select(func.max(PaymentProfileStatus.version_number)).where(
                PaymentProfileStatus.company_id == company_id
            )
        )
        version_number = int(version or 0) + 1
        payload = {
            "schema_version": "vcos.payment-profile-status.v1",
            "company_id": company_id,
            "version_number": version_number,
            "payee_ref": payee_ref,
            "tax_state": tax_state.upper(),
            "address_verification_state": address_verification_state.upper(),
            "payment_method_state": payment_method_state.upper(),
            "payment_hold_state": payment_hold_state.upper(),
            "source_type": source_type.upper(),
            "source_ref": source_ref,
            "confidence_state": confidence_state.upper(),
            "source_updated_at": source_updated_at,
            "valid_until": valid_until,
        }
        row = PaymentProfileStatus(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            version_number=version_number,
            payee_ref=payee_ref,
            tax_state=payload["tax_state"],
            address_verification_state=payload["address_verification_state"],
            payment_method_state=payload["payment_method_state"],
            payment_hold_state=payload["payment_hold_state"],
            source_type=payload["source_type"],
            source_ref=source_ref,
            confidence_state=payload["confidence_state"],
            source_updated_at=source_updated_at,
            valid_until=valid_until,
            content_hash=_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        current = self.session.scalar(
            select(PaymentProfileStatus)
            .where(PaymentProfileStatus.company_id == company_id)
            .order_by(
                PaymentProfileStatus.source_updated_at.desc(),
                PaymentProfileStatus.version_number.desc(),
            )
        )
        self._sync_payment_actions(current)
        return row

    def record_monetization_status(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        platform: str,
        program_type: str,
        eligibility_state: str,
        enrollment_state: str,
        restriction_state: str,
        source_type: str,
        source_ref: str,
        confidence_state: str,
        source_updated_at: datetime,
        valid_until: datetime | None,
    ) -> MonetizationAccountStatus:
        _require(
            source_updated_at <= utc_now(), "MONETIZATION_STATUS_SOURCE_FUTURE"
        )
        version = self.session.scalar(
            select(func.max(MonetizationAccountStatus.version_number)).where(
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id,
                MonetizationAccountStatus.platform == platform.upper(),
            )
        )
        version_number = int(version or 0) + 1
        payload = {
            "schema_version": "vcos.monetization-account-status.v1",
            "company_id": company_id,
            "channel_workspace_id": channel_workspace_id,
            "platform": platform.upper(),
            "version_number": version_number,
            "program_type": program_type.upper(),
            "eligibility_state": eligibility_state.upper(),
            "enrollment_state": enrollment_state.upper(),
            "restriction_state": restriction_state.upper(),
            "source_type": source_type.upper(),
            "source_ref": source_ref,
            "confidence_state": confidence_state.upper(),
            "source_updated_at": source_updated_at,
            "valid_until": valid_until,
        }
        row = MonetizationAccountStatus(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            platform=payload["platform"],
            version_number=version_number,
            program_type=payload["program_type"],
            eligibility_state=payload["eligibility_state"],
            enrollment_state=payload["enrollment_state"],
            restriction_state=payload["restriction_state"],
            source_type=payload["source_type"],
            source_ref=source_ref,
            confidence_state=payload["confidence_state"],
            source_updated_at=source_updated_at,
            valid_until=valid_until,
            content_hash=_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        current = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(
                MonetizationAccountStatus.company_id == company_id,
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id,
                MonetizationAccountStatus.platform == payload["platform"],
            )
            .order_by(
                MonetizationAccountStatus.source_updated_at.desc(),
                MonetizationAccountStatus.version_number.desc(),
            )
        )
        self._sync_monetization_actions(current)
        return row

    def record_revenue(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        source: str,
        amount_state: str,
        amount: Decimal | str | float | int,
        currency: str,
        period_start: datetime,
        period_end: datetime,
        source_ref: str,
        source_updated_at: datetime,
        confidence_state: str,
        video_project_id: uuid.UUID | None = None,
    ) -> RevenueSnapshot:
        state = _enum(amount_state, REVENUE_STATES, "REVENUE_AMOUNT_STATE_INVALID")
        _require(period_end > period_start, "REVENUE_PERIOD_INVALID")
        value = _decimal(amount)
        _require(value >= 0, "REVENUE_AMOUNT_NEGATIVE")
        payload = {
            "schema_version": "vcos.revenue-snapshot.v1",
            "channel_workspace_id": channel_workspace_id,
            "video_project_id": video_project_id,
            "source": source.upper(),
            "amount_state": state,
            "amount": value,
            "currency": currency.upper(),
            "period_start": period_start,
            "period_end": period_end,
            "source_ref": source_ref,
            "source_updated_at": source_updated_at,
            "confidence_state": confidence_state.upper(),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(RevenueSnapshot).where(RevenueSnapshot.content_hash == digest)
        )
        if existing is not None:
            return existing
        row = RevenueSnapshot(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            source=payload["source"],
            amount_state=state,
            amount=value,
            currency=payload["currency"],
            period_start=period_start,
            period_end=period_end,
            source_ref=source_ref,
            source_updated_at=source_updated_at,
            confidence_state=payload["confidence_state"],
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def build_channel_pnl(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        currency: str,
        direct_cost: Decimal | str | float | int | None = None,
        allocated_ops_cost: Decimal | str | float | int = Decimal("0"),
        calculation_version: str = "vcos.channel-pnl.v2",
    ) -> ChannelPnlSnapshot:
        rows = list(
            self.session.scalars(
                select(RevenueSnapshot).where(
                    RevenueSnapshot.company_id == company_id,
                    RevenueSnapshot.channel_workspace_id == channel_workspace_id,
                    RevenueSnapshot.period_start >= period_start,
                    RevenueSnapshot.period_end <= period_end,
                    RevenueSnapshot.currency == currency.upper(),
                )
            ).all()
        )
        buckets = {key: Decimal("0") for key in REVENUE_STATES}
        for row in rows:
            if row.amount_state in {"LOCKED", "FINALIZED", "PAID", "REVERSED"}:
                _require(
                    row.confidence_state in TRUSTED_SOURCE_CONFIDENCE,
                    "REVENUE_SOURCE_CONFIDENCE_INSUFFICIENT",
                )
                _require(
                    row.source_updated_at >= row.period_end,
                    "REVENUE_SOURCE_NOT_MATURE",
                )
            buckets[row.amount_state] += Decimal(row.amount)
        derived_direct_cost, cost_rows = self._actual_direct_cost(
            channel_workspace_id=channel_workspace_id,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
        )
        if direct_cost is not None:
            _require(
                _decimal(direct_cost) == derived_direct_cost,
                "ACTUAL_COST_AUTHORITY_MISMATCH",
            )
        direct = derived_direct_cost
        ops = _decimal(allocated_ops_cost)
        contribution = (
            buckets["LOCKED"]
            + buckets["FINALIZED"]
            + buckets["PAID"]
            - buckets["REVERSED"]
            - direct
            - ops
        )
        source_snapshot_refs = [f"revenue://{row.id}" for row in rows] + [
            f"cost-event://{row.id}" for row in cost_rows
        ]
        payload = {
            "schema_version": calculation_version,
            "channel_workspace_id": channel_workspace_id,
            "period_start": period_start,
            "period_end": period_end,
            "currency": currency.upper(),
            "buckets": buckets,
            "direct_cost": direct,
            "allocated_ops_cost": ops,
            "contribution_margin": contribution,
            "source_snapshot_refs": source_snapshot_refs,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(ChannelPnlSnapshot).where(
                ChannelPnlSnapshot.company_id == company_id,
                ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
                ChannelPnlSnapshot.period_start == period_start,
                ChannelPnlSnapshot.period_end == period_end,
                ChannelPnlSnapshot.calculation_version == calculation_version,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("CHANNEL_PNL_IMMUTABLE_CONFLICT")
            return existing
        row = ChannelPnlSnapshot(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            period_start=period_start,
            period_end=period_end,
            currency=currency.upper(),
            estimated_revenue=buckets["ESTIMATED"],
            locked_revenue=buckets["LOCKED"],
            finalized_revenue=buckets["FINALIZED"],
            cash_received=buckets["PAID"],
            reversed_revenue=buckets["REVERSED"],
            direct_cost=direct,
            allocated_ops_cost=ops,
            contribution_margin=contribution,
            calculation_version=calculation_version,
            source_snapshot_refs=source_snapshot_refs,
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _actual_direct_cost(
        self,
        *,
        channel_workspace_id: uuid.UUID,
        period_start: datetime,
        period_end: datetime,
        currency: str,
    ) -> tuple[Decimal, list[CostEvent]]:
        project_ids = select(VideoProject.id).where(
            VideoProject.channel_workspace_id == channel_workspace_id
        )
        cost_rows = list(
            self.session.scalars(
                select(CostEvent).where(
                    CostEvent.currency == currency.upper(),
                    CostEvent.created_at >= period_start,
                    CostEvent.created_at < period_end,
                    CostEvent.cost_type.in_({"ACTUAL", "ADJUSTED", "REFUNDED"}),
                    or_(
                        and_(
                            CostEvent.cost_scope_type == "CHANNEL",
                            CostEvent.cost_scope_id == channel_workspace_id,
                        ),
                        and_(
                            CostEvent.cost_scope_type == "PROJECT",
                            CostEvent.cost_scope_id.in_(project_ids),
                        ),
                    ),
                )
            ).all()
        )
        total = Decimal("0")
        for event in cost_rows:
            amount = Decimal(event.amount)
            total += -amount if event.cost_type == "REFUNDED" else amount
        return _decimal(max(total, Decimal("0"))), cost_rows

    def evaluate_self_funding(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        assessment_window_end: datetime,
        policy_version: str = "vcos.self-funding-gate.v1",
    ) -> SelfFundingAssessment:
        monetization = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id,
                MonetizationAccountStatus.company_id == company_id,
                MonetizationAccountStatus.platform == "YOUTUBE",
                MonetizationAccountStatus.source_updated_at <= assessment_window_end,
            )
            .order_by(
                MonetizationAccountStatus.source_updated_at.desc(),
                MonetizationAccountStatus.version_number.desc(),
            )
        )
        payment = self.session.scalar(
            select(PaymentProfileStatus)
            .where(
                PaymentProfileStatus.company_id == company_id,
                PaymentProfileStatus.source_updated_at <= assessment_window_end,
            )
            .order_by(
                PaymentProfileStatus.source_updated_at.desc(),
                PaymentProfileStatus.version_number.desc(),
            )
        )
        pnl = list(
            self.session.scalars(
                select(ChannelPnlSnapshot)
                .where(
                    ChannelPnlSnapshot.company_id == company_id,
                    ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
                    ChannelPnlSnapshot.period_end <= assessment_window_end,
                )
                .order_by(ChannelPnlSnapshot.period_end.desc())
                .limit(2)
            ).all()
        )
        open_critical = self.session.scalar(
            select(PlatformEnforcementIncident.id).where(
                PlatformEnforcementIncident.channel_workspace_id
                == channel_workspace_id,
                PlatformEnforcementIncident.company_id == company_id,
                PlatformEnforcementIncident.state == "OPEN",
                PlatformEnforcementIncident.severity.in_({"HIGH", "CRITICAL"}),
            )
        )
        reasons: list[str] = []
        if (
            monetization is None
            or monetization.enrollment_state != "ACTIVE"
            or monetization.restriction_state not in {"NONE", "CLEAR"}
        ):
            reasons.append("MONETIZATION_NOT_ACTIVE")
        elif (
            monetization.confidence_state not in TRUSTED_SOURCE_CONFIDENCE
            or monetization.valid_until is None
            or monetization.valid_until < assessment_window_end
            or monetization.source_updated_at > assessment_window_end
        ):
            reasons.append("MONETIZATION_SOURCE_NOT_CURRENT")
        if (
            payment is None
            or payment.tax_state != "VERIFIED"
            or payment.address_verification_state != "VERIFIED"
            or payment.payment_method_state != "READY"
            or payment.payment_hold_state not in {"NONE", "CLEAR"}
        ):
            reasons.append("PAYMENT_PROFILE_NOT_READY")
        elif (
            payment.confidence_state not in TRUSTED_SOURCE_CONFIDENCE
            or payment.valid_until is None
            or payment.valid_until < assessment_window_end
            or payment.source_updated_at > assessment_window_end
        ):
            reasons.append("PAYMENT_SOURCE_NOT_CURRENT")
        if len(pnl) < 2:
            reasons.append("TWO_REVIEW_CYCLES_REQUIRED")
        else:
            for snapshot in pnl:
                trusted = (
                    Decimal(snapshot.locked_revenue)
                    + Decimal(snapshot.finalized_revenue)
                    + Decimal(snapshot.cash_received)
                    - Decimal(snapshot.reversed_revenue)
                )
                cost = Decimal(snapshot.direct_cost) + Decimal(
                    snapshot.allocated_ops_cost
                )
                if trusted < cost:
                    reasons.append("TRUSTED_REVENUE_BELOW_COST")
                    break
        if open_critical is not None:
            reasons.append("CRITICAL_ENFORCEMENT_OPEN")
        decision = "SELF_FUNDING" if not reasons else "FUNDED_EXPERIMENT"
        inputs = [
            str(item.id) for item in [monetization, payment, *pnl] if item is not None
        ]
        payload = {
            "schema_version": policy_version,
            "channel_workspace_id": channel_workspace_id,
            "assessment_window_end": assessment_window_end,
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
            "input_refs": inputs,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(SelfFundingAssessment).where(
                SelfFundingAssessment.channel_workspace_id == channel_workspace_id,
                SelfFundingAssessment.company_id == company_id,
                SelfFundingAssessment.assessment_window_end == assessment_window_end,
                SelfFundingAssessment.policy_version == policy_version,
            )
        )
        if existing is not None:
            if existing.assessment_hash != digest:
                raise ConflictError("SELF_FUNDING_ASSESSMENT_IMMUTABLE_CONFLICT")
            return existing
        row = SelfFundingAssessment(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            assessment_window_end=assessment_window_end,
            policy_version=policy_version,
            decision=decision,
            reason_codes=sorted(set(reasons)),
            input_refs=inputs,
            assessment_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def continuation_recommendation(
        self, *, company_id: uuid.UUID, channel_workspace_id: uuid.UUID
    ) -> dict[str, Any]:
        """Project a review action; never executes a kill or pivot decision."""

        assessment = self.session.scalar(
            select(SelfFundingAssessment)
            .where(
                SelfFundingAssessment.company_id == company_id,
                SelfFundingAssessment.channel_workspace_id == channel_workspace_id,
            )
            .order_by(SelfFundingAssessment.assessment_window_end.desc())
        )
        pnl = list(
            self.session.scalars(
                select(ChannelPnlSnapshot)
                .where(
                    ChannelPnlSnapshot.company_id == company_id,
                    ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
                )
                .order_by(ChannelPnlSnapshot.period_end.desc())
                .limit(2)
            ).all()
        )
        high_enforcement = self.session.scalar(
            select(PlatformEnforcementIncident.id).where(
                PlatformEnforcementIncident.channel_workspace_id
                == channel_workspace_id,
                PlatformEnforcementIncident.company_id == company_id,
                PlatformEnforcementIncident.state == "OPEN",
                PlatformEnforcementIncident.severity.in_({"HIGH", "CRITICAL"}),
            )
        )
        if high_enforcement is not None:
            action, reasons = "THROTTLE", ("HIGH_ENFORCEMENT_OPEN",)
        elif assessment is None or len(pnl) < 2:
            action, reasons = "THROTTLE", ("INSUFFICIENT_MATURE_BUSINESS_EVIDENCE",)
        elif all(Decimal(item.contribution_margin) < 0 for item in pnl):
            action, reasons = "KILL_REVIEW", ("TWO_NEGATIVE_CONTRIBUTION_CYCLES",)
        elif assessment.decision == "SELF_FUNDING" and all(
            Decimal(item.contribution_margin) >= 0 for item in pnl
        ):
            action, reasons = "CONTINUE", ("SELF_FUNDING_WITH_NONNEGATIVE_CYCLES",)
        elif any(Decimal(item.contribution_margin) < 0 for item in pnl):
            action, reasons = "PIVOT", ("ECONOMIC_MODEL_REVIEW_REQUIRED",)
        else:
            action, reasons = "THROTTLE", ("SELF_FUNDING_NOT_YET_PROVEN",)
        return {
            "action": action,
            "reason_codes": reasons,
            "human_decision_required": action in {"PIVOT", "KILL_REVIEW"},
            "input_refs": tuple(
                ([str(assessment.id)] if assessment is not None else [])
                + [str(item.id) for item in pnl]
            ),
        }

    def freeze_continuation_recommendation(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        evaluated_at: datetime,
        policy_version: str = "vcos.continuation-capital-review.v1",
    ) -> ContinuationCapitalReview:
        """Persist a recommendation for human review; never enact a strategy change."""

        projection = self.continuation_recommendation(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
        )
        payload = {
            "schema_version": policy_version,
            "channel_workspace_id": channel_workspace_id,
            "recommendation": projection["action"],
            "reason_codes": projection["reason_codes"],
            "input_refs": projection["input_refs"],
            "human_decision_required": projection["human_decision_required"],
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(ContinuationCapitalReview).where(
                ContinuationCapitalReview.channel_workspace_id == channel_workspace_id,
                ContinuationCapitalReview.evidence_snapshot_hash == digest,
            )
        )
        if existing is not None:
            return existing
        row = ContinuationCapitalReview(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            recommendation=projection["action"],
            reason_codes=list(projection["reason_codes"]),
            input_refs=list(projection["input_refs"]),
            evidence_snapshot_hash=digest,
            policy_version=policy_version,
            evaluated_at=evaluated_at,
            human_decision_required=projection["human_decision_required"],
        )
        self.session.add(row)
        self.session.flush()
        if row.human_decision_required:
            self._action(
                company_id=company_id,
                channel_workspace_id=channel_workspace_id,
                action_type="HUMAN_CAPITAL_REVIEW",
                target_ref=f"continuation-capital://{channel_workspace_id}",
                priority="HIGH",
                reason_code=row.recommendation,
                evidence_refs=[f"continuation-capital-review://{row.id}"],
                due_at=None,
            )
        return row

    def open_enforcement_incident(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        platform: str,
        external_incident_ref: str,
        incident_type: str,
        severity: str,
        scope: str,
        source_ref: str,
        evidence_payload: Mapping[str, Any],
        detected_at: datetime,
        uploaded_video_id: uuid.UUID | None = None,
        deadline_at: datetime | None = None,
        freeze_learning: bool = True,
    ) -> PlatformEnforcementIncident:
        severity = _enum(severity, SEVERITIES, "ENFORCEMENT_SEVERITY_INVALID")
        platform = platform.strip().upper()
        external_incident_ref = external_incident_ref.strip()
        incident_type = incident_type.strip().upper()
        scope = scope.strip().upper()
        source_ref = source_ref.strip()
        _require(
            bool(
                platform
                and external_incident_ref
                and incident_type
                and scope
                and source_ref
            ),
            "ENFORCEMENT_INCIDENT_IDENTITY_REQUIRED",
        )
        _require(
            deadline_at is None or deadline_at > detected_at,
            "ENFORCEMENT_INCIDENT_DEADLINE_INVALID",
        )
        payload = {
            "schema_version": "vcos.platform-enforcement-incident.v1",
            "company_id": company_id,
            "channel_workspace_id": channel_workspace_id,
            "uploaded_video_id": uploaded_video_id,
            "platform": platform,
            "external_incident_ref": external_incident_ref,
            "incident_type": incident_type,
            "severity": severity,
            "scope": scope,
            "freeze_learning": freeze_learning,
            "deadline_at": deadline_at,
            "source_ref": source_ref,
            "evidence_payload": dict(evidence_payload),
            "detected_at": detected_at,
        }
        existing = self.session.scalar(
            select(PlatformEnforcementIncident).where(
                PlatformEnforcementIncident.platform == platform,
                PlatformEnforcementIncident.external_incident_ref
                == external_incident_ref,
            )
        )
        if existing is not None:
            if existing.incident_hash != _hash(payload):
                raise ConflictError("ENFORCEMENT_INCIDENT_IMMUTABLE_CONFLICT")
            return existing
        row = PlatformEnforcementIncident(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            uploaded_video_id=uploaded_video_id,
            platform=platform,
            external_incident_ref=external_incident_ref,
            incident_type=incident_type,
            severity=severity,
            scope=scope,
            state="OPEN",
            freeze_learning=freeze_learning,
            deadline_at=deadline_at,
            evidence_payload=_jsonable(evidence_payload),
            source_ref=source_ref,
            incident_hash=_hash(payload),
            detected_at=detected_at,
        )
        self.session.add(row)
        self._action(
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            action_type="REVIEW_ENFORCEMENT",
            target_ref=f"platform-enforcement://{row.id}",
            priority="CRITICAL" if severity == "CRITICAL" else "HIGH",
            reason_code=f"PLATFORM_{incident_type}",
            evidence_refs=[source_ref],
            due_at=deadline_at,
        )
        self.session.flush()
        return row

    def resolve_enforcement_incident(
        self,
        *,
        incident_id: uuid.UUID,
        resolution_summary: str,
    ) -> PlatformEnforcementIncident:
        row = self.session.scalar(
            select(PlatformEnforcementIncident)
            .where(PlatformEnforcementIncident.id == incident_id)
            .with_for_update()
        )
        if row is None:
            raise NotFoundError(f"enforcement incident not found: {incident_id}")
        if row.state == "RESOLVED":
            return row
        summary = resolution_summary.strip()
        _require(bool(summary), "ENFORCEMENT_RESOLUTION_REQUIRED")
        row.state = "RESOLVED"
        row.resolution_summary = summary
        row.resolved_at = utc_now()
        self._resolve_actions(
            company_id=row.company_id,
            channel_workspace_id=row.channel_workspace_id,
            action_type="REVIEW_ENFORCEMENT",
            target_ref=f"platform-enforcement://{row.id}",
            resolved_reasons={f"PLATFORM_{row.incident_type}"},
        )
        self.session.flush()
        return row

    def create_appeal_pack(
        self,
        *,
        incident_id: uuid.UUID,
        rights_basis: str,
        evidence_items: Sequence[Mapping[str, Any]],
        timeline: Sequence[Mapping[str, Any]],
        approved_by: uuid.UUID | None = None,
    ) -> AppealEvidencePack:
        incident = self.session.get(PlatformEnforcementIncident, incident_id)
        if incident is None:
            raise NotFoundError(f"enforcement incident not found: {incident_id}")
        version = self.session.scalar(
            select(func.max(AppealEvidencePack.version_number)).where(
                AppealEvidencePack.platform_enforcement_incident_id == incident_id
            )
        )
        version_number = int(version or 0) + 1
        payload = {
            "schema_version": "vcos.appeal-evidence-pack.v1",
            "incident_id": incident_id,
            "incident_hash": incident.incident_hash,
            "version_number": version_number,
            "rights_basis": rights_basis.strip(),
            "evidence_items": list(evidence_items),
            "timeline": list(timeline),
            "approved_by": approved_by,
        }
        _require(bool(payload["rights_basis"]), "APPEAL_RIGHTS_BASIS_REQUIRED")
        _require(bool(evidence_items), "APPEAL_EVIDENCE_REQUIRED")
        row = AppealEvidencePack(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=incident.company_id,
            channel_workspace_id=incident.channel_workspace_id,
            platform_enforcement_incident_id=incident.id,
            version_number=version_number,
            rights_basis=payload["rights_basis"],
            evidence_items=_jsonable(evidence_items),
            timeline=_jsonable(timeline),
            state="READY_FOR_HUMAN" if approved_by is None else "HUMAN_APPROVED",
            approved_by=approved_by,
            approved_at=utc_now() if approved_by is not None else None,
            pack_hash=_hash(payload),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def register_affiliate_offer(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        merchant: str,
        offer_ref: str,
        product_ref: str | None,
        commission_model: Mapping[str, Any],
        attribution_window_text: str,
        terms_hash: str,
        disclosure_required: bool,
        effective_at: datetime,
        expires_at: datetime | None,
    ) -> AffiliateOfferSnapshot:
        merchant = merchant.strip()
        offer_ref = offer_ref.strip()
        attribution_window_text = attribution_window_text.strip()
        _require(
            bool(merchant and offer_ref and attribution_window_text),
            "AFFILIATE_OFFER_IDENTITY_REQUIRED",
        )
        _require(bool(commission_model), "AFFILIATE_COMMISSION_MODEL_REQUIRED")
        _require(
            re.fullmatch(r"[0-9a-f]{64}", terms_hash) is not None,
            "AFFILIATE_TERMS_HASH_INVALID",
        )
        _require(
            expires_at is None or expires_at > effective_at,
            "AFFILIATE_OFFER_WINDOW_INVALID",
        )
        payload = {
            "schema_version": "vcos.affiliate-offer-snapshot.v1",
            "channel_workspace_id": channel_workspace_id,
            "merchant": merchant,
            "offer_ref": offer_ref,
            "product_ref": product_ref,
            "commission_model": dict(commission_model),
            "attribution_window_text": attribution_window_text,
            "terms_hash": terms_hash,
            "disclosure_required": disclosure_required,
            "effective_at": effective_at,
            "expires_at": expires_at,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(AffiliateOfferSnapshot).where(
                AffiliateOfferSnapshot.channel_workspace_id == channel_workspace_id,
                AffiliateOfferSnapshot.merchant == payload["merchant"],
                AffiliateOfferSnapshot.offer_ref == payload["offer_ref"],
                AffiliateOfferSnapshot.terms_hash == terms_hash,
            )
        )
        if existing is not None:
            if existing.snapshot_hash != digest:
                raise ConflictError("AFFILIATE_OFFER_IMMUTABLE_CONFLICT")
            return existing
        row = AffiliateOfferSnapshot(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            merchant=payload["merchant"],
            offer_ref=payload["offer_ref"],
            product_ref=product_ref,
            commission_model=_jsonable(commission_model),
            attribution_window_text=payload["attribution_window_text"],
            terms_hash=terms_hash,
            disclosure_required=disclosure_required,
            effective_at=effective_at,
            expires_at=expires_at,
            state="ACTIVE",
            snapshot_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def register_affiliate_link(
        self,
        *,
        offer_snapshot_id: uuid.UUID,
        canonical_url: str,
        short_url: str | None,
        utm_policy_version: str,
    ) -> AffiliateLinkRegistry:
        offer = self.session.get(AffiliateOfferSnapshot, offer_snapshot_id)
        if offer is None:
            raise NotFoundError(f"affiliate offer not found: {offer_snapshot_id}")
        canonical_url = canonical_url.strip()
        _require(
            self._is_https_url(canonical_url), "AFFILIATE_LINK_HTTPS_REQUIRED"
        )
        if short_url is not None:
            short_url = short_url.strip()
            _require(
                self._is_https_url(short_url), "AFFILIATE_SHORT_LINK_HTTPS_REQUIRED"
            )
        utm_policy_version = utm_policy_version.strip()
        _require(bool(utm_policy_version), "AFFILIATE_UTM_POLICY_REQUIRED")
        payload = {
            "schema_version": "vcos.affiliate-link-registry.v1",
            "offer_snapshot_id": offer.id,
            "offer_hash": offer.snapshot_hash,
            "canonical_url": canonical_url,
            "short_url": short_url,
            "utm_policy_version": utm_policy_version,
            "disclosure_required": offer.disclosure_required,
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(AffiliateLinkRegistry).where(
                AffiliateLinkRegistry.channel_workspace_id
                == offer.channel_workspace_id,
                AffiliateLinkRegistry.canonical_url == canonical_url,
            )
        )
        if existing is not None:
            if existing.content_hash != digest:
                raise ConflictError("AFFILIATE_LINK_IMMUTABLE_CONFLICT")
            return existing
        row = AffiliateLinkRegistry(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=offer.company_id,
            channel_workspace_id=offer.channel_workspace_id,
            affiliate_offer_snapshot_id=offer.id,
            canonical_url=canonical_url,
            short_url=short_url,
            utm_policy_version=utm_policy_version,
            disclosure_required=offer.disclosure_required,
            state="ACTIVE",
            last_health_state="UNKNOWN",
            content_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def assess_disclosures(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
        video_project_id: uuid.UUID,
        publish_package_ref: str,
        policy_version: str,
        required_disclosures: Sequence[str],
        observed_disclosures: Sequence[str],
        link_registry_refs: Sequence[uuid.UUID],
    ) -> BusinessDisclosureAssessment:
        required = sorted({str(item) for item in required_disclosures})
        observed = sorted({str(item) for item in observed_disclosures})
        missing = sorted(set(required) - set(observed))
        link_ref_set = set(link_registry_refs)
        link_rows = (
            list(
                self.session.scalars(
                    select(AffiliateLinkRegistry).where(
                        AffiliateLinkRegistry.id.in_(list(link_ref_set))
                    )
                ).all()
            )
            if link_ref_set
            else []
        )
        _require(
            {item.id for item in link_rows} == link_ref_set,
            "AFFILIATE_LINK_NOT_FOUND",
        )
        _require(
            all(
                item.company_id == company_id
                and item.channel_workspace_id == channel_workspace_id
                for item in link_rows
            ),
            "AFFILIATE_LINK_SCOPE_MISMATCH",
        )
        reasons = [f"DISCLOSURE_MISSING:{item}" for item in missing]
        now = utc_now()
        for link in link_rows:
            offer = self.session.get(
                AffiliateOfferSnapshot, link.affiliate_offer_snapshot_id
            )
            if link.state != "ACTIVE" or link.last_health_state == "BROKEN":
                reasons.append("AFFILIATE_LINK_NOT_HEALTHY")
            if (
                offer is None
                or offer.state != "ACTIVE"
                or (offer.expires_at is not None and offer.expires_at <= now)
            ):
                reasons.append("AFFILIATE_OFFER_NOT_ACTIVE")
            if link.disclosure_required and not required:
                reasons.append("AFFILIATE_DISCLOSURE_POLICY_MISSING")
        decision = "PASS" if not reasons else "BLOCK"
        payload = {
            "schema_version": "vcos.business-disclosure-assessment.v1",
            "company_id": company_id,
            "channel_workspace_id": channel_workspace_id,
            "video_project_id": video_project_id,
            "publish_package_ref": publish_package_ref,
            "policy_version": policy_version,
            "required_disclosures": required,
            "observed_disclosures": observed,
            "link_registry_refs": sorted(str(item) for item in link_registry_refs),
            "decision": decision,
            "reason_codes": sorted(set(reasons)),
        }
        digest = _hash(payload)
        existing = self.session.scalar(
            select(BusinessDisclosureAssessment).where(
                BusinessDisclosureAssessment.assessment_hash == digest
            )
        )
        if existing is not None:
            return existing
        conflicting_scope = self.session.scalar(
            select(BusinessDisclosureAssessment).where(
                BusinessDisclosureAssessment.publish_package_ref == publish_package_ref,
                BusinessDisclosureAssessment.policy_version == policy_version,
            )
        )
        if conflicting_scope is not None:
            _require(
                conflicting_scope.company_id == company_id
                and conflicting_scope.channel_workspace_id == channel_workspace_id
                and conflicting_scope.video_project_id == video_project_id,
                "BUSINESS_DISCLOSURE_SCOPE_MISMATCH",
            )
        row = BusinessDisclosureAssessment(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            video_project_id=video_project_id,
            publish_package_ref=publish_package_ref,
            policy_version=policy_version,
            required_disclosures=required,
            observed_disclosures=observed,
            link_registry_refs=sorted(str(item) for item in link_registry_refs),
            decision=decision,
            reason_codes=sorted(set(reasons)),
            assessment_hash=digest,
        )
        self.session.add(row)
        self.session.flush()
        self._sync_disclosure_actions(row)
        return row

    def dashboard(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID,
    ) -> BusinessDashboardProjection:
        monetization = self.session.scalar(
            select(MonetizationAccountStatus)
            .where(
                MonetizationAccountStatus.company_id == company_id,
                MonetizationAccountStatus.channel_workspace_id == channel_workspace_id,
            )
            .order_by(
                MonetizationAccountStatus.source_updated_at.desc(),
                MonetizationAccountStatus.version_number.desc(),
            )
        )
        payment = self.session.scalar(
            select(PaymentProfileStatus)
            .where(PaymentProfileStatus.company_id == company_id)
            .order_by(
                PaymentProfileStatus.source_updated_at.desc(),
                PaymentProfileStatus.version_number.desc(),
            )
        )
        pnl = self.session.scalar(
            select(ChannelPnlSnapshot)
            .where(
                ChannelPnlSnapshot.company_id == company_id,
                ChannelPnlSnapshot.channel_workspace_id == channel_workspace_id,
            )
            .order_by(ChannelPnlSnapshot.period_end.desc())
        )
        self_funding = self.session.scalar(
            select(SelfFundingAssessment)
            .where(
                SelfFundingAssessment.company_id == company_id,
                SelfFundingAssessment.channel_workspace_id == channel_workspace_id,
            )
            .order_by(SelfFundingAssessment.assessment_window_end.desc())
        )
        open_count = int(
            self.session.scalar(
                select(func.count(PlatformEnforcementIncident.id)).where(
                    PlatformEnforcementIncident.channel_workspace_id
                    == channel_workspace_id,
                    PlatformEnforcementIncident.company_id == company_id,
                    PlatformEnforcementIncident.state == "OPEN",
                )
            )
            or 0
        )
        open_actions = tuple(
            self.session.scalars(
                select(BusinessActionItem)
                .where(
                    BusinessActionItem.company_id == company_id,
                    or_(
                        BusinessActionItem.channel_workspace_id == channel_workspace_id,
                        BusinessActionItem.channel_workspace_id.is_(None),
                    ),
                    BusinessActionItem.state == "OPEN",
                )
                .order_by(BusinessActionItem.created_at)
            ).all()
        )
        actions = tuple(str(item.reason_code) for item in open_actions)
        total_cost = (
            Decimal(pnl.direct_cost) + Decimal(pnl.allocated_ops_cost)
            if pnl is not None
            else Decimal("0")
        )
        return BusinessDashboardProjection(
            channel_workspace_id=channel_workspace_id,
            monetization_state=self._effective_monetization_state(monetization),
            payment_state=self._effective_payment_state(payment),
            open_enforcement_count=open_count,
            disclosure_health=(
                "ACTION_REQUIRED"
                if any(
                    item.action_type == "REMEDIATE_DISCLOSURE" for item in open_actions
                )
                else "CLEAN"
            ),
            trailing_finalized_revenue=(
                Decimal(pnl.finalized_revenue) if pnl else Decimal("0")
            ),
            trailing_cash_received=(
                Decimal(pnl.cash_received) if pnl else Decimal("0")
            ),
            trailing_cost=total_cost,
            contribution_margin=(
                Decimal(pnl.contribution_margin) if pnl else Decimal("0")
            ),
            self_funding_decision=(
                self_funding.decision if self_funding else "NOT_ASSESSED"
            ),
            next_actions=actions,
        )

    def _action(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID | None,
        action_type: str,
        target_ref: str,
        priority: str,
        reason_code: str,
        evidence_refs: Sequence[str],
        due_at: datetime | None,
    ) -> BusinessActionItem:
        payload = {
            "schema_version": "vcos.business-action-item.v1",
            "channel_workspace_id": channel_workspace_id,
            "action_type": action_type,
            "target_ref": target_ref,
            "priority": priority,
            "reason_code": reason_code,
            "evidence_refs": list(evidence_refs),
            "due_at": due_at,
        }
        existing = self.session.scalar(
            select(BusinessActionItem).where(
                BusinessActionItem.company_id == company_id,
                (
                    BusinessActionItem.channel_workspace_id.is_(None)
                    if channel_workspace_id is None
                    else BusinessActionItem.channel_workspace_id == channel_workspace_id
                ),
                BusinessActionItem.action_type == action_type,
                BusinessActionItem.target_ref == target_ref,
                BusinessActionItem.reason_code == reason_code,
            )
        )
        if existing is not None:
            existing.priority = priority
            existing.due_at = due_at
            existing.evidence_refs = list(evidence_refs)
            existing.action_hash = _hash(payload)
            if existing.state in {"RESOLVED", "DONE", "DISMISSED"}:
                existing.state = "OPEN"
            return existing
        row = BusinessActionItem(
            id=_deterministic_id(_BUSINESS_NAMESPACE, payload),
            company_id=company_id,
            channel_workspace_id=channel_workspace_id,
            action_type=action_type,
            target_ref=target_ref,
            priority=priority,
            reason_code=reason_code,
            state="OPEN",
            due_at=due_at,
            evidence_refs=list(evidence_refs),
            action_hash=_hash(payload),
        )
        self.session.add(row)
        return row

    @staticmethod
    def _payment_action_reasons(row: PaymentProfileStatus) -> set[str]:
        reasons: set[str] = set()
        if row.tax_state != "VERIFIED":
            reasons.add("PAYMENT_TAX_VERIFICATION_REQUIRED")
        if row.address_verification_state != "VERIFIED":
            reasons.add("PAYMENT_ADDRESS_VERIFICATION_REQUIRED")
        if row.payment_method_state != "READY":
            reasons.add("PAYMENT_METHOD_ACTION_REQUIRED")
        if row.payment_hold_state not in {"NONE", "CLEAR"}:
            reasons.add("PAYMENT_HOLD_OPEN")
        if (
            row.confidence_state not in TRUSTED_SOURCE_CONFIDENCE
            or row.valid_until is None
            or row.valid_until <= utc_now()
        ):
            reasons.add("PAYMENT_STATUS_STALE_OR_UNTRUSTED")
        return reasons

    @staticmethod
    def _monetization_action_reasons(row: MonetizationAccountStatus) -> set[str]:
        reasons: set[str] = set()
        if row.eligibility_state not in {"ELIGIBLE", "ACTIVE"}:
            reasons.add("MONETIZATION_UNAVAILABLE")
        if row.enrollment_state != "ACTIVE":
            reasons.add("MONETIZATION_ENROLLMENT_INCOMPLETE")
        if row.restriction_state not in {"NONE", "CLEAR"}:
            reasons.add("MONETIZATION_RESTRICTED")
        if (
            row.confidence_state not in TRUSTED_SOURCE_CONFIDENCE
            or row.valid_until is None
            or row.valid_until <= utc_now()
        ):
            reasons.add("MONETIZATION_STATUS_STALE_OR_UNTRUSTED")
        return reasons

    @classmethod
    def _effective_payment_state(cls, row: PaymentProfileStatus | None) -> str:
        if row is None:
            return "UNKNOWN"
        reasons = cls._payment_action_reasons(row)
        return "READY" if not reasons else f"ACTION_REQUIRED:{sorted(reasons)[0]}"

    @classmethod
    def _effective_monetization_state(
        cls, row: MonetizationAccountStatus | None
    ) -> str:
        if row is None:
            return "UNKNOWN"
        reasons = cls._monetization_action_reasons(row)
        return "ACTIVE" if not reasons else f"ACTION_REQUIRED:{sorted(reasons)[0]}"

    def _resolve_actions(
        self,
        *,
        company_id: uuid.UUID,
        channel_workspace_id: uuid.UUID | None,
        action_type: str,
        target_ref: str,
        resolved_reasons: set[str],
    ) -> None:
        for item in self.session.scalars(
            select(BusinessActionItem).where(
                BusinessActionItem.company_id == company_id,
                (
                    BusinessActionItem.channel_workspace_id.is_(None)
                    if channel_workspace_id is None
                    else BusinessActionItem.channel_workspace_id == channel_workspace_id
                ),
                BusinessActionItem.action_type == action_type,
                BusinessActionItem.target_ref == target_ref,
                BusinessActionItem.state.in_({"OPEN", "IN_PROGRESS"}),
            )
        ):
            if item.reason_code in resolved_reasons:
                item.state = "RESOLVED"

    def _sync_payment_actions(self, row: PaymentProfileStatus) -> None:
        target_ref = f"payment-profile://{row.company_id}"
        reasons = self._payment_action_reasons(row)
        all_reasons = {
            "PAYMENT_TAX_VERIFICATION_REQUIRED",
            "PAYMENT_ADDRESS_VERIFICATION_REQUIRED",
            "PAYMENT_METHOD_ACTION_REQUIRED",
            "PAYMENT_HOLD_OPEN",
            "PAYMENT_STATUS_STALE_OR_UNTRUSTED",
        }
        self._resolve_actions(
            company_id=row.company_id,
            channel_workspace_id=None,
            action_type="RESOLVE_PAYMENT_PROFILE",
            target_ref=target_ref,
            resolved_reasons=all_reasons - reasons,
        )
        for reason in sorted(reasons):
            self._action(
                company_id=row.company_id,
                channel_workspace_id=None,
                action_type="RESOLVE_PAYMENT_PROFILE",
                target_ref=target_ref,
                priority="HIGH",
                reason_code=reason,
                evidence_refs=[f"payment-profile-status://{row.id}"],
                due_at=row.valid_until,
            )

    def _sync_monetization_actions(self, row: MonetizationAccountStatus) -> None:
        target_ref = f"monetization-account://{row.platform}/{row.channel_workspace_id}"
        reasons = self._monetization_action_reasons(row)
        all_reasons = {
            "MONETIZATION_UNAVAILABLE",
            "MONETIZATION_ENROLLMENT_INCOMPLETE",
            "MONETIZATION_RESTRICTED",
            "MONETIZATION_STATUS_STALE_OR_UNTRUSTED",
        }
        self._resolve_actions(
            company_id=row.company_id,
            channel_workspace_id=row.channel_workspace_id,
            action_type="RESOLVE_MONETIZATION_ACCOUNT",
            target_ref=target_ref,
            resolved_reasons=all_reasons - reasons,
        )
        for reason in sorted(reasons):
            self._action(
                company_id=row.company_id,
                channel_workspace_id=row.channel_workspace_id,
                action_type="RESOLVE_MONETIZATION_ACCOUNT",
                target_ref=target_ref,
                priority="HIGH",
                reason_code=reason,
                evidence_refs=[f"monetization-account-status://{row.id}"],
                due_at=row.valid_until,
            )

    def _sync_disclosure_actions(self, row: BusinessDisclosureAssessment) -> None:
        target_ref = f"business-disclosure://{row.publish_package_ref}"
        reasons = set(row.reason_codes)
        self._resolve_actions(
            company_id=row.company_id,
            channel_workspace_id=row.channel_workspace_id,
            action_type="REMEDIATE_DISCLOSURE",
            target_ref=target_ref,
            resolved_reasons={
                item.reason_code
                for item in self.session.scalars(
                    select(BusinessActionItem).where(
                        BusinessActionItem.company_id == row.company_id,
                        BusinessActionItem.channel_workspace_id
                        == row.channel_workspace_id,
                        BusinessActionItem.action_type == "REMEDIATE_DISCLOSURE",
                        BusinessActionItem.target_ref == target_ref,
                    )
                )
            }
            - reasons,
        )
        for reason in sorted(reasons):
            self._action(
                company_id=row.company_id,
                channel_workspace_id=row.channel_workspace_id,
                action_type="REMEDIATE_DISCLOSURE",
                target_ref=target_ref,
                priority="HIGH",
                reason_code=reason,
                evidence_refs=[f"business-disclosure-assessment://{row.id}"],
                due_at=None,
            )

    @staticmethod
    def _is_https_url(value: str) -> bool:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.username is None
            and parsed.password is None
        )


@dataclass(frozen=True, slots=True)
class ArchitectureAuditResult:
    hardcoded_channel_findings: tuple[str, ...]
    niche_branch_findings: tuple[str, ...]
    superseded_surface_findings: tuple[str, ...]
    one_engine_many_profiles: bool


class ArchitectureDebtAuditService:
    """Repository-level guard for P3; deliberately excludes docs/tests/history."""

    _runtime_suffixes = {".py", ".yaml", ".yml", ".json"}
    _excluded_parts = {"tests", "docs", "alembic", ".git", "reports"}
    _audit_service_relative_path = "app/services/remaining_debt_closeout.py"
    _classified_non_runtime_surfaces = {
        "app/contracts/nich1.py": "historical policy reason-code vocabulary",
        "app/services/img_canary.py": "non-production canary fixture generator",
        "app/services/mr1_local_production.py": "sealed legacy local recovery surface",
        "app/services/mr1_real_production.py": "sealed legacy recovery surface",
        "app/services/mr1_reapproval.py": "sealed legacy reapproval surface",
        "app/services/pkg1_market_revision.py": "historical revision fixture",
        "app/services/pkg1_market_revision_closeout.py": "historical closeout fixture",
        "app/services/pkg1_sc07_sc09_revision.py": "historical revision fixture",
    }

    def audit(self, root: Path) -> ArchitectureAuditResult:
        hardcoded: list[str] = []
        niche: list[str] = []
        superseded: list[str] = []
        channel_patterns = (
            re.compile(r"Small Team AI|@SmallTeamAI", re.IGNORECASE),
            re.compile(r"\bdef\s+\w*small_team_ai\w*\(", re.IGNORECASE),
            re.compile(
                r"\b(?:if|elif)\b[^\n]*(?:channel_key|channel_name|channel)\b[^\n]*"
                r"(?:==|!=)[^\n]*[\"']small-team-ai[\"']",
                re.IGNORECASE,
            ),
            re.compile(r"\bsmall_team_ai\b", re.IGNORECASE),
            re.compile(
                r"\b[A-Z0-9_]*(?:CHANNEL|PROFILE|STRATEG(?:Y|IES))[A-Z0-9_]*\s*="
                r"\s*\{[^}]*[\"']small-team-ai[\"']",
                re.IGNORECASE | re.DOTALL,
            ),
        )
        niche_patterns = (
            re.compile(r"\bif\s+[^\n]*\bniche\s*=="),
            re.compile(r"\bmatch\s+[^\n]*\bniche\b"),
            re.compile(r"\bif\s+[^\n]*\bchannel_name\s*=="),
        )
        superseded_patterns = (
            "PodcastPipeline",
            "ShortsPipeline",
            "TopicBankItem",
            "PodcastNoViewAgent",
            "NichePipeline",
            "SmallTeamAIPipeline",
        )
        for path in sorted(root.rglob("*")):
            if (
                not path.is_file()
                or path.suffix not in self._runtime_suffixes
                or any(part in self._excluded_parts for part in path.parts)
            ):
                continue
            if not any(part in {"app", "config"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            relative = str(path.relative_to(root))
            if relative in self._classified_non_runtime_surfaces:
                continue
            if relative == self._audit_service_relative_path:
                text = self._without_audit_detector_source(text)
            if any(pattern.search(text) for pattern in channel_patterns):
                hardcoded.append(relative)
            if any(pattern.search(text) for pattern in niche_patterns):
                niche.append(relative)
            if any(marker in text for marker in superseded_patterns):
                superseded.append(relative)
        return ArchitectureAuditResult(
            hardcoded_channel_findings=tuple(hardcoded),
            niche_branch_findings=tuple(niche),
            superseded_surface_findings=tuple(superseded),
            one_engine_many_profiles=not (hardcoded or niche or superseded),
        )

    @staticmethod
    def _without_audit_detector_source(text: str) -> str:
        """Remove only this audit class's detector vocabulary from its own file.

        The surrounding closeout services remain scanned, so a real executor
        violation added alongside the detector is still a hard finding.
        """

        try:
            tree = ast.parse(text)
        except SyntaxError:
            return text
        lines = text.splitlines(keepends=True)
        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef)
                and node.name == "ArchitectureDebtAuditService"
            ):
                for index in range(node.lineno - 1, node.end_lineno):
                    lines[index] = "\n"
        return "".join(lines)

    @staticmethod
    def code_isolation_proof(result: ArchitectureAuditResult) -> dict[str, Any]:
        """Report code isolation separately from evidence only live channels supply."""

        return {
            "state": (
                "CODE_ISOLATION_PROVEN"
                if result.one_engine_many_profiles
                else "CODE_ISOLATION_NOT_PROVEN"
            ),
            "blocking_findings": tuple(
                sorted(
                    set(
                        result.hardcoded_channel_findings
                        + result.niche_branch_findings
                        + result.superseded_surface_findings
                    )
                )
            ),
            "live_portfolio_state": "LIVE_PORTFOLIO_PROOF_NOT_PROVEN",
        }

    @staticmethod
    def portfolio_proof(
        *,
        verified_publications_by_channel: Mapping[uuid.UUID, int],
        compiled_profile_hash_by_channel: Mapping[uuid.UUID, str],
        code_audit: ArchitectureAuditResult,
    ) -> dict[str, Any]:
        """Report portfolio readiness without manufacturing external proof.

        The caller supplies only code-local aggregates.  Even when they show
        multiple channels and distinct snapshots, they cannot establish that
        those publications were verified by a live platform.  That evidence is
        intentionally retained as a separate operational checkpoint.
        """

        proven_channels = {
            channel_id
            for channel_id, count in verified_publications_by_channel.items()
            if count > 0 and compiled_profile_hash_by_channel.get(channel_id)
        }
        profile_hashes = {
            compiled_profile_hash_by_channel[channel_id]
            for channel_id in proven_channels
        }
        return {
            "state": "NOT_PROVEN",
            "code_isolation_state": (
                "CODE_ISOLATION_PROVEN"
                if code_audit.one_engine_many_profiles
                else "CODE_ISOLATION_NOT_PROVEN"
            ),
            "live_portfolio_state": "LIVE_PORTFOLIO_PROOF_NOT_PROVEN",
            "verified_channel_count": len(proven_channels),
            "distinct_profile_count": len(profile_hashes),
            "live_provider_proof_required": True,
        }


class RemainingDebtCloseoutCoordinator:
    """Deterministic projection invoked after verified public publication.

    It never calls a platform.  Legacy series without an active D15 arc remain
    publishable; they surface as live-migration work rather than being silently
    reinterpreted.
    """

    def __init__(self, session: Session):
        self.session = session

    def on_publication_verified(
        self,
        *,
        candidate: Any,
        public_receipt: Any,
        observed_at: datetime,
        compiled_policy_snapshot_hash: str,
    ) -> dict[str, str | None]:
        learning = LearningAuthorityService(self.session)
        _require(
            re.fullmatch(r"[0-9a-f]{64}", compiled_policy_snapshot_hash) is not None,
            "COMPILED_POLICY_SNAPSHOT_HASH_INVALID",
        )
        market = dict(getattr(candidate, "target_market_lineage", {}) or {})
        source_ref = f"public-publication-receipt://{public_receipt.id}"
        fingerprint = learning.create_fingerprint(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            source_entity_ref=source_ref,
            content_product_type=str(
                getattr(candidate, "content_product_type", None)
                or getattr(candidate, "content_mode", "EDITORIAL_NARRATED_VIDEO")
            ),
            series_plan_id=getattr(candidate, "series_plan_id", None),
            profile_snapshot_hash=compiled_policy_snapshot_hash,
            target_market=str(
                market.get("primary_market") or market.get("target_market") or "UNKNOWN"
            ),
            content_language=str(
                market.get("content_language") or market.get("locale") or "UNKNOWN"
            ),
            format_key=str(getattr(candidate, "content_mode", "STANDALONE")),
            normalized_features={
                "production_lane": getattr(candidate, "production_lane", None),
                "content_mode": getattr(candidate, "content_mode", None),
                "series_plan_id": getattr(candidate, "series_plan_id", None),
                "target_surface": getattr(candidate, "target_surface", None),
            },
        )
        delivery = learning.create_audience_delivery_plan(
            company_id=candidate.company_id,
            channel_workspace_id=candidate.channel_workspace_id,
            video_project_id=candidate.video_project_id,
            publication_receipt_id=public_receipt.id,
            target_markets=[
                str(
                    market.get("primary_market")
                    or market.get("target_market")
                    or "UNKNOWN"
                )
            ],
            target_languages=[
                str(market.get("content_language") or market.get("locale") or "UNKNOWN")
            ],
            packaging_refs=[
                "production-package://"
                f"{getattr(candidate, 'production_package_hash', '')}"
            ],
            playlist_refs=[],
        )
        ordinal_id: str | None = None
        series_plan_id = getattr(candidate, "series_plan_id", None)
        if series_plan_id is not None:
            active_arc = self.session.scalar(
                select(SeriesArcVersion).where(
                    SeriesArcVersion.series_plan_id == series_plan_id,
                    SeriesArcVersion.state.in_({"ACTIVE", "COMPLETION_PENDING"}),
                )
            )
            blueprint = self.session.scalar(
                select(SeriesEpisodeBlueprint).where(
                    SeriesEpisodeBlueprint.series_plan_id == series_plan_id,
                    SeriesEpisodeBlueprint.video_project_id
                    == candidate.video_project_id,
                )
            )
            if active_arc is not None and blueprint is not None:
                ordinal = SeriesAuthorityService(self.session).record_publication(
                    series_plan_id=series_plan_id,
                    publication_receipt_id=public_receipt.id,
                    video_project_id=candidate.video_project_id,
                    published_at=observed_at,
                    technical_attempt_ref=str(
                        getattr(candidate, "workflow_run_id", "") or ""
                    ),
                    blueprint_id=blueprint.id,
                )
                ordinal_id = str(ordinal.id)
        self.session.flush()
        return {
            "learning_fingerprint_id": str(fingerprint.id),
            "audience_delivery_plan_id": str(delivery.id),
            "series_public_ordinal_id": ordinal_id,
        }
