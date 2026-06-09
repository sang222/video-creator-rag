import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config.settings import get_settings
from app.core.enums import MemoryScope
from app.db.base import Base
from app.main import app, get_db
from app.models.entities import ChannelWorkspace, Company, EditorialPlaybook, MemoryItem, WorkspaceBudgetPolicy, WorkspaceProfile
from app.providers.embedding import EmbeddingProvider, MockEmbeddingProvider
from app.services.maturity import WorkspaceMaturityService
from app.services.memory_retrieval import MemoryRetrievalService, validate_embedding_vector


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def create_company_workspace(db, *, company_name="Acme", workspace_name="AI Daily", platform="youtube"):
    company = Company(name=company_name)
    db.add(company)
    db.flush()
    workspace = ChannelWorkspace(
        company_id=company.id,
        workspace_name=workspace_name,
        platform=platform,
        channel_name=workspace_name,
        niche="AI creator tools",
        language="en",
        target_market=["US"],
        follower_count=100,
        published_video_count=3,
        baseline_confidence=0.05,
    )
    stage, _ = WorkspaceMaturityService().classify_workspace(workspace)
    workspace.maturity_stage = stage
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceProfile(
            workspace_id=workspace.id,
            company_id=company.id,
            brand_voice="clear and practical",
            target_audience="solo creators",
            monetization_thesis_json={"primary": "affiliate validation"},
            platform_rules_json={"copyright": "no unlicensed media"},
        )
    )
    db.add(WorkspaceBudgetPolicy(company_id=company.id, workspace_id=workspace.id))
    db.add(
        EditorialPlaybook(
            company_id=company.id,
            workspace_id=workspace.id,
            version="test_playbook_v1",
            content_json={"principles": ["reuse first"]},
            active=True,
        )
    )
    db.commit()
    return company, workspace


def add_memory(db, company, workspace, *, scope, family, title, content, platform=None):
    item = MemoryItem(
        company_id=company.id,
        workspace_id=workspace.id if scope == MemoryScope.WORKSPACE_ONLY.value else None,
        scope=scope,
        family=family,
        type="note",
        title=title,
        content=content,
        metadata_json={"platform": platform} if platform else {},
        confidence=0.8,
    )
    db.add(item)
    db.commit()
    return item


def test_sqlite_dev_mode_falls_back_without_pgvector(db):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate validation",
        content="Affiliate clicks prove buyer intent.",
    )

    result = MemoryRetrievalService().retrieve(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
        agent_role="MonetizationStrategyAgent",
        query="affiliate buyer intent",
        families=["monetization_memory"],
        limit=5,
    )

    assert result.items
    assert result.backend_used in {"fallback_keyword", "fallback_embedding_scan"}
    assert result.retrieval_trace["pgvector_available"] is False
    assert "dialect=sqlite" in result.retrieval_trace["fallback_reason"]


def test_scope_isolation_is_applied_before_fallback_ranking(db):
    company_a, workspace_a = create_company_workspace(db, company_name="A", workspace_name="A YouTube", platform="youtube")
    _, workspace_b = create_company_workspace(db, company_name="A", workspace_name="A TikTok", platform="tiktok")
    company_c, workspace_c = create_company_workspace(db, company_name="C", workspace_name="C YouTube", platform="youtube")
    workspace_b.company_id = company_a.id
    db.commit()

    visible_workspace = add_memory(
        db,
        company_a,
        workspace_a,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace A buyer intent",
        content="Workspace A affiliate signal.",
    )
    add_memory(
        db,
        company_a,
        workspace_b,
        scope="workspace_only",
        family="monetization_memory",
        title="Workspace B buyer intent",
        content="Workspace B must not leak.",
    )
    visible_company = add_memory(
        db,
        company_a,
        workspace_a,
        scope="company_global",
        family="monetization_memory",
        title="Company A playbook",
        content="Company A revenue rule.",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="company_global",
        family="monetization_memory",
        title="Company C playbook",
        content="Company C should not leak.",
    )
    visible_platform = add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="YouTube platform rule",
        content="YouTube monetization rule.",
        platform="youtube",
    )
    add_memory(
        db,
        company_a,
        workspace_a,
        scope="platform_global",
        family="monetization_memory",
        title="TikTok platform rule",
        content="TikTok should not leak into YouTube.",
        platform="tiktok",
    )
    add_memory(
        db,
        company_c,
        workspace_c,
        scope="platform_global",
        family="monetization_memory",
        title="Other company YouTube",
        content="Cross-company platform memory should not leak.",
        platform="youtube",
    )

    result = MemoryRetrievalService().retrieve(
        db,
        company_id=company_a.id,
        workspace_id=workspace_a.id,
        workspace_context={"company_id": company_a.id, "workspace_id": workspace_a.id, "platform": "youtube"},
        agent_role="AuthorityAgent",
        query="buyer intent platform revenue",
        families=["monetization_memory"],
        limit=20,
    )
    ids = {item.id for item in result.items}

    assert {visible_workspace.id, visible_company.id, visible_platform.id}.issubset(ids)
    assert all(item.company_id == company_a.id for item in result.items)
    assert all(item.workspace_id == workspace_a.id for item in result.items if item.scope == "workspace_only")
    assert all((item.metadata_json or {}).get("platform") != "tiktok" for item in result.items)

    no_platform = MemoryRetrievalService().retrieve(
        db,
        company_id=company_a.id,
        workspace_id=workspace_a.id,
        workspace_context={"company_id": company_a.id, "workspace_id": workspace_a.id},
        agent_role="AuthorityAgent",
        query="youtube platform",
        families=["monetization_memory"],
        scopes=["platform_global"],
        limit=20,
    )
    assert no_platform.items == []


def test_embedding_dimension_policy_and_embed_endpoint_validation(client, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSION", "8")
    get_settings.cache_clear()
    assert len(MockEmbeddingProvider().embed("creator workflow")) == 8
    with pytest.raises(ValueError, match="dimension mismatch"):
        validate_embedding_vector([0.1] * 7, expected_dimension=8, label="test embedding")

    class BadEmbeddingProvider(EmbeddingProvider):
        model = "bad"
        version = "v1"

        def embed(self, text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

    import app.services.memory_retrieval as memory_retrieval

    monkeypatch.setattr(memory_retrieval, "get_embedding_provider", lambda settings=None: BadEmbeddingProvider())

    company = client.post("/companies", json={"name": "Dim Co"}).json()
    workspace = client.post(
        "/workspaces",
        json={
            "company_id": company["id"],
            "workspace_name": "Dim Workspace",
            "platform": "youtube",
            "channel_name": "Dim Channel",
        },
    ).json()
    item = client.post(
        "/memory/items",
        json={
            "company_id": company["id"],
            "workspace_id": workspace["id"],
            "scope": "workspace_only",
            "family": "monetization_memory",
            "title": "Bad dimension",
            "content": "This embedding provider returns the wrong dimension.",
        },
    ).json()
    response = client.post(f"/memory/items/{item['id']}/embed")

    assert response.status_code == 400
    assert "dimension mismatch" in response.json()["detail"]
    get_settings.cache_clear()


def test_context_pack_uses_retrieval_service_and_preserves_family_policy(db):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="brand_identity_memory",
        title="Brand voice",
        content="Clear practical brand voice.",
    )
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate revenue",
        content="Affiliate buyer intent.",
    )

    pack = MemoryRetrievalService().retrieve(
        db,
        company_id=company.id,
        workspace_id=workspace.id,
        workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
        agent_role="ScriptAgent",
        query="brand affiliate",
        limit=10,
    )
    assert {item.family for item in pack.items} == {"brand_identity_memory"}

    from app.memory.router import MemoryRouter

    context_pack = MemoryRouter().build_context_pack(
        db,
        agent_role="ScriptAgent",
        workspace_id=workspace.id,
        query="brand affiliate",
        limit=10,
    )
    assert context_pack["retrieval_backend"] in {"fallback_keyword", "fallback_embedding_scan"}
    assert context_pack["retrieval_trace"]["scope_filter_applied"] is True
    assert context_pack["memory_count"] == 1


def test_pgvector_backend_is_selected_only_for_postgresql(db, monkeypatch):
    company, workspace = create_company_workspace(db)
    add_memory(
        db,
        company,
        workspace,
        scope="workspace_only",
        family="monetization_memory",
        title="Affiliate validation",
        content="Affiliate clicks prove buyer intent.",
    )

    service = MemoryRetrievalService()
    available, reason = service.is_pgvector_available(db)
    assert available is False
    assert "dialect=sqlite" in reason

    monkeypatch.setenv("MEMORY_RETRIEVAL_BACKEND", "pgvector")
    get_settings.cache_clear()
    forced = MemoryRetrievalService()
    with pytest.raises(RuntimeError, match="pgvector memory retrieval requested but unavailable"):
        forced.retrieve(
            db,
            company_id=company.id,
            workspace_id=workspace.id,
            workspace_context={"company_id": company.id, "workspace_id": workspace.id, "platform": "youtube"},
            agent_role="MonetizationStrategyAgent",
            query="affiliate buyer intent",
            families=["monetization_memory"],
            limit=5,
        )
    get_settings.cache_clear()
