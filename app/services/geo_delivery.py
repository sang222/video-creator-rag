from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.geo_delivery import (
    ActualPublishDestination,
    AdsOnlyMonetizationPolicy,
    AnalyticsConfidenceState,
    ComparableVideoGeoSignal,
    DeliveryVerdict,
    DestinationRuntimeContract,
    EffectiveAdsOnlyPolicyArtifact,
    GEO_DELIVERY_ACCEPTANCE_GATES,
    GeoAlignmentState,
    GeoAnalyticsInput,
    GeoDiagnosticResult,
    GeoDeliveryAcceptanceEvidenceSet,
    GeoDeliveryAcceptanceGateResult,
    GeoDeliveryAcceptanceVerdicts,
    GeoDeliveryArtifactRef,
    GeoDeliveryImmutableEvidenceRef,
    GeoDeliveryNoExecutionProof,
    GeoDeliveryVerificationManifest,
    GeoDeliveryVerificationReceipt,
    GeoDistributionTracker,
    GeoMarketDeliveryCloseoutEvidence,
    MarketDeliveryAlignmentResult,
    MarketDeliveryEvidence,
    MarketDeliveryReasonCode,
    MetricDataState,
    PlatformRevenueType,
    SelfFundingResult,
    SelfFundingWindow,
    StrictMarketLineageEnvelope,
    StrictMarketLineageResult,
    geo_delivery_hash,
)
from app.contracts.geo_market import DestinationBinding
from app.contracts.workflow import ArtifactCreate, ArtifactVersionCreate
from app.core.errors import ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    VideoProject,
)
from app.services.workflow import ArtifactService, deterministic_artifact_content_hash
from app.services.geo_delivery_verification import (
    geo_delivery_workspace_hash,
    validate_geo_delivery_verification_scope,
)


GEO_METRIC_FIELDS: tuple[str, ...] = (
    "views_by_geo",
    "watch_time_by_geo",
    "average_view_duration_by_geo",
    "subscribers_gained_by_geo",
    "impressions_by_geo",
    "estimated_monetized_playbacks_by_geo",
    "revenue_by_geo",
    "traffic_source_by_geo",
    "subtitle_audio_language_usage",
)
GEO_DELIVERY_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def destination_binding_runtime_id(
    binding: DestinationBinding,
    *,
    canonical_ref: str | None = None,
) -> uuid.UUID:
    """Derive a stable id without changing the immutable v1 binding payload."""

    ref = canonical_ref or (
        f"destination-binding://{binding.channel_key}/v{binding.binding_version}"
    )
    return uuid.uuid5(uuid.NAMESPACE_URL, f"vcos:{ref}")


def destination_runtime_contract(
    binding: DestinationBinding,
    *,
    canonical_ref: str | None = None,
    verification_method: str | None = None,
) -> DestinationRuntimeContract:
    """Adapt the existing DestinationBinding; never rewrites its approved hash."""

    method = verification_method
    if binding.destination_status == "VERIFIED" and not method:
        method = "EXISTING_BINDING_VERIFICATION_EVIDENCE"
    return DestinationRuntimeContract(
        destination_binding_id=destination_binding_runtime_id(
            binding, canonical_ref=canonical_ref
        ),
        channel_workspace_id=binding.channel_id,
        platform=binding.platform,
        platform_account_ref=binding.platform_account_ref,
        platform_channel_id=binding.platform_channel_id,
        handle=binding.channel_handle,
        account_country_region=binding.account_country,
        default_language=binding.original_language,
        status=binding.destination_status,
        verified_at=binding.verification_timestamp,
        verification_method=method,
        binding_fingerprint=str(binding.content_hash),
    )


def market_policy_hash(*, policy_snapshot_id: uuid.UUID, market_slice: Any) -> str:
    """Hash an exact market-policy slice while retaining its snapshot authority."""

    return geo_delivery_hash(
        {
            "schema_version": "geo-delivery.market-policy-fingerprint.v1",
            "policy_snapshot_id": str(policy_snapshot_id),
            "market_slice": market_slice,
        }
    )


class MarketDeliveryAlignmentGate:
    """Deterministic final-delivery market alignment over exact bound evidence."""

    def evaluate(
        self, evidence: MarketDeliveryEvidence
    ) -> MarketDeliveryAlignmentResult:
        expected_locale = evidence.expected_locale
        expected_language = evidence.expected_content_language
        accepted_geos = {value.upper() for value in evidence.acceptable_visual_geos}
        preferred_jurisdictions = {
            value.upper() for value in evidence.preferred_source_jurisdictions
        }
        source_jurisdictions = {
            value.upper() for value in evidence.source_jurisdictions
        }
        visual_geos = {value.upper() for value in evidence.visual_geos}
        window_timezone = str(evidence.approved_publish_window.get("timezone") or "")

        checks = {
            "script_locale": evidence.script_locale == expected_locale,
            "voice_locale": evidence.voice_locale == expected_locale,
            "voice_language": (evidence.voice_content_language == expected_language),
            "metadata_locale": evidence.metadata_locale == expected_locale,
            "metadata_language": (
                evidence.metadata_original_language == expected_language
                and evidence.metadata_original_language
                == evidence.voice_content_language
            ),
            "caption_locale": expected_locale in evidence.caption_locales,
            "caption_authority": (
                evidence.caption_plan_state == "WAITING_FOR_FINAL_AUDIO_ALIGNMENT"
                and evidence.caption_artifact_ref is None
            )
            or (
                evidence.caption_plan_state == "FINALIZED"
                and bool(evidence.caption_artifact_ref)
            ),
            "currency": all(
                value == evidence.expected_currency
                for value in evidence.currency_contexts
            ),
            "unit_system": evidence.unit_system == evidence.expected_unit_system,
            "date_format": evidence.date_format == evidence.expected_date_format,
            "terminology": evidence.terminology_localized,
            "source_jurisdiction": bool(source_jurisdictions & preferred_jurisdictions),
            "local_examples": evidence.local_examples_present,
            "visual_geo": not visual_geos or visual_geos.issubset(accepted_geos),
            "ui_language": all(
                locale in {expected_locale, expected_language}
                for locale in evidence.ui_locales
            ),
            "destination": evidence.destination_market == evidence.expected_market,
            "publish_timezone": (
                evidence.publish_timezone == evidence.expected_timezone
                and bool(evidence.approved_publish_window)
                and window_timezone == evidence.expected_timezone
            ),
            "translated_copy": not evidence.translated_sounding_copy,
        }

        reasons: list[MarketDeliveryReasonCode] = []
        if not checks["script_locale"] or not checks["voice_language"]:
            reasons.append(MarketDeliveryReasonCode.MARKET_LANGUAGE_MISMATCH)
        if not checks["voice_locale"]:
            reasons.append(MarketDeliveryReasonCode.VOICE_LOCALE_MISMATCH)
        if (
            not checks["metadata_locale"]
            or not checks["metadata_language"]
            or not checks["caption_locale"]
            or not checks["caption_authority"]
            or not checks["ui_language"]
        ):
            reasons.append(MarketDeliveryReasonCode.METADATA_LOCALE_MISMATCH)
        if not checks["currency"]:
            reasons.append(MarketDeliveryReasonCode.CURRENCY_CONTEXT_MISMATCH)
        if not checks["unit_system"] or not checks["date_format"]:
            reasons.append(MarketDeliveryReasonCode.UNIT_SYSTEM_MISMATCH)
        if not checks["source_jurisdiction"]:
            reasons.append(MarketDeliveryReasonCode.SOURCE_JURISDICTION_MISMATCH)
        if not checks["visual_geo"]:
            reasons.append(MarketDeliveryReasonCode.VISUAL_GEO_MISMATCH)
        if not checks["publish_timezone"]:
            reasons.append(MarketDeliveryReasonCode.PUBLISH_TIMEZONE_MISMATCH)
        if not checks["destination"]:
            reasons.append(MarketDeliveryReasonCode.DESTINATION_MARKET_MISMATCH)
        if (
            not checks["terminology"]
            or not checks["local_examples"]
            or not checks["translated_copy"]
        ):
            reasons.append(MarketDeliveryReasonCode.LOCALIZATION_FEELS_TRANSLATED)

        reasons = list(dict.fromkeys(reasons))
        warn_only = {MarketDeliveryReasonCode.LOCALIZATION_FEELS_TRANSLATED}
        hard_reasons = [reason for reason in reasons if reason not in warn_only]
        verdict = (
            DeliveryVerdict.BLOCK
            if hard_reasons
            else DeliveryVerdict.WARN
            if reasons
            else DeliveryVerdict.PASS
        )
        refs = [
            evidence.target_market_profile_ref,
            evidence.market_alignment_dossier_ref,
            evidence.creative_brief_ref,
            evidence.research_pack_ref,
            evidence.script_ref,
            evidence.voice_manifest_ref,
            evidence.visual_plan_ref,
            evidence.metadata_package_ref,
            evidence.caption_plan_ref,
            evidence.thumbnail_brief_ref,
            evidence.publish_package_ref,
        ]
        if evidence.caption_artifact_ref is not None:
            refs.append(evidence.caption_artifact_ref)
        return MarketDeliveryAlignmentResult(
            policy_snapshot_id=evidence.policy_snapshot_id,
            market_policy_hash=evidence.market_policy_hash,
            destination_binding_id=evidence.destination_binding_id,
            destination_binding_fingerprint=(evidence.destination_binding_fingerprint),
            verdict=verdict,
            reason_codes=reasons,
            checks=checks,
            evidence_refs=refs,
            exact_next_action=(
                None
                if verdict == DeliveryVerdict.PASS
                else "Revise the mismatched delivery fields and obtain a new exact package approval."
                if verdict == DeliveryVerdict.BLOCK
                else "Complete human localization review before publish approval."
            ),
        )


class StrictMarketLineageService:
    """Enforce approved market/destination/package truth at manual publication."""

    def validate_handoff_context(
        self,
        *,
        envelope: StrictMarketLineageEnvelope,
        destination: DestinationRuntimeContract,
        project_policy_snapshot_id: uuid.UUID,
        target_platform: str,
    ) -> list[str]:
        reasons: list[str] = []
        if envelope.policy_snapshot_id != project_policy_snapshot_id:
            reasons.append("POLICY_SNAPSHOT_MISMATCH")
        if envelope.destination_binding_id != destination.destination_binding_id:
            reasons.append("DESTINATION_BINDING_ID_MISMATCH")
        if envelope.approved_destination_fingerprint != destination.binding_fingerprint:
            reasons.append("DESTINATION_BINDING_FINGERPRINT_MISMATCH")
        if (
            envelope.approved_platform != target_platform
            or destination.platform != target_platform
        ):
            reasons.append("DESTINATION_PLATFORM_MISMATCH")
        if destination.status != "VERIFIED":
            reasons.append("DESTINATION_NOT_VERIFIED")
        if not destination.platform_channel_id:
            reasons.append("DESTINATION_PLATFORM_CHANNEL_ID_MISSING")
        if envelope.approved_platform_channel_id != destination.platform_channel_id:
            reasons.append("APPROVED_DESTINATION_CHANNEL_MISMATCH")
        return list(dict.fromkeys(reasons))

    def validate_approval_record(
        self,
        *,
        envelope: StrictMarketLineageEnvelope,
        approval: Any,
        approved_package_version: Any,
        approved_package_artifact: Any,
        expected_video_project_id: uuid.UUID,
    ) -> None:
        if approval is None or approval.id != envelope.approval_decision_id:
            raise ValidationFailureError("STRICT_MARKET_APPROVAL_MISSING")
        if str(approval.decision).lower() != "approved":
            raise ValidationFailureError("STRICT_MARKET_APPROVAL_NOT_APPROVED")
        if getattr(approval, "target_type", None) != "artifact_version":
            raise ValidationFailureError("STRICT_MARKET_APPROVAL_TARGET_TYPE_MISMATCH")
        target_version_id = getattr(approval, "target_artifact_version_id", None)
        if target_version_id is None:
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_TARGET_PACKAGE_VERSION_MISSING"
            )
        if (
            approved_package_version is None
            or getattr(approved_package_version, "id", None) != target_version_id
            or getattr(approval, "target_id", None) != target_version_id
            or getattr(approved_package_version, "content_hash", None)
            != envelope.approved_package_hash
        ):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_TARGET_PACKAGE_VERSION_MISMATCH"
            )
        if (
            approved_package_artifact is None
            or getattr(approved_package_version, "artifact_id", None)
            != getattr(approved_package_artifact, "id", None)
            or getattr(approved_package_artifact, "video_project_id", None)
            != expected_video_project_id
        ):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_PACKAGE_PROJECT_MISMATCH"
            )

        # Strict market publication is opt-in, but once selected its approval
        # record must carry every immutable binding as first-class data.  Do
        # not infer missing bindings from whichever snapshot happens to be
        # active at publish time.
        required_columns = {
            "policy_snapshot_id": envelope.policy_snapshot_id,
            "destination_binding_id": envelope.destination_binding_id,
            "destination_binding_fingerprint": (
                envelope.approved_destination_fingerprint
            ),
            "market_policy_hash": envelope.approved_market_policy_hash,
            "approved_package_hash": envelope.approved_package_hash,
            "target_market_profile_ref": envelope.target_market_profile_ref,
            "target_market_profile_hash": envelope.target_market_profile_hash,
            "market_alignment_dossier_ref": envelope.market_alignment_dossier_ref,
            "market_alignment_dossier_hash": (envelope.market_alignment_dossier_hash),
            "approved_publish_window": envelope.approved_publish_window,
        }
        for field_name, expected in required_columns.items():
            actual = getattr(approval, field_name, None)
            if actual is None:
                raise ValidationFailureError(
                    f"STRICT_MARKET_APPROVAL_{field_name.upper()}_MISSING"
                )
            mismatch = (
                actual != expected
                if isinstance(expected, (dict, list))
                else str(actual) != str(expected)
            )
            if mismatch:
                raise ValidationFailureError(
                    f"STRICT_MARKET_APPROVAL_{field_name.upper()}_MISMATCH"
                )

        metadata = approval.metadata_ or {}
        approved_hash = metadata.get("package_content_hash") or metadata.get(
            "approved_package_hash"
        )
        if approved_hash != envelope.approved_package_hash:
            raise ValidationFailureError("STRICT_MARKET_APPROVED_PACKAGE_HASH_MISMATCH")
        if str(metadata.get("package_artifact_version_id")) != str(target_version_id):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_PACKAGE_VERSION_METADATA_MISMATCH"
            )

        metadata_bindings = {
            "effective_market_policy_hash": envelope.approved_market_policy_hash,
            "destination_binding_id": str(envelope.destination_binding_id),
            "market_alignment_dossier_ref": envelope.market_alignment_dossier_ref,
            "market_alignment_dossier_hash": envelope.market_alignment_dossier_hash,
            "approved_publish_timezone": envelope.approved_publish_timezone,
            "approved_publish_window": envelope.approved_publish_window,
        }
        for field_name, expected in metadata_bindings.items():
            actual = metadata.get(field_name)
            if actual is None:
                raise ValidationFailureError(
                    f"STRICT_MARKET_APPROVAL_METADATA_{field_name.upper()}_MISSING"
                )
            mismatch = (
                actual != expected
                if isinstance(expected, (dict, list))
                else str(actual) != str(expected)
            )
            if mismatch:
                raise ValidationFailureError(
                    f"STRICT_MARKET_APPROVAL_METADATA_{field_name.upper()}_MISMATCH"
                )

        policy_basis = approval.policy_basis or {}
        snapshot = policy_basis.get("compiled_channel_policy_snapshot") or {}
        if not snapshot.get("id"):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_POLICY_SNAPSHOT_MISSING"
            )
        if str(snapshot.get("id")) != str(envelope.policy_snapshot_id):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_POLICY_SNAPSHOT_MISMATCH"
            )

        target_profile = policy_basis.get("target_market_profile") or {}
        if not target_profile:
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_TARGET_MARKET_PROFILE_MISSING"
            )
        if (
            target_profile.get("ref") != envelope.target_market_profile_ref
            or target_profile.get("content_hash") != envelope.target_market_profile_hash
        ):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_TARGET_MARKET_PROFILE_MISMATCH"
            )

        destination = policy_basis.get("destination_binding") or {}
        if not destination:
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_DESTINATION_BINDING_MISSING"
            )
        if (
            str(destination.get("id")) != str(envelope.destination_binding_id)
            or destination.get("content_hash")
            != envelope.approved_destination_fingerprint
        ):
            raise ValidationFailureError("STRICT_MARKET_APPROVAL_DESTINATION_MISMATCH")

        dossier = policy_basis.get("market_alignment_dossier") or {}
        if not dossier:
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_MARKET_ALIGNMENT_DOSSIER_MISSING"
            )
        if (
            dossier.get("ref") != envelope.market_alignment_dossier_ref
            or dossier.get("content_hash") != envelope.market_alignment_dossier_hash
        ):
            raise ValidationFailureError(
                "STRICT_MARKET_APPROVAL_MARKET_ALIGNMENT_DOSSIER_MISMATCH"
            )

    def verify(
        self,
        *,
        envelope: StrictMarketLineageEnvelope,
        actual: ActualPublishDestination,
    ) -> StrictMarketLineageResult:
        reasons: list[str] = []
        if envelope.approved_market_policy_hash != actual.published_market_policy_hash:
            reasons.append("MARKET_POLICY_HASH_MISMATCH")
        if envelope.destination_binding_id != actual.destination_binding_id:
            reasons.append("DESTINATION_BINDING_ID_MISMATCH")
        if (
            envelope.approved_destination_fingerprint
            != actual.destination_binding_fingerprint
        ):
            reasons.append("DESTINATION_BINDING_FINGERPRINT_MISMATCH")
        if envelope.approved_platform != actual.platform:
            reasons.append("DESTINATION_PLATFORM_MISMATCH")
        if not envelope.approved_platform_channel_id:
            reasons.append("APPROVED_DESTINATION_UNVERIFIED")
        elif envelope.approved_platform_channel_id != actual.platform_channel_id:
            reasons.append("DESTINATION_PLATFORM_CHANNEL_MISMATCH")
        if actual.destination_status != "VERIFIED":
            reasons.append("DESTINATION_NOT_VERIFIED")
        if envelope.approved_package_hash != actual.published_package_hash:
            reasons.append("APPROVED_PACKAGE_HASH_MISMATCH")
        parsed_url = urlparse(actual.external_video_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            reasons.append("EXTERNAL_VIDEO_URL_INVALID")
        reasons = list(dict.fromkeys(reasons))
        verdict = DeliveryVerdict.BLOCK if reasons else DeliveryVerdict.PASS
        return StrictMarketLineageResult(
            verdict=verdict,
            reason_codes=reasons,
            approved_market_policy_hash=envelope.approved_market_policy_hash,
            published_market_policy_hash=actual.published_market_policy_hash,
            approved_destination_binding_id=envelope.destination_binding_id,
            actual_destination_binding_id=actual.destination_binding_id,
            approved_destination_fingerprint=(
                envelope.approved_destination_fingerprint
            ),
            actual_destination_fingerprint=(actual.destination_binding_fingerprint),
            exact_next_action=(
                "Accept the manual publish confirmation."
                if verdict == DeliveryVerdict.PASS
                else "Supersede the package and obtain a new exact human approval before accepting publication."
            ),
        )


class GeoDistributionTrackerService:
    def build(
        self,
        *,
        analytics: GeoAnalyticsInput,
        destination_binding_id: uuid.UUID,
        destination_binding_fingerprint: str,
        market_policy_hash: str,
        target_market_profile_ref: str,
        target_market_profile_hash: str,
        expected_primary_geos: list[str],
        acceptable_spillover_geos: list[str],
        target_share_threshold: float = 0.5,
    ) -> GeoDistributionTracker:
        if not 0 <= target_share_threshold <= 1:
            raise ValueError("TARGET_GEO_SHARE_THRESHOLD_INVALID")
        unavailable = set(analytics.unavailable_metrics)
        metric_states: dict[str, MetricDataState] = {}
        for name in GEO_METRIC_FIELDS:
            value = getattr(analytics, name)
            if value is None:
                metric_states[name] = (
                    MetricDataState.UNAVAILABLE
                    if name in unavailable
                    else MetricDataState.INSUFFICIENT_DATA
                )
            elif not value:
                metric_states[name] = MetricDataState.INSUFFICIENT_DATA
            else:
                metric_states[name] = MetricDataState.AVAILABLE

        target_share = self._target_geo_share(
            analytics.views_by_geo, expected_primary_geos
        )
        state = self._alignment_state(
            analytics.confidence_state,
            target_share=target_share,
            threshold=target_share_threshold,
        )
        reasons: list[str] = []
        if target_share is None:
            reasons.append("GEO_DATA_INSUFFICIENT")
        if state == GeoAlignmentState.GEO_DRIFT_DIRECTIONAL:
            reasons.append("GEO_DRIFT_DIRECTIONAL")
        elif state == GeoAlignmentState.GEO_MISMATCH_STABLE:
            reasons.append("TARGET_GEO_MISMATCH")
        elif state == GeoAlignmentState.ACTION_READY:
            reasons.extend(["TARGET_GEO_MISMATCH", "GEO_ACTION_READY"])
        if analytics.processing_or_policy_incident:
            reasons.append("PROCESSING_OR_POLICY_INCIDENT")
        if analytics.destination_enforcement_incident:
            reasons.append("DESTINATION_ENFORCEMENT_INCIDENT")
        action_allowed = bool(
            state == GeoAlignmentState.ACTION_READY
            and not analytics.processing_or_policy_incident
            and not analytics.destination_enforcement_incident
        )
        return GeoDistributionTracker(
            uploaded_video_id=analytics.uploaded_video_id,
            channel_workspace_id=analytics.channel_workspace_id,
            destination_binding_id=destination_binding_id,
            destination_binding_fingerprint=destination_binding_fingerprint,
            policy_snapshot_id=analytics.policy_snapshot_id,
            market_policy_hash=market_policy_hash,
            target_market_profile_ref=target_market_profile_ref,
            target_market_profile_hash=target_market_profile_hash,
            analytics_snapshot_id=analytics.analytics_snapshot_id,
            expected_primary_geos=[value.upper() for value in expected_primary_geos],
            acceptable_spillover_geos=[
                value.upper() for value in acceptable_spillover_geos
            ],
            latest_window=analytics.observation_window,
            latest_alignment_state=state,
            latest_confidence_state=analytics.confidence_state,
            views_by_geo=analytics.views_by_geo,
            watch_time_by_geo=analytics.watch_time_by_geo,
            average_view_duration_by_geo=(analytics.average_view_duration_by_geo),
            subscribers_gained_by_geo=analytics.subscribers_gained_by_geo,
            impressions_by_geo=analytics.impressions_by_geo,
            estimated_monetized_playbacks_by_geo=(
                analytics.estimated_monetized_playbacks_by_geo
            ),
            revenue_by_geo=analytics.revenue_by_geo,
            traffic_source_by_geo=analytics.traffic_source_by_geo,
            subtitle_audio_language_usage=(analytics.subtitle_audio_language_usage),
            metric_states=metric_states,
            target_geo_share=target_share,
            reason_codes=list(dict.fromkeys(reasons)),
            action_allowed=action_allowed,
        )

    @staticmethod
    def _target_geo_share(
        views_by_geo: dict[str, float] | None,
        expected_primary_geos: Iterable[str],
    ) -> float | None:
        if not views_by_geo:
            return None
        total = sum(views_by_geo.values())
        if total <= 0:
            return None
        expected = {value.upper() for value in expected_primary_geos}
        target = sum(
            value for geo, value in views_by_geo.items() if geo.upper() in expected
        )
        return target / total

    @staticmethod
    def _alignment_state(
        confidence: AnalyticsConfidenceState,
        *,
        target_share: float | None,
        threshold: float,
    ) -> GeoAlignmentState:
        if (
            confidence
            in {
                AnalyticsConfidenceState.TOO_EARLY,
                AnalyticsConfidenceState.WEAK_SIGNAL,
            }
            or target_share is None
        ):
            return GeoAlignmentState.INSUFFICIENT_DATA
        if target_share >= threshold:
            return GeoAlignmentState.GEO_ON_TRACK
        if confidence == AnalyticsConfidenceState.DIRECTIONAL:
            return GeoAlignmentState.GEO_DRIFT_DIRECTIONAL
        if confidence == AnalyticsConfidenceState.STABLE:
            return GeoAlignmentState.GEO_MISMATCH_STABLE
        return GeoAlignmentState.ACTION_READY


class GeoMaturityDiagnosticService:
    def evaluate(
        self,
        *,
        tracker: GeoDistributionTracker,
        comparable_signals: list[ComparableVideoGeoSignal] | None = None,
        profile_family_ref: str | None = None,
        policy_family_ref: str | None = None,
        drift_signature: str | None = None,
        minimum_comparable_videos: int = 3,
    ) -> GeoDiagnosticResult:
        signals = comparable_signals or []
        video_reasons: list[str] = []
        channel_reasons: list[str] = []
        incidents = {
            "PROCESSING_OR_POLICY_INCIDENT",
            "DESTINATION_ENFORCEMENT_INCIDENT",
        } & set(tracker.reason_codes)
        if (
            tracker.latest_confidence_state == AnalyticsConfidenceState.DIRECTIONAL
            and tracker.latest_alignment_state
            == GeoAlignmentState.GEO_DRIFT_DIRECTIONAL
        ):
            video_reasons.append("GEO_DRIFT_DIRECTIONAL")
        elif (
            tracker.latest_confidence_state
            in {
                AnalyticsConfidenceState.STABLE,
                AnalyticsConfidenceState.ACTION_READY,
            }
            and tracker.latest_alignment_state
            in {
                GeoAlignmentState.GEO_MISMATCH_STABLE,
                GeoAlignmentState.ACTION_READY,
            }
            and not incidents
        ):
            video_reasons.append("TARGET_GEO_MISMATCH")

        qualified = self._qualified_signals(
            signals,
            profile_family_ref=profile_family_ref,
            policy_family_ref=policy_family_ref,
            drift_signature=drift_signature,
        )
        if (
            tracker.latest_confidence_state == AnalyticsConfidenceState.ACTION_READY
            and tracker.latest_alignment_state == GeoAlignmentState.ACTION_READY
            and not incidents
            and len(qualified) >= minimum_comparable_videos
        ):
            channel_reasons.append("PROFILE_MARKET_MISMATCH")

        action_allowed = bool(
            tracker.latest_confidence_state == AnalyticsConfidenceState.ACTION_READY
            and not incidents
            and (video_reasons or channel_reasons)
        )
        if channel_reasons:
            next_action = "Create a human-reviewed profile market change proposal."
        elif video_reasons:
            next_action = (
                "Prepare a video-level market delivery correction for human review."
                if action_allowed
                else "Keep the current profile and monitor the next mature window."
            )
        else:
            next_action = "Wait for mature geo evidence; do not change strategy."
        return GeoDiagnosticResult(
            video_reason_codes=video_reasons,
            channel_reason_codes=channel_reasons,
            comparable_video_count=len(qualified),
            confidence_state=tracker.latest_confidence_state,
            action_allowed=action_allowed,
            exact_next_action=next_action,
        )

    @staticmethod
    def _qualified_signals(
        signals: list[ComparableVideoGeoSignal],
        *,
        profile_family_ref: str | None,
        policy_family_ref: str | None,
        drift_signature: str | None,
    ) -> list[ComparableVideoGeoSignal]:
        if not profile_family_ref or not policy_family_ref or not drift_signature:
            return []
        result: list[ComparableVideoGeoSignal] = []
        seen: set[uuid.UUID] = set()
        for signal in signals:
            if signal.uploaded_video_id in seen:
                continue
            if (
                signal.profile_family_ref == profile_family_ref
                and signal.policy_family_ref == policy_family_ref
                and signal.drift_signature == drift_signature
                and signal.confidence_state
                in {
                    AnalyticsConfidenceState.STABLE,
                    AnalyticsConfidenceState.ACTION_READY,
                }
                and signal.alignment_state
                in {
                    GeoAlignmentState.GEO_MISMATCH_STABLE,
                    GeoAlignmentState.ACTION_READY,
                }
                and not signal.processing_or_policy_incident
                and not signal.destination_enforcement_incident
            ):
                result.append(signal)
                seen.add(signal.uploaded_video_id)
        return result


class AdsOnlyMonetizationPolicyService:
    def compile_effective_overlay(
        self,
        *,
        base_policy_snapshot_id: uuid.UUID,
        base_policy_snapshot_hash: str,
        overlay_authority_ref: str,
    ) -> AdsOnlyMonetizationPolicy:
        return AdsOnlyMonetizationPolicy(
            base_policy_snapshot_id=base_policy_snapshot_id,
            base_policy_snapshot_hash=base_policy_snapshot_hash,
            overlay_authority_ref=overlay_authority_ref,
            allowed_revenue_types=list(PlatformRevenueType),
        )

    def compile_effective_policy(
        self,
        *,
        base_policy_snapshot_id: uuid.UUID,
        base_policy_snapshot_hash: str,
        overlay_authority_ref: str,
    ) -> tuple[AdsOnlyMonetizationPolicy, str]:
        policy = self.compile_effective_overlay(
            base_policy_snapshot_id=base_policy_snapshot_id,
            base_policy_snapshot_hash=base_policy_snapshot_hash,
            overlay_authority_ref=overlay_authority_ref,
        )
        effective_hash = market_policy_hash(
            policy_snapshot_id=base_policy_snapshot_id,
            market_slice={
                "base_policy_snapshot_hash": base_policy_snapshot_hash,
                "ads_only_overlay_hash": policy.content_hash,
            },
        )
        return policy, effective_hash


def acceptance_evidence_from_manifest(
    manifest: GeoDeliveryVerificationManifest,
) -> GeoDeliveryAcceptanceEvidenceSet:
    """Bind every acceptance verdict to the exact manifest and machine runs."""

    runs = {item.run_id: item for item in manifest.verification_runs}
    gate_results = {item.gate: item for item in manifest.gate_results}
    results: list[GeoDeliveryAcceptanceGateResult] = []
    for gate in GEO_DELIVERY_ACCEPTANCE_GATES:
        result = gate_results[gate]
        evidence_refs = [
            GeoDeliveryImmutableEvidenceRef(
                evidence_type="VERIFICATION_MANIFEST",
                ref=f"verification-manifest://{manifest.content_hash}#{gate.value}",
                content_hash=manifest.content_hash,
            )
        ]
        evidence_refs.extend(
            GeoDeliveryImmutableEvidenceRef(
                evidence_type="MACHINE_VERIFICATION_RUN",
                ref=(f"verification-run://{manifest.content_hash}/{run_id}"),
                content_hash=runs[run_id].content_hash,
            )
            for run_id in result.verification_run_ids
        )
        results.append(
            GeoDeliveryAcceptanceGateResult(
                gate=gate,
                verdict=result.verdict,
                checks=dict(result.checks),
                evidence_refs=evidence_refs,
            )
        )
    return GeoDeliveryAcceptanceEvidenceSet(results=results)


class GeoDeliveryCloseoutArtifactService:
    """Persist immutable submitted closeout artifacts without mutating profile v3."""

    EFFECTIVE_POLICY_ARTIFACT_TYPE = "effective_ads_only_monetization_policy"
    CLOSEOUT_ARTIFACT_TYPE = "geo_market_delivery_closeout_evidence"
    SOURCE_APPROVAL_SCOPE = "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
    SOURCE_RECEIPT_ARTIFACT_TYPE = "pkg1_market_revision_human_review_receipt"
    VERIFICATION_RECEIPT_ARTIFACT_TYPE = "geo_delivery_verification_receipt"
    VERIFICATION_RECEIPT_ARTIFACT_STATUSES = frozenset({"in_review"})
    VERIFICATION_RECEIPT_VERSION_STATUSES = frozenset({"submitted"})

    def __init__(self, session: Session):
        self.session = session

    def _resolve_source_approval_authority(
        self,
        *,
        source_package: ArtifactVersion,
        source_project: VideoProject,
    ) -> tuple[ApprovalDecision, ArtifactVersion]:
        approvals = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == source_package.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        approvals = [
            item
            for item in approvals
            if (item.metadata_ or {}).get("approval_scope")
            == self.SOURCE_APPROVAL_SCOPE
        ]
        if len(approvals) != 1:
            raise ValidationFailureError("GEO_CLOSEOUT_EXACT_SOURCE_APPROVAL_REQUIRED")
        approval = approvals[0]
        metadata = approval.metadata_ or {}
        if (
            metadata.get("package_artifact_version_id") != str(source_package.id)
            or metadata.get("package_content_hash") != source_package.content_hash
            or metadata.get("production_package_approved") is not True
            or metadata.get("mr1_execution_authorized") is not False
            or metadata.get("publish_execution_authorized") is not False
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_SOURCE_APPROVAL_LINEAGE_INVALID")

        receipt_artifacts = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_project.id,
                    Artifact.artifact_type == self.SOURCE_RECEIPT_ARTIFACT_TYPE,
                )
            ).all()
        )
        if len(receipt_artifacts) != 1:
            raise ValidationFailureError("GEO_CLOSEOUT_EXACT_HUMAN_RECEIPT_REQUIRED")
        receipt_artifact = receipt_artifacts[0]
        receipt = (
            self.session.get(ArtifactVersion, receipt_artifact.current_version_id)
            if receipt_artifact.current_version_id is not None
            else None
        )
        content = receipt.content if receipt is not None else {}
        reviewed_package = content.get("reviewed_package") or {}
        revision = content.get("revision") or {}
        if (
            receipt is None
            or receipt_artifact.status != "approved"
            or receipt.status != "approved"
            or receipt.artifact_id != receipt_artifact.id
            or receipt.created_by_user_id != approval.decided_by_user_id
            or receipt_artifact.created_by_user_id != approval.decided_by_user_id
            or deterministic_artifact_content_hash(content) != receipt.content_hash
            or content.get("approval_scope") != self.SOURCE_APPROVAL_SCOPE
            or content.get("approval_decision_id") != str(approval.id)
            or content.get("receipt_content_authority")
            != "ARTIFACT_VERSION_CONTENT_HASH"
            or content.get("decision") != "PASS"
            or content.get("decision_source") != "OPERATOR"
            or content.get("review_authority") != "HUMAN"
            or reviewed_package.get("artifact_version_id") != str(source_package.id)
            or reviewed_package.get("artifact_version_ref")
            != f"artifact-version://{source_package.id}"
            or reviewed_package.get("content_hash") != source_package.content_hash
            or revision.get("video_project_id") != str(source_project.id)
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_HUMAN_RECEIPT_LINEAGE_INVALID")
        return approval, receipt

    def _validate_market_alignment_evidence_bindings(
        self,
        *,
        evidence: MarketDeliveryEvidence,
        source_package: ArtifactVersion,
        source_project: VideoProject,
        snapshot: CompiledChannelPolicySnapshot,
        destination_runtime: DestinationRuntimeContract,
        effective_market_policy_hash: str,
    ) -> None:
        package_content = source_package.content or {}
        reused_bindings = package_content.get("reused_artifacts") or {}
        revised_bindings = package_content.get("revised_artifacts") or {}
        artifact_bindings = {
            **reused_bindings,
            **revised_bindings,
        }
        historical_ref = (
            (package_content.get("exact_bindings") or {})
            .get("historical_video_project", {})
            .get("ref")
        )
        historical_project_id: uuid.UUID | None = None
        if isinstance(historical_ref, str) and historical_ref.startswith(
            "video-project://"
        ):
            try:
                historical_project_id = uuid.UUID(
                    historical_ref.removeprefix("video-project://")
                )
            except ValueError:
                historical_project_id = None
        expected_refs = {
            "creative_brief": evidence.creative_brief_ref,
            "research_pack": evidence.research_pack_ref,
            "script": evidence.script_ref,
            "voice_policy": evidence.voice_manifest_ref,
            "visual_plan": evidence.visual_plan_ref,
            "publishing_metadata_package": evidence.metadata_package_ref,
            "thumbnail_brief": evidence.thumbnail_brief_ref,
            "market_alignment_dossier": evidence.market_alignment_dossier_ref,
        }
        resolved: dict[str, ArtifactVersion] = {}
        for artifact_type, expected_ref in expected_refs.items():
            binding = artifact_bindings.get(artifact_type)
            expected_project_id = (
                historical_project_id
                if artifact_type in reused_bindings
                else source_project.id
            )
            raw_id = (
                binding.get("artifact_version_id")
                if isinstance(binding, dict)
                else None
            )
            version = (
                self.session.get(ArtifactVersion, uuid.UUID(str(raw_id)))
                if raw_id
                else None
            )
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact.artifact_type != artifact_type
                or expected_project_id is None
                or artifact.video_project_id != expected_project_id
                or artifact.current_version_id != version.id
                or artifact.status not in {"in_review", "approved"}
                or version.status not in {"submitted", "approved"}
                or binding.get("artifact_version_ref") != expected_ref
                or binding.get("content_hash") != version.content_hash
                or deterministic_artifact_content_hash(version.content or {})
                != version.content_hash
            ):
                raise ValidationFailureError(
                    f"GEO_CLOSEOUT_MARKET_EVIDENCE_BINDING_INVALID:{artifact_type}"
                )
            resolved[artifact_type] = version

        handoff_binding = artifact_bindings.get("publish_handoff_package")
        expected_caption_plan_ref = (
            f"{handoff_binding.get('artifact_version_ref')}#caption-plan"
            if isinstance(handoff_binding, dict)
            else None
        )
        exact_bindings = package_content.get("exact_bindings") or {}
        profile_binding = exact_bindings.get("target_market_profile") or {}
        if (
            evidence.policy_snapshot_id != snapshot.id
            or evidence.market_policy_hash != effective_market_policy_hash
            or evidence.publish_package_ref != f"artifact-version://{source_package.id}"
            or evidence.caption_plan_ref != expected_caption_plan_ref
            or evidence.target_market_profile_ref != profile_binding.get("ref")
            or evidence.target_market_profile_hash
            != profile_binding.get("content_hash")
            or evidence.market_alignment_dossier_hash
            != resolved["market_alignment_dossier"].content_hash
            or evidence.destination_binding_id
            != destination_runtime.destination_binding_id
            or evidence.destination_binding_fingerprint
            != destination_runtime.binding_fingerprint
            or evidence.destination_status != destination_runtime.status
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_MARKET_EVIDENCE_LINEAGE_INVALID")

    def _resolve_verification_receipt(
        self,
        *,
        artifact_version_id: uuid.UUID,
        expected_content_hash: str,
        source_project: VideoProject,
        expected_creator_user_id: uuid.UUID,
    ) -> tuple[ArtifactVersion, GeoDeliveryVerificationReceipt]:
        version = self.session.get(ArtifactVersion, artifact_version_id)
        artifact = (
            self.session.get(Artifact, version.artifact_id)
            if version is not None
            else None
        )
        if (
            version is None
            or artifact is None
            or artifact.artifact_type != self.VERIFICATION_RECEIPT_ARTIFACT_TYPE
            or artifact.video_project_id != source_project.id
            or artifact.current_version_id != version.id
            or artifact.status not in self.VERIFICATION_RECEIPT_ARTIFACT_STATUSES
            or version.status not in self.VERIFICATION_RECEIPT_VERSION_STATUSES
            or version.content_hash != expected_content_hash
            or version.created_by_user_id != expected_creator_user_id
            or artifact.created_by_user_id != expected_creator_user_id
            or deterministic_artifact_content_hash(version.content or {})
            != version.content_hash
            or (version.packaging_metadata or {}).get("producer")
            != "VCOS_MACHINE_VERIFICATION_RUNNER"
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_VERIFICATION_RECEIPT_INVALID")
        try:
            receipt = GeoDeliveryVerificationReceipt.model_validate(version.content)
        except Exception as exc:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_VERIFICATION_RECEIPT_SCHEMA_INVALID"
            ) from exc
        if (
            (version.packaging_metadata or {}).get("manifest_content_hash")
            != receipt.manifest.content_hash
            or (version.packaging_metadata or {}).get("workspace_hash")
            != receipt.manifest.workspace_hash
            or {
                "type": "source_package_manifest",
                "artifact_version_id": str(
                    receipt.manifest.source_package_artifact_version_id
                ),
                "content_hash": receipt.manifest.source_package_content_hash,
            }
            not in (version.evidence_refs or [])
        ):
            raise ValidationFailureError(
                "GEO_CLOSEOUT_VERIFICATION_RECEIPT_PRODUCER_INVALID"
            )
        try:
            validate_geo_delivery_verification_scope(receipt.manifest)
        except ValueError as exc:
            raise ValidationFailureError(
                f"GEO_CLOSEOUT_VERIFICATION_RECEIPT_SCOPE_INVALID:{exc}"
            ) from exc
        return version, receipt

    def _resolve_current_workspace_revalidation_receipt(
        self,
        *,
        source_project: VideoProject,
        source_package: ArtifactVersion,
        expected_channel_workspace_id: uuid.UUID,
        expected_policy_snapshot_id: uuid.UUID,
        expected_policy_snapshot_hash: str,
        expected_creator_user_id: uuid.UUID,
        current_workspace_hash: str,
    ) -> tuple[ArtifactVersion, GeoDeliveryVerificationReceipt]:
        """Resolve one exact machine receipt without changing closeout authority.

        This is intentionally not a "latest receipt" lookup.  A later machine
        verification may establish that the current code is fresh, but it cannot
        replace the immutable receipt embedded in an already-submitted closeout.
        """

        artifacts = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_project.id,
                    Artifact.artifact_type
                    == self.VERIFICATION_RECEIPT_ARTIFACT_TYPE,
                    Artifact.created_by_user_id == expected_creator_user_id,
                )
            ).all()
        )
        current_claims: list[tuple[Artifact, ArtifactVersion]] = []
        for artifact in artifacts:
            version = (
                self.session.get(ArtifactVersion, artifact.current_version_id)
                if artifact.current_version_id is not None
                else None
            )
            if (
                version is None
                or version.created_by_user_id != expected_creator_user_id
            ):
                continue
            raw_content = version.content or {}
            raw_manifest = raw_content.get("manifest")
            manifest_workspace_hash = (
                raw_manifest.get("workspace_hash")
                if isinstance(raw_manifest, dict)
                else None
            )
            metadata_workspace_hash = (version.packaging_metadata or {}).get(
                "workspace_hash"
            )
            if current_workspace_hash in {
                manifest_workspace_hash,
                metadata_workspace_hash,
            }:
                current_claims.append((artifact, version))

        valid: list[tuple[ArtifactVersion, GeoDeliveryVerificationReceipt]] = []
        non_passing_count = 0
        for _artifact, claimed_version in current_claims:
            try:
                version, receipt = self._resolve_verification_receipt(
                    artifact_version_id=claimed_version.id,
                    expected_content_hash=claimed_version.content_hash,
                    source_project=source_project,
                    expected_creator_user_id=expected_creator_user_id,
                )
            except ValidationFailureError as exc:
                raise ValidationFailureError(
                    "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_INVALID"
                ) from exc

            manifest = receipt.manifest
            if (
                manifest.channel_workspace_id != expected_channel_workspace_id
                or manifest.policy_snapshot_id != expected_policy_snapshot_id
                or manifest.policy_snapshot_hash != expected_policy_snapshot_hash
                or manifest.source_package_artifact_version_id != source_package.id
                or manifest.source_package_content_hash
                != source_package.content_hash
                or manifest.workspace_hash != current_workspace_hash
                or manifest.repository_revision
                != f"workspace-sha256:{current_workspace_hash}"
            ):
                raise ValidationFailureError(
                    "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_SCOPE_INVALID"
                )
            try:
                validate_geo_delivery_verification_scope(manifest)
            except ValueError as exc:
                raise ValidationFailureError(
                    "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_SCOPE_INVALID"
                ) from exc
            if any(
                run.verdict != DeliveryVerdict.PASS
                or run.exit_code != 0
                or run.failed != 0
                for run in manifest.verification_runs
            ) or any(
                gate.verdict != DeliveryVerdict.PASS
                or not all(gate.checks.values())
                for gate in manifest.gate_results
            ):
                non_passing_count += 1
                continue
            valid.append((version, receipt))

        if not valid:
            if non_passing_count:
                raise ValidationFailureError(
                    "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_NOT_PASSING"
                )
            raise ValidationFailureError(
                "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_REQUIRED"
            )
        unique_passing: dict[
            tuple[str, str],
            tuple[ArtifactVersion, GeoDeliveryVerificationReceipt],
        ] = {}
        for version, receipt in valid:
            evidence_key = (version.content_hash, receipt.manifest.content_hash)
            existing = unique_passing.get(evidence_key)
            if existing is None or str(version.id) < str(existing[0].id):
                unique_passing[evidence_key] = (version, receipt)
        if len(unique_passing) != 1:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_CURRENT_WORKSPACE_REVALIDATION_AMBIGUOUS"
            )
        return next(iter(unique_passing.values()))

    def ensure_closeout_artifacts(
        self,
        *,
        video_project_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        base_policy_snapshot_id: uuid.UUID,
        base_policy_snapshot_hash: str,
        source_package_artifact_version_id: uuid.UUID,
        source_package_content_hash: str,
        overlay_authority_ref: str,
        destination_runtime: DestinationRuntimeContract,
        market_alignment_evidence: MarketDeliveryEvidence,
        market_alignment_result: MarketDeliveryAlignmentResult,
        verification_receipt_artifact_version_id: uuid.UUID,
        verification_receipt_content_hash: str,
    ) -> dict[str, Any]:
        from app.services.pkg1 import PKG1PackageService

        no_execution_before = PKG1PackageService(self.session).no_execution_counts()
        project = self.session.get(VideoProject, video_project_id)
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, base_policy_snapshot_id
        )
        if project is None:
            raise ValidationFailureError("GEO_CLOSEOUT_VIDEO_PROJECT_MISSING")
        if snapshot is None:
            raise ValidationFailureError("GEO_CLOSEOUT_POLICY_SNAPSHOT_MISSING")
        channel = self.session.get(ChannelWorkspace, project.channel_workspace_id)
        if channel is None:
            raise ValidationFailureError("GEO_CLOSEOUT_CHANNEL_WORKSPACE_MISSING")
        if project.policy_snapshot_id != snapshot.id:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_PROJECT_POLICY_SNAPSHOT_MISMATCH"
            )
        if (
            snapshot.content_hash != base_policy_snapshot_hash
            or deterministic_artifact_content_hash(snapshot.compiled_payload or {})
            != snapshot.content_hash
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_POLICY_SNAPSHOT_HASH_MISMATCH")
        if (
            snapshot.status != "active"
            or snapshot.channel_workspace_id != channel.id
            or snapshot.channel_profile_version_id != project.channel_profile_version_id
            or channel.active_policy_snapshot_id != snapshot.id
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_ACTIVE_SNAPSHOT_MISMATCH")
        if destination_runtime.channel_workspace_id != channel.id:
            raise ValidationFailureError("GEO_CLOSEOUT_DESTINATION_CHANNEL_MISMATCH")
        source_package = self.session.get(
            ArtifactVersion, source_package_artifact_version_id
        )
        source_artifact = (
            self.session.get(Artifact, source_package.artifact_id)
            if source_package is not None
            else None
        )
        source_project = (
            self.session.get(VideoProject, source_artifact.video_project_id)
            if source_artifact is not None
            else None
        )
        if (
            source_package is None
            or source_artifact is None
            or source_project is None
            or source_artifact.artifact_type != "package_manifest"
            or source_artifact.current_version_id != source_package.id
            or source_artifact.status != "approved"
            or source_package.status not in {"submitted", "approved"}
            or source_project.status != "approved"
            or source_project.channel_workspace_id != channel.id
            or source_project.policy_snapshot_id != snapshot.id
            or source_package.content_hash != source_package_content_hash
            or deterministic_artifact_content_hash(source_package.content or {})
            != source_package.content_hash
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_SOURCE_PACKAGE_INVALID")
        source_approval, human_review_receipt = self._resolve_source_approval_authority(
            source_package=source_package,
            source_project=source_project,
        )
        verification_receipt_version, verification_receipt = (
            self._resolve_verification_receipt(
                artifact_version_id=(verification_receipt_artifact_version_id),
                expected_content_hash=verification_receipt_content_hash,
                source_project=source_project,
                expected_creator_user_id=source_approval.decided_by_user_id,
            )
        )
        verification_manifest = verification_receipt.manifest
        if (
            verification_manifest.channel_workspace_id != channel.id
            or verification_manifest.policy_snapshot_id != snapshot.id
            or verification_manifest.policy_snapshot_hash != snapshot.content_hash
            or verification_manifest.source_package_artifact_version_id
            != source_package.id
            or verification_manifest.source_package_content_hash
            != source_package.content_hash
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_VERIFICATION_MANIFEST_STALE")
        try:
            current_workspace_hash = geo_delivery_workspace_hash(
                GEO_DELIVERY_REPOSITORY_ROOT
            )
        except OSError as exc:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_WORKSPACE_HASH_UNAVAILABLE"
            ) from exc
        if (
            verification_manifest.workspace_hash != current_workspace_hash
            or verification_manifest.repository_revision
            != f"workspace-sha256:{current_workspace_hash}"
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_VERIFICATION_WORKSPACE_STALE")
        try:
            validate_geo_delivery_verification_scope(verification_manifest)
        except ValueError as exc:
            raise ValidationFailureError(
                f"GEO_CLOSEOUT_VERIFICATION_SCOPE_INVALID:{exc}"
            ) from exc
        non_passing_runs = sorted(
            item.run_id
            for item in verification_manifest.verification_runs
            if item.verdict != DeliveryVerdict.PASS
            or item.exit_code != 0
            or item.failed != 0
        )
        non_passing_manifest_gates = sorted(
            item.gate.value
            for item in verification_manifest.gate_results
            if item.verdict != DeliveryVerdict.PASS or not all(item.checks.values())
        )
        if non_passing_runs or non_passing_manifest_gates:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_VERIFICATION_NOT_PASSING:"
                + ",".join([*non_passing_runs, *non_passing_manifest_gates])
            )

        expected_acceptance = acceptance_evidence_from_manifest(verification_manifest)
        acceptance_evidence = expected_acceptance
        non_passing = sorted(
            item.gate.value
            for item in acceptance_evidence.results
            if item.verdict != DeliveryVerdict.PASS
        )
        if non_passing:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_ACCEPTANCE_NOT_PASSING:" + ",".join(non_passing)
            )

        policy, effective_hash = (
            AdsOnlyMonetizationPolicyService().compile_effective_policy(
                base_policy_snapshot_id=snapshot.id,
                base_policy_snapshot_hash=snapshot.content_hash,
                overlay_authority_ref=overlay_authority_ref,
            )
        )
        self._validate_market_alignment_evidence_bindings(
            evidence=market_alignment_evidence,
            source_package=source_package,
            source_project=source_project,
            snapshot=snapshot,
            destination_runtime=destination_runtime,
            effective_market_policy_hash=effective_hash,
        )
        recomputed_alignment = MarketDeliveryAlignmentGate().evaluate(
            market_alignment_evidence
        )
        if (
            market_alignment_result.policy_snapshot_id != snapshot.id
            or market_alignment_result.market_policy_hash != effective_hash
            or market_alignment_result.destination_binding_id
            != destination_runtime.destination_binding_id
            or market_alignment_result.destination_binding_fingerprint
            != destination_runtime.binding_fingerprint
            or market_alignment_result.verdict != DeliveryVerdict.PASS
            or market_alignment_result.model_dump(mode="json")
            != recomputed_alignment.model_dump(mode="json")
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_ALIGNMENT_LINEAGE_MISMATCH")

        explicit_verdicts = GeoDeliveryAcceptanceVerdicts.model_validate(
            {
                item.gate.value: item.verdict.value
                for item in acceptance_evidence.results
            }
        )
        verification_refs = sorted(
            {
                f"{ref.ref}|sha256:{ref.content_hash}"
                for item in acceptance_evidence.results
                for ref in item.evidence_refs
            }
        )

        policy_artifact_content = EffectiveAdsOnlyPolicyArtifact(
            base_policy_snapshot_id=snapshot.id,
            base_policy_snapshot_hash=snapshot.content_hash,
            effective_market_policy_hash=effective_hash,
            policy=policy,
        ).model_dump(mode="json")
        policy_ref = self._ensure_submitted_artifact(
            project_id=project.id,
            created_by_user_id=created_by_user_id,
            artifact_type=self.EFFECTIVE_POLICY_ARTIFACT_TYPE,
            content=policy_artifact_content,
            evidence_refs=[
                {
                    "type": "compiled_channel_policy_snapshot",
                    "id": str(snapshot.id),
                    "content_hash": snapshot.content_hash,
                }
            ],
            context_refs=[
                {
                    "type": "overlay_authority",
                    "ref": overlay_authority_ref,
                }
            ],
        )
        no_execution_after = PKG1PackageService(self.session).no_execution_counts()
        no_execution_deltas = {
            key: no_execution_after[key] - value
            for key, value in no_execution_before.items()
        }
        if any(no_execution_deltas.values()):
            raise ValidationFailureError(
                "GEO_CLOSEOUT_PROVIDER_RENDER_PUBLISH_ACTIVITY_DETECTED"
            )
        no_execution_proof = GeoDeliveryNoExecutionProof(
            before_counts=no_execution_before,
            after_counts=no_execution_after,
            deltas=no_execution_deltas,
            all_deltas_zero=all(value == 0 for value in no_execution_deltas.values()),
        )

        closeout_content = GeoMarketDeliveryCloseoutEvidence(
            base_policy_snapshot_id=snapshot.id,
            base_policy_snapshot_hash=snapshot.content_hash,
            effective_market_policy_hash=effective_hash,
            source_approval_decision_id=source_approval.id,
            source_approval_scope=self.SOURCE_APPROVAL_SCOPE,
            human_review_receipt_artifact_version_id=(human_review_receipt.id),
            human_review_receipt_content_hash=(human_review_receipt.content_hash),
            verification_receipt_artifact_version_id=(verification_receipt_version.id),
            verification_receipt_content_hash=(
                verification_receipt_version.content_hash
            ),
            no_execution_proof=no_execution_proof,
            effective_ads_only_policy_ref=policy_ref,
            destination_runtime=destination_runtime,
            market_alignment_result=market_alignment_result,
            acceptance_verdicts=explicit_verdicts,
            acceptance_evidence=acceptance_evidence,
            verification_manifest=verification_manifest,
            implementation_versions={
                "market_lineage": "geo-delivery.strict-market-lineage.v1",
                "destination_enforcement": "geo-delivery.destination-runtime-contract.v1",
                "market_alignment": "geo-delivery.market-alignment-result.v1",
                "geo_distribution_tracker": "geo-delivery.geo-distribution-tracker.v1",
                "geo_maturity": "geo-delivery.geo-diagnostic-result.v1",
                "geo_diagnostics": "geo-delivery.geo-diagnostic-result.v1",
                "ads_only_policy": "geo-delivery.ads-only-policy.v1",
            },
            verification_refs=verification_refs,
            destination_status=destination_runtime.status,
            upload_ready=False,
            publish_execution_ready=False,
        ).model_dump(mode="json")
        closeout_ref = self._ensure_submitted_artifact(
            project_id=project.id,
            created_by_user_id=created_by_user_id,
            artifact_type=self.CLOSEOUT_ARTIFACT_TYPE,
            content=closeout_content,
            evidence_refs=[
                {
                    "type": "effective_ads_only_monetization_policy",
                    "artifact_version_id": str(policy_ref.artifact_version_id),
                    "content_hash": policy_ref.content_hash,
                },
                {
                    "type": "source_package_manifest",
                    "artifact_version_id": str(source_package.id),
                    "content_hash": source_package.content_hash,
                },
                {
                    "type": "source_approval_decision",
                    "id": str(source_approval.id),
                    "approval_scope": self.SOURCE_APPROVAL_SCOPE,
                },
                {
                    "type": "human_review_receipt",
                    "artifact_version_id": str(human_review_receipt.id),
                    "content_hash": human_review_receipt.content_hash,
                },
                {
                    "type": "verification_manifest",
                    "content_hash": verification_manifest.content_hash,
                    "workspace_hash": verification_manifest.workspace_hash,
                },
                {
                    "type": "geo_delivery_verification_receipt",
                    "artifact_version_id": str(verification_receipt_version.id),
                    "content_hash": verification_receipt_version.content_hash,
                },
                *[
                    {
                        "type": "acceptance_gate",
                        "gate": item.gate.value,
                        "verdict": item.verdict.value,
                        "content_hash": item.content_hash,
                        "evidence_refs": [
                            ref.model_dump(mode="json") for ref in item.evidence_refs
                        ],
                    }
                    for item in acceptance_evidence.results
                ],
            ],
            context_refs=[
                {
                    "type": "compiled_channel_policy_snapshot",
                    "id": str(snapshot.id),
                    "content_hash": snapshot.content_hash,
                },
                {
                    "type": "destination_binding",
                    "id": str(destination_runtime.destination_binding_id),
                    "content_hash": destination_runtime.binding_fingerprint,
                },
            ],
        )
        if (
            PKG1PackageService(self.session).no_execution_counts()
            != no_execution_before
        ):
            raise ValidationFailureError(
                "GEO_CLOSEOUT_PROVIDER_RENDER_PUBLISH_ACTIVITY_DETECTED"
            )
        return {
            "effective_ads_only_policy": policy_ref,
            "geo_closeout_evidence": closeout_ref,
            "effective_market_policy_hash": effective_hash,
            "base_policy_snapshot_id": snapshot.id,
            "base_policy_snapshot_hash": snapshot.content_hash,
            "destination_status": destination_runtime.status,
            "upload_ready": False,
            "publish_execution_ready": False,
        }

    def _ensure_submitted_artifact(
        self,
        *,
        project_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        artifact_type: str,
        content: dict[str, Any],
        evidence_refs: list[dict[str, Any]],
        context_refs: list[dict[str, Any]],
    ) -> GeoDeliveryArtifactRef:
        artifacts = list(
            self.session.scalars(
                select(Artifact)
                .where(
                    Artifact.video_project_id == project_id,
                    Artifact.artifact_type == artifact_type,
                )
                .order_by(Artifact.created_at.asc(), Artifact.id.asc())
            ).all()
        )
        if len(artifacts) > 1:
            raise ValidationFailureError(
                f"GEO_CLOSEOUT_DUPLICATE_ARTIFACT:{artifact_type}"
            )
        artifact_service = ArtifactService(self.session)
        artifact = (
            artifacts[0]
            if artifacts
            else artifact_service.create_artifact(
                data=ArtifactCreate(
                    video_project_id=project_id,
                    artifact_type=artifact_type,
                    status="in_review",
                    created_by_user_id=created_by_user_id,
                ),
                correlation_id="geo-delivery-closeout-artifact-create",
            )
        )
        expected_hash = deterministic_artifact_content_hash(content)
        current = (
            self.session.get(ArtifactVersion, artifact.current_version_id)
            if artifact.current_version_id
            else None
        )
        if current is not None and (
            current.artifact_id != artifact.id
            or artifact.status != "in_review"
            or artifact.created_by_user_id != created_by_user_id
            or current.status != "submitted"
            or current.created_by_user_id != created_by_user_id
            or deterministic_artifact_content_hash(current.content or {})
            != current.content_hash
            or not current.evidence_refs
            or not current.context_refs
        ):
            raise ValidationFailureError(
                f"GEO_CLOSEOUT_CURRENT_ARTIFACT_INVALID:{artifact_type}"
            )
        if current is not None and current.content_hash == expected_hash:
            if (
                current.evidence_refs != evidence_refs
                or current.context_refs != context_refs
            ):
                raise ValidationFailureError(
                    f"GEO_CLOSEOUT_CURRENT_ARTIFACT_PROVENANCE_INVALID:{artifact_type}"
                )
            return self._artifact_ref(artifact, current)
        version = artifact_service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                parent_version_id=current.id if current else None,
                content=content,
                status="submitted",
                created_by_user_id=created_by_user_id,
                evidence_refs=evidence_refs,
                context_refs=context_refs,
            ),
            correlation_id="geo-delivery-closeout-version-create",
        )
        artifact.status = "in_review"
        self.session.flush()
        return self._artifact_ref(artifact, version)

    @staticmethod
    def _artifact_ref(
        artifact: Artifact, version: ArtifactVersion
    ) -> GeoDeliveryArtifactRef:
        return GeoDeliveryArtifactRef(
            artifact_type=artifact.artifact_type,
            artifact_id=artifact.id,
            artifact_version_id=version.id,
            version_number=version.version_number,
            ref=f"artifact-version://{version.id}",
            content_hash=version.content_hash,
        )


class SelfFundingGate:
    EXCLUDED_FIELDS = [
        "raw_views",
        "cpm_times_views",
        "estimated_revenue",
        "projected_revenue",
        "affiliate_revenue",
        "sponsor_revenue",
        "product_revenue",
        "service_revenue",
    ]

    def evaluate(
        self,
        *,
        policy: AdsOnlyMonetizationPolicy,
        windows: list[SelfFundingWindow],
        minimum_consecutive_windows: int = 2,
    ) -> SelfFundingResult:
        if minimum_consecutive_windows < 2:
            raise ValueError("SELF_FUNDING_MINIMUM_WINDOWS_CANNOT_BE_WEAKENED")
        if set(policy.allowed_revenue_types) != set(PlatformRevenueType):
            raise ValueError("ADS_ONLY_ALLOWED_REVENUE_SET_INVALID")
        if len({window.window_key for window in windows}) != len(windows):
            raise ValueError("SELF_FUNDING_WINDOW_KEY_DUPLICATED")

        best: list[str] = []
        current: list[str] = []
        below_cost = False
        for window in windows:
            qualifies = bool(
                window.revenue_state in {"FINALIZED", "LOCKED", "PAID"}
                and window.revenue_amount > 0
                and window.revenue_amount >= window.allocated_cost
            )
            if qualifies:
                current.append(window.window_key)
                if len(current) > len(best):
                    best = list(current)
            else:
                if window.revenue_amount < window.allocated_cost:
                    below_cost = True
                current = []

        passed = len(best) >= minimum_consecutive_windows
        reasons: list[str] = []
        if below_cost:
            reasons.append("FINALIZED_PLATFORM_REVENUE_BELOW_ALLOCATED_COST")
        if not passed:
            reasons.append("INSUFFICIENT_CONSECUTIVE_QUALIFYING_WINDOWS")
        return SelfFundingResult(
            verdict=DeliveryVerdict.PASS if passed else DeliveryVerdict.BLOCK,
            consecutive_qualifying_windows=len(best),
            qualifying_window_keys=best,
            excluded_estimate_fields=self.EXCLUDED_FIELDS,
            reason_codes=reasons,
            exact_next_action=(
                "Self-funding evidence is mature; retain the ads-only accounting boundary."
                if passed
                else "Wait for two consecutive finalized platform-revenue windows covering allocated costs."
            ),
        )
