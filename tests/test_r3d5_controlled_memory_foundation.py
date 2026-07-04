from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.contracts.r3d1 import (
    CharacterBindingCreate,
    CharacterImageBranchCreate,
    CharacterProfileCreate,
    CharacterReferenceAssetPackCreate,
    CharacterVersionCreate,
    ContentCategoryCreate,
    VoiceProfileCreate,
)
from app.contracts.r3d5 import (
    ChannelMemoryDraftCreate,
    MemoryApprovalRequest,
    MemoryFacetInput,
    MemoryFromApprovedPlaybookCreate,
    MemoryUsageManifestCreate,
)
from app.contracts.workflow import VideoProjectCreate
from app.core.errors import ValidationFailureError
from app.core.time import utc_now
from app.db.models import (
    ApprovedPlaybookEntry,
    ChannelMemoryItem,
    Company,
    LearningCandidate,
    MemoryFacet,
    MemoryReviewQueueItem,
    MemoryUsageManifest,
    User,
)
from app.main import create_app
from app.services import R3D1AdminService, VideoProjectService
from app.services.r3d2 import EffectiveChannelRuntimeContextCompiler
from app.services.r3d5 import (
    ControlledMemoryService,
    MemoryApprovalGate,
    MemoryDuplicationGate,
    MemoryFreshnessGate,
    MemoryPromptBudgetGate,
    MemoryPromptSafetyGate,
    MemoryRightsGate,
    MemoryScopeGate,
    stable_hash,
)
from tests.qualification.conftest import QualificationFactory


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _category(db_session, scope, *, mode: str = "NO_CHARACTER"):
    return R3D1AdminService(db_session).create_content_category(
        ContentCategoryCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            category_key=f"r3d5-{uuid.uuid4().hex[:8]}",
            name="R3D5 Category",
            content_pillar="education",
            default_format_policy_json={"format": "explainer"},
            character_policy_mode=mode,
            status="ACTIVE",
            human_approved_at=utc_now(),
        )
    )


def _character_binding(db_session, scope, category) -> SimpleNamespace:
    admin = R3D1AdminService(db_session)
    profile = admin.create_character_profile(
        CharacterProfileCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            character_key=f"host-{uuid.uuid4().hex[:8]}",
            display_name="R3D5 Host",
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
    binding = admin.create_character_binding(
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
            status="ACTIVE",
        )
    )
    return SimpleNamespace(profile=profile, version=version, branch=branch, pack=pack, voice=voice, binding=binding)


def _effective_context(db_session, scope, *, category=None, binding=None):
    project = VideoProjectService(db_session).create_project(
        data=VideoProjectCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            policy_snapshot_id=scope.snapshot.id,
            category_id=category.id if category is not None else None,
            character_binding_id=binding.id if binding is not None else None,
            title=f"R3D5 project {uuid.uuid4().hex[:6]}",
            description="Controlled memory fixture.",
            created_by_user_id=scope.operator.id,
        )
    )
    effective = EffectiveChannelRuntimeContextCompiler(db_session).ensure_for_project(project.id)
    assert effective.compile_status == "PASS"
    return effective


def _candidate(db_session, scope, *, state: str = "READY_FOR_HUMAN_REVIEW", candidate_type: str = "PACKAGING_PATTERN"):
    candidate = LearningCandidate(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        candidate_type=candidate_type,
        candidate_state=state,
        operator_summary="Learning candidate for memory.",
        friendly_status="Ready",
        candidate_summary="Concrete framing beat abstract framing.",
        suggested_learning="Use concrete outcome framing; avoid vague abstract titles.",
        suggested_playbook_text="Prefer concrete outcome framing over abstract titles.",
        recommended_scope="CHANNEL",
        confidence_label="HIGH",
        risk_level="LOW",
    )
    db_session.add(candidate)
    db_session.flush()
    return candidate


def _approved_playbook(db_session, scope, *, text: str = "Prefer concrete outcome framing. Avoid abstract title promises."):
    candidate = _candidate(db_session, scope)
    entry = ApprovedPlaybookEntry(
        learning_candidate_id=candidate.id,
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        scope="CHANNEL",
        category="PACKAGING",
        playbook_text=text,
        evidence_refs=[{"type": "manual_review", "id": "r3d5"}],
        state="APPROVED",
    )
    db_session.add(entry)
    db_session.flush()
    return entry


def _memory(
    db_session,
    scope,
    *,
    category=None,
    approval_status: str = "DRAFT",
    rights_status: str = "SAFE",
    prompt_safety_state: str = "PROMPT_SAFE",
    freshness_state: str = "FRESH",
    reuse_scope: str = "CHANNEL",
    facet_text: str = "Prefer concrete hooks with evidence.",
    polarity: str = "POSITIVE",
    character=None,
):
    item = ControlledMemoryService(db_session).create_draft(
        data=ChannelMemoryDraftCreate(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            content_category_id=category.id if category is not None else None,
            character_profile_id=character.profile.id if character is not None else None,
            character_version_id=character.version.id if character is not None else None,
            character_binding_id=character.binding.id if character is not None else None,
            memory_type="PACKAGING_PATTERN" if polarity != "NEGATIVE" else "AVOID_REPEAT",
            source_type="HUMAN_REFERENCE",
            source_ref=f"r3d5:{uuid.uuid4()}",
            source_content={"text": facet_text, "nonce": uuid.uuid4().hex},
            summary=facet_text,
            rights_status=rights_status,
            prompt_safety_state=prompt_safety_state,
            freshness_state=freshness_state,
            reuse_scope=reuse_scope,
            facets=[
                MemoryFacetInput(
                    facet_type="PACKAGING_PATTERN",
                    facet_text=facet_text,
                    polarity=polarity,
                    confidence_label="HIGH",
                    prompt_safety_state=prompt_safety_state,
                )
            ],
        )
    )
    item.approval_status = approval_status
    db_session.flush()
    return item, ControlledMemoryService(db_session).list_facets(memory_item_id=item.id)[0]


def test_create_memory_item_from_approved_playbook_entry_and_api(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Playbook")
    entry = _approved_playbook(db_session, scope)

    item = ControlledMemoryService(db_session).create_from_approved_playbook_entry(
        playbook_entry_id=entry.id,
        data=MemoryFromApprovedPlaybookCreate(allowed_use_cases_json=["script"], embedding_eligible=True),
    )

    facets = ControlledMemoryService(db_session).list_facets(memory_item_id=item.id)
    queue = db_session.query(MemoryReviewQueueItem).filter_by(memory_item_id=item.id).one()
    assert item.approval_status == "REVIEW_REQUIRED"
    assert item.source_type == "APPROVED_PLAYBOOK_ENTRY"
    assert facets and facets[0].embedding_eligible is True
    assert queue.queue_status == "PENDING"

    db_session.commit()
    client = TestClient(create_app())
    response = client.get(f"/memory/items/{item.id}")
    assert response.status_code == 200
    assert response.json()["id"] == str(item.id)
    response = client.get("/memory/review-queue")
    assert response.status_code == 200
    assert response.json()[0]["memory_item_id"] == str(item.id)


def test_block_memory_creation_from_unapproved_learning_candidate(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Unapproved")
    candidate = _candidate(db_session, scope, state="READY_FOR_HUMAN_REVIEW")

    with pytest.raises(ValidationFailureError):
        ControlledMemoryService(db_session).create_draft_from_learning_candidate(candidate_id=candidate.id)


def test_rejected_learning_cannot_become_approved_memory(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Rejected")
    candidate = _candidate(db_session, scope, state="CANCELLED")

    with pytest.raises(ValidationFailureError):
        ControlledMemoryService(db_session).create_draft(
            data=ChannelMemoryDraftCreate(
                company_id=scope.company.id,
                channel_workspace_id=scope.channel.id,
                memory_type="APPROVED_PLAYBOOK",
                source_type="LEARNING_CANDIDATE",
                source_ref=str(candidate.id),
                source_content={"candidate_id": str(candidate.id)},
                summary="Rejected learning should not approve.",
                rights_status="SAFE",
                prompt_safety_state="PROMPT_SAFE",
                created_from_learning_candidate_id=candidate.id,
                facets=[
                    MemoryFacetInput(
                        facet_type="APPROVED_PLAYBOOK",
                        facet_text="Rejected candidate rule.",
                        prompt_safety_state="PROMPT_SAFE",
                    )
                ],
            )
        )


def test_failed_output_becomes_negative_avoid_pattern_only_after_human_approval(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Failed Output")

    with pytest.raises(ValidationFailureError):
        ControlledMemoryService(db_session).create_failed_output_avoid_memory(
            company_id=scope.company.id,
            channel_workspace_id=scope.channel.id,
            source_ref="failed-output:1",
            failed_output_summary="Bad hook overpromised.",
            avoid_rule="Avoid promising exact revenue without evidence.",
            approved_by=None,
        )

    item = ControlledMemoryService(db_session).create_failed_output_avoid_memory(
        company_id=scope.company.id,
        channel_workspace_id=scope.channel.id,
        source_ref="failed-output:2",
        failed_output_summary="Bad hook overpromised.",
        avoid_rule="Avoid promising exact revenue without evidence.",
        approved_by=scope.operator.id,
    )
    facet = ControlledMemoryService(db_session).list_facets(memory_item_id=item.id)[0]
    assert item.approval_status == "APPROVED"
    assert item.memory_type == "AVOID_REPEAT"
    assert facet.polarity == "NEGATIVE"


def test_memory_approval_rights_prompt_safety_and_freshness_gates(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Gates")
    draft, facet = _memory(db_session, scope, approval_status="DRAFT")
    assert MemoryApprovalGate().check(draft).passed is False

    for rights in ["RESTRICTED", "EXPIRED", "BLOCKED"]:
        item, _ = _memory(db_session, scope, approval_status="APPROVED", rights_status=rights, facet_text=f"rights {rights}")
        assert MemoryRightsGate().check(item).reason_codes == ["MEMORY_RIGHTS_NOT_SAFE"]

    unsafe, unsafe_facet = _memory(
        db_session,
        scope,
        approval_status="APPROVED",
        prompt_safety_state="NOT_PROMPT_SAFE",
        facet_text="unsafe prompt",
    )
    assert MemoryPromptSafetyGate().check(unsafe, unsafe_facet).passed is False

    expired, _ = _memory(
        db_session,
        scope,
        approval_status="APPROVED",
        freshness_state="EXPIRED",
        facet_text="expired lesson",
    )
    assert MemoryFreshnessGate().check(expired).reason_codes == ["MEMORY_NOT_FRESH"]
    assert facet.facet_text_hash


def test_memory_scope_gate_blocks_cross_company_and_cross_channel(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Scope A")
    other = qualification_factory.channel_scope(name="R3D5 Scope B")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)

    cross_company, _ = _memory(db_session, other, approval_status="APPROVED", facet_text="other company")
    cross_channel_company = ChannelMemoryItem(
        company_id=scope.company.id,
        channel_workspace_id=other.channel.id,
        memory_type="PACKAGING_PATTERN",
        source_type="HUMAN_REFERENCE",
        source_ref="manual:cross-channel",
        source_content_hash=stable_hash({"source": "cross-channel"}),
        summary="Cross-channel lesson.",
        approval_status="APPROVED",
        rights_status="SAFE",
        prompt_safety_state="PROMPT_SAFE",
        reuse_scope="CHANNEL",
        freshness_state="FRESH",
        content_hash=stable_hash({"item": "cross-channel"}),
    )
    db_session.add(cross_channel_company)
    db_session.flush()

    assert "MEMORY_SCOPE_COMPANY_MISMATCH" in MemoryScopeGate().check(item=cross_company, effective_context=effective).reason_codes
    assert "CROSS_CHANNEL_MEMORY_BLOCKED" in MemoryScopeGate().check(item=cross_channel_company, effective_context=effective).reason_codes


def test_memory_scope_gate_allows_same_channel_category_and_requires_company_approved_flag(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Scope Category")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    item, _ = _memory(db_session, scope, category=category, approval_status="APPROVED", facet_text="same category")
    company_item, _ = _memory(
        db_session,
        scope,
        approval_status="APPROVED",
        reuse_scope="COMPANY_APPROVED",
        facet_text="company approved",
    )

    assert MemoryScopeGate().check(item=item, effective_context=effective).passed is True
    assert "COMPANY_APPROVED_MEMORY_REQUIRES_EXPLICIT_ALLOW" in MemoryScopeGate().check(
        item=company_item,
        effective_context=effective,
    ).reason_codes
    assert MemoryScopeGate().check(item=company_item, effective_context=effective, allow_company_approved=True).passed is True


def test_character_scope_blocks_no_character_and_requires_matching_refs(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Character")
    no_char_category = _category(db_session, scope, mode="NO_CHARACTER")
    char_category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    char = _character_binding(db_session, scope, char_category)
    no_char_context = _effective_context(db_session, scope, category=no_char_category)
    char_context = _effective_context(db_session, scope, category=char_category, binding=char.binding)
    character_item, _ = _memory(
        db_session,
        scope,
        category=char_category,
        approval_status="APPROVED",
        reuse_scope="CHARACTER",
        character=char,
        facet_text="Host thumbnail should keep the same calm expression.",
    )

    assert "NO_CHARACTER_CONTEXT_BLOCKS_CHARACTER_MEMORY" in MemoryScopeGate().check(
        item=character_item,
        effective_context=no_char_context,
    ).reason_codes
    assert MemoryScopeGate().check(item=character_item, effective_context=char_context).passed is True

    other_char = _character_binding(db_session, scope, char_category)
    mismatch, _ = _memory(
        db_session,
        scope,
        category=char_category,
        approval_status="APPROVED",
        reuse_scope="CHARACTER",
        character=other_char,
        facet_text="Different host constraint.",
    )
    assert "CHARACTER_PROFILE_SCOPE_MISMATCH" in MemoryScopeGate().check(item=mismatch, effective_context=char_context).reason_codes


def test_prompt_budget_and_duplication_gates_block_raw_blobs_and_duplicates(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Budget Duplicate")
    oversized = MemoryFacetInput(
        facet_type="SCRIPT_STYLE",
        facet_text="x" * 900,
        prompt_safety_state="PROMPT_SAFE",
    )
    raw = MemoryFacetInput(
        facet_type="SCRIPT_STYLE",
        facet_text='{"full_script": ["line 1", "line 2"]}',
        prompt_safety_state="PROMPT_SAFE",
    )
    assert "MEMORY_FACET_TOO_LARGE" in MemoryPromptBudgetGate(max_facet_chars=120).check(oversized).reason_codes
    assert "RAW_ARTIFACT_BLOB_BLOCKED" in MemoryPromptBudgetGate().check(raw).reason_codes

    item, facet = _memory(db_session, scope, facet_text="Duplicate lesson text.")
    duplicate = MemoryDuplicationGate(db_session).check(
        source_content_hash=item.source_content_hash,
        facet_text_hash=facet.facet_text_hash,
    )
    assert "DUPLICATE_SOURCE_CONTENT_HASH" in duplicate.reason_codes
    assert "DUPLICATE_FACET_TEXT_HASH" in duplicate.reason_codes


def test_memory_usage_manifest_records_planned_and_blocked_without_prompt_injection(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D5 Manifest")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    item, facet = _memory(db_session, scope, category=category, approval_status="APPROVED")

    planned = ControlledMemoryService(db_session).create_usage_manifest(
        data=MemoryUsageManifestCreate(
            effective_context_snapshot_id=effective.id,
            memory_item_ids_json=[str(item.id)],
            memory_facet_ids_json=[str(facet.id)],
            use_case="script",
            usage_status="PLANNED",
            digest_hash=stable_hash({"digest": "planned"}),
        )
    )
    blocked = ControlledMemoryService(db_session).create_usage_manifest(
        data=MemoryUsageManifestCreate(
            effective_context_snapshot_id=effective.id,
            memory_item_ids_json=[str(item.id)],
            memory_facet_ids_json=[],
            use_case="script",
            usage_status="BLOCKED",
        )
    )

    assert db_session.query(MemoryUsageManifest).count() == 2
    assert planned.usage_status == "PLANNED"
    assert blocked.usage_status == "BLOCKED"
    assert "memory_digest" not in Path("app/prompts/agents/user_templates/base_task_payload.md").read_text(encoding="utf-8")


def test_r3d5_source_guards_no_vector_provider_upload_or_prompt_injection() -> None:
    service_source = Path("app/services/r3d5.py").read_text(encoding="utf-8")
    model_source = Path("app/db/models/r3d5.py").read_text(encoding="utf-8")
    prompt_sources = "\n".join(
        [
            Path("app/services/r3d3.py").read_text(encoding="utf-8"),
            Path("app/services/m12_2.py").read_text(encoding="utf-8"),
            Path("app/prompts/agents/user_templates/base_task_payload.md").read_text(encoding="utf-8"),
        ]
    )
    forbidden_runtime_tokens = [
        "requests.",
        "httpx",
        "GoogleVertexVeoProvider",
        "CreatomateRender",
        "YouTubeUpload",
        "GoogleDriveUploadService",
    ]
    forbidden_vector_tokens = ["EmbeddingFacet", "VectorRetrieval", "pgvector", "embedding_vector"]
    assert [token for token in forbidden_runtime_tokens if token in service_source] == []
    assert [token for token in forbidden_vector_tokens if token in service_source + model_source] == []
    assert "channel_memory_items" not in prompt_sources
    assert "MemoryFacet" not in prompt_sources
