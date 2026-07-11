from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.contracts.r3d5 import ChannelMemoryDraftCreate, MemoryFacetInput
from app.contracts.r3d6 import RetrievalPolicy, RetrievalRequest
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import ChannelMemoryItem, MemoryFacet, VectorRetrievalManifest
from app.services.r3d3 import AgentContextPackBuilder
from app.services.r3d6 import (
    EmbeddingJobService,
    VectorSafeRetrievalService,
    stable_hash,
)
from tests.qualification.conftest import QualificationFactory
from tests.test_r3d5_controlled_memory_foundation import (
    _category,
    _character_binding,
    _effective_context,
    _memory,
)


@pytest.fixture
def qualification_factory(db_session) -> QualificationFactory:
    return QualificationFactory(db_session)


def _eligible_memory(db_session, scope, *, effective=None, category=None, text: str, vector: list[float], facet_type: str = "PACKAGING_PATTERN"):
    item, facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="APPROVED",
        facet_text=text,
    )
    item.memory_type = facet_type
    facet.facet_type = facet_type
    facet.embedding_eligible = True
    facet.allowed_use_cases_json = []
    db_session.flush()
    embedding = EmbeddingJobService(db_session).store_embedding(
        memory_facet_id=facet.id,
        embedding_vector=vector,
        effective_context_snapshot_id=effective.id if effective is not None else None,
    )
    assert embedding.stale_state == "FRESH"
    return item, facet, embedding


def _retrieval_request(effective, *, agent_key: str = "ScriptWriterAgent", query_vector: list[float] | None = None, allow_company: bool = False):
    return RetrievalRequest(
        effective_context_snapshot_id=effective.id,
        agent_key=agent_key,
        use_case="script",
        query_facet_type="PACKAGING_PATTERN",
        query_text="concrete title framing",
        query_vector=query_vector,
        policy=RetrievalPolicy(
            allow_company_approved=allow_company,
            max_selected_facets=5,
            max_digest_chars=1600,
            requested_facet_types=["PACKAGING_PATTERN"],
            vector_enabled=query_vector is not None,
        ),
    )


def test_embedding_jobs_block_ineligible_memory_states(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Embedding Block")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)

    unapproved, unapproved_facet = _memory(db_session, scope, category=category, approval_status="DRAFT", facet_text="unapproved")
    unapproved_facet.embedding_eligible = True
    unsafe_rights, unsafe_rights_facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="APPROVED",
        rights_status="RESTRICTED",
        facet_text="unsafe rights",
    )
    unsafe_rights_facet.embedding_eligible = True
    not_prompt_safe, not_prompt_safe_facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="APPROVED",
        prompt_safety_state="NOT_PROMPT_SAFE",
        facet_text="not prompt safe",
    )
    not_prompt_safe_facet.embedding_eligible = True
    non_eligible, non_eligible_facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="APPROVED",
        facet_text="not embedding eligible",
    )
    db_session.flush()

    cases = [
        (unapproved, unapproved_facet, "MEMORY_NOT_APPROVED"),
        (unsafe_rights, unsafe_rights_facet, "MEMORY_RIGHTS_NOT_SAFE"),
        (not_prompt_safe, not_prompt_safe_facet, "MEMORY_ITEM_NOT_PROMPT_SAFE"),
        (non_eligible, non_eligible_facet, "FACET_NOT_EMBEDDING_ELIGIBLE"),
    ]
    for _, facet, expected in cases:
        job = EmbeddingJobService(db_session).prepare_job(memory_facet_id=facet.id, effective_context_snapshot_id=effective.id)
        assert job.job_status == "BLOCKED"
        assert expected in job.blocker_reason_codes_json


def test_embedding_facet_snapshots_status_and_stale_detection(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Embed Snapshot")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    item, facet, embedding = _eligible_memory(
        db_session,
        scope,
        effective=effective,
        category=category,
        text="Prefer concrete title outcomes.",
        vector=[1.0, 0.0, 0.0],
    )

    assert embedding.approval_status_at_embed == "APPROVED"
    assert embedding.rights_status_at_embed == "SAFE"
    assert embedding.prompt_safety_state_at_embed == "PROMPT_SAFE"
    assert embedding.embedding_eligible_at_embed is True

    facet.facet_text = "Changed text after embedding."
    facet.facet_text_hash = stable_hash({"changed": facet.facet_text})
    db_session.flush()
    stale = EmbeddingJobService(db_session).detect_stale_embeddings()
    assert stale[0].stale_state == "REINDEX_REQUIRED"
    assert "FACET_TEXT_HASH_CHANGED" in stale[0].stale_reason_codes_json
    assert item.id == embedding.memory_item_id


def test_sql_filter_runs_before_vector_and_blocks_cross_company_high_score(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 SQL First")
    other = qualification_factory.channel_scope(name="R3D6 SQL Other")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    good_item, good_facet, _ = _eligible_memory(
        db_session,
        scope,
        effective=effective,
        category=category,
        text="Prefer concrete title framing.",
        vector=[0.1, 0.0, 0.0],
    )
    bad_item, bad_facet = _memory(
        db_session,
        other,
        approval_status="APPROVED",
        facet_text="Cross-company high vector must not surface.",
    )
    bad_facet.embedding_eligible = True
    db_session.flush()
    EmbeddingJobService(db_session).store_embedding(memory_facet_id=bad_facet.id, embedding_vector=[1.0, 0.0, 0.0])

    result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0, 0.0])
    )

    selected_ids = {candidate.memory_facet_id for candidate in result.selected_candidates}
    assert result.sql_filter_applied_before_vector is True
    assert good_facet.id in selected_ids
    assert bad_facet.id not in selected_ids
    manifest = db_session.get(VectorRetrievalManifest, result.manifest_id)
    assert manifest.sql_filter_json["sql_filter_first"] is True
    assert manifest.candidate_count_before_vector == 1
    assert bad_item.company_id != good_item.company_id


def test_cross_channel_blocked_by_default_and_company_approved_requires_allow_flag(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Channel A")
    other = qualification_factory.channel_scope(name="R3D6 Channel B")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    _eligible_memory(db_session, scope, effective=effective, category=category, text="Same channel lesson.", vector=[0.2, 0.0])

    cross_item = ChannelMemoryItem(
        company_id=scope.company.id,
        channel_workspace_id=other.channel.id,
        memory_type="PACKAGING_PATTERN",
        source_type="HUMAN_REFERENCE",
        source_ref=f"cross:{uuid.uuid4()}",
        source_content_hash=stable_hash({"cross": "source"}),
        summary="Company approved cross-channel lesson.",
        approval_status="APPROVED",
        rights_status="SAFE",
        prompt_safety_state="PROMPT_SAFE",
        reuse_scope="COMPANY_APPROVED",
        freshness_state="FRESH",
        content_hash=stable_hash({"cross": "item"}),
    )
    db_session.add(cross_item)
    db_session.flush()
    cross_facet = MemoryFacet(
        memory_item_id=cross_item.id,
        company_id=scope.company.id,
        channel_workspace_id=other.channel.id,
        facet_type="PACKAGING_PATTERN",
        facet_text="Company-wide concrete framing lesson.",
        facet_text_hash=stable_hash({"text": "Company-wide concrete framing lesson."}),
        polarity="POSITIVE",
        confidence_label="HIGH",
        prompt_safety_state="PROMPT_SAFE",
        embedding_eligible=True,
    )
    db_session.add(cross_facet)
    db_session.flush()
    EmbeddingJobService(db_session).store_embedding(memory_facet_id=cross_facet.id, embedding_vector=[1.0, 0.0])

    blocked = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0], allow_company=False)
    )
    assert cross_facet.id not in {candidate.memory_facet_id for candidate in blocked.selected_candidates}

    allowed = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0], allow_company=True)
    )
    assert cross_facet.id in {candidate.memory_facet_id for candidate in allowed.selected_candidates}


def test_character_specific_memory_blocks_no_character_and_mismatched_context(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Character")
    no_char_category = _category(db_session, scope, mode="NO_CHARACTER")
    char_category = _category(db_session, scope, mode="REQUIRED_CHARACTER")
    char = _character_binding(db_session, scope, char_category)
    other_char = _character_binding(db_session, scope, char_category)
    no_char_context = _effective_context(db_session, scope, category=no_char_category)
    char_context = _effective_context(db_session, scope, category=char_category, binding=char.binding)
    item, facet = _memory(
        db_session,
        scope,
        approval_status="APPROVED",
        reuse_scope="CHARACTER",
        character=other_char,
        facet_text="Wrong character should not surface.",
    )
    item.content_category_id = None
    facet.content_category_id = None
    facet.embedding_eligible = True
    db_session.flush()

    no_char_result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=False).retrieve(
        _retrieval_request(no_char_context, query_vector=None)
    )
    mismatch_result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=False).retrieve(
        _retrieval_request(char_context, query_vector=None)
    )

    assert any("NO_CHARACTER_CONTEXT_BLOCKS_CHARACTER_MEMORY" in ref["reason_codes"] for ref in no_char_result.blocked_refs)
    assert any("CHARACTER_PROFILE_SCOPE_MISMATCH" in ref["reason_codes"] for ref in mismatch_result.blocked_refs)


def test_blocked_memory_cannot_surface_and_empty_states_are_safe(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Empty Safe")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    rejected, rejected_facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="REJECTED",
        facet_text="Rejected high-vector memory.",
    )
    rejected_facet.embedding_eligible = True
    db_session.flush()

    empty = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0])
    )
    assert empty.status == "EMPTY_SAFE_DIGEST"
    assert rejected.id not in [candidate.memory_item_id for candidate in empty.selected_candidates]

    unavailable = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=False).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0])
    )
    assert unavailable.status == "VECTOR_RUNTIME_EMPTY_SAFE"
    assert "VECTOR_RETRIEVAL_DISABLED" in unavailable.reason_codes


def test_agent_digests_are_digest_only_and_provider_agent_gets_no_creative_memory(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Digest")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    _eligible_memory(
        db_session,
        scope,
        effective=effective,
        category=category,
        text="Prefer concrete hooks with a visible payoff.",
        vector=[1.0, 0.0, 0.0],
    )

    script_result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0, 0.0])
    )
    digest_text = str(script_result.digest)
    assert script_result.digest["digest_type"] == "ScriptMemoryDigest"
    assert "embedding_vector_json" not in digest_text
    assert "ChannelMemoryItem" not in digest_text
    assert "full old script" not in digest_text.lower()

    provider_request = _retrieval_request(effective, agent_key="ProviderReadinessSummaryAgent")
    provider_result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=False).retrieve(provider_request)
    assert provider_result.digest["status"] == "EMPTY_SAFE_DIGEST"
    assert provider_result.digest["reason_codes"] == ["AGENT_DOES_NOT_ACCEPT_CREATIVE_MEMORY"]


def test_manifest_records_selected_blocked_rejected_and_hash_is_stable(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Manifest")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    _eligible_memory(db_session, scope, effective=effective, category=category, text="Selected lesson.", vector=[1.0, 0.0])
    blocked_item, blocked_facet = _memory(
        db_session,
        scope,
        category=category,
        approval_status="APPROVED",
        facet_text="Forbidden use case lesson.",
    )
    blocked_facet.embedding_eligible = True
    blocked_facet.allowed_use_cases_json = ["thumbnail"]
    db_session.flush()

    service = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True)
    request = _retrieval_request(effective, query_vector=[1.0, 0.0])
    first = service.retrieve(request)
    second = service.retrieve(request)
    first_manifest = db_session.get(VectorRetrievalManifest, first.manifest_id)

    assert first_manifest.selected_memory_facet_refs_json
    assert first_manifest.rejected_refs_json
    assert first.retrieval_hash == second.retrieval_hash

    _eligible_memory(db_session, scope, effective=effective, category=category, text="New eligible lesson.", vector=[0.9, 0.1])
    third = service.retrieve(request)
    assert third.retrieval_hash != first.retrieval_hash
    assert blocked_item.id


def test_conflict_resolver_collapses_contradictory_memory(db_session, qualification_factory) -> None:
    scope = qualification_factory.channel_scope(name="R3D6 Conflict")
    category = _category(db_session, scope)
    effective = _effective_context(db_session, scope, category=category)
    _eligible_memory(db_session, scope, effective=effective, category=category, text="Use abstract titles for curiosity.", vector=[1.0, 0.0])
    item, facet, _ = _eligible_memory(db_session, scope, effective=effective, category=category, text="Avoid abstract titles; use concrete outcomes.", vector=[0.9, 0.1])
    facet.polarity = "NEGATIVE"
    db_session.flush()

    result = VectorSafeRetrievalService(db_session, retrieval_enabled=True, vector_enabled=True).retrieve(
        _retrieval_request(effective, query_vector=[1.0, 0.0])
    )
    lessons = result.digest["lessons"]
    lesson_text = " ".join(str(item) for item in lessons)
    assert len(lessons) == 1
    assert "mixed signals about abstract titles" in lesson_text
    assert "Use abstract titles for curiosity. Ref" not in lesson_text
    assert item.id


def test_agent_context_pack_optional_memory_digest_has_manifest_refs_only(db_session, qualification_factory, monkeypatch) -> None:
    monkeypatch.setenv("CONTROLLED_MEMORY_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("VECTOR_RETRIEVAL_ENABLED", "false")
    get_settings.cache_clear()
    try:
        scope = qualification_factory.channel_scope(name="R3D6 Context Pack")
        category = _category(db_session, scope)
        effective = _effective_context(db_session, scope, category=category)
        _eligible_memory(
            db_session,
            scope,
            effective=effective,
            category=category,
            text="Memory digest should stay compact.",
            vector=[1.0, 0.0],
        )

        result = AgentContextPackBuilder(db_session).build(
            package_id=uuid.uuid4(),
            video_project_id=effective.video_project_id,
            agent_key="ScriptWriterAgent",
            task_type="long_form_script",
            lane="long_context_text",
            effective_context_snapshot_id=effective.id,
            effective_context_hash=effective.context_hash,
            compiled_policy_snapshot_id=scope.snapshot.id,
            compiled_policy_snapshot_hash=scope.snapshot.content_hash,
            channel_contract_hash=effective.channel_contract_hash,
            artifacts={"script_outline": {"outline": ["hook"]}},
            evidence_refs=[{"source_type": "OPERATOR_RESEARCH_PACK", "ref": "r3d6"}],
            current_package_state={"topic": "R3D6 memory", "research_pack_ref": "r3d6"},
            runtime_guard_state={"no_media_provider_calls": True, "no_upload": True, "no_publish": True},
        )
        memory_digest = result.context_pack["digests"]["memory_digest"]
        assert result.status == "OK"
        assert memory_digest["retrieval_manifest_id"]
        assert memory_digest["context_pack_payload"] == "digest_only"
        assert "embedding_vector_json" not in str(memory_digest)
        assert "facet_text" not in str(memory_digest)
    finally:
        get_settings.cache_clear()


def test_r3d6_source_guards_no_external_vector_db_embedding_provider_or_upload_calls() -> None:
    service_source = Path("app/services/r3d6.py").read_text(encoding="utf-8")
    config = get_settings()
    forbidden = [
        "qdrant",
        "weaviate",
        "pinecone",
        "requests.",
        "httpx",
        "YouTubeUpload",
        "GoogleDriveUploadService",
        "GoogleVertexVeoProvider",
    ]
    assert [token for token in forbidden if token in service_source] == []
    assert config.controlled_memory_retrieval_enabled is False
    assert config.vector_retrieval_enabled is False
    assert config.embedding_execution_enabled is False
