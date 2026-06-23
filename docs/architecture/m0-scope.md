# M0 Scope

## Included

- Repository skeleton.
- Architecture ledger and source-of-truth docs.
- PostgreSQL 16 Docker Compose setup.
- SQLAlchemy models and initial Alembic migration.
- Pydantic contracts for gates, events, audit, LLM run snapshots, and config versions.
- Deterministic config catalog seed flow.
- Minimal audit service.
- Minimal domain event bus without external broker.
- Minimal config registry service.
- Minimal RBAC role assignment skeleton.
- FastAPI health check backed by database connectivity.
- Typer CLI for migration, config seed, audit tail, and health.
- M0 test coverage.

## Excluded

- Channel workspace workflow.
- Channel profile compiler.
- Channel profile version runtime logic.
- Compiled channel policy snapshot runtime.
- Video projects.
- Artifact workflow.
- Review tasks and approval decisions.
- Media pipeline.
- TTS provider.
- Whisper integration.
- Envato integration.
- Publish package.
- Upload or publisher integrations.
- Analytics.
- No-view diagnostic.
- Learning.
- Dashboard.
- Provider integrations.
- LLM calls.
- Queue broker.
- Full observability platform.
