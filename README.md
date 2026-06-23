# VCOS M0 Foundation

VCOS is a budgeted, self-funding, multi-channel, artifact-first media workflow engine.

This repository currently contains M0 only: repo skeleton, architecture ledger, source-of-truth foundation, initial database schema, config catalogs, contracts, services, CLI, and tests.

## Stack

- Python 3.13+
- FastAPI
- Pydantic v2 and pydantic-settings
- SQLAlchemy 2.x
- Alembic
- PostgreSQL 16
- pytest
- Typer
- PyYAML
- Docker Compose

## Local

```bash
make install
make db-up
make migrate
make seed
make test
make health
```

## Boundaries

M0 does not implement media pipelines, agent runtime, publishing, analytics, dashboard UI, provider integrations, queue brokers, or LLM calls.
