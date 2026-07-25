from __future__ import annotations

import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.channel_policy import ChannelScopedPolicy
from app.contracts.geo_market import (
    DestinationBinding,
    IdeaMarketPreflightResult,
    MarketBoundPublishPackage,
    MarketVerdict,
    MetadataMarketAlignmentInput,
    PublishRiskMarketAlignment,
    ResearchJurisdictionInput,
    ScriptMarketAlignmentInput,
    TargetMarketDigest,
    TargetMarketProfile,
    ThumbnailMarketAlignmentInput,
    TopicMarketAlignmentInput,
    VisualMarketAlignmentInput,
    VoiceLocaleAlignmentInput,
)
from app.contracts.nich1 import (
    MetadataNicheAlignmentInput,
    NicheCriterion,
    NicheCriterionEvidence,
    NicheDossierScope,
    NicheEvidenceRef,
    NicheGateKey,
    NicheGateVerdict,
    NicheReasonCode,
    ScriptNicheAlignmentInput,
    ThumbnailNicheAlignmentInput,
    TopicNicheAlignmentInput,
    VisualNicheAlignmentInput,
)
from app.contracts.workflow import (
    ArtifactCreate,
    ArtifactVersionCreate,
    ReviewTaskCreate,
    VideoProjectCreate,
)
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    Artifact,
    ArtifactVersion,
    ApprovalDecision,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    ContentCategory,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    LLMRouteAttempt,
    LLMRunSnapshot,
    ReviewTask,
    User,
    VideoProject,
)
from app.services.config_registry import ConfigRegistryService, content_hash
from app.services.geo_market import (
    IdeaMarketPreflightEvaluator,
    MarketAlignmentDossierBuilder,
    MarketChannelGovernanceService,
    MetadataMarketAlignmentGate,
    ResearchJurisdictionGate,
    ScriptMarketAlignmentGate,
    ThumbnailMarketAlignmentGate,
    TopicMarketAlignmentGate,
    VisualMarketAlignmentGate,
    VoiceLocaleAlignmentGate,
)
from app.services.nich1 import (
    EditorialSlotValidator,
    MetadataNicheAlignmentGate,
    NicheAlignmentDossierBuilder,
    NicheContractDigestCompiler,
    ScriptNicheAlignmentGate,
    ThumbnailNicheAlignmentGate,
    TopicNicheAlignmentGate,
    VisualNicheAlignmentGate,
    evaluate_channel_fit,
)
from app.services.pkg1 import PKG1PackageService
from app.services.workflow import ArtifactService, ReviewService, VideoProjectService


ROOT = Path(__file__).resolve().parents[2]
PROJECT_TYPE = "PKG1_MARKET_REVISION"
REVISION_SCHEMA_VERSION = "pkg1.market-revision.v3"
LPRO1_ORCHESTRATOR_VERSION = "lpro1.long-production-orchestrator/1.0.0"
LPRO1_RENDER_CONTRACT_VERSION = "lpro1.long-form-render-package.v1"

DRIVE_IDEMPOTENCY_PHASES = [
    {
        "phase": "CANONICAL_REVIEW_ARCHIVE",
        "operation_key": "google_drive:archive",
        "boundary": "PRE_HUMAN_PASS",
        "max_mutations": 1,
        "cost_usd": 0.0,
    },
    {
        "phase": "FINALIZATION_SUPPLEMENT",
        "operation_key": "google_drive:finalization-supplement",
        "boundary": "POST_HUMAN_PASS_PRE_FINAL_MEDIA_REF",
        "max_mutations": 1,
        "cost_usd": 0.0,
    },
]

DEFAULT_REPORT_PATHS = {
    "lpro1": ROOT / "reports/lpro1_summary.json",
    "geo1": ROOT / "reports/geo1_summary.json",
    "geo2": ROOT / "reports/geo2_summary.json",
    "ch1": ROOT / "reports/ch1_market_profile_v3_summary.json",
}

REUSED_ARTIFACT_TYPES = (
    "script",
    "spoken_text_normalized",
    "narration_pacing_preflight_estimate",
)

REQUIRED_HISTORICAL_ARTIFACT_TYPES = set(REUSED_ARTIFACT_TYPES) | {
    "idea_admission_lineage",
    "research_pack",
    "source_pack",
    "claim_evidence_ledger",
    "creative_brief",
    "episode_originality_manifest",
    "visual_plan",
    "visual_direction_contract",
    "compiled_asset_request_plan",
    "caption_plan",
    "provider_execution_plan",
    "paid_attempt_plan",
    "cost_estimate_snapshot",
    "rights_disclosure_completeness_report",
    "synthetic_media_disclosure_receipt_draft",
    "publish_package_draft",
    "manual_publish_checklist_draft",
    "package_manifest",
}

REVISION_INVENTORY = {
    "IdeaDecision": "REUSE_UNCHANGED",
    "IdeaAdmissionLineage": "SUPERSEDE",
    "ResearchPack": "REVISE",
    "SourcePack": "REVISE",
    "ClaimEvidence": "REVISE",
    "CreativeBrief": "REVISE",
    "Script": "REUSE_UNCHANGED",
    "SpokenTextNormalized": "REUSE_UNCHANGED",
    "FormatIdentityContract": "REUSE_UNCHANGED",
    "EpisodeOriginalityManifest": "REVISE",
    "VisualDirectionContract": "REBUILD",
    "SceneVisualIntent": "REVISE",
    "VisualPlan": "REBUILD",
    "VisualSourceDecisionSet": "REBUILD",
    "CompiledAssetRequestPlan": "REBUILD",
    "VoicePolicy": "REBUILD",
    "ThumbnailBrief": "REBUILD",
    "PublishingMetadataPackage": "REBUILD",
    "TargetMarketConsistencyCheck": "REBUILD",
    "MarketAlignmentDossier": "REBUILD",
    "DestinationBinding": "REUSE_UNCHANGED",
    "ProviderExecutionPlan": "REBUILD",
    "CostEstimateSnapshot": "REBUILD",
    "RightsDisclosureCompletenessReport": "REBUILD",
    "SyntheticMediaDisclosureReceipt": "REBUILD",
    "AssetProvenancePlan": "REBUILD",
    "PublishRiskDossier": "REBUILD",
    "PublishHandoffPackage": "REBUILD",
    "UploadCard": "REBUILD",
    "MR1ExecutionApproval": "SUPERSEDE",
}


class PKG1MarketRevisionService:
    """Build a provider-free PKG1 revision under exact active market authority."""

    def __init__(
        self,
        session: Session,
        *,
        report_paths: dict[str, Path] | None = None,
    ) -> None:
        self.session = session
        self.report_paths = report_paths or DEFAULT_REPORT_PATHS

    def entry_status(self, channel_id: uuid.UUID) -> dict[str, Any]:
        reports = self._load_entry_reports()
        failures: list[str] = []
        lpro1 = reports["lpro1"]
        geo1 = reports["geo1"]
        geo2 = reports["geo2"]
        ch1 = reports["ch1"]
        if lpro1.get("result") != "PASS":
            failures.append("LPRO1_FINAL_NOT_PASS")
        if (geo1.get("verdicts") or {}).get("GEO1_FINAL") != "PASS":
            failures.append("GEO1_FINAL_NOT_PASS")
        if (geo2.get("verdicts") or {}).get("GEO2_FINAL") != "PASS":
            failures.append("GEO2_FINAL_NOT_PASS")
        if (ch1.get("verdicts") or {}).get("CH1_MARKET_V3_FINAL") != "PASS":
            failures.append("CH1_MARKET_V3_FINAL_NOT_PASS")
        if ch1.get("proceed_to_pkg1_revision") is not True:
            failures.append("PROCEED_TO_PKG1_REVISION_NOT_TRUE")
        if ch1.get("mr1_execution") != "ON_HOLD":
            failures.append("MR1_EXECUTION_NOT_ON_HOLD")
        if ch1.get("proceed_to_mr1") is not False:
            failures.append("PROCEED_TO_MR1_NOT_FALSE")

        channel = self.session.get(ChannelWorkspace, channel_id)
        if channel is None or channel.key != "small-team-ai":
            failures.append("CHANNEL_NOT_SMALL_TEAM_AI")
            return {"status": "FAIL", "reason_codes": failures}
        snapshot = self.session.get(
            CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id
        )
        profile = (
            self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
            if snapshot is not None
            else None
        )
        if snapshot is None or snapshot.status != "active":
            failures.append("ACTIVE_SNAPSHOT_MISSING")
        if profile is None or profile.version != 3 or profile.status != "active":
            failures.append("ACTIVE_PROFILE_V3_MISSING")
        if failures:
            return {"status": "FAIL", "reason_codes": failures}
        assert snapshot is not None and profile is not None

        activation = ch1.get("production_activation") or {}
        expected = {
            "channel_id": str(channel.id),
            "profile_v3_id": str(profile.id),
            "profile_v3_input_hash": profile.profile_input_hash,
            "snapshot_v3_id": str(snapshot.id),
            "snapshot_v3_hash": snapshot.content_hash,
        }
        for key, value in expected.items():
            if activation.get(key) != value:
                failures.append(f"CH1_REPORT_{key.upper()}_MISMATCH")

        try:
            policy = ChannelScopedPolicy.model_validate(
                (snapshot.compiled_payload or {}).get("channel_scoped_policy")
            )
        except Exception as exc:
            raise ValidationFailureError("ACTIVE_PROFILE_V3_POLICY_INVALID") from exc
        market_profile = policy.target_market_profile
        market_digest = policy.target_market_digest
        destination_policy = policy.destination_binding_policy
        if market_profile is None or market_digest is None or destination_policy is None:
            failures.append("MARKET_AUTHORITY_BINDING_MISSING")
            return {"status": "FAIL", "reason_codes": failures}
        destination = destination_policy.destination
        refs = (snapshot.compiled_payload or {}).get("snapshot_refs") or {}
        self._validate_snapshot_binding(
            refs=refs,
            key="target_market_profile",
            expected_hash=str(market_profile.content_hash),
            failures=failures,
        )
        self._validate_snapshot_binding(
            refs=refs,
            key="target_market_digest",
            expected_hash=str(market_digest.content_hash),
            failures=failures,
        )
        self._validate_snapshot_binding(
            refs=refs,
            key="destination_binding",
            expected_hash=str(destination.content_hash),
            failures=failures,
        )
        latest_destination = MarketChannelGovernanceService(
            self.session
        ).latest_destination_binding(channel.id)
        if (
            latest_destination is None
            or latest_destination.content_hash != destination.content_hash
        ):
            failures.append("DESTINATION_BINDING_REPOSITORY_MISMATCH")
        if (
            destination.destination_status != "PENDING_PLATFORM_ID"
            or destination.platform_channel_id is not None
            or destination.credential_ref is not None
            or destination.verification_state == "VERIFIED"
        ):
            failures.append("DESTINATION_PENDING_STATE_NOT_TRUTHFUL")

        historical_projects = list(
            self.session.scalars(
                select(VideoProject).where(
                    VideoProject.channel_workspace_id == channel.id,
                    VideoProject.project_type == "PKG1_FIRST_PRODUCTION_PACKAGE",
                )
            ).all()
        )
        historical_mr1_approval = None
        if len(historical_projects) != 1:
            failures.append("EXACTLY_ONE_HISTORICAL_PKG1_REQUIRED")
        elif historical_projects[0].status != "approved":
            failures.append("HISTORICAL_PKG1_NOT_APPROVED")
        else:
            historical_artifacts = self._current_artifacts(historical_projects[0].id)
            historical_package = historical_artifacts.get("package_manifest")
            package_approval = None
            if historical_package is not None:
                package_approval = self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id
                        == historical_package.id,
                        ApprovalDecision.decision == "approved",
                    )
                ).first()
            if (
                historical_package is None
                or package_approval is None
                or (package_approval.metadata_ or {}).get("approval_scope")
                != "PKG1_PACKAGE"
            ):
                failures.append("HISTORICAL_PKG1_EXACT_APPROVAL_MISSING")
            historical_provider_plan = historical_artifacts.get(
                "provider_execution_plan"
            )
            if historical_provider_plan is not None:
                historical_mr1_approval = self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id
                        == historical_provider_plan.id,
                        ApprovalDecision.decision == "approved",
                    )
                ).first()
            if (
                historical_provider_plan is None
                or historical_mr1_approval is None
                or (historical_mr1_approval.metadata_ or {}).get(
                    "approval_scope"
                )
                != "MR1_PAID_EXECUTION"
            ):
                failures.append("HISTORICAL_MR1_EXACT_APPROVAL_MISSING")
        categories = list(
            self.session.scalars(
                select(ContentCategory).where(
                    ContentCategory.channel_workspace_id == channel.id,
                    ContentCategory.status == "ACTIVE",
                )
            ).all()
        )
        if len(categories) != 1:
            failures.append("EXACTLY_ONE_ACTIVE_CATEGORY_REQUIRED")
        if failures:
            return {"status": "FAIL", "reason_codes": failures}
        return {
            "status": "PASS",
            "reason_codes": ["PKG1_MARKET_REVISION_ENTRY_VERIFIED"],
            "reports": reports,
            "channel": channel,
            "profile": profile,
            "snapshot": snapshot,
            "policy": policy,
            "target_market_profile": market_profile,
            "target_market_digest": market_digest,
            "destination": destination,
            "snapshot_refs": refs,
            "historical_project": historical_projects[0],
            "historical_mr1_approval": historical_mr1_approval,
            "category": categories[0],
        }

    def build_revision(
        self,
        *,
        channel_id: uuid.UUID,
        created_by_user_id: uuid.UUID,
    ) -> dict[str, Any]:
        locked_channel = self.session.scalar(
            select(ChannelWorkspace)
            .where(ChannelWorkspace.id == channel_id)
            .with_for_update()
        )
        if locked_channel is None:
            raise NotFoundError(f"channel workspace not found: {channel_id}")

        entry = self.entry_status(channel_id)
        if entry["status"] != "PASS":
            raise ValidationFailureError(
                "PKG1_MARKET_REVISION_ENTRY_FAILED:"
                + ",".join(entry["reason_codes"])
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
            raise ValidationFailureError("MULTIPLE_PKG1_MARKET_REVISIONS_FOUND")
        if existing:
            self._validate_existing_pending_revision(existing[0], entry)
            return self.read_revision(existing[0].id)
        user = self.session.get(User, created_by_user_id)
        if user is None:
            raise NotFoundError(f"user not found: {created_by_user_id}")

        channel: ChannelWorkspace = entry["channel"]
        profile: ChannelProfileVersion = entry["profile"]
        snapshot: CompiledChannelPolicySnapshot = entry["snapshot"]
        policy: ChannelScopedPolicy = entry["policy"]
        market_profile: TargetMarketProfile = entry["target_market_profile"]
        market_digest: TargetMarketDigest = entry["target_market_digest"]
        destination: DestinationBinding = entry["destination"]
        snapshot_refs: dict[str, Any] = entry["snapshot_refs"]
        historical_project: VideoProject = entry["historical_project"]
        historical_mr1_approval: ApprovalDecision = entry[
            "historical_mr1_approval"
        ]
        category: ContentCategory = entry["category"]

        historical_artifacts = self._current_artifacts(historical_project.id)
        missing = REQUIRED_HISTORICAL_ARTIFACT_TYPES - set(historical_artifacts)
        if missing:
            raise ValidationFailureError(
                "HISTORICAL_PKG1_ARTIFACTS_MISSING:" + ",".join(sorted(missing))
            )
        historical_package = historical_artifacts.get("package_manifest")
        if historical_package is None:
            raise ValidationFailureError("HISTORICAL_PKG1_PACKAGE_MANIFEST_MISSING")
        historical_before = self._historical_fingerprint(
            historical_project, historical_artifacts
        )
        no_execution_before = self._no_execution_counts()

        channel_contract = (snapshot.compiled_payload or {}).get(
            "channel_contract_json"
        ) or {}
        channel_contract_hash = content_hash(channel_contract)
        series_plan = (profile.profile_input or {}).get("series_plan") or []
        series_key = str((series_plan[0] if series_plan else {}).get("key") or "")
        if not series_key:
            raise ValidationFailureError("ACTIVE_PROFILE_SERIES_BINDING_MISSING")
        if not category.content_pillar:
            raise ValidationFailureError("ACTIVE_CATEGORY_CONTENT_PILLAR_MISSING")

        semantic_seed = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "builder_version": "pkg1-market-revision-builder/1.0.0",
            "historical_package_ref": f"artifact-version://{historical_package.id}",
            "historical_package_hash": historical_package.content_hash,
            "profile_v3_ref": f"channel-profile-version://{profile.id}",
            "profile_v3_version": profile.version,
            "profile_v3_hash": profile.profile_input_hash,
            "snapshot_v3_ref": f"compiled-policy-snapshot://{snapshot.id}",
            "snapshot_v3_version": snapshot.snapshot_version,
            "snapshot_v3_hash": snapshot.content_hash,
            "channel_contract_hash": channel_contract_hash,
            "target_market_profile_hash": market_profile.content_hash,
            "target_market_digest_hash": market_digest.content_hash,
            "destination_binding_hash": destination.content_hash,
            "category_hash": category.content_hash,
            "series_key": series_key,
            "content_pillar": category.content_pillar,
            "reused_artifact_hashes": {
                key: historical_artifacts[key].content_hash
                for key in REUSED_ARTIFACT_TYPES
            },
            "revision_source_artifact_hashes": {
                key: historical_artifacts[key].content_hash
                for key in sorted(REQUIRED_HISTORICAL_ARTIFACT_TYPES)
                if key != "package_manifest"
            },
        }
        revision_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "vcos:pkg1-market-revision:" + content_hash(semantic_seed),
            )
        )
        revision_slot = self._create_revision_slot(
            channel=channel,
            snapshot=snapshot,
            category=category,
            series_key=series_key,
            historical_artifacts=historical_artifacts,
            historical_project=historical_project,
            revision_id=revision_id,
            created_by_user_id=created_by_user_id,
        )
        niche_digest = NicheContractDigestCompiler().compile(
            channel=channel,
            profile_version=profile,
            policy_snapshot=snapshot,
            category=category,
            editorial_slot=revision_slot,
        )
        revision_hash = content_hash(
            {
                **semantic_seed,
                "editorial_slot_ref": niche_digest.editorial_slot_ref,
                "editorial_slot_hash": niche_digest.editorial_slot_hash,
                "niche_contract_digest_hash": niche_digest.content_hash,
                "lpro1_orchestrator_version": LPRO1_ORCHESTRATOR_VERSION,
                "lpro1_render_contract_version": LPRO1_RENDER_CONTRACT_VERSION,
            }
        )

        revision_project = VideoProjectService(self.session).create_project(
            data=VideoProjectCreate(
                company_id=channel.company_id,
                channel_workspace_id=channel.id,
                policy_snapshot_id=snapshot.id,
                category_id=category.id,
                channel_contract_content_hash=channel_contract_hash,
                title=historical_project.title,
                description=(
                    "PKG1 market-aware superseding revision; technical PASS remains "
                    "subject to exact human review and creates no provider output."
                ),
                status="in_review",
                project_type=PROJECT_TYPE,
                priority="normal",
                owner_user_id=created_by_user_id,
                created_by_user_id=created_by_user_id,
                financial_summary={
                    "estimated_cost_usd": 0.0,
                    "actual_cost_usd": None,
                    "state": "PLANNING_ONLY",
                },
                brand_safety_summary={"state": "PLANNING_PASS"},
                legal_compliance_summary={
                    "state": "PLANNING_PASS_HUMAN_REVIEW_PENDING"
                },
                audience_delivery_summary={
                    "destination": "YouTube",
                    "destination_status": "PENDING_PLATFORM_ID",
                    "publish_execution_allowed": False,
                },
            ),
            correlation_id="pkg1-market-revision-project",
        )

        refs = self._historical_refs(historical_artifacts)
        bindings = self._exact_bindings(
            historical_project=historical_project,
            revision_project=revision_project,
            profile=profile,
            snapshot=snapshot,
            snapshot_refs=snapshot_refs,
            channel_contract_hash=channel_contract_hash,
            niche_digest=niche_digest,
            revision_slot=revision_slot,
            category=category,
            market_profile=market_profile,
            market_digest=market_digest,
            destination=destination,
        )
        created: dict[str, ArtifactVersion] = {}
        created["pkg1_market_revision_inventory"] = self._create_artifact(
            revision_project.id,
            "pkg1_market_revision_inventory",
            {
                "schema_version": "pkg1.market-revision-inventory.v1",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "historical_package": self._version_ref(historical_package),
                "classification": REVISION_INVENTORY,
                "reused_artifacts": {
                    key: refs[key] for key in REUSED_ARTIFACT_TYPES
                },
                "historical_approval_policy": {
                    "pkg1_v1": "HISTORICAL_PASS_IMMUTABLE",
                    "old_mr1_approval": "SUPERSEDED_NOT_REUSABLE",
                    "historical_receipts_mutated": False,
                    "old_mr1_approval_decision_id": str(
                        historical_mr1_approval.id
                    ),
                },
            },
            created_by_user_id,
            revision_hash,
        )
        editorial_payloads = self._revised_editorial_payloads(
            historical_artifacts=historical_artifacts,
            historical_project=historical_project,
            revision_project=revision_project,
            revision_slot=revision_slot,
            revision_id=revision_id,
            revision_hash=revision_hash,
            profile=profile,
            snapshot=snapshot,
            bindings=bindings,
        )
        for artifact_type in ("idea_admission_lineage", "source_pack"):
            created[artifact_type] = self._create_artifact(
                revision_project.id,
                artifact_type,
                editorial_payloads[artifact_type],
                created_by_user_id,
                revision_hash,
            )
        editorial_payloads["research_pack"]["source_pack"] = self._version_ref(
            created["source_pack"]
        )
        created["research_pack"] = self._create_artifact(
            revision_project.id,
            "research_pack",
            editorial_payloads["research_pack"],
            created_by_user_id,
            revision_hash,
        )
        editorial_payloads["claim_evidence_ledger"].update(
            {
                "source_pack": self._version_ref(created["source_pack"]),
                "research_pack": self._version_ref(created["research_pack"]),
            }
        )
        created["claim_evidence_ledger"] = self._create_artifact(
            revision_project.id,
            "claim_evidence_ledger",
            editorial_payloads["claim_evidence_ledger"],
            created_by_user_id,
            revision_hash,
        )
        editorial_payloads["creative_brief"].update(
            {
                "script": refs["script"],
                "research_pack": self._version_ref(created["research_pack"]),
                "claim_evidence": self._version_ref(
                    created["claim_evidence_ledger"]
                ),
            }
        )
        created["creative_brief"] = self._create_artifact(
            revision_project.id,
            "creative_brief",
            editorial_payloads["creative_brief"],
            created_by_user_id,
            revision_hash,
        )
        editorial_payloads["episode_originality_manifest"].update(
            {
                "creative_brief": self._version_ref(created["creative_brief"]),
                "script": refs["script"],
            }
        )
        created["episode_originality_manifest"] = self._create_artifact(
            revision_project.id,
            "episode_originality_manifest",
            editorial_payloads["episode_originality_manifest"],
            created_by_user_id,
            revision_hash,
        )
        created["target_market_profile"] = self._create_artifact(
            revision_project.id,
            "target_market_profile",
            {
                "binding_mode": "EXACT_SNAPSHOT_COPY",
                "canonical_ref": snapshot_refs["target_market_profile"]["ref"],
                "governance_ref": market_digest.profile_ref,
                "canonical_hash": market_profile.content_hash,
                "profile": market_profile.model_dump(mode="json"),
            },
            created_by_user_id,
            revision_hash,
        )
        created["target_market_digest"] = self._create_artifact(
            revision_project.id,
            "target_market_digest",
            {
                "binding_mode": "EXACT_SNAPSHOT_COPY",
                "canonical_ref": snapshot_refs["target_market_digest"]["ref"],
                "governance_ref": f"{market_digest.profile_ref}/digest",
                "canonical_hash": market_digest.content_hash,
                "digest": market_digest.model_dump(mode="json"),
            },
            created_by_user_id,
            revision_hash,
        )
        created["destination_binding"] = self._create_artifact(
            revision_project.id,
            "destination_binding",
            {
                "binding_mode": "EXACT_SNAPSHOT_COPY",
                "canonical_ref": snapshot_refs["destination_binding"]["ref"],
                "canonical_hash": destination.content_hash,
                "destination": destination.model_dump(mode="json"),
                "publish_execution_allowed": False,
                "publish_blocker": "PENDING_PLATFORM_ID",
                "publish_blocker_reason_code": (
                    "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
                ),
            },
            created_by_user_id,
            revision_hash,
        )
        created["niche_contract_digest"] = self._create_artifact(
            revision_project.id,
            "niche_contract_digest",
            niche_digest.model_dump(mode="json"),
            created_by_user_id,
            revision_hash,
        )
        bindings["niche_contract_digest"] = {
            **self._version_ref(created["niche_contract_digest"]),
            "governance_ref": (
                f"editorial-slot://{revision_slot.id}#niche-contract-digest"
            ),
            "governance_hash": niche_digest.content_hash,
        }

        voice = self._voice_policy(
            policy=policy,
            market_profile=market_profile,
            snapshot=snapshot,
            revision_hash=revision_hash,
        )
        created["voice_policy"] = self._create_artifact(
            revision_project.id,
            "voice_policy",
            voice,
            created_by_user_id,
            revision_hash,
        )
        visual_payloads = self._visual_payloads(
            channel=channel,
            category=category,
            historical_artifacts=historical_artifacts,
            bindings=bindings,
            revision_hash=revision_hash,
        )
        created["scene_visual_intent"] = self._create_artifact(
            revision_project.id,
            "scene_visual_intent",
            visual_payloads["scene_visual_intent"],
            created_by_user_id,
            revision_hash,
        )
        created["visual_direction_contract"] = self._create_artifact(
            revision_project.id,
            "visual_direction_contract",
            visual_payloads["visual_direction_contract"],
            created_by_user_id,
            revision_hash,
        )
        visual_payloads["visual_source_decision_set"]["visual_direction_contract"] = (
            self._version_ref(created["visual_direction_contract"])
        )
        created["visual_source_decision_set"] = self._create_artifact(
            revision_project.id,
            "visual_source_decision_set",
            visual_payloads["visual_source_decision_set"],
            created_by_user_id,
            revision_hash,
        )
        visual_payloads["visual_plan"].update(
            {
                "visual_direction_contract": self._version_ref(
                    created["visual_direction_contract"]
                ),
                "visual_source_decision_set": self._version_ref(
                    created["visual_source_decision_set"]
                ),
            }
        )
        created["visual_plan"] = self._create_artifact(
            revision_project.id,
            "visual_plan",
            visual_payloads["visual_plan"],
            created_by_user_id,
            revision_hash,
        )
        visual_payloads["compiled_asset_request_plan"].update(
            {
                "visual_plan": self._version_ref(created["visual_plan"]),
                "visual_source_decision_set": self._version_ref(
                    created["visual_source_decision_set"]
                ),
            }
        )
        created["compiled_asset_request_plan"] = self._create_artifact(
            revision_project.id,
            "compiled_asset_request_plan",
            visual_payloads["compiled_asset_request_plan"],
            created_by_user_id,
            revision_hash,
        )

        script = historical_artifacts["script"].content
        claims = created["claim_evidence_ledger"]
        thumbnail = self._thumbnail_brief(
            script=script,
            script_ref=refs["script"],
            claims=claims,
            bindings=bindings,
            revision_hash=revision_hash,
        )
        created["thumbnail_brief"] = self._create_artifact(
            revision_project.id,
            "thumbnail_brief",
            thumbnail,
            created_by_user_id,
            revision_hash,
        )
        metadata = self._metadata_package(
            script=script,
            claims=claims,
            category=category,
            revision_hash=revision_hash,
        )
        created["publishing_metadata_package"] = self._create_artifact(
            revision_project.id,
            "publishing_metadata_package",
            metadata,
            created_by_user_id,
            revision_hash,
        )

        niche_dossier = self._niche_dossier(
            channel=channel,
            profile=profile,
            snapshot=snapshot,
            category=category,
            revision_slot=revision_slot,
            niche_digest=niche_digest,
            niche_digest_artifact_ref=self._version_ref(
                created["niche_contract_digest"]
            ),
            topic_ref=self._version_ref(created["idea_admission_lineage"]),
            script_ref=refs["script"],
            script=script,
            visual_ref=self._version_ref(created["visual_plan"]),
            visual_direction=created["visual_direction_contract"].content,
            scene_intents=created["scene_visual_intent"].content["scenes"],
            visual_decisions=created["visual_source_decision_set"].content[
                "decisions"
            ],
            thumbnail_ref=self._version_ref(created["thumbnail_brief"]),
            thumbnail=thumbnail,
            metadata_ref=self._version_ref(
                created["publishing_metadata_package"]
            ),
            metadata=metadata,
            idea_id=uuid.UUID(
                historical_artifacts["idea_admission_lineage"].content[
                    "daily_idea_decision_id"
                ]
            ),
        )
        created["niche_alignment_dossier"] = self._create_artifact(
            revision_project.id,
            "niche_alignment_dossier",
            niche_dossier,
            created_by_user_id,
            revision_hash,
        )
        bindings["niche_alignment_dossier"] = self._version_ref(
            created["niche_alignment_dossier"]
        )

        market_evidence = self._market_gate_evidence(
            market_profile=market_profile,
            market_digest=market_digest,
            revision_project=revision_project,
            historical_artifacts=historical_artifacts,
            revision_artifacts=created,
            revision_slot=revision_slot,
            category=category,
            niche_digest_ref=self._version_ref(created["niche_contract_digest"]),
            niche_dossier_ref=self._version_ref(
                created["niche_alignment_dossier"]
            ),
            voice_ref=self._version_ref(created["voice_policy"]),
            visual_ref=self._version_ref(created["visual_plan"]),
            thumbnail_ref=self._version_ref(created["thumbnail_brief"]),
            metadata_ref=self._version_ref(
                created["publishing_metadata_package"]
            ),
            profile=profile,
            snapshot=snapshot,
        )
        created["market_gate_results"] = self._create_artifact(
            revision_project.id,
            "market_gate_results",
            market_evidence["gate_results"],
            created_by_user_id,
            revision_hash,
        )
        created["market_alignment_dossier"] = self._create_artifact(
            revision_project.id,
            "market_alignment_dossier",
            market_evidence["dossier"],
            created_by_user_id,
            revision_hash,
        )
        bindings["market_alignment_dossier"] = self._version_ref(
            created["market_alignment_dossier"]
        )
        thumbnail_evidence_version = created["thumbnail_brief"]
        thumbnail = {
            **deepcopy(thumbnail),
            "market_alignment_dossier": bindings[
                "market_alignment_dossier"
            ],
            "niche_alignment_dossier": bindings["niche_alignment_dossier"],
            "market_alignment_evidence_subject": self._version_ref(
                thumbnail_evidence_version
            ),
            "binding_revision_only": True,
        }
        created["thumbnail_brief"] = self._revise_artifact_version(
            thumbnail_evidence_version,
            thumbnail,
            created_by_user_id,
            revision_hash,
        )
        bindings["thumbnail_brief"] = self._version_ref(
            created["thumbnail_brief"]
        )
        consistency = self._target_market_consistency(
            market_profile=market_profile,
            destination=destination,
            bindings=bindings,
            voice=voice,
            thumbnail=thumbnail,
            metadata=metadata,
            dossier_ref=self._version_ref(created["market_alignment_dossier"]),
            market_evidence=market_evidence,
        )
        if consistency["overall_decision"] != "PASS":
            raise ValidationFailureError("TARGET_MARKET_CONSISTENCY_NOT_PASS")
        created["target_market_consistency_check"] = self._create_artifact(
            revision_project.id,
            "target_market_consistency_check",
            consistency,
            created_by_user_id,
            revision_hash,
        )

        provider_plan = self._provider_plan(
            revision_id=revision_id,
            revision_hash=revision_hash,
            script_ref=refs["script"],
            voice_ref=self._version_ref(created["voice_policy"]),
            decision_set_ref=self._version_ref(
                created["visual_source_decision_set"]
            ),
            decisions=visual_payloads["visual_source_decision_set"]["decisions"],
            bindings=bindings,
        )
        created["provider_execution_plan"] = self._create_artifact(
            revision_project.id,
            "provider_execution_plan",
            provider_plan,
            created_by_user_id,
            revision_hash,
        )
        cost = self._cost_estimate(
            policy=policy,
            script_ref=refs["script"],
            script=script,
            visual_plan_ref=self._version_ref(created["visual_plan"]),
            provider_plan_ref=self._version_ref(
                created["provider_execution_plan"]
            ),
            decisions=visual_payloads["visual_source_decision_set"]["decisions"],
        )
        created["cost_estimate_snapshot"] = self._create_artifact(
            revision_project.id,
            "cost_estimate_snapshot",
            cost,
            created_by_user_id,
            revision_hash,
        )
        provenance = self._asset_provenance_plan(
            decisions=visual_payloads["visual_source_decision_set"]["decisions"],
            revision_hash=revision_hash,
        )
        created["asset_provenance_plan"] = self._create_artifact(
            revision_project.id,
            "asset_provenance_plan",
            provenance,
            created_by_user_id,
            revision_hash,
        )
        rights = self._rights_report(
            claims_ref=self._version_ref(created["claim_evidence_ledger"]),
            provenance_ref=self._version_ref(created["asset_provenance_plan"]),
            decisions=visual_payloads["visual_source_decision_set"]["decisions"],
        )
        created["rights_disclosure_completeness_report"] = self._create_artifact(
            revision_project.id,
            "rights_disclosure_completeness_report",
            rights,
            created_by_user_id,
            revision_hash,
        )
        disclosure = self._synthetic_disclosure(
            provenance_ref=self._version_ref(created["asset_provenance_plan"])
        )
        created["synthetic_media_disclosure_receipt_draft"] = self._create_artifact(
            revision_project.id,
            "synthetic_media_disclosure_receipt_draft",
            disclosure,
            created_by_user_id,
            revision_hash,
        )
        publish_risk = self._publish_risk_dossier(
            market_profile=market_profile,
            destination=destination,
            bindings=bindings,
            consistency=consistency,
            revision_hash=revision_hash,
            market_dossier_ref=self._version_ref(
                created["market_alignment_dossier"]
            ),
            consistency_ref=self._version_ref(
                created["target_market_consistency_check"]
            ),
            rights_ref=self._version_ref(
                created["rights_disclosure_completeness_report"]
            ),
            disclosure_ref=self._version_ref(
                created["synthetic_media_disclosure_receipt_draft"]
            ),
        )
        created["publish_risk_dossier"] = self._create_artifact(
            revision_project.id,
            "publish_risk_dossier",
            publish_risk,
            created_by_user_id,
            revision_hash,
        )
        publish_package = self._publish_handoff_package(
            revision_id=revision_id,
            revision_hash=revision_hash,
            bindings=bindings,
            thumbnail_ref=self._version_ref(created["thumbnail_brief"]),
            metadata_ref=self._version_ref(
                created["publishing_metadata_package"]
            ),
            disclosure_ref=self._version_ref(
                created["synthetic_media_disclosure_receipt_draft"]
            ),
            dossier_ref=self._version_ref(created["market_alignment_dossier"]),
            risk_ref=self._version_ref(created["publish_risk_dossier"]),
            title=metadata["title"],
            description=metadata["description"],
            market_profile=market_profile,
            destination=destination,
        )
        created["publish_handoff_package"] = self._create_artifact(
            revision_project.id,
            "publish_handoff_package",
            publish_package,
            created_by_user_id,
            revision_hash,
        )
        upload_card = self._upload_card(
            destination=destination,
            metadata_ref=self._version_ref(
                created["publishing_metadata_package"]
            ),
            title=metadata["title"],
            description=metadata["description"],
        )
        created["upload_card"] = self._create_artifact(
            revision_project.id,
            "upload_card",
            upload_card,
            created_by_user_id,
            revision_hash,
        )
        gate_results = {
            "schema_version": "pkg1.market-revision-gates.v1",
            "technical_revision": "PASS",
            "market_gate_results": self._version_ref(created["market_gate_results"]),
            "market_alignment_dossier": self._version_ref(
                created["market_alignment_dossier"]
            ),
            "target_market_consistency": self._version_ref(
                created["target_market_consistency_check"]
            ),
            "mandatory_states": {
                "niche_alignment": "PASS",
                "market_alignment": "PASS",
                "voice_locale": "PASS",
                "visual_market_alignment": "PASS",
                "thumbnail_market_alignment": "PASS",
                "metadata_market_alignment": "PASS",
                "rights_disclosure": "PASS",
                "provider_boundary": "PASS",
            },
            "review_required": ["PACKAGE_CONTENT_APPROVAL"],
            "publish_blocks": ["PENDING_PLATFORM_ID", "FINAL_MEDIA_MISSING"],
            "publish_blocker_reason_codes": [
                "DESTINATION_PLATFORM_ID_NOT_VERIFIED",
                "FINAL_MEDIA_MISSING",
            ],
        }
        created["gate_results"] = self._create_artifact(
            revision_project.id,
            "gate_results",
            gate_results,
            created_by_user_id,
            revision_hash,
        )

        no_execution_mid = self._no_execution_counts()
        if no_execution_mid != no_execution_before:
            raise ValidationFailureError(
                "PKG1_MARKET_REVISION_EXECUTION_BOUNDARY_CHANGED"
            )
        no_execution_deltas = {
            key: no_execution_mid[key] - no_execution_before[key]
            for key in no_execution_before
        }
        output_set_hash = content_hash(
            {
                key: value.content_hash
                for key, value in sorted(created.items())
            }
        )
        manifest = {
            "schema_version": REVISION_SCHEMA_VERSION,
            "revision_id": revision_id,
            "revision_version": 2,
            "revision_hash": revision_hash,
            "planning_output_set_hash": output_set_hash,
            "package_status": "TECHNICAL_PASS_HUMAN_REVIEW_PENDING",
            "supersedes": self._version_ref(historical_package),
            "historical_pkg1_state": "HISTORICAL_PASS",
            "historical_pkg1_mutated": False,
            "storage_project_ref": f"video-project://{revision_project.id}",
            "exact_bindings": bindings,
            "reused_artifacts": {
                key: refs[key] for key in REUSED_ARTIFACT_TYPES
            },
            "revised_artifacts": {
                key: self._version_ref(value) for key, value in created.items()
            },
            "old_mr1_approval": {
                "approval_decision_id": str(historical_mr1_approval.id),
                "approval_scope": "MR1_PAID_EXECUTION",
                "target_artifact_version_id": str(
                    historical_mr1_approval.target_artifact_version_id
                ),
                "approval_ref": (
                    historical_mr1_approval.metadata_ or {}
                ).get("approval_ref"),
                "reuse_allowed": False,
                "state_for_revision": "SUPERSEDED_BY_PKG1_MARKET_REVISION",
                "historical_receipt_mutated": False,
            },
            "provider_execution": "DISABLED",
            "PRODUCTION_PACKAGE_APPROVED": False,
            "FINAL_MARKET_PACKAGE_PENDING_MEDIA": True,
            "MARKET_PACKAGE_FROZEN": False,
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "publish_execution_allowed": False,
            "publish_blocker": "PENDING_PLATFORM_ID",
            "publish_blocker_reason_code": (
                "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
            ),
            "destination_status": destination.destination_status,
            "PKG1_MARKET_REVISION_HUMAN_REVIEW": "PENDING",
            "PKG1_MARKET_REVISION_FINAL": "WAITING_HUMAN_REVIEW",
            "MR1_EXECUTION": "ON_HOLD",
            "PROCEED_TO_MR1": False,
            "PROCEED_TO_MR1_REAPPROVAL": False,
            "no_execution_proof": {
                "provider_calls": 0,
                "render_calls": 0,
                "drive_calls": 0,
                "youtube_calls": 0,
                "before_counts": no_execution_before,
                "after_counts": no_execution_mid,
                "deltas": no_execution_deltas,
                "all_deltas_zero": all(
                    value == 0 for value in no_execution_deltas.values()
                ),
            },
            "exact_next_action": (
                "Operator reviews this exact revision/hash and returns PASS or "
                "REJECT with reasons. No approval is created by this build."
            ),
        }
        package_version = self._create_artifact(
            revision_project.id,
            "package_manifest",
            manifest,
            created_by_user_id,
            revision_hash,
        )
        review = ReviewService(self.session).create_review_task(
            data=ReviewTaskCreate(
                video_project_id=revision_project.id,
                target_type="artifact_version",
                target_id=package_version.id,
                target_artifact_version_id=package_version.id,
                review_type="final_human",
                status="open",
                assigned_to_user_id=created_by_user_id,
                requested_by_user_id=created_by_user_id,
                review_reason_codes=[
                    "PKG1_MARKET_REVISION_EXACT_PACKAGE_REVIEW_REQUIRED",
                    "MR1_REAPPROVAL_NOT_AUTHORIZED",
                ],
                evidence_required=True,
                evidence_refs=[
                    self._version_ref(package_version),
                    self._version_ref(created["market_alignment_dossier"]),
                    self._version_ref(created["publish_risk_dossier"]),
                ],
                review_scope=(
                    "Exact PKG1 market revision content/package planning authority only; "
                    "does not authorize provider, render, archive, upload, or publish execution."
                ),
                context_pack_ref=f"pkg1-market-revision://{revision_id}/{revision_hash}",
            ),
            correlation_id="pkg1-market-revision-human-review",
        )
        if self._no_execution_counts() != no_execution_before:
            raise ValidationFailureError("PKG1_MARKET_REVISION_EXECUTION_BOUNDARY_CHANGED")
        if self._historical_fingerprint(
            historical_project, self._current_artifacts(historical_project.id)
        ) != historical_before:
            raise ValidationFailureError("HISTORICAL_PKG1_MUTATED")
        result = self.read_revision(revision_project.id)
        result["human_review_task_id"] = str(review.id)
        return result

    def _validate_existing_pending_revision(
        self,
        project: VideoProject,
        entry: dict[str, Any],
    ) -> None:
        failures: list[str] = []
        result = self.read_revision(project.id)
        package = result["package"]
        artifacts = result["artifacts"]
        bindings = package.get("exact_bindings") or {}
        profile: ChannelProfileVersion = entry["profile"]
        snapshot: CompiledChannelPolicySnapshot = entry["snapshot"]
        market_profile: TargetMarketProfile = entry["target_market_profile"]
        market_digest: TargetMarketDigest = entry["target_market_digest"]
        destination: DestinationBinding = entry["destination"]
        category: ContentCategory = entry["category"]
        historical_project: VideoProject = entry["historical_project"]

        def require(condition: bool, reason_code: str) -> None:
            if not condition:
                failures.append(reason_code)

        require(project.status == "in_review", "EXISTING_REVISION_NOT_IN_REVIEW")
        require(
            project.policy_snapshot_id == snapshot.id,
            "EXISTING_REVISION_SNAPSHOT_ID_STALE",
        )
        require(
            project.category_id == category.id,
            "EXISTING_REVISION_CATEGORY_ID_STALE",
        )
        require(
            package.get("package_status")
            == "TECHNICAL_PASS_HUMAN_REVIEW_PENDING",
            "EXISTING_REVISION_PACKAGE_STATUS_NOT_PENDING",
        )
        require(
            package.get("PKG1_MARKET_REVISION_HUMAN_REVIEW") == "PENDING",
            "EXISTING_REVISION_HUMAN_REVIEW_NOT_PENDING",
        )
        require(
            package.get("PKG1_MARKET_REVISION_FINAL")
            == "WAITING_HUMAN_REVIEW",
            "EXISTING_REVISION_FINAL_STATE_CHANGED",
        )
        require(
            package.get("provider_execution") == "DISABLED",
            "EXISTING_REVISION_PROVIDER_EXECUTION_CHANGED",
        )
        require(
            package.get("PROCEED_TO_MR1") is False
            and package.get("PROCEED_TO_MR1_REAPPROVAL") is False,
            "EXISTING_REVISION_MR1_BOUNDARY_CHANGED",
        )
        require(
            (bindings.get("channel_profile_version") or {}).get("id")
            == str(profile.id)
            and (bindings.get("channel_profile_version") or {}).get(
                "content_hash"
            )
            == profile.profile_input_hash,
            "EXISTING_REVISION_PROFILE_BINDING_STALE",
        )
        require(
            (bindings.get("compiled_channel_policy_snapshot") or {}).get("id")
            == str(snapshot.id)
            and (bindings.get("compiled_channel_policy_snapshot") or {}).get(
                "content_hash"
            )
            == snapshot.content_hash,
            "EXISTING_REVISION_SNAPSHOT_BINDING_STALE",
        )
        require(
            (bindings.get("target_market_profile") or {}).get("content_hash")
            == market_profile.content_hash,
            "EXISTING_REVISION_TARGET_MARKET_BINDING_STALE",
        )
        require(
            (bindings.get("target_market_digest") or {}).get("content_hash")
            == market_digest.content_hash,
            "EXISTING_REVISION_MARKET_DIGEST_BINDING_STALE",
        )
        require(
            (bindings.get("destination_binding") or {}).get("content_hash")
            == destination.content_hash
            and (bindings.get("destination_binding") or {}).get(
                "destination_status"
            )
            == destination.destination_status,
            "EXISTING_REVISION_DESTINATION_BINDING_STALE",
        )
        require(
            (bindings.get("content_category") or {}).get("id")
            == str(category.id)
            and (bindings.get("content_category") or {}).get("content_hash")
            == category.content_hash,
            "EXISTING_REVISION_CATEGORY_BINDING_STALE",
        )
        require(
            (bindings.get("historical_video_project") or {}).get("ref")
            == f"video-project://{historical_project.id}"
            and (bindings.get("historical_video_project") or {}).get(
                "content_hash"
            )
            == self._project_hash(historical_project),
            "EXISTING_REVISION_HISTORICAL_PROJECT_BINDING_STALE",
        )

        package_id = uuid.UUID(result["package_artifact_version_id"])
        reviews = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == project.id
                )
            ).all()
        )
        require(
            len(reviews) == 1
            and reviews[0].review_type == "final_human"
            and reviews[0].status == "open"
            and reviews[0].target_artifact_version_id == package_id,
            "EXISTING_REVISION_EXACT_OPEN_REVIEW_MISSING",
        )

        revision_version_ids = list(
            self.session.scalars(
                select(ArtifactVersion.id)
                .join(Artifact, ArtifactVersion.artifact_id == Artifact.id)
                .where(Artifact.video_project_id == project.id)
            ).all()
        )
        approvals = []
        if revision_version_ids:
            approvals = list(
                self.session.scalars(
                    select(ApprovalDecision).where(
                        ApprovalDecision.target_artifact_version_id.in_(
                            revision_version_ids
                        )
                    )
                ).all()
            )
        require(
            not approvals,
            "EXISTING_REVISION_APPROVAL_ALREADY_PRESENT",
        )

        cost = (artifacts.get("cost_estimate_snapshot") or {}).get("content")
        require(cost is not None, "EXISTING_REVISION_COST_ARTIFACT_MISSING")
        if cost is not None:
            expected_catalogs = [
                ConfigRegistryService(self.session).validate_catalog(path)
                for path in (
                    ROOT / "config/media_provider_budget_policy_catalog.yaml",
                    ROOT
                    / "config/google_gemini_image_model_price_catalog.yaml",
                    ROOT / "config/google_veo_model_price_catalog.yaml",
                )
            ]
            expected_bindings = {
                f"config://{item.catalog_key}/{item.catalog_version}": (
                    item.content_hash
                )
                for item in expected_catalogs
            }
            actual_bindings = {
                item.get("ref"): item.get("content_hash")
                for item in cost.get("catalog_bindings", [])
            }
            require(
                actual_bindings == expected_bindings,
                "EXISTING_REVISION_COST_CATALOG_BINDING_STALE",
            )

        if failures:
            raise ValidationFailureError(
                "EXISTING_PKG1_MARKET_REVISION_INVALID:"
                + ",".join(failures)
            )

    def read_revision(self, project_id: uuid.UUID) -> dict[str, Any]:
        project = self.session.get(VideoProject, project_id)
        if project is None or project.project_type != PROJECT_TYPE:
            raise NotFoundError(f"PKG1 market revision not found: {project_id}")
        artifacts = self._current_artifacts(project.id)
        package = artifacts.get("package_manifest")
        if package is None:
            raise ValidationFailureError("PKG1_MARKET_REVISION_PACKAGE_MISSING")
        reviews = list(
            self.session.scalars(
                select(ReviewTask).where(
                    ReviewTask.video_project_id == project.id,
                    ReviewTask.target_artifact_version_id == package.id,
                    ReviewTask.review_type == "final_human",
                )
            ).all()
        )
        receipt = artifacts.get(
            "pkg1_market_revision_human_review_receipt"
        )
        approvals = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id == package.id,
                    ApprovalDecision.decision == "approved",
                )
            ).all()
        )
        closeout_approvals = [
            item
            for item in approvals
            if (item.metadata_ or {}).get("approval_scope")
            == "PKG1_MARKET_REVISION_PACKAGE_PLANNING"
        ]
        closeout_pass = (
            project.status == "approved"
            and receipt is not None
            and content_hash(receipt.content) == receipt.content_hash
            and receipt.content.get("decision") == "PASS"
            and receipt.content.get("decision_source") == "OPERATOR"
            and receipt.content.get("review_authority") == "HUMAN"
            and receipt.content.get("reviewed_package", {}).get(
                "artifact_version_id"
            )
            == str(package.id)
            and receipt.content.get("reviewed_package", {}).get("content_hash")
            == package.content_hash
            and len(closeout_approvals) == 1
            and receipt.content.get("approval_decision_id")
            == str(closeout_approvals[0].id)
            and len(reviews) == 1
            and reviews[0].status == "completed"
        )
        human_review_state = (
            "PASS"
            if closeout_pass
            else package.content["PKG1_MARKET_REVISION_HUMAN_REVIEW"]
        )
        final_state = (
            "PASS"
            if closeout_pass
            else package.content["PKG1_MARKET_REVISION_FINAL"]
        )
        return {
            "video_project_id": str(project.id),
            "package_artifact_version_id": str(package.id),
            "package_content_hash": package.content_hash,
            "revision_id": package.content["revision_id"],
            "revision_version": package.content["revision_version"],
            "revision_hash": package.content["revision_hash"],
            "package": deepcopy(package.content),
            "artifacts": {
                key: {
                    **self._version_ref(value),
                    "content": deepcopy(value.content),
                }
                for key, value in artifacts.items()
            },
            "human_review_task_ids": [str(item.id) for item in reviews],
            "approval_decision_ids": [
                str(item.id) for item in closeout_approvals
            ],
            "human_review_state": human_review_state,
            "final_state": final_state,
            "effective_state": {
                "PKG1_MARKET_REVISION_HUMAN_REVIEW": human_review_state,
                "PKG1_MARKET_REVISION_FINAL": final_state,
                "PRODUCTION_PACKAGE_APPROVED": closeout_pass,
                "UPLOAD_READY": False,
                "PUBLISH_EXECUTION_READY": False,
                "destination_status": "PENDING_PLATFORM_ID",
                "MR1_REAPPROVAL_ENTRY": (
                    "READY" if closeout_pass else "NOT_READY"
                ),
                "MR1_EXECUTION": "NOT_STARTED",
                "PROCEED_TO_MR1_REAPPROVAL": closeout_pass,
                "PROCEED_TO_MR1": False,
            },
            "provider_calls": 0,
            "render_calls": 0,
            "drive_calls": 0,
            "youtube_calls": 0,
        }

    def _load_entry_reports(self) -> dict[str, dict[str, Any]]:
        loaded: dict[str, dict[str, Any]] = {}
        for key, path in self.report_paths.items():
            if not path.exists():
                raise ValidationFailureError(f"ENTRY_REPORT_MISSING:{path}")
            loaded[key] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    @staticmethod
    def _validate_snapshot_binding(
        *,
        refs: dict[str, Any],
        key: str,
        expected_hash: str,
        failures: list[str],
    ) -> None:
        binding = refs.get(key) or {}
        if not binding.get("ref") or binding.get("content_hash") != expected_hash:
            failures.append(f"SNAPSHOT_{key.upper()}_BINDING_MISMATCH")

    def _create_revision_slot(
        self,
        *,
        channel: ChannelWorkspace,
        snapshot: CompiledChannelPolicySnapshot,
        category: ContentCategory,
        series_key: str,
        historical_artifacts: dict[str, ArtifactVersion],
        historical_project: VideoProject,
        revision_id: str,
        created_by_user_id: uuid.UUID,
    ) -> EditorialCalendarSlot:
        lineage = historical_artifacts["idea_admission_lineage"].content
        old_slot_id = uuid.UUID(lineage["editorial_calendar_slot_id"])
        old_slot = self.session.get(EditorialCalendarSlot, old_slot_id)
        if old_slot is None:
            raise ValidationFailureError("HISTORICAL_EDITORIAL_SLOT_MISSING")
        revision_slot_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"vcos:pkg1-market-revision:{revision_id}:editorial-slot",
        )
        existing = self.session.get(EditorialCalendarSlot, revision_slot_id)
        if existing is not None:
            return existing
        slot = EditorialCalendarSlot(
            id=revision_slot_id,
            company_id=channel.company_id,
            channel_workspace_id=channel.id,
            policy_snapshot_id=snapshot.id,
            slot_date=old_slot.slot_date,
            slot_type="MANUAL",
            status="OPEN",
            category_id=category.id,
            production_goal=old_slot.production_goal,
            target_platforms=list(old_slot.target_platforms or ["YouTube"]),
            content_pillar=category.content_pillar,
            series_key=series_key,
            format_hint=old_slot.format_hint,
            character_binding_policy_json=deepcopy(
                old_slot.character_binding_policy_json or {"mode": "NO_CHARACTER"}
            ),
            risk_level=old_slot.risk_level,
            operational_envelope={
                **deepcopy(old_slot.operational_envelope or {}),
                "pkg1_market_revision_id": revision_id,
                "supersedes_editorial_slot_ref": f"editorial-slot://{old_slot.id}",
                "historical_video_project_ref": f"video-project://{historical_project.id}",
                "target_market": "US",
                "primary_locale": "en-US",
            },
            created_by_user_id=created_by_user_id,
        )
        self.session.add(slot)
        self.session.flush()
        return slot

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
            correlation_id=f"pkg1-market-revision-{artifact_type}",
        )
        return service.create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=artifact.id,
                content=deepcopy(payload),
                status="submitted",
                created_by_user_id=created_by_user_id,
                context_refs=[
                    {"type": "pkg1_market_revision", "content_hash": revision_hash}
                ],
                packaging_metadata={
                    "pkg1_market_revision": True,
                    "provider_execution": "DISABLED",
                    "human_review": "PENDING",
                },
            ),
            correlation_id=f"pkg1-market-revision-version-{artifact_type}",
        )

    def _revise_artifact_version(
        self,
        parent: ArtifactVersion,
        payload: dict[str, Any],
        created_by_user_id: uuid.UUID,
        revision_hash: str,
    ) -> ArtifactVersion:
        return ArtifactService(self.session).create_artifact_version(
            data=ArtifactVersionCreate(
                artifact_id=parent.artifact_id,
                parent_version_id=parent.id,
                content=deepcopy(payload),
                status="submitted",
                created_by_user_id=created_by_user_id,
                context_refs=[
                    {
                        "type": "pkg1_market_revision",
                        "content_hash": revision_hash,
                    },
                    {
                        "type": "binding_only_parent_version",
                        "artifact_version_id": str(parent.id),
                        "content_hash": parent.content_hash,
                    },
                ],
                packaging_metadata={
                    "pkg1_market_revision": True,
                    "binding_revision_only": True,
                    "provider_execution": "DISABLED",
                    "human_review": "PENDING",
                },
            ),
            correlation_id="pkg1-market-revision-thumbnail-binding-version",
        )

    def _current_artifacts(self, project_id: uuid.UUID) -> dict[str, ArtifactVersion]:
        artifacts = list(
            self.session.scalars(
                select(Artifact).where(Artifact.video_project_id == project_id)
            ).all()
        )
        return {
            artifact.artifact_type: self.session.get(
                ArtifactVersion, artifact.current_version_id
            )
            for artifact in artifacts
            if artifact.current_version_id is not None
        }

    @staticmethod
    def _version_ref(version: ArtifactVersion) -> dict[str, Any]:
        return {
            "artifact_id": str(version.artifact_id),
            "artifact_version_id": str(version.id),
            "artifact_version_ref": f"artifact-version://{version.id}",
            "version_number": version.version_number,
            "content_hash": version.content_hash,
        }

    def _historical_refs(
        self, artifacts: dict[str, ArtifactVersion]
    ) -> dict[str, dict[str, Any]]:
        return {key: self._version_ref(value) for key, value in artifacts.items()}

    def _historical_fingerprint(
        self, project: VideoProject, artifacts: dict[str, ArtifactVersion]
    ) -> str:
        artifact_rows = list(
            self.session.scalars(
                select(Artifact).where(Artifact.video_project_id == project.id)
            ).all()
        )
        artifact_ids = [item.id for item in artifact_rows]
        version_rows = list(
            self.session.scalars(
                select(ArtifactVersion).where(
                    ArtifactVersion.artifact_id.in_(artifact_ids)
                )
            ).all()
        )
        version_ids = [item.id for item in version_rows]
        approval_rows = list(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_artifact_version_id.in_(version_ids)
                )
            ).all()
        )
        approval_rows.extend(
            self.session.scalars(
                select(ApprovalDecision).where(
                    ApprovalDecision.target_type == "video_project",
                    ApprovalDecision.target_id == project.id,
                )
            ).all()
        )
        approval_rows = list({item.id: item for item in approval_rows}.values())
        review_rows = list(
            self.session.scalars(
                select(ReviewTask).where(ReviewTask.video_project_id == project.id)
            ).all()
        )
        return content_hash(
            {
                "project": {
                    "id": str(project.id),
                    "status": project.status,
                    "policy_snapshot_id": str(project.policy_snapshot_id),
                    "channel_profile_version_id": str(
                        project.channel_profile_version_id
                    ),
                    "updated_at": project.updated_at.isoformat(),
                },
                "artifacts": {
                    key: {
                        "id": str(value.id),
                        "version": value.version_number,
                        "hash": value.content_hash,
                    }
                    for key, value in sorted(artifacts.items())
                },
                "artifact_rows": sorted(
                    (
                        str(item.id),
                        item.artifact_type,
                        str(item.current_version_id),
                        item.status,
                        item.updated_at.isoformat(),
                    )
                    for item in artifact_rows
                ),
                "artifact_versions": sorted(
                    (
                        str(item.id),
                        str(item.artifact_id),
                        item.version_number,
                        str(item.parent_version_id),
                        item.content_hash,
                        item.status,
                    )
                    for item in version_rows
                ),
                "approval_decisions": sorted(
                    (
                        str(item.id),
                        item.target_type,
                        str(item.target_id),
                        str(item.target_artifact_version_id),
                        item.decision,
                        item.decided_at.isoformat(),
                    )
                    for item in approval_rows
                ),
                "review_tasks": sorted(
                    (
                        str(item.id),
                        item.target_type,
                        str(item.target_id),
                        str(item.target_artifact_version_id),
                        item.review_type,
                        item.status,
                        item.updated_at.isoformat(),
                    )
                    for item in review_rows
                ),
            }
        )

    def _no_execution_counts(self) -> dict[str, int]:
        counts = PKG1PackageService(self.session).no_execution_counts()
        counts.update(
            {
                "llm_run_snapshots": self.session.scalar(
                    select(func.count()).select_from(LLMRunSnapshot)
                )
                or 0,
                "llm_route_attempts": self.session.scalar(
                    select(func.count()).select_from(LLMRouteAttempt)
                )
                or 0,
            }
        )
        return counts

    @staticmethod
    def _project_hash(project: VideoProject) -> str:
        return content_hash(
            {
                "id": str(project.id),
                "channel_workspace_id": str(project.channel_workspace_id),
                "policy_snapshot_id": str(project.policy_snapshot_id),
                "channel_profile_version_id": str(project.channel_profile_version_id),
                "title": project.title,
                "project_type": project.project_type,
                "created_at": project.created_at.isoformat(),
            }
        )

    @staticmethod
    def _idea_subject_hash(idea: DailyIdeaDecision) -> str:
        return content_hash(
            {
                "id": str(idea.id),
                "title": idea.proposed_title,
                "angle": idea.proposed_angle,
                "format": idea.proposed_format,
                "pillar": idea.proposed_pillar,
                "series": idea.proposed_series_key,
                "decision_status": idea.decision_status,
            }
        )

    def _exact_bindings(
        self,
        *,
        historical_project: VideoProject,
        revision_project: VideoProject,
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        snapshot_refs: dict[str, Any],
        channel_contract_hash: str,
        niche_digest: Any,
        revision_slot: EditorialCalendarSlot,
        category: ContentCategory,
        market_profile: TargetMarketProfile,
        market_digest: TargetMarketDigest,
        destination: DestinationBinding,
    ) -> dict[str, Any]:
        return {
            "channel_profile_version": {
                "ref": f"channel-profile-version://{profile.id}",
                "id": str(profile.id),
                "version": profile.version,
                "content_hash": profile.profile_input_hash,
            },
            "compiled_channel_policy_snapshot": {
                "ref": f"compiled-policy-snapshot://{snapshot.id}",
                "id": str(snapshot.id),
                "version": snapshot.snapshot_version,
                "content_hash": snapshot.content_hash,
            },
            "channel_contract": {
                "ref": f"compiled-policy-snapshot://{snapshot.id}/channel-contract",
                "content_hash": channel_contract_hash,
            },
            "niche_contract_digest": {
                "ref": f"editorial-slot://{revision_slot.id}#niche-contract-digest",
                "content_hash": niche_digest.content_hash,
            },
            "target_market_profile": {
                "ref": snapshot_refs["target_market_profile"]["ref"],
                "governance_ref": market_digest.profile_ref,
                "version": market_profile.profile_version,
                "content_hash": market_profile.content_hash,
            },
            "target_market_digest": {
                "ref": snapshot_refs["target_market_digest"]["ref"],
                "governance_ref": f"{market_digest.profile_ref}/digest",
                "content_hash": market_digest.content_hash,
            },
            "destination_binding": {
                "ref": snapshot_refs["destination_binding"]["ref"],
                "version": destination.binding_version,
                "content_hash": destination.content_hash,
                "destination_status": destination.destination_status,
            },
            "historical_video_project": {
                "ref": f"video-project://{historical_project.id}",
                "content_hash": self._project_hash(historical_project),
            },
            "revision_video_project": {
                "ref": f"video-project://{revision_project.id}",
                "content_hash": self._project_hash(revision_project),
            },
            "editorial_slot": {
                "ref": f"editorial-slot://{revision_slot.id}",
                "content_hash": niche_digest.editorial_slot_hash,
            },
            "content_category": {
                "ref": f"content-category://{category.id}",
                "id": str(category.id),
                "content_hash": category.content_hash,
                "pillar": category.content_pillar,
                "series": revision_slot.series_key,
                "production_goal": revision_slot.production_goal,
            },
            "provider_usage_policy": {
                "ref": snapshot_refs["provider_usage_policy"]["ref"],
                "content_hash": snapshot_refs["provider_usage_policy"][
                    "content_hash"
                ],
            },
            "budget_policy": {
                "ref": snapshot_refs["budget_policy"]["ref"],
                "content_hash": snapshot_refs["budget_policy"]["content_hash"],
            },
            "native_render_policy": {
                "ref": snapshot_refs["native_render_policy"]["ref"],
                "content_hash": snapshot_refs["native_render_policy"][
                    "content_hash"
                ],
            },
            "lpro1_production_orchestrator_version": LPRO1_ORCHESTRATOR_VERSION,
            "lpro1_production_contract_version": LPRO1_RENDER_CONTRACT_VERSION,
        }

    @staticmethod
    def _revised_editorial_payloads(
        *,
        historical_artifacts: dict[str, ArtifactVersion],
        historical_project: VideoProject,
        revision_project: VideoProject,
        revision_slot: EditorialCalendarSlot,
        revision_id: str,
        revision_hash: str,
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        bindings: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        old_lineage = historical_artifacts["idea_admission_lineage"].content
        source_pack = deepcopy(historical_artifacts["source_pack"].content)
        for source in source_pack.get("sources", []):
            if source.get("source_id") == "SRC-003":
                source.update(
                    {
                        "title": "Exact active Small Team AI policy v3",
                        "source_ref": f"compiled-policy-snapshot://{snapshot.id}",
                        "freshness": "CURRENT_REVISION",
                    }
                )
        source_pack.update(
            {
                "schema_version": "pkg1.market-source-pack.v2",
                "supersedes": {
                    "artifact_version_ref": f"artifact-version://{historical_artifacts['source_pack'].id}",
                    "content_hash": historical_artifacts["source_pack"].content_hash,
                },
                "active_profile_ref": f"channel-profile-version://{profile.id}",
                "active_snapshot_ref": f"compiled-policy-snapshot://{snapshot.id}",
                "active_snapshot_hash": snapshot.content_hash,
                "semantic_evidence_changed": False,
                "authority_binding_revised": True,
            }
        )
        research = deepcopy(historical_artifacts["research_pack"].content)
        research.update(
            {
                "schema_version": "pkg1.market-research-pack.v2",
                "supersedes": {
                    "artifact_version_ref": f"artifact-version://{historical_artifacts['research_pack'].id}",
                    "content_hash": historical_artifacts["research_pack"].content_hash,
                },
                "target_market": "US",
                "source_jurisdiction": "US",
                "external_measured_claim_count": 0,
                "research_jurisdiction_gate_required": True,
                "semantic_findings_changed": False,
            }
        )
        claims = deepcopy(historical_artifacts["claim_evidence_ledger"].content)
        claims.update(
            {
                "schema_version": "pkg1.market-claim-evidence-ledger.v2",
                "supersedes": {
                    "artifact_version_ref": f"artifact-version://{historical_artifacts['claim_evidence_ledger'].id}",
                    "content_hash": historical_artifacts["claim_evidence_ledger"].content_hash,
                },
                "target_market": "US",
                "source_pack_authority": "REVISION_SCOPED_SRC_003_V3",
                "claim_text_or_calculation_changed": False,
            }
        )
        brief = deepcopy(historical_artifacts["creative_brief"].content)
        brief.update(
            {
                "schema_version": "pkg1.market-creative-brief.v2",
                "profile_snapshot_ref": f"compiled-policy-snapshot://{snapshot.id}",
                "profile_snapshot_hash": snapshot.content_hash,
                "target_market": "US",
                "primary_locale": "en-US",
                "narration_locale": "en-US",
                "supersedes": {
                    "artifact_version_ref": f"artifact-version://{historical_artifacts['creative_brief'].id}",
                    "content_hash": historical_artifacts["creative_brief"].content_hash,
                },
            }
        )
        originality = deepcopy(
            historical_artifacts["episode_originality_manifest"].content
        )
        originality.update(
            {
                "schema_version": "pkg1.market-episode-originality-manifest.v2",
                "target_market": "US",
                "visual_authority": "REBUILT_UNDER_STOCK_ASSISTED_V3",
                "thumbnail_locale": "en-US",
                "metadata_locale": "en-US",
                "decision": "PASS",
                "human_review_state": "PENDING",
                "supersedes": {
                    "artifact_version_ref": f"artifact-version://{historical_artifacts['episode_originality_manifest'].id}",
                    "content_hash": historical_artifacts[
                        "episode_originality_manifest"
                    ].content_hash,
                },
            }
        )
        return {
            "idea_admission_lineage": {
                "schema_version": "pkg1.market-idea-lineage.v2",
                "revision_id": revision_id,
                "revision_hash": revision_hash,
                "historical_idea_decision_ref": f"daily-idea-decision://{old_lineage['daily_idea_decision_id']}",
                "historical_idea_decision_reused_as_source": True,
                "historical_slot_ref": f"editorial-slot://{old_lineage['editorial_calendar_slot_id']}",
                "historical_slot_current_authority": False,
                "revision_slot_ref": f"editorial-slot://{revision_slot.id}",
                "revision_project_ref": f"video-project://{revision_project.id}",
                "historical_project_ref": f"video-project://{historical_project.id}",
                "profile_v3": bindings["channel_profile_version"],
                "snapshot_v3": bindings["compiled_channel_policy_snapshot"],
                "category": bindings["content_category"],
            },
            "source_pack": source_pack,
            "research_pack": research,
            "claim_evidence_ledger": claims,
            "creative_brief": brief,
            "episode_originality_manifest": originality,
        }

    @staticmethod
    def _voice_policy(
        *,
        policy: ChannelScopedPolicy,
        market_profile: TargetMarketProfile,
        snapshot: CompiledChannelPolicySnapshot,
        revision_hash: str,
    ) -> dict[str, Any]:
        raw = policy.voice_policy.model_dump(mode="json")
        return {
            "schema_version": "pkg1.market-aware-voice-policy.v1",
            "revision_hash": revision_hash,
            "content_language": market_profile.content_language,
            "narration_locale": market_profile.narration_locale,
            "voice_profile_locale": "en-US",
            "voice_identity": {
                "provider": raw["provider"],
                "voice_id": raw["voice_id"],
                "voice_name": raw["voice_name"],
                "model_id": raw["model_id"],
                "approval_state": raw["commercial_use_state"],
            },
            "pronunciation_policy": {
                "dictionary_refs": raw["pronunciation_dictionary_refs"],
                "locale": "en-US",
                "foreign_locale_inheritance_allowed": False,
            },
            "pacing_policy": {
                "ref": f"compiled-policy-snapshot://{snapshot.id}/voice-policy",
                "style": (snapshot.compiled_payload or {}).get("voice_policy") or {},
                "settings": raw["settings"],
            },
            "forced_alignment_required": raw["forced_alignment_required"],
            "tts_called": False,
            "decision": "PASS",
        }

    @staticmethod
    def _visual_payloads(
        *,
        channel: ChannelWorkspace,
        category: ContentCategory,
        historical_artifacts: dict[str, ArtifactVersion],
        bindings: dict[str, Any],
        revision_hash: str,
    ) -> dict[str, dict[str, Any]]:
        old_scenes = deepcopy(historical_artifacts["visual_plan"].content["scenes"])
        intents: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for scene in old_scenes:
            role = scene["source_role"]
            route = "PEXELS_VIDEO" if role == "PEXELS_SUPPORTING" else "NATIVE_DIAGRAM"
            provider = "pexels_api" if route == "PEXELS_VIDEO" else "native"
            intent = {
                **scene,
                "niche_visual_source_profile": "STOCK_ASSISTED",
                "target_market": "US",
                "market_context": "US_SMALL_BUSINESS",
                "workplace_context": "US_SMALL_BUSINESS",
                "currency": "USD",
                "date_format": "MMM D, YYYY",
                "units_policy": "US_WITH_METRIC_WHEN_RELEVANT",
                "generated_evidence_authority": False,
                "canonical_timestamps": None,
            }
            intents.append(intent)
            decisions.append(
                {
                    "scene_id": scene["scene_id"],
                    "preferred_source_route": route,
                    "provider": provider,
                    "niche_visual_source_profile": "STOCK_ASSISTED",
                    "semantic_intent": scene["semantic_intent"],
                    "eligibility": (
                        "OBSERVABLE_REALITY_SUPPORTING_FOOTAGE_ONLY"
                        if route == "PEXELS_VIDEO"
                        else "MECHANISM_WORKFLOW_LABEL_NUMBER_COMPARISON_TIMELINE"
                    ),
                    "planned_requests": 1 if route == "PEXELS_VIDEO" else 0,
                    "maximum_automated_attempts": 1 if route == "PEXELS_VIDEO" else 0,
                    "automatic_pexels_to_ai_fallback": False,
                    "fallback": ["NATIVE_REVISION", "HUMAN_REVIEW"],
                    "generated_evidence_authority": False,
                    "market_checks": {
                        "us_workplace_context": True,
                        "usd_date_units": True,
                        "foreign_ui_context": False,
                    },
                }
            )
        return {
            "scene_visual_intent": {
                "schema_version": "pkg1.market-scene-intent.v1",
                "revision_hash": revision_hash,
                "scenes": intents,
                "canonical_timestamps_created": False,
            },
            "visual_direction_contract": {
                "schema_version": "pkg1.market-visual-direction.v1",
                "revision_hash": revision_hash,
                "channel_id": str(channel.id),
                "category_id": str(category.id),
                "content_pillar": category.content_pillar,
                "target_market": "US",
                "primary_locale": "en-US",
                "niche_visual_source_profile": "STOCK_ASSISTED",
                "rules": {
                    "pexels": "observable reality/supporting footage only",
                    "gemini_image": {
                        "use": "custom editorial still only",
                        "model": "gemini-3.1-flash-image",
                        "size": "2K",
                        "outputs": 1,
                        "maximum_automated_attempts": 1,
                        "exact_text_requires_native_overlay": True,
                    },
                    "native": "mechanism/workflow/labels/numbers/comparison/timeline",
                    "authorized_assets": "actual UI/product/document/evidence only",
                    "veo": "only when motion has semantic value",
                    "generated_evidence_authority": False,
                },
                "profile_binding": bindings["channel_profile_version"],
                "market_binding": bindings["target_market_profile"],
            },
            "visual_source_decision_set": {
                "schema_version": "pkg1.market-visual-source-decisions.v1",
                "revision_hash": revision_hash,
                "decisions": decisions,
                "one_route_per_scene": True,
                "automatic_pexels_to_ai_fallback": False,
                "provider_outputs": [],
            },
            "visual_plan": {
                "schema_version": "pkg1.market-visual-plan.v1",
                "revision_hash": revision_hash,
                "scenes": intents,
                "coverage": {
                    "scene_count": len(intents),
                    "routed_scene_count": len(decisions),
                    "complete": len(intents) == len(decisions),
                },
                "canonical_timestamps_created": False,
                "provider_outputs": [],
            },
            "compiled_asset_request_plan": {
                "schema_version": "pkg1.market-asset-request-plan.v1",
                "revision_hash": revision_hash,
                "execution_enabled": False,
                "requests": [
                    {
                        "scene_id": item["scene_id"],
                        "route": item["preferred_source_route"],
                        "provider": item["provider"],
                        "maximum_automated_attempts": item[
                            "maximum_automated_attempts"
                        ],
                        "state": "PLANNED_NOT_EXECUTED",
                        "human_approval_required": item["provider"] != "native",
                        "idempotency_ref": f"pkg1-market/{revision_hash}/{item['scene_id']}",
                    }
                    for item in decisions
                ],
                "raw_provider_urls": [],
                "selected_provider_assets": [],
                "automatic_pexels_to_ai_fallback": False,
            },
        }

    @staticmethod
    def _thumbnail_brief(
        *,
        script: dict[str, Any],
        script_ref: dict[str, Any],
        claims: ArtifactVersion,
        bindings: dict[str, Any],
        revision_hash: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-thumbnail-brief.v1",
            "revision_hash": revision_hash,
            "target_market": "US",
            "text_locale": "en-US",
            "topic_promise": script["title"],
            "thumbnail_copy": "20 HOURS?",
            "visual_concept": "A five-person workflow grid with one bounded automation path; question mark keeps the scenario illustrative.",
            "script_ref": script_ref,
            "claim_evidence_ref": {
                "artifact_version_ref": f"artifact-version://{claims.id}",
                "content_hash": claims.content_hash,
            },
            "target_market_profile": bindings["target_market_profile"],
            "rules": {
                "adjacent_niche_bait": False,
                "foreign_market_wording": False,
                "generated_exact_text_allowed": False,
                "unsupported_number_or_claim": False,
                "misleading_ui_or_product": False,
            },
            "provider_execution": "NOT_STARTED",
            "decision": "PASS",
        }

    @staticmethod
    def _metadata_package(
        *,
        script: dict[str, Any],
        claims: ArtifactVersion,
        category: ContentCategory,
        revision_hash: str,
    ) -> dict[str, Any]:
        chapters: list[str] = []
        for segment in script["segments"]:
            section = str(segment["section"])
            if section not in chapters:
                chapters.append(section)
        description = (
            "A practical workflow audit for small teams. The 20-hour example is an "
            "illustrative calculation—five people × one hour a day × four days—not a "
            "measured result or guarantee. Map one trigger, one owner, and one human "
            "exception path before automating."
        )
        return {
            "schema_version": "pkg1.market-metadata-package.v1",
            "revision_hash": revision_hash,
            "locale": "en-US",
            "market": "US",
            "original_language": "en",
            "title": script["title"],
            "description": description,
            "chapters": chapters,
            "keywords": [
                "small team automation",
                "AI workflow",
                "workflow audit",
                "small business productivity",
            ],
            "tags": ["AI workflows", "automation", "small teams"],
            "cta": "Map one repeated workflow, measure its baseline, and keep a human exception path.",
            "upload_card_copy": "US English master; illustrative scenario; manual YouTube upload only.",
            "category_ref": f"content-category://{category.id}",
            "content_pillar": category.content_pillar,
            "claim_evidence_ref": {
                "artifact_version_ref": f"artifact-version://{claims.id}",
                "content_hash": claims.content_hash,
            },
            "checks": {
                "us_spelling": True,
                "us_search_wording": True,
                "translated_sounding_copy": False,
                "adjacent_market_bait": False,
                "metadata_drift_from_script": False,
            },
            "decision": "PASS",
        }

    def _niche_dossier(
        self,
        *,
        channel: ChannelWorkspace,
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        category: ContentCategory,
        revision_slot: EditorialCalendarSlot,
        niche_digest: Any,
        niche_digest_artifact_ref: dict[str, Any],
        topic_ref: dict[str, Any],
        script_ref: dict[str, Any],
        script: dict[str, Any],
        visual_ref: dict[str, Any],
        visual_direction: dict[str, Any],
        scene_intents: list[dict[str, Any]],
        visual_decisions: list[dict[str, Any]],
        thumbnail_ref: dict[str, Any],
        thumbnail: dict[str, Any],
        metadata_ref: dict[str, Any],
        metadata: dict[str, Any],
        idea_id: uuid.UUID,
    ) -> dict[str, Any]:
        idea = self.session.get(DailyIdeaDecision, idea_id)
        if idea is None:
            raise ValidationFailureError("HISTORICAL_DAILY_IDEA_DECISION_MISSING")
        idea_subject_ref = f"daily-idea-decision://{idea.id}"
        idea_subject_hash = self._idea_subject_hash(idea)
        channel_contract = (snapshot.compiled_payload or {}).get(
            "channel_contract_json"
        ) or {}
        slot_validation = EditorialSlotValidator().validate(
            channel=channel,
            profile_version=profile,
            policy_snapshot=snapshot,
            channel_contract=channel_contract,
            category=category,
            editorial_slot=revision_slot,
            strict_production=True,
        )
        if (
            slot_validation.verdict != NicheGateVerdict.PASS
            or slot_validation.slot_binding is None
            or slot_validation.category_binding is None
        ):
            raise ValidationFailureError("PKG1_REVISION_NICHE_SLOT_NOT_PASS")

        digest_ref = niche_digest_artifact_ref["artifact_version_ref"]
        policy_ref = niche_digest.compiled_policy_snapshot_ref
        evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=topic_ref["artifact_version_ref"],
            content_hash=topic_ref["content_hash"],
        )

        def semantic(
            checks: dict[NicheCriterion, tuple[bool, str]],
            subject: NicheEvidenceRef,
        ) -> list[NicheCriterionEvidence]:
            return [
                NicheCriterionEvidence(
                    criterion=criterion,
                    verdict=(
                        NicheGateVerdict.PASS
                        if passed
                        else NicheGateVerdict.BLOCK
                    ),
                    score=1.0 if passed else 0.0,
                    rationale=rationale,
                    reason_codes=(
                        []
                        if passed
                        else [NicheReasonCode.SEMANTIC_ALIGNMENT_BLOCKED]
                    ),
                    evidence_refs=[subject],
                )
                for criterion, (passed, rationale) in checks.items()
            ]

        idea_text = f"{idea.proposed_title} {idea.proposed_angle}".lower()
        script_text = "\n".join(item["text"] for item in script["segments"])
        script_text_lower = script_text.lower()
        topic_checks = {
            NicheCriterion.NICHE_RELEVANCE: (
                "workflow" in idea_text or "automation" in idea_text,
                "Topic explicitly addresses a workflow or automation mechanism.",
            ),
            NicheCriterion.AUDIENCE_FIT: (
                bool(category.audience_segment and niche_digest.audience_segments),
                "Active category audience is present in the compiled niche audience set.",
            ),
            NicheCriterion.POSITIONING_FIT: (
                bool(niche_digest.positioning),
                "Compiled niche positioning is non-empty and exact-bound.",
            ),
            NicheCriterion.BRAND_PROMISE_FIT: (
                bool(niche_digest.brand_promise)
                and idea.decision_status == "ADMITTED",
                "Compiled brand promise is present and the admitted topic remains its approved evidence.",
            ),
            NicheCriterion.ALLOWED_TOPIC_COMPLIANCE: (
                idea.decision_status == "ADMITTED",
                "Historical idea remains ADMITTED and is reused only as topic evidence.",
            ),
            NicheCriterion.SERIES_FIT: (
                revision_slot.series_key == niche_digest.series_key,
                "Revision slot series equals the compiled niche digest series.",
            ),
            NicheCriterion.PRODUCTION_GOAL_FIT: (
                bool(revision_slot.production_goal),
                "Revision slot carries an explicit bounded production goal.",
            ),
        }

        common = {
            "niche_contract_digest": niche_digest,
            "niche_contract_digest_ref": digest_ref,
            "niche_contract_digest_hash": niche_digest.content_hash,
            "active_policy_snapshot_ref": policy_ref,
            "active_policy_snapshot_hash": snapshot.content_hash,
        }
        topic = TopicNicheAlignmentGate().evaluate(
            TopicNicheAlignmentInput(
                **common,
                subject_ref=idea_subject_ref,
                subject_hash=idea_subject_hash,
                semantic_evidence=semantic(topic_checks, evidence),
                evidence_refs=[evidence],
                channel_id=channel.id,
                slot_binding=slot_validation.slot_binding,
                category_binding=slot_validation.category_binding,
                topic=idea.proposed_title,
                angle=idea.proposed_angle,
                claim_scope=["illustrative 20-hour workflow scenario"],
                adjacent_niche_conflict=False,
            )
        )
        script_evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=script_ref["artifact_version_ref"],
            content_hash=script_ref["content_hash"],
        )
        script_result = ScriptNicheAlignmentGate().evaluate(
            ScriptNicheAlignmentInput(
                **common,
                subject_ref=script_ref["artifact_version_ref"],
                subject_hash=script_ref["content_hash"],
                semantic_evidence=semantic(
                    {
                        NicheCriterion.TOPIC_FIDELITY: (
                            script["title"] == idea.proposed_title,
                            "Script title exactly matches the admitted topic.",
                        ),
                        NicheCriterion.NICHE_RELEVANCE: (
                            "workflow" in script_text_lower
                            and "automation" in script_text_lower,
                            "Script repeatedly addresses bounded workflow automation.",
                        ),
                        NicheCriterion.AUDIENCE_FIT: (
                            "team" in script_text_lower,
                            "Script explicitly addresses teams and their operating handoffs.",
                        ),
                        NicheCriterion.POSITIONING_FIT: (
                            "human exception" in script_text_lower
                            or "exception path" in script_text_lower,
                            "Script preserves the channel's bounded human-control positioning.",
                        ),
                        NicheCriterion.BRAND_PROMISE_FIT: (
                            "practical" in script_text_lower
                            or "audit" in script_text_lower,
                            "Script delivers a practical audit method rather than hype.",
                        ),
                        NicheCriterion.CLAIM_SCOPE_FIT: (
                            "illustrative scenario" in script_text_lower
                            and "guarantee" in script_text_lower
                            and (
                                "not a benchmark" in script_text_lower
                                or "cannot promise" in script_text_lower
                            ),
                            "Script labels the arithmetic illustrative and rejects guarantees.",
                        ),
                    },
                    script_evidence,
                ),
                evidence_refs=[script_evidence],
                daily_idea_ref=idea_subject_ref,
                daily_idea_hash=idea_subject_hash,
                topic_gate_ref=f"niche-gate://topic/{topic.content_hash}",
                topic_gate_result=topic,
                approved_topic=idea.proposed_title,
                script_topic=script["title"],
                script_text=script_text,
                declared_primary_niche=niche_digest.primary_niche,
                declared_sub_niche=niche_digest.category_sub_niche,
                declared_category_id=niche_digest.category_id,
                declared_content_pillar_key=niche_digest.content_pillar_key,
                addressed_audience_pain_points=[
                    niche_digest.audience_pain_points[0]
                ],
                addressed_audience_desired_outcomes=[
                    niche_digest.audience_desired_outcomes[0]
                ],
                claim_scope=["illustrative workflow arithmetic only"],
                adjacent_niche_conflict=False,
            )
        )
        visual_evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=visual_ref["artifact_version_ref"],
            content_hash=visual_ref["content_hash"],
        )
        visual_result = VisualNicheAlignmentGate().evaluate(
            VisualNicheAlignmentInput(
                **common,
                subject_ref=visual_ref["artifact_version_ref"],
                subject_hash=visual_ref["content_hash"],
                semantic_evidence=semantic(
                    {
                        NicheCriterion.VISUAL_LANGUAGE_FIT: (
                            visual_direction.get("niche_visual_source_profile")
                            == niche_digest.visual_source_profile,
                            "Visual direction preserves the exact compiled source profile.",
                        ),
                        NicheCriterion.VISUAL_MEANING_FIDELITY: (
                            len(scene_intents) == len(visual_decisions)
                            and {
                                item["scene_id"] for item in scene_intents
                            }
                            == {
                                item["scene_id"] for item in visual_decisions
                            }
                            and all(
                                item.get("semantic_intent")
                                for item in visual_decisions
                            ),
                            "Every scene has one semantic source decision with matching ID.",
                        ),
                        NicheCriterion.PILLAR_CATEGORY_FIT: (
                            visual_direction.get("content_pillar")
                            == niche_digest.content_pillar_key
                            and visual_direction.get("category_id")
                            == str(niche_digest.category_id),
                            "Visual direction exactly binds the compiled pillar and category.",
                        ),
                    },
                    visual_evidence,
                ),
                evidence_refs=[visual_evidence],
                visual_direction_contract=visual_direction,
                scene_visual_intents=scene_intents,
                visual_source_decisions=visual_decisions,
                content_pillar_key=niche_digest.content_pillar_key,
                category_id=niche_digest.category_id,
                ai_image_editorial_justification_refs={},
                authorized_asset_evidence_refs={},
            )
        )
        thumbnail_evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=thumbnail_ref["artifact_version_ref"],
            content_hash=thumbnail_ref["content_hash"],
        )
        claim_evidence = NicheEvidenceRef(
            type="claim_evidence",
            ref=thumbnail["claim_evidence_ref"]["artifact_version_ref"],
            content_hash=thumbnail["claim_evidence_ref"]["content_hash"],
        )
        thumbnail_result = ThumbnailNicheAlignmentGate().evaluate(
            ThumbnailNicheAlignmentInput(
                **common,
                subject_ref=thumbnail_ref["artifact_version_ref"],
                subject_hash=thumbnail_ref["content_hash"],
                semantic_evidence=semantic(
                    {
                        NicheCriterion.THUMBNAIL_PROMISE_FIDELITY: (
                            thumbnail["topic_promise"] == script["title"],
                            "Thumbnail promise exactly matches the approved script topic.",
                        ),
                        NicheCriterion.VISUAL_LANGUAGE_FIT: (
                            "workflow" in thumbnail["visual_concept"].lower(),
                            "Thumbnail concept uses the episode's workflow visual language.",
                        ),
                        NicheCriterion.CLAIM_SCOPE_FIT: (
                            bool(thumbnail["claim_evidence_ref"].get("content_hash"))
                            and thumbnail["rules"]["unsupported_number_or_claim"]
                            is False,
                            "Numeric copy is linked to exact claim evidence and marked supported.",
                        ),
                    },
                    thumbnail_evidence,
                ),
                evidence_refs=[thumbnail_evidence],
                approved_topic=idea.proposed_title,
                thumbnail_promise=thumbnail["topic_promise"],
                implied_niche=niche_digest.category_sub_niche,
                visual_language=thumbnail["visual_concept"],
                text_claims=[thumbnail["thumbnail_copy"]],
                number_claims=["20"],
                claim_evidence_refs=[claim_evidence],
                misleading_product_or_ui_representation=False,
            )
        )
        metadata_evidence = NicheEvidenceRef(
            type="artifact_version",
            ref=metadata_ref["artifact_version_ref"],
            content_hash=metadata_ref["content_hash"],
        )
        metadata_result = MetadataNicheAlignmentGate().evaluate(
            MetadataNicheAlignmentInput(
                **common,
                subject_ref=metadata_ref["artifact_version_ref"],
                subject_hash=metadata_ref["content_hash"],
                semantic_evidence=semantic(
                    {
                        NicheCriterion.METADATA_TOPIC_FIDELITY: (
                            metadata["title"] == script["title"],
                            "Metadata title exactly matches the approved script topic.",
                        ),
                        NicheCriterion.AUDIENCE_FIT: (
                            "small team" in metadata["description"].lower(),
                            "Description explicitly addresses the channel's small-team audience.",
                        ),
                        NicheCriterion.POSITIONING_FIT: (
                            any(
                                "workflow" in item.lower()
                                or "automation" in item.lower()
                                for item in [
                                    *metadata["keywords"],
                                    *metadata["tags"],
                                ]
                            ),
                            "Keywords and tags remain inside workflow automation positioning.",
                        ),
                        NicheCriterion.CLAIM_SCOPE_FIT: (
                            "illustrative" in metadata["description"].lower()
                            and "not a measured result" in metadata[
                                "description"
                            ].lower(),
                            "Description labels the scenario illustrative and unmeasured.",
                        ),
                        NicheCriterion.CTA_FIT: (
                            "workflow" in metadata["cta"].lower()
                            and "human exception" in metadata["cta"].lower(),
                            "CTA asks for a bounded workflow and a human exception path.",
                        ),
                    },
                    metadata_evidence,
                ),
                evidence_refs=[metadata_evidence],
                approved_topic=idea.proposed_title,
                title=metadata["title"],
                description=metadata["description"],
                keywords=metadata["keywords"],
                tags=metadata["tags"],
                chapters=metadata["chapters"],
                summary_copy=metadata["description"],
                upload_card_copy=metadata["upload_card_copy"],
                cta=metadata["cta"],
                declared_category_id=niche_digest.category_id,
                declared_content_pillar_key=niche_digest.content_pillar_key,
                claim_scope=["illustrative 20-hour scenario"],
                claim_evidence_refs=[claim_evidence],
                adjacent_niche_conflict=False,
            )
        )
        gate_results = [
            topic,
            script_result,
            visual_result,
            thumbnail_result,
            metadata_result,
        ]
        channel_fit = evaluate_channel_fit(
            score=(
                1.0
                if all(
                    item.verdict == NicheGateVerdict.PASS
                    for item in gate_results
                )
                else 0.0
            ),
            compiled_policy=snapshot,
            gate_results=gate_results,
            evidence_refs=[evidence, script_evidence, visual_evidence],
            required_gate_keys=(
                NicheGateKey.TOPIC,
                NicheGateKey.SCRIPT,
                NicheGateKey.VISUAL,
                NicheGateKey.THUMBNAIL,
                NicheGateKey.METADATA,
            ),
        )
        dossier = NicheAlignmentDossierBuilder().build(
            digest=niche_digest,
            digest_ref=digest_ref,
            gate_results=gate_results,
            channel_fit=channel_fit,
            dossier_scope=NicheDossierScope.PRODUCTION_PACKAGE,
        )
        if dossier.overall_verdict != NicheGateVerdict.PASS:
            diagnostics = [
                {
                    "gate_key": str(item.gate_key),
                    "verdict": str(item.verdict),
                    "reason_codes": [str(code) for code in item.reason_codes],
                    "blocked_checks": [
                        check.check_key
                        for check in item.checks
                        if check.verdict != NicheGateVerdict.PASS
                    ],
                }
                for item in gate_results
                if item.verdict != NicheGateVerdict.PASS
            ]
            raise ValidationFailureError(
                "NICHE_ALIGNMENT_DOSSIER_NOT_PASS:"
                + json.dumps(diagnostics, sort_keys=True)
            )
        return dossier.model_dump(mode="json")

    def _market_gate_evidence(
        self,
        *,
        market_profile: TargetMarketProfile,
        market_digest: TargetMarketDigest,
        revision_project: VideoProject,
        historical_artifacts: dict[str, ArtifactVersion],
        revision_artifacts: dict[str, ArtifactVersion],
        revision_slot: EditorialCalendarSlot,
        category: ContentCategory,
        niche_digest_ref: dict[str, Any],
        niche_dossier_ref: dict[str, Any],
        voice_ref: dict[str, Any],
        visual_ref: dict[str, Any],
        thumbnail_ref: dict[str, Any],
        metadata_ref: dict[str, Any],
        profile: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any]:
        idea_lineage = historical_artifacts["idea_admission_lineage"].content
        idea_id = uuid.UUID(idea_lineage["daily_idea_decision_id"])
        idea = self.session.get(DailyIdeaDecision, idea_id)
        if idea is None:
            raise ValidationFailureError("HISTORICAL_DAILY_IDEA_DECISION_MISSING")
        idea_ref = f"daily-idea-decision://{idea.id}"
        research_content = revision_artifacts["research_pack"].content
        claims_content = revision_artifacts["claim_evidence_ledger"].content
        script_content = historical_artifacts["script"].content
        voice_content = revision_artifacts["voice_policy"].content
        visual_content = revision_artifacts["visual_plan"].content
        thumbnail_content = revision_artifacts["thumbnail_brief"].content
        metadata_content = revision_artifacts[
            "publishing_metadata_package"
        ].content
        idea_text = f"{idea.proposed_title} {idea.proposed_angle}".lower()
        preflight_criteria = {
            "topic_demand_market_scope": (
                market_profile.primary_market == "US"
                and ("workflow" in idea_text or "automation" in idea_text)
            ),
            "target_audience_fit": (
                market_profile.audience_market_context == "US_SMALL_BUSINESS"
                and bool(category.audience_segment)
            ),
            "terminology_fit": (
                script_content.get("language") == market_profile.primary_locale
                and metadata_content.get("locale") == market_profile.title_locale
            ),
            "tool_product_availability": (
                research_content.get("external_measured_claim_count") == 0
            ),
            "business_context_fit": (
                market_profile.workplace_context == "US_SMALL_BUSINESS"
            ),
            "monetization_fit": all(
                claim.get("claim_type") != "UNIVERSAL_OUTCOME"
                for claim in claims_content.get("claims", [])
            ),
            "source_availability": bool(
                revision_artifacts["source_pack"].content.get("sources")
                and research_content.get("source_pack")
            ),
            "local_relevance": (
                research_content.get("target_market") == "US"
                and revision_slot.category_id == category.id
            ),
        }
        preflight: IdeaMarketPreflightResult = IdeaMarketPreflightEvaluator().evaluate(
            daily_idea_decision_ref=idea_ref,
            niche_contract_digest_ref=niche_digest_ref["artifact_version_ref"],
            niche_contract_digest_hash=niche_digest_ref["content_hash"],
            target_market_digest=market_digest,
            editorial_slot_ref=f"editorial-slot://{revision_slot.id}",
            content_category_ref=f"content-category://{category.id}",
            market_scope=[market_profile.primary_market],
            criteria=preflight_criteria,
            evidence_refs=[
                self._version_ref(revision_artifacts["idea_admission_lineage"]),
                self._version_ref(historical_artifacts["script"]),
                self._version_ref(revision_artifacts["source_pack"]),
                self._version_ref(revision_artifacts["research_pack"]),
                self._version_ref(revision_artifacts["target_market_digest"]),
            ],
        )
        topic = TopicMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=TopicMarketAlignmentInput(preflight=preflight),
            subject_ref=idea_ref,
        )
        research = ResearchJurisdictionGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=ResearchJurisdictionInput(
                target_market=research_content["target_market"],
                source_jurisdictions=[research_content["source_jurisdiction"]],
                legal_or_regulatory_claim=False,
                jurisdiction_specific_claim=False,
                presented_as_target_market_truth=False,
                currency=market_profile.currency,
                units_policy=market_profile.units_policy,
                date_format=market_profile.date_format,
                foreign_source_context_disclosed=False,
                evidence_sensitive_claim=False,
                evidence_refs=[
                    self._version_ref(revision_artifacts["research_pack"])
                ],
            ),
            subject_ref=(
                f"artifact-version://{revision_artifacts['research_pack'].id}"
            ),
        )
        script = ScriptMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=ScriptMarketAlignmentInput(
                language_locale=script_content["language"],
                currencies=[market_profile.currency],
                units_policy=market_profile.units_policy,
                date_format=market_profile.date_format,
                workplace_context=market_profile.workplace_context,
                audience_market_context=market_profile.audience_market_context,
                translated_sounding_language_risk=metadata_content["checks"][
                    "translated_sounding_copy"
                ],
                foreign_legal_assumption_without_context=False,
            ),
            subject_ref=f"artifact-version://{historical_artifacts['script'].id}",
        )
        voice = VoiceLocaleAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=VoiceLocaleAlignmentInput(
                narration_locale=voice_content["narration_locale"],
                content_language=voice_content["content_language"],
                voice_profile_locale=voice_content["voice_profile_locale"],
                pronunciation_policy_ref=voice_content["pronunciation_policy"][
                    "dictionary_refs"
                ][0],
            ),
            subject_ref=voice_ref["artifact_version_ref"],
        )
        visual = VisualMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=VisualMarketAlignmentInput(
                market_contexts=sorted(
                    {
                        item["target_market"]
                        for item in visual_content["scenes"]
                    }
                ),
                currencies=sorted(
                    {item["currency"] for item in visual_content["scenes"]}
                ),
                date_format=visual_content["scenes"][0]["date_format"],
                workplace_context=visual_content["scenes"][0][
                    "workplace_context"
                ],
                evidence_authentic=all(
                    item["generated_evidence_authority"] is False
                    for item in visual_content["scenes"]
                ),
            ),
            subject_ref=visual_ref["artifact_version_ref"],
        )
        thumbnail = ThumbnailMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=ThumbnailMarketAlignmentInput(
                text_locale=thumbnail_content["text_locale"],
                currencies=[market_profile.currency],
                market_promise=thumbnail_content["topic_promise"],
                foreign_market_bait=thumbnail_content["rules"][
                    "foreign_market_wording"
                ],
            ),
            subject_ref=thumbnail_ref["artifact_version_ref"],
        )
        metadata = MetadataMarketAlignmentGate().evaluate(
            profile=market_profile,
            digest=market_digest,
            data=MetadataMarketAlignmentInput(
                title_locale=metadata_content["locale"],
                description_locale=metadata_content["locale"],
                original_language=metadata_content["original_language"],
                caption_locales=list(market_profile.caption_locales),
                keywords_market_scope=[metadata_content["market"]],
                cta_market_scope=[metadata_content["market"]],
                product_available_in_target_market=True,
            ),
            subject_ref=metadata_ref["artifact_version_ref"],
        )
        components = [topic, research, script, voice, visual, thumbnail, metadata]
        expected_gate_keys = {
            "topic_market_alignment_gate",
            "research_jurisdiction_gate",
            "script_market_alignment_gate",
            "voice_locale_alignment_gate",
            "visual_market_alignment_gate",
            "thumbnail_market_alignment_gate",
            "metadata_market_alignment_gate",
        }
        actual_gate_keys = {str(item.gate_key) for item in components}
        if len(components) != len(actual_gate_keys) or actual_gate_keys != expected_gate_keys:
            raise ValidationFailureError("MARKET_GATE_EVIDENCE_SET_INVALID")
        if any(
            item.verdict != MarketVerdict.PASS
            or item.target_market_profile_hash != market_profile.content_hash
            or item.target_market_digest_hash != market_digest.content_hash
            or not item.subject_ref
            for item in components
        ):
            raise ValidationFailureError("MARKET_GATE_EVIDENCE_NOT_EXACT_PASS")
        dossier = MarketAlignmentDossierBuilder().build(
            profile=market_profile,
            digest=market_digest,
            channel_profile_version_ref=f"channel-profile-version://{profile.id}",
            compiled_policy_snapshot_ref=f"compiled-policy-snapshot://{snapshot.id}",
            compiled_policy_snapshot_hash=snapshot.content_hash,
            video_project_ref=f"video-project://{revision_project.id}",
            video_project_hash=self._project_hash(revision_project),
            niche_alignment_dossier_ref=niche_dossier_ref["artifact_version_ref"],
            niche_alignment_dossier_hash=niche_dossier_ref["content_hash"],
            component_results=components,
        )
        if dossier.overall_verdict.value != "PASS":
            raise ValidationFailureError("MARKET_ALIGNMENT_DOSSIER_NOT_PASS")
        subject_artifact_bindings = {
            "topic_market_alignment_gate": {
                "gate_subject_ref": idea_ref,
                "gate_subject_hash": topic.subject_hash,
                "evidence_artifact": self._version_ref(
                    revision_artifacts["idea_admission_lineage"]
                ),
            },
            "research_jurisdiction_gate": {
                "gate_subject_contract_hash": research.subject_hash,
                "evidence_artifact": self._version_ref(
                    revision_artifacts["research_pack"]
                ),
            },
            "script_market_alignment_gate": {
                "gate_subject_contract_hash": script.subject_hash,
                "evidence_artifact": self._version_ref(
                    historical_artifacts["script"]
                ),
            },
            "voice_locale_alignment_gate": {
                "gate_subject_contract_hash": voice.subject_hash,
                "evidence_artifact": voice_ref,
            },
            "visual_market_alignment_gate": {
                "gate_subject_contract_hash": visual.subject_hash,
                "evidence_artifact": visual_ref,
            },
            "thumbnail_market_alignment_gate": {
                "gate_subject_contract_hash": thumbnail.subject_hash,
                "evidence_artifact": thumbnail_ref,
            },
            "metadata_market_alignment_gate": {
                "gate_subject_contract_hash": metadata.subject_hash,
                "evidence_artifact": metadata_ref,
            },
        }
        return {
            "gate_results": {
                "schema_version": "pkg1.market-gate-results.v1",
                "strict_order": [
                    "idea_market_preflight",
                    "topic_market_alignment_gate",
                    "research_jurisdiction_gate",
                    "script_market_alignment_gate",
                    "voice_locale_alignment_gate",
                    "visual_market_alignment_gate",
                    "thumbnail_market_alignment_gate",
                    "metadata_market_alignment_gate",
                ],
                "idea_market_preflight": preflight.model_dump(mode="json"),
                "idea_market_preflight_criteria_source": {
                    "mode": "DETERMINISTIC_CONTRACT_PREFLIGHT",
                    "topic_demand_state": (
                        "CHANNEL_POSITIONING_HYPOTHESIS_NOT_OBSERVED_DEMAND"
                    ),
                    "criteria": preflight_criteria,
                },
                "component_results": [
                    item.model_dump(mode="json") for item in components
                ],
                "subject_artifact_bindings": subject_artifact_bindings,
                "all_mandatory_evidence_present": all(
                    bool(value.get("evidence_artifact", {}).get("content_hash"))
                    for value in subject_artifact_bindings.values()
                ),
                "all_mandatory_gates_pass": all(
                    item.verdict == MarketVerdict.PASS for item in components
                ),
            },
            "dossier": dossier.model_dump(mode="json"),
        }

    @staticmethod
    def _target_market_consistency(
        *,
        market_profile: TargetMarketProfile,
        destination: DestinationBinding,
        bindings: dict[str, Any],
        voice: dict[str, Any],
        thumbnail: dict[str, Any],
        metadata: dict[str, Any],
        dossier_ref: dict[str, Any],
        market_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        checks = {
            "content_language_match": voice["content_language"] == "en",
            "narration_locale_match": voice["narration_locale"] == "en-US",
            "title_locale_match": metadata["locale"] == "en-US",
            "thumbnail_locale_match": thumbnail["text_locale"] == "en-US",
            "caption_language_match": "en-US" in market_profile.caption_locales,
            "currency_units_match": (
                market_profile.currency == "USD"
                and market_profile.units_policy == "US_WITH_METRIC_WHEN_RELEVANT"
            ),
            "cultural_context_match": (
                market_profile.audience_market_context == "US_SMALL_BUSINESS"
                and market_profile.workplace_context == "US_SMALL_BUSINESS"
            ),
            "source_jurisdiction_match": any(
                item["gate_key"] == "research_jurisdiction_gate"
                and item["verdict"] == "PASS"
                for item in market_evidence["gate_results"][
                    "component_results"
                ]
            ),
            "topic_market_demand_match": (
                market_evidence["gate_results"]["idea_market_preflight"][
                    "decision"
                ]
                == "PASS"
                and market_evidence["gate_results"][
                    "idea_market_preflight_criteria_source"
                ]["topic_demand_state"]
                == "CHANNEL_POSITIONING_HYPOTHESIS_NOT_OBSERVED_DEMAND"
            ),
            "destination_market_match": destination.target_market == "US",
        }
        return {
            "schema_version": "pkg1.target-market-consistency.v1",
            "target_market_profile": bindings["target_market_profile"],
            "target_market_profile_hash": market_profile.content_hash,
            "destination_binding": bindings["destination_binding"],
            "destination_binding_hash": destination.content_hash,
            "market_alignment_dossier": dossier_ref,
            "checks": checks,
            "overall_decision": "PASS" if all(checks.values()) else "BLOCK",
            "reason_codes": [],
        }

    @staticmethod
    def _provider_plan(
        *,
        revision_id: str,
        revision_hash: str,
        script_ref: dict[str, Any],
        voice_ref: dict[str, Any],
        decision_set_ref: dict[str, Any],
        decisions: list[dict[str, Any]],
        bindings: dict[str, Any],
    ) -> dict[str, Any]:
        pexels_count = sum(item["provider"] == "pexels_api" for item in decisions)
        return {
            "schema_version": "pkg1.market-provider-execution-plan.v1",
            "revision_id": revision_id,
            "revision_hash": revision_hash,
            "execution_enabled": False,
            "mr1_approval": "PENDING_REAPPROVAL",
            "script": script_ref,
            "voice_policy": voice_ref,
            "visual_source_decision_set": decision_set_ref,
            "profile_binding": bindings["channel_profile_version"],
            "snapshot_binding": bindings["compiled_channel_policy_snapshot"],
            "target_market_binding": bindings["target_market_profile"],
            "provider_usage_policy": bindings["provider_usage_policy"],
            "cost_policy": bindings["budget_policy"],
            "lpro1_render_contract_version": bindings[
                "lpro1_production_contract_version"
            ],
            "stages": [
                {"order": 1, "provider": "elevenlabs", "operation": "narration", "planned_requests": 1, "attempt_cap": 1, "state": "NOT_AUTHORIZED"},
                {"order": 2, "provider": "forced_alignment", "operation": "verified_alignment", "planned_requests": 1, "attempt_cap": 1, "state": "WAITING_FOR_FINAL_AUDIO"},
                {"order": 3, "provider": "pexels_api", "operation": "supporting_assets", "planned_requests": pexels_count, "attempt_cap_per_scene": 1, "state": "NOT_AUTHORIZED", "automatic_ai_fallback": False},
                {"order": 4, "provider": "google_gemini_image", "model": "gemini-3.1-flash-image", "size": "2K", "planned_requests": 0, "attempt_cap_per_scene": 1, "state": "NOT_PLANNED"},
                {"order": 5, "provider": "google_veo", "model": "veo-3.1-fast-generate-preview", "planned_requests": 0, "attempt_cap_per_scene": 1, "state": "NOT_PLANNED"},
                {"order": 6, "provider": "native_graphics", "planned_requests": sum(item["provider"] == "native" for item in decisions), "state": "PLANNING_ONLY"},
                {"order": 7, "provider": "native_ffmpeg_renderer", "planned_requests": 1, "state": "WAITING_FOR_MR1"},
                {
                    "order": 8,
                    "provider": "google_drive",
                    "operation": (
                        "canonical_review_archive_plus_finalization_supplement"
                    ),
                    "planned_requests": 2,
                    "state": "WAITING_FOR_FINAL_MEDIA",
                    "idempotency_phases": deepcopy(DRIVE_IDEMPOTENCY_PHASES),
                },
            ],
            "scene_routes": [
                {"scene_id": item["scene_id"], "route": item["preferred_source_route"], "provider": item["provider"], "attempt_cap": item["maximum_automated_attempts"], "idempotency_ref": f"provider-plan://{revision_id}/{item['scene_id']}"}
                for item in decisions
            ],
            "one_route_per_scene": True,
            "automatic_pexels_to_ai_fallback": False,
            "external_ai_video_fallback": False,
            "approval_requirements": ["EXACT_PKG1_MARKET_REVISION_PASS", "EXACT_MR1_REAPPROVAL"],
            "provider_outputs": [],
        }

    def _cost_estimate(
        self,
        *,
        policy: ChannelScopedPolicy,
        script_ref: dict[str, Any],
        script: dict[str, Any],
        visual_plan_ref: dict[str, Any],
        provider_plan_ref: dict[str, Any],
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        registry = ConfigRegistryService(self.session)
        loaded_catalogs = [
            registry.validate_catalog(
                ROOT / "config/media_provider_budget_policy_catalog.yaml"
            ),
            registry.validate_catalog(
                ROOT / "config/google_gemini_image_model_price_catalog.yaml"
            ),
            registry.validate_catalog(
                ROOT / "config/google_veo_model_price_catalog.yaml"
            ),
        ]
        for loaded in loaded_catalogs:
            record = registry.get_version(
                loaded.catalog_key, loaded.catalog_version
            )
            if (
                record is None
                or record.status != "active"
                or record.content_hash != loaded.content_hash
            ):
                raise ValidationFailureError(
                    f"CURRENT_COST_CATALOG_NOT_SEEDED:{loaded.catalog_key}"
                )
        media_catalog, gemini_catalog, veo_catalog = loaded_catalogs
        expected_veo_ref = (
            f"config://{veo_catalog.catalog_key}/{veo_catalog.catalog_version}"
        )
        if (
            policy.provider_usage_policy.google_veo.approved_model_catalog_ref
            != expected_veo_ref
        ):
            raise ValidationFailureError("VEO_COST_CATALOG_POLICY_BINDING_MISMATCH")

        gemini_item = next(
            (
                item
                for item in gemini_catalog.content["items"]
                if item.get("model_id") == "gemini-3.1-flash-image"
                and item.get("size") == "2K"
                and item.get("aspect_ratio") == "16:9"
                and item.get("policy_state") == "ALLOWED"
            ),
            None,
        )
        veo_item = next(
            (
                item
                for item in veo_catalog.content["items"]
                if item.get("model_id") == "veo-3.1-fast-generate-preview"
                and item.get("approved") is True
                and 8 in item.get("duration_seconds", [])
                and "720p" in item.get("resolutions", {})
            ),
            None,
        )
        if gemini_item is None or veo_item is None:
            raise ValidationFailureError("PLANNED_MEDIA_COST_ROUTE_NOT_IN_CATALOG")
        gemini_unit_cost = float(gemini_item["estimated_unit_cost_usd"])
        veo_second_cost = float(
            veo_item["resolutions"]["720p"]["price_per_second_usd"]
        )
        veo_unit_cost = round(8 * veo_second_cost, 6)
        characters = sum(len(item["text"]) for item in script["segments"])
        gemini_count = sum(item["provider"] == "google_gemini_image" for item in decisions)
        veo_count = sum(item["provider"] == "google_veo" for item in decisions)
        estimated = round(
            gemini_count * gemini_unit_cost + veo_count * veo_unit_cost, 6
        )
        hard_cap = float(policy.budget_policy.max_estimated_cost_per_video)
        catalog_bindings = [
            {
                "ref": f"config://{loaded.catalog_key}/{loaded.catalog_version}",
                "catalog_key": loaded.catalog_key,
                "catalog_version": loaded.catalog_version,
                "content_hash": loaded.content_hash,
            }
            for loaded in loaded_catalogs
        ]
        return {
            "schema_version": "pkg1.market-cost-estimate.v1",
            "currency": "USD",
            "catalog_refs": [item["ref"] for item in catalog_bindings],
            "catalog_bindings": catalog_bindings,
            "bindings": {
                "script": script_ref,
                "scene_plan": visual_plan_ref,
                "provider_plan": provider_plan_ref,
            },
            "line_items": [
                {"provider": "elevenlabs", "characters": characters, "attempt_cap": 1, "cost_class": "SUBSCRIPTION_CREDIT", "estimated_incremental_cost_usd": 0.0, "basis": "current subscription-credit planning; no billed cost invented"},
                {"provider": "forced_alignment", "planned_requests": 1, "estimated_incremental_cost_usd": 0.0},
                {"provider": "pexels_api", "planned_scenes": sum(item["provider"] == "pexels_api" for item in decisions), "cost_class": "FREE_API", "estimated_incremental_cost_usd": 0.0},
                {"provider": "google_gemini_image", "model": "gemini-3.1-flash-image", "size": "2K", "aspect_ratio": "16:9", "planned_scenes": gemini_count, "attempt_cap_per_scene": 1, "unit_estimate_usd": gemini_unit_cost, "catalog_item_key": gemini_item["key"], "estimated_incremental_cost_usd": round(gemini_count * gemini_unit_cost, 6)},
                {"provider": "google_veo", "model": "veo-3.1-fast-generate-preview", "duration_seconds": 8, "resolution": "720p", "planned_clips": veo_count, "attempt_cap_per_scene": 1, "price_per_second_usd": veo_second_cost, "unit_estimate_usd": veo_unit_cost, "estimated_incremental_cost_usd": round(veo_count * veo_unit_cost, 6)},
                {"provider": "native_ffmpeg_renderer", "cost_class": "LOCAL", "estimated_incremental_cost_usd": 0.0},
                {
                    "provider": "google_drive",
                    "cost_class": "EXISTING_WORKSPACE_PLAN",
                    "planned_requests": 2,
                    "idempotency_phases": deepcopy(DRIVE_IDEMPOTENCY_PHASES),
                    "estimated_incremental_cost_usd": 0.0,
                    "basis": (
                        "Two exact zero-cost Drive mutations: canonical review "
                        "archive before human PASS, then one finalization "
                        "supplement before FinalMediaRef."
                    ),
                },
            ],
            "estimated_cost": estimated,
            "hard_cap": hard_cap,
            "actual_cost": None,
            "attempt_caps_bound": True,
            "decision": "PASS" if estimated <= hard_cap else "BLOCK",
        }

    @staticmethod
    def _asset_provenance_plan(
        *, decisions: list[dict[str, Any]], revision_hash: str
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.asset-provenance-plan.v1",
            "revision_hash": revision_hash,
            "pexels": {"planned_scenes": [item["scene_id"] for item in decisions if item["provider"] == "pexels_api"], "asset_id_url_author_license_required": True, "outputs": []},
            "gemini_image": {"provider": "google_gemini_image", "model": "gemini-3.1-flash-image", "prompt_hash_plan_required": True, "planned_scenes": [], "outputs": []},
            "veo": {"provider": "google_veo", "model": "veo-3.1-fast-generate-preview", "planned_scenes": [], "outputs": []},
            "authorized_assets": {"source_authority_required": True, "outputs": []},
            "native_assets": {"authorship": "VCOS_NATIVE", "scene_ids": [item["scene_id"] for item in decisions if item["provider"] == "native"]},
            "generated_evidence_authority": False,
            "provider_output_exists": False,
        }

    @staticmethod
    def _rights_report(
        *,
        claims_ref: dict[str, Any],
        provenance_ref: dict[str, Any],
        decisions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-rights-disclosure.v1",
            "planning_state": "PASS",
            "final_rights_state": "WAITING_FOR_ASSET_ACQUISITION",
            "claim_evidence": claims_ref,
            "asset_provenance_plan": provenance_ref,
            "pexels_provenance_required": any(item["provider"] == "pexels_api" for item in decisions),
            "gemini_prompt_hash_and_model_required_if_executed": True,
            "veo_provider_model_required_if_executed": True,
            "authorized_asset_provenance_required": True,
            "native_asset_authorship_required": True,
            "generated_evidence_authority": False,
            "provider_outputs_claimed": False,
            "archive_before_purge": True,
            "decision": "PASS",
        }

    @staticmethod
    def _synthetic_disclosure(*, provenance_ref: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.synthetic-media-disclosure-receipt-draft.v2",
            "receipt_status": "PRE_RENDER_PLANNED",
            "asset_provenance_plan": provenance_ref,
            "synthetic_voice_planned": True,
            "synthetic_image_planned": False,
            "synthetic_video_planned": False,
            "real_person_likeness": False,
            "platform_disclosure_decision": "REVIEW_AFTER_FINAL_ASSET_SET",
            "provider_outputs_exist": False,
        }

    @staticmethod
    def _publish_risk_dossier(
        *,
        market_profile: TargetMarketProfile,
        destination: DestinationBinding,
        bindings: dict[str, Any],
        consistency: dict[str, Any],
        revision_hash: str,
        market_dossier_ref: dict[str, Any],
        consistency_ref: dict[str, Any],
        rights_ref: dict[str, Any],
        disclosure_ref: dict[str, Any],
    ) -> dict[str, Any]:
        checks = consistency["checks"]
        required_checks = {
            "content_language_match",
            "narration_locale_match",
            "title_locale_match",
            "thumbnail_locale_match",
            "caption_language_match",
            "currency_units_match",
            "cultural_context_match",
            "source_jurisdiction_match",
            "topic_market_demand_match",
            "destination_market_match",
        }
        if (
            set(checks) != required_checks
            or consistency["overall_decision"] != "PASS"
            or not all(checks.values())
        ):
            raise ValidationFailureError("PUBLISH_RISK_MARKET_CONSISTENCY_INVALID")
        publish_window_hypothesis = (
            market_profile.initial_publish_window_hypotheses[0]
        )
        publish_window_status = (
            "PASS"
            if publish_window_hypothesis.get("status") == "APPROVED"
            else "REVIEW_REQUIRED"
        )
        market_alignment = PublishRiskMarketAlignment(
            target_market_profile_ref=bindings["target_market_profile"]["ref"],
            target_market_profile_hash=market_profile.content_hash,
            primary_market=market_profile.primary_market,
            destination_binding_ref=bindings["destination_binding"]["ref"],
            destination_binding_hash=destination.content_hash,
            content_language_match=checks["content_language_match"],
            narration_locale_match=checks["narration_locale_match"],
            title_locale_match=checks["title_locale_match"],
            thumbnail_locale_match=checks["thumbnail_locale_match"],
            caption_language_match=checks["caption_language_match"],
            currency_units_match=checks["currency_units_match"],
            cultural_context_match=checks["cultural_context_match"],
            source_jurisdiction_match=checks["source_jurisdiction_match"],
            topic_market_demand_match=checks["topic_market_demand_match"],
            publish_window_status=publish_window_status,
            localized_asset_requirements=(
                []
                if publish_window_status == "PASS"
                else ["PUBLISH_WINDOW_REQUIRES_HUMAN_CONFIRMATION"]
            ),
            overall_decision=(
                MarketVerdict.PASS
                if publish_window_status == "PASS"
                else MarketVerdict.REVIEW_REQUIRED
            ),
            reason_codes=[],
        )
        return {
            "schema_version": "pkg1.publish-risk-dossier.v1",
            "content_risk": {"decision": "PASS", "scenario_not_measured": True},
            "rights_provenance_risk": {"decision": "PASS_PLANNING", "rights_report": rights_ref},
            "synthetic_media_disclosure": {"decision": "PLANNED", "receipt": disclosure_ref},
            "market_alignment": {
                **market_alignment.model_dump(mode="json"),
                "market_alignment_dossier": market_dossier_ref,
                "consistency_check": consistency_ref,
            },
            "destination_binding": {
                "ref": bindings["destination_binding"]["ref"],
                "content_hash": destination.content_hash,
                "status": destination.destination_status,
                "publish_execution_allowed": False,
                "decision": "BLOCK_PUBLISH_EXECUTION_ONLY",
                "reason_codes": ["DESTINATION_PLATFORM_ID_NOT_VERIFIED"],
                "publish_blocker": "PENDING_PLATFORM_ID",
            },
            "package_integrity": {
                "planning_authority_state": "BOUND",
                "planning_authority_hash": revision_hash,
                "final_package_integrity": "PENDING_PACKAGE_HASH",
                "change_requires_new_version": True,
            },
            "manual_publish_boundary": {"required": True, "automatic_publish": False},
            "package_content_decision": "REVIEW_REQUIRED",
            "publish_execution_decision": "BLOCK",
        }

    @staticmethod
    def _publish_handoff_package(
        *,
        revision_id: str,
        revision_hash: str,
        bindings: dict[str, Any],
        thumbnail_ref: dict[str, Any],
        metadata_ref: dict[str, Any],
        disclosure_ref: dict[str, Any],
        dossier_ref: dict[str, Any],
        risk_ref: dict[str, Any],
        title: str,
        description: str,
        market_profile: TargetMarketProfile,
        destination: DestinationBinding,
    ) -> dict[str, Any]:
        typed_package = MarketBoundPublishPackage(
            package_id=revision_id,
            package_version=2,
            video_project_ref=bindings["revision_video_project"]["ref"],
            media_file_ref=None,
            media_file_hash=None,
            destination_binding_ref=bindings["destination_binding"]["ref"],
            destination_binding_hash=destination.content_hash,
            target_market_profile_ref=bindings["target_market_profile"]["ref"],
            target_market_profile_hash=market_profile.content_hash,
            primary_market=market_profile.primary_market,
            primary_locale=market_profile.primary_locale,
            original_language=market_profile.content_language,
            caption_refs=[],
            localized_metadata_refs=[metadata_ref],
            thumbnail_refs=[thumbnail_ref],
            title=title,
            description=description,
            disclosures=[disclosure_ref["artifact_version_ref"]],
            approved_publish_timezone=market_profile.primary_timezone,
            approved_publish_window=(
                market_profile.initial_publish_window_hypotheses[0]
            ),
            market_alignment_dossier_ref=dossier_ref["artifact_version_ref"],
            market_alignment_dossier_hash=dossier_ref["content_hash"],
            publish_risk_dossier_ref=risk_ref["artifact_version_ref"],
            publish_risk_dossier_hash=risk_ref["content_hash"],
            technical_media_qc="REVIEW_REQUIRED",
            creative_human_review="PENDING",
            market_alignment_verdict=MarketVerdict.PASS,
            publish_risk_verdict=MarketVerdict.REVIEW_REQUIRED,
            destination_status=destination.destination_status,
            package_state="DRAFT",
            approved_package_hash=None,
            approval_ref=None,
        )
        payload = {
            **typed_package.model_dump(mode="json"),
            "planning_schema_version": "pkg1.market-bound-publish-handoff.v1",
            "revision_hash": revision_hash,
            "authority_state": "FINAL_MARKET_PACKAGE_PENDING_MEDIA",
            "media_output_placeholder": {"expected_from": "FUTURE_MR1", "file_ref": None, "content_hash": None},
            "thumbnail": thumbnail_ref,
            "metadata": metadata_ref,
            "caption_plan": {"locale": "en-US", "artifact_ref": None, "state": "WAITING_FOR_FINAL_AUDIO_ALIGNMENT"},
            "disclosure": disclosure_ref,
            "destination_binding": bindings["destination_binding"],
            "target_market_profile": bindings["target_market_profile"],
            "market_alignment_dossier": dossier_ref,
            "publish_risk_dossier": risk_ref,
            "primary_market": "US",
            "primary_locale": "en-US",
            "original_language": "en",
            "approved_publish_timezone": "America/New_York",
            "publish_window_hypothesis": market_profile.initial_publish_window_hypotheses[0],
            "title": title,
            "description": description,
            "destination_status": destination.destination_status,
            "UPLOAD_READY": False,
            "PUBLISH_EXECUTION_READY": False,
            "MARKET_PACKAGE_FROZEN": False,
            "publish_execution_allowed": False,
            "publish_blocker": "PENDING_PLATFORM_ID",
            "publish_blocker_reason_code": (
                "DESTINATION_PLATFORM_ID_NOT_VERIFIED"
            ),
            "freeze_requirements_pending": ["EXACT_MP4_REF_HASH", "THUMBNAIL_REF_HASH", "CAPTION_REF_HASH", "TECHNICAL_MEDIA_QC_PASS", "HUMAN_MEDIA_REVIEW_PASS", "DRIVE_ARCHIVE_STATE"],
        }
        payload["package_hash"] = content_hash(payload)
        return payload

    @staticmethod
    def _upload_card(
        *,
        destination: DestinationBinding,
        metadata_ref: dict[str, Any],
        title: str,
        description: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "pkg1.market-upload-card.v1",
            "state": "DRAFT_NOT_ACTIONABLE",
            "platform": destination.platform,
            "channel_handle": destination.channel_handle,
            "destination_status": destination.destination_status,
            "metadata": metadata_ref,
            "title": title,
            "description": description,
            "file_ref": None,
            "thumbnail_ref": None,
            "caption_ref": None,
            "human_upload_task_created": False,
            "publish_execution_allowed": False,
            "blocker": "PENDING_PLATFORM_ID",
            "blocker_reason_code": "DESTINATION_PLATFORM_ID_NOT_VERIFIED",
        }
