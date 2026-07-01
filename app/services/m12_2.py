from __future__ import annotations

import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.contracts.m12_2 import (
    FirstScriptedVideoPackageAgentRunsRead,
    FirstScriptedVideoPackageRead,
    FirstScriptedVideoPackageRequest,
    FirstScriptedVideoPackageReviewRead,
    M122SPreflightRead,
    VideoGenerationBoundaryRead,
)
from app.contracts.m12_1 import PromptOutputValidationRequest, PromptRenderRequest
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationFailureError
from app.db.models import (
    AgentContextPackSnapshot,
    ChannelProfileVersion,
    ChannelWorkspace,
    CompiledChannelPolicySnapshot,
    Company,
    EffectiveChannelRuntimeContextSnapshot,
    FirstScriptedVideoPackage,
    PromptAuditSnapshot,
    VideoGenerationBoundary,
    VideoProject,
)
from app.services.m10_1 import LLMRouterConfigLoader, LLMRouterService
from app.services.m12 import ProviderReadinessService
from app.services.m12_1 import PromptRegistryService
from app.services.r3d3 import AgentContextPackBuilder
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler, build_effective_channel_runtime_digest


ROOT = Path(__file__).resolve().parents[2]
M12_2_REQUIRED_TAGS = ("m12-1-prompt-registry-contracts", "m12-1r-mock-dryrun-purge")
M12_2S_REQUIRED_TAGS = (
    "m12-1-prompt-registry-contracts",
    "m12-1r-mock-dryrun-purge",
    "m12-2-first-scripted-video-package",
    "m12-2r-publish-handoff-ledger",
    "m12-2p-channel-contract-init",
)
CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION = "Bổ sung hoặc compile lại ChannelProfileVersion trước khi chạy video package production."
NEEDS_CHANNEL_NEXT_ACTION = "Tạo channel và compile ChannelProfileVersion trước khi chạy M12.2."
M12_2S_NEEDS_COMPANY_NEXT_ACTION = "Tạo company trước, sau đó tạo channel."
M12_2S_NEEDS_CHANNEL_NEXT_ACTION = "Tạo channel bằng Channel Init và compile snapshot."
M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION = "Bổ sung field còn thiếu và compile lại ChannelProfileVersion."
NEEDS_RESEARCH_PACK_NEXT_ACTION = "Bổ sung research pack/source notes trước khi chạy video package production."
HUMAN_APPROVAL_REQUIRED = "Human final approval required before any media generation, upload, publish, or reupload."
MEDIA_PROVIDER_BOUNDARY_SUMMARY = (
    "Gói nội dung đã sẵn sàng tới bước tạo media, nhưng chưa thể generate video vì chưa cấu hình provider voice/render/AI hero."
)
MEDIA_PROVIDER_BOUNDARY_NEXT_ACTION = "Cấu hình Creatomate và ElevenLabs trước; Veo là optional cho hero shot."
FULL_REHEARSAL_MILESTONE = "M12.2S Full Agent + Real Ollama Rehearsal"

VISUAL_SOURCE_ALLOWLIST = {
    "DIAGRAM",
    "CARD",
    "SCREENSHOT",
    "EXISTING_ASSET",
    "VEO_HERO_CANDIDATE_ONLY",
    "CREATOMATE_CARD_CANDIDATE_ONLY",
}


@dataclass(frozen=True)
class PackageAgentStep:
    agent_key: str
    router_lane: str
    artifact_key: str
    requested_task_type: str


PACKAGE_AGENT_CHAIN: tuple[PackageAgentStep, ...] = (
    PackageAgentStep("ChannelAuthorityAgent", "cheap_structured", "admission_decision", "json_schema_output"),
    PackageAgentStep("TopicIdeaScoringAgent", "cheap_structured", "topic_scores", "json_schema_output"),
    PackageAgentStep("ResearchPackSummarizer", "long_context_text", "research_notes", "long_context_synthesis"),
    PackageAgentStep("ScriptPlanningAgent", "long_context_text", "script_outline", "long_form_script"),
    PackageAgentStep("ScriptWriterAgent", "long_context_text", "narration_script", "long_form_script"),
    PackageAgentStep("PublishingMetadataAgent", "cheap_structured", "metadata_package", "metadata_generation"),
    PackageAgentStep("VisualPlanningAgent", "visual_creative_review", "visual_plan", "visual_plan_review"),
    PackageAgentStep("UploadCardCopyAgent", "cheap_structured", "upload_card_copy", "metadata_generation"),
    PackageAgentStep("GatekeeperSoftReviewAgent", "gatekeeper_soft_review", "gatekeeper_review", "policy_soft_review"),
)

FULL_REHEARSAL_AGENT_CHAIN: tuple[PackageAgentStep, ...] = (
    PackageAgentStep("ChannelAuthorityAgent", "cheap_structured", "admission_decision", "json_schema_output"),
    PackageAgentStep("TopicIdeaScoringAgent", "cheap_structured", "topic_scores", "json_schema_output"),
    PackageAgentStep("ResearchPackSummarizer", "long_context_text", "research_notes", "long_context_synthesis"),
    PackageAgentStep("ScriptPlanningAgent", "long_context_text", "script_outline", "long_form_script"),
    PackageAgentStep("ScriptWriterAgent", "long_context_text", "narration_script", "long_form_script"),
    PackageAgentStep("PublishingMetadataAgent", "cheap_structured", "metadata_package", "metadata_generation"),
    PackageAgentStep("VisualPlanningAgent", "visual_creative_review", "visual_plan", "visual_plan_review"),
    PackageAgentStep("ThumbnailBriefAgent", "visual_creative_review", "thumbnail_brief", "thumbnail_direction_review"),
    PackageAgentStep("RightsDisclosureReviewer", "gatekeeper_soft_review", "rights_disclosure_review", "policy_soft_review"),
    PackageAgentStep("GatekeeperSoftReviewAgent", "gatekeeper_soft_review", "gatekeeper_review", "policy_soft_review"),
    PackageAgentStep("UploadCardCopyAgent", "cheap_structured", "upload_card_copy", "metadata_generation"),
    PackageAgentStep("ProviderReadinessSummaryAgent", "cheap_structured", "provider_readiness_summary", "json_schema_output"),
    PackageAgentStep("MediaQCExplanationAgent", "cheap_structured", "media_qc_explanation", "small_classification"),
)

FULL_REHEARSAL_REQUIRED_AGENT_KEYS = {
    "ChannelAuthorityAgent",
    "TopicIdeaScoringAgent",
    "ResearchPackSummarizer",
    "ScriptPlanningAgent",
    "ScriptWriterAgent",
    "PublishingMetadataAgent",
    "VisualPlanningAgent",
    "ThumbnailBriefAgent",
    "RightsDisclosureReviewer",
    "GatekeeperSoftReviewAgent",
    "UploadCardCopyAgent",
    "ProviderReadinessSummaryAgent",
    "MediaQCExplanationAgent",
}


def verify_m12_2_required_tags(repo_root: Path = ROOT) -> dict[str, Any]:
    return _verify_required_tags(M12_2_REQUIRED_TAGS, repo_root=repo_root)


def verify_m12_2s_required_tags(repo_root: Path = ROOT) -> dict[str, Any]:
    return _verify_required_tags(M12_2S_REQUIRED_TAGS, repo_root=repo_root)


def _verify_required_tags(required_tags: tuple[str, ...], *, repo_root: Path = ROOT) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["git", "tag", "--list"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "required_tags": list(required_tags),
            "missing_tags": list(required_tags),
            "error": str(exc),
        }
    tags = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
    missing = [tag for tag in required_tags if tag not in tags]
    return {
        "status": "PASS" if not missing else "BLOCKED",
        "required_tags": list(required_tags),
        "missing_tags": missing,
    }


class FirstScriptedVideoPackageService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        prompt_registry: PromptRegistryService | None = None,
        llm_router: LLMRouterService | None = None,
        repo_root: Path = ROOT,
    ):
        self.session = session
        self.settings = settings or get_settings()
        self.prompt_registry = prompt_registry or PromptRegistryService(session)
        self.llm_router = llm_router or LLMRouterService(session)
        self.repo_root = repo_root

    def create(self, data: FirstScriptedVideoPackageRequest) -> FirstScriptedVideoPackageRead:
        channel = self.session.get(ChannelWorkspace, data.channel_id)
        if channel is None:
            raise ValidationFailureError(f"BLOCKED: NEEDS_CHANNEL_INIT. {NEEDS_CHANNEL_NEXT_ACTION}")

        preflight = verify_m12_2_required_tags(self.repo_root)
        if preflight["status"] != "PASS":
            return self._read(self._create_package(
                channel_id=channel.id,
                status="BLOCKED",
                artifacts={"preflight": preflight},
                limitations=["M12.2 preflight tags are required before production prompt activation."],
                next_action=f"Khôi phục hoặc tạo tag còn thiếu: {', '.join(preflight['missing_tags'])}.",
            ))

        readiness_snapshot = ProviderReadinessService(self.session, self.settings).run()
        snapshot = self._active_snapshot(channel)
        if snapshot is None:
            return self._read(self._create_package(
                channel_id=channel.id,
                status="BLOCKED",
                provider_readiness_snapshot_id=readiness_snapshot.id,
                artifacts={"preflight": {"reason_code": "CHANNEL_POLICY_SNAPSHOT_MISSING"}},
                limitations=["Thiếu active CompiledChannelPolicySnapshot nên không được render prompt production."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        if profile_version is None:
            return self._read(self._create_package(
                channel_id=channel.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="BLOCKED",
                artifacts={"preflight": {"reason_code": "CHANNEL_PROFILE_VERSION_MISSING"}},
                limitations=["CompiledPolicySnapshot không còn ChannelProfileVersion hợp lệ."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        video_project_id = self._validate_optional_project(data.video_project_id, channel_id=channel.id, snapshot_id=snapshot.id)
        flag_block = self._flag_block(data)
        if flag_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="BLOCKED",
                artifacts={"runtime_mode": flag_block},
                limitations=["Runtime mode M12.2 chưa cho phép production prompt activation an toàn."],
                next_action=flag_block["next_action"],
            ))

        channel_contract = (
            snapshot.compiled_payload.get("channel_contract_json")
            if isinstance(snapshot.compiled_payload, dict) and isinstance(snapshot.compiled_payload.get("channel_contract_json"), dict)
            else {}
        )
        contract_block = self._channel_contract_block(channel_contract, snapshot)
        if contract_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"channel_contract_review": contract_block},
                limitations=["Channel Contract chưa đủ để agent production suy luận an toàn."],
                next_action=CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
            ))

        effective_context_snapshot = self._ensure_effective_context(video_project_id)
        effective_context_block = self._effective_context_block(effective_context_snapshot)
        if effective_context_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED" if effective_context_snapshot and effective_context_snapshot.compile_status == "REVIEW_REQUIRED" else "BLOCKED",
                artifacts={"effective_context": effective_context_block},
                limitations=["EffectiveChannelRuntimeContextSnapshot chưa PASS nên không chạy agent chain production."],
                next_action=effective_context_block["next_action"],
            ))

        if not data.topic:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"topic": {"status": "NEEDS_TOPIC"}},
                limitations=["Thiếu seed topic hoặc project topic; VCOS không tự bịa đề tài."],
                next_action="Bổ sung topic hoặc chọn VideoProject/candidate topic trước khi chạy M12.2.",
            ))

        if not (data.research_pack_text or data.research_pack_ref):
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"research_notes": {"status": "NEEDS_RESEARCH_PACK"}},
                limitations=["Thiếu research pack/source notes; ResearchPackSummarizer không được browse web hoặc bịa nguồn."],
                next_action=NEEDS_RESEARCH_PACK_NEXT_ACTION,
            ))

        llm_block = self._llm_readiness_block()
        if llm_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="NOT_CONFIGURED",
                artifacts={"llm_readiness": llm_block},
                limitations=["Real LLM package run chưa configured; không dùng mock fallback."],
                next_action=llm_block["next_action"],
            ))

        package_state = self._run_agent_chain(
            channel=channel,
            profile_version=profile_version,
            snapshot=snapshot,
            channel_contract=channel_contract,
            effective_context_snapshot=effective_context_snapshot,
            provider_readiness_snapshot_id=readiness_snapshot.id,
            data=data,
            video_project_id=video_project_id,
        )
        package = self._create_package(**package_state)
        return self._read(package)

    def rehearse_full(self, data: FirstScriptedVideoPackageRequest) -> FirstScriptedVideoPackageRead:
        preflight = self.preflight_full_rehearsal(data)
        channel = self.session.get(ChannelWorkspace, data.channel_id)
        if channel is None:
            raise ValidationFailureError(f"{preflight.status}: {preflight.next_action}")
        if preflight.status != "READY":
            artifacts: dict[str, Any] = {"preflight": preflight.model_dump(mode="json")}
            if preflight.status == "BLOCKED_NEEDS_CHANNEL_CONTRACT":
                artifacts["channel_contract_review"] = preflight.details.get("channel_contract_review", {})
            if preflight.status == "BLOCKED_ACTIVATION_FLAGS":
                artifacts["runtime_mode"] = preflight.details.get("runtime_mode", {})
            if preflight.status == "NOT_CONFIGURED":
                artifacts["llm_readiness"] = preflight.details.get("llm_readiness", {})
            return self._read(self._create_package(
                channel_id=channel.id,
                status="NOT_CONFIGURED" if preflight.status == "NOT_CONFIGURED" else "BLOCKED",
                channel_profile_version_id=preflight.channel_profile_version_id,
                compiled_policy_snapshot_id=preflight.compiled_policy_snapshot_id,
                artifacts=artifacts,
                limitations=["M12.2S preflight blocked full agent rehearsal before provider/readiness or LLM work."],
                next_action=preflight.next_action,
            ))

        readiness_snapshot = ProviderReadinessService(self.session, self.settings).run()
        snapshot = self._active_snapshot(channel)
        if snapshot is None:
            raise ValidationFailureError(f"BLOCKED_NEEDS_CHANNEL_CONTRACT: {M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION}")

        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        if profile_version is None:
            raise ValidationFailureError(f"BLOCKED_NEEDS_CHANNEL_CONTRACT: {M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION}")

        video_project_id = self._validate_optional_project(data.video_project_id, channel_id=channel.id, snapshot_id=snapshot.id)

        channel_contract = (
            snapshot.compiled_payload.get("channel_contract_json")
            if isinstance(snapshot.compiled_payload, dict) and isinstance(snapshot.compiled_payload.get("channel_contract_json"), dict)
            else {}
        )
        effective_context_snapshot = self._ensure_effective_context(video_project_id)
        effective_context_block = self._effective_context_block(effective_context_snapshot)
        if effective_context_block is not None:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED" if effective_context_snapshot and effective_context_snapshot.compile_status == "REVIEW_REQUIRED" else "BLOCKED",
                artifacts={"effective_context": effective_context_block},
                limitations=["EffectiveChannelRuntimeContextSnapshot chưa PASS nên không chạy full agent rehearsal."],
                next_action=effective_context_block["next_action"],
            ))

        if not data.topic:
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="BLOCKED",
                artifacts={"topic": {"status": "NEEDS_TOPIC"}},
                limitations=["Thiếu topic; VCOS không tự bịa đề tài để chạy agent production."],
                next_action="Bổ sung topic trước khi chạy full Ollama rehearsal.",
            ))

        if not (data.research_pack_text or data.research_pack_ref):
            return self._read(self._create_package(
                channel_id=channel.id,
                video_project_id=video_project_id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
                effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
                provider_readiness_snapshot_id=readiness_snapshot.id,
                status="REVIEW_REQUIRED",
                artifacts={"research_notes": {"status": "NEEDS_RESEARCH_PACK"}},
                limitations=["Thiếu research pack/source notes; VCOS không browse web hoặc bịa nguồn."],
                next_action=NEEDS_RESEARCH_PACK_NEXT_ACTION,
            ))

        package_state = self._run_full_rehearsal_agent_chain(
            channel=channel,
            profile_version=profile_version,
            snapshot=snapshot,
            channel_contract=channel_contract,
            effective_context_snapshot=effective_context_snapshot,
            provider_readiness_snapshot=readiness_snapshot.model_dump(mode="json"),
            data=data,
            video_project_id=video_project_id,
        )
        package = self._create_package(**package_state)
        if self._should_create_boundary(package.artifacts):
            boundary = self._create_generation_boundary(package=package, readiness_snapshot=readiness_snapshot.model_dump(mode="json"))
            package.artifacts = {
                **package.artifacts,
                "video_generation_boundary_ref": str(boundary.id),
                "video_generation_boundary_status": boundary.boundary_status,
            }
            self.session.flush()
        return self._read(package)

    def preflight_full_rehearsal(
        self,
        data: FirstScriptedVideoPackageRequest | None = None,
        *,
        channel_id: uuid.UUID | None = None,
    ) -> M122SPreflightRead:
        requested_channel_id = data.channel_id if data is not None else channel_id
        companies = list(self.session.scalars(select(Company).order_by(desc(Company.created_at))).all())
        if not companies:
            return M122SPreflightRead(
                status="BLOCKED_NEEDS_COMPANY",
                next_action=M12_2S_NEEDS_COMPANY_NEXT_ACTION,
                reason_codes=["COMPANY_MISSING"],
            )

        channel = self._select_preflight_channel(requested_channel_id)
        if channel is None:
            return M122SPreflightRead(
                status="BLOCKED_NEEDS_CHANNEL",
                next_action=M12_2S_NEEDS_CHANNEL_NEXT_ACTION,
                company_id=companies[0].id,
                reason_codes=["CHANNEL_MISSING"],
                details={"requested_channel_id": str(requested_channel_id) if requested_channel_id else None},
            )

        snapshot = self._active_snapshot(channel)
        if snapshot is None:
            return M122SPreflightRead(
                status="BLOCKED_NEEDS_CHANNEL_CONTRACT",
                next_action=M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION,
                company_id=channel.company_id,
                channel_id=channel.id,
                contract_status="MISSING",
                reason_codes=["ACTIVE_COMPILED_POLICY_SNAPSHOT_MISSING"],
                details={
                    "channel_contract_review": {
                        "status": "BLOCKED_NEEDS_CHANNEL_CONTRACT",
                        "reason_codes": ["ACTIVE_COMPILED_POLICY_SNAPSHOT_MISSING"],
                        "missing_or_invalid_fields": ["active_compiled_policy_snapshot"],
                        "next_action": M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION,
                    }
                },
            )

        profile_version = self.session.get(ChannelProfileVersion, snapshot.channel_profile_version_id)
        if profile_version is None:
            return M122SPreflightRead(
                status="BLOCKED_NEEDS_CHANNEL_CONTRACT",
                next_action=M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION,
                company_id=channel.company_id,
                channel_id=channel.id,
                compiled_policy_snapshot_id=snapshot.id,
                contract_status="MISSING",
                reason_codes=["CHANNEL_PROFILE_VERSION_MISSING"],
                details={
                    "channel_contract_review": {
                        "status": "BLOCKED_NEEDS_CHANNEL_CONTRACT",
                        "reason_codes": ["CHANNEL_PROFILE_VERSION_MISSING"],
                        "missing_or_invalid_fields": ["channel_profile_version"],
                        "next_action": M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION,
                    }
                },
            )

        channel_contract = (
            snapshot.compiled_payload.get("channel_contract_json")
            if isinstance(snapshot.compiled_payload, dict) and isinstance(snapshot.compiled_payload.get("channel_contract_json"), dict)
            else {}
        )
        contract_block = self._channel_contract_block(channel_contract, snapshot)
        if contract_block is not None:
            return M122SPreflightRead(
                status="BLOCKED_NEEDS_CHANNEL_CONTRACT",
                next_action=M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION,
                company_id=channel.company_id,
                channel_id=channel.id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                contract_status=str(channel_contract.get("contract_status") or "MISSING"),
                reason_codes=["CHANNEL_CONTRACT_INCOMPLETE"],
                details={"channel_contract_review": {**contract_block, "status": "BLOCKED_NEEDS_CHANNEL_CONTRACT", "next_action": M12_2S_NEEDS_CHANNEL_CONTRACT_NEXT_ACTION}},
            )

        tag_preflight = verify_m12_2s_required_tags(self.repo_root)
        if tag_preflight["status"] != "PASS":
            missing_tags = tag_preflight.get("missing_tags", [])
            return M122SPreflightRead(
                status="BLOCKED_REQUIRED_TAGS",
                next_action=f"Khôi phục hoặc tạo tag còn thiếu: {', '.join(missing_tags)}.",
                company_id=channel.company_id,
                channel_id=channel.id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                contract_status=str(channel_contract.get("contract_status") or "COMPLETE"),
                reason_codes=["M12_2S_REQUIRED_TAGS_MISSING"],
                details={"required_tags": tag_preflight},
            )

        flag_block = self._flag_block(data or FirstScriptedVideoPackageRequest(channel_id=channel.id))
        if flag_block is not None:
            return M122SPreflightRead(
                status="BLOCKED_ACTIVATION_FLAGS",
                next_action=flag_block["next_action"],
                company_id=channel.company_id,
                channel_id=channel.id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                contract_status=str(channel_contract.get("contract_status") or "COMPLETE"),
                reason_codes=["M12_2S_ACTIVATION_FLAGS_INVALID"],
                details={"runtime_mode": flag_block},
            )

        llm_block = self._llm_readiness_block(full_rehearsal=True)
        if llm_block is not None:
            return M122SPreflightRead(
                status="NOT_CONFIGURED",
                next_action=llm_block["next_action"],
                company_id=channel.company_id,
                channel_id=channel.id,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                contract_status=str(channel_contract.get("contract_status") or "COMPLETE"),
                reason_codes=["OLLAMA_OR_LLM_ROUTER_NOT_READY"],
                details={"llm_readiness": llm_block},
            )

        return M122SPreflightRead(
            status="READY",
            next_action="Có thể chạy M12.2S full agent rehearsal bằng Ollama.",
            company_id=channel.company_id,
            channel_id=channel.id,
            channel_profile_version_id=profile_version.id,
            compiled_policy_snapshot_id=snapshot.id,
            contract_status=str(channel_contract.get("contract_status") or "COMPLETE"),
        )

    def get(self, package_id: uuid.UUID) -> FirstScriptedVideoPackageRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        return self._read(package)

    def agent_runs(self, package_id: uuid.UUID) -> FirstScriptedVideoPackageAgentRunsRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        provider_attempt_refs = [
            str(ref["provider_attempt_id"])
            for ref in package.agent_run_refs
            if isinstance(ref, dict) and ref.get("provider_attempt_id")
        ]
        llm_run_refs = [
            str(ref["llm_run_snapshot_id"])
            for ref in package.agent_run_refs
            if isinstance(ref, dict) and ref.get("llm_run_snapshot_id")
        ]
        return FirstScriptedVideoPackageAgentRunsRead(
            package_id=package.id,
            package_status=package.package_status,  # type: ignore[arg-type]
            agent_runs=package.agent_run_refs,
            prompt_render_run_refs=[uuid.UUID(str(item)) for item in package.prompt_render_run_refs],
            prompt_audit_snapshot_refs=[uuid.UUID(str(item)) for item in package.prompt_audit_snapshot_refs],
            provider_attempt_refs=provider_attempt_refs,
            llm_run_snapshot_refs=llm_run_refs,
        )

    def generation_boundary(self, package_id: uuid.UUID) -> VideoGenerationBoundaryRead:
        boundary = self.session.scalars(
            select(VideoGenerationBoundary)
            .where(VideoGenerationBoundary.package_id == package_id)
            .order_by(desc(VideoGenerationBoundary.created_at))
            .limit(1)
        ).one_or_none()
        if boundary is None:
            raise NotFoundError(f"video generation boundary not found for package: {package_id}")
        return VideoGenerationBoundaryRead.model_validate(boundary)

    def review(self, package_id: uuid.UUID) -> FirstScriptedVideoPackageReviewRead:
        package = self.session.get(FirstScriptedVideoPackage, package_id)
        if package is None:
            raise NotFoundError(f"first scripted video package not found: {package_id}")
        return FirstScriptedVideoPackageReviewRead(
            package_id=package.id,
            package_status=package.package_status,  # type: ignore[arg-type]
            channel_binding={
                "channel_id": str(package.channel_id),
                "channel_profile_version_id": str(package.channel_profile_version_id) if package.channel_profile_version_id else None,
                "compiled_policy_snapshot_id": str(package.compiled_policy_snapshot_id) if package.compiled_policy_snapshot_id else None,
            },
            effective_context={
                "effective_context_snapshot_id": str(package.effective_context_snapshot_id) if package.effective_context_snapshot_id else None,
                "effective_context_hash": package.effective_context_hash,
                "snapshot_ref": package.artifacts.get("effective_context_snapshot_ref") or package.artifacts.get("effective_context"),
            },
            human_review_checklist=package.artifacts.get("human_review_checklist", {}),
            agent_outputs={key: value for key, value in package.artifacts.items() if key not in {"human_review_checklist"}},
            prompt_snapshots={
                "prompt_render_run_refs": package.prompt_render_run_refs,
                "prompt_audit_snapshot_refs": package.prompt_audit_snapshot_refs,
                "agent_run_refs": package.agent_run_refs,
                "agent_context_pack_refs": package.artifacts.get("agent_context_pack_refs", []),
            },
            provider_readiness_snapshot_ref=package.provider_readiness_snapshot_id,
            limitations=package.limitations,
            next_action=package.next_action,
        )

    def _run_agent_chain(
        self,
        *,
        channel: ChannelWorkspace,
        profile_version: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        channel_contract: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot_id: uuid.UUID,
        data: FirstScriptedVideoPackageRequest,
        video_project_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        package_id = uuid.uuid4()
        artifacts: dict[str, Any] = {
            "channel_contract_snapshot_ref": {
                "channel_id": str(channel.id),
                "channel_profile_version_id": str(profile_version.id),
                "compiled_policy_snapshot_id": str(snapshot.id),
                "channel_contract_status": channel_contract.get("contract_status"),
                "compiled_policy_content_hash": snapshot.content_hash,
            }
        }
        if effective_context_snapshot is not None:
            artifacts["effective_context_snapshot_ref"] = build_effective_channel_runtime_digest(effective_context_snapshot)
        agent_run_refs: list[dict[str, Any]] = []
        prompt_render_run_refs: list[str] = []
        prompt_audit_snapshot_refs: list[str] = []
        context_pack_refs: list[dict[str, Any]] = []
        status = "READY_FOR_HUMAN_REVIEW"
        next_action = HUMAN_APPROVAL_REQUIRED
        limitations: list[str] = [
            "M12.2 chỉ tạo scripted video package; không render video, không TTS, không upload/publish.",
            "Google Drive chỉ là archive/storage; VCOS DB vẫn là source of truth.",
        ]

        for step in PACKAGE_AGENT_CHAIN:
            context_result = self._build_agent_context_pack(
                package_id=package_id,
                step=step,
                data=data,
                artifacts=artifacts,
                snapshot=snapshot,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_state={"id": str(provider_readiness_snapshot_id)},
                milestone="M12.2 Production Prompt Activation",
            )
            if context_result.snapshot is not None:
                context_pack_refs.append(
                    {
                        "agent_key": step.agent_key,
                        "agent_context_pack_snapshot_id": str(context_result.snapshot.id),
                        "context_pack_hash": context_result.snapshot.context_pack_hash,
                    }
                )
                artifacts["agent_context_pack_refs"] = context_pack_refs
            if context_result.status != "OK" or context_result.context_pack is None:
                artifacts[step.artifact_key] = context_result.blocking_report
                status = "REVIEW_REQUIRED" if context_result.status == "REVIEW_REQUIRED" else "BLOCKED"
                next_action = "Sửa AgentContextPack trước khi gọi LLM."
                break
            task_payload = self._task_payload(
                step=step,
                data=data,
                context_pack=context_result.context_pack,
                channel=channel,
                snapshot=snapshot,
            )
            render = self.prompt_registry.render_prompt(
                PromptRenderRequest(
                    agent_key=step.agent_key,
                    router_lane=step.router_lane,
                    task_payload=task_payload,
                    channel_profile_version_id=profile_version.id,
                    compiled_policy_snapshot_id=snapshot.id,
                    channel_contract_json=channel_contract,
                    compiled_policy_snapshot_json=snapshot.compiled_payload,
                    market_locale_context_json=channel_contract.get("market_locale"),
                    evidence_refs=self._evidence_refs(data),
                    artifact_refs=self._artifact_refs(artifacts),
                    input_payload_ref=f"first-scripted-video-package:{channel.id}:{step.agent_key}",
                )
            )
            if context_result.snapshot is not None:
                AgentContextPackBuilder(self.session).link_prompt_render_run(
                    snapshot_id=context_result.snapshot.id,
                    prompt_render_run_id=render.prompt_render_run_id,
                    prompt_context_hash=render.prompt_context_hash,
                )
                self._record_prompt_budget_metrics(context_result.snapshot.id, render.rendered_messages)
            prompt_render_run_refs.append(str(render.prompt_render_run_id))
            prompt_audit_snapshot_refs.append(str(render.prompt_audit_snapshot_id))
            if render.status != "OK":
                artifacts[step.artifact_key] = render.blocking_output.model_dump(mode="json") if render.blocking_output else None
                status = "REVIEW_REQUIRED" if render.status == "REVIEW_REQUIRED" else "BLOCKED"
                next_action = CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION
                break

            route = self.llm_router.route(
                lane_name=render.router_lane,
                messages=[message.model_dump() for message in render.rendered_messages],
                requested_task_type=step.requested_task_type,
                response_format="json",
                correlation_id=f"m12-2-first-video-package-{step.agent_key}",
            )
            if route.status != "SUCCESS":
                agent_run_refs.append(self._agent_ref(step, render, route=route, validation=None))
                artifacts[step.artifact_key] = {"status": route.status, "reason_codes": route.reason_codes}
                status = "NOT_CONFIGURED" if route.status == "SKIPPED" else "ERROR"
                next_action = "Cấu hình real LLMRouter/Ollama trước khi chạy package production."
                break

            raw_output: str | dict[str, Any] | None = route.structured_output or route.content
            validation = self.prompt_registry.validate_output(
                PromptOutputValidationRequest(
                    agent_key=step.agent_key,
                    raw_output=raw_output or "",
                    prompt_render_run_id=render.prompt_render_run_id,
                )
            )
            audit_id = self._latest_audit_id(
                render.prompt_render_run_id,
                provider_refs=[
                    {
                        "route_attempt_id": str(route.route_attempt_id),
                        "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
                        "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
                    }
                ],
            )
            if audit_id is not None:
                prompt_audit_snapshot_refs.append(str(audit_id))
            agent_run_refs.append(self._agent_ref(step, render, route=route, validation=validation.model_dump(mode="json")))

            if validation.parsed_output is None or validation.status not in {"OK", "REVIEW_REQUIRED", "BLOCK"}:
                artifacts[step.artifact_key] = validation.validation_result
                status = "ERROR"
                next_action = "Sửa output schema/LLM response trước khi tiếp tục package."
                break
            if validation.status == "REVIEW_REQUIRED":
                artifacts[step.artifact_key] = {
                    "validation_result": validation.validation_result,
                    "parsed_output": validation.parsed_output,
                    "repair_attempts": validation.repair_attempts,
                }
                status = "REVIEW_REQUIRED"
                next_action = "Sửa output schema/LLM response trước khi tiếp tục package."
                break

            output = validation.parsed_output
            artifacts[step.artifact_key] = output.get("artifact") or {}
            envelope_status = output.get("status")
            if step.agent_key == "VisualPlanningAgent":
                visual_block = self._visual_plan_block(artifacts[step.artifact_key])
                if visual_block is not None:
                    artifacts["visual_plan_review"] = visual_block
                    status = "REVIEW_REQUIRED"
                    next_action = "Sửa visual plan để chỉ dùng nguồn DIAGRAM/CARD/SCREENSHOT/EXISTING_ASSET/VEO hoặc Creatomate candidate-only."
                    break
            if step.agent_key == "GatekeeperSoftReviewAgent":
                gatekeeper_result = self._gatekeeper_result(output)
                if gatekeeper_result == "BLOCK":
                    status = "BLOCKED"
                    next_action = output.get("next_action") or "Sửa rủi ro gatekeeper trước khi đưa package vào human review."
                elif gatekeeper_result == "REVIEW_REQUIRED":
                    status = "REVIEW_REQUIRED"
                    next_action = output.get("next_action") or HUMAN_APPROVAL_REQUIRED
                else:
                    status = "READY_FOR_HUMAN_REVIEW"
                    next_action = HUMAN_APPROVAL_REQUIRED
                break
            if envelope_status == "BLOCK":
                status = "BLOCKED"
                next_action = output.get("next_action") or "Agent upstream trả BLOCK; không tiếp tục downstream."
                break
            if envelope_status == "REVIEW_REQUIRED":
                status = "REVIEW_REQUIRED"
                next_action = output.get("next_action") or "Agent upstream cần human review; không tiếp tục downstream."
                break

        artifacts["human_review_checklist"] = self._human_review_checklist(artifacts, provider_readiness_snapshot_id)
        risk_summary = self._risk_summary(artifacts, status)
        return {
            "id": package_id,
            "channel_id": channel.id,
            "video_project_id": video_project_id,
            "channel_profile_version_id": profile_version.id,
            "compiled_policy_snapshot_id": snapshot.id,
            "effective_context_snapshot_id": effective_context_snapshot.id if effective_context_snapshot else None,
            "effective_context_hash": effective_context_snapshot.context_hash if effective_context_snapshot else None,
            "provider_readiness_snapshot_id": provider_readiness_snapshot_id,
            "status": status,
            "agent_run_refs": agent_run_refs,
            "prompt_render_run_refs": prompt_render_run_refs,
            "prompt_audit_snapshot_refs": sorted(set(prompt_audit_snapshot_refs)),
            "artifacts": artifacts,
            "limitations": limitations + risk_summary.get("limitations", []),
            "risk_limitations_summary": risk_summary,
            "next_action": next_action,
        }

    def _run_full_rehearsal_agent_chain(
        self,
        *,
        channel: ChannelWorkspace,
        profile_version: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        channel_contract: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        data: FirstScriptedVideoPackageRequest,
        video_project_id: uuid.UUID | None,
    ) -> dict[str, Any]:
        package_id = uuid.uuid4()
        artifacts: dict[str, Any] = {
            "channel_contract_snapshot_ref": {
                "channel_id": str(channel.id),
                "channel_profile_version_id": str(profile_version.id),
                "compiled_policy_snapshot_id": str(snapshot.id),
                "channel_contract_status": channel_contract.get("contract_status"),
                "compiled_policy_content_hash": snapshot.content_hash,
            },
            "runtime_guard": {
                "real_ollama_agent_run": True,
                "llm_router_only": True,
                "no_media_provider_calls": True,
                "no_upload_or_publish": True,
                "old_provider_smoke_disabled": True,
            },
        }
        if effective_context_snapshot is not None:
            artifacts["effective_context_snapshot_ref"] = build_effective_channel_runtime_digest(effective_context_snapshot)
        agent_run_refs: list[dict[str, Any]] = []
        prompt_render_run_refs: list[str] = []
        prompt_audit_snapshot_refs: list[str] = []
        context_pack_refs: list[dict[str, Any]] = []
        status = "READY_FOR_MEDIA_PROVIDERS"
        next_action = HUMAN_APPROVAL_REQUIRED
        limitations: list[str] = [
            "M12.2S chỉ chạy agent text/review bằng Ollama; không generate media, không TTS, không upload/publish.",
            "Veo/ElevenLabs/Creatomate chỉ xuất hiện trong readiness/boundary, không được gọi runtime.",
        ]

        for step in FULL_REHEARSAL_AGENT_CHAIN:
            result = self._execute_rehearsal_agent_step(
                package_id=package_id,
                step=step,
                data=data,
                artifacts=artifacts,
                channel=channel,
                profile_version=profile_version,
                snapshot=snapshot,
                channel_contract=channel_contract,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_snapshot=provider_readiness_snapshot,
                agent_run_refs=agent_run_refs,
                prompt_render_run_refs=prompt_render_run_refs,
                prompt_audit_snapshot_refs=prompt_audit_snapshot_refs,
                context_pack_refs=context_pack_refs,
            )
            if result["stop_status"] is not None:
                status = result["stop_status"]
                next_action = result["next_action"] or next_action
                if step.agent_key == "GatekeeperSoftReviewAgent":
                    rewrite = self._maybe_run_script_rewrite(
                        gatekeeper_output=result.get("parsed_output"),
                        package_id=package_id,
                        data=data,
                        artifacts=artifacts,
                        channel=channel,
                        profile_version=profile_version,
                        snapshot=snapshot,
                        channel_contract=channel_contract,
                        effective_context_snapshot=effective_context_snapshot,
                        provider_readiness_snapshot=provider_readiness_snapshot,
                        agent_run_refs=agent_run_refs,
                        prompt_render_run_refs=prompt_render_run_refs,
                        prompt_audit_snapshot_refs=prompt_audit_snapshot_refs,
                        context_pack_refs=context_pack_refs,
                    )
                    if rewrite["ran"]:
                        status = rewrite["stop_status"] or "REVIEW_REQUIRED"
                        next_action = rewrite["next_action"] or "Review script rewrite trước khi chạy lại gatekeeper."
                break
            if step.agent_key == "GatekeeperSoftReviewAgent":
                agent_run_refs.append(self._safe_skip_ref("ScriptRewriteAgent", "Gatekeeper PASS; validation không yêu cầu rewrite."))

        artifacts["human_review_checklist"] = self._human_review_checklist(artifacts, provider_readiness_snapshot_id=uuid.UUID(provider_readiness_snapshot["id"]))
        risk_summary = self._risk_summary(artifacts, status)
        return {
            "id": package_id,
            "channel_id": channel.id,
            "video_project_id": video_project_id,
            "channel_profile_version_id": profile_version.id,
            "compiled_policy_snapshot_id": snapshot.id,
            "effective_context_snapshot_id": effective_context_snapshot.id if effective_context_snapshot else None,
            "effective_context_hash": effective_context_snapshot.context_hash if effective_context_snapshot else None,
            "provider_readiness_snapshot_id": uuid.UUID(provider_readiness_snapshot["id"]),
            "status": status,
            "agent_run_refs": agent_run_refs,
            "prompt_render_run_refs": prompt_render_run_refs,
            "prompt_audit_snapshot_refs": sorted(set(prompt_audit_snapshot_refs)),
            "artifacts": artifacts,
            "limitations": limitations + risk_summary.get("limitations", []),
            "risk_limitations_summary": risk_summary,
            "next_action": next_action,
        }

    def _execute_rehearsal_agent_step(
        self,
        *,
        package_id: uuid.UUID,
        step: PackageAgentStep,
        data: FirstScriptedVideoPackageRequest,
        artifacts: dict[str, Any],
        channel: ChannelWorkspace,
        profile_version: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        channel_contract: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        agent_run_refs: list[dict[str, Any]],
        prompt_render_run_refs: list[str],
        prompt_audit_snapshot_refs: list[str],
        context_pack_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        context_result = self._build_agent_context_pack(
            package_id=package_id,
            step=step,
            data=data,
            artifacts=artifacts,
            snapshot=snapshot,
            effective_context_snapshot=effective_context_snapshot,
            provider_readiness_state=provider_readiness_snapshot,
            milestone=FULL_REHEARSAL_MILESTONE,
            required_stop_at="video_generation",
        )
        if context_result.snapshot is not None:
            context_pack_refs.append(
                {
                    "agent_key": step.agent_key,
                    "agent_context_pack_snapshot_id": str(context_result.snapshot.id),
                    "context_pack_hash": context_result.snapshot.context_pack_hash,
                }
            )
            artifacts["agent_context_pack_refs"] = context_pack_refs
        if context_result.status != "OK" or context_result.context_pack is None:
            artifacts[step.artifact_key] = context_result.blocking_report
            return {
                "stop_status": "REVIEW_REQUIRED" if context_result.status == "REVIEW_REQUIRED" else "BLOCKED",
                "next_action": "Sửa AgentContextPack trước khi gọi LLM.",
                "parsed_output": None,
            }
        task_payload = self._full_rehearsal_task_payload(
            step=step,
            data=data,
            context_pack=context_result.context_pack,
            channel=channel,
            snapshot=snapshot,
        )
        render = self.prompt_registry.render_prompt(
            PromptRenderRequest(
                agent_key=step.agent_key,
                router_lane=step.router_lane,
                task_payload=task_payload,
                channel_profile_version_id=profile_version.id,
                compiled_policy_snapshot_id=snapshot.id,
                channel_contract_json=channel_contract,
                compiled_policy_snapshot_json=snapshot.compiled_payload,
                market_locale_context_json=channel_contract.get("market_locale"),
                evidence_refs=self._evidence_refs(data),
                artifact_refs=self._artifact_refs(artifacts),
                input_payload_ref=f"full-agent-rehearsal:{channel.id}:{step.agent_key}",
                )
            )
        if context_result.snapshot is not None:
            AgentContextPackBuilder(self.session).link_prompt_render_run(
                snapshot_id=context_result.snapshot.id,
                prompt_render_run_id=render.prompt_render_run_id,
                prompt_context_hash=render.prompt_context_hash,
            )
            self._record_prompt_budget_metrics(context_result.snapshot.id, render.rendered_messages)
        prompt_render_run_refs.append(str(render.prompt_render_run_id))
        prompt_audit_snapshot_refs.append(str(render.prompt_audit_snapshot_id))
        if render.status != "OK":
            artifacts[step.artifact_key] = render.blocking_output.model_dump(mode="json") if render.blocking_output else None
            return {
                "stop_status": "REVIEW_REQUIRED" if render.status == "REVIEW_REQUIRED" else "BLOCKED",
                "next_action": CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
                "parsed_output": None,
            }

        route = self.llm_router.route(
            lane_name=render.router_lane,
            messages=[message.model_dump() for message in render.rendered_messages],
            requested_task_type=step.requested_task_type,
            response_format="json",
            correlation_id=f"m12-2s-full-agent-rehearsal-{step.agent_key}",
        )
        if route.status != "SUCCESS":
            agent_run_refs.append(self._agent_ref(step, render, route=route, validation=None))
            artifacts[step.artifact_key] = {"status": route.status, "reason_codes": route.reason_codes}
            return {
                "stop_status": "NOT_CONFIGURED" if route.status == "SKIPPED" else "ERROR",
                "next_action": "Cấu hình real Ollama/LLMRouter trước khi chạy full agent rehearsal.",
                "parsed_output": None,
            }

        raw_output: str | dict[str, Any] | None = route.structured_output or route.content
        validation = self.prompt_registry.validate_output(
            PromptOutputValidationRequest(
                agent_key=step.agent_key,
                raw_output=raw_output or "",
                prompt_render_run_id=render.prompt_render_run_id,
            )
        )
        audit_id = self._latest_audit_id(
            render.prompt_render_run_id,
            provider_refs=[
                {
                    "route_attempt_id": str(route.route_attempt_id),
                    "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
                    "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
                }
            ],
        )
        if audit_id is not None:
            prompt_audit_snapshot_refs.append(str(audit_id))
        agent_run_refs.append(self._agent_ref(step, render, route=route, validation=validation.model_dump(mode="json")))

        validation_is_structurally_valid = bool(validation.validation_result.get("valid")) if isinstance(validation.validation_result, dict) else False
        if validation.parsed_output is None or validation.status not in {"OK", "REVIEW_REQUIRED", "BLOCK"}:
            artifacts[step.artifact_key] = validation.validation_result
            return {
                "stop_status": "ERROR",
                "next_action": "Sửa output schema/LLM response trước khi tiếp tục full rehearsal.",
                "parsed_output": validation.parsed_output,
            }
        if validation.status == "REVIEW_REQUIRED" and not validation_is_structurally_valid:
            artifacts[step.artifact_key] = {
                "validation_result": validation.validation_result,
                "parsed_output": validation.parsed_output,
                "repair_attempts": validation.repair_attempts,
            }
            return {
                "stop_status": "REVIEW_REQUIRED",
                "next_action": "Sửa output schema/LLM response trước khi tiếp tục full rehearsal.",
                "parsed_output": validation.parsed_output,
            }

        output = validation.parsed_output
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        if step.agent_key == "ProviderReadinessSummaryAgent" and not artifact:
            artifact = self._provider_readiness_summary_artifact(provider_readiness_snapshot, output)
        artifacts[step.artifact_key] = artifact
        agent_block = self._full_rehearsal_artifact_block(step.agent_key, artifacts[step.artifact_key])
        if agent_block is not None:
            artifacts[f"{step.artifact_key}_review"] = agent_block
            return {"stop_status": "REVIEW_REQUIRED", "next_action": agent_block["next_action"], "parsed_output": output}

        if step.agent_key == "GatekeeperSoftReviewAgent":
            gatekeeper_result = self._gatekeeper_result(output)
            if gatekeeper_result == "BLOCK":
                return {
                    "stop_status": "BLOCKED",
                    "next_action": output.get("next_action") or "Sửa rủi ro gatekeeper trước khi tới media boundary.",
                    "parsed_output": output,
                }
            if gatekeeper_result == "REVIEW_REQUIRED":
                return {
                    "stop_status": "REVIEW_REQUIRED",
                    "next_action": output.get("next_action") or HUMAN_APPROVAL_REQUIRED,
                    "parsed_output": output,
                }
            return {"stop_status": None, "next_action": None, "parsed_output": output}

        envelope_status = output.get("status")
        if envelope_status == "BLOCK":
            if step.agent_key == "ProviderReadinessSummaryAgent":
                artifacts[f"{step.artifact_key}_review"] = {
                    "status": "BLOCK",
                    "source": "agent_envelope",
                    "agent_key": step.agent_key,
                    "expected_boundary_block": True,
                    "reason_codes": ["PROVIDER_GAP_DEFERRED_TO_VIDEO_GENERATION_BOUNDARY"],
                    "next_action": output.get("next_action"),
                }
                return {"stop_status": None, "next_action": None, "parsed_output": output}
            return {
                "stop_status": "BLOCKED",
                "next_action": output.get("next_action") or "Agent upstream trả BLOCK; không tiếp tục downstream.",
                "parsed_output": output,
            }
        if envelope_status == "REVIEW_REQUIRED":
            artifacts[f"{step.artifact_key}_review"] = {
                "status": "REVIEW_REQUIRED",
                "source": "agent_envelope",
                "agent_key": step.agent_key,
                "next_action": output.get("next_action"),
            }
            return {
                "stop_status": None,
                "next_action": None,
                "parsed_output": output,
            }
        return {"stop_status": None, "next_action": None, "parsed_output": output}

    def _provider_readiness_summary_artifact(
        self,
        provider_readiness_snapshot: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        providers: dict[str, dict[str, Any]] = {}
        for summary in provider_readiness_snapshot.get("provider_summaries", []):
            if not isinstance(summary, dict) or not summary.get("provider_key"):
                continue
            provider_key = str(summary["provider_key"]).lower()
            if provider_key not in {"elevenlabs", "creatomate", "google-vertex-veo", "cloud-final-renderer"}:
                continue
            providers[provider_key] = {
                "readiness_state": summary.get("readiness_state"),
                "missing_env_keys": summary.get("missing_env_keys") or [],
                "reason_codes": summary.get("reason_codes") or [],
                "next_action": summary.get("next_action"),
            }
        return {
            "providers": providers,
            "summary_status": output.get("status"),
            "next_action": output.get("next_action"),
            "operator_summary_vi": output.get("operator_summary_vi"),
        }

    def _maybe_run_script_rewrite(
        self,
        *,
        gatekeeper_output: dict[str, Any] | None,
        package_id: uuid.UUID,
        data: FirstScriptedVideoPackageRequest,
        artifacts: dict[str, Any],
        channel: ChannelWorkspace,
        profile_version: ChannelProfileVersion,
        snapshot: CompiledChannelPolicySnapshot,
        channel_contract: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        agent_run_refs: list[dict[str, Any]],
        prompt_render_run_refs: list[str],
        prompt_audit_snapshot_refs: list[str],
        context_pack_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not _needs_script_rewrite(gatekeeper_output):
            agent_run_refs.append(self._safe_skip_ref("ScriptRewriteAgent", "Gatekeeper REVIEW_REQUIRED nhưng không yêu cầu rewrite."))
            return {"ran": False, "stop_status": None, "next_action": None}
        rewrite_step = PackageAgentStep("ScriptRewriteAgent", "long_context_text", "script_rewrite", "deep_rewrite")
        result = self._execute_rehearsal_agent_step(
            package_id=package_id,
            step=rewrite_step,
            data=data,
            artifacts=artifacts,
            channel=channel,
            profile_version=profile_version,
            snapshot=snapshot,
            channel_contract=channel_contract,
            effective_context_snapshot=effective_context_snapshot,
            provider_readiness_snapshot=provider_readiness_snapshot,
            agent_run_refs=agent_run_refs,
            prompt_render_run_refs=prompt_render_run_refs,
            prompt_audit_snapshot_refs=prompt_audit_snapshot_refs,
            context_pack_refs=context_pack_refs,
        )
        return {"ran": True, "stop_status": result["stop_status"], "next_action": result["next_action"]}

    def _safe_skip_ref(self, agent_key: str, reason: str) -> dict[str, Any]:
        return {
            "agent_key": agent_key,
            "route_status": "SKIPPED_SAFE",
            "skip_reason": reason,
            "llm_router_only": True,
            "provider_attempt_id": None,
            "llm_run_snapshot_id": None,
        }

    def _active_snapshot(self, channel: ChannelWorkspace) -> CompiledChannelPolicySnapshot | None:
        if channel.active_policy_snapshot_id is None:
            return None
        snapshot = self.session.get(CompiledChannelPolicySnapshot, channel.active_policy_snapshot_id)
        if snapshot is None or snapshot.channel_workspace_id != channel.id:
            return None
        return snapshot if snapshot.status == "active" else None

    def _select_preflight_channel(self, channel_id: uuid.UUID | None) -> ChannelWorkspace | None:
        if channel_id is not None:
            return self.session.get(ChannelWorkspace, channel_id)
        channels = list(
            self.session.scalars(
                select(ChannelWorkspace).order_by(desc(ChannelWorkspace.created_at))
            ).all()
        )
        active_channels = [channel for channel in channels if channel.status == "active"]
        return active_channels[0] if active_channels else (channels[0] if channels else None)

    def _validate_optional_project(
        self,
        video_project_id: uuid.UUID | None,
        *,
        channel_id: uuid.UUID,
        snapshot_id: uuid.UUID,
    ) -> uuid.UUID | None:
        if video_project_id is None:
            return None
        project = self.session.get(VideoProject, video_project_id)
        if project is None:
            raise NotFoundError(f"video project not found: {video_project_id}")
        if project.channel_workspace_id != channel_id:
            raise ValidationFailureError("video project does not belong to selected channel")
        if project.policy_snapshot_id != snapshot_id:
            raise ValidationFailureError("video project is not bound to the active compiled policy snapshot")
        return project.id

    def _ensure_effective_context(
        self,
        video_project_id: uuid.UUID | None,
    ) -> EffectiveChannelRuntimeContextSnapshot | None:
        if video_project_id is None:
            return None
        return EffectiveChannelRuntimeContextCompiler(self.session).ensure_for_project(video_project_id)

    def _effective_context_block(
        self,
        snapshot: EffectiveChannelRuntimeContextSnapshot | None,
    ) -> dict[str, Any] | None:
        if snapshot is None:
            return {
                "status": "NEEDS_EFFECTIVE_CONTEXT",
                "compile_status": "MISSING",
                "effective_context_snapshot_id": None,
                "context_hash": None,
                "reason_codes": ["EFFECTIVE_CONTEXT_SNAPSHOT_MISSING"],
                "next_action": "Chọn VideoProject đã có EffectiveChannelRuntimeContextSnapshot PASS trước khi chạy agent package.",
            }
        if snapshot.compile_status == "PASS":
            return None
        status = snapshot.compile_status
        return {
            "status": "NEEDS_EFFECTIVE_CONTEXT" if status == "BLOCK" else "REVIEW_REQUIRED",
            "compile_status": status,
            "effective_context_snapshot_id": str(snapshot.id),
            "context_hash": snapshot.context_hash,
            "reason_codes": snapshot.reason_codes_json,
            "next_action": "Sửa Channel Contract/category/character scope rồi compile lại EffectiveChannelRuntimeContextSnapshot.",
        }

    def _channel_contract_block(
        self,
        channel_contract: dict[str, Any],
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any] | None:
        missing: list[str] = []
        if not channel_contract:
            missing.append("channel_contract_json")
        if not snapshot.compiled_payload:
            missing.append("compiled_policy_snapshot_json")
        contract_status = channel_contract.get("contract_status")
        market = channel_contract.get("market_locale") if isinstance(channel_contract.get("market_locale"), dict) else {}
        market_status = market.get("market_locale_context_status")
        if contract_status != "COMPLETE":
            missing.append(f"contract_status:{contract_status or 'MISSING'}")
        if market_status != "KNOWN":
            missing.append(f"market_locale_context_status:{market_status or 'MISSING'}")
        if not missing:
            return None
        return {
            "status": "REVIEW_REQUIRED",
            "reason_codes": ["CHANNEL_CONTRACT_INCOMPLETE"],
            "missing_or_invalid_fields": sorted(set(missing)),
            "next_action": CHANNEL_CONTRACT_PACKAGE_NEXT_ACTION,
        }

    def _flag_block(self, data: FirstScriptedVideoPackageRequest) -> dict[str, Any] | None:
        failures: list[str] = []
        if not self.settings.production_prompt_activation_enabled:
            failures.append("VCOS_ENABLE_PRODUCTION_PROMPT_ACTIVATION")
        if not self.settings.media_provider_calls_disabled or not data.no_media:
            failures.append("VCOS_DISABLE_MEDIA_PROVIDER_CALLS")
        if not self.settings.upload_and_publish_disabled or not data.human_review_only:
            failures.append("VCOS_DISABLE_UPLOAD_AND_PUBLISH")
        if not self.settings.old_provider_smoke_disabled:
            failures.append("VCOS_DISABLE_OLD_PROVIDER_SMOKE")
        if not failures:
            return None
        return {
            "status": "BLOCKED",
            "missing_or_invalid_flags": failures,
            "next_action": "Bật đúng M12.2 activation flags và giữ media/upload/publish disabled.",
        }

    def _llm_readiness_block(self, *, full_rehearsal: bool = False) -> dict[str, Any] | None:
        failures: list[str] = []
        if not self.settings.real_llm_package_run_enabled:
            failures.append("VCOS_ENABLE_REAL_LLM_PACKAGE_RUN")
        if full_rehearsal and not self.settings.real_ollama_agent_run_enabled:
            failures.append("VCOS_ENABLE_REAL_OLLAMA_AGENT_RUN")
        if not self.settings.llm_real_execution_enabled:
            failures.append("VCOS_LLM_REAL_EXECUTION_ENABLED")
        if self.settings.llm_provider.lower() != "ollama":
            failures.append("VCOS_LLM_PROVIDER")
        lanes = LLMRouterConfigLoader(self.session).list_lanes(profile_key="default")
        lane_names = {lane.lane_name for lane in lanes}
        required_chain = FULL_REHEARSAL_AGENT_CHAIN if full_rehearsal else PACKAGE_AGENT_CHAIN
        required_lanes = {step.router_lane for step in required_chain}
        missing_lanes = sorted(required_lanes - lane_names)
        if missing_lanes:
            failures.extend(f"LLM_ROUTER_LANE:{lane}" for lane in missing_lanes)
        if not failures:
            return None
        return {
            "status": "NOT_CONFIGURED",
            "reason_codes": ["LLM_PROVIDER_NOT_CONFIGURED"],
            "missing_or_invalid_flags": sorted(set(failures)),
            "next_action": "Cấu hình Ollama/LLMRouter real execution trước khi chạy video package production.",
        }

    def _build_agent_context_pack(
        self,
        *,
        package_id: uuid.UUID,
        step: PackageAgentStep,
        data: FirstScriptedVideoPackageRequest,
        artifacts: dict[str, Any],
        snapshot: CompiledChannelPolicySnapshot,
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_state: dict[str, Any] | None = None,
        milestone: str,
        required_stop_at: str | None = None,
    ):
        current_package_state = {
            "milestone": milestone,
            "agent_task": step.artifact_key,
            "seed_topic": data.topic,
            "target_video_type": data.target_video_type,
            "package_title_seed": data.package_title_seed,
            "research_pack_text": data.research_pack_text,
            "research_pack_ref": data.research_pack_ref,
            "required_stop_at": required_stop_at,
        }
        runtime_guard_state = {
            "human_review_only": True,
            "llm_router_only": True,
            "no_media_provider_calls": True,
            "no_elevenlabs_call": True,
            "no_google_vertex_veo_call": True,
            "no_creatomate_call": True,
            "no_google_drive_upload": True,
            "no_youtube_upload": True,
            "no_upload": True,
            "no_publish": True,
            "no_reupload": True,
            "no_mock_fallback": True,
            "no_dry_run_success": True,
            "no_prompt_self_mutation": True,
            "no_channel_config_mutation": True,
            "google_drive_archive_only": True,
            "media_boundary_state": "BLOCKED_UNTIL_HUMAN_APPROVED_PROVIDER_STAGE",
        }
        return AgentContextPackBuilder(self.session).build(
            package_id=package_id,
            video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
            agent_key=step.agent_key,
            task_type=step.requested_task_type,
            lane=step.router_lane,
            effective_context_snapshot_id=effective_context_snapshot.id if effective_context_snapshot else None,
            effective_context_hash=effective_context_snapshot.context_hash if effective_context_snapshot else None,
            compiled_policy_snapshot_id=snapshot.id,
            compiled_policy_snapshot_hash=snapshot.content_hash,
            channel_contract_hash=effective_context_snapshot.channel_contract_hash if effective_context_snapshot else None,
            artifacts=artifacts,
            evidence_refs=self._evidence_refs(data),
            current_package_state=current_package_state,
            runtime_guard_state=runtime_guard_state,
            provider_readiness_state=provider_readiness_state,
            schema_requirements={"base_envelope": "m12.1.0", "response_format": "json"},
        )

    def _task_payload(
        self,
        *,
        step: PackageAgentStep,
        data: FirstScriptedVideoPackageRequest,
        context_pack: dict[str, Any],
        channel: ChannelWorkspace,
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any]:
        return {
            "milestone": "M12.2 Production Prompt Activation",
            "agent_task": step.artifact_key,
            "channel_id": str(channel.id),
            "compiled_policy_snapshot_id": str(snapshot.id),
            "seed_topic": data.topic,
            "research_pack_ref": data.research_pack_ref,
            "agent_context_pack": context_pack,
            "input_refs": {
                "research_pack_ref": data.research_pack_ref,
                "effective_context_snapshot_id": context_pack["audit_refs"]["effective_context_snapshot_id"],
                "context_pack_hash": context_pack["context_pack_hash"],
            },
            "runtime_constraints": {
                "human_review_only": True,
                "no_media_provider_calls": True,
                "no_upload": True,
                "no_publish": True,
                "no_reupload": True,
                "no_mock_fallback": True,
                "no_prompt_self_mutation": True,
                "no_channel_config_mutation": True,
            },
        }

    def _full_rehearsal_task_payload(
        self,
        *,
        step: PackageAgentStep,
        data: FirstScriptedVideoPackageRequest,
        context_pack: dict[str, Any],
        channel: ChannelWorkspace,
        snapshot: CompiledChannelPolicySnapshot,
    ) -> dict[str, Any]:
        return {
            "milestone": FULL_REHEARSAL_MILESTONE,
            "agent_task": step.artifact_key,
            "channel_id": str(channel.id),
            "compiled_policy_snapshot_id": str(snapshot.id),
            "seed_topic": data.topic,
            "target_video_type": data.target_video_type,
            "package_title_seed": data.package_title_seed,
            "research_pack_ref": data.research_pack_ref,
            "agent_context_pack": context_pack,
            "input_refs": {
                "research_pack_ref": data.research_pack_ref,
                "effective_context_snapshot_id": context_pack["audit_refs"]["effective_context_snapshot_id"],
                "context_pack_hash": context_pack["context_pack_hash"],
            },
            "required_stop_at": "video_generation",
            "runtime_constraints": {
                "real_ollama_via_llm_router_only": True,
                "human_review_only": True,
                "no_media_provider_calls": True,
                "no_elevenlabs_call": True,
                "no_google_vertex_veo_call": True,
                "no_creatomate_call": True,
                "no_google_drive_upload": True,
                "no_youtube_upload": True,
                "no_publish": True,
                "no_reupload": True,
                "no_mock_fallback": True,
                "no_dry_run_success": True,
                "no_prompt_self_mutation": True,
                "no_channel_config_mutation": True,
                "script_rewrite_rule": "Run ScriptRewriteAgent only when gatekeeper/validation explicitly requires rewrite; do not add new claims.",
                "missing_media_provider_rule": (
                    "Do not return REVIEW_REQUIRED or BLOCK only because ElevenLabs, Creatomate, or Veo are not configured. "
                    "For valid text/review artifacts, record provider gaps in limitations; VideoGenerationBoundary will block provider execution."
                ),
                "script_writer_artifact_contract": {
                    "required": "artifact.sentences",
                    "sentence_item_fields": ["sentence_id", "text", "approx_seconds"],
                    "sentence_id_format": "S1, S2, S3...",
                },
                "visual_plan_artifact_contract": {
                    "required": "artifact.scenes",
                    "scene_source_field": "intended_visual_source",
                    "allowed_values": sorted(VISUAL_SOURCE_ALLOWLIST),
                    "provider_backed_assets": "candidate-only; do not request or imply generation",
                },
                "media_qc_artifact_contract": {
                    "no_media_file_exists": True,
                    "allowed_artifact_status": ["NOT_AVAILABLE", "WAITING_MEDIA_GENERATION"],
                    "forbidden_status": ["PASS", "QC_PASS"],
                    "provider_gap_handling": "limitations plus VideoGenerationBoundary, not BLOCK",
                },
                "rights_disclosure_artifact_contract": {
                    "required_non_empty_artifact": True,
                    "minimum_fields": ["result", "source_manifest_status", "ai_disclosure_needed", "rights_risk", "disclosure_notes"],
                    "text_only_rehearsal_note": "future generated media still needs source/provider manifest review",
                },
                "provider_readiness_artifact_contract": {
                    "missing_media_providers_expected_at_boundary": True,
                    "top_level_status_for_valid_summary": "OK",
                    "forbidden_top_level_status_for_missing_provider_only": ["BLOCK", "REVIEW_REQUIRED"],
                    "minimum_artifact_fields": ["providers", "next_action"],
                },
                "visual_source_allowlist": sorted(VISUAL_SOURCE_ALLOWLIST),
                "media_qc_expected_without_media": ["NOT_AVAILABLE", "WAITING_MEDIA_GENERATION"],
            },
        }

    def _evidence_refs(self, data: FirstScriptedVideoPackageRequest) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        if data.research_pack_ref:
            refs.append({"source_type": "OPERATOR_RESEARCH_PACK", "ref": data.research_pack_ref})
        if data.research_pack_text:
            refs.append({"source_type": "OPERATOR_RESEARCH_PACK_INLINE", "provided": True})
        return refs

    def _artifact_refs(self, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"artifact_key": key} for key in sorted(artifacts)]

    def _latest_audit_id(self, render_run_id: uuid.UUID, *, provider_refs: list[dict[str, Any]]) -> uuid.UUID | None:
        audit = self.session.scalars(
            select(PromptAuditSnapshot)
            .where(PromptAuditSnapshot.prompt_render_run_id == render_run_id)
            .order_by(desc(PromptAuditSnapshot.created_at))
            .limit(1)
        ).one_or_none()
        if audit is None:
            return None
        audit.provider_attempt_refs = provider_refs
        audit.final_output_ref = f"prompt-output:{render_run_id}"
        self.session.flush()
        return audit.id

    def _record_prompt_budget_metrics(self, snapshot_id: uuid.UUID, rendered_messages: list[Any]) -> None:
        snapshot = self.session.get(AgentContextPackSnapshot, snapshot_id)
        if snapshot is None:
            return
        system_chars = 0
        user_chars = 0
        for message in rendered_messages:
            role = getattr(message, "role", None)
            content = getattr(message, "content", "")
            if isinstance(message, dict):
                role = message.get("role")
                content = message.get("content", "")
            if role == "system":
                system_chars += len(str(content))
            if role == "user":
                user_chars += len(str(content))
        budget = dict(snapshot.budget_report_json or {})
        budget["prompt_chars_system"] = system_chars
        budget["prompt_chars_user"] = user_chars
        budget["prompt_tokens_estimated"] = max(1, (system_chars + user_chars) // 4)
        snapshot.budget_report_json = budget
        self.session.flush()

    def _agent_ref(
        self,
        step: PackageAgentStep,
        render: Any,
        *,
        route: Any,
        validation: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "agent_key": step.agent_key,
            "artifact_key": step.artifact_key,
            "template_key": render.template_key,
            "template_version": render.template_version,
            "router_lane": render.router_lane,
            "prompt_hash": render.prompt_hash,
            "prompt_context_hash": render.prompt_context_hash,
            "prompt_render_run_id": str(render.prompt_render_run_id),
            "llm_route_attempt_id": str(route.route_attempt_id),
            "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
            "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
            "route_status": route.status,
            "validation": validation,
        }

    def _visual_plan_block(self, artifact: Any) -> dict[str, Any] | None:
        values = _find_visual_source_values(artifact)
        invalid = sorted(value for value in values if value not in VISUAL_SOURCE_ALLOWLIST)
        missing_scene_sources = _find_scenes_missing_visual_source(artifact)
        if not invalid and not missing_scene_sources:
            return None
        return {
            "status": "REVIEW_REQUIRED",
            "reason_codes": ["VISUAL_SOURCE_NOT_ALLOWED"],
            "invalid_visual_sources": invalid,
            "scenes_missing_intended_visual_source": missing_scene_sources,
            "allowed_visual_sources": sorted(VISUAL_SOURCE_ALLOWLIST),
        }

    def _gatekeeper_result(self, output: dict[str, Any]) -> str:
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        result = str(artifact.get("result") or artifact.get("decision") or output.get("status") or "").upper()
        if result in {"PASS", "OK"}:
            return "PASS"
        if result in {"BLOCK", "BLOCKED"}:
            return "BLOCK"
        return "REVIEW_REQUIRED" if result == "REVIEW_REQUIRED" else "PASS"

    def _full_rehearsal_artifact_block(self, agent_key: str, artifact: Any) -> dict[str, Any] | None:
        if agent_key == "VisualPlanningAgent":
            visual_block = self._visual_plan_block(artifact)
            if visual_block is not None:
                return {
                    **visual_block,
                    "next_action": "Sửa visual plan để chỉ dùng nguồn DIAGRAM/CARD/SCREENSHOT/EXISTING_ASSET/VEO hoặc Creatomate candidate-only.",
                }
        if agent_key == "ScriptWriterAgent" and not _has_sentence_ids(artifact):
            return {
                "status": "REVIEW_REQUIRED",
                "reason_codes": ["SCRIPT_SENTENCE_IDS_REQUIRED"],
                "next_action": "ScriptWriterAgent phải trả artifact.sentences với sentence_id/text/approx_seconds.",
            }
        if agent_key == "RightsDisclosureReviewer" and not _has_required_rights_review(artifact):
            return {
                "status": "REVIEW_REQUIRED",
                "reason_codes": ["RIGHTS_DISCLOSURE_ARTIFACT_REQUIRED"],
                "next_action": "RightsDisclosureReviewer phải trả artifact có result/source_manifest_status/ai_disclosure_needed/rights_risk/disclosure_notes.",
            }
        if agent_key == "ThumbnailBriefAgent":
            rendered_keys = sorted(_find_forbidden_thumbnail_render_keys(artifact))
            if rendered_keys:
                return {
                    "status": "REVIEW_REQUIRED",
                    "reason_codes": ["THUMBNAIL_RENDER_NOT_ALLOWED"],
                    "forbidden_render_keys": rendered_keys,
                    "next_action": "ThumbnailBriefAgent chỉ được tạo brief/variant, không render thumbnail.",
                }
        if agent_key == "MediaQCExplanationAgent":
            qc_status = _media_qc_status(artifact)
            if qc_status not in {"NOT_AVAILABLE", "WAITING_MEDIA_GENERATION"}:
                return {
                    "status": "REVIEW_REQUIRED",
                    "reason_codes": ["MEDIA_QC_CANNOT_PASS_WITHOUT_MEDIA"],
                    "observed_status": qc_status,
                    "next_action": "MediaQCExplanationAgent phải trả NOT_AVAILABLE hoặc WAITING_MEDIA_GENERATION khi chưa có media file.",
                }
        return None

    def _should_create_boundary(self, artifacts: dict[str, Any]) -> bool:
        return bool(artifacts.get("narration_script") and artifacts.get("visual_plan") and artifacts.get("thumbnail_brief"))

    def _create_generation_boundary(
        self,
        *,
        package: FirstScriptedVideoPackage,
        readiness_snapshot: dict[str, Any],
    ) -> VideoGenerationBoundary:
        provider_readiness = self._boundary_provider_readiness(readiness_snapshot)
        missing_required = [
            provider
            for provider in ("elevenlabs", "creatomate")
            if provider_readiness.get(provider, {}).get("status") != "CONFIGURED"
        ]
        required_inputs = {
            "narration_script": {"present": bool(package.artifacts.get("narration_script"))},
            "visual_plan": {"present": bool(package.artifacts.get("visual_plan"))},
            "thumbnail_brief": {"present": bool(package.artifacts.get("thumbnail_brief"))},
            "metadata_package": {"present": bool(package.artifacts.get("metadata_package"))},
            "rights_disclosure_review": {"present": bool(package.artifacts.get("rights_disclosure_review"))},
        }
        blocked_reasons: list[str] = []
        if any(not item["present"] for item in required_inputs.values()):
            blocked_reasons.append("REQUIRED_INPUT_MISSING")
            boundary_status = "REVIEW_REQUIRED"
            operator_summary = "Gói nội dung chưa đủ artifact để chuyển tới media boundary."
            next_action = "Bổ sung đủ script, visual plan, thumbnail brief, metadata và rights review."
        elif package.package_status == "BLOCKED":
            blocked_reasons.append("GATEKEEPER_BLOCK")
            boundary_status = "BLOCKED_GATEKEEPER"
            operator_summary = "Gatekeeper đang BLOCK nên chưa thể chuyển tới bước tạo media."
            next_action = "Sửa các blocker gatekeeper trước khi tạo media."
        elif package.package_status == "REVIEW_REQUIRED":
            blocked_reasons.append("PACKAGE_REVIEW_REQUIRED")
            boundary_status = "REVIEW_REQUIRED"
            operator_summary = "Package cần human review trước khi chuyển tới provider media."
            next_action = HUMAN_APPROVAL_REQUIRED
        elif missing_required:
            blocked_reasons.extend(f"{provider.upper()}_NOT_CONFIGURED" for provider in missing_required)
            boundary_status = "BLOCKED_PROVIDER_NOT_CONFIGURED"
            operator_summary = MEDIA_PROVIDER_BOUNDARY_SUMMARY
            next_action = MEDIA_PROVIDER_BOUNDARY_NEXT_ACTION
        else:
            boundary_status = "READY_FOR_MEDIA_PROVIDERS"
            operator_summary = "Gói nội dung đã sẵn sàng chuyển tới media providers khi operator phê duyệt."
            next_action = HUMAN_APPROVAL_REQUIRED

        boundary = VideoGenerationBoundary(
            package_id=package.id,
            channel_id=package.channel_id,
            video_project_id=package.video_project_id,
            required_inputs=required_inputs,
            required_providers=[
                {"provider_key": "elevenlabs", "role": "ElevenLabs voice", "required": True},
                {"provider_key": "creatomate", "role": "Creatomate render", "required": True},
                {"provider_key": "google-vertex-veo", "role": "optional Google Vertex Veo hero", "required": False},
            ],
            provider_readiness=provider_readiness,
            boundary_status=boundary_status,
            blocked_reasons=blocked_reasons,
            next_action=next_action,
            operator_summary_vi=operator_summary,
            no_provider_calls_confirmed=True,
        )
        self.session.add(boundary)
        self.session.flush()
        return boundary

    def _boundary_provider_readiness(self, readiness_snapshot: dict[str, Any]) -> dict[str, Any]:
        summaries = {
            str(summary.get("provider_key")): summary
            for summary in readiness_snapshot.get("provider_summaries", [])
            if isinstance(summary, dict) and summary.get("provider_key")
        }
        return {
            "elevenlabs": self._provider_boundary_state(summaries.get("elevenlabs")),
            "creatomate": self._provider_boundary_state(summaries.get("creatomate")),
            "veo": self._provider_boundary_state(summaries.get("google-vertex-veo"), optional=True),
        }

    def _provider_boundary_state(self, summary: dict[str, Any] | None, *, optional: bool = False) -> dict[str, Any]:
        if summary is None:
            return {
                "status": "NOT_CONFIGURED",
                "required": not optional,
                "readiness_state": "UNKNOWN",
                "reason_codes": ["PROVIDER_READINESS_MISSING"],
            }
        readiness_state = str(summary.get("readiness_state") or "UNKNOWN")
        reason_codes = list(summary.get("reason_codes") or [])
        missing_env_keys = list(summary.get("missing_env_keys") or [])
        credential_missing = any("KEY_MISSING" in code or "NEEDS_AUTH" in code or "CREDENTIAL" in code for code in reason_codes)
        if readiness_state == "PASS":
            status = "CONFIGURED"
        elif credential_missing or missing_env_keys:
            status = "NEEDS_CREDENTIAL"
        else:
            status = "NOT_CONFIGURED"
        return {
            "status": status,
            "required": not optional,
            "readiness_state": readiness_state,
            "missing_env_keys": missing_env_keys,
            "reason_codes": reason_codes,
            "next_action": summary.get("next_action"),
        }

    def _human_review_checklist(self, artifacts: dict[str, Any], provider_readiness_snapshot_id: uuid.UUID) -> dict[str, Any]:
        narration = artifacts.get("narration_script") or {}
        research = artifacts.get("research_notes") or {}
        metadata = artifacts.get("metadata_package") or {}
        visual_plan = artifacts.get("visual_plan") or {}
        return {
            "facts_claims_need_review": True,
            "evidence_refs_missing": not bool(research.get("evidence_refs") or research.get("sources") or research.get("source_notes")),
            "title_thumbnail_accuracy": "REVIEW_REQUIRED",
            "rights_source_manifest": "REVIEW_REQUIRED",
            "ai_disclosure_needed": True,
            "market_locale_fit": "REVIEW_REQUIRED",
            "content_language_check": "REVIEW_REQUIRED",
            "reused_content_risk": "REVIEW_REQUIRED",
            "upload_card_copy_ready": bool(artifacts.get("upload_card_copy")),
            "provider_readiness_gaps_ref": str(provider_readiness_snapshot_id),
            "narration_sentence_ids_present": bool(narration.get("sentences") or narration.get("sentence_ids")),
            "metadata_present": bool(metadata),
            "visual_plan_present": bool(visual_plan),
            "final_statement": HUMAN_APPROVAL_REQUIRED,
        }

    def _risk_summary(self, artifacts: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "package_status": status,
            "media_provider_calls_made": False,
            "upload_or_publish_calls_made": False,
            "no_provider_calls_confirmed": True,
            "old_provider_smoke_run": False,
            "mock_fallback_used": False,
            "dry_run_success_used": False,
            "local_fixture_success_used": False,
            "channel_config_mutated": False,
            "learning_auto_promotion": False,
            "limitations": [
                "Gatekeeper soft review không thay thế human approval.",
                "Visual plan là brief/candidate-only, chưa tạo Veo/Creatomate output.",
            ]
            if artifacts.get("visual_plan")
            else ["Package chưa có visual plan hoàn chỉnh."],
        }

    def _create_package(
        self,
        *,
        id: uuid.UUID | None = None,
        channel_id: uuid.UUID,
        status: str,
        video_project_id: uuid.UUID | None = None,
        channel_profile_version_id: uuid.UUID | None = None,
        compiled_policy_snapshot_id: uuid.UUID | None = None,
        effective_context_snapshot_id: uuid.UUID | None = None,
        effective_context_hash: str | None = None,
        provider_readiness_snapshot_id: uuid.UUID | None = None,
        agent_run_refs: list[dict[str, Any]] | None = None,
        prompt_render_run_refs: list[str] | None = None,
        prompt_audit_snapshot_refs: list[str] | None = None,
        artifacts: dict[str, Any] | None = None,
        limitations: list[str] | None = None,
        risk_limitations_summary: dict[str, Any] | None = None,
        next_action: str | None = None,
    ) -> FirstScriptedVideoPackage:
        package = FirstScriptedVideoPackage(
            id=id or uuid.uuid4(),
            video_project_id=video_project_id,
            channel_id=channel_id,
            channel_profile_version_id=channel_profile_version_id,
            compiled_policy_snapshot_id=compiled_policy_snapshot_id,
            effective_context_snapshot_id=effective_context_snapshot_id,
            effective_context_hash=effective_context_hash,
            provider_readiness_snapshot_id=provider_readiness_snapshot_id,
            package_status=status,
            agent_run_refs=agent_run_refs or [],
            prompt_render_run_refs=prompt_render_run_refs or [],
            prompt_audit_snapshot_refs=prompt_audit_snapshot_refs or [],
            artifacts=artifacts or {},
            limitations=limitations or [],
            risk_limitations_summary=risk_limitations_summary or self._risk_summary(artifacts or {}, status),
            next_action=next_action,
        )
        self.session.add(package)
        self.session.flush()
        return package

    def _read(self, package: FirstScriptedVideoPackage) -> FirstScriptedVideoPackageRead:
        return FirstScriptedVideoPackageRead(
            id=package.id,
            video_project_id=package.video_project_id,
            channel_id=package.channel_id,
            channel_profile_version_id=package.channel_profile_version_id,
            compiled_policy_snapshot_id=package.compiled_policy_snapshot_id,
            effective_context_snapshot_id=package.effective_context_snapshot_id,
            effective_context_hash=package.effective_context_hash,
            provider_readiness_snapshot_id=package.provider_readiness_snapshot_id,
            package_status=package.package_status,  # type: ignore[arg-type]
            agent_run_refs=package.agent_run_refs,
            prompt_render_run_refs=[uuid.UUID(str(item)) for item in package.prompt_render_run_refs],
            prompt_audit_snapshot_refs=[uuid.UUID(str(item)) for item in package.prompt_audit_snapshot_refs],
            artifacts=package.artifacts,
            limitations=package.limitations,
            risk_limitations_summary=package.risk_limitations_summary,
            next_action=package.next_action,
            created_at=package.created_at,
        )


def _find_visual_source_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"visual_source", "source_type", "intended_visual_source"} and isinstance(item, str):
                found.add(item)
            found.update(_find_visual_source_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_visual_source_values(item))
    return found


def _find_scenes_missing_visual_source(value: Any) -> list[int | str]:
    if not isinstance(value, dict):
        return []
    scenes = value.get("scenes")
    if not isinstance(scenes, list):
        return []
    missing: list[int | str] = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            missing.append(index)
            continue
        source = scene.get("intended_visual_source") or scene.get("visual_source") or scene.get("source_type")
        if not isinstance(source, str) or not source:
            missing.append(scene.get("section") or scene.get("sentence_id") or index)
    return missing


def _has_sentence_ids(value: Any) -> bool:
    if isinstance(value, dict):
        sentences = value.get("sentences")
        if isinstance(sentences, list) and sentences:
            return all(
                isinstance(item, dict)
                and isinstance(item.get("sentence_id"), str)
                and isinstance(item.get("text"), str)
                and item.get("approx_seconds") is not None
                for item in sentences
            )
        return any(_has_sentence_ids(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_sentence_ids(item) for item in value)
    return False


def _has_required_rights_review(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    required = {"result", "source_manifest_status", "ai_disclosure_needed", "rights_risk", "disclosure_notes"}
    return required <= set(value)


def _find_forbidden_thumbnail_render_keys(value: Any) -> set[str]:
    forbidden = {
        "render_url",
        "rendered_url",
        "rendered_thumbnail_url",
        "thumbnail_file_path",
        "image_url",
        "generated_image_ref",
        "actual_thumbnail_asset",
    }
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in forbidden:
                found.add(key)
            found.update(_find_forbidden_thumbnail_render_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_forbidden_thumbnail_render_keys(item))
    return found


def _media_qc_status(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("status", "artifact_status", "qc_status", "result", "media_qc_status"):
        if value.get(key):
            return str(value[key]).upper()
    return None


def _needs_script_rewrite(output: dict[str, Any] | None) -> bool:
    if not isinstance(output, dict):
        return False
    artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
    appendix = output.get("technical_appendix") if isinstance(output.get("technical_appendix"), dict) else {}
    markers = (
        artifact.get("needs_script_rewrite"),
        artifact.get("rewrite_required"),
        appendix.get("needs_script_rewrite"),
        appendix.get("rewrite_required"),
    )
    return any(bool(marker) for marker in markers)
