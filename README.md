# Company-level Editorial Content Operating System - Phase 2

Phase 2 keeps the Phase 1 backend backbone and adds a real API/data/agent runtime foundation:

```txt
Company -> Channel Workspace -> Video Project -> Uploaded Video
```

There is no dashboard UI in this phase. Each Channel Workspace corresponds to exactly one channel and is the unit a future dashboard will manage. `workspace_id` is the canonical channel workspace identifier.

## What Is Implemented

- FastAPI app with company, workspace, project, review, upload, analytics, memory, and cost endpoints.
- SQLAlchemy models for the required Phase 1 tables.
- Postgres migration DDL in `sql/001_phase1_core_backbone.sql`, including pgvector-ready `memory_items.embedding vector(16)`.
- Integrity index updates in `sql/002_phase1_integrity_indexes.sql`; ownership is enforced in the service layer for SQLite/Postgres compatibility.
- Deterministic workspace maturity classifier.
- Workspace context loader and operational constitution compiler.
- Workflow mode router with monetization-validation guardrails.
- Workspace-scoped memory/RAG foundation with mock hash embeddings and keyword fallback behavior.
- Agent interfaces and mock implementations for all Phase 1 agents.
- LangGraph skeleton when `langgraph` is installed, with fallback metadata when it is not.
- Mock workflow that can run one project to final human review task creation.
- Review action backend that updates project state.
- Uploaded video and analytics snapshot backend.
- Cost events for mock agent calls.
- Provider interfaces for LLM, embeddings, storage, analytics, and media.
- OpenAI-compatible LLM and embedding providers with mock fallback.
- Prompt/skill pack loading from `app/skills`.
- Provider-backed real-text agents for Authority, Script, Monetization Strategy, SEO Metadata, Publishing Content, and Compliance Checklist.
- Structured Pydantic output validation for high-risk agents.
- Workspace constitution compile/get APIs.
- Bulk memory ingestion, embedding, search, and role-specific context-pack APIs.
- Cost events with provider/model/token/usage metadata for provider-backed calls.

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Default local DB is SQLite at `./phase1.db` for fast development. For Postgres:

```bash
export DATABASE_URL="postgresql+psycopg://user:password@localhost:5432/video_creator_rag"
psql "$DATABASE_URL" -f sql/001_phase1_core_backbone.sql
uvicorn app.main:app --reload
```

## Runtime Configuration

Default mode is fully mock and requires no provider keys.

```bash
export AGENT_RUNTIME_MODE=mock        # mock | hybrid | real_text
export USE_MOCK_PROVIDERS=true        # true keeps deterministic local provider behavior
```

`AGENT_RUNTIME_MODE=real_text` uses provider-backed text agents and keeps media/render/analytics mocked.

OpenAI-compatible LLM:

```bash
export USE_MOCK_PROVIDERS=false
export LLM_PROVIDER=openai_compatible
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_API_KEY="..."
export LLM_MODEL="gpt-4.1-mini"
```

OpenAI-compatible embeddings:

```bash
export EMBEDDING_PROVIDER=openai_compatible
export EMBEDDING_MODEL="text-embedding-3-small"
# EMBEDDING_BASE_URL / EMBEDDING_API_KEY default to LLM_BASE_URL / LLM_API_KEY when unset.
```

If required provider keys or URLs are missing, the factory falls back to mock providers.

## Smoke Flow

1. `POST /companies`
2. `POST /workspaces`
3. `POST /workspaces/{id}/classify-maturity`
4. `GET /workspaces/{id}/context`
5. `POST /workspaces/{workspace_id}/compile-constitution`
6. `POST /memory/items/bulk`
7. `POST /memory/context-pack`
8. `POST /projects/start`
9. `POST /projects/{id}/run-next`
10. `POST /review-tasks/{id}/action` with `{"action": "APPROVE"}`
11. `POST /projects/{id}/mark-published`
12. `POST /projects/{id}/analytics/snapshot`
13. `POST /projects/{id}/run-next` after 7D analytics state to run post-publish diagnosis.

## Tests

```bash
pytest
```

Tests cover maturity classification, workflow mode routing, workspace memory isolation, explicit state transitions, review action handling, mock workflow review-task creation, provider fallback, skill pack loading, constitution compilation, memory context packs, real-text workflow execution, structured Authority validation, compliance checklist output, SEO/publishing output, cost events, and OpenAPI path generation.

## Providers

- Embeddings: `MockEmbeddingProvider`
- LLM: `MockLLMProvider` or `OpenAICompatibleLLMProvider`
- Embeddings: `OpenAICompatibleEmbeddingProvider` when configured
- Storage: `StorageProvider` interface placeholder
- Analytics: `AnalyticsProvider` interface placeholder
- Media: `MediaProvider` interface placeholder
- Image/video generation: mock local URI only
- Voiceover: mock local URI only
- Render: mock render timeline and manifest
- Analytics: mock diagnosis
- Publishing detection: mock detection

## Phase 3+ Integrations

- Dashboard UI for review queue, workspaces, cost, analytics, and memory.
- Real platform import/publish providers such as YouTube/TikTok APIs.
- Real media providers for image, voice, subtitles, and production render.
- Redis/Celery background jobs and semantic cache.
- Advanced RBAC and organization management.
