import json
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.contracts.m10_1 import LLMRouteResponse
from app.contracts.m12_2 import FirstScriptedVideoPackageRequest
from app.contracts.r3d1 import (
    CharacterBindingCreate,
    CharacterImageBranchCreate,
    CharacterProfileCreate,
    CharacterReferenceAssetPackCreate,
    CharacterVersionCreate,
    ContentCategoryCreate,
    VoiceProfileCreate,
)
from app.contracts.workflow import VideoProjectCreate
from app.core.config import Settings
from app.core.time import utc_now
from app.db.models import (
    ChannelProfileVersion,
    ChannelWorkspace,
    Company,
    CompiledChannelPolicySnapshot,
    EffectiveChannelRuntimeContextSnapshot,
    User,
)
from app.services import ConfigRegistryService, RBACService, R3D1AdminService, VideoProjectService
from app.services.config_registry import content_hash
from app.services.m12_2 import FirstScriptedVideoPackageService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler


ROOT = Path(__file__).resolve().parents[1]


class CompletedTags:
    stdout = "m12-1-prompt-registry-contracts\nm12-1r-mock-dryrun-purge\n"


class FakeRouter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def route(self, **kwargs) -> LLMRouteResponse:
        self.calls.append(kwargs)
        return LLMRouteResponse(
            status="SUCCESS",
            lane_name=kwargs["lane_name"],
            selected_model="test-router-model",
            fallback_level="PRIMARY",
            content=json.dumps({"status": "OK", "artifact": {}}),
            structured_output={"status": "OK", "artifact": {}},
            route_attempt_id=uuid.uuid4(),
            provider_attempt_id=None,
            llm_run_snapshot_id=uuid.uuid4(),
            reason_codes=["TEST_LLM_ROUTE"],
        )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        production_prompt_activation_enabled=True,
        real_llm_package_run_enabled=True,
        media_provider_calls_disabled=True,
        upload_and_publish_disabled=True,
        old_provider_smoke_disabled=True,
        llm_provider="ollama",
        llm_real_execution_enabled=True,
        llm_router_real_smoke=False,
    )


def _contract(*, status: str = "COMPLETE", language: str = "en", market: str = "US") -> dict:
    return {
        "contract_status": status,
        "missing_fields": [] if status == "COMPLETE" else ["market_locale.primary_market"],
        "contradiction_reasons": [],
        "channel_identity": {"channel_name": "R3D2 Channel", "niche": "operator education"},
        "target_audience": {
            "primary_persona": "Solo creator operator",
            "audience_level": "intermediate",
            "pain_points": ["workflow drift"],
        },
        "market_locale": {
            "primary_market": market,
            "audience_locale": "en-US",
            "content_language": language,
            "operator_language": "vi",
            "timezone": "UTC",
            "currency": "USD",
            "cultural_style": {"note": "plain and practical"},
            "market_locale_context_status": "KNOWN",
        },
        "editorial_strategy": {
            "content_pillars": ["education"],
            "forbidden_angles": ["fake scarcity"],
            "forbidden_topics": ["medical guarantees"],
            "claim_style": ["evidence_backed"],
        },
        "format_policy": {"default_format": "explainer"},
        "voice_style": {
            "narration_tone": "calm",
            "pacing": "steady",
            "allowed_style": ["plainspoken"],
            "forbidden_style": ["hype"],
        },
        "platform_strategy": {
            "primary_platform": "YouTube",
            "publish_mode": "human_handoff_only",
            "auto_publish_allowed": False,
            "studio_scraping_allowed": False,
            "configured_publish_window": "09:00-12:00",
        },
        "media_policy": {
            "allowed_visual_sources": ["DIAGRAM", "CARD", "SCREENSHOT"],
            "forbidden_visual_bait": ["misleading before-after"],
            "voice_provider": None,
        },
        "rights_policy": {
            "source_manifest_required": True,
            "rights_evidence_required": True,
            "ai_disclosure_required_when_ai_media_used": True,
            "required_disclosure_blocks": ["ai_assisted_script"],
        },
        "budget_policy": {"paid_provider_allowed": True, "budget_tier": "low"},
        "learning_policy": {"min_evidence_required": {"claim": "source_needed"}, "auto_promote_learning": False},
        "forbidden_behavior": ["fake_traffic", "bot_engagement", "youtube_studio_scraping"],
    }


def _scope(db_session, *, contract_status: str = "COMPLETE", language: str = "en", market: str = "US") -> SimpleNamespace:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    company = Company(name=f"R3D2 {uuid.uuid4().hex[:8]}", slug=f"r3d2-{uuid.uuid4().hex[:8]}")
    operator = User(email=f"r3d2-{uuid.uuid4().hex[:12]}@example.com", display_name="R3D2 Operator")
    db_session.add_all([company, operator])
    db_session.flush()
    RBACService(db_session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspace(
        company_id=company.id,
        key=f"r3d2-{uuid.uuid4().hex[:8]}",
        name="R3D2 Channel",
        status="active",
        primary_language=language,
        primary_timezone="UTC",
        default_timezone="UTC",
        target_market=market,
        target_regions=[market],
        target_metadata_languages=[language],
        target_subtitle_languages=[language],
    )
    db_session.add(channel)
    db_session.flush()
    profile_payload = {"source": "r3d2-test-profile", "version": 1}
    profile_hash = content_hash(profile_payload)
    profile = ChannelProfileVersion(
        channel_workspace_id=channel.id,
        version=1,
        status="active",
        profile_input=profile_payload,
        profile_input_hash=profile_hash,
    )
    db_session.add(profile)
    db_session.flush()
    contract = _contract(status=contract_status, language=language, market=market)
    compiled_policy = {
        "schema_version": "r3d2-test",
        "market_locale": contract["market_locale"],
        "monetization_policy": {"mode": "education", "allowed_cta_types": ["subscribe"]},
    }
    payload = {
        "channel_contract_json": contract,
        "compiled_policy_snapshot_json": compiled_policy,
        "field_source_map_json": {"market_locale.primary_market": {"source_type": "HUMAN_CONFIRMED"}},
        "contract_status": contract_status,
        "missing_fields": contract["missing_fields"],
        "contradiction_reasons": [],
    }
    snapshot = CompiledChannelPolicySnapshot(
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile.id,
        snapshot_version=1,
        status="active",
        compiler_version="r3d2-test",
        capability_matrix_version="test",
        compiled_payload=payload,
        content_hash=content_hash(payload),
        profile_input_hash=profile_hash,
        activated_at=utc_now(),
    )
    db_session.add(snapshot)
    db_session.flush()
    channel.active_policy_snapshot_id = snapshot.id
    db_session.flush()
    return SimpleNamespace(company=company, operator=operator, channel=channel, profile=profile, snapshot=snapshot)


def _category(db_session, scope, *, mode: str = "NO_CHARACTER", visual_style: dict | None = None, status: str = "ACTIVE"):
    return R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"cat-{uuid.uuid4().hex[:8]}",
            name="R3D2 Category",
            sub_niche="workflow systems",
            audience_segment="creator operators",
            content_pillar="education",
            default_format_policy_json={"format": "explainer"},
            default_visual_style_json=visual_style or {"style_note": "clean diagrams"},
            default_voice_style_json={"tone": "calm"},
            default_thumbnail_style_json={"style": "clear text"},
            visual_mode="DIAGRAM_FIRST",
            character_policy_mode=mode,
            status=status,
            human_approved_at=utc_now() if status == "ACTIVE" else None,
        )
    )


def _character_binding(db_session, scope, category, *, pack_status: str = "ACTIVE", voice: bool = True):
    admin = R3D1AdminService(db_session)
    profile = admin.create_character_profile(
        CharacterProfileCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            character_key=f"host-{uuid.uuid4().hex[:8]}",
            display_name="VCOS Host",
            persona_json={"persona": "calm operator"},
            status="ACTIVE",
        )
    )
    version = admin.create_character_version(
        CharacterVersionCreate(
            character_profile_id=profile.id,
            version=1,
            identity_json={"name": "VCOS Host"},
            visual_identity_json={"look": "neutral studio"},
            voice_identity_json={"tone": "calm"},
            continuity_rules_json={"no_prompt_self_mutation": True},
            status="ACTIVE",
        )
    )
    branch = admin.create_character_image_branch(
        CharacterImageBranchCreate(
            character_version_id=version.id,
            branch_key="default",
            visual_branch_json={"wardrobe": "plain"},
            provider_constraints_json={"no_unapproved_refs": True},
            status="ACTIVE",
        )
    )
    pack = admin.create_character_reference_asset_pack(
        CharacterReferenceAssetPackCreate(
            character_image_branch_id=branch.id,
            pack_key="approved",
            pack_manifest_json={"refs": ["face", "pose"]},
            rights_status="SAFE",
            prompt_safety_state="PROMPT_SAFE",
            status=pack_status,
        )
    )
    voice_profile = None
    if voice:
        voice_profile = admin.create_voice_profile(
            VoiceProfileCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                character_profile_id=profile.id,
                voice_key=f"voice-{uuid.uuid4().hex[:8]}",
                language="en",
                accent="US",
                tone_json={"tone": "calm"},
                pace_json={"pace": "steady"},
                consent_status="VERIFIED",
                commercial_use_status="ALLOWED",
                status="ACTIVE",
            )
        )
    binding = admin.create_character_binding(
        CharacterBindingCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            content_category_id=category.id,
            character_profile_id=profile.id,
            character_version_id=version.id,
            character_image_branch_id=branch.id,
            reference_asset_pack_id=pack.id,
            voice_profile_id=voice_profile.id if voice_profile else None,
            binding_scope="CATEGORY",
            status="ACTIVE",
        )
    )
    return SimpleNamespace(profile=profile, version=version, branch=branch, pack=pack, voice=voice_profile, binding=binding)


def _project(db_session, scope, *, category=None, binding=None):
    return VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id if category else None,
            character_binding_id=binding.id if binding else None,
            channel_contract_content_hash=content_hash(scope.snapshot.compiled_payload["channel_contract_json"]),
            title="R3D2 Project",
            description="Effective runtime context test",
            created_by_user_id=scope.operator.id,
        )
    )


def _compile(db_session, project, **kwargs):
    return EffectiveChannelRuntimeContextCompiler(db_session).compile_for_project(project=project, **kwargs)


def test_compile_pass_for_complete_contract_and_no_character_category(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="NO_CHARACTER")
    project = _project(db_session, scope, category=category)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "PASS"
    assert snapshot.content_category_id == category.id
    assert snapshot.character_binding_id is None
    assert project.effective_context_snapshot_id == snapshot.id


def test_compile_pass_for_required_character_with_active_refs_and_voice(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    refs = _character_binding(db_session, scope, category)
    project = _project(db_session, scope, category=category, binding=refs.binding)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "PASS"
    assert snapshot.character_binding_id == refs.binding.id
    assert snapshot.reference_asset_pack_id == refs.pack.id
    assert snapshot.voice_profile_id == refs.voice.id


def test_compile_blocks_when_channel_contract_incomplete(db_session) -> None:
    scope = _scope(db_session, contract_status="PARTIAL")
    category = _category(db_session, scope)
    project = _project(db_session, scope, category=category)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "BLOCK"
    assert "CHANNEL_CONTRACT_NOT_COMPLETE" in snapshot.reason_codes_json


def test_compile_blocks_when_compiled_policy_snapshot_missing(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope)
    project = _project(db_session, scope, category=category)

    snapshot = _compile(db_session, project, policy_snapshot_override=None)

    assert snapshot.compile_status == "BLOCK"
    assert "POLICY_SNAPSHOT_MISSING" in snapshot.reason_codes_json
    assert snapshot.compiled_policy_snapshot_id is None


def test_compile_blocks_when_category_missing(db_session) -> None:
    scope = _scope(db_session)
    project = _project(db_session, scope)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "BLOCK"
    assert "CATEGORY_SCOPE_MISSING" in snapshot.reason_codes_json


def test_compile_blocks_when_required_character_binding_missing(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    project = _project(db_session, scope, category=category)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "BLOCK"
    assert "CHARACTER_REQUIRED_BUT_MISSING" in snapshot.reason_codes_json


def test_compile_blocks_when_no_character_project_forces_binding(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="NO_CHARACTER")
    refs = _character_binding(db_session, scope, category)
    project = _project(db_session, scope, category=category, binding=refs.binding)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "BLOCK"
    assert "CHARACTER_BINDING_FORBIDDEN" in snapshot.reason_codes_json


def test_compile_blocks_when_character_asset_pack_archived(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    refs = _character_binding(db_session, scope, category, pack_status="ARCHIVED")
    project = _project(db_session, scope, category=category, binding=refs.binding)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "BLOCK"
    assert "CHARACTER_ASSET_PACK_MISSING" in snapshot.reason_codes_json


def test_compile_review_required_for_optional_missing_style_note(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, visual_style={"requires_style_note": True})
    project = _project(db_session, scope, category=category)

    snapshot = _compile(db_session, project)

    assert snapshot.compile_status == "REVIEW_REQUIRED"
    assert snapshot.reason_codes_json == ["OPTIONAL_STYLE_NOTE_MISSING"]


def test_context_hash_stable_for_same_normalized_inputs(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope)
    project = _project(db_session, scope, category=category)

    first = _compile(db_session, project)
    project.effective_context_snapshot_id = None
    db_session.flush()
    second = _compile(db_session, project)

    assert first.context_hash == second.context_hash


def test_context_hash_changes_when_category_or_voice_changes(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    refs = _character_binding(db_session, scope, category)
    project = _project(db_session, scope, category=category, binding=refs.binding)

    first = _compile(db_session, project)
    category.default_visual_style_json = {"style_note": "new visual rule"}
    category.content_hash = content_hash({"changed": "category"})
    refs.voice.tone_json = {"tone": "warmer"}
    refs.voice.content_hash = content_hash({"changed": "voice"})
    project.effective_context_snapshot_id = None
    db_session.flush()
    second = _compile(db_session, project)

    assert first.context_hash != second.context_hash


def test_old_project_snapshot_does_not_mutate_when_new_contract_is_activated(db_session) -> None:
    scope = _scope(db_session, market="US")
    category = _category(db_session, scope)
    project = _project(db_session, scope, category=category)
    first = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)

    new_profile_payload = {"source": "r3d2-test-profile", "version": 2}
    new_profile_hash = content_hash(new_profile_payload)
    new_profile = ChannelProfileVersion(
        channel_workspace_id=scope.channel.id,
        version=2,
        status="active",
        profile_input=new_profile_payload,
        profile_input_hash=new_profile_hash,
    )
    db_session.add(new_profile)
    db_session.flush()
    new_contract = _contract(market="CA")
    new_payload = {
        "channel_contract_json": new_contract,
        "compiled_policy_snapshot_json": {"schema_version": "r3d2-test", "market_locale": new_contract["market_locale"]},
        "field_source_map_json": {},
        "contract_status": "COMPLETE",
        "missing_fields": [],
        "contradiction_reasons": [],
    }
    new_snapshot = CompiledChannelPolicySnapshot(
        channel_workspace_id=scope.channel.id,
        channel_profile_version_id=new_profile.id,
        snapshot_version=2,
        status="active",
        compiler_version="r3d2-test",
        capability_matrix_version="test",
        compiled_payload=new_payload,
        content_hash=content_hash(new_payload),
        profile_input_hash=new_profile_hash,
        activated_at=utc_now(),
    )
    db_session.add(new_snapshot)
    db_session.flush()
    scope.channel.active_policy_snapshot_id = new_snapshot.id
    db_session.flush()

    again = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)

    assert again.id == first.id
    assert again.context_hash == first.context_hash
    assert again.market_locale_context_json["primary_market"] == "US"


def test_package_generation_blocks_before_llm_when_effective_context_blocks(db_session, monkeypatch) -> None:
    monkeypatch.setattr("app.services.m12_2.subprocess.run", lambda *args, **kwargs: CompletedTags())
    scope = _scope(db_session)
    project = _project(db_session, scope)
    router = FakeRouter()

    package = FirstScriptedVideoPackageService(db_session, settings=_settings(), llm_router=router).create(
        FirstScriptedVideoPackageRequest(
            channel_id=scope.channel.id,
            video_project_id=project.id,
            topic="R3D2 blocks before LLM",
            research_pack_text="Operator source notes.",
            research_pack_ref="operator_pack:r3d2",
        )
    )

    assert package.package_status == "BLOCKED"
    assert package.artifacts["effective_context"]["status"] == "NEEDS_EFFECTIVE_CONTEXT"
    assert "CATEGORY_SCOPE_MISSING" in package.artifacts["effective_context"]["reason_codes"]
    assert package.prompt_render_run_refs == []
    assert router.calls == []


def test_r3d2_does_not_add_provider_media_or_upload_calls() -> None:
    source = __import__("pathlib").Path("app/services/r3d2.py").read_text(encoding="utf-8")
    forbidden = [
        "GoogleDriveUploadService",
        "MediaOffloadJobService",
        "LLMRouterService",
        "ProviderReadinessService",
        "RealSmokeOrchestratorService",
        "YouTube",
        ".upload(",
        ".publish(",
    ]
    assert [token for token in forbidden if token in source] == []


def test_r3d2_does_not_add_vector_rag_or_memory_retrieval() -> None:
    source = __import__("pathlib").Path("app/services/r3d2.py").read_text(encoding="utf-8").lower()
    forbidden = ["resourceresolverservice", "contextpacksnapshot", "vector", "embedding", "rag"]
    assert [token for token in forbidden if token in source] == []
