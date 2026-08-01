from __future__ import annotations

# Compatibility note: semantic facade `packaging_handoff` re-exports this implementation; phase-coded import kept for reports/tests/backward compatibility.
import os
import re
import uuid
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.contracts.m1 import (
    HookSpecRead,
    PackagingGateResultRead,
    PackagingGateSummaryRead,
    PackagingHandoffSnapshotRead,
    PublishTimingRecommendationRead,
    ThumbnailHandoffRead,
    UploadHandoffCopyRead,
)
from app.core.errors import NotFoundError
from app.db.models import (
    EffectiveChannelRuntimeContextSnapshot,
    FirstScriptedVideoPackage,
    HumanUploadTask,
    R3D4GateBatchRun,
    UploadedVideo,
    VideoProject,
    ChannelWorkspace,
    VideoGenerationBoundary,
)
from app.services.r3d3 import stable_hash


PACKAGING_GATE_ORDER = [
    "HookTruthfulnessGate",
    "HookPayoffGate",
    "VisualHookRelevanceGate",
    "TitlePromiseGate",
    "MetadataTruthfulnessGate",
    "CaptionCoverageGate",
    "DescriptionCompletenessGate",
    "ThumbnailTruthfulnessGate",
    "MobileThumbnailLegibilityGate",
    "CharacterThumbnailConsistencyGate",
    "PublishTimingComplianceGate",
    "ManualPublishOnlyGate",
]

_STOPWORDS = {
    "about",
    "after",
    "before",
    "cach",
    "cho",
    "from",
    "have",
    "into",
    "only",
    "that",
    "this",
    "with",
    "your",
    "the",
    "and",
    "for",
    "you",
    "will",
    "can",
    "how",
    "why",
    "what",
    "video",
    "vcos",
}


class PackagingHandoffReadService:
    def __init__(self, session: Session):
        self.session = session

    def build(self, package_id: uuid.UUID) -> PackagingHandoffSnapshotRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        effective_context = (
            self.session.get(
                EffectiveChannelRuntimeContextSnapshot,
                package.effective_context_snapshot_id,
            )
            if package.effective_context_snapshot_id
            else None
        )
        artifacts = _dict(package.artifacts)
        hook = _extract_hook_spec(package, artifacts, effective_context)
        thumbnail = _extract_thumbnail_handoff(artifacts, effective_context)
        timing = _publish_timing_recommendation(package, effective_context, artifacts)
        gate_results = PackagingGateRunner().run(
            package=package,
            artifacts=artifacts,
            effective_context=effective_context,
            hook=hook,
            thumbnail=thumbnail,
            timing=timing,
        )
        gate_summary = _gate_summary(
            gate_results, self._r3d4_gate_batch_refs(package.id)
        )
        upload_copy = _extract_upload_copy(
            package=package,
            artifacts=artifacts,
            effective_context=effective_context,
            packaging_gate_status=gate_summary.overall_status,
        )
        market_alignment, market_package = self._market_handoff_state(
            package, artifacts
        )
        return PackagingHandoffSnapshotRead(
            package_id=package.id,
            package_status=package.package_status,
            channel_id=package.channel_id,
            video_project_id=package.video_project_id,
            effective_context_snapshot_id=package.effective_context_snapshot_id,
            effective_context_hash=package.effective_context_hash,
            hook_spec=hook,
            upload_handoff_copy=upload_copy,
            thumbnail_handoff=thumbnail,
            publish_timing_recommendation=timing,
            packaging_gate_summary=gate_summary,
            manual_upload=self._manual_upload_state(package),
            provider_readiness_summary=self._provider_boundary_state(package),
            market_alignment=market_alignment,
            market_package=market_package,
            manual_publish_only=True,
            no_upload_or_publish_calls_made=True,
            created_at=package.created_at,
        )

    def _market_handoff_state(
        self,
        package: FirstScriptedVideoPackage,
        artifacts: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        project = (
            self.session.get(VideoProject, package.video_project_id)
            if package.video_project_id
            else None
        )
        channel = self.session.get(ChannelWorkspace, package.channel_id)
        audience = (
            _dict(project.audience_delivery_summary) if project is not None else {}
        )
        frozen = _dict(audience.get("target_market_freeze"))
        alignment = _dict(
            audience.get("market_alignment")
            or artifacts.get("market_alignment_dossier")
        )
        component_states = _dict(alignment.get("component_gate_states"))
        if not component_states:
            for result in _list(alignment.get("component_results")):
                row = _dict(result)
                key = str(row.get("gate_key") or "")
                if key:
                    component_states[key] = row.get("verdict") or row.get("status")
        destination_root = _dict(
            _dict(channel.metadata_ if channel is not None else {}).get(
                "destination_governance"
            )
        )
        destination_rows = _list(destination_root.get("bindings"))
        destination = _dict(destination_rows[-1]) if destination_rows else {}
        market_package = _dict(artifacts.get("market_bound_publish_package"))
        if not market_package:
            market_package = {
                "package_state": "NOT_FROZEN",
                "approved_package_hash": None,
                "destination_binding_hash": destination.get("content_hash"),
                "target_market_profile_hash": frozen.get("target_market_profile_hash"),
                "media_file_ref": _dict(artifacts.get("render_package_snapshot")).get(
                    "final_video_ref"
                ),
                "media_file_hash": _dict(artifacts.get("render_package_snapshot")).get(
                    "final_video_hash"
                ),
            }
        return (
            {
                "target_market_profile_version": frozen.get(
                    "target_market_profile_version"
                ),
                "primary_market": frozen.get("primary_market"),
                "primary_locale": frozen.get("primary_locale"),
                "narration_locale": frozen.get("narration_locale"),
                "publish_timezone": frozen.get("primary_timezone")
                or timing_timezone(artifacts),
                "destination_binding": destination or None,
                "topic_market_fit": component_states.get(
                    "topic_market_alignment_gate", "NOT_EVALUATED"
                ),
                "research_jurisdiction": component_states.get(
                    "research_jurisdiction_gate", "NOT_EVALUATED"
                ),
                "script_context": component_states.get(
                    "script_market_alignment_gate", "NOT_EVALUATED"
                ),
                "voice_locale": component_states.get(
                    "voice_locale_alignment_gate", "NOT_EVALUATED"
                ),
                "visual_context": component_states.get(
                    "visual_market_alignment_gate", "NOT_EVALUATED"
                ),
                "thumbnail_locale": component_states.get(
                    "thumbnail_market_alignment_gate", "NOT_EVALUATED"
                ),
                "metadata_locale": component_states.get(
                    "metadata_market_alignment_gate", "NOT_EVALUATED"
                ),
                "currency_units": alignment.get("currency_units") or "NOT_EVALUATED",
                "overall_verdict": alignment.get("overall_verdict") or "NOT_EVALUATED",
                "reason_codes": _strings(alignment.get("reason_codes")),
                "review_required_items": _strings(
                    alignment.get("human_review_requirements")
                ),
            },
            market_package,
        )

    def _r3d4_gate_batch_refs(self, package_id: uuid.UUID) -> list[str]:
        rows = self.session.scalars(
            select(R3D4GateBatchRun)
            .where(R3D4GateBatchRun.package_id == package_id)
            .order_by(desc(R3D4GateBatchRun.created_at), desc(R3D4GateBatchRun.id))
        ).all()
        return [str(row.id) for row in rows]

    def _manual_upload_state(
        self, package: FirstScriptedVideoPackage
    ) -> dict[str, Any]:
        task = self.session.scalars(
            select(HumanUploadTask)
            .where(HumanUploadTask.first_scripted_video_package_id == package.id)
            .order_by(desc(HumanUploadTask.created_at), desc(HumanUploadTask.id))
            .limit(1)
        ).one_or_none()
        uploaded = None
        if task is not None and task.actual_uploaded_video_id is not None:
            uploaded = self.session.get(UploadedVideo, task.actual_uploaded_video_id)
        return {
            "human_upload_task_id": str(task.id) if task else None,
            "task_status": task.task_state if task else None,
            "actual_uploaded_video_id": str(task.actual_uploaded_video_id)
            if task and task.actual_uploaded_video_id
            else None,
            "youtube_video_id": uploaded.platform_video_id if uploaded else None,
            "youtube_url": uploaded.video_url if uploaded else None,
            "backfill_supported": True,
            "manual_upload_only": True,
            "no_upload_api_by_policy": True,
            "next_action_vi": (
                "Upload thủ công trên YouTube rồi nhập URL/video_id vào VCOS."
                if task is None or task.actual_uploaded_video_id is None
                else "Video đã được paste-back; tiếp tục xác minh YouTube read-only khi có cấu hình."
            ),
        }

    def _provider_boundary_state(
        self, package: FirstScriptedVideoPackage
    ) -> dict[str, Any]:
        boundary = self.session.scalars(
            select(VideoGenerationBoundary)
            .where(VideoGenerationBoundary.package_id == package.id)
            .order_by(
                desc(VideoGenerationBoundary.created_at),
                desc(VideoGenerationBoundary.id),
            )
            .limit(1)
        ).one_or_none()
        if boundary is None:
            return {
                "boundary_status": None,
                "no_provider_calls_confirmed": True,
                "operator_summary_vi": "M1 chỉ hiển thị package handoff; không gọi provider media.",
            }
        return {
            "video_generation_boundary_id": str(boundary.id),
            "boundary_status": boundary.boundary_status,
            "blocked_reasons": boundary.blocked_reasons,
            "provider_readiness": boundary.provider_readiness,
            "no_provider_calls_confirmed": boundary.no_provider_calls_confirmed,
            "operator_summary_vi": boundary.operator_summary_vi,
            "next_action": boundary.next_action,
        }


class PackagingGateRunner:
    def run(
        self,
        *,
        package: FirstScriptedVideoPackage,
        artifacts: dict[str, Any],
        effective_context: EffectiveChannelRuntimeContextSnapshot | None,
        hook: HookSpecRead,
        thumbnail: ThumbnailHandoffRead,
        timing: PublishTimingRecommendationRead,
    ) -> list[PackagingGateResultRead]:
        gate_inputs = {
            "package": package,
            "artifacts": artifacts,
            "effective_context": effective_context,
            "hook": hook,
            "thumbnail": thumbnail,
            "timing": timing,
        }
        methods = {
            "HookTruthfulnessGate": self._hook_truthfulness,
            "HookPayoffGate": self._hook_payoff,
            "VisualHookRelevanceGate": self._visual_hook_relevance,
            "TitlePromiseGate": self._title_promise,
            "MetadataTruthfulnessGate": self._metadata_truthfulness,
            "CaptionCoverageGate": self._caption_coverage,
            "DescriptionCompletenessGate": self._description_completeness,
            "ThumbnailTruthfulnessGate": self._thumbnail_truthfulness,
            "MobileThumbnailLegibilityGate": self._mobile_thumbnail_legibility,
            "CharacterThumbnailConsistencyGate": self._character_thumbnail_consistency,
            "PublishTimingComplianceGate": self._publish_timing_compliance,
            "ManualPublishOnlyGate": self._manual_publish_only,
        }
        return [methods[key](**gate_inputs) for key in PACKAGING_GATE_ORDER]

    def _hook_truthfulness(self, **kwargs: Any) -> PackagingGateResultRead:
        hook: HookSpecRead = kwargs["hook"]
        artifacts = kwargs["artifacts"]
        script_text = _text_blob(
            _dict(artifacts.get("narration_script")),
            _dict(artifacts.get("script_outline")),
        )
        if not hook.promise_made:
            return _gate(
                "HookTruthfulnessGate",
                "REVIEW_REQUIRED",
                ["HOOK_PROMISE_MISSING"],
                ["hook_spec", "narration_script"],
                ["script_contract"],
                "Thiếu promise của hook; cần người review.",
            )
        if not _claim_supported(hook.promise_made, script_text):
            return _gate(
                "HookTruthfulnessGate",
                "BLOCK",
                ["HOOK_PROMISE_UNSUPPORTED_BY_SCRIPT"],
                ["hook_spec", "narration_script"],
                ["script_contract"],
                "Hook đang hứa điều script không chứng minh.",
            )
        if hook.clickbait_risk == "HIGH":
            return _gate(
                "HookTruthfulnessGate",
                "REVIEW_REQUIRED",
                ["HOOK_CLICKBAIT_RISK_HIGH"],
                ["hook_spec"],
                ["safety_forbidden_claims_context"],
                "Hook có rủi ro clickbait cao; cần rewrite hoặc human review.",
            )
        return _gate(
            "HookTruthfulnessGate",
            "PASS",
            [],
            ["hook_spec", "narration_script"],
            ["script_contract"],
            "Hook khớp nội dung script.",
        )

    def _hook_payoff(self, **kwargs: Any) -> PackagingGateResultRead:
        hook: HookSpecRead = kwargs["hook"]
        artifacts = kwargs["artifacts"]
        if not hook.promise_made:
            return _gate(
                "HookPayoffGate",
                "REVIEW_REQUIRED",
                ["HOOK_PROMISE_MISSING"],
                ["hook_spec"],
                ["script_contract"],
                "Chưa đủ dữ liệu để kiểm tra payoff hook.",
            )
        if not hook.payoff_location:
            return _gate(
                "HookPayoffGate",
                "BLOCK",
                ["HOOK_PAYOFF_LOCATION_MISSING"],
                ["hook_spec", "narration_script"],
                ["script_contract"],
                "Hook promise phải có payoff_location trong script.",
            )
        if not _payoff_location_exists(
            hook.payoff_location, _dict(artifacts.get("narration_script"))
        ):
            return _gate(
                "HookPayoffGate",
                "BLOCK",
                ["HOOK_PAYOFF_LOCATION_NOT_FOUND"],
                ["hook_spec", "narration_script"],
                ["script_contract"],
                "payoff_location không trỏ tới sentence/section có thật.",
            )
        return _gate(
            "HookPayoffGate",
            "PASS",
            [],
            ["hook_spec", "narration_script"],
            ["script_contract"],
            "Hook có payoff trong script.",
        )

    def _visual_hook_relevance(self, **kwargs: Any) -> PackagingGateResultRead:
        hook: HookSpecRead = kwargs["hook"]
        artifacts = kwargs["artifacts"]
        if not hook.first_3_seconds_visual:
            return _gate(
                "VisualHookRelevanceGate",
                "REVIEW_REQUIRED",
                ["HOOK_VISUAL_MISSING"],
                ["hook_spec", "visual_plan"],
                ["visual_style_context"],
                "Thiếu mô tả visual 3 giây đầu.",
            )
        relevant_text = _text_blob(
            _dict(artifacts.get("visual_plan")),
            _dict(artifacts.get("narration_script")),
        )
        if not _claim_supported(
            hook.first_3_seconds_visual, relevant_text, minimum_ratio=0.25
        ):
            return _gate(
                "VisualHookRelevanceGate",
                "BLOCK",
                ["HOOK_VISUAL_NOT_RELEVANT_TO_CONTENT"],
                ["hook_spec", "visual_plan"],
                ["visual_style_context"],
                "Visual hook có vẻ không liên quan nội dung.",
            )
        return _gate(
            "VisualHookRelevanceGate",
            "PASS",
            [],
            ["hook_spec", "visual_plan"],
            ["visual_style_context"],
            "Visual hook liên quan tới nội dung.",
        )

    def _title_promise(self, **kwargs: Any) -> PackagingGateResultRead:
        artifacts = kwargs["artifacts"]
        title = _title(artifacts)
        corpus = _text_blob(
            _dict(artifacts.get("narration_script")),
            _dict(artifacts.get("research_notes")),
        )
        if not title:
            return _gate(
                "TitlePromiseGate",
                "REVIEW_REQUIRED",
                ["TITLE_MISSING"],
                ["metadata_package"],
                ["metadata_seo_policy_context"],
                "Thiếu title để upload.",
            )
        risky = _unsupported_offer_terms(title)
        if risky and not _has_real_offer_manifest(artifacts):
            return _gate(
                "TitlePromiseGate",
                "BLOCK",
                ["TITLE_OVER_PROMISE_UNSUPPORTED_OFFER"],
                ["metadata_package"],
                ["metadata_seo_policy_context", "monetization_cta_context"],
                "Title hứa asset/demo/freebie chưa có manifest.",
            )
        if _contains_any(
            title, ["guaranteed", "10x", "100%", "secret result"]
        ) and not _claim_supported(title, corpus):
            return _gate(
                "TitlePromiseGate",
                "BLOCK",
                ["TITLE_OVER_PROMISE_UNSUPPORTED_CLAIM"],
                ["metadata_package", "narration_script"],
                ["safety_forbidden_claims_context"],
                "Title over-promise so với script/evidence.",
            )
        return _gate(
            "TitlePromiseGate",
            "PASS",
            [],
            ["metadata_package"],
            ["metadata_seo_policy_context"],
            "Title không over-promise.",
        )

    def _metadata_truthfulness(self, **kwargs: Any) -> PackagingGateResultRead:
        artifacts = kwargs["artifacts"]
        text = _text_blob(_dict(artifacts.get("metadata_package")))
        risky = _unsupported_offer_terms(text)
        if risky and not _has_real_offer_manifest(artifacts):
            return _gate(
                "MetadataTruthfulnessGate",
                "BLOCK",
                ["METADATA_UNSUPPORTED_ASSET_OR_DEMO_CLAIM"],
                ["metadata_package"],
                ["monetization_cta_context"],
                "Metadata claim demo/freebie/checklist không có artifact thật.",
            )
        return _gate(
            "MetadataTruthfulnessGate",
            "PASS",
            [],
            ["metadata_package"],
            ["metadata_seo_policy_context"],
            "Metadata không claim asset không tồn tại.",
        )

    def _caption_coverage(self, **kwargs: Any) -> PackagingGateResultRead:
        refs = _subtitle_refs(kwargs["artifacts"])
        if not refs:
            return _gate(
                "CaptionCoverageGate",
                "REVIEW_REQUIRED",
                ["SUBTITLE_REFS_MISSING"],
                ["metadata_package", "subtitle_package"],
                ["metadata_seo_policy_context"],
                "Chưa có subtitle refs; operator cần biết draft/final.",
            )
        ambiguous = [
            ref
            for ref in refs
            if not (
                ref.get("lifecycle_state")
                or ref.get("status")
                or ref.get("is_final") is not None
            )
        ]
        if ambiguous:
            return _gate(
                "CaptionCoverageGate",
                "REVIEW_REQUIRED",
                ["SUBTITLE_LIFECYCLE_UNCLEAR"],
                ["metadata_package", "subtitle_package"],
                ["subtitle_lifecycle"],
                "Subtitle refs phải phân biệt draft/final khi có lifecycle.",
            )
        return _gate(
            "CaptionCoverageGate",
            "PASS",
            [],
            ["metadata_package", "subtitle_package"],
            ["subtitle_lifecycle"],
            "Subtitle refs đủ lifecycle để handoff.",
        )

    def _description_completeness(self, **kwargs: Any) -> PackagingGateResultRead:
        artifacts = kwargs["artifacts"]
        description = _description(artifacts)
        if not description:
            return _gate(
                "DescriptionCompletenessGate",
                "REVIEW_REQUIRED",
                ["DESCRIPTION_MISSING"],
                ["metadata_package"],
                ["metadata_seo_policy_context"],
                "Thiếu description paste-ready.",
            )
        if len(description.strip()) < 40:
            return _gate(
                "DescriptionCompletenessGate",
                "REVIEW_REQUIRED",
                ["DESCRIPTION_TOO_SHORT"],
                ["metadata_package"],
                ["metadata_seo_policy_context"],
                "Description quá ngắn cho handoff upload.",
            )
        return _gate(
            "DescriptionCompletenessGate",
            "PASS",
            [],
            ["metadata_package"],
            ["metadata_seo_policy_context"],
            "Description đủ để copy sang YouTube.",
        )

    def _thumbnail_truthfulness(self, **kwargs: Any) -> PackagingGateResultRead:
        thumbnail: ThumbnailHandoffRead = kwargs["thumbnail"]
        artifacts = kwargs["artifacts"]
        thumb_text = _text_blob(
            thumbnail.concept, thumbnail.text_overlay, thumbnail.main_subject
        )
        if not thumb_text:
            return _gate(
                "ThumbnailTruthfulnessGate",
                "REVIEW_REQUIRED",
                ["THUMBNAIL_BRIEF_MISSING"],
                ["thumbnail_brief"],
                ["thumbnail_style_context"],
                "Thiếu thumbnail brief.",
            )
        if _contains_any(
            thumb_text,
            [
                "shocking proof",
                "real proof",
                "actual result",
                "before after",
                "secret result",
            ],
        ) and not _claim_supported(
            thumb_text,
            _text_blob(_title(artifacts), _dict(artifacts.get("narration_script"))),
        ):
            return _gate(
                "ThumbnailTruthfulnessGate",
                "BLOCK",
                ["THUMBNAIL_MISLEADING_PROMISE"],
                ["thumbnail_brief", "narration_script"],
                ["thumbnail_style_context", "safety_forbidden_claims_context"],
                "Thumbnail đang hứa điều content không chứng minh.",
            )
        return _gate(
            "ThumbnailTruthfulnessGate",
            "PASS",
            [],
            ["thumbnail_brief", "narration_script"],
            ["thumbnail_style_context"],
            "Thumbnail brief không gây hiểu sai.",
        )

    def _mobile_thumbnail_legibility(self, **kwargs: Any) -> PackagingGateResultRead:
        thumbnail: ThumbnailHandoffRead = kwargs["thumbnail"]
        overlay = str(thumbnail.text_overlay or "")
        notes = str(thumbnail.mobile_readability_notes or "").lower()
        if (
            len(overlay) > 32
            or len(overlay.split()) > 5
            or _contains_any(notes, ["unreadable", "tiny", "too small", "blurry"])
        ):
            return _gate(
                "MobileThumbnailLegibilityGate",
                "BLOCK",
                ["THUMBNAIL_TEXT_NOT_MOBILE_LEGIBLE"],
                ["thumbnail_brief"],
                ["thumbnail_style_context.mobile_readability_rules"],
                "Rút ngắn text overlay để đọc được trên mobile.",
            )
        return _gate(
            "MobileThumbnailLegibilityGate",
            "PASS",
            [],
            ["thumbnail_brief"],
            ["thumbnail_style_context.mobile_readability_rules"],
            "Thumbnail text đủ ngắn cho mobile.",
        )

    def _character_thumbnail_consistency(
        self, **kwargs: Any
    ) -> PackagingGateResultRead:
        thumbnail: ThumbnailHandoffRead = kwargs["thumbnail"]
        effective_context: EffectiveChannelRuntimeContextSnapshot | None = kwargs[
            "effective_context"
        ]
        expected = (
            str(effective_context.character_image_branch_id)
            if effective_context and effective_context.character_image_branch_id
            else None
        )
        observed = (
            str(thumbnail.character_image_branch_id)
            if thumbnail.character_image_branch_id
            else None
        )
        if observed and expected and observed != expected:
            return _gate(
                "CharacterThumbnailConsistencyGate",
                "BLOCK",
                ["THUMBNAIL_CHARACTER_BRANCH_MISMATCH"],
                ["thumbnail_brief"],
                ["character_identity_context"],
                "Thumbnail phải dùng frozen character image branch.",
            )
        if observed and not expected:
            return _gate(
                "CharacterThumbnailConsistencyGate",
                "BLOCK",
                ["THUMBNAIL_CHARACTER_USED_WITHOUT_FROZEN_BRANCH"],
                ["thumbnail_brief"],
                ["character_identity_context"],
                "Không được dùng host/face khi context không bind character branch.",
            )
        return _gate(
            "CharacterThumbnailConsistencyGate",
            "PASS",
            [],
            ["thumbnail_brief"],
            ["character_identity_context"],
            "Character thumbnail khớp frozen context hoặc không dùng character.",
        )

    def _publish_timing_compliance(self, **kwargs: Any) -> PackagingGateResultRead:
        timing: PublishTimingRecommendationRead = kwargs["timing"]
        artifacts = kwargs["artifacts"]
        if _automation_requested(artifacts):
            return _gate(
                "PublishTimingComplianceGate",
                "BLOCK",
                ["PUBLISH_AUTOMATION_FORBIDDEN"],
                ["metadata_package", "publish_timing"],
                ["publish_timing_context"],
                "Publish timing chỉ là reminder; không được schedule/publish tự động.",
            )
        if not timing.manual_publish_only:
            return _gate(
                "PublishTimingComplianceGate",
                "BLOCK",
                ["MANUAL_PUBLISH_ONLY_FALSE"],
                ["publish_timing"],
                ["publish_timing_context"],
                "manual_publish_only phải luôn true.",
            )
        if not timing.configured_publish_window_json:
            return _gate(
                "PublishTimingComplianceGate",
                "REVIEW_REQUIRED",
                ["PUBLISH_WINDOW_MISSING"],
                ["publish_timing"],
                ["publish_timing_context"],
                "Thiếu khung giờ publish trong frozen context.",
            )
        return _gate(
            "PublishTimingComplianceGate",
            "PASS",
            [],
            ["publish_timing"],
            ["publish_timing_context"],
            "Publish timing là recommendation thủ công.",
        )

    def _manual_publish_only(self, **kwargs: Any) -> PackagingGateResultRead:
        artifacts = kwargs["artifacts"]
        if _automation_requested(artifacts):
            return _gate(
                "ManualPublishOnlyGate",
                "BLOCK",
                ["UPLOAD_OR_PUBLISH_AUTOMATION_ATTEMPT"],
                ["package_artifacts"],
                ["platform_strategy.publish_mode"],
                "VCOS chỉ handoff; mọi automation upload/publish bị chặn.",
            )
        return _gate(
            "ManualPublishOnlyGate",
            "PASS",
            [],
            ["package_artifacts"],
            ["platform_strategy.publish_mode"],
            "Không có upload/publish automation trong package.",
        )


def _extract_hook_spec(
    package: FirstScriptedVideoPackage,
    artifacts: dict[str, Any],
    effective_context: EffectiveChannelRuntimeContextSnapshot | None,
) -> HookSpecRead:
    script = _dict(artifacts.get("narration_script"))
    plan = _dict(artifacts.get("script_outline"))
    source = _dict(
        artifacts.get("hook_spec") or script.get("hook_spec") or plan.get("hook_spec")
    )
    first_sentence = _first_sentence(script)
    first_scene = _first_scene(artifacts)
    hook_type = _hook_type(
        source.get("hook_type") or source.get("type") or plan.get("hook_type")
    )
    payload = {
        "hook_type": hook_type,
        "first_3_seconds_script": _str_or_none(
            source.get("first_3_seconds_script")
            or source.get("script")
            or source.get("hook")
            or plan.get("hook")
            or first_sentence
        ),
        "first_3_seconds_visual": _str_or_none(
            source.get("first_3_seconds_visual") or source.get("visual") or first_scene
        ),
        "promise_made": _str_or_none(
            source.get("promise_made")
            or source.get("promise")
            or plan.get("promise_made")
        ),
        "payoff_location": _str_or_none(
            source.get("payoff_location")
            or source.get("payoff")
            or source.get("payoff_sentence_id")
        ),
        "clickbait_risk": _risk(source.get("clickbait_risk") or source.get("risk")),
        "visual_hook_relevance": _str_or_none(
            source.get("visual_hook_relevance") or source.get("visual_alignment_note")
        ),
        "title_hook_alignment": _str_or_none(
            source.get("title_hook_alignment") or source.get("title_alignment_note")
        ),
        "evidence_refs_json": _json_list(
            source.get("evidence_refs") or source.get("evidence_refs_json")
        ),
        "contract_paths_used_json": _strings(
            source.get("contract_paths_used_json")
            or source.get("contract_paths_used")
            or _applied_contract_paths(script)
        ),
    }
    content_hash = stable_hash({"package_id": str(package.id), "hook_spec": payload})
    return HookSpecRead(
        id=f"hookspec:{package.id}:{content_hash[:12]}",
        package_id=package.id,
        video_project_id=package.video_project_id,
        effective_context_snapshot_id=effective_context.id
        if effective_context
        else package.effective_context_snapshot_id,
        content_hash=content_hash,
        created_at=package.created_at,
        **payload,
    )


def _extract_upload_copy(
    *,
    package: FirstScriptedVideoPackage,
    artifacts: dict[str, Any],
    effective_context: EffectiveChannelRuntimeContextSnapshot | None,
    packaging_gate_status: str,
) -> UploadHandoffCopyRead:
    metadata = _dict(artifacts.get("metadata_package"))
    market = (
        _dict(effective_context.market_locale_context_json) if effective_context else {}
    )
    return UploadHandoffCopyRead(
        title=_str_or_none(metadata.get("title")),
        description=_str_or_none(metadata.get("description")),
        hashtags_json=_hashtags(metadata),
        subtitle_refs_json=_subtitle_refs(artifacts),
        disclosure_notes_json=_disclosure_notes(artifacts),
        checklist_items_json=_checklist_items(artifacts),
        language=_str_or_none(
            metadata.get("language")
            or metadata.get("content_language")
            or market.get("content_language")
        ),
        locale=_str_or_none(metadata.get("locale") or market.get("locale")),
        channel_contract_hash=effective_context.channel_contract_hash
        if effective_context
        else None,
        effective_context_snapshot_id=effective_context.id
        if effective_context
        else package.effective_context_snapshot_id,
        packaging_gate_status=packaging_gate_status,  # type: ignore[arg-type]
        source_artifact_refs_json=_source_artifact_refs(
            "metadata_package", "rights_disclosure_review"
        ),
    )


def _extract_thumbnail_handoff(
    artifacts: dict[str, Any],
    effective_context: EffectiveChannelRuntimeContextSnapshot | None,
) -> ThumbnailHandoffRead:
    thumb = _dict(artifacts.get("thumbnail_brief"))
    metadata = _dict(artifacts.get("metadata_package"))
    variant = _dict(_first(_list(thumb.get("variants"))))
    return ThumbnailHandoffRead(
        concept=_str_or_none(thumb.get("concept") or variant.get("concept")),
        text_overlay=_str_or_none(
            thumb.get("text_overlay")
            or variant.get("text")
            or variant.get("text_overlay")
        ),
        main_subject=_str_or_none(
            thumb.get("main_subject") or variant.get("main_subject")
        ),
        composition=_str_or_none(
            thumb.get("composition")
            or variant.get("composition")
            or variant.get("style")
        ),
        mobile_readability_notes=_str_or_none(
            thumb.get("mobile_readability_notes")
            or variant.get("mobile_readability_notes")
        ),
        thumbnail_ref=_safe_media_ref(
            thumb.get("thumbnail_ref")
            or metadata.get("thumbnail_ref")
            or metadata.get("planned_thumbnail_ref")
        ),
        drive_ref=_safe_media_ref(
            thumb.get("drive_ref") or metadata.get("thumbnail_drive_ref")
        ),
        character_image_branch_id=thumb.get("character_image_branch_id")
        or thumb.get("character_branch_id")
        or variant.get("character_image_branch_id"),
        reference_asset_pack_id=thumb.get("reference_asset_pack_id")
        or variant.get("reference_asset_pack_id")
        or (effective_context.reference_asset_pack_id if effective_context else None),
        thumbnail_variant_plan_json=thumb.get("thumbnail_variant_plan_json")
        or thumb.get("variants"),
        contract_paths_used_json=_strings(
            thumb.get("contract_paths_used_json")
            or _applied_contract_paths(thumb)
            or ["thumbnail_style_context"]
        ),
        source_artifact_refs_json=_source_artifact_refs(
            "thumbnail_brief", "metadata_package"
        ),
    )


def _publish_timing_recommendation(
    package: FirstScriptedVideoPackage,
    effective_context: EffectiveChannelRuntimeContextSnapshot | None,
    artifacts: dict[str, Any],
) -> PublishTimingRecommendationRead:
    timing = (
        _dict(effective_context.publish_timing_context_json)
        if effective_context
        else {}
    )
    override = _dict(
        artifacts.get("manual_publish_timing_override")
        or artifacts.get("publish_timing")
    )
    channel_tz = _str_or_none(timing.get("channel_timezone"))
    audience_tz = _str_or_none(timing.get("audience_timezone") or channel_tz)
    configured_window = (
        timing.get("configured_publish_window")
        or timing.get("suggested_publish_window_policy")
        or override.get("configured_publish_window")
        or override.get("manual_publish_window")
    )
    if not configured_window and override.get("publish_window_state"):
        configured_window = {
            "source": "manual_publish_timing_override",
            "state": override.get("publish_window_state"),
            "manual_publish_only": override.get("manual_publish_only", True),
        }
    operator_tz = (
        os.getenv("VCOS_OPERATOR_TIMEZONE") or os.getenv("TZ") or "Asia/Ho_Chi_Minh"
    )
    reason_codes: list[str] = []
    suggested_channel, suggested_operator = _suggest_times(
        package.created_at,
        channel_tz,
        operator_tz,
        configured_window,
        reason_codes,
    )
    if effective_context is None:
        reason_codes.append("EFFECTIVE_CONTEXT_SNAPSHOT_MISSING")
    if not configured_window:
        reason_codes.append("PUBLISH_WINDOW_MISSING")
    if _automation_requested(artifacts):
        reason_codes.append("PUBLISH_AUTOMATION_FORBIDDEN")
    return PublishTimingRecommendationRead(
        channel_timezone=channel_tz,
        audience_timezone=audience_tz,
        operator_local_timezone=operator_tz,
        configured_publish_window_json=configured_window,
        suggested_publish_time_channel_tz=suggested_channel,
        suggested_publish_time_operator_local=suggested_operator,
        publish_timing_policy_ref=f"effective_context:{effective_context.id}:publish_timing_context"
        if effective_context
        else None,
        manual_publish_only=True,
        source_contract_paths=_strings(
            timing.get("source_contract_paths")
            or ["platform_strategy.publish_mode", "market_locale.timezone"]
        ),
        reason_codes_json=sorted(set(reason_codes)),
    )


def _gate_summary(
    gate_results: list[PackagingGateResultRead], r3d4_refs: list[str]
) -> PackagingGateSummaryRead:
    if any(result.status == "BLOCK" for result in gate_results):
        status = "BLOCK"
        next_action = "Sửa gate BLOCK trước khi operator upload thủ công."
    elif any(result.status == "REVIEW_REQUIRED" for result in gate_results):
        status = "REVIEW_REQUIRED"
        next_action = "Review các gate cần kiểm tra trước khi upload thủ công."
    else:
        status = "PASS"
        next_action = "Có thể dùng handoff để upload thủ công ngoài VCOS, rồi paste-back video_id."
    return PackagingGateSummaryRead(
        overall_status=status,  # type: ignore[arg-type]
        gate_results=gate_results,
        r3d4_gate_batch_refs=r3d4_refs,
        next_action_vi=next_action,
    )


def _gate(
    key: str,
    status: str,
    codes: list[str],
    artifact_keys: list[str],
    contract_paths: list[str],
    summary: str,
    next_action: str | None = None,
) -> PackagingGateResultRead:
    return PackagingGateResultRead(
        gate_key=key,
        status=status,  # type: ignore[arg-type]
        reason_codes=sorted(set(codes)),
        checked_artifact_refs=[{"artifact_key": item} for item in artifact_keys],
        checked_contract_paths=contract_paths,
        summary_vi=summary,
        next_action_vi=next_action or (None if status == "PASS" else summary),
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _json_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in _list(value) if isinstance(item, dict)]


def _strings(value: Any) -> list[str]:
    result: list[str] = []
    for item in _list(value):
        if item not in (None, ""):
            result.append(str(item))
    return result


def timing_timezone(artifacts: dict[str, Any]) -> str | None:
    timing = _dict(
        artifacts.get("publish_timing")
        or artifacts.get("publish_timing_recommendation")
    )
    value = timing.get("channel_timezone") or timing.get("timezone")
    return str(value) if value else None


def _str_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _first(value: list[Any]) -> Any:
    return value[0] if value else None


def _hook_type(value: Any) -> str:
    normalized = str(value or "OTHER").upper()
    return (
        normalized
        if normalized in {"DIRECT", "CONTRAST", "RISK", "OUTCOME", "QUESTION", "OTHER"}
        else "OTHER"
    )


def _risk(value: Any) -> str:
    normalized = str(value or "MEDIUM").upper()
    return normalized if normalized in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"


def _first_sentence(script: dict[str, Any]) -> str | None:
    sentence = _dict(
        _first(
            [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
        )
    )
    return _str_or_none(sentence.get("text"))


def _first_scene(artifacts: dict[str, Any]) -> str | None:
    scene = _dict(
        _first(
            [
                item
                for item in _list(_dict(artifacts.get("visual_plan")).get("scenes"))
                if isinstance(item, dict)
            ]
        )
    )
    if not scene:
        return None
    return _str_or_none(
        scene.get("description")
        or scene.get("visual")
        or scene.get("kind")
        or scene.get("intended_visual_source")
    )


def _title(artifacts: dict[str, Any]) -> str:
    return str(_dict(artifacts.get("metadata_package")).get("title") or "")


def _description(artifacts: dict[str, Any]) -> str:
    return str(_dict(artifacts.get("metadata_package")).get("description") or "")


def _hashtags(metadata: dict[str, Any]) -> list[str] | None:
    values = (
        metadata.get("hashtags_json")
        or metadata.get("hashtags")
        or metadata.get("tags")
    )
    strings = _strings(values)
    return strings or None


def _subtitle_refs(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _dict(artifacts.get("metadata_package"))
    refs = _json_list(
        metadata.get("subtitle_refs_json")
        or metadata.get("subtitle_refs")
        or metadata.get("caption_refs")
    )
    for key in ("subtitle_package", "caption_track", "srt"):
        payload = _dict(artifacts.get(key))
        if payload:
            refs.append({"artifact_key": key, **payload})
    return refs


def _disclosure_notes(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for artifact_key in ("metadata_package", "rights_disclosure_review"):
        artifact = _dict(artifacts.get(artifact_key))
        for value in _list(
            artifact.get("disclosure_notes_json")
            or artifact.get("disclosure_notes")
            or artifact.get("disclosure_refs")
        ):
            notes.append(
                value
                if isinstance(value, dict)
                else {"artifact_key": artifact_key, "text": str(value)}
            )
    return notes


def _checklist_items(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = _dict(artifacts.get("metadata_package"))
    raw = (
        metadata.get("checklist_items_json")
        or metadata.get("checklist_items")
        or metadata.get("checklist")
    )
    items = [
        item if isinstance(item, dict) else {"item": str(item), "state": "PENDING"}
        for item in _list(raw)
    ]
    checklist = _dict(artifacts.get("human_review_checklist"))
    items.extend(
        {"item": key, "state": value} for key, value in sorted(checklist.items())
    )
    return items


def _source_artifact_refs(*keys: str) -> list[dict[str, Any]]:
    return [{"artifact_key": key} for key in keys]


def _applied_contract_paths(artifact: Any) -> list[str]:
    refs = _dict(_dict(artifact).get("applied_context_refs"))
    return _strings(refs.get("relevant_contract_paths_used"))


def _safe_media_ref(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (
            None
            if value.startswith("/") or "/Users/" in value or "file://" in value
            else value
        )
    if isinstance(value, dict):
        return {
            key: item
            for key, item in value.items()
            if key not in {"file_path", "local_path", "path"}
            and not (
                isinstance(item, str)
                and (item.startswith("/") or "/Users/" in item or "file://" in item)
            )
        }
    return value


def _text_blob(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            parts.extend(f"{key} {_text_blob(item)}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(_text_blob(item) for item in value)
        else:
            parts.append(str(value))
    return " ".join(part for part in parts if part).strip()


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_]{4,}", value.lower())
        if token not in _STOPWORDS
    }


def _claim_supported(claim: str, corpus: str, *, minimum_ratio: float = 0.35) -> bool:
    claim_tokens = _tokens(claim)
    if not claim_tokens:
        return True
    corpus_tokens = _tokens(corpus)
    if not corpus_tokens:
        return False
    overlap = claim_tokens & corpus_tokens
    return (len(overlap) / max(1, len(claim_tokens))) >= minimum_ratio


def _payoff_location_exists(location: str, script: dict[str, Any]) -> bool:
    normalized = location.strip().lower()
    if not normalized:
        return False
    if normalized in _text_blob(script).lower():
        return True
    for item in _list(script.get("sentences")):
        sentence = _dict(item)
        if normalized in {
            str(sentence.get("sentence_id") or "").lower(),
            str(sentence.get("section") or "").lower(),
        }:
            return True
    return False


def _contains_any(value: str, needles: list[str]) -> bool:
    lower = value.lower()
    return any(needle.lower() in lower for needle in needles)


def _unsupported_offer_terms(text: str) -> list[str]:
    terms = [
        "free checklist",
        "download checklist",
        "template download",
        "product demo",
        "free demo",
        "freebie",
    ]
    lower = text.lower()
    return [term for term in terms if term in lower]


def _has_real_offer_manifest(artifacts: dict[str, Any]) -> bool:
    return bool(
        artifacts.get("asset_manifest")
        or artifacts.get("funnel_manifest")
        or artifacts.get("download_manifest")
    )


def _automation_requested(artifacts: dict[str, Any]) -> bool:
    text = _text_blob(
        _dict(artifacts.get("metadata_package")),
        _dict(artifacts.get("publish_handoff")),
        _dict(artifacts.get("runtime_guard")),
    ).lower()
    if any(
        term in text
        for term in [
            "auto_publish",
            "scheduled_by_agent",
            "youtube_upload_api",
            "publish_now",
            "reupload_now",
        ]
    ):
        return True
    for artifact in artifacts.values():
        if isinstance(artifact, dict) and any(
            artifact.get(key) is True
            for key in (
                "auto_publish",
                "scheduled_by_agent",
                "youtube_upload_api",
                "upload_to_youtube",
                "publish_now",
                "reupload_now",
            )
        ):
            return True
    return False


def _suggest_times(
    created_at: datetime,
    channel_timezone: str | None,
    operator_timezone: str,
    configured_window: Any,
    reason_codes: list[str],
) -> tuple[str | None, str | None]:
    window = _first_window(configured_window)
    start_value = _window_start(window)
    if not channel_timezone or not start_value:
        return None, None
    try:
        channel_zone = ZoneInfo(channel_timezone)
    except ZoneInfoNotFoundError:
        reason_codes.append("CHANNEL_TIMEZONE_INVALID")
        return None, None
    try:
        operator_zone = ZoneInfo(operator_timezone)
    except ZoneInfoNotFoundError:
        operator_zone = channel_zone
        reason_codes.append("OPERATOR_TIMEZONE_INVALID_USING_CHANNEL_TZ")
    start_time = _parse_hhmm(start_value)
    if start_time is None:
        reason_codes.append("PUBLISH_WINDOW_START_INVALID")
        return None, None
    base = created_at.astimezone(channel_zone)
    candidate = base.replace(
        hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0
    )
    target_weekday = _weekday(window)
    if target_weekday is not None:
        for offset in range(8):
            current = candidate + timedelta(days=offset)
            if current.weekday() == target_weekday and current > base:
                candidate = current
                break
    elif candidate <= base:
        candidate = candidate + timedelta(days=1)
    return candidate.isoformat(), candidate.astimezone(operator_zone).isoformat()


def _first_window(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return _dict(_first(value))
    if isinstance(value, dict):
        for key in ("windows", "preferred_publish_windows", "publish_windows"):
            if isinstance(value.get(key), list) and value[key]:
                return _dict(value[key][0])
        return value
    if isinstance(value, str):
        return {"start": value}
    return {}


def _window_start(window: dict[str, Any]) -> str | None:
    for key in ("start", "start_time", "from", "time", "local_time"):
        value = window.get(key)
        if value:
            return str(value)
    return None


def _parse_hhmm(value: str) -> time | None:
    match = re.match(r"^(\d{1,2}):(\d{2})", value)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def _weekday(window: dict[str, Any]) -> int | None:
    raw = str(window.get("day") or window.get("weekday") or "").upper()
    mapping = {
        "MONDAY": 0,
        "TUESDAY": 1,
        "WEDNESDAY": 2,
        "THURSDAY": 3,
        "FRIDAY": 4,
        "SATURDAY": 5,
        "SUNDAY": 6,
        "MON": 0,
        "TUE": 1,
        "WED": 2,
        "THU": 3,
        "FRI": 4,
        "SAT": 5,
        "SUN": 6,
    }
    return mapping.get(raw)
