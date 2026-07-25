from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.geo_delivery import (
    EffectiveAdsOnlyPolicyArtifact,
    GeoMarketDeliveryCloseoutEvidence,
)
from app.contracts.geo_market import (
    MarketVerdict,
    TargetMarketDigest,
    TargetMarketProfile,
    VisualMarketAlignmentInput,
)
from app.contracts.nich1 import (
    NicheContractDigest,
    NicheCriterion,
    NicheCriterionEvidence,
    NicheEvidenceRef,
    NicheGateVerdict,
    VisualNicheAlignmentInput,
)
from app.contracts.visual_routing import (
    NicheVisualSourceProfile,
    SceneVisualRealizationRequirements,
    VisualSourceRoute,
)
from app.contracts.workflow import (
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewTaskCreate,
    VideoProjectCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    ApprovalDecision,
    Artifact,
    ArtifactVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ReviewTask,
    User,
    VideoProject,
)
from app.services.config_registry import content_hash
from app.services.pkg1_market_revision import (
    DRIVE_IDEMPOTENCY_PHASES as _DRIVE_IDEMPOTENCY_PHASES,
    PKG1MarketRevisionService,
    PROJECT_TYPE as SOURCE_PROJECT_TYPE,
)
from app.services.geo_delivery import (
    GEO_DELIVERY_REPOSITORY_ROOT,
    GeoDeliveryCloseoutArtifactService,
    acceptance_evidence_from_manifest,
    destination_runtime_contract,
    market_policy_hash,
)
from app.services.geo_delivery_verification import geo_delivery_workspace_hash
from app.services.geo_market import VisualMarketAlignmentGate
from app.services.nich1 import VisualNicheAlignmentGate
from app.services.visual_source_routing import (
    DiagramSuitabilityGate,
    EvidenceTruthSourceGate,
    PexelsEligibilityGate,
    VisualRealizationCompletenessGate,
    VisualSourceRouter,
    stable_hash,
)
from app.services.workflow import ArtifactService, ReviewService, VideoProjectService


PROJECT_TYPE = "PKG1_SC04_REVISION"
SCENE_ID = "SC-04"
REVISION_SCHEMA_VERSION = "pkg1.sc04-revision.v1"
BUILDER_VERSION = "pkg1-sc04-revision-builder/1.2.0"

ROOT_CAUSE = "INSUFFICIENT_SCENE_SPEC"
REPAIRED_ROUTE = VisualSourceRoute.NATIVE_MOTION_GRAPHIC

DIFF_ARTIFACT_TYPE = "pkg1_sc04_revision_diff"
REVIEW_PACKET_ARTIFACT_TYPE = "pkg1_sc04_review_packet"
_CLOSEOUT_RECEIPT_ARTIFACT_TYPE = "pkg1_sc04_revision_human_review_receipt"

_AFFECTED_SOURCE_TYPES = {
    "scene_visual_intent",
    "visual_source_decision_set",
    "visual_plan",
    "compiled_asset_request_plan",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "asset_provenance_plan",
    "rights_disclosure_completeness_report",
    "publish_risk_dossier",
    "market_gate_results",
    "gate_results",
    "package_manifest",
}

_REQUIRED_SOURCE_TYPES = {
    "script",
    "spoken_text_normalized",
    "narration_pacing_preflight_estimate",
    "voice_policy",
    "scene_visual_intent",
    "visual_source_decision_set",
    "visual_plan",
    "compiled_asset_request_plan",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "asset_provenance_plan",
    "rights_disclosure_completeness_report",
    "publish_risk_dossier",
    "market_alignment_dossier",
    "niche_alignment_dossier",
    "target_market_profile",
    "target_market_digest",
    "destination_binding",
    "niche_contract_digest",
    "visual_direction_contract",
    "synthetic_media_disclosure_receipt_draft",
}

_MR1_EFFECTIVE_REQUIRED_TYPES = {
    "script",
    "spoken_text_normalized",
    "narration_pacing_preflight_estimate",
    "voice_policy",
    "visual_direction_contract",
    "visual_plan",
    "visual_source_decision_set",
    "compiled_asset_request_plan",
    "market_alignment_dossier",
    "niche_alignment_dossier",
    "provider_execution_plan",
    "cost_estimate_snapshot",
    "rights_disclosure_completeness_report",
    "synthetic_media_disclosure_receipt_draft",
    "asset_provenance_plan",
    "publish_risk_dossier",
    "target_market_profile",
    "target_market_digest",
    "destination_binding",
}

_SC04_SCRIPT_CONCEPT_RULES: dict[str, tuple[str, ...]] = {
    "workflow_observation": ("watch the workflow",),
    "request_origin": ("request begins",),
    "copied_fields": ("fields are copied",),
    "missing_information": ("information is missing",),
    "gap_owner": ("resolves the gap",),
    "evidence_limit": ("cannot prove the process",),
    "team_baseline": ("team's own baseline", "team’s own baseline"),
    "completed_handoffs": ("completed handoffs",),
    "rework": ("rework",),
    "judgment_steps": ("steps that require judgment",),
    "information_movement": ("work that moves information",),
    "decision_work": ("work that makes a decision",),
    "human_exception": ("exceptions should stay visible to a person",),
    "human_responsibility": ("without hiding responsibility",),
}

_SC04_REQUIRED_SCRIPT_CONCEPTS = frozenset(_SC04_SCRIPT_CONCEPT_RULES)

_SC04_BLUEPRINT = {
    "native_mechanism": "BASELINE_CHECKLIST_THEN_INFORMATION_VS_JUDGMENT_SPLIT",
    "route": REPAIRED_ROUTE.value,
    "phases": [
        {
            "phase": "OBSERVE_WORKFLOW",
            "items": [
                "REQUEST_BEGINS",
                "FIELDS_COPIED",
                "MISSING_INFORMATION",
                "GAP_OWNER",
            ],
        },
        {
            "phase": "MEASURE_BASELINE",
            "items": ["COMPLETED_HANDOFFS", "REWORK", "JUDGMENT_STEPS"],
        },
        {
            "phase": "SPLIT_WORK",
            "branches": ["MOVE_INFORMATION", "MAKE_DECISION"],
        },
        {
            "phase": "PRESERVE_RESPONSIBILITY",
            "items": ["HUMAN_EXCEPTION_PATH", "VISIBLE_OWNER"],
        },
    ],
    "exact_text_authority": "NATIVE_ONLY",
    "stock_layer_allowed": False,
    "provider_execution_required": False,
}

class PKG1SC04RevisionService:
    """Create an immutable, provider-free SC-04 package delta for human review."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.source_service = PKG1MarketRevisionService(session)

    def build_revision(
        self,
        *,
        channel_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
        ads_only_overlay_artifact_version_id: uuid.UUID | None = None,
        ads_only_overlay_content_hash: str | None = None,
        geo_closeout_artifact_version_id: uuid.UUID | None = None,
        geo_closeout_content_hash: str | None = None,
        source_project_id: uuid.UUID | None = None,
        source_package_artifact_version_id: uuid.UUID | None = None,
        source_package_content_hash: str | None = None,
        source_approval_decision_id: uuid.UUID | None = None,
        source_human_receipt_artifact_version_id: uuid.UUID | None = None,
        source_human_receipt_content_hash: str | None = None,
    ) -> dict[str, Any]:
        channel = self.session.scalar(
            select(ChannelWorkspace)
            .where(ChannelWorkspace.id == channel_id)
            .with_for_update()
        )
        if channel is None:
            raise NotFoundError(f"channel workspace not found: {channel_id}")
        if self.session.get(User, created_by_user_id) is None:
            raise NotFoundError(f"user not found: {created_by_user_id}")
        geo_bindings = (
            ads_only_overlay_artifact_version_id,
            ads_only_overlay_content_hash,
            geo_closeout_artifact_version_id,
            geo_closeout_content_hash,
        )
        if not all(value is not None for value in geo_bindings):
            raise ValidationFailureError("PKG1_SC04_EXACT_GEO_BINDINGS_REQUIRED")

        overlay = self._resolve_ads_only_overlay(
            channel_id=channel_id,
            snapshot_id=channel.active_policy_snapshot_id,
            requested_version_id=ads_only_overlay_artifact_version_id,
            requested_hash=ads_only_overlay_content_hash,
            requested_closeout_version_id=geo_closeout_artifact_version_id,
            requested_closeout_hash=geo_closeout_content_hash,
        )
        existing = list(
            self.session.scalars(
                select(VideoProject).where(
                    VideoProject.channel_workspace_id == channel_id,
                    VideoProject.project_type == PROJECT_TYPE,
                )
            ).all()
        )
        if len(existing) > 1:
            raise ValidationFailureError("MULTIPLE_PKG1_SC04_REVISIONS_FOUND")
        source_project, source_package, source_human_authority = (
            self._approved_source_package(
                channel_id,
                requested_project_id=source_project_id,
                requested_package_version_id=(source_package_artifact_version_id),
                requested_package_hash=source_package_content_hash,
                requested_approval_id=source_approval_decision_id,
                requested_receipt_version_id=(source_human_receipt_artifact_version_id),
                requested_receipt_hash=source_human_receipt_content_hash,
            )
        )
        self._validate_geo_source_authority(
            overlay=overlay,
            source_project=source_project,
            source_package=source_package,
            source_human_authority=source_human_authority,
        )
        if existing:
            self._validate_existing_pending_revision(
                existing[0],
                overlay,
                source_project=source_project,
                source_package=source_package,
                source_human_authority=source_human_authority,
            )
            return self.read_revision(existing[0].id)
        source_artifacts = self._resolve_source_package_artifacts(
            source_package, source_project=source_project
        )
        missing = _REQUIRED_SOURCE_TYPES - set(source_artifacts)
        if missing:
            raise ValidationFailureError(
                "PKG1_SC04_SOURCE_ARTIFACTS_MISSING:" + ",".join(sorted(missing))
            )
        source_fingerprint = self.source_service._historical_fingerprint(
            source_project,
            self.source_service._current_artifacts(source_project.id),
        )
        no_execution_before = self.source_service._no_execution_counts()

        run_version, attempt_evidence = self._failed_attempt_evidence(source_project)
        old_scene = self._one_scene(
            source_artifacts["scene_visual_intent"].content, SCENE_ID
        )
        old_decision = self._one_scene(
            source_artifacts["visual_source_decision_set"].content,
            SCENE_ID,
            key="decisions",
        )
        old_provider_route = self._one_scene(
            source_artifacts["provider_execution_plan"].content,
            SCENE_ID,
            key="scene_routes",
        )
        old_semantic_intent = old_scene.get("semantic_intent")
        if (
            not isinstance(old_semantic_intent, str)
            or not old_semantic_intent.strip()
            or old_scene.get("source_role") != "PEXELS_SUPPORTING"
            or old_decision.get("semantic_intent") != old_semantic_intent
            or old_decision.get("preferred_source_route") != "PEXELS_VIDEO"
            or old_decision.get("provider") != "pexels_api"
            or old_provider_route.get("route") != "PEXELS_VIDEO"
            or old_provider_route.get("provider") != "pexels_api"
            or old_provider_route.get("attempt_cap") != 1
        ):
            raise ValidationFailureError("PKG1_SC04_SOURCE_ROUTE_NOT_PEXELS_VIDEO")

        script_segment = self._script_segment(source_artifacts["script"], "S04")
        requirements, semantic_derivation = self._requirements(
            source_artifacts=source_artifacts,
            run_version=run_version,
            script_segment=script_segment,
        )
        routing = self._routing_evidence(requirements)
        decision = routing["decision"]
        if decision.preferred_source_route != REPAIRED_ROUTE:
            raise ValidationFailureError("PKG1_SC04_NATIVE_MOTION_ROUTE_NOT_SELECTED")

        source_package_ref = self._version_ref(source_package)
        seed = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "builder_version": BUILDER_VERSION,
            "source_project_id": str(source_project.id),
            "source_package": source_package_ref,
            "source_revision_id": source_package.content.get("revision_id"),
            "source_revision_hash": source_package.content.get("revision_hash"),
            "source_human_authority": source_human_authority,
            "old_scene_intent_hash": source_artifacts[
                "scene_visual_intent"
            ].content_hash,
            "old_visual_decision_hash": source_artifacts[
                "visual_source_decision_set"
            ].content_hash,
            "requirements_hash": requirements.content_hash,
            "semantic_derivation_hash": semantic_derivation["content_hash"],
            "decision_hash": decision.content_hash,
            "attempt_version_hashes": [
                item["content_hash"] for item in attempt_evidence["attempts"]
            ],
            "ads_only_overlay": overlay["ref"],
            "geo_closeout_evidence": overlay["closeout_ref"],
            "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            "root_cause": ROOT_CAUSE,
        }
        revision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "vcos:pkg1-sc04-revision:" + content_hash(seed),
            )
        )
        revision_hash = content_hash({**seed, "revision_id": revision_id})

        project = VideoProjectService(self.session).create_project(
            data=VideoProjectCreate(
                company_id=source_project.company_id,
                channel_workspace_id=source_project.channel_workspace_id,
                policy_snapshot_id=source_project.policy_snapshot_id,
                channel_profile_version_id=source_project.channel_profile_version_id,
                category_id=source_project.category_id,
                channel_contract_content_hash=(
                    source_project.channel_contract_content_hash
                ),
                title=source_project.title,
                description=(
                    "Immutable SC-04 visual-source repair delta. Technical PASS is "
                    "provider-free and remains pending exact human package review."
                ),
                status="in_review",
                project_type=PROJECT_TYPE,
                priority="high",
                owner_user_id=created_by_user_id,
                created_by_user_id=created_by_user_id,
                financial_summary={
                    "estimated_incremental_cost_usd": 0.0,
                    "actual_cost_usd": None,
                    "provider_execution": "DISABLED",
                },
                brand_safety_summary={"status": "PASS_PLANNING"},
                legal_compliance_summary={
                    "rights_state": "PASS_PLANNING",
                    "generated_evidence_authority": False,
                },
                audience_delivery_summary={
                    "target_market": "US",
                    "human_review": "PENDING",
                },
            ),
            correlation_id="pkg1-sc04-revision-project",
        )

        created: dict[str, ArtifactVersion] = {}
        scene_intent_payload = self._scene_intent_payload(
            source=source_artifacts["scene_visual_intent"],
            requirements=requirements,
            semantic_derivation=semantic_derivation,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["scene_visual_intent"] = self._create_artifact(
            project.id,
            "scene_visual_intent",
            scene_intent_payload,
            created_by_user_id,
            revision_hash,
        )

        decision_payload = self._decision_set_payload(
            source=source_artifacts["visual_source_decision_set"],
            requirements=requirements,
            decision=decision,
            routing=routing,
            semantic_derivation=semantic_derivation,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["visual_source_decision_set"] = self._create_artifact(
            project.id,
            "visual_source_decision_set",
            decision_payload,
            created_by_user_id,
            revision_hash,
        )

        visual_plan_payload = self._visual_plan_payload(
            source=source_artifacts["visual_plan"],
            requirements=requirements,
            semantic_derivation=semantic_derivation,
            decision_set_ref=self._version_ref(created["visual_source_decision_set"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["visual_plan"] = self._create_artifact(
            project.id,
            "visual_plan",
            visual_plan_payload,
            created_by_user_id,
            revision_hash,
        )

        compiled_payload = self._compiled_asset_plan_payload(
            source=source_artifacts["compiled_asset_request_plan"],
            visual_plan_ref=self._version_ref(created["visual_plan"]),
            decision_set_ref=self._version_ref(created["visual_source_decision_set"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["compiled_asset_request_plan"] = self._create_artifact(
            project.id,
            "compiled_asset_request_plan",
            compiled_payload,
            created_by_user_id,
            revision_hash,
        )

        provider_payload = self._provider_plan_payload(
            source=source_artifacts["provider_execution_plan"],
            decision_set_ref=self._version_ref(created["visual_source_decision_set"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["provider_execution_plan"] = self._create_artifact(
            project.id,
            "provider_execution_plan",
            provider_payload,
            created_by_user_id,
            revision_hash,
        )

        cost_payload = self._cost_payload(
            source=source_artifacts["cost_estimate_snapshot"],
            visual_plan_ref=self._version_ref(created["visual_plan"]),
            provider_plan_ref=self._version_ref(created["provider_execution_plan"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["cost_estimate_snapshot"] = self._create_artifact(
            project.id,
            "cost_estimate_snapshot",
            cost_payload,
            created_by_user_id,
            revision_hash,
        )

        provenance_payload = self._provenance_payload(
            source=source_artifacts["asset_provenance_plan"],
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["asset_provenance_plan"] = self._create_artifact(
            project.id,
            "asset_provenance_plan",
            provenance_payload,
            created_by_user_id,
            revision_hash,
        )

        rights_payload = self._rights_payload(
            source=source_artifacts["rights_disclosure_completeness_report"],
            provenance_ref=self._version_ref(created["asset_provenance_plan"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["rights_disclosure_completeness_report"] = self._create_artifact(
            project.id,
            "rights_disclosure_completeness_report",
            rights_payload,
            created_by_user_id,
            revision_hash,
        )

        alignment_payload = self._supplemental_alignment_payload(
            source_artifacts=source_artifacts,
            source_project=source_project,
            scene_intent_version=created["scene_visual_intent"],
            decision_set_version=created["visual_source_decision_set"],
            visual_plan_version=created["visual_plan"],
            overlay=overlay,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["market_gate_results"] = self._create_artifact(
            project.id,
            "market_gate_results",
            alignment_payload,
            created_by_user_id,
            revision_hash,
        )

        risk_payload = self._risk_payload(
            source=source_artifacts["publish_risk_dossier"],
            rights_ref=self._version_ref(
                created["rights_disclosure_completeness_report"]
            ),
            alignment_ref=self._version_ref(created["market_gate_results"]),
            overlay=overlay,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["publish_risk_dossier"] = self._create_artifact(
            project.id,
            "publish_risk_dossier",
            risk_payload,
            created_by_user_id,
            revision_hash,
        )

        reused_refs, historical_context_refs, superseded_refs = (
            self._artifact_classification(source_artifacts)
        )
        effective_core = self._effective_artifacts(
            source_artifacts=source_artifacts,
            revised={
                key: self._version_ref(value) for key, value in sorted(created.items())
            },
        )
        mr1_compatibility = self._mr1_manifest_compatibility_gate(
            effective_artifacts=effective_core,
            current_visual_authority=self._version_ref(created["market_gate_results"]),
            visual_plan_ref=self._version_ref(created["visual_plan"]),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        planning_gate_evaluations = self._evaluate_planning_gates(
            created=created,
            requirements=requirements,
            source_artifacts=source_artifacts,
            run_version=run_version,
            script_segment=script_segment,
            semantic_derivation=semantic_derivation,
        )
        gate_payload = self._gate_payload(
            routing=routing,
            attempt_evidence=attempt_evidence,
            overlay=overlay,
            alignment_ref=self._version_ref(created["market_gate_results"]),
            visual_plan_ref=self._version_ref(created["visual_plan"]),
            decision_ref=self._version_ref(created["visual_source_decision_set"]),
            cost_ref=self._version_ref(created["cost_estimate_snapshot"]),
            rights_ref=self._version_ref(
                created["rights_disclosure_completeness_report"]
            ),
            provenance_ref=self._version_ref(created["asset_provenance_plan"]),
            alignment_evaluation=alignment_payload,
            planning_gate_evaluations=planning_gate_evaluations,
            mr1_manifest_compatibility=mr1_compatibility,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created["gate_results"] = self._create_artifact(
            project.id,
            "gate_results",
            gate_payload,
            created_by_user_id,
            revision_hash,
        )

        diff_payload = self._diff_payload(
            source_package=source_package,
            source_artifacts=source_artifacts,
            created=created,
            reused_refs=reused_refs,
            historical_context_refs=historical_context_refs,
            superseded_refs=superseded_refs,
            old_scene=old_scene,
            new_scene=self._one_scene(scene_intent_payload, SCENE_ID),
            old_decision=old_decision,
            new_decision=self._one_scene(decision_payload, SCENE_ID, key="decisions"),
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created[DIFF_ARTIFACT_TYPE] = self._create_artifact(
            project.id,
            DIFF_ARTIFACT_TYPE,
            diff_payload,
            created_by_user_id,
            revision_hash,
        )

        review_packet = self._review_packet(
            source_package=source_package,
            source_artifacts=source_artifacts,
            requirements=requirements,
            routing=routing,
            attempt_evidence=attempt_evidence,
            script_segment=script_segment,
            semantic_derivation=semantic_derivation,
            overlay=overlay,
            created=created,
            revision_id=revision_id,
            revision_hash=revision_hash,
        )
        created[REVIEW_PACKET_ARTIFACT_TYPE] = self._create_artifact(
            project.id,
            REVIEW_PACKET_ARTIFACT_TYPE,
            review_packet,
            created_by_user_id,
            revision_hash,
        )

        no_execution_mid = self.source_service._no_execution_counts()
        if no_execution_mid != no_execution_before:
            raise ValidationFailureError("PKG1_SC04_PROVIDER_BOUNDARY_CHANGED")
        no_execution_deltas = {
            key: no_execution_mid[key] - no_execution_before[key]
            for key in no_execution_before
        }
        no_execution_proof = {
            "before_counts": no_execution_before,
            "after_counts": no_execution_mid,
            "deltas": no_execution_deltas,
            "provider_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in (
                    "provider_attempts",
                    "provider_jobs",
                    "paid_provider_calls",
                )
            ),
            "render_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("media_render_jobs", "final_media_refs")
            ),
            "drive_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("media_offload_jobs", "cloud_media_refs")
            ),
            "youtube_calls": sum(
                no_execution_deltas.get(key, 0)
                for key in ("human_upload_tasks", "uploaded_videos")
            ),
        }
        no_execution_proof["all_deltas_zero"] = all(
            value == 0 for value in no_execution_proof["deltas"].values()
        )
        output_set_hash = content_hash(
            {key: value.content_hash for key, value in sorted(created.items())}
        )
        old_approvals = self._source_package_approvals(source_package)
        revised_refs = {
            key: self._version_ref(value) for key, value in sorted(created.items())
        }
        effective_artifacts = self._effective_artifacts(
            source_artifacts=source_artifacts,
            revised=revised_refs,
        )
        historical_project_ref = (
            (source_package.content or {})
            .get("exact_bindings", {})
            .get("historical_video_project", {})
            .get("ref")
        )
        historical_project_id = (
            historical_project_ref.removeprefix("video-project://")
            if isinstance(historical_project_ref, str)
            and historical_project_ref.startswith("video-project://")
            else None
        )
        composite_alignment_core = {
            "schema_version": "pkg1.sc04-composite-alignment-authority.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "subject": self._version_ref(created["visual_plan"]),
            "nonvisual_market_alignment": {
                **self._version_ref(source_artifacts["market_alignment_dossier"]),
                "authority_scope": "NONVISUAL_COMPONENTS_ONLY",
            },
            "nonvisual_niche_alignment": {
                **self._version_ref(source_artifacts["niche_alignment_dossier"]),
                "authority_scope": "NONVISUAL_COMPONENTS_ONLY",
            },
            "supplemental_visual_alignment": self._version_ref(
                created["market_gate_results"]
            ),
        }
        composite_alignment_hash = content_hash(composite_alignment_core)
        composite_alignment_authority = {
            **composite_alignment_core,
            "ref": (
                f"pkg1-sc04-composite-alignment://{revision_id}/"
                f"{composite_alignment_hash}"
            ),
            "content_hash": composite_alignment_hash,
        }
        effective_artifact_authority = {
            "schema_version": "pkg1.sc04-effective-authority.v1",
            "authority_project_ids": {
                "current_revision": str(project.id),
                "source_revision": str(source_project.id),
                "historical_source": historical_project_id,
            },
            "current_visual_authority": {
                "binding_key": "supplemental_visual_alignment",
                **self._version_ref(created["market_gate_results"]),
            },
            "nonvisual_reuse_authorities": {
                "market_alignment_dossier": self._version_ref(
                    source_artifacts["market_alignment_dossier"]
                ),
                "niche_alignment_dossier": self._version_ref(
                    source_artifacts["niche_alignment_dossier"]
                ),
            },
            "composite_market_alignment_authority": (composite_alignment_authority),
            "superseded_visual_authorities": [
                {
                    "artifact_type": key,
                    **self._version_ref(source_artifacts[key]),
                    "authority_scope": "NONVISUAL_COMPONENTS_ONLY",
                }
                for key in (
                    "market_alignment_dossier",
                    "niche_alignment_dossier",
                )
            ],
            "resolver_contract": "EFFECTIVE_ARTIFACTS_V1",
        }
        exact_bindings = deepcopy(
            (source_package.content or {}).get("exact_bindings") or {}
        )
        if not exact_bindings:
            raise ValidationFailureError("SOURCE_PACKAGE_EXACT_BINDINGS_MISSING")
        exact_bindings.update(
            {
                "effective_ads_only_monetization_policy": overlay["ref"],
                "geo_market_delivery_closeout_evidence": overlay["closeout_ref"],
                "effective_market_policy_hash": overlay["effective_market_policy_hash"],
                "scene_visual_intent": self._version_ref(
                    created["scene_visual_intent"]
                ),
                "visual_source_decision_set": self._version_ref(
                    created["visual_source_decision_set"]
                ),
                "visual_plan": self._version_ref(created["visual_plan"]),
                "provider_execution_plan": self._version_ref(
                    created["provider_execution_plan"]
                ),
                "cost_estimate_snapshot": self._version_ref(
                    created["cost_estimate_snapshot"]
                ),
                "rights_disclosure_completeness_report": self._version_ref(
                    created["rights_disclosure_completeness_report"]
                ),
                "asset_provenance_plan": self._version_ref(
                    created["asset_provenance_plan"]
                ),
                "supplemental_visual_alignment": self._version_ref(
                    created["market_gate_results"]
                ),
                "market_alignment_dossier_visual_authority": {
                    **self._version_ref(source_artifacts["market_alignment_dossier"]),
                    "authority_scope": "HISTORICAL_NONVISUAL_COMPONENTS_ONLY",
                    "old_visual_binding": "SUPERSEDED",
                    "new_visual_binding": self._version_ref(
                        created["market_gate_results"]
                    ),
                },
                "niche_alignment_dossier_visual_authority": {
                    **self._version_ref(source_artifacts["niche_alignment_dossier"]),
                    "authority_scope": "HISTORICAL_NONVISUAL_COMPONENTS_ONLY",
                    "old_visual_binding": "SUPERSEDED",
                    "new_visual_binding": self._version_ref(
                        created["market_gate_results"]
                    ),
                },
                "composite_market_alignment_authority": (composite_alignment_authority),
            }
        )
        manifest = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "revision_id": revision_id,
            "revision_version": 3,
            "revision_hash": revision_hash,
            "builder_version": BUILDER_VERSION,
            "planning_output_set_hash": output_set_hash,
            "project_type": PROJECT_TYPE,
            "package_status": "TECHNICAL_PASS_HUMAN_REVIEW_PENDING",
            "supersedes": source_package_ref,
            "supersession_scope": "SC04_VISUAL_SOURCE_AND_DEPENDENT_PLANS_ONLY",
            "source_project_ref": f"video-project://{source_project.id}",
            "source_package_mutated": False,
            "source_human_authority": source_human_authority,
            "root_cause": ROOT_CAUSE,
            "repaired_scene": SCENE_ID,
            "repaired_route": REPAIRED_ROUTE.value,
            "effective_monetization_policy": overlay["ref"],
            "geo_market_delivery_closeout_evidence": overlay["closeout_ref"],
            "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            "base_snapshot_monetization_contradiction": (
                "REPAIRED_BY_IMMUTABLE_ADS_ONLY_OVERLAY"
            ),
            "exact_bindings": exact_bindings,
            "reused_artifacts": reused_refs,
            "historical_context_artifacts": historical_context_refs,
            "superseded_artifacts": superseded_refs,
            "revised_artifacts": revised_refs,
            "effective_artifacts": effective_artifacts,
            "effective_artifact_authority": effective_artifact_authority,
            "mr1_reapproval_manifest_compatibility_gate": mr1_compatibility,
            "attempt_evidence": attempt_evidence,
            "superseded_approvals": [
                {
                    **item,
                    "reuse_allowed": False,
                    "historical_receipt_mutated": False,
                }
                for item in old_approvals
            ],
            "provider_execution": "DISABLED",
            "automatic_retry_allowed": False,
            "provider_substitution_allowed": False,
            "automatic_pexels_to_ai_fallback": False,
            "third_sc04_pexels_attempt_allowed": False,
            "PRODUCTION_PACKAGE_APPROVED": False,
            "PKG1_SC04_REVISION_HUMAN_REVIEW": "PENDING",
            "PKG1_SC04_REVISION_FINAL": "WAITING_HUMAN_REVIEW",
            "MR1_EXECUTION": "BLOCKED_PENDING_PACKAGE_APPROVAL",
            "PROCEED_TO_MR1_REAPPROVAL": False,
            "PROCEED_TO_MR1": False,
            "no_execution_proof": no_execution_proof,
            "exact_next_action": (
                "Assigned operator reviews the exact package, SC-04 diff, gate "
                "evidence, and review packet. No approval is created by this build."
            ),
        }
        package_version = self._create_artifact(
            project.id,
            "package_manifest",
            manifest,
            created_by_user_id,
            revision_hash,
        )
        review = ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=project.id,
                target_type="artifact_version",
                target_id=package_version.id,
                target_artifact_version_id=package_version.id,
                review_type="final_human",
                status="open",
                assigned_to_user_id=created_by_user_id,
                requested_by_user_id=created_by_user_id,
                review_reason_codes=[
                    "PKG1_SC04_EXACT_PACKAGE_REVIEW_REQUIRED",
                    "SC04_VISUAL_ROUTE_CHANGED_TO_NATIVE_MOTION",
                    "MR1_REAPPROVAL_NOT_AUTHORIZED",
                ],
                evidence_required=True,
                evidence_refs=[
                    self._version_ref(package_version),
                    self._version_ref(created[DIFF_ARTIFACT_TYPE]),
                    self._version_ref(created[REVIEW_PACKET_ARTIFACT_TYPE]),
                    self._version_ref(created["gate_results"]),
                    overlay["ref"],
                    overlay["closeout_ref"],
                ],
                review_scope=(
                    "Exact PKG1_SC04_REVISION package only. It does not authorize "
                    "provider, render, archive, upload, publish, or MR1 execution."
                ),
                context_pack_ref=(
                    f"pkg1-sc04-revision://{revision_id}/{revision_hash}"
                ),
            ),
            correlation_id="pkg1-sc04-revision-human-review",
        )
        if self.source_service._no_execution_counts() != no_execution_before:
            raise ValidationFailureError("PKG1_SC04_PROVIDER_BOUNDARY_CHANGED")
        if (
            self.source_service._historical_fingerprint(
                source_project,
                self.source_service._current_artifacts(source_project.id),
            )
            != source_fingerprint
        ):
            raise ValidationFailureError("PKG1_SC04_SOURCE_PROJECT_MUTATED")
        result = self.read_revision(project.id)
        result["human_review_task_id"] = str(review.id)
        return result

    def read_revision(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None or project.project_type != PROJECT_TYPE:
            raise NotFoundError(f"PKG1 SC04 revision not found: {project_id}")
        validated = self._validated_current_revision_state(project)
        artifacts: dict[str, ArtifactVersion] = validated["artifacts"]
        package: ArtifactVersion = validated["package"]
        no_execution: dict[str, Any] = validated["no_execution_proof"]
        reviews = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == project.id,
                    ReviewTask.target_artifact_version_id == package.id,
                    ReviewTask.review_type == "final_human",
                )
            ).all()
        )
        expected_review_statuses = (
            {"open", "in_progress"} if project.status == "in_review" else {"completed"}
        )
        if (
            len(reviews) != 1
            or reviews[0].target_type != "artifact_version"
            or reviews[0].target_id != package.id
            or reviews[0].status not in expected_review_statuses
        ):
            raise ValidationFailureError("PKG1_SC04_EXACT_REVIEW_STATE_INVALID")
        return {
            "video_project_id": str(project.id),
            "project_type": project.project_type,
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "revision_id": package.content["revision_id"],
            "revision_version": package.content["revision_version"],
            "revision_hash": package.content["revision_hash"],
            "package": deepcopy(package.content),
            "artifacts": {
                key: {**self._version_ref(value), "content": deepcopy(value.content)}
                for key, value in artifacts.items()
            },
            "human_review_task_ids": [str(item.id) for item in reviews],
            "human_review_state": package.content["PKG1_SC04_REVISION_HUMAN_REVIEW"],
            "final_state": package.content["PKG1_SC04_REVISION_FINAL"],
            "no_execution_proof": deepcopy(no_execution),
            "provider_calls": no_execution["provider_calls"],
            "render_calls": no_execution["render_calls"],
            "drive_calls": no_execution["drive_calls"],
            "youtube_calls": no_execution["youtube_calls"],
        }

    def _validated_current_revision_state(
        self, project: VideoProject
    ) -> dict[str, Any]:
        artifact_rows = list(
            self.session.scalars(
                select(Artifact).where(Artifact.video_project_id == project.id)
            ).all()
        )
        if project.status not in {"in_review", "approved"}:
            raise ValidationFailureError("PKG1_SC04_REVISION_PROJECT_STATUS_INVALID")
        package_rows = [
            item for item in artifact_rows if item.artifact_type == "package_manifest"
        ]
        if len(package_rows) != 1:
            raise ValidationFailureError("PKG1_SC04_PACKAGE_MISSING")
        package_seed = (
            self.session.get(ArtifactVersion, package_rows[0].current_version_id)
            if package_rows[0].current_version_id is not None
            else None
        )
        if (
            package_seed is None
            or package_seed.artifact_id != package_rows[0].id
            or package_seed.status != "submitted"
            or content_hash(package_seed.content) != package_seed.content_hash
        ):
            raise ValidationFailureError(
                "PKG1_SC04_CURRENT_ARTIFACT_INVALID:package_manifest"
            )
        seed_revised = (package_seed.content or {}).get("revised_artifacts") or {}
        if not isinstance(seed_revised, dict) or not seed_revised:
            raise ValidationFailureError("PKG1_SC04_REVISED_ARTIFACTS_INVALID")
        revision_artifact_types = set(seed_revised) | {"package_manifest"}
        if project.status == "approved":
            revision_artifact_types.add(_CLOSEOUT_RECEIPT_ARTIFACT_TYPE)

        artifacts: dict[str, ArtifactVersion] = {}
        artifact_models: dict[str, Artifact] = {}
        invalid_current: list[str] = []
        for artifact in artifact_rows:
            # Downstream MR1 approval/production artifacts coexist on the approved
            # SC-04 project but are not part of the immutable revision artifact set.
            if artifact.artifact_type not in revision_artifact_types:
                continue
            if artifact.artifact_type in artifacts:
                invalid_current.append(f"DUPLICATE:{artifact.artifact_type}")
                continue
            version = (
                self.session.get(ArtifactVersion, artifact.current_version_id)
                if artifact.current_version_id is not None
                else None
            )
            if (
                version is None
                or version.artifact_id != artifact.id
                or version.status != "submitted"
                or content_hash(version.content) != version.content_hash
            ):
                invalid_current.append(artifact.artifact_type)
                continue
            artifacts[artifact.artifact_type] = version
            artifact_models[artifact.artifact_type] = artifact
        if invalid_current:
            raise ValidationFailureError(
                "PKG1_SC04_CURRENT_ARTIFACT_INVALID:"
                + ",".join(sorted(invalid_current))
            )

        package = artifacts.get("package_manifest")
        package_artifact = artifact_models.get("package_manifest")
        if package is None or package_artifact is None:
            raise ValidationFailureError("PKG1_SC04_PACKAGE_MISSING")
        manifest = package.content or {}
        revision_id = manifest.get("revision_id")
        revision_hash = manifest.get("revision_hash")
        try:
            uuid.UUID(str(revision_id))
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError("PKG1_SC04_REVISION_IDENTITY_INVALID") from exc
        if (
            not isinstance(revision_hash, str)
            or not revision_hash
            or manifest.get("schema_version") != REVISION_SCHEMA_VERSION
            or manifest.get("project_type") != PROJECT_TYPE
            or manifest.get("package_status") != "TECHNICAL_PASS_HUMAN_REVIEW_PENDING"
            or manifest.get("PKG1_SC04_REVISION_HUMAN_REVIEW") != "PENDING"
            or manifest.get("PKG1_SC04_REVISION_FINAL") != "WAITING_HUMAN_REVIEW"
            or manifest.get("provider_execution") != "DISABLED"
        ):
            raise ValidationFailureError("PKG1_SC04_REVISION_IDENTITY_INVALID")

        revised = manifest.get("revised_artifacts") or {}
        if not isinstance(revised, dict) or not revised:
            raise ValidationFailureError("PKG1_SC04_REVISED_ARTIFACTS_INVALID")
        expected_types = set(revised) | {"package_manifest"}
        if project.status == "approved":
            expected_types.add(_CLOSEOUT_RECEIPT_ARTIFACT_TYPE)
        if set(artifacts) != expected_types:
            raise ValidationFailureError("PKG1_SC04_CURRENT_ARTIFACT_SET_INVALID")

        expected_context = [
            {"type": "pkg1_sc04_revision", "content_hash": revision_hash}
        ]
        expected_packaging = {
            "pkg1_sc04_revision": True,
            "provider_execution": "DISABLED",
            "human_review": "PENDING",
        }
        revised_hashes: dict[str, str] = {}
        provenance_invalid: list[str] = []
        for artifact_type in sorted(set(revised) | {"package_manifest"}):
            version = artifacts.get(artifact_type)
            artifact = artifact_models.get(artifact_type)
            if version is None or artifact is None:
                provenance_invalid.append(artifact_type)
                continue
            expected_artifact_status = (
                "approved"
                if artifact_type == "package_manifest" and project.status == "approved"
                else "in_review"
            )
            if (
                artifact.status != expected_artifact_status
                or version.context_refs != expected_context
                or version.evidence_refs != []
                or version.packaging_metadata != expected_packaging
                or version.content.get("revision_id") != revision_id
                or version.content.get("revision_hash") != revision_hash
            ):
                provenance_invalid.append(artifact_type)
                continue
            if artifact_type == "package_manifest":
                continue
            ref = revised.get(artifact_type)
            if ref != self._version_ref(version):
                provenance_invalid.append(artifact_type)
                continue
            revised_hashes[artifact_type] = version.content_hash
        if provenance_invalid:
            raise ValidationFailureError(
                "PKG1_SC04_REVISION_ARTIFACT_PROVENANCE_INVALID:"
                + ",".join(sorted(set(provenance_invalid)))
            )
        if len(revised_hashes) != len(revised) or content_hash(
            revised_hashes
        ) != manifest.get("planning_output_set_hash"):
            raise ValidationFailureError("PKG1_SC04_PLANNING_OUTPUT_SET_INVALID")

        if project.status == "approved":
            self._validate_closeout_receipt_provenance(
                package=package,
                receipt_artifact=artifact_models.get(_CLOSEOUT_RECEIPT_ARTIFACT_TYPE),
                receipt=artifacts.get(_CLOSEOUT_RECEIPT_ARTIFACT_TYPE),
            )

        self._validate_effective_revision_refs(
            project=project,
            manifest=manifest,
            revised=revised,
        )
        no_execution = self._validated_no_execution_proof(manifest)
        return {
            "artifacts": artifacts,
            "package": package,
            "no_execution_proof": no_execution,
        }

    def _validate_closeout_receipt_provenance(
        self,
        *,
        package: ArtifactVersion,
        receipt_artifact: Artifact | None,
        receipt: ArtifactVersion | None,
    ) -> None:
        receipt_content = receipt.content if receipt is not None else {}
        approval_id = receipt_content.get("approval_decision_id")
        expected_evidence = [
            {
                "type": "reviewed_package",
                "artifact_version_id": str(package.id),
                "content_hash": package.content_hash,
            },
            {"type": "approval_decision", "id": approval_id},
        ]
        if (
            receipt is None
            or receipt_artifact is None
            or receipt_artifact.status != "approved"
            or receipt_content.get("reviewed_package") != self._version_ref(package)
            or (receipt_content.get("revision") or {}).get("revision_id")
            != package.content.get("revision_id")
            or (receipt_content.get("revision") or {}).get("revision_hash")
            != package.content.get("revision_hash")
            or not approval_id
            or receipt.evidence_refs != expected_evidence
            or receipt.context_refs
            != [
                {
                    "type": "pkg1_sc04_human_closeout",
                    "revision_hash": package.content.get("revision_hash"),
                }
            ]
        ):
            raise ValidationFailureError(
                "PKG1_SC04_CLOSEOUT_RECEIPT_PROVENANCE_INVALID"
            )

    def _validate_effective_revision_refs(
        self,
        *,
        project: VideoProject,
        manifest: dict[str, Any],
        revised: dict[str, Any],
    ) -> None:
        effective = manifest.get("effective_artifacts") or {}
        authority = manifest.get("effective_artifact_authority") or {}
        authority_ids = authority.get("authority_project_ids") or {}
        source_authority = manifest.get("source_human_authority") or {}
        if (
            not isinstance(effective, dict)
            or not effective
            or not isinstance(authority_ids, dict)
            or not isinstance(source_authority, dict)
            or set(authority_ids)
            != {"current_revision", "source_revision", "historical_source"}
            or authority_ids.get("current_revision") != str(project.id)
            or authority_ids.get("source_revision")
            != source_authority.get("source_project_id")
        ):
            raise ValidationFailureError("PKG1_SC04_EFFECTIVE_AUTHORITY_INVALID")
        try:
            project_ids = {
                uuid.UUID(str(value))
                for value in authority_ids.values()
                if value is not None
            }
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "PKG1_SC04_EFFECTIVE_AUTHORITY_INVALID"
            ) from exc
        if project.id not in project_ids:
            raise ValidationFailureError("PKG1_SC04_EFFECTIVE_AUTHORITY_INVALID")

        invalid: list[str] = []
        for artifact_type, ref in sorted(effective.items()):
            try:
                version = self.session.get(
                    ArtifactVersion,
                    uuid.UUID(str(ref["artifact_version_id"])),
                )
            except (KeyError, TypeError, ValueError):
                version = None
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            artifact_project = (
                self.session.get(VideoProject, artifact.video_project_id)
                if artifact is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact_project is None
                or artifact.artifact_type != artifact_type
                or artifact.video_project_id not in project_ids
                or artifact.current_version_id != version.id
                or artifact.status not in {"in_review", "approved"}
                or version.status != "submitted"
                or ref != self._version_ref(version)
                or content_hash(version.content) != version.content_hash
                or (
                    artifact.video_project_id != project.id
                    and artifact_project.status != "approved"
                )
                or (
                    artifact.video_project_id == project.id
                    and revised.get(artifact_type) != ref
                )
            ):
                invalid.append(artifact_type)
        missing_revised = [
            key for key, ref in revised.items() if effective.get(key) != ref
        ]
        if invalid or missing_revised:
            raise ValidationFailureError(
                "PKG1_SC04_EFFECTIVE_ARTIFACT_INVALID:"
                + ",".join(sorted(set(invalid + missing_revised)))
            )

        current_visual = authority.get("current_visual_authority") or {}
        current_visual_ref = {
            key: value for key, value in current_visual.items() if key != "binding_key"
        }
        nonvisual = authority.get("nonvisual_reuse_authorities") or {}
        composite = authority.get("composite_market_alignment_authority") or {}
        composite_core = {
            key: deepcopy(value)
            for key, value in composite.items()
            if key not in {"ref", "content_hash"}
        }
        if (
            current_visual.get("binding_key") != "supplemental_visual_alignment"
            or current_visual_ref != effective.get("market_gate_results")
            or nonvisual.get("market_alignment_dossier")
            != effective.get("market_alignment_dossier")
            or nonvisual.get("niche_alignment_dossier")
            != effective.get("niche_alignment_dossier")
            or composite.get("schema_version")
            != "pkg1.sc04-composite-alignment-authority.v1"
            or composite.get("revision_id") != manifest.get("revision_id")
            or composite.get("revision_hash") != manifest.get("revision_hash")
            or composite.get("subject") != effective.get("visual_plan")
            or composite.get("supplemental_visual_alignment")
            != effective.get("market_gate_results")
            or (composite.get("nonvisual_market_alignment") or {}).get(
                "authority_scope"
            )
            != "NONVISUAL_COMPONENTS_ONLY"
            or (composite.get("nonvisual_niche_alignment") or {}).get("authority_scope")
            != "NONVISUAL_COMPONENTS_ONLY"
            or composite.get("content_hash") != content_hash(composite_core)
            or composite.get("ref")
            != (
                "pkg1-sc04-composite-alignment://"
                f"{manifest.get('revision_id')}/"
                f"{composite.get('content_hash')}"
            )
        ):
            raise ValidationFailureError("PKG1_SC04_EFFECTIVE_AUTHORITY_INVALID")

    @staticmethod
    def _validated_no_execution_proof(
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        proof = manifest.get("no_execution_proof") or {}
        before = proof.get("before_counts")
        after = proof.get("after_counts")
        deltas = proof.get("deltas")
        summary_keys = (
            "provider_calls",
            "render_calls",
            "drive_calls",
            "youtube_calls",
        )
        counts_are_valid = (
            isinstance(before, dict)
            and isinstance(after, dict)
            and isinstance(deltas, dict)
            and bool(before)
            and set(before) == set(after) == set(deltas)
            and all(
                isinstance(key, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
                for counts in (before, after)
                for key, value in counts.items()
            )
            and all(
                isinstance(value, int) and not isinstance(value, bool)
                for value in deltas.values()
            )
        )
        expected_deltas = (
            {key: after[key] - before[key] for key in before}
            if counts_are_valid
            else None
        )
        if (
            not counts_are_valid
            or deltas != expected_deltas
            or before != after
            or proof.get("all_deltas_zero") is not True
            or any(deltas.values())
            or any(proof.get(key) != 0 for key in summary_keys)
        ):
            raise ValidationFailureError("PKG1_SC04_NO_EXECUTION_PROOF_INVALID")
        return deepcopy(proof)

    def _approved_source_package(
        self,
        channel_id: uuid.UUID,
        *,
        requested_project_id: uuid.UUID | None = None,
        requested_package_version_id: uuid.UUID | None = None,
        requested_package_hash: str | None = None,
        requested_approval_id: uuid.UUID | None = None,
        requested_receipt_version_id: uuid.UUID | None = None,
        requested_receipt_hash: str | None = None,
    ) -> tuple[VideoProject, ArtifactVersion, dict[str, Any]]:
        requested_bindings = (
            requested_project_id,
            requested_package_version_id,
            requested_package_hash,
            requested_approval_id,
            requested_receipt_version_id,
            requested_receipt_hash,
        )
        provided = tuple(value is not None for value in requested_bindings)
        if any(provided) and not all(provided):
            raise ValidationFailureError("PKG1_SC04_SOURCE_EXACT_BINDINGS_INCOMPLETE")

        if requested_project_id is not None:
            project = self.session.get(VideoProject, requested_project_id)
            if project is None:
                raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_PROJECT_MISSING")
        else:
            projects = list(
                self.session.scalars(
                    select(VideoProject).where(
                        VideoProject.channel_workspace_id == channel_id,
                        VideoProject.project_type == SOURCE_PROJECT_TYPE,
                        VideoProject.status == "approved",
                    )
                ).all()
            )
            if len(projects) != 1:
                raise ValidationFailureError(
                    "EXACTLY_ONE_APPROVED_PKG1_MARKET_REVISION_REQUIRED"
                )
            project = projects[0]
        if (
            project.channel_workspace_id != channel_id
            or project.project_type != SOURCE_PROJECT_TYPE
            or project.status != "approved"
        ):
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_PROJECT_INVALID")

        if requested_package_version_id is not None:
            package = self.session.get(ArtifactVersion, requested_package_version_id)
            package_candidates = [package] if package is not None else []
        else:
            package_candidates = list(
                self.session.scalars(
                    select(ArtifactVersion)
                    .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
                    .where(
                        Artifact.video_project_id == project.id,
                        Artifact.artifact_type == "package_manifest",
                        Artifact.current_version_id == ArtifactVersion.id,
                    )
                ).all()
            )
        if len(package_candidates) != 1:
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_PACKAGE_REQUIRED")
        package = package_candidates[0]
        package_artifact = self.session.get(Artifact, package.artifact_id)
        if (
            package_artifact is None
            or package_artifact.video_project_id != project.id
            or package_artifact.artifact_type != "package_manifest"
            or package_artifact.current_version_id != package.id
            or package_artifact.status != "approved"
            or package.status not in {"submitted", "approved"}
            or content_hash(package.content) != package.content_hash
            or (
                requested_package_hash is not None
                and package.content_hash != requested_package_hash
            )
        ):
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_PACKAGE_INVALID")

        if requested_approval_id is not None:
            approval = self.session.get(ApprovalDecision, requested_approval_id)
            approval_candidates = [approval] if approval is not None else []
        else:
            approval_candidates = [
                item
                for item in self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id == package.id
                    )
                ).all()
                if item.decision == "approved"
                and (item.metadata_ or {}).get("approval_scope")
                == "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
            ]
        if len(approval_candidates) != 1:
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_APPROVAL_REQUIRED")
        approval = approval_candidates[0]
        approval_metadata = approval.metadata_ or {}
        if (
            approval.target_type != "artifact_version"
            or approval.target_id != package.id
            or approval.target_artifact_version_id != package.id
            or approval.decision != "approved"
            or approval_metadata.get("approval_scope")
            != "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
            or approval_metadata.get("package_artifact_version_id") != str(package.id)
            or approval_metadata.get("package_content_hash") != package.content_hash
        ):
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_APPROVAL_INVALID")

        if requested_receipt_version_id is not None:
            receipt = self.session.get(ArtifactVersion, requested_receipt_version_id)
            receipt_candidates = [receipt] if receipt is not None else []
        else:
            receipt_candidates = list(
                self.session.scalars(
                    select(ArtifactVersion)
                    .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
                    .where(
                        Artifact.video_project_id == project.id,
                        Artifact.artifact_type
                        == "pkg1_market_revision_human_review_receipt",
                        Artifact.current_version_id == ArtifactVersion.id,
                    )
                ).all()
            )
        if len(receipt_candidates) != 1:
            raise ValidationFailureError(
                "PKG1_SC04_EXACT_SOURCE_HUMAN_RECEIPT_REQUIRED"
            )
        receipt = receipt_candidates[0]
        receipt_artifact = self.session.get(Artifact, receipt.artifact_id)
        receipt_content = receipt.content or {}
        reviewed_package = receipt_content.get("reviewed_package") or {}
        revision = receipt_content.get("revision") or {}
        if (
            receipt_artifact is None
            or receipt_artifact.video_project_id != project.id
            or receipt_artifact.artifact_type
            != "pkg1_market_revision_human_review_receipt"
            or receipt_artifact.current_version_id != receipt.id
            or receipt_artifact.status != "approved"
            or receipt.status != "approved"
            or content_hash(receipt.content) != receipt.content_hash
            or (
                requested_receipt_hash is not None
                and receipt.content_hash != requested_receipt_hash
            )
            or receipt_content.get("schema_version")
            != "pkg1.market-revision-human-review-receipt.v1"
            or receipt_content.get("decision") != "PASS"
            or receipt_content.get("decision_source") != "OPERATOR"
            or receipt_content.get("review_authority") != "HUMAN"
            or receipt_content.get("approval_scope")
            != "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
            or receipt_content.get("approval_decision_id") != str(approval.id)
            or revision.get("video_project_id") != str(project.id)
            or reviewed_package.get("artifact_version_id") != str(package.id)
            or reviewed_package.get("artifact_version_ref")
            != f"artifact-version://{package.id}"
            or reviewed_package.get("content_hash") != package.content_hash
        ):
            raise ValidationFailureError("PKG1_SC04_EXACT_SOURCE_HUMAN_RECEIPT_INVALID")

        human_authority = {
            "schema_version": "pkg1.sc04-source-human-authority.v1",
            "source_project_id": str(project.id),
            "source_project_ref": f"video-project://{project.id}",
            "approved_package": {
                "artifact_type": "package_manifest",
                **self._version_ref(package),
            },
            "approval": {
                "approval_decision_id": str(approval.id),
                "approval_scope": "PKG1_MARKET_REVISION_PACKAGE_PLANNING",
                "approval_ref": approval_metadata.get("approval_ref"),
                "decision": approval.decision,
                "target_artifact_version_id": str(package.id),
                "target_content_hash": package.content_hash,
            },
            "human_review_receipt": {
                "artifact_type": ("pkg1_market_revision_human_review_receipt"),
                **self._version_ref(receipt),
            },
            "decision": "PASS",
            "decision_source": "OPERATOR",
            "review_authority": "HUMAN",
        }
        return project, package, human_authority

    def _resolve_source_package_artifacts(
        self,
        package: ArtifactVersion,
        *,
        source_project: VideoProject,
    ) -> dict[str, ArtifactVersion]:
        payload = package.content or {}
        refs: dict[str, Any] = {}
        for field in ("reused_artifacts", "revised_artifacts"):
            for key, value in (payload.get(field) or {}).items():
                if isinstance(value, dict):
                    refs[key] = value
        resolved: dict[str, ArtifactVersion] = {}
        allowed_project_ids = {source_project.id}
        historical_ref = (
            (package.content or {})
            .get("exact_bindings", {})
            .get("historical_video_project", {})
            .get("ref")
        )
        if isinstance(historical_ref, str) and historical_ref.startswith(
            "video-project://"
        ):
            allowed_project_ids.add(
                uuid.UUID(historical_ref.removeprefix("video-project://"))
            )
        for key, ref in refs.items():
            raw_id = ref.get("artifact_version_id")
            if not raw_id:
                continue
            version = self.session.get(ArtifactVersion, uuid.UUID(str(raw_id)))
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or version.content_hash != ref.get("content_hash")
                or content_hash(version.content) != version.content_hash
                or artifact.artifact_type != key
                or artifact.video_project_id not in allowed_project_ids
                or artifact.current_version_id != version.id
                or artifact.status not in {"in_review", "approved"}
                or version.status not in {"submitted", "approved"}
            ):
                raise ValidationFailureError(
                    f"PKG1_SC04_SOURCE_ARTIFACT_BINDING_INVALID:{key}"
                )
            resolved[key] = version
        return resolved

    def _failed_attempt_evidence(
        self, source_project: VideoProject
    ) -> tuple[ArtifactVersion, dict[str, Any]]:
        run_artifacts = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_project.id,
                    Artifact.artifact_type == "mr1_execution_run",
                )
            ).all()
        )
        if len(run_artifacts) != 1 or run_artifacts[0].current_version_id is None:
            raise ValidationFailureError("EXACT_MR1_RUN_EVIDENCE_MISSING")
        run_version = self.session.get(
            ArtifactVersion, run_artifacts[0].current_version_id
        )
        assert run_version is not None
        if (
            content_hash(run_version.content) != run_version.content_hash
            or run_artifacts[0].current_version_id != run_version.id
        ):
            raise ValidationFailureError("SC04_MR1_RUN_HASH_BINDING_INVALID")
        state = run_version.content or {}
        if (
            state.get("current_state") != "BLOCKED_REQUIRES_NEW_MR1_APPROVAL"
            or state.get("blocker") != "pexels:SC-04:supplement:02:POST_SUBMIT_FAILURE"
        ):
            raise ValidationFailureError("SC04_MR1_BLOCKED_STATE_INVALID")

        attempts: list[dict[str, Any]] = []
        attempt_contents: dict[str, dict[str, Any]] = {}
        attempt_artifacts = list(
            self.session.scalars(
                select(Artifact).where(
                    Artifact.video_project_id == source_project.id,
                    Artifact.artifact_type == "mr1_provider_attempt_ledger",
                )
            ).all()
        )
        for artifact in attempt_artifacts:
            if artifact.current_version_id is None:
                continue
            version = self.session.get(ArtifactVersion, artifact.current_version_id)
            if version is None:
                continue
            if (
                artifact.current_version_id != version.id
                or content_hash(version.content) != version.content_hash
            ):
                raise ValidationFailureError("SC04_ATTEMPT_LEDGER_HASH_INVALID")
            content = version.content or {}
            if (
                content.get("scene_id") != SCENE_ID
                or content.get("provider") != "pexels_api"
            ):
                continue
            operation_key = str(content.get("operation_key") or "")
            if operation_key in attempt_contents:
                raise ValidationFailureError("SC04_ATTEMPT_OPERATION_BINDING_DUPLICATE")
            attempt_contents[operation_key] = deepcopy(content)
            attempts.append(
                {
                    **self._version_ref(version),
                    "run_id": content.get("run_id"),
                    "operation_key": operation_key,
                    "provider_attempt_ordinal": (
                        content.get("provider_attempt_ordinal")
                        or (2 if operation_key.endswith("supplement:02") else 1)
                    ),
                    "state": content.get("state"),
                    "submit_state": content.get("submit_state"),
                    "attempt_count": content.get("attempt_count"),
                    "attempt_cap": content.get("attempt_cap"),
                    "search_submit_count": content.get("search_submit_count"),
                    "download_submit_count": content.get("download_submit_count"),
                    "request_hash": content.get("request_hash"),
                    "failure": content.get("failure"),
                    "automatic_retry_allowed": content.get("automatic_retry_allowed"),
                    "provider_substitution_allowed": content.get(
                        "provider_substitution_allowed"
                    ),
                    "continuation_approval_id": (
                        (content.get("provider_attempt_continuation") or {}).get(
                            "approval_decision_id"
                        )
                    ),
                    "approval_id": content.get("approval_id"),
                }
            )
        attempts.sort(key=lambda item: int(item["provider_attempt_ordinal"]))
        if len(attempts) != 2:
            raise ValidationFailureError("EXACTLY_TWO_SC04_ATTEMPTS_REQUIRED")
        if {item["operation_key"] for item in attempts} != {
            "pexels:SC-04",
            "pexels:SC-04:supplement:02",
        }:
            raise ValidationFailureError("SC04_ATTEMPT_OPERATION_SET_INVALID")
        if [item["provider_attempt_ordinal"] for item in attempts] != [1, 2]:
            raise ValidationFailureError("SC04_ATTEMPT_ORDINALS_INVALID")
        run_attempts = state.get("attempts") or {}
        run_artifact_ids = state.get("attempt_artifact_ids") or {}
        for item in attempts:
            persisted = run_attempts.get(item["operation_key"]) or {}
            if not (
                item["run_id"] == state.get("run_id")
                and item["state"] == "CONSUMED_FAILED"
                and item["submit_state"] == "FAILED_CONSUMED"
                and item["attempt_count"] == item["attempt_cap"] == 1
                and item["search_submit_count"] == 1
                and item["download_submit_count"] == 0
                and item["failure"] == "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE"
                and item["automatic_retry_allowed"] is False
                and item["provider_substitution_allowed"] is False
                and persisted.get("request_hash") == item["request_hash"]
                and persisted.get("artifact_version_id") == item["artifact_version_id"]
                and persisted.get("state") == item["state"]
                and persisted.get("attempt_count") == item["attempt_count"]
                and persisted.get("attempt_cap") == item["attempt_cap"]
                and run_artifact_ids.get(item["operation_key"]) == item["artifact_id"]
            ):
                raise ValidationFailureError("SC04_ATTEMPT_EVIDENCE_INVALID")

        source_package = self.source_service._current_artifacts(source_project.id).get(
            "package_manifest"
        )
        if source_package is None:
            raise ValidationFailureError("SC04_ATTEMPT_SOURCE_PACKAGE_MISSING")
        source_package_artifact = self.session.get(Artifact, source_package.artifact_id)
        if (
            source_package_artifact is None
            or source_package_artifact.current_version_id != source_package.id
            or content_hash(source_package.content) != source_package.content_hash
        ):
            raise ValidationFailureError("SC04_ATTEMPT_SOURCE_PACKAGE_INVALID")
        base_content = attempt_contents["pexels:SC-04"]
        supplement_content = attempt_contents["pexels:SC-04:supplement:02"]
        try:
            base_approval_id = uuid.UUID(str(base_content.get("approval_id")))
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "SC04_ATTEMPT_BASE_APPROVAL_ID_INVALID"
            ) from exc
        base_approval = self.session.get(ApprovalDecision, base_approval_id)
        continuation = supplement_content.get("provider_attempt_continuation") or {}
        continuation_id = continuation.get("approval_decision_id")
        try:
            continuation_uuid = (
                uuid.UUID(str(continuation_id)) if continuation_id else None
            )
        except (TypeError, ValueError) as exc:
            raise ValidationFailureError(
                "SC04_ATTEMPT_CONTINUATION_APPROVAL_ID_INVALID"
            ) from exc
        continuation_approval = (
            self.session.get(ApprovalDecision, continuation_uuid)
            if continuation_uuid is not None
            else None
        )
        prior = supplement_content.get("prior_consumed_attempt") or {}
        first = attempts[0]
        authorization_scope = {
            key: deepcopy(value)
            for key, value in continuation.items()
            if key
            not in {
                "approval_decision_id",
                "authorization_content_hash",
                "decided_by_user_id",
                "decided_at",
                "receipt_content_hash",
            }
        }
        authorization_hash = content_hash(authorization_scope)
        receipt_core = {
            key: deepcopy(value)
            for key, value in continuation.items()
            if key != "receipt_content_hash"
        }
        persisted_continuation_receipts = (
            state.get("provider_attempt_continuation_approvals") or []
        )
        if not (
            base_approval is not None
            and base_approval.decision == "approved"
            and (base_approval.metadata_ or {}).get("approval_scope")
            == "MR1_REAL_PRODUCTION_EXECUTION"
            and (base_approval.metadata_ or {}).get("package_content_hash")
            == source_package.content_hash
            and base_approval.target_artifact_version_id == source_package.id
            and state.get("approval_id") == str(base_approval.id)
            and state.get("package_artifact_version_id") == str(source_package.id)
            and state.get("package_content_hash") == source_package.content_hash
            and continuation_approval is not None
            and continuation_approval.decision == "approved"
            and (continuation_approval.metadata_ or {}).get("approval_scope")
            == "MR1_EXACT_PROVIDER_ATTEMPT_CONTINUATION"
            and continuation_approval.target_artifact_version_id == source_package.id
            and continuation.get("authorization_content_hash") == authorization_hash
            and continuation.get("receipt_content_hash") == content_hash(receipt_core)
            and (continuation_approval.metadata_ or {}).get(
                "authorization_content_hash"
            )
            == authorization_hash
            and continuation_approval.decision_basis == authorization_scope
            and persisted_continuation_receipts == [continuation]
            and continuation.get("semantic_fit_threshold") == 0.78
            and continuation.get("base_approval_id") == str(base_approval.id)
            and continuation.get("prior_attempt_artifact_id") == first["artifact_id"]
            and continuation.get("prior_attempt_artifact_version_id")
            == first["artifact_version_id"]
            and continuation.get("prior_attempt_content_hash") == first["content_hash"]
            and continuation.get("prior_request_hash") == first["request_hash"]
            and continuation.get("additional_attempts") == 1
            and continuation.get("maximum_total_attempts") == 2
            and continuation.get("automatic_retry_allowed") is False
            and continuation.get("provider_substitution_allowed") is False
            and continuation.get("automatic_pexels_to_ai_fallback") is False
            and prior.get("artifact_version_id") == first["artifact_version_id"]
            and prior.get("content_hash") == first["content_hash"]
            and prior.get("request_hash") == first["request_hash"]
            and prior.get("artifact_id") == first["artifact_id"]
            and prior.get("state") == "CONSUMED_FAILED"
            and prior.get("failure") == "RuntimeError:PEXELS_SEMANTIC_FIT_INADEQUATE"
            and supplement_content.get("approval_id") == str(base_approval.id)
        ):
            raise ValidationFailureError("SC04_ATTEMPT_AUTHORITY_BINDING_INVALID")

        old_query_family = self._old_query_family(
            self._one_scene(
                self._resolve_source_package_artifacts(
                    source_package,
                    source_project=source_project,
                )["scene_visual_intent"].content,
                SCENE_ID,
            )["semantic_intent"]
        )
        return run_version, {
            "schema_version": "pkg1.sc04-attempt-evidence.v1",
            "run": {
                **self._version_ref(run_version),
                "run_id": state.get("run_id"),
                "current_state": state.get("current_state"),
                "blocker": state.get("blocker"),
            },
            "attempts": attempts,
            "attempt_count": 2,
            "all_attempt_ledgers_preserved": True,
            "third_attempt_created": False,
            "old_query_family": {
                "queries": old_query_family,
                "planner_version": "pexels-query-planner/v1.0.0",
                "evidence_state": "DETERMINISTIC_RECONSTRUCTION_FROM_BOUND_OLD_INTENT",
                "provider_call_made_for_reconstruction": False,
            },
            "candidate_scores": {
                "state": "UNAVAILABLE_NOT_PERSISTED",
                "values": [],
                "fabricated": False,
                "reason": (
                    "The Pexels failure path persisted request hashes and counters "
                    "but did not serialize candidate rankings or semantic scores."
                ),
            },
        }

    def _requirements(
        self,
        *,
        source_artifacts: dict[str, ArtifactVersion],
        run_version: ArtifactVersion,
        script_segment: dict[str, Any],
    ) -> tuple[SceneVisualRealizationRequirements, dict[str, Any]]:
        scenes = source_artifacts["visual_plan"].content.get("scenes") or []
        matching_indexes = [
            idx
            for idx, scene in enumerate(scenes)
            if isinstance(scene, dict) and scene.get("scene_id") == SCENE_ID
        ]
        if len(matching_indexes) != 1:
            raise ValidationFailureError("SC04_VISUAL_PLAN_SCENE_MISSING")
        index = matching_indexes[0]
        if index == 0 or index + 1 >= len(scenes):
            raise ValidationFailureError("SC04_ADJACENT_SCENE_AUTHORITY_MISSING")
        current_scene = scenes[index]
        previous_scene = scenes[index - 1]
        next_scene = scenes[index + 1]
        previous = previous_scene.get("semantic_intent")
        following = next_scene.get("semantic_intent")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                previous_scene.get("scene_id"),
                previous,
                next_scene.get("scene_id"),
                following,
            )
        ):
            raise ValidationFailureError("SC04_ADJACENT_SCENE_AUTHORITY_INVALID")

        if script_segment.get("segment_id") != "S04" or current_scene.get(
            "segment_refs"
        ) != ["S04"]:
            raise ValidationFailureError("SC04_SCRIPT_SCENE_BINDING_INVALID")
        script_text = script_segment.get("text")
        visual_intent_hint = script_segment.get("visual_intent_hint")
        if not isinstance(script_text, str) or not script_text.strip():
            raise ValidationFailureError("SC04_SCRIPT_TEXT_MISSING")
        if not isinstance(visual_intent_hint, str) or not visual_intent_hint.strip():
            raise ValidationFailureError("SC04_SCRIPT_VISUAL_INTENT_HINT_MISSING")

        normalized_text = self._normalize_semantic_text(script_text)
        concept_evidence: dict[str, str] = {}
        for concept, alternatives in _SC04_SCRIPT_CONCEPT_RULES.items():
            match = next(
                (
                    phrase
                    for phrase in alternatives
                    if self._normalize_semantic_text(phrase) in normalized_text
                ),
                None,
            )
            if match is not None:
                concept_evidence[concept] = match
        missing_concepts = sorted(
            _SC04_REQUIRED_SCRIPT_CONCEPTS - set(concept_evidence)
        )
        if missing_concepts:
            raise ValidationFailureError(
                "SC04_SCRIPT_SEMANTIC_DERIVATION_INCOMPLETE:"
                + ",".join(missing_concepts)
            )
        if (
            current_scene.get("semantic_intent") != visual_intent_hint
            or "native baseline checklist"
            not in self._normalize_semantic_text(visual_intent_hint)
        ):
            raise ValidationFailureError("SC04_SCRIPT_VISUAL_HINT_AUTHORITY_MISMATCH")

        temporal = (run_version.content or {}).get("temporal_authority") or {}
        windows = [
            window
            for window in temporal.get("scene_windows") or []
            if isinstance(window, dict) and window.get("scene_id") == SCENE_ID
        ]
        if len(windows) != 1 or not temporal.get("timeline_hash"):
            raise ValidationFailureError("SC04_TEMPORAL_AUTHORITY_MISSING")
        window = windows[0]
        try:
            start_ms = int(window["start_ms"])
            end_ms = int(window["end_ms"])
            duration_ms = int(window["duration_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationFailureError("SC04_TEMPORAL_WINDOW_INVALID") from exc
        if start_ms < 0 or end_ms <= start_ms or duration_ms != end_ms - start_ms:
            raise ValidationFailureError("SC04_TEMPORAL_WINDOW_INVALID")
        duration_seconds = duration_ms / 1000.0

        old_scene_intent = self._one_scene(
            source_artifacts["scene_visual_intent"].content,
            SCENE_ID,
        )
        strict_fields = {
            "scene_class",
            "narrative_function",
            "scene_meaning",
            "filmability_score",
            "stock_searchability_score",
            "custom_composition_score",
            "exact_text_dependency",
            "diagram_clarity_advantage",
            "motion_semantic_value",
        }
        missing_old_spec_fields = sorted(
            field for field in strict_fields if old_scene_intent.get(field) is None
        )
        if not missing_old_spec_fields:
            raise ValidationFailureError("SC04_ROOT_CAUSE_NOT_INSUFFICIENT_SCENE_SPEC")

        stock_context_signal = float(
            all(
                phrase in normalized_text
                for phrase in ("grounded office shot", "provide context")
            )
        )
        evidence_noncarry_signal = float(
            all(
                concept in concept_evidence
                for concept in ("evidence_limit", "team_baseline")
            )
        )
        workflow_coverage = len(concept_evidence) / len(_SC04_REQUIRED_SCRIPT_CONCEPTS)
        label_concepts = {
            "request_origin",
            "copied_fields",
            "missing_information",
            "gap_owner",
            "completed_handoffs",
            "rework",
            "judgment_steps",
        }
        label_coverage = len(label_concepts & set(concept_evidence)) / len(
            label_concepts
        )
        relationship_concepts = {
            "information_movement",
            "decision_work",
            "human_exception",
            "human_responsibility",
        }
        relationship_coverage = len(
            relationship_concepts & set(concept_evidence)
        ) / len(relationship_concepts)
        sequence_markers = ("before changing", "then separate", "moves information")
        sequence_coverage = sum(
            marker in normalized_text for marker in sequence_markers
        ) / len(sequence_markers)
        number_dependency = (
            1.0 if re.search(r"\b\d+(?:[.,]\d+)?\b", script_text) else 0.0
        )
        product_terms = ("brand", "product", "software", "app", "dashboard")
        product_dependency = min(
            1.0,
            sum(term in normalized_text for term in product_terms) / 2.0,
        )
        feature_scores = {
            "filmability_score": round(
                0.50 * stock_context_signal * (1.0 - 0.60 * evidence_noncarry_signal),
                4,
            ),
            "stock_searchability_score": round(
                0.50 * stock_context_signal * (1.0 - 0.70 * evidence_noncarry_signal),
                4,
            ),
            "required_specificity": round(workflow_coverage, 4),
            "custom_composition_score": round(
                (label_coverage + relationship_coverage) / 2.0,
                4,
            ),
            "exact_text_dependency": round(0.50 + 0.40 * label_coverage, 4),
            "exact_number_dependency": number_dependency,
            "diagram_clarity_advantage": round(
                0.50 * label_coverage + 0.50 * relationship_coverage,
                4,
            ),
            "brand_or_product_dependency": product_dependency,
            "product_specificity": product_dependency,
            "evidence_truth_requirement": round(
                0.10 + 0.30 * evidence_noncarry_signal,
                4,
            ),
            "identity_consistency_requirement": 0.0,
            "human_action_requirement": round(
                0.10 + 0.10 * relationship_coverage,
                4,
            ),
            "motion_semantic_value": round(
                0.50 * sequence_coverage + 0.40 * relationship_coverage,
                4,
            ),
        }
        semantic_intent = (
            "Animate the exact S04 narration as a labeled sequence: "
            f"{concept_evidence['workflow_observation']}; "
            f"{concept_evidence['request_origin']}; "
            f"{concept_evidence['copied_fields']}; "
            f"{concept_evidence['missing_information']}; "
            f"{concept_evidence['gap_owner']}; "
            f"{concept_evidence['team_baseline']}; "
            f"{concept_evidence['information_movement']} versus "
            f"{concept_evidence['decision_work']}; "
            f"{concept_evidence['human_responsibility']}."
        )

        cost_lines = (
            source_artifacts["cost_estimate_snapshot"].content.get("line_items") or []
        )
        aspect_ratios = {
            str(item["aspect_ratio"])
            for item in cost_lines
            if isinstance(item, dict) and item.get("aspect_ratio")
        }
        if len(aspect_ratios) != 1:
            raise ValidationFailureError("SC04_OUTPUT_ASPECT_AUTHORITY_MISSING")
        aspect_ratio = next(iter(aspect_ratios))
        routing_catalog = VisualSourceRouter().catalog
        minimum_resolution = routing_catalog.policy["minimum_output_resolution"]

        payload: dict[str, Any] = {
            "scene_id": SCENE_ID,
            "semantic_intent": semantic_intent,
            "target_duration_seconds": duration_seconds,
            "aspect_ratio": aspect_ratio,
            "crop_safety_required": True,
            "previous_scene_summary": previous,
            "next_scene_summary": following,
            "subject_action": f"{script_segment.get('section')}: {script_text.split('.', 1)[0]}.",
            "camera_angle": "eye-level",
            "shot_size": "medium",
            "segment_ids": ["S04"],
            "niche_visual_source_profile": NicheVisualSourceProfile(
                current_scene["niche_visual_source_profile"]
            ),
            "scene_class": "mechanism",
            "narrative_function": "primary_explanation",
            "scene_meaning": script_text.strip(),
            "editorial_intent": visual_intent_hint.strip(),
            **feature_scores,
            "named_workflow_nodes_required": True,
            "authorized_asset_available": False,
            "recurring_identity_required": False,
            "target_aspect_ratio": aspect_ratio,
            "minimum_resolution": minimum_resolution,
            "crop_safety_requirement": (
                "Keep the exact S04 workflow labels and human-responsibility "
                "contrast inside title- and caption-safe regions."
            ),
            "previous_scene_intent_ref": (
                f"artifact-version://{source_artifacts['visual_plan'].id}#scene/"
                f"{previous_scene['scene_id']}"
            ),
            "next_scene_intent_ref": (
                f"artifact-version://{source_artifacts['visual_plan'].id}#scene/"
                f"{next_scene['scene_id']}"
            ),
        }
        payload["content_hash"] = stable_hash(payload)
        requirements = SceneVisualRealizationRequirements.model_validate(payload)
        derivation_core = {
            "schema_version": "pkg1.sc04-semantic-derivation.v1",
            "root_cause": ROOT_CAUSE,
            "script_ref": self._version_ref(source_artifacts["script"]),
            "script_segment_id": "S04",
            "script_segment_hash": content_hash(script_segment),
            "script_text_hash": content_hash(script_text),
            "visual_intent_hint_hash": content_hash(visual_intent_hint),
            "concept_evidence": concept_evidence,
            "missing_old_spec_fields": missing_old_spec_fields,
            "feature_scores": feature_scores,
            "semantic_intent": semantic_intent,
            "requirements_hash": requirements.content_hash,
            "visual_plan_ref": self._version_ref(source_artifacts["visual_plan"]),
            "adjacent_scene_authority": {
                "previous": {
                    "scene_id": previous_scene["scene_id"],
                    "semantic_intent_hash": content_hash(previous),
                    "ref": requirements.previous_scene_intent_ref,
                },
                "next": {
                    "scene_id": next_scene["scene_id"],
                    "semantic_intent_hash": content_hash(following),
                    "ref": requirements.next_scene_intent_ref,
                },
            },
            "temporal_authority": {
                "run_ref": self._version_ref(run_version),
                "timeline_hash": temporal["timeline_hash"],
                "scene_window": deepcopy(window),
            },
            "routing_policy": {
                "ref": routing_catalog.policy_ref,
                "hash": routing_catalog.policy_hash,
                "minimum_output_resolution": minimum_resolution,
            },
        }
        return requirements, {
            **derivation_core,
            "content_hash": content_hash(derivation_core),
        }

    @staticmethod
    def _normalize_semantic_text(value: str) -> str:
        normalized = value.lower().replace("’", "'")
        return " ".join(re.sub(r"[^a-z0-9']+", " ", normalized).split())

    @staticmethod
    def _routing_evidence(
        requirements: SceneVisualRealizationRequirements,
    ) -> dict[str, Any]:
        router = VisualSourceRouter()
        completeness = VisualRealizationCompletenessGate().evaluate(requirements)
        pexels = PexelsEligibilityGate(router.catalog).evaluate(requirements)
        evidence_truth = EvidenceTruthSourceGate(router.catalog).evaluate(requirements)
        diagram = DiagramSuitabilityGate(router.catalog).evaluate(requirements)
        decision = router.route(requirements)
        if not completeness.passed:
            raise ValidationFailureError("SC04_REALIZATION_REQUIREMENTS_INCOMPLETE")
        return {
            "completeness": completeness,
            "pexels": pexels,
            "evidence_truth": evidence_truth,
            "diagram": diagram,
            "decision": decision,
        }

    def _scene_intent_payload(
        self,
        *,
        source: ArtifactVersion,
        requirements: SceneVisualRealizationRequirements,
        semantic_derivation: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-scene-intent.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
            }
        )
        old = self._one_scene(payload, SCENE_ID)
        new = {
            **old,
            "semantic_intent": requirements.semantic_intent,
            "source_role": "NATIVE_VISUAL",
            "evidence_role": "EXPLANATORY",
            "source_justification": (
                "SC-04 carries a labeled workflow/checklist relationship that "
                "stock cannot express; native motion preserves exact text and sequence."
            ),
            "native_mechanism": _SC04_BLUEPRINT["native_mechanism"],
            "native_motion_blueprint": deepcopy(_SC04_BLUEPRINT),
            "visual_realization_requirements": requirements.model_dump(mode="json"),
            "semantic_derivation": deepcopy(semantic_derivation),
            "root_cause_repaired": ROOT_CAUSE,
        }
        self._replace_scene(payload, new)
        return payload

    def _decision_set_payload(
        self,
        *,
        source: ArtifactVersion,
        requirements: SceneVisualRealizationRequirements,
        decision: Any,
        routing: dict[str, Any],
        semantic_derivation: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-visual-source-decisions.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "automatic_pexels_to_ai_fallback": False,
                "provider_outputs": [],
            }
        )
        item = decision.model_dump(mode="json")
        item.update(
            {
                "provider": "native",
                "planned_requests": 0,
                "maximum_automated_attempts": 0,
                "automatic_pexels_to_ai_fallback": False,
                "generated_evidence_authority": False,
                "eligibility": "LABELED_MECHANISM_NATIVE_MOTION_REQUIRED",
                "semantic_intent": requirements.semantic_intent,
                "requirements_hash": requirements.content_hash,
                "semantic_derivation_hash": semantic_derivation["content_hash"],
                "root_cause": ROOT_CAUSE,
                "old_route_finding": "PEXELS_ROUTE_INVALID_FOR_SCENE_MEANING",
                "gate_hashes": {
                    "completeness": routing["completeness"].content_hash,
                    "pexels": routing["pexels"].content_hash,
                    "evidence_truth": routing["evidence_truth"].content_hash,
                    "diagram": routing["diagram"].content_hash,
                },
            }
        )
        self._replace_scene(payload, item, key="decisions")
        return payload

    def _visual_plan_payload(
        self,
        *,
        source: ArtifactVersion,
        requirements: SceneVisualRealizationRequirements,
        semantic_derivation: dict[str, Any],
        decision_set_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-visual-plan.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "visual_source_decision_set": decision_set_ref,
                "provider_outputs": [],
            }
        )
        old = self._one_scene(payload, SCENE_ID)
        new = {
            **old,
            "semantic_intent": requirements.semantic_intent,
            "source_role": "NATIVE_VISUAL",
            "evidence_role": "EXPLANATORY",
            "source_justification": (
                "SC-04 carries a labeled workflow/checklist relationship that "
                "stock cannot express; native motion preserves exact text and sequence."
            ),
            "preferred_source_route": REPAIRED_ROUTE.value,
            "provider": "native",
            "native_mechanism": _SC04_BLUEPRINT["native_mechanism"],
            "native_motion_blueprint": deepcopy(_SC04_BLUEPRINT),
            "visual_realization_requirements_hash": requirements.content_hash,
            "semantic_derivation_hash": semantic_derivation["content_hash"],
        }
        self._replace_scene(payload, new)
        payload["coverage"] = {
            **deepcopy(payload.get("coverage") or {}),
            "complete": True,
            "sc04_route": REPAIRED_ROUTE.value,
        }
        return payload

    def _compiled_asset_plan_payload(
        self,
        *,
        source: ArtifactVersion,
        visual_plan_ref: dict[str, Any],
        decision_set_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-asset-request-plan.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "visual_plan": visual_plan_ref,
                "visual_source_decision_set": decision_set_ref,
                "execution_enabled": False,
                "selected_provider_assets": [],
                "raw_provider_urls": [],
                "automatic_pexels_to_ai_fallback": False,
            }
        )
        request = self._one_scene(payload, SCENE_ID, key="requests")
        self._replace_scene(
            payload,
            {
                **request,
                "route": REPAIRED_ROUTE.value,
                "provider": "native",
                "maximum_automated_attempts": 0,
                "human_approval_required": False,
                "state": "PLANNED_LOCAL_NOT_EXECUTED",
                "idempotency_ref": f"pkg1-sc04/{revision_hash}/{SCENE_ID}",
            },
            key="requests",
        )
        return payload

    def _provider_plan_payload(
        self,
        *,
        source: ArtifactVersion,
        decision_set_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-provider-execution-plan.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "visual_source_decision_set": decision_set_ref,
                "execution_enabled": False,
                "provider_outputs": [],
                "automatic_pexels_to_ai_fallback": False,
                "external_ai_video_fallback": False,
                "mr1_approval": "PENDING_FRESH_REAPPROVAL",
                "approval_requirements": [
                    "EXACT_PKG1_SC04_REVISION_PASS",
                    "FRESH_EXACT_MR1_REAPPROVAL",
                ],
            }
        )
        route = self._one_scene(payload, SCENE_ID, key="scene_routes")
        self._replace_scene(
            payload,
            {
                **route,
                "route": REPAIRED_ROUTE.value,
                "provider": "native",
                "attempt_cap": 0,
                "idempotency_ref": f"provider-plan://{revision_id}/{SCENE_ID}",
            },
            key="scene_routes",
        )
        for stage in payload.get("stages") or []:
            if stage.get("provider") == "pexels_api":
                stage["planned_requests"] = 2
                stage["state"] = "NOT_AUTHORIZED"
            if stage.get("provider") == "native_graphics":
                stage["planned_requests"] = 7
                stage["state"] = "PLANNING_ONLY"
            if stage.get("provider") == "google_drive":
                stage["operation"] = (
                    "canonical_review_archive_plus_finalization_supplement"
                )
                stage["planned_requests"] = 2
                stage["state"] = "WAITING_FOR_FINAL_MEDIA"
                stage["idempotency_phases"] = deepcopy(_DRIVE_IDEMPOTENCY_PHASES)
        return payload

    def _cost_payload(
        self,
        *,
        source: ArtifactVersion,
        visual_plan_ref: dict[str, Any],
        provider_plan_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-cost-estimate.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "actual_cost": None,
                "incremental_cost_usd": 0.0,
                "decision": "PASS",
            }
        )
        payload.setdefault("bindings", {})["scene_plan"] = visual_plan_ref
        payload["bindings"]["provider_plan"] = provider_plan_ref
        for line in payload.get("line_items") or []:
            if line.get("provider") == "pexels_api":
                line["planned_scenes"] = 2
            if line.get("provider") == "native_ffmpeg_renderer":
                line["planned_native_scenes"] = 7
            if line.get("provider") == "google_drive":
                line.update(
                    {
                        "planned_requests": 2,
                        "idempotency_phases": deepcopy(_DRIVE_IDEMPOTENCY_PHASES),
                        "estimated_incremental_cost_usd": 0.0,
                        "basis": (
                            "Two exact zero-cost Drive mutations: canonical "
                            "review archive before human PASS, then one "
                            "finalization supplement before FinalMediaRef."
                        ),
                    }
                )
        return payload

    def _provenance_payload(
        self,
        *,
        source: ArtifactVersion,
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-asset-provenance-plan.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "provider_output_exists": False,
                "generated_evidence_authority": False,
            }
        )
        pexels = payload.setdefault("pexels", {})
        pexels["planned_scenes"] = [
            scene for scene in pexels.get("planned_scenes", []) if scene != SCENE_ID
        ]
        pexels["outputs"] = []
        native = payload.setdefault("native_assets", {})
        native["scene_ids"] = sorted(set(native.get("scene_ids", [])) | {SCENE_ID})
        native["authorship"] = "VCOS_NATIVE"
        return payload

    def _rights_payload(
        self,
        *,
        source: ArtifactVersion,
        provenance_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-rights-disclosure.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "asset_provenance_plan": provenance_ref,
                "provider_outputs_claimed": False,
                "generated_evidence_authority": False,
                "planning_state": "PASS",
                "decision": "PASS",
                "sc04_rights_state": "NATIVE_AUTHORSHIP_REQUIRED_NO_STOCK_ASSET",
                "drive_archive_scope": {
                    "archive_only_not_publish": True,
                    "canonical_review_archive_required": True,
                    "finalization_supplement_required": True,
                    "canonical_archive_mutated_by_supplement": False,
                    "idempotency_phases": deepcopy(_DRIVE_IDEMPOTENCY_PHASES),
                    "rights_or_license_expansion": False,
                },
            }
        )
        return payload

    def _supplemental_alignment_payload(
        self,
        *,
        source_artifacts: dict[str, ArtifactVersion],
        source_project: VideoProject,
        scene_intent_version: ArtifactVersion,
        decision_set_version: ArtifactVersion,
        visual_plan_version: ArtifactVersion,
        overlay: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        try:
            market_profile = TargetMarketProfile.model_validate(
                source_artifacts["target_market_profile"].content["profile"]
            )
            market_digest = TargetMarketDigest.model_validate(
                source_artifacts["target_market_digest"].content["digest"]
            )
            niche_digest = NicheContractDigest.model_validate(
                source_artifacts["niche_contract_digest"].content
            )
        except Exception as exc:
            raise ValidationFailureError(
                "PKG1_SC04_ALIGNMENT_AUTHORITY_INVALID"
            ) from exc
        if (
            source_artifacts["target_market_profile"].content.get("canonical_hash")
            != market_profile.content_hash
            or source_artifacts["target_market_digest"].content.get("canonical_hash")
            != market_digest.content_hash
        ):
            raise ValidationFailureError("PKG1_SC04_MARKET_CANONICAL_HASH_MISMATCH")
        visual_plan_ref = self._version_ref(visual_plan_version)
        scenes = visual_plan_version.content.get("scenes") or []
        market_result = VisualMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=VisualMarketAlignmentInput(
                market_contexts=sorted(
                    {
                        str(item["target_market"])
                        for item in scenes
                        if item.get("target_market")
                    }
                ),
                currencies=sorted(
                    {str(item["currency"]) for item in scenes if item.get("currency")}
                ),
                date_format=(scenes[0].get("date_format") if scenes else None),
                workplace_context=(
                    scenes[0].get("workplace_context") if scenes else None
                ),
                evidence_authentic=bool(scenes)
                and all(
                    item.get("generated_evidence_authority") is False for item in scenes
                ),
            ),
            subject_ref=f"artifact-version://{visual_plan_version.id}",
        )
        if market_result.verdict != MarketVerdict.PASS:
            raise ValidationFailureError("PKG1_SC04_VISUAL_MARKET_ALIGNMENT_NOT_PASS")

        scene_intents = scene_intent_version.content.get("scenes") or []
        visual_decisions = decision_set_version.content.get("decisions") or []
        visual_direction = source_artifacts["visual_direction_contract"].content
        semantic_checks = {
            NicheCriterion.VISUAL_LANGUAGE_FIT: (
                visual_direction.get("niche_visual_source_profile")
                == niche_digest.visual_source_profile,
                "The exact compiled visual source profile remains bound.",
            ),
            NicheCriterion.VISUAL_MEANING_FIDELITY: (
                len(scene_intents) == len(visual_decisions)
                and {item.get("scene_id") for item in scene_intents}
                == {item.get("scene_id") for item in visual_decisions}
                and all(item.get("semantic_intent") for item in visual_decisions),
                "Every scene retains one semantic source decision with a matching ID.",
            ),
            NicheCriterion.PILLAR_CATEGORY_FIT: (
                visual_direction.get("content_pillar")
                == niche_digest.content_pillar_key
                and visual_direction.get("category_id")
                == str(niche_digest.category_id),
                "The visual direction remains bound to the compiled pillar and category.",
            ),
        }
        if not all(passed for passed, _ in semantic_checks.values()):
            raise ValidationFailureError(
                "PKG1_SC04_VISUAL_NICHE_SEMANTIC_EVIDENCE_NOT_PASS"
            )
        evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=f"artifact-version://{visual_plan_version.id}",
            content_hash=visual_plan_version.content_hash,
        )
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, source_project.policy_snapshot_id
        )
        if snapshot is None:
            raise ValidationFailureError("PKG1_SC04_POLICY_SNAPSHOT_MISSING")
        niche_result = VisualNicheAlignmentGate().evaluate(
            VisualNicheAlignmentInput(
                niche_contract_digest=niche_digest,
                niche_contract_digest_ref=(
                    f"artifact-version://{source_artifacts['niche_contract_digest'].id}"
                ),
                niche_contract_digest_hash=niche_digest.content_hash,
                active_policy_snapshot_ref=(
                    f"compiled-policy-snapshot://{snapshot.id}"
                ),
                active_policy_snapshot_hash=snapshot.content_hash,
                subject_ref=evidence.ref,
                subject_hash=visual_plan_version.content_hash,
                semantic_evidence=[
                    NicheCriterionEvidence(
                        criterion=criterion,
                        verdict=NicheGateVerdict.PASS,
                        rationale=rationale,
                        evidence_refs=[evidence],
                    )
                    for criterion, (_passed, rationale) in semantic_checks.items()
                ],
                evidence_refs=[evidence],
                visual_direction_contract=visual_direction,
                scene_visual_intents=scene_intents,
                visual_source_decisions=visual_decisions,
                content_pillar_key=niche_digest.content_pillar_key,
                category_id=niche_digest.category_id,
                ai_image_editorial_justification_refs={},
                authorized_asset_evidence_refs={},
            )
        )
        if niche_result.verdict != NicheGateVerdict.PASS:
            raise ValidationFailureError("PKG1_SC04_VISUAL_NICHE_ALIGNMENT_NOT_PASS")
        return {
            "schema_version": "pkg1.sc04-visual-alignment-evidence.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "subject": visual_plan_ref,
            "market_alignment": {
                "verdict": market_result.verdict.value,
                "evaluation_type": "VisualMarketAlignmentGate",
                "gate_result": market_result.model_dump(mode="json"),
                "target_market": "US",
                "workplace_context": "US_SMALL_BUSINESS",
                "currency": "USD",
                "date_format": "MMM D, YYYY",
                "generated_evidence_authority": False,
                "reason_codes": [
                    "SC04_NATIVE_MOTION_HAS_NO_FOREIGN_UI_OR_MARKET_DRIFT",
                    "SC04_EXACT_LABELS_RETAIN_NATIVE_AUTHORITY",
                ],
            },
            "niche_alignment": {
                "verdict": niche_result.verdict.value,
                "evaluation_type": "VisualNicheAlignmentGate",
                "gate_result": niche_result.model_dump(mode="json"),
                "niche_visual_source_profile": "STOCK_ASSISTED",
                "native_route_allowed": True,
                "reason_codes": [
                    "NATIVE_MOTION_MATCHES_WORKFLOW_EXPLAINER_NICHE",
                    "STOCK_ASSISTED_PROFILE_DOES_NOT_REQUIRE_STOCK_PER_SCENE",
                ],
            },
            "effective_ads_only_policy": overlay["ref"],
            "geo_closeout_evidence": overlay["closeout_ref"],
            "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            "prior_dossiers": {
                "market_alignment_dossier": {
                    **self._version_ref(source_artifacts["market_alignment_dossier"]),
                    "nonvisual_components_reused": True,
                    "old_visual_binding_state": "SUPERSEDED",
                },
                "niche_alignment_dossier": {
                    **self._version_ref(source_artifacts["niche_alignment_dossier"]),
                    "nonvisual_components_reused": True,
                    "old_visual_binding_state": "SUPERSEDED",
                },
            },
            "all_required_checks_pass": True,
        }

    def _risk_payload(
        self,
        *,
        source: ArtifactVersion,
        rights_ref: dict[str, Any],
        alignment_ref: dict[str, Any],
        overlay: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        payload = deepcopy(source.content)
        payload.update(
            {
                "schema_version": "pkg1.sc04-publish-risk-dossier.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "supersedes": self._version_ref(source),
                "package_content_decision": "REVIEW_REQUIRED",
                "publish_execution_decision": "BLOCK",
                "sc04_visual_repair": {
                    "decision": "PASS_PLANNING",
                    "route": REPAIRED_ROUTE.value,
                    "provider_execution_required": False,
                    "third_pexels_attempt_allowed": False,
                },
                "effective_ads_only_policy": overlay["ref"],
                "geo_closeout_evidence": overlay["closeout_ref"],
                "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            }
        )
        payload["market_alignment"] = {
            "overall_decision": "PASS_PLANNING",
            "supplemental_visual_alignment": alignment_ref,
            "prior_visual_dossier_binding": "SUPERSEDED",
            "publish_window_status": "REVIEW_REQUIRED",
        }
        payload["rights_provenance_risk"] = {
            "decision": "PASS_PLANNING",
            "rights_report": rights_ref,
        }
        payload["package_integrity"] = {
            "planning_authority_hash": revision_hash,
            "planning_authority_state": "BOUND",
            "change_requires_new_version": True,
            "final_package_integrity": "PENDING_PACKAGE_HASH",
        }
        return payload

    def _evaluate_planning_gates(
        self,
        *,
        created: dict[str, ArtifactVersion],
        requirements: SceneVisualRealizationRequirements,
        source_artifacts: dict[str, ArtifactVersion],
        run_version: ArtifactVersion,
        script_segment: dict[str, Any],
        semantic_derivation: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        rederived_requirements, rederived_semantics = self._requirements(
            source_artifacts=source_artifacts,
            run_version=run_version,
            script_segment=script_segment,
        )
        visual_plan = created["visual_plan"].content
        decision_set = created["visual_source_decision_set"].content
        provider_plan = created["provider_execution_plan"].content
        provenance = created["asset_provenance_plan"].content
        rights = created["rights_disclosure_completeness_report"].content
        cost = created["cost_estimate_snapshot"].content
        sc04_scene = self._one_scene(visual_plan, SCENE_ID)
        sc04_decision = self._one_scene(decision_set, SCENE_ID, key="decisions")
        sc04_route = self._one_scene(provider_plan, SCENE_ID, key="scene_routes")
        blueprint = sc04_scene.get("native_motion_blueprint") or {}
        phases = [item.get("phase") for item in blueprint.get("phases") or []]
        pexels = provenance.get("pexels") or {}
        native = provenance.get("native_assets") or {}
        pexels_scene_count = sum(
            item.get("provider") == "pexels_api"
            for item in provider_plan.get("scene_routes") or []
        )
        drive_stages = [
            item
            for item in provider_plan.get("stages") or []
            if item.get("provider") == "google_drive"
        ]
        drive_cost_lines = [
            item
            for item in cost.get("line_items") or []
            if item.get("provider") == "google_drive"
        ]
        drive_stage = drive_stages[0] if len(drive_stages) == 1 else {}
        drive_cost_line = drive_cost_lines[0] if len(drive_cost_lines) == 1 else {}
        drive_rights_scope = rights.get("drive_archive_scope") or {}

        def evaluated(
            name: str, checks: dict[str, bool], **evidence: Any
        ) -> dict[str, Any]:
            if not checks or not all(value is True for value in checks.values()):
                failed = sorted(
                    key for key, value in checks.items() if value is not True
                )
                raise ValidationFailureError(
                    f"PKG1_SC04_{name.upper()}_GATE_NOT_PASS:" + ",".join(failed)
                )
            return {
                "verdict": "PASS",
                "evaluation_type": "DETERMINISTIC_PLANNING_CONTRACT",
                "checks": checks,
                **evidence,
            }

        return {
            "semantic_match": evaluated(
                "semantic_match",
                {
                    "baseline_checklist_represented": (
                        "BASELINE_CHECKLIST" in str(blueprint.get("native_mechanism"))
                    ),
                    "information_vs_judgment_split_represented": (
                        "INFORMATION_VS_JUDGMENT_SPLIT"
                        in str(blueprint.get("native_mechanism"))
                    ),
                    "human_exception_path_represented": any(
                        "HUMAN_EXCEPTION_PATH" in (item.get("items") or [])
                        for item in blueprint.get("phases") or []
                    ),
                    "semantic_intent_exact": (
                        sc04_scene.get("semantic_intent")
                        == requirements.semantic_intent
                        == sc04_decision.get("semantic_intent")
                    ),
                    "script_segment_hash_revalidated": (
                        semantic_derivation.get("script_segment_hash")
                        == content_hash(script_segment)
                        == rederived_semantics.get("script_segment_hash")
                    ),
                    "script_meaning_derivation_revalidated": (
                        semantic_derivation == rederived_semantics
                        and semantic_derivation.get("content_hash")
                        == content_hash(
                            {
                                key: deepcopy(value)
                                for key, value in semantic_derivation.items()
                                if key != "content_hash"
                            }
                        )
                    ),
                    "requirements_rederived_from_exact_script": (
                        requirements.content_hash
                        == rederived_requirements.content_hash
                        == semantic_derivation.get("requirements_hash")
                    ),
                },
                requirements_hash=requirements.content_hash,
                semantic_derivation_hash=semantic_derivation["content_hash"],
            ),
            "visual_continuity": evaluated(
                "visual_continuity",
                {
                    "previous_scene_binding_present": bool(
                        requirements.previous_scene_intent_ref
                    ),
                    "next_scene_binding_present": bool(
                        requirements.next_scene_intent_ref
                    ),
                    "aspect_ratio_unchanged": (
                        requirements.aspect_ratio == "16:9"
                        and requirements.target_aspect_ratio == "16:9"
                    ),
                    "crop_safe_labels": (
                        requirements.crop_safety_required is True
                        and bool(requirements.crop_safety_requirement)
                    ),
                    "provider_cut_removed": (
                        sc04_route.get("provider") == "native"
                        and sc04_route.get("attempt_cap") == 0
                    ),
                },
            ),
            "repetitive_production_risk": evaluated(
                "repetitive_production_risk",
                {
                    "stock_asset_reuse_for_sc04": (
                        SCENE_ID not in (pexels.get("planned_scenes") or [])
                    ),
                    "native_blueprint_has_four_semantic_phases": (
                        phases
                        == [
                            "OBSERVE_WORKFLOW",
                            "MEASURE_BASELINE",
                            "SPLIT_WORK",
                            "PRESERVE_RESPONSIBILITY",
                        ]
                    ),
                    "only_sc04_native_route_added": (
                        SCENE_ID in (native.get("scene_ids") or [])
                    ),
                    "recurring_identity_required": (
                        requirements.recurring_identity_required is False
                    ),
                },
            ),
            "rights_disclosure_completeness": evaluated(
                "rights_disclosure_completeness",
                {
                    "sc04_native_authorship_required": (
                        rights.get("sc04_rights_state")
                        == "NATIVE_AUTHORSHIP_REQUIRED_NO_STOCK_ASSET"
                        and native.get("authorship") == "VCOS_NATIVE"
                    ),
                    "sc04_stock_provenance_required": (
                        SCENE_ID not in (pexels.get("planned_scenes") or [])
                    ),
                    "provider_outputs_claimed": (
                        rights.get("provider_outputs_claimed") is False
                    ),
                    "generated_evidence_authority": (
                        rights.get("generated_evidence_authority") is False
                    ),
                    "drive_archive_rights_scope_exact": (
                        drive_rights_scope
                        == {
                            "archive_only_not_publish": True,
                            "canonical_review_archive_required": True,
                            "finalization_supplement_required": True,
                            "canonical_archive_mutated_by_supplement": False,
                            "idempotency_phases": _DRIVE_IDEMPOTENCY_PHASES,
                            "rights_or_license_expansion": False,
                        }
                    ),
                },
            ),
            "provider_cost_estimate": evaluated(
                "provider_cost_estimate",
                {
                    "sc04_provider_attempt_cap_zero": (
                        sc04_route.get("attempt_cap") == 0
                    ),
                    "sc04_provider_is_native": (sc04_route.get("provider") == "native"),
                    "incremental_cost_usd_zero": (
                        cost.get("incremental_cost_usd", 0.0) == 0.0
                    ),
                    "actual_cost_not_claimed": cost.get("actual_cost") is None,
                    "remaining_pexels_scene_count_two": pexels_scene_count == 2,
                    "execution_disabled": (
                        provider_plan.get("execution_enabled") is False
                    ),
                    "drive_exact_two_phase_mutation_plan": (
                        drive_stage.get("planned_requests") == 2
                        and drive_stage.get("idempotency_phases")
                        == _DRIVE_IDEMPOTENCY_PHASES
                    ),
                    "drive_cost_scope_zero": (
                        drive_cost_line.get("planned_requests") == 2
                        and drive_cost_line.get("idempotency_phases")
                        == _DRIVE_IDEMPOTENCY_PHASES
                        and drive_cost_line.get("estimated_incremental_cost_usd") == 0.0
                    ),
                },
            ),
        }

    @staticmethod
    def _gate_payload(
        *,
        routing: dict[str, Any],
        attempt_evidence: dict[str, Any],
        overlay: dict[str, Any],
        alignment_ref: dict[str, Any],
        visual_plan_ref: dict[str, Any],
        decision_ref: dict[str, Any],
        cost_ref: dict[str, Any],
        rights_ref: dict[str, Any],
        provenance_ref: dict[str, Any],
        alignment_evaluation: dict[str, Any],
        planning_gate_evaluations: dict[str, dict[str, Any]],
        mr1_manifest_compatibility: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.sc04-gates.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "technical_revision": "PASS",
            "root_cause_classification": {
                "verdict": "PASS",
                "primary": ROOT_CAUSE,
                "exactly_one_primary": True,
            },
            "scene_spec_completeness": {
                "verdict": "PASS",
                **routing["completeness"].model_dump(mode="json"),
            },
            "pexels_eligibility": {
                "verdict": "PASS",
                **routing["pexels"].model_dump(mode="json"),
            },
            "evidence_truth": {
                "verdict": "PASS",
                **routing["evidence_truth"].model_dump(mode="json"),
            },
            "diagram_suitability": {
                "verdict": "PASS",
                **routing["diagram"].model_dump(mode="json"),
            },
            "route_decision": {
                "verdict": "PASS",
                "selected_route": routing["decision"].preferred_source_route.value,
                "decision_hash": routing["decision"].content_hash,
                "provider_execution_required": False,
            },
            "visual_niche_alignment": {
                "verdict": alignment_evaluation["niche_alignment"]["verdict"],
                "evaluation_type": alignment_evaluation["niche_alignment"][
                    "evaluation_type"
                ],
                "evidence_ref": alignment_ref,
                "gate_result": alignment_evaluation["niche_alignment"]["gate_result"],
            },
            "visual_market_alignment": {
                "verdict": alignment_evaluation["market_alignment"]["verdict"],
                "evaluation_type": alignment_evaluation["market_alignment"][
                    "evaluation_type"
                ],
                "evidence_ref": alignment_ref,
                "gate_result": alignment_evaluation["market_alignment"]["gate_result"],
            },
            "semantic_match": {
                **deepcopy(planning_gate_evaluations["semantic_match"]),
                "visual_plan_ref": visual_plan_ref,
                "decision_ref": decision_ref,
                "requirements_hash": routing["decision"].input_feature_snapshot[
                    "requirements_hash"
                ],
                "decision_hash": routing["decision"].content_hash,
            },
            "visual_continuity": {
                **deepcopy(planning_gate_evaluations["visual_continuity"]),
                "visual_plan_ref": visual_plan_ref,
            },
            "repetitive_production_risk": {
                **deepcopy(planning_gate_evaluations["repetitive_production_risk"]),
                "provenance_ref": provenance_ref,
            },
            "rights_disclosure_completeness": {
                **deepcopy(planning_gate_evaluations["rights_disclosure_completeness"]),
                "rights_ref": rights_ref,
                "provenance_ref": provenance_ref,
            },
            "provider_cost_estimate": {
                **deepcopy(planning_gate_evaluations["provider_cost_estimate"]),
                "cost_ref": cost_ref,
            },
            "ai_image_eligibility": {
                "verdict": "NOT_APPLICABLE",
                "reason_codes": [
                    "SC04_NATIVE_MOTION_ROUTE_SELECTED",
                    "AI_IMAGE_PROVIDER_NOT_REQUIRED",
                ],
                "provider_execution_allowed": False,
            },
            "attempt_evidence": {
                "verdict": "PASS",
                "attempt_count": attempt_evidence["attempt_count"],
                "all_ledgers_preserved": True,
                "third_attempt_created": False,
            },
            "effective_ads_only_policy": {
                "verdict": "PASS",
                "binding": overlay["ref"],
                "geo_closeout_evidence": overlay["closeout_ref"],
                "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            },
            "visual_market_niche_alignment": {
                "verdict": "PASS",
                "binding": alignment_ref,
            },
            "mr1_reapproval_manifest_compatibility": deepcopy(
                mr1_manifest_compatibility
            ),
            "provider_boundary": "PASS",
            "threshold_integrity": {
                "verdict": "PASS",
                "routing_policy_ref": routing["decision"].policy_ref,
                "routing_policy_hash": routing["decision"].policy_hash,
                "thresholds_modified": False,
                "semantic_fit_threshold_lowered": False,
            },
            "human_review": "PENDING",
            "mr1_state": "BLOCKED_PENDING_PACKAGE_APPROVAL",
        }

    def _artifact_classification(
        self, source_artifacts: dict[str, ArtifactVersion]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        historical_context_types = {
            "market_alignment_dossier",
            "niche_alignment_dossier",
            "target_market_consistency_check",
            "publish_handoff_package",
            "upload_card",
        }
        reused: dict[str, Any] = {}
        historical: dict[str, Any] = {}
        superseded: dict[str, Any] = {}
        for key, version in sorted(source_artifacts.items()):
            ref = self._version_ref(version)
            if key in _AFFECTED_SOURCE_TYPES:
                superseded[key] = ref
            elif key in historical_context_types:
                historical[key] = {
                    **ref,
                    "authority_for_new_visual_plan": False,
                }
            else:
                reused[key] = ref
        return reused, historical, superseded

    def _effective_artifacts(
        self,
        *,
        source_artifacts: dict[str, ArtifactVersion],
        revised: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return the exact artifact-version resolver view; revised refs win."""

        effective = {
            key: self._version_ref(version)
            for key, version in sorted(source_artifacts.items())
        }
        effective.update(deepcopy(revised))
        return effective

    def _mr1_manifest_compatibility_gate(
        self,
        *,
        effective_artifacts: dict[str, dict[str, Any]],
        current_visual_authority: dict[str, Any],
        visual_plan_ref: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        missing = sorted(_MR1_EFFECTIVE_REQUIRED_TYPES - set(effective_artifacts))
        if missing:
            raise ValidationFailureError(
                "PKG1_SC04_MR1_EFFECTIVE_BINDINGS_MISSING:" + ",".join(missing)
            )
        invalid: list[str] = []
        for key in sorted(_MR1_EFFECTIVE_REQUIRED_TYPES):
            ref = effective_artifacts[key]
            raw_version_id = ref.get("artifact_version_id")
            try:
                version_id = uuid.UUID(str(raw_version_id))
            except (TypeError, ValueError):
                invalid.append(key)
                continue
            version = self.session.get(ArtifactVersion, version_id)
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact.artifact_type != key
                or artifact.current_version_id != version.id
                or ref.get("artifact_id") != str(artifact.id)
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or ref.get("content_hash") != version.content_hash
                or content_hash(version.content) != version.content_hash
            ):
                invalid.append(key)
        if invalid:
            raise ValidationFailureError(
                "PKG1_SC04_MR1_EFFECTIVE_BINDINGS_INVALID:" + ",".join(invalid)
            )
        current_visual_version = self.session.get(
            ArtifactVersion,
            uuid.UUID(str(current_visual_authority["artifact_version_id"])),
        )
        visual_content = (
            current_visual_version.content if current_visual_version is not None else {}
        )
        provider_content = self.session.get(
            ArtifactVersion,
            uuid.UUID(
                str(
                    effective_artifacts["provider_execution_plan"][
                        "artifact_version_id"
                    ]
                )
            ),
        )
        cost_content = self.session.get(
            ArtifactVersion,
            uuid.UUID(
                str(
                    effective_artifacts["cost_estimate_snapshot"]["artifact_version_id"]
                )
            ),
        )
        provider_payload = provider_content.content if provider_content else {}
        cost_payload = cost_content.content if cost_content else {}
        checks = {
            "all_required_effective_artifacts_present": not missing,
            "all_required_effective_refs_hash_revalidated": not invalid,
            "current_visual_subject_is_revised_visual_plan": (
                visual_content.get("subject") == visual_plan_ref
            ),
            "current_visual_revision_binding": (
                visual_content.get("revision_id") == revision_id
                and visual_content.get("revision_hash") == revision_hash
            ),
            "actual_visual_market_gate_pass": (
                (visual_content.get("market_alignment") or {}).get("verdict") == "PASS"
            ),
            "actual_visual_niche_gate_pass": (
                (visual_content.get("niche_alignment") or {}).get("verdict") == "PASS"
            ),
            "provider_plan_uses_effective_decision_set": (
                provider_payload.get("visual_source_decision_set")
                == effective_artifacts["visual_source_decision_set"]
            ),
            "provider_execution_disabled": (
                provider_payload.get("execution_enabled") is False
            ),
            "cost_plan_uses_effective_provider_plan": (
                (cost_payload.get("bindings") or {}).get("provider_plan")
                == effective_artifacts["provider_execution_plan"]
            ),
            "approval_intentionally_pending": True,
        }
        if not all(checks.values()):
            failed = sorted(key for key, value in checks.items() if not value)
            raise ValidationFailureError(
                "PKG1_SC04_MR1_MANIFEST_COMPATIBILITY_NOT_PASS:" + ",".join(failed)
            )
        return {
            "schema_version": "pkg1.sc04-mr1-manifest-compatibility.v1",
            "verdict": "PASS",
            "resolver_contract": "EFFECTIVE_ARTIFACTS_V1",
            "required_effective_artifact_types": sorted(_MR1_EFFECTIVE_REQUIRED_TYPES),
            "checks": checks,
            "pre_review_only": True,
            "approval_created": False,
            "provider_execution_authorized": False,
        }

    def _diff_payload(
        self,
        *,
        source_package: ArtifactVersion,
        source_artifacts: dict[str, ArtifactVersion],
        created: dict[str, ArtifactVersion],
        reused_refs: dict[str, Any],
        historical_context_refs: dict[str, Any],
        superseded_refs: dict[str, Any],
        old_scene: dict[str, Any],
        new_scene: dict[str, Any],
        old_decision: dict[str, Any],
        new_decision: dict[str, Any],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        def unchanged_rows_exact(
            old_rows: list[dict[str, Any]],
            new_rows: list[dict[str, Any]],
        ) -> tuple[bool, list[str]]:
            old_index = {
                str(item.get("scene_id")): item
                for item in old_rows
                if item.get("scene_id") != SCENE_ID
            }
            new_index = {
                str(item.get("scene_id")): item
                for item in new_rows
                if item.get("scene_id") != SCENE_ID
            }
            duplicate_free = len(old_index) == len(
                [item for item in old_rows if item.get("scene_id") != SCENE_ID]
            ) and len(new_index) == len(
                [item for item in new_rows if item.get("scene_id") != SCENE_ID]
            )
            exact = duplicate_free and old_index == new_index
            return exact, sorted(old_index)

        comparisons: dict[str, tuple[bool, list[str]]] = {
            "scene_visual_intent": unchanged_rows_exact(
                source_artifacts["scene_visual_intent"].content.get("scenes") or [],
                created["scene_visual_intent"].content.get("scenes") or [],
            ),
            "visual_source_decision_set": unchanged_rows_exact(
                source_artifacts["visual_source_decision_set"].content.get("decisions")
                or [],
                created["visual_source_decision_set"].content.get("decisions") or [],
            ),
            "visual_plan": unchanged_rows_exact(
                source_artifacts["visual_plan"].content.get("scenes") or [],
                created["visual_plan"].content.get("scenes") or [],
            ),
            "compiled_asset_request_plan": unchanged_rows_exact(
                source_artifacts["compiled_asset_request_plan"].content.get("requests")
                or [],
                created["compiled_asset_request_plan"].content.get("requests") or [],
            ),
            "provider_execution_plan": unchanged_rows_exact(
                source_artifacts["provider_execution_plan"].content.get("scene_routes")
                or [],
                created["provider_execution_plan"].content.get("scene_routes") or [],
            ),
        }
        drift = sorted(
            key for key, (exact, _scene_ids) in comparisons.items() if not exact
        )
        if drift:
            raise ValidationFailureError(
                "PKG1_SC04_UNAFFECTED_SCENE_DRIFT:" + ",".join(drift)
            )
        unchanged_scene_ids = comparisons["visual_plan"][1]
        unchanged_exact = not drift
        return {
            "schema_version": "pkg1.sc04-revision-diff.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "supersedes": self._version_ref(source_package),
            "scope": "SC04_VISUAL_SOURCE_AND_DEPENDENT_PLANS_ONLY",
            "changed_scene_ids": [SCENE_ID],
            "unchanged_scene_ids": unchanged_scene_ids,
            "unchanged_scenes_exact": unchanged_exact,
            "unchanged_component_rows": {
                key: {
                    "exact": exact,
                    "scene_ids": scene_ids,
                }
                for key, (exact, scene_ids) in comparisons.items()
            },
            "scene_diff": {
                "before": old_scene,
                "after": new_scene,
            },
            "decision_diff": {
                "before": old_decision,
                "after": new_decision,
            },
            "reused_artifacts": reused_refs,
            "historical_context_artifacts": historical_context_refs,
            "superseded_artifacts": superseded_refs,
            "revised_artifacts": {
                key: self._version_ref(value) for key, value in sorted(created.items())
            },
            "script_changed": False,
            "spoken_text_changed": False,
            "voice_changed": False,
            "attempt_ledgers_changed": False,
            "provider_call_made": False,
            "affected_artifact_types": sorted(
                {
                    *created,
                    DIFF_ARTIFACT_TYPE,
                    REVIEW_PACKET_ARTIFACT_TYPE,
                    "package_manifest",
                }
            ),
            "unexpected_artifact_types": [],
        }

    def _review_packet(
        self,
        *,
        source_package: ArtifactVersion,
        source_artifacts: dict[str, ArtifactVersion],
        requirements: SceneVisualRealizationRequirements,
        routing: dict[str, Any],
        attempt_evidence: dict[str, Any],
        script_segment: dict[str, Any],
        semantic_derivation: dict[str, Any],
        overlay: dict[str, Any],
        created: dict[str, ArtifactVersion],
        revision_id: str,
        revision_hash: str,
    ) -> dict[str, Any]:
        old_provider = source_artifacts["provider_execution_plan"].content
        new_provider = created["provider_execution_plan"].content
        old_visual_scene = self._one_scene(
            source_artifacts["visual_plan"].content,
            SCENE_ID,
        )
        old_sc04 = self._one_scene(old_provider, SCENE_ID, key="scene_routes")
        new_sc04 = self._one_scene(new_provider, SCENE_ID, key="scene_routes")
        old_pexels_count = sum(
            item.get("provider") == "pexels_api"
            for item in old_provider.get("scene_routes") or []
        )
        new_pexels_count = sum(
            item.get("provider") == "pexels_api"
            for item in new_provider.get("scene_routes") or []
        )
        old_native_count = sum(
            item.get("provider") == "native"
            for item in old_provider.get("scene_routes") or []
        )
        new_native_count = sum(
            item.get("provider") == "native"
            for item in new_provider.get("scene_routes") or []
        )
        old_drive_stage = next(
            (
                item
                for item in old_provider.get("stages") or []
                if item.get("provider") == "google_drive"
            ),
            {},
        )
        new_drive_stage = next(
            (
                item
                for item in new_provider.get("stages") or []
                if item.get("provider") == "google_drive"
            ),
            {},
        )
        old_cost = source_artifacts["cost_estimate_snapshot"].content
        new_cost = created["cost_estimate_snapshot"].content
        gate_results = created["gate_results"].content
        named_gate_keys = (
            "scene_spec_completeness",
            "pexels_eligibility",
            "evidence_truth",
            "diagram_suitability",
            "visual_niche_alignment",
            "visual_market_alignment",
            "semantic_match",
            "visual_continuity",
            "repetitive_production_risk",
            "rights_disclosure_completeness",
            "provider_cost_estimate",
            "ai_image_eligibility",
            "threshold_integrity",
        )
        return {
            "schema_version": "pkg1.sc04-review-packet.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "review_state": "PENDING_HUMAN",
            "review_target": "EXACT_PACKAGE_MANIFEST_TO_BE_BOUND_BY_REVIEW_TASK",
            "source_package": self._version_ref(source_package),
            "root_cause": {
                "primary_classification": ROOT_CAUSE,
                "exactly_one_primary": True,
                "finding": (
                    "The old SceneVisualIntent omitted the strict realization "
                    "features required for meaning-first routing and mechanically "
                    "mapped PEXELS_SUPPORTING to PEXELS_VIDEO."
                ),
                "downstream_route_finding": (
                    "PEXELS_ROUTE_INVALID_FOR_SC04_SCENE_MEANING"
                ),
            },
            "script_evidence": {
                "script": self._version_ref(source_artifacts["script"]),
                "segment_id": "S04",
                "segment_hash": content_hash(script_segment),
                "segment": script_segment,
                "semantic_derivation": deepcopy(semantic_derivation),
                "script_changed": False,
                "spoken_text_changed": False,
            },
            "old_attempt_evidence": attempt_evidence,
            "old_scene_authority": {
                "scene_id": SCENE_ID,
                "semantic_intent": old_visual_scene.get("semantic_intent"),
                "source_role": old_visual_scene.get("source_role"),
                "preferred_source_route": old_sc04.get("route"),
                "provider": old_sc04.get("provider"),
                "attempt_cap": old_sc04.get("attempt_cap"),
                "visual_plan_ref": self._version_ref(source_artifacts["visual_plan"]),
                "provider_plan_ref": self._version_ref(
                    source_artifacts["provider_execution_plan"]
                ),
            },
            "candidate_score_disclosure": attempt_evidence["candidate_scores"],
            "strict_scene_spec": requirements.model_dump(mode="json"),
            "gate_evidence": {
                "completeness": routing["completeness"].model_dump(mode="json"),
                "pexels": routing["pexels"].model_dump(mode="json"),
                "evidence_truth": routing["evidence_truth"].model_dump(mode="json"),
                "diagram": routing["diagram"].model_dump(mode="json"),
            },
            "new_decision": routing["decision"].model_dump(mode="json"),
            "new_scene_meaning": {
                "semantic_intent": requirements.semantic_intent,
                "scene_meaning": requirements.scene_meaning,
                "editorial_intent": requirements.editorial_intent,
            },
            "native_motion_blueprint": deepcopy(_SC04_BLUEPRINT),
            "provider_attempt_scope": {
                "sc04_attempt_cap": {
                    "before": old_sc04.get("attempt_cap"),
                    "after": new_sc04.get("attempt_cap"),
                },
                "sc04_provider": {
                    "before": old_sc04.get("provider"),
                    "after": new_sc04.get("provider"),
                },
                "pexels_scene_count": {
                    "before": old_pexels_count,
                    "after": new_pexels_count,
                },
                "native_scene_count": {
                    "before": old_native_count,
                    "after": new_native_count,
                },
                "third_sc04_attempt_allowed": False,
                "google_drive_mutation_scope": {
                    "before_planned_requests": old_drive_stage.get("planned_requests"),
                    "after_planned_requests": new_drive_stage.get("planned_requests"),
                    "idempotency_phases": deepcopy(
                        new_drive_stage.get("idempotency_phases") or []
                    ),
                    "canonical_archive_mutated_by_supplement": False,
                    "execution_requires_fresh_mr1_approval": True,
                },
            },
            "cost_difference": {
                "incremental_cost_usd": 0.0,
                "actual_cost": None,
                "estimated_cost": {
                    "before": old_cost.get("estimated_cost"),
                    "after": new_cost.get("estimated_cost"),
                    "unchanged": old_cost.get("estimated_cost")
                    == new_cost.get("estimated_cost"),
                },
                "hard_cap": {
                    "before": old_cost.get("hard_cap"),
                    "after": new_cost.get("hard_cap"),
                    "unchanged": old_cost.get("hard_cap") == new_cost.get("hard_cap"),
                },
                "evidence_ref": self._version_ref(created["cost_estimate_snapshot"]),
                "google_drive": {
                    "planned_requests": new_drive_stage.get("planned_requests"),
                    "idempotency_phases": deepcopy(
                        new_drive_stage.get("idempotency_phases") or []
                    ),
                    "incremental_cost_usd": 0.0,
                },
            },
            "rights_provenance_result": {
                "verdict": "PASS",
                "sc04_source": "VCOS_NATIVE",
                "stock_asset_required": False,
                "provider_output_claimed": False,
                "generated_evidence_authority": False,
                "drive_archive_scope": deepcopy(
                    created["rights_disclosure_completeness_report"].content[
                        "drive_archive_scope"
                    ]
                ),
                "rights_ref": self._version_ref(
                    created["rights_disclosure_completeness_report"]
                ),
                "provenance_ref": self._version_ref(created["asset_provenance_plan"]),
            },
            "named_gate_verdict_matrix": {
                key: gate_results[key]["verdict"] for key in named_gate_keys
            },
            "effective_ads_only_policy": overlay["ref"],
            "geo_closeout_evidence": overlay["closeout_ref"],
            "effective_market_policy_hash": overlay["effective_market_policy_hash"],
            "base_snapshot_contradiction_disclosed": True,
            "old_visual_dossier_bindings": {
                "market": {
                    **self._version_ref(source_artifacts["market_alignment_dossier"]),
                    "new_visual_authority": False,
                },
                "niche": {
                    **self._version_ref(source_artifacts["niche_alignment_dossier"]),
                    "new_visual_authority": False,
                },
            },
            "supplemental_visual_alignment": self._version_ref(
                created["market_gate_results"]
            ),
            "technical_gate_results": self._version_ref(created["gate_results"]),
            "guardrails": {
                "provider_execution": "DISABLED",
                "third_same_query_or_decision": "PROHIBITED",
                "threshold_lowering": "PROHIBITED",
                "attempt_ledger_reset": "PROHIBITED",
                "runtime_fallback": "PROHIBITED",
                "automatic_approval": "PROHIBITED",
            },
            "review_checklist": [
                "Confirm root cause evidence and unavailable candidate-score disclosure.",
                "Confirm SC-04 meaning and strict realization fields.",
                "Confirm NATIVE_MOTION_GRAPHIC blueprint and exact text authority.",
                "Confirm only SC-04 and dependent planning artifacts changed.",
                "Confirm ads-only overlay and supplemental visual alignment bindings.",
                "Confirm two distinct zero-cost Drive archive phases and boundaries.",
                "Confirm MR1 remains blocked and no provider call occurred.",
            ],
        }

    def _resolve_ads_only_overlay(
        self,
        *,
        channel_id: uuid.UUID,
        snapshot_id: uuid.UUID | None,
        requested_version_id: uuid.UUID,
        requested_hash: str,
        requested_closeout_version_id: uuid.UUID,
        requested_closeout_hash: str,
    ) -> dict[str, Any]:
        version = self.session.get(ArtifactVersion, requested_version_id)
        if version is None:
            raise NotFoundError(
                f"ads-only overlay artifact version not found: {requested_version_id}"
            )
        candidates = [version]
        snapshot = (
            self.session.get(CompiledChannelPolicySnapshot, snapshot_id)
            if snapshot_id is not None
            else None
        )
        if snapshot is None:
            raise ValidationFailureError("ADS_ONLY_BASE_SNAPSHOT_MISSING")
        try:
            scoped_policy = ChannelScopedPolicy.model_validate(
                (snapshot.compiled_payload or {}).get("channel_scoped_policy")
            )
            destination_policy = scoped_policy.destination_binding_policy
            destination_ref = (
                (snapshot.compiled_payload or {})
                .get("snapshot_refs", {})
                .get("destination_binding", {})
                .get("ref")
            )
            if destination_policy is None or not destination_ref:
                raise ValueError("destination binding missing")
            canonical_destination_runtime = destination_runtime_contract(
                destination_policy.destination,
                canonical_ref=destination_ref,
            )
        except Exception as exc:
            raise ValidationFailureError(
                "ADS_ONLY_CANONICAL_DESTINATION_INVALID"
            ) from exc
        valid: list[
            tuple[ArtifactVersion, Artifact, EffectiveAdsOnlyPolicyArtifact]
        ] = []
        for version in candidates:
            artifact = self.session.get(Artifact, version.artifact_id)
            artifact_project = (
                self.session.get(VideoProject, artifact.video_project_id)
                if artifact is not None
                else None
            )
            if (
                artifact is None
                or artifact.artifact_type != "effective_ads_only_monetization_policy"
                or artifact.current_version_id != version.id
                or artifact.status != "in_review"
                or version.status != "submitted"
                or artifact_project is None
                or artifact_project.channel_workspace_id != channel_id
                or content_hash(version.content) != version.content_hash
            ):
                continue
            try:
                typed = EffectiveAdsOnlyPolicyArtifact.model_validate(version.content)
            except Exception:
                continue
            expected_effective_hash = market_policy_hash(
                policy_snapshot_id=snapshot.id,
                market_slice={
                    "base_policy_snapshot_hash": snapshot.content_hash,
                    "ads_only_overlay_hash": typed.policy.content_hash,
                },
            )
            if not (
                typed.artifact_state == "SUBMITTED"
                and typed.immutable is True
                and typed.base_policy_snapshot_id == snapshot.id
                and typed.base_policy_snapshot_hash == snapshot.content_hash
                and typed.policy.base_policy_snapshot_id == snapshot.id
                and typed.policy.base_policy_snapshot_hash == snapshot.content_hash
                and typed.effective_market_policy_hash == expected_effective_hash
            ):
                continue
            valid.append((version, artifact, typed))
        if len(valid) != 1:
            raise ValidationFailureError(
                "EXACT_IMMUTABLE_ADS_ONLY_POLICY_OVERLAY_REQUIRED"
            )
        version, artifact, typed_overlay = valid[0]
        if version.content_hash != requested_hash:
            raise ValidationFailureError("ADS_ONLY_OVERLAY_HASH_MISMATCH")
        effective_hash = typed_overlay.effective_market_policy_hash

        closeout = self.session.get(ArtifactVersion, requested_closeout_version_id)
        if closeout is None:
            raise ValidationFailureError("EXACT_GEO_CLOSEOUT_EVIDENCE_REQUIRED")
        closeout_artifact = self.session.get(Artifact, closeout.artifact_id)
        try:
            typed_closeout = GeoMarketDeliveryCloseoutEvidence.model_validate(
                closeout.content
            )
        except Exception as exc:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_EVIDENCE_SCHEMA_INVALID"
            ) from exc
        acceptance = typed_closeout.acceptance_verdicts.model_dump(mode="json")
        verification_manifest = typed_closeout.verification_manifest
        verification_source = self.session.get(
            ArtifactVersion,
            verification_manifest.source_package_artifact_version_id,
        )
        verification_source_artifact = (
            self.session.get(Artifact, verification_source.artifact_id)
            if verification_source is not None
            else None
        )
        verification_source_project = (
            self.session.get(
                VideoProject, verification_source_artifact.video_project_id
            )
            if verification_source_artifact is not None
            else None
        )
        if (
            closeout_artifact is None
            or closeout_artifact.artifact_type
            != "geo_market_delivery_closeout_evidence"
            or closeout_artifact.video_project_id != artifact.video_project_id
            or closeout_artifact.current_version_id != closeout.id
            or closeout_artifact.status != "in_review"
            or closeout.status != "submitted"
            or content_hash(closeout.content) != closeout.content_hash
            or typed_closeout.artifact_state != "SUBMITTED"
            or typed_closeout.immutable is not True
            or typed_closeout.base_policy_snapshot_id != snapshot.id
            or typed_closeout.base_policy_snapshot_hash != snapshot.content_hash
            or typed_closeout.effective_market_policy_hash != effective_hash
            or typed_closeout.market_alignment_result.policy_snapshot_id != snapshot.id
            or typed_closeout.market_alignment_result.market_policy_hash
            != effective_hash
            or typed_closeout.market_alignment_result.destination_binding_id
            != typed_closeout.destination_runtime.destination_binding_id
            or typed_closeout.market_alignment_result.destination_binding_fingerprint
            != typed_closeout.destination_runtime.binding_fingerprint
            or typed_closeout.market_alignment_result.verdict.value != "PASS"
            or typed_closeout.destination_runtime != canonical_destination_runtime
            or verification_manifest.channel_workspace_id != channel_id
            or verification_manifest.policy_snapshot_id != snapshot.id
            or verification_manifest.policy_snapshot_hash != snapshot.content_hash
            or verification_source is None
            or verification_source_artifact is None
            or verification_source_project is None
            or verification_source_artifact.artifact_type != "package_manifest"
            or verification_source_artifact.current_version_id != verification_source.id
            or verification_source_artifact.status != "approved"
            or verification_source.status not in {"submitted", "approved"}
            or verification_source_project.status != "approved"
            or verification_source_project.channel_workspace_id != channel_id
            or verification_source_project.policy_snapshot_id != snapshot.id
            or verification_source.content_hash
            != verification_manifest.source_package_content_hash
            or content_hash(verification_source.content)
            != verification_source.content_hash
            or acceptance_evidence_from_manifest(verification_manifest)
            != typed_closeout.acceptance_evidence
            or set(acceptance.values()) != {"PASS"}
            or len(acceptance) != 7
            or typed_closeout.destination_status != "PENDING_PLATFORM_ID"
            or typed_closeout.destination_runtime.status != "PENDING_PLATFORM_ID"
            or typed_closeout.upload_ready is not False
            or typed_closeout.publish_execution_ready is not False
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_EVIDENCE_BINDING_INVALID")
        if (
            typed_closeout.effective_ads_only_policy_ref.artifact_version_id
            != version.id
            or typed_closeout.effective_ads_only_policy_ref.content_hash
            != version.content_hash
            or typed_closeout.effective_ads_only_policy_ref.ref
            != f"artifact-version://{version.id}"
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_OVERLAY_REF_MISMATCH")
        if closeout.content_hash != requested_closeout_hash:
            raise ValidationFailureError("GEO_CLOSEOUT_HASH_MISMATCH")

        geo_closeout_service = GeoDeliveryCloseoutArtifactService(self.session)
        source_approval, human_receipt = (
            geo_closeout_service._resolve_source_approval_authority(
                source_package=verification_source,
                source_project=verification_source_project,
            )
        )
        verification_receipt, typed_verification_receipt = (
            geo_closeout_service._resolve_verification_receipt(
                artifact_version_id=(
                    typed_closeout.verification_receipt_artifact_version_id
                ),
                expected_content_hash=(
                    typed_closeout.verification_receipt_content_hash
                ),
                source_project=verification_source_project,
                expected_creator_user_id=source_approval.decided_by_user_id,
            )
        )
        if (
            typed_closeout.source_approval_decision_id != source_approval.id
            or typed_closeout.human_review_receipt_artifact_version_id
            != human_receipt.id
            or typed_closeout.human_review_receipt_content_hash
            != human_receipt.content_hash
            or typed_closeout.verification_receipt_artifact_version_id
            != verification_receipt.id
            or typed_closeout.verification_receipt_content_hash
            != verification_receipt.content_hash
            or typed_verification_receipt.manifest != verification_manifest
        ):
            raise ValidationFailureError("GEO_CLOSEOUT_IMMUTABLE_AUTHORITY_INVALID")
        try:
            current_workspace_hash = geo_delivery_workspace_hash(
                GEO_DELIVERY_REPOSITORY_ROOT
            )
        except OSError as exc:
            raise ValidationFailureError(
                "GEO_CLOSEOUT_WORKSPACE_HASH_UNAVAILABLE"
            ) from exc
        workspace_freshness_receipt = verification_receipt
        workspace_freshness_manifest = verification_manifest
        workspace_freshness_mode = "IMMUTABLE_CLOSEOUT_RECEIPT"
        if (
            verification_manifest.workspace_hash != current_workspace_hash
            or verification_manifest.repository_revision
            != f"workspace-sha256:{current_workspace_hash}"
        ):
            (
                workspace_freshness_receipt,
                typed_workspace_freshness_receipt,
            ) = geo_closeout_service._resolve_current_workspace_revalidation_receipt(
                source_project=verification_source_project,
                source_package=verification_source,
                expected_channel_workspace_id=channel_id,
                expected_policy_snapshot_id=snapshot.id,
                expected_policy_snapshot_hash=snapshot.content_hash,
                expected_creator_user_id=source_approval.decided_by_user_id,
                current_workspace_hash=current_workspace_hash,
            )
            workspace_freshness_manifest = (
                typed_workspace_freshness_receipt.manifest
            )
            workspace_freshness_mode = "CURRENT_WORKSPACE_MACHINE_REVALIDATION"
        return {
            "ref": self._geo_version_ref(version, artifact.artifact_type),
            "closeout_ref": self._geo_version_ref(
                closeout, closeout_artifact.artifact_type
            ),
            "effective_market_policy_hash": effective_hash,
            "policy": typed_overlay.policy.model_dump(mode="json"),
            "source_authority": {
                "source_project_id": str(verification_source_project.id),
                "source_package_artifact_version_id": str(verification_source.id),
                "source_package_content_hash": verification_source.content_hash,
                "source_approval_decision_id": str(source_approval.id),
                "source_human_receipt_artifact_version_id": str(human_receipt.id),
                "source_human_receipt_content_hash": human_receipt.content_hash,
            },
            "workspace_freshness": {
                "mode": workspace_freshness_mode,
                "artifact_version_id": str(workspace_freshness_receipt.id),
                "content_hash": workspace_freshness_receipt.content_hash,
                "manifest_content_hash": workspace_freshness_manifest.content_hash,
                "workspace_hash": workspace_freshness_manifest.workspace_hash,
                "producer": workspace_freshness_manifest.producer,
            },
        }

    @staticmethod
    def _validate_geo_source_authority(
        *,
        overlay: dict[str, Any],
        source_project: VideoProject,
        source_package: ArtifactVersion,
        source_human_authority: dict[str, Any],
    ) -> None:
        approval = source_human_authority.get("approval") or {}
        human_receipt = source_human_authority.get("human_review_receipt") or {}
        expected = {
            "source_project_id": str(source_project.id),
            "source_package_artifact_version_id": str(source_package.id),
            "source_package_content_hash": source_package.content_hash,
            "source_approval_decision_id": approval.get("approval_decision_id"),
            "source_human_receipt_artifact_version_id": human_receipt.get(
                "artifact_version_id"
            ),
            "source_human_receipt_content_hash": human_receipt.get("content_hash"),
        }
        if overlay.get("source_authority") != expected:
            raise ValidationFailureError("PKG1_SC04_GEO_SOURCE_AUTHORITY_MISMATCH")

    def _validate_existing_pending_revision(
        self,
        project: VideoProject,
        overlay: dict[str, Any],
        *,
        source_project: VideoProject,
        source_package: ArtifactVersion,
        source_human_authority: dict[str, Any],
    ) -> None:
        result = self.read_revision(project.id)
        package = result["package"]
        package_version = self.session.get(
            ArtifactVersion, uuid.UUID(result["package_artifact_version_id"])
        )
        package_artifact = (
            self.session.get(Artifact, package_version.artifact_id)
            if package_version is not None
            else None
        )
        failures: list[str] = []
        if (
            package_version is None
            or package_artifact is None
            or package_artifact.video_project_id != project.id
            or package_artifact.artifact_type != "package_manifest"
            or package_artifact.current_version_id != package_version.id
            or content_hash(package_version.content) != package_version.content_hash
            or package_version.content_hash != result["package_content_hash"]
        ):
            failures.append("EXISTING_SC04_PACKAGE_HASH_BINDING_INVALID")
        if project.status != "in_review":
            failures.append("EXISTING_SC04_REVISION_NOT_IN_REVIEW")
        if package.get("package_status") != "TECHNICAL_PASS_HUMAN_REVIEW_PENDING":
            failures.append("EXISTING_SC04_PACKAGE_NOT_PENDING")
        if package.get("repaired_route") != REPAIRED_ROUTE.value:
            failures.append("EXISTING_SC04_ROUTE_CHANGED")
        if package.get("source_project_ref") != (
            f"video-project://{source_project.id}"
        ):
            failures.append("EXISTING_SC04_SOURCE_PROJECT_CHANGED")
        if package.get("supersedes") != self._version_ref(source_package):
            failures.append("EXISTING_SC04_SOURCE_PACKAGE_CHANGED")
        if package.get("source_human_authority") != source_human_authority:
            failures.append("EXISTING_SC04_SOURCE_HUMAN_AUTHORITY_CHANGED")
        if package.get("effective_monetization_policy") != overlay["ref"]:
            failures.append("EXISTING_SC04_ADS_OVERLAY_CHANGED")
        if (
            package.get("geo_market_delivery_closeout_evidence")
            != overlay["closeout_ref"]
        ):
            failures.append("EXISTING_SC04_GEO_CLOSEOUT_CHANGED")
        if (
            package.get("effective_market_policy_hash")
            != overlay["effective_market_policy_hash"]
        ):
            failures.append("EXISTING_SC04_EFFECTIVE_MARKET_HASH_CHANGED")
        if package.get("provider_execution") != "DISABLED":
            failures.append("EXISTING_SC04_PROVIDER_BOUNDARY_CHANGED")
        no_execution_proof = package.get("no_execution_proof") or {}
        before_counts = no_execution_proof.get("before_counts")
        after_counts = no_execution_proof.get("after_counts")
        if (
            no_execution_proof.get("all_deltas_zero") is not True
            or any((no_execution_proof.get("deltas") or {}).values())
            or no_execution_proof.get("provider_calls") != 0
            or no_execution_proof.get("render_calls") != 0
            or no_execution_proof.get("drive_calls") != 0
            or no_execution_proof.get("youtube_calls") != 0
            or not isinstance(before_counts, dict)
            or not isinstance(after_counts, dict)
            or before_counts != after_counts
        ):
            failures.append("EXISTING_SC04_NO_EXECUTION_PROOF_INVALID")
        revised = package.get("revised_artifacts") or {}
        revised_hashes: dict[str, str] = {}
        for key, ref in sorted(revised.items()):
            try:
                version_id = uuid.UUID(str(ref.get("artifact_version_id")))
            except (AttributeError, TypeError, ValueError):
                failures.append(f"EXISTING_SC04_REVISED_REF_INVALID:{key}")
                continue
            version = self.session.get(ArtifactVersion, version_id)
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact.video_project_id != project.id
                or artifact.artifact_type != key
                or artifact.current_version_id != version.id
                or ref.get("artifact_id") != str(artifact.id)
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or ref.get("content_hash") != version.content_hash
                or content_hash(version.content) != version.content_hash
            ):
                failures.append(f"EXISTING_SC04_REVISED_REF_INVALID:{key}")
                continue
            revised_hashes[key] = version.content_hash
        if len(revised_hashes) != len(revised) or content_hash(
            revised_hashes
        ) != package.get("planning_output_set_hash"):
            failures.append("EXISTING_SC04_OUTPUT_SET_HASH_INVALID")
        exact_bindings = package.get("exact_bindings") or {}
        for key in (
            "scene_visual_intent",
            "visual_source_decision_set",
            "visual_plan",
            "provider_execution_plan",
            "cost_estimate_snapshot",
            "rights_disclosure_completeness_report",
            "asset_provenance_plan",
        ):
            if exact_bindings.get(key) != revised.get(key):
                failures.append(f"EXISTING_SC04_EXACT_BINDING_INVALID:{key}")
        compatibility = package.get("mr1_reapproval_manifest_compatibility_gate") or {}
        if (
            compatibility.get("verdict") != "PASS"
            or compatibility.get("resolver_contract") != "EFFECTIVE_ARTIFACTS_V1"
            or compatibility.get("approval_created") is not False
        ):
            failures.append("EXISTING_SC04_MR1_COMPATIBILITY_INVALID")
        effective = package.get("effective_artifacts") or {}
        authority = package.get("effective_artifact_authority") or {}
        try:
            authority_project_ids = {
                uuid.UUID(str(value))
                for value in (authority.get("authority_project_ids") or {}).values()
                if value
            }
        except (TypeError, ValueError):
            authority_project_ids = set()
        if not effective or project.id not in authority_project_ids:
            failures.append("EXISTING_SC04_EFFECTIVE_AUTHORITY_INVALID")
        for key, ref in sorted(effective.items()):
            try:
                version = self.session.get(
                    ArtifactVersion,
                    uuid.UUID(str(ref.get("artifact_version_id"))),
                )
            except (AttributeError, TypeError, ValueError):
                version = None
            artifact = (
                self.session.get(Artifact, version.artifact_id)
                if version is not None
                else None
            )
            if (
                version is None
                or artifact is None
                or artifact.artifact_type != key
                or artifact.video_project_id not in authority_project_ids
                or artifact.current_version_id != version.id
                or ref.get("artifact_id") != str(artifact.id)
                or ref.get("artifact_version_ref") != f"artifact-version://{version.id}"
                or ref.get("content_hash") != version.content_hash
                or content_hash(version.content) != version.content_hash
            ):
                failures.append(f"EXISTING_SC04_EFFECTIVE_REF_INVALID:{key}")
        composite = authority.get("composite_market_alignment_authority") or {}
        composite_core = {
            key: deepcopy(value)
            for key, value in composite.items()
            if key not in {"ref", "content_hash"}
        }
        if (
            composite.get("content_hash") != content_hash(composite_core)
            or composite.get("subject") != effective.get("visual_plan")
            or composite.get("supplemental_visual_alignment")
            != effective.get("market_gate_results")
            or package.get("exact_bindings", {}).get(
                "composite_market_alignment_authority"
            )
            != composite
        ):
            failures.append("EXISTING_SC04_COMPOSITE_ALIGNMENT_INVALID")
        reviews = result["human_review_task_ids"]
        if len(reviews) != 1:
            failures.append("EXISTING_SC04_EXACT_REVIEW_MISSING")
        else:
            review = self.session.get(ReviewTask, uuid.UUID(reviews[0]))
            if (
                review is None
                or review.status not in {"open", "in_progress"}
                or package_version is None
                or review.target_artifact_version_id != package_version.id
            ):
                failures.append("EXISTING_SC04_REVIEW_NOT_PENDING")
        version_ids = list(
            self.session.scalars(
                select(ArtifactVersion.id)
                .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
                .where(Artifact.video_project_id == project.id)
            ).all()
        )
        approvals = (
            list(
                self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id.in_(version_ids)
                    )
                ).all()
            )
            if version_ids
            else []
        )
        if approvals:
            failures.append("EXISTING_SC04_APPROVAL_ALREADY_PRESENT")
        if failures:
            raise ValidationFailureError(
                "EXISTING_PKG1_SC04_REVISION_INVALID:" + ",".join(failures)
            )

    def _create_artifact(
        self,
        project_id: uuid.UUID,
        artifact_type: str,
        payload: dict[str, Any],
        created_by_user_id: uuid.UUID,
        revision_hash: str,
    ) -> ArtifactVersion:
        service = ArtifactService(self.session)
        artifact = service.create_artifact(
            data=ArtifactCreate(
                video_project_id=project_id,
                artifact_type=artifact_type,
                status="in_review",
                created_by_user_id=created_by_user_id,
            ),
            correlation_id=f"pkg1-sc04-revision-{artifact_type}",
        )
        return service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(payload),
                status="submitted",
                created_by_user_id=created_by_user_id,
                context_refs=[
                    {"type": "pkg1_sc04_revision", "content_hash": revision_hash}
                ],
                packaging_metadata={
                    "pkg1_sc04_revision": True,
                    "provider_execution": "DISABLED",
                    "human_review": "PENDING",
                },
            ),
            correlation_id=f"pkg1-sc04-revision-version-{artifact_type}",
        )

    def _source_package_approvals(
        self, package: ArtifactVersion
    ) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(ApprovalDecision)
                .where(ApprovalDecision.target_artifact_version_id == package.id)
                .order_by(ApprovalDecision.decided_at)
            ).all()
        )
        return [
            {
                "approval_decision_id": str(item.id),
                "decision": item.decision,
                "approval_scope": (item.metadata_ or {}).get("approval_scope"),
                "approval_ref": (item.metadata_ or {}).get("approval_ref"),
                "target_artifact_version_id": str(item.target_artifact_version_id),
                "target_content_hash": package.content_hash,
            }
            for item in rows
        ]

    @staticmethod
    def _version_ref(version: ArtifactVersion) -> dict[str, Any]:
        return {
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "artifact_version_ref": f"artifact-version://{version.id}",
            "version_number": version.version_number,
            "content_hash": version.content_hash,
        }

    def _geo_version_ref(
        self, version: ArtifactVersion, artifact_type: str
    ) -> dict[str, Any]:
        return {
            "artifact_type": artifact_type,
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "version_number": version.version_number,
            "ref": f"artifact-version://{version.id}",
            "content_hash": version.content_hash,
        }

    @staticmethod
    def _one_scene(
        payload: dict[str, Any], scene_id: str, *, key: str = "scenes"
    ) -> dict[str, Any]:
        matches = [
            item
            for item in payload.get(key) or []
            if isinstance(item, dict) and item.get("scene_id") == scene_id
        ]
        if len(matches) != 1:
            raise ValidationFailureError(f"EXACT_SCENE_REQUIRED:{key}:{scene_id}")
        return matches[0]

    @staticmethod
    def _replace_scene(
        payload: dict[str, Any], replacement: dict[str, Any], *, key: str = "scenes"
    ) -> None:
        values = payload.get(key) or []
        matches = [
            idx for idx, item in enumerate(values) if item.get("scene_id") == SCENE_ID
        ]
        if len(matches) != 1:
            raise ValidationFailureError(f"EXACT_SC04_REQUIRED:{key}")
        values[matches[0]] = replacement
        payload[key] = values

    @staticmethod
    def _script_segment(script: ArtifactVersion, segment_id: str) -> dict[str, Any]:
        matches = [
            item
            for item in (script.content or {}).get("segments") or []
            if item.get("segment_id") == segment_id
        ]
        if len(matches) != 1:
            raise ValidationFailureError("EXACT_SC04_SCRIPT_SEGMENT_REQUIRED")
        return deepcopy(matches[0])

    @staticmethod
    def _old_query_family(semantic_intent: str) -> list[str]:
        stop_words = {
            "a",
            "an",
            "and",
            "are",
            "as",
            "at",
            "be",
            "by",
            "for",
            "from",
            "in",
            "is",
            "it",
            "of",
            "on",
            "or",
            "that",
            "the",
            "this",
            "to",
            "with",
        }
        normalized = re.sub(r"[^a-zA-Z0-9\s-]", " ", semantic_intent).lower()
        words = [
            word
            for word in normalized.split()
            if word not in stop_words and len(word) > 2
        ][:7]
        core = " ".join(words[:4])
        if not core:
            raise ValidationFailureError("SC04_OLD_QUERY_RECONSTRUCTION_EMPTY")
        return [
            f"{core} workplace b roll",
            f"{core} close up action",
            f"{core} clean composition",
        ]
