from __future__ import annotations

# Compatibility note: semantic facades `video_package_generation`, `agent_rehearsal`, and `package_generation_rehearsal` re-export this implementation; phase-coded import kept for reports/tests/backward compatibility.
import hashlib
import re
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
from app.services.m1 import PackagingHandoffReadService
from app.services.m2 import ProviderReadinessM2Service
from app.services.m12 import ProviderReadinessService
from app.services.m12_1 import PromptRegistryService
from app.services.r3d3 import AgentContextPackBuilder
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler, build_effective_channel_runtime_digest
from app.services.r3d4 import (
    AgentOutputValidationService,
    GATE_BLOCK,
    GATE_REVIEW,
    PackageStatusReducer,
    R3D4GateService,
    compact_gate_report,
)


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
MEDIA_PROVIDER_BOUNDARY_NEXT_ACTION = "Cấu hình ElevenLabs và Creatomate Growth 10K; Luma API chỉ là AI hero optional và không được gọi trong VCOS."
FULL_REHEARSAL_MILESTONE = "M12.2S Full Agent + Real Ollama Rehearsal"

VISUAL_SOURCE_ALLOWLIST = {
    "DIAGRAM",
    "CARD",
    "SCREENSHOT",
    "EXISTING_ASSET",
    "LUMA_HERO_CANDIDATE_ONLY",
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
        self.output_validation = AgentOutputValidationService(session)
        self.deterministic_gates = R3D4GateService(session)
        self.package_status_reducer = PackageStatusReducer()
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
        from app.services.r3d9_ux2 import PackagingReviewQueueService

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
            packaging_handoff=PackagingHandoffReadService(self.session).build(package.id),
            packaging_review_queue=PackagingReviewQueueService(self.session).read(package.id),
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
        artifacts["duration_model"] = _duration_model_from_context(effective_context_snapshot, target_video_type=data.target_video_type)
        if effective_context_snapshot is not None:
            artifacts["effective_context_snapshot_ref"] = build_effective_channel_runtime_digest(effective_context_snapshot)
        agent_run_refs: list[dict[str, Any]] = []
        prompt_render_run_refs: list[str] = []
        prompt_audit_snapshot_refs: list[str] = []
        context_pack_refs: list[dict[str, Any]] = []
        status = "READY_FOR_HUMAN_REVIEW"
        next_action = HUMAN_APPROVAL_REQUIRED
        pre_gatekeeper_batch = None
        limitations: list[str] = [
            "M12.2 chỉ tạo scripted video package; không render video, không TTS, không upload/publish.",
            "Google Drive chỉ là archive/storage; VCOS DB vẫn là source of truth.",
        ]

        for step in PACKAGE_AGENT_CHAIN:
            if step.agent_key == "GatekeeperSoftReviewAgent" and pre_gatekeeper_batch is None:
                pre_gatekeeper_batch = self._run_package_deterministic_gates(
                    package_id=package_id,
                    video_project_id=video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_state={"id": str(provider_readiness_snapshot_id)},
                    include_provider_boundary=False,
                )
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
            output_validation = self._validate_agent_output(
                package_id=package_id,
                video_project_id=video_project_id,
                step=step,
                raw_output=raw_output,
                parsed_output=output,
                prompt_validation_result=validation.model_dump(mode="json"),
                runtime_context_refs=self._runtime_context_refs(
                    context_pack=context_result.context_pack,
                    context_pack_snapshot=context_result.snapshot,
                    render=render,
                    snapshot=snapshot,
                    effective_context_snapshot=effective_context_snapshot,
                ),
                render=render,
                context_pack_snapshot=context_result.snapshot,
            )
            if agent_run_refs:
                agent_run_refs[-1]["agent_output_validation_run_id"] = str(output_validation.validation_run.id)
                agent_run_refs[-1]["canonical_artifact_hash"] = output_validation.validation_run.artifact_hash
            if output_validation.status != "OK":
                artifacts[step.artifact_key] = output_validation.blocking_report
                status = "REVIEW_REQUIRED" if output_validation.status == "REVIEW_REQUIRED" else "BLOCKED"
                next_action = "Sửa AgentOutputContract/envelope trước khi tiếp tục package."
                break

            artifacts[step.artifact_key] = output_validation.canonical_artifact or {}
            output = {**output, "artifact": artifacts[step.artifact_key]}
            envelope_status = output.get("status")
            if step.agent_key == "VisualPlanningAgent":
                visual_block = self._visual_plan_block(artifacts[step.artifact_key])
                if visual_block is not None:
                    artifacts["visual_plan_review"] = visual_block
                    status = "REVIEW_REQUIRED"
                    next_action = "Sửa visual plan để chỉ dùng nguồn DIAGRAM/CARD/SCREENSHOT/EXISTING_ASSET/VEO hoặc Creatomate candidate-only."
                    break
            gate_stop = self._run_agent_deterministic_gates(
                package_id=package_id,
                video_project_id=video_project_id,
                step=step,
                artifacts=artifacts,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_state={"id": str(provider_readiness_snapshot_id)},
            )
            if gate_stop is not None:
                status = gate_stop["stop_status"]
                next_action = gate_stop["next_action"]
                break
            if step.agent_key == "ScriptWriterAgent":
                artifacts["srt"] = _build_srt_caption_artifact(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    script=_dict(artifacts.get("narration_script")),
                    duration_model=_dict(artifacts.get("duration_model")),
                    repo_root=self.repo_root,
                )
                srt_gate_stop = self._run_custom_deterministic_gates(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_state={"id": str(provider_readiness_snapshot_id)},
                    gate_keys=[
                        "srt_format_gate",
                        "srt_timing_gate",
                        "caption_coverage_gate",
                        "caption_readability_gate",
                        "script_to_srt_consistency_gate",
                        "hook_caption_gate",
                    ],
                    trigger_agent_key="SRTCaptionArtifactGenerator",
                )
                if srt_gate_stop is not None:
                    status = srt_gate_stop["stop_status"]
                    next_action = srt_gate_stop["next_action"]
                    break
            if step.agent_key == "VisualPlanningAgent" and artifacts.get("srt"):
                visual_srt_gate_stop = self._run_custom_deterministic_gates(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_state={"id": str(provider_readiness_snapshot_id)},
                    gate_keys=["visual_srt_timeline_gate"],
                    trigger_agent_key="VisualSRTTimelineCrossCheck",
                )
                if visual_srt_gate_stop is not None:
                    status = visual_srt_gate_stop["stop_status"]
                    next_action = visual_srt_gate_stop["next_action"]
                    break
            if step.agent_key == "GatekeeperSoftReviewAgent":
                gatekeeper_result = self._gatekeeper_result(output)
                reducer_decision = self.package_status_reducer.resolve(
                    current_status="READY_FOR_HUMAN_REVIEW",
                    deterministic_batch=pre_gatekeeper_batch,
                    gatekeeper_result=gatekeeper_result,
                )
                artifacts["package_state_reducer"] = reducer_decision
                status = reducer_decision["package_status"]
                if reducer_decision["source"] == "gatekeeper_soft_review" and gatekeeper_result in {"BLOCK", "REVIEW_REQUIRED"}:
                    next_action = output.get("next_action") or self._next_action_for_reducer_decision(reducer_decision)
                else:
                    next_action = self._next_action_for_reducer_decision(reducer_decision)
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
        artifacts["duration_model"] = _duration_model_from_context(effective_context_snapshot, target_video_type=data.target_video_type)
        if effective_context_snapshot is not None:
            artifacts["effective_context_snapshot_ref"] = build_effective_channel_runtime_digest(effective_context_snapshot)
        agent_run_refs: list[dict[str, Any]] = []
        prompt_render_run_refs: list[str] = []
        prompt_audit_snapshot_refs: list[str] = []
        context_pack_refs: list[dict[str, Any]] = []
        status = "READY_FOR_MEDIA_PROVIDERS"
        next_action = HUMAN_APPROVAL_REQUIRED
        pre_gatekeeper_batch = None
        limitations: list[str] = [
            "M12.2S chỉ chạy agent text/review bằng Ollama; không generate media, không TTS, không upload/publish.",
            "ElevenLabs/Luma API/Creatomate Growth 10K/Pexels API chỉ xuất hiện trong readiness/boundary, không được gọi runtime.",
        ]

        for step in FULL_REHEARSAL_AGENT_CHAIN:
            if step.agent_key == "GatekeeperSoftReviewAgent" and pre_gatekeeper_batch is None:
                if artifacts.get("narration_script") and not artifacts.get("srt"):
                    artifacts["srt"] = _build_srt_caption_artifact(
                        package_id=package_id,
                        video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                        script=_dict(artifacts.get("narration_script")),
                        duration_model=_dict(artifacts.get("duration_model")),
                        repo_root=self.repo_root,
                    )
                    srt_gate_stop = self._run_custom_deterministic_gates(
                        package_id=package_id,
                        video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                        artifacts=artifacts,
                        effective_context_snapshot=effective_context_snapshot,
                        provider_readiness_state=provider_readiness_snapshot,
                        gate_keys=[
                            "srt_format_gate",
                            "srt_timing_gate",
                            "caption_coverage_gate",
                            "caption_readability_gate",
                            "script_to_srt_consistency_gate",
                            "hook_caption_gate",
                        ],
                        trigger_agent_key="SRTCaptionArtifactGenerator",
                    )
                    if srt_gate_stop is not None:
                        status = srt_gate_stop["stop_status"]
                        next_action = srt_gate_stop["next_action"] or next_action
                        break
                if artifacts.get("visual_plan") and artifacts.get("srt"):
                    visual_srt_gate_stop = self._run_custom_deterministic_gates(
                        package_id=package_id,
                        video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                        artifacts=artifacts,
                        effective_context_snapshot=effective_context_snapshot,
                        provider_readiness_state=provider_readiness_snapshot,
                        gate_keys=["visual_srt_timeline_gate"],
                        trigger_agent_key="VisualSRTTimelineCrossCheck",
                    )
                    if visual_srt_gate_stop is not None:
                        status = visual_srt_gate_stop["stop_status"]
                        next_action = visual_srt_gate_stop["next_action"] or next_action
                        break
                pre_gatekeeper_batch = self._run_package_deterministic_gates(
                    package_id=package_id,
                    video_project_id=video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_state=provider_readiness_snapshot,
                    include_provider_boundary=False,
                )
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
            if step.agent_key == "GatekeeperSoftReviewAgent" and result["stop_status"] is None:
                gatekeeper_result = self._gatekeeper_result(result.get("parsed_output") or {})
                reducer_decision = self.package_status_reducer.resolve(
                    current_status="READY_FOR_MEDIA_PROVIDERS",
                    deterministic_batch=pre_gatekeeper_batch,
                    gatekeeper_result=gatekeeper_result,
                )
                artifacts["package_state_reducer"] = reducer_decision
                if reducer_decision["package_status"] != "READY_FOR_MEDIA_PROVIDERS":
                    status = reducer_decision["package_status"]
                    next_action = self._next_action_for_reducer_decision(reducer_decision)
                    break
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

        if status == "READY_FOR_MEDIA_PROVIDERS" and self._should_create_boundary(artifacts):
            provider_boundary_batch = self._run_package_deterministic_gates(
                package_id=package_id,
                video_project_id=video_project_id,
                artifacts=artifacts,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_state=provider_readiness_snapshot,
                include_provider_boundary=True,
            )
            if provider_boundary_batch and provider_boundary_batch.status in {GATE_BLOCK, GATE_REVIEW}:
                reducer_decision = self.package_status_reducer.resolve(
                    current_status=status,
                    deterministic_batch=provider_boundary_batch,
                    gatekeeper_result="PASS",
                )
                artifacts["package_state_reducer"] = reducer_decision
                status = reducer_decision["package_status"]
                next_action = self._next_action_for_reducer_decision(reducer_decision)

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
        task_payload["duration_model"] = artifacts.get("duration_model", {})
        if step.agent_key in {"ScriptWriterAgent", "ScriptRewriteAgent"}:
            task_payload["script_duration_contract"] = _script_duration_contract(
                task_payload["duration_model"],
                script_outline=_dict(artifacts.get("script_outline")),
            )
        task_payload["hook_spec_contract"] = {
            "required_before_downstream_visual_or_provider_plan": True,
            "fields": [
                "hook_type",
                "first_3_seconds_script",
                "first_3_seconds_visual",
                "promise_made",
                "payoff_location",
                "clickbait_risk",
                "visual_hook_relevance",
                "title_hook_alignment",
            ],
            "no_fake_demo_result_or_asset_claim": True,
        }
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

        if step.agent_key == "TopicIdeaScoringAgent" and _topic_idea_needs_schema_retry(validation.model_dump(mode="json")):
            retry = self._retry_topic_idea_schema_once(
                step=step,
                render=render,
                validation=validation,
                agent_run_refs=agent_run_refs,
                prompt_audit_snapshot_refs=prompt_audit_snapshot_refs,
            )
            artifacts["topic_idea_schema_retry_attempt"] = retry["audit"]
            if retry["validation"] is not None:
                validation = retry["validation"]
                raw_output = retry["raw_output"]

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
            output = {**output, "artifact": artifact}
        output_validation = self._validate_agent_output(
            package_id=package_id,
            video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
            step=step,
            raw_output=raw_output,
            parsed_output=output,
            prompt_validation_result=validation.model_dump(mode="json"),
            runtime_context_refs=self._runtime_context_refs(
                context_pack=context_result.context_pack,
                context_pack_snapshot=context_result.snapshot,
                render=render,
                snapshot=snapshot,
                effective_context_snapshot=effective_context_snapshot,
            ),
            render=render,
            context_pack_snapshot=context_result.snapshot,
        )
        if agent_run_refs:
            agent_run_refs[-1]["agent_output_validation_run_id"] = str(output_validation.validation_run.id)
            agent_run_refs[-1]["canonical_artifact_hash"] = output_validation.validation_run.artifact_hash
        if output_validation.status != "OK":
            artifacts[step.artifact_key] = output_validation.blocking_report
            return {
                "stop_status": "REVIEW_REQUIRED" if output_validation.status == "REVIEW_REQUIRED" else "BLOCKED",
                "next_action": "Sửa AgentOutputContract/envelope trước khi tiếp tục full rehearsal.",
                "parsed_output": output,
            }

        artifacts[step.artifact_key] = output_validation.canonical_artifact or {}
        if step.agent_key == "ScriptPlanningAgent" and isinstance(artifacts[step.artifact_key], dict):
            artifacts["script_word_budget"] = _script_word_budget_contract(
                _dict(artifacts.get("duration_model")),
                script_outline=artifacts[step.artifact_key],
            )
            artifacts[step.artifact_key]["section_budgets"] = artifacts["script_word_budget"]["section_word_budgets"]
        if step.agent_key in {"ScriptWriterAgent", "ScriptRewriteAgent"} and isinstance(artifacts[step.artifact_key], dict):
            _refresh_script_duration_self_check(artifacts[step.artifact_key], _dict(artifacts.get("duration_model")))
        output = {**output, "artifact": artifacts[step.artifact_key]}
        agent_block = self._full_rehearsal_artifact_block(step.agent_key, artifacts[step.artifact_key])
        if agent_block is not None:
            artifacts[f"{step.artifact_key}_review"] = agent_block
            return {"stop_status": "REVIEW_REQUIRED", "next_action": agent_block["next_action"], "parsed_output": output}

        gate_stop = self._run_agent_deterministic_gates(
            package_id=package_id,
            video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
            step=step,
            artifacts=artifacts,
            effective_context_snapshot=effective_context_snapshot,
            provider_readiness_state=provider_readiness_snapshot,
        )
        if gate_stop is not None:
            if step.agent_key == "ScriptWriterAgent":
                duration_repair = self._maybe_repair_script_duration_once(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_snapshot=provider_readiness_snapshot,
                    gate_stop=gate_stop,
                )
                if duration_repair["attempted"]:
                    if duration_repair["repaired"] and duration_repair["stop_status"] is None:
                        return {"stop_status": None, "next_action": None, "parsed_output": output}
                    gate_stop = {
                        "stop_status": duration_repair["stop_status"] or gate_stop["stop_status"],
                        "next_action": duration_repair["next_action"] or gate_stop["next_action"],
                        "gate_batch": duration_repair.get("gate_batch") or gate_stop.get("gate_batch"),
                    }
                    remaining_codes = _gate_stop_fail_codes(gate_stop)
                    if any(code in remaining_codes for code in ("SCRIPT_DURATION_ABOVE_MAXIMUM", "SCRIPT_DURATION_BELOW_MINIMUM", "SCRIPT_WORD_BUDGET_BELOW_MINIMUM")):
                        return {
                            "stop_status": gate_stop["stop_status"],
                            "next_action": gate_stop["next_action"],
                            "parsed_output": output,
                        }
                repair = self._maybe_repair_script_style_once(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_snapshot=provider_readiness_snapshot,
                    gate_stop=gate_stop,
                )
                if repair["attempted"]:
                    if repair["repaired"] and repair["stop_status"] is None:
                        return {"stop_status": None, "next_action": None, "parsed_output": output}
                    return {
                        "stop_status": repair["stop_status"] or gate_stop["stop_status"],
                        "next_action": repair["next_action"] or gate_stop["next_action"],
                        "parsed_output": output,
                    }
            if step.agent_key == "VisualPlanningAgent":
                visual_repair = self._maybe_repair_visual_coverage_once(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_snapshot=provider_readiness_snapshot,
                    gate_stop=gate_stop,
                )
                if visual_repair["attempted"]:
                    if visual_repair["repaired"] and visual_repair["stop_status"] is None:
                        return {"stop_status": None, "next_action": None, "parsed_output": output}
                    return {
                        "stop_status": visual_repair["stop_status"] or gate_stop["stop_status"],
                        "next_action": visual_repair["next_action"] or gate_stop["next_action"],
                        "parsed_output": output,
                    }
            if step.agent_key == "RightsDisclosureReviewer":
                disclosure_repair = self._maybe_repair_ai_disclosure_wording_once(
                    package_id=package_id,
                    video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                    artifacts=artifacts,
                    effective_context_snapshot=effective_context_snapshot,
                    provider_readiness_snapshot=provider_readiness_snapshot,
                    gate_stop=gate_stop,
                )
                if disclosure_repair["attempted"]:
                    if disclosure_repair["repaired"] and disclosure_repair["stop_status"] is None:
                        return {"stop_status": None, "next_action": None, "parsed_output": output}
                    return {
                        "stop_status": disclosure_repair["stop_status"] or gate_stop["stop_status"],
                        "next_action": disclosure_repair["next_action"] or gate_stop["next_action"],
                        "parsed_output": output,
                    }
            return {
                "stop_status": gate_stop["stop_status"],
                "next_action": gate_stop["next_action"],
                "parsed_output": output,
            }

        if step.agent_key == "ScriptWriterAgent":
            artifacts["srt"] = _build_srt_caption_artifact(
                package_id=package_id,
                video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                script=_dict(artifacts.get("narration_script")),
                duration_model=_dict(artifacts.get("duration_model")),
                repo_root=self.repo_root,
            )
            srt_gate_stop = self._run_custom_deterministic_gates(
                package_id=package_id,
                video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                artifacts=artifacts,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_state=provider_readiness_snapshot,
                gate_keys=[
                    "srt_format_gate",
                    "srt_timing_gate",
                    "caption_coverage_gate",
                    "caption_readability_gate",
                    "script_to_srt_consistency_gate",
                    "hook_caption_gate",
                ],
                trigger_agent_key="SRTCaptionArtifactGenerator",
            )
            if srt_gate_stop is not None:
                return {
                    "stop_status": srt_gate_stop["stop_status"],
                    "next_action": srt_gate_stop["next_action"],
                    "parsed_output": output,
                }

        if step.agent_key == "VisualPlanningAgent" and artifacts.get("srt"):
            visual_srt_gate_stop = self._run_custom_deterministic_gates(
                package_id=package_id,
                video_project_id=effective_context_snapshot.video_project_id if effective_context_snapshot else data.video_project_id,
                artifacts=artifacts,
                effective_context_snapshot=effective_context_snapshot,
                provider_readiness_state=provider_readiness_snapshot,
                gate_keys=["visual_srt_timeline_gate"],
                trigger_agent_key="VisualSRTTimelineCrossCheck",
            )
            if visual_srt_gate_stop is not None:
                return {
                    "stop_status": visual_srt_gate_stop["stop_status"],
                    "next_action": visual_srt_gate_stop["next_action"],
                    "parsed_output": output,
                }

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
        if step.agent_key == "ProviderReadinessSummaryAgent":
            artifacts["provider_plan_dry_validation"] = _provider_plan_dry_validation(output.get("artifact"))
        return {"stop_status": None, "next_action": None, "parsed_output": output}

    def _retry_topic_idea_schema_once(
        self,
        *,
        step: PackageAgentStep,
        render: Any,
        validation: Any,
        agent_run_refs: list[dict[str, Any]],
        prompt_audit_snapshot_refs: list[str],
    ) -> dict[str, Any]:
        validation_payload = validation.model_dump(mode="json")
        errors = _strings(_dict(validation_payload.get("validation_result")).get("errors"))
        retry_message = {
            "role": "user",
            "content": (
                "Schema retry for TopicIdeaScoringAgent. Return exactly one complete BaseEnvelope JSON object. "
                "Do not return markdown, prose, or partial JSON. Required missing/invalid fields from previous attempt: "
                f"{errors}. Required top-level fields: contract_version, agent_key, status, confidence_label, "
                "evidence_refs, limitations, next_action, operator_summary_vi, technical_appendix, artifact. "
                "artifact must be an object containing semantic topic scoring, for example "
                "{\"topic_score\":{\"score\":\"UNKNOWN\"},\"risk_assessment\":{\"risk_level\":\"MEDIUM\"}}. "
                "operator_summary_vi must be a non-empty Vietnamese sentence. Do not output top-level risk_level."
            ),
        }
        route = self.llm_router.route(
            lane_name=render.router_lane,
            messages=[message.model_dump() for message in render.rendered_messages] + [retry_message],
            requested_task_type=step.requested_task_type,
            response_format="json",
            correlation_id="m12-2s-full-agent-rehearsal-TopicIdeaScoringAgent-schema-retry",
        )
        audit = {
            "attempted": True,
            "repair_type": "bounded_topic_idea_schema_retry",
            "semantic_change_allowed": False,
            "max_attempts": 1,
            "reason_codes": ["TOPIC_IDEA_SCHEMA_RETRY_MISSING_ARTIFACT"],
            "validation_errors": errors,
            "uses_llm_router": True,
            "mock_or_canned_output_used": False,
        }
        if route.status != "SUCCESS":
            audit.update({"retry_status": route.status, "route_reason_codes": route.reason_codes})
            agent_run_refs.append(self._agent_ref(step, render, route=route, validation=None))
            return {"audit": audit, "validation": None, "raw_output": None}
        raw_output: str | dict[str, Any] | None = route.structured_output or route.content
        retry_validation = self.prompt_registry.validate_output(
            PromptOutputValidationRequest(
                agent_key=step.agent_key,
                raw_output=raw_output or "",
                prompt_render_run_id=render.prompt_render_run_id,
            )
        )
        audit["retry_validation_status"] = retry_validation.status
        audit["retry_validation_reason_codes"] = retry_validation.reason_codes
        audit["repaired"] = bool(retry_validation.validation_result.get("valid")) if isinstance(retry_validation.validation_result, dict) else False
        retry_audit_id = self._latest_audit_id(
            render.prompt_render_run_id,
            provider_refs=[
                {
                    "route_attempt_id": str(route.route_attempt_id),
                    "provider_attempt_id": str(route.provider_attempt_id) if route.provider_attempt_id else None,
                    "llm_run_snapshot_id": str(route.llm_run_snapshot_id) if route.llm_run_snapshot_id else None,
                }
            ],
        )
        if retry_audit_id is not None:
            prompt_audit_snapshot_refs.append(str(retry_audit_id))
        ref = self._agent_ref(step, render, route=route, validation=retry_validation.model_dump(mode="json"))
        ref["retry_reason_codes"] = ["TOPIC_IDEA_SCHEMA_RETRY_MISSING_ARTIFACT"]
        agent_run_refs.append(ref)
        return {"audit": audit, "validation": retry_validation, "raw_output": raw_output}

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
            if provider_key not in {"elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"}:
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

    def _maybe_repair_script_style_once(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        gate_stop: dict[str, Any],
    ) -> dict[str, Any]:
        batch = gate_stop.get("gate_batch")
        fail_codes = batch.fail_codes if batch is not None else []
        if "SCRIPT_FORBIDDEN_STYLE_USED" not in fail_codes or effective_context_snapshot is None:
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None}
        if artifacts.get("script_style_repair_attempt"):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None}
        script = artifacts.get("narration_script")
        if not isinstance(script, dict):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None}
        forbidden_terms = _strings(_dict(effective_context_snapshot.brand_voice_persona_context_json).get("forbidden_style"))
        repaired_script, sentence_patches = _repair_forbidden_style_terms(script, forbidden_terms)
        artifacts["script_style_repair_attempt"] = {
            "attempted": True,
            "repair_type": "rewrite_script_style_only",
            "semantic_change_allowed": False,
            "max_attempts": 1,
            "forbidden_style_terms": forbidden_terms,
            "sentence_patches": sentence_patches,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        if not sentence_patches:
            return {"attempted": True, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"]}
        _refresh_script_duration_self_check(repaired_script, _dict(artifacts.get("duration_model")))
        artifacts["narration_script"] = repaired_script
        rerun = self.deterministic_gates.run_after_agent(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            agent_key="ScriptWriterAgent",
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_snapshot,
        )
        if rerun is None:
            return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None}
        artifacts["deterministic_gate_report"] = _gate_report_after_repair(artifacts.get("deterministic_gate_report"), rerun)
        if rerun.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_MEDIA_PROVIDERS",
                deterministic_batch=rerun,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "attempted": True,
                "repaired": True,
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": rerun,
            }
        return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": rerun}

    def _maybe_repair_script_duration_once(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        gate_stop: dict[str, Any],
    ) -> dict[str, Any]:
        batch = gate_stop.get("gate_batch")
        fail_codes = batch.fail_codes if batch is not None else []
        duration_codes = {"SCRIPT_DURATION_ABOVE_MAXIMUM", "SCRIPT_DURATION_BELOW_MINIMUM", "SCRIPT_WORD_BUDGET_BELOW_MINIMUM"}
        if effective_context_snapshot is None or not any(code in fail_codes for code in duration_codes):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        if artifacts.get("script_duration_repair_attempt"):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        script = artifacts.get("narration_script")
        if not isinstance(script, dict):
            return {"attempted": False, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
        duration_model = _dict(artifacts.get("duration_model"))
        budget = _script_word_budget_contract(duration_model, script_outline=_dict(artifacts.get("script_outline")))
        word_count_before = _script_word_count(script)
        repair_record: dict[str, Any] = {
            "attempted": True,
            "semantic_change_allowed": False,
            "max_attempts": 1,
            "target_words": budget["target_word_count"],
            "minimum_word_count": budget["minimum_word_count"],
            "maximum_word_count": budget["maximum_word_count"],
            "word_count_before": word_count_before,
            "reason_codes": list(fail_codes),
            "preserve_fields": [
                "hook_spec",
                "hook_spec.promise_made",
                "hook_spec.payoff_location",
                "sentence_order",
                "evidence_refs",
            ],
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        if "SCRIPT_DURATION_BELOW_MINIMUM" in fail_codes or "SCRIPT_WORD_BUDGET_BELOW_MINIMUM" in fail_codes:
            repaired_script, sentence_patches = _expand_script_to_word_budget(script, duration_model, budget)
            word_count_after = _script_word_count(repaired_script)
            repair_record.update(
                {
                    "repair_type": "bounded_script_duration_expand",
                    "repaired": bool(sentence_patches)
                    and int(budget["minimum_word_count"]) <= word_count_after <= int(budget["maximum_word_count"]),
                    "repair_status": (
                        "EXPANDED_TO_WORD_BUDGET"
                        if sentence_patches and int(budget["minimum_word_count"]) <= word_count_after <= int(budget["maximum_word_count"])
                        else "NOT_SAFE_TO_EXPAND_WITHOUT_SUFFICIENT_SCRIPT_STRUCTURE"
                    ),
                    "word_count_after": word_count_after,
                    "sentence_patches": sentence_patches,
                    "hook_preserved": repaired_script.get("hook_spec") == script.get("hook_spec"),
                    "payoff_location_preserved": _dict(repaired_script.get("hook_spec")).get("payoff_location")
                    == _dict(script.get("hook_spec")).get("payoff_location"),
                    "section_order_preserved": True,
                }
            )
            artifacts["script_duration_repair_attempt"] = repair_record
            if not repair_record["repaired"]:
                return {"attempted": True, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
            _refresh_script_duration_self_check(repaired_script, duration_model)
            artifacts["narration_script"] = repaired_script
            rerun = self.deterministic_gates.run_after_agent(
                package_id=package_id,
                video_project_id=video_project_id,
                effective_context=effective_context_snapshot,
                agent_key="ScriptWriterAgent",
                artifacts=artifacts,
                provider_readiness_state=provider_readiness_snapshot,
            )
            if rerun is None:
                return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": None}
            artifacts["deterministic_gate_report"] = _gate_report_after_repair(artifacts.get("deterministic_gate_report"), rerun)
            if rerun.status in {GATE_BLOCK, GATE_REVIEW}:
                decision = self.package_status_reducer.resolve(
                    current_status="READY_FOR_MEDIA_PROVIDERS",
                    deterministic_batch=rerun,
                )
                artifacts["package_state_reducer"] = decision
                return {
                    "attempted": True,
                    "repaired": True,
                    "stop_status": decision["package_status"],
                    "next_action": self._next_action_for_reducer_decision(decision),
                    "gate_batch": rerun,
                }
            return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": rerun}
        repaired_script, sentence_patches = _trim_script_to_word_budget(script, duration_model, budget)
        word_count_after = _script_word_count(repaired_script)
        repair_record.update(
            {
                "repair_type": "bounded_script_duration_trim",
                "repaired": bool(sentence_patches) and word_count_after <= int(budget["maximum_word_count"]),
                "repair_status": "TRIMMED_TO_WORD_BUDGET" if sentence_patches else "NO_TRIMMABLE_SENTENCES",
                "word_count_after": word_count_after,
                "sentence_patches": sentence_patches,
                "hook_preserved": repaired_script.get("hook_spec") == script.get("hook_spec"),
                "payoff_location_preserved": _dict(repaired_script.get("hook_spec")).get("payoff_location")
                == _dict(script.get("hook_spec")).get("payoff_location"),
            }
        )
        artifacts["script_duration_repair_attempt"] = repair_record
        if not repair_record["repaired"]:
            return {"attempted": True, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
        _refresh_script_duration_self_check(repaired_script, duration_model)
        artifacts["narration_script"] = repaired_script
        rerun = self.deterministic_gates.run_after_agent(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            agent_key="ScriptWriterAgent",
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_snapshot,
        )
        if rerun is None:
            return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": None}
        artifacts["deterministic_gate_report"] = _gate_report_after_repair(artifacts.get("deterministic_gate_report"), rerun)
        if rerun.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_MEDIA_PROVIDERS",
                deterministic_batch=rerun,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "attempted": True,
                "repaired": True,
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": rerun,
            }
        return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": rerun}

    def _maybe_repair_visual_coverage_once(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        gate_stop: dict[str, Any],
    ) -> dict[str, Any]:
        batch = gate_stop.get("gate_batch")
        fail_codes = batch.fail_codes if batch is not None else []
        repairable_codes = {
            "VISUAL_PLAN_UNKNOWN_SENTENCE_REFS",
            "VISUAL_COVERAGE_MISSING_SENTENCE_IDS",
            "VISUAL_SOURCE_DISALLOWED_BY_CONTRACT",
        }
        if effective_context_snapshot is None or not any(code in fail_codes for code in repairable_codes):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        if artifacts.get("visual_coverage_repair_attempt"):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        visual_plan = artifacts.get("visual_plan")
        narration_script = artifacts.get("narration_script")
        if not isinstance(visual_plan, dict) or not isinstance(narration_script, dict):
            return {"attempted": False, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
        allowed_sources = set(_strings(_dict(effective_context_snapshot.visual_style_context_json).get("allowed_visual_sources")))
        if not allowed_sources:
            allowed_sources = {"DIAGRAM", "CARD", "SCREENSHOT", "EXISTING_ASSET"}
        repaired_plan, patches = _repair_visual_unknown_sentence_refs(visual_plan, narration_script, allowed_sources=allowed_sources)
        artifacts["visual_coverage_repair_attempt"] = {
            "attempted": True,
            "repair_type": "drop_visual_unknown_sentence_refs",
            "semantic_change_allowed": False,
            "max_attempts": 1,
            "reason_codes": list(fail_codes),
            "repaired": bool(patches),
            "patches": patches,
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        if not patches:
            return {"attempted": True, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
        artifacts["visual_plan"] = repaired_plan
        rerun = self.deterministic_gates.run_after_agent(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            agent_key="VisualPlanningAgent",
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_snapshot,
        )
        if rerun is None:
            return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": None}
        artifacts["deterministic_gate_report"] = _gate_report_after_repair(artifacts.get("deterministic_gate_report"), rerun)
        if rerun.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_HUMAN_REVIEW",
                deterministic_batch=rerun,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "attempted": True,
                "repaired": True,
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": rerun,
            }
        return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": rerun}

    def _maybe_repair_ai_disclosure_wording_once(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_snapshot: dict[str, Any],
        gate_stop: dict[str, Any],
    ) -> dict[str, Any]:
        batch = gate_stop.get("gate_batch")
        fail_codes = batch.fail_codes if batch is not None else []
        if effective_context_snapshot is None or "AI_DISCLOSURE_CONDITIONAL_WORDING_MISSING" not in fail_codes:
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        if "AI_MEDIA_DISCLOSURE_FALSE_PRESENT_TENSE" in fail_codes or artifacts.get("disclosure_wording_repair_attempt"):
            return {"attempted": False, "repaired": False, "stop_status": None, "next_action": None, "gate_batch": None}
        metadata = artifacts.get("metadata_package")
        rights = artifacts.get("rights_disclosure_review")
        if not isinstance(metadata, dict) or not isinstance(rights, dict):
            return {"attempted": False, "repaired": False, "stop_status": gate_stop["stop_status"], "next_action": gate_stop["next_action"], "gate_batch": batch}
        note = (
            str(rights.get("disclosure_notes") or "").strip()
            or "Future or planned AI-generated media, if produced later, requires source/provider manifest review and platform disclosure before publishing."
        )
        if "future" not in note.lower() and "planned" not in note.lower():
            note = f"Future planned AI media disclosure note: {note}"
        repaired_metadata = {**metadata, "disclosure_notes": note}
        artifacts["metadata_package"] = repaired_metadata
        artifacts["disclosure_wording_repair_attempt"] = {
            "attempted": True,
            "repair_type": "complete_ai_disclosure_conditional_wording",
            "semantic_change_allowed": False,
            "max_attempts": 1,
            "reason_codes": ["AI_DISCLOSURE_CONDITIONAL_WORDING_MISSING"],
            "repaired": True,
            "metadata_fields": ["disclosure_notes"],
            "source_artifact": "rights_disclosure_review.disclosure_notes",
            "no_provider_media_upload_execution": True,
            "does_not_mutate": ["Channel Contract", "EffectiveChannelRuntimeContextSnapshot", "ChannelProfileVersion"],
        }
        rerun = self.deterministic_gates.run_after_agent(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            agent_key="RightsDisclosureReviewer",
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_snapshot,
        )
        if rerun is None:
            return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": None}
        artifacts["deterministic_gate_report"] = _gate_report_after_repair(artifacts.get("deterministic_gate_report"), rerun)
        if rerun.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_HUMAN_REVIEW",
                deterministic_batch=rerun,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "attempted": True,
                "repaired": True,
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": rerun,
            }
        return {"attempted": True, "repaired": True, "stop_status": None, "next_action": None, "gate_batch": rerun}

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
            "no_luma_api_call": True,
            "no_luma_generation": True,
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
                "no_luma_api_call": True,
                "no_luma_generation": True,
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
                    "Do not return REVIEW_REQUIRED or BLOCK only because ElevenLabs, Luma API, or Creatomate Growth 10K are not configured. "
                    "For valid text/review artifacts, record provider gaps in limitations; VideoGenerationBoundary will block provider execution."
                ),
                "script_writer_artifact_contract": {
                    "required": "artifact.sentences",
                    "sentence_item_fields": ["sentence_id", "text", "approx_seconds"],
                    "sentence_id_format": "S1, S2, S3...",
                    "duration_contract_required": True,
                    "total_approx_seconds_must_be_within_allowed_range": True,
                    "max_seconds_per_sentence": 15,
                    "self_check_required": "artifact.duration_self_check.actual_total_seconds must match narration_word_count / words_per_minute_assumption * 60.",
                    "hook_spec_required": True,
                },
                "duration_model_rule": "Use task_payload.duration_model as read-only source of truth for target duration, word budget, and section allocation.",
                "hook_spec_rule": "ScriptPlanningAgent or ScriptWriterAgent must provide hook_spec with first_3_seconds_script, first_3_seconds_visual, promise_made, payoff_location, clickbait_risk, visual_hook_relevance, and title_hook_alignment before visual/provider planning.",
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

    def _runtime_context_refs(
        self,
        *,
        context_pack: dict[str, Any],
        context_pack_snapshot: AgentContextPackSnapshot | None,
        render: Any,
        snapshot: CompiledChannelPolicySnapshot,
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
    ) -> dict[str, Any]:
        relevant_paths: list[str] = []
        for digest in (context_pack.get("digests") or {}).values():
            if isinstance(digest, dict):
                relevant_paths.extend(str(path) for path in digest.get("relevant_contract_paths", []) if path)
        audit_refs = context_pack.get("audit_refs") if isinstance(context_pack.get("audit_refs"), dict) else {}
        return {
            "effective_context_snapshot_id": str(effective_context_snapshot.id) if effective_context_snapshot else audit_refs.get("effective_context_snapshot_id"),
            "compiled_policy_snapshot_id": str(snapshot.id),
            "channel_contract_hash": (
                effective_context_snapshot.channel_contract_hash
                if effective_context_snapshot
                else audit_refs.get("channel_contract_hash")
            ),
            "prompt_context_hash": render.prompt_context_hash,
            "agent_context_pack_snapshot_id": str(context_pack_snapshot.id) if context_pack_snapshot else None,
            "context_pack_hash": context_pack_snapshot.context_pack_hash if context_pack_snapshot else context_pack.get("context_pack_hash"),
            "relevant_contract_paths_used": sorted(set(relevant_paths)) or ["effective_channel_runtime_context_snapshot"],
        }

    def _validate_agent_output(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        step: PackageAgentStep,
        raw_output: Any,
        parsed_output: dict[str, Any] | None,
        prompt_validation_result: dict[str, Any],
        runtime_context_refs: dict[str, Any],
        render: Any,
        context_pack_snapshot: AgentContextPackSnapshot | None,
    ):
        return self.output_validation.validate(
            package_id=package_id,
            video_project_id=video_project_id,
            agent_key=step.agent_key,
            raw_output=raw_output,
            parsed_output=parsed_output,
            prompt_validation_result=prompt_validation_result,
            runtime_context_refs=runtime_context_refs,
            prompt_render_run_id=render.prompt_render_run_id,
            agent_context_pack_snapshot_id=context_pack_snapshot.id if context_pack_snapshot else None,
            raw_output_ref=f"prompt-output:{render.prompt_render_run_id}",
        )

    def _run_agent_deterministic_gates(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        step: PackageAgentStep,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if effective_context_snapshot is None:
            return None
        batch = self.deterministic_gates.run_after_agent(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            agent_key=step.agent_key,
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_state,
        )
        if batch is None:
            return None
        artifacts["deterministic_gate_report"] = compact_gate_report(
            artifacts.get("deterministic_gate_report"),
            batch,
        )
        if batch.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_HUMAN_REVIEW",
                deterministic_batch=batch,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": batch,
            }
        return None

    def _run_package_deterministic_gates(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_state: dict[str, Any] | None = None,
        include_provider_boundary: bool = False,
    ):
        if effective_context_snapshot is None:
            return None
        batch = self.deterministic_gates.run_final_package_gates(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            artifacts=artifacts,
            provider_readiness_state=provider_readiness_state,
            include_provider_boundary=include_provider_boundary,
        )
        artifacts["deterministic_gate_report"] = compact_gate_report(
            artifacts.get("deterministic_gate_report"),
            batch,
        )
        return batch

    def _run_custom_deterministic_gates(
        self,
        *,
        package_id: uuid.UUID,
        video_project_id: uuid.UUID | None,
        artifacts: dict[str, Any],
        effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
        provider_readiness_state: dict[str, Any] | None,
        gate_keys: list[str],
        trigger_agent_key: str,
    ) -> dict[str, Any] | None:
        if effective_context_snapshot is None:
            return None
        batch = self.deterministic_gates.run_batch(
            package_id=package_id,
            video_project_id=video_project_id,
            effective_context=effective_context_snapshot,
            artifacts=artifacts,
            gate_keys=gate_keys,
            trigger_agent_key=trigger_agent_key,
            provider_readiness_state=provider_readiness_state,
        )
        artifacts["deterministic_gate_report"] = compact_gate_report(
            artifacts.get("deterministic_gate_report"),
            batch,
        )
        if batch.status in {GATE_BLOCK, GATE_REVIEW}:
            decision = self.package_status_reducer.resolve(
                current_status="READY_FOR_HUMAN_REVIEW",
                deterministic_batch=batch,
            )
            artifacts["package_state_reducer"] = decision
            return {
                "stop_status": decision["package_status"],
                "next_action": self._next_action_for_reducer_decision(decision),
                "gate_batch": batch,
            }
        return None

    def _next_action_for_reducer_decision(self, decision: dict[str, Any]) -> str:
        source = decision.get("source")
        status = decision.get("package_status")
        if status == "WAITING_PROVIDER_CONFIG":
            return MEDIA_PROVIDER_BOUNDARY_NEXT_ACTION
        if source == "deterministic_gates":
            return "Sửa deterministic gate blockers trước khi chuyển trạng thái package."
        if source == "gatekeeper_soft_review":
            return "Review kết quả GatekeeperSoftReviewAgent trước khi tiếp tục."
        return HUMAN_APPROVAL_REQUIRED

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
        if result == "REVIEW_REQUIRED":
            return "REVIEW_REQUIRED"
        return "REVIEW_REQUIRED"

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
            for provider in ("elevenlabs", "creatomate_growth_10k")
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
                {"provider_key": "creatomate_growth_10k", "role": "Creatomate Growth 10K final/template render", "required": True},
                {"provider_key": "luma_api", "role": "optional Luma API AI hero", "required": False},
                {"provider_key": "pexels_api", "role": "optional Pexels API visual fallback", "required": False},
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
        m2 = {
            item.provider_key: self._m2_provider_boundary_state(item.model_dump(mode="json"))
            for item in ProviderReadinessM2Service(self.settings).snapshot().providers
        }
        return {
            "elevenlabs": m2.get("elevenlabs") or self._provider_boundary_state(summaries.get("elevenlabs")),
            "creatomate_growth_10k": m2.get("creatomate_growth_10k") or self._provider_boundary_state(summaries.get("creatomate_growth_10k")),
            "luma_api": {**m2.get("luma_api", {"status": "NOT_CONFIGURED"}), "required": False},
            "pexels_api": {**m2.get("pexels_api", {"status": "NOT_CONFIGURED"}), "required": False},
            "google_drive_archive": {**m2.get("google_drive_archive", {"status": "DISABLED"}), "required": False},
            "youtube_readonly": {**m2.get("youtube_readonly", {"status": "DISABLED"}), "required": False},
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

    def _m2_provider_boundary_state(self, item: dict[str, Any]) -> dict[str, Any]:
        readiness_state = str(item.get("readiness_state") or "UNKNOWN")
        reason_codes = list(item.get("blocker_reason_codes") or [])
        missing_env_keys = list(item.get("missing_env_keys") or [])
        if readiness_state in {"READY_FOR_HUMAN_PAID_APPROVAL", "READY_FOR_FUTURE_EXECUTION", "CAPABILITY_READY"}:
            status = "CONFIGURED"
        elif readiness_state == "DISABLED":
            status = "DISABLED"
        elif missing_env_keys or any("CREDENTIAL" in code or "KEY" in code for code in reason_codes):
            status = "NEEDS_CREDENTIAL"
        else:
            status = "NOT_CONFIGURED"
        return {
            "status": status,
            "required": item.get("provider_key") in {"elevenlabs", "creatomate_growth_10k"},
            "readiness_state": readiness_state,
            "missing_env_keys": missing_env_keys,
            "reason_codes": reason_codes,
            "next_action": item.get("future_required_next_action"),
            "m2_wiring_only": True,
            "no_provider_calls_confirmed": True,
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
                "Visual plan là brief/candidate-only, chưa tạo Luma/Creatomate output.",
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


def _find_visual_source_values(value: Any, *, in_scene: bool = False) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"visual_source", "intended_visual_source"} and isinstance(item, str):
                found.add(item)
            elif key == "source_type" and in_scene and isinstance(item, str):
                found.add(item)
            elif key == "scenes" and isinstance(item, list):
                for scene in item:
                    found.update(_find_visual_source_values(scene, in_scene=True))
            elif key not in {"evidence_refs", "applied_context_refs", "runtime_context_refs", "source_manifest_refs"}:
                found.update(_find_visual_source_values(item, in_scene=in_scene))
    elif isinstance(value, list):
        for item in value:
            found.update(_find_visual_source_values(item, in_scene=in_scene))
    return found


def _repair_visual_unknown_sentence_refs(
    visual_plan: dict[str, Any],
    narration_script: dict[str, Any],
    *,
    allowed_sources: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    valid_sentence_ids = {
        str(item.get("sentence_id"))
        for item in _list(narration_script.get("sentences"))
        if isinstance(item, dict) and item.get("sentence_id")
    }
    if not valid_sentence_ids:
        return visual_plan, []
    allowed_sources = allowed_sources or {"DIAGRAM", "CARD", "SCREENSHOT", "EXISTING_ASSET"}
    repaired = {**visual_plan}
    scenes = [dict(item) if isinstance(item, dict) else item for item in _list(visual_plan.get("scenes"))]
    repaired_scenes: list[Any] = []
    patches: list[dict[str, Any]] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            repaired_scenes.append(scene)
            continue
        scene_patches: list[dict[str, Any]] = []
        scene_ref = scene.get("scene_id") or scene.get("scene_index")
        ref_key = next(
            (
                key
                for key in (
                    "sentence_ids",
                    "covered_sentence_ids",
                    "sentence_ids_covered",
                    "covers_sentence_ids",
                    "sentence_refs",
                    "primary_sentence_ids",
                    "narration_sentence_ids",
                    "sentence_range",
                    "sentence_id",
                )
                if scene.get(key)
            ),
            None,
        )
        if ref_key is not None:
            refs = _strings(scene.get(ref_key))
            unknown_refs = [ref for ref in refs if ref not in valid_sentence_ids]
            kept_refs = [ref for ref in refs if ref in valid_sentence_ids]
            if ref_key in {
                "covers_sentence_ids",
                "sentence_refs",
                "sentence_range",
                "sentence_ids_covered",
                "primary_sentence_ids",
                "narration_sentence_ids",
            }:
                scene["sentence_ids"] = kept_refs or refs
                scene_patches.append(
                    {
                        "scene_id": scene_ref,
                        "repair_action": f"normalize_{ref_key}",
                        "kept_sentence_refs": kept_refs or refs,
                    }
                )
            if unknown_refs:
                patch = {
                    "scene_id": scene_ref,
                    "removed_unknown_sentence_refs": unknown_refs,
                    "kept_sentence_refs": kept_refs,
                }
                if not kept_refs:
                    patch["repair_action"] = "drop_unanchored_visual_scene"
                    patches.extend(scene_patches)
                    patches.append(patch)
                    continue
                scene["sentence_ids"] = kept_refs
                if ref_key != "sentence_ids":
                    scene.pop(ref_key, None)
                patch["repair_action"] = "drop_unknown_sentence_refs"
                scene_patches.append(patch)
        source = scene.get("intended_visual_source") or scene.get("visual_source") or scene.get("source_type")
        if isinstance(source, str) and source not in allowed_sources:
            fallback = _visual_source_fallback(source, allowed_sources)
            if fallback is None:
                return visual_plan, []
            scene["intended_visual_source"] = fallback
            scene["candidate_provider_backed"] = False
            scene["provider_dependencies"] = []
            scene["provider_readiness"] = "NOT_APPLICABLE_STATIC_VISUAL_SOURCE_REPAIR"
            scene_patches.append(
                {
                    "scene_id": scene_ref,
                    "repair_action": "normalize_disallowed_candidate_visual_source",
                    "before_visual_source": source,
                    "after_visual_source": fallback,
                }
            )
        patches.extend(scene_patches)
        repaired_scenes.append(scene)
    if not patches:
        return visual_plan, []
    repaired["scenes"] = repaired_scenes
    covered: set[str] = set()
    unknown: set[str] = set()
    disallowed: set[str] = set()
    for scene in repaired_scenes:
        if not isinstance(scene, dict):
            continue
        refs = _strings(
            scene.get("sentence_ids")
            or scene.get("covered_sentence_ids")
            or scene.get("sentence_ids_covered")
            or scene.get("covers_sentence_ids")
            or scene.get("sentence_refs")
            or scene.get("primary_sentence_ids")
            or scene.get("narration_sentence_ids")
            or scene.get("sentence_range")
            or scene.get("sentence_id")
        )
        for ref in refs:
            if ref in valid_sentence_ids:
                covered.add(ref)
            else:
                unknown.add(ref)
        source = scene.get("intended_visual_source") or scene.get("visual_source") or scene.get("source_type")
        if isinstance(source, str) and source not in allowed_sources:
            disallowed.add(source)
    if unknown or disallowed or valid_sentence_ids - covered:
        missing_ids = [
            str(item.get("sentence_id"))
            for item in _list(narration_script.get("sentences"))
            if isinstance(item, dict) and item.get("sentence_id") and str(item.get("sentence_id")) not in covered
        ]
        if unknown or disallowed or not missing_ids:
            return visual_plan, []
        fallback_source = "CARD" if "CARD" in allowed_sources else ("DIAGRAM" if "DIAGRAM" in allowed_sources else None)
        if fallback_source is None:
            return visual_plan, []
        for index in range(0, len(missing_ids), 6):
            group = missing_ids[index : index + 6]
            repaired_scenes.append(
                {
                    "scene_id": f"AUTO_COVERAGE_{(index // 6) + 1:02d}",
                    "sentence_ids": group,
                    "intended_visual_source": fallback_source,
                    "visual_description": f"Static operator visual covering narration range {group[0]}-{group[-1]}.",
                    "overlay_text": "Workflow step summary",
                    "deterministic_coverage_repair": True,
                    "provider_dependencies": [],
                    "provider_readiness": "NOT_APPLICABLE_STATIC_VISUAL_SOURCE_REPAIR",
                }
            )
            patches.append(
                {
                    "scene_id": f"AUTO_COVERAGE_{(index // 6) + 1:02d}",
                    "repair_action": "add_grouped_static_visual_coverage_scene",
                    "covered_sentence_ids": group,
                    "intended_visual_source": fallback_source,
                }
            )
        repaired["scenes"] = repaired_scenes
        covered.update(missing_ids)
    if unknown or disallowed or valid_sentence_ids - covered:
        return visual_plan, []
    return repaired, patches


def _visual_source_fallback(source: str, allowed_sources: set[str]) -> str | None:
    if source == "LUMA_HERO_CANDIDATE_ONLY" and "DIAGRAM" in allowed_sources:
        return "DIAGRAM"
    if source == "CREATOMATE_CARD_CANDIDATE_ONLY" and "CARD" in allowed_sources:
        return "CARD"
    if "CARD" in allowed_sources:
        return "CARD"
    if "DIAGRAM" in allowed_sources:
        return "DIAGRAM"
    return None


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


def _duration_model_from_context(
    effective_context_snapshot: EffectiveChannelRuntimeContextSnapshot | None,
    *,
    target_video_type: str,
) -> dict[str, Any]:
    policy = {}
    if effective_context_snapshot is not None:
        category = effective_context_snapshot.category_runtime_context_json or {}
        if isinstance(category, dict):
            policy = category.get("default_format_policy") if isinstance(category.get("default_format_policy"), dict) else {}
    target = _first_number(policy.get("target_duration_seconds"), policy.get("target_seconds"))
    allowed = _dict(policy.get("allowed_duration_range_seconds"))
    min_seconds = _first_number(allowed.get("min"), policy.get("min_seconds"), policy.get("target_duration_seconds_min"))
    max_seconds = _first_number(allowed.get("max"), policy.get("max_seconds"), policy.get("target_duration_seconds_max"))
    long_form = _dict(policy.get("long_form"))
    if long_form and target is None:
        minutes = _dict(long_form.get("target_duration_minutes"))
        min_minutes = _first_number(minutes.get("min"), long_form.get("min_minutes"))
        max_minutes = _first_number(minutes.get("max"), long_form.get("max_minutes"))
        min_seconds = _first_number(min_seconds, long_form.get("min_seconds"), min_minutes * 60 if min_minutes is not None else None)
        max_seconds = _first_number(max_seconds, long_form.get("max_seconds"), max_minutes * 60 if max_minutes is not None else None)
        if min_seconds is not None and max_seconds is not None:
            target = round((min_seconds + max_seconds) / 2)
    if target is None and target_video_type == "long_form":
        target = 450
    if min_seconds is None and target is not None:
        min_seconds = round(target * 0.9)
    if max_seconds is None and target is not None:
        max_seconds = round(target * 1.1)
    wpm = int(policy.get("words_per_minute_assumption") or 140)
    words_target = round((float(target or 0) / 60) * wpm)
    return {
        "target_format": target_video_type,
        "target_duration_seconds": target,
        "allowed_duration_range_seconds": {"min": min_seconds, "max": max_seconds},
        "narration_words_target": words_target,
        "words_per_minute_assumption": wpm,
        "variance_policy": "0.90_to_1.10_target_seconds",
        "source": "EffectiveChannelRuntimeContextSnapshot.category_runtime_context_json.default_format_policy_or_package_default",
        "read_only": True,
    }


def _provider_plan_dry_validation(artifact: Any) -> dict[str, Any]:
    providers = _dict(_dict(artifact).get("providers"))
    canonical = ["elevenlabs", "luma_api", "creatomate_growth_10k", "pexels_api"]
    observed = sorted(key for key in providers if key in canonical)
    return {
        "status": "REACHED",
        "will_execute": False,
        "canonical_provider_keys": canonical,
        "observed_provider_keys": observed,
        "no_network_call_made": True,
        "no_final_media_ref": True,
        "no_human_upload_task": True,
    }


def _build_srt_caption_artifact(
    *,
    package_id: uuid.UUID,
    video_project_id: uuid.UUID | None,
    script: dict[str, Any],
    duration_model: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    wpm = _first_number(duration_model.get("words_per_minute_assumption")) or 140.0
    cues: list[dict[str, Any]] = []
    current = 0.0
    for sentence_index, sentence in enumerate(sentences, start=1):
        sentence_id = str(sentence.get("sentence_id") or sentence.get("id") or f"S{sentence_index}")
        chunks = _caption_chunks(str(sentence.get("text") or ""), max_words=16)
        sentence_word_count = sum(len(chunk.split()) for chunk in chunks)
        sentence_duration = round((sentence_word_count / wpm) * 60, 3) if sentence_word_count else 0.0
        chunk_durations = _caption_chunk_durations(chunks, sentence_duration, wpm=wpm)
        for chunk, duration in zip(chunks, chunk_durations, strict=False):
            words = chunk.split()
            if not words:
                continue
            start = current
            end = round(current + duration, 3)
            lines = _wrap_caption_lines(chunk, max_chars=42)
            cues.append(
                {
                    "index": len(cues) + 1,
                    "start_seconds": round(start, 3),
                    "end_seconds": end,
                    "duration_seconds": round(end - start, 3),
                    "text": chunk,
                    "text_lines": lines,
                    "sentence_ids": [sentence_id],
                }
            )
            current = end
    srt_total = round(current, 3)
    estimated_total = _script_estimated_seconds_for_srt(script, duration_model)
    if cues and abs(srt_total - estimated_total) > 0.001:
        delta = round(estimated_total - srt_total, 3)
        last = cues[-1]
        adjusted_duration = round(last["duration_seconds"] + delta, 3)
        if 1.0 <= adjusted_duration <= 7.0:
            last["end_seconds"] = round(last["end_seconds"] + delta, 3)
            last["duration_seconds"] = adjusted_duration
            srt_total = round(last["end_seconds"], 3)
    srt_text = _render_srt(cues)
    artifact_dir = repo_root / "var" / "tmp" / "pa1-precheck-srt" / str(package_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    local_path = artifact_dir / "narration.en.srt"
    local_path.write_text(srt_text, encoding="utf-8")
    checksum = hashlib.sha256(local_path.read_bytes()).hexdigest()
    return {
        "artifact_type": "SRT_CAPTION_FILE",
        "language": "en",
        "package_id": str(package_id),
        "video_project_id": str(video_project_id) if video_project_id else None,
        "script_artifact_ref": f"first_scripted_video_package:{package_id}:artifacts.narration_script",
        "estimated_total_seconds": estimated_total,
        "srt_total_seconds": srt_total,
        "caption_count": len(cues),
        "checksum_sha256": checksum,
        "local_path": str(local_path),
        "lifecycle_state": "DRAFT_SCRIPT_TIMING",
        "not_final_media": True,
        "not_publishable": True,
        "provider_calls_made": False,
        "upload_publish_made": False,
        "final": False,
        "cloud_media_ref_created": False,
        "final_media_ref_created": False,
        "human_upload_task_created": False,
        "srt": srt_text,
        "content": srt_text,
        "cues": cues,
    }


def _caption_chunks(text: str, *, max_words: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = [*current, word]
        if current and (len(candidate) > max_words or not _caption_chunk_fits(candidate)):
            chunks.append(" ".join(current).strip())
            current = [word]
        else:
            current = candidate
    if current:
        chunks.append(" ".join(current).strip())
    return chunks


def _caption_chunk_fits(words: list[str]) -> bool:
    lines = _wrap_caption_lines(" ".join(words), max_chars=42)
    return len(lines) <= 2 and all(len(line) <= 42 for line in lines)


def _caption_chunk_durations(chunks: list[str], sentence_duration: float, *, wpm: float) -> list[float]:
    if not chunks:
        return []
    word_counts = [len(chunk.split()) for chunk in chunks]
    raw = [round((count / wpm) * 60, 3) if count else 0.0 for count in word_counts]
    durations = [min(7.0, max(1.0, value)) for value in raw]
    target = max(float(len(chunks)), sentence_duration)
    delta = round(sum(durations) - target, 3)
    if delta > 0:
        for index in sorted(range(len(durations)), key=lambda item: durations[item], reverse=True):
            removable = min(delta, max(0.0, durations[index] - 1.0))
            if removable <= 0:
                continue
            durations[index] = round(durations[index] - removable, 3)
            delta = round(delta - removable, 3)
            if delta <= 0:
                break
    elif delta < 0:
        delta = abs(delta)
        for index in sorted(range(len(durations)), key=lambda item: durations[item]):
            addable = min(delta, max(0.0, 7.0 - durations[index]))
            if addable <= 0:
                continue
            durations[index] = round(durations[index] + addable, 3)
            delta = round(delta - addable, 3)
            if delta <= 0:
                break
    return [round(value, 3) for value in durations]


def _wrap_caption_lines(text: str, *, max_chars: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return lines
    midpoint = max(1, len(words) // 2)
    return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]


def _render_srt(cues: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for cue in cues:
        lines = cue.get("text_lines") or [cue.get("text") or ""]
        blocks.append(
            "\n".join(
                [
                    str(cue["index"]),
                    f"{_format_srt_timestamp(float(cue['start_seconds']))} --> {_format_srt_timestamp(float(cue['end_seconds']))}",
                    *[str(line) for line in lines],
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _format_srt_timestamp(seconds: float) -> str:
    millis_total = int(round(seconds * 1000))
    hours, remainder = divmod(millis_total, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _script_estimated_seconds_for_srt(script: dict[str, Any], duration_model: dict[str, Any]) -> float:
    word_count = _script_word_count(script)
    wpm = _first_number(duration_model.get("words_per_minute_assumption")) or 140.0
    if word_count and wpm:
        return round((word_count / wpm) * 60, 3)
    return round(sum(_first_number(item.get("approx_seconds")) or 0 for item in _list(script.get("sentences")) if isinstance(item, dict)), 3)


def _script_duration_contract(duration_model: Any, *, script_outline: dict[str, Any] | None = None) -> dict[str, Any]:
    model = _dict(duration_model)
    budget = _script_word_budget_contract(model, script_outline=_dict(script_outline))
    min_seconds = budget["min_seconds"]
    max_seconds = budget["max_seconds"]
    target_seconds = budget["target_seconds"]
    words_target = budget["target_word_count"]
    max_seconds_per_sentence = 15
    duration_sentence_count = int((float(min_seconds or target_seconds or 0) + max_seconds_per_sentence - 1) // max_seconds_per_sentence)
    minimum_word_count = int(budget["minimum_word_count"])
    maximum_word_count = int(budget["maximum_word_count"])
    min_sentence_count = duration_sentence_count
    target_sentence_count = int((float(words_target or minimum_word_count or 0) + 23) // 24)
    contract = {
        "required": bool(target_seconds),
        "target_seconds": target_seconds,
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "narration_words_target": words_target,
        "minimum_word_count": minimum_word_count,
        "maximum_word_count": maximum_word_count,
        "words_per_minute_assumption": model.get("words_per_minute_assumption"),
        "max_seconds_per_sentence": max_seconds_per_sentence,
        "minimum_sentence_count": min_sentence_count,
        "target_sentence_count_range": {
            "min": min_sentence_count,
            "max": max(min_sentence_count + 10, target_sentence_count + 12),
        },
        "recommended_average_words_per_sentence": 20,
        "maximum_average_words_per_sentence": 28,
        "must_not_downgrade_target_format": True,
        "source": model.get("source"),
        "read_only": True,
        "section_word_budgets": budget["section_word_budgets"],
        "output_word_range_rule": f"Output narration must be between {minimum_word_count} and {maximum_word_count} words.",
        "max_words_rule": "Do not exceed maximum_word_count. Prefer concise narration over extra examples, recaps, or repeated disclaimers.",
    }
    return contract


def _script_word_budget_contract(duration_model: dict[str, Any], *, script_outline: dict[str, Any] | None = None) -> dict[str, Any]:
    model = _dict(duration_model)
    allowed = _dict(model.get("allowed_duration_range_seconds"))
    target_seconds = _first_number(model.get("target_duration_seconds") or model.get("target_seconds")) or 0
    min_seconds = _first_number(allowed.get("min"), model.get("min_seconds")) or round(target_seconds * 0.9)
    max_seconds = _first_number(allowed.get("max"), model.get("max_seconds")) or round(target_seconds * 1.1)
    wpm = _first_number(model.get("words_per_minute_assumption")) or 140
    target_word_count = int(round((_first_number(model.get("narration_words_target")) or (target_seconds / 60 * wpm))))
    minimum_word_count = int(round((float(min_seconds) / 60) * float(wpm)))
    maximum_word_count = int(round((float(max_seconds) / 60) * float(wpm)))
    if target_word_count <= 0:
        target_word_count = int(round((minimum_word_count + maximum_word_count) / 2))
    section_word_budgets = _normalize_section_word_budgets(
        _list(_dict(script_outline).get("section_budgets")),
        target_word_count=target_word_count,
        target_seconds=target_seconds,
    )
    return {
        "target_seconds": target_seconds,
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "words_per_minute_assumption": wpm,
        "target_word_count": target_word_count,
        "minimum_word_count": minimum_word_count,
        "maximum_word_count": maximum_word_count,
        "section_word_budgets": section_word_budgets,
        "read_only": True,
        "source": model.get("source"),
    }


def _normalize_section_word_budgets(
    section_budgets: list[Any],
    *,
    target_word_count: int,
    target_seconds: float | int,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, item in enumerate(section_budgets, start=1):
        section = _dict(item)
        if not section:
            continue
        name = str(section.get("section_id") or section.get("section") or f"section_{index}")
        seconds = _first_number(section.get("seconds"), section.get("duration_seconds"), section.get("target_seconds"))
        words = _first_number(section.get("word_target"), section.get("target_words"), section.get("words"), section.get("raw_word_target"))
        sections.append({"section_id": name, "seconds": seconds, "raw_word_target": words})
    if not sections:
        defaults = [
            ("hook", 0.10),
            ("problem", 0.18),
            ("solution", 0.24),
            ("mechanism", 0.24),
            ("proof_and_caveats", 0.14),
            ("close", 0.10),
        ]
        return _distribute_word_budget(defaults, target_word_count=target_word_count, target_seconds=target_seconds)
    weights: list[float] = []
    for section in sections:
        raw_words = _first_number(section.get("raw_word_target"))
        seconds = _first_number(section.get("seconds"))
        weights.append(float(raw_words or seconds or 1))
    total_weight = sum(weights) or float(len(sections))
    allocated = [max(1, int(round(target_word_count * weight / total_weight))) for weight in weights]
    delta = target_word_count - sum(allocated)
    index = 0
    while delta:
        step = 1 if delta > 0 else -1
        if allocated[index] + step > 0:
            allocated[index] += step
            delta -= step
        index = (index + 1) % len(allocated)
    normalized: list[dict[str, Any]] = []
    for section, words in zip(sections, allocated):
        seconds = _first_number(section.get("seconds"))
        normalized.append(
            {
                "section_id": section["section_id"],
                "word_target": words,
                "min_words": max(1, int(round(words * 0.9))),
                "max_words": max(1, int(round(words * 1.1))),
                "seconds": seconds,
            }
        )
    return normalized


def _distribute_word_budget(defaults: list[tuple[str, float]], *, target_word_count: int, target_seconds: float | int) -> list[dict[str, Any]]:
    sections = [{"section_id": name, "raw_word_target": weight, "seconds": round(float(target_seconds or 0) * weight, 3)} for name, weight in defaults]
    return _normalize_section_word_budgets(sections, target_word_count=target_word_count, target_seconds=target_seconds)


def _script_word_count(script: dict[str, Any]) -> int:
    return sum(len(str(item.get("text") or "").split()) for item in _list(script.get("sentences")) if isinstance(item, dict))


def _gate_stop_fail_codes(gate_stop: dict[str, Any]) -> list[str]:
    batch = gate_stop.get("gate_batch")
    return list(getattr(batch, "fail_codes", []) or [])


def _topic_idea_needs_schema_retry(validation_payload: dict[str, Any]) -> bool:
    if validation_payload.get("status") != "REVIEW_REQUIRED":
        return False
    result = _dict(validation_payload.get("validation_result"))
    if result.get("valid") is True:
        return False
    parsed = _dict(validation_payload.get("parsed_output"))
    if parsed.get("agent_key") != "TopicIdeaScoringAgent":
        return False
    errors = " ".join(_strings(result.get("errors")))
    return "artifact" in errors and not isinstance(parsed.get("artifact"), dict)


def _trim_script_to_word_budget(
    script: dict[str, Any],
    duration_model: dict[str, Any],
    budget: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    max_words = max(1, int(budget["maximum_word_count"]) - 8)
    sentences = [dict(item) if isinstance(item, dict) else item for item in _list(script.get("sentences"))]
    repaired = {**script, "sentences": sentences}
    patches: list[dict[str, Any]] = []
    current_words = _script_word_count(repaired)
    if current_words <= max_words:
        _refresh_script_duration_self_check(repaired, duration_model)
        return repaired, patches
    trimmable = [item for item in sentences if isinstance(item, dict) and str(item.get("text") or "").strip()]
    if not trimmable:
        return repaired, patches
    min_sentence_words = 6
    while current_words > max_words:
        candidates = sorted(
            (
                (len(str(item.get("text") or "").split()), index, item)
                for index, item in enumerate(trimmable)
                if len(str(item.get("text") or "").split()) > min_sentence_words
            ),
            reverse=True,
        )
        if not candidates:
            break
        word_count, _, sentence = candidates[0]
        remove_count = min(word_count - min_sentence_words, current_words - max_words, max(1, word_count - 10))
        before = str(sentence.get("text") or "")
        words = before.split()
        after = " ".join(words[: word_count - remove_count]).rstrip(" ,;:-")
        if after and after[-1] not in ".!?":
            after += "."
        sentence["text"] = after
        sentence["approx_seconds"] = round((len(after.split()) / (_first_number(duration_model.get("words_per_minute_assumption")) or 140)) * 60, 3)
        patches.append(
            {
                "sentence_id": str(sentence.get("sentence_id") or len(patches) + 1),
                "before_word_count": word_count,
                "after_word_count": len(after.split()),
                "removed_words": remove_count,
                "repair_action": "trim_sentence_tail_to_word_budget",
            }
        )
        current_words = _script_word_count(repaired)
    _refresh_script_duration_self_check(repaired, duration_model)
    return repaired, patches


def _expand_script_to_word_budget(
    script: dict[str, Any],
    duration_model: dict[str, Any],
    budget: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    minimum_words = int(budget["minimum_word_count"])
    target_words = min(int(budget["target_word_count"]), int(budget["maximum_word_count"]) - 8)
    wpm = _first_number(duration_model.get("words_per_minute_assumption")) or 140.0
    sentences = [dict(item) if isinstance(item, dict) else item for item in _list(script.get("sentences"))]
    repaired = {**script, "sentences": sentences}
    patches: list[dict[str, Any]] = []
    current_words = _script_word_count(repaired)
    if current_words >= minimum_words:
        _refresh_script_duration_self_check(repaired, duration_model)
        return repaired, patches
    eligible = [item for item in sentences if isinstance(item, dict) and str(item.get("text") or "").strip()]
    if current_words < max(1, int(minimum_words * 0.5)) or len(eligible) < 20:
        _refresh_script_duration_self_check(repaired, duration_model)
        return repaired, patches
    expansion_clauses = [
        "This keeps the claim tied to verified time savings before publication.",
        "The workflow should reduce repeated handoffs while keeping human review in place.",
        "Operators still verify the numbers and exceptions before any public use.",
        "The team monitors edge cases instead of repeating the full task manually.",
        "This adds practical detail without changing the original hook promise.",
    ]
    index = 0
    max_iterations = max(1, len(eligible) * 4)
    while current_words < target_words and index < max_iterations:
        sentence = eligible[index % len(eligible)]
        before = str(sentence.get("text") or "").strip()
        before_words = len(before.split())
        if before_words >= 34 and index < len(eligible) * 2:
            index += 1
            continue
        clause = expansion_clauses[index % len(expansion_clauses)]
        stem = before[:-1].rstrip() if before.endswith((".", "!", "?")) else before
        after = f"{stem}; {clause}"
        sentence["text"] = after
        sentence["approx_seconds"] = round((len(after.split()) / wpm) * 60, 3)
        patches.append(
            {
                "sentence_id": str(sentence.get("sentence_id") or len(patches) + 1),
                "before_word_count": before_words,
                "after_word_count": len(after.split()),
                "added_words": max(0, len(after.split()) - before_words),
                "repair_action": "expand_existing_sentence_to_word_budget",
            }
        )
        current_words = _script_word_count(repaired)
        index += 1
    _refresh_script_duration_self_check(repaired, duration_model)
    return repaired, patches


def _repair_forbidden_style_terms(script: dict[str, Any], forbidden_terms: list[str]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    repaired = {**script, "sentences": [dict(item) if isinstance(item, dict) else item for item in _list(script.get("sentences"))]}
    patches: list[dict[str, str]] = []
    for sentence in repaired["sentences"]:
        if not isinstance(sentence, dict):
            continue
        before = str(sentence.get("text") or "")
        after = before
        for term in forbidden_terms:
            if term:
                after = re.sub(re.escape(term), "", after, flags=re.IGNORECASE)
        after = re.sub(r"\s+([,.;:!?])", r"\1", after)
        after = re.sub(r"\b(No|Avoid)\s*,\s*", r"\1 ", after, flags=re.IGNORECASE)
        after = re.sub(r"\b(No|Avoid)\s+\1\b", r"\1", after, flags=re.IGNORECASE)
        after = re.sub(r"\s{2,}", " ", after).strip()
        if after != before:
            sentence["text"] = after
            patches.append(
                {
                    "sentence_id": str(sentence.get("sentence_id") or len(patches) + 1),
                    "before_text": before,
                    "after_text": after,
                }
            )
    return repaired, patches


def _refresh_script_duration_self_check(script: dict[str, Any], duration_model: dict[str, Any]) -> None:
    sentences = [item for item in _list(script.get("sentences")) if isinstance(item, dict)]
    word_count = sum(len(str(item.get("text") or "").split()) for item in sentences)
    sentence_total = sum(_first_number(item.get("approx_seconds")) or 0 for item in sentences)
    wpm = _first_number(duration_model.get("words_per_minute_assumption")) or 140.0
    actual_total = round((word_count / wpm) * 60, 3) if word_count and wpm else round(sentence_total, 3)
    target = _first_number(duration_model.get("target_duration_seconds") or duration_model.get("target_seconds"))
    allowed_range = _dict(duration_model.get("allowed_duration_range_seconds"))
    min_seconds = _first_number(allowed_range.get("min"), duration_model.get("min_seconds"))
    max_seconds = _first_number(allowed_range.get("max"), duration_model.get("max_seconds"))
    words_target = _first_number(duration_model.get("narration_words_target"))
    script["total_approx_seconds"] = actual_total
    script["duration_self_check"] = {
        "actual_total_seconds": actual_total,
        "target_seconds": target,
        "min_seconds": min_seconds,
        "max_seconds": max_seconds,
        "coverage_ratio": round(actual_total / target, 4) if target else None,
        "sentence_count": len(sentences),
        "narration_word_count": word_count,
        "minimum_word_count": int(words_target * 0.9) if words_target else None,
        "maximum_word_count": int(words_target * 1.1) if words_target else None,
    }


def _gate_report_after_repair(existing: dict[str, Any] | None, batch: Any) -> dict[str, Any]:
    batch_refs = list(_dict(existing).get("gate_batch_run_refs") or [])
    if getattr(batch, "gate_batch_run_id", None):
        batch_refs.append(str(batch.gate_batch_run_id))
    return {
        "status": batch.status,
        "gate_batch_run_refs": [item for item in batch_refs if item],
        "hard_block_count": int(batch.hard_block_count),
        "review_required_count": int(batch.review_required_count),
        "fail_codes": list(batch.fail_codes),
        "latest_gate_results": [
            {
                "gate_key": result.gate_key,
                "status": result.status,
                "fail_codes": result.fail_codes,
                "summary": result.human_readable_summary,
            }
            for result in batch.gate_results
            if result.status != "PASS"
        ],
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _strings(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item not in (None, "")]


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None
