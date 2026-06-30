import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.contracts.m5 import ProjectAdmissionDecisionCreate
from app.contracts.r3d1 import (
    CharacterBindingCreate,
    CharacterImageBranchCreate,
    CharacterProfileCreate,
    CharacterReferenceAssetPackCreate,
    CharacterVersionCreate,
    ContentCategoryCreate,
    VoiceProfileCreate,
)
from app.core.time import utc_now
from app.db.models import (
    ChannelDailyRun,
    ChannelProfileVersion,
    ChannelWorkspace,
    Company,
    CompiledChannelPolicySnapshot,
    ContextPackSnapshot,
    DailyIdeaDecision,
    EditorialCalendarSlot,
    IdeaMarketPreflight,
    RetrievalPlanSnapshot,
    User,
    VideoProject,
)
from app.services import ConfigRegistryService, ProjectAdmissionService, R3D1AdminService, RBACService
from app.services.config_registry import content_hash


ROOT = Path(__file__).resolve().parents[1]


def _complete_contract(status: str = "COMPLETE") -> dict:
    return {
        "contract_status": status,
        "missing_fields": [] if status == "COMPLETE" else ["market_locale.primary_market"],
        "contradiction_reasons": [],
        "market_locale": {"content_language": "en", "operator_language": "vi"},
        "forbidden_behavior": ["fake_traffic", "bot_engagement", "youtube_studio_scraping"],
    }


def _scope(db_session, *, contract_status: str = "COMPLETE") -> SimpleNamespace:
    ConfigRegistryService(db_session).seed([ROOT / "config"])
    company = Company(name=f"R3D1 {uuid.uuid4().hex[:8]}", slug=f"r3d1-{uuid.uuid4().hex[:8]}")
    operator = User(email=f"r3d1-{uuid.uuid4().hex[:12]}@example.com", display_name="R3D1 Operator")
    db_session.add_all([company, operator])
    db_session.flush()
    RBACService(db_session).assign_role(user_id=operator.id, role_key="operator", company_id=company.id)
    channel = ChannelWorkspace(
        company_id=company.id,
        key=f"r3d1-{uuid.uuid4().hex[:8]}",
        name="R3D1 Channel",
        status="active",
        primary_language="en",
        primary_timezone="UTC",
        default_timezone="UTC",
    )
    db_session.add(channel)
    db_session.flush()
    profile_payload = {"source": "r3d1-test-profile"}
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
    contract = _complete_contract(contract_status)
    payload = {
        "channel_contract_json": contract,
        "contract_status": contract_status,
        "missing_fields": contract["missing_fields"],
        "contradiction_reasons": [],
    }
    snapshot = CompiledChannelPolicySnapshot(
        channel_workspace_id=channel.id,
        channel_profile_version_id=profile.id,
        snapshot_version=1,
        status="active",
        compiler_version="r3d1-test",
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
    return SimpleNamespace(company=company, channel=channel, profile=profile, snapshot=snapshot, operator=operator)


def _category(db_session, scope, *, key: str = "default", mode: str = "NO_CHARACTER", status: str = "ACTIVE"):
    return R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"{key}-{uuid.uuid4().hex[:8]}",
            name=f"{key.title()} Category",
            content_pillar="education",
            character_policy_mode=mode,
            status=status,
            human_approved_at=utc_now() if status == "ACTIVE" else None,
        )
    )


def _daily_flow(db_session, scope, *, category=None) -> SimpleNamespace:
    slot = EditorialCalendarSlot(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        category_id=category.id if category is not None else None,
        slot_date=date(2026, 6, 30),
        slot_type="DAILY",
        status="OPEN",
        production_goal="Explain VCOS runtime scope",
        target_platforms=["YOUTUBE"],
        content_pillar="education",
    )
    db_session.add(slot)
    db_session.flush()
    daily_run = ChannelDailyRun(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        editorial_calendar_slot_id=slot.id,
        run_date=slot.slot_date,
        status="COMPLETED",
        run_mode="REAL_DISABLED",
        trigger_type="MANUAL",
    )
    db_session.add(daily_run)
    db_session.flush()
    plan_payload = {"purpose": "TEST", "daily_run_id": str(daily_run.id)}
    plan = RetrievalPlanSnapshot(
        purpose="TEST",
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        allowed_sources=["manual_input"],
        excluded_sources=[],
        redaction_rules={},
        source_order=[],
        plan_hash=content_hash(plan_payload),
    )
    db_session.add(plan)
    db_session.flush()
    pack_payload = {"plan_id": str(plan.id)}
    pack = ContextPackSnapshot(
        retrieval_plan_snapshot_id=plan.id,
        purpose="TEST",
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        input_refs=[],
        policy_refs=[],
        evidence_refs=[],
        metric_refs=[],
        memory_refs=[],
        pack_content={},
        freshness_state="FRESH",
        confidence_level="HIGH",
        pack_hash=content_hash(pack_payload),
    )
    db_session.add(pack)
    db_session.flush()
    idea = DailyIdeaDecision(
        channel_daily_run_id=daily_run.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        policy_snapshot_id=scope.snapshot.id,
        context_pack_snapshot_id=pack.id,
        decision_status="PROPOSED",
        proposed_title="R3D1 Runtime Scope",
        proposed_angle="Channel contract authority",
        rationale={"source": "test"},
        evidence_refs=[],
        reason_codes=["IDEA_ADMITTED"],
        confidence_level="HIGH",
    )
    db_session.add(idea)
    db_session.flush()
    preflight = IdeaMarketPreflight(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        channel_daily_run_id=daily_run.id,
        daily_idea_decision_id=idea.id,
        policy_fit_state="PASS",
        confidence_state="HIGH",
        evidence_blob={"evidence_refs": []},
        reason_codes=["IDEA_ADMITTED"],
        decision="PASS",
    )
    db_session.add(preflight)
    db_session.flush()
    return SimpleNamespace(slot=slot, daily_run=daily_run, idea=idea, preflight=preflight)


def _admit(db_session, scope, flow, **overrides):
    return ProjectAdmissionService(db_session).create_decision(
        data=ProjectAdmissionDecisionCreate(
            channel_daily_run_id=flow.daily_run.id,
            daily_idea_decision_id=flow.idea.id,
            idea_market_preflight_id=flow.preflight.id,
            created_by_user_id=scope.operator.id,
            **overrides,
        )
    )


def _character_binding(db_session, scope, category, *, status: str = "ACTIVE"):
    admin = R3D1AdminService(db_session)
    profile = admin.create_character_profile(
        CharacterProfileCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            character_key=f"host-{uuid.uuid4().hex[:8]}",
            display_name="VCOS Host",
            status="ACTIVE",
        )
    )
    version = admin.create_character_version(
        CharacterVersionCreate(character_profile_id=profile.id, version=1, status="ACTIVE")
    )
    branch = admin.create_character_image_branch(
        CharacterImageBranchCreate(character_version_id=version.id, branch_key="default", status="ACTIVE")
    )
    pack = admin.create_character_reference_asset_pack(
        CharacterReferenceAssetPackCreate(
            character_image_branch_id=branch.id,
            pack_key="approved",
            rights_status="SAFE",
            prompt_safety_state="PROMPT_SAFE",
            status="ACTIVE",
        )
    )
    voice = admin.create_voice_profile(
        VoiceProfileCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            character_profile_id=profile.id,
            voice_key=f"voice-{uuid.uuid4().hex[:8]}",
            language="en",
            consent_status="VERIFIED",
            commercial_use_status="ALLOWED",
            status="ACTIVE",
        )
    )
    return admin.create_character_binding(
        CharacterBindingCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            content_category_id=category.id,
            character_profile_id=profile.id,
            character_version_id=version.id,
            character_image_branch_id=branch.id,
            reference_asset_pack_id=pack.id,
            voice_profile_id=voice.id,
            binding_scope="CATEGORY",
            status=status,
        )
    )


def test_admission_blocks_when_channel_contract_incomplete(db_session) -> None:
    scope = _scope(db_session, contract_status="PARTIAL")
    category = _category(db_session, scope)
    flow = _daily_flow(db_session, scope, category=category)
    admission = _admit(db_session, scope, flow)
    assert admission.decision == "BLOCK"
    assert "CHANNEL_CONTRACT_NOT_COMPLETE" in admission.reason_codes
    assert admission.admitted_video_project_id is None


def test_admission_blocks_when_category_missing_with_multiple_active_categories(db_session) -> None:
    scope = _scope(db_session)
    _category(db_session, scope, key="one")
    _category(db_session, scope, key="two")
    flow = _daily_flow(db_session, scope)
    admission = _admit(db_session, scope, flow)
    assert admission.decision == "BLOCK"
    assert "CATEGORY_SCOPE_MISSING" in admission.reason_codes


def test_admission_auto_binds_single_active_category(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope)
    flow = _daily_flow(db_session, scope)
    admission = _admit(db_session, scope, flow)
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    assert admission.decision == "ADMIT"
    assert project.category_id == category.id


def test_admission_blocks_category_from_another_channel_or_company(db_session) -> None:
    scope = _scope(db_session)
    other_scope = _scope(db_session)
    other_category = _category(db_session, other_scope)
    flow = _daily_flow(db_session, scope)
    flow.slot.category_id = other_category.id
    db_session.flush()
    admission = _admit(db_session, scope, flow)
    assert admission.decision == "BLOCK"
    assert "CATEGORY_SCOPE_MISSING" in admission.reason_codes


def test_no_character_category_forbids_character_binding(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="NO_CHARACTER")
    binding = _character_binding(db_session, scope, category)
    flow = _daily_flow(db_session, scope, category=category)
    admission = _admit(db_session, scope, flow, character_binding_id=binding.id)
    assert admission.decision == "BLOCK"
    assert "CHARACTER_BINDING_FORBIDDEN" in admission.reason_codes


def test_required_character_blocks_when_binding_missing(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    flow = _daily_flow(db_session, scope, category=category)
    admission = _admit(db_session, scope, flow)
    assert admission.decision == "BLOCK"
    assert "CHARACTER_REQUIRED_BUT_MISSING" in admission.reason_codes


def test_required_character_passes_with_active_binding_and_required_refs(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    binding = _character_binding(db_session, scope, category)
    flow = _daily_flow(db_session, scope, category=category)
    admission = _admit(db_session, scope, flow)
    project = db_session.get(VideoProject, admission.admitted_video_project_id)
    expected_hash = content_hash(scope.snapshot.compiled_payload["channel_contract_json"])
    assert admission.decision == "ADMIT"
    assert project.category_id == category.id
    assert project.character_binding_id == binding.id
    assert project.channel_contract_content_hash == expected_hash


def test_inactive_character_binding_blocks(db_session) -> None:
    scope = _scope(db_session)
    category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    binding = _character_binding(db_session, scope, category, status="ARCHIVED")
    flow = _daily_flow(db_session, scope, category=category)
    admission = _admit(db_session, scope, flow, character_binding_id=binding.id)
    assert admission.decision == "BLOCK"
    assert "CHARACTER_BINDING_NOT_ACTIVE" in admission.reason_codes


def test_r3d1_does_not_add_provider_media_or_upload_calls() -> None:
    source = (ROOT / "app/services/r3d1.py").read_text()
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


def test_r3d1_does_not_add_vector_rag_or_memory_retrieval() -> None:
    source = (ROOT / "app/services/r3d1.py").read_text().lower()
    forbidden = ["resourceresolverservice", "contextpacksnapshot", "vector", "embedding", "rag"]
    assert [token for token in forbidden if token in source] == []
